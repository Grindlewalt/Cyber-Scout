"""Idempotency-Key for every mutating /app/v1 request (docs/SYNC.md): offline clients replay their outbox, and a
request whose answer was lost on a bad link must not be applied twice.

A signed-in POST/PUT/PATCH/DELETE with `Idempotency-Key: <1-128 chars of A-Z a-z 0-9 . _ : ->`:
  * first time: the request runs; its status + body are stored for (user, key) for 48 h;
  * same key again, same method + path + query + body: the stored response is replayed (header
    `Idempotent-Replayed: true`), nothing is applied again;
  * same key, different request: 422;
  * same key while the first is still running: waits up to ~10 s for it, then 429 + Retry-After (not 409, so an
    outbox never mistakes it for a picklist conflict; retry later and get the replay).
Not stored (so a retry runs again): 5xx and transient refusals (401, 403, 408, 423, 425, 429). Requests without
the header, without a session, and sign-in / sign-out / password / setup and /sync/bundle (a download) are
passed through unchanged. Runs inside the CSRF guard (main.py), so a replay still needs a valid CSRF header."""
import asyncio, hashlib, re, time

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import db as DB
from .config import PREFIX
from .deps import load_session

DB.add_schema("""
CREATE TABLE IF NOT EXISTS idempotency (
  user_id INTEGER NOT NULL,
  key TEXT NOT NULL,
  fingerprint TEXT NOT NULL,             -- sha256(method, path, query, body)
  method TEXT NOT NULL,
  path TEXT NOT NULL,
  status INTEGER,                        -- NULL while the first request is still running
  body BLOB,
  content_type TEXT,
  created_at REAL NOT NULL,
  PRIMARY KEY (user_id, key)
);
CREATE INDEX IF NOT EXISTS idempotency_at ON idempotency(created_at);
""")

RETENTION = 48 * 3600
STALE_PENDING = 300                      # a "running" row older than this is from a crashed request
WAIT_S = 10.0
MAX_STORED_BODY = 1 << 20
KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
SAFE = {"GET", "HEAD", "OPTIONS"}
# sign-in / out / password / setup set cookies (not replayed); the bundle is a download
EXCLUDE = {PREFIX + p for p in ("/sync/bundle", "/setup", "/auth/login", "/auth/logout", "/auth/password")}
NOT_STORED = {401, 403, 408, 423, 425, 429}
_last_purge = [0.0]


def _user_id(scope):
    c = DB.connect()
    try:
        u = load_session(c, Request(scope))
        return u.id if u else None
    finally:
        c.close()


def _claim(uid, key, fp, method, path):
    """-> ("new",) | ("replay", status, body, content_type) | ("mismatch",) | ("busy",)"""
    now = time.time()
    c = DB.connect()
    try:
        with DB.tx(c):
            if now - _last_purge[0] > 600:
                _last_purge[0] = now
                c.execute("DELETE FROM idempotency WHERE created_at < ?", (now - RETENTION,))
            r = c.execute("SELECT fingerprint, status, body, content_type, created_at FROM idempotency "
                          "WHERE user_id=? AND key=?", (uid, key)).fetchone()
            if r and (r["created_at"] < now - RETENTION or (r["status"] is None and r["created_at"] < now - STALE_PENDING)):
                c.execute("DELETE FROM idempotency WHERE user_id=? AND key=?", (uid, key))
                r = None
            if r is None:
                c.execute("INSERT INTO idempotency(user_id,key,fingerprint,method,path,created_at) VALUES(?,?,?,?,?,?)",
                          (uid, key, fp, method, path[:300], now))
                return ("new",)
            if r["fingerprint"] != fp:
                return ("mismatch",)
            if r["status"] is None:
                return ("busy",)
            return ("replay", r["status"], r["body"], r["content_type"])
    finally:
        c.close()


def _finish(uid, key, status, body, ctype):
    c = DB.connect()
    try:
        if status is None:
            c.execute("DELETE FROM idempotency WHERE user_id=? AND key=? AND status IS NULL", (uid, key))
        else:
            c.execute("UPDATE idempotency SET status=?, body=?, content_type=? WHERE user_id=? AND key=?",
                      (status, body, ctype, uid, key))
    finally:
        c.close()


class IdempotencyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in SAFE:
            return await self.app(scope, receive, send)
        path = scope["path"]
        if not path.startswith(PREFIX + "/") or path in EXCLUDE:
            return await self.app(scope, receive, send)
        key = None
        for k, v in scope.get("headers") or ():
            if k == b"idempotency-key":
                key = v.decode("latin-1").strip()
                break
        if key is None:
            return await self.app(scope, receive, send)
        if not KEY_RE.match(key):
            return await JSONResponse({"detail": "bad Idempotency-Key (1-128 characters: letters, digits, . _ : -)"},
                                      status_code=400)(scope, receive, send)
        # buffer the body: it is part of the fingerprint and is handed to the route unchanged
        chunks, more = [], True
        while more:
            msg = await receive()
            if msg["type"] != "http.request":
                return                                    # client went away
            chunks.append(msg.get("body", b""))
            more = msg.get("more_body", False)
        body = b"".join(chunks)
        sent = [False]

        async def replay_receive():
            if not sent[0]:
                sent[0] = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        uid = await run_in_threadpool(_user_id, scope)
        if uid is None:                                   # not signed in: the route answers 401 / 403 itself
            return await self.app(scope, replay_receive, send)
        h = hashlib.sha256()
        for part in (scope["method"].encode(), path.encode(), scope.get("query_string") or b"", body):
            h.update(len(part).to_bytes(8, "big"))
            h.update(part)
        fp = h.hexdigest()
        deadline = time.monotonic() + WAIT_S
        while True:
            st = await run_in_threadpool(_claim, uid, key, fp, scope["method"], path)
            if st[0] != "busy" or time.monotonic() > deadline:
                break
            await asyncio.sleep(0.25)
        if st[0] == "busy":
            return await JSONResponse({"detail": "a request with this Idempotency-Key is still being processed; retry"},
                                      status_code=429, headers={"Retry-After": "2"})(scope, receive, send)
        if st[0] == "mismatch":
            return await JSONResponse({"detail": "Idempotency-Key reused for a different request"},
                                      status_code=422)(scope, receive, send)
        if st[0] == "replay":
            _, status, rbody, ctype = st
            scope.setdefault("state", {})["audited"] = True     # nothing changed: no fallback audit entry
            hdrs = {"Idempotent-Replayed": "true"}
            resp = Response(content=rbody or b"", status_code=status, headers=hdrs,
                            media_type=ctype or "application/json")
            if status in (204, 304):
                resp = Response(status_code=status, headers=hdrs)
            return await resp(scope, receive, send)

        cap = {"status": None, "ctype": None, "body": [], "size": 0, "big": False}

        async def capture_send(msg):
            if msg["type"] == "http.response.start":
                cap["status"] = msg["status"]
                for k, v in msg.get("headers") or ():
                    if k.lower() == b"content-type":
                        cap["ctype"] = v.decode("latin-1")
            elif msg["type"] == "http.response.body" and not cap["big"]:
                b = msg.get("body", b"")
                cap["size"] += len(b)
                if cap["size"] > MAX_STORED_BODY:
                    cap["big"], cap["body"] = True, []
                else:
                    cap["body"].append(b)
            await send(msg)

        try:
            await self.app(scope, replay_receive, capture_send)
        except BaseException:
            await run_in_threadpool(_finish, uid, key, None, None, None)
            raise
        s = cap["status"]
        keep = s is not None and s < 500 and s not in NOT_STORED and not cap["big"]
        await run_in_threadpool(_finish, uid, key, s if keep else None, b"".join(cap["body"]) if keep else None,
                                cap["ctype"])
