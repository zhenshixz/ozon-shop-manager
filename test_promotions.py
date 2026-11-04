import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import promotions as p


class PromotionsTests(unittest.TestCase):
    def test_discovers_unseen_actions_dates_pagination_and_keeps_other_products(self):
        future=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(days=7)).isoformat()
        past=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=7)).isoformat()
        actions=[dict(id=i,title=f'活动{i}',auto_add_dates=[future,past]) for i in (900001,900002,900003,900004)]
        data={i:[dict(product_id=n) for n in range(101 if i==900004 else 1)] for i in range(900001,900005)}
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

    def test_partial_response_is_failure_not_whole_shop_success(self):
        date=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(days=1)).isoformat()
        def fake(shop,path,payload=None):
            if path=='/v1/actions':return dict(result=[dict(id=123,title='测试',auto_add_dates=[date])])
            if path==p.READ_LIST:return dict(products=[dict(product_id=1)],total=1)
            return dict(product_ids=[])
        r=p.clean(dict(id='a',name='a'),lambda v:None,lambda v:None,fake)
        self.assertEqual(r['status'],'error'); self.assertEqual(r['removed'],0)

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
                self.assertEqual(client.post(url,json={'shops':['custom'],'confirm':True},headers=headers).status_code,202)
                self.assertEqual(launch.call_count,1)
                self.assertEqual(client.post(url,json={'shops':['custom'],'confirm':True},headers=headers).status_code,400)

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


if __name__=='__main__':unittest.main()
