"""Bounded original-file indexing; never edits the uploaded source bytes.

Text/table extraction and local OCR are retrieval aids, not visual approval.
Embedded Word images remain in the downloadable original for on-demand review.
"""
import io
import os
import subprocess
import tempfile
import zipfile
import uuid
from pathlib import Path
from lxml import etree
from docreader.models.document import Document
from docreader.parser.base_parser import BaseParser

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

def docx_text(content):
    with zipfile.ZipFile(content if isinstance(content, (str, Path)) else io.BytesIO(content)) as z:
        info = z.getinfo('word/document.xml')
        if info.file_size > 100_000_000:
            raise ValueError('Word document XML exceeds 100MB indexing limit')
        node = etree.fromstring(z.read(info), etree.XMLParser(resolve_entities=False, no_network=True))
        body = node.find(W+'body')
        if body is None:
            raise ValueError('Missing Word body')
        pieces=[]
        def text(el):
            if el.tag==W+'t':return el.text or ''
            if el.tag==W+'tab':return '\t'
            if el.tag in (W+'br',W+'cr'):return '\n'
            value=''.join(text(child) for child in el)
            return value+'\n' if el.tag==W+'p' else value
        for block in body:
            if block.tag==W+'p':pieces.append(text(block).strip())
            elif block.tag==W+'tbl':
                rows=[]
                for row in block.findall(W+'tr'):
                    rows.append([text(cell).strip().replace('|','\\|').replace('\n','<br>') for cell in row.findall(W+'tc')])
                if rows:
                    width=max(map(len,rows)); rows=[r+['']*(width-len(r)) for r in rows]
                    pieces.extend(['| '+' | '.join(rows[0])+' |','| '+' | '.join(['---']*width)+' |'])
                    pieces.extend('| '+' | '.join(r)+' |' for r in rows[1:])
            else:pieces.append(text(block).strip())
        for name in sorted(z.namelist()):
            if name.startswith(('word/header','word/footer')) and name.endswith('.xml') or name in ('word/footnotes.xml','word/endnotes.xml'):
                if z.getinfo(name).file_size>10_000_000:raise ValueError('Word ancillary XML exceeds 10MB limit')
                extra=etree.fromstring(z.read(name),etree.XMLParser(resolve_entities=False,no_network=True))
                value=text(extra).strip()
                if value:pieces.append('['+name+']\n'+value)
        return '\n\n'.join(pieces).strip()

def run(args,timeout=120):
    env=dict(os.environ,OMP_THREAD_LIMIT='1')
    p=subprocess.run(args,capture_output=True,timeout=timeout,env=env)
    if p.returncode:raise ValueError(p.stderr.decode(errors='replace')[-500:])
    return p.stdout.decode('utf-8',errors='replace').strip()

def legacy_word_text(src, folder):
    """Fallback in the same offline image used by native document skills."""
    name='deta-docread-'+uuid.uuid4().hex
    try:
        run(['docker','run','--rm','--name',name,'--network','none',
             '--memory','768m','--cpus','1','--pids-limit','128',
             '--user',f'{os.getuid()}:{os.getgid()}',
             '-v',f'{folder}:/work:rw','deta-bid-sandbox:20261023',
             'libreoffice','-env:UserInstallation=file:///tmp/lo-profile',
             '--headless','--convert-to','txt:Text','--outdir','/work',
             '/work/'+src.name],180)
        target=Path(folder)/(src.stem+'.txt')
        if not target.exists():raise ValueError('LibreOffice could not open original Word file')
        if target.stat().st_size>100_000_000:raise ValueError('Extracted Word text exceeds 100MB limit')
        return target.read_text(encoding='utf-8',errors='replace').strip()
    finally:
        subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)

def docx_image_text(content, folder):
    pieces=[]
    with zipfile.ZipFile(content if isinstance(content, (str, Path)) else io.BytesIO(content)) as z:
        images=[n for n in z.namelist() if n.startswith('word/media/') and Path(n).suffix.lower() in ('.png','.jpg','.jpeg','.tif','.tiff','.bmp')]
        if len(images)>100:raise ValueError('Image-only Word exceeds 100-image local OCR limit; review required')
        for i,name in enumerate(images):
            if z.getinfo(name).file_size>50_000_000:raise ValueError('Embedded image exceeds 50MB OCR limit')
            path=Path(folder)/('image-'+str(i)+Path(name).suffix);path.write_bytes(z.read(name))
            text=run(['tesseract',str(path),'stdout','-l','chi_sim+eng','--psm','3'],90)
            if text:pieces.append('['+name+'; 本地OCR，未经视觉核验]\n'+text)
    return '\n\n'.join(pieces)

class DetaOriginalParser(BaseParser):
    def parse_into_text(self,content):
        ext=self.file_type.lower().lstrip('.')
        with tempfile.TemporaryDirectory(prefix='deta-original-') as folder:
            src=Path(folder)/('original.'+ext);src.write_bytes(content)
            if zipfile.is_zipfile(src) and ext in ('docx','doc'):
                try:
                    text=docx_text(src);method='word_xml_text_tables_embedded_images_not_ocr'
                    if not text:
                        text=docx_image_text(src,folder);method='word_embedded_images_local_ocr_not_visual_approval'
                except KeyError:
                    text=legacy_word_text(src,folder);method='offline_libreoffice_text_layout_not_verified'
            elif ext=='doc':
                try:
                    text=run(['antiword',str(src)],180);method='legacy_word_text_embedded_images_not_ocr'
                except ValueError:
                    text=legacy_word_text(src,folder);method='offline_libreoffice_text_layout_not_verified'
            elif ext=='pdf':
                text=run(['pdftotext','-layout',str(src),'-'],180);method='pdf_text_layer_scanned_pages_need_visual_review'
                if not text:
                    raise ValueError('Scanned PDF has no text layer; requires OCR. Original bytes preserved.')
            elif ext in ('jpg','jpeg','png','tiff','bmp','webp'):
                text=run(['tesseract',str(src),'stdout','-l','chi_sim+eng','--psm','3'],90);method='tesseract_local_ocr_not_visual_approval'
                if not text:text='[本地OCR未识别出文字；原图已保留，需视觉核验。]'
            else:raise ValueError('Unsupported original format: '+ext)
        if not text:raise ValueError('Original contains no extractable text; review required')
        return Document(content=text,metadata={'indexing_method':method,'original_unchanged':True,'visual_review_complete':False})
