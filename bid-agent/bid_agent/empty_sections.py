"""Remove only an empty section whose predecessor already closes a section."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile
from docx import Document
from docx.oxml.ns import qn
from lxml import etree

def remove_redundant_empty_section(source,source_sha256,target,block):
    try:
        from .document_ops import digest,publish
    except ImportError:
        from document_ops import digest,publish
    if digest(source)!=source_sha256:raise ValueError('Source version changed')
    d=Document(source);body=d.element.body;blocks=list(body)
    if type(block) is not int or not 0<block<len(blocks)-1:raise ValueError('Invalid body index')
    p=blocks[block]
    if p.tag!=qn('w:p') or not p.xpath('./w:pPr/w:sectPr'):raise ValueError('Expected section-bearing empty paragraph')
    allowed={qn(x) for x in ['w:p','w:pPr','w:r','w:rPr','w:t']}
    section=p.xpath('./w:pPr/w:sectPr')[0]
    for node in p.iter():
        if node is section or section in node.iterancestors():continue
        if node.tag not in allowed or (node.text or '').strip():raise ValueError('Paragraph has text or protected content')
    if not blocks[block-1].xpath('./w:pPr/w:sectPr'):raise ValueError('Previous block must already close its section')
    following=None
    for x in blocks[block+1:]:
        ss=[x] if x.tag==qn('w:sectPr') else x.xpath('./w:pPr/w:sectPr')
        if ss:following=ss[0];break
    if following is None:raise ValueError('Following section missing')
    def canonical(s):
        s=deepcopy(s)
        for key in list(s.attrib):
            if etree.QName(key).localname.startswith('rsid'):del s.attrib[key]
        return etree.tostring(s,method='c14n')
    if canonical(section)!=canonical(following):raise ValueError('Empty section differs from following section; refuse geometry/header changes')
    prior=[etree.tostring(x) for i,x in enumerate(blocks) if i!=block]
    body.remove(p)
    xml=etree.tostring(d.element,encoding='UTF-8',xml_declaration=True,standalone=True)
    with TemporaryDirectory() as tmp:
        out=Path(tmp)/'trimmed.docx'
        with ZipFile(source) as a,ZipFile(out,'w') as b:
            for info in a.infolist():b.writestr(info,xml if info.filename=='word/document.xml' else a.read(info.filename))
        if [etree.tostring(x) for x in Document(out).element.body]!=prior:raise ValueError('Other body blocks changed')
        if digest(source)!=source_sha256:raise ValueError('Source changed')
        return {**publish(out,target),'source_sha256':source_sha256,'removed_block':block,'quality_status':'not_verified','limitations':['Only empty section with identical following section removed; render and inspect pagination']}
