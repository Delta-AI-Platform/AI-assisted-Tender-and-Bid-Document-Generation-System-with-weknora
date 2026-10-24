"""Repeat an explicitly bound, text/table-only template without deleting content."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile
from docx import Document
from docx.oxml.ns import qn
from lxml import etree


def repeat_docx_blocks(source, source_sha256, target, start_block, end_block, copies, expected_texts):
    try:
        from .document_ops import digest, publish
    except ImportError:
        from document_ops import digest, publish
    if digest(source) != source_sha256: raise ValueError('Source version changed')
    if any(type(n) is not int for n in (start_block,end_block,copies)) or not 2<=copies<=30:
        raise ValueError('Integer block indices and 2–30 total copies required')
    doc=Document(source);body=doc.element.body;blocks=list(body)
    if not 0<=start_block<=end_block<len(blocks)-1: raise ValueError('Invalid body range')
    chosen=blocks[start_block:end_block+1]
    text=lambda e: ''.join(n.text or '' for n in e.iter(qn('w:t')))
    if not isinstance(expected_texts,list) or [text(e) for e in chosen]!=expected_texts:
        raise ValueError('Exact per-block text binding required')
    forbidden={'sectPr','drawing','pict','sdt','fldChar','instrText','hyperlink','bookmarkStart','bookmarkEnd','commentRangeStart','commentRangeEnd','commentReference','footnoteReference','endnoteReference','numPr','ins','del','moveFrom','moveTo','altChunk'}
    for block in chosen:
        if block.tag not in {qn('w:p'),qn('w:tbl')}: raise ValueError('Only paragraphs and tables can be repeated')
        if any(etree.QName(n).localname in forbidden for n in block.iter()):
            raise ValueError('Template contains protected content, identifiers or section/numbering semantics')
    original=[etree.tostring(e) for e in blocks]
    index=end_block+1
    for _ in range(copies-1):
        for block in chosen:
            body.insert(index,deepcopy(block));index+=1
    expected=original[:start_block]+original[start_block:end_block+1]*copies+original[end_block+1:]
    if [etree.tostring(e) for e in body]!=expected: raise ValueError('Unexpected structural change')
    xml=etree.tostring(doc.element,encoding='UTF-8',xml_declaration=True,standalone=True)
    with TemporaryDirectory() as tmp:
        out=Path(tmp)/'repeated.docx'
        with ZipFile(source) as src,ZipFile(out,'w') as dest:
            for info in src.infolist():dest.writestr(info,xml if info.filename=='word/document.xml' else src.read(info.filename))
        saved=Document(out)
        if [etree.tostring(e) for e in saved.element.body]!=expected:raise ValueError('Readback structure mismatch')
        if digest(source)!=source_sha256:raise ValueError('Source changed during repetition')
        return {**publish(out,target),'source_sha256':source_sha256,'start_block':start_block,'end_block':end_block,'copies':copies,'original_blocks_preserved':True,'quality_status':'not_verified','limitations':['Only duplicates explicit template blocks; does not fill facts or approve evidence','Render and inspect all repeated forms before acceptance']}
