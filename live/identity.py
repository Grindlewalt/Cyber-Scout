"""Event-level robot -> team assignment.

Within a match the tracker only knows "red robot A/B/C".  Across an event each
team plays ~10 matches with different partners, and its robot looks the same in
all of them (colour, height, where it starts, whether it climbs).  So solve
jointly: per (match, alliance) choose the permutation of tracks -> the three
known teams that makes every team's appearances most self-consistent.

Alternating optimisation from several initialisations:
  prototypes (per team)  <- mean features of the tracks currently assigned to it
  assignment (per m, a)  <- best of the 3! permutations against the prototypes
plus fixed evidence that does not depend on prototypes (TBA per-station climb
results vs. whether the track ends at its tower).  Confidence per (m, a) is the
cost margin between the best and second-best permutation.

Manual overrides (data/events/<key>/overrides.json:
{"<match_key>": {"red": {"<track idx>": "frc1234", ...}}}) are honoured and anchor the solve.
"""
import itertools, json, os, numpy as np

W_HIST, W_H, W_START, W_CLIMB = 1.0, 1.0, 0.6, 1.5
SIG_H, SIG_START = 0.08, 0.8
TOWER = {"red": np.array([16.0, 4.05]), "blue": np.array([0.55, 4.0])}


def _bhat(p, q):
    return float(1.0 - np.sqrt(np.clip(p, 0, None) * np.clip(q, 0, None)).sum())


def _tower_cost(track, climbed, key):
    xy = track["feat"].get(key)
    if climbed is None or xy is None:
        return 0.0
    near = np.linalg.norm(np.array(xy) - TOWER[track["alliance"]]) < 1.6
    if climbed and not near: return W_CLIMB
    if not climbed and near: return 0.5 * W_CLIMB
    return 0.0


def climb_cost(track, climbed):
    """climbed: (auto, endgame) booleans/None from TBA for the station's team."""
    if climbed is None:
        return 0.0
    auto, end = climbed if isinstance(climbed, tuple) else (None, climbed)
    return _tower_cost(track, auto, "auto_end_xy") + _tower_cost(track, end, "end_xy")


def feat_cost(track, proto):
    if proto is None:
        return 0.0
    f = track["feat"]; c = W_HIST * _bhat(np.array(f["hist"]), proto["hist"]) / 0.05
    if f.get("height") is not None and proto["height"] is not None:
        c += W_H * min(((f["height"] - proto["height"]) / SIG_H) ** 2, 9.0)
    if proto["start"] is not None:
        d = np.linalg.norm(np.array(f["start_xy"]) - proto["start"])
        c += W_START * min((d / SIG_START) ** 2, 9.0)
    return c


def build_protos(groups, assign, teams):
    acc = {t: {"hist": [], "h": [], "start": []} for t in teams}
    for g, perm in zip(groups, assign):
        for ti, team in zip(perm, g["teams"]):
            if ti is None: continue
            f = g["tracks"][ti]["feat"]
            acc[team]["hist"].append(f["hist"])
            if f.get("height") is not None: acc[team]["h"].append(f["height"])
            acc[team]["start"].append(f["start_xy"])
    P = {}
    for t, a in acc.items():
        if not a["hist"]:
            P[t] = None; continue
        P[t] = dict(hist=np.mean(a["hist"], 0),
                    height=float(np.median(a["h"])) if a["h"] else None,
                    start=np.median(np.array(a["start"]), 0) if len(a["start"]) >= 3 else None)
    return P


def perms_for(g):
    n = len(g["tracks"])
    idx = list(range(n)) + [None] * (3 - n)
    return sorted(set(itertools.permutations(idx, 3)), key=str)


def perm_cost(g, perm, P):
    c = 0.0
    for ti, team in zip(perm, g["teams"]):
        if ti is None:
            c += 3.0; continue
        tr = g["tracks"][ti]
        c += feat_cost(tr, P.get(team)) + climb_cost(tr, g["climb"].get(team))
    return c


def solve(groups, teams, overrides=None, restarts=8, iters=15, seed=0):
    """groups: [{key, alliance, teams[3], tracks[...], climb{team: bool|None}}]."""
    rng = np.random.default_rng(seed)
    fixed = {}
    for i, g in enumerate(groups):
        ov = (overrides or {}).get(g["key"], {}).get(g["alliance"])
        if ov:   # {track_idx: team_key}
            pos = {str(tr["idx"]): k for k, tr in enumerate(g["tracks"])}
            inv = {team: pos.get(str(ti)) for ti, team in ov.items()}
            fixed[i] = tuple(inv.get(team) for team in g["teams"])
    best = None
    for r in range(restarts):
        if r == 0:
            # climb-evidence-only start; pure prototype-free
            A = [min(perms_for(g), key=lambda p: perm_cost(g, p, {})) for g in groups]
        else:
            A = [perms_for(g)[rng.integers(len(perms_for(g)))] for g in groups]
        A = [fixed.get(i, a) for i, a in enumerate(A)]
        for _ in range(iters):
            P = build_protos(groups, A, teams)
            newA = [fixed.get(i) or min(perms_for(g), key=lambda p: perm_cost(g, p, P))
                    for i, g in enumerate(groups)]
            if newA == A: break
            A = newA
        P = build_protos(groups, A, teams)
        tot = sum(perm_cost(g, a, P) for g, a in zip(groups, A))
        if best is None or tot < best[0]:
            best = (tot, A, P)
    tot, A, P = best
    out = []
    for i, (g, a) in enumerate(zip(groups, A)):
        cs = sorted(perm_cost(g, p, P) for p in perms_for(g))
        margin = cs[1] - cs[0] if len(cs) > 1 else 0.0
        conf = 1.0 if i in fixed else float(1 - np.exp(-margin / 2.0))
        out.append(dict(key=g["key"], alliance=g["alliance"], perm=a, confidence=round(conf, 3),
                        manual=i in fixed))
    return out, tot


def climb_flags(match, alliance):
    """TBA per-station endgame tower result -> {team: True/False}; None if unknown."""
    sb = (match.get("score_breakdown") or {}).get(alliance) or {}
    teams = match["alliances"][alliance]["team_keys"]
    out = {}
    for i, t in enumerate(teams):
        a, e = sb.get(f"autoTowerRobot{i + 1}"), sb.get(f"endGameTowerRobot{i + 1}")
        out[t] = (None if a is None else a not in ("None", ""), None if e is None else e not in ("None", ""))
    return out


def assign_event(results, tba_matches, overrides=None):
    """results: {match_key: processed JSON}.  Returns {match_key: {alliance: [team per track idx]}, conf}."""
    groups, teams = [], set()
    mm = {m["key"]: m for m in tba_matches}
    for k, res in results.items():
        m = mm.get(k)
        if not m: continue
        for a in ("red", "blue"):
            tk = [t for t in res["tracks"] if t["alliance"] == a]
            tm = m["alliances"][a]["team_keys"]
            if not tk or len(tm) != 3: continue
            teams.update(tm)
            groups.append(dict(key=k, alliance=a, teams=tm, tracks=tk, climb=climb_flags(m, a)))
    if not groups:
        return {}
    sol, _ = solve(groups, sorted(teams), overrides)
    out = {}
    for g, s in zip(groups, sol):
        lab = {}
        for ti, team in zip(s["perm"], g["teams"]):
            if ti is not None: lab[g["tracks"][ti]["idx"]] = team
        out.setdefault(g["key"], {})[g["alliance"]] = dict(labels=lab, confidence=s["confidence"],
                                                           manual=s["manual"])
    return out
