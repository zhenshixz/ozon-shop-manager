"""Cache Chinese reference titles; only public product titles leave this service."""
import datetime as dt
from collections import Counter
import html
import hashlib
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

import profit_core as core


class TranslationError(Exception):
    pass


class ReferenceTitle(str):
    """Explicit category reference while a full translation awaits retry."""


QUALITY_ERRORS = {'specification-mismatch','category-mismatch','invalid-result',
                  'unsupported-attributes','untranslated-result','empty-result','source-too-long'}


def settings_for(store):
    path=store.directory.parent/'title-translation.json'
    if not path.exists():return {'provider':'mymemory'}
    try:
        value=json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(value,dict):raise ValueError()
        return value
    except (OSError,ValueError,UnicodeError):raise TranslationError('configuration') from None


def provider_options(settings):
    name=settings.get('provider','mymemory')
    if name=='mymemory':return 'MyMemory',4000
    if name=='local-hymt':
        revision=settings.get('model_revision','tq2_0')
        if revision not in ('tq2_0','q4_k_m'):raise TranslationError('configuration')
        return 'Local/HY-MT1.5-1.8B'+('/Q4_K_M' if revision=='q4_k_m' else ''),100000
    if name=='siliconflow':
        if settings.get('model')!='tencent/Hunyuan-MT-7B' or not isinstance(settings.get('api_key'),str) or not settings['api_key'].strip():
            raise TranslationError('configuration')
        budget=settings.get('daily_character_budget',20000)
        if isinstance(budget,bool) or not isinstance(budget,int) or budget<1:raise TranslationError('configuration')
        return 'SiliconFlow/Hunyuan-MT-7B',min(budget,20000)
    if name!='baidu':raise TranslationError('configuration')
    if any(not isinstance(settings.get(key),str) or not settings[key].strip() for key in ('app_id','app_key')):
        raise TranslationError('configuration')
    # Default stays below the standard plan's 50,000 free characters/month.
    ceiling=900000 if settings.get('tier')=='personal' else 45000
    budget=settings.get('monthly_character_budget',ceiling)
    if isinstance(budget,bool) or not isinstance(budget,int) or budget<1:raise TranslationError('configuration')
    return 'Baidu',min(budget,ceiling)


def validate_result(text,result):
    if not isinstance(result,str) or not result.strip():raise TranslationError('empty-result')
    if re.search('[\u0400-\u04ff]',text) and not re.search('[\u3400-\u9fff]',result):
        raise TranslationError('untranslated-result')
    return html.unescape(result).strip()


def category_rows(nodes,now,path=()):
    rows=[]
    if not isinstance(nodes,list):raise TranslationError('context-unavailable')
    for node in nodes:
        if not isinstance(node,dict):raise TranslationError('context-unavailable')
        name=node.get('category_name') or node.get('type_name') or ''
        current=path+(name,) if name else path
        for kind,field in (('category','description_category_id'),('type','type_id')):
            if node.get(field):rows.append((kind,str(node[field]),' / '.join(current),now))
        rows.extend(category_rows(node.get('children') or [],now,current))
    return rows


def context_for_title(store,title,now):
    with store.connection() as db:
        rows=[dict(row) for row in db.execute('''SELECT DISTINCT o.shop,l.offer_id,l.sku,c.category_path,c.checked
            FROM profit_lines l JOIN profit_orders o ON o.id=l.order_id
            LEFT JOIN profit_title_product_context c ON c.shop=o.shop AND c.product_key=
                CASE WHEN l.offer_id!='' THEN 'offer:'||l.offer_id ELSE 'sku:'||l.sku END
            WHERE l.name=?''',(title,))]
    if not rows:raise TranslationError('context-unavailable')
    missing=[row for row in rows if not row['category_path'] or not row['checked'] or now-row['checked']>7*86400]
    if missing:
        # Late import avoids a module initialization cycle. Seller keys never enter the model request.
        import monitor
        try:cfg=monitor.load_config(store.directory.parent/'config.json')
        except Exception:raise TranslationError('context-unavailable') from None
        shops={shop['id']:shop for shop in cfg['shops']}
        for row in missing:
            if row['shop'] not in shops:raise TranslationError('context-unavailable')
            api=monitor.Ozon(shops[row['shop']],monitor.Transport(timeout=8),cfg['max_pages'])
            try:
                by_sku=not bool(row['offer_id']);identifier=row['sku'] if by_sku else row['offer_id']
                products=api.product_info([identifier],by_sku=by_sku)
                product=next((p for p in products if str(p.get('sku') if by_sku else p.get('offer_id'))==identifier),None)
                if product is None:raise ValueError()
                type_id=str(product.get('type_id') or '')
                category_id=str(product.get('description_category_id') or '')
                with store.connection() as db:
                    found=db.execute("SELECT path FROM profit_title_categories WHERE (kind='type' AND category_id=?) OR (kind='category' AND category_id=?) ORDER BY CASE kind WHEN 'type' THEN 0 ELSE 1 END LIMIT 1",(type_id,category_id)).fetchone()
                    tree_check=db.execute("SELECT value FROM profit_translation_control WHERE key='category-tree-checked'").fetchone()
                if found is None or not tree_check or now-tree_check[0]>7*86400:
                    if not tree_check or now-tree_check[0]>=3600:
                        tree=api.post('/v1/description-category/tree',{'language':'ZH_HANS'})
                        categories=category_rows(tree.get('result'),now)
                        if not categories:raise ValueError()
                        with store.connection() as db:
                            db.executemany('INSERT INTO profit_title_categories VALUES (?,?,?,?) ON CONFLICT(kind,category_id) DO UPDATE SET path=excluded.path,checked=excluded.checked',categories)
                            db.execute("INSERT INTO profit_translation_control VALUES ('category-tree-checked',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(now,))
                    with store.connection() as db:
                        found=db.execute("SELECT path FROM profit_title_categories WHERE (kind='type' AND category_id=?) OR (kind='category' AND category_id=?) ORDER BY CASE kind WHEN 'type' THEN 0 ELSE 1 END LIMIT 1",(type_id,category_id)).fetchone()
                if found is None:raise ValueError()
                row['category_path']=found['path']
                key=('sku:' if by_sku else 'offer:')+identifier
                with store.connection() as db:
                    db.execute('INSERT INTO profit_title_product_context VALUES (?,?,?,?) ON CONFLICT(shop,product_key) DO UPDATE SET category_path=excluded.category_path,checked=excluded.checked',
                        (row['shop'],key,row['category_path'],now))
            except Exception:raise TranslationError('context-unavailable') from None
    paths={row['category_path'] for row in rows if row['category_path']}
    if len(paths)!=1:raise TranslationError('context-ambiguous')
    return paths.pop()


def required_title_tokens(text):
    if not re.search('[\u0400-\u04ff]',text):return []
    # A separator is part of a model/size only when followed by ASCII text.
    # In S/M/L-кодов the hyphen joins a Russian suffix, not the size token.
    tokens=re.findall(r'(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]*(?:[-/&][A-Za-z0-9]+)*',text)
    return [t for t in tokens if len(t)>=2 and (any(c.isdigit() for c in t) or t.isupper() or t[0].isupper())]


def validate_title_specs(text,result,context=''):
    result=validate_result(text,result)
    if result.startswith('**') and result.endswith('**'):result=result[2:-2].strip()
    if len(result)>1 and (result[0],result[-1]) in (('“','”'),('"','"')):result=result[1:-1].strip()
    if len(result)>1200 or '\n' in result or '<think' in result.lower():raise TranslationError('invalid-result')
    if len(re.findall('[\u3400-\u9fff]',result))>max(24,int(len(text)*.8)):raise TranslationError('invalid-result')
    if re.search('枪械|弹孔',result) and not re.search(r'оруж|пистолет|винтов|стрельб|калибр',text,re.I):
        raise TranslationError('unsupported-attributes')
    numbers=lambda value:Counter(n.replace(',','.') for n in re.findall(r'\d+(?:[.,]\d+)?',value))
    if numbers(text)!=numbers(result):raise TranslationError('specification-mismatch')
    if any(not re.search(r'(?<![A-Za-z0-9])'+re.escape(token)+r'(?![A-Za-z0-9])',result,re.I) for token in required_title_tokens(text)):
        raise TranslationError('specification-mismatch')
    if '积木' in context and re.search(r'конструктор',text,re.I) and '积木' not in result:
        raise TranslationError('category-mismatch')
    return result


def translate_siliconflow_title(text,settings,context):
    provider_options(settings)  # Locks the request to the selected free model and fixed host.
    prompt=('Translate the following Russian e-commerce product title into Simplified Chinese, without additional explanation. '
            'Output only the translated title on one line. Preserve every number and do not add attributes. '
            'Use this category only to resolve word meaning, do not output the category: '+context+'. ')
    required=required_title_tokens(text)
    if required:prompt+='Keep these exact tokens unchanged in the translation: '+', '.join(required)+'. '
    if '积木' in context and re.search(r'конструктор',text,re.I):prompt+='In this toy context, конструктор means 积木 (building blocks). '
    prompt+='\n\n'+text
    payload=json.dumps({'model':settings['model'],'messages':[{'role':'user','content':prompt}],
        'temperature':0.1,'top_p':0.6,'max_tokens':512,'stream':False},ensure_ascii=False).encode('utf-8')
    request=urllib.request.Request('https://api.siliconflow.cn/v1/chat/completions',data=payload,
        headers={'Authorization':'Bearer '+settings['api_key'],'Content-Type':'application/json','User-Agent':'OzonProfitTitle/3.0'})
    try:
        with urllib.request.urlopen(request,timeout=20) as response:data=json.load(response)
    except urllib.error.HTTPError as exc:
        code=exc.code;exc.close()
        label='configuration' if code in (401,403) else 'rate-limit' if code==429 else 'quota' if code==402 else 'http-'+str(code)
        raise TranslationError(label) from None
    except (OSError,ValueError,UnicodeError):raise TranslationError('network-or-response') from None
    try:
        choice=data['choices'][0]
        if choice.get('finish_reason')!='stop':raise ValueError()
        result=choice['message']['content']
    except (KeyError,IndexError,TypeError,ValueError):raise TranslationError('invalid-response') from None
    return validate_title_specs(text,result,context)


def translate_local_title(text,context):
    prompt='商品类目：'+context+'。\n'
    required=required_title_tokens(text)
    terms=[(token,token) for token in required]
    for pattern,term in (
        (r'пистолет\s+для\s+монтажной\s+пены','发泡胶枪'),
        (r'механическ\w*','机械式'),(r'скелетн\w*','骨架式'),
        (r'термостатическ\w*','恒温'),
        (r'салфет\w*\s+влажн\w*|влажн\w*\s+салфет\w*','湿巾'),
    ):
        terms.extend((match.group(),term) for match in re.finditer(pattern,text,re.I))
    if '积木' in context:
        terms.extend((token,'积木') for token in re.findall(r'конструктор\w*',text,re.I))
    if terms:prompt+='参考下面的翻译：\n'+'\n'.join(source+' 翻译成 '+target for source,target in terms)+'\n'
    prompt+=('参考上面的信息，把下面的商品标题翻译成简体中文，只输出标题，不要额外解释。'
             '保留全部数字，不添加原文没有的属性，不翻译上面的信息：\n'+text)
    try:
        result=_local_title_response(prompt)
        for token in required:
            localized={'samsung':'三星'}.get(token.lower())
            if localized and token.lower() not in result.lower():result=result.replace(localized,token+' ')
        return validate_title_specs(text,result,context)
    except TranslationError as exc:
        if str(exc) not in QUALITY_ERRORS:raise
    # Do not repeat the same deterministic failed whole-title request forever.
    try:
        return translate_protected_title(text,context)
    except TranslationError as exc:
        if str(exc) not in QUALITY_ERRORS:raise
        category=context.rsplit('/',1)[-1].strip() or '商品'
        return ReferenceTitle(category+' · '+text)


def _local_title_response(prompt):
    payload=json.dumps({'model':'local-hymt','messages':[{'role':'user','content':prompt}],
        'temperature':0,'max_tokens':256,'stream':False},ensure_ascii=False).encode('utf-8')
    request=urllib.request.Request('http://127.0.0.1:5311/v1/chat/completions',data=payload,
        headers={'Content-Type':'application/json','User-Agent':'OzonProfitTitle/4.0'})
    try:
        with urllib.request.urlopen(request,timeout=45) as response:data=json.load(response)
    except urllib.error.HTTPError as exc:
        code=exc.code;exc.close()
        raise TranslationError('source-too-long' if code==400 else 'network-or-response') from None
    except (OSError,ValueError,UnicodeError):raise TranslationError('network-or-response') from None
    try:
        choice=data['choices'][0]
        if choice.get('finish_reason')!='stop':raise ValueError()
        result=choice['message']['content']
    except (KeyError,IndexError,TypeError,ValueError):raise TranslationError('invalid-result') from None
    if not isinstance(result,str):raise TranslationError('invalid-result')
    return result


def translate_protected_title(text,context=''):
    """Translate words only; original ASCII specs never enter model output."""
    parts=re.split(r'([A-Za-z0-9]+(?:[./&+\-][A-Za-z0-9]+)*)',text)
    units={'м':'米','см':'厘米','мм':'毫米','л':'升','мл':'毫升','г':'克','кг':'千克',
           'шт':'件','пара':'对','пары':'对','пар':'对','х':'×','×':'×'}
    result=[];calls=0
    for index,part in enumerate(parts):
        if index%2:
            result.append(part);continue
        fragment=part.strip(' \t\r\n,;，；.-')
        if not fragment:continue
        if not re.search('[\u0400-\u04ff]',fragment):
            result.append(fragment);continue
        translated=units.get(fragment.lower())
        # Category-grounded disambiguation of known misleading dictionary senses.
        if fragment.lower()=='клумба' and '花坛' in context:translated='花坛'
        if translated is None:
            calls+=1
            if calls>6:raise TranslationError('source-too-long')
            translated=_local_title_response('Translate the following Russian text into Simplified Chinese, without additional explanation.\n\n'+fragment)
            translated=validate_title_specs(fragment,translated,context)
            if re.search('[\u0400-\u04ff]',translated):raise TranslationError('untranslated-result')
        result.append(translated)
    return validate_title_specs(text,' '.join(result),context)


def translate_baidu_segment(text,settings):
    salt=secrets.token_hex(12)
    signature=hashlib.md5((settings['app_id']+text+salt+settings['app_key']).encode('utf-8')).hexdigest()
    payload=urllib.parse.urlencode({'q':text,'from':'ru' if re.search('[\u0400-\u04ff]',text) else 'auto',
        'to':'zh','appid':settings['app_id'],'salt':salt,'sign':signature}).encode('ascii')
    request=urllib.request.Request('https://fanyi-api.baidu.com/api/trans/vip/translate',data=payload,
        headers={'Content-Type':'application/x-www-form-urlencoded','User-Agent':'OzonProfitTitle/2.0'})
    try:
        with urllib.request.urlopen(request,timeout=8) as response:data=json.load(response)
    except urllib.error.HTTPError as exc:
        code=exc.code;exc.close();raise TranslationError('quota' if code==429 else 'http-'+str(code)) from None
    except (OSError,ValueError,UnicodeError):raise TranslationError('network-or-response') from None
    if not isinstance(data,dict):raise TranslationError('invalid-response')
    error=str(data.get('error_code') or '')
    if error:
        if error in ('54004','54006','54007'):raise TranslationError('quota')
        if error in ('52003','54000','54001','58000','58002','58003'):raise TranslationError('configuration')
        raise TranslationError('provider-status')
    rows=data.get('trans_result')
    if not isinstance(rows,list) or not rows or any(not isinstance(row,dict) or not isinstance(row.get('dst'),str) for row in rows):
        raise TranslationError('invalid-response')
    return validate_result(text,' '.join(row['dst'] for row in rows))


def segments(text,maximum=480):
    parts=[];current=''
    for word in re.findall(r'\S+\s*',text):
        if len((current+word).encode('utf-8'))<=maximum:
            current+=word;continue
        if current.strip():parts.append(current.strip());current=''
        for char in word:
            if len((current+char).encode('utf-8'))>maximum:
                parts.append(current.strip());current=''
            current+=char
    if current.strip():parts.append(current.strip())
    return parts


def translate_segment(text):
    language='ru' if re.search('[\u0400-\u04ff]',text) else 'en'
    query=urllib.parse.urlencode({'q':text,'langpair':language+'|zh-CN','mt':'1'})
    request=urllib.request.Request('https://api.mymemory.translated.net/get?'+query,
                                   headers={'User-Agent':'OzonProfitTitle/1.0'})
    try:
        with urllib.request.urlopen(request,timeout=8) as response:
            data=json.load(response)
    except urllib.error.HTTPError as exc:
        code=exc.code;exc.close();raise TranslationError('quota' if code==429 else 'http-'+str(code)) from None
    except (OSError,ValueError,UnicodeError):raise TranslationError('network-or-response') from None
    if not isinstance(data,dict):raise TranslationError('invalid-response')
    if data.get('quotaFinished') or data.get('responseStatus')==429:raise TranslationError('quota')
    if data.get('responseStatus')!=200:raise TranslationError('provider-status')
    result=(data.get('responseData') or {}).get('translatedText')
    return validate_result(text,result)


def refresh_titles(store,now=None,translator=None,settings=None):
    now=time.time() if now is None else now
    settings=settings_for(store) if settings is None else settings
    provider,budget=provider_options(settings)
    local_provider=settings.get('provider')=='local-hymt'
    model_provider=local_provider or provider=='SiliconFlow/Hunyuan-MT-7B'
    translate=translator
    if translate is None:
        translate=(lambda text:translate_baidu_segment(text,settings)) if provider=='Baidu' else translate_segment
    blocked_key='blocked-until:'+provider if provider!='MyMemory' else 'blocked-until'
    start=dt.datetime.fromtimestamp(now,core.CHINA).replace(day=1,hour=0,minute=0,second=0,microsecond=0)
    next_month=(start.replace(year=start.year+1,month=1) if start.month==12 else start.replace(month=start.month+1)).timestamp()
    window_start=start.timestamp() if provider=='Baidu' else now-86400
    with store.connection() as db:
        blocked=db.execute('SELECT value FROM profit_translation_control WHERE key=?',(blocked_key,)).fetchone()
        if blocked and blocked[0]>now:return {'translated':0,'blocked':True}
        rows=db.execute('''SELECT DISTINCT l.name FROM profit_lines l LEFT JOIN profit_title_translations t
            ON t.source_text=l.name WHERE trim(l.name)!='' AND (t.source_text IS NULL OR
            (t.provider NOT IN ('reviewed','user-reviewed','original') AND t.next_attempt<=? AND
             (t.zh_text='' OR (?=0 AND t.provider!=?) OR t.provider=? OR
              (?=1 AND t.provider LIKE 'Local/%' AND t.provider!=?))))
            ORDER BY l.id LIMIT 4''',(now,int(local_provider),provider,provider+'/reference',
                int(local_provider and settings.get('model_revision')=='q4_k_m'),provider)).fetchall()
    count=0
    for row in rows:
        title=row['name'];parts=[title] if model_provider else segments(title)
        if not parts:continue
        already_chinese=bool(re.search('[\u3400-\u9fff]',title)) and not re.search('[\u0400-\u04ff]',title)
        reference=False
        try:
            if already_chinese:translated=title
            else:
                result=[];reference=False
                for part in parts:
                    with store.connection() as db:
                        db.execute('BEGIN IMMEDIATE')
                        used=db.execute('SELECT COALESCE(SUM(characters),0) FROM profit_translation_requests WHERE created>=? AND provider=?',(window_start,provider)).fetchone()[0]
                        if used+len(part)>budget:raise TranslationError('local-budget')
                        db.execute('INSERT INTO profit_translation_requests(created,characters,provider) VALUES (?,?,?)',(now,len(part),provider))
                    if provider=='Baidu' and translator is None:time.sleep(1.05)
                    if model_provider and translator is None:
                        context=context_for_title(store,title,now)
                        value=translate_local_title(part,context) if local_provider else translate_siliconflow_title(part,settings,context)
                    else:value=translate(part)
                    reference=reference or isinstance(value,ReferenceTitle)
                    result.append(value)
                translated=' '.join(result)
            with store.connection() as db:
                db.execute('''INSERT INTO profit_title_translations VALUES (?,?,?,?,?,?) ON CONFLICT(source_text)
                    DO UPDATE SET zh_text=excluded.zh_text,provider=excluded.provider,translated_at=excluded.translated_at,
                    next_attempt=excluded.next_attempt,last_error=excluded.last_error WHERE profit_title_translations.provider NOT IN ('reviewed','user-reviewed')''',
                    (title,translated,'original' if already_chinese else provider+('/reference' if reference else ''),now,
                     now+3600 if not already_chinese and reference else 0,'reference-only' if not already_chinese and reference else ''))
                db.execute('DELETE FROM profit_translation_requests WHERE provider=? AND created<?',(provider,min(window_start,now-172800)))
            count+=1
        except TranslationError as exc:
            delay=max(300,next_month-now) if provider=='Baidu' and str(exc) in ('quota','local-budget') else (86400 if str(exc) in ('quota','local-budget','configuration') else 300)
            if local_provider and str(exc)=='network-or-response':delay=30
            with store.connection() as db:
                db.execute('''INSERT INTO profit_title_translations VALUES (?,'','',NULL,?,?) ON CONFLICT(source_text)
                    DO UPDATE SET next_attempt=excluded.next_attempt,last_error=excluded.last_error
                    WHERE profit_title_translations.provider NOT IN ('reviewed','user-reviewed')''',(title,now+delay,str(exc)))
                local_error=str(exc) in ('specification-mismatch','category-mismatch','invalid-result','unsupported-attributes','untranslated-result','context-unavailable','context-ambiguous','source-too-long')
                if not local_error:
                    db.execute('INSERT INTO profit_translation_control VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(blocked_key,now+delay))
            if not local_error:return {'translated':count,'blocked':True}
    return {'translated':count,'blocked':False}
