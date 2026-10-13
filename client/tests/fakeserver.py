"""A fake Cyber-Scout server implementing the part-1 endpoints of docs/SYNC.md (info, session/login/logout with the
real cookie + double-submit CSRF rules, sync/events, sync/manifest with since/deleted, sync/bundle with Range, sync/media,
/api + /media files with Range, notes POST with Idempotency-Key replay, picklist PUT with base_version 409), plus knobs
to simulate failures: `down`, `cut_bundle_after`, `drop_response_once`, `latency`."""
import gzip
import hashlib
import http.server
import io
import json
import re
import secrets
import socket
import tarfile
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit

WEB = {
    "index.html": b'<!doctype html><html><head><script src="theme.js?v=abc"></script>'
                  b'<link rel="stylesheet" href="style.css?v=abc"></head><body><script src="app.js?v=abc"></script>'
                  b'</body></html>',
    "theme.js": b"window.T=1;",
    "style.css": b'@font-face{src:url("fonts/a.woff2")} body{background:url(field.jpg)}',
    "app.js": b'const FIELD="field.jpg"; navigator.serviceWorker && navigator.serviceWorker.register("sw.js");'
              b' api("event.json");',
    "sw.js": b"self.addEventListener('fetch',()=>{});",
    "fonts/a.woff2": b"wOF2fakefont",
    "field.jpg": b"\xff\xd8\xff\xe0fakejpeg",
}


class State:
    def __init__(self):
        self.lock = threading.RLock()
        self.counter = 0
        self.files, self.mtimes, self.deleted = {}, {}, {}
        self.media = {}          # path -> (bytes, kind, match)
        self.web = dict(WEB)
        self.version = "v1"
        self.users = {"scout": ("pw-scout-1", "scout", "Scout One")}
        self.sessions = {}       # token -> {"user":..., "csrf":...}
        self.idem = {}           # key -> (status, body)
        self.notes = []
        self.applied = 0
        self.picklist = {"version": 1, "teams": ["frc1"]}
        self.down = False
        self.cut_bundle_after = None
        self.drop_response_once = set()
        self.latency = 0.0
        self.requests = []       # (method, path, headers)
        self.events = [{"key": "2026test", "name": "Test Event", "start_date": "2026-03-01", "end_date": "2026-03-03",
                        "status": "done", "progress": {"tracked": 2, "total": 2}}]

    def put(self, path, data):
        with self.lock:
            self.counter += 1
            self.files[path] = data
            self.mtimes[path] = self.counter
            self.deleted.pop(path, None)

    def delete(self, path):
        with self.lock:
            self.counter += 1
            self.files.pop(path, None)
            self.mtimes.pop(path, None)
            self.deleted[path] = self.counter

    @staticmethod
    def in_scope(path, ev):
        return path.startswith(f"api/{ev}/") or re.match(r"^api/[^/]+$", path) is not None

    def manifest(self, ev, since):
        with self.lock:
            cur = self.counter
            full = since is None or not since.isdigit() or int(since) > cur
            s = 0 if full else int(since)
            files = [{"path": p, "size": len(b), "sha256": hashlib.sha256(b).hexdigest(), "mtime": self.mtimes[p]}
                     for p, b in sorted(self.files.items()) if self.in_scope(p, ev) and self.mtimes[p] > s]
            deleted = [] if full else [p for p, c in sorted(self.deleted.items()) if c > s and self.in_scope(p, ev)]
            return {"ev": ev, "cursor": str(cur), "full": full, "files": files, "deleted": deleted}

    def bundle(self, paths):
        """Deterministic tar.gz (same paths -> same bytes) so Range resume works."""
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tf:
                for p in paths:
                    data = self.files[p]
                    ti = tarfile.TarInfo(p)
                    ti.size, ti.mtime, ti.mode = len(data), 0, 0o644
                    tf.addfile(ti, io.BytesIO(data))
        return buf.getvalue()


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    @property
    def st(self) -> State:
        return self.server.state

    def do_GET(self): self.route()
    def do_HEAD(self): self.route()
    def do_POST(self): self.route()
    def do_PUT(self): self.route()
    def do_DELETE(self): self.route()

    # ---- helpers
    def cookies(self):
        out = {}
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k:
                out[k] = v
        return out

    def user(self):
        s = self.st.sessions.get(self.cookies().get("cs_session", ""))
        return s

    def send(self, status, body=b"", ctype="application/json", headers=None, cookies=()):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(status)
        if body or status not in (204, 304):
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        for c in cookies:
            self.send_header("Set-Cookie", c)
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def body(self):
        # read once and keep it: every request's body is consumed (see route), so an early 401/403 answer can't
        # leave body bytes on a keep-alive connection to be parsed as the next request line
        if getattr(self, "_body", None) is None:
            n = int(self.headers.get("Content-Length") or 0)
            self._body = self.rfile.read(n) if n else b""
        return self._body

    def session_out(self, s, csrf):
        return {"setup_required": False, "setup_code_required": False,
                "user": s["user"] if s else None, "csrf": csrf, "configured": True, "lockdown": False,
                "network": "lan", "passwordless_ok": False}

    def csrf_ok(self, s):
        hdr = self.headers.get("X-CSRF-Token")
        ck = self.cookies().get("cs_csrf")
        if not hdr or hdr != ck:
            return False
        return s is None or hdr == s["csrf"]

    def ranged(self, data, ctype):
        size = len(data)
        rng = self.headers.get("Range")
        m = re.match(r"^bytes=(\d+)-(\d*)$", rng or "")
        if m:
            a = int(m.group(1))
            b = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            if a >= size:
                return self.send(416, b"", headers={"Content-Range": f"bytes */{size}"})
            return self.send(206, data[a:b + 1], ctype, {"Content-Range": f"bytes {a}-{b}/{size}",
                                                         "Accept-Ranges": "bytes"})
        return self.send(200, data, ctype, {"Accept-Ranges": "bytes"})

    # ---- routing
    def route(self):
        self._body = None
        self.body()
        st = self.st
        u = urlsplit(self.path)
        path, q = unquote(u.path), parse_qs(u.query)
        with st.lock:
            st.requests.append((self.command, self.path, dict(self.headers.items())))
        if st.down:
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            return
        if st.latency:
            time.sleep(st.latency)
        s = self.user()
        m = self.command
        if path == "/app/v1/info":
            return self.send(200, {"app": "cyber-scout", "api": 1, "version": st.version, "name": "Fake",
                                   "time": int(time.time())})
        if path == "/app/v1/session":
            csrf = s["csrf"] if s else (self.cookies().get("cs_csrf") or secrets.token_urlsafe(18))
            return self.send(200, self.session_out(s, csrf), cookies=[f"cs_csrf={csrf}; Path=/; SameSite=lax"])
        if path == "/app/v1/auth/login" and m == "POST":
            if not self.csrf_ok(None):
                return self.send(403, {"detail": "missing or bad CSRF token"})
            d = json.loads(self.body() or b"{}")
            rec = st.users.get(d.get("username"))
            if not rec or rec[0] != d.get("password"):
                return self.send(401, {"detail": "invalid username or password"})
            tok, csrf = secrets.token_urlsafe(24), secrets.token_urlsafe(18)
            sess = {"user": {"id": 1, "username": d["username"], "display_name": rec[2], "role": rec[1]}, "csrf": csrf}
            st.sessions[tok] = sess
            return self.send(200, self.session_out(sess, csrf),
                             cookies=[f"cs_session={tok}; HttpOnly; Path=/; SameSite=lax",
                                      f"cs_csrf={csrf}; Path=/; SameSite=lax"])
        if path == "/app/v1/auth/logout" and m == "POST":
            st.sessions.pop(self.cookies().get("cs_session", ""), None)
            return self.send(204, b"", cookies=['cs_session=""; Max-Age=0; Path=/'])
        if path.startswith(("/app/v1/", "/api/", "/media/")) and not s:
            return self.send(401, {"detail": "sign in required"})
        if m in ("POST", "PUT", "PATCH", "DELETE") and path.startswith("/app/v1/") and path != "/app/v1/sync/bundle":
            if not self.csrf_ok(s):
                return self.send(403, {"detail": "missing or bad CSRF token"})
        if path == "/app/v1/sync/events":
            return self.send(200, st.events)
        if path == "/app/v1/sync/manifest":
            ev = (q.get("ev") or [""])[0]
            if ev not in [e["key"] for e in st.events]:
                return self.send(404, {"detail": "unknown event"})
            return self.send(200, st.manifest(ev, (q.get("since") or [None])[0]))
        if path == "/app/v1/sync/bundle" and m == "POST":
            d = json.loads(self.body() or b"{}")
            paths = d.get("paths") or []
            with st.lock:
                if any(p not in st.files or not st.in_scope(p, d.get("ev")) for p in paths):
                    return self.send(400, {"detail": "path not in manifest"})
                data = st.bundle(paths)
                cut = st.cut_bundle_after
                st.cut_bundle_after = None
            if cut is not None and not self.headers.get("Range"):
                self.send_response(200)
                self.send_header("Content-Type", "application/gzip")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                self.wfile.write(data[:cut])
                self.wfile.flush()
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            return self.ranged(data, "application/gzip")
        if path == "/app/v1/sync/media":
            ev = (q.get("ev") or [""])[0]
            return self.send(200, [{"path": p, "size": len(b), "sha256": hashlib.sha256(b).hexdigest(), "kind": k,
                                    "match": mt} for p, (b, k, mt) in sorted(st.media.items())
                                   if p.startswith(f"media/{ev}/")])
        mm = re.match(r"^/app/v1/events/(\w+)/notes$", path)
        if mm and m == "POST":
            key = self.headers.get("Idempotency-Key")
            body = self.body()
            with st.lock:
                if key and key in st.idem:
                    code, out = st.idem[key]
                    return self.send(code, out, headers={"Idempotent-Replay": "true"})
                d = json.loads(body or b"{}")
                note = {"id": len(st.notes) + 1, "text": d.get("text"), "by": s["user"]["display_name"]}
                st.notes.append(note)
                st.applied += 1
                if key:
                    st.idem[key] = (201, note)
                drop = path in st.drop_response_once
                st.drop_response_once.discard(path)
            if drop:                    # applied, but the answer is lost on the way back
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            return self.send(201, note)
        mm = re.match(r"^/app/v1/events/(\w+)/picklists/(\w+)$", path)
        if mm and m == "PUT":
            key = self.headers.get("Idempotency-Key")
            d = json.loads(self.body() or b"{}")
            with st.lock:
                if key and key in st.idem:
                    code, out = st.idem[key]
                    return self.send(code, out)
                if d.get("base_version") is not None and d["base_version"] != st.picklist["version"]:
                    out = {"detail": {"message": "changed by someone else", "current": st.picklist}}
                    if key:
                        st.idem[key] = (409, out)
                    return self.send(409, out)
                st.picklist = {"version": st.picklist["version"] + 1, "teams": d.get("teams") or []}
                st.applied += 1
                if key:
                    st.idem[key] = (200, st.picklist)
                return self.send(200, st.picklist)
        if path.startswith("/api/"):
            data = st.files.get(path[1:])
            return self.ranged(data, "application/json") if data is not None else self.send(404, {"detail": "nf"})
        if path.startswith("/media/"):
            rec = st.media.get(path[1:])
            return self.ranged(rec[0], "video/mp4") if rec else self.send(404, {"detail": "nf"})
        if path.startswith("/app/v1/"):
            return self.send(404, {"detail": "Not Found"})
        rel = path.lstrip("/") or "index.html"
        if rel in st.web:
            ct = "text/html" if rel.endswith(".html") else "application/octet-stream"
            return self.send(200, st.web[rel], ct)
        return self.send(200, st.web["index.html"], "text/html")   # nginx try_files ... /index.html


class QuietServer(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):     # cut connections are part of the tests
        pass


class FakeServer:
    def __init__(self, port=0):
        self.state = State()
        self.httpd = QuietServer(("127.0.0.1", port), Handler)
        self.httpd.daemon_threads = True
        self.httpd.state = self.state
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
