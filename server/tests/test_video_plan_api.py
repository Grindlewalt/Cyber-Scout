"""GET/PUT/POST /events/{ev}/video-plan...: the per-match video plan (live/videoplan.py) -- viewer reads,
admin-only overrides and checks, ids validated, the plan rebuilt offline after an override."""
import json

import pytest

from server.routers import sources as R

from .conftest import GOOD_KEY

EV = "2026wasam"


def _rec(vid, ty, title, ch="FIRST Washington"):
    return dict(id=vid, type=ty, title=title, channel=ch, duration=214.0, height=1080, conf=0.9, classifier=R.VP.CLASSIFIER,
                hashes=[vid[0] * 16 if vid[0] in "0123456789abcdef" else "1" * 16] * 6, dhashes=["0" * 16] * 5,
                times=[30.0, 60, 90, 120, 150, 180], at=1.0)


@pytest.fixture
def seeded(data):
    if R.VP is None or R.SRC is None:
        pytest.skip("live/videoplan.py not importable")
    recs = {"wwwwwwwwwww": _rec("wwwwwwwwwww", "wide", "Wide Match 4"), "aaaaaaaaaaa": _rec("aaaaaaaaaaa", "stations", "Match 4"),
            "bbbbbbbbbbb": _rec("bbbbbbbbbbb", "broadcast", "Qualification 4 - PNW", "FIRSTRoboticsCompetition")}
    (data / "cache").mkdir(parents=True, exist_ok=True)
    (data / "cache" / "vclass.json").write_text(json.dumps(recs))
    evd = data / "events" / EV
    evd.mkdir(parents=True, exist_ok=True)
    ms = [dict(key=f"{EV}_qm4", alliances={"red": {"score": 20}}, videos=[{"type": "youtube", "key": v} for v in recs])]
    plan = R.VP.build_plan(EV, str(evd), ms, {"sources": []}, recs)
    R.VP.save_plan(str(evd), plan)
    return evd


def test_plan_read_roles(team, seeded):
    for role in ("viewer", "scout", "admin"):
        r = team[role].get(f"/events/{EV}/video-plan")
        assert r.status_code == 200, (role, r.text)
    j = r.json()
    e = j["plan"]["matches"][f"{EV}_qm4"]
    assert e["track"] == "wwwwwwwwwww" and e["stations"] == "aaaaaaaaaaa" and j["plan"]["summary"]["wide"] == 1
    assert any(g["type"] == "wide" and g["has_layout"] is False for g in j["groups"])


def test_plan_read_needs_login(client, seeded):
    assert client.get(f"/events/{EV}/video-plan").status_code in (401, 403)


def test_plan_empty_before_first_build(admin, data):
    if R.VP is None:
        pytest.skip("live/videoplan.py not importable")
    j = admin.get(f"/events/{EV}/video-plan").json()
    assert j["plan"] is None and j["overrides"] == {}


def test_override_admin_only_and_rebuilds(team, seeded):
    url = f"/events/{EV}/video-plan/matches/{EV}_qm4"
    for role in ("viewer", "scout", "strategist"):
        assert team[role].put(url, {"pin": "bbbbbbbbbbb"}).status_code == 403
    r = team["admin"].put(url, {"pin": "https://youtu.be/bbbbbbbbbbb", "exclude": ["aaaaaaaaaaa"]})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["override"]["pin"] == "bbbbbbbbbbb" and j["override"]["by"] == "Coach"
    assert j["entry"]["track"] == "bbbbbbbbbbb" and j["entry"]["pinned"] and j["entry"]["limited"]
    assert j["entry"]["stations"] is None                                    # the excluded stations video
    doc = json.loads((seeded / "sources.json").read_text())
    assert doc["match_overrides"][f"{EV}_qm4"]["exclude"] == ["aaaaaaaaaaa"]
    plan = json.loads((seeded / "video_plan.json").read_text())
    assert plan["matches"][f"{EV}_qm4"]["track"] == "bbbbbbbbbbb"
    # back to automatic
    j = team["admin"].put(url, {}).json()
    assert j["override"] is None and j["entry"]["track"] == "wwwwwwwwwww"


@pytest.mark.parametrize("body", [{"pin": "-o"}, {"pin": "javascript:alert(1)"}, {"pin": "https://twitch.tv/x"},
                                  {"exclude": ["../../etc"]}, {"add": ["http://10.0.0.5/x.mp4"]}])
def test_override_rejects_bad_ids(admin, seeded, body):
    assert admin.put(f"/events/{EV}/video-plan/matches/{EV}_qm4", body).status_code == 422


def test_override_bad_match(admin, seeded):
    assert admin.put(f"/events/{EV}/video-plan/matches/2026other_qm1", {}).status_code == 404
    assert admin.put(f"/events/{EV}/video-plan/matches/{EV}_qm4;rm", {}).status_code in (404, 422)


def test_check_job_admin_only(team, seeded, monkeypatch):
    calls = []
    monkeypatch.setattr(R.SRC, "submit", lambda data, ev, kind, params, by=None, inline=None:
                        calls.append((ev, kind, params)) or dict(id="ab12cd34ef", kind=kind, state="queued", created=1.0))
    assert team["viewer"].post(f"/events/{EV}/video-plan/check", {}).status_code == 403
    assert team["admin"].post(f"/events/{EV}/video-plan/check", {}).status_code == 409       # no TBA key yet
    assert team["admin"].put("/settings/tba-key", {"key": GOOD_KEY}).status_code == 200
    from . import conftest
    monkeypatch.setitem(conftest.FAKE_TBA, f"/event/{EV}/matches", [
        dict(key=f"{EV}_qm4", comp_level="qm", set_number=1, match_number=4, alliances={"red": {"score": 3}, "blue": {"score": 1}},
             videos=[{"type": "youtube", "key": "wwwwwwwwwww"}])])
    r = team["admin"].post(f"/events/{EV}/video-plan/check", {"only": ["wwwwwwwwwww"]})
    assert r.status_code == 200, r.text
    ev, kind, params = calls[-1]
    assert kind == "plan" and params["only"] == ["wwwwwwwwwww"] and params["_matches"][0]["alliances"]["red"]["score"] == 3


def test_layout_on_a_detected_type_group(admin, seeded):
    gid = R.VP.group_id("wide", "FIRST Washington")
    r = admin.put(f"/events/{EV}/layout/{gid}", {"W": 1920, "H": 1080, "regions": [{"kind": "clock", "box": [900, 60, 1000, 110]}]})
    assert r.status_code == 200, r.text
    lay = r.json()
    assert gid in lay["sources"] and lay["processing_source"] == gid             # the wide type is what the tracker reads
    assert admin.put(f"/events/{EV}/layout/auto-wide-nope", {"W": 10, "H": 10, "regions": []}).status_code == 404
