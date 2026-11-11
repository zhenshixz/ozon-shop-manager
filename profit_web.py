"""Small authenticated phone UI for the existing monitor's profit estimates."""
import collections
import datetime as dt
import hmac
import json
import os
from pathlib import Path
import secrets
import time
from flask import Flask, request, session, jsonify, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix
import profit_core as core
import recent_sales
import parcel_comparison
import product_status

PREFIX = '/ozon-profit'
ROOT = Path(__file__).parent

def create_app(settings=None):
    settings = settings or json.loads(Path(os.environ.get('PROFIT_SETTINGS', ROOT/'profit-web.json')).read_text())
    app = Flask(__name__, static_folder=None)
    app.config.update(SECRET_KEY=settings['secret_key'], SESSION_COOKIE_NAME='ozon_profit',
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SECURE=settings.get('secure_cookie',True),
        SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_PATH=PREFIX,
        PERMANENT_SESSION_LIFETIME=dt.timedelta(days=30), MAX_CONTENT_LENGTH=32768)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=0)
    database = settings.get('database',str(ROOT/'data'/'monitor.sqlite3'))
    try:
        with core.connection(database) as db:
            core.ensure_schema(db)
            core.inherit_measured_weights(db)
    except Exception:
        pass
    failures = collections.deque(maxlen=100)
    configured_shops=settings.get('shops')
    if configured_shops is None:
        try:
            config=json.loads((ROOT/'config.json').read_text(encoding='utf-8-sig'))
            configured_shops=[{'id':s['id'],'name':s['name']} for s in config['shops'] if s.get('enabled',True)]
        except (OSError,ValueError,KeyError,TypeError):
            configured_shops=[]

    def available_shops(db):
        return configured_shops or [dict(id=r['shop'],name=r['shop_name']) for r in db.execute(
            'SELECT shop,MAX(shop_name) AS shop_name FROM profit_orders GROUP BY shop ORDER BY shop')]

    @app.after_request
    def headers(response):
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    import order_sync
    manual_sync=order_sync.OrderSync(database,settings.get('monitor_config',ROOT/'config.json'))

    @app.route(PREFIX+'/api/order-sync',methods=['GET','POST'])
    def sync_orders():
        if request.method=='GET':return jsonify(task=manual_sync.status())
        payload=request.get_json(silent=True)
        ids=payload.get('shops') if isinstance(payload,dict) else None
        if not isinstance(ids,list) or not ids or len(ids)>100 or any(not isinstance(i,str) for i in ids):
            raise ValueError('请选择需要同步的店铺')
        with core.connection(database) as db:shops=available_shops(db)
        selected=[s for s in shops if s['id'] in ids]
        if set(ids)!={s['id'] for s in selected}:raise ValueError('包含未配置的店铺，请刷新后重选')
        return jsonify(task=manual_sync.start(selected)),202

    @app.before_request
    def authorize():
        if not request.path.startswith(PREFIX+'/api/'): return
        if request.path == PREFIX+'/api/login': return
        if settings.get('public_access',False):
            session.setdefault('csrf',secrets.token_urlsafe(24))
        elif not session.get('authorized'): return jsonify(error='请从推送里的链接打开页面'),401
        if request.method=='POST':
            if not hmac.compare_digest(request.headers.get('X-CSRF-Token',''), session.get('csrf','!')):
                return jsonify(error='页面已过期，请刷新'),403
            origin=request.headers.get('Origin')
            if origin and origin!=request.host_url.rstrip('/'):
                return jsonify(error='请求来源不正确'),403

    @app.errorhandler(ValueError)
    def invalid(error): return jsonify(error=str(error)),400

    @app.errorhandler(413)
    def large(error): return jsonify(error='填写内容过长'),413

    @app.get(PREFIX+'/')
    def index(): return send_from_directory(ROOT/'web','index.html')

    @app.get(PREFIX+'/assets/<name>')
    def assets(name):
        if name not in ('app.js','style.css','compass.js','compass.css'): return '',404
        return send_from_directory(ROOT/'web',name)

    @app.post(PREFIX+'/api/login')
    def login():
        now=time.monotonic()
        while failures and failures[0]<now-60: failures.popleft()
        if len(failures)>=20: return jsonify(error='打开次数过多，请稍后重试'),429
        payload=request.get_json(silent=True) or {}
        token=payload.get('token','') if isinstance(payload,dict) else ''
        if not isinstance(token,str) or not hmac.compare_digest(token,settings['access_token']):
            failures.append(now)
            return jsonify(error='访问链接无效，请使用最新的推送链接'),401
        session.clear(); session['authorized']=True; session['csrf']=secrets.token_urlsafe(24)
        session.permanent=True
        return jsonify(ok=True)

    @app.get(PREFIX+'/api/bootstrap')
    def bootstrap():
        with core.connection(database) as db:
            shops=[dict(shop=s['id'],shop_name=s['name']) for s in available_shops(db)]
        return jsonify(csrf=session['csrf'],shops=shops,templates=core.TEMPLATES)

    @app.get(PREFIX+'/api/statistics')
    def statistics():
        now=time.time()
        with core.connection(database) as db:
            result=core.order_statistics(db,available_shops(db),request.args.get('shop',''),now=now)
            month=core.order_statistics(db,available_shops(db),request.args.get('shop',''),days=30,now=now)
        return jsonify(statistics=result,statistics_30=month)

    @app.get(PREFIX+'/api/cost-statistics')
    def cost_statistics():
        with core.connection(database) as db:
            result=core.purchase_statistics(db,available_shops(db),request.args.get('shop',''),
                request.args.get('date_from',''),request.args.get('date_to',''))
            details=None
            if request.args.get('details')=='1':
                raw=request.args.get('offset','0')
                if not raw.isdigit() or len(raw)>8:raise ValueError('分页参数不正确')
                details=core.purchase_order_details(db,available_shops(db),request.args.get('shop',''),
                    result['date_from'],result['date_to'],int(raw))
        return jsonify(statistics=result,details=details)

    @app.get(PREFIX+'/api/images')
    def images():
        raw=request.args.get('ids','').split(',')
        if len(raw)>500 or any(not item.isdigit() or len(item)>18 for item in raw):
            raise ValueError('商品编号不正确')
        ids=list(dict.fromkeys(int(item) for item in raw))
        with core.connection(database) as db:
            rows=db.execute('SELECT id,image_url FROM profit_lines WHERE id IN ('+
                ','.join('?' for _ in ids)+')',ids).fetchall()
        return jsonify(images=[dict(row) for row in rows])

    @app.get(PREFIX+'/api/orders')
    def orders():
        query='SELECT id FROM profit_orders WHERE 1=1'
        args=[]
        if request.args.get('shop'):
            query+=' AND shop=?'; args.append(request.args['shop'])
        if request.args.get('date'):
            try:
                start=dt.datetime.strptime(request.args['date'],'%Y-%m-%d').replace(tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp()
            except ValueError: raise ValueError('日期格式不正确') from None
            query+=' AND created>=? AND created<?'; args.extend([start,start+86400])
        query+=' ORDER BY created DESC,id DESC LIMIT 500'
        with core.connection(database) as db:
            result=[core.load_order(db,r['id']) for r in db.execute(query,args).fetchall()]
            recent_sales.annotate(db,result)
        search=request.args.get('q','').lower().strip()
        if search:
            result=[o for o in result if search in ' '.join([o['posting'],o['order_number'],o['shop_name']]+[l['name']+' '+l.get('name_zh','')+' '+l['offer_id'] for l in o['lines']]).lower()]
        state=request.args.get('state','')
        if state: result=[o for o in result if o['calculation']['status']==state]
        product_status.annotate_orders(result)
        return jsonify(orders=result,limit=500)

    @app.route(PREFIX+'/api/orders/<int:order_id>',methods=['GET','POST'])
    @app.post(PREFIX+'/api/orders/<int:order_id>/preview')
    def detail(order_id):
        synced=None
        trial_weights=None
        with core.connection(database) as db:
            order=core.load_order(db,order_id)
            if not order: return jsonify(error='订单不存在'),404
            if request.method=='POST':
                payload=request.get_json(silent=True)
                preview=request.path.endswith('/preview')
                order=core.apply_inputs(order,payload,require_confirmation=not preview)
                trial_weights=payload.get('parcel_weights')
                if 'dispatch_choice' in payload:
                    order['dispatch_plan']=parcel_comparison.select(db,order,payload['dispatch_choice'],trial_weights,preview)
                    order['calculation']=core.calculate(order)
                    if preview and payload['dispatch_choice']>0 and order['dispatch_plan'] is None:
                        order['calculation'].update(dispatch_pending=True,status='incomplete',profit=None,
                            profit_exact=None,sales_margin=None,cost_margin=None,shipping=None,
                            shipping_exact=None,forwarder=None)
                if not preview:
                    audit_base=dict(channel='web-save',session=session.setdefault('audit_id',secrets.token_hex(8)),
                        request_id=secrets.token_hex(8),user_agent=request.user_agent.string[:160],
                        weight_mode=payload.get('weight_mode'),api_weight_g=order['calculation']['api_weight_g'])
                    audit=json.dumps(audit_base,ensure_ascii=True)
                    core.save_order(db,order,source=audit)
                    if 'dispatch_choice' in payload or order.get('dispatch_plan'):
                        parcel_comparison.save(db,order)
                    if isinstance(payload.get('batch_sync'),dict):
                        batch_audit=json.dumps({**audit_base,'channel':'web-batch-sync','source_order_id':order_id},ensure_ascii=True)
                        synced=core.batch_sync_from_order(db,order,payload['batch_sync'],source=batch_audit)
            core.enrich_order_sync_info(db,order)
            saved_weights=(order.get('dispatch_plan') or {}).get('weights')
            order['parcel_comparison']=parcel_comparison.compare(db,order,trial_weights if trial_weights is not None else saved_weights)
        product_status.annotate_orders([order])
        return jsonify(order=order,synced=synced)

    @app.get(PREFIX+'/api/analytics')
    def analytics():
        import compass_core
        with core.connection(database) as db:
            result=compass_core.build(db,request.args.get('shop',''),request.args.get('from',''),request.args.get('to',''),request.args.get('q',''),settings.get('operating_start_date','2026-10-24'),shops=available_shops(db))
        product_status.annotate_products(result['products'])
        return jsonify(result)

    import promotions
    promotions.register(app)
    @app.get(PREFIX+'/api/product-status')
    def status_snapshots():
        allowed={s['id'] for s in configured_shops}
        return jsonify(statuses={json.dumps(k):v for k,v in product_status.snapshots().items() if k[0] in allowed})
    return app

if __name__=='__main__':
    create_app().run(host='127.0.0.1',port=5310,debug=False)
