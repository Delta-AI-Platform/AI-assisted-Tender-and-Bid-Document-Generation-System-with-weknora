import tempfile
import unittest
from pathlib import Path
from bid_agent.store import Store
from bid_agent.tools import ProjectTools

class DocumentTests(unittest.TestCase):
    def test_malformed_edit_rejects_before_output_with_repair_hint(self):
        source=self.call('create_docx',target='original.docx',blocks=[{'type':'paragraph','text':'原文'}])
        for edits in ([{'block':0,'old_text':'原文','new_text':'改文'}],
                      [{'block':-1,'before':'原文','after':'改文'}], []):
            with self.assertRaisesRegex(ValueError, 'block'):
                self.call('edit_docx',source='original.docx',source_sha256=source['sha256'],target='invalid.docx',edits=edits)
            self.assertFalse((self.root/'invalid.docx').exists())
        self.assertEqual(self.tools.artifact_info('original.docx')['sha256'],source['sha256'])

    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.store=Store(self.root/'state.sqlite')
        self.id=self.store.create('document test',{})
        self.tools=ProjectTools(self.root,self.store,self.id)
    def call(self,name,**args): return self.tools.tools[name].handler(**args)
    def test_bad_heading_schema_reports_repair_without_partial_file(self):
        for block in [{'block_type':'heading1','text':'标题'}, {'type':'heading1','text':'标题'}]:
            with self.assertRaisesRegex(ValueError, 'Heading example'):
                self.call('create_docx',target='bad.docx',blocks=[block])
            self.assertFalse((self.root/'bad.docx').exists())
        self.call('create_docx',target='fixed.docx',blocks=[{'type':'heading','level':1,'text':'标题'}])
        self.assertEqual(self.tools.read_document('fixed.docx')['blocks'][0]['text'],'标题')
    def test_revision_preserves_blank_and_rejects_stale_source(self):
        original=self.call('create_docx',target='a.docx',blocks=[{'type':'paragraph','text':'原文 ≥ 1000 kg'},{'type':'table','rows':[['要求','偏离'],['原文','']]}])
        self.call('edit_docx',source='a.docx',source_sha256=original['sha256'],target='b.docx',edits=[{'block':0,'before':'原文 ≥ 1000 kg','after':'新说明 ≥ 1000 kg'}])
        self.assertEqual(self.tools.read_document('a.docx')['blocks'][0]['text'],'原文 ≥ 1000 kg')
        self.assertEqual(self.tools.read_document('b.docx')['blocks'][1]['rows'][1][1],'')
        b=self.tools.artifact_info('b.docx')
        self.call('format_docx',source='b.docx',source_sha256=b['sha256'],target='formatted.docx',sections=[dict(section_index=0,width_mm=210,height_mm=297,top_mm=25,bottom_mm=25,left_mm=27,right_mm=27)])
        self.assertEqual(self.tools.read_document('formatted.docx')['blocks'],self.tools.read_document('b.docx')['blocks'])
        with self.assertRaises(ValueError): self.call('edit_docx',source='a.docx',source_sha256='wrong',target='bad.docx',edits=[])
        self.assertFalse((self.root/'bad.docx').exists())
        with self.assertRaises(FileExistsError): self.call('create_docx',target='a.docx',blocks=[])
    def test_render_assemble_and_image_are_not_visual_pass(self):
        self.call('create_docx',target='a.docx',blocks=[{'type':'paragraph','text':'中文 ≥ 1000 kg'}])
        render=self.call('render_pdf',source='a.docx',target='a.pdf')
        self.assertEqual(render['pages'],1);self.assertFalse(render['visually_checked'])
        self.assertTrue(self.tools.artifact_history('a.pdf')['receipts'][0]['matches_current_file'])
        with (self.root/'a.pdf').open('ab') as changed: changed.write(b'\n% changed')
        self.assertFalse(self.tools.artifact_history('a.pdf')['receipts'][0]['matches_current_file'])
        merged=self.call('assemble_pdf',sources=['a.pdf','a.pdf'],target='b.pdf')
        self.assertEqual(merged['pages'],2)
        self.assertEqual([x['page_start'] for x in merged['sources']],[1,2])
        image=self.call('page_image',source='b.pdf',page=2,target='page.png')
        self.assertFalse(image['visually_checked']);self.assertEqual(image['source_sha256'],merged['sha256'])
        self.assertEqual(self.tools.read_pdf_pages('b.pdf',2,2)['pages'][0]['text'].strip(),'中文 ≥ 1000 kg')
        with self.assertRaises(ValueError): self.tools.read_pdf_pages('b.pdf',3,3)
