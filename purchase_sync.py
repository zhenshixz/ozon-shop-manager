"""Small, confirmed Miaoshou browser batches. Never calls external APIs.

Field-level baselines distinguish unchanged upstream values from local edits.
All business writes and their audit records commit in the same transaction.
"""
import copy
import datetime as dt
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path
import re
import secrets
import time
import profit_core as core

LIMIT=5
TTL=1800

def encode(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def fingerprint(order):
    fields=['id','offer_id','sku','quantity','purchase_unit','price_override','unit_price','currency','weight_g']
    value=dict(shop=order['shop'],posting=order['posting'],status=order['status'],
               lines=[{k:x.get(k) for k in fields} for x in order['lines']],
               inputs={k:order['inputs'].get(k) for k in ['weight_override','template','rate','forwarder','other_cost','weight_origin']},
               dispatch=order.get('dispatch_plan'))
    return hashlib.sha256(encode(value).encode()).hexdigest()

def schema(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS purchase_sync_previews(id TEXT PRIMARY KEY,owner TEXT NOT NULL,
        created REAL NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS purchase_sync_fields(shop TEXT,posting TEXT,product TEXT,field TEXT,
        observed TEXT,seen REAL NOT NULL,status TEXT NOT NULL,source_value TEXT,target_value TEXT,
        applied REAL,PRIMARY KEY(shop,posting,product,field));
      CREATE TABLE IF NOT EXISTS purchase_sync_batches(id TEXT PRIMARY KEY,owner TEXT NOT NULL,
        created REAL NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL);
    ''')

def normalize(data):
    if not isinstance(data,dict) or data.get('mode','incremental') not in ('incremental','full'):
        raise ValueError('请选择增量或全量核对')
    rows=data.get('records')
    if not isinstance(rows,list) or not 1<=len(rows)<=LIMIT:raise ValueError('MVP每批仅支持1—5笔订单')
    out=[]
    for row in rows:
        if not isinstance(row,dict):raise ValueError('订单数据格式不正确')
        # Only these allowlisted fields can cross the browser/server boundary.
        x={k:str(row.get(k) if row.get(k) is not None else '').strip() for k in
           ('shop_name','posting','offer_id','sku','quantity','cost_total','note','identity_error')}
        if any(len(v)>200 for v in x.values()):raise ValueError('字段过长，请核对读取结果')
        if not x['shop_name'] or not re.fullmatch(r'\d{5,14}-\d{2,10}-\d{1,5}',x['posting']):
            raise ValueError('缺少店铺或完整OZON订单编号')
        if not x['offer_id'] and not re.fullmatch(r'[1-9]\d{5,17}',x['sku']):raise ValueError('缺少商品货号或SKU')
        out.append(x)
    if len({(x['shop_name'],x['posting'],x['offer_id'] or x['sku']) for x in out})!=len(out):
        raise ValueError('本批有重复的店铺＋订单＋商品，请重新读取')
    return out,data.get('mode','incremental')

def positive(value,label):
    result=core.number(value,label)
    if result is None or result<=0:raise ValueError(label+'为空或为0，等待后续补齐')
    return result

def _source(db,key):
    row=db.execute('SELECT * FROM purchase_sync_fields WHERE shop=? AND posting=? AND product=? AND field=?',key).fetchone()
    return dict(row) if row else None

def _seen(db,key,value,status,now):
    db.execute('''INSERT INTO purchase_sync_fields(shop,posting,product,field,observed,seen,status) VALUES(?,?,?,?,?,?,?)
      ON CONFLICT(shop,posting,product,field) DO UPDATE SET observed=excluded.observed,seen=excluded.seen,status=excluded.status''',
      (*key,value,now,status))

def _baseline(db,key,source,target,now):
    db.execute('UPDATE purchase_sync_fields SET source_value=?,target_value=?,applied=?,status=? WHERE shop=? AND posting=? AND product=? AND field=?',
               (source,target,now,'synced',*key))

def preview(db,data,owner,shops,now=None):
    now=time.time() if now is None else now
    rows,mode=normalize(data)
    allowed={s['id'] for s in shops}
    entries=[];keys={};resolved={}
    db.execute('BEGIN IMMEDIATE')
    for index,x in enumerate(rows):
        product=x['offer_id'] or 'sku:'+x['sku']
        e=dict(row=index+1,source=x,changes=[],notes=[],error='',shop_name=x['shop_name'],posting=x['posting'],product=product)
        entries.append(e)
        order=None
        try:
            if x['identity_error']:raise ValueError(x['identity_error'])
            orders=db.execute('SELECT id,shop FROM profit_orders WHERE lower(shop_name)=lower(?) AND posting=?',(x['shop_name'],x['posting'])).fetchall()
            matches=[r for r in orders if r['shop'] in allowed]
            if len(matches)!=1:raise ValueError('店铺＋完整订单号未唯一匹配；不修改数据')
            order=core.load_order(db,matches[0]['id'])
            if len(order['lines'])!=1:raise ValueError('MVP仅支持单款订单，混款采购金额请在详情核对')
            lines=[l for l in order['lines'] if (l['offer_id']==x['offer_id'] if x['offer_id'] else str(l['sku'])==x['sku'])]
            if len(lines)!=1:raise ValueError('商品货号/SKU未唯一匹配')
            line=lines[0]
            if x['sku'] and str(line['sku'])!=x['sku']:raise ValueError('货号和SKU不一致')
            quantity=positive(x['quantity'],'商品数量')
            if quantity!=line['quantity']:raise ValueError('妙手件数与当前订单件数不一致，可能已经拆单')
            identity=(order['id'],line['id'])
            if identity in resolved:raise ValueError('本批两条记录指向同一商品，请重新读取')
            resolved[identity]=True
            e.update(order_id=order['id'],line_id=line['id'],shop=order['shop'],sku=line['sku'],offer_id=line['offer_id'],
                name=line.get('name_zh') or line['name'],image=line.get('image_url',''),quantity=line['quantity'],
                current_profit=order['calculation']['profit'],fingerprint=fingerprint(order))
            current_cost=core.number(line['purchase_unit'],allow_blank=True)
            current_weight=core.number(order['inputs'].get('weight_override'),allow_blank=True)
            e.update(current_cost=str(current_cost*quantity) if current_cost is not None else None,
                     current_weight=order['calculation']['weight_g'])
            for field in ('cost','weight'):
                key=(order['shop'],order['posting'],product,field)
                old=_source(db,key)
                source=None;target=None
                try:
                    if field=='cost':
                        total=positive(x['cost_total'],'采购金额')
                        unit=total/quantity
                        if unit!=unit.quantize(D('.00000001')):raise ValueError('采购总额分摊超出精度，请人工核对')
                        source=str(total.normalize());target=str(unit)
                        current=current_cost;origin=line.get('purchase_origin','')
                    else:
                        match=re.fullmatch(r'(\d+(?:\.\d+)?)\s*(?:g|克)',x['note'],re.I)
                        if not match:raise ValueError('未读取到独立的数字＋g/克；重量保留，待补称重')
                        value=positive(match[1],'整包重量')
                        if value>25000:raise ValueError('重量超过25kg，请人工核实')
                        if len(order['lines'])!=1 or order.get('dispatch_plan'):
                            raise ValueError('混款或已有拆包方案：整包重量需在订单详情核对')
                        if order['inputs'].get('weight_origin')=='shape_changed':raise ValueError('订单组成已变化，重量需在详情重新确认')
                        source=target=str(value.normalize());current=current_weight;origin=order['inputs'].get('weight_origin','')
                except ValueError as exc:
                    _seen(db,key,x['cost_total'] if field=='cost' else x['note'],'waiting',now)
                    e['notes'].append(dict(field=field,text=str(exc),status='waiting'));continue
                _seen(db,key,source,'review',now)
                if current is not None and D(target)==current:
                    _baseline(db,key,source,str(current),now)
                    e['notes'].append(dict(field=field,text='与当前值相同，已核对',status='same'));continue
                if mode=='incremental' and old and old['source_value']==source:
                    _seen(db,key,source,'local_changed',now)
                    e['notes'].append(dict(field=field,text='妙手数据未变，保留系统当前值',status='same'));continue
                local_changed=old and old['target_value'] is not None and (current is None or current!=D(old['target_value']))
                conflict=bool(local_changed or (not old or old['target_value'] is None) and current is not None and origin not in ('inherited','scaled',''))
                cid=f'{index}:{field}'
                e['changes'].append(dict(id=cid,field=field,line_id=line['id'],old=e['current_cost'] if field=='cost' else e['current_weight'],
                    old_raw=str(current) if current is not None else None,new=source,value=target,key=list(key),
                    conflict=conflict,checked=not conflict,label='采购合计' if field=='cost' else '整包实称'))
                if conflict:_seen(db,key,source,'conflict',now)
        except ValueError as exc:
            e['error']=str(exc);e['changes']=[]
            # Even unmatched orders stay visible for follow-up; only configured shop names are allowed.
            names=[s for s in shops if s['name'].casefold()==x['shop_name'].casefold()]
            if len(names)==1:
                for field in ('cost','weight'):_seen(db,(names[0]['id'],x['posting'],product,field),x['cost_total'] if field=='cost' else x['note'],'unmatched',now)
    # Recalculate each affected whole order from the preview values only.
    for e in entries:
        if e['error'] or not e['changes']:continue
        o=core.load_order(db,e['order_id']);e['new_profit']=project_order(o,e['changes'])['profit']
    rid=secrets.token_hex(16)
    p=dict(id=rid,mode=mode,entries=entries,created=now,expires=now+TTL)
    db.execute('INSERT INTO purchase_sync_previews VALUES(?,?,?,?,?)',(rid,owner,now,encode(p),'ready'))
    db.execute("DELETE FROM purchase_sync_previews WHERE created<?",(now-7*86400,))
    return p

def project_order(order,changes):
    o=copy.deepcopy(order)
    for c in changes:
        if c['field']=='cost':next(l for l in o['lines'] if l['id']==c['line_id'])['purchase_unit']=c['value']
        else:o['inputs'].update(weight_override=c['value'],weight_origin='extension')
    return core.calculate(o)

def _write(db,order,line,field,value,now,batch):
    if field=='cost':
        prior=db.execute('SELECT * FROM profit_purchase_sources WHERE line_id=?',(line,)).fetchone()
        db.execute('UPDATE profit_lines SET purchase_unit=?,updated=? WHERE order_id=? AND id=?',(value,now,order,line))
        db.execute("INSERT INTO profit_purchase_sources VALUES (?,'extension',NULL,?,NULL) ON CONFLICT(line_id) DO UPDATE SET origin='extension',source_line_id=NULL,recorded_at=excluded.recorded_at,source_purchase_unit=NULL",(line,now))
    else:
        prior=db.execute('SELECT * FROM profit_weight_sources WHERE order_id=?',(order,)).fetchone()
        old=db.execute('SELECT weight_override FROM profit_inputs WHERE order_id=?',(order,)).fetchone()
        db.execute('''INSERT INTO profit_inputs(order_id,weight_override,template,rate,forwarder,other_cost,updated)
          VALUES (?,?,'auto',NULL,'2','0',?) ON CONFLICT(order_id) DO UPDATE SET weight_override=excluded.weight_override,updated=excluded.updated''',(order,value,now))
        db.execute("INSERT INTO profit_weight_sources VALUES (?,'extension',NULL,?,NULL) ON CONFLICT(order_id) DO UPDATE SET origin='extension',source_order_id=NULL,recorded_at=excluded.recorded_at,source_weight_g=NULL",(order,now))
        db.execute('INSERT INTO profit_weight_audit(order_id,old_weight,new_weight,changed,source) VALUES(?,?,?,?,?)',(order,old[0] if old else None,value,now,encode(dict(channel='miaoshou-extension',batch=batch))))
    return dict(prior) if prior else None

def apply(db,rid,selected,owner,now=None):
    now=time.time() if now is None else now
    if not isinstance(selected,list) or not selected or any(not isinstance(x,str) for x in selected) or len(selected)!=len(set(selected)):
        raise ValueError('请选择有效变更')
    db.execute('BEGIN IMMEDIATE')
    row=db.execute('SELECT * FROM purchase_sync_previews WHERE id=? AND owner=?',(rid,owner)).fetchone()
    if not row or row['state']!='ready' or now-row['created']>TTL:raise ValueError('预览已过期或已处理，请重新读取')
    p=json.loads(row['payload']);allowed={c['id']:(e,c) for e in p['entries'] for c in e['changes'] if not e['error']}
    if not set(selected)<=set(allowed):raise ValueError('选择包含无效字段')
    orders={};audit=[]
    for cid in selected:
        e,c=allowed[cid];oid=e['order_id']
        o=orders.setdefault(oid,core.load_order(db,oid))
        if not o or fingerprint(o)!=e['fingerprint']:raise ValueError('订单数据已变化，请重新预览后确认')
    for cid in selected:
        e,c=allowed[cid];oid=e['order_id'];line=e['line_id'];before_source=_source(db,c['key'])
        before_meta=_write(db,oid,line,c['field'],c['value'],now,rid)
        _baseline(db,c['key'],c['new'],c['value'],now)
        audit.append(dict(order=oid,line=line,field=c['field'],old=c['old_raw'],new=c['value'],key=c['key'],before_meta=before_meta,before_source=before_source))
    after={str(oid):fingerprint(core.load_order(db,oid)) for oid in orders}
    db.execute('INSERT INTO purchase_sync_batches VALUES(?,?,?,?,?)',(rid,owner,now,encode(dict(changes=audit,after=after)),'applied'))
    db.execute("UPDATE purchase_sync_previews SET state='applied' WHERE id=?",(rid,))
    return dict(id=rid,orders=len(orders),fields=len(audit),undo_available=True)

def undo(db,rid,owner):
    db.execute('BEGIN IMMEDIATE')
    row=db.execute('SELECT * FROM purchase_sync_batches WHERE id=? AND owner=?',(rid,owner)).fetchone()
    if not row or row['state']!='applied':raise ValueError('该批次不能撤销')
    p=json.loads(row['payload'])
    for oid,expected in p['after'].items():
        o=core.load_order(db,int(oid))
        if not o or fingerprint(o)!=expected:raise ValueError('相关订单已被再次修改，不能直接撤销，请在详情核对')
    now=time.time()
    for c in p['changes']:
        if c['field']=='cost':
            db.execute('UPDATE profit_lines SET purchase_unit=?,updated=? WHERE id=? AND order_id=?',(c['old'],now,c['line'],c['order']))
            table,pk,identity='profit_purchase_sources','line_id',c['line']
        else:
            db.execute('UPDATE profit_inputs SET weight_override=?,updated=? WHERE order_id=?',(c['old'],now,c['order']))
            table,pk,identity='profit_weight_sources','order_id',c['order']
            db.execute('INSERT INTO profit_weight_audit(order_id,old_weight,new_weight,changed,source) VALUES(?,?,?,?,?)',(c['order'],c['new'],c['old'],now,encode(dict(channel='miaoshou-extension-undo',batch=rid))))
        db.execute('DELETE FROM '+table+' WHERE '+pk+'=?',(identity,))
        if c['before_meta']:
            columns=list(c['before_meta']);db.execute('INSERT INTO '+table+'('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')',[c['before_meta'][k] for k in columns])
        old=c['before_source'] or {}
        db.execute('UPDATE purchase_sync_fields SET source_value=?,target_value=?,applied=?,status=? WHERE shop=? AND posting=? AND product=? AND field=?',
          (old.get('source_value'),old.get('target_value'),old.get('applied'),'review',*c['key']))
    db.execute("UPDATE purchase_sync_batches SET state='undone' WHERE id=?",(rid,))
    return dict(ok=True,fields=len(p['changes']))

def register(app,settings,database,shops_fn):
    from flask import request,session,jsonify,send_from_directory,send_file
    root=Path(__file__).parent
    with core.connection(database) as db:schema(db)
    base='/ozon-profit/api/purchase-sync'
    def owner():return session.setdefault('purchase_sync_owner',secrets.token_hex(16))
    @app.get('/ozonzusou/purchase-sync/')
    def sync_page():return send_from_directory(root/'web','purchase-sync.html')
    @app.get('/ozonzusou/purchase-sync/download')
    def sync_download():
        import io,zipfile
        buf=io.BytesIO()
        folder=root/'extensions/miaoshou-sync'
        names=['manifest.json','reader.js','background.js','bridge.js','popup.html','popup.js','popup.css','README.md']
        with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
            for name in names:z.write(folder/name,'ozon-miaoshou-sync/'+name)
        buf.seek(0)
        return send_file(buf,mimetype='application/zip',as_attachment=True,download_name='ozon-miaoshou-sync-mvp.zip')
    @app.get('/ozonzusou/purchase-sync/assets/<name>')
    def sync_asset(name):
        if name not in ('purchase-sync.js','purchase-sync.css'):return '',404
        return send_from_directory(root/'web',name)
    @app.get(base+'/state')
    def sync_state():
        user=owner()
        with core.connection(database) as db:
            waiting=[dict(r) for r in db.execute("SELECT shop,posting,product,field,seen,status FROM purchase_sync_fields WHERE status IN ('waiting','unmatched','conflict','local_changed') ORDER BY seen ASC LIMIT 100")]
            names={s['id']:s['name'] for s in shops_fn(db)}
            history=[dict(id=r['id'],created=r['created'],state=r['state'],fields=len(json.loads(r['payload'])['changes'])) for r in db.execute('SELECT * FROM purchase_sync_batches WHERE owner=? ORDER BY created DESC LIMIT 10',(user,))]
        for x in waiting:x['shop_name']=names.get(x['shop'],x['shop'])
        return jsonify(csrf=session['csrf'],limit=LIMIT,waiting=waiting,history=history)
    @app.post(base+'/preview')
    def sync_preview():
        with core.connection(database) as db:value=preview(db,request.get_json(silent=True),owner(),shops_fn(db))
        # Internal fingerprints and database identities are not needed in the page.
        for e in value['entries']:
            e.pop('fingerprint',None)
        return jsonify(value)
    @app.post(base+'/apply')
    def sync_apply():
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or data.get('confirm') is not True:raise ValueError('请确认本次更新')
        with core.connection(database) as db:value=apply(db,data.get('id'),data.get('selected'),owner())
        return jsonify(value)
    @app.post(base+'/project')
    def sync_project():
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or not isinstance(data.get('selected'),list) or any(not isinstance(x,str) for x in data['selected']):raise ValueError('预览选择不正确')
        with core.connection(database) as db:
            row=db.execute('SELECT * FROM purchase_sync_previews WHERE id=? AND owner=?',(data.get('id'),owner())).fetchone()
            if not row or row['state']!='ready' or time.time()-row['created']>TTL:raise ValueError('预览已过期，请重新读取')
            p=json.loads(row['payload']);chosen=set(data['selected'])
            if not chosen<={c['id'] for e in p['entries'] for c in e['changes']}:raise ValueError('选择无效')
            orders={};result={}
            for e in p['entries']:
                if e['error']:continue
                oid=e['order_id']
                o=core.load_order(db,oid)
                if not o or fingerprint(o)!=e['fingerprint']:raise ValueError('订单数据已变化，请重新预览')
                orders[oid]=o
            for oid,o in orders.items():
                changes=[c for e in p['entries'] if e.get('order_id')==oid for c in e['changes'] if c['id'] in chosen]
                result[str(oid)]=project_order(o,changes)['profit']
        return jsonify(profits=result)
    @app.post(base+'/undo')
    def sync_undo():
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or data.get('confirm') is not True:raise ValueError('请确认撤销')
        with core.connection(database) as db:value=undo(db,data.get('id'),owner())
        return jsonify(value)
