import tempfile,unittest,json
from pathlib import Path
from bid_agent.store import Store
from bid_agent.tools import ProjectTools
from bid_agent.context import build_messages
class EventValueTests(unittest.TestCase):
 def test_decodes_tool_message_and_enforces_project_scope(self):
  with tempfile.TemporaryDirectory() as tmp:
   s=Store(Path(tmp)/'s.sqlite');a=s.create('task',{'project':'a'});b=s.create('other',{'project':'b'})
   with s.connect() as c:
    s.event(c,a,'message',{'role':'tool','content':json.dumps({'ok':True,'data':{'tables':[{'rows':132}],'large':'x'*20000}}),'tool_call_id':'c'})
    seq=c.execute('select max(sequence) from events').fetchone()[0]
   t=ProjectTools(tmp,s,a).tools['read_event_value'].handler
   self.assertEqual(t(seq,'/data/tables')['value'],[{'rows':132}]);self.assertTrue(t(seq,'')['truncated'])
   with self.assertRaises(ValueError):ProjectTools(tmp,s,b).tools['read_event_value'].handler(seq,'')
 def test_latest_current_memory_survives_large_shared_memory(self):
  memory=[{'run_id':'old','kind':'plan','key':str(i),'revision':1,'value':{'text':'x'*2000}} for i in range(100)]
  memory += [{'run_id':'current','kind':'plan','key':'next','revision':1,'value':{'text':'USE_BOUND_RECORDS'}}]
  result=build_messages({'run':{'id':'current','objective':'task'},'memory':memory,'events':[]},'sys')
  self.assertIn('USE_BOUND_RECORDS',result[1]['content']);self.assertLess(len(result[1]['content']),17000)
