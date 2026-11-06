'use strict';
const $=id=>document.getElementById(id), base='/ozon-profit/api';
let csrf='',current=null,state='',orders=[],timer=null,previewSerial=0,editRevision=0,loading=false,loadSerial=0;
let weightMode='api',confirmedWeight='';
let parcelWeights={},dispatchChoice=0;
let costRange={from:'',to:''},costSerial=0,costOverviewSerial=0,imagesLoading=false,costOffset=0,costDetailOrders=[];
let titleLanguage='zh';
try{titleLanguage=localStorage.getItem('ozon-title-language')||'zh';}catch{}
if(!['zh','ru'].includes(titleLanguage))titleLanguage='zh';
const money=v=>v==null?'—':'¥'+Number(v).toFixed(2);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmtTime=v=>new Date(v*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
async function api(path,body){
  const response=await fetch(base+path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:body===undefined?undefined:JSON.stringify(body),credentials:'same-origin'});
  const result=await response.json().catch(()=>({error:'服务暂时没有响应，请稍后刷新'}));
  if(!response.ok)throw new Error(result.error||'读取失败');return result;
}
function notice(text){$('notice').textContent=text;$('notice').hidden=!text;}
function displayTitle(line){return titleLanguage==='zh'&&line?.name_zh?line.name_zh:(line?.name||'商品明细同步中');}
function thumbnail(line){const url=line?.image_url||'',ready=/^https:\/\//.test(url);return `<div class="thumbnail-slot" data-line-image="${esc(line?.id||'')}" data-image-missing="${!ready}">${ready?`<button type="button" class="thumb-button" data-image="${esc(url)}" data-caption="${esc(displayTitle(line))}" aria-label="查看商品大图"><img class="thumb" src="${esc(url)}" alt="${esc(displayTitle(line))}" loading="lazy" referrerpolicy="no-referrer"></button>`:'<div class="thumb placeholder" aria-label="商品图片同步中">图片同步中</div>'}</div>`;}
function bindThumbnails(scope){
  for(const button of scope.querySelectorAll('.thumb-button')){
    button.addEventListener('click',event=>{event.stopPropagation();openImage(button.dataset.image,button.dataset.caption);});
    button.addEventListener('keydown',event=>event.stopPropagation());
  }
  for(const img of scope.querySelectorAll('img.thumb')){
    const fallback=()=>{const placeholder=document.createElement('div');placeholder.className='thumb placeholder';placeholder.textContent='图片暂不可用';(img.closest('.thumb-button')||img).replaceWith(placeholder);};
    img.addEventListener('error',fallback,{once:true});if(img.complete&&img.naturalWidth===0)fallback();
  }
}
function translationHint(line){
  const reason=line?.translation_error||'';
  if(reason==='quota'||reason==='local-budget')return '翻译额度暂不可用 · 暂显原文';
  if(reason==='configuration')return '翻译服务需要处理账号或权限 · 暂显原文';
  if(reason==='rate-limit')return '翻译服务繁忙 · 稍后自动重试';
  if(['specification-mismatch','category-mismatch','unsupported-attributes','invalid-result','untranslated-result','source-too-long'].includes(reason))return '译名未通过校验 · 暂显原文';
  if(reason)return '译名暂未完成 · 稍后重试，可切换原文';
  return '中文翻译排队中 · 可切换原文';
}
function titleNote(line){
  if(titleLanguage!=='zh')return '';
  if(line?.translation_provider?.endsWith('/reference'))return '类目参考 · 保留原文，系统稍后自动重译';
  return !line?.name_zh&&!/[\u3400-\u9fff]/.test(line?.name||'')?translationHint(line):'';
}
function productTitle(line,linked=false){const title=esc(displayTitle(line)),sku=String(line?.sku??'');return linked&&/^[1-9]\d*$/.test(sku)?`<a class="product-title-link" href="https://www.ozon.ru/product/${sku}/" target="_blank" rel="noopener noreferrer" title="在新标签页查看 OZON 商品">${title}<span class="product-title-arrow" aria-hidden="true"> ↗</span></a>`:title;}
function productStateContent(s,detail=false){if(!s||!['removed','archived'].includes(s.state))return '';const label=s.state==='removed'?'已下架':'已归档',reason=s.state==='removed'?(s.reasons||[]).join('；'):'';const note=[reason,`检查于 ${fmtTime(s.checked)}`,s.stale?'状态较旧，等待同步':''].filter(Boolean).join(' · ');return `<span class="product-state ${s.state}" title="${esc(note)}">${label}</span>${detail?`<span class="product-state-detail">${esc(note)}</span>`:''}`;}
function productInfo(line,linked=false){const qty=line?.quantity??'—',multi=Number(qty)>1;return `<div class="product">${thumbnail(line)}<div class="product-info"><h3 title="${esc(line?.name||'')}">${productTitle(line,linked)}</h3>${productStateBadge(line?.product_status,linked)}${titleNote(line)?`<small class="translation-pending">${esc(titleNote(line))}</small>`:''}<div class="meta">${line?.offer_id?`<div class="meta-line"><span class="meta-label">货号</span> <span class="meta-code">${esc(line.offer_id)}</span></div>`:''}<div class="meta-line"><span class="meta-qty${multi?' is-multi':''}">数量 <b>${esc(qty)}</b></span>${line?.sku?`<span class="meta-dot">·</span><span class="meta-label">SKU</span> <span class="meta-code">${esc(line.sku)}</span>`:''}</div></div></div></div>`;}
function statusTone(status){const s=String(status||'').toLowerCase();return ['awaiting_packaging','awaiting_approve'].includes(s)?'packaging':s==='awaiting_deliver'?'deliver':s==='delivering'?'delivering':s==='delivered'?'delivered':['cancelled','canceled','returned'].includes(s)?'cancelled':'default';}
function statusBadgeHtml(order){const main=statusName(order.status),stage=order.status_label,hasSub=order.substatus&&stage&&stage!==main;return `<span class="status-badge status-${statusTone(order.status)}" title="${esc(statusHint(order))}"><b>${esc(main)}</b>${hasSub?`<em> · ${esc(stage)}</em>`:''}</span>`;}
function statusName(status){return ({awaiting_deliver:'待交运',awaiting_packaging:'待备货',delivering:'配送中',delivered:'已送达',cancelled:'已取消',canceled:'已取消',returned:'已退货',awaiting_approve:'待确认'})[status]||status||'状态待同步';}
function orderStatus(order){const main=statusName(order.status),stage=order.status_label;return order.substatus&&stage&&stage!==main?main+' · '+stage:main;}
function statusHint(order){return `OZON 主状态：${order.status}${order.substatus?'；子状态：'+order.substatus:''}；核对 ${fmtTime(order.status_checked_at||order.seen)}`;}
function openImage(url,caption){
  if(!/^https:\/\//.test(url))return;
  $('image-caption').textContent=caption||'商品大图';$('large-image').alt=caption||'商品大图';
  $('large-image').src=url;$('image-original').href=url;$('image-stage').classList.remove('zoomed');
  $('image-zoom').textContent='放大';$('image-zoom').setAttribute('aria-pressed','false');$('image-error').hidden=true;
  $('image-viewer').showModal();
}
$('image-close').addEventListener('click',()=>$('image-viewer').close());
$('image-viewer').addEventListener('click',event=>{if(event.target===$('image-viewer'))$('image-viewer').close();});
$('image-zoom').addEventListener('click',()=>{const zoomed=$('image-stage').classList.toggle('zoomed');$('image-zoom').textContent=zoomed?'适应屏幕':'放大';$('image-zoom').setAttribute('aria-pressed',String(zoomed));});
$('large-image').addEventListener('click',()=>$('image-zoom').click());
$('large-image').addEventListener('error',()=>{if($('image-viewer').open)$('image-error').hidden=false;});
function updateLanguageButtons(){for(const button of document.querySelectorAll('[data-title-language]'))button.setAttribute('aria-pressed',String(button.dataset.titleLanguage===titleLanguage));}
for(const button of document.querySelectorAll('[data-title-language]'))button.addEventListener('click',()=>{titleLanguage=button.dataset.titleLanguage;try{localStorage.setItem('ozon-title-language',titleLanguage);}catch{}updateLanguageButtons();renderOrders();});
updateLanguageButtons();
function fitStats(){
  const elements=[...document.querySelectorAll('.stats strong')];let size=matchMedia('(max-width:760px)').matches?16:24;
  const apply=()=>elements.forEach(el=>el.style.fontSize=size+'px');apply();
  while(elements.some(el=>el.scrollWidth>el.clientWidth)&&size>10){size--;apply();}
}
window.addEventListener('resize',()=>requestAnimationFrame(fitStats));
async function loadCostOverview(){
  const serial=++costOverviewSerial;
  try{
    const result=await api('/cost-statistics?'+new URLSearchParams({shop:$('shop').value}));
    if(serial!==costOverviewSerial)return;
    $('cost-card-total').textContent=money(result.statistics.total_cost);fitStats();
  }catch{if(serial===costOverviewSerial)$('cost-card-total').textContent='—';}
}
async function loadCosts(offset=0){
  const serial=++costSerial,range={...costRange};
  if(!$('cost-orders').children.length){
    $('cost-orders').innerHTML='<p class="cost-empty">正在读取关联订单…</p>';
    $('cost-prev').disabled=true;$('cost-next').disabled=true;
  }
  try{
    const result=await api('/cost-statistics?'+new URLSearchParams({shop:$('shop').value,date_from:range.from,date_to:range.to,details:'1',offset:String(offset)}));
    if(serial!==costSerial)return;
    const c=result.statistics;
    $('purchase-total').textContent=money(c.total_cost);$('purchase-cancelled').textContent=money(c.cancelled_returned_cost);
    $('purchase-active').textContent=money(c.active_cost);
    $('purchase-total-note').textContent=`已填采购价 ${c.filled_packages} 个包裹${c.missing_packages?` · ${c.missing_packages} 个包裹待补采购价`:''}`;
    const period=c.all_time?'全部时间':(c.date_from===c.date_to&&c.date_from?`${c.date_from}（单日）`:`${c.date_from||'最早记录'} 至 ${c.date_to||'最新记录'}`);
    const shop=$('shop').selectedOptions[0]?.textContent||'全部店铺';
    $('cost-scope').textContent=`${period} · ${shop} · 下单日期（北京时间）`;
    $('cost-error').hidden=true;
    costOffset=offset;
    const details=result.details||{orders:[],has_more:false};
    costDetailOrders=details.orders;
    const totalCount=details.total_count??c.filled_packages;
    if($('cost-orders-meta'))$('cost-orders-meta').textContent=`共 ${totalCount} 个已填包裹`;
    $('cost-orders').innerHTML=details.orders.length?details.orders.map(order=>{
      const hasCost=order.lines.some(line=>line.purchase_unit!=null&&String(line.purchase_unit).trim()!=='');
      const hasSale=order.lines.length&&order.lines.every(line=>line.price_override!=null||(line.currency==='CNY'&&line.unit_price!=null));
      const products=order.lines.filter(line=>line.purchase_unit!=null&&String(line.purchase_unit).trim()!=='');
      return `<article class="cost-order cost-simple"><div class="cost-simple-head"><span>${esc(order.shop_name)}</span><span>订单 ${esc(order.order_number||order.posting)}</span></div><div class="cost-simple-products">${products.map(line=>`<div class="cost-simple-product-row">${thumbnail(line)}<div class="cost-simple-product" title="${esc(displayTitle(line))}">${esc(displayTitle(line))}</div></div>`).join('')}</div><div class="cost-simple-amounts"><div><span>采购成本</span><strong>${money(hasCost?order.recorded_purchase_cost:null)}</strong></div><div><span>售价</span><strong>${money(hasSale?order.calculation.revenue:null)}</strong></div><div><span>利润</span><strong class="${order.calculation.profit!=null&&Number(order.calculation.profit)<0?'negative':''}">${money(order.calculation.profit)}</strong></div></div></article>`;
    }).join(''):'<p class="cost-empty">该时间范围暂无已填写采购成本的订单</p>';
    bindThumbnails($('cost-orders'));
    $('cost-page').textContent=`第 ${Math.floor(offset/20)+1} 页 · 共 ${totalCount} 个包裹`;
    $('cost-prev').disabled=offset===0;$('cost-next').disabled=!details.has_more;
  }catch(error){if(serial===costSerial){$('purchase-total').textContent='—';$('purchase-cancelled').textContent='—';$('cost-error').textContent=error.message;$('cost-error').hidden=false;}}
}
$('cost-prev').addEventListener('click',()=>loadCosts(Math.max(0,costOffset-20)));
$('cost-next').addEventListener('click',()=>loadCosts(costOffset+20));
$('cost-filter').addEventListener('submit',event=>event.preventDefault());
let rangeStart='',rangeEnd='',calendarMonth=null,renderedMonthKey='';
const isoDay=date=>date.toISOString().slice(0,10);
function chinaToday(){
  const parts=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date()).map(part=>[part.type,part.value]));
  return new Date(Date.UTC(Number(parts.year),Number(parts.month)-1,Number(parts.day)));
}
function syncCostPeriodButtons(period){
  for(const btn of document.querySelectorAll('[data-cost-period]')){
    const active=btn.dataset.costPeriod===period;
    btn.classList.toggle('active',active);
    btn.setAttribute('aria-pressed',String(active));
  }
}
function updateRangeEditLabel(){
  if(rangeStart&&!rangeEnd){
    $('cost-range-edit').textContent=`起点 ${rangeStart} → 点选结束日期`;
  }else if(costRange.from&&costRange.to){
    $('cost-range-edit').textContent=costRange.from===costRange.to?`${costRange.from}（单日）`:`${costRange.from} 至 ${costRange.to}`;
  }else{
    $('cost-range-edit').textContent='选择起止日期';
  }
  $('cost-range-edit').setAttribute('aria-expanded',String(!$('cost-calendar').hidden));
}
function renderCostCalendar(){
  const year=calendarMonth.getUTCFullYear(),month=calendarMonth.getUTCMonth(),monthKey=`${year}-${month}`;
  $('cost-calendar-month').textContent=`${year} 年 ${month+1} 月`;
  const activeStart=rangeStart||costRange.from,activeEnd=rangeStart?rangeEnd:costRange.to;
  if(renderedMonthKey!==monthKey||!$('cost-calendar-days').children.length){
    const leading=(calendarMonth.getUTCDay()+6)%7,count=new Date(Date.UTC(year,month+1,0)).getUTCDate();
    let markup='<span></span>'.repeat(leading);const today=isoDay(chinaToday());
    for(let day=1;day<=count;day++){
      const value=isoDay(new Date(Date.UTC(year,month,day)));
      markup+=`<button type="button" data-calendar-date="${value}" class="${value===today?'today':''}" aria-pressed="false" aria-label="${value}">${day}</button>`;
    }
    $('cost-calendar-days').innerHTML=markup;
    renderedMonthKey=monthKey;
  }
  for(const btn of $('cost-calendar-days').querySelectorAll('[data-calendar-date]')){
    const value=btn.dataset.calendarDate,selected=value===activeStart||value===activeEnd,between=Boolean(activeStart&&activeEnd&&value>activeStart&&value<activeEnd);
    btn.classList.toggle('selected',selected);
    btn.classList.toggle('in-range',between);
    btn.classList.remove('hover-range');
    btn.setAttribute('aria-pressed',String(selected));
  }
  const pickingEnd=Boolean(rangeStart&&!rangeEnd);
  $('cost-calendar-hint').textContent=pickingEnd?`已选开始：${rangeStart}，请点结束日期（再点同一天可查单日）`:'先点开始日期，再点结束日期，自动查询';
  if($('cost-single-day'))$('cost-single-day').hidden=!pickingEnd;
  updateRangeEditLabel();
}
function finishCalendarRange(start,end){
  [rangeStart,rangeEnd]=[start,end].sort();costRange={from:rangeStart,to:rangeEnd};
  $('cost-calendar').hidden=true;
  if($('cost-single-day'))$('cost-single-day').hidden=true;
  updateRangeEditLabel();loadCosts();
}
function openCostCalendar(){
  rangeStart='';rangeEnd='';const date=costRange.from?new Date(costRange.from+'T00:00:00Z'):chinaToday();
  calendarMonth=new Date(Date.UTC(date.getUTCFullYear(),date.getUTCMonth(),1));$('cost-calendar').hidden=false;renderCostCalendar();
}
$('cost-range-edit').addEventListener('click',event=>{
  event.stopPropagation();
  if(!$('cost-calendar').hidden){
    $('cost-calendar').hidden=true;rangeStart='';rangeEnd='';updateRangeEditLabel();return;
  }
  openCostCalendar();
});
$('cost-calendar').addEventListener('click',event=>event.stopPropagation());
if($('cost-single-day'))$('cost-single-day').addEventListener('click',()=>{if(rangeStart)finishCalendarRange(rangeStart,rangeStart);});
$('cost-month-prev').addEventListener('click',()=>{calendarMonth.setUTCMonth(calendarMonth.getUTCMonth()-1);renderCostCalendar();});
$('cost-month-next').addEventListener('click',()=>{calendarMonth.setUTCMonth(calendarMonth.getUTCMonth()+1);renderCostCalendar();});
$('cost-calendar-days').addEventListener('click',event=>{
  const day=event.target.closest('[data-calendar-date]')?.dataset.calendarDate;if(!day)return;
  if(!rangeStart||rangeEnd){rangeStart=day;rangeEnd='';renderCostCalendar();return;}
  finishCalendarRange(rangeStart,day);
});
$('cost-calendar-days').addEventListener('mouseover',event=>{
  if(!rangeStart||rangeEnd)return;
  const hoverDay=event.target.closest('[data-calendar-date]')?.dataset.calendarDate;if(!hoverDay)return;
  const [low,high]=[rangeStart,hoverDay].sort();
  for(const btn of $('cost-calendar-days').querySelectorAll('[data-calendar-date]')){
    const d=btn.dataset.calendarDate;
    btn.classList.toggle('hover-range',d>low&&d<high);
  }
});
$('cost-calendar-days').addEventListener('mouseleave',()=>{
  for(const btn of $('cost-calendar-days').querySelectorAll('.hover-range'))btn.classList.remove('hover-range');
});
function applyCostPeriod(period){
  $('cost-period').value=period;
  syncCostPeriodButtons(period);
  $('cost-date-fields').hidden=period!=='custom';$('cost-error').hidden=true;
  if(period==='custom'){openCostCalendar();return;}
  $('cost-calendar').hidden=true;
  costRange={from:'',to:''};rangeStart='';rangeEnd='';
  if(period!=='all'){
    const end=chinaToday(),start=new Date(end);
    if(period==='week')start.setUTCDate(start.getUTCDate()-6);
    if(period==='month')start.setUTCDate(1);
    costRange={from:isoDay(start),to:isoDay(end)};
  }
  loadCosts();
}
for(const btn of document.querySelectorAll('[data-cost-period]')){
  btn.addEventListener('click',()=>applyCostPeriod(btn.dataset.costPeriod));
}
$('cost-period').addEventListener('change',()=>applyCostPeriod($('cost-period').value));
$('cost-open').addEventListener('click',()=>{
  costRange={from:'',to:''};rangeStart='';rangeEnd='';
  $('cost-period').value='all';syncCostPeriodButtons('all');
  $('cost-date-fields').hidden=true;$('cost-calendar').hidden=true;
  updateRangeEditLabel();$('cost-error').hidden=true;
  if(!$('cost-orders').children.length){
    $('purchase-total').textContent='—';$('purchase-active').textContent='—';$('purchase-cancelled').textContent='—';
    $('purchase-total-note').textContent='正在读取采购成本…';$('cost-scope').textContent='全部时间';
  }
  $('cost-dialog').showModal();loadCosts();
});
for(const id of ['cost-close','cost-done'])$(id).addEventListener('click',()=>{$('cost-dialog').close();costSerial++;});
$('cost-dialog').addEventListener('click',event=>{
  if(event.target===$('cost-dialog')){$('cost-dialog').close();costSerial++;return;}
  if(!$('cost-calendar').hidden&&!event.target.closest('#cost-date-fields')){$('cost-calendar').hidden=true;rangeStart='';rangeEnd='';updateRangeEditLabel();}
});
$('cost-dialog').addEventListener('cancel',()=>{costSerial++;});
async function fillMissingImages(){
  if(document.hidden||$('workspace').hidden||imagesLoading||loading)return;
  const slots=[...document.querySelectorAll('.thumbnail-slot[data-image-missing="true"]')];
  const ids=[...new Set(slots.map(el=>el.dataset.lineImage).filter(id=>/^\d+$/.test(id)))].slice(0,500);
  if(!ids.length)return;imagesLoading=true;
  try{
    const result=await api('/images?'+new URLSearchParams({ids:ids.join(',')}));
    for(const image of result.images){
      if(!/^https:\/\//.test(image.image_url||''))continue;
      const lines=[...orders.flatMap(order=>order.lines),...costDetailOrders.flatMap(order=>order.lines),...(current?.lines||[])];
      for(const line of lines)if(line.id===image.id)line.image_url=image.image_url;
      const line=lines.find(value=>value.id===image.id);if(!line)continue;
      for(const slot of document.querySelectorAll(`.thumbnail-slot[data-line-image="${image.id}"][data-image-missing="true"]`)){
        const template=document.createElement('template');template.innerHTML=thumbnail(line);const replacement=template.content.firstElementChild;
        slot.replaceWith(replacement);bindThumbnails(replacement);
      }
    }
  }catch{}finally{imagesLoading=false;}
}
async function loadOrders(){
  const serial=++loadSerial;
  loadCostOverview();
  $('refresh').disabled=true;
  try{
    const query=new URLSearchParams({shop:$('shop').value,date:$('date').value,q:$('search').value});
    const [list,statistics,dashboard]=await Promise.all([api('/orders?'+query),api('/statistics?'+new URLSearchParams({shop:$('shop').value})),api('/analytics?'+new URLSearchParams({shop:$('shop').value,from:$('date').value,to:$('date').value,q:$('search').value}))]);
    if(serial!==loadSerial)return;
    orders=list.orders;
    const ready=orders.filter(o=>o.calculation.status==='ready');
    const eligible=ready.filter(o=>o.calculation.included_in_summary!==false);
    const summary=dashboard.summary;
    const total=Number(summary.profit);
    $('total').textContent=summary.eligible_ready?money(total):'—';
    $('total').classList.toggle('negative',total<0);
    const sales=Number(summary.sales);
    $('sales-total').textContent=money(sales);
    $('sales-detail').textContent=`${summary.orders} 个有效订单 · 按当前筛选`;
    $('home-margin').textContent=summary.margin==null?'—':summary.margin+'%';
    const stats=statistics.statistics;
    $('daily-average').textContent=stats.complete?stats.daily_average+' 单':'同步中';
    $('daily-detail').textContent=`近 7 天 ${stats.total_orders} 单 · 今日 ${stats.today_orders} 单`;
    if($('mobile-statistics-note'))$('mobile-statistics-note').textContent=`已运营 ${summary.operating_days} 天 · 今日 ${stats.today_orders} 单 · 近7天 ${stats.total_orders} 单`;
    $('statistics-note').textContent=`${stats.start_date} 至 ${stats.end_date} · 北京时间 · ${stats.shop_count} 家店${stats.complete?' · 日均 = 有效订单数 ÷ 7 天':' · 历史订单补查中，完整后显示日均，避免把缺失记录当成零单'}`;
    renderOrders();fitStats();notice('');
  }catch(error){if(serial===loadSerial)notice(error.message);}finally{if(serial===loadSerial)$('refresh').disabled=false;}
}
function renderOrders(){
  const cancelledOnly=$('cancelled-only')?.checked||false;
  const scoped=orders.filter(o=>['cancelled','canceled'].includes(String(o.status||'').toLowerCase())===cancelledOnly);
  const readyCount=scoped.filter(o=>o.calculation.status==='ready').length;
  $('pending').textContent=scoped.length-readyCount;$('ready').textContent=readyCount;
  const shown=scoped.filter(o=>!state||o.calculation.status===state);
  $('count').textContent=`${shown.length} 个${cancelledOnly?'已取消':''}包裹 · 北京时间`;
  $('orders').innerHTML=shown.length?shown.map(o=>{
    const c=o.calculation;
    const products=(o.lines.length?o.lines:[null]).map(line=>{
      const unitPrice=line?.unit_price==null?'待同步':line.currency==='CNY'?money(line.unit_price):`${esc(line.currency||'原币')} ${esc(line.unit_price)}`;
      return `<div class="order-product">${productInfo(line)}${o.lines.length>1?`<div class="order-unit-price">成交单价 <b>${unitPrice}</b></div>`:''}</div>`;
    }).join('');
    return `<article class="order-card" data-order="${o.id}" tabindex="0" role="button" aria-label="查看 ${esc(o.shop_name)} ${esc(o.posting)} 的利润"><div class="card-main"><div class="card-top"><div class="card-head-primary"><span class="shop-badge">${esc(o.shop_name)}</span><span class="card-posting">${esc(o.posting)}</span></div>${statusBadgeHtml(o)}<div class="card-head-time"><span>下单 ${fmtTime(o.created)}</span><span class="sync-time">核对 ${fmtTime(o.status_checked_at||o.seen)}</span></div></div><div class="order-products">${products}</div>${o.lines.length>1?`<div class="order-products-summary">共 ${o.lines.length} 种商品 · ${o.lines.reduce((sum,line)=>sum+Number(line.quantity||0),0)} 件</div>`:''}<div class="price-row"><div class="price-metrics"><div class="price-metric"><span class="price-label">订单价</span><strong class="price-val">${money(c.revenue)}</strong></div><span class="price-divider" aria-hidden="true"></span><div class="price-metric price-metric-cost"><span class="price-label">采购成本</span><strong class="price-val${c.purchase_cost==null?' is-missing':''}">${c.purchase_cost==null?'待填写':money(c.purchase_cost)}</strong></div></div>${purchaseReuseBadge(o)}</div></div><div class="card-profit"><div class="profit-summary">${c.profit==null?'<strong class="pending">待补资料</strong>':`<span class="profit-caption">预估利润</span><strong class="${Number(c.profit)<0?'negative':''}">${money(c.profit)}</strong><small class="profit-margin">销售利润率 ${esc(c.sales_margin??'—')}%</small>`}${c.included_in_summary===false?'<small class="profit-excluded">已取消 / 退货 · 不计合计</small>':''}</div><button type="button" tabindex="-1">${c.profit==null?'填写采购价':'查看 / 修改'} ↗</button></div></article>`;
  }).join(''):`<div class="empty">${cancelledOnly?'当前筛选下没有已取消订单':'当前筛选下还没有订单'}</div>`;
  bindThumbnails($('orders'));
  for(const card of $('orders').querySelectorAll('[data-order]')){
    card.addEventListener('click',()=>openOrder(Number(card.dataset.order)));
    card.addEventListener('keydown',event=>{if(event.target===card&&(event.key==='Enter'||event.key===' ')){event.preventDefault();openOrder(Number(card.dataset.order));}});
  }
}
function purchaseReuseBadge(order){
  const badges=[];
  if(order.dispatch_plan)badges.push(`<span class="purchase-reuse-badge">拆发 ${order.calculation.packages} 包</span>`);
  const reused=order.lines.filter(line=>line.purchase_origin==='inherited');
  if(reused.length){
    const sources=[...new Set(reused.map(line=>line.purchase_source_posting).filter(Boolean))];
    badges.push(`<span class="purchase-reuse-badge" title="${esc(sources.length?'来源订单：'+sources.join('、'):'沿用最近一次人工填写的同商品采购单价')}" aria-label="采购价沿用，可在详情修改">采购价沿用</span>`);
  }
  if(order.inputs?.weight_origin==='inherited'&&order.inputs?.weight_override!=null){
    const src=order.inputs?.weight_source_posting||order.weight_source_posting||'';
    badges.push(`<span class="purchase-reuse-badge" title="${esc(src?'来源订单：'+src:'沿用最近一次核对的同商品实测重量')}" aria-label="实测重量沿用，可在详情取消勾选">实测重量沿用</span>`);
  }
  return badges.join('');
}
function purchaseCostHint(line){
  if(line.purchase_origin==='inherited')return `<small id="reuse-note-${line.id}" class="purchase-reuse-note" title="${esc(line.purchase_source_posting?'来源订单：'+line.purchase_source_posting:'历史采购记录')}">采购价沿用${line.purchase_source_posting?' · 来源 '+esc(line.purchase_source_posting):''}，可修改</small>`;
  if(line.previous_cost!=null){
    const src=[line.previous_cost_shop&&line.previous_cost_shop!==current?.shop_name?line.previous_cost_shop:'',line.previous_cost_posting||''].filter(Boolean).join(' · ');
    return `<button type="button" class="suggestion" data-cost="${line.id}" data-value="${esc(line.previous_cost)}" title="${esc(src?'来源：'+src:'历史同商品采购价')}">参考上次 ${money(line.previous_cost)}${line.previous_cost_shop&&line.previous_cost_shop!==current?.shop_name?' ('+esc(line.previous_cost_shop)+')':''}，点击填入</button>`;
  }
  return '<small>出单后实际购买的单件成本</small>';
}
function updateWeightReuseUI(){
  const note=$('weight-reuse-note'),btn=$('weight-suggestion');
  if(!current)return;
  const p=current.inputs||{},pw=current.previous_weight;
  const isInherited=weightMode==='custom'&&p.weight_origin==='inherited'&&
    p.weight_override!=null&&Number($('weight').value)===Number(p.weight_override);
  if(note){
    if(isInherited){
      const src=p.weight_source_posting||pw?.posting||'';
      note.hidden=false;
      note.textContent=`实测重量沿用${src?' · 来源 '+src:''}，取消勾选可恢复 API 重量`;
    }else{
      note.hidden=true;
    }
  }
  if(btn){
    if(!isInherited&&pw&&pw.weight_g!=null&&(weightMode==='api'||Number($('weight').value)!==Number(pw.weight_g))){
      btn.hidden=false;
      btn.textContent=`参考上次实测 ${pw.weight_g} 克，点击填入`;
    }else{
      btn.hidden=true;
    }
  }
}
function payload(){
  const body={lines:current.lines.map(l=>({id:l.id,purchase_unit:$('cost-'+l.id).value,price_override:$('price-'+l.id).value})),inputs:{weight_override:weightMode==='custom'?$('weight').value:null,template:$('template').value,rate:$('rate').value,forwarder:$('forwarder').value,other_cost:$('other').value},weight_mode:weightMode,weight_confirmed:confirmedWeight!==''&&confirmedWeight===$('weight').value,expected_api_weight_g:current.calculation.api_weight_g,expected_weight_override:current.inputs.weight_override??null};
  if(current.parcel_comparison)Object.assign(body,{dispatch_choice:dispatchChoice,parcel_weights:parcelWeights});
  return body;
}
function updateWeightConfirmation(){
  if(!current)return;
  const value=$('weight').value,prior=current.inputs.weight_override;
  const changed=weightMode==='custom'&&(prior==null||Number(value)!==Number(prior));
  $('weight-custom').checked=weightMode==='custom';
  $('weight-confirm-row').hidden=!changed;$('weight-confirm').required=changed;
  const api=current.calculation.api_weight_g;
  $('weight-confirm-text').textContent=`我已核实，采用 ${value||'所填'} 克自定义计费重量${api==null?'':`（API ${Number(api)} 克）`}`;
  updateWeightReuseUI();
}
async function openOrder(id){
  if(loading)return;loading=true;
  try{
    current=(await api('/orders/'+id)).order;editRevision=0;previewSerial++;
    $('detail-shop').textContent=current.shop_name+' · '+current.posting+' · '+orderStatus(current);
    $('order-status').textContent='平台最新阶段：'+orderStatus(current)+'。'+statusHint(current)+(current.provider_status?'；物流商原文：'+current.provider_status:'');
    $('detail-title').textContent='采购成本与利润';
    $('product-fields').innerHTML=current.lines.map(l=>`<section class="product-block">${productInfo(l,true)}<div class="field-grid"><label class="cost-label">实际采购单价 · 元 / 件<input id="cost-${l.id}" type="number" min="0" step="any" inputmode="decimal" placeholder="请填写每件采购价" value="${esc(l.purchase_unit??'')}">${purchaseCostHint(l)}${l.quantity>1?`<small id="cost-subtotal-${l.id}"></small>`:''}</label><label>核算售价 · 元 / 件<input id="price-${l.id}" type="number" min="0" step="any" inputmode="decimal" placeholder="${esc(l.currency==='CNY'?l.unit_price:'请填写人民币售价')}" value="${esc(l.price_override??'')}"><small>订单成交价 ${esc(l.currency)} ${esc(l.unit_price??'待同步')}；留空则沿用</small>${l.quantity>1?`<small id="price-subtotal-${l.id}"></small>`:''}</label></div></section>`).join('');
    bindThumbnails($('product-fields'));
    const p=current.inputs,c=current.calculation;
    weightMode=p.weight_override==null?'api':'custom';confirmedWeight='';
    $('weight').readOnly=weightMode==='api';
    $('weight').value=p.weight_override??(c.api_weight_g==null?'':String(Number(c.api_weight_g)));
    $('weight-confirm').checked=false;
    $('weight').placeholder='请填写实际计费重量';
    updateWeightConfirmation();
    $('forwarder').value=p.forwarder??'2';$('other').value=p.other_cost??'0';$('rate').value=p.rate??'';$('template').value=p.template||'auto';
    $('save-status').textContent='修改后请保存';$('save').disabled=false;$('save').textContent='保存核算';
    showCalculation(c);
    updateLineSubtotals();
    parcelWeights={...current.dispatch_plan?.weights};
    dispatchChoice=current.dispatch_plan?.choice??0;
    showParcelComparison(current.parcel_comparison,true);
    for(const button of $('product-fields').querySelectorAll('[data-cost]'))button.addEventListener('click',()=>{$('cost-'+button.dataset.cost).value=button.dataset.value;changed();});
    $('detail').showModal();
  }catch(error){notice(error.message);}finally{loading=false;}
}
function showCalculation(c){
  const apiWeight=c.api_weight_g==null?null:Number(c.api_weight_g);
  $('api-weight').textContent=c.weight_source==='custom'
    ?`当前使用自定义计费重量 ${Number(c.custom_weight_g)} 克${apiWeight==null?'':`，覆盖 API 商品重量 ${apiWeight} 克`}。取消勾选“按实际称重修改”可恢复 API 重量。`
    :apiWeight!=null?`当前按 API 商品重量合计 ${apiWeight} 克预估。如需使用实包称重，请勾选下方“按实际称重修改”。`:'API 未提供完整重量，请勾选“按实际称重修改”并填写实际计费重量。';
  if(weightMode==='api')$('weight').value=c.api_weight_g==null?'':String(Number(c.api_weight_g));
  updateWeightConfirmation();
  $('profit').textContent=money(c.profit);$('profit').classList.toggle('negative',Number(c.profit)<0);
  $('margin').textContent=c.sales_margin==null?'待补齐资料':`销售利润率 ${c.sales_margin}%`;
  if(!$('rate').value)$('rate').placeholder=`当前档位默认 ${c.rate}%`;
  $('profit-title').textContent=c.packages>1?'整单预估利润':'本包裹预估利润';
  const freightLabel=c.packages>1?`运费 · ${c.packages} 包合计`:c.weight_g==null||Number(c.weight_g)<=0?'运费 · 待补计费重量':Number(c.weight_g)>25000?'运费 · 超出25千克范围':`运费 · ${$('template').value==='auto'?'自动匹配 · ':''}${c.template.name} / ${Number(c.weight_g)}克`;
  const values=[['核算售价合计',c.revenue],['采购成本合计',c.purchase_cost],[freightLabel,c.shipping],[`平台合计扣费 · ${c.rate}%`,c.platform_fee],[c.packages>1?`货代费 · ${c.packages} 包合计`:'货代费',c.forwarder],['其他成本',c.other_cost]];
  $('breakdown').innerHTML=values.map(([label,value])=>`<div class="break-row"><span>${esc(label)}</span><b>${money(value)}</b></div>`).join('');
  $('calc-note').textContent=[...c.issues,...c.warnings,c.profit_exact!=null?`未取整利润 ${c.profit_exact} 元；运费未取整 ${c.shipping_exact} 元。`:''].filter(Boolean).join('；');
}
function updateLineSubtotals(){
  for(const line of current.lines){
    if(line.quantity<=1)continue;
    const cost=$('cost-'+line.id).value;
    const price=$('price-'+line.id).value||(line.currency==='CNY'?line.unit_price:null);
    const subtotal=value=>value==null||value===''?'待填单价':
      `${money(value)}/件 × ${line.quantity} = ${money(Number(value)*line.quantity)}`;
    $('cost-subtotal-'+line.id).textContent='采购合计 · '+subtotal(cost);
    $('price-subtotal-'+line.id).textContent='售价合计 · '+subtotal(price);
  }
}
function showParcelComparison(data,reset=false){
  const panel=$('parcel-comparison');
  panel.hidden=!data;
  if(reset){
    panel.open=dispatchChoice>0;
    $('parcel-weights').innerHTML='';
  }
  if(!data)return;
  const {packages,plans,best,spread,forwarder}=data;
  let summary='补齐资料后比较';
  if(best!=null){
    if(Number(spread)===0)summary='合发与拆发费用相同';
    else if(best===0)summary=`合发预计省 ${money(spread)}`;
    else summary=`${plans[best].parts.reduce((n,part)=>n+part.count,0)} 包预计省 ${money(spread)}`;
  }
  if(dispatchChoice>0 && plans[dispatchChoice]){
    const count=plans[dispatchChoice].parts.reduce((n,part)=>n+part.count,0);
    summary=`已选拆发 ${count} 包`;
  }
  $('parcel-summary').textContent=summary;
  const restoreFocus=document.activeElement?.name==='dispatch-choice';
  $('parcel-plans').innerHTML=plans.map((plan,index)=>{
    const breakdown=plan.parts.map(({key,count})=>{
      const p=packages[key];
      return p.error?esc(p.error):`${esc(p.label)}${count>1?' × '+count:''} · ${money(p.revenue)} / ${Number(p.weight)} 克 · ${esc(p.tier)}`;
    }).join('<br>');
    return `<label class="parcel-plan${index===dispatchChoice?' is-selected':''}"><input type="radio" name="dispatch-choice" value="${index}" ${index===dispatchChoice?'checked':''}><span class="parcel-plan-body"><b>${esc(plan.label)}</b><small>${breakdown}</small></span><strong>${money(plan.cost)}</strong></label>`;
  }).join('');
  if(restoreFocus)$('parcel-plans').querySelector(':checked')?.focus();
  const fields=$('parcel-weights');
  if(!fields.childElementCount){
    fields.innerHTML=Object.entries(packages).filter(([key])=>key!=='all').map(([key,p])=>`<label>${esc(p.label)}<input type="text" inputmode="decimal" maxlength="18" data-parcel-weight="${esc(key)}" autocomplete="off" value="${esc(parcelWeights[key]??'')}"><small data-parcel-source="${esc(key)}"></small></label>`).join('');
  }
  const selectedKeys=plans[dispatchChoice]?.parts.map(part=>part.key)||[];
  $('parcel-weight-area').hidden=dispatchChoice===0;
  for(const input of fields.querySelectorAll('[data-parcel-weight]')){
    const key=input.dataset.parcelWeight,p=packages[key];
    input.closest('label').hidden=!selectedKeys.includes(key);
    input.placeholder=p.default_weight==null?'填写克数':String(Number(p.default_weight));
    fields.querySelector(`[data-parcel-source="${key}"]`).textContent=parcelWeights[key]?'手动核实':key.includes('-')?'留空沿用首包':'API 估重参考';
  }
  $('parcel-note').textContent=`货代费 ${money(forwarder)}/包。按实际发货方式选择并保存后计入利润。平台扣费沿用本单费率。`;
}
function queuePreview(){
  previewSerial++;
  if(!$('parcel-comparison').hidden)$('parcel-summary').textContent='更新中…';
  clearTimeout(timer);timer=setTimeout(preview,300);
}
function changed(){
  updateWeightConfirmation();
  updateLineSubtotals();
  editRevision++;$('save-status').textContent='有修改，尚未保存';
  $('profit').textContent='计算中…';$('margin').textContent='';
  queuePreview();
}
async function preview(){
  const serial=++previewSerial,id=current.id,revision=editRevision;
  try{
    const result=await api('/orders/'+id+'/preview',payload());
    if(serial===previewSerial&&revision===editRevision&&current.id===id){
      showCalculation(result.order.calculation);
      showParcelComparison(result.order.parcel_comparison);
    }
  }catch(error){
    if(serial===previewSerial){
      $('profit').textContent='—';$('margin').textContent='输入有误';$('calc-note').textContent=error.message;
      $('parcel-summary').textContent='试算待更新';
    }
  }
}
$('weight-custom').addEventListener('input',event=>{
  weightMode=event.target.checked?'custom':'api';confirmedWeight='';$('weight-confirm').checked=false;
  $('weight').readOnly=weightMode==='api';
  const fallbackCustom=current.inputs.weight_override??current.calculation.api_weight_g??'';
  $('weight').value=weightMode==='api'?(current.calculation.api_weight_g==null?'':String(Number(current.calculation.api_weight_g))):String(fallbackCustom);
  changed();if(weightMode==='custom')$('weight').focus();
});
$('weight-suggestion')?.addEventListener('click',()=>{
  const pw=current?.previous_weight;
  if(!pw||pw.weight_g==null)return;
  weightMode='custom';
  $('weight-custom').checked=true;
  $('weight').readOnly=false;
  $('weight').value=String(pw.weight_g);
  $('weight-confirm').checked=false;
  confirmedWeight='';
  changed();
});
$('weight-confirm').addEventListener('input',event=>{confirmedWeight=event.target.checked?$('weight').value:'';changed();});
$('edit-form').addEventListener('input',event=>{
  if(event.target.name==='dispatch-choice'){
    dispatchChoice=Number(event.target.value);
    changed();
    return;
  }
  const key=event.target.dataset.parcelWeight;
  if(key){
    const value=event.target.value.trim();
    if(value)parcelWeights[key]=value;
    else delete parcelWeights[key];
    changed();
    return;
  }
  if(event.target.id==='weight'){confirmedWeight='';$('weight-confirm').checked=false;}
  if(event.target.matches('input,select'))changed();
});
$('edit-form').addEventListener('change',event=>{
  if(event.target.tagName==='SELECT')changed();
});
$('edit-form').addEventListener('submit',async event=>{
  event.preventDefault();clearTimeout(timer);previewSerial++;const revision=editRevision;
  $('save').disabled=true;$('save-status').textContent='正在保存…';
  try{const result=await api('/orders/'+current.id,payload());
    if(revision===editRevision){
      current=result.order;showCalculation(current.calculation);
      showParcelComparison(current.parcel_comparison);
      for(const line of current.lines){const note=$('reuse-note-'+line.id);if(note&&line.purchase_origin!=='inherited')note.textContent='已保存本单采购价';}
      $('save-status').textContent='已保存，下次打开仍会保留';
    }else $('save-status').textContent='已保存先前内容；新修改请再次保存';
    await loadOrders();
  }catch(error){$('save-status').textContent=error.message;}finally{$('save').disabled=false;}
});
$('parcel-weights').addEventListener('keydown',event=>{
  if(event.key==='Enter')event.preventDefault();
});
$('close').addEventListener('click',()=>{if($('save').disabled)return;$('detail').close();clearTimeout(timer);previewSerial++;});
$('detail').addEventListener('cancel',event=>{if($('save').disabled)event.preventDefault();clearTimeout(timer);previewSerial++;});
$('refresh').addEventListener('click',loadOrders);
$('cancelled-only')?.addEventListener('change',renderOrders);
for(const id of ['shop','date'])$(id).addEventListener('change',loadOrders);
let searchTimer;$('search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(loadOrders,250);});
document.querySelectorAll('[data-state]').forEach(button=>button.addEventListener('click',()=>{state=button.dataset.state;document.querySelectorAll('[data-state]').forEach(b=>b.classList.toggle('active',b===button));renderOrders();}));
async function start(){
  if($('cancelled-only'))$('cancelled-only').checked=false;
  try{
    const params=new URLSearchParams(location.hash.slice(1)),token=params.get('k');
    let info;
    try{info=await api('/bootstrap');}
    catch(error){if(!token)throw error;await api('/login',{token});info=await api('/bootstrap');}
    if(token)history.replaceState(null,'',location.pathname);
    csrf=info.csrf;
    for(const shop of info.shops){const option=document.createElement('option');option.value=shop.shop;option.textContent=shop.shop_name;$('shop').append(option);}
    for(const t of info.templates){const option=document.createElement('option');option.value=t.id;option.textContent=t.name+' · '+t.scope;$('template').append(option);}
    $('gate').hidden=true;$('workspace').hidden=false;$('refresh').hidden=false;await loadOrders();window.dispatchEvent(new Event('compass-ready'));
  }catch(error){$('gate-status').textContent=error.message;}
}
start();
setInterval(()=>{if(!document.hidden&&!$('workspace').hidden&&!$('detail').open&&!$('cost-dialog').open&&!$('image-viewer').open&&!loading&&!$('refresh').disabled)loadOrders();},60000);
setInterval(fillMissingImages,10000);

function productStateBadge(s,detail=false){return s?.key?`<span data-product-state="${esc(s.key)}" data-state-detail="${detail}">${productStateContent(s,detail)}</span>`:productStateContent(s,detail);}
async function refreshProductBadges(){if(document.visibilityState!=='visible')return;try{const r=await api('/product-status');for(const node of document.querySelectorAll('[data-product-state]')){const s=r.statuses[node.dataset.productState];if(s)node.innerHTML=productStateContent(s,node.dataset.stateDetail==='true');}}catch{}}
setInterval(refreshProductBadges,60000);
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')refreshProductBadges();});
