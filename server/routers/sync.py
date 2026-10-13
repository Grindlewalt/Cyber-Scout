"""Client sync API (docs/SYNC.md part 1): server info, followed events, per-event file manifests with delta
cursors, many-files-in-one-request bundles (gzip tar, Range-resumable) and the media list.

Logic lives in server/syncindex.py; this file is the HTTP surface. Downloads (manifest, bundle) are reads: the
bundle is a POST only because the path list is long, so it needs the CSRF header like every POST but does not
count against the write budget, is allowed during a lockdown and is not written to the audit log."""
import datetime, os, time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask

from .. import db as DB, syncindex
from ..config import cfg
from ..deps import EVENT_RE, User, check_csrf, load_session, require_role
from .events import event_out

router = APIRouter(tags=["sync"])

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_version = None


def version() -> str:
    """CS_VERSION env, else the VERSION file the api image writes at build time (git short sha, or src-<hash>)."""
    global _version
    if cfg.VERSION:
        return cfg.VERSION
    if _version is None:
        _version = "dev"
        for p in (os.path.join(os.path.dirname(_HERE), "VERSION"), os.path.join(_HERE, "VERSION")):
            try:
                with open(p) as f:
                    v = f.read().strip()[:40]
                if v:
                    _version = v
                    break
            except OSError:
                pass
    return _version


class InfoOut(BaseModel):
    app: str = "cyber-scout"
    api: int = 1
    version: str = Field(description="git short sha of the deployed code (or src-<content hash> / dev)")
    name: str = Field(description="server display name (SERVER_NAME, else Cyber-Scout + team number)")
    time: int = Field(description="server clock, unix seconds")


@router.get("/info", response_model=InfoOut, summary="Server identity for 'Test connection' (no auth; cacheable 10 s)")
def info(response: Response, db=Depends(DB.get_db)):
    name = cfg.SERVER_NAME.strip()[:80]
    if not name:
        n = DB.get_setting(db, "team_number")
        name = f"Cyber-Scout · Team {n}" if n else "Cyber-Scout"
    response.headers["Cache-Control"] = "public, max-age=10"
    return InfoOut(version=version(), name=name, time=int(time.time()))


class Progress(BaseModel):
    tracked: int
    total: int


class SyncEventOut(BaseModel):
    key: str
    name: str | None
    start_date: str | None
    end_date: str | None
    status: str = Field(description="needs_setup (no camera calibration) | upcoming | in_progress | finished")
    progress: Progress
    updated: float | None = Field(None, description="last site build (unix s)")


def _status(e) -> str:
    if e.needs_setup:
        return "needs_setup"
    today = datetime.date.today().isoformat()
    if e.start_date and today < e.start_date:
        return "upcoming"
    if e.end_date and today > e.end_date:
        return "finished"
    if e.n_total and (e.n_done or 0) >= e.n_total and not e.end_date:
        return "finished"
    return "in_progress"


@router.get("/sync/events", response_model=list[SyncEventOut], summary="Followed events to keep in sync (viewer+)")
def sync_events(user: User = Depends(require_role("viewer")), db=Depends(DB.get_db)):
    out = []
    for r in db.execute("SELECT * FROM events ORDER BY start_date, key").fetchall():
        e = event_out(r)
        out.append(SyncEventOut(key=e.key, name=e.name, start_date=e.start_date, end_date=e.end_date,
                                status=_status(e), updated=e.updated,
                                progress=Progress(tracked=e.n_done or 0, total=e.n_total or 0)))
    return out


def _sync_event(ev: str, db) -> str:
    ev = (ev or "").strip().lower()
    if not EVENT_RE.match(ev):
        raise HTTPException(422, "bad event key (expected e.g. 2026wasam)")
    followed = db.execute("SELECT 1 FROM events WHERE key=?", (ev,)).fetchone()
    if not followed and not os.path.isdir(os.path.join(cfg.SITE, "api", ev)):
        raise HTTPException(404, "unknown event (not followed, no data)")
    return ev


class ManifestFile(BaseModel):
    path: str
    size: int
    sha256: str
    mtime: float


class ManifestOut(BaseModel):
    ev: str
    cursor: str = Field(description="opaque; send back as ?since= for the changes after this manifest")
    full: bool = Field(description="true: `files` is everything (no/unknown/expired cursor); delete local files not in it")
    files: list[ManifestFile]
    deleted: list[str]


@router.get("/sync/manifest", response_model=ManifestOut,
            summary="Data files of one event (+ global files), all or changed since a cursor (viewer+)")
def manifest(ev: str = Query(..., max_length=24), since: str | None = Query(None, max_length=64),
             user: User = Depends(require_role("viewer")), db=Depends(DB.get_db)):
    ev = _sync_event(ev, db)
    return JSONResponse(syncindex.index(ev).manifest(since))


def require_reader(request: Request, db=Depends(DB.get_db)) -> User:
    """Signed in (any role) + CSRF header; a download, so no write budget, lockdown or audit entry."""
    u = load_session(db, request)
    if not u:
        raise HTTPException(401, "sign in required")
    check_csrf(request, u)
    request.state.user = u
    request.state.audited = True
    return u


class BundleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ev: str = Field(max_length=24)
    paths: list[str] = Field(min_length=1, max_length=20000,
                             description="manifest paths (api/<ev>/... or global api/<file>); at most 64 MB in total")


@router.post("/sync/bundle", response_class=FileResponse,
             responses={200: {"content": {"application/gzip": {}}, "description": "gzip tar of the files"},
                        206: {"description": "partial content (Range)"}},
             summary="Many files in one request: gzip tar of manifest paths; Range / If-Range (ETag) resumable (viewer+)")
def sync_bundle(body: BundleIn, user: User = Depends(require_reader), db=Depends(DB.get_db)):
    ev = _sync_event(body.ev, db)
    try:
        path, key, n, raw, cached = syncindex.bundle(ev, body.paths)
    except syncindex.BundleError as e:
        raise HTTPException(e.status, e.detail)
    headers = {"Cache-Control": "private, no-store", "X-Bundle-Files": str(n), "X-Bundle-Bytes": str(raw)}
    if key:
        headers["ETag"] = f'"{key}"'
    return FileResponse(path, media_type="application/gzip", headers=headers, filename=f"{ev}-bundle.tar.gz",
                        background=None if cached else BackgroundTask(_rm, path))


def _rm(p):
    try:
        os.remove(p)
    except OSError:
        pass


class MediaItem(BaseModel):
    path: str
    size: int
    sha256: str | None = None
    kind: str = Field(description="proxy | wide | inset | clip")
    match: str | None


@router.get("/sync/media", response_model=list[MediaItem], response_model_exclude_none=False,
            summary="Videos of one event: proxies (small previews), full wide videos, insets, livestream clips (viewer+)")
def sync_media(ev: str = Query(..., max_length=24), user: User = Depends(require_role("viewer")), db=Depends(DB.get_db)):
    ev = _sync_event(ev, db)
    return JSONResponse(syncindex.media(ev))
