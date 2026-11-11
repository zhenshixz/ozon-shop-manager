"""Batch-only recent unit sales for order cards; no external API or writes."""
import datetime as dt
import time

from profit_core import CHINA, EXCLUDED_PROFIT_STATUSES


def product_key(line):
    offer = line.get('offer_id')
    sku = line.get('sku')
    return 'offer:'+offer if offer else 'sku:'+sku if sku else None


def annotate(db, orders, now=None):
    if not orders: return
    now = time.time() if now is None else now
    today = dt.datetime.fromtimestamp(now, CHINA).replace(hour=0, minute=0, second=0, microsecond=0)
    start = (today-dt.timedelta(days=6)).timestamp()
    shops = sorted({o['shop'] for o in orders})
    excluded = ','.join('?' for _ in EXCLUDED_PROFIT_STATUSES)
    placeholders = ','.join('?' for _ in shops)
    query = f'''SELECT o.shop,
        CASE WHEN l.offer_id<>'' THEN 'offer:'||l.offer_id ELSE 'sku:'||l.sku END AS product,
        SUM(l.quantity) AS units
        FROM profit_orders o JOIN profit_lines l ON l.order_id=o.id
        WHERE o.created>=? AND o.created<=? AND o.shop IN ({placeholders})
          AND lower(o.status) NOT IN ({excluded}) AND (l.offer_id<>'' OR l.sku<>'')
        GROUP BY o.shop,product'''
    totals = {(r['shop'], r['product']): int(r['units']) for r in db.execute(
        query, [start,now]+shops+list(EXCLUDED_PROFIT_STATUSES))}
    for order in orders:
        for line in order['lines']:
            key = product_key(line)
            line['sales_7d'] = totals.get((order['shop'], key), 0) if key else None
