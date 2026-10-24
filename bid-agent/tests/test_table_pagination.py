import hashlib
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile
from docx import Document
from lxml import etree
from bid_agent.table_pagination import set_table_pagination, NS, w


class PaginationTests(unittest.TestCase):
    def test_only_selected_row_properties_change_and_bad_binding_rejects(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);d=Document()
            for label in ['报价','其他表']:
                t=d.add_table(rows=3,cols=2)
                for i,row in enumerate(t.rows):
                    row.cells[0].text=label+str(i);row.cells[1].text='001.2300'
            d.save(p/'source.docx');digest=hashlib.sha256((p/'source.docx').read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError,'version'):
                set_table_pagination(p/'source.docx','bad',p/'bad.docx',0,1,True)
            self.assertFalse((p/'bad.docx').exists())
            set_table_pagination(p/'source.docx',digest,p/'out.docx',0,1,True)
            with ZipFile(p/'source.docx') as a,ZipFile(p/'out.docx') as b:
                for name in a.namelist():
                    if name!='word/document.xml':self.assertEqual(a.read(name),b.read(name))
                before=etree.fromstring(a.read('word/document.xml'));after=etree.fromstring(b.read('word/document.xml'))
                old=before.findall('w:body/w:tbl',NS);new=after.findall('w:body/w:tbl',NS)
                self.assertEqual(etree.tostring(old[1]),etree.tostring(new[1]))
                for i,(r1,r2) in enumerate(zip(old[0].findall(w('tr')),new[0].findall(w('tr')))):
                    self.assertEqual([etree.tostring(c) for c in r1.findall(w('tc'))],[etree.tostring(c) for c in r2.findall(w('tc'))])
                    self.assertEqual(r2.find('w:trPr/w:tblHeader',NS).get(w('val')),'1' if i==0 else '0')
                    self.assertEqual(r2.find('w:trPr/w:cantSplit',NS).get(w('val')),'1')
