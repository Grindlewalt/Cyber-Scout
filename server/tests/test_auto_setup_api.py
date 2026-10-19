"""Automatic set-up of a stream on the Video page: GET/POST /events/{ev}/sources/{sid}/auto-setup, .../overlay.jpg,
.../accept (camera + layout + view like the wizard's accept, audited), PATCH dynamic (true / false / null = auto),
and what GET /sources and the livestream-only checklist say about it.  The candidate files are written by hand here
(the job itself: tests/test_auto_setup.py)."""
import json
import time

import pytest

EV, URL = "2026wagg", "https://www.twitch.tv/firstwa_red1"
CAM = {"W": 1920, "H": 1080, "K": [[1038.3, 0, 1008.1], [0, 1038.3, 536.6], [0, 0, 1]], "dist": [-0.38, 0.28, 0, 0, -0.1],
       "rvec": [0.0024, -2.8331, 1.2245], "tvec": [8.02, -3.61, 12.89], "calib": {"method": "dynpose auto_setup"}}


@pytest.fixture
def stream(admin, data, monkeypatch):
    monkeypatch.setenv("MEDIA", str(data / "media"))
    monkeypatch.delenv("LIVE_DIR", raising=False)
    s = admin.post(f"/events/{EV}/sources", {"url": URL, "role": "dynamic"}).json()
    return s["id"]


def recorded(data, sid, clip=True):
    d = data / "media" / "live" / EV / "319604392919"
    (d / "clips").mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps(dict(event=EV, source=sid, url=URL, started=1.0)))
    (d / "chunks.jsonl").write_text("")
    if clip: (d / "clips" / "tba-qm33.mp4").write_bytes(b"\0" * 200_000)
    return d


def candidate(data, sid, status="ok"):
    d = data / "events" / EV / "auto_setup" / sid
    d.mkdir(parents=True)
    st = dict(status=status, at=time.time(), media="tba-qm33.mp4", media_kind="clip", W=1920, H=1080, program_feed=True,
              feed=dict(program_feed=True, why="3 camera views and 8 cuts in 190 s"),
              fixed=dict(on_air=0.55, shots=4, tags=6, tag_rms_px=0.25) if status == "ok" else None,
              scoreboard=dict(layout="bottom", share=1.0), clock=[866, 988, 1052, 1064], field=[0, 142, 1920, 824],
              message=None if status == "ok" else "no locked-off view of the whole field in this clip")
    (d / "state.json").write_text(json.dumps(st))
    (d / "overlay.jpg").write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    if status == "ok":
        (d / f"camera_{sid}.json").write_text(json.dumps(dict(CAM, source=sid)))
        (d / f"view_{sid}.png").write_bytes(b"\x89PNGfake")
        (d / "layout.json").write_text(json.dumps({"version": 1, "processing_source": sid, "sources": {sid: dict(
            W=1920, H=1080, regions=[dict(id="a0", kind="scoreboard", box=[0, 824, 1920, 1080]),
                                     dict(id="aclock", kind="clock", box=[866, 988, 1052, 1064]),
                                     dict(id="afield", kind="field", box=[0, 142, 1920, 824])])}}))
    doc = json.loads((data / "events" / EV / "sources.json").read_text())
    next(s for s in doc["sources"] if s["id"] == sid).update(program_feed=True, program_feed_why=st["feed"]["why"])
    (data / "events" / EV / "sources.json").write_text(json.dumps(doc))
    return d


def test_roles(team, data, monkeypatch):
    monkeypatch.setenv("MEDIA", str(data / "media"))
    sid = team["admin"].post(f"/events/{EV}/sources", {"url": URL, "role": "dynamic"}).json()["id"]
    for role in ("strategist", "scout", "viewer"):
        c = team[role]
        assert c.get(f"/events/{EV}/sources/{sid}/auto-setup").status_code == 403
        assert c.post(f"/events/{EV}/sources/{sid}/auto-setup").status_code == 403
        assert c.post(f"/events/{EV}/sources/{sid}/auto-setup/accept").status_code == 403
        assert c.get(f"/events/{EV}/sources/{sid}/auto-setup/overlay.jpg").status_code == 403
        assert c.patch(f"/events/{EV}/sources/{sid}", {"dynamic": True}).status_code == 403
    assert team["viewer"].get(f"/events/{EV}/sources").status_code == 200


def test_run_waits_for_a_recording_then_queues_a_job(admin, data, stream):
    sid = stream
    j = admin.get(f"/events/{EV}/sources/{sid}/auto-setup").json()
    assert j["auto_setup"] is None and j["ready"] == {"media": None, "why": "nothing recorded from this stream yet"}
    r = admin.post(f"/events/{EV}/sources/{sid}/auto-setup")
    assert r.status_code == 409 and "nothing recorded" in r.json()["detail"]
    recorded(data, sid)
    assert admin.get(f"/events/{EV}/sources/{sid}/auto-setup").json()["ready"] == {"media": "clip", "why": None}
    r = admin.post(f"/events/{EV}/sources/{sid}/auto-setup")
    assert r.status_code == 200 and r.json()["kind"] == "auto_setup" and r.json()["state"] == "queued"   # the worker runs it
    a = admin.get(f"/events/{EV}/sources/{sid}/auto-setup").json()["auto_setup"]
    assert a["running"] and a["job"]["state"] == "queued"
    assert admin.post(f"/events/{EV}/sources/{sid}/auto-setup").status_code == 409                    # already running
    src = next(s for s in admin.get(f"/events/{EV}/sources").json()["sources"] if s["id"] == sid)
    assert src["auto_setup_ok"] and src["auto_setup"]["running"] and src["calibration"] is None
    # a per-match upload family cannot be set up this way
    doc = json.loads((data / "events" / EV / "sources.json").read_text())
    doc["sources"].append(dict(id="tba-uploads-x", origin="tba", kind="per_match", role="wide", enabled=True))
    (data / "events" / EV / "sources.json").write_text(json.dumps(doc))
    assert admin.post(f"/events/{EV}/sources/tba-uploads-x/auto-setup").status_code == 422
    assert admin.get(f"/events/{EV}/sources/nope/auto-setup").status_code == 404


def test_accept_saves_the_candidate_and_is_audited(admin, data, stream):
    sid, evd = stream, data / "events" / EV
    assert admin.post(f"/events/{EV}/sources/{sid}/auto-setup/accept").status_code == 409             # nothing yet
    candidate(data, sid)
    j = admin.get(f"/events/{EV}/sources/{sid}/auto-setup").json()["auto_setup"]
    assert j["status"] == "ok" and j["has_overlay"] and j["program_feed"] is True and not j.get("accepted_at")
    r = admin.get(f"/events/{EV}/sources/{sid}/auto-setup/overlay.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    C = {c["key"]: c for c in admin.get(f"/events/{EV}/video-mode").json()["checks"]}
    assert C["calibration"]["state"] == "bad" and "Accept" in C["calibration"]["detail"]
    assert C["source"]["state"] == "ok" and "every camera" in C["source"]["detail"]          # a broadcast, detected feed
    r = admin.post(f"/events/{EV}/sources/{sid}/auto-setup/accept")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["camera"] == "camera.json" and out["layout"] and out["view"] == f"view_{sid}.png"
    assert out["auto_setup"]["accepted_by"] == "Coach"
    cam = json.loads((evd / "camera.json").read_text())
    assert cam["source"] == sid and cam["calib"]["accepted_by"] == "Coach"
    assert json.loads((evd / "layout.json").read_text())["sources"][sid]["regions"][2]["kind"] == "field"
    assert (evd / f"view_{sid}.png").read_bytes() == b"\x89PNGfake"
    e = [x for x in admin.get(f"/audit?event={EV}").json()["entries"] if x["action"] == "sources.auto_setup_accept"]
    assert e and e[0]["target"] == sid
    src = next(s for s in admin.get(f"/events/{EV}/sources").json()["sources"] if s["id"] == sid)
    assert src["calibration"] == "camera.json" and src["auto_setup"]["accepted_file"] == "camera.json"
    C = {c["key"]: c for c in admin.get(f"/events/{EV}/video-mode").json()["checks"]}
    assert C["calibration"]["state"] == "ok" and C["layout"]["state"] == "ok" and C["clock"]["state"] == "ok"


def test_no_camera_candidate_cannot_be_accepted(admin, data, stream):
    candidate(data, stream, status="no_camera")
    r = admin.post(f"/events/{EV}/sources/{stream}/auto-setup/accept")
    assert r.status_code == 409 and "locked-off" in r.json()["detail"]
    C = {c["key"]: c for c in admin.get(f"/events/{EV}/video-mode").json()["checks"]}
    assert "calibrate by hand" in C["calibration"]["detail"]


def test_dynamic_patch_true_false_auto(admin, data, stream):
    sid = stream
    src = lambda: next(s for s in admin.get(f"/events/{EV}/sources").json()["sources"] if s["id"] == sid)
    assert src()["dynamic_on"] is False and "dynamic" not in src()
    candidate(data, sid, status="no_camera")                                           # detected: program feed
    assert src()["dynamic_on"] is True
    r = admin.patch(f"/events/{EV}/sources/{sid}", {"dynamic": False})
    assert r.status_code == 200 and r.json()["dynamic"] is False and r.json()["dynamic_on"] is False
    assert admin.patch(f"/events/{EV}/sources/{sid}", {"label": "Red 1"}).json()["dynamic"] is False   # untouched
    r = admin.patch(f"/events/{EV}/sources/{sid}", {"dynamic": None})
    assert r.status_code == 200 and "dynamic" not in r.json() and r.json()["dynamic_on"] is True
    assert admin.patch(f"/events/{EV}/sources/{sid}", {"dynamic": "maybe"}).status_code == 422
    doc = json.loads((data / "events" / EV / "sources.json").read_text())
    assert "dynamic" not in next(s for s in doc["sources"] if s["id"] == sid)
    e = [x for x in admin.get(f"/audit?event={EV}").json()["entries"] if x["action"] == "sources.dynamic"]
    assert len(e) == 2 and e[0]["target"] == sid
