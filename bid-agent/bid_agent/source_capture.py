"""Capture exact source spans selected by the Agent; never paraphrase quotes."""
import json
import tempfile
from pathlib import Path


def locate_anchor(text, anchor):
    """Match only after removing whitespace, mapping back to original offsets."""
    positions=[i for i,c in enumerate(text) if not c.isspace()]
    compact=''.join(text[i] for i in positions)
    needle=''.join(c for c in anchor if not c.isspace())
    if not needle:raise ValueError('Empty anchor')
    first=compact.find(needle)
    if first<0 or compact.find(needle,first+1)>=0:
        raise ValueError('Anchor missing or ambiguous after whitespace-only normalization')
    return positions[first],positions[first+len(needle)-1]+1


def capture_requirements(source, source_sha256, target, selections):
    from pypdf import PdfReader
    try:
        from .document_ops import digest, publish
    except ImportError:
        from document_ops import digest, publish
    if digest(source)!=source_sha256: raise ValueError('Source version changed')
    if not selections or len(selections)>100: raise ValueError('Select 1–100 source spans')
    reader=PdfReader(source);cache={};rows=[];ids=set()
    for selected in selections:
        ident=selected['requirement_id']
        if not isinstance(ident,str) or not ident.strip() or ident in ids:
            raise ValueError('Stable unique requirement ID required')
        ids.add(ident)
        start,end=selected['start_page'],selected['end_page']
        if type(start)is not int or type(end)is not int or not 1<=start<=end<=len(reader.pages) or end-start>3:
            raise ValueError('Source span must cover 1–4 consecutive physical pages')
        for page in range(start,end+1):
            if page not in cache:cache[page]=reader.pages[page-1].extract_text() or ''
        text='\n'.join(cache[p] for p in range(start,end+1))
        left,right=selected['start_text'],selected['end_text']
        if not isinstance(left,str) or not isinstance(right,str) or not left or not right:
            raise ValueError('Nonempty exact start/end anchors required')
        try:first,left_end=locate_anchor(text,left)
        except ValueError as exc:raise ValueError(ident+': start '+str(exc)) from None
        tail=text[first:]
        try:_,last_in_tail=locate_anchor(tail,right)
        except ValueError as exc:raise ValueError(ident+': end '+str(exc)) from None
        last=first+last_in_tail
        if last<left_end:raise ValueError('End anchor precedes end of start anchor')
        quote=text[first:last]
        rows.append({'requirement_id':ident,'quote':quote,'source_physical_pages':list(range(start,end+1)),
                     'category':selected['category'],'target_section':selected['target_section'],
                     'classification_status':'unverified','source_offsets':[first,last]})
    if digest(source)!=source_sha256:raise ValueError('Source changed during extraction')
    result={'source_sha256':source_sha256,'requirements':rows,
            'anchor_matching':'Whitespace-only normalization; quote retains original text and line breaks',
            'scope':'Exact selected text spans only; selection completeness, classification, table fidelity and visual review remain unverified'}
    with tempfile.TemporaryDirectory() as tmp:
        out=Path(tmp)/'requirements.json';out.write_text(json.dumps(result,ensure_ascii=False,indent=2))
        return {**publish(out,target),'source_sha256':source_sha256,'captured':len(rows),
                'requirement_ids':[r['requirement_id'] for r in rows],'scope':result['scope']}
