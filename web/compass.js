(()=>{
const el=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),cash=v=>v==null?'—':'¥'+Number(v).toFixed(2);
let data=null,rank='quantity',dimension='products',shopContext=null,shopReturn=null,profitMode='unit_profit',limit=10,serial=0,homeScroll=0,chart='orders',selected=null,rangeStart='',month=new Date(),shopCounts={};
const day=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
const todayStr=()=>new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());

const panel=document.createElement('section');panel.id='compass';panel.hidden=true;
panel.innerHTML=`
<div class="cp-heading">
  <div class="cp-head-left">
    <button id="cp-back" type="button" class="secondary cp-back-btn"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M10 3.5L5.5 8 10 12.5" stroke-linecap="round" stroke-linejoin="round"/></svg><span id="cp-back-label">返回首页</span></button>
    <div class="cp-title-wrap">
      <h2 id="cp-title">数据罗盘</h2>
      <span id="cp-context-tag" class="cp-context-tag">全店经营分析</span>
    </div>
  </div>
  <div class="cp-head-right">
    <button id="cp-shop-overview" type="button" class="cp-shop-overview-btn" hidden><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><rect x="2.5" y="2.5" width="11" height="11" rx="2.5"/><path d="M5.5 6h5M5.5 8.5h5M5.5 11h3" stroke-linecap="round"/></svg><span>店铺概况 ›</span></button>
  </div>
</div>
<div class="cp-control-bar">
  <div class="cp-dimensions" role="tablist" aria-label="统计维度">
    <button type="button" role="tab" data-dimension="products" aria-selected="true">
      <svg viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M9 2.2L3 5.4v7.2l6 3.2 6-3.2V5.4L9 2.2z" stroke-linejoin="round"/><path d="M3 5.4L9 8.6l6-3.2M9 8.6v7.2" stroke-linecap="round" stroke-linejoin="round"/></svg>
      <span>商品</span>
      <em id="cp-dim-products-count" class="cp-dim-count"></em>
    </button>
    <button type="button" role="tab" data-dimension="shops" aria-selected="false">
      <svg viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M3.2 7.8V15h11.6V7.8M2.2 7.8l1.5-4.3h10.6l1.5 4.3M2.2 7.8c0 2 3.4 2 3.4 0 0 2 3.4 2 3.4 0 0 2 3.4 2 3.4 0 0 2 3.4 2 3.4 0M7.2 15v-4.2h3.6V15" stroke-linecap="round" stroke-linejoin="round"/></svg>
      <span>店铺</span>
      <em id="cp-dim-shops-count" class="cp-dim-count"></em>
    </button>
  </div>
  <div class="cp-filters">
    <div class="cp-shop-select-wrap">
      <select id="cp-shop" aria-label="店铺"></select>
    </div>
    <div class="cp-period-tabs" role="group" aria-label="统计时间快捷切换">
      <button type="button" data-period="30" class="active" aria-pressed="true">近30天</button>
      <button type="button" data-period="7" aria-pressed="false">近7天</button>
      <button type="button" data-period="1" aria-pressed="false">今日</button>
      <button type="button" data-period="custom" aria-pressed="false">自定义</button>
    </div>
    <select id="cp-period" class="sr-only" aria-label="统计时间"><option value="30">近30天</option><option value="7">近7天</option><option value="1">今日</option><option value="custom">自定义日期</option></select>
  </div>
</div>
<div id="cp-custom-bar" class="cp-custom-bar" hidden>
  <button id="cp-range" type="button" class="secondary cp-range-btn" hidden>选择起止日期</button>
  <div id="cp-calendar" class="cp-calendar-pop" hidden></div>
</div>
<div class="cp-overview-card">
  <div id="cp-summary"></div>
  <details class="cp-trends">
    <summary>
      <div class="cp-range-meta">
        <p id="cp-coverage" class="cp-coverage">正在读取统计…</p>
        <span class="cp-meta-dot" aria-hidden="true">·</span>
        <p id="cp-status" role="status"></p>
      </div>
      <span class="cp-trend-pill"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M2 12l3.8-3.8 2.6 2.2L14 4.5" stroke-linecap="round" stroke-linejoin="round"/><path d="M10.5 4.5H14V8" stroke-linecap="round" stroke-linejoin="round"/></svg><span>经营趋势</span><i class="cp-trend-chevron" aria-hidden="true"></i></span>
    </summary>
    <div class="cp-trends-body">
      <div class="cp-trends-head">
        <div class="cp-chart-buttons" role="group" aria-label="趋势指标">
          <button type="button" data-chart="orders" class="active" aria-pressed="true">订单量</button>
          <button type="button" data-chart="sales" aria-pressed="false">销售额</button>
          <button type="button" data-chart="profit" aria-pressed="false">利润</button>
        </div>
        <span id="cp-chart-meta" class="cp-chart-meta"></span>
      </div>
      <div id="cp-chart"></div>
    </div>
  </details>
</div>
<div class="cp-rank-toolbar">
  <div class="tabs cp-ranks" role="tablist" aria-label="榜单切换">
    <button type="button" data-rank="quantity" class="active"><span>销量榜</span><em id="cp-count-quantity" class="cp-tab-count"></em></button>
    <button type="button" data-rank="profit"><span>利润榜</span><em id="cp-count-profit" class="cp-tab-count"></em></button>
    <button type="button" data-rank="loss"><span>亏损榜</span><em id="cp-count-loss" class="cp-tab-count"></em></button>
  </div>
  <div id="cp-profit-sort" class="cp-sort-toolbar" hidden>
    <span class="cp-sort-label">利润排序</span>
    <div class="cp-profit-switch" role="group" aria-label="利润排名方式">
      <button type="button" data-profit-mode="unit_profit" aria-pressed="true" title="按平均单件预估利润排序">单件</button>
      <button type="button" data-profit-mode="profit" aria-pressed="false" title="按累计预估利润排序">累计</button>
    </div>
  </div>
</div>
<div id="cp-list"></div>
<button id="cp-more" type="button" class="secondary cp-more-btn" hidden>加载更多</button>`;
document.querySelector('.shell').append(panel);

const modal=document.createElement('dialog');modal.className='cp-product';
modal.innerHTML=`<div class="dialog-head"><div class="cp-dialog-title-wrap"><span id="cp-modal-eyebrow" class="eyebrow">数据罗盘</span><h2 id="cp-modal-title">商品详情</h2></div><button id="cp-close" type="button" class="icon-button" aria-label="关闭">×</button></div><div id="cp-product-body" class="dialog-body"></div>`;
document.body.append(modal);
modal.addEventListener('click',e=>{if(e.target===modal)modal.close();});

/* Custom Shop Picker Popover for both Data Compass (#cp-shop) and Home Page (#shop) */
function closeAllShopPickers(exceptId=''){
  document.querySelectorAll('.shop-picker-wrap.is-open').forEach(w=>{
    if(exceptId&&w.dataset.pickerFor===exceptId)return;
    w.classList.remove('is-open');
    const pop=w.querySelector('.shop-picker-pop'),btn=w.querySelector('.shop-picker-btn');
    if(pop)pop.hidden=true;
    if(btn)btn.setAttribute('aria-expanded','false');
  });
}

function syncShopPicker(selectId){
  const sel=el(selectId);if(!sel)return;
  const wrap=sel.closest('.shop-picker-wrap');if(!wrap)return;
  const btn=wrap.querySelector('.shop-picker-btn'),pop=wrap.querySelector('.shop-picker-pop');
  if(!btn||!pop)return;
  const opts=[...sel.options];
  const curOpt=opts.find(o=>o.value===sel.value)||opts[0];
  const hasShop=Boolean(sel.value);
  const isCompass=selectId==='cp-shop';
  wrap.classList.toggle('has-selection',hasShop);
  btn.innerHTML=`<span class="shop-picker-label"><svg class="shop-picker-icon" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M3.2 7.8V15h11.6V7.8M2.2 7.8l1.5-4.3h10.6l1.5 4.3M2.2 7.8c0 2 3.4 2 3.4 0 0 2 3.4 2 3.4 0 0 2 3.4 2 3.4 0 0 2 3.4 2 3.4 0M7.2 15v-4.2h3.6V15" stroke-linecap="round" stroke-linejoin="round"/></svg><span class="shop-picker-text">${esc(curOpt?.textContent||'全部店铺')}</span></span><i class="shop-picker-chevron" aria-hidden="true"></i>`;
  const shopOpts=opts.filter(o=>o.value!=='');
  const allLabel=isCompass&&shopContext?'全部店铺（返回店铺榜）':'全部店铺';
  const checkSvg=`<svg class="shop-pop-check" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="M3.5 8.2l3 3 6-6.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  pop.innerHTML=`
    <div class="shop-pop-head"><span>选择店铺</span><small>共 ${shopOpts.length} 家店铺</small></div>
    <button type="button" class="shop-pop-all ${!hasShop?'active':''}" data-shop-val="" role="option" aria-selected="${!hasShop}">
      <span class="shop-pop-all-left"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><rect x="2" y="2" width="5" height="5" rx="1.2"/><rect x="9" y="2" width="5" height="5" rx="1.2"/><rect x="2" y="9" width="5" height="5" rx="1.2"/><rect x="9" y="9" width="5" height="5" rx="1.2"/></svg><span>${esc(allLabel)}</span></span>
      ${!hasShop?checkSvg:'<span class="shop-pop-all-hint">汇总全部</span>'}
    </button>
    <div class="shop-pop-grid" role="listbox" aria-label="店铺列表">
      ${shopOpts.map(o=>{
        const active=o.value===sel.value;
        const cnt=isCompass&&shopCounts[o.value]!=null?shopCounts[o.value]:null;
        return `<button type="button" class="shop-pop-item ${active?'active':''}" data-shop-val="${esc(o.value)}" role="option" aria-selected="${active}">
          <span class="shop-item-main"><i class="shop-item-dot" aria-hidden="true"></i><b>${esc(o.textContent)}</b></span>
          ${active?checkSvg:(cnt!=null?`<em class="shop-item-cnt">${cnt}单</em>`:'')}
        </button>`;
      }).join('')}
    </div>`;
  pop.querySelectorAll('[data-shop-val]').forEach(item=>{
    item.onclick=e=>{
      e.stopPropagation();
      const val=item.dataset.shopVal;
      closeAllShopPickers();
      if(sel.value===val)return;
      sel.value=val;
      syncShopPicker(selectId);
      if(typeof sel.onchange==='function')sel.onchange();
      else sel.dispatchEvent(new Event('change'));
    };
  });
}

function initShopPicker(selectId){
  const sel=el(selectId);if(!sel)return;
  let wrap=sel.closest('.cp-shop-select-wrap, .home-shop-select-wrap');
  if(!wrap){
    wrap=document.createElement('div');
    wrap.className='home-shop-select-wrap';
    sel.parentNode.insertBefore(wrap,sel);
    wrap.append(sel);
  }
  wrap.classList.add('shop-picker-wrap');
  wrap.dataset.pickerFor=selectId;
  sel.classList.add('sr-only');
  const btn=document.createElement('button');
  btn.type='button';
  btn.className='shop-picker-btn';
  btn.id=selectId+'-trigger';
  btn.setAttribute('aria-haspopup','listbox');
  btn.setAttribute('aria-expanded','false');
  const pop=document.createElement('div');
  pop.className='shop-picker-pop';
  pop.id=selectId+'-pop';
  pop.hidden=true;
  wrap.append(btn,pop);
  btn.onclick=e=>{
    e.stopPropagation();
    const opening=pop.hidden;
    closeAllShopPickers(selectId);
    if(selectId==='cp-shop'&&!el('cp-calendar').hidden){el('cp-calendar').hidden=true;rangeStart='';}
    syncShopPicker(selectId);
    pop.hidden=!opening;
    wrap.classList.toggle('is-open',opening);
    btn.setAttribute('aria-expanded',String(opening));
  };
  pop.onclick=e=>e.stopPropagation();
  sel.addEventListener('change',()=>syncShopPicker(selectId));
  syncShopPicker(selectId);
}

initShopPicker('cp-shop');
initShopPicker('shop');

function fitCompassSummary(){
  if(panel.hidden)return;
  const elements=[...panel.querySelectorAll('#cp-summary strong')];
  if(!elements.length)return;
  let size=matchMedia('(max-width:760px)').matches?16:21;
  const apply=()=>elements.forEach(node=>node.style.fontSize=size+'px');apply();
  while(elements.some(node=>node.scrollWidth>node.clientWidth)&&size>11){size--;apply();}
}
window.addEventListener('resize',()=>requestAnimationFrame(fitCompassSummary));

function syncPeriodUI(){
  const val=el('cp-period').value,custom=val==='custom';
  panel.querySelectorAll('[data-period]').forEach(b=>{
    const active=b.dataset.period===val;
    b.classList.toggle('active',active);
    b.setAttribute('aria-pressed',String(active));
  });
  el('cp-custom-bar').hidden=!custom;
  el('cp-range').hidden=!custom;
  if(!custom)el('cp-calendar').hidden=true;
}

function openCompass(){
  homeScroll=scrollY;panel.hidden=false;el('workspace').hidden=true;document.querySelector('header').hidden=true;
  el('cp-shop').innerHTML=el('shop').innerHTML;el('cp-shop').value=el('shop').value;
  syncShopPicker('cp-shop');
  syncPeriodUI();syncScope();window.scrollTo(0,0);load();
}
function closeCompass(){
  closeAllShopPickers();
  panel.hidden=true;el('workspace').hidden=false;document.querySelector('header').hidden=false;
  if(typeof fitStats==='function')fitStats();
  window.scrollTo(0,homeScroll);
}
el('compass-open').onclick=()=>{history.pushState({compass:true},'','#compass');openCompass();};
el('cp-back').onclick=()=>{if(shopContext||history.state?.compass)history.back();else{history.replaceState(null,'',location.pathname);closeCompass();}};
window.addEventListener('popstate',()=>{
  closeAllShopPickers();
  if(location.hash==='#compass'){
    if(shopContext&&!history.state?.shopId)return restoreShopParent();
    if(history.state?.shopId){const id=history.state.shopId;return enterShop({id},false);}
    openCompass();
  }else{
    shopContext=null;shopReturn=null;syncScope();closeCompass();
  }
});

function syncScope(){
  const scoped=Boolean(shopContext),name=[...el('cp-shop').options].find(o=>o.value===shopContext)?.textContent||'';
  el('cp-title').textContent=scoped?name+' · 商品表现':'数据罗盘';
  const backLabel=el('cp-back-label');
  if(backLabel)backLabel.textContent=scoped?'店铺榜':'返回首页';
  else el('cp-back').textContent=scoped?'‹ 店铺榜':'‹ 返回首页';
  const tag=el('cp-context-tag');
  if(tag){
    tag.textContent=scoped?'单店商品排行与核算':'商品与店铺经营分析';
    tag.classList.toggle('is-scoped',scoped);
  }
  panel.querySelector('.cp-dimensions').hidden=scoped;
  el('cp-shop-overview').hidden=!scoped;
  panel.classList.toggle('cp-shop-context',scoped);
  syncShopPicker('cp-shop');
}

function enterShop(p,push=true){
  if(!shopContext)shopReturn={dimension,rank,profitMode,limit,shop:el('cp-shop').value,period:el('cp-period').value,from:el('cp-range').dataset.from,to:el('cp-range').dataset.to,rangeText:el('cp-range').textContent,scroll:scrollY};
  shopContext=p.id;dimension='products';el('cp-shop').value=p.id;
  if(rank==='profit')profitMode='profit';
  if(push)history.pushState({compass:true,shopId:p.id},'','#compass');
  syncScope();window.scrollTo(0,0);load();
}

function restoreShopParent(){
  const previous=shopReturn;shopContext=null;shopReturn=null;
  if(!previous){syncScope();openCompass();return;}
  dimension=previous.dimension;rank=previous.rank;profitMode=previous.profitMode;el('cp-shop').value=previous.shop;
  el('cp-period').value=previous.period;el('cp-range').dataset.from=previous.from||'';el('cp-range').dataset.to=previous.to||'';el('cp-range').textContent=previous.rangeText;
  syncPeriodUI();el('cp-calendar').hidden=true;
  panel.querySelectorAll('[data-dimension]').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.dimension===dimension)));
  panel.querySelectorAll('[data-rank]').forEach(b=>b.classList.toggle('active',b.dataset.rank===rank));
  syncScope();load().then(()=>{limit=previous.limit;render();window.scrollTo(0,previous.scroll);});
}

el('cp-shop-overview').onclick=()=>{const store=data?.shops.find(s=>s.id===shopContext);if(store)showShop(store);};

function bounds(){
  if(el('cp-period').value==='custom')return {from:el('cp-range').dataset.from||'',to:el('cp-range').dataset.to||''};
  let end=new Date(),start=new Date();start.setDate(start.getDate()-Number(el('cp-period').value)+1);
  return {from:day(start),to:day(end)};
}

function filterItems(dim,rk){
  if(!data||!data[dim])return [];
  return data[dim].filter(p=>rk==='quantity'||(p.ready>0&&(rk!=='loss'||Number(p.profit)<0)));
}

function updateCounts(){
  if(!data)return;
  const prodCount=el('cp-dim-products-count'),shopCount=el('cp-dim-shops-count');
  if(prodCount)prodCount.textContent=data.products?.length??0;
  if(shopCount)shopCount.textContent=data.shops?.length??0;
  for(const rk of ['quantity','profit','loss']){
    const badge=el('cp-count-'+rk);
    if(badge){
      const count=filterItems(dimension,rk).length;
      badge.textContent=count;
      badge.classList.toggle('is-alert',rk==='loss'&&count>0);
    }
  }
}

async function load(){
  const n=++serial;panel.classList.add('cp-loading');el('cp-status').textContent='正在读取统计…';
  try{
    const b=bounds();
    if(!b.from||!b.to){el('cp-status').textContent='请选择起止日期';return;}
    const r=await fetch('/ozon-profit/api/analytics?'+new URLSearchParams({shop:el('cp-shop').value,...b}));
    if(!r.ok)throw Error('统计暂时无法读取，请重试');
    const d=await r.json();if(n!==serial)return;
    data=d;limit=10;const s=d.summary;
    if(!el('cp-shop').value&&Array.isArray(d.shops)){
      shopCounts={};
      for(const st of d.shops)shopCounts[st.id]=st.quantity;
    }
    syncShopPicker('cp-shop');
    const profitNum=Number(s.profit);
    const profitClass=s.eligible_ready?(profitNum<0?'cp-num-loss':'cp-num-profit'):'';
    el('cp-summary').innerHTML=[
      ['有效订单',s.orders+' 单',s.units+' 件 · 日均 '+s.daily_average+' 单',''],
      ['预计销售额',cash(s.sales),'包含待核算订单',''],
      ['预估利润',s.eligible_ready?cash(s.profit):'—','已核算有效包裹',profitClass],
      ['预估利润率',s.margin==null?'—':s.margin+'%','按同批已核算销售额计算','']
    ].map(([name,value,note,cls])=>`<div title="${esc(note)}"><span>${name}</span><strong class="${cls}">${value}</strong><small>${note}</small></div>`).join('');
    el('cp-coverage').textContent=`已核算 ${s.eligible_ready} 个有效包裹 · 日均 ${s.daily_average} 单`;
    el('cp-status').textContent=(b.from===b.to?b.from:`${b.from} 至 ${b.to}`)+' · 北京时间';
    fitCompassSummary();
    render();draw();
  }catch(e){
    if(n===serial)el('cp-status').textContent=e.message;
  }finally{
    if(n===serial)panel.classList.remove('cp-loading');
  }
}

function bindImages(scope){
  for(const img of scope.querySelectorAll('img[data-cp-img]')){
    const fallback=()=>{
      const span=document.createElement('span');
      span.className='cp-no-image';span.textContent='待补图';
      img.replaceWith(span);
    };
    img.addEventListener('error',fallback,{once:true});
    if(img.complete&&img.naturalWidth===0)fallback();
  }
}

function render(){
  if(!data)return;
  updateCounts();
  el('cp-profit-sort').hidden=rank!=='profit'||dimension==='shops';
  panel.querySelectorAll('[data-profit-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.profitMode===profitMode)));
  let items=filterItems(dimension,rank);
  items.sort((a,b)=>rank==='quantity'?b.quantity-a.quantity:rank==='loss'?Number(a.profit)-Number(b.profit):Number(b[dimension==='shops'?'profit':profitMode])-Number(a[dimension==='shops'?'profit':profitMode]));
  el('cp-list').innerHTML=items.slice(0,limit).map((p,i)=>dimension==='shops'?shopRow(p,i):productRow(p,i)).join('')||`<div class="cp-empty"><div class="cp-empty-icon">${rank==='loss'?'✓':'📊'}</div><p>${rank==='loss'?'当前范围暂无累计预估亏损':'当前范围暂无可排名数据'}</p></div>`;
  bindImages(el('cp-list'));
  el('cp-more').hidden=limit>=items.length;
  if(!el('cp-more').hidden)el('cp-more').textContent=`加载更多（已显 ${Math.min(limit,items.length)} / ${items.length}）`;
  el('cp-list').querySelectorAll('[data-key]').forEach(button=>button.onclick=()=>(dimension==='shops'?enterShop:showProduct)(items.find(p=>p.key===button.dataset.key)));
}

function posBadge(i){
  const cls=i===0?'cp-pos-1':i===1?'cp-pos-2':i===2?'cp-pos-3':'cp-pos-n';
  return `<span class="cp-position ${cls}">${i+1}</span>`;
}

function productRow(p,i){
  const hasImg=/^https:\/\//.test(p.image);
  const tagText=shopContext?(p.offer||p.sku||p.shop):p.shop;
  let metaHtml='';
  if(rank==='quantity'){
    metaHtml=`<span class="cp-shop-tag">${esc(tagText)}</span><span>销售额 ${cash(p.sales)}${p.ready>0?` · <em class="${Number(p.profit)<0?'cp-loss':'cp-meta-profit'}">利润 ${cash(p.profit)}</em>`:''}</span>`;
  }else if(rank==='profit'){
    const comp=profitMode==='unit_profit'?`累计 ${cash(p.profit)}`:`单件 ${cash(p.unit_profit)}/件`;
    metaHtml=`<span class="cp-shop-tag">${esc(tagText)}</span><span>${comp} · 已核算 ${p.ready_quantity} 件</span>`;
  }else{
    metaHtml=`<span class="cp-shop-tag">${esc(tagText)}</span><span>销量 ${p.quantity} 件 · 销售额 ${cash(p.sales)}</span>`;
  }
  const valText=rank==='quantity'?`${p.quantity} 件`:rank==='profit'&&profitMode==='unit_profit'?`${cash(p.unit_profit)}/件`:cash(p.profit);
  const valClass=Number(p.profit)<0&&rank!=='quantity'?'cp-loss':rank==='profit'?'cp-profit-text':'';
  const subText=rank==='quantity'?(p.margin!=null?`利润率 ${p.margin}% ›`:'查看详情 ›'):`${p.margin==null?'利润率 —':`利润率 ${p.margin}%`} ›`;
  return `<button type="button" class="cp-item" data-key="${esc(p.key)}">${posBadge(i)}${hasImg?`<img data-cp-img src="${esc(p.image)}" alt="" loading="lazy" referrerpolicy="no-referrer">`:'<span class="cp-no-image">待补图</span>'}<span class="cp-name"><b>${esc(p.name)}</b><small class="cp-item-meta">${metaHtml}</small></span><span class="cp-value ${valClass}"><strong>${valText}</strong><small>${subText}</small></span></button>`;
}

function shopRow(p,i){
  const metaHtml=rank==='quantity'
    ?`<span>销量 ${p.units} 件 · 销售额 ${cash(p.sales)}${p.ready>0?` · <em class="${Number(p.profit)<0?'cp-loss':'cp-meta-profit'}">利润 ${cash(p.profit)}</em>`:''}</span>`
    :`<span>${p.quantity} 单 (${p.units} 件) · 已核算 ${p.ready} 个包裹</span>`;
  const valClass=rank!=='quantity'&&Number(p.profit)<0?'cp-loss':rank==='profit'?'cp-profit-text':'';
  const valText=rank==='quantity'?`${p.quantity} 单`:cash(p.profit);
  const subText=rank==='quantity'?'商品表现 ›':`${p.margin==null?'利润率 —':`利润率 ${p.margin}%`} ›`;
  return `<button type="button" class="cp-item cp-shop-row" data-key="${esc(p.key)}">${posBadge(i)}<span class="cp-shop-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 10v10h16V10M3 10l2-6h14l2 6M3 10c0 3 4 3 4 0 0 3 5 3 5 0 0 3 5 3 5 0 0 3 4 3 4 0M9 20v-6h6v6" stroke-linecap="round" stroke-linejoin="round"/></svg></span><span class="cp-name"><b>${esc(p.name)}</b><small class="cp-item-meta">${metaHtml}</small></span><span class="cp-value ${valClass}"><strong>${valText}</strong><small>${subText}</small></span></button>`;
}

function showShop(p){
  const eyebrow=el('cp-modal-eyebrow'),title=el('cp-modal-title');
  if(eyebrow)eyebrow.textContent='店铺经营概况';
  if(title)title.textContent='店铺详情';
  const profitCls=p.ready?(Number(p.profit)<0?'cp-loss':'cp-profit-text'):'';
  el('cp-product-body').innerHTML=`
  <div class="cp-modal-hero">
    <span class="cp-shop-icon cp-hero-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 10v10h16V10M3 10l2-6h14l2 6M3 10c0 3 4 3 4 0 0 3 5 3 5 0 0 3 5 3 5 0 0 3 4 3 4 0M9 20v-6h6v6" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
    <div class="cp-hero-info">
      <h3>${esc(p.name)}</h3>
      <p class="cp-hero-tags"><span class="cp-shop-tag">当前时间范围</span><span class="cp-sku-badge">有效包裹 ${p.ready} / ${p.packages} 已核算</span></p>
    </div>
  </div>
  <div class="cp-detail-stats">
    ${[
      ['有效订单',p.quantity+' 单',''],
      ['商品销量',p.units+' 件',''],
      ['预计销售额',cash(p.sales),''],
      ['累计预估利润',p.ready?cash(p.profit):'待核算',profitCls],
      ['预估利润率',p.margin==null?'—':p.margin+'%',''],
      ['有效包裹已核算',p.ready+' / '+p.packages,'']
    ].map(([name,value,cls])=>`<div><span>${name}</span><strong class="${cls}">${value}</strong></div>`).join('')}
  </div>
  <p class="muted cp-modal-note">利润仅统计已完整核算的有效包裹；单量按订单去重。</p>
  <div class="cp-linked-head"><h3>关联订单</h3><span>共 ${p.orders.length} 个包裹 · 点击可核算</span></div>
  <div class="cp-linked-list">${p.orders.map(o=>`<button type="button" class="cp-linked" data-id="${o.id}"><span><b>${esc(o.posting)}</b><small>${new Date(o.created*1000).toLocaleDateString('zh-CN',{timeZone:'Asia/Shanghai'})} · <em class="cp-status-pill">${esc(o.status)}</em></small></span><span><strong>${cash(o.sales)}</strong><small class="${o.profit==null?'cp-pending-tag':Number(o.profit)<0?'cp-loss':'cp-profit-text'}">${o.profit==null?'待核算':'利润 '+cash(o.profit)} ›</small></span></button>`).join('')||'<p class="cp-empty">当前范围暂无有效订单</p>'}</div>`;
  el('cp-product-body').querySelectorAll('[data-id]').forEach(b=>b.onclick=()=>{modal.close();openOrder(Number(b.dataset.id));});
  modal.showModal();
}

function showProduct(p){
  const eyebrow=el('cp-modal-eyebrow'),title=el('cp-modal-title');
  if(eyebrow)eyebrow.textContent=p.shop+' · 商品分析';
  if(title)title.textContent='商品详情';
  selected=p;
  const hasImg=/^https:\/\//.test(p.image);
  const profitCls=p.ready?(Number(p.profit)<0?'cp-loss':'cp-profit-text'):'';
  const unitCls=p.unit_profit!=null?(Number(p.unit_profit)<0?'cp-loss':'cp-profit-text'):'';
  el('cp-product-body').innerHTML=`
  <div class="cp-modal-hero">
    ${hasImg?`<button type="button" class="cp-hero-thumb" id="cp-hero-img-btn" aria-label="查看大图"><img data-cp-img src="${esc(p.image)}" alt="${esc(p.name)}" referrerpolicy="no-referrer"></button>`:'<span class="cp-no-image cp-hero-no-img">待补图</span>'}
    <div class="cp-hero-info">
      <h3>${productTitle({name:p.name,sku:p.sku},true)}</h3>
      <p class="cp-hero-tags"><span class="cp-shop-tag">${esc(p.shop)}</span><span class="cp-sku-badge">货号 ${esc(p.offer||p.sku||'—')}</span>${p.sku&&p.offer&&p.sku!==p.offer?`<span class="cp-sku-badge">SKU ${esc(p.sku)}</span>`:''}</p>
    </div>
  </div>
  <div class="cp-detail-stats cp-product-stats">
    ${[
      ['累计预估利润',p.ready?cash(p.profit):'待核算',profitCls],
      ['平均单件利润',p.unit_profit==null?'待核算':cash(p.unit_profit)+'/件',unitCls],
      ['预估利润率',p.margin==null?'—':p.margin+'%',''],
      ['商品总销量',p.quantity+' 件',''],
      ['已核算销量',p.ready_quantity+' 件',''],
      ['预计销售额',cash(p.sales),''],
      ['已核算采购成本',cash(p.purchase),''],
      ['已核算公共费用',cash(p.fees),'']
    ].map(([n,v,cls])=>`<div><span>${n}</span><strong class="${cls}">${v}</strong></div>`).join('')}
  </div>
  <details class="cp-alloc-details"><summary><span>费用分摊与统计口径说明</span><i class="cp-trend-chevron" aria-hidden="true"></i></summary><p>采购成本按实际填写；包裹公共费用按商品销售额占比分摊，销售额为零时按数量分摊。利润和利润率仅统计已完整核算的有效包裹。</p></details>
  <div class="cp-linked-head"><h3>关联订单</h3><span>共 ${p.orders.length} 笔 · 点击进入订单核算</span></div>
  <div class="cp-linked-list">${p.orders.map(o=>`<button type="button" class="cp-linked" data-id="${o.id}"><span><b>${esc(o.posting)}</b><small>${new Date(o.created*1000).toLocaleDateString('zh-CN',{timeZone:'Asia/Shanghai'})} · ${o.quantity?o.quantity+' 件 · ':''}<em class="cp-status-pill">${esc(o.status)}</em></small></span><span><strong>${cash(o.sales)}</strong><small class="${o.profit==null?'cp-pending-tag':Number(o.profit)<0?'cp-loss':'cp-profit-text'}">${o.profit==null?'待核算':'利润 '+cash(o.profit)} ›</small></span></button>`).join('')}</div>`;
  bindImages(el('cp-product-body'));
  const imgBtn=el('cp-hero-img-btn');
  if(imgBtn&&typeof openImage==='function'){
    imgBtn.onclick=()=>{openImage(p.image,p.name);};
  }
  el('cp-product-body').querySelectorAll('[data-id]').forEach(b=>b.onclick=()=>{modal.close();openOrder(Number(b.dataset.id));});
  modal.showModal();
}

function draw(){
  if(!data)return;
  panel.querySelectorAll('[data-chart]').forEach(b=>{
    const active=b.dataset.chart===chart;
    b.classList.toggle('active',active);
    b.setAttribute('aria-pressed',String(active));
  });
  const rows=data.trend||[],values=rows.map(r=>Number(r[chart]));
  const min=Math.min(0,...values),max=Math.max(1,...values),span=Math.max(1,max-min);
  const x=i=>24+i*552/Math.max(1,rows.length-1),y=v=>136-(v-min)/span*108;
  const points=values.map((v,i)=>x(i).toFixed(1)+','+y(v).toFixed(1)).join(' ');
  const zeroY=y(0).toFixed(1);
  const areaPoints=rows.length?`${x(0).toFixed(1)},${zeroY} ${points} ${x(rows.length-1).toFixed(1)},${zeroY}`:'';
  const maxVal=Math.max(...values,0),sumVal=values.reduce((a,b)=>a+b,0);
  const fmt=v=>chart==='orders'?`${Math.round(v)} 单`:`¥${v.toFixed(2)}`;
  const metaEl=el('cp-chart-meta');
  if(metaEl)metaEl.textContent=`合计 ${fmt(sumVal)} · 最高 ${fmt(maxVal)}`;
  const midIdx=Math.floor((rows.length-1)/2);
  const dots=rows.length<=35?values.map((v,i)=>`<circle cx="${x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="2.8" fill="#fff" stroke="#225fe4" stroke-width="1.8"><title>${rows[i].date}：${fmt(v)}</title></circle>`).join(''):'';
  el('cp-chart').innerHTML=`<svg viewBox="0 0 600 172" role="img" aria-label="每日${chart==='orders'?'订单量':chart==='sales'?'销售额':'利润'}趋势"><defs><linearGradient id="cp-area-grad" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#225fe4" stop-opacity="0.22"/><stop offset="100%" stop-color="#225fe4" stop-opacity="0.0"/></linearGradient></defs><path d="M24 28H576" stroke="#edf2f9" stroke-dasharray="3 3"/><path d="M24 ${zeroY}H576" stroke="#dbe3ef"/><polygon points="${areaPoints}" fill="url(#cp-area-grad)"/><polyline points="${points}" fill="none" stroke="#225fe4" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>${dots}<text x="24" y="164">${rows[0]?.date||''}</text>${rows.length>2?`<text x="${x(midIdx).toFixed(1)}" y="164" text-anchor="middle">${rows[midIdx]?.date||''}</text>`:''}<text x="576" y="164" text-anchor="end">${rows.at(-1)?.date||''}</text><text x="24" y="18">最高 ${maxVal.toFixed(chart==='orders'?0:2)}</text></svg>`;
}

function calendar(){
  const y=month.getFullYear(),m=month.getMonth(),first=new Date(y,m,1),count=new Date(y,m+1,0).getDate();
  const activeFrom=rangeStart||el('cp-range').dataset.from||'',activeTo=rangeStart?'':(el('cp-range').dataset.to||'');
  const today=todayStr();
  const lead=(first.getDay()+6)%7;
  el('cp-calendar').innerHTML=`
  <div class="cp-cal-head">
    <button type="button" data-step="-1" aria-label="上一个月">‹</button>
    <b>${y} 年 ${m+1} 月</b>
    <button type="button" data-step="1" aria-label="下一个月">›</button>
  </div>
  <div class="cp-cal-week">${'一二三四五六日'.split('').map(v=>'<small>'+v+'</small>').join('')}</div>
  <div class="cp-cal-grid">${'<span></span>'.repeat(lead)}${Array.from({length:count},(_,i)=>{
    const dStr=day(new Date(y,m,i+1));
    const isSel=dStr===activeFrom||dStr===activeTo;
    const inRange=Boolean(activeFrom&&activeTo&&dStr>activeFrom&&dStr<activeTo);
    const isToday=dStr===today;
    return `<button type="button" data-day="${dStr}" class="${isSel?'selected':''} ${inRange?'in-range':''} ${isToday?'today':''}">${i+1}</button>`;
  }).join('')}</div>
  <div class="cp-cal-foot">
    <p>${rangeStart?`已选起点 ${rangeStart}，请点结束日期`:'先点开始日期，再点结束日期'}</p>
    ${rangeStart?`<button type="button" id="cp-single-day" class="cp-single-day-btn">只查该日</button>`:''}
  </div>`;
  el('cp-calendar').querySelectorAll('[data-step]').forEach(b=>b.onclick=e=>{e.stopPropagation();month=new Date(y,m+Number(b.dataset.step),1);calendar();});
  const singleBtn=el('cp-single-day');
  if(singleBtn)singleBtn.onclick=e=>{
    e.stopPropagation();
    el('cp-range').dataset.from=rangeStart;el('cp-range').dataset.to=rangeStart;
    el('cp-range').textContent=`${rangeStart}（单日）`;
    el('cp-calendar').hidden=true;rangeStart='';load();
  };
  el('cp-calendar').querySelectorAll('[data-day]').forEach(b=>b.onclick=e=>{
    e.stopPropagation();
    if(!rangeStart){rangeStart=b.dataset.day;el('cp-range').textContent=`起点 ${rangeStart} → 点选结束日期`;calendar();}
    else{
      const dates=[rangeStart,b.dataset.day].sort();
      el('cp-range').dataset.from=dates[0];el('cp-range').dataset.to=dates[1];
      el('cp-range').textContent=dates[0]===dates[1]?`${dates[0]}（单日）`:dates.join(' 至 ');
      el('cp-calendar').hidden=true;rangeStart='';load();
    }
  });
}

panel.querySelectorAll('[data-period]').forEach(b=>b.onclick=()=>{
  closeAllShopPickers();
  if(el('cp-period').value===b.dataset.period&&b.dataset.period!=='custom')return;
  el('cp-period').value=b.dataset.period;
  el('cp-period').onchange();
});

el('cp-period').onchange=()=>{
  const custom=el('cp-period').value==='custom';
  syncPeriodUI();
  el('cp-calendar').hidden=!custom;
  if(custom){rangeStart='';calendar();}else load();
};
el('cp-range').onclick=e=>{e.stopPropagation();closeAllShopPickers();rangeStart='';el('cp-calendar').hidden=!el('cp-calendar').hidden;if(!el('cp-calendar').hidden)calendar();};
document.addEventListener('click',e=>{
  if(!e.target.closest('.shop-picker-wrap'))closeAllShopPickers();
  if(!panel.hidden&&!el('cp-calendar').hidden&&!e.target.closest('#cp-custom-bar')){
    el('cp-calendar').hidden=true;rangeStart='';
  }
});
document.addEventListener('keydown',e=>{
  if(e.key==='Escape')closeAllShopPickers();
});

el('cp-shop').onchange=()=>{
  syncShopPicker('cp-shop');
  if(shopContext){
    if(!el('cp-shop').value){history.back();return;}
    shopContext=el('cp-shop').value;
    history.replaceState({compass:true,shopId:shopContext},'','#compass');
    syncScope();
  }
  load();
};

panel.querySelectorAll('[data-dimension]').forEach(b=>b.onclick=()=>{
  dimension=b.dataset.dimension;limit=10;
  panel.querySelectorAll('[data-dimension]').forEach(v=>v.setAttribute('aria-selected',String(v===b)));
  render();
});
panel.querySelectorAll('[data-rank]').forEach(b=>b.onclick=()=>{
  rank=b.dataset.rank;limit=10;
  panel.querySelectorAll('[data-rank]').forEach(v=>v.classList.toggle('active',v===b));
  render();
});
panel.querySelectorAll('[data-chart]').forEach(b=>b.onclick=()=>{chart=b.dataset.chart;draw();});
panel.querySelectorAll('[data-profit-mode]').forEach(b=>b.onclick=()=>{profitMode=b.dataset.profitMode;limit=10;render();});
el('cp-more').onclick=()=>{limit+=10;render();};
el('cp-close').onclick=()=>modal.close();
el('detail').addEventListener('close',()=>{if(!panel.hidden)load();});
window.addEventListener('compass-ready',()=>{
  syncShopPicker('shop');
  if(location.hash==='#compass')openCompass();
});
if(location.hash==='#compass'&&!el('workspace').hidden)openCompass();
})();
