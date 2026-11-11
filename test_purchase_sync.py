import copy
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
import profit_core as core
import purchase_sync as sync

SHOP={'id':'shop01','name':'Luo001'}

class PurchaseSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'test.sqlite3'
        with core.connection(self.path) as db:
            core.ensure_schema(db);sync.schema(db)
            for number in ['12345678-0001-1','12345678-0002-1']:
                post={'posting_number':number,'order_id':number,'order_number':number,'created_at':'2026-11-01T01:00:00Z','status':'awaiting_packaging',
                    'products':[dict(offer_id='test-offer',sku=6169746567,name='Test product',quantity=2,weight=.1,price={'amount':'25','currency':'CNY'})]}
                core.capture_postings(db,SHOP,'fbs',[post],time.time())
    def record(self,**kwargs):
        return dict(shop_name='Luo001',posting='12345678-0001-1',offer_id='test-offer',sku='6169746567',quantity='2',cost_total='31.07',note='854g',**kwargs)
    def preview(self,record=None,mode='incremental'):
        with core.connection(self.path) as db:return sync.preview(db,dict(records=[record or self.record()],mode=mode),'test',[SHOP])
    def apply(self,p,fields=None):
        ids=[c['id'] for e in p['entries'] for c in e['changes'] if fields is None or c['field'] in fields]
        with core.connection(self.path) as db:return sync.apply(db,p['id'],ids,'test')
    def order(self,oid=1):
        with core.connection(self.path) as db:return core.load_order(db,oid)

    def test_exact_cost_and_package_weight_only_selected_order_and_undo(self):
        original=self.order(2);p=self.preview();self.assertEqual(len(p['entries'][0]['changes']),2)
        self.assertIsNone(self.order()['lines'][0]['purchase_unit'])
        r=self.apply(p);o=self.order();self.assertEqual(core.D(o['lines'][0]['purchase_unit']),core.D('15.535'))
        self.assertEqual(core.D(o['inputs']['weight_override']),core.D('854'))
        self.assertEqual(self.order(2),original)
        with core.connection(self.path) as db:sync.undo(db,r['id'],'test')
        self.assertIsNone(self.order()['lines'][0]['purchase_unit']);self.assertIsNone(self.order()['inputs']['weight_override'])

    def test_zero_cost_is_pending_and_later_backfilled_order_is_retried(self):
        x=self.record();x['cost_total']='0.00';p=self.preview(x)
        self.assertEqual([c['field'] for c in p['entries'][0]['changes']],['weight'])
        with core.connection(self.path) as db:self.assertEqual(db.execute("SELECT status FROM purchase_sync_fields WHERE field='cost'").fetchone()[0],'waiting')
        p=self.preview(self.record());self.assertIn('cost',[c['field'] for c in p['entries'][0]['changes']])
        self.apply(p)
        p=self.preview();self.assertFalse(p['entries'][0]['changes'])

    def test_incremental_preserves_local_edit_full_shows_conflict_then_upstream_change(self):
        self.apply(self.preview())
        with core.connection(self.path) as db:db.execute("UPDATE profit_lines SET purchase_unit='20' WHERE order_id=1")
        p=self.preview();self.assertFalse(p['entries'][0]['changes'])
        p=self.preview(mode='full');changes=p['entries'][0]['changes']
        self.assertEqual(len(changes),1);self.assertTrue(changes[0]['conflict']);self.assertFalse(changes[0]['checked'])
        x=self.record();x['cost_total']='40';p=self.preview(x)
        self.assertFalse(p['entries'][0]['changes'])

    def test_old_order_source_change_replaces_last_synced_target(self):
        self.apply(self.preview());x=self.record();x['cost_total']='36.60'
        p=self.preview(x);c=p['entries'][0]['changes'][0]
        self.assertTrue(c['checked']);self.assertFalse(c['conflict'])
        self.apply(p);self.assertEqual(core.D(self.order()['lines'][0]['purchase_unit']),core.D('18.3'))

    def test_cannot_commit_after_quantity_or_local_fields_change(self):
        p=self.preview()
        with core.connection(self.path) as db:db.execute('UPDATE profit_lines SET quantity=1 WHERE order_id=1')
        with self.assertRaisesRegex(ValueError,'数据已变化'):self.apply(p)
        self.assertIsNone(self.order()['lines'][0]['purchase_unit'])

    def test_invalid_note_zero_cost_preserves_data_and_field_selection(self):
        x=self.record();x['note']='11-11:2-2';x['cost_total']='0';p=self.preview(x)
        self.assertFalse(p['entries'][0]['changes']);self.assertEqual(len(p['entries'][0]['notes']),2)
        p=self.preview();self.apply(p,['cost']);self.assertIsNone(self.order()['inputs']['weight_override'])
        with core.connection(self.path) as db:
            before=db.execute('SELECT COUNT(*) FROM profit_weight_audit').fetchone()[0]
            self.assertEqual(before,0)

    def test_bad_match_duplicates_and_unconfirmed_input_do_not_mutate_orders(self):
        before=self.order();x=self.record();x['quantity']='3';p=self.preview(x);self.assertTrue(p['entries'][0]['error'])
        x=self.record();x['sku']='9999999999';p=self.preview(x);self.assertTrue(p['entries'][0]['error'])
        with self.assertRaises(ValueError):sync.normalize(dict(records=[self.record()]*2))
        self.assertEqual(self.order(),before)

    def test_batch_replay_and_undo_conflict_and_owner_isolation(self):
        p=self.preview();result=self.apply(p)
        with self.assertRaises(ValueError):self.apply(p)
        with core.connection(self.path) as db:
            with self.assertRaises(ValueError):sync.undo(db,result['id'],'different-browser')
        with core.connection(self.path) as db:db.execute("UPDATE profit_inputs SET forwarder='3' WHERE order_id=1")
        with core.connection(self.path) as db:
            with self.assertRaises(ValueError):sync.undo(db,result['id'],'test')
        self.assertEqual(self.order()['inputs']['forwarder'],'3')

if __name__=='__main__':unittest.main()
