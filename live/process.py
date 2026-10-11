"""One match video -> six field-space trajectories + per-robot features (JSON).

    python live/process.py VIDEO CAMERA.json MODEL.onnx OUT.json

Steps: camera pose check against AprilTags (re-solve pose if the camera was
nudged), detection at 30 Hz over the field ROI, match-start detection from robot
motion, tracking (live/fuse.py), and export of a compact per-match JSON.
"""
import os, sys, json, time, math
import numpy as np, cv2, av

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
import camera as CAMM
from detector import Detector, alliance

FPS = 30.0
AUTO_S, PAUSE_S, TELEOP_S = 20.0, 3.0, 140.0          # 2026 REBUILT
MATCH_S = AUTO_S + PAUSE_S + TELEOP_S
CONF_KEEP = float(os.environ.get("CONF_KEEP", "0.10"))  # saved raw
CONF_TRACK = float(os.environ.get("CONF_TRACK", "0.30"))
START_FALLBACK = float(os.environ.get("START_FALLBACK", "6.5"))
SCAN_T1 = float(os.environ.get("SCAN_T1", "190"))
OUT_HZ = 10                                             # web export rate
HB, SB = 12, 3
NO_FEED_LEVEL = 20                                      # field mean below this = feed lost
CLOCK_BOX = tuple(int(v) for v in os.environ.get("CLOCK_BOX", "912,72,1008,116").split(","))   # x0,y0,x1,y1


def hsv_desc(img, x0, y0, x1, y1):
    """Same descriptor as src/appear.py (12x3 H-S histogram + 4 V bins)."""
    h, w = img.shape[:2]
    x0, x1 = max(0, int(x0)), min(w, int(x1)); y0, y1 = max(0, int(y0)), min(h, int(y1))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return np.zeros(HB * SB + 4, np.float32)
    c = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    H, S, V = c[..., 0], c[..., 1], c[..., 2]
    m = (V > 35) & (V < 250)
    if m.sum() < 20:
        return np.zeros(HB * SB + 4, np.float32)
    hh = np.histogram2d(H[m], S[m], bins=[HB, SB], range=[[0, 180], [0, 256]])[0].ravel()
    vv = np.histogram(V[m], bins=4, range=(0, 256))[0]
    d = np.r_[hh, vv].astype(np.float32)
    return d / max(d.sum(), 1)


# ---------------------------------------------------------------- camera check
_TAGDET = None


def check_pose(video, cam, tag_csv, log=print):
    """AprilTags near their predicted pixels in ~28 frames -> re-solve pose
    (intrinsics fixed) when the camera moved.  Returns (cam, info)."""
    try:
        from pupil_apriltags import Detector as TagDet
    except ImportError:
        return cam, {"checked": False}
    import csv
    IN = 0.0254
    tags = {int(r["ID"]): np.array([float(r["X"]), float(r["Y"]), float(r["Z"])]) * IN
            for r in csv.DictReader(open(tag_csv, encoding="utf-8-sig"))}
    c = av.open(video); s = c.streams.video[0]; s.thread_type = "AUTO"
    frames, nxt = [], 8.0
    for f in c.decode(s):
        if f.time is None or f.time < nxt: continue
        g = cv2.cvtColor(f.to_ndarray(format="bgr24"), cv2.COLOR_BGR2GRAY); nxt += 6.0
        if g[::16, ::16].mean() > NO_FEED_LEVEL: frames.append(g)
        if nxt > 170: break
    c.close()
    if len(frames) < 5:
        return cam, {"checked": False}
    # per-frame detection (a median plate blurs the tiny tags), median of the hits
    # one detector for the process lifetime: pupil_apriltags' destructor intermittently
    # segfaults (libapriltag, seen twice in 40 runs right after the pose check)
    global _TAGDET
    if _TAGDET is None:
        _TAGDET = TagDet(families="tag36h11", quad_decimate=1.0, decode_sharpening=0.25)
    det = _TAGDET
    hits = {}
    for g in frames:
        for tid, P in tags.items():
            u, v = cam.project(P[None])[0]
            if not (40 <= u < cam.W - 40 and 40 <= v < cam.H - 40): continue
            x0, y0 = int(u) - 40, int(v) - 40
            roi = cv2.resize(g[y0:y0 + 80, x0:x0 + 80], None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
            for d in det.detect(roi):
                if d.tag_id == tid and d.hamming == 0 and d.decision_margin > 15:
                    hits.setdefault(tid, []).append(d.center / 4 + [x0, y0])
    found = {k: np.median(v, 0) for k, v in hits.items() if len(v) >= 3}
    if len(found) < 3:
        log(f"  pose check: only {len(found)} tags, keeping calibration")
        return cam, {"checked": True, "tags": len(found), "moved_px": None}
    ids = sorted(found)
    P = np.array([tags[i] for i in ids]); uv = np.array([found[i] for i in ids])
    err = np.linalg.norm(cam.project(P) - uv, axis=1)
    info = {"checked": True, "tags": len(ids), "moved_px": round(float(np.median(err)), 2)}
    if np.median(err) > 2.0:
        plate = np.median(np.stack(frames), 0).astype(np.uint8)
        cam2 = resolve_pose(cam, P, uv, plate)
        e2 = np.linalg.norm(cam2.project(P) - uv, axis=1)
        # accept only if the tags fit AND the centre line lines up at least as well
        s_old, s_new = centreline_score(cam, plate), centreline_score(cam2, plate)
        info.update(resolved_px=round(float(np.median(e2)), 2), line_old_px=round(s_old, 1), line_new_px=round(s_new, 1))
        if np.median(e2) < 0.5 * np.median(err) and s_new <= max(s_old, 3.0):
            cam = cam2; info["applied"] = True
    log(f"  pose check: {info}")
    return cam, info


def centreline_pixels(cam, plate, search=25):
    """White centre-line pixels near where `cam` predicts it, from a robot-free plate."""
    Y = np.linspace(0.5, CAMM.WID - 0.5, 70)
    pred = cam.project(np.c_[np.full(70, CAMM.LEN / 2), Y, np.zeros(70)])
    g = cv2.GaussianBlur(plate, (3, 3), 0).astype(float)
    pts = []
    for u, v in pred:
        u, v = int(round(u)), int(round(v))
        if not (search <= u < g.shape[1] - search and 0 <= v < g.shape[0]): continue
        row = g[v, u - search:u + search + 1]
        k = int(row.argmax())
        if row[k] - np.median(row) < 50: continue          # no clear white line here (ball/robot)
        w = np.clip(row - np.median(row), 0, None); xs = np.arange(len(row))
        w[np.abs(xs - k) > 3] = 0
        pts.append([u - search + float((xs * w).sum() / w.sum()), float(v)])
    return np.array(pts).reshape(-1, 2)


def resolve_pose(cam, P, uv, plate=None):
    """Camera was nudged: re-fit its pose from the tags plus the white centre line
    (tags alone may all sit on one side), intrinsics fixed, position held near the
    calibrated one by a soft prior (a bump mostly re-aims, it doesn't relocate)."""
    from scipy.optimize import least_squares
    C0 = cam.C.copy()
    line = np.zeros((0, 2))
    def proj(x, X):
        rv, C = x[:3], x[3:]
        R, _ = cv2.Rodrigues(rv); tv = -R @ C
        q, _ = cv2.projectPoints(X.reshape(-1, 1, 3), rv, tv, cam.K, cam.D)
        return q.reshape(-1, 2)
    Yl = np.linspace(-0.3, CAMM.WID + 0.3, 200)
    LW = np.c_[np.full(200, CAMM.LEN / 2), Yl, np.zeros(200)]
    def res(x):
        r = [(proj(x, P) - uv).ravel()]
        if len(line):
            poly = proj(x, LW)
            d = np.abs(np.interp(line[:, 1], poly[:, 1], poly[:, 0]) - line[:, 0])   # line is near-vertical
            r.append(d * np.sqrt(2 * len(P) / max(len(line), 1)))                    # line ~ as much weight as the tags
        r.append((x[3:] - C0) / 0.25)                                              # camera position prior, 25 cm
        return np.concatenate(r)
    x = np.r_[cam.rvec, C0]
    for _ in range(2):          # fit, then re-extract the line near the improved guess
        x = least_squares(res, x, loss="soft_l1", f_scale=2.0).x
        if plate is not None:
            R, _ = cv2.Rodrigues(x[:3]); tmp = dict(cam.raw); tmp["rvec"] = x[:3].tolist(); tmp["tvec"] = (-R @ x[3:]).tolist()
            line = centreline_pixels(CAMM.Camera(tmp), plate)
    x = least_squares(res, x, loss="soft_l1", f_scale=2.0).x
    rv = x[:3]; R, _ = cv2.Rodrigues(rv); tv = -R @ x[3:]
    new = dict(cam.raw); new["rvec"] = np.ravel(rv).tolist(); new["tvec"] = np.ravel(tv).tolist()
    return CAMM.Camera(new)


def centreline_score(cam, plate):
    """Median pixel offset between the predicted and the actual white centre line
    (lower = the calibration lines up).  inf if the line isn't found."""
    pts = centreline_pixels(cam, plate)
    if len(pts) < 10: return float("inf")
    Y = np.linspace(-0.3, CAMM.WID + 0.3, 200)
    poly = cam.project(np.c_[np.full(200, CAMM.LEN / 2), Y, np.zeros(200)])
    return float(np.median(np.abs(np.interp(pts[:, 1], poly[:, 1], poly[:, 0]) - pts[:, 0])))


# ---------------------------------------------------------------- detection
def _hue_is(h, which):
    return (h < 12 or h > 165) if which == "red" else (95 <= h <= 135)


def swap_check(cam):
    """Broadcast glitches sometimes swap red and blue for a few frames (RGB<->BGR),
    which would flip every robot's alliance.  The red and blue bumps are static and
    always visible: if red reads blue and blue reads red, the frame is swapped."""
    pts = cam.project(np.array([[cam_len() - 4.64, 6.6, 0.1], [4.64, 6.6, 0.1]])).astype(int)
    def swapped(im):
        hs = []
        for u, v in pts:
            p = cv2.cvtColor(im[v - 5:v + 5, u - 5:u + 5], cv2.COLOR_BGR2HSV).reshape(-1, 3)
            p = p[p[:, 1] > 90]
            if len(p) < 20: return False
            hs.append(float(np.median(p[:, 0])))
        return _hue_is(hs[0], "blue") and _hue_is(hs[1], "red")
    return swapped


def cam_len():
    return CAMM.LEN


def detect_video(video, cam, model, t1=SCAN_T1, log=print, hooks=(), ocr=None):
    """Robot detection at FPS (30 Hz) over the field ROI.  `hooks` get EVERY decoded
    frame (full video rate, colour-swap fixed, no-feed frames skipped) via
    hook.push(video_frame_idx, t, bgr) -- the fuel tracker needs 60 fps.
    `ocr`: optional ocr.OcrSampler -- reads robots' bumper numbers from the detection
    frames (its reads index the returned detection rows); the caller closes it."""
    x0, y0, x1, y1 = cam.roi()
    swapped = swap_check(cam); n_swap = 0
    feed = []                       # per processed frame: True if the field camera is live
    c = av.open(video); s = c.streams.video[0]; s.thread_type = "AUTO"
    rows, chas, body = [], [], []
    clock = []                      # (t, binarised clock digits) for the first 45 s
    n = 0; nxt = 0.0; t0 = time.time(); k = -1
    for f in c.decode(s):
        k += 1
        if f.time is None: continue
        if f.time > t1: break
        sample = f.time + 1e-6 >= nxt
        if not sample and not hooks: continue
        im = f.to_ndarray(format="bgr24")
        live = float(im[y0:y1:16, x0:x1:16].mean()) > NO_FEED_LEVEL
        if live and swapped(im):
            im = np.ascontiguousarray(im[:, :, ::-1]); n_swap += 1
        if live:
            for h in hooks: h.push(k, float(f.time), im)
        if not sample: continue
        nxt = (n + 1) / FPS
        feed.append(live)
        if not live:                # camera feed missing (black field): nothing to detect
            n += 1; continue
        if f.time < 45:
            cx0, cy0, cx1, cy1 = CLOCK_BOX
            g = cv2.cvtColor(im[cy0:cy1, cx0:cx1], cv2.COLOR_BGR2GRAY)
            clock.append((float(f.time), g))
        crop = im[y0:y1, x0:x1]
        b, sc = model(crop, conf=CONF_KEEP)
        if ocr is not None and len(b):
            ocr.frame(float(f.time), im, b + [x0, y0, x0, y0], np.asarray(sc), len(rows))
        for j in range(len(b)):
            bx = b[j] + [x0, y0, x0, y0]
            rows.append([n, float(f.time), -1.0, *bx, float(sc[j]), alliance(im, bx)])
            hgt = bx[3] - bx[1]
            chas.append(hsv_desc(im, *bx))
            body.append(hsv_desc(im, bx[0], bx[1], bx[2], bx[1] + 0.6 * hgt))
        n += 1
        if n % 900 == 0:
            log(f"  {n} frames t={f.time:.0f}s {len(rows)} dets {n / (time.time() - t0):.1f} fps")
    c.close()
    if n_swap:
        log(f"  fixed {n_swap} colour-swapped frames")
    return (np.array(rows, float).reshape(-1, 9), np.array(chas, np.float32).reshape(-1, HB * SB + 4),
            np.array(body, np.float32).reshape(-1, HB * SB + 4), n, clock, np.array(feed, bool))


def clock_start(clock, log=print):
    """Auto start from the broadcast clock: once the match starts the clock digits
    change exactly once per second.  Find the first run of >= 4 changes spaced
    1.0 s apart; the first of them is 1 s after the start.  None if no overlay."""
    if len(clock) < 60:
        return None
    ts = np.array([t for t, _ in clock])
    ok = np.array([g.mean() > 150 for _, g in clock])           # white clock box present
    bins = [(g < 110) for _, g in clock]                        # dark digit pixels
    d = np.array([0] + [int((bins[i] ^ bins[i - 1]).sum()) if ok[i] and ok[i - 1] else 0
                        for i in range(1, len(bins))])
    ch = ts[d > 25]
    ev = []                                                    # merge changes within 0.2 s
    for t in ch:
        if not ev or t - ev[-1] > 0.2: ev.append(t)
    ev = np.array(ev)
    for i in range(len(ev) - 3):
        gaps = np.diff(ev[i:i + 4])
        if np.all(np.abs(gaps - 1.0) < 0.12):
            # must be the PRE-MATCH clock that starts ticking: visible and static for
            # >= 2 s before the first tick (an overlay that appears mid-match is not)
            pre = (ts > ev[i] - 2.2) & (ts < ev[i] - 0.15)
            if pre.sum() < 20 or not ok[pre].all() or d[pre].max() > 25:
                return None
            return float(ev[i] - 1.0)
    return None


def match_start(det, xy, cov, n, log=print, fallback=None):
    """Auto start from robot motion, seconds into the video: the first moment >= 3
    robots move at once (sustained 1 s).  Uses pure tracklets so detection flicker
    (which moves nothing) cannot trigger it.  Pre-match robots are parked."""
    import track as T
    moving = np.zeros(n)
    for t in T.build_tracklets(det, xy, cov):
        fr = np.array(t.fr); xs = np.array(t.xs)
        if len(fr) < 10: continue
        k = int(FPS * 0.5)
        for i in range(k, len(fr)):
            dt = (fr[i] - fr[i - k]) / FPS
            if dt > 0 and np.linalg.norm(xs[i] - xs[i - k]) / dt > 0.6:
                moving[fr[i] - k // 2] += 1
    run = np.convolve(moving >= 3, np.ones(int(FPS)), mode="valid")       # frames in a 1 s window
    lo, hi = int(0.5 * FPS), int(30 * FPS)
    cand = np.where(run[lo:hi] >= 0.7 * FPS)[0]
    if len(cand) == 0:
        fb = START_FALLBACK if fallback is None else fallback
        log(f"  match start: no motion onset found, fallback {fb:.2f}s")
        return fb, False
    f = lo + cand[0]
    while f > 0 and moving[f] >= 2: f -= 1                               # walk back to the first mover
    t = f / FPS - 0.3                                                    # reaction latency
    return float(max(t, 0.0)), True


# ---------------------------------------------------------------- stats
def zone_of(x, LEN):
    # alliance zones end at the hub line; 2026 hubs sit at x=4.63 / 11.92 m
    if x < 4.0: return "blue_zone"
    if x > LEN - 4.0: return "red_zone"
    return "neutral"


def track_stats(xy, obs, alliance_name, t_auto_end, LEN, live=None):
    """Per-robot match stats.  Only frames where the camera feed was live count;
    distance and speed are scaled up to the full match so matches stay comparable."""
    dt = 1 / FPS
    live = np.ones(len(xy), bool) if live is None else live[:len(xy)]
    v = np.r_[0, np.linalg.norm(np.diff(xy, axis=0), axis=1)] / dt
    v = np.minimum(v, 5.5)
    ok = live & np.r_[True, live[:-1]]
    fa = int(t_auto_end * FPS)
    cover, acover = max(ok.mean(), 1e-6), max(ok[:fa].mean(), 1e-6)
    za = np.array([zone_of(x, LEN) for x in xy[:, 0]])[live]
    own = "red_zone" if alliance_name == "red" else "blue_zone"
    opp = "blue_zone" if alliance_name == "red" else "red_zone"
    z = lambda name: round(float((za == name).mean()), 3) if len(za) else None
    return dict(distance_m=round(float(v[ok].sum() * dt / cover), 1),
                auto_distance_m=round(float(v[:fa][ok[:fa]].sum() * dt / acover), 1) if ok[:fa].any() else None,
                max_speed=round(float(np.percentile(v[ok], 99)), 2) if ok.any() else None,
                observed=round(float(obs[live].mean()), 3) if live.any() else 0.0,
                time_own_zone=z(own), time_neutral=z("neutral"), time_opp_zone=z(opp))


# ---------------------------------------------------------------- main
def process(video, cam_path, onnx, tag_csv=None, log=print, start_hint=None, pose_guess=None,
            teams=None, hooks=(), dump=None, extra_reads=None):
    """start_hint: typical match-start offset for this kind of upload, used only
    when neither the clock nor robot motion gives one.  pose_guess: {rvec, tvec} of
    the camera as last seen (a bump persists across matches).
    teams: {"red": [3 team keys], "blue": [3 team keys]} in TBA station order.  With
    it, bumper numbers are read during detection and every output track is one team
    (tracks ordered red 1-3, blue 1-3; .team, .team_conf, per-frame .conf); without it
    (or if OCR can't run) tracks are the unlabelled v1 output.
    hooks: frame hooks passed to detect_video (fuel tracker).  dump: pickle path for
    the post-detection state (development: re-run finish() without the video).
    extra_reads: bumper reads from other cameras for the identity solve -- an array of
    field-located rows (fuse.attribute_extra) or a callable (st, teams, log) -> rows run after
    prepare() (it gets the overhead detections; e.g. station_ocr.extra_reads_fn(...)).
    Ignored without teams; None = overhead reads only (unchanged behaviour)."""
    raw = json.load(open(cam_path))
    if pose_guess:
        raw = dict(raw, rvec=pose_guess["rvec"], tvec=pose_guess["tvec"])
    cam = CAMM.Camera(raw)
    pose = {"checked": False}
    if tag_csv:
        cam, pose = check_pose(video, cam, tag_csv, log)
    CAMM.install_geom_shim(cam)

    team_list = None
    if teams and len(teams.get("red") or []) == 3 and len(teams.get("blue") or []) == 3:
        team_list = list(teams["red"]) + list(teams["blue"])
    model = Detector(onnx)
    sampler = None
    if team_list:
        try:
            import ocr as OCRM
            sampler = OCRM.OcrSampler(cam, team_list, log)
        except Exception as e:
            log(f"  bumper OCR unavailable ({e!r}): tracking without team identity")
    t0 = time.time()
    det, chas, body, n, clock, feed = detect_video(video, cam, model, log=log, hooks=hooks, ocr=sampler)
    reads = sampler.close() if sampler else np.zeros((0, 7))
    log(f"  detection: {n} frames, {len(det)} raw dets in {time.time() - t0:.0f}s")
    st = prepare(det, chas, body, n, clock, feed, reads, cam, log, start_hint)
    st.update(camera_pose=dict(rvec=cam.rvec.tolist(), tvec=cam.tvec.tolist()), pose=pose,
              cam_raw=cam.raw, teams=team_list, ocr_ok=sampler is not None)
    if extra_reads is not None and team_list and sampler is not None:
        try:
            st["extra_reads"] = np.asarray(extra_reads(st, team_list, log) if callable(extra_reads) else extra_reads, float)
        except Exception as e:                                  # never lose the match over extra reads
            log(f"  extra reads failed ({e!r}): overhead reads only")
    if dump:
        import pickle
        pickle.dump(st, open(dump, "wb"))
    return finish(st, log)


def prepare(det, chas, body, n, clock, feed, reads, cam, log=print, start_hint=None):
    """Raw detections -> tracking inputs inside the match window (+ match start)."""
    idx = np.arange(len(det))
    keep = det[:, 7] >= CONF_TRACK
    d, ch, bo, idx = det[keep], chas[keep], body[keep], idx[keep]
    xy = cam.box_to_field(d[:, 3:7])
    ok = np.isfinite(xy).all(1) & CAMM.in_field(xy)
    d, ch, bo, xy, idx = d[ok], ch[ok], bo[ok], xy[ok], idx[ok]
    nms = CAMM.field_nms(d[:, 0], xy, d[:, 7])
    d, ch, bo, xy, idx = d[nms], ch[nms], bo[nms], xy[nms], idx[nms]

    t_clock = clock_start(clock, log)
    t_motion, found = match_start(d, xy, cam.cov(xy, su=2.0, sv=2.5), n, log, fallback=start_hint)
    if t_clock is not None and 0 <= t_clock < 40:
        t_start, found, how = t_clock, True, "clock"
    else:
        t_start, how = t_motion, "motion" if found else "default"
    log(f"  match start {t_start:.2f}s from {how} (clock {t_clock}, motion {t_motion:.2f})")
    f0 = max(0, int(round((t_start - 0.5) * FPS)))
    nf = min(n - f0, int(math.ceil((MATCH_S + 1.0) * FPS)))
    fw = np.ones(nf, bool); seg = feed[f0:f0 + nf]; fw[:len(seg)] = seg
    w = (d[:, 0] >= f0) & (d[:, 0] < f0 + nf)
    d, ch, bo, xy, idx = d[w].copy(), ch[w], bo[w], xy[w], idx[w]
    d[:, 0] -= f0
    # reads: raw detection row -> kept detection (a read on an NMS-suppressed duplicate
    # moves to the kept detection of that robot in the same frame)
    rd = np.zeros((0, 7))
    if len(reads):
        lut = np.full(len(det), -1); lut[idx] = np.arange(len(idx))
        out = []
        byf = {}
        for k, f in enumerate(d[:, 0].astype(int)): byf.setdefault(f, []).append(k)
        for r in reads:
            g = lut[int(r[0])]
            if g < 0:
                f = int(det[int(r[0]), 0]) - f0
                if not (0 <= f < nf) or det[int(r[0]), 7] < 0.1: continue
                p = cam.box_to_field(det[int(r[0]), 3:7][None])[0]
                ks = byf.get(f, [])
                if not ks or not np.isfinite(p).all(): continue
                dd = np.linalg.norm(xy[ks] - p, axis=1)
                if dd.min() > 0.7: continue
                g = ks[int(dd.argmin())]
            out.append([g, *r[1:]])
        rd = np.array(out, float).reshape(-1, 7)
    return dict(d=d, ch=ch, bo=bo, xy=xy, reads=rd, nf=nf, fw=fw, f0=f0, t_start=t_start, found=found,
                how=how, n_raw_reads=len(reads))


def finish(st, log=print):
    """Tracking (+ identity) and the per-match JSON from prepare()'s state."""
    cam = CAMM.Camera(st["cam_raw"]); CAMM.install_geom_shim(cam)
    import fuse, track as T
    T.DT = 1 / FPS
    d, ch, bo, xy, nf, fw = st["d"], st["ch"], st["bo"], st["xy"], st["nf"], st["fw"]
    cov = cam.cov(xy, su=2.0, sv=2.5)
    hs = cam.heights(d[:, 3:7], xy)
    chn = ch / np.maximum(ch.sum(1, keepdims=True), 1e-9)
    team_list = st.get("teams")
    identity = None
    tracks = None
    if team_list and st.get("ocr_ok"):
        t0 = time.time()
        try:
            tracks, identity = fuse.run_labeled(d, xy, cov, hs, chn, nf, CAMM.LEN, CAMM.WID, st["reads"],
                                                team_list, log=log, extra_reads=st.get("extra_reads"))
            identity["seconds"] = round(time.time() - t0, 1)
            identity["n_reads_raw"] = int(st.get("n_raw_reads", 0))
            v0 = st["t_start"] - 0.5
            for c in identity.get("corrections", []):          # add video time for review
                c["t_video"] = round(v0 + c["frame"] / OUT_HZ, 2)
        except Exception as e:                                  # never lose the match over identity
            import traceback; traceback.print_exc()
            log(f"  labelled tracking failed ({e!r}): falling back to unlabelled tracks")
            tracks, identity = None, None
    if tracks is None:
        tracks = fuse.run(d, xy, cov, hs, chn, nf, CAMM.LEN, CAMM.WID, log=log)

    # per-robot appearance for cross-match identity: body histogram from frames
    # where the robot is isolated (>1.5 m from everyone), so it is not polluted
    fr = d[:, 0].astype(int)
    out_tracks = []
    t_auto_end = 0.5 + AUTO_S
    step = int(FPS // OUT_HZ)
    for k, tr in enumerate(tracks):
        di = tr["det_idx"]
        iso = []
        for i in di:
            others = xy[(fr == fr[i])]
            if (np.linalg.norm(others - xy[i], axis=1) < 1.5).sum() <= 1: iso.append(i)
        sel = np.array(iso if len(iso) >= 20 else di, int)
        hist = bo[sel].mean(0) if len(sel) else np.zeros(bo.shape[1])
        wpx = np.median(d[sel, 5] - d[sel, 3]) if len(sel) else np.nan
        row = dict(
            alliance=tr["alliance"], idx=k,
            xy=np.round(tr["xy"][::step] * 100).astype(int).tolist(),      # cm @ OUT_HZ
            obs=tr["obs"][::step].astype(int).tolist(),
            stats=track_stats(tr["xy"], tr["obs"], tr["alliance"], t_auto_end, CAMM.LEN, fw),
            feat=dict(height=None if not np.isfinite(tr["height"]) else round(tr["height"], 3),
                      hist=np.round(hist, 4).tolist(), n_iso=len(iso),
                      box_w_px=None if not np.isfinite(wpx) else float(wpx),
                      start_xy=np.round(tr["xy"][int(0.5 * FPS)], 2).tolist(),
                      auto_end_xy=np.round(tr["xy"][min(len(tr["xy"]) - 1, int(t_auto_end * FPS))], 2).tolist(),
                      end_xy=np.round(tr["xy"][-1], 2).tolist()))
        if "team" in tr:
            row.update(team=tr["team"], team_conf=round(float(tr["team_conf"]), 3),
                       conf=np.round(tr["conf"][::step], 2).tolist())
        out_tracks.append(row)
    gaps, i = [], 0
    while i < nf:
        if not fw[i]:
            j = i
            while j < nf and not fw[j]: j += 1
            gaps.append([round(i / FPS, 2), round(j / FPS, 2)]); i = j
        else:
            i += 1
    if gaps: log(f"  camera feed missing for {(~fw).sum() / FPS:.0f}s: {gaps}")
    res = dict(t_start=round(st["t_start"], 2), start_detected=st["found"], start_source=st["how"],
               camera_pose=st["camera_pose"],
               feed_ok=round(float(fw.mean()), 3), feed_gaps=gaps,
               auto_feed_ok=round(float(fw[:int(t_auto_end * FPS)].mean()), 3), frames=nf, hz=OUT_HZ,
               t_auto_end=t_auto_end, t_teleop_start=0.5 + AUTO_S + PAUSE_S,
               pose=st["pose"], n_dets=int(len(d)), tracks=out_tracks,
               field=dict(length=CAMM.LEN, width=CAMM.WID))
    if identity is not None:
        res["identity"] = identity
    return res


if __name__ == "__main__":
    video, cam_path, onnx, out = sys.argv[1:5]
    tag_csv = sys.argv[5] if len(sys.argv) > 5 else None
    teams = None
    if len(sys.argv) > 6:          # red1,red2,red3 blue1,blue2,blue3 (team keys or numbers)
        key = lambda s: s if s.startswith("frc") else "frc" + s
        teams = {"red": [key(s) for s in sys.argv[6].split(",")], "blue": [key(s) for s in sys.argv[7].split(",")]}
    res = process(video, cam_path, onnx, tag_csv, teams=teams)
    json.dump(res, open(out, "w"))
    print("wrote", out)
