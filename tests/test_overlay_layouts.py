"""Stream analyser scoreboard layouts (live/livestream.py): the 2026 FIRST top-centre scoreboard and the full-width bottom
score bar (2026wagg Twitch program feed), auto-detected per frame; apply_layout moves the layout an event's clock box
belongs to.  Real fixtures: tests/fixtures/scorebar_*.jpg = the overlay strip of a 1920x1080 broadcast frame (qm33 and
Final 1 of 2026wagg, qm12 of 2026wasam), read with the repo's OCR model on the CPU."""
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "live"))
cv2 = pytest.importorskip("cv2")
import livestream as LS  # noqa: E402
import layout as LY  # noqa: E402

FIX = os.path.join(HERE, "fixtures")


def frame(name, y0):
    """A 1920x1080 frame: grey picture + the real overlay strip at row y0."""
    im = np.full((1080, 1920, 3), 90, np.uint8)
    s = cv2.imread(os.path.join(FIX, name))
    im[y0:y0 + s.shape[0]] = s
    return im


QM33 = lambda: frame("scorebar_bottom_qm33.jpg", 826)
F1M1 = lambda: frame("scorebar_bottom_f1m1.jpg", 826)
TOPQ = lambda: frame("scorebar_top_qm12.jpg", 0)


@pytest.fixture(autouse=True)
def defaults():
    LS.apply_layout(None)
    yield
    LS.apply_layout(None)


class StubOCR:
    device = "stub"

    def read(self, im, boxes, alphabets, trim=None, stretch=None):
        return [("", 0.0)] * len(boxes)


@pytest.fixture(scope="module")
def ocr():
    os.environ["FORCE_CPU"] = "1"
    try:
        return LS.OverlayOCR()
    except Exception as e:                       # no onnxruntime / model in this checkout
        pytest.skip(f"overlay OCR unavailable: {e!r}")


def test_each_frame_finds_its_scoreboard_layout():
    sc = LS.Scanner(StubOCR(), log=lambda *a: None)
    g, left = sc.detect(QM33())
    assert (g.name, left) == ("bottom", "blue")
    g, left = sc.detect(F1M1())
    assert (g.name, left) == ("bottom", "red")            # playoffs: red on the left (side colours are read)
    g, left = sc.detect(TOPQ())
    assert (g.name, left) == ("top", "red")
    assert sc.detect(np.full((1080, 1920, 3), 90, np.uint8)) == (None, None)
    assert LS.TOP.present(QM33()) is None and LS.BOTTOM.present(TOPQ()) is None
    assert sc.geo_n == {"bottom": 2, "top": 1}


def test_detect_geometry_votes_over_frames_any_size():
    fr = [QM33()] * 3 + [np.full((1080, 1920, 3), 90, np.uint8)] * 2
    g, share = LS.detect_geometry(fr)
    assert g is LS.BOTTOM and share == 0.6
    assert LS.detect_geometry([cv2.resize(f, (1280, 720), interpolation=cv2.INTER_AREA) for f in fr])[0] is LS.BOTTOM
    assert LS.detect_geometry(fr[3:]) == (None, 0.0)
    assert LS.detect_geometry([QM33()] + fr[3:] * 5)[0] is None           # 1 of 11 frames: not enough


def test_read_bottom_bar_qualification(ocr):
    sc = LS.Scanner(ocr, log=lambda *a: None)
    im = QM33()
    g, left = sc.detect(im)
    r = sc.read_scoreboard(100.0, im, left, True, g)
    assert (r["clock"], r["teleop"], r["shift"]) == (80, True, 3)            # 1:20, "3 / 6"
    assert r["score"] == {"blue": 9, "red": 307}
    assert r["teams"] == {"blue": [2980, 9997, 9980], "red": [2910, 5588, 360]}
    assert r["match"][:3] == ("qm", 1, 33)
    assert r["name"] == {"blue": None, "red": None}                         # "BLUE" / "RED" are not alliance names
    assert r["geo"] == "bottom" and r["layout"] == "bottom"


def test_read_bottom_bar_playoff(ocr):
    sc = LS.Scanner(ocr, log=lambda *a: None)
    im = F1M1()
    g, left = sc.detect(im)
    r = sc.read_scoreboard(100.0, im, left, True, g)
    assert (r["clock"], r["teleop"]) == (103, True)
    assert r["score"] == {"red": 205, "blue": 117}
    assert r["teams"] == {"red": [2910, 2046, 5588], "blue": [3574, 360, 4469]}
    assert r["match"][:3] == ("f", 1, 1)
    assert r["name"] == {"red": "Alliance 1", "blue": "Alliance 2"}          # (OCR may drop the space: normalised)


def test_top_scoreboard_reads_as_before(ocr):
    sc = LS.Scanner(ocr, log=lambda *a: None)
    im = TOPQ()
    g, left = sc.detect(im)
    r = sc.read_scoreboard(100.0, im, left, True, g)
    assert (r["clock"], r["teleop"], r["shift"]) == (105, True, 3)
    assert r["score"] == {"red": 70, "blue": 101}
    assert r["teams"] == {"red": [6350, 4911, 5937], "blue": [7461, 4469, 8032]}
    assert r["match"][:3] == ("qm", 1, 12) and r["layout"] == "qual" and r["geo"] == "top"


def _layout(box):
    return LY.Layout({"version": 1, "processing_source": "s1",
                      "sources": {"s1": {"W": 1920, "H": 1080, "regions": [{"kind": "clock", "box": list(box)}]}}}, "mem")


def test_apply_layout_moves_the_layout_the_clock_box_belongs_to():
    b = LS.BOTTOM.clock_box
    assert LS.apply_layout(_layout((b[0] + 10, b[1] + 6, b[2] + 10, b[3] + 6)), "s1", log=lambda *a: None)
    m = LS.GEOMETRIES[0]
    assert m.base == "bottom" and m.name == "bottom@layout"
    s0 = LS.BOTTOM.sb["score_l"]
    assert m.sb["score_l"] == (s0[0] + 10, s0[1] + 6, s0[2] + 10, s0[3] + 6)
    assert LS.GEOMETRIES[1:] == list(LS.KNOWN)                               # the defaults stay as fallbacks
    assert LS.CLOCK_BOX == LS.TOP.clock_box                                  # the top layout's names unchanged
    # the top one (the old behaviour): every SB box moves with the clock box
    t = LS.TOP.clock_box
    assert LS.apply_layout(_layout((t[0] - 20, t[1] + 4, t[2] - 20, t[3] + 4)), "s1", log=lambda *a: None)
    assert LS.CLOCK_BOX == (t[0] - 20, t[1] + 4, t[2] - 20, t[3] + 4)
    assert LS.SB["score_l"][0] == LS.TOP.sb["score_l"][0] - 20 and LS.GEOMETRIES[0].base == "top"
    # nowhere near a known scoreboard / the default box / no layout: the defaults, auto-detected
    assert not LS.apply_layout(_layout((600, 500, 700, 540)), "s1", log=lambda *a: None)
    assert LS.GEOMETRIES == list(LS.KNOWN) and LS.CLOCK_BOX == LS.TOP.clock_box
    assert not LS.apply_layout(_layout(b), "s1") and LS.GEOMETRIES == list(LS.KNOWN)
    assert not LS.apply_layout(None)


def test_a_wrong_clock_box_cannot_hide_the_scoreboard():
    # the auto set-up's old 1 Hz search found the small shift timer above the clock: mapped, that layout fails the
    # white-box + side-colour test, and the default bottom layout still reads the frame
    LS.apply_layout(_layout((890, 917, 1070, 963)), "s1", log=lambda *a: None)
    assert LS.GEOMETRIES[0].base == "bottom"
    sc = LS.Scanner(StubOCR(), log=lambda *a: None)
    g, left = sc.detect(QM33())
    assert g is LS.BOTTOM and left == "blue"


def test_a_layout_moved_off_the_frame_is_never_on_air():
    LS.apply_layout(_layout((958, 1024, 961, 1027)), "s1", log=lambda *a: None)      # every box shrinks to ~0 px
    m = LS.GEOMETRIES[0]
    assert m.base == "bottom" and m.present(QM33()) is None
    sc = LS.Scanner(StubOCR(), log=lambda *a: None)
    assert sc.detect(QM33())[0] is LS.BOTTOM


def test_ticks_of_the_bottom_clock():
    sc = LS.Scanner(StubOCR(), log=lambda *a: None)
    base = QM33()
    x0, y0, x1, y1 = LS.BOTTOM.clock_box
    for k in range(61):                       # 6 s at 10 Hz, the seconds digit changes every second
        t = k / 10.0
        im = base.copy()
        im[y0:y1, x0:x1] = 255
        cv2.putText(im, f"1:{40 - int(t):02d}", (x0 + 6, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 2.2, (20, 20, 20), 6)
        sc.push(t, im)
    assert len(sc.ticks) == 6 and np.allclose(np.diff(sc.ticks), 1.0, atol=0.11)
    assert all(s[1] for s in sc.samples) and len(sc.reads) >= 11 and sc.reads[0]["geo"] == "bottom"
    assert len(sc.thumbs) == len(sc.reads) and cv2.imdecode(sc.thumbs[0][1], cv2.IMREAD_COLOR).shape[1] == 1040
