"""Distributed match processing: any computer running the Cyber-Scout Client can "Help process matches" (client
processor mode, client/csclient/processor.py).  Mounted under /app/v1.  Admin only: a processor's uploads become
the event's tracking data.

One match at a time per processor, the same claim files and rules as the server's own worker and tools/helper.py
(live/claims.py: events/<ev>/claims/<key>.json {"by", "at"}, O_EXCL, stale after CLAIM_S), in the same order
(time order, events/<ev>/priority.json first), so nobody tracks a match twice:

  POST /processing/claim                      the next trackable livestream clip -> a job (match, live input, URLs)
  GET  /processing/{ev}/{key}/clip            the clip (Range-resumable)       GET .../view.png  the view reference
  POST /processing/{ev}/{key}/heartbeat       keeps the claim fresh (every HEARTBEAT_S) + progress for the status
  PUT  /processing/{ev}/{key}/results/{kind}  fuel | shots | analytics | matches JSON (gzip ok), staged
  POST /processing/{ev}/{key}/complete        staged files into events/<ev>/<kind>/<key>.json (matches last: the
                                              worker then treats the match as done and rebuilds the site), the clip
                                              into MEDIA/<ev>/<key>.mp4, claim released
  POST /processing/{ev}/{key}/release         give the match back (failed / stopping)
  GET  /processing/pipeline                   code + model versions + per-runtime requirements; .../code.tar.gz,
                                              .../model.onnx (Range)
  GET  /processing/status                     live claims of all followed events (who, progress)
  GET  /processing/speedtest?bytes=N          N random bytes (connection check: the client's slow-link notice)
  POST /processing/speedtest                  upload check (body discarded)
  GET  /processing/sample                     the start of a recent livestream clip for the client's benchmark
                                              (.../sample.mp4, Range)

"Clip only" (a processor too slow to track, or without a GPU, records a livestream and cuts every match by TBA time;
the server registers each uploaded clip as a livestream record, so the worker or a GPU processor tracks it):
  POST /processing/streams/claim                         assign a stream (a source the server doesn't record first,
                                                         else a backup of the one it does); one processor per stream
  POST /processing/streams/{ev}/{aid}/heartbeat          keeps it; -> TBA matches (start times) + which clips to send
  POST /processing/streams/{ev}/{aid}/release            stop recording it
  POST /processing/streams/{ev}/{aid}/clips/{mid}        start / resume a chunked clip upload -> offset
  PUT  /processing/streams/{ev}/{aid}/clips/{mid}/data   one chunk at ?offset= (<= CHUNK_MAX)
  POST /processing/streams/{ev}/{aid}/clips/{mid}/finish size + sha256 checked, then MEDIA/live/<ev>/<aid>/
                                                         clips/<mid>.mp4 + matches/<mid>.json + meta.json

Files (DATA = cfg.DATA):
  events/<ev>/claims/<key>.json      claims (live/claims.py)
  processing/<ev>/<key>/job.json     the server's record of a handed-out job (server paths; never sent as-is)
  processing/<ev>/<key>/<kind>.json  staged uploads (same filesystem as events/: moved in with os.replace)
  processing/<ev>/failures.json      processor failures per match: after MAX_FAILS it is left to the worker
  processing/streams/<ev>/<aid>.json a stream assignment {aid, ev, source, url, by, at, ...} (stale after CLAIM_S)
  cache/processing/code-<v>.tar.gz   the pipeline code (live/ + src/ + the client's runner), by content hash
  MEDIA/live/<ev>/<aid>/.uploads/    clip uploads in progress (<mid>.part + <mid>.json), same disk as the clips

Only livestream clips are handed out (the worker's livestream input, liveingest.LiveView.for_processing); per-match
uploads stay with the worker.  Needs live/claims.py, live/liveingest.py and live/layout.py importable (the macOS
native server has the whole repo; the Docker api image copies only live/sources.py + live/layout.py -> 503)."""
import hashlib, json, os, re, shutil, sys, tarfile, threading, time, zlib
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .. import audit, db as DB, tba
from ..config import cfg
from ..deps import EVENT_RE, MATCH_RE, User, require_role
from ..security import atomic_write_json

router = APIRouter(tags=["processing"])

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(_HERE, "..", ".."))
for _p in (os.path.join(REPO, "live"), "/srv/live", "/app/live"):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.append(_p)
try:
    import claims as CL            # live/claims.py (stdlib)
    import liveingest as LI        # live/liveingest.py (stdlib at import; LiveView uses live/layout.py)
except Exception as _e:            # pragma: no cover - only when the image lacks live/
    CL = LI = None
    _IMPORT_ERR = repr(_e)
try:
    import sources as SRC          # live/sources.py: the event's video mode
except Exception:                  # pragma: no cover
    SRC = None

PROC_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$")
STAGE_RE = re.compile(r"^[a-z][a-z _-]{0,31}$")
KINDS = ("fuel", "shots", "analytics", "matches")
MAX_BODY = 20 * 1024 * 1024        # one upload on the wire (nginx: 25m on /app/v1/)
MAX_JSON = 64 * 1024 * 1024        # ... and decompressed
HEARTBEAT_S = 60                   # processors refresh their claim this often (CLAIM_S is 1800)
MAX_FAILS = 2                      # processor failures of one match before processors leave it to the worker
TBA_FRESH_S = 900                  # the worker's TBA cache is used when it is this fresh (else TBA, else the cache)
# the processing Python's packages (keep in sync with the root Dockerfile's pip line; CPU onnxruntime, which has
# CoreML on macOS).  Sent to the client for its one-time "set up processing" (uv venv).
REQUIREMENTS = ["onnxruntime==1.31.0", "opencv-python-headless==5.0.0.93", "av==19.0.1", "numpy==2.5.4",
                "scipy==1.18.1", "networkx==3.7", "pupil-apriltags==1.0.4.post11"]
PYTHON = "3.12"
# "Set up processing" on the client (client/csclient/procsetup.py) installs one runtime, picked by its GPU check:
#   cuda   NVIDIA (Windows / Linux): the GPU build with the pip CUDA 13 + cuDNN wheels (driver >= 580, compute
#          capability >= 7.5; ort.preload_dlls() finds them), as in the root Dockerfile
#   coreml Apple silicon: the plain build (Core ML execution provider, macOS 14+)
#   clip   "Clip only" without a GPU (records + cuts, no tracking): Intel Macs have no onnxruntime / pupil-apriltags
#          wheels for these pins
# + CLIP_EXTRAS everywhere (Clip only: ffmpeg binary, deno for yt-dlp's YouTube support) and yt-dlp from
#   live/requirements-ytdlp.txt (hash-pinned, installed with --require-hashes like the worker image).
RUNTIMES = {
    "cuda": ["onnxruntime-gpu[cuda,cudnn]==1.31.0"] + [r for r in REQUIREMENTS if not r.startswith("onnxruntime")],
    "coreml": list(REQUIREMENTS),
    "clip": [r for r in REQUIREMENTS if r.split("==")[0] in ("numpy", "opencv-python-headless", "av")],
}
CLIP_EXTRAS = ["imageio-ffmpeg==0.6.0", "deno==2.5.6"]
YTDLP_REQ = "live/requirements-ytdlp.txt"
CODE_DIRS = ("live", "src")
SKIP_DIRS = {"__pycache__", "train", "tests", ".pytest_cache", ".ipynb_checkpoints"}
RUNNER = os.path.join("client", "csclient", "procrunner.py")   # -> runner.py in the code tarball
_lock = threading.Lock()


def _mods():
    if CL is None or LI is None:
        raise HTTPException(503, "match processing is not installed in this API image (needs live/claims.py, "
                                 "live/liveingest.py and live/layout.py)")
    return CL, LI


# ------------------------------------------------------------------ helpers
def _jload(p, default=None):
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _evd(ev): return os.path.join(cfg.DATA, "events", ev)
def _stage(ev, key): return os.path.join(cfg.DATA, "processing", ev, key)
def _live_root(): return os.environ.get("LIVE_DIR") or os.path.join(cfg.MEDIA, "live")


def _by(user: User, processor: str) -> str:
    """Claim owner id: app-u<user id>-<processor id> (never the worker's host name or a helper's helper-<host>)."""
    if not isinstance(processor, str) or not PROC_RE.match(processor):
        raise HTTPException(422, "bad processor id (1-48 characters: letters, digits, . _ -)")
    return f"app-u{user.id}-{processor}"


def _match(ev: str, key: str):
    if not EVENT_RE.match(ev):
        raise HTTPException(422, "bad event key (expected e.g. 2026wasam)")
    m = MATCH_RE.match(key)
    if not m or m.group(1) != ev:
        raise HTTPException(422, "bad match key (expected e.g. 2026wasam_qm12)")
    return ev, key


def _under(p, root):
    try:
        rp, rr = os.path.realpath(p), os.path.realpath(root)
    except (TypeError, ValueError):
        return False
    return rp.startswith(rr.rstrip(os.sep) + os.sep)


def _clip_ok(p):
    """A livestream clip path from a record: must be a file under the recordings (LIVE_DIR) or MEDIA."""
    return _under(p, _live_root()) or _under(p, cfg.MEDIA)


def _sha256(p, chunk=1 << 20):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                return h.hexdigest()
            h.update(b)


_sha_cache: dict = {}


def _file_sha(p):
    """sha256 of a file, cached by (path, size, mtime)."""
    st = os.stat(p)
    k = (p, st.st_size, st.st_mtime_ns)
    if k not in _sha_cache:
        if len(_sha_cache) > 256:
            _sha_cache.clear()
        _sha_cache[k] = _sha256(p)
    return st.st_size, _sha_cache[k]


def _tba_disk(ev, ep):
    """The worker's TBA cache entry (live/tba.py: DATA/tba/<ep with / -> __>.json) -> (data, fetched)."""
    d = _jload(os.path.join(cfg.DATA, "tba", ep.strip("/").replace("/", "__") + ".json"))
    if isinstance(d, dict) and "data" in d:
        return d["data"], float(d.get("fetched") or 0)
    return None, 0.0


def _tba_matches(ev, key):
    data, at = _tba_disk(ev, f"/event/{ev}/matches")
    if data is not None and time.time() - at < TBA_FRESH_S:
        return data
    if key:
        try:
            return tba.get(key, f"/event/{ev}/matches", ttl=60)
        except tba.TBAError:
            pass
    return data or []


def _event_ctx(ev, key, ms):
    """process(event=...) for open-set bumper OCR + integrity checks, like worker.event_ctx (team keys + compact
    lineups; integrity.lineups accepts {key, red, blue})."""
    teams, _ = _tba_disk(ev, f"/event/{ev}/teams/simple")
    teams = [t.get("key") for t in teams if isinstance(t, dict)] if isinstance(teams, list) else None
    if not teams and key:
        try:
            teams = tba.get(key, f"/event/{ev}/teams/keys", ttl=3600)
        except tba.TBAError:
            teams = None
    lineups = []
    for m in ms:
        a = m.get("alliances") or {}
        red, blue = (a.get("red") or {}).get("team_keys") or [], (a.get("blue") or {}).get("team_keys") or []
        if red or blue:
            lineups.append({"key": m.get("key"), "red": red, "blue": blue})
    return {"teams": [t for t in teams or [] if isinstance(t, str)], "matches": lineups}


def _video_mode(ev):
    try:
        return SRC.video_mode(SRC.load(_evd(ev))) if SRC else "auto"
    except Exception:
        return "auto"


def _failures(ev):
    d = _jload(os.path.join(cfg.DATA, "processing", ev, "failures.json"), {})
    return d if isinstance(d, dict) else {}


def _note_failure(ev, key, by, reason):
    with _lock:
        d = _failures(ev)
        d.setdefault(key, []).append({"by": by, "at": round(time.time(), 1), "reason": (reason or "")[:300]})
        d[key] = d[key][-10:]
        atomic_write_json(os.path.join(cfg.DATA, "processing", ev, "failures.json"), d)


def _clear_failures(ev, key):
    with _lock:
        d = _failures(ev)
        if key in d:
            d.pop(key)
            atomic_write_json(os.path.join(cfg.DATA, "processing", ev, "failures.json"), d)


def _audit(db, request, user, action, ev, key, after):
    with DB.tx(db):
        audit.record(db, request, user, action, f"{ev}/{key}", ev, None, after)


# ------------------------------------------------------------------ pipeline code + model
_code = {"sig": None}


def _code_files():
    """(archive path, abs path) of the pipeline: live/ + src/ (minus caches / training / tests) and the runner."""
    out = []
    for top in CODE_DIRS:
        base = os.path.join(REPO, top)
        for d, dirs, files in os.walk(base):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith("."))
            for f in sorted(files):
                p = os.path.join(d, f)
                if f.startswith(".") or f.endswith((".pyc", ".pyo")) or os.path.islink(p):
                    continue
                out.append((os.path.relpath(p, REPO).replace(os.sep, "/"), p))
    r = os.path.join(REPO, RUNNER)
    if os.path.exists(r):
        out.append(("runner.py", r))
    return out


def pipeline_code():
    """-> {version, path, sha256, size}: the code tarball, rebuilt when a file changes (cached by content hash)."""
    files = _code_files()
    if not any(a.startswith("live/") for a, _ in files) or not any(a == "runner.py" for a, _ in files):
        raise HTTPException(503, "the pipeline code (live/, src/, client/csclient/procrunner.py) is not in this "
                                 "server install")
    sig = tuple((a, os.stat(p).st_size, os.stat(p).st_mtime_ns) for a, p in files)
    with _lock:
        if _code.get("sig") == sig and os.path.exists(_code["path"]):
            return dict(_code)
        req = ("\n".join(REQUIREMENTS) + "\n").encode()
        rts = json.dumps({"runtimes": RUNTIMES, "clip_extras": CLIP_EXTRAS}, sort_keys=True).encode()
        h = hashlib.sha256()
        for a, p in files:
            h.update(a.encode() + b"\0" + _sha256(p).encode() + b"\0")
        h.update(b"requirements.txt\0" + hashlib.sha256(req).hexdigest().encode())
        h.update(b"runtimes\0" + hashlib.sha256(rts).hexdigest().encode())
        version = "p-" + h.hexdigest()[:12]
        out = os.path.join(cfg.DATA, "cache", "processing", f"code-{version}.tar.gz")
        if not os.path.exists(out):
            os.makedirs(os.path.dirname(out), exist_ok=True)
            tmp = f"{out}.tmp{os.getpid()}"
            import gzip, io
            with open(tmp, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz, \
                    tarfile.open(fileobj=gz, mode="w") as t:
                def add(name, data):
                    ti = tarfile.TarInfo(name)
                    ti.size, ti.mtime, ti.mode, ti.uid, ti.gid = len(data), 0, 0o644, 0, 0
                    t.addfile(ti, io.BytesIO(data))
                for a, p in files:
                    with open(p, "rb") as f:
                        add(a, f.read())
                add("requirements.txt", req)
                add("PIPELINE.json", json.dumps({"version": version, "python": PYTHON, "runtimes": RUNTIMES,
                                                 "clip_extras": CLIP_EXTRAS}).encode())
            os.replace(tmp, out)
            for old in os.listdir(os.path.dirname(out)):          # keep the last few versions
                op = os.path.join(os.path.dirname(out), old)
                if old.startswith("code-") and op != out and time.time() - os.path.getmtime(op) > 7 * 86400:
                    try:
                        os.remove(op)
                    except OSError:
                        pass
        size, sha = _file_sha(out)
        _code.update(sig=sig, version=version, path=out, sha256=sha, size=size)
        return dict(_code)


def _model_path():
    return os.environ.get("MODEL") or os.path.join(cfg.DATA, "models", "robot.onnx")


class FileRef(BaseModel):
    url: str = Field(description="path on this server (prefix the server address)")
    size: int
    sha256: str


class PipelineOut(BaseModel):
    version: str = Field(description="content hash of the pipeline code; claims carry it, a different one -> 409")
    code: FileRef = Field(description="gzip tar: live/, src/, runner.py, requirements.txt, PIPELINE.json")
    model: FileRef | None = Field(description="the robot detector (robot.onnx); null = none on the server yet")
    requirements: list[str] = Field(description="pip requirements of the processing Python")
    runtimes: dict[str, list[str]] = Field(default_factory=dict, description="requirements per runtime: cuda "
                                           "(NVIDIA), coreml (Apple silicon), clip (Clip only, no GPU)")
    clip_extras: list[str] = Field(default_factory=list, description="added to every runtime (Clip only tools)")
    ytdlp_requirements: str | None = Field(None, description="hash-pinned yt-dlp requirements file in the code "
                                                             "tarball (uv pip install --require-hashes --no-deps)")
    python: str
    claim_s: float = Field(description="a claim not refreshed for this long is stale and handed to someone else")
    heartbeat_s: int = Field(description="refresh the claim (POST .../heartbeat) this often")
    max_upload_bytes: int


@router.get("/processing/pipeline", response_model=PipelineOut,
            summary="Processing code + model versions for client processors (admin)")
def pipeline(user: User = Depends(require_role("admin"))):
    C, _ = _mods()
    code = pipeline_code()
    model = None
    mp = _model_path()
    if os.path.isfile(mp):
        size, sha = _file_sha(mp)
        model = FileRef(url="/app/v1/processing/pipeline/model.onnx", size=size, sha256=sha)
    return PipelineOut(version=code["version"], requirements=REQUIREMENTS, python=PYTHON, claim_s=C.CLAIM_S,
                       runtimes=RUNTIMES, clip_extras=CLIP_EXTRAS,
                       ytdlp_requirements=YTDLP_REQ if os.path.isfile(os.path.join(REPO, YTDLP_REQ)) else None,
                       code=FileRef(url=f"/app/v1/processing/pipeline/code.tar.gz?v={code['version']}",
                                    size=code["size"], sha256=code["sha256"]),
                       model=model, heartbeat_s=HEARTBEAT_S, max_upload_bytes=MAX_BODY)


@router.get("/processing/pipeline/code.tar.gz", response_class=FileResponse,
            responses={200: {"content": {"application/gzip": {}}}, 206: {"description": "partial (Range)"}},
            summary="The pipeline code tarball (admin; Range-resumable)")
def pipeline_code_file(v: str | None = Query(None, max_length=40), user: User = Depends(require_role("admin"))):
    _mods()
    code = pipeline_code()
    if v and v != code["version"]:
        raise HTTPException(409, f"the pipeline changed (now {code['version']}); GET /processing/pipeline again")
    return FileResponse(code["path"], media_type="application/gzip", filename=f"pipeline-{code['version']}.tar.gz",
                        headers={"Cache-Control": "private, no-store", "X-Pipeline-Version": code["version"]})


@router.get("/processing/pipeline/model.onnx", response_class=FileResponse,
            responses={200: {"content": {"application/octet-stream": {}}}, 206: {"description": "partial (Range)"}},
            summary="The robot detector model (admin; Range-resumable)")
def pipeline_model(user: User = Depends(require_role("admin"))):
    mp = _model_path()
    if not os.path.isfile(mp):
        raise HTTPException(404, "no detector model on the server yet")
    return FileResponse(mp, media_type="application/octet-stream", filename="robot.onnx",
                        headers={"Cache-Control": "private, no-store"})


# ------------------------------------------------------------------ claim
class ClaimIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    processor: str = Field(min_length=1, max_length=48, description="this processor's id (per install)")
    pipeline: str | None = Field(None, max_length=40, description="pipeline version it runs: 409 when outdated")
    events: list[str] | None = Field(None, max_length=50, description="only these followed events (default: all)")
    skip: list[str] = Field(default_factory=list, max_length=500, description="match keys it failed on recently")


class ClaimOut(BaseModel):
    job: dict | None = Field(description="null = nothing to do now; else ev, key, match, event, live, camera, "
                                         "pose_guess, clip {url,size,sha256}, view_ref {url,size,sha256}|null, "
                                         "urls, claim_s, heartbeat_s, pipeline")
    why: str | None = None
    counts: dict = Field(default_factory=dict, description="per state: tracked, claimed (by others), yours (held "
                                                           "by this processor: never handed out twice), waiting "
                                                           "(no trackable clip), failed")


def _played(m):
    s = ((m.get("alliances") or {}).get("red") or {}).get("score")
    return s is not None and s >= 0 and not m.get("livestream")


def _skipset(st):
    """Records the worker would not use again for this match (worker.live_input / live_only_input)."""
    sk = set(st.get("live_skip") or [])
    if st.get("live_rejected"):
        sk.add(st["live_rejected"])
    return sk


def _next(ev, key, by, skip, counts):
    """The first match of the event (claims.order) with a trackable livestream clip, claimed for `by`."""
    ms = list(_tba_matches(ev, key) or [])
    lv = LI.LiveView(ev, root=_live_root())
    if lv:
        ms += lv.extra_matches({m["key"] for m in ms})
    ms = CL.order(cfg.DATA, ev, ms)
    status = _jload(os.path.join(_evd(ev), "status.json"), {}) or {}
    mode, fails = _video_mode(ev), _failures(ev)
    for m in ms:
        k = m.get("key")
        if not isinstance(k, str) or not MATCH_RE.match(k):
            continue
        if os.path.exists(os.path.join(_evd(ev), "matches", f"{k}.json")):
            counts["tracked"] = counts.get("tracked", 0) + 1; continue
        st = status.get(k) or {}
        if st.get("state") == "failed" or len(fails.get(k) or []) >= MAX_FAILS:
            counts["failed"] = counts.get("failed", 0) + 1; continue
        if mode != "livestream" and st.get("live_failed", 0) >= 2:      # worker.LIVE_FAILS: waits for uploads
            counts["waiting"] = counts.get("waiting", 0) + 1; continue
        if k in skip:
            continue
        c = CL.read(cfg.DATA, ev, k)
        if c and c.get("by") == by:                # never hand a processor a match it holds (any age): it keeps
            counts["yours"] = counts.get("yours", 0) + 1; continue   # it fresh with heartbeats or releases it
        if CL.claimed_elsewhere(cfg.DATA, ev, k, by):
            counts["claimed"] = counts.get("claimed", 0) + 1; continue
        try:
            live = lv.for_processing(m, _played(m), skip=_skipset(st)) if lv else None
        except Exception:
            live = None
        if live is None or not live.get("clip") or not _clip_ok(live["clip"]):
            counts["waiting"] = counts.get("waiting", 0) + 1; continue
        cam = _jload(live.get("camera"))
        if not isinstance(cam, dict):
            counts["waiting"] = counts.get("waiting", 0) + 1; continue
        if not CL.claim(cfg.DATA, ev, k, by):
            counts["claimed"] = counts.get("claimed", 0) + 1; continue
        return m, live, cam, ms
    return None


def _job_out(ev, m, live, cam, ms, key, user, processor, by, version):
    k = m["key"]
    base = f"/app/v1/processing/{ev}/{k}"
    q = f"?processor={processor}"
    size, sha = _file_sha(live["clip"])
    vr = live.get("view_ref")
    vref = None
    if vr and os.path.isfile(vr) and _under(vr, cfg.DATA):
        vs, vh = _file_sha(vr)
        vref = {"url": f"{base}/view.png{q}", "size": vs, "sha256": vh}
    else:
        vr = None
    src = live.get("source") or ""
    job = {"ev": ev, "key": k, "by": by, "processor": processor, "user": user.id, "claimed_at": time.time(),
           "clip": live["clip"], "clip_size": size, "clip_sha256": sha, "view_ref": vr,
           "record_path": (live.get("info") or {}).get("record_path"), "pipeline": version}
    atomic_write_json(os.path.join(_stage(ev, k), "job.json"), job, mode=0o600)
    pose = _jload(os.path.join(_evd(ev), f"pose_latest_{src}.json")) if re.match(r"^[A-Za-z0-9_.-]{1,64}$", src) else None
    return {
        "ev": ev, "key": k, "by": by, "processor": processor, "claimed_at": job["claimed_at"],
        "claim_s": CL.CLAIM_S, "heartbeat_s": HEARTBEAT_S, "pipeline": version,
        "match": m,                                          # the TBA match (score_breakdown for shots.reconcile)
        "event": None if m.get("livestream") else dict(_event_ctx(ev, key, ms), key=k),
        "live": {x: live.get(x) for x in ("start", "start_trusted", "teams", "teams_from", "source", "tba_posted",
                                         "layout", "info", "video")},
        "camera": {"name": os.path.basename(live["camera"]), "data": cam},
        "pose_guess": pose if isinstance(pose, dict) else None,
        "clip": {"url": f"{base}/clip{q}", "size": size, "sha256": sha},
        "view_ref": vref,
        "urls": {"heartbeat": f"{base}/heartbeat", "results": f"{base}/results/{{kind}}{q}",
                 "complete": f"{base}/complete", "release": f"{base}/release"},
        "kinds": list(KINDS),
    }


@router.post("/processing/claim", response_model=ClaimOut,
             summary="Claim the next match to track (admin; same claims + order as the worker)")
def claim_next(body: ClaimIn, request: Request, user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    C, _ = _mods()
    by = _by(user, body.processor)
    code = pipeline_code()
    if body.pipeline and body.pipeline != code["version"]:
        raise HTTPException(409, f"the pipeline changed (server runs {code['version']}): update it first "
                                 "(GET /processing/pipeline)")
    followed = [r["key"] for r in db.execute("SELECT key FROM events ORDER BY start_date DESC, key").fetchall()]
    if body.events is not None:
        bad = [e for e in body.events if not isinstance(e, str) or not EVENT_RE.match(e)]
        if bad:
            raise HTTPException(422, "events: event keys like 2026wasam")
        followed = [e for e in followed if e in set(body.events)]
    skip = {k for k in body.skip if isinstance(k, str) and MATCH_RE.match(k)}
    key = DB.get_setting(db, "tba_key")
    counts: dict = {}
    request.state.audited = True                   # nothing to log when nothing was claimed
    for ev in followed:
        try:
            got = _next(ev, key, by, skip, counts)
        except HTTPException:
            raise
        except Exception as e:                     # one broken event must not stop the others
            import logging
            logging.getLogger("cyberscout.api").exception("processing claim: %s failed", ev)
            counts["errors"] = counts.get("errors", 0) + 1
            counts.setdefault("error", f"{ev}: {e.__class__.__name__}")
            continue
        if got:
            m, live, cam, ms = got
            try:
                job = _job_out(ev, m, live, cam, ms, key, user, body.processor, by, code["version"])
            except Exception:
                C.release(cfg.DATA, ev, m["key"], by)
                raise
            _audit(db, request, user, "processing.claim", ev, m["key"],
                   {"by": by, "clip": os.path.basename(live["clip"]), "pipeline": code["version"]})
            return ClaimOut(job=job, counts=counts)
    why = ("no followed events" if not followed else
           "nothing to track right now" + (f" ({', '.join(f'{v} {k}' for k, v in counts.items() if k != 'error')})"
                                           if counts else ""))
    return ClaimOut(job=None, why=why, counts=counts)


# ------------------------------------------------------------------ per-job routes
def _job(ev, key, user, processor, live=True):
    """job.json of a match handed to this processor; live=True: its claim must still be held (not stale)."""
    C, _ = _mods()
    ev, key = _match(ev, key)
    by = _by(user, processor)
    job = _jload(os.path.join(_stage(ev, key), "job.json"))
    if not isinstance(job, dict) or job.get("by") != by:
        raise HTTPException(409, "this match is not claimed by this processor (claim it first)")
    if live and not C.holds(cfg.DATA, ev, key, by):
        raise HTTPException(409, "the claim on this match was lost (stale or taken over); give it up")
    return job, by


@router.get("/processing/{ev}/{key}/clip", response_class=FileResponse,
            responses={200: {"content": {"video/mp4": {}}}, 206: {"description": "partial (Range)"}},
            summary="The claimed match's livestream clip (admin; Range-resumable)")
def job_clip(ev: str, key: str, processor: str = Query(..., max_length=48), user: User = Depends(require_role("admin"))):
    job, _ = _job(ev, key, user, processor)
    p = job.get("clip")
    if not p or not os.path.isfile(p) or not _clip_ok(p):
        raise HTTPException(410, "the clip is gone from the server")
    return FileResponse(p, media_type="video/mp4", headers={"Cache-Control": "private, no-store"})


@router.get("/processing/{ev}/{key}/view.png", response_class=FileResponse,
            responses={200: {"content": {"image/png": {}}}},
            summary="The stream camera's calibrated view (process.view_ref) for the claimed match (admin)")
def job_view(ev: str, key: str, processor: str = Query(..., max_length=48), user: User = Depends(require_role("admin"))):
    job, _ = _job(ev, key, user, processor)
    p = job.get("view_ref")
    if not p or not os.path.isfile(p) or not _under(p, cfg.DATA):
        raise HTTPException(404, "no view reference for this stream camera")
    return FileResponse(p, media_type="image/png", headers={"Cache-Control": "private, no-store"})


class BeatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    processor: str = Field(min_length=1, max_length=48)
    progress: float | None = Field(None, ge=0, le=1)
    stage: str | None = Field(None, max_length=32, description="downloading | processing | uploading ...")


@router.post("/processing/{ev}/{key}/heartbeat", summary="Keep the claim fresh; report progress (admin)")
def job_heartbeat(ev: str, key: str, body: BeatIn, request: Request, user: User = Depends(require_role("admin"))):
    request.state.audited = True                   # every minute per processor: not worth an audit entry
    job, by = _job(ev, key, user, body.processor, live=False)
    if not CL.heartbeat(cfg.DATA, ev, key, by):
        raise HTTPException(409, "the claim on this match was lost (stale or taken over); give it up")
    if body.stage is not None and not STAGE_RE.match(body.stage):
        raise HTTPException(422, "stage: a short lower-case word")
    job.update(beat_at=time.time(), progress=body.progress, stage=body.stage)
    atomic_write_json(os.path.join(_stage(ev, key), "job.json"), job, mode=0o600)
    return {"ok": True, "at": job["beat_at"], "claim_s": CL.CLAIM_S}


def _decode(raw: bytes, enc: str) -> bytes:
    enc = (enc or "").strip().lower()
    if enc in ("", "identity"):
        return raw
    if enc not in ("gzip", "x-gzip"):
        raise HTTPException(415, "Content-Encoding: gzip or none")
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = d.decompress(raw, MAX_JSON + 1)
    except zlib.error:
        raise HTTPException(400, "corrupt gzip body")
    if len(out) > MAX_JSON or d.unconsumed_tail:
        raise HTTPException(413, f"result too large (max {MAX_JSON >> 20} MB)")
    if not d.eof:
        raise HTTPException(400, "truncated gzip body")
    return out


def _validate(kind, key, obj):
    if not isinstance(obj, dict):
        raise HTTPException(422, f"{kind}: a JSON object")
    if kind == "matches":
        if obj.get("key") != key:
            raise HTTPException(422, "matches: its key is not this match")
        if not isinstance(obj.get("video"), dict) or not isinstance(obj.get("feed_ok"), (int, float)):
            raise HTTPException(422, "matches: not a tracking result (video, feed_ok)")
    elif kind == "fuel" and not isinstance(obj.get("tracks"), list):
        raise HTTPException(422, "fuel: {tracks: [...]}")


@router.put("/processing/{ev}/{key}/results/{kind}",
            summary="Upload one result file of the claimed match (admin; JSON, optionally gzip; staged until complete)")
async def job_result(ev: str, key: str, kind: str, request: Request, processor: str = Query(..., max_length=48),
                     user: User = Depends(require_role("admin"))):
    request.state.audited = True                   # logged once, by complete
    if kind not in KINDS:
        raise HTTPException(404, f"kind: one of {', '.join(KINDS)}")
    await run_in_threadpool(_job, ev, key, user, processor)
    try:
        n = int(request.headers.get("content-length") or 0)
    except ValueError:
        raise HTTPException(400, "bad Content-Length")
    if n > MAX_BODY:
        raise HTTPException(413, f"upload too large (max {MAX_BODY >> 20} MB; send it gzip'd)")
    chunks, total = [], 0
    async for c in request.stream():
        total += len(c)
        if total > MAX_BODY:
            raise HTTPException(413, f"upload too large (max {MAX_BODY >> 20} MB; send it gzip'd)")
        chunks.append(c)
    data = _decode(b"".join(chunks), request.headers.get("content-encoding"))
    try:
        obj = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, f"{kind}: not valid JSON")
    _validate(kind, key, obj)

    def write():
        p = os.path.join(_stage(ev, key), f"{kind}.json")
        tmp = f"{p}.tmp{os.getpid()}.{threading.get_ident()}"
        with open(tmp, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, p)
    await run_in_threadpool(write)
    return {"kind": kind, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


class DoneIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    processor: str = Field(min_length=1, max_length=48)
    seconds: float | None = Field(None, ge=0, le=86400, description="processing time on the processor")


def _keep_clip(ev, key, clip):
    """MEDIA/<ev>/<key>.mp4 (like the worker's KEEP_VIDEO): a hard link to the stream clip, else a copy."""
    dst = os.path.join(cfg.MEDIA, ev, f"{key}.mp4")
    if os.path.exists(dst):
        return {"ok": True, "kept": "already there"}
    if not clip or not os.path.isfile(clip):
        return {"ok": False, "error": "the clip is gone from the server"}
    tmp = f"{dst}.tmp{os.getpid()}"
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            os.link(clip, tmp)
            how = "linked"
        except OSError:
            shutil.copyfile(clip, tmp)
            how = "copied"
        os.replace(tmp, dst)
        return {"ok": True, "kept": how}
    except OSError as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return {"ok": False, "error": f"could not write MEDIA/{ev}/{key}.mp4 ({e.__class__.__name__})"}


@router.post("/processing/{ev}/{key}/complete",
             summary="Publish the uploaded results (matches last), keep the clip, release the claim (admin)")
def job_complete(ev: str, key: str, body: DoneIn, request: Request, user: User = Depends(require_role("admin")),
                 db=Depends(DB.get_db)):
    C, _ = _mods()
    ev, key = _match(ev, key)
    by = _by(user, body.processor)
    stage, evd = _stage(ev, key), _evd(ev)
    final = os.path.join(evd, "matches", f"{key}.json")
    with _lock:
        job = _jload(os.path.join(stage, "job.json"))
        if not isinstance(job, dict):
            if os.path.exists(final):              # a retry after the answer was lost: done already
                request.state.audited = True
                return {"ok": True, "already": True, "files": [], "media": None}
            raise HTTPException(409, "this match is not claimed by this processor (claim it first)")
        if job.get("by") != by:
            raise HTTPException(409, "this match is not claimed by this processor (claim it first)")
        if os.path.exists(final):                  # someone else finished it meanwhile
            C.release(cfg.DATA, ev, key, by)
            shutil.rmtree(stage, ignore_errors=True)
            raise HTTPException(409, "this match was tracked by another processor meanwhile; results dropped")
        if not C.claim(cfg.DATA, ev, key, by):     # ours (fresh or stale but untouched), else lost
            raise HTTPException(409, "the claim on this match was lost (taken over by another processor)")
        if not os.path.exists(os.path.join(stage, "matches.json")):
            raise HTTPException(409, "upload results/matches first")
        files, nbytes = [], 0
        for kind in KINDS:                         # matches last: then the worker treats the match as done
            src = os.path.join(stage, f"{kind}.json")
            if not os.path.exists(src):
                continue
            dst = os.path.join(evd, kind, f"{key}.json")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            nbytes += os.path.getsize(src)
            os.replace(src, dst)
            files.append(kind)
        media = _keep_clip(ev, key, job.get("clip"))
        C.release(cfg.DATA, ev, key, by)
        shutil.rmtree(stage, ignore_errors=True)
    _clear_failures(ev, key)
    _audit(db, request, user, "processing.upload", ev, key,
           {"by": by, "files": files, "bytes": nbytes, "media": media.get("ok"), "seconds": body.seconds,
            "pipeline": job.get("pipeline")})
    return {"ok": True, "already": False, "files": files, "media": media}


class ReleaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    processor: str = Field(min_length=1, max_length=48)
    failed: bool = Field(False, description="true: it failed on this processor (after MAX_FAILS the processors "
                                            "leave the match to the worker)")
    reason: str | None = Field(None, max_length=300)


@router.post("/processing/{ev}/{key}/release", summary="Give a claimed match back (admin)")
def job_release(ev: str, key: str, body: ReleaseIn, request: Request, user: User = Depends(require_role("admin")),
                db=Depends(DB.get_db)):
    C, _ = _mods()
    ev, key = _match(ev, key)
    by = _by(user, body.processor)
    stage = _stage(ev, key)
    job = _jload(os.path.join(stage, "job.json"))
    mine = isinstance(job, dict) and job.get("by") == by
    released = C.release(cfg.DATA, ev, key, by)
    if mine:
        shutil.rmtree(stage, ignore_errors=True)
    if not (mine or released):
        request.state.audited = True
        return {"released": False}
    reason = "".join(ch for ch in (body.reason or "") if ch.isprintable())[:300]
    if body.failed:
        _note_failure(ev, key, by, reason)
    _audit(db, request, user, "processing.release", ev, key, {"by": by, "failed": body.failed, "reason": reason})
    return {"released": True}


# ------------------------------------------------------------------ status
@router.get("/processing/status", summary="Matches being tracked by processors right now (admin)")
def processing_status(user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    C, _ = _mods()
    out = []
    now = time.time()
    for r in db.execute("SELECT key FROM events ORDER BY start_date DESC, key").fetchall():
        ev = r["key"]
        d = os.path.join(_evd(ev), "claims")
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for f in names:
            k = f[:-5]
            if not f.endswith(".json") or not MATCH_RE.match(k):
                continue
            c = C.read(cfg.DATA, ev, k) or {}
            job = _jload(os.path.join(_stage(ev, k), "job.json")) or {}
            by = c.get("by") if isinstance(c.get("by"), str) else None
            out.append({"ev": ev, "key": k, "by": by, "age_s": round(now - float(c.get("at") or 0), 1),
                        "stale": C.is_stale(c, now), "mine": bool(by and by.startswith(f"app-u{user.id}-")),
                        "progress": job.get("progress") if job.get("by") == by else None,
                        "stage": job.get("stage") if job.get("by") == by else None})
    return {"claims": out, "claim_s": C.CLAIM_S, "fails": {r["key"]: _failures(r["key"]) for r in
                                                           db.execute("SELECT key FROM events").fetchall()
                                                           if _failures(r["key"])}}


# ------------------------------------------------------------------ connection check (client's slow-link notice)
SPEED_MAX = 8 << 20                # one speed-test download / upload
_NOISE = os.urandom(1 << 20)       # incompressible (a proxy / tunnel can't shrink it)


@router.get("/processing/speedtest", response_class=StreamingResponse,
            responses={200: {"content": {"application/octet-stream": {}}}},
            summary="N random bytes: download speed to this server (admin; processors)")
def speedtest_get(request: Request, bytes: int = Query(1 << 20, ge=1, le=SPEED_MAX),
                  user: User = Depends(require_role("admin"))):
    def gen(n=bytes):
        while n > 0:
            b = _NOISE[:min(n, len(_NOISE))]
            n -= len(b)
            yield b
    return StreamingResponse(gen(), media_type="application/octet-stream",
                             headers={"Content-Length": str(bytes), "Cache-Control": "no-store",
                                      "Content-Encoding": "identity"})


@router.post("/processing/speedtest", summary="Upload speed check: the body is read and dropped (admin; processors)")
async def speedtest_post(request: Request, user: User = Depends(require_role("admin"))):
    request.state.audited = True
    t0, n = time.time(), 0
    async for c in request.stream():
        n += len(c)
        if n > SPEED_MAX:
            raise HTTPException(413, f"speed test bodies are at most {SPEED_MAX >> 20} MB")
    return {"bytes": n, "seconds": round(time.time() - t0, 3)}


# ------------------------------------------------------------------ benchmark sample
SAMPLE_BYTES = 24 << 20            # ~20 s of a 1080p60 livestream clip (faststart: the index is at the front)
SAMPLE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}\.mp4$")


def _faststart(p):
    """The MP4's moov box comes before mdat (a prefix of the file is decodable)."""
    try:
        with open(p, "rb") as f:
            off = 0
            for _ in range(16):
                f.seek(off)
                h = f.read(16)
                if len(h) < 8:
                    return False
                n, kind = int.from_bytes(h[:4], "big"), h[4:8]
                if n == 1:
                    n = int.from_bytes(h[8:16], "big")
                if kind == b"moov":
                    return True
                if kind == b"mdat" or n < 8:
                    return False
                off += n
    except OSError:
        pass
    return False


def _sample_candidates(db):
    """Recent livestream clips of the followed events, newest first: (ev, path)."""
    out = []
    for r in db.execute("SELECT key FROM events ORDER BY start_date DESC, key").fetchall():
        ev = r["key"]
        base = os.path.join(_live_root(), ev)
        try:
            dirs = os.listdir(base)
        except OSError:
            continue
        for d in dirs:
            cd = os.path.join(base, d, "clips")
            try:
                names = os.listdir(cd)
            except OSError:
                continue
            for n in names:
                p = os.path.join(cd, n)
                if SAMPLE_RE.match(n) and os.path.isfile(p) and os.path.getsize(p) > 2 * SAMPLE_BYTES:
                    out.append((os.path.getmtime(p), ev, p))
    out.sort(reverse=True)
    return [(ev, p) for _, ev, p in out[:40]]


_sample_cache: dict = {}


def _sample(db):
    for ev, p in _sample_candidates(db):
        if _clip_ok(p) and _faststart(p):
            st = os.stat(p)
            k = (p, st.st_size, st.st_mtime_ns)
            if k not in _sample_cache:
                h = hashlib.sha256()
                with open(p, "rb") as f:
                    h.update(f.read(SAMPLE_BYTES))
                _sample_cache.clear()
                _sample_cache[k] = h.hexdigest()
            return ev, p, st.st_size, _sample_cache[k]
    return None


@router.get("/processing/sample", summary="A short sample of a recent livestream clip for the benchmark (admin)")
def sample_info(user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    got = _sample(db)
    if not got:
        return {"clip": None, "typical_clip_bytes": None,
                "why": "no livestream clips on the server yet (the benchmark uses synthetic frames)"}
    ev, p, size, sha = got
    return {"clip": {"url": f"/app/v1/processing/sample.mp4?ev={ev}&name={os.path.basename(p)}",
                     "size": SAMPLE_BYTES, "sha256": sha},
            "of": f"{ev}/{os.path.basename(p)}", "full_bytes": size, "typical_clip_bytes": size}


@router.get("/processing/sample.mp4", response_class=Response,
            responses={200: {"content": {"video/mp4": {}}}, 206: {"description": "partial (Range)"}},
            summary="The first bytes of that clip (admin; Range-resumable)")
def sample_file(request: Request, ev: str = Query(..., max_length=24), name: str = Query(..., max_length=84),
                user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    if not EVENT_RE.match(ev) or not SAMPLE_RE.match(name):
        raise HTTPException(422, "bad sample reference")
    p = next((p for e, p in _sample_candidates(db) if e == ev and os.path.basename(p) == name), None)
    if not p or not _clip_ok(p):
        raise HTTPException(404, "that sample is gone; GET /processing/sample again")
    n, a = SAMPLE_BYTES, 0
    m = re.match(r"^bytes=(\d+)-$", (request.headers.get("range") or "").strip())
    if m:
        a = int(m.group(1))
        if a >= n:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{n}"})
    with open(p, "rb") as f:
        f.seek(a)
        data = f.read(n - a)
    h = {"Cache-Control": "private, no-store", "Accept-Ranges": "bytes"}
    if m:
        h["Content-Range"] = f"bytes {a}-{n - 1}/{n}"
    return Response(data, status_code=206 if m else 200, media_type="video/mp4", headers=h)


# ------------------------------------------------------------------ Clip only: stream assignments
AID_RE = re.compile(r"^cs[0-9a-f]{10}$")
MID_RE = re.compile(r"^tba-(qm[0-9]{1,3}|sf[0-9]{1,2}m[0-9]{1,2}|f[0-9]{1,2}m[0-9]{1,2})$")
FLAG_RE = re.compile(r"^[a-z0-9_]{1,48}$")
UTC_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z$")
MAX_CLIP = 1 << 30                 # one uploaded match clip (a 1080p60 livestream clip is ~190 MB)
CHUNK = 8 << 20                    # suggested chunk (nginx: 25m on /app/v1/)
CHUNK_MAX = 16 << 20
GRACE_S = 240                      # the server records this event too: wait this long after a match for its own clip
MIN_FREE = 2 << 30                 # free space to keep on the media disk
MATCH_END_S = 163 + 10 + 20        # liveingest MATCH_S + TBA_TAIL_S + 20: the earliest a TBA-time cut can happen
_slock = threading.Lock()
_ulocks: dict = {}


def _streams_dir(ev): return os.path.join(cfg.DATA, "processing", "streams", ev)
def _sdir(ev, aid): return os.path.join(_live_root(), ev, aid)


def _assignments(ev):
    out = []
    try:
        names = sorted(os.listdir(_streams_dir(ev)))
    except OSError:
        return out
    for n in names:
        if n.endswith(".json") and AID_RE.match(n[:-5]):
            a = _jload(os.path.join(_streams_dir(ev), n))
            if isinstance(a, dict) and a.get("aid") == n[:-5]:
                out.append(a)
    return out


def _live(a, now=None):
    return not a.get("released") and (now or time.time()) - float(a.get("at") or 0) < CL.CLAIM_S


def _event_info(ev, key):
    d, _ = _tba_disk(ev, f"/event/{ev}")
    if isinstance(d, dict):
        return d
    if key:
        try:
            return tba.get(key, f"/event/{ev}", ttl=3600)
        except tba.TBAError:
            pass
    return None


def _today(info):
    now = datetime.now(timezone.utc)
    out = {now.strftime("%Y-%m-%d")}
    tz = (info or {}).get("timezone")
    if tz:
        try:
            from zoneinfo import ZoneInfo
            out.add(now.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d"))
        except Exception:
            pass
    return out


def _in_dates(info):
    """Like the recorder service: the event's dates +- 1 day (unknown dates: yes)."""
    if not info or not info.get("start_date"):
        return True
    try:
        d0 = datetime.fromisoformat(info["start_date"]).date() - timedelta(days=1)
        d1 = datetime.fromisoformat(info.get("end_date") or info["start_date"]).date() + timedelta(days=1)
    except ValueError:
        return True
    return d0 <= datetime.now(timezone.utc).date() <= d1


def _server_records(ev):
    """The source the server's own recorder service is on for this event right now (livestream.json), or None."""
    st = _jload(os.path.join(_evd(ev), "livestream.json"), {}) or {}
    if time.time() - float(st.get("updated") or 0) > 180 or st.get("state") not in ("recording", "waiting",
                                                                                      "starting"):
        return None
    return ((st.get("source") or {}).get("id")) or None


def _sources(ev, key):
    if SRC is None:
        raise HTTPException(503, "video setup (live/sources.py) is not installed in this API image")
    info = _event_info(ev, key)
    doc = SRC.load(_evd(ev))
    return LI.live_sources(doc, _today(info), info), info


class StreamClaimIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    processor: str = Field(min_length=1, max_length=48)
    events: list[str] | None = Field(None, max_length=50, description="only these followed events (default: all)")
    ev: str | None = Field(None, max_length=24, description="this event only")
    source: str | None = Field(None, max_length=64, description="this livestream source (sources.json id) only")


def _assignment_out(a, matches_for=None):
    return {k: a.get(k) for k in ("aid", "ev", "source", "url", "label", "provider", "role", "by", "processor",
                                  "started", "at")} | {
        "heartbeat_s": HEARTBEAT_S, "claim_s": CL.CLAIM_S, "chunk_bytes": CHUNK, "chunk_max": CHUNK_MAX,
        "max_clip_bytes": MAX_CLIP, "lead_s": float(getattr(LI, "TBA_LEAD_S", 15.0)),
        "tail_s": float(getattr(LI, "TBA_TAIL_S", 10.0)), "backfill_min": 0}


@router.post("/processing/streams/claim", summary="Clip only: get a livestream to record and cut (admin)")
def stream_claim(body: StreamClaimIn, request: Request, user: User = Depends(require_role("admin")),
                 db=Depends(DB.get_db)):
    _mods()
    by = _by(user, body.processor)
    followed = [r["key"] for r in db.execute("SELECT key FROM events ORDER BY start_date DESC, key").fetchall()]
    want = set(body.events) if body.events is not None else None
    if want is not None and any(not isinstance(e, str) or not EVENT_RE.match(e) for e in want):
        raise HTTPException(422, "events: event keys like 2026wasam")
    if body.ev is not None and not EVENT_RE.match(body.ev):
        raise HTTPException(422, "bad event key (expected e.g. 2026wasam)")
    evs = [e for e in followed if (want is None or e in want) and (body.ev is None or e == body.ev)]
    key = DB.get_setting(db, "tba_key")
    request.state.audited = True
    now, why = time.time(), []
    for ev in evs:
        try:
            ls, info = _sources(ev, key)
        except Exception as e:                          # one broken event must not stop the others
            why.append(f"{ev}: {e.__class__.__name__}")
            continue
        if body.source:
            ls = [s for s in ls if s.get("id") == body.source]
        if not ls:
            why.append(f"{ev}: no livestream source")
            continue
        if not _in_dates(info):
            why.append(f"{ev}: not today ({(info or {}).get('start_date')})")
            continue
        rec = _server_records(ev)
        with _slock:
            mine = [a for a in _assignments(ev) if a.get("by") == by and not a.get("released")]
            taken = {a["source"] for a in _assignments(ev) if a.get("by") != by and _live(a, now)}
            # a source the server's recorder is not on first (a second view), then a backup of the one it is on
            free = [s for s in ls if s["id"] not in taken]
            free.sort(key=lambda s: s["id"] == rec)
            prev = next((a for a in mine if any(s["id"] == a["source"] for s in free)), None)
            s = next((s for s in free if prev and s["id"] == prev["source"]), None) or (free[0] if free else None)
            if s is None:
                why.append(f"{ev}: every livestream is being recorded by another processor")
                continue
            for a in mine:                              # one stream per processor
                if a["source"] != s["id"]:
                    a.update(released=True, released_at=now)
                    atomic_write_json(os.path.join(_streams_dir(ev), f"{a['aid']}.json"), a)
            aid = "cs" + hashlib.sha256(f"{by}\0{ev}\0{s['id']}".encode()).hexdigest()[:10]
            p = os.path.join(_streams_dir(ev), f"{aid}.json")
            a = _jload(p) if os.path.exists(p) else None
            fresh = not isinstance(a, dict) or bool(a.get("released"))     # resumed after a restart: same start
            a = a if isinstance(a, dict) else {"aid": aid, "ev": ev, "by": by, "processor": body.processor,
                                               "user": user.id}
            if fresh:
                a["started"] = now
            a.update(source=s["id"], url=s["url"], label=s.get("label"), provider=s.get("provider"),
                     role="backup" if s["id"] == rec else "secondary", at=now, released=False)
            atomic_write_json(p, a)
        _audit(db, request, user, "processing.stream", ev, aid, {"by": by, "source": s["id"], "role": a["role"],
                                                                  "resumed": not fresh})
        return {"assignment": _assignment_out(a), "why": None}
    return {"assignment": None, "why": "; ".join(why) if why else "no followed events"}


def _assignment(ev, aid, user, processor, live=False):
    _mods()
    if not EVENT_RE.match(ev):
        raise HTTPException(422, "bad event key (expected e.g. 2026wasam)")
    if not AID_RE.match(aid):
        raise HTTPException(422, "bad stream assignment id")
    by = _by(user, processor)
    a = _jload(os.path.join(_streams_dir(ev), f"{aid}.json"))
    if not isinstance(a, dict) or a.get("by") != by:
        raise HTTPException(409, "this stream is not assigned to this processor (claim one first)")
    if live and a.get("released"):
        raise HTTPException(409, "this stream assignment was released; claim again")
    return a, by


def _mid(m):
    return "tba-" + m["key"].split("_", 1)[1]


def _match_brief(m):
    a = m.get("alliances") or {}
    return {"key": m.get("key"), "comp_level": m.get("comp_level"), "set_number": m.get("set_number"),
            "match_number": m.get("match_number"), "actual_time": m.get("actual_time"), "time": m.get("time"),
            "alliances": {s: {"team_keys": list((a.get(s) or {}).get("team_keys") or []),
                              "score": (a.get(s) or {}).get("score")} for s in ("red", "blue")}}


class StreamBeatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    processor: str = Field(min_length=1, max_length=48)
    state: str | None = Field(None, max_length=32, description="recording | waiting | ... (shown in the status)")
    rec: dict | None = Field(None, description="recorder progress {chunks, bytes, behind_s, last_wall, video}")


@router.post("/processing/streams/{ev}/{aid}/heartbeat",
             summary="Clip only: keep the stream; -> TBA match start times and which clips to upload (admin)")
def stream_heartbeat(ev: str, aid: str, body: StreamBeatIn, request: Request,
                     user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    request.state.audited = True
    a, by = _assignment(ev, aid, user, body.processor)
    if a.get("released"):
        return {"ok": False, "stop": True, "why": "released (another stream was assigned, or it was stopped)"}
    now = time.time()
    with _slock:
        other = next((x for x in _assignments(ev) if x["aid"] != aid and x.get("source") == a.get("source")
                      and x.get("by") != by and _live(x, now) and float(x.get("started") or 0) > float(a["at"])), None)
        if other:                                       # stale and taken over meanwhile
            a.update(released=True, released_at=now)
            atomic_write_json(os.path.join(_streams_dir(ev), f"{aid}.json"), a)
            return {"ok": False, "stop": True, "why": "the stream was assigned to another processor"}
        if body.state is not None and not STAGE_RE.match(body.state):
            raise HTTPException(422, "state: a short lower-case word")
        rec = {k: v for k, v in (body.rec or {}).items() if k in ("chunks", "bytes", "behind_s", "last_wall", "video",
                                                                  "cut", "uploaded", "dirs")
               and (isinstance(v, (int, float)) or (isinstance(v, str) and len(v) <= 64))}
        a.update(at=now, state=body.state, rec=rec)
        atomic_write_json(os.path.join(_streams_dir(ev), f"{aid}.json"), a)
    key = DB.get_setting(db, "tba_key")
    ms = [m for m in (_tba_matches(ev, key) or []) if isinstance(m, dict) and isinstance(m.get("key"), str)
          and MATCH_RE.match(m["key"]) and LI.tba_record_fields(ev, m) is not None]
    t_from = float(a.get("started") or now) - 900       # what this recording can cover (+ a little DVR)
    ms = [m for m in ms if (m.get("actual_time") or 0) >= t_from or (not m.get("actual_time")
                                                                      and (m.get("time") or 0) >= t_from)]
    ms.sort(key=lambda m: m.get("actual_time") or m.get("time") or 0)
    lv = LI.LiveView(ev, root=_live_root())
    sd = os.path.realpath(_sdir(ev, aid))
    have = {r["_key"] for r in lv.recs if os.path.realpath(r["_dir"]) == sd and r.get("_key")}
    elsewhere = {k for k, r in lv.clips.items() if os.path.realpath(r["_dir"]) != sd}
    grace = GRACE_S if _server_records(ev) else 0
    want, skipped = [], []
    for m in ms:
        k, t = m["key"], m.get("actual_time")
        if k in have:
            continue
        if k in elsewhere:
            skipped.append(k)                           # the server has a usable clip of it already
            continue
        if t and now >= t + MATCH_END_S + grace:
            want.append(k)
    return {"ok": True, "stop": False, "matches": [_match_brief(m) for m in ms[-200:]], "want": want,
            "have": sorted(have), "skipped": skipped, "grace_s": grace, "role": a.get("role"),
            "heartbeat_s": HEARTBEAT_S}


class StreamReleaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    processor: str = Field(min_length=1, max_length=48)
    reason: str | None = Field(None, max_length=300)


@router.post("/processing/streams/{ev}/{aid}/release", summary="Clip only: stop recording this stream (admin)")
def stream_release(ev: str, aid: str, body: StreamReleaseIn, request: Request,
                   user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    a, by = _assignment(ev, aid, user, body.processor)
    if a.get("released"):
        request.state.audited = True
        return {"released": False}
    with _slock:
        a.update(released=True, released_at=time.time())
        atomic_write_json(os.path.join(_streams_dir(ev), f"{aid}.json"), a)
    reason = "".join(ch for ch in (body.reason or "") if ch.isprintable())[:300]
    _audit(db, request, user, "processing.stream_release", ev, aid, {"by": by, "reason": reason})
    return {"released": True}


# ------------------------------------------------------------------ Clip only: chunked clip upload
class UploadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    processor: str = Field(min_length=1, max_length=48)
    size: int = Field(ge=1024, le=MAX_CLIP)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _upaths(ev, aid, mid):
    if not MID_RE.match(mid or ""):
        raise HTTPException(422, "bad clip id (expected e.g. tba-qm12)")
    up = os.path.join(_sdir(ev, aid), ".uploads")
    return up, os.path.join(up, f"{mid}.part"), os.path.join(up, f"{mid}.json")


def _ulock(k):
    with _slock:
        if len(_ulocks) > 500:
            _ulocks.clear()
        return _ulocks.setdefault(k, threading.Lock())


@router.post("/processing/streams/{ev}/{aid}/clips/{mid}",
             summary="Clip only: start or resume a chunked clip upload -> the offset to send from (admin)")
def clip_begin(ev: str, aid: str, mid: str, body: UploadIn, request: Request,
               user: User = Depends(require_role("admin"))):
    request.state.audited = True                   # logged once, by finish
    a, by = _assignment(ev, aid, user, body.processor)
    up, part, meta = _upaths(ev, aid, mid)
    if not _under(up, _live_root()):
        raise HTTPException(500, "the livestream folder is outside the media folder")
    final = os.path.join(_sdir(ev, aid), "clips", f"{mid}.mp4")
    with _ulock((ev, aid, mid)):
        m = _jload(meta)
        rec = _jload(os.path.join(_sdir(ev, aid), "matches", f"{mid}.json"))
        if os.path.isfile(final) and isinstance(rec, dict) and (rec.get("uploaded") or {}).get("sha256") == body.sha256:
            return {"done": True, "offset": body.size, "chunk_bytes": CHUNK}
        if not isinstance(m, dict) or m.get("sha256") != body.sha256 or m.get("size") != body.size:
            os.makedirs(up, exist_ok=True)
            free = shutil.disk_usage(up).free
            if free < body.size + MIN_FREE:
                raise HTTPException(507, f"the server's media disk is nearly full ({free >> 30} GB free)")
            try:
                os.remove(part)
            except OSError:
                pass
            m = {"size": body.size, "sha256": body.sha256, "by": by, "created": time.time()}
            atomic_write_json(meta, m)
            open(part, "wb").close()
        off = os.path.getsize(part) if os.path.exists(part) else 0
    return {"done": False, "offset": off, "size": body.size, "chunk_bytes": CHUNK, "chunk_max": CHUNK_MAX}


@router.put("/processing/streams/{ev}/{aid}/clips/{mid}/data",
            summary="Clip only: one chunk of the clip at ?offset= (admin; at most chunk_max bytes)")
async def clip_chunk(ev: str, aid: str, mid: str, request: Request, processor: str = Query(..., max_length=48),
                     offset: int = Query(..., ge=0, le=MAX_CLIP), user: User = Depends(require_role("admin"))):
    request.state.audited = True
    await run_in_threadpool(_assignment, ev, aid, user, processor)
    up, part, meta = _upaths(ev, aid, mid)
    try:
        n = int(request.headers.get("content-length") or 0)
    except ValueError:
        raise HTTPException(400, "bad Content-Length")
    if n > CHUNK_MAX:
        raise HTTPException(413, f"chunk too large (max {CHUNK_MAX >> 20} MB)")
    chunks, total = [], 0
    async for c in request.stream():
        total += len(c)
        if total > CHUNK_MAX:
            raise HTTPException(413, f"chunk too large (max {CHUNK_MAX >> 20} MB)")
        chunks.append(c)
    data = b"".join(chunks)

    def write():
        with _ulock((ev, aid, mid)):
            m = _jload(meta)
            if not isinstance(m, dict) or not os.path.exists(part):
                raise HTTPException(409, "no upload in progress for this clip (POST .../clips/{mid} first)")
            have = os.path.getsize(part)
            if offset != have:
                raise HTTPException(409, f"expected offset {have}")
            if have + len(data) > int(m["size"]):
                raise HTTPException(413, "more bytes than the announced size")
            with open(part, "ab") as f:
                f.write(data)
            return have + len(data)
    off = await run_in_threadpool(write)
    return {"offset": off}


class ClipRecord(BaseModel):
    """The match record a client cut (live/liveingest.py tba_cut): only these fields; the server sets the clip path."""
    model_config = ConfigDict(extra="forbid")
    id: str = Field(max_length=24)
    match_name: str | None = Field(None, max_length=100)
    comp_level: str = Field(pattern=r"^(qm|sf|f)$")
    set: int | None = Field(None, ge=0, le=999)
    number: int | None = Field(None, ge=0, le=999)
    teams: dict[str, list[int]] = Field(default_factory=dict)
    score: dict[str, int | None] | None = None
    score_source: str | None = Field(None, pattern=r"^(tba|stream|results_screen)$")
    t_start_stream: float | None = None
    t_start_utc: str | None = Field(None, max_length=40)
    auto_start_in_clip: float = Field(ge=0, le=60)
    confidence: dict[str, float] = Field(default_factory=dict)
    flags: list[str] = Field(default_factory=list, max_length=20)
    cut_from: dict[str, float] | None = None


class FinishIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    processor: str = Field(min_length=1, max_length=48)
    record: ClipRecord


def _clean_record(ev, mid, r: ClipRecord):
    if r.id != mid:
        raise HTTPException(422, "record: its id is not this clip")
    if set(r.teams) - {"red", "blue"} or any(len(v) > 4 or any(not 0 < t < 100000 for t in v)
                                              for v in r.teams.values()):
        raise HTTPException(422, "record: teams {red: [team numbers], blue: [...]}")
    if r.score is not None and (set(r.score) - {"red", "blue"} or any(v is not None and not -1 <= v <= 9999
                                                                       for v in r.score.values())):
        raise HTTPException(422, "record: score {red, blue}")
    if r.t_start_utc is not None and not UTC_RE.match(r.t_start_utc):
        raise HTTPException(422, "record: t_start_utc like 2026-10-17T18:00:00.00Z")
    if set(r.confidence) - {"match", "name", "teams", "score"} or any(not 0 <= v <= 1 for v in r.confidence.values()):
        raise HTTPException(422, "record: confidence {match, name, teams, score} in 0..1")
    if any(not FLAG_RE.match(f) for f in r.flags):
        raise HTTPException(422, "record: flags are short lower-case words")
    if r.cut_from is not None and set(r.cut_from) - {"actual_time", "lead", "at"}:
        raise HTTPException(422, "record: cut_from {actual_time, lead, at}")
    rec = r.model_dump()
    rec["match_name"] = "".join(ch for ch in (rec.get("match_name") or "") if ch.isprintable())[:100] or None
    if LI.record_key(ev, rec) != f"{ev}_{mid[4:]}":
        raise HTTPException(422, "record: comp_level / set / number are not this match")
    rec.update(breakdown=None, alliance_names=None, evidence={})
    if "uploaded_by_client" not in rec["flags"]:
        rec["flags"].append("uploaded_by_client")
    return rec


def _mp4_ok(p):
    try:
        with open(p, "rb") as f:
            h = f.read(12)
        return len(h) == 12 and h[4:8] == b"ftyp"
    except OSError:
        return False


@router.post("/processing/streams/{ev}/{aid}/clips/{mid}/finish",
             summary="Clip only: check the uploaded clip and register it as a livestream match record (admin)")
def clip_finish(ev: str, aid: str, mid: str, body: FinishIn, request: Request,
                user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    a, by = _assignment(ev, aid, user, body.processor)
    up, part, meta = _upaths(ev, aid, mid)
    rec = _clean_record(ev, mid, body.record)
    sd = _sdir(ev, aid)
    final = os.path.join(sd, "clips", f"{mid}.mp4")
    rp = os.path.join(sd, "matches", f"{mid}.json")
    with _ulock((ev, aid, mid)):
        m = _jload(meta)
        old = _jload(rp)
        if not isinstance(m, dict):
            if isinstance(old, dict) and os.path.isfile(final):    # a retry after the answer was lost
                request.state.audited = True
                return {"ok": True, "already": True, "key": f"{ev}_{mid[4:]}", "record": mid}
            raise HTTPException(409, "no upload in progress for this clip (POST .../clips/{mid} first)")
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if have != int(m["size"]):
            raise HTTPException(409, f"the clip is incomplete ({have} of {m['size']} bytes)")
        if _sha256(part) != m["sha256"]:
            os.remove(part)
            os.remove(meta)
            raise HTTPException(422, "the clip does not match its sha256; upload it again")
        if not _mp4_ok(part):
            raise HTTPException(422, "not an MP4 file")
        os.makedirs(os.path.dirname(final), exist_ok=True)
        os.makedirs(os.path.dirname(rp), exist_ok=True)
        os.chmod(part, 0o644)
        os.replace(part, final)
        os.remove(meta)
        mp = os.path.join(sd, "meta.json")
        md = _jload(mp, {}) or {}
        md.update(event=ev, source=a["source"], url=a.get("url"), video_id=aid, label=a.get("label"),
                  managed=False, mode="client", started=md.get("started") or a.get("started"),
                  client={"by": by, "processor": a.get("processor"), "user": user.id})
        atomic_write_json(mp, md)
        rec.update(clip=final, uploaded={"by": by, "at": time.time(), "size": int(m["size"]), "sha256": m["sha256"]})
        atomic_write_json(rp, rec)
    _audit(db, request, user, "processing.clip", ev, f"{aid}/{mid}",
           {"by": by, "source": a["source"], "bytes": int(m["size"]), "flags": rec["flags"]})
    return {"ok": True, "already": False, "key": f"{ev}_{mid[4:]}", "record": mid,
            "media": f"media/live/{ev}/{aid}/clips/{mid}.mp4"}
