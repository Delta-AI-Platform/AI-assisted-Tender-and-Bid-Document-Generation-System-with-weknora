"""Pre-freeze partition into explicit paragraph-ended sections."""
from pathlib import Path
from zipfile import ZipFile
from lxml import etree
from copy import deepcopy
import tempfile
NS={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
def q(n):return '{'+NS['w']+'}'+n

def partition_sections(source, source_sha256, target, sections):
    try:
        from .document_ops import digest,publish
    except ImportError:
        from document_ops import digest,publish
    if digest(source)!=source_sha256:raise ValueError('Source version changed')
    with ZipFile(source) as z:infos=z.infolist();parts={i.filename:z.read(i) for i in infos}
    root=etree.fromstring(parts['word/document.xml']);body=root.find('w:body',NS)
    old=body.find('w:sectPr',NS)
    if old is None or len(root.xpath('//w:sectPr',namespaces=NS))!=1:raise ValueError('Only single-section unfrozen sources supported')
    if root.xpath('//w:drawing|//w:pict|//w:sdt',namespaces=NS):raise ValueError('Tagged slots or image evidence require separate handling')
    blocks=[e for e in body if e is not old];last=-1
    if not isinstance(sections,list) or not sections:raise ValueError('Explicit partition required')
    specs=[]
    for spec in sections:
        keys={'end_body_index','width_mm','height_mm','top_mm','bottom_mm','left_mm','right_mm'}
        if not isinstance(spec,dict) or set(spec)!=keys:raise ValueError('All partition geometry required')
        end=spec['end_body_index']
        if type(end) is not int or not last<end<len(blocks) or blocks[end].tag not in (q('p'),q('tbl')):raise ValueError('Partition ends must be increasing paragraph or table indices')
        for key in keys-{'end_body_index'}:
            if type(spec[key]) not in (int,float) or not 0<=spec[key]<=1000:raise ValueError('Invalid geometry')
        if spec['width_mm']<=spec['left_mm']+spec['right_mm'] or spec['height_mm']<=spec['top_mm']+spec['bottom_mm']:raise ValueError('No content area')
        sec=deepcopy(old)
        for name in ['type']:
            for e in sec.findall(q(name)):sec.remove(e)
        typ=etree.Element(q('type'));typ.set(q('val'),'nextPage');sec.insert(0,typ)
        size=sec.find(q('pgSz'));mar=sec.find(q('pgMar'))
        if size is None or mar is None:raise ValueError('Missing source geometry')
        for k,v in [('w','width_mm'),('h','height_mm')]:size.set(q(k),str(round(spec[v]*1440/25.4)))
        size.set(q('orient'),'landscape' if spec['width_mm']>spec['height_mm'] else 'portrait')
        for side in ('top','bottom','left','right'):mar.set(q(side),str(round(spec[side+'_mm']*1440/25.4)))
        specs.append((end,sec));last=end
    if last!=len(blocks)-1:raise ValueError('Partition must cover complete body')
    original_text=root.xpath('//w:t/text()',namespaces=NS)
    for end,sec in specs[:-1]:
        p=blocks[end]
        if p.tag==q('tbl'):
            boundary=etree.Element(q('p'));p.addnext(boundary);p=boundary
        pr=p.find(q('pPr'))
        if pr is None:pr=etree.Element(q('pPr'));p.insert(0,pr)
        pr.append(sec)
    body.replace(old,specs[-1][1])
    if original_text!=root.xpath('//w:t/text()',namespaces=NS):raise ValueError('Text changed')
    parts['word/document.xml']=etree.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)
    with tempfile.TemporaryDirectory() as tmp:
        out=Path(tmp)/'sections.docx'
        with ZipFile(out,'w') as z:
            for info in infos:z.writestr(info,parts[info.filename])
        if digest(source)!=source_sha256:raise ValueError('Source changed')
        return {**publish(out,target),'source_sha256':source_sha256,'partitions':sections,'quality_status':'not_verified','scope':'Explicit pre-freeze section geometry; no text, table widths, fonts or other ZIP part changes; render required'}
