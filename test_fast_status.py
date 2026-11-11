"""Fast status polling uses temporary databases and no external API calls."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import monitor
import profit_core as core
import profit_sync as sync
from profit_web import create_app


class FastStatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=monitor.Store(self.tmp.name)
        self.shop=dict(id='s1',name='One',client_id='1',api_key='fake')
        self.now=time.time()
        self.post=dict(posting_number='TEST-1',order_number='TEST',status='awaiting_packaging',
                       substatus='posting_created',created_at=monitor.stamp(self.now-3600),
                       products=[dict(offer_id='x',sku=123,name='Test',quantity=1,price='30',weight=.2)])
        with self.store.connection() as db:
            core.capture_postings(db,self.shop,'fbs',[self.post],self.now-300)

    def test_only_real_status_changes_increment_version(self):
        with self.store.connection() as db:
            self.assertEqual(sync.status_version(db),'0')
            sync.save_status(db,1,sync.status_values(self.post),self.now)
            self.assertEqual(sync.status_version(db,'s1'),'1')
            sync.save_status(db,1,sync.status_values(self.post),self.now+1)
            self.assertEqual(sync.status_version(db,'s1'),'1')
            sync.save_status(db,1,sync.status_values(dict(self.post,substatus='posting_packing')),self.now+2)
            self.assertEqual(sync.status_version(db,'s1'),'2')
            sync.save_status(db,1,sync.status_values(dict(self.post,status='awaiting_deliver')),self.now+3)
            self.assertEqual(sync.status_version(db,'s1'),'3')
            self.assertEqual(sync.status_version(db),'3')

    def test_shop_versions_are_isolated_and_old_refresh_cannot_increment(self):
        other=dict(self.shop,id='s2',name='Two')
        with self.store.connection() as db:
            core.capture_postings(db,other,'fbs',[self.post],self.now)
            sync.save_status(db,1,sync.status_values(self.post),self.now+10)
            sync.save_status(db,2,sync.status_values(self.post),self.now+10)
            self.assertEqual(sync.status_version(db),'2')
            self.assertEqual(sync.status_version(db,'s1'),'1')
            self.assertEqual(sync.status_version(db,'s2'),'1')
            sync.save_status(db,1,sync.status_values(dict(self.post,status='delivered')),self.now)
            self.assertEqual(sync.status_version(db),'2')
            self.assertEqual(core.load_order(db,1)['status'],'awaiting_packaging')

    def test_old_history_and_new_order_list_cannot_overwrite_confirmed_status(self):
        with self.store.connection() as db:
            sync.save_status(db,1,sync.status_values(dict(self.post,status='awaiting_deliver')),
                             self.now)
            # List replies can arrive later, even with a later ingestion timestamp.
            core.capture_postings(db,self.shop,'fbs',[self.post],self.now+10)
            self.assertEqual(core.load_order(db,1)['status'],'awaiting_deliver')
            core.capture_postings(db,self.shop,'fbs',[self.post],self.now-100)
            self.assertEqual(core.load_order(db,1)['seen'],self.now+10)
            self.assertEqual(sync.status_version(db,'s1'),'1')

    def test_fast_job_uses_change_feed_without_history_or_detail_work(self):
        with self.store.connection() as db:
            sync.save_status(db,1,sync.status_values(self.post),self.now-300)
            sync.save_status_cursor(db,sync.status_cursor_key(self.shop),self.now-300)
        calls=[]
        class API:
            def postings(self,source,start,end,changed_since=None):
                if changed_since is None: raise AssertionError('Unexpected full history fetch')
                calls.append((source,changed_since));return []
            def posting(self,*args):raise AssertionError('Unexpected per-order request')
        monitor.poll(self.store,API(),self.shop,'profit_status_fbs',{'status_poll_seconds':30})
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0],('fbs',self.now-300-sync.STATUS_OVERLAP))
        with self.store.connection() as db:
            self.assertEqual(sync.status_version(db,'s1'),'1')
            self.assertIsNotNone(db.execute("SELECT last_success FROM health WHERE source='profit_status_fbs'").fetchone())

    def test_fast_interval_has_separate_sensible_limits(self):
        filename=Path(self.tmp.name)/'config.json'
        config=dict(shops=[self.shop],pushplus={'token':'fake'},data_dir=self.tmp.name)
        filename.write_text(json.dumps(config),encoding='utf-8')
        cfg=monitor.load_config(filename)
        self.assertEqual(monitor.job_interval('profit_status_fbs',cfg),30)
        self.assertEqual(monitor.job_interval('profit_fbs',cfg),300)
        config['status_poll_seconds']=14
        filename.write_text(json.dumps(config),encoding='utf-8')
        with self.assertRaises(monitor.MonitorError):monitor.load_config(filename)

    def test_version_endpoint_is_authorized_read_only_and_matches_orders_snapshot(self):
        app=create_app(dict(database=str(self.store.path),secret_key='test',access_token='test',
                            public_access=False,secure_cookie=False,shops=[self.shop]))
        client=app.test_client();prefix='/ozon-profit/api'
        self.assertEqual(client.get(prefix+'/order-status-version').status_code,401)
        client.post(prefix+'/login',json={'token':'test'})
        with patch('profit_web.core.load_order',side_effect=AssertionError('Version must not recalculate orders')):
            result=client.get(prefix+'/order-status-version?shop=s1')
        self.assertEqual(result.json,{'version':'0'})
        self.assertEqual(result.headers['Cache-Control'],'no-store')
        with self.store.connection() as db:
            sync.save_status(db,1,sync.status_values(self.post),self.now)
        version=client.get(prefix+'/order-status-version?shop=s1').json['version']
        self.assertEqual(client.get(prefix+'/orders?shop=s1').json['status_version'],version)
        self.assertEqual(client.get(prefix+'/order-status-version?shop=unknown').json['version'],'0')


if __name__=='__main__':unittest.main()
