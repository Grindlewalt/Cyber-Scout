"""Processor mode, the pipeline side: track ONE claimed match with the server's own code (live/worker.py
process_match).  Not imported by the client: the server ships this file as runner.py inside its pipeline code
tarball (server/routers/processing.py), so it always matches the live/ code next to it, and the client runs it with
the processing Python (numpy, cv2, av, onnxruntime, pupil_apriltags):

    <python> -u <pipeline dir>/runner.py <job.json>

job.json (written by csclient/processor.py):
    {"code": <pipeline dir>, "work": <empty work dir>, "model": <robot.onnx>, "worker_id": <claim owner>,
     "env": {extra environment}, "ev", "match": <TBA match>, "event": <event context> | null,
     "live": <livestream input with local "clip" and "view_ref" paths>, "camera": {"name", "data"},
     "pose_guess": {...} | null}

stdout: log lines, and protocol lines "@@CS <json>": {"progress": 0..1, "stage": "processing"} ... then
{"done": true, "ok": bool, "files": {kind: path}, "error", "note", "seconds"}.  Exit code 0 = ok.

Two more modes for the client:

    <python> -u runner.py --bench <job.json>   benchmark: decode a sample clip + run the detector -> {"done", "ok",
                                               "bench": {provider, decode_fps, det_fps, ...}}
    <python> -u runner.py --clip <job.json>    Clip only: record a livestream with live/livestream.py's Recorder
                                               (one dir per video id under <root>/<ev>/), cut the matches the client
                                               lists in <want.json> with live/liveingest.py tba_cut (TBA start
                                               time), report {"rec": {...}}, {"cut": {key, record}}, {"wait": {key,
                                               why}} until it is stopped"""
import json, os, re, sys, time

PROTO = "@@CS "
KINDS = ("fuel", "shots", "analytics", "matches")
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}\.json$")
SRC_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def emit(**kw):
    sys.stdout.write(PROTO + json.dumps(kw) + "\n")
    sys.stdout.flush()


def die_with_parent():
    """The client keeps our stdin open; EOF = the client is gone (crashed, killed, closed): stop too, instead of
    tracking / recording on without anyone reading the results (all OSes: the pipe closes when the process ends)."""
    import threading

    def watch():
        try:
            while sys.stdin.read(4096):
                pass
        except (OSError, ValueError):
            pass
        os._exit(3)
    if os.environ.get("CS_RUNNER_PARENT_PIPE") == "1":
        threading.Thread(target=watch, name="parent-watch", daemon=True).start()


def clip_seconds(path):
    try:
        import av
        with av.open(path) as c:
            if c.duration:
                return c.duration / 1e6
    except Exception:
        pass
    return 190.0                                    # a livestream match clip: ~15 s lead + 163 s match + tail


def main(argv):
    t0 = time.time()
    with open(argv[1]) as f:
        job = json.load(f)
    work, ev, m = job["work"], job["ev"], job["match"]
    key = m["key"]
    data = os.path.join(work, "data")
    evd = os.path.join(data, "events", ev)
    os.makedirs(evd, exist_ok=True)
    live = dict(job["live"])
    cam = job["camera"]
    if not NAME_RE.match(cam.get("name") or ""):
        raise SystemExit("bad camera file name")
    live["camera"] = os.path.join(evd, cam["name"])
    with open(live["camera"], "w") as f:
        json.dump(cam["data"], f)
    src = live.get("source") or ""
    if job.get("pose_guess") and SRC_RE.match(src):
        with open(os.path.join(evd, f"pose_latest_{src}.json"), "w") as f:
            json.dump(job["pose_guess"], f)
    os.environ.update(DATA=data, MEDIA=os.path.join(work, "media"), KEEP_VIDEO="0", HUBCAM="0",
                      MODEL=job["model"], WORKER_ID=job.get("worker_id") or "processor",
                      VIDEO_CACHE=os.path.join(work, "cache"), MEDIA_PROXIES="0")
    os.environ.update({k: str(v) for k, v in (job.get("env") or {}).items()})
    _coreml_cache(job, data)
    code = job["code"]
    sys.path[:0] = [os.path.join(code, "live"), os.path.join(code, "src")]
    import worker as W

    ctx = job.get("event")
    W.event_ctx = lambda ev_, m_: ctx               # the server sent it (the worker would ask TBA with its key)
    dur = max(30.0, clip_seconds(live["clip"]))
    last = [0.0]

    def log(*a):
        line = " ".join(str(x) for x in a)
        print(time.strftime("%H:%M:%S"), line, flush=True)
        p = None
        mm = re.search(r" frames t=(\d+)s ", line)
        if mm:
            p = 0.8 * min(1.0, int(mm.group(1)) / dur)
        elif "detection:" in line:
            p = 0.8
        elif "camera coverage" in line:
            p = 0.9
        if p is not None and p > last[0]:
            last[0] = p
            emit(progress=round(p, 3), stage="processing")

    W.log = log
    emit(progress=0.0, stage="processing")
    st = {}
    try:
        ok = W.process_match(ev, m, {"video_mode": "livestream"}, st, {}, live=live)
    except Exception as e:                          # process_match catches its own; this is a setup problem
        import traceback
        traceback.print_exc(file=sys.stdout)
        ok, st = False, {key: {"error": f"{e.__class__.__name__}: {e}"[-300:]}}
    files = {k: os.path.join(evd, k, f"{key}.json") for k in KINDS
             if os.path.exists(os.path.join(evd, k, f"{key}.json"))}
    s = st.get(key) or {}
    ok = bool(ok) and "matches" in files
    emit(done=True, ok=ok, files=files, error=s.get("error"), note=s.get("note"), state=s.get("state"),
         seconds=round(time.time() - t0, 1))
    return 0 if ok else 1


def _paths(job):
    code = job["code"]
    sys.path[:0] = [os.path.join(code, "live"), os.path.join(code, "src")]
    os.environ.update({k: str(v) for k, v in (job.get("env") or {}).items()})


# ---------------------------------------------------------------- benchmark
def _coreml_cache(job, data):
    """Keep Core ML's compiled model between runs (live/detector.py caches it under $DATA/cache/coreml)."""
    keep = job.get("coreml_cache")
    if not keep or os.environ.get("ORT_COREML") != "1":
        return
    os.makedirs(keep, exist_ok=True)
    link = os.path.join(data, "cache", "coreml")
    if not os.path.exists(link):
        os.makedirs(os.path.dirname(link), exist_ok=True)
        try:
            os.symlink(keep, link, target_is_directory=True)
        except OSError:
            pass


def bench_main(path):
    """Decode up to `decode_frames` frames of the sample (full rate, bgr24 like process.detect_video) and run the
    detector on `det_frames` of them (synthetic frames when there is no sample)."""
    t0 = time.time()
    with open(path) as f:
        job = json.load(f)
    _paths(job)
    os.environ["DATA"] = job["work"]
    _coreml_cache(job, job["work"])
    import numpy as np
    import detector as D
    emit(progress=0.05, stage="loading the detector")
    t = time.time()
    det = D.Detector(job["model"])
    load_s = time.time() - t
    frames, dec, N = [], None, int(job.get("det_frames") or 40)
    if job.get("sample"):
        import av
        n, t, shape, err = 0, time.time(), None, None
        try:
            with av.open(job["sample"]) as c:
                s = c.streams.video[0]
                s.thread_type = "AUTO"
                for fr in c.decode(s):
                    im = fr.to_ndarray(format="bgr24")
                    n += 1
                    shape = im.shape
                    if len(frames) < N and n % 5 == 0:
                        frames.append(im)
                    if n % 100 == 0:
                        emit(progress=0.05 + 0.45 * min(1.0, n / job.get("decode_frames", 900)), stage="decoding")
                    if n >= int(job.get("decode_frames") or 900):
                        break
        except Exception as e:                  # the sample is the start of a clip: it ends mid-file
            err = f"{e.__class__.__name__}"
        el = time.time() - t
        if n >= 30:
            dec = {"frames": n, "fps": round(n / el, 1), "w": shape[1], "h": shape[0], "stopped": err}
    if len(frames) < 4:
        rng = np.random.default_rng(1)
        frames = [rng.integers(0, 255, (1080, 1920, 3), dtype=np.uint8) for _ in range(4)]
    emit(progress=0.55, stage="warming up the detector")
    t = time.time()
    for im in frames[:3]:
        det(im)
    warm = time.time() - t
    t = time.time()
    for i in range(N):
        det(frames[i % len(frames)])
        if i % 10 == 0:
            emit(progress=0.6 + 0.4 * i / N, stage="detector")
    el = time.time() - t
    out = {"provider": det.device, "det_fps": round(N / el, 2), "det_frames": N, "warmup_s": round(warm, 2),
           "load_s": round(load_s, 2), "decode": dec, "input": [det.h, det.w],
           "frames": "sample" if dec else "synthetic"}
    emit(done=True, ok=True, bench=out, seconds=round(time.time() - t0, 1))
    return 0


# ---------------------------------------------------------------- Clip only
def _ffmpeg(tools):
    """An `ffmpeg` on PATH for livestream.py (ChunkStore.cut, the DASH remux): imageio-ffmpeg's pinned binary."""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        import shutil
        return shutil.which("ffmpeg")
    os.makedirs(tools, exist_ok=True)
    shim = os.path.join(tools, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if not os.path.exists(shim):
        try:
            os.symlink(exe, shim)
        except (OSError, NotImplementedError):
            import shutil
            shutil.copy2(exe, shim)
    return shim


def _last_row(p, pos, n, nb, since=0.0):
    """(new pos, rows so far, bytes downloaded since `since`, last parsed row) of a growing chunks.jsonl."""
    last = None
    try:
        with open(p) as f:
            f.seek(pos)
            while True:
                ln = f.readline()
                if not ln or not ln.endswith("\n"):
                    break
                pos = f.tell()
                n += 1
                try:
                    last = json.loads(ln)
                    if float(last.get("got") or 0) >= since:
                        nb += int(last.get("size") or 0)
                except (ValueError, TypeError, AttributeError):
                    pass
    except OSError:
        pass
    return pos, n, nb, last


def _dir_bytes(d):
    tot = 0
    for sub in ("seg", "clips"):
        try:
            for e in os.scandir(os.path.join(d, sub)):
                if e.is_file():
                    tot += e.stat().st_size
        except OSError:
            pass
    return tot


def clip_main(path):
    import threading
    with open(path) as f:
        job = json.load(f)
    _paths(job)
    bindir = os.path.dirname(sys.executable)
    ff = _ffmpeg(job["tools"])
    os.environ["PATH"] = os.pathsep.join(x for x in (job["tools"], bindir, os.environ.get("PATH", "")) if x)
    yt = os.path.join(bindir, "yt-dlp.exe" if os.name == "nt" else "yt-dlp")
    if os.path.exists(yt):
        os.environ["YTDLP"] = yt
    try:
        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())   # python-build-standalone may lack a CA path
    except ImportError:
        pass
    import livestream as LS
    import liveingest as LI
    ev, url, root = job["ev"], job["url"], job["root"]
    evroot = os.path.join(root, ev)
    os.makedirs(evroot, exist_ok=True)
    print(f"clip only: {ev} {url} -> {evroot} (ffmpeg {ff}, yt-dlp {os.environ.get('YTDLP', 'yt-dlp')})", flush=True)
    st = {"state": "starting", "msg": None}

    def rlog(*a):
        line = " ".join(str(x) for x in a)
        print(time.strftime("%H:%M:%S"), line, flush=True)
        if "recorder: error" in line:
            st.update(state="waiting", msg=line.split("recorder: error", 1)[1].strip()[:200])
        elif re.search(r"recorder: \S+ (is_live|post_live|is_upcoming)", line):
            st.update(state="recording" if "is_upcoming" not in line else "waiting",
                      msg=None if "is_upcoming" not in line else "the stream has not started yet")
        elif "stream status" in line or "ENDLIST" in line or "nothing was recorded" in line:
            st.update(state="ended", msg=line.split("recorder: ", 1)[-1][:200])
        elif "max hours" in line:
            st.update(state="ended", msg="recorded the maximum hours; restarting")

    dirs = sorted(d for d in os.listdir(evroot) if re.match(r"^r\d{1,4}$", d))
    k = max([int(d[1:]) for d in dirs] or [0])
    rec = th = None
    idx = {"pos": 0, "n": 0, "nb": 0, "last": None, "dir": None}
    ocr_cache = []

    def ocr():                          # the overlay OCR measures the auto start on each clip (CPU)
        if not ocr_cache:
            ocr_cache.append(LS.OverlayOCR())
        return ocr_cache[0]

    def start(new):
        nonlocal rec, th, k
        if new or k == 0:
            k += 1
        out = os.path.join(evroot, f"r{k}")
        os.makedirs(out, exist_ok=True)
        mp = os.path.join(out, "meta.json")
        meta = {}
        if os.path.exists(mp):
            with open(mp) as f:
                meta = json.load(f)
        meta.update(event=ev, source=job.get("source"), url=url, mode="client", started=meta.get("started") or time.time())
        with open(mp, "w") as f:
            json.dump(meta, f)
        rec = LS.Recorder(url, out, rlog, max_hours=float(job.get("max_hours") or 16),
                          budget_gb=float(job.get("budget_gb") or 8), keep_h=float(job.get("keep_h") or 2),
                          keep_match_h=float(job.get("keep_h") or 2), backfill_min=float(job.get("backfill_min") or 0),
                          exit_on_new_video=True)
        th = threading.Thread(target=rec.run, name="recorder", daemon=True)
        th.start()
        if idx["dir"] != out:
            idx.update(pos=0, n=0, last=None, dir=out)
        st.update(state="starting", msg=None)

    t_run = time.time()
    start(False)
    done, tried, said = set(), {}, {}
    t_status = t_want = 0.0
    restart_at = None
    while True:
        now = time.time()
        if not th.is_alive():
            if rec.new_video:
                print(f"new video {rec.new_video}: a new recording folder", flush=True)
                start(True)
            elif restart_at is None:
                restart_at = now + 60
                if st["state"] not in ("ended",):
                    st.update(state="waiting", msg=st.get("msg") or "the recorder stopped; retrying in a minute")
            elif now >= restart_at:
                restart_at = None
                start(False)
        d = idx["dir"]
        idx["pos"], idx["n"], idx["nb"], last = _last_row(os.path.join(d, "chunks.jsonl"), idx["pos"], idx["n"],
                                                          idx["nb"], t_run)
        if last:
            idx["last"] = last
        lr = idx["last"]
        if lr and lr.get("pts") is not None:
            # no overlay analyser here: everything recorded counts as looked at (liveingest.tba_cut_plan waits for
            # the analyser; Recorder.retention trims segments older than keep_h once they are "analysed")
            an = {"last_sq": lr.get("sq"), "cursor_wall": lr.get("got"), "t_last": lr["pts"] + (lr.get("dur") or 5.0),
                  "at": now, "client": True}
            tmp = os.path.join(d, "analyzer.json.tmp")
            with open(tmp, "w") as f:
                json.dump(an, f)
            os.replace(tmp, os.path.join(d, "analyzer.json"))
        if now - t_status >= 10:
            t_status = now
            behind = round(now - (lr["wall"] + (lr.get("dur") or 5.0)), 1) if lr and lr.get("wall") else None
            emit(rec={"state": st["state"], "msg": st["msg"], "chunks": idx["n"], "bytes": _dir_bytes(d),
                      "got_bytes": idx["nb"],
                      "last_wall": lr.get("wall") if lr else None, "behind_s": behind,
                      "video": getattr(rec, "video_id", None), "dir": os.path.basename(d)})
        if now - t_want >= 10:
            t_want = now
            try:
                with open(job["want"]) as f:
                    w = json.load(f)
            except (OSError, ValueError):
                w = {}
            ms = {m["key"]: m for m in w.get("matches") or [] if isinstance(m, dict) and m.get("key")}
            for key in w.get("want") or []:
                m = ms.get(key)
                if m is None or key in done or now - tried.get(key, 0) < 60:
                    continue
                tried[key] = now
                p, why = LI.tba_cut(ev, m, root=root, now=time.time(), log=rlog,
                                    ocr=ocr if job.get("measure", True) else None)
                if p:
                    done.add(key)
                    emit(cut={"key": key, "record": p})
                elif why == "already cut by TBA time":
                    done.add(key)
                elif said.get(key) != why:
                    said[key] = why
                    emit(wait={"key": key, "why": why})
        time.sleep(2)


if __name__ == "__main__":
    die_with_parent()
    if len(sys.argv) > 2 and sys.argv[1] == "--bench":
        sys.exit(bench_main(sys.argv[2]))
    if len(sys.argv) > 2 and sys.argv[1] == "--clip":
        sys.exit(clip_main(sys.argv[2]))
    sys.exit(main(sys.argv))
