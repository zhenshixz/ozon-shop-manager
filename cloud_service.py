"""Windows launcher's server-side prerequisite and service checks (standard library)."""
import argparse
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT=Path('/opt/ozon-push-monitor')
RUNTIME=Path('/opt/ozon-profit-runtime/bin/python')
UNITS=['ozon-push-monitor','ozon-profit-web']
URL='https://voice.xzbest.site/ozon-profit/'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args()
    for name in ('monitor.py','profit_core.py','profit_sync.py','profit_web.py','config.json','profit-web.json','web/index.html'):
        if not (ROOT/name).is_file():raise RuntimeError('Missing server file: '+name)
    if not RUNTIME.is_file():raise RuntimeError('Profit Python runtime is missing')
    subprocess.run([str(RUNTIME),'-B','-c','import flask, gunicorn'],check=True,timeout=15)
    if not args.check:subprocess.run(['systemctl','start',*UNITS],check=True,timeout=40)
    for unit in UNITS:
        subprocess.run(['systemctl','is-active','--quiet',unit],check=True,timeout=10)
        print(unit+': running',flush=True)
    with urllib.request.urlopen(URL,timeout=15) as response:
        if response.status!=200:raise RuntimeError('Profit page returned HTTP '+str(response.status))
    print('Local service: http://127.0.0.1:5310/ozon-profit/')
    print('Public service: '+URL)
    print('Cloud public IP: 8.138.252.219')
    print('Cloud LAN IP: '+subprocess.check_output(['hostname','-I'],text=True).strip())
    print('CLOUD_SERVICE_OK',flush=True)


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('Service check failed: '+str(exc),file=sys.stderr);sys.exit(1)
