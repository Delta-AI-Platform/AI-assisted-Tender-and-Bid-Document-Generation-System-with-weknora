"""Replace layouts only when every physical cell's text and merge signature match."""
from pathlib import Path
from zipfile import ZipFile
from lxml import etree
from copy import deepcopy
import tempfile
NS={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
def signature(t):
    return [(len(row.findall('w:tc',NS)),[(tc.xpath('./w:tcPr/w:gridSpan/@w:val',namespaces=NS),tc.xpath('./w:tcPr/w:vMerge/@w:val',namespaces=NS),bool(tc.find('w:tcPr/w:vMerge',NS) is not None),tc.xpath('.//w:t/text()',namespaces=NS)) for tc in row.findall('w:tc',NS)]) for row in t.findall('w:tr',NS)]
def replace_table_layouts(source,source_sha256,target,replacements):
    try:
        from .document_ops import digest,publish
    except ImportError:
        from document_ops import digest,publish
    if digest(source)!=source_sha256:raise ValueError('Source version changed')
    if not replacements:raise ValueError('Explicit replacements required')
    with ZipFile(source) as z:infos=z.infolist();parts={i.filename:z.read(i) for i in infos}
    root=etree.fromstring(parts['word/document.xml']);tables=root.findall('w:body/w:tbl',NS);seen=set();receipts=[]
    for item in replacements:
        i=item['table_index'];fragment=Path(item['fragment'])
        if type(i) is not int or not 0<=i<len(tables) or i in seen:raise ValueError('Invalid table index')
        seen.add(i)
        if tables[i].xpath('.//w:drawing|.//w:pict|.//w:sdt|.//w:hyperlink|.//w:fldChar',namespaces=NS):raise ValueError('Protected or complex source table rejected')
        if digest(fragment)!=item['fragment_sha256']:raise ValueError('Fragment version changed')
        with ZipFile(fragment) as z:f=etree.fromstring(z.read('word/document.xml'))
        candidates=f.findall('w:body/w:tbl',NS)
        if len(candidates)!=1:raise ValueError('Single table fragment required')
        table=candidates[0]
        if table.xpath('.//w:drawing|.//w:pict|.//w:sdt|.//w:hyperlink|.//w:fldChar',namespaces=NS):raise ValueError('Complex fragment unsupported')
        if signature(tables[i])!=signature(table):raise ValueError('Fixed text or merge structure differs')
        tables[i].getparent().replace(tables[i],deepcopy(table));receipts.append(item)
    parts['word/document.xml']=etree.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)
    with tempfile.TemporaryDirectory() as tmp:
        out=Path(tmp)/'tables.docx'
        with ZipFile(out,'w') as z:
            for info in infos:z.writestr(info,parts[info.filename])
        if digest(source)!=source_sha256:raise ValueError('Source changed')
        if any(digest(x['fragment'])!=x['fragment_sha256'] for x in replacements):raise ValueError('Fragment changed')
        return {**publish(out,target),'source_sha256':source_sha256,'replacements':receipts,'quality_status':'not_verified','scope':'Matching table text/merges required; only table layout XML replaced, rendering required'}
