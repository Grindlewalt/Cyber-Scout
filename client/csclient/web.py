"""The local HTTP server: the web app (mirror or bundled copy), /api + /media from the data mirror, /app/v1 proxied to
the server (session + CSRF handled here, never visible to the page; writes go through the outbox), and the local
control API /client/v1/* (docs/SYNC.md section 2)."""
import email.utils
import hmac
import http.server
import json
import logging
import mimetypes
import os
import re
import socket
import threading
import time
import uuid
from urllib.parse import quote, unquote, urlsplit

from . import VERSION
from .install import NotInstalled
from .net import MUTATING, Conn, HTTPError, NetError, detail_text
from .store import MEDIA_MODES, normalize_server
from .util import safe_relpath, target_path

log = logging.getLogger("cyberscout.http")

TOKEN_COOKIE = "cs_client"
CSRF_COOKIE = "cs_csrf"
COOKIE_AGE = 10 * 365 * 86400
MAX_BODY = 25 * 1024 * 1024
MAX_QUEUED_BODY = 2 * 1024 * 1024
WRITE_WAIT_S = 20
EVENT_RE = re.compile(r"^[0-9]{4}[a-z0-9]{1,16}$")
KEY_MAX = 64                                     # a connection key, e.g. 7KQ2-M9XA-P4HD-W8NE (the server checks it)
# writes whose answer is the point (an admin's "Make key": the key is only in the answer): straight to the server, never
# queued (offline: 503)
DIRECT_RE = re.compile(r"^/users/[^/]+/connect-key$")

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' data: https://fonts.gstatic.com; img-src 'self' data: blob: https://i.ytimg.com "
       "https://img.youtube.com; media-src 'self' blob:; connect-src 'self'; frame-src https://www.youtube.com "
       "https://www.youtube-nocookie.com; worker-src 'self' blob:; object-src 'none'; base-uri 'self'; "
       "form-action 'self'; frame-ancestors 'self'")
CSP_DATA = "default-src 'none'; media-src 'self'; frame-ancestors 'self'"
CSP_API = "default-src 'none'; frame-ancestors 'none'"

TYPES = {".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8", ".json": "application/json",
         ".webmanifest": "application/manifest+json", ".mp4": "video/mp4", ".webm": "video/webm",
         ".woff2": "font/woff2", ".woff": "font/woff", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon", ".webp": "image/webp",
         ".txt": "text/plain; charset=utf-8", ".gz": "application/gzip"}
# answer headers of the server worth passing to the page
PASS_HEADERS = ("content-type", "cache-control", "etag", "last-modified", "retry-after", "content-range",
                "accept-ranges", "x-cs-role", "location")
# page headers worth passing to the server
FWD_HEADERS = ("content-type", "accept", "if-none-match", "if-match", "if-modified-since", "range", "idempotency-key")


def ctype_for(path):
    ext = os.path.splitext(path)[1].lower()
    return TYPES.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"


def consteq(a, b):
    return isinstance(a, str) and isinstance(b, str) and hmac.compare_digest(a.encode(), b.encode())


class LocalHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"     # Windows: SO_REUSEADDR would let a second copy bind the same port
    app = None


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "CyberScoutClient/" + VERSION
    protocol_version = "HTTP/1.1"

    def version_string(self):
        return self.server_version

    # ------------------------------------------------------------------------------------------------ plumbing
    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)

    def log_request(self, code="-", size="-"):
        log.debug('%s "%s" %s', self.address_string(), self.requestline, code)

    def do_GET(self): self.dispatch()
    def do_HEAD(self): self.dispatch()
    def do_POST(self): self.dispatch()
    def do_PUT(self): self.dispatch()
    def do_PATCH(self): self.dispatch()
    def do_DELETE(self): self.dispatch()

    def do_OPTIONS(self):           # no CORS, ever
        self.send_json(405, {"detail": "method not allowed"})

    def send_json(self, status, obj, headers=None, cookies=(), csp=CSP_API):
        body = b"" if status in (204, 304) else json.dumps(obj).encode()
        self.send_response(status)
        if status not in (204, 304):
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._common(csp)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        for c in cookies:
            self.send_header("Set-Cookie", c)
        self.end_headers()
        if self.command != "HEAD" and body:
            self._write(body)

    def _common(self, csp):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", csp)
        self.send_header("X-Frame-Options", "SAMEORIGIN" if csp in (CSP, CSP_DATA) else "DENY")
        self.send_header("X-Cyber-Scout-Client", VERSION)

    def _write(self, data):
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.timeout):
            self.close_connection = True

    def read_body(self):
        if self._body is not None:                 # read once per request (dispatch drains what nobody read)
            return self._body
        n = self.headers.get("Content-Length")
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            self.close_connection = True           # can't tell where the next request starts
            raise ValueError("chunked request bodies are not supported")
        n = int(n) if n and n.isdigit() else 0
        if n > MAX_BODY:
            self.close_connection = True
            raise ValueError("request body too large")
        self._body = self.rfile.read(n) if n else b""
        return self._body

    def _drain(self):
        """A keep-alive connection carries the next request right after this one's body: a handler that answered
        without reading the body (POST /client/v1/logout {}, a 405, ...) must not leave it to be parsed as the next
        request line ("Unsupported method ('{}GET')", CLIENT-9)."""
        if self._body is not None or self.close_connection:
            return
        try:
            self.read_body()
        except ValueError:
            self.close_connection = True
        except OSError:
            self.close_connection = True

    def json_body(self):
        b = self.read_body()
        if not b:
            return {}
        d = json.loads(b.decode("utf-8"))
        if not isinstance(d, dict):
            raise ValueError("expected a JSON object")
        return d

    # ------------------------------------------------------------------------------------------------ security
    def host_ok(self):
        allowed = self.app.allowed_hosts
        if allowed is None:
            return True
        return (self.headers.get("Host") or "").lower() in allowed

    def origin_ok(self):
        o = self.headers.get("Origin")
        if o is not None and o.lower() not in self.app.allowed_origins:
            return False
        sfs = self.headers.get("Sec-Fetch-Site")
        if sfs and sfs.lower() not in ("same-origin", "none"):
            return False
        return True

    def cookie(self, name):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return None

    def token_ok(self):
        return consteq(self.cookie(TOKEN_COOKIE), self.app.config["token"])

    def guard(self):
        """Control API, /app/v1, /api and /media: only the page this client served (token cookie, SameSite=Strict,
        HttpOnly) from this client's own origin."""
        if not self.origin_ok():
            self.send_json(403, {"detail": "cross-site request refused"})
            return False
        if not self.token_ok():
            self.send_json(403, {"detail": "open Cyber-Scout from the client's own address "
                                           f"({self.app.url}) to use it"})
            return False
        return True

    def page_cookies(self):
        cfg = self.app.config
        return [f"{TOKEN_COOKIE}={cfg['token']}; Path=/; Max-Age={COOKIE_AGE}; HttpOnly; SameSite=Strict",
                f"{CSRF_COOKIE}={cfg['local_csrf']}; Path=/; Max-Age={COOKIE_AGE}; SameSite=Strict"]

    # ------------------------------------------------------------------------------------------------ routing
    _body = None

    def dispatch(self):
        self._body = None
        try:
            self._route()
        finally:
            self._drain()

    def _route(self):
        try:
            if not self.host_ok():
                return self.send_json(403, {"detail": "unknown Host (use http://127.0.0.1:%d/)" % self.app.port})
            path, _, query = self.path.partition("?")
            if path.startswith(("http://", "https://")):
                return self.send_json(400, {"detail": "proxy requests are not supported"})
            upath = unquote(path)
            if ".." in upath.split("/") or "\\" in upath or "\x00" in upath:
                return self.send_json(400, {"detail": "bad path"})
            if upath.startswith("/client/v1/") or upath == "/client/v1":
                if not self.guard():
                    return
                return self.control(upath[len("/client/v1/"):].strip("/"))
            if upath.startswith("/app/v1/") or upath == "/app/v1":
                if not self.guard():
                    return
                return self.proxy(path, query)
            if upath.startswith(("/api/", "/media/")):
                if not self.guard():
                    return
                return self.site(upath)
            if self.command not in ("GET", "HEAD"):
                return self.send_json(405, {"detail": "method not allowed"})
            if upath in ("/client/processing", "/client/processing.js"):
                return self.proc_page(upath)
            return self.shell(upath)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except Exception as e:
            log.exception("request failed: %s %s", self.command, self.path)
            try:
                self.send_json(500, {"detail": f"client error: {e.__class__.__name__}"})
            except Exception:
                self.close_connection = True

    # ------------------------------------------------------------------------------------------------ files
    def serve_file(self, p, cache, csp, cookies=()):
        try:
            stt = os.stat(p)
        except OSError:
            return self.send_json(404, {"detail": "not found"})
        size = stt.st_size
        etag = f'"{int(stt.st_mtime_ns // 1000):x}-{size:x}"'
        if self.headers.get("If-None-Match") == etag and not cookies:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", cache)
            self.end_headers()
            return
        a, b, status = 0, size - 1, 200
        rng = self.headers.get("Range")
        m = re.match(r"^bytes=(\d*)-(\d*)$", (rng or "").strip())
        if rng and m and (m.group(1) or m.group(2)):
            if m.group(1):
                a = int(m.group(1))
                b = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                a = max(0, size - int(m.group(2)))
            if a >= size or a > b:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", ctype_for(p))
        self.send_header("Content-Length", str(b - a + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", email.utils.formatdate(stt.st_mtime, usegmt=True))
        self.send_header("Cache-Control", cache)
        if status == 206:
            self.send_header("Content-Range", f"bytes {a}-{b}/{size}")
        self._common(csp)
        if csp == CSP:
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        for c in cookies:
            self.send_header("Set-Cookie", c)
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(p, "rb") as f:
            f.seek(a)
            left = b - a + 1
            while left > 0:
                chunk = f.read(min(left, 256 * 1024))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.timeout):
                    self.close_connection = True
                    return
                left -= len(chunk)

    def shell(self, upath):
        """The web app: the server's copy once mirrored, else the bundled one. Unknown paths -> index.html (SPA)."""
        app = self.app
        root = app.mirror.root(use_mirror=bool(app.config["server"]))
        if not root:
            return self.send_json(503, {"detail": "no copy of the web app yet: run the client with a server "
                                                  "configured, or rebuild it with build/make_bundle.py"})
        rel = safe_relpath(upath.lstrip("/") or "index.html", roots=None)
        if upath.endswith("/") or rel is None:
            rel = "index.html"
        p = None
        try:
            cand = target_path(root, rel)
            if os.path.isfile(cand):
                p = cand
        except ValueError:
            pass
        if p is None:
            if rel.endswith((".js", ".css")) or rel.startswith("fonts/"):
                return self.send_json(404, {"detail": "not found"})
            rel, p = "index.html", os.path.join(root, "index.html")
        cookies = self.page_cookies() if rel.endswith(".html") else ()
        cache = "no-cache"
        if rel.startswith("fonts/") or ("v=" in self.path.partition("?")[2] and rel.endswith((".js", ".css"))):
            cache = "public, max-age=31536000, immutable"
        return self.serve_file(p, cache, CSP, cookies)

    def site(self, upath):
        """Site JSON and media from the mirror; when missing and online, straight from the server."""
        app = self.app
        if self.command not in ("GET", "HEAD"):
            return self.send_json(405, {"detail": "method not allowed"})
        rel = safe_relpath(upath.lstrip("/"))
        if rel is None:
            return self.send_json(404, {"detail": "not found"})
        is_media = rel.startswith("media/")
        cache = "private, max-age=86400" if is_media else "private, no-cache"
        root = app.engine.site_root
        cands = [rel]
        parts = rel.split("/")
        if is_media and len(parts) == 3 and rel.endswith(".mp4"):       # media/<ev>/<match>.mp4 -> its proxy copy
            cands.append(f"media/{parts[1]}/proxy/{parts[2]}")
        for c in cands:
            try:
                p = target_path(root, c)
            except ValueError:
                continue
            if os.path.isfile(p):
                return self.serve_file(p, cache, CSP_DATA)
        if app.server.base and app.engine.online:
            return self.passthrough(rel, cache)
        return self.send_json(404, {"detail": "not in the local copy" + ("" if app.engine.online else " (offline)")},
                              csp=CSP_DATA)

    def passthrough(self, rel, cache):
        app = self.app
        h = {k: self.headers[k] for k in ("Range", "If-None-Match") if self.headers.get(k)}
        try:
            r = app.server.call(self.command, "/" + quote(rel), headers=h, stream=True, timeout=60)
        except NetError as e:
            app.engine.set_online(False, str(e))
            return self.send_json(404, {"detail": "not in the local copy (offline)"}, csp=CSP_DATA)
        try:
            self.send_response(r.status)
            for k in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "ETag", "Last-Modified"):
                if r.header(k):
                    self.send_header(k, r.header(k))
            if not r.header("Content-Length"):
                self.close_connection = True
            self.send_header("Cache-Control", cache)
            self._common(CSP_DATA)
            self.end_headers()
            if self.command == "HEAD":
                return
            while True:
                b = r.read(256 * 1024)
                if not b:
                    break
                self.wfile.write(b)
        except NetError:
            self.close_connection = True
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
            r.close(broken=True)
        finally:
            r.close()

    # ------------------------------------------------------------------------------------------------ /app/v1
    def proxy(self, path, query):
        app = self.app
        sub = path[len("/app/v1"):]
        full = path + ("?" + query if query else "")
        method = self.command
        try:
            body = self.read_body()
        except ValueError as e:
            return self.send_json(413, {"detail": str(e)})
        if method in MUTATING and not consteq(self.headers.get("X-CSRF-Token"), app.config["local_csrf"]):
            return self.send_json(403, {"detail": "missing or bad CSRF token (send the cs_csrf cookie value as "
                                                  "X-CSRF-Token)"})
        if not app.server.base:
            return self.send_json(503, {"offline": True, "detail": "no server configured: set the server address "
                                                                   "in Settings → Connection"})
        if method in MUTATING:
            if sub in ("/auth/login", "/auth/key-login"):
                return self.page_login(body, key=sub == "/auth/key-login")
            if sub == "/auth/logout":
                app.logout()
                return self.send_json(204, None)
            # sign-in/out/password/setup, downloads (/sync/bundle) and connection keys are never queued
            direct = (sub.startswith(("/auth/", "/sync/")) or sub == "/setup" or DIRECT_RE.match(sub)
                      or len(body) > MAX_QUEUED_BODY)
            if direct:
                return self.forward(method, full, body, sub)
            return self.queue_write(method, full, body)
        # reads
        if app.engine.online:
            r = self.forward(method, full, body, sub, answer=False)
            if r is not None:
                return r
        if sub == "/session":
            return self.send_session(app.session.d.get("session"), offline=True)
        row = app.db.cache_get(full)
        if row is not None and method in ("GET", "HEAD"):
            data = bytes(row["body"] or b"")
            data = self._rewrite_csrf(data, row["ctype"])
            self.send_response(row["status"])
            self.send_header("Content-Type", row["ctype"] or "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-CS-Offline", "1")
            self.send_header("X-CS-Cached-At", str(int(row["stored"])))
            self.send_header("X-CS-At", str(int(row["stored"] * 1000)))     # (ms, like the service worker's x-cs-at)
            self._common(CSP_API)
            self.end_headers()
            if method != "HEAD":
                self._write(data)
            return
        return self.send_json(503, {"offline": True, "detail": "offline: this needs the server"})

    def _rewrite_csrf(self, data, ctype):
        if not ctype or "json" not in ctype or b'"csrf"' not in data:
            return data
        try:
            d = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return data
        if isinstance(d, dict) and "csrf" in d:
            d["csrf"] = self.app.config["local_csrf"]
            return json.dumps(d).encode()
        return data

    def send_session(self, sess, offline=False):
        app = self.app
        d = dict(sess or {"setup_required": False, "setup_code_required": False, "user": None,
                          "configured": True, "lockdown": False, "network": "lan", "passwordless_ok": False})
        d["csrf"] = app.config["local_csrf"]
        if offline:
            d["offline"] = True
        return self.send_json(200, d, cookies=[self.page_cookies()[1]])

    def forward(self, method, full, body, sub, answer=True, extra_headers=None):
        """Send one request to the server with our session. answer=False (reads): returns None after answering, or
        a falsy-but-not-None marker when the server could not be reached (caller falls back to the cache)."""
        app = self.app
        h = {k: self.headers[k] for k in self.headers.keys() if k.lower() in FWD_HEADERS}
        h.update(extra_headers or {})
        if method in MUTATING and "Idempotency-Key" not in h:
            h["Idempotency-Key"] = str(uuid.uuid4())
        gen = app.session.gen if method == "GET" else None     # a read must not undo a sign-in made meanwhile
        try:
            r = app.server.call(method, full, body=body or None, headers=h, timeout=120,
                                ctype=self.headers.get("Content-Type"), gen=gen)
            if r.status == 403 and method in MUTATING and "csrf" in detail_text(r.body).lower():
                app.server.refresh_session()
                r = app.server.call(method, full, body=body or None, headers=h, timeout=120,
                                    ctype=self.headers.get("Content-Type"))
        except NetError as e:
            app.engine.set_online(False, str(e))
            if answer:
                return self.send_json(503, {"offline": True, "detail": f"can't reach the server: {e}"})
            return None
        app.engine.set_online(True)
        ctype = r.header("Content-Type") or ""
        data = r.body or b""
        if "json" in ctype and b'"csrf"' in data:
            d = r.json()
            if isinstance(d, dict) and "csrf" in d and "user" in d:
                app.session.absorb_session_json(d, gen=gen)
        if method == "GET" and r.status == 200 and "json" in ctype and len(data) <= 4 * 1024 * 1024 \
                and not sub.startswith(("/auth/", "/sync/")):
            app.db.cache_put(full, r.status, ctype, data)
        data = self._rewrite_csrf(data, ctype)
        self.send_response(r.status)
        for k in r.headers.keys():
            if k.lower() in PASS_HEADERS:
                self.send_header(k, r.headers[k])
        if r.status not in (204, 304):
            self.send_header("Content-Length", str(len(data)))
        self._common(CSP_API)
        if sub in ("/session", "/setup", "/auth/profile"):
            self.send_header("Set-Cookie", self.page_cookies()[1])
        self.end_headers()
        if method != "HEAD" and data and r.status not in (204, 304):
            self._write(data)
        return True if not answer else None

    def page_login(self, body, key=False):
        """The web app's own sign-in (POST /app/v1/auth/login, or /auth/key-login with a connection key) signs the
        *client* in."""
        app = self.app
        try:
            d = json.loads(body.decode("utf-8") or "{}")
            if not isinstance(d, dict):
                raise ValueError("expected a JSON object")
        except (ValueError, UnicodeDecodeError):
            return self.send_json(422, {"detail": "bad JSON"})
        if key and _bad_key(d.get("key")):
            return self.send_json(422, {"detail": _bad_key(d.get("key"))})
        try:
            if key:
                sess = app.login_key(d["key"].strip())
            else:
                sess = app.login(str(d.get("username") or ""), str(d.get("password") or ""))
        except HTTPError as e:
            return self.send_json(e.status if 400 <= e.status < 600 else 502, {"detail": _cap(str(e))})
        except NetError as e:
            return self.send_json(503, {"offline": True, "detail": f"Can't reach the server: {e}."})
        return self.send_session(sess)

    def queue_write(self, method, full, body):
        app = self.app
        key = self.headers.get("Idempotency-Key") or ""
        if not re.match(r"^[A-Za-z0-9._:-]{8,100}$", key):
            key = str(uuid.uuid4())
        else:
            ex = app.db.db.execute("SELECT id FROM outbox WHERE key=?", (key,)).fetchone()
            if ex:                                 # the page retried a request we already hold
                return self.send_json(202, {"queued": True, "id": ex[0], "key": key})
        was_empty = app.db.pending(app.server.addresses()) == 0
        # rows remember the configured server (not the alternate address in use): only ever sent to that server
        item = app.db.enqueue(method, full, self.headers.get("Content-Type"), body, app.config["server"], key)
        oid = item["id"]
        log.info("write %s %s %s #%d", method, full, "merged into queued" if item.get("merged") else "queued as", oid)
        if not app.engine.online:
            return self.send_json(202, {"queued": True, "id": oid, "key": key})
        if not was_empty or item.get("merged"):    # keep the order: behind what is already waiting
            app.engine.flush_async()
            return self.send_json(202, {"queued": True, "id": oid, "key": key})
        ev = threading.Event()
        app.engine.waiters[oid] = ev
        try:
            app.engine.flush_async()
            ev.wait(WRITE_WAIT_S)
        finally:
            app.engine.waiters.pop(oid, None)
        row = app.db.get(oid)
        if row is not None and row["state"] in ("sent", "conflict", "failed"):
            app.db.delete(oid)                     # answered to the page: nothing left to show in the outbox
            data = bytes(row["response"] or b"")
            self.send_response(row["status"])
            if data:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self._common(CSP_API)
            self.end_headers()
            if data:
                self._write(data)
            return
        return self.send_json(202, {"queued": True, "id": oid, "key": key})

    # ------------------------------------------------------------------------------------------------ /client/v1
    def control(self, sub):
        app, m = self.app, self.command
        try:
            if sub == "status" and m in ("GET", "HEAD"):
                return self.send_json(200, app.status())
            if sub == "config" and m in ("PUT", "PATCH", "POST"):
                return self.ctl_config(self.json_body())
            if sub == "config" and m == "GET":
                return self.send_json(200, app.config.public())
            if sub == "test" and m == "POST":
                return self.ctl_test(self.json_body())
            if sub in ("login", "login-key") and m == "POST":
                return self.ctl_login(self.json_body(), key=sub == "login-key")
            if sub == "logout" and m == "POST":
                app.logout()
                return self.send_json(200, {"ok": True})
            if sub == "sync" and m == "POST":
                d = self.json_body()
                ev = d.get("ev")
                if ev is not None and (not isinstance(ev, str) or not EVENT_RE.match(ev)):
                    return self.send_json(422, {"detail": "ev: an event key like 2026wasam"})
                media = d.get("media")
                if media is not None and not isinstance(media, bool):
                    return self.send_json(422, {"detail": "media: true or false"})
                app.engine.kick(ev, media)
                return self.send_json(202, {"started": True, "online": app.engine.online})
            if sub == "processing" and m in ("GET", "HEAD"):
                return self.send_json(200, app.processor.status())
            if sub == "processing" and m in ("PUT", "PATCH", "POST"):
                return self.ctl_processing(self.json_body())
            if sub == "processing/setup" and m == "POST":
                started = app.processor.start_setup()
                return self.send_json(202 if started else 409, {"started": started,
                                                                "setup": app.processor.status()["setup"]})
            if sub == "processing/bench" and m == "POST":
                started = app.processor.start_bench()
                return self.send_json(202 if started else 409, {"started": started})
            if sub == "processing/netcheck" and m == "POST":
                started = app.processor.start_netcheck()
                return self.send_json(202 if started else 409, {"started": started})
            if sub == "processing/gpu" and m == "POST":
                g = app.processor.gpu(again=True)
                app.processor.kick()
                return self.send_json(200, {"gpu": g})
            if sub == "outbox" and m == "GET":
                return self.send_json(200, app.db.items())
            if sub == "quit" and m == "POST":
                return self.ctl_quit()
            if sub == "uninstall" and m == "POST":
                return self.ctl_uninstall(self.json_body())
            me = re.match(r"^events/([^/]+)$", sub)
            if me and m == "DELETE":
                ev = me.group(1)
                if not EVENT_RE.match(ev):
                    return self.send_json(422, {"detail": "ev: an event key like 2026wasam"})
                chosen = app.config["events"]
                if ev in chosen if chosen is not None else any(e.get("key") == ev for e in app.engine.events_list):
                    return self.send_json(409, {"detail": "This competition is still synced: untick it first."})
                if app.engine.syncing:
                    return self.send_json(409, {"detail": "A sync is running: try again in a moment."})
                return self.send_json(200, {"ok": True, "freed": app.engine.remove_event(ev)})
            mm = re.match(r"^outbox/(\d+)$", sub)
            if mm and m == "DELETE":
                oid = int(mm.group(1))
                if app.db.get(oid) is None:
                    return self.send_json(404, {"detail": "no such queued change"})
                if not app.db.delete(oid):
                    return self.send_json(409, {"detail": "being sent right now; try again in a moment"})
                log.info("outbox #%d discarded by the user", oid)
                return self.send_json(204, None)
            return self.send_json(404, {"detail": "unknown client endpoint"})
        except (ValueError, UnicodeDecodeError) as e:
            return self.send_json(422, {"detail": f"bad request: {e}"})

    def ctl_login(self, d, key=False):
        """POST login {username, password} / login-key {key}: the client signs itself in (the page never sees the
        server's session) -> {ok, user: {display_name, role}}; the server's status + {ok:false, detail} (401, 429, ...);
        503 {offline:true}."""
        app = self.app
        if key and _bad_key(d.get("key")):
            return self.send_json(422, {"ok": False, "detail": _bad_key(d.get("key"))})
        if not app.server.base:
            return self.send_json(400, {"ok": False, "detail": "set the server address first"})
        try:
            if key:
                sess = app.login_key(d["key"].strip())
            else:
                sess = app.login(str(d.get("username") or ""), str(d.get("password") or ""))
        except HTTPError as e:
            return self.send_json(e.status if 400 <= e.status < 600 else 502, {"ok": False, "detail": _cap(str(e))})
        except NetError as e:
            return self.send_json(503, {"ok": False, "offline": True, "detail": f"Can't reach the server: {e}."})
        u = sess.get("user") or {}
        return self.send_json(200, {"ok": True, "user": {"display_name": u.get("display_name"), "role": u.get("role")}})

    def ctl_quit(self):
        """Stop: answer, then shut down like Ctrl+C (main() does it) and make sure the login item doesn't start the
        client again right away (csclient/install.py)."""
        app = self.app
        how, then = app.install.stop()
        log.info("stop asked by the app (%s)", how)
        self.close_connection = True
        self.send_json(200, {"ok": True, "how": how, "message": app.install.stop_message(how)})
        app.request_quit(then)

    def ctl_uninstall(self, d):
        """Uninstall {delete_data?: bool}: remove what the install scripts installed (csclient/install.py), answer
        {ok, removed, kept, errors}, then stop; 409 when run from source."""
        app = self.app
        dd = d.get("delete_data", False)
        if not isinstance(dd, bool):
            return self.send_json(422, {"detail": "delete_data: true or false"})
        try:
            out, then = app.install.uninstall(dd)
        except NotInstalled as e:
            return self.send_json(409, {"detail": str(e)})
        self.close_connection = True
        self.send_json(200, out)
        app.request_quit(then)

    def ctl_config(self, d):
        app = self.app
        ch = {}
        if "server" in d:
            s = d["server"]
            if s in (None, ""):
                ch["server"] = None
            else:
                try:
                    ch["server"] = normalize_server(s)
                except ValueError as e:
                    return self.send_json(422, {"detail": f"server: {e}"})
        if "alt_servers" in d:
            al = d["alt_servers"]
            if al is None:
                al = []
            if not isinstance(al, list) or len(al) > 5 or not all(isinstance(s, str) for s in al):
                return self.send_json(422, {"detail": "alt_servers: a list of up to 5 server addresses"})
            try:
                ch["alt_servers"] = list(dict.fromkeys(normalize_server(s) for s in al if s.strip()))
            except ValueError as e:
                return self.send_json(422, {"detail": f"alt_servers: {e}"})
        if "media" in d:
            if d["media"] not in MEDIA_MODES:
                return self.send_json(422, {"detail": "media: none, proxy or full"})
            ch["media"] = d["media"]
        if "events" in d:
            evs = d["events"]
            if evs is not None and (not isinstance(evs, list) or len(evs) > 200
                                    or not all(isinstance(e, str) and EVENT_RE.match(e) for e in evs)):
                return self.send_json(422, {"detail": "events: null (all followed events) or a list of event keys"})
            ch["events"] = None if evs is None else list(dict.fromkeys(evs))
        if "interval" in d:
            iv = d["interval"]
            if not isinstance(iv, int) or not 15 <= iv <= 3600:
                return self.send_json(422, {"detail": "interval: 15-3600 seconds"})
            ch["interval"] = iv
        app.set_config(**ch)
        return self.send_json(200, {"ok": True, "config": app.config.public()})

    def ctl_processing(self, d):
        """{enabled?: bool, python?: absolute path | null, events?: [event keys] | null, warn_mb?: int (0 = never),
        allow_large?: true, mode?: "track" | "clip", allow_slow?: true} (processor mode)."""
        app = self.app
        ch = {}
        if "python" in d:
            py = d["python"]
            if py in (None, ""):
                ch["process_python"] = None
            else:
                if not isinstance(py, str) or len(py) > 1024 or "\x00" in py or not os.path.isabs(py):
                    return self.send_json(422, {"detail": "python: the full path of a Python executable"})
                if not os.path.isfile(py) or not os.access(py, os.X_OK):
                    return self.send_json(422, {"detail": f"python: {py} is not an executable file"})
                ch["process_python"] = py
        if "events" in d:
            evs = d["events"]
            if evs is not None and (not isinstance(evs, list) or len(evs) > 50
                                    or not all(isinstance(e, str) and EVENT_RE.match(e) for e in evs)):
                return self.send_json(422, {"detail": "events: null (all followed events) or a list of event keys"})
            ch["process_events"] = None if evs is None else list(dict.fromkeys(evs))
        if "warn_mb" in d:
            w = d["warn_mb"]
            if not isinstance(w, int) or isinstance(w, bool) or not 0 <= w <= 100000:
                return self.send_json(422, {"detail": "warn_mb: 0 (never warn) to 100000 MB"})
            ch["process_warn_mb"] = w
        if "allow_large" in d and d["allow_large"] is not True:
            return self.send_json(422, {"detail": "allow_large: true (allows large clips until the client restarts)"})
        if "enabled" in d and not isinstance(d["enabled"], bool):
            return self.send_json(422, {"detail": "enabled: true or false"})
        if "mode" in d and d["mode"] not in ("track", "clip"):
            return self.send_json(422, {"detail": "mode: track (GPU tracking) or clip (Clip only)"})
        if "allow_slow" in d and d["allow_slow"] is not True:
            return self.send_json(422, {"detail": "allow_slow: true (track even though this computer is slow)"})
        if ch:
            app.config.update(**ch)
            app.processor.kick()
        if d.get("allow_large") is True:
            app.processor.allow_large()
        if "mode" in d:
            app.processor.set_mode(d["mode"])
        if d.get("allow_slow") is True:
            app.processor.allow_slow()
        if "enabled" in d:
            app.processor.set_enabled(d["enabled"])
        return self.send_json(200, app.processor.status())

    def proc_page(self, upath):
        from .procui import PAGE, SCRIPT
        page = upath.endswith("processing")
        body = PAGE if page else SCRIPT
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8" if page else "text/javascript; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self._common(CSP)
        if page:
            for c in self.page_cookies():
                self.send_header("Set-Cookie", c)
        self.end_headers()
        if self.command != "HEAD":
            self._write(body)

    def ctl_test(self, d):
        """Test connection: always 200 {ok, server?, latency_ms?, version?, name?, error?}; errors are one plain
        sentence for the person typing the address (CLIENT-11)."""
        app = self.app
        s = d.get("server") or app.config["server"]
        try:
            base = normalize_server(s)
        except ValueError as e:
            return self.send_json(200, {"ok": False, "error": _cap(str(e)) + "."})
        u = urlsplit(base)
        if (u.hostname or "").lower() in ("127.0.0.1", "localhost", "::1") and (u.port or 80) == app.port:
            return self.send_json(200, {"ok": False, "server": base, "error":
                                        "That's this Cyber-Scout Client's own address. Enter your team's server: the "
                                        "address you use for Cyber-Scout in a browser."})
        c = Conn(base, ca_file=app.config.get("ca_file"), pool_size=0)
        t0 = time.time()
        try:
            r = c.request("GET", "/app/v1/info", headers={"Accept": "application/json"}, timeout=10,
                          max_body=64 * 1024)
            ms = round((time.time() - t0) * 1000)
            info = r.json()
            if r.status != 200 or not isinstance(info, dict) or info.get("app") != "cyber-scout":
                if self._looks_like_cyber_scout(c):
                    msg = (f"{base} is a Cyber-Scout server, but a version without offline sync (GET /app/v1/info: "
                           f"HTTP {r.status}). Ask your admin to update it.")
                else:
                    msg = (f"Something answers at {base}, but it isn't Cyber-Scout (GET /app/v1/info: HTTP {r.status})."
                           " Check the address.")
                return self.send_json(200, {"ok": False, "server": base, "latency_ms": ms, "error": msg})
            out = {"ok": True, "server": base, "latency_ms": ms, "version": info.get("version"),
                   "name": info.get("name"), "api": info.get("api"), "time": info.get("time")}
            if info.get("api") != 1:
                out.update(ok=False, error=f"This server speaks sync API v{info.get('api')} and this Client speaks "
                                           "v1: update the Client.")
            return self.send_json(200, out)
        except NetError as e:
            if getattr(e, "kind", None) == "no_tls" and base.startswith("https://"):
                return self.send_json(200, {"ok": False, "server": base, "suggest": "http://" + base[8:], "error":
                                            f"This address doesn't use HTTPS. Try http://{base[8:]}"})
            return self.send_json(200, {"ok": False, "server": base, "error": f"Can't reach {base}: {e}."})
        finally:
            c.close()

    @staticmethod
    def _looks_like_cyber_scout(c):
        """Does the site at this address serve the Cyber-Scout web app (an older server without /app/v1/info)?"""
        try:
            r = c.request("GET", "/", headers={"Accept": "text/html"}, timeout=6, max_body=512 * 1024)
        except NetError:
            return False
        return r.status == 200 and b"Cyber-Scout" in (r.body or b"")


def _cap(t):
    return t[:1].upper() + t[1:] if t else t


def _bad_key(k):
    """What is wrong with a connection key, or None (a light check; the server decides)."""
    if not isinstance(k, str) or not k.strip():
        return "key: the connection key, e.g. 7KQ2-M9XA-P4HD-W8NE"
    if len(k.strip()) > KEY_MAX or any(c < " " or c == "\x7f" for c in k):
        return f"key: at most {KEY_MAX} characters, no control characters"
    return None
