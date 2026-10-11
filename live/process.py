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
def check_pose(video, cam, tag_csv, log=print):
    """Median plate -> AprilTags near their predicted pixels -> re-solve pose
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
        frames.append(f.to_ndarray(format="bgr24")); nxt += 6.0
        if nxt > 170: break
    c.close()
    if len(frames) < 5:
        return cam, {"checked": False}
    plate = np.median(np.stack(frames), 0).astype(np.uint8)
    g = cv2.cvtColor(plate, cv2.COLOR_BGR2GRAY)
    det = TagDet(families="tag36h11", quad_decimate=1.0, decode_sharpening=0.8)
    found = {}
    for tid, P in tags.items():
        u, v = cam.project(P[None])[0]
        if not (40 <= u < cam.W - 40 and 40 <= v < cam.H - 40): continue
        x0, y0 = int(u) - 40, int(v) - 40
        roi = cv2.resize(g[y0:y0 + 80, x0:x0 + 80], None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        for d in det.detect(roi):
            if d.tag_id == tid and d.hamming == 0 and d.decision_margin > 20:
                found[tid] = d.center / 4 + [x0, y0]
    if len(found) < 3:
        log(f"  pose check: only {len(found)} tags, keeping calibration")
        return cam, {"checked": True, "tags": len(found), "moved_px": None}
    ids = sorted(found)
    P = np.array([tags[i] for i in ids]); uv = np.array([found[i] for i in ids])
    err = np.linalg.norm(cam.project(P) - uv, axis=1)
    info = {"checked": True, "tags": len(ids), "moved_px": round(float(np.median(err)), 2)}
    if np.median(err) > 2.0 and len(ids) >= 4:
        ok, rv, tv = cv2.solvePnP(P, uv, cam.K, cam.D, cam.rvec.copy(), cam.tvec.copy(),
                                  useExtrinsicGuess=True, flags=cv2.SOLVEPNP_ITERATIVE)
        if ok:
            new = dict(cam.raw); new["rvec"] = rv.ravel().tolist(); new["tvec"] = tv.ravel().tolist()
            cam2 = CAMM.Camera(new)
            e2 = np.linalg.norm(cam2.project(P) - uv, axis=1)
            if np.median(e2) < np.median(err):
                info["resolved_px"] = round(float(np.median(e2)), 2); cam = cam2
    log(f"  pose check: {info}")
    return cam, info


# ---------------------------------------------------------------- detection
def detect_video(video, cam, model, t1=SCAN_T1, log=print):
    x0, y0, x1, y1 = cam.roi()
    c = av.open(video); s = c.streams.video[0]; s.thread_type = "AUTO"
    rows, chas, body = [], [], []
    clock = []                      # (t, binarised clock digits) for the first 45 s
    n = 0; nxt = 0.0; t0 = time.time()
    for f in c.decode(s):
        if f.time is None: continue
        if f.time > t1: break
        if f.time + 1e-6 < nxt: continue
        nxt = (n + 1) / FPS
        im = f.to_ndarray(format="bgr24")
        if f.time < 45:
            cx0, cy0, cx1, cy1 = CLOCK_BOX
            g = cv2.cvtColor(im[cy0:cy1, cx0:cx1], cv2.COLOR_BGR2GRAY)
            clock.append((float(f.time), g))
        crop = im[y0:y1, x0:x1]
        b, sc = model(crop, conf=CONF_KEEP)
        for k in range(len(b)):
            bx = b[k] + [x0, y0, x0, y0]
            rows.append([n, float(f.time), -1.0, *bx, float(sc[k]), alliance(im, bx)])
            hgt = bx[3] - bx[1]
            chas.append(hsv_desc(im, *bx))
            body.append(hsv_desc(im, bx[0], bx[1], bx[2], bx[1] + 0.6 * hgt))
        n += 1
        if n % 900 == 0:
            log(f"  {n} frames t={f.time:.0f}s {len(rows)} dets {n / (time.time() - t0):.1f} fps")
    c.close()
    return (np.array(rows, float).reshape(-1, 9), np.array(chas, np.float32).reshape(-1, HB * SB + 4),
            np.array(body, np.float32).reshape(-1, HB * SB + 4), n, clock)


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
            return float(ev[i] - 1.0)
    return None


def match_start(det, xy, n, log=print):
    """First moment the robots start moving together (auto start), seconds into
    the video.  Pre-match robots are parked; at auto start several move at once."""
    fr = det[:, 0].astype(int)
    lag = int(FPS * 0.5)
    by = {f: xy[fr == f] for f in np.unique(fr)}
    sig = np.zeros(n)
    for f in range(lag, n):
        a, b = by.get(f), by.get(f - lag)
        if a is None or b is None or len(a) < 3 or len(b) < 3: continue
        d = np.linalg.norm(a[:, None] - b[None], axis=2).min(1)
        sig[f] = np.mean(np.sort(d)[-3:])      # three most-moved robots
    k = np.ones(int(FPS)) / FPS
    sm = np.convolve(sig, k, mode="same")
    lo, hi = int(2 * FPS), int(16 * FPS)
    cand = np.where(sm[lo:hi] > 0.25)[0]
    if len(cand) == 0:
        log(f"  match start: no motion onset found, fallback {START_FALLBACK}s")
        return START_FALLBACK, False
    f = lo + cand[0]
    while f > lo and sm[f] > 0.08: f -= 1
    t = f / FPS - 0.25 + 0.5            # signal is centred on a 0.5 s lag window
    log(f"  match start: {t:.2f}s")
    return float(t), True


# ---------------------------------------------------------------- stats
def zone_of(x, LEN):
    # alliance zones end at the hub line; 2026 hubs sit at x=4.63 / 11.92 m
    if x < 4.0: return "blue_zone"
    if x > LEN - 4.0: return "red_zone"
    return "neutral"


def track_stats(xy, obs, alliance_name, t_auto_end, LEN):
    dt = 1 / FPS
    v = np.r_[0, np.linalg.norm(np.diff(xy, axis=0), axis=1)] / dt
    v = np.minimum(v, 5.5)
    dist = float(v.sum() * dt)
    za = np.array([zone_of(x, LEN) for x in xy[:, 0]])
    own = "red_zone" if alliance_name == "red" else "blue_zone"
    opp = "blue_zone" if alliance_name == "red" else "red_zone"
    fa = int(t_auto_end * FPS)
    return dict(distance_m=round(dist, 1),
                auto_distance_m=round(float(v[:fa].sum() * dt), 1),
                max_speed=round(float(np.percentile(v, 99)), 2),
                observed=round(float(obs.mean()), 3),
                time_own_zone=round(float((za == own).mean()), 3),
                time_neutral=round(float((za == "neutral").mean()), 3),
                time_opp_zone=round(float((za == opp).mean()), 3))


# ---------------------------------------------------------------- main
def process(video, cam_path, onnx, tag_csv=None, log=print):
    cam = CAMM.Camera(cam_path)
    pose = {"checked": False}
    if tag_csv:
        cam, pose = check_pose(video, cam, tag_csv, log)
    CAMM.install_geom_shim(cam)
    import fuse, track as T
    T.DT = 1 / FPS

    model = Detector(onnx)
    t0 = time.time()
    det, chas, body, n, clock = detect_video(video, cam, model, log=log)
    log(f"  detection: {n} frames, {len(det)} raw dets in {time.time() - t0:.0f}s")

    keep = det[:, 7] >= CONF_TRACK
    d, ch, bo = det[keep], chas[keep], body[keep]
    xy = cam.box_to_field(d[:, 3:7])
    ok = np.isfinite(xy).all(1) & CAMM.in_field(xy)
    d, ch, bo, xy = d[ok], ch[ok], bo[ok], xy[ok]
    nms = CAMM.field_nms(d[:, 0], xy, d[:, 7])
    d, ch, bo, xy = d[nms], ch[nms], bo[nms], xy[nms]

    t_clock = clock_start(clock, log)
    t_motion, found = match_start(d, xy, n, log)
    if t_clock is not None and 0 <= t_clock < 40:
        t_start, found, how = t_clock, True, "clock"
    else:
        t_start, how = t_motion, "motion" if found else "default"
    log(f"  match start {t_start:.2f}s from {how} (clock {t_clock}, motion {t_motion:.2f})")
    f0 = max(0, int(round((t_start - 0.5) * FPS)))
    nf = min(n - f0, int(math.ceil((MATCH_S + 1.0) * FPS)))
    w = (d[:, 0] >= f0) & (d[:, 0] < f0 + nf)
    d, ch, bo, xy = d[w].copy(), ch[w], bo[w], xy[w]
    d[:, 0] -= f0
    cov = cam.cov(xy, su=2.0, sv=2.5)
    hs = cam.heights(d[:, 3:7], xy)
    chn = ch / np.maximum(ch.sum(1, keepdims=True), 1e-9)
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
        out_tracks.append(dict(
            alliance=tr["alliance"], idx=k,
            xy=np.round(tr["xy"][::step] * 100).astype(int).tolist(),      # cm @ OUT_HZ
            obs=tr["obs"][::step].astype(int).tolist(),
            stats=track_stats(tr["xy"], tr["obs"], tr["alliance"], t_auto_end, CAMM.LEN),
            feat=dict(height=None if not np.isfinite(tr["height"]) else round(tr["height"], 3),
                      hist=np.round(hist, 4).tolist(), n_iso=len(iso),
                      box_w_px=None if not np.isfinite(wpx) else float(wpx),
                      start_xy=np.round(tr["xy"][int(0.5 * FPS)], 2).tolist(),
                      end_xy=np.round(tr["xy"][-1], 2).tolist())))
    return dict(t_start=round(t_start, 2), start_detected=found, start_source=how, frames=nf, hz=OUT_HZ,
                t_auto_end=t_auto_end, t_teleop_start=0.5 + AUTO_S + PAUSE_S,
                pose=pose, n_dets=int(len(d)), tracks=out_tracks,
                field=dict(length=CAMM.LEN, width=CAMM.WID))


if __name__ == "__main__":
    video, cam_path, onnx, out = sys.argv[1:5]
    tag_csv = sys.argv[5] if len(sys.argv) > 5 else None
    res = process(video, cam_path, onnx, tag_csv)
    json.dump(res, open(out, "w"))
    print("wrote", out)
