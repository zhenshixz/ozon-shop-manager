/* Read only visible DOM fields. No page requests, cookies, inputs or button clicks. */
(()=>{
 const postingPattern=/\b\d{5,14}-\d{2,10}-\d{1,5}\b/g;
 const text=e=>(e?.innerText||'').replace(/\u00a0/g,' ').trim();
 const all=(s,re)=>[...s.matchAll(re)].map(x=>x[1]);
 const unique=a=>[...new Set(a)];
 function parseCard(value,quantityHints=[]){
  const postings=unique(value.match(postingPattern)||[]);
  const shops=unique(all(value,/\b(Luo\d{3})\b/gi).map(x=>'Luo'+x.slice(-3)));
  const offers=unique(all(value,/平台\s*SKU\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9_.-]{1,149})/gi));
  const skus=unique(all(value,/(?:^|\n|\s)SKU\s*ID\s*[:：]?\s*(\d{6,18})/gi));
  const quantities=quantityHints.length?unique(quantityHints):unique(all(value,/(?:^|\n)\s*[×xX]\s*(\d{1,5})\s*(?=\n|$)/g));
  const costs=unique(all(value,/采\s*购\s*金\s*额\s*[:：]?\s*(?:CNY|RMB|¥|￥)\s*([\d,]+(?:\.\d+)?)/gi).map(x=>x.replaceAll(',','')));
  const notes=all(value,/本地备注\s*[:：]?\s*([^\n]*)/g).map(x=>x.replace(/[✎✏\uE000-\uF8FF]+\s*$/g,'').trim());
  const issues=[];
  if(postings.length!==1||shops.length!==1)issues.push('订单编号或店名不唯一');
  if(offers.length!==1&&skus.length!==1)issues.push('未识别唯一的商品货号/SKU');
  if(offers.length>1||skus.length>1)issues.push('本单包含多款商品，MVP需人工核对行金额');
  if(quantities.length!==1)issues.push('未识别唯一的商品数量');
  return {shop_name:shops[0]||'',posting:postings[0]||'',offer_id:offers.length===1?offers[0]:'',sku:skus.length===1?skus[0]:'',
    quantity:quantities.length===1?quantities[0]:'',cost_total:costs.length===1?costs[0]:'',note:notes.length===1?notes[0]:'',
    identity_error:issues.join('；'),cost_note:costs.length>1?'采购金额不唯一，暂不读取':costs.length===0?'采购金额/币种未识别':Number(costs[0])===0?'采购金额为0，记为待补价':'',
    weight_note:notes.length>1?'出现多个备注，重量暂不读取':''};
 }
 function collect(doc=document){
  const walker=doc.createTreeWalker(doc.body,NodeFilter.SHOW_TEXT),anchors=[],visited=new Set(),cards=[];
  while(walker.nextNode()){
   const node=walker.currentNode,p=node.parentElement;
   if(!p||p.closest('script,style,textarea,input,[hidden]'))continue;
   if(!p.getClientRects().length)continue;
   if((node.nodeValue||'').match(postingPattern))anchors.push(p);
  }
  for(const anchor of anchors){
   let best=null;
   for(let p=anchor;p&&p!==doc.body;p=p.parentElement){
    const value=text(p);if(value.length>40000)break;
    const ids=unique(value.match(postingPattern)||[]);if(ids.length>1)break;
    if(ids.length===1&&/平台\s*SKU/i.test(value)&&/采购|采\s*购/.test(value))best=p;
   }
   if(best&&!visited.has(best)){visited.add(best);cards.push(best);}
  }
  const records=[],seen=new Set();
  for(const card of cards){
   const hints=[...card.querySelectorAll('span,b,strong,div')].filter(n=>n.childElementCount<2).map(n=>text(n).match(/^[×xX]\s*([1-9]\d{0,4})$/)).filter(Boolean).map(m=>m[1]);
   const row=parseCard(text(card),hints);const k=row.shop_name+'|'+row.posting;
   if(seen.has(k))continue;seen.add(k);records.push(row);
  }
  return {records:records.slice(0,100),detected:anchors.length,truncated:records.length>100,scope:'current-page'};
 }
 globalThis.MiaoshouReader={collect,parseCard};
})();
