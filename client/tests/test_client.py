"""Cyber-Scout Client tests against the fake part-1 server.  Run:  python3 -m unittest discover -s client/tests -v"""
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from csclient.app import App                      # noqa: E402
from csclient.mirror import extract_bundle        # noqa: E402
from csclient.util import safe_relpath            # noqa: E402
from fakeserver import FakeServer                 # noqa: E402

import logging                                    # noqa: E402
logging.getLogger("cyberscout").setLevel(logging.CRITICAL if not os.environ.get("CS_TEST_LOG") else logging.DEBUG)
if os.environ.get("CS_TEST_LOG"):
    logging.getLogger("cyberscout").addHandler(logging.StreamHandler())

EV = "2026test"


def j(b):
    return hashlib.sha256(b).hexdigest()


class Local:
    """A browser-ish caller of the client: carries the token + CSRF cookies and a same-origin Origin header."""

    def __init__(self, app, token=True, origin=True):
        self.app = app
        self.base = f"http://127.0.0.1:{app.port}"
        self.token, self.origin = token, origin
        self.host = None

    def req(self, method, path, body=None, headers=None, raw=False):
        h = {}
        ck = []
        if self.token is True:
            ck.append(f"cs_client={self.app.config['token']}")
        elif self.token:
            ck.append(f"cs_client={self.token}")
        ck.append(f"cs_csrf={self.app.config['local_csrf']}")
        h["Cookie"] = "; ".join(ck)
        if self.origin is True:
            h["Origin"] = self.base
        elif self.origin:
            h["Origin"] = self.origin
        if self.host:
            h["Host"] = self.host
        if method not in ("GET", "HEAD"):
            h["X-CSRF-Token"] = self.app.config["local_csrf"]
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            resp = urllib.request.urlopen(r, timeout=30)
            code, hdrs, b = resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as e:
            code, hdrs, b = e.code, e.headers, e.read()
        if raw:
            return code, hdrs, b
        try:
            return code, json.loads(b.decode()) if b else None
        except ValueError:
            return code, b


class Base(unittest.TestCase):
    def setUp(self):
        self.fs = FakeServer().start()
        st = self.fs.state
        self.files = {
            f"api/{EV}/event.json": b'{"key":"2026test","matches":[]}',
            f"api/{EV}/m/qm1.json": b'{"m":1}' * 100,
            f"api/{EV}/m/qm2.json": b'{"m":2}' * 100,
            f"api/{EV}/t/frc4911.json": os.urandom(300_000),       # incompressible: bundles are big enough to cut
            "api/events.json": b'[{"key":"2026test"}]',
        }
        for p, b in self.files.items():
            st.put(p, b)
        self.tmp = tempfile.mkdtemp(prefix="csc-test-")
        self.app = App(os.path.join(self.tmp, "data"), port=0, server_url=self.fs.url,
                       bundled_web=os.path.join(HERE, "nonexistent"))
        self.app.config.update(interval=3600, probe_interval=3600)
        self.app.start()
        self.c = Local(self.app)
        self.wait(lambda: self.app.engine.online, "online")

    def tearDown(self):
        self.app.shutdown()
        self.fs.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def wait(self, cond, what, timeout=20):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if cond():
                return
            time.sleep(0.05)
        self.fail("timed out waiting for " + what)

    def login(self):
        code, d = self.c.req("POST", "/client/v1/login", {"username": "scout", "password": "pw-scout-1"})
        self.assertEqual(code, 200, d)
        return d

    def sync(self, **kw):
        e = self.app.engine
        n = e.cycles
        code, d = self.c.req("POST", "/client/v1/sync", kw)
        self.assertEqual(code, 202, d)
        self.wait(lambda: e.cycles > n and not e.syncing, "sync cycle", timeout=40)
        return self.status()

    def status(self):
        code, d = self.c.req("GET", "/client/v1/status")
        self.assertEqual(code, 200)
        return d

    def local(self, rel):
        p = os.path.join(self.app.engine.site_root, *rel.split("/"))
        with open(p, "rb") as f:
            return f.read()

    def bundle_requests(self):
        return [r for r in self.fs.state.requests if r[1].startswith("/app/v1/sync/bundle")]


class TestSync(Base):
    def test_first_sync_delta_and_deletes(self):
        self.login()
        # login answers the page's way: user + role, no server secrets
        s = self.sync()
        self.assertTrue(s["signed_in"])
        self.assertEqual(s["user"], {"display_name": "Scout One", "role": "scout"})
        self.assertEqual(s["errors"], [], s["errors"])
        for p, b in self.files.items():
            self.assertEqual(self.local(p), b, p)
        ev = [e for e in s["events"] if e["key"] == EV][0]
        self.assertEqual(ev["files"], len(self.files))
        self.assertIsNotNone(s["last_sync"])
        # served to the page from the mirror
        code, d = self.c.req("GET", f"/api/{EV}/event.json")
        self.assertEqual((code, d), (200, {"key": "2026test", "matches": []}))

        # delta: one changed, one added, one deleted
        st = self.fs.state
        st.put(f"api/{EV}/m/qm1.json", b'{"m":"changed"}')
        st.put(f"api/{EV}/m/qm3.json", b'{"m":3}')
        st.delete(f"api/{EV}/m/qm2.json")
        st.requests.clear()
        s = self.sync()
        self.assertEqual(s["errors"], [])
        man = [r for r in st.requests if r[1].startswith("/app/v1/sync/manifest")]
        self.assertIn("since=", man[0][1])
        bundles = self.bundle_requests()
        self.assertEqual(len(bundles), 1)
        self.assertEqual(self.local(f"api/{EV}/m/qm1.json"), b'{"m":"changed"}')
        self.assertEqual(self.local(f"api/{EV}/m/qm3.json"), b'{"m":3}')
        self.assertFalse(os.path.exists(os.path.join(self.app.engine.site_root, "api", EV, "m", "qm2.json")))
        code, _ = self.c.req("GET", f"/api/{EV}/m/qm2.json")
        self.assertEqual(code, 404)
        # nothing changed -> no bundle at all
        st.requests.clear()
        self.sync()
        self.assertEqual(self.bundle_requests(), [])

    def test_unknown_cursor_gets_full_and_prunes(self):
        self.login()
        self.sync()
        st = self.fs.state
        # the server forgot its history (cursor from the future) -> full manifest; files missing from it are removed
        with st.lock:
            st.files.pop(f"api/{EV}/m/qm2.json")
            st.mtimes.pop(f"api/{EV}/m/qm2.json")
            st.counter = 0
        self.sync()
        self.assertFalse(os.path.exists(os.path.join(self.app.engine.site_root, "api", EV, "m", "qm2.json")))
        self.assertEqual(self.local(f"api/{EV}/m/qm1.json"), self.files[f"api/{EV}/m/qm1.json"])

    def test_resume_after_cut_connection(self):
        self.login()
        self.fs.state.cut_bundle_after = 100_000
        s = self.sync()
        self.assertEqual(s["errors"], [], s["errors"])
        bundles = self.bundle_requests()
        self.assertGreaterEqual(len(bundles), 2)
        self.assertNotIn("Range", bundles[0][2])
        self.assertEqual(bundles[1][2].get("Range"), "bytes=100000-")
        for p, b in self.files.items():
            self.assertEqual(self.local(p), b)
        self.assertEqual(os.listdir(self.app.engine.parts_dir), [])

    def test_resume_across_restart(self):
        """A partial bundle left on disk (client quit mid-download) is resumed, not restarted."""
        eng = self.app.engine
        paths = sorted(self.files)
        data = self.fs.state.bundle(paths)
        part, meta = eng._part_paths("bundle", [EV, paths])
        with open(part, "wb") as f:
            f.write(data[:150_000])
        with open(meta, "w") as f:
            json.dump({"path": "/app/v1/sync/bundle"}, f)
        self.login()
        self.sync()
        b = self.bundle_requests()
        self.assertEqual(b[0][2].get("Range"), "bytes=150000-")
        for p, v in self.files.items():
            self.assertEqual(self.local(p), v)

    def test_chunking(self):
        import csclient.sync as S
        old = S.CHUNK_BYTES
        S.CHUNK_BYTES = 1000
        try:
            self.login()
            s = self.sync()
            self.assertEqual(s["errors"], [])
            self.assertGreaterEqual(len(self.bundle_requests()), 3)
            for p, v in self.files.items():
                self.assertEqual(self.local(p), v)
        finally:
            S.CHUNK_BYTES = old

    def test_big_file_fetched_alone(self):
        import csclient.sync as S
        old = S.BIG_FILE
        S.BIG_FILE = 100_000
        try:
            self.login()
            s = self.sync()
            self.assertEqual(s["errors"], [], s["errors"])
            gets = [r for r in self.fs.state.requests if r[1] == f"/api/{EV}/t/frc4911.json"]
            self.assertEqual(len(gets), 1)
            for p, v in self.files.items():
                self.assertEqual(self.local(p), v)
        finally:
            S.BIG_FILE = old

    def test_media_proxy_mode(self):
        st = self.fs.state
        vid = os.urandom(200_000)
        st.media[f"media/{EV}/proxy/{EV}_qm1.mp4"] = (vid, "proxy", "qm1")
        st.media[f"media/{EV}/{EV}_qm1.mp4"] = (os.urandom(1000), "wide", "qm1")
        self.login()
        self.c.req("PUT", "/client/v1/config", {"media": "proxy"})
        s = self.sync()
        self.assertEqual(s["errors"], [], s["errors"])
        self.assertEqual(self.local(f"media/{EV}/proxy/{EV}_qm1.mp4"), vid)
        self.assertFalse(os.path.exists(os.path.join(self.app.engine.site_root, "media", EV, f"{EV}_qm1.mp4")))
        # the page asks for the full video: offline it gets the local proxy copy, with Range for seeking
        self.fs.state.down = True
        self.app.engine.set_online(False, "test")
        code, hdrs, b = self.c.req("GET", f"/media/{EV}/{EV}_qm1.mp4", headers={"Range": "bytes=10-19"}, raw=True)
        self.assertEqual(code, 206)
        self.assertEqual(b, vid[10:20])
        self.assertEqual(hdrs["Content-Range"], f"bytes 10-19/{len(vid)}")

    def test_web_mirror_and_refresh(self):
        self.wait(lambda: self.app.mirror.version == "v1", "web mirror")
        code, hdrs, b = self.c.req("GET", "/", raw=True)
        self.assertEqual(code, 200)
        self.assertIn(b"app.js", b)
        self.assertIn("cs_client=", hdrs.get("Set-Cookie", "") + " ".join(hdrs.get_all("Set-Cookie") or []))
        for rel in ("app.js", "style.css", "theme.js", "fonts/a.woff2", "field.jpg", "sw.js"):
            code, _, body = self.c.req("GET", "/" + rel + "?v=abc", raw=True)
            self.assertEqual((code, body), (200, self.fs.state.web[rel]), rel)
        root = self.app.mirror.root()
        self.assertFalse(os.path.exists(os.path.join(root, "event.json")))   # html fallback was not mirrored
        code, _, _ = self.c.req("GET", "/missing.js", raw=True)
        self.assertEqual(code, 404)
        code, _, body = self.c.req("GET", "/some/spa/route", raw=True)
        self.assertEqual(body, self.fs.state.web["index.html"])
        # new server version -> new copy
        self.fs.state.web["app.js"] = b"const v2=1;"
        self.fs.state.version = "v2"
        self.c.req("POST", "/client/v1/sync", {})
        self.wait(lambda: self.app.mirror.version == "v2", "web mirror v2")
        code, _, body = self.c.req("GET", "/app.js", raw=True)
        self.assertEqual(body, b"const v2=1;")
        self.assertEqual(len([d for d in os.listdir(self.app.mirror.web_dir) if d.startswith("v-")]), 1)


class TestOutbox(Base):
    def post_note(self, text, key=None):
        h = {"Idempotency-Key": key} if key else None
        return self.c.req("POST", f"/app/v1/events/{EV}/notes", {"text": text}, headers=h)

    def test_online_write_passes_through(self):
        self.login()
        code, d = self.post_note("hello")
        self.assertEqual(code, 201, d)
        self.assertEqual(d["text"], "hello")
        self.assertEqual(self.fs.state.applied, 1)
        self.assertEqual(self.status()["outbox"], 0)
        # the server saw an Idempotency-Key and its own CSRF token, not the page's
        req = [r for r in self.fs.state.requests if r[1].endswith("/notes")][0]
        self.assertTrue(req[2].get("Idempotency-Key"))
        self.assertNotEqual(req[2].get("X-CSRF-Token"), self.app.config["local_csrf"])

    def test_offline_queue_then_flush_exactly_once(self):
        self.login()
        self.fs.state.down = True
        self.app.engine.set_online(False, "test")
        code, d = self.post_note("one")
        self.assertEqual(code, 202, d)
        self.assertTrue(d["queued"])
        code2, d2 = self.post_note("two")
        self.assertEqual(code2, 202)
        # retried by the page with the same key: not queued twice
        code3, d3 = self.post_note("two", key=d2["key"])
        self.assertEqual(d3["id"], d2["id"])
        s = self.status()
        self.assertEqual(s["outbox"], 2)
        self.assertFalse(s["online"])
        code, items = self.c.req("GET", "/client/v1/outbox")
        self.assertEqual([i["body"]["text"] for i in items], ["one", "two"])
        # the first send is applied but its answer is lost -> resent with the same key -> replayed, not re-applied
        self.fs.state.drop_response_once.add(f"/app/v1/events/{EV}/notes")
        self.fs.state.down = False
        self.c.req("POST", "/client/v1/sync", {})
        self.wait(lambda: self.app.db.pending() == 0 and self.app.engine.online, "flush", timeout=60)
        self.assertEqual([n["text"] for n in self.fs.state.notes], ["one", "two"])
        self.assertEqual(self.fs.state.applied, 2)
        sent = [r for r in self.fs.state.requests if r[1].endswith("/notes")]
        keys = [r[2]["Idempotency-Key"] for r in sent]
        self.assertGreaterEqual(len(sent), 3)                  # one resend
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(self.status()["outbox"], 0)

    def test_write_while_offline_survives_restart(self):
        self.login()
        self.fs.state.down = True
        self.app.engine.set_online(False, "test")
        self.post_note("durable")
        self.app.shutdown()
        self.app = App(self.app.data_dir, port=0, server_url=self.fs.url, bundled_web=os.path.join(HERE, "x"))
        self.app.config.update(interval=3600, probe_interval=3600)
        self.c = Local(self.app)
        self.fs.state.down = False
        self.app.start()
        self.wait(lambda: self.app.db.pending() == 0 and self.fs.state.applied == 1, "flush after restart")
        self.assertEqual(self.fs.state.notes[0]["text"], "durable")

    def test_conflict_surfaced(self):
        self.login()
        self.fs.state.down = True
        self.app.engine.set_online(False, "test")
        code, d = self.c.req("PUT", f"/app/v1/events/{EV}/picklists/main", {"base_version": 1, "teams": ["frc2"]})
        self.assertEqual(code, 202)
        self.fs.state.picklist = {"version": 5, "teams": ["frc9"]}   # someone else changed it meanwhile
        self.fs.state.down = False
        self.c.req("POST", "/client/v1/sync", {})
        self.wait(lambda: self.app.db.count("conflict") == 1, "conflict")
        s = self.status()
        conf = [e for e in s["errors"] if e["kind"] == "conflict"]
        self.assertEqual(len(conf), 1)
        self.assertEqual(conf[0]["response"]["detail"]["current"]["version"], 5)
        self.assertEqual(s["outbox"], 0)
        code, items = self.c.req("GET", "/client/v1/outbox")
        self.assertEqual(items[0]["state"], "conflict")
        code, _ = self.c.req("DELETE", f"/client/v1/outbox/{items[0]['id']}")
        self.assertEqual(code, 204)
        self.assertEqual([e for e in self.status()["errors"] if e["kind"] == "conflict"], [])

    def test_online_conflict_answered_directly(self):
        self.login()
        code, d = self.c.req("PUT", f"/app/v1/events/{EV}/picklists/main", {"base_version": 0, "teams": []})
        self.assertEqual(code, 409)
        self.assertEqual(d["detail"]["current"]["version"], 1)
        self.assertEqual(self.app.db.items(), [])

    def test_offline_reads(self):
        self.login()
        code, d = self.c.req("GET", "/app/v1/session")
        self.assertEqual(code, 200)
        self.assertEqual(d["csrf"], self.app.config["local_csrf"])          # never the server's token
        self.fs.state.down = True
        self.app.engine.set_online(False, "test")
        code, d = self.c.req("GET", "/app/v1/session")
        self.assertEqual(code, 200)
        self.assertEqual(d["user"]["display_name"], "Scout One")
        self.assertTrue(d.get("offline"))
        code, d = self.c.req("GET", f"/app/v1/events/{EV}/whatever")
        self.assertEqual(code, 503)
        self.assertTrue(d["offline"])


class TestSecurity(Base):
    def test_session_never_reaches_the_page(self):
        code, hdrs, b = self.c.req("POST", "/app/v1/auth/login", {"username": "scout", "password": "pw-scout-1"},
                                   raw=True)
        self.assertEqual(code, 200, b)
        cookies = " ".join(hdrs.get_all("Set-Cookie") or [])
        self.assertNotIn("cs_session", cookies)
        server_tok = self.app.session.d["cookies"]["cs_session"]
        self.assertNotIn(server_tok.encode(), b)
        self.assertNotIn(self.app.session.csrf.encode(), b)
        self.assertEqual(json.loads(b)["csrf"], self.app.config["local_csrf"])
        sess_file = os.path.join(self.app.data_dir, "session.json")
        if os.name != "nt":
            self.assertEqual(os.stat(sess_file).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(os.path.join(self.app.data_dir, "config.json")).st_mode & 0o777, 0o600)

    def test_control_api_protection(self):
        ok = self.c.req("GET", "/client/v1/status")[0]
        self.assertEqual(ok, 200)
        no_tok = Local(self.app, token=False)
        self.assertEqual(no_tok.req("GET", "/client/v1/status")[0], 403)
        bad_tok = Local(self.app, token="x" * 43)
        self.assertEqual(bad_tok.req("POST", "/client/v1/sync", {})[0], 403)
        evil = Local(self.app, origin="https://evil.example")
        self.assertEqual(evil.req("PUT", "/client/v1/config", {"server": "https://evil.example"})[0], 403)
        other_port = Local(self.app, origin="http://127.0.0.1:8080")       # cookies are not port-isolated
        self.assertEqual(other_port.req("POST", "/client/v1/login", {"username": "a", "password": "b"})[0], 403)
        null = Local(self.app, origin="null")
        self.assertEqual(null.req("GET", "/client/v1/status")[0], 403)
        xsite = Local(self.app, origin=False)
        self.assertEqual(xsite.req("GET", "/client/v1/status", headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(xsite.req("GET", "/client/v1/status", headers={"Sec-Fetch-Site": "same-site"})[0], 403)
        rebind = Local(self.app)
        rebind.host = "attacker.example:%d" % self.app.port                 # DNS rebinding
        self.assertEqual(rebind.req("GET", "/client/v1/status")[0], 403)
        self.assertEqual(rebind.req("GET", "/")[0], 403)
        # the same gate covers the data and the proxy
        self.assertEqual(no_tok.req("GET", f"/api/{EV}/event.json")[0], 403)
        self.assertEqual(evil.req("GET", "/app/v1/session")[0], 403)
        # page writes need the local CSRF token
        code, _ = self.c.req("POST", f"/app/v1/events/{EV}/notes", {"text": "x"}, headers={"X-CSRF-Token": "nope"})
        self.assertEqual(code, 403)
        # config is validated
        self.assertEqual(self.c.req("PUT", "/client/v1/config", {"media": "everything"})[0], 422)
        self.assertEqual(self.c.req("PUT", "/client/v1/config", {"server": "ftp://x"})[0], 422)
        self.assertEqual(self.c.req("PUT", "/client/v1/config", {"events": ["../etc"]})[0], 422)

    def test_test_endpoint(self):
        code, d = self.c.req("POST", "/client/v1/test", {"server": self.fs.url})
        self.assertEqual(code, 200)
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["version"], "v1")
        self.assertIsInstance(d["latency_ms"], int)
        code, d = self.c.req("POST", "/client/v1/test", {"server": "http://127.0.0.1:9"})
        self.assertFalse(d["ok"])
        self.assertIn("refused", d["error"])

    def test_login_errors_and_logout(self):
        code, d = self.c.req("POST", "/client/v1/login", {"username": "scout", "password": "wrong"})
        self.assertEqual(code, 401)
        self.assertIn("invalid", d["detail"])
        self.login()
        self.assertTrue(self.status()["signed_in"])
        code, _ = self.c.req("POST", "/client/v1/logout", {})
        self.assertEqual(code, 200)
        s = self.status()
        self.assertFalse(s["signed_in"])
        self.assertEqual(self.fs.state.sessions, {})


def make_tar(members):
    """members: list of (TarInfo kwargs, data|None)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for kw, data in members:
            ti = tarfile.TarInfo(kw.pop("name"))
            for k, v in kw.items():
                setattr(ti, k, v)
            if data is not None:
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
            else:
                tf.addfile(ti)
    return buf.getvalue()


class TestPathSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="csc-tar-")
        self.root = os.path.join(self.tmp, "site")
        os.makedirs(self.root)
        self.tmpd = os.path.join(self.tmp, "tmp")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_safe_relpath(self):
        good = {"api/2026x/a.json": "api/2026x/a.json", "api//x/./y.json": "api/x/y.json",
                "media/ev/proxy/q.mp4": "media/ev/proxy/q.mp4"}
        for a, b in good.items():
            self.assertEqual(safe_relpath(a), b)
        for bad in ("/etc/passwd", "../x", "api/../../x", "api/x/../../../y", "C:/x", "c:\\x", "api\\..\\x",
                    "web/index.html", "", "api/x\x00y", "api/CON", "api/x/nul.json", "api/x. ", "\\\\srv\\share"):
            self.assertIsNone(safe_relpath(bad), bad)

    def test_hostile_members_rejected(self):
        ok = b'{"ok":1}'
        evil = b"pwned"
        tar = make_tar([
            ({"name": "/tmp/abs.json"}, evil),
            ({"name": "../escape.json"}, evil),
            ({"name": "api/2026x/../../../escape2.json"}, evil),
            ({"name": "api/2026x/link.json", "type": tarfile.SYMTYPE, "linkname": "/etc/passwd"}, None),
            ({"name": "api/2026x/hard.json", "type": tarfile.LNKTYPE, "linkname": "api/2026x/ok.json"}, None),
            ({"name": "api/2026x/dev", "type": tarfile.CHRTYPE, "devmajor": 1, "devminor": 3}, None),
            ({"name": "api/2026x/fifo", "type": tarfile.FIFOTYPE}, None),
            ({"name": "api/2026x/unrequested.json"}, evil),
            ({"name": "api/2026x/badhash.json"}, evil),
            ({"name": "api/2026x/ok.json"}, ok),
        ])
        p = os.path.join(self.tmp, "b.tar.gz")
        with open(p, "wb") as f:
            f.write(tar)
        expected = {"api/2026x/ok.json": (j(ok), len(ok)), "api/2026x/badhash.json": (j(b"other"), len(evil)),
                    "api/2026x/link.json": (j(ok), 0), "api/2026x/hard.json": (j(ok), 0),
                    "api/2026x/dev": (j(b""), 0), "api/2026x/fifo": (j(b""), 0)}
        written, problems, corrupt = extract_bundle(p, self.root, expected, self.tmpd)
        self.assertFalse(corrupt)
        self.assertEqual(set(written), {"api/2026x/ok.json"})
        self.assertEqual(len(problems), 9, problems)
        found = []
        for d, dirs, fs in os.walk(self.tmp):
            for f in fs:
                found.append(os.path.relpath(os.path.join(d, f), self.tmp))
        self.assertEqual(sorted(found), ["b.tar.gz", os.path.join("site", "api", "2026x", "ok.json")])
        for d, dirs, fs in os.walk(self.root):
            for x in dirs + fs:
                self.assertFalse(os.path.islink(os.path.join(d, x)))

    def test_symlinked_dir_in_mirror_not_followed(self):
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        os.makedirs(os.path.join(self.root, "api"))
        os.symlink(outside, os.path.join(self.root, "api", "2026x"))
        data = b"x"
        p = os.path.join(self.tmp, "b.tar.gz")
        with open(p, "wb") as f:
            f.write(make_tar([({"name": "api/2026x/a.json"}, data)]))
        written, problems, _ = extract_bundle(p, self.root, {"api/2026x/a.json": (j(data), 1)}, self.tmpd)
        self.assertEqual(written, {})
        self.assertEqual(os.listdir(outside), [])

    def test_truncated_bundle(self):
        a, b = os.urandom(5000), os.urandom(5000)
        tar = make_tar([({"name": "api/e/a.bin"}, a), ({"name": "api/e/b.bin"}, b)])
        p = os.path.join(self.tmp, "b.tar.gz")
        with open(p, "wb") as f:
            f.write(tar[: len(tar) - 3000])
        written, problems, corrupt = extract_bundle(p, self.root, {"api/e/a.bin": (j(a), 5000),
                                                                   "api/e/b.bin": (j(b), 5000)}, self.tmpd)
        self.assertTrue(corrupt)
        self.assertNotIn("api/e/b.bin", written)
        self.assertFalse(os.path.exists(os.path.join(self.root, "api", "e", "b.bin")))


if __name__ == "__main__":
    unittest.main()
