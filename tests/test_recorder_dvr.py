"""Recorder backfill: chunks older than the playlist are fetched by sequence number only when the chunk URLs
carry it (YouTube /sq/N/).  Twitch URLs do not: fetching "older" chunks there downloaded the playlist's first
chunk again for every one of them (2026wagg: 23 min of one repeated 2 s chunk)."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "live"))

import livestream as LS  # noqa: E402


def playlist(first_sq, n, url, dur=2.0):
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", f"#EXT-X-TARGETDURATION:{int(dur)}", f"#EXT-X-MEDIA-SEQUENCE:{first_sq}",
             "#EXT-X-PROGRAM-DATE-TIME:2026-10-17T17:50:00.000Z"]
    for k in range(n):
        lines += [f"#EXTINF:{dur:.3f},live", url(first_sq + k)]
    return "\n".join(lines) + "\n"


def run_once(tmp_path, monkeypatch, text, backfill_min):
    got = []

    def http(u, timeout=20):
        if u == "https://hls.example/index.m3u8":
            return text.encode()
        got.append(u)
        return b"\x47" + u.encode().ljust(187, b" ")

    monkeypatch.setattr(LS, "_http", http)
    monkeypatch.setattr(LS, "_ipv4_only", lambda: None)
    logs = []
    r = LS.Recorder("https://www.twitch.tv/x", str(tmp_path), log=logs.append, backfill_min=backfill_min)
    monkeypatch.setattr(r, "resolve", lambda: ("v1", "is_live", "src", "https://hls.example/index.m3u8"))
    monkeypatch.setattr(r, "retention", lambda: r.stop.set())
    r.run()
    rows = [json.loads(x) for x in open(tmp_path / "chunks.jsonl")]
    return got, rows, logs


def test_can_synth_only_with_sq_in_url():
    assert LS.can_synth({"url": "https://rr1.googlevideo.com/videoplayback/id/abc/sq/1200/goap/x"})
    assert not LS.can_synth({"url": "https://video-edge-1.example.abs.hls.ttvnw.net/v1/segment/CqAFabc.ts"})
    assert not LS.can_synth({})


def test_twitch_backfill_starts_at_the_playlist_head(tmp_path, monkeypatch):
    text = playlist(500, 6, lambda q: f"https://video-edge.example.ttvnw.net/v1/segment/seg{q}.ts")
    got, rows, logs = run_once(tmp_path, monkeypatch, text, backfill_min=60)
    assert [r["sq"] for r in rows] == list(range(500, 506))
    assert len(set(got)) == len(got) == 6                        # every chunk fetched once, no repeats
    assert any("not available from this stream" in x for x in logs)


def test_youtube_backfill_fetches_older_chunks_by_sq(tmp_path, monkeypatch):
    text = playlist(1000, 4, lambda q: f"https://rr1.googlevideo.com/videoplayback/id/abc/sq/{q}/goap/x", dur=5.0)
    got, rows, logs = run_once(tmp_path, monkeypatch, text, backfill_min=1)   # 60 s / 5 s = 12 chunks back
    assert [r["sq"] for r in rows] == list(range(991, 1004))
    assert len(set(got)) == len(got) == 13
    assert rows[0]["wall"] < rows[-1]["wall"]


def test_repeated_chunks_are_not_video(tmp_path):
    """Recordings made before the fix: rows sharing a pts (one chunk under many sq) are skipped by the readers."""
    import liveingest as LI
    rows = [dict(sq=q, file="seg/a.ts", off=0, size=10, pts=4882.0, dur=2.0, wall=1000.0 + 2 * (q - 100)) for q in range(100, 110)]
    rows += [dict(sq=q, file="seg/b.ts", off=0, size=10, pts=5000.0 + 2 * (q - 110), dur=2.0, wall=1020.0 + 2 * (q - 110))
             for q in range(110, 115)]
    (tmp_path / "chunks.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert [r["sq"] for r in LI._rows(str(tmp_path))] == list(range(110, 115))
    cs = LS.ChunkStore(str(tmp_path)); cs.refresh()
    assert [r["sq"] for r in cs.rows if cs.real(r)] == list(range(110, 115))
    assert cs.wall(5004.0) == 1024.0
