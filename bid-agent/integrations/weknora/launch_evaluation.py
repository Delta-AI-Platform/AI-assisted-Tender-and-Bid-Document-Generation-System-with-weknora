"""Launch only the isolated loopback WeKnora build, without copying model keys."""
import json
import os
from pathlib import Path

root = Path('/home/ubuntu/codex-workspace/weknora-evaluation')
build = Path('/home/ubuntu/codex-workspace/weknora-build-tools')
binary = build / 'weknora-lite'
config = json.loads((build / 'evaluation-env.json').read_text())
assert config['SERVER_HOST'] == '127.0.0.1'
assert config['SERVER_PORT'] == '18765'
assert 'host: "127.0.0.1"' in (root / 'config/config.yaml').read_text()
if not binary.is_file():
    raise SystemExit('Build is not complete; no server started')
(build / 'data').mkdir(exist_ok=True)
environment = {key: value for key, value in os.environ.items()
               if key in {'PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR'}}
environment.update(config)
os.chdir(root)
os.execve(str(binary), [str(binary)], environment)
