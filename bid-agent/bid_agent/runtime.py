from pathlib import Path

APP_PYTHON = str(Path(__file__).resolve().parent.parent / '.venv/bin/python')
SKILL_PYTHON = '/home/ubuntu/.local/share/deta-bid-skill/venv/bin/python'


def implementation_version():
    import hashlib
    package=Path(__file__).resolve().parent
    digest=hashlib.sha256()
    files=sorted(package.glob('*.py'))
    lock=package.parent/'requirements-runtime.lock.txt'
    if lock.exists(): files.append(lock)
    for file in files:
        digest.update(file.name.encode());digest.update(b'\0');digest.update(file.read_bytes())
    return digest.hexdigest()
