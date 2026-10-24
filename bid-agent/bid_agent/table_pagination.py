"""Explicit pagination properties only; preserve text, cells and other parts."""
import tempfile
from pathlib import Path
from zipfile import ZipFile
from lxml import etree

NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
def w(name):
    return '{' + NS['w'] + '}' + name


def set_table_pagination(source, source_sha256, target, table_index, header_rows, keep_rows_together):
    try:
        from .document_ops import digest, publish
    except ImportError:
        from document_ops import digest, publish
    if digest(source) != source_sha256:
        raise ValueError('Source version changed')
    if type(table_index) is not int or table_index < 0 or type(header_rows) is not int or header_rows < 0:
        raise ValueError('Nonnegative table_index and header_rows required')
    if type(keep_rows_together) is not bool:
        raise ValueError('Explicit keep_rows_together boolean required')
    with ZipFile(source) as z:
        infos = z.infolist()
        parts = {i.filename: z.read(i) for i in infos}
    root = etree.fromstring(parts['word/document.xml'])
    tables = root.findall('w:body/w:tbl', NS)
    if table_index >= len(tables):
        raise ValueError('Table index outside document')
    table = tables[table_index]
    rows = table.findall(w('tr'))
    if header_rows >= len(rows):
        raise ValueError('Header rows must leave at least one body row')
    before = [etree.tostring(cell) for row in rows for cell in row.findall(w('tc'))]
    for i, row in enumerate(rows):
        props = row.find(w('trPr'))
        if props is None:
            props = etree.Element(w('trPr'))
            # trPr must follow optional table property exceptions.
            row.insert(1 if len(row) and row[0].tag == w('tblPrEx') else 0, props)
        for name, enabled in [('tblHeader', i < header_rows), ('cantSplit', keep_rows_together)]:
            for old in props.findall(w(name)):
                props.remove(old)
            node = etree.SubElement(props, w(name))
            node.set(w('val'), '1' if enabled else '0')
    after = [etree.tostring(cell) for row in rows for cell in row.findall(w('tc'))]
    if before != after:
        raise ValueError('Unexpected cell content change')
    parts['word/document.xml'] = etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / 'pagination.docx'
        with ZipFile(out, 'w') as z:
            for info in infos:
                z.writestr(info, parts[info.filename])
        if digest(source) != source_sha256:
            raise ValueError('Source changed during operation')
        return {**publish(out, target), 'source_sha256': source_sha256, 'table_index': table_index,
                'header_rows': header_rows, 'keep_rows_together': keep_rows_together,
                'rows': len(rows), 'cell_xml_unchanged': True, 'quality_status': 'not_verified',
                'limitation': 'Render and inspect; rows taller than a page may still split.'}
