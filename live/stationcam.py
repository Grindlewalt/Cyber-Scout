"""Driver-station cameras (picture-in-picture insets of the "Match N" upload).

FIRST Washington posts two uploads per match: "Wide Match N" (the clean overhead camera the
pipeline tracks on) and "Match N" = the SAME program with two fixed low cameras inset at the
bottom (plus a middle inset that is a crop of the overhead camera -- ignored):

    red  inset  x 0..622,    y 708..1080   camera on the near side at the RED end, looking
                                           across the red hub (+Y face, tags 2/11) and tower
    blue inset  x 1294..1920, y 708..1080  same at the BLUE end (blue hub +Y face, tags 21/24)

Everything above y=708 is pixel-identical to the wide upload, so the two uploads are synced by
direct frame comparison (frame-accurate), see sync_offset().

Station cameras are calibrated once per event in INSET pixels (crop of the 1920x1080 frame, no
rescale): events/<ev>/station_red.json / station_blue.json = camera JSON as camera.py reads it
plus "crop": [x0, y0, x1, y1].  Per-match pose check: check_station_pose().

API for downstream agents:
    sc = StationCams(event_dir, match_json, inset_video, offset)
    for t_wide, red_bgr, blue_bgr in sc.frames(t0, t1):  ...   # t_wide = wide-video seconds
    sc.project(P_field_m, "red")     -> inset px  (N,2)
    sc.ray(uv, "red")                -> (origin C, unit directions) in field metres
    sc.ground(uv, "red", Z=0)        -> field XY on the plane Z
    sc.cam["red"]                    -> camera.Camera (pose already corrected for this match)
CLI:
    python live/stationcam.py sync WIDE.mp4 INSET.mp4
    python live/stationcam.py sheet EVENT_DIR MATCH.json WIDE.mp4 INSET.mp4 OUT.jpg [t ...]
"""
import os, sys, json, re, csv
import numpy as np, cv2, av

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import camera as CAMM

W, H = 1920, 1080
RECTS = {"red": (0, 708, 622, 1080), "blue": (1294, 708, 1920, 1080)}
SYNC_REGION = (0, 0, 1920, 700)          # identical in both uploads (overhead + scoreboard)
SCORE_REGION = (450, 0, 1470, 160)       # scoreboard graphic
CLOCK_BOX = (912, 72, 1008, 116)         # match clock digits (process.CLOCK_BOX)
IN = 0.0254
TAG_SIZE = 6.5 * IN                      # 2026 FRC AprilTags (36h11), black square edge
# The constants above are the 2026 FIRST Washington layout.  Other broadcasts: the event's layout.json
# (live/layout.py, drawn in the box editor) gives the inset rectangles / clock box / shared region; the
# station calibration's own "crop" always wins for the inset it was calibrated in.  No layout and no
# crop = the constants above (unchanged behaviour).


def rects_for(event_dir=None, cams=None):
    """{"red": box, "blue": box} for an event: per camera its calibration's "crop", else the event
    layout's station inset, else RECTS."""
    out = dict(RECTS)
    try:
        import layout as LY
        lr = LY.station_rects(LY.find(event_dir=event_dir)) if event_dir else None
    except Exception:
        lr = None
    for w in RECTS:
        crop = ((cams or {}).get(w).raw.get("crop") if (cams or {}).get(w) is not None else None)
        if crop and len(crop) == 4: out[w] = tuple(int(v) for v in crop)
        elif lr and w in lr: out[w] = tuple(lr[w])
    return out


def layout_regions(event_dir=None):
    """(sync region, clock box) for an event: from its layout.json, else SYNC_REGION / CLOCK_BOX."""
    try:
        import layout as LY
        L = LY.find(event_dir=event_dir) if event_dir else None
        return tuple(LY.sync_region(L, SYNC_REGION)), tuple(LY.station_clock_box(L, CLOCK_BOX))
    except Exception:
        return SYNC_REGION, CLOCK_BOX


# ---------------------------------------------------------------- 1. which upload
def find_inset_video(match_tba, meta_cache, meta_fn=None):
    """The "Match N" upload (two station-camera insets) among a TBA match's videos, or None.
    `meta_cache` {yt id: {id, channel, title, duration, height}} as worker.py keeps it
    (data/cache/ytmeta.json); missing ids are fetched with videos.meta (or `meta_fn`)."""
    if meta_fn is None:
        import videos
        meta_fn = videos.meta
    n = match_tba.get("match_number")
    lv = match_tba.get("comp_level", "qm")
    num = n if lv in ("qm", "f") else match_tba.get("set_number", n)
    pat = re.compile(r"^Match\s+%d(\s*\([^)]*\))?\s*$" % num, re.I)
    best = []
    for v in match_tba.get("videos", []):
        if v.get("type") != "youtube": continue
        vid = v["key"].split("?")[0]
        if vid not in meta_cache:
            meta_cache[vid] = meta_fn(vid)
        m = meta_cache[vid]
        if not m: continue
        t = m["title"].strip()
        if t.lower().startswith("wide"): continue
        if not pat.match(t): continue
        if m.get("height", 1080) and m.get("height", 1080) < 1080: continue
        if m.get("duration", 0) >= 600: continue
        best.append((m.get("channel") != "FIRST Washington", m))
    best.sort(key=lambda b: b[0])
    return best[0][1] if best else None


# ---------------------------------------------------------------- 2. sync
def _thumbs(video, t0=0.0, t1=None, scale=4, region=None, clock_box=None):
    """Grey thumbnails (1/scale) of SYNC_REGION + the clock box at 1/2 scale, every decoded frame."""
    c = av.open(video); s = c.streams.video[0]; s.thread_type = "AUTO"
    if t0 > 1:
        c.seek(int((t0 - 1) / s.time_base), stream=s)
    x0, y0, x1, y1 = region or SYNC_REGION
    cx0, cy0, cx1, cy1 = clock_box or CLOCK_BOX
    ts, th, ck = [], [], []
    for f in c.decode(s):
        if f.time is None or f.time < t0 - 1e-6: continue
        if t1 is not None and f.time > t1: break
        g = f.reformat(width=W // 2, height=H // 2, format="gray").to_ndarray()
        ck.append(g[cy0 // 2:cy1 // 2, cx0 // 2:cx1 // 2].copy())
        th.append(cv2.resize(g[y0 // 2:y1 // 2, x0 // 2:x1 // 2], None, fx=2 / scale, fy=2 / scale,
                             interpolation=cv2.INTER_AREA))
        ts.append(f.time)
    c.close()
    return np.array(ts), np.array(th, np.float32), np.array(ck, np.float32)


def _fine(ta, A, tb, B, offset, search=0.15):
    """Best wide-minus-inset offset near `offset` by direct thumbnail comparison: for each inset
    frame the nearest wide frame at t+offset; mean abs diff per candidate."""
    dt = np.median(np.diff(ta))
    cands = offset + np.arange(-round(search / dt), round(search / dt) + 1) * dt
    errs = []
    for o in cands:
        j = np.searchsorted(tb, ta + o - dt / 2)
        ok = (j < len(tb)) & (ta + o >= tb[0]) & (ta + o <= tb[-1])
        j = np.clip(j, 0, len(tb) - 1)
        ok &= np.abs(tb[j] - (ta + o)) < dt / 2
        if ok.sum() < 30: errs.append(np.inf); continue
        ii, jj = np.where(ok)[0], j[ok]
        e = [np.abs(A[ii[c:c + 64]] - B[jj[c:c + 64]]).mean() * len(ii[c:c + 64]) for c in range(0, len(ii), 64)]
        errs.append(float(np.sum(e) / len(ii)))
    errs = np.array(errs); k = int(np.argmin(errs))
    return float(cands[k]), errs, cands


def _coarse(ta, A, tb, B):
    """Lag (s) maximising the correlation of per-frame motion energy."""
    ea = np.r_[0, np.abs(np.diff(A, axis=0)).mean((1, 2))]
    eb = np.r_[0, np.abs(np.diff(B, axis=0)).mean((1, 2))]
    dt = float(np.median(np.diff(ta)))
    grid_b = np.arange(tb[0], tb[-1], dt)
    eb_g = np.interp(grid_b, tb, eb); ea_g = np.interp(np.arange(ta[0], ta[-1], dt), ta, ea)
    k = np.ones(2) / 2                      # 60p containers carry 30p content
    eb_g = np.convolve(eb_g, k, "same"); ea_g = np.convolve(ea_g, k, "same")
    ea_n = (ea_g - ea_g.mean()) / (ea_g.std() + 1e-9)
    n = len(ea_n)
    if len(eb_g) < n: return 0.0, -1.0
    win = np.lib.stride_tricks.sliding_window_view(eb_g, n)
    z = (win - win.mean(1, keepdims=True)) / (win.std(1, keepdims=True) + 1e-9)
    r = (z * ea_n).mean(1); lag = int(np.argmax(r))
    return float(grid_b[lag] - ta[0]), float(r[lag])


def _window(wide_video, inset_video, a0, a1, guess=None, max_lag=30.0, region=None, clock_box=None):
    ta, A, _ = _thumbs(inset_video, a0, a1, region=region, clock_box=clock_box)
    if len(ta) < 60: return None
    lag = max_lag if guess is None else 1.0
    g = 0.0 if guess is None else guess
    tb, B, _ = _thumbs(wide_video, max(0.0, a0 + g - lag), a1 + g + lag, region=region, clock_box=clock_box)
    if len(tb) < 60: return None
    co, corr = _coarse(ta, A, tb, B)
    if guess is not None and abs(co - guess) > 0.5:
        co = guess                           # trust the extrapolation over a weak correlation
    off, errs, cands = _fine(ta, A, tb, B, co, search=0.2)
    dt = float(np.median(np.diff(ta)))
    e0 = float(errs[np.argmin(errs)])
    # neighbours one CONTENT frame away (60p containers carry 30p content: +-1/60 s is half-right)
    nb = [float(errs[i]) for i in range(len(cands)) if abs(abs(cands[i] - off) - 2 * dt) < dt / 2]
    e1 = min(nb) if nb else np.inf
    return dict(t=round((a0 + a1) / 2, 2), offset=round(off, 4), err=round(e0, 3), err_pm1=round(e1, 3),
                conf=round(float(np.clip(1 - e0 / max(e1, 1e-9), 0, 1)), 3), corr=round(corr, 3), dt=dt,
                ok=bool(e1 - e0 > 0.1 and corr > 0.4))


def sync_offset(wide_video, inset_video, first=(5.0, 45.0), every=40.0, span=15.0, max_lag=30.0, log=None,
                region=None, clock_box=None):
    """Map inset-video time -> wide-video time by matching the region the two uploads share
    (overhead picture + scoreboard, y < 700): coarse cross-correlation of per-frame motion energy
    (+-max_lag s) on the first window, then frame-by-frame comparison (fine) in windows every
    `every` s along the whole video.  Same-family uploads ("Match N" vs "Wide Match N", both 60p
    from the same encoder) have ONE constant offset; FIRST HQ re-encodes (59.94p, e.g. 2026wasam
    qm19's wide video) drift by ~0.25 % and get a piecewise-linear map.
    Returns dict(offset (median, s to ADD to inset time), knots [[t_inset, offset]...], constant,
    windows [...], err / err_pm1 / conf of the first window, ok).  Use wide_time(sync, t).
    region / clock_box: the shared area / clock digits (default SYNC_REGION / CLOCK_BOX; layout_regions())."""
    c = av.open(inset_video); s = c.streams.video[0]
    dur = float(s.duration * s.time_base) if s.duration else float(c.duration / 1e6); c.close()
    w0 = _window(wide_video, inset_video, first[0], first[1], None, max_lag, region, clock_box)
    wins = [w0] if w0 else []
    t = first[1] + every - span
    while t + span < dur - 2:
        good = [w for w in wins if w["ok"]]
        if len(good) >= 2:
            pa = np.polyfit([w["t"] for w in good], [w["offset"] for w in good], 1)
            g = float(np.polyval(pa, t + span / 2))
        else:
            g = good[-1]["offset"] if good else (w0["offset"] if w0 else None)
        w = _window(wide_video, inset_video, t, t + span, g, max_lag, region, clock_box)
        if w: wins.append(w)
        t += every
    # reject windows that matched poorly (err well above the others: replays, end cards, a cut)
    # or that disagree with the robust line through the rest by more than 3 frames
    good = [w for w in wins if w["ok"]]
    if len(good) >= 3:
        me = np.median([w["err"] for w in good])
        good = [w for w in good if w["err"] < 1.6 * me]
    if len(good) >= 3:
        tt = np.array([w["t"] for w in good]); oo = np.array([w["offset"] for w in good])
        keep = np.ones(len(good), bool)
        for _ in range(2):
            pa = np.polyfit(tt[keep], oo[keep], 1) if keep.sum() >= 2 else np.array([0, np.median(oo)])
            keep = np.abs(oo - np.polyval(pa, tt)) <= 3 * good[0]["dt"] + 1e-6
        good = [w for w, k in zip(good, keep) if k]
    for w in wins:
        w["used"] = any(w is g for g in good)
    good = good or wins
    offs = np.array([w["offset"] for w in good]); ts = np.array([w["t"] for w in good])
    dt = good[0]["dt"] if good else 1 / 60
    const = bool(len(offs) and np.ptp(offs) <= 1.5 * dt)
    med = float(np.median(offs)) if len(offs) else 0.0
    knots = [[0.0, round(med, 4)]] if const else [[float(a), float(b)] for a, b in zip(ts, offs)]
    res = dict(offset=round(med, 4), constant=const, knots=knots, windows=wins,
               err=w0["err"] if w0 else None, err_pm1=w0["err_pm1"] if w0 else None,
               conf=w0["conf"] if w0 else 0.0, ok=bool(w0 and w0["ok"] and len(good) >= 2), dt=dt)
    if log: log(f"  station sync: offset {med:.4f} const {const} windows {[(w['t'], w['offset'], w['ok']) for w in wins]}")
    return res


def wide_time(sync, t_inset):
    """Wide-video seconds for inset-video seconds (sync: float offset or sync_offset() dict)."""
    if not isinstance(sync, dict):
        return np.asarray(t_inset, float) + float(sync)
    k = np.array(sync["knots"], float)
    t = np.asarray(t_inset, float)
    if len(k) == 1:
        return t + k[0, 1]
    o = np.interp(t, k[:, 0], k[:, 1])
    # linear extrapolation beyond the end knots
    lo = (k[1, 1] - k[0, 1]) / (k[1, 0] - k[0, 0]); hi = (k[-1, 1] - k[-2, 1]) / (k[-1, 0] - k[-2, 0])
    o = np.where(t < k[0, 0], k[0, 1] + lo * (t - k[0, 0]), o)
    o = np.where(t > k[-1, 0], k[-1, 1] + hi * (t - k[-1, 0]), o)
    return t + o


def inset_time(sync, t_wide):
    """Inverse of wide_time (fixed-point iteration; offsets change slowly)."""
    t = np.asarray(t_wide, float) - (sync["offset"] if isinstance(sync, dict) else float(sync))
    for _ in range(4):
        t = t - (wide_time(sync, t) - np.asarray(t_wide, float))
    return t


def verify_sync(wide_video, inset_video, sync, t0=0.0, t1=None, thr=5.0, region=None, clock_box=None):
    """Clock-digit check over [t0, t1] (inset time) after alignment.  For each candidate shift
    (map + k/60 s, k = -3..3) count frames where the inset clock box differs from the wide one
    (mean abs grey diff > thr): digits must match everywhere except while a digit is changing.
    Finding (2026wasam): the scoreboard graphic is 60p while the camera picture is 30p (every
    other frame duplicated); between the two uploads the graphic aligns one 60p frame later than
    the picture, i.e. within half a content frame."""
    ta, A, CA = _thumbs(inset_video, t0, t1, region=region, clock_box=clock_box)
    tw = wide_time(sync, ta)
    tb, B, CB = _thumbs(wide_video, max(0, float(tw[0]) - 1), float(tw[-1]) + 1, region=region, clock_box=clock_box)
    dt = float(np.median(np.diff(ta)))
    dcb = np.r_[0, np.abs(np.diff(CB, axis=0)).mean((1, 2))]
    out = dict(ticks=int((dcb > 8).sum()), shifts={})
    for k in range(-3, 4):
        o = tw + k * dt
        j = np.clip(np.searchsorted(tb, o - dt / 2), 0, len(tb) - 1)
        ii = np.where(np.abs(tb[j] - o) < dt / 2)[0]
        ce = np.abs(CA[ii] - CB[j[ii]]).mean((1, 2))
        se = np.array([np.abs(A[i] - B[j[i]]).mean() for i in ii])
        out["shifts"][k] = dict(frames=int(len(ii)), clock_bad=int((ce > thr).sum()),
                                clock_err=round(float(ce.mean()), 3), picture_err=round(float(se.mean()), 3))
    sh = out["shifts"]
    kc = min(sh, key=lambda k: sh[k]["clock_bad"]); kp = min(sh, key=lambda k: sh[k]["picture_err"])
    out.update(clock_best_shift=kc, picture_best_shift=kp, clock_bad_at_map=sh[0]["clock_bad"],
               clock_bad_per_tick=round(sh[0]["clock_bad"] / max(out["ticks"], 1), 2),
               frames=sh[0]["frames"])
    # tick timing (robust to re-encodes / 1 px shifts that defeat the pixel comparison): first frame
    # of every clock-box change in each video; inset ticks mapped to wide time vs nearest wide tick
    def starts(C):
        d = np.r_[0, np.abs(np.diff(C, axis=0)).mean((1, 2))] > 8
        return np.where(d & ~np.r_[False, d[:-1]])[0]
    ia, ib = starts(CA), starts(CB)
    if len(ia) and len(ib):
        ma = wide_time(sync, ta[ia])
        dd = (ma[:, None] - tb[ib][None]); k = np.abs(dd).argmin(1)
        e = dd[np.arange(len(ia)), k] / dt                       # inset tick - wide tick, 60p frames
        e = e[np.abs(e) < 15]
        out["tick_err_frames"] = dict(n=int(len(e)), median=round(float(np.median(e)), 2),
                                      p90_abs=round(float(np.percentile(np.abs(e), 90)), 2),
                                      max_abs=round(float(np.abs(e).max()), 2))
    return out


# ---------------------------------------------------------------- 3. calibration data
def _yellow(bgr):
    """min(R,G) - B, minus a penalty for orange / lime (as fuel.yellow_from_bgr)."""
    b, g, r = [bgr[..., i].astype(np.int16) for i in range(3)]
    return np.clip(np.minimum(r, g) - b - np.maximum(np.abs(r - g) - 30, 0), 0, 255).astype(np.uint8)


class _Reader:
    """Sequential frame access by increasing time."""
    def __init__(self, path):
        self.c = av.open(path); self.s = self.c.streams.video[0]; self.s.thread_type = "AUTO"
        self.it = self.c.decode(self.s); self.f = None

    def at(self, t):
        while self.f is None or self.f.time < t - 1e-4:
            self.f = next(self.it)
        return self.f.to_ndarray(format="bgr24")

    def close(self):
        self.c.close()


_TAGDET = None


def _tagdet():
    # one detector per process: pupil_apriltags' destructor intermittently segfaults (process.py)
    global _TAGDET
    if _TAGDET is None:
        from pupil_apriltags import Detector
        _TAGDET = Detector(families="tag36h11", quad_decimate=1.0, decode_sharpening=0.25)
    return _TAGDET


def detect_tags(gray, scale=4, min_margin=20):
    """{id: 4x2 corners} in `gray` pixels (upscaled detection; insets are low-res)."""
    up = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    out = {}
    for d in _tagdet().detect(up):
        if d.hamming == 0 and d.decision_margin > min_margin:
            out[int(d.tag_id)] = d.corners / scale
    return out


def collect_pairs(wide_video, inset_video, sync, cam_over, step=2.0, log=None):
    """Static-ball correspondences for calibration.  Every `step` s: yellow blobs that stay put for
    +-0.25 s (per-pixel min of 3 frames) in the overhead frame (-> field XY at ball-centre height,
    via the per-match overhead camera) and in both insets (-> inset px), plus tag corners per inset.
    Returns dict(samples=[{t, over: [[X, Y, u, v, area, r_px, single]...], red: [[u, v, area, w, h,
    fill]...], blue: [...], mean}], tags={red: {id: [[t, 8 corner coords]...]}, blue: ...},
    crops={red: [bgr...], blue: [...]})  (crops every 3rd sample, for plates)."""
    cam = cam_over
    edge = np.concatenate([cam.project(np.c_[np.linspace(a[0], b[0], 50), np.linspace(a[1], b[1], 50), np.zeros(50)])
                           for a, b in [((0, 0), (CAMM.LEN, 0)), ((CAMM.LEN, 0), (CAMM.LEN, CAMM.WID)),
                                        ((CAMM.LEN, CAMM.WID), (0, CAMM.WID)), ((0, CAMM.WID), (0, 0))]])
    fmask = np.zeros((H, W), np.uint8)
    cv2.fillPoly(fmask, [edge.astype(np.int32)], 1)
    fmask[:170, 440:1480] = 0                                 # score bar

    def over_balls(Y):
        n, lab, st, cen = cv2.connectedComponentsWithStats(((Y > 60) & (fmask > 0)).astype(np.uint8), 8)
        out = []
        for i in range(1, n):
            x, y, w, h, a = st[i]
            if a < 12: continue
            P = cam.img_to_plane([cen[i]], 0.075)[0]
            if not np.isfinite(P).all(): continue
            q = cam.project(np.array([[P[0], P[1], .075], [P[0] + .075, P[1], .075], [P[0], P[1] + .075, .075]]))
            r = max(np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[2] - q[0]))
            single = 0.45 * np.pi * r * r < a < 1.7 * np.pi * r * r and max(w, h) < 2.6 * r + 3
            out.append([float(P[0]), float(P[1]), float(cen[i][0]), float(cen[i][1]), int(a), float(r), int(single)])
        return out

    def blobs(Y):
        n, lab, st, cen = cv2.connectedComponentsWithStats((Y > 50).astype(np.uint8), 8)
        return [[float(cen[i][0]), float(cen[i][1]), int(st[i][4]), int(st[i][2]), int(st[i][3]),
                 float(st[i][4] / (st[i][2] * st[i][3]))] for i in range(1, n) if st[i][4] >= 6]

    ri, rw = _Reader(inset_video), _Reader(wide_video)
    dur = float(ri.s.duration * ri.s.time_base)
    res = dict(samples=[], tags={k: {} for k in RECTS}, crops={k: [] for k in RECTS})
    t, n = 1.0, 0
    while float(wide_time(sync, t)) + 0.3 < float(rw.s.duration * rw.s.time_base) - 1 and t + 0.3 < dur - 1:
        try:
            I = [ri.at(t + d) for d in (-0.25, 0.0, 0.25)]
            Wd = [rw.at(float(wide_time(sync, t + d))) for d in (-0.25, 0.0, 0.25)]
        except StopIteration:
            break
        smp = dict(t=round(t, 3), over=over_balls(np.min([_yellow(w) for w in Wd], 0)),
                   mean=float(Wd[1][::16, ::16].mean()))
        for k, (x0, y0, x1, y1) in RECTS.items():
            cr = [im[y0:y1, x0:x1] for im in I]
            smp[k] = blobs(np.min([_yellow(c) for c in cr], 0))
            if n % 3 == 0: res["crops"][k].append(cr[1])
            for tid, cc in detect_tags(cv2.cvtColor(cr[1], cv2.COLOR_BGR2GRAY)).items():
                res["tags"][k].setdefault(tid, []).append([t] + cc.ravel().tolist())
        res["samples"].append(smp)
        t += step; n += 1
    ri.close(); rw.close()
    if log: log(f"  collected {len(res['samples'])} samples; tags {{k: sorted(v) for k, v in res['tags'].items()}}")
    return res


# ---------------------------------------------------------------- 3b. calibration fit
def _calib():
    import calibrate
    return calibrate


def _median_tags(tags, min_hits=10):
    """{id: [[t, 8 coords]...]} -> {id: 4x2 median corners} (ids seen >= min_hits times)."""
    return {int(k): np.median(np.array(v)[:, 1:].reshape(-1, 4, 2), 0) for k, v in tags.items() if len(v) >= min_hits}


def _ball_pairs(p, samples, which, size, rad):
    """Overhead single balls whose projection lands within `rad` px of a compact station blob,
    unambiguously (no other overhead blob projects near that blob)."""
    C = _calib(); Wc, Hc = size
    P3, U = [], []
    for s in samples:
        if s.get("mean", 99) < 20 or not s["over"] or not s[which]: continue
        O = np.array(s["over"], float); B = np.array(s[which], float)
        B = B[(B[:, 5] > 0.45) & (np.maximum(B[:, 3], B[:, 4]) < 2.2 * np.minimum(B[:, 3], B[:, 4]) + 2)]
        if not len(B): continue
        Xa = np.c_[O[:, 0], O[:, 1], np.full(len(O), 0.075)]
        qa = C.proj_pinhole(p, Xa)
        sel = (O[:, 6] > 0) & np.isfinite(qa).all(1) & (qa[:, 0] >= 0) & (qa[:, 0] < Wc) & (qa[:, 1] >= 0) & (qa[:, 1] < Hc)
        if not sel.any(): continue
        d = np.linalg.norm(qa[sel][:, None] - B[None, :, :2], axis=2)          # singles x blobs
        j = d.argmin(1); dm = d[np.arange(len(j)), j]
        qa_f = np.where(np.isfinite(qa), qa, 1e6)
        crowd = (np.linalg.norm(qa_f[:, None] - B[None, :, :2], axis=2) < 2.5 * rad).sum(0)   # per blob
        for i, (jj, dd) in zip(np.where(sel)[0], zip(j, dm)):
            if dd <= rad and crowd[jj] == 1:
                P3.append(Xa[i]); U.append(B[jj, :2])
    return np.array(P3).reshape(-1, 3), np.array(U).reshape(-1, 2)


def fit_station(colls, which, tag_csv, log=print, sig_c=10.0):
    """Joint fit over several matches: shared intrinsics [f, aspect, cx, cy, k1] (k2 held ~0),
    one pose per match.  colls = [(name, collect_pairs() dict)].  Data: tag corners (median per
    match, sigma 0.3 px) + static ball pairs (sigma 1.5 px, soft-L1).  Returns (camera dict with
    the reference pose = median of the per-match poses, report dict)."""
    from scipy.optimize import least_squares
    from scipy.sparse import lil_matrix
    C = _calib()
    poses = C.load_tag_poses(tag_csv)
    x0, y0, x1, y1 = RECTS[which]; Wc, Hc = x1 - x0, y1 - y0
    T = []
    for name, d in colls:
        mt = _median_tags(d["tags"][which])
        P = np.concatenate([C.tag_corners(poses[i]) for i in sorted(mt)]) if mt else np.zeros((0, 3))
        U = np.concatenate([mt[i] for i in sorted(mt)]) if mt else np.zeros((0, 2))
        T.append((P, U, sorted(mt)))
    # init: focal grid, PnP on each match's tags, score = number of ball pairs
    best = None
    for f in np.arange(400, 1501, 25):
        K = np.array([[f, 0, Wc / 2], [0, f, Hc / 2], [0, 0, 1.0]])
        ps, sc = [], 0
        for (name, d), (P, U, ids) in zip(colls, T):
            if len(P) < 8: ps.append(None); continue
            ok, rv, tv = cv2.solvePnP(P, U.reshape(-1, 1, 2), K, None, flags=cv2.SOLVEPNP_SQPNP)
            R, _ = cv2.Rodrigues(rv); Cc = (-R.T @ tv).ravel()
            p = np.r_[f, 1.0, Wc / 2, Hc / 2, 0, 0, rv.ravel(), Cc]
            ps.append(p); sc += len(_ball_pairs(p, d["samples"][::3], which, (Wc, Hc), 6.0)[0])
        if best is None or sc > best[0]: best = (sc, f, ps)
    _, f0, ps = best
    keep = [i for i, p in enumerate(ps) if p is not None]
    colls = [colls[i] for i in keep]; T = [T[i] for i in keep]; ps = [ps[i] for i in keep]
    M = len(colls)
    x = np.r_[f0, 1.0, Wc / 2, Hc / 2, 0.0, np.concatenate([p[6:12] for p in ps])]

    def full(x, m):
        return np.r_[x[:5], 0.0, x[5 + 6 * m:11 + 6 * m]]

    for rad in (6.0, 5.0, 4.0, 3.0, 3.0):
        pairs = [_ball_pairs(full(x, m), d["samples"], which, (Wc, Hc), rad) for m, (name, d) in enumerate(colls)]
        sizes = [2 * len(T[m][0]) + 2 * len(pairs[m][0]) for m in range(M)]

        def res(x):
            r = []
            for m in range(M):
                p = full(x, m)
                for P, U, s in ((T[m][0], T[m][1], 0.3), (pairs[m][0], pairs[m][1], 1.5)):
                    if len(P):
                        e = C.proj_pinhole(p, P) - U; e[~np.isfinite(e)] = 100.0
                        r.append((e / s).ravel())
            r.append([(x[2] - Wc / 2) / sig_c, (x[3] - Hc / 2) / sig_c, (x[1] - 1) / 0.05])
            return np.concatenate(r)
        J = lil_matrix((sum(sizes) + 3, len(x)), dtype=int)
        row = 0
        for m in range(M):
            J[row:row + sizes[m], :5] = 1; J[row:row + sizes[m], 5 + 6 * m:11 + 6 * m] = 1; row += sizes[m]
        J[row:, :5] = 1
        x = least_squares(res, x, jac_sparsity=J, loss="soft_l1", f_scale=2.0, max_nfev=200, x_scale="jac").x
    rep = dict(matches={}, f=round(float(x[0]), 2), aspect=round(float(x[1]), 4),
               c=[round(float(x[2]), 1), round(float(x[3]), 1)], k1=round(float(x[4]), 4))
    rvs = np.array([x[5 + 6 * m:8 + 6 * m] for m in range(M)]); Cs = np.array([x[8 + 6 * m:11 + 6 * m] for m in range(M)])
    ref = np.r_[x[:5], 0.0, np.median(rvs, 0), np.median(Cs, 0)]
    Rref, _ = cv2.Rodrigues(ref[6:9])
    for m, (name, d) in enumerate(colls):
        p = full(x, m)
        et = np.linalg.norm(C.proj_pinhole(p, T[m][0]) - T[m][1], axis=1)
        eb = np.linalg.norm(C.proj_pinhole(p, pairs[m][0]) - pairs[m][1], axis=1)
        Rm, _ = cv2.Rodrigues(p[6:9])
        ang = float(np.degrees(np.arccos(np.clip((np.trace(Rm @ Rref.T) - 1) / 2, -1, 1))))
        rep["matches"][name] = dict(tags=T[m][2], tag_rms_px=round(float(np.sqrt((et ** 2).mean())), 3),
                                    balls=int(len(eb)), ball_med_px=round(float(np.median(eb)), 3) if len(eb) else None,
                                    ball_p90_px=round(float(np.percentile(eb, 90)), 3) if len(eb) else None,
                                    C=np.round(p[9:12], 3).tolist(), rot_vs_ref_deg=round(ang, 3),
                                    pos_vs_ref_m=round(float(np.linalg.norm(p[9:12] - ref[9:12])), 3))
        if log: log(f"  {which} {name}: {rep['matches'][name]}")
    cam = C.camera_dict(ref, Wc, Hc, crop=list(RECTS[which]), which=which, model="pinhole f,aspect,c,k1",
                        tag_ids=sorted({i for t in T for i in t[2]}))
    rep["C_ref"] = np.round(ref[9:12], 3).tolist()
    return cam, rep


# ---------------------------------------------------------------- 4. per-match pose check
def check_station_pose(cam, inset_video, which, tag_csv, t_from=8.0, t_to=170.0, every=6.0, log=print,
                       moved_thr=0.7, bad_rms=1.5):
    """AprilTags on the hub faces in ~28 frames of this match's inset -> re-solve the pose
    (intrinsics fixed, position held near the calibrated one by a 0.25 m prior) when the camera
    was nudged.  Returns (camera.Camera, info); info["usable"] False when no tag is found (camera
    knocked away / covered) or the corrected pose still misfits (rms > bad_rms px)."""
    C = _calib()
    poses = C.load_tag_poses(tag_csv)
    x0, y0, x1, y1 = (cam.raw.get("crop") if cam.raw.get("crop") and len(cam.raw["crop"]) == 4 else RECTS[which])
    c = av.open(inset_video); s = c.streams.video[0]; s.thread_type = "AUTO"
    grays, nxt = [], t_from
    for f in c.decode(s):
        if f.time is None or f.time < nxt: continue
        g = cv2.cvtColor(f.to_ndarray(format="bgr24")[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY); nxt += every
        if g[::8, ::8].mean() > 20: grays.append(g)
        if nxt > t_to: break
    c.close()
    info = dict(checked=True, frames=len(grays))
    if len(grays) < 5:
        info.update(usable=False, reason="no frames"); log(f"  {which} station pose: {info}")
        return cam, info
    hits = {}
    for g in grays:
        for tid, cc in detect_tags(g).items():
            if tid in poses: hits.setdefault(tid, []).append(cc)
    found = {k: np.median(v, 0) for k, v in hits.items() if len(v) >= 3}
    if not found:
        info.update(usable=False, reason="no tags", tags=0); log(f"  {which} station pose: {info}")
        return cam, info
    ids = sorted(found)
    P = np.concatenate([C.tag_corners(poses[i]) for i in ids]); U = np.concatenate([found[i] for i in ids])
    e0 = np.linalg.norm(cam.project(P) - U, axis=1)
    info.update(tags=ids, moved_px=round(float(np.median(e0)), 2), rms_px=round(float(np.sqrt((e0 ** 2).mean())), 2))
    if np.median(e0) > moved_thr:
        # a bump re-aims the camera, it doesn't relocate it: solve rotation only first (well posed from
        # the hub tags); free the position (0.10 m prior) only if a re-aim can't explain the tags.
        # (With 2 coplanar tags rotation and position trade off: a free position fit "explains" 1 px
        # by 0.7 deg + 9 cm, which is wrong away from the hub.)
        p0 = C.pack_pinhole(cam.raw)
        p, _ = C.fit_points(cam.W, cam.H, [(P, U, 0.3)], p0, free=np.arange(6, 9))
        e1 = np.linalg.norm(C.proj_pinhole(p, P) - U, axis=1)
        mode = "rotation"
        if np.sqrt((e1 ** 2).mean()) > 0.8:
            p2, _ = C.fit_points(cam.W, cam.H, [(P, U, 0.3)], p, free=np.arange(6, 12), prior_C=p0[9:12], sig_C=0.10)
            e2 = np.linalg.norm(C.proj_pinhole(p2, P) - U, axis=1)
            if np.sqrt((e2 ** 2).mean()) < 0.5 * np.sqrt((e1 ** 2).mean()):
                p, e1, mode = p2, e2, "pose"
        R0, _ = cv2.Rodrigues(p0[6:9]); R1, _ = cv2.Rodrigues(p[6:9])
        info.update(resolved_rms_px=round(float(np.sqrt((e1 ** 2).mean())), 2), mode=mode,
                    rot_deg=round(float(np.degrees(np.arccos(np.clip((np.trace(R1 @ R0.T) - 1) / 2, -1, 1)))), 3),
                    shift_m=round(float(np.linalg.norm(p[9:12] - p0[9:12])), 3))
        if np.sqrt((e1 ** 2).mean()) < np.sqrt((e0 ** 2).mean()):
            new = dict(cam.raw); new.update(C.camera_dict(p, cam.W, cam.H)); new["crop"] = cam.raw.get("crop")
            cam = CAMM.Camera(new); info["applied"] = True
            e0 = e1
    info["usable"] = bool(np.sqrt((e0 ** 2).mean()) < bad_rms)
    if not info["usable"]: info["reason"] = "tags misfit"
    log(f"  {which} station pose: {info}")
    return cam, info


# ---------------------------------------------------------------- 5. API
def load_station(event_dir, which):
    """events/<ev>/station_<which>.json -> camera.Camera (inset pixels) or None."""
    p = os.path.join(event_dir, f"station_{which}.json")
    return CAMM.Camera(p) if os.path.exists(p) else None


class StationCams:
    """Both station cameras for one match.

    sc = StationCams("events/2026wasam", match_json, inset_video, sync)
        match_json: processed match JSON (dict or path; may be None) -- only used for robot tracks
                    in the sheet; sync: float offset or sync_offset() dict (None -> computed from
                    wide_video=...).  check_pose=True re-solves each camera's pose from the hub tags.
    sc.cam[w]       camera.Camera in INSET pixels (w = "red" | "blue"), pose for this match
    sc.usable[w]    False if the pose check failed (no tags / misfit) -> don't trust projections
    sc.pose_info[w] the check_station_pose() info
    sc.frames(t0, t1)  -> iterator (t_wide, red_bgr, blue_bgr) over inset frames, t_wide = wide-video s
    sc.frame_at(t_wide) -> (red_bgr, blue_bgr) (seek; slow for many calls -- iterate frames())
    sc.project(P, w)   -> Nx2 inset px (NaN behind the camera); sc.to_full(uv, w) -> 1920x1080 px
    sc.visible(P, w, margin=0) -> bool mask (in front and inside the inset)
    sc.ray(uv, w)      -> (C (3,), unit dirs Nx3) in field metres
    sc.ground(uv, w, Z=0.0) -> Nx2 field XY where the ray meets the plane Z
    sc.wide_time(t_inset) / sc.inset_time(t_wide)
    """

    def __init__(self, event_dir, match_json, inset_video, sync=None, wide_video=None, check_pose=True,
                 tag_csv=None, log=print):
        self.event_dir, self.inset_video = event_dir, inset_video
        self.match = json.load(open(match_json)) if isinstance(match_json, str) else match_json
        if sync is None:
            region, clk = layout_regions(event_dir)
            sync = sync_offset(wide_video, inset_video, log=log,
                               region=None if region == SYNC_REGION else region,
                               clock_box=None if clk == CLOCK_BOX else clk)
        self.sync = sync
        tag_csv = tag_csv or os.path.join(HERE, "field", "2026-rebuilt-welded.csv")
        self.cam, self.usable, self.pose_info = {}, {}, {}
        for w in RECTS:
            cam = load_station(event_dir, w)
            if cam is None:
                self.usable[w] = False; self.pose_info[w] = {"checked": False, "reason": "no calibration"}
                continue
            if check_pose:
                cam, info = check_station_pose(cam, inset_video, w, tag_csv, log=log)
            else:
                info = {"checked": False}
            self.cam[w], self.pose_info[w] = cam, info
            self.usable[w] = bool(info.get("usable", True))
        self.rects = rects_for(event_dir, self.cam)          # inset rectangles (RECTS for 2026wasam)

    # time
    def wide_time(self, t_inset):
        return wide_time(self.sync, t_inset)

    def inset_time(self, t_wide):
        return inset_time(self.sync, t_wide)

    # frames
    def frames(self, t0=None, t1=None, step=None):
        c = av.open(self.inset_video); s = c.streams.video[0]; s.thread_type = "AUTO"
        if t0 is not None and t0 > 2:
            c.seek(int(max(0.0, float(self.inset_time(t0)) - 2) / s.time_base), stream=s)
        last = -1e9
        try:
            for f in c.decode(s):
                if f.time is None: continue
                tw = float(self.wide_time(f.time))
                if t0 is not None and tw < t0 - 1e-6: continue
                if t1 is not None and tw > t1: break
                if step is not None and tw - last < step - 1e-3: continue
                last = tw
                im = f.to_ndarray(format="bgr24")
                yield tw, self.crop(im, "red", self.rects), self.crop(im, "blue", self.rects)
        finally:
            c.close()

    def frame_at(self, t_wide):
        for tw, r, b in self.frames(t_wide - 1e-3, t_wide + 0.1):
            return r, b
        return None, None

    @staticmethod
    def crop(full_bgr, w, rects=None):
        x0, y0, x1, y1 = (rects or RECTS)[w]
        return full_bgr[y0:y1, x0:x1]

    # geometry
    def project(self, P, w):
        cam = self.cam[w]; P = np.asarray(P, float)
        if P.shape[-1] == 2:
            P = np.concatenate([P, np.zeros(P.shape[:-1] + (1,))], -1)
        q = cam.project(P).reshape(-1, 2)
        q[((P.reshape(-1, 3) - cam.C) @ cam.R[2]) <= 0.1] = np.nan
        return q.reshape(P.shape[:-1] + (2,))

    def visible(self, P, w, margin=0):
        q = self.project(P, w).reshape(-1, 2); cam = self.cam[w]
        return (np.isfinite(q).all(1) & (q[:, 0] >= -margin) & (q[:, 0] < cam.W + margin)
                & (q[:, 1] >= -margin) & (q[:, 1] < cam.H + margin))

    def to_full(self, uv, w):
        x0, y0 = getattr(self, "rects", RECTS)[w][:2]
        return np.asarray(uv, float) + [x0, y0]

    def ray(self, uv, w):
        cam = self.cam[w]
        n = cv2.undistortPoints(np.asarray(uv, float).reshape(-1, 1, 2), cam.K, cam.D).reshape(-1, 2)
        d = np.c_[n, np.ones(len(n))] @ cam.R            # = R^T [x, y, 1]
        return cam.C.copy(), d / np.linalg.norm(d, axis=1, keepdims=True)

    def ground(self, uv, w, Z=0.0):
        return self.cam[w].img_to_plane(uv, Z)

    # robots from the processed match JSON
    def robots_at(self, t_wide):
        """[(team or alliance label, alliance, x_m, y_m, conf)] at wide-video time t_wide."""
        m = self.match
        if not m or "tracks" not in m: return []
        hz = m.get("hz", 10); i = int(round((t_wide - (m["t_start"] - 0.5)) * hz))
        out = []
        for k, tr in enumerate(m["tracks"]):
            if not (0 <= i < len(tr["xy"])): continue
            if tr.get("obs") and not tr["obs"][i]: continue
            x, y = np.array(tr["xy"][i], float) / 100.0
            conf = tr["conf"][i] if tr.get("conf") else None
            out.append((str(tr.get("team") or f"{tr['alliance']}{k}").replace("frc", ""), tr["alliance"], x, y, conf))
        return out


# ---------------------------------------------------------------- verification sheet
def _draw_geometry(im, project, scale, col=(255, 255, 255)):
    def seg(a, b, c, n=40):
        q = project(np.linspace(a, b, n)) * scale
        for i in range(n - 1):
            if np.isfinite(q[i:i + 2]).all() and np.abs(q[i:i + 2]).max() < 1e4:
                cv2.line(im, tuple(q[i].astype(int)), tuple(q[i + 1].astype(int)), c, 1, cv2.LINE_AA)
    L, Wd = CAMM.LEN, CAMM.WID
    for a, b in (((0, 0, 0), (L, 0, 0)), ((L, 0, 0), (L, Wd, 0)), ((L, Wd, 0), (0, Wd, 0)), ((0, Wd, 0), (0, 0, 0)),
                 ((L / 2, 0, 0), (L / 2, Wd, 0))):
        seg(a, b, col)
    for x in (4.028, L - 4.028):
        seg((x, 0, 0), (x, Wd, 0), (0, 200, 255))
    for hx in (4.626, 11.915):                       # hub footprint at carpet + tag band
        h = 1.194 / 2
        for z in (0.0, 1.124):
            seg((hx - h, 4.035 - h, z), (hx + h, 4.035 - h, z), (0, 255, 0)); seg((hx + h, 4.035 - h, z), (hx + h, 4.035 + h, z), (0, 255, 0))
            seg((hx + h, 4.035 + h, z), (hx - h, 4.035 + h, z), (0, 255, 0)); seg((hx - h, 4.035 + h, z), (hx - h, 4.035 - h, z), (0, 255, 0))
        cx = (hx, 4.035, 1.829)                        # hub opening centre (72 in)
        q = project(np.array([cx]))[0] * scale
        if np.isfinite(q).all(): cv2.drawMarker(im, tuple(q.astype(int)), (0, 255, 0), cv2.MARKER_CROSS, 10, 1)


def _draw_robot(im, project, scale, x, y, label, alliance, conf):
    col = (60, 60, 255) if alliance == "red" else (255, 140, 40)
    h = 0.455
    base = np.array([[x - h, y - h], [x + h, y - h], [x + h, y + h], [x - h, y + h]])
    for z0, z1 in ((0.0, 0.0), (0.3, 0.3)):
        q = project(np.c_[base, np.full(4, z0)]) * scale
        if not np.isfinite(q).all(): return
        cv2.polylines(im, [q.astype(np.int32)], True, col, 1, cv2.LINE_AA)
    q0 = project(np.c_[base, np.zeros(4)]) * scale; q1 = project(np.c_[base, np.full(4, 0.3)]) * scale
    for a, b in zip(q0, q1):
        cv2.line(im, tuple(a.astype(int)), tuple(b.astype(int)), col, 1, cv2.LINE_AA)
    top = project(np.array([[x, y, 0.75]]))[0] * scale
    txt = label + ("" if conf is None else f" {conf:.2f}")
    cv2.putText(im, txt, tuple((top - [20, 0]).astype(int)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(im, txt, tuple((top - [20, 0]).astype(int)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1, cv2.LINE_AA)


def sheet(event_dir, match_json, wide_video, inset_video, out, times=None, sync=None, log=print):
    """Verification sheet: per time a row [overhead frame | red station | blue station] with the
    field geometry and the tracked robots (match JSON, team labels) projected into all three."""
    sc = StationCams(event_dir, match_json, inset_video, sync, wide_video=wide_video, log=log)
    m = sc.match
    cj = json.load(open(os.path.join(event_dir, "camera.json")))
    if m and m.get("camera_pose"): cj.update(m["camera_pose"])
    over = CAMM.Camera(cj)
    if not times:
        t0 = (m["t_start"] if m else 5.0)
        times = [t0 + 8, t0 + 45, t0 + 100, t0 + 150]
    rw = _Reader(wide_video); rows = []
    for tw in sorted(times):
        red, blue = sc.frame_at(tw)
        if red is None: continue
        wide = rw.at(tw)
        so = 0.5; ow = cv2.resize(wide, None, fx=so, fy=so, interpolation=cv2.INTER_AREA)
        _draw_geometry(ow, over.project, so, (200, 200, 200))
        robots = sc.robots_at(tw)
        for lab, al, x, y, cf in robots:
            _draw_robot(ow, over.project, so, x, y, lab, al, cf)
        panels = [ow]
        for w, im in (("red", red), ("blue", blue)):
            s = 540 / im.shape[0]
            p = cv2.resize(im, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
            if w in sc.cam:
                prj = lambda P, w=w: sc.project(P, w)
                _draw_geometry(p, prj, s)
                for lab, al, x, y, cf in robots:
                    if sc.visible(np.array([[x, y, 0.2]]), w, margin=40)[0]:
                        _draw_robot(p, prj, s, x, y, lab, al, cf)
                inf = sc.pose_info[w]
                txt = f"{w}: usable={sc.usable[w]} tags={inf.get('tags')} moved={inf.get('moved_px')}px applied={inf.get('applied', False)}"
                cv2.putText(p, txt, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3, cv2.LINE_AA)
                cv2.putText(p, txt, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
            panels.append(p)
        cv2.putText(panels[0], f"wide t={tw:.2f}s  inset t={float(sc.inset_time(tw)):.3f}s", (8, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
        rows.append(np.hstack(panels))
    rw.close()
    wmax = max(r.shape[1] for r in rows)
    rows = [np.pad(r, ((0, 0), (0, wmax - r.shape[1]), (0, 0))) for r in rows]
    cv2.imwrite(out, np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 85])
    log(f"wrote {out}")
    return sc


# ---------------------------------------------------------------- CLI
def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("sync"); a.add_argument("wide"); a.add_argument("inset"); a.add_argument("--verify", action="store_true")
    a = sub.add_parser("check"); a.add_argument("event_dir"); a.add_argument("inset")
    a = sub.add_parser("sheet"); a.add_argument("event_dir"); a.add_argument("match_json"); a.add_argument("wide")
    a.add_argument("inset"); a.add_argument("out"); a.add_argument("times", nargs="*", type=float)
    a.add_argument("--offset", type=float)
    a = sub.add_parser("calibrate", help="once per event: KEY=WIDE,INSET,MATCH_JSON ... -> station_*.json")
    a.add_argument("event_dir"); a.add_argument("items", nargs="+"); a.add_argument("--out", default=None)
    a.add_argument("--step", type=float, default=2.0)
    g = ap.parse_args(argv)
    tag_csv = os.path.join(HERE, "field", "2026-rebuilt-welded.csv")
    if g.cmd == "sync":
        r = sync_offset(g.wide, g.inset, log=print)
        if g.verify:
            r["verify"] = verify_sync(g.wide, g.inset, r)
        print(json.dumps(r, default=float, indent=1))
    elif g.cmd == "check":
        for w in RECTS:
            check_station_pose(load_station(g.event_dir, w), g.inset, w, tag_csv)
    elif g.cmd == "sheet":
        sheet(g.event_dir, g.match_json, g.wide, g.inset, g.out, g.times or None, g.offset)
    elif g.cmd == "calibrate":
        colls = []
        for it in g.items:
            key, rest = it.split("=", 1); wide, inset, mj = rest.split(",")
            m = json.load(open(mj))
            cj = json.load(open(os.path.join(g.event_dir, "camera.json"))); cj.update(m.get("camera_pose", {}))
            sy = sync_offset(wide, inset, log=print)
            if not sy["ok"]:
                print(f"  {key}: sync not confident, skipped"); continue
            d = collect_pairs(wide, inset, sy, CAMM.Camera(cj), step=g.step, log=print)
            colls.append((key, d))
        out = g.out or g.event_dir
        for w in RECTS:
            cam, rep = fit_station(colls, w, tag_csv)
            cam["calib"] = rep
            json.dump(cam, open(os.path.join(out, f"station_{w}.json"), "w"), indent=1)
            print("wrote", os.path.join(out, f"station_{w}.json"), {k: v for k, v in rep.items() if k != "matches"})


if __name__ == "__main__":
    _main(sys.argv[1:])
    os._exit(0)          # skip libapriltag's destructor (intermittent segfault, see process.py)
