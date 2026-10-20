"""Team settings (team number, season goal, worker options), the TBA key, and TBA lookups for the wizard.

The TBA key is validated against TBA, stored server-side, and never returned (only `tba_key_set` + last 4 chars)."""
import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .. import audit, db as DB, tba, textrules, workerconf
from ..deps import User, require_role

router = APIRouter(tags=["settings"])


class Options(BaseModel):
    """Worker options; null = use the worker's default / its .env value."""
    model_config = ConfigDict(extra="forbid")
    poll_seconds: int | None = Field(None, ge=30, le=3600, description="seconds between TBA polls")
    gpu_device: int | None = Field(None, ge=0, le=15, description="CUDA device index inside the worker container")
    cpu_only: bool | None = Field(None, description="force CPU inference (no GPU)")
    hubcam: bool | None = Field(None, description="optional driver-station camera pass (~1-2 min/match extra)")


class SettingsOut(BaseModel):
    team_number: int | None
    team_name: str | None
    season_goal: str | None
    tba_key_set: bool
    tba_key_hint: str | None = Field(None, description="admin only: '••••abcd'")
    setup_complete: bool = Field(False, description="an admin finished the setup wizard (imported .env values don't count)")
    options: Options | None = Field(None, description="admin only")


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team_number: int | None = Field(None, ge=1, le=99999)
    season_goal: str | None = Field(None, max_length=40, description='e.g. "DCMP", "Worlds", "Win event" (plain text)')
    options: Options | None = None
    setup_complete: bool | None = None

    @field_validator("season_goal")
    @classmethod
    def check_goal(cls, v):
        return None if v is None else (textrules.label(v, 40, "season goal") or None)


class KeyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=20, max_length=128, pattern=r"^\s*[A-Za-z0-9]+\s*$")


# settings an admin can revert from the audit log (settings.* entries); never the TBA key
REVERTIBLE_KEYS = ("team_number", "team_name", "season_goal", "setup_complete", "options",
                   "lockdown", "passwordless_login", "scout_overrides")


def settings_snap(db, keys):
    return {k: DB.get_setting(db, k) for k in keys}


def apply_settings_state(db, request, user, state: dict, action, ref=None):
    """Set settings back to an audit snapshot and record it. Call inside a transaction."""
    keys = [k for k in state if k in REVERTIBLE_KEYS]
    before = settings_snap(db, keys)
    for k in keys:
        DB.set_setting(db, k, state[k])
    workerconf.write(db)
    return audit.record(db, request, user, action, ",".join(keys), None, before, settings_snap(db, keys), ref)


def settings_out(db, user: User) -> SettingsOut:
    key = DB.get_setting(db, "tba_key")
    admin = user.can("admin")
    return SettingsOut(team_number=DB.get_setting(db, "team_number"), team_name=DB.get_setting(db, "team_name"),
                       season_goal=DB.get_setting(db, "season_goal"), tba_key_set=bool(key),
                       tba_key_hint=("••••" + key[-4:]) if key and admin else None,
                       setup_complete=bool(DB.get_setting(db, "setup_complete", False)),
                       options=Options(**(DB.get_setting(db, "options", {}) or {})) if admin else None)


def tba_key_or_409(db) -> str:
    k = DB.get_setting(db, "tba_key")
    if not k:
        raise HTTPException(409, "set the TBA key first")
    return k


def tba_call(key, path, ttl=600, not_found: str | None = None):
    """TBA GET with the app's errors; not_found = the sentence for a 404 (what isn't there and how to fix it)."""
    try:
        return tba.get(key, path, ttl)
    except tba.TBAError as e:
        if e.status == 401:
            raise HTTPException(409, "the stored TBA key was rejected by TBA; set a new one")
        if e.status == 404:
            raise HTTPException(404, not_found or "The Blue Alliance has no such team or event")
        raise HTTPException(502, str(e))


@router.get("/settings", response_model=SettingsOut, summary="Team settings (viewer+; secrets omitted)")
def get_settings(user: User = Depends(require_role("viewer")), db=Depends(DB.get_db)):
    return settings_out(db, user)


@router.patch("/settings", response_model=SettingsOut, summary="Update team number / season goal / options (admin)")
def patch_settings(body: SettingsPatch, request: Request, user: User = Depends(require_role("admin")),
                   db=Depends(DB.get_db)):
    fs = body.model_fields_set
    team_name = None
    if "team_number" in fs and body.team_number:
        key = DB.get_setting(db, "tba_key")
        if key:     # look the team up so the UI can show its name (and catch typos)
            t = tba_call(key, f"/team/frc{body.team_number}/simple", ttl=86400,
                         not_found=f"Team {body.team_number} isn't on The Blue Alliance: check the number")
            team_name = t.get("nickname")
    keys = [k for k in ("team_number", "team_name", "season_goal", "setup_complete", "options")
            if k in fs or (k == "team_name" and "team_number" in fs)]
    with DB.tx(db):
        before = settings_snap(db, keys)
        if "team_number" in fs:
            DB.set_setting(db, "team_number", body.team_number)
            DB.set_setting(db, "team_name", team_name)
        if "season_goal" in fs:
            DB.set_setting(db, "season_goal", (body.season_goal or "").strip() or None)
        if "setup_complete" in fs:
            DB.set_setting(db, "setup_complete", bool(body.setup_complete))
        if "options" in fs:
            cur = DB.get_setting(db, "options", {}) or {}
            cur.update(body.options.model_dump(exclude_unset=True) if body.options else {})
            DB.set_setting(db, "options", cur)
        workerconf.write(db)
        if keys:
            audit.record(db, request, user, "settings.update", ",".join(keys), None, before, settings_snap(db, keys))
    return settings_out(db, user)


@router.put("/settings/tba-key", response_model=SettingsOut, summary="Validate against TBA and store the key (admin)")
def put_tba_key(body: KeyIn, request: Request, user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    key = body.key.strip()
    try:
        tba.validate_key(key)
    except tba.TBAError as e:
        if e.status in (401, 403):
            raise HTTPException(422, "The Blue Alliance rejected this key (thebluealliance.com/account -> Read API Keys)")
        raise HTTPException(502, "couldn't reach The Blue Alliance to check the key; not saved")
    with DB.tx(db):
        old = DB.get_setting(db, "tba_key")
        DB.set_setting(db, "tba_key", key)
        workerconf.write(db)
        audit.record(db, request, user, "settings.tba_key", "tba_key", None,
                     {"set": bool(old), "hint": ("••••" + old[-4:]) if old else None}, {"set": True, "hint": "••••" + key[-4:]})
    return settings_out(db, user)


@router.delete("/settings/tba-key", response_model=SettingsOut, summary="Remove the stored TBA key (admin)")
def delete_tba_key(request: Request, user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    with DB.tx(db):
        old = DB.get_setting(db, "tba_key")
        DB.set_setting(db, "tba_key", None)
        workerconf.write(db)
        audit.record(db, request, user, "settings.tba_key", "tba_key", None,
                     {"set": bool(old), "hint": ("••••" + old[-4:]) if old else None}, {"set": False, "hint": None})
    return settings_out(db, user)


# ------------------------------------------------------------------ TBA lookups (key stays on the server)
def _ev(e):
    d = e.get("district") or {}
    return {"key": e["key"], "name": e.get("name"), "event_type": e.get("event_type"),
            "start_date": e.get("start_date"), "end_date": e.get("end_date"), "city": e.get("city"),
            "state_prov": e.get("state_prov"), "country": e.get("country"), "district": d.get("abbreviation"),
            "year": e.get("year")}


@router.get("/tba/team/{number}", summary="Look up a team on TBA (admin)")
def tba_team(number: int, user: User = Depends(require_role("admin")), db=Depends(DB.get_db)):
    if not 1 <= number <= 99999:
        raise HTTPException(422, "bad team number")
    t = tba_call(tba_key_or_409(db), f"/team/frc{number}/simple", ttl=86400,
                 not_found=f"Team {number} isn't on The Blue Alliance: check the number")
    return {k: t.get(k) for k in ("key", "team_number", "nickname", "name", "city", "state_prov", "country")}


@router.get("/tba/events", summary="Events for a season: your team's first, then all (admin)")
def tba_events(year: int | None = Query(None, ge=1992, le=2100), user: User = Depends(require_role("admin")),
               db=Depends(DB.get_db)):
    key = tba_key_or_409(db)
    year = year or datetime.date.today().year
    allev = sorted((_ev(e) for e in tba_call(key, f"/events/{year}/simple")), key=lambda e: (e["start_date"] or "", e["key"]))
    team = DB.get_setting(db, "team_number")
    mine = []
    if team:
        try:
            mine = sorted((_ev(e) for e in tba.get(key, f"/team/frc{team}/events/{year}/simple")),
                          key=lambda e: (e["start_date"] or "", e["key"]))
        except tba.TBAError:
            mine = []
    return {"year": year, "team_number": team, "team_events": mine, "events": allev}
