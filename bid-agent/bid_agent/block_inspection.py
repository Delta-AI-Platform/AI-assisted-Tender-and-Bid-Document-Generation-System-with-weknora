"""Bounded top-level DOCX text with indices shared by guarded editors."""
import hashlib
from xml.etree import ElementTree as ET
from zipfile import ZipFile

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

def inspect_blocks(path, offset, count, query):
    if type(offset) is not int or offset < 0 or type(count) is not int or not 1 <= count <= 40:
        raise ValueError('offset >= 0 and count between 1 and 40 required')
    if not isinstance(query, str) or len(query) > 300:
        raise ValueError('query must be a string of at most 300 characters')
    with path.open('rb') as stream:
        sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        stream.seek(0)
        with ZipFile(stream) as z:
            info = z.getinfo('word/document.xml')
            if info.file_size > 20_000_000:
                raise ValueError('document XML exceeds bounded inspection limit')
            body = ET.fromstring(z.read(info)).find(W+'body')
    if body is None:
        raise ValueError('DOCX body missing')
    matches = []
    for index, block in enumerate(body):
        text = ''.join(n.text or '' for n in block.iter(W+'t'))
        if not query or query in text:
            matches.append({'block': index, 'kind': block.tag.rsplit('}',1)[-1],
                            'text': text[:4000], 'text_truncated': len(text)>4000,
                            'text_characters':len(text)})
    return {'sha256':sha, 'total_blocks':len(body), 'matching_blocks':len(matches),
            'offset':offset, 'blocks':matches[offset:offset+count],
            'next_offset':offset+count if offset+count<len(matches) else None,
            'scope':'Top-level text only; block indices match edit_docx. Offset pages matching records, not body indices. Truncated text is not a valid edit before-value. No visual or field verification.'}
