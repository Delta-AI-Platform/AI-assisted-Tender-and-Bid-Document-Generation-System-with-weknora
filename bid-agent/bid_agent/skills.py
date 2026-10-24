"""Explicit, read-only skill mounts pinned at task creation."""
import hashlib
from pathlib import Path

DEFAULT_SKILL = Path('/home/ubuntu/.agents/skills/tender-bid-authoring')
MANUAL = 'assets/标书制作全流程操作手册_v1.38.docx'
EXPECTED_MANUAL = '32fe1560c7d9ce9ab5b9136d548bd5adf6666a50a3d2b8843eb82395910f7b28'


def mount_skills(overrides=None):
    mounts = dict(overrides or {})
    if not mounts and (DEFAULT_SKILL/'SKILL.md').is_file():
        mounts['tender-bid-authoring'] = str(DEFAULT_SKILL)
    pins = {}
    for name, root in mounts.items():
        root=Path(root).resolve(strict=True)
        pins[name] = {'SKILL.md': hashlib.sha256((root/'SKILL.md').read_bytes()).hexdigest()}
        helper = root/'scripts/docx_tools.py'
        if helper.is_file():
            pins[name]['scripts/docx_tools.py'] = hashlib.sha256(helper.read_bytes()).hexdigest()
        if (root/MANUAL).is_file():
            actual=hashlib.sha256((root/MANUAL).read_bytes()).hexdigest()
            if name=='tender-bid-authoring' and actual!=EXPECTED_MANUAL:
                raise ValueError('Protected v1.38 manual fingerprint differs; do not silently substitute')
            pins[name][MANUAL]=actual
        mounts[name]=str(root)
    return mounts,pins
