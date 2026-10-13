"""Unit tests for live/strategy/matchday.py (pure helpers; no data files needed)."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live.strategy import matchday as MD  # noqa: E402
from live.strategy.data import LEN, WID  # noqa: E402


def test_their_view_zone_flips_sides_and_ends():
    assert MD.their_view_zone("own zone / left trench") == "their end, our right trench"
    assert MD.their_view_zone("opp zone / right bump") == "our end, our left bump"
    assert MD.their_view_zone("neutral / centre") == "midfield, our centre"


def test_field_xy_ours():
    # target's own wall (x=LEN) is the far end from us; its driver-left (low y) is our right
    assert MD.field_xy_ours(LEN, 0.0) == [1.0, 1.0]
    assert MD.field_xy_ours(0.0, WID) == [0.0, 0.0]


def _row(i, y, pb, pn, em, mg, post):
    return {"key": f"ev_qm{i}", "short": f"Q{i}", "post": post, "y": y, "p_blend": pb, "p_novideo": pn, "p_epa": 0.5,
            "epa_margin": em, "margin": mg, "auto_pred": {"red": 30, "blue": 30}, "auto_act": {"red": 40, "blue": 36}}


def test_backtest_asof_uses_only_posted_matches():
    rows = [_row(i, i % 2, 0.8 if i % 2 else 0.2, 0.6 if i % 2 else 0.4, 10, 20, 100 * i) for i in range(1, 21)]
    b = MD.backtest_asof(rows, 1000)
    assert b["n"] == 10 and b["through"] == "Q10"
    assert math.isclose(b["brier"]["blend"], 0.04, abs_tol=1e-6)
    assert b["brier"]["blend"] < b["brier"]["no_video"] < b["brier"]["epa"]
    assert b["auto_bias_recent"]["mean"] == 8.0
    assert MD.backtest_asof(rows, 50)["n"] == 0


def test_epa_sd_shrinks_toward_prior_with_few_rows():
    assert math.isclose(MD.epa_sd([], None), 80.0)
    rows = [_row(1, 1, .5, .5, 0, 10, 10)]
    assert MD.epa_sd(rows, None) < 80.0


def test_defense_measured_only_when_library_says_significant():
    """defense.json teams[].retention: 'measured' needs significant (BH + FCR-adjusted interval), not just the raw
    90% interval below 100% (production had 5 such teams with q ~0.43 and 0 discoveries)."""
    eff = {"retained": 0.71, "lo": 0.56, "hi": 0.88, "significant": True, "source": "library"}
    loose = {"est": 0.55, "lo": 0.31, "hi": 0.93, "q": 0.43, "lo_adj": 0.2, "hi_adj": 1.4, "significant": False}
    sig = {"est": 0.40, "lo": 0.25, "hi": 0.62, "q": 0.04, "lo_adj": 0.21, "hi_adj": 0.71, "significant": True}
    X = {"defense_effect": eff, "lib_teams": {"frc488": {"team": "frc488", "retention": loose},
                                              "frc254": {"team": "frc254", "retention": sig}}}
    ret, kind, txt = MD.retention_for("frc488", X)
    assert kind == "assumption" and ret == 0.71 and "event-wide" in txt
    ret, kind, txt = MD.retention_for("frc254", X)
    assert kind == "measured" and ret == 0.40 and "21–71%" in txt          # the adjusted interval is shown
    assert MD.retention_for("frc9999", X)[1] == "assumption"
    assert MD.lib_significant(loose) is None and MD.lib_significant(sig) is sig
    assert MD.lib_significant({"est": 0.5, "significant": "yes"}) is None    # only a real True counts


def test_preview_defense_factors_follow_the_library():
    from types import SimpleNamespace
    from live.strategy import preview as PV
    comp = {c: {"beta": 1.0, "beta_sd": 1.0} for c in ("trans", "active", "endgame")}
    M = SimpleNamespace(comp=comp)
    al = {"p_win_auto": 0.5, "fuel": {"mean": 60.0}, "fuel_by_period_mean": {"auto": 10.0}}
    S = {"p_red_win": 0.5, "n_sims": 100, "margin": {"mean": 0, "q25": -5, "q75": 5},
         "alliances": {"red": al, "blue": al}}
    bp = {p: 5.0 for p in ("auto", "transition", "shift1", "shift2", "shift3", "shift4", "endgame")}

    def robot(t, a, rr):
        return {"team": t, "number": t[3:], "alliance": a, "expected_fuel": {"total": 35.0, "by_period": bp},
                "defended": {"rate_retained": rr}, "reliability": {"dead_rate": None},
                "climb": {"end": {"L0": 1.0}, "n_obs": 0, "auto_l1": 0, "expected_pts": 0},
                "data_quality": {"video_weight": 0.9, "n_video_matches": 4},
                "rating": {"active": {"mean": 20.0, "sd": 4.0}}}
    shrunk = {"v": 0.5, "n_eff": 3.0}                 # the old trigger: shrunk per-robot estimate < 75%
    robots = [robot("frc1", "red", shrunk), robot("frc2", "red", None), robot("frc3", "red", None),
              robot("frc4", "blue", shrunk), robot("frc5", "blue", None), robot("frc6", "blue", None)]
    loose = {"est": 0.55, "lo": 0.31, "hi": 0.93, "significant": False}
    sig = {"est": 0.40, "lo_adj": 0.21, "hi_adj": 0.71, "significant": True}
    kinds = lambda F: {(f["team"], f.get("basis")) for f in F if f["kind"].startswith("defense_")}
    lib = {"frc1": {"retention": loose}, "frc5": {"retention": sig}}
    assert kinds(PV.key_factors(M, S, robots, [], [], lib)) == {("frc5", "measured")}
    assert kinds(PV.key_factors(M, S, robots, [], [], None)) == {("frc1", "assumption"), ("frc4", "assumption")}
