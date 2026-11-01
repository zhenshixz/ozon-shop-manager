"""Focused regressions for procurement totals and first-picture delivery."""
import datetime as dt
from pathlib import Path
import tempfile
import unittest

import monitor
import profit_core as core
import profit_sync as sync
from profit_web import create_app


class ImagesAPI:
    def __init__(self):
        self.offer_items=[]; self.sku_items=[]; self.calls=[]

    def product_info(self,identifiers,by_sku=False):
        self.calls.append((identifiers,by_sku))
        return self.sku_items if by_sku else self.offer_items


class CostImagesTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store=monitor.Store(self.temp.name)
        self.shops=[{'id':'one','name':'店铺一'},{'id':'two','name':'店铺二'}]
        self.now=dt.datetime(2026,10,31,12,tzinfo=core.CHINA).timestamp()
        self.api=ImagesAPI()

    def capture(self,posting='A',cost=None,quantity=1,status='awaiting_packaging',shop=0,created=None,sku='123'):
        data={'posting_number':posting,'order_number':posting,'status':status,
              'created_at':monitor.stamp(self.now if created is None else created),
              'products':[{'offer_id':'hat','sku':sku,'name':'帽子','quantity':quantity,
                           'price':{'amount':'39','currency':'CNY'},'weight':.339}]}
        with self.store.connection() as db:
            core.capture_postings(db,self.shops[shop],'fbs',[data],self.now)
            oid=db.execute('SELECT id FROM profit_orders WHERE shop=? AND posting=?',
                (self.shops[shop]['id'],posting)).fetchone()[0]
            if cost is not None: db.execute('UPDATE profit_lines SET purchase_unit=? WHERE order_id=?',(cost,oid))
        return oid

    def statistics(self,**kwargs):
        with self.store.connection() as db:
            return core.purchase_statistics(db,self.shops,**kwargs)

    def picture(self,oid):
        with self.store.connection() as db:
            return db.execute('SELECT image_url FROM profit_lines WHERE order_id=?',(oid,)).fetchone()[0]

    def test_picture_retries_every_ten_seconds_until_success_then_stops(self):
        oid=self.capture()
        sync.refresh_images(self.store,self.api,self.shops[0],self.now)
        self.assertEqual(len(self.api.calls),2)
        sync.refresh_images(self.store,self.api,self.shops[0],self.now+9)
        self.assertEqual(len(self.api.calls),2)
        self.api.offer_items=[{'offer_id':'hat','primary_image':['https://ir.ozone.ru/hat.jpg']}]
        sync.refresh_images(self.store,self.api,self.shops[0],self.now+10)
        self.assertEqual(len(self.api.calls),3)
        self.assertEqual(self.picture(oid),'https://ir.ozone.ru/hat.jpg')
        sync.refresh_images(self.store,self.api,self.shops[0],self.now+86400)
        self.assertEqual(len(self.api.calls),3)
        self.assertEqual(monitor.job_interval('profit_images',{}),10)

    def test_successful_cache_reused_during_capture_without_waiting_for_worker(self):
        oid=self.capture()
        self.api.offer_items=[{'offer_id':'hat','images':['https://ir.ozone.ru/hat.jpg']}]
        sync.refresh_images(self.store,self.api,self.shops[0],self.now)
        second=self.capture('B')
        self.assertEqual(self.picture(second),self.picture(oid))
        other=self.capture('C',shop=1)
        self.assertEqual(self.picture(other),'')
        sync.refresh_images(self.store,self.api,self.shops[0],self.now+10)
        self.assertEqual(len(self.api.calls),1)

    def test_sku_fallback_recovers_missing_offer_result_and_caches_both_identifiers(self):
        oid=self.capture()
        self.api.sku_items=[{'sku':123,'primary_image':'https://ir.ozone.ru/sku.jpg'}]
        sync.refresh_images(self.store,self.api,self.shops[0],self.now)
        self.assertEqual(self.picture(oid),'https://ir.ozone.ru/sku.jpg')
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM profit_image_checks WHERE image_url!=\'\'').fetchone()[0],2)
        self.assertEqual(self.picture(self.capture('B')),'https://ir.ozone.ru/sku.jpg')

    def test_existing_order_image_is_reused_even_without_product_cache(self):
        oid=self.capture(cost='12')
        with self.store.connection() as db:
            db.execute('UPDATE profit_lines SET image_url=? WHERE order_id=?',('https://ir.ozone.ru/known.jpg',oid))
        second=self.capture('B')
        self.assertEqual(self.picture(second),'https://ir.ozone.ru/known.jpg')
        sync.refresh_images(self.store,self.api,self.shops[0],self.now)
        self.assertEqual(self.api.calls,[])

    def test_total_includes_cancelled_returned_partial_and_zero_costs(self):
        self.capture('A','12.345',2)
        self.capture('B','5.1',2,'cancelled')
        self.capture('C','2',3,'returned')
        self.capture('D','0')
        self.capture('E')
        stats=self.statistics()
        self.assertEqual(stats['total_cost'],'40.89')
        self.assertEqual(stats['cancelled_returned_cost'],'16.20')
        self.assertEqual(stats['active_cost'],'24.69')
        self.assertEqual(stats['missing_packages'],1)
        self.assertEqual(stats['filled_packages'],4)

    def test_dates_are_inclusive_china_calendar_days_and_shop_filter_applies(self):
        start=dt.datetime(2026,10,31,tzinfo=core.CHINA).timestamp()
        self.capture('before','100',created=start-1)
        self.capture('first','1',created=start)
        self.capture('last','2',status='returned',created=start+86399)
        self.capture('next','200',created=start+86400)
        self.capture('other','500',shop=1,created=start+10)
        stats=self.statistics(shop_id='one',date_from='2026-10-31',date_to='2026-10-31')
        self.assertEqual(stats['total_cost'],'3.00')
        self.assertEqual(stats['cancelled_returned_cost'],'2.00')
        self.assertFalse(stats['all_time'])
        self.assertEqual(self.statistics(shop_id='unknown')['total_cost'],'0.00')

    def test_totals_are_not_truncated_at_order_list_limit_and_round_only_at_end(self):
        for index in range(501): self.capture(str(index),'.005')
        self.assertEqual(self.statistics()['total_cost'],'2.51')
        self.assertEqual(self.statistics()['filled_packages'],501)

    def test_authenticated_read_endpoints_validate_dates_and_keep_manual_inputs(self):
        oid=self.capture(cost='12',quantity=2)
        with self.store.connection() as db:
            line=db.execute('SELECT id FROM profit_lines WHERE order_id=?',(oid,)).fetchone()[0]
        app=create_app({'database':str(self.store.path),'shops':self.shops,'access_token':'fake',
                        'secret_key':'fake','secure_cookie':False})
        client=app.test_client(); base='/ozon-profit/api'
        self.assertEqual(client.get(base+'/cost-statistics').status_code,401)
        self.assertEqual(client.get(base+'/images?ids='+str(line)).status_code,401)
        client.post(base+'/login',json={'token':'fake'})
        self.assertEqual(client.get(base+'/cost-statistics').json['statistics']['total_cost'],'24.00')
        self.assertEqual(client.get(base+'/cost-statistics?date_from=2026-11-01&date_to=2026-10-31').status_code,400)
        self.assertEqual(client.get(base+'/cost-statistics?date_from=wrong').status_code,400)
        self.assertEqual(client.get(base+'/images?ids=1%20OR%201').status_code,400)
        self.assertEqual(client.get(base+'/images?ids='+str(line)).json['images'],[{'id':line,'image_url':''}])
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT purchase_unit FROM profit_lines WHERE id=?',(line,)).fetchone()[0],'12')

    def test_cost_order_details_match_scope_show_prices_and_paginate(self):
        for index in range(23):self.capture(str(index),cost='12',quantity=2)
        self.capture('other-shop',cost='50',shop=1)
        app=create_app({'database':str(self.store.path),'shops':self.shops,'access_token':'fake','secret_key':'fake','secure_cookie':False})
        client=app.test_client();base='/ozon-profit/api/cost-statistics'
        self.assertEqual(client.get(base+'?details=1').status_code,401)
        client.post('/ozon-profit/api/login',json={'token':'fake'})
        page=client.get(base+'?details=1&shop=one&date_from=2026-10-31&date_to=2026-10-31').json
        self.assertEqual(page['statistics']['package_count'],23)
        self.assertEqual(page['statistics']['total_cost'],'552.00')
        self.assertEqual(len(page['details']['orders']),20)
        self.assertTrue(page['details']['has_more'])
        order=page['details']['orders'][0]
        self.assertEqual(order['recorded_purchase_cost'],'24.00')
        self.assertEqual(order['lines'][0]['purchase_subtotal'],'24.00')
        self.assertEqual(order['lines'][0]['unit_price'],'39')
        second=client.get(base+'?details=1&shop=one&offset=20').json['details']
        self.assertEqual(len(second['orders']),3);self.assertFalse(second['has_more'])
        self.assertEqual(client.get(base+'?details=1&offset=-1').status_code,400)

    def test_cost_details_hide_unfilled_before_pagination_but_keep_zero(self):
        self.capture('unfilled')
        self.capture('paid',cost='12')
        self.capture('zero',cost='0')
        with self.store.connection() as db:
            details=core.purchase_order_details(db,self.shops)
        self.assertEqual(details['total_count'],2)
        self.assertEqual({o['order_number'] for o in details['orders']},{'paid','zero'})


if __name__=='__main__': unittest.main()
