"""Compare and confirm the packaging used for an order's profit estimate."""

from collections import Counter, defaultdict
import json
import time
import profit_core as core


def composition(lines):
    return sorted([line['line_key'],line['quantity']] for line in lines)


def has_sibling(db, order):
    return db.execute('''SELECT 1 FROM profit_orders WHERE shop=? AND order_number=? AND id<>?
        AND lower(status) NOT IN ('cancelled','canceled','returned') LIMIT 1''',
        (order['shop'],order['order_number'],order['id'])).fetchone() is not None


def saved_plan(db, order):
    row = db.execute('SELECT data FROM profit_dispatch_plans WHERE order_id=? AND applied=0',
                     (order['id'],)).fetchone()
    if row and not has_sibling(db,order):
        data = json.loads(row['data'])
        if data['shape'] == composition(order['lines']):
            return data['plan']
    return None


def split_charges(lines, plan, forwarding, rate_override=None):
    by_key = {line['line_key']:line for line in lines}
    total = core.D(0)
    line_logistics = defaultdict(core.D)
    line_fees = defaultdict(core.D)
    fees = core.D(0)
    rates = []
    for parcel in plan['packages']:
        items = [dict(by_key[key],quantity=qty) for key,qty in parcel['shape']]
        quote = core.calculate(dict(lines=items,inputs={'weight_override':parcel['weight'],'rate':rate_override}))
        shipping = core.D(quote['shipping_exact'])
        fee = core.D(quote['platform_fee_exact'])
        total += shipping
        fees += fee
        rates.append(quote['rate'])
        # In this version each split parcel contains only one SKU.
        line_logistics[items[0]['line_key']] += shipping + forwarding
        line_fees[items[0]['line_key']] += fee
    return dict(shipping=total,fees=fees,rates=rates,
                logistics={key:str(value) for key,value in line_logistics.items()},
                line_fees={key:str(value) for key,value in line_fees.items()})


def compare(db, order, trial_weights=None):
    lines = order['lines']
    quantities = [line['quantity'] for line in lines]
    if quantities not in ([2], [3], [1, 1]) or order['source'] != 'fbs':
        return None
    if not order['calculation']['included_in_summary'] or has_sibling(db,order):
        return None
    base = core.calculate(dict(order,dispatch_plan=None))

    weights = trial_weights if isinstance(trial_weights, dict) else {}
    inputs = order['inputs']
    forwarding = core.number(inputs.get('forwarder', '2'), '货代费')
    packages = {}
    exact = {}
    platform_exact = {}

    def add_package(key, label, items, default_weight, source, template='auto', reference_posting=None):
        weight = default_weight
        error = None
        if key != 'all' and weights.get(key) not in (None, ''):
            source = '本次试算'
            try:
                weight = core.number(weights[key], '试算重量', maximum='25000')
            except ValueError:
                weight = None
                error = '请输入大于 0 且不超过 25000 的克数'
        if not error and (weight is None or not 0 < core.D(weight) <= 25000):
            error = '请补充有效的整包重量（不超过 25 千克）'
        quote = core.calculate(dict(lines=items, inputs=dict(
            weight_override=None if error else weight,
            template=template, forwarder=forwarding,rate=inputs.get('rate'))))
        if '请补充人民币核算售价' in quote['issues']:
            error = '请先补充人民币核算售价'
        if not error:
            exact[key] = core.D(quote['shipping_exact']) + forwarding
            platform_exact[key] = core.D(quote['platform_fee_exact'])
        packages[key] = dict(label=label, weight=None if weight is None else str(weight),
                             default_weight=None if default_weight is None else str(default_weight),
                             source=source, error=error, revenue=None if error else quote['revenue'],
                             tier=None if error else quote['template']['name'],rate=quote['rate'],shape=composition(items),
                             reference_posting=reference_posting)

    if base['weight_source'] == 'custom':
        source = '本单计费重量'
        if inputs.get('weight_origin') == 'inherited' and inputs.get('inherited_weight_g') == base['custom_weight_g']:
            source = '同件数历史实称'
    else:
        source = 'API 估重' if base['weight_g'] is not None else '待补重量'
    add_package('all', '合发整包', lines, base['weight_g'], source, inputs.get('template', 'auto'))

    def add_split_package(key, label, line, quantity):
        ref = core.latest_measured_weight(db,order['shop'],quantity,line['offer_id'],line['sku'],order['id'])
        if ref:
            weight, source = ref['weight_override'], '同件数历史实称'
        elif line.get('weight_g') not in (None, ''):
            weight, source = core.D(line['weight_g']) * quantity, 'API 估重'
        else:
            weight, source = None, '待补重量'
        add_package(key, label, [dict(line, quantity=quantity)], weight, source,
                    reference_posting=ref['posting'] if ref else None)

    plans = []

    def add_plan(label, parts):
        parcels = []
        by_key = {line['line_key']:line for line in lines}
        for key,count in parts:
            parcels.append(key)
            for index in range(2,count+1):
                repeated = f'{key}-{index}'
                package = packages[key]
                items = [dict(by_key[line_key],quantity=qty) for line_key,qty in package['shape']]
                source = 'API 估重' if package['source']=='API 估重' else '同首包重量'
                add_package(repeated,f'第 {index} 包',items,package['weight'],source)
                parcels.append(repeated)
        cost = platform_fee = total_cost = None
        if all(key in exact for key in parcels):
            cost = core.money(sum(exact[key] for key in parcels))
            platform_fee = core.money(sum(platform_exact[key] for key in parcels))
            total_cost = sum(exact[key]+platform_exact[key] for key in parcels)
        plans.append(dict(label=label,parts=[dict(key=key,count=1) for key in parcels],cost=cost,
                          platform_fee=platform_fee,total_cost=core.money(total_cost)))

    add_plan('合发 1 包', [('all', 1)])
    if quantities == [1, 1]:
        add_split_package('one', '商品 1 单独发', lines[0], 1)
        add_split_package('two', '商品 2 单独发', lines[1], 1)
        add_plan('两款各发 1 包', [('one', 1), ('two', 1)])
    else:
        add_split_package('one', '1 件装一包', lines[0], 1)
        if quantities == [3]:
            add_split_package('two', '2 件装一包', lines[0], 2)
            add_plan('拆 2 包 · 2+1', [('two', 1), ('one', 1)])
            add_plan('拆 3 包 · 1+1+1', [('one', 3)])
        else:
            add_plan('拆 2 包 · 1+1', [('one', 2)])

    best = spread = None
    if all(plan['total_cost'] is not None for plan in plans):
        def total(index):
            return sum(exact[part['key']]+platform_exact[part['key']] for part in plans[index]['parts'])
        ranked = sorted(range(len(plans)), key=total)
        best = ranked[0]
        alternative = ranked[1] if best == 0 else 0
        spread = core.money(total(alternative) - total(best))
    return dict(packages=packages, plans=plans, best=best, spread=spread, forwarder=base['forwarder'])


def select(db, order, choice, weights, preview):
    if type(choice) is not int or choice < 0:
        raise ValueError('请选择实际发货方式')
    if choice == 0:
        return None
    comparison = compare(db,order,weights)
    if comparison is None or choice >= len(comparison['plans']):
        raise ValueError('发货单明细已更新，请重新打开核算')
    plan = comparison['plans'][choice]
    supplied = weights if isinstance(weights,dict) else {}
    if any(supplied.get(key) in (None,'') and not comparison['packages'][key]['reference_posting']
           for key in (part['key'] for part in plan['parts']) if '-' not in key):
        if preview:
            return None
        raise ValueError('请填写所选拆发方案的实际包裹重量')
    if plan['cost'] is None:
        if preview:
            return None
        raise ValueError('请检查所选方案的重量和人民币售价')
    packages = []
    actual = {}
    for part in plan['parts']:
        key = part['key']
        package = comparison['packages'][key]
        if '-' not in key or supplied.get(key) not in (None,''):
            actual[key] = package['weight']
        packages.extend([dict(shape=package['shape'],weight=package['weight']) for _ in range(part['count'])])
    return dict(choice=choice,weights=actual,packages=packages)


def save(db, order):
    plan = order.get('dispatch_plan')
    if plan is None:
        db.execute('DELETE FROM profit_dispatch_plans WHERE order_id=? AND applied=0',(order['id'],))
        return
    shape = composition(order['lines'])
    stored = db.execute('SELECT line_key,quantity FROM profit_lines WHERE order_id=?',(order['id'],)).fetchall()
    if composition(stored) != shape or has_sibling(db,order):
        raise ValueError('发货单已拆分或件数已变化，请重新打开核算')
    data = dict(plan=plan,shape=shape,rate=order['calculation']['rate'],rate_override=order['inputs'].get('rate'),
                forwarding=order['inputs']['forwarder'],
                prices={line['line_key']:[line['purchase_unit'],line['price_override'],line['id']] for line in order['lines']})
    db.execute('''INSERT INTO profit_dispatch_plans(order_id,data,updated) VALUES (?,?,?)
        ON CONFLICT(order_id) DO UPDATE SET data=excluded.data,updated=excluded.updated,applied=0''',
        (order['id'],json.dumps(data,ensure_ascii=False),time.time()))


def apply_to_synced_packages(db, shop, postings, now):
    """Hand confirmed weights to the normal posting rows on an exact split match."""
    pending = db.execute('''SELECT d.*,o.posting,o.order_number FROM profit_dispatch_plans d
        JOIN profit_orders o ON o.id=d.order_id WHERE o.shop=? AND d.applied=0''',(shop,)).fetchall()
    if not pending:
        return
    groups = defaultdict(dict)
    for posting in postings:
        order_number = str(posting.get('order_number') or '')
        pnum = posting.get('posting_number')
        if pnum and str(posting.get('status','')).lower() not in core.EXCLUDED_PROFIT_STATUSES:
            groups[order_number][pnum] = posting
    for record in pending:
        group = groups.get(record['order_number'],{})
        if len(group) < 2 or record['posting'] not in group:
            continue
        # Only use batches that contain the main and child postings Ozon links.
        if not any(p.get('parent_posting_number') == record['posting'] for p in group.values()):
            continue
        data = json.loads(record['data'])
        original_inputs = db.execute('SELECT rate FROM profit_inputs WHERE order_id=?',(record['order_id'],)).fetchone()
        rate_override = data.get('rate_override',original_inputs['rate'] if original_inputs else None)
        shapes = {}
        for number, posting in group.items():
            shape = []
            for item in posting.get('products') or []:
                key = item.get('offer_id') or item.get('sku') or item.get('product_id')
                quantity = item.get('quantity')
                if key and type(quantity) is int and quantity > 0:
                    shape.append([str(key),quantity])
            if shape and len(shape) == len(posting.get('products') or []):
                shapes[number] = sorted(shape)
        if len(shapes) != len(group):
            continue
        expected = defaultdict(list)
        for parcel in data['plan']['packages']:
            expected[tuple(map(tuple,parcel['shape']))].append(parcel['weight'])
        if Counter(tuple(map(tuple,s)) for s in shapes.values()) != Counter({k:len(v) for k,v in expected.items()}):
            continue
        previous = []
        for number in sorted(shapes,key=lambda p:(p!=record['posting'],p)):
            shape = shapes[number]
            weight = expected[tuple(map(tuple,shape))].pop(0)
            oid = db.execute('SELECT id FROM profit_orders WHERE shop=? AND posting=?',(shop,number)).fetchone()['id']
            old_inputs = db.execute('SELECT * FROM profit_inputs WHERE order_id=?',(oid,)).fetchone()
            old_weight_source = db.execute('SELECT * FROM profit_weight_sources WHERE order_id=?',(oid,)).fetchone()
            newer_edit = old_inputs and old_inputs['updated'] > record['updated'] and (
                (old_weight_source and old_weight_source['origin']=='manual') or
                old_inputs['template']!='auto' or old_inputs['rate'] is not None or
                core.D(old_inputs['forwarder'])!=2 or core.D(old_inputs['other_cost'])!=0)
            snapshot = dict(order_id=oid,inputs=dict(old_inputs) if old_inputs else None,
                            weight_source=dict(old_weight_source) if old_weight_source else None,removed_lines=[])
            previous.append(snapshot)
            keys = {key for key,qty in shape}
            for line in db.execute('SELECT * FROM profit_lines WHERE order_id=?',(oid,)).fetchall():
                if line['line_key'] not in keys:
                    source = db.execute('SELECT * FROM profit_purchase_sources WHERE line_id=?',(line['id'],)).fetchone()
                    snapshot['removed_lines'].append(dict(line=dict(line),source=dict(source) if source else None))
                    db.execute('DELETE FROM profit_purchase_sources WHERE line_id=?',(line['id'],))
                    db.execute('DELETE FROM profit_lines WHERE id=?',(line['id'],))
                    continue
                if oid == record['order_id'] or newer_edit:
                    continue
                unit, price, source_id = data['prices'][line['line_key']]
                origin = db.execute('SELECT origin FROM profit_purchase_sources WHERE line_id=?',(line['id'],)).fetchone()
                if unit is not None and (line['purchase_unit'] is None or (origin and origin['origin']=='inherited')):
                    if not db.execute('SELECT 1 FROM profit_lines WHERE id=? AND order_id=?',
                                      (source_id,record['order_id'])).fetchone():
                        source_id = None
                    db.execute('UPDATE profit_lines SET purchase_unit=? WHERE id=?',(unit,line['id']))
                    db.execute("INSERT OR REPLACE INTO profit_purchase_sources VALUES (?,'inherited',?,?,?)",
                               (line['id'],source_id,now,unit))
                if price is not None and line['price_override'] is None:
                    db.execute('UPDATE profit_lines SET price_override=? WHERE id=?',(price,line['id']))
            if newer_edit:
                continue
            db.execute('''INSERT INTO profit_inputs(order_id,weight_override,template,rate,forwarder,other_cost,updated)
                VALUES (?,?,'auto',?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET weight_override=excluded.weight_override,
                template=excluded.template,rate=excluded.rate,forwarder=excluded.forwarder,updated=excluded.updated''',
                (oid,weight,rate_override,data['forwarding'],'0',now))
            db.execute("INSERT OR REPLACE INTO profit_weight_sources VALUES (?,'dispatch',?,?,?)",
                       (oid,record['order_id'],now,weight))
        data['adopted_from'] = previous
        db.execute('UPDATE profit_dispatch_plans SET data=?,applied=1 WHERE order_id=?',
                   (json.dumps(data,ensure_ascii=False),record['order_id']))
