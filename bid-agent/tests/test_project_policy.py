import copy
import unittest
from bid_agent.project_policy import audit_policy


class ProjectPolicyTests(unittest.TestCase):
    def policy(self):
        return {'project_id':'current','evaluation_method':'qualitative',
                'performance':{'required':False,'submitted':True,'status':'not_applicable',
                               'requirement_refs':['tender:p5'],'evidence_refs':['contract'],'claimed_points':None},
                'forms':[{'form_id':'deviation','mode':'fill_from_evidence','authority_refs':['tender:p49']}]}
    def test_current_project_can_submit_performance_and_fill_forms(self):
        result=audit_policy(self.policy())
        self.assertTrue(result['structurally_consistent'])
        self.assertEqual(result['quality_status'],'not_verified')
    def test_missing_state_is_not_defaulted_and_false_met_is_rejected(self):
        p=self.policy();del p['performance']['submitted']
        self.assertTrue(audit_policy(p)['errors'])
        p=self.policy();p['performance'].update(required=True,submitted=False,status='met')
        self.assertTrue(audit_policy(p)['errors'])
    def test_old_blank_policy_conflict_is_retained(self):
        p=self.policy();p['forms'][0].update(mode='blank',procurement_requires_filling=True)
        self.assertTrue(audit_policy(p)['open_items'])
        p['performance']['claimed_points']=0
        self.assertTrue(audit_policy(p)['errors'])
