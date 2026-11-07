"""Explicit, asynchronous Seller API refresh for user-selected shops."""
import concurrent.futures
import json
import os
from pathlib import Path
import secrets
import threading
import time
import profit_core as core


class OrderSync:
    def __init__(self,database,config):
        self.database=database;self.config=config
        with core.connection(database) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS profit_manual_sync (
                id TEXT PRIMARY KEY,state TEXT NOT NULL,updated REAL NOT NULL,
                worker INTEGER NOT NULL,data TEXT NOT NULL)''')

    def status(self):
        with core.connection(self.database) as db:
            row=db.execute('SELECT * FROM profit_manual_sync ORDER BY updated DESC LIMIT 1').fetchone()
            if row and row['state']=='running':
                alive=True
                try:os.kill(row['worker'],0)
                except ProcessLookupError:alive=False
                except PermissionError:pass
                if not alive or time.time()-row['updated']>=900:
                    data=json.loads(row['data']);data['error']='上次同步已中断，请重新同步'
                    db.execute("UPDATE profit_manual_sync SET state='failed',data=? WHERE id=?",(json.dumps(data,ensure_ascii=False),row['id']))
                    row=db.execute('SELECT * FROM profit_manual_sync WHERE id=?',(row['id'],)).fetchone()
        return dict(id=row['id'],state=row['state'],**json.loads(row['data'])) if row else None

    def start(self,shops):
        with core.connection(self.database) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT * FROM profit_manual_sync WHERE state='running' LIMIT 1").fetchone()
            if row:
                alive=True
                try:os.kill(row['worker'],0)
                except ProcessLookupError:alive=False
                except PermissionError:pass
                if alive and time.time()-row['updated']<900:
                    return dict(id=row['id'],state=row['state'],**json.loads(row['data']))
                db.execute("UPDATE profit_manual_sync SET state='failed' WHERE id=?",(row['id'],))
            task=secrets.token_hex(12)
            data=dict(shops=shops,total=len(shops),completed=0,added=0,results=[],error='')
            db.execute('INSERT INTO profit_manual_sync VALUES (?,?,?,?,?)',
                       (task,'running',time.time(),os.getpid(),json.dumps(data,ensure_ascii=False)))
        threading.Thread(target=self.run,args=(task,data),daemon=True,name='manual-order-sync').start()
        return dict(id=task,state='running',**data)

    def update(self,task,data,state='running'):
        with core.connection(self.database) as db:
            db.execute('UPDATE profit_manual_sync SET state=?,updated=?,data=? WHERE id=?',
                       (state,time.time(),json.dumps(data,ensure_ascii=False),task))

    def run(self,task,data):
        import monitor
        import profit_sync
        try:
            cfg=monitor.load_config(self.config);store=monitor.Store(cfg['data_dir'])
            if Path(store.path).resolve()!=Path(self.database).resolve():raise ValueError('Database mismatch')
            configured={s['id']:s for s in cfg['shops']}
            transport=monitor.Transport(timeout=cfg['http_timeout_seconds'])
            def sync(selected):
                shop=configured.get(selected['id']);errors=[]
                if not shop:return dict(**selected,added=0,succeeded=0,errors=['店铺已停用或未配置'])
                def keys():
                    with store.connection() as db:
                        return {r[0] for r in db.execute('SELECT DISTINCT order_number FROM profit_orders WHERE shop=?',(shop['id'],))}
                before=keys();api=monitor.Ozon(shop,transport,cfg['max_pages'])
                for source in ('fbs','fbo'):
                    try:profit_sync.refresh_orders(store,api,shop,source,force=True)
                    except Exception as exc:
                        message=str(exc) if isinstance(exc,monitor.MonitorError) else '同步失败，请稍后重试'
                        errors.append(source.upper()+'：'+message)
                return dict(**selected,added=len(keys()-before),succeeded=2-len(errors),errors=errors)
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(3,len(data['shops']))) as pool:
                jobs=[pool.submit(sync,shop) for shop in data['shops']]
                for future in concurrent.futures.as_completed(jobs):
                    result=future.result()
                    data['results'].append(result);data['completed']+=1;data['added']+=result['added']
                    self.update(task,data)
            failures=sum(bool(r['errors']) for r in data['results'])
            self.update(task,data,'failed' if not sum(r['succeeded'] for r in data['results']) else 'partial' if failures else 'completed')
        except Exception:
            data['error']='同步中断，请重试；若持续失败请检查服务器配置和日志'
            self.update(task,data,'failed')
