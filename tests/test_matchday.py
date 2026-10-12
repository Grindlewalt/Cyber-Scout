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
