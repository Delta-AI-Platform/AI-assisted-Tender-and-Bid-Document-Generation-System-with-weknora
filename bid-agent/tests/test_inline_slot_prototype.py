import tempfile
import unittest
from pathlib import Path
from docx import Document
from zipfile import ZipFile
from lxml import etree
from bid_agent.tag_inline_slots import tag_inline_slots
from bid_agent.document_ops import digest
from bid_agent.slot_bridge import fill_empty_slots

class InlineSlotPrototypeTests(unittest.TestCase):
    def test_empty_inline_range_fills_without_changing_fixed_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);d=Document();d.add_paragraph('供应商：    （单位公章）');d.save(r/'in.docx')
            slot=dict(paragraph=0,slot_id='supplier',start=4,end=8,before='供应商：    （单位公章）')
            tag_inline_slots(r/'in.docx',digest(r/'in.docx'),r/'tagged.docx',[slot])
            fill_empty_slots(r/'tagged.docx',digest(r/'tagged.docx'),r/'filled.docx',{'supplier':'测试企业'})
            with ZipFile(r/'filled.docx') as z:
                root=etree.fromstring(z.read('word/document.xml'));ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
                self.assertEqual(''.join(root.xpath('//w:t/text()',namespaces=ns)),'供应商：测试企业（单位公章）')
            with self.assertRaises(ValueError):tag_inline_slots(r/'in.docx',digest(r/'in.docx'),r/'bad.docx',[{**slot,'start':0}])
            self.assertFalse((r/'bad.docx').exists())
