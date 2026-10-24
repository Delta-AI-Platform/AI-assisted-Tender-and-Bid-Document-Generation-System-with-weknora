"""Explicit style-only changes; no implicit page or business defaults."""
import re
import tempfile
from pathlib import Path
from zipfile import ZipFile
from lxml import etree

NS={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
def w(name):return '{'+NS['w']+'}'+name


def set_docx_styles(source,source_sha256,target,updates):
    try:
        from .document_ops import digest, publish
    except ImportError:
        from document_ops import digest, publish
    if digest(source)!=source_sha256:raise ValueError('Source version changed')
    if not isinstance(updates,list) or not updates:raise ValueError('Explicit style updates required')
    with ZipFile(source) as z:
        infos=z.infolist();parts={i.filename:z.read(i) for i in infos}
    root=etree.fromstring(parts['word/styles.xml']);seen=set()
    for update in updates:
        if not isinstance(update,dict) or set(update)-{'style_id','color','font_name','size_pt','bold'}:
            raise ValueError('Only explicit style_id/color/font_name/size_pt/bold supported')
        ident=update.get('style_id')
        if not isinstance(ident,str) or ident in seen:raise ValueError('Unique style_id required')
        seen.add(ident)
        matches=root.xpath('//w:style[@w:styleId=$ident]',namespaces=NS,ident=ident)
        if len(matches)!=1:raise ValueError('Unknown or duplicate style')
        style=matches[0];rpr=style.find(w('rPr'))
        if rpr is None:rpr=etree.SubElement(style,w('rPr'))
        def replace(tag,attrs):
            for old in rpr.findall(w(tag)):rpr.remove(old)
            node=etree.SubElement(rpr,w(tag))
            for key,value in attrs.items():node.set(w(key),str(value))
        if 'color' in update:
            color=update['color']
            if not isinstance(color,str) or not re.fullmatch('[0-9A-Fa-f]{6}',color):raise ValueError('Six digit RGB required')
            replace('color',{'val':color.upper()})
        if 'font_name' in update:
            font=update['font_name']
            if not isinstance(font,str) or not font.strip():raise ValueError('Explicit font name required')
            replace('rFonts',{k:font for k in ['ascii','hAnsi','eastAsia','cs']})
        if 'size_pt' in update:
            size=update['size_pt']
            if type(size) not in (int,float) or not 6<=size<=72 or size*2!=int(size*2):raise ValueError('Font size must be 6-72 pt in half-point increments')
            replace('sz',{'val':int(size*2)});replace('szCs',{'val':int(size*2)})
        if 'bold' in update:
            if type(update['bold']) is not bool:raise ValueError('Explicit boolean bold required')
            replace('b',{'val':int(update['bold'])});replace('bCs',{'val':int(update['bold'])})
    parts['word/styles.xml']=etree.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)
    with tempfile.TemporaryDirectory() as tmp:
        out=Path(tmp)/'styled.docx'
        with ZipFile(out,'w') as z:
            for info in infos:z.writestr(info,parts[info.filename])
        with ZipFile(source) as a,ZipFile(out) as b:
            for name in a.namelist():
                if name!='word/styles.xml' and a.read(name)!=b.read(name):raise ValueError('Non-style part changed')
        if digest(source)!=source_sha256:raise ValueError('Source changed')
        return {**publish(out,target),'source_sha256':source_sha256,'updates':updates,'quality_status':'not_verified',
                'scope':'Only selected style font properties; direct formatting can override styles; installed font availability and rendered layout need review'}
