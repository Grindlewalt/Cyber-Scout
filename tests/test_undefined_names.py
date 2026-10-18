"""Undefined names in the pipeline modules (2026-10-17: a NameError in process.finish() failed every match of a
comp day; the tests fake process() so nothing ran that line).  Needs pyflakes (skipped without it)."""
import glob, os
import pytest

pyflakes_api = pytest.importorskip("pyflakes.api")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_no_undefined_names_in_live_and_server():
    from pyflakes import reporter as R
    import io
    out = io.StringIO()
    files = sorted(glob.glob(os.path.join(ROOT, "live", "*.py")) + glob.glob(os.path.join(ROOT, "live", "strategy", "*.py"))
                   + glob.glob(os.path.join(ROOT, "server", "**", "*.py"), recursive=True))
    for f in files:
        pyflakes_api.checkPath(f, R.Reporter(out, out))
    bad = [l for l in out.getvalue().splitlines() if "undefined name" in l]
    assert not bad, "\n".join(bad)
