"""Monte Carlo match simulator from six team models (live/strategy/model.py).

Per simulated match:
  1. parameter draw: each robot's component means are drawn from the joint GLS posterior (parameter uncertainty:
     few matches per team -> wide), including the event-level mu / schedule effect beta;
  2. AUTO: robot auto FUEL ~ Gamma(mean, sigma_auto^2) (+ the alliance's mean human-player FUEL); the alliance with
     more auto FUEL wins auto (tie -> coin flip, as FMS does) -> its hub is INACTIVE in shift 1 and active in 2+4;
  3. TELEOP: transition / active-shift / endgame FUEL per robot ~ Gamma with mean theta + beta*won_auto/3; the four
     components of one robot are correlated through a Gaussian copula with the residual correlation estimated from TBA
     (e.g. a robot that has a bad active-shift game tends to have a bad endgame too);  the active total is split
     between the two active shifts with the event's measured first/second active-shift fraction;
  4. towers: auto L1 ~ Bernoulli (max 2 scored per alliance), endgame level ~ Categorical(team posterior);
  5. fouls: NOT modelled per team; foul points are resampled from the event's empirical distribution (they are part
     of the real score the backtest compares against).  `fouls=False` turns that off.
Not modelled (no estimate from data yet, see README): defence and congestion interactions between robots,
explicit dead-robot mixture (dead/stall matches are inside the fitted match-to-match variance instead).
"""
import math

import numpy as np
from scipy import stats

from .data import COMPONENTS, PERIODS, TOWER_AUTO_PTS, TOWER_TELEOP_PTS
from .model import team_theta

MIN_MEAN = 0.05


def _gamma_ppf(u, mean, var):
    mean = np.maximum(mean, MIN_MEAN)
    k = mean * mean / var
    return stats.gamma.ppf(u, a=k, scale=var / mean)


def _param_draws(M, c, teams, n, rng):
    """draw (n, 6) robot component means mu+delta and (n,) beta from the joint posterior."""
    cp = M.comp[c]
    nf = cp["nf"]
    idx = list(range(nf))
    cols = []
    for t in teams:
        if t in cp["team_index"]:
            cols.append(nf + cp["team_index"][t])
        else:
            cols.append(None)
    sub = idx + [k for k in cols if k is not None]
    mean = cp["mean"][sub]
    cov = cp["cov"][np.ix_(sub, sub)]
    L = np.linalg.cholesky(cov + 1e-9 * np.eye(len(sub)))
    z = rng.standard_normal((n, len(sub))) @ L.T + mean
    mu = z[:, 0]
    bi = cp.get("beta_idx", 1 if nf == 2 else None)
    beta = z[:, bi] if bi is not None else np.zeros(n)
    th = np.zeros((n, len(teams)))
    j = nf
    for r, k in enumerate(cols):
        if k is None:      # team never seen by the model: event mean + between-team spread
            th[:, r] = mu + rng.standard_normal(n) * math.sqrt(cp["tau2"])
        else:
            th[:, r] = mu + z[:, j]
            j += 1
    return th, beta


def _core(M, teams, TH, BE, n, rng, fouls=True, schedule_effect=True, force_red_auto=None):
    """one match given parameter draws TH[c] (n, 6), BE[c] (n,) -> raw per-sim arrays."""
    al = np.array([0, 0, 0, 1, 1, 1])
    nc = len(COMPONENTS)
    # ---- correlated uniforms per robot (copula)
    L = np.linalg.cholesky(M.R)
    z = rng.standard_normal((n, 6, nc)) @ L.T
    U = np.clip(stats.norm.cdf(z), 1e-6, 1 - 1e-6)
    X = {}
    ci = {c: i for i, c in enumerate(COMPONENTS)}
    # auto
    X["auto"] = _gamma_ppf(U[:, :, ci["auto"]], TH["auto"], M.comp["auto"]["sigma2"])
    auto_al = np.stack([X["auto"][:, al == k].sum(1) + M.h["auto"] for k in (0, 1)], 1)
    red_wins_auto = (auto_al[:, 0] > auto_al[:, 1]) | ((auto_al[:, 0] == auto_al[:, 1]) & (rng.random(n) < 0.5))
    if force_red_auto is not None:
        red_wins_auto = np.full(n, bool(force_red_auto))
    won = np.where(al[None, :] == 0, red_wins_auto[:, None], ~red_wins_auto[:, None]).astype(float)
    for c in ("trans", "active", "endgame"):
        mean = TH[c] + (BE[c][:, None] * won / 3.0 if schedule_effect else BE[c][:, None] / 6.0)
        X[c] = _gamma_ppf(U[:, :, ci[c]], mean, M.comp[c]["sigma2"])
    # ---- per period (robots)
    f13, f24 = M.first_active_frac["13"], M.first_active_frac["24"]
    P = {p: np.zeros((n, 6)) for p in PERIODS}
    P["auto"] = X["auto"]
    P["transition"] = X["trans"]
    P["endgame"] = X["endgame"]
    w = won.astype(bool)
    P["shift1"] = np.where(w, 0.0, X["active"] * f13)
    P["shift3"] = np.where(w, 0.0, X["active"] * (1 - f13))
    P["shift2"] = np.where(w, X["active"] * f24, 0.0)
    P["shift4"] = np.where(w, X["active"] * (1 - f24), 0.0)
    # ---- human player (alliance-level means; their variance is inside the robots' fitted sigma)
    hp = {"auto": M.h["auto"], "transition": M.h["trans"], "endgame": M.h["endgame"]}
    hp_active = M.h["active"]
    # ---- towers
    auto_l1 = np.zeros((n, 6))
    end_lvl = np.zeros((n, 6), int)
    for r, t in enumerate(teams):
        ct = M.climb["team"].get(t) or {"auto_l1": M.climb["event_auto"], "end": M.climb["event_end"]}
        auto_l1[:, r] = rng.random(n) < ct["auto_l1"]
        pe = np.asarray(ct["end"], float)
        end_lvl[:, r] = rng.choice(4, size=n, p=pe / pe.sum())
    tower_end_robot = np.asarray(TOWER_TELEOP_PTS)[end_lvl]
    R = {"P": P, "red_wins_auto": red_wins_auto, "auto_l1": auto_l1, "end_lvl": end_lvl,
         "tower_auto_robot": auto_l1 * TOWER_AUTO_PTS, "tower_end_robot": tower_end_robot, "hp": hp,
         "hp_active": hp_active, "f13": f13, "f24": f24, "alliances": {}}
    for k, a in enumerate(("red", "blue")):
        sel = al == k
        fuel_by_p = {p: P[p][:, sel].sum(1) for p in PERIODS}
        fuel_by_p["auto"] = fuel_by_p["auto"] + hp["auto"]
        fuel_by_p["transition"] = fuel_by_p["transition"] + hp["transition"]
        fuel_by_p["endgame"] = fuel_by_p["endgame"] + hp["endgame"]
        aw = red_wins_auto if a == "red" else ~red_wins_auto
        fuel_by_p["shift1"] = fuel_by_p["shift1"] + np.where(aw, 0.0, hp_active * f13)
        fuel_by_p["shift3"] = fuel_by_p["shift3"] + np.where(aw, 0.0, hp_active * (1 - f13))
        fuel_by_p["shift2"] = fuel_by_p["shift2"] + np.where(aw, hp_active * f24, 0.0)
        fuel_by_p["shift4"] = fuel_by_p["shift4"] + np.where(aw, hp_active * (1 - f24), 0.0)
        fuel = sum(fuel_by_p.values())
        ta = np.minimum(auto_l1[:, sel].sum(1), 2) * TOWER_AUTO_PTS
        te = tower_end_robot[:, sel].sum(1)
        fl = rng.choice(M.fouls, size=n) if (fouls and len(M.fouls)) else np.zeros(n)
        R["alliances"][a] = {"fuel_by_p": fuel_by_p, "fuel": fuel, "ta": ta, "te": te, "fl": fl,
                             "score": fuel + ta + te + fl, "aw": aw}
    return R


def simulate(M, red, blue, n=4000, seed=0, fouls=True, param_uncertainty=True, schedule_effect=True,
             force_red_auto=None, raw=False):
    """force_red_auto=True/False overrides the auto winner (counterfactual schedule; auto FUEL itself unchanged).
    raw=True also returns the per-sim arrays under "raw"."""
    rng = np.random.default_rng(seed)
    teams = list(red) + list(blue)
    # ---- parameters
    TH, BE = {}, {}
    for c in COMPONENTS:
        if param_uncertainty:
            TH[c], BE[c] = _param_draws(M, c, teams, n, rng)
        else:
            TH[c] = np.tile([team_theta(M.comp[c], t)[0] for t in teams], (n, 1))
            BE[c] = np.full(n, M.comp[c]["beta"])
    R = _core(M, teams, TH, BE, n, rng, fouls=fouls, schedule_effect=schedule_effect, force_red_auto=force_red_auto)
    P, red_wins_auto = R["P"], R["red_wins_auto"]
    hp, hp_active = R["hp"], R["hp_active"]
    # ---- alliance scores
    out = {"n_sims": n, "alliances": {}, "robots": []}
    scores = []
    for a in ("red", "blue"):
        A = R["alliances"][a]
        score, fl, ta, te, aw = A["score"], A["fl"], A["ta"], A["te"], A["aw"]
        scores.append(score)
        out["alliances"][a] = {
            "teams": list(red if a == "red" else blue),
            "score": _dist(score), "score_no_fouls": _dist(score - fl),
            "fuel": _dist(A["fuel"]), "tower_pts_mean": round(float((ta + te).mean()), 2),
            "foul_pts_mean": round(float(fl.mean()), 2),
            "fuel_by_period_mean": {p: round(float(v.mean()), 2) for p, v in A["fuel_by_p"].items()},
            "hp_fuel_mean": round(float(hp["auto"] + hp["transition"] + hp["endgame"] + hp_active), 2),
            "p_win_auto": round(float(aw.mean()), 4),
        }
    rs, bs = scores
    p_red = float(((rs > bs) + 0.5 * (rs == bs)).mean())
    out["p_red_win"] = round(p_red, 4)
    rw = (rs > bs) + 0.5 * (rs == bs)
    out["p_red_win_if_red_wins_auto"] = round(float(rw[red_wins_auto].mean()), 4) if red_wins_auto.any() else None
    out["p_red_win_if_blue_wins_auto"] = round(float(rw[~red_wins_auto].mean()), 4) if (~red_wins_auto).any() else None
    out["margin"] = _dist(rs - bs)
    # ---- robots
    tower_auto_robot, tower_end_robot = R["tower_auto_robot"], R["tower_end_robot"]
    for r, t in enumerate(teams):
        a = "red" if r < 3 else "blue"
        aw = red_wins_auto if a == "red" else ~red_wins_auto
        rec = {"team": t, "alliance": a, "station": r % 3 + 1,
               "fuel_by_period_mean": {p: round(float(P[p][:, r].mean()), 2) for p in PERIODS},
               "fuel_mean": round(float(sum(P[p][:, r] for p in PERIODS).mean()), 2),
               "fuel_sd": round(float(sum(P[p][:, r] for p in PERIODS).std()), 2),
               "fuel_if_won_auto": round(float(sum(P[p][aw, r] for p in PERIODS).mean()), 2) if aw.any() else None,
               "fuel_if_lost_auto": round(float(sum(P[p][~aw, r] for p in PERIODS).mean()), 2) if (~aw).any() else None,
               "tower_pts_mean": round(float((tower_auto_robot[:, r] + tower_end_robot[:, r]).mean()), 2)}
        out["robots"].append(rec)
    if raw:
        out["raw"] = R
    return out


def simulate_schedule(M, matches, n=2000, seed=0, fouls=True):
    """Simulate many matches with each team's strength drawn ONCE per simulated event (so a strong draw for a team
    carries through all its matches -- needed for rank distributions).  matches: [(red3, blue3)].
    -> list of {"red"|"blue": {"score","fuel","tower","auto_fuel","fouls"} (n,) arrays, "red_wins_auto"}."""
    rng = np.random.default_rng(seed)
    teams = sorted({t for r, b in matches for t in list(r) + list(b)})
    col = {t: i for i, t in enumerate(teams)}
    TH, BE = {}, {}
    for c in COMPONENTS:
        TH[c], BE[c] = _param_draws(M, c, teams, n, rng)
    out = []
    for red, blue in matches:
        six = list(red) + list(blue)
        idx = [col[t] for t in six]
        R = _core(M, six, {c: TH[c][:, idx] for c in COMPONENTS}, BE, n, rng, fouls=fouls)
        rec = {"red_wins_auto": R["red_wins_auto"]}
        for a in ("red", "blue"):
            A = R["alliances"][a]
            rec[a] = {"score": A["score"], "fuel": A["fuel"], "tower": A["ta"] + A["te"],
                      "auto_fuel": A["fuel_by_p"]["auto"], "fouls": A["fl"]}
        out.append(rec)
    return out


def _dist(x):
    q = np.percentile(x, [5, 25, 50, 75, 95])
    return {"mean": round(float(x.mean()), 2), "sd": round(float(x.std()), 2),
            "q05": round(float(q[0]), 1), "q25": round(float(q[1]), 1), "q50": round(float(q[2]), 1),
            "q75": round(float(q[3]), 1), "q95": round(float(q[4]), 1)}
