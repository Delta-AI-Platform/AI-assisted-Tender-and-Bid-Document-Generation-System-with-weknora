"""Independent source-quote audit; does not judge semantic coverage."""
import argparse
import hashlib
import json
import re
from pathlib import Path


def audit(requirements, pages, source_sha256):
    source={p['page']:p['text'] for p in pages}
    results=[];seen=set()
    for item in requirements:
        errors=[]
        ident=item.get('requirement_id')
        if not isinstance(ident,str) or not ident.strip(): errors.append('missing stable ID')
        elif ident in seen: errors.append('duplicate stable ID')
        else: seen.add(ident)
        quote=item.get('quote');refs=item.get('source_physical_pages')
        if not isinstance(quote,str) or not quote.strip():errors.append('missing original quote')
        if not isinstance(refs,list) or not refs or any(type(p) is not int or p not in source for p in refs):
            errors.append('invalid physical source pages')
        elif refs!=sorted(set(refs)) or refs!=list(range(refs[0],refs[-1]+1)):
            errors.append('source pages must be unique, ordered and consecutive')
        if not errors:
            original='\n'.join(source[p] for p in refs)
            compact=lambda text:re.sub(r'\s+','',text)
            if compact(quote) not in compact(original): errors.append('quote differs from cited source text')
        results.append({'requirement_id':ident,'passed':not errors,'errors':errors,
                        'source_physical_pages':refs})
    return {'source_sha256':source_sha256,'checked':len(results),
            'passed':bool(results) and all(r['passed'] for r in results),'results':results,
            'scope':'Quote location and whitespace-only text comparison; not semantic completeness, table fidelity, OCR correctness or legal approval'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('requirements',type=Path)
    parser.add_argument('pdf',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    from pypdf import PdfReader
    data=json.loads(args.requirements.read_text())
    items=data if isinstance(data,list) else data['requirements']
    raw=args.pdf.read_bytes();reader=PdfReader(args.pdf)
    result=audit(items,[{'page':i,'text':p.extract_text() or ''} for i,p in enumerate(reader.pages,1)],hashlib.sha256(raw).hexdigest())
    result['artifact_sha256']=hashlib.sha256(args.requirements.read_bytes()).hexdigest()
    with args.output.open('x') as out:json.dump(result,out,ensure_ascii=False,indent=2)
    print(json.dumps({'passed':result['passed'],'checked':result['checked']}))
