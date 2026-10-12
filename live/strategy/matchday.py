"""Match-day helpers for the dashboard (docs/STRATEGIST_REVIEW.md, "Agent M"):

  * coverage_asof    -- verified-video coverage per team as of a replay time (shared rule: live/coverage.py)
  * backtest rows    -- prequential predictions for every played qual (blend, blend without video, local EPA),
                        computed once per Context and cached on disk; the dashboard footer summarises only the
                        matches whose result was posted before `asof` (honest as-of credibility)
  * event_extras     -- event-level facts the queue card needs (bonus RPs ever achieved, fouls given away, defence
                        effect, who defends, recent auto bias)
  * queue_card       -- the six-line card for one match from our alliance's point of view (+ goal mode)

Nothing here changes the robot model or the simulator; it only reads them.
"""
import hashlib
import json
import math
import os

import numpy as np
from scipy import stats

from . import epa as EPA
from .baselines import S_PRIOR, S_PRIOR_N
from .data import LEN, PERIODS, WID, shot_zone
from . import model as MODEL
from .model import fit_event_model
from .sim import simulate

try:
    from live import coverage as COV
except ImportError:          # pragma: no cover (run from inside live/)
    import coverage as COV

FEED_CONV = 0.5          # assumption: share of a feeder's passes that become counted FUEL for its partners
INACTIVE_MIN = 50 / 60   # our two inactive shifts last 2 x 25 s
DEF_MIN_S = 10           # a robot "defended" in a match when its defence time is at least this
DISAGREE_PTS = 0.15      # blend vs EPA win prob: "models disagree" chip above 15 points
GOAL_LO, GOAL_HI = 0.10, 0.90


def disp(p):
    """the dashboard's display of a probability (dash.js wp5): '<5%', '>95%', else 5% steps -> int percent or None."""
    if p is None:
        return None
    return 4 if p < 0.05 else 96 if p > 0.95 else int(round(p * 20) * 5)


def _r(x, nd=2):
    return None if x is None else round(float(x), nd)


def _short(key):
    s = (key or "").split("_")[-1]
    return ("Q" + s[2:]) if s.startswith("qm") else s.upper()


def _num(t):
    return t[3:] if t.startswith("frc") else t


def posted(m, post_lag=300):
    return m.get("post_time") or (m["time"] + post_lag)


# ------------------------------------------------------------------------------------------------- coverage
def load_coverage_matches(site_dir):
    """compact {key, identity.method, tracks[team, team_conf]} for every site match JSON (once per Context)."""
    out = []
    if not site_dir:
        return out
    for m in COV.load_matches(os.path.join(site_dir, "m")):
        if not m.get("key"):
            continue                                   # *.fuel.json companions
        out.append({"key": m["key"], "identity": {"method": (m.get("identity") or {}).get("method")},
                    "tracks": [{"team": t.get("team"), "team_conf": t.get("team_conf")} for t in m.get("tracks") or []]})
    return out


def coverage_asof(cov_matches, V, asof, video_lag):
    """{team: {"n", "of", "keys": [Q..], "last": "Q..", "tier", "label"}} for every team; only matches whose video
    could exist at `asof` (posted + video_lag), in play order."""
    by = V["by_key"]
    vis = []
    for m in cov_matches:
        t = by.get(m["key"])
        if not t or not t["played"] or t["comp_level"] != "qm":     # quals only, like scout.json
            continue
        if asof is not None and posted(t) + video_lag > asof:
            continue
        vis.append((t["time"], m))
    vis.sort(key=lambda x: x[0])
    played = {}
    for m in V["tba"]:
        if m["played"] and m["comp_level"] == "qm":
            for tk in m["teams"]["red"] + m["teams"]["blue"]:
                played[tk] = played.get(tk, 0) + 1
    cov = COV.team_coverage([m for _, m in vis], played)
    out = {}
    for t in V["teams"]:
        c = cov.get(t) or {"n": 0, "keys": [], "last": None, "of": played.get(t), "tier": COV.tier(0)}
        out[t] = {"n": c["n"], "of": c.get("of"), "keys": [_short(k) for k in c["keys"]],
                  "last": _short(c["last"]) if c.get("last") else None, "tier": c["tier"],
                  "label": COV.label(c, short=_short), "keys_full": list(c["keys"])}
    return out


# ------------------------------------------------------------------------------------------------- as-of backtest
def backtest_rows(ctx, sims=1500, cache_path=None, log=None):
    """One prequential prediction per played qual (model fitted on matches before it).  Cached by
    (n prior matches, n prior video blocks) so a rebuild only refits matches whose inputs changed."""
    E = ctx.E
    cache = {}
    if cache_path and os.path.exists(cache_path):
        try:
            cache = {r["key"]: r for r in json.load(open(cache_path))}
        except Exception:
            cache = {}
    played = sorted([m for m in E["tba"] if m["played"]], key=lambda m: m["time"])
    quals = [m for m in played if m["comp_level"] == "qm"]
    # cache key: everything a prequential fit depends on -- prior matches, the video content of those matches and the
    # model's video/trend/shrinkage settings (a re-track or a setting change invalidates exactly the affected rows)
    vdig = {}
    for b in E["video"]["alliance"]:
        h = hashlib.sha1(json.dumps([b.get("v2"), b.get("recon"), {c: {k: v.get(k) for k in ("cred", "unc", "hp_raw", "tba",
                         "coverage", "y", "usable")} for c, v in b["comp"].items()}], sort_keys=True, default=float).encode())
        vdig[b["match"]] = hashlib.sha1((vdig.get(b["match"], "") + h.hexdigest()).encode()).hexdigest()
    cfg = json.dumps([MODEL.VIDEO, sorted(MODEL.TREND), MODEL.SHRINK_MULT, sims], sort_keys=True, default=str)
    cepa = EPA.local_epa(E, [], [], prior_events=ctx.prior_events)
    done = set()
    rows = []
    for i, m in enumerate(quals):
        prior = [p for p in played if p["time"] < m["time"]]
        for p in prior:
            if p["key"] not in done:
                cepa.update(p)
                done.add(p["key"])
        sig = hashlib.sha1((cfg + "|".join(f"{p['key']}:{vdig.get(p['key'], '')}" for p in prior)).encode()).hexdigest()
        c = cache.get(m["key"])
        if c and c.get("sig") == sig and c.get("post") == posted(m):
            rows.append(c)
            continue
        red, blue = m["teams"]["red"], m["teams"]["blue"]
        ce = [sum(cepa.value(t)["total"] for t in tl) for tl in (red, blue)]
        Mv = fit_event_model(E, m["time"], use_video=True, epa_prior=ctx.prior)
        sv = simulate(Mv, red, blue, n=sims, seed=1000 + i)
        Mt = fit_event_model(E, m["time"], use_video=False, epa_prior=ctx.prior)
        st = simulate(Mt, red, blue, n=sims, seed=1000 + i)
        rows.append({"key": m["key"], "short": _short(m["key"]), "time": m["time"], "post": posted(m), "sig": sig,
                     "y": m["red_win"], "p_blend": sv["p_red_win"], "p_novideo": st["p_red_win"],
                     "epa_margin": _r(ce[0] - ce[1], 2), "margin": m["red"]["score"] - m["blue"]["score"],
                     "auto_pred": {a: sv["alliances"][a]["fuel_by_period_mean"]["auto"] for a in ("red", "blue")},
                     "auto_act": {a: m[a]["comp"]["auto"] for a in ("red", "blue")},
                     "n_video": len(Mv.video_matches)})
        if log:
            log(f"  backtest {m['key']}: blend {sv['p_red_win']:.2f} no-video {st['p_red_win']:.2f}")
    if cache_path:
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            tmp = cache_path + ".tmp"
            json.dump(rows, open(tmp, "w"), default=float)
            os.replace(tmp, cache_path)
        except OSError:
            pass
    # prequential EPA win prob (s from earlier out-of-sample margin errors, as baselines.Prequential)
    sq = []
    for r in rows:
        s = math.sqrt((S_PRIOR_N * S_PRIOR ** 2 + sum(sq)) / (S_PRIOR_N + len(sq)))
        r["p_epa"] = float(stats.norm.cdf(r["epa_margin"] / s))
        r["epa_s"] = s
        sq.append((r["margin"] - r["epa_margin"]) ** 2)
    return rows


def epa_sd(rows, asof):
    """prequential EPA margin sd using only results posted before asof."""
    sq = [(r["margin"] - r["epa_margin"]) ** 2 for r in rows if asof is None or r["post"] <= asof]
    return math.sqrt((S_PRIOR_N * S_PRIOR ** 2 + sum(sq)) / (S_PRIOR_N + len(sq)))


def _boot(d, nb=2000, seed=0):
    if len(d) < 2:
        return None
    rng = np.random.default_rng(seed)
    bs = rng.choice(d, size=(nb, len(d)), replace=True).mean(1)
    return {"mean": _r(d.mean(), 4), "ci90": [_r(np.percentile(bs, 5), 4), _r(np.percentile(bs, 95), 4)]}


def backtest_asof(rows, asof, min_n=8):
    rs = [r for r in rows if asof is None or r["post"] <= asof]
    out = {"n": len(rs), "through": rs[-1]["short"] if rs else None, "asof": asof, "min_n": min_n,
           "what": "every qual whose result was posted before this time, each predicted from the matches before it"}
    if not rs:
        return out
    y = np.array([r["y"] for r in rs], float)
    P = {k: np.array([r[k] for r in rs], float) for k in ("p_blend", "p_novideo", "p_epa")}
    br = {k: (P[k] - y) ** 2 for k in P}
    out["brier"] = {"blend": _r(br["p_blend"].mean(), 4), "no_video": _r(br["p_novideo"].mean(), 4),
                    "epa": _r(br["p_epa"].mean(), 4), "coin": 0.25}
    dec = y != 0.5
    out["accuracy"] = {k2: _r(np.mean((P[k][dec] > 0.5) == (y[dec] == 1)), 3) if dec.any() else None
                       for k, k2 in (("p_blend", "blend"), ("p_epa", "epa"))}
    out["blend_minus_epa"] = _boot(br["p_blend"] - br["p_epa"])
    out["video_increment"] = _boot(br["p_blend"] - br["p_novideo"], seed=1)
    out["n_with_video"] = sum(1 for r in rs if r.get("n_video"))
    last = rs[-10:]
    res = [r["auto_act"][a] - r["auto_pred"][a] for r in last for a in ("red", "blue")]
    out["auto_bias_recent"] = {"mean": _r(np.mean(res), 1), "n_alliances": len(res),
                               "from": last[0]["short"], "to": last[-1]["short"]}
    return out


# ------------------------------------------------------------------------------------------------- event extras
def _an(r, *path):
    d = r.get("an")
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def load_library(site_dir):
    """the defense library (defense.json, built by live/scouting.py) or None."""
    p = os.path.join(site_dir or "", "defense.json")
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return None


def library_effect(lib):
    """event-wide retention (share of shot rate a defended robot keeps), placebo-controlled when the library has a
    placebo; None when the library has no usable global estimate."""
    g = (lib or {}).get("global") or {}
    r = g.get("ratio")
    if r is None:
        return None
    pl = (g.get("placebo") or {}).get("pooled_ratio")
    ci = g.get("ratio_ci")
    k = 1.0 / pl if pl else 1.0
    est = r * k
    lo, hi = ((ci[0] * k, ci[1] * k) if ci and ci[0] is not None and ci[1] is not None else (None, None))
    if lo is not None and not (lo <= est <= hi):        # library interval that excludes its own estimate: ignore it
        lo = hi = None
    return {"retained": _r(min(max(est, 0.3), 1.0), 2), "lo": _r(lo, 2), "hi": _r(hi, 2),
            "significant": bool(hi is not None and hi < 1.0), "placebo": pl, "n": g.get("tested")}


def retention_for(team, X):
    """(retained, basis, detail) for a defense target: the library's per-team placebo-controlled estimate only when its
    interval excludes 100%; otherwise the event-wide effect, labelled an assumption."""
    t = (X.get("lib_teams") or {}).get(team) or {}
    r = t.get("retention") or {}
    if r.get("est") is not None and r.get("hi") is not None and (r.get("significant") or r["hi"] < 1.0):
        return float(r["est"]), "measured", (f"defense library: kept {r['est'] * 100:.0f}% "
                                             f"({r['lo'] * 100:.0f}–{r['hi'] * 100:.0f}%, placebo-controlled)")
    e = X.get("defense_effect") or {}
    v = e.get("retained") or 0.75
    return float(v), "assumption", f"assumption: event-wide, defended robots keep ~{round(v * 20) * 5:.0f}% of their shot rate"


def event_extras(V, cov, cov_keys_full, lib=None):
    """event-level facts for the queue card.  cov_keys_full: {team: set(match keys)} of verified video;
    lib: the defense library (defense.json) -- its estimates use all tracked matches, not just those before asof."""
    played = [m for m in V["tba"] if m["played"]]
    ever = {b: any(m[a]["bonus"].get(b) for m in played for a in ("red", "blue"))
            for b in ("energized", "supercharged", "traversal")}
    given, all_given = {}, []
    for m in played:
        for a, o in (("red", "blue"), ("blue", "red")):
            f = m[o]["fouls"] or 0          # foul points awarded to the other alliance = fouls this alliance committed
            all_given.append(f)
            for t in m["teams"][a]:
                given.setdefault(t, []).append(f)
    # verified robot-match video records
    ver = [r for r in V["video"]["robot"] if r["match"] in cov_keys_full.get(r["team"], ())]
    rr = [(_an(r, "defended", "rate_retained"), min(1.0, (_an(r, "defended", "time_s") or 0) / 20.0)) for r in ver
          if (_an(r, "defended", "time_s") or 0) >= 5 and _an(r, "defended", "rate_retained") is not None]
    eff = None
    if len(rr) >= 3:
        x = np.array([a for a, _ in rr], float)
        w = np.array([b for _, b in rr], float)
        m_ = float((w * x).sum() / w.sum())
        sd = float(np.sqrt((w * (x - m_) ** 2).sum() / w.sum()))
        eff = {"retained": _r(m_, 2), "sd": _r(sd, 2), "n": len(rr),
               "text": f"robots defended here kept ~{round(m_ * 20) * 5:.0f}% of their shot rate "
                       f"(typical range {max(0, round((m_ - sd) * 20) * 5):.0f}–{round((m_ + sd) * 20) * 5:.0f}%; event average from "
                       f"{len(rr)} verified robot-matches)"}
    le = library_effect(lib)
    if le:
        lo_hi = f"{le['lo'] * 100:.0f}–{le['hi'] * 100:.0f}%" if le["lo"] is not None else "no reliable interval"
        eff = dict(le, source="library", text=(f"defended robots keep ~{round(le['retained'] * 20) * 5:.0f}% of their shot rate "
                                               f"(event-wide, defense library{', placebo-controlled' if le['placebo'] else ''}; "
                                               f"{lo_hi}){'' if le['significant'] else ' — not distinguishable from no effect, an assumption'}"))
    elif eff:
        eff = dict(eff, source="video", significant=False, text=eff["text"] + " — an assumption")
    lib_teams = {d.get("team"): d for d in ((lib or {}).get("teams") or []) if isinstance(d, dict)}
    lib_def = {}
    raw_def = (lib or {}).get("defenders") or {}
    for item in (raw_def.items() if isinstance(raw_def, dict) else raw_def):
        if isinstance(item, (list, tuple)) and len(item) == 2:
            lib_def[item[0]] = item[1]
        elif isinstance(item, dict) and item.get("team"):
            lib_def[item["team"]] = item
    defend_by = {}
    for r in ver:
        d = defend_by.setdefault(r["team"], {"n": 0, "def": [], "secs": []})
        d["n"] += 1
        s = _an(r, "defense", "time_s") or 0
        d["secs"].append(s)
        if s >= DEF_MIN_S:
            d["def"].append(_short(r["match"]))
    # one source: where the library lists a team's defended matches, use those (restricted to verified, visible ones)
    for t, d in defend_by.items():
        ld = lib_def.get(t)
        if ld and isinstance(ld.get("matches"), list):
            vis = {_short(k) for k in cov_keys_full.get(t, ())}
            d["def"] = [k for k in ld["matches"] if k in vis]
    return {"bonus_ever": ever, "fouls_given": given, "fouls_event": float(np.mean(all_given)) if all_given else None,
            "defense_effect": eff, "defend_by": defend_by, "lib_teams": lib_teams}


# ------------------------------------------------------------------------------------------------- queue card
def their_view_zone(zone):
    """target's zone name (its driver's view) -> from OUR driver station."""
    region, lane = zone.split(" / ")
    reg = {"own zone": "their end", "neutral": "midfield", "opp zone": "our end"}.get(region, region)
    sw = {"left": "right", "right": "left"}
    w = lane.split(" ")
    if w[0] in sw:
        w[0] = sw[w[0]]
    return f"{reg}, our {' '.join(w)}"


def field_xy_ours(xc, yc):
    """target's canonical (own wall at x=LEN, driver-left = low y) -> our-view field fractions:
    u = 0 our left .. 1 our right, v = 0 our wall .. 1 their wall."""
    return [round(min(max((WID - yc) / WID, 0.0), 1.0), 3), round(min(max(xc / LEN, 0.0), 1.0), 3)]


def zone_centroid(V, team, zone, keys):
    xs, ys, ws = [], [], []
    for r in V["video"]["robot"]:
        if r["team"] != team or r["w"] <= 0 or (keys is not None and r["match"] not in keys):
            continue
        for o in r.get("origins") or []:
            if o["counted"] and shot_zone(o["xc"], o["yc"]) == zone:
                xs.append(o["xc"]); ys.append(o["yc"]); ws.append(r["w"])
    if not ws:
        return None
    return float(np.average(xs, weights=ws)), float(np.average(ys, weights=ws))


def pick_defender(robots_us, X):
    """our robot that gives up the least while our hub is inactive: passes it would otherwise feed (measured on
    verified video, FEED_CONV assumed) -- not simply the lowest scorer."""
    cands = []
    known = [((r.get("hub_state") or {}).get("inactive") or {}).get("passes_per_min") for r in robots_us]
    med = float(np.median([k for k in known if k is not None])) if any(k is not None for k in known) else None
    for r, ppm in zip(robots_us, known):
        cost = None if ppm is None else ppm * INACTIVE_MIN * FEED_CONV
        cands.append({"team": r["team"], "passes_per_min": _r(ppm, 1), "feed_cost_fuel": _r(cost, 1),
                      "expected_fuel": r["expected_fuel"]["total"],
                      "defense_s": ((r.get("defense") or {}).get("defense_s_per_match") or {}).get("v")
                      if not ((r.get("defense") or {}).get("defense_s_per_match") or {}).get("prior_only") else None})
    have = [c for c in cands if c["feed_cost_fuel"] is not None]
    if have:
        best = min(cands, key=lambda c: (c["feed_cost_fuel"] if c["feed_cost_fuel"] is not None else (med or 0) *
                                         INACTIVE_MIN * FEED_CONV, c["expected_fuel"]))
        feeders = sorted([c for c in have if c["team"] != best["team"] and (c["passes_per_min"] or 0) >= 5],
                         key=lambda c: -c["passes_per_min"])
        why = (f"{_num(best['team'])} gives up the least feeding while our hub is inactive"
               + (f" (~{best['passes_per_min']:.0f} passes/min on video)" if best["passes_per_min"] is not None else
                  " (no verified passing data; assumed median)")
               + ("; keep " + ", ".join(f"{_num(c['team'])} (~{c['passes_per_min']:.0f}/min)" for c in feeders)
                  + " feeding" if feeders else ""))
        basis = "video passing rate"
    else:
        best = min(cands, key=lambda c: c["expected_fuel"])
        why = f"{_num(best['team'])} has the lowest expected FUEL on our alliance (no verified passing data for anyone)"
        basis = "lowest expected scorer"
    return {"team": best["team"], "why": why, "basis": basis, "candidates": cands}


def _ever_bonus_names(X):
    return [b for b in ("energized", "supercharged", "traversal") if b != "traversal" or X["bonus_ever"]["traversal"]]


def queue_card(nm, P, raw, X, cov, epa_wp, auto_bias, V, team):
    """the six-line queue card (+ goal-mode headline) for one plan, our alliance's point of view."""
    us, them = nm["alliance"], ("blue" if nm["alliance"] == "red" else "red")
    pr = nm["prediction"]
    wp = pr["win_prob"]
    robots = {r["team"]: r for r in P["robots"]}
    # ---- RP odds (us / them) from the plan's own simulation
    rp = {}
    shown = _ever_bonus_names(X)
    for side, a in (("us", us), ("them", them)):
        A = raw[a]
        win = wp if side == "us" else 1 - wp          # one win % per match (the plan's headline number)
        d = {"win": _r(win, 3), "energized": _r((A["fuel"] >= 100).mean(), 3),
             "supercharged": _r((A["fuel"] >= 360).mean(), 3), "traversal": _r(((A["ta"] + A["te"]) >= 50).mean(), 3)}
        d["rp_mean"] = _r(3 * win + sum(d[b] for b in ("energized", "supercharged", "traversal")), 2)
        d["fuel"] = {"mean": _r(A["fuel"].mean(), 0), "lo": _r(np.percentile(A["fuel"], 10), 0),
                     "hi": _r(np.percentile(A["fuel"], 90), 0)}
        rp[side] = d
    # ---- headline / goal mode
    mode = "goal" if (wp < GOAL_LO or wp > GOAL_HI) else "win"
    lever = None
    if mode == "goal":
        cands = [b for b in shown if b != "traversal" or X["bonus_ever"]["traversal"]]
        mid = [b for b in cands if 0.03 <= rp["us"][b] <= 0.97]
        b = min(mid, key=lambda b: abs(rp["us"][b] - 0.5)) if mid else "energized"
        need = {"energized": 100, "supercharged": 360, "traversal": 50}[b]
        f = rp["us"]["fuel"]
        lever = {"bonus": b, "needed": need, "unit": "FUEL" if b != "traversal" else "tower pts",
                 "expected": f["mean"] if b != "traversal" else None, "lo": f["lo"], "hi": f["hi"], "p": rp["us"][b],
                 "text": (f"{'Win is likely' if wp > GOAL_HI else 'A win is unlikely'} — play for the "
                          f"{b.upper()} RP: we expect ~{f['mean']:.0f} counted FUEL (10–90%: {f['lo']:.0f}–{f['hi']:.0f}) vs "
                          f"{need} needed." if b != "traversal" else
                          f"Play for TRAVERSAL: needs {need} tower points.")}
    # ---- line 1: auto -> our active shifts, and the swap
    pa = pr["p_win_auto"] or 0.5
    ours = P["alliances"][us]["fuel_by_period"]["auto"]
    theirs = P["alliances"][them]["fuel_by_period"]["auto"]
    hist = {}
    for t in nm["teams"]["red"] + nm["teams"]["blue"]:
        h = []
        for m in sorted([m for m in V["tba"] if m["played"] and t in m["teams"]["red"] + m["teams"]["blue"]],
                        key=lambda m: m["time"])[-4:]:
            a = "red" if t in m["teams"]["red"] else "blue"
            h.append({"match": _short(m["key"]), "alliance_auto": m[a]["comp"]["auto"]})
        hist[t] = h
    hist_avg = {}
    for side, a in (("us", us), ("them", them)):
        vals = [np.mean([x["alliance_auto"] for x in hist[t]]) for t in nm["teams"][a] if hist[t]]
        hist_avg[side] = _r(np.mean(vals), 0) if vals else None
    first = pa >= 0.5
    auto = {"p_win_auto": pr["p_win_auto"], "our_expected": _r(ours, 0), "their_expected": _r(theirs, 0),
            "if_won": {"our_active": ["shift2", "shift4"], "defend_in": ["shift1", "shift3"],
                       "win_prob": pr["win_prob_if_auto_won"]},
            "if_lost": {"our_active": ["shift1", "shift3"], "defend_in": ["shift2", "shift4"],
                        "win_prob": pr["win_prob_if_auto_lost"]},
            "history": hist, "history_alliance_avg": hist_avg,
            "recent_bias": auto_bias,
            "text": (f"We win auto {round(pa * 20) * 5:.0f}% of sims (exp. FUEL {ours:.0f} vs {theirs:.0f}). "
                     f"Win auto → our hub is live S2+S4. Lose it → swap: live S1+S3.")}
    if auto_bias and auto_bias.get("mean") is not None and abs(auto_bias["mean"]) >= 5:
        auto["caution"] = (f"Model has {'under' if auto_bias['mean'] > 0 else 'over'}-predicted alliance auto by "
                           f"~{abs(auto_bias['mean']):.0f} FUEL over {auto_bias['from']}–{auto_bias['to']} "
                           f"(autos at this event are trending {'up' if auto_bias['mean'] > 0 else 'down'}).")
    # ---- line 2: defend who / when / where
    d0 = (nm["plan"]["defend"] or [None])[0]
    dfd = nm["plan"]["suggested_defender"]
    defend = None
    if d0:
        tz = (robots[d0["target"]].get("top_zones") or {})
        keys = set(cov.get(d0["target"], {}).get("keys_full") or [])
        where = None
        if tz.get("zones") and tz.get("w_made", 0) >= 3:
            z = tz["zones"][0]
            c = zone_centroid(V, d0["target"], z["zone"], keys or None)
            where = {"zone_ours": their_view_zone(z["zone"]), "share": z["share"],
                     "field": field_xy_ours(*c) if c else None}
        when = d0["when"]
        defend = {"target": d0["target"], "defender": dfd["team"], "when": when, "when_alt": d0["when_alt"],
                  "where": where, "denied_fuel": int(round(d0["expected_gain_fuel"] or 0)),
                  "basis": "measured" if d0["basis"] == "measured" else "assumption",
                  "coverage": cov.get(d0["target"], {}).get("label"),
                  "text": (f"{_num(dfd['team'])} defends {_num(d0['target'])} in {'+'.join(w.replace('shift', 'S') for w in when)}"
                           f" (their live shifts{' if we win auto' if first else ' if we lose auto'})"
                           + (f", {where['zone_ours']}" if where else "") + ".")}
    if defend and not d0.get("top_scorer"):
        ts_ = next((d for d in nm["plan"]["defend"] if d.get("top_scorer")), None)
        if ts_:
            defend["alt"] = {"target": ts_["target"], "denied_fuel": int(round(ts_["expected_gain_fuel"] or 0)),
                             "basis": "measured" if ts_["basis"] == "measured" else "assumption",
                             "text": f"alternative: their top scorer {_num(ts_['target'])} (≈{ts_['expected_gain_fuel']:.0f} FUEL denied)"}
    if defend and defend["denied_fuel"] < 2:
        defend["skip_note"] = "No opponent is worth a dedicated defender here (under 2 FUEL denied) — all three score/feed."
    if defend and mode == "goal" and lever and lever["bonus"] != "traversal":
        defend["goal_note"] = (f"Goal mode: defense only changes the win ({'already likely' if wp > GOAL_HI else 'unlikely'}); "
                               f"{lever['bonus'].upper()} needs our own FUEL, so keep all three robots scoring/feeding "
                               f"unless we are clearly past {lever['needed']}.")
    # ---- line 3: who will defend us
    eff = X["defense_effect"]
    opp = []
    for t in nm["opponents"]:
        d = X["defend_by"].get(t)
        c = cov.get(t, {})
        if d and d["n"]:
            opp.append({"team": t, "n_video": d["n"], "n_defended": len(d["def"]), "matches": d["def"],
                        "avg_defense_s": _r(np.mean(d["secs"]), 0), "tier": c.get("tier"),
                        "label": f"defended in {len(d['def'])} of {d['n']} verified matches"
                                 + (f" ({', '.join(d['def'])})" if d["def"] else "")})
        else:
            opp.append({"team": t, "n_video": 0, "n_defended": None, "matches": [], "avg_defense_s": None,
                        "tier": "none", "label": "no verified video"})
    likely = [o for o in opp if o["n_defended"] and o["n_defended"] >= 0.5 * o["n_video"]]
    likely.sort(key=lambda o: (-(o["n_defended"] / o["n_video"]), -(o["avg_defense_s"] or 0)))
    some = [o for o in opp if o["n_defended"] and o not in likely]
    if likely:
        txt = ("Expect defense from " + ", ".join(f"{_num(o['team'])} ({o['n_defended']}/{o['n_video']} verified matches)"
                                                  for o in likely) + ".")
    elif some:
        txt = ("Occasional defense: " + ", ".join(f"{_num(o['team'])} ({o['n_defended']}/{o['n_video']})" for o in some) + ".")
    elif all(o["n_video"] == 0 for o in opp):
        txt = "Unknown who defends — none of them has verified video yet."
    else:
        txt = "None of them has defended in verified video (" + ", ".join(
            f"{_num(o['team'])} n={o['n_video']}" for o in opp) + ")."
    defenders = {"teams": opp, "likely": [o["team"] for o in likely], "effect": eff, "text": txt}
    if likely and raw is not None:
        # the simulator has no defense: show what a dedicated defender on our top scorer would do to the win %
        rs = [r for r in P["robots"] if r["team"] in nm["teams"][us]]
        top = max(rs, key=lambda r: sum(r["expected_fuel"]["by_period"][s_] for s_ in ("shift1", "shift2", "shift3", "shift4")))
        act = sum(top["expected_fuel"]["by_period"][s_] for s_ in ("shift1", "shift2", "shift3", "shift4"))
        ret = (eff or {}).get("retained") or 0.75
        loss = act * (1 - ret) * 0.7
        mg = raw[us]["score"] - raw[them]["score"] - loss
        wd = float(((mg > 0) + 0.5 * (mg == 0)).mean())
        defenders["if_defended"] = {"defender": likely[0]["team"], "target": top["team"], "fuel_lost": _r(loss, 0),
                                    "win_prob": _r(wd, 3), "basis": "measured" if (eff or {}).get("significant") else "assumption"}
        defenders["ignores"] = (f"Win % ignores {_num(likely[0]['team'])}'s defense; if it shuts down our "
                                f"{_num(top['team'])} (~{loss:.0f} FUEL) the win % is ~"
                                + ("<5%" if wd < 0.05 else ">95%" if wd > 0.95 else f"{round(wd * 20) * 5}%") + ".")
    # ---- line 4: RP
    rp_line = {"us": rp["us"], "them": rp["them"], "shown": shown,
               "hidden": [b for b in ("traversal",) if b not in shown],
               "hidden_why": "nobody at this event has earned TRAVERSAL yet" if "traversal" not in shown else None}
    # ---- line 5: fouls
    ev = X["fouls_event"]
    fl = []
    for side, a in (("us", us), ("them", them)):
        for t in nm["teams"][a]:
            g = X["fouls_given"].get(t) or []
            fl.append({"team": t, "side": side, "avg_given": _r(np.mean(g), 1) if g else None, "n": len(g)})
    flag = [f for f in fl if f["avg_given"] is not None and ev and f["n"] >= 3 and f["avg_given"] >= max(1.5 * ev, ev + 5)]
    if flag:
        ftxt = "; ".join(f"{'our partner ' if f['side'] == 'us' and f['team'] != team else 'we (' if f['team'] == team else 'their '}"
                         f"{_num(f['team'])}{')' if f['team'] == team else ''}: alliances gave away {f['avg_given']:.0f} foul pts/match"
                         for f in flag) + \
               f" (event avg {ev:.0f}). " + ("Careful in contact — we are the ones fouling." if any(f["side"] == "us" for f in flag)
                                            else "Stay clean; their fouls are free points for us.")
        ftxt = ftxt[0].upper() + ftxt[1:]
    else:
        ftxt = f"No foul outliers in this match (event avg {ev:.0f} pts given away per alliance)." if ev is not None else "No foul data yet."
    fouls = {"teams": fl, "event_avg": _r(ev, 1), "flag": [f["team"] for f in flag], "text": ftxt,
             "note": "foul points are alliance-level in TBA; a team's number is its alliances' average, not its own"}
    # ---- EPA beside the blend
    mdis = None if epa_wp is None else abs(disp(wp) - disp(epa_wp)) >= DISAGREE_PTS * 100   # on the shown numbers
    return {"mode": mode, "lever": lever, "win_prob": wp, "win_prob_epa": _r(epa_wp, 3), "models_disagree": mdis,
            "auto": auto, "defend": defend, "defenders": defenders, "rp": rp_line, "fouls": fouls}
