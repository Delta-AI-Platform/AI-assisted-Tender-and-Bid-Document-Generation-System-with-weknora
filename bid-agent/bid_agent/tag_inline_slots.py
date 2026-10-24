"""Pre-freeze empty inline fields; exact paragraph and whitespace-only ranges."""
from pathlib import Path
from zipfile import ZipFile
from copy import deepcopy
from lxml import etree
import tempfile,re
NS={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
def q(n):return '{'+NS['w']+'}'+n

def tag_inline_slots(source,source_sha256,target,slots):
    try:
        from .document_ops import digest,publish
    except ImportError:
        from document_ops import digest,publish
    if digest(source)!=source_sha256:raise ValueError('Source version changed')
    if not isinstance(slots,list) or not slots:raise ValueError('Explicit slots required')
    with ZipFile(source) as z:infos=z.infolist();parts={i.filename:z.read(i) for i in infos}
    root=etree.fromstring(parts['word/document.xml']);paragraphs=root.findall('w:body/w:p',NS);seen=set();known=set(root.xpath('//w:tag/@w:val',namespaces=NS));before=root.xpath('//w:t/text()',namespaces=NS)
    for slot in slots:
        ident=slot['slot_id'];i=slot['paragraph'];start=slot['start'];end=slot['end']
        if not isinstance(ident,str) or not re.fullmatch('[A-Za-z0-9_-]{1,100}',ident) or 'BID_SLOT:'+ident in known:raise ValueError('Invalid slot identifier')
        known.add('BID_SLOT:'+ident)
        if type(i) is not int or not 0<=i<len(paragraphs) or i in seen:raise ValueError('Unique paragraph coordinate required')
        seen.add(i);p=paragraphs[i];runs=p.findall('w:r',NS)
        if len(runs)!=1 or any(e.tag not in (q('pPr'),q('r')) for e in p):raise ValueError('Only one simple text run supported')
        run=runs[0];texts=run.findall('w:t',NS)
        if len(texts)!=1 or any(e.tag not in (q('rPr'),q('t')) for e in run):raise ValueError('Complex run rejected')
        text=texts[0].text or ''
        if text!=slot['before'] or type(start) is not int or type(end) is not int or not 0<=start<=end<=len(text):raise ValueError('Source text or range changed')
        if text[start:end].strip():raise ValueError('Only empty/whitespace field ranges supported')
        def text_run(value):
            node=deepcopy(run);t=node.find(q('t'));t.text=value;t.set('{http://www.w3.org/XML/1998/namespace}space','preserve');return node
        sdt=etree.Element(q('sdt'));pr=etree.SubElement(sdt,q('sdtPr'));tag=etree.SubElement(pr,q('tag'));tag.set(q('val'),'BID_SLOT:'+ident);content=etree.SubElement(sdt,q('sdtContent'));content.append(text_run(text[start:end]))
        index=p.index(run);p.remove(run)
        for node in [text_run(text[:start]),sdt,text_run(text[end:])]:p.insert(index,node);index+=1
    if ''.join(before)!=''.join(root.xpath('//w:t/text()',namespaces=NS)):raise ValueError('Fixed text changed')
    parts['word/document.xml']=etree.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)
    with tempfile.TemporaryDirectory() as tmp:
        out=Path(tmp)/'tagged.docx'
        with ZipFile(out,'w') as z:
            for info in infos:z.writestr(info,parts[info.filename])
        if digest(source)!=source_sha256:raise ValueError('Source changed')
        return {**publish(out,target),'source_sha256':source_sha256,'slots':slots,'quality_status':'prototype_not_frozen'}
