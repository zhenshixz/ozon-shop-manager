import datetime as dt
import sqlite3
import unittest

import recent_sales as sales


class RecentSalesTests(unittest.TestCase):
    def test_units_shop_status_and_beijing_window(self):
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
        db.executescript('''CREATE TABLE profit_orders(id INTEGER,shop TEXT,status TEXT,created REAL);
            CREATE TABLE profit_lines(order_id INTEGER,offer_id TEXT,sku TEXT,quantity INTEGER);''')
        now=dt.datetime(2026,11,11,9,tzinfo=sales.CHINA).timestamp()
        start=dt.datetime(2026,11,5,tzinfo=sales.CHINA).timestamp()
        rows=[('a','awaiting_packaging',start,3),('a','delivered',now,2),
              ('a','cancelled',now,50),('a','returned',now,50),('a','CANCELED',now,50),
              ('a','delivering',start-1,50),('a','delivering',now+1,50),('b','delivered',now,9)]
        for oid,(shop,status,created,qty) in enumerate(rows,1):
            db.execute('INSERT INTO profit_orders VALUES(?,?,?,?)',(oid,shop,status,created))
            db.execute('INSERT INTO profit_lines VALUES(?,?,?,?)',(oid,'same','sku',qty))
        orders=[dict(shop='a',lines=[dict(offer_id='same',sku='sku'),dict(offer_id='missing',sku='other'),dict(offer_id='',sku='')]),
                dict(shop='b',lines=[dict(offer_id='same',sku='sku')])]
        sales.annotate(db,orders,now)
        self.assertEqual([l['sales_7d'] for l in orders[0]['lines']],[5,0,None])
        self.assertEqual(orders[1]['lines'][0]['sales_7d'],9)
        # A different item in the same order must not contribute to the first item.
        db.execute("INSERT INTO profit_lines VALUES(1,'another','other',4)")
        orders[0]['lines'].append(dict(offer_id='another',sku='other'))
        sales.annotate(db,orders,now)
        self.assertEqual(orders[0]['lines'][-1]['sales_7d'],4)
        self.assertEqual(orders[0]['lines'][0]['sales_7d'],5)
        # Missing offer ID falls back to SKU without mixing different identities.
        db.execute("INSERT INTO profit_lines VALUES(1,'','fallback',7)")
        orders[0]['lines'].append(dict(offer_id='',sku='fallback'))
        sales.annotate(db,orders,now)
        self.assertEqual(orders[0]['lines'][-1]['sales_7d'],7)
        db.close()


if __name__=='__main__':unittest.main()
