import tempfile,unittest
from pathlib import Path
from bid_agent.store import Store
from bid_agent.tools import ProjectTools
class HistorySearchTests(unittest.TestCase):
 def test_query_and_retrieval_echo_are_not_evidence(self):
  with tempfile.TemporaryDirectory() as tmp:
   s=Store(Path(tmp)/'s.sqlite');r=s.create('task',{'project':tmp});tools=ProjectTools(tmp,s,r)
   args={'query':'unique_missing_term','limit':10};call=s.begin_call(r,tools.tools['search_history'],args)
   with s.connect() as c:s.event(c,r,'message',{'role':'assistant','content':'Search unique_missing_term'})
   self.assertEqual(tools.tools['search_history'].handler(**args)['results'],[])
   s.end_call(r,call,{'ok':True,'data':{'results':[{'snippet':'unique_missing_term'}]}})
   self.assertEqual(tools.tools['search_history'].handler(**args)['results'],[])
   with s.connect() as c:s.event(c,r,'note',{'source':'file','text':'unique_missing_term real source'})
   found=tools.tools['search_history'].handler(**args)['results'];self.assertEqual(len(found),1);self.assertEqual(found[0]['kind'],'note')
