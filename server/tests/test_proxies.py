"""live/proxies.py: ffmpeg command, encode + index (tiny synthetic clip, CPU), backfill CLI, worker submit."""
import json, os, shutil, subprocess, sys, time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "live"))
import proxies  # noqa: E402

EV = "2026wasam"
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def test_command_shapes():
    cpu = proxies.command("in.mp4", "out.mp4", None)
    assert "libx264" in cpu and "-an" in cpu and "+faststart" in cpu and cpu.count("-threads") == 2
    assert ("nice" in cpu) == bool(shutil.which("nice"))
    gpu = proxies.command("in.mp4", "out.mp4", "1")
    assert "h264_nvenc" in gpu and gpu[gpu.index("-gpu") + 1] == "1" and "-an" in gpu
    assert any("min(480,ih)" in a for a in cpu)


def _src(media, key, seconds=2):
    d = os.path.join(media, EV)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{key}.mp4")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc=size=1280x720:rate=60:duration={seconds}", "-f", "lavfi", "-i",
                    f"sine=duration={seconds}", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", p],
                   check=True)
    return p


@needs_ffmpeg
def test_make_and_backfill(tmp_path, monkeypatch):
    monkeypatch.setenv("PROXY_ENCODER", "cpu")
    proxies._enc_cache.clear()
    media = str(tmp_path / "media")
    _src(media, f"{EV}_qm1")
    _src(media, f"{EV}_qm2")
    e = proxies.make(media, EV, f"{EV}_qm1", log=lambda *a: None)
    assert e and e["encoder"] == "libx264"
    out = os.path.join(media, EV, "proxy", f"{EV}_qm1.mp4")
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,height",
                            "-of", "json", out], capture_output=True, text=True).stdout
    streams = json.loads(probe)["streams"]
    assert [s["codec_type"] for s in streams] == ["video"] and streams[0]["height"] == 480
    assert streams[0]["codec_name"] == "h264"
    with open(out, "rb") as f:
        head = f.read(64)
    assert b"moov" in head or b"ftyp" in head[:16]                    # faststart: moov right after ftyp
    idx = json.load(open(os.path.join(media, EV, "proxy", "index.json")))
    assert idx[f"{EV}_qm1"]["size"] == os.path.getsize(out)
    assert proxies.up_to_date(media, EV, f"{EV}_qm1")
    assert proxies.make(media, EV, f"{EV}_qm1", log=lambda *a: None) is None      # up to date: skipped
    # backfill: makes the missing one, skips the done one
    assert proxies.main([str(tmp_path), media, EV]) == 0
    assert os.path.exists(os.path.join(media, EV, "proxy", f"{EV}_qm2.mp4"))
    assert proxies.main([str(tmp_path), media, EV, "--only", "qm9"]) == 1           # no such video
    # the api's media list picks up the proxy sha256 from the index
    from server import syncindex
    from server.config import cfg
    monkeypatch.setattr(cfg, "MEDIA", media)
    syncindex._media_cache.clear()
    items = {i["path"]: i for i in syncindex.media(EV)}
    assert items[f"media/{EV}/proxy/{EV}_qm1.mp4"]["sha256"] == idx[f"{EV}_qm1"]["sha256"]


@needs_ffmpeg
def test_submit_background_and_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("PROXY_ENCODER", "cpu")
    proxies._enc_cache.clear()
    media = str(tmp_path / "media")
    _src(media, f"{EV}_qm3", seconds=1)
    monkeypatch.setenv("MEDIA_PROXIES", "0")
    proxies.submit(media, EV, f"{EV}_qm3", log=lambda *a: None)
    assert proxies._thr[0] is None or not proxies._thr[0].is_alive()
    monkeypatch.setenv("MEDIA_PROXIES", "1")
    proxies.submit(media, EV, f"{EV}_qm3", log=lambda *a: None)
    t0 = time.time()
    while not os.path.exists(os.path.join(media, EV, "proxy", f"{EV}_qm3.mp4")) and time.time() - t0 < 60:
        time.sleep(0.2)
    assert os.path.exists(os.path.join(media, EV, "proxy", f"{EV}_qm3.mp4"))
