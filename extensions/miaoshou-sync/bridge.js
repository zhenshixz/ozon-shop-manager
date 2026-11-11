'use strict';
(()=>{
 const channel=new URLSearchParams(location.hash.slice(1)).get('batch');
 if(!channel)return;
 let latest;
 function deliver(){if(latest)window.postMessage({type:'OZON_MIAOSHOU_BATCH',channel,...latest},location.origin);}
 window.addEventListener('message',event=>{if(event.source===window&&event.origin===location.origin&&event.data?.type==='OZON_MIAOSHOU_READY'&&event.data.channel===channel)deliver();});
 chrome.runtime.sendMessage({type:'GET_BATCH',channel},result=>{
  latest=chrome.runtime.lastError?{ok:false,error:'插件连接中断，请重新读取'}:result;
  deliver();
 });
})();
