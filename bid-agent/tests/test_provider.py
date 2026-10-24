import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from bid_agent.provider import Provider

class ProviderTests(unittest.TestCase):
    def test_invalid_history_is_transport_only_and_disclosed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'provider.json'
            p.write_text(json.dumps({'base_url':'https://example.invalid/v3','model':'test','api_key':'test-only','stream':False}));os.chmod(p,0o600)
            messages=[{'role':'assistant','tool_calls':[{'id':'bad','type':'function','index':0,'function':{'name':'remember','arguments':'{"x":'}}]},
                      {'role':'tool','tool_call_id':'bad','content':'JSONDecodeError; not executed'}]
            captured=[]
            class Opener:
                def open(self,request,timeout):
                    captured.append(json.loads(request.data))
                    return io.BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'ok'}}]}).encode())
            with patch('urllib.request.build_opener',return_value=Opener()):
                Provider(p).decide(messages,[])
            self.assertEqual(messages[0]['tool_calls'][0]['function']['arguments'],'{"x":')
            self.assertEqual(captured[0]['messages'][0]['tool_calls'][0]['function']['arguments'],'{}')
            self.assertIn('not executed',captured[0]['messages'][1]['content'])
            self.assertNotIn('test-only',json.dumps(captured[0]))

class StreamingTests(unittest.TestCase):
    def provider(self):
        provider=Provider.__new__(Provider);provider.on_progress=None
        return provider
    def test_interleaved_tool_chunks_are_assembled(self):
        chunks=[{'choices':[{'index':0,'delta':{'tool_calls':[{'index':0,'id':'a','function':{'name':'read_text','arguments':'{"pa'}},{'index':1,'id':'b','function':{'name':'read_text','arguments':'{"path":'}}]}}]},
                {'choices':[{'index':0,'delta':{'tool_calls':[{'index':1,'function':{'arguments':'"b"}'}},{'index':0,'function':{'arguments':'th":"a"}'}}]},'finish_reason':'tool_calls'}]}]
        stream=io.BytesIO(b''.join(b'data: '+json.dumps(c).encode()+b'\n\n' for c in chunks)+b'data: [DONE]\n')
        calls=self.provider().read_stream(stream)['choices'][0]['message']['tool_calls']
        self.assertEqual([json.loads(c['function']['arguments'])['path'] for c in calls],['a','b'])
    def test_incomplete_stream_cannot_execute(self):
        from bid_agent.provider import ProviderError
        with self.assertRaises(ProviderError): self.provider().read_stream(io.BytesIO(b'data: {"choices":[]}\n'))
