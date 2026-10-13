"""Durable local state: config, server session (0600), outbox + GET cache (sqlite), per-event sync state."""
import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
from urllib.parse import urlsplit

from .util import ensure_dir, read_json, write_json

MEDIA_MODES = ("none", "proxy", "full")


def normalize_server(url) -> str:
    """'scout.example.dev' -> 'https://scout.example.dev'; strips the path; rejects credentials / odd schemes."""
    if not isinstance(url, str):
        raise ValueError("server address must be text")
    url = url.strip()
    if not url:
        raise ValueError("server address is empty")
    if "://" not in url:
        url = "https://" + url
    u = urlsplit(url)
    if u.scheme not in ("http", "https"):
        raise ValueError("server address must start with http:// or https://")
    if not u.hostname:
        raise ValueError("server address has no host name")
    if u.username or u.password:
        raise ValueError("server address must not contain a user name or password")
    if u.path not in ("", "/") or u.query or u.fragment:
        raise ValueError("use just the server address (e.g. https://scout.example.dev), without a path")
    try:
        port = u.port
    except ValueError:
        raise ValueError("bad port in server address")
    host = u.hostname if ":" not in u.hostname else "[" + u.hostname + "]"
    return f"{u.scheme}://{host}" + (f":{port}" if port else "")


class Config:
    """config.json (0600): server, media mode, followed events, intervals, per-install secrets."""
    DEFAULTS = {"server": None, "media": "none", "events": None, "interval": 60, "probe_interval": 15,
                "ca_file": None}

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "config.json")
        self.lock = threading.RLock()
        d = read_json(self.path, {}) or {}
        self.data = dict(self.DEFAULTS)
        self.data.update({k: v for k, v in d.items()})
        changed = False
        for k in ("token", "local_csrf"):          # per-install random secrets (control-API cookie, page CSRF)
            if not isinstance(self.data.get(k), str) or len(self.data[k]) < 32:
                self.data[k] = secrets.token_urlsafe(32)
                changed = True
        if changed or not os.path.exists(self.path):
            self.save()

    def __getitem__(self, k):
        with self.lock:
            return self.data.get(k)

    def get(self, k, default=None):
        with self.lock:
            return self.data.get(k, default)

    def save(self):
        with self.lock:
            write_json(self.path, self.data, mode=0o600)

    def update(self, **kv):
        with self.lock:
            self.data.update(kv)
            self.save()

    def public(self) -> dict:
        with self.lock:
            return {k: self.data.get(k) for k in ("server", "media", "events", "interval")}


class SessionStore:
    """session.json (0600): the server's cookies + CSRF token + the last /session answer. Never sent to the page."""

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "session.json")
        self.lock = threading.RLock()
        self.d = read_json(self.path, {}) or {}
        self.d.setdefault("cookies", {})
        self.gen = 0            # bumped by every sign-in / sign-out / server change (see absorb_session_json)

    def save(self):
        with self.lock:
            write_json(self.path, self.d, mode=0o600)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    @property
    def server(self):
        return self.d.get("server")

    @property
    def user(self):
        s = self.d.get("session") or {}
        return s.get("user")

    @property
    def csrf(self) -> str:
        # the server requires X-CSRF-Token == the cs_csrf cookie (== the session's token once signed in)
        return self.d["cookies"].get("cs_csrf") or self.d.get("csrf") or ""

    def cookie_header(self) -> str:
        with self.lock:
            return "; ".join(f"{k}={v}" for k, v in sorted(self.d["cookies"].items()) if v)

    def absorb_set_cookies(self, values, gen=None):
        """Apply Set-Cookie header values from the server to the jar (only the Cyber-Scout cookies). `gen`: as in
        absorb_session_json (an answer to a request sent before a sign-in/out must not change the new session's
        cookies, e.g. an anonymous GET /session handing out a fresh cs_csrf)."""
        changed = False
        with self.lock:
            if gen is not None and gen != self.gen:
                return False
            for sc in values or ():
                first, _, attrs = sc.partition(";")
                name, _, val = first.strip().partition("=")
                name = name.strip()
                if name not in ("cs_session", "cs_csrf"):
                    continue
                val = val.strip().strip('"')
                a = attrs.lower().replace(" ", "")
                expired = "max-age=0" in a or "max-age=-" in a or not val or "expires=thu,01jan1970" in a
                if expired:
                    if name in self.d["cookies"]:
                        self.d["cookies"].pop(name, None)
                        changed = True
                elif self.d["cookies"].get(name) != val:
                    self.d["cookies"][name] = val
                    changed = True
            if changed:
                self.save()
        return changed

    def absorb_session_json(self, body: dict, gen=None):
        """A SessionOut answer (session/login/setup/profile): remember user + csrf. `gen` = self.gen when the request
        was sent: a GET /session that started before a sign-in (e.g. a background sync's 401 check) and answers after
        it must not replace the new session with its older, signed-out view."""
        if not isinstance(body, dict) or "csrf" not in body:
            return
        with self.lock:
            if gen is not None and gen != self.gen:
                return
            self.gen += 1
            self.d["session"] = body
            if body.get("csrf"):
                self.d["csrf"] = body["csrf"]
            self.save()

    def set_server(self, server):
        with self.lock:
            if self.d.get("server") != server:
                self.gen += 1
                self.d = {"server": server, "cookies": {}}
                self.save()

    def clear(self):
        with self.lock:
            self.gen += 1
            self.d = {"server": self.d.get("server"), "cookies": {}}
            self.save()


class LocalDB:
    """outbox + GET cache in one sqlite file (WAL, synchronous=FULL: a queued change survives a crash)."""

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "client.sqlite3")
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS outbox(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            method TEXT NOT NULL, path TEXT NOT NULL, ctype TEXT, body BLOB,
            created REAL NOT NULL, updated REAL,
            state TEXT NOT NULL DEFAULT 'queued',      -- queued | sending | sent | conflict | failed
            attempts INTEGER NOT NULL DEFAULT 0, status INTEGER, response BLOB, error TEXT, server TEXT);
        CREATE TABLE IF NOT EXISTS getcache(
            path TEXT PRIMARY KEY, status INTEGER, ctype TEXT, body BLOB, stored REAL);
        """)
        # a crash while sending: the request may or may not have reached the server -> resend with the same key
        self.db.execute("UPDATE outbox SET state='queued' WHERE state='sending'")

    # ---- outbox
    def enqueue(self, method, path, ctype, body: bytes, server, key=None) -> dict:
        key = key or str(uuid.uuid4())
        with self.lock:
            cur = self.db.execute("INSERT INTO outbox(key,method,path,ctype,body,created,updated,server) "
                                  "VALUES(?,?,?,?,?,?,?,?)", (key, method, path, ctype, body, time.time(),
                                                              time.time(), server))
            return {"id": cur.lastrowid, "key": key}

    def next_queued(self, after_id=0):
        with self.lock:
            return self.db.execute("SELECT * FROM outbox WHERE state='queued' AND id>? ORDER BY id LIMIT 1",
                                   (after_id,)).fetchone()

    def set_state(self, oid, state, status=None, response=None, error=None, bump=False):
        with self.lock:
            self.db.execute("UPDATE outbox SET state=?, status=COALESCE(?,status), response=COALESCE(?,response), "
                            "error=?, updated=?, attempts=attempts+? WHERE id=?",
                            (state, status, response, error, time.time(), 1 if bump else 0, oid))

    def get(self, oid):
        with self.lock:
            return self.db.execute("SELECT * FROM outbox WHERE id=?", (oid,)).fetchone()

    def delete(self, oid) -> bool:
        with self.lock:
            return self.db.execute("DELETE FROM outbox WHERE id=? AND state<>'sending'", (oid,)).rowcount > 0

    def count(self, state="queued") -> int:
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM outbox WHERE state=?", (state,)).fetchone()[0]

    def pending(self) -> int:
        """Changes not yet accepted by the server (queued or being sent right now)."""
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM outbox WHERE state IN ('queued','sending')").fetchone()[0]

    def items(self):
        with self.lock:
            rows = self.db.execute("SELECT * FROM outbox WHERE state<>'sent' ORDER BY id").fetchall()
        out = []
        for r in rows:
            resp = None
            if r["response"]:
                try:
                    resp = json.loads(bytes(r["response"]).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    resp = None
            body = None
            if r["body"]:
                try:
                    body = json.loads(bytes(r["body"]).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    body = None
            out.append({"id": r["id"], "key": r["key"], "method": r["method"], "path": r["path"],
                        "created": r["created"], "updated": r["updated"], "state": r["state"],
                        "attempts": r["attempts"], "status": r["status"], "error": r["error"],
                        "body": body, "response": resp})
        return out

    def prune_sent(self, older_than_s=7 * 86400):
        with self.lock:
            self.db.execute("DELETE FROM outbox WHERE state='sent' AND updated<?", (time.time() - older_than_s,))

    # ---- GET cache for /app/v1 reads (served when offline)
    def cache_put(self, path, status, ctype, body: bytes):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO getcache(path,status,ctype,body,stored) VALUES(?,?,?,?,?)",
                            (path, status, ctype, body, time.time()))

    def cache_get(self, path):
        with self.lock:
            return self.db.execute("SELECT * FROM getcache WHERE path=?", (path,)).fetchone()

    def cache_clear(self):
        with self.lock:
            self.db.execute("DELETE FROM getcache")


class EventState:
    """state/<ev>.json: manifest cursor, last sync time and the index of verified local files {path: [sha256,size]}."""

    def __init__(self, state_dir: str, ev: str):
        self.path = os.path.join(state_dir, ev + ".json")
        d = read_json(self.path, {}) or {}
        self.ev = ev
        self.cursor = d.get("cursor")
        self.synced_at = d.get("synced_at")
        self.files = d.get("files") or {}
        self.media = d.get("media") or {}
        self.server = d.get("server")
        self.name = d.get("name")

    def save(self):
        write_json(self.path, {"cursor": self.cursor, "synced_at": self.synced_at, "files": self.files,
                               "media": self.media, "server": self.server, "name": self.name})

    def summary(self) -> dict:
        return {"key": self.ev, "name": self.name, "synced_at": self.synced_at, "files": len(self.files),
                "bytes": sum(v[1] for v in self.files.values()),
                "media_bytes": sum(v[1] for v in self.media.values())}


def list_event_states(state_dir: str):
    ensure_dir(state_dir)
    out = []
    for f in sorted(os.listdir(state_dir)):
        if f.endswith(".json") and not f.startswith((".", "_")):
            out.append(EventState(state_dir, f[:-5]))
    return out
