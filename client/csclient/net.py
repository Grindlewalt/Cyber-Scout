"""Talking to the Cyber-Scout server: keep-alive connection pool (slow links: a TLS handshake through the tunnel costs
seconds), TLS trust store that also works in frozen apps, the server session (cookies + CSRF, never shown to the page)."""
import gzip
import http.client
import json
import logging
import os
import socket
import ssl
import threading
import time
import zlib
from urllib.parse import urlsplit

from . import VERSION

log = logging.getLogger("cyberscout.net")
UA = f"CyberScoutClient/{VERSION}"
MUTATING = ("POST", "PUT", "PATCH", "DELETE")


class NetError(Exception):
    """The server could not be reached / the connection broke (=> offline)."""


class HTTPError(Exception):
    def __init__(self, status, body=None, msg=None):
        self.status, self.body = status, body
        super().__init__(msg or detail_text(body) or f"HTTP {status}")


def detail_text(body) -> str:
    """The FastAPI 'detail' of an error answer as one readable line."""
    if isinstance(body, (bytes, bytearray)):
        try:
            body = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return ""
    if not isinstance(body, dict):
        return ""
    d = body.get("detail")
    if isinstance(d, str):
        return d
    if isinstance(d, list):
        return "; ".join(str(x.get("msg", x)) if isinstance(x, dict) else str(x) for x in d)
    if isinstance(d, dict):
        return str(d.get("message") or json.dumps(d))
    return ""


_CA_CANDIDATES = ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt",
                  "/etc/ssl/ca-bundle.pem", "/opt/homebrew/etc/openssl@3/cert.pem", "/usr/local/etc/openssl@3/cert.pem")


def make_ssl_context(ca_file=None) -> ssl.SSLContext:
    """Verified TLS. Frozen (PyInstaller) and python.org macOS builds may ship OpenSSL without a CA store: fall back to
    the OS bundle (/etc/ssl/cert.pem exists on macOS). Windows loads the system store by itself."""
    if ca_file:
        return ssl.create_default_context(cafile=ca_file)
    ctx = ssl.create_default_context()
    try:
        empty = ctx.cert_store_stats().get("x509_ca", 0) == 0
    except Exception:
        empty = True
    if empty:
        for p in _CA_CANDIDATES:
            if os.path.exists(p):
                try:
                    ctx.load_verify_locations(cafile=p)
                    break
                except (OSError, ssl.SSLError):
                    continue
    return ctx


class Response:
    def __init__(self, status, headers, body=None, raw=None, conn=None, pool=None):
        self.status, self.headers, self.body = status, headers, body
        self.raw, self._conn, self._pool = raw, conn, pool

    @property
    def ok(self):
        return 200 <= self.status < 300

    def header(self, name, default=None):
        return self.headers.get(name, default)

    def json(self):
        try:
            return json.loads((self.body or b"").decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def read(self, n):
        """Streamed body chunk (stream=True); raises NetError if the connection breaks."""
        try:
            return self.raw.read(n)
        except (http.client.IncompleteRead, OSError, ssl.SSLError, http.client.HTTPException) as e:
            self.close(broken=True)
            raise NetError(f"connection broke while downloading: {e.__class__.__name__}: {e}")

    def close(self, broken=False):
        if self.raw is None:
            return
        try:
            if not broken and self.raw.isclosed() and not self.raw.will_close and self._pool:
                self._pool._put(self._conn)
            else:
                self._conn.close()
        except Exception:
            pass
        self.raw = None


class Conn:
    """HTTP(S) to one origin, with a small pool of keep-alive connections. Never follows redirects (only the configured
    server is contacted)."""

    def __init__(self, base: str, ca_file=None, pool_size=4):
        u = urlsplit(base)
        self.base = base
        self.https = u.scheme == "https"
        self.host = u.hostname
        self.port = u.port or (443 if self.https else 80)
        self.ctx = make_ssl_context(ca_file) if self.https else None
        self.pool, self.pool_size, self.lock = [], pool_size, threading.Lock()

    def _new(self, timeout):
        if self.https:
            return http.client.HTTPSConnection(self.host, self.port, timeout=timeout, context=self.ctx)
        return http.client.HTTPConnection(self.host, self.port, timeout=timeout)

    def _get(self, timeout):
        with self.lock:
            while self.pool:
                c, t = self.pool.pop()
                if time.time() - t < 50:           # nginx keepalive_timeout is 75 s; Cloudflare ~100 s
                    c.timeout = timeout
                    if c.sock is not None:
                        c.sock.settimeout(timeout)
                    return c, True
                c.close()
        return self._new(timeout), False

    def _put(self, c):
        with self.lock:
            if len(self.pool) < self.pool_size:
                self.pool.append((c, time.time()))
                return
        c.close()

    def close(self):
        with self.lock:
            for c, _ in self.pool:
                c.close()
            self.pool = []

    def request(self, method, path, body=None, headers=None, timeout=30.0, stream=False,
                max_body=256 * 1024 * 1024) -> Response:
        hdrs = {"User-Agent": UA}
        hdrs.update(headers or {})
        for attempt in (0, 1):
            c, reused = self._get(timeout)
            try:
                c.request(method, path, body=body, headers=hdrs)
                r = c.getresponse()
            except ssl.SSLCertVerificationError as e:
                c.close()
                raise NetError(f"the server's certificate could not be verified ({e.verify_message})")
            except (http.client.RemoteDisconnected, BrokenPipeError, ConnectionResetError, ConnectionAbortedError,
                    http.client.BadStatusLine) as e:
                c.close()
                if reused and attempt == 0:   # a pooled connection the server had already closed: retry on a new one
                    continue
                raise NetError(f"{e.__class__.__name__}: {e}")
            except (socket.timeout, OSError, ssl.SSLError, http.client.HTTPException) as e:
                c.close()
                raise NetError(_os_err_text(e))
            if stream:
                return Response(r.status, r.msg, None, raw=r, conn=c, pool=self)
            try:
                data = r.read(max_body + 1)
            except (http.client.IncompleteRead, OSError, ssl.SSLError, http.client.HTTPException) as e:
                c.close()
                raise NetError(f"connection broke while reading the answer: {e.__class__.__name__}")
            if len(data) > max_body:
                c.close()
                raise NetError("answer too large")
            enc = (r.getheader("Content-Encoding") or "").lower()
            if enc in ("gzip", "x-gzip"):
                try:
                    data = gzip.decompress(data)
                except (OSError, EOFError, zlib.error):
                    raise NetError("corrupt compressed answer")
            if r.will_close:
                c.close()
            else:
                self._put(c)
            return Response(r.status, r.msg, data)
        raise NetError("connection failed")


def _os_err_text(e) -> str:
    if isinstance(e, socket.timeout):
        return "timed out"
    if isinstance(e, socket.gaierror):
        return "server name not found (DNS)"
    if isinstance(e, ConnectionRefusedError):
        return "connection refused (is the server running at that address?)"
    return f"{e.__class__.__name__}: {e}"


class Server:
    """The configured server + our stored session with it."""

    def __init__(self, config, session):
        self.config, self.session = config, session
        self.conn = None
        self.base = None
        self.lock = threading.Lock()
        self.configure(config["server"])

    def configure(self, base):
        with self.lock:
            if self.conn:
                self.conn.close()
            self.base = base
            self.conn = Conn(base, ca_file=self.config.get("ca_file")) if base else None

    def call(self, method, path, body=None, headers=None, timeout=30.0, stream=False, auth=True,
             ctype=None, max_body=256 * 1024 * 1024, gen=None) -> Response:
        conn = self.conn
        if conn is None:
            raise NetError("no server configured")
        h = {"Accept-Encoding": "identity" if stream else "gzip"}
        h.update(headers or {})
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
            ctype = ctype or "application/json"
        if ctype:
            h["Content-Type"] = ctype
        if auth:
            ck = self.session.cookie_header()
            if ck:
                h["Cookie"] = ck
            if method in MUTATING:
                h["X-CSRF-Token"] = self.session.csrf
        r = conn.request(method, path, body=body, headers=h, timeout=timeout, stream=stream, max_body=max_body)
        if auth:
            self.session.absorb_set_cookies(r.headers.get_all("Set-Cookie") or [], gen=gen)
        return r

    # ---- addresses: the configured server first, then its alternates (same server through another route,
    # e.g. the Cloudflare tunnel + the Tailscale address); the session is the server's, so it works on all of them
    def addresses(self):
        out = []
        for b in [self.config["server"]] + list(self.config.get("alt_servers") or []):
            if b and b not in out:
                out.append(b)
        return out

    @staticmethod
    def _info_of(r) -> dict:
        d = r.json()
        if r.status != 200 or not isinstance(d, dict) or d.get("app") != "cyber-scout":
            raise HTTPError(r.status, r.body, "this address answers, but it is not a Cyber-Scout server "
                            f"(GET /app/v1/info: HTTP {r.status})")
        return d

    def info_at(self, base, timeout=5.0) -> dict:
        """GET /app/v1/info from another address (no cookies sent)."""
        c = Conn(base, ca_file=self.config.get("ca_file"), pool_size=0)
        try:
            return self._info_of(c.request("GET", "/app/v1/info", headers={"Accept": "application/json"},
                                           timeout=timeout, max_body=64 * 1024))
        finally:
            c.close()

    # ---- session
    def info(self, timeout=5.0) -> dict:
        return self._info_of(self.call("GET", "/app/v1/info", timeout=timeout, auth=False))

    def refresh_session(self, timeout=15.0):
        gen = self.session.gen
        r = self.call("GET", "/app/v1/session", timeout=timeout, gen=gen)
        d = r.json()
        if r.status != 200 or not isinstance(d, dict):
            raise HTTPError(r.status, r.body)
        self.session.absorb_session_json(d, gen=gen)
        return d

    def login(self, username, password) -> dict:
        if not self.session.csrf:
            self.refresh_session()
        r = self.call("POST", "/app/v1/auth/login", {"username": username, "password": password})
        if r.status == 403 and "csrf" in detail_text(r.body).lower():
            self.refresh_session()
            r = self.call("POST", "/app/v1/auth/login", {"username": username, "password": password})
        d = r.json()
        if r.status != 200 or not isinstance(d, dict):
            raise HTTPError(r.status, d)
        self.session.absorb_session_json(d)
        return d

    def logout(self):
        try:
            self.call("POST", "/app/v1/auth/logout", timeout=10)
        finally:
            self.session.clear()
