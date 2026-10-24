from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


class Store:
    """Transactions commit observable events with their state transitions."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs(
                    id TEXT PRIMARY KEY, objective TEXT NOT NULL,
                    status TEXT NOT NULL, scope TEXT NOT NULL,
                    last_result TEXT, owner TEXT, lease_until REAL NOT NULL DEFAULT 0,
                    created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS events(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
                    created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS calls(
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, tool TEXT NOT NULL,
                    tool_version TEXT NOT NULL, args TEXT NOT NULL,
                    side_effect INTEGER NOT NULL, status TEXT NOT NULL,
                    result TEXT, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS memory(
                    run_id TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL,
                    revision INTEGER NOT NULL, value TEXT NOT NULL,
                    created REAL NOT NULL, PRIMARY KEY(run_id,kind,key,revision));
            """)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def event(conn, run_id, kind, payload):
        conn.execute("INSERT INTO events(run_id,kind,payload,created) VALUES(?,?,?,?)",
                     (run_id, kind, encode(payload), time.time()))

    def create(self, objective, scope):
        if not objective.strip():
            raise ValueError("Task objective is required")
        run_id = uuid.uuid4().hex
        with self.connect() as conn:
            conn.execute("INSERT INTO runs(id,objective,status,scope,created) VALUES(?,?,?,?,?)",
                         (run_id, objective, "pending", encode(scope), time.time()))
            self.event(conn, run_id, "task_received", {"objective": objective, "scope": scope})
        return run_id

    def get(self, run_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        result = dict(row)
        result["scope"] = json.loads(result["scope"])
        result["last_result"] = json.loads(result["last_result"]) if result["last_result"] else None
        return result

    def claim(self, run_id, owner, lease_seconds):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise KeyError(run_id)
            if row["status"] not in {"pending", "running", "yielded"}:
                raise ValueError(f"Run cannot execute in state {row['status']}")
            if row["owner"] and row["lease_until"] > time.time():
                raise ValueError("Run already has a live executor lease")
            # A missing execution result is uncertainty, not evidence of success.
            calls = conn.execute("SELECT id,side_effect FROM calls WHERE run_id=? AND status='running'",
                                 (run_id,)).fetchall()
            uncertain = any(c["side_effect"] for c in calls)
            for call in calls:
                conn.execute("UPDATE calls SET status='interrupted' WHERE id=?", (call["id"],))
                self.event(conn, run_id, "tool_interrupted", dict(call))
            status = "needs_recovery" if uncertain else "running"
            conn.execute("UPDATE runs SET status=?,last_result=NULL,owner=?,lease_until=? WHERE id=?",
                         (status, owner, time.time() + lease_seconds, run_id))
            self.event(conn, run_id, "executor_claimed", {"owner": owner, "status": status})
            return status

    def renew(self, run_id, owner, lease_seconds):
        with self.connect() as conn:
            count = conn.execute("UPDATE runs SET lease_until=? WHERE id=? AND owner=?",
                                 (time.time()+lease_seconds, run_id, owner)).rowcount
            if count != 1:
                raise RuntimeError("Execution lease lost")

    def release(self, run_id, owner):
        with self.connect() as conn:
            conn.execute("UPDATE runs SET owner=NULL,lease_until=0 WHERE id=? AND owner=?", (run_id, owner))

    def transition(self, run_id, status, payload):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if not current:
                raise KeyError(run_id)
            # A concurrent stop must not be overwritten by model/tool completion.
            if current["status"] == "canceled" and status != "canceled":
                return
            conn.execute("UPDATE runs SET status=?,last_result=? WHERE id=?", (status, encode(payload), run_id))
            self.event(conn, run_id, status, payload)

    def answer(self, run_id, text):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status,last_result FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None or row["status"] != "waiting_input":
                raise ValueError("Run is not waiting for input")
            self.event(conn, run_id, "user_answer", {"text": text, "question": json.loads(row["last_result"] or "{}").get("question")})
            conn.execute("UPDATE runs SET status='pending' WHERE id=?", (run_id,))

    def remember(self, run_id, kind, key, value):
        # The model cannot write user authorizations, check receipts, or run status.
        if kind not in {"plan", "requirement", "evidence", "fact", "open_item"}:
            raise ValueError("Allowed memory kinds: plan, requirement, evidence, fact, open_item")
        if kind in {"requirement", "evidence", "fact"} and not value.get("source"):
            raise ValueError("Source reference is required; this remains an unverified assertion")
        value = {**value, "verification": "unverified"}
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rev = conn.execute("SELECT COALESCE(MAX(revision),0)+1 FROM memory WHERE run_id=? AND kind=? AND key=?",
                               (run_id, kind, key)).fetchone()[0]
            conn.execute("INSERT INTO memory VALUES(?,?,?,?,?,?)", (run_id, kind, key, rev, encode(value), time.time()))
            self.event(conn, run_id, "memory_written", {"kind": kind, "key": key, "revision": rev})
        return {"kind": kind, "key": key, "revision": rev, "verification": "unverified"}

    def begin_call(self, run_id, tool, args, model_call_id=None):
        call_id = uuid.uuid4().hex
        with self.connect() as conn:
            conn.execute("INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?)",
                         (call_id, run_id, tool.name, tool.version, encode(args), int(tool.side_effect),
                          "running", None, time.time()))
            self.event(conn, run_id, "tool_started", {"call_id": call_id, "tool": tool.name,
                                                      "version": tool.version, "args": args, "model_call_id": model_call_id})
        return call_id

    def end_call(self, run_id, call_id, result):
        with self.connect() as conn:
            conn.execute("UPDATE calls SET status=?,result=? WHERE id=? AND run_id=?",
                         ("success" if result["ok"] else "failed", encode(result), call_id, run_id))
            self.event(conn, run_id, "tool_observation", {"call_id": call_id, **result})

    def context(self, run_id):
        run = self.get(run_id)
        project = run['scope'].get('project')
        with self.connect() as conn:
            related = [run_id]
            if project:
                related = [r['id'] for r in conn.execute('SELECT id,scope FROM runs')
                           if json.loads(r['scope']).get('project') == project]
            placeholders = ','.join('?' for _ in related)
            decisions = [dict(r) for r in conn.execute(
                f"SELECT run_id,sequence,payload FROM events WHERE run_id IN ({placeholders}) AND kind='user_answer' ORDER BY sequence", related)]
            events = [dict(r) for r in conn.execute("SELECT sequence,kind,payload FROM events WHERE run_id=? ORDER BY sequence", (run_id,))]
            memory = [dict(r) for r in conn.execute(
                f"SELECT run_id,kind,key,revision,value FROM memory WHERE run_id IN ({placeholders}) ORDER BY created", related)]
        for e in events:
            e["payload"] = json.loads(e["payload"])
        for m in memory:
            m["value"] = json.loads(m["value"])
        for decision in decisions:
            decision['payload'] = json.loads(decision['payload'])
        return {"run": run, "events": events, "memory": memory, "project_decisions": decisions}
