"""Offline tests for notification correctness. No real API calls or messages."""
import json
import datetime as dt
import io
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.error

from monitor import (ApiError, MonitorError, Notifier, Ozon, Pushplus, Store,
                     CHINA, Transport, instance_lock, load_config, poll, stamp)


SHOP = {"id": "shop01", "name": "测试店", "client_id": "123", "api_key": "not-a-real-key"}
PUSH = {"token": "not-a-real-token", "topic": "test-family", "secret_key": "",
        "min_send_interval_seconds": 13, "daily_request_budget": 180, "order_push_seconds": 1}


class FakeTransport:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def request(self, service, base, path, payload=None, headers=None):
        self.requests.append((path, payload, headers))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


class FakePush:
    def __init__(self, failure=False, status=2):
        self.calls = []
        self.failure = failure
        self.status = status

    def send(self, title, content):
        self.calls.append((title, content))
        if self.failure:
            raise ApiError("pushplus", "/send", 429, 30)
        return "test-receipt"

    def result(self, receipt, now):
        return self.status


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.now = dt.datetime(2026, 10, 26, 12, 0, tzinfo=CHINA).timestamp()

    def events(self):
        with self.store.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM events ORDER BY id")]

    def messages(self):
        with self.store.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM messages ORDER BY created")]

    def new_order(self, order_id=1, shop=SHOP, source="fbs", offset=1):
        posting = {"order_id": order_id, "posting_number": f"{order_id}-1",
                   "created_at": stamp(self.now + offset)}
        self.store.orders_snapshot(shop, source, [posting], self.now + offset, self.now - 10)

    def test_existing_products_suppressed_and_pending_new_product_waits_for_sale(self):
        old = {"1": {"product_id": 1, "offer_id": "old"}}
        new = {"2": {"product_id": 2, "offer_id": "new"}}
        self.store.products_snapshot(SHOP, old, old, self.now)
        self.store.products_snapshot(SHOP, {**old, **new}, old, self.now + 10)
        self.assertEqual(self.events(), [])
        self.store.products_snapshot(SHOP, {**old, **new}, {**old, **new}, self.now + 20)
        self.assertEqual(len(self.events()), 1)
        # Going off sale, returning on sale and restarting must not notify again.
        self.store.products_snapshot(SHOP, {**old, **new}, {}, self.now + 30)
        Store(self.temp.name).products_snapshot(SHOP, {**old, **new}, {**old, **new}, self.now + 40)
        self.assertEqual(len(self.events()), 1)

    def test_order_push_product_details_use_cache_actual_price_and_all_known_postings(self):
        for suffix,name,price,currency in [('1','Товар', '88.5','CNY'),('2','Second item','100','RUB')]:
            posting={'order_id':42,'order_number':'ORDER-42','posting_number':'42-'+suffix,
                     'created_at':stamp(self.now+1),'products':[{'offer_id':suffix,'name':name,'quantity':2,
                     'price':{'amount':price,'currency':currency}}]}
            self.store.orders_snapshot(SHOP,'fbs',[posting],self.now+2,self.now-10)
        with self.store.connection() as db:
            db.execute("INSERT INTO profit_title_translations VALUES ('Товар','测试商品','reviewed',?,0,'')",(self.now,))
            db.execute("UPDATE profit_lines SET image_url='https://cdn.example.test/a.jpg?x=1&y=2',purchase_unit='7',price_override='999'")
        self.store.prepare_batches(self.now+600,{**PUSH,'order_push_seconds':600})
        messages=[m for m in self.messages() if m['kind']=='order']
        self.assertEqual(len(messages),1)
        content=messages[0]['content']
        self.assertIn('新订单 1 笔',content)
        self.assertIn('商品：测试商品',content)
        self.assertIn('商品：Second item',content)
        self.assertIn('售价：¥88.5 / 件；数量：2',content)
        self.assertIn('售价：₽100 / 件',content)
        self.assertNotIn('999',content)
        self.assertIn('商品图片：https://cdn.example.test/a.jpg?x=1&y=2',content)

    def test_push_html_renders_only_safe_images_and_escapes_product_text(self):
        transport=FakeTransport([{'code':200,'data':'receipt'}])
        client=Pushplus(PUSH,transport)
        text='商品：<script>alert(1)</script>\n商品图片：https://cdn.example.test/a.jpg?x=1&y=2\n商品图片：javascript:alert(1)'
        with patch('monitor.profit_core.entry_url',return_value=''):
            client.send('title',text)
        payload=transport.requests[0][1]
        self.assertEqual(payload['template'],'html')
        self.assertIn('<img src="https://cdn.example.test/a.jpg?x=1&amp;y=2"',payload['content'])
        self.assertIn('&lt;script&gt;',payload['content'])
        self.assertNotIn('<script>',payload['content'])
        self.assertNotIn('src="javascript:',payload['content'])

    def test_missing_product_image_does_not_block_notification(self):
        posting={'order_id':55,'posting_number':'55-1','created_at':stamp(self.now+1),
                 'products':[{'offer_id':'x','name':'New item','quantity':1,'price':'0'}]}
        self.store.orders_snapshot(SHOP,'fbs',[posting],self.now+2,self.now-10)
        self.store.prepare_batches(self.now+600,{**PUSH,'order_push_seconds':600})
        message=next(m for m in self.messages() if m['kind']=='order')
        self.assertIn('商品：New item',message['content'])
        self.assertIn('售价：¥0 / 件',message['content'])
        self.assertIn('图片待同步',message['content'])

    def test_product_appearing_between_all_and_sale_queries_is_not_lost(self):
        self.store.products_snapshot(SHOP, {}, {}, self.now)
        self.store.products_snapshot(SHOP, {}, {"9": {"product_id": 9}}, self.now + 10)
        self.assertEqual(len(self.events()), 1)

    def test_first_order_poll_suppresses_history_but_keeps_orders_created_during_request(self):
        started = self.store.order_start(SHOP["id"], self.now)
        postings = [{"order_id": 1, "posting_number": "old-1", "created_at": stamp(self.now - 60)},
                    {"order_id": 2, "posting_number": "new-1", "created_at": stamp(self.now + 1)}]
        self.store.orders_snapshot(SHOP, "fbs", postings, self.now + 2, started)
        self.assertEqual(len(self.events()), 1)
        self.assertIn(":2", self.events()[0]["event_key"])
        self.assertEqual(self.store.order_start(SHOP["id"], self.now + 100), started)

    def test_split_postings_and_fbo_fbs_do_not_duplicate_order_but_shops_are_separate(self):
        self.new_order(1)
        self.new_order(1, source="fbo")
        other = {**SHOP, "id": "shop02"}
        self.new_order(1, shop=other)
        self.assertEqual(len(self.events()), 2)

    def test_invalid_order_timestamp_does_not_commit_a_partial_snapshot(self):
        rows = [{"order_id": 1, "posting_number": "1", "created_at": stamp(self.now + 1)},
                {"order_id": 2, "posting_number": "2"}]
        with self.assertRaises(MonitorError):
            self.store.orders_snapshot(SHOP, "fbs", rows, self.now + 2, self.now)
        self.assertEqual(self.events(), [])

    def test_partial_product_api_failure_does_not_establish_baseline(self):
        transport = FakeTransport([{"result": {"items": [{"product_id": 1}]}},
                                   ApiError("Ozon", "/v3/product/list", 429)])
        with self.assertRaises(ApiError):
            poll(self.store, Ozon(SHOP, transport), SHOP, "products", {"poll_seconds": 10})
        with self.store.connection() as db:
            self.assertIsNone(Store.get(db, "product-baseline:shop01"))

    def test_products_pagination_and_total_items(self):
        first = [{"product_id": i} for i in range(1, 1001)]
        transport = FakeTransport([
            {"result": {"items": first, "last_id": "next", "total_items": 1001}},
            {"result": {"items": [{"product_id": 1001}], "last_id": "end", "total_items": 1001}},
        ])
        self.assertEqual(len(Ozon(SHOP, transport).products("ALL")), 1001)
        self.assertEqual(transport.requests[1][1]["last_id"], "next")

    def test_bad_pagination_cannot_silently_create_empty_baseline(self):
        transport = FakeTransport([{"result": {"items": [{"product_id": i} for i in range(1, 1001)]}}])
        with self.assertRaises(MonitorError):
            Ozon(SHOP, transport).products("ALL")
        with self.assertRaises(MonitorError):
            Ozon(SHOP, FakeTransport([{"result": {}}])).products("ALL")

    def test_current_fbs_and_fbo_shapes_and_empty_next_page_guard(self):
        item = {"posting_number": "1"}
        transport = FakeTransport([{"postings": [item], "has_next": False, "cursor": ""},
                                   {"postings": [item], "has_next": False, "cursor": ""}])
        api = Ozon(SHOP, transport)
        self.assertEqual(api.postings("fbs", self.now - 60, self.now), [item])
        self.assertEqual(api.postings("fbo", self.now - 60, self.now), [item])
        self.assertEqual(transport.requests[0][0], "/v4/posting/fbs/list")
        self.assertEqual(transport.requests[1][0], "/v3/posting/fbo/list")
        with self.assertRaises(MonitorError):
            Ozon(SHOP, FakeTransport([{"postings": [], "has_next": True, "cursor": "next"}])).postings(
                "fbs", self.now - 60, self.now)

    def test_order_cursor_pagination_uses_new_limit_and_does_not_silently_truncate(self):
        transport = FakeTransport([
            {"postings": [{"posting_number": "1"}], "has_next": True, "cursor": "next"},
            {"postings": [{"posting_number": "2"}], "has_next": False, "cursor": ""},
        ])
        items = Ozon(SHOP, transport).postings("fbo", self.now - 60, self.now)
        self.assertEqual(len(items), 2)
        self.assertEqual(transport.requests[1][1]["cursor"], "next")
        self.assertEqual(transport.requests[1][1]["limit"], 100)
        self.assertEqual(transport.requests[1][1]["sort_dir"], "asc")
        self.assertNotIn("offset", transport.requests[1][1])
        transport = FakeTransport([
            {"postings": [{"posting_number": "1"}], "has_next": True, "cursor": "same"},
            {"postings": [{"posting_number": "2"}], "has_next": True, "cursor": "same"},
        ])
        with self.assertRaises(MonitorError):
            Ozon(SHOP, transport).postings("fbs", self.now - 60, self.now)

    def test_rate_limit_keeps_orders_durable_and_products_out_of_order_messages(self):
        self.new_order(1)
        client = FakePush()
        notifier = Notifier(self.store, client, PUSH)
        notifier.tick(self.now + 2)
        self.new_order(2, offset=3)
        self.store.products_snapshot(SHOP, {}, {}, self.now)
        self.store.products_snapshot(SHOP, {"1": {}}, {"1": {"offer_id": "new"}}, self.now + 3)
        notifier.tick(self.now + 4)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(len([r for r in self.events() if r['batch'] is None]), 1)
        notifier.tick(self.now + 16)
        self.assertEqual(len(client.calls), 2)
        self.assertIn("新订单 1 笔", client.calls[1][0])
        self.assertNotIn("上新", client.calls[1][0])
        self.assertEqual({r["state"] for r in self.messages()}, {"submitted"})

    def test_submitted_is_not_delivered_or_resent_after_restart(self):
        self.new_order()
        client = FakePush()
        Notifier(self.store, client, PUSH).tick(self.now + 2)
        restarted = Notifier(Store(self.temp.name), client, PUSH)
        restarted.recover()
        restarted.tick(self.now + 30)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(self.messages()[0]["state"], "submitted")

    def test_failure_retry_is_durable_and_respects_retry_after(self):
        self.new_order()
        client = FakePush(failure=True)
        notifier = Notifier(self.store, client, PUSH)
        notifier.tick(self.now + 2)
        self.assertEqual(self.messages()[0]["state"], "pending")
        notifier.tick(self.now + 20)
        self.assertEqual(len(client.calls), 1)
        client.failure = False
        notifier.tick(self.now + 33)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(self.messages()[0]["state"], "submitted")

    def test_optional_confirmation_and_failed_delivery(self):
        self.new_order()
        notifier = Notifier(self.store, FakePush(), {**PUSH, "secret_key": "fake"})
        notifier.tick(self.now + 2)
        self.assertEqual(self.messages()[0]["state"], "sent")
        self.new_order(2, offset=20)
        notifier.client.status = 3
        notifier.tick(self.now + 22)
        self.assertEqual(self.messages()[1]["state"], "pending")

    def test_daily_budget_is_persisted_and_excess_events_are_not_dropped(self):
        cfg = {**PUSH, "daily_request_budget": 1}
        client = FakePush()
        self.new_order(1)
        Notifier(self.store, client, cfg).tick(self.now + 2)
        self.new_order(2, offset=3)
        Notifier(Store(self.temp.name), client, cfg).tick(self.now + 30)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(len([r for r in self.messages() if r["state"] == 'pending']), 1)

    def test_orders_wait_for_five_minute_boundary_and_restart_does_not_repeat(self):
        self.new_order()
        cfg = {**PUSH, 'order_push_seconds': 300}
        client = FakePush()
        Notifier(self.store, client, cfg).tick(self.now+299)
        self.assertEqual(client.calls, [])
        Notifier(Store(self.temp.name), client, cfg).tick(self.now+300)
        self.assertEqual(len(client.calls), 1)
        self.new_order(2, offset=301)
        Notifier(self.store, client, cfg).tick(self.now+320)
        self.assertEqual(len(client.calls), 1)
        Notifier(self.store, client, cfg).tick(self.now+600)
        self.assertEqual(len(client.calls), 2)
        self.assertIn('新订单 1 笔', client.calls[-1][0])
        self.assertIn('5分钟汇总', client.calls[-1][0])

    def test_ten_minute_window_groups_orders_skips_empty_windows_and_survives_restart(self):
        cfg = {**PUSH, 'order_push_seconds': 600}
        client = FakePush()
        self.new_order(1)
        Notifier(self.store, client, cfg).tick(self.now+300)
        self.assertEqual(client.calls, [])
        self.new_order(2, offset=350)
        Notifier(Store(self.temp.name), client, cfg).tick(self.now+599)
        self.assertEqual(client.calls, [])
        Notifier(Store(self.temp.name), client, cfg).tick(self.now+600)
        self.assertEqual(len(client.calls), 1)
        self.assertIn('新订单 2 笔｜10分钟汇总', client.calls[0][0])
        self.assertIn('订单 1｜FBS/rFBS', client.calls[0][1])
        self.assertIn('订单 2｜FBS/rFBS', client.calls[0][1])
        Notifier(Store(self.temp.name), client, cfg).tick(self.now+601)
        Notifier(Store(self.temp.name), client, cfg).tick(self.now+1200)
        self.assertEqual(len(client.calls), 1)
        self.new_order(3, offset=1201)
        Notifier(Store(self.temp.name), client, cfg).tick(self.now+1800)
        self.assertEqual(len(client.calls), 2)
        self.assertIn('新订单 1 笔｜10分钟汇总', client.calls[1][0])

    def test_quiet_hours_coalesce_night_orders_before_resuming_pushes(self):
        self.now = self.now - 12*3600
        wake = self.now + 8*3600
        cfg = {**PUSH,'quiet_end_hour':8,'order_push_seconds':600,'poll_seconds':300,'shops':[SHOP]}
        client = FakePush()
        self.store.initialize_schedule(self.now-3600)
        with self.store.connection() as db:
            Store.put(db,'digest:next-cutoff',self.now+22*3600)
            self.store.queue_message(db,'Earlier digest',['Earlier digest'],self.now-60,[],kind='product-digest')
        notifier = Notifier(self.store,client,cfg)
        self.new_order(1,offset=2)
        notifier.tick(self.now)
        notifier.tick(wake-1)
        self.assertFalse(self.store.reserve_send(cfg,wake-1))
        self.assertEqual(client.calls,[])
        self.assertIsNone(self.events()[0]['batch'])

        notifier.tick(wake)
        self.assertEqual(client.calls,[])
        # The first post-08:00 poll may discover orders created before 08:00.
        for order_id,created in ((2,wake-5),(3,wake+15)):
            posting={'order_id':order_id,'posting_number':f'{order_id}-1','created_at':stamp(created)}
            self.store.orders_snapshot(SHOP,'fbs',[posting],wake+30,self.now)
        for source in ('fbs','fbo'):
            self.store.health(SHOP['id'],source,now=wake+30)
        notifier = Notifier(Store(self.temp.name),client,cfg)
        notifier.tick(wake+30)
        self.assertEqual(len(client.calls),1)
        self.assertIn('新订单 2 笔｜夜间汇总',client.calls[0][0])
        self.assertIn('订单 2｜',client.calls[0][1])
        self.assertNotIn('订单 3｜',client.calls[0][1])
        notifier.tick(wake+60)
        notifier.tick(wake+600)
        self.assertEqual(len(client.calls),3)
        self.assertIn('新订单 1 笔｜10分钟汇总',client.calls[-1][0])

    def test_daily_digest_includes_all_shops_and_over_fifty_products_once(self):
        self.store.products_snapshot(SHOP, {}, {}, self.now)
        items = {str(i): {'offer_id': str(i)} for i in range(120)}
        self.store.products_snapshot(SHOP, items, items, self.now+10)
        cfg = {**PUSH, 'shops': [SHOP, {**SHOP, 'id':'shop02','name':'零上新店'}]}
        client = FakePush()
        notifier = Notifier(self.store, client, cfg)
        cutoff = self.now+10*3600
        notifier.tick(cutoff-1)
        self.assertEqual(client.calls, [])
        notifier.tick(cutoff)
        self.assertEqual(len(client.calls), 1)
        self.assertIn('120 件', client.calls[0][0])
        self.assertIn('零上新店】上新 0 件', client.calls[0][1])
        self.assertEqual(len([r for r in self.events() if r['batch'] is None]), 0)
        Notifier(Store(self.temp.name), client, cfg).tick(cutoff+60)
        self.assertEqual(len(client.calls), 1)

    def test_missed_digest_survives_restart_and_product_after_cutoff_waits_next_day(self):
        client = FakePush()
        notifier = Notifier(self.store, client, PUSH)
        notifier.tick(self.now)
        cutoff = self.now+10*3600
        self.store.products_snapshot(SHOP, {}, {}, self.now)
        self.store.products_snapshot(SHOP, {'1':{}}, {'1':{}}, cutoff+10)
        Notifier(Store(self.temp.name), client, PUSH).tick(cutoff+3600)
        self.assertIn('0 件', client.calls[0][0])
        notifier.tick(cutoff+86400)
        self.assertIn('1 件', client.calls[1][0])

    def test_digest_failure_is_retried_as_same_message(self):
        client = FakePush(failure=True)
        cutoff = self.now+36000
        notifier = Notifier(self.store, client, PUSH)
        notifier.tick(cutoff)
        self.assertEqual(self.messages()[0]['state'], 'pending')
        client.failure = False
        Notifier(Store(self.temp.name), client, PUSH).tick(cutoff+31)
        self.assertEqual(len(self.messages()), 1)
        self.assertEqual(self.messages()[0]['state'], 'submitted')

    def test_daily_digest_has_reserved_budget_and_priority(self):
        cfg = {**PUSH, 'daily_request_budget':2, 'reserve_digest':True}
        client = FakePush()
        self.new_order(1)
        notifier = Notifier(self.store, client, cfg)
        notifier.tick(self.now+2)
        self.new_order(2, offset=10)
        notifier.tick(self.now+30)
        self.assertEqual(len(client.calls), 1)
        notifier.tick(self.now+36000)
        self.assertEqual(len(client.calls), 2)
        self.assertIn('每日上新汇总', client.calls[-1][0])
        self.assertEqual(len([m for m in self.messages() if m['state']=='pending']),1)

    def test_incremental_order_poll_uses_previous_success_with_overlap(self):
        self.store.order_start(SHOP['id'], self.now-86400)
        self.store.health(SHOP['id'], 'fbs', now=self.now-300)
        transport = FakeTransport([{'postings':[], 'has_next':False}])
        with patch('monitor.time.time', return_value=self.now):
            poll(self.store, Ozon(SHOP,transport), SHOP, 'fbs', {'poll_seconds':300,'order_lookback_hours':48})
        self.assertEqual(len(transport.requests),1)
        self.assertEqual(transport.requests[0][1]['filter']['since'], stamp(self.now-1200))

    def test_transport_splits_invalid_json_from_network_and_honors_account_cooldown(self):
        transport = Transport()
        error = urllib.error.HTTPError('https://example.invalid',429,'rate limit',{'Retry-After':'120'},io.BytesIO())
        with patch('monitor.urllib.request.urlopen', side_effect=error) as request:
            with self.assertRaises(ApiError) as caught:
                transport.request('Ozon','https://example.invalid','/fbs',{}, {'Client-Id':'test'})
            self.assertEqual(caught.exception.retry_after,120)
            with self.assertRaises(ApiError) as caught:
                transport.request('Ozon','https://example.invalid','/products',{}, {'Client-Id':'test'})
            self.assertEqual(caught.exception.code,'rate-limit-cooldown')
            self.assertEqual(request.call_count,1)
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'<html>gateway unavailable</html>'
        with patch('monitor.urllib.request.urlopen', return_value=response):
            with self.assertRaises(ApiError) as caught:
                Transport().request('pushplus','https://example.invalid','/send',{})
            self.assertEqual(caught.exception.code,'invalid-json')
        with patch('monitor.urllib.request.urlopen', side_effect=TimeoutError()):
            with self.assertRaises(ApiError) as caught:
                Transport().request('pushplus','https://example.invalid','/send',{})
            self.assertEqual(caught.exception.code,'timeout')

    def test_retry_after_supports_http_date(self):
        with patch('monitor.time.time', return_value=self.now):
            text=dt.datetime.fromtimestamp(self.now+120,dt.timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
            self.assertGreaterEqual(Transport.retry_seconds(text),120)
            self.assertEqual(Transport.retry_seconds('invalid'),0)

    def test_pushplus_https_payload_and_optional_result_api(self):
        transport = FakeTransport([{"code": 200, "data": "receipt"},
                                   {"code": 200, "data": {"accessKey": "access", "expiresIn": 7200}},
                                   {"code": 200, "data": {"status": 2}}])
        client = Pushplus({**PUSH, "secret_key": "fake-secret"}, transport)
        self.assertEqual(client.send("title", "body"), "receipt")
        self.assertEqual(client.result("receipt", self.now), 2)
        self.assertEqual(transport.requests[0][1]["topic"], "test-family")
        self.assertEqual(transport.requests[0][1]["channel"], "wechat")
        self.assertEqual(transport.requests[2][2]["access-key"], "access")

    def test_config_checks_do_not_echo_secrets_and_disabled_shops_can_be_empty(self):
        path = Path(self.temp.name) / "config.json"
        cfg = {"pushplus": PUSH, "shops": [SHOP, {"enabled": False}]}
        path.write_text(json.dumps(cfg), encoding="utf-8")
        self.assertEqual(len(load_config(path)["shops"]), 1)
        cfg["shops"].append(SHOP)
        path.write_text(json.dumps(cfg), encoding="utf-8")
        with self.assertRaises(MonitorError) as exc:
            load_config(path)
        self.assertNotIn(SHOP["api_key"], str(exc.exception))

    def test_second_process_lock_is_rejected(self):
        # File locks prevent a BAT, service or container from sharing one DB concurrently.
        with instance_lock(self.temp.name):
            with self.assertRaises(MonitorError):
                with instance_lock(self.temp.name):
                    self.fail("second lock acquired")


if __name__ == "__main__":
    unittest.main()
