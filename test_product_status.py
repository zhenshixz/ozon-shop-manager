import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import product_status as ps

class StatusTest(unittest.TestCase):
    def test_real_hidden_reason_and_archive_priority(self):
        item={'is_archived':False,'errors':[], 'statuses':{'moderate_status':'approved'},'availabilities':[{'sku':123,'availability':'HIDDEN','reasons':[{'id':6},{'id':81}]}]}
        self.assertEqual(ps.classify(item,123),{'state':'removed','reasons':['侵犯版权','缺少品牌文件']})
        item['is_archived']=True
        self.assertEqual(ps.classify(item,123),{'state':'archived','reasons':[]})

    def test_sku_sources_unknown_and_warning(self):
        item={'errors':[{'level':'ERROR_LEVEL_WARNING'}], 'availabilities':[{'sku':123,'availability':'AVAILABLE'},{'sku':456,'availability':'HIDDEN'}]}
        self.assertEqual(ps.classify(item,123)['state'],'active')
        self.assertEqual(ps.classify(item,789)['state'],'unknown')
        item['availabilities']=[{'sku':123,'availability':'UNRECOGNIZED'}]
        self.assertEqual(ps.classify(item,123)['state'],'unknown')

    def test_cache_partitions_and_missing(self):
        data={('a','sku','123'):{'state':'removed'},('b','sku','123'):{'state':'active'},('a','offer','x'):{'state':'archived'}}
        self.assertEqual(ps.lookup(data,'a',123,'x')['state'],'removed')
        self.assertEqual(ps.lookup(data,'b',123,'x')['state'],'active')
        self.assertEqual(ps.lookup(data,'b',456,'x')['state'],'unknown')
        self.assertEqual(ps.lookup(data,'a',0,'x')['state'],'archived')
        with tempfile.TemporaryDirectory() as d, patch.object(ps,'CACHE',Path(d)/'missing.sqlite3'):
            self.assertEqual(ps.snapshots(),{})

if __name__=='__main__': unittest.main()
