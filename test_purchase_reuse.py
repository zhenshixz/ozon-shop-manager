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

if __name__=='__main__':unittest.main()
