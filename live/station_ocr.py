"""Bumper-number reads from the two driver-station cameras (stationcam.py insets).

The station cameras are low (~1.8 m) and close to each alliance end, so the bumper numbers of
robots near that end face the camera and are 2-3x larger than in the overhead view.  This module
turns them into FIELD-LOCATED reads that the OCR-anchored tracker (fuse.run_labeled) attributes
to the overhead detection nearest in field space, like its own reads:

  1. inset frames at STOCR_HZ (both cameras), synced to wide-video time (stationcam.sync);
  2. the overhead detections of that instant (process.prepare state: d, xy; station pictures
     lead the overhead one by ~STOCR_LEAD s) are projected into each station view; every robot in
     view and within STOCR_RANGE m gets a crop around its bumpers (+-STOCR_MARGIN m, positions
     can be 0.3-0.5 m off) -> ocr.BumperOCR (6-team CTC scoring, same as the overhead reads);
  3. each read's text centre is back-projected onto the plane z = TEXT_Z and moved FACE m away
     from the camera (bumper face -> robot centre): a field xy whose lateral component is precise
     and whose depth component is not (sigma grows with range) -> rows
         [t_video, cand, lp, margin, x, y, dir_x, dir_y, sig_along, sig_perp, cam, u, v]
     (t_video = wide-video seconds of the overhead instant the read belongs to; dir = unit
     horizontal viewing direction; cam 0 = red station, 1 = blue).
     Open-set scoring (open_teams = the event's teams, or st["open_teams"]): rows get 7 more
     columns [best_team, best_lp, alt_team, alt_lp, rejected, best_margin, oos] (ocr.read_many) and
     ALSO hold the rejected reads (rejected = 1: fuse.attribute_extra skips them; out-of-match reads
     are rejected only with STOCR_OPEN_REJECT=1, default $OCR_OPEN_REJECT = off).

Attribution to overhead detections (with an ambiguity guard) happens in fuse.attribute_extra.

    rows = station_reads(st, sc, teams)                          # sc = stationcam.StationCams
    process.process(..., extra_reads=station_ocr.extra_reads_fn(event_dir, inset, wide_video))

CLI (development):  python live/station_ocr.py STAGE.pkl INSET.mp4 EVENT_DIR OUT.npy [SYNC.json KEY]
"""
import os, sys, json, time
import numpy as np, cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

E = os.environ.get
HZ = float(E("STOCR_HZ", "5"))              # inset frames per second read
LEAD = float(E("STOCR_LEAD", "0.08"))       # s the station pictures lead the overhead picture
RANGE = float(E("STOCR_RANGE", "11"))       # m, farthest robot worth a crop
MARGIN = float(E("STOCR_MARGIN", "0.35"))   # m around the +-0.5 m robot footprint
TEXT_Z = float(E("STOCR_TEXT_Z", "0.12"))   # m, height of the bumper-number centre
FACE = float(E("STOCR_FACE", "0.40"))       # m, bumper face -> robot centre
SIG_PERP = float(E("STOCR_SIG_PERP", "0.45"))
SIG_ALONG0, SIG_ALONG_K = float(E("STOCR_SIG_A0", "0.30")), float(E("STOCR_SIG_AK", "0.03"))
ANGLES = tuple(float(a) for a in E("STOCR_ANGLES", "0").split(","))   # "0,25,-25": +4% reads, 2x cost
UP = float(E("STOCR_UP", "3.0"))
CONFUSE = E("STOCR_CONFUSABLE", "1") == "1"   # digits scored with look-alike glyphs (ocr.CONFUSABLE)
OPEN_REJECT = E("STOCR_OPEN_REJECT", E("OCR_OPEN_REJECT", "0")) == "1"   # drop out-of-match reads (open set)
MIN_PX = 14                                 # px, smallest crop side worth reading
OVERHEAD_FPS = 30.0


def _rect(cam, x, y, W, H, half=0.5 + MARGIN, z1=0.6):
    P = np.array([[x + dx, y + dy, z] for dx in (-half, half) for dy in (-half, half) for z in (0.0, z1)])
    v = ((P - cam.C) @ cam.R[2]) > 0.3
    if not v.all(): return None
    q = cam.project(P)
    if not np.isfinite(q).all(): return None
    x0, y0 = np.floor(q.min(0)).astype(int); x1, y1 = np.ceil(q.max(0)).astype(int)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    if x1 - x0 < MIN_PX or y1 - y0 < MIN_PX: return None
    return x0, y0, x1, y1


def locate(cam, uv):
    """Inset px of bumper text -> (robot-centre xy (N,2), horizontal unit view dir (N,2), range m)."""
    uv = np.asarray(uv, float).reshape(-1, 2)
    p = cam.img_to_plane(uv, TEXT_Z)
    d = p - cam.C[:2]
    r = np.linalg.norm(d, axis=1)
    u = d / np.maximum(r, 1e-6)[:, None]
    return p + FACE * u, u, r


def station_reads(st, sc, teams, ocr=None, hz=HZ, log=print, t_range=None, open_teams=None):
    """st: process.prepare() state (d rows [frame, t_video, ., x0,y0,x1,y1, conf, colour], xy);
    sc: stationcam.StationCams for the match; teams: 6 team keys (candidate order = read cand idx).
    open_teams: the event's team keys for open-set scoring (default st.get("open_teams"); None/empty
    = closed set, 13 columns).  -> (R, 13) or (R, 20) float rows (see module doc)."""
    import ocr as OCRM
    if ocr is None:
        ocr = OCRM.BumperOCR()
    cands = [t[3:] if t.startswith("frc") else t for t in teams]
    if open_teams is None: open_teams = st.get("open_teams")
    oc = [c for c in OCRM.team_numbers(open_teams) if c not in cands] if open_teams else []
    oc = oc or None
    ncol = 20 if oc else 13
    d, xy = st["d"], st["xy"]
    tv = d[:, 1]
    order = np.argsort(tv, kind="stable"); ts = tv[order]
    fr_t = np.unique(ts)                                          # overhead frame times
    ws = [w for w in ("red", "blue") if sc.usable.get(w)]
    if not ws:
        log("  station OCR: no usable station camera"); return np.zeros((0, ncol))
    if t_range is None:
        t0 = st["t_start"] - 0.5; t_range = (t0, t0 + st["nf"] / OVERHEAD_FPS)
    rows, n_crops, n_fr, t_start = [], 0, 0, time.time()
    for tw, red, blue in sc.frames(t_range[0], t_range[1], step=1.0 / hz):
        to = tw + LEAD                                            # the overhead instant shown
        k = np.searchsorted(fr_t, to)
        cand_t = [fr_t[i] for i in (k - 1, k) if 0 <= i < len(fr_t)]
        if not cand_t: continue
        tn = min(cand_t, key=lambda x: abs(x - to))
        if abs(tn - to) > 0.1: continue
        lo, hi = np.searchsorted(ts, tn - 1e-6), np.searchsorted(ts, tn + 1e-6)
        rws = order[lo:hi]
        n_fr += 1
        crops, offs, meta = [], [], []
        for ci, (w, im) in enumerate((("red", red), ("blue", blue))):
            if w not in ws or im is None: continue
            cam = sc.cam[w]; H, W = im.shape[:2]
            for g in rws:
                x, y = xy[g]
                if np.hypot(x - cam.C[0], y - cam.C[1]) > RANGE: continue
                r = _rect(cam, x, y, W, H)
                if r is None: continue
                x0, y0, x1, y1 = r
                crops.append(im[y0:y1, x0:x1]); offs.append((x0, y0)); meta.append((ci, w))
        if not crops: continue
        n_crops += len(crops)
        res = ocr.read_many(crops, cands, offsets=offs, up=UP, angles=ANGLES, confusable=CONFUSE,
                            open_cands=oc, keep_rejected=oc is not None, open_reject=OPEN_REJECT)
        seen = {}
        for (ci, w), rr in zip(meta, res):
            for r in rr:
                dup = [q for q in seen.get(ci, []) if np.hypot(q.uv[0] - r.uv[0], q.uv[1] - r.uv[1]) < 12]
                if dup:
                    if dup[0].best_lp >= r.best_lp: continue
                    seen[ci].remove(dup[0])
                seen.setdefault(ci, []).append(r)
        for ci, rr in seen.items():
            cam = sc.cam["red" if ci == 0 else "blue"]
            uv = np.array([r.uv for r in rr])
            c, u, rng = locate(cam, uv)
            for r, p, dv, rg in zip(rr, c, u, rng):
                if not np.isfinite(p).all(): continue
                row = [tn, r.cand, round(r.lp, 3), round(r.margin, 2), p[0], p[1], dv[0], dv[1],
                       SIG_ALONG0 + SIG_ALONG_K * rg, SIG_PERP, ci, r.uv[0], r.uv[1]]
                if oc:
                    row += [int(r.best), round(r.best_lp, 3), int(r.alt), round(r.alt_lp, 3), int(r.rejected),
                            round(r.best_margin, 2), int(r.oos)]
                rows.append(row)
    out = np.array(rows, float).reshape(-1, ncol)
    out = debias(out, tv, xy, log)
    acc = out[out[:, 17] == 0] if oc else out
    log(f"  station OCR: {n_fr} frames, {n_crops} crops, {len(acc)} reads in {time.time() - t_start:.0f}s "
        + (f"(+{len(out) - len(acc)} rejected; {int(out[:, 19].sum())} out of match) " if oc else "")
        + f"(per team {np.bincount(acc[:, 1].astype(int), minlength=len(teams)).tolist() if len(acc) else []})")
    return out


def debias(rows, tv, xy, log=print, it=2):
    """Per camera, shift the read locations by the robust median offset to the overhead
    detections they clearly belong to (station / overhead calibration residuals, text printed
    off the bumper centre): measured up to 0.5 m sideways per camera."""
    rows = rows.copy()
    for ci in (0, 1):
        m = np.where(rows[:, 10] == ci)[0]
        if len(m) < 20: continue
        tot = np.zeros(2)
        for _ in range(it):
            res = []
            for i in m:
                r = rows[i]; k = np.abs(tv - r[0]) < 1e-4
                if not k.any(): continue
                v = xy[k] - r[4:6]; dv = r[6:8]; pv = np.array([-dv[1], dv[0]])
                D = np.hypot(v @ dv / r[8], v @ pv / r[9]); o = np.argsort(D)
                if D[o[0]] < 1.5 and (len(o) == 1 or D[o[1]] - D[o[0]] > 1.5): res.append(v[o[0]])
            if len(res) < 20: break
            sh = np.median(np.array(res), 0)
            rows[m, 4:6] += sh; tot += sh
        log(f"  station OCR: {'red' if ci == 0 else 'blue'} read locations shifted by {np.round(tot, 2).tolist()} m")
    return rows


def extra_reads_fn(event_dir, inset_video, wide_video=None, sync=None, ocr=None):
    """For process.process(extra_reads=...): a callable (st, teams, log) -> station read rows.
    Never raises (returns no reads when the insets can't be used)."""
    def fn(st, teams, log=print):
        if not inset_video or not os.path.exists(inset_video):
            log("  station OCR: no inset video"); return np.zeros((0, 13))
        try:
            import stationcam
            sc = stationcam.StationCams(event_dir, None, inset_video, sync=sync, wide_video=wide_video, log=log)
            if isinstance(sc.sync, dict) and not sc.sync.get("ok", True):
                log("  station OCR: inset sync failed, skipped"); return np.zeros((0, 13))
            return station_reads(st, sc, teams, ocr=ocr, log=log)
        except Exception as e:
            import traceback; traceback.print_exc()
            log(f"  station OCR failed ({e!r}): no station reads"); return np.zeros((0, 13))
    return fn


if __name__ == "__main__":
    import pickle
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
    import stationcam
    pkl, inset, event_dir, out = sys.argv[1:5]
    sync = None
    if len(sys.argv) > 6:
        sync = json.load(open(sys.argv[5]))[sys.argv[6]]["sync"]
    st = pickle.load(open(pkl, "rb"))
    sc = stationcam.StationCams(event_dir, None, inset, sync=sync)
    rows = station_reads(st, sc, st["teams"])
    np.save(out, rows)
    print("wrote", out, rows.shape)
