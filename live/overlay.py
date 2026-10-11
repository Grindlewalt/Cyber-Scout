"""Accuracy-check overlay renderer.

render(video_path, site_match_json, shots_json, cam, out_mp4, log=print, width=1920,
       t_from=None, t_to=None)

Draws everything the system believes on top of the broadcast video: robot footprints + team
tags, reconstructed ball arcs with made/missed/pass marks, and a right-hand scoreboard panel
(per-robot made/attempts, hub state, traced-vs-TBA, minimap).  Video is extended on the right by
a PANEL_W px panel and the whole canvas is scaled to `width` px wide, H.264 yuv420p via ffmpeg.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shots import traj, C_DRAG  # noqa: E402

OUT_FPS = 30
PANEL_W = 440
FIELD_L, FIELD_W = 16.541, 8.069
HUBS = {"red": (11.915, 4.035), "blue": (4.626, 4.035)}
ROBOT_SIDE = 0.91
FONT = cv2.FONT_HERSHEY_DUPLEX
AA = cv2.LINE_AA

# BGR
ALLI = {"red": (74, 74, 240), "blue": (240, 150, 60)}
SHADES = {"red": [(74, 74, 240), (120, 150, 255), (40, 40, 170)],
          "blue": [(240, 150, 60), (255, 205, 120), (170, 100, 25)]}
BG = (30, 24, 20)
CARD = (46, 38, 32)
TXT = (245, 245, 245)
DIM = (170, 160, 150)
GREEN = (110, 210, 90)
AMBER = (50, 190, 255)
BAD = (80, 80, 245)
MADE_HOLD = 1.0


# ------------------------------------------------------------------ drawing helpers
def text(img, s, org, scale=0.7, color=TXT, th=1, anchor="l", shadow=False):
    (w, h), _ = cv2.getTextSize(s, FONT, scale, th)
    x, y = int(org[0]), int(org[1])
    if anchor == "r":
        x -= w
    elif anchor == "c":
        x -= w // 2
    if shadow:
        cv2.putText(img, s, (x + 2, y + 2), FONT, scale, (0, 0, 0), th + 2, AA)
    cv2.putText(img, s, (x, y), FONT, scale, color, th, AA)
    return w


def rrect(img, x0, y0, x1, y1, color, r=10, alpha=1.0):
    x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
    if alpha < 1.0:
        roi = img[y0:y1, x0:x1].copy()
        layer = roi.copy()
        _rr(layer, 0, 0, x1 - x0, y1 - y0, color, r)
        cv2.addWeighted(layer, alpha, roi, 1 - alpha, 0, roi)
        img[y0:y1, x0:x1] = roi
    else:
        _rr(img, x0, y0, x1, y1, color, r)


def _rr(img, x0, y0, x1, y1, color, r):
    r = min(r, (x1 - x0) // 2, (y1 - y0) // 2)
    cv2.rectangle(img, (x0 + r, y0), (x1 - r - 1, y1 - 1), color, -1)
    cv2.rectangle(img, (x0, y0 + r), (x1 - 1, y1 - r - 1), color, -1)
    for cx, cy in ((x0 + r, y0 + r), (x1 - r - 1, y0 + r), (x0 + r, y1 - r - 1), (x1 - r - 1, y1 - r - 1)):
        cv2.circle(img, (cx, cy), r, color, -1, AA)


def _fill(img, poly, color, alpha):
    x0, y0 = np.maximum(poly.min(0) - 2, 0)
    x1, y1 = poly.max(0) + 3
    x1, y1 = min(x1, img.shape[1]), min(y1, img.shape[0])
    if x1 <= x0 or y1 <= y0:
        return
    roi = img[y0:y1, x0:x1]
    lay = roi.copy()
    cv2.fillPoly(lay, [poly - np.array([x0, y0], np.int32)], color)
    cv2.addWeighted(lay, alpha, roi, 1 - alpha, 0, roi)


def dashed(img, pts, color, th, dash=14, gap=10):
    for a, b in zip(pts, np.roll(pts, -1, 0)):
        d = float(np.hypot(*(b - a)))
        if d < 1:
            continue
        u = (b - a) / d
        s = 0.0
        while s < d:
            e = min(s + dash, d)
            cv2.line(img, tuple(np.round(a + u * s).astype(int)), tuple(np.round(a + u * e).astype(int)), color, th, AA)
            s += dash + gap


def tag(img, s, x, y, color, scale=0.8, faded=False):
    (w, h), _ = cv2.getTextSize(s, FONT, scale, 2)
    x0, y0 = int(x - w / 2 - 8), int(y - h - 7)
    rrect(img, x0, y0, x0 + w + 16, y0 + h + 14, color, 7, alpha=0.55 if faded else 0.92)
    cv2.putText(img, s, (int(x - w / 2), int(y)), FONT, scale, (255, 255, 255), 2, AA)


def mark(img, kind, c, r=13):
    x, y = int(c[0]), int(c[1])
    cv2.circle(img, (x, y), r + 5, (0, 0, 0), -1, AA)
    if kind == "made":
        col = GREEN
        cv2.circle(img, (x, y), r + 5, col, 2, AA)
        cv2.polylines(img, [np.array([[x - r // 2 - 3, y], [x - 3, y + r // 2], [x + r // 2 + 6, y - r // 2]])], False, col, 4, AA)
    elif kind == "missed":
        col = BAD
        cv2.circle(img, (x, y), r + 5, col, 2, AA)
        d = r // 2 + 2
        cv2.line(img, (x - d, y - d), (x + d, y + d), col, 4, AA)
        cv2.line(img, (x - d, y + d), (x + d, y - d), col, 4, AA)
    else:
        col = AMBER
        cv2.circle(img, (x, y), r + 5, col, 2, AA)
        cv2.arrowedLine(img, (x - r // 2 - 4, y), (x + r // 2 + 5, y), col, 4, AA, tipLength=0.55)


# ------------------------------------------------------------------ data prep
class _Shots:
    """Per-shot precomputed arc (image polyline), timing, ownership."""

    def __init__(self, shots, site_shots, tracks, cam, t_start):
        self.items = []
        for i, s in enumerate(shots):
            th = np.array(list(s["origin"]) + list(s["v0"]) + list(s.get("acc") or [0, 0, 0]), float)
            c = float(s.get("drag") or C_DRAG)
            tau = np.arange(0, 4.0, 1 / 60)
            P = traj(th, tau, c)
            te_tau = None
            if s.get("t_entry") is not None:
                te_tau = max(float(s["t_entry"]) - float(s["t"]), 0.2)
            else:  # first descent through the floor (or 0.25 m)
                dn = np.where((P[:, 2] < 0.25) & (tau > 0.3) & (np.gradient(P[:, 2]) < 0))[0]
                te_tau = float(tau[dn[0]]) if len(dn) else float(tau[-1])
            te_tau = min(te_tau, 4.0)
            n = int(te_tau * 60) + 1
            tau = np.linspace(0, te_tau, max(n, 3))
            P = traj(th, tau, c)
            uv = np.clip(np.nan_to_num(cam.project(P), nan=-1e5, posinf=1e5, neginf=-1e5), -1e5, 1e5)
            site = site_shots[i] if i < len(site_shots) and abs(site_shots[i]["t"] - s["t"]) < 1e-3 else None
            sh = s["shooter"]
            k = sh.get("track_idx")
            team = (site or {}).get("team") or s.get("team")
            if team is None and k is not None:
                team = tracks[k].get("team")
            al = sh.get("alliance")
            grp = k if k is not None else (6 if al == "red" else 7 if al == "blue" else None)
            shade = SHADES[al][(k % 3) if k is not None else 0] if al else (200, 200, 200)
            self.items.append(dict(
                t0=float(s["t"]), t1=float(s["t"]) + te_tau, tau=tau, uv=uv, outcome=s["outcome"],
                team=_num(team) if k is not None or team else ("HP" if sh["kind"] == "human" else "?"),
                grp=grp, al=al, color=shade, period=s.get("period"),
                hub=(s.get("target") or "").split("_")[0] or None, counted=s.get("counted_period") or s.get("period"),
                kind=sh["kind"]))
        self.t0 = np.array([x["t0"] for x in self.items])
        self.t1 = np.array([x["t1"] for x in self.items])


def _num(team):
    return str(team).replace("frc", "") if team else "?"


# ------------------------------------------------------------------ renderer
class _R:
    def __init__(self, site, ev, cam, log):
        self.site, self.cam, self.log = site, cam, log
        self.hz = float(site.get("hz", 10))
        self.t_start = float(site["t_start"])
        self.f0 = self.t_start - 0.5
        self.tracks = site["tracks"]
        self.xy = [np.asarray(t["xy"], float) / 100.0 for t in self.tracks]
        self.obs = [np.asarray(t.get("obs") or np.ones(len(t["xy"])), float) for t in self.tracks]
        self.teams = [_num(t.get("team")) for t in self.tracks]
        self.idc = [t.get("id_conf") if t.get("id_conf") is not None else 1.0 for t in self.tracks]
        self.S = _Shots(ev["shots"], site["shots"], self.tracks, cam, self.t_start)
        self.rec = ev.get("reconcile", {})
        self.periods = site["periods"]
        self.T_tele = float(site.get("t_teleop_start", 23.5)) - 0.5
        self.gaps = [(self.f0 + a, self.f0 + b) for a, b in (site.get("feed_gaps") or [])]
        self.label = site.get("short") or site.get("key", "")
        self.score = {a: site[a].get("score") for a in ("red", "blue")}
        self.tcache = {}
        self.pkey, self.pimg = None, None
        self.mini_bg = self._minimap_bg()

    # ---- clock
    def clock(self, t):
        rt = t - self.t_start
        mc = rt if rt <= 20 else (20.0 if rt < self.T_tele else rt - (self.T_tele - 20))
        per = None
        if 20.0 <= rt < max(self.T_tele, 23.0):
            per = self.periods[0]
        else:
            for p in self.periods:
                if p["t0"] <= mc < p["t1"]:
                    per = p
            if per is None:
                per = self.periods[-1] if mc >= 0 else self.periods[0]
        return rt, mc, per

    def robot(self, k, t):
        xy = self.xy[k]
        f = np.clip((t - self.f0) * self.hz, 0, len(xy) - 1.000001)
        i = int(f)
        w = f - i
        p = xy[i] * (1 - w) + xy[i + 1] * w
        if not np.isfinite(p).all():
            return None, False
        o = self.obs[k]
        return p, bool(o[min(int(round(f)), len(o) - 1)])

    # ---- counts at time t
    def counts(self, t):
        S = self.S
        done = S.t1 <= t
        C = np.zeros((8, 5), int)  # made_a att_a made_t att_t pass
        live = {a: 0 for a in ("red", "blue")}
        idx = np.where(done)[0]
        for j in idx:
            it = S.items[j]
            if it["grp"] is not None:
                a = 0 if it["period"] == "auto" else 2
                if it["outcome"] == "made":
                    C[it["grp"], a] += 1
                    C[it["grp"], a + 1] += 1
                elif it["outcome"] == "missed":
                    C[it["grp"], a + 1] += 1
                elif it["outcome"] == "pass":
                    C[it["grp"], 4] += 1
        return C

    def traced_live(self, t, pname):
        out = {"red": 0, "blue": 0}
        S = self.S
        for j in np.where(S.t1 <= t)[0]:
            it = S.items[j]
            if it["outcome"] == "made" and it["hub"] and it["counted"] == pname:
                out[it["hub"]] += 1
        return out

    # ---- field geometry on image
    def footprint(self, p):
        h = ROBOT_SIDE / 2
        c = np.array([[p[0] - h, p[1] - h], [p[0] + h, p[1] - h], [p[0] + h, p[1] + h], [p[0] - h, p[1] + h]])
        return self.cam.project(c)

    # ---- minimap
    MM_W, MM_H = PANEL_W - 40, int((PANEL_W - 40) * FIELD_W / FIELD_L)

    def _mm(self, X, Y):
        return (int((FIELD_L - X) / FIELD_L * self.MM_W), int(Y / FIELD_W * self.MM_H))

    def _minimap_bg(self):
        im = np.zeros((self.MM_H, self.MM_W, 3), np.uint8)
        im[:] = (52, 62, 48)
        cv2.rectangle(im, (0, 0), (self.MM_W // 2, self.MM_H), (50, 50, 70), -1)
        cv2.rectangle(im, (self.MM_W // 2, 0), (self.MM_W, self.MM_H), (70, 56, 46), -1)
        cv2.line(im, (self.MM_W // 2, 0), (self.MM_W // 2, self.MM_H), (110, 110, 110), 1, AA)
        for al, (x, y) in HUBS.items():
            cv2.circle(im, self._mm(x, y), 11, ALLI[al], 2, AA)
        cv2.rectangle(im, (0, 0), (self.MM_W - 1, self.MM_H - 1), (140, 140, 140), 1)
        return im

    # ---- panel
    def panel(self, t, rt, mc, per, in_gap, C, tl):
        key = (int(rt) if rt > 0 else -1, C.tobytes(), per["name"], in_gap, tuple(tl.values()), self.mmkey)
        if key == self.pkey:
            return self.pimg
        H = 1080
        P = np.zeros((H, PANEL_W, 3), np.uint8)
        P[:] = BG
        # header
        text(P, f"{self.label}  ACCURACY CHECK", (20, 36), 0.62, DIM, 1)
        if rt < 0:
            clk = "PRE"
        elif 20 <= rt < self.T_tele:
            clk = "0:00"
        else:
            rem = (20 if mc <= 20 and per["name"] == "auto" else 160) - mc
            rem = max(rem, 0)
            clk = f"{int(rem // 60)}:{int(rem % 60):02d}"
        text(P, clk, (20, 108), 2.2, TXT, 3)
        pname = per["name"].upper().replace("SHIFT", "SHIFT ")
        if 20 <= rt < self.T_tele:
            pname = "AUTO  (ASSESSING)"
        rrect(P, 232, 60, PANEL_W - 20, 108, CARD, 10)
        text(P, pname, (PANEL_W - 126, 93), 0.75, TXT, 2, "c")
        # hub state
        y = 132
        for i, al in enumerate(("red", "blue")):
            x0 = 20 + i * 205
            act = per["active"][al] if per else True
            rrect(P, x0, y, x0 + 195, y + 54, CARD, 10)
            cv2.rectangle(P, (x0, y + 8), (x0 + 6, y + 46), ALLI[al], -1)
            text(P, f"{al.upper()} HUB", (x0 + 18, y + 24), 0.55, DIM, 1)
            cv2.circle(P, (x0 + 24, y + 40), 6, GREEN if act else (110, 110, 110), -1, AA)
            text(P, "ACTIVE" if act else "INACTIVE", (x0 + 38, y + 46), 0.58, GREEN if act else (150, 150, 150), 1)
            sc = self.score[al]
            if sc is not None:
                text(P, str(sc), (x0 + 185, y + 30), 0.8, TXT, 2, "r")
                text(P, "final", (x0 + 185, y + 46), 0.4, DIM, 1, "r")
        # robot cards
        y = 204
        for al in ("red", "blue"):
            ks = [k for k, tr in enumerate(self.tracks) if tr["alliance"] == al]
            rows = len(ks)
            hgt = 48 + rows * 54 + 26
            rrect(P, 12, y, PANEL_W - 12, y + hgt, CARD, 12)
            cv2.rectangle(P, (12, y + 10), (18, y + hgt - 10), ALLI[al], -1)
            ga = 6 if al == "red" else 7
            ma = C[ks, 0].sum() + C[ks, 2].sum() + C[ga, 0] + C[ga, 2]
            at = C[ks, 1].sum() + C[ks, 3].sum() + C[ga, 1] + C[ga, 3]
            text(P, al.upper(), (32, y + 28), 0.7, ALLI[al], 2)
            text(P, f"{ma}/{at} made", (PANEL_W - 28, y + 28), 0.7, TXT, 1, "r")
            yy = y + 36
            text(P, "TEAM", (32, yy + 8), 0.4, DIM, 1)
            text(P, "MADE/ATT", (214, yy + 8), 0.4, DIM, 1, "c")
            text(P, "AUTO", (318, yy + 8), 0.4, DIM, 1, "c")
            text(P, "TELEOP", (386, yy + 8), 0.4, DIM, 1, "c")
            yy += 12
            for k in ks:
                r = C[k]
                m, a = r[0] + r[2], r[1] + r[3]
                col = SHADES[al][k % 3] if False else ALLI[al]
                cv2.circle(P, (40, yy + 26), 7, SHADES[al][self.tracks[k]["idx"] % 3], -1, AA)
                w = text(P, self.teams[k], (58, yy + 34), 0.95, TXT, 2)
                if self.idc[k] < 0.5:
                    text(P, "?", (62 + w, yy + 34), 0.8, AMBER, 2)
                text(P, f"{m}/{a}", (214, yy + 33), 0.8, TXT, 2, "c")
                pc = f"{100 * m // a}%" if a else ""
                text(P, pc, (214, yy + 49), 0.4, DIM, 1, "c")
                text(P, f"{r[0]}/{r[1]}", (318, yy + 33), 0.55, DIM, 1, "c")
                text(P, f"{r[2]}/{r[3]}", (386, yy + 33), 0.55, DIM, 1, "c")
                yy += 54
            g = C[ga]
            text(P, "HP / unattributed", (32, yy + 14), 0.45, DIM, 1)
            text(P, f"{g[0] + g[2]}/{g[1] + g[3]} made", (PANEL_W - 28, yy + 14), 0.45, DIM, 1, "r")
            y += hgt + 10
        # traced vs TBA
        pn = per["name"]
        rrect(P, 12, y, PANEL_W - 12, y + 124, CARD, 12)
        text(P, f"TRACED vs TBA  -  {pn.upper()}", (28, y + 28), 0.55, TXT, 1)
        rc = self.rec
        for i, al in enumerate(("red", "blue")):
            r = (rc.get(al) or {}).get(pn) or {}
            tba, fin = r.get("tba"), r.get("traced_made")
            yy = y + 56 + i * 34
            text(P, al.upper(), (28, yy + 8), 0.5, ALLI[al], 1)
            bx0, bx1 = 92, 292
            cv2.rectangle(P, (bx0, yy - 6), (bx1, yy + 12), (70, 62, 56), -1)
            mx = max(tba or 0, fin or 0, tl[al], 1)
            w = int((bx1 - bx0) * tl[al] / mx)
            cv2.rectangle(P, (bx0, yy - 6), (bx0 + w, yy + 12), ALLI[al], -1)
            if tba is not None:
                xt = bx0 + int((bx1 - bx0) * tba / mx)
                cv2.line(P, (xt, yy - 11), (xt, yy + 17), (255, 255, 255), 2, AA)
            text(P, f"{tl[al]}", (bx1 + 12, yy + 10), 0.7, TXT, 2)
            if tba is not None:
                d = (fin or 0) - tba
                text(P, f"/{tba} TBA", (bx1 + 50, yy + 10), 0.5, DIM, 1)
            else:
                text(P, "n/a", (bx1 + 50, yy + 10), 0.5, DIM, 1)
        text(P, "bar=traced made so far   white tick=TBA total", (28, y + 120), 0.38, DIM, 1)
        y += 132
        self.mm_y = y
        self.pkey, self.pimg = key, P
        return P

    mmkey = 0

    def compose(self, frame, t):
        """frame: BGR 1920x1080 (modified in place); returns canvas (1080 x (1920+PANEL_W))."""
        cam = self.cam
        rt, mc, per = self.clock(t)
        in_gap = any(a <= t <= b for a, b in self.gaps)
        # ---- robots
        cur = {}
        for k, tr in enumerate(self.tracks):
            p, ob = self.robot(k, t)
            if p is None:
                continue
            cur[k] = (p, ob)
            if not (-0.5 < p[0] < FIELD_L + 0.5 and -0.5 < p[1] < FIELD_W + 0.5):
                continue
            poly = self.footprint(p)
            col = SHADES[tr["alliance"]][tr["idx"] % 3] if False else ALLI[tr["alliance"]]
            ip = np.round(poly).astype(np.int32)
            if ob:
                _fill(frame, ip, col, 0.22)
                cv2.polylines(frame, [ip], True, (0, 0, 0), 6, AA)
                cv2.polylines(frame, [ip], True, col, 3, AA)
            else:
                dashed(frame, poly, (0, 0, 0), 5)
                dashed(frame, poly, col, 2)
        for k, (p, ob) in cur.items():
            if not (-0.5 < p[0] < FIELD_L + 0.5 and -0.5 < p[1] < FIELD_W + 0.5):
                continue
            tr = self.tracks[k]
            c = cam.project(np.array([[p[0], p[1], 0.95]]))[0]
            lbl = self.teams[k] + ("?" if self.idc[k] < 0.5 else "")
            tag(frame, lbl, c[0], c[1] - 6, ALLI[tr["alliance"]], 0.8, faded=not ob)
        # ---- shots
        S = self.S
        act = np.where((S.t0 - 0.02 <= t) & (t <= S.t1 + MADE_HOLD))[0]
        for j in act:
            it = S.items[j]
            uv = it["uv"]
            tau = it["tau"]
            now = t - it["t0"]
            n_end = len(tau)
            n = int(np.searchsorted(tau, now, side="right"))
            col = it["color"]
            flying = now < tau[-1]
            n = min(max(n, 1), n_end)
            if n >= 2 and abs(uv[:n]).max() < 4000:
                seg = np.round(uv[:n]).astype(np.int32)
                if now - tau[-1] <= 0.6:
                    nseg = len(seg) - 1
                    step = max(1, nseg // 10)
                    for a in range(0, nseg, step):
                        b = min(nseg, a + step + 1)
                        w = 2 + int(3 * b / max(nseg, 1))
                        cv2.polylines(frame, [seg[a:b + 1]], False, (0, 0, 0), w + 2, AA)
                        cv2.polylines(frame, [seg[a:b + 1]], False, col, w, AA)
            if flying and n >= 1 and abs(uv[min(n, n_end) - 1]).max() < 4000:
                h = tuple(int(v) for v in np.round(uv[min(n, n_end) - 1]))
                cv2.circle(frame, h, 9, (0, 0, 0), -1, AA)
                cv2.circle(frame, h, 6, (255, 255, 255), -1, AA)
                if np.isfinite(uv[0]).all():
                    text(frame, it["team"], (h[0] + 12, h[1] - 10), 0.6, col, 2, shadow=True)
            if not flying and now - tau[-1] <= MADE_HOLD and abs(uv[-1]).max() < 4000:
                after = now - tau[-1]
                e = uv[-1] + np.array([0, -min(after, 0.5) * 30.0])
                mark(frame, it["outcome"] if it["outcome"] in ("made", "missed", "pass") else "pass", e)
                if it["outcome"] != "made":
                    text(frame, it["team"], (e[0] + 22, e[1] + 6), 0.55, it["color"], 2, shadow=True)
        # ---- feed gap banner
        if in_gap:
            rrect(frame, 560, 40, 1360, 110, (20, 20, 20), 14, alpha=0.85)
            text(frame, "NO CAMERA FEED", (960, 90), 1.3, AMBER, 3, "c")
        # ---- panel
        C = self.counts(t)
        tl = self.traced_live(t, per["name"])
        self.mmkey = hash(tuple(round(float(v[0][0]) / 0.5) * 1000 + round(float(v[0][1]) / 0.5) for v in cur.values())) if False else 0
        P = self.panel(t, rt, mc, per, in_gap, C, tl).copy()
        # minimap
        mm = self.mini_bg.copy()
        for j in act:
            it = S.items[j]
            if it["t0"] <= t <= it["t1"]:
                tau_i = min(max(t - it["t0"], 0), it["tau"][-1])
                P3 = None
        for j in act:
            it = S.items[j]
            if it["t0"] <= t <= it["t1"]:
                s = self.shot_pos(j, t)
                if s is not None:
                    cv2.circle(mm, self._mm(s[0], s[1]), 4, (255, 255, 255), -1, AA)
        for k, (p, ob) in cur.items():
            al = self.tracks[k]["alliance"]
            c = self._mm(p[0], p[1])
            cv2.circle(mm, c, 11, ALLI[al] if ob else (90, 90, 90), -1, AA)
            cv2.circle(mm, c, 11, (255, 255, 255), 1, AA)
            text(mm, self.teams[k][-4:], (c[0], c[1] - 14), 0.42, TXT, 1, "c", shadow=True)
        y = self.mm_y
        rrect(P, 12, y, PANEL_W - 12, y + self.MM_H + 38, CARD, 12)
        text(P, "FIELD (top-down, as in video)", (28, y + 24), 0.45, DIM, 1)
        P[y + 32:y + 32 + self.MM_H, 20:20 + self.MM_W] = mm
        canvas = np.empty((1080, 1920 + PANEL_W, 3), np.uint8)
        canvas[:, :1920] = frame
        canvas[:, 1920:] = P
        return canvas

    def shot_pos(self, j, t):
        it = self.S.items[j]
        if not hasattr(self, "_th"):
            self._th = {}
        s = self.S_src[j]
        th = np.array(list(s["origin"]) + list(s["v0"]) + list(s.get("acc") or [0, 0, 0]), float)
        tau = min(max(t - it["t0"], 0), it["tau"][-1])
        return traj(th, np.array([tau]), float(s.get("drag") or C_DRAG))[0]


# ------------------------------------------------------------------ main entry
def _segment(args):
    cv2.setNumThreads(1)
    R, video_path, ta, n0, n1, part, ow, oh, crf = args
    W0, H0 = 1920 + PANEL_W, 1080
    nf = n1 - n0
    t_a = ta + n0 / OUT_FPS
    dec = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-threads", "2", "-ss", f"{t_a:.4f}", "-i", str(video_path),
         "-vf", f"fps={OUT_FPS},scale=1920:1080", "-frames:v", str(nf), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE, bufsize=1920 * 1080 * 3 * 4)
    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{ow}x{oh}",
         "-r", str(OUT_FPS), "-i", "-", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
         "-threads", "2", "-g", "60", "-pix_fmt", "yuv420p", str(part)], stdin=subprocess.PIPE)
    size = 1920 * 1080 * 3
    last = None
    for n in range(nf):
        buf = dec.stdout.read(size)
        if len(buf) == size:
            last = np.frombuffer(buf, np.uint8).reshape(1080, 1920, 3).copy()
        elif last is None:
            last = np.zeros((1080, 1920, 3), np.uint8)
        canvas = R.compose(last.copy() if len(buf) != size else last, t_a + n / OUT_FPS)
        if (ow, oh) != (W0, H0):
            canvas = cv2.resize(canvas, (ow, oh), interpolation=cv2.INTER_AREA)
        enc.stdin.write(canvas)
    enc.stdin.close()
    enc.wait()
    dec.stdout.close()
    dec.wait()
    return part


def render(video_path, site_match_json, shots_json, cam, out_mp4, log=print, width=1920,
           t_from=None, t_to=None, crf=23, workers=8):
    """Render the accuracy-check video.  Parallel over `workers` time segments (ffmpeg decode/encode
    per segment, concatenated losslessly at the end)."""
    import multiprocessing as mp
    import tempfile
    site = site_match_json if isinstance(site_match_json, dict) else json.load(open(site_match_json))
    ev = shots_json if isinstance(shots_json, dict) else json.load(open(shots_json))
    t0 = time.time()
    R = _R(site, ev, cam, log)
    R.S_src = ev["shots"]
    cap = cv2.VideoCapture(str(video_path))
    vdur = cap.get(cv2.CAP_PROP_FRAME_COUNT) / (cap.get(cv2.CAP_PROP_FPS) or 60.0)
    cap.release()
    t_end_match = R.f0 + site["frames"] / R.hz
    ta = max(0.0, R.f0 if t_from is None else float(t_from))
    tb = min(vdur - 0.1, t_end_match if t_to is None else float(t_to))
    nout = int((tb - ta) * OUT_FPS)
    W0, H0 = 1920 + PANEL_W, 1080
    sc = width / W0
    ow, oh = width - width % 2, int(round(H0 * sc)) // 2 * 2
    workers = max(1, min(workers, nout // 60 or 1))
    edges = np.linspace(0, nout, workers + 1).astype(int)
    tmp = Path(tempfile.mkdtemp(prefix="overlay_", dir=str(Path(out_mp4).resolve().parent)))
    jobs = [(R, video_path, ta, int(edges[i]), int(edges[i + 1]), tmp / f"p{i:02d}.mp4", ow, oh, crf)
            for i in range(workers)]
    log(f"overlay: {nout} frames, {workers} workers, {ow}x{oh}")
    with mp.get_context("fork").Pool(workers) as pool:
        parts = pool.map(_segment, jobs, chunksize=1)
    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c", "copy", "-movflags", "+faststart", str(out_mp4)], check=True)
    for p in parts:
        p.unlink()
    lst.unlink()
    tmp.rmdir()
    log(f"overlay done {nout} frames in {time.time() - t0:.0f}s -> {out_mp4}")
    return out_mp4


if __name__ == "__main__":
    import argparse
    import camera as CAMM
    ap = argparse.ArgumentParser()
    ap.add_argument("video"); ap.add_argument("site"); ap.add_argument("events")
    ap.add_argument("cam_json"); ap.add_argument("processed"); ap.add_argument("out")
    ap.add_argument("--from", dest="tf", type=float); ap.add_argument("--to", dest="tt", type=float)
    ap.add_argument("--width", type=int, default=1920); ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    site = json.load(open(a.site))
    pose = json.load(open(a.processed))["camera_pose"]
    cam = CAMM.Camera(dict(json.load(open(a.cam_json)), **pose))
    render(a.video, site, json.load(open(a.events)), cam, a.out, width=a.width, t_from=a.tf, t_to=a.tt, workers=a.workers)
