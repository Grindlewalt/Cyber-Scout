"""Shot reconstruction: who launched every FUEL flight, from where, with what 3D arc,
and whether it was made / missed / a pass (Cyber-Scout v2, see SPEC_V2.md "Events").

    reconstruct(fuel, match, cam, rules, tba_match=None, log=print) -> Events
    python live/shots.py FUEL.json MATCH.json CAMERA.json RULES.json OUT.json [TBA_MATCHES.json]

Inputs
  fuel   FuelResult of live/fuel.py (tracks: t video s, uv full-res px, airborne_score, view_ok)
  match  processed match JSON (process.py): t_start, t_teleop_start, tracks[6].xy cm @hz,
         camera_pose (already applied to `cam` by the caller, e.g. fuel.load_camera)
  cam    camera.Camera (or dict / path)
  rules  live/field/rebuilt_rules.json (dict or path)
  tba_match  TBA match dict (score_breakdown) -> hub active per shift + reconciliation

Method
  0. Candidates: fuel tracks with view_ok, >= 6 samples and airborne_score >= 0.5 (>= 0.15 if
     the track ends in a hub opening); duplicate parallel tracks of one blurred ball dropped.
  1. Physics.  Linear drag (rate C_DRAG ~ quadratic drag of a 0.2 kg / 0.15 m foam ball at
     shot speeds) + gravity + a small free constant acceleration (Gaussian prior):
     X(tau) = p + v phi1(tau) + (a - g z) phi2(tau), phi1 = (1-e^-c tau)/c, phi2 = (tau-phi1)/c.
     For a fixed launch time this is LINEAR in (p, v, a): each pixel gives two linear equations
     (DLT on undistorted normalised coordinates, reweighted by 1/depth = pixel error), so all
     hypotheses x launch times are solved as one batched least squares.  Gravity fixes the
     metric scale; the launch prior fixes the remaining monocular depth ambiguity.
  2. Shooter = discrete choice.  Hypotheses: each robot (track position at the launch time,
     z 0.55 m, sigma 0.45/0.35 m), each human player (behind the wall at the outpost, z 1.7 m),
     and "floor" (a bounce / kicked ball off the carpet below the first sighting, handicapped).
     Launch time searched on a 1/30 s grid up to 0.9 s before the first sighting.  Cost =
     reprojection chi^2 + priors + penalty for predicted flight that should have been VISIBLE
     but was not tracked.  Lowest cost wins; conf from the margin to the runner-up.
     Floor-launched non-scoring flights are dropped (hops); floor-launched made shots get
     shooter "unknown".
  3. Made = the track ENDS inside the image of the hub opening / clear funnel AND a fit with
     the last sighting anchored at the opening (3D point on its ray inside the opening) costs
     little more than the free best fit (MADE_DC; stricter into an inactive hub and for HP
     lobs), is not beaten by "came down on the carpet behind the hub", was above the rim and
     falling at the end, and no ball pops back UP out of that spot right after (bounce-out
     -> missed; the bounce track is not a new shot).  Missed = a non-scoring flight whose
     2nd half passes within MISS_PX of an opening (or the 3D arc within 1.6 m).  Pass = lands
     >= 3 m from launch away from hubs.  Flights that start and end on one robot (hopper
     churn), end low at a robot (intake), travel < 1.2 m, or are short/ill-conditioned are not
     shots.  Fragments of one flight are joined when one ballistic fit explains all pieces.
     Track repair before deciding: a hub-ending track that fits no single arc (ball in hand at the
     start, tracker switched balls) keeps its longest clean tail (split_bad); a short piece that
     re-appears in the funnel after passing behind the hub-net poles is appended to the flight
     whose arc predicts it (join_continuations); a ball still seen falling INSIDE the clear funnel
     is anchored below the rim too (end_anchors).  Human-player lobs need an end inside the
     opening only when flying away from the camera (or into an inactive hub).
     Missed vs pass (labelled by eye, .work/shots2/labels_*.json): a flight near a hub is a
     missed shot only if launched by a robot from that hub's alliance zone (+0.5 m) -- lobs from
     the neutral zone over / into the hub towards the alliance zone and human-player throws are
     passes; a pass ending at / landing within 1 m of a teammate is a feed (pass_kind "feed",
     receiver_idx).  Short (< 0.45 s), low-and-slow, off-field-landing and badly fitting
     non-scoring flights are dropped as junk.
  4. Period on the match clock (analytics.match_clock; the auto->teleop pause counts as auto).
     Hub active per shift from TBA (auto winner -> inactive in shift 1, then alternating; tie
     -> per-shift counts); fuel entering <= 3 s after a hub deactivates counts for the
     previous period.
  5. Reconciliation: traced made per alliance hub per period vs TBA hubScore (+ uncounted,
     attempts, per-shooter sums, per-period feed coverage).  See Events["reconcile"].
"""
import json, math, os, sys, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import camera as CAMM

G = 9.81
C_DRAG = 0.15                 # 1/s, linear-drag rate (k|v| with k~0.025/m at ~10 m/s)
SIG_PX = 2.5                  # px, observation noise used in chi^2
SIG_ACC = 0.7                 # m/s^2, prior on the free extra acceleration
ROBOT_H0, ROBOT_SH, ROBOT_SZ = 0.55, 0.45, 0.35   # launch-point prior for robots (m)
HP_H0, HP_SXY, HP_SZ = 1.7, 0.5, 0.4               # ... for human players
FLOOR_MARGIN = 4.0            # cost handicap of the bounce hypothesis
CROSS_PEN = 0.0               # (off) robot shooting into the OTHER alliance's hub -> flag only:
AUTO_SIDE_PEN = 0.0           # (off) penalties moved shots to far-away robots when the upstream
                              # robot track had swapped identities; the robot AT the launch wins
FLOOR_VZ = 4.5               # m/s, max upward speed of a bounce
FLOOR_SXY = 1.5               # m, bounce hypothesis: launch on the carpet near the first obs
TAU_MAX = 0.9                 # s, latest first observation after launch we search
TAU_STEP = 1 / 30.
GAP_FREE = 0.10               # s of visible-but-untracked flight that costs nothing
GAP_SIG = 0.12                # s, scale of the gap penalty beyond that
OVERLAY = [(446, 8, 1474, 134), (896, 126, 1024, 170), (28, 60, 358, 118), (1562, 60, 1890, 118),
           (616, 1004, 1304, 1072)]
HUB_POLY_R = 0.65             # m, image test: a track ENDING inside this circle at the opening height
FUNNEL_DZ = 0.20              # m, ... and up to this far above the rim (last sighting while dropping in)
FUNNEL_Z0 = 1.60              # m, ... or inside the clear funnel below it, down to this height
END_Z, END_SXY, END_SZ = 1.80, 0.30, 0.25
END_Z_DEEP = (1.55, 1.35)     # m, extra hub-entry anchors: last seen falling inside the clear funnel
HUB_OPEN_R = 0.55             # m, opening radius (hexagon ~1.06 m across)         # 3D anchor of a hub entry (last observation)
MADE_DC_INACTIVE = 8.0        # ... into a hub that is inactive at that moment
FUNNEL_POP_DZ = 0.35          # m, ... unless its arc rises this far above the rim
START_OUT_PX = 20.0           # px, a made flight must start this far outside the hub funnel
LAND_MARGIN = 8.0             # hub entry must not be worse than 'lands behind the hub' by more
MADE_DC_HP = 20.0             # ... thrown by a human player
MADE_DC = 25.0                # max cost increase of the hub-entry anchored fit vs the free best
HUB_R_NEAR = 1.6              # m, "aimed at the hub" if the descending arc passes this close
MIN_APEX, MIN_SPEED = 1.0, 3.0   # below either: a hop / kicked ball, not a shot
MIN_TRAVEL = 1.2              # m, observed horizontal travel of a non-scoring shot
END_AT_ROBOT = 0.9            # m, a flight ending low this close to a robot = intake/hopper
PASS_MIN_RANGE, PASS_MIN_APEX = 3.0, 1.5
MISS_PX = 40.0                # px, a flight passing this close to an opening was aimed at it
MAX_SPEED = 16.0              # m/s, faster launches are fit failures
MIN_DUR_OUTCOME = 0.45        # s, shorter non-scoring flights are dropped (labelled: all junk)
HOP_APEX, HOP_SPEED = 1.9, 6.0  # m, m/s: non-scoring flights below BOTH are pop-ups / hops
LAND_OUT_M = 0.5              # m, non-scoring flights landing further outside the field are junk
JUNK_RMS = 20.0               # px, non-scoring flights fitting this badly are junk
ZONE_MARGIN = 0.5             # m, 'launched from the hub's alliance zone' tolerance
FEED_R = 1.0                  # m, a pass landing this close to a teammate = feed
SPLIT_RMS = 6.0               # px, a hub-ending track fitting worse than this is split ...
SPLIT_TAILS = (1.0, 0.8, 0.6, 0.45)   # s, ... keeping the longest tail that is one clean arc
SPLIT_TAIL_RMS = 4.5          # px
MAX_RMS = 6.0                 # px, worse fits -> shooter unknown
JOIN_GAP = 0.8                # s, max gap when joining fragments of one flight
BOUNCE_DT, BOUNCE_PX = 0.25, 30.0
GRACE_S = 3.0                 # s, fuel still counts after its hub deactivates
VOLLEY_GAP = 1.0              # s between consecutive balls of one volley
MIN_AIR = 0.5                 # airborne_score of candidate tracks
MIN_AIR_HUB = 0.15            # ... of candidates that end in a hub opening
MIN_VIEW = 0.5
MIN_N = 6
DUP_R = 1.4                   # duplicate tracks: closer than this many ball radii
CONT_MIN_N = 3                # continuation piece (re-appearing in the funnel after an occluder): min samples
CONT_GAP = 0.35               # s, max gap before the continuation piece
CONT_NEAR_PX = 60.0           # px, the flight must end this close to an opening (funnel hull)


# ============================================================================ helpers
def _load(x):
    if isinstance(x, (str, os.PathLike)):
        return json.load(open(x))
    return x


def _camera(cam):
    return cam if isinstance(cam, CAMM.Camera) else CAMM.Camera(_load(cam))


def _phi(tau, c=C_DRAG):
    tau = np.asarray(tau, float)
    if c < 1e-6:
        return tau, 0.5 * tau * tau
    e = -np.expm1(-c * tau)
    p1 = e / c
    return p1, (tau - p1) / c


def traj(th, tau, c=C_DRAG):
    """th = [p(3), v(3), a(3)] -> positions at tau (..., 3)."""
    p1, p2 = _phi(tau, c)
    p1, p2 = p1[..., None], p2[..., None]
    acc = th[6:9] + np.array([0, 0, -G])
    return th[0:3] + th[3:6] * p1 + acc * p2


def traj_vel(th, tau, c=C_DRAG):
    tau = np.asarray(tau, float)[..., None]
    e = np.exp(-c * tau)
    acc = th[6:9] + np.array([0, 0, -G])
    return th[3:6] * e + acc * (1 - e) / c


class _Robots:
    """Robot ground positions (m) by video time from the match JSON (10 Hz, cm)."""

    def __init__(self, match):
        self.hz = float(match.get("hz", 10))
        self.t0 = float(match["t_start"]) - 0.5
        self.tr = match["tracks"]
        self.xy = [np.asarray(t["xy"], float) / 100.0 for t in self.tr]
        self.obs = [np.asarray(t.get("obs", np.ones(len(t["xy"]))), float) for t in self.tr]
        self.alliance = [t["alliance"] for t in self.tr]
        self.n = len(self.tr)

    def pos(self, k, t):
        """xy at video time(s) t (linear interpolation, clamped)."""
        xy = self.xy[k]
        f = (np.asarray(t, float) - self.t0) * self.hz
        f = np.clip(f, 0, len(xy) - 1.000001)
        i = np.floor(f).astype(int); w = (f - i)[..., None]
        return xy[i] * (1 - w) + xy[np.minimum(i + 1, len(xy) - 1)] * w

    def vel(self, k, t, dt=0.3):
        return (self.pos(k, t + dt / 2) - self.pos(k, t - dt / 2)) / dt

    def observed(self, k, t):
        f = int(round((t - self.t0) * self.hz))
        o = self.obs[k]
        return bool(o[min(max(f, 0), len(o) - 1)])


def _in_overlay(uv):
    u, v = uv[..., 0], uv[..., 1]
    m = np.zeros(u.shape, bool)
    for x0, y0, x1, y1 in OVERLAY:
        m |= (u >= x0) & (u <= x1) & (v >= y0) & (v <= y1)
    return m


# ============================================================================ fitting
class _Fitter:
    def __init__(self, cam):
        import cv2
        self.cv2 = cv2
        self.cam = cam
        self.R, self.tv = cam.R, cam.tvec
        self.f = float(cam.K[0, 0])

    def norm(self, uv):
        return self.cv2.undistortPoints(np.asarray(uv, float).reshape(-1, 1, 2), self.cam.K,
                                        self.cam.D).reshape(-1, 2)

    def solve(self, tau, xn, prior_mu=None, prior_sig=None, depth0=None, c=None, iters=2,
              end_tau=None, end_mu=None, end_sig=None):
        """Batched over launch-time hypotheses.
        tau (G, n) times since launch; xn (n, 2) normalised obs.
        prior_mu (G, 3) / prior_sig (3,) Gaussian prior on the launch point (None = free).
        end_tau (G,) / end_mu (3,) / end_sig (3,): Gaussian prior on the position at end_tau.
        -> th (G, 9), chi2_px (G,), prior_cost (G,), X (G, n, 3)."""
        c = C_DRAG if c is None else c
        R, tv, f = self.R, self.tv, self.f
        Gn, n = tau.shape
        p1, p2 = _phi(tau, c)                                    # (G, n)
        r1, r2, r3 = R
        cx = r1[None, :] - xn[:, 0:1] * r3[None, :]              # (n, 3)
        cy = r2[None, :] - xn[:, 1:2] * r3[None, :]
        bx = xn[:, 0] * tv[2] - tv[0]
        by = xn[:, 1] * tv[2] - tv[1]
        w = np.ones((Gn, n)) / (depth0 if depth0 is not None else 8.0)
        gz = np.array([0, 0, -G])
        # prior rows (fixed)
        npr = 3 + (3 if prior_mu is not None else 0) + (3 if end_mu is not None else 0)
        P = np.zeros((Gn, npr, 9)); pb = np.zeros((Gn, npr))
        q = 0
        for j in range(3):
            P[:, q, 6 + j] = 1.0 / SIG_ACC; q += 1
        if prior_mu is not None:
            psig = np.broadcast_to(np.asarray(prior_sig, float), (Gn, 3))
            for j in range(3):
                P[:, q, j] = 1.0 / psig[:, j]; pb[:, q] = prior_mu[:, j] / psig[:, j]; q += 1
        if end_mu is not None:
            e1, e2 = _phi(end_tau, c)
            for j in range(3):
                P[:, q, j] = 1.0 / end_sig[j]
                P[:, q, 3 + j] = e1 / end_sig[j]
                P[:, q, 6 + j] = e2 / end_sig[j]
                pb[:, q] = (end_mu[j] - gz[j] * e2) / end_sig[j]; q += 1
        for it in range(iters):
            rows, rhs = [], []
            for cc, bb in ((cx, bx), (cy, by)):
                A = np.concatenate([np.broadcast_to(cc, (Gn, n, 3)), cc[None] * p1[..., None],
                                    cc[None] * p2[..., None]], -1)          # (G, n, 9)
                b = bb[None, :] + G * cc[None, :, 2] * p2                # move gravity to rhs
                ws = w * f / SIG_PX
                rows.append(A * ws[..., None]); rhs.append(b * ws)
            A = np.concatenate(rows + [P], 1); b = np.concatenate(rhs + [pb], 1)
            At = A.transpose(0, 2, 1)
            AtA = At @ A + 1e-9 * np.eye(9)
            Atb = (At @ b[..., None])[..., 0]
            th = np.linalg.solve(AtA, Atb[..., None])[..., 0]
            X = (th[:, None, 0:3] + th[:, None, 3:6] * p1[..., None]
                 + (th[:, None, 6:9] + gz) * p2[..., None])
            depth = X @ r3 + tv[2]
            bad = ~(depth > 0.5).all(1)
            w = 1.0 / np.maximum(depth, 0.5)
        Xc = X @ R.T + tv
        pr = Xc[..., :2] / np.maximum(Xc[..., 2:3], 1e-3)
        res = (pr - xn[None]) * f
        chi2 = (res ** 2).sum((1, 2)) / SIG_PX ** 2
        chi2[bad] = 1e9
        pc = (th[:, 6:9] ** 2).sum(1) / SIG_ACC ** 2
        if prior_mu is not None:
            pc = pc + (((th[:, 0:3] - prior_mu) / psig) ** 2).sum(1)
        if end_mu is not None:
            Xe = th[:, 0:3] + th[:, 3:6] * e1[:, None] + (th[:, 6:9] + gz) * e2[:, None]
            pc = pc + (((Xe - end_mu) / end_sig) ** 2).sum(1)
        return th, chi2, pc, X


# ============================================================================ geometry
class _Field:
    def __init__(self, rules):
        r = rules
        self.hub = {k: np.array(v, float) for k, v in r["hub"]["centers"].items()}
        self.hub_z = float(r["hub"].get("opening_height_m", 1.829))
        self.L, self.W = CAMM.LEN, CAMM.WID
        z = r.get("zones", {})
        self.blue_x = float(z.get("blue_alliance_x_max", 4.028))
        self.red_x = float(z.get("red_alliance_x_min", 12.513))
        op = r.get("outposts", {})
        self.hp = {}
        for al, sgn in (("red", 1), ("blue", -1)):
            c = np.array(op.get(al, {}).get("centre_m", [16.54 if al == "red" else 0.0,
                                                           7.19 if al == "red" else 0.88]), float)
            wall = self.L if al == "red" else 0.0
            self.hp[al] = np.array([wall + sgn * 0.45, c[1], HP_H0])
        self.periods = [(p["name"], float(p["t0"]), float(p["t1"])) for p in r["periods"]]

    def zone(self, x):
        return "blue" if x < self.blue_x else ("red" if x > self.red_x else "neutral")


# ============================================================================ main
def _period(mc, periods):
    for name, a, b in periods:
        if a <= mc < b:
            return name
    if mc < periods[0][1]:
        return None
    if mc >= periods[-1][2]:
        return periods[-1][0] if mc < periods[-1][2] + 5 else None
    return None


def hub_schedule(tba_match):
    """-> {"red": {period: bool}, "blue": {...}} (None if unknown)."""
    act = {al: {"auto": True, "transition": True, "endgame": True} for al in ("red", "blue")}
    first = None                      # alliance whose hub is INACTIVE in shift1
    if tba_match and tba_match.get("score_breakdown"):
        sb = tba_match["score_breakdown"]
        hr, hb = sb["red"]["hubScore"], sb["blue"]["hubScore"]
        if hr["autoCount"] > hb["autoCount"]:
            first = "red"
        elif hb["autoCount"] > hr["autoCount"]:
            first = "blue"
        else:                         # tie: FMS random; infer from counts per shift
            odd_r = hr["shift1Count"] + hr["shift3Count"] - hr["shift2Count"] - hr["shift4Count"]
            odd_b = hb["shift1Count"] + hb["shift3Count"] - hb["shift2Count"] - hb["shift4Count"]
            if odd_r - odd_b < 0:
                first = "red"
            elif odd_r - odd_b > 0:
                first = "blue"
    for al in ("red", "blue"):
        for k in range(1, 5):
            if first is None:
                act[al][f"shift{k}"] = None
            else:
                inactive_first = (al == first)
                act[al][f"shift{k}"] = (k % 2 == 0) if inactive_first else (k % 2 == 1)
    return act, first


def _match_clock(rt, match):
    try:
        from analytics import match_clock
        return float(match_clock(rt, match))
    except Exception:                                   # pragma: no cover
        T = float(match.get("t_teleop_start", 23.5)) - 0.5
        return rt if rt <= 20 else (20.0 if rt < T else rt - (T - 20))


class Reconstructor:
    def __init__(self, match, cam, rules, tba_match=None, log=print):
        import cv2
        self.cv2 = cv2
        self.match, self.cam = match, _camera(cam)
        self.rules = _load(rules)
        self.F = _Field(self.rules)
        self.fit = _Fitter(self.cam)
        self.rob = _Robots(match)
        self.log = log
        self.tba = tba_match
        self.sched, self.first_inactive = hub_schedule(tba_match)
        self.t_start = float(match["t_start"])
        # dynamic view (process(dynamic=True)): feed_gaps = frames without ANY camera pose; shots come from the
        # fixed camera only, so its own gaps apply
        gaps = (match.get("dynamic") or {}).get("fixed_gaps", match.get("feed_gaps")) or []
        self.feed_gaps = [(self.t_start - 0.5 + a, self.t_start - 0.5 + b) for a, b in gaps]
        ang = np.linspace(0, 2 * np.pi, 25)[:-1]
        self.hub_poly = {}
        for al, c in self.F.hub.items():
            P = np.c_[c[0] + HUB_POLY_R * np.cos(ang), c[1] + HUB_POLY_R * np.sin(ang),
                      np.full(len(ang), self.F.hub_z)]
            self.hub_poly[al] = self.cam.project(P).astype(np.float32)
        # the funnel under the opening is clear: a made ball is often last seen INSIDE it,
        # below the rim -> end test on the image hull of the opening ring swept down
        self.hub_funnel = {}
        for al, c in self.F.hub.items():
            P = np.concatenate([np.c_[c[0] + HUB_POLY_R * np.cos(ang), c[1] + HUB_POLY_R * np.sin(ang),
                                      np.full(len(ang), z)] for z in (FUNNEL_Z0, self.F.hub_z + FUNNEL_DZ)])
            self.hub_funnel[al] = self.cv2.convexHull(self.cam.project(P).astype(np.float32)).reshape(-1, 2)

    def period_window(self, name):
        """Video-time window of a period (auto includes the pause)."""
        T = float(self.match.get("t_teleop_start", 23.5)) - 0.5
        for n, a, b in self.F.periods:
            if n == name:
                f = lambda mc: mc if mc < 20 else mc + (T - 20)
                a_, b_ = f(a), (T if n == "auto" else f(b))
                return self.t_start + a_, self.t_start + b_
        return None

    def coverage(self, fuel):
        """Fraction of each period with live feed inside the fuel-tracked window."""
        out = {}
        f0, f1 = float(fuel.get("t_from", -1e9)), float(fuel.get("t_to", 1e9))
        # camera moving / wrong view (fuel tracks with view_ok = 0): nothing is traced there, so
        # those seconds are not covered either (0.5 s bins where view-bad tracks dominate)
        bad_bins = set()
        nb, ng = {}, {}
        for tr in fuel.get("tracks", []):
            b = int(math.floor(float(tr["t"][0]) / 0.5))
            if tr.get("view_ok", 1) < MIN_VIEW:
                nb[b] = nb.get(b, 0) + 1
            else:
                ng[b] = ng.get(b, 0) + 1
        for b, v in nb.items():
            if v > ng.get(b, 0):
                bad_bins.update((b - 1, b, b + 1))
        for n, _, _ in self.F.periods:
            a, b = self.period_window(n)
            ts = np.linspace(a, b, 200)
            ok = (ts >= f0) & (ts <= f1)
            for g0, g1 in self.feed_gaps:
                ok &= ~((ts >= g0) & (ts <= g1))
            if bad_bins:
                ok &= ~np.isin(np.floor(ts / 0.5).astype(int), list(bad_bins))
            out[n] = round(float(ok.mean()), 3)
        return out

    def hub_active_at(self, al, t_video):
        """True / False / None, incl. the 3 s grace after deactivation."""
        mc, pe = self.clock(t_video)
        if pe is None:
            return None
        act = self.sched[al].get(pe)
        if act is False and pe.startswith("shift"):
            names = [p[0] for p in self.F.periods]
            pi = names.index(pe)
            if mc - self.F.periods[pi][1] <= GRACE_S and self.sched[al].get(names[pi - 1]):
                return True
        return act

    def clock(self, t_video):
        """-> (match clock s, period).  The AUTO->TELEOP pause (and the 3 s assessment
        after the AUTO timer) belongs to auto."""
        rt = t_video - self.t_start
        mc = _match_clock(rt, self.match)
        T = float(self.match.get("t_teleop_start", 23.5)) - 0.5
        if 20.0 <= rt < max(T, 23.0):
            return mc, "auto"
        return mc, _period(mc, self.F.periods)

    # ---------------------------------------------------------------- candidates
    def candidates(self, fuel):
        out = []
        for tr in fuel["tracks"]:
            if tr.get("view_ok", 1) < MIN_VIEW or len(tr["t"]) < MIN_N:
                continue
            T = np.asarray(tr["t"], float)
            air = tr.get("airborne_score", 0)
            if air < MIN_AIR:
                # weak airborne evidence is enough for a track that ends in a hub opening
                if air < MIN_AIR_HUB or self.end_hub(T, np.asarray(tr["uv"], float)) is None:
                    continue
            if any(a <= T[0] <= b for a, b in self.feed_gaps):
                continue
            out.append(dict(ids=[tr["id"]], T=T, uv=np.asarray(tr["uv"], float),
                            r=float(np.median(tr.get("r") or [7.0])),
                            air=float(tr.get("airborne_score", 0))))
        out.sort(key=lambda c: c["T"][0])
        return self.dedupe(out)

    def dedupe(self, cands):
        """Drop duplicate tracks of ONE ball: the blob splitter sometimes cuts a
        motion-blurred ball into two parallel tracks less than a ball diameter apart."""
        drop = set()
        ends = np.array([c["T"][-1] for c in cands])
        for i, A in enumerate(cands):
            if i in drop:
                continue
            for j in range(i + 1, len(cands)):
                B = cands[j]
                if B["T"][0] > A["T"][-1]:
                    break
                if j in drop:
                    continue
                ta, tb = np.round(A["T"], 3), np.round(B["T"], 3)
                com, ia, ib = np.intersect1d(ta, tb, return_indices=True)
                if len(com) < 4:
                    continue
                d = float(np.median(np.linalg.norm(A["uv"][ia] - B["uv"][ib], axis=1)))
                if d < DUP_R * max(A["r"], B["r"], 4.0):
                    drop.add(j if len(B["T"]) <= len(A["T"]) else i)
                    if i in drop:
                        break
        self.n_dups = len(drop)
        return [c for k, c in enumerate(cands) if k not in drop]

    def end_hub(self, T, uv):
        """Alliance of the hub whose opening the track ENDS in (image test), else None."""
        n = len(uv)
        e = (float(uv[-1, 0]), float(uv[-1, 1]))
        k = max(0, n - 4)
        desc = (uv[-1, 1] - uv[k, 1]) > 0.5
        polys = self.hub_funnel if FUNNEL_Z0 < self.F.hub_z else self.hub_poly
        for al, poly in polys.items():
            if self.cv2.pointPolygonTest(poly, e, False) >= 0 and desc:
                return al
        return None

    def in_hopper(self, T, uv):
        """Track starts AND ends on the same robot's image footprint: a ball churning in a
        hopper / bouncing off a robot, not a launch."""
        for k in range(self.rob.n):
            ends = []
            for t, q in ((T[0], uv[0]), (T[-1], uv[-1])):
                xy = self.rob.pos(k, t)
                c = self.cam.project(np.r_[xy, 0.4][None])[0]
                e = self.cam.project(np.r_[xy + np.array([0.6, 0.0]), 0.4][None])[0]
                rad = max(25.0, float(np.linalg.norm(e - c)))
                ends.append(np.linalg.norm(q - c) < 1.2 * rad)
            if all(ends):
                return True
        return False

    def img_near_hub(self, uv):
        """Hub whose projected opening the second half of the track passes close to."""
        best, bd = None, MISS_PX
        k = len(uv) // 2
        for al, poly in self.hub_poly.items():
            for q in uv[k:]:
                d = -self.cv2.pointPolygonTest(poly, (float(q[0]), float(q[1])), True)
                if d < bd:
                    best, bd = al, d
        return best

    # ---------------------------------------------------------------- hypotheses
    def _sources(self):
        src = [("robot", self.rob.alliance[k], k) for k in range(self.rob.n)]
        src += [("human", al, None) for al in ("red", "blue")]
        src.append(("floor", None, None))     # a bounce / kicked ball leaving the carpet
        return src

    def _mu(self, s, tL):
        kind, al, k = s
        if kind == "robot":
            xy = self.rob.pos(k, tL)
            return np.c_[xy, np.full(len(tL), ROBOT_H0)], np.array([ROBOT_SH, ROBOT_SH, ROBOT_SZ])
        if kind == "floor":
            return (np.repeat(np.r_[self._floor_xy, 0.075][None], len(tL), 0),
                    np.array([FLOOR_SXY, FLOOR_SXY, 0.08]))
        return np.repeat(self.F.hp[al][None], len(tL), 0), np.array([0.35, HP_SXY, HP_SZ])

    def _gap_cost(self, s, th, tL, t_first):
        """Seconds of predicted flight before the first observation during which the ball
        would have been visible (in frame, not in overlay, not over the shooter)."""
        Gn = len(tL)
        m = 8
        fr = np.linspace(0, 1, m + 1)[:-1]
        ts = tL[:, None] + (t_first - tL)[:, None] * fr[None]    # (G, m)
        tau = ts - tL[:, None]
        p1, p2 = _phi(tau)
        X = (th[:, None, 0:3] + th[:, None, 3:6] * p1[..., None]
             + (th[:, None, 6:9] + np.array([0, 0, -G])) * p2[..., None])
        uv = self.cam.project(X.reshape(-1, 3)).reshape(Gn, m, 2)
        vis = ((uv[..., 0] > 5) & (uv[..., 0] < self.cam.W - 5) & (uv[..., 1] > 5)
               & (uv[..., 1] < self.cam.H - 5) & ~_in_overlay(uv) & (X[..., 2] > 0.05))
        kind, al, k = s
        if kind == "robot":
            rxy = self.rob.pos(k, ts.ravel()).reshape(Gn, m, 2)
            d = np.linalg.norm(X[..., :2] - rxy, axis=-1)
            vis &= ~((d < 0.75) & (X[..., 2] < 1.4))
            c3 = np.concatenate([rxy, np.full((Gn, m, 1), 0.4)], -1)
            ruv = self.cam.project(c3.reshape(-1, 3)).reshape(Gn, m, 2)
            vis &= np.linalg.norm(uv - ruv, axis=-1) > 45
        elif kind == "human":
            vis &= ~((np.abs(X[..., 0] - self.F.hp[al][0]) < 0.9))
        dt = (t_first - tL) / m
        vis_t = vis.sum(1) * dt
        return np.maximum(vis_t - GAP_FREE, 0) ** 2 / GAP_SIG ** 2, vis_t

    def fit_hyps(self, T, uv, end=None):
        """end: None or (al, E (3,), sig (3,)) anchor at the last observation.
        -> list of hypothesis dicts sorted by cost."""
        xn = self.fit.norm(uv)
        t_first = T[0]
        g = self.cam.img_to_plane(uv[:1], 0.075)[0]
        self._floor_xy = g if np.isfinite(g).all() else np.array([self.F.L / 2, self.F.W / 2])
        taus = np.arange(0.0, TAU_MAX + 1e-9, TAU_STEP)
        tL = t_first - taus
        tau = T[None, :] - tL[:, None]
        hyps = []
        kw = {}
        if end is not None:
            kw = dict(end_tau=T[-1] - tL, end_mu=end[1], end_sig=end[2])
        srcs = self._sources()
        Gn = len(tL)
        mus, sigs = zip(*[self._mu(s, tL) for s in srcs])
        MU = np.concatenate(mus)
        SG = np.concatenate([np.repeat(sg[None], Gn, 0) for sg in sigs])
        D0 = (MU @ self.cam.R[2] + self.cam.tvec[2])[:, None]
        if end is not None:
            kw["end_tau"] = np.tile(kw["end_tau"], len(srcs))
        TH, CHI2, PC, _ = self.fit.solve(np.tile(tau, (len(srcs), 1)), xn, MU, SG, depth0=D0, **kw)
        for si, s in enumerate(srcs):
            sl = slice(si * Gn, (si + 1) * Gn)
            mu, th, chi2, pc = MU[sl], TH[sl], CHI2[sl], PC[sl]
            gc, vis_t = self._gap_cost(s, th, tL, t_first)
            cost = chi2 + pc + gc
            cost = np.where(th[:, 2] < 0.05, cost + 50, cost)
            if s[0] == "floor":                # a bounce off the carpet cannot fly high
                cost = cost + (np.maximum(th[:, 5] - FLOOR_VZ, 0) / 0.5) ** 2
            if s[0] == "robot":
                # AUTO: robots stay on their side of the centre line (G403) -> a robot track
                # on the wrong side is an identity swap upstream; don't trust it
                rt = tL - self.t_start
                wrong = (mu[:, 0] < self.F.L / 2 - 0.5) if s[1] == "red" else (mu[:, 0] > self.F.L / 2 + 0.5)
                cost = cost + np.where((rt < 20.0) & wrong, AUTO_SIDE_PEN, 0.0)
                if end is not None and s[1] != end[0]:
                    cost = cost + CROSS_PEN    # scoring into the opponent's hub is rare
            j = int(np.argmin(cost))
            hyps.append(dict(src=s, th=th[j], tL=float(tL[j]), chi2=float(chi2[j]), prior=float(pc[j]),
                             gap=float(gc[j]), vis_gap=float(vis_t[j]), cost=float(cost[j])))
        for h in hyps:                         # bounces are only chosen when clearly better
            if h["src"][0] == "floor":
                h["cost_raw"] = h["cost"]; h["cost"] += FLOOR_MARGIN
        hyps.sort(key=lambda h: h["cost"])
        return hyps

    def free_fit(self, T, uv):
        xn = self.fit.norm(uv)
        tau = (T - T[0])[None]
        th, chi2, pc, X = self.fit.solve(tau, xn)
        return dict(th=th[0], tL=float(T[0]), chi2=float(chi2[0]), cost=float(chi2[0] + pc[0]))

    def end_anchors(self, al, uv_end):
        """Anchors of a hub entry: the last sighting at rim height (end_anchor) and, since a
        ball is often still visible falling INSIDE the clear funnel below the rim, points
        lower down the same viewing ray that are still inside the hub."""
        out = [self.end_anchor(al, uv_end)]
        c = self.F.hub[al]
        for z in END_Z_DEEP:
            P = self.cam.img_to_plane(uv_end[None], z)[0]
            if np.isfinite(P).all() and np.linalg.norm(P - c) < HUB_OPEN_R:
                out.append((al, np.r_[P, z], np.array([END_SXY, END_SXY, 0.12])))
        return out

    def end_anchor(self, al, uv_end):
        """3D anchor of a hub entry: the point on the last observation's viewing ray, at
        rim height, that lies in the opening (closest to its centre if none does)."""
        c = self.F.hub[al]
        zs = np.linspace(1.3, 2.6, 27)
        P = np.array([self.cam.img_to_plane(uv_end[None], z)[0] for z in zs])
        ok = np.isfinite(P).all(1)
        if not ok.any():
            return (al, np.r_[c, END_Z], np.array([END_SXY, END_SXY, END_SZ]))
        zs, P = zs[ok], P[ok]
        d = np.linalg.norm(P - c, axis=1)
        inside = d < HUB_OPEN_R
        if inside.any():
            j = np.where(inside)[0][np.argmin(np.abs(zs[inside] - END_Z))]
        else:
            j = int(np.argmin(d))
        return (al, np.r_[P[j], zs[j]], np.array([END_SXY, END_SXY, END_SZ]))

    # ---------------------------------------------------------------- arcs
    def arc(self, h, t_max=4.0):
        tau = np.arange(0, t_max, 1 / 120.)
        X = traj(h["th"], tau)
        V = traj_vel(h["th"], tau)
        desc = V[:, 2] < 0
        land_i = np.where(desc & (X[:, 2] <= 0.075))[0]
        li = int(land_i[0]) if len(land_i) else len(tau) - 1
        return tau[:li + 1] + h["tL"], X[:li + 1], V[:li + 1]

    def near_hub(self, ts, X, V):
        """Closest approach of the descending arc to each hub opening."""
        best = None
        for al, c in self.F.hub.items():
            zc = self.F.hub_z
            m = (V[:, 2] < 0) & (X[:, 2] > 0.8)
            if not m.any():
                m = X[:, 2] > 0.8
            if not m.any():
                continue
            d = np.linalg.norm(X[m, :2] - c, axis=1)
            j = int(np.argmin(d))
            cr = None
            idx = np.where((V[1:, 2] < 0) & (X[:-1, 2] >= zc) & (X[1:, 2] < zc))[0]
            if len(idx):
                i = idx[0] + 1
                cr = (float(np.linalg.norm(X[i, :2] - c)), float(ts[i]))
            cand = dict(hub=al, dmin=float(d[j]), t_near=float(ts[m][j]), cross=cr)
            if best is None or cand["dmin"] < best["dmin"]:
                best = cand
        return best

    def _pred_px(self, h, t):
        X = traj(h["th"], np.asarray(t) - h["tL"])
        return self.cam.project(X)

    # ---------------------------------------------------------------- per flight
    def analyse(self, c):
        T, uv = c["T"], c["uv"]
        al_end = self.end_hub(T, uv)
        free = self.free_fit(T, uv)
        hyps = self.fit_hyps(T, uv)
        c.update(free=free, hyps=hyps, end_al=al_end, hyps_end=None)
        if al_end is not None:
            best = None
            for anc in self.end_anchors(al_end, uv[-1]):
                hy = self.fit_hyps(T, uv, anc)
                if best is None or hy[0]["cost"] < best[0]["cost"]:
                    best = hy
            c["hyps_end"] = best
            # alternative: the ball came down on the carpet BEHIND the hub (same pixel)
            g = self.cam.img_to_plane(uv[-1:], 0.1)[0]
            if np.isfinite(g).all():
                c["hyps_land"] = self.fit_hyps(T, uv, (None, np.r_[g, 0.1], np.array([0.3, 0.3, 0.12])))
        return c

    def _rms(self, h, n):
        return math.sqrt(h["chi2"] * SIG_PX ** 2 / max(1, n))

    def split_bad(self, cands):
        """A long track that ends in a hub opening but fits no single flight (the 2D tracker
        switched balls mid-track, or a throw from behind the alliance wall bounced off the net)
        loses that hub entry.  Keep the longest TAIL that is one clean ballistic arc as the
        flight (the earlier part becomes its own candidate)."""
        out, n = [], 0
        for c in cands:
            T, uv = c["T"], c["uv"]
            if c["end_al"] is None or len(T) < 2 * MIN_N:
                out.append(c); continue
            best = min([c["hyps"][0]] + ([c["hyps_end"][0]] if c["hyps_end"] else []), key=lambda h: h["cost"])
            if self._rms(best, len(T)) <= SPLIT_RMS:
                out.append(c); continue
            cut = None
            for w in SPLIT_TAILS:
                i0 = int(np.searchsorted(T, T[-1] - w))
                if len(T) - i0 < MIN_N or i0 == 0:
                    continue
                f = self.free_fit(T[i0:], uv[i0:])
                if f["chi2"] < 1e8 and self._rms(f, len(T) - i0) < SPLIT_TAIL_RMS:
                    cut = i0
                    break
            if cut is None:
                out.append(c); continue
            n += 1
            for a, b in ((0, cut), (cut, len(T))):
                if b - a >= MIN_N:
                    out.append(self.analyse(dict(ids=list(c["ids"]), T=T[a:b], uv=uv[a:b], r=c["r"], air=c["air"],
                                                 split=True)))
        out.sort(key=lambda c: c["T"][0])
        return out, n

    def join_fragments(self, cands):
        """Join A -> B when B starts after A ended and A's fitted arc (best hypothesis)
        predicts B's first observations (one ball whose track was broken mid-flight)."""
        starts = np.array([c["T"][0] for c in cands])
        succ, taken = {}, set()
        order = sorted(range(len(cands)), key=lambda i: cands[i]["T"][-1])
        for i in order:
            A = cands[i]
            if A["end_al"] is not None:
                continue
            tA = A["T"][-1]
            lo, hi = np.searchsorted(starts, tA - 0.02), np.searchsorted(starts, tA + JOIN_GAP)
            h = A["hyps"][0]
            best, bd = None, None
            for j in range(lo, hi):
                if j == i or j in taken:
                    continue
                B = cands[j]
                if B["T"][0] <= A["T"][-1] - 0.02:
                    continue
                if set(A["ids"]) & set(B["ids"]):
                    continue                   # pieces of a track split_bad() cut apart
                k = min(4, len(B["T"]))
                p = self._pred_px(h, B["T"][:k])
                gap = B["T"][0] - tA
                d = float(np.median(np.linalg.norm(p - B["uv"][:k], axis=1)))
                # ... or B's own (often longer, hub-anchored) fit extrapolated back onto A
                hb = (B["hyps_end"] or B["hyps"])[0]
                ka = min(4, len(A["T"]))
                pb = self._pred_px(hb, A["T"][-ka:])
                d = min(d, float(np.median(np.linalg.norm(pb - A["uv"][-ka:], axis=1))))
                tol = 12 + 60 * gap
                if d < tol and (bd is None or d < bd):
                    best, bd = j, d
            if best is not None:
                succ[i] = best; taken.add(best)
        if not succ:
            return cands, 0
        out, used = [], set()
        njoin = 0
        for i in range(len(cands)):
            if i in taken or i in used:
                continue
            chain = [i]
            while chain[-1] in succ:
                chain.append(succ[chain[-1]])
            used.update(chain)
            if len(chain) == 1:
                out.append(cands[i]); continue
            T = np.concatenate([cands[q]["T"] for q in chain])
            uv = np.concatenate([cands[q]["uv"] for q in chain])
            o = np.argsort(T, kind="stable")
            c = dict(ids=sum((cands[q]["ids"] for q in chain), []), T=T[o], uv=uv[o],
                     r=cands[chain[0]]["r"], air=max(cands[q]["air"] for q in chain))
            c = self.analyse(c)
            # keep the join only if ONE flight still explains all pieces
            def rms(cc):
                h = (cc["hyps_end"] or cc["hyps"])[0]
                return math.sqrt(h["chi2"] * SIG_PX ** 2 / len(cc["T"]))
            worst = max(rms(cands[q]) for q in chain)
            if rms(c) > 1.3 * worst + 1.0:
                out.extend(cands[q] for q in chain)
                continue
            njoin += len(chain) - 1
            out.append(c)
        return out, njoin

    def join_continuations(self, cands, all_tracks):
        """A ball passing behind a hub-net pole / frame just above the opening loses its track;
        the piece that re-appears inside the funnel is often too short (< MIN_N samples) or too
        weakly airborne to be a candidate itself.  Append such a non-candidate piece to the flight
        whose fitted arc predicts it, when the piece ends in a hub opening."""
        used = {i for c in cands for i in c["ids"]}
        st = sorted((tr for tr in all_tracks if tr["id"] not in used and len(tr["t"]) >= CONT_MIN_N
                     and tr.get("view_ok", 1) >= MIN_VIEW), key=lambda tr: tr["t"][0])
        if not st:
            return cands, 0
        t0s = np.array([tr["t"][0] for tr in st])
        taken, out, n = set(), [], 0
        for c in cands:
            if c["end_al"] is not None:
                out.append(c); continue
            T, uv = c["T"], c["uv"]
            # only flights whose end is close to an opening (image)
            e = (float(uv[-1, 0]), float(uv[-1, 1]))
            if max(self.cv2.pointPolygonTest(p, e, True) for p in self.hub_funnel.values()) < -CONT_NEAR_PX:
                out.append(c); continue
            h = c["hyps"][0]
            lo, hi = np.searchsorted(t0s, T[-1] - 0.02), np.searchsorted(t0s, T[-1] + CONT_GAP)
            best, bd = None, None
            for j in range(lo, hi):
                if j in taken:
                    continue
                B = st[j]
                TB, UB = np.asarray(B["t"], float), np.asarray(B["uv"], float)
                if TB[0] <= T[-1] - 0.02 or self.end_hub(TB, UB) is None:
                    continue
                k = min(3, len(TB))
                d = float(np.median(np.linalg.norm(self._pred_px(h, TB[:k]) - UB[:k], axis=1)))
                if d < 12 + 60 * (TB[0] - T[-1]) and (bd is None or d < bd):
                    best, bd = j, d
            if best is None:
                out.append(c); continue
            B = st[best]
            TT = np.r_[T, np.asarray(B["t"], float)]; UU = np.r_[uv, np.asarray(B["uv"], float)]
            o = np.argsort(TT, kind="stable")
            c2 = self.analyse(dict(ids=c["ids"] + [B["id"]], T=TT[o], uv=UU[o], r=c["r"], air=c["air"]))
            if c2["end_al"] is None:
                out.append(c); continue
            taken.add(best); n += 1
            c2["cont"] = B["id"]
            out.append(c2)
        return out, n

    def decide(self, c):
        """-> shot dict fields or None (not a shot)."""
        T, uv = c["T"], c["uv"]
        hyps = c["hyps"]
        made_ok = False
        if c["hyps_end"] is not None:
            he = c["hyps_end"][0]
            # the end anchor must not cost much more than the unconstrained best
            dc = he["cost"] - hyps[0]["cost"]
            c["made_dc"] = dc
            # an INACTIVE hub is rarely shot at (no points) but often lobbed over when
            # passing -> demand a cleaner hub-entry fit there
            thr = MADE_DC if self.hub_active_at(c["end_al"], T[-1]) is not False else MADE_DC_INACTIVE
            if he["src"][0] == "human":
                # long, flat human-player lobs often die on the far rim / net: demand a clean
                # fit and an end inside the opening itself (not the funnel below it)
                thr = min(thr, MADE_DC_HP)
                # ... when the lob flies AWAY from the camera: the far rim / net then projects
                # into the funnel image.  A lob coming TOWARDS the camera (the far-side outpost)
                # is last seen dropping into the funnel below the near rim -> funnel end is fine.
                e = (float(uv[-1, 0]), float(uv[-1, 1]))
                away = float(he["th"][3:5] @ (self.F.hub[c["end_al"]] - self.cam.C[:2])) > 0
                inactive = thr == MADE_DC_INACTIVE
                if (away or inactive) and self.cv2.pointPolygonTest(self.hub_poly[c["end_al"]], e, False) < 0:
                    thr = -1e9
            # balls rolling INSIDE the hub (already scored) start in the funnel too
            s0 = (float(uv[0, 0]), float(uv[0, 1]))
            # (they stay low; a ball that first appears near the funnel image but then flies well
            # above the rim is a real shot launched behind / beyond the hub -- common at the
            # far-side hub, whose funnel image reaches up to where those flights are first seen)
            if (he["src"][0] == "floor"
                    and self.cv2.pointPolygonTest(self.hub_funnel[c["end_al"]], s0, True) > -START_OUT_PX):
                tsf, Xf, _ = self.arc(he)
                zf = Xf[:min(len(tsf) - 1, int(np.searchsorted(tsf, T[-1]))) + 1, 2].max()
                if zf < self.F.hub_z + FUNNEL_POP_DZ:
                    thr = -1e9
            # ... and must explain the track better than a ball coming down on the carpet
            # behind the hub along the same line of sight
            if c.get("hyps_land"):
                c["land_dc"] = he["cost"] - c["hyps_land"][0]["cost"]
                if c["land_dc"] > LAND_MARGIN:
                    thr = -1e9
            made_ok = dc < thr
            if made_ok:
                tsa, Xa, Va = self.arc(he)
                ie = min(len(tsa) - 1, int(np.searchsorted(tsa, T[-1])))
                # must have been above the rim and be falling into the opening
                made_ok = Xa[:ie + 1, 2].max() > self.F.hub_z + 0.1 and Va[ie, 2] < -0.3
        if made_ok:
            hl = c["hyps_end"]
        else:
            hl = hyps
        h = hl[0]
        alt = hl[1] if len(hl) > 1 else None
        ts, X, V = self.arc(h)
        nh = self.near_hub(ts, X, V)
        apex = float(X[:, 2].max())
        v0 = h["th"][3:6]
        speed = float(np.linalg.norm(v0))
        outc, target, oconf, t_entry = None, None, 0.5, None
        if h["src"][0] == "floor" and not made_ok:
            c["why"] = "floor"
            return None
        if made_ok and not c.get("bounced"):
            outc, target, t_entry = "made", f"{c['end_al']}_hub", float(T[-1])
            oconf = 0.9 if c["made_dc"] < MADE_DC / 3 else 0.7
        else:
            # not a hub entry: real shot / pass or just a hop / bounce?
            if apex < MIN_APEX or speed < MIN_SPEED:
                c["why"] = "apex_speed"
                return None
            # the OBSERVED flight must carry the ball away from where it started, and not
            # just drop it into a robot (balls churning in a hopper, intakes, hops)
            Xo = traj(h["th"], T - h["tL"])
            d_obs = float(np.linalg.norm(Xo[-1, :2] - h["th"][0:2]))
            if d_obs < MIN_TRAVEL:
                c["why"] = "travel"
                return None
            if h["src"][0] not in ("robot", "human"):
                c["why"] = "unattributed"
                return None                    # unattributed non-scoring flight: not useful, mostly junk
            if self.in_hopper(T, uv):
                c["why"] = "hopper"
                return None
            te = T[-1]
            kind, sal, sk = h["src"]
            rms = math.sqrt(h["chi2"] * SIG_PX ** 2 / max(1, len(T)))
            receiver = None
            for k in range(self.rob.n):
                if (np.linalg.norm(Xo[-1, :2] - self.rob.pos(k, te)) < END_AT_ROBOT
                        and Xo[-1, 2] < 1.3):
                    # a ball dropping into a TEAMMATE's hopper after a real lob is a feed;
                    # anything else ending low at a robot is an intake / hopper churn
                    if (kind == "robot" and k != sk and self.rob.alliance[k] == sal
                            and d_obs >= PASS_MIN_RANGE and apex >= PASS_MIN_APEX):
                        receiver = k
                        break
                    c["why"] = "end_at_robot"
                    return None
            land = X[-1, :2]
            m_ = LAND_OUT_M
            implausible = (speed > MAX_SPEED or land[0] < -m_ or land[0] > self.F.L + m_
                           or land[1] < -m_ or land[1] > self.F.W + m_ or rms > JUNK_RMS)
            if implausible or T[-1] - T[0] < MIN_DUR_OUTCOME:
                # short / ill-conditioned non-scoring fragments are (on inspection) almost
                # all hops, hopper churn, balls handled off-field -> not shots
                c["why"] = "implausible_short"
                return None
            if apex < HOP_APEX and speed < HOP_SPEED:
                c["why"] = "hop"                # low, slow pop-ups next to robots
                return None
            near = self.img_near_hub(uv) is not None or (nh is not None and nh["dmin"] < HUB_R_NEAR)
            hal = (self.img_near_hub(uv) or nh["hub"]) if near else None
            o = h["th"][0:3]
            if near and kind == "robot" and not c.get("bounced") and rms <= MAX_RMS:
                # the hubs only take fuel launched from their alliance zone (the rear net blocks
                # the neutral side): a launch from outside it that comes near the hub is a lob
                # over / into the net towards the alliance zone = a pass, not a missed shot
                in_zone = (o[0] < self.F.blue_x + ZONE_MARGIN) if hal == "blue" else (o[0] > self.F.red_x - ZONE_MARGIN)
                if not in_zone:
                    near = False
            if near and kind == "robot" and sal in ("red", "blue") and hal != sal:
                # robots only score in their own hub: a launch that comes near the OTHER alliance's hub is a
                # clear / lob out of that end of the field, not a missed shot (strategist review 2026-10)
                near = False
                c["clear"] = True
            if near and kind == "human" and not c.get("bounced"):
                near = False                    # human-player throws are feeds into the field
            if near:
                outc, target = "missed", f"{hal}_hub"
                oconf = 0.75 if c.get("bounced") or T[-1] >= nh["t_near"] - 0.1 else 0.5
                if c.get("bounced"):
                    oconf = 0.8
            else:
                if receiver is None and (np.linalg.norm(land - h["th"][0:2]) < PASS_MIN_RANGE
                                         or apex < PASS_MIN_APEX):
                    c["why"] = "pass_short"
                    return None
                outc, target = "pass", None
                oconf = 0.6
                if receiver is None and kind == "robot":
                    # landing next to a teammate (its intake) -> feed
                    tl = ts[-1]
                    for k in range(self.rob.n):
                        if (k != sk and self.rob.alliance[k] == sal
                                and np.linalg.norm(land - self.rob.pos(k, tl)) < FEED_R):
                            receiver = k
                            break
                c["receiver"] = receiver
        return dict(h=h, alt=alt, arc=(ts, X), nh=nh, outcome=outc, target=target, oconf=oconf,
                    t_entry=t_entry, apex=apex, speed=speed)

    def mark_bounces(self, cands, all_tracks):
        """A track ending in a hub opening, followed right away by a ball leaving that
        spot (a rim bounce-out), is a miss; the follower is not a new shot."""
        st = sorted(all_tracks, key=lambda tr: tr["t"][0])
        t0s = np.array([tr["t"][0] for tr in st])
        for c in cands:
            if c["end_al"] is None:
                continue
            te, e = c["T"][-1], c["uv"][-1]
            lo, hi = np.searchsorted(t0s, te - 0.03), np.searchsorted(t0s, te + BOUNCE_DT)
            for j in range(lo, hi):
                tr = st[j]
                if tr["id"] in c["ids"] or len(tr["t"]) < 4:
                    continue
                u0 = np.asarray(tr["uv"][0], float)
                if np.linalg.norm(u0 - e) > BOUNCE_PX:
                    continue
                uvb = np.asarray(tr["uv"], float)
                # follower must leave the opening (not another ball ending in it)
                if self.end_hub(np.asarray(tr["t"]), uvb) is not None:
                    continue
                if np.linalg.norm(uvb[-1] - e) < BOUNCE_PX:
                    continue
                # ... and move clearly differently from the incoming ball (a stream of
                # balls raining into the hub all move alike)
                k = min(4, len(c["T"]) - 1)
                va = (c["uv"][-1] - c["uv"][-1 - k]) / max(c["T"][-1] - c["T"][-1 - k], 1e-3)
                kb = min(3, len(uvb) - 1)
                tb = np.asarray(tr["t"], float)
                vb = (uvb[kb] - uvb[0]) / max(tb[kb] - tb[0], 1e-3)
                cosang = float(va @ vb / max(np.linalg.norm(va) * np.linalg.norm(vb), 1e-6))
                if cosang > 0.0 or vb[1] > -60 or tb[0] < te + 0.01:
                    continue                   # a bounce-out pops UP out of the opening
                c["bounced"] = tr["id"]
                break

    # ---------------------------------------------------------------- run
    def run(self, fuel):
        t0 = time.time()
        cands = self.candidates(fuel)
        n_raw = len(cands)
        cands = [self.analyse(c) for c in cands]
        cands, nsplit = self.split_bad(cands)
        cands, njoin = self.join_fragments(cands)
        cands, ncont = self.join_continuations(cands, fuel["tracks"])
        self.mark_bounces(cands, fuel["tracks"])
        bounce_ids = {c["bounced"] for c in cands if c.get("bounced")}
        shots, n_hop = [], 0
        for c in cands:
            if set(c["ids"]) & bounce_ids:
                continue
            d = self.decide(c)
            if d is None:
                n_hop += 1
                continue
            shots.append(self.shot_record(c, d))
        shots.sort(key=lambda s: s["t"])
        self.volleys(shots)
        ev = dict(shots=shots, intakes=[], first_inactive=self.first_inactive,
                  hub_active={al: v for al, v in self.sched.items()})
        ev["reconcile"] = reconcile(shots, self.tba, self.sched, self.coverage(fuel))
        ev["stats"] = dict(candidates=n_raw, duplicates=getattr(self, "n_dups", 0), joined=njoin, continued=ncont, split=nsplit, hops=n_hop, bounce_outs=len(bounce_ids),
                           shots=len(shots), made=sum(s["outcome"] == "made" for s in shots),
                           wall_s=round(time.time() - t0, 1))
        self.log(f"  shots: {n_raw} candidate flights ({njoin} joined, {n_hop} hops) -> {len(shots)} shots "
                 f"({ev['stats']['made']} made) in {ev['stats']['wall_s']}s")
        return ev

    def shot_record(self, c, d):
        T = c["T"]
        h, alt = d["h"], d["alt"]
        kind, al, k = h["src"]
        rms = math.sqrt(h["chi2"] * SIG_PX ** 2 / max(1, len(T)))
        margin = (alt["cost"] - h["cost"]) if alt else 99.0
        conf = float(1 - math.exp(-max(margin, 0) / 4.0))
        excess = h["cost"] - c["free"]["cost"]
        if rms > MAX_RMS:
            kind = "unknown"; conf *= 0.3
        if kind == "floor":                    # a real shot whose shooter fits no source
            kind = "unknown"; conf = 0.0
        rt = h["tL"] - self.t_start
        mc, per = self.clock(h["tL"])
        hub_al = d["target"].split("_")[0] if d["target"] else None
        hub_active, counted = None, None
        if hub_al:
            te = d["t_entry"] if d["t_entry"] is not None else (d["nh"]["t_near"] if d["nh"] else h["tL"])
            mce, pe = self.clock(te)
            act = self.sched[hub_al].get(pe) if pe else None
            counted = pe
            if act is False and pe and pe.startswith("shift"):
                names = [p[0] for p in self.F.periods]
                pi = names.index(pe)
                if mce - self.F.periods[pi][1] <= GRACE_S and self.sched[hub_al].get(names[pi - 1]):
                    act, counted = True, names[pi - 1]
            hub_active = act
        ts, X = d["arc"]
        nh = d["nh"]
        rx = None
        if kind == "robot":
            rxy = self.rob.pos(k, h["tL"])
            rx = dict(robot_xy=np.round(rxy, 2).tolist(), robot_speed=round(float(np.linalg.norm(self.rob.vel(k, h["tL"]))), 2))
        return dict(
            t=round(h["tL"], 3), match_t=round(rt, 3), match_clock=round(mc, 2), period=per,
            shooter=dict(kind=kind, alliance=al if kind != "unknown" else None,
                         track_idx=k if kind == "robot" else None),
            origin=np.round(h["th"][0:3], 3).tolist(), v0=np.round(h["th"][3:6], 2).tolist(),
            acc=np.round(h["th"][6:9], 2).tolist(), drag=C_DRAG,
            landing=np.round(X[-1, :2], 2).tolist(), apex_z=round(d["apex"], 2), speed=round(d["speed"], 2),
            target=d["target"], outcome=d["outcome"], hub_active=hub_active, counted_period=counted,
            t_entry=None if d["t_entry"] is None else round(d["t_entry"], 3),
            hub_dmin=None if nh is None else round(nh["dmin"], 2),
            fit_rms_px=round(rms, 2), n_obs=int(len(T)), t_obs=[round(float(T[0]), 3), round(float(T[-1]), 3)],
            conf=round(conf, 2), outcome_conf=round(d["oconf"], 2),
            alt=dict(kind=alt["src"][0], alliance=alt["src"][1], track_idx=alt["src"][2],
                     dcost=round(margin, 1)) if alt else None,
            vis_gap=round(h["vis_gap"], 2), fuel_ids=c["ids"], excess=round(excess, 1),
            made_dc=None if c.get("made_dc") is None else round(c["made_dc"], 1),
            land_dc=None if c.get("land_dc") is None else round(c["land_dc"], 1),
            bounced=c.get("bounced"),
            pass_kind=(("feed" if c.get("receiver") is not None else "clear" if c.get("clear") else "floor")
                       if d["outcome"] == "pass" else None),
            receiver_idx=c.get("receiver") if d["outcome"] == "pass" else None,
            cross_alliance=bool(kind == "robot" and hub_al is not None and al != hub_al), **(rx or {}))

    def volleys(self, shots, gap=VOLLEY_GAP):
        last = {}
        vid = 0
        for s in shots:
            sh = s["shooter"]
            if sh["kind"] == "unknown":
                s["volley"] = None; continue
            key = (sh["kind"], sh["alliance"], sh["track_idx"])
            if key in last and s["t"] - last[key][0] <= gap:
                s["volley"] = last[key][1]
            else:
                vid += 1; s["volley"] = vid
            last[key] = (s["t"], s["volley"])


PERIOD_KEYS = [("auto", "autoCount"), ("transition", "transitionCount"), ("shift1", "shift1Count"),
               ("shift2", "shift2Count"), ("shift3", "shift3Count"), ("shift4", "shift4Count"),
               ("endgame", "endgameCount")]


def reconcile(shots, tba_match, sched, coverage=None, fuel_window=None):
    """Traced made per alliance hub per period vs TBA hubScore.  coverage: {period: fraction
    of that period with live camera feed} -- compare only where it is ~1."""
    out = {}
    for al in ("red", "blue"):
        hs = (tba_match or {}).get("score_breakdown", {}).get(al, {}).get("hubScore") if tba_match else None
        d = {}
        made = [s for s in shots if s["outcome"] == "made" and s["target"] == f"{al}_hub"]
        for p, key in PERIOD_KEYS:
            n = sum(1 for s in made if s["counted_period"] == p and s["hub_active"] is not False)
            d[p] = dict(traced_made=n, tba=None if hs is None else hs.get(key))
            if coverage is not None:
                d[p]["coverage"] = coverage.get(p)
        n_un = sum(1 for s in made if s["hub_active"] is False)
        d["uncounted"] = dict(traced_made=n_un, tba=None if hs is None else hs.get("uncounted"))
        tot = sum(v["traced_made"] for k, v in d.items() if k != "uncounted")
        d["total"] = dict(traced_made=tot, tba=None if hs is None else hs.get("totalCount"))
        per_robot = {}
        for s in made:
            sh = s["shooter"]
            key = (f"{sh['kind']}:{sh['track_idx']}" if sh["kind"] == "robot" else
                   f"{sh['kind']}:{sh['alliance']}")
            per_robot[key] = per_robot.get(key, 0) + 1
        d["by_shooter"] = per_robot
        att = [s for s in shots if s["target"] == f"{al}_hub" and s["outcome"] in ("made", "missed")]
        d["traced_attempts"] = len(att)
        d["cross_alliance_made"] = sum(1 for s in made if s["shooter"]["kind"] == "robot"
                                       and s["shooter"]["alliance"] != al)
        out[al] = d
    if coverage is not None:
        out["coverage"] = coverage
    return out


def reconstruct(fuel, match, cam, rules, tba_match=None, log=print):
    fuel, match = _load(fuel), _load(match)
    return Reconstructor(match, cam, rules, tba_match, log).run(fuel)


def find_tba_match(tba, key):
    d = _load(tba)
    if isinstance(d, dict) and "data" in d:
        d = d["data"]
    for m in d:
        if m.get("key") == key:
            return m
    return None


if __name__ == "__main__":
    fj, mj, cj, rj, out = sys.argv[1:6]
    m = json.load(open(mj))
    raw = json.load(open(cj))
    if m.get("camera_pose"):
        raw = dict(raw, rvec=m["camera_pose"]["rvec"], tvec=m["camera_pose"]["tvec"])
    tba = find_tba_match(sys.argv[6], m.get("key")) if len(sys.argv) > 6 else None
    ev = reconstruct(fj, m, CAMM.Camera(raw), rj, tba)
    json.dump(ev, open(out, "w"))
    print("wrote", out)
