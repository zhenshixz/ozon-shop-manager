import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from overweight import Service, TZ, grams, digest_lines
from overweight_worker import tick

class FakeAPI:
    def __init__(self,items):self.items=items;self.writes=[];self.confirm=True;self.omit=False
    def products(self,visibility):return {str(x['id']):dict(product_id=x['id']) for x in self.items}
    def post(self,path,data):
        ids=data.get('product_id') or data.get('filter',{}).get('product_id') or []
        found=[x.copy() for x in self.items if str(x['id']) in {str(i) for i in ids}]
        if path=='/v4/product/info/attributes':return {'result':found[:-1] if self.omit else found}
        if path=='/v3/product/info/list':return {'items':found}
        if path=='/v1/product/archive':
            self.writes.append(data)
            if self.confirm:
                for x in self.items:
                    if str(x['id']) in {str(i) for i in ids}:x['is_archived']=True
            return {'result':True}
        raise AssertionError(path)

def item(pid,weight,unit='g',archived=False,sku=None):
    return dict(id=pid,sku=pid+100 if sku is None else sku,offer_id='offer-'+str(pid),name='测试商品'+str(pid),
        weight=weight,weight_unit=unit,is_archived=archived,created_at='2026-11-10T15:00:00Z',
        primary_image=['https://example.com/test.jpg'])

class WeightTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.config=root/'config.json'
        self.config.write_text(json.dumps(dict(product_digest_hour=22,shops=[dict(id='a',name='店A'),dict(id='b',name='店B')])))
        self.apis={'a':FakeAPI([item(1,2000),item(2,2.9,'kg'),item(3,23657,archived=True,sku=0)]),'b':FakeAPI([item(2,4000)])}
        self.s=Service(root/'weight.sqlite3',self.config,lambda shop:self.apis[shop['id']])
        self.now=dt.datetime(2026,11,11,15,tzinfo=TZ).timestamp()
    def scan(self,ids=None,trigger='manual',now=None):
        rid=self.s.enqueue(ids or ['a','b'],trigger=trigger,now=now)
        self.s.execute(rid);return rid
    def selection(self):return [dict(shop=x['shop'],pid=x['pid'],version=x['version']) for x in self.s.view({'state':'pending'})['items']]

    def test_manual_immediate_auto_next_beijing_day_and_multiple_saves(self):
        self.s.save_rule(3000,self.now);self.s.save_rule(2500,self.now+1)
        self.assertEqual(self.s.rule(self.now)['manual'],2500)
        self.assertEqual(self.s.rule(self.now)['automatic'],2000)
        tomorrow=dt.datetime(2026,11,12,tzinfo=TZ).timestamp()
        self.assertEqual(self.s.rule(tomorrow)['automatic'],2500)
        self.s.save_rule(4500,tomorrow+60)
        self.assertEqual(self.s.rule(tomorrow+60)['automatic'],2500)
        self.assertEqual(self.s.rule(tomorrow+60)['manual'],4500)

    def test_scan_threshold_units_shop_identity_archived_zero_sku_and_dedup(self):
        self.scan();v=self.s.view({'state':'pending'})
        self.assertEqual(v['total'],2)
        self.assertEqual(sorted(x['weight_g'] for x in v['items']),[2900,4000])
        self.assertEqual(self.s.view({'state':'archived'})['items'][0]['sku'],0)
        self.scan();self.assertEqual(self.s.view()['total'],3)
        self.assertEqual(sum(len(a.writes) for a in self.apis.values()),0)
        self.assertEqual(self.s.view({'q':'测试'})['total'],3)

    def test_retain_version_and_reopen_after_weight_changes(self):
        self.scan(['a']);self.s.retain(self.selection());self.scan(['a'])
        self.assertEqual(self.s.view({'state':'pending'})['total'],0)
        self.apis['a'].items[1]['weight']=3.1;self.scan(['a'])
        self.assertEqual(self.s.view({'state':'pending'})['total'],1)

    def test_weight_normalization_and_unknown_not_silently_clear(self):
        self.scan(['a']);self.apis['a'].items[1]['weight']=None
        rid=self.scan(['a']);self.assertEqual(self.s.job(rid)['state'],'attention')
        self.assertEqual(self.s.view({'state':'pending'})['total'],1)
        self.apis['a'].items[1]['weight']=1.9;self.scan(['a'])
        self.assertEqual(self.s.view({'state':'normal'})['total'],1)

    def test_no_write_on_stale_weight_and_archive_requires_verified_state(self):
        self.scan(['a']);selection=self.selection();self.apis['a'].items[1]['weight']=3.0
        rid=self.s.enqueue(['a'],kind='archive',selection=selection);self.s.execute(rid)
        self.assertFalse(self.apis['a'].writes)
        self.scan(['a']);selection=self.selection();self.apis['a'].confirm=False
        rid=self.s.enqueue(['a'],kind='archive',selection=selection);self.s.execute(rid)
        self.assertEqual(self.s.job(rid)['state'],'attention')
        self.assertEqual(self.s.view({'state':'pending'})['total'],1)
        self.apis['a'].confirm=True
        rid=self.s.enqueue(['a'],kind='archive',selection=selection);self.s.execute(rid)
        self.assertEqual(self.s.job(rid)['state'],'done')
        self.assertEqual(self.s.view({'state':'pending'})['total'],0)

    def test_archive_batches_use_separate_credentials_and_no_unselected_writes(self):
        self.scan();selection=self.selection()
        rid=self.s.enqueue(['a','b'],kind='archive',selection=selection);self.s.execute(rid)
        for api in self.apis.values():self.assertEqual(api.writes,[{'product_id':[2]}])
        self.assertEqual(self.s.job(rid)['state'],'done')

    def test_daily_job_dedup_conflict_and_digest(self):
        self.s.save_rule(5000,self.now)
        rid=self.s.enqueue(['a','b'],trigger='daily',now=self.now)
        self.assertEqual(self.s.job(rid)['threshold'],2000)
        with self.assertRaises(ValueError):self.s.enqueue(['a'])
        self.s.execute(rid)
        self.assertIsNone(self.s.enqueue(['a','b'],trigger='daily',now=self.now))
        text='\n'.join(digest_lines(self.s.path,self.now))
        self.assertIn('2000g',text);self.assertIn('新增 2 款',text)
        self.assertIn('/ozonzusou/overweight/',text)
        later=self.s.enqueue(['a'],now=self.now)
        self.assertEqual(self.s.job(later)['threshold'],5000)

    def test_incomplete_response_does_not_clear_prior_and_rejects_stale_selection(self):
        self.scan(['a']);selection=self.selection();self.apis['a'].omit=True
        rid=self.scan(['a']);self.assertEqual(self.s.job(rid)['state'],'attention')
        self.assertEqual(self.s.view({'state':'pending'})['total'],1)
        self.s.retain(selection)
        with self.assertRaises(ValueError):self.s.enqueue(['a'],kind='archive',selection=selection)

    def test_schedule_before_digest_no_early_run(self):
        earlier=dt.datetime(2026,11,11,21,40,tzinfo=TZ).timestamp()
        self.assertIsNone(tick(self.s,earlier))
        rid=tick(self.s,earlier+300)
        self.assertEqual(self.s.job(rid)['trigger'],'daily')
        self.assertIsNone(tick(self.s,earlier+600))

    def test_archived_product_absent_from_all_list_is_rechecked_by_id(self):
        self.scan(['a'])
        self.apis['a'].items[1]['is_archived']=True
        original=self.apis['a'].products
        self.apis['a'].products=lambda visibility:{k:v for k,v in original(visibility).items() if k!='2'}
        rid=self.scan(['a'])
        self.assertEqual(self.s.job(rid)['state'],'done')
        self.assertEqual(self.s.view({'state':'pending'})['total'],0)
        self.assertEqual(self.s.view({'state':'archived'})['total'],2)

if __name__=='__main__':unittest.main()
