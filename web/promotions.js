'use strict';
const $=id=>document.getElementById(id), base='/ozon-profit/promotions/api/';
let state=null,csrf='',selected=new Set(),dirty=false,active=false,busy=false;
let draftHour=9,draftMinute=0;
const padTime=n=>String(n).padStart(2,'0');
function parseTime(value){
 const text=value.trim();let h,m;
 if(/^\d{1,2}:\d{1,2}$/.test(text))[h,m]=text.split(':').map(Number);
 else if(/^\d{3,4}$/.test(text)){const digits=text.padStart(4,'0');h=Number(digits.slice(0,2));m=Number(digits.slice(2));}
 else return null;
 return h<24&&m<60?[h,m]:null;
}
function timeDisplay(){
 $('time-value').textContent=$('time').value;
 const changed=state&&$('time').value!==state.plan.time;
 $('time-hint').textContent=changed?'时间已修改，请保存每日计划。':'选择时间后，保存每日计划生效。';
 $('time-hint').classList.toggle('unsaved',Boolean(changed));
}
function paintTime(scroll=false){
 for(const [id,value] of [['time-hours',draftHour],['time-minutes',draftMinute]]){
  const column=$(id);
  for(const option of column.children)option.setAttribute('aria-selected',String(Number(option.dataset.timeOption)===value));
  const option=column.querySelector('[aria-selected="true"]');
  column.setAttribute('aria-activedescendant',option.id);
  if(scroll)column.scrollTop=option.offsetTop-(column.clientHeight-option.clientHeight)/2;
 }
 for(const button of document.querySelectorAll('[data-quick-time]'))button.classList.toggle('active',button.dataset.quickTime===`${padTime(draftHour)}:${padTime(draftMinute)}`);
}
function setDraft(h,m,scroll=true){
 draftHour=h;draftMinute=m;$('time-draft').value=`${padTime(h)}:${padTime(m)}`;
 $('time-error').hidden=true;$('time-apply').disabled=false;paintTime(scroll);
}
for(const [id,limit] of [['time-hours',24],['time-minutes',60]]){
 const column=$(id);
 column.innerHTML=Array.from({length:limit},(_,n)=>`<button type="button" role="option" tabindex="-1" id="${id}-${n}" data-time-option="${n}" aria-selected="false">${padTime(n)}</button>`).join('');
 column.addEventListener('click',e=>{const option=e.target.closest('[data-time-option]');if(!option)return;const n=Number(option.dataset.timeOption);setDraft(id==='time-hours'?n:draftHour,id==='time-minutes'?n:draftMinute);column.focus({preventScroll:true});});
 column.addEventListener('keydown',e=>{const current=id==='time-hours'?draftHour:draftMinute;let n;
  if(e.key==='ArrowDown')n=Math.min(limit-1,current+1);else if(e.key==='ArrowUp')n=Math.max(0,current-1);else if(e.key==='Home')n=0;else if(e.key==='End')n=limit-1;else if(e.key==='PageDown')n=Math.min(limit-1,current+5);else if(e.key==='PageUp')n=Math.max(0,current-5);else if(e.key==='Enter'){e.preventDefault();$('time-apply').click();return;}else return;
  e.preventDefault();setDraft(id==='time-hours'?n:draftHour,id==='time-minutes'?n:draftMinute);
 });
}
$('time-open').addEventListener('click',()=>{
 const [h,m]=parseTime($('time').value)||[9,0];$('time-picker').showModal();$('time-open').setAttribute('aria-expanded','true');setDraft(h,m);$('time-hours').focus({preventScroll:true});
});
$('time-picker').addEventListener('close',()=>{$('time-open').setAttribute('aria-expanded','false');});
window.addEventListener('resize',()=>{if($('time-picker').open)requestAnimationFrame(()=>paintTime(true));});
for(const button of document.querySelectorAll('[data-quick-time]'))button.addEventListener('click',()=>setDraft(...parseTime(button.dataset.quickTime)));
$('time-draft').addEventListener('input',()=>{const parsed=parseTime($('time-draft').value);$('time-error').hidden=Boolean(parsed);$('time-apply').disabled=!parsed;if(parsed){[draftHour,draftMinute]=parsed;paintTime(true);}});
$('time-draft').addEventListener('blur',()=>{const parsed=parseTime($('time-draft').value);if(parsed)setDraft(...parsed);});
$('time-draft').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();if(parseTime(e.target.value))$('time-apply').click();}});
$('time-apply').addEventListener('click',()=>{const parsed=parseTime($('time-draft').value);if(!parsed)return;$('time').value=parsed.map(padTime).join(':');dirty=true;timeDisplay();$('time-picker').close();});
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const stamp=v=>v?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(typeof v==='number'?v*1000:v)):'尚未运行';
const day=v=>new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(v*1000));
const label={ok:'完成',error:'需关注',running:'执行中',queued:'等待执行'};
let detailRequest=0;
function productList(batch){
 const products=batch.products;
 if(!Array.isArray(products)||(!products.length&&batch.before>0))return `<p class="product-empty">${batch.before?'此历史记录未保留可恢复的商品明细。':'没有待加入商品。'}</p>`;
 if(!products.length)return '<p class="product-empty">没有待加入商品。</p>';
 const names={removed:'已移除',unconfirmed:'待核对',not_removed:'未执行移除',remaining:'仍在待加入队列'};
 return `<details class="product-section" ${products.length<=8?'open':''}><summary>商品明细 <span>${products.length} 条 · 接口确认移除 ${products.filter(p=>p.status==='removed').length} 条</span></summary><div class="product-list">${products.map(p=>`<div class="removed-product"><div class="product-top"><strong>${esc(p.name||`商品 ${p.product_id}`)}</strong><span class="badge ${p.status==='removed'?'ok':p.status==='not_removed'?'neutral':'error'}">${esc(names[p.status]||'待核对')}</span></div><div class="product-identifiers">${p.offer_id?`<span>货号：${esc(p.offer_id)}</span>`:''}<span>商品 ID：${esc(p.product_id)}</span>${p.sku?`<span>SKU：${esc(p.sku)}</span>`:''}</div></div>`).join('')}</div></details>`;
}
async function showDetails(rid,sid='',open=false){
 const dialog=$('detail'),ticket=++detailRequest;
 if(open){dialog.dataset.run=rid;dialog.dataset.sid=sid;$('detail-title').textContent='商品与活动明细';$('detail-body').textContent='正在读取记录…';dialog.showModal();}
 try{
  const {run:r}=await api('runs/'+encodeURIComponent(rid));
  if(ticket!==detailRequest||!dialog.open)return;
  const results=sid?r.result.filter(s=>s.shop===sid):r.result;
  $('detail-title').textContent=(sid?state.shops.find(s=>s.id===sid)?.name||'店铺详情':'任务详情')+' · '+stamp(r.created);
  $('detail-body').innerHTML=results.length?results.map(s=>`<section class="detail-shop"><h3>${esc(s.name)} <span class="badge ${esc(s.status)}">${label[s.status]||'需关注'}</span></h3><p>发现 ${s.activities??'—'} 个官方活动 · 移除 ${s.removed||0} 条队列记录</p>${s.error?`<p class="batch-error">${esc(s.error)}</p>`:''}${(s.batches||[]).map(a=>`<div class="batch"><strong>${esc(a.title)}</strong><small>计划加入：${stamp(a.date)}（北京时间）</small><div class="batch-values"><span>原队列 <b>${a.before}</b></span><span>已移除 <b>${a.removed}</b></span><span>剩余 <b>${a.remaining??'待核对'}</b></span></div>${a.error?`<p class="batch-error">${esc(a.error)}</p>`:''}${productList(a)}</div>`).join('')||'<div class="empty">没有未来待加入批次，或任务尚未开始。</div>'}</section>`).join(''):'<p>任务尚未开始，或执行中断。未收到核对结果的店铺不能视为已完成。</p>';
 }catch(e){if(ticket===detailRequest&&dialog.open)$('detail-body').textContent=e.message;}
}
function message(text,bad=false){$('notice').hidden=false;$('notice').className='notice'+(bad?' bad':'');$('notice').textContent=text;}
async function api(path,data){const r=await fetch(base+path,{method:data?'POST':'GET',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},...(data?{body:JSON.stringify(data)}:{})});const body=await r.json();if(r.status===401){$('gate').hidden=false;$('workspace').hidden=true;$('logout').hidden=true;}if(!r.ok)throw new Error(body.error||'请求失败，请稍后再试');return body;}
function latest(sid){for(const run of state.runs){if(run.shops.includes(sid)){const item=run.result.find(s=>s.shop===sid);return{run,item,status:run.status==='error'&&(!item||item.status==='running')?'error':item?.status||run.status};}}return null;}
function historyShopName(run,sid){return run.result.find(s=>s.shop===sid)?.name||state.shops.find(s=>s.id===sid)?.name||sid;}
function historyShops(run){
 const removedShops=run.shops.filter(sid=>(run.result.find(s=>s.shop===sid)?.removed||0)>0);
 if(!removedShops.length)return '';
 return `<div class="history-shops"><span>已移除店铺</span>${removedShops.map(sid=>{const result=run.result.find(s=>s.shop===sid);const unfinished=!result||['running','queued'].includes(result.status);const failed=result?.status==='error'||(unfinished&&!['running','queued'].includes(run.status));const suffix=failed?(result?.status==='error'?'需关注':'未完成'):unfinished?(result?label[result.status]||'待处理':'待处理'):'';return `<button type="button" class="history-shop-name ${failed?'shop-needs-attention':''}" data-run="${esc(run.id)}" data-sid="${esc(sid)}">${esc(historyShopName(run,sid))}${suffix?` · ${suffix}`:''}</button>`;}).join('')}</div>`;
}
function activityOwners(activity,runId){
 if(!runId||!activity.shops.size)return '';
 return `<div class="activity-owners">${[...activity.shops.values()].filter(s=>s.removed>0).map(s=>`<button type="button" class="activity-owner ${s.error?'owner-error':s.pending?'owner-pending':''}" data-run="${esc(runId)}" data-sid="${esc(s.id)}"><b>${esc(s.name)}</b><span>移除 ${s.removed} 条${s.error?' · 需核对':s.pending?' · 核对中':''}</span></button>`).join('')}</div>`;
}
function activityNames(batches,runId=''){
 const grouped=new Map();
 for(const batch of batches||[]){
  const key=batch.action_id??batch.title;if(!key)continue;
  if(!grouped.has(key))grouped.set(key,{title:batch.title||`活动 ${batch.action_id}`,removed:0,dates:new Set(),error:false,pending:false,shops:new Map()});
  const item=grouped.get(key);item.removed+=batch.removed||0;if(batch.date&&batch.removed>0)item.dates.add(batch.date);
  item.error||=batch.status==='error';item.pending||=!['ok','error'].includes(batch.status);
  if(batch.shop_id){
   if(!item.shops.has(batch.shop_id))item.shops.set(batch.shop_id,{id:batch.shop_id,name:batch.shop_name,removed:0,error:false,pending:false});
   const shop=item.shops.get(batch.shop_id);shop.removed+=batch.removed||0;shop.error||=batch.status==='error';shop.pending||=!['ok','error'].includes(batch.status);
  }
 }
 const removedActivities=[...grouped.values()].filter(a=>a.removed>0);
 if(!removedActivities.length)return '';
 return `<div class="activity-list"><span class="activity-caption">已移除活动</span>${removedActivities.map(a=>`<div class="activity-item"><div class="activity-name">${esc(a.title)}</div><span class="activity-outcome ${a.error?'activity-error':''}">${a.error?'需核对 · ':a.pending?'核对中 · ':''}已移除 ${a.removed} 条</span><small>计划加入：${[...a.dates].sort().map(d=>esc(stamp(d))).join('、')}（北京时间）</small>${activityOwners(a,runId)}</div>`).join('')}</div>`;
}
function selection(){const count=selected.size;$('selection-count').textContent=`已选择 ${count} 家`;$('run').disabled=!count||active||busy;$('run').textContent=active?'任务执行中':`立即执行${count?' · '+count+' 家':''}`;$('select-all').checked=count===state.shops.length&&count>0;$('select-all').indeterminate=count>0&&count<state.shops.length;}
function render(){
 timeDisplay();
 active=state.runs.some(r=>['queued','running'].includes(r.status));
 $('shop-count').textContent=state.shops.length;
 $('shops').innerHTML=state.shops.map(s=>{const last=latest(s.id),r=last?.item;let text='尚未运行';if(last)text=last.status==='ok'?(r?.removed?`已清理 ${r.removed} 条`:'无待加入商品'):label[last.status]||'需关注';return `<div class="shop-entry"><div class="shop-row"><input type="checkbox" data-shop="${esc(s.id)}" aria-label="选择${esc(s.name)}" ${selected.has(s.id)?'checked':''}><span class="shop-avatar">${esc(s.name.slice(-3))}</span><div class="shop-info"><strong>${esc(s.name)}</strong><div class="shop-sub">${last?`${stamp(last.run.created)} · ${last.run.source==='schedule'?'每日计划':'手动执行'}`:'尚无执行记录'}</div></div><div class="shop-result"><span class="badge ${last?.status||'neutral'}">${esc(text)}</span>${last?`<button class="shop-detail" data-run="${esc(last.run.id)}" data-sid="${esc(s.id)}">活动与商品 ↗</button>`:''}</div></div>${activityNames(r?.batches)}</div>`;}).join('');
 const today=state.runs.filter(r=>day(r.created)===day(state.now));let done=new Set(),removed=0;
 for(const r of today)for(const s of r.result){if(s.status==='ok')done.add(s.shop);removed+=s.removed||0;}
 $('metric-shops').textContent=today.length?`${done.size} / ${state.shops.length}`:'—';$('metric-removed').textContent=today.length?removed:'—';
 const last=state.runs[0];$('metric-errors').textContent=last?last.shops.filter(s=>{const v=last.result.find(x=>x.shop===s);return v?.status==='error'||(last.status==='error'&&(!v||v.status==='running'));}).length:'—';
 $('plan-status').textContent=state.plan.enabled?'每日计划已启用':'定时未启用';$('plan-status').className='badge '+(state.plan.enabled?'ok':'neutral');
 const names=state.shops.filter(s=>state.plan.shops.includes(s.id)).map(s=>s.name);$('saved-scope').textContent=names.join('、')||'尚未选择';
 const scheduledToday=state.runs.some(r=>r.source==='schedule'&&day(r.created)===day(state.now));
 if(state.plan.enabled){const nowHH=new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(state.now*1000));const due=nowHH>=state.plan.time;const skipped=state.plan.last_day===day(state.now);$('metric-next').textContent=(!due?'今天 ':due&&!skipped?'等待执行 · ':'明天 ')+state.plan.time;$('saved-time').textContent=due&&!skipped&&!scheduledToday?'今日计划时间已到，等待调度；超过两分钟未启动请检查服务。':`每天 ${state.plan.time} · ${names.length} 家店铺`;}else{$('metric-next').textContent='未启用';$('saved-time').textContent='每日计划已暂停，可单独手动执行。';}
 $('history').innerHTML=state.runs.length?state.runs.map(r=>`<div class="history-entry"><div class="history-row"><span class="badge ${esc(r.status)}">${label[r.status]||'需关注'}</span><div class="history-main"><strong>${r.source==='schedule'?'每日计划':'手动清理'} · ${r.shops.length===1?esc(historyShopName(r,r.shops[0])):r.shops.length+' 家店铺'} · 移除 ${r.result.reduce((n,s)=>n+(s.removed||0),0)} 条</strong><small>${stamp(r.created)}${r.finished?' — '+stamp(r.finished):''}</small></div><button class="text-button" data-run="${esc(r.id)}">活动与商品 ↗</button></div>${historyShops(r)}${activityNames(r.result.flatMap(s=>(s.batches||[]).map(b=>({...b,shop_id:s.shop,shop_name:s.name||historyShopName(r,s.shop)}))),r.id)}</div>`).join(''):'<div class="empty"><b>◷</b>还没有运行记录<br>选择店铺后可手动执行，或设置每日计划。</div>';
 $('last-refresh').textContent='状态更新于 '+stamp(state.now);selection();
}
async function load(){try{const next=await api('state');const first=!state;state=next;csrf=state.csrf;if(first){selected=new Set(state.plan.shops);$('enabled').checked=state.plan.enabled;$('time').value=state.plan.time;}$('gate').hidden=true;$('workspace').hidden=false;$('logout').hidden=false;render();if($('detail').open)showDetails($('detail').dataset.run,$('detail').dataset.sid);}catch(e){if(state)message(e.message,true);}}
$('login').addEventListener('submit',async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;try{await api('login',{password:$('password').value});$('password').value='';$('login-error').textContent='';await load();}catch(err){$('login-error').textContent=err.message;}finally{button.disabled=false;}});
$('shops').addEventListener('change',e=>{const id=e.target.dataset.shop;if(!id)return;e.target.checked?selected.add(id):selected.delete(id);dirty=true;selection();});
$('select-all').addEventListener('change',e=>{selected=new Set(e.target.checked?state.shops.map(s=>s.id):[]);dirty=true;render();});
$('save').addEventListener('click',async()=>{const b=$('save');b.disabled=true;try{await api('plan',{enabled:$('enabled').checked,time:$('time').value,shops:[...selected]});dirty=false;message('每日计划已保存。'+($('enabled').checked?'将从下一次计划时间开始执行。':'当前为暂停状态，不会自动清理。'));await load();}catch(e){message(e.message,true);}finally{b.disabled=false;}});
$('run').addEventListener('click',()=>{$('confirm-shops').textContent=state.shops.filter(s=>selected.has(s.id)).map(s=>s.name).join('、');$('confirm').showModal();});
$('confirm-run').addEventListener('click',async()=>{busy=true;$('confirm-run').disabled=true;try{await api('run',{shops:[...selected],confirm:true});$('confirm').close();message('任务已提交，结果将自动更新。');await load();}catch(e){message(e.message,true);$('confirm').close();}finally{busy=false;$('confirm-run').disabled=false;selection();}});
document.addEventListener('click',e=>{const close=e.target.closest('[data-close]');if(close){$(close.dataset.close).close();return;}const b=e.target.closest('[data-run]');if(b)showDetails(b.dataset.run,b.dataset.sid||'',true);});
$('refresh').addEventListener('click',load);$('logout').addEventListener('click',async()=>{try{await api('logout',{});state=null;selected.clear();$('workspace').hidden=true;$('logout').hidden=true;$('gate').hidden=false;}catch(e){message(e.message,true);}});
setInterval(()=>{if(state)load();},5000);load();
