"""autoscout-live v2 robot analytics (movement-derived + event-derived metrics).

Public API
----------
match_analytics(match, events, rules, tba_match) -> {"tracks": {idx: metrics}, "alliance": {...}}
team_aggregate(per_match_list)                    -> aggregated metrics per team
cluster_autos(auto_paths_by_team)                 -> routine labels per match + routine summaries

Conventions
-----------
* match = processed match JSON (xy in cm at match["hz"], frame 0 = auto start - 0.5 s).  If the
  site JSON was merged in, match["tracks"][k]["team"] ("frc1234") is used.
* All metrics are computed on "good" frames only: observed (obs==1), or interpolated across a
  gap of <= 1.5 s between two observations, and outside match["feed_gaps"].  Every metric group
  has a coverage number in out["coverage"]; team_aggregate drops matches whose coverage for a
  group is < MIN_COV so a bad-tracking match cannot poison a team average.
* Everything geometric is done in the CANONICAL RED FRAME: red robots as-is, blue robots mapped
  by the field point symmetry (x,y)->(16.541-x, 8.069-y).  There "own zone" is x > 11.915, the
  own tower / outpost / driver wall are at x ~ 16.5, and station k looks the same for both colours.
* TIME BASE: replay time (seconds from auto start = frame/hz - 0.5) differs from the MATCH CLOCK
  the rules periods use, because of a real pause between auto end (20) and teleop start.
  match_clock(t, match): t if t<=20; 20 (frozen, "pause") for 20<t<T; t-(T-20) after, with
  T = match["t_teleop_start"] - 0.5.  All period comparisons use the match clock; the pause belongs
  to no period (excluded from per-period/teleop metrics, kept in movement totals).  Shot/intake
  events' `match_t` is treated as REPLAY time from auto start and mapped the same way.  Reported
  times (arrival_s, crossings t, etc.) are on the match clock.  Raw replay time: `Prep.t_replay`.
* Metrics that need shot/intake events degrade to None when events are missing.
"""
import math
import re
import warnings
from collections import Counter

import numpy as np

warnings.filterwarnings("ignore", message="Mean of empty slice")
FIELD_L = 16.541
FIELD_W = 8.069
HUB_RED = (11.915, 4.035)
HUB_BLUE = (4.626, 4.035)
MIN_COV = 0.4          # min group coverage for a match to count in team aggregates
GAP_MAX_S = 1.5        # interpolated stretches longer than this are not trusted
V_MOVING = 0.30        # m/s
V_GLITCH = 6.0         # m/s: faster than any FRC robot -> identity swap / projection glitch
HYST = 0.75            # m hysteresis for zone transitions

# ---- fallbacks (clearly marked; used when rules file lacks the entry) ---------------------------
FALLBACK = {
    "periods": [("auto", 0, 20), ("transition", 23, 33), ("shift1", 33, 58), ("shift2", 58, 83),
                ("shift3", 83, 108), ("shift4", 108, 133), ("endgame", 133, 163)],   # FALLBACK
    "trench_band": 1.28,        # FALLBACK: trench lane y in [0,1.28] and [W-1.28,W] (from tag y=0.64/7.43)
    "bump_band": (1.28, 3.40),  # FALLBACK: bump lane (y) next to trench; hub body 3.40-4.67 is centre
    "tower_canon": (16.541, 4.10),  # FALLBACK: red tower centre (tags 15/16 y 3.89-4.32)
    "tower_reach": (1.6, 1.1),      # FALLBACK: x-depth from wall, +- y half-width
    "outpost_canon": (16.3, 7.2),   # FALLBACK: red outpost (tags 13/14, y 6.97-7.40)
    "outpost_r": 1.5,
}
FALLBACK_USED = set()


# ================================================================================ helpers
def _teleop_T(match):
    return float(match.get("t_teleop_start", 23.5)) - 0.5


def match_clock(t, match, auto_end=20.0):
    """replay-time-from-auto-start -> match clock (frozen at auto_end during the pause)."""
    t = np.asarray(t, float)
    T = _teleop_T(match)
    return np.where(t <= auto_end, t, np.where(t < T, auto_end, t - (T - auto_end)))


def in_pause(t, match, auto_end=20.0):
    t = np.asarray(t, float)
    return (t > auto_end) & (t < _teleop_T(match))


def _canon(xy, alliance):
    xy = np.asarray(xy, float)
    if alliance == "blue":
        return np.stack([FIELD_L - xy[..., 0], FIELD_W - xy[..., 1]], -1)
    return xy


def _runs(mask, min_len=1, merge_gap=0):
    """[(i0, i1_exclusive)] of True runs; gaps of <= merge_gap False frames are bridged."""
    m = np.asarray(mask, bool)
    out = []
    i, n = 0, len(m)
    while i < n:
        if m[i]:
            j = i
            while j < n and m[j]:
                j += 1
            out.append([i, j])
            i = j
        else:
            i += 1
    merged = []
    for r in out:
        if merged and r[0] - merged[-1][1] <= merge_gap:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    return [(a, b) for a, b in merged if b - a >= min_len]


def _pip(pt, poly):
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1:
            inside = not inside
    return inside


def _polys(items):
    out = []
    for it in items or []:
        pts = None
        if isinstance(it, dict):
            for k in ("polygon", "points", "xy", "corners", "poly", "vertices"):
                if k in it:
                    pts = it[k]
                    break
        elif isinstance(it, (list, tuple)):
            pts = it
        try:
            arr = [(float(p[0]), float(p[1])) for p in pts]
            if len(arr) >= 3:
                out.append(arr)
        except Exception:
            pass
    return out


def _xy(d, default):
    if isinstance(d, dict):
        for k in ("centre_m", "center_m", "center", "centre", "xy", "position"):
            v = d.get(k)
            try:
                if v is not None and len(v) >= 2:
                    return (float(v[0]), float(v[1]))
            except Exception:
                pass
    return default


class Ctx:
    """Resolved geometry/timing (rules file with explicit fallbacks)."""

    def __init__(self, rules):
        r = rules or {}
        self.rules = r
        hubs = (r.get("hub") or {}).get("centers") or {}
        self.hub = {"red": tuple(hubs.get("red", HUB_RED)), "blue": tuple(hubs.get("blue", HUB_BLUE))}
        if not hubs:
            FALLBACK_USED.add("hub centres")
        self.L_opp = self.hub["blue"][0]   # canonical: opp hub line
        self.L_own = self.hub["red"][0]
        per = r.get("periods")
        self.periods = []
        if per:
            self.periods = [(p["name"], float(p["t0"]), float(p["t1"])) for p in per]
        else:
            self.periods = list(FALLBACK["periods"])
            FALLBACK_USED.add("periods")
        self.t_end = max(p[2] for p in self.periods)
        eg = [p for p in self.periods if p[0].startswith("endgame")]
        self.t_endgame = eg[0][1] if eg else 133.0
        self.bumps = _polys(r.get("bumps"))
        self.trenches = _polys(r.get("trenches"))
        if not (self.bumps and self.trenches):
            FALLBACK_USED.add("bump/trench polygons (y-band fallback)")
        tw = (r.get("towers") or {}).get("red")
        self.tower_c = _xy(tw, FALLBACK["tower_canon"])
        if not isinstance(tw, dict) or not any(k in tw for k in ("centre_m", "center_m")):
            FALLBACK_USED.add("tower centre")
        self.tower_reach = FALLBACK["tower_reach"]
        ob = (r.get("outposts") or {}).get("red")
        self.outpost_c = _xy(ob, FALLBACK["outpost_canon"])
        if not isinstance(ob, dict):
            FALLBACK_USED.add("outpost centre")
        area = (ob or {}).get("area_m") if isinstance(ob, dict) else None
        self.outpost_area = (tuple(area["x"]), tuple(area["y"])) if area else None
        zn = r.get("zones") or {}
        self.Z_own = float(zn.get("red_alliance_x_min", self.L_own))     # canonical alliance-zone boundaries
        self.Z_opp = float(zn.get("blue_alliance_x_max", self.L_opp))
        if not zn:
            FALLBACK_USED.add("zones (hub lines used)")

    def at_post(self, c):
        """canonical point near own outpost (rules area rect if given)."""
        if self.outpost_area:
            (x0, x1), (y0, y1) = self.outpost_area
            return x0 - 0.3 <= c[0] <= x1 + 0.3 and y0 - 0.3 <= c[1] <= y1 + 0.3
        return np.hypot(c[0] - self.outpost_c[0], c[1] - self.outpost_c[1]) < FALLBACK["outpost_r"]

    def period_of(self, t):
        for n, a, b in self.periods:
            if a <= t < b:
                return n
        return None

    def lane(self, x, y):
        """Lane class at canonical point (x on a hub line): trench | bump | open."""
        for poly in self.trenches:
            if _pip((x, y), poly):
                return "trench"
        for poly in self.bumps:
            if _pip((x, y), poly):
                return "bump"
        if self.bumps and self.trenches:
            return "open"
        yy = min(y, FIELD_W - y)               # distance to nearest side wall
        if yy < FALLBACK["trench_band"]:
            return "trench"
        if yy < FALLBACK["bump_band"][1]:
            return "bump"
        return "open"                          # centre = hub body: robots can't pass -> rare


class Prep:
    """Per-track cleaned kinematics."""

    def __init__(self, match, k, ctx):
        tr = match["tracks"][k]
        hz = float(match.get("hz", 10))
        self.hz = hz
        self.alliance = tr["alliance"]
        xy = np.asarray(tr["xy"], float) / 100.0
        n = len(xy)
        self.n = n
        obs = np.asarray(tr.get("obs", np.ones(n)), int)[:n] > 0
        good = obs.copy()
        idx = np.where(obs)[0]
        if len(idx):
            prev = np.full(n, -10 ** 9)
            nxt = np.full(n, 10 ** 9)
            prev[idx] = idx
            nxt[idx] = idx
            prev = np.maximum.accumulate(prev)
            nxt = np.minimum.accumulate(nxt[::-1])[::-1]
            inter = (prev > -10 ** 8) & (nxt < 10 ** 8) & ((nxt - prev) <= GAP_MAX_S * hz)
            good |= inter
        for a, b in match.get("feed_gaps") or []:
            good[int(max(0, a * hz)): int(min(n, b * hz + 1))] = False
        self.obs = obs
        self.good = good
        self.t_replay = np.arange(n) / hz - 0.5
        self.t = match_clock(self.t_replay, match)           # MATCH CLOCK
        self.pause = in_pause(self.t_replay, match)
        self.xy = xy
        self.cxy = _canon(xy, self.alliance)
        # smoothed (5-pt moving mean on good neighbours)
        sm = xy.copy()
        for c in range(2):
            v = np.where(good, xy[:, c], np.nan)
            pad = np.pad(v, 2, constant_values=np.nan)
            win = np.stack([pad[i:i + n] for i in range(5)], 0)
            with np.errstate(all="ignore"):
                s = np.nanmean(win, 0)
            sm[:, c] = np.where(good, s, np.nan)
        self.sm = sm
        self.csm = _canon(sm, self.alliance)
        # velocity over +-0.2 s
        v = np.full((n, 2), np.nan)
        if n > 4:
            d = (sm[4:] - sm[:-4]) / (4.0 / hz)
            ok = np.all(good[0:-4][:, None] & good[4:][:, None], 1)
            # require interior also good for robustness
            for off in (1, 2, 3):
                ok &= good[off:n - 4 + off]
            d[~ok] = np.nan
            v[2:n - 2] = d
        self.v = v
        sp = np.hypot(v[:, 0], v[:, 1])
        self.glitch = sp > V_GLITCH
        sp[self.glitch] = np.nan
        sp[sp < 0.08] = 0.0           # dead-band: pixel jitter on a parked robot
        self.speed = sp
        self.zone = self._zone_states(ctx)

    def _zone_states(self, ctx):
        """0 opp / 1 neutral / 2 own (canonical), hysteresis; -1 where unknown."""
        x = self.cxy[:, 0]
        st = np.full(self.n, -1)
        cur = None
        for i in range(self.n):
            if not self.good[i]:
                continue
            xi = x[i]
            if cur is None:
                cur = 0 if xi < ctx.L_opp else (2 if xi > ctx.L_own else 1)
            else:
                if cur == 0 and xi > ctx.L_opp + HYST:
                    cur = 1
                elif cur == 1:
                    if xi < ctx.L_opp - HYST:
                        cur = 0
                    elif xi > ctx.L_own + HYST:
                        cur = 2
                elif cur == 2 and xi < ctx.L_own - HYST:
                    cur = 1
            st[i] = cur
        return st

    def raw_zone(self, ctx):
        x = self.cxy[:, 0]
        z = np.where(x < ctx.Z_opp, 0, np.where(x > ctx.Z_own, 2, 1))
        return np.where(self.good, z, -1)


def _num(x, nd=3):
    if x is None:
        return None
    try:
        if isinstance(x, (np.floating, float)) and not math.isfinite(float(x)):
            return None
        if isinstance(x, (np.bool_,)):
            return bool(x)
        if isinstance(x, (np.integer,)):
            return int(x)
        return round(float(x), nd) if isinstance(x, (np.floating, float)) else x
    except Exception:
        return None


def _pct(a, q):
    a = np.asarray(a, float)
    a = a[np.isfinite(a)]
    return float(np.percentile(a, q)) if len(a) else None


def _team_norm(t):
    if t is None:
        return None
    t = str(t)
    return t if t.startswith("frc") else ("frc" + t if t.isdigit() else t)


# ================================================================================ E movement
def _movement(p, ctx):
    n, hz = p.n, p.hz
    active = (p.t >= 0) & (p.t <= ctx.t_end)
    g = p.good & active
    cov = float(g.sum() / max(1, active.sum()))
    out = {"coverage": _num(cov)}
    if g.sum() < 20:
        return {k: None for k in ("distance_m", "distance_observed_m", "moving_speed_mean", "moving_speed_p95",
                                  "max_accel_p99", "pct_moving", "zone_frac", "zone_transitions", "crossings",
                                  "lane_counts", "trench_capable")}, cov
    # distance on consecutive good frames of the smoothed track
    d = np.hypot(*(p.sm[1:] - p.sm[:-1]).T)
    ok = g[1:] & g[:-1] & (d / (1 / hz) < V_GLITCH)
    d = np.where(ok & (d > 0.08 / hz), d, 0.0)
    dist_obs = float(d.sum())
    covered_steps = ok.sum() / max(1, active[1:].sum())
    dist_est = dist_obs / covered_steps if covered_steps >= 0.5 else None
    sp = p.speed[g]
    sp = sp[np.isfinite(sp)]
    mv = sp[sp > V_MOVING]
    # acceleration (robust: p99 of |dv/dt| on 0.4 s windows)
    a = np.hypot(*(p.v[4:] - p.v[:-4]).T) / (4.0 / hz)
    a = a[np.isfinite(a)]
    a = a[a < 25]
    zr = p.raw_zone(ctx)
    zf = {}
    for z, nm in ((2, "own"), (1, "neutral"), (0, "opp")):
        zf[nm] = _num((zr[g] == z).mean())
    # zone transitions (hysteresis) + lane-classified crossings of the hub lines
    cross = []
    st = p.zone
    last = None
    ntrans = 0
    for i in range(p.n):
        if st[i] < 0 or not active[i]:
            continue
        if last is not None and st[i] != last[0]:
            ntrans += 1
            a_, b_ = last[0], st[i]
            if abs(a_ - b_) == 1:
                line = ctx.L_opp if min(a_, b_) == 0 else ctx.L_own
                x = p.cxy[:, 0]
                s_new = np.sign(x[i] - line)
                k = i - 1
                while k > last[1] and np.sign(x[k] - line) == s_new:
                    k -= 1
                # crossing between k and k+1
                x0, x1 = x[k], x[min(k + 1, p.n - 1)]
                f = 0.5 if x1 == x0 else float(np.clip((line - x0) / (x1 - x0), 0, 1))
                y = p.cxy[k, 1] + f * (p.cxy[min(k + 1, p.n - 1), 1] - p.cxy[k, 1])
                solid = bool(p.good[k] and p.good[min(k + 1, p.n - 1)])
                lane = ctx.lane(line, y) if solid else "unknown"
                cross.append({"t": _num(p.t[k] + f / hz, 1),
                              "dir": "to_neutral" if (b_ == 1 and a_ in (0, 2)) else "to_alliance",
                              "side": "own_half" if line == ctx.L_own else "opp_half",
                              "lane": lane, "y_canon": _num(y, 2),
                              "near": bool(p.xy[k, 1] > FIELD_W / 2)})
        if last is None or st[i] != last[0]:
            last = (st[i], i)
        else:
            last = (st[i], last[1])
    lanes = Counter(c["lane"] for c in cross)
    ys = [c["y_canon"] for c in cross if c["lane"] != "unknown"]
    out.update({
        "distance_observed_m": _num(dist_obs, 1),
        "distance_m": _num(dist_est, 1),
        "moving_speed_mean": _num(mv.mean()) if len(mv) else None,
        "moving_speed_p95": _num(_pct(mv, 95)) if len(mv) > 10 else None,
        "max_speed_p99": _num(_pct(sp, 99)),
        "max_accel_p99": _num(_pct(a, 99), 2) if len(a) > 20 else None,
        "pct_moving": _num((sp > V_MOVING).mean()),
        "speed_glitch_frames": int((p.glitch & p.good).sum()),
        "zone_frac": zf,
        "zone_transitions": ntrans,
        "crossings": cross,
        "n_crossings": len(cross),
        "lane_counts": {"bump": lanes.get("bump", 0), "trench": lanes.get("trench", 0),
                        "open": lanes.get("open", 0), "unknown": lanes.get("unknown", 0)},
        # canonical-frame side: y_canon < 4.035 = "low-y" side of the driver's view (same for both colours)
        "frac_crossings_high_y_canon": _num(np.mean([y > FIELD_W / 2 for y in ys])) if ys else None,
        "frac_crossings_trench": _num(lanes.get("trench", 0) / max(1, len(cross) - lanes.get("unknown", 0)))
        if len(cross) > lanes.get("unknown", 0) else None,
        "trench_capable": bool(lanes.get("trench", 0) > 0) if len(cross) else None,
    })
    return out, cov


# ================================================================================ F autonomous
def _resample_time(p, i0, i1, m=24):
    idx = np.where(p.good[i0:i1])[0] + i0
    if len(idx) < 5:
        return None
    tt = np.linspace(idx[0], idx[-1], m)
    return np.stack([np.interp(tt, idx, p.xy[idx, 0]), np.interp(tt, idx, p.xy[idx, 1])], 1)


def _auto(p, match, ctx, tba_level):
    hz = p.hz
    i1 = min(p.n, int((match.get("t_auto_end", 20.5)) * hz) + 1)
    i0 = 0
    g = p.good[i0:i1]
    cov = float(g.mean()) if len(g) else 0.0
    out = {"coverage": _num(cov), "start_xy": None, "start_xy_canon": None, "path": None, "end_xy": None,
           "distance_m": None, "enters_neutral": None, "enters_opp_zone": None, "first_neutral_s": None,
           "tba_auto_tower": tba_level}
    if g.sum() < 10:
        return out, cov
    first = np.where(p.good[:min(p.n, int(2 * hz))])[0]
    if len(first) >= 3:
        s = np.median(p.xy[first[:10]], 0)
        out["start_xy"] = [_num(s[0], 2), _num(s[1], 2)]
        sc = _canon(s, p.alliance)
        out["start_xy_canon"] = [_num(sc[0], 2), _num(sc[1], 2)]
    last = np.where(p.good[max(0, i1 - int(1.5 * hz)):i1])[0]
    if len(last):
        e = p.xy[max(0, i1 - int(1.5 * hz)) + last[-1]]
        out["end_xy"] = [_num(e[0], 2), _num(e[1], 2)]
        ec = _canon(e, p.alliance)
        out["end_xy_canon"] = [_num(ec[0], 2), _num(ec[1], 2)]
    d = np.hypot(*(p.sm[1:i1] - p.sm[:i1 - 1]).T)
    ok = p.good[1:i1] & p.good[:i1 - 1] & (d * hz < V_GLITCH) & (d > 0.08 / hz)
    covsteps = ok.sum() / max(1, i1 - 1)
    out["distance_m"] = _num(d[ok].sum() / covsteps if covsteps > 0.5 else None, 1)
    z = p.zone[i0:i1]
    zg = z[z >= 0]
    out["enters_neutral"] = bool((zg == 1).any() or (zg == 0).any()) if len(zg) else None
    out["enters_opp_zone"] = bool((zg == 0).any()) if len(zg) else None
    ne = np.where(z == 1)[0]
    out["first_neutral_s"] = _num(p.t[ne[0]], 1) if len(ne) else None
    path = _resample_time(p, i0, i1)
    if path is not None:
        out["path"] = [[_num(a, 2), _num(b, 2)] for a, b in path]       # raw field frame, 24 pts in time
    out["usable_for_clustering"] = bool(cov >= 0.6 and path is not None)
    return out, cov


# ================================================================================ pairwise (G, J)
def _pair_arrays(P):
    N = min(p.n for p in P)
    D = np.full((6, 6, N), np.nan)
    for i in range(6):
        for j in range(6):
            if i == j:
                continue
            d = np.hypot(*(P[i].xy[:N] - P[j].xy[:N]).T)
            D[i, j] = np.where(P[i].good[:N] & P[j].good[:N], d, np.nan)
    return D, N


def _roll_mean(a, w):
    """NaN-aware centred rolling mean."""
    n = len(a)
    pad = w // 2
    x = np.pad(a, pad, constant_values=np.nan)
    win = np.stack([x[i:i + n] for i in range(w)], 0)
    with np.errstate(all="ignore"):
        return np.nanmean(win, 0)


def _defense_all(P, D, N, ctx, events):
    """Returns per-track defense dicts + masks needed by strategy / fuel effectiveness."""
    hz = P[0].hz
    t = P[0].t[:N]
    active = (t >= 0) & (t <= ctx.t_end)
    mid = FIELD_L / 2
    # directional defending masks def[i][j] (i defends j)
    defm = {}
    shadow = {}
    for i in range(6):
        for j in range(6):
            if P[i].alliance == P[j].alliance:
                continue
            near = np.isfinite(D[i, j]) & (D[i, j] < 1.5) & active
            xi = P[i].xy[:N, 0]
            # i is in the opponent's half (red robot on x<mid side, blue on x>mid)
            in_opp_half = (xi < mid) if P[i].alliance == "red" else (xi > mid)
            xj = P[j].xy[:N, 0]
            j_home = (xj > mid) if P[j].alliance == "red" else (xj < mid)
            m = near & in_opp_half & j_home
            r = np.zeros(N, bool)
            for a, b in _runs(m, int(2 * hz), int(0.5 * hz)):   # >=2 s sustained: kills incidental passing
                r[a:b] = True
            defm[(i, j)] = r
            vi, vj = P[i].v[:N], P[j].v[:N]
            ni, nj = np.hypot(*vi.T), np.hypot(*vj.T)
            with np.errstate(all="ignore"):
                cs = np.where((ni > V_MOVING) & (nj > V_MOVING), (vi * vj).sum(1) / (ni * nj), np.nan)
            rm = _roll_mean(cs, int(3 * hz))
            shadow[(i, j)] = r & (rm > 0.5)
    res = {}
    for i in range(6):
        opp = [j for j in range(6) if P[j].alliance != P[i].alliance]
        mine = np.zeros(N, bool)
        sh = np.zeros(N, bool)
        be = np.zeros(N, bool)
        for j in opp:
            mine |= defm[(i, j)]
            sh |= shadow[(i, j)]
            be |= defm[(j, i)]
        res[i] = {"defending": mine, "shadowing": sh, "defended": be}
    # contacts (opposing centre distance < 1.0 m, >= 0.4 s, merge gaps <= 0.5 s)
    contacts = {i: [] for i in range(6)}
    pins_by = {i: 0 for i in range(6)}
    pinned = {i: 0 for i in range(6)}
    pin_s_by = {i: 0.0 for i in range(6)}
    pin_s_v = {i: 0.0 for i in range(6)}
    for i in range(6):
        for j in range(i + 1, 6):
            if P[i].alliance == P[j].alliance:
                continue
            m = np.isfinite(D[i, j]) & (D[i, j] < 1.0) & (t > 1.0) & (t <= ctx.t_end)
            for a, b in _runs(m, max(1, int(0.4 * hz)), int(0.5 * hz)):
                dur = (b - a) / hz
                si = np.nanmedian(P[i].speed[a:b]) if np.isfinite(P[i].speed[a:b]).any() else np.nan
                sj = np.nanmedian(P[j].speed[a:b]) if np.isfinite(P[j].speed[a:b]).any() else np.nan
                pre = slice(max(0, a - int(1.5 * hz)), a)
                pi = np.nanmean(P[i].speed[pre]) if np.isfinite(P[i].speed[pre]).any() else 0.0
                pj = np.nanmean(P[j].speed[pre]) if np.isfinite(P[j].speed[pre]).any() else 0.0
                rec = {"t0": _num(t[a], 1), "dur": _num(dur, 1), "med_speed": [_num(si, 2), _num(sj, 2)]}
                for me, ot, sm_, so_, pm_, po_ in ((i, j, si, sj, pi, pj), (j, i, sj, si, pj, pi)):
                    r = dict(rec, opp=ot, pin=None)
                    contacts[me].append(r)
                # pin: sustained >=2 s, victim nearly stopped, and not a tower/outpost/mutual stall
                if dur >= 2.0 and np.isfinite(si) and np.isfinite(sj):
                    vic, att = (i, j) if si < sj else (j, i)
                    sv, sa = min(si, sj), max(si, sj)
                    cpos = P[vic].cxy[a:b].mean(0)
                    at_tower = np.hypot(cpos[0] - ctx.tower_c[0], cpos[1] - ctx.tower_c[1]) < 2.2
                    at_post = ctx.at_post(cpos)
                    if sv < 0.3 and sa >= 0.0 and not at_tower and not at_post:
                        # attacker = the one that was moving beforehand (arrived); else ambiguous mutual stall
                        arrived = (pi if att == i else pj) > (pj if att == i else pi)
                        pins_by[att] += 1
                        pinned[vic] += 1
                        pin_s_by[att] += dur
                        pin_s_v[vic] += dur
                        for r in contacts[vic][-1:]:
                            r["pin"] = "victim"
                        for r in contacts[att][-1:]:
                            r["pin"] = "pinner"
    for i in range(6):
        res[i]["contacts"] = contacts[i]
        res[i]["pins"] = pins_by[i]
        res[i]["pinned"] = pinned[i]
        res[i]["pin_s"] = pin_s_by[i]
        res[i]["pinned_s"] = pin_s_v[i]
    return res


def _defense_out(i, res, P, D, N, ctx, shots_by, cov):
    hz = P[i].hz
    t = P[i].t[:N]
    active = (t >= 0) & (t <= ctx.t_end)
    r = res[i]
    s = lambda m: float(m.sum() / hz)
    tot = float(active.sum() / hz)
    out = {
        "coverage": _num(cov),
        "defending_s": _num(s(r["defending"]), 1),
        "defending_frac": _num(s(r["defending"]) / tot) if tot else None,
        "shadowing_s": _num(s(r["shadowing"]), 1),
        "defended_s": _num(s(r["defended"]), 1),
        "defended_frac": _num(s(r["defended"]) / tot) if tot else None,
        "n_contacts": len(r["contacts"]),
        "contact_s": _num(sum(float(c["dur"]) for c in r["contacts"]), 1),
        "pins_made": r["pins"], "pinned_count": r["pinned"],
        "pin_s_made": _num(r["pin_s"], 1), "pinned_s": _num(r["pinned_s"], 1),
        "defended_runs": len(_runs(r["defended"], 1)),
        "mean_defended_run_s": _num(np.mean([(b - a) / hz for a, b in _runs(r["defended"], 1)]), 1)
        if r["defended"].any() else None,
        "contacts": r["contacts"][:30],
        "defense_effectiveness": None, "retained_rate": None,
    }
    # event-dependent: shot rate when defended vs not (victim view), and opp rate reduction (defender view)
    if shots_by is not None:
        zr = P[i].raw_zone(ctx)[:N]
        can_shoot = (zr == 2) & active          # time in own alliance zone = time able to score
        def rate(mask, k):
            sec = float(mask.sum() / hz)
            if sec < 8:
                return None, sec
            ts = np.array([x for x in shots_by.get(k, [])])
            if not len(ts):
                return 0.0, sec
            idx = np.clip(((ts + 0.5) * hz).astype(int), 0, N - 1)
            return float(mask[idx].sum() / sec * 60.0), sec
        rd, sd = rate(can_shoot & r["defended"], i)
        ru, su = rate(can_shoot & ~r["defended"], i)
        out["retained_rate"] = {"shots_per_min_defended": _num(rd, 1), "shots_per_min_undefended": _num(ru, 1),
                                "defended_own_zone_s": _num(sd, 1), "undefended_own_zone_s": _num(su, 1),
                                "retained": _num(rd / ru) if (rd is not None and ru) else None}
        # defender view: opponents' rate while I defend them vs otherwise
        effs = []
        for j in range(6):
            if P[j].alliance == P[i].alliance:
                continue
            m = res[i]["defending"] & (D[i, j] < 1.5)
            zj = (P[j].raw_zone(ctx)[:N] == 2) & active
            a, sa = rate(zj & m, j)
            b, sb = rate(zj & ~m & ~res[j]["defended"], j)
            if a is not None and b:
                effs.append((a, b, sa))
        if effs:
            w = np.array([e[2] for e in effs])
            out["defense_effectiveness"] = {
                "opp_shots_per_min_when_defended_by_me": _num(np.average([e[0] for e in effs], weights=w), 1),
                "opp_shots_per_min_otherwise": _num(np.average([e[1] for e in effs], weights=w), 1),
                "reduction": _num(1 - np.average([e[0] for e in effs], weights=w) /
                                  max(1e-6, np.average([e[1] for e in effs], weights=w)))}
    return out


# ================================================================================ H endgame
def _in_tower(p, ctx):
    c = p.cxy
    reach, half = ctx.tower_reach
    return (c[:, 0] > ctx.tower_c[0] - reach) & (np.abs(c[:, 1] - ctx.tower_c[1]) < half) & p.good


def _endgame(p, ctx, level_end, events_last_shot):
    hz = p.hz
    tw = _in_tower(p, ctx)
    tmask = (p.t > 0)
    cov_end = float(p.good[p.t >= ctx.t_endgame - 5].mean()) if (p.t >= ctx.t_endgame - 5).any() else 0.0
    out = {"coverage": _num(cov_end), "tower_visits": None, "arrival_s": None, "at_tower_at_end": None,
           "tower_dwell_s": None, "tower_stopped_s": None, "climb_duration_proxy_s": None,
           "left_scoring_area_s": None, "last_shot_s": events_last_shot, "tba_end_level": level_end,
           "failed_climb_attempt": None, "climb_success": None, "climb_attempt_detected": None,
           "climb_status": None}
    if cov_end < 0.3:
        return out, cov_end
    runs = _runs(tw & tmask, int(3 * hz), int(1.0 * hz))
    out["tower_visits"] = len(runs)
    end_i = int(np.searchsorted(p.t, ctx.t_end))
    final = None
    for a, b in runs:
        if b >= end_i - int(5 * hz) or b >= p.n - int(3 * hz):
            final = (a, b)
    out["at_tower_at_end"] = bool(final is not None)
    if final:
        a, b = final
        b = min(b, end_i)
        out["arrival_s"] = _num(p.t[a], 1)
        out["tower_dwell_s"] = _num((b - a) / hz, 1)
        st = np.nan_to_num(p.speed[a:b], nan=0.0) < 0.2
        out["tower_stopped_s"] = _num(st.sum() / hz, 1)
        # climb proxy: from arrival to end of the match, in which the robot first settles
        out["climb_duration_proxy_s"] = _num(min(ctx.t_end, p.t[min(b, p.n - 1)]) - p.t[a], 1)
        hub = np.array(ctx.hub["red"])
        near_hub = (np.hypot(*(p.cxy[:a] - hub).T) < 3.8) & p.good[:a]
        w = np.where(near_hub)[0]
        out["left_scoring_area_s"] = _num(p.t[w[-1]], 1) if len(w) else None
    # attempt evidence: >=5 s in endgame within 0.8 m of the tower centre line, mostly stationary there
    cand = tw & (p.t >= ctx.t_endgame) & (np.abs(p.cxy[:, 1] - ctx.tower_c[1]) < 0.8)
    csec = float(cand.sum() / hz)
    sfrac = float((np.nan_to_num(p.speed[cand], nan=0.0) < 0.25).mean()) if cand.any() else 0.0
    attempt = bool(csec >= 5 and sfrac >= 0.6)
    out["tower_attempt_s"] = _num(csec, 1)
    out["tower_attempt_stationary_frac"] = _num(sfrac)
    out["climb_attempt_detected"] = attempt
    ok = level_end is not None
    if ok:
        out["climb_success"] = bool(level_end not in ("None", None))
    if out["climb_success"]:
        out["climb_status"] = "climbed"
    elif attempt and ok:
        out["climb_status"] = "failed_attempt"
        out["failed_climb_attempt"] = True
    else:
        out["climb_status"] = "no_attempt_detected"      # failed_climb_attempt stays None (not evidence)
    return out, cov_end


# ================================================================================ I reliability
def _reliability(p, ctx, match):
    hz = p.hz
    cov_all = float(p.good[(p.t >= 0) & (p.t <= ctx.t_end)].mean())
    sp = p.speed
    stat = (np.nan_to_num(sp, nan=0.0) < 0.25) & p.good
    # bridge short bad stretches (<= 2 s) between stationary frames
    runs = _runs(stat | (~p.good), 1, 0)
    res = []
    for a, b in runs:
        gi = np.where(p.good[a:b])[0] + a
        if len(gi) < 0.6 * (b - a) or len(gi) < 5:
            continue
        pos = p.cxy[gi]
        spread = float(np.hypot(*(pos - np.median(pos, 0)).T).max())
        if spread > 0.7:
            continue
        t0, t1 = float(p.t[a]), float(p.t[min(b, p.n) - 1])
        if t1 < 0 or t0 > ctx.t_end:
            continue
        # robots legitimately idle through auto/transition (static auto) -> only judge from teleop start
        t_tel = 20.0   # match clock
        t0, t1 = max(t0, t_tel), min(t1, ctx.t_end)
        if t1 - t0 < 5:
            continue
        c = np.median(pos, 0)
        at_tower = np.hypot(c[0] - ctx.tower_c[0], c[1] - ctx.tower_c[1]) < 2.2
        at_post = ctx.at_post(c)
        kind = "dead"
        if at_tower and t1 >= ctx.t_endgame - 5:
            kind = "climb"
        elif at_post:
            kind = "waiting_outpost"
        res.append({"t0": _num(t0, 1), "t1": _num(t1, 1), "dur": _num(t1 - t0, 1), "kind": kind,
                    "xy_canon": [_num(c[0], 2), _num(c[1], 2)]})
    dead = [r for r in res if r["kind"] == "dead" and r["dur"] >= 15]
    stalls = [r for r in res if r["kind"] == "dead" and 5 <= r["dur"] < 15]
    feed_ok = match.get("feed_ok")
    out = {
        "coverage": _num(cov_all),
        "stationary_runs_ge15s": [r for r in res if r["dur"] >= 15],
        "dead_s": _num(sum(r["dur"] for r in dead), 1) if cov_all >= 0.5 else None,
        "dead_flag": bool(dead) if cov_all >= 0.5 else None,
        "died_at_s": _num(dead[0]["t0"], 1) if dead else None,
        "n_stalls_5_15s": len(stalls) if cov_all >= 0.5 else None,
        "low_coverage_flag": bool(cov_all < 0.6 or (feed_ok is not None and feed_ok < 0.8)),
        "observed_frac": _num(float(p.obs[(p.t >= 0) & (p.t <= ctx.t_end)].mean())),
    }
    return out, cov_all


# ================================================================================ A/B/C fuel
def _fuel(events, k, P, ctx, match, tm):
    p = P[k]
    hz = p.hz
    keys = ["n_shots", "n_made", "n_missed", "accuracy", "by_period", "est_points", "uncounted_made",
            "volleys", "fire_rate_in_volley", "volley_size_max", "volley_size_p90", "cycle_time_s",
            "gap_between_volleys_s", "shot_distance", "shoot_on_move_frac", "passes", "intakes"]
    if not events or not isinstance(events, dict):
        return {"events_present": False, **{x: None for x in keys}}, None, None
    al = p.alliance
    mine = [s for s in (events.get("shots") or [])
            if (s.get("shooter") or {}).get("track_idx") == k and (s.get("shooter") or {}).get("kind", "robot") == "robot"]
    ints = [x for x in (events.get("intakes") or []) if x.get("track_idx") == k]
    mine.sort(key=lambda s: s.get("match_t", s.get("t", 0)))
    shots = [s for s in mine if s.get("outcome") != "pass"]
    passes = [s for s in mine if s.get("outcome") == "pass"]
    mt = lambda s: float(match_clock(s.get("match_t", 0.0), match))
    rt = lambda s: float(s.get("match_t", 0.0))                       # replay time
    made = [s for s in shots if s.get("outcome") == "made"]
    missed = [s for s in shots if s.get("outcome") == "missed"]
    out = {"events_present": True, "n_shots": len(shots), "n_made": len(made), "n_missed": len(missed)}
    out["n_unknown_outcome"] = len(shots) - len(made) - len(missed)
    out["accuracy"] = _num(len(made) / (len(made) + len(missed))) if (len(made) + len(missed)) >= 3 else None
    byp = {}
    for s in mine:
        pn = "pause" if in_pause(rt(s), match) else (ctx.period_of(mt(s)) or s.get("period") or "other")
        d = byp.setdefault(pn, {"shots": 0, "made": 0, "missed": 0, "passes": 0})
        if s.get("outcome") == "pass":
            d["passes"] += 1
        else:
            d["shots"] += 1
            d["made"] += s.get("outcome") == "made"
            d["missed"] += s.get("outcome") == "missed"
    out["by_period"] = byp
    out["est_points"] = sum(1 for s in made if s.get("hub_active") is not False)
    out["uncounted_made"] = sum(1 for s in made if s.get("hub_active") is False)
    out["time_first_shot_auto_s"] = next((_num(mt(s), 1) for s in shots if mt(s) <= 20), None)
    out["time_first_shot_teleop_s"] = next((_num(mt(s), 1) for s in shots if mt(s) > 20), None)
    # volleys
    ts = [mt(s) for s in mine]
    vol = []
    for x in ts:
        if vol and x - vol[-1][-1] <= 1.2:
            vol[-1].append(x)
        else:
            vol.append([x])
    out["volleys"] = len(vol)
    big = [v for v in vol if len(v) >= 3]
    out["fire_rate_in_volley"] = _num(np.median([(len(v) - 1) / max(0.3, v[-1] - v[0]) for v in big]), 2) if big else None
    sizes = [len(v) for v in vol]
    out["volley_size_max"] = max(sizes) if sizes else None
    out["volley_size_p90"] = _num(_pct(sizes, 90), 1) if sizes else None
    out["volley_size_mean"] = _num(np.mean(sizes), 1) if sizes else None
    starts = [v[0] for v in vol]
    gaps_ss = [b - a for a, b in zip(starts[:-1], starts[1:]) if b - a < 45]
    gaps_es = [vol[i + 1][0] - vol[i][-1] for i in range(len(vol) - 1) if vol[i + 1][0] - vol[i][-1] < 45]
    out["cycle_time_s"] = _num(np.median(gaps_ss), 1) if gaps_ss else None
    out["gap_between_volleys_s"] = _num(np.median(gaps_es), 1) if gaps_es else None
    # shooting location (canonical frame so blue/red comparable) + distance/bearing to own hub
    hub = np.array(ctx.hub[al])
    org = [(s["origin"][0], s["origin"][1]) for s in shots if s.get("origin")]
    if org:
        o = np.array(org)
        dd = np.hypot(*(o - hub).T)
        bear = np.degrees(np.arctan2(o[:, 1] - hub[1], o[:, 0] - hub[0]))
        co = _canon(o, al)
        bc = np.degrees(np.arctan2(co[:, 1] - ctx.hub["red"][1], co[:, 0] - ctx.hub["red"][0]))
        mb = math.degrees(math.atan2(np.sin(np.radians(bc)).mean(), np.cos(np.radians(bc)).mean()))
        bins = np.histogram(dd, [0, 2, 3, 4, 5, 6, 99])[0] / len(dd)
        out["shot_distance"] = {"mean": _num(dd.mean(), 2), "std": _num(dd.std(), 2), "p10": _num(_pct(dd, 10), 2),
                                "p90": _num(_pct(dd, 90), 2), "bearing_canon_mean_deg": _num(mb, 0),
                                "bins_lt2_2_3_3_4_4_5_5_6_gt6": [_num(b, 2) for b in bins],
                                "origin_canon_spread_m": _num(np.hypot(*(co - co.mean(0)).T).mean(), 2),
                                "origins_canon": [[_num(a, 2), _num(b, 2)] for a, b in co[:150]]}
    else:
        out["shot_distance"] = None
    # shoot on the move
    sp = []
    for s in shots:
        i = int(round((rt(s) + 0.5) * hz))
        if 0 <= i < p.n and np.isfinite(p.speed[i]):
            sp.append(p.speed[i])
    out["shoot_on_move_frac"] = _num(np.mean(np.array(sp) > 0.5)) if len(sp) >= 3 else None
    out["shot_speed_median_ms"] = _num(np.median(sp), 2) if len(sp) >= 3 else None
    v0 = [np.linalg.norm(s["v0"]) for s in shots if s.get("v0")]
    out["launch_speed"] = {"mean": _num(np.mean(v0), 2), "cv": _num(np.std(v0) / np.mean(v0), 3)} if len(v0) >= 5 else None
    # misses: landing offset relative to hub along the shooter->hub line
    mm = []
    for s in missed:
        if s.get("landing") and s.get("origin"):
            L = np.array(s["landing"]) - hub
            u = hub - np.array(s["origin"][:2])
            u = u / (np.linalg.norm(u) + 1e-9)
            mm.append((float(L @ u), float(L @ np.array([-u[1], u[0]]))))
    if len(mm) >= 3:
        mm = np.array(mm)
        out["miss_pattern"] = {"long_short_m": _num(mm[:, 0].mean(), 2), "left_right_m": _num(mm[:, 1].mean(), 2)}
    else:
        out["miss_pattern"] = None
    # passes
    act_min = max(1e-6, (ctx.t_end) / 60.0)
    out["passes"] = {"n": len(passes), "per_min": _num(len(passes) / act_min, 2),
                     "frac_of_launches": _num(len(passes) / max(1, len(passes) + len(shots))),
                     "origins_canon": [[_num(a, 2), _num(b, 2)] for a, b in
                                       _canon([s["origin"][:2] for s in passes if s.get("origin")], al)[:80]]
                     if any(s.get("origin") for s in passes) else []}
    # intakes
    if ints:
        pts = _canon([x["xy"][:2] for x in ints if x.get("xy")], al) if any(x.get("xy") for x in ints) else np.zeros((0, 2))
        zc = Counter()
        for x_, y_ in pts:
            zc["own" if x_ > ctx.Z_own else ("opp" if x_ < ctx.Z_opp else "neutral")] += 1
        near_post = int(sum(np.hypot(x_ - ctx.outpost_c[0], y_ - ctx.outpost_c[1]) < 2.0 for x_, y_ in pts))
        out["intakes"] = {"n": len(ints), "per_min": _num(len(ints) / act_min, 2), "zone_counts": dict(zc),
                          "near_outpost": near_post,
                          "per_cycle": _num(len(ints) / max(1, len(vol)), 1),
                          "xy_canon": [[_num(a, 2), _num(b, 2)] for a, b in pts[:150]]}
    else:
        out["intakes"] = {"n": 0, "per_min": 0.0, "zone_counts": {}, "near_outpost": 0, "per_cycle": None, "xy_canon": []}
    out["last_shot_s"] = _num(max(ts), 1) if ts else None
    shot_ts = [mt(s) for s in shots]
    return out, shot_ts, len(ints)


# ================================================================================ J strategy
def _strategy(i, P, D, N, ctx, defout, fuel, reli):
    p = P[i]
    hz = p.hz
    t = p.t
    tel = (t > 20) & ~p.pause & (t <= ctx.t_end) & p.good
    zr = p.raw_zone(ctx)
    n = max(1, tel.sum())
    own = float((zr[tel] == 2).sum() / n)
    neu = float((zr[tel] == 1).sum() / n)
    opp = float((zr[tel] == 0).sum() / n)
    mates = [j for j in range(6) if j != i and P[j].alliance == p.alliance]
    cong, cong_own = [], []
    for j in mates:
        m = np.isfinite(D[i, j]) & (t[:N] >= 0) & (t[:N] <= ctx.t_end)
        if m.sum() < 100:
            continue
        c = D[i, j][m] < 1.5
        cong.append(float(c.mean()))
        mo = m & (zr[:N] == 2)
        if mo.sum() > 100:
            cong_own.append(float((D[i, j][mo] < 1.5).mean()))
    cov = float(tel.sum() / max(1, ((t > 20) & ~p.pause & (t <= ctx.t_end)).sum()))
    out = {"coverage": _num(cov), "teleop_zone_frac": {"own": _num(own), "neutral": _num(neu), "opp": _num(opp)},
           "congestion_frac": _num(np.mean(cong)) if cong else None,
           "congestion_frac_own_zone": _num(np.mean(cong_own)) if cong_own else None}
    dfrac = defout.get("defending_frac") or 0.0
    scores = {}
    ev = fuel.get("events_present")
    if reli.get("dead_flag") and (reli.get("dead_s") or 0) > 60:
        role, basis = "disabled", "movement"
    elif ev and (fuel["n_shots"] + (fuel["passes"] or {}).get("n", 0)) >= 4:
        nl = fuel["n_shots"] + fuel["passes"]["n"]
        pf = fuel["passes"]["n"] / nl
        ni = (fuel["intakes"] or {}).get("n", 0)
        scores = {"scorer": fuel["n_shots"] / nl, "feeder": pf, "collector": min(1, ni / max(1, nl)) * 0.5,
                  "defender": dfrac * 2}
        if dfrac >= 0.2 and fuel["n_shots"] < 15:
            role = "defender"
        elif pf >= 0.5:
            role = "feeder"
        elif dfrac >= 0.15:
            role = "hybrid"
        else:
            role = "scorer" if fuel["n_shots"] >= 8 else "hybrid"
        basis = "events"
    else:
        scores = {"scorer": own, "collector": neu, "defender": dfrac * 2}
        if dfrac >= 0.2 or opp >= 0.4:
            role = "defender"
        elif own >= 0.55:
            role = "scorer"
        elif neu >= 0.45:
            role = "collector"
        else:
            role = "hybrid"
        basis = "movement"
    out.update({"role": role, "role_basis": basis, "role_scores": {k: _num(v, 2) for k, v in scores.items()}})
    return out, cov


# ================================================================================ main entry
def match_analytics(match, events=None, rules=None, tba_match=None):
    ctx = Ctx(rules)
    ntr = len(match["tracks"])
    P = [Prep(match, k, ctx) for k in range(ntr)]
    key = match.get("key")
    # stations / TBA
    team = {}
    station = {}
    levels_end, levels_auto = {}, {}
    sb = (tba_match or {}).get("score_breakdown") or {}
    for k, tr in enumerate(match["tracks"]):
        tm = _team_norm(tr.get("team"))
        team[k] = tm
        station[k] = None
        levels_end[k] = levels_auto[k] = None
        if tba_match and tm:
            keys = (tba_match.get("alliances", {}).get(tr["alliance"], {}) or {}).get("team_keys") or []
            if tm in keys:
                s = keys.index(tm) + 1
                station[k] = s
                b = sb.get(tr["alliance"]) or {}
                levels_end[k] = b.get("endGameTowerRobot%d" % s)
                levels_auto[k] = b.get("autoTowerRobot%d" % s)
    D, N = _pair_arrays(P)
    dres = _defense_all(P, D, N, ctx, events)
    tracks = {}
    shots_by = None
    fuels, shot_ts_by = {}, {}
    for k in range(ntr):
        f, sts, ni = _fuel(events, k, P, ctx, match, team[k])
        fuels[k] = f
        shot_ts_by[k] = sts
    if events and isinstance(events, dict):
        shots_by = {k: [t for t in (shot_ts_by[k] or [])] for k in range(ntr)}
    for k in range(ntr):
        p = P[k]
        mv, c_mv = _movement(p, ctx)
        au, c_au = _auto(p, match, ctx, levels_auto[k])
        eg, c_eg = _endgame(p, ctx, levels_end[k], fuels[k].get("last_shot_s") if fuels[k].get("events_present") else None)
        pts = (ctx.rules.get("points") or {})
        tt = pts.get("tower_teleop") or {"level1": 10, "level2": 20, "level3": 30}
        ta = (pts.get("tower_auto") or {}).get("level1", 15)
        lv = lambda x: {"Level1": "level1", "Level2": "level2", "Level3": "level3"}.get(x)
        eg["climb_points"] = {"auto": (ta if levels_auto[k] not in (None, "None") else 0) if levels_auto[k] is not None else None,
                              "teleop": tt.get(lv(levels_end[k]), 0) if levels_end[k] is not None else None}
        rl, c_rl = _reliability(p, ctx, match)
        de = _defense_out(k, dres, P, D, N, ctx, shots_by, c_mv)
        st, c_st = _strategy(k, P, D, N, ctx, de, fuels[k], rl)
        fuels[k]["coverage"] = {"events_present": fuels[k]["events_present"], "n_shots": fuels[k].get("n_shots")}
        tracks[k] = {
            "meta": {"track_idx": k, "alliance": p.alliance, "team": team[k], "station": station[k],
                     "match": key, "match_order": _match_order(key),
                     "id_conf": match["tracks"][k].get("id_conf")},
            "coverage": {"movement": c_mv, "auto": c_au, "endgame": c_eg, "reliability": c_rl,
                         "defense": c_mv, "strategy": c_st,
                         "fuel": (1.0 if fuels[k]["events_present"] else None),
                         "feed_ok": match.get("feed_ok")},
            "movement": mv, "auto": au, "defense": de, "endgame": eg, "reliability": rl, "strategy": st,
            "fuel": fuels[k],
            "unreliable_notes": _notes(),
        }
    # alliance-level
    alliance = {}
    for al in ("red", "blue"):
        ks = [k for k in range(ntr) if P[k].alliance == al]
        pairs = {}
        for a in range(len(ks)):
            for b in range(a + 1, len(ks)):
                m = np.isfinite(D[ks[a], ks[b]]) & (P[0].t[:N] >= 0) & (P[0].t[:N] <= ctx.t_end)
                pairs["%d-%d" % (ks[a], ks[b])] = _num((D[ks[a], ks[b]][m] < 1.5).mean()) if m.sum() > 100 else None
        vals = [v for v in pairs.values() if v is not None]
        b = sb.get(al) or {}
        hs = b.get("hubScore") or {}
        alliance[al] = {"tracks": ks, "congestion_pairs": pairs, "congestion_mean": _num(np.mean(vals)) if vals else None,
                        "tba_fuel_counts": {k_: hs.get(k_) for k_ in ("autoCount", "transitionCount", "shift1Count",
                                                                       "shift2Count", "shift3Count", "shift4Count",
                                                                       "endgameCount", "totalCount", "uncounted")} if hs else None,
                        "tba_tower_points": b.get("totalTowerPoints"),
                        "n_climbing_at_end": sum(1 for k in ks if tracks[k]["endgame"].get("at_tower_at_end")) or 0,
                        "reconcile": ((events or {}).get("reconcile") or {}).get(al) if isinstance(events, dict) else None}
    return {"tracks": tracks, "alliance": alliance, "fallbacks_used": sorted(FALLBACK_USED)}


def _notes():
    return ["distance/speed: smoothed 10 Hz positions; ID swaps near contacts can inject speed glitches (counted)",
            "contacts/pins: ID swaps happen exactly when robots touch -> treat as indicative",
            "climb timing: lifted robots project off their ground position in the overhead view",
            "role without events is a movement-only guess"]


def _match_order(key):
    if not key:
        return None
    m = re.search(r"(qm|sf\dm|f\dm|qf\dm)(\d+)$", key)
    base = {"qm": 0, "qf": 1000, "sf": 2000, "f": 3000}
    if not m:
        return None
    pre = m.group(1)
    return base.get(pre[:2] if pre[:2] in base else pre[:1], 0) + int(m.group(2))


# ================================================================================ team aggregate
_SKIP = {"crossings", "contacts", "origins_canon", "xy_canon", "path", "stationary_runs_ge15s", "unreliable_notes",
         "start_xy", "end_xy", "start_xy_canon", "end_xy_canon", "role_scores"}


def _flatten(d, pre=""):
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            if k in _SKIP or k == "meta" or k == "coverage":
                continue
            out.update(_flatten(v, pre + k + "."))
    elif isinstance(d, (bool, np.bool_)):
        out[pre[:-1]] = float(d)
    elif isinstance(d, (int, float, np.floating, np.integer)) and d is not None and math.isfinite(float(d)):
        out[pre[:-1]] = float(d)
    elif isinstance(d, str):
        out[pre[:-1]] = d
    return out


def team_aggregate(per_match_list):
    """per_match_list: list of per-track metric dicts from match_analytics()["tracks"][k] (same team).
    Returns {metric: {mean, median, std, n, trend, coverage, ...}} + summaries."""
    items = sorted(per_match_list, key=lambda m: (m["meta"].get("match_order") if m["meta"].get("match_order") is not None else 1e9))
    nm = len(items)
    flat = []
    for m in items:
        f = _flatten(m)
        flat.append(f)
    keys = sorted({k for f in flat for k in f})
    metrics = {}
    for k in keys:
        group = k.split(".")[0]
        vals, xs, cats = [], [], []
        for idx, (m, f) in enumerate(zip(items, flat)):
            if k not in f:
                continue
            c = (m.get("coverage") or {}).get(group)
            if group in ("movement", "auto", "endgame", "defense", "strategy", "reliability") and c is not None and c < MIN_COV:
                continue
            if group == "fuel" and not m["fuel"].get("events_present"):
                continue
            v = f[k]
            if isinstance(v, str):
                cats.append(v)
                continue
            vals.append(v)
            xs.append(m["meta"].get("match_order") if m["meta"].get("match_order") is not None else idx)
        if cats:
            metrics[k] = {"counts": dict(Counter(cats)), "n": len(cats), "coverage": _num(len(cats) / nm)}
        elif vals:
            a = np.array(vals)
            tr = None
            if len(a) >= 4 and np.ptp(xs) > 0:
                tr = float(np.polyfit(np.array(xs, float), a, 1)[0])
            metrics[k] = {"mean": _num(a.mean(), 3), "median": _num(np.median(a), 3), "std": _num(a.std(), 3),
                          "min": _num(a.min(), 3), "max": _num(a.max(), 3), "n": len(a), "trend": _num(tr, 4),
                          "coverage": _num(len(a) / nm)}
    lanes = Counter()
    for m in items:
        for k_, v in ((m.get("movement") or {}).get("lane_counts") or {}).items():
            lanes[k_] += v
    stations = Counter(str(m["meta"].get("station")) for m in items)
    roles = Counter(m["strategy"]["role"] for m in items if m.get("strategy"))
    roles_ev = Counter(m["strategy"]["role"] for m in items if (m.get("strategy") or {}).get("role_basis") == "events")
    starts = [m["auto"].get("start_xy_canon") for m in items if (m.get("auto") or {}).get("start_xy_canon")]
    return {"n_matches": nm, "matches": [m["meta"]["match"] for m in items],
            "stations": dict(stations), "lane_totals": dict(lanes),
            "trench_capable": bool(lanes.get("trench", 0) > 0) if sum(lanes.values()) else None,
            "role_counts": dict(roles), "role_counts_events_only": dict(roles_ev),
            "auto_start_canon_mean": [_num(np.mean([s[0] for s in starts]), 2), _num(np.mean([s[1] for s in starts]), 2)] if starts else None,
            "dead_matches": sum(1 for m in items if (m.get("reliability") or {}).get("dead_flag")),
            "low_coverage_matches": sum(1 for m in items if (m.get("reliability") or {}).get("low_coverage_flag")),
            "metrics": metrics}


# ================================================================================ auto clustering
def _arc_resample(path, m=32):
    path = np.asarray(path, float)
    seg = np.hypot(*np.diff(path, axis=0).T)
    L = float(seg.sum())
    if L < 0.5:
        return np.repeat(path[:1], m, 0), L
    s = np.concatenate([[0], np.cumsum(seg)])
    q = np.linspace(0, L, m)
    return np.stack([np.interp(q, s, path[:, 0]), np.interp(q, s, path[:, 1])], 1), L


def _dtw(a, b, band=8):
    n, m = len(a), len(b)
    C = np.hypot(a[:, None, 0] - b[None, :, 0], a[:, None, 1] - b[None, :, 1])
    A = np.full((n + 1, m + 1), np.inf)
    A[0, 0] = 0
    for i in range(1, n + 1):
        for j in range(max(1, i - band), min(m, i + band) + 1):
            A[i, j] = C[i - 1, j - 1] + min(A[i - 1, j], A[i, j - 1], A[i - 1, j - 1])
    return A[n, m] / (n + m)


def cluster_autos(auto_paths_by_team, threshold=1.3):
    """auto_paths_by_team: {team: [{"match": key, "alliance": "red"|"blue", "path": [[x,y]..] (raw field m),
                                    "coverage": 0..1 (optional)}, ...]}
    Blue paths are mapped to the red frame by (x,y)->(16.541-x, 8.069-y).
    Returns {team: {"labels": {match: "A"|None}, "routines": [...], "top_routine_freq": f, "n_autos": n}}"""
    from scipy.cluster.hierarchy import fcluster, linkage
    out = {}
    for team, lst in auto_paths_by_team.items():
        items = []
        for a in lst:
            if isinstance(a, (tuple, list)):
                a = dict(zip(("match", "alliance", "path", "coverage"), a))
            if a.get("path") is None or len(a["path"]) < 3 or (a.get("coverage") is not None and a["coverage"] < 0.6):
                items.append({**a, "_ok": False})
                continue
            cp = _canon(np.asarray(a["path"], float), a.get("alliance", "red"))
            rs, L = _arc_resample(cp)
            items.append({**a, "_ok": True, "_cp": cp, "_rs": rs, "_len": L})
        ok = [it for it in items if it["_ok"]]
        labels = {it["match"]: None for it in items}
        routines = []
        if ok:
            n = len(ok)
            Dm = np.zeros((n, n))
            for i in range(n):
                for j in range(i + 1, n):
                    d = _dtw(ok[i]["_rs"], ok[j]["_rs"]) + 0.15 * abs(ok[i]["_len"] - ok[j]["_len"]) \
                        + 0.5 * float(np.hypot(*(ok[i]["_cp"][0] - ok[j]["_cp"][0])))
                    Dm[i, j] = Dm[j, i] = d
            cl = np.zeros(n, int) if n == 1 else fcluster(
                linkage(Dm[np.triu_indices(n, 1)], "average"), threshold, "distance") - 1
            order = [c for c, _ in Counter(cl).most_common()]
            for r, c in enumerate(order):
                mem = [i for i in range(n) if cl[i] == c]
                sub = Dm[np.ix_(mem, mem)]
                med = mem[int(np.argmin(sub.sum(1)))] if len(mem) > 1 else mem[0]
                rid = chr(ord("A") + r) if r < 26 else "R%d" % r
                for i in mem:
                    labels[ok[i]["match"]] = rid
                lens = [ok[i]["_len"] for i in mem]
                routines.append({
                    "id": rid, "n": len(mem), "freq": _num(len(mem) / len(ok), 2),
                    "matches": [ok[i]["match"] for i in mem], "rep_match": ok[med]["match"],
                    "rep_path_red_frame": [[_num(a, 2), _num(b, 2)] for a, b in ok[med]["_cp"]],
                    "start_xy_red_frame": [_num(ok[med]["_cp"][0][0], 2), _num(ok[med]["_cp"][0][1], 2)],
                    "end_xy_red_frame": [_num(ok[med]["_cp"][-1][0], 2), _num(ok[med]["_cp"][-1][1], 2)],
                    "mean_len_m": _num(np.mean(lens), 1),
                    "kind": "static" if np.mean(lens) < 1.0 else "moving",
                    "mean_intra_dist": _num(sub[np.triu_indices(len(mem), 1)].mean(), 2) if len(mem) > 1 else 0.0})
        out[team] = {"labels": labels, "routines": routines, "n_autos": len(ok),
                     "n_unusable": len(items) - len(ok),
                     "top_routine_freq": routines[0]["freq"] if routines else None}
    return out
