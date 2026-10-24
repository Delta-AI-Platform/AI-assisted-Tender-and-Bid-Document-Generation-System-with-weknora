import tempfile,unittest
from pathlib import Path
from bid_agent.store import Store
class ResumeStatusTest(unittest.TestCase):
 def test_resume_clears_stale_result_keeps_history(self):
  with tempfile.TemporaryDirectory() as tmp:
   s=Store(Path(tmp)/'state.sqlite');r=s.create('test',{'project':tmp})
   s.transition(r,'yielded',{'reason':'step_budget'})
   self.assertEqual(s.claim(r,'owner',60),'running')
   self.assertIsNone(s.get(r)['last_result'])
   with s.connect() as c:
    self.assertEqual(c.execute("select count(*) from events where run_id=? and kind='yielded'",(r,)).fetchone()[0],1)
