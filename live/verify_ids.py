"""Contact sheet for checking robot->team labels by eye (bumper numbers).

    python live/verify_ids.py VIDEO CAMERA.json SITE_MATCH.json OUT.jpg

For each track: 4 enlarged crops at moments it was observed, captioned with the
team the identity solver assigned and its confidence.
"""
import sys, json, numpy as np, cv2, av
sys.path.insert(0, __import__("os").path.dirname(__file__))
from camera import Camera

video, cam_p, mj, out = sys.argv[1:5]
cam = Camera(cam_p); M = json.load(open(mj)); hz = M["hz"]; v0 = M["t_start"] - 0.5
rng = np.random.default_rng(0)
want = {}
for k, t in enumerate(M["tracks"]):
    obs = np.where(np.array(t["obs"]) > 0)[0]
    if len(obs) == 0: continue
    for i in np.sort(rng.choice(obs, min(4, len(obs)), replace=False)):
        want.setdefault(int(i), []).append(k)
c = av.open(video); s = c.streams.video[0]; s.thread_type = "AUTO"
crops = {k: [] for k in range(len(M["tracks"]))}
todo = sorted(want)
for f in c.decode(s):
    if not todo: break
    i = int(round((f.time - v0) * hz))
    if i < todo[0]: continue
    im = f.to_ndarray(format="bgr24")
    for k in want.pop(todo.pop(0)):
        x, y = np.array(M["tracks"][k]["xy"][i]) / 100.0
        p = cam.project(np.array([[x, y, 0.0], [x, y, 0.9]]))
        u, v = p[0]; top = p[1][1]
        r = int(max(45, (v - top) * 1.1))
        x0, y0 = int(u - r), int(top - r * 0.3)
        crop = im[max(0, y0):y0 + 2 * r, max(0, x0):x0 + 2 * r]
        if crop.size: crops[k].append(cv2.resize(crop, (240, 240), interpolation=cv2.INTER_CUBIC))
rows = []
for k, t in enumerate(M["tracks"]):
    cs = crops[k] + [np.zeros((240, 240, 3), np.uint8)] * (4 - len(crops[k]))
    cap = np.zeros((240, 260, 3), np.uint8)
    col = (80, 80, 255) if t["alliance"] == "red" else (255, 160, 60)
    cv2.putText(cap, f"track {t['idx']}", (10, 60), 0, 0.8, col, 2)
    cv2.putText(cap, str(t.get("team") or "?").replace("frc", ""), (10, 120), 0, 1.4, (255, 255, 255), 3)
    cv2.putText(cap, f"conf {t.get('id_conf') or 0:.2f}", (10, 170), 0, 0.7, (200, 200, 200), 1)
    rows.append(np.hstack([cap] + cs[:4]))
cv2.imwrite(out, np.vstack(rows))
print("teams red", M["red"]["teams"], "blue", M["blue"]["teams"])
