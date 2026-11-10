import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import promotions as p


class PromotionsTests(unittest.TestCase):
    def test_public_access_needs_no_password_but_keeps_request_protection(self):
        from flask import Flask
        with tempfile.TemporaryDirectory() as folder,patch.object(p,'ROOT',Path(folder)),patch.object(p,'launch') as launch:
            root=Path(folder)
            (root/'config.json').write_text(json.dumps(dict(shops=[dict(id='custom',name='测试店',api_key='not-for-browser')])) )
            (root/'promo-access.json').write_text(json.dumps(dict(public_access=True,password_hash='retained')))
            app=Flask(__name__);app.secret_key='test';p.register(app)
            client=app.test_client();response=client.get('/ozon-profit/promotions/api/state')
            self.assertEqual(response.status_code,200);self.assertTrue(response.json['public_access'])
            self.assertNotIn('not-for-browser',response.get_data(as_text=True))
            url='/ozon-profit/promotions/api/plan';data=dict(enabled=False,time='09:00',shops=[])
            self.assertEqual(client.post(url,json=data).status_code,403)
            headers={'X-CSRF-Token':response.json['csrf']}
            self.assertEqual(client.post(url,json=data,headers={**headers,'Origin':'https://elsewhere.invalid'}).status_code,403)
            self.assertEqual(client.post(url,json=data,headers=headers).status_code,200)
            launch.assert_not_called()
            with p.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM runs').fetchone()[0],0)

    def test_discovers_unseen_actions_dates_pagination_and_keeps_other_products(self):
        future=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(days=7)).isoformat()
        past=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=7)).isoformat()
        actions=[dict(id=i,title=f'活动{i}',auto_add_dates=[future,past]) for i in (900001,900002,900003,900004)]
        data={i:[dict(product_id=n,name=f'商品{n}',offer_id=f'货号{n}',sku=n+100) for n in range(101 if i==900004 else 1)] for i in range(900001,900005)}
        calls=[]; audit=[]; results=[]
        def fake(shop,path,payload=None):
            calls.append((path,payload))
            if path=='/v1/actions': return dict(result=actions)
            self.assertEqual(payload['auto_add_date'],future)
            aid=payload['action_id']
            if path==p.READ_LIST:
                offset=payload['offset']; return dict(products=copy.deepcopy(data[aid][offset:offset+100]),total=len(data[aid]))
            self.assertEqual(path,p.DELETE)
            ids=payload['product_ids']; data[aid]=[x for x in data[aid] if x['product_id'] not in ids]
            return dict(product_ids=ids)
        r=p.clean(dict(id='arbitrary-shop',name='朋友的店'),audit.append,lambda x:results.append(copy.deepcopy(x)),fake)
        self.assertEqual(r['status'],'ok'); self.assertEqual(r['activities'],4)
        self.assertEqual(r['removed'],104); self.assertEqual(len(r['batches']),4)
        self.assertTrue(all(b['remaining']==0 for b in r['batches']))
        self.assertTrue(any(path==p.READ_LIST and d['offset']==100 for path,d in calls))
        self.assertEqual({path for path,_ in calls},{'/v1/actions',p.READ_LIST,p.DELETE})
        self.assertEqual(len([a for a in audit if a['stage']=='snapshot']),4)
        products=[product for batch in r['batches'] for product in batch['products']]
        self.assertEqual(len(products),104)
        self.assertTrue(all(x['status']=='removed' and x['name'] and x['offer_id'] for x in products))

    def test_partial_response_is_failure_not_whole_shop_success(self):
        date=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(days=1)).isoformat()
        def fake(shop,path,payload=None):
            if path=='/v1/actions':return dict(result=[dict(id=123,title='测试',auto_add_dates=[date])])
            if path==p.READ_LIST:return dict(products=[dict(product_id=1),dict(product_id=2)],total=2)
            return dict(product_ids=[1])
        r=p.clean(dict(id='a',name='a'),lambda v:None,lambda v:None,fake)
        self.assertEqual(r['status'],'error'); self.assertEqual(r['removed'],1)
        self.assertEqual([x['status'] for x in r['batches'][0]['products']],['removed','unconfirmed'])

    def test_legacy_product_details_recovered_without_api_or_database_write(self):
        import hashlib
        with tempfile.TemporaryDirectory() as folder,patch.object(p,'ROOT',Path(folder)),patch.object(p,'api',side_effect=AssertionError('No network')):
            rid='a'*24;sid='custom'
            directory=Path(folder)/'data'/'promotions'/rid;directory.mkdir(parents=True)
            events=[dict(stage='snapshot',action_id=7,date='future',products=[dict(product_id=1,name='商品一',offer_id='one'),dict(product_id=2,name='商品二')]),
                    dict(stage='delete-prepared',payload=dict(action_id=7,auto_add_date='future',product_ids=[1])),
                    dict(stage='delete-response',response=dict(product_ids=[1])),
                    dict(stage='delete-prepared',payload=dict(action_id=7,auto_add_date='future',product_ids=[2]))]
            path=directory/(hashlib.sha256(sid.encode()).hexdigest()+'.jsonl')
            path.write_text('\n'.join(json.dumps(x) for x in events),encoding='utf-8')
            original=path.read_bytes()
            run=dict(id=rid,result=[dict(shop=sid,batches=[dict(action_id=7,date='future')])])
            result=p.product_details(run)['result'][0]['batches'][0]['products']
            self.assertEqual([x['status'] for x in result],['removed','unconfirmed'])
            self.assertEqual(result[0]['offer_id'],'one');self.assertEqual(path.read_bytes(),original)

    def test_queue_change_during_pagination_fails_before_delete(self):
        def fake(shop,path,payload):
            return dict(products=[dict(product_id=i) for i in range(100)],total=101 if payload['offset']==0 else 102)
        with self.assertRaises(ValueError):p.queue({},1,'date',fake)

    def test_disabled_timer_and_protected_routes(self):
        from flask import Flask
        from werkzeug.security import generate_password_hash
        with tempfile.TemporaryDirectory() as folder,patch.object(p,'ROOT',Path(folder)):
            root=Path(folder)
            (root/'config.json').write_text(json.dumps(dict(shops=[dict(id='custom',name='测试店',client_id='fake',api_key='fake')])) )
            (root/'promo-access.json').write_text(json.dumps(dict(password_hash=generate_password_hash('test-only',method='pbkdf2:sha256:1000'))))
            app=Flask(__name__);app.secret_key='test';p.register(app)
            @app.errorhandler(ValueError)
            def invalid(e):return dict(error=str(e)),400
            with patch.object(p,'api',side_effect=AssertionError('No real API')),patch.object(p,'launch') as launch:
                p.tick();launch.assert_not_called()
                with p.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM runs').fetchone()[0],0)
                client=app.test_client()
                self.assertEqual(client.get('/ozon-profit/promotions/api/state').status_code,401)
                self.assertEqual(client.post('/ozon-profit/promotions/api/login',json={'password':'test-only'}).status_code,200)
                state=client.get('/ozon-profit/promotions/api/state').json
                self.assertFalse(state['plan']['enabled']);self.assertNotIn('api_key',json.dumps(state))
                url='/ozon-profit/promotions/api/run'
                self.assertEqual(client.post(url,json={'shops':['custom'],'confirm':True}).status_code,403)
                headers={'X-CSRF-Token':state['csrf']}
                self.assertEqual(client.post(url,json={'shops':['unknown'],'confirm':True},headers=headers).status_code,400)
                response=client.post(url,json={'shops':['custom'],'confirm':True},headers=headers)
                self.assertEqual(response.status_code,202)
                rid=response.json['id']
                self.assertEqual(launch.call_count,1)
                self.assertEqual(client.post(url,json={'shops':['custom'],'confirm':True},headers=headers).status_code,400)
                product=dict(product_id=1,name='商品明细',offer_id='offer',status='removed')
                with p.db() as c:
                    c.execute('UPDATE runs SET result=? WHERE id=?',(json.dumps([dict(shop='custom',batches=[dict(action_id=1,date='future',products=[product])])]),rid))
                self.assertNotIn('products',client.get('/ozon-profit/promotions/api/state').json['runs'][0]['result'][0]['batches'][0])
                detail='/ozon-profit/promotions/api/runs/'+rid
                self.assertEqual(app.test_client().get(detail).status_code,401)
                self.assertEqual(client.get(detail).json['run']['result'][0]['batches'][0]['products'],[product])

    def test_daily_dispatch_runs_once_and_only_selected_shops(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(p,'ROOT',Path(folder)),patch.object(p,'worker') as worker:
            with p.db() as c:
                value=dict(enabled=True,time='00:00',shops=['selected-only'],last_day='')
                c.execute('INSERT INTO settings VALUES(?,?)',('self',json.dumps(value)))
            p.tick()
            self.assertEqual(worker.call_count,1)
            with p.db() as c:
                row=c.execute('SELECT * FROM runs').fetchone()
                self.assertEqual(json.loads(row['shops']),['selected-only'])
                c.execute("UPDATE runs SET status='ok'")
            p.tick()
            self.assertEqual(worker.call_count,1)

    def test_two_slots_each_run_once_and_legacy_morning_does_not_skip_evening(self):
        class Clock(dt.datetime):
            current = dt.datetime(2026,11,11,12,11,tzinfo=p.TZ)
            @classmethod
            def now(cls, tz=None): return cls.current
        with tempfile.TemporaryDirectory() as folder,patch.object(p,'ROOT',Path(folder)),patch.object(p,'worker') as worker,patch.object(p.dt,'datetime',Clock):
            def completed(rid):
                with p.db() as c:c.execute("UPDATE runs SET status='ok' WHERE id=?",(rid,))
            worker.side_effect=completed
            with p.db() as c:
                value=dict(enabled=True,time='12:12',extra_times=['20:00'],shops=['selected-only'],last_day='2026-11-10')
                c.execute('INSERT INTO settings VALUES(?,?)',('self',json.dumps(value)))
            p.tick();self.assertEqual(worker.call_count,0)
            Clock.current=Clock.current.replace(hour=12,minute=12)
            p.tick();p.tick();self.assertEqual(worker.call_count,1)
            Clock.current=Clock.current.replace(hour=19,minute=59)
            p.tick();self.assertEqual(worker.call_count,1)
            Clock.current=Clock.current.replace(hour=20,minute=0)
            p.tick();p.tick();self.assertEqual(worker.call_count,2)
            with p.db() as c:
                self.assertEqual(p.plan(c)['last_runs'],{'12:12':'2026-11-11','20:00':'2026-11-11'})
                self.assertTrue(all(json.loads(r[0])==['selected-only'] for r in c.execute('SELECT shops FROM runs')))

    def test_enabling_after_both_times_skips_today_and_saving_preserves_evening(self):
        from flask import Flask
        class Clock(dt.datetime):
            @classmethod
            def now(cls, tz=None): return cls(2026,11,10,21,0,tzinfo=p.TZ)
        with tempfile.TemporaryDirectory() as folder,patch.object(p,'ROOT',Path(folder)),patch.object(p,'worker') as worker,patch.object(p.dt,'datetime',Clock):
            root=Path(folder)
            (root/'config.json').write_text(json.dumps(dict(shops=[dict(id='only',name='Test')])) )
            (root/'promo-access.json').write_text(json.dumps(dict(public_access=True)))
            with p.db() as c:c.execute('INSERT INTO settings VALUES(?,?)',('self',json.dumps(dict(enabled=False,time='12:12',extra_times=['20:00'],shops=['only'],last_day=''))))
            app=Flask(__name__);app.secret_key='test';p.register(app)
            client=app.test_client();csrf=client.get('/ozon-profit/promotions/api/state').json['csrf']
            response=client.post('/ozon-profit/promotions/api/plan',json=dict(enabled=True,time='12:12',shops=['only']),headers={'X-CSRF-Token':csrf})
            self.assertEqual(response.status_code,200)
            p.tick();worker.assert_not_called()
            with p.db() as c:
                value=p.plan(c)
                self.assertEqual(value['times'],['12:12','20:00'])
                self.assertEqual(value['last_runs'],{'12:12':'2026-11-10','20:00':'2026-11-10'})

    def test_missed_slots_coalesce_to_one_sweep(self):
        class Clock(dt.datetime):
            @classmethod
            def now(cls, tz=None):return cls(2026,11,11,21,0,tzinfo=p.TZ)
        with tempfile.TemporaryDirectory() as folder,patch.object(p,'ROOT',Path(folder)),patch.object(p,'worker') as worker,patch.object(p.dt,'datetime',Clock):
            with p.db() as c:c.execute('INSERT INTO settings VALUES(?,?)',('self',json.dumps(dict(enabled=True,time='12:12',extra_times=['20:00'],shops=['only'],last_day=''))))
            p.tick()
            self.assertEqual(worker.call_count,1)
            with p.db() as c:
                self.assertEqual(p.plan(c)['last_runs'],{'12:12':'2026-11-11','20:00':'2026-11-11'})


if __name__=='__main__':unittest.main()
