"""Competitions are separate: notes, picklists (+ versions), identity overrides / fix queue and the audit log of one
event never show up under another; the followed-events list carries what the competition switcher needs."""
import json

from . import conftest
from .conftest import GOOD_KEY

A, B = "2026wasam", "2026wabe"


def follow_both(admin, monkeypatch):
    monkeypatch.setitem(conftest.FAKE_TBA, f"/event/{B}/simple",
                        {"key": B, "name": "PNW District Bonney Lake Event", "year": 2026, "start_date": "2026-03-13",
                         "end_date": "2026-03-15", "city": "Bonney Lake", "state_prov": "WA", "country": "USA"})
    assert admin.put("/settings/tba-key", {"key": GOOD_KEY}).status_code == 200
    for ev in (A, B):
        assert admin.post("/events", {"key": ev}).status_code == 201


def test_notes_are_per_event(team):
    sc, v = team["scout"], team["viewer"]
    assert sc.post(f"/events/{A}/notes", {"team": "frc4911", "match": f"{A}_qm1", "text": "A only"}).status_code == 201
    assert sc.post(f"/events/{B}/notes", {"team": "frc4911", "text": "B only"}).status_code == 201
    assert [n["text"] for n in v.get(f"/events/{A}/notes").json()] == ["A only"]
    assert [n["text"] for n in v.get(f"/events/{B}/notes?team=frc4911").json()] == ["B only"]
    # a match of event A can't be attached to (or used to filter) event B
    assert sc.post(f"/events/{B}/notes", {"match": f"{A}_qm1", "text": "x"}).status_code == 422
    assert v.get(f"/events/{B}/notes?match={A}_qm1").status_code == 422
    assert v.get("/events/2026nothing/notes").json() == []


def test_picklists_and_versions_are_per_event(team):
    s, v = team["strategist"], team["viewer"]
    assert s.put(f"/events/{A}/picklists/main", {"data": {"stars": ["frc4911"]}}).status_code == 200
    assert s.put(f"/events/{A}/picklists/main", {"data": {"stars": ["frc254"]}}).status_code == 200
    assert v.get(f"/events/{B}/picklists/main").status_code == 404
    assert v.get(f"/events/{B}/picklists").json() == []
    assert v.get(f"/events/{B}/picklists/main/versions").status_code == 404
    assert v.get(f"/events/{B}/picklists/main/versions/1").status_code == 404
    assert s.post(f"/events/{B}/picklists/main/versions/1/restore").status_code == 404     # A's history isn't B's
    r = s.put(f"/events/{B}/picklists/main", {"data": {"stars": ["frc1983"]}})
    assert r.status_code == 200 and r.json()["version"] == 1 and r.json()["event"] == B   # own version counter
    assert v.get(f"/events/{A}/picklists/main").json()["data"] == {"stars": ["frc254"]}
    assert [x["version"] for x in v.get(f"/events/{A}/picklists/main/versions").json()] == [2, 1]
    assert [x["version"] for x in v.get(f"/events/{B}/picklists/main/versions").json()] == [1]


def test_overrides_and_fix_queue_are_per_event(team, data):
    st, sc, v = team["strategist"], team["scout"], team["viewer"]
    assert st.put(f"/events/{A}/overrides/{A}_qm3", {"red": {"0": "frc1983"}}).status_code == 200
    r = sc.put(f"/events/{A}/overrides/{A}_qm4", {"blue": {"1": "frc9442"}})                 # scout: queued
    assert r.status_code == 202
    rid = r.json()["request"]["id"]
    ob = v.get(f"/events/{B}/overrides").json()
    assert ob["overrides"] == {} and ob["log"] == [] and ob["pending"] == []
    oa = v.get(f"/events/{A}/overrides").json()
    assert list(oa["overrides"]) == [f"{A}_qm3"] and [p["id"] for p in oa["pending"]] == [rid]
    # A's request can't be decided through B (404), and B's file is untouched
    assert st.post(f"/events/{B}/override-requests/{rid}/approve").status_code == 404
    assert sc.post(f"/events/{B}/override-requests/{rid}/reject").status_code == 404
    assert not (data / "events" / B / "overrides.json").exists()
    assert st.put(f"/events/{B}/overrides/{A}_qm3", {"red": {"0": "frc1"}}).status_code == 422   # A's match under B
    assert st.post(f"/events/{A}/override-requests/{rid}/approve").status_code == 200
    assert json.loads((data / "events" / A / "overrides.json").read_text()) == {
        f"{A}_qm3": {"red": {"0": "frc1983"}}, f"{A}_qm4": {"blue": {"1": "frc9442"}}}


def test_audit_log_filters_by_event(team, data):
    adm, s = team["admin"], team["strategist"]
    s.put(f"/events/{A}/picklists/main", {"data": {"stars": ["frc4911"]}})
    s.put(f"/events/{B}/picklists/main", {"data": {"stars": ["frc254"]}})
    # routers without explicit audit records (video setup) are filed under their event too
    (data / "events" / B).mkdir(parents=True, exist_ok=True)
    adm.post(f"/events/{B}/sources", {"url": "https://www.youtube.com/watch?v=abcdefghijk", "role": "wide"})
    ea = adm.get(f"/audit?event={A}").json()["entries"]
    eb = adm.get(f"/audit?event={B}").json()["entries"]
    assert ea and all(e["event"] == A for e in ea) and {e["target"] for e in ea} == {f"{A}/main"}
    assert eb and all(e["event"] == B for e in eb)
    assert f"{B}/main" in {e["target"] for e in eb}
    assert any(e["action"].startswith("http.") and e["target"].startswith(f"/app/v1/events/{B}/") for e in eb)
    en = adm.get("/audit?event=none").json()["entries"]
    assert en and all(e["event"] is None for e in en)                       # accounts / settings / sign-ins
    assert adm.get("/audit?event=../x").status_code == 422


def test_followed_events_carry_switcher_info(admin, data, monkeypatch):
    follow_both(admin, monkeypatch)
    evs = {e["key"]: e for e in admin.get("/events").json()}
    b = evs[B]
    assert (b["end_date"], b["city"], b["state_prov"]) == ("2026-03-15", "Bonney Lake", "WA")
    assert b["needs_setup"] is True and b["calibrated"] is False and b["n_total"] is None
    # site build progress + calibration
    site = data / "site" / "api" / A
    site.mkdir(parents=True)
    (site / "event.json").write_text(json.dumps({"key": A, "name": "Sammamish", "start": "2026-03-06", "end": "2026-03-08",
                                                 "city": "Sammamish", "state_prov": "WA", "n_done": 54, "n_total": 74,
                                                 "updated": 1.0e9, "calibrated": True, "matches": [], "teams": []}))
    (data / "events" / B).mkdir(parents=True, exist_ok=True)
    (data / "events" / B / "camera_url-abc.json").write_text("{}")         # a livestream camera counts
    evs = {e["key"]: e for e in admin.get("/events").json()}
    a = evs[A]
    assert (a["n_done"], a["n_total"], a["end_date"], a["city"]) == (54, 74, "2026-03-08", "Sammamish")
    assert a["needs_setup"] is False and evs[B]["needs_setup"] is False
    # unfollow + revert from the audit log restores the full row (dates, place)
    assert admin.delete(f"/events/{B}").status_code == 204
    e = admin.get(f"/audit?event={B}&action=events.unfollow").json()["entries"][0]
    assert admin.post(f"/audit/{e['id']}/revert").status_code == 200
    b = {e["key"]: e for e in admin.get("/events").json()}[B]
    assert (b["end_date"], b["city"]) == ("2026-03-15", "Bonney Lake")
    wc = json.loads((data / "config" / "worker.json").read_text())
    assert set(wc["events"]) == {A, B}
