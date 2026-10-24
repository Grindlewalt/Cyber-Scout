"""GET /admin/jobs: admin only; the snapshot of running + queued + recent work from the files the containers write."""
import json, os, time

import pytest

from server import db as DB
from server.config import cfg

from .conftest import new_client

EV = "2026zzjb"


def w(p, obj, raw=None):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(raw if raw is not None else json.dumps(obj))


@pytest.fixture
def busy(data):
    now = time.time()
    c = DB.connect()
    c.execute("INSERT INTO events(key,name,year,start_date,added_at) VALUES(?,?,?,?,?)", (EV, "Jobs Event", 2026, "2026-10-17", now))
    c.close()
    ev = data / "events" / EV
    # logs: worker with a GPU card, a GPU processor, recorder
    w(data / "logs" / "worker.json", dict(role="worker", at=now - 2, doing="building the site", doing_since=now - 40, gpu_ids="0,1", lines=[],
                                          gpus=[dict(index=0, name="RTX 3090", util_pct=80, mem_used_mb=9000, mem_total_mb=24000)]))
    w(data / "logs" / "processor-gpu1.json", dict(role="processor-gpu1", at=now - 3, doing="tracking Q5", doing_since=now - 90,
                                                 gpu_device="1", gpus=[dict(index=1, name="RTX 3080", util_pct=55, mem_used_mb=5000, mem_total_mb=10000)]))
    w(data / "logs" / "recorder.json", dict(role="recorder", at=now - 1, doing="", lines=[]))
    w(data / "processors" / "gpu1.json", dict(id="gpu1", at=now - 3, gpu="1", state="tracking", current=dict(ev=EV, key=f"{EV}_qm5", since=now - 90)))
    w(data / "processors" / "gpu0.json", dict(id="gpu0", at=now - 1000, gpu="0", state="tracking", current=dict(ev=EV, key=f"{EV}_qm6", since=now - 2000)))
    # claims: processor (live), a Client (live, progress), a stale one, a dead processor's
    w(ev / "claims" / f"{EV}_qm5.json", dict(by="gpu1", at=now - 5))
    w(ev / "claims" / f"{EV}_qm7.json", dict(by="app-u1-laptop", at=now - 5))
    w(data / "processing" / EV / f"{EV}_qm7" / "job.json", dict(by="app-u1-laptop", progress=0.4, stage="processing", started=now - 70))
    w(ev / "claims" / f"{EV}_qm8.json", dict(by="app-u1-old", at=now - 5000))
    w(ev / "claims" / f"{EV}_qm6.json", dict(by="gpu0", at=now - 1000))
    w(ev / "status.json", {f"{EV}_qm1": dict(state="done", at=now - 100), f"{EV}_qm2": dict(state="failed", at=now - 50, note="could not read the clip"),
                           f"{EV}_qm9": dict(state="processing", at=now - 20)})
    w(data / "site" / "api" / EV / "event.json", dict(matches=[
        dict(key=f"{EV}_qm3", short="Q3", state="pending", wait="queued", state_note="queued: tracked in match order"),
        dict(key=f"{EV}_qm4", short="Q4", state="pending", wait="video", state_note="no match video yet"),
        dict(key=f"{EV}_qm5", short="Q5", state="processing"),
        dict(key=f"{EV}_qm10", short="Q10", state="pending", wait="not_played"),
        dict(key=f"{EV}_qm1", short="Q1", state="done")]))
    # video jobs
    jd = data / "jobs" / "sources"
    w(jd / "a1.json", dict(id="a1", kind="frames", state="queued", created=now - 30, ev=EV))
    w(jd / "a2.json", dict(id="a2", kind="auto_setup", state="running", created=now - 200, started=now - 100, runner="worker:1", ev=EV))
    w(jd / "a3.json", dict(id="a3", kind="frames", state="error", created=now - 400, started=now - 390, finished=now - 380, error="no video", ev=EV))
    w(jd / "a4.json", dict(id="a4", kind="suggest", state="running", created=now - 99999, started=now - 99999, ev=EV))      # dead runner
    w(jd / "a5.json", dict(id="a5", kind="frames", state="queued", created=now - 9000, ev=EV))                              # expired
    w(ev / "livestream.json", dict(event=EV, state="recording", updated=now - 5, source="yt1", video_id="v1"))
    return data


def test_admin_only(team, app):
    for role in ("strategist", "scout", "viewer"):
        assert team[role].get("/admin/jobs").status_code == 403, role
    assert new_client(app).get("/admin/jobs").status_code == 401


def test_empty(team):
    d = team["admin"].get("/admin/jobs").json()
    assert d["running"] == [] and d["queued"]["videos"] == [] and d["queued"]["matches"] == [] and d["recent"] == []
    assert [r["id"] for r in d["runners"]] == ["worker", "recorder"] and not d["runners"][0]["alive"]


def test_snapshot(team, busy):
    d = team["admin"].get("/admin/jobs").json()
    R = {r["id"]: r for r in d["runners"]}
    assert R["worker"]["alive"] and R["worker"]["doing"] == "building the site" and R["worker"]["gpu"]["cards"][0]["util_pct"] == 80
    p = R["processor:gpu1"]
    assert p["alive"] and p["where"] == "GPU 1 · RTX 3080" and p["current"]["key"] == f"{EV}_qm5" and p["current"]["label"] == "Q5"
    assert not R["processor:gpu0"]["alive"] and R["processor:gpu0"]["current"] is None
    c = R["client:app-u1-laptop"]
    assert c["kind"] == "client" and c["current"]["progress"] == 0.4 and c["current"]["stage"] == "processing"
    assert "client:app-u1-old" not in R                                   # a stale claim is nobody's
    assert R["recorder"]["alive"]
    run = {(x["type"], x["key"]): x for x in d["running"]}
    assert (("match", f"{EV}_qm5") in run and ("match", f"{EV}_qm7") in run and ("match", f"{EV}_qm9") in run
            and ("video", "a2") in run and ("stream", "v1") in run)
    assert ("match", f"{EV}_qm6") not in run and ("match", f"{EV}_qm8") not in run and ("video", "a4") not in run
    assert run[("match", f"{EV}_qm9")]["by"] == "worker" and run[("match", f"{EV}_qm7")]["by"] == "client:app-u1-laptop"
    q = d["queued"]
    assert [v["id"] for v in q["videos"]] == ["a1"]                       # expired a5 dropped
    assert [(m["label"], m["wait"]) for m in q["matches"]] == [("Q3", "queued"), ("Q4", "video")]
    assert q["matches"][0]["note"] and "Clients" in q["matches"][0]["takers"] and q["counts"][EV] == {"queued": 1, "video": 1}
    rec = {(x["type"], x["key"]): x for x in d["recent"]}
    assert rec[("video", "a3")]["ok"] is False and rec[("video", "a3")]["error"] == "no video"
    assert rec[("match", f"{EV}_qm2")]["error"] == "could not read the clip" and rec[("match", f"{EV}_qm1")]["ok"]


def test_garbage_and_symlinks(team, data, busy, tmp_path_factory):
    out = tmp_path_factory.mktemp("o") / "x.json"
    out.write_text(json.dumps(dict(role="worker", at=time.time(), doing="LEAK")))
    (data / "logs" / "worker.json").unlink()
    os.symlink(out, data / "logs" / "worker.json")
    (data / "processors" / "bad.json").write_text("{nope")
    (data / "processors" / "big.json").write_text("x" * 100)
    (data / "jobs" / "sources" / "z.json").write_text(json.dumps(["list"]))
    (data / "jobs" / "sources" / "y.json").write_text(json.dumps(dict(id=5, kind=[], state="queued", created="x")))
    (data / "site" / "api" / EV / "event.json").write_text("[1,2]")
    (data / "events" / EV / "claims" / f"{EV}_qm3.json").write_text("not json")
    r = team["admin"].get("/admin/jobs")
    assert r.status_code == 200
    d = r.json()
    assert "LEAK" not in json.dumps(d) and d["runners"][0]["doing"] == ""
    assert d["queued"]["matches"] == []
