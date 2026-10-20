"""Video page links (UX audit SETUP-6 / 12 / 32): only YouTube / Twitch links, direct video addresses and server files
are accepted, with sentences an admin can act on; a dead direct link is refused before it is saved; adding a link
never changes the match video mode; TBA's webcasts are offered (named by their channel)."""
import pytest

from server.routers import sources as R

EV = "2026wasam"


@pytest.fixture(autouse=True)
def mods(monkeypatch):
    if R.SRC is None:
        pytest.skip("live/sources.py not importable")
    monkeypatch.setattr(R.SRC, "_resolve", lambda h, p: ["93.184.216.34"])
    R.SRC._PUBLIC_OK.clear()
    monkeypatch.setattr(R.SRC, "probe_http", lambda url, **k: None)


@pytest.mark.parametrize("url, says", [
    ("https://vimeo.com/123456", "vimeo.com isn't YouTube or Twitch"),
    ("https://example.com/page", "example.com isn't YouTube or Twitch"),
    ("not a url", "That isn't a link"),
    ("youtube.com", "That YouTube link doesn't point at a stream"),
    ("twitch.tv/", "That Twitch link doesn't name a channel"),
])
def test_other_links_refused_with_a_sentence(admin, url, says):
    r = admin.post(f"/events/{EV}/sources", {"url": url, "role": "wide"})
    assert r.status_code == 422 and says in r.json()["detail"], r.text
    assert admin.get(f"/events/{EV}/sources").json()["sources"] == []


def test_dead_direct_link_refused_and_not_saved(admin, monkeypatch):
    seen = []
    monkeypatch.setattr(R.SRC, "probe_http", lambda url, **k: seen.append(url) or "That address answers “404 Not Found”.")
    r = admin.post(f"/events/{EV}/sources", {"url": "https://cdn.example.com/v/match.mp4", "role": "wide"})
    assert r.status_code == 422 and "404" in r.json()["detail"]
    assert seen == ["https://cdn.example.com/v/match.mp4"]
    assert admin.get(f"/events/{EV}/sources").json()["sources"] == []
    # YouTube / Twitch links are never probed
    assert admin.post(f"/events/{EV}/sources", {"url": "https://www.twitch.tv/firstwa_red1", "role": "wide"}).status_code == 201
    assert seen == ["https://cdn.example.com/v/match.mp4"]


def test_replacing_with_a_dead_link_is_refused(admin, monkeypatch):
    s = admin.post(f"/events/{EV}/sources", {"url": "https://cdn.example.com/a.m3u8", "role": "wide"}).json()
    monkeypatch.setattr(R.SRC, "probe_http", lambda url, **k: "a web page, not a video")
    r = admin.patch(f"/events/{EV}/sources/{s['id']}", {"url": "https://cdn.example.com/b.mp4"})
    assert r.status_code == 422
    assert admin.get(f"/events/{EV}/sources").json()["sources"][0]["url"] == "https://cdn.example.com/a.m3u8"


def test_adding_a_link_never_sets_the_match_video_mode(admin):
    assert admin.post(f"/events/{EV}/sources", {"url": "https://www.twitch.tv/firstwa_red1", "role": "wide", "primary": True}).status_code == 201
    v = admin.get(f"/events/{EV}/video-mode").json()
    assert v["video_mode"] == "auto" and v["chosen"] is False


def test_tba_webcasts(admin, monkeypatch):
    r = admin.get(f"/events/{EV}/sources/webcasts")
    assert r.status_code == 200 and r.json()["webcasts"] == [] and r.json()["note"]          # no TBA key: quiet
    from server.tests.conftest import GOOD_KEY
    assert admin.put("/settings/tba-key", {"key": GOOD_KEY}).status_code == 200
    ev = {"webcasts": [{"type": "twitch", "channel": "firstwa_red2"}, {"type": "twitch", "channel": "firstwa_red1"},
                       {"type": "twitch", "channel": "firstwa_red1"}, {"type": "html5", "file": "http://10.0.0.1/x"},
                       {"type": "youtube", "channel": "@FIRSTWashington", "date": "2026-10-18"}]}
    monkeypatch.setattr(R, "tba_call", lambda key, path, ttl=600: ev)
    import os
    from server.config import cfg
    d = os.path.join(cfg.DATA, "events", EV); os.makedirs(d, exist_ok=True)
    R.SRC.save(d, {"sources": [{"id": "url-1", "origin": "admin", "kind": "livestream", "url": "https://www.twitch.tv/firstwa_red1"}]})
    W = admin.get(f"/events/{EV}/sources/webcasts").json()["webcasts"]
    assert [w["label"] for w in W] == ["Event webcast · Twitch firstwa_red2", "Event webcast · Twitch firstwa_red1",
                                       "Event webcast · YouTube @FIRSTWashington live, 2026-10-18"]
    assert [w["source"] for w in W] == [None, "url-1", None]
    assert admin.get(f"/events/{EV}/sources/webcasts").status_code == 200


def test_webcasts_admin_only(team):
    assert team["strategist"].get(f"/events/{EV}/sources/webcasts").status_code == 403


def test_old_webcast_names_get_their_channel(admin):
    import os
    from server.config import cfg
    d = os.path.join(cfg.DATA, "events", EV); os.makedirs(d, exist_ok=True)
    R.SRC.save(d, {"sources": [{"id": "tba-webcast-0", "origin": "tba", "kind": "livestream", "url": "https://www.twitch.tv/firstwa_red2",
                                "label": "Event webcast (twitch)", "webcast": {"type": "twitch", "channel": "firstwa_red2"}},
                               {"id": "url-2", "origin": "admin", "kind": "livestream", "url": "https://www.twitch.tv/x",
                                "label": "Event webcast (twitch)"}]})
    S = admin.get(f"/events/{EV}/sources").json()["sources"]
    assert [s["label"] for s in S] == ["Event webcast · Twitch firstwa_red2", "Event webcast (twitch)"]   # an admin's own name stays
