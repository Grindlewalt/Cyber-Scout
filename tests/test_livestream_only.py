"""Video mode "livestream" (offseason, no per-match uploads): TBA gives the match list, every match is tracked from
the recorded livestream -- uploads are never looked up; missed matches are cut from the recording by TBA time.
Pure file fixtures (no video, no network)."""
import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "live"))

import liveingest as LI  # noqa: E402
import sources as SRC  # noqa: E402

EV = "2026test"
T0 = 1_760_000_000.0                                      # wall clock of chunk sq 0 (pts 0)


def rec(i, n=1, teams=None, flags=(), auto=8.5, **kw):
    r = dict(id=i, comp_level="qm", set=1, number=n, match_name=f"Qualification {n}",
             teams=teams or {"red": [1, 2, 3], "blue": [4, 5, 6]}, score={"red": 10, "blue": 20},
             score_source="results_screen", flags=list(flags), t_start_stream=100.0 * n,
             t_start_utc=f"2026-10-17T18:{n:02d}:00.00Z", clip=f"/nowhere/clips/{i}.mp4", auto_start_in_clip=auto)
    r.update(kw)
    return r


def tba(n, score=-1, teams=True, actual=None):
    tk = (lambda a: [f"frc{x}" for x in ((1, 2, 3) if a == "red" else (4, 5, 6))]) if teams else (lambda a: [])
    return dict(key=f"{EV}_qm{n}", comp_level="qm", set_number=1, match_number=n, time=1000 + n, actual_time=actual,
                alliances={a: dict(team_keys=tk(a), score=score) for a in ("red", "blue")}, videos=[])


@pytest.fixture
def env(tmp_path, monkeypatch):
    data, media = tmp_path / "data", tmp_path / "media"
    for d in (data / "events" / EV, media / "live"):
        d.mkdir(parents=True)
    monkeypatch.setenv("DATA", str(data))
    monkeypatch.setenv("MEDIA", str(media))
    monkeypatch.delenv("LIVE_DIR", raising=False)
    monkeypatch.delenv("LAYOUT", raising=False)
    evd = data / "events" / EV
    (evd / "layout.json").write_text(json.dumps({"processing_source": "url-abc", "sources": {"url-abc": {
        "W": 1920, "H": 1080, "role": "wide", "regions": [{"kind": "field", "box": [0, 150, 1920, 1080]}]}}}))
    (evd / "camera.json").write_text(json.dumps({"source": "url-abc"}))          # calibrated on the stream
    doc = {"sources": [dict(id="url-abc", kind="livestream", url="https://www.youtube.com/@x/live", role="wide",
                            primary=True, origin="admin", enabled=True)]}
    SRC.set_video_mode(doc, "livestream", "Coach")
    (evd / "sources.json").write_text(json.dumps(doc))
    return dict(data=data, media=media, live=media / "live", evd=evd)


def write_dir(env, vid, recs, chunks=None, analysed_to=None):
    d = env["live"] / EV / vid
    for sub in ("matches", "clips", "seg"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps(dict(event=EV, source="url-abc", video_id=vid, started=1.0)))
    for r in recs:
        (d / "matches" / f"{r['id']}.json").write_text(json.dumps(r))
        (d / "clips" / os.path.basename(r["clip"])).write_bytes(b"x")
    if chunks:                                            # 5 s chunks sq 0..chunks-1, one 60 s segment per 12
        with open(d / "chunks.jsonl", "w") as f:
            for sq in range(chunks):
                seg = f"seg/{sq // 12:05d}.ts"
                (d / seg).write_bytes(b"x")
                f.write(json.dumps(dict(sq=sq, file=seg, off=0, size=1, pts=5.0 * sq, dur=5.0, wall=T0 + 5.0 * sq)) + "\n")
    if analysed_to is not None:
        (d / "analyzer.json").write_text(json.dumps(dict(last_sq=chunks, t_last=analysed_to, at=time.time())))
    return d


class FakeTBA:
    def __init__(self, ms): self.ms = ms
    def matches(self, ev): return self.ms


def test_video_mode_helpers():
    assert SRC.video_mode({}) == "auto" and SRC.video_mode({"video_mode": "bogus"}) == "auto"
    d = SRC.set_video_mode({}, "livestream", "x")
    assert SRC.video_mode(d) == "livestream" and d["video_mode_by"] == "x"
    with pytest.raises(ValueError):
        SRC.set_video_mode({}, "uploads")


def test_sync_livestream_only_never_looks_up_uploads(env, monkeypatch):
    import worker as W, videos, videoplan
    monkeypatch.setattr(W, "DATA", str(env["data"]))
    monkeypatch.setattr(videos, "candidates", lambda *a, **k: pytest.fail("uploads looked up"))
    monkeypatch.setattr(videoplan, "refresh", lambda *a, **k: pytest.fail("video plan built"))
    monkeypatch.setattr(W, "build_site", lambda ev, tba: None)
    write_dir(env, "vidA", [rec("qm1", n=1)])
    calls = []
    monkeypatch.setattr(W, "process_match", lambda ev, m, cfg, st, mc, live=None:
                        calls.append((m["key"], cfg["video_mode"], live and live["teams_from"])) or False)
    # qm1 has a clip; qm2 was played but the stream analyser missed it; qm3 is not played yet
    W.sync(EV, FakeTBA([tba(1, score=30), tba(2, score=12), tba(3)]))
    assert calls == [(f"{EV}_qm1", "livestream", "tba")]
    st = json.loads((env["evd"] / "status.json").read_text())
    assert st[f"{EV}_qm2"]["state"] == "waiting_live" and "has not found this match" in st[f"{EV}_qm2"]["note"]
    assert f"{EV}_qm3" not in st


def test_stream_only_quals_are_synthesized_and_tracked(env, monkeypatch):
    """TBA lists no matches yet (offseason schedule not synced): the stream's qual records become matches."""
    import worker as W
    monkeypatch.setattr(W, "DATA", str(env["data"]))
    monkeypatch.setattr(W, "build_site", lambda ev, tba: None)
    write_dir(env, "vidA", [rec("qm5", n=5, teams={"red": [11, 12, 13], "blue": [14, 15, 16]})])
    calls = []
    monkeypatch.setattr(W, "process_match", lambda ev, m, cfg, st, mc, live=None:
                        calls.append((m["key"], m.get("livestream"), live["teams"])) or False)
    W.sync(EV, FakeTBA([]))
    assert calls == [(f"{EV}_qm5", True, {"red": ["frc11", "frc12", "frc13"], "blue": ["frc14", "frc15", "frc16"]})]


def test_process_match_livestream_only_waits_and_fails_clearly(env, monkeypatch):
    import worker as W, videos, process
    monkeypatch.setattr(W, "DATA", str(env["data"]))
    monkeypatch.setattr(W, "MEDIA", str(env["media"]))
    monkeypatch.setattr(videos, "candidates", lambda *a, **k: pytest.fail("uploads looked up"))
    cfg, status, m = {"video_mode": "livestream"}, {}, tba(1, score=30)
    assert W.process_match(EV, m, cfg, status, {}, live=None) is False
    assert status[m["key"]]["state"] == "waiting_live"
    d = write_dir(env, "vidA", [rec("qm1", n=1)])
    lv = LI.LiveView(EV)

    def boom(*a, **k): raise RuntimeError("decoder exploded")
    monkeypatch.setattr(process, "process", boom)
    states = []
    for _ in range(3):
        live, lv, why = W.live_only_input(EV, lv, m, True, status)
        if live is None:
            states.append(("no input", why)); break
        W.process_match(EV, m, cfg, status, {}, live=live)
        states.append((status[m["key"]]["state"], status[m["key"]].get("note")))
    # the clip is tried twice, then skipped (no recording to cut from -> waiting with the reason)
    assert states[0][0] == "waiting_live" and "try 1 of 3" in states[0][1]
    assert states[1][0] == "waiting_live" and "try 2 of 3" in states[1][1]
    assert states[2][0] == "no input" and "rejected / failed" in states[2][1]
    assert status[m["key"]]["live_skip"] == [str(d / "matches" / "qm1.json")]
    # a third failure (e.g. of a TBA-time clip) -> failed, with a clear note; the sync then leaves it alone
    status[m["key"]]["live_skip"] = []
    live, lv, _ = W.live_only_input(EV, lv, m, True, status)
    W.process_match(EV, m, cfg, status, {}, live=live)
    assert status[m["key"]]["state"] == "failed" and "3 tries" in status[m["key"]]["note"]


def test_tba_cut_plan_reasons(env):
    T = T0 + 600.0                                         # match start at stream pts 600
    m = tba(4, score=40, actual=T)
    now = T + 400
    assert LI.tba_cut_plan(EV, tba(4), now=now)[1] == "TBA has no start time for this match"
    assert LI.tba_cut_plan(EV, m, now=T + 60)[1] == "match not over yet"
    assert "does not cover" in LI.tba_cut_plan(EV, m, now=now)[1]
    write_dir(env, "vidA", [], chunks=140, analysed_to=690.0)            # 0..700 s recorded: match cut off
    assert "missing" in LI.tba_cut_plan(EV, m, now=now)[1]
    write_dir(env, "vidB", [], chunks=200, analysed_to=650.0)            # covered, analyser still before the end
    assert "has not reached" in LI.tba_cut_plan(EV, m, now=now)[1]
    p, why = LI.tba_cut_plan(EV, m, now=T + LI.MATCH_S + LI.TBA_WAIT_S + 1)   # analyser stalled: cut anyway
    assert why is None and p["dir"].endswith("vidB") and abs(p["pts"] - 600.0) < 1e-6
    assert p["t0"] == 600.0 - LI.TBA_LEAD_S and p["t1"] == 600.0 + LI.MATCH_S + LI.TBA_TAIL_S


def test_tba_cut_writes_a_record_the_worker_uses(env, monkeypatch):
    import worker as W
    monkeypatch.setattr(W, "DATA", str(env["data"]))
    monkeypatch.setattr(W, "build_site", lambda ev, tba: None)
    T = T0 + 600.0
    write_dir(env, "vidB", [], chunks=200, analysed_to=990.0)
    cuts = []

    def fake_cut(d, t0, t1, dst):
        cuts.append((t0, t1)); open(dst, "wb").write(b"clip"); return 585.2      # first frame pts (chunk aligned)
    real = LI.tba_cut
    monkeypatch.setattr(LI, "tba_cut", lambda ev, m, **kw: real(ev, m, now=T + 400, cut=fake_cut, measure=lambda c: None,
                                                                 log=kw.get("log", print)))
    calls = []
    monkeypatch.setattr(W, "process_match", lambda ev, m, cfg, st, mc, live=None: calls.append(live) or False)
    monkeypatch.setattr(W.time, "time", lambda: T + 400)
    W.sync(EV, FakeTBA([tba(4, score=40, actual=T)]))
    assert cuts == [(600.0 - LI.TBA_LEAD_S, 600.0 + LI.MATCH_S + LI.TBA_TAIL_S)]
    r = json.loads((env["live"] / EV / "vidB" / "matches" / "tba-qm4.json").read_text())
    assert LI.record_key(EV, r) == f"{EV}_qm4" and "cut_by_tba_time" in r["flags"] and "start_from_tba_time" in r["flags"]
    assert r["teams"] == {"red": [1, 2, 3], "blue": [4, 5, 6]} and r["score"] == {"red": 40, "blue": 40}
    assert abs(r["auto_start_in_clip"] - 14.8) < 1e-6                          # 600 - 585.2
    (live,) = calls
    assert live["start_trusted"] is False and abs(live["start"] - 14.8) < 1e-6 and live["teams_from"] == "tba"
    # cut once only: the next poll reuses the record (no second cut)
    W.sync(EV, FakeTBA([tba(4, score=40, actual=T)]))
    assert len(cuts) == 1 and len(calls) == 2


def test_recorded_media_for_livestream_frames(env):
    s = dict(id="url-abc", kind="livestream", url="https://www.youtube.com/@x/live")
    assert SRC.recorded_media(EV, s) == (None, None)
    d = write_dir(env, "vidA", [])
    rows = []
    for i in range(3):
        (d / "seg" / f"{i:05d}.ts").write_bytes(bytes([65 + i]) * 200_000)
        rows += [dict(sq=10 * i + k, file=f"seg/{i:05d}.ts", off=k * 50_000, size=50_000, pts=100.0 + 20 * i + 5 * k, dur=5.0)
                 for k in range(4)]
    rows += [dict(sq=900 + k, file="seg/00000.ts", off=0, size=50_000, pts=7.0, dur=5.0) for k in range(5)]   # repeated chunk: junk
    (d / "chunks.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    smp = str(d / "sample.ts")
    assert SRC.recorded_media(EV, s) == (smp, 90.0)        # the last minutes of real chunks, concatenated
    assert os.path.getsize(smp) == 11 * 50_000              # 12 real chunks minus the newest (may still be written)
    assert SRC.media_for(dict(s, _ev=EV, sample={"video": s["url"]})) == (smp, 90.0)   # sample = the stream URL itself
    (d / "clips" / "qm1.mp4").write_bytes(b"x" * 200_000)
    assert SRC.recorded_media(EV, s) == (str(d / "clips" / "qm1.mp4"), 60.0)    # a match clip beats segments
    assert SRC.media_for(dict(s, _ev=EV)) == (str(d / "clips" / "qm1.mp4"), 60.0)
    assert SRC.media_for(dict(s, _ev=EV, sample={"video": "abcdefghijk"}))[0].endswith("v=abcdefghijk")   # explicit sample wins


def test_calibration_frames_keep_the_main_view():
    """A program feed cuts away (close-ups, results screen): the wizard keeps the frames of the main view."""
    import numpy as np, cv2
    rng = np.random.default_rng(1)
    wide = rng.integers(0, 255, (54, 96, 3), dtype=np.uint8)
    fr = [cv2.resize(wide, (960, 540)) + rng.integers(0, 6, (540, 960, 3), dtype=np.uint8) for _ in range(8)]
    fr += [np.full((540, 960, 3), 200, np.uint8), np.zeros((540, 960, 3), np.uint8), np.full((540, 960, 3), 90, np.uint8)]
    ts = list(range(len(fr)))
    kf, kt = SRC._main_view(np, cv2, fr, ts)
    assert kt == list(range(8))


def test_tba_cut_before_calibration_and_without_model(env, monkeypatch):
    """Not calibrated yet (no field box / camera): the match is still cut by TBA time (its video on the site,
    kept past retention), and waits for calibration; trackable but no detector model: waits for the model."""
    import worker as W
    monkeypatch.setattr(W, "DATA", str(env["data"]))
    monkeypatch.setattr(W, "build_site", lambda ev, tba: None)
    (env["evd"] / "layout.json").unlink(); (env["evd"] / "camera.json").unlink()
    T = T0 + 600.0
    write_dir(env, "vidB", [], chunks=200, analysed_to=990.0)
    cuts = []

    def fake_cut(d, t0, t1, dst):
        cuts.append((t0, t1)); open(dst, "wb").write(b"clip"); return 585.2
    real = LI.tba_cut
    monkeypatch.setattr(LI, "tba_cut", lambda ev, m, **kw: real(ev, m, now=T + 400, cut=fake_cut, measure=lambda c: None,
                                                                 log=kw.get("log", print)))
    calls = []
    monkeypatch.setattr(W, "process_match", lambda ev, m, cfg, st, mc, live=None: calls.append(live) or False)
    monkeypatch.setattr(W.time, "time", lambda: T + 400)
    W.sync(EV, FakeTBA([tba(4, score=40, actual=T)]))
    assert len(cuts) == 1 and calls == []
    assert (env["live"] / EV / "vidB" / "matches" / "tba-qm4.json").exists()
    st = json.loads((env["evd"] / "status.json").read_text())[f"{EV}_qm4"]
    assert st["state"] == "waiting_live" and st["note"].startswith("stream not trackable")
    # calibrated now, but no detector model: the clip waits for the model, no second cut, no tracking
    (env["evd"] / "layout.json").write_text(json.dumps({"processing_source": "url-abc", "sources": {"url-abc": {
        "W": 1920, "H": 1080, "role": "wide", "regions": [{"kind": "field", "box": [0, 150, 1920, 1080]}]}}}))
    (env["evd"] / "camera.json").write_text(json.dumps({"source": "url-abc"}))
    W.sync(EV, FakeTBA([tba(4, score=40, actual=T)]), track=False)
    st = json.loads((env["evd"] / "status.json").read_text())[f"{EV}_qm4"]
    assert len(cuts) == 1 and calls == [] and st["note"].startswith("clip ready")


def test_program_feed_view_check(env):
    """A program feed cuts between cameras: only frames showing the calibrated view are tracked.  The view
    reference is the calibration plate of the stream source (the wizard's median frame)."""
    import numpy as np, cv2
    import process as PR
    rng = np.random.default_rng(3)
    wide = cv2.resize(rng.integers(0, 255, (27, 48, 3), dtype=np.uint8), (1920, 1080), interpolation=cv2.INTER_CUBIC)
    roi = (0, 100, 1920, 1000)
    ref = PR.view_reference(wide, wide.shape, roi)
    moved = wide.copy(); cv2.circle(moved, (900, 400), 60, (0, 255, 255), -1)           # robots / fuel moved
    closeup = cv2.resize(wide[300:500, 700:1050], (1920, 1080), interpolation=cv2.INTER_CUBIC)
    assert PR.on_air(moved, ref, roi) and not PR.on_air(closeup, ref, roi) and PR.on_air(closeup, None, roi)
    # the reference is the view saved at ACCEPT time (view_<source>.png), never the wizard's working plate
    (env["evd"] / "frames").mkdir(); cv2.imwrite(str(env["evd"] / "frames" / "calib_overhead_plate.png"), wide)
    assert LI.stream_view_ref(EV, "url-abc") is None
    cv2.imwrite(str(env["evd"] / "view_url-abc.png"), wide)
    assert LI.stream_view_ref(EV, "url-abc").endswith("view_url-abc.png")
    assert LI.trackable(EV, "url-abc")["view_ref"].endswith("view_url-abc.png")


def test_priority_order_and_claims(env, monkeypatch):
    """priority.json puts the chosen matches first (the rest keep their order); a claim by another processor
    (a helper Mac) keeps this worker off that match until it is stale."""
    import worker as W
    monkeypatch.setattr(W, "DATA", str(env["data"]))
    ms = [dict(key=f"{EV}_qm{i}") for i in range(1, 7)]
    assert [m["key"] for m in W.prioritized(EV, ms)] == [m["key"] for m in ms]
    (env["evd"] / "priority.json").write_text(json.dumps({"order": [f"{EV}_qm5", f"{EV}_qm2"]}))
    assert [m["key"][-3:] for m in W.prioritized(EV, ms)] == ["qm5", "qm2", "qm1", "qm3", "qm4", "qm6"]
    k = f"{EV}_qm5"
    assert W.claim(EV, k, by="macbook") and not W.claimed_elsewhere(EV, k, by="macbook")
    assert W.claimed_elsewhere(EV, k, by="m2") and not W.claim(EV, k, by="m2")
    assert W.claim(EV, k, by="macbook")                      # re-claiming your own claim is fine
    W.release(EV, k, by="m2")                                # someone else's claim: not released
    assert W.claimed_elsewhere(EV, k, by="m2")
    p = env["evd"] / "claims" / f"{k}.json"
    p.write_text(json.dumps({"by": "macbook", "at": time.time() - W.CLAIM_S - 1}))   # stale
    assert not W.claimed_elsewhere(EV, k, by="m2") and W.claim(EV, k, by="m2")
    W.release(EV, k, by="m2")
    assert not p.exists()


def test_calibrate_reads_the_latest_minutes_or_a_chosen_time(env):
    """Auto-calibrate (job_detect when=...): the latest minutes by default, not the newest clip (2026wamil's only clip
    was Q1, with a raised bar moving AprilTag 1); "ago:<m>" ends m minutes back; "match:<key>" reads that clip."""
    s = dict(id="url-abc", kind="livestream", url="https://www.youtube.com/@x/live")
    d = write_dir(env, "vidA", [])
    rows = []
    for i in range(30):                                     # 30 x 60 s chunks
        (d / "seg" / f"{i:05d}.ts").write_bytes(bytes([65 + i % 26]) * 1000)
        rows.append(dict(sq=i, file=f"seg/{i:05d}.ts", off=0, size=1000, pts=100.0 + 60 * i, dur=60.0))
    (d / "chunks.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (d / "clips" / "qm1.mp4").write_bytes(b"x" * 200_000)
    assert SRC.recorded_media(EV, s) == (str(d / "clips" / "qm1.mp4"), 60.0)          # frame grabs: still the clip
    latest, t = SRC.recorded_media(EV, s, when="latest")
    b = open(latest, "rb").read()
    assert latest == str(d / "sample.ts") and t == 0.0 and len(b) == 3000 and b[0] == 65 + 26 % 26   # chunks 26..28 (29 may still be written)
    back, _ = SRC.recorded_media(EV, s, when="ago:10")
    assert back == str(d / "sample_ago10.ts") and open(back, "rb").read()[0] == 65 + 16                 # 10 min earlier
    assert SRC.recorded_media(EV, s, when="match:2026x_qm1") == (str(d / "clips" / "qm1.mp4"), 60.0)
    assert SRC.recorded_media(EV, s, when="match:2026x_qm9") == (None, None)


def test_jobs_cut_off_by_a_restart_are_marked_failed(tmp_path):
    d = tmp_path / "jobs" / "sources"; d.mkdir(parents=True)
    host = SRC.RUNNER.split(":")[0]
    jobs = {"a1": dict(state="running", runner=f"{host}:1"), "b2": dict(state="running", runner=SRC.RUNNER),
            "c3": dict(state="running", runner="other-box:5"), "d4": dict(state="running", started=0), "e5": dict(state="done")}
    for k, j in jobs.items():
        (d / f"{k}.json").write_text(json.dumps(dict(j, id=k, kind="detect")))
    SRC.orphans(str(tmp_path), log=lambda *a: None)
    st = {k: json.loads((d / f"{k}.json").read_text()) for k in jobs}
    assert st["a1"]["state"] == "error" and st["a1"]["error"] == SRC.JOB_RESTARTED     # this container, an older process
    assert st["d4"]["state"] == "error"                                                  # no runner, long ago
    assert [st[k]["state"] for k in ("b2", "c3", "e5")] == ["running", "running", "done"]
