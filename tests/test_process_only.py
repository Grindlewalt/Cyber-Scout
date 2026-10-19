"""Process-only processors (live/worker.py --process-only): claim + track only, per-match state files instead of
status.json, the main worker's side (delegation, reconcile from the match file, proxy backfill, wake-up on new
match files), dynamic mode per source.  Pure file fixtures (no video, no network, no GPU)."""
import json
import os
import signal
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "live"))

import sources as SRC  # noqa: E402
import worker as W  # noqa: E402

EV = "2026test"


def rec(n, auto=8.5):
    return dict(id=f"qm{n}", comp_level="qm", set=1, number=n, match_name=f"Qualification {n}",
                teams={"red": [1, 2, 3], "blue": [4, 5, 6]}, score={"red": 10, "blue": 20}, score_source="results_screen",
                flags=[], t_start_stream=100.0 * n, t_start_utc=f"2026-10-17T18:{n:02d}:00.00Z",
                clip=f"/nowhere/clips/qm{n}.mp4", auto_start_in_clip=auto)


def tba(n, score=30):
    tk = lambda a: [f"frc{x}" for x in ((1, 2, 3) if a == "red" else (4, 5, 6))]
    return dict(key=f"{EV}_qm{n}", comp_level="qm", set_number=1, match_number=n, time=1000 + n, actual_time=None,
                alliances={a: dict(team_keys=tk(a), score=score) for a in ("red", "blue")}, videos=[])


class FakeTBA:
    def __init__(self, ms): self.ms = ms
    def matches(self, ev): return self.ms
    def event(self, ev): return {"start_date": None}


@pytest.fixture
def env(tmp_path, monkeypatch):
    data, media = tmp_path / "data", tmp_path / "media"
    evd = data / "events" / EV
    for d in (evd, media / "live", data / "config"):
        d.mkdir(parents=True)
    for k, v in (("DATA", data), ("MEDIA", media)):
        monkeypatch.setenv(k, str(v))
        monkeypatch.setattr(W, k, str(v))
    for k in ("LIVE_DIR", "LAYOUT", "DYNAMIC", "WORKER_TRACK", "PROCESS_ONLY", "ONCE", "PAUSE_PROCESSING"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(W, "WORKER_CONF", str(data / "config" / "worker.json"))
    monkeypatch.setattr(W, "_DELEGATING", [None])
    (evd / "layout.json").write_text(json.dumps({"processing_source": "url-abc", "sources": {"url-abc": {
        "W": 1920, "H": 1080, "role": "wide", "regions": [{"kind": "field", "box": [0, 150, 1920, 1080]}]}}}))
    (evd / "camera.json").write_text(json.dumps({"source": "url-abc"}))
    doc = {"sources": [dict(id="url-abc", kind="livestream", url="https://www.youtube.com/@x/live", role="wide",
                            primary=True, origin="admin", enabled=True)]}
    SRC.set_video_mode(doc, "livestream", "Coach")
    (evd / "sources.json").write_text(json.dumps(doc))
    vd = media / "live" / EV / "vidA"
    for sub in ("matches", "clips"):
        (vd / sub).mkdir(parents=True)
    (vd / "meta.json").write_text(json.dumps(dict(event=EV, source="url-abc", video_id="vidA", started=1.0)))
    for n in (1, 2, 3):
        (vd / "matches" / f"qm{n}.json").write_text(json.dumps(rec(n)))
        (vd / "clips" / f"qm{n}.mp4").write_bytes(b"x")
    return dict(data=data, media=media, evd=evd)


def as_processor(monkeypatch, wid):
    monkeypatch.setattr(W, "PROCESS_ONLY", True)
    monkeypatch.setattr(W, "WORKER_ID", wid)


def fake_track(env, calls, by=None):
    """process_match stand-in: records the call, writes the match file like the real one (last)."""
    def pm(ev, m, cfg, status, meta_cache, live=None):
        calls.append((by or W.WORKER_ID, m["key"], live is not None))
        st = status.setdefault(m["key"], {})
        st.update(state="processing", at=time.time(), attempts=st.get("attempts", 0) + 1)
        W.save_status(ev, status, m["key"])
        W.jsave(os.path.join(W.ev_dir(ev), "matches", f"{m['key']}.json"),
                dict(key=m["key"], source="livestream", live=dict(tba_posted=live["tba_posted"] if live else True)))
        st.update(state="done", at=time.time())
        W.save_status(ev, status, m["key"])
        return True
    return pm


def test_processor_writes_its_own_state_files_and_the_main_worker_merges_them(env, monkeypatch):
    W.jsave(str(env["evd"] / "status.json"), {f"{EV}_qm1": {"state": "error", "at": 100.0, "attempts": 1},
                                             f"{EV}_qm2": {"state": "no_video", "at": 9e9}})
    as_processor(monkeypatch, "gpu1-a")
    st = W.load_status(EV)
    st[f"{EV}_qm1"].update(state="processing", at=200.0, attempts=2)
    st[f"{EV}_qm2"].update(state="processing", at=150.0)
    W.save_status(EV, st, f"{EV}_qm1"); W.save_status(EV, st, f"{EV}_qm2")
    assert json.loads((env["evd"] / "status.json").read_text())[f"{EV}_qm1"]["state"] == "error"   # untouched
    assert (env["evd"] / "proc_status" / f"{EV}_qm1.json").exists()
    merged = W.load_status(EV)
    assert merged[f"{EV}_qm1"] == {"state": "processing", "at": 200.0, "attempts": 2}     # newer entry wins
    assert merged[f"{EV}_qm2"]["state"] == "no_video"                                     # status.json is newer
    monkeypatch.setattr(W, "PROCESS_ONLY", False)                                         # main worker: whole file
    W.save_status(EV, merged)
    assert json.loads((env["evd"] / "status.json").read_text())[f"{EV}_qm1"]["state"] == "processing"


def test_processors_split_the_matches_in_priority_order(env, monkeypatch):
    (env["evd"] / "priority.json").write_text(json.dumps({"order": [f"{EV}_qm3"]}))
    t = FakeTBA([tba(1), tba(2), tba(3)])
    as_processor(monkeypatch, "gpu1-a")
    m_a, _, live_a = W.next_job(EV, t)
    as_processor(monkeypatch, "gpu1-b")
    m_b, _, _ = W.next_job(EV, t)                          # qm3 is claimed by gpu1-a: the next one
    assert m_a["key"] == f"{EV}_qm3" and live_a["tba_posted"] and m_b["key"] == f"{EV}_qm1"
    W.release(EV, f"{EV}_qm1", by="gpu1-b")
    (env["evd"] / "matches").mkdir()
    (env["evd"] / "matches" / f"{EV}_qm1.json").write_text("{}")             # done meanwhile
    W.jsave(str(env["evd"] / "proc_status" / f"{EV}_qm2.json"), {"state": "failed", "at": time.time()})
    assert W.next_job(EV, t) is None                                         # qm3 held, qm1 done, qm2 failed
    W.release(EV, f"{EV}_qm3", by="gpu1-a")
    assert W.next_job(EV, t)[0]["key"] == f"{EV}_qm3"


def test_claim_of_a_dead_processor_is_void_a_live_one_is_not(env, monkeypatch):
    k = f"{EV}_qm1"
    as_processor(monkeypatch, "gpu1-a")
    W.proc_beat(state="tracking")
    assert W.claim(EV, k)
    monkeypatch.setattr(W, "WORKER_ID", "gpu1-b")
    assert W.claimed_elsewhere(EV, k) and not W.claim(EV, k)                  # gpu1-a is alive
    assert W.being_tracked(EV, k) and W.processors_alive() == ["gpu1-a"]
    p = json.loads(open(W._proc_file("gpu1-a")).read()); p["at"] -= W.PROC_DEAD_S + 1
    W.jsave(W._proc_file("gpu1-a"), p)                                       # gpu1-a stopped beating
    assert not W.being_tracked(EV, k) and W.processors_alive() == []
    assert not W.claimed_elsewhere(EV, k) and W.claim(EV, k)
    assert json.loads((env["evd"] / "claims" / f"{k}.json").read_text())["by"] == "gpu1-b"
    # a helper's claim (no processor heartbeat file) is never voided early
    W.CL.claim(str(env["data"]), EV, f"{EV}_qm2", "macbook-helper")
    assert W.claimed_elsewhere(EV, f"{EV}_qm2") and not W.claim(EV, f"{EV}_qm2")


def test_main_worker_delegates_while_a_processor_is_alive(env, monkeypatch):
    calls, sites = [], []
    monkeypatch.setattr(W, "process_match", fake_track(env, calls, by="main"))
    monkeypatch.setattr(W, "build_site", lambda ev, t: sites.append(ev))
    t = FakeTBA([tba(n, score=-1) for n in (1, 2, 3)])
    monkeypatch.setenv("WORKER_TRACK", "1")
    as_processor(monkeypatch, "gpu1-a"); W.proc_beat(state="idle")
    monkeypatch.setattr(W, "PROCESS_ONLY", False); monkeypatch.setattr(W, "WORKER_ID", "main")
    W.sync(EV, t)
    assert [c[1] for c in calls] == [f"{EV}_qm{n}" for n in (1, 2, 3)]       # WORKER_TRACK=1: tracks anyway
    for f in (env["evd"] / "matches").iterdir(): f.unlink()
    calls.clear(); sites.clear()
    monkeypatch.setenv("WORKER_TRACK", "auto")
    W.sync(EV, t)
    assert calls == [] and sites == [EV]                                     # left to gpu1-a; site still built
    os.remove(W._proc_file("gpu1-a"))
    W.sync(EV, t)
    assert len(calls) == 3                                                   # no processor alive: tracks here
    for f in (env["evd"] / "matches").iterdir(): f.unlink()
    calls.clear()
    monkeypatch.setenv("WORKER_TRACK", "0")
    W.sync(EV, t)
    assert calls == []


def test_reconcile_is_driven_by_the_match_file(env, monkeypatch):
    """A processor tracked qm1 from the stream before TBA posted (status.json knows nothing): once TBA posts,
    the main worker reconciles it, once."""
    (env["evd"] / "matches").mkdir()
    p = env["evd"] / "matches" / f"{EV}_qm1.json"
    p.write_text(json.dumps(dict(key=f"{EV}_qm1", source="livestream", live=dict(tba_posted=False, teams=None))))
    rec_calls = []
    real = W.live_reconcile
    monkeypatch.setattr(W, "live_reconcile", lambda ev, m, st: rec_calls.append(m["key"]) or real(ev, m, st))
    monkeypatch.setattr(W, "V2", False)
    monkeypatch.setattr(W, "build_site", lambda ev, t: None)
    monkeypatch.setattr(W, "process_match", lambda *a, **k: False)
    W.sync(EV, FakeTBA([tba(1, score=-1)]))
    assert rec_calls == []                                                   # TBA has not posted yet
    W.sync(EV, FakeTBA([tba(1)]))
    assert rec_calls == [f"{EV}_qm1"]
    assert json.loads(p.read_text())["live"]["tba_posted"] is True
    W.sync(EV, FakeTBA([tba(1)]))
    assert rec_calls == [f"{EV}_qm1"]                                        # done: not again


def test_proxy_backfill_for_videos_kept_by_processors(env, monkeypatch):
    import proxies
    (env["evd"] / "matches").mkdir()
    (env["media"] / EV).mkdir()
    for n in (1, 2, 3):
        (env["evd"] / "matches" / f"{EV}_qm{n}.json").write_text("{}")
    for n in (1, 2):
        (env["media"] / EV / f"{EV}_qm{n}.mp4").write_bytes(b"x")              # qm3: no kept video
    sent = []
    monkeypatch.setattr(proxies, "submit", lambda media, ev, key, log=None: sent.append(key))
    monkeypatch.setattr(proxies, "up_to_date", lambda media, ev, key: key.endswith("qm2"))
    monkeypatch.setenv("MEDIA_PROXIES", "1")
    assert W.backfill_proxies(EV) == 1 and sent == [f"{EV}_qm1"]
    assert W.backfill_proxies(EV) == 0                                       # once per video version
    os.utime(env["media"] / EV / f"{EV}_qm1.mp4", ns=(1, 1))                 # the video was replaced
    assert W.backfill_proxies(EV) == 1
    monkeypatch.setattr(W, "PROCESS_ONLY", True)
    os.utime(env["media"] / EV / f"{EV}_qm1.mp4", ns=(2, 2))
    assert W.backfill_proxies(EV) == 0                                       # processors never encode


def test_dynamic_mode_per_source(env, monkeypatch):
    """liveingest resolves the source's choice (sources.json dynamic, null = program_feed) into live["dynamic"];
    env DYNAMIC=1 / 0 overrides it."""
    import liveingest as LI
    doc = json.loads((env["evd"] / "sources.json").read_text())
    s = doc["sources"][0]

    def live_dyn(**kw):
        s.update(kw); (env["evd"] / "sources.json").write_text(json.dumps(doc))
        return W.dynamic_for(LI.LiveView(EV).for_processing(tba(1), True))
    assert live_dyn() is False                                               # no flags: fixed view
    assert live_dyn(program_feed=True) is True                               # auto: a program feed
    assert live_dyn(dynamic=False) is False                                  # the admin says no
    assert live_dyn(dynamic=None, program_feed=False) is False
    assert live_dyn(dynamic=True) is True
    monkeypatch.setenv("DYNAMIC", "0")
    assert live_dyn() is False                                               # env forces off
    monkeypatch.setenv("DYNAMIC", "1")
    assert live_dyn(dynamic=False) is True and W.dynamic_for(None) is True   # ... and on
    monkeypatch.delenv("DYNAMIC")
    assert W.dynamic_for(None) is False and W.dynamic_for({"dynamic": True}) is True


def test_process_match_passes_dynamic_and_writes_the_match_file_last(env, monkeypatch):
    import process, enrich, liveingest as LI
    seen = {}
    mp = env["evd"] / "matches" / f"{EV}_qm1.json"

    def fake_process(vpath, cam, model, tags, **kw):
        seen["dynamic"] = kw.get("dynamic")
        return dict(feed_ok=0.97, pose={}, camera_pose=None, tracks=[], identity={})

    def fake_enrich(evd, key, *a, **k):
        seen["file_during_enrich"] = mp.exists()
    monkeypatch.setattr(process, "process", fake_process)
    monkeypatch.setattr(enrich, "enrich", fake_enrich)
    doc = json.loads((env["evd"] / "sources.json").read_text())
    doc["sources"][0]["program_feed"] = True
    (env["evd"] / "sources.json").write_text(json.dumps(doc))
    as_processor(monkeypatch, "gpu1-a")
    lv = LI.LiveView(EV)
    m, status = tba(1), {}
    live = lv.for_processing(m, True)
    assert W.process_match(EV, m, {"video_mode": "livestream"}, status, {}, live=live) is True
    assert seen == {"dynamic": True, "file_during_enrich": False} and mp.exists()
    assert not (env["evd"] / "status.json").exists()
    assert json.loads((env["evd"] / "proc_status" / f"{EV}_qm1.json").read_text())["state"] == "done"
    assert (env["media"] / EV / f"{EV}_qm1.mp4").exists()                     # clip kept (no proxy here)


def test_process_only_loop_once_gpu_and_cleanup(env, monkeypatch):
    (env["data"] / "config" / "worker.json").write_text(json.dumps({"events": [EV], "options": {"gpu_device": 1}}))
    model = env["data"] / "robot.onnx"; model.write_bytes(b"m")
    monkeypatch.setattr(W, "MODEL", str(model))
    monkeypatch.setattr(W, "_ENV0", dict(W._ENV0, GPU_DEVICE="0", TBA_KEY=None))
    monkeypatch.setattr(W, "SharedTBA", lambda key: FakeTBA([tba(1), tba(2)]))
    monkeypatch.setenv("ONCE", "1"); monkeypatch.setenv("WORKER_ID", "gpu0-a")
    monkeypatch.setattr(W, "PROCESS_ONLY", False); monkeypatch.setattr(W, "WORKER_ID", "x")
    calls = []
    monkeypatch.setattr(W, "process_match", fake_track(env, calls))
    W.CL.claim(str(env["data"]), EV, f"{EV}_qm2", "gpu0-a")                 # left over from before a restart
    old = signal.getsignal(signal.SIGTERM)
    try:
        W.process_only()
    finally:
        signal.signal(signal.SIGTERM, old)
    assert calls == [("gpu0-a", f"{EV}_qm1", True)] and os.environ["GPU_DEVICE"] == "0"   # env wins over Admin
    assert not (env["evd"] / "claims" / f"{EV}_qm1.json").exists()          # released after the match
    assert not (env["evd"] / "claims" / f"{EV}_qm2.json").exists()          # the old claim was given back
    assert not os.path.exists(W._proc_file("gpu0-a"))                       # heartbeat file gone on exit


def test_process_only_stopped_mid_match_gives_the_match_back(env, monkeypatch):
    (env["data"] / "config" / "worker.json").write_text(json.dumps({"events": [EV]}))
    model = env["data"] / "robot.onnx"; model.write_bytes(b"m")
    monkeypatch.setattr(W, "MODEL", str(model))
    monkeypatch.setattr(W, "SharedTBA", lambda key: FakeTBA([tba(1)]))
    monkeypatch.setenv("WORKER_ID", "gpu0-a")
    monkeypatch.setattr(W, "PROCESS_ONLY", False); monkeypatch.setattr(W, "WORKER_ID", "x")

    def pm(ev, m, cfg, status, mc, live=None):
        st = status.setdefault(m["key"], {}); st.update(state="processing", at=time.time(), attempts=1)
        W.save_status(ev, status, m["key"])
        raise SystemExit(0)                                                   # SIGTERM (docker stop)
    monkeypatch.setattr(W, "process_match", pm)
    old = signal.getsignal(signal.SIGTERM)
    try:
        with pytest.raises(SystemExit):
            W.process_only()
    finally:
        signal.signal(signal.SIGTERM, old)
    st = json.loads((env["evd"] / "proc_status" / f"{EV}_qm1.json").read_text())
    assert st["state"] == "pending" and st["attempts"] == 0
    assert not (env["evd"] / "claims" / f"{EV}_qm1.json").exists()


def test_processor_trips_after_failures_and_reports_unhealthy(env, monkeypatch):
    (env["data"] / "config" / "worker.json").write_text(json.dumps({"events": [EV]}))
    model = env["data"] / "robot.onnx"; model.write_bytes(b"m")
    monkeypatch.setattr(W, "MODEL", str(model))
    monkeypatch.setattr(W, "SharedTBA", lambda key: FakeTBA([tba(1), tba(2), tba(3)]))
    monkeypatch.setenv("WORKER_ID", "gpu0-a")
    monkeypatch.setattr(W, "PROCESS_ONLY", False); monkeypatch.setattr(W, "WORKER_ID", "x")
    calls, beats = [], []

    def pm(ev, m, cfg, status, mc, live=None):
        calls.append(m["key"])
        st = status.setdefault(m["key"], {})                                  # livestream-only: a failed clip
        st.update(state="waiting_live", at=time.time(), error="CUDA out of memory", live_failed=st.get("live_failed", 0) + 1)
        return False
    monkeypatch.setattr(W, "process_match", pm)
    real_beat = W.proc_beat
    monkeypatch.setattr(W, "proc_beat", lambda **s: beats.append(s.get("state")) or real_beat(**s))

    def stop_when_unhealthy(*a, **k):
        if "unhealthy" in beats: raise KeyboardInterrupt
    monkeypatch.setattr(W, "_sleep", stop_when_unhealthy)
    old = signal.getsignal(signal.SIGTERM)
    try:
        with pytest.raises(KeyboardInterrupt):
            W.process_only()
    finally:
        signal.signal(signal.SIGTERM, old)
    assert len(calls) == W.PROC_TRIP and beats[-1] == "unhealthy"


def test_shared_tba_reads_the_cache_and_never_writes_it(env, monkeypatch):
    d = env["data"] / "tba"; d.mkdir()
    p = d / f"event__{EV}__matches.json"
    p.write_text(json.dumps({"etag": "x", "fetched": time.time(), "data": [tba(1)]}))
    assert [m["key"] for m in W.SharedTBA(None).matches(EV)] == [f"{EV}_qm1"]
    p.write_text(json.dumps({"etag": "x", "fetched": time.time() - 3600, "data": [tba(1)]}))
    assert W.SharedTBA(None).matches(EV)[0]["key"] == f"{EV}_qm1"          # stale, no key: the stale copy
    got = []
    monkeypatch.setattr(W.TBA, "get", lambda self, ep, max_age=0: got.append((self.cache_dir, ep)) or [tba(2)])
    monkeypatch.setattr(W, "WORKER_ID", "gpu0-a")
    assert W.SharedTBA("k").matches(EV)[0]["key"] == f"{EV}_qm2"
    assert got[0][0].endswith(os.path.join("cache", "tba-gpu0-a"))          # its own cache dir
    assert json.loads(p.read_text())["etag"] == "x"                          # the main worker's file untouched
    p.unlink()
    monkeypatch.setattr(W.TBA, "get", lambda self, ep, max_age=0: (_ for _ in ()).throw(OSError("offline")))
    with pytest.raises(RuntimeError):
        W.SharedTBA("k").matches(EV)


def test_main_worker_wakes_up_when_a_match_file_appears(env, monkeypatch):
    s0 = W._matches_sig([EV])
    (env["evd"] / "matches").mkdir()
    assert W._matches_sig([EV]) != s0
    s1 = W._matches_sig([EV])
    (env["evd"] / "matches" / f"{EV}_qm1.json").write_text("{}")
    assert W._matches_sig([EV]) != s1
    clock = [0.0]
    monkeypatch.setattr(W.time, "time", lambda: clock[0])

    def tick(s):
        clock[0] += s
        if clock[0] >= 10: (env["evd"] / "matches" / f"{EV}_qm2.json").write_text("{}")
    monkeypatch.setattr(W.time, "sleep", tick)
    W._sleep(120, [EV])
    assert clock[0] < 20                                                     # woke at the new file, not after 120 s


def test_gpu_out_of_memory_is_not_a_failed_try(env, monkeypatch):
    """N=2 jobs on a card with ~350 MiB free (atomosserver GPU 1 beside llama-server): the detector's CUDA arena
    fails.  That must not burn the clip's tries (livestream-only: 3 -> "failed"); the pass stops."""
    import process, liveingest as LI
    oom = ("[ONNXRuntimeError] : 1 : FAIL : Non-zero status code returned while running Conv node. "
           "bfc_arena.cc:360 Failed to allocate memory for requested buffer of size 11141120")
    tried = []

    def boom(vpath, *a, **k):
        tried.append(vpath); raise RuntimeError(oom)
    monkeypatch.setattr(process, "process", boom)
    m, status = tba(1), {}
    live = LI.LiveView(EV).for_processing(m, True)
    assert W.process_match(EV, m, {"video_mode": "livestream"}, status, {}, live=live) is False
    st = status[m["key"]]
    assert st["state"] == "waiting_live" and st["attempts"] == 0 and not st.get("live_failed") \
        and not st.get("live_skip") and "out of memory" in st["note"]
    assert W.gpu_oom([oom]) and not W.gpu_oom(["decoder exploded"]) and not W.gpu_oom([])
    # the main worker's pass stops at the first one (qm2, qm3 would hit the same full card)
    monkeypatch.setattr(W, "build_site", lambda ev, t: None)
    tried.clear()
    W.sync(EV, FakeTBA([tba(1), tba(2), tba(3)]))
    assert len(tried) == 1
