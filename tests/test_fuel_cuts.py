"""Regression tests for live/fuel.py scene-cut handling (no GPU, no video files).

Bug: a hard cut / wipe inside the fuel window inflated the duplicate-frame threshold
(ema_diff); every real frame was then called a duplicate and fuel tracking stayed blind
(livestream clip of 2026wasam qm13: a frozen lead-in, then a cut that seeded ema_diff with
81 % -> all auto shots lost).  Synthetic 30p-in-60p frame sequences go through
FuelTracker.push() and the processed-frame counts are checked around the cuts.
"""
import os
import sys

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "live"))

import camera as CAMM  # noqa: E402
import fuel as F  # noqa: E402

CAM_JSON = os.path.join(ROOT, "events", "2026wasam", "camera.json")
H, W = 1080, 1920
FPS = 60.0


@pytest.fixture(scope="module")
def cam():
    if not os.path.exists(CAM_JSON):
        pytest.skip("no camera calibration")
    import json
    return CAMM.Camera(json.load(open(CAM_JSON)))


def _scene(seed, level):
    """Static textured background (16 px cells) around a mean luma level."""
    r = np.random.default_rng(seed)
    small = r.integers(level - 40, level + 40, (H // 16, W // 16, 3)).clip(0, 255).astype(np.uint8)
    return cv2.resize(small, (W, H), interpolation=cv2.INTER_NEAREST)


class Clip:
    """30p content in a 60p container: a new picture on even frames, exact repeats on odd ones.
    Moving dark squares change ~2 % of the pixels per new picture (like a real match)."""

    def __init__(self, seed=0, level=100):
        self.base = _scene(seed, level)
        r = np.random.default_rng(seed + 100)
        self.xy = np.c_[r.integers(100, W - 400, 40), r.integers(150, H - 300, 40)]
        self.v = r.uniform(-10, 10, (40, 2))              # incoherent motion (not a camera pan)
        self.k = 0

    def frame(self, step=True):
        if step:
            self.k += 1
        im = self.base.copy()
        for (x, y), (vx, vy) in zip(self.xy, self.v):
            x, y = int(x + vx * self.k) % (W - 80), int(y + vy * self.k) % (H - 80)
            im[y:y + 60, x:x + 60] = 20
        return im


def _run(cam, frames):
    """frames: list of (t, bgr) at 60 fps -> (tracker, [n_proc after each frame])."""
    ft = F.FuelTracker(cam, log=lambda *a: None, floor_every=0)
    proc = []
    for i, (t, im) in enumerate(frames):
        ft.push(i, t, im)
        proc.append(ft.n_proc)
    return ft, np.array(proc)


def _processed(proc, ts, a, b):
    ts = np.asarray(ts)
    i0, i1 = np.searchsorted(ts, a), np.searchsorted(ts, b)
    return int(proc[i1 - 1] - proc[i0 - 1])


def _seq(parts):
    """parts: [(seconds, fn(i) -> bgr)] -> [(t, bgr)] at 60 fps."""
    out, n = [], 0
    for dur, fn in parts:
        for i in range(int(round(dur * FPS))):
            out.append((n / FPS, fn(i)))
            n += 1
    return out


def test_clean_sequence_unchanged(cam):
    """No cuts: every new picture processed, every repeat skipped, no resets."""
    a = Clip()
    frames = _seq([(2.0, lambda i: a.frame(step=i % 2 == 0))])
    ft, proc = _run(cam, frames)
    assert ft.cuts == [] and ft.n_cut_frames == 0 and ft.n_dup_reset == 0
    assert ft.n_dup == 60 and ft.n_proc == 60
    assert 1.0 < ft.ema_diff < 4.0


def test_frozen_lead_in_then_cut(cam):
    """The stream bug: frozen picture (threshold never seeded) then a hard cut to the match.
    Old code seeded ema_diff with the cut (~90 %) and processed ~nothing for 30 s."""
    frozen = _scene(7, 100)
    b = Clip(seed=1, level=100)                           # same brightness: only the d-test sees it
    frames = _seq([(1.0, lambda i: frozen), (3.0, lambda i: b.frame(step=i % 2 == 0))])
    ts = [t for t, _ in frames]
    ft, proc = _run(cam, frames)
    assert ft.cuts == [pytest.approx(1.0, abs=0.02)]
    n = _processed(proc, ts, 1.3, 3.3)                   # 60 new pictures in these 2 s
    assert 57 <= n <= 63, n
    assert ft.ema_diff < 5


def test_cut_to_graphics_and_back(cam):
    """Match -> 1 s of bright graphics -> match: both cuts found, tracking resumes at once."""
    a = Clip(seed=0, level=100)
    gfx = _scene(3, 200)
    frames = _seq([(1.5, lambda i: a.frame(step=i % 2 == 0)), (1.0, lambda i: gfx),
                   (2.0, lambda i: a.frame(step=i % 2 == 0))])
    ts = [t for t, _ in frames]
    ft, proc = _run(cam, frames)
    assert len(ft.cuts) == 2
    assert ft.cuts[0] == pytest.approx(1.5, abs=0.02) and ft.cuts[1] == pytest.approx(2.5, abs=0.02)
    n = _processed(proc, ts, 2.8, 4.5)                   # 51 new pictures
    assert 48 <= n <= 54, n
    assert ft.ema_diff < 5


def test_wipe_transition(cam):
    """A 0.5 s wipe to other content and a cut back: no lasting blindness."""
    a = Clip(seed=0, level=100)
    other = _scene(5, 170)

    def wipe(i):
        im = a.frame(step=i % 2 == 0)
        x = int(W * min(1.0, (i + 1) / 30.0))
        im[:, :x] = other[:, :x]
        return im

    frames = _seq([(1.0, lambda i: a.frame(step=i % 2 == 0)), (0.5, wipe), (0.5, lambda i: other),
                   (2.0, lambda i: a.frame(step=i % 2 == 0))])
    ts = [t for t, _ in frames]
    ft, proc = _run(cam, frames)
    assert ft.cuts and ft.cuts[0] < 1.5
    n = _processed(proc, ts, 2.4, 4.0)                   # 48 new pictures
    assert 45 <= n <= 51, n


def test_inflated_threshold_recovers(cam):
    """Whatever inflates ema_diff (a missed transition), a run of 'duplicates' while the
    picture keeps changing re-seeds it within a few frames."""
    a = Clip()
    frames = _seq([(3.0, lambda i: a.frame(step=i % 2 == 0))])
    ft = F.FuelTracker(cam, log=lambda *a: None, floor_every=0)
    proc = []
    for i, (t, im) in enumerate(frames):
        if i == 60:
            ft.ema_diff = 80.0
        ft.push(i, t, im)
        proc.append(ft.n_proc)
    proc = np.array(proc)
    assert ft.n_dup_reset >= 1
    assert proc[119] - proc[71] >= 22                    # 1.2 -> 2.0 s: 24 new pictures
    assert ft.ema_diff < 5


def test_no_stitching_across_cut():
    A = F._Track(0, 0.0, 0, (100.0, 500.0, 4, 30, 8, 0.0))
    for k in range(1, 8):
        A.add(k / 30, k, (100.0 + 20 * k, 500.0, 4, 30, 8, 0.0))
    B = F._Track(1, 10 / 30, 10, (300.0, 500.0, 4, 30, 8, 0.0))
    for k in range(11, 18):
        B.add(k / 30, k, (100.0 + 20 * k, 500.0, 4, 30, 8, 0.0))
    import copy
    assert len(F.stitch(copy.deepcopy([A, B]))) == 1       # same flight, a 0.1 s gap: joined
    assert len(F.stitch(copy.deepcopy([A, B]), cuts=[0.3])) == 2
    assert F._cut_between([0.3], 0.25, 0.34) and not F._cut_between([0.3], 0.3, 0.34)
    assert not F._cut_between([], 0.0, 1.0)
