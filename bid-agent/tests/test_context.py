import json
import tempfile
import unittest
from pathlib import Path
from bid_agent.context import build_messages
from bid_agent.store import Store
from bid_agent.tools import ProjectTools

class ContextTests(unittest.TestCase):
    def test_large_result_is_archived_with_retrievable_sequence(self):
        context={'run':{'objective':'task'},'memory':[],'project_decisions':[],
                 'events':[{'sequence':1,'kind':'message','payload':{'role':'assistant','tool_calls':[{'id':'a','function':{'name':'read_text','arguments':'{}'}}]}},
                           {'sequence':2,'kind':'message','payload':{'role':'tool','tool_call_id':'a','content':'原文'*15000}}]}
        messages=build_messages(context,'system',max_characters=20000)
        result=json.loads(messages[-1]['content'])
        self.assertTrue(result['archived']);self.assertEqual(result['event_sequence'],2)
        self.assertEqual(context['events'][-1]['payload']['content'],'原文'*15000)
        self.assertLess(len(json.dumps(messages,ensure_ascii=False)),10000)
    def test_latest_large_observation_is_delivered_before_archiving(self):
        original='采购原文'*7000
        context={'run':{'objective':'task'},'memory':[], 'events':[
            {'sequence':1,'kind':'message','payload':{'role':'assistant','tool_calls':[{'id':'a','function':{'name':'read_pdf_pages','arguments':'{}'}}]}},
            {'sequence':2,'kind':'message','payload':{'role':'tool','tool_call_id':'a','content':original}}]}
        messages=build_messages(context,'sys')
        self.assertEqual(messages[-1]['content'],original)
        context['events'].append({'sequence':3,'kind':'message','payload':{'role':'assistant','content':'observed'}})
        messages=build_messages(context,'sys')
        self.assertEqual(messages[-2]['content'],original)
    def test_compaction_keeps_tool_protocol_groups(self):
        events=[]
        for i in range(30):
            events.extend([{'sequence':2*i,'kind':'message','payload':{'role':'assistant','content':'x'*2000,'tool_calls':[{'id':str(i),'function':{'name':'x','arguments':'{}'}}]}},
                           {'sequence':2*i+1,'kind':'message','payload':{'role':'tool','tool_call_id':str(i),'content':'y'*2000}}])
        result=build_messages({'run':{'objective':'task'},'memory':[],'events':events},'sys',max_characters=15000)
        pending=set()
        for message in result:
            for call in message.get('tool_calls',[]): pending.add(call['id'])
            if message['role']=='tool':
                self.assertIn(message['tool_call_id'],pending);pending.remove(message['tool_call_id'])
        self.assertFalse(pending)
        self.assertEqual(result[-1]['tool_call_id'],'29')
    def test_history_access_is_project_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);s=Store(root/'state.sqlite');r=s.create('a',{'project':'a'});other=s.create('b',{'project':'b'})
            with s.connect() as conn:
                s.event(conn,r,'note',{'text':'private a'});s.event(conn,other,'note',{'text':'private b'})
            t=ProjectTools(root,s,r)
            found=t.tools['search_history'].handler(query='private',limit=10)['results']
            self.assertEqual(len(found),1)
            own=t.tools['read_event'].handler(sequence=found[0]['sequence'],offset=0,limit=200)
            self.assertIn('private a',own['content'])
            with s.connect() as conn: sequence=conn.execute('SELECT MAX(sequence) FROM events').fetchone()[0]
            with self.assertRaises(ValueError): t.tools['read_event'].handler(sequence=sequence,offset=0,limit=100)
