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

import csclient.clipper as PR_C                  # noqa: E402
import csclient.gpu as G                         # noqa: E402
import csclient.procnet as N                     # noqa: E402
import csclient.processor as PR                  # noqa: E402
import csclient.procsetup as PS                  # noqa: E402
from csclient.app import App                      # noqa: E402
from fakeprocessing import FakeProcessing, wait_for   # noqa: E402
from fakeserver import FakeServer                 # noqa: E402
from test_client import Local                     # noqa: E402

import logging                                    # noqa: E402
logging.getLogger("cyberscout").setLevel(logging.CRITICAL if not os.environ.get("CS_TEST_LOG") else logging.DEBUG)

EV = "2026test"


class Base(unittest.TestCase):
    modes = ("ok",)

    env = {}                                      # extra environment for the test (CS_FAKE_*)

    def setUp(self):
        self._imports, self._beat, self._idle = PR.IMPORTS, PR.MIN_BEAT, PR.IDLE_S
        self._cimports = PR.CLIP_IMPORTS
        PR.IMPORTS = PR.CLIP_IMPORTS = ()         # the test Python has no onnxruntime; the fake runner needs none
        PR.MIN_BEAT = 0.3
        PR.IDLE_S = 0.5
        self._env = {k: os.environ.get(k) for k in ("CS_FAKE_GPU", "CS_FAKE_DET_FPS", "CS_FAKE_PROVIDER",
                                                    "CS_FAKE_CUT_WAIT", "CS_FAKE_CLIP_BYTES")}
        os.environ.update(dict({"CS_FAKE_GPU": "coreml"}, **self.env))   # an Apple-silicon GPU, on any test machine
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
        PR.CLIP_IMPORTS = self._cimports
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

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
        self.assertEqual((m["env"].get("FORCE_CPU"), m["env"].get("ORT_COREML")), ("1", "1"))   # Core ML (fake GPU)
        self.assertTrue(m["work"].startswith(os.path.join(self.app.data_dir, "processing", "work")))
        # code + model downloaded once and cached by version / checksum
        self.assertEqual((P.code_gets, P.model_gets), (1, 1))
        root = os.path.join(self.app.data_dir, "processing")
        self.assertTrue(os.path.isfile(os.path.join(root, "pipelines", P.version, "runner.py")))
        self.assertEqual(len(os.listdir(os.path.join(root, "models"))), 1)
        self.assertTrue(wait_for(lambda: os.listdir(os.path.join(root, "work", EV)) == []))   # cleaned up
        self.assertTrue(os.path.isfile(os.path.join(root, "logs", f"{k1}.log")))
        b = P.claim_bodies[0]
        self.assertEqual((b["pipeline"], b["processor"]), (P.version, self.app.config["process_id"]))
        self.assertTrue(wait_for(lambda: self.pstatus()["state"] == "idle"))
        s = self.pstatus()
        self.assertEqual((s["done"], s["failed"], s["last"]["key"]), (2, 0, f"{EV}_qm2"))
        self.assertTrue(s["line"].startswith("idle, done 2"), s["line"])
        self.assertEqual(self.c.req("GET", "/client/v1/status")[1]["processing"]["done"], 2)
        self.assertGreaterEqual(len(P.beats), 0)
        # the benchmark ran once before the first claim (no sample clip on the server: synthetic frames)
        b = s["speed"]["bench"]
        self.assertEqual((b["provider"], b["frames"], b["ok"], b["runtime"]), ("CoreMLExecutionProvider", "synthetic",
                                                                                True, "coreml"))
        self.assertEqual(b["est_min"], PR.estimate_min({"det_fps": 60.0}))
        self.assertEqual(s["speed"]["from"], "measured")                 # real jobs count from then on
        self.assertEqual(len(s["speed"]["jobs"]), 2)
        self.assertEqual(s["gpu"]["kind"], "coreml")
        self.assertTrue(os.path.isfile(os.path.join(root, "bench.json")))

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
        # the server lists qm2 as completed before the client's own count goes up
        self.assertTrue(wait_for(lambda: (self.pstatus()["failed"], self.pstatus()["done"]) == (1, 1)), self.pstatus())


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


class TestMetered(Base):
    def test_large_clip_asks_first_and_counts_data(self):
        P = self.proc
        P.clip = os.urandom(1_600_000)                 # 1.5 MB clip, warning at 1 MB
        self.login()
        code, d = self.c.req("PUT", "/client/v1/processing", {"warn_mb": 1})
        self.assertEqual((code, d["warn_mb"]), (200, 1))
        self.turn_on()
        self.assertTrue(wait_for(lambda: self.pstatus()["state"] == "confirm"), self.pstatus())
        s = self.pstatus()
        self.assertEqual(s["large"], {"key": f"{EV}_qm1", "mb": 2, "warn_mb": 1})
        self.assertIn("over your 1 MB warning", s["message"])
        self.assertEqual(s["line"], "paused: the next clip (qm1) is 2 MB, over 1 MB, done 0")
        self.assertEqual(P.released, [(f"{EV}_qm1", False, "clip over the processor's size warning")])
        self.assertFalse([r for r in self.fs.state.requests if "/clip?" in r[1]])      # nothing downloaded
        down0 = s["data"]["down"]                                # code + model only
        self.assertEqual(down0, len(P.code) + len(P.model))
        code, d = self.c.req("PUT", "/client/v1/processing", {"allow_large": True})
        self.assertTrue(d["large_ok"])
        self.assertTrue(wait_for(lambda: P.completed == [f"{EV}_qm1"]), self.pstatus())
        s = self.pstatus()
        self.assertEqual((s["data"]["down"] - down0, s["data"]["clips"]), (len(P.clip), 1))
        self.assertGreater(s["data"]["up"], 0)
        for bad in ({"warn_mb": -1}, {"warn_mb": "10"}, {"warn_mb": True}, {"allow_large": False}):
            self.assertEqual(self.c.req("PUT", "/client/v1/processing", bad)[0], 422, bad)

    def test_page_shows_the_data_cost(self):
        code, h, b = self.c.req("GET", "/client/processing", raw=True)
        self.assertIn(b"Data cost:", b)
        self.assertIn(b"150&ndash;250 MB", b)


class TestInflight(Base):
    def test_never_runs_a_match_twice(self):
        self.login()
        pr = self.app.processor
        k = f"{EV}_qm1"
        pr.inflight[k] = {"ev": EV, "at": time.time()}
        job = self.proc.job(k, pr.pid)
        self.assertIsNone(pr.run_job(job, {"heartbeat_s": 60}, sys.executable))
        self.assertFalse([r for r in self.fs.state.requests if "/clip?" in r[1] or "/release" in r[1]])

    def test_leftover_claims_are_released_after_a_restart(self):
        self.login()
        self.app.shutdown()
        root = os.path.join(self.app.data_dir, "processing")
        with open(os.path.join(root, "inflight.json"), "w") as f:
            json.dump({f"{EV}_qm7": {"ev": EV, "at": 1}, "../bad": {"ev": EV}}, f)
        self.app = App(self.app.data_dir, port=0, server_url=self.fs.url, bundled_web=os.path.join(HERE, "nonexistent"))
        self.app.config.update(interval=3600, probe_interval=3600)
        self.app.start()
        self.c = Local(self.app)
        self.assertTrue(wait_for(lambda: self.app.engine.online))
        self.turn_on()
        P = self.proc
        self.assertTrue(wait_for(lambda: P.completed), self.pstatus())
        self.assertEqual(P.released[0], (f"{EV}_qm7", False, "the processor restarted"))
        self.assertEqual(len(P.released), 1)
        def inflight():
            with open(os.path.join(root, "inflight.json")) as f:
                return json.load(f)
        self.assertTrue(wait_for(lambda: inflight() == {}), inflight())   # the job's cleanup runs after complete


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


class TestNoGPU(Base):
    env = {"CS_FAKE_GPU": "none"}

    def test_tracking_needs_a_gpu(self):
        self.login()
        self.turn_on()
        self.assertTrue(wait_for(lambda: self.pstatus()["state"] == "no_gpu"), self.pstatus())
        s = self.pstatus()
        self.assertIn("NVIDIA GPU", s["message"])
        self.assertEqual(s["line"], "can't track here: no usable GPU (Clip only works)")
        self.assertEqual(s["notices"][0]["kind"], "no_gpu")
        self.assertEqual(s["plan"]["runtime"], "clip")                  # Set up would install the Clip only tools
        pr = self.app.processor                                          # a benchmark from another GPU doesn't count
        pr.bench = {"v": PR.BENCH_V, "runtime": "coreml", "est_min": 66.0, "ok": True}
        s = self.pstatus()
        self.assertEqual((s["speed"]["per_match_min"], s["speed"]["slow"]), (None, False))
        self.assertNotIn("slow_gpu", [n["kind"] for n in s["notices"]])
        self.assertEqual(self.proc.claim_bodies, [])
        self.assertFalse([r for r in self.fs.state.requests if "/pipeline/code" in r[1]])   # nothing downloaded


class TestSlowComputer(Base):
    env = {"CS_FAKE_DET_FPS": "1.5"}             # ~2 h per match

    def test_slow_computer_pauses_and_offers_clip_only(self):
        self.login()
        self.turn_on()
        self.assertTrue(wait_for(lambda: self.pstatus()["state"] == "slow"), self.pstatus())
        s = self.pstatus()
        self.assertGreater(s["speed"]["per_match_min"], PR.SLOW_MIN)
        self.assertIn("Clip only helps", s["message"])
        self.assertIn("slow_gpu", [n["kind"] for n in s["notices"]])
        self.assertEqual(self.proc.claim_bodies, [])
        code, d = self.c.req("PUT", "/client/v1/processing", {"allow_slow": True})
        self.assertTrue(d["allow_slow"])
        self.assertTrue(wait_for(lambda: self.proc.completed), self.pstatus())     # tracks anyway
        self.assertEqual(self.c.req("PUT", "/client/v1/processing", {"allow_slow": False})[0], 422)


class TestGpuFailsInBenchmark(Base):
    env = {"CS_FAKE_PROVIDER": "CPUExecutionProvider"}

    def test_cpu_fallback_counts_as_no_gpu(self):
        self.login()
        self.turn_on()
        self.assertTrue(wait_for(lambda: self.pstatus()["state"] == "no_gpu"), self.pstatus())
        s = self.pstatus()
        self.assertIn("did not work in the benchmark", s["message"])
        self.assertFalse(s["speed"]["bench"]["ok"])
        self.assertEqual(self.proc.claim_bodies, [])


class TestBenchmarkSample(Base):
    def test_benchmark_uses_the_servers_sample_clip(self):
        self.proc.sample = os.urandom(200_000)
        self.login()
        code, d = self.c.req("PUT", "/client/v1/processing", {"python": sys.executable})
        code, d = self.c.req("POST", "/client/v1/processing/bench", {})
        self.assertEqual(code, 202)
        self.assertTrue(wait_for(lambda: (self.pstatus()["speed"]["running"] or {}).get("state") == "done"),
                        self.pstatus()["speed"])
        b = self.pstatus()["speed"]["bench"]
        self.assertEqual((b["frames"], b["decode_fps"]), ("sample", 300.0))
        self.assertEqual(self.pstatus()["data"]["down"] >= 200_000, True)
        self.assertEqual(self.pstatus()["net"]["clip_bytes"], 191_000_000)     # typical clip size from the server
        bd = os.path.join(self.app.data_dir, "processing", "bench")
        import hashlib
        self.assertEqual([n for n in os.listdir(bd) if n.startswith("sample-")],
                         [f"sample-{hashlib.sha256(self.proc.sample).hexdigest()[:16]}.mp4"])


class TestSlowNet(Base):
    def test_slow_wifi_notice_with_the_metered_warning(self):
        self.proc.speed_bps = 400_000                       # 0.4 MB/s: a 191 MB clip takes ~8 min
        self.login()
        code, d = self.c.req("PUT", "/client/v1/processing", {"python": sys.executable})
        self.assertEqual(self.c.req("POST", "/client/v1/processing/netcheck", {})[0], 202)
        self.assertTrue(wait_for(lambda: self.pstatus()["net"]["down_bps"] and not self.pstatus()["net"]["probing"]),
                        self.pstatus()["net"])
        n = self.pstatus()["net"]
        self.assertTrue(n["slow"])
        self.assertLess(n["down_bps"], 500_000)
        self.assertGreater(n["clip_down_min"], 5)
        self.assertRegex(n["notice"], r"^Slow Wi-Fi: about 0\.\d MB/s from the server, so a match clip \(191 MB\) "
                                      r"takes about \d+ min to download; processing here won't be optimal")
        self.assertIn("over your 100 MB data warning", n["notice"])
        st = self.c.req("GET", "/client/v1/status")[1]["processing"]
        self.assertEqual(st["notices"][-1]["kind"], "slow_net")
        # the main web app's Settings read the same notice through GET /client/v1/processing (web/settings.js)


class TestClipOnly(Base):
    env = {"CS_FAKE_GPU": "none", "CS_FAKE_CLIP_BYTES": "300000"}

    def setUp(self):
        super().setUp()
        now = time.time()
        P = self.proc
        P.stream_matches = [{"key": f"{EV}_qm{n}", "comp_level": "qm", "set_number": 1, "match_number": n,
                             "actual_time": now - 600 + 100 * n, "alliances": {}} for n in (1, 2, 3)]
        P.stream_want = [f"{EV}_qm1", f"{EV}_qm2"]
        P.stream_skipped = [f"{EV}_qm3"]

    def test_records_cuts_and_uploads_what_the_server_wants(self):
        P = self.proc
        P.cut_chunk_once = True                             # one broken chunk upload on the way
        self.login()
        code, d = self.c.req("PUT", "/client/v1/processing", {"python": sys.executable, "mode": "clip",
                                                               "enabled": True})
        self.assertEqual((code, d["mode"]), (200, "clip"))
        self.assertTrue(wait_for(lambda: len(P.clips_done) == 2, 40), (self.pstatus(), P.clip_uploads.keys()))
        rec, data, _ = P.clips_done["tba-qm1"]
        self.assertTrue(data.startswith(b"\0\0\0\x14ftyp") and len(data) == 300_012)
        self.assertEqual(sorted(rec), sorted(set(rec) & set(PR_C.REC_FIELDS)))   # only the record fields, no paths
        self.assertGreater(P.chunk_puts, 300_012 // (64 << 10))                     # chunked (64 KB here)
        self.assertTrue(wait_for(lambda: self.pstatus()["clip"]["uploaded"] == 2), self.pstatus()["clip"])
        s = self.pstatus()
        self.assertEqual(s["state"], "clip")
        self.assertRegex(s["line"], r"^clip only: (recording Main stream, \d+ (KB|MB) kept, 2 uploaded|uploading)")
        rows = {r["match"]: r["state"] for r in s["clip"]["matches"]}
        self.assertEqual(rows["qm1"], "uploaded")
        self.assertEqual(rows["qm3"], "the server has it")
        rec_dir = os.path.join(self.app.data_dir, "processing", "streams", "cs0123456789", "rec", EV, "r1")
        self.assertEqual(os.listdir(os.path.join(rec_dir, "clips")), [])              # sent clips are deleted
        self.assertTrue(os.path.isfile(os.path.join(rec_dir, "matches", "tba-qm1.json")))
        self.assertGreater(s["data"]["up"], 400_000)                                   # 2 clips, acknowledged chunks
        self.assertGreater(s["data"]["down"], 0)                                       # the recording's bytes
        self.assertTrue(wait_for(lambda: any(b.get("state") == "recording" and b["rec"].get("chunks")
                                             for b in P.stream_beats), 15), P.stream_beats)
        # turned off: the runner stops and the stream is given back
        self.c.req("PUT", "/client/v1/processing", {"enabled": False})
        self.assertTrue(wait_for(lambda: P.stream_released == ["turned off"]), P.stream_released)
        self.assertTrue(wait_for(lambda: self.pstatus()["state"] == "off"))

    def test_waits_are_shown_and_a_lost_stream_stops(self):
        os.environ["CS_FAKE_CUT_WAIT"] = "the recording does not cover this match"
        P = self.proc
        self.login()
        self.c.req("PUT", "/client/v1/processing", {"python": sys.executable, "mode": "clip", "enabled": True})
        self.assertTrue(wait_for(lambda: any("waiting: the recording does not cover" in r["state"]
                                             for r in (self.pstatus()["clip"] or {}).get("matches") or [])),
                        self.pstatus()["clip"])
        P.stream_stop = True
        self.assertTrue(wait_for(lambda: self.pstatus()["state"] in ("idle", "clip") and P.stream_by is not None
                                 and len(P.stream_beats) > 3), self.pstatus())
        self.assertEqual(P.clips_done, {})


class TestSetup(Base):
    env = {"CS_FAKE_GPU": "coreml"}

    def test_setup_installs_the_runtime_then_benchmarks(self):
        self.login()
        pr = self.app.processor
        cmds = []

        def fake_uv(cmd, watch=None, expect=0, timeout=0):
            cmds.append(cmd[1:3])
            if cmd[1] == "venv":
                os.makedirs(os.path.join(cmd[-1], "bin" if os.name != "nt" else "Scripts"), exist_ok=True)
                py = PR.venv_python(cmd[-1])
                try:
                    os.symlink(sys.executable, py)
                except (OSError, NotImplementedError):
                    shutil.copy(sys.executable, py)
        pr.setup_cmd = fake_uv
        os.makedirs(os.path.join(pr.root, "tools", f"uv-{PS.UV_VERSION}"), exist_ok=True)
        uv = os.path.join(pr.root, "tools", f"uv-{PS.UV_VERSION}", "uv.exe" if os.name == "nt" else "uv")
        open(uv, "w").close()
        orig = PS.find_uv
        PS.find_uv = lambda: None
        try:
            code, d = self.c.req("POST", "/client/v1/processing/setup", {})
            self.assertEqual(code, 202)
            self.assertTrue(wait_for(lambda: (self.pstatus()["setup"] or {}).get("state") in ("done", "error")))
        finally:
            PS.find_uv = orig
        s = self.pstatus()
        self.assertEqual(s["setup"]["state"], "done", s["setup"])
        self.assertEqual([c[0] for c in cmds], ["venv", "pip"])
        self.assertTrue(any("Core ML" in ln for ln in s["setup"]["log"]), s["setup"]["log"])
        self.assertEqual(s["speed"]["bench"]["provider"], "CoreMLExecutionProvider")   # benchmarked right after
        self.assertTrue(s["plan"]["installed"])
        with open(os.path.join(pr.root, "requirements-coreml.txt")) as f:
            self.assertIn("imageio-ffmpeg==0.6.0", f.read())

    def test_setup_refuses_without_disk_space(self):
        self.login()
        orig = PS.shutil.disk_usage
        PS.shutil.disk_usage = lambda p: type("U", (), {"free": 1 << 30, "total": 1, "used": 1})()
        try:
            self.c.req("POST", "/client/v1/processing/setup", {})
            self.assertTrue(wait_for(lambda: (self.pstatus()["setup"] or {}).get("state") == "error"))
            self.assertIn("not enough free disk space", self.pstatus()["setup"]["message"])
            self.assertFalse(self.pstatus()["plan"]["ok"])
        finally:
            PS.shutil.disk_usage = orig


class TestGPUDetect(unittest.TestCase):
    def run_with(self, table):
        def run(cmd, timeout=15):
            for k, v in table.items():
                if k in " ".join(cmd):
                    return v
            return (1, "", "not found")
        return run

    def test_nvidia(self):
        q = "0, NVIDIA GeForce RTX 3060 Ti, 8192, 300, 610.43.03, 8.6\n1, NVIDIA GeForce RTX 3060, 12288, 11800, 610.43.03, 8.6\n"
        orig = G.find_smi
        G.find_smi = lambda: "/usr/bin/nvidia-smi"
        try:
            g = G._nvidia(self.run_with({"--query-gpu": (0, q, "")}))
            self.assertEqual((g["ok"], g["kind"], g["device"], g["name"]), (True, "cuda", 0, "NVIDIA GeForce RTX 3060 Ti"))
            self.assertEqual(G.pipeline_env(g), {"GPU_DEVICE": "0"})                    # the most free memory
            g = G._nvidia(self.run_with({"--query-gpu": (0, "0, NVIDIA GeForce RTX 3060, 12288, 11800, 610.43.03, 8.6\n", "")}))
            self.assertTrue(g["ok"] and g["busy"])
            self.assertIn("11.5 GB in use by other programs", g["detail"])
            g = G._nvidia(self.run_with({"--query-gpu": (0, "0, NVIDIA GeForce GTX 1080, 8192, 0, 590.10, 6.1\n", "")}))
            self.assertFalse(g["ok"])
            self.assertIn("compute capability 6.1", g["why"])
            self.assertEqual(g["runtime"], "clip")
            g = G._nvidia(self.run_with({"--query-gpu": (0, "0, NVIDIA RTX A2000, 6144, 0, 536.23, 8.6\n", "")}))
            self.assertIn("driver (536.23) is too old", g["why"])
            old = {"compute_cap": (2, "", 'Field "compute_cap" is not a valid field to query.'),
                   "driver_version --format": (0, "0, NVIDIA GeForce RTX 4090, 24564, 1000, 612.01\n", "")}
            g = G._nvidia(self.run_with(old))
            self.assertTrue(g["ok"], g)
        finally:
            G.find_smi = orig
        G.find_smi, orig2 = (lambda: None), G._other_gpus
        G._other_gpus = lambda run: ["Intel(R) Iris(R) Xe Graphics"]
        try:
            g = G._nvidia(self.run_with({}))
            self.assertIn("Iris(R) Xe Graphics", g["why"])
            self.assertFalse(g["ok"])
        finally:
            G.find_smi, G._other_gpus = orig, orig2

    def test_mac(self):
        orig = G.apple_silicon
        try:
            G.apple_silicon = lambda run=None: False
            self.assertIn("Intel Macs", G._mac(self.run_with({}))["why"])
            G.apple_silicon = lambda run=None: True
            g = G._mac(self.run_with({"machdep.cpu": (0, "Apple M2\n", ""), "hw.memsize": (0, str(16 << 30), ""),
                                      "SPDisplays": (0, json.dumps({"SPDisplaysDataType": [{"sppci_cores": "10"}]}), "")}))
            if tuple(int(x) for x in (__import__("platform").mac_ver()[0] or "15").split(".")[:2]) >= (14, 0):
                self.assertEqual((g["ok"], g["kind"], g["name"]), (True, "coreml", "Apple M2 (10-core GPU)"))
                self.assertEqual(G.pipeline_env(g), {"FORCE_CPU": "1", "ORT_COREML": "1"})
        finally:
            G.apple_silicon = orig


class TestSetupUnits(unittest.TestCase):
    def test_requirements_per_runtime(self):
        base = ["onnxruntime==1.31.0", "opencv-python-headless==5.0.0.93", "av==19.0.1", "numpy==2.5.4", "scipy==1.18.1"]
        pipe = {"requirements": base}                                # an older server: no "runtimes"
        r, _ = PS.requirements(pipe, "cuda")
        self.assertEqual(r[0], "onnxruntime-gpu[cuda,cudnn]==1.31.0")
        self.assertIn("imageio-ffmpeg==0.6.0", r)
        r, _ = PS.requirements(pipe, "clip")
        self.assertEqual({x.split("==")[0] for x in r}, {"opencv-python-headless", "av", "numpy", "imageio-ffmpeg", "deno"})
        r, _ = PS.requirements(dict(pipe, runtimes={"coreml": ["x==1"]}, clip_extras=["y==2"]), "coreml")
        self.assertEqual(r, ["x==1", "y==2"])

    def test_plan_checks_disk(self):
        tmp = tempfile.mkdtemp(prefix="csc-plan-")
        try:
            p = PS.plan(tmp, {"requirements": ["numpy==2"]}, {"runtime": "cuda"})
            self.assertEqual((p["runtime"], p["download"], p["installed"]), ("cuda", PS.SIZES["cuda"]["download"], False))
            self.assertEqual(p["need"], 2 * PS.SIZES["cuda"]["disk"] + PS.HEADROOM)
            orig = PS.shutil.disk_usage
            PS.shutil.disk_usage = lambda p: type("U", (), {"free": 2 << 30})()
            try:
                p = PS.plan(tmp, {"requirements": []}, {"runtime": "cuda"})
                self.assertFalse(p["ok"])
                self.assertRegex(p["why"], r"needs about 9\.\d GB on this disk, 2\.0 GB free")
            finally:
                PS.shutil.disk_usage = orig
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_uv_download_is_checksum_verified(self):
        tmp = tempfile.mkdtemp(prefix="csc-uv-")
        try:
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as t:
                for name, data in (("uv-x/uv", b"#!uv"), ("uv-x/uvx", b"#!uvx"), ("uv-x/uv.exe", b"MZuv")):
                    ti = tarfile.TarInfo(name)
                    ti.size = len(data)
                    t.addfile(ti, io.BytesIO(data))
            blob = buf.getvalue()
            key = PS.machine()
            orig_assets, orig_find = dict(PS.UV_ASSETS), PS.find_uv
            PS.find_uv = lambda: None
            PS.UV_ASSETS[key] = ("uv-x.tar.gz", "0" * 64)
            try:
                with self.assertRaises(RuntimeError):
                    PS.ensure_uv(tmp, lambda m: None, fetch=lambda u: blob)
                import hashlib
                PS.UV_ASSETS[key] = ("uv-x.tar.gz", hashlib.sha256(blob).hexdigest())
                if os.name != "nt":
                    p = PS.ensure_uv(tmp, lambda m: None, fetch=lambda u: blob)
                    with open(p, "rb") as f:
                        self.assertEqual(f.read(), b"#!uv")
                    self.assertTrue(os.access(p, os.X_OK))
            finally:
                PS.UV_ASSETS.clear()
                PS.UV_ASSETS.update(orig_assets)
                PS.find_uv = orig_find
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestNotices(unittest.TestCase):
    def test_texts(self):
        slow, t = N.notice("track", 600_000, None, 191e6, 0)
        self.assertTrue(slow)
        self.assertEqual(t, "Slow Wi-Fi: about 0.6 MB/s from the server, so a match clip (191 MB) takes about 5 min "
                            "to download; processing here won't be optimal (the server may be faster on its own).")
        self.assertEqual(N.notice("track", 20e6, None, 191e6, 0), (False, None))
        slow, t = N.notice("track", 20e6, None, 191e6, 100)
        self.assertFalse(slow)
        self.assertIn("over your 100 MB warning", t)
        slow, t = N.notice("clip", 0.8e6, 0.3e6, 191e6, 100, behind_s=240)
        self.assertTrue(slow)
        self.assertIn("the recording is 4 min behind the live stream", t)
        self.assertIn("Also slow: about 0.3 MB/s up, so a match clip (191 MB) takes about 11 min to upload", t)
        self.assertEqual(N.notice("clip", 0.2e6, 5e6, 191e6, 100, behind_s=8), (False, None))   # stream keeps up

    def test_probe_due(self):
        m = N.NetMeter()
        self.assertTrue(m.due())
        m.add("down", 50 << 20, 10, "clip")                 # a real download just now: no speed test needed
        self.assertFalse(m.due())
        self.assertTrue(m.due(up=True))                      # ... unless the upload speed matters (Clip only)
        m.add("up", 1 << 20, 2, "probe")
        m.probe_at = time.time()
        self.assertFalse(m.due(up=True))
        self.assertFalse(m.due(now=time.time() + 600))
        self.assertTrue(m.due(now=time.time() + N.PROBE_EVERY_S + N.WINDOW_S))

    def test_estimate(self):
        self.assertIsNone(PR.estimate_min({}))
        fast = PR.estimate_min({"det_fps": 120, "decode": {"fps": 600}})
        slow = PR.estimate_min({"det_fps": 2, "decode": {"fps": 100}})
        self.assertLess(fast, 5)
        self.assertGreater(slow, PR.SLOW_MIN)


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

    def test_runner_stops_when_the_client_is_gone(self):
        import subprocess
        code = ("import sys, time; sys.path.insert(0, %r); import procrunner; procrunner.die_with_parent(); "
                "print('up', flush=True); time.sleep(60)" % os.path.join(os.path.dirname(HERE), "csclient"))
        p = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             env=PR.child_env(), text=True)
        self.assertEqual(p.stdout.readline().strip(), "up")
        t0 = time.time()
        p.stdin.close()                                   # what the OS does when the client process ends
        self.assertEqual(p.wait(10), 3)
        self.assertLess(time.time() - t0, 5)
        p.stdout.close()

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
                               text=True, timeout=60, env=PR.child_env({"CS_RUNNER_PARENT_PIPE": "0"}))
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
