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
{"done": true, "ok": bool, "files": {kind: path}, "error", "note", "seconds"}.  Exit code 0 = ok."""
import json, os, re, sys, time

PROTO = "@@CS "
KINDS = ("fuel", "shots", "analytics", "matches")
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}\.json$")
SRC_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def emit(**kw):
    sys.stdout.write(PROTO + json.dumps(kw) + "\n")
    sys.stdout.flush()


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


if __name__ == "__main__":
    sys.exit(main(sys.argv))
