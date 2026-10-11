"""Contact sheet for checking robot->team labels by eye (bumper numbers).

    python live/verify_ids.py VIDEO CAMERA.json SITE_MATCH.json OUT.jpg [N_CROPS=12]

CAMERA.json: the event calibration; if SITE_MATCH.json (or the processed match JSON)
carries `camera_pose`, that per-match pose is used.  For each track: N enlarged,
sharpened crops spread uniformly over the match (in each time bin the moment the
robot is observed, isolated, slow and near the camera), each captioned with its
time and -- if the track has `segments` ([[i0, i1, team, conf], ...]) -- the team the
solver says the track is AT THAT MOMENT (v1 tracks often swap robots mid-match), or for
tracker-labelled matches (track .team + per-frame .conf) the team and that frame's conf.
"""
import sys, os, json, numpy as np, cv2, av
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from camera import Camera


def pick_moments(M, n):
    XY = [np.array(t["xy"], float) / 100 for t in M["tracks"]]
    OB = [np.array(t["obs"]) > 0 for t in M["tracks"]]
    L = min(len(x) for x in XY); want = {}
    for k in range(len(XY)):
        d = np.full(L, 9.0)
        for j in range(len(XY)):
            if j != k: d = np.minimum(d, np.linalg.norm(XY[k][:L] - XY[j][:L], axis=1))
        sp = np.r_[0, np.linalg.norm(np.diff(XY[k][:L], axis=0), axis=1)] * M["hz"]
        sp = np.convolve(sp, np.ones(5) / 5, mode="same")
        sc = np.where(OB[k][:L], 0.0, -100.0) + XY[k][:L, 1] + 0.6 * np.minimum(d, 2.5) - np.minimum(sp, 3)
        for b in np.array_split(np.arange(L), n):
            if len(b): want.setdefault(int(b[np.argmax(sc[b])]), []).append(k)
    return want


def seg_team(t, i):
    for i0, i1, team, conf in t.get("segments") or []:
        if i0 <= i < i1: return team, conf
    if t.get("conf") is not None and i < len(t["conf"]):      # tracker-labelled (process.py with teams)
        return t.get("team"), t["conf"][i]
    return t.get("team"), t.get("id_conf")


def main(video, cam_p, mj, out, n=12, size=240):
    M = json.load(open(mj)); hz = M["hz"]; v0 = M["t_start"] - 0.5
    raw = json.load(open(cam_p))
    if M.get("camera_pose"): raw = dict(raw, rvec=M["camera_pose"]["rvec"], tvec=M["camera_pose"]["tvec"])
    cam = Camera(raw)
    want = pick_moments(M, n)
    c = av.open(video); s = c.streams.video[0]; s.thread_type = "AUTO"
    todo = sorted(want); crops = {}
    if v0 + todo[0] / hz > 3: c.seek(int((v0 + todo[0] / hz - 1) * 1e6), any_frame=False, backward=True)
    pad = 300
    for f in c.decode(s):
        if not todo: break
        if f.time is None: continue
        if int(round((f.time - v0) * hz)) < todo[0]: continue
        i = todo.pop(0)
        im = cv2.copyMakeBorder(f.to_ndarray(format="bgr24"), pad, pad, pad, pad, cv2.BORDER_CONSTANT)
        for k in want[i]:
            t = M["tracks"][k]; x, y = np.array(t["xy"][i]) / 100.0
            p = cam.project(np.array([[x + dx, y + dy, z] for dx in (-.55, .55) for dy in (-.55, .55) for z in (0, 1.0)]))
            x0, y0 = p.min(0) - 6; x1, y1 = p.max(0) + 6
            w = int(max(x1 - x0, y1 - y0)); cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            x0, y0 = int(cx - w / 2) + pad, int(cy - w / 2) + pad
            cr = cv2.resize(im[y0:y0 + w, x0:x0 + w], (size, size), interpolation=cv2.INTER_LANCZOS4)
            cr = cv2.addWeighted(cr, 1.6, cv2.GaussianBlur(cr, (0, 0), 2.0), -0.6, 0)
            team, conf = seg_team(t, i)
            cap = f"{i / hz:.0f}s " + (str(team).replace("frc", "") if team else "?")
            if conf is not None: cap += f" {float(conf):.2f}"
            if not t["obs"][i]: cap += " U"
            cv2.rectangle(cr, (0, 0), (size, 24), (0, 0, 0), -1)
            cv2.putText(cr, cap, (4, 18), 0, 0.6, (0, 255, 255) if t["obs"][i] else (0, 0, 255), 2)
            crops.setdefault(k, []).append((i, cr))
    c.close()
    per = 6; rows = []
    for k, t in enumerate(M["tracks"]):
        cs = [cr for _, cr in sorted(crops.get(k, []), key=lambda z: z[0])]
        cs += [np.zeros((size, size, 3), np.uint8)] * (-(-max(len(cs), 1) // per) * per - len(cs))
        cap = np.zeros((size, 170, 3), np.uint8)
        col = (80, 80, 255) if t["alliance"] == "red" else (255, 160, 60)
        cv2.putText(cap, f"track {t['idx']}", (8, 40), 0, 0.8, col, 2)
        cv2.putText(cap, str(t.get("team") or "?").replace("frc", ""), (8, 100), 0, 1.3, (255, 255, 255), 3)
        cv2.putText(cap, f"conf {float(t.get('id_conf') or t.get('team_conf') or 0):.2f}", (8, 145), 0, 0.7, (200, 200, 200), 1)
        for r0 in range(0, len(cs), per):
            rows.append(np.hstack([cap if r0 == 0 else np.zeros_like(cap)] + cs[r0:r0 + per]))
    cv2.imwrite(out, np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 92])
    if "red" in M and "blue" in M:
        print("teams red", M["red"]["teams"], "blue", M["blue"]["teams"])


if __name__ == "__main__":
    a = sys.argv[1:]
    main(*a[:4], n=int(a[4]) if len(a) > 4 else 12)
