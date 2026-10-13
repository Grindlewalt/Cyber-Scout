"""POST/PATCH /events/{ev}/sources: pasted URLs are normalised and validated (audit findings 2 + 4)."""
import pytest

from server.routers import sources as R


@pytest.fixture
def no_dns(monkeypatch):
    if R.SRC is None:
        pytest.skip("live/sources.py not importable")
    table = {"lan.example.com": ["10.0.0.58"], "cdn.example.com": ["93.184.216.34"]}
    monkeypatch.setattr(R.SRC, "_resolve", lambda h, p: table.get(h, ["93.184.216.34"]))
    R.SRC._PUBLIC_OK.clear()


@pytest.mark.parametrize("url", ["-o", "--exec=id", "file:///etc/passwd", "javascript:alert(1)", "relative/x.mp4",
                                 "example.com/x.mp4", "/etc/passwd", "http://127.0.0.1:2375/", "http://lan.example.com/x.m3u8",
                                 "http://[::1]/x.mp4", "http://100.100.100.100/"])
def test_add_source_rejects(admin, no_dns, url):
    r = admin.post("/events/2026wasam/sources", {"url": url, "role": "wide"})
    assert r.status_code == 422, (url, r.text)


def test_add_and_patch_source(admin, no_dns):
    r = admin.post("/events/2026wasam/sources", {"url": "twitch.tv/firstinspires", "role": "dynamic"})
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["url"] == "https://www.twitch.tv/firstinspires" and s["kind"] == "livestream"
    r = admin.post("/events/2026wasam/sources", {"url": "https://CDN.example.com/a/index.m3u8", "role": "wide"})
    assert r.status_code == 201 and r.json()["url"] == "https://cdn.example.com/a/index.m3u8"
    for bad in ({"video": "-o"}, {"video": "javascript:alert(1)"}, {"video": "http://lan.example.com/x"}):
        assert admin.patch(f"/events/2026wasam/sources/{s['id']}", {"sample": bad}).status_code == 422
    r = admin.patch(f"/events/2026wasam/sources/{s['id']}", {"sample": {"video": "https://youtu.be/dQw4w9WgXcQ", "t": 30}})
    assert r.status_code == 200 and r.json()["sample"]["video"] == "dQw4w9WgXcQ"
