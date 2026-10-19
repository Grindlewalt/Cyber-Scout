"""Clip only ("light mode" of Help process matches): for a computer too slow to track, or without a GPU.  It records
a livestream the server assigns (POST /app/v1/processing/streams/claim: a stream the server doesn't record itself,
else a backup of the one it does), cuts every match out of the recording by TBA's start time and uploads the clips
the server is missing (chunked, resumable).  The server registers each one as a livestream match record
(MEDIA/live/<ev>/<aid>/), so its worker or a GPU processor tracks it.

The recording + cutting is the server's own code (live/livestream.py Recorder + ChunkStore, live/liveingest.py
tba_cut) run by runner.py --clip in the processing Python; this module drives it:

  heartbeat (thread, every heartbeat_s): keeps the assignment, gets TBA's match times + the keys the server wants
                                         -> processing/streams/<aid>/want.json (the runner cuts those)
  main loop (the processor thread):      uploads each cut clip the server still wants (records in
                                         rec/<ev>/r*/matches/tba-*.json), deletes local clips once sent or not needed

Files: processing/streams/<aid>/ {rec/ (the recording), want.json, job.json, state.json (uploaded / skipped),
runner.log}."""
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import quote

from .net import HTTPError, NetError
from .util import ensure_dir, read_json, write_json

log = logging.getLogger("cyberscout.clipper")

API = "/app/v1/processing/streams"
PROTO = "@@CS "
AID_RE = re.compile(r"^cs[0-9a-f]{10}$")
MID_RE = re.compile(r"^tba-(qm[0-9]{1,3}|sf[0-9]{1,2}m[0-9]{1,2}|f[0-9]{1,2}m[0-9]{1,2})$")
REC_FIELDS = ("id", "match_name", "comp_level", "set", "number", "teams", "score", "score_source", "t_start_stream",
              "t_start_utc", "auto_start_in_clip", "confidence", "flags", "cut_from")
MIN_FREE = 8 << 30                   # a recording keeps ~2 h of 1080p60 (~7 GB) next to the clips
LOG_MAX = 8 << 20


class StreamLost(Exception):
    pass


def budget_gb(free):
    """Disk the recording may use: 40 % of what is free, 3..12 GB."""
    return round(max(3.0, min(12.0, 0.4 * free / 2 ** 30)), 1)


class ClipJob:
    def __init__(self, proc, a, pipe, py):
        self.proc, self.a, self.pipe, self.py = proc, a, pipe, py
        self.ev, self.aid = a["ev"], a["aid"]
        if not AID_RE.match(self.aid or "") or not re.match(r"^[0-9]{4}[a-z0-9]{1,16}$", self.ev or ""):
            raise HTTPError(502, None, "the server sent a bad stream assignment")
        self.base = ensure_dir(os.path.join(proc.root, "streams", self.aid))
        self.rec_root = ensure_dir(os.path.join(self.base, "rec"))
        self.want_p = os.path.join(self.base, "want.json")
        self.state_p = os.path.join(self.base, "state.json")
        self.state = read_json(self.state_p, {}) or {}
        self.state.setdefault("uploaded", {})
        self.state.setdefault("skipped", [])
        self.child = None
        self.stop_ev = threading.Event()
        self.lost = None
        self.view = {}                       # last heartbeat answer
        self.rec = {}                        # last runner status
        self.waits = {}                      # key -> why the runner can't cut it yet
        self.upload = None                   # {key, pct}
        self.lock = threading.Lock()
        self._got = 0
        self._sha = {}

    # ------------------------------------------------------------------------------------------------ status
    def status(self):
        v, r = self.view, self.rec
        have, skipped = set(v.get("have") or []), set(v.get("skipped") or [])
        rows = []
        for m in (v.get("matches") or [])[-12:]:
            k = m.get("key")
            up = self.upload if self.upload and self.upload.get("key") == k else None
            if k in have:
                s = "uploaded"
            elif up:
                s = f"uploading {up.get('pct', 0)}%"
            elif k in skipped:
                s = "the server has it"
            elif k in (v.get("want") or []):
                s = "cutting" if k not in self.waits else f"waiting: {self.waits[k]}"
            elif m.get("actual_time"):
                s = "match not over" if time.time() < m["actual_time"] + 200 else "waiting for the server's own clip"
            else:
                s = "not played yet"
            rows.append({"key": k, "match": (k or "").split("_", 1)[-1], "state": s})
        return {"aid": self.aid, "ev": self.ev, "source": self.a.get("source"), "label": self.a.get("label"),
                "url": self.a.get("url"), "role": self.a.get("role"), "rec": dict(r),
                "uploaded": len(self.state["uploaded"]), "skipped": len(skipped), "upload": self.upload,
                "matches": rows, "grace_s": v.get("grace_s")}

    def line(self):
        r = self.rec or {}
        what = self.a.get("label") or self.a.get("source")
        st = r.get("state") or "starting"
        if self.upload:
            return f"clip only: uploading {self.upload['key'].split('_', 1)[-1]} at {self.upload.get('pct', 0)}%"
        if st == "recording":
            mb = (r.get("bytes") or 0) / 2 ** 20
            return (f"clip only: recording {what}" + (f", {mb / 1024:.1f} GB kept" if mb >= 1024 else f", {mb:.0f} MB kept")
                    + f", {len(self.state['uploaded'])} uploaded")
        if st == "waiting":
            return f"clip only: waiting for {what}" + (f" ({r['msg']})" if r.get("msg") else "")
        if st == "ended":
            return f"clip only: {what} is over" + (f" ({r['msg']})" if r.get("msg") else "")
        return f"clip only: starting the recorder for {what}"

    # ------------------------------------------------------------------------------------------------ run
    def run(self):
        free = shutil.disk_usage(self.base).free
        if free < MIN_FREE:
            raise RuntimeError(f"Clip only needs about {MIN_FREE >> 30} GB free for the recording; "
                               f"{free / 2 ** 30:.1f} GB free")
        self._beat_once()                    # matches + want.json before the runner starts
        beat = threading.Thread(target=self._beats, name="stream-heartbeat", daemon=True)
        beat.start()
        fails = 0
        try:
            while True:
                if self.stop_ev.is_set() or self.proc.stop_ev.is_set():
                    break
                if not self.proc.enabled or self.proc.mode() != "clip":
                    break
                if self.lost:
                    raise StreamLost(self.lost)
                if self.child is None or self.child.poll() is not None:
                    if self.child is not None:
                        try:
                            self.child.stdin.close()
                        except (OSError, AttributeError):
                            pass
                        fails += 1
                        log.warning("%s: the clip runner exited (code %s); restarting in %d s", self.aid,
                                    self.child.returncode, min(120, 10 * fails))
                        self.child = None
                        if self.proc.stop_ev.wait(min(120, 10 * fails)):
                            break
                        continue
                    self._start_runner(free)
                self._uploads()
                self.proc.wake.wait(5)
                self.proc.wake.clear()
        finally:
            self.stop_ev.set()
            self._kill()

    def _start_runner(self, free):
        job = {"code": self.pipe["dir"], "ev": self.ev, "url": self.a["url"], "source": self.a.get("source"),
               "root": self.rec_root, "want": self.want_p, "tools": os.path.join(self.proc.root, "tools", "bin"),
               "env": {"FORCE_CPU": "1"}, "budget_gb": budget_gb(free), "keep_h": 2,
               "backfill_min": self.a.get("backfill_min") or 0, "measure": True}
        jp = os.path.join(self.base, "job.json")
        write_json(jp, job, mode=0o600)
        from .processor import child_env
        lp = os.path.join(self.base, "runner.log")
        if os.path.exists(lp) and os.path.getsize(lp) > LOG_MAX:
            os.replace(lp, lp + ".1")
        self.logf = open(lp, "a", encoding="utf-8", errors="replace")
        self.child = subprocess.Popen([self.py, "-u", os.path.join(self.pipe["dir"], "runner.py"), "--clip", jp],
                                      cwd=self.base, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      stdin=subprocess.PIPE, env=child_env(), text=True, encoding="utf-8",
                                      errors="replace", bufsize=1,
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._got = 0
        threading.Thread(target=self._read, args=(self.child, self.logf), name="clip-runner-out", daemon=True).start()
        log.info("%s: recording %s (%s) for %s", self.aid, self.a.get("label") or self.a.get("source"), self.a["url"],
                 self.ev)

    def _read(self, child, logf):
        try:
            for line in child.stdout:
                if not line.startswith(PROTO):
                    logf.write(line)
                    logf.flush()
                    continue
                try:
                    msg = json.loads(line[len(PROTO):])
                except ValueError:
                    continue
                if isinstance(msg.get("rec"), dict):
                    r = msg["rec"]
                    got = int(r.get("got_bytes") or 0)
                    if got > self._got:
                        self.proc._count("down", got - self._got)
                        self._got = got
                    self.rec = r
                elif isinstance(msg.get("cut"), dict):
                    k = msg["cut"].get("key")
                    self.waits.pop(k, None)
                    log.info("%s: cut %s from the recording", self.aid, k)
                    self.proc.wake.set()
                elif isinstance(msg.get("wait"), dict):
                    w = msg["wait"]
                    if w.get("key"):
                        self.waits[w["key"]] = str(w.get("why") or "")[:160]
        except (OSError, ValueError):
            pass
        finally:
            try:
                logf.close()
            except OSError:
                pass

    def _kill(self):
        c = self.child
        if c is not None and c.poll() is None:
            try:
                c.terminate()
                c.wait(10)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    c.kill()
                except OSError:
                    pass
        if c is not None and c.stdin is not None:
            try:
                c.stdin.close()
            except OSError:
                pass
        self.child = None

    # ------------------------------------------------------------------------------------------------ heartbeat
    def _beat_once(self):
        r = self.proc.server.call("POST", f"{API}/{self.ev}/{self.aid}/heartbeat",
                                  {"processor": self.proc.pid, "state": (self.rec or {}).get("state") or "starting",
                                   "rec": {k: v for k, v in (self.rec or {}).items()
                                           if k in ("chunks", "bytes", "behind_s", "last_wall", "video")}},
                                  timeout=30)
        d = r.json() or {}
        if r.status == 409 or (r.ok and d.get("stop")):
            self.lost = d.get("why") or d.get("detail") or "the stream assignment was lost"
            self.proc.wake.set()
            return
        if not r.ok:
            raise HTTPError(r.status, d)
        self.view = d
        for k in range(3):                  # Windows: replacing a file the runner has open fails; try again
            try:
                write_json(self.want_p, {"matches": d.get("matches") or [], "want": d.get("want") or [],
                                         "at": time.time()})
                break
            except OSError:
                time.sleep(0.5)
        skipped = set(d.get("skipped") or []) | set(d.get("have") or [])
        for k in skipped:                   # the server has a clip of it: the local copy isn't needed
            self._drop_local(k)

    def _beats(self):
        every = max(5.0, float(self.a.get("heartbeat_s") or 60))
        while not self.stop_ev.wait(every):
            try:
                self._beat_once()
            except NetError:
                pass                         # the assignment is good for claim_s (30 min); next beat
            except Exception as e:
                log.warning("%s: heartbeat: %s", self.aid, e)

    # ------------------------------------------------------------------------------------------------ uploads
    def _records(self):
        out = []
        ev_dir = os.path.join(self.rec_root, self.ev)
        try:
            dirs = sorted(os.listdir(ev_dir))
        except OSError:
            return out
        for d in dirs:
            md = os.path.join(ev_dir, d, "matches")
            try:
                names = sorted(os.listdir(md))
            except OSError:
                continue
            for n in names:
                if n.endswith(".json") and MID_RE.match(n[:-5]):
                    out.append(os.path.join(md, n))
        return out

    def _key(self, mid):
        return f"{self.ev}_{mid[4:]}"

    def _drop_local(self, key):
        mid = "tba-" + key.split("_", 1)[1]
        for p in self._records():
            if os.path.basename(p)[:-5] == mid:
                rec = read_json(p, {}) or {}
                c = self._clip_path(rec)
                if c and os.path.exists(c):
                    os.remove(c)
                    log.info("%s: %s not needed by the server any more; local clip deleted", self.aid, key)

    def _clip_path(self, rec):
        c = rec.get("clip")
        if not isinstance(c, str):
            return None
        rp, root = os.path.realpath(c), os.path.realpath(self.rec_root)
        return rp if rp.startswith(root + os.sep) else None

    def _uploads(self):
        want = set(self.view.get("want") or [])
        for p in self._records():
            if self.stop_ev.is_set() or not self.proc.enabled or self.proc.mode() != "clip" or self.lost:
                return
            mid = os.path.basename(p)[:-5]
            key = self._key(mid)
            if mid in self.state["uploaded"] or key not in want:
                continue
            rec = read_json(p, {}) or {}
            clip = self._clip_path(rec)
            if not clip or not os.path.isfile(clip):
                continue
            self._upload(key, mid, rec, clip)

    def _sha256(self, p):
        st = os.stat(p)
        k = (p, st.st_size, st.st_mtime_ns)
        if k not in self._sha:
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for b in iter(lambda: f.read(1 << 20), b""):
                    h.update(b)
            self._sha = {k: h.hexdigest()}
        return st.st_size, self._sha[k]

    def _upload(self, key, mid, rec, clip):
        size, sha = self._sha256(clip)
        base = f"{API}/{self.ev}/{self.aid}/clips/{quote(mid)}"
        P = self.proc
        self.upload = {"key": key, "pct": 0, "mb": round(size / 2 ** 20)}
        t0 = time.time()
        try:
            d = P._json("POST", base, {"processor": P.pid, "size": size, "sha256": sha})
            off = int(d.get("offset") or 0)
            chunk = max(256 << 10, min(int(d.get("chunk_bytes") or 8 << 20), int(d.get("chunk_max") or 16 << 20)))
            tries = 0
            if not d.get("done"):
                with open(clip, "rb") as f:
                    while off < size:
                        if self.stop_ev.is_set() or not P.enabled or P.mode() != "clip":
                            return
                        f.seek(off)
                        b = f.read(min(chunk, size - off))
                        ts = time.time()
                        try:
                            r = P.server.call("PUT", f"{base}/data?processor={quote(P.pid)}&offset={off}", b,
                                              ctype="application/octet-stream", timeout=300)
                        except NetError as e:
                            tries += 1
                            if tries > 8:
                                raise
                            log.warning("%s upload: %s; resuming in %d s", key, e, min(60, 5 * tries))
                            if P.stop_ev.wait(min(60, 5 * tries)):
                                return
                            d = P._json("POST", base, {"processor": P.pid, "size": size, "sha256": sha})
                            off = int(d.get("offset") or 0)
                            continue
                        if r.status == 409:              # out of step (a retry landed): ask where to go on
                            d = P._json("POST", base, {"processor": P.pid, "size": size, "sha256": sha})
                            off = int(d.get("offset") or 0)
                            continue
                        if not r.ok:
                            raise HTTPError(r.status, r.json() if r.json() is not None else r.body)
                        P._count("up", len(b))
                        P.net.add("up", len(b), time.time() - ts, "clip")
                        off = int((r.json() or {}).get("offset") or off + len(b))
                        tries = 0
                        self.upload = dict(self.upload, pct=int(100 * off / size))
            fields = {k: rec[k] for k in REC_FIELDS if k in rec}
            d = P._retry(lambda: P._json("POST", base + "/finish", {"processor": P.pid, "record": fields}))
            self.state["uploaded"][mid] = {"sha256": sha, "at": time.time(), "seconds": round(time.time() - t0, 1)}
            write_json(self.state_p, self.state)
            try:
                os.remove(clip)
            except OSError:
                pass
            log.info("%s: uploaded %s (%d MB in %.0f s)%s", self.aid, key, size >> 20, time.time() - t0,
                     " (it was there already)" if d.get("already") else "")
            have = list(self.view.get("have") or [])
            self.view = dict(self.view, have=have + [key], want=[k for k in self.view.get("want") or [] if k != key])
        except HTTPError as e:
            if e.status == 409 and ("not assigned" in str(e) or "released" in str(e)):
                self.lost = str(e)
                return
            if e.status in (409, 422):
                log.warning("%s: upload of %s refused (%s)", self.aid, key, e)
                self.state["skipped"].append(key)
                self.state["skipped"] = self.state["skipped"][-200:]
                self.view = dict(self.view, want=[k for k in self.view.get("want") or [] if k != key])
                write_json(self.state_p, self.state)
                return
            raise
        finally:
            self.upload = None

    def release(self, reason):
        try:
            self.proc._json("POST", f"{API}/{self.ev}/{self.aid}/release",
                            {"processor": self.proc.pid, "reason": reason}, timeout=20)
        except (NetError, HTTPError) as e:
            log.warning("%s: release failed (%s)", self.aid, e)
