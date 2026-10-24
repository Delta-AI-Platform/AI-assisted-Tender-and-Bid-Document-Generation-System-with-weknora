"""Read only registered outputs; web callers must first verify session ownership."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3


class ArtifactAccess:
    def __init__(self, state, run_id):
        self.state = Path(state).resolve()
        self.run_id = run_id

    def _records(self):
        with sqlite3.connect(self.state.as_uri() + '?mode=ro', uri=True) as db:
            row = db.execute('SELECT scope FROM runs WHERE id=?', (self.run_id,)).fetchone()
            if not row:
                raise KeyError('Unknown bound task')
            root = Path(json.loads(row[0])['project']).resolve(strict=True)
            rows = db.execute("SELECT sequence,payload FROM events WHERE run_id=? AND kind='artifact_created' ORDER BY sequence", (self.run_id,)).fetchall()
        return root, {str(seq): json.loads(payload) for seq, payload in rows}

    def _delivery_records(self):
        root, events = self._records()
        # Native ten-step projects publish only the stage-9 delivery set. All
        # skeletons, fragments, JSON, page images and prior versions remain
        # available to the workflow internally but never appear as downloads.
        receipt_dir = root / 'stage_receipts'
        stage9, stage10 = receipt_dir / '09.json', receipt_dir / '10.json'
        if stage9.is_file() or stage10.is_file():
            if not (stage9.is_file() and stage10.is_file()):
                return root, {}
            nine, ten = json.loads(stage9.read_text()), json.loads(stage10.read_text())
            if nine.get('status') == 'stale' or ten.get('status') == 'stale':
                return root, {}
            allowed = {'final_docx', 'final_xlsx', 'rendered_pdf'}
            records = {}
            for item in nine.get('artifacts', []):
                if item.get('kind') in allowed:
                    records['stage9-' + item['kind']] = {
                        'output': item['path'], 'sha256': item['sha256'],
                        'quality_status': 'final_candidate',
                    }
            return root, records
        # Legacy tasks must explicitly mark a receipt as final. This avoids
        # exposing every edit version and visual-check image in the sidebar.
        return root, {key: value for key, value in events.items()
                      if value.get('final_delivery') is True or value.get('delivery_class') == 'final'}

    def list(self):
        _, records = self._delivery_records()
        return [{'id': key, 'name': Path(value['output']).name,
                 'sha256': value['sha256'],
                 'quality_status': value.get('quality_status', 'final_candidate')}
                for key, value in records.items() if value.get('output') and value.get('sha256')]

    def read(self, artifact_id):
        root, records = self._delivery_records()
        receipt = records.get(str(artifact_id))
        if not receipt or not receipt.get('sha256'):
            raise KeyError('Unknown artifact')
        path = PurePosixPath(receipt['output'])
        if path.is_absolute() or not path.parts or any(p in {'.', '..'} for p in path.parts):
            raise ValueError('Invalid registered path')
        # Open every component relative to an already-open directory. Reject
        # symlinks even if replaced concurrently between validation and open.
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in path.parts[:-1]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            file_fd = os.open(path.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(file_fd, 'rb') as handle:
                import stat
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise ValueError('Artifact is not a regular file')
                data = handle.read()
        finally:
            os.close(fd)
        if hashlib.sha256(data).hexdigest() != receipt['sha256']:
            raise ValueError('Artifact differs from registered version')
        return path.name, data
