import copy
import datetime as dt
from pathlib import Path
import tempfile
import unittest

import monitor
import profit_core as core
import profit_sync as sync
from profit_web import create_app


class FakeAPI:
    def __init__(self):
        self.history=[];self.details={};self.products=[];self.calls=[];self.error=None
    def postings(self,source,start,end):
        self.calls.append(('history',source,start,end))
        if self.error: raise self.error
        return self.history
    def posting(self,source,number):
        self.calls.append(('detail',source,number));return self.details[number]
    def product_info(self,identifiers,by_sku=False):
        self.calls.append(('images',identifiers,by_sku))
        if self.error: raise self.error
        return self.products


class ProfitSyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=monitor.Store(self.tmp.name)
        self.shop={'id':'s1','name':'Shop one'};self.other={'id':'s2','name':'Shop two'}
        self.now=dt.datetime(2026,10,28,12,tzinfo=core.CHINA).timestamp()
        self.api=FakeAPI()

    def posting(self,number='A-1',order='A',status='awaiting_packaging',age=86400):
        return {'posting_number':number,'order_number':order,'created_at':monitor.stamp(self.now-age),
                'status':status,'products':[{'offer_id':'hat','sku':123,'name':'Hat','quantity':1,
                'price':{'amount':'39','currency':'CNY'},'weight':.339}]}

    def capture(self,post,shop=None,seen=None):
        with self.store.connection() as db:
            core.capture_postings(db,shop or self.shop,'fbs',[post],self.now-3600 if seen is None else seen)

    def cover(self,shops=None):
        with self.store.connection() as db:
            for shop in shops or [self.shop]:
                for source in ('fbs','fbo'):
                    db.execute('INSERT OR REPLACE INTO profit_sync_state VALUES (?,?,?,?)',
                               (shop['id'],source,sync.history_start(self.now),self.now))

    def statistics(self,shop_id=''):
        with self.store.connection() as db:
            return core.order_statistics(db,[self.shop,self.other],shop_id,now=self.now)

    def test_history_backfill_has_no_notification_side_effects_and_keeps_cost(self):
        post=self.posting();self.capture(post)
        with self.store.connection() as db:
            db.execute("UPDATE profit_lines SET purchase_unit='12',price_override='39'")
        self.api.history=[{**post,'status':'delivered'},self.posting('B-1','B',age=3*86400)]
        self.api.details={p['posting_number']:p for p in self.api.history}
        sync.refresh_orders(self.store,self.api,self.shop,'fbs',self.now)
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM profit_orders').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT purchase_unit FROM profit_lines ORDER BY id').fetchone()[0],'12')
            self.assertEqual(db.execute('SELECT status FROM profit_orders ORDER BY id').fetchone()[0],'delivered')
            self.assertEqual(db.execute('SELECT history_since FROM profit_sync_state').fetchone()[0],sync.history_start(self.now))

    def test_old_active_order_refresh_preserves_created_price_and_manual_inputs(self):
        self.capture(self.posting(age=45*86400));self.cover()
        with self.store.connection() as db:
            db.execute("UPDATE profit_lines SET purchase_unit='12',price_override='40'")
        self.api.details={'A-1':{'posting_number':'A-1','status':'delivered','products':[]}}
        sync.refresh_orders(self.store,self.api,self.shop,'fbs',self.now)
        with self.store.connection() as db:
            order=core.load_order(db,1)
            self.assertEqual(order['status'],'delivered');self.assertEqual(order['seen'],self.now)
            self.assertEqual(order['created'],self.now-45*86400)
            self.assertEqual(order['lines'][0]['price_override'],'40')
            self.assertEqual(order['lines'][0]['purchase_unit'],'12')
        self.assertEqual(self.api.calls,[('detail','fbs','A-1')])

    def test_invalid_detail_does_not_claim_success(self):
        self.capture(self.posting());self.cover()
        self.api.details={'A-1':{'posting_number':'wrong','status':'delivered'}}
        with self.assertRaises(ValueError):sync.refresh_orders(self.store,self.api,self.shop,'fbs',self.now)
        with self.store.connection() as db:
            row=db.execute('SELECT status,seen FROM profit_orders').fetchone()
            self.assertEqual(row['status'],'awaiting_packaging');self.assertEqual(row['seen'],self.now-3600)

    def test_failed_or_invalid_history_keeps_coverage_incomplete(self):
        self.api.error=monitor.ApiError('Ozon','/list',429)
        with self.assertRaises(monitor.ApiError):sync.refresh_orders(self.store,self.api,self.shop,'fbs',self.now)
        self.api.error=None;self.api.history=[{'posting_number':'bad','status':'delivered'}]
        with self.assertRaises(ValueError):sync.refresh_orders(self.store,self.api,self.shop,'fbs',self.now)
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM profit_sync_state').fetchone()[0],0)

    def test_images_primary_fallback_cache_and_shop_isolation(self):
        self.capture(self.posting());self.capture(self.posting(),self.other)
        self.api.products=[{'offer_id':'hat','primary_image':['https://ir.ozone.ru/hat.jpg'],'images':[]}]
        sync.refresh_images(self.store,self.api,self.shop,self.now)
        with self.store.connection() as db:
            lines=db.execute('SELECT image_url FROM profit_lines ORDER BY id').fetchall()
            self.assertEqual(lines[0][0],'https://ir.ozone.ru/hat.jpg');self.assertEqual(lines[1][0],'')
        self.capture(self.posting('B-1','B'),seen=self.now)
        sync.refresh_images(self.store,self.api,self.shop,self.now+10)
        self.assertEqual(len(self.api.calls),1)
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM profit_lines WHERE image_url!=''").fetchone()[0],2)
        self.assertEqual(sync.image_url({'primary_image':'http://bad/image','images':['https://ir.ozone.ru/fallback.jpg']}),'https://ir.ozone.ru/fallback.jpg')

    def test_existing_picture_is_preserved_without_unnecessary_refetch(self):
        self.capture(self.posting())
        with self.store.connection() as db:db.execute("UPDATE profit_lines SET image_url='https://ir.ozone.ru/manual.jpg'")
        sync.refresh_images(self.store,self.api,self.shop,self.now)
        sync.refresh_images(self.store,self.api,self.shop,self.now+60)
        self.assertEqual(len(self.api.calls),0)
        self.api.products=[{'offer_id':'hat','images':['https://ir.ozone.ru/new.jpg']}]
        sync.refresh_images(self.store,self.api,self.shop,self.now+3601)
        self.assertEqual(len(self.api.calls),0)
        with self.store.connection() as db:self.assertEqual(db.execute('SELECT image_url FROM profit_lines').fetchone()[0],'https://ir.ozone.ru/manual.jpg')

    def test_daily_average_counts_orders_zero_days_and_mixed_split_cancellations(self):
        self.cover([self.shop,self.other])
        self.capture(self.posting('A-1','A',status='cancelled',age=60))
        self.capture(self.posting('A-2','A',age=60))
        self.capture(self.posting('B-1','B',status='cancelled',age=60))
        self.capture(self.posting('A-1','A',age=86400),self.other)
        stats=self.statistics()
        self.assertEqual(stats['total_orders'],2);self.assertEqual(stats['today_orders'],1)
        self.assertEqual(stats['daily_average'],'0.29');self.assertEqual(len(stats['daily']),7)
        self.assertEqual(sum(x['orders']==0 for x in stats['daily']),5)
        self.assertEqual(self.statistics('s1')['total_orders'],1)

    def test_calendar_boundary_and_incomplete_history(self):
        start=dt.datetime.fromtimestamp(self.now,core.CHINA).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()-6*86400
        self.capture(self.posting(age=self.now-start))
        self.capture(self.posting('B-1','B',age=self.now-start+1))
        stats=self.statistics('s1')
        self.assertEqual(stats['total_orders'],1);self.assertIsNone(stats['daily_average'])
        self.cover();self.assertEqual(self.statistics('s1')['daily_average'],'0.14')

    def test_thirty_day_average_uses_full_window_and_deduplicates(self):
        self.capture(self.posting('A-1','A',age=10*86400))
        self.capture(self.posting('A-2','A',age=10*86400))
        self.capture(self.posting('B-1','B',status='cancelled',age=12*86400))
        self.capture(self.posting('C-1','C',age=86400))
        with self.store.connection() as db:
            self.assertFalse(core.order_statistics(db,[self.shop],days=30,now=self.now)['complete'])
        self.cover()
        with self.store.connection() as db:
            result=core.order_statistics(db,[self.shop],days=30,now=self.now)
            self.assertEqual(result['total_orders'],2)
            self.assertEqual(result['daily_average'],'0.07')
            self.assertEqual(len(result['daily']),30)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)

    def test_statistics_cover_zero_order_shops_and_are_not_limited_to_500(self):
        self.cover([self.shop,self.other])
        with self.store.connection() as db:
            db.executemany('''INSERT INTO profit_orders(shop,shop_name,posting,order_number,source,status,created,seen)
                VALUES ('s1','one',?,?,'fbs','delivered',?,?)''',
                [(str(i),str(i),self.now-60,self.now) for i in range(501)])
        self.assertEqual(self.statistics()['total_orders'],501)
        self.assertEqual(self.statistics('s2')['daily_average'],'0.00')

    def test_cancelled_and_returned_profit_excluded_but_cost_retained(self):
        order={'status':'cancelled','lines':[{'quantity':1,'unit_price':'39','currency':'CNY','purchase_unit':'12','weight_g':'339'}]}
        result=core.calculate(order)
        self.assertEqual(result['profit'],'8.27');self.assertFalse(result['included_in_summary'])
        order['status']='returned';self.assertFalse(core.calculate(order)['included_in_summary'])
        order['status']='delivered';self.assertTrue(core.calculate(order)['included_in_summary'])

    def test_statistics_endpoint_requires_login_and_contains_zero_order_shops(self):
        self.cover([self.shop,self.other])
        app=create_app({'database':str(self.store.path),'access_token':'test','secret_key':'test',
                        'secure_cookie':False,'shops':[self.shop,self.other]})
        client=app.test_client();prefix='/ozon-profit/api'
        self.assertEqual(client.get(prefix+'/statistics').status_code,401)
        client.post(prefix+'/login',json={'token':'test'})
        self.assertEqual(len(client.get(prefix+'/bootstrap').json['shops']),2)
        self.assertEqual(client.get(prefix+'/statistics?shop=s2').json['statistics']['total_orders'],0)


if __name__=='__main__':unittest.main()
