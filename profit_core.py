"""Order snapshots and profit estimates matching the supplied OGL worksheet."""
from __future__ import annotations
import contextlib
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
from pathlib import Path
import sqlite3
import time
import urllib.parse

D = Decimal
TEMPLATES = [
    dict(id='xs', name='超轻小件', scope='≤135元 · ＜500克', fix='2.9', per_g='0.0247', rate='14'),
    dict(id='budget', name='低客单轻小件', scope='≤135元 · 500克至25千克', fix='22.25', per_g='0.0164', rate='14'),
    dict(id='small', name='轻小件', scope='＞135元 · ≤2千克', fix='15.48', per_g='0.0242', rate='17'),
    dict(id='big', name='大件', scope='＞135元 · ＞2千克至25千克', fix='34.82', per_g='0.0164', rate='19'),
    dict(id='premium-small', name='高客单轻小件', scope='原表：7001–250000以上 · ≤5千克', fix='21.28', per_g='0.0242', rate='19'),
    dict(id='premium-big', name='高客单大件', scope='原表：7001–250000以上 · 5至25千克', fix='59.97', per_g='0.022222', rate='19'),
]
TEMPLATE_MAP = {t['id']: t for t in TEMPLATES}
CHINA = dt.timezone(dt.timedelta(hours=8))
CANCELLED_STATUSES = ('cancelled', 'canceled')
EXCLUDED_PROFIT_STATUSES = CANCELLED_STATUSES + ('returned',)
STATUS_LABELS={'awaiting_approve':'待确认','awaiting_packaging':'待备货','awaiting_deliver':'待交运',
               'delivering':'配送中','delivered':'已送达','cancelled':'已取消','canceled':'已取消','returned':'已退货'}
SUBSTATUS_LABELS={
    'posting_created':'订单已创建','posting_packing':'正在备货',
    'posting_acceptance_in_progress':'正在验收','posting_registered':'物流已登记',
    'posting_transferring_to_delivery':'正在交接配送',
    'posting_transferred_to_courier_service':'正在移交快递',
    'posting_in_courier_service':'快递员配送中','posting_on_way_to_city':'运往收货城市',
    'posting_on_way_to_pickup_point':'运往自提点','posting_in_pickup_point':'到达自提点',
    'posting_driver_pick_up':'司机已取件','posting_in_carriage':'运输中',
    'posting_awaiting_passport_data':'等待清关证件资料','posting_not_in_sort_center':'分拣中心待接收',
    'posting_returned_to_warehouse':'已退回仓库','posting_delivered':'已送达',
    'posting_received':'买家已收货','posting_conditionally_delivered':'待确认送达',
    'posting_canceled':'已取消','sent_by_seller':'卖家已发出',
}

def number(value, label='数值', maximum='10000000', allow_blank=False):
    if value is None or str(value).strip() == '':
        if allow_blank: return None
        raise ValueError(f'请填写{label}')
    if isinstance(value, bool): raise ValueError(f'{label}必须是数字')
    try: result = D(str(value).strip())
    except InvalidOperation: raise ValueError(f'{label}必须是数字') from None
    if not result.is_finite() or result < 0 or result > D(maximum):
        raise ValueError(f'{label}超出有效范围')
    if result.as_tuple().exponent < -8: raise ValueError(f'{label}最多保留8位小数')
    return result

def money(value):
    return str(value.quantize(D('.01'), rounding=ROUND_HALF_UP)) if value is not None else None

def ensure_schema(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS profit_orders (
            id INTEGER PRIMARY KEY, shop TEXT NOT NULL, shop_name TEXT NOT NULL,
            posting TEXT NOT NULL, order_number TEXT NOT NULL, source TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT '', created REAL NOT NULL, seen REAL NOT NULL,
            UNIQUE(shop,posting));
        CREATE TABLE IF NOT EXISTS profit_lines (
            id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, line_key TEXT NOT NULL,
            sku TEXT NOT NULL DEFAULT '', offer_id TEXT NOT NULL DEFAULT '', name TEXT NOT NULL,
            quantity INTEGER NOT NULL, unit_price TEXT, currency TEXT NOT NULL DEFAULT '',
            weight_g TEXT, image_url TEXT NOT NULL DEFAULT '',
            purchase_unit TEXT, price_override TEXT, updated REAL NOT NULL,
            UNIQUE(order_id,line_key));
        CREATE TABLE IF NOT EXISTS profit_inputs (
            order_id INTEGER PRIMARY KEY, weight_override TEXT, template TEXT NOT NULL DEFAULT 'auto',
            rate TEXT, forwarder TEXT NOT NULL DEFAULT '2', other_cost TEXT NOT NULL DEFAULT '0',
            updated REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS profit_orders_created ON profit_orders(created DESC);
        CREATE INDEX IF NOT EXISTS profit_lines_order ON profit_lines(order_id);
        CREATE INDEX IF NOT EXISTS profit_lines_sku ON profit_lines(offer_id,purchase_unit);
        CREATE INDEX IF NOT EXISTS profit_orders_refresh ON profit_orders(shop,source,seen);
        CREATE TABLE IF NOT EXISTS profit_sync_state (
            shop TEXT NOT NULL, source TEXT NOT NULL, history_since REAL NOT NULL,
            history_updated REAL NOT NULL, PRIMARY KEY(shop,source));
        CREATE TABLE IF NOT EXISTS profit_image_checks (
            shop TEXT NOT NULL, product_key TEXT NOT NULL, image_url TEXT NOT NULL DEFAULT '',
            checked_at REAL NOT NULL, PRIMARY KEY(shop,product_key));
        CREATE TABLE IF NOT EXISTS profit_weight_audit (
            id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, old_weight TEXT, new_weight TEXT,
            changed REAL NOT NULL, source TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS profit_order_status_details (
            order_id INTEGER PRIMARY KEY,top_status TEXT NOT NULL,substatus TEXT NOT NULL DEFAULT '',
            provider_status TEXT NOT NULL DEFAULT '',tracking_present INTEGER NOT NULL DEFAULT 0,
            checked REAL NOT NULL,changed REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS profit_title_translations (
            source_text TEXT PRIMARY KEY,zh_text TEXT NOT NULL DEFAULT '',provider TEXT NOT NULL DEFAULT '',
            translated_at REAL,next_attempt REAL NOT NULL DEFAULT 0,last_error TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS profit_translation_requests (
            id INTEGER PRIMARY KEY,created REAL NOT NULL,characters INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS profit_translation_control (key TEXT PRIMARY KEY,value REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS profit_title_categories (
            kind TEXT NOT NULL,category_id TEXT NOT NULL,path TEXT NOT NULL,checked REAL NOT NULL,
            PRIMARY KEY(kind,category_id));
        CREATE TABLE IF NOT EXISTS profit_title_product_context (
            shop TEXT NOT NULL,product_key TEXT NOT NULL,category_path TEXT NOT NULL,checked REAL NOT NULL,
            PRIMARY KEY(shop,product_key));
    ''')
    if 'provider' not in {r[1] for r in db.execute('PRAGMA table_info(profit_translation_requests)')}:
        db.execute("ALTER TABLE profit_translation_requests ADD COLUMN provider TEXT NOT NULL DEFAULT 'MyMemory'")

@contextlib.contextmanager
def connection(path):
    db = sqlite3.connect(str(path), timeout=15)
    db.row_factory = sqlite3.Row
    try:
        with db: yield db
    finally: db.close()

def cached_product_image(db, shop, offer_id='', sku=''):
    """Reuse successful images within one shop, including older order snapshots."""
    keys = ([f'offer:{offer_id}'] if offer_id else []) + ([f'sku:{sku}'] if sku else [])
    if not keys:
        return ''
    placeholders = ','.join('?' for _ in keys)
    row = db.execute('SELECT image_url FROM profit_image_checks WHERE shop=? AND product_key IN ('+
                     placeholders+") AND image_url!='' ORDER BY checked_at DESC LIMIT 1", [shop]+keys).fetchone()
    if row:
        return row[0]
    conditions, args = [], [shop]
    if offer_id:
        conditions.append('l.offer_id=?'); args.append(offer_id)
    if sku:
        conditions.append('l.sku=?'); args.append(sku)
    row = db.execute('''SELECT l.image_url FROM profit_lines l JOIN profit_orders o ON o.id=l.order_id
        WHERE o.shop=? AND l.image_url!='' AND ('''+ ' OR '.join(conditions)+
        ') ORDER BY l.updated DESC LIMIT 1', args).fetchone()
    return row[0] if row else ''


def capture_postings(db, shop, source, postings, now):
    """Capture all postings, independent of the once-per-order notification dedup."""
    for posting in postings:
        pnum = str(posting.get('posting_number') or '')
        if not pnum: continue
        value = posting.get('created_at') or posting.get('in_process_at')
        try: created = dt.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
        except (ValueError, TypeError, AttributeError): created = now
        db.execute('''INSERT INTO profit_orders(shop,shop_name,posting,order_number,source,status,created,seen)
            VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(shop,posting) DO UPDATE SET
            shop_name=excluded.shop_name,status=excluded.status,seen=excluded.seen''',
            (shop['id'],shop['name'],pnum,str(posting.get('order_number') or posting.get('order_id') or pnum),
             source,str(posting.get('status') or ''),created,now))
        order_id = db.execute('SELECT id FROM profit_orders WHERE shop=? AND posting=?',(shop['id'],pnum)).fetchone()[0]
        for item in posting.get('products',[]) or []:
            key = str(item.get('offer_id') or item.get('sku') or item.get('product_id') or '')
            if not key: continue
            price = item.get('price')
            currency = item.get('currency_code') or posting.get('currency_code') or ''
            if isinstance(price,dict):
                currency = price.get('currency') or price.get('currency_code') or currency
                price = price.get('amount')
            if not currency: currency = shop.get('currency_code', 'CNY')
            try: price = str(number(price,'订单价',allow_blank=True)) if price is not None else None
            except ValueError: price = None
            weight = item.get('weight')
            try:
                weight = number(weight,'重量',maximum='1000000',allow_blank=True)
                if weight is not None:
                    # Posting list weights are kg unless explicitly marked otherwise.
                    weight *= D(1) if item.get('weight_unit') == 'g' else D(1000)
                    if weight <= 0: weight = None
            except ValueError: weight = None
            try:
                quantity = int(item.get('quantity',1))
                if quantity < 1 or quantity > 100000: continue
            except (TypeError,ValueError): continue
            sku, offer = str(item.get('sku') or ''), str(item.get('offer_id') or '')
            picture = cached_product_image(db, shop['id'], offer, sku)
            db.execute('''INSERT INTO profit_lines(order_id,line_key,sku,offer_id,name,quantity,unit_price,currency,weight_g,image_url,updated)
                VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(order_id,line_key) DO UPDATE SET
                name=excluded.name,quantity=excluded.quantity,
                sku=CASE WHEN profit_lines.sku='' THEN excluded.sku ELSE profit_lines.sku END,
                offer_id=CASE WHEN profit_lines.offer_id='' THEN excluded.offer_id ELSE profit_lines.offer_id END,
                image_url=CASE WHEN profit_lines.image_url='' THEN excluded.image_url ELSE profit_lines.image_url END,
                weight_g=COALESCE(excluded.weight_g,profit_lines.weight_g),updated=excluded.updated''',
                (order_id,key,sku,offer,str(item.get('name') or key),quantity,price,str(currency),
                 str(weight) if weight is not None else None,picture,now))

def recommend_template(revenue, weight):
    # User-confirmed worksheet rules (2026-11-01), using CNY package revenue
    # and the effective package weight (explicit custom weight, otherwise API).
    if weight is None or weight <= 0 or weight > 25000: return None
    if revenue <= 135: return 'xs' if weight < 500 else 'budget'
    return 'small' if weight <= 2000 else 'big'

def calculate(order):
    lines = order['lines']
    inputs = order.get('inputs') or {}
    issues = []
    if not lines: issues.append('商品明细尚未同步')
    revenue = D(0)
    purchase = D(0)
    api_weight = D(0)
    weight_complete = bool(lines)
    for line in lines:
        q = D(line['quantity'])
        price = line.get('price_override')
        if price is None:
            price = line.get('unit_price') if line.get('currency') == 'CNY' else None
        price = number(price,'核算售价',allow_blank=True)
        cost = number(line.get('purchase_unit'),'采购单价',allow_blank=True)
        if price is None: issues.append('请补充人民币核算售价')
        else: revenue += price*q
        if cost is None: issues.append('待填写采购价')
        else: purchase += cost*q
        weight = number(line.get('weight_g'),'API重量',maximum='1000000',allow_blank=True)
        if weight is None: weight_complete = False
        else: api_weight += weight*q
    weight = number(inputs.get('weight_override'),'计费重量',maximum='1000000',allow_blank=True)
    custom_weight = weight
    if weight is None: weight = api_weight if weight_complete else None
    if weight is None or weight <= 0: issues.append('请填写包裹计费重量')
    if weight is not None and weight > 25000: issues.append('重量超过附件25千克范围，请单独核算')
    tid = inputs.get('template','auto')
    if tid == 'auto':
        # Invalid/missing weights already block profit above. A provisional
        # template keeps the breakdown shape stable without accepting a result.
        tid = recommend_template(revenue,weight) or ('xs' if revenue <= 135 else 'small')
    if tid not in TEMPLATE_MAP: raise ValueError('请选择有效的运费档位')
    template = TEMPLATE_MAP[tid]
    rate = number(inputs.get('rate'),'合计扣费率',maximum='99.99',allow_blank=True)
    if rate is None: rate = D(template['rate'])
    forwarder = number(inputs.get('forwarder','2'),'货代费')
    other = number(inputs.get('other_cost','0'),'其他成本')
    shipping = D(template['fix']) + D(template['per_g'])*weight if weight is not None else None
    fee = revenue*rate/100
    profit = None if issues else revenue-purchase-shipping-fee-forwarder-other
    warnings = []
    included = str(order.get('status','')).lower() not in EXCLUDED_PROFIT_STATUSES
    if not included: warnings.append('此包裹已取消或退货，预估金额不计入利润合计；实际损益需另行核对')
    if len(lines)>1: warnings.append('按一个包裹汇总计费；多SKU订单请确认包裹重量及采购单价')
    if inputs.get('template','auto') != 'auto': warnings.append('正在使用手动选择的运费档位')
    return dict(status='ready' if profit is not None else 'incomplete',included_in_summary=included,issues=list(dict.fromkeys(issues)),
                warnings=warnings,template=template,rate=str(rate),weight_g=str(weight) if weight is not None else None,
                api_weight_g=str(api_weight) if weight_complete else None,
                weight_source='custom' if custom_weight is not None else ('api' if weight_complete else 'missing'),
                custom_weight_g=str(custom_weight) if custom_weight is not None else None,revenue=money(revenue),
                purchase_cost=money(purchase) if not any(l.get('purchase_unit') is None for l in lines) and lines else None,
                shipping=money(shipping),shipping_exact=str(shipping) if shipping is not None else None,
                platform_fee=money(fee),forwarder=money(forwarder),other_cost=money(other),
                profit=money(profit),profit_exact=str(profit) if profit is not None else None,
                sales_margin=str((profit/revenue*100).quantize(D('.01'),rounding=ROUND_HALF_UP)) if profit is not None and revenue else None,
                cost_margin=str((profit/purchase*100).quantize(D('.01'),rounding=ROUND_HALF_UP)) if profit is not None and purchase else None)

def load_order(db, order_id):
    row = db.execute('SELECT * FROM profit_orders WHERE id=?',(order_id,)).fetchone()
    if not row: return None
    order = dict(row)
    order['lines'] = [dict(r) for r in db.execute('''SELECT l.*,COALESCE(t.zh_text,'') AS name_zh,
        COALESCE(t.provider,'') AS translation_provider,COALESCE(t.last_error,'') AS translation_error,
        COALESCE(t.next_attempt,0) AS translation_retry_at FROM profit_lines l
        LEFT JOIN profit_title_translations t ON t.source_text=l.name WHERE l.order_id=? ORDER BY l.id''',(order_id,))]
    detail=db.execute('SELECT * FROM profit_order_status_details WHERE order_id=?',(order_id,)).fetchone()
    order['substatus']=detail['substatus'] if detail and detail['top_status']==order['status'] else ''
    order['provider_status']=detail['provider_status'] if detail and detail['top_status']==order['status'] else ''
    order['status_checked_at']=detail['checked'] if detail and detail['top_status']==order['status'] else order['seen']
    order['status_changed_at']=detail['changed'] if detail else None
    terminal=order['status'] in ('delivered','cancelled','canceled','returned')
    order['status_label']=(None if terminal else SUBSTATUS_LABELS.get(order['substatus'])) or STATUS_LABELS.get(order['status'],order['status'] or '状态待同步')
    row = db.execute('SELECT * FROM profit_inputs WHERE order_id=?',(order_id,)).fetchone()
    order['inputs'] = dict(row) if row else dict(template='auto',rate=None,forwarder='2',other_cost='0',weight_override=None)
    for line in order['lines']:
        previous = db.execute('''SELECT l.purchase_unit FROM profit_lines l JOIN profit_orders o ON o.id=l.order_id
            WHERE l.offer_id=? AND l.id<>? AND o.shop=? AND l.purchase_unit IS NOT NULL
            ORDER BY l.updated DESC LIMIT 1''',(line['offer_id'],line['id'],order['shop'])).fetchone() if line['offer_id'] else None
        line['previous_cost'] = previous[0] if previous else None
    order['calculation'] = calculate(order)
    return order

def apply_inputs(order, payload, require_confirmation=True):
    if not isinstance(payload,dict) or not isinstance(payload.get('lines'),list): raise ValueError('填写内容格式不正确')
    prior_weight=number((order.get('inputs') or {}).get('weight_override'),'原计费重量',allow_blank=True)
    api_weight=number(order['calculation'].get('api_weight_g'),'API重量',maximum='1000000',allow_blank=True)
    try: supplied = {int(x['id']): x for x in payload['lines'] if isinstance(x,dict) and 'id' in x}
    except (ValueError,TypeError): raise ValueError('商品标识格式不正确') from None
    expected = {line['id'] for line in order['lines']}
    if set(supplied) != expected: raise ValueError('商品明细已变化，请刷新后重新填写')
    for line in order['lines']:
        for key,label in (('purchase_unit','采购单价'),('price_override','核算售价')):
            value = number(supplied[line['id']].get(key),label,allow_blank=True)
            line[key] = str(value) if value is not None else None
    inputs = payload.get('inputs') or {}
    if not isinstance(inputs,dict): raise ValueError('费用参数格式不正确')
    template = inputs.get('template','auto')
    if template != 'auto' and template not in TEMPLATE_MAP: raise ValueError('运费档位不正确')
    clean = {'template':template}
    for key,label,maximum,blank,default in (
        ('weight_override','计费重量','1000000',True,None),('rate','合计扣费率','99.99',True,None),
        ('forwarder','货代费','10000000',False,'2'),('other_cost','其他成本','10000000',False,'0')):
        value = number(inputs.get(key,default),label,maximum,blank)
        clean[key] = str(value) if value is not None else None
    mode=payload.get('weight_mode')
    new_weight=number(clean.get('weight_override'),'计费重量',maximum='1000000',allow_blank=True)
    if mode=='api' and new_weight is not None:
        raise ValueError('API重量模式不能提交自定义重量，请刷新页面')
    if mode=='custom' and (new_weight is None or new_weight<=0):
        raise ValueError('请填写已核实的实际计费重量')
    if new_weight!=prior_weight:
        if mode not in ('api','custom'):
            raise ValueError('重量来源未确认，请刷新页面后明确选择 API 或自定义重量')
        if 'expected_weight_override' not in payload or number(payload.get('expected_weight_override'),'原自定义重量',maximum='1000000',allow_blank=True)!=prior_weight:
            raise ValueError('计费重量已被其他页面修改，请刷新后重新确认')
        if mode=='custom':
            expected=number(payload.get('expected_api_weight_g'),'参考API重量',maximum='1000000',allow_blank=True)
            if expected!=api_weight:
                raise ValueError('API商品重量已变化，请刷新后重新核实计费重量')
            if require_confirmation and payload.get('weight_confirmed') is not True:
                raise ValueError('请核实并勾选确认使用自定义计费重量')
    order['inputs'] = clean
    order['calculation'] = calculate(order)
    return order

def save_order(db, order, now=None, source='web-save'):
    now = time.time() if now is None else now
    prior = db.execute('SELECT weight_override FROM profit_inputs WHERE order_id=?',(order['id'],)).fetchone()
    old_weight = prior[0] if prior else None
    new_weight = order['inputs'].get('weight_override')
    if old_weight != new_weight:
        db.execute('INSERT INTO profit_weight_audit(order_id,old_weight,new_weight,changed,source) VALUES (?,?,?,?,?)',
                   (order['id'],old_weight,new_weight,now,source))
    for line in order['lines']:
        db.execute('UPDATE profit_lines SET purchase_unit=?,price_override=?,updated=? WHERE id=? AND order_id=?',
                   (line['purchase_unit'],line['price_override'],now,line['id'],order['id']))
    p=order['inputs']
    db.execute('''INSERT INTO profit_inputs(order_id,weight_override,template,rate,forwarder,other_cost,updated)
        VALUES (?,?,?,?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET weight_override=excluded.weight_override,
        template=excluded.template,rate=excluded.rate,forwarder=excluded.forwarder,other_cost=excluded.other_cost,updated=excluded.updated''',
        (order['id'],p.get('weight_override'),p.get('template','auto'),p.get('rate'),p.get('forwarder','2'),p.get('other_cost','0'),now))

def entry_url(settings_path=None):
    path = Path(settings_path) if settings_path else Path(__file__).with_name('profit-web.json')
    try:
        settings=json.loads(path.read_text(encoding='utf-8'))
        base=settings['public_base_url'].rstrip('/')+'/'
        return base+'#k='+urllib.parse.quote(settings['access_token'],safe='')
    except (OSError,ValueError,KeyError): return ''


def order_statistics(db, shops, shop_id='', days=7, now=None):
    """Calendar days in China; count orders, not postings, including zero-order days."""
    now = time.time() if now is None else now
    today = dt.datetime.fromtimestamp(now, CHINA).replace(hour=0,minute=0,second=0,microsecond=0)
    start = today - dt.timedelta(days=days-1)
    selected = [s for s in shops if not shop_id or s['id']==shop_id]
    ids = [s['id'] for s in selected]
    daily = {(start+dt.timedelta(days=i)).date().isoformat():0 for i in range(days)}
    complete = bool(ids)
    if ids:
        placeholders = ','.join('?' for _ in ids)
        # Group before date filtering so split postings cannot become extra orders.
        rows = db.execute('''SELECT shop,order_number,MIN(created) AS created,
            MAX(CASE WHEN lower(status) IN ('cancelled','canceled') THEN 0 ELSE 1 END) AS active
            FROM profit_orders WHERE shop IN ('''+placeholders+''')
            GROUP BY shop,order_number HAVING MIN(created)>=? AND MIN(created)<=?''',ids+[start.timestamp(),now])
        for row in rows:
            if row['active']:
                day=dt.datetime.fromtimestamp(row['created'],CHINA).date().isoformat()
                if day in daily: daily[day]+=1
        coverage={(r['shop'],r['source']):r['history_since'] for r in db.execute(
            'SELECT shop,source,history_since FROM profit_sync_state')}
        complete=all(coverage.get((sid,source),now+1)<=start.timestamp()
                     for sid in ids for source in ('fbs','fbo'))
    total=sum(daily.values())
    return dict(days=days,start_date=start.date().isoformat(),end_date=today.date().isoformat(),
                total_orders=total,today_orders=daily[today.date().isoformat()],
                daily_average=money(D(total)/D(days)) if complete else None,
                complete=complete,shop_count=len(ids),
                daily=[dict(date=day,orders=count) for day,count in daily.items()])


def purchase_order_details(db, shops, shop_id='', date_from='', date_to='', offset=0):
    selected=[s['id'] for s in shops if not shop_id or s['id']==shop_id]
    if not selected: return dict(orders=[],has_more=False,offset=offset,total_count=0)
    query='SELECT id FROM profit_orders WHERE shop IN ('+','.join('?' for _ in selected)+')'
    args=list(selected)
    if date_from:
        query+=' AND created>=?';args.append(dt.datetime.strptime(date_from,'%Y-%m-%d').replace(tzinfo=CHINA).timestamp())
    if date_to:
        query+=' AND created<?';args.append((dt.datetime.strptime(date_to,'%Y-%m-%d').replace(tzinfo=CHINA)+dt.timedelta(days=1)).timestamp())
    query+=" AND EXISTS (SELECT 1 FROM profit_lines l WHERE l.order_id=profit_orders.id AND l.purchase_unit IS NOT NULL AND trim(l.purchase_unit)!='')"
    total_count=db.execute('SELECT COUNT(*) FROM ('+query+')',args).fetchone()[0]
    query+=' ORDER BY created DESC,id DESC LIMIT 21 OFFSET ?';args.append(offset)
    rows=db.execute(query,args).fetchall()
    orders=[]
    for row in rows[:20]:
        order=load_order(db,row['id'])
        recorded=D(0)
        for line in order['lines']:
            cost=number(line['purchase_unit'],'采购单价',allow_blank=True)
            line['purchase_subtotal']=money(cost*D(line['quantity'])) if cost is not None else None
            if cost is not None:recorded+=cost*D(line['quantity'])
        order['recorded_purchase_cost']=money(recorded)
        orders.append(order)
    return dict(orders=orders,has_more=len(rows)>20,offset=offset,total_count=total_count)


def purchase_statistics(db, shops, shop_id='', date_from='', date_to=''):
    """Sum saved procurement spend, including cancelled/returned purchases.

    Independent of the profit-readiness state and the order list's 500-row limit.
    Date bounds are inclusive calendar dates in China, applied to order creation.
    """
    bounds=[]
    for value in (date_from,date_to):
        if not value:
            bounds.append(None); continue
        try:
            parsed=dt.datetime.strptime(value,'%Y-%m-%d').replace(tzinfo=CHINA)
            if parsed.strftime('%Y-%m-%d')!=value: raise ValueError()
        except (TypeError,ValueError): raise ValueError('日期格式不正确') from None
        bounds.append(parsed)
    start,end=bounds
    if start and end and start>end: raise ValueError('开始日期不能晚于结束日期')
    selected=[s['id'] for s in shops if not shop_id or s['id']==shop_id]
    total,cancelled=D(0),D(0)
    packages,pending,filled=set(),set(),set()
    units=0
    if selected:
        query='''SELECT o.id AS order_id,o.status,l.id AS line_id,l.quantity,l.purchase_unit
            FROM profit_orders o LEFT JOIN profit_lines l ON l.order_id=o.id WHERE o.shop IN ('''+','.join('?' for _ in selected)+')'
        args=list(selected)
        if start: query+=' AND o.created>=?'; args.append(start.timestamp())
        if end:
            try: boundary=(end+dt.timedelta(days=1)).timestamp()
            except OverflowError: raise ValueError('结束日期超出有效范围') from None
            query+=' AND o.created<?'; args.append(boundary)
        for row in db.execute(query,args):
            packages.add(row['order_id'])
            cost=number(row['purchase_unit'],'采购单价',allow_blank=True)
            if row['line_id'] is None or cost is None:
                pending.add(row['order_id']); continue
            amount=cost*D(row['quantity'])
            total+=amount; units+=row['quantity']; filled.add(row['order_id'])
            if row['status'].lower() in EXCLUDED_PROFIT_STATUSES: cancelled+=amount
    return dict(total_cost=money(total),cancelled_returned_cost=money(cancelled),
                active_cost=money(total-cancelled),package_count=len(packages),
                filled_packages=len(filled),missing_packages=len(pending),purchased_units=units,
                date_from=date_from,date_to=date_to,all_time=not(date_from or date_to),
                shop_count=len(selected))
