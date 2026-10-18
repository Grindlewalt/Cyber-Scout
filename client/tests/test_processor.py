"""Processor mode ("Help process matches", csclient/processor.py) against the fake server + fake processing API.
Run:  python3 -m unittest discover -s client/tests -v"""
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import csclient.processor as PR                  # noqa: E402
from csclient.app import App                      # noqa: E402
from fakeprocessing import FakeProcessing, wait_for   # noqa: E402
from fakeserver import FakeServer                 # noqa: E402
from test_client import Local                     # noqa: E402

import logging                                    # noqa: E402
logging.getLogger("cyberscout").setLevel(logging.CRITICAL if not os.environ.get("CS_TEST_LOG") else logging.DEBUG)

EV = "2026test"


class Base(unittest.TestCase):
    modes = ("ok",)

    def setUp(self):
        self._imports, self._beat, self._idle = PR.IMPORTS, PR.MIN_BEAT, PR.IDLE_S
        PR.IMPORTS = ()                           # the test Python has no onnxruntime; the fake runner needs none
        PR.MIN_BEAT = 0.3
        PR.IDLE_S = 0.5
        self.fs = FakeServer().start()
        self.proc = self.fs.state.proc = FakeProcessing(self.modes)
        self.tmp = tempfile.mkdtemp(prefix="csc-proc-")
        self.app = App(os.path.join(self.tmp, "data"), port=0, server_url=self.fs.url,
                       bundled_web=os.path.join(HERE, "nonexistent"))
        self.app.config.update(interval=3600, probe_interval=3600)
        self.app.start()
        self.c = Local(self.app)
        self.assertTrue(wait_for(lambda: self.app.engine.online), "online")

    def tearDown(self):
        self.app.shutdown()
        self.fs.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        PR.IMPORTS, PR.MIN_BEAT, PR.IDLE_S = self._imports, self._beat, self._idle

    def login(self, user="coach", pw="pw-admin-1"):
        code, d = self.c.req("POST", "/client/v1/login", {"username": user, "password": pw})
        self.assertEqual(code, 200, d)

    def turn_on(self, python=sys.executable):
        code, d = self.c.req("PUT", "/client/v1/processing", {"python": python, "enabled": True})
        self.assertEqual(code, 200, d)
        return d

    def pstatus(self):
        code, d = self.c.req("GET", "/client/v1/processing")
        self.assertEqual(code, 200)
        return d


class TestProcessing(Base):
    modes = ("ok", "ok")

    def test_processes_matches_end_to_end(self):
        self.login()
        self.turn_on()
        P = self.proc
        self.assertTrue(wait_for(lambda: len(P.completed) == 2), (self.pstatus(), P.released))
        k1 = f"{EV}_qm1"
        self.assertEqual(P.completed, [k1, f"{EV}_qm2"])
        up = P.uploads[k1]
        self.assertEqual(sorted(up), ["analytics", "fuel", "matches", "shots"])
        m = up["matches"]
        self.assertEqual((m["key"], m["feed_ok"], m["source"]), (k1, 0.95, "livestream"))
        self.assertEqual(m["live"]["record_path"], "/srv/media/live/x/matches/qm1.json")   # the server's record
        self.assertEqual(m["env"].get("FORCE_CPU"), "1" if sys.platform == "darwin" else None)
        self.assertTrue(m["work"].startswith(os.path.join(self.app.data_dir, "processing", "work")))
        # code + model downloaded once and cached by version / checksum
        self.assertEqual((P.code_gets, P.model_gets), (1, 1))
        root = os.path.join(self.app.data_dir, "processing")
        self.assertTrue(os.path.isfile(os.path.join(root, "pipelines", P.version, "runner.py")))
        self.assertEqual(len(os.listdir(os.path.join(root, "models"))), 1)
        self.assertEqual(os.listdir(os.path.join(root, "work", EV)), [])                 # cleaned up
        self.assertTrue(os.path.isfile(os.path.join(root, "logs", f"{k1}.log")))
        b = P.claim_bodies[0]
        self.assertEqual((b["pipeline"], b["processor"]), (P.version, self.app.config["process_id"]))
        self.assertTrue(wait_for(lambda: self.pstatus()["state"] == "idle"))
        s = self.pstatus()
        self.assertEqual((s["done"], s["failed"], s["last"]["key"]), (2, 0, f"{EV}_qm2"))
        self.assertTrue(s["line"].startswith("idle, done 2"), s["line"])
        self.assertEqual(self.c.req("GET", "/client/v1/status")[1]["processing"]["done"], 2)
        self.assertGreaterEqual(len(P.beats), 0)

    def test_turn_off_stops(self):
        self.login()
        self.turn_on()
        self.assertTrue(wait_for(lambda: len(self.proc.completed) == 2))
        code, d = self.c.req("PUT", "/client/v1/processing", {"enabled": False})
        self.assertEqual((code, d["enabled"]), (200, False))
        self.assertTrue(wait_for(lambda: self.pstatus()["state"] == "off"))
        self.assertEqual(self.pstatus()["line"], "off")


class TestResume(Base):
    def test_clip_download_resumes_with_range(self):
        self.proc.cut_clip_after = 150_000
        self.login()
        self.turn_on()
        self.assertTrue(wait_for(lambda: self.proc.completed), self.pstatus())
        clips = [r for r in self.fs.state.requests if "/clip?" in r[1]]
        self.assertEqual(len(clips), 2)
        self.assertEqual(clips[1][2].get("Range"), "bytes=150000-")


class TestFailure(Base):
    modes = ("fail", "ok")

    def test_failed_match_is_released_and_skipped(self):
        self.login()
        self.turn_on()
        P = self.proc
        self.assertTrue(wait_for(lambda: P.completed == [f"{EV}_qm2"]), (self.pstatus(), P.released))
        self.assertEqual(P.released[0], (f"{EV}_qm1", True, "only 2 AprilTags found"))
        self.assertNotIn(f"{EV}_qm1", P.uploads)
        self.assertIn(f"{EV}_qm1", P.claim_bodies[-1]["skip"])
        s = self.pstatus()
        self.assertEqual((s["failed"], s["done"]), (1, 1))


class TestLostClaim(Base):
    modes = ("slow",)

    def test_lost_claim_stops_the_pipeline_without_uploading(self):
        self.login()
        self.turn_on()
        P = self.proc
        self.assertTrue(wait_for(lambda: self.pstatus()["state"] == "processing"), self.pstatus())
        t0 = time.time()
        P.lose_claim = True
        self.assertTrue(wait_for(lambda: self.pstatus()["state"] in ("idle", "checking")), self.pstatus())
        self.assertLess(time.time() - t0, 10)                 # the slow runner (30 s) was stopped
        self.assertEqual((P.uploads, P.completed), ({}, []))
        self.assertIn(f"{EV}_qm1", self.app.processor.skip)


class TestTurnOffMidJob(Base):
    modes = ("slow",)

    def test_turning_off_mid_job_gives_the_match_back(self):
        self.login()
        self.turn_on()
        self.assertTrue(wait_for(lambda: self.pstatus()["state"] == "processing" and (self.pstatus()["pct"] or 0) > 5))
        s = self.pstatus()
        self.assertRegex(s["line"], r"^processing qm1 at \d+%, done 0$")
        self.c.req("PUT", "/client/v1/processing", {"enabled": False})
        self.assertTrue(wait_for(lambda: self.proc.released), self.pstatus())
        self.assertEqual(self.proc.released[0], (f"{EV}_qm1", False, "stopped"))
        self.assertTrue(wait_for(lambda: self.pstatus()["state"] == "off"))


class TestNeeds(Base):
    def test_non_admin_waits(self):
        self.login("scout", "pw-scout-1")
        self.turn_on()
        self.assertTrue(wait_for(lambda: self.pstatus()["state"] == "waiting"))
        self.assertIn("admin", self.pstatus()["message"])
        self.assertEqual(self.proc.claim_bodies, [])

    def test_no_python_needs_setup(self):
        self.login()
        code, d = self.c.req("PUT", "/client/v1/processing", {"enabled": True})
        self.assertEqual(code, 200)
        self.assertTrue(wait_for(lambda: self.pstatus()["state"] == "needs_setup"), self.pstatus())
        self.assertIn("Set up processing", self.pstatus()["message"])
        self.assertEqual(self.proc.claim_bodies, [])

    def test_python_without_packages_needs_setup(self):
        PR.IMPORTS = ("surely_not_a_module_xyz",)
        self.login()
        self.turn_on()
        self.assertTrue(wait_for(lambda: self.pstatus()["state"] == "needs_setup"), self.pstatus())
        self.assertIn("surely_not_a_module_xyz", self.pstatus()["message"])

    def test_control_api_validation_and_page(self):
        for body in ({"python": "relative/python"}, {"python": "/no/such/python"}, {"enabled": "yes"},
                     {"events": ["not an event"]}):
            code, d = self.c.req("PUT", "/client/v1/processing", body)
            self.assertEqual(code, 422, (body, d))
        code, d = self.c.req("PUT", "/client/v1/processing", {"events": [EV]})
        self.assertEqual((code, d["events"]), (200, [EV]))
        self.assertEqual(Local(self.app, token=False).req("GET", "/client/v1/processing")[0], 403)
        code, h, b = self.c.req("GET", "/client/processing", raw=True)
        self.assertEqual(code, 200)
        self.assertIn(b"Help process matches", b)
        self.assertIn("script-src 'self'", h["Content-Security-Policy"])
        self.assertIn("cs_client=", " ".join(h.get_all("Set-Cookie")))
        code, h, b = self.c.req("GET", "/client/processing.js", raw=True)
        self.assertEqual((code, h["Content-Type"].split(";")[0]), (200, "text/javascript"))


class TestUnits(unittest.TestCase):
    def test_status_line(self):
        L = PR.Processor.line
        self.assertEqual(L({"enabled": True, "state": "processing", "key": f"{EV}_qm12", "pct": 40, "done": 3}),
                         "processing qm12 at 40%, done 3")
        self.assertEqual(L({"enabled": True, "state": "idle", "done": 2, "failed": 1, "message": None}),
                         "idle, done 2, failed 1")
        self.assertEqual(L({"enabled": False, "state": "off"}), "off")
        self.assertEqual(L({"enabled": True, "state": "needs_setup", "message": "choose a Python"}),
                         "needs_setup: choose a Python")

    def test_extract_code_refuses_unsafe_archives(self):
        tmp = tempfile.mkdtemp(prefix="csc-x-")
        try:
            def tar(entries):
                p = os.path.join(tmp, "c.tar.gz")
                with tarfile.open(p, "w:gz") as t:
                    for name, data, kind in entries:
                        ti = tarfile.TarInfo(name)
                        if kind == "sym":
                            ti.type, ti.linkname = tarfile.SYMTYPE, "/etc/passwd"
                            t.addfile(ti)
                        else:
                            ti.size = len(data)
                            t.addfile(ti, io.BytesIO(data))
                return p
            ok = [("runner.py", b"x", "f"), ("live/worker.py", b"y", "f")]
            for bad in ([("../evil.py", b"x", "f")], [("/abs.py", b"x", "f")], [("live/l", b"", "sym")],
                        [("other/x.py", b"x", "f")]):
                with self.assertRaises(ValueError):
                    PR.extract_code(tar(ok + bad), os.path.join(tmp, "out"), tmp)
                self.assertFalse(os.path.exists(os.path.join(tmp, "out")))
            with self.assertRaises(ValueError):                   # no runner
                PR.extract_code(tar([("live/worker.py", b"y", "f")]), os.path.join(tmp, "out"), tmp)
            PR.extract_code(tar(ok), os.path.join(tmp, "out"), tmp)
            self.assertTrue(os.path.isfile(os.path.join(tmp, "out", "live", "worker.py")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_child_env_drops_python_overrides(self):
        os.environ["PYTHONPATH"] = "/somewhere"
        try:
            e = PR.child_env({"X": "1"})
        finally:
            os.environ.pop("PYTHONPATH")
        self.assertNotIn("PYTHONPATH", e)
        self.assertEqual((e["X"], e["PYTHONUNBUFFERED"]), ("1", "1"))

    def test_runner_protocol_with_real_runner_file(self):
        """csclient/procrunner.py (shipped as runner.py) against a stub worker module: progress + files."""
        tmp = tempfile.mkdtemp(prefix="csc-r-")
        try:
            code = os.path.join(tmp, "code")
            os.makedirs(os.path.join(code, "live"))
            shutil.copy(os.path.join(os.path.dirname(HERE), "csclient", "procrunner.py"), os.path.join(code, "runner.py"))
            with open(os.path.join(code, "live", "worker.py"), "w") as f:
                f.write(STUB_WORKER)
            work = os.path.join(tmp, "work")
            os.makedirs(work)
            clip = os.path.join(work, "clip.mp4")
            with open(clip, "wb") as f:
                f.write(b"\0" * 10)
            job = {"code": code, "work": work, "model": clip, "worker_id": "app-u1-x", "env": {"FOO": "bar"},
                   "ev": EV, "match": {"key": f"{EV}_qm1"}, "event": {"teams": ["frc1"]},
                   "live": {"clip": clip, "source": "yt1", "info": {}}, "camera": {"name": "camera.json", "data": {"a": 1}},
                   "pose_guess": {"p": 1}}
            jp = os.path.join(work, "job.json")
            with open(jp, "w") as f:
                json.dump(job, f)
            import subprocess
            p = subprocess.run([sys.executable, "-u", os.path.join(code, "runner.py"), jp], capture_output=True,
                               text=True, timeout=60, env=PR.child_env())
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            msgs = [json.loads(ln[len(PR.PROTO):]) for ln in p.stdout.splitlines() if ln.startswith(PR.PROTO)]
            self.assertIn({"progress": 0.4, "stage": "processing"}, msgs)
            done = msgs[-1]
            self.assertTrue(done["done"] and done["ok"])
            self.assertEqual(sorted(done["files"]), ["matches", "shots"])
            with open(done["files"]["matches"]) as f:
                m = json.load(f)
            self.assertEqual(m["seen"], {"camera": {"a": 1}, "pose": {"p": 1}, "ctx": {"teams": ["frc1"]}, "FOO": "bar",
                                         "KEEP_VIDEO": "0", "worker_id": "app-u1-x"})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


STUB_WORKER = r'''
import json, os
def log(*a): print(*a)
def event_ctx(ev, m): return None
def process_match(ev, m, cfg, status, meta_cache, live=None):
    assert cfg == {"video_mode": "livestream"}
    d = os.path.join(os.environ["DATA"], "events", ev)
    log(m["key"], "  1800 frames t=95s 9000 dets 30.0 fps")
    seen = {"camera": json.load(open(live["camera"])), "pose": json.load(open(os.path.join(d, "pose_latest_yt1.json"))),
            "ctx": event_ctx(ev, m), "FOO": os.environ.get("FOO"), "KEEP_VIDEO": os.environ.get("KEEP_VIDEO"),
            "worker_id": os.environ.get("WORKER_ID")}
    for kind in ("matches", "shots"):
        os.makedirs(os.path.join(d, kind), exist_ok=True)
        json.dump({"key": m["key"], "seen": seen}, open(os.path.join(d, kind, m["key"] + ".json"), "w"))
    status[m["key"]] = {"state": "done"}
    return True
'''


if __name__ == "__main__":
    unittest.main()
