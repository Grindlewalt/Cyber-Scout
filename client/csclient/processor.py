"""Processor mode, "Help process matches": this computer tracks matches for the server, in parallel with the
server's own worker (server API: server/routers/processing.py; the same per-match claims, so no match is tracked
twice).  Loop: claim the next match -> download its livestream clip (Range-resumable) -> run the server's pipeline
on it -> upload the 4 result files -> complete (the server publishes them and keeps the clip).  A heartbeat keeps
the claim fresh; a failed match is released (and skipped here for a while).

GPU only (csclient/gpu.py): NVIDIA with CUDA on Windows / Linux, Apple silicon with Core ML.  Without one, tracking
stays off and the page says why.  After setup a short benchmark (runner.py --bench: decode a sample of a real clip +
run the detector) estimates the minutes per match; real jobs refine it.  A computer that would need more than
SLOW_MIN per match pauses with an offer of "Clip only" (csclient/clipper.py: record a livestream, cut every match by
TBA time, upload the clips the server is missing), which works on any computer.  The link to the server is measured
(csclient/procnet.py) and a slow one gets a notice.

The pipeline (live/ + src/: onnxruntime, av, cv2, numpy, scipy, pupil_apriltags) can't be part of this stdlib
client, so it runs as a subprocess of a *processing Python* that has those packages:
  * config "process_python" (Settings / --process-python): e.g. a dev checkout's .venv/bin/python, or
  * <data>/processing/venv, made once by "Set up processing" (csclient/procsetup.py: uv, the runtime for this GPU).
The code (live/, src/, runner.py = csclient/procrunner.py) and the detector model come from the server and are
cached by version (processing/pipelines/<version>/, processing/models/<sha256>.onnx), so they always match the
server's.

Needs an admin account (the server only gives processing jobs to admins)."""
import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
import uuid
from urllib.parse import quote

from . import procsetup as PS
from .clipper import ClipJob, StreamLost
from .gpu import detect as gpu_detect, pipeline_env as gpu_env
from .net import HTTPError, NetError, detail_text
from .procnet import NetMeter
from .util import ensure_dir, read_json, safe_relpath, target_path, write_json

log = logging.getLogger("cyberscout.processor")

API = "/app/v1/processing"
PROTO = "@@CS "
KINDS = ("fuel", "shots", "analytics", "matches")
IMPORTS = PS.TRACK_IMPORTS                       # the processing Python needs these to track
CLIP_IMPORTS = ("av", "cv2", "numpy", "yt_dlp")  # ... and these for Clip only (+ an ffmpeg)
VER_RE = re.compile(r"^p-[0-9a-f]{12}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
EVENT_RE = re.compile(r"^[0-9]{4}[a-z0-9]{1,16}$")
MATCH_RE = re.compile(r"^([0-9]{4}[a-z0-9]{1,16})_(qm|ef|qf|sf|f)[0-9]{1,3}(m[0-9]{1,2})?$")
URL_RE = re.compile(r"^/app/v1/processing/[A-Za-z0-9_./?=&%-]{1,300}$")
MAX_CODE = 200 * 1024 * 1024
JOB_TIMEOUT_S = 45 * 60          # one match normally takes 2-5 min
IDLE_S = 45                      # nothing to do: ask again after this long
SKIP_S = 30 * 60                 # a match that failed here is not taken again for this long
MIN_BEAT = 5.0                   # heartbeat interval floor (s)
WARN_MB = 100                    # default "warn before a clip download over N MB" (config process_warn_mb; 0 = off)
SLOW_MIN = 25.0                  # minutes per match: slower than this, Clip only helps the event more
SERVER_MIN = 3.5                 # what the server's own worker takes per match (RTX 3060 Ti), for the comparison
# what a GPU we have measured takes per match (client/README.md, "How fast"), shown before this computer is measured
TYPICAL = ((re.compile(r"^Apple M1(?! (Pro|Max|Ultra))\b"), 5.2, "an Apple M1, Core ML"),)
BENCH_V = 1                      # bump to re-run every client's benchmark
# minutes per match from the benchmark: a livestream clip is ~190 s at 60 fps; the detector runs at 30 Hz over all of
# it (process.detect_video), and the rest of the pipeline (decode, fuel tracker on every frame, OCR, shots, intakes,
# identity) is CPU work that scales with the decode time.  Fitted to a real job: Apple M1, Core ML, 2026wagg qm10:
# detector 27.3 fps + decode 622 fps -> 312.5 s estimated, 312.7 s measured (README: Benchmark).
CLIP_S, CLIP_FPS, DET_HZ = 190.0, 60.0, 30.0
POST_K, EST_C = 5.0, 12.0
DEFAULT_DECODE_FPS = 220.0       # no sample clip on the server: a typical 1080p H.264 software decode


class Stopped(Exception):
    pass


class NeedsSetup(Exception):
    pass


class Lost(Exception):
    """The claim on the match is gone (stale / taken over): stop working on it."""


def _short(key):
    return (key or "").split("_", 1)[-1]


def default_processor_id():
    host = re.sub(r"[^A-Za-z0-9-]", "-", socket.gethostname().split(".")[0])[:24].strip("-") or "client"
    return f"{host}-{uuid.uuid4().hex[:6]}"


venv_python = PS.venv_python
find_uv = PS.find_uv


def child_env(extra=None):
    """Environment for the processing Python: ours minus what a frozen (PyInstaller) client changed or Python
    settings that would make another interpreter load the wrong modules."""
    env = dict(os.environ)
    for k in list(env):
        if k.startswith(("_MEI", "_PYI")) or k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONNOUSERSITE"):
            env.pop(k, None)
    if getattr(sys, "frozen", False):
        for k in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH"):
            orig = env.pop(k + "_ORIG", None)
            if orig is not None:
                env[k] = orig
            else:
                env.pop(k, None)
    env["PYTHONUNBUFFERED"] = "1"
    env["CS_RUNNER_PARENT_PIPE"] = "1"          # runner.py: our stdin pipe closing = the client is gone, stop
    env.update(extra or {})
    return env


def pipeline_env(gpu=None):
    """Detector settings for the pipeline child (csclient/gpu.py): Core ML on Apple silicon, the CUDA device."""
    return gpu_env(gpu)


def estimate_min(b):
    """Benchmark result -> minutes per match (None without a detector measurement)."""
    det = (b or {}).get("det_fps")
    if not det:
        return None
    dec = ((b.get("decode") or {}).get("fps")) or DEFAULT_DECODE_FPS
    secs = CLIP_S * DET_HZ / det + POST_K * CLIP_S * CLIP_FPS / dec + EST_C
    return round(secs / 60, 1)


def extract_code(tar_path, dst, tmp_root):
    """Unpack the pipeline tarball safely (regular files only, no absolute / '..' / link members) into dst
    (atomically: a temp dir renamed into place)."""
    roots = ("live", "src", "runner.py", "requirements.txt", "PIPELINE.json")
    tmp = os.path.join(tmp_root, f".x-{uuid.uuid4().hex[:8]}")
    os.makedirs(tmp)
    total = 0
    try:
        with tarfile.open(tar_path, "r:gz") as t:
            for ti in t:
                if ti.isdir():
                    continue
                rel = safe_relpath(ti.name, roots=roots)
                if not ti.isfile() or rel is None:
                    raise ValueError(f"unsafe entry in the pipeline archive: {ti.name!r}")
                total += ti.size
                if total > MAX_CODE:
                    raise ValueError("pipeline archive too large")
                p = target_path(tmp, rel)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                src = t.extractfile(ti)
                with open(p, "wb") as f:
                    shutil.copyfileobj(src, f, 1 << 20)
        for need in ("runner.py", "live/worker.py"):
            if not os.path.isfile(os.path.join(tmp, need)):
                raise ValueError(f"the pipeline archive has no {need}")
        if os.path.isdir(dst):
            shutil.rmtree(dst, ignore_errors=True)
        os.replace(tmp, dst)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


class Processor:
    def __init__(self, app):
        self.app = app
        self.cfg, self.server = app.config, app.server
        self.root = ensure_dir(os.path.join(app.data_dir, "processing"))
        self.parts = ensure_dir(os.path.join(self.root, "parts"))
        self.logs = ensure_dir(os.path.join(self.root, "logs"))
        if not self.cfg.get("process_id"):
            self.cfg.update(process_id=default_processor_id())
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stop_ev = threading.Event()
        self.thread = None
        self.child = None
        self.skip = {}                          # match key -> time until which it isn't taken again
        self._py_ok = {}                        # (python, mtime, version, mode) -> error text | None
        self.pipe = None
        self.st = {"state": "off", "message": None, "ev": None, "key": None, "pct": None, "stage": None,
                   "done": 0, "failed": 0, "last": None, "since": None, "counts": None, "large": None}
        self.data = {"down": 0, "up": 0, "clips": 0}   # bytes this session (data cost on a metered link)
        self.large_ok = False                   # "Allow large clips" pressed (this session)
        self.setup_st = None                    # one-time setup progress {state, message, log[]}
        self.gpu_info = None                    # csclient/gpu.py detect(), once per run (POST .../gpu: again)
        self.gpu_lock = threading.Lock()
        self.bench_p = os.path.join(self.root, "bench.json")
        self.bench = read_json(self.bench_p, None)
        self.bench_st = None                    # {state, message, pct} while / after a benchmark runs
        self.bench_lock = threading.Lock()
        self.perf_p = os.path.join(self.root, "perf.json")
        perf = read_json(self.perf_p, []) or []
        self.perf = [x for x in perf if isinstance(x, dict)][-20:] if isinstance(perf, list) else []
        self.net = NetMeter()
        self.clip_job = None                    # csclient/clipper.py ClipJob while Clip only records
        self.clip_last = None
        self.installer = None                   # procsetup.Installer while Set up runs
        self.setup_cmd = None                   # tests: runs uv's commands instead
        # jobs in flight (never two of the same match); persisted, so after a crash / restart the claims this client
        # still holds on the server are released before it asks for work again (instead of going stale)
        self.inflight_p = os.path.join(self.root, "inflight.json")
        self.inflight = {}
        lo = read_json(self.inflight_p, {}) or {}
        self.leftovers = {k: v for k, v in lo.items() if isinstance(v, dict) and MATCH_RE.match(k)
                          and EVENT_RE.match(str(v.get("ev") or ""))} if isinstance(lo, dict) else {}
        for d in ("work",):                     # leftovers of a crash: the claims go stale on the server
            shutil.rmtree(os.path.join(self.root, d), ignore_errors=True)

    # ------------------------------------------------------------------------------------------------ control
    @property
    def pid(self):
        return self.cfg["process_id"]

    @property
    def enabled(self):
        return bool(self.cfg.get("process"))

    def mode(self):
        return "clip" if self.cfg.get("process_mode") == "clip" else "track"

    def start(self):
        threading.Thread(target=self.gpu, name="gpu-check", daemon=True).start()
        self.thread = threading.Thread(target=self.run, name="processor", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_ev.set()
        self.wake.set()
        self._kill_child()
        cj = self.clip_job
        if cj is not None:                      # Clip only: stop the recorder now (the next start resumes it)
            cj.stop_ev.set()
            cj._kill()

    def set_enabled(self, on):
        self.cfg.update(process=bool(on))
        if not on:
            self._kill_child()                  # the job notices, releases its claim
        log.info("help process matches: %s", "on" if on else "off")
        self.wake.set()

    def set_mode(self, m):
        if m == self.mode():
            return
        self.cfg.update(process_mode=m)
        self._kill_child()                      # a tracking job in progress is given back
        log.info("help process matches: mode %s", m)
        self.wake.set()

    def allow_slow(self):
        self.cfg.update(process_allow_slow=True)
        self.wake.set()

    def kick(self):
        self.wake.set()

    def allow_large(self):
        self.large_ok = True
        self._set(large=None)
        self.wake.set()

    def warn_mb(self):
        v = self.cfg.get("process_warn_mb", WARN_MB)
        return WARN_MB if v is None else int(v)

    def _count(self, k, n):
        with self.lock:
            self.data[k] += n

    def _save_inflight(self):
        try:
            write_json(self.inflight_p, {**self.leftovers, **self.inflight}, mode=0o600)
        except OSError:
            pass

    def _release_leftovers(self):
        """Claims a previous run of this client left on the server (crash / restart mid-job): give them back."""
        for k, v in list(self.leftovers.items()):
            if self._release(v["ev"], k, failed=False, reason="the processor restarted"):
                log.info("%s: released the claim left by the last run", k)
                self.leftovers.pop(k, None)
                self._save_inflight()
            else:
                return

    def _set(self, **kv):
        with self.lock:
            self.st.update(kv)

    def _wait(self, s):
        self.wake.wait(s)
        self.wake.clear()
        if self.stop_ev.is_set():
            raise Stopped()

    def _kill_child(self):
        c = self.child
        if c is not None and c.poll() is None:
            try:
                c.kill()
            except OSError:
                pass

    def python(self):
        p = self.cfg.get("process_python")
        if p:
            return p
        v = venv_python(os.path.join(self.root, "venv"))
        return v if os.path.isfile(v) else None

    def gpu(self, again=False):
        with self.gpu_lock:
            if self.gpu_info is None or again:
                self.gpu_info = gpu_detect()
                g = self.gpu_info
                log.info("GPU check: %s (%s)", g.get("name"), "usable: " + g["kind"] if g.get("ok") else g.get("why"))
            return self.gpu_info

    # ------------------------------------------------------------------------------------------------ speed
    def measured_min(self):
        """Median minutes of the pipeline per match over the last 3 real jobs here (None before the first)."""
        xs = sorted(x["process_s"] for x in self.perf[-3:] if x.get("process_s") is not None)
        return round(xs[len(xs) // 2] / 60, 1) if xs else None

    def per_match_min(self):
        """(minutes per match, "measured" | "benchmark") for the GPU this computer has now (none: (None, None))."""
        g = self.gpu_info
        if g is not None and not g.get("ok"):
            return None, None
        m = self.measured_min()
        if m is not None:
            return m, "measured"
        b = self.bench
        if b and b.get("est_min") is not None and b.get("v") == BENCH_V and \
                (g is None or b.get("runtime") == g.get("runtime")):
            return b["est_min"], "benchmark"
        return None, None

    def slow(self):
        m, _ = self.per_match_min()
        return m is not None and m > SLOW_MIN

    def _slow_text(self):
        m, how = self.per_match_min()
        return (f"This computer needs about {m:.0f} min per match ({'real matches' if how == 'measured' else 'benchmark'}"
                f"; the server needs about {SERVER_MIN:g}): tracking here would slow the event down. Clip only helps "
                "more, or track anyway.")

    def notices(self, s):
        out = []
        if self.mode() == "track":
            g = self.gpu_info
            if g and not g.get("ok"):
                out.append({"kind": "no_gpu", "level": "bad", "text": f"Tracking needs a GPU: {g.get('why')}"})
            b = self.bench
            if g and g.get("ok") and b and b.get("v") == BENCH_V and not b.get("ok") and b.get("why") \
                    and b.get("runtime") == g.get("runtime"):
                out.append({"kind": "gpu_failed", "level": "bad", "text": b["why"]})
            if self.slow():
                out.append({"kind": "slow_gpu", "level": "warn", "text": self._slow_text()})
        n = s.get("net") or {}
        if n.get("notice"):
            out.append({"kind": "slow_net" if n.get("slow") else "metered", "level": "warn" if n.get("slow")
                        else "info", "text": n["notice"]})
        return out

    def status(self):
        with self.lock:
            s = dict(self.st)
            s["setup"] = dict(self.setup_st, log=list(self.setup_st.get("log") or [])[-12:],
                              progress=self.setup_progress()) if self.setup_st else None
            s["data"] = dict(self.data)
        mode = self.mode()
        s.update(enabled=self.enabled, processor=self.pid, python=self.python(), warn_mb=self.warn_mb(),
                 large_ok=self.large_ok, mode=mode, allow_slow=bool(self.cfg.get("process_allow_slow")),
                 python_setting=self.cfg.get("process_python"), events=self.cfg.get("process_events"),
                 pipeline=(self.pipe or {}).get("version"), match=_short(s.get("key")) or None,
                 gpu=self.gpu_info)
        m, how = self.per_match_min()
        b = dict(self.bench or {}) if self.bench else None
        g = self.gpu_info or {}
        typ = next(((mins, what) for rx, mins, what in TYPICAL if g.get("ok") and rx.match(g.get("name") or "")), None)
        u = self.app.session.user if self.cfg["server"] else None
        s["user"] = {"display_name": u.get("display_name"), "role": u.get("role")} if u else None    # (admin only)
        s["speed"] = {"per_match_min": m, "from": how, "measured_min": self.measured_min(), "slow": self.slow(),
                      "typical_min": typ[0] if typ else None, "typical_of": typ[1] if typ else None,
                      "slow_min": SLOW_MIN, "server_min": SERVER_MIN, "bench": b, "running": self.bench_st,
                      "jobs": [{k: x.get(k) for k in ("key", "process_s", "download_s", "total_s", "at")}
                               for x in self.perf[-5:]]}
        cj = self.clip_job
        s["net"] = self.net.view(mode, self.warn_mb(), (cj.rec or {}).get("behind_s") if cj else None)
        s["notices"] = self.notices(s)
        s["clip"] = cj.status() if cj else self.clip_last
        try:
            s["plan"] = PS.plan(self.root, self.pipe, self.gpu_info) if self.gpu_info else None
        except OSError:
            s["plan"] = None
        s["line"] = self.line(s, cj)
        return s

    @staticmethod
    def line(s, clip=None):
        """One status line: "processing qm12 at 40%, done 3"."""
        done = f"done {s.get('done') or 0}" + (f", failed {s['failed']}" if s.get("failed") else "")
        st, m, pct = s.get("state"), _short(s.get("key")), s.get("pct")
        if not s.get("enabled") and st in (None, "off"):
            return "off"
        if st == "clip" and clip is not None:
            return clip.line()
        if st in ("downloading", "processing", "uploading") and m:
            at = f" at {pct}%" if pct is not None else ""
            return f"{st} {m}{at}, {done}"
        if st == "idle":
            return f"idle, {done}" + (f": {s['message']}" if s.get("message") else "")
        if st == "confirm" and s.get("large"):
            lg = s["large"]
            return f"paused: the next clip ({_short(lg.get('key'))}) is {lg.get('mb')} MB, over {lg.get('warn_mb')} MB, {done}"
        if st == "no_gpu":
            return "can't track here: no usable GPU (Clip only works)"
        if st == "slow":
            return "paused: too slow to track here; Clip only helps more"
        if st == "benchmarking":
            return "benchmarking this computer" + (f" ({s['message']})" if s.get("message") else "")
        tail = f" ({done})" if s.get("done") or s.get("failed") else ""
        if st == "waiting" and s.get("message"):           # the reason alone, not "waiting: ..." (CLIENT-16)
            return s["message"] + tail
        label = {"needs_setup": "set up needed", "checking": "checking this computer", "updating": "updating"}.get(st, st)
        return f"{label}: {s.get('message') or ''}".rstrip(": ") + tail

    # ------------------------------------------------------------------------------------------------ loop
    def run(self):
        backoff = 0
        while not self.stop_ev.is_set():
            try:
                if not self.enabled:
                    self._set(state="off", message=None, key=None, ev=None, pct=None, stage=None)
                    self._wait(3600)
                    continue
                u = self.app.session.user if self.cfg["server"] else None
                if not u:
                    self._set(state="waiting", message="sign in with an admin account to help process matches")
                    self._wait(20)
                    continue
                if u.get("role") != "admin":
                    self._set(state="waiting", message="processing needs an admin account (signed in as "
                                                       f"{u.get('role') or 'a non-admin'})")
                    self._wait(60)
                    continue
                if not self.app.engine.online:
                    self._set(state="waiting", message="offline: waiting for the server")
                    self._wait(15)
                    continue
                mode = self.mode()
                if mode == "track":
                    g = self.gpu()
                    if not g.get("ok"):
                        self._set(state="no_gpu", message=g.get("why"), key=None, pct=None, stage=None)
                        sig = (self.mode(), self.enabled, self.gpu_info is g)
                        while (self.mode(), self.enabled, self.gpu_info is g) == sig:
                            self._wait(3600)
                        continue
                self._set(state="checking", message=None)
                pipe = self.ensure_pipeline(model=mode == "track")
                py = self.check_python(pipe, mode)
                if self.net.due(up=mode == "clip"):
                    self._probe()
                if mode == "clip":
                    self.run_clip(pipe, py)
                    backoff = 0
                    continue
                if self._bench_needed(py):
                    self._set(state="benchmarking", message="measuring this computer once", key=None, pct=None)
                    self.benchmark(pipe, py)
                b = self.bench
                if b and b.get("v") == BENCH_V and not b.get("ok"):
                    self._set(state="no_gpu", message=b.get("why"))
                    sig = (self.mode(), self.enabled, self.bench is b)
                    while (self.mode(), self.enabled, self.bench is b) == sig:
                        self._wait(3600)
                    continue
                if self.slow() and not self.cfg.get("process_allow_slow"):
                    self._set(state="slow", message=self._slow_text(), key=None, pct=None, stage=None)
                    sig = self._slow_sig()
                    while self._slow_sig() == sig:
                        self._wait(3600)
                    continue
                if self.leftovers:
                    self._release_leftovers()
                job, why, counts = self.claim(pipe)
                if job is None:
                    self._set(state="idle", message=why, key=None, ev=None, pct=None, stage=None, counts=counts)
                    backoff = 0
                    self._wait(IDLE_S)
                    continue
                if self._too_large(job):                      # paused until the user allows it, changes the
                    sig = self._large_sig()                   # warning or turns processing off
                    while self._large_sig() == sig:
                        self._wait(3600)
                    continue
                self.run_job(job, pipe, py)
                backoff = 0
            except Stopped:
                break
            except NeedsSetup as e:
                self._set(state="needs_setup", message=str(e), key=None, pct=None, stage=None)
                self._safe_wait(120)
            except HTTPError as e:
                msg = str(e)
                if e.status == 401:
                    msg = "signed out by the server: sign in again"
                elif e.status == 403:
                    msg = f"the server refused: {msg} (processing needs an admin account)"
                elif e.status == 503:
                    msg = f"the server can't hand out processing jobs: {msg}"
                elif e.status == 409 and "pipeline changed" in msg:
                    self.pipe = None
                    continue                                  # fetch the new pipeline right away
                self._set(state="error", message=msg, key=None, pct=None, stage=None)
                backoff = min(backoff + 1, 6)
                self._safe_wait(15 * 2 ** (backoff - 1))
            except NetError as e:
                self._set(state="waiting", message=f"can't reach the server ({e})", key=None, pct=None, stage=None)
                backoff = min(backoff + 1, 6)
                self._safe_wait(5 * 2 ** (backoff - 1))
            except Exception as e:
                log.exception("processor")
                self._set(state="error", message=f"{e.__class__.__name__}: {e}"[:300], key=None, pct=None,
                          stage=None)
                self._safe_wait(60)

    def _slow_sig(self):
        return self.mode(), self.enabled, bool(self.cfg.get("process_allow_slow")), self.slow()

    def _probe(self):
        try:
            self.net.probe(self.server, count=self._count, up=self.mode() == "clip" or not self.net.rate("up"))
        except Exception as e:                       # a failed speed test must not stop processing
            log.warning("speed test: %s", e)

    def start_netcheck(self):
        if self.net.probing:
            return False
        threading.Thread(target=self._probe, name="speedtest", daemon=True).start()
        return True

    def _large_sig(self):
        return self.warn_mb(), self.large_ok, self.enabled

    def _too_large(self, job):
        """A clip over the warning size (metered links): give the match back and ask the user first."""
        size = int((job.get("clip") or {}).get("size") or 0)
        warn = self.warn_mb()
        if not warn or self.large_ok or size <= warn * 1048576:
            return False
        mb = round(size / 1048576)
        self._release(job["ev"], job["key"], failed=False, reason="clip over the processor's size warning")
        msg = (f"the next clip ({_short(job['key'])}) is {mb} MB, over your {warn} MB warning: allow large clips, "
               "raise the warning, or turn processing off")
        log.info("processing paused: %s", msg)
        self._set(state="confirm", message=msg, large={"key": job["key"], "mb": mb, "warn_mb": warn},
                  key=None, ev=None, pct=None, stage=None)
        return True

    def _safe_wait(self, s):
        try:
            self._wait(s)
        except Stopped:
            pass

    # ------------------------------------------------------------------------------------------------ server
    def _json(self, method, path, body=None, timeout=60, headers=None):
        r = self.server.call(method, path, body, timeout=timeout, headers=headers)
        if r.status == 403 and method != "GET" and "csrf" in detail_text(r.body).lower():
            self.server.refresh_session()           # the server's CSRF token changed: fetch it, send once more
            r = self.server.call(method, path, body, timeout=timeout, headers=headers)
        d = r.json()
        if not r.ok:
            raise HTTPError(r.status, d if d is not None else r.body)
        return d

    def _download(self, url, dst, size, sha, what, progress=None):
        """GET url -> dst, resumed with Range after a broken link (parts/ survive restarts), checked against
        size + sha256, moved into place atomically."""
        if not URL_RE.match(url or "") or ".." in url or not SHA_RE.match(sha or ""):
            raise HTTPError(502, None, f"{what}: bad download reference from the server")
        part = os.path.join(self.parts, f"{sha}.part")
        tries = 0
        while True:
            if self.stop_ev.is_set():
                raise Stopped()
            have = os.path.getsize(part) if os.path.exists(part) else 0
            if have > size:
                os.remove(part)
                have = 0
            if have < size:
                h = {"Accept": "*/*"}
                if have:
                    h["Range"] = f"bytes={have}-"
                t_req, b_req = time.time(), have
                try:
                    r = self.server.call("GET", url, headers=h, stream=True, timeout=60)
                    try:
                        if r.status not in (200, 206):
                            data = r.read(65536)
                            r.close()
                            if r.status in (502, 503, 504):
                                raise NetError(f"server busy (HTTP {r.status})")
                            raise HTTPError(r.status, data, f"{what}: HTTP {r.status} {detail_text(data)}".strip())
                        mode = "ab"
                        if r.status == 200:
                            mode, have = "wb", 0
                        elif not (r.header("Content-Range") or "").startswith(f"bytes {have}-"):
                            r.close(broken=True)
                            _rm(part)
                            raise NetError("the server resumed at the wrong offset; restarting the download")
                        with open(part, mode) as f:
                            while True:
                                if self.stop_ev.is_set():
                                    r.close(broken=True)
                                    raise Stopped()
                                b = r.read(256 * 1024)
                                if not b:
                                    break
                                f.write(b)
                                have += len(b)
                                self._count("down", len(b))
                                if progress:
                                    progress(have / max(1, size))
                        r.close()
                        self.net.add("down", have - b_req, time.time() - t_req, "clip")
                        if have < size:           # the link dropped mid-file: resume from here
                            raise NetError(f"connection closed after {have} of {size} bytes")
                    except BaseException:
                        r.close(broken=True)
                        raise
                except NetError as e:
                    if os.path.exists(part) and os.path.getsize(part) > b_req:
                        self.net.add("down", os.path.getsize(part) - b_req, time.time() - t_req, "clip")
                    tries += 1
                    if tries > 8:
                        raise
                    log.warning("%s: %s; resuming in %d s", what, e, min(30, 2 * tries))
                    if self.stop_ev.wait(min(30, 2 * tries)):
                        raise Stopped()
                    continue
            got = os.path.getsize(part)
            if got != size or _sha256(part) != sha:
                os.remove(part)
                tries += 1
                if tries > 3:
                    raise NetError(f"{what}: the download does not match the server's checksum")
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.replace(part, dst)
            return dst

    def ensure_pipeline(self, model=True):
        """The server's pipeline version -> code dir + model file here (downloaded once per version; model=False:
        Clip only, which needs no detector)."""
        d = self._json("GET", API + "/pipeline")
        v = d.get("version") or ""
        if not VER_RE.match(v):
            raise HTTPError(502, None, "the server sent a bad pipeline version")
        pdir = os.path.join(self.root, "pipelines", v)
        if not os.path.isfile(os.path.join(pdir, "runner.py")):
            c = d["code"]
            self._set(state="updating", message=f"downloading the processing code ({c['size'] >> 10} KB)")
            tar = os.path.join(self.root, "pipelines", f"{v}.tar.gz")
            self._download(c["url"], tar, int(c["size"]), c["sha256"], "processing code")
            ensure_dir(os.path.dirname(pdir))
            extract_code(tar, pdir, os.path.dirname(pdir))
            os.remove(tar)
            log.info("processing code %s ready", v)
        m = d.get("model")
        if not m and model:
            raise NeedsSetup("the server has no detector model yet (data/models/robot.onnx)")
        mp = os.path.join(self.root, "models", f"{(m or {}).get('sha256')}.onnx")
        if m and not SHA_RE.match(m.get("sha256") or ""):
            raise HTTPError(502, None, "the server sent a bad model checksum")
        if model and not os.path.isfile(mp):
            self._set(state="updating", message=f"downloading the detector model ({m['size'] >> 20} MB)")
            self._download(m["url"], mp, int(m["size"]), m["sha256"], "detector model")
            log.info("detector model %s ready", m["sha256"][:12])
        if not os.path.isfile(mp):
            mp = None
        self.pipe = {"version": v, "dir": pdir, "model": mp, "heartbeat_s": int(d.get("heartbeat_s") or 60),
                     "requirements": d.get("requirements") or [], "python": d.get("python") or "3.12",
                     "runtimes": d.get("runtimes") or {}, "clip_extras": d.get("clip_extras"),
                     "ytdlp_requirements": d.get("ytdlp_requirements")}
        if mp:
            self._prune(v, mp)
        return self.pipe

    def _prune(self, keep_v, keep_model):
        for base, keep in ((os.path.join(self.root, "pipelines"), keep_v),
                           (os.path.join(self.root, "models"), os.path.basename(keep_model))):
            try:
                names = os.listdir(base)
            except OSError:
                continue
            for n in names:
                p = os.path.join(base, n)
                if n != keep and time.time() - os.path.getmtime(p) > 3600:
                    shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else _rm(p)

    def _setup_hint(self):
        g = self.gpu_info or {}
        sz = PS.SIZES.get(g.get("runtime") or "clip")
        dl = sz["download"] / PS.GB
        return (f"Run Set up processing (it installs the {sz['label']}: about "
                + (f"{dl:.1f} GB" if dl >= 1 else f"{sz['download'] >> 20} MB") + " to download)")

    def check_python(self, pipe, mode="track"):
        py = self.python()
        if not py:
            raise NeedsSetup(self._setup_hint() + ", or choose a Python that has the pipeline's packages")
        if not os.path.isfile(py):
            raise NeedsSetup(f"processing Python not found: {py}")
        k = (py, os.path.getmtime(py), pipe["version"], mode)
        if k not in self._py_ok:
            mods = IMPORTS if mode == "track" else CLIP_IMPORTS
            code = "import sys\n" + "".join(f"import {m}\n" for m in mods)
            if mode == "clip":                           # livestream.py runs ffmpeg by name
                code += ("try:\n import imageio_ffmpeg; imageio_ffmpeg.get_ffmpeg_exe()\nexcept Exception:\n"
                         " import shutil\n assert shutil.which('ffmpeg'), 'no ffmpeg (Set up installs imageio-ffmpeg)'\n")
            code += "print(sys.version.split()[0])"
            try:
                p = subprocess.run([py, "-c", code], capture_output=True, text=True, timeout=180, env=child_env(),
                                   cwd=self.root)
                err = None if p.returncode == 0 else (p.stderr.strip().splitlines() or ["failed"])[-1][:300]
            except (OSError, subprocess.SubprocessError) as e:
                err = f"{e.__class__.__name__}: {e}"[:300]
            self._py_ok = {kk: v for kk, v in self._py_ok.items() if kk[3] != mode}
            self._py_ok[k] = err
        if self._py_ok[k]:
            what = "track matches" if mode == "track" else "record and cut clips (Clip only)"
            raise NeedsSetup(f"{py} can't {what}: {self._py_ok[k]}. {self._setup_hint()}")
        return py

    # ------------------------------------------------------------------------------------------------ benchmark
    def _bench_needed(self, py):
        b, g = self.bench, self.gpu_info or {}
        return not b or b.get("v") != BENCH_V or b.get("python") != py or b.get("runtime") != g.get("runtime")

    def start_bench(self):
        """POST /client/v1/processing/bench: run the benchmark now (in its own thread). -> False if one runs."""
        if self.bench_lock.locked():
            return False

        def go():
            try:
                pipe = self.pipe or self.ensure_pipeline()
                self.benchmark(pipe, self.check_python(pipe, "track"))
            except Exception as e:
                log.warning("benchmark: %s", e)
                with self.lock:
                    self.bench_st = {"state": "error", "message": str(e)[:300], "at": time.time()}
        threading.Thread(target=go, name="benchmark", daemon=True).start()
        return True

    def _bench_sample(self):
        """The start of a recent clip from the server (cached by checksum), or None (synthetic frames)."""
        try:
            d = self._json("GET", API + "/sample", timeout=30)
        except (NetError, HTTPError) as e:
            log.info("benchmark sample: %s", e)
            return None
        if d.get("typical_clip_bytes"):
            self.net.clip_bytes = int(d["typical_clip_bytes"])
        c = d.get("clip")
        if not c:
            return None
        dst = os.path.join(self.root, "bench", f"sample-{c['sha256'][:16]}.mp4")
        if os.path.isfile(dst):
            return dst
        bd = ensure_dir(os.path.join(self.root, "bench"))
        for n in os.listdir(bd):
            if n.startswith("sample-"):
                _rm(os.path.join(bd, n))
        with self.lock:
            self.bench_st = dict(self.bench_st or {}, message=f"downloading a sample clip ({int(c['size']) >> 20} MB)")
        return self._download(c["url"], dst, int(c["size"]), c["sha256"], "benchmark sample")

    def benchmark(self, pipe, py):
        """runner.py --bench: decode a sample clip + time the detector -> bench.json with the minutes per match."""
        if not self.bench_lock.acquire(blocking=False):
            while self.bench_lock.locked() and not self.stop_ev.wait(1):
                pass
            return self.bench
        try:
            g = self.gpu()
            with self.lock:
                self.bench_st = {"state": "running", "message": "getting a sample clip", "pct": 0,
                                 "started": time.time()}
            sample = self._bench_sample()
            work = os.path.join(self.root, "bench", "work")
            shutil.rmtree(work, ignore_errors=True)
            ensure_dir(work)
            job = {"code": pipe["dir"], "model": pipe["model"], "sample": sample, "work": work, "env": gpu_env(g),
                   "det_frames": 40, "decode_frames": 900, "coreml_cache": os.path.join(self.root, "coreml-cache")}
            jp = os.path.join(work, "job.json")
            write_json(jp, job, mode=0o600)
            final, lines = {}, []
            with self.lock:
                self.bench_st.update(message="decoding + running the detector", pct=5)
            p = subprocess.Popen([py, "-u", os.path.join(pipe["dir"], "runner.py"), "--bench", jp], cwd=work,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE,
                                 env=child_env(), text=True, encoding="utf-8", errors="replace", bufsize=1,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            killer = threading.Timer(900, p.kill)
            killer.daemon = True
            killer.start()
            try:
                for line in p.stdout:
                    if line.startswith(PROTO):
                        try:
                            msg = json.loads(line[len(PROTO):])
                        except ValueError:
                            continue
                        if msg.get("done"):
                            final = msg
                        elif isinstance(msg.get("progress"), (int, float)):
                            with self.lock:
                                self.bench_st.update(pct=int(100 * msg["progress"]), message=msg.get("stage"))
                    else:
                        lines.append(line)
                code = p.wait()
            finally:
                killer.cancel()
                p.stdout.close()
                p.stdin.close()
            with open(os.path.join(self.logs, "benchmark.log"), "w", encoding="utf-8", errors="replace") as f:
                f.writelines(lines[-400:])
            r = final.get("bench")
            if code != 0 or not r:
                tail = "".join(lines[-3:]).strip()[-300:]
                raise RuntimeError(f"the benchmark failed (exit {code}): {tail or 'no result'}")
            want = {"cuda": "CUDAExecutionProvider", "coreml": "CoreMLExecutionProvider"}.get(g.get("kind"))
            ok = bool(want) and r.get("provider") == want
            why = None
            if not ok:
                cause = next((m.group(1).strip() for m in (re.search(r"CUDA failure \d+: ([^;]+)", x) for x in lines)
                              if m), None)
                if cause and "out of memory" in cause:
                    cause = "the GPU's memory is full: close other programs that use it"
                why = (f"the GPU did not work in the benchmark: onnxruntime ran on {r.get('provider') or 'the CPU'} "
                       f"instead of {want or 'a GPU'}" + (f" ({cause})" if cause else "")
                       + f"; details in {os.path.join(self.logs, 'benchmark.log')}")
            est = estimate_min(r)
            b = {"v": BENCH_V, "at": time.time(), "python": py, "runtime": g.get("runtime"), "gpu": g.get("name"),
                 "provider": r.get("provider"), "det_fps": r.get("det_fps"),
                 "decode_fps": (r.get("decode") or {}).get("fps"), "frames": r.get("frames"),
                 "warmup_s": r.get("warmup_s"), "est_min": est, "ok": ok, "why": why,
                 "seconds": final.get("seconds")}
            write_json(self.bench_p, b)
            self.bench = b
            log.info("benchmark: %s, detector %.1f fps, decode %s fps -> about %s min per match", b["provider"],
                     b["det_fps"] or 0, b["decode_fps"], est)
            with self.lock:
                self.bench_st = {"state": "done", "message": None, "pct": 100, "at": time.time()}
            self.wake.set()
            return b
        except Exception as e:
            with self.lock:
                self.bench_st = {"state": "error", "message": str(e)[:300], "at": time.time()}
            raise
        finally:
            self.bench_lock.release()

    # ------------------------------------------------------------------------------------------------ Clip only
    def run_clip(self, pipe, py):
        body = {"processor": self.pid}
        evs = self.cfg.get("process_events")
        if evs is not None:
            body["events"] = list(evs)
        d = self._json("POST", API + "/streams/claim", body)
        a = d.get("assignment")
        if not a:
            self._set(state="idle", message=f"no stream to record right now ({d.get('why') or 'nothing assigned'})",
                      key=None, ev=None, pct=None, stage=None)
            self._wait(IDLE_S * 2)
            return
        job = ClipJob(self, a, pipe, py)
        self.clip_job = job
        self._set(state="clip", message=None, key=None, ev=a["ev"], pct=None, stage=None)
        reason = "stopped"
        try:
            job.run()
            reason = "turned off" if not self.enabled else "switched to tracking" if self.mode() != "clip" else "stopped"
        except StreamLost as e:
            log.warning("clip only: %s", e)
            reason = None
            self._set(state="idle", message=str(e))
        finally:
            self.clip_last = job.status()
            self.clip_job = None
            if reason and (not self.enabled or self.mode() != "clip"):     # a client restart keeps (resumes) it
                job.release(reason)
        if self.stop_ev.is_set():
            raise Stopped()

    def claim(self, pipe):
        now = time.time()
        self.skip = {k: t for k, t in self.skip.items() if t > now}
        body = {"processor": self.pid, "pipeline": pipe["version"], "skip": sorted(self.skip)[:500]}
        evs = self.cfg.get("process_events")
        if evs is not None:
            body["events"] = list(evs)
        d = self._json("POST", API + "/claim", body)
        return d.get("job"), d.get("why"), d.get("counts")

    # ------------------------------------------------------------------------------------------------ one job
    def run_job(self, job, pipe, py):
        ev, key = job.get("ev"), job.get("key")
        mm = MATCH_RE.match(key or "")
        if not (EVENT_RE.match(ev or "") and mm and mm.group(1) == ev):
            raise HTTPError(502, None, "the server sent a bad match key")
        if key in self.inflight:                    # never two jobs (or downloads) of the same match
            log.warning("%s: already being processed here; not starting it twice", key)
            return
        t0 = time.time()
        self.inflight[key] = {"ev": ev, "at": t0}
        self._save_inflight()
        self._set(state="downloading", key=key, ev=ev, pct=0, stage="downloading", message=None, since=t0,
                  large=None)
        log.info("%s: claimed; downloading the clip (%d MB)", key, (job["clip"]["size"] or 0) >> 20)
        work = os.path.join(self.root, "work", ev, key)
        shutil.rmtree(work, ignore_errors=True)
        ensure_dir(work)
        beat = _Heartbeat(self, ev, key, int(job.get("heartbeat_s") or pipe["heartbeat_s"]))
        beat.start()
        released = False
        try:
            clip = self._download(job["clip"]["url"], os.path.join(work, "clip.mp4"), int(job["clip"]["size"]),
                                  job["clip"]["sha256"], f"{key} clip",
                                  progress=lambda f: self._set(pct=int(5 * f)))
            self._count("clips", 1)
            vref = None
            if job.get("view_ref"):
                vr = job["view_ref"]
                vref = self._download(vr["url"], os.path.join(work, "view.png"), int(vr["size"]), vr["sha256"],
                                      f"{key} view reference")
            beat.check()
            t_dl = time.time() - t0
            self._set(state="processing", pct=5, stage="processing")
            t_p = time.time()
            out = self._process(job, pipe, py, work, clip, vref, beat)
            t_p = time.time() - t_p
            beat.check()
            if not out.get("ok"):
                why = out.get("error") or out.get("note") or f"exit code {out.get('code')}"
                log.warning("%s: failed here (%s); released", key, str(why)[:200])
                self.skip[key] = time.time() + SKIP_S
                self._release(ev, key, failed=True, reason=str(why)[:300])
                released = True
                self._set(failed=self.st["failed"] + 1, last={"key": key, "ok": False, "at": time.time(),
                                                               "error": str(why)[:300]})
                return
            self._set(state="uploading", pct=95, stage="uploading")
            files = out.get("files") or {}
            evd = os.path.realpath(os.path.join(work, "data", "events", ev))
            n = 0
            for kind in KINDS:                          # matches last (the server publishes it last anyway)
                p = files.get(kind)
                if not p:
                    continue
                rp = os.path.realpath(p)
                if not rp.startswith(evd + os.sep) or not os.path.isfile(rp):
                    raise RuntimeError(f"runner reported an unexpected file: {p}")
                self._upload(ev, key, kind, rp)
                n += 1
                self._set(pct=95 + int(4 * n / len(files)))
            beat.stop()
            secs = round(time.time() - t0, 1)
            d = self._retry(lambda: self._json("POST", f"{API}/{ev}/{quote(key)}/complete",
                                               {"processor": self.pid, "seconds": secs},
                                               headers={"Idempotency-Key": f"proc-{self.pid}-{key}-{int(t0)}"}))
            released = True
            log.info("%s: done in %.0f s (pipeline %.0f s), uploaded (%s)", key, secs, t_p,
                     ", ".join(d.get("files") or []))
            self._set(done=self.st["done"] + 1, pct=100,
                      last={"key": key, "ok": True, "at": time.time(), "seconds": secs})
            self.perf = (self.perf + [{"key": key, "at": time.time(), "process_s": round(t_p, 1),
                                       "download_s": round(t_dl, 1), "total_s": secs,
                                       "clip_bytes": int(job["clip"]["size"])}])[-20:]
            self.net.clip_bytes = int(job["clip"]["size"]) or self.net.clip_bytes
            try:
                write_json(self.perf_p, self.perf)
            except OSError:
                pass
        except Lost:
            log.warning("%s: the claim was lost (taken over / stale); dropped", key)
            self.skip[key] = time.time() + SKIP_S
            released = True
        except _Off:
            log.info("%s: turned off; the match is given back", key)
        except Stopped:
            raise
        except (NetError, HTTPError) as e:
            log.warning("%s: %s; giving the match back", key, e)
            self.skip[key] = time.time() + 600
            raise
        finally:
            beat.stop()
            if not released:
                self._release(ev, key, failed=False, reason="stopped" if not self.enabled else "error here")
            self._keep_log(work, key)
            shutil.rmtree(work, ignore_errors=True)
            self.inflight.pop(key, None)
            self._save_inflight()
            self._set(key=None, ev=None, pct=None, stage=None, state="idle" if self.enabled else "off")

    def _process(self, job, pipe, py, work, clip, vref, beat):
        """Run runner.py with the processing Python; -> its final {"done": true, ...} message (+ "code")."""
        live = dict(job["live"], clip=clip, view_ref=vref)
        spec = {"code": pipe["dir"], "work": work, "model": pipe["model"], "worker_id": job.get("by"),
                "env": pipeline_env(self.gpu_info), "ev": job["ev"], "match": job["match"], "event": job.get("event"),
                "live": live, "camera": job["camera"], "pose_guess": job.get("pose_guess"),
                "coreml_cache": os.path.join(self.root, "coreml-cache")}
        jp = os.path.join(work, "job.json")
        write_json(jp, spec, mode=0o600)
        logf = open(os.path.join(work, "runner.log"), "w", encoding="utf-8", errors="replace")
        final = {}
        try:
            self.child = subprocess.Popen([py, "-u", os.path.join(pipe["dir"], "runner.py"), jp], cwd=work,
                                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE,
                                          env=child_env(), text=True, encoding="utf-8", errors="replace", bufsize=1)
            killer = threading.Timer(JOB_TIMEOUT_S, self._kill_child)
            killer.daemon = True
            killer.start()
            try:
                for line in self.child.stdout:
                    if line.startswith(PROTO):
                        try:
                            msg = json.loads(line[len(PROTO):])
                        except ValueError:
                            msg = {}
                        if msg.get("done"):
                            final = msg
                        elif isinstance(msg.get("progress"), (int, float)):
                            p = max(0.0, min(1.0, float(msg["progress"])))
                            self._set(pct=5 + int(90 * p))
                            beat.progress = 0.05 + 0.9 * p
                    else:
                        logf.write(line)
                        logf.flush()
                code = self.child.wait()
            finally:
                killer.cancel()
        finally:
            logf.close()
            if self.child is not None and self.child.stdout is not None:
                self.child.stdout.close()
                self.child.stdin.close()
            self.child = None
        if beat.lost:
            raise Lost()
        if not self.enabled:
            raise Stopped() if self.stop_ev.is_set() else _Off()
        final.setdefault("ok", False)
        if code != 0:
            final["ok"] = False
            final.setdefault("error", f"the pipeline exited with code {code} (see {self.logs}/{job['key']}.log)")
        final["code"] = code
        return final

    def _upload(self, ev, key, kind, path):
        with open(path, "rb") as f:
            raw = f.read()
        body = gzip.compress(raw, 6)
        self._count("up", len(body))

        def put():
            r = self.server.call("PUT", f"{API}/{ev}/{quote(key)}/results/{kind}?processor={quote(self.pid)}", body,
                                 headers={"Content-Encoding": "gzip"}, ctype="application/json", timeout=120)
            d = r.json()
            if r.status == 409:
                raise Lost()
            if not r.ok:
                raise HTTPError(r.status, d if d is not None else r.body)
            return d
        self._retry(put)

    def _retry(self, fn, tries=6):
        for k in range(tries):
            try:
                return fn()
            except NetError as e:
                if k == tries - 1:
                    raise
                log.warning("upload: %s; retrying", e)
                if self.stop_ev.wait(min(30, 3 * (k + 1))):
                    raise Stopped()

    def _release(self, ev, key, failed, reason):
        try:
            self._json("POST", f"{API}/{ev}/{quote(key)}/release",
                       {"processor": self.pid, "failed": bool(failed), "reason": reason}, timeout=20)
            return True
        except (NetError, HTTPError) as e:
            log.warning("%s: release failed (%s); the claim goes stale on the server", key, e)
            return False

    def _keep_log(self, work, key):
        src = os.path.join(work, "runner.log")
        if os.path.exists(src):
            try:
                shutil.copyfile(src, os.path.join(self.logs, f"{key}.log"))
            except OSError:
                pass
        try:
            ls = sorted(os.listdir(self.logs), key=lambda n: os.path.getmtime(os.path.join(self.logs, n)))
            for n in ls[:-30]:
                _rm(os.path.join(self.logs, n))
        except OSError:
            pass

    # ------------------------------------------------------------------------------------------------ setup
    def start_setup(self):
        """One-time "Set up processing": a Python 3.12 venv with the pipeline's packages (uv), the code and model.
        -> False if one is already running."""
        with self.lock:
            if self.setup_st and self.setup_st.get("state") == "running":
                return False
            self.setup_st = {"state": "running", "message": "starting", "log": [], "started": time.time()}
        threading.Thread(target=self._setup, name="processing-setup", daemon=True).start()
        return True

    def _setup_log(self, msg):
        log.info("setup: %s", msg)
        with self.lock:
            self.setup_st["message"] = msg
            self.setup_st["log"].append(msg)

    def _setup(self):
        try:
            g = self.gpu()
            self._setup_log("getting the processing code and model from the server")
            pipe = self.ensure_pipeline()
            pl = PS.plan(self.root, pipe, g)
            if not pl["ok"]:
                raise RuntimeError(pl["why"])
            if not g.get("ok"):
                self._setup_log(f"no usable GPU ({g.get('why')}): installing the Clip only tools")
            inst = PS.Installer(self.root, pipe, g, self._setup_log, PS.uv_env(self.root, child_env()),
                                run_cmd=self.setup_cmd, stop=self.stop_ev)
            self.installer = inst
            py, info = inst.install()
            self._py_ok = {}
            if self.cfg.get("process_python") and self.cfg["process_python"] != py:
                self._setup_log(f"note: Settings still name {self.cfg['process_python']}; clear it to use the new one")
            self.check_python(dict(pipe), "track" if g.get("ok") else "clip")
            if g.get("ok"):
                with self.lock:
                    self.setup_st.update(message="installed; measuring this computer (benchmark)")
                try:
                    b = self.benchmark(pipe, py)
                    if b:
                        self._setup_log(f"benchmark: about {b.get('est_min')} min per match on {b.get('provider')}"
                                        + ("" if b.get("ok") else f" ({b.get('why')})"))
                except Exception as e:                  # the install is fine; the benchmark runs again later
                    self._setup_log(f"benchmark failed: {e}")
            with self.lock:
                self.setup_st.update(state="done", message=f"ready: {py}", finished=time.time())
            self.wake.set()
        except Exception as e:
            log.warning("setup failed: %s", e)
            with self.lock:
                self.setup_st.update(state="error", message=str(e)[:500], finished=time.time())
        finally:
            self.installer = None

    def setup_progress(self):
        inst = getattr(self, "installer", None)
        p = inst.progress if inst else None
        if not p or not p.get("expect"):
            return None
        return {"bytes": p["bytes"], "expect": p["expect"], "pct": min(99, int(100 * p["bytes"] / p["expect"]))}


class _Off(Stopped):
    """Turned off mid-job (the loop continues; the claim is released)."""


class _Heartbeat:
    """POST .../heartbeat every `every` s while a job runs; marks the job lost (and stops the pipeline) on 409."""

    def __init__(self, proc, ev, key, every):
        self.proc, self.ev, self.key, self.every = proc, ev, key, max(MIN_BEAT, every)
        self.ev_stop = threading.Event()
        self.lost = False
        self.progress = 0.0
        self.t = threading.Thread(target=self.run, name="heartbeat", daemon=True)

    def start(self):
        self.t.start()

    def stop(self):
        self.ev_stop.set()

    def check(self):
        if self.lost:
            raise Lost()

    def run(self):
        while not self.ev_stop.wait(self.every):
            st = self.proc.st.get("stage") or "processing"
            try:
                r = self.proc.server.call("POST", f"{API}/{self.ev}/{quote(self.key)}/heartbeat",
                                          {"processor": self.proc.pid, "progress": round(self.progress, 3),
                                           "stage": st}, timeout=20)
                if r.status == 409:
                    self.lost = True
                    self.proc._kill_child()
                    return
            except NetError:
                pass                    # the claim is good for CLAIM_S (30 min); next beat


def _sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _rm(p):
    try:
        os.remove(p)
    except OSError:
        pass

