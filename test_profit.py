import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
import profit_core as p
from profit_web import create_app
import monitor

class ProfitTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'monitor.sqlite3'
        self.shop={'id':'shop01','name':'Luo001'}
        self.post={'posting_number':'TEST-1','order_id':10,'order_number':'TEST',
            'created_at':'2026-10-26T01:00:00Z','status':'awaiting_deliver','products':[
                {'offer_id':'bag','sku':6130292548,'name':'测试包','quantity':1,'weight':.339,'price':{'amount':'39','currency':'CNY'}}]}
        with p.connection(self.path) as db:
            p.ensure_schema(db);p.capture_postings(db,self.shop,'fbs',[self.post],time.time())
            self.order=p.load_order(db,1)
    def tearDown(self): self.tmp.cleanup()
    def filled(self,price='39',cost='12',weight='369'):
        return p.apply_inputs(copy.deepcopy(self.order),{'lines':[{'id':self.order['lines'][0]['id'],'purchase_unit':cost,'price_override':price}],
            'inputs':{'weight_override':weight},'weight_mode':'custom','weight_confirmed':True,'expected_api_weight_g':'339','expected_weight_override':None})
    def test_attachment_examples(self):
        for price,cost,weight,profit in [('57','23','400','11.2400'),('34','12','369','3.2257'),('39','12','369','7.5257'),('39','12','339','8.2667')]:
            c=self.filled(price,cost,weight)['calculation']
            self.assertEqual(p.D(c['profit_exact']),p.D(profit))
        self.assertEqual(self.filled('34')['calculation']['sales_margin'],'9.49')
        self.assertEqual(self.filled('34')['calculation']['cost_margin'],'26.88')
    def test_missing_cost_not_zero(self):
        self.assertIsNone(self.order['calculation']['profit'])
        self.assertEqual(self.order['calculation']['api_weight_g'],'339.000')
        self.assertEqual(self.filled(cost='0')['calculation']['status'],'ready')
    def test_weight_source_is_explicit_and_clearing_custom_restores_api(self):
        order=self.filled(weight='369')
        self.assertEqual(order['calculation']['weight_source'],'custom')
        self.assertEqual(order['calculation']['custom_weight_g'],'369')
        order['inputs']['weight_override']=None
        result=p.calculate(order)
        self.assertEqual(result['weight_source'],'api')
        self.assertEqual(p.D(result['weight_g']),p.D('339'))
    def test_weight_change_is_audited_without_duplicate_on_other_edits(self):
        with p.connection(self.path) as db:
            order=self.filled(weight='369');p.save_order(db,order,now=1)
            p.save_order(db,order,now=2)
            order['inputs']['weight_override']=None;p.save_order(db,order,now=3)
            rows=list(db.execute('SELECT old_weight,new_weight,source FROM profit_weight_audit ORDER BY id'))
            self.assertEqual([tuple(row) for row in rows],[(None,'369','web-save'),('369',None,'web-save')])
    def weight_payload(self):
        return {'lines':[{'id':self.order['lines'][0]['id'],'purchase_unit':'12','price_override':None}],
                'inputs':{'weight_override':'369'},'expected_weight_override':None}
    def test_legacy_or_unconfirmed_weight_cannot_silently_override_api(self):
        payload=self.weight_payload()
        with self.assertRaisesRegex(ValueError,'重量来源未确认'):
            p.apply_inputs(copy.deepcopy(self.order),payload)
        payload.update(weight_mode='custom',expected_api_weight_g='339')
        with self.assertRaisesRegex(ValueError,'勾选确认'):
            p.apply_inputs(copy.deepcopy(self.order),payload)
        result=p.apply_inputs(copy.deepcopy(self.order),payload,require_confirmation=False)
        self.assertEqual(result['calculation']['weight_g'],'369')
        payload['weight_confirmed']=True
        self.assertEqual(p.apply_inputs(copy.deepcopy(self.order),payload)['calculation']['weight_g'],'369')
    def test_api_mode_ignores_no_autofilled_override_and_stale_reference_is_rejected(self):
        payload=self.weight_payload();payload['weight_mode']='api'
        with self.assertRaisesRegex(ValueError,'API重量模式'):
            p.apply_inputs(copy.deepcopy(self.order),payload)
        payload.update(weight_mode='custom',weight_confirmed=True,expected_api_weight_g='180')
        with self.assertRaisesRegex(ValueError,'已变化'):
            p.apply_inputs(copy.deepcopy(self.order),payload)
    def test_explicit_api_mode_restores_api_and_preserves_existing_custom_without_reconfirmation(self):
        custom=self.filled(weight='369');payload=self.weight_payload()
        preserved=p.apply_inputs(copy.deepcopy(custom),payload)
        self.assertEqual(preserved['calculation']['weight_g'],'369')
        payload['weight_mode']='api';payload['inputs']['weight_override']=None;payload['expected_weight_override']='369'
        restored=p.apply_inputs(copy.deepcopy(custom),payload)
        self.assertEqual(restored['calculation']['weight_source'],'api')
        self.assertEqual(p.D(restored['calculation']['weight_g']),p.D('339'))
    def test_http_weight_guard_rejects_unconfirmed_changes_and_logs_confirmed_source(self):
        app=create_app({'database':str(self.path),'access_token':'test','secret_key':'test','secure_cookie':False})
        client=app.test_client();prefix='/ozon-profit/api';client.post(prefix+'/login',json={'token':'test'})
        headers={'X-CSRF-Token':client.get(prefix+'/bootstrap').json['csrf']}
        payload=self.weight_payload()
        self.assertEqual(client.post(prefix+'/orders/1',json=payload,headers=headers).status_code,400)
        payload.update(weight_mode='custom',expected_api_weight_g='339')
        self.assertEqual(client.post(prefix+'/orders/1/preview',json=payload,headers=headers).status_code,200)
        self.assertEqual(client.post(prefix+'/orders/1',json=payload,headers=headers).status_code,400)
        self.assertEqual(client.get(prefix+'/orders/1').json['order']['calculation']['weight_source'],'api')
        payload['weight_confirmed']=True
        self.assertEqual(client.post(prefix+'/orders/1',json=payload,headers=headers).status_code,200)
        with p.connection(self.path) as db:
            audit=json.loads(db.execute('SELECT source FROM profit_weight_audit').fetchone()[0])
            self.assertEqual(audit['channel'],'web-save');self.assertEqual(audit['weight_mode'],'custom')
            self.assertIn('request_id',audit);self.assertIn('session',audit)
    def test_stale_page_cannot_clear_or_replace_another_pages_custom_weight(self):
        custom=self.filled(weight='369');payload=self.weight_payload()
        payload.update(weight_mode='api',expected_weight_override=None)
        payload['inputs']['weight_override']=None
        with self.assertRaisesRegex(ValueError,'其他页面修改'):
            p.apply_inputs(copy.deepcopy(custom),payload)
        payload.update(weight_mode='custom',weight_confirmed=True,expected_api_weight_g='339')
        payload['inputs']['weight_override']='400'
        with self.assertRaisesRegex(ValueError,'其他页面修改'):
            p.apply_inputs(copy.deepcopy(custom),payload)
    def test_currency_requires_override(self):
        order=copy.deepcopy(self.order);order['lines'][0].update(currency='RUB',purchase_unit='12')
        self.assertIsNone(p.calculate(order)['profit'])
        order['lines'][0]['price_override']='39'
        self.assertEqual(p.calculate(order)['profit'],'8.27')
    def test_multi_quantity_charges_once(self):
        order=self.filled(weight='678');order['lines'][0]['quantity']=2
        order['inputs']['template']='xs'
        c=p.calculate(order)
        self.assertEqual(c['revenue'],'78.00');self.assertEqual(c['purchase_cost'],'24.00')
        self.assertEqual(p.D(c['profit_exact']),p.D('78')-24-p.D('2.9')-p.D('.0247')*678-p.D('78')*p.D('.14')-2)
    def test_inputs_validate(self):
        for bad in ['NaN','Infinity','-1','10000001','0.000000001',True]:
            with self.assertRaises(ValueError):self.filled(cost=bad)
        with self.assertRaises(ValueError):p.apply_inputs(copy.deepcopy(self.order),{'lines':[]})
        self.assertIsNone(self.filled(weight='25001')['calculation']['profit'])
    def test_snapshot_keeps_manual_inputs_and_order_price(self):
        order=self.filled()
        with p.connection(self.path) as db:
            p.save_order(db,order)
            changed=copy.deepcopy(self.post);changed['status']='delivered';changed['products'][0]['price']['amount']='34.44'
            p.capture_postings(db,self.shop,'fbs',[changed],time.time())
            actual=p.load_order(db,1)
            self.assertEqual(actual['lines'][0]['unit_price'],'39')
            self.assertEqual(actual['lines'][0]['purchase_unit'],'12')
            self.assertEqual(actual['status'],'delivered')
            self.assertEqual(actual['calculation']['profit'],'7.53')
            self.assertEqual(db.execute('select count(*) from profit_orders').fetchone()[0],1)
    def test_all_attachment_templates(self):
        for template in p.TEMPLATES:
            order=self.filled();order['inputs']['template']=template['id'];c=p.calculate(order)
            self.assertEqual(p.D(c['shipping_exact']),p.D(template['fix'])+p.D(template['per_g'])*369)
    def test_confirmed_auto_price_and_weight_boundaries(self):
        for price,weight,expected in [
            ('135','499.99','xs'),('135','500','budget'),('135','2000','budget'),
            ('135','25000','budget'),('135.01','499.99','small'),
            ('135.01','500','small'),('135.01','2000','small'),
            ('135.01','2000.01','big'),('136','25000','big'),('1000','339','small')]:
            with self.subTest(price=price,weight=weight):
                result=self.filled(price=price,weight=weight)['calculation']
                self.assertEqual(result['template']['id'],expected)
                self.assertEqual(result['status'],'ready')
        for weight in (None,p.D('0'),p.D('25000.01')):
            self.assertIsNone(p.recommend_template(p.D('389'),weight))
        for weight in ('0','25000.01'):
            order=self.filled(price='389');order['inputs']['weight_override']=weight
            self.assertIsNone(p.calculate(order)['profit'])
        order=self.filled(price='389');order['inputs']['weight_override']=None;order['lines'][0]['weight_g']=None
        self.assertIsNone(p.calculate(order)['profit'])
    def test_four_worksheet_examples_with_confirmed_seventeen_percent(self):
        for price,cost,weight,template,profit in [
            ('115','82','360','xs','3.1080'),('115','85','500','budget','-18.5500'),
            ('389','111','200','small','189.5500'),('228','66.9','2290','big','43.4040')]:
            with self.subTest(template=template):
                result=self.filled(price,cost,weight)['calculation']
                self.assertEqual(result['template']['id'],template)
                self.assertEqual(p.D(result['profit_exact']),p.D(profit))
    def test_auto_tracks_effective_weight_and_price_without_manual_template(self):
        order=self.filled(price='115',weight='500')
        self.assertEqual(order['inputs']['template'],'auto')
        self.assertEqual(order['calculation']['template']['id'],'budget')
        order['inputs']['weight_override']=None
        self.assertEqual(p.calculate(order)['template']['id'],'xs')
        order['lines'][0]['price_override']='389'
        self.assertEqual(p.calculate(order)['template']['id'],'small')
        order['lines'][0]['weight_g']='2001'
        result=p.calculate(order)
        self.assertEqual(result['template']['id'],'big')
        self.assertEqual(result['rate'],'19')
    def test_monitor_split_postings_capture_without_extra_notification(self):
        store=monitor.Store(Path(self.tmp.name)/'store')
        second=copy.deepcopy(self.post);second['posting_number']='TEST-2'
        store.orders_snapshot(self.shop,'fbs',[self.post,second],time.time(),0)
        store.orders_snapshot(self.shop,'fbs',[self.post,second],time.time(),0)
        with store.connection() as db:
            self.assertEqual(db.execute('select count(*) from events').fetchone()[0],1)
            self.assertEqual(db.execute('select count(*) from profit_orders').fetchone()[0],2)
    def test_private_entry_only_in_order_summary(self):
        store=monitor.Store(Path(self.tmp.name)/'store')
        store.orders_snapshot(self.shop,'fbs',[self.post],time.time()-600,0)
        store.prepare_batches(time.time(),{'profit_url':'https://example.test/#k=secret','shops':[self.shop]})
        with store.connection() as db:
            order=db.execute("select content from messages where kind='order'").fetchone()
            self.assertIn('#k=secret',order[0])
    def test_profit_push_has_clickable_escaped_link(self):
        client=monitor.Pushplus({'token':'test','topic':''},None)
        with patch.object(p,'entry_url',return_value='https://example.test/#k=secret'),patch.object(client,'call',return_value='receipt') as call:
            client.send('title','<product>\nhttps://example.test/#k=secret')
        payload=call.call_args.args[1]
        self.assertEqual(payload['template'],'html')
        self.assertIn('&lt;product&gt;',payload['content'])
        self.assertIn('<a href="https://example.test/#k=secret">',payload['content'])
    def test_public_access_without_login_and_normal_preview(self):
        app=create_app({'database':str(self.path),'access_token':'test-token','secret_key':'test-key','secure_cookie':False,'public_access':True})
        client=app.test_client();prefix='/ozon-profit/api'
        bootstrap=client.get(prefix+'/bootstrap')
        self.assertEqual(bootstrap.status_code,200)
        self.assertEqual(client.get(prefix+'/orders').status_code,200)
        self.assertEqual(client.get(prefix+'/cost-statistics').status_code,200)
        body={'lines':[{'id':1,'purchase_unit':'12','price_override':None}],'inputs':{'weight_override':'369'},
              'weight_mode':'custom','weight_confirmed':True,'expected_api_weight_g':'339','expected_weight_override':None}
        response=client.post(prefix+'/orders/1/preview',json=body,headers={'X-CSRF-Token':bootstrap.json['csrf']})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['order']['calculation']['profit'],'7.53')

    def test_parcel_comparison_uses_trial_weights_without_saving(self):
        self.post.update(posting_number='MULTI-1',order_number='MULTI')
        self.post['products'][0].update(quantity=3,weight=.2,price={'amount':'60','currency':'CNY'})
        with p.connection(self.path) as db:
            p.capture_postings(db,self.shop,'fbs',[self.post],time.time())
        app=create_app({'database':str(self.path),'secret_key':'test-key','secure_cookie':False,'public_access':True})
        client=app.test_client()
        path='/ozon-profit/api/orders/2'
        token=client.get('/ozon-profit/api/bootstrap').json['csrf']
        order=client.get(path).json['order']
        self.assertIsNone(order['calculation']['profit'])
        comparison=order['parcel_comparison']
        self.assertEqual([plan['cost'] for plan in comparison['plans']],['32.00','24.62','29.52'])
        self.assertEqual(comparison['best'],1)

        result=client.post(path+'/preview',headers={'X-CSRF-Token':token},json={
            'lines':[{'id':order['lines'][0]['id'],'purchase_unit':'','price_override':''}],
            'inputs':{},'parcel_weights':{'two':'500'},
        }).json['order']
        self.assertEqual(result['calculation'],order['calculation'])
        self.assertEqual(result['parcel_comparison']['packages']['two']['tier'],'低客单轻小件')
        self.assertEqual(result['parcel_comparison']['best'],2)
        self.assertEqual(client.get(path).json['order']['parcel_comparison'],comparison)
        with p.connection(self.path) as db:
            single=p.load_order(db,1)
            single['inputs']['weight_override']='312'
            p.save_order(db,single)
        estimate=client.get(path).json['order']['parcel_comparison']['packages']['one']
        self.assertEqual(estimate['default_weight'],'312')
        self.assertEqual(estimate['reference_posting'],'TEST-1')
        body={'lines':[{'id':order['lines'][0]['id'],'purchase_unit':'9.12'}],
              'inputs':{},'dispatch_choice':2,'parcel_weights':{}}
        saved=client.post(path,json=body,headers={'X-CSRF-Token':token}).json['order']
        self.assertEqual(saved['dispatch_plan']['weights'],{'one':'312'})
        self.assertEqual([pack['weight'] for pack in saved['dispatch_plan']['packages']],['312']*3)

    def test_confirmed_dispatch_updates_compass_and_hands_off_to_actual_postings(self):
        self.post.update(posting_number='DISPATCH-1',order_number='DISPATCH')
        self.post['products'][0].update(weight=.1,price={'amount':'25','currency':'CNY'})
        second=dict(self.post['products'][0],offer_id='other',sku=111,weight=.312)
        self.post['products'].append(second)
        with p.connection(self.path) as db:
            p.capture_postings(db,self.shop,'fbs',[self.post],time.time())
        app=create_app({'database':str(self.path),'secret_key':'test-key','secure_cookie':False,'public_access':True})
        client=app.test_client()
        prefix='/ozon-profit/api'
        path=prefix+'/orders/2'
        headers={'X-CSRF-Token':client.get(prefix+'/bootstrap').json['csrf']}
        order=client.get(path).json['order']
        self.assertEqual(order['calculation']['weight_g'],'412.000')
        body={'lines':[{'id':l['id'],'purchase_unit':'9.12','price_override':''} for l in order['lines']],
              'inputs':{},'dispatch_choice':1,'parcel_weights':{'one':'312','two':'312'}}
        pending=client.post(path+'/preview',json=dict(body,parcel_weights={}),headers=headers).json['order']
        self.assertTrue(pending['calculation']['dispatch_pending'])
        self.assertIsNone(pending['calculation']['profit'])
        self.assertIsNone(pending['calculation']['shipping'])
        self.assertEqual(client.post(path,json=dict(body,parcel_weights={}),headers=headers).status_code,400)
        preview=client.post(path+'/preview',json=body,headers=headers).json['order']
        self.assertEqual(preview['calculation']['profit'],'-0.45')
        self.assertIsNone(client.get(path).json['order']['dispatch_plan'])
        saved=client.post(path,json=body,headers=headers).json['order']
        self.assertEqual(saved['calculation']['profit'],'-0.45')
        self.assertEqual(client.get(path).json['order']['calculation']['packages'],2)
        compass=client.get(prefix+'/analytics?q=DISPATCH').json
        self.assertEqual(compass['summary']['sales'],'50.00')
        self.assertEqual(compass['summary']['profit'],'-0.45')
        self.assertEqual(compass['summary']['orders'],1)
        self.assertEqual(compass['summary']['packages'],2)

        parent=dict(self.post,products=self.post['products'][:1])
        child=dict(self.post,posting_number='DISPATCH-2',parent_posting_number='DISPATCH-1',products=[second])
        with p.connection(self.path) as db:
            p.capture_postings(db,self.shop,'fbs',[parent,child],time.time())
            p.capture_postings(db,self.shop,'fbs',[parent,child],time.time())
            ids=[r['id'] for r in db.execute("SELECT id FROM profit_orders WHERE order_number='DISPATCH' ORDER BY id")]
            postings=[p.load_order(db,oid) for oid in ids]
            self.assertEqual([len(o['lines']) for o in postings],[1,1])
            self.assertEqual([o['inputs']['weight_override'] for o in postings],['312','312'])
            self.assertEqual([o['lines'][0]['purchase_unit'] for o in postings],['9.12','9.12'])
            self.assertTrue(all(o['dispatch_plan'] is None for o in postings))
        after=client.get(prefix+'/analytics?q=DISPATCH').json
        self.assertEqual(after['summary']['sales'],'50.00')
        self.assertEqual(after['summary']['orders'],1)
        self.assertEqual(after['summary']['units'],2)
        self.assertEqual(after['summary']['packages'],2)

    def test_split_three_uses_each_parcel_rate_and_keeps_explicit_override(self):
        self.post.update(posting_number='RATE-1',order_number='RATE')
        self.post['products'][0].update(quantity=3,weight=.264,price={'amount':'83','currency':'CNY'})
        with p.connection(self.path) as db:
            p.capture_postings(db,self.shop,'fbs',[self.post],time.time())
        app=create_app({'database':str(self.path),'secret_key':'test-key','secure_cookie':False,'public_access':True})
        client=app.test_client();prefix='/ozon-profit/api';path=prefix+'/orders/2'
        headers={'X-CSRF-Token':client.get(prefix+'/bootstrap').json['csrf']}
        order=client.get(path).json['order']
        self.assertEqual(order['calculation']['rate'],'17')
        body={'lines':[{'id':order['lines'][0]['id'],'purchase_unit':'60.5'}],
              'inputs':{},'dispatch_choice':2,'parcel_weights':{'one':'264'}}
        preview=client.post(path+'/preview',json=body,headers=headers).json['order']
        calc=preview['calculation']
        self.assertEqual(calc['platform_fee'],'34.86')
        self.assertEqual(calc['shipping'],'28.26')
        self.assertEqual(calc['forwarder'],'6.00')
        self.assertEqual(calc['rate'],'14')
        self.assertEqual(calc['profit'],'-1.62')
        self.assertEqual(p.D(calc['profit_exact']),p.D('-.5408')*3)
        self.assertEqual(preview['parcel_comparison']['packages']['one']['rate'],'14')
        manual=client.post(path+'/preview',json=dict(body,inputs={'rate':'17'}),headers=headers).json['order']
        self.assertEqual(manual['calculation']['platform_fee'],'42.33')
        self.assertEqual(manual['calculation']['profit'],'-9.09')
        mixed=client.post(path+'/preview',json=dict(body,dispatch_choice=1,parcel_weights={'one':'264','two':'528'}),headers=headers).json['order']
        self.assertEqual(mixed['calculation']['platform_fee'],'39.84')
        self.assertEqual(mixed['calculation']['rate'],'16.00')
        self.assertEqual(client.post(path,json=body,headers=headers).status_code,200)
        self.assertEqual(client.get(path).json['order']['calculation']['profit'],'-1.62')
        compass=client.get(prefix+'/analytics?q=RATE').json
        self.assertEqual(compass['summary']['profit'],'-1.62')
        self.assertEqual(compass['products'][0]['profit'],'-1.62')

    def test_auth_csrf_preview_save_and_persistence(self):
        app=create_app({'database':str(self.path),'access_token':'test-token','secret_key':'test-key','secure_cookie':False});app.testing=True
        client=app.test_client();prefix='/ozon-profit/api'
        self.assertEqual(client.get(prefix+'/orders').status_code,401)
        self.assertEqual(client.post(prefix+'/login',json={'token':'wrong'}).status_code,401)
        self.assertEqual(client.post(prefix+'/login',json={'token':'test-token'}).status_code,200)
        csrf=client.get(prefix+'/bootstrap').json['csrf']
        body={'lines':[{'id':1,'purchase_unit':'12','price_override':None}],'inputs':{'weight_override':'369'},
              'weight_mode':'custom','weight_confirmed':True,'expected_api_weight_g':'339','expected_weight_override':None}
        self.assertEqual(client.post(prefix+'/orders/1',json=body).status_code,403)
        headers={'X-CSRF-Token':csrf}
        response=client.post(prefix+'/orders/1/preview',json=body,headers=headers)
        self.assertEqual(response.json['order']['calculation']['profit'],'7.53')
        self.assertIsNone(client.get(prefix+'/orders/1').json['order']['calculation']['profit'])
        self.assertEqual(client.post(prefix+'/orders/1',json=body,headers={**headers,'Origin':'https://evil.test'}).status_code,403)
        self.assertEqual(client.post(prefix+'/orders/1',json=body,headers=headers).status_code,200)
        self.assertEqual(client.get(prefix+'/orders/1').json['order']['calculation']['profit'],'7.53')
        self.assertIn('no-store',response.headers['Cache-Control'])
        cookie=client.get_cookie('ozon_profit',path='/ozon-profit')
        self.assertTrue(cookie.http_only)
        self.assertEqual(client.get(prefix+'/orders?q=bag').json['orders'][0]['id'],1)
        self.assertEqual(client.get(prefix+'/orders?shop=missing').json['orders'],[])
        self.assertEqual(client.get(prefix+'/orders?date=invalid').status_code,400)
        self.assertEqual(client.post(prefix+'/orders/1',json={'lines':[{'id':'NaN'}]},headers=headers).status_code,400)

if __name__=='__main__': unittest.main()
