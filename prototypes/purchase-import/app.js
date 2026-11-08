const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const demo = new URLSearchParams(location.search).get('demo') === '1';
const yuan = v => v == null ? '—' : `¥${Number(v).toFixed(2)}`;
const grams = v => v == null ? '—' : Number(v).toLocaleString('zh-CN');
let state, initial, preview, selected = new Set(), projected, projectionSeq = 0;
let page = 1, size = 50, tab = 'changed', orderPage = 1, projecting = false, busy = false;
let orderMap = new Map(), initialMap = new Map(), projectedMap = new Map(), changeMap = new Map();
let projectTimer, uploadSeq = 0;
async function api(path, data, raw = false) {
 const r = await fetch('/api/'+path+(demo?'?demo=1':''), data ? {method:'POST',headers:{'Content-Type':raw?'application/octet-stream':'application/json'},body:raw?data:JSON.stringify(data)} : {});
 const d = await r.json(); if (!r.ok) throw Error(d.error); return d;
}
function notice(text, bad = false) { $('#notice').hidden=false; $('#notice').textContent=text; $('#notice').className=bad?'bad':''; }
function image(url) { try { const u=new URL(url); return u.protocol==='https:'?esc(u.href):''; } catch { return ''; } }
function setState(s) { state=s; orderMap=new Map(s.orders.map(o=>[o.id,o])); }
function rowChanges(e) { return e.error ? [] : e.changes; }
function manual(e) { return rowChanges(e).some(c=>c.origin==='manual'); }
function rowStatus(e) { return e.error?'error':e.changes.length?'changed':'skipped'; }
function satisfies(e, type) {
 if (type==='all') return true;
 if (type==='manual') return manual(e);
 if (type==='unselected') return rowChanges(e).some(c=>!selected.has(c.id));
 return rowStatus(e)===type;
}
function filtered() {
 const q=$('#search').value.trim().toLowerCase(), shop=$('#shop').value;
 return preview.entries.filter(e=>satisfies(e,tab)&&(!shop||e.shop===shop)&&(!q||e.search.includes(q)));
}
function toggleHeader(selector, ids) {
 const input=$(selector); if(!input)return;
 const count=ids.filter(id=>selected.has(id)).length;
 input.disabled=!ids.length||busy; input.checked=!!ids.length&&count===ids.length; input.indeterminate=count>0&&count<ids.length;
}
function pagerHTML(current, total, perPage, scope) {
 const pages=Math.max(1,Math.ceil(total/perPage)), start=total?(current-1)*perPage+1:0, end=Math.min(current*perPage,total);
 return `<span>第 ${start.toLocaleString()}–${end.toLocaleString()} 行，共 ${total.toLocaleString()} 行</span><div class="page-actions">${scope==='review'?`<label>每页 <select id="page-size" aria-label="每页行数">${[50,100,200].map(n=>`<option ${n===size?'selected':''}>${n}</option>`).join('')}</select> 行</label>`:''}<button data-page="1" data-scope="${scope}" ${current===1?'disabled':''} aria-label="首页">«</button><button data-page="${current-1}" data-scope="${scope}" ${current===1?'disabled':''} aria-label="上一页">‹</button><b>${current} / ${pages}</b><button data-page="${current+1}" data-scope="${scope}" ${current===pages?'disabled':''} aria-label="下一页">›</button><button data-page="${pages}" data-scope="${scope}" ${current===pages?'disabled':''} aria-label="末页">»</button></div>`;
}
function productCell(e) {
 const img=image(e.image);
 return `<div class="product">${img?`<img loading="lazy" src="${img}" alt="">`:'<span class="product-placeholder">▣</span>'}<div><strong title="${esc(e.name)}">${esc(e.name||'未匹配商品')}</strong><small title="${esc(e.offer_id||e.source?.['平台SKU'])}">${esc(e.offer_id||e.source?.['平台SKU']||'—')}</small></div></div>`;
}
function checkCell(c, e) {
 if (!c) return '';
 const manualTip=c.origin==='manual'?'<span class="manual-dot" title="将替换已人工核算的数据" aria-label="将替换已人工核算的数据">!</span>':'';
 return `<input type="checkbox" data-change="${esc(c.id)}" aria-label="${esc(e.posting+' '+c.label)}" ${selected.has(c.id)?'checked':''} ${busy?'disabled':''}>${manualTip}`;
}
function valueCells(e, kind) {
 const c=rowChanges(e).find(c=>c.field===kind), fmt=kind==='cost'?yuan:grams;
 const current=kind==='cost'?e.current_cost:e.current_weight;
 let proposed=kind==='cost'?e.source['采购金额(产品-RMB)']:null;
 if(kind==='weight') proposed=/^(\d+(?:\.\d+)?)\s*(?:g|克)$/i.exec(e.source['本地备注'])?.[1];
 const display=c?c.new:proposed;
 const tooltip=kind==='cost'&&c?`采购合计 ${yuan(c.new)}，${e.quantity} 件，单价 ¥${Number(c.value)}`:e.notes.join('；');
 return `<td class="number original">${fmt(current)}</td><td class="number proposed ${c?'has-change':''} ${c&&!selected.has(c.id)?'muted-change':''}" title="${esc(tooltip)}"><div class="cell-select">${checkCell(c,e)}<span>${display==null||display===''?'—':fmt(display)}</span>${!c&&display!=null&&display!==''?'<span class="equal-mark">·</span>':''}</div></td>`;
}
function renderRow(e) {
 const ids=rowChanges(e).map(c=>c.id), num=ids.filter(id=>selected.has(id)).length;
 const o=projectedMap.get(e.order_id)||orderMap.get(e.order_id), profit=o?.calculation.profit;
 const status=e.error?'异常':e.changes.length?(num?'待更新':'未勾选'):'无变更';
 const note=e.error||e.notes.join('；')||(manual(e)?'将替换人工核算值':'精确匹配');
 return `<tr data-row="${e.row}" class="${e.error?'error-row':''}"><td class="frozen f-check"><input type="checkbox" data-row-select="${e.row}" aria-label="选择第 ${e.row} 行" ${num===ids.length&&ids.length?'checked':''} ${!ids.length||busy?'disabled':''}></td><td class="frozen f-shop"><span class="shop-tag">${esc(e.shop||e.source['店铺名称'])}</span></td><td class="frozen f-order"><button class="posting" data-copy="${esc(e.posting||e.source['订单编号'])}">${esc(e.posting||e.source['订单编号'])} <span>⧉</span></button><small>表格第 ${e.row} 行</small></td><td class="frozen f-product">${productCell(e)}</td><td class="sku-cell" title="${esc(e.sku||e.source['SKU ID'])}">${esc(e.sku||e.source['SKU ID'])}</td><td class="number">${esc(e.quantity||e.source['产品数量'])}</td>${valueCells(e,'cost')}${valueCells(e,'weight')}<td class="number original">${yuan(e.old_profit)}</td><td class="number ${Number(profit)<0?'loss':'gain'}">${projecting?'…':yuan(profit)}</td><td class="status-cell"><span class="row-status ${e.error?'bad':num?'ready':''}">${status}</span><small title="${esc(note)}">${esc(note)}</small></td></tr>`;
}
function summary() {
 const chosen=[...selected].map(id=>changeMap.get(id)).filter(Boolean);
 const orders=new Set(chosen.map(x=>x.entry.order_id)), shops=new Set(chosen.map(x=>x.entry.shop));
 $('#selection').textContent=`已选 ${shops.size} 家店 · ${orders.size.toLocaleString()} 笔订单 · 成本 ${chosen.filter(x=>x.change.field==='cost').length.toLocaleString()} 项 · 重量 ${chosen.filter(x=>x.change.field==='weight').length.toLocaleString()} 项`;
 $('#apply').disabled=!selected.size||projecting||busy;
 $('#apply').textContent=busy?'正在更新…':projecting?'正在重算利润…':'确认更新模拟数据';
 $('#clear').disabled=busy; $('#clear').textContent=selected.size?'取消所有勾选':'勾选全部有效变更';
}
function showPreview(resetScroll=false) {
 const labels={changed:'有变更',manual:'覆盖人工值',unselected:'未勾选',skipped:'无变更',error:'异常',all:'全部'};
 $('#tabs').innerHTML=Object.entries(labels).map(([k,v])=>`<button data-tab="${k}" class="${tab===k?'active':''}" aria-pressed="${tab===k}">${v}<b>${preview.entries.filter(e=>satisfies(e,k)).length.toLocaleString()}</b></button>`).join('');
 const list=filtered(), pages=Math.max(1,Math.ceil(list.length/size)); page=Math.min(page,pages);
 const slice=list.slice((page-1)*size,page*size), x=$('#changes').scrollLeft, y=$('#changes').scrollTop;
 $('#changes').innerHTML=`<table class="sheet"><colgroup><col style="width:42px"><col style="width:86px"><col style="width:185px"><col style="width:270px"><col style="width:115px"><col style="width:52px"><col style="width:100px"><col style="width:120px"><col style="width:90px"><col style="width:110px"><col style="width:100px"><col style="width:100px"><col style="width:160px"></colgroup><thead><tr><th rowspan="2" class="frozen f-check"><input id="page-check" type="checkbox" title="仅选择本页" aria-label="选择本页全部变更"></th><th rowspan="2" class="frozen f-shop">店铺</th><th rowspan="2" class="frozen f-order">订单号</th><th rowspan="2" class="frozen f-product">商品 / 货号</th><th rowspan="2">SKU</th><th rowspan="2">件数</th><th colspan="2" class="group-cost">采购合计 · 元</th><th colspan="2" class="group-weight">整包实称 · 克</th><th colspan="2">整单预估利润 · 元</th><th rowspan="2">状态 / 说明</th></tr><tr><th>当前</th><th class="group-cost"><label title="作用于筛选结果的全部页"><input id="cost-check" type="checkbox" aria-label="选择筛选结果的采购成本">导入</label></th><th>当前</th><th class="group-weight"><label title="作用于筛选结果的全部页"><input id="weight-check" type="checkbox" aria-label="选择筛选结果的实测重量">导入</label></th><th>当前</th><th>按所选更新后</th></tr></thead><tbody>${slice.map(renderRow).join('')||'<tr><td colspan="13" class="empty">没有符合条件的记录</td></tr>'}</tbody></table>`;
 const heads=$('#changes thead').rows[0].cells;
 const widths=[...heads].slice(0,3).map(h=>h.getBoundingClientRect().width);
 $('#changes').style.setProperty('--shop-left',widths[0]+'px');
 $('#changes').style.setProperty('--order-left',(widths[0]+widths[1])+'px');
 $('#changes').style.setProperty('--product-left',(widths[0]+widths[1]+widths[2])+'px');
 $('#changes').scrollLeft=x; $('#changes').scrollTop=resetScroll?0:y;
 for (const e of slice) {const input=$(`[data-row-select="${e.row}"]`);const ids=rowChanges(e).map(c=>c.id);input.indeterminate=ids.some(id=>selected.has(id))&&!ids.every(id=>selected.has(id));}
 toggleHeader('#page-check',slice.flatMap(e=>rowChanges(e).map(c=>c.id)));
 for(const kind of ['cost','weight'])toggleHeader('#'+kind+'-check',list.flatMap(e=>rowChanges(e).filter(c=>c.field===kind).map(c=>c.id)));
 $('#pager').innerHTML=pagerHTML(page,list.length,size,'review');
 $('#count').textContent=preview.entries.length.toLocaleString()+' 行';
 $('#match-note').textContent=`筛选 ${list.length.toLocaleString()} 行`;
 $('#select-filtered').disabled=!list.some(e=>rowChanges(e).length)||busy;
 $('#unselect-filtered').disabled=!list.some(e=>rowChanges(e).some(c=>selected.has(c.id)))||busy;
 summary();
}
function showOrders() {
 $('#order-count').textContent=state.orders.length.toLocaleString()+' 笔'; $('#revision').textContent=`模拟版本 ${state.version}`; $('#undo').disabled=!state.history.length||busy;
 orderPage=Math.min(orderPage,Math.max(1,Math.ceil(state.orders.length/50)));
 const rows=state.orders.slice((orderPage-1)*50,orderPage*50);
 $('#orders').innerHTML=`<table class="orders-sheet"><thead><tr><th>店铺</th><th>订单号</th><th>商品</th><th>件数</th><th>采购合计</th><th>整包计费重量</th><th>预估利润</th></tr></thead><tbody>${rows.map(o=>{const c=o.calculation,b=initialMap.get(o.id)?.calculation||c;return `<tr><td><span class="shop-tag">${esc(o.shop_name)}</span></td><td><button class="posting" data-copy="${esc(o.posting)}">${esc(o.posting)} ⧉</button></td><td class="order-name" title="${esc(o.lines.map(l=>l.name_zh||l.name).join(' / '))}">${esc(o.lines.map(l=>l.name_zh||l.name).join(' / '))}</td><td class="number">${o.lines.reduce((n,l)=>n+l.quantity,0)}</td><td class="number ${b.purchase_cost!==c.purchase_cost?'new':''}">${yuan(c.purchase_cost)}</td><td class="number ${b.weight_g!==c.weight_g?'new':''}">${grams(c.weight_g)} 克</td><td class="number ${Number(c.profit)<0?'loss':'gain'}">${yuan(c.profit)}</td></tr>`;}).join('')}</tbody></table>`;
 $('#orders-pager').innerHTML=pagerHTML(orderPage,state.orders.length,50,'orders');
}
function installPreview(p, filename) {
 preview=p; changeMap=new Map();
 for(const e of p.entries){e.search=[e.posting,e.name,e.sku,e.offer_id,e.source['订单编号'],e.source['平台SKU']].join(' ').toLowerCase();for(const c of rowChanges(e))changeMap.set(c.id,{change:c,entry:e});}
 selected=new Set(changeMap.keys());projected=null;projectedMap.clear();page=1;tab='changed';
 $('#filename').textContent=filename;$('#review').hidden=false;$('#step2').className='active';$('#step3').className='';$('#search').value='';
 $('#shop').innerHTML='<option value="">全部店铺</option>'+[...new Set(p.entries.map(e=>e.shop).filter(Boolean))].sort().map(s=>`<option>${esc(s)}</option>`).join('');
 showPreview(true);project();
}
function project() {
 clearTimeout(projectTimer);const seq=++projectionSeq;
 if(!selected.size){projected=null;projectedMap.clear();projecting=false;showPreview();return;}
 projecting=true;summary();
 projectTimer=setTimeout(async()=>{try {const p=await api('project',{token:preview.token,selected:[...selected]});if(seq===projectionSeq){projected=p;projectedMap=new Map(p.orders.map(o=>[o.id,o]));projecting=false;showPreview();}}catch(e){if(seq===projectionSeq){notice(e.message,true);$('#apply').disabled=true;}}},180);
}
function changeSelection(ids, checked) { for(const id of ids)checked?selected.add(id):selected.delete(id);project();showPreview(); }
$('#file').onchange=async e=>{const f=e.target.files[0];if(!f)return;const seq=++uploadSeq;++projectionSeq;clearTimeout(projectTimer);$('#apply').disabled=true;try{const p=await api('preview',await f.arrayBuffer(),true);if(seq!==uploadSeq)return;installPreview(p,f.name);notice('已读取表格。有效变更默认勾选，可按行或字段取消，确认后才更新模拟数据。');$('#review').scrollIntoView({behavior:'smooth',block:'start'});}catch(error){notice(error.message,true);}finally{e.target.value='';}};
$('#changes').onchange=e=>{
 const id=e.target.dataset.change,row=e.target.dataset.rowSelect;
 if(id)return changeSelection([id],e.target.checked);
 if(row)return changeSelection(rowChanges(preview.entries.find(x=>x.row===Number(row))).map(c=>c.id),e.target.checked);
 const list=filtered();
 if(e.target.id==='page-check')return changeSelection(list.slice((page-1)*size,page*size).flatMap(e=>rowChanges(e).map(c=>c.id)),e.target.checked);
 if(['cost-check','weight-check'].includes(e.target.id))changeSelection(list.flatMap(x=>rowChanges(x).filter(c=>c.field===e.target.id.split('-')[0]).map(c=>c.id)),e.target.checked);
};
$('#tabs').onclick=e=>{const b=e.target.closest('[data-tab]');if(b){tab=b.dataset.tab;page=1;showPreview(true);}};
let searchTimer;$('#search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{page=1;showPreview(true);},140);};
$('#shop').onchange=()=>{page=1;showPreview(true);};
$('#select-filtered').onclick=()=>changeSelection(filtered().flatMap(e=>rowChanges(e).map(c=>c.id)),true);
$('#unselect-filtered').onclick=()=>changeSelection(filtered().flatMap(e=>rowChanges(e).map(c=>c.id)),false);
$('#clear').onclick=()=>changeSelection([...changeMap.keys()],!selected.size);
document.addEventListener('change',e=>{if(e.target.id==='page-size'){size=Number(e.target.value);page=1;showPreview(true);}});
document.addEventListener('click',async e=>{
 const b=e.target.closest('[data-page]');if(b&&!b.disabled){if(b.dataset.scope==='review'){page=Number(b.dataset.page);showPreview(true);}else{orderPage=Number(b.dataset.page);showOrders();$('#orders').scrollTop=0;}}
 const text=e.target.closest('[data-copy]')?.dataset.copy;if(text){try{await navigator.clipboard.writeText(text);notice('订单号已复制：'+text);}catch{notice('请直接选中订单号复制',true);}}
});
$('#apply').onclick=async()=>{
 if(projecting||busy||!selected.size)return;busy=true;summary();
 try{setState(await api('apply',{token:preview.token,selected:[...selected]}));const h=state.history.at(-1);$('#review').hidden=true;$('#step3').className='active';orderPage=1;notice(`模拟更新完成：${h.orders.toLocaleString()} 笔订单、${h.fields.toLocaleString()} 项变更。未勾选字段和其他订单保持原值，可撤销。`);}catch(e){notice(e.message,true);}finally{busy=false;showOrders();summary();}
};
$('#undo').onclick=async()=>{if(busy)return;busy=true;$('#undo').disabled=true;try{setState(await api('undo',{}));++projectionSeq;clearTimeout(projectTimer);projecting=false;$('#review').hidden=true;notice('已撤销上次模拟导入，相关字段与利润已恢复。');}catch(e){notice(e.message,true);}finally{busy=false;showOrders();}};
if(demo){$('#demo-link').textContent='返回真实订单副本 ↗';$('#demo-link').href='/';$('#intro').textContent='2,000 条虚拟订单 · 验证批量筛选、翻页和选择，不是真实交易';$('#orders-note').textContent='虚拟订单，和 9 笔真实副本分开保存；所有修改只在内存中。';}
api('state').then(async d=>{setState(d);initial=structuredClone(d);initialMap=new Map(initial.orders.map(o=>[o.id,o]));showOrders();if(demo){installPreview(await api('demo-preview',{}),'2,000 行批量演示数据（虚拟订单）');notice('虚拟演示：共 2,000 行。包含相同值、人工核算覆盖及件数不符，供测试跨页勾选。');}}).catch(e=>notice(e.message,true));
