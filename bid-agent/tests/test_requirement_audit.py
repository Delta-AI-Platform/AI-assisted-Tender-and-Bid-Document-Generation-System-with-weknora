import unittest
from validation.requirements import audit

class RequirementAuditTests(unittest.TestCase):
    def test_quotes_require_exact_conditions_and_real_locations(self):
        pages=[{'page':1,'text':'质保期从最后一次交货后清点完毕起计算。'},
               {'page':2,'text':'不得缺项报价。'}]
        valid={'requirement_id':'r1','quote':'质保期从最后一次交货后\n清点完毕起计算。','source_physical_pages':[1]}
        self.assertTrue(audit([valid],pages,'hash')['passed'])
        for bad in ({**valid,'quote':'质保期从交货日起计算。'}, {**valid,'source_physical_pages':[2]},
                    {**valid,'source_physical_pages':[1,1]}, {**valid,'quote':''}):
            self.assertFalse(audit([bad],pages,'hash')['passed'])
        self.assertFalse(audit([valid,valid],pages,'hash')['passed'])
        self.assertFalse(audit([],pages,'hash')['passed'])
