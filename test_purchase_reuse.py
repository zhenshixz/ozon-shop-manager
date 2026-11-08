"""Purchase-cost inheritance in isolated SQLite databases, with no API calls."""
import tempfile,time,unittest
from pathlib import Path
import profit_core as p

class PurchaseReuseTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'test.sqlite3'
        with p.connection(self.path) as db:p.ensure_schema(db)
        self.now=time.time()
    def add(self,posting,shop='s1',offer='bag',sku='123',quantity=1,status='awaiting_deliver'):
        with p.connection(self.path) as db:
            p.capture_postings(db,dict(id=shop,name=shop),'fbs',[dict(posting_number=posting,
                order_number=posting,created_at='2026-11-01T00:00:00Z',status=status,
                products=[dict(offer_id=offer,sku=sku,name='bag',quantity=quantity,weight=.2,
                price=dict(amount='39',currency='CNY'))])],self.now)
            return db.execute('SELECT id FROM profit_orders WHERE shop=? AND posting=?',(shop,posting)).fetchone()[0]
    def load(self,order_id):
        with p.connection(self.path) as db:return p.load_order(db,order_id)
    def save_cost(self,order_id,value,when):
        with p.connection(self.path) as db:
            order=p.load_order(db,order_id);order['lines'][0]['purchase_unit']=value
            p.save_order(db,order,now=when)
    def test_existing_pending_and_new_orders_fill_unit_price(self):
        first=self.add('first');pending=self.add('pending',quantity=3)
        self.save_cost(first,'12.5',100)
        order=self.load(pending)
        self.assertEqual(order['lines'][0]['purchase_unit'],'12.5')
        self.assertEqual(order['lines'][0]['purchase_origin'],'inherited')
        self.assertEqual(order['lines'][0]['purchase_source_posting'],'first')
        self.assertEqual(order['calculation']['purchase_cost'],'37.50')
        new=self.add('new')
        self.assertEqual(self.load(new)['lines'][0]['purchase_unit'],'12.5')
    def test_latest_manual_edit_beats_sync_time_and_inherited_rows(self):
        old=self.add('old');self.save_cost(old,'12',100)
        latest=self.add('latest');self.save_cost(latest,'10',200)
        self.add('old')  # Re-fetching an old order cannot make its cost newest.
        third=self.add('third');self.assertEqual(self.load(third)['lines'][0]['purchase_unit'],'10')
        self.save_cost(old,'11',300)
        fourth=self.add('fourth');self.assertEqual(self.load(fourth)['lines'][0]['purchase_unit'],'11')
        self.assertEqual(self.load(third)['lines'][0]['purchase_unit'],'10')
    def test_no_cross_shop_or_different_offer_and_sku_fallback(self):
        first=self.add('first');self.save_cost(first,'12',100)
        other_shop=self.add('other-shop',shop='s2')
        other_offer=self.add('other-offer',offer='different',sku='123')
        self.assertIsNone(self.load(other_shop)['lines'][0]['purchase_unit'])
        self.assertIsNone(self.load(other_offer)['lines'][0]['purchase_unit'])
        no_offer=self.add('no-offer',offer='',sku='456');self.save_cost(no_offer,'7',200)
        self.assertEqual(self.load(self.add('fallback',offer='',sku='456'))['lines'][0]['purchase_unit'],'7')
    def test_zero_valid_manual_changes_clear_badge_and_deliberate_blank_stays(self):
        source=self.add('source');self.save_cost(source,'0',100)
        new=self.add('new');self.assertEqual(self.load(new)['lines'][0]['purchase_unit'],'0')
        self.save_cost(new,'9',200)
        self.assertEqual(self.load(new)['lines'][0]['purchase_origin'],'manual')
        self.save_cost(new,None,300)
        self.add('new')
        self.assertIsNone(self.load(new)['lines'][0]['purchase_unit'])
        self.assertEqual(self.load(new)['lines'][0]['purchase_origin'],'manual')
    def test_cancelled_returned_targets_not_filled_and_saved_costs_not_overwritten(self):
        source=self.add('source');self.save_cost(source,'12',100)
        cancelled=self.add('cancelled',status='cancelled');returned=self.add('returned',status='returned')
        self.assertIsNone(self.load(cancelled)['lines'][0]['purchase_unit'])
        self.assertIsNone(self.load(returned)['lines'][0]['purchase_unit'])
        old=self.add('old');self.save_cost(old,'8',200)
        self.save_cost(source,'10',300)
        self.assertEqual(self.load(old)['lines'][0]['purchase_unit'],'8')
    def test_legacy_migration_uses_saved_input_time_and_is_idempotent(self):
        old=self.add('old');recent=self.add('recent');pending=self.add('pending')
        with p.connection(self.path) as db:
            db.execute("UPDATE profit_lines SET purchase_unit='12',updated=999999 WHERE order_id=?",(old,))
            db.execute("UPDATE profit_lines SET purchase_unit='8',updated=1 WHERE order_id=?",(recent,))
            for oid,saved in ((old,100),(recent,200)):
                db.execute("INSERT INTO profit_inputs VALUES (?,NULL,'auto',NULL,'2','0',?)",(oid,saved))
            p.ensure_schema(db);first=p.inherit_purchase_prices(db,300)
            p.ensure_schema(db);second=p.inherit_purchase_prices(db,400)
            self.assertEqual(len(first),1);self.assertEqual(second,[])
        self.assertEqual(self.load(pending)['lines'][0]['purchase_unit'],'8')
        self.assertEqual(self.load(pending)['lines'][0]['purchase_origin'],'inherited')
    def test_saving_unrelated_fields_keeps_inherited_origin(self):
        source=self.add('source');self.save_cost(source,'12',100)
        other=self.add('other')
        with p.connection(self.path) as db:
            order=p.load_order(db,other);order['inputs']['forwarder']='3'
            order['lines'][0]['purchase_unit']='12.00'
            p.save_order(db,order,200)
        self.assertEqual(self.load(other)['lines'][0]['purchase_origin'],'inherited')
    def test_weight_memory_reuses_matching_package_only(self):
        first=self.add('first',quantity=1)
        with p.connection(self.path) as db:
            order=p.load_order(db,first)
            order['lines'][0]['purchase_unit']='15'
            order['inputs']['weight_override']='350'
            p.save_order(db,order,now=100)
        second=self.add('second',quantity=1)
        cross=self.add('cross',shop='s2',quantity=3)
        with p.connection(self.path) as db:
            o2=p.enrich_order_sync_info(db,p.load_order(db,second))
            o3=p.enrich_order_sync_info(db,p.load_order(db,cross))
        self.assertEqual(o2['inputs']['weight_override'],'350')
        self.assertEqual(o2['inputs']['weight_origin'],'inherited')
        self.assertEqual(o2['inputs']['weight_source_posting'],'first')
        self.assertIsNotNone(o2['previous_weight'])
        self.assertEqual(o2['previous_weight']['weight_g'],'350')
        with p.connection(self.path) as db:
            o2_clear=p.load_order(db,second)
            o2_clear['inputs']['weight_override']=None
            p.save_order(db,o2_clear,now=150)
        self.add('second')
        self.assertIsNone(self.load(second)['inputs']['weight_override'])
        self.assertEqual(self.load(second)['inputs']['weight_origin'],'manual')
        self.assertIsNone(o3['previous_weight'])
        self.assertEqual(o3['lines'][0]['previous_cost'],'15')
        self.assertEqual(o3['lines'][0]['previous_cost_shop'],'s1')
    def test_measured_weight_rejects_legacy_cross_quantity_override(self):
        source=self.add('source',shop='s1',quantity=1)
        inherited_t=self.add('inherited-t',shop='s1',quantity=2)
        manual_t=self.add('manual-t',shop='s1',quantity=1)
        cross_t=self.add('cross-t',shop='s2',quantity=3)
        self.save_cost(source,'10',100)
        self.save_cost(manual_t,'9',150)
        with p.connection(self.path) as db:
            order=p.load_order(db,source)
            order['lines'][0]['purchase_unit']='11.5'
            order['inputs']['weight_override']='320'
            p.save_order(db,order,now=200)
        self.assertIsNone(self.load(inherited_t)['inputs']['weight_override'])
        with p.connection(self.path) as db:
            # Existing deployment stored twice the source's entire package weight.
            db.execute("INSERT INTO profit_inputs(order_id,weight_override,updated) VALUES (?,'640',?)",
                       (inherited_t,201))
            db.execute("INSERT INTO profit_weight_sources VALUES (?,'inherited',?,?,'640')",
                       (inherited_t,source,201))
            p.inherit_measured_weights(db,now=202)
            audit=db.execute("SELECT old_weight FROM profit_weight_audit WHERE order_id=?",(inherited_t,)).fetchone()
            self.assertEqual(audit['old_weight'],'640')
        self.assertIsNone(self.load(inherited_t)['inputs']['weight_override'])
        self.assertEqual(self.load(inherited_t)['calculation']['api_weight_g'],'400.0')
        with p.connection(self.path) as db:
            order=p.load_order(db,inherited_t)
            order['inputs']['weight_override']='475'
            p.save_order(db,order,now=203)
        next_two=self.add('next-two',quantity=2)
        self.assertEqual(self.load(next_two)['inputs']['weight_override'],'475')
        self.assertEqual(self.load(next_two)['calculation']['template']['id'],'xs')
        self.assertEqual(self.load(manual_t)['inputs']['weight_override'],'320')
        self.assertEqual(self.load(manual_t)['inputs']['weight_origin'],'inherited')
        self.assertIsNone(self.load(cross_t)['inputs']['weight_override'])

    def test_confirming_weight_refreshes_old_manual_and_inherited_packages(self):
        original=self.add('old-source')
        new=self.add('new-source')
        automatic=self.add('automatic')
        use_api=self.add('use-api')
        different_count=self.add('different-count',quantity=2)
        with p.connection(self.path) as db:
            order=p.load_order(db,original)
            order['inputs']['weight_override']='100'
            p.save_order(db,order,now=100)
            order=p.load_order(db,use_api)
            order['inputs']['weight_override']=None
            p.save_order(db,order,now=110)
            order=p.load_order(db,new)
            order['inputs']['weight_override']='312'
            p.save_order(db,order,now=120)
            audit=db.execute('SELECT old_weight,new_weight FROM profit_weight_audit WHERE order_id=?',
                             (automatic,)).fetchone()
            self.assertEqual(tuple(audit),('100','312'))
        self.assertEqual(self.load(automatic)['inputs']['weight_source_posting'],'new-source')
        self.assertEqual(self.load(automatic)['calculation']['weight_g'],'312')
        self.assertEqual(self.load(original)['calculation']['weight_g'],'312')
        self.assertIsNone(self.load(use_api)['inputs']['weight_override'])
        self.assertIsNone(self.load(different_count)['inputs']['weight_override'])

    def test_global_refresh_includes_dispatch_and_is_idempotent(self):
        old=self.add('old');handed=self.add('handed');latest=self.add('latest')
        with p.connection(self.path) as db:
            order=p.load_order(db,old);order['inputs']['weight_override']='312'
            p.save_order(db,order,now=100)
            db.execute("UPDATE profit_weight_sources SET origin='dispatch',source_order_id=? WHERE order_id=?",(old,handed))
            order=p.load_order(db,latest);order['inputs']['weight_override']='475'
            p.save_order(db,order,now=200)
            self.assertEqual(p.inherit_measured_weights(db,now=201),[])
            self.assertEqual(p.load_order(db,latest)['inputs']['weight_origin'],'manual')
            audit=db.execute('SELECT source,old_weight,new_weight FROM profit_weight_audit WHERE order_id=? ORDER BY id DESC',(handed,)).fetchone()
            self.assertEqual((audit['old_weight'],audit['new_weight']),('312','475'))
            self.assertIn('dispatch',audit['source'])
        for oid in (old,handed,latest):self.assertEqual(self.load(oid)['calculation']['weight_g'],'475')
        with p.connection(self.path) as db:
            order=p.load_order(db,old);order['lines'][0]['purchase_unit']='10'
            p.save_order(db,order,now=300)
            self.assertEqual(order['calculation']['weight_g'],'475')
            order=p.load_order(db,old);order['inputs']['weight_override']='480'
            p.save_order(db,order,now=400)
        for oid in (old,handed,latest):self.assertEqual(self.load(oid)['calculation']['weight_g'],'480')

if __name__=='__main__':unittest.main()
