import io,json,tempfile,unittest,zipfile
from pathlib import Path
import profit_core as core
from profit_web import create_app

class SyncWebTests(unittest.TestCase):
    def test_extension_contract_preview_apply_undo_and_legacy_routes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);database=root/'profit.sqlite3';cfg=root/'config.json'
            cfg.write_text(json.dumps({'shops':[{'id':'shop01','name':'Luo001'}]}))
            app=create_app(dict(secret_key='test-only',public_access=True,secure_cookie=False,access_token='unused',
                database=str(database),overweight_database=str(root/'weight.sqlite3'),monitor_config=str(cfg),shops=[{'id':'shop01','name':'Luo001'}]))
            with core.connection(database) as db:
                core.capture_postings(db,{'id':'shop01','name':'Luo001'},'fbs',[dict(posting_number='12345678-0001-1',order_id=1,created_at='2026-11-01T01:00:00Z',status='awaiting_packaging',products=[dict(offer_id='offer',sku=6169746567,name='test',quantity=2,weight=.1,price={'amount':'25','currency':'CNY'})])],1)
            client=app.test_client();base='/ozon-profit/api/purchase-sync/'
            for path in ['/ozonzusou/purchase-sync/','/ozonzusou/purchase-sync/assets/purchase-sync.js','/ozon-profit/','/ozonzusou/overweight/']:
                response=client.get(path);self.assertEqual(response.status_code,200);response.close()
            response=client.get('/ozonzusou/purchase-sync/download');self.assertEqual(response.status_code,200)
            z=zipfile.ZipFile(io.BytesIO(response.data));manifest=json.loads(z.read('ozon-miaoshou-sync/manifest.json'))
            self.assertEqual(manifest['manifest_version'],3);self.assertNotIn('cookies',manifest['permissions']);response.close()
            token=client.get(base+'state').get_json()['csrf'];headers={'X-CSRF-Token':token}
            data={'records':[dict(shop_name='Luo001',posting='12345678-0001-1',offer_id='offer',sku='6169746567',quantity='2',cost_total='31.07',note='854g')]}
            self.assertEqual(client.post(base+'preview',json=data).status_code,403)
            p=client.post(base+'preview',json=data,headers=headers).get_json();self.assertEqual(len(p['entries'][0]['changes']),2)
            selected=[c['id'] for c in p['entries'][0]['changes']]
            self.assertEqual(client.post(base+'apply',json={'id':p['id'],'selected':selected},headers=headers).status_code,400)
            project=client.post(base+'project',json={'id':p['id'],'selected':selected},headers=headers)
            self.assertEqual(project.status_code,200)
            with core.connection(database) as db:self.assertIsNone(core.load_order(db,1)['lines'][0]['purchase_unit'])
            applied=client.post(base+'apply',json={'id':p['id'],'selected':selected,'confirm':True},headers=headers)
            self.assertEqual(applied.status_code,200);self.assertEqual(applied.get_json()['fields'],2)
            self.assertEqual(client.post(base+'undo',json={'id':p['id'],'confirm':True},headers=headers).status_code,200)
            with core.connection(database) as db:self.assertIsNone(core.load_order(db,1)['lines'][0]['purchase_unit'])

if __name__=='__main__':unittest.main()
