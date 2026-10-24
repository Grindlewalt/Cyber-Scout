"""Dynamic view: a camera pose for EVERY frame of a broadcast program feed, from the field's AprilTags.

A produced feed cuts between a fixed wide camera and close-up cameras that pan and zoom.  The tracker
(live/process.py) can locate robots in field coordinates from any of them if it knows each frame's camera.
This module estimates it:

  pass 1 (`_Scan`, one decode at the tracker's 30 Hz sample rate)
    * cut detector: normalised thumbnails of the picture (broadcast graphics masked out); the
      correlation with the previous sample drops at a cut (same camera 0.6-1.0, another camera ~0.1)
    * fixed view: at the first tag frame of a shot, the AprilTags are searched (x4 upscaled windows)
      where the reference camera (the event calibration, `ref`) predicts them; >= 3 tags within 3 px ->
      the shot IS that camera (kind FIXED: the calibration itself, nothing estimated).  Its thumbnail
      then confirms every frame of the shot (VIEW_NCC, as process.on_air)
    * other views: frame-to-frame homography from sparse optical flow (LK + RANSAC on a quarter-size
      grey frame) and, at TAG_HZ, AprilTags -- in windows around the tags the current pose predicts
      (upscaled for small ones), the whole frame at 1x when there is no pose yet (or every second),
      tiled x2 as a last resort.  A quick single-frame pose keeps the prediction going.
  pass 2 (`_Solve`)
    * per shot: one camera centre (broadcast cameras pan / tilt / zoom on a fixed mount), chosen among
      the shot's own free solves, the clip's other cameras and the reference camera by the total
      reprojection error, then refined jointly with every tag frame's rotation + focal length
    * tag frames with RMS <= MAX_RMS px become anchors (kind TAGS); frames in between get the pose
      carried along the flow homographies (a rotating / zooming camera maps the image by
      H = K2 R2 R1^T K1^-1) from the anchors before and after, blended (kind FLOW), or rotation /
      focal interpolation when the flow broke (kind INTERP); at most MAX_AGE s from an anchor
    * everything else: no pose (kind NONE) -> a feed gap for the tracker

Lens: the reference camera keeps its calibrated distortion; other cameras are pinhole with the
principal point at the image centre and radial k1 = K1_PRIOR (zoom lenses at broadcast zoom are nearly
distortion free; DYN_K1 to change).

    python live/dynpose.py VIDEO --ref camera.json [--view-ref view.png] [--layout layout.json]
                           [--t0 S --t1 S] [--out pose.npz] [--report report.json] [--proof DIR]

`estimate(...)` -> PoseTrack (per 30 Hz sample: camera or None, kind, RMS, tags, shot);
`auto_setup(video)` -> what the pipeline needs for a stream nobody calibrated (see there).
"""
import os, sys, time, json, math
import numpy as np, cv2

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path: sys.path.insert(0, HERE)
import calibrate as CAL
import camera as CAMM

FPS = 30.0                                                    # = process.FPS (same sampling rule)
TAG_HZ = float(os.environ.get("DYN_TAG_HZ", "5"))             # tag detection rate in moving views
MAX_RMS = float(os.environ.get("DYN_MAX_RMS", "2.5"))         # px: anchor tag-corner RMS above = rejected
MAX_AGE = float(os.environ.get("DYN_MAX_AGE", "1.0"))         # s: pose carried at most this far from an anchor
CUT_NCC = float(os.environ.get("DYN_CUT_NCC", "0.5"))         # thumbnail correlation below = cut
VIEW_NCC = float(os.environ.get("VIEW_NCC", "0.45"))          # = process.VIEW_NCC (fixed view still on air)
FIXED_PX = float(os.environ.get("DYN_FIXED_PX", "3.0"))       # ref-predicted tags within this = the fixed view
FIXED_MIN_Z = float(os.environ.get("DYN_FIXED_MIN_Z", "3.0"))  # m: auto_setup's fixed camera looks down from this high
K1_PRIOR = float(os.environ.get("DYN_K1", "0.0"))
NO_FEED_LEVEL = 20
SMALL_W = 480                                                 # flow / thumbnail frame width
MC_LAG = int(os.environ.get("DYN_MC_LAG", "10"))              # samples: motion-compensated view-change test
MC_NCC = float(os.environ.get("DYN_MC_NCC", "0.7"))           # below = transition (dissolve / wipe / whip pan)
MC_LAG2 = int(os.environ.get("DYN_MC_LAG2", "30"))            # samples: the same over 1 s (slow dissolves)
MC_NCC2 = float(os.environ.get("DYN_MC_NCC2", "0.5"))
JOIN_NCC = float(os.environ.get("DYN_JOIN_NCC", "0.85"))      # pass 2: a transition frame this like a shot joins it
FLOW_DRIFT_PX = float(os.environ.get("DYN_FLOW_DRIFT", "6"))   # flow carried anchor->anchor this far off = not trusted
NONE, FIXED, TAGS, FLOW, INTERP = 0, 1, 2, 3, 4
KIND = {NONE: "none", FIXED: "fixed", TAGS: "tags", FLOW: "flow", INTERP: "interp"}


# ---------------------------------------------------------------------------------------------- geometry
_POSES = {}


def tag_layout(tag_csv=CAL.TAG_CSV):
    """{id: (4x3 corner points, centre, unit normal)} of the field's AprilTags (cached)."""
    if tag_csv not in _POSES:
        out = {}
        for tid, (c, yaw) in CAL.load_tag_poses(tag_csv).items():
            out[tid] = (CAL.tag_corners((c, yaw)), c, np.array([np.cos(yaw), np.sin(yaw), 0.0]))
        _POSES[tag_csv] = out
    return _POSES[tag_csv]


def _K(f, cx, cy):
    return np.array([[f, 0, cx], [0, f, cy], [0, 0, 1.0]])


class Pose:
    """One camera: rotation vector rv, centre C (m), focal f (px; fy = a * f), principal point, distortion (5)."""
    __slots__ = ("rv", "C", "f", "cx", "cy", "D", "a")

    def __init__(self, rv, C, f, cx, cy, D=None, a=1.0):
        self.rv = np.asarray(rv, float).reshape(3); self.C = np.asarray(C, float).reshape(3)
        self.f, self.cx, self.cy, self.a = float(f), float(cx), float(cy), float(a)
        self.D = np.zeros(5) if D is None else np.asarray(D, float).reshape(5)

    @classmethod
    def from_raw(cls, raw):
        K = np.array(raw["K"], float); rv = np.array(raw["rvec"], float).reshape(3)
        R, _ = cv2.Rodrigues(rv)
        C = -R.T @ np.array(raw["tvec"], float).reshape(3)
        D = np.zeros(5); d = np.ravel(raw["dist"])[:5]; D[:len(d)] = d
        return cls(rv, C, K[0, 0], K[0, 2], K[1, 2], D, K[1, 1] / K[0, 0])

    def R(self):
        return cv2.Rodrigues(self.rv)[0]

    def K(self):
        K = _K(self.f, self.cx, self.cy); K[1, 1] *= self.a
        return K

    def raw(self, W, H, **extra):
        R = self.R()
        d = dict(W=int(W), H=int(H), K=self.K().tolist(), dist=self.D.tolist(), rvec=self.rv.tolist(),
                 tvec=(-R @ self.C).tolist())
        d.update(extra)
        return d

    def project(self, P):
        """Field points -> px (NaN behind the camera)."""
        P = np.asarray(P, float).reshape(-1, 3)
        R = self.R()
        q, _ = cv2.projectPoints(P.reshape(-1, 1, 3), self.rv, -R @ self.C, self.K(), self.D)
        q = q.reshape(-1, 2)
        q[(P - self.C) @ R[2] <= 0.1] = np.nan
        return q

    def copy(self):
        return Pose(self.rv.copy(), self.C.copy(), self.f, self.cx, self.cy, self.D.copy(), self.a)


def _points(tags, lay):
    """{id: 4x2 corners} -> (P 4n x 3, uv 4n x 2, ids)."""
    ids = sorted(t for t in tags if t in lay)
    if not ids: return np.zeros((0, 3)), np.zeros((0, 2)), []
    return (np.concatenate([lay[i][0] for i in ids]), np.concatenate([np.asarray(tags[i], float) for i in ids]), ids)


def _rms(pose, P, uv):
    e = pose.project(P) - uv
    if not np.isfinite(e).all(): return float("inf")
    return float(np.sqrt((e ** 2).sum(1).mean()))


def _plausible(pose, W):
    C = pose.C
    return (0.2 < C[2] < 25 and np.hypot(C[0] - CAMM.LEN / 2, C[1] - CAMM.WID / 2) < 45
            and 0.2 * W < pose.f < 40 * W)


def _kabsch(a, b):
    """R minimising sum |R a_i - b_i|^2 (rows of a, b are unit vectors)."""
    U, _, Vt = np.linalg.svd(a.T @ b)
    S = np.diag([1, 1, np.sign(np.linalg.det(Vt.T @ U.T))])
    return Vt.T @ S @ U.T


def solve_rot(P, uv, C, W, H, D=None, init=None, cx=None, cy=None):
    """Rotation + focal length with the camera centre C fixed (a panning / zooming broadcast camera).
    Works from one tag (4 corners).  -> (Pose, rms px) or (None, inf)."""
    from scipy.optimize import least_squares
    cx = W / 2 if cx is None else cx; cy = H / 2 if cy is None else cy
    D = np.zeros(5) if D is None else np.asarray(D, float)
    w = P - C; w /= np.linalg.norm(w, axis=1, keepdims=True)
    cands = []
    if init is not None:
        cands.append((init.rv, init.f))
    else:
        best = None
        for f in np.geomspace(0.25 * W, 30 * W, 36):
            n = cv2.undistortPoints(uv.reshape(-1, 1, 2), _K(f, cx, cy), D).reshape(-1, 2)
            d = np.c_[n, np.ones(len(n))]; d /= np.linalg.norm(d, axis=1, keepdims=True)
            R = _kabsch(w, d)
            p = Pose(cv2.Rodrigues(R)[0], C, f, cx, cy, D)
            e = _rms(p, P, uv)
            if best is None or e < best[0]: best = (e, p.rv, f)
        cands.append((best[1], best[2]))

    def res(x):
        p = Pose(x[:3], C, math.exp(x[3]), cx, cy, D)
        e = p.project(P) - uv
        e[~np.isfinite(e)] = 1e3
        return e.ravel()
    out = (None, float("inf"))
    for rv, f in cands:
        try:
            s = least_squares(res, np.r_[rv, math.log(f)], loss="soft_l1", f_scale=2.0, max_nfev=200)
        except Exception:
            continue
        p = Pose(s.x[:3], C, math.exp(s.x[3]), cx, cy, D)
        e = _rms(p, P, uv)
        if e < out[1]: out = (p, e)
    return out


def solve_free(P, uv, W, H, D=None, init=None, cx=None, cy=None, fit_k1=None, k1_sigma=0.25):
    """Rotation, centre and focal length (and radial k1 from >= 3 tags, prior N(D[0], k1_sigma)) from >= 2 tags:
    focal sweep + PnP, then least squares.  -> (Pose, rms px) or (None, inf)."""
    from scipy.optimize import least_squares
    if len(P) < 6: return None, float("inf")
    cx = W / 2 if cx is None else cx; cy = H / 2 if cy is None else cy
    D = np.zeros(5) if D is None else np.asarray(D, float)
    fit_k1 = len(P) >= 12 if fit_k1 is None else fit_k1
    k10 = float(D[0])
    cands = []
    if init is not None: cands.append(init)
    sweep = []
    for f in np.geomspace(0.25 * W, 30 * W, 40):
        try:
            ok, rv, tv = cv2.solvePnP(P.reshape(-1, 1, 3), uv.reshape(-1, 1, 2), _K(f, cx, cy), D,
                                      flags=cv2.SOLVEPNP_SQPNP)
        except cv2.error:
            continue
        if not ok: continue
        R, _ = cv2.Rodrigues(rv)
        p = Pose(rv, -R.T @ tv.ravel(), f, cx, cy, D)
        e = _rms(p, P, uv)
        if np.isfinite(e): sweep.append((e, p))
    sweep.sort(key=lambda c: c[0])
    cands += [p for _, p in sweep[:3]]

    def mk(x):
        Dx = D.copy()
        if fit_k1: Dx[0] = x[7]
        return Pose(x[:3], x[3:6], math.exp(x[6]), cx, cy, Dx)

    def res(x):
        e = mk(x).project(P) - uv
        e[~np.isfinite(e)] = 1e3
        return np.r_[e.ravel(), (x[7] - k10) / k1_sigma] if fit_k1 else e.ravel()
    out = (None, float("inf"))
    for p0 in cands:
        x0 = np.r_[p0.rv, p0.C, math.log(p0.f)]
        if fit_k1: x0 = np.r_[x0, p0.D[0]]
        try:
            s = least_squares(res, x0, loss="soft_l1", f_scale=2.0, max_nfev=300)
        except Exception:
            continue
        p = mk(s.x)
        e = _rms(p, P, uv)
        if e < out[1] and _plausible(p, W) and abs(p.D[0]) < 0.6: out = (p, e)
    return out


def solve_shot(frames, C0, W, H, D=None, cx=None, cy=None, fit_C=True, fit_k1=False, k1_sigma=0.25):
    """Joint fit of one camera centre (+ radial k1) + per-frame rotation / focal over a shot's tag frames.
    frames: [(P, uv, Pose init)] -> (C, k1, [Pose], [rms]).  fit_C=False: C0 fixed."""
    from scipy.optimize import least_squares
    from scipy.sparse import lil_matrix
    cx = W / 2 if cx is None else cx; cy = H / 2 if cy is None else cy
    D = np.zeros(5) if D is None else np.asarray(D, float)
    m = len(frames); k10 = float(D[0])
    g = (3 if fit_C else 0) + (1 if fit_k1 else 0)
    x0 = np.r_[np.asarray(C0, float)] if fit_C else np.zeros(0)
    if fit_k1: x0 = np.r_[x0, k10]
    x0 = np.r_[x0, np.concatenate([np.r_[p.rv, math.log(p.f)] for _, _, p in frames])]

    def poses(x):
        C = x[:3] if fit_C else np.asarray(C0, float)
        Dx = D.copy()
        if fit_k1: Dx[0] = x[g - 1]
        return C, Dx, [Pose(x[g + 4 * i:g + 4 * i + 3], C, math.exp(x[g + 4 * i + 3]), cx, cy, Dx) for i in range(m)]

    def res(x):
        _, Dx, ps = poses(x)
        r = []
        for (P, uv, _), p in zip(frames, ps):
            e = p.project(P) - uv; e[~np.isfinite(e)] = 1e3; r.append(e.ravel())
        if fit_k1: r.append([(Dx[0] - k10) / k1_sigma])
        return np.concatenate(r)
    # sparsity: residuals of frame i depend on the shared parameters and frame i's 4
    nres = sum(2 * len(P) for P, _, _ in frames) + (1 if fit_k1 else 0)
    S = lil_matrix((nres, len(x0)), dtype=int); r0 = 0
    for i, (P, _, _) in enumerate(frames):
        S[r0:r0 + 2 * len(P), :g] = 1; S[r0:r0 + 2 * len(P), g + 4 * i:g + 4 * i + 4] = 1; r0 += 2 * len(P)
    if fit_k1: S[r0, g - 1] = 1
    s = least_squares(res, x0, loss="soft_l1", f_scale=2.0, max_nfev=80, jac_sparsity=S, x_scale="jac")
    C, Dx, ps = poses(s.x)
    return C, float(Dx[0]), ps, [_rms(p, P, uv) for (P, uv, _), p in zip(frames, ps)]


def from_homography(pose, Hm):
    """Pose of the next frame of a camera that only rotates / zooms about its centre: the image moved by
    Hm (px -> px, distortion ignored) => K2 R2 ~ Hm K1 R1."""
    M = Hm @ pose.K() @ pose.R()
    s = math.copysign(np.linalg.norm(M[2]), np.linalg.det(M))
    if not np.isfinite(s) or abs(s) < 1e-12: return None
    fx = np.linalg.norm(M[0] - pose.cx * M[2]) / abs(s)
    fy = np.linalg.norm(M[1] - pose.cy * M[2]) / abs(s)
    f = math.sqrt(fx * fy)
    if not np.isfinite(f) or f <= 0: return None
    A = np.linalg.inv(_K(f, pose.cx, pose.cy)) @ M / s
    U, _, Vt = np.linalg.svd(A)
    R = U @ Vt
    if np.linalg.det(R) < 0: return None
    return Pose(cv2.Rodrigues(R)[0], pose.C, f, pose.cx, pose.cy, pose.D)


def blend(a, b, w):
    """Pose between a (w=0) and b (w=1): slerp rotation, log-linear focal, linear centre."""
    Ra, Rb = a.R(), b.R()
    rel = cv2.Rodrigues(Rb @ Ra.T)[0].ravel()
    R = cv2.Rodrigues(w * rel)[0] @ Ra
    return Pose(cv2.Rodrigues(R)[0], (1 - w) * a.C + w * b.C, math.exp((1 - w) * math.log(a.f) + w * math.log(b.f)),
                a.cx, a.cy, a.D)


def pose_px_diff(a, b, W, H, grid=1.0):
    """Median / max pixel distance between two cameras' projections of the carpet grid points inside the image."""
    g = np.array([[x, y, 0] for x in np.arange(0, CAMM.LEN + 1e-6, grid) for y in np.arange(0, CAMM.WID + 1e-6, grid)])
    qa, qb = a.project(g), b.project(g)
    ok = np.isfinite(qa).all(1) & np.isfinite(qb).all(1) & (qa[:, 0] >= 0) & (qa[:, 0] < W) & (qa[:, 1] >= 0) & (qa[:, 1] < H)
    if not ok.any(): return None, None
    d = np.linalg.norm(qa[ok] - qb[ok], axis=1)
    return float(np.median(d)), float(d.max())


# ---------------------------------------------------------------------------------------------- tags
def _detector():
    return CAL._wtagdet()                 # one per process (pupil_apriltags' destructor can segfault)


def _accept(d, lay, min_margin=15):
    return d.hamming == 0 and d.decision_margin >= min_margin and int(d.tag_id) in lay


def detect_full(gray, lay, masks=()):
    """All tags on the frame at 1x (close-up tags are 30-150 px)."""
    out = {}
    for d in _detector().detect(gray):
        if _accept(d, lay) and not _in_masks(d.center, masks):
            out[int(d.tag_id)] = (d.corners.astype(float), float(d.decision_margin))
    return out


def detect_tiled(gray, lay, masks=(), scale=2.0):
    out = CAL.detect_tags_gray(gray, scale=scale, tile=640, overlap=96, valid=set(lay))
    return {k: v for k, v in out.items() if not _in_masks(v[0].mean(0), masks)}


def predict_tags(pose, lay, W, H, masks=(), margin=6, min_px=5.0):
    """Tags the pose expects on screen: [(id, centre uv, size px)] (facing the camera, inside the frame)."""
    out = []
    for tid, (P4, c, n) in lay.items():
        v = pose.C - c
        if n @ v < 0.17 * np.linalg.norm(v): continue                 # seen edge-on or from behind
        q = pose.project(P4)
        if not np.isfinite(q).all(): continue
        if q[:, 0].min() < margin or q[:, 1].min() < margin or q[:, 0].max() > W - margin or q[:, 1].max() > H - margin:
            continue
        s = float(max(np.linalg.norm(q[0] - q[2]), np.linalg.norm(q[1] - q[3])))
        if s < min_px or _in_masks(q.mean(0), masks): continue
        out.append((tid, q.mean(0), s))
    return out


def detect_windows(gray, preds, lay, slack=12.0, max_scale=4.0, want=None):
    """Tags in windows around predicted positions (upscaled so a tag spans >= ~48 px).
    want: only these ids (default: any layout tag found in a window)."""
    H, W = gray.shape[:2]
    det = _detector(); out = {}
    for tid, (u, v), s in preds:
        if tid in out: continue
        half = int(max(1.1 * s, 18) + slack + 0.25 * s)
        x0, y0 = max(0, int(u) - half), max(0, int(v) - half)
        x1, y1 = min(W, int(u) + half), min(H, int(v) + half)
        if x1 - x0 < 16 or y1 - y0 < 16: continue
        sc = float(np.clip(48.0 / max(s, 1.0), 1.0, max_scale))
        sub = gray[y0:y1, x0:x1]
        if sc > 1.01: sub = cv2.resize(sub, None, fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC)
        for d in det.detect(sub):
            if not _accept(d, lay): continue
            k = int(d.tag_id)
            if want is not None and k not in want: continue
            c = d.corners / sc + [x0, y0]
            if k not in out or d.decision_margin > out[k][1]:
                out[k] = (c.astype(float), float(d.decision_margin))
    return out


def _in_masks(uv, masks):
    u, v = float(uv[0]), float(uv[1])
    return any(a <= u < c and b <= v < d for a, b, c, d in masks)


# ---------------------------------------------------------------------------------------------- pass 1
def _flow_mask(W, H, masks, s):
    m = np.full((int(round(H * s)), int(round(W * s))), 255, np.uint8)
    for a, b, c, d in masks:
        m[int(b * s):int(math.ceil(d * s)), int(a * s):int(math.ceil(c * s))] = 0
    return m


def homography(prev, cur, fmask, s):
    """Full-res px homography prev -> cur from sparse LK flow on the small frames; None if unreliable.
    -> (H, inlier count, n tracked)."""
    p0 = cv2.goodFeaturesToTrack(prev, maxCorners=300, qualityLevel=0.01, minDistance=8, mask=fmask, blockSize=7)
    if p0 is None or len(p0) < 20: return None, 0, 0
    p1, st, _ = cv2.calcOpticalFlowPyrLK(prev, cur, p0, None, winSize=(21, 21), maxLevel=3,
                                         criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03))
    ok = st.ravel() == 1
    if ok.sum() < 15: return None, 0, int(ok.sum())
    a, b = p0[ok].reshape(-1, 2), p1[ok].reshape(-1, 2)
    Hs, inl = cv2.findHomography(a, b, cv2.RANSAC, 1.5)
    if Hs is None: return None, 0, int(ok.sum())
    ni = int(inl.sum())
    if ni < 12 or ni < 0.35 * ok.sum(): return None, ni, int(ok.sum())
    S = np.diag([s, s, 1.0]); Si = np.diag([1 / s, 1 / s, 1.0])
    return Si @ Hs @ S, ni, int(ok.sum())


class PoseTrack:
    """Per 30 Hz sample n (the tracker's detection frames): camera or none.
    Arrays: t, k (video frame index), kind (NONE/FIXED/TAGS/FLOW/INTERP), shot, rms (anchor tag RMS px),
    ntag (tags at that frame), age (s to the nearest anchor), rv / C / f (NaN without pose).
    ref: the fixed reference camera dict (or None); W, H, cx, cy, D (non-reference lens)."""

    def __init__(self, n, W, H, ref=None):
        self.n, self.W, self.H, self.ref = n, int(W), int(H), ref
        self.t = np.zeros(n); self.k = np.zeros(n, int); self.kind = np.zeros(n, np.int8)
        self.shot = np.zeros(n, int); self.rms = np.full(n, np.nan); self.ntag = np.zeros(n, int)
        self.age = np.full(n, np.nan); self.rv = np.full((n, 3), np.nan); self.C = np.full((n, 3), np.nan)
        self.f = np.full(n, np.nan); self.k1 = np.full(n, np.nan); self.cx, self.cy = W / 2, H / 2
        self.motion = np.full(n, np.nan)        # px the image corners moved since the previous sample (flow)
        self.info = {}
        self._cache = {}
        self._refcam = None

    @property
    def ok(self):
        return self.kind > 0

    def pose(self, i):
        if self.kind[i] == NONE: return None
        if self.kind[i] == FIXED: return Pose.from_raw(self.ref)
        return Pose(self.rv[i], self.C[i], self.f[i], self.cx, self.cy, np.r_[self.k1[i], 0, 0, 0, 0])

    def raw(self, i):
        if self.kind[i] == NONE: return None
        if self.kind[i] == FIXED: return self.ref
        return self.pose(i).raw(self.W, self.H, dyn_kind=KIND[int(self.kind[i])])

    def camera(self, i):
        """camera.Camera of sample i (the same object for every fixed-view frame), or None."""
        if i >= self.n or self.kind[i] == NONE: return None
        if self.kind[i] == FIXED:
            if self._refcam is None: self._refcam = CAMM.Camera(self.ref)
            return self._refcam
        c = self._cache.get(i)
        if c is None:
            if len(self._cache) > 64: self._cache.clear()
            c = self._cache[i] = CAMM.Camera(self.raw(i))
        return c

    def set(self, i, kind, pose=None, rms=np.nan, age=0.0):
        self.kind[i] = kind; self.rms[i] = rms; self.age[i] = age
        if pose is not None and kind != FIXED:
            self.rv[i], self.C[i], self.f[i], self.k1[i] = pose.rv, pose.C, pose.f, pose.D[0]

    def summary(self):
        k = self.kind
        n = max(self.n, 1)
        frac = lambda m: round(float(np.mean(m)), 3) if self.n else 0.0
        anc = np.isfinite(self.rms) & (k == TAGS)
        moving = (k >= TAGS)
        zoom = self.f[moving] / (self.ref["K"][0][0] if self.ref else self.W / 2) if moving.any() else np.zeros(0)
        out = dict(frames=int(self.n), pose_ok=frac(k > 0), fixed=frac(k == FIXED), moving=frac(moving),
                   tags=frac(k == TAGS), flow=frac(k == FLOW), interp=frac(k == INTERP), none=frac(k == NONE),
                   shots=int(len(np.unique(self.shot))) if self.n else 0,
                   anchor_rms_px=dict(median=round(float(np.median(self.rms[anc])), 2) if anc.any() else None,
                                      p90=round(float(np.percentile(self.rms[anc], 90)), 2) if anc.any() else None),
                   age_s_p90=round(float(np.nanpercentile(self.age[moving], 90)), 2) if moving.any() else None,
                   zoom_vs_ref=dict(min=round(float(zoom.min()), 2), max=round(float(zoom.max()), 2)) if len(zoom) else None,
                   seconds=n / FPS)
        out.update(self.info)
        return out

    def view_string(self, step=3):
        """One char per `step` samples: F fixed, M moving view with a pose, - none."""
        ch = np.where(self.kind == FIXED, "F", np.where(self.kind > 0, "M", "-"))
        return "".join(ch[::step])

    def save(self, path):
        np.savez_compressed(path, t=self.t, k=self.k, kind=self.kind, shot=self.shot, rms=self.rms, ntag=self.ntag,
                            age=self.age, rv=self.rv, C=self.C, f=self.f, k1=self.k1, motion=self.motion,
                            meta=json.dumps(dict(W=self.W, H=self.H, ref=self.ref, cx=self.cx, cy=self.cy, info=self.info)))

    @classmethod
    def load(cls, path):
        z = np.load(path, allow_pickle=False)
        m = json.loads(str(z["meta"]))
        p = cls(len(z["t"]), m["W"], m["H"], m["ref"])
        for k in ("t", "k", "kind", "shot", "rms", "ntag", "age", "rv", "C", "f", "k1", "motion"):
            if k in z: setattr(p, k, z[k].copy())
        p.cx, p.cy, p.info = m["cx"], m["cy"], m.get("info", {})
        return p


def _tiny(small):
    return cv2.resize(small, (96, 54), interpolation=cv2.INTER_AREA)


def _ncc(a, b, valid):
    """Normalised correlation of two tiny frames over `valid` pixels (None if < 30% valid)."""
    if valid.mean() < 0.3: return None
    x, y = a[valid].astype(np.float32), b[valid].astype(np.float32)
    x -= x.mean(); y -= y.mean()
    return float((x * y).sum() / (math.sqrt(float((x * x).sum() * (y * y).sum())) + 1e-6))


def warp_ncc(old, new, Hc, tmask, St, Sti):
    """NCC of tiny frame `new` with `old` warped by the full-res homography Hc (None: unwarped)."""
    if Hc is None:
        return _ncc(new, old, tmask)
    Ht = St @ Hc @ Sti
    w = cv2.warpPerspective(old, Ht, (96, 54), flags=cv2.INTER_LINEAR)
    v = cv2.warpPerspective(tmask.astype(np.uint8), Ht, (96, 54), flags=cv2.INTER_NEAREST) > 0
    return _ncc(new, w, v & tmask)


def refine_rotation(pose, P, uv):
    """Re-aim a known camera (centre, intrinsics fixed) to tag corners: a bumped / drifting fixed camera."""
    from scipy.optimize import least_squares

    def res(x):
        e = Pose(x, pose.C, pose.f, pose.cx, pose.cy, pose.D, pose.a).project(P) - uv
        e[~np.isfinite(e)] = 1e3
        return e.ravel()
    s = least_squares(res, pose.rv, loss="soft_l1", f_scale=1.0, max_nfev=100)
    return Pose(s.x, pose.C, pose.f, pose.cx, pose.cy, pose.D, pose.a)


def rot_deg(a, b):
    return float(np.degrees(np.linalg.norm(cv2.Rodrigues(b.R() @ a.R().T)[0])))


class _Scan:
    """Pass 1 over the decoded samples (see the module doc)."""

    def __init__(self, W, H, ref=None, view_ref=None, masks=(), tag_csv=CAL.TAG_CSV, tag_hz=TAG_HZ, log=print,
                 keep_frames=0):
        self.W, self.H, self.log = W, H, log
        self.lay = tag_layout(tag_csv)
        self.ref = ref; self.refpose = Pose.from_raw(ref) if ref else None; self.refadj = self.refpose
        self.masks = [tuple(int(v) for v in m) for m in (masks or [])]
        self.s = SMALL_W / W
        self.fmask = _flow_mask(W, H, self.masks, self.s)
        self.tmask = _tiny(self.fmask) > 128
        self.St = np.diag([96.0 / W, 54.0 / H, 1.0]); self.Sti = np.linalg.inv(self.St)
        self.ref_tiny = None
        if view_ref is not None:
            im = cv2.imread(view_ref) if isinstance(view_ref, str) else view_ref
            if im is not None:
                g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY) if im.ndim == 3 else im
                g = cv2.resize(g, (self.fmask.shape[1], self.fmask.shape[0]), interpolation=cv2.INTER_AREA)
                self.ref_tiny = _tiny(g)
        self.learn_ref = self.ref_tiny is None
        self.step = max(1, int(round(FPS / tag_hz)))
        self.recs = []                      # per sample dict
        self.hist = []                      # (tiny, H from the previous sample) of the last MC_LAG samples
        self.shot = 0; self.state = "new"; self.guess = None; self.shotC = []; self.shotK1 = []; self.freeC = []
        self.prev_small = None; self.last_tag = -99; self.last_full = -99; self.last_tiled = -999; self.last_ref = -99
        self.n_tries = 0; self.fast = False
        self.ms = dict(tags=0.0, flow=0.0, solve=0.0)
        self.keep_frames = keep_frames; self.kept = []          # (n, gray) of fixed-view frames (auto_setup)

    def _new_shot(self, state="new"):
        self.shot += 1; self.state = state; self.guess = None; self.shotC = []; self.shotK1 = []; self.n_tries = 0
        self.last_full = -99; self.last_tiled = -999

    def _mc_ncc(self, tiny, lag):
        """Correlation with the frame `lag` samples ago, warped along the flow (a pan / zoom keeps it high, a cut
        or a dissolve does not).  None while the history is short."""
        if len(self.hist) < lag: return None
        Hc = np.eye(3)
        for _, Hm in self.hist[-lag + 1:] + [(None, self._h_now)]:
            if Hm is None: Hc = None; break
            Hc = Hm @ Hc
        return warp_ncc(self.hist[-lag][0], tiny, Hc, self.tmask, self.St, self.Sti)

    def push(self, n, k, t, gray):
        small = cv2.resize(gray, (self.fmask.shape[1], self.fmask.shape[0]), interpolation=cv2.INTER_AREA)
        tiny = _tiny(small)
        rec = dict(n=n, k=k, t=t, shot=None, state=None, H=None, tags=None, ncc=None, mc=None, refncc=None, pose=None,
                   rms=None)
        if float(small[::4, ::4].mean()) <= NO_FEED_LEVEL:
            if self.state != "dark": self._new_shot("dark")
            rec.update(shot=self.shot, state="dark"); self.recs.append(rec)
            self.prev_small = None; self.hist = []
            return
        if self.state == "dark": self._new_shot()
        ncc = _ncc(tiny, self.hist[-1][0], self.tmask) if self.hist else None
        rec["ncc"] = ncc
        if ncc is None or ncc < CUT_NCC:                # hard cut
            if self.hist: self._new_shot()
            self.hist = []; self.prev_small = None
        # flow (identity while the fixed camera is on air)
        Hm = None
        if self.state == "fixed":
            Hm = np.eye(3)
        elif self.prev_small is not None:
            t0 = time.time()
            Hm, _, _ = homography(self.prev_small, small, self.fmask, self.s)
            self.ms["flow"] += time.time() - t0
        self._h_now = Hm
        rec["H"] = Hm if self.state != "fixed" else None
        mc, mc2 = self._mc_ncc(tiny, MC_LAG), self._mc_ncc(tiny, MC_LAG2)
        rec["mc"], rec["mc2"], rec["tiny"] = mc, mc2, tiny
        refncc = _ncc(tiny, self.ref_tiny, self.tmask) if self.ref_tiny is not None else None
        rec["refncc"] = refncc
        if (mc is not None and mc < MC_NCC) or (mc2 is not None and mc2 < MC_NCC2):
            if self.state != "trans": self._new_shot("trans")   # dissolve / wipe / whip pan in progress: no pose
        elif self.state == "trans":
            self._new_shot()
        if self.state == "fixed" and refncc is not None and refncc < VIEW_NCC:
            self._new_shot("trans")                     # the fixed view left the air without a hard cut
        if self.state == "moving" and self.refpose is not None and n - self.last_ref >= (
                self.step if refncc is not None and refncc >= VIEW_NCC else (2 * FPS if self.ref_tiny is None else 1e9)):
            self.last_ref = n                           # back on the fixed camera without a detected cut?
            r = self._ref_check(gray)
            if r is not None:
                self._new_shot("fixed"); self.refadj = r[2]
                rec.update(tags=r[0], fixed_err=round(r[1], 2))
        self.hist.append((tiny, Hm)); self.hist = self.hist[-MC_LAG2 - 1:]
        self.prev_small = small
        rec["shot"] = self.shot
        if self.state == "fixed":
            if self.keep_frames and len(self.kept) < self.keep_frames and n - self.last_tag >= 4 * FPS:
                self.kept.append((n, gray.copy())); self.last_tag = n
        elif self.state != "trans":
            if self.guess is not None and Hm is not None:
                self.guess = from_homography(self.guess, Hm) or self.guess
            hi = self.guess is not None and self.guess.f > HIGH_ZOOM * self.W   # robots fill the frame: flow unreliable
            step = 2 if hi else (max(2, self.step // 2) if self.fast else self.step)
            due = (self.state == "new" and (self.n_tries < 3 or n - self.last_tag >= 3)) or n - self.last_tag >= step
            if due:
                self._tag_frame(rec, gray, refncc)
        rec["state"] = self.state
        self.recs.append(rec)

    def _ref_check(self, gray):
        """Tags where the (re-aimed) reference camera predicts them -> (tags, median err px, re-aimed Pose) or None."""
        W, H = self.W, self.H
        preds = predict_tags(self.refadj, self.lay, W, H, self.masks)
        found = detect_windows(gray, preds, self.lay, slack=8.0, max_scale=4.0, want={p[0] for p in preds})
        tags = {k: v[0] for k, v in found.items()}
        P, uv, ids = _points(tags, self.lay)
        if len(ids) < 3: return None
        e = np.median(np.linalg.norm(self.refadj.project(P) - uv, axis=1).reshape(-1, 4).mean(1))
        if e <= FIXED_PX: return tags, float(e), self.refadj
        if e > 4 * FIXED_PX: return None
        p2 = refine_rotation(self.refpose, P, uv)        # the fixed camera was nudged / drifted
        e2 = np.median(np.linalg.norm(p2.project(P) - uv, axis=1).reshape(-1, 4).mean(1))
        if e2 <= 1.5 and rot_deg(self.refpose, p2) < 1.0:
            return tags, float(e2), p2
        return None

    def _tag_frame(self, rec, gray, refncc):
        n = rec["n"]; W, H = self.W, self.H
        self.last_tag = n; self.n_tries += 1
        t0 = time.time()
        # 1. is this the fixed reference camera?
        if self.refpose is not None and self.state == "new" and (refncc is None or refncc >= 0.8 * VIEW_NCC):
            r = self._ref_check(gray)
            if r is not None:
                self.ms["tags"] += time.time() - t0
                tags, e, p = r
                self.refadj = p
                self.state = "fixed"; rec["tags"] = tags; rec["fixed_err"] = round(e, 2)
                if self.learn_ref and self.ref_tiny is None:
                    small = cv2.resize(gray, (self.fmask.shape[1], self.fmask.shape[0]), interpolation=cv2.INTER_AREA)
                    self.ref_tiny = _tiny(small)
                return
        # 2. other views
        found = {}
        if self.guess is not None:
            found = detect_windows(gray, predict_tags(self.guess, self.lay, W, H, self.masks), self.lay)
        if self.guess is None or (len(found) < 3 and n - self.last_full >= FPS):
            for k_, v in detect_full(gray, self.lay, self.masks).items():
                if k_ not in found or v[1] > found[k_][1]: found[k_] = v
            self.last_full = n
        if not found and self.guess is None and n - self.last_tiled >= 2 * FPS:
            found = detect_tiled(gray, self.lay, self.masks)
            self.last_tiled = n
        self.ms["tags"] += time.time() - t0
        tags = {k_: v[0] for k_, v in found.items()}
        rec["tags"] = tags
        if not tags: return
        if self.guess is not None:                     # how far the flow-carried pose drifted: sample tags faster
            P, uv, _ = _points(tags, self.lay)
            q = self.guess.project(P)
            if np.isfinite(q).all():
                rec["drift"] = float(np.median(np.linalg.norm(q - uv, axis=1)))
                self.fast = rec["drift"] > FLOW_DRIFT_PX / 2
        t0 = time.time()
        p, e = self._solve_online(tags)
        self.ms["solve"] += time.time() - t0
        if p is not None and e <= 1.5 * MAX_RMS:
            self.guess = p; self.state = "moving"; rec["pose"] = p; rec["rms"] = e
            # a second pass for tags the first pose predicts but the search missed
            if len(tags) < 6:
                more = detect_windows(gray, [q for q in predict_tags(p, self.lay, W, H, self.masks) if q[0] not in tags],
                                      self.lay)
                if more:
                    tags = {**tags, **{k_: v[0] for k_, v in more.items()}}; rec["tags"] = tags
                    p2, e2 = self._solve_online(tags)
                    if p2 is not None and e2 <= 1.5 * MAX_RMS:
                        self.guess, rec["pose"], rec["rms"] = p2, p2, e2

    def _solve_online(self, tags):
        P, uv, ids = _points(tags, self.lay)
        W, H = self.W, self.H
        k1 = float(np.median(self.shotK1)) if self.shotK1 else K1_PRIOR
        D = np.r_[k1, 0, 0, 0, 0]
        if self.shotC:
            C = np.median(np.array(self.shotC), 0)
            g = self.guess
            return solve_rot(P, uv, C, W, H, D, init=g if g is not None and np.allclose(g.C, C) and g.D[0] == k1 else None)
        best = (None, float("inf"))
        if len(ids) >= 2:
            p, e = solve_free(P, uv, W, H, D)
            if p is not None and e <= MAX_RMS:
                self.shotC.append(p.C); self.freeC.append((p.C, p.D[0], p.f))
                if len(ids) >= 3: self.shotK1.append(p.D[0])
                best = (p, e)
        if best[0] is None:
            for C, k1c, _ in _clusters(self.freeC):
                p, e = solve_rot(P, uv, C, W, H, np.r_[k1c, 0, 0, 0, 0])
                if p is not None and e < best[1]: best = (p, e)
        return best


def _clusters(cams, radius=0.75):
    """Greedy clusters of camera centres [(C, k1, f)] -> [(median C, median k1, median f)] (biggest first)."""
    left = list(cams); out = []
    while left:
        X = np.array([c[0] for c in left])
        cnt = [(np.linalg.norm(X - X[j], axis=1) < radius).sum() for j in range(len(X))]
        j = int(np.argmax(cnt))
        m = np.linalg.norm(X - X[j], axis=1) < radius
        sel = [c for c, keep in zip(left, m) if keep]
        out.append((np.median(X[m], 0), float(np.median([c[1] for c in sel])), float(np.median([c[2] for c in sel]))))
        left = [c for c, keep in zip(left, m) if not keep]
    return out


# ---------------------------------------------------------------------------------------------- pass 2
def _finalize(scan, log=print):
    recs = scan.recs; n = len(recs); W, H = scan.W, scan.H; lay = scan.lay
    pt = PoseTrack(n, W, H, scan.ref)
    corners = np.array([[0, 0, 1], [W, 0, 1], [0, H, 1], [W, H, 1.0]]).T
    for i, r in enumerate(recs):
        pt.t[i], pt.k[i] = r["t"], r["k"]
        pt.ntag[i] = len(r["tags"] or {})
        if r["H"] is not None:
            q = r["H"] @ corners
            pt.motion[i] = float(np.abs(q[:2] / q[2] - corners[:2]).max())
    _join_transitions(scan, recs)
    for i, r in enumerate(recs): pt.shot[i] = r["shot"] if r["shot"] is not None else -1
    clusters = [(C, k1) for C, k1, _ in _clusters(scan.freeC)]
    if scan.refpose is not None: clusters.append((scan.refpose.C, K1_PRIOR))
    shots = {}
    for i, r in enumerate(recs): shots.setdefault(r["shot"], []).append(i)
    nshot_fixed = nshot_moving = 0
    for sid, idx in shots.items():
        states = {recs[i]["state"] for i in idx}
        if "fixed" in states:
            nshot_fixed += 1
            for i in idx:
                if recs[i]["state"] == "fixed": pt.set(i, FIXED, rms=np.nan, age=0.0)
            continue
        if "dark" in states: continue
        tf = [i for i in idx if recs[i]["tags"]]
        if not tf: continue
        # camera centre (+ k1) candidates: this shot's online solves, the clip's cameras, the reference's mount
        own = [recs[i]["pose"] for i in tf if recs[i]["pose"] is not None]
        cands = ([(np.median(np.array([p.C for p in own]), 0), float(np.median([p.D[0] for p in own])))] if own else [])
        cands += clusters
        cands += [(C, 0.0) for C, k1 in cands if abs(k1) > 0.02]      # high zoom: (nearly) no distortion
        best = None
        for C, k1 in cands:
            D = np.r_[k1, 0, 0, 0, 0]
            sols = []
            for i in tf:
                P, uv, ids = _points(recs[i]["tags"], lay)
                p0 = recs[i]["pose"]
                p, e = solve_rot(P, uv, C, W, H, D, init=Pose(p0.rv, C, p0.f, W / 2, H / 2, D) if p0 is not None else None)
                sols.append((i, P, uv, p, e))
            good = [s_ for s_ in sols if s_[3] is not None and s_[4] <= MAX_RMS]
            cost = sum(min(s_[4], 3 * MAX_RMS) ** 2 for s_ in sols if s_[3] is not None) + 100 * (len(sols) - len(good))
            if best is None or (len(good), -cost) > (len(best[2]), -best[3]): best = (C, k1, good, cost)
        if best is None or not best[2]: continue
        C, k1, good, _ = best
        multi = [g for g in good if len(g[1]) >= 8]
        fit_k1 = any(len(g[1]) >= 12 for g in good)
        if len(multi) >= 2 or fit_k1:             # refine the centre (+ k1) with every frame of the shot
            try:
                C2, k12, ps, es = solve_shot([(g[1], g[2], g[3]) for g in good], C, W, H, np.r_[k1, 0, 0, 0, 0],
                                             fit_C=len(multi) >= 2, fit_k1=fit_k1)
                if np.median(es) <= np.median([g[4] for g in good]) * 1.05 + 0.05 and np.linalg.norm(C2 - C) < 3:
                    good = [(g[0], g[1], g[2], p, e) for g, p, e in zip(good, ps, es)]; C, k1 = C2, k12
            except Exception as ex:                 # keep the per-frame solves
                log(f"  dynpose: shot {sid} joint fit failed ({ex!r})")
        anchors = {g[0]: (g[3], g[4], g[1], g[2]) for g in good if g[4] <= MAX_RMS}
        if not anchors: continue
        nshot_moving += 1
        _fill(pt, recs, idx, anchors)
    pt.info["shot_still_px"] = _stillness(recs, shots)
    if scan.refpose is not None:
        _reaim_ref(pt, scan, recs, log)
    pt.info.update(n_cameras=len(clusters), shots_fixed=nshot_fixed, shots_moving=nshot_moving,
                   ms=dict((k, round(1000 * v / max(n, 1), 2)) for k, v in scan.ms.items()))
    return pt


def _stillness(recs, shots):
    """{shot id: px} -- how far the same tag's detected centre wandered within the shot (median over tags of the
    90th-percentile distance from its median position; tags seen in >= 3 frames).  A locked-off camera: < 0.5 px;
    a slow pan of 0.1 deg at f = 1700 px is already 3 px.  Shots without repeated tags are left out."""
    out = {}
    for sid, idx in shots.items():
        cen = {}
        for i in idx:
            for k, c in (recs[i]["tags"] or {}).items():
                cen.setdefault(k, []).append(np.asarray(c, float).mean(0))
        sp = [float(np.percentile(np.linalg.norm(np.array(v) - np.median(v, 0), axis=1), 90)) for v in cen.values() if len(v) >= 3]
        if sp: out[str(sid)] = round(float(np.median(sp)), 3)
    return out


def _join_transitions(scan, recs):
    """A detected transition (dissolve) is flagged up to 1 s too long: its frames that already / still look like
    the shot after / before it (flow-warped NCC >= JOIN_NCC, or like the fixed view) join that shot."""
    n = len(recs); i = 0
    I3 = np.eye(3)

    def like(m, j, Hc):
        if recs[j]["state"] == "fixed":
            return scan.ref_tiny is not None and (_ncc(recs[m]["tiny"], scan.ref_tiny, scan.tmask) or 0) >= JOIN_NCC
        return (warp_ncc(recs[m]["tiny"], recs[j]["tiny"], Hc, scan.tmask, scan.St, scan.Sti) or 0) >= JOIN_NCC
    while i < n:
        if recs[i]["state"] != "trans": i += 1; continue
        j = i
        while j < n and recs[j]["state"] == "trans": j += 1
        hi = j                                           # run [i, j)
        if j < n and recs[j]["state"] in ("moving", "new", "fixed"):
            Hc = I3; m = j - 1
            while m >= i:
                if recs[j]["state"] != "fixed":
                    Hm = recs[m + 1]["H"]
                    if Hm is None: break
                    Hc = Hc @ Hm                         # maps sample m -> j
                if not like(m, j, Hc): break
                recs[m]["shot"], recs[m]["state"], recs[m]["joined"] = recs[j]["shot"], recs[j]["state"], True
                hi = m; m -= 1
        if i > 0 and recs[i - 1]["state"] in ("moving", "new", "fixed"):
            Hc = I3; m = i
            while m < hi:
                if recs[i - 1]["state"] != "fixed":
                    Hm = recs[m]["H"]
                    if Hm is None: break
                    Hc = Hm @ Hc                         # maps sample i-1 -> m
                    Hinv = np.linalg.inv(Hc)
                else:
                    Hinv = I3
                if not like(m, i - 1, Hinv): break
                recs[m]["shot"], recs[m]["state"], recs[m]["joined"] = recs[i - 1]["shot"], recs[i - 1]["state"], True
                m += 1
        i = j


def _reaim_ref(pt, scan, recs, log=print):
    """Every fixed-view tag frame together: re-aim the reference camera when its tags are > 1 px off (it was nudged
    since the calibration / the pose the caller passed).  pt.ref = the re-aimed camera dict."""
    lay = scan.lay
    fr = [_points(r["tags"], lay)[:2] for r in recs if r.get("fixed_err") is not None]
    if not fr: return
    P = np.concatenate([a for a, _ in fr]); uv = np.concatenate([b for _, b in fr])
    e0 = float(np.median(np.linalg.norm(scan.refpose.project(P) - uv, axis=1)))
    info = dict(ref_tag_frames=len(fr), ref_err_px=round(e0, 2))
    if e0 > 1.0:
        p2 = refine_rotation(scan.refpose, P, uv)
        e2 = float(np.median(np.linalg.norm(p2.project(P) - uv, axis=1)))
        if e2 < 0.7 * e0:
            keep = {k: v for k, v in scan.ref.items() if k not in ("K", "dist", "rvec", "tvec", "W", "H")}
            pt.ref = p2.raw(scan.W, scan.H, **keep)
            pt.ref["dist"] = list(np.ravel(scan.ref["dist"]))          # unchanged lens
            pt.ref["dyn_reaim"] = dict(err_before_px=round(e0, 2), err_after_px=round(e2, 2),
                                       deg=round(rot_deg(scan.refpose, p2), 3), C=p2.C.tolist())
            info.update(ref_reaimed=True, ref_err_after_px=round(e2, 2), ref_reaim_deg=round(rot_deg(scan.refpose, p2), 3))
            log(f"  dynpose: fixed camera re-aimed {info['ref_reaim_deg']} deg ({e0:.2f} -> {e2:.2f} px over {len(fr)} frames)")
    pt.info.update(info)


def _carry(p, recs, idx, j0, j1):
    """Pose p of sample idx[j0] carried along the homographies to idx[j1] (either direction); None if broken."""
    if j1 >= j0:
        for j in range(j0 + 1, j1 + 1):
            Hm = recs[idx[j]]["H"]
            if Hm is None: return None
            p = from_homography(p, Hm)
            if p is None: return None
    else:
        for j in range(j0, j1, -1):
            Hm = recs[idx[j]]["H"]                        # maps idx[j-1] -> idx[j]
            if Hm is None: return None
            try:
                p = from_homography(p, np.linalg.inv(Hm))
            except np.linalg.LinAlgError:
                return None
            if p is None: return None
    return p


EDGE_AGE = float(os.environ.get("DYN_EDGE_AGE", "0.33"))      # s: one-sided flow before / after a shot's anchors
INTERP_MAX = 0.7                                              # s: anchors this close bridge an untrusted interval
HIGH_ZOOM = 1.5                                               # f > this x frame width: tags every 2nd sample, short edges


def _sane(p, anchor, dt):
    """A carried pose that rotated / zoomed faster than a broadcast camera can is a flow failure."""
    return p is not None and rot_deg(p, anchor) <= 2.0 + 20.0 * dt and 0.7 < p.f / anchor.f < 1.5


def _fill(pt, recs, idx, anchors):
    """Anchors (sample -> (Pose, rms, P, uv)) of one shot -> poses for the samples around them.
    Between two anchors: the flow-carried poses from both sides, blended (kind FLOW) -- if carrying the first
    anchor along the flow lands on the second one's tags within FLOW_DRIFT_PX; otherwise (a person / robot filling
    a zoomed frame drags the flow) interpolation when they are <= INTERP_MAX apart (INTERP), else one-sided flow
    within 0.1 s of each anchor.  Before the first / after the last anchor: one-sided flow up to EDGE_AGE.  Every carried
    pose must stay within MAX_AGE of its anchor and pass _sane()."""
    idx = sorted(idx); a_list = sorted(anchors)
    pos = {i: j for j, i in enumerate(idx)}
    trusted = {}
    for a, b in zip(a_list, a_list[1:]):
        q = _carry(anchors[a][0], recs, idx, pos[a], pos[b])
        e = _rms(q, anchors[b][2], anchors[b][3]) if q is not None else float("inf")
        trusted[(a, b)] = e <= FLOW_DRIFT_PX

    def carry_side(a, j_to_stop, step, horizon):
        out = {}
        p = anchors[a][0]; j = pos[a] + step
        while 0 <= j < len(idx) and j != j_to_stop:
            i = idx[j]
            if abs(pt.t[i] - pt.t[a]) > horizon: break
            Hm = recs[i]["H"] if step > 0 else recs[idx[j + 1]]["H"]
            if Hm is None: break
            try:
                p = from_homography(p, Hm if step > 0 else np.linalg.inv(Hm))
            except np.linalg.LinAlgError:
                p = None
            if not _sane(p, anchors[a][0], abs(pt.t[i] - pt.t[a])): break
            out[i] = p; j += step
        return out
    fwd, bwd = {}, {}
    for k, a in enumerate(a_list):
        nxt = a_list[k + 1] if k + 1 < len(a_list) else None
        prv = a_list[k - 1] if k > 0 else None
        edge = EDGE_AGE if anchors[a][0].f <= HIGH_ZOOM * pt.W else 0.1
        hf = edge if nxt is None else (MAX_AGE if trusted[(a, nxt)] else 0.1)
        hb = edge if prv is None else (MAX_AGE if trusted[(prv, a)] else 0.1)
        stop_f = pos[nxt] if nxt is not None else len(idx)
        stop_b = pos[prv] if prv is not None else -1
        for i, p in carry_side(a, stop_f, +1, hf).items(): fwd[i] = (p, a)
        for i, p in carry_side(a, stop_b, -1, hb).items(): bwd[i] = (p, a)
    for i in idx:
        if i in anchors:
            p, e = anchors[i][:2]; pt.set(i, TAGS, p, rms=e, age=0.0); continue
        prev = [a for a in a_list if a < i]; nxt = [b for b in a_list if b > i]
        a = prev[-1] if prev else None; b = nxt[0] if nxt else None
        if a is not None and b is not None and not trusted[(a, b)] and pt.t[b] - pt.t[a] <= INTERP_MAX:
            w = (pt.t[i] - pt.t[a]) / (pt.t[b] - pt.t[a])
            pt.set(i, INTERP, blend(anchors[a][0], anchors[b][0], w), age=min(pt.t[i] - pt.t[a], pt.t[b] - pt.t[i]))
            continue
        F, B = fwd.get(i), bwd.get(i)
        if F and B:
            ta, tb = pt.t[F[1]], pt.t[B[1]]
            w = (pt.t[i] - ta) / max(tb - ta, 1e-6)
            pt.set(i, FLOW, blend(F[0], B[0], w), age=min(pt.t[i] - ta, tb - pt.t[i]))
        elif F or B:
            p, a_ = F or B
            pt.set(i, FLOW, p, age=abs(pt.t[i] - pt.t[a_]))
    nbad = sum(1 for v in trusted.values() if not v)
    pt.info["flow_bad_intervals"] = pt.info.get("flow_bad_intervals", 0) + nbad


# ---------------------------------------------------------------------------------------------- entry points
def clock0(stream):
    """Where a video's clock starts (s): estimate counts video time from there.  Concatenated live .ts segments keep
    the stream's clock (a recorder sample started at 6748 s, so every frame was past t1 and nothing was read).
    Under 1 s (an mp4 edit list a frame or two in) it is 0, so the samples stay those of process.detect_video."""
    off = float(stream.start_time * stream.time_base) if stream.start_time else 0.0
    return off if off >= 1.0 else 0.0


def estimate(video, ref=None, view_ref=None, masks=(), t0=0.0, t1=190.0, tag_csv=CAL.TAG_CSV, log=print,
             tag_hz=TAG_HZ, keep_frames=0, return_scan=False):
    """Per-sample camera poses of `video` (samples exactly as process.detect_video takes them: 30 Hz, video time
    t0 (0) .. t1).  ref: the fixed camera's dict (camera.json, with the match's pose) or None; view_ref: image /
    path of that camera's view (optional, confirms its frames); masks: broadcast graphics boxes (full-frame
    px) kept out of the flow, the cut test and the tag search.  -> PoseTrack."""
    import av
    tt = time.time()
    c = av.open(video); s = c.streams.video[0]; s.thread_type = "AUTO"
    W, H = s.codec_context.width, s.codec_context.height
    if ref is not None and (int(ref["W"]), int(ref["H"])) != (W, H):
        log(f"  dynpose: reference camera is {ref['W']}x{ref['H']}, video {W}x{H}: ignored")
        ref = None
    scan = _Scan(W, H, ref, view_ref, masks, tag_csv, tag_hz, log, keep_frames)
    off = clock0(s)
    n = 0; nxt = 0.0; k = -1
    for f in c.decode(s):
        k += 1
        if f.time is None: continue
        ft = float(f.time) - off
        if ft > t1: break
        if ft + 1e-6 < nxt: continue
        nxt = (n + 1) / FPS
        if ft >= t0:
            scan.push(n, k, ft, f.to_ndarray(format="gray"))
        else:
            scan.recs.append(dict(n=n, k=k, t=ft, shot=-1, state="dark", H=None, tags=None, ncc=None,
                                  refncc=None, pose=None, rms=None))
        n += 1
        if n % 1800 == 0:
            log(f"  dynpose: {n} samples t={ft:.0f}s shots {scan.shot + 1} ({n / (time.time() - tt):.0f}/s)")
    c.close()
    pt = _finalize(scan, log)
    pt.info["seconds_wall"] = round(time.time() - tt, 1)
    sm = pt.summary()
    log(f"  dynpose: {sm['frames']} samples, pose {sm['pose_ok']:.0%} (fixed {sm['fixed']:.0%}, moving {sm['moving']:.0%}), "
        f"{sm['shots']} shots, anchor RMS {sm['anchor_rms_px']}, {pt.info['seconds_wall']}s")
    return (pt, scan) if return_scan else pt


def visible_field(cam_or_pose, W, H, step=0.5):
    """Share of the carpet (0..1) that projects inside the frame (grid of `step` m)."""
    p = cam_or_pose if isinstance(cam_or_pose, Pose) else Pose.from_raw(cam_or_pose.raw)
    g = np.array([[x, y, 0] for x in np.arange(step / 2, CAMM.LEN, step) for y in np.arange(step / 2, CAMM.WID, step)])
    q = p.project(g)
    ok = np.isfinite(q).all(1) & (q[:, 0] >= 0) & (q[:, 0] < W) & (q[:, 1] >= 0) & (q[:, 1] < H)
    return float(ok.mean())


# ---------------------------------------------------------------------------------------------- auto set-up
def _duration(video):
    import av
    c = av.open(video); s = c.streams.video[0]
    d = float(s.duration * s.time_base) if s.duration else float(c.duration or 0) / 1e6
    W, H = s.codec_context.width, s.codec_context.height
    c.close()
    return d, W, H


def _merge_boxes(boxes, gap=12):
    """Union boxes that overlap / touch (within gap px) until stable."""
    b = [list(map(int, x)) for x in boxes]
    changed = True
    while changed:
        changed = False
        for i in range(len(b)):
            for j in range(i + 1, len(b)):
                p, q = b[i], b[j]
                if p[0] - gap <= q[2] and q[0] - gap <= p[2] and p[1] - gap <= q[3] and q[1] - gap <= p[3]:
                    b[i] = [min(p[0], q[0]), min(p[1], q[1]), max(p[2], q[2]), max(p[3], q[3])]
                    del b[j]; changed = True; break
            if changed: break
    return b


def auto_overlays(video, n=16, frames=None):
    """Broadcast graphics of a program feed: pixels that stay identical while the pictures behind them change
    (layout.suggest_overlays over frames spread over the middle of the clip, where the score bar is up and several
    cameras were on air).  -> [[x0, y0, x1, y1], ...] full-frame px (score bar, logos, clock; merged)."""
    import layout as LY
    if frames is None:
        dur, _, _ = _duration(video)
        ts = np.linspace(0.3 * dur, 0.85 * dur, n)
        frames = [f for _, f in LY.grab_frames(video, ts)]
    if len(frames) < 6: return []
    sug = LY.suggest_overlays(frames)
    Hh, Ww = frames[0].shape[:2]
    boxes = _merge_boxes([o["box"] for o in sug])
    # a band of graphics along the top / bottom edge -> one full-width box (the score bar's gaps change too)
    out = []
    for b in boxes:
        if b[3] >= 0.97 * Hh and b[1] > 0.6 * Hh and b[2] - b[0] > 0.3 * Ww: b = [0, b[1], Ww, Hh]
        if b[1] <= 0.03 * Hh and b[3] < 0.4 * Hh and b[2] - b[0] > 0.3 * Ww: b = [b[0], 0, b[2], b[3]]
        out.append(b)
    return _merge_boxes(out)


def _static_shots(pt, min_s=1.5, max_px=1.0):
    """Shots whose camera did not move -- the same tags stayed on the same pixels (pt.info["shot_still_px"] <= max_px)
    -- and that show most of the field -> [(shot id, sample indices, Pose of the best anchor)]."""
    out = []
    still = pt.info.get("shot_still_px") or {}
    for sid in np.unique(pt.shot):
        idx = np.where((pt.shot == sid) & (pt.kind == TAGS))[0]
        allx = np.where((pt.shot == sid) & (pt.kind > 0))[0]
        if len(idx) < 2 or len(allx) < min_s * FPS: continue
        if still.get(str(int(sid)), 1e9) > max_px: continue
        best = idx[int(np.nanargmin(pt.rms[idx] / np.maximum(pt.ntag[idx], 1)))]
        p = pt.pose(best)
        if visible_field(p, pt.W, pt.H) < 0.6: continue
        out.append((int(sid), allx, p))
    return out


def feed_summary(pt, min_s=2.0, still_px=2.0):
    """Is this a produced program feed (cuts between cameras, or cameras that pan / zoom) or one fixed camera?
    Views = shots with >= min_s s of pose: locked-off ones (the same tags on the same pixels, <= still_px) grouped by
    pose, plus every shot whose camera moved.  >= 2 views -> program feed.  Without poses, cuts decide (>= 4 in the
    clip); with neither -> None (unknown).  -> dict(program_feed, why, views, static_views, moving_s, shots, cuts)."""
    still = pt.info.get("shot_still_px") or {}
    groups, moving, posed, mov_n = [], 0, 0, 0
    for sid in np.unique(pt.shot):
        if sid < 0: continue
        idx = np.where((pt.shot == sid) & (pt.kind > 0))[0]
        if len(idx) < min_s * FPS: continue
        posed += len(idx)
        tg = np.where((pt.shot == sid) & (pt.kind == TAGS))[0]
        sp = still.get(str(int(sid)))
        if sp is not None and sp <= still_px and len(tg):
            p = pt.pose(tg[int(np.nanargmin(pt.rms[tg] / np.maximum(pt.ntag[tg], 1)))])
            for g in groups:
                if np.linalg.norm(p.C - g[0].C) < 1.0 and rot_deg(p, g[0]) < 2.0 and abs(p.f / g[0].f - 1) < 0.05:
                    g.append(p); break
            else:
                groups.append([p])
        else:
            moving += 1; mov_n += len(idx)
    sh = pt.shot[pt.shot >= 0]
    cuts = int(np.sum(sh[1:] != sh[:-1])) if len(sh) > 1 else 0
    views = len(groups) + moving
    mov_s = round(mov_n / FPS, 1)
    if posed >= 5 * FPS:
        pf = views >= 2
        why = (f"{views} camera views ({len(groups)} locked-off, {moving} moving) and {cuts} cuts in {pt.n / FPS:.0f} s"
               if pf else f"one locked-off camera ({cuts} cuts in {pt.n / FPS:.0f} s)")
    elif cuts >= 4:
        pf, why = True, f"{cuts} cuts in {pt.n / FPS:.0f} s (too few AprilTags to tell the cameras apart)"
    else:
        pf, why = None, "too few AprilTags in view to tell"
    return dict(program_feed=pf, why=why, views=views, static_views=len(groups), moving_shots=moving,
                moving_s=mov_s, shots=int(len(np.unique(sh))) if len(sh) else 0, cuts=cuts)


def _fixed_group(shots, n):
    """_static_shots -> the fixed camera: static shots clustered by pose; among the views that look down on the field
    from >= FIXED_MIN_Z, the one on air longest (a low corner camera can be locked off and on air longest in a clip:
    2026wagg qm30's red-corner camera at 1.9 m, 42 % -- the wide camera sits at 6.6 m).
    -> (group [(shot, idx, Pose)] or None, why-not or None, [view summaries])."""
    groups = []
    for sid, idx, p in shots:
        for g in groups:
            q = g[0][2]
            if np.linalg.norm(p.C - q.C) < 1.0 and rot_deg(p, q) < 2.0 and abs(p.f / q.f - 1) < 0.05:
                g.append((sid, idx, p)); break
        else:
            groups.append([(sid, idx, p)])
    n_of = lambda g: sum(len(x[1]) for x in g)
    views = [dict(on_air=round(n_of(g) / max(n, 1), 3), shots=len(g), C=np.round(g[0][2].C, 2).tolist(), f=round(float(g[0][2].f), 1))
             for g in sorted(groups, key=n_of, reverse=True)]
    high = [g for g in groups if g[0][2].C[2] >= FIXED_MIN_Z]
    if not high:
        zs = ", ".join(f"{g[0][2].C[2]:.1f}" for g in groups)
        return None, (f"the locked-off views are low (camera {zs} m above the carpet): the fixed tracking camera looks down "
                      f"from {FIXED_MIN_Z:.0f} m or more; calibrate by hand if one of them should be used"), views
    return max(high, key=n_of), None, views


def _scaled(b, W, H):
    return [int(round(b[0] * W / 1920)), int(round(b[1] * H / 1080)), int(round(b[2] * W / 1920)), int(round(b[3] * H / 1080))]


def auto_setup(video, t1=190.0, log=print, overlays=None, source_id=None):
    """Everything the tracker needs for a stream nobody calibrated, from one recorded clip of it (a match):

      overlays  broadcast graphics (auto_overlays) -> layout regions (scoreboard / mask) the tracker greys out
      camera    the FIXED wide camera, if the feed has one: the static, field-wide view that is on air the longest,
                re-fitted like the calibration wizard (tags of several frames, median; focal, k1..k3, principal
                point; refined on the white centre line) -> camera.json content ("which": "overhead")
      view_ref  that camera's median picture (BGR) -> view_<sid>.png (process(view_ref=...) / dynpose)
      field     the projected field box (+ robot height) above the score bar -> the layout's "field" region
      clock     the clock digits: of the known 2026 FIRST scoreboard on air (livestream.detect_geometry: top-centre
                or bottom bar), else the 1 Hz-ticking block (layout.suggest_clock on 10 s of 10 Hz frames)
      feed      program feed or one fixed camera (feed_summary)

    -> dict(camera, layout, view_ref, overlays, report, poses, p (the wizard parameter vector of the camera, for
    calibrate.overlay_lines), frame (a mid-clip BGR frame)).  camera / view_ref / p are None when no fixed view was
    found (report["fixed_why"] says why): process(dynamic=True) then still locates robots from every camera, with no
    fuel / shots.  live/sources.py job_auto_setup runs it on a recorded clip of a stream (Video page: Accept)."""
    import layout as LY
    import livestream as LS
    tt = time.time()
    dur, W, H = _duration(video)
    ts = np.linspace(0.3 * dur, 0.85 * dur, 16) if dur > 20 else np.linspace(0, max(dur - 0.5, 0), 6)
    frames = [f for _, f in LY.grab_frames(video, ts)]
    if overlays is None:
        overlays = auto_overlays(video, frames=frames)
    geo, geo_share = LS.detect_geometry(frames)
    log(f"  auto_setup: {len(overlays)} graphics boxes {overlays}; scoreboard "
        f"{geo.name + ' layout' if geo else 'not recognised'} ({geo_share:.0%} of frames)")
    pt = estimate(video, None, None, overlays, 0.0, min(t1, dur), log=log)
    feed = feed_summary(pt)
    report = dict(pose=pt.summary(), overlays=overlays, feed=feed, W=W, H=H, duration=round(dur, 1),
                  scoreboard=dict(layout=geo.name if geo else None, share=geo_share))
    log(f"  auto_setup: program feed {feed['program_feed']} ({feed['why']})")
    shots = _static_shots(pt)
    camd, plate, field, pfit, fixed_why = None, None, None, None, None
    if not shots and pt.n == 0:
        fixed_why = ("couldn't read the recording: no video frames could be decoded from it (a damaged or empty file, or "
                     "its clock doesn't start at 0)")
    elif not shots:
        n_tagf = int(np.sum(pt.kind == TAGS))
        fixed_why = ("no locked-off view of the whole field in this clip: the cameras pan / zoom, or show too few "
                     f"AprilTags ({n_tagf} of {pt.n} frames with tags)")
    if shots:
        g, fixed_why, report["static_views"] = _fixed_group(shots, pt.n)
        if g is None: shots = []
    if shots:
        on_air = sum(len(x[1]) for x in g) / max(pt.n, 1)
        idx = np.concatenate([x[1] for x in g])
        p0 = g[int(np.argmax([len(x[1]) for x in g]))][2]
        fts = pt.t[np.sort(idx)[np.linspace(0, len(idx) - 1, min(10, len(idx))).astype(int)]]
        ffr = [f for _, f in LY.grab_frames(video, fts)]
        grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in ffr]
        tags = CAL.detect_tags_frames(grays)
        tags = {k: v for k, v in tags.items() if not _in_masks(v["center"], overlays)}
        vec = np.r_[p0.f, 1.0, p0.cx, p0.cy, p0.D[0], 0.0, p0.rv, p0.C, 0.0]
        # robust (a tag that moved / isn't mounted is left out: calibrate.wizard_fit), as the Video page's wizard
        pfit, rep = CAL.wizard_fit(W, H, tags, None, None, p_init=vec, robust=True)
        out = lambda r: {d["id"] for d in r.get("dropped_tags") or []}
        if pfit is not None:                    # the wizard's second pass: tags near where the first fit puts them
            more = CAL.detect_tags_predicted(grays, pfit, W, H, have=set(tags))
            more = {k: v for k, v in more.items() if not _in_masks(v["center"], overlays)}
            if more:
                p2, rep2 = CAL.wizard_fit(W, H, {k: v for k, v in {**tags, **more}.items() if k not in out(rep)}, None, None,
                                          p_init=pfit, robust=True, dropped=rep.get("dropped_tags") or [])
                if p2 is not None and CAL.VERDICT_RANK[rep2["verdict"]] <= CAL.VERDICT_RANK[rep["verdict"]]:
                    pfit, rep, tags = p2, rep2, {**tags, **more}
        if pfit is None:
            fixed_why = (f"the fixed wide camera (on air {on_air:.0%} of the clip) shows {len(tags)} AprilTag"
                         f"{'' if len(tags) == 1 else 's'}: " + ("too few for a fit (it needs at least 2)" if rep.get("code") == "too_few"
                                                               else rep.get("error") or "the fit failed"))
            report["fixed_tags"] = len(tags)
        if pfit is not None:
            plate = np.median(np.stack(ffr), 0).astype(np.uint8)
            try:
                gplate = cv2.cvtColor(plate, cv2.COLOR_BGR2GRAY)
                p2, rep2 = CAL.refine_with_lines(pfit, gplate, W, H, {k: v for k, v in tags.items() if k not in out(rep)},
                                                 dropped=rep.get("dropped_tags") or [])
                if CAL.keep_refit(rep, rep2):
                    pfit, rep = p2, rep2
                else:
                    CAL.line_check(rep, pfit, gplate)
            except Exception as e:
                log(f"  auto_setup: centre-line refinement failed ({e!r})")
            camd = CAL.wizard_camera(pfit, W, H, event=None, which="overhead", model="wizard pinhole f,c,k1,k2,k3",
                                     source=source_id, calib=dict(method="dynpose auto_setup", tag_rms_px=rep.get("tag_rms_px"),
                                                                  line_med_px=rep.get("line_med_px"), n_tags=rep.get("n_tags"),
                                                                  on_air=round(float(on_air), 3), shots=len(g),
                                                                  warnings=rep.get("warnings"), verdict=rep.get("verdict"),
                                                                  dropped_tags=sorted(out(rep)), at=time.time()))
            camd.pop("event", None)
            (px0, py0, px1, py1) = _picture(W, H, overlays)
            x0, y0, x1, y1 = CAMM.Camera(camd).roi()
            field = [max(x0, px0), max(y0, py0), min(x1, px1), min(y1, py1)]
            camd["field_roi"] = field
            report["fixed"] = dict(on_air=round(float(on_air), 3), shots=len(g), tags=rep.get("n_tags"),
                                   tag_rms_px=rep.get("tag_rms_px"), line_med_px=rep.get("line_med_px"),
                                   f=rep.get("f"), c=rep.get("c"), C=rep.get("C"), k1=rep.get("k1"), warnings=rep.get("warnings"),
                                   verdict=rep.get("verdict"), verdict_reasons=rep.get("verdict_reasons"),
                                   dropped_tags=rep.get("dropped_tags"), centre_line_px=rep.get("centre_line_px"),
                                   items=[dict(id=i["id"], err_px=i["err_px"], dropped=bool(i.get("dropped")))
                                          for i in rep.get("items") or [] if i["kind"] == "tag"])
            log(f"  auto_setup: fixed camera on air {on_air:.0%} ({len(g)} shots), {len(tags)} tags, "
                f"RMS {rep.get('tag_rms_px')} px, line {rep.get('line_med_px')} px")
    if camd is None:
        fixed_why = fixed_why or "no fixed wide camera found"
        log(f"  auto_setup: {fixed_why} (moving views only: robots located, no fuel / shots)")
    # clock: the known scoreboard's digit box, else the 1 Hz-ticking block in 10 s of 10 Hz frames of the graphics
    clock = None
    if geo is not None:
        clock = dict(kind="clock", box=_scaled(geo.clock_box, W, H), label=f"Clock digits (2026 FIRST {geo.name} scoreboard)",
                     layout=geo.name)
    else:
        try:
            dense = LY.grab_dense(video, 0.5 * dur, seconds=10.0, hz=10.0)
            within = max(overlays, key=lambda b: (b[2] - b[0]) * (b[3] - b[1])) if overlays else None
            clock = LY.suggest_clock(dense, tuple(within) if within else None)
        except Exception as e:
            log(f"  auto_setup: clock search failed ({e!r})")
    sid = source_id or "auto"
    regions = [dict(id=f"a{i}", kind="scoreboard" if i == 0 else "mask", label="Graphics (auto)", box=list(map(int, b)))
               for i, b in enumerate(sorted(overlays, key=lambda b: -(b[2] - b[0]) * (b[3] - b[1])))]
    if clock: regions.append(dict(id="aclock", kind="clock", label=clock.get("label") or "Clock digits (auto)", box=clock["box"]))
    if field: regions.append(dict(id="afield", kind="field", label="Field area (auto)", box=list(map(int, field))))
    layout_doc = {"version": 1, "processing_source": sid,
                  "sources": {sid: dict(W=W, H=H, role="dynamic", regions=regions, updated_by="dynpose auto_setup",
                                        updated_at=time.time())}}
    report.update(clock=clock["box"] if clock else None, fixed_why=fixed_why, seconds=round(time.time() - tt, 1))
    mid = frames[len(frames) // 2] if frames else None
    return dict(camera=camd, layout=layout_doc, view_ref=plate, overlays=overlays, report=report, poses=pt,
                p=[float(x) for x in pfit] if camd is not None else None, frame=mid)


def _picture(W, H, overlays):
    """Frame minus full-width graphics bands at the top / bottom (= process.picture_box)."""
    x0, y0, x1, y1 = 0, 0, W, H
    for a, b, c, d in overlays or []:
        if c - a >= 0.6 * W and b <= 0.02 * H and d < 0.4 * H: y0 = max(y0, int(d))
        elif c - a >= 0.6 * W and d >= 0.98 * H and b > 0.6 * H: y1 = min(y1, int(b))
    return x0, y0, x1, y1


def write_setup(setup, out_dir, source_id="auto"):
    """auto_setup(...) -> files in out_dir: camera_<sid>.json, layout.json, view_<sid>.png, auto_setup.json (report).
    A candidate: copy / accept it into the event dir like a wizard calibration."""
    os.makedirs(out_dir, exist_ok=True)
    out = {}
    if setup["camera"] is not None:
        out["camera"] = os.path.join(out_dir, f"camera_{source_id}.json")
        json.dump(setup["camera"], open(out["camera"], "w"), indent=1)
    if setup["view_ref"] is not None:
        out["view_ref"] = os.path.join(out_dir, f"view_{source_id}.png")
        cv2.imwrite(out["view_ref"], setup["view_ref"])
    out["layout"] = os.path.join(out_dir, "layout.json")
    json.dump(setup["layout"], open(out["layout"], "w"), indent=1)
    out["report"] = os.path.join(out_dir, "auto_setup.json")
    json.dump(setup["report"], open(out["report"], "w"), indent=1, default=float)
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("video"); ap.add_argument("--ref"); ap.add_argument("--view-ref"); ap.add_argument("--layout")
    ap.add_argument("--t0", type=float, default=0.0); ap.add_argument("--t1", type=float, default=190.0)
    ap.add_argument("--out"); ap.add_argument("--report")
    ap.add_argument("--auto", metavar="DIR", help="auto set-up (no calibration): write camera / layout / view ref to DIR")
    g = ap.parse_args()
    if g.auto:
        su = auto_setup(g.video, t1=g.t1)
        print(json.dumps(write_setup(su, g.auto), indent=1)); print(json.dumps(su["report"], indent=1, default=float))
        sys.exit(0)
    ref = json.load(open(g.ref)) if g.ref else None
    masks = []
    if g.layout:
        import layout as LY
        L = LY.load(g.layout)
        masks = LY.overlay_boxes(L, ref["W"] if ref else 1920, ref["H"] if ref else 1080) if L else []
    pt = estimate(g.video, ref, g.view_ref, masks, g.t0, g.t1)
    if g.out: pt.save(g.out)
    if g.report: json.dump(pt.summary(), open(g.report, "w"), indent=1)
    print(json.dumps(pt.summary(), indent=1))
