"""Audit the isolated browser continuation against durable records."""
import hashlib
import json
import sqlite3
from pathlib import Path

root = Path(__file__).resolve().parents[2]
p = root / 'runs/weknora-bridge-validation'
before = json.loads((p / 'ui-recovery-injection.json').read_text())
file = p / 'project' / before['file']
with sqlite3.connect(p / 'state.sqlite') as db:
    db.row_factory = sqlite3.Row
    calls = [dict(r) for r in db.execute("SELECT id,status,result FROM calls WHERE tool='create_docx' AND json_extract(args,'$.target')=?", (before['file'],))]
    memory = db.execute("SELECT run_id,value FROM memory WHERE key='ui-recovery-check-20261022' ORDER BY revision DESC LIMIT 1").fetchone()
with sqlite3.connect(root.parent / 'weknora-build-tools/data/weknora.db') as db:
    db.row_factory = sqlite3.Row
    message = db.execute("SELECT id,channel,agent_id,is_completed,content FROM messages WHERE session_id=? AND role='assistant' ORDER BY created_at DESC LIMIT 1", ('42c90d3b-65f8-4870-b7ff-075106f19528',)).fetchone()
report = {
    'scope': before['injected_by'],
    'file_unchanged': hashlib.sha256(file.read_bytes()).hexdigest() == before['sha256'],
    'mtime_unchanged': file.stat().st_mtime_ns == before['mtime_ns'],
    'create_call_count': len(calls),
    'original_call_recovered': len(calls)==1 and calls[0]['status']=='success' and json.loads(calls[0]['result']).get('recovered') is True,
    'memory_saved': memory is not None,
    'message': dict(message) if message else None,
}
report['passed'] = all([report['file_unchanged'],report['mtime_unchanged'],report['create_call_count']==1,report['original_call_recovered'],report['memory_saved'],message is not None and message['channel']=='web' and message['is_completed']==1])
(p / 'browser-recovery-acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps({k:v for k,v in report.items() if k!='message'},ensure_ascii=False))
