"""Hub on / off split (analytics._hub_phases) and clears of the other alliance's hub (analytics._is_clear).

Synthetic red robot, 10 Hz, rules-file period table (shift1 30-55 s ...): in its hub-active shifts (1, 3) it sits in its own alliance zone
and shoots; in the inactive shifts (2, 4) it sits in the neutral zone, intakes, passes and lobs one ball at the blue
hub (a clear, not a missed shot).
"""
import json
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "live"))
import analytics as AN  # noqa: E402

HZ, T_TELEOP = 10.0, 23.5
SCHED = {"red": {"shift1": True, "shift2": False, "shift3": True, "shift4": False}}
RULES = json.load(open(os.path.join(os.path.dirname(HERE), "live", "field", "rebuilt_rules.json")))


def _replay(mc):
    """match clock -> replay time (inverse of analytics.match_clock for teleop)."""
    return mc + (T_TELEOP - 0.5 - 20.0)


def _shot(mc, outcome, target="red_hub"):
    return {"shooter": {"track_idx": 0, "kind": "robot"}, "match_t": _replay(mc), "outcome": outcome, "target": target}


def _setup(obs_off=1):
    ctx = AN.Ctx(RULES)
    per = {name: (a, b) for name, a, b in ctx.periods}
    n = int((_replay(ctx.t_end) + 0.5) * HZ)
    mc = AN.match_clock(np.arange(n) / HZ - 0.5, {"t_teleop_start": T_TELEOP})
    within = lambda *names: np.any([(mc >= per[k][0]) & (mc < per[k][1]) for k in names], 0)
    on, off = within("shift1", "shift3"), within("shift2", "shift4")
    x = np.where(on, 14.0, 8.27)                       # red canonical frame: x > Z_own is the own alliance zone
    xy = np.stack([x, np.full(n, 4.0)], -1) * 100
    obs = np.where(off, obs_off, 1)
    match = {"hz": HZ, "t_teleop_start": T_TELEOP,
             "tracks": [{"alliance": "red", "xy": xy.tolist(), "obs": obs.tolist()}]}
    P = [AN.Prep(match, 0, ctx)]
    events = {"shots": [_shot(40, "made"), _shot(41, "made"), _shot(45, "missed"),       # on: 3 hub shots
                        _shot(70, "missed", "blue_hub"), _shot(75, "pass"),               # off: clear + pass
                        {**_shot(90, "made"), "shooter": {"track_idx": 1, "kind": "robot"}}],  # someone else
              "intakes": [{"track_idx": 0, "match_t": _replay(t)} for t in (56, 60, 65, 78)]}
    return ctx, P, match, events


def test_is_clear():
    assert AN._is_clear({"outcome": "missed", "target": "blue_hub"}, "red")
    assert AN._is_clear({"outcome": "missed", "target": "red_hub"}, "blue")
    assert not AN._is_clear({"outcome": "missed", "target": "red_hub"}, "red")
    assert not AN._is_clear({"outcome": "missed", "target": None}, "red")
    assert not AN._is_clear({"outcome": "missed"}, "red")
    assert not AN._is_clear({"outcome": "made", "target": "blue_hub"}, "red")
    assert not AN._is_clear({"outcome": "pass", "target": "blue_hub"}, "red")


def test_hub_phases_split():
    ctx, P, match, events = _setup()
    out, cov = AN._hub_phases(0, P, ctx, match, events, SCHED, None, P[0].n)
    on, off = out["hub_on"], out["hub_off"]
    # off shifts lose their first HUB_GRACE_S (fuel still counts then): 2 x (25 - 3) s
    assert on["observed_s"] == pytest.approx(50, abs=0.3) and off["observed_s"] == pytest.approx(44, abs=0.3)
    assert cov == pytest.approx(1.0, abs=0.01)
    # on: 3 hub shots (2 made) in 50 s; nothing else
    assert on["shots_pm"] == pytest.approx(3.6, abs=0.05) and on["made_pm"] == pytest.approx(2.4, abs=0.05)
    assert on["passes_pm"] == 0 and on["intakes_pm"] == 0
    assert on["zone"]["own"] == pytest.approx(1.0) and on["defending_frac"] == 0
    # off: the blue-hub "miss" is a clear -> a pass, not a shot
    assert off["shots_pm"] == 0 and off["made_pm"] == 0
    # clear + pass and 3 intakes over 44 s; the intake at 56 s is in the grace window after the 55 s boundary
    assert off["passes_pm"] == pytest.approx(2.73, abs=0.05) and off["intakes_pm"] == pytest.approx(4.09, abs=0.05)
    assert off["zone"]["neutral"] == pytest.approx(1.0)


def test_hub_phases_unknown_schedule_and_unobserved():
    ctx, P, match, events = _setup()
    assert AN._hub_phases(0, P, ctx, match, events, None, None, P[0].n) == (None, None)
    tie = {"red": {f"shift{k}": None for k in range(1, 5)}}
    assert AN._hub_phases(0, P, ctx, match, events, tie, None, P[0].n) == (None, None)
    ctx, P, match, events = _setup(obs_off=0)          # never seen while its hub is off
    out, cov = AN._hub_phases(0, P, ctx, match, None, SCHED, None, P[0].n)
    assert out["hub_off"] is None and cov == 0
    assert "shots_pm" not in out["hub_on"]             # no events file: time/zone only, no rates
    assert out["hub_on"]["observed_s"] == pytest.approx(50, abs=0.3)
