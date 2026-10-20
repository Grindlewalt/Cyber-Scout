"""Local detector training (live/train/cstrain.py, cstrain_core.py): ids, hashes and the near-duplicate filter,
splits by match, weighted box fusion, uncertainty, label priority (review > import > auto), YOLO text, the detection
metrics, and `build` end to end on a synthetic project.  No torch / ultralytics / video needed."""
import json
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "live", "train"))

import cstrain_core as K  # noqa: E402
import cstrain as CT  # noqa: E402


# ---------------------------------------------------------------- ids, hashes
def test_ids():
    assert K.frame_id("2026wasam", "qm12", 43.5) == "2026wasam_qm12_043500"
    assert K.safe_id("a b/c.d") == "a-b-c-d"
    assert CT.match_of("/m/2026wasam/2026wasam_qm12.mp4", "2026wasam") == "qm12"
    assert CT.match_of("/m/live/2026wagg/319/clips/tba-qm20.mp4", "2026wagg") == "qm20"
    assert CT.match_of("/x/2026wagg_qm7_cut.mp4", "2026wagg", r"(qm\d+)") == "qm7"


def test_phash_and_dedupe():
    rng = np.random.default_rng(0)
    a = rng.integers(0, 255, (32, 32)).astype(float)
    a = (a + np.roll(a, 1, 0) + np.roll(a, 1, 1)) / 3            # some structure
    b = a + rng.normal(0, 2, a.shape)                               # the same picture, a little noise
    c = rng.integers(0, 255, (32, 32)).astype(float)
    ha, hb, hc = K.phash(a), K.phash(b), K.phash(c)
    assert len(ha) == 16 and K.hamming(ha, ha) == 0
    assert K.hamming(ha, hb) <= 6 < K.hamming(ha, hc)
    d = K.Dedupe(6)
    assert d.add(ha) and not d.add(hb) and d.add(hc)
    assert d.near(hb) == [0] and d.near(hc) == [1]
    for k in range(200):                                            # growth past the first buffer
        d.push(f"{k:016x}")
    assert d.n == 202 and d.near(hc) == [1]
    assert K.Dedupe(6, [ha]).add(hb) is False


def test_thumb_change():
    a = np.full((54, 96), 100, np.uint8)
    assert K.thumb_change(a, a) == 0
    b = a.copy()
    b[:27] = 110
    assert K.thumb_change(a, b) == pytest.approx(5.0)


# ---------------------------------------------------------------- splits
def test_split_by_match():
    s = [K.split_of("2026wasam", f"qm{i}") for i in range(3000)]
    assert s == [K.split_of("2026wasam", f"qm{i}") for i in range(3000)]      # deterministic
    assert 0.07 < s.count("val") / len(s) < 0.13 and "test" not in s
    assert K.split_of("2026wagg", "qm1", test_events=["2026wagg"]) == "test"
    t = [K.split_of("e", f"m{i}", 0.1, (), 0.1) for i in range(3000)]
    assert 0.07 < t.count("test") / len(t) < 0.13


# ---------------------------------------------------------------- fusion, uncertainty
def test_iou():
    assert K.iou([0, 0, 10, 10], [[0, 0, 10, 10], [5, 0, 15, 10], [20, 20, 30, 30]]).tolist() == \
        pytest.approx([1.0, 1 / 3, 0.0])


def test_wbf():
    t1 = (np.array([[0, 0, 10, 10], [50, 50, 60, 60]]), np.array([0.9, 0.8]), np.array([0, 0]))
    t2 = (np.array([[1, 1, 11, 11]]), np.array([0.7]), np.array([0]))
    out = sorted(K.wbf([t1, t2]), key=lambda b: b["b"][0])
    assert len(out) == 2
    both, one = out
    assert both["n"] == 2 and both["p"] == pytest.approx((0.9 + 0.7) / 2)
    assert both["b"][0] == pytest.approx(0.7 / 1.6, abs=0.06)        # score-weighted mean of 0 and 1
    assert one["n"] == 1 and one["p"] == pytest.approx(0.8 / 2)      # only half the teachers see it
    w = K.wbf([t1, t2], weights=[3, 1])
    assert max(b["p"] for b in w) == pytest.approx((3 * 0.9 + 0.7) / 4)
    cls = K.wbf([(np.array([[0, 0, 10, 10]]), np.array([0.9]), np.array([0])),
                 (np.array([[0, 0, 10, 10]]), np.array([0.9]), np.array([1]))])
    assert sorted(b["c"] for b in cls) == [0, 1]                       # classes are never merged
    tta = K.wbf([(np.array([[0, 0, 10, 10], [0, 0, 10, 10]]), np.array([0.6, 0.9]), np.array([0, 0]))])
    assert len(tta) == 1 and tta[0]["p"] == pytest.approx(0.9)       # one teacher's duplicates: its best score


def test_uncertainty():
    assert K.box_uncertainty(0.5) == 1.0 and K.box_uncertainty(1.0) == 0.0
    assert K.box_uncertainty(0.95, n=1, T=2) == 0.5                  # sure, but only one of two teachers
    assert K.frame_uncertainty([]) == 0.0
    assert K.frame_uncertainty([{"p": 0.95, "n": 2}, {"p": 0.5, "n": 2}], T=2) == 1.0


# ---------------------------------------------------------------- label priority
REV = {"status": "fixed", "boxes": [{"c": 0, "b": [1, 2, 3, 4]}], "diff": {"added": 1}, "conf": 0.9}
IMP = {"boxes": [{"c": 0, "b": [5, 6, 7, 8]}]}
AUTO = {"u": 0.2, "boxes": [{"c": 0, "b": [0, 0, 9, 9], "p": 0.9}, {"c": 0, "b": [20, 20, 29, 29], "p": 0.2}]}


def test_pick_labels_priority():
    assert K.pick_labels(REV, IMP, AUTO) == ("review", [(0, [1, 2, 3, 4])])
    assert K.pick_labels(dict(REV, status="ok"), None, AUTO)[0] == "review"
    assert K.pick_labels(dict(REV, status="skip"), IMP, AUTO) == (None, None)
    assert K.pick_labels(dict(REV, status="bad"), IMP, AUTO) == (None, None)
    assert K.pick_labels(None, IMP, AUTO) == ("import", [(0, [5, 6, 7, 8])])
    assert K.pick_labels(None, None, AUTO) == ("auto", [(0, [0, 0, 9, 9])])      # the 0.2 box is no label
    assert K.pick_labels() == (None, None)


def test_pick_labels_doubtful_auto_frames():
    mid = {"u": 0.9, "boxes": [{"c": 0, "b": [0, 0, 9, 9], "p": 0.9}, {"c": 0, "b": [20, 20, 29, 29], "p": 0.35}]}
    assert K.pick_labels(None, None, mid) == (None, None)                       # a probable robot under the bar
    assert K.pick_labels(None, None, mid, strict=False) == ("auto", [(0, [0, 0, 9, 9])])
    assert K.pick_labels(None, None, mid, auto_mid=0.4)[0] == "auto"
    bg = {"u": 0.8, "boxes": [{"c": 0, "b": [0, 0, 9, 9], "p": 0.9}, {"c": 0, "b": [30, 30, 39, 39], "p": 0.4,
                                                                         "src": "bglabel"}]}
    assert K.pick_labels(None, None, bg) == (None, None)
    assert K.pick_labels(None, None, bg, auto_min_conf=0.3, strict=False) == ("auto", [(0, [0, 0, 9, 9])])
    assert K.pick_labels(None, None, AUTO, auto_max_u=0.1) == (None, None)
    assert K.pick_labels({"status": "ok", "boxes": []}, None, mid) == ("review", [])   # a reviewed empty frame


# ---------------------------------------------------------------- YOLO text, class maps
def test_yolo_text():
    t = K.to_yolo([(0, [100, 50, 300, 250]), (0, [-10, -10, 2, 2]), (1, [900, 500, 1100, 700])], 1000, 600)
    rows = t.splitlines()
    assert rows[0] == "0 0.200000 0.250000 0.200000 0.333333"
    assert len(rows) == 2 and rows[1].startswith("1 0.950000")             # clipped to the frame; tiny one dropped
    back = K.from_yolo(t, 1000, 600)
    assert back[0] == (0, [100.0, 50.0, 300.0, 250.0])
    assert K.from_yolo("0 0.1 0.1 0.3 0.1 0.3 0.4 0.1 0.4\n", 100, 100) == [(0, [10.0, 10.0, 30.0, 40.0])]
    assert K.from_yolo("3 0.5 0.5 0.2 0.2\n0 0.5 0.5 0.2 0.2\n", 10, 10, {3: 0}) == [(0, [4.0, 4.0, 6.0, 6.0])]
    assert K.from_yolo("bad\n0 1 2\n", 10, 10) == []


def test_class_map():
    assert K.class_map(["Robot", "note"], ["robot"]) == {0: 0}
    assert K.class_map(["red_alliance", "blue_alliance", "ref"], ["robot"]) == {0: 0, 1: 0}
    assert K.class_map(["bot", "piece"], ["robot", "piece"], {"bot": 0}) == {0: 0, 1: 1}


# ---------------------------------------------------------------- metrics
def test_average_precision():
    assert K.average_precision([True, True], [0.9, 0.8], 2) == pytest.approx(1.0)
    assert K.average_precision([], [], 3) == 0.0
    assert np.isnan(K.average_precision([], [], 0))
    ap = K.average_precision([True, False, True], [0.9, 0.8, 0.7], 2)          # P 1, 0.5, 0.67 at R 0.5, 0.5, 1
    assert ap == pytest.approx((51 * 1.0 + 50 * (2 / 3)) / 101, abs=1e-6)


def test_evaluate():
    g = [(0, [0, 0, 10, 10]), (0, [20, 20, 30, 30])]
    perfect = K.evaluate([(g, [(0, b, 0.9) for _, b in g])])
    assert perfect["map50"] == pytest.approx(1.0) and perfect["map"] == pytest.approx(1.0)
    assert perfect["p"] == 1.0 and perfect["r"] == 1.0
    half = K.evaluate([(g, [(0, [0, 0, 10, 10], 0.9), (0, [50, 50, 60, 60], 0.8), (0, [20, 20, 30, 30], 0.2)])])
    assert half["per_class"][0]["tp"] == 1 and half["per_class"][0]["fp"] == 1   # at conf 0.30
    assert half["r"] == 0.5 and half["p"] == 0.5
    shifted = K.evaluate([([(0, [0, 0, 10, 10])], [(0, [2, 0, 12, 10], 0.9)])])   # IoU 0.67
    assert shifted["map50"] == pytest.approx(1.0) and shifted["map"] < 0.5
    two = K.evaluate([(g, [(0, g[0][1], 0.9)]), ([(1, [0, 0, 5, 5])], [])], nc=2)
    assert two["per_class"][1]["ap50"] == 0.0 and two["n_gt"] == 3


# ---------------------------------------------------------------- views, build
def test_frame_view(tmp_path):
    md = tmp_path / "matches"
    md.mkdir()
    (md / "2026wagg_qm20.json").write_text(json.dumps({"dynamic": {"view": "FFFM-M"}}))
    info = CT.view_info("2026wagg", "qm20", None, str(md), None, "livestream")
    assert info[0] == "dyn"
    assert [CT.frame_view(info, t) for t in (0.05, 0.35, 0.45, 9.0)] == ["F", "M", "?", "?"]
    assert CT.view_info("2026wasam", "qm1", None, str(md), "/cam.json", "upload") == ("F",)
    assert CT.view_info("2026wasam", "qm1", None, str(md), None, "upload") == ("?",)


def make_project(tmp_path, monkeypatch):
    monkeypatch.setattr(CT, "TRAIN_DIR", str(tmp_path))
    CT.main(["init", "p", "--classes", "robot"])
    P = str(tmp_path / "p")
    recs = []
    for ev, src in (("2026wasam", "upload"), ("2026wagg", "livestream"), ("pub_frc", "public")):
        os.makedirs(os.path.join(P, "frames", ev))
        for m in range(6):
            for t in range(3):
                fid = K.frame_id(ev, f"qm{m}", t)
                rel = f"frames/{ev}/{fid}.jpg"
                open(os.path.join(P, rel), "wb").write(b"jpg")
                recs.append(dict(id=fid, path=rel, event=ev, match=f"qm{m}", video="v", t=float(t), w=100, h=50,
                                 view="F", phash=f"{len(recs):016x}", src=src,
                                 split="val" if (m == 0 and src != "public") else "train"))
    CT.append_index(P, recs)
    for r in recs:
        if r["src"] == "public":
            CT.jsave(os.path.join(P, "labels", "import", r["id"] + ".json"),
                     {"id": r["id"], "boxes": [{"c": 0, "b": [10, 10, 30, 30]}]})
            continue
        boxes = [{"c": 0, "b": [10, 10, 30, 30], "p": 0.9}]
        if r["match"] == "qm1":
            boxes.append({"c": 0, "b": [50, 10, 70, 30], "p": 0.35})        # doubtful frames
        if r["match"] == "qm2" and r["t"] == 0:
            boxes = []                                                    # empty (a negative)
        CT.jsave(os.path.join(P, "labels", "auto", r["id"] + ".json"), {"id": r["id"], "boxes": boxes, "u": 0.1})
    rid = K.frame_id("2026wasam", "qm1", 0)
    CT.jsave(os.path.join(P, "labels", "review", rid + ".json"),
             {"id": rid, "status": "fixed", "boxes": [{"c": 0, "b": [50, 10, 70, 30]}], "by": "t", "at": 0,
              "diff": {"added": 1}, "conf": 1.0})
    return P


def test_build(tmp_path, monkeypatch):
    P = make_project(tmp_path, monkeypatch)
    CT.main(["build", "d1", "--project", "p", "--test-events", "2026wagg", "--dup", "review=3", "--neg-max", "0"])
    D = os.path.join(P, "datasets", "d1")
    y = open(os.path.join(D, "data.yaml")).read()
    assert "test: images/test" in y and 'names: ["robot"]' in y and "nc: 1" in y
    man = json.load(open(os.path.join(D, "manifest.json")))
    ims = {s: sorted(os.listdir(os.path.join(D, "images", s))) for s in ("train", "val", "test")}
    assert len(ims["test"]) == 18                                          # the whole held-out event, every frame
    assert all(f.startswith("2026wagg_") for f in ims["test"])
    assert len(ims["val"]) == 3 and all("_qm0_" in f for f in ims["val"])
    rid = K.frame_id("2026wasam", "qm1", 0)
    assert man["frames"][rid] == ["train", "review", 1]
    assert {f for f in ims["train"] if f.startswith(rid)} == {f"{rid}.jpg", f"{rid}__d1.jpg", f"{rid}__d2.jpg"}
    assert K.frame_id("2026wasam", "qm1", 1) not in man["frames"]          # doubtful auto frame left out of train
    assert K.frame_id("2026wasam", "qm2", 0) not in man["frames"]          # negatives capped at 0
    assert sum(f.startswith("pub_frc_") for f in ims["train"]) == 18       # public sets go to train
    assert man["counts"]["train"]["left_out"] == 2
    lab = open(os.path.join(D, "labels", "train", rid + ".txt")).read()
    assert lab == "0 0.600000 0.400000 0.200000 0.400000\n"
    assert os.stat(os.path.join(D, "images", "train", rid + ".jpg")).st_ino == \
        os.stat(os.path.join(P, "frames", "2026wasam", rid + ".jpg")).st_ino  # hardlinked, not copied
    with pytest.raises(SystemExit):
        CT.main(["build", "d1", "--project", "p"])                         # never silently replaced
    CT.main(["build", "d1", "--project", "p", "--force", "--no-public", "--neg-max", "1"])
    man = json.load(open(os.path.join(D, "manifest.json")))
    assert not any(k.startswith("pub_") for k in man["frames"])
    assert man["frames"][K.frame_id("2026wasam", "qm2", 0)] == ["train", "auto", 0]


def test_load_index_last_wins(tmp_path, monkeypatch):
    P = make_project(tmp_path, monkeypatch)
    idx = CT.load_index(P)
    r = dict(next(iter(idx.values())), split="test")
    CT.append_index(P, [r])
    assert CT.load_index(P)[r["id"]]["split"] == "test" and len(CT.load_index(P)) == len(idx)
