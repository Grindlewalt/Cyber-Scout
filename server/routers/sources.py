"""Video sources, broadcast layout and camera calibration (agent B).  Mounted under /app/v1.

Reads are viewer+, everything that changes setup or touches video is admin.  The logic lives in
live/sources.py (stdlib) and live/layout.py (stdlib at import); the API image needs those two files
(`COPY live/sources.py live/layout.py live/`).  Video work (frame grabs, suggestions, AprilTags, fits) runs as
jobs: in this process when numpy/cv2 are importable, else in the worker's job thread (sources.job_loop).

Files (DATA = cfg.DATA):
  events/<ev>/sources.json       sources (live/sources.py)
  events/<ev>/layout.json        boxes per source (live/layout.py) -- the pipeline reads it
  events/<ev>/camera.json        overhead calibration (accepted from the wizard)
  events/<ev>/station_<side>.json station-camera calibrations (inset pixels + crop)
  events/<ev>/calib_wizard/<target>.json, frames/*.jpg   wizard state / sample frames
  jobs/sources/<id>.json         job queue
"""
import json, os, shutil, sys, time

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import db as DB
from ..config import cfg
from ..deps import User, require_role, valid_event
from ..security import atomic_write_json
from .settings import tba_call, tba_key_or_409

router = APIRouter(tags=["sources"])

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.join(_HERE, "..", "..", "live"), "/srv/live", "/app/live"):
    _p = os.path.normpath(_p)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.append(_p)
try:
    import sources as SRC          # live/sources.py
    import layout as LY            # live/layout.py
except Exception as _e:            # pragma: no cover - only when the image lacks live/
    SRC = LY = None
    _IMPORT_ERR = repr(_e)

TARGETS = ("overhead", "station_red", "station_blue")


def _mods():
    if SRC is None or LY is None:
        raise HTTPException(503, "video setup is not installed in this API image (needs live/sources.py and live/layout.py)")
    return SRC, LY


def _evd(ev):
    return os.path.join(cfg.DATA, "events", ev)


def _doc(ev):
    S, _ = _mods()
    return S.load(_evd(ev))


def _save(ev, doc):
    doc["event"] = ev; doc["updated_at"] = time.time()
    atomic_write_json(os.path.join(_evd(ev), "sources.json"), doc)


def _layout_doc(ev):
    p = os.path.join(_evd(ev), "layout.json")
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return None


def _job(ev, kind, params, user):
    S, _ = _mods()
    j = S.submit(cfg.DATA, ev, kind, params, by=user.username)
    return {k: j[k] for k in ("id", "kind", "state", "created")}


# ------------------------------------------------------------------ sources
class SourceIn(BaseModel):
    url: str = Field(..., max_length=2000, description="YouTube/Twitch URL (video, livestream, playlist, channel), "
                                                        "direct .mp4/.m3u8 URL, or a file path under the media/data dir")
    role: str = Field("wide", description="wide | dynamic | alliance-side")
    label: str | None = Field(None, max_length=200)
    primary: bool = False
    coverage: dict | None = None


class SourcePatch(BaseModel):
    role: str | None = None
    primary: bool | None = None
    enabled: bool | None = None
    label: str | None = Field(None, max_length=200)
    coverage: dict | None = None
    sample: dict | None = None
    notes: str | None = Field(None, max_length=2000)


@router.get("/events/{ev}/sources", summary="Video sources of an event (viewer+)")
def list_sources(ev: str = Depends(valid_event), user: User = Depends(require_role("viewer"))):
    S, L = _mods()
    doc = _doc(ev)
    lay = _layout_doc(ev) or {}
    for s in doc.get("sources", []):
        s["has_layout"] = s["id"] in (lay.get("sources") or {})
        s["n_videos"] = len(s.get("videos") or {})
        s["has_frame"] = os.path.exists(os.path.join(_evd(ev), "frames", f"{s['id']}.jpg"))
    return dict(event=ev, sources=doc.get("sources", []), discovered_at=doc.get("discovered_at"),
                roles=list(S.ROLES), kinds=L.KINDS, jobs_inline=S.vision_available(),
                video_config=S.video_config(doc, None, lay), station_pattern=S.station_pattern(doc, lay),
                livestream=S.livestream_url(doc))


@router.post("/events/{ev}/sources/discover", summary="Discover sources from TBA (admin)")
def discover(ev: str = Depends(valid_event), user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    key = tba_key_or_409(db)
    event = tba_call(key, f"/event/{ev}", ttl=600)
    matches = tba_call(key, f"/event/{ev}/matches", ttl=300)
    slim = [dict(key=m["key"], comp_level=m.get("comp_level"), set_number=m.get("set_number"),
                 match_number=m.get("match_number"), videos=m.get("videos") or []) for m in matches or []]
    return _job(ev, "discover", {"_event": {"webcasts": event.get("webcasts") or [], "name": event.get("name")},
                                 "_matches": slim, "full": True}, user)


@router.post("/events/{ev}/sources", status_code=201, summary="Add a pasted source (admin)")
def add_source(body: SourceIn, ev: str = Depends(valid_event), user: User = Depends(require_role("admin")),
               db=Depends(DB.get_db)):
    S, _ = _mods()
    doc = _doc(ev)
    try:
        s = S.add(doc, body.url, body.role, body.label, body.coverage, user.display_name or f"Teammate #{user.id}", body.primary)
    except ValueError as e:
        raise HTTPException(422, str(e))
    _save(ev, doc)
    if s["kind"] == "per_match":                     # playlist / channel: map its titles to matches
        matches = []
        try:
            k = DB.get_setting(db, "tba_key")
            if k: matches = [dict(key=m["key"]) for m in tba_call(k, f"/event/{ev}/matches", ttl=300) or []]
        except HTTPException:
            pass
        s["job"] = _job(ev, "index_playlist", {"source": s["id"], "_matches": matches}, user)
    return s


@router.patch("/events/{ev}/sources/{sid}", summary="Edit a source: role, primary, coverage, ... (admin)")
def patch_source(sid: str, body: SourcePatch, ev: str = Depends(valid_event), user: User = Depends(require_role("admin"))):
    S, _ = _mods()
    doc = _doc(ev)
    try:
        s = S.update(doc, sid, body.model_dump(exclude_none=True), user.display_name or f"Teammate #{user.id}")
    except KeyError:
        raise HTTPException(404, "no such source")
    except ValueError as e:
        raise HTTPException(422, str(e))
    _save(ev, doc)
    lay = _layout_doc(ev)
    if lay and body.primary and sid in (lay.get("sources") or {}):
        lay["processing_source"] = sid
        atomic_write_json(os.path.join(_evd(ev), "layout.json"), lay)
    return s


@router.delete("/events/{ev}/sources/{sid}", status_code=204, summary="Remove a source (admin; its layout is kept)")
def delete_source(sid: str, ev: str = Depends(valid_event), user: User = Depends(require_role("admin"))):
    S, _ = _mods()
    doc = _doc(ev)
    if not S.remove(doc, sid):
        raise HTTPException(404, "no such source")
    _save(ev, doc)


@router.post("/events/{ev}/sources/{sid}/frame", summary="Grab a sample frame (admin; job)")
def grab_frame(sid: str, body: dict = Body(default={}), ev: str = Depends(valid_event),
               user: User = Depends(require_role("admin"))):
    if SRC and not SRC.get(_doc(ev), sid):
        raise HTTPException(404, "no such source")
    p = {"source": sid}
    if isinstance(body, dict):
        if body.get("t") is not None: p["t"] = float(body["t"])
        if body.get("match"): p["match"] = str(body["match"])[:40]
    return _job(ev, "frame", p, user)


def _safe_file(ev, name):
    d = os.path.join(_evd(ev), "frames")
    f = os.path.normpath(os.path.join(d, name))
    if not f.startswith(d + os.sep) or not os.path.exists(f):
        raise HTTPException(404, "no frame yet")
    return FileResponse(f, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/events/{ev}/sources/{sid}/frame.jpg", summary="The last grabbed frame of a source (admin)")
def frame_jpg(sid: str, ev: str = Depends(valid_event), user: User = Depends(require_role("admin"))):
    if not sid.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(404, "no frame yet")
    return _safe_file(ev, f"{sid}.jpg")


# ------------------------------------------------------------------ layout
class Region(BaseModel):
    id: str | None = Field(None, max_length=40)
    kind: str
    label: str | None = Field(None, max_length=120)
    box: list[float] = Field(..., min_length=4, max_length=4)
    light_on_dark: bool | None = None


class LayoutIn(BaseModel):
    W: int = Field(..., gt=0, le=8192)
    H: int = Field(..., gt=0, le=8192)
    regions: list[Region] = Field(default_factory=list, max_length=60)
    frame: dict | None = None


@router.get("/events/{ev}/layout", summary="Layout boxes per source + defaults (viewer+)")
def get_layout(ev: str = Depends(valid_event), user: User = Depends(require_role("viewer"))):
    _, L = _mods()
    return dict(event=ev, layout=_layout_doc(ev), kinds=L.KINDS,
                defaults=dict(W=L.REF_W, H=L.REF_H, wide=L.first2026_regions(False), program=L.first2026_regions(True),
                              note="used by the pipeline when an event has no layout.json (2026 FIRST broadcast)"))


@router.put("/events/{ev}/layout/{sid}", summary="Save the boxes of one source (admin)")
def put_layout(sid: str, body: LayoutIn, ev: str = Depends(valid_event), user: User = Depends(require_role("admin"))):
    S, L = _mods()
    doc = _doc(ev)
    s = S.get(doc, sid)
    if s is None:
        raise HTTPException(404, "no such source")
    regs = []
    for i, r in enumerate(body.regions):
        if r.kind not in L.KINDS:
            raise HTTPException(422, f"unknown region kind {r.kind!r}")
        x0, y0, x1, y1 = r.box
        x0, x1 = sorted((max(0.0, min(body.W, x0)), max(0.0, min(body.W, x1))))
        y0, y1 = sorted((max(0.0, min(body.H, y0)), max(0.0, min(body.H, y1))))
        if x1 - x0 < 2 or y1 - y0 < 2:
            raise HTTPException(422, f"region {i} is empty")
        d = dict(id=r.id or f"r{i}", kind=r.kind, label=r.label or L.KINDS[r.kind], box=[round(v) for v in (x0, y0, x1, y1)])
        if r.light_on_dark is not None: d["light_on_dark"] = r.light_on_dark
        regs.append(d)
    for k in ("station_red", "station_blue", "clock", "field", "overhead"):
        if sum(r["kind"] == k for r in regs) > 1:
            raise HTTPException(422, f"only one {k} box per source")
    lay = _layout_doc(ev) or {"version": 1, "sources": {}}
    lay.setdefault("sources", {})[sid] = dict(W=body.W, H=body.H, role=s.get("role"), regions=regs, frame=body.frame,
                                              updated_at=time.time(), updated_by=user.display_name or f"Teammate #{user.id}")
    if s.get("primary"):
        lay["processing_source"] = sid
    if any(r["kind"].startswith("station_") for r in regs):
        lay["station_source"] = sid
    elif lay.get("station_source") == sid:
        lay.pop("station_source")
    atomic_write_json(os.path.join(_evd(ev), "layout.json"), lay)
    return lay


@router.delete("/events/{ev}/layout/{sid}", status_code=204, summary="Forget the boxes of one source (admin)")
def delete_layout(sid: str, ev: str = Depends(valid_event), user: User = Depends(require_role("admin"))):
    lay = _layout_doc(ev)
    if not lay or sid not in (lay.get("sources") or {}):
        raise HTTPException(404, "no layout for this source")
    lay["sources"].pop(sid)
    for k in ("processing_source", "station_source"):
        if lay.get(k) == sid: lay.pop(k)
    atomic_write_json(os.path.join(_evd(ev), "layout.json"), lay)


@router.post("/events/{ev}/layout/{sid}/suggest", summary="Suggest boxes from the video (admin; job)")
def suggest(sid: str, body: dict = Body(default={}), ev: str = Depends(valid_event),
            user: User = Depends(require_role("admin"))):
    p = {"source": sid}
    if isinstance(body, dict):
        if body.get("other"): p["other"] = str(body["other"])[:80]
        if body.get("match"): p["match"] = str(body["match"])[:40]
    return _job(ev, "suggest", p, user)


# ------------------------------------------------------------------ calibration
def _calib_status(ev, target):
    _, L = _mods()
    name = "camera.json" if target == "overhead" else f"{target}.json"
    out = dict(target=target, file=name, saved=False)
    for d, where in ((_evd(ev), "data"), *[(x, "repo") for x in L.event_dirs(ev)[1:]]):
        p = os.path.join(d, name)
        if os.path.exists(p):
            try:
                c = json.load(open(p))
            except (OSError, ValueError):
                continue
            out.update(saved=True, where=where, W=c.get("W"), H=c.get("H"), crop=c.get("crop"),
                       calib=c.get("calib") if isinstance(c.get("calib"), dict) else None,
                       tag_rms=c.get("tag_rms"), mtime=os.path.getmtime(p))
            break
    w = os.path.join(_evd(ev), "calib_wizard", f"{target}.json")
    if os.path.exists(w):
        try:
            st = json.load(open(w))
            out["candidate"] = dict(fitted_at=st.get("fitted_at"), report={k: v for k, v in (st.get("report") or {}).items()
                                                                            if k != "items"},
                                    has_camera=bool(st.get("camera")), source=st.get("source"))
        except (OSError, ValueError):
            pass
    return out


@router.get("/events/{ev}/calibration", summary="Calibration status per camera (viewer+)")
def calibration(ev: str = Depends(valid_event), user: User = Depends(require_role("viewer"))):
    return dict(event=ev, targets=[_calib_status(ev, t) for t in TARGETS])


def _target(t):
    if t not in TARGETS:
        raise HTTPException(404, "target must be overhead, station_red or station_blue")
    return t


@router.post("/events/{ev}/calibration/{target}/detect", summary="Wizard step 1: frames + AprilTags (admin; job)")
def calib_detect(target: str, body: dict = Body(default={}), ev: str = Depends(valid_event),
                 user: User = Depends(require_role("admin"))):
    p = {"target": _target(target)}
    if isinstance(body, dict):
        for k in ("source", "match"):
            if body.get(k): p[k] = str(body[k])[:80]
    return _job(ev, "detect", p, user)


class FitIn(BaseModel):
    points: dict[str, list[float] | None] = Field(default_factory=dict, description="named field point -> [u, v] (target px)")
    drop_tags: list[int] = Field(default_factory=list)
    line_clicks: dict[str, list[list[float]]] = Field(default_factory=dict,
                                                       description="field line -> [[u, v], ...] (any points along it, >= 2)")
    tags: dict | None = None
    auto_lines: bool = True
    more_tags: bool = True


@router.post("/events/{ev}/calibration/{target}/fit", summary="Wizard step 2: fit tags + clicked points (admin; job)")
def calib_fit(target: str, body: FitIn, ev: str = Depends(valid_event), user: User = Depends(require_role("admin"))):
    _target(target)
    if not os.path.exists(os.path.join(_evd(ev), "calib_wizard", f"{target}.json")):
        raise HTTPException(409, "run detect first")
    pts = {k: v for k, v in body.points.items() if v and len(v) == 2}
    lc = {k: [p for p in v if len(p) == 2][:60] for k, v in body.line_clicks.items()}
    return _job(ev, "fit", dict(target=target, points=pts, line_clicks=lc, drop_tags=body.drop_tags, tags=body.tags,
                                auto_lines=body.auto_lines, more_tags=body.more_tags), user)


@router.get("/events/{ev}/calibration/{target}/frame.jpg", summary="The wizard frame of a camera (admin)")
def calib_frame(target: str, ev: str = Depends(valid_event), user: User = Depends(require_role("admin"))):
    return _safe_file(ev, f"calib_{_target(target)}.jpg")


@router.get("/events/{ev}/calibration/{target}/wizard", summary="Wizard state: tags, last fit (admin)")
def calib_state(target: str, ev: str = Depends(valid_event), user: User = Depends(require_role("admin"))):
    w = os.path.join(_evd(ev), "calib_wizard", f"{_target(target)}.json")
    if not os.path.exists(w):
        raise HTTPException(404, "run detect first")
    st = json.load(open(w))
    st.pop("p", None)
    return st


@router.post("/events/{ev}/calibration/{target}/overlay", summary="Overlay of the saved calibration (admin; job)")
def calib_overlay(target: str, ev: str = Depends(valid_event), user: User = Depends(require_role("admin"))):
    return _job(ev, "overlay", {"target": _target(target)}, user)


@router.post("/events/{ev}/calibration/{target}/accept", summary="Save the fitted camera for the pipeline (admin)")
def calib_accept(target: str, ev: str = Depends(valid_event), user: User = Depends(require_role("admin"))):
    _target(target)
    w = os.path.join(_evd(ev), "calib_wizard", f"{target}.json")
    try:
        st = json.load(open(w))
    except (OSError, ValueError):
        raise HTTPException(409, "run detect + fit first")
    cam = st.get("camera")
    if not cam:
        raise HTTPException(409, "no fitted camera yet: run fit")
    name = "camera.json" if target == "overhead" else f"{target}.json"
    dst = os.path.join(_evd(ev), name)
    if os.path.exists(dst):
        shutil.copy2(dst, f"{dst}.bak-{int(time.time())}")
    cam = dict(cam); cam.setdefault("calib", {})["accepted_by"] = user.display_name or f"Teammate #{user.id}"   # never the username (it can be a credential)
    cam["calib"]["accepted_at"] = time.time()
    atomic_write_json(dst, cam)
    if target == "overhead":
        # the worker starts each match from the last verified pose: a new calibration replaces it
        pl = os.path.join(_evd(ev), "pose_latest.json")
        if os.path.exists(pl):
            os.replace(pl, f"{pl}.bak-{int(time.time())}")
    st["accepted_at"] = time.time()
    atomic_write_json(w, st)
    return _calib_status(ev, target)


# ------------------------------------------------------------------ livestream recorder
LIVE_STALE_S = 60      # the recorder service rewrites the status every ~10 s


@router.get("/events/{ev}/livestream", summary="Livestream recorder status (viewer+)")
def livestream_status(ev: str = Depends(valid_event), user: User = Depends(require_role("viewer"))):
    """What the recorder service (compose service `recorder`, live/liveingest.py) is doing for this event:
    state (recording / waiting / stream_over / idle / error / no_source / disabled / stopped), the source and
    video, recorder + analyser progress (behind_live_s, analyser.behind_chunks x 5 s), the last detected
    match and recent ones (teams, score, flags, clip URL under /media/), disk use, recent errors, and whether
    the stream can be tracked (tracking.ok / note).  Written to DATA/events/<ev>/livestream.json."""
    st = None
    try:
        st = json.load(open(os.path.join(_evd(ev), "livestream.json")))
    except (OSError, ValueError):
        pass
    configured = None
    if SRC is not None:
        try:
            configured = SRC.livestream_url(_doc(ev))
        except Exception:
            configured = None
    if not st:
        return dict(event=ev, state="not_running" if configured else "no_source", configured_url=configured,
                    stale=True, message="no status from the recorder service yet" if configured else
                    "no enabled livestream source (add one on the Video page)")
    age = time.time() - float(st.get("updated") or 0)
    st.update(event=ev, configured_url=configured, age_s=round(age, 1), stale=age > LIVE_STALE_S)
    if st["stale"] and st.get("state") in ("recording", "waiting", "starting"):
        st["message"] = f"recorder service not reporting for {age:.0f}s (is the `recorder` container running?)"
    return st


# ------------------------------------------------------------------ jobs
@router.get("/events/{ev}/jobs/{jid}", summary="Job state / result (admin)")
def job(jid: str, ev: str = Depends(valid_event), user: User = Depends(require_role("admin"))):
    S, _ = _mods()
    j = S.get_job(cfg.DATA, jid)
    if not j or j.get("ev") != ev:
        raise HTTPException(404, "no such job")
    j.pop("params", None)
    if j.get("state") == "queued" and time.time() - j.get("created", 0) > 20 and not S.vision_available():
        j["hint"] = "waiting for the worker's video job runner (sources.job_loop)"
    return j
