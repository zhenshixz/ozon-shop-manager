"""Operations entry pages, sharing existing profit API session/CSRF protection."""
from pathlib import Path
from flask import jsonify,request,session,send_from_directory,redirect
from overweight import Service

ROOT=Path(__file__).resolve().parent
API='/ozon-profit/api/operations'

def register(app,settings,database):
    service=Service(settings.get('overweight_database'),settings.get('monitor_config',ROOT/'config.json'))
    app.extensions['overweight']=service

    @app.get('/ozonzusou')
    def ops_slash():return redirect('/ozonzusou/')

    @app.get('/ozonzusou/')
    @app.get('/ozonzusou/overweight/')
    def ops_page():return send_from_directory(ROOT/'web','operations.html')

    @app.get('/ozonzusou/assets/<name>')
    def ops_asset(name):
        if name not in ('operations.css','operations.js'):return '',404
        return send_from_directory(ROOT/'web',name)

    @app.get('/ozonzusou/promotions/')
    def ops_promotions():return send_from_directory(ROOT/'web','promotions.html')

    @app.get('/ozonzusou/profit/')
    def ops_profit():return send_from_directory(ROOT/'web','index.html')

    @app.get('/ozonzusou/compass/')
    def ops_compass():return redirect('/ozonzusou/profit/#compass')

    @app.get(API+'/state')
    def ops_state():
        value=service.view(request.args)
        value['csrf']=session['csrf']
        try:
            import sqlite3,time,datetime as dt
            from overweight import TZ
            start=dt.datetime.now(TZ).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
            with sqlite3.connect('file:'+str(database)+'?mode=ro',uri=True,timeout=.5) as source:
                value['new_products_today']=source.execute("SELECT COUNT(*) FROM events WHERE kind='product' AND created>=? AND created<=?",(start,time.time())).fetchone()[0]
        except (OSError,sqlite3.Error):value['new_products_today']=None
        try:
            import json
            value['digest_hour']=int(json.loads(service.config.read_text(encoding='utf-8-sig')).get('product_digest_hour',22))
        except (OSError,ValueError,KeyError):value['digest_hour']=22
        # Read existing promotion summary only; do not reconcile or start a job.
        try:
            import sqlite3,json
            with sqlite3.connect('file:'+str(ROOT/'data/promotions/state.sqlite3')+'?mode=ro',uri=True,timeout=.5) as db:
                db.row_factory=sqlite3.Row
                row=db.execute("SELECT value FROM settings WHERE owner='self'").fetchone()
                p=json.loads(row[0]) if row else {}
                last=db.execute("SELECT status,created FROM runs WHERE owner='self' ORDER BY created DESC LIMIT 1").fetchone()
            value['promotions']=dict(enabled=p.get('enabled',False),times=sorted(set([p.get('time','09:00')]+p.get('extra_times',[]))),last=dict(last) if last else None)
        except Exception:value['promotions']=dict(unavailable=True)
        return jsonify(value)

    @app.post(API+'/rule')
    def ops_rule():
        data=request.get_json(silent=True)
        if not isinstance(data,dict):raise ValueError('设置格式不正确')
        return jsonify(rule=service.save_rule(data.get('weight')))

    @app.post(API+'/scan')
    def ops_scan():
        data=request.get_json(silent=True)
        if not isinstance(data,dict):raise ValueError('请选择店铺')
        rid=service.enqueue(data.get('shops'));service.launch(rid)
        return jsonify(id=rid),202

    @app.post(API+'/archive')
    def ops_archive():
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or data.get('confirm') is not True:raise ValueError('请确认归档所选商品')
        selection=data.get('selection')
        if not isinstance(selection,list) or not selection or len(selection)>500:raise ValueError('单次请选择1—500款商品')
        shops=list(dict.fromkeys(x.get('shop') for x in selection if isinstance(x,dict)))
        rid=service.enqueue(shops,kind='archive',selection=selection);service.launch(rid)
        return jsonify(id=rid),202

    @app.post(API+'/retain')
    def ops_retain():
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or data.get('confirm') is not True:raise ValueError('请确认保留所选商品')
        service.retain(data.get('selection'))
        return jsonify(ok=True)
