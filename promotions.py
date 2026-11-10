"""Official Ozon future-auto-add cleanup; private UI, durable jobs and opt-in timer."""
import argparse
import datetime as dt
from email.utils import parsedate_to_datetime
import hmac
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
TZ = dt.timezone(dt.timedelta(hours=8))
READ_LIST = '/v2/actions/auto-add/products/list'
DELETE = '/v2/actions/auto-add/products/delete'
RETRY_DELAYS = (2, 4, 8, 16, 32)
REQUEST_INTERVAL = 1.0
MAX_RETRY_WAIT = 120
_next_request_at = 0.0


class ApiFailure(ValueError):
    def __init__(self, code=None, retry_after=None):
        self.code, self.retry_after = code, retry_after
        self.retryable = code is None or code == 429 or code >= 500
        if code == 429: message = 'Ozon接口限流（429），需等待后核对剩余队列'
        elif self.retryable: message = 'Ozon请求未确认，请核对剩余队列'
        else: message = f'Ozon接口HTTP {code}，本次处理未完成'
        super().__init__(message)


def retry_seconds(value):
    if not value: return None
    try:
        delay = float(value)
    except (ValueError, TypeError):
        try: delay = parsedate_to_datetime(value).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError): return None
    return delay if 0 <= delay < float('inf') else None


def wait_retry(error, attempt, notify=None, path=''):
    delay = max(RETRY_DELAYS[attempt], error.retry_after or 0)
    # Never retry earlier than a long server-requested pause.
    if delay > MAX_RETRY_WAIT:
        raise ValueError(f'Ozon要求等待{int(delay)}秒，本轮暂停，请稍后核对剩余队列')
    if notify: notify(dict(stage='retry-wait', path=path, code=error.code,
                           seconds=delay, attempt=attempt+1))
    time.sleep(delay)


def shops():
    config = json.loads((ROOT/'config.json').read_text(encoding='utf-8-sig'))
    return {s['id']: s for s in config['shops'] if s.get('enabled', True)}


def db():
    folder = ROOT/'data'/'promotions'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    c = sqlite3.connect(folder/'state.sqlite3', timeout=10)
    c.row_factory = sqlite3.Row
    c.executescript('''CREATE TABLE IF NOT EXISTS settings(owner TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,owner TEXT NOT NULL,created REAL NOT NULL,
      finished REAL,status TEXT NOT NULL,source TEXT NOT NULL,shops TEXT NOT NULL,result TEXT NOT NULL DEFAULT '[]',pid INTEGER);''')
    return c


def reconcile(c):
    for row in c.execute("SELECT * FROM runs WHERE status IN ('queued','running')").fetchall():
        alive = False
        if row['pid']:
            try:
                cmd = Path(f"/proc/{row['pid']}/cmdline").read_bytes()
                alive = b'promotions.py' in cmd and (row['id'].encode() in cmd or b'--tick' in cmd)
            except OSError:
                pass
        if (row['status']=='running' and not alive) or (row['status']=='queued' and time.time()-row['created']>90):
            c.execute("UPDATE runs SET status='error',finished=? WHERE id=?", (time.time(),row['id']))


def plan(c):
    row = c.execute("SELECT value FROM settings WHERE owner='self'").fetchone()
    p = json.loads(row[0]) if row else dict(enabled=False, time='09:00', shops=[], last_day='')
    p.setdefault('extra_times', [])
    p['times'] = sorted(set([p['time']] + p['extra_times']))
    # Legacy last_day describes only the original daily time, not every slot.
    p.setdefault('last_runs', {p['time']: p.get('last_day', '')})
    return p


def api(shop, path, payload=None, notify=None):
    global _next_request_at
    if path not in ('/v1/actions', READ_LIST, DELETE):
        raise ValueError('禁止调用其他接口')
    req = urllib.request.Request('https://api-seller.ozon.ru'+path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Client-Id': str(shop['client_id']), 'Api-Key': shop['api_key'], 'Content-Type': 'application/json'})
    for attempt in range(len(RETRY_DELAYS)+1):
        pause = _next_request_at - time.monotonic()
        if pause > 0: time.sleep(pause)
        _next_request_at = time.monotonic() + REQUEST_INTERVAL
        error = None
        try:
            with urllib.request.urlopen(req, timeout=35) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            # Only keep the status and safe rate-limit metadata, never keys/headers/body.
            error = ApiFailure(e.code, retry_seconds(e.headers.get('Retry-After')))
            e.close()
        except (urllib.error.URLError, TimeoutError, OSError):
            error = ApiFailure()
        # Writes are recovered in clean() only after reading the remaining queue.
        if path == DELETE or not error.retryable or attempt == len(RETRY_DELAYS):
            raise error from None
        wait_retry(error, attempt, notify, path)


def future(value):
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.tzinfo is not None and parsed.timestamp() > time.time()
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Ozon 返回的自动加入日期无法识别') from None


def queue(shop, aid, date, call=api):
    items, offset, total = [], 0, None
    for _ in range(100):
        r = call(shop, READ_LIST, dict(action_id=aid, auto_add_date=date, limit=100, offset=offset))
        page = r['products']
        if total is not None and r['total'] != total:
            raise ValueError('队列在读取期间变化，请重新检查')
        total = r['total']; items.extend(page)
        if len(items) == total:
            if len({p['product_id'] for p in items}) != total:
                raise ValueError('队列商品重复，已停止')
            return items
        if not page or len(items) > total:
            break
        offset += len(page)
    raise ValueError('队列未完整读取，未执行后续删除')


def product_record(item, status='not_removed'):
    return {**{k:item.get(k, '') for k in ('product_id','offer_id','sku','name')}, 'status':status}


def product_details(run):
    """Enrich pre-feature history from existing private audits, without API calls."""
    for shop in run['result']:
        missing=[b for b in shop.get('batches',[]) if 'products' not in b]
        if not missing:
            continue
        records={}; prepared=None
        path=ROOT/'data'/'promotions'/run['id']/(hashlib.sha256(shop['shop'].encode()).hexdigest()+'.jsonl')
        try:
            with path.open(encoding='utf-8') as stream:
                for line in stream:
                    try: event=json.loads(line)
                    except ValueError:
                        prepared=None
                        continue
                    if event.get('stage')=='snapshot':
                        key=(event['action_id'],event['date'])
                        records[key]={p['product_id']:product_record(p) for p in event['products']}
                    elif event.get('stage')=='delete-prepared':
                        payload=event['payload'];key=(payload['action_id'],payload['auto_add_date'])
                        prepared=(key,set(payload['product_ids']))
                        for pid in prepared[1]:
                            if pid in records.get(key,{}):records[key][pid]['status']='unconfirmed'
                    elif event.get('stage')=='delete-response' and prepared:
                        key,ids=prepared
                        for pid in set(event['response'].get('product_ids',[])) & ids:
                            if pid in records.get(key,{}):records[key][pid]['status']='removed'
                        prepared=None
        except (OSError,KeyError,TypeError):
            pass
        for batch in missing:
            items=records.get((batch['action_id'],batch['date']))
            if items is not None:batch['products']=list(items.values())
    return run


def clean(shop, audit, progress, call=None):
    result = dict(shop=shop['id'], name=shop['name'], status='running', activities=0,
                  removed=0, batches=[], error='', notice='', retry_count=0)
    def notify(event):
        audit(event)
        result['retry_count'] += 1
        result['notice'] = f"接口暂时限流或未响应，等待{event['seconds']:g}秒后核对重试（第{event['attempt']}次）"
        progress(result)
    if call is None:
        call = lambda s, path, payload=None: api(s, path, payload, notify=notify)
    actions = call(shop, '/v1/actions')['result']
    result['activities'] = len(actions)
    progress(result)
    for action in actions:
        for date in dict.fromkeys(action.get('auto_add_dates') or []):
            if not future(date):
                continue
            batch = dict(action_id=action['id'], title=action['title'], date=date,
                         before=0, removed=0, remaining=None, status='checking')
            result['batches'].append(batch)
            try:
                items = queue(shop, action['id'], date, call)
                batch['before'] = len(items)
                batch['products'] = [product_record(item) for item in items]
                audit(dict(stage='snapshot', action_id=action['id'], date=date, products=items))
                ids = [p['product_id'] for p in items]
                if ids and not future(date):
                    raise ValueError('计划加入时间已到，未执行删除')
                for start in range(0, len(ids), 1000):
                    pending = ids[start:start+1000]
                    for attempt in range(len(RETRY_DELAYS)+1):
                        if not pending: break
                        if not future(date): raise ValueError('计划加入时间已到，未继续删除')
                        payload = dict(action_id=action['id'], auto_add_date=date, product_ids=pending)
                        audit(dict(stage='delete-prepared', payload=payload))
                        for item in batch['products']:
                            if item['product_id'] in pending:item['status']='unconfirmed'
                        try:
                            response = call(shop, DELETE, payload)
                        except ApiFailure as error:
                            if not error.retryable or attempt == len(RETRY_DELAYS): raise
                            wait_retry(error, attempt, notify, DELETE)
                            remaining = queue(shop, action['id'], date, call)
                            remaining_ids = {p['product_id'] for p in remaining}
                            absent = set(pending) - remaining_ids
                            for item in batch['products']:
                                if item['product_id'] in absent:item['status']='verified_absent'
                            audit(dict(stage='delete-reconciled',action_id=action['id'],date=date,absent_ids=list(absent)))
                            pending = [pid for pid in pending if pid in remaining_ids]
                            continue
                        audit(dict(stage='delete-response', response=response))
                        removed = response.get('product_ids', [])
                        confirmed_ids=set(removed) & set(pending)
                        for item in batch['products']:
                            if item['product_id'] in confirmed_ids:item['status']='removed'
                        batch['removed'] += len(confirmed_ids)
                        result['removed'] += len(confirmed_ids)
                        if set(removed) != set(pending):
                            raise ValueError('接口未确认全部移除，请核对剩余队列')
                        break
                remaining = queue(shop, action['id'], date, call) if ids else []
                remaining_ids={p['product_id'] for p in remaining}
                for item in batch['products']:
                    if item['product_id'] in remaining_ids:item['status']='remaining'
                batch.update(remaining=len(remaining), status='ok' if not remaining else 'error')
                if remaining:
                    raise ValueError('仍有待加入商品，需重新检查')
                result['notice'] = ''
            except Exception as e:
                batch.update(status='error', error=str(e) if isinstance(e, ValueError) else '接口数据异常，已停止本店处理')
                result.update(status='error', error=batch['error'])
                progress(result)
                return result
            progress(result)
    # Discover again: a new activity/date appearing during the run must not be
    # silently counted as a completed whole-shop cleanup.
    final = call(shop, '/v1/actions')['result']
    expected = {(a['id'], d) for a in actions for d in (a.get('auto_add_dates') or []) if future(d)}
    actual = {(a['id'], d) for a in final for d in (a.get('auto_add_dates') or []) if future(d)}
    pending_final = []
    for action in final:
        for date in dict.fromkeys(action.get('auto_add_dates') or []):
            if not future(date): continue
            remaining = queue(shop, action['id'], date, call)
            if remaining: pending_final.append((action['id'], date, len(remaining)))
    audit(dict(stage='final-verification',pending=[dict(action_id=a,date=d,total=n) for a,d,n in pending_final]))
    if pending_final:
        result.update(status='error',error='结束核对仍有待加入商品，请检查活动明细',pending_final=[dict(action_id=a,date=d,total=n) for a,d,n in pending_final])
    elif actual - expected:
        result.update(status='error', error='执行期间出现新的待加入活动批次，请再次运行检查')
    else:
        result['status'] = 'ok'
    progress(result)
    return result


def enqueue(ids, source='manual'):
    available = shops()
    if not isinstance(ids, list) or not ids or any(not isinstance(s, str) or s not in available for s in ids):
        raise ValueError('请选择有效店铺')
    ids = list(dict.fromkeys(ids))
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        reconcile(c)
        if c.execute("SELECT 1 FROM runs WHERE status IN ('queued','running')").fetchone():
            raise ValueError('已有任务正在执行，请等待完成')
        rid = secrets.token_hex(12)
        c.execute('INSERT INTO runs(id,owner,created,status,source,shops) VALUES(?,?,?,?,?,?)',
                  (rid, 'self', time.time(), 'queued', source, json.dumps(ids)))
    return rid


def launch(rid):
    try:
        subprocess.Popen([sys.executable, str(ROOT/'promotions.py'), '--worker', rid],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, close_fds=True)
    except Exception:
        with db() as c:
            c.execute("UPDATE runs SET status='error',finished=? WHERE id=?", (time.time(), rid))
        raise ValueError('任务启动失败') from None


def worker(rid):
    import fcntl
    os.umask(0o077)
    with db() as c:
        row = c.execute("SELECT * FROM runs WHERE id=? AND status='queued'", (rid,)).fetchone()
    if not row:
        return
    lock = open(ROOT/'data'/'promotions'/'worker.lock', 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        with db() as c:
            c.execute("UPDATE runs SET status='error',finished=? WHERE id=?", (time.time(), rid))
        return
    results = []
    try:
        with db() as c:
            c.execute("UPDATE runs SET status='running',pid=? WHERE id=?", (os.getpid(),rid))
        available = shops()
        folder = ROOT/'data'/'promotions'/rid
        folder.mkdir(mode=0o700)
        for sid in json.loads(row['shops']):
            current = dict(shop=sid, name=available.get(sid, {}).get('name', sid), status='running', removed=0, batches=[])
            results.append(current)

            def progress(value):
                results[-1] = value
                with db() as c:
                    c.execute('UPDATE runs SET result=? WHERE id=?', (json.dumps(results, ensure_ascii=False), rid))

            def audit(value):
                with (folder/(hashlib.sha256(sid.encode()).hexdigest()+'.jsonl')).open('a', encoding='utf-8') as f:
                    f.write(json.dumps(dict(at=time.time(), **value), ensure_ascii=False)+'\n')

            try:
                if sid not in available:
                    raise ValueError('店铺已停用或不存在')
                clean(available[sid], audit, progress)
            except Exception as e:
                current = results[-1]
                current.update(status='error', error=str(e) if isinstance(e, ValueError) else '处理异常，请检查任务记录')
                progress(current)
        status = 'ok' if all(r['status']=='ok' for r in results) else 'error'
    except Exception:
        status = 'error'
    finally:
        with db() as c:
            c.execute('UPDATE runs SET status=?,finished=?,result=? WHERE id=?',
                      (status, time.time(), json.dumps(results, ensure_ascii=False), rid))
        lock.close()


def tick():
    # Installed timers are harmless until the owner explicitly enables a plan.
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        reconcile(c)
        p = plan(c); now = dt.datetime.now(TZ)
        today = now.date().isoformat()
        due = [t for t in p['times'] if t <= now.strftime('%H:%M') and p['last_runs'].get(t) != today]
        if not p['enabled'] or not p['shops'] or not due:
            return
        if c.execute("SELECT 1 FROM runs WHERE status IN ('queued','running')").fetchone():
            return
        ids = p['shops']
        rid = secrets.token_hex(12)
        c.execute('INSERT INTO runs(id,owner,created,status,source,shops) VALUES(?,?,?,?,?,?)',
                  (rid,'self',time.time(),'queued','schedule',json.dumps(ids)))
        # If a restart missed several slots, one full sweep covers all overdue slots.
        p['last_runs'].update({t: today for t in due})
        p['last_day'] = today
        c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('self', json.dumps(p)))
    worker(rid)


def register(app):
    from flask import Blueprint, request, session, jsonify, send_from_directory
    from werkzeug.security import check_password_hash
    bp = Blueprint('promotions', __name__, url_prefix='/ozon-profit/promotions')
    attempts = []

    def public_access():
        try:
            return json.loads((ROOT/'promo-access.json').read_text()).get('public_access') is True
        except (OSError,ValueError,TypeError):
            return False

    @bp.before_request
    def guard():
        if not request.path.startswith('/ozon-profit/promotions/api/'):
            return
        if request.method == 'POST' and request.headers.get('Origin') not in (None, request.host_url.rstrip('/')):
            return jsonify(error='请求来源不正确'), 403
        if request.path.endswith('/login'):
            return
        if public_access():
            session.setdefault('promotions_csrf',secrets.token_urlsafe(24))
        elif not session.get('promotions_authorized'):
            return jsonify(error='请输入促销工具访问口令'), 401
        if request.method == 'POST':
            if not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), session.get('promotions_csrf', '!')):
                return jsonify(error='页面已过期，请刷新'), 403
            if request.headers.get('Origin') not in (None, request.host_url.rstrip('/')):
                return jsonify(error='请求来源不正确'), 403

    @bp.get('/')
    def index():
        return send_from_directory(ROOT/'web', 'promotions.html')

    @bp.get('/assets/<name>')
    def assets(name):
        if name not in ('promotions.css', 'promotions.js'):
            return '', 404
        return send_from_directory(ROOT/'web', name)

    @bp.post('/api/login')
    def login():
        now=time.time(); attempts[:] = [t for t in attempts if t > now-300]
        if len(attempts) >= 10:
            return jsonify(error='尝试过多，请5分钟后再试'), 429
        data = request.get_json(silent=True) or {}
        password = data.get('password', '') if isinstance(data, dict) else ''
        config = ROOT/'promo-access.json'
        if not config.exists():
            return jsonify(error='管理员尚未配置访问口令'), 503
        if not isinstance(password,str) or len(password)>200 or not check_password_hash(json.loads(config.read_text())['password_hash'], password):
            attempts.append(now)
            return jsonify(error='访问口令不正确'), 401
        session['promotions_authorized'] = True
        session['promotions_csrf'] = secrets.token_urlsafe(24)
        session.permanent = True
        return jsonify(ok=True)

    @bp.get('/api/state')
    def state():
        with db() as c:
            reconcile(c)
            p = plan(c)
            rows = c.execute("SELECT * FROM runs WHERE owner='self' ORDER BY created DESC LIMIT 60").fetchall()
        runs = [dict(r) for r in rows]
        for r in runs:
            r['shops'] = json.loads(r['shops']); r['result'] = json.loads(r['result'])
            # Product lists are fetched on demand rather than every five seconds.
            for shop in r['result']:
                for batch in shop.get('batches',[]):batch.pop('products',None)
        return jsonify(csrf=session['promotions_csrf'], public_access=public_access(), plan=p, runs=runs,
                       shops=[dict(id=s['id'],name=s['name']) for s in shops().values()], now=time.time())

    @bp.get('/api/runs/<rid>')
    def run_details(rid):
        if not re.fullmatch(r'[a-f0-9]{24}',rid):
            return jsonify(error='任务不存在'),404
        with db() as c:
            row=c.execute("SELECT * FROM runs WHERE id=? AND owner='self'",(rid,)).fetchone()
        if not row:return jsonify(error='任务不存在'),404
        result=dict(row)
        result['shops']=json.loads(result['shops']);result['result']=json.loads(result['result'])
        return jsonify(run=product_details(result))

    @bp.post('/api/plan')
    def save_plan():
        data = request.get_json(silent=True)
        if not isinstance(data,dict) or type(data.get('enabled')) is not bool or not isinstance(data.get('time'),str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',data['time']):
            raise ValueError('计划格式不正确')
        ids=data.get('shops'); available=shops()
        if not isinstance(ids,list) or any(not isinstance(s,str) or s not in available for s in ids) or (data['enabled'] and not ids):
            raise ValueError('请选择有效店铺')
        with db() as c:
            p=plan(c)
            if data['time'] in p['extra_times']:
                raise ValueError('执行时间不能与晚间补清时间重复')
            new_times=sorted(set([data['time']]+p['extra_times']))
            now=dt.datetime.now(TZ)
            last_runs={t:d for t,d in p['last_runs'].items() if t in new_times}
            if data['enabled']:
                for t in new_times:
                    if (not p['enabled'] or t not in p['times']) and t<=now.strftime('%H:%M'):
                        last_runs[t]=now.date().isoformat()
            p.update(last_runs=last_runs,times=new_times)
            p.update(enabled=data['enabled'], time=data['time'], shops=list(dict.fromkeys(ids)))
            c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('self',json.dumps(p)))
        return jsonify(ok=True)

    @bp.post('/api/run')
    def run():
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or data.get('confirm') is not True:
            raise ValueError('请确认仅清理待自动加入队列')
        rid=enqueue(data.get('shops')); launch(rid)
        return jsonify(id=rid), 202

    @bp.post('/api/logout')
    def logout():
        session.pop('promotions_authorized',None); session.pop('promotions_csrf',None)
        return jsonify(ok=True)

    app.register_blueprint(bp)


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--worker'); parser.add_argument('--tick',action='store_true')
    args=parser.parse_args()
    if args.worker: worker(args.worker)
    elif args.tick: tick()
