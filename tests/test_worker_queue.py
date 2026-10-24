"""The Matches tab's queue: why every untracked match waits (live/worker.py row_wait -> site row wait + state_note).
Pure file fixtures (no video, no network)."""
import json
import os
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "live"))

import worker as W  # noqa: E402

EV = "2026test"


def m(n, score=-1, **kw):
    return dict(dict(key=f"{EV}_qm{n}", comp_level="qm", set_number=1, match_number=n, time=1000 + n,
                     alliances={a: dict(team_keys=[f"frc{a[0] == 'r' and n or n + 10}"], score=score) for a in ("red", "blue")}), **kw)


def rw(state="pending", st=None, mm=None, played=True, setup=False, ready=False, only_live=False, model=True):
    return W.row_wait(state, st or {}, mm or m(1, score=50), played, setup, ready, only_live, model)


def test_row_wait_reasons():
    assert rw("done") == (None, None) and rw("processing") == (None, None)
    assert rw(played=False, mm=m(1)) == ("not_played", None)
    assert rw(setup=True) == ("setup", W.SETUP_NOTE)
    assert W.SETUP_NOTE == "waiting for video setup (camera calibration on the Video page)"
    assert rw(st={"state": "no_video"}) == ("video", "no match video yet: checked again every 5 min")
    assert rw() == ("queued", "queued: tracked in match order")
    assert rw(model=False) == ("model", "robot tracking waits for the detector model")
    w, note = rw("error", st={"state": "error", "attempts": 2})
    assert w == "retry" and note.startswith("tracking failed (try 2 of 3)")
    w, note = rw("failed", st={"state": "failed", "attempts": 3})
    assert w == "failed" and "3 tries" in note
    # a trackable stream clip needs no upload camera, and no TBA result
    assert rw(setup=True, ready=True, mm=m(1)) == ("queued", "queued: tracked in match order")
    # the stream saw it (row.live), TBA hasn't posted, no clip to track: uploads come after TBA
    assert rw("live", mm=m(1))[0] == "video"
    # livestream only: the loop's own note stays; no camera at all / a stream that can't be tracked -> setup
    st = {"state": "waiting_live", "note": "the stream analyser has not found this match"}
    assert rw("waiting_live", st=st, only_live=True) == ("stream", st["note"])
    assert rw("waiting_live", st=st, only_live=True, setup=True) == ("setup", W.SETUP_NOTE)
    st = {"state": "waiting_live", "note": "stream not trackable: the stream's layout has no field/overhead box"}
    assert rw("waiting_live", st=st, only_live=True) == ("setup", "waiting for video setup (the stream's layout has no field/overhead box)")
    st = {"state": "failed", "note": "livestream clip could not be tracked (3 tries); see the worker log"}
    assert rw("failed", st=st, only_live=True) == ("failed", st["note"])


class FakeTBA:
    def __init__(self, ms): self.ms = ms
    def event(self, ev): return {"key": ev, "name": "Test Event", "year": 2026, "start_date": "2026-10-24"}
    def matches(self, ev): return self.ms
    def teams(self, ev): return [{"key": f"frc{n}", "team_number": n, "nickname": f"T{n}"} for n in (1, 2, 3, 11, 12, 13)]
    def oprs(self, ev): return {}
    def rankings(self, ev): return {}


def test_build_site_rows_say_why(tmp_path, monkeypatch):
    """Played + no upload camera -> needs video setup; unplayed -> not_played (no note); camera -> queued / no video."""
    data = tmp_path / "data"
    (data / "events" / EV).mkdir(parents=True)
    monkeypatch.setattr(W, "DATA", str(data))
    monkeypatch.setattr(W, "API", str(data / "site" / "api"))
    monkeypatch.setattr(W, "MODEL", str(tmp_path / "robot.onnx"))
    (tmp_path / "robot.onnx").write_bytes(b"x")
    monkeypatch.setenv("DASHBOARD", "0")
    monkeypatch.setitem(sys.modules, "scouting", types.SimpleNamespace(site_build_extra=lambda *a, **k: None))
    monkeypatch.setattr(W, "live_view", lambda ev: None)
    sys.path.insert(0, ROOT)
    from live.strategy import goals
    monkeypatch.setattr(goals, "site_build", lambda *a, **k: None)
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)
    ms = [m(1, score=40, actual_time=time.time() - 900), m(2, score=-1), m(3, score=55)]
    W.build_site(EV, FakeTBA(ms))
    rows = {r["short"]: r for r in json.load(open(data / "site" / "api" / EV / "event.json"))["matches"]}
    assert rows["Q1"]["wait"] == "setup" and rows["Q1"]["state_note"] == W.SETUP_NOTE
    assert rows["Q2"]["wait"] == "not_played" and "state_note" not in rows["Q2"] and rows["Q2"]["state"] == "pending"
    (data / "events" / EV / "camera.json").write_text("{}")
    (data / "events" / EV / "status.json").write_text(json.dumps({f"{EV}_qm3": {"state": "no_video", "at": time.time()}}))
    W.build_site(EV, FakeTBA(ms))
    rows = {r["short"]: r for r in json.load(open(data / "site" / "api" / EV / "event.json"))["matches"]}
    assert rows["Q1"]["wait"] == "queued" and rows["Q3"]["wait"] == "video"
    assert rows["Q3"]["state_note"].startswith("no match video yet") and rows["Q2"]["wait"] == "not_played"
    q = json.load(open(data / "site" / "api" / EV / "event.json"))["queue"]
    assert q["waiting"] == {"queued": 1, "not_played": 1, "video": 1} and q["setup"] is False and q["tracking"] == []


def test_queue_summary():
    now = 100000.0
    rows = [dict(key="a", short="Q1", state="done"), dict(key="b", short="Q2", state="processing"),
            dict(key="c", short="Q3", state="pending", wait="setup"),
            dict(key="d", short="Q5", state="pending", wait="not_played", time=now + 900, pred=now + 1200),
            dict(key="e", short="Q4", state="pending", wait="not_played", time=now + 600),
            dict(key="f", short="Q0", state="pending", wait="not_played", time=now - 4 * 3600)]   # stale schedule
    q = W.queue_summary(rows, True, now)
    assert q["tracking"] == ["b"] and q["waiting"] == {"setup": 1, "not_played": 3} and q["setup"] is True
    assert q["next"] == {"key": "e", "short": "Q4", "time": now + 600}
    assert W.queue_summary([], False, now)["next"] is None
