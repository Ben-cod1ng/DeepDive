"""SQLite repositories with short transactions and explicit research scopes."""
import hashlib
import json
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

def uid(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:20]}"

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()

class Store:
    def __init__(self, data):
        self.data = data
        data.mkdir(parents=True, exist_ok=True)
        self.path = data / "deepdive.sqlite3"
        self.lock = threading.RLock()
        self.observer = None
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS records (
                    kind TEXT NOT NULL, id TEXT PRIMARY KEY, scope TEXT NOT NULL,
                    created REAL NOT NULL, body TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS records_scope ON records(kind, scope);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    created REAL NOT NULL, body TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_run ON events(run_id, seq);
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY, scope TEXT NOT NULL, expires REAL NOT NULL,
                    body TEXT NOT NULL);
                CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(
                    id UNINDEXED, scope UNINDEXED, kind UNINDEXED, text);
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def put(self, kind, item, scope):
        item = dict(item)
        item.setdefault("id", uid(kind))
        item.setdefault("created", time.time())
        with self.lock, self.connection() as db:
            existing = db.execute("SELECT kind,scope,body FROM records WHERE id=?", (item["id"],)).fetchone()
            if existing and (existing["kind"] != kind or existing["scope"] != scope):
                raise ValueError("Record identity cannot change scope or type")
            if existing and kind in {"source", "passage"} and json.loads(existing["body"]) != item:
                raise ValueError("Evidence is immutable; create a new snapshot")
            db.execute("INSERT INTO records VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                       (kind, item["id"], scope, item["created"], json.dumps(item)))
            if kind in {"passage", "memory"}:
                db.execute("DELETE FROM search WHERE id=?", (item["id"],))
                db.execute("INSERT INTO search(id,scope,kind,text) VALUES(?,?,?,?)",
                           (item["id"], scope, kind, item.get("text", "")))
        return item

    def update(self, kind, record_id, **changes):
        with self.lock:
            with self.connection() as db:
                row = db.execute("SELECT scope,body FROM records WHERE id=? AND kind=?", (record_id, kind)).fetchone()
            if not row:
                raise KeyError(record_id)
            item = json.loads(row["body"])
            item.update(changes)
            return self.put(kind, item, row["scope"])

    def reserve_call(self, run_id, maximum):
        with self.lock:
            run = self.get("run", run_id)
            if run["calls"] >= maximum:
                return False
            self.update("run", run_id, calls=run["calls"] + 1)
            return True

    def add_tokens(self, run_id, count):
        with self.lock:
            self.update("run", run_id, tokens=self.get("run", run_id)["tokens"] + count)

    def delete_events(self, run_id):
        with self.connection() as db:
            db.execute("DELETE FROM events WHERE run_id=?", (run_id,))

    def get(self, kind, record_id, scope=None):
        with self.connection() as db:
            row = db.execute("SELECT * FROM records WHERE kind=? AND id=?", (kind, record_id)).fetchone()
        if not row or (scope is not None and row["scope"] != scope):
            return None
        return json.loads(row["body"])

    def list(self, kind, scope=None):
        sql, args = "SELECT body FROM records WHERE kind=?", [kind]
        if scope is not None:
            sql += " AND scope=?"
            args.append(scope)
        with self.connection() as db:
            rows = db.execute(sql + " ORDER BY created", args).fetchall()
        return [json.loads(r["body"]) for r in rows]

    def delete(self, kind, record_id, scope=None):
        with self.lock, self.connection() as db:
            row = db.execute("SELECT scope FROM records WHERE id=? AND kind=?", (record_id, kind)).fetchone()
            if not row or (scope is not None and scope != row["scope"]):
                return False
            db.execute("DELETE FROM records WHERE id=?", (record_id,))
            db.execute("DELETE FROM search WHERE id=?", (record_id,))
            db.execute("DELETE FROM cache WHERE scope=?", (row["scope"],))
        return True

    def event(self, run_id, event, **details):
        from .guardrails import redact
        body = {"event": event, **details}
        body = json.loads(redact(json.dumps(body, default=str)))
        with self.connection() as db:
            db.execute("INSERT INTO events(run_id,created,body) VALUES(?,?,?)",
                       (run_id, time.time(), json.dumps(body)))
        if self.observer:
            self.observer.emit(event, run_id, **details)

    def events(self, run_id, after=0):
        with self.connection() as db:
            rows = db.execute("SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq",
                              (run_id, after)).fetchall()
        return [{"seq": r["seq"], "created": r["created"], **json.loads(r["body"])} for r in rows]

    def search(self, scope, query, kind="passage", limit=8):
        words = re.findall(r"\w+", query, flags=re.UNICODE)[:20]
        if not words:
            return []
        expression = " OR ".join('"' + word.replace('"', '""') + '"' for word in words)
        with self.connection() as db:
            rows = db.execute("""SELECT id FROM search WHERE search MATCH ?
                AND scope=? AND kind=? ORDER BY rank LIMIT ?""", (expression, scope, kind, limit)).fetchall()
        return [item for row in rows if (item := self.get(kind, row["id"], scope))]

    def cached(self, scope, key):
        with self.connection() as db:
            row = db.execute("SELECT body FROM cache WHERE key=? AND scope=? AND expires>?",
                             (key, scope, time.time())).fetchone()
        return json.loads(row["body"]) if row else None

    def cache(self, scope, key, body, ttl):
        with self.connection() as db:
            db.execute("DELETE FROM cache WHERE expires<=?", (time.time(),))
            db.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?,?)",
                       (key, scope, time.time() + ttl, json.dumps(body)))
            db.execute("DELETE FROM cache WHERE key IN (SELECT key FROM cache ORDER BY expires DESC LIMIT -1 OFFSET 1000)")

    def clear_cache(self, scope):
        with self.connection() as db:
            db.execute("DELETE FROM cache WHERE scope=?", (scope,))
