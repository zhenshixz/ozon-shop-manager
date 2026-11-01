"""Read-only Seller API enrichment, independent of new-order notification cursors."""
import datetime as dt
import time
import urllib.parse

import profit_core as core


def history_start(now):
    today=dt.datetime.fromtimestamp(now,core.CHINA).replace(hour=0,minute=0,second=0,microsecond=0)
    return (today-dt.timedelta(days=29)).timestamp()


def refresh_orders(store, api, shop, source, now=None):
    now=time.time() if now is None else now
    start=history_start(now)
    with store.connection() as db:
        state=db.execute('SELECT * FROM profit_sync_state WHERE shop=? AND source=?',
                         (shop['id'],source)).fetchone()
    # Reconcile the thirty-day window every six hours, and fill missing coverage.
    if state is None or state['history_since']>start or now-state['history_updated']>=21600:
        postings=api.postings(source,start,now)
        for posting in postings:
            value=posting.get('created_at') or posting.get('in_process_at')
            parsed=dt.datetime.fromisoformat(str(value).replace('Z','+00:00'))
            if parsed.tzinfo is None or not posting.get('posting_number') or not posting.get('status'):
                raise ValueError('Incomplete history response; coverage was not advanced')
        with store.connection() as db:
            core.capture_postings(db,shop,source,postings,now)
            db.execute('''INSERT INTO profit_sync_state VALUES (?,?,?,?)
                ON CONFLICT(shop,source) DO UPDATE SET
                history_since=MIN(profit_sync_state.history_since,excluded.history_since),
                history_updated=excluded.history_updated''',(shop['id'],source,start,now))
    # Old active orders stay in this queue regardless of their creation date.
    # Refresh terminals less often to catch late changes without wasting requests.
    with store.connection() as db:
        orders=db.execute('''SELECT o.id,o.posting FROM profit_orders o LEFT JOIN profit_order_status_details d ON d.order_id=o.id
            WHERE o.shop=? AND o.source=? AND
            ((lower(o.status) NOT IN ('delivered','cancelled','canceled','returned') AND (d.checked IS NULL OR d.checked<=?)) OR
             (o.created>=? AND (d.checked IS NULL OR d.checked<=?))) ORDER BY COALESCE(d.checked,0),o.id LIMIT 25''',
            (shop['id'],source,now-240,start,now-21600)).fetchall()
    for row in orders:
        posting=api.posting(source,row['posting'])
        status=posting.get('status')
        if posting.get('posting_number')!=row['posting'] or not isinstance(status,str) or not status.strip():
            raise ValueError('Incomplete posting status; last update was not advanced')
        with store.connection() as db:
            # Do not replace cost inputs, quantity, price, weight, or creation time.
            db.execute('UPDATE profit_orders SET status=?,seen=? WHERE id=?',
                       (status,now,row['id']))
            substatus=posting.get('substatus') or ''
            provider=posting.get('provider_status') or ''
            if not isinstance(substatus,str) or not isinstance(provider,str):raise ValueError('Invalid status detail')
            db.execute('''INSERT INTO profit_order_status_details VALUES (?,?,?,?,?,?,?) ON CONFLICT(order_id)
                DO UPDATE SET top_status=excluded.top_status,substatus=excluded.substatus,
                provider_status=excluded.provider_status,tracking_present=excluded.tracking_present,checked=excluded.checked,
                changed=CASE WHEN profit_order_status_details.top_status!=excluded.top_status
                    OR profit_order_status_details.substatus!=excluded.substatus THEN excluded.changed
                    ELSE profit_order_status_details.changed END''',
                (row['id'],status,substatus,provider,int(bool(posting.get('tracking_number'))),now,now))


def image_url(product):
    for field in ('primary_image','images'):
        values=product.get(field) or []
        if isinstance(values,str): values=[values]
        if not isinstance(values,list): continue
        for value in values:
            if not isinstance(value,str): continue
            parsed=urllib.parse.urlsplit(value)
            if parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password:
                return value
    return ''


def refresh_images(store, api, shop, now=None):
    now=time.time() if now is None else now
    with store.connection() as db:
        rows=db.execute('''SELECT DISTINCT l.offer_id,l.sku FROM profit_lines l
            JOIN profit_orders o ON o.id=l.order_id WHERE o.shop=? AND l.image_url='' ''',(shop['id'],)).fetchall()
        checked={r['product_key']:dict(r) for r in db.execute(
            'SELECT * FROM profit_image_checks WHERE shop=?',(shop['id'],))}
        cached={(row['offer_id'],row['sku']):core.cached_product_image(
            db,shop['id'],row['offer_id'],row['sku']) for row in rows}
    pending={}
    for row in rows:
        offer,sku=row['offer_id'],row['sku']
        key='offer:'+offer if offer else 'sku:'+sku
        if key=='sku:': continue
        if cached[(offer,sku)]:
            _save_product_image(store,shop,offer,sku,cached[(offer,sku)],now)
            continue
        previous=checked.get(key)
        # Empty responses are transient, not an hour-long "no image" verdict.
        if previous and now-previous['checked_at']<10:
            continue
        pending[key]=(offer,sku)
    # Round-robin by previous attempt so genuinely unavailable products cannot
    # starve untried ones when the queue exceeds one API batch.
    keys=sorted(pending,key=lambda key:checked.get(key,{}).get('checked_at',0))[:100]
    if not keys: return
    found={}
    offers=list(dict.fromkeys(pending[key][0] for key in keys if pending[key][0]))
    if offers:
        for item in api.product_info(offers):
            url=image_url(item)
            if url and item.get('offer_id') is not None:
                found['offer:'+str(item['offer_id'])]=url
    # Some newly listed goods are visible by SKU before offer_id lookup returns
    # a usable picture. Try that independent identifier in the same pass.
    fallback=[key for key in keys if not found.get(key) and pending[key][1]]
    skus=list(dict.fromkeys(pending[key][1] for key in fallback))
    if skus:
        for item in api.product_info(skus,by_sku=True):
            url=image_url(item)
            if url and item.get('sku') is not None:
                found['sku:'+str(item['sku'])]=url
    for key in keys:
        offer,sku=pending[key]
        url=found.get(key) or found.get('sku:'+sku,'')
        if url: _save_product_image(store,shop,offer,sku,url,now)
        else: _save_image(store,shop,key,'',now)


def _save_product_image(store,shop,offer,sku,url,checked_at):
    for key in (('offer:'+offer if offer else ''),('sku:'+sku if sku else '')):
        if key: _save_image(store,shop,key,url,checked_at)


def _save_image(store,shop,key,url,checked_at):
    with store.connection() as db:
        db.execute('''INSERT INTO profit_image_checks VALUES (?,?,?,?)
            ON CONFLICT(shop,product_key) DO UPDATE SET
            image_url=CASE WHEN excluded.image_url!='' THEN excluded.image_url ELSE profit_image_checks.image_url END,
            checked_at=excluded.checked_at''',
            (shop['id'],key,url,checked_at))
        if url:
            field='offer_id' if key.startswith('offer:') else 'sku'
            db.execute('UPDATE profit_lines SET image_url=? WHERE '+field+'''=? AND order_id IN
                (SELECT id FROM profit_orders WHERE shop=?)''',(url,key.split(':',1)[1],shop['id']))
