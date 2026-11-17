"""SQLite store (one file in the data folder). Small, synchronous, guarded by a lock; JSON columns end in _json."""
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value_json TEXT);
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, instructions TEXT DEFAULT '', folders_json TEXT DEFAULT '[]',
  default_model TEXT, default_harness TEXT, connectors_json TEXT DEFAULT '[]', memory_enabled INTEGER DEFAULT 1,
  archived INTEGER DEFAULT 0, created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS chats (
  id TEXT PRIMARY KEY, project_id TEXT, title TEXT, model TEXT, harness TEXT, connectors_json TEXT DEFAULT '[]',
  folders_json TEXT DEFAULT '[]', thinking INTEGER DEFAULT 0, summary TEXT DEFAULT '', summary_upto TEXT,
  origin TEXT DEFAULT 'user', schedule_id TEXT, pinned INTEGER DEFAULT 0, archived INTEGER DEFAULT 0,
  learned_upto TEXT, created TEXT, updated TEXT);
CREATE INDEX IF NOT EXISTS chats_project ON chats(project_id, updated);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT DEFAULT '', meta_json TEXT DEFAULT '{}',
  created TEXT);
CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat_id, created);
CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY, scope TEXT NOT NULL, scope_id TEXT DEFAULT '', kind TEXT DEFAULT 'fact', text TEXT NOT NULL,
  source TEXT DEFAULT 'learned', chat_id TEXT, pinned INTEGER DEFAULT 0, uses INTEGER DEFAULT 0, created TEXT, updated TEXT);
CREATE INDEX IF NOT EXISTS memories_scope ON memories(scope, scope_id);
CREATE TABLE IF NOT EXISTS models (
  id TEXT PRIMARY KEY, name TEXT, repo TEXT, files_json TEXT DEFAULT '[]', path TEXT, size INTEGER, sha256 TEXT,
  quant TEXT, meta_json TEXT DEFAULT '{}', settings_json TEXT DEFAULT '{}', bench_json TEXT DEFAULT '{}',
  added TEXT, last_used TEXT);
CREATE TABLE IF NOT EXISTS connectors (
  id TEXT PRIMARY KEY, name TEXT, transport TEXT, url TEXT, command TEXT, args_json TEXT DEFAULT '[]',
  env_json TEXT DEFAULT '{}', auth TEXT DEFAULT 'none', approval TEXT DEFAULT 'changes', enabled INTEGER DEFAULT 1,
  description TEXT DEFAULT '', tools_json TEXT DEFAULT '[]', created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, kind TEXT, title TEXT, status TEXT, chat_id TEXT, schedule_id TEXT, harness TEXT, model TEXT,
  payload_json TEXT DEFAULT '{}', result_json TEXT DEFAULT '{}', error TEXT, created TEXT, started TEXT, finished TEXT);
CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created);
CREATE TABLE IF NOT EXISTS schedules (
  id TEXT PRIMARY KEY, name TEXT, enabled INTEGER DEFAULT 1, cron TEXT, prompt TEXT DEFAULT '', harness TEXT,
  model TEXT, project_id TEXT, inputs_json TEXT DEFAULT '{}', chat_mode TEXT DEFAULT 'new', chat_id TEXT,
  connectors_json TEXT DEFAULT '[]', auto_approve INTEGER DEFAULT 0, catch_up INTEGER DEFAULT 1,
  only_on_ac INTEGER DEFAULT 0, max_runs_per_day INTEGER DEFAULT 0, last_run TEXT, next_run TEXT,
  created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS skill_suggestions (
  id TEXT PRIMARY KEY, skill TEXT, suggestion TEXT, chat_id TEXT, status TEXT DEFAULT 'open', created TEXT);
CREATE TABLE IF NOT EXISTS plugin_state (
  id TEXT PRIMARY KEY, enabled INTEGER DEFAULT 1, settings_json TEXT DEFAULT '{}', setup_json TEXT DEFAULT '{}',
  updated TEXT);
CREATE TABLE IF NOT EXISTS item_state (
  kind TEXT, id TEXT, enabled INTEGER DEFAULT 1, PRIMARY KEY (kind, id));
CREATE TABLE IF NOT EXISTS downloads (
  id TEXT PRIMARY KEY, spec_json TEXT DEFAULT '{}', state TEXT, error TEXT, created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS cloud_models (
  id TEXT PRIMARY KEY, provider TEXT NOT NULL, remote_id TEXT NOT NULL, name TEXT, enabled INTEGER DEFAULT 1,
  caps_json TEXT DEFAULT '{}', price_json TEXT DEFAULT '{}', settings_json TEXT DEFAULT '{}', added TEXT, last_used TEXT);
CREATE TABLE IF NOT EXISTS usage (
  id TEXT PRIMARY KEY, created TEXT, month TEXT, model_id TEXT, provider TEXT, chat_id TEXT, job_id TEXT, purpose TEXT,
  input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0, cost REAL DEFAULT 0, estimated INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS usage_month ON usage(month);
CREATE INDEX IF NOT EXISTS usage_chat ON usage(chat_id);
CREATE TABLE IF NOT EXISTS kb_docs (
  id TEXT PRIMARY KEY, project_id TEXT, name TEXT, path TEXT, size INTEGER DEFAULT 0, status TEXT, error TEXT,
  chunks INTEGER DEFAULT 0, chars INTEGER DEFAULT 0, added TEXT, updated TEXT);
CREATE INDEX IF NOT EXISTS kb_docs_project ON kb_docs(project_id);
CREATE TABLE IF NOT EXISTS kb_chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT, project_id TEXT, seq INTEGER, text TEXT, vec BLOB);
CREATE INDEX IF NOT EXISTS kb_chunks_doc ON kb_chunks(doc_id);
CREATE INDEX IF NOT EXISTS kb_chunks_project ON kb_chunks(project_id);
CREATE TABLE IF NOT EXISTS msg_vec (message_id TEXT PRIMARY KEY, chat_id TEXT, vec BLOB);
CREATE INDEX IF NOT EXISTS msg_vec_chat ON msg_vec(chat_id);
CREATE TABLE IF NOT EXISTS mem_vec (memory_id TEXT PRIMARY KEY, text TEXT, vec BLOB);
CREATE TABLE IF NOT EXISTS step_stats (
  key TEXT PRIMARY KEY, harness TEXT, model_id TEXT, runs INTEGER DEFAULT 0, ok INTEGER DEFAULT 0,
  calls REAL DEFAULT 0, fixed REAL DEFAULT 0, growth REAL DEFAULT 0, output REAL DEFAULT 0, seconds REAL DEFAULT 0,
  read_tps REAL DEFAULT 0, write_tps REAL DEFAULT 0, updated TEXT);
"""

# Full-text search (SQLite FTS5) over messages and knowledge passages, kept in step by triggers. If this SQLite has
# no FTS5, search falls back to LIKE (slower, no ranking).
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS msg_fts USING fts5(content, content='messages', content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS msg_fts_ai AFTER INSERT ON messages BEGIN
  INSERT INTO msg_fts(rowid, content) VALUES (new.rowid, new.content); END;
CREATE TRIGGER IF NOT EXISTS msg_fts_ad AFTER DELETE ON messages BEGIN
  INSERT INTO msg_fts(msg_fts, rowid, content) VALUES ('delete', old.rowid, old.content); END;
CREATE TRIGGER IF NOT EXISTS msg_fts_au AFTER UPDATE OF content ON messages BEGIN
  INSERT INTO msg_fts(msg_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
  INSERT INTO msg_fts(rowid, content) VALUES (new.rowid, new.content); END;
CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(text, content='kb_chunks', content_rowid='id',
  tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS kb_fts_ai AFTER INSERT ON kb_chunks BEGIN
  INSERT INTO kb_fts(rowid, text) VALUES (new.id, new.text); END;
CREATE TRIGGER IF NOT EXISTS kb_fts_ad AFTER DELETE ON kb_chunks BEGIN
  INSERT INTO kb_fts(kb_fts, rowid, text) VALUES ('delete', old.id, old.text); END;
"""

# Columns added after the first release: (table, column, definition). Applied once, on start.
MIGRATIONS = [
    ("chats", "settings_json", "TEXT DEFAULT '{}'"),        # per-chat limits (reply length, steps, time…)
    ("chats", "parent_chat_id", "TEXT"),                   # agents started by another chat (teams)
    ("chats", "agent_name", "TEXT"),
    ("projects", "notes", "TEXT DEFAULT ''"),               # living project notes (2.4.0)
    ("projects", "memory_mode", "TEXT DEFAULT 'shared'"),   # shared | isolated (2.4.0)
]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")  # ms: keeps message order exact


def new_id(prefix=""):
    return prefix + uuid.uuid4().hex[:16]


class DB:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.executescript(SCHEMA)
            for table, column, definition in MIGRATIONS:
                have = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}
                if column not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            self.fts = self._setup_fts()

    def _setup_fts(self):
        try:
            new = not self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='msg_fts'").fetchone()
            self.conn.executescript(FTS_SCHEMA)
            if new:     # index what is already there (chats from before 2.4.0)
                self.conn.execute("INSERT INTO msg_fts(msg_fts) VALUES ('rebuild')")
                self.conn.execute("INSERT INTO kb_fts(kb_fts) VALUES ('rebuild')")
            return True
        except sqlite3.OperationalError:
            return False

    @staticmethod
    def _row(row):
        out = {}
        for key in row.keys():
            value = row[key]
            if key.endswith("_json"):
                out[key[:-5]] = json.loads(value) if value else None
            else:
                out[key] = value
        return out

    @staticmethod
    def _params(values):
        cols, params = [], []
        for key, value in values.items():
            if isinstance(value, (dict, list)) or (key.endswith("_json")):
                key = key if key.endswith("_json") else key + "_json"
                value = json.dumps(value, ensure_ascii=False, default=str)
            cols.append(key)
            params.append(value)
        return cols, params

    def all(self, sql, params=()):
        with self.lock:
            return [self._row(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        with self.lock:
            row = self.conn.execute(sql, params).fetchone()
        return self._row(row) if row else None

    def run(self, sql, params=()):
        with self.lock:
            return self.conn.execute(sql, params)

    def insert(self, table, values):
        cols, params = self._params(values)
        with self.lock:
            self.conn.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", params)
        return values.get("id")

    def update(self, table, row_id, values, key="id"):
        if not values:
            return
        cols, params = self._params(values)
        with self.lock:
            self.conn.execute(f"UPDATE {table} SET {','.join(c + '=?' for c in cols)} WHERE {key}=?", [*params, row_id])

    def get(self, table, row_id, key="id"):
        return self.one(f"SELECT * FROM {table} WHERE {key}=?", (row_id,))

    def delete(self, table, row_id, key="id"):
        with self.lock:
            self.conn.execute(f"DELETE FROM {table} WHERE {key}=?", (row_id,))

    # settings ------------------------------------------------------------
    def setting(self, key, default=None):
        row = self.one("SELECT value_json FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    def set_setting(self, key, value):
        with self.lock:
            self.conn.execute("INSERT INTO settings(key, value_json) VALUES(?, ?) "
                              "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                              (key, json.dumps(value, ensure_ascii=False)))

    def item_enabled(self, kind, item_id, default=True):
        row = self.one("SELECT enabled FROM item_state WHERE kind=? AND id=?", (kind, item_id))
        return bool(row["enabled"]) if row else default

    def set_item_enabled(self, kind, item_id, enabled):
        with self.lock:
            self.conn.execute("INSERT INTO item_state(kind, id, enabled) VALUES(?,?,?) "
                              "ON CONFLICT(kind, id) DO UPDATE SET enabled=excluded.enabled",
                              (kind, item_id, int(bool(enabled))))
