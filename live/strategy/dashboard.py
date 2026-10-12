"""Strategy dashboard JSON (contract: live/strategy/DASHBOARD.md).  EPA is the prior, Cyber-Scout video the update.

    python -m live.strategy.dashboard DATA_DIR [EVENT] [--team frc4911] [--asof TS] [--out FILE]
    python -m live.strategy.dashboard DATA_DIR [EVENT] --site [--snapshots] [--previews team|all|none]

--site       write DATA_DIR/site/api/<ev>/dashboard.json (+ dashboard.<team>.json, preview/<match>.json)
--snapshots  also write dash/<ts>.json every 30 min across each event day + dash/index.json (replay slider)
--asof TS    replay: only results posted (TBA post_result_time) before TS; video only VIDEO_LAG s after posting
--offline    no network (Statbotics / TBA prior events from cache only)

Pipeline per dashboard:  as-of view of the event -> pre-event EPA prior (epa.py; static, cached) -> robot model
(model.fit_event_model with the EPA prior; event TBA rows + trusted video update it) -> ratings (EPA, blend,
video_weight) -> rank projection (rank.py, Monte Carlo of the remaining quals) -> next-match plan (preview.py +
counterfactual auto sims + defence/auto/endgame) -> our levers + trend -> schedule.
"""
import argparse
import datetime
import json
import math
import os
import sys
import time

import numpy as np
from scipy import stats

from . import epa as EPA
from . import matchday as MD
from . import rank as RANK
from .data import COMPONENTS, LEN, PERIODS, SHIFTS, WID, load_event, shot_zone
from .model import fit_event_model, team_theta
from .preview import build_preview
from .sim import simulate

SCHEMA = "cyber-scout/dashboard@1"
DEFAULT_TEAM = os.environ.get("DASH_TEAM", "frc4911")
VIDEO_LAG = 600            # replay: video robot data assumed available 10 min after TBA posts the result
POST_LAG = 300             # when TBA has no post_result_time: result assumed posted 5 min after the match
PLAYOFF_VISIBLE = 1500     # replay: a playoff match's alliances count as known 25 min before it is played
SNAP_STEP = 1800
ENGAGE = 0.7               # fraction of the target's active-shift time a dedicated defender actually affects (a priori)
DISAGREE_RATING = 15.0     # ratings table: blend vs EPA chip when |diff| >= max(15 pts, 1.5 sd)
GENERIC_RETAINED = 0.75    # shot rate kept while defended when there is no event data at all (a priori)
# reference backtest of the dashboard model (python -m live.strategy.backtest, 2026wasam, 58 quals, prior-only)
BACKTEST_REF = {"event": "2026wasam", "n": 58,
                "brier": {"sim_full": 0.1181, "sim_full_no_prior": 0.1587, "epa_pre": 0.1474, "epa": 0.189,
                          "opr": 0.1974},
                "warm": {"n": 48, "sim_full": 0.1229, "sim_full_no_prior": 0.1551, "epa": 0.1893},
                "early": {"n": 10, "sim_full": 0.095, "sim_full_no_prior": 0.1757, "epa": 0.1877}}


def _r(x, nd=2):
    return None if x is None else round(float(x), nd)


def _num(t):
    return int(t[3:]) if t.startswith("frc") else t


def posted(m):
    return m.get("post_time") or (m["time"] + POST_LAG)


def label(m):
    lvl, num, sub = m["order"]
    if lvl == 0:
        return f"Qual {num}", f"Q{num}"
    if lvl == 4:
        return f"Final {sub or num}", f"F{sub or num}"
    return f"Playoff {num}" + (f"-{sub}" if sub and sub > 1 else ""), f"SF{num}"


# ------------------------------------------------------------------------------------------------- as-of view
def asof_view(E, asof, video_lag=VIDEO_LAG):
    """Copy of E where only results posted before `asof` count as played and only their video (after video_lag)
    exists.  Playoff matches whose alliances could not be known yet are dropped."""
    if asof is None:
        return E
    V = dict(E)
    tba = []
    quals_done = all(posted(m) <= asof for m in E["tba"] if m["comp_level"] == "qm")
    for m in E["tba"]:
        if m["comp_level"] != "qm" and not (quals_done and asof >= m["time"] - PLAYOFF_VISIBLE):
            continue
        if m["played"] and posted(m) > asof:
            m = {k: v for k, v in m.items() if k not in ("red", "blue", "red_win")}
            m["played"] = False
        tba.append(m)
    V["tba"] = tba
    V["by_key"] = {m["key"]: m for m in tba}
    vis = {m["key"] for m in tba if m["played"] and posted(m) + video_lag <= asof}
    V["video"] = {"alliance": [b for b in E["video"]["alliance"] if b["match"] in vis],
                  "robot": [r for r in E["video"]["robot"] if r["match"] in vis]}
    return V


# ------------------------------------------------------------------------------------------------- ratings
def _pos_mean(m, sd):
    if sd <= 0:
        return max(m, 0.0)
    z = m / sd
    return m * stats.norm.cdf(z) + sd * stats.norm.pdf(z)


def blend_rating(M, t):
    """{auto, teleop, endgame, total, sd} in points per match (schedule-neutral: won auto half the time; includes a
    third of the alliance's human-player FUEL so a sum over an alliance ~ its expected score without fouls)."""
    th = {c: team_theta(M.comp[c], t) for c in COMPONENTS}
    fuel = {c: _pos_mean(*th[c]) + M.h[c] / 3.0 + (M.comp[c]["beta"] / 6.0 if c != "auto" else 0.0) for c in COMPONENTS}
    cl = M.climb["team"].get(t) or {"auto_l1": M.climb["event_auto"], "end": M.climb["event_end"]}
    auto_tower = 15.0 * cl["auto_l1"]
    end_tower = sum(p * pts for p, pts in zip(cl["end"], (0, 10, 20, 30)))
    auto = fuel["auto"] + auto_tower
    teleop = fuel["trans"] + fuel["active"] + fuel["endgame"]
    sd = math.sqrt(sum(th[c][1] ** 2 for c in COMPONENTS))
    return {"total": _r(auto + teleop + end_tower, 1), "auto": _r(auto, 1), "teleop": _r(teleop, 1),
            "endgame": _r(end_tower, 1), "sd": _r(sd, 1)}


def info_weights(M, t):
    """share of the rating's information from the prior (EPA or event-mean shrinkage), event TBA results and video;
    components weighted by their between-team variance tau^2."""
    W = {"prior": 0.0, "tba": 0.0, "video": 0.0}
    tot = 0.0
    for c in COMPONENTS:
        sh = M.comp[c]["info_shares"].get(t)
        if not sh:
            continue
        w = M.comp[c]["tau2"]
        tot += w
        for k in W:
            W[k] += w * sh[k]
    return {k: _r(v / tot, 3) for k, v in W.items()} if tot else {"prior": 1.0, "tba": 0.0, "video": 0.0}


def display_epa(t, sb, sb_ok, cepa, asof):
    if sb_ok:
        v = EPA.sb_asof(sb, t, asof)
        if v and v.get("total") is not None:
            return {"total": _r(v["total"], 1), "auto": _r(v.get("auto"), 1), "teleop": _r(v.get("teleop"), 1),
                    "endgame": _r(v.get("endgame"), 1), "source": "statbotics"}
    v = cepa.value(t)
    return {"total": _r(v["total"], 1), "auto": _r(v["auto"], 1), "teleop": _r(v["teleop"], 1),
            "endgame": _r(v["endgame"], 1), "source": "local"}


# ------------------------------------------------------------------------------------------------- next match plan
def _zone_xy(E_v, team, zone, alliance):
    """weighted centroid (field metres, TBA frame: blue wall X=0) of the team's counted shots launched in `zone`."""
    xs, ys, ws = [], [], []
    for r in E_v["video"]["robot"]:
        if r["team"] != team or r["w"] <= 0:
            continue
        for o in r.get("origins") or []:
            if o["counted"] and shot_zone(o["xc"], o["yc"]) == zone:
                xs.append(o["xc"]); ys.append(o["yc"]); ws.append(r["w"])
    if not ws:
        return None
    x, y = float(np.average(xs, weights=ws)), float(np.average(ys, weights=ws))
    if alliance == "blue":
        x, y = LEN - x, WID - y
    return [round(x, 2), round(y, 2)]


DROP_FACTORS = {"uncertainty"}          # noise in a queue (strategist review)


def _our_factors(F, us_teams):
    """preview key factors from OUR alliance's point of view: no advice about defending our own robots, no partner
    passing trivia, no generic uncertainty line; weights stay in the JSON but are not points."""
    out = []
    for f in F:
        if f["kind"] in DROP_FACTORS:
            continue
        if f.get("team") in us_teams and f["kind"] in ("defense_target", "defense_resistant", "inactive_behaviour"):
            continue
        out.append(f)
    return out


def next_match_plan(E_v, M, m, team, sims=6000, seed=0, X=None, cov=None, epa_wp=None, auto_bias=None, asof_stamp=None):
    """plan for one of our matches (any remaining match, not only the next).  X/cov/epa_wp/auto_bias feed the queue
    card (matchday.queue_card); without them the card is omitted (old behaviour)."""
    us = "red" if team in m["teams"]["red"] else "blue"
    them = "blue" if us == "red" else "red"
    P = build_preview(E_v, m["key"], asof="prior", sims=sims, seed=seed, M=M)
    red, blue = m["teams"]["red"], m["teams"]["blue"]
    S = simulate(M, red, blue, n=sims, seed=seed + 11, raw=True)
    raw = S["raw"]["alliances"]
    sc = {}
    for a in ("red", "blue"):
        x = raw[a]["score"]
        sc[a] = {"mean": _r(x.mean(), 1), "lo": _r(np.percentile(x, 10), 1), "hi": _r(np.percentile(x, 90), 1)}
    pr = P["prediction"]
    p_red = pr["p_red_win"]
    flip = (lambda p: p) if us == "red" else (lambda p: None if p is None else round(1 - p, 4))
    p_auto_us = P["alliances"][us]["p_win_auto"]
    win_if_won = pr["p_red_win_if_red_wins_auto"] if us == "red" else 1 - pr["p_red_win_if_blue_wins_auto"]
    win_if_lost = pr["p_red_win_if_blue_wins_auto"] if us == "red" else 1 - pr["p_red_win_if_red_wins_auto"]
    prediction = {"win_prob": flip(p_red), "score": sc, "p_win_auto": _r(p_auto_us, 3),
                  "win_prob_if_auto_won": _r(win_if_won, 3), "win_prob_if_auto_lost": _r(win_if_lost, 3),
                  "margin_mean": _r(S["margin"]["mean"] * (1 if us == "red" else -1), 1),
                  "schedule_value_fuel": pr["schedule_value_fuel"], "n_sims": sims}
    robots = {r["team"]: r for r in P["robots"]}
    # ---- defence: their active shifts are our inactive shifts
    p_them_auto = 1 - p_auto_us
    likely = ["shift2", "shift4"] if p_them_auto >= 0.5 else ["shift1", "shift3"]
    other = ["shift1", "shift3"] if likely == ["shift2", "shift4"] else ["shift2", "shift4"]
    ours = [robots[t] for t in m["teams"][us]]
    dfd = MD.pick_defender(ours, X) if X is not None else \
        {"team": min(ours, key=lambda r: r["expected_fuel"]["total"])["team"], "why": "lowest expected FUEL on our alliance"}
    defend = []
    for t in m["teams"][them]:
        D = M.robot["team"].get(t) or {}
        rr = D.get("rate_retained_defended")
        if rr and not rr.get("prior_only") and rr["n_eff"] >= 2:
            ret, basis = rr["v"], f"measured on {rr['n_eff']:.1f} weighted defended matches"
        else:
            ret, basis = GENERIC_RETAINED, "assumption: defended robots keep ~75% of their shot rate"
        r = robots[t]
        act = sum(r["expected_fuel"]["by_period"][s] for s in SHIFTS)
        gain = act * max(0.0, 1.0 - min(ret, 1.0)) * ENGAGE
        tz = r.get("top_zones")
        where = None
        ztxt = "no verified shot locations yet"
        if tz and tz["w_made"] >= 3:
            z = tz["zones"][0]
            where = {"zone": z["zone"], "share": z["share"], "xy": _zone_xy(E_v, t, z["zone"], them),
                     "zone_ours": MD.their_view_zone(z["zone"])}
            ztxt = f"{z['share'] * 100:.0f}% of its tracked made FUEL comes from {MD.their_view_zone(z['zone'])}"
        defend.append({
            "target": t, "when": likely, "when_alt": other, "p_when": _r(max(p_them_auto, 1 - p_them_auto), 3),
            "where": where, "expected_gain_fuel": _r(gain, 1), "active_fuel": _r(act, 1),
            "rate_retained": _r(ret, 2), "basis": "measured" if basis.startswith("measured") else "prior",
            "why": (f"{_num(t)} is expected to score ~{act:.0f} FUEL in its live shifts ({' + '.join(likely)} "
                    f"in {max(p_them_auto, 1 - p_them_auto) * 100:.0f}% of sims). {basis[0].upper() + basis[1:]}; with "
                    f"~{ENGAGE * 100:.0f}% engagement that is ~{gain:.0f} FUEL denied. {ztxt[0].upper() + ztxt[1:]}. "
                    f"Their live shifts are our inactive shifts, so the defender gives up little counted FUEL.")})
    defend.sort(key=lambda d: -(d["expected_gain_fuel"] or 0))
    # ---- auto
    matchups = []
    for t in red + blue:
        r = robots[t]
        rt = (r["auto"] or {}).get("routines")
        exp = r["auto"]["expected_fuel"]
        if rt and rt["routines"]:
            key = "freq_at_station" if rt["conditioned_on_station"] else "freq"
            top = max(rt["routines"], key=lambda x: x.get(key) or 0)
            p = top.get(key) or top["freq"]
            note = (f"{top['name']} in {top['n']} of {rt['n_matches']} clustered autos"
                    + (f" (station {r['station']})" if rt["conditioned_on_station"] else " (never seen at this station)")
                    + (f", ~{top['auto_made_mean']:.0f} made" if top.get("auto_made_mean") is not None else "")
                    + f"; model expects ~{exp:.0f} auto FUEL")
            matchups.append({"team": t, "alliance": r["alliance"], "station": r["station"],
                             "likely_routine": top["name"], "routine_id": top["routine"], "p": _r(p, 2),
                             "expected_fuel": _r(exp, 1), "note": note})
        else:
            matchups.append({"team": t, "alliance": r["alliance"], "station": r["station"], "likely_routine": None,
                             "routine_id": None, "p": None, "expected_fuel": _r(exp, 1),
                             "note": f"no clustered autos yet; model expects ~{exp:.0f} auto FUEL"})
    auto = {"our_expected": _r(P["alliances"][us]["fuel_by_period"]["auto"], 1),
            "their_expected": _r(P["alliances"][them]["fuel_by_period"]["auto"], 1),
            "p_win_auto": _r(p_auto_us, 3), "matchups": matchups,
            "note": (f"The auto winner's hub is live in shifts 2 and 4; at this event that schedule is worth about "
                     f"{pr['schedule_value_fuel']['mean']:.0f} ± {pr['schedule_value_fuel']['sd']:.0f} teleop FUEL. "
                     f"Win prob {win_if_won * 100:.0f}% if we win auto vs {win_if_lost * 100:.0f}% if we lose it.")}
    endgame = []
    for t in red + blue:
        cl = robots[t]["climb"]
        endgame.append({"team": t, "alliance": robots[t]["alliance"],
                        "p_climb": {k: cl["end"][k] for k in ("L1", "L2", "L3")}, "p_any": _r(1 - (cl["end"]["L0"] or 0), 3),
                        "auto_l1": cl["auto_l1"], "n_obs": cl["n_obs"], "expected_pts": cl["expected_pts"]})
    lbl, short = label(m)
    nm = {"key": m["key"], "label": lbl, "short": short, "time": m["time"], "alliance": us,
          "partners": [t for t in m["teams"][us] if t != team], "opponents": list(m["teams"][them]),
          "teams": {"red": red, "blue": blue},
          "prediction": prediction,
          "plan": {"defend": defend, "suggested_defender": dfd,
                   "auto": auto, "endgame": endgame, "key_factors": _our_factors(P["key_factors"], set(m["teams"][us]))},
          "preview_url": f"preview/{m['key']}.json"}
    if asof_stamp:
        nm["as_of"] = asof_stamp
    if X is not None:
        q = MD.queue_card(nm, P, raw, X, cov or {}, epa_wp, auto_bias, E_v, team)
        nm["queue"] = q
        prediction["win_prob_epa"] = q["win_prob_epa"]
        prediction["models_disagree"] = q["models_disagree"]
        prediction["rp"] = {"us": q["rp"]["us"], "them": q["rp"]["them"]}
    return nm, P


# ------------------------------------------------------------------------------------------------- our team levers
LEVERS = [  # name, source, unit, lower_better, label
    ("cycle_s", "desc", "s", True, "seconds between volleys"),
    ("accuracy", "desc", "frac", False, "made / (made + missed)"),
    ("fire_rate", "desc", "balls/s", False, "balls per second inside a volley"),
    ("volley_size", "desc", "balls", False, "balls per volley"),
    ("auto_fuel", "model", "fuel", False, "counted auto FUEL per match (model)"),
    ("teleop_fuel", "model", "fuel", False, "counted teleop FUEL per match (model, schedule-neutral)"),
    ("on_move_rate", "desc", "frac", False, "share of shots taken while moving"),
    ("climb", "climb", "frac", False, "P(endgame climb, any level)"),
]


def _lever_values(M, t, name, src):
    if src == "desc":
        d = (M.robot["team"].get(t) or {}).get(name)
        if not d:
            return None, None
        return (None if d["prior_only"] else d["v"]), d
    if src == "model":
        if name == "auto_fuel":
            m, sd = team_theta(M.comp["auto"], t)
            return _pos_mean(m, sd) + M.h["auto"] / 3.0, None
        v = 0.0
        for c in ("trans", "active", "endgame"):
            m, sd = team_theta(M.comp[c], t)
            v += _pos_mean(m, sd) + M.h[c] / 3.0 + M.comp[c]["beta"] / 6.0
        return v, None
    cl = M.climb["team"].get(t)
    return (None if not cl else 1 - cl["end"][0]), None


FLOOR = {"s": 0.5, "frac": 0.05, "balls/s": 0.25, "balls": 0.5, "fuel": 2.0}


def _points_gap(name, ours, target, our_fuel):
    """rough points per match gained if our value moved to `target`, all else equal (None when not convertible)."""
    if ours is None or target is None or our_fuel is None:
        return None
    if name in ("auto_fuel", "teleop_fuel"):
        return target - ours
    if name == "climb":
        return (target - ours) * 10.0                 # an L1 climb is 10 points (L2 20, L3 30)
    if name == "accuracy" and ours > 0:
        return our_fuel * (target / ours - 1.0)       # same attempts, more of them made
    if name == "volley_size" and ours > 0:
        return our_fuel * (target / ours - 1.0)       # same number of cycles, more per cycle
    if name == "cycle_s" and target > 0:
        return our_fuel * (ours / target - 1.0)       # more cycles in the same time
    return None


def levers(M, team, teams):
    """our values vs the event; gap_rank 1 = biggest gap to close, ranked by a rough points-per-match impact where one
    can be computed (gap_pts), then by the normalised gap (gap_z)."""
    our_fuel = None
    if team in M.comp["auto"]["team_index"]:
        our_fuel = _lever_values(M, team, "auto_fuel", "model")[0] + _lever_values(M, team, "teleop_fuel", "model")[0]
    out = []
    for name, src, unit, lower, desc in LEVERS:
        vals = {t: _lever_values(M, t, name, src)[0] for t in teams}
        have = {t: v for t, v in vals.items() if v is not None}
        ours = vals.get(team)
        if len(have) < 2:
            continue
        arr = np.array(list(have.values()))
        best_t = (min if lower else max)(have, key=have.get)
        best = have[best_t]
        med = float(np.median(arr))
        spread = max(float(np.percentile(arr, 75) - np.percentile(arr, 25)), float(arr.std()), FLOOR.get(unit, 1e-6))
        gap = None if ours is None else max(0.0, (ours - best) if lower else (best - ours))
        pct = None if ours is None else float(np.mean([(v >= ours) if lower else (v <= ours) for v in arr]))
        d = (M.robot["team"].get(team) or {}).get(name) if src == "desc" else None
        gp = _points_gap(name, ours, best, our_fuel)
        gpm = _points_gap(name, ours, med, our_fuel)
        out.append({"name": name, "desc": desc, "unit": unit, "lower_better": lower, "ours": _r(ours, 3),
                    "event_best": _r(best, 3), "event_best_team": best_t, "event_median": _r(med, 3),
                    "gap": _r(gap, 3), "gap_z": _r(gap / spread, 2) if gap is not None else None,
                    "gap_pts": _r(max(gp, 0.0), 1) if gp is not None else None,
                    "gap_pts_median": _r(max(gpm, 0.0), 1) if gpm is not None else None,
                    "percentile": _r(pct, 2), "n_teams": len(have),
                    "n_eff": d["n_eff"] if d else None, "basis": "video" if src == "desc" else
                    ("model (EPA prior + TBA + video)" if src == "model" else "TBA tower levels + earlier events")})
    ranked = sorted([l for l in out if l["gap_z"] is not None],
                    key=lambda l: (l["gap_pts"] is None, -(l["gap_pts"] if l["gap_pts"] is not None else l["gap_z"])))
    for i, l in enumerate(ranked):
        l["gap_rank"] = i + 1
    for l in out:
        l.setdefault("gap_rank", None)
    return out


def _fuel_made(E_v, key, team):
    for r in E_v["video"]["robot"]:
        if r["match"] == key and r["team"] == team and r.get("by_period") and r["w"] >= 0.5:
            return sum(r["by_period"][p]["counted"] for p in PERIODS)
    return None


# ------------------------------------------------------------------------------------------------- build
class Context:
    """Everything that does not depend on asof (loaded once; reused by snapshots)."""

    def __init__(self, data_dir, event=None, offline=False, use_statbotics=True, log=None):
        self.log = log or (lambda *a: None)
        self.E = load_event(data_dir, event)
        self.data_dir = self.E["paths"]["root"]
        E = self.E
        self.sb = EPA.fetch_statbotics(self.data_dir, E["event"], offline=offline, log=self.log) if use_statbotics \
            else {"team_events": {}, "team_matches": {}, "ok": False, "info": {}}
        self.prior = EPA.pre_event_prior(self.data_dir, E, sb=self.sb, offline=offline, log=self.log)
        start = E["info"].get("start_date") or "9999"
        self.prior_events = EPA.fetch_prior_events(self.data_dir, E["event"], E["teams"], start,
                                                   int(E["info"].get("year") or 2026), offline=True)["matches"]
        self.nick = {}
        tp = os.path.join(self.data_dir, "tba", f"event__{E['event']}__teams__simple.json")
        if os.path.exists(tp):
            raw = json.load(open(tp))
            for t in (raw.get("data", raw) if isinstance(raw, dict) else raw):
                self.nick[t["key"]] = t.get("nickname")
        self.cov_matches = MD.load_coverage_matches(E["paths"].get("site"))
        self._bt_rows = None

    def bt_rows(self):
        """prequential predictions of every played qual (asof-independent; footers subset them by post time)."""
        if self._bt_rows is None:
            cp = os.path.join(self.data_dir, "cache", "strategy", f"backtest_{self.E['event']}.json")
            self._bt_rows = MD.backtest_rows(self, cache_path=cp, log=self.log)
        return self._bt_rows


def build(ctx, team=DEFAULT_TEAM, asof=None, sims_rank=2000, sims_match=6000, seed=0, previews=None):
    """-> (dashboard dict, {match_key: preview dict} for previews requested)."""
    t0 = time.time()
    E = ctx.E
    V = asof_view(E, asof)
    now = asof if asof is not None else time.time()
    M = fit_event_model(V, None, use_video=True, epa_prior=ctx.prior)
    played = [m for m in V["tba"] if m["played"]]
    quals = [m for m in V["tba"] if m["comp_level"] == "qm"]
    qp = [m for m in quals if m["played"]]
    ql = [m for m in quals if not m["played"]]
    # ---- EPA for display
    cepa = EPA.local_epa(V, played, sorted(played, key=lambda m: m["time"]), prior_events=ctx.prior_events)
    # ---- projection
    proj, per_match, table = RANK.project(M, V, qp, ql, n=sims_rank, seed=seed)
    ranks_now = RANK.current_ranks(table, E["teams"])
    # ---- ratings
    vid_matches = {}
    idc = {}
    for b in V["video"]["alliance"]:
        if b["match"] in M.video_matches:
            for t in b["teams"]:
                vid_matches.setdefault(t, set()).add(b["match"])
    for r in V["video"]["robot"]:
        idc.setdefault(r["team"], []).append(r["ident"])
    cov = MD.coverage_asof(ctx.cov_matches, V, asof, VIDEO_LAG)
    bt_rows = ctx.bt_rows()
    ratings = []
    for t in E["teams"]:
        w = info_weights(M, t)
        s = table[t]
        pr = (ctx.prior or {}).get("team", {}).get(t)
        ratings.append({
            "team": t, "number": _num(t), "nickname": ctx.nick.get(t), "rank": ranks_now.get(t),
            "record": f"{s['w']}-{s['l']}-{s['t']}", "rp": s["rp"], "matches_played": s["n"],
            "epa": display_epa(t, ctx.sb, ctx.sb.get("ok"), cepa, asof),
            "blend": blend_rating(M, t),
            "video_weight": w["video"], "weights": w,
            "prior_source": (f"epa-{pr['source']}" if pr else "event-mean"),
            "prior_events": (pr or {}).get("events", []),
            "video_matches": len(vid_matches.get(t, ())),
            "id_conf": _r(np.mean(idc[t]), 2) if idc.get(t) else None,
            "coverage": {k: v for k, v in cov[t].items() if k != "keys_full"},
            "projection": proj[t]})
        rr = ratings[-1]
        if rr["epa"]["total"] is not None and rr["blend"]["total"] is not None:
            diff = rr["blend"]["total"] - rr["epa"]["total"]
            rr["disagree"] = {"diff": _r(diff, 1), "flag": abs(diff) >= max(DISAGREE_RATING, 1.5 * (rr["blend"]["sd"] or 0))}
    ratings.sort(key=lambda r: (r["rank"] or 999, -(r["blend"]["total"] or 0)))
    # ---- our next match
    mine = sorted([m for m in V["tba"] if team in m["teams"]["red"] + m["teams"]["blue"]],
                  key=lambda m: (m["time"], m["order"]))
    upcoming = [m for m in mine if not m["played"]]
    nxt = None
    prev_out = {}
    plans = []
    X = MD.event_extras(V, cov, {t: set(c["keys_full"]) for t, c in cov.items()})
    s_epa = MD.epa_sd(bt_rows, asof)
    bt_asof = MD.backtest_asof(bt_rows, asof)
    qp_sorted = sorted(qp, key=lambda m: m["time"])
    stamp = {"ts": int(now), "asof": int(asof) if asof is not None else None, "quals_played": len(qp),
             "last_result": label(qp_sorted[-1])[1] if qp_sorted else None}
    for i, um in enumerate(upcoming):
        ce = [sum(cepa.value(t)["total"] for t in um["teams"][a]) for a in ("red", "blue")]
        p_epa_red = float(stats.norm.cdf((ce[0] - ce[1]) / s_epa))
        us_ = "red" if team in um["teams"]["red"] else "blue"
        pl, P = next_match_plan(V, M, um, team, sims=sims_match if i == 0 else max(2000, sims_match // 2), seed=seed,
                                X=X, cov=cov, epa_wp=p_epa_red if us_ == "red" else 1 - p_epa_red,
                                auto_bias=bt_asof.get("auto_bias_recent"), asof_stamp=stamp)
        plans.append(pl)
        prev_out[um["key"]] = P
    if plans:
        nxt = plans[0]
    # ---- schedule + trend (pre-match win prob for played matches from a fit with only earlier matches)
    sched, trend = [], []
    pm_by_key = {d["key"]: d for d in per_match}
    for m in mine:
        us = "red" if team in m["teams"]["red"] else "blue"
        them = "blue" if us == "red" else "red"
        lbl, short = label(m)
        if m["played"]:
            Mb = fit_event_model(V, m["time"], use_video=True, epa_prior=ctx.prior)
            S = simulate(Mb, m["teams"]["red"], m["teams"]["blue"], n=2000, seed=seed + 3)
            wp = S["p_red_win"] if us == "red" else 1 - S["p_red_win"]
            res = {"red": m["red"]["score"], "blue": m["blue"]["score"],
                   "won": m[us]["score"] > m[them]["score"], "tie": m[us]["score"] == m[them]["score"],
                   "rp": m[us].get("rp"), "bonus": m[us].get("bonus"),
                   "rp_them": m[them].get("rp"), "bonus_them": m[them].get("bonus")}
            Ma = fit_event_model(V, m["time"] + 1, use_video=True, epa_prior=ctx.prior)
            trend.append({"match": m["key"], "short": short, "time": m["time"], "blend_total": blend_rating(Ma, team)["total"],
                          "blend_sd": blend_rating(Ma, team)["sd"],
                          "fuel_made": _fuel_made(V, m["key"], team), "alliance_fuel": m[us]["fuel_count"],
                          "won": res["won"]})
        else:
            pl = next((p for p in plans if p["key"] == m["key"]), None)
            if pl:
                wp = pl["prediction"]["win_prob"]       # one win % per match everywhere
            else:
                S = simulate(M, m["teams"]["red"], m["teams"]["blue"], n=2000, seed=seed + 5)
                wp = S["p_red_win"] if us == "red" else 1 - S["p_red_win"]
            res = None
        pm = pm_by_key.get(m["key"])
        if not m["played"]:
            pl = next((p for p in plans if p["key"] == m["key"]), None)
            if pl and pl["prediction"].get("rp"):
                pm = {us: pl["prediction"]["rp"]["us"], them: pl["prediction"]["rp"]["them"]}
        sched.append({"key": m["key"], "label": lbl, "short": short, "comp_level": m["comp_level"], "time": m["time"],
                      "played": m["played"], "alliance": us, "partners": [t for t in m["teams"][us] if t != team],
                      "opponents": list(m["teams"][them]), "win_prob": _r(wp, 3), "result": res,
                      "bonus_probs": ({k: _r(v, 3) for k, v in pm[us].items() if not isinstance(v, dict)} if pm else None),
                      "bonus_probs_them": ({k: _r(v, 3) for k, v in pm[them].items() if not isinstance(v, dict)}
                                           if pm else None),
                      "preview_url": f"preview/{m['key']}.json",
                      "analyze_url": f"#/{E['event']}/a/{m['key']}" if m["played"] else None})
    our = {"team": team, "levers": levers(M, team, E["teams"]), "trend": trend,
           "blend": blend_rating(M, team), "projection": proj.get(team)}
    # ---- event block
    allq = [m for m in E["tba"] if m["comp_level"] == "qm"]
    times = [m["time"] for m in E["tba"]]
    info = E["info"]
    out = {
        "schema": SCHEMA, "generated": int(time.time()),
        "event": {"key": E["event"], "name": info.get("name"), "short_name": info.get("short_name"),
                  "start": min(times), "end": max(times), "start_date": info.get("start_date"),
                  "end_date": info.get("end_date"), "timezone": info.get("timezone"),
                  "now": int(now), "asof": int(asof) if asof is not None else None,
                  "quals_played": len(qp), "quals_total": len(allq),
                  "playoffs": any(m["comp_level"] != "qm" for m in V["tba"])},
        "team": team, "next_match": nxt, "plans": plans, "ratings": ratings, "our_team": our, "schedule": sched,
        "model": {"backtest": BACKTEST_REF, "backtest_asof": bt_asof,
                  "epa_source": {"display": "statbotics" if ctx.sb.get("ok") else "local",
                                 "prior": "local (earlier events this season, TBA)" if (ctx.prior or {}).get("sources", {}).get("local_events") else
                                 ("statbotics" if ctx.prior else "none (event-mean shrinkage)"),
                                 "prior_events": (ctx.prior or {}).get("sources", {}).get("local_events", []),
                                 "prior_teams": (ctx.prior or {}).get("n_teams", 0),
                                 "statbotics_error": None if ctx.sb.get("ok") else
                                 next((v.get("error") for v in ctx.sb.get("info", {}).values() if v.get("error")), "unavailable")},
                  "n_matches_used": M.n_matches, "video_matches_used": len(M.video_matches),
                  "sims": {"rank": sims_rank, "match": sims_match},
                  "notes": [
                      "EPA prior = each team's rating from its earlier 2026 events (same robot model, TBA only), with "
                      "between-event drift; event TBA results and Cyber-Scout video update it.",
                      "video_weight = share of a team's rating information that comes from tracked video "
                      "(precision decomposition; 0 = EPA prior + TBA results only).",
                      "Rank projection: remaining quals simulated with team strengths drawn once per simulated event; "
                      "RP = 3 win / 1 tie + ENERGIZED (>=100 FUEL) + SUPERCHARGED (>=360 FUEL) + TRAVERSAL (>=50 tower pts); "
                      "TBA tiebreakers avg match pts (no fouls), avg auto FUEL, avg tower pts.",
                      f"Defence gains assume ~{ENGAGE * 100:.0f}% engagement during the target's active shifts; "
                      "rate retained is measured only where the target has been defended on video (basis field).",
                      "Not modelled in the simulator: defence/congestion interactions, per-team fouls."],
                  "build_s": None}}
    out["model"]["build_s"] = round(time.time() - t0, 2)
    if previews:
        for m in mine if previews == "team" else [m for m in V["tba"]]:
            if m["key"] in prev_out:
                continue
            prev_out[m["key"]] = build_preview(V, m["key"], asof="prior", sims=4000, seed=seed,
                                               epa_prior=ctx.prior)
    return out, prev_out


# ------------------------------------------------------------------------------------------------- snapshots
def _tz(E):
    try:
        import zoneinfo
        return zoneinfo.ZoneInfo(E["info"].get("timezone") or "UTC")
    except Exception:            # no tzdata in the image: labels in UTC
        return datetime.timezone.utc


def snapshot_times(E, step=SNAP_STEP):
    """every `step` s from the first match (rounded down) to the last posted result (+ one step) of each event day;
    days = runs of played matches separated by > 6 h (timezone-independent)."""
    tz = _tz(E)
    ms = sorted((m for m in E["tba"] if m["played"]), key=lambda m: m["time"])
    days, cur = [], []
    for m in ms:
        if cur and m["time"] - cur[-1]["time"] > 6 * 3600:
            days.append(cur)
            cur = []
        cur.append(m)
    if cur:
        days.append(cur)
    out = []
    for ms in days:
        t0 = min(m["time"] for m in ms)
        t1 = max(posted(m) for m in ms)
        ts = int(t0 // step * step)
        while ts <= t1 + step:
            out.append(ts)
            ts += step
    return out, tz


def _dump(obj, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, separators=(",", ":"), default=float)
    os.replace(tmp, path)
    return os.path.getsize(path)


def site_build(data_dir, event, team=DEFAULT_TEAM, out_dir=None, previews="team", snapshots=False, offline=False,
               log=None):
    """Entry point for the worker's build_site(): writes dashboard.json, dashboard.<team>.json, preview/<match>.json
    (and, with snapshots=True, any missing dash/<ts>.json + dash/index.json).  Progress goes to stderr."""
    argv = [data_dir, event, "--team", team, "--site", "--previews", previews]
    if out_dir:
        argv += ["--out-dir", out_dir]
    if snapshots:
        argv += ["--snapshots", "--only-missing"]
    if offline:
        argv += ["--offline"]
    return main(argv)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir")
    ap.add_argument("event", nargs="?")
    ap.add_argument("--team", default=DEFAULT_TEAM)
    ap.add_argument("--teams", help="extra perspectives for --site: comma list or 'all'")
    ap.add_argument("--asof", type=float)
    ap.add_argument("--out")
    ap.add_argument("--site", action="store_true", help="write into DATA_DIR/site/api/<ev>/ (or --out-dir)")
    ap.add_argument("--out-dir")
    ap.add_argument("--snapshots", action="store_true")
    ap.add_argument("--only-missing", action="store_true", help="snapshots: skip dash/<ts>.json files that exist "
                    "(past snapshots only change when video is reprocessed); the last one is always rebuilt")
    ap.add_argument("--previews", choices=["none", "team", "all"], default=None)
    ap.add_argument("--sims-rank", type=int, default=2000)
    ap.add_argument("--sims-match", type=int, default=6000)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--no-statbotics", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    log = (lambda *x: print(*x, file=sys.stderr)) if a.verbose else None
    T0 = time.time()
    ctx = Context(a.data_dir, a.event, offline=a.offline, use_statbotics=not a.no_statbotics, log=log)
    ev = ctx.E["event"]
    team = a.team if a.team.startswith("frc") else f"frc{a.team}"
    if not a.site and not a.snapshots:
        D, _ = build(ctx, team, a.asof, a.sims_rank, a.sims_match)
        s = json.dumps(D, separators=(",", ":"), default=float)
        if a.out:
            open(a.out, "w").write(s)
            print(f"wrote {a.out} ({len(s) / 1024:.0f} KB, {time.time() - T0:.1f}s)", file=sys.stderr)
        else:
            print(s)
        return
    out_dir = a.out_dir or os.path.join(ctx.data_dir, "site", "api", ev)
    if a.site:
        previews = a.previews or "team"
        D, P = build(ctx, team, a.asof, a.sims_rank, a.sims_match, previews=None if previews == "none" else previews)
        n1 = _dump(D, os.path.join(out_dir, "dashboard.json"))
        _dump(D, os.path.join(out_dir, f"dashboard.{team}.json"))
        for k, p in P.items():
            _dump(p, os.path.join(out_dir, "preview", f"{k}.json"))
        extra = []
        if a.teams:
            extra = ctx.E["teams"] if a.teams == "all" else [t if t.startswith("frc") else f"frc{t}" for t in a.teams.split(",")]
        for t in extra:
            if t == team:
                continue
            Dt, _ = build(ctx, t, a.asof, a.sims_rank, a.sims_match)
            _dump(Dt, os.path.join(out_dir, f"dashboard.{t}.json"))
        print(f"dashboard.json {n1 / 1024:.0f} KB, {len(P)} previews, {len(extra)} extra perspectives "
              f"({time.time() - T0:.1f}s)", file=sys.stderr)
    if a.snapshots:
        ts_list, tz = snapshot_times(ctx.E)
        index = []
        for i, ts in enumerate(ts_list):
            t1 = time.time()
            fp = os.path.join(out_dir, "dash", f"{ts}.json")
            if ts > time.time():
                break
            if a.only_missing and os.path.exists(fp) and i < len(ts_list) - 1:
                try:
                    old = json.load(open(fp))
                    index.append({"ts": ts, "label": datetime.datetime.fromtimestamp(ts, tz).strftime("%a %H:%M"),
                                  "quals_played": old["event"]["quals_played"]})
                    continue
                except Exception:
                    pass
            D, _ = build(ctx, team, ts, a.sims_rank, a.sims_match, seed=0)
            for r in D["schedule"]:
                r["preview_url"] = None        # previews on disk reflect current data, not this asof
            for pl in D.get("plans") or []:
                pl["preview_url"] = None       # the plan itself (stamped as_of) is the snapshot's record
            if D["next_match"]:
                D["next_match"]["preview_url"] = None
            n = _dump(D, fp)
            lbl = datetime.datetime.fromtimestamp(ts, tz).strftime("%a %H:%M")
            index.append({"ts": ts, "label": lbl, "quals_played": D["event"]["quals_played"]})
            print(f"  snapshot {lbl} ({ts}): {n / 1024:.0f} KB, {time.time() - t1:.1f}s", file=sys.stderr)
        _dump(index, os.path.join(out_dir, "dash", "index.json"))
        print(f"{len(index)} snapshots ({time.time() - T0:.1f}s total)", file=sys.stderr)


if __name__ == "__main__":
    main()
