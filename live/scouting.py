"""Scouting intelligence: per-team spec sheets + the defense library.

Reads the site JSON the worker already wrote (api/<ev>/{event.json, m/*.json, t/*.json}) and writes
    api/<ev>/spec/index.json          one row per team (for the list page)
    api/<ev>/spec/<team>.json         spec sheet: drive, scoring, volleys/hopper, autos, climb, lanes,
                                      shooting locations, shooting paths (drive segment into each volley)
    api/<ev>/defense.json             defense episodes + effect vs the target's own baseline,
                                      per-target "what works against X", per-defender stats, clip data

Worker hook:  site_build_extra(DATA, ev)  (call after build_site; pure post-processing, ~1-3 s/event)
CLI:          python -m live.scouting <DATA> <ev> [--out <api_ev_dir>]

Conventions
-----------
* Frames are the match JSON's replay frames (hz, 10 Hz).  replay t = i/hz - 0.5 (s from auto start);
  video t = t_start - 0.5 + i/hz; match clock = analytics.match_clock (pause after auto frozen at 20).
* IDENTITY QUARANTINE: a robot-frame counts for its team only when tracks[k].conf[i] >= ID_CONF_MIN (0.5)
  and the robot is observed (or inside a <= 1.5 s gap between such frames).  Shots count only when the
  site already credited them to the team (team != null; the worker nulls shots launched under conf < 0.5).
* TEAM FRAME (spec sheets): canonical red frame - blue positions point-reflected, own alliance wall at
  x = 16.54, drawn on the web with the own end on the LEFT.  Driver's LEFT = low y (top of the drawing).
* Clips / episodes keep the absolute field frame (same as the video).

Defense effect (per episode)
----------------------------
Observed: target's made fuel launched in the episode while its hub was active (and attempts).
Expected: the same robot's own output in comparable undefended time - every window of the same length
from its tracked matches (no opponent within 2 m) whose zone mix (alliance zone / lane band / own-half
neutral) and hub-active share match the episode ("placebo windows").  The windows give an empirical
null distribution that keeps the burstiness of volleys, so the interval and p-value are honest for
small counts.  Effect = (obs - exp) / active-hub seconds with a 90 % interval from the null quantiles.
Benjamini-Hochberg q-values over all testable episodes guard against cherry-picking.
"""
import argparse
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict

import numpy as np

try:
    from live import coverage as COV
except ImportError:          # run as a script from live/
    import coverage as COV

LEN, WID = 16.541, 8.069
CX = LEN / 2
ID_CONF_MIN = float(os.environ.get("ID_CONF_MIN", "0.5"))
GAP_MAX_S = 1.5
V_GLITCH = 6.0
V_MOVING = 0.3
V_SHOOT_MOVING = 0.5
VOLLEY_GAP_S = 1.2
HERE = os.path.dirname(os.path.abspath(__file__))

# defense parameters
DEF_R = 1.5            # m centre distance: "within ~1.5 m"
DEF_MIN_S = 2.0        # s of closeness
DEF_MERGE_S = 1.0      # gaps bridged inside one episode
DEF_JOIN_S = 3.0       # same pair re-engaging within this -> one episode
CONTACT_R = 1.0        # m centre distance ~ bumpers touching (0.91 m robots)
FREE_R = 2.0           # baseline frames: no opponent within this
PAD_S = 3.0            # clip padding
EMBED_CLIPS = 30       # episodes with embedded clip data (successful first, then by fuel denied)

PERIODS = [("auto", 0, 20), ("transition", 20, 30), ("shift1", 30, 55), ("shift2", 55, 80),
           ("shift3", 80, 105), ("shift4", 105, 130), ("endgame", 130, 160)]


# ================================================================================ small helpers
def jload(p, default=None):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return default


def jsave(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, separators=(",", ":"), allow_nan=False)
    os.replace(tmp, p)


def r_(x, nd=2):
    if x is None:
        return None
    try:
        x = float(x)
    except Exception:
        return None
    return round(x, nd) if math.isfinite(x) else None


def runs(mask, min_len=1, merge_gap=0):
    """[(i0, i1_excl)] of True runs, bridging gaps of <= merge_gap frames."""
    m = np.asarray(mask, bool)
    if not m.any():
        return []
    d = np.diff(np.r_[0, m.astype(np.int8), 0])
    st, en = np.where(d == 1)[0], np.where(d == -1)[0]
    out = []
    for a, b in zip(st, en):
        if out and a - out[-1][1] <= merge_gap:
            out[-1][1] = b
        else:
            out.append([a, b])
    return [(int(a), int(b)) for a, b in out if b - a >= min_len]


def canon(xy, al):
    xy = np.asarray(xy, float)
    if al == "blue":
        return np.stack([LEN - xy[..., 0], WID - xy[..., 1]], -1)
    return xy


def wilson(k, n, z=1.645):
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [r_(c - h, 3), r_(c + h, 3)]


def short_of(key):
    k = key.split("_")[-1]
    return k.upper().replace("QM", "Q")


def bh_q(ps):
    """Benjamini-Hochberg q-values (list with None passthrough)."""
    idx = [i for i, p in enumerate(ps) if p is not None]
    q = [None] * len(ps)
    if not idx:
        return q
    order = sorted(idx, key=lambda i: ps[i])
    m = len(order)
    prev = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        prev = min(prev, ps[i] * m / rank)
        q[i] = prev
    return q


# ================================================================================ geometry
class Geo:
    def __init__(self, rules=None):
        r = rules or jload(os.path.join(HERE, "field", "rebuilt_rules.json"), {}) or {}
        hubs = (r.get("hub") or {}).get("centers") or {}
        self.hub_red = tuple(hubs.get("red", (11.915, 4.035)))
        self.L_own = self.hub_red[0]                     # canonical own hub line
        self.L_opp = LEN - self.L_own
        zn = r.get("zones") or {}
        self.Z_own = float(zn.get("red_alliance_x_min", 12.513))
        # lane bands along y (from rules polygons on the red side; field is point-symmetric)
        tr = [p for p in (r.get("trenches") or []) if p.get("side") == "red"]
        bu = [p for p in (r.get("bumps") or []) if p.get("side") == "red"]
        self.bands = []
        for kind, items in (("trench", tr), ("bump", bu)):
            for it in items:
                ys = [q[1] for q in it["polygon"]]
                xs = [q[0] for q in it["polygon"]]
                self.bands.append((kind, min(ys), max(ys), min(xs), max(xs)))
        if not self.bands:
            self.bands = [("trench", 0, 1.194, 11.08, 12.75), ("bump", 1.584, 3.438, 11.35, 12.48),
                          ("bump", 4.631, 6.486, 11.35, 12.48), ("trench", 6.876, WID, 11.08, 12.75)]
        self.lane_x0 = min(b[3] for b in self.bands)      # lane band x extent (canonical own side)
        self.lane_x1 = max(b[4] for b in self.bands)
        tw = ((r.get("towers") or {}).get("red") or {}).get("centre_m") or [16.54, 4.108]
        self.tower = (float(tw[0]), float(tw[1]))
        op = ((r.get("outposts") or {}).get("red") or {}).get("centre_m") or [16.54, 7.187]
        self.outpost = (float(op[0]), float(op[1]))
        ar = ((r.get("outposts") or {}).get("red") or {}).get("area_m") or {"x": [13.1365, 16.5405], "y": [6.2666, 8.0696]}
        self.outpost_area = (tuple(ar["x"]), tuple(ar["y"]))

    def lane(self, y):
        """Lane at a hub-line crossing (canonical y): nearest band; 'open' only if far from every band."""
        best, bd = "open", 1e9
        for kind, y0, y1, _, _ in self.bands:
            d = 0 if y0 <= y <= y1 else min(abs(y - y0), abs(y - y1))
            if d < bd:
                best, bd = kind, d
        return best if bd < 0.6 else "open"

    @staticmethod
    def side(y):
        return "L" if y < WID / 2 else "R"     # driver's left = low canonical y

    def zone_class(self, cx):
        """0 alliance zone, 1 hub/lane band, 2 own-half neutral, 3 opponent half (canonical x)."""
        cx = np.asarray(cx, float)
        return np.where(cx >= self.Z_own, 0, np.where(cx >= self.lane_x0, 1, np.where(cx >= CX, 2, 3)))

    def zone_name(self, cxy):
        x, y = cxy
        s = "left" if y < WID / 2 else "right"
        if x >= self.Z_own:
            if math.hypot(x - self.hub_red[0], y - self.hub_red[1]) < 2.2:
                return "hub", s
            return "alliance zone", s
        if x >= self.lane_x0:
            ln = self.lane(y)
            return (ln if ln != "open" else "hub"), s
        if x >= CX:
            return "neutral (own half)", s
        return "neutral (far half)", s


ZONE_CLASS_NAMES = ["alliance zone", "lane band", "own-half neutral", "far half"]


# ================================================================================ per-match prep
class Match:
    def __init__(self, d, geo):
        self.d = d
        self.key = d["key"]
        self.verified = COV.verified_teams(d)
        self.recon = COV.reconcile(d)
        # per-match gate for team FUEL counts: only alliance results within 20% of TBA (coverage.reconcile)
        self.fuel_ok = {al: (self.recon.get(al) or {}).get("gate") == "ok" for al in ("red", "blue")}
        self.short = d.get("short") or short_of(d["key"])
        self.hz = float(d.get("hz", 10))
        self.t_start = float(d["t_start"])
        self.v0 = self.t_start - 0.5
        self.N = min(len(t["xy"]) for t in d["tracks"]) if d.get("tracks") else 0
        N, hz = self.N, self.hz
        self.tr = np.arange(N) / hz - 0.5                           # replay time from auto start
        T = float(d.get("t_teleop_start", 23.5)) - 0.5
        self.T_tel = T
        self.mc = np.where(self.tr <= 20, self.tr, np.where(self.tr < T, 20.0, self.tr - (T - 20)))
        self.pause = (self.tr > 20) & (self.tr < T)
        self.vt = self.v0 + np.arange(N) / hz
        live = np.ones(N, bool)
        for a, b in d.get("feed_gaps") or []:
            live[int(max(0, a * hz)):int(min(N, b * hz + 1))] = False
        self.live = live
        # periods + hub active per alliance per frame
        per = d.get("periods") or [{"name": n, "t0": a, "t1": b, "active": {}} for n, a, b in PERIODS]
        self.per_name = np.array(["pre"] * N, dtype=object)
        self.active = {"red": np.zeros(N, bool), "blue": np.zeros(N, bool)}
        for p in per:
            m = (self.mc >= p["t0"]) & (self.mc < p["t1"]) & ~self.pause
            self.per_name[m] = p["name"]
            for al in ("red", "blue"):
                a = (p.get("active") or {}).get(al)
                self.active[al][m] = True if a is None else bool(a)
        self.per_name[self.pause] = "pause"
        self.teleop = (self.mc > 20) & ~self.pause & (self.mc <= 160)
        self.tracks = [Trk(self, k, geo) for k in range(len(d["tracks"]))]
        # launches per track (credited to the track's team only)
        self.launch = {k: [] for k in range(len(self.tracks))}
        for s in d.get("shots") or []:
            sh = s.get("shooter") or {}
            k = sh.get("track_idx")
            if sh.get("kind") != "robot" or k is None or k >= len(self.tracks):
                continue
            tk = self.tracks[k]
            if not s.get("team") or s.get("team") != tk.team:
                continue          # quarantined (or relabelled) - credited to no team
            f = int(round((float(s["t"]) - self.v0) * hz))
            if not (0 <= f < N):
                continue
            self.launch[k].append({"f": f, "t": float(s["t"]), "o": s.get("outcome") or "unknown",
                                   "pass": s.get("outcome") == "pass", "origin": s.get("origin"),
                                   "landing": s.get("landing"), "hub_active": s.get("hub_active"),
                                   "period": s.get("period")})
        for k in self.launch:
            self.launch[k].sort(key=lambda x: x["f"])
        self.intakes = {k: [] for k in range(len(self.tracks))}
        for it in d.get("intakes") or []:
            k = it.get("track_idx")
            if k is None or k >= len(self.tracks):
                continue
            f = int(round((float(it["t"]) - self.v0) * hz)) if it.get("t") is not None else None   # t = video s
            if f is not None and 0 <= f < N and self.tracks[k].ok[f]:
                self.intakes[k].append(f)
        # per-frame launch counts
        for k, L in self.launch.items():
            tk = self.tracks[k]
            made = np.zeros(N, np.int16)
            att = np.zeros(N, np.int16)
            pts = np.zeros(N, np.int16)
            for s in L:
                if s["pass"]:
                    continue
                att[s["f"]] += 1
                if s["o"] == "made":
                    made[s["f"]] += 1
                    if s["hub_active"] is not False and self.active[tk.alliance][s["f"]]:
                        pts[s["f"]] += 1
            tk.made, tk.att, tk.pts = made, att, pts


class Trk:
    def __init__(self, M, k, geo):
        t = M.d["tracks"][k]
        N, hz = M.N, M.hz
        self.k = k
        # trust rule (live/coverage.py): a track feeds its team's numbers only in a v2 match with team_conf >= VERIFIED_CONF
        self.team = t.get("team") if t.get("team") in M.verified else None
        self.label = t.get("team")
        self.alliance = t["alliance"]
        xy = np.asarray(t["xy"], float)[:N] / 100.0
        obs = np.asarray(t.get("obs") or np.ones(N), int)[:N] > 0
        cf = t.get("conf")
        if cf:
            conf = np.asarray(cf, float)[:N]
            if len(conf) < N:
                conf = np.r_[conf, np.zeros(N - len(conf))]
        else:   # v1 match without per-frame identity: the overall label confidence applies everywhere
            c0 = t.get("team_conf") if t.get("team_conf") is not None else t.get("id_conf")
            conf = np.full(N, float(c0 or 0.0))
        self.conf = conf
        base = obs & (conf >= ID_CONF_MIN) & M.live
        ok = base.copy()
        xi = xy.copy()
        idx = np.where(base)[0]
        if len(idx) >= 2:
            gaps = np.diff(idx)
            for j in np.where((gaps > 1) & (gaps <= GAP_MAX_S * hz))[0]:
                a, b = idx[j], idx[j + 1]
                if not M.live[a:b].all():
                    continue
                w = (np.arange(a + 1, b) - a) / (b - a)
                xi[a + 1:b] = xy[a][None] * (1 - w[:, None]) + xy[b][None] * w[:, None]
                ok[a + 1:b] = True
        self.ok = ok
        self.valid_frac = float(ok[(M.mc >= 0) & (M.mc <= 160)].mean()) if N else 0.0
        self.xy = xi
        # smoothing (5-pt mean on ok frames)
        sm = np.full_like(xi, np.nan)
        for c in range(2):
            v = np.where(ok, xi[:, c], np.nan)
            pad = np.pad(v, 2, constant_values=np.nan)
            win = np.stack([pad[i:i + N] for i in range(5)], 0)
            with np.errstate(all="ignore"), _quiet():
                sm[:, c] = np.where(ok, np.nanmean(win, 0), np.nan)
        self.sm = sm
        v = np.full((N, 2), np.nan)
        if N > 4:
            d = (sm[4:] - sm[:-4]) / (4.0 / hz)
            okw = ok[:-4] & ok[4:] & ok[2:-2]
            d[~okw] = np.nan
            v[2:-2] = d
        sp = np.hypot(v[:, 0], v[:, 1])
        glitch = sp > V_GLITCH
        sp[glitch] = np.nan
        v[glitch] = np.nan
        sp[sp < 0.08] = 0.0
        self.v = v
        self.speed = sp
        a = np.full(N, np.nan)
        if N > 4:
            a[2:-2] = np.hypot(*(v[4:] - v[:-4]).T) / (4.0 / hz)
        self.acc = a
        self.cxy = canon(xi, self.alliance)
        self.zc = geo.zone_class(self.cxy[:, 0])


class _quiet:
    def __enter__(self):
        import warnings
        self._w = warnings.catch_warnings()
        self._w.__enter__()
        warnings.simplefilter("ignore")

    def __exit__(self, *a):
        self._w.__exit__(*a)


# ================================================================================ crossings / volleys / paths
def crossings(M, tk, geo):
    """Hub-line crossings of the team-valid track (canonical frame), with lane + driver side."""
    hz = M.hz
    x, y = tk.cxy[:, 0], tk.cxy[:, 1]
    out = []
    H = 0.6
    for line, nm in ((geo.L_own, "own"), (geo.L_opp, "opp")):
        st, last_i = None, None
        for i in np.where(tk.ok & (M.mc >= 0) & (M.mc <= 160))[0]:
            s = 1 if x[i] > line + H else (-1 if x[i] < line - H else 0)
            if s == 0:
                continue
            if st is not None and s != st:
                if (i - last_i) <= 2.5 * hz:
                    seg = np.arange(last_i, i + 1)
                    seg = seg[tk.ok[seg]]
                    xs = x[seg] - line
                    j = int(np.argmin(np.abs(xs)))
                    yc = float(y[seg[j]])
                    lane = geo.lane(yc)
                    out.append({"f": int(seg[j]), "t": r_(M.mc[seg[j]], 1), "line": nm,
                                "dir": "in" if s > 0 else "out",    # in = toward own wall
                                "lane": lane, "side": geo.side(yc), "y": r_(yc, 2)})
                else:
                    out.append({"f": int(i), "t": r_(M.mc[i], 1), "line": nm, "dir": "in" if s > 0 else "out",
                                "lane": "unknown", "side": None, "y": None})
            st, last_i = s, i
    out.sort(key=lambda c: c["f"])
    return out


def volleys(launches, hz):
    vol = []
    for s in launches:
        if vol and (s["f"] - vol[-1][-1]["f"]) / hz <= VOLLEY_GAP_S:
            vol[-1].append(s)
        else:
            vol.append([s])
    return vol


def shooting_paths(M, tk, vols, cross, geo, prev_end_f=None):
    """Drive segment leading into each volley (start = latest of: previous volley end, last intake,
    neutral-zone exit, 10 s cap, last >1 s tracking hole) + the volley itself, 5 Hz, canonical cm."""
    hz = M.hz
    out = []
    intakes = sorted(M.intakes.get(tk.k, []))
    prev_end = None
    for v in vols:
        f1, f2 = v[0]["f"], v[-1]["f"]
        if not tk.ok[f1]:
            prev_end = f2
            continue
        cands = {"cap": f1 - int(10 * hz)}
        if prev_end is not None:
            cands["after volley"] = prev_end + int(0.3 * hz)
        ins = [f for f in intakes if f < f1 - int(0.3 * hz)]
        if ins:
            cands["intake"] = ins[-1]
        # neutral-zone exit: last frame before f1 the robot was on the far side of its own hub line
        cx = tk.cxy[:, 0]
        if cx[f1] >= geo.L_own - 0.3:
            back = np.where(tk.ok[:f1] & (cx[:f1] < geo.L_own - 0.6))[0]
            if len(back):
                cands["neutral exit"] = int(back[-1])
        hole = np.where(~tk.ok[:f1 + 1])[0]
        if len(hole):
            cands["tracking"] = int(hole[-1]) + 1
        kind = max(cands, key=lambda k: cands[k])
        f0 = max(0, cands[kind])
        f0 = min(f0, f1)
        if f1 - f0 < int(0.8 * hz) and kind != "tracking":
            f0 = max(0, f1 - int(0.8 * hz))
        seg = np.arange(f0, f2 + 1, 2)
        seg = seg[seg < M.N]
        pts = [[int(round(tk.cxy[i, 0] * 100)), int(round(tk.cxy[i, 1] * 100))] if tk.ok[i] else None for i in seg]
        i_shoot = int(np.searchsorted(seg, f1))
        sp1 = tk.speed[f1] if np.isfinite(tk.speed[f1]) else None
        spv = [tk.speed[s["f"]] for s in v if np.isfinite(tk.speed[s["f"]])]
        appr = [c for c in cross if c["line"] == "own" and c["dir"] == "in" and f0 - int(3 * hz) <= c["f"] <= f1]
        a = appr[-1] if appr else None
        n_hub = sum(1 for s in v if not s["pass"])
        out.append({
            "m": M.short, "key": M.key, "t": r_(M.mc[f1], 1), "vt": r_(M.vt[f1], 2), "per": str(M.per_name[f1]),
            "n": len(v), "hub": n_hub, "made": sum(1 for s in v if s["o"] == "made"),
            "pass": sum(1 for s in v if s["pass"]), "dur": r_((f2 - f1) / hz, 1),
            "from": kind, "lead_s": r_((f1 - f0) / hz, 1),
            "moving": None if sp1 is None else bool(sp1 > V_SHOOT_MOVING), "speed": r_(sp1, 2),
            "move_frac": r_(np.mean(np.array(spv) > V_SHOOT_MOVING), 2) if spv else None,
            "approach": {"lane": a["lane"], "side": a["side"]} if a else None,
            "xy": pts, "i": i_shoot, "hz": hz / 2,
        })
        prev_end = f2
    return out


# ================================================================================ spec sheets
def spec_sheets(matches, geo, teams_meta, team_files):
    per = defaultdict(list)          # team -> [(M, tk)]
    for M in matches:
        for tk in M.tracks:
            if tk.team:
                per[tk.team].append((M, tk))
    specs = {}
    for team, lst in per.items():
        lst.sort(key=lambda x: _mnum(x[0].key))
        meta = teams_meta.get(team, {})
        tf = team_files.get(team) or {}
        mrows, speeds, accs, mov, dists = [], [], [], [], []
        shots_pts, paths, cross_all = [], [], []
        att = made = missed = passes = 0
        vol_sizes, fire, cycles, onmove, sdist = [], [], [], [], []
        byp = defaultdict(lambda: {"a": 0, "m": 0})
        teleop_s = active_s = 0.0
        made_active = 0
        auto_rows, climb_rows = [], []
        n_fuel = 0
        for M, tk in lst:
            hz = M.hz
            act = (M.mc >= 0) & (M.mc <= 160)
            okm = tk.ok & act
            mrows.append({"m": M.short, "key": M.key, "al": tk.alliance, "valid": r_(tk.valid_frac, 2),
                          "conf": r_(float(np.mean(tk.conf[act])) if act.any() else None, 2)})
            if okm.sum() > 20 * hz:
                s = tk.speed[okm]
                s = s[np.isfinite(s)]
                speeds.append(s)
                a = tk.acc[okm]
                accs.append(a[np.isfinite(a) & (a < 20)])
                mov.append(float((s > V_MOVING).mean()) if len(s) else None)
                d = np.hypot(*np.diff(tk.sm, axis=0).T)
                okd = okm[1:] & okm[:-1] & np.isfinite(d) & (d < V_GLITCH / hz)
                dists.append(float(d[okd].sum() / max(0.3, okd.mean())) if okd.mean() > 0.5 else None)
            L = M.launch[tk.k]
            hub = [s for s in L if not s["pass"]]
            fok = M.fuel_ok.get(tk.alliance, False)
            mrows[-1]["fuel_ok"] = fok
            if fok:                     # FUEL counts only from reconciled alliance results
                n_fuel += 1
                att += len(hub)
                made += sum(s["o"] == "made" for s in hub)
                missed += sum(s["o"] == "missed" for s in hub)
                passes += len(L) - len(hub)
                for s in hub:
                    p = s["period"] or str(M.per_name[s["f"]])
                    byp[p]["a"] += 1
                    byp[p]["m"] += s["o"] == "made"
                tel = M.teleop & tk.ok
                teleop_s += tel.sum() / hz
                active_s += (tel & M.active[tk.alliance]).sum() / hz
                made_active += int(tk.pts[tel].sum())
            vols = volleys(L, hz)
            vol_sizes += [len(v) for v in vols if M.mc[v[0]["f"]] > 20]
            fire += [(len(v) - 1) / max(0.3, (v[-1]["f"] - v[0]["f"]) / hz) for v in vols if len(v) >= 3]
            st = [M.mc[v[0]["f"]] for v in vols if M.mc[v[0]["f"]] > 20]
            cycles += [b - a for a, b in zip(st[:-1], st[1:]) if b - a < 45]
            for s in hub:
                if s["origin"]:
                    c = canon(s["origin"][:2], tk.alliance)
                    sp = tk.speed[s["f"]]
                    mvf = None if not np.isfinite(sp) else int(sp > V_SHOOT_MOVING)
                    if mvf is not None:
                        onmove.append(mvf)
                    sdist.append(math.hypot(c[0] - geo.hub_red[0], c[1] - geo.hub_red[1]))
                    shots_pts.append([int(round(c[0] * 100)), int(round(c[1] * 100)),
                                      {"made": "m", "missed": "x"}.get(s["o"], "u"), _pcode(s["period"]), M.short, mvf])
            cr = crossings(M, tk, geo)
            cross_all += [dict(c, m=M.short) for c in cr]
            paths += shooting_paths(M, tk, vols, cr, geo)
            # auto
            au = [s for s in hub if M.mc[s["f"]] <= 20]
            bd = (M.d.get("breakdown") or {}).get(tk.alliance) or {}
            st_i = _station(M.d, tk)
            lvl_a = bd.get(f"autoTowerRobot{st_i}") if st_i else None
            lvl_e = bd.get(f"endGameTowerRobot{st_i}") if st_i else None
            auto_rows.append({"m": M.short, "att": len(au), "made": sum(s["o"] == "made" for s in au), "fuel_ok": fok,
                              "climb": None if lvl_a is None else lvl_a not in ("None", None)})
            climb_rows.append({"m": M.short, "level": _lvl(lvl_e)})
        allsp = np.concatenate(speeds) if speeds else np.zeros(0)
        allac = np.concatenate(accs) if accs else np.zeros(0)
        mvs = allsp[allsp > V_MOVING]
        nm = len(lst)
        levels = Counter(str(c["level"]) for c in climb_rows if c["level"] is not None)
        lanes = _lane_summary(cross_all)
        appr = Counter()
        for p in paths:
            if p["hub"] == 0:
                continue
            a = p["approach"]
            appr[f"{a['lane']} {a['side']}" if a and a["lane"] != "unknown" else ("from neutral" if p["from"] == "neutral exit" else "inside zone")] += 1
        first = [p for p in paths if p["hub"] > 0 and p["per"] not in ("auto", "pause")]
        specs[team] = {
            "team": team, "number": int(team[3:]) if team[3:].isdigit() else team,
            "nickname": meta.get("nickname"), "rank": meta.get("rank"),
            "n": nm, "matches": mrows,
            "drive": {"top_speed": r_(np.percentile(allsp, 99), 2) if len(allsp) > 50 else None,
                      "p95_speed": r_(np.percentile(mvs, 95), 2) if len(mvs) > 30 else None,
                      "mean_speed": r_(mvs.mean(), 2) if len(mvs) else None,
                      "accel_p90": r_(np.percentile(allac, 90), 1) if len(allac) > 50 else None,
                      "accel_max": r_(np.percentile(allac, 99), 1) if len(allac) > 50 else None,
                      "pct_moving": r_(np.mean([x for x in mov if x is not None]), 2) if any(x is not None for x in mov) else None,
                      "dist_per_match": r_(np.mean([x for x in dists if x is not None]), 0) if any(x is not None for x in dists) else None,
                      "seconds": r_(len(allsp) / 10.0, 0)},
            "scoring": {"att": att, "made": made, "missed": missed, "passes": passes,
                        "acc": r_(made / (made + missed), 3) if made + missed >= 5 else None,
                        "acc_ci": wilson(made, made + missed) if made + missed >= 5 else None,
                        "made_pm": r_(made / n_fuel, 1) if n_fuel else None, "att_pm": r_(att / n_fuel, 1) if n_fuel else None,
                        "n_fuel": n_fuel,
                        "pts_per_active_min": r_(made_active / active_s * 60, 1) if active_s > 30 else None,
                        "att_per_teleop_min": r_(sum(b["a"] for p, b in byp.items() if p != "auto") / teleop_s * 60, 1) if teleop_s > 30 else None,
                        "fire_rate": r_(np.median(fire), 1) if fire else None,
                        "volley_mean": r_(np.mean(vol_sizes), 1) if vol_sizes else None,
                        "volley_p90": r_(np.percentile(vol_sizes, 90), 0) if vol_sizes else None,
                        "volley_max": max(vol_sizes) if vol_sizes else None,
                        "hopper_est": r_(np.percentile(vol_sizes, 90), 0) if len(vol_sizes) >= 4 else None,
                        "n_volleys": len(vol_sizes),
                        "cycle_s": r_(np.median(cycles), 1) if cycles else None,
                        "cycle_iqr": [r_(np.percentile(cycles, 25), 1), r_(np.percentile(cycles, 75), 1)] if len(cycles) >= 4 else None,
                        "on_move": r_(np.mean(onmove), 2) if len(onmove) >= 5 else None,
                        "dist_med": r_(np.median(sdist), 2) if sdist else None,
                        "by_period": {p: v for p, v in byp.items()},
                        "active_s": r_(active_s, 0), "teleop_s": r_(teleop_s, 0)},
            "auto": {"rows": auto_rows,
                     "made_avg": r_(np.mean([a["made"] for a in auto_rows if a["fuel_ok"]]), 1) if any(a["fuel_ok"] for a in auto_rows) else None,
                     "att_avg": r_(np.mean([a["att"] for a in auto_rows if a["fuel_ok"]]), 1) if any(a["fuel_ok"] for a in auto_rows) else None,
                     "n_fuel": sum(1 for a in auto_rows if a["fuel_ok"]),
                     "climb_rate": r_(np.mean([a["climb"] for a in auto_rows if a["climb"] is not None]), 2)
                     if any(a["climb"] is not None for a in auto_rows) else None,
                     "routines": [{k: r.get(k) for k in ("id", "name", "n", "of", "freq", "matches", "xy", "start_xy", "end_xy", "kind")}
                                  for r in (tf.get("routines") or [])]},
            "climb": {"rows": climb_rows, "levels": dict(levels),
                      "rate": r_(sum(v for k, v in levels.items() if k != "0") / max(1, sum(levels.values())), 2) if levels else None},
            "lanes": lanes, "crossings": [{k: c[k] for k in ("m", "t", "line", "dir", "lane", "side", "y")} for c in cross_all],
            "shots": shots_pts,
            "paths": paths,
            "approach": dict(appr.most_common()),
            "first_shot": {"moving": r_(np.mean([p["moving"] for p in first if p["moving"] is not None]), 2)
                           if any(p["moving"] is not None for p in first) else None,
                           "lead_s": r_(np.median([p["lead_s"] for p in first]), 1) if first else None,
                           "n": len(first)},
        }
    return specs


def _pcode(p):
    return {"auto": "a", "transition": "t", "shift1": "1", "shift2": "2", "shift3": "3", "shift4": "4", "endgame": "e"}.get(p or "", "?")


def _lvl(v):
    if v is None:
        return None
    return {"Level1": 1, "Level2": 2, "Level3": 3}.get(v, 0)


def _station(d, tk):
    teams = (d.get(tk.alliance) or {}).get("teams") or []
    return teams.index(tk.team) + 1 if tk.team in teams else None


def _mnum(key):
    s = key.split("_")[-1]
    lv = "".join(c for c in s if c.isalpha())
    n = "".join(c if c.isdigit() else " " for c in s).split()
    return ({"qm": 0, "ef": 1, "qf": 2, "sf": 3, "f": 4}.get(lv, 5), int(n[0]) if n else 0, int(n[1]) if len(n) > 1 else 0)


def _lane_summary(cross):
    out = {}
    for line in ("own", "opp"):
        c = Counter()
        for x in cross:
            if x["line"] == line and x["lane"] != "unknown":
                c[f"{x['lane']}_{x['side']}"] += 1
        tot = sum(c.values())
        out[line] = {"counts": dict(c), "n": tot}
    allc = Counter()
    for x in cross:
        if x["lane"] != "unknown":
            allc[x["lane"]] += 1
            allc[x["side"]] += 1
    n = sum(allc[k] for k in ("trench", "bump", "open"))
    out["trench_share"] = r_(allc["trench"] / n, 2) if n else None
    out["left_share"] = r_(allc["L"] / max(1, allc["L"] + allc["R"]), 2) if (allc["L"] + allc["R"]) else None
    out["trench_capable"] = bool(allc["trench"] > 0) if n else None
    out["unknown"] = sum(1 for x in cross if x["lane"] == "unknown")
    return out


# ================================================================================ defense library
def _shooting_spots(per_team_tracks):
    """team -> Nx2 canonical launch origins (for 'in shooting zone' checks)."""
    out = {}
    for team, lst in per_team_tracks.items():
        pts = []
        for M, tk in lst:
            for s in M.launch[tk.k]:
                if not s["pass"] and s["origin"]:
                    pts.append(canon(s["origin"][:2], tk.alliance))
        out[team] = np.array(pts) if pts else np.zeros((0, 2))
    return out


def _in_spots(cxy, spots, r=0.9):
    if len(spots) < 8:
        return None
    d = np.hypot(cxy[:, None, 0] - spots[None, :, 0], cxy[:, None, 1] - spots[None, :, 1]).min(1)
    return d < r


def detect_episodes(M, geo):
    """Raw (defender, target) episodes in one match: opponent within DEF_R of a robot on its own half."""
    hz = M.hz
    eps = []
    trs = M.tracks
    for D in trs:
        for T in trs:
            if D.alliance == T.alliance or not D.team or not T.team:
                continue
            both = D.ok & T.ok
            dist = np.hypot(*(D.xy - T.xy).T)
            # target in its scoring area (alliance zone / lane band, small margin): neutral-zone meetings
            # were mostly incidental traffic around fuel piles in the hand check
            close = both & (dist < DEF_R) & (T.cxy[:, 0] > geo.lane_x0 - 0.5) & M.teleop
            # exclude endgame tower jams (both at T's tower)
            tw = np.hypot(T.cxy[:, 0] - geo.tower[0], T.cxy[:, 1] - geo.tower[1]) < 1.8
            close &= ~(tw & (M.mc > 125))
            rs = runs(close, 1, int(DEF_MERGE_S * hz))
            rs = [(a, b) for a, b in rs if close[a:b].sum() >= DEF_MIN_S * hz]
            joined = []
            for a, b in rs:
                if joined and (a - joined[-1][1]) <= DEF_JOIN_S * hz:
                    joined[-1][1] = b
                else:
                    joined.append([a, b])
            for a, b in joined:
                eps.append({"M": M, "D": D, "T": T, "a": a, "b": b, "dist": dist})
    return eps


def _features(ep, geo, spots):
    M, D, T, a, b = ep["M"], ep["D"], ep["T"], ep["a"], ep["b"]
    hz = M.hz
    sl = slice(a, b)
    dist = ep["dist"][sl]
    both = D.ok[sl] & T.ok[sl]
    close = both & (dist < DEF_R)
    contact = both & (dist < CONTACT_R)
    sT, sD = T.speed[sl], D.speed[sl]
    vT, vD = T.v[sl], D.v[sl]
    nT, nD = np.hypot(*vT.T), np.hypot(*vD.T)
    with np.errstate(all="ignore"):
        cos = (vT * vD).sum(1) / (nT * nD)
    movboth = (nT > V_MOVING) & (nD > V_MOVING) & np.isfinite(cos)
    shadow_frac = float((cos[movboth] > 0.5).mean()) if movboth.sum() >= 5 else 0.0
    shadow_s = float(((cos > 0.5) & movboth).sum() / hz)
    # pins: contact runs >= 2 s with target nearly stopped
    pin_s = 0.0
    for c0, c1 in runs(contact, int(1.0 * hz), int(0.3 * hz)):
        st = np.nanmedian(sT[c0:c1]) if np.isfinite(sT[c0:c1]).any() else np.nan
        if (c1 - c0) >= 2.0 * hz and np.isfinite(st) and st < 0.3:
            pin_s += (c1 - c0) / hz
    # pushed: target displacement along defender->target direction during contact frames
    pushed = 0.0
    for i in range(a + 1, b):
        if contact[i - a] and contact[i - 1 - a]:
            u = T.xy[i - 1] - D.xy[i - 1]
            n = np.linalg.norm(u)
            if n > 1e-6:
                pushed += float((T.xy[i] - T.xy[i - 1]) @ (u / n))
    # zone denial: defender camped in target's alliance zone / lane band, slow, little contact
    dz = geo.zone_class(canon(D.xy[sl], T.alliance)[:, 0])
    d_in_zone = float(np.isin(dz, [0, 1]).mean())
    d_slow = float((np.nan_to_num(sD, nan=0) < 0.6).mean())
    contact_s = float(contact.sum() / hz)
    if pin_s >= 2.0:
        method = "pin"
    elif contact_s >= 1.0:
        method = "contact"
    elif shadow_frac >= 0.45 and shadow_s >= 1.0:
        method = "shadow"
    elif d_in_zone >= 0.6:
        method = "zone-denial"
    else:
        method = "shadow"
    mid = (a + b) // 2
    zn, side = geo.zone_name(T.cxy[mid])
    tin = _in_spots(T.cxy[sl], spots.get(T.team, np.zeros((0, 2))))
    d_int = sum(1 for f in M.intakes.get(D.k, []) if a <= f < b)
    # defender in the target's outpost corner, where spilled fuel collects: in the hand check several such
    # low-contact episodes were the "defender" collecting fuel, not defending -> caveat shown with the episode
    dc = canon(D.xy[sl], T.alliance)
    at_post = float(((dc[:, 0] > geo.outpost_area[0][0] - 0.5) & (dc[:, 1] > geo.outpost_area[1][0] - 0.5)).mean())
    return {
        "close_s": r_(close.sum() / hz, 1), "contact_s": r_(contact_s, 1), "pin_s": r_(pin_s, 1),
        "min_dist": r_(np.nanmin(np.where(both, dist, np.nan)), 2) if both.any() else None,
        "shadow_frac": r_(shadow_frac, 2), "pushed_m": r_(pushed, 2),
        "t_speed": r_(np.nanmean(sT), 2) if np.isfinite(sT).any() else None,
        "d_speed": r_(np.nanmean(sD), 2) if np.isfinite(sD).any() else None,
        "d_in_zone": r_(d_in_zone, 2), "d_slow": r_(d_slow, 2), "d_intakes": d_int, "d_at_outpost": r_(at_post, 2),
        "method": method, "zone": zn, "side": side,
        "t_in_spots": r_(float(tin.mean()), 2) if tin is not None else None,
        "zc": int(np.bincount(T.zc[sl][T.ok[sl]], minlength=4).argmax()) if T.ok[sl].any() else 0,
    }


class Baseline:
    """Per-team arrays over all its tracked matches for placebo-window baselines."""

    def __init__(self, team, lst, defended_masks):
        self.team = team
        self.segs = []      # per match: dict of arrays
        for M, tk in lst:
            hz = M.hz
            elig = tk.ok & M.teleop & ~defended_masks[(M.key, tk.k)]
            self.segs.append({
                "M": M, "tk": tk, "hz": hz,
                "elig": np.r_[0, np.cumsum(elig)],
                "z0": np.r_[0, np.cumsum((tk.zc == 0) & elig)],
                "z1": np.r_[0, np.cumsum((tk.zc == 1) & elig)],
                "z2": np.r_[0, np.cumsum((tk.zc == 2) & elig)],
                "act": np.r_[0, np.cumsum(M.active[tk.alliance] & elig)],
                "pts": np.r_[0, np.cumsum(np.where(elig, tk.pts, 0))],
                "att": np.r_[0, np.cumsum(np.where(elig, tk.att, 0))],
                "spots": None,
            })
        self.free_s = sum(s["elig"][-1] / s["hz"] for s in self.segs)
        act_s = sum(s["act"][-1] / s["hz"] for s in self.segs)
        self.pts_rate = sum(s["pts"][-1] for s in self.segs) / act_s if act_s > 5 else None
        self.att_rate = sum(s["att"][-1] for s in self.segs) / self.free_s if self.free_s > 5 else None
        self.act_s = act_s

    def null(self, L, zmix, act_frac, step=2):
        """Placebo windows of L frames: >= 80 % eligible, similar zone mix and hub-active share."""
        res_pts, res_att, res_act = [], [], []
        level = "zone+state"
        for relax in (0, 1, 2):
            res_pts, res_att, res_act = [], [], []
            for s in self.segs:
                n = len(s["elig"]) - 1
                if n < L:
                    continue
                st = np.arange(0, n - L + 1, step)
                e = s["elig"][st + L] - s["elig"][st]
                ok = e >= 0.8 * L
                if relax < 2:
                    for zi, key in enumerate(("z0", "z1", "z2")):
                        zw = (s[key][st + L] - s[key][st]) / np.maximum(1, e)
                        ok &= np.abs(zw - zmix[zi]) <= (0.35 if relax == 0 else 0.6)
                actw = (s["act"][st + L] - s["act"][st]) / np.maximum(1, e)
                if relax < 2:
                    ok &= np.abs(actw - act_frac) <= (0.25 if relax == 0 else 0.4)
                st = st[ok]
                res_pts += list(s["pts"][st + L] - s["pts"][st])
                res_att += list(s["att"][st + L] - s["att"][st])
                res_act += list((s["act"][st + L] - s["act"][st]) / s["hz"])
            if len(res_pts) >= 40:
                level = ["zone+state", "loose", "any"][relax]
                break
            level = ["zone+state", "loose", "any"][relax]
        return np.array(res_pts, float), np.array(res_att, float), np.array(res_act, float), level


def _null_stats(obs, null):
    if len(null) < 15:
        return None
    lo, hi = np.percentile(null, [5, 95])
    p = float((null < obs).mean() + 0.5 * (null == obs).mean())    # one-sided mid-p: defense lowers output
    return {"exp": float(null.mean()), "lo": float(lo), "hi": float(hi), "p": p, "n": int(len(null))}


def defense_library(matches, geo, specs=None):
    hz_any = matches[0].hz if matches else 10
    per_team = defaultdict(list)
    for M in matches:
        for tk in M.tracks:
            if tk.team:
                per_team[tk.team].append((M, tk))
    spots = _shooting_spots(per_team)
    raw = []
    for M in matches:
        raw += detect_episodes(M, geo)
    # "defended" masks for baselines: any opponent within FREE_R (team-valid both) or inside an episode
    dmask = {}
    for M in matches:
        for T in M.tracks:
            m = np.zeros(M.N, bool)
            for D in M.tracks:
                if D.alliance == T.alliance:
                    continue
                d = np.hypot(*(D.xy - T.xy).T)
                m |= D.ok & T.ok & (d < FREE_R)
            dmask[(M.key, T.k)] = m
    for e in raw:
        dmask[(e["M"].key, e["T"].k)][e["a"]:e["b"]] = True
    base = {team: Baseline(team, lst, dmask) for team, lst in per_team.items()}
    # cycle stats per team (volley starts)
    vstarts = {}
    for team, lst in per_team.items():
        cyc = []
        vs = {}
        for M, tk in lst:
            st = [v[0]["f"] for v in volleys([s for s in M.launch[tk.k]], M.hz)]
            vs[M.key] = st
            ts = [M.mc[f] for f in st if M.mc[f] > 20]
            cyc += [b - a for a, b in zip(ts[:-1], ts[1:]) if b - a < 45]
        vstarts[team] = (vs, float(np.median(cyc)) if cyc else None)
    eps = []
    for e in raw:
        M, D, T, a, b = e["M"], e["D"], e["T"], e["a"], e["b"]
        hz = M.hz
        L = b - a
        f = _features(e, geo, spots)
        sl = slice(a, b)
        okT = T.ok[sl]
        zmix = [float(((T.zc[sl] == z) & okT).sum() / max(1, okT.sum())) for z in (0, 1, 2)]
        actv = M.active[T.alliance][sl]
        act_frac = float((actv & okT).sum() / max(1, okT.sum()))
        active_s = float((actv & okT).sum() / hz)
        obs_pts = int(T.pts[sl][okT].sum())
        obs_att = int(T.att[sl][okT].sum())
        obs_made = int(T.made[sl][okT].sum())
        B = base[T.team]
        npts, natt, nact, level = B.null(L, zmix, act_frac)
        sp = _null_stats(obs_pts, npts)
        sa = _null_stats(obs_att, natt)
        dur = L / hz
        eff = {"pts_obs": obs_pts, "att_obs": obs_att, "made_obs": obs_made, "active_s": r_(active_s, 1),
               "null_n": int(len(npts)), "null_level": level}
        if sp:
            denom = max(active_s, 1.0)
            eff.update({"pts_exp": r_(sp["exp"], 2), "pts_ci": [r_(sp["lo"], 1), r_(sp["hi"], 1)], "pts_p": r_(sp["p"], 3),
                        "d_rate": r_((obs_pts - sp["exp"]) / denom, 3),
                        "d_rate_ci": [r_((obs_pts - sp["hi"]) / denom, 3), r_((obs_pts - sp["lo"]) / denom, 3)],
                        "ratio": r_(obs_pts / sp["exp"], 2) if sp["exp"] > 0.05 else None,
                        "denied": r_(sp["exp"] - obs_pts, 2)})
        if sa:
            eff.update({"att_exp": r_(sa["exp"], 2), "att_ci": [r_(sa["lo"], 1), r_(sa["hi"], 1)], "att_p": r_(sa["p"], 3),
                        "att_ratio": r_(obs_att / sa["exp"], 2) if sa["exp"] > 0.05 else None})
        # cycle disruption
        vs, med = vstarts[T.team]
        st = vs.get(M.key, [])
        prv = [x for x in st if x <= a]
        nxt = [x for x in st if x > a]
        gap = (M.mc[nxt[0]] - M.mc[prv[-1]]) if (prv and nxt) else None
        cyc = {"gap": r_(gap, 1), "median": r_(med, 1),
               "delta": r_(gap - med, 1) if (gap is not None and med is not None) else None,
               "next_shot_s": r_((nxt[0] - a) / hz, 1) if nxt else None}
        # displacement vs the target's own share of time in its shooting spots (free time, own half)
        bt = _base_in_spots(per_team[T.team], spots.get(T.team), dmask)
        disp = {"pushed_m": f["pushed_m"], "in_spots": f["t_in_spots"], "base_in_spots": r_(bt, 2) if bt is not None else None}
        disp["displaced"] = bool((f["pushed_m"] or 0) >= 0.75 or
                                 (bt is not None and f["t_in_spots"] is not None and bt - f["t_in_spots"] >= 0.4))
        # doubled: another defender on the same target overlapping >= 50 %
        dbl = any(o is not e and o["M"] is M and o["T"] is T and o["D"] is not D and
                  min(o["b"], b) - max(o["a"], a) >= 0.5 * L for o in raw)
        mid = (a + b) // 2
        eps.append({
            "m": M.short, "key": M.key, "a": a, "b": b,
            "t_video": r_(M.vt[a], 2), "t_match": r_(M.mc[a], 1), "dur": r_(dur, 1),
            "defender": D.team, "target": T.team, "d_al": D.alliance, "t_al": T.alliance, "d_k": D.k, "t_k": T.k,
            "shift": str(M.per_name[mid]), "active": bool(act_frac >= 0.5), "act_frac": r_(act_frac, 2),
            "zone": f["zone"], "side": f["side"], "zc": f["zc"],
            "method": f["method"], "double": dbl,
            "feat": {k: f[k] for k in ("close_s", "contact_s", "pin_s", "min_dist", "shadow_frac", "t_speed", "d_speed",
                                       "d_in_zone", "d_intakes", "d_at_outpost")},
            "maybe_collecting": bool(f["d_at_outpost"] >= 0.5 and (f["contact_s"] or 0) < 1.0),
            "effect": eff, "cycle": cyc, "disp": disp,
            "clip": {"v0": r_(max(M.vt[0], M.vt[a] - PAD_S), 2), "v1": r_(min(M.vt[-1], M.vt[b - 1] + PAD_S), 2)},
            "conf": r_(min(float(np.mean(D.conf[sl])), float(np.mean(T.conf[sl]))), 2),
            # centres closer than ~0.5 m are physically impossible (0.91 m robots): merged/occluded tracking
            "overlap": bool(f["min_dist"] is not None and f["min_dist"] < 0.5),
        })
    _verdicts(eps)
    for i, e in enumerate(sorted(eps, key=lambda e: (_mnum(e["key"]), e["a"]))):
        e["id"] = f"{e['m']}-{i:03d}"
    eps.sort(key=lambda e: (-(e["effect"].get("denied") or -99), e["id"]))
    return eps, base, per_team, spots, dmask


_BTS_CACHE = {}


def _base_in_spots(lst, spots, dmask):
    if spots is None or len(spots) < 8 or not lst:
        return None
    key = id(lst)
    if key in _BTS_CACHE:
        return _BTS_CACHE[key]
    num = den = 0
    for M, tk in lst:
        m = tk.ok & M.teleop & (tk.cxy[:, 0] > CX) & ~dmask[(M.key, tk.k)]
        if m.sum() < 20:
            continue
        ins = _in_spots(tk.cxy[m][::3], spots)
        num += ins.sum()
        den += len(ins)
    v = num / den if den else None
    _BTS_CACHE[key] = v
    return v


def _verdicts(eps):
    """success = clearly reduced output: expected >= 2 points at stake, observed <= half, p <= 0.1;
    significant = also survives BH at q <= 0.2.  Inactive-hub episodes are judged on attempts."""
    ps = []
    for e in eps:
        ef = e["effect"]
        if e.get("overlap"):
            ps.append(None)
            continue
        if ef.get("pts_exp") is not None and ef["pts_exp"] >= 2.0 and (ef.get("active_s") or 0) >= 2:
            ef["metric"] = "points"
            ps.append(ef["pts_p"])
        elif ef.get("att_exp") is not None and ef["att_exp"] >= 2.0:
            ef["metric"] = "attempts"
            ps.append(ef["att_p"])
        else:
            ef["metric"] = None
            ps.append(None)
    qs = bh_q(ps)
    for e, p, q in zip(eps, ps, qs):
        ef = e["effect"]
        ef["q"] = r_(q, 3)
        e["sig"] = False
        if e.get("overlap"):
            e["verdict"] = "tracking"           # merged robots: positions/identity unreliable, not scored
            ef["metric"] = None
            continue
        if ef["metric"] is None:
            e["verdict"] = "untestable"         # nothing at stake: target would not have scored anyway
            continue
        ratio = ef.get("ratio") if ef["metric"] == "points" else ef.get("att_ratio")
        if ratio is not None and ratio <= 0.5 and p <= 0.10:
            e["verdict"] = "success"
        elif ratio is not None and ratio >= 1.0:
            e["verdict"] = "no effect"
        else:
            e["verdict"] = "weak"
        e["sig"] = bool(p <= 0.05 and q is not None and q <= 0.2)


def placebo_check(base, eps, rng=None, n=400):
    """Pipeline calibration: score random undefended windows exactly like episodes; the 'success' rate
    there is the false-positive rate of the verdict rule."""
    rng = rng or np.random.default_rng(7)
    teams = [t for t in base if base[t].free_s > 60]
    if not teams or not eps:
        return None
    lens = [e["b"] - e["a"] for e in eps]
    succ = tested = 0
    so = se = 0.0
    wins = []
    for _ in range(n):
        B = base[teams[rng.integers(len(teams))]]
        s = B.segs[rng.integers(len(B.segs))]
        L = int(lens[rng.integers(len(lens))])
        nfr = len(s["elig"]) - 1
        if nfr <= L + 1:
            continue
        a = int(rng.integers(0, nfr - L))
        e_ = s["elig"][a + L] - s["elig"][a]
        if e_ < 0.8 * L:
            continue
        tk, M = s["tk"], s["M"]
        zmix = [float((s[k][a + L] - s[k][a]) / max(1, e_)) for k in ("z0", "z1", "z2")]
        if sum(zmix[:2]) < 0.3:
            continue                                  # episodes happen near the scoring area
        act = float((s["act"][a + L] - s["act"][a]) / max(1, e_))
        obs = int(s["pts"][a + L] - s["pts"][a])
        npts, _, _, _ = B.null(L, zmix, act)
        # leave-one-out-ish: the window itself is in the null; acceptable for a calibration estimate
        st = _null_stats(obs, npts)
        if not st or st["exp"] < 2.0 or act * L / M.hz < 2:
            continue
        tested += 1
        so += obs
        se += st["exp"]
        wins.append((obs, st["exp"]))
        if obs / st["exp"] <= 0.5 and st["p"] <= 0.10:
            succ += 1
    return {"tested": tested, "false_success": succ, "rate": r_(succ / tested, 3) if tested else None,
            "pooled_ratio": r_(so / se, 2) if se else None, "_windows": wins}


# ================================================================================ summaries
METHODS = ["pin", "contact", "shadow", "zone-denial"]


def _pool(lst):
    tst = [e for e in lst if e["effect"].get("metric") == "points"]
    O = sum(e["effect"]["pts_obs"] for e in tst)
    E = sum(e["effect"]["pts_exp"] for e in tst)
    return {"n": len(lst), "tested": len(tst), "succ": sum(e["verdict"] == "success" for e in lst),
            "sig": sum(bool(e.get("sig")) for e in lst),
            "obs": O, "exp": r_(E, 1), "ratio": r_(O / E, 2) if E > 0.5 else None,
            "denied": r_(E - O, 1), "secs": r_(sum(e["dur"] for e in lst), 0)}


def _boot_samples(lst, rng, B=1000):
    """(point estimate sum(obs)/sum(exp), bootstrap samples) over testable episodes, or (None, None) if < 3.
    Resamples episodes and adds each episode's count noise (from its own placebo spread: volleys make counts
    overdispersed, and a few all-zero episodes must not give a falsely tight [0, 0] interval).  The noise is
    mean-zero and NOT clipped per episode (clipping at 0 biased the samples upward, so the interval could exclude
    the estimate: "71% (74-105%)"); only the summed count is floored at 0."""
    tst = [e for e in lst if e["effect"].get("metric") == "points"]
    if len(tst) < 3:
        return None, None
    O = np.array([e["effect"]["pts_obs"] for e in tst], float)
    E = np.array([e["effect"]["pts_exp"] for e in tst], float)
    sd = np.array([max(0.5, ((e["effect"]["pts_ci"][1] or 0) - (e["effect"]["pts_ci"][0] or 0)) / 3.29) for e in tst])
    idx = rng.integers(0, len(tst), (B, len(tst)))
    On = np.maximum(0.0, (O[idx] + rng.normal(0, 1, idx.shape) * sd[idx]).sum(1))
    return float(O.sum() / max(1e-6, E.sum())), On / np.maximum(1e-6, E[idx].sum(1))


def _ci_contains(est, lo, hi):
    return [r_(min(lo, est), 2), r_(max(hi, est), 2)]


def _boot_ratio(lst, rng, B=500):
    est, smp = _boot_samples(lst, rng, B)
    if est is None:
        return None
    return _ci_contains(est, float(np.percentile(smp, 5)), float(np.percentile(smp, 95)))


def _kish(lst):
    E = np.array([e["effect"]["pts_exp"] for e in lst if e["effect"].get("metric") == "points"], float)
    return r_(E.sum() ** 2 / max(1e-9, (E ** 2).sum()), 1) if len(E) else 0


def team_retention(eps, placebo_w, rng=None, B=2000, alpha=0.10, min_n=3):
    """Per-team placebo-controlled retention for defense.json `teams[]`.
    retention (as TARGET): own active-hub points kept while defended = [sum obs / sum exp over the team's testable
    episodes] / [same ratio over placebo windows (random undefended windows scored exactly like episodes)].  The
    placebo ratio removes the pipeline's own bias (it is not 1.0: windows are chosen near the scoring area).
    as_defender: the same for the victims of the team's defense.
    Intervals: bootstrap (episodes + count noise) / bootstrap (placebo windows).  Multiple testing: two-sided
    bootstrap p vs 1.0, Benjamini-Hochberg over every team estimate in the file; the reported adjusted interval is
    the FCR-adjusted one (level 1 - alpha*R/m, Benjamini-Yekutieli 2005) and `significant` = it excludes 1.0."""
    rng = rng or np.random.default_rng(23)
    if not placebo_w or len(placebo_w) < 20:
        return [], None
    po = np.array([w[0] for w in placebo_w], float)
    pe = np.array([w[1] for w in placebo_w], float)
    pr = po.sum() / pe.sum()
    pidx = rng.integers(0, len(po), (B, len(po)))
    psmp = po[pidx].sum(1) / np.maximum(1e-6, pe[pidx].sum(1))
    by = {"retention": defaultdict(list), "as_defender": defaultdict(list)}
    for e in eps:
        by["retention"][e["target"]].append(e)
        by["as_defender"][e["defender"]].append(e)
    rows, cells = {}, []
    for kind, dd in by.items():
        for team, lst in dd.items():
            est, smp = _boot_samples(lst, rng, B)
            if est is None or sum(1 for e in lst if e["effect"].get("metric") == "points") < min_n:
                continue
            ctl = smp / psmp
            e0 = est / pr
            p = float(min(1.0, 2 * min((ctl >= 1).mean(), (ctl <= 1).mean()) + 1.0 / B))
            cell = {"est": r_(e0, 2), "lo": None, "hi": None, "n_episodes": len(lst),
                    "n_testable": sum(1 for e in lst if e["effect"].get("metric") == "points"), "n_eff": _kish(lst),
                    "p": r_(p, 4), "_ctl": ctl}
            cell["lo"], cell["hi"] = _ci_contains(e0, float(np.percentile(ctl, 100 * alpha / 2)),
                                                  float(np.percentile(ctl, 100 * (1 - alpha / 2))))
            rows.setdefault(team, {"team": team})[kind] = cell
            cells.append(cell)
    m = len(cells)
    qs = bh_q([c["p"] for c in cells])
    R = sum(1 for q in qs if q is not None and q <= alpha)
    a_adj = alpha * max(R, 1) / max(m, 1)
    for c, q in zip(cells, qs):
        ctl = c.pop("_ctl")
        c["q"] = r_(q, 4)
        lo, hi = _ci_contains(c["est"], float(np.percentile(ctl, 100 * a_adj / 2)), float(np.percentile(ctl, 100 * (1 - a_adj / 2))))
        c["lo_adj"], c["hi_adj"] = lo, hi
        c["significant"] = bool(q is not None and q <= alpha and (hi < 1.0 or lo > 1.0))
    ge, gs = _boot_samples(eps, rng, B)
    event = None
    if ge is not None:                     # event-wide placebo-controlled retention (the "assumption" default)
        gc = gs / psmp
        lo, hi = _ci_contains(ge / pr, float(np.percentile(gc, 100 * alpha / 2)), float(np.percentile(gc, 100 * (1 - alpha / 2))))
        event = {"est": r_(ge / pr, 2), "lo": lo, "hi": hi, "n_testable": sum(1 for e in eps if e["effect"].get("metric") == "points"),
                 "n_eff": _kish(eps)}
    meta = {"event": event, "placebo_ratio": r_(pr, 3), "placebo_ci": [r_(np.percentile(psmp, 5), 3), r_(np.percentile(psmp, 95), 3)],
            "placebo_windows": len(po), "alpha": alpha, "tests": m, "discoveries": R, "adj_level": r_(1 - a_adj, 4),
            "min_testable_episodes": min_n}
    return sorted(rows.values(), key=lambda r: int(r["team"][3:]) if r["team"][3:].isdigit() else 0), meta


def summaries(eps, base, specs):
    rng = np.random.default_rng(11)
    targets, defenders = {}, {}
    by_t, by_d = defaultdict(list), defaultdict(list)
    for e in eps:
        by_t[e["target"]].append(e)
        by_d[e["defender"]].append(e)
    for team, B in base.items():
        lst = by_t.get(team, [])
        tot = _pool(lst)
        tot["ci"] = _boot_ratio(lst, rng)
        bm = {m: _pool([e for e in lst if e["method"] == m]) for m in METHODS}
        bz = {}
        for e in lst:
            bz.setdefault(e["zone"] + " " + e["side"], []).append(e)
        bz = {k: _pool(v) for k, v in bz.items()}
        bs = {"active": _pool([e for e in lst if e["active"]]), "inactive": _pool([e for e in lst if not e["active"]])}
        bd = sorted(((d, _pool([e for e in lst if e["defender"] == d])) for d in {e["defender"] for e in lst}),
                    key=lambda x: -(x[1]["denied"] or 0))
        sp = specs.get(team) or {}
        targets[team] = {
            "baseline": {"pts_per_active_s": r_(B.pts_rate, 3), "att_per_s": r_(B.att_rate, 3),
                         "free_s": r_(B.free_s, 0), "active_s": r_(B.act_s, 0)},
            "received": tot, "by_method": bm, "by_zone": bz, "by_state": bs,
            "defenders": [dict(team=d, **p) for d, p in bd[:6]],
            "advice": _advice(team, lst, bm, bz, bs, B, sp),
        }
    for team, lst in by_d.items():
        p = _pool(lst)
        p["ci"] = _boot_ratio(lst, rng)
        p["methods"] = dict(Counter(e["method"] for e in lst))
        p["targets"] = dict(Counter(e["target"] for e in lst))
        p["matches"] = sorted({e["m"] for e in lst}, key=lambda s: _mnum(s.lower().replace("q", "qm")))
        defenders[team] = p
    return targets, defenders


def _advice(team, lst, bm, bz, bs, B, sp):
    """Short plain-language 'how to defend X' bullets; every claim carries its sample size."""
    out = []
    n = team[3:]
    if B.pts_rate is not None:
        out.append(f"Undefended, {n} scores {B.pts_rate * 60:.0f} points per active-hub minute "
                   f"({B.act_s:.0f} s of free active time tracked).")
    lanes = (sp.get("lanes") or {}).get("own") or {}
    if lanes.get("n"):
        top = max(lanes["counts"].items(), key=lambda kv: kv[1])
        lane, side = top[0].split("_")
        out.append(f"Returns to its zone mostly via the {lane} on the driver's {'left' if side == 'L' else 'right'} "
                   f"({top[1]}/{lanes['n']} own-side crossings) - a place to meet it.")
    ap = sp.get("approach") or {}
    fs = sp.get("first_shot") or {}
    if fs.get("moving") is not None and fs.get("n", 0) >= 4:
        out.append(f"Starts {'most volleys on the move' if fs['moving'] >= 0.5 else 'most volleys from a stop'} "
                   f"({fs['moving'] * 100:.0f}% moving, n={fs['n']}).")
    tested = [(m, p) for m, p in bm.items() if p["tested"] >= 1]
    if not lst:
        out.append("Never defended in the tracked matches - no evidence either way.")
        return out
    good = sorted([(m, p) for m, p in tested if p["ratio"] is not None and p["ratio"] < 0.7],
                  key=lambda x: x[1]["ratio"])
    bad = [(m, p) for m, p in tested if p["ratio"] is not None and p["ratio"] >= 0.9]
    for m, p in good[:2]:
        strength = "significant" if p["sig"] else ("suggestive" if p["succ"] else "not significant")
        out.append(f"{m.capitalize()} cut it to {p['ratio'] * 100:.0f}% of baseline "
                   f"({p['tested']} episode{'s' if p['tested'] != 1 else ''}, {p['succ']} clear, {strength}).")
    for m, p in bad[:2]:
        out.append(f"{m.capitalize()} did not slow it ({p['ratio'] * 100:.0f}% of baseline, {p['tested']} episode"
                   f"{'s' if p['tested'] != 1 else ''}).")
    a, i = bs["active"], bs["inactive"]
    if i["n"] and not a["n"]:
        out.append("All defense it faced came during its inactive shift - no points were at stake.")
    zz = sorted([(k, p) for k, p in bz.items() if p["ratio"] is not None and p["tested"] >= 1], key=lambda x: x[1]["ratio"])
    if zz and zz[0][1]["ratio"] < 0.7:
        out.append(f"Most effective spot: {zz[0][0]} ({zz[0][1]['ratio'] * 100:.0f}% of baseline, n={zz[0][1]['tested']}).")
    if not good and tested:
        out.append("No defense style clearly reduced its scoring yet (small samples).")
    return out


# ================================================================================ clips
def clip_data(M, e, step=2):
    """Compact top-down clip: all robots at 5 Hz over the clip window (absolute cm), plus launches."""
    hz = M.hz
    f0 = max(0, int(round((e["clip"]["v0"] - M.v0) * hz)))
    f1 = min(M.N - 1, int(round((e["clip"]["v1"] - M.v0) * hz)))
    fr = np.arange(f0, f1 + 1, step)
    trs = []
    for tk in M.tracks:
        xy = [[int(round(tk.xy[i, 0] * 100)), int(round(tk.xy[i, 1] * 100))] if (tk.ok[i] or M.d["tracks"][tk.k]["obs"][i]) else None
              for i in fr]
        trs.append({"team": tk.team, "al": tk.alliance, "xy": xy,
                    "q": [1 if tk.ok[i] else 0 for i in fr]})
    shots = []
    for s in M.d.get("shots") or []:
        if not (e["clip"]["v0"] <= s["t"] <= e["clip"]["v1"]) or not s.get("origin"):
            continue
        sh = s.get("shooter") or {}
        shots.append([r_(s["t"] - e["clip"]["v0"], 2), int(round(s["origin"][0] * 100)), int(round(s["origin"][1] * 100)),
                      int(round((s.get("landing") or s["origin"])[0] * 100)), int(round((s.get("landing") or s["origin"])[1] * 100)),
                      {"made": "m", "missed": "x", "pass": "p"}.get(s.get("outcome"), "u"),
                      sh.get("track_idx") if sh.get("kind") == "robot" else -1, 1 if s.get("team") else 0])
    return {"hz": hz / step, "t0": r_(M.vt[f0], 2), "tracks": trs, "shots": shots,
            "a": r_((e["a"] - f0) / step, 1), "b": r_((e["b"] - f0) / step, 1)}


# ================================================================================ entry points
def load_matches(api_ev, geo, log=print):
    out = []
    mdir = os.path.join(api_ev, "m")
    if not os.path.isdir(mdir):
        return out
    for fn in sorted(os.listdir(mdir), key=lambda s: _mnum(s.replace(".json", ""))):
        if not fn.endswith(".json") or fn.count(".") > 1:
            continue
        d = jload(os.path.join(mdir, fn))
        if not d or not d.get("tracks") or d.get("shots") is None:
            continue          # v1 match (no shots) - nothing for scouting
        if not COV.is_v2(d):
            log(f"scouting: {fn}: v1 tracks (no OCR identity) - replay only, feeds no team numbers")
            continue
        try:
            out.append(Match(d, geo))
        except Exception as ex:          # one bad match must not kill the event
            log(f"scouting: skip {fn}: {ex!r}")
    return out


def build_asof(api_ev, ev, asof, out_dir=None, log=print):
    """Replay view: scout/<asof>.json from verified matches played <= asof (TBA actual time) and the dashboard
    snapshot <= asof.  Spec sheets / defense library are not snapshotted."""
    out_dir = out_dir or api_ev
    geo = Geo()
    evj = jload(os.path.join(api_ev, "event.json"), {}) or {}
    teams_meta = {t["key"]: dict(t, rank=None, opr=None) for t in evj.get("teams") or []}   # no end-of-event rank/OPR
    data_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(api_ev))))
    tt = {m["key"]: (m.get("actual_time") or m.get("time") or 0) for m in _tba_matches(data_root, ev)}
    matches = [M for M in load_matches(api_ev, geo, lambda *a: None) if 0 < tt.get(M.key, 0) <= asof]
    specs = spec_sheets(matches, geo, teams_meta, {})
    try:                                      # defense played / received as of then
        eps, base, _, _, _ = defense_library(matches, geo, specs)
        targets, defenders = summaries(eps, base, specs)
        for team, sp in specs.items():
            sp["defense"] = {"received": (targets.get(team) or {}).get("received"), "played": defenders.get(team)}
    except Exception as ex:
        log(f"scouting asof: defense skipped: {ex!r}")
    p = os.path.join(out_dir, "scout", f"{int(asof)}.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return build_truth(api_ev, ev, matches, specs, teams_meta, data_root=data_root, log=log, asof=asof, out_path=p)


def build(api_ev, ev=None, out_dir=None, log=print):
    t0 = time.time()
    out_dir = out_dir or api_ev
    geo = Geo()
    evj = jload(os.path.join(api_ev, "event.json"), {}) or {}
    teams_meta = {t["key"]: t for t in evj.get("teams") or []}
    matches = load_matches(api_ev, geo, log)
    team_files = {}
    for tk in teams_meta:
        tf = jload(os.path.join(api_ev, "t", f"{tk}.json"))
        if tf:
            team_files[tk] = tf
    specs = spec_sheets(matches, geo, teams_meta, team_files)
    eps, base, per_team, spots, dmask = defense_library(matches, geo, specs)
    targets, defenders = summaries(eps, base, specs)
    cal = placebo_check(base, eps, n=1500)
    pw = (cal or {}).pop("_windows", None)
    team_rows, team_meta = team_retention(eps, pw)
    # clips: embed for successful + top by points denied
    mby = {M.key: M for M in matches}
    order = sorted(eps, key=lambda e: (e["verdict"] != "success", -(e["effect"].get("denied") or -99)))
    for e in order[:EMBED_CLIPS]:
        e["clip"]["data"] = clip_data(mby[e["key"]], e)
    # defense played/received into spec sheets
    for team, sp in specs.items():
        t = targets.get(team) or {}
        dd = defenders.get(team)
        sp["defense"] = {"received": t.get("received"), "played": dd,
                         "baseline": t.get("baseline"), "advice": t.get("advice")}
    tested = [e for e in eps if e["effect"].get("metric") == "points"]
    O = sum(e["effect"]["pts_obs"] for e in tested)
    E = sum(e["effect"]["pts_exp"] for e in tested)
    rng = np.random.default_rng(3)
    glob_ci = _boot_ratio(eps, rng)
    out = {
        "event": ev or evj.get("key"), "generated": time.time(),
        "matches": [M.key for M in matches],
        "params": {"radius_m": DEF_R, "min_s": DEF_MIN_S, "contact_m": CONTACT_R, "free_m": FREE_R,
                   "id_conf_min": ID_CONF_MIN, "success": "expected >= 2 points at stake, observed <= 50 %, one-sided p <= 0.10",
                   "significant": "p <= 0.05 and Benjamini-Hochberg q <= 0.2"},
        "global": {"n": len(eps), "tested": len(tested), "succ": sum(e["verdict"] == "success" for e in eps),
                   "sig": sum(bool(e.get("sig")) for e in eps), "obs": O, "exp": r_(E, 1),
                   "ratio": r_(O / E, 2) if E else None, "ratio_ci": glob_ci, "placebo": cal,
                   "verdicts": dict(Counter(e["verdict"] for e in eps)),
                   "methods": dict(Counter(e["method"] for e in eps))},
        "episodes": eps, "targets": targets, "defenders": defenders,
        "teams": team_rows, "teams_meta": team_meta,
    }
    jsave(os.path.join(out_dir, "defense.json"), out)
    truth = {}
    try:
        T = build_truth(api_ev, ev or evj.get("key"), matches, specs, teams_meta, log=log, out_dir=out_dir)
        truth = {r["team"]: r for r in T["teams"]}
    except Exception:
        import traceback
        traceback.print_exc()
    for team, sp in specs.items():
        tr = truth.get(team) or {}
        sp["coverage"] = tr.get("coverage")
        sp["truth"] = {k: tr.get(k) for k in ("video", "flags", "rating", "trend")}
        if tr.get("climb"):                 # climb from TBA per robot (every played qual), not from the tracked subset
            cl = tr["climb"]
            sp["climb"] = {"rows": sp["climb"]["rows"], "levels": cl["levels"], "rate": cl["rate"], "n": cl["n"],
                           "pts_mean": cl["pts_mean"], "source": "TBA"}
            sp["auto"]["climb_rate"] = cl["auto_l1_rate"]
    idx = []
    for team, sp in specs.items():
        jsave(os.path.join(out_dir, "spec", f"{team}.json"), dict(sp, event=ev or evj.get("key")))
        s = sp["scoring"]
        idx.append({"team": team, "number": sp["number"], "nickname": sp["nickname"], "rank": sp["rank"], "n": sp["n"],
                    "made_pm": s["made_pm"], "acc": s["acc"], "pts_min": s["pts_per_active_min"], "fire": s["fire_rate"],
                    "hopper": s["hopper_est"] or s["volley_max"], "cycle": s["cycle_s"], "speed": sp["drive"]["p95_speed"],
                    "trench": sp["lanes"]["trench_share"], "left": sp["lanes"]["left_share"],
                    "on_move": s["on_move"], "climb": sp["climb"]["rate"],
                    "def_recv": (targets.get(team) or {}).get("received", {}).get("n"),
                    "def_play": (defenders.get(team) or {}).get("n"),
                    "cov": sp.get("coverage"), "made_v": ((sp.get("truth") or {}).get("video") or {}).get("made_pm"),
                    "flags": [f["k"] for f in (sp.get("truth") or {}).get("flags") or []]})
    idx.sort(key=lambda r: (-(r["made_pm"] or -1)))
    jsave(os.path.join(out_dir, "spec", "index.json"), {"event": ev or evj.get("key"), "teams": idx,
                                                         "matches": [M.key for M in matches], "generated": time.time()})
    log(f"scouting: {len(matches)} matches, {len(specs)} spec sheets, {len(eps)} defense episodes "
        f"({out['global']['succ']} successful) in {time.time() - t0:.1f}s")
    return out


# ------------------------------------------------------------------------------------------------ scouting truth
# api/<ev>/scout.json (schema cyber-scout/scout@1, contract in live/strategy/README.md): ONE canonical value per team
# metric for every page (teams table, team page, spec sheets, compare, picklist, selection).  Video numbers obey the
# trust rule (coverage.py), are TBA-reconciled and shrunk; climbs come from TBA per robot; ratings from dashboard.json.
DEAD_MIN_S, DEAD_TBA_FRAC = 30.0, 0.85   # "Dead": stopped >= 30 s AND the alliance scored < 85% of its expected (TBA)
VH_MULT, VH_ADD = 1.15, 8.0   # "video higher" chip: reconciled video FUEL >= 1.15 x EPA FUEL + 8 (retuned round 3)
SHRINK_K = 1.0          # pseudo-matches of the event mean in shrunk per-match video values
ACC_PRIOR = 20.0        # pseudo-shots of the event accuracy


def _tba_matches(data_root, ev):
    raw = jload(os.path.join(data_root, "tba", f"event__{ev}__matches.json"), {}) or {}
    ms = raw.get("data", raw) if isinstance(raw, dict) else raw
    return [m for m in (ms or []) if isinstance(m, dict)]


def _played_tba(m):
    return (m.get("alliances", {}).get("red", {}).get("score") or -1) >= 0 and m.get("score_breakdown")


LVL_PTS = {0: 0, 1: 10, 2: 20, 3: 30}


def _tba_robot_rows(tms, asof=None):
    """{team: [{key, t, al, end_level, auto_l1, alliance_auto_fuel, score, partners}]} from TBA quals (played, <= asof)."""
    out = defaultdict(list)
    for m in sorted(tms, key=lambda m: COV.match_order(m["key"])):
        if m.get("comp_level") != "qm" or not _played_tba(m):
            continue
        t = m.get("actual_time") or m.get("time") or 0
        if asof is not None and t and t > asof:
            continue
        for al in ("red", "blue"):
            bd = m["score_breakdown"].get(al) or {}
            tks = m["alliances"][al]["team_keys"]
            afuel = (bd.get("totalAutoPoints") or 0) - (bd.get("autoTowerPoints") or 0)
            for i, tk in enumerate(tks):
                out[tk].append({"key": m["key"], "t": t, "al": al, "end_level": _lvl(bd.get(f"endGameTowerRobot{i + 1}")),
                                "auto_l1": bd.get(f"autoTowerRobot{i + 1}") not in (None, "None"),
                                "alliance_auto_fuel": afuel, "score": m["alliances"][al]["score"],
                                "fouls_drawn": bd.get("foulPoints") or 0,
                                "partners": [x for x in tks if x != tk]})
    return out


def _opr(tms):
    """{team: OPR (score minus fouls drawn)} by least squares over played quals in `tms` (as-of view)."""
    rows, ys, idx = [], [], {}
    for m in tms:
        if m.get("comp_level") != "qm" or not _played_tba(m):
            continue
        for al in ("red", "blue"):
            tks = m["alliances"][al]["team_keys"]
            for t in tks:
                idx.setdefault(t, len(idx))
            rows.append([idx[t] for t in tks])
            ys.append(m["alliances"][al]["score"] - ((m["score_breakdown"].get(al) or {}).get("foulPoints") or 0))
    if len(rows) < len(idx):
        return {}
    A = np.zeros((len(rows), len(idx)))
    for i, r in enumerate(rows):
        A[i, r] = 1.0
    x = np.linalg.lstsq(A, np.array(ys, float), rcond=None)[0]
    return {t: r_(x[i], 1) for t, i in idx.items()}


def _sv(v, raw, n, **kw):
    d = {"v": r_(v, 1) if v is not None else None, "raw": r_(raw, 1) if raw is not None else None, "n": n,
         "tier": COV.tier(n)}
    d.update({k: x for k, x in kw.items() if x is not None})
    return d


def build_truth(api_ev, ev, matches, specs, teams_meta, data_root=None, log=print, out_dir=None, asof=None, out_path=None):
    data_root = data_root or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(api_ev))))
    tms = _tba_matches(data_root, ev)
    rows_tba = _tba_robot_rows(tms, asof)
    if asof is not None:
        tms = [m for m in tms if (m.get("actual_time") or m.get("time") or 0) <= asof]
    played = {t: len(v) for t, v in rows_tba.items()}
    mds = [M.d for M in matches]
    # coverage counts quals only, like `played` (TBA quals) and the dashboard -- playoffs made it read "15/12"
    cov = COV.team_coverage([d for d in mds if "_qm" in str(d.get("key") or "")], played)
    dash = jload(os.path.join(api_ev, "dashboard.json")) or {}
    if asof is not None:                    # ratings as they stood: the latest dashboard snapshot <= asof
        ts_ = sorted(int(f.split(".")[0]) for f in os.listdir(os.path.join(api_ev, "dash")) if f.split(".")[0].isdigit()) \
            if os.path.isdir(os.path.join(api_ev, "dash")) else []
        ts_ = [t for t in ts_ if t <= asof]
        dash = (jload(os.path.join(api_ev, "dash", f"{ts_[-1]}.json")) or {}) if ts_ else {}
    rat = {r["team"]: r for r in dash.get("ratings") or []}
    # reconciliation per verified match
    recon = {M.key: COV.reconcile(M.d) for M in matches}
    team_rec = defaultdict(list)            # team -> [(match short, made credited, scaled made, lower_bound, over, al)]
    for M in matches:
        for al, r in recon[M.key].items():
            for tk in M.verified:
                al_t = next((t.alliance for t in M.tracks if t.label == tk), None)
                if al_t != al:
                    continue
                made = r["teams"].get(tk, 0)
                team_rec[tk].append({"m": M.short, "key": M.key, "made": made, "scaled": r_(made * r["scale"], 1),
                                     "lower_bound": r["lower_bound"], "over": r["over"], "gate": r.get("gate"),
                                     "unattributed": r["unattributed"], "alliance_tba": r["tba"]})
    # event means for shrinkage (verified teams only)
    pm = [np.mean([x["scaled"] for x in v if x["gate"] == "ok"]) for v in team_rec.values() if any(x["gate"] == "ok" for x in v)]
    ev_made = float(np.mean(pm)) if pm else None
    accs = [(sp["scoring"]["made"], sp["scoring"]["made"] + sp["scoring"]["missed"]) for sp in specs.values()]
    ev_acc = (sum(a for a, b in accs) / max(1, sum(b for a, b in accs))) if accs else None
    autos = [sp["auto"]["made_avg"] for sp in specs.values() if sp["auto"]["made_avg"] is not None]
    ev_auto = float(np.mean(autos)) if autos else None
    cyc = [sp["scoring"]["cycle_s"] for sp in specs.values() if sp["scoring"]["cycle_s"] is not None]
    ev_cyc = float(np.median(cyc)) if cyc else None
    # climbs (TBA per robot, every played qual)
    tower = {}
    for tk, rs in rows_tba.items():
        lv = Counter(r["end_level"] for r in rs if r["end_level"] is not None)
        n = sum(lv.values())
        tower[tk] = {"n": n, "levels": {str(k): v for k, v in sorted(lv.items())},
                     "rate": r_(sum(v for k, v in lv.items() if k) / n, 2) if n else None,
                     "pts_mean": r_(sum(LVL_PTS.get(k, 0) * v for k, v in lv.items()) / n, 1) if n else None,
                     "auto_l1_rate": r_(np.mean([r["auto_l1"] for r in rs]), 2) if rs else None,
                     "keys": [r["key"] for r in rs]}
    tower_max = max([v["pts_mean"] or 0 for v in tower.values()] or [0])
    opr_asof = _opr(tms) if asof is not None else {}
    # blend history from the dashboard snapshots (trend)
    hist = defaultdict(list)
    snaps = sorted(p for p in glob_json(os.path.join(api_ev, "dash"))
                   if asof is None or (os.path.basename(p).split(".")[0].isdigit() and int(os.path.basename(p).split(".")[0]) <= asof))
    for p in snaps:
        try:
            ts = int(os.path.basename(p).split(".")[0])
        except ValueError:
            continue
        d = jload(p) or {}
        for r in d.get("ratings") or []:
            b = r.get("blend") or {}
            if b.get("total") is not None:
                hist[r["team"]].append([ts, r_(b["total"], 1), r_(b.get("sd"), 1), r_((r.get("epa") or {}).get("total"), 1)])
    teams = []
    keys = sorted(set(teams_meta) | set(rows_tba), key=lambda k: int(k[3:]) if k[3:].isdigit() else 0)
    last_v = max((M.key for M in matches), key=COV.match_order, default=None)
    for tk in keys:
        meta = teams_meta.get(tk, {})
        c = cov.get(tk) or {"n": 0, "keys": [], "last": None, "of": played.get(tk), "tier": "none"}
        c = dict(c, label=COV.label(c) if c.get("n") else "no verified video")
        r = rat.get(tk) or {}
        b = r.get("blend") or {}
        e = r.get("epa") or {}
        sp = specs.get(tk)
        tr = team_rec.get(tk) or []
        row = {"team": tk, "number": int(tk[3:]) if tk[3:].isdigit() else tk, "nickname": meta.get("nickname") or r.get("nickname"),
               "rank": meta.get("rank") or r.get("rank"), "coverage": c,
               "rating": {"blend": r_(b.get("total"), 1), "sd": r_(b.get("sd"), 1), "auto": r_(b.get("auto"), 1),
                          "teleop": r_(b.get("teleop"), 1), "endgame": r_(b.get("endgame"), 1),
                          "epa": r_(e.get("total"), 1), "epa_source": e.get("source"),
                          "opr": meta.get("opr") if asof is None else opr_asof.get(tk),
                          "video_weight": r.get("video_weight"), "record": r.get("record"),
                          "p_top8": (r.get("projection") or {}).get("p_top8")} if r or meta.get("opr") is not None else None,
               "climb": tower.get(tk), "flags": [], "video": None}
        # TBA trend: alliance score minus partners' end-of-data blend, last 4 vs earlier
        rs = rows_tba.get(tk) or []
        contrib = []
        for x in rs:
            pb = [(rat.get(p) or {}).get("blend", {}).get("total") for p in x["partners"]]
            if all(v is not None for v in pb):
                contrib.append(x["score"] - x["fouls_drawn"] - sum(pb))
        tt = None
        if len(contrib) >= 6:
            a_, b_ = np.array(contrib[:-4]), np.array(contrib[-4:])
            diff = b_.mean() - a_.mean()
            se = math.sqrt(a_.var(ddof=1) / len(a_) + b_.var(ddof=1) / len(b_))
            tt = {"last_n": 4, "last": r_(b_.mean(), 0), "before": r_(a_.mean(), 0), "diff": r_(diff, 0),
                  "ci": [r_(diff - 1.64 * se, 0), r_(diff + 1.64 * se, 0)],
                  "verdict": "up" if diff - 1.64 * se > 0 else "down" if diff + 1.64 * se < 0 else "flat"}
        h = hist.get(tk) or []
        if len(h) > 14:
            idx = np.unique(np.linspace(0, len(h) - 1, 14).round().astype(int))
            h = [h[i] for i in idx]
        row["trend"] = {"tba": tt, "blend": h if len(h) >= 3 else None}
        if sp and c["n"]:
            S, A = sp["scoring"], sp["auto"]
            n = c["n"]
            tro = [x for x in tr if x["gate"] == "ok"]          # reconciled alliance results only
            nf = len(tro)
            raw_pm = float(np.mean([x["scaled"] for x in tro])) if tro else None
            lb = False                                           # under-credited results are dropped, not floored
            over = any(x["over"] for x in tro)
            gates = dict(Counter(x["gate"] for x in tr))
            shr = (raw_pm * nf + SHRINK_K * ev_made) / (nf + SHRINK_K) if (raw_pm is not None and ev_made is not None) else raw_pm
            mm = S["made"] + S["missed"]
            acc_raw = S["made"] / mm if mm else None
            acc_v = (S["made"] + ACC_PRIOR * ev_acc) / (mm + ACC_PRIOR) if (mm and ev_acc is not None) else None
            au_raw = A["made_avg"]
            na = A.get("n_fuel") or 0
            au_v = (au_raw * na + SHRINK_K * ev_auto) / (na + SHRINK_K) if (au_raw is not None and ev_auto is not None) else au_raw
            cy_raw = S["cycle_s"]
            ncy = max(0, len(sp.get("paths") or []))
            # dead share from verified matches' analytics (only shown at n >= SHOW_N)
            dead = []
            for M in matches:
                for t in M.tracks:
                    if t.team == tk:
                        an = (M.d.get("analytics") or [None] * 99)
                        a = an[t.k] if t.k < len(an) else None
                        if a and a.get("reliability"):
                            rl = a["reliability"]
                            dd = bool(rl.get("dead")) and (rl.get("dead_s") or 0) >= DEAD_MIN_S
                            if dd:                 # corroborate with TBA: the alliance scored well under expectation
                                sc_ = ((M.d.get(t.alliance) or {}).get("score"))
                                exp_ = sum(((rat.get(x) or {}).get("blend") or {}).get("total") or 0
                                           for x in ((M.d.get(t.alliance) or {}).get("teams") or []))
                                dd = sc_ is not None and exp_ > 0 and sc_ < DEAD_TBA_FRAC * exp_
                            dead.append(dd)
            auto_bad = None
            if row["rating"] and row["rating"]["auto"] is not None and au_raw is not None:
                # blend auto includes the auto tower (15 pts x P(L1)); compare FUEL with FUEL
                mod = row["rating"]["auto"] - 15.0 * ((row["climb"] or {}).get("auto_l1_rate") or 0.0)
                if mod >= 12 and au_raw <= 0.3 * mod:
                    auto_bad = {"video": au_raw, "model": mod}
                    row["flags"].append({"k": "auto_not_tracked",
                                         "text": f"auto not tracked: video credited {au_raw:.0f} auto FUEL/match, TBA-based model says ~{mod:.0f}"})
            row["video"] = {
                "made_pm": (_sv(shr, raw_pm, nf, over=over or None, gates=gates,
                               per_match=[{"m": x["m"], "made": x["made"], "scaled": x["scaled"], "gate": x["gate"],
                                           "un": x["unattributed"]} for x in tr]) if nf else None),
                "fuel_gate": gates,
                "acc": {"v": r_(acc_v, 3), "raw": r_(acc_raw, 3), "made": S["made"], "shots": mm, "n": nf, "tier": COV.tier(nf)} if nf and mm else None,
                "auto_made": None if (auto_bad or not na or au_raw is None) else _sv(au_v, au_raw, na),
                "cycle_s": _sv(cy_raw, cy_raw, n, volleys=S.get("n_volleys")) if cy_raw is not None else None,
                "fire": _sv(S["fire_rate"], S["fire_rate"], n) if S["fire_rate"] is not None else None,
                "hopper": _sv(S["hopper_est"] or S["volley_max"], S["volley_max"], n) if (S["hopper_est"] or S["volley_max"]) else None,
                "pts_active_min": _sv(S["pts_per_active_min"], S["pts_per_active_min"], n) if S["pts_per_active_min"] is not None else None,
                "speed_p95": _sv(sp["drive"]["p95_speed"], sp["drive"]["p95_speed"], n) if sp["drive"]["p95_speed"] is not None else None,
                "trench": sp["lanes"].get("trench_share"), "on_move": S.get("on_move"),
                "dead": {"v": r_(np.mean(dead), 2), "n": len(dead)} if len(dead) >= COV.SHOW_N else None,
                "def_played": ((sp.get("defense") or {}).get("played") or {}).get("n"),
                "def_received": ((sp.get("defense") or {}).get("received") or {}).get("n"),
                "def_matches": len(((sp.get("defense") or {}).get("played") or {}).get("matches") or []),
                "def_secs": ((sp.get("defense") or {}).get("played") or {}).get("secs"),
            }
            # video vs model: positive evidence only (critics: every "video says worse" call was attribution loss)
            ep = (rat.get(tk) or {}).get("epa") or {}
            if ep.get("total") is not None and raw_pm is not None:      # vs the TBA-only EPA (the blend already has this video)
                mfuel = (ep.get("auto") or 0) + (ep.get("teleop") or 0)
                if nf >= 2 and raw_pm >= 25 and raw_pm >= VH_MULT * mfuel + VH_ADD:
                    row["flags"].append({"k": "video_higher", "text": f"video higher: {raw_pm:.0f} FUEL/match credited in "
                                         f"{nf} reconciled matches vs EPA {mfuel:.0f}"})
                elif nf >= COV.SHOW_N and not over and raw_pm <= 0.6 * mfuel - 10:
                    row["flags"].append({"k": "video_lower", "text": f"video lower ({raw_pm:.0f} vs {mfuel:.0f}) in {nf} reconciled "
                                         "matches: check the replays -- low video counts were attribution loss every time at 2026wasam"})
            if n and not nf:
                row["flags"].append({"k": "no_reconciled", "text": f"none of {n} verified matches reconciled with TBA within 20%: "
                                     "no video FUEL numbers"})
        teams.append(row)
    out = {"schema": "cyber-scout/scout@1", "event": ev, "generated": int(time.time()),
           "verified_matches": [M.key for M in sorted(matches, key=lambda M: COV.match_order(M.key))],
           "last_verified": last_v,
           "quals_played": sum(1 for m in tms if m.get("comp_level") == "qm" and _played_tba(m)),
           "policy": {"verified_conf": COV.VERIFIED_CONF, "show_n": COV.SHOW_N, "shrink_matches": SHRINK_K,
                      "acc_prior_shots": ACC_PRIOR, "lower_bound_unattributed": 0.2},
           "event_stats": {"made_pm_mean": r_(ev_made, 1), "acc": r_(ev_acc, 3), "auto_made_mean": r_(ev_auto, 1),
                           "cycle_median": r_(ev_cyc, 1), "tower_pts_max": r_(tower_max, 1),
                           "climb_matters": bool(tower_max > 10),
                           "any_climb": any((v["rate"] or 0) > 0 for v in tower.values())},
           "dash_generated": dash.get("generated"),
           "teams": teams}
    out["asof"] = asof
    jsave(out_path or os.path.join(out_dir or api_ev, "scout.json"), out)
    log(f"scouting: scout.json {len(teams)} teams, {sum(1 for t in teams if t['coverage']['n'])} with verified video, "
        f"tower max {tower_max}")
    return out


def glob_json(d):
    try:
        return [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".json")]
    except OSError:
        return []


def site_build_extra(DATA, ev, log=print):
    """Worker hook: run after build_site(ev).  Never raises."""
    try:                 # team pages: aggregates from verified matches only (before the spec sheets / scout.json)
        try:
            from live import sitev2
        except ImportError:
            import sitev2
        sitev2.verified_team_files(DATA, ev, log=log)
    except Exception:
        import traceback
        traceback.print_exc()
    try:
        api_ev = os.path.join(DATA, "site", "api", ev)
        return build(api_ev, ev, log=log)
    except Exception:
        import traceback
        traceback.print_exc()
        return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("data")
    ap.add_argument("ev")
    ap.add_argument("--out", default=None)
    ap.add_argument("--asof", type=float, default=None, help="replay: write scout/<asof>.json only")
    ap.add_argument("--snapshots", action="store_true", help="scout/<ts>.json for every dash/<ts>.json (+ scout/index.json)")
    a = ap.parse_args()
    api_ev = os.path.join(a.data, "site", "api", a.ev)
    if a.asof is not None or a.snapshots:
        times = [a.asof] if a.asof is not None else sorted(int(f.split(".")[0]) for f in os.listdir(os.path.join(api_ev, "dash"))
                                                           if f.split(".")[0].isdigit())
        for ts in times:
            build_asof(api_ev, a.ev, ts, out_dir=a.out, log=lambda *x: None)
        od = os.path.join(a.out or api_ev, "scout")
        jsave(os.path.join(od, "index.json"), {"event": a.ev, "times": sorted(int(f.split(".")[0]) for f in os.listdir(od) if f.split(".")[0].isdigit())})
        print(f"scout snapshots: {len(times)}")
    else:
        build(api_ev, a.ev, out_dir=a.out)
