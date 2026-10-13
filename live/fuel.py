"""Moving-FUEL detection and 2D tracking from the overhead broadcast camera.

    track_fuel(video_path, cam, t_from, t_to, log=print, frame_iter=None) -> FuelResult
    ft = FuelTracker(cam); ft.push(frame_idx, t, bgr) ...; ft.result() -> FuelResult
    python live/fuel.py VIDEO CAMERA.json T_FROM T_TO OUT.json [MATCH.json]

FuelResult (SPEC_V2.md + extras):
    {"fps", "t_from", "t_to",
     "tracks": [{"id", "f": [video frame idx], "t": [video s], "uv": [[u,v] full-res px],
                 "r": [px radius], "streak": [px blob length], "moving": true,
                 "airborne_score": 0..1, "view_ok": 0..1, "diag": {...}}],
     "floor": {"t", "zones", "zone_x", "counts_by_zone": {zone: [...]}, "heat": [[...]]},
     "stats": {frames, duplicates, dark, swapped, glitch, camera_moving, view_ok_frac, ...}}
  view_ok: fraction of the track's time the picture looked like the calibrated overhead
  view (bump colours).  When it is low (other camera / bumped mount) the 2D track is
  still valid but airborne_score (which uses the camera model) is not.

How it works (model-free; ~4-9 ms CPU per processed frame incl. decode):
* Broadcasts are 60 fps containers carrying 30p content (every 2nd frame is a re-encoded
  duplicate).  Duplicates are detected (% of pixels changed by >10 levels) and skipped;
  everything downstream works in seconds, so true 60p or 30 Hz input works the same.
* Yellowness at half resolution over the field box extruded 5 m up (shot apexes are far
  above the field in the image), broadcast overlay boxes masked.  Own decode reads the
  YUV planes directly (U/V are already half-res): yellow = strongly negative Cb with
  near-neutral Cr (orange/red has high Cr).  BGR input uses min(R,G)-B, same scale.
* Motion: yellowness above an asymmetric running background (falls in ~0.05 s, rises in
  ~0.8 s: a ball that came to rest is absorbed, a ball leaving is forgotten at once so
  the next ball of a stream on the same path is still new) AND new vs the previous
  frame or vs ~0.1 s ago (kills things that just stopped).  Connected components ->
  weighted centroid / 2nd moments; the shutter is fast (blob length ~ diameter), so
  oversized / elongated blobs are touching balls and are split by a small k-means.
* Frame gating: black (no-feed) frames, frames with a huge changed fraction (cuts,
  wipes) and a moving camera (phase correlation) reset the background; red<->blue
  swapped glitch frames are un-swapped (bump-colour test as process.swap_check).
* Scene cuts (stream cuts / wipes / inserted graphics / spliced uploads: a big frame
  difference or a luma-histogram jump vs ~0.1 s ago) skip the transition frames and reset
  every running statistic (duplicate threshold, background, live tracks; no stitching
  across a cut).  A run of "duplicates" while the picture keeps changing re-seeds the
  duplicate threshold, so it can never stay inflated (fuel blind) after a missed cut.
* Tracking: per-track quadratic prediction (last <=10 obs), acceleration-limited gates,
  birth links constrained by the blob's streak direction, Hungarian assignment,
  coasting <= 0.15 s; fragments are stitched (image extrapolation), split again at
  gaps the motion does not bridge (e.g. into the hub top / out of its base), and
  airborne fragments are merged when ONE 3D ballistic flight explains both (bridges an
  apex hidden behind the overlay without merging successive balls of a volley).
* airborne_score: per <=0.8 s window, noisy-OR of (a) a linear 3D fit: ballistic
  (gravity, small drag) explains the path much better than rolling on the carpet, (b)
  image-space parabola with gravity-like curvature for this spot, (c) implied ground
  speed if it were rolling > ~5 m/s, (d) ground projection leaving the field; shape
  evidence is down-weighted for short windows.  Track score = best window.
"""
import os, sys, json, time, math
import numpy as np, cv2

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import camera as CAMM

FUEL_D = 0.150
ZMAX = 5.0                      # m: highest a shot can be (ROI = field box up to this)
SCALE = 2                       # detection works at 1/SCALE resolution
NO_FEED_LEVEL = 20              # mean luma below this = feed lost (as process.py)
# broadcast overlay (FIRST 2026 graphics), full-res 1920x1080 px boxes (x0, y0, x1, y1)
OVERLAY_BOXES = [(446, 8, 1474, 134), (896, 126, 1024, 170),           # title/score bar, clock
                 (28, 60, 358, 118), (1562, 60, 1890, 118),             # alliance boxes + "<-"/"->" arrows
                 (616, 1004, 1304, 1072)]                              # event banner
# Other broadcasts: the event's layout.json (live/layout.py; box editor) replaces these -- track_fuel looks
# it up (scoreboard / clock / mask / inset boxes); no layout.json = the boxes above, unchanged.

TY = 60                         # min yellowness of a moving-fuel pixel (BGR scale ~ min(R,G)-B)
TD = 45                         # min yellowness increase over the lagged background
BG_TAU_UP = 0.8                 # s, background absorbs a new static yellow thing
BG_TAU_DOWN = 0.05              # s, background forgets yellow that left
GLITCH_FRAC = 0.03              # changed fraction of the ROI above this = glitch frame
# scene cuts / feed changes (livestream cuts, wipes, inserted graphics, spliced uploads).  Measured
# on clean wasam uploads: frame-to-frame changed % <= ~21 (camera bumps), luma-histogram shift
# <= 3.5 % even over 0.5 s; a cut to graphics / another camera / black shifts 20-80 %.
CUT_D = 35.0                    # % of sample pixels changed vs the last processed frame: hard cut
CUT_HIST = 10.0                 # % of the luma histogram mass moved vs ~CUT_LAG s ago: transition
CUT_LAG = 0.1                   # s (frames up to this long after a cut compare to the old scene -> skipped)
DUP_RUN_MAX = 4                 # this many "duplicates" in a row while the picture changes (d >= DUP_STUCK_D)
DUP_STUCK_D = 1.0               # = inflated duplicate threshold (30p-in-60p repeats a frame once): re-seed it

VMAX = 2400.0                   # px/s (full res), fastest plausible image speed of fuel
COAST_S = 0.15                  # established tracks survive this long unobserved
COAST_YOUNG_S = 0.045
MIN_DETS = 5                    # output tracks need this many detections ...
MIN_SPAN_S = 0.12               # ... spanning at least this long
MIN_DISP_PX = 24                # ... and moving at least this far (full-res px)
MAX_LOCAL_RMS = 3.5             # px: jitter above this = association noise, dropped


# ---------------------------------------------------------------- geometry helpers
def _field_box_hull(cam, zmax=ZMAX, pad=0.3):
    """Image hull of the volume fuel can occupy: the field up to zmax over most of it,
    tapering to 2.5 m at the near (camera-side) wall (points high up close to the
    camera fall outside the lens model's valid range and project wildly)."""
    L, W = CAMM.LEN, CAMM.WID
    pts = []
    for X in np.linspace(-pad, L + pad, 40):
        for Y in np.linspace(-pad, W + pad, 12):
            zt = zmax if Y < W - 2.5 else 2.5
            pts += [(X, Y, 0.0), (X, Y, zt)]
    p = cam.project(np.array(pts, float))
    ok = (np.isfinite(p).all(1) & (p[:, 0] > -0.5 * cam.W) & (p[:, 0] < 1.5 * cam.W)
          & (p[:, 1] > -0.5 * cam.H) & (p[:, 1] < 1.5 * cam.H))
    return cv2.convexHull(p[ok].astype(np.float32)).reshape(-1, 2)


def _ground_hull(cam, pad=0.15):
    L, W = CAMM.LEN, CAMM.WID
    pts = [(X, Y, 0.0) for X in np.linspace(-pad, L + pad, 40) for Y in (-pad, W + pad)]
    pts += [(X, Y, 0.0) for Y in np.linspace(-pad, W + pad, 20) for X in (-pad, L + pad)]
    p = cam.project(np.array(pts, float))
    return cv2.convexHull(p.astype(np.float32)).reshape(-1, 2)


def ball_radius_px(cam, P):
    """Pixel radius of a fuel ball centred at field point(s) P (Nx3)."""
    P = np.atleast_2d(np.asarray(P, float))
    d = P - cam.C[None]
    n = d / np.linalg.norm(d, axis=1, keepdims=True)
    # offset perpendicular to the viewing ray (horizontal-ish)
    side = np.cross(n, [0, 0, 1.0]); side /= np.maximum(np.linalg.norm(side, axis=1, keepdims=True), 1e-9)
    a = cam.project(P); b = cam.project(P + side * FUEL_D / 2)
    return np.linalg.norm(b - a, axis=1)


def gravity_px(cam, uv, z=1.5):
    """Image-space acceleration (px/s^2, +v = down) of a free-falling ball seen at
    pixel uv, assuming it is ~z m up (scale only)."""
    P = cam.img_to_plane(np.atleast_2d(uv), z)
    if not np.isfinite(P).all():
        return np.nan
    P3 = np.c_[P, np.full(len(P), z)]
    a = cam.project(P3 + [0, 0, 0.25]); b = cam.project(P3 - [0, 0, 0.25])
    return float(np.abs((b - a)[:, 1]).mean() / 0.5 * 9.81)


# ---------------------------------------------------------------- yellowness
def yellow_from_bgr(bgr):
    """uint8 yellowness: min(R,G) - B, minus a penalty for orange / lime (|R-G| > 30)."""
    b, g, r = cv2.split(bgr)
    mn = cv2.min(r, g)
    y = cv2.subtract(mn, b)
    pen = cv2.subtract(cv2.absdiff(r, g), 30)
    return cv2.subtract(y, pen)


def yellow_from_uv(U, V):
    """Same scale from the chroma planes: 2*(128-Cb) - 2*max(0, |Cr-140|-15)."""
    y = cv2.subtract(np.full(U.shape, 128, np.uint8), U)   # 128-Cb, saturating
    y = cv2.add(y, y)
    pen = cv2.subtract(cv2.absdiff(V, 140), 15)
    return cv2.subtract(y, cv2.add(pen, pen))


# ---------------------------------------------------------------- tracker
AMAX = 4000.0                   # px/s^2: acceleration allowance for young tracks


def _pfit(T, P, deg=2):
    """Least-squares polynomial (normal equations; T should be roughly centred or
    relative to an end point) for several columns of P at once -> coeffs (deg+1, k)
    for [1, T, T^2...]."""
    A = np.vander(T, deg + 1, increasing=True)
    try:
        return np.linalg.solve(A.T @ A, A.T @ P)
    except np.linalg.LinAlgError:
        return np.linalg.lstsq(A, P, rcond=None)[0]


def _qfit(T, P):
    """Quadratic fit of P(T): coeffs (3, k) for [1, T, T^2] and rms residual."""
    c = _pfit(T, P, 2)
    r = np.vander(T, 3, increasing=True) @ c - P
    return c, float(np.sqrt((r * r).sum(1).mean()))


class _Track:
    """Image-space track.  The motion model is refit on every update (cheap) so the
    per-frame prediction is just a polynomial evaluation."""
    __slots__ = ("id", "t", "f", "u", "v", "r", "a", "L", "th", "rms", "last_t", "c", "kind", "t_ref", "sp")

    def __init__(self, tid, t, f, d):
        self.id = tid
        self.t, self.f = [t], [f]
        self.u, self.v, self.r, self.a, self.L, self.th = [d[0]], [d[1]], [d[2]], [d[3]], [d[4]], [d[5]]
        self.rms, self.sp = 0.0, 0.0
        self.last_t = t
        self._refit()

    def n(self):
        return len(self.t)

    def _refit(self):
        n = len(self.t)
        self.t_ref = self.t[-1]
        if n == 1:
            self.kind, self.c = 0, None
            return
        k = min(n, 10)
        T = np.array(self.t[-k:]) - self.t_ref
        if k >= 5 and T[0] < -0.1:
            P = np.array((self.u[-k:], self.v[-k:])).T
            self.c, self.rms = _qfit(T, P)
            self.kind = 2
            self.sp = float(np.hypot(*self.c[1]))
            return
        d = T[-1] - T[-2]
        vu = (self.u[-1] - self.u[-2]) / d; vv = (self.v[-1] - self.v[-2]) / d
        self.c = np.array([[self.u[-1], self.v[-1]], [vu, vv], [0.0, 0.0]])
        self.kind, self.sp = 1, float(math.hypot(vu, vv))

    def predict(self, t):
        """Predicted (u, v) and gate radius (px) at time t."""
        tm = t - self.last_t                      # time since last observation
        if self.kind == 0:
            return self.u[-1], self.v[-1], VMAX * tm + 6.0
        tt = t - self.t_ref
        c = self.c
        pu = c[0, 0] + c[1, 0] * tt + c[2, 0] * tt * tt
        pv = c[0, 1] + c[1, 1] * tt + c[2, 1] * tt * tt
        if self.kind == 2:
            gate = 6.0 + 2.5 * self.rms + 0.08 * self.sp * tm + 0.5 * 1500.0 * tm * tm
        else:
            gate = 9.0 + 0.12 * self.sp * tm + 0.5 * AMAX * tm * tm
        return pu, pv, gate

    def add(self, t, f, d):
        self.t.append(t); self.f.append(f)
        self.u.append(d[0]); self.v.append(d[1]); self.r.append(d[2]); self.a.append(d[3])
        self.L.append(d[4]); self.th.append(d[5])
        self.last_t = t
        self._refit()


class FuelTracker:
    """Stateful moving-fuel tracker.  Feed every decoded frame (or any subset, e.g. the
    30 Hz frames process.py already decodes) in time order:

        ft = FuelTracker(cam, fps=60)
        for idx, t, bgr in frames: ft.push(idx, t, bgr)
        res = ft.result()
    """

    def __init__(self, cam, fps=60.0, log=print, scale=SCALE, overlay=OVERLAY_BOXES,
                 floor_every=2.0, t_from=None, t_to=None, overlay_px=None):
        self.cam, self.fps, self.log, self.S = cam, float(fps), log, int(scale)
        self.t_from, self.t_to = t_from, t_to
        S = self.S
        hull = _field_box_hull(cam)
        x0, y0 = np.floor(hull.min(0)).astype(int); x1, y1 = np.ceil(hull.max(0)).astype(int)
        x0, y0 = max(0, x0) // S * S, max(0, y0) // S * S
        x1 = min(cam.W, x1) // S * S; y1 = min(cam.H, y1) // S * S
        self.roi = (int(x0), int(y0), int(x1), int(y1))
        h, w = (y1 - y0) // S, (x1 - x0) // S
        m = np.zeros((h, w), np.uint8)
        cv2.fillPoly(m, [np.round((hull - [x0, y0]) / S).astype(np.int32)], 1)
        if overlay_px is not None:                    # layout boxes, already in camera pixels
            overlay, sx, sy = overlay_px, 1.0, 1.0
        else:                                         # 1920x1080 reference boxes (OVERLAY_BOXES)
            sx, sy = cam.W / 1920.0, cam.H / 1080.0
        for bx0, by0, bx1, by1 in (overlay or []):
            cv2.rectangle(m, (int((bx0 * sx - x0) / S), int((by0 * sy - y0) / S)),
                          (int((bx1 * sx - x0) / S), int((by1 * sy - y0) / S)), 0, -1)
        self.mask = m
        self.mask_px = int(m.sum())
        g = np.zeros((h, w), np.uint8)
        cv2.fillPoly(g, [np.round((_ground_hull(cam) - [x0, y0]) / S).astype(np.int32)], 1)
        self.ground_mask = g & m
        # expected radius of a ball on the floor, coarse map (half-res px)
        self._floor_r = None
        # swap check points (red / blue bumps)
        by = 6.6 if cam.C[1] >= CAMM.WID / 2 else CAMM.WID - 6.6        # bumps on the camera's side (process.bump_y)
        self.swap_pts = cam.project(np.array([[CAMM.LEN - 4.64, by, 0.1], [4.64, by, 0.1]])).astype(int)

        self.bg = None                # running background yellowness (float32)
        self.bg_t, self.bg_n = None, 0
        self.recent = []              # [(t, dilated yellowness)] of the last ~0.2 s
        self._k3 = np.ones((3, 3), np.uint8)
        self.prev_sample = None
        self.ema_diff = None
        self._dup_run = 0             # consecutive frames called duplicates
        self._hist_q = []             # [(t, luma histogram of the sample)] of the last ~CUT_LAG s
        self._in_cut = False          # inside a cut / transition (frames skipped)
        self._dark = False
        self.cuts = []                # [t] scene cuts (tracks are never joined across one)
        self.n_cut_frames = self.n_dup_reset = 0
        self.tracks, self.done = [], []
        self.next_id = 0
        self.n_in = self.n_proc = self.n_dup = self.n_dark = self.n_swap = self.n_glitch = 0
        self.n_dets = 0
        self.n_moving = 0
        self._win = None
        self._t_cur = 0.0
        self.view = []                # [(t, looks like the calibrated view)]
        self.t_det = self.t_trk = 0.0
        self.floor_every = floor_every
        self.next_floor = None
        self.floor = []

    # -------------------------------------------------------------- inputs
    def push(self, frame_idx, t, bgr):
        """One BGR frame (full-res HxWx3 uint8)."""
        self.n_in += 1
        self._t_cur = float(t)
        x0, y0, x1, y1 = self.roi
        crop = bgr[y0:y1, x0:x1]
        smp = crop[::8, ::8, 1]
        if not self._gate_frame(smp, float(smp.mean())):
            return
        if self._swapped_bgr(bgr):
            self.n_swap += 1
            crop = crop[:, :, ::-1]
        small = cv2.resize(crop, ((x1 - x0) // self.S, (y1 - y0) // self.S), interpolation=cv2.INTER_AREA)
        self._step(int(frame_idx), float(t), yellow_from_bgr(small))

    def push_av(self, frame_idx, t, frame):
        """One PyAV VideoFrame (fast path: yuv420p planes, no RGB conversion)."""
        if frame.format.name not in ("yuv420p", "yuvj420p") or self.S != 2:
            return self.push(frame_idx, t, frame.to_ndarray(format="bgr24"))
        self.n_in += 1
        self._t_cur = float(t)
        x0, y0, x1, y1 = self.roi
        W, H = frame.width, frame.height
        p0, p1, p2 = frame.planes
        Y = np.frombuffer(p0, np.uint8).reshape(H, p0.line_size)
        smp = Y[y0:y1:8, x0:x1:8]
        if not self._gate_frame(smp, float(smp.mean())):
            return
        U = np.frombuffer(p1, np.uint8).reshape(H // 2, p1.line_size)
        V = np.frombuffer(p2, np.uint8).reshape(H // 2, p2.line_size)
        if self._swapped_yuv(Y, U, V):
            self.n_swap += 1
            bgr = frame.to_ndarray(format="bgr24")[y0:y1, x0:x1, ::-1]
            small = cv2.resize(bgr, ((x1 - x0) // 2, (y1 - y0) // 2), interpolation=cv2.INTER_AREA)
            yel = yellow_from_bgr(small)
        else:
            yel = yellow_from_uv(U[y0 // 2:y1 // 2, x0 // 2:x1 // 2], V[y0 // 2:y1 // 2, x0 // 2:x1 // 2])
        self._step(int(frame_idx), float(t), yel)

    # -------------------------------------------------------------- frame gating
    def _scene_cut(self, t):
        """Cut / feed change at t: forget everything learned from the old picture (duplicate
        threshold, background, recent frames, live tracks) and start over on the new one."""
        if not self._in_cut:
            self.cuts.append(round(float(t), 3))
        self._in_cut = True
        self.ema_diff = None; self._dup_run = 0
        self.bg = None; self.recent = []
        for tr in self.tracks:
            self._finish(tr)
        self.tracks = []

    def _hist_jump(self, smp, t):
        """% of the sample's luma-histogram mass that moved vs ~CUT_LAG s ago."""
        h = np.bincount((smp >> 4).ravel(), minlength=16).astype(np.float32)
        h /= max(float(h.sum()), 1.0)
        q = self._hist_q
        q.append((t, h))
        while len(q) > 2 and q[1][0] <= t - CUT_LAG:
            q.pop(0)
        if q[0][0] > t - CUT_LAG or q[0][1].shape != h.shape:
            return 0.0
        return float(np.abs(h - q[0][1]).sum() * 50.0)

    def _gate_frame(self, smp, level):
        """False for black (no-feed) frames, cut / transition frames and duplicated frames."""
        t = self._t_cur
        if level <= NO_FEED_LEVEL:
            self.n_dark += 1
            if not self._dark:
                self._scene_cut(t)           # the feed may come back as something else
            self._dark = True
            self.bg = None; self.prev_sample = None; self._hist_q = []
            return False
        self._dark = False
        s = smp.astype(np.int16)
        if self._hist_jump(smp, t) >= CUT_HIST:
            # the picture's brightness distribution jumped (graphics, another camera, a wipe /
            # dissolve in progress, black): skip it, restart everything after it
            self.n_cut_frames += 1
            self._scene_cut(t)
            self.prev_sample = s
            return False
        has_prev = self.prev_sample is not None and self.prev_sample.shape == s.shape
        # % of sample pixels that changed by > 10 levels: re-encoded duplicates of a
        # 30p source in a 60p stream score ~0-0.4, real new frames ~1.3-3
        d = float((np.abs(s - self.prev_sample) > 10).mean() * 100) if has_prev else 0.0
        if d >= CUT_D and (self.ema_diff is None or d >= 6 * self.ema_diff):
            # hard cut with a similar histogram: never let it seed / inflate ema_diff
            # (an inflated duplicate threshold calls every real frame a duplicate)
            self.n_cut_frames += 1
            self._scene_cut(t)
            self.prev_sample = s
            return False
        self._in_cut = False
        if has_prev:
            ref = self.ema_diff if self.ema_diff is not None else max(d, 0.5)
            if d < 0.3 * ref:
                self._dup_run += 1
                if self._dup_run < DUP_RUN_MAX or d < DUP_STUCK_D:
                    self.n_dup += 1
                    return False             # same picture as the last processed one
                # many "duplicates" in a row although the picture changed: the threshold was
                # inflated (a transition the cut tests missed) -> re-seed it from this frame
                self.n_dup_reset += 1
                self.ema_diff = None
            self._dup_run = 0
            self.ema_diff = d if self.ema_diff is None else 0.9 * self.ema_diff + 0.1 * d
            # camera moving (a different / handheld camera, a bumped mount): everything
            # changes -> no fuel detection on this frame, restart the background
            if self._win is None or self._win.shape != s.shape:
                self._win = cv2.createHanningWindow((s.shape[1], s.shape[0]), cv2.CV_32F)
            (dx, dy), resp = cv2.phaseCorrelate(self.prev_sample.astype(np.float32), s.astype(np.float32), self._win)
            if math.hypot(dx, dy) > 0.3 and resp > 0.05:
                self.n_moving += 1
                self.bg = None
                self.prev_sample = s
                return False
        self.prev_sample = s
        return True

    def _view_check(self, ycc):
        """ycc: [(Y, Cr, Cb)] median at the red and the blue bump.  Records whether the
        picture looks like the calibrated overhead view (bumps read red / blue, or both
        swapped); robots parked on a bump make single frames unreliable, so this is
        smoothed over +-2 s in result()."""
        (_, cr_r, cb_r), (_, cr_b, cb_b) = ycc
        normal = cr_r > 140 and cb_b > 145
        swapped = cb_r > 150 and cr_b > 140
        self.view.append((self._t_cur, bool(normal or swapped)))
        return swapped

    def _swapped_bgr(self, im):
        """As process.swap_check: the red bump reads blue and the blue bump reads red."""
        ps = []
        for u, v in self.swap_pts:
            if not (5 <= u < im.shape[1] - 5 and 5 <= v < im.shape[0] - 5):
                self.view.append((self._t_cur, False))
                return False
            p = cv2.cvtColor(np.ascontiguousarray(im[v - 5:v + 5, u - 5:u + 5]), cv2.COLOR_BGR2YCrCb)
            ps.append(np.median(p.reshape(-1, 3), 0).astype(float))
        return self._view_check(ps)

    def _swapped_yuv(self, Y, U, V):
        ps = []
        for u, v in self.swap_pts:
            if not (6 <= u < self.cam.W - 6 and 6 <= v < self.cam.H - 6):
                self.view.append((self._t_cur, False))
                return False
            u2, v2 = u // 2, v // 2
            ps.append((0.0, float(np.median(V[v2 - 3:v2 + 2, u2 - 3:u2 + 2])),
                       float(np.median(U[v2 - 3:v2 + 2, u2 - 3:u2 + 2]))))
        return self._view_check(ps)

    # -------------------------------------------------------------- per frame
    def _step(self, fidx, t, yel):
        self.n_proc += 1
        t0 = time.perf_counter()
        yel[self.mask == 0] = 0
        if self.floor_every and (self.next_floor is None or t >= self.next_floor):
            self.next_floor = t + self.floor_every
            self._floor(t, yel)
        Y = yel.astype(np.float32)
        if self.bg is None:
            self.bg, self.bg_t, self.bg_n = Y, t, 0
            self.recent = []
            return
        # background yellowness: falls fast (a ball that leaves is forgotten in ~0.1 s,
        # so the next ball of a stream on the same path is still "new"), rises slowly
        # (a ball that comes to rest is absorbed in ~1 s; one passing never is)
        dt = max(t - self.bg_t, 1e-3)
        a_up, a_dn = 1 - math.exp(-dt / BG_TAU_UP), 1 - math.exp(-dt / BG_TAU_DOWN)
        Bd = cv2.dilate(self.bg.astype(np.uint8), self._k3)
        d = cv2.subtract(Y, self.bg)
        self.bg = cv2.scaleAdd(cv2.max(d, 0.0), a_up, self.bg)
        self.bg = cv2.scaleAdd(cv2.min(d, 0.0), a_dn, self.bg)
        self.bg_t = t; self.bg_n += 1
        # recent frames: something that just came to rest is new vs the slow background
        # but not vs the last frames -> require novelty vs the previous processed frame
        # OR vs ~0.1 s ago (the OR keeps slow movers and balls in a stream)
        Dn = cv2.dilate(yel, self._k3)
        recent = self.recent
        D1 = recent[-1][1] if recent else None
        D3 = None
        for tb, Db in recent:
            if tb <= t - 0.09:
                D3 = Db
        recent.append((t, Dn))
        while len(recent) > 1 and recent[0][0] < t - 0.2:
            recent.pop(0)
        if self.bg_n < 3 or D1 is None:
            return
        Rm = cv2.min(D1, D3) if D3 is not None else D1
        diff = cv2.subtract(yel, Bd)
        m = cv2.compare(diff, TD, cv2.CMP_GE)
        m = cv2.bitwise_and(m, cv2.compare(yel, TY, cv2.CMP_GE))
        m = cv2.bitwise_and(m, cv2.compare(cv2.subtract(yel, Rm), TD, cv2.CMP_GE))
        m = cv2.bitwise_and(m, 1)
        nz = cv2.countNonZero(m)
        if nz > GLITCH_FRAC * self.mask_px:
            self.n_glitch += 1
            self.bg, self.bg_n = Y, 0                  # cut / wipe: restart the background
            self.recent = []
            return
        dets = self._blobs(m, diff) if nz else np.zeros((0, 6))
        self.n_dets += len(dets)
        t1 = time.perf_counter(); self.t_det += t1 - t0
        self._track(fidx, t, dets)
        self.t_trk += time.perf_counter() - t1

    def _blobs(self, m, diff):
        """Connected components -> [u, v, r, area, streak_len, streak_angle] (full-res px)."""
        n, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
        if n <= 1:
            return np.zeros((0, 6))
        nzp = cv2.findNonZero(m).reshape(-1, 2)
        xs, ys = nzp[:, 0], nzp[:, 1]
        l = lab[ys, xs]
        w = diff[ys, xs].astype(np.float64)
        sw = np.bincount(l, w, n)
        sx = np.bincount(l, w * xs, n); sy = np.bincount(l, w * ys, n)
        cx, cy = sx / np.maximum(sw, 1e-9), sy / np.maximum(sw, 1e-9)
        dx, dy = xs - cx[l], ys - cy[l]
        cxx = np.bincount(l, w * dx * dx, n) / np.maximum(sw, 1e-9)
        cyy = np.bincount(l, w * dy * dy, n) / np.maximum(sw, 1e-9)
        cxy = np.bincount(l, w * dx * dy, n) / np.maximum(sw, 1e-9)
        tr, det = cxx + cyy, cxx * cyy - cxy * cxy
        disc = np.sqrt(np.maximum(tr * tr / 4 - det, 0))
        lmax, lmin = tr / 2 + disc, np.maximum(tr / 2 - disc, 0)
        area = st[:, cv2.CC_STAT_AREA].astype(float)
        S = self.S
        keep = np.arange(1, n)
        keep = keep[(area[keep] >= 2) & (area[keep] <= 600)]
        x0, y0 = self.roi[:2]
        # radius across the streak: uniform disc var = r^2/4; +0.5 px for pixelisation
        r = (2 * np.sqrt(lmin[keep]) + 0.5) * S
        L = 2 * np.sqrt(3 * lmax[keep]) * S          # streak length (uniform bar of length L)
        u = x0 + (cx[keep] + 0.5) * S - 0.5
        v = y0 + (cy[keep] + 0.5) * S - 0.5
        th = 0.5 * np.arctan2(2 * cxy[keep], cxx[keep] - cyy[keep])   # streak direction
        out = np.column_stack((u, v, r, area[keep] * S * S, L, th))
        # merged balls (a volley in contact, a stream bunching up before the hub): the
        # shutter is fast (streak length ~ diameter), so an elongated / oversized blob is
        # several balls -> split it with a small weighted k-means along its major axis
        rh = 2 * np.sqrt(lmin[keep]) + 0.5
        k_est = np.round(area[keep] / (0.85 * np.pi * rh ** 2)).astype(int)
        elong = lmax[keep] / np.maximum(lmin[keep], 1e-6)
        split = np.where((area[keep] >= 10) & (k_est >= 2) & ((elong > 2.2) | (k_est >= 3)))[0]
        if len(split) == 0:
            return out
        order = np.argsort(l, kind="stable")
        bounds = np.searchsorted(l[order], np.arange(n + 1))
        extra, drop = [], []
        for q in split:
            lb = keep[q]
            idx = order[bounds[lb]:bounds[lb + 1]]
            P = np.column_stack((xs[idx], ys[idx])).astype(float); wq = w[idx]
            k = int(min(k_est[q], 6, max(2, len(idx) // 4)))
            dirv = np.array([math.cos(th[q]), math.sin(th[q])])
            half = math.sqrt(3 * lmax[lb])
            C = np.array([cx[lb], cy[lb]]) + np.outer(np.linspace(-half, half, k) * (1 - 1.0 / k), dirv)
            for _ in range(5):
                lab_k = np.argmin(((P[:, None, :] - C[None]) ** 2).sum(2), 1)
                sw_ = np.bincount(lab_k, wq, k)
                ok_ = sw_ > 0
                C[ok_, 0] = np.bincount(lab_k, wq * P[:, 0], k)[ok_] / sw_[ok_]
                C[ok_, 1] = np.bincount(lab_k, wq * P[:, 1], k)[ok_] / sw_[ok_]
            cnt = np.bincount(lab_k, minlength=k)
            if (cnt < 3).any():
                continue
            drop.append(q)
            for j in range(k):
                extra.append([x0 + (C[j, 0] + 0.5) * S - 0.5, y0 + (C[j, 1] + 0.5) * S - 0.5,
                              rh[q] * S, cnt[j] * S * S, 2 * rh[q] * S, th[q]])
        if drop:
            out = np.vstack([np.delete(out, drop, 0), np.array(extra)])
        return out

    # -------------------------------------------------------------- tracking
    def _track(self, fidx, t, dets):
        from scipy.optimize import linear_sum_assignment
        live = self.tracks
        nt, nd = len(live), len(dets)
        matched_t, matched_d = set(), set()
        if nt and nd:
            P = np.array([tr.predict(t) for tr in live])               # (nt, 3)
            n_obs = np.array([tr.n() for tr in live])
            du = dets[None, :, 0] - P[:, 0:1]; dv = dets[None, :, 1] - P[:, 1:2]
            g2 = (P[:, 2:3]) ** 2
            C = (du * du + dv * dv) / g2
            # birth links (track seen once): the displacement must run along the
            # motion streak of either blob when that blob is clearly elongated
            young = np.where(n_obs == 1)[0]
            if len(young):
                ang = np.arctan2(dv[young], du[young])
                for blob_th, blob_el in ((np.array([live[i].th[-1] for i in young])[:, None],
                                          np.array([live[i].L[-1] > 2.6 * live[i].r[-1] for i in young])[:, None]),
                                         (dets[None, :, 5], (dets[:, 4] > 2.6 * dets[:, 2])[None, :])):
                    dth = np.abs(((ang - blob_th) + np.pi / 2) % np.pi - np.pi / 2)
                    C[young] = np.where(blob_el & (dth > 0.5) & (C[young] <= 1), 1e6, C[young])
                C[young] = np.where(C[young] <= 1, 0.6 + 0.4 * C[young], C[young])
            mid = np.where((n_obs > 1) & (n_obs < 5))[0]
            C[mid] = np.where(C[mid] <= 1, 0.2 + 0.8 * C[mid], C[mid])
            C[C > 1] = 1e6
            rows, cols = linear_sum_assignment(C)
            for i, j in zip(rows, cols):
                if C[i, j] < 1e5:
                    live[i].add(t, fidx, dets[j]); matched_t.add(i); matched_d.add(j)
        keep = []
        for i, tr in enumerate(live):
            if i in matched_t:
                keep.append(tr); continue
            lim = COAST_S if tr.n() >= 5 else COAST_YOUNG_S
            if t - tr.last_t > lim:
                self._finish(tr)
            else:
                keep.append(tr)
        for j in range(nd):
            if j not in matched_d:
                keep.append(_Track(self.next_id, t, fidx, dets[j])); self.next_id += 1
        self.tracks = keep

    def _finish(self, tr):
        if tr.n() >= 3:
            self.done.append(tr)

    # -------------------------------------------------------------- floor fuel
    def _floor(self, t, yel):
        """Static yellow blobs on the carpet -> ball counts per zone (coarse)."""
        m = ((yel >= 110) & (self.ground_mask > 0)).astype(np.uint8)
        n, lab, st, cen = cv2.connectedComponentsWithStats(m, connectivity=8)
        if n <= 1:
            self.floor.append((t, np.zeros((0, 2)), np.zeros(0))); return
        a = st[1:, cv2.CC_STAT_AREA].astype(float)
        c = cen[1:]
        ok = a >= 3
        a, c = a[ok], c[ok]
        x0, y0 = self.roi[:2]
        uv = np.c_[x0 + (c[:, 0] + 0.5) * self.S, y0 + (c[:, 1] + 0.5) * self.S]
        G = self.cam.img_to_plane(uv, FUEL_D / 2)
        good = np.isfinite(G).all(1)
        G, a, uv = G[good], a[good], uv[good]
        if len(G) == 0:
            self.floor.append((t, np.zeros((0, 2)), np.zeros(0))); return
        rp = ball_radius_px(self.cam, np.c_[G, np.full(len(G), FUEL_D / 2)]) / self.S
        # visible area of one ball ~ pi r^2 * 0.8 (threshold eats the shaded rim)
        cnt = np.maximum(1, np.round(a / (0.8 * np.pi * rp ** 2)))
        self.floor.append((t, G, cnt))

    # -------------------------------------------------------------- output
    def result(self):
        for tr in self.tracks:
            self._finish(tr)
        self.tracks = []
        tracks = stitch(self.done, cuts=self.cuts)
        out = []
        M = _Models(self.cam)
        for tr in tracks:
            out += finalize_track(tr, self.cam, M)
        out = merge_ballistic(out, M, cuts=self.cuts)
        out.sort(key=lambda d: d["t"][0])
        # does the picture match the calibrated view? (bump colours, smoothed +-2 s)
        if self.view:
            vt = np.array([a for a, _ in self.view]); vo = np.array([b for _, b in self.view], float)
            cs = np.r_[0, np.cumsum(vo)]
            lo = np.searchsorted(vt, vt - 2.0); hi = np.searchsorted(vt, vt + 2.0, side="right")
            vs = (cs[hi] - cs[lo]) > 0
        else:
            vt, vs = np.zeros(0), np.zeros(0, bool)
        for k, d in enumerate(out):
            d["id"] = k
            if len(vt):
                j = np.clip(np.searchsorted(vt, d["t"]), 0, len(vt) - 1)
                d["view_ok"] = round(float(vs[j].mean()), 2)
        res = {"fps": self.fps, "t_from": self.t_from, "t_to": self.t_to,
               "tracks": out, "floor": self._floor_summary(),
               "stats": {"frames_in": self.n_in, "frames_processed": self.n_proc,
                         "duplicates": self.n_dup, "dark": self.n_dark, "swapped": self.n_swap,
                         "glitch": self.n_glitch, "camera_moving": self.n_moving,
                         "cuts": len(self.cuts), "cut_t": self.cuts[:50], "cut_frames": self.n_cut_frames,
                         "dup_resets": self.n_dup_reset,
                         "view_ok_frac": round(float(vs.mean()), 3) if len(vs) else None,
                         "detections": self.n_dets,
                         "raw_tracks": len(self.done), "tracks": len(out),
                         "airborne_tracks": int(sum(d["airborne_score"] >= 0.5 for d in out)),
                         "t_detect_s": round(self.t_det, 2), "t_track_s": round(self.t_trk, 2),
                         "roi": list(self.roi)}}
        return res

    def _floor_summary(self):
        if not self.floor:
            return None
        L, W = CAMM.LEN, CAMM.WID
        zones = ["blue_alliance", "neutral", "red_alliance"]
        xb, xr = 4.028, 12.513
        ts, cz, heat = [], {z: [] for z in zones}, np.zeros((int(math.ceil(W)), int(math.ceil(L))))
        for t, G, cnt in self.floor:
            ts.append(round(t, 2))
            x = G[:, 0] if len(G) else np.zeros(0)
            inside = (x > -0.2) & (x < L + 0.2) & ((G[:, 1] > -0.2) & (G[:, 1] < W + 0.2) if len(G) else np.zeros(0, bool))
            cz["blue_alliance"].append(int(cnt[inside & (x < xb)].sum()))
            cz["red_alliance"].append(int(cnt[inside & (x > xr)].sum()))
            cz["neutral"].append(int(cnt[inside & (x >= xb) & (x <= xr)].sum()))
            if inside.any():
                gx = np.clip(G[inside, 0].astype(int), 0, heat.shape[1] - 1)
                gy = np.clip(G[inside, 1].astype(int), 0, heat.shape[0] - 1)
                np.add.at(heat, (gy, gx), cnt[inside])
        heat /= max(len(self.floor), 1)
        return {"t": ts, "zones": zones, "zone_x": [xb, xr], "counts_by_zone": cz,
                "heat": np.round(heat, 1).tolist(), "heat_cell_m": 1.0,
                "note": "mean visible floor balls per 1 m cell (rows = Y, cols = X); piles are undercounted"}


# ---------------------------------------------------------------- post-processing
def _arrays(tr):
    return (np.array(tr.t), np.array(tr.f), np.array(tr.u), np.array(tr.v),
            np.array(tr.r), np.array(tr.a), np.array(tr.L), np.array(tr.th))


def _cut_between(cuts, t_end, t_start):
    """Is there a scene cut c with t_end < c <= t_start (cuts sorted)?"""
    if not len(cuts):
        return False
    i = int(np.searchsorted(cuts, t_end, side="right"))
    return i < len(cuts) and cuts[i] <= t_start


def stitch(tracks, max_gap=0.25, cuts=()):
    """Join fragments A -> B when B starts shortly after A ends and each one's
    quadratic extrapolation lands on the other (never across a scene cut)."""
    trs = sorted(tracks, key=lambda tr: tr.t[0])
    by_start = sorted(range(len(trs)), key=lambda i: trs[i].t[0])
    starts = np.array([trs[i].t[0] for i in by_start])
    # greedy: for each track (by end time), find the best successor
    order = sorted(range(len(trs)), key=lambda i: trs[i].t[-1])
    succ = {}
    taken = set()
    for i in order:
        A = trs[i]
        if A.n() < 4:
            continue
        lo = np.searchsorted(starts, A.t[-1] + 1e-6)
        hi = np.searchsorted(starts, A.t[-1] + max_gap)
        best, bc = None, 1.0
        for k in range(lo, hi):
            j = by_start[k]
            if j == i or j in taken:
                continue
            B = trs[j]
            if B.n() < 3:
                continue
            gap = B.t[0] - A.t[-1]
            if _cut_between(cuts, A.t[-1], B.t[0]):
                continue
            if math.hypot(B.u[0] - A.u[-1], B.v[0] - A.v[-1]) > 20 + (A.sp + 300) * gap:
                continue
            c = _join_cost(A, B)
            if c < bc:
                best, bc = j, c
        if best is not None:
            succ[i] = best; taken.add(best)
    out = []
    for i in range(len(trs)):
        if i in taken:
            continue
        tr = trs[i]
        j = succ.get(i)
        while j is not None:
            B = trs[j]
            for q in range(B.n()):
                tr.add(B.t[q], B.f[q], (B.u[q], B.v[q], B.r[q], B.a[q], B.L[q], B.th[q]))
            j = succ.get(j)
        out.append(tr)
    return out


def _fit_eval(T, U, V, tq, deg=2):
    deg = min(deg, len(T) - 1)
    c = _pfit(np.asarray(T, float), np.column_stack((U, V)), deg)
    q = np.vander(np.atleast_1d(np.asarray(tq, float)), deg + 1, increasing=True) @ c
    return q[:, 0], q[:, 1]


def _join_cost(A, B):
    ka, kb = min(A.n(), 10), min(B.n(), 10)
    Ta = np.array(A.t[-ka:]); Ua = np.array(A.u[-ka:]); Va = np.array(A.v[-ka:])
    Tb = np.array(B.t[:kb]); Ub = np.array(B.u[:kb]); Vb = np.array(B.v[:kb])
    gap = Tb[0] - Ta[-1]
    pu, pv = _fit_eval(Ta - Ta[-1], Ua, Va, Tb[:2] - Ta[-1], 2 if ka >= 5 else 1)
    e1 = np.hypot(pu - Ub[:2], pv - Vb[:2]).max()
    qu, qv = _fit_eval(Tb - Tb[0], Ub, Vb, Ta[-2:] - Tb[0], 2 if kb >= 5 else 1)
    e2 = np.hypot(qu - Ua[-2:], qv - Va[-2:]).max()
    sp = np.hypot(np.diff(Ua[-2:]), np.diff(Va[-2:])).max() / max(Ta[-1] - Ta[-2], 1e-3)
    gate = 8 + 0.08 * sp * gap + 750 * gap * gap
    c = max(e1, e2) / gate
    return c if c < 1 else 1.0


def _local_rms(T, U, V, win=9):
    """Median rms residual of quadratic fits over sliding windows (jitter measure)."""
    n = len(T)
    if n < 5:
        return float("nan")
    win = min(win, n)
    rs = []
    for s in range(0, n - win + 1, max(1, win // 2)):
        tt = T[s:s + win] - T[s]
        pu, pv = _fit_eval(tt, U[s:s + win], V[s:s + win], tt, 2)
        rs.append(np.sqrt(np.mean((pu - U[s:s + win]) ** 2 + (pv - V[s:s + win]) ** 2)))
    return float(np.median(rs))


DRAG_SIGMA = 1.5                # m/s^2, prior on the horizontal (drag) acceleration


class _Models:
    """Linear (DLT-style) fits of two motion hypotheses to an image track:
    ballistic  X(t) = p0 + v0 t + 1/2 (ax, ay, -g) t^2   (free horizontal accel ~ drag)
    rolling    X(t) = (a + b t + c t^2, r_ball)            (on the carpet)
    Each observation gives two equations linear in the unknowns (normalised image
    coordinates x X_z - X_x = 0 ...), reweighted once by 1/depth -> geometric error."""
    G = 9.81

    def __init__(self, cam):
        self.cam = cam
        self.R, self.tv = cam.R, cam.tvec

    def norm(self, uv):
        return cv2.undistortPoints(np.asarray(uv, float).reshape(-1, 1, 2), self.cam.K, self.cam.D).reshape(-1, 2)

    def _solve(self, T, xn, basis, k, prior=None):
        """basis(T) -> (n, 3, m) design, k(T) -> (n, 3) known part.  prior: optional
        {param index: sigma} pulling those parameters towards 0 (sigma in their units
        costs about one pixel of residual)."""
        R, tv = self.R, self.tv
        Ab, kk = basis(T), k(T)
        r1, r2, r3 = R
        w = np.ones(len(T))
        for _ in range(2):
            rows, rhs = [], []
            for (r, j, tj) in ((r1, 0, tv[0]), (r2, 1, tv[1])):
                coef = r[None, :] - xn[:, j:j + 1] * r3[None, :]          # (n, 3)
                rows.append(np.einsum("nc,ncm->nm", coef, Ab) * w[:, None])
                rhs.append((xn[:, j] * tv[2] - tj - (coef * kk).sum(1)) * w)
            A = np.vstack(rows); y = np.concatenate(rhs)
            if prior:
                f = self.cam.K[0, 0]
                P = np.zeros((len(prior), A.shape[1]))
                for q, (idx, sg) in enumerate(prior.items()):
                    P[q, idx] = 1.0 / (sg * f)
                A = np.vstack([A, P]); y = np.r_[y, np.zeros(len(prior))]
            th, *_ = np.linalg.lstsq(A, y, rcond=None)
            X = np.einsum("ncm,m->nc", Ab, th) + kk
            depth = X @ r3 + tv[2]
            if not (depth > 0.3).all():
                return th, X, False
            w = 1.0 / depth
        return th, X, True

    def ballistic(self, T, xn):
        def basis(T):
            n = len(T); B = np.zeros((n, 3, 8))
            for c in range(3):
                B[:, c, c] = 1; B[:, c, 3 + c] = T
            B[:, 0, 6] = 0.5 * T * T; B[:, 1, 7] = 0.5 * T * T
            return B
        k = lambda T: np.c_[np.zeros(len(T)), np.zeros(len(T)), -0.5 * self.G * T * T]
        return self._solve(T, xn, basis, k, prior={6: DRAG_SIGMA, 7: DRAG_SIGMA})

    def rolling(self, T, xn):
        def basis(T):
            n = len(T); B = np.zeros((n, 3, 6))
            for c in range(2):
                B[:, c, c] = 1; B[:, c, 2 + c] = T; B[:, c, 4 + c] = T * T
            return B
        k = lambda T: np.c_[np.zeros(len(T)), np.zeros(len(T)), np.full(len(T), FUEL_D / 2)]
        return self._solve(T, xn, basis, k)

    def rms(self, X, uv):
        p = self.cam.project(X)
        return float(np.sqrt(np.mean(np.sum((p - uv) ** 2, 1))))


def _window_evidence(M, T, uv, xn):
    """Airborne evidence in [0,1] for one window plus diagnostics."""
    L, W = CAMM.LEN, CAMM.WID
    tb, Xb, okb = M.ballistic(T - T[0], xn)
    tr, Xr, okr = M.rolling(T - T[0], xn)
    rb = M.rms(Xb, uv) if okb else 1e3
    rr = M.rms(Xr, uv) if okr else 1e3
    # physical plausibility of the ballistic solution
    v0 = float(np.linalg.norm(tb[3:6])) if okb else 1e3
    plaus = (okb and Xb[:, 2].min() > -0.4 and Xb[:, 2].max() < 7.0 and v0 < 25
             and (Xb[:, 0] > -1).all() and (Xb[:, 0] < L + 1).all() and (Xb[:, 1] > -1).all() and (Xb[:, 1] < W + 1).all()
             and abs(tb[6]) < 6 and abs(tb[7]) < 6)
    # rolling hypothesis implausible: too fast for fuel on the carpet / off the field
    if okr:
        vr = np.linalg.norm(np.diff(Xr[:, :2], axis=0), axis=1) / np.maximum(np.diff(T), 1e-3)
        vmax_r = float(np.percentile(vr, 90)) if len(vr) else 0.0
        off = float(np.mean((Xr[:, 0] < -0.3) | (Xr[:, 0] > L + 0.3) | (Xr[:, 1] < -0.3) | (Xr[:, 1] > W + 0.3)))
    else:
        vmax_r, off = 99.0, 1.0
    sig = 1.0
    e_fit = 0.0
    if plaus:
        ratio = rr / max(rb, sig)
        e_fit = float(np.clip((ratio - 1.5) / 2.5, 0, 1))
        if rb > 4.0:                       # neither explains it: unreliable
            e_fit *= 0.4
    e_fast = float(np.clip((vmax_r - 5.0) / 4.0, 0, 1)) * (1.0 if (plaus or vmax_r > 10) else 0.6)
    e_off = off * (1.0 if plaus else 0.5)
    # image-space parabola with a gravity-like downward acceleration (scale from the
    # camera at this spot): robust to the depth ambiguity of short 3D fits
    e_grav, ratio_g = 0.0, None
    n, dur = len(T), float(T[-1] - T[0])
    if n >= 6 and dur >= 0.15:
        Tt = T - T[0]
        c = _pfit(Tt, uv, 2)
        res = float(np.sqrt(np.mean(np.sum((np.vander(Tt, 3, increasing=True) @ c - uv) ** 2, 1))))
        av = 2 * c[2, 1]
        grav = gravity_px(M.cam, np.median(uv, 0))
        if np.isfinite(grav) and grav > 0:
            ratio_g = av / grav
            curv = 0.125 * abs(av) * dur ** 2          # sag of the parabola over the window, px
            t_apex = -c[1, 1] / (2 * c[2, 1]) if c[2, 1] != 0 else -1
            apex = c[2, 1] > 0 and 0.05 < t_apex < dur - 0.05
            if 0.4 < ratio_g < 2.5 and curv > 3 * max(res, 1.0) and res < 4:
                e_grav = 0.85 if apex else 0.6
    cs = min(1.0, n / 10.0) * min(1.0, dur / 0.3)       # confidence in shape evidence
    cv = min(1.0, n / 5.0) * min(1.0, dur / 0.1)        # ... in speed / position evidence
    e = 1 - (1 - e_fit * cs) * (1 - e_grav * cs) * (1 - 0.9 * e_fast * cv) * (1 - 0.6 * e_off * cv)
    return e, dict(g_ratio=None if ratio_g is None else round(float(ratio_g), 2),rms_ballistic=round(rb, 2), rms_rolling=round(rr, 2), plausible=bool(plaus),
                   zmax=round(float(Xb[:, 2].max()), 2) if okb else None,
                   v0=round(v0, 1) if okb else None, roll_speed=round(vmax_r, 1))


def split_track(T, U, V):
    """Split at gaps that the motion does not bridge cleanly (e.g. a shot entering
    the hub top, then a ball leaving its base; a ball occluded then bounced)."""
    cuts = [0]
    for i in range(1, len(T)):
        gap = T[i] - T[i - 1]
        if gap < 0.055:
            continue
        a0 = max(cuts[-1], i - 8)
        b1 = min(len(T), i + 8)
        if i - a0 < 3 or b1 - i < 3:
            if gap > 0.12:
                cuts.append(i)
            continue
        Ta, Tb = T[a0:i], T[i:b1]
        deg_a = 2 if len(Ta) >= 5 else 1
        deg_b = 2 if len(Tb) >= 5 else 1
        pu, pv = _fit_eval(Ta - Ta[-1], U[a0:i], V[a0:i], Tb[:2] - Ta[-1], deg_a)
        qu, qv = _fit_eval(Tb - Tb[0], U[i:b1], V[i:b1], Ta[-2:] - Tb[0], deg_b)
        e = max(np.hypot(pu - U[i:i + 2], pv - V[i:i + 2]).max(), np.hypot(qu - U[i - 2:i], qv - V[i - 2:i]).max())
        sp = math.hypot(U[i - 1] - U[i - 2], V[i - 1] - V[i - 2]) / max(T[i - 1] - T[i - 2], 1e-3)
        if e > 7 + 0.06 * sp * gap + 750 * gap * gap:
            cuts.append(i)
    cuts.append(len(T))
    return [(cuts[k], cuts[k + 1]) for k in range(len(cuts) - 1)]


def merge_ballistic(out, M, max_gap=0.6, cuts=()):
    """Second stitching pass with the 3D ballistic model: join an airborne fragment to
    a later one when ONE free flight explains both (bridges the apex hidden behind the
    broadcast overlay, the net, a robot).  A following ball of the same volley flies
    the same path ~0.1-0.2 s later, which a single time-parameterised flight cannot
    fit, so volleys are not merged into one track."""
    cand = [d for d in out if d["airborne_score"] >= 0.3]
    if len(cand) < 2:
        return out
    cand.sort(key=lambda d: d["t"][0])
    starts = np.array([d["t"][0] for d in cand])
    xn = {id(d): M.norm(d["uv"]) for d in cand}
    succ, taken = {}, set()
    for ia in sorted(range(len(cand)), key=lambda i: cand[i]["t"][-1]):
        A = cand[ia]
        tA = A["t"][-1]
        lo, hi = np.searchsorted(starts, tA - 0.02), np.searchsorted(starts, tA + max_gap)
        best, bc = None, None
        for ib in range(lo, hi):
            B = cand[ib]
            if ib == ia or ib in taken or B["t"][0] <= A["t"][0]:
                continue
            gap = max(B["t"][0] - tA, 0.0)
            if _cut_between(cuts, tA, B["t"][0]):
                continue
            if math.dist(A["uv"][-1], B["uv"][0]) > 60 + 900 * gap:
                continue
            T = np.r_[A["t"], B["t"]]; uv = np.r_[A["uv"], B["uv"]]
            o = np.argsort(T, kind="stable")
            if T[o][-1] - T[o][0] > 2.5:
                continue
            th, X, ok = M.ballistic(T[o] - T[o][0], np.r_[xn[id(A)], xn[id(B)]][o])
            if (not ok or X[:, 2].min() < -0.4 or X[:, 2].max() > 7 or abs(th[6]) > 4 or abs(th[7]) > 4
                    or np.linalg.norm(th[3:6]) > 22):
                continue
            nA = len(A["t"])
            isA = np.r_[np.ones(nA, bool), np.zeros(len(B["t"]), bool)][o]
            P = M.cam.project(X); e = np.sum((P - uv[o]) ** 2, 1)
            rj = float(max(np.sqrt(e[isA].mean()), np.sqrt(e[~isA].mean())))
            ra, rb = A["diag"].get("rms_ballistic") or 9, B["diag"].get("rms_ballistic") or 9
            if rj < max(2.5, 1.4 * max(ra, rb)) and (bc is None or rj < bc):
                best, bc = ib, rj
        if best is not None:
            succ[ia] = best; taken.add(best)
    if not succ:
        return out
    merged, drop = [], set()
    for ia in range(len(cand)):
        if ia in taken or ia not in succ:
            continue
        chain = [cand[ia]]; j = succ[ia]
        while j is not None:
            chain.append(cand[j]); j = succ.get(j)
        T = np.concatenate([c["t"] for c in chain]); o = np.argsort(T, kind="stable")
        cat = lambda k: np.concatenate([np.asarray(c[k], float) for c in chain])[o]
        uv = np.concatenate([np.asarray(c["uv"], float) for c in chain])[o]
        d = _finalize_seg(M, chain[0]["id"], T[o], cat("f"), uv[:, 0], uv[:, 1], cat("r"), cat("streak"),
                          check_jitter=False)
        if d is not None:
            d["diag"]["merged"] = len(chain)
            merged.append(d)
            drop.update(id(c) for c in chain)
    return [d for d in out if id(d) not in drop] + merged


def finalize_track(tr, cam, models=None):
    """-> list of output track dicts (a raw track may be split into several)."""
    T, F, U, V, R, A, Ls, _ = _arrays(tr)
    o = np.argsort(T, kind="stable"); T, F, U, V, R, A, Ls = T[o], F[o], U[o], V[o], R[o], A[o], Ls[o]
    M = models or _Models(cam)
    out = []
    for a, b in split_track(T, U, V):
        d = _finalize_seg(M, tr.id, T[a:b], F[a:b], U[a:b], V[a:b], R[a:b], Ls[a:b])
        if d is not None:
            out.append(d)
    return out


def _finalize_seg(M, tid, T, F, U, V, R, Ls, check_jitter=True):
    n = len(T)
    if n < MIN_DETS or T[-1] - T[0] < MIN_SPAN_S:
        return None
    disp = float(np.hypot(U - U[0], V - V[0]).max())
    if disp < MIN_DISP_PX:
        return None
    rms = _local_rms(T, U, V)
    if check_jitter and np.isfinite(rms) and rms > MAX_LOCAL_RMS:
        return None                            # association noise, not a ball
    uv = np.c_[U, V]
    xn = M.norm(uv)
    # windows of <= 0.8 s, half overlapping
    best, bdiag = 0.0, None
    wins = []
    if T[-1] - T[0] <= 0.8:
        wins = [(0, n)]
    else:
        s0 = 0
        while s0 < n - 4:
            e0 = np.searchsorted(T, T[s0] + 0.8)
            wins.append((s0, min(n, e0)))
            if e0 >= n:
                break
            s0 = np.searchsorted(T, T[s0] + 0.4)
    for a, b in wins:
        if b - a < 6:
            continue
        e, dg = _window_evidence(M, T[a:b], uv[a:b], xn[a:b])
        if bdiag is None or e > best:
            best, bdiag = e, dg
    if bdiag is None:
        best, bdiag = _window_evidence(M, T, uv, xn) if n >= 5 else (0.0, {})
    dur = float(T[-1] - T[0])
    score = best
    sp = np.hypot(np.diff(U), np.diff(V)) / np.maximum(np.diff(T), 1e-3)
    return {"id": tid, "f": [int(x) for x in F], "t": [round(float(x), 4) for x in T],
            "uv": np.round(uv, 1).tolist(), "r": np.round(R, 1).tolist(),
            "streak": np.round(Ls, 1).tolist(),
            "moving": True, "airborne_score": round(float(score), 3),
            "diag": dict(n=n, dur=round(dur, 3), disp_px=round(disp, 1),
                         img_speed_med=round(float(np.median(sp)), 1),
                         local_rms=None if not np.isfinite(rms) else round(rms, 2), **bdiag)}


# ---------------------------------------------------------------- driver
def iter_av(video_path, t_from, t_to, warm=0.3):
    """Yield (frame_idx, t, av.VideoFrame) for t in [t_from - warm, t_to]."""
    import av
    c = av.open(video_path)
    s = c.streams.video[0]; s.thread_type = "AUTO"
    fps = float(s.average_rate or 60)
    t0 = max(0.0, t_from - warm)
    if t0 > 1.0:
        c.seek(int(max(0.0, t0 - 1.0) / s.time_base), stream=s)
    try:
        for f in c.decode(s):
            if f.time is None or f.time < t0:
                continue
            if f.time > t_to:
                break
            yield int(round(f.time * fps)), float(f.time), f
    finally:
        c.close()


def overlay_from_layout(cam, video_path=None, layout=None, log=print):
    """Graphics boxes (camera px) from the event's layout.json, or None (= OVERLAY_BOXES).
    layout: None = look it up (camera "event" key / event key in the video path), False = ignore,
    or a layout.Layout / layout.json path."""
    if layout is False:
        return None
    try:
        import layout as LY
        L = LY.load(layout) if isinstance(layout, str) else (layout or LY.find(cam=cam, video_path=video_path))
        boxes = LY.overlay_boxes(L, cam.W, cam.H) if L else None
    except Exception as e:
        log(f"  fuel: layout unavailable ({e!r}), default overlay boxes"); return None
    if boxes is not None:
        log(f"  fuel: overlay boxes from layout {L.path}: {boxes}")
    return boxes


def track_fuel(video_path, cam, t_from, t_to, log=print, frame_iter=None, fps=None, cv_threads=None, layout=None):
    """Moving-fuel tracks between video seconds t_from and t_to.  frame_iter: optional
    iterable of (frame_idx, time_s, bgr ndarray) to reuse another decode pass (any
    subset of frames in time order, e.g. 30 Hz).  cv_threads: temporarily set OpenCV's
    thread count (1 = same wall time on these small images at ~1/3 of the CPU).
    layout: broadcast layout for the overlay mask (overlay_from_layout; default = look it up)."""
    t_start = time.time()
    if cv_threads is not None:
        prev_threads = cv2.getNumThreads(); cv2.setNumThreads(int(cv_threads))
        try:
            return track_fuel(video_path, cam, t_from, t_to, log, frame_iter, fps, None, layout)
        finally:
            cv2.setNumThreads(prev_threads)
    if fps is None:
        fps = 60.0
        if video_path and frame_iter is None:
            import av
            with av.open(video_path) as c:
                fps = float(c.streams.video[0].average_rate or 60)
    ft = FuelTracker(cam, fps=fps, log=log, t_from=t_from, t_to=t_to,
                     overlay_px=overlay_from_layout(cam, video_path, layout, log))
    if frame_iter is None:
        for idx, t, fr in iter_av(video_path, t_from, t_to):
            ft.push_av(idx, t, fr)
    else:
        for idx, t, bgr in frame_iter:
            if t < t_from - 0.3 or t > t_to:
                continue
            ft.push(idx, t, bgr)
    res = ft.result()
    st = res["stats"]
    st["wall_s"] = round(time.time() - t_start, 1)
    log(f"  fuel: {st['frames_processed']}/{st['frames_in']} frames ({st['duplicates']} dup, "
        f"{st['dark']} dark, {st['swapped']} swapped, {st['glitch']} glitch, {st['camera_moving']} cam-moving, "
        f"{st['cuts']} cuts ({st['cut_frames']} frames), {st['dup_resets']} dup-resets; "
        f"view ok {st['view_ok_frac']}), {st['detections']} blobs, "
        f"{st['tracks']} tracks ({st['airborne_tracks']} airborne) in {st['wall_s']}s")
    return res


def load_camera(cam_path, match_json=None):
    raw = json.load(open(cam_path))
    if match_json:
        m = json.load(open(match_json))
        if m.get("camera_pose"):
            raw = dict(raw, rvec=m["camera_pose"]["rvec"], tvec=m["camera_pose"]["tvec"])
    return CAMM.Camera(raw)


if __name__ == "__main__":
    video, cam_path, a, b, out = sys.argv[1:6]
    cam = load_camera(cam_path, sys.argv[6] if len(sys.argv) > 6 else None)
    res = track_fuel(video, cam, float(a), float(b), cv_threads=1)
    json.dump(res, open(out, "w"))
    print("wrote", out)
