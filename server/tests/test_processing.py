"""Distributed processing API (server/routers/processing.py): claims shared with the worker (live/claims.py),
order, collisions, stale claims, clip download (Range), uploads (validation, limits, staging), complete, release,
heartbeat, pipeline code + model, auth."""
import gzip, io, json, os, sys, tarfile, time

import pytest

from server import db as DB
from server.config import cfg

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "live"))
import claims as CL  # noqa: E402

EV = "2026zzpt"
P = "/processing"


def _match(n, t):
    teams = [f"frc{10 * n + i}" for i in range(6)]
    return {"key": f"{EV}_qm{n}", "event_key": EV, "comp_level": "qm", "set_number": 1, "match_number": n,
            "alliances": {"red": {"team_keys": teams[:3], "score": 50}, "blue": {"team_keys": teams[3:], "score": 40}},
            "time": t, "actual_time": t, "score_breakdown": {"red": {}, "blue": {}}}


@pytest.fixture
def setup(admin, data, monkeypatch):
    """A followed event with 3 played quals, each with a trackable livestream clip."""
    media = data / "media"
    monkeypatch.setenv("DATA", str(data))
    monkeypatch.delenv("LIVE_DIR", raising=False)
    monkeypatch.delenv("LAYOUT", raising=False)
    monkeypatch.setattr(cfg, "MEDIA", str(media))
    c = DB.connect()
    c.execute("INSERT INTO events(key,name,year,start_date,added_at) VALUES(?,?,?,?,?)",
              (EV, "Test Event", 2026, "2026-10-17", time.time()))
    c.close()
    evd = data / "events" / EV
    evd.mkdir(parents=True)
    (evd / "layout.json").write_text(json.dumps({"version": 1, "processing_source": "yt1", "sources": {
        "yt1": {"W": 1920, "H": 1080, "role": "wide", "regions": [{"id": "f", "kind": "field", "box": [0, 100, 1920, 1000]}]}}}))
    (evd / "camera.json").write_text(json.dumps({"source": "yt1", "K": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}))
    (evd / "view_yt1.png").write_bytes(b"\x89PNG fake view")
    ms = [_match(n, 1_790_000_000 + 600 * n) for n in (1, 2, 3)]
    (data / "tba").mkdir()
    (data / "tba" / f"event__{EV}__matches.json").write_text(json.dumps({"etag": None, "fetched": time.time(), "data": ms}))
    (data / "tba" / f"event__{EV}__teams__simple.json").write_text(json.dumps(
        {"fetched": time.time(), "data": [{"key": t} for m in ms for a in ("red", "blue") for t in m["alliances"][a]["team_keys"]]}))
    vd = media / "live" / EV / "vid1"
    (vd / "matches").mkdir(parents=True)
    (vd / "clips").mkdir()
    (vd / "meta.json").write_text(json.dumps({"source": "yt1", "video_id": "vid1"}))
    for n in (1, 2, 3):
        clip = vd / "clips" / f"qm{n}.mp4"
        clip.write_bytes(bytes(range(256)) * 40 + f"clip{n}".encode())
        (vd / "matches" / f"qm{n}.json").write_text(json.dumps({
            "id": f"qm{n}", "comp_level": "qm", "number": n, "match_name": f"Qualification {n}",
            "teams": {"red": [10 * n, 10 * n + 1, 10 * n + 2], "blue": [10 * n + 3, 10 * n + 4, 10 * n + 5]},
            "score": {"red": 50, "blue": 40}, "clip": str(clip), "auto_start_in_clip": 12.5,
            "t_start_utc": "2026-10-17T18:00:00.00Z", "flags": []}))
    return {"data": data, "evd": evd, "media": media, "ms": ms}


def claim(c, pid="lap1", **kw):
    r = c.post(P + "/claim", {"processor": pid, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def result(n=1, **kw):
    return dict({"key": f"{EV}_qm{n}", "video": {"id": "live-vid1-qm1", "title": "Livestream"}, "feed_ok": 0.97,
                 "source": "livestream"}, **kw)


def put(c, key, kind, obj, pid="lap1", gz=False, raw=None):
    body = raw if raw is not None else json.dumps(obj).encode()
    h = {"X-CSRF-Token": c.tc.cookies.get("cs_csrf"), "Content-Type": "application/json"}
    if gz:
        body = gzip.compress(body)
        h["Content-Encoding"] = "gzip"
    return c.tc.put(f"/app/v1{P}/{EV}/{key}/results/{kind}?processor={pid}", content=body, headers=h)


# ------------------------------------------------------------------ auth
def test_needs_admin(team, setup):
    for role in ("viewer", "scout", "strategist"):
        assert team[role].post(P + "/claim", {"processor": "x"}).status_code == 403
        assert team[role].get(P + "/pipeline").status_code == 403
        assert team[role].get(P + "/status").status_code == 403
    assert team["admin"].post(P + "/claim", {"processor": "x"}, csrf=False).status_code == 403   # CSRF


def test_anonymous_401(app, setup):
    from server.tests.conftest import new_client
    client = new_client(app)
    client.get("/session")
    assert client.post(P + "/claim", {"processor": "x"}).status_code == 401
    assert client.get(f"{P}/{EV}/{EV}_qm1/clip?processor=x").status_code == 401
    assert client.get(P + "/pipeline/model.onnx").status_code == 401


def test_bad_ids(admin, setup):
    assert admin.post(P + "/claim", {"processor": "../x"}).status_code == 422
    assert admin.post(P + "/claim", {"processor": "ok", "events": ["../etc"]}).status_code == 422
    assert admin.post(P + "/claim", {"processor": "ok", "bogus": 1}).status_code == 422
    assert admin.get(f"{P}/{EV}/2026other_qm1/clip?processor=ok").status_code == 422     # key of another event
    assert admin.get(f"{P}/{EV}/{EV}_qm1x/clip?processor=ok").status_code == 422
    assert admin.post(f"{P}/{EV}/..%2F..%2Fx/release", {"processor": "ok"}).status_code in (404, 422)


# ------------------------------------------------------------------ claims + order
def test_claim_time_order_then_priority(admin, setup):
    j = claim(admin)["job"]
    assert j["key"] == f"{EV}_qm1"
    assert CL.read(str(setup["data"]), EV, f"{EV}_qm1")["by"] == j["by"] == "app-u1-lap1"
    (setup["evd"] / "priority.json").write_text(json.dumps({"order": [f"{EV}_qm3"]}))
    assert claim(admin, "lap2")["job"]["key"] == f"{EV}_qm3"
    assert claim(admin, "lap3")["job"]["key"] == f"{EV}_qm2"
    r = claim(admin, "lap4")
    assert r["job"] is None and r["counts"]["claimed"] == 3 and "nothing to track" in r["why"]


def test_claim_job_contents(admin, setup):
    j = claim(admin)["job"]
    assert j["match"]["key"] == f"{EV}_qm1" and j["match"]["score_breakdown"] == {"red": {}, "blue": {}}
    assert j["live"]["start"] == 12.5 and j["live"]["teams"]["red"] == ["frc10", "frc11", "frc12"]
    assert j["live"]["source"] == "yt1" and j["live"]["layout"]["processing_source"] == "yt1"
    assert j["camera"]["name"] == "camera.json" and j["camera"]["data"]["source"] == "yt1"
    assert j["event"]["teams"][:3] == ["frc10", "frc11", "frc12"] and j["event"]["key"] == f"{EV}_qm1"
    assert {"key": f"{EV}_qm2", "red": ["frc20", "frc21", "frc22"], "blue": ["frc23", "frc24", "frc25"]} in j["event"]["matches"]
    assert j["clip"]["url"] == f"/app/v1{P}/{EV}/{EV}_qm1/clip?processor=lap1" and j["clip"]["size"] == 256 * 40 + 5
    assert j["view_ref"]["url"].endswith("/view.png?processor=lap1")
    assert j["heartbeat_s"] == 60 and j["claim_s"] == CL.CLAIM_S and j["pipeline"].startswith("p-")
    s = json.dumps(j)
    assert "k" * 60 not in s and "tba_key" not in s       # no secrets


def test_live_claim_elsewhere_is_skipped(admin, setup):
    CL.claim(str(setup["data"]), EV, f"{EV}_qm1", "m2")        # the server's own worker
    r = claim(admin)
    assert r["job"]["key"] == f"{EV}_qm2" and r["counts"]["claimed"] == 1
    # and the worker sees the processor's claim
    assert CL.claimed_elsewhere(str(setup["data"]), EV, f"{EV}_qm2", "m2")
    assert not CL.claim(str(setup["data"]), EV, f"{EV}_qm2", "m2")


def test_stale_claim_is_taken_over(admin, setup):
    p = setup["evd"] / "claims" / f"{EV}_qm1.json"
    p.parent.mkdir()
    p.write_text(json.dumps({"by": "helper-old", "at": time.time() - CL.CLAIM_S - 5}))
    assert claim(admin)["job"]["key"] == f"{EV}_qm1"
    assert json.loads(p.read_text())["by"] == "app-u1-lap1"


def test_unreadable_fresh_claim_counts_as_held(setup):
    d = str(setup["data"])
    p = setup["evd"] / "claims" / f"{EV}_qm1.json"
    p.parent.mkdir()
    p.write_text("")                                          # another processor between O_EXCL and its write
    assert CL.claimed_elsewhere(d, EV, f"{EV}_qm1", "m2") and not CL.claim(d, EV, f"{EV}_qm1", "m2")
    os.utime(p, (time.time() - 60, time.time() - 60))
    assert CL.claim(d, EV, f"{EV}_qm1", "m2")


def test_skips_tracked_failed_and_waiting(admin, setup):
    (setup["evd"] / "matches").mkdir()
    (setup["evd"] / "matches" / f"{EV}_qm1.json").write_text("{}")
    (setup["evd"] / "status.json").write_text(json.dumps({f"{EV}_qm2": {"state": "failed"}}))
    os.remove(setup["media"] / "live" / EV / "vid1" / "clips" / "qm3.mp4")           # no clip: waiting
    r = claim(admin)
    assert r["job"] is None and r["counts"] == {"tracked": 1, "failed": 1, "waiting": 1}


def test_skip_list_and_events_filter(admin, setup):
    assert claim(admin, skip=[f"{EV}_qm1"])["job"]["key"] == f"{EV}_qm2"
    assert claim(admin, "lap2", events=["2026other"])["job"] is None


def test_untrackable_stream_not_handed_out(admin, setup):
    os.remove(setup["evd"] / "camera.json")                    # stream camera not calibrated
    assert claim(admin)["job"] is None


def test_outdated_pipeline_409(admin, setup):
    r = admin.post(P + "/claim", {"processor": "lap1", "pipeline": "p-000000000000"})
    assert r.status_code == 409 and "pipeline changed" in r.json()["detail"]


# ------------------------------------------------------------------ clip, heartbeat
def test_clip_download_resumes(admin, setup):
    j = claim(admin)["job"]
    whole = admin.tc.get(j["clip"]["url"])
    assert whole.status_code == 200 and len(whole.content) == j["clip"]["size"]
    part = admin.tc.get(j["clip"]["url"], headers={"Range": "bytes=100-"})
    assert part.status_code == 206 and part.content == whole.content[100:]
    assert admin.tc.get(j["view_ref"]["url"]).content == b"\x89PNG fake view"
    # another processor (or a released claim) can't download it
    assert admin.tc.get(j["clip"]["url"].replace("lap1", "lap2")).status_code == 409
    assert admin.post(f"{P}/{EV}/{EV}_qm1/release", {"processor": "lap1"}).json() == {"released": True}
    assert admin.tc.get(j["clip"]["url"]).status_code == 409


def test_heartbeat_refreshes_and_detects_loss(admin, setup):
    j = claim(admin)["job"]
    p = setup["evd"] / "claims" / f"{EV}_qm1.json"
    p.write_text(json.dumps({"by": j["by"], "at": time.time() - 1000}))
    r = admin.post(f"{P}/{EV}/{EV}_qm1/heartbeat", {"processor": "lap1", "progress": 0.4, "stage": "processing"})
    assert r.status_code == 200 and time.time() - json.loads(p.read_text())["at"] < 5
    st = admin.get(P + "/status").json()["claims"]
    assert st == [dict(st[0], ev=EV, key=f"{EV}_qm1", by="app-u1-lap1", progress=0.4, stage="processing", mine=True)]
    p.write_text(json.dumps({"by": "m2", "at": time.time()}))           # taken over
    assert admin.post(f"{P}/{EV}/{EV}_qm1/heartbeat", {"processor": "lap1"}).status_code == 409
    assert admin.post(f"{P}/{EV}/{EV}_qm1/heartbeat", {"processor": "lap1", "stage": "<b>"}).status_code in (409, 422)


# ------------------------------------------------------------------ upload + complete
def test_upload_validation(admin, setup):
    claim(admin)
    k = f"{EV}_qm1"
    assert put(admin, k, "bogus", {}).status_code == 404
    assert put(admin, k, "matches", None, raw=b"{not json").status_code == 422
    assert put(admin, k, "matches", [1, 2]).status_code == 422
    assert put(admin, k, "matches", result(2)).status_code == 422                 # another match's result
    assert put(admin, k, "matches", {"key": k}).status_code == 422                # not a tracking result
    assert put(admin, k, "fuel", {"nope": 1}).status_code == 422
    assert put(admin, k, "matches", result(), pid="lap2").status_code == 409     # not this processor's claim
    big = b'{"key": "' + b"x" * (21 * 1024 * 1024) + b'"}'
    assert put(admin, k, "shots", None, raw=big).status_code == 413
    bomb = gzip.compress(b'{"a": "' + b"0" * (65 * 1024 * 1024) + b'"}')
    h = {"X-CSRF-Token": admin.tc.cookies.get("cs_csrf"), "Content-Encoding": "gzip"}
    assert admin.tc.put(f"/app/v1{P}/{EV}/{k}/results/shots?processor=lap1", content=bomb, headers=h).status_code == 413
    h["Content-Encoding"] = "br"
    assert admin.tc.put(f"/app/v1{P}/{EV}/{k}/results/shots?processor=lap1", content=b"{}", headers=h).status_code == 415
    assert put(admin, k, "matches", result(), gz=True).status_code == 200
    assert not (setup["evd"] / "matches").exists()                               # staged until complete


def test_complete_publishes_matches_last_and_keeps_clip(admin, setup):
    claim(admin)
    k = f"{EV}_qm1"
    url = f"{P}/{EV}/{k}/complete"
    assert admin.post(url, {"processor": "lap1"}).status_code == 409             # nothing uploaded
    assert put(admin, k, "fuel", {"tracks": []}).status_code == 200
    assert put(admin, k, "shots", {"shots": [1]}, gz=True).status_code == 200
    assert put(admin, k, "analytics", {"a": 1}).status_code == 200
    assert put(admin, k, "matches", result()).status_code == 200
    r = admin.post(url, {"processor": "lap1", "seconds": 201.5})
    assert r.status_code == 200, r.text
    assert r.json()["files"] == ["fuel", "shots", "analytics", "matches"] and r.json()["media"]["ok"]
    evd = setup["evd"]
    assert json.loads((evd / "matches" / f"{k}.json").read_text())["feed_ok"] == 0.97
    assert json.loads((evd / "shots" / f"{k}.json").read_text()) == {"shots": [1]}
    clip = setup["media"] / "live" / EV / "vid1" / "clips" / "qm1.mp4"
    assert (setup["media"] / EV / f"{k}.mp4").read_bytes() == clip.read_bytes()
    assert CL.read(str(setup["data"]), EV, k) is None                            # released
    assert not (setup["data"] / "processing" / EV / k).exists()
    assert admin.post(url, {"processor": "lap1"}).json()["already"] is True      # answer lost -> retried
    acts = [e["action"] for e in admin.get("/audit?action=processing").json()["entries"]]
    assert acts == ["processing.upload", "processing.claim"]
    e = admin.get("/audit?action=processing.upload").json()["entries"][0]
    assert e["event"] == EV and e["target"] == f"{EV}/{k}" and e["after"]["seconds"] == 201.5
    assert claim(admin)["job"]["key"] == f"{EV}_qm2"                             # qm1 is done now


def test_complete_refused_when_claim_lost_or_done_elsewhere(admin, setup):
    claim(admin)
    k = f"{EV}_qm1"
    assert put(admin, k, "matches", result()).status_code == 200
    p = setup["evd"] / "claims" / f"{k}.json"
    p.write_text(json.dumps({"by": "m2", "at": time.time()}))
    assert admin.post(f"{P}/{EV}/{k}/complete", {"processor": "lap1"}).status_code == 409
    assert not (setup["evd"] / "matches").exists()
    p.write_text(json.dumps({"by": "app-u1-lap1", "at": time.time()}))
    (setup["evd"] / "matches").mkdir()
    (setup["evd"] / "matches" / f"{k}.json").write_text('{"key": "worker"}')
    r = admin.post(f"{P}/{EV}/{k}/complete", {"processor": "lap1"})
    assert r.status_code == 409 and "another processor" in r.json()["detail"]
    assert json.loads((setup["evd"] / "matches" / f"{k}.json").read_text()) == {"key": "worker"}


def test_release_failures_leave_match_to_worker(admin, setup):
    k = f"{EV}_qm1"
    for pid in ("lap1", "lap2"):
        assert claim(admin, pid)["job"]["key"] == k
        r = admin.post(f"{P}/{EV}/{k}/release", {"processor": pid, "failed": True, "reason": "no tags\x00"})
        assert r.json() == {"released": True}
    assert claim(admin, "lap3")["job"]["key"] == f"{EV}_qm2"
    assert admin.post(f"{P}/{EV}/{k}/release", {"processor": "lap9"}).json() == {"released": False}
    e = admin.get("/audit?action=processing.release").json()["entries"][0]
    assert e["after"] == {"by": "app-u1-lap2", "failed": True, "reason": "no tags"}


# ------------------------------------------------------------------ pipeline
def test_pipeline_code_and_model(admin, setup):
    p = admin.get(P + "/pipeline").json()
    assert p["version"].startswith("p-") and p["model"] is None and "numpy==2.5.4" in p["requirements"]
    r = admin.tc.get(p["code"]["url"])
    assert r.status_code == 200 and len(r.content) == p["code"]["size"]
    with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as t:
        names = set(t.getnames())
    assert {"runner.py", "requirements.txt", "PIPELINE.json", "live/worker.py", "live/claims.py"} <= names
    assert not any("__pycache__" in n or n.startswith("live/train/") or n.startswith(("/", "..")) for n in names)
    assert admin.tc.get(p["code"]["url"], headers={"Range": "bytes=10-"}).content == r.content[10:]
    assert admin.get(P + "/pipeline/code.tar.gz?v=p-000000000000").status_code == 409
    assert admin.get(P + "/pipeline").json()["version"] == p["version"]          # stable
    assert admin.get(P + "/pipeline/model.onnx").status_code == 404
    (setup["data"] / "models").mkdir()
    (setup["data"] / "models" / "robot.onnx").write_bytes(b"onnx" * 100)
    m = admin.get(P + "/pipeline").json()["model"]
    assert m["size"] == 400 and admin.tc.get(m["url"]).content == b"onnx" * 100


def test_never_handed_a_match_it_already_holds(admin, setup):
    k1 = f"{EV}_qm1"
    assert claim(admin)["job"]["key"] == k1
    r = claim(admin)                                          # asks again while holding qm1 (e.g. a prefetch)
    assert r["job"]["key"] == f"{EV}_qm2" and r["counts"]["yours"] == 1
    p = setup["evd"] / "claims" / f"{k1}.json"
    p.write_text(json.dumps({"by": "app-u1-lap1", "at": time.time() - CL.CLAIM_S - 5}))
    r = claim(admin)                                          # not even its own stale claim
    assert r["job"]["key"] == f"{EV}_qm3" and r["counts"]["yours"] == 2
    assert claim(admin, "lap2")["job"]["key"] == k1           # someone else takes the stale one over
    assert admin.post(f"{P}/{EV}/{k1}/release", {"processor": "lap2", "reason": "restarted"}).json()["released"]
    assert claim(admin, "lap2")["job"]["key"] == k1           # released, then a fresh claim: fine


def test_clip_outside_media_is_not_handed_out(admin, setup):
    rec = setup["media"] / "live" / EV / "vid1" / "matches" / "qm1.json"
    r = json.loads(rec.read_text())
    os.remove(setup["media"] / "live" / EV / "vid1" / "clips" / "qm1.mp4")
    outside = setup["data"] / "secret.mp4"
    outside.write_bytes(b"x" * 10)
    r["clip"] = str(outside)
    rec.write_text(json.dumps(r))
    assert claim(admin)["job"]["key"] == f"{EV}_qm2"
