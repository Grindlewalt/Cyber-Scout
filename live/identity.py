"""Event-level robot -> team assignment.

Within a match the tracker only knows "red robot A/B/C" -- and, measured against
hand-read bumper numbers on 20 Sammamish quals, its tracks are NOT one robot each:
only ~1/3 of tracks stay on one robot for the whole match; the rest swap with a
teammate when the robots touch (and occasionally pick up an opponent).  So
identity is solved per FRAME, not per track:

1. Evidence (per track, time-stamped), from `extract_features()` (a second,
   light pass over the match video, run after process.py):
     * bumper-number OCR -- PP-OCRv3 text detection + recognition (onnxruntime,
       ~13 MB of models), each text box scored by CTC against the six team
       numbers in the match.  ~96% of reads are right; a read lands on ~5-10% of
       track-samples, i.e. tens of reads per track per match.
     * appearance -- a 64-bin RGB histogram of the robot (fuel, bumper red/blue
       and carpet pixels masked out) on frames where the robot is isolated.
2. Per (match, alliance) an HMM whose state is the assignment of the alliance's
   three teams to its tracks (3! states).  Teams can only trade tracks when the
   two tracks are close (robots in contact) -- the swap probability is a function
   of their distance at that moment.  Emissions: OCR reads, appearance vs. team
   prototypes, TBA per-station climb results, manual overrides.
3. Event level: team appearance prototypes are learnt from frames whose identity
   is already certain (OCR-anchored, posterior > 0.9) across ALL matches, then
   the HMMs are re-run with appearance evidence (helps tracks with few reads).

Matches processed with bumper OCR inside the tracker (process.process(..., teams=...),
tracks carry .team / .team_conf / per-frame .conf and the match has "identity") are
already one team per track: assign_event passes their labels through (method
"ocr-ilp"), applying manual overrides, and only falls back to the solvers below when
the labels are inconsistent with TBA (wrong team set, duplicate team, alliance mix-up).

Matches without time-series evidence (old JSONs: feat has no "ocr"/"app") fall
back to the legacy per-track solver -- which measured at chance level (34% of
tracks right vs 33% random), so its confidence is capped at 0.2.

Manual overrides (data/events/<key>/overrides.json:
{"<match_key>": {"red": {"<track idx>": "frc1234", ...}}}) are honoured and anchor
the solve (the track is that team for the whole match).

Output of assign_event (per match, per alliance) -- v1 keys unchanged:
  labels      {track idx: team}  dominant team of each track (one team per track)
  confidence  mean posterior that the labels are right over observed frames
  manual      bool
  segments    {track idx: [[i0, i1, team, conf], ...]}  frames [i0, i1) at the
              match JSON's hz: who the track really is over time (use this to
              attribute per-frame metrics; a track is often two robots)
  track_conf  {track idx: fraction of the track's observed frames that are its label}
  method      "hmm" | "legacy"
  alliance_flags {track idx: [[opponent team, n reads, i0, i1], ...]}  (hmm only) the
              track followed an opposing robot for a while -- a tracker alliance error
"""
import itertools, json, os, numpy as np

# ------------------------------------------------------------------ legacy solver (fallback)
W_HIST, W_H, W_START, W_CLIMB = 1.0, 1.0, 0.6, 1.5
SIG_H, SIG_START = 0.08, 0.8
TOWER = {"red": np.array([16.0, 4.05]), "blue": np.array([0.55, 4.0])}
LEGACY_CONF_CAP = 0.2


def _bhat(p, q):
    return float(1.0 - np.sqrt(np.clip(p, 0, None) * np.clip(q, 0, None)).sum())


def _near_tower(track, key):
    xy = track["feat"].get(key)
    if xy is None: return None
    return np.linalg.norm(np.array(xy) - TOWER[track["alliance"]]) < 1.6


def _tower_cost(track, climbed, key):
    near = _near_tower(track, key)
    if climbed is None or near is None:
        return 0.0
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
    """Legacy per-track solver.  groups: [{key, alliance, teams[3], tracks[...], climb{team: bool|None}}]."""
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
        conf = 1.0 if i in fixed else min(LEGACY_CONF_CAP, float(1 - np.exp(-margin / 2.0)))
        out.append(dict(key=g["key"], alliance=g["alliance"], perm=a, confidence=round(conf, 3),
                        manual=i in fixed))
    return out, tot


def climb_flags(match, alliance):
    """TBA per-station tower result -> {team: (auto bool|None, endgame bool|None)}."""
    sb = (match.get("score_breakdown") or {}).get(alliance) or {}
    teams = match["alliances"][alliance]["team_keys"]
    out = {}
    for i, t in enumerate(teams):
        a, e = sb.get(f"autoTowerRobot{i + 1}"), sb.get(f"endGameTowerRobot{i + 1}")
        out[t] = (None if a is None else a not in ("None", ""), None if e is None else e not in ("None", ""))
    return out


# ------------------------------------------------------------------ per-frame HMM
HMM = dict(
    q_ocr=0.97,          # P(an OCR read names the robot that is really on the track)
    lp_min=-8.0,         # CTC log-prob floor for a read to count
    p_swap=0.15,         # per-step (1/hz) swap probability of two tracks in contact
    d_swap=1.1,          # m: centre distance at which swapping becomes likely (robots touch ~0.9-1.0 m)
    w_swap=0.15,         # m: softness of that threshold
    p_floor=1e-4,        # swap probability of two far-apart tracks (tracker re-acquisition)
    unobs_bonus=0.7,     # m: an unobserved (coasting) track's position is uncertain
    p_unused=1e-4,       # a 1-2 track alliance: a track can pick up the untracked robot
    tau_app=0.05,        # appearance: softmax temperature on Bhattacharyya distance
    w_app=0.02,          # per-frame weight (frames are strongly correlated; >0.05 hurt in tests)
    app_post=0.9,        # frames with posterior above this feed the team prototypes
    app_min_frames=5,    # a team needs this many confident frames for a prototype
    w_climb=2.0,         # nats: TBA tower result vs. track at the tower at auto end / match end
    w_manual=5.0,        # nats per observed frame for a manual override
)


def _logsumexp(a, axis):
    m = np.max(a, axis=axis, keepdims=True)
    m = np.where(np.isfinite(m), m, 0.0)
    return np.squeeze(m, axis) + np.log(np.sum(np.exp(a - m), axis=axis))


def _app_vec(v):
    v = np.asarray(v, float)
    return v / max(v.sum(), 1e-9)


def group_hmm(xy, obs, teams, E, P=HMM):
    """xy: per track (T,2) metres; obs: per track (T,) bool; teams: alliance team keys;
    E: (T, S) log emissions over `states`.  Returns (viterbi path, states, posteriors)."""
    n = len(xy); T = E.shape[0]
    states = group_states(teams, n)
    S = len(states); sidx = {s: i for i, s in enumerate(states)}
    pairs = [(a, b) for a in range(n) for b in range(a + 1, n)]
    psw = {}
    for a, b in pairs:
        d = np.linalg.norm(xy[a][:T] - xy[b][:T], axis=1)
        d = d - P["unobs_bonus"] * ~(obs[a][:T] & obs[b][:T])
        psw[(a, b)] = P["p_swap"] / (1 + np.exp(np.clip((d - P["d_swap"]) / P["w_swap"], -50, 50))) + P["p_floor"]
    A = np.zeros((T, S, S))                   # A[t] = transition t -> t+1 (probabilities)
    for s, st in enumerate(states):
        out = np.zeros(T)
        for a, b in pairs:
            st2 = list(st); st2[a], st2[b] = st2[b], st2[a]
            A[:, s, sidx[tuple(st2)]] += psw[(a, b)]; out += psw[(a, b)]
        if n < len(teams):
            for a in range(n):
                for u in (t for t in teams if t not in st):
                    st2 = list(st); st2[a] = u
                    A[:, s, sidx[tuple(st2)]] += P["p_unused"]; out += P["p_unused"]
        A[:, s, s] = np.maximum(1 - out, 1e-9)
    with np.errstate(divide="ignore"):
        lA = np.log(A)
    # Viterbi
    v = E[0] - np.log(S); bp = np.zeros((T, S), int)
    for t in range(1, T):
        m = v[:, None] + lA[t - 1]
        bp[t] = m.argmax(0); v = m.max(0) + E[t]
    path = np.zeros(T, int); path[-1] = int(v.argmax())
    for t in range(T - 1, 0, -1): path[t - 1] = bp[t, path[t]]
    # forward-backward
    f = np.zeros((T, S)); f[0] = E[0] - np.log(S)
    for t in range(1, T): f[t] = _logsumexp(f[t - 1][:, None] + lA[t - 1], 0) + E[t]
    b = np.zeros((T, S))
    for t in range(T - 2, -1, -1): b[t] = _logsumexp(lA[t] + (E[t + 1] + b[t + 1])[None, :], 1)
    post = f + b; post = np.exp(post - _logsumexp(post, 1)[:, None])
    return path, states, post


def group_states(teams, n):
    return list(itertools.permutations(teams, n))


def _emissions(g, P, protos=None, overrides=None):
    """Log-emission matrix (T, S) for a group from OCR reads, appearance, climbs, overrides."""
    tracks, teams = g["tracks"], g["teams"]
    n = len(tracks); T = g["T"]; states = group_states(teams, n)
    S = len(states)
    on = np.array([[st[k] == t for t in teams] for st in states for k in range(n)]).reshape(S, n, len(teams))
    E = np.zeros((T, S))
    lq, lnq = np.log(P["q_ocr"]), np.log((1 - P["q_ocr"]) / max(len(teams) - 1, 1))
    tix = {t: j for j, t in enumerate(teams)}
    for k, tr in enumerate(tracks):
        f = tr["feat"]
        for i, team, lp in f.get("ocr") or []:
            if team not in tix or lp < P["lp_min"] or i >= T: continue
            E[i] += np.where(on[:, k, tix[team]], lq, lnq)
        if protos:
            have = [t for t in teams if t in protos]
            if len(have) >= 2:
                M = np.array([protos[t] for t in have])
                for i, v in f.get("app") or []:
                    if i >= T: continue
                    d = 1 - np.sqrt(np.outer(np.ones(len(have)), _app_vec(v)) * M).sum(1)
                    ll = -d / P["tau_app"]; ll = ll - _logsumexp(ll, 0)
                    full = np.full(len(teams), np.log(1.0 / len(teams)))
                    for j, t in enumerate(have): full[tix[t]] = ll[j] + np.log(len(have) / len(teams))
                    E[i] += P["w_app"] * (on[:, k, :] @ full)
        # TBA climb: is this track at the tower at auto end / match end?
        hz = g["hz"]
        for key, frame, ci in (("auto_end_xy", int(g["t_auto_end"] * hz), 0), ("end_xy", T - 1, 1)):
            near = _near_tower(tr, key)
            if near is None or frame >= T: continue
            for t in teams:
                cl = (g["climb"].get(t) or (None, None))[ci]
                if cl is None: continue
                if bool(cl) != bool(near):
                    E[frame] -= P["w_climb"] * (0.5 if near else 1.0) * on[:, k, tix[t]]
    if overrides:
        for k, tr in enumerate(tracks):
            team = overrides.get(str(tr["idx"])) or overrides.get(tr["idx"])
            if team in tix:
                ob = np.asarray(tr["obs"][:T]) > 0
                E[ob] -= P["w_manual"] * ~on[:, k, tix[team]][None, :]
    return E


def _decode(g, path, states, post, manual):
    """Viterbi path -> labels (one team per track), segments, confidences."""
    n = len(g["tracks"]); teams = g["teams"]; T = g["T"]
    lab_t = np.array([[states[s][k] for k in range(n)] for s in path], dtype=object)   # (T, n)
    marg = {}
    for k in range(n):
        for t in teams:
            marg[(k, t)] = post[:, [s for s, st in enumerate(states) if st[k] == t]].sum(1)
    obs = [np.asarray(tr["obs"][:T]) > 0 for tr in g["tracks"]]
    # one label per track: maximise observed frames covered (assignment over teams)
    cnt = np.array([[(lab_t[:, k][obs[k]] == t).sum() for t in teams] for k in range(n)])
    best = max(itertools.permutations(range(len(teams)), n), key=lambda p: sum(cnt[k, p[k]] for k in range(n)))
    labels, segs, tconf = {}, {}, {}
    for k, tr in enumerate(g["tracks"]):
        t = teams[best[k]]; labels[tr["idx"]] = t
        tconf[tr["idx"]] = round(float(cnt[k, best[k]] / max(obs[k].sum(), 1)), 3)
        sg, i0 = [], 0
        for i in range(1, T + 1):
            if i == T or lab_t[i, k] != lab_t[i0, k]:
                tm = str(lab_t[i0, k])
                sg.append([i0, i, tm, round(float(marg[(k, tm)][i0:i].mean()), 3)])
                i0 = i
        segs[tr["idx"]] = sg
    w = np.concatenate([obs[k] for k in range(n)]).astype(float)
    pc = np.concatenate([marg[(k, labels[g["tracks"][k]["idx"]])] for k in range(n)])
    conf = 1.0 if manual else float((pc * w).sum() / max(w.sum(), 1))
    # reads naming an OPPONENT team: the tracker put the wrong alliance on (part of) a track
    flags = {}
    opp = set(g.get("opp_teams") or [])
    for tr in g["tracks"]:
        rr = [r for r in tr["feat"].get("ocr") or [] if r[1] in opp and r[2] >= -4]
        for t in sorted({r[1] for r in rr}):
            fr = [r[0] for r in rr if r[1] == t]
            if len(fr) >= 8:
                flags.setdefault(tr["idx"], []).append([t, len(fr), min(fr), max(fr) + 1])
    return dict(labels=labels, confidence=round(conf, 3), manual=manual, segments=segs,
                track_conf=tconf, method="hmm", alliance_flags=flags), marg


def _has_ts(tracks):
    return all(("ocr" in t["feat"]) or ("app" in t["feat"]) for t in tracks)


def solve_hmm(groups, overrides=None, P=HMM, rounds=2):
    """groups with time-series evidence -> per-group decode dicts (same order)."""
    protos = None
    for rnd in range(rounds):
        out, conf_frames = [], {}
        for g in groups:
            ov = (overrides or {}).get(g["key"], {}).get(g["alliance"])
            E = _emissions(g, P, protos, ov)
            xy = [np.asarray(t["xy"], float)[:g["T"]] / 100.0 for t in g["tracks"]]
            ob = [np.asarray(t["obs"][:g["T"]]) > 0 for t in g["tracks"]]
            path, states, post = group_hmm(xy, ob, g["teams"], E, P)
            res, marg = _decode(g, path, states, post, bool(ov))
            out.append(res)
            # appearance samples whose identity is certain -> team prototypes
            for k, tr in enumerate(g["tracks"]):
                for i, v in tr["feat"].get("app") or []:
                    if i >= g["T"]: continue
                    for t in g["teams"]:
                        if marg[(k, t)][i] > P["app_post"]:
                            conf_frames.setdefault(t, []).append(_app_vec(v))
        protos = {t: np.mean(v, 0) for t, v in conf_frames.items() if len(v) >= P["app_min_frames"]}
        if not protos:
            break
    return out


def _conf_segments(conf, team):
    """Per-frame conf -> [[i0, i1, team, mean conf]] runs (conf 0 = unknown, kept as its own run)."""
    c = np.asarray(conf, float); T = len(c); out, i0 = [], 0
    if T == 0: return out
    z = c <= 0
    for i in range(1, T + 1):
        if i == T or z[i] != z[i0]:
            out.append([i0, i, team, round(float(c[i0:i].mean()), 3)]); i0 = i
    return out


def labeled_match(res, m, overrides=None):
    """Pass-through for tracks labelled by the tracker (track["team"]).  None if the match
    isn't labelled or the labels contradict TBA (then the solvers handle it)."""
    tr = res.get("tracks") or []
    if not tr or not all(t.get("team") for t in tr):
        return None
    out = {}
    for a in ("red", "blue"):
        tm = list(m["alliances"][a]["team_keys"])
        tk = [t for t in tr if t["alliance"] == a]
        if len(tm) != 3 or sorted(t["team"] for t in tk) != sorted(tm):
            return None
        labels = {t["idx"]: t["team"] for t in tk}
        manual = False
        ov = (overrides or {}).get(a) or {}
        for ti, team in ov.items():          # manual fix: relabel, swapping to keep teams unique
            ti = int(ti)
            if ti not in labels or team not in tm: continue
            other = next((j for j, x in labels.items() if x == team), None)
            if other is not None and other != ti: labels[other] = labels[ti]
            labels[ti] = team; manual = True
        w = np.array([max(float(np.mean(t["obs"])), 1e-6) for t in tk])
        tc = np.array([float(t.get("team_conf") or 0.0) for t in tk])
        segs, tconf = {}, {}
        for t in tk:
            team = labels[t["idx"]]
            moved = team != t["team"]
            c = t.get("conf") or [t.get("team_conf") or 0.0] * len(t["xy"])
            if moved: c = [1.0] * len(c)
            segs[t["idx"]] = _conf_segments(c, team)
            tconf[t["idx"]] = 1.0 if moved else round(float(t.get("team_conf") or 0.0), 3)
        out[a] = dict(labels=labels, confidence=1.0 if manual else round(float((w * tc).sum() / w.sum()), 3),
                      manual=manual, segments=segs, track_conf=tconf, method="ocr-ilp", alliance_flags={})
    return out


def assign_event(results, tba_matches, overrides=None):
    """results: {match_key: processed JSON}.  Returns {match_key: {alliance: {labels, confidence,
    manual, segments, track_conf, method}}} (see module docstring)."""
    groups, teams = [], set()
    mm = {m["key"]: m for m in tba_matches}
    pre = {}
    for k, res in results.items():
        m = mm.get(k)
        if not m: continue
        lab = labeled_match(res, m, (overrides or {}).get(k))
        if lab is not None:
            pre[k] = lab
            continue
        for a in ("red", "blue"):
            tk = [t for t in res["tracks"] if t["alliance"] == a]
            tm = m["alliances"][a]["team_keys"]
            if not tk or len(tm) != 3: continue
            if len(tk) > 3:                       # keep the 3 best-observed tracks
                tk = sorted(tk, key=lambda t: -float(np.mean(t["obs"])))[:3]
            teams.update(tm)
            T = min(len(t["xy"]) for t in tk)
            opp = m["alliances"]["blue" if a == "red" else "red"]["team_keys"]
            groups.append(dict(key=k, alliance=a, teams=tm, tracks=tk, climb=climb_flags(m, a), T=T, opp_teams=opp,
                               hz=res.get("hz", 10), t_auto_end=res.get("t_auto_end", 20.5)))
    out = {}
    for k in pre:
        out[k] = pre[k]
    if not groups:
        return out
    ts = [g for g in groups if _has_ts(g["tracks"])]
    legacy = [g for g in groups if not _has_ts(g["tracks"])]
    if ts:
        for g, r in zip(ts, solve_hmm(ts, overrides)):
            out.setdefault(g["key"], {})[g["alliance"]] = r
    if legacy:
        sol, _ = solve(legacy, sorted(teams), overrides)
        for g, s in zip(legacy, sol):
            lab = {}
            for ti, team in zip(s["perm"], g["teams"]):
                if ti is not None: lab[g["tracks"][ti]["idx"]] = team
            out.setdefault(g["key"], {})[g["alliance"]] = dict(
                labels=lab, confidence=s["confidence"], manual=s["manual"],
                segments={g["tracks"][ti]["idx"]: [[0, g["T"], team, s["confidence"]]]
                          for ti, team in zip(s["perm"], g["teams"]) if ti is not None},
                track_conf={}, method="legacy")
    return out


# ------------------------------------------------------------------ feature extraction (video pass)
OCR_DIRS = [os.environ.get("AUTOSCOUT_OCR_DIR", ""),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "ocr"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".work", "identity", "models")]
_OCR = {}


def _ocr_sessions():
    """PP-OCRv3 det + rec (from rapidocr_onnxruntime 1.2.3, Apache-2.0), loaded once."""
    if _OCR: return _OCR
    import onnxruntime as ort
    d = next((p for p in OCR_DIRS if p and os.path.exists(os.path.join(p, "ch_PP-OCRv3_rec_infer.onnx"))), None)
    if d is None:
        raise FileNotFoundError("OCR models not found; set AUTOSCOUT_OCR_DIR (see identity.py)")
    so = ort.SessionOptions(); so.log_severity_level = 3
    so.intra_op_num_threads = int(os.environ.get("AUTOSCOUT_OCR_THREADS", "2"))
    prov = ["CPUExecutionProvider"]
    if os.environ.get("AUTOSCOUT_OCR_CUDA") is not None and "CUDAExecutionProvider" in ort.get_available_providers():
        prov = [("CUDAExecutionProvider", {"device_id": int(os.environ["AUTOSCOUT_OCR_CUDA"])}), "CPUExecutionProvider"]
    _OCR["det"] = ort.InferenceSession(os.path.join(d, "ch_PP-OCRv3_det_infer.onnx"), so, providers=prov)
    _OCR["rec"] = ort.InferenceSession(os.path.join(d, "ch_PP-OCRv3_rec_infer.onnx"), so, providers=prov)
    chars = ["<b>"] + _OCR["rec"].get_modelmeta().custom_metadata_map["character"].splitlines() + [" "]
    cidx = {}
    for i, ch in enumerate(chars): cidx.setdefault(ch, i)
    _OCR["cidx"] = cidx
    return _OCR


def _det_boxes(img, thr=0.25, box_thr=0.4, unclip=1.8):
    import cv2
    det = _ocr_sessions()["det"]
    h, w = img.shape[:2]
    H, W = max(32, int(round(h / 32)) * 32), max(32, int(round(w / 32)) * 32)
    x = cv2.resize(img, (W, H)).astype(np.float32) / 255.
    x = (x - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
    p = det.run(None, {det.get_inputs()[0].name: x.transpose(2, 0, 1)[None].astype(np.float32)})[0][0, 0]
    m = (p > thr).astype(np.uint8)
    cs, _ = cv2.findContours(m, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cs:
        if len(c) < 4: continue
        mask = np.zeros_like(m); cv2.drawContours(mask, [c], -1, 1, -1)
        if cv2.mean(p, mask=mask)[0] < box_thr: continue
        (cx, cy), (rw, rh), a = cv2.minAreaRect(c)
        if min(rw, rh) < 2: continue
        dd = rw * rh * unclip / (2 * (rw + rh))
        out.append(((cx * w / W, cy * h / H), ((rw + 2 * dd) * w / W, (rh + 2 * dd) * h / H), a))
    return out


def _warp(img, r):
    import cv2
    (cx, cy), (rw, rh), a = r
    if rw < rh: rw, rh, a = rh, rw, a + 90
    M = cv2.getRotationMatrix2D((cx, cy), a, 1.0)
    rot = cv2.warpAffine(img, M, (img.shape[1], img.shape[0]), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return cv2.getRectSubPix(rot, (int(rw), int(rh)), (cx, cy))


def _rec_logp(im):
    import cv2
    rec = _ocr_sessions()["rec"]
    h, w = im.shape[:2]
    nw = int(min(320, max(16, np.ceil(48 * w / max(h, 1)))))
    x = (cv2.resize(im, (nw, 48)).astype(np.float32) / 255. - 0.5) / 0.5
    pad = np.zeros((48, 320, 3), np.float32); pad[:, :nw] = x
    p = rec.run(None, {rec.get_inputs()[0].name: pad.transpose(2, 0, 1)[None]})[0][0]
    return np.log(np.clip(p[:min(int(np.ceil(nw / 8)) + 1, len(p))], 1e-9, 1))


def _ctc(lp, label):
    """log P(label | per-step log probs) -- CTC forward, vectorised over the extended label."""
    ext = np.array([0] + [x for l in label for x in (l, 0)])
    S = len(ext)
    skip = np.zeros(S, bool); skip[2:] = (ext[2:] != 0) & (ext[2:] != ext[:-2])
    a = np.full(S, -np.inf); a[0] = lp[0, ext[0]]; a[1] = lp[0, ext[1]]
    for t in range(1, len(lp)):
        a1 = np.r_[-np.inf, a[:-1]]; a2 = np.where(skip, np.r_[-np.inf, -np.inf, a[:-2]], -np.inf)
        a = np.logaddexp(np.logaddexp(a, a1), a2) + lp[t, ext]
    return float(np.logaddexp(a[-1], a[-2]))


def read_bumpers(crop, cands, up=3.0, min_lp=-8.0):
    """BGR robot crop + candidate team numbers (strings) -> [(cand index, log P)] per text box."""
    import cv2
    O = _ocr_sessions()
    labels = [[O["cidx"][c] for c in s] for s in cands]
    big = cv2.resize(crop, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
    res = []
    for r in _det_boxes(big):
        if max(r[1]) < 30: continue
        w = _warp(big, r)
        if w.size == 0: continue
        best = None
        for im in (w, cv2.rotate(w, cv2.ROTATE_180)):
            lp = _rec_logp(im)
            s = [_ctc(lp, l) for l in labels]
            j = int(np.argmax(s))
            if best is None or s[j] > best[1]: best = (j, s[j])
        if best[1] > min_lp: res.append(best)
    return res


def app_desc(crop):
    """64-bin RGB histogram of robot pixels (fuel, red/blue bumper and carpet masked), or None."""
    import cv2
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    H, S, V = [hsv[..., i].astype(int) for i in range(3)]
    junk = (((H >= 18) & (H <= 38) & (S > 90) & (V > 110))           # fuel
            | (((H < 8) | (H > 170)) & (S > 110))                       # red bumper
            | ((H >= 100) & (H <= 125) & (S > 110) & (V > 60))          # blue bumper
            | ((S < 40) & (V > 60) & (V < 120)))                        # carpet
    m = ~junk
    if m.sum() < 50: return None
    x = (crop[m] // 64).astype(int)
    d = np.bincount(x[:, 0] * 16 + x[:, 1] * 4 + x[:, 2], minlength=64).astype(float)
    return d / d.sum()


def extract_features(video, cam, match, teams, step_s=0.5, iso_m=1.3, up=3.0, max_frames=None, log=print):
    """Second pass over a match video -> per-track identity evidence.

    video: path; cam: live.camera.Camera with the match's pose (match["camera_pose"]);
    match: processed match JSON (t_start, hz, tracks[].xy/obs) as written by process.py;
    teams: the match's team keys (TBA, red + blue, any order) -- OCR scores against these.
    Returns {track idx: {"ocr": [[i, team, logp], ...], "app": [[i, [64 x uint8]], ...]}}
    with i = frame index at match["hz"]; merge into tracks[idx]["feat"].
    Cost: ~3-4 CPU-minutes per match at step_s=0.5, up=3 (OCR dominates).  More reads
    (= better identity): up=4 gave ~1.45x the reads at ~1.8x the cost; step_s=0.2 ~2.5x.
    max_frames: stop after that many sampled frames (testing)."""
    import av, cv2
    hz = match["hz"]; v0 = match["t_start"] - 0.5
    XY = [np.asarray(t["xy"], float) / 100.0 for t in match["tracks"]]
    OB = [np.asarray(t["obs"]) > 0 for t in match["tracks"]]
    L = min(len(x) for x in XY)
    cands = [t[3:] for t in teams]
    want = set(range(0, L, max(1, int(round(step_s * hz)))))
    out = {t["idx"]: {"ocr": [], "app": []} for t in match["tracks"]}
    c = av.open(video); s = c.streams.video[0]; s.thread_type = "AUTO"
    if v0 > 2: c.seek(int((v0 - 1) * 1e6), any_frame=False, backward=True)
    done = set()
    for f in c.decode(s):
        if f.time is None: continue
        i = int(round((f.time - v0) * hz))
        if i >= L or (max_frames and len(done) >= max_frames): break
        if i < 0 or i not in want or i in done: continue
        done.add(i)
        im = f.to_ndarray(format="bgr24")
        if float(im[::32, ::32].mean()) < 20: continue          # feed missing
        for k, tr in enumerate(match["tracks"]):
            if not OB[k][i]: continue
            x, y = XY[k][i]
            dmin = min([np.linalg.norm(XY[k][i] - XY[j][i]) for j in range(len(XY)) if j != k] or [9.0])
            P = np.array([[x + dx, y + dy, z] for dx in (-.6, .6) for dy in (-.6, .6) for z in (0, .5)])
            p = cam.project(P)
            x0, y0 = np.floor(p.min(0)).astype(int) - 4; x1, y1 = np.ceil(p.max(0)).astype(int) + 4
            crop = im[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
            if crop.shape[0] < 20 or crop.shape[1] < 20: continue
            for j, lp in read_bumpers(crop, cands, up=up):
                out[tr["idx"]]["ocr"].append([i, teams[j], round(lp, 2)])
            if dmin >= iso_m:
                P = np.array([[x + dx, y + dy, z] for dx in (-.7, .7) for dy in (-.7, .7) for z in (0, .6)])
                p = cam.project(P)
                x0, y0 = np.floor(p.min(0)).astype(int); x1, y1 = np.ceil(p.max(0)).astype(int)
                bw, bh = x1 - x0, y1 - y0
                x0, x1 = x0 + int(.1 * bw), x1 - int(.1 * bw); y0, y1 = y0 + int(.1 * bh), y1 - int(.1 * bh)
                d = app_desc(im[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]) if x1 > x0 + 8 and y1 > y0 + 8 else None
                if d is not None:
                    out[tr["idx"]]["app"].append([i, np.round(d * 255).astype(int).tolist()])
    c.close()
    log(f"  identity features: {len(done)} frames, "
        f"{sum(len(v['ocr']) for v in out.values())} bumper reads, "
        f"{sum(len(v['app']) for v in out.values())} appearance samples")
    return out
