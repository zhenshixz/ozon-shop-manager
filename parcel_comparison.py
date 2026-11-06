"""Read-only shipping and forwarding estimates for small split plans."""

import profit_core as core


def compare(db, order, trial_weights=None):
    lines = order['lines']
    quantities = [line['quantity'] for line in lines]
    if quantities not in ([2], [3], [1, 1]) or order['source'] != 'fbs':
        return None
    base = order['calculation']
    if not base['included_in_summary'] or db.execute('''SELECT 1 FROM profit_orders
        WHERE shop=? AND order_number=? AND id<>? AND lower(status) NOT IN ('cancelled','canceled','returned')
        LIMIT 1''', (order['shop'], order['order_number'], order['id'])).fetchone():
        return None

    weights = trial_weights if isinstance(trial_weights, dict) else {}
    inputs = order['inputs']
    forwarding = core.number(inputs.get('forwarder', '2'), '货代费')
    packages = {}
    exact = {}

    def add_package(key, label, items, default_weight, source, template='auto'):
        weight = default_weight
        error = None
        if weights.get(key) not in (None, ''):
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
            template=template, forwarder=forwarding)))
        if '请补充人民币核算售价' in quote['issues']:
            error = '请先补充人民币核算售价'
        if not error:
            exact[key] = core.D(quote['shipping_exact']) + forwarding
        packages[key] = dict(label=label, weight=None if weight is None else str(weight),
                             default_weight=None if default_weight is None else str(default_weight),
                             source=source, error=error, revenue=None if error else quote['revenue'],
                             tier=None if error else quote['template']['name'])

    if base['weight_source'] == 'custom':
        source = '本单计费重量'
        if inputs.get('weight_origin') == 'inherited' and inputs.get('inherited_weight_g') == base['custom_weight_g']:
            source = '同件数历史实称'
    else:
        source = 'API 估重' if base['weight_g'] is not None else '待补重量'
    add_package('all', '合发整包', lines, base['weight_g'], source, inputs.get('template', 'auto'))

    def add_split_package(key, label, line, quantity):
        ref = core.latest_measured_weight(db, order['shop'], quantity, line['offer_id'], line['sku'], order['id'])
        if ref:
            weight, source = ref['weight_override'], '同件数历史实称'
        elif line.get('weight_g') not in (None, ''):
            weight, source = core.D(line['weight_g']) * quantity, 'API 估重'
        else:
            weight, source = None, '待补重量'
        add_package(key, label, [dict(line, quantity=quantity)], weight, source)

    plans = []

    def add_plan(label, parts):
        cost = None
        if all(key in exact for key, count in parts):
            cost = core.money(sum(exact[key] * count for key, count in parts))
        plans.append(dict(label=label, parts=[dict(key=key, count=count) for key, count in parts], cost=cost))

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
    if all(plan['cost'] is not None for plan in plans):
        ranked = sorted(range(len(plans)), key=lambda i: core.D(plans[i]['cost']))
        best = ranked[0]
        alternative = ranked[1] if best == 0 else 0
        spread = core.money(core.D(plans[alternative]['cost']) - core.D(plans[best]['cost']))
    return dict(packages=packages, plans=plans, best=best, spread=spread, forwarder=base['forwarder'])
