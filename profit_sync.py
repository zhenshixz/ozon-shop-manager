"""Read-only Seller API enrichment, independent of new-order notification cursors."""
import datetime as dt
import logging
import time
import urllib.parse

import profit_core as core

STATUS_OVERLAP = 900
LOG = logging.getLogger('ozon-monitor.profit-sync')


def status_version(db, shop_id=''):
    row=db.execute('SELECT value FROM meta WHERE key=?',('profit-status-version/'+(shop_id or 'global'),)).fetchone()
    return row[0] if row else '0'


def bump_status_version(db, shop_id):
    for scope in ('global',shop_id):
        db.execute('''INSERT INTO meta(key,value) VALUES (?, '1') ON CONFLICT(key)
            DO UPDATE SET value=CAST(meta.value AS INTEGER)+1''',('profit-status-version/'+scope,))


def status_values(posting, require_substatus=False):
    number, status = posting.get('posting_number'), posting.get('status')
    substatus = posting.get('substatus') if require_substatus else (posting.get('substatus') or '')
    provider = posting.get('provider_status') or ''
    if (not isinstance(number,str) or not number.strip() or
            not isinstance(status,str) or not status.strip() or
            not isinstance(substatus,str) or not isinstance(provider,str)):
        raise ValueError('Incomplete posting status; last update was not advanced')
    return number, status, substatus, provider, int(bool(posting.get('tracking_number')))


def save_status(db, order_id, values, now):
    _, status, substatus, provider, tracking = values
    if not db.in_transaction: db.execute('BEGIN IMMEDIATE')
    prior=db.execute('''SELECT o.shop,o.status,d.top_status,d.substatus FROM profit_orders o
        LEFT JOIN profit_order_status_details d ON d.order_id=o.id WHERE o.id=?''',(order_id,)).fetchone()
    # A slower background request must not undo a newer manual/status refresh.
    changed = db.execute('''UPDATE profit_orders SET status=?,seen=MAX(seen,?) WHERE id=?
        AND NOT EXISTS (SELECT 1 FROM profit_order_status_details WHERE order_id=? AND checked>?)''',
        (status,now,order_id,order_id,now)).rowcount
    if not changed: return
    db.execute('''INSERT INTO profit_order_status_details VALUES (?,?,?,?,?,?,?) ON CONFLICT(order_id)
        DO UPDATE SET top_status=excluded.top_status,substatus=excluded.substatus,
        provider_status=excluded.provider_status,tracking_present=excluded.tracking_present,checked=excluded.checked,
        changed=CASE WHEN profit_order_status_details.top_status!=excluded.top_status
            OR profit_order_status_details.substatus!=excluded.substatus THEN excluded.changed
            ELSE profit_order_status_details.changed END''',
        (order_id,status,substatus,provider,tracking,now,now))
    if prior and (prior['status']!=status or prior['top_status']!=status or prior['substatus']!=substatus):
        bump_status_version(db,prior['shop'])


def save_status_batch(db, shop, source, values, now):
    for value in values:
        row=db.execute('SELECT id FROM profit_orders WHERE shop=? AND posting=? AND source=?',
                       (shop['id'],value[0],source)).fetchone()
        if row: save_status(db,row['id'],value,now)


def status_cursor_key(shop):
    return 'profit-status:fbs:'+shop['id']


def save_status_cursor(db, key, now):
    db.execute('''INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key)
        DO UPDATE SET value=excluded.value WHERE CAST(meta.value AS REAL)<CAST(excluded.value AS REAL)''',
        (key,str(now)))


def history_start(now):
    today=dt.datetime.fromtimestamp(now,core.CHINA).replace(hour=0,minute=0,second=0,microsecond=0)
    return (today-dt.timedelta(days=29)).timestamp()


def refresh_orders(store, api, shop, source, now=None, force=False):
    now=time.time() if now is None else now
    started=time.monotonic()
    start=history_start(now)
    history=None
    with store.connection() as db:
        state=db.execute('SELECT * FROM profit_sync_state WHERE shop=? AND source=?',
                         (shop['id'],source)).fetchone()
    # Reconcile the thirty-day window every six hours, and fill missing coverage.
    if force or state is None or state['history_since']>start or now-state['history_updated']>=21600:
        postings=api.postings(source,start,now)
        values=[status_values(p,require_substatus=source=='fbs') for p in postings]
        for posting in postings:
            value=posting.get('created_at') or posting.get('in_process_at')
            parsed=dt.datetime.fromisoformat(str(value).replace('Z','+00:00'))
            if parsed.tzinfo is None or not posting.get('posting_number') or not posting.get('status'):
                raise ValueError('Incomplete history response; coverage was not advanced')
        with store.connection() as db:
            core.capture_postings(db,shop,source,postings,now)
            save_status_batch(db,shop,source,values,now)
            db.execute('''INSERT INTO profit_sync_state VALUES (?,?,?,?)
                ON CONFLICT(shop,source) DO UPDATE SET
                history_since=MIN(profit_sync_state.history_since,excluded.history_since),
                history_updated=excluded.history_updated''',(shop['id'],source,start,now))
        history=postings
    if source=='fbs':
        refresh_fbs_statuses(store,api,shop,start,now,history)
    if force:
        return len(postings)
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
        values=status_values(posting)
        if values[0]!=row['posting']:
            raise ValueError('Incomplete posting status; last update was not advanced')
        with store.connection() as db:
            # Do not replace cost inputs, quantity, price, weight, or creation time.
            save_status(db,row['id'],values,now)
    if source=='fbs':
        LOG.info('店铺 %s FBS批量状态同步完成；详情补查 %d 笔，总耗时 %.2f 秒',
                 shop['name'],len(orders),time.monotonic()-started)


def refresh_fbs_statuses(store, api, shop, start, now, history=None):
    """One paginated change feed covers ALL known FBS orders, not 25 per cycle."""
    key=status_cursor_key(shop)
    with store.connection() as db:
        row=db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
        cursor=float(row[0]) if row else None
        oldest=db.execute('''SELECT MIN(created) FROM profit_orders WHERE shop=? AND source='fbs'
            AND lower(status) NOT IN ('delivered','cancelled','canceled','returned')''',(shop['id'],)).fetchone()[0]
    since=min(start,oldest-1) if oldest is not None else start
    baseline=cursor is None or history is not None
    # Reuse the existing six-hour reconciliation when it covers old active orders.
    if history is not None and since==start:
        postings=history
    elif baseline:
        postings=api.postings('fbs',since,now)
    else:
        postings=api.postings('fbs',since,now,changed_since=min(cursor,now)-STATUS_OVERLAP)
    values=[status_values(p,require_substatus=True) for p in postings]
    with store.connection() as db:
        save_status_batch(db,shop,'fbs',values,now)
        if not baseline:
            # A complete delta proves previously verified, absent orders unchanged.
            # Never certify gaps, missing details, or a different newly ingested status.
            db.execute('''UPDATE profit_order_status_details SET checked=? WHERE order_id IN (
                    SELECT o.id FROM profit_orders o JOIN profit_order_status_details d ON d.order_id=o.id
                    WHERE o.shop=? AND o.source='fbs' AND o.created>=? AND o.created<=?
                    AND d.checked>=? AND d.checked<=? AND o.status=d.top_status)''',
                (now,shop['id'],since,now,cursor,now))
        save_status_cursor(db,key,now)
    LOG.info('店铺 %s FBS状态%s：批量返回 %d 笔',
             shop['name'],'校准' if baseline else '增量',len(postings))


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
