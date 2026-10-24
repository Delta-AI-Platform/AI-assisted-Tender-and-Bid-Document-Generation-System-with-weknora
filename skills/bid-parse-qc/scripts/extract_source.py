"""Local source extraction, never a semantic or visual QC pass."""
from __future__ import annotations
import argparse, hashlib, importlib, json, sys
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

NS={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
def visible_text(node):
    chunks=[]
    for el in node.iter():
        if el.tag==f"{{{NS['w']}}}t":chunks.append(el.text or '')
        elif el.tag in {f"{{{NS['w']}}}br",f"{{{NS['w']}}}cr"}:chunks.append('\n')
        elif el.tag==f"{{{NS['w']}}}tab":chunks.append('\t')
    return ''.join(chunks)

def extract(path:Path):
    blocks=[];warnings=[];suffix=path.suffix.lower()
    if suffix=='.docx':
        with ZipFile(path) as z:
            root=ET.fromstring(z.read('word/document.xml'));body=root.find('w:body',NS)
            if body is None:raise ValueError('DOCX main body missing')
            def walk(parent,location):
                for i,node in enumerate(parent,1):
                    tag=node.tag.split('}')[-1];loc=f'{location}/{tag}[{i}]'
                    if tag=='p':
                        t=visible_text(node)
                        if t.strip():blocks.append({'location':loc,'kind':'paragraph','text':t})
                    elif tag=='tbl':
                        for ri,row in enumerate(node.findall('w:tr',NS),1):
                            cells=[{'column':ci,'text':visible_text(c),'grid_span':(c.find('w:tcPr/w:gridSpan',NS).get(f"{{{NS['w']}}}val") if c.find('w:tcPr/w:gridSpan',NS) is not None else '1'),'vertical_merge':(c.find('w:tcPr/w:vMerge',NS).get(f"{{{NS['w']}}}val",'continue') if c.find('w:tcPr/w:vMerge',NS) is not None else None)} for ci,c in enumerate(row.findall('w:tc',NS),1)]
                            blocks.append({'location':f'{loc}/row[{ri}]','kind':'table_row','text':' | '.join(c['text'] for c in cells),'cells':cells})
                    elif tag in {'sdt','sdtContent','customXml'}:walk(node,loc)
            walk(body,'word/document.xml/body')
            warnings.append('DOCX positions are XML/paragraph/table positions, not rendered page numbers. Images, text boxes, headers/footers, fields and linked objects require original-file inspection.')
        engine='python-stdlib-ooxml';page_count=None
    elif suffix=='.pdf':
        try:
            fitz=importlib.import_module('pymupdf')
        except ImportError:
            try:fitz=importlib.import_module('fitz')
            except ImportError:fitz=None
        if fitz is not None:
            with fitz.open(path) as pdf:
                if pdf.needs_pass:raise ValueError('PDF needs an authorized decrypted copy')
                page_count=len(pdf)
                for i,p in enumerate(pdf,1):blocks.append({'location':f'physical_page:{i}','kind':'page','page':i,'text':p.get_text(),'needs_ocr':not bool(p.get_text().strip())})
            engine='pymupdf'
        else:
            try:reader_class=importlib.import_module('pypdf').PdfReader
            except ImportError:raise ValueError('No PDF extractor available. Use existing native reading or provide readable text; no software was installed.')
            reader=reader_class(path)
            if reader.is_encrypted:raise ValueError('PDF is encrypted; provide an authorized decrypted copy')
            page_count=len(reader.pages)
            for i,p in enumerate(reader.pages,1):
                t=p.extract_text() or '';blocks.append({'location':f'physical_page:{i}','kind':'page','page':i,'text':t,'needs_ocr':not bool(t.strip())})
            engine='pypdf'
        warnings.append('PDF text order, table geometry, merged cells, symbols and image-only clauses require original-page review. No OCR or semantic completeness judgment was performed.')
    else:raise ValueError('Supported input: .docx or .pdf. Legacy .doc must be converted to a separate readable copy; renaming is not conversion.')
    source_hash=hashlib.sha256(path.read_bytes()).hexdigest()
    for i,b in enumerate(blocks,1):
        b['source_order']=i;b['source_id']=source_hash[:12]+':'+b['location'];b['text_sha256']=hashlib.sha256(b['text'].encode('utf-8')).hexdigest()
    return {'source_path':str(path),'source_sha256':source_hash,'engine':engine,'page_count':page_count,'blocks':blocks,'unread_pages':[b['page'] for b in blocks if b.get('needs_ocr')],'warnings':warnings,'semantic_qc':'not_performed','visual_qc':'not_performed'}

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--input',type=Path,required=True);ap.add_argument('--out-dir',type=Path,required=True);a=ap.parse_args()
    src=a.input.resolve(strict=True);out=a.out_dir.resolve();targets=[out/'source.txt',out/'source.json']
    if src in targets or any(p.exists() for p in targets):ap.error('Refusing to overwrite source or existing extraction; choose a new output directory.')
    try:data=extract(src)
    except (ValueError,OSError,KeyError,ET.ParseError) as e:ap.exit(2,str(e)+'\n')
    out.mkdir(parents=True,exist_ok=True)
    text='\n\n'.join((f"=====PAGE {b['page']}=====\n" if b.get('page') else f"====={b['location']}=====\n")+b['text']+('\n[OCR REQUIRED: NOT READ]' if b.get('needs_ocr') else '') for b in data['blocks'])
    targets[0].write_text(text,encoding='utf-8');targets[1].write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':'extracted_not_qc_passed','engine':data['engine'],'blocks':len(data['blocks']),'unread_pages':data['unread_pages'],'outputs':[str(p) for p in targets]},ensure_ascii=False))
if __name__=='__main__':main()
