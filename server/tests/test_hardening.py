"""Troll-proofing: audit log + revert, picklist history, soft-deleted notes, identity-fix review, write limits,
plain-text / display-name validation, passwordless sign-in only on the team network, lockdown, sign-everyone-out."""
import json

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from server import db as DB
from server.deps import require_role
from server.security import write_limiter

from .conftest import LAN, C, new_client

EV = "2026wasam"
TUNNEL = {"X-Real-IP": "203.0.113.7", "X-Forwarded-For": "203.0.113.7", "Cf-Connecting-Ip": "203.0.113.7",
          "Cf-Ray": "8f00000000000000-SEA", "X-Forwarded-Proto": "https"}


def public_client(app, headers=TUNNEL):
    """A browser on the internet coming in through the Cloudflare tunnel (cloudflared -> docker gateway -> nginx)."""
    return C(TestClient(app, base_url="https://testserver", client=("172.25.0.1", 50000), headers=headers))


def uid_of(admin, username):
    return [u for u in admin.get("/users").json() if u["username"] == username][0]["id"]


def audit(admin, **q):
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    r = admin.get("/audit" + ("?" + qs if qs else ""))
    assert r.status_code == 200, r.text
    return r.json()["entries"]


# ------------------------------------------------------------------ audit log
def test_audit_records_every_change_with_who_and_where(team):
    s, admin = team["strategist"], team["admin"]
    assert s.put(f"/events/{EV}/picklists/main", {"data": {"stars": ["frc4911"]}}).status_code == 200
    e = audit(admin, action="picklist")[0]
    assert e["action"] == "picklist.save" and e["target"] == f"{EV}/main" and e["event"] == EV
    assert e["user"]["username"] == "strategist1" and e["user"]["role"] == "strategist"
    assert e["ip"] == LAN[0] and e["via"] == "lan" and e["ua"] and e["before"] is None and e["after"] == {"version": 1}
    assert e["revertible"] and e["reverted_by"] is None
    # exactly one record per change (no duplicate from the fallback logger)
    assert len([x for x in audit(admin) if x["target"] and "picklists" in x["target"]]) == 0
    # admins only
    for role in ("viewer", "scout", "strategist"):
        assert team[role].get("/audit").status_code == 403
        assert team[role].post("/audit/1/revert").status_code == 403


def test_audit_is_append_only(team):
    team["scout"].post(f"/events/{EV}/notes", {"text": "x"})
    c = DB.connect()
    with pytest.raises(Exception):
        c.execute("UPDATE audit_log SET action='x'")
    with pytest.raises(Exception):
        c.execute("DELETE FROM audit_log")


def test_audit_never_holds_secrets(team):
    admin = team["admin"]
    uid = uid_of(admin, "scout1")
    admin.patch(f"/users/{uid}", {"password": "new secret pw"})
    admin.put("/settings/tba-key", {"key": "k" * 60})
    blob = json.dumps(audit(admin, limit=500))
    assert "new secret pw" not in blob and "argon2" not in blob and "k" * 60 not in blob
    e = audit(admin, action="users.update")[0]
    assert e["after"]["password_reset"] is True and e["before"]["role"] == "scout"


def test_failed_logins_are_logged_without_revealing_users(team, app):
    c = new_client(app)
    a = c.login("strategist1", "wrong!!")
    b = c.login("nobody-here", "wrong!!")
    assert a.status_code == b.status_code == 401 and a.json() == b.json()
    fails = audit(team["admin"], action="auth.login_failed")
    assert {f["after"]["known_user"] for f in fails} == {True, False}


def test_fallback_audit_for_routes_without_records(team, app):
    @app.post("/app/v1/_test_write")
    def _w(user=Depends(require_role("scout"))):
        return {"ok": True}
    assert team["scout"].post("/_test_write").status_code == 200
    e = audit(team["admin"], action="http")[0]
    assert e["action"] == "http.post" and e["target"] == "/app/v1/_test_write" and e["user"]["username"] == "scout1"


# ------------------------------------------------------------------ picklists: history, restore, revert
def test_picklist_history_survives_delete_and_restores(team):
    s, v, admin = team["strategist"], team["viewer"], team["admin"]
    P = f"/events/{EV}/picklists/main"
    for stars in (["frc1"], ["frc1", "frc2"], ["frc3"]):
        assert s.put(P, {"data": {"stars": stars}}).status_code == 200
    hist = v.get(P + "/versions").json()
    assert [h["version"] for h in hist] == [3, 2, 1] and hist[0]["current"] and "username" not in hist[0]["updated_by"]
    assert v.get(P + "/versions/1").json()["data"] == {"stars": ["frc1"]}
    assert s.delete(P).status_code == 204 and v.get(P).status_code == 404
    assert [h["version"] for h in v.get(P + "/versions").json()] == [3, 2, 1]     # history kept
    assert v.post(P + "/versions/2/restore").status_code == 403                  # viewers can't
    r = s.post(P + "/versions/2/restore")
    assert r.status_code == 200 and r.json()["version"] == 4 and r.json()["data"] == {"stars": ["frc1", "frc2"]}
    assert v.get(P + "/versions").json()[0]["note"] == "restored v2"
    assert s.post(P + "/versions/99/restore").status_code == 404
    # admin reverts the delete straight from the log
    d = audit(admin, action="picklist.delete")[0]
    assert d["before"] == {"version": 3}
    assert admin.post(f"/audit/{d['id']}/revert").status_code == 200
    assert v.get(P).json()["data"] == {"stars": ["frc3"]}
    assert admin.post(f"/audit/{d['id']}/revert").status_code == 409             # only once


def test_revert_of_a_revert(team):
    s, admin = team["strategist"], team["admin"]
    P = f"/events/{EV}/picklists/main"
    s.put(P, {"data": {"stars": ["frc1"]}})
    s.put(P, {"data": {"stars": ["frc666"]}})
    e = audit(admin, action="picklist.save")[0]
    rv = admin.post(f"/audit/{e['id']}/revert").json()["entry"]
    assert s.get(P).json()["data"] == {"stars": ["frc1"]}
    assert admin.post(f"/audit/{rv}/revert").status_code == 200
    assert s.get(P).json()["data"] == {"stars": ["frc666"]}


def test_revert_everything_a_leaked_login_did(team):
    """A strategist login leaks: the troll wipes the picklist, hides notes, and writes junk. One call undoes it."""
    s, sc, admin = team["strategist"], team["scout"], team["admin"]
    P = f"/events/{EV}/picklists/main"
    admin.put(P, {"data": {"stars": ["frc4911", "frc254"], "dnp": ["frc1"]}})
    good = sc.post(f"/events/{EV}/notes", {"team": "frc254", "text": "great climber"}).json()["id"]
    import time
    t0 = time.time()
    s.put(P, {"data": {}})
    s.put(P, {"data": {"dnp": ["frc4911"]}})
    s.delete(f"/notes/{good}")
    junk = s.post(f"/events/{EV}/notes", {"text": "lol"}).json()["id"]
    r = admin.post("/audit/revert-user", {"user_id": uid_of(admin, "strategist1"), "since": t0})
    assert r.status_code == 200 and len(r.json()["reverted"]) == 4
    assert s.get(P).json()["data"] == {"stars": ["frc4911", "frc254"], "dnp": ["frc1"]}
    texts = [n["text"] for n in sc.get(f"/events/{EV}/notes").json()]
    assert "great climber" in texts and "lol" not in texts and junk


# ------------------------------------------------------------------ notes: soft delete, hide, restore, plain text
def test_notes_soft_delete_hide_restore(team, app):
    sc, s, admin, v = team["scout"], team["strategist"], team["admin"], team["viewer"]
    nid = sc.post(f"/events/{EV}/notes", {"team": "frc4911", "text": "fast cycles"}).json()["id"]
    assert sc.delete(f"/notes/{nid}").status_code == 204
    assert v.get(f"/events/{EV}/notes").json() == []
    assert sc.get(f"/events/{EV}/notes?include_removed=true").status_code == 403
    gone = s.get(f"/events/{EV}/notes?include_removed=true").json()[0]
    assert gone["removed"] == "deleted" and gone["text"] == "fast cycles"
    assert sc.post(f"/notes/{nid}/restore").status_code == 200                     # author undoes own delete
    # a strategist hides it: the author can't un-hide
    assert s.delete(f"/notes/{nid}").status_code == 204
    assert s.get(f"/events/{EV}/notes?include_removed=true").json()[0]["removed"] == "hidden"
    assert sc.post(f"/notes/{nid}/restore").status_code == 403
    assert s.post(f"/notes/{nid}/restore").status_code == 200
    # edits by someone else are visible as such
    s.patch(f"/notes/{nid}", {"text": "fast cycles, weak defense"})
    n = v.get(f"/events/{EV}/notes").json()[0]
    assert n["edited_by"]["id"] == s.get("/session").json()["user"]["id"] and "username" not in n["author"]
    # admin reverts the edit
    e = audit(admin, action="note.edit")[0]
    admin.post(f"/audit/{e['id']}/revert")
    assert v.get(f"/events/{EV}/notes").json()[0]["text"] == "fast cycles"
    # still in the DB (nothing is hard-deleted)
    assert DB.connect().execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1


XSS = ["<img src=x onerror=alert(1)>", "<script>alert(1)</script>", "<svg/onload=alert(1)>", "</textarea><b>",
       "&lt;script&gt;", "&#60;img&#62;", "click javascript:alert(1)", "see data:text/html,hi", "<!-- x -->",
       "ad​min says hi", "evil ‮gnp.exe", "bell\x07", "nul\x00"]


@pytest.mark.parametrize("payload", XSS)
def test_notes_refuse_markup_and_invisible_chars(team, payload):
    sc = team["scout"]
    assert sc.post(f"/events/{EV}/notes", {"text": payload}).status_code == 422, payload
    nid = sc.post(f"/events/{EV}/notes", {"text": "ok"}).json()["id"]
    assert sc.patch(f"/notes/{nid}", {"text": payload}).status_code == 422, payload


def test_notes_plain_text_kept(team):
    sc = team["scout"]
    for t in ("2 < 3 and 5 > 4", "<3 this robot", "scored 5/6 @ 3:20 -- ok!", "line1\nline2", "Zoë's bot"):
        r = sc.post(f"/events/{EV}/notes", {"text": t})
        assert r.status_code == 201 and r.json()["text"] == t, t
    assert sc.post(f"/events/{EV}/notes", {"text": "x\n" * 80}).status_code == 422          # too many lines
    assert sc.post(f"/events/{EV}/notes", {"text": "x", "html": "<b>"}).status_code == 422  # unknown fields


# ------------------------------------------------------------------ identity fixes: review queue
def test_scout_identity_fix_waits_for_approval(team, data):
    sc, s, v, admin = team["scout"], team["strategist"], team["viewer"], team["admin"]
    p = data / "events" / EV / "overrides.json"
    r = sc.put(f"/events/{EV}/overrides/{EV}_qm3", {"red": {"0": "frc1983"}})
    assert r.status_code == 202 and r.json()["status"] == "pending" and not p.exists()   # published data untouched
    o = v.get(f"/events/{EV}/overrides").json()
    assert o["mode"] == "review" and len(o["pending"]) == 1 and o["overrides"] == {}
    req = o["pending"][0]
    assert req["data"] == {"red": {"0": "frc1983"}} and "username" not in req["by"]
    assert sc.post(f"/events/{EV}/override-requests/{req['id']}/approve").status_code == 403
    assert s.post(f"/events/{EV}/override-requests/{req['id']}/approve").status_code == 200
    assert json.loads(p.read_text()) == {f"{EV}_qm3": {"red": {"0": "frc1983"}}}
    assert s.post(f"/events/{EV}/override-requests/{req['id']}/approve").status_code == 409
    log = v.get(f"/events/{EV}/overrides").json()["log"][0]
    assert log["requested_by"]["id"] == req["by"]["id"] and log["by"]["id"] != req["by"]["id"]
    # a wrong approved fix is one click to undo for an admin
    e = audit(admin, action="override.approve")[0]
    assert e["before"] == {"entry": {}} and admin.post(f"/audit/{e['id']}/revert").status_code == 200
    assert json.loads(p.read_text()) == {}
    # a scout's clear request is queued too; the scout can withdraw it, a strategist can reject
    r = sc.delete(f"/events/{EV}/overrides/{EV}_qm3")
    assert r.status_code == 202
    rid = r.json()["request"]["id"]
    assert sc.post(f"/events/{EV}/override-requests/{rid}/reject").json()["status"] == "withdrawn"
    rid = sc.put(f"/events/{EV}/overrides/{EV}_qm4", {"blue": {"1": "frc2"}}).json()["request"]["id"]
    assert s.post(f"/events/{EV}/override-requests/{rid}/reject").json()["status"] == "rejected"
    assert v.get(f"/events/{EV}/overrides").json()["pending"] == []


def test_identity_fix_direct_mode_and_validation(team, data):
    sc, admin = team["scout"], team["admin"]
    assert admin.patch("/admin/security", {"scout_overrides": "direct"}).status_code == 200
    r = sc.put(f"/events/{EV}/overrides/{EV}_qm3", {"red": {"0": "frc1983"}})
    assert r.status_code == 200 and r.json()["status"] == "applied"
    for bad in ({"red": {"0": "<img src=x>"}}, {"red": {"<b>": "frc1"}}, {"green": {"0": "frc1"}},
                {"red": {str(i): "frc1" for i in range(13)}}, {"red": {"0": 254}}):
        assert sc.put(f"/events/{EV}/overrides/{EV}_qm3", bad).status_code == 422, bad


def test_pending_fixes_are_capped(team, monkeypatch):
    from server.routers import scouting
    monkeypatch.setattr(scouting, "MAX_PENDING_PER_USER", 3)
    sc = team["scout"]
    codes = [sc.put(f"/events/{EV}/overrides/{EV}_qm{i}", {"red": {"0": "frc1"}}).status_code for i in range(1, 5)]
    assert codes == [202, 202, 202, 429]


# ------------------------------------------------------------------ write limits
def test_note_flood_is_rate_limited(team):
    sc = team["scout"]
    codes = [sc.post(f"/events/{EV}/notes", {"text": f"spam {i}"}).status_code for i in range(12)]
    assert codes[:10] == [201] * 10 and codes[10] == 429
    r = sc.post(f"/events/{EV}/notes", {"text": "more"})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    # per account: someone else can still write
    assert team["strategist"].post(f"/events/{EV}/notes", {"text": "real note"}).status_code == 201


def test_picklist_and_global_write_limits(team, monkeypatch):
    monkeypatch.setitem(write_limiter.LIMITS, "picklist", [(60, 3)])
    s = team["strategist"]
    codes = [s.put(f"/events/{EV}/picklists/main", {"data": {"stars": [f"frc{i}"]}}).status_code for i in range(4)]
    assert codes == [200, 200, 200, 429]
    monkeypatch.setitem(write_limiter.LIMITS, "write", [(60, 2)])
    write_limiter.reset()
    sc = team["scout"]
    codes = [sc.post(f"/events/{EV}/notes", {"text": f"n{i}"}).status_code for i in range(3)]
    assert codes == [201, 201, 429]


# ------------------------------------------------------------------ display names
def test_display_name_rules(team, app):
    sc, admin = team["scout"], team["admin"]
    admin.patch(f"/users/{uid_of(admin, 'strategist1')}", {"display_name": "Sam Lee"})
    for bad in ("Admin", "admin", "Site Admin", "Аdmin", "4dm1n", "ad​min", "Coach", "C0ACH", "Mentor Bob",
                "<b>x</b>", "x" * 41, "‮admin", "tab\there", "...", "javascript:x"):
        r = sc.patch("/auth/profile", {"display_name": bad})
        assert r.status_code == 422, (bad, r.text)
    assert sc.patch("/auth/profile", {"display_name": "Sam  Lee"}).status_code == 409        # someone else's name
    assert sc.patch("/auth/profile", {"display_name": "5am 1ee"}).status_code == 409        # ... look-alike
    assert sc.patch("/auth/profile", {"display_name": "strategist1"}).status_code == 409    # ... or username
    r = sc.patch("/auth/profile", {"display_name": "Zoë O'Neil-Park"})
    assert r.status_code == 200 and r.json()["user"]["display_name"] == "Zoë O'Neil-Park"
    assert sc.patch("/auth/profile", {"display_name": "Zoe", "role": "admin"}).status_code == 422
    assert team["viewer"].patch("/auth/profile", {"display_name": "Viewer Two"}).status_code == 403   # shared logins
    # admins may name a mentor "Coach Dave", but not with markup / invisible chars / a duplicate
    assert admin.post("/users", {"username": "dave", "display_name": "Coach Dave", "password": "pw-dave"}).status_code == 201
    assert admin.post("/users", {"username": "x1", "display_name": "<script>", "password": "pw-x1"}).status_code == 422
    assert admin.post("/users", {"username": "x2", "display_name": "Co​ach Dave", "password": "pw-x2"}).status_code == 422
    assert admin.post("/users", {"username": "x3", "display_name": "coach dave", "password": "pw-x3"}).status_code == 409
    # an account without a password must not show its username (= its credential) as its display name
    assert admin.post("/users", {"username": "viewer", "display_name": "Viewer"}).status_code == 422
    assert admin.post("/users", {"username": "viewer", "display_name": "Team viewer"}).status_code == 201


def test_new_accounts_default_to_viewer(admin):
    r = admin.post("/users", {"username": "frosh", "password": "pw-frosh"})
    assert r.status_code == 201 and r.json()["role"] == "viewer"


# ------------------------------------------------------------------ passwordless only on the team network
def test_passwordless_only_from_team_network(admin, app):
    admin.post("/users", {"username": "viewer", "display_name": "Team viewer"})        # shared, no password
    lan = new_client(app)
    assert lan.login("viewer", "").status_code == 200
    assert lan.get("/session").json()["network"] == "lan"
    tail = new_client(app, client=("100.101.102.103", 1))                             # Tailscale
    assert tail.login("viewer", "").status_code == 200
    pub = public_client(app)
    s = pub.get("/session").json()
    assert s["network"] == "public" and s["passwordless_ok"] is False
    r = pub.post("/auth/login", {"username": "viewer", "password": ""})
    assert r.status_code == 401 and "password is required" in r.json()["detail"]
    r2 = pub.post("/auth/login", {"username": "no-such-user", "password": ""})       # same answer: no enumeration
    assert r2.status_code == 401 and r2.json() == r.json()
    # accounts with a password are fine through the tunnel, and the cookie is Secure there
    r = pub.post("/auth/login", {"username": "coach", "password": "correct horse 1"})
    assert r.status_code == 200
    assert "secure" in [h for h in r.headers.get_list("set-cookie") if h.startswith("cs_session=")][0].lower()
    # a passwordless session made on the LAN doesn't work from the internet (cookie copied / phone left the Wi-Fi)
    tok = lan.tc.cookies.get("cs_session")
    stolen = public_client(app)
    stolen.tc.cookies.set("cs_session", tok)
    assert stolen.get("/auth/check").status_code == 401
    assert lan.get("/auth/check").status_code == 204


@pytest.mark.parametrize("headers", [
    {"X-Real-IP": "172.25.0.1", "X-Forwarded-For": "203.0.113.7"},               # nginx realip trust list wrong
    {"X-Real-IP": "10.0.0.5", "Cf-Connecting-Ip": "10.0.0.5"},                  # anything via Cloudflare
    {"X-Real-IP": "10.0.0.5", "X-Forwarded-For": "10.0.0.9, 198.51.100.4"},     # any public hop
    {"X-Real-IP": "not-an-ip"},
    {"X-Real-IP": "8.8.8.8"},
    {"X-Real-IP": "10.0.0.5", "Forwarded": "for=198.51.100.4"},
])
def test_trusted_network_fails_closed(admin, app, headers):
    admin.post("/users", {"username": "viewer", "display_name": "Team viewer"})
    c = new_client(app, headers=headers)
    assert c.login("viewer", "").status_code == 401


def test_spoofed_private_forwarded_for_from_lan_is_still_lan(admin, app):
    admin.post("/users", {"username": "viewer", "display_name": "Team viewer"})
    assert new_client(app, headers={"X-Forwarded-For": "192.168.1.4"}).login("viewer", "").status_code == 200


def test_passwordless_policy_setting(admin, app):
    admin.post("/users", {"username": "viewer", "display_name": "Team viewer"})
    from server import deps
    db = DB.connect(); DB.set_setting(db, "passwordless_login", None)
    assert deps.passwordless_policy(db) == "never"                                       # default: off
    assert admin.patch("/admin/security", {"passwordless_login": "lan"}).status_code == 200
    lan = new_client(app)
    assert lan.login("viewer", "").status_code == 200
    assert admin.patch("/admin/security", {"passwordless_login": "never"}).status_code == 200
    assert lan.get("/auth/check").status_code == 401                                    # existing sessions end
    r = new_client(app).login("viewer", "")
    assert r.status_code == 401 and "Every account needs a password" in r.json()["detail"]
    assert admin.patch("/admin/security", {"passwordless_login": "everywhere"}).status_code == 200
    assert public_client(app).login("viewer", "").status_code == 200
    assert admin.patch("/admin/security", {"passwordless_login": "sometimes"}).status_code == 422
    e = audit(admin, action="settings.security")[0]
    assert e["before"] == {"passwordless_login": "never"} and e["after"] == {"passwordless_login": "everywhere"}


def test_setting_a_password_unlocks_the_session(admin, app):
    admin.post("/users", {"username": "kid", "display_name": "Kid A", "role": "scout"})
    c = new_client(app)
    assert c.login("kid", "").status_code == 200
    assert c.post("/auth/password", {"current_password": "", "new_password": "kidpass1"}).status_code == 204
    assert DB.connect().execute("SELECT net_locked FROM sessions s JOIN users u ON u.id=s.user_id "
                                "WHERE u.username='kid'").fetchone()[0] == 0


def test_login_rate_limit_keys_on_real_client_ip(admin, app):
    a = new_client(app, headers={"X-Real-IP": "198.51.100.1"})
    for i in range(10):
        a.login("coach", f"bad {i}")
    assert a.login("coach", "correct horse 1").status_code == 429
    b = new_client(app, headers={"X-Real-IP": "198.51.100.2"})                        # another person, same proxy peer
    assert b.login("coach", "correct horse 1").status_code == 200


# ------------------------------------------------------------------ kill switches
def test_lockdown_freezes_non_admin_writes(team):
    admin, s, sc, v = team["admin"], team["strategist"], team["scout"], team["viewer"]
    assert s.patch("/admin/security", {"lockdown": True}).status_code == 403
    assert admin.patch("/admin/security", {"lockdown": True}).status_code == 200
    assert v.get("/session").json()["lockdown"] is True
    assert sc.post(f"/events/{EV}/notes", {"text": "x"}).status_code == 423
    assert s.put(f"/events/{EV}/picklists/main", {"data": {}}).status_code == 423
    assert sc.put(f"/events/{EV}/overrides/{EV}_qm1", {"red": {"0": "frc1"}}).status_code == 423
    assert s.get(f"/events/{EV}/picklists").status_code == 200                           # reads still work
    assert v.post("/auth/password", {"current_password": "password-viewer", "new_password": "newer pw"}).status_code == 204
    assert admin.put(f"/events/{EV}/picklists/main", {"data": {"stars": ["frc4911"]}}).status_code == 200
    assert admin.get("/admin/security").json()["lockdown"]["by"]["display_name"]
    assert admin.patch("/admin/security", {"lockdown": False}).status_code == 200
    assert sc.post(f"/events/{EV}/notes", {"text": "x"}).status_code == 201


def test_sign_everyone_out(team, app):
    admin = team["admin"]
    other_admin = new_client(app)
    assert other_admin.login("coach", "correct horse 1").status_code == 200
    assert team["scout"].post("/admin/sessions/revoke", {}).status_code == 403
    r = admin.post("/admin/sessions/revoke", {})
    assert r.status_code == 200 and r.json()["signed_out"] == 3
    for role in ("viewer", "scout", "strategist"):
        assert team[role].get("/auth/check").status_code == 401
    assert admin.get("/auth/check").status_code == 204 and other_admin.get("/auth/check").status_code == 204
    admin.post("/admin/sessions/revoke", {"include_admins": True})
    assert admin.get("/auth/check").status_code == 204 and other_admin.get("/auth/check").status_code == 401


def test_disable_account_is_instant_and_logged(team):
    admin, s = team["admin"], team["strategist"]
    assert admin.patch(f"/users/{uid_of(admin, 'strategist1')}", {"disabled": True}).status_code == 200
    assert s.put(f"/events/{EV}/picklists/main", {"data": {}}).status_code == 401
    assert audit(admin, action="users.update")[0]["after"]["disabled"] is True


# ------------------------------------------------------------------ privilege escalation / enumeration
def test_no_self_promotion(team):
    for role in ("viewer", "scout", "strategist"):
        c = team[role]
        me = c.get("/session").json()["user"]["id"]
        assert c.patch(f"/users/{me}", {"role": "admin"}).status_code == 403
        assert c.post("/users", {"username": "evil" + role, "password": "long password!", "role": "admin"}).status_code == 403
        assert c.get("/users").status_code == 403
        assert c.patch("/admin/security", {"lockdown": False}).status_code == 403
        assert c.get("/openapi.json").status_code == 403
        assert c.post("/setup", {"setup_code": "x", "username": "evil", "password": "correct horse 9"}).status_code in (403, 409)
    assert team["admin"].get("/session").json()["user"]["role"] == "admin"


def test_no_usernames_leak_to_teammates(team):
    sc, s, v = team["scout"], team["strategist"], team["viewer"]
    s.put(f"/events/{EV}/picklists/main", {"data": {"stars": ["frc1"]}})
    sc.post(f"/events/{EV}/notes", {"text": "hello"})
    sc.put(f"/events/{EV}/overrides/{EV}_qm1", {"red": {"0": "frc1"}})
    s.put(f"/events/{EV}/overrides/{EV}_qm2", {"red": {"0": "frc2"}})
    for p in (f"/events/{EV}/picklists", f"/events/{EV}/picklists/main/versions", f"/events/{EV}/notes",
              f"/events/{EV}/overrides"):
        body = v.get(p).text
        assert "scout1" not in body and "strategist1" not in body and "username" not in body, p


# ------------------------------------------------------------------ settings / events: strict + revertible
@pytest.mark.parametrize("goal", ["<img src=x onerror=alert(1)>", "DCMP\"><b>", "a&b", "javascript:alert(1)",
                                  "Worlds​"])
def test_settings_refuse_markup(admin, goal):
    assert admin.patch("/settings", {"season_goal": goal}).status_code == 422
    assert admin.patch("/settings", {"options": {"hubcam": True, "x": "<b>"}}).status_code == 422
    assert admin.patch("/settings", {"season_goal": "Win event"}).status_code == 200


def test_settings_and_events_revert(admin, data):
    admin.put("/settings/tba-key", {"key": "k" * 60})
    admin.patch("/settings", {"season_goal": "DCMP"})
    admin.patch("/settings", {"season_goal": "Worlds"})
    e = audit(admin, action="settings.update")[0]
    assert e["before"] == {"season_goal": "DCMP"}
    assert admin.post(f"/audit/{e['id']}/revert").status_code == 200
    assert admin.get("/settings").json()["season_goal"] == "DCMP"
    assert admin.post(f"/audit/{audit(admin, action='settings.tba_key')[0]['id']}/revert").status_code == 409
    admin.post("/events", {"key": EV})
    admin.delete(f"/events/{EV}")
    e = audit(admin, action="events.unfollow")[0]
    assert admin.post(f"/audit/{e['id']}/revert").status_code == 200
    assert [x["key"] for x in admin.get("/events").json()] == [EV]
    assert json.loads((data / "config" / "worker.json").read_text())["events"] == [EV]


def test_audit_filters(team):
    sc, admin = team["scout"], team["admin"]
    sc.post(f"/events/{EV}/notes", {"text": "a"})
    team["strategist"].post(f"/events/{EV}/notes", {"text": "b"})
    mine = audit(admin, user_id=uid_of(admin, "scout1"), action="note")
    assert [e["user"]["username"] for e in mine] == ["scout1"]
    assert audit(admin, action="note_")== []                         # LIKE wildcards are escaped
    assert all(e["via"] == "lan" for e in audit(admin, via="lan"))
    page = admin.get("/audit?limit=1").json()
    assert len(page["entries"]) == 1 and page["next_before_id"]


# ------------------------------------------------------------------ upgrade path: an existing production DB
def test_migrates_an_existing_db(data):
    import sqlite3
    from server.config import cfg
    old = sqlite3.connect(cfg.DB_PATH)
    old.executescript("""
      CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE, display_name TEXT NOT NULL DEFAULT '',
        pw_hash TEXT NOT NULL, role TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
        updated_at REAL NOT NULL, last_login REAL);
      CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, csrf TEXT NOT NULL, created_at REAL NOT NULL,
        last_seen REAL NOT NULL, expires_at REAL NOT NULL);
      CREATE TABLE picklists (event TEXT NOT NULL, name TEXT NOT NULL, data TEXT NOT NULL, version INTEGER NOT NULL,
        updated_at REAL NOT NULL, updated_by INTEGER, PRIMARY KEY (event, name));
      CREATE TABLE notes (id INTEGER PRIMARY KEY, event TEXT NOT NULL, team TEXT, match TEXT, text TEXT NOT NULL,
        author_id INTEGER, created_at REAL NOT NULL, updated_at REAL NOT NULL);
      CREATE TABLE override_log (id INTEGER PRIMARY KEY, event TEXT NOT NULL, match TEXT NOT NULL, data TEXT NOT NULL,
        user_id INTEGER, at REAL NOT NULL);
      INSERT INTO users VALUES (1, 'coach', 'Coach', 'x', 'admin', 0, 0, 0, NULL);
      INSERT INTO picklists VALUES ('2026wasam', 'main', '{"stars":["frc1"]}', 7, 0, 1);
      INSERT INTO notes VALUES (1, '2026wasam', NULL, NULL, 'old note', 1, 0, 0);
      INSERT INTO sessions VALUES ('h', 1, 'c', 0, 0, 0);
    """)
    old.commit(); old.close()
    DB.init(); DB.init()                                                     # idempotent
    c = DB.connect()
    assert c.execute("SELECT pw_strength FROM users").fetchone()[0] == 2
    assert c.execute("SELECT net_locked FROM sessions").fetchone()[0] == 0
    assert c.execute("SELECT removed_at FROM notes").fetchone()[0] is None
    assert [tuple(r) for r in c.execute("SELECT version, data FROM picklist_versions")] == [(7, '{"stars":["frc1"]}')]
    assert c.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0


def test_compose_gives_api_an_env_allowlist():
    """deploy/docker-compose.yml: the api gets the account settings from .env (explicit allowlist, defaults equal to
    server/config.py) but no env_file and no TBA_KEY / EVENTS passthrough."""
    import os, re
    from server.config import Config
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    txt = open(os.path.join(root, "deploy", "docker-compose.yml")).read()
    api = txt.split("\n  api:\n", 1)[1].split("\n  web:\n", 1)[0]
    api = "\n".join(ln.split("#", 1)[0] for ln in api.splitlines())          # settings only, not the comments
    assert "env_file" not in api and "TBA_KEY" not in api and "EVENTS" not in api
    env = dict(re.findall(r"^\s+([A-Z_]+): \$\{\1:-([^}]*)\}\s*$", api, re.M))
    for k in ("SESSION_IDLE_DAYS", "SESSION_MAX_DAYS", "PASSWORD_MIN", "MEMBER_PASSWORD_MIN", "SETUP_CODE_REQUIRED",
              "COOKIE_SECURE"):
        assert k in env, k
    saved = {k: os.environ.pop(k, None) for k in env}
    try:
        d = Config()
    finally:
        for k, v in saved.items():
            if v is not None: os.environ[k] = v
    assert float(env["SESSION_IDLE_DAYS"]) == d.SESSION_IDLE_DAYS and float(env["SESSION_MAX_DAYS"]) == d.SESSION_MAX_DAYS
    assert int(env["PASSWORD_MIN"]) == d.PASSWORD_MIN and int(env["MEMBER_PASSWORD_MIN"]) == d.MEMBER_PASSWORD_MIN
    assert (env["SETUP_CODE_REQUIRED"] != "0") == d.SETUP_CODE_REQUIRED and env["COOKIE_SECURE"] == d.COOKIE_SECURE
    assert "CS_URL_ALLOW" in env and env.get("CS_PROBE_LINKS") == "1"      # pasted-link checks run in the api
