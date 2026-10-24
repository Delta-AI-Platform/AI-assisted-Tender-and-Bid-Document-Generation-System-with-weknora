"""Bounded DOCX table metadata without loading every cell into model context."""
import hashlib
from xml.etree import ElementTree as ET
from zipfile import ZipFile

NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}


def inspect_tables(path, offset, count):
    if type(offset) is not int or offset < 0 or type(count) is not int or not 1 <= count <= 20:
        raise ValueError('offset >= 0 and count between 1 and 20 required')
    raw = path.read_bytes()
    import io
    with ZipFile(io.BytesIO(raw)) as package:
        document = ET.fromstring(package.read('word/document.xml'))
    body = document.find('w:body', NS)
    tables = []
    for block_index, block in enumerate(body):
        if block.tag != '{'+NS['w']+'}tbl':
            continue
        table_index = len(tables)
        rows = block.findall('w:tr', NS)
        cells = rows[0].findall('w:tc', NS) if rows else []
        headers = [''.join(t.text or '' for t in c.findall('.//w:t', NS)) for c in cells]
        tables.append({'table_index':table_index, 'block_index':block_index,
                       'rows':len(rows), 'first_row_cells':len(cells),
                       'header_preview':[h[:160] for h in headers[:24]],
                       'header_preview_truncated':len(headers)>24 or any(len(h)>160 for h in headers),
                       'repeated_header_rows':sum(r.find('w:trPr/w:tblHeader',NS) is not None and r.find('w:trPr/w:tblHeader',NS).get('{'+NS['w']+'}val','1') not in ('0','false','off') for r in rows)})
    return {'sha256':hashlib.sha256(raw).hexdigest(), 'total_tables':len(tables),
            'offset':offset, 'tables':tables[offset:offset+count],
            'next_offset':offset+count if offset+count<len(tables) else None,
            'scope':'Table structure and bounded header previews only; not full text or visual verification'}
