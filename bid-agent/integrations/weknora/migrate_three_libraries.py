"""Split the native WeKnora evidence UI into three source-bound libraries.

The operation moves database ownership references only. Stored original bytes,
knowledge IDs, chunks, embeddings and hashes remain unchanged.
"""
import gzip
import io
import json
import sqlite3
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/home/ubuntu/codex-workspace')
DB = ROOT / 'weknora-build-tools/data/weknora.db'
CATALOG = '/srv/deta-bid/knowledge-index/catalog.json.gz'
FACTS_ID = '5d7c068a-a0ea-4e2d-9177-5e46d9774ebc'
REPORTS_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, 'deta-bid:inspection-reports:v1'))
HISTORY_ID = '746eaa80-cd88-4a69-a0ef-d27dc6c0a6b6'
AGENT_ID = '5546cce7-872d-483a-a260-893339fbe05d'
RERANK_ID = '18f740b6-d83e-4576-a469-9c8753c11659'
EMBED_ID = 'f6ca9bac-bc2f-465a-8657-9df1b5d0261e'
VLM_ID = '07d64682-6a60-458a-9252-ee064ae4f750'


def report_name(name):
    name = str(name or '')
    return (name.startswith('TEST__') or
            (not name.lower().endswith(('.rar', '.zip', '.7z')) and
             any(x in name for x in ('检验报告', '检测报告', '试验报告', '测试报告', '出厂检验'))))


def load_catalog():
    raw = subprocess.run(['sudo', '-n', '-u', 'deta-bid', 'cat', CATALOG],
                         check=True, capture_output=True).stdout
    return json.load(gzip.GzipFile(fileobj=io.BytesIO(raw)))['documents']


def json_value(value):
    if isinstance(value, bytes):
        value = value.decode('utf-8')
    return json.loads(value or '{}')


def main():
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    backup = DB.with_name('weknora-before-three-libraries-20261024.db')
    source = sqlite3.connect(DB)
    if not backup.exists():
        target = sqlite3.connect(backup)
        source.backup(target)
        target.close()
    source.row_factory = sqlite3.Row
    catalog = load_catalog()
    by_sha = {}
    for doc in catalog:
        if not doc.get('active_module'):
            continue
        kind = 'report' if report_name(doc.get('display_name')) or doc.get('category_code') == 'TEST' else 'fact'
        for member in doc.get('members', []):
            if member.get('sha256'):
                by_sha.setdefault(member['sha256'], set()).add(kind)

    with source:
        facts = source.execute('SELECT * FROM knowledge_bases WHERE id=?', (FACTS_ID,)).fetchone()
        existing = source.execute('SELECT id FROM knowledge_bases WHERE id=?', (REPORTS_ID,)).fetchone()
        if not existing:
            row = dict(facts)
            row.update(id=REPORTS_ID, name='检验报告参数库',
                       description='只存放车型、子型号、部件检验/检测/试验报告原件及报告页。参数必须绑定报告、测试条件和原件物理页；本库不包含企业证照、财务、业绩或历史响应。',
                       embedding_model_id=EMBED_ID,
                       indexing_strategy=json.dumps({'vector_enabled': True, 'keyword_enabled': True, 'wiki_enabled': False, 'graph_enabled': False}, ensure_ascii=False),
                       created_at=now, updated_at=now, deleted_at=None)
            cols = list(row)
            source.execute(f"INSERT INTO knowledge_bases ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})", [row[x] for x in cols])
        source.execute('''UPDATE knowledge_bases SET name=?,description=?,embedding_model_id=?,indexing_strategy=?,updated_at=? WHERE id=?''',
                       ('企业事实库', '只存放营业执照、企业证书、财务、人员、业绩、合同和授权承诺等原件事实；检验报告与历史响应已分库。事实使用必须绑定原件和具体位置。', EMBED_ID,
                        json.dumps({'vector_enabled': True, 'keyword_enabled': True, 'wiki_enabled': False, 'graph_enabled': False}, ensure_ascii=False), now, FACTS_ID))
        source.execute('''UPDATE knowledge_bases SET name=?,description=?,embedding_model_id=?,indexing_strategy=?,updated_at=? WHERE id=?''',
                       ('历史响应权威库', '只存放人工核验过的历史响应原件及标准答案来源。必须按车型范围和版本使用；历史响应不是本项目事实证据。', EMBED_ID,
                        json.dumps({'vector_enabled': True, 'keyword_enabled': True, 'wiki_enabled': False, 'graph_enabled': False}, ensure_ascii=False), now, HISTORY_ID))

        moved = 0
        rows = source.execute('SELECT id,file_name,metadata FROM knowledges WHERE knowledge_base_id=? AND deleted_at IS NULL AND type=?', (FACTS_ID, 'file')).fetchall()
        for row in rows:
            meta = json_value(row['metadata'])
            sha = meta.get('original_sha256')
            classes = by_sha.get(sha, set())
            to_report = report_name(row['file_name']) or classes == {'report'}
            if to_report:
                source.execute('UPDATE knowledges SET knowledge_base_id=?,updated_at=? WHERE id=?', (REPORTS_ID, now, row['id']))
                moved += 1

        # Retrieval rows denormalize the knowledge-base ID. Rebind them after
        # moving originals so native keyword/vector retrieval follows the same
        # three-library boundary as the document list.
        report_ids = [x[0] for x in source.execute('SELECT id FROM knowledges WHERE knowledge_base_id=? AND deleted_at IS NULL', (REPORTS_ID,))]
        rebound_chunks = rebound_embeddings = 0
        for start in range(0, len(report_ids), 500):
            batch = report_ids[start:start + 500]; marks = ','.join('?' for _ in batch)
            rebound_chunks += source.execute(f'UPDATE chunks SET knowledge_base_id=? WHERE knowledge_id IN ({marks}) AND knowledge_base_id<>?', [REPORTS_ID, *batch, REPORTS_ID]).rowcount
            rebound_embeddings += source.execute(f'UPDATE lite_embeddings SET knowledge_base_id=? WHERE knowledge_id IN ({marks}) AND knowledge_base_id<>?', [REPORTS_ID, *batch, REPORTS_ID]).rowcount

        # Directory-index manuals were useful during bootstrap but mix library
        # boundaries and are not originals. Failed parses were already excluded
        # from the authoritative catalog and must not remain visible.
        manuals = source.execute('UPDATE knowledges SET deleted_at=?,updated_at=? WHERE knowledge_base_id=? AND type=? AND deleted_at IS NULL', (now, now, FACTS_ID, 'manual')).rowcount
        failed = source.execute('UPDATE knowledges SET deleted_at=?,updated_at=? WHERE knowledge_base_id IN (?,?) AND parse_status=? AND deleted_at IS NULL', (now, now, FACTS_ID, REPORTS_ID, 'failed')).rowcount

        agent = source.execute('SELECT config FROM custom_agents WHERE id=? AND tenant_id=1', (AGENT_ID,)).fetchone()
        config = json_value(agent['config'])
        config.update(kb_selection_mode='selected', knowledge_bases=[FACTS_ID, REPORTS_ID, HISTORY_ID],
                      rerank_model_id=RERANK_ID, embedding_top_k=30, rerank_top_k=8,
                      rerank_threshold=0, citation_enabled=True,
                      image_upload_enabled=True, vlm_model_id=VLM_ID,
                      attachment_image_understanding=True,
                      retrieve_kb_only_when_mentioned=True)
        marker = ('\n\n知识与证据库固定为三个独立库：企业事实库、检验报告参数库、历史响应权威库。'
                  '检索必须依次执行主体/车型过滤、关键词、向量召回、rerank、规则校验、原件页验证。'
                  '召回候选不得直接生成满足或无偏离；企业事实与检验报告不得从历史响应反推。')
        if marker not in config.get('system_prompt', ''):
            config['system_prompt'] = config.get('system_prompt', '') + marker
        source.execute('UPDATE custom_agents SET config=?,updated_at=? WHERE id=? AND tenant_id=1',
                       (json.dumps(config, ensure_ascii=False), now, AGENT_ID))

    check = source.execute('PRAGMA integrity_check').fetchone()[0]
    counts = {}
    for ident, name in ((FACTS_ID, 'enterprise_facts'), (REPORTS_ID, 'inspection_reports'), (HISTORY_ID, 'history')):
        counts[name] = dict(source.execute('''SELECT count(*) total,sum(CASE WHEN type='file' THEN 1 ELSE 0 END) originals,
                                      sum(CASE WHEN parse_status='completed' THEN 1 ELSE 0 END) completed
                                      FROM knowledges WHERE knowledge_base_id=? AND deleted_at IS NULL''', (ident,)).fetchone())
    report = {'version': 'deta-three-library-migration-v1', 'database': str(DB), 'backup': str(backup),
              'integrity_check': check, 'library_ids': {'enterprise_facts': FACTS_ID, 'inspection_reports': REPORTS_ID, 'history': HISTORY_ID},
              'moved_to_inspection_reports': moved, 'mixed_manual_indexes_removed': manuals,
              'failed_records_removed': failed, 'chunk_rows_rebound': rebound_chunks,
              'embedding_rows_rebound': rebound_embeddings, 'counts': counts,
              'agent_id': AGENT_ID, 'agent_bound_libraries': [FACTS_ID, REPORTS_ID, HISTORY_ID],
              'embedding_model_id': EMBED_ID, 'rerank_model_id': RERANK_ID, 'vlm_model_id': VLM_ID}
    out = ROOT / 'bid-agent/integrations/weknora/three-library-migration-report.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
