import json
import datetime as dt
import hashlib
import io
import tempfile
import time
import unittest
import urllib.parse
import urllib.error
from unittest.mock import patch

import monitor
import profit_core as core
import profit_sync as sync
import profit_titles as titles
from profit_web import create_app


class ProductViewTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=monitor.Store(self.tmp.name);self.now=time.time()
        self.shop={'id':'s1','name':'test'}
        self.post={'posting_number':'A-1','order_number':'A','status':'awaiting_deliver',
                   'created_at':monitor.stamp(self.now-86400),'products':[{'offer_id':'cap','sku':123,'name':'Бейсболка',
                   'quantity':1,'weight':.18,'price':{'amount':'69','currency':'CNY'}}]}
        with self.store.connection() as db:
            core.capture_postings(db,self.shop,'fbs',[self.post],self.now)
            db.execute('INSERT INTO profit_sync_state VALUES (?,?,?,?)',('s1','fbs',sync.history_start(self.now),self.now))

    def test_substatus_is_checked_even_when_list_seen_is_fresh_and_change_time_is_stable(self):
        class API:
            detail={'posting_number':'A-1','status':'awaiting_deliver','substatus':'posting_transferring_to_delivery','tracking_number':'test'}
            def posting(self,source,number):return self.detail
        api=API();sync.refresh_orders(self.store,api,self.shop,'fbs',self.now)
        with self.store.connection() as db:
            order=core.load_order(db,1)
            self.assertEqual(order['status_label'],'正在交接配送')
            self.assertEqual(order['substatus'],'posting_transferring_to_delivery')
            changed=order['status_changed_at']
        sync.refresh_orders(self.store,api,self.shop,'fbs',self.now+301)
        with self.store.connection() as db:self.assertEqual(core.load_order(db,1)['status_changed_at'],changed)
        api.detail={**api.detail,'substatus':'posting_on_way_to_city'}
        sync.refresh_orders(self.store,api,self.shop,'fbs',self.now+602)
        with self.store.connection() as db:
            order=core.load_order(db,1)
            self.assertEqual(order['status_label'],'运往收货城市')
            self.assertEqual(order['status_changed_at'],self.now+602)

    def test_stale_substatus_never_overrides_new_main_status(self):
        with self.store.connection() as db:
            db.execute('INSERT INTO profit_order_status_details VALUES (?,?,?,?,?,?,?)',
                       (1,'awaiting_deliver','posting_transferring_to_delivery','',1,self.now,self.now))
            db.execute("UPDATE profit_orders SET status='delivered'")
            self.assertEqual(core.load_order(db,1)['status_label'],'已送达')
            self.assertEqual(core.load_order(db,1)['substatus'],'')

    def test_titles_cached_once_keep_originals_and_financial_inputs_untouched(self):
        calls=[]
        def translator(text):calls.append(text);return '棒球帽'
        result=titles.refresh_titles(self.store,self.now,translator)
        self.assertEqual(result['translated'],1)
        self.assertEqual(titles.refresh_titles(self.store,self.now+1,translator)['translated'],0)
        with self.store.connection() as db:
            order=core.load_order(db,1);line=order['lines'][0]
            self.assertEqual(line['name'],'Бейсболка');self.assertEqual(line['name_zh'],'棒球帽')
            self.assertEqual(line['unit_price'],'69');self.assertIsNone(line['purchase_unit'])
        self.assertEqual(len(calls),1)

    def test_quota_error_is_not_published_as_a_title_and_has_global_backoff(self):
        calls=[]
        def translator(text):calls.append(text);raise titles.TranslationError('quota')
        self.assertTrue(titles.refresh_titles(self.store,self.now,translator)['blocked'])
        titles.refresh_titles(self.store,self.now+60,translator)
        self.assertEqual(len(calls),1)
        with self.store.connection() as db:self.assertEqual(core.load_order(db,1)['lines'][0]['name_zh'],'')

    def test_rolling_character_budget_stops_requests(self):
        with self.store.connection() as db:
            db.execute('INSERT INTO profit_translation_requests(created,characters) VALUES (?,?)',(self.now-60,3999))
        calls=[]
        result=titles.refresh_titles(self.store,self.now,lambda text:calls.append(text))
        self.assertTrue(result['blocked']);self.assertEqual(calls,[])

    def test_long_unicode_titles_are_split_under_provider_byte_limit(self):
        parts=titles.segments('Эксклюзивный набор для выживания '*80)
        self.assertGreater(len(parts),1)
        self.assertTrue(all(0<len(p.encode('utf-8'))<=480 for p in parts))

    def test_provider_response_validation_and_plain_text_decoding(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return json.dumps({'responseStatus':200,'responseData':{'translatedText':'棒球帽 &amp; 配饰'}}).encode()
        with patch('profit_titles.urllib.request.urlopen',return_value=Response()):
            self.assertEqual(titles.translate_segment('Бейсболка'),'棒球帽 & 配饰')

    def test_chinese_search_and_status_fields_require_authentication(self):
        titles.refresh_titles(self.store,self.now,lambda text:'棒球帽')
        app=create_app({'database':str(self.store.path),'access_token':'test','secret_key':'test','secure_cookie':False})
        c=app.test_client();prefix='/ozon-profit/api'
        self.assertEqual(c.get(prefix+'/orders?q=棒球帽').status_code,401)
        c.post(prefix+'/login',json={'token':'test'})
        data=c.get(prefix+'/orders?q=棒球帽').json['orders']
        self.assertEqual(len(data),1);self.assertEqual(data[0]['lines'][0]['name_zh'],'棒球帽')
        self.assertIn('status_label',data[0]);self.assertIn('status_checked_at',data[0])

    def test_baidu_request_uses_utf8_signature_and_returns_plain_chinese(self):
        text='Шарнирный соединитель для строительных конструкторов 517 деталей'
        settings={'provider':'baidu','app_id':'123','app_key':'test-only-secret'}
        response=io.BytesIO(json.dumps({'trans_result':[{'src':text,'dst':'拼装积木铰链连接件，517件'}]}).encode())
        with patch('profit_titles.secrets.token_hex',return_value='salt'),patch('profit_titles.urllib.request.urlopen',return_value=response) as request:
            self.assertEqual(titles.translate_baidu_segment(text,settings),'拼装积木铰链连接件，517件')
        sent=request.call_args.args[0]
        self.assertEqual(sent.full_url,'https://fanyi-api.baidu.com/api/trans/vip/translate')
        self.assertEqual(sent.get_method(),'POST')
        body=urllib.parse.parse_qs(sent.data.decode())
        self.assertEqual(body['q'],[text]);self.assertEqual(body['from'],['ru']);self.assertEqual(body['to'],['zh'])
        self.assertEqual(body['sign'],[hashlib.md5(('123'+text+'salt'+'test-only-secret').encode()).hexdigest()])
        self.assertNotIn('test-only-secret',sent.full_url);self.assertNotIn('test-only-secret',sent.data.decode())

    def test_baidu_error_body_does_not_leak_provider_details_or_keys(self):
        response=io.BytesIO(json.dumps({'error_code':'54001','error_msg':'private-secret-echo'}).encode())
        with patch('profit_titles.urllib.request.urlopen',return_value=response),self.assertRaises(titles.TranslationError) as error:
            titles.translate_baidu_segment('Бейсболка',{'app_id':'123','app_key':'test-only-secret'})
        self.assertEqual(str(error.exception),'configuration')

    def test_provider_switch_refreshes_machine_cache_and_preserves_reviewed_title(self):
        titles.refresh_titles(self.store,self.now,lambda text:'旧机翻')
        calls=[];settings={'provider':'baidu','app_id':'123','app_key':'test-only-secret'}
        result=titles.refresh_titles(self.store,self.now+1,lambda text:calls.append(text) or '新中文标题',settings)
        self.assertEqual(result['translated'],1)
        with self.store.connection() as db:
            self.assertEqual(core.load_order(db,1)['lines'][0]['translation_provider'],'Baidu')
            db.execute("UPDATE profit_title_translations SET zh_text='已核实中文',provider='reviewed'")
        result=titles.refresh_titles(self.store,self.now+2,lambda text:self.fail('Reviewed text must not be retranslated'),settings)
        self.assertEqual(result['translated'],0)
        self.assertEqual(len(calls),1)

    def test_baidu_monthly_budget_is_separate_and_blocks_before_network(self):
        settings={'provider':'baidu','app_id':'123','app_key':'test-only-secret','monthly_character_budget':10}
        with self.store.connection() as db:
            db.execute('INSERT INTO profit_translation_requests(created,characters,provider) VALUES (?,?,?)',(self.now-60,10,'Baidu'))
            db.execute('INSERT INTO profit_translation_requests(created,characters,provider) VALUES (?,?,?)',(self.now-60,3999,'MyMemory'))
        calls=[]
        result=titles.refresh_titles(self.store,self.now,lambda text:calls.append(text),settings)
        self.assertTrue(result['blocked']);self.assertEqual(calls,[])
        with self.store.connection() as db:
            blocked=db.execute("SELECT value FROM profit_translation_control WHERE key='blocked-until:Baidu'").fetchone()[0]
        self.assertGreater(blocked,self.now)
        self.assertEqual(dt.datetime.fromtimestamp(blocked,core.CHINA).day,1)
        self.assertEqual(titles.provider_options({**settings,'monthly_character_budget':99999999}),('Baidu',45000))

    def test_missing_baidu_key_does_not_silently_use_old_provider(self):
        with self.assertRaises(titles.TranslationError),patch('profit_titles.urllib.request.urlopen') as request:
            titles.refresh_titles(self.store,self.now,settings={'provider':'baidu','app_id':'123','app_key':''})
        request.assert_not_called()

    def test_mymemory_cleanup_cannot_remove_current_baidu_month_usage(self):
        now=dt.datetime(2026,10,30,16,tzinfo=core.CHINA).timestamp()
        with self.store.connection() as db:
            db.execute('INSERT INTO profit_translation_requests(created,characters,provider) VALUES (?,?,?)',(now-10*86400,100,'Baidu'))
        titles.refresh_titles(self.store,now,lambda text:'棒球帽',{'provider':'mymemory'})
        with self.store.connection() as db:self.assertEqual(db.execute("SELECT SUM(characters) FROM profit_translation_requests WHERE provider='Baidu'").fetchone()[0],100)

    def test_siliconflow_only_allows_selected_free_model(self):
        settings={'provider':'siliconflow','api_key':'test-only-secret','model':'tencent/Hunyuan-MT-7B'}
        self.assertEqual(titles.provider_options(settings),('SiliconFlow/Hunyuan-MT-7B',20000))
        with self.assertRaises(titles.TranslationError):titles.provider_options({**settings,'model':'Pro/paid-model'})
        with self.assertRaises(titles.TranslationError):titles.provider_options({**settings,'api_key':''})

    def test_translation_keeps_specs_and_building_block_sense(self):
        source='Шарнирный соединитель для строительных конструкторов 517 деталей'
        self.assertEqual(titles.validate_title_specs(source,'拼装积木铰链连接件，517件','儿童用品 / 积木'),'拼装积木铰链连接件，517件')
        with self.assertRaisesRegex(titles.TranslationError,'specification-mismatch'):
            titles.validate_title_specs(source,'拼装积木铰链连接件，571件','儿童用品 / 积木')
        with self.assertRaisesRegex(titles.TranslationError,'category-mismatch'):
            titles.validate_title_specs(source,'建筑施工用铰链连接器，517件','儿童用品 / 积木')
        with self.assertRaisesRegex(titles.TranslationError,'specification-mismatch'):
            titles.validate_title_specs('Аккумулятор BLP899 для OnePlus 10 Pro NE2210 5000 мАч','OnePlus 10 Pro手机电池 NE2210，5000毫安时')
        self.assertEqual(titles.validate_title_specs('Стеллаж 1/10 RC','1/10 RC轮胎收纳架'),'1/10 RC轮胎收纳架')

    def test_siliconflow_sends_only_title_context_and_does_not_expose_key_in_body(self):
        settings={'provider':'siliconflow','api_key':'test-only-secret','model':'tencent/Hunyuan-MT-7B'}
        response=io.BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'拼装积木连接件，517件'}}]}).encode())
        with patch('profit_titles.urllib.request.urlopen',return_value=response) as request:
            result=titles.translate_siliconflow_title('Соединитель для конструкторов 517 деталей',settings,'儿童用品 / 积木')
        self.assertEqual(result,'拼装积木连接件，517件')
        sent=request.call_args.args[0];body=json.loads(sent.data)
        self.assertEqual(sent.full_url,'https://api.siliconflow.cn/v1/chat/completions')
        self.assertEqual(body['model'],'tencent/Hunyuan-MT-7B')
        self.assertIn('儿童用品 / 积木',body['messages'][0]['content'])
        self.assertNotIn('test-only-secret',sent.data.decode());self.assertNotIn('api_key',body)

    def test_siliconflow_errors_never_copy_remote_body_or_key(self):
        settings={'provider':'siliconflow','api_key':'test-only-secret','model':'tencent/Hunyuan-MT-7B'}
        error=urllib.error.HTTPError('https://api.siliconflow.cn/v1/chat/completions',401,'Unauthorized',{},io.BytesIO(b'private-secret-echo'))
        with patch('profit_titles.urllib.request.urlopen',side_effect=error),self.assertRaises(titles.TranslationError) as caught:
            titles.translate_siliconflow_title('Бейсболка',settings,'服饰 / 棒球帽')
        self.assertEqual(str(caught.exception),'configuration')

    def test_cached_category_context_needs_no_external_request(self):
        with self.store.connection() as db:
            db.execute('INSERT INTO profit_title_product_context VALUES (?,?,?,?)',('s1','offer:cap','服饰 / 棒球帽',self.now))
        with patch('monitor.load_config') as load:
            self.assertEqual(titles.context_for_title(self.store,'Бейсболка',self.now),'服饰 / 棒球帽')
        load.assert_not_called()
        parsed=titles.category_rows([{'description_category_id':1,'category_name':'儿童用品','children':[
            {'type_id':92952,'type_name':'积木','children':[]}]}],self.now)
        self.assertIn(('type','92952','儿童用品 / 积木',self.now),parsed)

    def test_quality_failure_does_not_block_other_titles_or_overwrite_existing_cache(self):
        with self.store.connection() as db:
            db.execute("INSERT INTO profit_title_translations VALUES ('Бейсболка','原有中文','MyMemory',?,0,'')",(self.now,))
            second={**self.post,'posting_number':'A-2','products':[{**self.post['products'][0],'offer_id':'new','name':'Сумка'}]}
            core.capture_postings(db,self.shop,'fbs',[second],self.now)
        def translator(text):
            if text=='Бейсболка':raise titles.TranslationError('specification-mismatch')
            return '单肩包'
        settings={'provider':'siliconflow','api_key':'test-only-secret','model':'tencent/Hunyuan-MT-7B'}
        result=titles.refresh_titles(self.store,self.now,translator,settings)
        self.assertEqual(result,{'translated':1,'blocked':False})
        with self.store.connection() as db:
            self.assertEqual(core.load_order(db,1)['lines'][0]['name_zh'],'原有中文')
            self.assertEqual(core.load_order(db,2)['lines'][0]['name_zh'],'单肩包')

    def test_title_guard_rejects_invented_use_and_cleans_presentation_only(self):
        source='Эксклюзивный выживательный набор с 11-мм слотом'
        with self.assertRaises(titles.TranslationError):
            titles.validate_title_specs(source,'生存工具包，11毫米弹孔，用于安装枪械')
        self.assertEqual(titles.validate_title_specs('Бейсболка','**棒球帽**'),'棒球帽')
        self.assertEqual(titles.validate_title_specs('Бейсболка','“棒球帽”'),'棒球帽')


    def test_local_model_uses_loopback_and_validates_specs(self):
        source='Тренажер для шеи JSQ-050'
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'颈部训练器 JSQ-050'}}]}).encode()
        with patch('profit_titles.urllib.request.urlopen',return_value=Response()) as request:
            self.assertEqual(titles.translate_local_title(source,'运动 / 健身器材'),'颈部训练器 JSQ-050')
        sent=request.call_args.args[0]
        self.assertEqual(sent.full_url,'http://127.0.0.1:5311/v1/chat/completions')
        self.assertNotIn('Authorization',dict(sent.header_items()))
        self.assertIn('JSQ-050',json.loads(sent.data)['messages'][0]['content'])
        class MissingModel(Response):
            def read(self,*args):return json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'颈部训练器'}}]}).encode()
        with patch('profit_titles.urllib.request.urlopen',return_value=MissingModel()):
            with self.assertRaisesRegex(titles.TranslationError,'specification-mismatch'):
                titles.translate_local_title(source,'运动 / 健身器材')

    def test_local_provider_preserves_existing_translations_and_only_fills_missing(self):
        with self.store.connection() as db:
            db.execute("INSERT INTO profit_title_translations VALUES ('Бейсболка','棒球帽','SiliconFlow/Hunyuan-MT-7B',?,0,'')",(self.now,))
            second={**self.post,'posting_number':'A-2','products':[{**self.post['products'][0],'offer_id':'new','name':'Тренажер для шеи JSQ-050'}]}
            core.capture_postings(db,self.shop,'fbs',[second],self.now)
        calls=[]
        result=titles.refresh_titles(self.store,self.now,lambda text:calls.append(text) or '颈部训练器 JSQ-050',{'provider':'local-hymt'})
        self.assertEqual(result,{'translated':1,'blocked':False})
        self.assertEqual(calls,['Тренажер для шеи JSQ-050'])
        self.assertEqual(titles.refresh_titles(self.store,self.now+1,lambda text:self.fail('cached'),{'provider':'local-hymt'})['translated'],0)
        with self.store.connection() as db:
            self.assertEqual(core.load_order(db,1)['lines'][0]['name_zh'],'棒球帽')
            self.assertEqual(core.load_order(db,2)['lines'][0]['translation_provider'],'Local/HY-MT1.5-1.8B')

    def test_failed_translation_reason_is_exposed_without_remote_error_text(self):
        def failed(text):raise titles.TranslationError('network-or-response')
        titles.refresh_titles(self.store,self.now,failed,{'provider':'local-hymt'})
        with self.store.connection() as db:line=core.load_order(db,1)['lines'][0]
        self.assertEqual(line['translation_error'],'network-or-response')
        self.assertEqual(line['translation_retry_at'],self.now+30)
        self.assertEqual(line['name_zh'],'')


if __name__=='__main__':unittest.main()
