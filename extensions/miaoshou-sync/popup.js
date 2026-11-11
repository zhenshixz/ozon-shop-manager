'use strict';
const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let records=[];
function selection(){const n=document.querySelectorAll('[data-record]:checked').length;$('count').textContent=`已选 ${n} / 5 笔`;$('send').disabled=!n||n>5;}
$('read').onclick=async()=>{
 $('read').disabled=true;$('status').className='';$('status').textContent='正在识别订单字段…';
 try{
  const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
  const host=new URL(tab?.url||'https://invalid.local').hostname;
  if(!(host==='91miaoshou.com'||host.endsWith('.91miaoshou.com')))throw Error('请在妙手ERP订单列表页面点击插件。');
  await chrome.scripting.executeScript({target:{tabId:tab.id},files:['reader.js']});
  const result=await chrome.scripting.executeScript({target:{tabId:tab.id},func:()=>globalThis.MiaoshouReader.collect()});
  const data=result[0]?.result;records=data?.records||[];
  if(!records.length)throw Error('未识别到完整订单卡片。请确认已显示店名、订单号、商品数量和采购金额；MVP暂不自动翻页。');
  $('list').innerHTML=records.map((x,i)=>`<label class="record"><input data-record="${i}" type="checkbox" ${x.identity_error?'disabled':''}><div><strong>${esc(x.shop_name)} · ${esc(x.posting)}</strong><small>${esc(x.offer_id||x.sku)}</small><div class="values"><span>${esc(x.quantity||'?')} 件</span><span>采购 ¥${esc(x.cost_total||'—')}</span></div><small>备注：${esc(x.note||'未标注')}</small>${[x.identity_error,x.cost_note,x.weight_note].filter(Boolean).map(n=>`<small class="${x.identity_error?'error':'warn'}">${esc(n)}</small>`).join('')}</div></label>`).join('');
  $('status').textContent=`本页识别 ${records.length} 笔，请选1—5笔。金额为0的记录会留作待补价。`;$('footer').hidden=false;selection();
 }catch(e){$('status').textContent=e.message;$('status').className='error';}finally{$('read').disabled=false;}
};
$('list').onchange=e=>{if(document.querySelectorAll('[data-record]:checked').length>5){e.target.checked=false;$('status').textContent='MVP每批最多选择5笔。';}selection();};
$('send').onclick=async()=>{const items=[...document.querySelectorAll('[data-record]:checked')].map(n=>records[Number(n.dataset.record)]);$('send').disabled=true;try{const r=await chrome.runtime.sendMessage({type:'OPEN_PREVIEW',records:items});if(!r?.ok)throw Error(r?.error||'无法打开核对页');$('status').textContent='已打开线上核对页，请检查差异并确认。';}catch(e){$('status').textContent=e.message;}finally{selection();}};
