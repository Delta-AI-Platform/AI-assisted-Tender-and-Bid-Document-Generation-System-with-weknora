"""Precisely bound paragraph page breaks; no text or table edits."""
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile
from docx import Document
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from lxml import etree


def set_paragraph_pagination(source,source_sha256,target,edits):
    try:
        from .document_ops import digest,publish
    except ImportError:
        from document_ops import digest,publish
    if digest(source)!=source_sha256:raise ValueError('Source version changed')
    if not isinstance(edits,list) or not 1<=len(edits)<=200:raise ValueError('Provide 1–200 bound paragraph edits')
    doc=Document(source);blocks=list(doc.element.body);seen=set()
    for edit in edits:
        if not isinstance(edit,dict) or set(edit)!={'block','before','page_break_before'}:raise ValueError('Expected block,before,page_break_before')
        index=edit['block']
        if type(index) is not int or not 0<=index<len(blocks) or index in seen:raise ValueError('Invalid or duplicate body index')
        seen.add(index);p=blocks[index]
        if p.tag!=qn('w:p'):raise ValueError('Selected block must be a paragraph')
        if ''.join(n.text or '' for n in p.iter(qn('w:t')))!=edit['before']:raise ValueError('Paragraph text binding mismatch')
        if type(edit['page_break_before']) is not bool:raise ValueError('Explicit boolean required')
        if p.xpath('.//w:sectPr|.//w:drawing|.//w:pict|.//w:sdt|.//w:fldChar|.//w:instrText'):raise ValueError('Protected paragraph content')
    for edit in edits:
        p=blocks[edit['block']];pr=p.get_or_add_pPr();node=pr.find(qn('w:pageBreakBefore'))
        if node is None:node=OxmlElement('w:pageBreakBefore');pr.insert_element_before(node,'w:framePr','w:widowControl','w:numPr','w:suppressLineNumbers','w:pBdr','w:shd','w:tabs','w:spacing','w:ind','w:jc','w:rPr','w:sectPr')
        node.set(qn('w:val'),'1' if edit['page_break_before'] else '0')
    xml=etree.tostring(doc.element,encoding='UTF-8',xml_declaration=True,standalone=True)
    with TemporaryDirectory() as tmp:
        f=Path(tmp)/'paginated.docx'
        with ZipFile(source) as a,ZipFile(f,'w') as b:
            for info in a.infolist():b.writestr(info,xml if info.filename=='word/document.xml' else a.read(info.filename))
        check=Document(f)
        for edit in edits:
            if list(check.element.body)[edit['block']].pPr.pageBreakBefore.val!=edit['page_break_before']:raise ValueError('Readback mismatch')
        if digest(source)!=source_sha256:raise ValueError('Source changed during pagination')
        return {**publish(f,target),'source_sha256':source_sha256,'edited_paragraphs':len(edits),'quality_status':'not_verified','limitations':['Only bound paragraph page-break property changed','Render and inspect actual page boundaries; this does not keep arbitrary following content on one page']}
