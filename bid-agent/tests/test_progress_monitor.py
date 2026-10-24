import json,tempfile,unittest
from pathlib import Path
from bid_agent.store import Store
from bid_agent.tools import ProjectTools
from bid_agent.core import Agent
from test_core import Model
class ProgressMonitorTests(unittest.TestCase):
 def test_empty_search_warning_once_then_real_work_continues(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);project=root/'project';project.mkdir();s=Store(root/'s.sqlite');r=s.create('test',{})
   calls=[('search_text',{'query':q,'limit':10}) for q in ['one','two','three','four']]
   calls += [('write_text',{'path':'result.txt','content':'actual result'}),('finish_candidate',{'summary':'candidate','artifacts':['result.txt'],'open_items':[]})]
   model=Model(calls);result=Agent(s,model,ProjectTools(project,s,r)).run(r,6)
   self.assertEqual(result['status'],'candidate');self.assertEqual(result['last_result']['quality_status'],'not_verified')
   with s.connect() as c:
    notes=[json.loads(x[0]) for x in c.execute("select payload from events where run_id=? and kind='recovery_advice'",(r,))]
   self.assertEqual(sum(x['active_code']=='repeated_empty_search' for x in notes),1)
   self.assertEqual((project/'result.txt').read_text(),'actual result')
   self.assertIn('执行器恢复提示',json.dumps(model.observations,ensure_ascii=False))
