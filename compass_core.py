"""Full-history operational aggregates; no writes or external requests."""
import datetime as dt
from decimal import Decimal as D
import profit_core as core

def build(db,shop='',date_from='',date_to='',search='',started='2026-10-24',shops=None):
    today=dt.datetime.now(core.CHINA).date()
    bounds=[]
    for value in (date_from,date_to):
        try:bounds.append(dt.datetime.strptime(value,'%Y-%m-%d').replace(tzinfo=core.CHINA) if value else None)
        except ValueError:raise ValueError('日期格式不正确') from None
    start,end=bounds
    if start and end and start>end:raise ValueError('开始日期不能晚于结束日期')
    query='SELECT id FROM profit_orders WHERE 1=1';args=[]
    if shop:query+=' AND shop=?';args.append(shop)
    if start:query+=' AND created>=?';args.append(start.timestamp())
    if end:query+=' AND created<?';args.append((end+dt.timedelta(days=1)).timestamp())
    orders=[core.load_order(db,r['id']) for r in db.execute(query+' ORDER BY created,id',args).fetchall()]
    q=search.strip().lower()
    if q:orders=[o for o in orders if q in ' '.join([o['shop_name'],o['posting'],o['order_number']]+[l['name']+' '+l.get('name_zh','')+' '+l['offer_id'] for l in o['lines']]).lower()]
    revenue=profit=ready_sales=D(0);ready=eligible_ready=0;order_keys=set();products={};days={};units=0
    stores={}
    for item in shops or []:
        if not shop or item['id']==shop:
            stores[item['id']]=dict(id=item['id'],key=item['id'],name=item.get('name',item['id']),quantity=set(),units=0,sales=D(0),profit=D(0),ready_sales=D(0),purchase=D(0),fees=D(0),ready=0,packages=0,orders=[])
    for o in orders:
        c=o['calculation'];complete=c['status']=='ready';ready+=int(complete)
        if not c['included_in_summary']:continue
        order_key=(o['shop'],o['order_number']);order_keys.add(order_key)
        store=stores.setdefault(o['shop'],dict(id=o['shop'],key=o['shop'],name=o['shop_name'],quantity=set(),units=0,sales=D(0),profit=D(0),ready_sales=D(0),purchase=D(0),fees=D(0),ready=0,packages=0,orders=[]))
        store['quantity'].add(o['order_number']);store['packages']+=c['packages']
        store['units']+=sum(l['quantity'] for l in o['lines']);store['sales']+=D(c['revenue'])
        if complete:
            store['ready']+=1;store['profit']+=D(c['profit']);store['ready_sales']+=D(c['revenue']);store['purchase']+=D(c['purchase_cost']);store['fees']+=D(c['revenue'])-D(c['purchase_cost'])-D(c['profit'])
        store['orders'].append(dict(id=o['id'],posting=o['posting'],created=o['created'],sales=c['revenue'],profit=c['profit'] if complete else None,status=o['status_label']))
        day=dt.datetime.fromtimestamp(o['created'],core.CHINA).date().isoformat()
        daily=days.setdefault(day,dict(orders=set(),sales=D(0),profit=D(0)))
        daily['orders'].add(order_key);sales=D(c['revenue']);revenue+=sales;daily['sales']+=sales
        if complete:
            eligible_ready+=1;package_profit=D(c['profit']);profit+=package_profit;ready_sales+=sales;daily['profit']+=package_profit
        line_sales=[D(l['price_override'] if l.get('price_override') is not None else (l['unit_price'] if l.get('currency')=='CNY' and l.get('unit_price') is not None else '0'))*l['quantity'] for l in o['lines']]
        costs=[D(l.get('purchase_unit') or '0')*l['quantity'] for l in o['lines']]
        logistics={key:D(value) for key,value in c['line_logistics'].items()}
        for key,value in c['line_platform_fees'].items():
            logistics[key]=logistics.get(key,D(0))+D(value)
        shared=sum(line_sales)-sum(costs)-D(c['profit'])-sum(map(D,logistics.values())) if complete else D(0)
        allocated=D(0);quantity=sum(l['quantity'] for l in o['lines'])
        for index,l in enumerate(o['lines']):
            key=o['shop']+'|'+(l['offer_id'] or l['sku'] or str(l['id']))
            p=products.setdefault(key,dict(key=key,shop=o['shop_name'],name=l.get('name_zh') or l['name'],image=l['image_url'],sku=l['sku'],offer=l['offer_id'],quantity=0,ready_quantity=0,sales=D(0),profit=D(0),ready_sales=D(0),purchase=D(0),fees=D(0),ready=0,orders=[]))
            units+=l['quantity'];p['quantity']+=l['quantity'];p['sales']+=line_sales[index]
            part=None;fee=None
            if complete:
                share=line_sales[index]/sales if sales else D(l['quantity'])/D(quantity)
                common=D(core.money(shared*share)) if index<len(o['lines'])-1 else shared-allocated
                allocated+=common
                fee=common+D(logistics.get(l['line_key'],'0'));part=line_sales[index]-costs[index]-fee
                p['profit']+=part;p['ready_sales']+=line_sales[index];p['purchase']+=costs[index];p['fees']+=fee;p['ready']+=1;p['ready_quantity']+=l['quantity']
            p['orders'].append(dict(id=o['id'],posting=o['posting'],created=o['created'],quantity=l['quantity'],sales=core.money(line_sales[index]),profit=core.money(part),status=o['status_label']))
    for p in products.values():
        p['unit_profit']=core.money(p['profit']/D(p['ready_quantity'])) if p['ready_quantity'] else None
        p['margin']=core.money(p['profit']/p['ready_sales']*100) if p['ready_sales'] else None
        for field in ('sales','profit','ready_sales','purchase','fees'):p[field]=core.money(p[field])
    first=(start.date() if start else (dt.date.fromisoformat(min(days)) if days else today));last=end.date() if end else today
    trend=[];cursor=first
    while cursor<=last:
        d=days.get(cursor.isoformat(),dict(orders=set(),sales=D(0),profit=D(0)))
        trend.append(dict(date=cursor.isoformat(),orders=len(d['orders']),sales=core.money(d['sales']),profit=core.money(d['profit'])));cursor+=dt.timedelta(days=1)
    for store in stores.values():
        store['quantity']=len(store['quantity'])
        store['margin']=core.money(store['profit']/store['ready_sales']*100) if store['ready_sales'] else None
        for field in ('sales','profit','ready_sales','purchase','fees'):store[field]=core.money(store[field])
    return dict(summary=dict(sales=core.money(revenue),profit=core.money(profit),margin=core.money(profit/ready_sales*100) if ready_sales else None,ready_sales=core.money(ready_sales),orders=len(order_keys),daily_average=core.money(D(len(order_keys))/D(max(1,len(trend)))),units=units,ready=ready,pending=len(orders)-ready,eligible_ready=eligible_ready,packages=sum(o['calculation']['packages'] for o in orders),operating_days=max(0,(today-dt.date.fromisoformat(started)).days+1)),products=list(products.values()),shops=list(stores.values()),trend=trend)
