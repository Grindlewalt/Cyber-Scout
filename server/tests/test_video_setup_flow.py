"""The guided setup flow: find an event on TBA (GET /events/search) and follow it; on the Video page add links in
plain words (main / station cams, live or recording), replace a link in place, remove a TBA source for good
(dismissed: discovery keeps it off, restore with PATCH), and the suggested match video mode."""
import json

import pytest

from . import conftest
from .conftest import GOOD_KEY
from server.routers import sources as R

EV = "2026wagg"
EVENTS = [
    {"key": "2026wasam", "name": "PNW District Sammamish Event", "year": 2026, "start_date": "2026-03-06",
     "end_date": "2026-03-08", "city": "Sammamish", "state_prov": "WA", "event_type": 1, "district": {"abbreviation": "pnw"}},
    {"key": EV, "name": "Glacier Peak Girls Gen", "short_name": "Girls Gen", "year": 2026, "start_date": "2026-10-17",
     "end_date": "2026-10-17", "city": "Snohomish", "state_prov": "WA", "event_type": 99},
    {"key": "2026cmptx", "name": "FIRST Championship", "year": 2026, "start_date": "2026-04-15", "city": "Houston",
     "event_type": 4},
]


@pytest.fixture
def tba(admin, monkeypatch):
    monkeypatch.setitem(conftest.FAKE_TBA, "/events/2026/simple", EVENTS)
    monkeypatch.setitem(conftest.FAKE_TBA, f"/event/{EV}/simple", EVENTS[1])
    monkeypatch.setitem(conftest.FAKE_TBA, f"/event/{EV}/matches", [{"key": f"{EV}_qm1", "videos": []}])
    monkeypatch.setitem(conftest.FAKE_TBA, "/event/2025wasam/simple",
                        {"key": "2025wasam", "name": "PNW District Sammamish Event", "year": 2025, "start_date": "2025-03-07"})
    monkeypatch.setitem(conftest.FAKE_TBA, "/events/2025/simple", [])
    R._SUGGEST.clear()
    assert admin.put("/settings/tba-key", {"key": GOOD_KEY}).status_code == 200
    return admin


def test_search_by_name_place_and_key(tba):
    a = tba
    r = a.get("/events/search?q=girls")
    assert r.status_code == 200, r.text
    j = r.json()
    assert [e["key"] for e in j["results"]] == [EV] and j["results"][0]["offseason"] is True
    assert [e["key"] for e in a.get("/events/search?q=snoho").json()["results"]] == [EV]
    assert a.get("/events/search?q=2026wag").json()["results"][0]["key"] == EV          # key prefix
    assert a.get("/events/search?q=pnw sam").json()["results"][0]["key"] == "2026wasam"  # district + name words
    assert a.get("/events/search?q=zzzz").json()["results"] == []
    # another season's key: looked up directly
    j = a.get("/events/search?q=2025wasam").json()
    assert j["year"] == 2025 and [e["key"] for e in j["results"]] == ["2025wasam"]
    # empty query: every event, the team's events flagged; follow + the flag
    j = a.get("/events/search").json()
    assert {e["key"] for e in j["results"]} == {e["key"] for e in EVENTS} and j["team_number"] in (None, 4911)
    assert a.post("/events", {"key": EV}).status_code == 201
    assert a.get("/events/search?q=girls").json()["results"][0]["followed"] is True
    assert "k" * 20 not in a.get("/events/search?q=").text                              # never the TBA key


def test_search_roles_and_key(team, monkeypatch):
    assert team["strategist"].get("/events/search?q=x").status_code == 403
    assert team["admin"].get("/events/search?q=x").status_code == 409                   # no TBA key yet


def test_add_station_cams_live_flag_and_labels(tba, data):
    a = tba
    m = a.post(f"/events/{EV}/sources", {"url": "twitch.tv/firstwa", "role": "wide", "primary": True})
    assert m.status_code == 201, m.text
    assert m.json()["label"] == "Twitch firstwa" and m.json()["kind"] == "livestream"
    r = a.post(f"/events/{EV}/sources", {"url": "https://youtu.be/dQw4w9WgXcQ", "role": "alliance-side",
                                         "station": "red", "live": True})
    assert r.status_code == 201 and r.json()["station"] == "red" and r.json()["kind"] == "livestream"
    b = a.post(f"/events/{EV}/sources", {"url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "role": "alliance-side",
                                         "station": "blue"}).json()
    assert b["kind"] == "video" and b["station"] == "blue"                              # a recording (VOD)
    assert a.post(f"/events/{EV}/sources", {"url": "twitch.tv/firstwa"}).status_code == 422   # already listed
    assert a.post(f"/events/{EV}/sources", {"url": "twitch.tv/x2", "station": "green"}).status_code == 422
    assert a.patch(f"/events/{EV}/sources/{b['id']}", {"station": "purple"}).status_code == 422
    j = a.patch(f"/events/{EV}/sources/{b['id']}", {"role": "dynamic", "station": ""}).json()
    assert j["role"] == "dynamic" and "station" not in j
    d = a.get(f"/events/{EV}/sources").json()
    assert d["stream_source"] == m.json()["id"] and d["stations"] == ["red", "blue"]


def test_replace_keeps_id_layout_and_calibration(tba, data):
    a = tba
    s = a.post(f"/events/{EV}/sources", {"url": "twitch.tv/old_stream", "role": "wide", "primary": True}).json()
    evd = data / "events" / EV
    (evd / "layout.json").write_text(json.dumps({"sources": {s["id"]: {"W": 1920, "H": 1080, "regions": [
        {"kind": "field", "box": [0, 100, 1920, 1080]}]}}}))
    r = a.patch(f"/events/{EV}/sources/{s['id']}", {"url": "https://www.youtube.com/@FIRSTWashington/live"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["id"] == s["id"] and j["url"] == "https://www.youtube.com/@FIRSTWashington/live" and j["kind"] == "livestream"
    assert j["label"] == "YouTube @FIRSTWashington live" and j["primary"] is True      # automatic name follows the link
    assert a.get(f"/events/{EV}/sources").json()["sources"][0]["has_layout"] is True
    # the old link can be added again as a new source (no id clash)
    n = a.post(f"/events/{EV}/sources", {"url": "twitch.tv/old_stream", "role": "dynamic"})
    assert n.status_code == 201 and n.json()["id"] != s["id"]
    assert a.patch(f"/events/{EV}/sources/{s['id']}", {"url": "twitch.tv/old_stream"}).status_code == 422   # duplicate
    assert a.patch(f"/events/{EV}/sources/{s['id']}", {"url": "-x"}).status_code == 422


def test_remove_tba_source_is_sticky_and_restorable(tba, data):
    a = tba
    S = R.SRC
    evd = data / "events" / EV
    found = S.discover(EV, {"webcasts": [{"type": "youtube", "channel": "@placeholder"}]}, [], meta_fn=lambda v: None)
    doc = S.merge(S.load(str(evd)), found); S.save(str(evd), doc)
    sid = found[0]["id"]
    lst = a.get(f"/events/{EV}/sources").json()
    assert lst["sources"][0]["origin"] == "tba" and lst["stream_source"] == sid
    # an admin source is deleted for real
    mine = a.post(f"/events/{EV}/sources", {"url": "twitch.tv/firstwa", "role": "wide", "primary": True}).json()
    assert a.delete(f"/events/{EV}/sources/{mine['id']}").status_code == 204
    assert all(x["id"] != mine["id"] for x in a.get(f"/events/{EV}/sources").json()["sources"])
    # a TBA one stays listed, off + dismissed; rediscovery keeps it that way; the recorder has no source
    assert a.delete(f"/events/{EV}/sources/{sid}").status_code == 204
    x = a.get(f"/events/{EV}/sources").json()
    assert x["sources"][0]["dismissed"] is True and x["sources"][0]["enabled"] is False and x["stream_source"] is None
    S.save(str(evd), S.merge(S.load(str(evd)), S.discover(EV, {"webcasts": [{"type": "youtube", "channel": "@placeholder"}]},
                                                          [], meta_fn=lambda v: None)))
    doc = S.load(str(evd))
    assert doc["sources"][0]["dismissed"] is True and doc["sources"][0]["enabled"] is False and S.livestream_url(doc) is None
    # its link can still be added by hand; the API can restore the TBA one
    assert a.post(f"/events/{EV}/sources", {"url": "https://www.youtube.com/@placeholder/live", "role": "wide"}).status_code == 201
    r = a.patch(f"/events/{EV}/sources/{sid}", {"dismissed": False, "enabled": True})
    assert r.status_code == 200 and "dismissed" not in r.json() and r.json()["enabled"] is True
    assert a.patch(f"/events/{EV}/sources/{sid}", {"url": "twitch.tv/zzz"}).status_code == 422     # TBA link: not editable
    assert a.delete(f"/events/{EV}/sources/nope").status_code == 404


def test_suggested_video_mode(tba, monkeypatch):
    a = tba
    j = a.get(f"/events/{EV}/video-mode").json()
    assert j["suggested"] == "livestream" and "offseason" in j["suggested_why"] and j["chosen"] is False
    assert a.put(f"/events/{EV}/video-mode", {"video_mode": "livestream"}).json()["chosen"] is True
    # an official event, or match videos on TBA: uploads + livestream
    monkeypatch.setitem(conftest.FAKE_TBA, "/event/2026wasam/matches", [{"key": "2026wasam_qm1", "videos": [
        {"type": "youtube", "key": "dQw4w9WgXcQ"}]}])
    monkeypatch.setitem(conftest.FAKE_TBA, "/event/2026wasam/simple", EVENTS[0])
    j = a.get("/events/2026wasam/video-mode").json()
    assert j["suggested"] == "auto" and "1 match" in j["suggested_why"]
