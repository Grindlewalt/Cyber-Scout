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


def wait_for(cond, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.05)
    return False
