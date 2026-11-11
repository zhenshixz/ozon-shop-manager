'use strict';
const $=id=>document.getElementById(id),API='/ozon-profit/api/operations/';
const weightPage=location.pathname.includes('/overweight/');
let state=null,csrf='',page=1,filter='pending',selected=new Map(),action='',busy=false,poll=null,loading=false;
const labels={pending:'待审核',archived:'已归档',retained:'确认保留',normal:'已正常',done:'完成',attention:'需关注',error:'失败',queued:'排队中',running:'运行中',interrupted:'已中断'};
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date=v=>v?new Date(typeof v==='number'?v*1000:v).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):'—';
const key=x=>x.shop+'|'+x.pid;
const safeURL=u=>{try{const p=new URL(u);return p.protocol==='https:'?p.href:'';}catch{return '';}};
const badge=(s,label)=>`<span class="badge ${s==='archived'||s==='done'?'success':s==='pending'||s==='running'||s==='queued'?'warning':s==='attention'||s==='error'||s==='interrupted'?'error':s==='retained'?'retained':'neutral'}">${esc(label||labels[s]||s)}</span>`;
async function api(path,body){
 const r=await fetch(API+path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json','X-CSRF-Token':csrf}:{},body:body?JSON.stringify(body):undefined});
 let data;try{data=await r.json();}catch{throw Error('服务暂不可用，请稍后刷新');}
 if(!r.ok)throw Error(data.error||'请求未完成，请重试');return data;
}
function notice(text,error=false){$('notice').textContent=text;$('notice').className='notice'+(error?' error':'');$('notice').hidden=false;}
function row(x){
 const sku=String(x.sku||''),url=/^[1-9]\d*$/.test(sku)?'https://www.ozon.ru/product/'+sku+'/':'';
 const image=safeURL(x.image),title=url?`<a href="${url}" target="_blank" rel="noopener noreferrer">${esc(x.name)} ↗</a>`:`<strong>${esc(x.name)}</strong><small>SKU 暂未生成</small>`;
 const check=x.state==='pending'?`<input type="checkbox" data-select="${esc(key(x))}" aria-label="选择 ${esc(x.shop_name+' '+x.offer_id)}" ${selected.has(key(x))?'checked':''}>`:'';
 return `<tr><td>${check}</td><td><div class="product-cell">${image?`<img src="${esc(image)}" class="thumb" alt="商品图片" loading="lazy" data-image="${esc(image)}" referrerpolicy="no-referrer">`:'<span class="thumb thumb-empty">暂无图片</span>'}<div>${title}</div></div></td><td><span class="shop-pill">${esc(x.shop_name)}</span><small>${esc(x.offer_id)}</small><small>SKU ${esc(x.sku||'—')}</small></td><td><span class="weight-value">${Number(x.weight_g).toLocaleString('zh-CN')} g</span><small>检查标准 &gt; ${esc(x.threshold)}g</small></td><td><span>上新 ${date(x.created_at)}</span><small>发现 ${date(x.first_seen)}</small><small>最近检查 ${date(x.last_seen)}</small></td><td>${badge(x.state)}</td></tr>`;
}
function paintSelection(){
 const n=selected.size;$('selected-note').textContent=`已选 ${n} 款`;
 $('archive-open').textContent=n?`批量归档 · ${n} 款`:'批量归档';
 $('archive-open').disabled=$('retain-open').disabled=!n||busy||activeJob();
 $('batch-note').textContent=n?'已选商品跨页保留；仅处理本次勾选项。':'勾选商品后，可确认保留或批量归档。';
 const visible=(state?.items||[]).filter(x=>x.state==='pending');
 $('select-page').checked=!!visible.length&&visible.every(x=>selected.has(key(x)));
 $('select-page').indeterminate=visible.some(x=>selected.has(key(x)))&&!$('select-page').checked;
 $('select-page').disabled=!visible.length||activeJob();
}
function activeJob(){return Boolean(state?.jobs.some(j=>['queued','running'].includes(j.state)));}
function paintJobs(){
 $('jobs').innerHTML=state.jobs.length?state.jobs.map(j=>{
  const total=j.result.reduce((a,r)=>a+((j.kind==='archive'?r.archived:r.checked)||0),0);
  const name=j.kind==='archive'?'人工归档':j.trigger==='daily'?'每日检查':j.trigger==='baseline'?'历史归档核对':j.trigger==='initial'?(j.state==='interrupted'?'首次检查（发布调整后已重跑）':'首次检查'):'手动检查';
  const result=j.result.map(r=>`<div class="job-result"><b>${esc(r.name)}</b> · ${r.checked} 款已核对${j.kind==='scan'?` · 待审核 ${r.pending} · 新发现 ${r.new}`:` · 已归档 ${r.archived}`} ${badge(r.state)}${(r.errors||[]).map(e=>`<p class="error-text">${esc(e)}</p>`).join('')}</div>`).join('');
  return `<details class="job" data-job="${esc(j.id)}"><summary>${badge(j.state)}<div><div class="job-title">${name} · ${j.shops.length} 家店 · ${j.kind==='archive'?'已归档':'已检查'} ${total} 款</div><div class="job-time">${date(j.created)}${j.kind==='scan'?` · 标准 &gt; ${j.threshold}g`:''}</div></div></summary><div class="job-results">${result||'<p>正在等待后台开始…</p>'}</div></details>`;
 }).join(''):'<div class="empty"><p>暂无运行记录。可以先选择店铺，立即检查。</p></div>';
}
function paint(){
 const counts=state.counts,rule=state.rule;
 $('new-products-count').textContent=state.new_products_today??'—';
 $('pending-count').textContent=counts.pending||0;$('nav-count').textContent=counts.pending||0;
 $('archived-count').textContent=counts.archived||0;
 const latest=state.jobs.find(j=>j.kind==='scan');
 $('checked-count').textContent=latest?latest.result.reduce((n,r)=>n+r.checked,0).toLocaleString('zh-CN'):'—';
 $('checked-note').textContent=latest?`${date(latest.created)} · ${labels[latest.state]||latest.state}`:'等待首次检查';
 $('daily-time').textContent=String(state.digest_hour).padStart(2,'0')+':00';
 $('rule-short').textContent=rule.manual+'g';
 $('daily-note').textContent=`今日自动标准 > ${rule.automatic}g`;
 $('home-pending').textContent=(counts.pending||0)+' 款待审核';
 $('home-rule').textContent=`手动检查 > ${rule.manual}g · 自动检查 > ${rule.automatic}g`;
 const promo=state.promotions;
 $('promo-summary').textContent=promo?.unavailable?'打开查看促销运行情况':`${promo?.enabled?'每日计划已启用':'每日计划已暂停'}${promo?.times?.length?' · '+promo.times.join(' / '):''}`;
 $('scan-open').disabled=busy||activeJob();$('scan-open').textContent=activeJob()?'任务运行中…':'立即检查';
 const existing=$('shop-filter').value;
 if($('shop-filter').options.length!==state.shops.length+1){
  $('shop-filter').innerHTML='<option value="">全部店铺</option>'+state.shops.map(s=>`<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('');$('shop-filter').value=existing;
 }
 for(const x of state.items){const old=selected.get(key(x));if(old&&(old.version!==x.version||x.state!=='pending'))selected.delete(key(x));}
 $('products').innerHTML=state.items.map(row).join('');$('total').textContent=state.total.toLocaleString('zh-CN');
 $('empty').hidden=state.total>0;$('page-number').textContent=page;
 $('pagination-note').textContent=state.total?`共 ${state.total} 款 · 第 ${(page-1)*50+1}—${Math.min(page*50,state.total)} 款`:'共 0 款';
 $('prev').disabled=page<=1;$('next').disabled=page*50>=state.total;
 const opened=[...document.querySelectorAll('details.job[open]')].map(n=>n.dataset.job);
 paintJobs();for(const id of opened){const n=document.querySelector(`[data-job="${id}"]`);if(n)n.open=true;}
 $('updated').textContent='更新于 '+date(state.now);paintSelection();
 $('loading').hidden=true;$('workspace').hidden=false;
 clearTimeout(poll);poll=setTimeout(()=>{if(!document.hidden&&!document.querySelector('dialog[open]'))load();else poll=setTimeout(()=>load(),10000);},activeJob()?3500:30000);
}
async function load(){
 if(loading)return;loading=true;
 try{
  const params=new URLSearchParams({state:filter,page:String(page),shop:$('shop-filter').value,q:$('search').value.trim(),date:$('date-filter').value});
  state=await api('state?'+params);csrf=state.csrf;paint();
 }catch(e){notice(e.message,true);$('loading').hidden=true;clearTimeout(poll);poll=setTimeout(load,15000);}finally{loading=false;}
}
function openConfirm(kind){
 if(!selected.size)return;action=kind;
 $('confirm-title').textContent=kind==='archive'?'归档所选商品？':'确认保留所选商品？';
 $('confirm-description').textContent=kind==='archive'?'确认后将向 OZON 申请归档这些商品。执行前会重新核对重量，完成后逐款验证归档状态。':'保留当前重量版本，不再每天重复提醒；重量变化后会重新进入审核。';
 const groups={};for(const x of selected.values())groups[x.shop_name]=(groups[x.shop_name]||0)+1;
 $('confirm-summary').textContent=`共 ${selected.size} 款 · `+Object.entries(groups).map(([n,c])=>`${n} ${c}款`).join('、');
 $('confirm-items').innerHTML=[...selected.values()].map(x=>`<div class="confirm-item">${esc(x.name)}<small>${esc(x.shop_name)} · ${esc(x.offer_id)} · ${x.weight_g}g</small></div>`).join('');
 $('confirm-action').textContent=kind==='archive'?'确认归档':'确认保留';$('confirm-action').className=kind==='archive'?'danger':'primary';
 $('confirm-dialog').showModal();
}
async function post(path,data,message,dialog){
 if(busy)return;busy=true;
 const button=dialog==='scan-dialog'?$('scan-confirm'):dialog==='confirm-dialog'?$('confirm-action'):$('rule-form').querySelector('button[type=submit]');button.disabled=true;
 try{await api(path,data);if(dialog)$(dialog).close();notice(message);if(path==='archive'||path==='retain')selected.clear();await load();}
 catch(e){notice(e.message,true);if(dialog)$(dialog).close();}finally{busy=false;button.disabled=false;if(state)paint();}
}
document.querySelectorAll('[data-close]').forEach(b=>b.addEventListener('click',()=>$(b.dataset.close).close()));
document.querySelectorAll('dialog').forEach(d=>d.addEventListener('click',e=>{if(e.target===d){const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close();}}));
$('settings-open').onclick=()=>{if(!state)return;$('rule-input').value=state.rule.manual;$('automatic-rule-note').textContent=`今日自动检查使用 ${state.rule.automatic}g。新设置从北京时间次日生效；已有检查结果可点击“立即检查”更新。`;$('settings-dialog').showModal();};
$('rule-form').onsubmit=e=>{e.preventDefault();const value=Number($('rule-input').value);if(!Number.isInteger(value)||value<1||value>1000000)return;post('rule',{weight:value},'标准已保存：手动检查立即使用，自动检查从北京时间次日使用。','settings-dialog');};
$('scan-open').onclick=()=>{if(!state)return;$('scan-rule-note').textContent=`本次按 API 单件重量 > ${state.rule.manual}g 检查。`;$('scan-all').checked=true;$('scan-shops').innerHTML=state.shops.map(s=>`<label class="shop-option"><input type="checkbox" value="${esc(s.id)}" checked> ${esc(s.name)}</label>`).join('');$('scan-dialog').showModal();};
$('scan-all').onchange=e=>document.querySelectorAll('#scan-shops input').forEach(x=>x.checked=e.target.checked);
$('scan-shops').onchange=()=>{const checks=[...document.querySelectorAll('#scan-shops input')];$('scan-all').checked=checks.every(x=>x.checked);$('scan-all').indeterminate=checks.some(x=>x.checked)&&!$('scan-all').checked;};
$('scan-confirm').onclick=()=>{const ids=[...document.querySelectorAll('#scan-shops input:checked')].map(x=>x.value);if(!ids.length)return;post('scan',{shops:ids},'检查已开始，后台运行中，可稍后查看结果。','scan-dialog');};
$('select-page').onchange=e=>{for(const x of state.items.filter(x=>x.state==='pending')){if(e.target.checked){if(selected.size>=500){notice('单次最多选择500款，请分批处理。',true);break;}selected.set(key(x),x);}else selected.delete(key(x));}paint();};
$('products').onchange=e=>{const k=e.target.dataset.select;if(!k)return;const x=state.items.find(x=>key(x)===k);if(e.target.checked){if(selected.size>=500){e.target.checked=false;notice('单次最多选择500款，请分批处理。',true);return;}selected.set(k,x);}else selected.delete(k);paintSelection();};
$('products').onclick=e=>{if(e.target.dataset.image){$('large-image').src=e.target.dataset.image;$('image-dialog').showModal();}};
$('products').addEventListener('error',e=>{if(e.target.tagName==='IMG'){const placeholder=document.createElement('span');placeholder.className='thumb thumb-empty';placeholder.textContent='暂无图片';e.target.replaceWith(placeholder);}},true);
$('clear-selection').onclick=()=>{selected.clear();paint();};
$('archive-open').onclick=()=>openConfirm('archive');$('retain-open').onclick=()=>openConfirm('retain');
$('confirm-action').onclick=()=>post(action,{selection:[...selected.values()].map(x=>({shop:x.shop,pid:x.pid,version:x.version})),confirm:true},action==='archive'?'归档任务已提交，完成后将逐款更新状态。':'已确认保留当前重量版本。','confirm-dialog');
document.querySelectorAll('[data-state]').forEach(b=>b.onclick=()=>{filter=b.dataset.state;page=1;selected.clear();document.querySelectorAll('[data-state]').forEach(x=>x.classList.toggle('active',x===b));load();});
let searchTimer;$('search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{page=1;selected.clear();load();},350);};
for(const id of ['shop-filter','date-filter'])$(id).onchange=()=>{page=1;selected.clear();load();};
$('reset-filter').onclick=()=>{$('shop-filter').value='';$('date-filter').value='';$('search').value='';page=1;selected.clear();load();};
$('prev').onclick=()=>{if(page>1){page--;load();}};$('next').onclick=()=>{page++;load();};$('reload').onclick=load;
if(weightPage){$('page-title').textContent='超重排查';$('breadcrumb').textContent='超重排查';$('eyebrow').textContent='PRODUCT WEIGHT REVIEW';$('page-description').textContent='每日检查 API 单件重量，审核后再归档。';document.title='超重排查 · OZON 店铺助手';}
$('home-view').hidden=weightPage;$('weight-view').hidden=!weightPage;
document.querySelector(`[data-nav="${weightPage?'weight':'home'}"]`).classList.add('active');
load();
