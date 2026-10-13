"""Append-only audit log (table audit_log; UPDATE/DELETE are refused by triggers).

Every mutating endpoint calls `record()` inside its transaction with a before/after *state snapshot* of what it
changed, so an admin can see who did what (account, role, IP, network, browser) and revert it: `routers/admin.py`
applies an entry's `before` state back, which is itself recorded (action "<family>.revert", ref = reverted id) and
can be reverted again. Snapshots per family (target):

  picklist  "<ev>/<name>"     {"version": n} | null (no picklist)      -> restore that version / delete
  note      "<note id>"       {"text", "removed_kind", "removed_at", "removed_by"} | null (not created yet)
  override  "<ev>/<match>"    {"entry": {...}}  ({} = no fix for that match)
  settings  "<keys>"          {key: value}       (team_number, season_goal, options, lockdown, passwordless_login, ...)
  events    "<ev>"            {"row": {...}} | null (not followed)

Anything else (auth.*, users.*, http.* from routers without explicit records) is recorded but not revertible.
"""
import json, time

from fastapi import Request

from .deps import User, client_ip, via_public

REVERTIBLE = ("picklist", "note", "override", "settings", "events")


def _j(v):
    return None if v is None else json.dumps(v, sort_keys=True, separators=(",", ":"), default=str)


def record(db, request: Request | None, user: User | None, action: str, target: str | None = None,
           event: str | None = None, before=None, after=None, ref: int | None = None, *, actor: dict | None = None):
    """Append one entry. `actor` ({id, username, display_name, role}) overrides `user` (e.g. failed sign-ins)."""
    a = actor or ({"id": user.id, "username": user.username, "display_name": user.display_name, "role": user.role}
                  if user else {})
    ip = client_ip(request) if request is not None else None
    via = ("public" if via_public(request) else "lan") if request is not None else None
    ua = (request.headers.get("user-agent") or "")[:300] if request is not None else None
    rid = db.execute("INSERT INTO audit_log(at,user_id,username,display_name,role,ip,via,ua,action,event,target,"
                     "before,after,ref) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (time.time(), a.get("id"), a.get("username"), a.get("display_name"), a.get("role"), ip, via, ua,
                      action, event, target, _j(before), _j(after), ref)).lastrowid
    if request is not None:
        request.state.audited = True
    return rid


def family(action: str) -> str:
    return action.split(".", 1)[0]


def revertible(action: str) -> bool:
    return family(action) in REVERTIBLE and action not in ("override.request", "override.reject",
                                                          "override.withdraw", "settings.tba_key")
