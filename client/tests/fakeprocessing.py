"""A fake of the server's processing API (server/routers/processing.py) for the client's processor-mode tests,
hooked into fakeserver.Handler (state.proc).  Its pipeline tarball holds a fake runner.py that writes result files
the way client/csclient/procrunner.py does (the real one needs numpy/cv2/onnxruntime); the match's "fake" field
picks its behaviour: ok | fail | slow."""
import gzip
import hashlib
import io
import json
import os
import re
import socket
import tarfile
import threading
import time

FAKE_RUNNER = r'''
import json, os, sys, time
PROTO = "@@CS "
def emit(**kw): print(PROTO + json.dumps(kw), flush=True)
if sys.argv[1] == "--bench":            # benchmark: the provider the env asks for, speed from CS_FAKE_DET_FPS
    job = json.load(open(sys.argv[2]))
    env = job["env"]
    prov = ("CoreMLExecutionProvider" if env.get("ORT_COREML") == "1" else "CUDAExecutionProvider"
            if "GPU_DEVICE" in env else "CPUExecutionProvider")
    prov = os.environ.get("CS_FAKE_PROVIDER") or prov
    emit(progress=0.5, stage="detector")
    dec = {"frames": 900, "fps": 300.0, "w": 1920, "h": 1080} if job.get("sample") else None
    emit(done=True, ok=True, bench={"provider": prov, "det_fps": float(os.environ.get("CS_FAKE_DET_FPS", "60")),
                                    "det_frames": 40, "warmup_s": 0.1, "load_s": 0.1, "decode": dec,
                                    "frames": "sample" if dec else "synthetic", "sample_bytes":
                                    os.path.getsize(job["sample"]) if job.get("sample") else None}, seconds=0.2)
    sys.exit(0)
if sys.argv[1] == "--clip":             # Clip only: "records" and cuts every wanted match into rec/<ev>/r1/
    job = json.load(open(sys.argv[2]))
    d = os.path.join(job["root"], job["ev"], "r1")
    os.makedirs(os.path.join(d, "matches"), exist_ok=True); os.makedirs(os.path.join(d, "clips"), exist_ok=True)
    done = set(); n = 0
    while True:
        n += 1
        emit(rec={"state": "recording", "msg": None, "chunks": n, "bytes": 1000 * n, "got_bytes": 2000 * n,
                  "last_wall": time.time(), "behind_s": 6.0, "video": "fakevid", "dir": "r1"})
        try: w = json.load(open(job["want"]))
        except Exception: w = {}
        ms = {m["key"]: m for m in w.get("matches") or []}
        for k in w.get("want") or []:
            if k in done or k not in ms: continue
            if os.environ.get("CS_FAKE_CUT_WAIT"):
                emit(wait={"key": k, "why": os.environ["CS_FAKE_CUT_WAIT"]}); continue
            mid = "tba-" + k.split("_", 1)[1]
            clip = os.path.join(d, "clips", mid + ".mp4")
            with open(clip, "wb") as f:
                f.write(b"\0\0\0\x14ftypisom" + os.urandom(int(os.environ.get("CS_FAKE_CLIP_BYTES", "300000"))))
            rec = {"id": mid, "match_name": "Qualification", "comp_level": "qm", "set": 1,
                   "number": int(k.split("qm")[1]), "teams": {"red": [1, 2, 3], "blue": [4, 5, 6]},
                   "score": None, "score_source": None, "breakdown": None, "alliance_names": None,
                   "t_start_stream": 100.0, "t_start_utc": "2026-10-18T18:00:00.00Z", "clip": clip,
                   "auto_start_in_clip": 15.0, "confidence": {"match": 0.5}, "flags": ["cut_by_tba_time"],
                   "evidence": {}, "cut_from": {"actual_time": ms[k]["actual_time"], "lead": 15.0, "at": time.time()}}
            json.dump(rec, open(os.path.join(d, "matches", mid + ".json"), "w"))
            done.add(k)
            emit(cut={"key": k, "record": os.path.join(d, "matches", mid + ".json")})
        time.sleep(0.2)
job = json.load(open(sys.argv[1]))
m, ev = job["match"], job["ev"]
key, mode = m["key"], m.get("fake", "ok")
assert os.path.getsize(job["live"]["clip"]) > 0 and os.path.exists(job["model"])
assert job["camera"]["data"]["source"] == "yt1" and job["live"]["layout"]["processing_source"] == "yt1"
print("runner: tracking", key, "with", os.path.basename(job["code"]), flush=True)
emit(progress=0.0, stage="processing")
if mode == "slow":
    for i in range(300):
        time.sleep(0.1)
        emit(progress=min(0.8, i / 100), stage="processing")
if mode == "fail":
    emit(done=True, ok=False, files={}, error="only 2 AprilTags found")
    sys.exit(1)
evd = os.path.join(job["work"], "data", "events", ev)
files = {}
for kind, obj in (("fuel", {"tracks": []}), ("shots", {"shots": [1, 2]}), ("analytics", {"a": 1}),
                  ("matches", {"key": key, "video": job["live"]["video"], "feed_ok": 0.95, "source": "livestream",
                               "live": job["live"]["info"], "env": job["env"], "work": job["work"]})):
    p = os.path.join(evd, kind, key + ".json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(obj, open(p, "w"))
    files[kind] = p
    emit(progress=0.5, stage="processing")
emit(done=True, ok=True, files=files, seconds=0.1)
'''


def _tar(files):
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz, tarfile.open(fileobj=gz, mode="w") as t:
        for name, data in files.items():
            ti = tarfile.TarInfo(name)
            ti.size, ti.mode = len(data), 0o644
            t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def sha(b):
    return hashlib.sha256(b).hexdigest()


class FakeProcessing:
    EV = "2026test"

    def __init__(self, modes=("ok",)):
        self.lock = threading.RLock()
        self.version = "p-" + "a" * 12
        self.code = _tar({"runner.py": FAKE_RUNNER.encode(), "live/worker.py": b"# fake\n",
                          "requirements.txt": b"numpy\n", "PIPELINE.json": b"{}"})
        self.model = b"onnx-model-bytes" * 64
        self.clip = os.urandom(400_000)
        self.queue = [f"{self.EV}_qm{i + 1}" for i in range(len(modes))]
        self.modes = {k: md for k, md in zip(self.queue, modes)}
        self.claims = {}            # key -> processor
        self.uploads = {}           # key -> {kind: obj}
        self.completed = []
        self.released = []          # (key, failed, reason)
        self.beats = []
        self.claim_bodies = []
        self.cut_clip_after = None  # bytes: the first clip download is cut there
        self.lose_claim = False     # heartbeats answer 409
        self.code_gets = self.model_gets = 0
        self.sample = None          # bytes of the benchmark sample (None: the server has no clips)
        self.speed_bps = None       # speed test: throttle to this many bytes/s
        # Clip only: one stream; matches with TBA times; which ones the server wants
        self.stream = {"aid": "cs0123456789", "ev": self.EV, "source": "yt1", "url": "https://www.youtube.com/live",
                       "label": "Main stream", "role": "backup", "heartbeat_s": 1, "chunk_bytes": 64 << 10,
                       "chunk_max": 16 << 20, "max_clip_bytes": 1 << 30}
        self.stream_by = None
        self.stream_matches = []
        self.stream_want = []
        self.stream_skipped = []
        self.stream_beats = []
        self.stream_released = []
        self.stream_stop = False
        self.clip_uploads = {}      # mid -> {"size", "sha256", "data": bytearray}
        self.clips_done = {}        # mid -> (record, bytes)
        self.chunk_puts = 0
        self.cut_chunk_once = False  # the first chunk PUT breaks the connection

    def job(self, key, pid):
        base = f"/app/v1/processing/{self.EV}/{key}"
        q = f"?processor={pid}"
        return {"ev": self.EV, "key": key, "by": f"app-u1-{pid}", "processor": pid, "claim_s": 1800,
                "heartbeat_s": 1, "pipeline": self.version,
                "match": {"key": key, "comp_level": "qm", "fake": self.modes[key]},
                "event": {"teams": ["frc1"], "matches": [], "key": key},
                "live": {"start": 12.5, "start_trusted": True, "teams": {"red": [], "blue": []}, "source": "yt1",
                         "tba_posted": True, "layout": {"processing_source": "yt1"},
                         "info": {"record_path": "/srv/media/live/x/matches/qm1.json"},
                         "video": {"id": "live-x-qm1", "title": "Livestream", "channel": "livestream"}},
                "camera": {"name": "camera.json", "data": {"source": "yt1"}}, "pose_guess": None,
                "clip": {"url": f"{base}/clip{q}", "size": len(self.clip), "sha256": sha(self.clip)},
                "view_ref": None}

    # ------------------------------------------------------------------ HTTP
    def handle(self, h, m, path, q):
        pid = (q.get("processor") or [None])[0]
        if path == "/app/v1/processing/sample":
            if self.sample is None:
                return h.send(200, {"clip": None, "typical_clip_bytes": None, "why": "no clips"})
            return h.send(200, {"clip": {"url": "/app/v1/processing/sample.mp4?ev=x&name=y.mp4",
                                         "size": len(self.sample), "sha256": sha(self.sample)},
                                "typical_clip_bytes": 191_000_000, "full_bytes": 191_000_000})
        if path == "/app/v1/processing/sample.mp4":
            return h.ranged(self.sample or b"", "video/mp4")
        if path == "/app/v1/processing/speedtest":
            if m == "POST":
                n = len(h.body())
                if self.speed_bps:
                    time.sleep(n / self.speed_bps)
                return h.send(200, {"bytes": n, "seconds": 0.0})
            n = int((q.get("bytes") or ["1048576"])[0])
            if self.speed_bps:
                time.sleep(n / self.speed_bps)
            return h.send(200, b"\0" * n, "application/octet-stream")
        if path.startswith("/app/v1/processing/streams/"):
            return self.handle_stream(h, m, path, q, pid)
        if path == "/app/v1/processing/pipeline":
            return h.send(200, {"version": self.version, "python": "3.12", "requirements": ["numpy"],
                                "claim_s": 1800, "heartbeat_s": 1, "max_upload_bytes": 20 << 20,
                                "code": {"url": f"/app/v1/processing/pipeline/code.tar.gz?v={self.version}",
                                         "size": len(self.code), "sha256": sha(self.code)},
                                "model": {"url": "/app/v1/processing/pipeline/model.onnx", "size": len(self.model),
                                          "sha256": sha(self.model)}})
        if path == "/app/v1/processing/pipeline/code.tar.gz":
            self.code_gets += 1
            return h.ranged(self.code, "application/gzip")
        if path == "/app/v1/processing/pipeline/model.onnx":
            self.model_gets += 1
            return h.ranged(self.model, "application/octet-stream")
        if path == "/app/v1/processing/claim" and m == "POST":
            d = json.loads(h.body() or b"{}")
            self.claim_bodies.append(d)
            if d.get("pipeline") not in (None, self.version):
                return h.send(409, {"detail": "the pipeline changed"})
            with self.lock:
                for k in self.queue:
                    if k not in self.claims and k not in self.completed and k not in (d.get("skip") or []):
                        self.claims[k] = d["processor"]
                        return h.send(200, {"job": self.job(k, d["processor"]), "counts": {}})
            return h.send(200, {"job": None, "why": "nothing to track right now", "counts": {"tracked": 1}})
        mm = re.match(r"^/app/v1/processing/(\w+)/(\w+)/(clip|heartbeat|complete|release|results/(\w+))$", path)
        if not mm:
            return h.send(404, {"detail": "Not Found"})
        key, what = mm.group(2), mm.group(3)
        if what == "clip":
            if self.claims.get(key) != pid:
                return h.send(409, {"detail": "not claimed by this processor"})
            cut, self.cut_clip_after = self.cut_clip_after, None
            if cut is not None and not h.headers.get("Range"):
                h.send_response(200)
                h.send_header("Content-Type", "video/mp4")
                h.send_header("Content-Length", str(len(self.clip)))
                h.end_headers()
                h.wfile.write(self.clip[:cut])
                h.wfile.flush()
                h.close_connection = True
                h.connection.shutdown(socket.SHUT_RDWR)
                return
            return h.ranged(self.clip, "video/mp4")
        d = json.loads(h.body() or b"{}") if what != "results/" + str(mm.group(4)) else None
        if what == "heartbeat":
            self.beats.append(d)
            if self.lose_claim or self.claims.get(key) != d.get("processor"):
                return h.send(409, {"detail": "the claim on this match was lost"})
            return h.send(200, {"ok": True})
        if what.startswith("results/"):
            if self.claims.get(key) != pid:
                return h.send(409, {"detail": "not claimed"})
            raw = h.body()
            if (h.headers.get("Content-Encoding") or "") == "gzip":
                raw = gzip.decompress(raw)
            self.uploads.setdefault(key, {})[mm.group(4)] = json.loads(raw)
            return h.send(200, {"kind": mm.group(4), "size": len(raw)})
        if what == "complete":
            with self.lock:
                if key in self.completed:
                    return h.send(200, {"ok": True, "already": True, "files": [], "media": None})
                if self.claims.get(key) != d.get("processor") or "matches" not in self.uploads.get(key, {}):
                    return h.send(409, {"detail": "upload results/matches first"})
                self.completed.append(key)
                self.claims.pop(key, None)
            return h.send(200, {"ok": True, "already": False, "files": list(self.uploads[key]),
                                "media": {"ok": True}})
        if what == "release":
            with self.lock:
                self.released.append((key, d.get("failed"), d.get("reason")))
                mine = self.claims.get(key) == d.get("processor")
                if mine:
                    self.claims.pop(key)
            return h.send(200, {"released": mine})


    def handle_stream(self, h, m, path, q, pid):
        st = self.stream
        if path == "/app/v1/processing/streams/claim":
            d = json.loads(h.body() or b"{}")
            with self.lock:
                if self.stream_by not in (None, d["processor"]):
                    return h.send(200, {"assignment": None, "why": "every livestream is being recorded"})
                self.stream_by = d["processor"]
            return h.send(200, {"assignment": dict(st, by=f"app-u1-{d['processor']}", processor=d["processor"],
                                                   started=time.time(), at=time.time()), "why": None})
        mm = re.match(r"^/app/v1/processing/streams/(\w+)/(cs[0-9a-f]{10})/(heartbeat|release|clips/([\w-]+)"
                      r"(/data|/finish)?)$", path)
        if not mm or mm.group(2) != st["aid"]:
            return h.send(404, {"detail": "Not Found"})
        what, mid, sub = mm.group(3), mm.group(4), mm.group(5)
        d = json.loads(h.body() or b"{}") if sub != "/data" else {}
        who = d.get("processor") or pid
        if who != self.stream_by:
            return h.send(409, {"detail": "this stream is not assigned to this processor (claim one first)"})
        if what == "heartbeat":
            self.stream_beats.append(d)
            if self.stream_stop:
                return h.send(200, {"ok": False, "stop": True, "why": "the stream was assigned to another processor"})
            have = [f"{self.EV}_{k[4:]}" for k in self.clips_done]
            return h.send(200, {"ok": True, "stop": False, "matches": self.stream_matches,
                                "want": [k for k in self.stream_want if k not in have], "have": have,
                                "skipped": list(self.stream_skipped), "grace_s": 0, "heartbeat_s": 1})
        if what == "release":
            self.stream_released.append(d.get("reason"))
            with self.lock:
                self.stream_by = None
            return h.send(200, {"released": True})
        if sub is None:                       # begin / resume
            u = self.clip_uploads.get(mid)
            if mid in self.clips_done and self.clips_done[mid][2] == d["sha256"]:
                return h.send(200, {"done": True, "offset": d["size"]})
            if not u or u["sha256"] != d["sha256"]:
                u = self.clip_uploads[mid] = {"size": d["size"], "sha256": d["sha256"], "data": bytearray()}
            return h.send(200, {"done": False, "offset": len(u["data"]), "chunk_bytes": st["chunk_bytes"],
                                "chunk_max": st["chunk_max"]})
        if sub == "/data":
            u = self.clip_uploads.get(mid)
            off = int((q.get("offset") or ["0"])[0])
            raw = h.body()
            self.chunk_puts += 1
            if self.cut_chunk_once:
                self.cut_chunk_once = False
                u["data"] += raw[:len(raw) // 2]          # half of it arrived, then the link broke
                h.close_connection = True
                h.connection.shutdown(socket.SHUT_RDWR)
                return
            if u is None or off != len(u["data"]):
                return h.send(409, {"detail": f"expected offset {len(u['data']) if u else 0}"})
            u["data"] += raw
            return h.send(200, {"offset": len(u["data"])})
        if sub == "/finish":
            u = self.clip_uploads.get(mid)
            if u is None:
                if mid in self.clips_done:
                    return h.send(200, {"ok": True, "already": True})
                return h.send(409, {"detail": "no upload in progress"})
            if len(u["data"]) != u["size"] or sha(bytes(u["data"])) != u["sha256"]:
                return h.send(422, {"detail": "the clip does not match its sha256"})
            rec = d.get("record") or {}
            if "clip" in rec or rec.get("id") != mid:
                return h.send(422, {"detail": "bad record"})
            self.clips_done[mid] = (rec, bytes(u["data"]), u["sha256"])
            self.clip_uploads.pop(mid)
            return h.send(200, {"ok": True, "already": False, "key": f"{self.EV}_{mid[4:]}", "record": mid})
        return h.send(404, {"detail": "Not Found"})


def wait_for(cond, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.05)
    return False
