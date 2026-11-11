"""Local-only import rehearsal. Never opens a database or calls production APIs."""
import argparse
import copy
from decimal import Decimal as D, InvalidOperation
import importlib.util
import io
import json
from pathlib import Path
import re
import sys
import uuid
import xml.etree.ElementTree as E
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
HEADERS = ['店铺名称', '订单编号', '平台SKU', '产品数量', '采购金额(产品-RMB)', 'SKU ID', '本地备注']

def decimal(value, positive=False):
    try:
        n = D(str(value).strip())
    except InvalidOperation:
        raise ValueError('金额或数量不是有效数字') from None
    if not n.is_finite() or n < 0 or n > 10000000 or (positive and n == 0):
        raise ValueError('金额或数量超出范围')
    return n

def read_xlsx(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if sum(i.file_size for i in z.infolist()) > 20_000_000:
            raise ValueError('表格解压后过大')
        shared = []
        if 'xl/sharedStrings.xml' in z.namelist():
            shared = [''.join(t.text or '' for t in x.findall('.//s:t', NS)) for x in E.fromstring(z.read('xl/sharedStrings.xml'))]
        workbook = E.fromstring(z.read('xl/workbook.xml'))
        sheets = workbook.findall('s:sheets/s:sheet', NS)
        if len(sheets) != 1:
            raise ValueError('首版请使用只有一个工作表的导出文件')
        rid = sheets[0].get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        rels = E.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        target = next(x.get('Target') for x in rels if x.get('Id') == rid)
        path = target.lstrip('/') if target.startswith('/') else 'xl/' + target
        rows = []
        for row in E.fromstring(z.read(path)).findall('s:sheetData/s:row', NS):
            cells = {}
            for c in row.findall('s:c', NS):
                if c.find('s:f', NS) is not None:
                    raise ValueError('请导出原始数值，暂不接受公式单元格')
                v = c.find('s:v', NS)
                value = v.text if v is not None else ''
                if c.get('t') == 's': value = shared[int(value)]
                if c.get('t') == 'inlineStr': value = ''.join(t.text or '' for t in c.findall('.//s:t', NS))
                col = re.match(r'[A-Z]+', c.get('r')).group()
                cells[col] = str(value or '').strip()
            if any(cells.values()): rows.append((int(row.get('r')), cells))
        if not rows or len(rows) > 2001: raise ValueError('表格为空或超过 2000 行')
        mapping = {col: value for col, value in rows[0][1].items()}
        if len(set(mapping.values())) != len(mapping): raise ValueError('存在重复列名')
        if not set(HEADERS).issubset(mapping.values()): raise ValueError('缺少列：' + '、'.join(set(HEADERS) - set(mapping.values())))
        return [dict(row=n, **{header: cells.get(col, '') for col, header in mapping.items()}) for n, cells in rows[1:]]

class Sandbox:
    def __init__(self, snapshot, calculate):
        self.initial = copy.deepcopy(snapshot['orders'])
        self.orders = copy.deepcopy(self.initial)
        self.calculate = calculate
        self.version = 0
        self.previews = {}
        self.history = []

    def state(self):
        return dict(orders=self.orders, version=self.version, history=[x['summary'] for x in self.history], sandbox=True)

    def preview(self, records):
        entries, used = [], {}
        by_offer, by_sku = {}, {}
        for o in self.orders:
            for l in o['lines']:
                by_offer.setdefault((o['shop_name'], o['posting'], l['offer_id']), []).append((o, l))
                by_sku.setdefault((o['shop_name'], o['posting'], l['sku']), []).append((o, l))
        for record in records:
            e = dict(row=record['row'], changes=[], notes=[], error='', source=record)
            entries.append(e)
            try:
                lookup = by_offer if record['平台SKU'] else by_sku
                matches = lookup.get((record['店铺名称'], record['订单编号'], record['平台SKU'] or record['SKU ID']), [])
                if len(matches) != 1: raise ValueError('店铺＋订单＋商品无法唯一匹配')
                o, l = matches[0]
                e.update(order_id=o['id'], line_id=l['id'], shop=o['shop_name'], posting=o['posting'],
                         name=l.get('name_zh') or l['name'], offer_id=l['offer_id'], sku=l['sku'],
                         quantity=l['quantity'], image=l.get('image_url', ''), old_profit=o['calculation']['profit'],
                         current_cost=str(D(l['purchase_unit']) * l['quantity']) if l.get('purchase_unit') is not None else None,
                         current_weight=o['calculation']['weight_g'])
                if record['SKU ID'] and record['SKU ID'] != l['sku']: raise ValueError('货号与 SKU ID 指向不一致')
                if decimal(record['产品数量'], True) != D(l['quantity']): raise ValueError('导出件数与当前订单不一致')
                key = (o['id'], l['id'])
                if key in used:
                    used[key]['error'] = '同一店铺／订单／商品在文件中重复，需整理后重试'
                    used[key]['changes'] = []
                    raise ValueError('同一店铺／订单／商品在文件中重复，需整理后重试')
                used[key] = e
                value = record['采购金额(产品-RMB)'].strip()
                if value:
                    total = decimal(value, True)
                    unit = total / D(l['quantity'])
                    if unit != unit.quantize(D('.00000001')): raise ValueError('采购总额无法精确分摊为单价，需人工处理')
                    old = None if l.get('purchase_unit') is None else D(l['purchase_unit']) * l['quantity']
                    if old != total:
                        e['changes'].append(dict(id=f"{o['id']}:{l['id']}:cost", field='cost', old=str(old) if old is not None else None,
                            new=str(total), value=str(unit), origin=l.get('purchase_origin', ''), label='采购合计'))
                    else: e['notes'].append('采购金额相同')
                else: e['notes'].append('采购金额为空，保留原值')
                note = record['本地备注'].strip()
                m = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(?:g|克)', note, re.I)
                if m:
                    weight = decimal(m[1], True)
                    if weight > 25000: raise ValueError('重量超过 25 千克，需单独核实')
                    if len(o['lines']) > 1:
                        e['notes'].append('多种商品共用整包重量，首版需人工核实，重量暂不导入')
                    elif o.get('dispatch_plan'):
                        e['notes'].append('已有拆包方案，整包重量无法代替各包实称，重量暂不导入')
                    elif D(o['calculation']['weight_g'] or '0') != weight or o['inputs'].get('weight_override') is None:
                        e['changes'].append(dict(id=f"{o['id']}:weight", field='weight', old=o['calculation']['weight_g'],
                            new=str(weight), value=str(weight), origin=o['inputs'].get('weight_origin') or 'api', label='整包实称'))
                    else: e['notes'].append('整包实称相同')
                elif note: e['notes'].append('备注不是独立的“数字＋g/克”，重量未识别')
                else: e['notes'].append('备注为空，保留重量')
            except (ValueError, InvalidOperation) as error:
                e['error'] = str(error)
                e['changes'] = []
        token = uuid.uuid4().hex
        p = dict(token=token, version=self.version, entries=entries)
        self.previews = {token: p}
        return p

    def prepare(self, token, selected):
        p = self.previews.get(token)
        if not p or p['version'] != self.version: raise ValueError('数据已变化，请重新预览')
        allowed = {c['id'] for e in p['entries'] for c in e['changes'] if not e['error']}
        selected = set(selected)
        if not selected or not selected.issubset(allowed): raise ValueError('请选择有效变更')
        originals = {o['id']: o for o in self.orders}
        updated, fields = {}, 0
        for e in p['entries']:
            for c in e['changes']:
                if c['id'] not in selected: continue
                if e['order_id'] not in updated:
                    updated[e['order_id']] = copy.deepcopy(originals[e['order_id']])
                o = updated[e['order_id']]
                if c['field'] == 'cost':
                    l = next(l for l in o['lines'] if l['id'] == e['line_id'])
                    l.update(purchase_unit=c['value'], purchase_origin='imported')
                else: o['inputs'].update(weight_override=c['value'], weight_origin='manual')
                fields += 1
        for o in updated.values(): o['calculation'] = self.calculate(o)
        return updated, fields

    def project(self, token, selected):
        updated, _ = self.prepare(token, selected)
        return dict(orders=list(updated.values()))

    def apply(self, token, selected):
        updated, fields = self.prepare(token, selected)
        before = {o['id']: o for o in self.orders if o['id'] in updated}
        self.orders = [updated.get(o['id'], o) for o in self.orders]
        self.version += 1
        summary = dict(orders=len(updated), fields=fields, version=self.version, order_ids=list(updated))
        self.history.append(dict(before=before, summary=summary))
        self.previews.clear()
        return self.state()

    def undo(self):
        if not self.history: raise ValueError('没有可撤销的导入')
        before = self.history.pop()['before']
        self.orders = [before.get(o['id'], o) for o in self.orders]
        self.version += 1
        self.previews.clear()
        return self.state()


def bulk_demo(calculate, count=2000):
    """Synthetic orders, never mixed with the real snapshot rehearsal."""
    orders, records = [], []
    names = ['汽车喇叭 16.5 cm', '花坛 0.9×0.9米', '儿童迷你对讲机', '温度调节器', '不锈钢汤锅 20cm']
    for i in range(count):
        qty = i % 3 + 1
        shop, posting = f'演示店{i % 10 + 1:03}', f'DEMO-{i+1:06}-1'
        offer, sku = f'demo-product-{i % 5 + 1}', f'DEMOSKU{i % 5 + 1}'
        weight = str(260 * qty)
        unchanged = i % 10 == 0
        cost = D('15.20') if unchanged else D('16.35')
        o = dict(id=i+1, shop_name=shop, posting=posting, status='awaiting_packaging',
                 inputs=dict(weight_override=weight if unchanged else None,
                             weight_origin='manual' if unchanged else '', template='auto', rate=None, forwarder='2', other_cost='0'),
                 lines=[dict(id=i+1, offer_id=offer, sku=sku, name='演示商品 · '+names[i%5], quantity=qty,
                             purchase_unit=str(cost), purchase_origin='manual' if i%2 else 'inherited',
                             weight_g='300', unit_price='54', currency='CNY', image_url='')])
        o['calculation'] = calculate(o)
        orders.append(o)
        records.append(dict(row=i+2, **{'店铺名称':shop,'订单编号':posting,'平台SKU':offer,
            '产品数量':str(qty+1 if i%100==99 else qty),
            '采购金额(产品-RMB)':'' if i%100 in (2,3) else str(D('15.20')*qty),
            'SKU ID':sku,'本地备注':('' if i%100 in (1,3) else '11-03:6-2' if i%100==4 else weight+'g')}))
    return Sandbox(dict(orders=orders), calculate), records

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8768)
    args = parser.parse_args()
    # Engine copied read-only from production, outside Git. No DB or save functions used.
    sys.path.insert(0, str(args.data_dir.resolve()))
    spec = importlib.util.spec_from_file_location('profit_core', args.data_dir / 'profit_core.py')
    core = importlib.util.module_from_spec(spec); sys.modules['profit_core'] = core; spec.loader.exec_module(core)
    sandbox = Sandbox(json.loads((args.data_dir / 'snapshot.json').read_text(encoding='utf-8-sig')), core.calculate)
    demo, demo_records = bulk_demo(core.calculate)

    class Handler(BaseHTTPRequestHandler):
        def reply(self, data, status=200, mime='application/json; charset=utf-8'):
            raw = json.dumps(data, ensure_ascii=False).encode() if not isinstance(data, bytes) else data
            self.send_response(status); self.send_header('Content-Type', mime)
            self.send_header('Cache-Control', 'no-store'); self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers(); self.wfile.write(raw)

        def do_GET(self):
            path = urlsplit(self.path).path
            box = demo if urlsplit(self.path).query == 'demo=1' else sandbox
            if path == '/api/state': return self.reply(box.state())
            names = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}
            if path not in names: return self.reply(dict(error='不存在'), 404)
            name, mime = names[path]
            self.reply((HERE / name).read_bytes(), mime=mime+'; charset=utf-8')

        def do_POST(self):
            try:
                path = urlsplit(self.path).path
                box = demo if urlsplit(self.path).query == 'demo=1' else sandbox
                if self.headers.get('Origin') not in (None, f'http://127.0.0.1:{args.port}', f'http://localhost:{args.port}'):
                    raise ValueError('仅允许本地模拟页面提交')
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 5_000_000: raise ValueError('文件或请求超过 5 MB')
                raw = self.rfile.read(length)
                if path == '/api/preview': return self.reply(box.preview(read_xlsx(raw)))
                payload = json.loads(raw)
                if path == '/api/demo-preview' and box is demo: return self.reply(box.preview(demo_records))
                if path == '/api/project': return self.reply(box.project(payload['token'], payload['selected']))
                if path == '/api/apply': return self.reply(box.apply(payload['token'], payload['selected']))
                if path == '/api/undo': return self.reply(box.undo())
                self.reply(dict(error='不存在'), 404)
            except (ValueError, KeyError, zipfile.BadZipFile, E.ParseError) as error:
                self.reply(dict(error=str(error)), 400)

    print(f'Local sandbox: http://127.0.0.1:{args.port} (memory only)', flush=True)
    HTTPServer(('127.0.0.1', args.port), Handler).serve_forever()

if __name__ == '__main__': main()
