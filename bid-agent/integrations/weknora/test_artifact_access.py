import hashlib
import tempfile
import unittest
from pathlib import Path
from worker import Store
from artifact_access import ArtifactAccess


class ArtifactAccessTests(unittest.TestCase):
    def test_registered_versions_only_and_no_cross_task_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = root / 'project'; project.mkdir()
            store = Store(root / 'state.sqlite')
            run = store.create('delivery', {'project': str(project)})
            other = store.create('other', {'project': str(project)})
            (project / 'bid.docx').write_bytes(b'original')
            (project / 'private.txt').write_text('not registered')
            with store.connect() as db:
                store.event(db, run, 'artifact_created', {'output': 'bid.docx', 'sha256': hashlib.sha256(b'original').hexdigest(), 'final_delivery': True})
            access = ArtifactAccess(store.path, run)
            item = access.list()[0]
            self.assertEqual(access.read(item['id']), ('bid.docx', b'original'))
            self.assertEqual(ArtifactAccess(store.path, other).list(), [])
            with self.assertRaises(KeyError): access.read('private.txt')
            with self.assertRaises(KeyError): ArtifactAccess(store.path, other).read(item['id'])
            (project / 'bid.docx').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'differs'): access.read(item['id'])
            (project / 'bid.docx').unlink()
            outside = root / 'outside'; outside.write_bytes(b'original')
            (project / 'bid.docx').symlink_to(outside)
            with self.assertRaises(OSError): access.read(item['id'])

    def test_ten_stage_project_exposes_only_stage_nine_final_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = root / 'project'; project.mkdir()
            (project / 'outputs').mkdir(); (project / 'analysis').mkdir(); (project / 'stage_receipts').mkdir()
            store = Store(root / 'state.sqlite')
            run = store.create('delivery', {'project': str(project)})
            artifacts = []
            for kind, name, data in [
                ('final_docx', 'final.docx', b'docx'),
                ('final_xlsx', 'final.xlsx', b'xlsx'),
                ('rendered_pdf', 'final.pdf', b'pdf'),
                ('attachment_manifest', 'manifest.json', b'{}'),
            ]:
                folder = 'outputs' if kind.startswith('final_') or kind == 'rendered_pdf' else 'analysis'
                path = project / folder / name; path.write_bytes(data)
                artifacts.append({'kind': kind, 'path': f'{folder}/{name}', 'sha256': hashlib.sha256(data).hexdigest()})
            (project / 'stage_receipts/09.json').write_text(__import__('json').dumps({'artifacts': artifacts, 'status': 'candidate_needs_review'}))
            (project / 'stage_receipts/10.json').write_text(__import__('json').dumps({'artifacts': [], 'status': 'candidate_needs_review'}))
            with store.connect() as db:
                store.event(db, run, 'artifact_created', {'output': 'analysis/manifest.json', 'sha256': hashlib.sha256(b'{}').hexdigest(), 'final_delivery': True})
            access = ArtifactAccess(store.path, run)
            self.assertEqual({x['name'] for x in access.list()}, {'final.docx', 'final.xlsx', 'final.pdf'})


if __name__ == '__main__': unittest.main()
