"""live/runlog.py: the ring buffer, the file, the stdout tee and above all the redaction (nothing secret in the file)."""
import json, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "live"))
import pytest
import runlog as R

TBA = "Zk3" + "a9Qx7LmN2pVb8TcR5yUe1WdFhJ4oGiK6sAtBnMq0XrCvEzLj"[:61]          # 64 chars, like a real TBA key
DISCORD = "https://discord.com/api/webhooks/123456789012345678/AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-AbCdEfGhIjKlMn"


@pytest.fixture(autouse=True)
def fresh():
    R._SECRETS.clear()
    R.S.buf.clear(); R.S.seq = 0; R.S.doing = {R.MAIN: ("", 0.0)}
    yield
    R._SECRETS.clear()


@pytest.mark.parametrize("raw,gone", [
    ("GET https://x.org/api?key=abc123&y=2", "abc123"),
    ("fetch ?token=sekrit99 done", "sekrit99"),
    ("url /v1/a?sig=deadbeef01&exp=3", "deadbeef01"),
    ("auth=hunter2 ok", "hunter2"),
    ("password=correct-horse x", "correct-horse"),
    ("PASSWORD: s3cr3t!", "s3cr3t!"),
    ("api_key=QWERTY1 api-key: ZXCV2", "QWERTY1"),
    ("X-TBA-Auth-Key: TOPSECRETVALUE", "TOPSECRETVALUE"),
    ("Authorization: Bearer abcDEF123.ghi-jkl_mno", "abcDEF123"),
    ("bearer abcdefghij0123", "abcdefghij0123"),
    ("posting " + DISCORD + " now", "AbCdEfGhIjKlMnOpQrStUvWxYz"),
    ("https://user:pw0rd@host.example/x", "pw0rd"),
    ("blob 3f9a1c7e5b2d4a68901c3e5f7a9b1d3f5a7c9e1b rest", "3f9a1c7e5b2d4a68901c3e5f7a9b1d3f5a7c9e1b"),
    ("b64 QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU2Nzg5 x", "QUJDREVGR0hJSktMTU5PUFFSU1RVVldY"),
    ("tok Ab3dEf6hIj9lMn2pQr5tUv8xYz1BcD4fGh7J", "Ab3dEf6hIj9lMn2pQr5tUv8xYz1BcD4fGh7J"),
])
def test_redacted(raw, gone):
    out = R.redact(raw)
    assert gone not in out, out
    assert "redact" in out


def test_tba_key_exact_anywhere():
    R.add_secret("shortbutok1")
    assert "shortbutok1" not in R.redact("hit /event/x with shortbutok1!")
    assert R.redact("12345") == "12345"                       # (too short secrets are not registered)
    R.add_secret("abc")
    assert R.redact("abc") == "abc"


@pytest.mark.parametrize("keep", [
    "tracking 2026wasam_qm12 (livestream clip)",
    "2026wasam_qm12_red1 match_key=2026wasam_qm3 team=frc4911",
    "youtube dQw4w9WgXcQ 11 chars; twitch firstwa_red1 video 2345678901",
    "/mnt/nas/autoscout-media/2026wasam/2026wasam_qm12_cam1_clip_final_version.mp4 saved",
    "/opt/stacks/cyber-scout/data/events/2026wasam/matches/2026wasam_qm12.json",
    "[epa] 2026wasam: 62 teams, mean 31.4 in 0.8 s",
    "dashboard.json 132 KB (gz 21 KB)",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30",
    "keyboard keys pressed; monkey=1 turkey 5",
    "waiting: match processing paused (Admin → Settings)",
    "a" * 40,                                                  # a long word: no digit, not a token
    "1234567890" * 4,                                          # a long number
    "robot_tracking_2026wasam_qm12_blue_cam1",                 # words joined by _
])
def test_kept(keep):
    assert R.redact(keep) == keep


def test_ring_and_limits():
    for i in range(R.LINES + 50): R.add(f"line {i}")
    assert len(R.S.buf) == R.LINES and R.S.buf[-1]["seq"] == R.LINES + 50
    R.add("x" * 1000)
    assert len(R.S.buf[-1]["text"]) <= R.LINE_MAX
    R.add("a\n\nb\rprogress 50%\rprogress 100%\n")
    assert [r["text"] for r in list(R.S.buf)[-2:]] == ["a", "progress 100%"]


def test_file_tee_and_doing(tmp_path, monkeypatch, capsys):
    o, e = sys.stdout, sys.stderr
    try:
        R.start(str(tmp_path), "worker", secrets=[TBA])
        R.S.path = str(tmp_path / "logs" / "worker.json")
        print("12:00:00 key is " + TBA + " and ?key=zzz999")
        print("partial", end=""); print(" line")
        sys.stderr.write("Traceback boom\n")
        R.doing("tracking Q3 (livestream clip)")
        R.doing("Video-page job frame", key="job")
        R.flush()
    finally:
        sys.stdout, sys.stderr = o, e
    d = json.load(open(tmp_path / "logs" / "worker.json"))
    assert d["role"] == "worker" and d["pid"] == os.getpid() and d["doing"] == "tracking Q3 (livestream clip)"
    assert d["also"][0]["text"] == "Video-page job frame" and d["doing_since"]
    txt = [l["text"] for l in d["lines"]]
    assert "12:00:00 key is [redacted] and ?key=[redacted]" in txt and "partial line" in txt and "Traceback boom" in txt
    assert [l["seq"] for l in d["lines"]] == sorted(l["seq"] for l in d["lines"]) and d["seq"] == d["lines"][-1]["seq"]
    assert TBA not in json.dumps(d) and "zzz999" not in json.dumps(d)
    R.doing("", key="job"); assert not R.snapshot()["also"]


def test_writer_throttle(tmp_path):
    R.S.path = str(tmp_path / "w.json"); R.S.wrote = 0
    R.flush(); t = os.path.getmtime(R.S.path)
    time.sleep(0.01); R.add("x")
    assert os.path.getmtime(R.S.path) == t                    # add() itself never writes


def test_gpu_stats(monkeypatch):
    class P:                                                  # a finished nvidia-smi
        returncode, stdout = 0, "0, NVIDIA RTX 3090, 37, 9000, 24576\nbad line\n1, RTX 3080, 5, 100, 10240\n"
    calls = []
    monkeypatch.setattr(R.shutil, "which", lambda n: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(R.subprocess, "run", lambda *a, **k: calls.append(k) or P)
    monkeypatch.setenv("GPU_IDS", "0,1"); monkeypatch.setenv("GPU_DEVICE", "1")
    R._GPU.update(at=0.0, val=None)
    s = R.snapshot()
    assert s["gpus"] == [dict(index=0, name="NVIDIA RTX 3090", util_pct=37, mem_used_mb=9000, mem_total_mb=24576),
                         dict(index=1, name="RTX 3080", util_pct=5, mem_used_mb=100, mem_total_mb=10240)]
    assert s["gpu_ids"] == "0,1" and s["gpu_device"] == "1" and calls[0]["timeout"] == R.GPU_TIMEOUT_S
    R.snapshot(); assert len(calls) == 1                      # cached for GPU_S


def test_gpu_stats_absent_or_failing(monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda n: None)
    R._GPU.update(at=0.0, val=None); assert R.snapshot()["gpus"] is None
    def boom(*a, **k): raise R.subprocess.TimeoutExpired("nvidia-smi", 3)
    monkeypatch.setattr(R.shutil, "which", lambda n: "/x"); monkeypatch.setattr(R.subprocess, "run", boom)
    R._GPU.update(at=0.0, val=None); assert R.snapshot()["gpus"] is None
