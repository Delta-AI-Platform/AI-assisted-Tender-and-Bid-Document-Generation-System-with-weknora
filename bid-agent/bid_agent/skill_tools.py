"""Read-only adapter for the installed bid skill's audited document helper."""
import hashlib
import json
import subprocess
from .runtime import SKILL_PYTHON


def compact_inspection(data):
    """Expose actionable structure without echoing raw OOXML into model context."""
    import xml.etree.ElementTree as ET
    result={k:v for k,v in data.items() if k not in {'body_text','sections'}}
    body=data.get('body_text','')
    result['body_text_characters']=len(body)
    result['body_text_sha256']=hashlib.sha256(body.encode()).hexdigest()
    result['body_text_access']='Use read_document for content; this result is structure only.'
    ns='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    sections=[]
    for index,xml in enumerate(data.get('sections',[])):
        root=ET.fromstring(xml)
        section={'index':index,'xml_sha256':hashlib.sha256(xml.encode()).hexdigest()}
        for tag in ('type','pgSz','pgMar','cols','docGrid'):
            node=root.find(ns+tag)
            if node is not None:section[tag]={k.split('}')[-1]:v for k,v in node.attrib.items()}
        sections.append(section)
    result['section_count']=len(sections)
    result['sections']=sections
    result['image_occurrence_count']=len(data.get('image_occurrences',[]))
    return result


def install_skill_tools(registry):
    from .tools import schema, STRING

    def inspect_skill_document(path, operation):
        if operation not in ('inspect', 'pages'):
            raise ValueError('Only read-only inspect/pages operations are supported')
        name = 'tender-bid-authoring'
        root = registry.skills.get(name)
        if root is None:
            raise ValueError('Bid skill is not mounted for this task')
        relative = 'scripts/docx_tools.py'
        script = (root/relative).resolve(strict=True)
        if not script.is_relative_to(root):
            raise ValueError('Helper escapes mounted skill')
        digest = hashlib.sha256(script.read_bytes()).hexdigest()
        pin = registry.store.get(registry.run_id)['scope'].get('skill_pins', {}).get(name, {}).get(relative)
        if not pin or pin != digest:
            raise ValueError('Helper requires a matching task-creation fingerprint')
        source = registry.path(path)
        if source.suffix.lower() != ('.docx' if operation == 'inspect' else '.pdf'):
            raise ValueError('Document extension does not match operation')
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        result = subprocess.run([SKILL_PYTHON, str(script), operation, str(source)],
                                capture_output=True, text=True, timeout=90)
        if result.returncode:
            raise ValueError('Skill inspection failed: '+result.stderr[-2000:])
        data = json.loads(result.stdout)
        if data.get('sha256') != before or hashlib.sha256(source.read_bytes()).hexdigest() != before:
            raise ValueError('Source changed during inspection')
        return {'source': path, 'source_sha256': before, 'helper_sha256': digest,
                'data': compact_inspection(data) if operation == 'inspect' else data, 'scope': 'Document structure only; no factual or visual approval'}

    registry.add('inspect_skill_document',
                 '通过已锁定版本的现有技能检查 DOCX 表格、分节尺寸、图片数量等结构摘要（不返回原始XML，正文用read_document读取），或 PDF 物理页数及书签；不代表内容或排版通过。',
                 schema({'path': STRING, 'operation': {'type': 'string', 'enum': ['inspect', 'pages']}}),
                 inspect_skill_document)
