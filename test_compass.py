import tempfile,time,unittest
import monitor,profit_core as core,compass_core as compass

class CompassTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=monitor.Store(self.tmp.name);self.now=time.time()
    def post(self,number,products,status='awaiting_packaging'):
        return dict(posting_number=number,order_number=number,created_at=monitor.stamp(self.now),status=status,products=products)
    def test_same_sample_margin_allocation_and_exclusions(self):
        items=[dict(offer_id='a',name='A',quantity=1,price='100'),dict(offer_id='b',name='B',quantity=1,price='100')]
        with self.store.connection() as db:
            core.capture_postings(db,{'id':'s','name':'Shop'},'fbs',[self.post('1',items),self.post('2',[dict(offer_id='x',name='X',quantity=1,price='999')]),self.post('3',items,'cancelled')],self.now)
            db.execute("UPDATE profit_lines SET purchase_unit=CASE offer_id WHEN 'a' THEN '80' WHEN 'b' THEN '10' END WHERE order_id=1")
            db.execute("INSERT INTO profit_inputs VALUES (1,'500','small','0','0','0',?)",(self.now,))
            result=compass.build(db)
            self.assertEqual(result['summary']['sales'],'1199.00')
            self.assertEqual(result['summary']['ready_sales'],'200.00')
            self.assertEqual(result['summary']['orders'],2)
            products={p['offer']:p for p in result['products']}
            self.assertEqual(core.D(products['b']['profit'])-core.D(products['a']['profit']),70)
            self.assertEqual(sum(core.D(p['profit']) for p in products.values()),core.D(result['summary']['profit']))
            self.assertEqual(products['x']['ready'],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)
    def test_unit_profit_uses_only_calculated_units_not_rows_or_total_sales(self):
        items=[dict(offer_id='a',name='A',quantity=3,price='30')]
        with self.store.connection() as db:
            core.capture_postings(db,{'id':'s','name':'Shop'},'fbs',[self.post('1',items),self.post('2',[dict(offer_id='a',name='A',quantity=2,price='30')]),self.post('3',items,'cancelled')],self.now)
            db.execute("UPDATE profit_lines SET purchase_unit='10' WHERE order_id=1")
            db.execute("INSERT INTO profit_inputs VALUES (1,'500','small','0','0','0',?)",(self.now,))
            p=compass.build(db)['products'][0]
            self.assertEqual(p['quantity'],5);self.assertEqual(p['ready_quantity'],3)
            self.assertEqual(p['unit_profit'],core.money(core.D(p['profit'])/3))

    def test_shop_dimension_counts_orders_not_postings_and_keeps_zero_shops(self):
        with self.store.connection() as db:
            a=self.post('1-a',[dict(offer_id='a',name='A',quantity=1,price='30')]);a['order_number']='1'
            b=self.post('1-b',[dict(offer_id='a',name='A',quantity=1,price='30')]);b['order_number']='1'
            core.capture_postings(db,{'id':'s','name':'Shop'},'fbs',[a,b,self.post('cancelled',[dict(offer_id='b',name='B',quantity=9,price='100')],'cancelled')],self.now)
            db.execute("UPDATE profit_lines SET purchase_unit='10' WHERE order_id IN (1,2)")
            for oid in (1,2):db.execute("INSERT INTO profit_inputs VALUES (?,'500','small','0','0','0',?)",(oid,self.now))
            configured=[{'id':'s','name':'Shop'},{'id':'empty','name':'Zero shop'}]
            result=compass.build(db,shops=configured);stores={s['id']:s for s in result['shops']}
            self.assertEqual(stores['s']['quantity'],1);self.assertEqual(stores['s']['units'],2)
            self.assertEqual(stores['s']['ready'],2)
            self.assertEqual(stores['s']['profit'],result['summary']['profit'])
            self.assertEqual(stores['empty']['quantity'],0);self.assertEqual(stores['empty']['ready'],0)
            self.assertEqual([s['id'] for s in compass.build(db,shop='empty',shops=configured)['shops']],['empty'])

    def test_full_history_over_500_and_search(self):
        with self.store.connection() as db:
            core.capture_postings(db,{'id':'s','name':'Shop'},'fbs',[self.post(str(i),[dict(offer_id='a',name='Hat',quantity=1,price='1')]) for i in range(501)],self.now)
            result=compass.build(db)
            self.assertEqual(result['summary']['sales'],'501.00');self.assertEqual(result['summary']['orders'],501)
            self.assertEqual(compass.build(db,search='missing')['summary']['orders'],0)
            with self.assertRaises(ValueError):compass.build(db,date_from='2026-11-02',date_to='2026-11-01')

if __name__=='__main__':unittest.main()
