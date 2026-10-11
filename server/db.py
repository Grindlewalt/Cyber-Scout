"""SQLite storage (DATA/app.db). Stdlib sqlite3, one short-lived connection per request, WAL mode.

Other routers (e.g. agent B's server/routers/sources.py) add their own tables with
`add_schema("CREATE TABLE IF NOT EXISTS ...")` at import time; it runs on startup, after the core schema.
"""
import json, os, sqlite3, time

from .config import cfg

CORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  display_name TEXT NOT NULL DEFAULT '',
  pw_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('viewer','scout','strategist','admin')),
  disabled INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  last_login REAL,
  pw_strength INTEGER NOT NULL DEFAULT 2   -- 0 no password, 1 short (member rules), 2 meets the admin rules
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,           -- sha256 of the cookie value; the raw token is never stored
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  csrf TEXT NOT NULL,
  created_at REAL NOT NULL,
  last_seen REAL NOT NULL,
  expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,                   -- JSON
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  key TEXT PRIMARY KEY,
  name TEXT,
  year INTEGER,
  start_date TEXT,
  added_at REAL NOT NULL,
  added_by INTEGER REFERENCES users(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS picklists (
  event TEXT NOT NULL,
  name TEXT NOT NULL,
  data TEXT NOT NULL,                    -- JSON object (client-defined; event.js uses {stars,dnp,weights})
  version INTEGER NOT NULL,
  updated_at REAL NOT NULL,
  updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  PRIMARY KEY (event, name)
);
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY,
  event TEXT NOT NULL,
  team TEXT,
  match TEXT,
  text TEXT NOT NULL,
  author_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS notes_ev ON notes(event, team, match);
CREATE TABLE IF NOT EXISTS override_log (
  id INTEGER PRIMARY KEY,
  event TEXT NOT NULL,
  match TEXT NOT NULL,
  data TEXT NOT NULL,                    -- JSON written for this match ({} = cleared)
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  at REAL NOT NULL
);
"""

_extra = []


def add_schema(sql):
    """Register idempotent DDL (CREATE TABLE IF NOT EXISTS ...) for a feature router; runs at startup."""
    _extra.append(sql)


def connect():
    c = sqlite3.connect(cfg.DB_PATH, timeout=10, isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=10000")
    return c


def init():
    os.makedirs(os.path.dirname(cfg.DB_PATH), exist_ok=True)
    c = connect()
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(CORE_SCHEMA)
        if "pw_strength" not in {r[1] for r in c.execute("PRAGMA table_info(users)")}:
            # older DBs: every existing password was set under the admin-grade rule
            c.execute("ALTER TABLE users ADD COLUMN pw_strength INTEGER NOT NULL DEFAULT 2")
        for sql in _extra:
            c.executescript(sql)
    finally:
        c.close()
    try:
        os.chmod(cfg.DB_PATH, 0o600)
    except OSError:
        pass


def get_db():
    """FastAPI dependency: a connection for the duration of the request."""
    c = connect()
    try:
        yield c
    finally:
        c.close()


class tx:
    """`with tx(db): ...` -> BEGIN IMMEDIATE / COMMIT (or ROLLBACK on error)."""
    def __init__(self, db): self.db = db
    def __enter__(self): self.db.execute("BEGIN IMMEDIATE"); return self.db
    def __exit__(self, et, e, tb):
        self.db.execute("ROLLBACK" if et else "COMMIT")
        return False


# ---- settings (JSON values)
def get_setting(db, key, default=None):
    r = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def set_setting(db, key, value):
    if value is None:
        db.execute("DELETE FROM settings WHERE key=?", (key,))
    else:
        db.execute("INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                   (key, json.dumps(value), time.time()))


def user_count(db):
    return db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
