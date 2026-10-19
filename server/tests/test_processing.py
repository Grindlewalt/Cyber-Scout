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


def test_job_carries_the_dynamic_view_flag(admin, setup):
    """worker.dynamic_for(live) reads live["dynamic"] (liveingest: the source's dynamic / detected program feed)."""
    assert claim(admin)["job"]["live"]["dynamic"] is False
    (setup["evd"] / "sources.json").write_text(json.dumps({"version": 1, "event": EV, "sources": [
        {"id": "yt1", "kind": "livestream", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "role": "wide",
         "enabled": True, "dynamic": True}]}))
    assert claim(admin, "lap2")["job"]["live"]["dynamic"] is True


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


# ------------------------------------------------------------------ runtimes, speed test, benchmark sample
def test_pipeline_runtimes(admin, setup):
    p = admin.get(P + "/pipeline").json()
    rt = p["runtimes"]
    assert rt["cuda"][0].startswith("onnxruntime-gpu[cuda,cudnn]==") and not any(r.startswith("onnxruntime==") for r in rt["cuda"])
    assert rt["coreml"] == p["requirements"] and any(r.startswith("onnxruntime==") for r in rt["coreml"])
    assert {r.split("==")[0] for r in rt["clip"]} == {"numpy", "opencv-python-headless", "av"}
    assert any(r.startswith("imageio-ffmpeg==") for r in p["clip_extras"]) and p["ytdlp_requirements"]
    r = admin.tc.get(p["code"]["url"])
    with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as t:
        meta = json.load(t.extractfile("PIPELINE.json"))
        assert "live/requirements-ytdlp.txt" in t.getnames()
    assert meta["runtimes"] == rt and meta["version"] == p["version"]


def test_speedtest(team, admin, setup):
    r = admin.tc.get(f"/app/v1{P}/speedtest?bytes=300000")
    assert r.status_code == 200 and len(r.content) == 300000 and r.headers["cache-control"] == "no-store"
    assert len(gzip.compress(r.content)) > 290000                         # incompressible
    assert admin.get(P + "/speedtest?bytes=99999999").status_code == 422
    h = {"X-CSRF-Token": admin.tc.cookies.get("cs_csrf")}
    r = admin.tc.post(f"/app/v1{P}/speedtest", content=b"x" * 200000, headers=h)
    assert r.status_code == 200 and r.json()["bytes"] == 200000
    assert admin.tc.post(f"/app/v1{P}/speedtest", content=b"x" * (9 << 20), headers=h).status_code == 413
    assert team["scout"].get(P + "/speedtest").status_code == 403
    assert not admin.get("/audit?action=http").json()["entries"]          # not logged


def _mp4(n, faststart=True):
    box = lambda k, b: (8 + len(b)).to_bytes(4, "big") + k + b
    head = box(b"ftyp", b"isom" + b"\0" * 8)
    body = box(b"mdat", bytes(range(256)) * (n // 256))
    return head + (box(b"moov", b"\0" * 64) + body if faststart else body + box(b"moov", b"\0" * 64))


def test_benchmark_sample(admin, setup, monkeypatch):
    from server.routers import processing as PR
    monkeypatch.setattr(PR, "SAMPLE_BYTES", 4096)
    assert admin.get(P + "/sample").json()["clip"] is None                 # the fixture's clips are tiny
    clips = setup["media"] / "live" / EV / "vid1" / "clips"
    (clips / "old.mp4").write_bytes(_mp4(20000, faststart=False))          # index at the end: not decodable
    assert admin.get(P + "/sample").json()["clip"] is None
    big = _mp4(30000)
    (clips / "qm9.mp4").write_bytes(big)
    s = admin.get(P + "/sample").json()
    assert s["clip"]["size"] == 4096 and s["full_bytes"] == len(big) and s["of"] == f"{EV}/qm9.mp4"
    r = admin.tc.get(s["clip"]["url"])
    assert r.status_code == 200 and r.content == big[:4096]
    import hashlib
    assert hashlib.sha256(r.content).hexdigest() == s["clip"]["sha256"]
    r = admin.tc.get(s["clip"]["url"], headers={"Range": "bytes=1000-"})
    assert r.status_code == 206 and r.content == big[1000:4096] and r.headers["content-range"] == "bytes 1000-4095/4096"
    assert admin.get(f"{P}/sample.mp4?ev={EV}&name=..%2Fx.mp4").status_code == 422
    assert admin.get(f"{P}/sample.mp4?ev={EV}&name=nope.mp4").status_code == 404


# ------------------------------------------------------------------ Clip only: streams + chunked upload
@pytest.fixture
def streams(setup):
    """Two livestream sources (yt1 = the calibrated wide stream, cam2 = a second view) and 3 recent matches."""
    evd = setup["evd"]
    (evd / "sources.json").write_text(json.dumps({"version": 1, "event": EV, "sources": [
        {"id": "yt1", "kind": "livestream", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "role": "wide",
         "primary": True, "enabled": True, "label": "Main stream"},
        {"id": "cam2", "kind": "livestream", "url": "https://www.twitch.tv/somecam", "role": "dynamic",
         "enabled": True, "label": "Station cam"}]}))
    now = time.time()
    ms = setup["ms"] + [_match(n, now - t) for n, t in ((4, 800), (5, 400), (6, 100))]
    p = setup["data"] / "tba" / f"event__{EV}__matches.json"
    p.write_text(json.dumps({"etag": None, "fetched": time.time(), "data": ms}))
    return dict(setup, now=now)


def sclaim(c, pid="lap1", **kw):
    r = c.post(P + "/streams/claim", {"processor": pid, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_stream_claim_prefers_a_stream_the_server_does_not_record(admin, streams):
    (streams["evd"] / "livestream.json").write_text(json.dumps(
        {"event": EV, "updated": time.time(), "state": "recording", "source": {"id": "yt1"}}))
    a1 = sclaim(admin)["assignment"]
    assert (a1["source"], a1["role"], a1["ev"]) == ("cam2", "secondary", EV) and a1["aid"].startswith("cs")
    assert a1["chunk_bytes"] <= a1["chunk_max"] <= 16 << 20 and a1["max_clip_bytes"] >= 500 << 20
    a2 = sclaim(admin, "lap2")["assignment"]
    assert (a2["source"], a2["role"]) == ("yt1", "backup")
    r = sclaim(admin, "lap3")
    assert r["assignment"] is None and "another processor" in r["why"]
    assert sclaim(admin)["assignment"]["aid"] == a1["aid"]                # the same processor resumes it
    assert sclaim(admin, "lap9", source="yt1")["assignment"] is None
    assert sclaim(admin, "lap9", events=["2026other"])["assignment"] is None
    e = admin.get("/audit?action=processing.stream").json()["entries"][0]
    assert e["after"]["source"] in ("yt1", "cam2")


def test_stream_claim_stale_is_handed_over_and_old_holder_stops(admin, streams):
    a1 = sclaim(admin)["assignment"]
    assert a1["source"] == "yt1"                                         # the server records nothing: best first
    p = streams["data"] / "processing" / "streams" / EV / f"{a1['aid']}.json"
    d = json.loads(p.read_text()); d["at"] = time.time() - CL.CLAIM_S - 5; p.write_text(json.dumps(d))
    a2 = sclaim(admin, "lap2")["assignment"]
    assert a2["source"] == "yt1" and a2["aid"] != a1["aid"]
    r = admin.post(f"{P}/streams/{EV}/{a1['aid']}/heartbeat", {"processor": "lap1"}).json()
    assert r["stop"] is True and "another processor" in r["why"]


def test_stream_heartbeat_says_which_clips_to_send(admin, streams):
    a = sclaim(admin)["assignment"]
    url = f"{P}/streams/{EV}/{a['aid']}/heartbeat"
    r = admin.post(url, {"processor": "lap1", "state": "recording", "rec": {"chunks": 12, "bytes": 1e7, "x": "y"}})
    assert r.status_code == 200, r.text
    d = r.json()
    keys = [m["key"] for m in d["matches"]]
    assert keys == [f"{EV}_qm4", f"{EV}_qm5", f"{EV}_qm6"]               # recent ones only (this recording)
    assert d["matches"][0]["alliances"]["red"]["team_keys"] == ["frc40", "frc41", "frc42"]
    assert d["want"] == [f"{EV}_qm4", f"{EV}_qm5"] and d["grace_s"] == 0  # qm6 is not over yet
    st = json.loads((streams["data"] / "processing" / "streams" / EV / f"{a['aid']}.json").read_text())
    assert st["state"] == "recording" and st["rec"] == {"chunks": 12, "bytes": 1e7}
    (streams["evd"] / "livestream.json").write_text(json.dumps(
        {"event": EV, "updated": time.time(), "state": "recording", "source": {"id": "cam2"}}))
    d = admin.post(url, {"processor": "lap1"}).json()
    assert d["want"] == [f"{EV}_qm4"] and d["grace_s"] > 0               # the server records too: waits for its clip
    assert admin.post(url, {"processor": "lap2"}).status_code == 409
    assert admin.post(url, {"processor": "lap1", "state": "<b>"}).status_code == 422
    assert admin.post(f"{P}/streams/{EV}/csffffffffff/heartbeat", {"processor": "lap1"}).status_code == 409
    assert admin.post(f"{P}/streams/{EV}/..%2Fx/heartbeat", {"processor": "lap1"}).status_code in (404, 422)
    assert admin.post(f"{P}/streams/{EV}/{a['aid']}/release", {"processor": "lap1"}).json() == {"released": True}
    assert admin.post(url, {"processor": "lap1"}).json()["stop"] is True


def _record(n, **kw):
    return dict({"id": f"tba-qm{n}", "match_name": f"Qualification {n}", "comp_level": "qm", "set": 1, "number": n,
                 "teams": {"red": [10 * n, 10 * n + 1, 10 * n + 2], "blue": [10 * n + 3, 10 * n + 4, 10 * n + 5]},
                 "score": {"red": 50, "blue": 40}, "score_source": "tba", "t_start_stream": 1234.5,
                 "t_start_utc": "2026-10-18T18:00:00.00Z", "auto_start_in_clip": 15.0,
                 "confidence": {"match": 0.5, "name": 1.0, "teams": 1.0, "score": 1.0},
                 "flags": ["cut_by_tba_time", "start_from_tba_time"],
                 "cut_from": {"actual_time": 1792000000.0, "lead": 15.0, "at": 1792000300.0}}, **kw)


def upload(c, ev, aid, mid, data, pid="lap1", chunk=4096, record=None):
    import hashlib
    base = f"{P}/streams/{ev}/{aid}/clips/{mid}"
    r = c.post(base, {"processor": pid, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    assert r.status_code == 200, r.text
    off = r.json()["offset"]
    h = {"X-CSRF-Token": c.tc.cookies.get("cs_csrf"), "Content-Type": "application/octet-stream"}
    while off < len(data):
        r = c.tc.put(f"/app/v1{base}/data?processor={pid}&offset={off}", content=data[off:off + chunk], headers=h)
        assert r.status_code == 200, r.text
        off = r.json()["offset"]
    return c.post(base + "/finish", {"processor": pid, "record": record or _record(int(mid.split("qm")[1]))})


def test_clip_upload_registers_a_livestream_record_that_is_then_tracked(admin, streams):
    a = sclaim(admin)["assignment"]
    aid = a["aid"]
    data = _mp4(50000)
    base = f"{P}/streams/{EV}/{aid}/clips/tba-qm4"
    import hashlib
    sha = hashlib.sha256(data).hexdigest()
    assert admin.post(base, {"processor": "lap1", "size": len(data), "sha256": sha}).json()["offset"] == 0
    h = {"X-CSRF-Token": admin.tc.cookies.get("cs_csrf")}
    put_ = lambda off, b, pid="lap1": admin.tc.put(f"/app/v1{base}/data?processor={pid}&offset={off}", content=b, headers=h)
    assert put_(0, data[:10000]).json() == {"offset": 10000}
    assert put_(5000, data[5000:6000]).status_code == 409                    # wrong offset
    assert put_(10000, data[10000:20000], pid="lap2").status_code == 409     # not this processor's stream
    assert put_(10000, b"x" * (17 << 20)).status_code == 413
    # resume: the begin call answers where to go on
    assert admin.post(base, {"processor": "lap1", "size": len(data), "sha256": sha}).json()["offset"] == 10000
    assert admin.post(base + "/finish", {"processor": "lap1", "record": _record(4)}).status_code == 409   # incomplete
    r = upload(admin, EV, aid, "tba-qm4", data)
    assert r.status_code == 200, r.text
    assert r.json()["key"] == f"{EV}_qm4" and r.json()["media"] == f"media/live/{EV}/{aid}/clips/tba-qm4.mp4"
    sd = streams["media"] / "live" / EV / aid
    assert (sd / "clips" / "tba-qm4.mp4").read_bytes() == data
    rec = json.loads((sd / "matches" / "tba-qm4.json").read_text())
    assert rec["clip"] == str(sd / "clips" / "tba-qm4.mp4") and rec["uploaded"]["sha256"] == sha
    assert "uploaded_by_client" in rec["flags"] and rec["teams"]["red"] == [40, 41, 42]
    meta = json.loads((sd / "meta.json").read_text())
    assert (meta["source"], meta["video_id"], meta["mode"]) == ("yt1", aid, "client")
    assert not list((sd / ".uploads").iterdir())
    # retries are fine; the heartbeat no longer asks for it
    assert admin.post(base + "/finish", {"processor": "lap1", "record": _record(4)}).json()["already"] is True
    assert admin.post(base, {"processor": "lap1", "size": len(data), "sha256": sha}).json()["done"] is True
    d = admin.post(f"{P}/streams/{EV}/{aid}/heartbeat", {"processor": "lap1"}).json()
    assert d["have"] == [f"{EV}_qm4"] and d["want"] == [f"{EV}_qm5"]
    # the stream (yt1) is calibrated: a GPU processor is handed this clip like any livestream clip
    seen = [claim(admin, f"gpu{i}")["job"]["key"] for i in range(4)]
    assert f"{EV}_qm4" in seen
    j = json.loads((streams["data"] / "processing" / EV / f"{EV}_qm4" / "job.json").read_text())
    assert j["clip"] == str(sd / "clips" / "tba-qm4.mp4")
    e = admin.get("/audit?action=processing.clip").json()["entries"][0]
    assert e["target"] == f"{EV}/{aid}/tba-qm4" and e["after"]["bytes"] == len(data)


def test_clip_upload_validation(team, admin, streams, monkeypatch):
    aid = sclaim(admin)["assignment"]["aid"]
    data = _mp4(20000)
    base = f"{P}/streams/{EV}/{aid}/clips"
    import hashlib
    sha = hashlib.sha256(data).hexdigest()
    ok = {"processor": "lap1", "size": len(data), "sha256": sha}
    assert admin.post(f"{base}/qm4", ok).status_code == 422                       # not a tba-<match> id
    assert admin.post(f"{base}/tba-qm4", dict(ok, size=2 << 30)).status_code == 422
    assert admin.post(f"{base}/tba-qm4", dict(ok, sha256="x")).status_code == 422
    assert team["strategist"].post(f"{base}/tba-qm4", ok).status_code == 403
    for bad in (_record(5), _record(4, comp_level="sf"), _record(4, flags=["<script>"]), _record(4, teams={"red": [0]}),
                _record(4, auto_start_in_clip=99), _record(4, t_start_utc="yesterday"), _record(4, clip="/etc/passwd")):
        assert upload(admin, EV, aid, "tba-qm4", data, record=bad).status_code == 422, bad
    assert upload(admin, EV, aid, "tba-qm4", b"\0" * 5000).status_code == 422          # not an mp4
    from server.routers import processing as PR
    import collections
    monkeypatch.setattr(PR.shutil, "disk_usage", lambda p: collections.namedtuple("u", "total used free")(1, 1, 1 << 20))
    r = admin.post(f"{base}/tba-qm5", dict(ok, size=len(data)))
    assert r.status_code == 507 and "nearly full" in r.json()["detail"]
    assert not (streams["media"] / "live" / EV / aid / "matches" / "tba-qm4.json").exists()
