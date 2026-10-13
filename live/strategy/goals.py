"""Season-goal odds (DCMP / Worlds / district-rank target), rankings.json and schedule.json for the web UI.

    python -m live.strategy.goals DATA_DIR EVENT [--team frc4911] [--goal DCMP|Worlds|rank:N] [--asof TS] [--sims 800]
                                  [--out-dir DIR] [--offline] [--no-matches]
    python -m live.strategy.goals DATA_DIR EVENT --replay          # goal odds at several points of the season vs outcome
Worker entry point:  goals.site_build(DATA_DIR, EVENT, team=DASH_TEAM)  (writes api/<ev>/rankings.json + schedule.json)

Model (everything vectorised over n Monte-Carlo seasons; contract in README.md):
  * Team strength  theta_t (points an alliance gets per robot): the dashboard's blended rating (EPA prior + TBA + video)
    for the event's teams when `ratings` is given, else a running EPA over all district matches played before `asof`
    (Statbotics' district EPAs are used first when the API is up and asof is None).  One draw per team per season, so a
    strong draw helps in every event.  Match score = sum(theta) + N(0, sigma) (sigma from the district's own residuals).
  * Remaining events (each team's registered events, TBA): remaining quals from the posted schedule (random pairing if
    none), RP = 3 win + Bernoulli bonus RPs (logistic in score, fitted on the district's played matches), ranking by
    average RP, qual points by the manual formula, alliance selection (captain n picks best remaining rating; snake),
    8-alliance double elimination (win prob = Phi(dS / (sigma sqrt 2))), elim points 30/20/13/7, alliance points,
    awards (Impact 10, EI 8, RAS 8, and the event's usual number of 5-point judged awards at random teams).
  * Season total = first two district events + rookie bonus + adjustments (TBA).
  * DCMP: Impact / EI / RAS winners pre-qualify; the rest of the capacity goes to the highest-point teams.  Worlds:
    surrogate DCMP (performance = theta + noise) with x3 points, top `worlds_slots` of the final totals.
  * Value of a remaining match: the focal event is re-simulated with the result forced (common random numbers).
"""
import argparse
import json
import math
import os
import sys
import time

import numpy as np
from scipy import stats
from scipy.optimize import minimize

from . import district as DI
from . import epa as EPA

SCHEMA = "cyber-scout/goals@1"
DEFAULT_TEAM = os.environ.get("DASH_TEAM", "frc4911")
SIGMA_FALLBACK = 38.0
THETA_SD0 = 11.0
# 8-alliance double elimination (2023+): (set, a, b) with W<n>/L<n> = winner/loser of set n, A<k> = alliance k
BRACKET = [(1, "A1", "A8"), (2, "A4", "A5"), (3, "A2", "A7"), (4, "A3", "A6"), (5, "L1", "L2"), (6, "L3", "L4"),
           (7, "W1", "W2"), (8, "W3", "W4"), (9, "L7", "W6"), (10, "L8", "W5"), (11, "W7", "W8"), (12, "W10", "W9"),
           (13, "L11", "W12")]
ELIM_OUT = {12: 7, 13: 13}       # loser of set -> points (sets 5, 6, 9, 10 losers get 0)


def _num(t):
    return int(t[3:])


def _r(x, nd=1):
    return None if x is None else round(float(x), nd)


def _q(a, qs=(10, 50, 90)):
    return [float(x) for x in np.percentile(a, qs)]


# ------------------------------------------------------------------------------------------------- strengths
def _played(m):
    s = m["alliances"]["red"]["score"]
    return s is not None and s >= 0


def _mt(m):
    return m.get("actual_time") or m.get("time") or m.get("predicted_time") or 0


def district_strengths(D):
    """Running EPA (Statbotics update rule) on alliance scores over every visible district match, chronological.
    -> theta {team: (mean, sd)}, sigma (alliance score residual sd), mean_alliance, n_matches, foul_mean (per alliance)."""
    ms = [m for ev in D["events"] if not ev["dcmp"] or True for m in ev["matches"] if _played(m)]
    ms.sort(key=_mt)
    if not ms:
        return {}, SIGMA_FALLBACK, 100.0, 0, 7.0
    mean_all = float(np.mean([m["alliances"][c]["score"] for m in ms for c in ("red", "blue")]))
    r, n, res = {}, {}, []
    fouls = []
    for m in ms:
        for c in ("red", "blue"):
            tl = m["alliances"][c]["team_keys"]
            for t in tl:
                r.setdefault(t, mean_all / 3.0)
                n.setdefault(t, 0)
        for c in ("red", "blue"):
            tl = m["alliances"][c]["team_keys"]
            pred = sum(r[t] for t in tl)
            err = m["alliances"][c]["score"] - pred
            if len(res) or True:
                res.append(err)
            sb = (m.get("score_breakdown") or {}).get(c) or {}
            if sb.get("foulPoints") is not None:
                fouls.append(sb["foulPoints"])
            for t in tl:
                k = 1.0 / 3.0 + (0.5 - 1.0 / 3.0) * max(0.0, 1.0 - n[t] / 6.0)
                r[t] += k * err / 3.0
                n[t] += 1
    tail = res[len(res) // 3:] if len(res) > 90 else res
    sigma = float(np.sqrt(np.mean(np.square(tail)))) if tail else SIGMA_FALLBACK
    theta = {t: (r[t], THETA_SD0 / math.sqrt(1.0 + n[t] / 5.0)) for t in r}
    return theta, sigma, mean_all, len(ms), float(np.mean(fouls)) if fouls else 7.0


def statbotics_district(data_dir, D, offline=False, log=None):
    """{team: (mean, sd)} from Statbotics team_events of the district's events (current EPA); {} when the API is down."""
    out = {}
    cdir = os.path.join(data_dir, "cache", "statbotics")
    for ev in D["events"]:
        if ev["status"] == "done" and ev["dcmp"]:
            continue
        te, _ = EPA.cached_get(f"{EPA.SB_BASE}/team_events?event={ev['key']}&limit=1000", cdir, f"team_events_{ev['key']}",
                               max_age=3600, offline=offline, valid=EPA._nonempty, log=log)
        for r in te or []:
            b = EPA._sb_breakdown(r.get("epa"))
            if b and r.get("team") is not None:
                out[f"frc{r['team']}"] = (b["total"] / 3.0 if False else b["total"], b.get("sd") or THETA_SD0)
    return out


def fit_bonus(D):
    """P(bonus RP | alliance score) logistic fits for energized / supercharged / traversal on played district matches."""
    xs, ys = [], {"energizedAchieved": [], "superchargedAchieved": [], "traversalAchieved": []}
    for ev in D["events"]:
        for m in ev["matches"]:
            if m["comp_level"] != "qm" or not _played(m) or not m.get("score_breakdown"):
                continue
            for c in ("red", "blue"):
                sb = m["score_breakdown"].get(c) or {}
                if "energizedAchieved" not in sb:
                    continue
                xs.append(m["alliances"][c]["score"])
                for k in ys:
                    ys[k].append(1.0 if sb.get(k) else 0.0)
    out = {}
    x = np.array(xs, float)
    for k, y in ys.items():
        y = np.array(y, float)
        if len(x) < 40 or y.sum() < 3:
            out[k] = (-12.0, 0.0) if y.sum() < 3 or not len(x) else (0.0, 0.0)
            if len(x) and y.sum() < 3:
                out[k] = (-9.0, 0.0)
            continue
        mu, sd = x.mean(), x.std() + 1e-9
        z = (x - mu) / sd

        def nll(p):
            eta = p[0] + p[1] * z
            return float(np.sum(np.logaddexp(0, eta) - y * eta)) + 1e-3 * float(p @ p)
        p = minimize(nll, [0.0, 1.0], method="BFGS").x
        # store as logistic on raw score: eta = a + b * score
        out[k] = (float(p[0] - p[1] * mu / sd), float(p[1] / sd))
    return out


def _bonus_draw(bonus, score, rng):
    """Bernoulli bonus RPs for scores array -> int array of bonus RP count."""
    rp = np.zeros(score.shape)
    for a, b in bonus.values():
        p = 1.0 / (1.0 + np.exp(-(a + b * score)))
        rp += rng.random(score.shape) < p
    return rp


# ------------------------------------------------------------------------------------------------- one event
def _qual_rp(m, c):
    """(rp, counts) of alliance colour c of a played qual, for the team list."""
    al = m["alliances"][c]
    o = al["score"]
    other = m["alliances"]["blue" if c == "red" else "red"]["score"]
    sb = (m.get("score_breakdown") or {}).get(c) or {}
    rp = sb.get("rp")
    if rp is None:
        rp = 3 if o > other else 1 if o == other else 0
    return rp


def random_schedule(teams, per_team, rng):
    ts = list(teams)
    out = []
    for _ in range(per_team):
        p = list(rng.permutation(len(ts)))
        while len(p) % 6:
            p.append(int(rng.integers(len(ts))))
        for i in range(0, len(p), 6):
            g = p[i:i + 6]
            out.append((g[:3], g[3:]))
    return out


def award_model(D):
    """mean number of 5-point judged awards per event and probability of a Rookie All-Star, from finished district events."""
    k5, ras, n = [], 0, 0
    for ev in D["events"]:
        if ev["dcmp"] or ev["status"] != "done":
            continue
        n += 1
        c = 0
        for a in ev.get("awards") or []:
            ty = a.get("award_type")
            if ty in DI.NO_POINT_AWARDS or ty in DI.AWARD_PTS:
                ras += ty == 10
                continue
            c += sum(1 for r in a.get("recipient_list") or [] if r.get("team_key"))
        k5.append(c)
    return {"k5": int(round(np.mean(k5))) if k5 else 12, "p_ras": (ras / n) if n else 0.3}


def sim_event(ev, col, theta, sigma, bonus, awm, seed, n, forced=None, fixed_alliances=None, rookies=None, draft=None):
    """Simulate one district event for n seasons.  theta (n, T) global strength draws, col {team: column}.
    Returns {"teams": [cols], "qual","alliance","elim","award": (n, Ne) points (x3 at DCMP), "rank": (n, Ne), "prequal": (n, Ne) bool,
             "rp_avg": (n, Ne)} -- common random numbers: the same seed gives the same draws except forced matches.
    rookies: team keys eligible for Rookie All-Star (else any team).  draft: {captain_key: ("take"|"skip", team_key)} --
    whenever that team captains, its first pick is forced to (take) or forbidden from (skip) team_key."""
    rng = np.random.default_rng(seed)
    teams = sorted(set(ev["teams"]) & set(col), key=_num)
    Ne = len(teams)
    loc = {t: i for i, t in enumerate(teams)}
    cols = [col[t] for t in teams]
    th = theta[:, cols]
    mult = DI.MULT_DCMP if ev["dcmp"] else 1
    rp = np.zeros((n, Ne))
    nm = np.zeros(Ne)
    for m in ev["matches"]:
        if m["comp_level"] != "qm" or not _played(m):
            continue
        for c in ("red", "blue"):
            al = m["alliances"][c]
            for t in al["team_keys"]:
                if t not in loc or t in (al.get("surrogate_team_keys") or []):
                    continue
                nm[loc[t]] += 1
                if t not in (al.get("dq_team_keys") or []):
                    rp[:, loc[t]] += _qual_rp(m, c)
    left = [m for m in ev["matches"] if m["comp_level"] == "qm" and not _played(m)]
    sched = []
    for m in sorted(left, key=lambda m: (m.get("match_number") or 0)):
        r = [loc[t] for t in m["alliances"]["red"]["team_keys"] if t in loc]
        b = [loc[t] for t in m["alliances"]["blue"]["team_keys"] if t in loc]
        sched.append((r, b, m["key"]))
    if not left and ev["quals_played"] == 0:
        per = 10
        sched = [(r, b, f"rand{i}") for i, (r, b) in enumerate(random_schedule(range(Ne), per, rng))]
    for r, b, key in sched:
        sa = th[:, r].sum(1) + sigma * rng.standard_normal(n)
        sb = th[:, b].sum(1) + sigma * rng.standard_normal(n)
        ba, bb = _bonus_draw(bonus, sa, rng), _bonus_draw(bonus, sb, rng)
        f = (forced or {}).get(key)
        if f:
            lo, hi = np.minimum(sa, sb), np.maximum(sa, sb)
            sa, sb = (hi, lo) if f == "red" else (lo, hi)
        wa = (sa > sb) * 3.0 + (sa == sb)
        wb = (sb > sa) * 3.0 + (sa == sb)
        for i in r:
            rp[:, i] += wa + ba
            nm[i] += 1
        for i in b:
            rp[:, i] += wb + bb
            nm[i] += 1
    avg = rp / np.maximum(nm, 1)
    key = -avg - 1e-7 * rng.random((n, Ne))
    order = np.argsort(key, axis=1)
    rank = np.empty((n, Ne), int)
    np.put_along_axis(rank, order, np.arange(1, Ne + 1)[None, :].repeat(n, 0), axis=1)
    qtab = np.array([DI.qual_points(r, Ne) for r in range(1, Ne + 1)]) * mult
    qual = qtab[rank - 1]
    # ---- alliance selection (captains = best remaining by rank; picks = best remaining rating, snake)
    pairs = np.zeros((n, 8, 3), int)
    if fixed_alliances:
        for k, a in enumerate(fixed_alliances[:8]):
            pairs[:, k, :] = [loc[t] for t in a[:3]]
    else:
        dr = {loc[c]: (how, loc[t]) for c, (how, t) in (draft or {}).items() if c in loc and t in loc}
        for s in range(n):
            avail = list(order[s])
            ths = th[s]
            caps, p1 = [], []
            for k in range(8):
                cap = avail.pop(0)
                how, tgt = dr.get(cap, (None, None))
                if how == "take" and tgt in avail:
                    j = avail.index(tgt)
                else:
                    cand = [i for i in range(len(avail)) if not (how == "skip" and avail[i] == tgt)]
                    j = max(cand, key=lambda i: ths[avail[i]])
                p1.append(avail.pop(j))
                caps.append(cap)
            p2 = [0] * 8
            for k in range(7, -1, -1):
                j = max(range(len(avail)), key=lambda i: ths[avail[i]])
                p2[k] = avail.pop(j)
            pairs[s, :, 0], pairs[s, :, 1], pairs[s, :, 2] = caps, p1, p2
    alliance = np.zeros((n, Ne))
    for k in range(8):
        pts = (17 - (k + 1), 17 - (k + 1), k + 1)
        for j in range(3):
            np.put_along_axis(alliance, pairs[:, k, j:j + 1], pts[j] * mult * np.ones((n, 1)), axis=1)
    # ---- playoffs
    S = np.stack([np.take_along_axis(th, pairs[:, k, :], axis=1).sum(1) for k in range(8)], axis=1)
    known = _known_results(ev, fixed_alliances, loc) if fixed_alliances else {}
    W, L, E = {}, {}, np.zeros((n, 8))
    ar = np.arange(n)

    def res(x):
        return np.full(n, int(x[1:]) - 1) if x[0] == "A" else (W if x[0] == "W" else L)[int(x[1:])]
    for sn, a, b in BRACKET:
        ia, ib = res(a), res(b)
        p = stats.norm.cdf((S[ar, ia] - S[ar, ib]) / (sigma * math.sqrt(2)))
        aw = rng.random(n) < p
        if sn in known:
            aw = np.where((ia == known[sn]) | (ib == known[sn]), ia == known[sn], aw)
        W[sn], L[sn] = np.where(aw, ia, ib), np.where(aw, ib, ia)
    for sn, pts in ELIM_OUT.items():
        E[ar, L[sn]] = pts
    ia, ib = W[11], W[13]
    p = stats.norm.cdf((S[ar, ia] - S[ar, ib]) / (sigma * math.sqrt(2)))
    wins_a = (rng.random((n, 3)) < p[:, None]).sum(1) >= 2
    if "f" in known:
        wins_a = np.where((ia == known["f"]) | (ib == known["f"]), ia == known["f"], wins_a)
    fw, fl = np.where(wins_a, ia, ib), np.where(wins_a, ib, ia)
    E[ar, fw], E[ar, fl] = 30, 20
    elim = np.zeros((n, Ne))
    for k in range(8):
        for j in range(3):
            np.put_along_axis(elim, pairs[:, k, j:j + 1], E[:, k:k + 1] * mult, axis=1)
    # ---- awards (random teams) + DCMP bids
    perm = np.argsort(rng.random((n, Ne)), axis=1)
    award = np.zeros((n, Ne))
    prequal = np.zeros((n, Ne), bool)
    if not ev["dcmp"]:
        for slot, pts in ((0, 10), (1, 8)):
            np.put_along_axis(award, perm[:, slot:slot + 1], float(pts), axis=1)
            np.put_along_axis(prequal, perm[:, slot:slot + 1], True, axis=1)
        has_ras = rng.random(n) < awm["p_ras"]
        rk_loc = [loc[t] for t in teams if t in (rookies or ())] if rookies is not None else None
        if rk_loc is None:
            np.put_along_axis(award, perm[:, 2:3], np.where(has_ras, 8.0, 0.0)[:, None], axis=1)
            np.put_along_axis(prequal, perm[:, 2:3], has_ras[:, None], axis=1)
        elif rk_loc:                                   # Rookie All-Star goes to a rookie team only
            who = np.array(rk_loc)[rng.integers(0, len(rk_loc), n)]
            ar_ = np.arange(n)[has_ras]
            award[ar_, who[has_ras]] += 8.0
            prequal[ar_, who[has_ras]] = True
        for j in range(3, min(3 + awm["k5"], Ne)):
            cur = np.take_along_axis(award, perm[:, j:j + 1], axis=1)
            np.put_along_axis(award, perm[:, j:j + 1], cur + 5.0, axis=1)
    else:
        for j in range(awm["k5"]):
            np.put_along_axis(award, perm[:, j:j + 1], 5.0 * mult, axis=1)
    return {"teams": teams, "cols": cols, "qual": qual, "alliance": alliance, "elim": elim, "award": award,
            "total": qual + alliance + elim + award, "rank": rank, "prequal": prequal, "rp_avg": avg,
            "pairs": pairs, "alliance_elim": E, "fixed": bool(fixed_alliances), "mult": mult}


def _known_results(ev, fixed_alliances, loc):
    """{set number | 'f': winning alliance index} from played playoff matches (alliances = lists of loc indices)."""
    idx = {}
    for k, a in enumerate(fixed_alliances):
        for i in a:
            idx[i] = k
    out = {}
    wins = {}
    for m in ev["matches"]:
        if m["comp_level"] == "qm" or not _played(m) or m.get("winning_alliance") not in ("red", "blue"):
            continue
        t = [x for x in m["alliances"][m["winning_alliance"]]["team_keys"] if x in loc]
        k = next((idx[loc[x]] for x in t if loc[x] in idx), None)
        if k is None:
            continue
        if m["comp_level"] == "f":
            wins[k] = wins.get(k, 0) + 1
            if wins[k] >= 2:
                out["f"] = k
        else:
            out[m["set_number"]] = k
    return out


# ------------------------------------------------------------------------------------------------- the district season
class Season:
    """Monte-Carlo of the rest of a district season.  Build once, then query odds for any team / goal."""

    def __init__(self, D, n=800, seed=0, ratings=None, sigma=None, capacity=None, worlds_slots=None, data_dir=None,
                 offline=False, use_statbotics=True, log=None):
        self.D, self.n, self.seed = D, n, seed
        theta, sig, mean_all, nm, foul = district_strengths(D)
        self.sigma = sigma or sig
        self.theta_src = "local EPA (district matches)"
        if use_statbotics and D["asof"] is None and data_dir:
            sb = statbotics_district(data_dir, D, offline=offline, log=log)
            if len(sb) > 20:
                theta = {**theta, **{t: (v[0], v[1]) for t, v in sb.items()}}
                self.theta_src = "statbotics EPA"
        for t, (mu, sd) in (ratings or {}).items():        # dashboard blend (per-robot points incl. HP share, no fouls)
            theta[t] = (mu + foul / 3.0, sd)
            self.theta_src += " + dashboard blend" if self.theta_src.count("dashboard") == 0 else ""
        self.mean_theta = mean_all / 3.0
        self.teams = sorted({t for ev in D["events"] for t in ev["teams"]} | set(D["district_teams"]), key=_num)
        self.col = {t: i for i, t in enumerate(self.teams)}
        mu = np.array([theta.get(t, (self.mean_theta, THETA_SD0 * 1.3))[0] for t in self.teams])
        sd = np.array([theta.get(t, (self.mean_theta, THETA_SD0 * 1.3))[1] for t in self.teams])
        rng = np.random.default_rng(seed)
        self.theta = mu[None, :] + sd[None, :] * rng.standard_normal((n, len(self.teams)))
        self.mu, self.sd = mu, sd
        self.bonus = fit_bonus(D)
        self.awm = award_model(D)
        self.capacity = capacity or int(os.environ.get("DCMP_CAPACITY", 0)) or DI.CAPACITY.get(D["key"], DI.DEFAULT_CAPACITY)
        self.worlds_slots = worlds_slots or DI.WORLDS_SLOTS.get(D["key"], 15)
        self.rb = {r["team_key"]: r.get("rookie_bonus", 0) + r.get("adjustments", 0) for r in D["tba_rankings"]}
        rbon = {r["team_key"]: r.get("rookie_bonus", 0) or 0 for r in D["tba_rankings"]}
        mx = max(rbon.values() or [0])
        self.rookies = {t for t, v in rbon.items() if v > 0 and v >= mx}     # first-year rookies (largest bonus)
        self.draft = {}                                                    # {event: {captain: (take|skip, team)}}
        self.events = [e for e in D["events"] if not e["dcmp"]]
        self.dcmp = next((e for e in D["events"] if e["dcmp"]), None)
        self._plan_events()
        self.base_run = self._run({})

    # which events each team plays and which count (first two by date, among non-DCMP events)
    def _plan_events(self):
        self.attend = {t: [] for t in self.teams}
        for ev in self.events:
            for t in ev["teams"]:
                if t in self.attend:
                    self.attend[t].append(ev["key"])
        self.counted = {t: set(v[:2]) for t, v in self.attend.items()}
        self.done_pts = {}
        self.prequal_done = set()
        for ev in self.events:
            if ev["status"] == "done" and ev.get("rankings"):
                self.done_pts[ev["key"]] = DI.event_points(ev)
                for a in ev.get("awards") or []:
                    if a.get("award_type") in DI.PREQUAL_AWARDS:
                        self.prequal_done |= {r["team_key"] for r in a.get("recipient_list") or [] if r.get("team_key")}

    def _fixed_alliances(self, ev):
        al = ev.get("alliances") or []
        if len(al) >= 8 and all(len(a.get("picks") or []) >= 3 for a in al):
            loc = {t: i for i, t in enumerate(sorted(set(ev["teams"]) & set(self.col), key=_num))}
            try:
                return [[loc[t] for t in a["picks"][:3]] for a in al[:8]]
            except KeyError:
                return None
        return None

    def _run(self, forced, only=None):
        """-> dict(pre (n,T) pre-DCMP points, prequal (n,T) bool, ev {key: sim result}, played_events)"""
        n, T = self.n, len(self.teams)
        pre = np.zeros((n, T))
        for t, c in self.col.items():
            pre[:, c] = self.rb.get(t, 0)
        prq = np.zeros((n, T), bool)
        for t in self.prequal_done:
            if t in self.col:
                prq[:, self.col[t]] = True
        sims = {}
        cache = getattr(self, "_ev_cache", {})
        self._ev_cache = cache
        for i, ev in enumerate(self.events):
            k = ev["key"]
            if k in self.done_pts:
                for t, v in self.done_pts[k].items():
                    if t in self.col and k in self.counted.get(t, ()):
                        pre[:, self.col[t]] += v["total"]
                continue
            if not ev["teams"]:
                continue
            f = forced if (only is None or only == k) else {}
            dr = self.draft.get(k) or {}
            ck = (k, tuple(sorted(f.items())), tuple(sorted(dr.items())))
            if ck in cache:
                R = cache[ck]
            else:
                R = sim_event(ev, self.col, self.theta, self.sigma, self.bonus, self.awm, seed=self.seed * 1000 + i + 1,
                              n=self.n, forced=f, fixed_alliances=self._fixed_alliances(ev), rookies=self.rookies,
                              draft=dr)
                cache[ck] = R
            sims[k] = R
            for j, t in enumerate(R["teams"]):
                if k in self.counted.get(t, ()):
                    pre[:, R["cols"][j]] += R["total"][:, j]
            for j, c in enumerate(R["cols"]):
                prq[:, c] |= R["prequal"][:, j]
        return {"pre": pre, "prq": prq, "ev": sims}

    # ---- qualification
    def dcmp_outcome(self, run):
        """-> qualified (n,T) bool, cutoff (n,) points of the last points-qualifier."""
        pre, prq = run["pre"], run["prq"]
        n, T = pre.shape
        attended = np.array([bool(self.attend[t]) or self.rb.get(t, 0) for t in self.teams])
        slots = np.maximum(self.capacity - prq.sum(1), 0)
        jit = pre + 1e-6 * np.random.default_rng(self.seed + 99).random((n, T))
        jit = np.where(prq | ~attended[None, :], -1e9, jit)
        srt = -np.sort(-jit, axis=1)
        idx = np.clip(slots - 1, 0, T - 1)
        cutoff = srt[np.arange(n), idx]
        qual = prq | (jit >= cutoff[:, None])
        qual &= attended[None, :]
        cut_pts = np.floor(cutoff + 1e-3)
        return qual, cut_pts

    def worlds(self, run, qual):
        """Surrogate DCMP: performance = theta + noise; points x3 by finish; -> final totals (n,T)."""
        n, T = self.n, len(self.teams)
        rng = np.random.default_rng(self.seed + 7)
        perf = self.theta + 14.0 * rng.standard_normal((n, T))
        perf = np.where(qual, perf, -1e9)
        total = run["pre"].copy()
        order = np.argsort(-perf, axis=1)
        nq = qual.sum(1)
        dc = np.zeros((n, T))
        for s in range(n):
            N = int(nq[s])
            o = order[s, :N]
            qp = np.array([DI.qual_points(r, N) for r in range(1, N + 1)]) * 3.0
            dc[s, o] = qp
            if N >= 24:
                for k in range(8):                       # top 16 by performance: captains/firsts; next 8: seconds
                    dc[s, o[k]] += (16 - k) * 3.0
                    dc[s, o[8 + k]] += (16 - k) * 3.0
                    dc[s, o[16 + (7 - k)]] += (k + 1) * 3.0
                al = o[:24].reshape(3, 8) if False else None
                S = np.array([perf[s, o[k]] + perf[s, o[8 + k]] + perf[s, o[16 + (7 - k)]] for k in range(8)])
                lvl = np.argsort(-(S + 20.0 * rng.standard_normal(8)))
                for rk, pts in zip(lvl, (30, 20, 13, 7, 0, 0, 0, 0)):
                    for mem in (o[rk], o[8 + rk], o[16 + (7 - rk)]):
                        dc[s, mem] += pts * 3.0
            dc[s, o[:self.awm["k5"]]] += 0.0
            dc[s, o] += 0.0
        total = total + dc
        return total

    # ---- team queries
    def goal(self, team, goal="DCMP", run=None):
        run = run or self.base_run
        c = self.col[team]
        qual, cutoff = self.dcmp_outcome(run)
        g = goal.lower()
        if g.startswith("worlds"):
            tot = self.worlds(run, qual)
            tj = tot + 1e-6 * np.random.default_rng(1).random(tot.shape)
            tj = np.where(qual, tj, -1e9)
            rank = (tj > tj[:, [c]]).sum(1) + 1
            hit = qual[:, c] & (rank <= self.worlds_slots)
            return hit, qual, cutoff, tot
        if g.startswith("rank"):
            N = int(g.split(":")[1])
            pj = run["pre"] + 1e-6 * np.random.default_rng(2).random(run["pre"].shape)
            rank = (pj > pj[:, [c]]).sum(1) + 1
            cutoff = np.floor(-np.sort(-run["pre"], axis=1)[:, min(N, run["pre"].shape[1]) - 1] + 1e-3)
            return rank <= N, qual, cutoff, run["pre"]
        return qual[:, c], qual, cutoff, run["pre"]

    def team_events(self, team):
        return [ev for ev in self.events if team in ev["teams"]]


# ------------------------------------------------------------------------------------------------- goal block
def goal_block(S, team, goal="DCMP", focal=None, value_matches=True, now=None):
    """The `goal` block of rankings.json for one team (see README contract)."""
    D = S.D
    c = S.col.get(team)
    if c is None:
        return None
    hit, qual, cutoff, tot = S.goal(team, goal)
    run = S.base_run
    pre = run["pre"][:, c]
    cur = 0
    for ev in S.events:
        if ev["key"] in S.done_pts and ev["key"] in S.counted[team]:
            cur += S.done_pts[ev["key"]].get(team, {}).get("total", 0)
    cur += S.rb.get(team, 0)
    cut_now = None if goal.lower().startswith("rank") else _cutoff_now(S)
    p_aw = float(hit.mean())
    noaw = no_award_mask(S, team, run)
    p_no = float(hit[noaw].mean()) if noaw.sum() >= 20 else p_aw
    pq = award_prequal_mask(S, team, run)
    contender = _contender()
    p = p_aw if contender else p_no             # headline: no awards for us unless we are an Impact/EI contender
    q50 = _q(cutoff)
    need = {}
    for lab, pr in (("p50", 50), ("p80", 80), ("p95", 95)):
        x = float(np.percentile(cutoff, pr)) + 1
        need[lab] = {"final_points": _r(x, 0), "still_needed": _r(max(0.0, x - cur), 0)}
    evs = []
    for ev in S.team_events(team):
        row = {"event": ev["key"], "name": ev.get("short_name") or ev.get("name"), "start_date": ev["start_date"],
               "status": ev["status"], "counts": ev["key"] in S.counted[team]}
        if ev["key"] in S.done_pts:
            row["points"] = S.done_pts[ev["key"]].get(team, {}).get("total", 0)
        elif ev["key"] in run["ev"]:
            R = run["ev"][ev["key"]]
            j = R["teams"].index(team)
            row["points"] = {"mean": _r(R["total"][:, j].mean()), "p10": _r(_q(R["total"][:, j])[0]),
                             "p90": _r(_q(R["total"][:, j])[2]), "rank_mean": _r(R["rank"][:, j].mean(), 1)}
        evs.append(row)
    block = {"goal": goal, "team": team, "district": D["key"], "p_goal": _r(p, 3), "p_goal_ci": _ci(p, int(noaw.sum()) if not contender else S.n),
             "awards": {"headline": "with random awards" if contender else "without awards for us",
                        "p_no_awards": _r(p_no, 3), "p_with_awards": _r(p_aw, 3),
                        "p_if_impact_or_ei": _r(float(hit[pq].mean()), 3) if pq.sum() >= 5 else (1.0 if goal.upper() == "DCMP" else None),
                        "p_we_win_award": _r(1 - float(noaw.mean()), 3), "contender": contender,
                        "note": "awards at unplayed events are random (Impact, EI, Rookie All-Star to first-year rookies only, "
                                "~12 judged 5-pt awards); set DASH_AWARD_CONTENDER=1 to headline the with-awards number"},
             "current_points": cur, "projected_points": {"mean": _r(pre.mean()), "p10": _r(_q(pre)[0]), "p90": _r(_q(pre)[2])},
             "cutoff": {"now": cut_now, "projected": {"mean": _r(cutoff.mean()), "p10": _r(q50[0]), "p50": _r(q50[1]), "p90": _r(q50[2])}},
             "points_needed": need,
             "points_still_needed": _r(max(0.0, q50[1] + 1 - cur), 0),
             "dcmp": {"capacity": S.capacity, "points_slots_mean": _r((S.capacity - run["prq"].sum(1)).mean(), 1),
                      "award_prequalified_now": len(S.prequal_done), "is_prequalified": bool(team in S.prequal_done)},
             "events": evs, "model": {"sims": S.n, "theta": S.theta_src, "sigma_alliance": _r(S.sigma),
                                      "worlds_slots": S.worlds_slots if goal.lower().startswith("worlds") else None,
                                      "theta_team": {"mean": _r(S.mu[c]), "sd": _r(S.sd[c])}}}
    if goal.lower().startswith("worlds"):
        block["p_dcmp"] = _r(float(qual[:, c].mean()), 3)
    if value_matches:
        block["value"] = value_of_matches(S, team, goal, focal, p)
    return block


def _contender():
    return os.environ.get("DASH_AWARD_CONTENDER", "0") == "1"


def _p_headline(S, team, goal, run=None):
    """P(goal) as headlined: conditional on no award for `team` unless it is an award contender."""
    hit = S.goal(team, goal, run)[0]
    if _contender():
        return float(hit.mean())
    m = no_award_mask(S, team, run)
    return float(hit[m].mean()) if m.sum() >= 20 else float(hit.mean())


def no_award_mask(S, team, run=None):
    """(n,) bool: simulated seasons in which `team` wins no award (no points, no pre-qualification) at its remaining
    events -- the headline odds condition on this, so they do not lean on a random award."""
    run = run or S.base_run
    m = np.ones(S.n, bool)
    for k, R in run["ev"].items():
        if team in R["teams"]:
            j = R["teams"].index(team)
            m &= (R["award"][:, j] == 0) & ~R["prequal"][:, j]
    return m


def award_prequal_mask(S, team, run=None):
    run = run or S.base_run
    m = np.zeros(S.n, bool)
    for k, R in run["ev"].items():
        if team in R["teams"]:
            m |= R["prequal"][:, R["teams"].index(team)]
    return m


def _ci(p, n):
    se = math.sqrt(max(p * (1 - p), 1e-6) / n)
    return [_r(max(0, p - 1.64 * se), 3), _r(min(1, p + 1.64 * se), 3)]


def _cutoff_now(S):
    """points of the last points-qualifier if the season ended now."""
    pre = {}
    for t in S.teams:
        v = S.rb.get(t, 0)
        for k, d in S.done_pts.items():
            if k in S.counted[t]:
                v += d.get(t, {}).get("total", 0)
        pre[t] = v
    nonq = sorted((v for t, v in pre.items() if t not in S.prequal_done and S.attend[t]), reverse=True)
    slots = max(S.capacity - len(S.prequal_done), 0)
    return nonq[slots - 1] if 0 < slots <= len(nonq) else None


def value_of_matches(S, team, goal, focal, p0):
    """Delta P(goal) per remaining qual of `team` at the focal (live) event: result forced to win / loss, common random
    numbers; plus conditional-on-event-outcome odds for remaining events."""
    out = {"matches": [], "events": [], "baseline": _r(p0, 3)}
    c = S.col[team]
    fev = next((e for e in S.events if e["key"] == focal and e["key"] in S.base_run["ev"]), None)
    if fev:
        mine = sorted((m for m in fev["matches"] if m["comp_level"] == "qm" and not _played(m) and
                       team in m["alliances"]["red"]["team_keys"] + m["alliances"]["blue"]["team_keys"]),
                      key=lambda m: m.get("match_number") or 0)
        for m in mine:
            side = "red" if team in m["alliances"]["red"]["team_keys"] else "blue"
            opp = "blue" if side == "red" else "red"
            pr = {}
            for lab, who in (("win", side), ("loss", opp)):
                r = S._run({m["key"]: who}, only=fev["key"])
                pr[lab] = _p_headline(S, team, goal, r)
            out["matches"].append({"key": m["key"], "label": f"Qual {m['match_number']}", "time": _mt(m) or None,
                                   "p_if_win": _r(pr["win"], 3), "p_if_loss": _r(pr["loss"], 3),
                                   "delta": _r(pr["win"] - pr["loss"], 3)})
    hit = S.goal(team, goal)[0]
    keep = np.ones(S.n, bool) if _contender() else no_award_mask(S, team)
    for ev in S.team_events(team):
        R = S.base_run["ev"].get(ev["key"])
        if R is None or team not in R["teams"]:
            continue
        j = R["teams"].index(team)
        pts = R["total"][:, j]
        rk = R["rank"][:, j]
        e = {"event": ev["key"], "by_points": [], "by_rank": []}
        qs = np.percentile(pts, [25, 75])
        for lab, mask in (("bottom quartile", (pts <= qs[0]) & keep), ("middle half", (pts > qs[0]) & (pts < qs[1]) & keep),
                          ("top quartile", (pts >= qs[1]) & keep)):
            if mask.sum() >= 5:
                e["by_points"].append({"outcome": lab, "points": _r(pts[mask].mean(), 0), "p_goal": _r(hit[mask].mean(), 3)})
        Ne = len(R["teams"])
        for lab, mask in (("rank 1-4", (rk <= 4) & keep), ("rank 5-8", (rk > 4) & (rk <= 8) & keep),
                          ("rank 9-16", (rk > 8) & (rk <= 16) & keep), (f"rank 17+", (rk > 16) & keep)):
            if mask.sum() >= 5:
                e["by_rank"].append({"outcome": lab, "p_goal": _r(hit[mask].mean(), 3), "share": _r(mask.sum() / max(1, keep.sum()), 3)})
        out["events"].append(e)
    return out


# ------------------------------------------------------------------------------------------------- replay validation
def replay(data_dir, district, team_set, times, goal="DCMP", n=600, log=print, offline=True):
    """Goal odds at replay times vs the teams that actually reached DCMP (event 'pncmp' team list)."""
    rows = []
    D0 = DI.load_district(data_dir, district, asof=None, offline=offline)
    actual = set(next((e["teams"] for e in D0["events"] if e["dcmp"]), []))
    for ts in times:
        D = DI.load_district(data_dir, district, asof=ts, offline=offline)
        S = Season(D, n=n, seed=1, data_dir=data_dir, offline=offline, use_statbotics=False)
        qual, cut = S.dcmp_outcome(S.base_run)
        p = qual.mean(0)
        eligible = [t for t in S.teams if S.attend[t]]
        y = np.array([t in actual for t in eligible], float)
        pp = np.array([p[S.col[t]] for t in eligible])
        brier = float(np.mean((pp - y) ** 2))
        base = float(np.mean((y.mean() - y) ** 2))
        ll = -float(np.mean(y * np.log(np.clip(pp, 1e-3, 1)) + (1 - y) * np.log(np.clip(1 - pp, 1e-3, 1))))
        # sharpness-weighted: accuracy of p>0.5, AUC
        order = np.argsort(pp)
        from scipy.stats import rankdata
        auc = float((rankdata(pp)[y == 1].sum() - y.sum() * (y.sum() + 1) / 2) / (y.sum() * (len(y) - y.sum()))) if 0 < y.sum() < len(y) else None
        fav = pp > 0.5
        rows.append({"asof": ts, "when": time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)),
                     "events_done": [e["key"][4:] for e in D["events"] if e["status"] == "done" and not e["dcmp"]],
                     "events_live": [e["key"][4:] for e in D["events"] if e["status"] == "live"],
                     "teams": len(eligible), "actual_dcmp": int(y.sum()), "expected_dcmp": _r(pp.sum(), 1),
                     "brier": _r(brier, 4), "brier_base_rate": _r(base, 4), "logloss": _r(ll, 3), "auc": _r(auc, 3),
                     "acc_at_50": _r(float(np.mean(fav == (y == 1))), 3),
                     "cutoff_mean": _r(cut.mean()), "cutoff_actual": None})
        if log:
            log(rows[-1])
    return rows


# ------------------------------------------------------------------------------------------------- per-match where-you'll-be-beaten
PERIODS = ["auto", "transition", "shift1", "shift2", "shift3", "shift4", "endgame"]
PLABEL = {"auto": "Auto", "transition": "Transition", "shift1": "Shift 1", "shift2": "Shift 2", "shift3": "Shift 3",
          "shift4": "Shift 4", "endgame": "Endgame"}      # endgame row = endgame FUEL + climb; split in row["tower"]


def period_breakdown(M, red, blue, us, sims=3000, seed=0):
    """Expected points per period for `us` ('red'|'blue') and the other alliance, from the simulator's per-sim arrays.
    -> dict with win_prob, p_win_auto, periods[] (each: ours, theirs, delta, our_active_p), summary text."""
    from .sim import simulate
    S = simulate(M, red, blue, n=sims, seed=seed, raw=True)
    R = S["raw"]
    them = "blue" if us == "red" else "red"
    A, B = R["alliances"][us], R["alliances"][them]
    rw_auto = R["red_wins_auto"]
    we_won_auto = rw_auto if us == "red" else ~rw_auto
    p_auto = float(we_won_auto.mean())
    pw = S["p_red_win"] if us == "red" else 1 - S["p_red_win"]
    rows = []
    for p in PERIODS:
        a, b = A["fuel_by_p"][p].astype(float), B["fuel_by_p"][p].astype(float)
        extra_a = extra_b = 0.0
        if p == "auto":
            extra_a, extra_b = A["ta"], B["ta"]
        if p == "endgame":
            extra_a, extra_b = A["te"], B["te"]
        a, b = a + extra_a, b + extra_b
        # hub active (own hub scoring counts): auto/transition/endgame both; shifts 1,3 active for the auto LOSER
        if p in ("shift1", "shift3"):
            act = float((~we_won_auto).mean())
            act_them = float(we_won_auto.mean())
        elif p in ("shift2", "shift4"):
            act, act_them = float(we_won_auto.mean()), float((~we_won_auto).mean())
        else:
            act = act_them = 1.0
        if p in ("auto", "endgame"):     # tower (climb) points inside this row, so labels can say FUEL vs climb
            tw = {"ours": _r(float(np.mean(extra_a))), "theirs": _r(float(np.mean(extra_b)))}
        else:
            tw = None
        rows.append({"period": p, "label": PLABEL[p], "ours": _r(a.mean()), "theirs": _r(b.mean()), "tower": tw,
                     "delta": _r((a - b).mean()), "p_ours_ahead": _r(float((a > b).mean()), 2),
                     "our_active_p": _r(act, 2), "their_active_p": _r(act_them, 2),
                     "ours_if_won_auto": _r(a[we_won_auto].mean()) if we_won_auto.any() else None,
                     "ours_if_lost_auto": _r(a[~we_won_auto].mean()) if (~we_won_auto).any() else None,
                     "theirs_if_won_auto": _r(b[we_won_auto].mean()) if we_won_auto.any() else None,
                     "theirs_if_lost_auto": _r(b[~we_won_auto].mean()) if (~we_won_auto).any() else None})
    fa, fb = A["fl"].astype(float), B["fl"].astype(float)
    tot_a, tot_b = A["score"].astype(float), B["score"].astype(float)
    out = {"win_prob": _r(pw, 3), "p_win_auto": _r(p_auto, 3),
           "score": {"ours": _r(tot_a.mean()), "theirs": _r(tot_b.mean()), "margin": _r((tot_a - tot_b).mean()),
                     "margin_sd": _r((tot_a - tot_b).std())},
           "fouls": {"ours": _r(fa.mean()), "theirs": _r(fb.mean())}, "periods": rows}
    out["summary"] = narrative(out)
    return out


def narrative(b):
    """'You'll be beaten in auto by ~X; make it up in shift 2-3 ...' from a period_breakdown dict."""
    rows = {r["period"]: r for r in b["periods"]}
    p_auto = b["p_win_auto"]
    loses = [r for r in b["periods"] if r["delta"] is not None and r["delta"] < -1.5]
    wins = [r for r in b["periods"] if r["delta"] is not None and r["delta"] > 1.5]
    m = b["score"]["margin"]
    if m is None:
        return ""
    parts = []
    if b["win_prob"] >= 0.5:
        parts.append(f"Favoured ({b['win_prob'] * 100:.0f}%), expected margin {m:+.0f}.")
    else:
        parts.append(f"Underdog ({b['win_prob'] * 100:.0f}%), expected margin {m:+.0f}.")
    a = rows["auto"]["delta"]
    if a is not None and abs(a) >= 1.5:
        parts.append(("You'll be beaten in auto by ~%.0f" % -a) if a < 0 else ("You lead auto by ~%.0f" % a))
        parts[-1] += f" (win auto {p_auto * 100:.0f}%)."
    likely_active = ("shifts 2+4" if p_auto >= 0.5 else "shifts 1+3")
    other = ("shifts 1+3" if p_auto >= 0.5 else "shifts 2+4")
    sh = {s: rows[s] for s in ("shift1", "shift2", "shift3", "shift4")}
    act = [s for s in sh if (sh[s]["our_active_p"] or 0) >= 0.5]
    inact = [s for s in sh if s not in act]
    d_act = sum(sh[s]["delta"] for s in act)
    d_in = sum(sh[s]["delta"] for s in inact)
    parts.append(f"Your hub is likely active in {likely_active}: {d_act:+.0f} there, {d_in:+.0f} in {other} (opponent's active shifts).")
    e = rows["endgame"]["delta"]
    if e is not None and abs(e) >= 2:
        tw = rows["endgame"].get("tower") or {}
        td = (tw.get("ours") or 0) - (tw.get("theirs") or 0)
        if abs(td) >= 2:
            parts.append(f"Endgame {e:+.0f} (FUEL {e - td:+.0f}, climb {td:+.0f}).")
        else:
            parts.append(f"Endgame FUEL {e - td:+.0f} (climbs even).")
    worst = min(loses, key=lambda r: r["delta"]) if loses else None
    if worst and worst["delta"] <= -3 and worst["period"] != "auto":
        parts.append(f"Biggest deficit: {worst['label']} ({worst['delta']:+.0f}).")
    return " ".join(parts)


# ------------------------------------------------------------------------------------------------- site JSON
def _label(m):
    lvl = m["comp_level"]
    if lvl == "qm":
        return f"Qual {m['match_number']}", f"Q{m['match_number']}"
    if lvl == "f":
        return f"Final {m['match_number']}", f"F{m['match_number']}"
    return f"Playoff {m['set_number']}" + (f"-{m['match_number']}" if m["match_number"] > 1 else ""), f"M{m['set_number']}"


def _dump(obj, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, separators=(",", ":"), default=float)
    os.replace(tmp, path)
    return os.path.getsize(path)


def _read(path):
    try:
        return json.load(open(path))
    except Exception:
        return None


def district_key_of(data_dir, ev):
    """TBA district key of an event (from the cached event info); None for non-district events."""
    ip = os.path.join(data_dir, "tba", f"event__{ev}.json")
    raw = _read(ip)
    info = (raw.get("data", raw) if isinstance(raw, dict) else None) or {}
    d = (info.get("district") or {}).get("key")
    if d:
        return d
    info = DI.tba_get(data_dir, f"event/{ev}/simple", max_age=None) or {}
    return (info.get("district") or {}).get("key")


def build_event_json(data_dir, ev, team=DEFAULT_TEAM, goal="DCMP", sims=800, asof=None, offline=False, ctx=None,
                     with_matches=True, log=None, seed=0):
    """-> (rankings dict, schedule dict).  ctx = dashboard.Context (built here if None and with_matches)."""
    from . import dashboard as DASH
    from . import rank as RANK
    from .data import load_event
    from .model import fit_event_model
    from .sim import simulate
    t0 = time.time()
    dk = district_key_of(data_dir, ev)
    api_dir = os.path.join(data_dir, "site", "api", ev)
    dash = _read(os.path.join(api_dir, f"dashboard.{team}.json")) or _read(os.path.join(api_dir, "dashboard.json"))
    E = load_event(data_dir, ev) if ctx is None else ctx.E
    V = DASH.asof_view(E, asof)
    now = asof if asof is not None else time.time()
    ratings = {}
    ratings_src = None
    if dash and asof is None:
        for r in dash["ratings"]:
            b = r.get("blend") or {}
            if b.get("total") is not None:
                ratings[r["team"]] = (b["total"] / 3.0, max(b.get("sd") or 6.0, 3.0) / 3.0 * 1.0)
        ratings_src = "dashboard"
    # ---- model + projection (event-level rank projection and per-match sims)
    M = proj = table = per_match = None
    if with_matches:
        if ctx is None:
            ctx = DASH.Context(data_dir, ev, offline=offline, log=log)
        M = fit_event_model(V, None, use_video=True, epa_prior=ctx.prior)
        qp = [m for m in V["tba"] if m["comp_level"] == "qm" and m["played"]]
        ql = [m for m in V["tba"] if m["comp_level"] == "qm" and not m["played"]]
        proj, per_match, table = RANK.project(M, V, qp, ql, n=1500, seed=seed)
        if not ratings or asof is not None:
            ratings = {}
            for t in V["teams"]:
                b = DASH.blend_rating(M, t)
                ratings[t] = (b["total"] / 3.0, max(b["sd"], 3.0) / 3.0)
    # ---- district + goal
    goal_out = dist_rows = selection = None
    S = None
    if dk:
        D = DI.load_district(data_dir, dk, asof=asof, offline=offline, log=log)
        S = Season(D, n=sims, seed=seed, ratings=ratings or None, data_dir=data_dir, offline=offline, log=log)
        goal_out = goal_block(S, team, goal, focal=ev, now=now)
        try:
            from . import selection as SEL
            comps = {}
            if dash and asof is None:
                comps = {r["team"]: r.get("blend") or {} for r in dash["ratings"]}
            elif M is not None:
                comps = {t: DASH.blend_rating(M, t) for t in V["teams"]}
            sc = _read(os.path.join(api_dir, "scout.json")) or {}
            if asof is not None:                       # replay: the scout snapshot as of then, if built
                ss = os.path.join(api_dir, "scout", f"{int(asof)}.json")
                sc = _read(ss) or sc
            dfj = _read(os.path.join(api_dir, "defense.json")) or {}
            selection = SEL.selection_block(S, ev, team, goal_out if goal.upper() == "DCMP" else None, asof=asof,
                                            nick=(ctx.nick if ctx is not None else {}), comps=comps,
                                            scout={r["team"]: r for r in sc.get("teams") or []},
                                            defense={"teams": dfj.get("teams"), "teams_meta": dfj.get("teams_meta")})
        except Exception:
            import traceback
            traceback.print_exc()
            selection = None
        qual, cutoff = S.dcmp_outcome(S.base_run)
        pq = qual.mean(0)
        pre = S.base_run["pre"]
        dist_rows = {}
        for t in V["teams"] if V else []:
            c = S.col.get(t)
            if c is None:
                continue
            cur = S.rb.get(t, 0) + sum(d.get(t, {}).get("total", 0) for k, d in S.done_pts.items() if k in S.counted[t])
            # our row = the headline (no awards for us unless contender); other teams keep random awards, which is
            # better calibrated across the event (09:00 replay: Brier .133 with vs .147 without)
            na = no_award_mask(S, t) if t == team else None
            p_us = qual[na, c].mean() if (na is not None and na.sum() >= 20 and not _contender()) else pq[c]
            dist_rows[t] = {"current": cur, "projected": _r(pre[:, c].mean()), "p10": _r(_q(pre[:, c])[0]),
                            "p90": _r(_q(pre[:, c])[2]), "p_dcmp": _r(p_us, 3), "p_dcmp_with_awards": _r(pq[c], 3)}
    # ---- rankings.json
    teams_out = []
    nick = {}
    if ctx is not None:
        nick = ctx.nick
    ranks_now = RANK.current_ranks(table, E["teams"]) if table else {}
    for t in E["teams"]:
        s = (table or {}).get(t, {})
        pj = (proj or {}).get(t, {})
        row = {"team": t, "number": _num(t), "nickname": nick.get(t),
               "rank": ranks_now.get(t), "rp": s.get("rp"), "matches_played": s.get("n"),
               "record": f"{s.get('w', 0)}-{s.get('l', 0)}-{s.get('t', 0)}" if s else None,
               "rs": round(s["rp"] / s["n"], 3) if s and s.get("n") else None,
               "projection": pj or None, "district": (dist_rows or {}).get(t)}
        teams_out.append(row)
    teams_out.sort(key=lambda r: (r["rank"] or 999, r["number"]))
    allq = [m for m in V["tba"] if m["comp_level"] == "qm"]
    rk = {"schema": SCHEMA, "kind": "rankings", "generated": int(time.time()), "event": ev, "team": team, "asof": asof,
          "quals_played": sum(1 for m in allq if m["played"]), "quals_total": len(allq),
          "district": dk, "teams": teams_out, "goal": goal_out,
          "notes": ["Rank projection: Monte Carlo of remaining quals (dashboard model).",
                    "District points: manual rules, exact vs TBA; projection and goal odds from goals.py (see README)."]}
    # ---- schedule.json
    sched = {"schema": SCHEMA, "kind": "schedule", "generated": int(time.time()), "event": ev, "team": team, "asof": asof,
             "matches": []}
    pm = {d["key"]: d for d in (per_match or [])}
    raw_by_key = {}
    for m in V["tba"]:
        us = "red" if team in m["teams"]["red"] else "blue" if team in m["teams"]["blue"] else None
        lbl, short = DASH.label(m)
        row = {"key": m["key"], "label": lbl, "short": short, "comp_level": m["comp_level"], "time": m["time"],
               "played": m["played"], "red": m["teams"]["red"], "blue": m["teams"]["blue"], "us": us,
               "analyze_url": f"#/{ev}/a/{m['key']}" if m["played"] else None}
        if m["played"]:
            row["result"] = {"red": m["red"]["score"], "blue": m["blue"]["score"],
                             "winner": "red" if m["red"]["score"] > m["blue"]["score"] else
                             "blue" if m["blue"]["score"] > m["red"]["score"] else "tie",
                             "rp": {"red": m["red"].get("rp"), "blue": m["blue"].get("rp")},
                             "bonus": {"red": m["red"].get("bonus"), "blue": m["blue"].get("bonus")}}
        elif M is not None and not m["time"] is None:
            S2 = simulate(M, m["teams"]["red"], m["teams"]["blue"], n=1500, seed=seed + 5)
            row["pred"] = {"p_red": _r(S2["p_red_win"], 3),
                           "score": {"red": _r(S2["alliances"]["red"]["score"]["mean"]),
                                     "blue": _r(S2["alliances"]["blue"]["score"]["mean"])}}
            if m["key"] in pm:
                row["pred"]["rp"] = {c: _r(pm[m["key"]][c]["rp_mean"], 2) for c in ("red", "blue")}
        sched["matches"].append(row)
    if M is not None:
        ups = [r for r in sched["matches"] if not r["played"] and r["us"]]
        for r in ups:
            b = period_breakdown(M, r["red"], r["blue"], r["us"], sims=3000, seed=seed + 11)
            r["breakdown"] = b
    sched["build_s"] = None
    rk["build_s"] = round(time.time() - t0, 1)
    sched["build_s"] = rk["build_s"]
    if selection is not None:
        selection["generated"] = int(time.time())
        selection["quals_played"], selection["quals_total"] = rk["quals_played"], rk["quals_total"]
    rk["_selection"] = selection          # popped by site_build -> selection.json
    return rk, sched


def site_build(data_dir, event, team=DEFAULT_TEAM, out_dir=None, goal=None, sims=800, offline=False, ctx=None, log=None,
               asof=None, snapshot_only=False):
    """Worker entry point: writes <out_dir or DATA/site/api/<ev>>/rankings.json and schedule.json.  `goal` defaults to env
    DASH_GOAL or DCMP (or 'Worlds', 'rank:N').  Exceptions in the district part never prevent the files being written."""
    goal = goal or os.environ.get("DASH_GOAL", "DCMP")
    out_dir = out_dir or os.path.join(data_dir, "site", "api", event)
    rk, sc = build_event_json(data_dir, event, team, goal, sims, asof, offline, ctx, True, log)
    sel = rk.pop("_selection", None)
    if asof is not None and sel:      # replay build: dated selection snapshot
        c = _dump(sel, os.path.join(out_dir, "selection", f"{int(asof)}.json"))
        if snapshot_only:             # (never overwrite the live files)
            return {"selection_kb": c // 1024, "build_s": rk["build_s"]}
    a = _dump(rk, os.path.join(out_dir, "rankings.json"))
    b = _dump(sc, os.path.join(out_dir, "schedule.json"))
    c = _dump(sel, os.path.join(out_dir, "selection.json")) if sel else 0
    return {"rankings_kb": a // 1024, "schedule_kb": b // 1024, "selection_kb": c // 1024, "build_s": rk["build_s"]}


def selection_snapshots(data_dir, event, team=DEFAULT_TEAM, out_dir=None, sims=800, offline=False, only_missing=True,
                        times=None, log=None):
    """Dated selection views for replay: one api/<ev>/selection/<ts>.json per dashboard snapshot time (dash/<ts>.json)
    or per `times`.  Each uses goals --asof <ts> (only data posted before ts)."""
    out_dir = out_dir or os.path.join(data_dir, "site", "api", event)
    if times is None:
        dd = os.path.join(out_dir, "dash")
        times = sorted(int(f.split(".")[0]) for f in os.listdir(dd) if f.endswith(".json") and f.split(".")[0].isdigit()) \
            if os.path.isdir(dd) else []
    done = []
    for ts in times:
        p = os.path.join(out_dir, "selection", f"{int(ts)}.json")
        if only_missing and os.path.exists(p):
            continue
        try:
            site_build(data_dir, event, team, out_dir, None, sims, offline, log=log, asof=ts, snapshot_only=True)   # goal: DASH_GOAL (the app setting) like the live build
            done.append(ts)
        except Exception as ex:
            (log or print)(f"selection snapshot {ts}: {ex!r}")
    idx = sorted(int(f.split(".")[0]) for f in os.listdir(os.path.join(out_dir, "selection"))
                 if f.endswith(".json") and f.split(".")[0].isdigit()) if os.path.isdir(os.path.join(out_dir, "selection")) else []
    _dump({"event": event, "times": idx}, os.path.join(out_dir, "selection", "index.json"))
    return done


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir")
    ap.add_argument("event")
    ap.add_argument("--team", default=DEFAULT_TEAM)
    ap.add_argument("--goal", default=os.environ.get("DASH_GOAL", "DCMP"))
    ap.add_argument("--asof", type=float)
    ap.add_argument("--sims", type=int, default=800)
    ap.add_argument("--out-dir")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--goal-only", action="store_true", help="print the goal block only (no match model)")
    ap.add_argument("--selection-snapshots", action="store_true",
                    help="write selection/<ts>.json for every dash/<ts>.json snapshot (or --asof) for replay")
    ap.add_argument("--all", action="store_true", help="with --selection-snapshots: rebuild existing snapshots too")
    a = ap.parse_args(argv)
    team = a.team if a.team.startswith("frc") else f"frc{a.team}"
    if a.replay:
        dk = district_key_of(a.data_dir, a.event)
        D = DI.load_district(a.data_dir, dk, offline=a.offline)
        ts = []
        for e in D["events"]:
            if e["dcmp"]:
                continue
            ms = [m for m in e["matches"] if _played(m)]
            if ms:
                ts += [_mt(min(ms, key=_mt)) - 3600, (_mt(min(ms, key=_mt)) + _mt(max(ms, key=_mt))) / 2, _mt(max(ms, key=_mt)) + 4 * 3600]
        ts = sorted(set(int(x) for x in ts))
        replay(a.data_dir, dk, None, ts, a.goal, a.sims, offline=a.offline)
        return
    if a.goal_only:
        dk = district_key_of(a.data_dir, a.event)
        D = DI.load_district(a.data_dir, dk, asof=a.asof, offline=a.offline)
        S = Season(D, n=a.sims, data_dir=a.data_dir, offline=a.offline)
        print(json.dumps(goal_block(S, team, a.goal, a.event), indent=1, default=float))
        return
    if a.selection_snapshots:
        done = selection_snapshots(a.data_dir, a.event, team, a.out_dir, a.sims, a.offline, only_missing=not a.all,
                                   times=[a.asof] if a.asof else None, log=lambda *x: print(*x, file=sys.stderr))
        print({"selection_snapshots": len(done)})
        return
    r = site_build(a.data_dir, a.event, team, a.out_dir, a.goal, a.sims, a.offline, asof=a.asof,
                   log=lambda *x: print(*x, file=sys.stderr))
    print(r)


if __name__ == "__main__":
    main()
