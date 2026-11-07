import datetime as dt
import tempfile
import unittest
from unittest.mock import patch
import monitor
import order_sync
import profit_core as core
from profit_web import create_app


class ManualSyncTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=monitor.Store(self.temp.name)
        self.shops=[dict(id='s1',name='One'),dict(id='s2',name='Two')]
        self.worker=order_sync.OrderSync(str(self.store.path),'unused.json')

    def test_explicit_selection_and_csrf(self):
        app=create_app(dict(database=str(self.store.path),secret_key='test',public_access=True,
                            secure_cookie=False,shops=self.shops))
        client=app.test_client();base='/ozon-profit/api'
        token=client.get(base+'/bootstrap').json['csrf'];headers={'X-CSRF-Token':token}
        self.assertEqual(client.post(base+'/order-sync',json={'shops':['s1']}).status_code,403)
        for ids in ([],['unknown'],'s1'):
            self.assertEqual(client.post(base+'/order-sync',json={'shops':ids},headers=headers).status_code,400)
        with patch('order_sync.threading.Thread.start'):
            task=client.post(base+'/order-sync',json={'shops':['s2']},headers=headers).json['task']
            self.assertEqual(task['shops'],[self.shops[1]])
            again=client.post(base+'/order-sync',json={'shops':['s1']},headers=headers).json['task']
            self.assertEqual(again['id'],task['id'])

    def test_forced_api_fetch_preserves_inputs_and_partial_errors(self):
        now=dt.datetime.now(core.CHINA);stamp=now.isoformat()
        posting=dict(posting_number='A-1',order_number='A',created_at=stamp,status='awaiting_packaging',
                     products=[dict(offer_id='x',sku=123,name='X',quantity=1,price='30',weight=.3)])
        with self.store.connection() as db:
            core.capture_postings(db,self.shops[1],'fbs',[posting],now.timestamp())
            order=core.load_order(db,1);order['lines'][0]['purchase_unit']='12'
            order['inputs']['weight_override']='312';core.save_order(db,order)
            for source in ('fbs','fbo'):
                db.execute('INSERT OR REPLACE INTO profit_sync_state VALUES (?,?,?,?)',('s2',source,0,now.timestamp()))
        calls=[]
        class API:
            def __init__(self,shop,*args):calls.append(shop['id'])
            def postings(self,source,start,end):
                calls.append(source)
                if source=='fbo':raise monitor.ApiError('Ozon','/list',429)
                return [dict(posting,status='delivering'),dict(posting,posting_number='B-1',order_number='B')]
        with patch('order_sync.threading.Thread.start'):
            task=self.worker.start([self.shops[1]])
        cfg=dict(data_dir=self.temp.name,shops=self.shops,http_timeout_seconds=5,max_pages=10)
        with patch('monitor.load_config',return_value=cfg),patch('monitor.Ozon',API):
            self.worker.run(task['id'],{k:v for k,v in task.items() if k not in ('id','state')})
        result=self.worker.status()
        self.assertEqual(result['state'],'partial')
        self.assertEqual(result['added'],1)
        self.assertEqual(calls,['s2','fbs','fbo'])
        with self.store.connection() as db:
            order=core.load_order(db,1)
            self.assertEqual(order['status'],'delivering')
            self.assertEqual(order['lines'][0]['purchase_unit'],'12')
            self.assertEqual(order['inputs']['weight_override'],'312')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)

if __name__=='__main__':unittest.main()
