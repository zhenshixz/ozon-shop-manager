#!/usr/bin/env python3
"""Small, read-only Ozon monitor. Python 3.9+, standard library only."""
from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import datetime as dt
import email.utils
import html
import json
import logging
import logging.handlers
import os
import random
from pathlib import Path
import signal
import socket
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

LOG = logging.getLogger("ozon-monitor")
UTC = dt.timezone.utc
CHINA = dt.timezone(dt.timedelta(hours=8))
OZON = "https://api-seller.ozon.ru"
PUSHPLUS = "https://www.pushplus.plus"


class MonitorError(Exception):
    pass


class ApiError(MonitorError):
    def __init__(self, service, path, code, retry_after=0):
        # Never copy remote bodies or request headers into errors: they may contain keys.
        self.code = code
        self.retry_after = retry_after
        super().__init__(f"{service} {path}: code={code}")


def stamp(value=None):
    value = time.time() if value is None else value
    return dt.datetime.fromtimestamp(value, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def display_time(value):
    return dt.datetime.fromtimestamp(value, CHINA).strftime("%m-%d %H:%M:%S")


def parse_time(value):
    if not isinstance(value, str) or not value:
        raise MonitorError("订单缺少创建时间，停止本轮以免误推存量订单")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone missing")
        return parsed.timestamp()
    except ValueError:
        raise MonitorError("订单时间格式无法识别，停止本轮") from None


def require_list(value, description):
    if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
        raise MonitorError(f"{description} 响应结构不符合预期，未保存同步进度")
    return value


def expand_env(value):
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return os.environ.get(value[2:-1], "")
    return value


def load_config(filename):
    path = Path(filename).resolve()
    try:
        cfg = expand_env(json.loads(path.read_text(encoding="utf-8-sig")))
    except FileNotFoundError:
        raise MonitorError("未找到 config.json，请先复制 config.example.json 并填写配置") from None
    except (ValueError, UnicodeError):
        raise MonitorError("配置不是有效的 UTF-8 JSON；请检查引号、逗号和编码") from None
    if not isinstance(cfg, dict):
        raise MonitorError("配置根节点必须是 JSON 对象")
    defaults = {
        "poll_seconds": 300, "workers": 3, "http_timeout_seconds": 20,
        "order_lookback_hours": 48, "max_pages": 100,
        "product_poll_seconds": 900, "order_push_seconds": 600, "product_digest_hour": 22,
        "order_overlap_seconds": 900,
        "profit_poll_seconds": 300, "profit_image_poll_seconds": 10,
        "status_poll_seconds": 30,
    }
    for key, default in defaults.items():
        cfg.setdefault(key, default)
    limits = {"poll_seconds": (5, 300), "workers": (1, 16),
              "http_timeout_seconds": (2, 60), "order_lookback_hours": (1, 720),
              "max_pages": (1, 1000), "product_poll_seconds": (300, 86400),
              "order_push_seconds": (1, 3600), "product_digest_hour": (0, 23),
              "order_overlap_seconds": (300, 86400),
              "profit_poll_seconds": (300, 3600), "profit_image_poll_seconds": (10, 86400),
              "status_poll_seconds": (15, 300)}
    for key, (low, high) in limits.items():
        if type(cfg[key]) is not int or not low <= cfg[key] <= high:
            raise MonitorError(f"配置 {key} 必须为 {low}–{high} 的整数")
    push = cfg.get("pushplus")
    if not isinstance(push, dict):
        raise MonitorError("缺少 pushplus 配置")
    if not isinstance(push.get("token"), str) or not push["token"].strip():
        raise MonitorError("请填写 pushplus.token（或对应环境变量）")
    for key in ("topic", "secret_key"):
        push.setdefault(key, "")
        if not isinstance(push[key], str):
            raise MonitorError(f"pushplus.{key} 必须为字符串")
    push.setdefault("min_send_interval_seconds", 13)
    push.setdefault("daily_request_budget", 180)
    push.setdefault("quiet_end_hour", 7)
    for key, low, high in (("min_send_interval_seconds", 3, 600),
                          ("daily_request_budget", 1, 1900), ("quiet_end_hour", 0, 23)):
        if type(push[key]) is not int or not low <= push[key] <= high:
            raise MonitorError(f"pushplus.{key} 必须为 {low}–{high} 的整数")
    shops = cfg.get("shops")
    if not isinstance(shops, list) or not shops or any(not isinstance(s, dict) for s in shops):
        raise MonitorError("shops 必须是店铺对象列表")
    enabled = []
    ids, clients = set(), set()
    for shop in shops:
        if type(shop.get("enabled", True)) is not bool:
            raise MonitorError("店铺 enabled 必须为 true 或 false")
        if not shop.get("enabled", True):
            continue
        for field in ("id", "name", "client_id", "api_key"):
            if not isinstance(shop.get(field), str) or not shop[field].strip():
                raise MonitorError(f"已启用店铺缺少字符串字段 {field}；未填写的店铺请设 enabled=false")
        if not shop["client_id"].isdigit():
            raise MonitorError("已启用店铺的 client_id 必须填写数字字符串")
        if shop["id"] in ids or shop["client_id"] in clients:
            raise MonitorError("已启用店铺 id 或 client_id 重复，会导致重复查询或错误去重")
        ids.add(shop["id"])
        clients.add(shop["client_id"])
        enabled.append(shop)
    if not enabled:
        raise MonitorError("至少启用并填写一家店铺")
    if not isinstance(cfg.get("data_dir", "data"), str) or not cfg.get("data_dir", "data").strip():
        raise MonitorError("data_dir 必须为非空路径字符串")
    cfg["shops"] = enabled
    cfg["data_dir"] = str((path.parent / cfg.get("data_dir", "data")).resolve())
    return cfg


class Transport:
    def __init__(self, timeout=8):
        self.timeout = timeout
        self.lock = threading.Lock()
        self.next_request = 0
        self.account_next = {}
        self.cooldowns = {}

    @staticmethod
    def retry_seconds(value):
        try:
            return max(0, int(float(value)))
        except (TypeError, ValueError):
            try:
                return max(0, int(email.utils.parsedate_to_datetime(value).timestamp() - time.time()) + 1)
            except (TypeError, ValueError, OverflowError):
                return 0

    def pace(self, service, headers):
        if service != "Ozon":
            return
        account = (headers or {}).get("Client-Id", "")
        with self.lock:
            now = time.monotonic()
            remaining = self.cooldowns.get(account, 0) - now
            if remaining > 0:
                raise ApiError(service, "account-cooldown", "rate-limit-cooldown", int(remaining) + 1)
            due = max(now, self.next_request, self.account_next.get(account, 0))
            self.next_request = due + .3
            self.account_next[account] = due + 1.25
        time.sleep(max(0, due - time.monotonic()))

    def request(self, service, base, path, payload=None, headers=None):
        self.pace(service, headers)
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(base + path, data=data, headers={
            "Content-Type": "application/json", "User-Agent": "OzonPersonalMonitor/1.0",
            **(headers or {}),
        }, method="GET" if data is None else "POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
                if service == "Ozon" and response.headers.get("Ratelimit-Remaining") == "0":
                    with self.lock:
                        account = (headers or {}).get("Client-Id", "")
                        self.account_next[account] = max(self.account_next.get(account, 0), time.monotonic() + 2)
        except urllib.error.HTTPError as exc:
            retry = self.retry_seconds(exc.headers.get("Retry-After", "0"))
            if service == "Ozon" and exc.code == 429:
                retry = max(60, retry)
                with self.lock:
                    self.cooldowns[(headers or {}).get("Client-Id", "")] = time.monotonic() + retry
            exc.close()
            raise ApiError(service, path.split("?")[0], exc.code, retry) from None
        except (TimeoutError, socket.timeout):
            raise ApiError(service, path.split("?")[0], "timeout") from None
        except urllib.error.URLError as exc:
            code = "timeout" if isinstance(exc.reason, (TimeoutError, socket.timeout)) else "network-error"
            raise ApiError(service, path.split("?")[0], code) from None
        except OSError:
            raise ApiError(service, path.split("?")[0], "network-error") from None
        try:
            result = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise ApiError(service, path.split("?")[0], "invalid-json") from None
        if not isinstance(result, dict):
            raise ApiError(service, path.split("?")[0], "unexpected-response")
        return result


class Ozon:
    def __init__(self, shop, transport, max_pages=100):
        self.transport = transport
        self.max_pages = max_pages
        self.headers = {"Client-Id": shop["client_id"], "Api-Key": shop["api_key"]}

    def post(self, path, payload):
        result = self.transport.request("Ozon", OZON, path, payload, self.headers)
        if "code" in result and "result" not in result and "items" not in result:
            raise ApiError("Ozon", path, result.get("code"))
        return result

    def products(self, visibility):
        result, cursor, cursors = {}, "", set()
        for _ in range(self.max_pages):
            response = self.post("/v3/product/list", {
                "filter": {"visibility": visibility}, "last_id": cursor, "limit": 1000,
            }).get("result")
            if not isinstance(response, dict):
                raise MonitorError("Ozon 商品列表缺少 result")
            items = require_list(response.get("items"), "Ozon 商品列表")
            for item in items:
                product_id = item.get("product_id")
                if not product_id:
                    raise MonitorError("Ozon 商品列表缺少 product_id")
                result[str(product_id)] = item
            # Do not depend on the deprecated total field (replaced by total_items).
            # last_id can be populated on a short final page as well.
            total = response.get("total_items", response.get("total"))
            if len(items) < 1000 or (type(total) is int and len(result) >= total):
                return result
            cursor = response.get("last_id")
            if not isinstance(cursor, str) or not cursor or cursor in cursors:
                raise MonitorError("商品分页游标异常，未将不完整列表作为基线")
            cursors.add(cursor)
        raise MonitorError("商品分页超过 max_pages；本轮没有更新基线")

    def postings(self, source, since, until, changed_since=None):
        path = "/v4/posting/fbs/list" if source == "fbs" else "/v3/posting/fbo/list"
        filters = {"since": stamp(since), "to": stamp(until)}
        if changed_since is not None:
            if source != 'fbs': raise ValueError('Status change filter is only supported for FBS')
            filters['last_changed_status_date'] = {'from': stamp(changed_since), 'to': stamp(until)}
        result, cursor, cursors = {}, "", set()
        for _ in range(self.max_pages):
            payload = {"sort_dir": "asc", "cursor": cursor,
                       "filter": filters,
                       "limit": 100, "translit": False,
                       "with": {"analytics_data": False, "financial_data": False}}
            response = self.post(path, payload)
            # Current FBS v4 and FBO v3 both return postings/cursor/has_next
            # at the root. The obsolete versions used result and offset pagination.
            items = require_list(response.get("postings"), "Ozon 订单")
            has_next = response.get("has_next")
            if type(has_next) is not bool:
                raise MonitorError("Ozon 订单 has_next 格式异常")
            for posting in items:
                number = posting.get("posting_number")
                if not number:
                    raise MonitorError("Ozon 订单缺少 posting_number")
                result[str(number)] = posting
            if not has_next:
                return list(result.values())
            if not items:
                raise MonitorError("Ozon 返回空页但仍标记下一页，未保存进度")
            cursor = response.get("cursor")
            if not isinstance(cursor, str) or not cursor or cursor in cursors:
                raise MonitorError("Ozon 订单分页游标异常，未保存同步进度")
            cursors.add(cursor)
        raise MonitorError("订单分页超过 max_pages；请检查订单量和查询范围")

    def posting(self, source, number):
        path='/v3/posting/fbs/get' if source=='fbs' else '/v2/posting/fbo/get'
        response=self.post(path,{'posting_number':number,
                                'with':{'analytics_data':False,'financial_data':False}})
        result=response.get('result')
        if not isinstance(result,dict): raise MonitorError('订单详情缺少 result')
        return result

    def product_info(self, identifiers, by_sku=False):
        response=self.post('/v3/product/info/list',{'sku' if by_sku else 'offer_id':identifiers})
        return require_list(response.get('items'),'Ozon 商品详情')


import profit_core
import profit_sync
import profit_titles

class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "monitor.sqlite3"
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS products (
                    shop TEXT, product TEXT, baseline INTEGER NOT NULL, first_seen REAL NOT NULL,
                    PRIMARY KEY (shop, product));
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, event_key TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
                    shop TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL,
                    batch TEXT);
                CREATE INDEX IF NOT EXISTS events_pending ON events(batch, kind, id);
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
                    state TEXT NOT NULL, created REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0, receipt TEXT, last_error TEXT);
                CREATE TABLE IF NOT EXISTS health (
                    shop TEXT, source TEXT, last_success REAL, last_error TEXT,
                    PRIMARY KEY (shop, source));
            """)
            if 'kind' not in {r[1] for r in db.execute('PRAGMA table_info(messages)')}:
                db.execute("ALTER TABLE messages ADD COLUMN kind TEXT NOT NULL DEFAULT 'legacy'")
            profit_core.ensure_schema(db)

    @contextlib.contextmanager
    def connection(self):
        db = sqlite3.connect(str(self.path), timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def get(db, key, default=None):
        row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    @staticmethod
    def put(db, key, value):
        db.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, str(value)))

    @staticmethod
    def event(db, key, kind, shop, payload, now):
        db.execute("INSERT OR IGNORE INTO events(event_key,kind,shop,payload,created) VALUES (?,?,?,?,?)",
                   (key, kind, shop, json.dumps(payload, ensure_ascii=False), now))

    def products_snapshot(self, shop, all_items, sale_items, now):
        # Both complete API lists must have succeeded before this transaction.
        items = {**all_items, **sale_items}
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            baseline = self.get(db, f"product-baseline:{shop['id']}") is None
            for product in items:
                db.execute("INSERT OR IGNORE INTO products VALUES (?,?,?,?)",
                           (shop["id"], product, int(baseline), now))
            if not baseline:
                for product, item in sale_items.items():
                    row = db.execute("SELECT baseline FROM products WHERE shop=? AND product=?",
                                     (shop["id"], product)).fetchone()
                    if not row[0]:
                        self.event(db, f"product:{shop['id']}:{product}", "product", shop["id"], {
                            "shop": shop["name"], "product": product,
                            "offer": str(item.get("offer_id") or product),
                        }, now)
            self.put(db, f"product-baseline:{shop['id']}", stamp(now))
            if baseline:
                LOG.info("店铺 %s：商品基线完成，现有 %s 件不补推", shop["name"], len(items))

    def order_start(self, shop_id, now):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            key = f"order-start:{shop_id}"
            value = self.get(db, key)
            if value is None:
                # Ozon timestamps can have second precision. Include the starting second
                # rather than permanently suppress a new order created within that second.
                self.put(db, key, int(now))
                return int(now)
            return float(value)

    def last_success(self, shop_id, source):
        with self.connection() as db:
            row = db.execute("SELECT last_success FROM health WHERE shop=? AND source=?",
                             (shop_id, source)).fetchone()
            return row[0] if row else None

    def orders_snapshot(self, shop, source, postings, now, started):
        # Validate the complete response first; no partial batch is committed on a schema error.
        rows = []
        accepted = []
        for posting in postings:
            created = parse_time(posting.get("created_at") or posting.get("in_process_at"))
            if created < started:
                continue
            accepted.append(posting)
            order_id = str(posting.get("order_id") or posting["posting_number"])
            rows.append((order_id, {
                "shop": shop["name"], "order": str(posting.get("order_number") or order_id),
                "posting": str(posting["posting_number"]),
                "source": "FBS/rFBS" if source == "fbs" else "FBO",
                "created_at": stamp(created),
            }))
        with self.connection() as db:
            profit_core.capture_postings(db, shop, source, accepted, now)
            for order_id, payload in rows:
                # An Ozon order can be split into multiple postings: notify once per order.
                self.event(db, f"order:{shop['id']}:{order_id}", "order", shop["id"], payload, now)

    def health(self, shop_id, source, now=None, error=None):
        with self.connection() as db:
            db.execute("""INSERT INTO health VALUES (?,?,?,?) ON CONFLICT(shop,source)
                       DO UPDATE SET last_success=COALESCE(excluded.last_success,health.last_success),
                       last_error=excluded.last_error""", (shop_id, source, now, error))

    @staticmethod
    def queue_message(db, title, lines, now, rows, kind='order'):
        batch = uuid.uuid4().hex
        db.execute("INSERT INTO messages(id,title,content,state,created,kind) VALUES (?,?,?,'pending',?,?)",
                   (batch, title, "\n".join(lines), now, kind))
        db.executemany("UPDATE events SET batch=? WHERE id=? AND batch IS NULL",
                       ((batch, row["id"]) for row in rows))

    def initialize_schedule(self, now, hour=22):
        cutoff = dt.datetime.fromtimestamp(now, CHINA).replace(hour=hour, minute=0, second=0, microsecond=0)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if self.get(db, "digest:next-cutoff") is None:
                self.put(db, "digest:next-cutoff", cutoff.timestamp())

    def prepare_batches(self, now, cfg):
        self.initialize_schedule(now, cfg.get("product_digest_hour", 22))
        interval = cfg.get("order_push_seconds", 600)
        boundary = int(now // interval) * interval
        local = dt.datetime.fromtimestamp(now, CHINA)
        quiet_end = cfg.get('quiet_end_hour', 0)
        wake = int(local.replace(hour=quiet_end,minute=0,second=0,microsecond=0).timestamp())
        if quiet_end:
            boundary = max(boundary,wake)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = int(self.get(db, "orders:last-boundary", "0"))
            if local.hour >= quiet_end and previous < boundary:
                first_morning = quiet_end and previous < wake
                if first_morning and now < wake + cfg.get('poll_seconds', 300) + 60:
                    checked = {(row['shop'],row['source']) for row in db.execute(
                        "SELECT shop,source FROM health WHERE source IN ('fbs','fbo') AND last_success>=?",(wake,))}
                    if any((shop['id'],source) not in checked for shop in cfg.get('shops',[]) for source in ('fbs','fbo')):
                        return False
                rows = db.execute("SELECT * FROM events WHERE batch IS NULL AND kind='order' "
                                  "AND created<=? ORDER BY id", (now if first_morning else boundary,)).fetchall()
                morning = False
                if first_morning:
                    cutoff_time = stamp(wake)
                    backlog = []
                    for row in rows:
                        order_time = json.loads(row['payload']).get('created_at')
                        if row['created'] < wake or (order_time and order_time < cutoff_time):
                            backlog.append(row)
                    morning = bool(backlog)
                    rows = backlog if morning else [row for row in rows if row['created'] <= boundary]
                if rows:
                    window = f"{interval//60}分钟" if interval % 60 == 0 else f"{interval}秒"
                    label = '夜间汇总' if morning else f'{window}汇总'
                    title = f"OZON｜新订单 {len(rows)} 笔｜{label}"
                    lines = [title, f"汇总时间：{display_time(now if morning else boundary)}（北京时间）", ""]
                    grouped = {}
                    for row in rows:
                        item = json.loads(row["payload"])
                        item['_shop_id']=row['shop']
                        grouped.setdefault(item['shop'], []).append(item)
                    details_left=20
                    for name, items in grouped.items():
                        lines.append(f"【{name}】新订单 {len(items)} 笔")
                        for item in items[:10]:
                            lines.append(f"订单 {item['order']}｜{item['source']}")
                            if details_left:
                                products=db.execute('''SELECT l.*,t.zh_text FROM profit_orders o
                                    JOIN profit_lines l ON l.order_id=o.id
                                    LEFT JOIN profit_title_translations t ON t.source_text=l.name
                                    WHERE o.shop=? AND o.order_number=? ORDER BY o.id,l.id LIMIT 6''',
                                    (item['_shop_id'],item['order'])).fetchall()
                                shown=min(5,details_left)
                                for product in products[:shown]:
                                    product_name=' '.join((product['zh_text'] or product['name'] or product['offer_id'] or '商品信息同步中').split())
                                    if len(product_name)>200:product_name=product_name[:199]+'…'
                                    lines.append('商品：'+product_name)
                                    price=product['unit_price'];currency=product['currency']
                                    amount=('¥' if currency=='CNY' else '₽' if currency=='RUB' else '')+str(price) if price is not None else '待同步'
                                    if price is not None and currency not in ('CNY','RUB'):amount+=' '+(currency or '币种待确认')
                                    lines.append(f"售价：{amount} / 件；数量：{product['quantity']}")
                                    if push_image_url(product['image_url']):lines.append('商品图片：'+product['image_url'])
                                    else:lines.append('图片待同步，可在利润页查看')
                                    details_left-=1
                                if len(products)>shown:lines.append('更多商品请在订单利润页查看。')
                            else:lines.append('商品详情请在订单利润页查看。')
                        if len(items) > 10:
                            lines.append(f"另有 {len(items)-10} 笔，详见卖家后台。")
                    lines += ["", "包含此前未发送的新增订单；同一订单不重复提醒。"]
                    if cfg.get('profit_url'):
                        lines += ["", "填写采购价／查看预估利润：", cfg['profit_url']]
                    self.queue_message(db, title, lines, now, rows, kind='order-morning' if morning else 'order')
                self.put(db, "orders:last-boundary", boundary)
            cutoff = float(self.get(db, "digest:next-cutoff"))
            if now >= cutoff:
                # Include every product event in the reporting window, plus unsent
                # backlog from earlier windows. Previously submitted messages stay intact.
                rows = db.execute("SELECT * FROM events WHERE kind='product' AND created<=? "
                                  "AND (created>? OR batch IS NULL) ORDER BY id",
                                  (cutoff, cutoff-86400)).fetchall()
                counts = {shop['id']: [shop['name'], 0] for shop in cfg.get('shops', [])}
                for row in rows:
                    counts.setdefault(row['shop'], [json.loads(row['payload'])['shop'], 0])[1] += 1
                title = f"OZON｜每日上新汇总｜{len(rows)} 件"
                lines = [title, f"统计区间：{display_time(cutoff-86400)} 至 {display_time(cutoff)}（北京时间）",
                         "上新口径：监控首次发现并确认可售的商品。", ""]
                for shop_id, (name, count) in counts.items():
                    health = db.execute("SELECT last_success,last_error FROM health WHERE shop=? AND source='products'",
                                        (shop_id,)).fetchone()
                    stale = not health or health['last_success'] is None or cutoff-health['last_success'] > cfg.get('product_poll_seconds',900)*2 or health['last_error']
                    note = "（采集异常或未更新，数量可能不完整）" if stale else ""
                    lines.append(f"【{name}】上新 {count} 件{note}")
                lines += ["", f"全部店铺合计：{len(rows)} 件。", "如有未发送积压记录，一并计入本次汇总。"]
                # Independent scan results only; never call product APIs in the notifier.
                try:
                    import overweight
                    lines += overweight.digest_lines(self.directory/'overweight.sqlite3',cutoff)
                except Exception:
                    LOG.exception('读取超重检查摘要失败；上新汇总照常发送')
                self.queue_message(db, title, lines, now, rows, kind='product-digest')
                self.put(db, "digest:next-cutoff", cutoff+86400)

    def make_batch(self, now):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM messages WHERE state='pending' AND next_attempt<=? "
                                  "ORDER BY CASE kind WHEN 'order-morning' THEN 0 WHEN 'product-digest' THEN 1 ELSE 2 END,created LIMIT 1", (now,)).fetchone()
            return dict(existing) if existing else None

    def reserve_send(self, cfg, now, digest=False):
        local = dt.datetime.fromtimestamp(now, CHINA)
        if local.hour < cfg.get('quiet_end_hour', 0):
            return False
        day = local.strftime("%Y-%m-%d")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if now < float(self.get(db, "push:blocked-until", "0")):
                return False
            previous = float(self.get(db, "push:last-attempt", "0"))
            if now - previous < cfg["min_send_interval_seconds"]:
                return False
            count = int(self.get(db, f"push:count:{day}", "0"))
            reserved = int(bool(cfg.get('reserve_digest')) and not digest and self.get(db, 'digest:submitted-day') != day)
            if count >= cfg["daily_request_budget"] - reserved:
                return False
            self.put(db, "push:last-attempt", now)
            self.put(db, f"push:count:{day}", count + 1)
            return True

    def has_work(self, now):
        with self.connection() as db:
            return bool(db.execute("SELECT 1 FROM messages WHERE state='pending' AND next_attempt<=? LIMIT 1",
                                   (now,)).fetchone())


def push_image_url(value):
    if not isinstance(value,str) or len(value)>2048 or any(c.isspace() or ord(c)<32 for c in value):return False
    try:
        parsed=urllib.parse.urlsplit(value)
        return parsed.scheme=='https' and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:return False


class Pushplus:
    def __init__(self, cfg, transport):
        self.cfg = cfg
        self.transport = transport
        self.access_key = None
        self.expires = 0

    def call(self, path, payload=None, headers=None):
        response = self.transport.request("pushplus", PUSHPLUS, path, payload, headers)
        if response.get("code") != 200:
            raise ApiError("pushplus", path.split("?")[0], response.get("code", "missing-code"))
        return response.get("data")

    def send(self, title, content):
        template = 'txt'
        profit_url = profit_core.entry_url()
        has_link=bool(profit_url and profit_url in content)
        has_images=any(line.startswith('商品图片：') and push_image_url(line[5:]) for line in content.splitlines())
        if has_link or has_images:
            rendered=[]
            for line in content.split('\n'):
                if line.startswith('商品图片：') and push_image_url(line[5:]):
                    rendered.append('<img src="'+html.escape(line[5:],quote=True)+'" alt="商品图片" width="128" style="max-width:100%;height:auto;border-radius:8px" referrerpolicy="no-referrer">')
                else:
                    value=html.escape(line)
                    if has_link:value=value.replace(html.escape(profit_url),'<a href="'+html.escape(profit_url,quote=True)+'">打开订单利润 · 填写采购价</a>')
                    rendered.append(value)
            content='<br>'.join(rendered);template='html'
        receipt = self.call("/send", {
            "token": self.cfg["token"], "topic": self.cfg["topic"],
            "title": title, "content": content, "template": template, "channel": "wechat",
        })
        if not isinstance(receipt, str) or not receipt:
            raise ApiError("pushplus", "/send", "missing-receipt")
        return receipt

    def result(self, receipt, now):
        if not self.access_key or now >= self.expires:
            result = self.call("/api/common/openApi/getAccessKey", {
                "token": self.cfg["token"], "secretKey": self.cfg["secret_key"],
            })
            if not isinstance(result, dict) or not result.get("accessKey"):
                raise ApiError("pushplus", "getAccessKey", "missing-access-key")
            self.access_key = result["accessKey"]
            self.expires = now + max(1, int(result.get("expiresIn", 7200)) - 300)
        path = "/api/open/message/sendMessageResult?" + urllib.parse.urlencode({"shortCode": receipt})
        try:
            result = self.call(path, headers={"access-key": self.access_key})
        except ApiError:
            self.access_key = None
            raise
        if not isinstance(result, dict) or result.get("status") not in (0, 1, 2, 3):
            raise ApiError("pushplus", "sendMessageResult", "unknown-status")
        return result["status"]


class Notifier:
    def __init__(self, store, client, cfg):
        self.store, self.client, self.cfg = store, client, cfg
        self.next_result_check = 0
        self.next_budget_log = 0

    def recover(self):
        # An interrupted HTTP request may already have reached pushplus. Retry for availability,
        # but do not claim exactly-once delivery across ambiguous network failures.
        with self.store.connection() as db:
            db.execute("UPDATE messages SET state='pending', next_attempt=?, "
                       "last_error='process-interrupted; delivery-unknown' WHERE state='sending'",
                       (time.time() + 60,))

    def fail(self, batch, error, now, retry_after=0):
        delay = max(retry_after, min(3600, 15 * (2 ** min(batch["attempts"], 8))))
        with self.store.connection() as db:
            db.execute("UPDATE messages SET state='pending', next_attempt=?, last_error=?, receipt=NULL "
                       "WHERE id=?", (now + delay, error, batch["id"]))
        LOG.warning("推送未成功，保留待重试（%s 秒后）：%s", delay, error)

    def confirm(self, now):
        if not self.cfg.get("secret_key") or now < self.next_result_check:
            return
        self.next_result_check = now + 15
        with self.store.connection() as db:
            batches = [dict(row) for row in db.execute(
                "SELECT * FROM messages WHERE state='submitted' ORDER BY created LIMIT 10")]
        for batch in batches:
            try:
                status = self.client.result(batch["receipt"], now)
                if status == 2:
                    with self.store.connection() as db:
                        db.execute("UPDATE messages SET state='sent',last_error=NULL WHERE id=?", (batch["id"],))
                    LOG.info("pushplus 已确认发送（不代表两部手机均已展示或已读）")
                elif status == 3:
                    self.fail(batch, "pushplus-reported-delivery-failure", now)
                elif now - batch["created"] > 300:
                    LOG.warning("pushplus 消息仍在队列中，不重复提交；请检查官网发送记录")
            except MonitorError as exc:
                LOG.warning("无法核实 pushplus 投递结果，保持已提交状态：%s", exc)
                break

    def tick(self, now):
        if dt.datetime.fromtimestamp(now, CHINA).hour < self.cfg.get('quiet_end_hour', 0):
            return
        if self.store.prepare_batches(now, self.cfg) is False:
            return
        if self.store.has_work(now):
            batch = self.store.make_batch(now)
            if batch and self.store.reserve_send(self.cfg, now, digest=batch['kind'] == 'product-digest'):
                # Durable batches are created on their schedule and retried independently.
                with self.store.connection() as db:
                    db.execute("UPDATE messages SET state='sending',attempts=attempts+1 WHERE id=?", (batch["id"],))
                try:
                    receipt = self.client.send(batch["title"], batch["content"])
                except MonitorError as exc:
                    if getattr(exc, 'code', None) in (900, 905):
                        tomorrow = dt.datetime.fromtimestamp(now, CHINA).replace(hour=0,minute=0,second=0,microsecond=0) + dt.timedelta(days=1)
                        until = tomorrow.timestamp() if exc.code == 900 else now+3600
                        with self.store.connection() as db:
                            Store.put(db, 'push:blocked-until', until)
                    self.fail(batch, str(exc), now, getattr(exc, "retry_after", 0))
                else:
                    with self.store.connection() as db:
                        db.execute("UPDATE messages SET state='submitted',receipt=?,last_error=NULL WHERE id=?",
                                   (receipt, batch["id"]))
                        if batch['kind'] == 'product-digest':
                            Store.put(db, 'digest:submitted-day', dt.datetime.fromtimestamp(now,CHINA).strftime('%Y-%m-%d'))
                    LOG.info("已提交 pushplus：%s；尚不代表微信已收到", batch["title"])
            elif now >= self.next_budget_log:
                LOG.info("待推送消息保留中；等待发送间隔或今日预算恢复")
                self.next_budget_log = now + 60
        self.confirm(now)


def poll(store, api, shop, source, cfg):
    now = time.time()
    if source=='profit_status_fbs':
        profit_sync.refresh_fbs_statuses(store,api,shop,profit_sync.history_start(now),now)
    elif source in ('profit_fbs','profit_fbo'):
        profit_sync.refresh_orders(store,api,shop,source.split('_',1)[1],now)
    elif source=='profit_images':
        profit_sync.refresh_images(store,api,shop,now)
    elif source == "products":
        all_items = api.products("ALL")
        sale_items = api.products("IN_SALE")
        store.products_snapshot(shop, all_items, sale_items, time.time())
    else:
        started = store.order_start(shop["id"], now)
        previous = store.last_success(shop["id"], source)
        # Incremental scans with overlap catch delayed visibility without repeatedly
        # downloading 48 hours of history. A long outage resumes at the last success.
        since = max(0, started, previous - cfg.get("order_overlap_seconds", 900)
                    if previous is not None else now - cfg["order_lookback_hours"] * 3600)
        # Split long downtime into <= 24-hour API requests. Commit the cursor only on success.
        begin = since
        while begin < now:
            end = min(now, begin + 86400)
            postings = api.postings(source, begin, end)
            store.orders_snapshot(shop, source, postings, time.time(), started)
            begin = end
    store.health(shop["id"], source, now=now)
    elapsed = time.time() - now
    interval = job_interval(source,cfg)
    if elapsed >= interval:
        LOG.warning("店铺 %s %s 查询耗时 %.1f 秒，超过本任务轮询间隔",
                    shop["name"], source, elapsed)


def job_interval(source,cfg):
    if source=='profit_status_fbs': return cfg.get('status_poll_seconds',30)
    if source=='products': return cfg.get('product_poll_seconds',900)
    if source=='profit_images': return cfg.get('profit_image_poll_seconds',10)
    if source.startswith('profit_'): return cfg.get('profit_poll_seconds',300)
    return cfg['poll_seconds']


@contextlib.contextmanager
def instance_lock(directory):
    handle = (Path(directory) / "monitor.lock").open("a+b")
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            if handle.read(1) == b"":
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise MonitorError("同一数据目录已有监控进程运行；请勿重复启动") from None
    try:
        yield
    finally:
        handle.close()


def setup_logging(directory):
    stream = logging.StreamHandler()
    rotating = logging.handlers.RotatingFileHandler(
        Path(directory) / "monitor.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    for handler in (stream, rotating):
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)


def show_addresses():
    addresses = set()
    try:
        addresses = {row[4][0] for row in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    except OSError:
        pass
    lan = sorted(x for x in addresses if not x.startswith("127."))
    print("本机地址：127.0.0.1", flush=True)
    print("局域网/服务器网卡地址：" + (", ".join(lan) or "未识别"), flush=True)
    print("本程序仅向外访问 API，没有网页或监听端口，无需开放服务器端口。", flush=True)


def run(cfg, store, transport):
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    push_cfg = {**cfg["pushplus"], "order_push_seconds": cfg["order_push_seconds"], "poll_seconds": cfg["poll_seconds"],
                "product_digest_hour": cfg["product_digest_hour"],
                "product_poll_seconds": cfg["product_poll_seconds"], "reserve_digest": True,
                "shops": [{"id": s["id"], "name": s["name"]} for s in cfg["shops"]],
                "profit_url": profit_core.entry_url()}
    notifier = Notifier(store, Pushplus(cfg["pushplus"], transport), push_cfg)
    notifier.recover()
    store.initialize_schedule(time.time(), cfg["product_digest_hour"])
    jobs = []
    for index, shop in enumerate(cfg["shops"]):
        api = Ozon(shop, transport, cfg["max_pages"])
        for source in ("profit_status_fbs", "fbs", "fbo", "products", "profit_fbs", "profit_fbo", "profit_images"):
            startup_delay=index+1 if source=='profit_status_fbs' else index*.2 if source=='profit_images' else index*2+{
                "fbs":0,"fbo":5,"products":10,"profit_fbs":20,"profit_fbo":30}[source]
            jobs.append({"shop": shop, "source": source, "api": api, "future": None,
                         "interval": job_interval(source,cfg),
                         "due": time.monotonic() + startup_delay, "failures": 0})
    LOG.info("开始监控 %s 家店；订单每 %s 秒查询、每 %s 秒汇总；商品每 %s 秒采集，每天北京时间 %02d:00 汇总。",
             len(cfg["shops"]), cfg["poll_seconds"], cfg["order_push_seconds"],
             cfg["product_poll_seconds"], cfg["product_digest_hour"])
    LOG.info('FBS状态每 %s 秒批量核对，独立轻量任务；保留账号限速及失败退避',cfg['status_poll_seconds'])
    if not cfg["pushplus"]["topic"]:
        LOG.warning("未配置 pushplus.topic：消息仅发给令牌所属用户，请建立私密群组让两人加入")
    if not cfg["pushplus"]["secret_key"]:
        LOG.info("未配置可选 secret_key：仅记录推送受理结果，实际投递结果请在 pushplus 官网核对")
    push_future = None
    title_future = None
    title_due = time.monotonic()+15
    # Slow pushplus calls or optional receipt checks must not delay Ozon polling.
    with concurrent.futures.ThreadPoolExecutor(max_workers=cfg["workers"]) as pool, \
            concurrent.futures.ThreadPoolExecutor(max_workers=1) as push_pool, \
            concurrent.futures.ThreadPoolExecutor(max_workers=1) as title_pool, \
            concurrent.futures.ThreadPoolExecutor(max_workers=2) as image_pool, \
            concurrent.futures.ThreadPoolExecutor(max_workers=2) as status_pool:
        while not stop.is_set():
            for job in jobs:
                future = job["future"]
                if future is not None and future.done():
                    job["future"] = None
                    try:
                        future.result()
                        job["failures"] = 0
                        job["due"] = max(job["due"], time.monotonic() + 1)
                    except Exception as exc:
                        job["failures"] += 1
                        error = str(exc) if isinstance(exc, MonitorError) else type(exc).__name__
                        store.health(job["shop"]["id"], job["source"], error=error)
                        base = 60 if getattr(exc, "code", None) == 429 else 30
                        delay = max(getattr(exc, "retry_after", 0),
                                    min(1800, base * 2 ** min(job["failures"] - 1, 5))) + random.uniform(1, 10)
                        job["due"] = time.monotonic() + delay
                        LOG.warning("店铺 %s %s 查询失败；%s 秒后重试：%s",
                                    job["shop"]["name"], job["source"], delay, error)
                if job["future"] is None and time.monotonic() >= job["due"]:
                    worker=status_pool if job['source']=='profit_status_fbs' else image_pool if job['source']=='profit_images' else pool
                    job["future"] = worker.submit(poll, store, job["api"], job["shop"], job["source"], cfg)
                    job["due"] = time.monotonic() + job["interval"]
            if push_future is not None and push_future.done():
                push_future.result()
                push_future = None
            if push_future is None:
                push_future = push_pool.submit(notifier.tick, time.time())
            if title_future is not None and title_future.done():
                try:
                    title_result=title_future.result()
                    store.health('_global','profit_titles',error='translation-blocked' if title_result.get('blocked') else None,now=time.time())
                except Exception as exc:
                    store.health('_global','profit_titles',error=type(exc).__name__)
                    LOG.warning('中文标题缓存更新失败：%s',type(exc).__name__)
                title_future=None;title_due=time.monotonic()+15
            if title_future is None and time.monotonic()>=title_due:
                title_future=title_pool.submit(profit_titles.refresh_titles,store)
            stop.wait(.5)
    LOG.info("监控已停止，待推送记录已保存在数据目录")


def print_status(cfg):
    path = Path(cfg["data_dir"]) / "monitor.sqlite3"
    if not path.exists():
        print("尚未运行监控，没有同步记录。")
        return
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        names = {s["id"]: s["name"] for s in cfg["shops"]}
        for shop, source, success, error in db.execute("SELECT * FROM health ORDER BY shop,source"):
            freshness = f"{int(time.time() - success)} 秒前" if success is not None else "尚未成功"
            print(f"{names.get(shop, shop)} | {source} | 最后成功：{freshness} | {error or '正常'}")
        print("推送状态（submitted=平台受理，sent=平台确认发送；均不代表手机已读）：")
        for state, count in db.execute("SELECT state,COUNT(*) FROM messages GROUP BY state"):
            print(f"  {state}: {count}")
        count = db.execute("SELECT COUNT(*) FROM events WHERE batch IS NULL").fetchone()[0]
        print(f"等待组装通知：{count} 条事件")
    finally:
        db.close()


def main(argv=None):
    if sys.version_info < (3, 9):
        print("需要 Python 3.9 或更高版本。", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description="Ozon 十店上新/出单 → pushplus 微信提醒（无第三方依赖）")
    parser.add_argument("--config", default=str(Path(__file__).with_name("config.json")))
    parser.add_argument("command", choices=("check", "probe", "run", "status", "test-push"))
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
        if args.command == "check":
            print(f"配置有效，已启用 {len(cfg['shops'])} 家店；未联网、未发送消息。")
            if not cfg["pushplus"]["topic"]:
                print("提示：topic 为空，目前只会通知令牌所属用户。")
            return 0
        if args.command == "status":
            print_status(cfg)
            return 0
        transport = Transport(cfg["http_timeout_seconds"])
        if args.command == "probe":
            failures = 0
            for shop in cfg["shops"]:
                api = Ozon(shop, transport, cfg["max_pages"])
                for source in ("products", "fbs", "fbo"):
                    try:
                        if source == "products":
                            all_items = api.products("ALL")
                            sale_items = api.products("IN_SALE")
                            print(f"{shop['name']} 商品：共 {len(all_items)}，可售 {len(sale_items)}")
                        else:
                            now = time.time()
                            items = api.postings(source, now - 3600, now)
                            print(f"{shop['name']} {source}：近一小时 {len(items)} 个发货单")
                    except MonitorError as exc:
                        failures += 1
                        print(f"{shop['name']} {source}：连接失败 {exc}")
            print("只读连通性检查完成；未建立基线、未发送通知。")
            return 1 if failures else 0
        store = Store(cfg["data_dir"])
        with instance_lock(cfg["data_dir"]):
            setup_logging(cfg["data_dir"])
            if args.command == "test-push":
                if not store.reserve_send(cfg["pushplus"], time.time()):
                    raise MonitorError("尚在推送间隔内或已达到今日预算，请稍后再试")
                receipt = Pushplus(cfg["pushplus"], transport).send(
                    "OZON 提醒测试", "这是 Ozon 监控的测试消息。请你和家人分别确认微信是否收到。")
                print(f"测试消息已受理，流水号：{receipt}。请在两部手机上确认；受理不代表送达。")
                return 0
            show_addresses()
            run(cfg, store, transport)
        return 0
    except (MonitorError, sqlite3.Error, OSError) as exc:
        error = str(exc) if isinstance(exc, MonitorError) else type(exc).__name__
        print(f"启动/运行失败：{error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
