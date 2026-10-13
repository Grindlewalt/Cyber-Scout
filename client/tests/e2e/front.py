"""An nginx-like front for end-to-end tests: serves web/ (SPA fallback), /api + /media from disk behind the API's
auth check (GET /app/v1/auth/check, like auth_request), proxies /app/v1 to the real API, gzips JSON. Simulates a
venue link: per-request latency + a shared bandwidth cap, a kill switch (`down`), cutting the next bundle mid-way and
losing the answer of the next write."""
import gzip
import http.client
import http.server
import os
import re
import socket
import threading
import time
from urllib.parse import unquote, urlsplit

HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-authorization",
       "proxy-authenticate", "host", "content-length", "accept-encoding"}


class Link:
    """Shared link: latency per request and a byte rate shared by all connections (None = unlimited)."""

    def __init__(self):
        self.latency, self.rate = 0.0, None
        self.lock = threading.Lock()
        self.next_free = 0.0
        self.bytes_out = 0
        self.bytes_in = 0

    def write(self, wfile, data):
        for i in range(0, len(data), 16384):
            chunk = data[i:i + 16384]
            if self.rate:
                with self.lock:
                    now = time.time()
                    start = max(now, self.next_free)
                    self.next_free = start + len(chunk) / self.rate
                    wait = self.next_free - now
                if wait > 0:
                    time.sleep(wait)
            wfile.write(chunk)
            with self.lock:
                self.bytes_out += len(chunk)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    @property
    def f(self):
        return self.server.front

    def do_GET(self): self.route()
    def do_HEAD(self): self.route()
    def do_POST(self): self.route()
    def do_PUT(self): self.route()
    def do_PATCH(self): self.route()
    def do_DELETE(self): self.route()

    def kill(self):
        self.close_connection = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def reply(self, status, body=b"", headers=()):
        gz = False
        ctype = dict((k.lower(), v) for k, v in headers).get("content-type", "")
        if body and len(body) > 512 and "json" in ctype and "gzip" in (self.headers.get("Accept-Encoding") or "") \
                and status == 200:
            body = gzip.compress(body, 5)
            gz = True
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        if gz:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD" and body:
            self.f.link.write(self.wfile, body)

    def api(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.f.api_port, timeout=300)
        c.request(method, path, body=body, headers=headers or {})
        return c, c.getresponse()

    def route(self):
        f = self.f
        with f.lock:
            f.requests.append((self.command, self.path))
        if f.down:
            return self.kill()
        if f.link.latency:
            time.sleep(f.link.latency)
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else None
        f.link.bytes_in += n
        path = unquote(urlsplit(self.path).path)
        try:
            if path.startswith("/app/v1/"):
                return self.proxy(body)
            if path.startswith(("/api/", "/media/")):
                return self.data(path)
            return self.web(path)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def fwd_headers(self):
        h = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        h["X-Real-IP"] = self.client_address[0]
        h["X-Forwarded-Proto"] = "http"
        return h

    def proxy(self, body):
        f = self.f
        c, r = self.api(self.command, self.path, body, self.fwd_headers())
        try:
            is_bundle = self.path.startswith("/app/v1/sync/bundle")
            hdrs = [(k, v) for k, v in r.getheaders() if k.lower() not in HOP]
            if is_bundle:
                cut, f.cut_bundle_after = f.cut_bundle_after, None
                self.send_response(r.status)
                for k, v in hdrs:
                    self.send_header(k, v)
                clen = r.getheader("Content-Length")
                if clen:
                    self.send_header("Content-Length", clen)
                else:
                    self.close_connection = True
                self.end_headers()
                sent = 0
                while True:
                    b = r.read(65536)
                    if not b:
                        break
                    if cut is not None and sent + len(b) > cut:
                        f.link.write(self.wfile, b[:cut - sent])
                        self.wfile.flush()
                        return self.kill()
                    f.link.write(self.wfile, b)
                    sent += len(b)
                return
            data = r.read()
            if self.command in ("POST", "PUT", "PATCH", "DELETE") and f.lose_next_write_answer \
                    and "/notes" in self.path:
                f.lose_next_write_answer = False      # the server applied it; the answer is lost on the way back
                return self.kill()
            self.reply(r.status, data, hdrs)
        finally:
            c.close()

    def data(self, path):
        f = self.f
        h = {"Cookie": self.headers.get("Cookie", ""), "X-Real-IP": self.client_address[0]}
        c, r = self.api("GET", "/app/v1/auth/check", None, h)
        r.read()
        c.close()
        if r.status != 204:
            return self.reply(401, b'{"detail":"sign in required"}', [("Content-Type", "application/json")])
        rel = path.lstrip("/")
        base = f.site if rel.startswith("api/") else f.media
        sub = rel.split("/", 1)[1]
        if ".." in sub.split("/"):
            return self.reply(400)
        p = os.path.join(base, *sub.split("/"))
        if not os.path.isfile(p) or os.path.islink(p):
            return self.reply(404, b'{"detail":"not found"}', [("Content-Type", "application/json")])
        size = os.path.getsize(p)
        ctype = "application/json" if p.endswith(".json") else "video/mp4" if p.endswith(".mp4") else \
            "application/octet-stream"
        m = re.match(r"^bytes=(\d+)-(\d*)$", self.headers.get("Range") or "")
        a, b, st = 0, size - 1, 200
        if m:
            a = int(m.group(1))
            b = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            if a >= size:
                return self.reply(416, b"", [("Content-Range", f"bytes */{size}")])
            st = 206
        if st == 200 and ctype == "application/json":
            with open(p, "rb") as fh:
                return self.reply(200, fh.read(), [("Content-Type", ctype)])
        self.send_response(st)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(b - a + 1))
        self.send_header("Accept-Ranges", "bytes")
        if st == 206:
            self.send_header("Content-Range", f"bytes {a}-{b}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(p, "rb") as fh:
            fh.seek(a)
            left = b - a + 1
            while left > 0:
                chunk = fh.read(min(left, 1 << 20))
                if not chunk:
                    break
                f.link.write(self.wfile, chunk)
                left -= len(chunk)

    def web(self, path):
        rel = path.lstrip("/") or "index.html"
        p = os.path.join(self.f.web, *[x for x in rel.split("/") if x not in ("", ".", "..")])
        if not os.path.isfile(p):
            if rel.endswith((".js", ".css")):
                return self.reply(404)
            p = os.path.join(self.f.web, "index.html")
        ext = os.path.splitext(p)[1]
        ct = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2",
              ".jpg": "image/jpeg"}.get(ext, "application/octet-stream")
        with open(p, "rb") as fh:
            self.reply(200, fh.read(), [("Content-Type", ct)])


class Quiet(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


class Front:
    def __init__(self, api_port, site, media, web, port=0):
        self.api_port, self.site, self.media, self.web = api_port, site, media, web
        self.link = Link()
        self.lock = threading.Lock()
        self.requests = []
        self.down = False
        self.cut_bundle_after = None
        self.lose_next_write_answer = False
        self.httpd = Quiet(("127.0.0.1", port), Handler)
        self.httpd.front = self
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
