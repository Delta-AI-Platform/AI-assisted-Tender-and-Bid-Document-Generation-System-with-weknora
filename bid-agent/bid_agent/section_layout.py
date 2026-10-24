"""Explicit section geometry, with untouched package parts and body content."""
from pathlib import Path
from zipfile import ZipFile
from lxml import etree
import tempfile

NS={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
def q(name):return '{'+NS['w']+'}'+name

def format_docx(source,source_sha256,target,sections):
    try:
        from .document_ops import digest,publish
    except ImportError:
        from document_ops import digest,publish
    if digest(source)!=source_sha256:raise ValueError('Source version changed')
    if not isinstance(sections,list) or not sections:raise ValueError('Explicit section geometry required')
    with ZipFile(source) as z:
        infos=z.infolist();parts={i.filename:z.read(i) for i in infos}
    root=etree.fromstring(parts['word/document.xml']);nodes=root.xpath('//w:sectPr',namespaces=NS);seen=set()
    for update in sections:
        required={'section_index','width_mm','height_mm','top_mm','bottom_mm','left_mm','right_mm'}
        if not isinstance(update,dict) or set(update)!=required:raise ValueError('Specify section_index and all six geometry values')
        i=update['section_index']
        if type(i) is not int or not 0<=i<len(nodes) or i in seen:raise ValueError('Invalid or duplicate section index')
        seen.add(i)
        for key in required-{'section_index'}:
            value=update[key]
            if type(value) not in (int,float) or not 0<=value<=1000:raise ValueError('Invalid millimeter geometry')
        if update['width_mm']<=update['left_mm']+update['right_mm'] or update['height_mm']<=update['top_mm']+update['bottom_mm']:raise ValueError('Margins leave no content area')
        node=nodes[i]
        size=node.find(q('pgSz'));margin=node.find(q('pgMar'))
        if size is None or margin is None:raise ValueError('Missing existing section geometry')
        size.set(q('w'),str(round(update['width_mm']*1440/25.4)));size.set(q('h'),str(round(update['height_mm']*1440/25.4)))
        size.set(q('orient'),'landscape' if update['width_mm']>update['height_mm'] else 'portrait')
        for side in ('top','bottom','left','right'):margin.set(q(side),str(round(update[side+'_mm']*1440/25.4)))
    parts['word/document.xml']=etree.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)
    with tempfile.TemporaryDirectory() as tmp:
        out=Path(tmp)/'layout.docx'
        with ZipFile(out,'w') as z:
            for info in infos:z.writestr(info,parts[info.filename])
        if digest(source)!=source_sha256:raise ValueError('Source changed during layout')
        return {**publish(out,target),'source_sha256':source_sha256,'sections':sections,'quality_status':'not_verified','scope':'Only explicitly selected existing section geometry; no font, table, text or unselected section changes. Render required.'}
