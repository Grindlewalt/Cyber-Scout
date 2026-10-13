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
-- every saved picklist version (picklists holds only the current one); restoring = saving an old one again
CREATE TABLE IF NOT EXISTS picklist_versions (
  event TEXT NOT NULL,
  name TEXT NOT NULL,
  version INTEGER NOT NULL,
  data TEXT NOT NULL,
  updated_at REAL NOT NULL,
  updated_by INTEGER,                    -- no FK: history outlives deleted accounts
  note TEXT,                             -- e.g. "restored v3", "deleted"
  PRIMARY KEY (event, name, version)
);
-- identity fixes proposed by scouts, applied when a strategist/admin approves (setting scout_overrides=review)
CREATE TABLE IF NOT EXISTS override_requests (
  id INTEGER PRIMARY KEY,
  event TEXT NOT NULL,
  match TEXT NOT NULL,
  data TEXT NOT NULL,                    -- the requested entry for this match ({} = clear it)
  base TEXT NOT NULL,                    -- the entry when it was requested
  status TEXT NOT NULL CHECK (status IN ('pending','approved','rejected','withdrawn','superseded')),
  user_id INTEGER,
  created_at REAL NOT NULL,
  decided_by INTEGER,
  decided_at REAL
);
CREATE INDEX IF NOT EXISTS override_requests_ev ON override_requests(event, status);
-- append-only record of every change (triggers below refuse UPDATE/DELETE); admins list it and revert from it
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY,
  at REAL NOT NULL,
  user_id INTEGER,                       -- no FK: the record outlives the account
  username TEXT,
  display_name TEXT,
  role TEXT,
  ip TEXT,
  via TEXT,                              -- 'lan' (direct, private/Tailscale address) | 'public' (tunnel / internet)
  ua TEXT,
  action TEXT NOT NULL,                  -- family.verb, e.g. picklist.save, note.hide, override.set, settings.update
  event TEXT,
  target TEXT,
  before TEXT,                           -- JSON state before (null = did not exist)
  after TEXT,                            -- JSON state after
  ref INTEGER                            -- for *.revert: the entry that was reverted
);
CREATE INDEX IF NOT EXISTS audit_at ON audit_log(at);
CREATE INDEX IF NOT EXISTS audit_user ON audit_log(user_id, id);
CREATE INDEX IF NOT EXISTS audit_ref ON audit_log(ref);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
  BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
  BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
"""

# columns added after the first release: (table, column, DDL); added in place on startup (idempotent)
MIGRATIONS = [
    # older DBs: every existing password was set under the admin-grade rule
    ("users", "pw_strength", "ALTER TABLE users ADD COLUMN pw_strength INTEGER NOT NULL DEFAULT 2"),
    # 1 = signed in without a password: only valid from the trusted network (setting passwordless_login)
    ("sessions", "net_locked", "ALTER TABLE sessions ADD COLUMN net_locked INTEGER NOT NULL DEFAULT 0"),
    # soft delete / hide: removed notes stay in the DB (restorable), hidden from lists
    ("notes", "removed_at", "ALTER TABLE notes ADD COLUMN removed_at REAL"),
    ("notes", "removed_by", "ALTER TABLE notes ADD COLUMN removed_by INTEGER"),
    ("notes", "removed_kind", "ALTER TABLE notes ADD COLUMN removed_kind TEXT"),     # deleted | hidden | reverted
    ("notes", "edited_by", "ALTER TABLE notes ADD COLUMN edited_by INTEGER"),
    ("override_log", "requested_by", "ALTER TABLE override_log ADD COLUMN requested_by INTEGER"),
]

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
        for table, col, ddl in MIGRATIONS:
            if col not in {r[1] for r in c.execute(f"PRAGMA table_info({table})")}:
                c.execute(ddl)
        # picklists saved before version history existed: keep their current version as history
        c.execute("INSERT OR IGNORE INTO picklist_versions(event,name,version,data,updated_at,updated_by) "
                  "SELECT event,name,version,data,updated_at,updated_by FROM picklists")
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
