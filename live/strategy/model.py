"""Robot model: per-team estimates (with uncertainty) from per-match robot-level data, shrunk toward event priors.

Scoring (what the simulator uses) -- one hierarchical linear-Gaussian model per FUEL component
c in {auto, trans, active, endgame} (counted FUEL, i.e. points):

    robot i in a match:   x_ic = mu_c + beta_c * won_auto / 3 + delta_ic + eps,   eps ~ N(0, sigma_c^2)
    team effect:          delta_ic ~ N(0, tau_c^2)                (shrinkage toward the event mean mu_c)
    TBA alliance row:     y_ac = h_c + sum_{i in a} x_ic          (h_c = mean human-player FUEL, from video)
    video robot rows:     y_ic = x_ic + eta_i,  eta ~ N(0, V_meas) (soft allocation of the alliance's TBA count;
                                                                    V_meas from identity uncertainty + 10% call noise)

  An alliance-match with a usable video split contributes its three robot rows *instead of* its TBA row (they sum to
  the TBA count minus HP, so using both would double count).  Low identity confidence makes the allocation uniform and
  V_meas large, so such matches degrade gracefully to "TBA row" information.  sigma_c^2, tau_c^2 are set by maximising
  the (profiled) marginal likelihood of the TBA alliance rows with a weak log-normal prior on tau/sigma; mu_c, beta_c
  and the team effects come out of one GLS solve.  beta_c is the event-level "schedule effect" of winning auto
  (hub active in shifts 2+4 instead of 1+3).

Climbs: per-robot TBA tower levels (exact station mapping -> robot-level, trusted) with Dirichlet / Beta shrinkage.
Everything else (fire rate, volleys, zones, defence, reliability, ...): weighted shrinkage of video robot-match
features, weight = identity ramp x coverage (v1 identity ~0.2 -> weight 0).
"""
import math
from collections import Counter, defaultdict

import numpy as np

from .data import COMPONENTS, PERIODS, SHIFTS, PERIOD_T, shot_zone

DEFAULT_ALLIANCE = {"auto": 25.0, "trans": 8.0, "active": 70.0, "endgame": 40.0}   # pre-event guess (no data)
LAM_GRID = np.exp(np.linspace(math.log(0.02), math.log(20.0), 41))
LAM_PRIOR_SD = 1.0          # log(tau^2/sigma^2) ~ N(0, 1)
REL_CALL_NOISE = 0.10       # extra relative noise on video robot rows (made calls, attribution, scale)
TRUST_ID_INFO = 0.30        # video blocks whose mean identity information is below this are not used
SHRINK_K = 3.0              # pseudo-matches for descriptive shrinkage
CLIMB_K = 4.0               # pseudo-matches for climb shrinkage
# event-level time trend (drift) per component: x_ic gets + gamma_c * s, s = (match index - latest index) / TREND_UNIT,
# so mu is the level at the most recent match and predictions do not extrapolate.  gamma ~ N(0, (TREND_SD_FRAC *
# DEFAULT_ALLIANCE[c])^2) per robot per TREND_UNIT matches.  TREND = components that get it (empty = off).
TREND = frozenset()
TREND_UNIT = 10.0
TREND_SD_FRAC = 0.06


# ================================================================================ scoring components
def _profile_marglik(y, F, Z, lam, base_var):
    """log marginal likelihood of y ~ N(F g, s2 (base_var I + lam Z Z^T)), g and s2 profiled out."""
    n = len(y)
    C = base_var * np.eye(n) + lam * Z @ Z.T
    L = np.linalg.cholesky(C)
    Li_y = np.linalg.solve(L, y)
    Li_F = np.linalg.solve(L, F)
    g, *_ = np.linalg.lstsq(Li_F, Li_y, rcond=None)
    r = Li_y - Li_F @ g
    s2 = max(float(r @ r) / n, 1e-6)
    ll = -0.5 * n * math.log(s2) - np.log(np.diag(L)).sum() - 0.5 * n
    return ll, s2


def fit_hyper(c, rows_tba, teams, h, prior_hyper=None, use_trend=False):
    """sigma^2 (robot match noise) and tau^2 (between-team spread) from TBA alliance rows (team_list, won_auto, y, w).
    prior_hyper (from the EPA prior's earlier events) replaces the generic guesses while the event has < 6 rows."""
    T = {t: i for i, t in enumerate(teams)}
    nT = len(teams)
    use_beta = c != "auto"
    nf = (2 if use_beta else 1) + int(use_trend)
    # ---- hyperparameters from TBA alliance rows (all prior alliance-matches)
    n = len(rows_tba)
    if n >= 6:
        y = np.array([r[2] - h for r in rows_tba], float)
        F = np.array([[3.0] + ([float(r[1])] if use_beta else []) + ([3.0 * r[4]] if use_trend else []) for r in rows_tba])
        Z = np.zeros((n, nT))
        for j, r in enumerate(rows_tba):
            for t in r[0]:
                Z[j, T[t]] = 1.0
        best = None
        for lam in LAM_GRID:
            ll, s2 = _profile_marglik(y, F, Z, lam, 3.0)
            lp = ll - 0.5 * (math.log(lam) / LAM_PRIOR_SD) ** 2
            if best is None or lp > best[0]:
                best = (lp, lam, s2)
        _, lam, s2 = best
        s2 *= n / max(n - nf, 1.0)      # REML-style df correction for the profiled fixed effects
    elif prior_hyper:
        s2 = float(prior_hyper["sigma2"])
        lam = float(prior_hyper["tau2"]) / s2
    elif n >= 2:
        yy = np.array([r[2] for r in rows_tba], float)
        lam = 1.0
        s2 = max(float(np.var(yy, ddof=1)), 25.0) / (3.0 + 3.0 * lam)
    else:
        lam = 1.0
        s2 = (0.6 * DEFAULT_ALLIANCE[c]) ** 2 / 6.0
    mean_all = np.mean([r[2] for r in rows_tba]) if rows_tba else DEFAULT_ALLIANCE[c]
    return {"sigma2": float(s2), "tau2": float(lam * s2), "lam": float(lam), "mu0": (mean_all - h) / 3.0, "n_rows": n}


def team_theta(comp, team):
    """posterior mean / sd of mu + delta_team (expected robot FUEL for the component, schedule-neutral)."""
    T = comp["team_index"]
    nf = comp["nf"]
    if team not in T:
        return comp["mu"], math.sqrt(comp["cov"][0, 0] + comp["tau2"])
    i = nf + T[team]
    m = comp["mean"][0] + comp["mean"][i]
    v = comp["cov"][0, 0] + comp["cov"][i, i] + 2 * comp["cov"][0, i]
    return float(m), float(math.sqrt(max(v, 0.0)))


# ================================================================================ event model
class EventModel:
    pass


def _prior(E, before, max_order=None):
    """played matches strictly before `before` (unix time); None = all played."""
    out = []
    for m in E["tba"]:
        if not m["played"]:
            continue
        if before is not None and m["time"] >= before:
            continue
        out.append(m)
    return out


def fit_event_model(E, before=None, use_video=True, recency_halflife=None, epa_prior=None, trend=None):
    """Fit everything from matches played before `before` (unix time; None = all played).
    epa_prior (epa.pre_event_prior): pre-event per-team component means/sds replace the "event mean" shrinkage target
    (team effects start at the team's EPA instead of 0) and set the mu/beta/hyper priors until the event has data."""
    prior = _prior(E, before)
    keys = {m["key"] for m in prior}
    teams = E["teams"]
    M = EventModel()
    M.event = E["event"]
    M.before = before
    M.n_matches = len(prior)
    M.teams = teams
    M.use_video = use_video
    M.epa_prior = epa_prior
    # recency weights (optional): weight 0.5 ** (age in matches / halflife)
    order = {m["key"]: i for i, m in enumerate(prior)}
    nprior = len(prior)

    trend = TREND if trend is None else frozenset(trend)
    M.trend_s = {m["key"]: (order[m["key"]] - (nprior - 1)) / TREND_UNIT for m in prior}
    ts = M.trend_s

    def rw(key):
        if not recency_halflife:
            return 1.0
        return 0.5 ** ((nprior - 1 - order[key]) / recency_halflife)

    # ---- video blocks (trusted only)
    vblocks = []
    if use_video:
        for b in E["video"]["alliance"]:
            if b["match"] not in keys:
                continue
            infos = [v["id_info"] for v in b["comp"].values() if v["usable"]]
            if not infos or np.mean(infos) < TRUST_ID_INFO:
                continue
            vblocks.append(b)
    M.video_matches = sorted({b["match"] for b in vblocks})
    # ---- human-player mean per component (alliance level) from trusted video
    M.h = {}
    for c in COMPONENTS:
        hs = [b["comp"][c]["hp"] for b in vblocks if b["comp"][c]["usable"]]
        M.h[c] = float(np.mean(hs)) if len(hs) >= 2 else 0.0
    # ---- components
    M.comp = {}
    for c in COMPONENTS:
        used = set()
        blocks = []
        for b in vblocks:
            v = b["comp"][c]
            if v["usable"]:
                blocks.append((b["teams"], b["won_auto"], v["y"], v["V"], rw(b["match"]), ts[b["match"]]))
                used.add((b["match"], b["alliance"]))
        rows_all = [(m["teams"][a], m[a]["won_auto"], float(m[a]["comp"][c]), rw(m["key"]), ts[m["key"]])
                    for m in prior for a in ("red", "blue")]
        rows_tba_post = [(m["teams"][a], m[a]["won_auto"], float(m[a]["comp"][c]), rw(m["key"]), ts[m["key"]])
                         for m in prior for a in ("red", "blue") if (m["key"], a) not in used]
        # hyperparameters from all TBA rows; posterior from TBA rows (non-video) + video blocks
        ph = (epa_prior or {}).get("hyper", {}).get(c)
        ut = c in trend and len(prior) >= 6
        hyp = fit_hyper(c, rows_all, teams, M.h[c], prior_hyper=ph, use_trend=ut)
        comp = fit_posterior(c, hyp, rows_tba_post, blocks, teams, M.h[c], epa_prior=epa_prior,
                             rows_tba_all=rows_all if blocks else None, use_trend=ut)
        M.comp[c] = comp
    # ---- residual correlation across components (alliance level), shrunk toward identity
    M.R = _resid_corr(M, prior)
    # ---- schedule split of the active total between the two active shifts
    fr = {"13": [], "24": []}
    for m in prior:
        for a in ("red", "blue"):
            f = m[a]["fuel"]
            if m[a]["won_auto"]:
                s = f["shift2"] + f["shift4"]
                if s > 0:
                    fr["24"].append(f["shift2"] / s)
            else:
                s = f["shift1"] + f["shift3"]
                if s > 0:
                    fr["13"].append(f["shift1"] / s)
    M.first_active_frac = {k: (float((np.sum(v) + 0.5 * 4) / (len(v) + 4))) for k, v in fr.items()}
    # ---- climbs
    M.climb = _fit_climbs(prior, teams, epa_prior)
    # ---- fouls: empirical distribution of foul points per alliance-match
    M.fouls = np.array([m[a]["fouls"] for m in prior for a in ("red", "blue")], float)
    # ---- descriptive robot model (video)
    M.robot = _fit_descriptive(E, keys, teams)
    M.auto_routines = _fit_routines(E, prior, teams)
    return M


def _prior_terms(c, hyp, teams, h, epa_prior, use_trend=False):
    """prior mean m0 and precision P0 over [mu, (beta), (trend), delta_teams]."""
    T = {t: i for i, t in enumerate(teams)}
    nT = len(teams)
    use_beta = c != "auto"
    nf = (2 if use_beta else 1) + int(use_trend)
    p = nf + nT
    m0 = np.zeros(p)
    P0 = np.zeros((p, p))
    m0[0] = hyp["mu0"]
    P0[0, 0] = 1.0 / (0.5 * DEFAULT_ALLIANCE[c] / 3.0) ** 2   # pre-event guess, +-50% (matters only with ~no data)
    if use_beta:
        P0[1, 1] = 1.0 / (40.0 ** 2)
    if use_trend:
        P0[nf - 1, nf - 1] = 1.0 / (TREND_SD_FRAC * DEFAULT_ALLIANCE[c]) ** 2
    for i in range(nT):
        P0[nf + i, nf + i] = 1.0 / hyp["tau2"]
    pt = (epa_prior or {}).get("team") or {}
    if pt:
        mbar = epa_prior["event_mean"][c]
        if hyp.get("n_rows", 0) < 6:      # no event data yet: the EPA field mean is the best guess for mu
            m0[0] = mbar - h / 3.0
        P0[0, 0] = 1.0 / max(0.3 * abs(mbar), 1.0) ** 2
        bp = ((epa_prior.get("hyper") or {}).get(c) or {}).get("beta")
        if use_beta and bp:
            m0[1] = bp[0]
            P0[1, 1] = 1.0 / bp[1] ** 2
        for t, d in pt.items():
            if t not in T:
                continue
            m, sd = d["comp"][c]
            m0[nf + T[t]] = m - mbar
            P0[nf + T[t], nf + T[t]] = 1.0 / max(sd, 1e-3) ** 2
    return m0, P0, T, nf, p


def _rows_info(c, rows_tba, sigma2, T, nf, p, h, use_trend=False):
    use_beta = c != "auto"
    A = np.zeros((p, p))
    b = np.zeros(p)
    for (tl, wa, yv, wt, sv) in rows_tba:
        x = np.zeros(p)
        x[0] = 3.0
        if use_beta:
            x[1] = float(wa)
        if use_trend:
            x[nf - 1] = 3.0 * sv
        for t in tl:
            x[nf + T[t]] = 1.0
        prec = wt / (3.0 * sigma2)
        A += prec * np.outer(x, x)
        b += prec * x * (yv - h)
    return A, b


def _theta_var(cov, nf, j):
    return float(cov[0, 0] + cov[nf + j, nf + j] + 2 * cov[0, nf + j])


def fit_posterior(c, hyp, rows_tba, blocks, teams, h, epa_prior=None, rows_tba_all=None, use_trend=False):
    """posterior with hyperparameters fixed from `hyp`.  Also returns, per team, the share of the posterior precision
    of theta (= mu + delta) that comes from the prior (EPA or event-mean shrinkage), the event's TBA rows and video."""
    sigma2, tau2 = hyp["sigma2"], hyp["tau2"]
    m0, P0, T, nf, p = _prior_terms(c, hyp, teams, h, epa_prior, use_trend)
    use_beta = c != "auto"
    A_t, b_t = _rows_info(c, rows_tba, sigma2, T, nf, p, h, use_trend)
    A = P0 + A_t
    b = P0 @ m0 + b_t
    for (tl, wa, yv, V, wt, sv) in blocks:
        X = np.zeros((3, p))
        X[:, 0] = 1.0
        if use_beta:
            X[:, 1] = float(wa) / 3.0
        if use_trend:
            X[:, nf - 1] = sv
        for k, t in enumerate(tl):
            X[k, nf + T[t]] = 1.0
        yv = np.asarray(yv, float)
        S = sigma2 * np.eye(3) + np.asarray(V) + np.diag((REL_CALL_NOISE * yv) ** 2 + 1.0)
        Si = np.linalg.inv(S) * wt
        A += X.T @ Si @ X
        b += X.T @ Si @ yv
    cov = np.linalg.inv(A)
    mean = cov @ b
    # ---- information shares per team (precision of theta_team)
    cov_p = np.linalg.inv(P0)
    if blocks and rows_tba_all is not None:
        A_all, _ = _rows_info(c, rows_tba_all, sigma2, T, nf, p, h, use_trend)
        cov_nv = np.linalg.inv(P0 + A_all)
    else:
        cov_nv = cov
    shares = {}
    for t, j in T.items():
        i_full = 1.0 / _theta_var(cov, nf, j)
        i_prior = 1.0 / _theta_var(cov_p, nf, j)
        i_nv = 1.0 / _theta_var(cov_nv, nf, j)
        vid = max(0.0, i_full - i_nv) / i_full
        pri = min(1.0, i_prior / i_full)
        shares[t] = {"prior": pri, "video": min(vid, 1.0 - pri), "tba": max(0.0, 1.0 - pri - vid)}
    return {"mu": float(mean[0]), "beta": float(mean[1]) if use_beta else 0.0,
            "beta_sd": float(math.sqrt(cov[1, 1])) if use_beta else 0.0,
            "sigma2": sigma2, "tau2": tau2, "lam": hyp["lam"], "h": float(h), "nf": nf,
            "beta_idx": 1 if use_beta else None, "trend_idx": nf - 1 if use_trend else None,
            "trend": float(mean[nf - 1]) if use_trend else 0.0,
            "trend_sd": float(math.sqrt(cov[nf - 1, nf - 1])) if use_trend else 0.0,
            "mean": mean, "cov": cov, "team_index": T, "n_tba_rows": len(rows_tba), "n_video_blocks": len(blocks),
            "info_shares": shares, "prior_mean": m0, "epa_prior": bool((epa_prior or {}).get("team"))}


def _resid_corr(M, prior, n0=10.0):
    k = len(COMPONENTS)
    if len(prior) < 3:
        return np.eye(k)
    R = []
    for m in prior:
        for a in ("red", "blue"):
            r = []
            for c in COMPONENTS:
                cp = M.comp[c]
                pred = cp["h"] + cp["beta"] * m[a]["won_auto"] + sum(team_theta(cp, t)[0] for t in m["teams"][a]) \
                    + 3.0 * cp.get("trend", 0.0) * M.trend_s.get(m["key"], 0.0)
                r.append((m[a]["comp"][c] - pred) / math.sqrt(3 * cp["sigma2"]))
            R.append(r)
    R = np.array(R)
    n = len(R)
    C = np.corrcoef(R.T) if n > 3 else np.eye(k)
    C = np.nan_to_num(C)
    C = (n * C + n0 * np.eye(k)) / (n + n0)
    np.fill_diagonal(C, 1.0)
    # make PSD
    w, V = np.linalg.eigh(C)
    C = V @ np.diag(np.clip(w, 1e-3, None)) @ V.T
    d = np.sqrt(np.diag(C))
    return C / np.outer(d, d)


def _fit_climbs(prior, teams, epa_prior=None):
    n = defaultdict(int)
    auto_k = defaultdict(int)
    end_k = defaultdict(lambda: np.zeros(4))
    pc = {t: d["climb"] for t, d in ((epa_prior or {}).get("team") or {}).items() if d.get("climb")}
    for m in prior:
        for a in ("red", "blue"):
            for s, t in enumerate(m["teams"][a]):
                n[t] += 1
                auto_k[t] += int(m[a]["auto_tower"][s] >= 1)
                end_k[t][m[a]["end_tower"][s]] += 1
    N = sum(n.values())
    p_auto = (sum(auto_k.values()) + 0.5) / (N + 10.0)
    p_end = (sum(end_k.values(), np.zeros(4)) + np.array([5.0, 0.5, 0.25, 0.25])) / (N + 6.0)
    out = {"event_auto": float(p_auto), "event_end": p_end.tolist(), "team": {}}
    for t in teams:
        k = n.get(t, 0)
        q = pc.get(t)
        if q:       # earlier events this season: down-weighted pseudo-observations
            kk = k + q["n"]
            pa = (auto_k.get(t, 0) + q["auto"] + CLIMB_K * p_auto) / (kk + CLIMB_K)
            pe = (end_k[t] + np.asarray(q["end"]) + CLIMB_K * p_end) / (kk + CLIMB_K)
            out["team"][t] = {"n": k, "auto_l1": float(pa), "end": [float(x) for x in pe],
                              "auto_obs": int(auto_k.get(t, 0)), "end_obs": [int(x) for x in end_k[t]] if t in end_k else [0, 0, 0, 0],
                              "prior_n": q["n"]}
            continue
        pa = (auto_k.get(t, 0) + CLIMB_K * p_auto) / (k + CLIMB_K)
        pe = (end_k[t] + CLIMB_K * p_end) / (k + CLIMB_K) if t in end_k else p_end.copy()
        out["team"][t] = {"n": k, "auto_l1": float(pa), "end": [float(x) for x in pe],
                          "auto_obs": int(auto_k.get(t, 0)), "end_obs": [int(x) for x in end_k[t]] if t in end_k else [0, 0, 0, 0]}
    return out


# ================================================================================ descriptive (video)
def _wstats(vals):
    """vals: list of (value, weight). weighted mean, weighted var, effective n."""
    v = [(x, w) for x, w in vals if x is not None and w > 0 and not (isinstance(x, float) and math.isnan(x))]
    if not v:
        return None, None, 0.0
    x = np.array([a for a, _ in v], float)
    w = np.array([b for _, b in v], float)
    m = float((w * x).sum() / w.sum())
    var = float((w * (x - m) ** 2).sum() / w.sum()) if len(v) > 1 else None
    neff = float(w.sum() ** 2 / (w ** 2).sum())
    return m, var, float(w.sum())


def _shrink(team_vals, event_vals, k=SHRINK_K):
    """shrink a team's weighted mean toward the event mean; returns {v, sd, n_eff, event} or None."""
    em, ev, _ = _wstats(event_vals)
    if em is None:
        return None
    tm, tv, wsum = _wstats(team_vals)
    var = ev if ev is not None else (abs(em) * 0.5) ** 2
    if tm is None:
        return {"v": round(em, 3), "sd": round(math.sqrt(var), 3), "n_eff": 0.0, "event": round(em, 3), "prior_only": True}
    v = (wsum * tm + k * em) / (wsum + k)
    sd = math.sqrt(var / (wsum + k))
    return {"v": round(v, 3), "sd": round(sd, 3), "n_eff": round(wsum, 2), "event": round(em, 3), "prior_only": False}


def _an(r, *path):
    d = r.get("an")
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


DESC = {   # name -> extractor on a robot-match record
    "fire_rate": lambda r: _an(r, "scoring", "fire_rate"),
    "volley_size": lambda r: _an(r, "scoring", "volley_size"),
    "volley_max": lambda r: _an(r, "scoring", "volley_max"),
    "cycle_s": lambda r: _an(r, "scoring", "cycle_s"),
    "accuracy": lambda r: _an(r, "scoring", "accuracy"),
    "shot_dist_m": lambda r: _an(r, "scoring", "dist_mean"),
    "on_move_rate": lambda r: _an(r, "scoring", "on_move_rate"),
    "passes": lambda r: _an(r, "scoring", "passes"),
    "intakes": lambda r: _an(r, "collection", "intakes"),
    "defense_s": lambda r: _an(r, "defense", "time_s"),
    "contacts": lambda r: _an(r, "defense", "contacts"),
    "pins": lambda r: _an(r, "defense", "pins"),
    "defended_s": lambda r: _an(r, "defended", "time_s"),
    "congestion": lambda r: _an(r, "congestion"),
    "dead": lambda r: (None if _an(r, "reliability", "dead") is None else float(bool(_an(r, "reliability", "dead")))),
    "stalls": lambda r: _an(r, "reliability", "stalls"),
    "auto_made": lambda r: _an(r, "auto", "made"),
    "first_shot_teleop": lambda r: _an(r, "scoring", "first_shot_teleop"),
}


def _fit_descriptive(E, keys, teams):
    recs = [r for r in E["video"]["robot"] if r["match"] in keys and r["w"] > 0]
    by_team = defaultdict(list)
    for r in recs:
        by_team[r["team"]].append(r)
    out = {"n_records": len(recs), "team": {}}
    ev = {name: [(f(r), r["w"]) for r in recs] for name, f in DESC.items()}
    # retained fire rate when defended: weight additionally by defended time (needs >= ~5 s to mean anything)
    ret = lambda r: [(_an(r, "defended", "rate_retained"),
                      r["w"] * min(1.0, (_an(r, "defended", "time_s") or 0) / 20.0))]
    ev_ret = [x for r in recs for x in ret(r) if x[0] is not None]
    # hub-state behaviour (event level)
    for t in teams:
        R = by_team.get(t, [])
        d = {"n_matches": len(R), "w_sum": round(sum(r["w"] for r in R), 2),
             "matches": [r["match"] for r in R]}
        for name, f in DESC.items():
            d[name] = _shrink([(f(r), r["w"]) for r in R], ev[name])
        d["rate_retained_defended"] = _shrink([x for r in R for x in ret(r) if x[0] is not None], ev_ret, k=1.5)
        d["hub_state"] = _hub_state(R)
        d["zones"] = _zones(R)
        d["by_period_made"] = _by_period(R)
        d["role"] = _role(R)
        out["team"][t] = d
    out["event_hub_state"] = _hub_state(recs)
    return out


def _hub_state(R):
    acc = {s: {"att": 0.0, "made": 0.0, "pass": 0.0, "secs": 0.0, "zones": defaultdict(float), "zw": 0.0}
           for s in ("active", "inactive")}
    W = 0.0
    for r in R:
        hs = r.get("hub_state")
        if not hs:
            continue
        w = r["w"]
        W += w
        for s in ("active", "inactive"):
            for k in ("att", "made", "pass", "secs"):
                acc[s][k] += w * hs[s][k]
            z = (r.get("zones_by_hub_state") or {}).get(s)
            if z:
                for k in ("own", "neutral", "opp"):
                    acc[s]["zones"][k] += w * z[k]
                acc[s]["zw"] += w
    if W <= 0:
        return None
    out = {"w_sum": round(W, 2)}
    for s in ("active", "inactive"):
        a = acc[s]
        mins = a["secs"] / 60.0
        out[s] = {"shots_per_min": round(a["att"] / mins, 2) if mins else None,
                  "made_per_min": round(a["made"] / mins, 2) if mins else None,
                  "passes_per_min": round(a["pass"] / mins, 2) if mins else None,
                  "zone_frac": {k: round(v / a["zw"], 2) for k, v in a["zones"].items()} if a["zw"] else None}
    return out


def _zones(R):
    z = defaultdict(float)
    zp = defaultdict(lambda: defaultdict(float))
    W = 0.0
    for r in R:
        for o in r.get("origins") or []:
            if not o["counted"]:
                continue
            name = shot_zone(o["xc"], o["yc"])
            z[name] += r["w"]
            zp[name][o["p"]] += r["w"]
            W += r["w"]
    if W <= 0:
        return None
    top = sorted(z.items(), key=lambda kv: -kv[1])
    return {"w_made": round(W, 1), "share": [{"zone": k, "share": round(v / W, 3),
                                              "periods": {p: round(x / v, 2) for p, x in zp[k].items()}}
                                             for k, v in top]}


def _by_period(R):
    s = defaultdict(float)
    W = 0.0
    for r in R:
        bp = r.get("by_period")
        if not bp:
            continue
        W += r["w"]
        for p in PERIODS:
            s[p] += r["w"] * bp[p]["counted"]
    if W <= 0:
        return None
    tot = sum(s.values())
    return {"per_match": {p: round(s[p] / W, 2) for p in PERIODS},
            "share": {p: round(s[p] / tot, 3) for p in PERIODS} if tot else None, "w_sum": round(W, 2)}


def _role(R):
    c = Counter()
    for r in R:
        role = _an(r, "role")
        if role:
            c[role] += r["w"]
    if not c:
        return None
    tot = sum(c.values())
    return {"v": c.most_common(1)[0][0], "scores": {k: round(v / tot, 2) for k, v in c.items()}, "w_sum": round(tot, 2)}


def _fit_routines(E, prior, teams):
    """routine usage per team and start station, from the site's clustered autos (matches limited to `prior`)."""
    keys = {m["key"]: m for m in prior}
    short2key = {}
    for m in prior:
        lvl, num, sub = m["order"]
        short2key[("Q" if lvl == 0 else m["comp_level"].upper()) + str(num) + (f"M{sub}" if sub else "")] = m["key"]
    vid_w = {(r["match"], r["team"]): r for r in E["video"]["robot"]}
    out = {}
    for t in teams:
        res = []
        for r in E["routines"].get(t, []):
            for s in r["matches"]:
                k = short2key.get(s)
                if not k:
                    continue
                m = keys[k]
                a = "red" if t in m["teams"]["red"] else "blue"
                st = m["teams"][a].index(t) + 1
                vr = vid_w.get((k, t))
                am = _an(vr, "auto", "made") if vr else None
                res.append({"routine": r["id"], "name": r["name"], "match": k, "station": st,
                            "auto_made": am, "w": vr["w"] if vr else 0.0,
                            "auto_tower": m[a]["auto_tower"][st - 1]})
        out[t] = res
    return out
