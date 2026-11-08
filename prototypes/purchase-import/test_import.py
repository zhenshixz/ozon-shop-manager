import copy
import unittest
from server import Sandbox

def calculate(o):
    return dict(profit='0.00', weight_g=o['inputs'].get('weight_override') or '200')

def order(oid=1, posting='TEST-1', shop='Demo001'):
    return dict(id=oid, shop_name=shop, posting=posting, calculation=dict(profit='0.00',weight_g='200'),
                inputs=dict(weight_override=None,weight_origin=''),
                lines=[dict(id=oid, offer_id='offer-a', sku='sku-a', name='测试商品', quantity=2,
                            purchase_unit='10', purchase_origin='manual')])

def row(**kw):
    result={'row':2,'店铺名称':'Demo001','订单编号':'TEST-1','平台SKU':'offer-a','产品数量':'2',
            '采购金额(产品-RMB)':'31.07','SKU ID':'sku-a','本地备注':'854g'}
    result.update(kw)
    return result

class ImportTest(unittest.TestCase):
    def setUp(self):
        self.box=Sandbox(dict(orders=[order(),order(2,'TEST-2'),order(3,'TEST-1','Demo002')]),calculate)

    def test_cost_precision_weight_and_scope(self):
        p=self.box.preview([row()]); ids=[c['id'] for c in p['entries'][0]['changes']]
        original=copy.deepcopy(self.box.orders)
        self.box.apply(p['token'], ids)
        self.assertEqual(self.box.orders[0]['lines'][0]['purchase_unit'],'15.535')
        self.assertEqual(self.box.orders[0]['inputs']['weight_override'],'854')
        self.assertEqual(self.box.orders[1:],original[1:])
        self.box.undo(); self.assertEqual(self.box.orders,original)

    def test_unchecked_weight_is_preserved(self):
        p=self.box.preview([row()]); self.box.apply(p['token'],['1:1:cost'])
        self.assertIsNone(self.box.orders[0]['inputs']['weight_override'])

    def test_repeated_import_no_changes(self):
        p=self.box.preview([row()]); self.box.apply(p['token'],[c['id'] for c in p['entries'][0]['changes']])
        self.assertFalse(self.box.preview([row()])['entries'][0]['changes'])

    def test_duplicates_and_quantity_mismatch(self):
        p=self.box.preview([row(),row()])
        self.assertTrue(all(e['error'] and not e['changes'] for e in p['entries']))
        self.assertTrue(self.box.preview([row(产品数量='3')])['entries'][0]['error'])

    def test_bad_note_and_empty_cost(self):
        e=self.box.preview([row(本地备注='11-07;312g;已采购',**{'采购金额(产品-RMB)':''})])['entries'][0]
        self.assertFalse(e['changes'])

    def test_stale_preview_and_unknown_field(self):
        p=self.box.preview([row()])
        with self.assertRaises(ValueError): self.box.apply(p['token'],['2:weight'])
        self.box.apply(p['token'],['1:1:cost'])
        with self.assertRaises(ValueError): self.box.apply(p['token'],['1:weight'])

    def test_split_plan_blocks_whole_weight(self):
        self.box.orders[0]['dispatch_plan']={'packages':[{},{}]}
        e=self.box.preview([row()])['entries'][0]
        self.assertEqual([c['field'] for c in e['changes']],['cost'])

    def test_multiple_products_weight_is_not_duplicated(self):
        self.box.orders[0]['lines'].append(dict(id=4,offer_id='offer-b',sku='sku-b',quantity=1,purchase_unit='1',name='B'))
        e=self.box.preview([row()])['entries'][0]
        self.assertEqual([c['field'] for c in e['changes']],['cost'])

if __name__=='__main__': unittest.main()
