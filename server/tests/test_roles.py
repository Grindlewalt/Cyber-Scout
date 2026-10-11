"""Role matrix: who may call what (viewer < scout < strategist < admin)."""
import pytest

from .conftest import C, GOOD_KEY

ROLES = ["viewer", "scout", "strategist", "admin"]
# (method, path, body, minimum role)
CASES = [
    ("get", "/auth/check", None, "viewer"),
    ("get", "/settings", None, "viewer"),
    ("get", "/events", None, "viewer"),
    ("get", "/events/2026wasam/picklists", None, "viewer"),
    ("get", "/events/2026wasam/notes", None, "viewer"),
    ("get", "/events/2026wasam/overrides", None, "viewer"),
    ("post", "/events/2026wasam/notes", {"team": "frc4911", "text": "fast cycles"}, "scout"),
    ("put", "/events/2026wasam/overrides/2026wasam_qm12", {"red": {"0": "frc1983"}}, "scout"),
    ("delete", "/events/2026wasam/overrides/2026wasam_qm12", None, "scout"),
    ("put", "/events/2026wasam/picklists/main", {"data": {"stars": ["frc4911"]}}, "strategist"),
    ("delete", "/events/2026wasam/picklists/other", None, "strategist"),
    ("get", "/users", None, "admin"),
    ("post", "/users", {"username": "newbie", "password": "long enough pw", "role": "viewer"}, "admin"),
    ("patch", "/settings", {"season_goal": "Worlds"}, "admin"),
    ("put", "/settings/tba-key", {"key": GOOD_KEY}, "admin"),
    ("get", "/tba/events?year=2026", None, "admin"),
    ("get", "/tba/team/4911", None, "admin"),
    ("post", "/events", {"key": "2026wasam"}, "admin"),
]


@pytest.mark.parametrize("method,path,body,need", CASES, ids=[f"{c[0]} {c[1]}" for c in CASES])
def test_matrix(team, method, path, body, need):
    team["admin"].put("/settings/tba-key", {"key": GOOD_KEY})
    for role in ROLES:
        c = team[role]
        r = getattr(c, method)(path, body) if body is not None else getattr(c, method)(path)
        allowed = ROLES.index(role) >= ROLES.index(need)
        if allowed:
            assert r.status_code < 400 or (method == "post" and r.status_code == 409), (role, r.status_code, r.text)
        else:
            assert r.status_code == 403, (role, r.status_code, r.text)
        if method == "post" and path == "/users" and allowed:
            break   # username now taken; the rest would 409


def test_anonymous_gets_401(app, admin):
    c = C(admin.tc.__class__(app, base_url="http://testserver"))
    c.get("/session")
    for p in ("/auth/check", "/settings", "/events", "/users", "/events/2026wasam/notes"):
        assert c.get(p).status_code == 401, p
    assert c.post("/events/2026wasam/notes", {"text": "x"}).status_code == 401


def test_options_hidden_from_non_admin(team):
    team["admin"].patch("/settings", {"options": {"hubcam": True, "poll_seconds": 60}})
    a = team["admin"].get("/settings").json()
    assert a["options"]["hubcam"] is True and a["options"]["poll_seconds"] == 60
    v = team["viewer"].get("/settings").json()
    assert v["options"] is None and v["tba_key_hint"] is None


def test_note_ownership(team):
    scout, scout_note = team["scout"], None
    r = scout.post("/events/2026wasam/notes", {"match": "2026wasam_qm12", "text": "bumper fell off"})
    assert r.status_code == 201
    nid = r.json()["id"]
    # another scout-level user cannot edit; strategist can
    admin = team["admin"]
    admin.post("/users", {"username": "scout2", "password": "password-scout2", "role": "scout"})
    s2 = C(admin.tc.__class__(admin.tc.app, base_url="http://testserver"))
    assert s2.login("scout2", "password-scout2").status_code == 200
    assert s2.patch(f"/notes/{nid}", {"text": "hijack"}).status_code == 403
    assert s2.delete(f"/notes/{nid}").status_code == 403
    assert scout.patch(f"/notes/{nid}", {"text": "bumper fell off in auto"}).status_code == 200
    assert team["strategist"].patch(f"/notes/{nid}", {"text": "edited by strat"}).status_code == 200
    assert team["viewer"].get("/events/2026wasam/notes?match=2026wasam_qm12").json()[0]["text"] == "edited by strat"
    assert team["strategist"].delete(f"/notes/{nid}").status_code == 204


def test_role_change_applies_immediately(team):
    admin, viewer = team["admin"], team["viewer"]
    uid = [u for u in admin.get("/users").json() if u["username"] == "viewer1"][0]["id"]
    assert viewer.put("/events/2026wasam/picklists/main", {"data": {}}).status_code == 403
    admin.patch(f"/users/{uid}", {"role": "strategist"})
    assert viewer.put("/events/2026wasam/picklists/main", {"data": {}}).status_code == 200
