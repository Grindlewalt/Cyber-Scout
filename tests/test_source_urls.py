"""Security audit findings 2 + 4: source URL normalisation, '--' before URLs, SSRF guard (live/sources.py)."""
import os
import socket
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "live"))

import sources as SRC  # noqa: E402
import layout as LY  # noqa: E402
import videos as VID  # noqa: E402
import liveingest as LI  # noqa: E402

PUBLIC = "142.250.69.206"


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch, tmp_path):
    """No real DNS in tests: a table of names; unknown names resolve to a public address."""
    table = {"lan.example.com": ["192.168.1.20"], "ts.example.com": ["100.101.102.103"],
             "dock.example.com": ["172.25.0.1"], "lo.example.com": ["127.0.0.1"],
             "v6lo.example.com": ["::1"], "mixed.example.com": [PUBLIC, "10.0.0.5"],
             "ula.example.com": ["fd7a:115c:a1e0::1"], "mapped.example.com": ["::ffff:10.1.2.3"],
             "meta.example.com": ["169.254.169.254"]}

    def res(host, port):
        if host in table: return table[host]
        try:                                   # numeric forms (127.1, 0x7f000001, ...): the real parser, no network
            return [i[4][0] for i in socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM, 0, socket.AI_NUMERICHOST)]
        except socket.gaierror:
            return [PUBLIC]
    monkeypatch.setattr(SRC, "_resolve", res)
    SRC._PUBLIC_OK.clear()
    monkeypatch.delenv("CS_URL_ALLOW", raising=False)
    monkeypatch.setenv("MEDIA", str(tmp_path / "media"))
    monkeypatch.setenv("DATA", str(tmp_path / "data"))
    (tmp_path / "media").mkdir(); (tmp_path / "data").mkdir()
    yield table
    SRC._PUBLIC_OK.clear()


# ---------------------------------------------------------------- classify_url (finding 2)
@pytest.mark.parametrize("bad", [
    "-o", "--config-location=/data/x", "-Iexec", " --batch-file /etc/passwd",
    "file:///etc/passwd", "FILE:/etc/passwd", "javascript:alert(1)", "JavaScript://%0aalert(1)",
    "data:text/html,<script>alert(1)</script>", "ftp://example.com/x.mp4", "rtmp://example.com/live",
    "concat:/a|/b", "example.com/video.mp4", "evil", "relative/path.mp4", "./x.mp4", "../../etc/passwd",
    "//evil.example.com/x.m3u8", "https:///nohost", "http://user:pw@example.com/x.mp4",
    "https://example.com/a b.mp4", "https://example.com/x\n.mp4",
    "https://www.youtube.com/@x\"onmouseover=1/live", "", "   "])
def test_classify_rejects(bad):
    with pytest.raises(ValueError):
        SRC.classify_url(bad)


@pytest.mark.parametrize("good,kind,provider,url", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "video", "youtube", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?t=5", "video", "youtube", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("dQw4w9WgXcQ", "video", "youtube", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("youtube.com/@FIRSTinMichigan/live", "livestream", "youtube", "https://www.youtube.com/@FIRSTinMichigan/live"),
    ("https://www.youtube.com/live/dQw4w9WgXcQ", "video", "youtube", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("https://www.youtube.com/playlist?list=PLabcdef123", "per_match", "youtube",
     "https://www.youtube.com/playlist?list=PLabcdef123"),
    ("https://www.twitch.tv/firstinspires", "livestream", "twitch", "https://www.twitch.tv/firstinspires"),
    ("https://m.twitch.tv/videos/123456789", "video", "twitch", "https://www.twitch.tv/videos/123456789"),
    ("https://cdn.example.com/live/index.m3u8", "livestream", "http", "https://cdn.example.com/live/index.m3u8"),
    ("HTTPS://CDN.Example.com/v/match.mp4?sig=Ab", "video", "http", "https://cdn.example.com/v/match.mp4?sig=Ab"),
    ("/media/2026wasam/qm1.mp4", "file", "file", "/media/2026wasam/qm1.mp4"),
])
def test_classify_accepts(good, kind, provider, url):
    c = SRC.classify_url(good)
    assert (c["kind"], c["provider"], c["url"]) == (kind, provider, url)


def test_lookalike_hosts_are_generic():
    assert SRC.classify_url("https://eviltwitch.tv/foo")["provider"] == "http"
    assert SRC.classify_url("https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ")["provider"] == "http"


def test_add_file_roots_and_private(tmp_path):
    doc = {"sources": []}
    s = SRC.add(doc, str(tmp_path / "media" / "x.mp4"))
    assert s["kind"] == "file" and s["url"] == str(tmp_path / "media" / "x.mp4")
    for bad in ("/etc/passwd", str(tmp_path / "media" / ".." / ".." / "etc" / "passwd"),
                "http://lan.example.com/x.m3u8", "http://127.0.0.1:8000/", "https://dock.example.com/a.mp4"):
        with pytest.raises(ValueError):
            SRC.add(doc, bad)
    s = SRC.add(doc, "https://cdn.example.com/live/index.m3u8")
    assert s["kind"] == "livestream" and s["sample"]["video"] == "https://cdn.example.com/live/index.m3u8"


def test_update_validates_sample(tmp_path):
    doc = {"sources": []}
    s = SRC.add(doc, "https://www.twitch.tv/firstinspires")
    for bad in ({"video": "-o"}, {"video": "file:///etc/passwd"}, {"video": "javascript:alert(1)"},
                {"video": "rel/x.mp4"}, {"video": "/etc/passwd"}, {"video": "http://ts.example.com/x.mp4"},
                {"video": 5}, {"match": "<b>"}, {"t": "60"}, {"t": -1}, "str"):
        with pytest.raises(ValueError):
            SRC.update(doc, s["id"], {"sample": bad})
    SRC.update(doc, s["id"], {"sample": {"video": "youtu.be/dQw4w9WgXcQ", "match": "2026wasam_qm17", "t": 60, "x": 1}})
    assert s["sample"] == {"video": "dQw4w9WgXcQ", "match": "2026wasam_qm17", "t": 60.0}


def test_media_for_revalidates_stored_values():
    with pytest.raises(ValueError):
        SRC.media_for({"kind": "video", "url": "-o", "sample": {}})
    with pytest.raises(ValueError):
        SRC.media_for({"kind": "per_match", "videos": {"2026wasam_qm1": "file:///etc/passwd"}}, "2026wasam_qm1")
    assert SRC.media_for({"kind": "video", "sample": {"video": "dQw4w9WgXcQ", "t": 3}})[0] == \
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


# ---------------------------------------------------------------- SSRF guard (finding 4)
@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://127.1/", "http://2130706433/", "http://0x7f000001/", "http://0.0.0.0:8000/",
    "http://10.0.0.58:7560/", "http://192.168.1.1/", "http://172.25.0.1:9443/", "http://172.17.0.1:8200/",
    "http://100.64.0.1/", "http://100.100.100.100/", "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/", "http://[fe80::1]/", "http://[fd7a:115c:a1e0::1]/", "http://[::ffff:127.0.0.1]/",
    "http://[2002:c0a8:0101::1]/", "http://[64:ff9b::a00:1]/",
    "http://lan.example.com/x.m3u8", "http://ts.example.com/", "http://dock.example.com/", "http://lo.example.com/",
    "http://v6lo.example.com/", "http://mixed.example.com/", "http://ula.example.com/", "http://mapped.example.com/",
    "http://meta.example.com/", "file:///etc/passwd", "gopher://example.com/", "-o", "http://u:p@example.com/"])
def test_safe_public_url_blocks(url):
    with pytest.raises(ValueError):
        SRC.safe_public_url(url)


def test_safe_public_url_allows_public_and_allowlist(monkeypatch):
    assert SRC.safe_public_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert SRC.safe_public_url("https://example.com/x.m3u8", resolve=lambda h, p: ["2607:f8b0:400a::200e"])
    monkeypatch.setenv("CS_URL_ALLOW", "lan.example.com,100.64.0.0/10")
    assert SRC.safe_public_url("http://lan.example.com/x.m3u8")
    assert SRC.safe_public_url("http://ts.example.com/x.m3u8")
    with pytest.raises(ValueError):
        SRC.safe_public_url("http://dock.example.com/")


def test_dns_failure_is_refused(monkeypatch):
    def boom(h, p): raise OSError("no such host")
    monkeypatch.setattr(SRC, "_resolve", boom)
    with pytest.raises(ValueError):
        SRC.safe_public_url("https://nx.example.com/")


def test_tba_webcasts_only_youtube_twitch():
    W = SRC.webcast_url
    assert W({"type": "youtube", "channel": "dQw4w9WgXcQ"}) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert W({"type": "twitch", "channel": "firstinspires"}) == "https://www.twitch.tv/firstinspires"
    assert W({"type": "youtube", "channel": "@FIRSTinMichigan"}) == "https://www.youtube.com/@FIRSTinMichigan/live"
    for w in ({"type": "html5", "file": "http://172.25.0.1:9443/"}, {"type": "direct_link", "channel": "http://x/"},
              {"type": "iframe", "file": "https://example.com/"}, {"type": "twitch", "channel": "a/../../x"},
              {"type": "youtube", "channel": "../redirect?x=http://10.0.0.1"}, {"type": "youtube", "channel": "-o"}):
        assert W(w) is None
    found = SRC.discover("2026wasam", {"webcasts": [{"type": "html5", "file": "http://10.0.0.1/"},
                                                    {"type": "twitch", "channel": "firstinspires"}]},
                         [{"key": "2026wasam_qm1", "videos": [{"type": "youtube", "key": "http://10.0.0.1/x"},
                                                              {"type": "youtube", "key": "-o"}]}],
                         meta_fn=lambda v: pytest.fail("non-id video key fetched"))
    assert [s["url"] for s in found] == ["https://www.twitch.tv/firstinspires"]


# ---------------------------------------------------------------- "--" before URLs, guard before fetch
class Cap:
    def __init__(self, out="https://rr1.googlevideo.com/videoplayback?x=1\n"):
        self.argv, self.out = [], out

    def __call__(self, argv, **kw):
        self.argv.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout=self.out if kw.get("text") else self.out.encode(), stderr="")


def _dashdash_before(argv, url):
    i = argv.index(url)
    return argv[i - 1] == "--" if argv[0] not in ("ffmpeg", "ffprobe") else argv[i - 1] == "-i"


def test_layout_resolve_and_download_guarded(monkeypatch):
    cap = Cap(); monkeypatch.setattr(LY.subprocess, "run", cap); LY._RESOLVED.clear()
    u = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert LY.resolve_media(u).startswith("https://rr1.googlevideo.com/")
    assert _dashdash_before(cap.argv[-1], u)
    for bad in ("http://dock.example.com/x", "http://172.25.0.1:9443/x.mp4", "gopher://example.com/"):
        with pytest.raises(ValueError):
            LY.resolve_media(bad)
    with pytest.raises(ValueError):
        LY.cached_download("http://lan.example.com/v")
    n = len(cap.argv)
    assert LY.resolve_media("-o") == os.path.abspath("-o")          # local: absolute path, never an option
    assert LY.resolve_media("concat:/a|/b").startswith("/")
    assert len(cap.argv) == n
    LY.duration("/media/x.mp4")
    assert _dashdash_before(cap.argv[-1], "/media/x.mp4")


def test_extractor_result_must_be_public(monkeypatch):
    monkeypatch.setattr(LY.subprocess, "run", Cap("http://10.0.0.58:7560/\n")); LY._RESOLVED.clear()
    with pytest.raises(ValueError):
        LY.resolve_media("https://www.youtube.com/watch?v=dQw4w9WgXcQ")


def test_videos_and_liveingest_argv(monkeypatch):
    cap = Cap("c\tt\t60\t1080\n"); monkeypatch.setattr(VID.subprocess, "run", cap)
    VID.meta("dQw4w9WgXcQ")
    assert _dashdash_before(cap.argv[-1], "https://youtu.be/dQw4w9WgXcQ")
    assert VID.meta("-o") is None and VID.meta("http://10.0.0.1/") is None and len(cap.argv) == 1
    with pytest.raises(ValueError):
        VID.download("--exec=x", "/tmp/x.mp4")
    assert VID.candidates({"videos": [{"type": "youtube", "key": "http://10.0.0.1/"}]}, {}, {}) == []
    cap2 = Cap("vid\tis_live\n"); monkeypatch.setattr(LI.subprocess, "run", cap2)
    LI.resolve("https://www.twitch.tv/firstinspires")
    assert _dashdash_before(cap2.argv[-1], "https://www.twitch.tv/firstinspires")
    for bad in ("http://ts.example.com/x.m3u8", "file:///etc/passwd", "-o"):
        with pytest.raises(RuntimeError):
            LI.resolve(bad)
    assert len(cap2.argv) == 1


def test_recorder_guarded(monkeypatch):
    pytest.importorskip("cv2")
    import livestream as LS
    with pytest.raises(ValueError):
        LS._http("http://172.25.0.1:8200/")
    cap = Cap("abc\tis_live\t301\t1080\nhttps://manifest.googlevideo.com/x.m3u8\n")
    monkeypatch.setattr(LS.subprocess, "run", cap)
    r = LS.Recorder.__new__(LS.Recorder)
    r.url = "https://www.youtube.com/@x/live"
    assert r.resolve()[0] == "abc" and _dashdash_before(cap.argv[-1], r.url)
    r.url = "http://lan.example.com/live.m3u8"
    with pytest.raises(RuntimeError):
        r.resolve()
    assert len(cap.argv) == 1
