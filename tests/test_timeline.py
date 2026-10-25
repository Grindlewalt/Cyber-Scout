import math
import unittest

import numpy as np

from live.strategy import behavior as B
from live.strategy import timeline as TL


def mk(i, side=1.5, noise=0.0, shift=0, score_t=6.0, bursts=None, team="frc1", station=1):
    """A synthetic robot that starts at its alliance wall on `side` (canonical y), shoots at 6 s, then loops 13 <-> 8 m."""
    rng = np.random.default_rng(i)
    t = np.arange(161)
    x = 13.0 - 2.5 * (1 - np.cos(2 * math.pi * (t - shift) / 14.0))
    y = side + 0.3 * np.sin(t / 5.0) + rng.normal(0, noise, 161)
    tl = np.c_[x, y]
    cyc = [[(13 - 0.5 * k, side) for k in range(10)] for _ in range(3)]
    b = bursts or dict(auto=1, transition=1, shift1=2, shift2=1, shift3=2, shift4=1, endgame=1)
    return dict(key=f"m{i}", team=team, alliance="red", station=station, partners=["frc2", "frc3"], tl=tl,
                ev=dict(first_pickup=2.0, first_score=score_t, first_score_xy=[13.0, side], first_defense=None,
                        cross_neutral=9.0, climb=None), bursts=b, cycles=cyc, start=[13.0, side],
                active={p: True for p in B.PERIODS}, intakes=[], made={p: 1 for p in B.PERIODS})


def hist(n=5, **kw):
    return [mk(i, **kw) for i in range(n)]


class TimelineTests(unittest.TestCase):
    def build(self, h, v=TL.VERSION, al="red", prior=None):
        return TL.team_timeline(h, 1, set(), {}, al, version=v, prior=prior)

    def test_speed_limit_and_shape(self):
        for v in TL.OPTS:
            r = self.build(hist(4, noise=0.4), v)
            p = r["timeline"]["_canon"]
            self.assertEqual(p.shape, (TL.T, 2))
            step = np.hypot(*np.diff(p, axis=0).T)
            self.assertLessEqual(step.max(), TL.VMAX + 1e-6, v)
            self.assertTrue((p[:, 0] > 0).all() and (p[:, 0] < B.LEN).all() and (p[:, 1] > 0).all() and (p[:, 1] < B.WID).all())
            t = r["timeline"]
            self.assertEqual(len(t["xy"]), len(t["spread"]), TL.T)

    def test_phases_cover_the_match_and_events_ordered(self):
        t = self.build(hist(4))["timeline"]
        ph = t["phases"]
        self.assertEqual(ph[0]["t0"], 0)
        self.assertGreaterEqual(ph[-1]["t1"], 160)
        for a, b in zip(ph, ph[1:]):
            self.assertEqual(a["t1"], b["t0"])
        ts = [e["t"] for e in t["events"]]
        self.assertEqual(ts, sorted(ts))
        self.assertIn("first_score", [e["kind"] for e in t["events"]])
        self.assertAlmostEqual(next(e for e in t["events"] if e["kind"] == "first_score")["t"], 6.0, 1)

    def test_team_that_goes_left_keeps_going_left(self):
        for al in ("red", "blue"):
            r = self.build(hist(5, side=1.5, noise=0.2), al=al)
            ys = np.array([q[1] for q in r["timeline"]["xy"]])
            # canonical y 1.5 (low) is the driver's left: field y low for red, high for blue
            if al == "red":
                self.assertLess(np.median(ys), B.WID / 2)
            else:
                self.assertGreater(np.median(ys), B.WID / 2)
        right = self.build(hist(5, side=6.5, noise=0.2))
        self.assertGreater(np.median([q[1] for q in right["timeline"]["xy"]]), B.WID / 2)

    def test_two_modes_do_not_collapse_to_the_middle(self):
        h = hist(3, side=1.5) + hist(3, side=6.5)
        h = [dict(r, key=f"x{i}") for i, r in enumerate(h)]
        p = self.build(h, "v2")["timeline"]["_canon"]
        self.assertTrue((np.abs(p[:, 1] - B.WID / 2) > 1.5).mean() > 0.9)

    def test_path_heat_peaks_on_travelled_route(self):
        r = self.build(hist(4, side=1.5))
        cells = r["path_heat"]["cells"]
        self.assertTrue(cells)
        best = max(cells, key=lambda c: c[2])
        self.assertAlmostEqual(best[2], 1.0, 2)
        # route runs along canonical y 1.5 -> red field frame y 1.5 (0.25 m cells), x between 8.5 and 13
        self.assertLess(abs((best[1] + 0.5) * 0.25 - 1.5), 0.6)
        self.assertTrue(8.0 < (best[0] + 0.5) * 0.25 < 13.5)
        ys = [(c[1] + 0.5) * 0.25 for c in cells if c[2] > 0.5]
        self.assertTrue(all(y < 3.0 for y in ys))
        self.assertEqual((r["path_heat"]["nx"], r["path_heat"]["ny"], r["path_heat"]["cell"]), (67, 33, 0.25))

    def test_cycles_per_shift_counts_bursts_and_skips_inactive_shifts(self):
        h = hist(4)
        h[0]["active"]["shift1"] = False
        h[0]["bursts"]["shift1"] = 9          # not counted: hub inactive there
        c = TL.cycles_per_shift(h)
        self.assertEqual(c["shift1"]["mean"], 2.0)
        self.assertEqual(c["shift2"]["mean"], 1.0)
        self.assertEqual(set(c), set(TL.CPS_KEYS))

    def test_bursts_from_shots(self):
        shots = [dict(match_t=t, target="red_hub", outcome="made", origin=[13, 2, 0.5]) for t in (31, 32, 33, 50, 51, 120)]
        per = {p: dict(name=p, t0=a, t1=b) for p, a, b in TL.PHASES}
        f = B.timeline_features([(float(t), 13.0, 2.0) for t in range(0, 160)], shots, [], {"analytics": [], "_defense_eps": []},
                                0, "red", "frc1", per)
        self.assertEqual(f["bursts"]["shift1"], 2)          # 31-33 and 50-51 are separate bursts (> 3 s apart)
        self.assertEqual(f["bursts"]["shift4"], 1)
        self.assertEqual(f["ev"]["first_score"], 31.0)

    def test_no_history_means_none_and_future_cannot_leak(self):
        self.assertIsNone(self.build([]))
        h = hist(4)
        a = self.build(h[:3])["timeline"]["xy"]
        h2 = h[:3] + [mk(9, side=6.5)]            # a later match exists but is not in this team's earlier history
        self.assertEqual(a, self.build(h2[:3])["timeline"]["xy"])

    def test_actual_timeline(self):
        r = mk(1)
        r["tl"][10:20] = np.nan
        a = TL.actual_timeline(r, "red")
        self.assertEqual(len(a["xy"]), TL.T)
        self.assertIsNone(a["xy"][12])
        self.assertIsNotNone(a["xy"][5])

    def test_score_and_calib(self):
        h = hist(4)
        tgt = mk(50)
        s = TL.score(self.build(h), tgt)
        self.assertLess(np.median(s["err"]), 3.0)
        c = TL.calib_summary([s])
        self.assertEqual(c["version"], TL.VERSION)
        self.assertLess(c["pos_err_m"]["median"], c["baseline_stay_pos_err_m"])


if __name__ == "__main__":
    unittest.main()
