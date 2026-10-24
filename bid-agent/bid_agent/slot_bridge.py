"""Guarded bridge to the pinned skill's structure snapshot and slot filler."""
import json
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZipFile
from lxml import etree
import hashlib
try:
    from .runtime import SKILL_PYTHON
except ImportError:
    from runtime import SKILL_PYTHON


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

SCRIPT = Path('/home/ubuntu/.agents/skills/tender-bid-authoring/scripts/skeleton_tools.py')
SCRIPT_SHA = 'db0ff9c17a40ed554919b77fd59a756e26369e4900cf39c9fc8bf19221763271'


def skill_call(operation, *paths):
    if digest(SCRIPT) != SCRIPT_SHA:
        raise ValueError('Pinned skeleton skill changed; review required')
    code = '''import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from skeleton_tools import structure_snapshot,fill_text_slots
operation=sys.argv[2]
paths=[Path(p) for p in sys.argv[3:]]
result=structure_snapshot(paths[0]) if operation=='inspect' else fill_text_slots(*paths)
print(json.dumps(result,ensure_ascii=False))
'''
    if operation not in {'inspect', 'fill'}:
        raise ValueError('Unsupported skill operation')
    result = subprocess.run([SKILL_PYTHON, '-c', code, str(SCRIPT.parent), operation,
                             *map(str, paths)], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise ValueError('Skill operation failed: '+result.stderr[-1200:])
    if digest(SCRIPT) != SCRIPT_SHA:
        raise ValueError('Pinned skeleton skill changed during operation')
    return json.loads(result.stdout)


def fill_empty_slots(source, source_sha256, target, values):
    try:
        from .document_ops import publish
    except ImportError:
        from document_ops import publish
    if digest(source) != source_sha256:
        raise ValueError('Source version changed')
    if not isinstance(values, dict) or not values or any(not isinstance(v, str) or not v.strip() for v in values.values()):
        raise ValueError('Explicit nonempty text values required')
    before = skill_call('inspect', source)
    states = {slot['slot_id']: slot['filled'] for slot in before['slots']}
    if len(states) != len(before['slots']):
        raise ValueError('Duplicate source slot identifiers')
    if set(values)-set(states):
        raise ValueError('Unknown slot identifiers')
    if any(states[key] for key in values):
        raise ValueError('Filled slots require explicit revision, not initial fill')
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp);vf=root/'values.json';out=root/'filled.docx'
        vf.write_text(json.dumps(values, ensure_ascii=False))
        result = skill_call('fill', source, vf, out)
        if set(result['updated_slots']) != set(values):
            raise ValueError('Skill did not fill exactly the requested slots')
        after = skill_call('inspect', out)
        if before['structure_sha256'] != after['structure_sha256'] or before['locked_parts'] != after['locked_parts']:
            raise ValueError('Fixed structure changed during fill')
        with ZipFile(source) as a, ZipFile(out) as b:
            if set(a.namelist()) != set(b.namelist()):
                raise ValueError('Package parts changed')
            for name in a.namelist():
                if name != 'word/document.xml' and a.read(name) != b.read(name):
                    raise ValueError('Unrelated package content changed')
            ns = {'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            xml = etree.fromstring(b.read('word/document.xml'))
            for node in xml.xpath('//w:sdt', namespaces=ns):
                tags = node.xpath('./w:sdtPr/w:tag/@w:val', namespaces=ns)
                if not tags or not tags[0].startswith('BID_SLOT:'):
                    continue
                ident = tags[0][9:]
                if ident not in values:
                    continue
                content = node.find('w:sdtContent', ns)
                def text_of(element):
                    return ''.join('\n' if etree.QName(x).localname == 'br' else (x.text or '')
                                   for x in element.xpath('.//w:t|.//w:br', namespaces=ns))
                paragraphs = content.findall('w:p', ns)
                actual = '\n'.join(text_of(p) for p in paragraphs) if paragraphs else text_of(content)
                if actual != values[ident]:
                    raise ValueError('Skill changed requested text; output not published')
        if digest(source) != source_sha256:
            raise ValueError('Source changed during fill')
        return {**publish(out,target),'source_sha256':source_sha256,
                'skill_script_sha256':SCRIPT_SHA,'updated_slots':result['updated_slots'],
                'structure_sha256':after['structure_sha256'],'quality_status':'not_verified',
                'scope':'Initial text fill in existing empty tagged slots; factual correctness and page layout require review'}
