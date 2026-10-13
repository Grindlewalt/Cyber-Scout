"""Settings, events, worker config, picklists, overrides file."""
import json

from .conftest import GOOD_KEY


def test_wizard_flow(admin, data):
    assert admin.get("/tba/events").status_code == 409            # no key yet
    assert admin.put("/settings/tba-key", {"key": GOOD_KEY}).status_code == 200
    r = admin.patch("/settings", {"team_number": 4911, "season_goal": "DCMP"})
    assert r.status_code == 200 and r.json()["team_name"] == "CyberKnights"
    assert admin.patch("/settings", {"team_number": 1}).status_code == 404     # not on TBA
    ev = admin.get("/tba/events?year=2026").json()
    assert [e["key"] for e in ev["team_events"]] == ["2026wasam"] and len(ev["events"]) == 2
    assert admin.get("/session").json()["configured"] is False
    r = admin.post("/events", {"key": "2026wasam"})
    assert r.status_code == 201 and r.json()["name"].startswith("PNW")
    assert admin.post("/events", {"key": "2026nope"}).status_code == 404
    assert admin.post("/events", {"key": "../etc"}).status_code == 422
    assert admin.get("/session").json()["configured"] is False      # not until the wizard's last step
    assert admin.patch("/settings", {"setup_complete": True}).status_code == 200
    assert admin.get("/session").json()["configured"] is True
    wc = json.loads((data / "config" / "worker.json").read_text())
    assert wc["events"] == ["2026wasam"] and wc["team_number"] == 4911 and wc["season_goal"] == "DCMP"
    admin.patch("/settings", {"options": {"gpu_device": 1, "cpu_only": False}})
    admin.patch("/settings", {"options": {"hubcam": True}})          # partial update keeps the others
    wc = json.loads((data / "config" / "worker.json").read_text())
    assert wc["options"] == {"gpu_device": 1, "cpu_only": False, "hubcam": True, "poll_seconds": None}
    assert admin.delete("/events/2026wasam").status_code == 204
    wc = json.loads((data / "config" / "worker.json").read_text())
    assert wc["events"] == []                                          # configured-but-empty: worker idles


def test_worker_conf_events_null_until_configured(admin, data):
    wc = json.loads((data / "config" / "worker.json").read_text())
    assert wc["events"] is None and wc["tba_key"] is None              # worker falls back to env


def test_picklist_versioning(team):
    s, v = team["strategist"], team["viewer"]
    assert v.get("/events/2026wasam/picklists/main").status_code == 404
    r = s.put("/events/2026wasam/picklists/main", {"data": {"stars": ["frc4911"], "dnp": [], "weights": {"made": 3}}})
    assert r.status_code == 200 and r.json()["version"] == 1
    r = s.put("/events/2026wasam/picklists/main", {"data": {"stars": []}, "base_version": 1})
    # other users see the display name only, never the username (audit: _who() exposed usernames)
    assert r.json()["version"] == 2 and r.json()["updated_by"]["display_name"] and "username" not in r.json()["updated_by"]
    r = team["admin"].put("/events/2026wasam/picklists/main", {"data": {"stars": ["frc1"]}, "base_version": 1})
    assert r.status_code == 409 and r.json()["detail"]["current"]["version"] == 2
    assert v.get("/events/2026wasam/picklists/main").json()["data"] == {"stars": []}
    assert s.put("/events/2026wasam/picklists/a%2Fb", {"data": {}}).status_code in (404, 422)
    assert s.put("/events/2026wasam/picklists/big", {"data": {"x": "y" * 300000}}).status_code in (413, 422)   # strict schema rejects it first


def test_overrides_file(team, data):
    sc = team["strategist"]           # strategists apply directly; scouts' fixes wait for approval (test_hardening)
    p = data / "events" / "2026wasam" / "overrides.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"2026wasam_qm1": {"blue": {"2": "frc9442"}}}))     # hand-edited entry survives
    r = sc.put("/events/2026wasam/overrides/2026wasam_qm12", {"red": {"0": "frc1983", "1": "frc9442"}})
    assert r.status_code == 200, r.text
    d = json.loads(p.read_text())
    assert d == {"2026wasam_qm1": {"blue": {"2": "frc9442"}}, "2026wasam_qm12": {"red": {"0": "frc1983", "1": "frc9442"}}}
    assert sc.put("/events/2026wasam/overrides/2026wasam_qm12", {"red": {"x": "frc1"}}).status_code == 422
    assert sc.put("/events/2026wasam/overrides/2026other_qm12", {"red": {"0": "frc1"}}).status_code == 422
    assert sc.put("/events/2026wasam/overrides/2026wasam_qm12", {"red": {"0": "1983"}}).status_code == 422
    assert sc.delete("/events/2026wasam/overrides/2026wasam_qm12").status_code == 204
    assert json.loads(p.read_text()) == {"2026wasam_qm1": {"blue": {"2": "frc9442"}}}
    log = team["viewer"].get("/events/2026wasam/overrides").json()["log"]
    me = sc.get("/session").json()["user"]["id"]
    assert [x["by"]["id"] for x in log] == [me, me] and all("username" not in x["by"] for x in log)


def test_notes_validation(team):
    sc = team["scout"]
    assert sc.post("/events/2026wasam/notes", {"team": "4911", "text": "x"}).status_code == 422
    assert sc.post("/events/2026wasam/notes", {"text": ""}).status_code == 422
    assert sc.post("/events/2026wasam/notes", {"text": "x" * 4001}).status_code == 422
    assert sc.post("/events/BAD/notes", {"text": "x"}).status_code == 422
    assert sc.post("/events/2026wasam/notes", {"team": "frc4911", "text": "<script>"}).status_code == 422   # plain text only
    assert sc.post("/events/2026wasam/notes", {"team": "frc4911", "text": "fast <3, a < b"}).status_code == 201
    assert len(team["viewer"].get("/events/2026wasam/notes?team=frc4911").json()) == 1


def test_openapi(client, admin):
    assert client.get("/docs").status_code == 404                 # no Swagger UI
    spec = admin.get("/openapi.json").json()                      # schema: admins only
    paths = spec["paths"]
    for p in ("/app/v1/session", "/app/v1/auth/login", "/app/v1/users", "/app/v1/settings/tba-key",
              "/app/v1/events/{ev}/picklists/{name}", "/app/v1/events/{ev}/overrides/{match}"):
        assert p in paths, p


def test_picklist_rejects_markup(team):
    """Security audit finding 1: picklist data is strictly typed (no strings where the UI renders numbers)."""
    s = team["strategist"]
    bad = [{"weights": {"made": "<img src=x onerror=alert(1)>"}}, {"stars": ["<b>x</b>"]},
           {"weights": {"<x>": 1}}, {"html": "<script>"}, {"weights": {"made": 1e9}}]
    for d in bad:
        assert s.put("/events/2026wasam/picklists/main", {"data": d}).status_code == 422, d
    ok = s.put("/events/2026wasam/picklists/main", {"data": {"stars": ["frc4911"], "dnp": ["frc254"], "weights": {"blend": 4, "made": 0.5}}})
    assert ok.status_code == 200 and ok.json()["data"]["weights"]["made"] == 0.5
