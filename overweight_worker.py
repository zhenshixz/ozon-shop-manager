"""Run independent weight scans; no notification sending or automatic archiving."""
import argparse
import datetime as dt
import time
from overweight import ROOT, Service, TZ

def tick(service,now=None):
    now=time.time() if now is None else now
    # Scan before the existing product digest; keep scheduling separate from orders.
    import json
    hour=int(json.loads(service.config.read_text(encoding='utf-8-sig')).get('product_digest_hour',22))
    local=dt.datetime.fromtimestamp(now,TZ)
    due=local.replace(hour=hour,minute=0,second=0,microsecond=0)-dt.timedelta(minutes=15)
    if local<due:return None
    try:rid=service.enqueue(list(service.shops()),trigger='daily',now=now)
    except ValueError:return None
    if rid:service.execute(rid)
    return rid

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--job');parser.add_argument('--tick',action='store_true')
    parser.add_argument('--database');parser.add_argument('--config')
    args=parser.parse_args();service=Service(args.database,args.config)
    if args.job:service.execute(args.job)
    elif args.tick:tick(service)
