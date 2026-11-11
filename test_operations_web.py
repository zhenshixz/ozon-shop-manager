"""HTTP contract checks against isolated databases; no real API or push calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import profit_web

class OperationsWebTests(unittest.TestCase):
    def test_new_and_legacy_routes_csrf_and_threshold_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);config=root/'config.json'
            config.write_text(json.dumps({'product_digest_hour':22,'shops':[{'id':'test','name':'Test shop'}]}))
            app=profit_web.create_app(dict(secret_key='isolated-test-secret',access_token='unused',public_access=True,
                secure_cookie=False,database=str(root/'profit.sqlite3'),overweight_database=str(root/'weight.sqlite3'),
                monitor_config=str(config),shops=[{'id':'test','name':'Test shop'}]))
            app.config['TESTING']=True;client=app.test_client()
            for url in ('/ozonzusou/','/ozonzusou/overweight/','/ozonzusou/profit/','/ozonzusou/promotions/',
                        '/ozon-profit/','/ozonzusou/assets/operations.js'):
                response=client.get(url)
                self.assertEqual(response.status_code,200,url)
                response.close()
            base='/ozon-profit/api/operations/'
            data=client.get(base+'state').get_json();token=data['csrf']
            self.assertEqual(data['rule']['manual'],2000)
            self.assertEqual(client.post(base+'rule',json={'weight':3000}).status_code,403)
            headers={'X-CSRF-Token':token}
            reply=client.post(base+'rule',json={'weight':3000},headers=headers)
            self.assertEqual(reply.status_code,200)
            self.assertEqual(reply.get_json()['rule']['manual'],3000)
            self.assertEqual(reply.get_json()['rule']['automatic'],2000)
            self.assertEqual(client.post(base+'archive',json={'selection':[]},headers=headers).status_code,400)
            self.assertEqual(client.post(base+'rule',json={'weight':-1},headers=headers).status_code,400)
            with patch.object(app.extensions['overweight'],'launch'):
                reply=client.post(base+'scan',json={'shops':['test']},headers=headers)
            self.assertEqual(reply.status_code,202)
            job=app.extensions['overweight'].job(reply.get_json()['id'])
            self.assertEqual(job['threshold'],3000)
            self.assertEqual(client.get('/ozon-profit/api/bootstrap').status_code,200)

if __name__=='__main__':unittest.main()
