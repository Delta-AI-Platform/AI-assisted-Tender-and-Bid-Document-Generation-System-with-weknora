import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from bid_agent.store import Store
from bid_agent.tools import ProjectTools

class RetrievalBoundsTests(unittest.TestCase):
    def test_large_json_child_is_exact_and_project_boundary_holds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'project'; root.mkdir()
            outside=Path(tmp)/'outside.json'; outside.write_text('{"secret":true}')
            data={'large':'原文'*9000,'a/b':{'~key':['要求\\n逐字保留']}}
            raw=json.dumps(data,ensure_ascii=False).encode()
            (root/'source.json').write_bytes(raw)
            (root/'escape.json').symlink_to(outside)
            store=Store(root/'state.sqlite');run=store.create('test',{'project':str(root)})
            tools=ProjectTools(root,store,run)
            read=tools.tools['read_json_value'].handler
            summary=read('source.json','')
            self.assertTrue(summary['truncated']); self.assertNotIn('value',summary)
            value=read('source.json','/a~1b/~0key/0')
            self.assertEqual(value['value'],data['a/b']['~key'][0])
            self.assertEqual(value['sha256'],hashlib.sha256(raw).hexdigest())
            for path in ('../outside.json','escape.json'):
                with self.assertRaises(ValueError):read(path,'')
            chunk=tools.tools['read_text_chunk'].handler
            self.assertEqual(chunk('source.json',4,7)['content'],raw.decode()[4:11])
            for offset,limit in ((-1,5),(0,8001),(True,5)):
                with self.assertRaises(ValueError):chunk('source.json',offset,limit)
