#!/usr/bin/env python3
"""Export active WeKnora evidence chunks into a small read-only retrieval index."""
import argparse
import gzip
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

LIBRARIES = {
    "5d7c068a-a0ea-4e2d-9177-5e46d9774ebc": "enterprise_facts",
    "906bfd8a-586c-5634-bced-d7842fce6929": "inspection_reports",
}


def decode_json(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    return json.loads(value or "{}")


def catalog_sha_map(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        catalog = json.load(stream)
    result = defaultdict(list)
    for document in catalog["documents"]:
        if not document.get("active_module"):
            continue
        for member in document.get("members") or []:
            digest = member.get("sha256")
            if digest:
                result[digest].append((document["id"], member.get("id"), member.get("page_no")))
    return result


def build(source, catalog_path, destination):
    sha_map = catalog_sha_map(catalog_path)
    source_db = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    source_db.row_factory = sqlite3.Row
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=destination.name + ".", dir=destination.parent)
    os.close(fd)
    try:
        target = sqlite3.connect(temporary)
        target.executescript("""
          PRAGMA journal_mode=DELETE;
          PRAGMA synchronous=FULL;
          CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE chunks(
            id INTEGER PRIMARY KEY,
            library TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            member_id TEXT,
            member_page INTEGER,
            native_knowledge_id TEXT NOT NULL,
            native_chunk_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            source_sha256 TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            source_locators TEXT NOT NULL
          );
          CREATE INDEX chunks_library_doc ON chunks(library,doc_id,chunk_index);
          CREATE INDEX chunks_native_knowledge ON chunks(native_knowledge_id,chunk_index);
        """)
        query = """
          SELECT c.id chunk_id,c.chunk_index,c.content,c.source_locators,
                 k.id knowledge_id,k.knowledge_base_id,k.title,k.metadata
            FROM chunks c JOIN knowledges k ON k.id=c.knowledge_id
           WHERE k.knowledge_base_id IN (?,?) AND k.deleted_at IS NULL
             AND k.type='file' AND k.parse_status='completed' AND k.enable_status='enabled'
             AND c.deleted_at IS NULL AND c.is_enabled=1
           ORDER BY k.knowledge_base_id,k.id,c.chunk_index
        """
        inserted = 0
        unmatched_knowledges = set()
        matched_knowledges = set()
        for row in source_db.execute(query, tuple(LIBRARIES)):
            metadata = decode_json(row["metadata"])
            digest = metadata.get("original_sha256") or ""
            mappings = sha_map.get(digest, [])
            if not mappings:
                unmatched_knowledges.add(row["knowledge_id"])
                continue
            matched_knowledges.add(row["knowledge_id"])
            locators = row["source_locators"] or "[]"
            # Validate now so the installed index never contains malformed locator JSON.
            json.loads(locators)
            for doc_id, member_id, member_page in mappings:
                target.execute("""INSERT INTO chunks(
                  library,doc_id,member_id,member_page,native_knowledge_id,native_chunk_id,
                  chunk_index,source_sha256,title,content,source_locators
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (
                    LIBRARIES[row["knowledge_base_id"]], doc_id, member_id, member_page,
                    row["knowledge_id"], row["chunk_id"], row["chunk_index"], digest,
                    row["title"], row["content"], locators,
                ))
                inserted += 1
        metadata = {
            "schema_version": "deta-evidence-text-index-v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_db": str(source),
            "source_db_sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest(),
            "catalog": str(catalog_path),
            "chunk_rows": inserted,
            "matched_native_knowledges": len(matched_knowledges),
            "unmatched_native_knowledges": len(unmatched_knowledges),
        }
        target.executemany("INSERT INTO metadata(key,value) VALUES(?,?)", ((k, json.dumps(v, ensure_ascii=False)) for k, v in metadata.items()))
        target.commit()
        integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
        target.close()
        if integrity != "ok":
            raise RuntimeError("index integrity check failed: " + integrity)
        os.chmod(temporary, 0o640)
        os.replace(temporary, destination)
        return {**metadata, "destination": str(destination), "integrity_check": integrity, "bytes": destination.stat().st_size}
    finally:
        source_db.close()
        if os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.catalog, args.destination), ensure_ascii=False, indent=2))
