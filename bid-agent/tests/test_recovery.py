import json
import tempfile
import unittest
from pathlib import Path
from bid_agent.store import Store
from bid_agent.tools import ProjectTools
from bid_agent.core import Agent
from bid_agent.recovery import recover

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.store=Store(self.root/'state.sqlite');self.run=self.store.create('write',{})
        self.registry=ProjectTools(self.root,self.store,self.run)
    def interrupted(self,content):
        args={'path':'result.txt','content':'expected'}
        agent=Agent(self.store,None,self.registry)
        agent.message(self.run,{'role':'assistant','tool_calls':[{'id':'model-call','type':'function','function':{'name':'write_text','arguments':json.dumps(args)}}]})
        self.store.begin_call(self.run,self.registry.tools['write_text'],args,model_call_id='model-call')
        if content is not None: (self.root/'result.txt').write_text(content)
        self.assertEqual(agent.run(self.run)['status'],'needs_recovery')
        return agent
    def test_committed_write_recovered_as_real_observation(self):
        agent=self.interrupted('expected')
        result=recover(self.store,self.run,self.registry)
        self.assertEqual(result['status'],'pending')
        message=agent.messages(self.run)[-1]
        self.assertTrue(json.loads(message['content'])['ok'])
        self.assertTrue(json.loads(message['content'])['recovered'])
    def test_partial_file_is_preserved_and_not_replayed(self):
        self.interrupted('partial')
        result=recover(self.store,self.run,self.registry)
        self.assertEqual(result['status'],'needs_recovery')
        self.assertEqual((self.root/'result.txt').read_text(),'partial')
    def test_absent_output_can_resume_with_failure_observation(self):
        agent=self.interrupted(None)
        self.assertEqual(recover(self.store,self.run,self.registry)['status'],'pending')
        self.assertFalse(json.loads(agent.messages(self.run)[-1]['content'])['ok'])
