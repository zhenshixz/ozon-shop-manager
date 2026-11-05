"""Read-only Seller API status snapshots for products used by order/compass pages."""
import json
import sqlite3
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
CACHE = ROOT/'data/product-status.sqlite3'
REASONS = {6: '侵犯版权', 81: '缺少品牌文件'}

def classify(item, sku):
    archived = item.get('is_archived') is True or item.get('is_autoarchived') is True
    entries = [a for a in item.get('availabilities', []) if str(a.get('sku')) == str(sku)]
    values = {a.get('availability') for a in entries}
    state = 'archived' if archived else 'active' if 'AVAILABLE' in values else 'removed' if values == {'HIDDEN'} else 'unknown'
    if not archived and not values and item.get('statuses', {}).get('status_description') == 'Убран из продажи':
        state = 'removed'
    reasons = []
    if state == 'removed':
        for a in entries:
            for r in a.get('reasons', []):
                raw = (r.get('human_text') or {}).get('text', '')
                text = REASONS.get(r.get('id'), raw)
                if text and text not in reasons: reasons.append(text)
    return dict(state=state, reasons=reasons)

def snapshots():
    if not CACHE.exists(): return {}
    try:
        with sqlite3.connect('file:'+str(CACHE)+'?mode=ro', uri=True, timeout=2) as db:
            return {(shop, kind, key): dict(json.loads(payload), checked=checked, stale=time.time()-checked>3900)
                    for shop, kind, key, payload, checked in db.execute('SELECT shop,kind,key,payload,checked FROM snapshots WHERE checked>0')}
    except sqlite3.Error:
        return {}

def annotate_orders(orders):
    data = snapshots()
    for order in orders:
        for line in order['lines']:
            line['product_status'] = lookup(data, order['shop'], line.get('sku'), line.get('offer_id'))

def lookup(data, shop, sku, offer):
    key = (shop, 'sku', str(sku)) if sku and str(sku) != '0' else (shop, 'offer', str(offer))
    return dict(data.get(key, dict(state='unknown',reasons=[],checked=0)), key=json.dumps(key))

def annotate_products(products):
    data = snapshots()
    for p in products:
        p['product_status'] = lookup(data, p['key'].split('|', 1)[0], p.get('sku'), p.get('offer'))

def refresh():
    import fcntl
    lock = open(ROOT/'data/product-status.lock', 'a')
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: return
    config = json.loads((ROOT/'config.json').read_text(encoding='utf-8-sig'))
    settings = json.loads((ROOT/'profit-web.json').read_text(encoding='utf-8-sig'))
    database = settings.get('database', str(ROOT/'data/monitor.sqlite3'))
    with sqlite3.connect('file:'+database+'?mode=ro', uri=True) as source:
        rows = source.execute('SELECT DISTINCT o.shop,l.sku,l.offer_id FROM profit_lines l JOIN profit_orders o ON o.id=l.order_id').fetchall()
    db = sqlite3.connect(CACHE, timeout=15)
    db.execute('''CREATE TABLE IF NOT EXISTS snapshots (shop TEXT,kind TEXT,key TEXT,payload TEXT NOT NULL DEFAULT '{}',checked REAL NOT NULL DEFAULT 0,attempt REAL NOT NULL,error TEXT,PRIMARY KEY(shop,kind,key))''')
    counts = dict(requested=0, updated=0, removed=0, archived=0, failed=0)
    for shop in config['shops']:
        if not shop.get('enabled', True): continue
        ids = {'sku': set(), 'offer': set()}
        for sid, sku, offer in rows:
            if sid != shop['id']: continue
            if str(sku).isdigit() and int(sku)>0: ids['sku'].add(str(sku))
            elif offer: ids['offer'].add(offer)
        for kind, keys in ids.items():
            keys = sorted(keys)
            for offset in range(0, len(keys), 100):
                batch = keys[offset:offset+100]; counts['requested'] += len(batch)
                now = time.time(); found = {}; error = 'not-returned'
                try:
                    req = urllib.request.Request('https://api-seller.ozon.ru/v3/product/info/list',
                        data=json.dumps({'sku' if kind=='sku' else 'offer_id':batch}).encode(),
                        headers={'Client-Id':shop['client_id'], 'Api-Key':shop['api_key'], 'Content-Type':'application/json'}, method='POST')
                    with urllib.request.urlopen(req, timeout=30) as response: result = json.load(response)
                    if not isinstance(result.get('items'), list): raise ValueError('invalid-response')
                    for item in result['items']:
                        key = str(item.get('sku' if kind=='sku' else 'offer_id', ''))
                        if key in batch: found[key] = classify(item, item.get('sku'))
                except Exception as exc:
                    # Never log request headers, credentials, or response bodies.
                    error = type(exc).__name__
                for key in batch:
                    value = found.get(key)
                    if value is not None:
                        db.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?,NULL) ON CONFLICT(shop,kind,key) DO UPDATE SET payload=excluded.payload,checked=excluded.checked,attempt=excluded.attempt,error=NULL',
                                   (shop['id'],kind,key,json.dumps(value,ensure_ascii=False),now,now))
                        counts['updated'] += 1
                        if value['state'] in ('removed', 'archived'): counts[value['state']] += 1
                    else:
                        db.execute("INSERT INTO snapshots(shop,kind,key,attempt,error) VALUES (?,?,?,?,?) ON CONFLICT(shop,kind,key) DO UPDATE SET attempt=excluded.attempt,error=excluded.error",(shop['id'],kind,key,now,error))
                        counts['failed'] += 1
                db.commit()
                time.sleep(1.5)
    db.close(); lock.close()
    print(json.dumps(counts), flush=True)
    if counts['failed']: raise SystemExit(1)

if __name__ == '__main__': refresh()
