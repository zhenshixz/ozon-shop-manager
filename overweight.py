"""Independent product-weight review storage and official API operations.

No order/profit database writes. Scans never archive products; archiving requires
an explicit immutable selection from the review page.
"""
import contextlib
import datetime as dt
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import time
from decimal import Decimal, InvalidOperation

ROOT = Path(__file__).resolve().parent
TZ = dt.timezone(dt.timedelta(hours=8))
UNITS = {'g': Decimal(1), 'kg': Decimal(1000), 'mg': Decimal('.001')}


def day(now=None):
    return dt.datetime.fromtimestamp(time.time() if now is None else now, TZ).strftime('%Y-%m-%d')


def grams(item):
    try:
        result = Decimal(str(item['weight'])) * UNITS[str(item['weight_unit']).lower()]
        if not result.is_finite() or result <= 0: raise ValueError()
        return float(result)
    except (KeyError, InvalidOperation, ValueError, TypeError):
        raise ValueError('API重量或单位不完整') from None


class Service:
    def __init__(self, database=None, config=None, api_factory=None):
        self.path = Path(database or ROOT/'data/overweight.sqlite3')
        self.config = Path(config or ROOT/'config.json')
        self.api_factory = api_factory
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS rules(id INTEGER PRIMARY KEY,weight INTEGER NOT NULL,
                  saved REAL NOT NULL,auto_day TEXT NOT NULL);
                INSERT INTO rules SELECT 1,2000,0,'1970-01-01' WHERE NOT EXISTS(SELECT 1 FROM rules);
                CREATE TABLE IF NOT EXISTS products(shop TEXT,pid TEXT,payload TEXT NOT NULL,
                  weight REAL NOT NULL,state TEXT NOT NULL,retained_weight REAL,
                  first_seen REAL NOT NULL,last_seen REAL NOT NULL,version INTEGER NOT NULL DEFAULT 1,
                  PRIMARY KEY(shop,pid));
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,kind TEXT NOT NULL,trigger TEXT NOT NULL,
                  day TEXT NOT NULL,threshold INTEGER NOT NULL,shops TEXT NOT NULL,selection TEXT NOT NULL,
                  state TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,result TEXT NOT NULL DEFAULT '[]');
                CREATE INDEX IF NOT EXISTS jobs_recent ON jobs(created DESC);
                CREATE TABLE IF NOT EXISTS observations(job TEXT,shop TEXT,pid TEXT,payload TEXT NOT NULL,
                  PRIMARY KEY(job,shop,pid));
                CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,job TEXT,shop TEXT,endpoint TEXT,
                  request TEXT,response TEXT,created REAL);
            ''')

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db: yield db
        finally: db.close()

    def shops(self):
        cfg = json.loads(self.config.read_text(encoding='utf-8-sig'))
        return {s['id']:s for s in cfg['shops'] if s.get('enabled',True)}

    def rule(self, now=None):
        date = day(now)
        with self.db() as db:
            manual = dict(db.execute('SELECT * FROM rules ORDER BY id DESC LIMIT 1').fetchone())
            auto = dict(db.execute('SELECT * FROM rules WHERE auto_day<=? ORDER BY id DESC LIMIT 1',(date,)).fetchone())
        return dict(manual=manual['weight'],automatic=auto['weight'],effective_day=manual['auto_day'],
                    pending=manual['id']!=auto['id'])

    def save_rule(self, value, now=None):
        if type(value) is not int or not 1<=value<=1000000:
            raise ValueError('请输入1—1000000之间的整数克重')
        now = time.time() if now is None else now
        tomorrow=(dt.datetime.fromtimestamp(now,TZ)+dt.timedelta(days=1)).strftime('%Y-%m-%d')
        with self.db() as db:
            db.execute('INSERT INTO rules(weight,saved,auto_day) VALUES(?,?,?)',(value,now,tomorrow))
        return self.rule(now)

    def enqueue(self, ids, trigger='manual', kind='scan', selection=None, now=None):
        now=time.time() if now is None else now
        available=self.shops()
        if not isinstance(ids,list) or not ids or len(ids)>100 or any(not isinstance(s,str) or s not in available for s in ids):
            raise ValueError('请选择有效店铺')
        ids=list(dict.fromkeys(ids))
        rule=self.rule(now)
        threshold=rule['automatic' if trigger=='daily' else 'manual']
        rid=secrets.token_hex(12)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            # A killed worker cannot leave a permanent lock in the web UI.
            db.execute("UPDATE jobs SET state='interrupted' WHERE state IN ('queued','running') AND updated<?",(now-1800,))
            if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running')").fetchone():
                raise ValueError('已有检查或归档任务运行中，请稍后再试')
            if trigger=='daily' and db.execute("SELECT 1 FROM jobs WHERE trigger='daily' AND day=?",(day(now),)).fetchone():
                return None
            if kind=='archive':
                if not isinstance(selection,list) or not selection or len(selection)>1000:
                    raise ValueError('请选择1—1000款待审核商品')
                keys=set()
                for item in selection:
                    if not isinstance(item,dict) or item.get('shop') not in ids or not isinstance(item.get('pid'),str) or type(item.get('version')) is not int:
                        raise ValueError('商品选择格式不正确')
                    key=(item['shop'],item['pid'])
                    if key in keys: raise ValueError('包含重复商品')
                    keys.add(key)
                    row=db.execute('SELECT * FROM products WHERE shop=? AND pid=?',key).fetchone()
                    if not row or row['state']!='pending' or row['version']!=item['version']:
                        raise ValueError('商品状态已变化，请刷新列表后重新勾选')
            db.execute('INSERT INTO jobs(id,kind,trigger,day,threshold,shops,selection,state,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (rid,kind,trigger,day(now),threshold,json.dumps(ids),json.dumps(selection or []),'queued',now,now))
        return rid

    def launch(self, rid):
        if not rid:return
        try:
            with open(self.path.parent/'overweight-worker.log','ab') as output:
                subprocess.Popen([sys.executable,str(ROOT/'overweight_worker.py'),'--job',rid,
                                  '--database',str(self.path),'--config',str(self.config)],
                                 stdout=output,stderr=output,start_new_session=True)
        except OSError:
            with self.db() as db:db.execute("UPDATE jobs SET state='error' WHERE id=?",(rid,))
            raise ValueError('后台任务启动失败，请稍后重试') from None

    def retain(self, items):
        if not isinstance(items,list) or not 1<=len(items)<=1000:raise ValueError('请选择商品')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running')").fetchone():
                raise ValueError('请等待当前任务完成后再审核')
            for x in items:
                if not isinstance(x,dict) or not isinstance(x.get('shop'),str) or not isinstance(x.get('pid'),str) or type(x.get('version')) is not int:
                    raise ValueError('商品选择格式不正确')
                changed=db.execute("UPDATE products SET state='retained',retained_weight=weight,version=version+1 WHERE shop=? AND pid=? AND version=? AND state='pending'",
                    (x['shop'],x['pid'],x['version'])).rowcount
                if not changed:raise ValueError('商品状态已变化，请刷新后重选')

    def job(self,rid):
        with self.db() as db:row=db.execute('SELECT * FROM jobs WHERE id=?',(rid,)).fetchone()
        if not row:return None
        item=dict(row)
        for key in ('shops','selection','result'):item[key]=json.loads(item[key])
        return item

    def heartbeat(self,rid):
        with self.db() as db:db.execute('UPDATE jobs SET updated=? WHERE id=?',(time.time(),rid))

    def observe(self,rid,shop,item,weight,state,now=None):
        now=time.time() if now is None else now
        pid=str(item['id'])
        with self.db() as db:
            prior=db.execute('SELECT * FROM products WHERE shop=? AND pid=?',(shop,pid)).fetchone()
            if prior and state=='pending' and prior['state']=='retained' and prior['retained_weight']==weight:
                state='retained'
            changed=not prior or prior['weight']!=weight or prior['state']!=state
            data=dict(item,weight_g=weight,shop=shop,state=state)
            db.execute('''INSERT INTO products(shop,pid,payload,weight,state,first_seen,last_seen) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(shop,pid) DO UPDATE SET payload=excluded.payload,weight=excluded.weight,
                state=excluded.state,last_seen=excluded.last_seen,version=products.version+?''',
                (shop,pid,json.dumps(data,ensure_ascii=False),weight,state,now,now,int(changed)))
            db.execute('INSERT OR REPLACE INTO observations VALUES(?,?,?,?)',(rid,shop,pid,json.dumps(data,ensure_ascii=False)))
        return state,not prior or (prior['state'] in ('normal','archived','retained') and state=='pending')

    def _api(self,shop,transport):
        if self.api_factory:return self.api_factory(shop)
        from monitor import Ozon
        return Ozon(shop,transport)

    def execute(self,rid):
        from monitor import Transport, ApiError
        from concurrent.futures import ThreadPoolExecutor
        import threading
        job=self.job(rid)
        if not job or job['state']!='queued':return
        with self.db() as db:
            changed=db.execute("UPDATE jobs SET state='running',updated=? WHERE id=? AND state='queued'",(time.time(),rid)).rowcount
        if not changed:return
        stop=threading.Event()
        def pulse():
            while not stop.wait(10):self.heartbeat(rid)
        thread=threading.Thread(target=pulse,daemon=True);thread.start()
        transport=Transport(timeout=25)
        results=[]
        try:
            shops=self.shops()
            results=[dict(shop=sid,name=shops[sid]['name'],state='queued',checked=0,pending=0,new=0,archived=0,retained=0,unknown=0,errors=[]) for sid in job['shops']]
            publish_lock=threading.Lock()
            def publish():
                with publish_lock:
                    with self.db() as db:db.execute('UPDATE jobs SET result=?,updated=? WHERE id=?',(json.dumps(results),time.time(),rid))
            def run_shop(result):
                sid=result['shop'];result['state']='running';publish()
                try:
                    api=self._api(shops[sid],transport)
                    def read(path,payload):
                        for attempt in range(3):
                            try:return api.post(path,payload)
                            except ApiError as exc:
                                if attempt==2:raise
                                if float(exc.retry_after)>180:raise
                                time.sleep(max(float(exc.retry_after),2**(attempt+1)))
                    if job['kind']=='scan':self._scan(job,sid,api,read,result)
                    else:self._archive(job,sid,api,read,result)
                    result['state']='attention' if result['errors'] or result['unknown'] else 'done'
                except Exception as exc:
                    result['state']='error';result['errors'].append(str(exc)[:250])
                publish()
            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(run_shop,results))
            status='done' if all(r['state']=='done' for r in results) else 'attention'
            with self.db() as db:db.execute('UPDATE jobs SET state=?,updated=?,result=? WHERE id=?',(status,time.time(),json.dumps(results),rid))
        except Exception as exc:
            with self.db() as db:
                db.execute("UPDATE jobs SET state='error',updated=?,result=? WHERE id=?",(time.time(),json.dumps(results+[dict(name='任务',state='error',errors=[str(exc)[:250]])]),rid))
        finally:
            stop.set();thread.join(timeout=1)

    def _scan(self,job,sid,api,read,result):
        # Only read operations. Verify complete pagination via the existing client.
        products=api.products('ALL')
        with self.db() as db:known={r['pid']:dict(r) for r in db.execute('SELECT * FROM products WHERE shop=?',(sid,))}
        # ALL can omit archived cards. Recheck tracked-but-absent IDs explicitly
        # so archiving outside this tool cannot leave a permanent pending alert.
        absent=sorted(set(known)-set(products))
        for offset in range(0,len(absent),500):
            batch=absent[offset:offset+500]
            response=read('/v3/product/info/list',{'product_id':batch})
            details={str(x['id']):x for x in response.get('items',[])}
            for pid in batch:
                item=details.get(pid)
                if not item:
                    result['unknown']+=1;result['errors'].append(pid+'：历史商品状态暂未返回');continue
                if item.get('is_archived') or item.get('is_autoarchived'):
                    prior=known[pid]
                    self.observe(job['id'],sid,json.loads(prior['payload']),prior['weight'],'archived')
                    result['archived']+=1
                else:products[pid]={'product_id':int(pid)}
        for start in range(0,len(products),500):
            ids=list(products)[start:start+500]
            response=read('/v4/product/info/attributes',{'filter':{'product_id':[int(i) for i in ids],'visibility':'ALL'},'limit':1000})
            attributes={str(x['id']):x for x in response.get('result',[])}
            if set(attributes)!=set(ids):raise ValueError('商品属性返回不完整，请重试该店铺')
            candidates=[]; weights={}
            for pid,x in attributes.items():
                try:weight=grams(x)
                except ValueError:
                    result['unknown']+=1
                    result['errors'].append('重量未知：'+str(x.get('offer_id') or pid));continue
                result['checked']+=1
                weights[pid]=weight
                # Archived findings are retained in history, not the pending list.
                if weight>job['threshold'] or pid in known:candidates.append(pid)
            if not candidates:continue
            info=read('/v3/product/info/list',{'product_id':candidates})
            details={str(x['id']):x for x in info.get('items',[])}
            if set(details)!=set(candidates):raise ValueError('商品详情返回不完整，请重试该店铺')
            for pid,item in details.items():
                x=attributes[pid];weight=weights[pid]
                image=item.get('primary_image') or item.get('images') or []
                if isinstance(image,list):image=next((a for a in image if isinstance(a,str) and a.startswith('https://')),'')
                if not isinstance(image,str) or not image.startswith('https://'):image=''
                state='archived' if item.get('is_archived') or item.get('is_autoarchived') else 'pending' if weight>job['threshold'] else 'normal'
                payload=dict(id=pid,offer_id=item.get('offer_id') or x.get('offer_id',''),sku=item.get('sku') or x.get('sku') or 0,
                    name=item.get('name') or x.get('name',''),image=image,created_at=item.get('created_at',''),
                    shop_name=result['name'],api_weight=x['weight'],api_unit=x['weight_unit'],threshold=job['threshold'])
                state,new=self.observe(job['id'],sid,payload,weight,state)
                if state in result:result[state]+=1
                if new and state=='pending':result['new']+=1

    def _archive(self,job,sid,api,read,result):
        targets=[x for x in job['selection'] if x['shop']==sid]
        for start in range(0,len(targets),100):
            batch=targets[start:start+100];ids=[x['pid'] for x in batch]
            before=read('/v3/product/info/list',{'product_id':ids})
            details={str(x['id']):x for x in before.get('items',[])}
            attrs=read('/v4/product/info/attributes',{'filter':{'product_id':[int(i) for i in ids],'visibility':'ALL'},'limit':1000})
            weights={str(x['id']):grams(x) for x in attrs.get('result',[])}
            write_ids=[]; stored={}
            with self.db() as db:
                for x in batch:
                    row=db.execute('SELECT * FROM products WHERE shop=? AND pid=?',(sid,x['pid'])).fetchone()
                    if not row or row['version']!=x['version'] or row['state']!='pending':
                        result['errors'].append(x['pid']+'：审核版本已改变');continue
                    item=json.loads(row['payload']);fresh=details.get(x['pid'])
                    if not fresh or fresh.get('offer_id')!=item['offer_id'] or weights.get(x['pid'])!=row['weight']:
                        result['errors'].append(item['offer_id']+'：商品或重量已变化，请重新检查');continue
                    if row['weight']<=job['threshold']:
                        result['errors'].append(item['offer_id']+'：已不超过当前标准，请重新检查');continue
                    stored[x['pid']]=(item,row['weight'])
                    if not fresh.get('is_archived'):write_ids.append(int(x['pid']))
            if write_ids:
                request={'product_id':write_ids}
                # Persist intent before sending; a failed or timed-out write is never blindly replayed.
                with self.db() as db:
                    cursor=db.execute('INSERT INTO audit(job,shop,endpoint,request,response,created) VALUES(?,?,?,?,?,?)',
                        (job['id'],sid,'/v1/product/archive',json.dumps(request),json.dumps({'before':before}),time.time()))
                    aid=cursor.lastrowid
                try:reply=api.post('/v1/product/archive',request)
                except Exception as exc:reply={'error':str(exc)[:250]}
                with self.db() as db:db.execute('UPDATE audit SET response=? WHERE id=?',(json.dumps({'before':before,'reply':reply}),aid))
            if not stored:continue
            after={}
            for delay in (1,3,8):
                if not self.api_factory:time.sleep(delay)
                value=read('/v3/product/info/list',{'product_id':list(stored)})
                after={str(x['id']):x for x in value.get('items',[])}
                if all(after.get(pid,{}).get('is_archived') is True for pid in stored):break
            for pid,(item,weight) in stored.items():
                result['checked']+=1
                if after.get(pid,{}).get('is_archived') is True:
                    self.observe(job['id'],sid,item,weight,'archived');result['archived']+=1
                else:result['errors'].append(item['offer_id']+'：尚未确认归档，请检查后重试')
            with self.db() as db:db.execute('INSERT INTO audit(job,shop,endpoint,request,response,created) VALUES(?,?,?,?,?,?)',
                (job['id'],sid,'archive-verification',json.dumps(list(stored)),json.dumps(value),time.time()))

    def view(self,args=None):
        args=args or {}; where=['1=1'];values=[]
        for key,col in (('shop','shop'),('state','state')):
            if args.get(key):where.append(col+'=?');values.append(args[key])
        if args.get('date'):
            try:dt.date.fromisoformat(args['date'])
            except (ValueError,TypeError):raise ValueError('日期格式不正确') from None
            where.append('EXISTS(SELECT 1 FROM observations o JOIN jobs j ON j.id=o.job WHERE o.shop=products.shop AND o.pid=products.pid AND j.day=?)');values.append(args['date'])
        if args.get('q'):
            where.append("(payload LIKE ? OR pid LIKE ?)");values.extend(['%'+str(args['q'])[:200]+'%']*2)
        try:page=max(1,min(100000,int(args.get('page',1))))
        except (ValueError,TypeError):raise ValueError('页码格式不正确') from None
        with self.db() as db:
            db.execute("UPDATE jobs SET state='interrupted' WHERE state IN ('queued','running') AND updated<?",(time.time()-1800,))
            count=db.execute('SELECT COUNT(*) FROM products WHERE '+' AND '.join(where),values).fetchone()[0]
            rows=db.execute('SELECT * FROM products WHERE '+' AND '.join(where)+' ORDER BY first_seen DESC,shop,pid LIMIT 50 OFFSET ?',values+[(page-1)*50]).fetchall()
            counts={r['state']:r['n'] for r in db.execute('SELECT state,COUNT(*) n FROM products GROUP BY state')}
            recent=[r['id'] for r in db.execute('SELECT id FROM jobs ORDER BY created DESC LIMIT 20')]
        items=[dict(json.loads(r['payload']),pid=r['pid'],state=r['state'],version=r['version'],first_seen=r['first_seen'],last_seen=r['last_seen']) for r in rows]
        jobs=[{k:v for k,v in self.job(r).items() if k!='selection'} for r in recent]
        return dict(items=items,total=count,page=page,page_size=50,counts=counts,rule=self.rule(),
                    shops=[dict(id=s['id'],name=s['name']) for s in self.shops().values()],jobs=jobs,now=time.time())


def digest_lines(database, cutoff):
    """Bounded local read only; never scan APIs or block order pushes here."""
    path=Path(database)
    if not path.exists():return []
    url='https://voice.xzbest.site/ozonzusou/overweight/'
    try:
        with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=.5) as db:
            db.row_factory=sqlite3.Row
            row=db.execute("SELECT * FROM jobs WHERE trigger='daily' AND day=? ORDER BY created DESC LIMIT 1",(day(cutoff),)).fetchone()
            if not row:return ['', '超重检查：今日尚未完成，请打开排查页查看。',url]
            if row['state'] in ('queued','running'):
                return ['', '超重检查仍在进行，结果请在排查页查看。',url]
            results=json.loads(row['result'])
            new=sum(r.get('new',0) for r in results)
            pending=db.execute("SELECT COUNT(*) FROM products WHERE state='pending' AND weight>?",(row['threshold'],)).fetchone()[0]
            archived=db.execute("SELECT COUNT(*) FROM observations WHERE job=? AND json_extract(payload,'$.state')='archived'",(row['id'],)).fetchone()[0]
            lines=['',f"超重检查｜API单件重量 > {row['threshold']}g",f'本次新增 {new} 款 · 仍待审核 {pending} 款 · 已归档历史 {archived} 款']
            failed=[r['name'] for r in results if r.get('state')!='done']
            if failed:lines.append('检查未完整完成：'+'、'.join(failed))
            if row['state']=='interrupted':lines.append('本轮检查中断，请重试。')
            return lines+['查看并审核归档：',url]
    except (OSError,sqlite3.Error,ValueError,KeyError):
        return ['', '超重检查结果暂不可用，请打开排查页查看。',url]
