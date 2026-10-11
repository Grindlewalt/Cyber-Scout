"""Qualification ranking: 2026 REBUILT ranking points + TBA tiebreakers, actual and projected.

Ranking points per alliance per qual match (2026 Game Manual 6.x; checked against all 116 Sammamish alliance-matches:
TBA `rp` == 3*win + 1*tie + energized + supercharged + traversal, no exceptions):
    win 3, tie 1, loss 0
    ENERGIZED     >= 100 FUEL counted in the hub (TBA energizedAchieved; Sammamish: min achieved 101, max not 99)
    SUPERCHARGED  >= 360 FUEL counted (TBA superchargedAchieved; Sammamish: min achieved 373, max not 349)
    TRAVERSAL     >= 50 TOWER points (TBA traversalAchieved; never achieved at Sammamish -> threshold from the manual,
                  not verified on data)
A disqualified team gets 0 RP (and 0 for the tiebreak averages) for that match; surrogate appearances do not count.
Ranking score = RP / matches.  Tiebreakers (TBA rankings sort_order_info, verified on the final Sammamish table):
    1 Avg Match points (score - foul points)   2 Avg Auto FUEL (autoCount)   3 Avg Tower points   then random.

Projection: remaining quals are simulated with sim.simulate_schedule (team strength drawn once per simulated event),
RP and tiebreak stats come from the simulated alliance FUEL / tower / scores; played matches use TBA's actual values.
"""
import numpy as np

from .sim import simulate_schedule

RP_WIN, RP_TIE = 3, 1
BONUS = {"energized": ("fuel", 100.0), "supercharged": ("fuel", 360.0), "traversal": ("tower", 50.0)}


def _counts(m, a, t):
    return t not in m.get("surrogates", {}).get(a, [])


def actual_table(quals_played, teams):
    """per team: n, rp, sums for tiebreakers, W-L-T, from played quals."""
    S = {t: {"n": 0, "rp": 0.0, "pts": 0.0, "auto": 0.0, "tower": 0.0, "w": 0, "l": 0, "t": 0} for t in teams}
    for m in quals_played:
        for a in ("red", "blue"):
            o = "blue" if a == "red" else "red"
            for t in m["teams"][a]:
                if t not in S or not _counts(m, a, t):
                    continue
                s = S[t]
                s["n"] += 1
                dq = t in m.get("dqs", {}).get(a, [])
                sa, so = m[a]["score"], m[o]["score"]
                if sa > so:
                    s["w"] += 1
                elif sa < so:
                    s["l"] += 1
                else:
                    s["t"] += 1
                if dq:
                    continue
                rp = m[a].get("rp")
                if rp is None:
                    rp = (RP_WIN if sa > so else RP_TIE if sa == so else 0) + sum(m[a].get("bonus", {}).values())
                s["rp"] += rp
                s["pts"] += sa - m[a]["fouls"]
                s["auto"] += m[a]["fuel"]["auto"]
                s["tower"] += m[a].get("tower_total", m[a]["tower_pts"])
    return S


def rank_order(rs, pts, auto, tower, tiebreak):
    """indices sorted best first (arrays over teams)."""
    return np.lexsort((tiebreak, -tower, -auto, -pts, -rs))


def current_ranks(table, teams):
    """deterministic ranks (random tiebreak replaced by team number) of teams with >= 1 match."""
    ts = [t for t in teams if table[t]["n"] > 0]
    if not ts:
        return {}
    f = lambda k: np.array([table[t][k] / table[t]["n"] for t in ts])
    order = rank_order(f("rp"), f("pts"), f("auto"), f("tower"), np.array([int(t[3:]) for t in ts], float))
    return {ts[i]: r + 1 for r, i in enumerate(order)}


def bonus_probs(sim_rec):
    """per alliance P(win), P(each bonus RP), E[RP] from one simulated match record (simulate_schedule output)."""
    out = {}
    for a in ("red", "blue"):
        o = "blue" if a == "red" else "red"
        A, O = sim_rec[a], sim_rec[o]
        win = (A["score"] > O["score"]).astype(float)
        tie = (A["score"] == O["score"]).astype(float)
        d = {"win": float(win.mean())}
        rp = RP_WIN * win + RP_TIE * tie
        for name, (k, thr) in BONUS.items():
            hit = A[k] >= thr
            d[name] = float(hit.mean())
            rp = rp + hit
        d["rp_mean"] = float(rp.mean())
        out[a] = d
    return out


def project(M, E, quals_played, quals_left, n=2000, seed=0):
    """-> ({team: projection dict}, [per remaining match bonus probs])."""
    teams = E["teams"]
    table = actual_table(quals_played, teams)
    T = {t: i for i, t in enumerate(teams)}
    nT = len(teams)
    RP = np.tile([table[t]["rp"] for t in teams], (n, 1)).astype(float)
    NM = np.array([table[t]["n"] for t in teams], float)
    PTS = np.tile([table[t]["pts"] for t in teams], (n, 1)).astype(float)
    AU = np.tile([table[t]["auto"] for t in teams], (n, 1)).astype(float)
    TW = np.tile([table[t]["tower"] for t in teams], (n, 1)).astype(float)
    per_match = []
    if quals_left:
        sims = simulate_schedule(M, [(m["teams"]["red"], m["teams"]["blue"]) for m in quals_left], n=n, seed=seed)
        for m, s in zip(quals_left, sims):
            for a in ("red", "blue"):
                o = "blue" if a == "red" else "red"
                A, O = s[a], s[o]
                rp = RP_WIN * (A["score"] > O["score"]) + RP_TIE * (A["score"] == O["score"])
                for name, (k, thr) in BONUS.items():
                    rp = rp + (A[k] >= thr)
                for t in m["teams"][a]:
                    if t not in T or not _counts(m, a, t):
                        continue
                    j = T[t]
                    RP[:, j] += rp
                    NM[j] += 1
                    PTS[:, j] += A["score"] - A["fouls"]
                    AU[:, j] += A["auto_fuel"]
                    TW[:, j] += A["tower"]
            per_match.append({"key": m["key"], **bonus_probs(s)})
    NMs = np.maximum(NM, 1)
    rng = np.random.default_rng(seed + 7)
    ranks = np.zeros((n, nT), int)
    for k in range(n):
        order = rank_order(RP[k] / NMs, PTS[k] / NMs, AU[k] / NMs, TW[k] / NMs, rng.random(nT))
        ranks[k, order] = np.arange(1, nT + 1)
    out = {}
    for t, j in T.items():
        r = ranks[:, j]
        out[t] = {"rank_mean": round(float(r.mean()), 2), "rank_p10": int(np.percentile(r, 10)),
                  "rank_p90": int(np.percentile(r, 90)), "p_top8": round(float((r <= 8).mean()), 3),
                  "p_top4": round(float((r <= 4).mean()), 3), "p_first": round(float((r == 1).mean()), 3),
                  "rp_mean": round(float(RP[:, j].mean()), 2), "rs_mean": round(float((RP[:, j] / NMs[j]).mean()), 3),
                  "rp_now": table[t]["rp"], "matches_total": int(NM[j]), "matches_played": table[t]["n"]}
    return out, per_match, table
