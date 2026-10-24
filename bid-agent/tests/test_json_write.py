import tempfile
import unittest
from pathlib import Path
from bid_agent.store import Store
from bid_agent.tools import ProjectTools


class JsonWriteTests(unittest.TestCase):
    def test_invalid_json_never_creates_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=Store(root/'state.sqlite');run=store.create('test',{})
            tools=ProjectTools(root,store,run)
            for content in ['{} trailing','{"x":1,"x":2}','{"nested":{"x":1,"x":2}}','{"x":NaN}']:
                with self.assertRaises(ValueError):tools.write_text('analysis/bad.json',content)
                self.assertFalse((root/'analysis/bad.json').exists())
            tools.write_text('analysis/good.json','{"quote":"原文","deviation":null}')
            self.assertEqual((root/'analysis/good.json').read_text(),'{"quote":"原文","deviation":null}')
