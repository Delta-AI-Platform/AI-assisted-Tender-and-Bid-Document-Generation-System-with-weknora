import tempfile
import unittest
from pathlib import Path
from docx import Document
import pymupdf
from bid_agent.assembly import append_pdf_evidence, digest

class EvidenceSourceBindingTest(unittest.TestCase):
    def test_same_page_number_wrong_pdf_rejected_before_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            master=root/'master.docx';Document().save(master)
            for name,text in [('tender','AGREEMENT'),('gold','CERTIFICATE')]:
                with pymupdf.open() as pdf:
                    pdf.new_page().insert_text((40,40),text)
                    pdf.save(root/f'{name}.pdf')
            target=root/'candidate.docx'
            with self.assertRaisesRegex(ValueError,'Evidence PDF version/source mismatch'):
                append_pdf_evidence(master,digest(master),root/'gold.pdf',[1],target,
                                    pdf_sha256=digest(root/'tender.pdf'))
            self.assertFalse(target.exists())
            result=append_pdf_evidence(master,digest(master),root/'tender.pdf',[1],target,
                                       pdf_sha256=digest(root/'tender.pdf'))
            self.assertEqual(result['evidence_pages'][0]['pdf_sha256'],digest(root/'tender.pdf'))
