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

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import audit, db as DB
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
try:
    import videoplan as VP         # live/videoplan.py (stdlib at import): the per-match video plan
except Exception:                  # pragma: no cover
    VP = None

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
                                                        "direct video URL (.mp4/.m3u8/...: checked with one HEAD "
                                                        "request), or a file path under the media/data dir; any other "
                                                        "web page is refused (422, a sentence for the admin)")
    role: str = Field("wide", description="wide | dynamic | alliance-side")
    label: str | None = Field(None, max_length=200, description="null: a short name from the link")
    primary: bool = False
    coverage: dict | None = None
    station: str | None = Field(None, description="alliance-side only: red | blue (which driver-station camera)")
    live: bool | None = Field(None, description="true: a YouTube video link of a stream that is live / scheduled "
                                                "(recorded like a /live link); channel /live and Twitch channels are live anyway")


class SourcePatch(BaseModel):
    url: str | None = Field(None, max_length=2000, description="replace the link (admin-added sources only; keeps the id, "
                                                               "so its layout boxes and calibration stay)")
    dynamic: bool | None = Field(None, description="track from every camera (dynpose): true / false forces it; an explicit "
                                                   "null = auto (on for a detected program feed). Left out = unchanged")
    live: bool | None = Field(None, description="with url: see SourceIn.live")
    station: str | None = Field(None, description="red | blue | '' (none)")
    dismissed: bool | None = Field(None, description="false: restore a removed TBA source (with enabled: true)")
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
        s["label"] = S.display_label(s)               # an old TBA webcast name gets its channel
        s["has_frame"] = os.path.exists(os.path.join(_evd(ev), "frames", f"{s['id']}.jpg"))
        s["dynamic_on"] = S.dynamic_on(s)
        if s.get("kind") in S.AUTO_KINDS:
            s["auto_setup_ok"] = True
            try:
                a = S.auto_state(_evd(ev), s["id"], cfg.DATA)
            except Exception:
                a = None
            if a: s["auto_setup"] = _auto_brief(a)
            if not a or not (a.get("running") or a.get("status") in ("ok", "no_camera")):
                k, why = S.setup_ready(ev, s)
                s["auto_ready"] = dict(media=k, why=why)
            c = S.stream_calibration(_evd(ev), s["id"])
            s["calibration"] = os.path.basename(c) if c else None
    st = _stream_source(doc)
    return dict(event=ev, sources=doc.get("sources", []), discovered_at=doc.get("discovered_at"),
                stream_source=st["id"] if st else None, stations=list(S.STATIONS),
                roles=list(S.ROLES), kinds=L.KINDS, jobs_inline=S.vision_available(),
                video_config=S.video_config(doc, None, lay), station_pattern=S.station_pattern(doc, lay),
                livestream=S.livestream_url(doc), video_mode=S.video_mode(doc))


# ------------------------------------------------------------------ match video mode
class VideoModeIn(BaseModel):
    video_mode: str = Field(..., description="auto = per-match uploads + livestream | livestream = livestream only "
                                             "(TBA gives the match list; no upload is looked up)")


def _stream_source(doc):
    """The livestream source the recorder records first (liveingest.live_sources order, without dates)."""
    ls = [s for s in doc.get("sources", []) if s.get("kind") == "livestream" and s.get("url") and s.get("enabled", True)]
    ls.sort(key=lambda s: (not (s.get("primary") or s.get("role") == "wide"), s.get("origin") != "admin"))
    return ls[0] if ls else None


def _live_status(ev):
    try:
        st = json.load(open(os.path.join(_evd(ev), "livestream.json")))
    except (OSError, ValueError):
        return None
    st["age_s"] = round(time.time() - float(st.get("updated") or 0), 1)
    return st


def _readiness(ev, doc):
    """Checklist for livestream-only mode: stream source, recorder, layout field box, calibration, TBA matches.
    Each item {key, label, state: ok | warn | bad, detail, tab}."""
    out = []
    add = lambda key, label, state, detail, tab=None: out.append(dict(key=key, label=label, state=state, detail=detail, tab=tab))
    src = _stream_source(doc)
    if src is None:
        add("source", "Livestream source", "bad", "add the event's livestream URL (YouTube / Twitch) below", "sources")
    else:
        S_, _ = _mods()
        feed = S_.dynamic_on(src)                 # a program feed tracked from every camera is a tracked stream too
        tracked = src.get("primary") or src.get("role") == "wide" or feed
        add("source", "Livestream source", "ok" if tracked else "warn",
            (src.get("label") or src.get("url") or src["id"]) + (" · program feed: robots tracked from every camera" if feed
             else "" if tracked else " · set its camera role to wide (or mark it Tracked) so it is the stream that is calibrated"),
            "sources")
    st = _live_status(ev)
    if src is None:
        pass
    elif not st:
        add("recorder", "Recorder", "bad", "no status from the recorder service (is the `recorder` container running?)")
    else:
        stale = st["age_s"] > LIVE_STALE_S
        state = st.get("state")
        if stale and state in ("recording", "waiting", "starting"):
            add("recorder", "Recorder", "bad", f"not reporting for {st['age_s']:.0f} s (is the `recorder` container running?)")
        elif state == "recording":
            b = st.get("behind_live_s")
            add("recorder", "Recorder", "ok", "recording" + (f", {b:.0f} s behind live" if isinstance(b, (int, float)) else ""))
        elif state in ("waiting", "starting", "idle", "stream_over"):
            add("recorder", "Recorder", "warn", st.get("message") or state.replace("_", " "))
        else:
            add("recorder", "Recorder", "bad", st.get("message") or str(state))
    lay = (_layout_doc(ev) or {}).get("sources") or {}
    sid = src["id"] if src else None
    regs = {r.get("kind") for r in (lay.get(sid) or {}).get("regions", [])} if sid else set()
    if sid:
        if regs & {"field", "overhead"}:
            add("layout", "Layout: field box", "ok", "drawn")
        else:
            add("layout", "Layout: field box", "bad", "draw the field box on a frame of the stream", "layout")
        add("clock", "Layout: clock box", "ok" if "clock" in regs else "warn",
            "drawn" if "clock" in regs else "optional: the analyser looks for the FIRST overlay clock at its usual place", "layout")
        cam = None
        for d in _event_dirs(ev):
            if os.path.exists(os.path.join(d, f"camera_{sid}.json")): cam = f"camera_{sid}.json"; break
            p = os.path.join(d, "camera.json")
            if os.path.exists(p):
                try:
                    if (json.load(open(p)) or {}).get("source") == sid: cam = "camera.json"; break
                except (OSError, ValueError):
                    pass
        if cam:
            add("calibration", "Calibration", "ok", f"stream camera calibrated ({cam})")
        else:
            other = os.path.exists(os.path.join(_evd(ev), "camera.json"))
            S, _ = _mods()
            a = S.auto_state(_evd(ev), sid, cfg.DATA) if src.get("kind") in S.AUTO_KINDS else None
            if a and a.get("running"):
                hint = "being set up automatically from the recording"
            elif a and a.get("status") == "ok":
                hint = "an automatic set-up is ready: check it and Accept it on the stream's card"
            elif a and a.get("status") == "no_camera":
                hint = f"automatic set-up: {a.get('message') or 'no fixed camera found'} · calibrate by hand (Field setup)"
            else:
                hint = "calibrate the stream camera (Field setup on its card)"
            add("calibration", "Calibration", "bad", hint + (" · camera.json was made on another video" if other else ""), "calib")
    try:
        sj = json.load(open(os.path.join(cfg.DATA, "site", "api", ev, "event.json")))
        ms = sj.get("matches") or []
        add("tba", "TBA match list", "ok" if ms else "warn",
            f"{len(ms)} match{'es' if len(ms) != 1 else ''} listed" if ms else
            "no matches on TBA yet: matches found on the stream are added from its overlay")
    except (OSError, ValueError):
        add("tba", "TBA match list", "warn", "no site data yet (the worker builds it on its next poll)")
    return out


def _event_dirs(ev):
    """The data dir first (cfg.DATA), then the repo's shipped events/<ev>/ (like _calib_status)."""
    _, L = _mods()
    try:
        return [_evd(ev)] + list(L.event_dirs(ev))[1:]
    except Exception:
        return [_evd(ev)]


OFFSEASON_TYPES = {99: "offseason", 100: "preseason"}       # TBA event_type
_SUGGEST = {}                                                # ev -> (time, (mode, why)); TBA calls are cached too


def _suggest_mode(ev, db):
    """Default for the Video page: livestream only for an offseason / preseason event whose matches have no videos
    on TBA, else uploads + livestream.  (None, why) when TBA can't be asked (no key, TBA down)."""
    c = _SUGGEST.get(ev)
    if c and time.time() - c[0] < 300:
        return c[1]
    try:
        key = DB.get_setting(db, "tba_key")
        if not key:
            return None, "no TBA key"
        e = tba_call(key, f"/event/{ev}/simple", ttl=3600) or {}
        try:
            ms = tba_call(key, f"/event/{ev}/matches", ttl=300) or []
        except HTTPException as x:
            if x.status_code != 404: raise
            ms = []
    except HTTPException as x:
        return None, f"TBA: {x.detail}"
    nv = sum(1 for m in ms if any((v or {}).get("type") == "youtube" for v in m.get("videos") or []))
    off = OFFSEASON_TYPES.get(e.get("event_type"))
    if off and not nv:
        out = ("livestream", f"{off} event and TBA lists no match videos" + ("" if ms else " (no schedule yet)"))
    elif not nv:
        out = ("auto", "TBA lists no match videos yet; official events usually get per-match uploads")
    else:
        out = ("auto", f"TBA links videos for {nv} match{'es' if nv != 1 else ''}")
    _SUGGEST[ev] = (time.time(), out)
    return out


@router.get("/events/{ev}/video-mode", summary="Match video mode + livestream-only readiness (viewer+)")
def get_video_mode(ev: str = Depends(valid_event), user: User = Depends(require_role("viewer")), db=Depends(DB.get_db)):
    """video_mode: auto (per-match uploads, plus the livestream clip when the stream is trackable) or livestream
    (offseason: TBA gives the match list; every match is tracked from the recorded livestream, never an upload).
    checks: what livestream-only mode needs (stream source, recorder, layout field box, calibration, TBA).
    suggested / suggested_why: the default the Video page offers (livestream for an offseason event without
    match videos on TBA); chosen: an admin has set the mode (else the event runs on the default, auto)."""
    S, _ = _mods()
    doc = _doc(ev)
    src = _stream_source(doc)
    sug, why = _suggest_mode(ev, db)
    return dict(event=ev, video_mode=S.video_mode(doc), modes=list(S.VIDEO_MODES), at=doc.get("video_mode_at"),
                by=doc.get("video_mode_by"), chosen=bool(doc.get("video_mode_at")), suggested=sug, suggested_why=why,
                stream_source=src["id"] if src else None, checks=_readiness(ev, doc))


@router.put("/events/{ev}/video-mode", summary="Set the match video mode (admin)")
def put_video_mode(body: VideoModeIn, request: Request, ev: str = Depends(valid_event),
                   user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    S, _ = _mods()
    doc = _doc(ev)
    before = S.video_mode(doc)
    try:
        S.set_video_mode(doc, body.video_mode, user.display_name or f"Teammate #{user.id}")
    except ValueError as e:
        raise HTTPException(422, str(e))
    _save(ev, doc)
    with DB.tx(db):
        audit.record(db, request, user, "sources.video_mode", ev, ev, {"video_mode": before}, {"video_mode": body.video_mode})
    return get_video_mode(ev, user, db)


@router.post("/events/{ev}/sources/discover", summary="Discover sources from TBA (admin)")
def discover(ev: str = Depends(valid_event), user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    key = tba_key_or_409(db)
    event = tba_call(key, f"/event/{ev}", ttl=600)
    matches = tba_call(key, f"/event/{ev}/matches", ttl=300)
    slim = [dict(key=m["key"], comp_level=m.get("comp_level"), set_number=m.get("set_number"),
                 match_number=m.get("match_number"), videos=m.get("videos") or []) for m in matches or []]
    # TBA webcasts become livestream sources (the recorder picks today's); the plan is rebuilt from cached checks
    return _job(ev, "discover", {"_event": {"webcasts": event.get("webcasts") or [], "name": event.get("name")},
                                 "_matches": slim, "full": True}, user)


@router.get("/events/{ev}/sources/webcasts", summary="The event's webcasts listed on TBA, to add on the Video page (admin)")
def tba_webcasts(ev: str = Depends(valid_event), user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    """webcasts: TBA's YouTube / Twitch streams for the event, with `source` = the id of the source that already has
    that link (null: not added yet) and `dismissed`.  No TBA key or TBA unreachable: an empty list and a `note`
    (the page just doesn't offer any), never an error."""
    S, _ = _mods()
    key = DB.get_setting(db, "tba_key")
    if not key:
        return dict(event=ev, webcasts=[], note="no TBA key")
    try:
        e = tba_call(key, f"/event/{ev}", ttl=600) or {}
    except HTTPException as x:
        return dict(event=ev, webcasts=[], note=f"TBA: {x.detail}")
    return dict(event=ev, webcasts=S.webcast_list(e, _doc(ev)))


@router.post("/events/{ev}/sources", status_code=201, summary="Add a pasted source (admin)")
def add_source(body: SourceIn, ev: str = Depends(valid_event), user: User = Depends(require_role("admin")),
               db=Depends(DB.get_db)):
    S, _ = _mods()
    doc = _doc(ev)
    try:
        s = S.add(doc, body.url, body.role, body.label, body.coverage, user.display_name or f"Teammate #{user.id}", body.primary,
                  station=body.station, live=body.live)
    except ValueError as e:
        raise HTTPException(422, str(e))
    _probe_or_422(S, s)
    _save(ev, doc)
    if s["kind"] == "per_match":
        s["job"] = _index_job(ev, s, user, db)
    return s


def _probe_or_422(S, s):
    """A direct http(s) video address: one HEAD request before it is saved, so a dead link or a web page is refused
    with a sentence instead of failing later in a frame grab (YouTube / Twitch / files are not probed)."""
    if s.get("provider") == "http":
        why = S.probe_http(s["url"])
        if why:
            raise HTTPException(422, why)


def _index_job(ev, s, user, db):
    """A playlist / channel source: map its titles to matches (job)."""
    matches = []
    try:
        k = DB.get_setting(db, "tba_key")
        if k: matches = [dict(key=m["key"]) for m in tba_call(k, f"/event/{ev}/matches", ttl=300) or []]
    except HTTPException:
        pass
    return _job(ev, "index_playlist", {"source": s["id"], "_matches": matches}, user)


@router.patch("/events/{ev}/sources/{sid}", summary="Edit a source: link, role, station, primary, coverage, ... (admin)")
def patch_source(sid: str, body: SourcePatch, request: Request, ev: str = Depends(valid_event),
                 user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    S, _ = _mods()
    doc = _doc(ev)
    by = user.display_name or f"Teammate #{user.id}"
    patch = body.model_dump(exclude_none=True)
    url, live = patch.pop("url", None), patch.pop("live", None)
    patch.pop("dynamic", None)
    before = (S.get(doc, sid) or {}).get("dynamic")
    try:
        if url is not None:
            _probe_or_422(S, S.set_url(doc, sid, url, by, live=live))
        if "dynamic" in body.model_fields_set:          # explicit null = back to auto
            S.set_dynamic(doc, sid, body.dynamic, by)
        s = S.update(doc, sid, patch, by)
    except KeyError:
        raise HTTPException(404, "no such source")
    except ValueError as e:
        raise HTTPException(422, str(e))
    _save(ev, doc)
    if "dynamic" in body.model_fields_set and before != body.dynamic:
        with DB.tx(db):
            audit.record(db, request, user, "sources.dynamic", sid, ev, {"dynamic": before}, {"dynamic": body.dynamic})
    s["dynamic_on"] = S.dynamic_on(s)
    if url is not None and s["kind"] == "per_match":
        s["job"] = _index_job(ev, s, user, db)
    lay = _layout_doc(ev)
    if lay and body.primary and sid in (lay.get("sources") or {}):
        lay["processing_source"] = sid
        atomic_write_json(os.path.join(_evd(ev), "layout.json"), lay)
    return s


@router.delete("/events/{ev}/sources/{sid}", status_code=204,
               summary="Remove a source (admin; a TBA one stays listed as dismissed so discovery doesn't re-add it)")
def delete_source(sid: str, ev: str = Depends(valid_event), user: User = Depends(require_role("admin"))):
    """An admin-added source is deleted.  A TBA source (event webcast, upload family) is switched off and marked
    `dismissed`: TBA discovery keeps it off, and the recorder doesn't fall back to TBA's webcasts.  Restore it
    with PATCH {"dismissed": false, "enabled": true}.  Layout boxes are kept either way."""
    S, _ = _mods()
    doc = _doc(ev)
    if not S.discard(doc, sid, user.display_name or f"Teammate #{user.id}"):
        raise HTTPException(404, "no such source")
    _save(ev, doc)


@router.post("/events/{ev}/sources/{sid}/frame", summary="Grab a sample frame (admin; job)")
def grab_frame(sid: str, body: dict = Body(default={}), ev: str = Depends(valid_event),
               user: User = Depends(require_role("admin"))):
    if SRC and not SRC.find_source(_evd(ev), sid):
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


# ------------------------------------------------------------------ automatic set-up of a stream (live/sources.py)
AUTO_BRIEF = ("status", "at", "message", "media", "media_kind", "program_feed", "feed", "fixed", "scoreboard", "clock",
              "field", "graphics", "vs_saved", "accepted_at", "accepted_by", "accepted_file", "running", "job", "W", "H",
              "seconds", "auto", "last_error")


def _auto_brief(a):
    out = {k: a.get(k) for k in AUTO_BRIEF if k in a}
    j = out.get("job")
    if isinstance(j, dict):
        out["job"] = {k: j.get(k) for k in ("id", "state", "created", "started", "error", "auto")}
    out["has_overlay"] = a.get("status") in ("ok", "no_camera")
    return out


def _auto_source(ev, sid):
    S, _ = _mods()
    s = S.get(_doc(ev), sid)
    if s is None:
        raise HTTPException(404, "no such source")
    if s.get("kind") not in S.AUTO_KINDS:
        raise HTTPException(422, "automatic set-up works on live streams (recorded) and files: use Auto-calibrate")
    return s


@router.get("/events/{ev}/sources/{sid}/auto-setup", summary="Automatic set-up of a stream: candidate + state (admin)")
def auto_setup_state(sid: str, ev: str = Depends(valid_event), user: User = Depends(require_role("admin"))):
    """The latest automatic set-up of a livestream / file source (dynpose.auto_setup on its recording, run by the
    worker once the stream is recorded, or by POST): status ok (a fixed wide camera was found and fitted) |
    no_camera (message says why: calibrate by hand) | error; program_feed (cuts / several cameras); the fixed camera's
    fit; scoreboard layout + clock box; field box; vs_saved (px difference to the saved calibration); accepted_at;
    running + job.  null when it never ran."""
    S, _ = _mods()
    _auto_source(ev, sid)
    a = S.auto_state(_evd(ev), sid, cfg.DATA)
    kind, why = S.setup_ready(ev, S.get(_doc(ev), sid))
    return dict(event=ev, source=sid, auto_setup=_auto_brief(a) if a else None, ready=dict(media=kind, why=why))


@router.post("/events/{ev}/sources/{sid}/auto-setup", summary="Run the automatic set-up of a stream now (admin; job)")
def auto_setup_run(sid: str, ev: str = Depends(valid_event), user: User = Depends(require_role("admin"))):
    """Queues dynpose.auto_setup on the stream's newest recorded match clip (else its last few recorded minutes):
    minutes of CPU, so it runs in the worker's slow-job thread (sources.slow_job_loop), never here."""
    S, _ = _mods()
    s = _auto_source(ev, sid)
    a = S.auto_state(_evd(ev), sid, cfg.DATA)
    if a and a.get("running"):
        raise HTTPException(409, "an automatic set-up of this stream is already running")
    kind, why = S.setup_ready(ev, s)
    if kind is None:
        raise HTTPException(409, f"{why}: the stream sets itself up once the recorder has a few minutes of it")
    j = S.submit_auto_setup(cfg.DATA, ev, sid, by=user.display_name or f"Teammate #{user.id}", inline=False)
    return {k: j[k] for k in ("id", "kind", "state", "created")}


@router.get("/events/{ev}/sources/{sid}/auto-setup/overlay.jpg", summary="The automatic set-up's proof image (admin)")
def auto_setup_overlay(sid: str, ev: str = Depends(valid_event), user: User = Depends(require_role("admin"))):
    S, _ = _mods()
    try:
        p = os.path.join(S.auto_dir(_evd(ev), sid), "overlay.jpg")
    except ValueError:
        raise HTTPException(404, "no such source")
    if not os.path.exists(p):
        raise HTTPException(404, "no automatic set-up yet")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/events/{ev}/sources/{sid}/auto-setup/accept", summary="Accept the automatic set-up: camera + layout + view (admin)")
def auto_setup_accept(sid: str, request: Request, ev: str = Depends(valid_event), user: User = Depends(require_role("admin")),
                      db=Depends(DB.get_db)):
    """Like the wizard's accept: the candidate camera -> camera.json (with "source", when camera.json is free or
    this stream's) or camera_<sid>.json (old file kept as .bak-<ts>), its graphics / clock / field boxes -> this source
    in layout.json (station / inset boxes kept), its view -> view_<sid>.png (a program feed is tracked on the fixed
    view only while it is on air), the carried pose reset.  The stream is then trackable."""
    S, _ = _mods()
    _auto_source(ev, sid)
    a = S.auto_state(_evd(ev), sid, cfg.DATA) or {}
    if a.get("running"):
        raise HTTPException(409, "an automatic set-up of this stream is running: wait for its result")
    if a.get("status") != "ok":
        raise HTTPException(409, a.get("message") or "no automatic set-up with a camera to accept: calibrate by hand")
    by = user.display_name or f"Teammate #{user.id}"
    before = S.stream_calibration(_evd(ev), sid)
    try:
        out = S.accept_auto(_evd(ev), sid, by)
    except ValueError as e:
        raise HTTPException(409, str(e))
    with DB.tx(db):
        audit.record(db, request, user, "sources.auto_setup_accept", sid, ev,
                     {"camera": os.path.basename(before) if before else None}, out)
    return dict(out, auto_setup=_auto_brief(S.auto_state(_evd(ev), sid, cfg.DATA) or {}))


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
    s = S.find_source(_evd(ev), sid, doc)            # a source, or a detected video type group of the plan
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
    if s.get("primary") or (s.get("origin") == "auto" and s.get("type") == "wide" and not lay.get("processing_source")):
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
    out["has_frame"] = os.path.exists(os.path.join(_evd(ev), "frames", f"calib_{target}.jpg"))   # the wizard's frame
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
    if target == "overhead" and st.get("source"):
        # the accepted camera's view (the wizard's median frame) -> view_<source>.png: a program feed is tracked only
        # while this view is on air (liveingest.stream_view_ref); the wizard's own plate changes with every Detect
        plate = os.path.join(_evd(ev), "frames", "calib_overhead_plate.png")
        if os.path.exists(plate):
            shutil.copy2(plate, os.path.join(_evd(ev), f"view_{st['source']}.png"))
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


# ------------------------------------------------------------------ per-match video plan (live/videoplan.py)
def _vp():
    if VP is None:
        raise HTTPException(503, "the video plan is not installed in this API image (needs live/videoplan.py)")
    return VP


def _slim_matches(ev, db):
    key = tba_key_or_409(db)
    matches = tba_call(key, f"/event/{ev}/matches", ttl=300) or []
    return [dict(key=m["key"], comp_level=m.get("comp_level"), set_number=m.get("set_number"),
                 match_number=m.get("match_number"), videos=m.get("videos") or [],
                 alliances={a: {"score": ((m.get("alliances") or {}).get(a) or {}).get("score")} for a in ("red", "blue")})
            for m in matches]


def _plan_matches(plan):
    """The match list a plan was built from (TBA-linked videos only), to rebuild it offline after an override."""
    out = []
    for k, e in ((plan or {}).get("matches") or {}).items():
        out.append(dict(key=k, played=bool(e.get("played")),
                        videos=[dict(type="youtube", key=c["id"]) for c in e.get("candidates") or []
                                if "tba" in (c.get("via") or [])]))
    return out


@router.get("/events/{ev}/video-plan", summary="Per-match video plan: candidates, detected types, choices (viewer+)")
def video_plan(ev: str = Depends(valid_event), user: User = Depends(require_role("viewer"))):
    """Which upload the tracker uses for every match (live/videoplan.py): candidates with their detected type
    (wide / broadcast / stations), duplicates, the choice and why, plus the admin overrides.  Built by the worker
    every poll and by POST .../video-plan/check; null until the first build."""
    V = _vp()
    plan = V.load_plan(_evd(ev))
    doc = _doc(ev)
    lay = _layout_doc(ev) or {}
    fam = [dict(id=s["id"], label=s.get("label"), kind=s.get("kind"), provider=s.get("provider"), url=s.get("url"),
                channel=s.get("channel"), title_pattern=s.get("title_pattern"), example_title=s.get("example_title"),
                enabled=s.get("enabled", True), origin=s.get("origin"), n_videos=len(s.get("videos") or {}),
                role=s.get("role"), webcast=s.get("webcast")) for s in doc.get("sources", [])]
    groups = []
    for g in ((plan or {}).get("groups") or {}).values():
        groups.append(dict(g, videos=None, has_layout=g["id"] in (lay.get("sources") or {})))
    return dict(event=ev, plan=plan, overrides=doc.get("match_overrides") or {}, sources=fam, groups=groups,
                discovered_at=doc.get("discovered_at"), jobs_inline=bool(SRC and SRC.vision_available()))


class PlanOverride(BaseModel):
    pin: str | None = Field(None, max_length=200, description="video id / URL the tracker must use for this match")
    pin_stations: str | None = Field(None, max_length=200, description="video id / URL for the station cameras")
    exclude: list[str] = Field(default_factory=list, max_length=40, description="video ids never used for this match")
    add: list[str] = Field(default_factory=list, max_length=10, description="extra video ids / URLs for this match")


def _vid(v):
    """A YouTube id or URL -> the 11-char id, else 422."""
    if v is None or v == "":
        return None
    v = str(v).strip()
    if SRC and SRC._YT_ID.match(v):
        return v
    try:
        c = SRC.classify_url(v) if SRC else {}
    except ValueError as e:
        raise HTTPException(422, f"not a YouTube video: {e}")
    if c.get("provider") != "youtube" or not c.get("video"):
        raise HTTPException(422, "only YouTube videos can be pinned")
    return c["video"]


@router.put("/events/{ev}/video-plan/matches/{mk}", summary="Pin / exclude videos for one match (admin)")
def plan_override(mk: str, body: PlanOverride, ev: str = Depends(valid_event), user: User = Depends(require_role("admin"))):
    V = _vp()
    if not mk.startswith(ev + "_") or not SRC._MATCH_KEY.match(mk):
        raise HTTPException(404, "no such match")
    ov = dict(pin=_vid(body.pin), pin_stations=_vid(body.pin_stations),
              exclude=sorted({_vid(x) for x in body.exclude if x}), add=sorted({_vid(x) for x in body.add if x}))
    ov = {k: v for k, v in ov.items() if v}
    doc = _doc(ev)
    mo = doc.setdefault("match_overrides", {})
    if ov:
        ov.update(by=user.display_name or f"Teammate #{user.id}", at=time.time())
        mo[mk] = ov
    else:
        mo.pop(mk, None)
    _save(ev, doc)
    plan = V.load_plan(_evd(ev))
    if plan:                                          # rebuild from the cached checks (no video work here)
        ms = _plan_matches(plan)
        if mk not in {m["key"] for m in ms}: ms.append(dict(key=mk, videos=[]))
        plan = V.save_plan(_evd(ev), V.build_plan(ev, _evd(ev), ms, doc, V.load_cache(cfg.DATA),
                                                  live=V.live_clips(ev)))
    return dict(match=mk, override=mo.get(mk), entry=((plan or {}).get("matches") or {}).get(mk))


@router.post("/events/{ev}/video-plan/check", summary="Check the event's videos and rebuild the plan (admin; job)")
def plan_check(body: dict = Body(default={}), ev: str = Depends(valid_event), user: User = Depends(require_role("admin")),
               db=Depends(DB.get_db)):
    _vp()
    p = {"_matches": _slim_matches(ev, db)}
    if isinstance(body, dict) and isinstance(body.get("only"), list):
        p["only"] = [_vid(x) for x in body["only"][:40] if x]
    return _job(ev, "plan", p, user)


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
