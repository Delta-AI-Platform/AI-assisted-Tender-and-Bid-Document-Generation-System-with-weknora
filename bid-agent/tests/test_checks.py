import unittest
from bid_agent.checks import match_history, check_coverage

class CheckTests(unittest.TestCase):
    def test_no_fuzzy_model_or_arbitrary_first(self):
        rows=[{'model':'A','value':900},{'model':'AB','value':1200},{'model':'A','value':950}]
        result=match_history(rows,'A')
        self.assertEqual(len(result['matches']),2)
        self.assertIsNone(result['selection'])
        with self.assertRaises(ValueError): match_history(rows,'')
    def test_full_text_and_order(self):
        text='要求'*50
        source=[{'id':'1','requirement':text},{'id':'2','requirement':'另一个要求'}]
        self.assertFalse(check_coverage(source,[{'id':'1','requirement':text+'改'}])['ok'])
        self.assertFalse(check_coverage(source,list(reversed(source)))['ok'])
        self.assertTrue(check_coverage(source,source)['ok'])
    def test_empty_stays_empty(self):
        row={'id':'1','requirement':'必须提供报告','deviation':''}
        check_coverage([row],[row])
        self.assertEqual(row['deviation'],'')
        self.assertFalse(check_coverage([row],[{**row,'deviation':'无偏离'}])['ok'])
