"""TBA-only baselines, updated strictly from matches played before the predicted one.

* OPR  -- ridge least squares of alliance total score on team indicators, shrunk toward (mean score)/3 with
          alpha = 1 match of pseudo-data per team (needed: fewer matches than teams early on).
* EPA  -- Statbotics-style running rating: pred = sum(EPA); after each match every team moves by
          K * (actual - pred) / 3, K = 0.5 for a team's first matches decaying to 1/3 after 6.
Win probability for both: Phi(pred_margin / s), s = RMS of this method's own *out-of-sample* margin errors so far
(prequential), with a weak prior of 80 points worth 4 matches.  Nothing is tuned on the backtest matches.
"""
import math

import numpy as np
from scipy import stats

FALLBACK_SCORE = 100.0      # pre-event guess for an alliance score (same for all methods)
OPR_ALPHA = 1.0
S_PRIOR, S_PRIOR_N = 80.0, 4.0


def _mean_score(prior):
    s = [m[a]["score"] for m in prior for a in ("red", "blue")]
    return float(np.mean(s)) if s else FALLBACK_SCORE


def opr_predict(prior, teams_all, red, blue):
    mean = _mean_score(prior)
    if not prior:
        return mean, mean
    T = {t: i for i, t in enumerate(teams_all)}
    rows, y = [], []
    for m in prior:
        for a in ("red", "blue"):
            x = np.zeros(len(T))
            for t in m["teams"][a]:
                x[T[t]] = 1.0
            rows.append(x)
            y.append(m[a]["score"])
    A = np.array(rows)
    y = np.array(y, float)
    x0 = np.full(len(T), mean / 3.0)
    lhs = A.T @ A + OPR_ALPHA * np.eye(len(T))
    rhs = A.T @ y + OPR_ALPHA * x0
    opr = np.linalg.solve(lhs, rhs)
    f = lambda tl: float(sum(opr[T[t]] for t in tl))
    return f(red), f(blue)


class EPA:
    def __init__(self):
        self.r = {}
        self.n = {}

    def _get(self, t, prior_mean):
        if t not in self.r:
            self.r[t] = prior_mean / 3.0
            self.n[t] = 0
        return self.r[t]

    def predict(self, red, blue, prior_mean):
        return (sum(self._get(t, prior_mean) for t in red), sum(self._get(t, prior_mean) for t in blue))

    def update(self, m, prior_mean):
        pr, pb = self.predict(m["teams"]["red"], m["teams"]["blue"], prior_mean)
        for a, p in (("red", pr), ("blue", pb)):
            err = m[a]["score"] - p
            for t in m["teams"][a]:
                k = 1.0 / 3.0 + (0.5 - 1.0 / 3.0) * max(0.0, 1.0 - self.n[t] / 6.0)
                self.r[t] += k * err / 3.0
                self.n[t] += 1


class Prequential:
    """win prob = Phi(margin / s) with s from past out-of-sample margin errors."""

    def __init__(self):
        self.sq = []

    def s(self):
        n = len(self.sq)
        return math.sqrt((S_PRIOR_N * S_PRIOR ** 2 + sum(self.sq)) / (S_PRIOR_N + n))

    def p(self, margin):
        return float(stats.norm.cdf(margin / self.s()))

    def add(self, pred_margin, actual_margin):
        self.sq.append((actual_margin - pred_margin) ** 2)
