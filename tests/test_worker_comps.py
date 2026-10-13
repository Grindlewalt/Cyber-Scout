"""Worker with several competitions: live one first, a finished event's backlog never holds up today's matches,
per-competition calibration gate, team number from the app settings. Pure file fixtures (no video, no network)."""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "live"))

DATES = {"2026live": ("2026-10-10", "2026-10-12"), "2026next": ("2026-11-01", "2026-11-03"),
         "2026old": ("2026-03-06", "2026-03-08"), "2026older": ("2026-02-01", "2026-02-03"),
         "2026live2": ("2026-10-12", "2026-10-13")}


class FakeTBA:
    def event(self, ev):
        if ev not in DATES: raise RuntimeError("404")
        s, e = DATES[ev]
        return {"key": ev, "start_date": s, "end_date": e}


@pytest.fixture
def W(tmp_path, monkeypatch):
    import worker
    monkeypatch.setattr(worker, "DATA", str(tmp_path))
    return worker


def test_comp_order_live_first(W):
    evs = ["2026older", "2026next", "2026unknown", "2026old", "2026live2", "2026live"]
    order, live = W.comp_order(evs, FakeTBA(), today="2026-10-12")
    assert order == ["2026live", "2026live2", "2026next", "2026old", "2026older", "2026unknown"]
    assert live == {"2026live", "2026live2"}
    order, live = W.comp_order(evs, FakeTBA(), today="2026-12-01")
    assert order[:3] == ["2026next", "2026live2", "2026live"] and live == set()


def test_sync_all_caps_backlog_while_a_comp_is_live(W, monkeypatch):
    calls = []
    monkeypatch.setattr(W, "sync", lambda ev, tba, cap=None: calls.append((ev, cap)) or ev == "2026old")
    monkeypatch.setattr(W, "comp_order", lambda evs, tba: (["2026live", "2026old"], {"2026live"}))
    assert W.sync_all(["2026old", "2026live"], FakeTBA()) is True         # old has more -> come back soon
    assert calls == [("2026live", None), ("2026old", W.BACKLOG_BATCH)]
    calls.clear()
    monkeypatch.setattr(W, "comp_order", lambda evs, tba: (["2026old"], set()))
    W.sync_all(["2026old"], FakeTBA())
    assert calls == [("2026old", None)]                                   # nothing live: no cap (as before)
    # one competition failing never stops the others
    calls.clear()
    monkeypatch.setattr(W, "comp_order", lambda evs, tba: (["2026live", "2026old"], {"2026live"}))
    monkeypatch.setattr(W, "sync", lambda ev, tba, cap=None: (_ for _ in ()).throw(RuntimeError("boom"))
                        if ev == "2026live" else calls.append(ev))
    W.sync_all(["2026live", "2026old"], FakeTBA())
    assert calls == ["2026old"]


def test_sync_without_calibration_waits_and_reports(W, tmp_path, monkeypatch):
    ev = "2026zzcomp"                          # no camera.json in the data dir or the repo
    os.makedirs(tmp_path / "events" / ev)
    calls, built = [], []
    monkeypatch.setattr(W, "process_match", lambda *a, **k: calls.append(a[1]["key"]) or True)
    monkeypatch.setattr(W, "build_site", lambda ev, tba: built.append(ev))
    m = dict(key=f"{ev}_qm1", comp_level="qm", set_number=1, match_number=1, time=1,
             alliances={a: dict(team_keys=[], score=50) for a in ("red", "blue")}, videos=[])

    class T:
        def matches(self, e): return [m]
    assert not W.is_calibrated(ev)
    assert W.sync(ev, T()) is False and calls == [] and built == [ev]      # skipped, site still built
    (tmp_path / "events" / ev / "camera_url-abc.json").write_text("{}")
    assert W.is_calibrated(ev)                                             # a livestream camera counts for the flag
    (tmp_path / "events" / ev / "camera.json").write_text("{}")
    assert W.sync(ev, T(), 1) is True and calls == [f"{ev}_qm1"]           # stopped at max_new -> more


def test_load_config_team_number(W, tmp_path, monkeypatch):
    for k in ("DASH_TEAM", "DASH_GOAL", "TBA_KEY", "GPU_DEVICE", "FORCE_CPU"):   # restored after the test (load_config sets them)
        monkeypatch.setenv(k, "x"); monkeypatch.delenv(k)
    monkeypatch.setitem(W._ENV0, "DASH_TEAM", None)
    monkeypatch.setitem(W._ENV0, "DASH_GOAL", None)
    conf = tmp_path / "config" / "worker.json"
    conf.parent.mkdir()
    monkeypatch.setattr(W, "WORKER_CONF", str(conf))
    conf.write_text(json.dumps({"tba_key": "k", "events": ["2026a", "2026b"], "team_number": 1983,
                                "season_goal": "Worlds", "options": {}}))
    assert W.load_config() == ("k", ["2026a", "2026b"])
    assert os.environ["DASH_TEAM"] == "frc1983" and os.environ["DASH_GOAL"] == "Worlds"
    conf.write_text(json.dumps({"tba_key": "k", "events": ["2026a"], "team_number": None, "options": {}}))
    W.load_config()
    assert "DASH_TEAM" not in os.environ and "DASH_GOAL" not in os.environ   # back to the .env / default
    monkeypatch.setitem(W._ENV0, "DASH_GOAL", "rank:8")                    # .env value when the app has none
    conf.write_text(json.dumps({"events": [], "season_goal": "Scouting"}))
    W.load_config()
    assert os.environ["DASH_GOAL"] == "rank:8"


def test_engine_goal(W):
    assert [W.engine_goal(g) for g in ("DCMP", "Worlds", "Einstein", "rank:12", "Win an event", "Scouting", None, "rank:x")] == \
        ["DCMP", "Worlds", "Worlds", "rank:12", None, None, None, None]
