"""Alliance-selection view (api/<ev>/selection.json, schema cyber-scout/selection@1; contract in README.md).

Built from the same goals.Season Monte Carlo that drives the DCMP odds, so every number here is conditional on one set of
simulated seasons: final qual ranks -> draft (captains by rank, picks = best remaining rating, snake, no declines) ->
playoffs -> district points -> DCMP.  "Outcome" = (alliance seed, role) of a team at the focal event.

Honesty notes carried into the JSON:
  * the draft model is "best available by rating": real captains decline, scout differently and pick for fit;
  * P(DCMP | outcome) is conditional (not causal) and comes from >= MIN_N sims, else it is omitted;
  * the manual scenario on the page re-runs only the draft (sampled ranks + rating draws), then maps outcomes to the
    base-run P(DCMP | outcome) table -- it does not re-simulate playoffs.
"""
import math
from collections import Counter, defaultdict

import numpy as np

ROLES = {1: "captain", 2: "first pick", 3: "second pick"}
RESULTS = [("win", 30), ("finalist", 20), ("top 4 (13)", 13), ("top 6 (7)", 7), ("out early", 0)]
MIN_N = 15
DRAFT_SAMPLE = 300


def _r(x, nd=3):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), nd)


def _num(t):
    return int(t[3:]) if str(t)[3:].isdigit() else t


def roles(R):
    """-> role (n, Ne) int 0/1/2/3, seed (n, Ne) int 0 or 1..8."""
    pairs = R["pairs"]
    n, Ne = R["rank"].shape
    role = np.zeros((n, Ne), int)
    seed = np.zeros((n, Ne), int)
    ar = np.arange(n)
    for k in range(8):
        for j in range(3):
            role[ar, pairs[:, k, j]] = j + 1
            seed[ar, pairs[:, k, j]] = k + 1
    return role, seed


def selection_block(S, ev, team, goal=None, asof=None, nick=None, comps=None, scout=None):
    """S = goals.Season (base run), ev = focal event key, team = our team.  comps = {team: {auto, teleop, endgame, total, sd}}
    (dashboard blend components) for the pitch cards; scout = scout.json teams by key (video facts, optional)."""
    nick = nick or {}
    comps = comps or {}
    scout = scout or {}
    R = S.base_run["ev"].get(ev)
    out = {"schema": "cyber-scout/selection@1", "event": ev, "team": team, "asof": asof, "sims": S.n,
           "model": {"draft": "captains = top 8 by simulated final rank; picks = best remaining rating draw "
                              "(snake, no declines); ratings: " + S.theta_src,
                     "playoffs": "8-alliance double elimination on summed ratings"}}
    if R is None:
        out["status"] = "done" if ev in S.done_pts else "unavailable"
        evd = next((e for e in S.D["events"] if e["key"] == ev), None) or {}
        al = evd.get("alliances") or []
        if al:                                    # what actually happened (TBA alliances + district points)
            pts = S.done_pts.get(ev) or {}
            out["actual"] = [{"seed": i + 1, "name": a.get("name"), "picks": (a.get("picks") or [])[:4],
                              "status": ((a.get("status") or {}).get("status") or (a.get("status") or {}).get("level")),
                              "points": {t: (pts.get(t) or {}).get("total") for t in (a.get("picks") or [])[:4]}}
                             for i, a in enumerate(al[:8])]
            if team in S.col:
                qual, _ = S.dcmp_outcome(S.base_run)
                out["actual_us"] = {"team": team, "event_points": (pts.get(team) or {}).get("total"),
                                    "p_dcmp": _r(qual[:, S.col[team]].mean()),
                                    "seed": next((x["seed"] for x in out["actual"] if team in x["picks"]), None),
                                    "role": next((["captain", "first pick", "second pick", "backup"][x["picks"].index(team)]
                                                  for x in out["actual"] if team in x["picks"]), None)}
        return out
    out["status"] = "fixed" if R.get("fixed") else "projected"
    teams = R["teams"]
    loc = {t: i for i, t in enumerate(teams)}
    n, Ne = R["rank"].shape
    role, seed = roles(R)
    E = R["alliance_elim"]                         # (n, 8) playoff points per alliance (unmultiplied)
    qual, cutoff = S.dcmp_outcome(S.base_run)
    rows = []
    for j, t in enumerate(teams):
        rl = role[:, j]
        row = {"team": t, "number": _num(t), "nickname": nick.get(t),
               "rank_mean": _r(R["rank"][:, j].mean(), 1),
               "rank_p10": int(np.percentile(R["rank"][:, j], 10)), "rank_p90": int(np.percentile(R["rank"][:, j], 90)),
               "p_captain": _r((rl == 1).mean()), "p_r1": _r((rl == 2).mean()), "p_r2": _r((rl == 3).mean()),
               "p_selected": _r((rl > 0).mean()),
               "rating": _r(S.mu[S.col[t]] * 3, 1) if t in S.col else None,
               "rating_sd": _r(S.sd[S.col[t]] * 3, 1) if t in S.col else None,
               "p_dcmp": _r(qual[:, S.col[t]].mean()) if t in S.col else None}
        # most likely captain that picks this team
        cap = Counter()
        for s in np.where(rl >= 2)[0]:
            cap[teams[R["pairs"][s, seed[s, j] - 1, 0]]] += 1
        if cap:
            c, k = cap.most_common(1)[0]
            row["likely_captain"] = {"team": c, "p": _r(k / n)}
        rows.append(row)
    rows.sort(key=lambda r: (-(r["p_selected"] or 0), r["rank_mean"]))
    out["teams"] = rows
    # projected seeds: who captains each alliance
    seeds = []
    for k in range(8):
        cnt = Counter(R["pairs"][:, k, 0])
        p1 = Counter(R["pairs"][:, k, 1])
        seeds.append({"seed": k + 1,
                      "captain": [{"team": teams[i], "p": _r(c / n)} for i, c in cnt.most_common(3)],
                      "first_pick": [{"team": teams[i], "p": _r(c / n)} for i, c in p1.most_common(3)],
                      "playoffs": {lab: _r((E[:, k] == pts).mean()) for lab, pts in RESULTS}})
    out["seeds"] = seeds
    if team not in loc:
        return out
    j = loc[team]
    c_us = S.col[team]
    q_us = qual[:, c_us]
    rl, sd = role[:, j], seed[:, j]
    ar = np.arange(n)
    e_us = np.where(sd > 0, E[ar, np.maximum(sd - 1, 0)], 0)
    outcomes = []
    for k in range(1, 9):
        for ro in (1, 2, 3):
            m = (sd == k) & (rl == ro)
            if m.sum() == 0:
                continue
            o = {"seed": k, "role": ROLES[ro], "share": _r(m.mean()), "n": int(m.sum()),
                 "event_points": _r(R["total"][m, j].mean() / R["mult"], 1),
                 "p_dcmp": _r(q_us[m].mean()) if m.sum() >= MIN_N else None,
                 "playoffs": {lab: _r((e_us[m] == pts).mean()) for lab, pts in RESULTS}}
            outcomes.append(o)
    m = rl == 0
    outcomes.append({"seed": None, "role": "not selected", "share": _r(m.mean()), "n": int(m.sum()),
                     "event_points": _r(R["total"][m, j].mean() / R["mult"], 1) if m.any() else None,
                     "p_dcmp": _r(q_us[m].mean()) if m.sum() >= MIN_N else None})
    outcomes.sort(key=lambda o: -(o["share"] or 0))
    # role-level DCMP odds (for the manual scenario: fallback when a seed x role cell is thin)
    by_role = {}
    for ro, lab in ((1, "captain"), (2, "first pick"), (3, "second pick"), (0, "not selected")):
        mm = rl == ro
        by_role[lab] = {"share": _r(mm.mean()), "p_dcmp": _r(q_us[mm].mean()) if mm.sum() >= MIN_N else None,
                        "n": int(mm.sum())}
    # who picks us
    who, det = Counter(), defaultdict(Counter)
    for s in np.where(rl >= 2)[0]:
        c = teams[R["pairs"][s, sd[s] - 1, 0]]
        who[c] += 1
        det[c][(int(sd[s]), ROLES[int(rl[s])])] += 1
    picks_us = []
    for c, v in who.most_common(8):
        (k, r), _ = det[c].most_common(1)[0]
        picks_us.append({"captain": c, "seed": k, "role": r, "p": _r(v / n),
                         "slots": [{"seed": kk, "role": rr, "p": _r(vv / n)} for (kk, rr), vv in det[c].most_common(3)]})
    # DCMP need in pick terms
    need = None
    if goal and goal.get("points_needed"):
        fin = goal["points_needed"]["p50"]["final_points"]
        cur = goal.get("current_points") or 0
        need_here = max(0.0, fin - cur)
        qmed = float(np.median(R["qual"][:, j] / R["mult"]))
        grid = []
        for k in range(1, 9):
            row = {"seed": k}
            for ro, apts in (("captain", 17 - k), ("first pick", 17 - k), ("second pick", k)):
                cell = None
                for lab, pts in reversed(RESULTS):                    # least demanding result first
                    if qmed + apts + pts >= need_here:
                        cell = {"result": lab, "points": _r(qmed + apts + pts, 0),
                                "p_result": _r((E[:, k - 1] >= pts).mean(), 2)}
                        break
                row[ro] = cell or {"result": None, "points": _r(qmed + apts + 30, 0)}
            grid.append(row)
        out_need = {"final_points": fin, "current_points": cur, "need_here": _r(need_here, 0),
                    "qual_points_median": _r(qmed, 0), "grid": grid, "text": _need_text(need_here, qmed, grid),
                    "note": "excludes award points; cutoff = median projected DCMP cutoff (goal block)"}
        need = out_need
    pitches = _pitches(team, picks_us, comps, scout, teams, nick)
    us = {"team": team, "rank_mean": _r(R["rank"][:, j].mean(), 1), "p_captain": _r((rl == 1).mean()),
          "p_r1": _r((rl == 2).mean()), "p_r2": _r((rl == 3).mean()), "p_unpicked": _r((rl == 0).mean()),
          "p_dcmp": _r(q_us.mean()), "outcomes": outcomes, "by_role": by_role, "picked_by": picks_us,
          "need": need, "pitches": pitches}
    out["us"] = us
    # draft sample for the manual "captain picks X" scenario (client-side re-draft)
    rng = np.random.default_rng(5)
    ss = np.sort(rng.choice(n, size=min(DRAFT_SAMPLE, n), replace=False))
    th = S.theta[:, R["cols"]] * 3.0
    order = np.argsort(R["rank"], axis=1)
    out["draft"] = {"teams": teams, "order": order[ss].tolist(),
                    "theta": np.round(th[ss], 1).tolist(),
                    "p_dcmp_by_outcome": {f"{o['seed']}:{o['role']}": o["p_dcmp"] for o in outcomes if o["seed"]},
                    "p_dcmp_by_role": {k: v["p_dcmp"] for k, v in by_role.items()}}
    return out


def _need_text(need_here, qmed, grid):
    if need_here <= qmed:
        return f"need ~{need_here:.0f} event points: qual points alone (~{qmed:.0f}) cover it"
    parts = []
    for ro in ("first pick", "captain", "second pick"):
        by_res = defaultdict(list)
        for g in grid:
            c = g[ro]
            if c and c["result"]:
                by_res[c["result"]].append(g["seed"])
        for res, seeds in sorted(by_res.items(), key=lambda x: [r[0] for r in RESULTS][::-1].index(x[0])):
            rng = f"A{seeds[0]}" if len(seeds) == 1 else f"A{seeds[0]}–A{seeds[-1]}"
            label = {"captain": "captain of", "first pick": "R1 pick of", "second pick": "R2 pick of"}[ro]
            parts.append(f"{label} {rng}" + ("" if res == "out early" else f" + {res}"))
    if not parts:
        return f"need ~{need_here:.0f} event points: out of reach without awards"
    return f"need ~{need_here:.0f} event points (qual ~{qmed:.0f}): " + "; ".join(parts[:4])


COMP_TXT = {"auto": "auto", "teleop": "teleop FUEL", "endgame": "endgame"}


def _pitches(team, picks_us, comps, scout, teams, nick):
    """One card per likely captain: what the captain lacks (blend components vs the event's top-8 median) and what
    `team` brings, with the evidence and its n."""
    if not comps or team not in comps:
        return []
    allc = [c for t, c in comps.items() if t in teams and c.get("total") is not None]
    top = sorted(allc, key=lambda c: -c["total"])[:8]
    med = {k: float(np.median([c.get(k) or 0 for c in top])) for k in COMP_TXT}
    spread = {k: float(np.std([c.get(k) or 0 for c in allc]) or 1.0) for k in COMP_TXT}
    ours = comps[team]
    keys = [k for k in COMP_TXT if k != "endgame" or med["endgame"] > 5]     # nobody climbs -> endgame is no lever
    rank_of = {k: 1 + sum(1 for c in allc if (c.get(k) or 0) > (ours.get(k) or 0)) for k in COMP_TXT}
    sv = (scout.get(team) or {}).get("video") or {}
    facts = []
    if sv.get("acc") and sv["acc"].get("raw") is not None:
        facts.append(f"accuracy {round(sv['acc']['raw'] * 100)}% ({sv['acc']['made']}/{sv['acc']['shots']} shots, n={sv['acc']['n']})")
    if sv.get("hopper") and sv["hopper"].get("v"):
        facts.append(f"volley p90 {sv['hopper']['v']:.0f} (n={sv['hopper']['n']})")
    if sv.get("auto_made") and sv["auto_made"].get("raw") is not None:
        facts.append(f"auto {sv['auto_made']['raw']:.0f} FUEL (n={sv['auto_made']['n']})")
    seen = set()
    cards = []
    for p in picks_us:
        cap = p["captain"]
        if cap in seen or cap not in comps:
            continue
        seen.add(cap)
        cc = comps[cap]
        gaps = sorted(keys, key=lambda k: ((cc.get(k) or 0) - med[k]) / spread[k])
        weak = gaps[0]
        best = min(keys, key=lambda k: rank_of[k])
        lack = f"{COMP_TXT[weak]} {cc.get(weak) or 0:.0f} vs top-8 median {med[weak]:.0f}"
        bring = f"{COMP_TXT[weak]} {ours.get(weak) or 0:.0f} (#{rank_of[weak]} of {len(allc)})"
        if rank_of[weak] > len(allc) / 2 and best != weak:
            bring = f"{COMP_TXT[best]} {ours.get(best) or 0:.0f} (#{rank_of[best]} of {len(allc)}) rather than {COMP_TXT[weak]}"
        pitch = (f"{_num(cap)} is light on {COMP_TXT[weak]}; {_num(team)} adds {ours.get(weak) or 0:.0f} {COMP_TXT[weak]} pts/match"
                 if rank_of[weak] <= len(allc) / 2 else
                 f"{_num(team)} adds {ours.get(best) or 0:.0f} {COMP_TXT[best]} pts/match (#{rank_of[best]} here)")
        cards.append({"captain": cap, "nickname": nick.get(cap), "seed": p["seed"], "role": p["role"], "p": p["p"],
                      "captain_rating": {k: _r(cc.get(k), 1) for k in ("total", "auto", "teleop", "endgame", "sd")},
                      "lacks": lack, "we_bring": bring, "pitch": pitch, "evidence": facts})
        if len(cards) >= 4:
            break
    return cards
