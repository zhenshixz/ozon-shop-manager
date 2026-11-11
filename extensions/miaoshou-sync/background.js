'use strict';
const HOME='https://voice.xzbest.site/ozonzusou/purchase-sync/';
chrome.runtime.onMessage.addListener((message,sender,reply)=>{
 (async()=>{
  if(sender.id!==chrome.runtime.id)throw Error('请求来源不正确');
  if(message.type==='OPEN_PREVIEW'){
   if(sender.tab)throw Error('请从插件面板发起同步');
   const records=message.records;
   if(!Array.isArray(records)||!records.length||records.length>5)throw Error('每批请选择1—5笔订单');
   const channel=crypto.randomUUID();
   const clean=records.map(x=>Object.fromEntries(['shop_name','posting','offer_id','sku','quantity','cost_total','note','identity_error'].map(k=>[k,String(x[k]??'').slice(0,200)])));
   await chrome.storage.session.set({handoff:{channel,records:clean,created:Date.now(),tab:null}});
   const tab=await chrome.tabs.create({url:HOME+'#batch='+channel});
   const state=(await chrome.storage.session.get('handoff')).handoff;
   if(state?.channel===channel)await chrome.storage.session.set({handoff:{...state,tab:tab.id}});
   reply({ok:true});return;
  }
  if(message.type==='GET_BATCH'){
   const url=new URL(sender.url||'https://invalid.local');
   if(url.origin!==new URL(HOME).origin||url.pathname!==new URL(HOME).pathname)throw Error('仅店铺助手核对页可以接收');
   const state=(await chrome.storage.session.get('handoff')).handoff;
   if(!state||state.channel!==message.channel||Date.now()-state.created>20*60*1000)throw Error('读取记录已过期，请回到妙手重新读取');
   if(state.tab!==null&&state.tab!==sender.tab?.id)throw Error('请使用插件刚打开的核对页');
   reply({ok:true,records:state.records,channel:state.channel});return;
  }
  throw Error('未知操作');
 })().catch(e=>reply({ok:false,error:e.message}));return true;
});
