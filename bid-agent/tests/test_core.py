import json
import tempfile
import unittest
from pathlib import Path
from bid_agent.store import Store
from bid_agent.tools import ProjectTools
from bid_agent.core import Agent

class Model:
    def __init__(self, calls): self.calls=iter(calls); self.observations=[]
    def decide(self,messages,tools):
        self.observations.append(messages)
        name,args=next(self.calls)
        return {'role':'assistant','tool_calls':[{'id':str(len(self.observations)),'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}

class Tests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        root=Path(tmp.name); self.project=root/'project'; self.project.mkdir()
        self.store=Store(root/'state.sqlite'); self.id=self.store.create('test',{})
        self.tools=ProjectTools(self.project,self.store,self.id)
    def test_repair(self):
        (self.project/'source').write_text('原文')
        model=Model([('read_text',{'path':'missing'}),('read_text',{'path':'source'}),('write_text',{'path':'source','content':'覆盖'}),('write_text',{'path':'result','content':'原文'}),('finish_candidate',{'summary':'完成','artifacts':['result'],'open_items':[]})])
        result=Agent(self.store,model,self.tools).run(self.id)
        self.assertEqual(result['status'],'candidate')
        self.assertEqual(result['last_result']['quality_status'],'not_verified')
        self.assertEqual((self.project/'source').read_text(),'原文')
        self.assertIn('FileNotFoundError',json.dumps(model.observations))
        self.assertIn('FileExistsError',json.dumps(model.observations))
    def test_resume(self):
        model=Model([('remember',{'kind':'open_item','key':'a','value':{'text':'pending'}})])
        self.assertEqual(Agent(self.store,model,self.tools).run(self.id,1)['status'],'yielded')
        model=Model([('ask_user',{'question':'提供资料'})])
        self.assertEqual(Agent(self.store,model,self.tools).run(self.id)['status'],'waiting_input')
        self.assertIn('pending',json.dumps(model.observations))
        self.assertEqual([m['role'] for m in model.observations[0]].count('system'),1)
        self.assertEqual(model.observations[0][0]['role'],'system')
        self.store.answer(self.id,'稍后'); self.assertEqual(self.store.get(self.id)['status'],'pending')
    def test_uncertain_write(self):
        self.store.begin_call(self.id,self.tools.tools['write_text'],{'path':'a','content':'x'})
        self.assertEqual(Agent(self.store,Model([]),self.tools).run(self.id)['status'],'needs_recovery')
    def test_fake_artifact(self):
        model=Model([('finish_candidate',{'summary':'done','artifacts':['fake'],'open_items':[]})])
        self.assertEqual(Agent(self.store,model,self.tools).run(self.id,1)['status'],'yielded')
    def test_boundaries(self):
        for path in ('../secret','/etc/passwd'):
            with self.assertRaises(ValueError): self.tools.read_text(path)
        (self.project/'link').symlink_to('/etc/passwd')
        with self.assertRaises(ValueError): self.tools.read_text('link')
        with self.assertRaises(ValueError): self.store.remember(self.id,'authorization','a',{})
        with self.assertRaises(ValueError): self.store.remember(self.id,'fact','a',{})
    def test_live_lock(self):
        agent=Agent(self.store,Model([]),self.tools)
        with agent.lock(self.id):
            with self.assertRaises(RuntimeError): agent.run(self.id)
    def test_cancel(self):
        self.store.transition(self.id,'canceled',{})
        self.store.transition(self.id,'candidate',{})
        self.assertEqual(self.store.get(self.id)['status'],'canceled')

if __name__=='__main__': unittest.main()

class ProjectMemoryTests(unittest.TestCase):
    def test_project_memory_is_shared_but_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'state.sqlite')
            first=store.create('first',{'project':'/project/a'})
            second=store.create('second',{'project':'/project/a'})
            other=store.create('other',{'project':'/project/b'})
            store.remember(first,'open_item','x',{'text':'missing report'})
            store.transition(first,'waiting_input',{'question':'是否已有报告？'})
            store.answer(first,'稍后提供')
            self.assertEqual(len(store.context(second)['memory']),1)
            self.assertEqual(store.context(second)['project_decisions'][0]['payload']['question'],'是否已有报告？')
            self.assertEqual(store.context(other)['memory'],[])
            self.assertEqual(store.context(other)['project_decisions'],[])

class RetryTests(unittest.TestCase):
    def test_incomplete_model_response_retries_without_tool_side_effect(self):
        from bid_agent.provider import ProviderError
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);s=Store(root/'state.sqlite');r=s.create('retry',{})
            class Flaky(Model):
                attempts=0
                def decide(self,messages,tools):
                    self.attempts+=1
                    if self.attempts==1: raise ProviderError('Model stream ended without completion; partial tools not executed')
                    return super().decide(messages,tools)
            model=Flaky([('ask_user',{'question':'补充证据'})])
            result=Agent(s,model,ProjectTools(root,s,r)).run(r)
            self.assertEqual(result['status'],'waiting_input');self.assertEqual(model.attempts,2)
            events=s.context(r)['events']
            self.assertEqual(len([e for e in events if e['kind']=='model_retry']),1)
            self.assertFalse(any(e['kind']=='tool_started' for e in events))
