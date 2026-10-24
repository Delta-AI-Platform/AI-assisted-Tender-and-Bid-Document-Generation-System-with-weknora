import fcntl
import json
import tempfile
import unittest
import subprocess
import sys
from pathlib import Path
from worker import dispatch, Store


class BridgeTests(unittest.TestCase):
    def test_worker_exit_after_write_recovers_from_evidence_without_replay(self):
        # Kill the worker at the real durability boundary: the handler has
        # written its file, but end_call has not persisted the observation.
        code = '''
import os,sys
from worker import dispatch,Store
Store.end_call=lambda *args,**kwargs: os._exit(86)
dispatch(sys.argv[1],sys.argv[2],{'method':'call','name':'write_text','arguments':{'path':'crash.txt','content':'原始证据'}})
'''
        process = subprocess.run([sys.executable, '-c', code, str(self.db), self.run],
                                 cwd=Path(__file__).parent, capture_output=True, timeout=30)
        self.assertEqual(process.returncode, 86, process.stderr)
        output = self.project / 'crash.txt'
        self.assertEqual(output.read_text(), '原始证据')
        original_mtime = output.stat().st_mtime_ns
        observation = self.call('write_text', {'path':'crash.txt','content':'原始证据'})
        self.assertEqual(observation['error_type'], 'RecoveryObservation')
        self.assertFalse(observation['ok'])
        result = observation['recovery']
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(result['unresolved'], [])
        self.assertEqual(output.stat().st_mtime_ns, original_mtime)
        with self.store.connect() as conn:
            rows = conn.execute('SELECT tool,status,result FROM calls').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], 'success')
        self.assertTrue(json.loads(rows[0]['result'])['recovered'])
        self.assertTrue(self.call('read_text', {'path':'crash.txt'})['ok'])

    def test_document_crash_recovers_receipt_without_running_next_write(self):
        code = """
import os,sys
from worker import dispatch,Store
Store.end_call=lambda *args,**kwargs: os._exit(86)
dispatch(sys.argv[1],sys.argv[2],{'method':'call','name':'create_docx','arguments':{'target':'proof.docx','blocks':[{'type':'paragraph','text':'证据原文，不作合格承诺'}]}})
"""
        process = subprocess.run([sys.executable, '-c', code, str(self.db), self.run],
                                 cwd=Path(__file__).parent, capture_output=True, timeout=30)
        self.assertEqual(process.returncode, 86, process.stderr)
        output = self.project / 'proof.docx'
        before = output.read_bytes(), output.stat().st_mtime_ns
        observation = self.call('write_text', {'path':'next.txt','content':'must wait'})
        self.assertEqual(observation['error_type'], 'RecoveryObservation')
        self.assertEqual(observation['recovery']['status'], 'pending')
        self.assertTrue(observation['recovery']['resolved'][0]['ok'])
        self.assertFalse((self.project / 'next.txt').exists())
        self.assertEqual((output.read_bytes(), output.stat().st_mtime_ns), before)
        with self.store.connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM calls').fetchone()[0], 1)
        self.assertTrue(self.call('write_text', {'path':'next.txt','content':'explicit next action'})['ok'])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.db = self.root / 'state.sqlite'
        self.store = Store(self.db)
        self.run = self.store.create('Bridge verification', {'project': str(self.project)})

    def call(self, name, args):
        return dispatch(self.db, self.run, {'method': 'call', 'name': name, 'arguments': args})

    def test_write_read_journal_and_no_overwrite(self):
        result = self.call('write_text', {'path': 'evidence.txt', 'content': '来源原文'})
        self.assertTrue(result['ok'])
        self.assertFalse(self.call('write_text', {'path': 'evidence.txt', 'content': '覆盖'})['ok'])
        self.assertEqual((self.project / 'evidence.txt').read_text(), '来源原文')
        with self.store.connect() as conn:
            statuses = [r['status'] for r in conn.execute('SELECT status FROM calls ORDER BY created')]
        self.assertEqual(statuses, ['success', 'failed'])

    def test_scope_and_live_executor(self):
        self.assertFalse(self.call('read_text', {'path': '../outside'})['ok'])
        other = self.store.create('Other task', {'project': str(self.project)})
        with (self.root / (other + '.lock')).open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, 'live executor'):
                self.call('write_text', {'path': 'no.txt', 'content': 'no'})
        self.assertFalse((self.project / 'no.txt').exists())

    def test_uncertain_write_blocks_retry_but_allows_inspection(self):
        from worker import ProjectTools
        tool = ProjectTools(self.project, self.store, self.run).tools['write_text']
        self.store.begin_call(self.run, tool, {'path': 'unknown.txt', 'content': 'x'})
        (self.project / 'unknown.txt').write_text('conflicting output')
        observation = self.call('write_text', {'path': 'unknown.txt', 'content': 'x'})
        self.assertEqual(observation['recovery']['status'], 'needs_recovery')
        self.assertEqual(len(observation['recovery']['unresolved']), 1)
        self.assertEqual((self.project / 'unknown.txt').read_text(), 'conflicting output')
        self.assertTrue(self.call('list_files', {})['ok'])

    def test_catalog_and_memory_authority(self):
        tools = dispatch(self.db, self.run, {'method': 'list'})['tools']
        self.assertIn('expand_table_from_records', [t['name'] for t in tools])
        result = self.call('remember', {'kind': 'fact', 'key': 'claim', 'value': {'source': 'test', 'verification': 'approved'}})
        self.assertEqual(result['data']['verification'], 'unverified')


if __name__ == '__main__':
    unittest.main()
