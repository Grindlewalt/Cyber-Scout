"""Client sync (docs/SYNC.md part 1): /info, /sync/events, manifest deltas, bundles (validation, content, Range),
media list, Idempotency-Key, per-role access."""
import gzip, hashlib, io, json, os, tarfile, time

import pytest

from server import syncindex
from server.config import cfg

EV = "2026wasam"


def _w(root, rel, data):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode())
    return p


@pytest.fixture
def site(data):
    s = os.path.join(str(data), "site")
    _w(s, "api/events.json", '[{"key":"2026wasam"}]')
    _w(s, f"api/{EV}/event.json", '{"name":"Sammamish","n_done":3,"n_total":80}')
    _w(s, f"api/{EV}/m/{EV}_qm1.json", '{"k":1}' * 50)
    _w(s, f"api/{EV}/t/frc4911.json", '{"team":4911}')
    _w(s, "api/2026other/event.json", '{"secret":1}')
    _w(s, f"api/{EV}/m/{EV}_qm2.json.tmp", "partial")          # worker temp file: never listed
    syncindex.SCAN_TTL = 0                                      # tests change files between calls
    yield s
    syncindex.SCAN_TTL = 2.0


def _follow(admin, data):
    import sqlite3
    c = sqlite3.connect(os.path.join(str(data), "app.db"))
    c.execute("INSERT INTO events(key,name,year,start_date,end_date,added_at) VALUES(?,?,?,?,?,?)",
              (EV, "PNW Sammamish", 2026, "2026-03-06", "2026-03-08", time.time()))
    c.commit()
    c.close()


def _bundle(c, paths, ev=EV, headers=None):
    h = {"X-CSRF-Token": c.tc.cookies.get("cs_csrf")}
    h.update(headers or {})
    return c.tc.post("/app/v1/sync/bundle", json={"ev": ev, "paths": paths}, headers=h)


def _untar(b):
    with tarfile.open(fileobj=io.BytesIO(b), mode="r:gz") as tf:
        return {m.name: tf.extractfile(m).read() for m in tf.getmembers()}


# ------------------------------------------------------------------ info
def test_info_public_and_cacheable(client, monkeypatch):
    r = client.get("/info")
    assert r.status_code == 200
    j = r.json()
    assert j["app"] == "cyber-scout" and j["api"] == 1 and isinstance(j["time"], int) and j["version"]
    assert abs(j["time"] - time.time()) < 5 and j["name"].startswith("Cyber-Scout")
    assert r.headers["cache-control"] == "public, max-age=10"
    monkeypatch.setattr(cfg, "VERSION", "abc1234")
    monkeypatch.setattr(cfg, "SERVER_NAME", "Shop server")
    j = client.get("/info").json()
    assert j["version"] == "abc1234" and j["name"] == "Shop server"


# ------------------------------------------------------------------ auth per role
def test_sync_auth_per_role(client, team, site, data):
    _follow(team["admin"], data)
    anon = client.__class__(client.tc.__class__(client.tc.app, base_url="http://testserver", client=("10.0.0.9", 1)))
    for p in (f"/sync/manifest?ev={EV}", f"/sync/media?ev={EV}", "/sync/events"):
        assert anon.get(p).status_code == 401
    anon.get("/session")
    assert _bundle(anon, [f"api/{EV}/event.json"]).status_code == 401
    for role in ("viewer", "scout", "strategist", "admin"):
        c = team[role]
        assert c.get(f"/sync/manifest?ev={EV}").status_code == 200, role
        assert c.get(f"/sync/media?ev={EV}").status_code == 200, role
        ev = c.get("/sync/events").json()
        assert ev[0]["key"] == EV and ev[0]["progress"] == {"tracked": 3, "total": 80}
        assert ev[0]["status"] in ("needs_setup", "finished") and ev[0]["start_date"] == "2026-03-06"
        assert _bundle(c, [f"api/{EV}/event.json"]).status_code == 200, role
    # bundle without the CSRF header: refused (it is a POST)
    r = team["viewer"].tc.post("/app/v1/sync/bundle", json={"ev": EV, "paths": [f"api/{EV}/event.json"]})
    assert r.status_code == 403


def test_bundle_allowed_in_lockdown_and_not_audited(team, site, data):
    _follow(team["admin"], data)
    assert team["admin"].patch("/admin/security", {"lockdown": True}).status_code == 200
    assert _bundle(team["viewer"], [f"api/{EV}/event.json"]).status_code == 200
    log = team["admin"].get("/audit?limit=200").json()
    rows = log["entries"] if isinstance(log, dict) else log
    assert not any("sync/bundle" in (e.get("target") or "") for e in rows)


# ------------------------------------------------------------------ manifest
def test_manifest_full_since_deleted(team, site, data):
    _follow(team["admin"], data)
    v = team["viewer"]
    m = v.get(f"/sync/manifest?ev={EV}").json()
    assert m["ev"] == EV and m["full"] is True and m["deleted"] == []
    paths = {f["path"]: f for f in m["files"]}
    assert set(paths) == {"api/events.json", f"api/{EV}/event.json", f"api/{EV}/m/{EV}_qm1.json",
                          f"api/{EV}/t/frc4911.json"}                        # no other event, no temp files
    raw = open(os.path.join(site, f"api/{EV}/t/frc4911.json"), "rb").read()
    f = paths[f"api/{EV}/t/frc4911.json"]
    assert f["sha256"] == hashlib.sha256(raw).hexdigest() and f["size"] == len(raw) and f["mtime"] > 0
    cur = m["cursor"]

    # nothing changed -> empty delta; rewriting a file with the same bytes is not a change
    p = _w(site, f"api/{EV}/event.json", open(os.path.join(site, f"api/{EV}/event.json")).read())
    os.utime(p, (time.time() + 5, time.time() + 5))
    d = v.get(f"/sync/manifest?ev={EV}&since={cur}").json()
    assert d["full"] is False and d["files"] == [] and d["deleted"] == [] and d["cursor"] == cur

    # change + add + delete
    _w(site, f"api/{EV}/t/frc4911.json", '{"team":4911,"x":2}')
    _w(site, f"api/{EV}/t/frc1.json", '{"team":1}')
    os.remove(os.path.join(site, f"api/{EV}/m/{EV}_qm1.json"))
    d = v.get(f"/sync/manifest?ev={EV}&since={cur}").json()
    assert d["full"] is False and d["cursor"] != cur
    assert sorted(f["path"] for f in d["files"]) == [f"api/{EV}/t/frc1.json", f"api/{EV}/t/frc4911.json"]
    assert d["deleted"] == [f"api/{EV}/m/{EV}_qm1.json"]
    cur2 = d["cursor"]
    assert v.get(f"/sync/manifest?ev={EV}&since={cur2}").json()["files"] == []

    # global files show up in the delta too
    _w(site, "api/events.json", '[{"key":"2026wasam"},{"key":"2026x"}]')
    d = v.get(f"/sync/manifest?ev={EV}&since={cur2}").json()
    assert [f["path"] for f in d["files"]] == ["api/events.json"]

    # unknown / garbage / future cursors -> full
    for bad in ("nope", "zzzz.1", cur.split(".")[0] + ".999999", ""):
        assert v.get(f"/sync/manifest?ev={EV}&since={bad}").json()["full"] is True

    # survives an API restart (state on disk): same cursor still works
    syncindex._reg.clear()
    d = v.get(f"/sync/manifest?ev={EV}&since={cur2}").json()
    assert d["full"] is False


def test_manifest_bad_and_unknown_event(team, site, data):
    v = team["viewer"]
    assert v.get("/sync/manifest?ev=../etc").status_code == 422
    assert v.get("/sync/manifest?ev=2026nothere").status_code == 404
    assert v.get(f"/sync/manifest?ev={EV}").status_code == 200          # not followed, but has data


def test_manifest_symlink_not_listed(team, site, data, tmp_path):
    secret = tmp_path / "outside.txt"
    secret.write_text("secret")
    os.symlink(str(secret), os.path.join(site, f"api/{EV}/leak.json"))
    os.symlink(os.path.join(site, "api/2026other"), os.path.join(site, f"api/{EV}/other"))
    m = team["viewer"].get(f"/sync/manifest?ev={EV}").json()
    assert not any("leak" in f["path"] or "/other/" in f["path"] for f in m["files"])
    r = _bundle(team["viewer"], [f"api/{EV}/leak.json"])
    assert r.status_code in (403, 404)
    r = _bundle(team["viewer"], [f"api/{EV}/other/event.json"])
    assert r.status_code in (403, 404)


def test_manifest_perf_many_files(team, site, data):
    for i in range(3000):
        _w(site, f"api/{EV}/m/f{i}.json", '{"i":%d}' % i)
    v = team["viewer"]
    t0 = time.perf_counter()
    m = v.get(f"/sync/manifest?ev={EV}").json()
    first = time.perf_counter() - t0
    assert len(m["files"]) >= 3000
    t0 = time.perf_counter()
    d = v.get(f"/sync/manifest?ev={EV}&since={m['cursor']}").json()
    again = time.perf_counter() - t0
    assert d["files"] == [] and again < 1.0, (first, again)


# ------------------------------------------------------------------ bundle
def test_bundle_content_and_deterministic(team, site, data):
    v = team["viewer"]
    m = v.get(f"/sync/manifest?ev={EV}").json()
    paths = [f["path"] for f in m["files"]]
    r = _bundle(v, paths + paths[:1])                                     # duplicates are fine
    assert r.status_code == 200 and r.headers["content-type"] == "application/gzip"
    assert r.headers["accept-ranges"] == "bytes" and r.headers["etag"]
    got = _untar(r.content)
    assert set(got) == set(paths)
    for f in m["files"]:
        assert hashlib.sha256(got[f["path"]]).hexdigest() == f["sha256"]
    assert int(r.headers["x-bundle-files"]) == len(paths)
    # cache evicted -> rebuilt byte for byte (a resumed download stays consistent)
    for n in os.listdir(syncindex.bundle_dir()):
        os.remove(os.path.join(syncindex.bundle_dir(), n))
    r2 = _bundle(v, list(reversed(paths)))
    assert r2.content == r.content and r2.headers["etag"] == r.headers["etag"]


def test_bundle_range_resume(team, site, data):
    _w(site, f"api/{EV}/big.json", json.dumps([os.urandom(64).hex() for _ in range(3000)]))
    v = team["viewer"]
    paths = [f["path"] for f in v.get(f"/sync/manifest?ev={EV}").json()["files"]]
    full = _bundle(v, paths)
    assert full.status_code == 200 and len(full.content) > 10000
    et = full.headers["etag"]
    part = _bundle(v, paths, headers={"Range": "bytes=5000-", "If-Range": et})
    assert part.status_code == 206
    assert part.headers["content-range"] == f"bytes 5000-{len(full.content) - 1}/{len(full.content)}"
    assert full.content[:5000] + part.content == full.content
    # stale validator -> whole bundle again (200)
    r = _bundle(v, paths, headers={"Range": "bytes=5000-", "If-Range": '"other"'})
    assert r.status_code == 200 and r.content == full.content


def test_bundle_path_validation(team, site, data):
    v = team["viewer"]
    v.get(f"/sync/manifest?ev={EV}")
    bad = {
        "../app.db": 422, f"api/{EV}/../../app.db": 422, f"api/{EV}/./event.json": 422,
        "/etc/passwd": 422, f"api//{EV}/event.json": 422, "app.db": 422, f"api\\{EV}\\event.json": 422,
        "api/2026other/event.json": 403,                                   # another event's file
        f"api/{EV}/nothere.json": 404,                                     # not in the manifest
        f"api/{EV}/m/{EV}_qm2.json.tmp": 404,                              # excluded temp file
        "media/2026wasam/2026wasam_qm1.mp4": 422,
    }
    for p, want in bad.items():
        r = _bundle(v, [f"api/{EV}/event.json", p])
        assert r.status_code == want, (p, r.status_code, r.text)
    assert _bundle(v, [f"api/{EV}/event.json"], ev="../x").status_code == 422
    assert v.tc.post("/app/v1/sync/bundle", json={"ev": EV, "paths": []},
                     headers={"X-CSRF-Token": v.tc.cookies.get("cs_csrf")}).status_code == 422


def test_bundle_size_cap(team, site, data, monkeypatch):
    monkeypatch.setattr(cfg, "SYNC_BUNDLE_MAX", 100)
    _w(site, f"api/{EV}/big.json", "x" * 200)
    r = _bundle(team["viewer"], [f"api/{EV}/big.json"])
    assert r.status_code == 413


def test_bundle_cache_cleanup(team, site, data, monkeypatch):
    v = team["viewer"]
    paths = [f["path"] for f in v.get(f"/sync/manifest?ev={EV}").json()["files"]]
    for p in paths:
        assert _bundle(v, [p]).status_code == 200
    d = syncindex.bundle_dir()
    assert len(os.listdir(d)) == len(paths)
    old = os.path.join(d, os.listdir(d)[0])
    os.utime(old, (time.time() - 7 * 3600,) * 2)
    _w(site, f"api/{EV}/new.json", "{}")
    v.get(f"/sync/manifest?ev={EV}")
    assert _bundle(v, [f"api/{EV}/new.json"]).status_code == 200
    assert not os.path.exists(old)
    monkeypatch.setattr(cfg, "SYNC_CACHE_MB", 0)                           # over budget: all but the newest go
    _w(site, f"api/{EV}/new2.json", "{}")
    v.get(f"/sync/manifest?ev={EV}")
    assert _bundle(v, [f"api/{EV}/new2.json"]).status_code == 200
    assert len(os.listdir(d)) == 1


# ------------------------------------------------------------------ media
def test_media_list(team, site, data, monkeypatch):
    med = os.path.join(str(data), "nas")
    monkeypatch.setattr(cfg, "MEDIA", med)
    syncindex._media_cache.clear()
    _w(med, f"{EV}/{EV}_qm1.mp4", b"w" * 100)
    _w(med, f"{EV}/insets/{EV}_qm1.mp4", b"i" * 50)
    pp = _w(med, f"{EV}/proxy/{EV}_qm1.mp4", b"p" * 30)
    st = os.stat(pp)
    _w(med, f"{EV}/proxy/index.json", json.dumps({f"{EV}_qm1": {"size": 30, "mtime_ns": st.st_mtime_ns,
                                                                 "sha256": hashlib.sha256(b"p" * 30).hexdigest()}}))
    _w(med, f"live/{EV}/vid1/clips/Q1_a.mp4", b"c" * 10)
    _w(med, f"live/{EV}/vid1/matches/Q1_a.json", json.dumps({"id": "Q1_a", "clip": "/media/live/x/Q1_a.mp4",
                                                              "comp_level": "qm", "number": 1}))
    r = team["viewer"].get(f"/sync/media?ev={EV}")
    assert r.status_code == 200
    got = {i["path"]: i for i in r.json()}
    assert got[f"media/{EV}/{EV}_qm1.mp4"] == {"path": f"media/{EV}/{EV}_qm1.mp4", "size": 100, "kind": "wide",
                                                "match": f"{EV}_qm1"}
    assert got[f"media/{EV}/insets/{EV}_qm1.mp4"]["kind"] == "inset"
    p = got[f"media/{EV}/proxy/{EV}_qm1.mp4"]
    assert p["kind"] == "proxy" and p["sha256"] == hashlib.sha256(b"p" * 30).hexdigest()
    c = got[f"media/live/{EV}/vid1/clips/Q1_a.mp4"]
    assert c["kind"] == "clip" and c["match"] == f"{EV}_qm1"


# ------------------------------------------------------------------ Idempotency-Key
def _idem(c, method, path, body, key):
    h = {"X-CSRF-Token": c.tc.cookies.get("cs_csrf"), "Idempotency-Key": key}
    return c.tc.request(method, "/app/v1" + path, json=body, headers=h)


def test_idempotency_replay(team, data):
    _follow(team["admin"], data)
    s = team["scout"]
    r1 = _idem(s, "POST", f"/events/{EV}/notes", {"team": "frc4911", "text": "fast cycler"}, "k-1")
    assert r1.status_code in (200, 201), r1.text
    r2 = _idem(s, "POST", f"/events/{EV}/notes", {"team": "frc4911", "text": "fast cycler"}, "k-1")
    assert r2.status_code == r1.status_code and r2.json() == r1.json()
    assert r2.headers.get("idempotent-replayed") == "true"
    notes = s.get(f"/events/{EV}/notes?team=frc4911").json()
    items = notes["notes"] if isinstance(notes, dict) else notes
    assert len([n for n in items if n["text"] == "fast cycler"]) == 1
    # another key -> applied again
    assert _idem(s, "POST", f"/events/{EV}/notes", {"team": "frc4911", "text": "fast cycler"}, "k-2").status_code in (200, 201)
    # per user: the same key from someone else is their own request
    r = _idem(team["strategist"], "POST", f"/events/{EV}/notes", {"team": "frc4911", "text": "other"}, "k-1")
    assert r.status_code in (200, 201) and r.headers.get("idempotent-replayed") is None


def test_idempotency_conflict_and_errors(team, data):
    _follow(team["admin"], data)
    s = team["scout"]
    assert _idem(s, "POST", f"/events/{EV}/notes", {"team": "frc4911", "text": "a"}, "same").status_code in (200, 201)
    r = _idem(s, "POST", f"/events/{EV}/notes", {"team": "frc4911", "text": "b"}, "same")
    assert r.status_code == 422 and "Idempotency-Key" in r.json()["detail"]
    r = _idem(s, "PUT", f"/events/{EV}/picklists/main", {"data": {}}, "same")              # other path / method
    assert r.status_code == 422
    assert _idem(s, "POST", f"/events/{EV}/notes", {"team": "frc4911", "text": "a"}, "bad key!").status_code == 400
    # transient refusals are not stored: a viewer's 403 is not replayed once ... it stays 403 but recomputed
    v = team["viewer"]
    assert _idem(v, "POST", f"/events/{EV}/notes", {"team": "frc4911", "text": "x"}, "v1").status_code == 403
    import sqlite3
    c = sqlite3.connect(os.path.join(str(data), "app.db"))
    assert c.execute("SELECT COUNT(*) FROM idempotency WHERE key='v1'").fetchone()[0] == 0
    # stored validation errors (4xx that are deterministic) are replayed
    r1 = _idem(s, "POST", f"/events/{EV}/notes", {"team": "frc4911", "text": ""}, "e1")
    assert r1.status_code == 422
    # retention: rows older than 48 h are forgotten (the key can be used again)
    c.execute("UPDATE idempotency SET created_at=created_at-49*3600 WHERE key='same'")
    c.commit()
    c.close()
    r = _idem(s, "POST", f"/events/{EV}/notes", {"team": "frc4911", "text": "b"}, "same")
    assert r.status_code in (200, 201)


def test_idempotency_picklist_conflict_replayed(team, data):
    _follow(team["admin"], data)
    st = team["strategist"]
    r = _idem(st, "PUT", f"/events/{EV}/picklists/main", {"data": {"stars": []}, "base_version": 0}, "p1")
    assert r.status_code == 200, r.text
    r2 = _idem(st, "PUT", f"/events/{EV}/picklists/main", {"data": {"stars": []}, "base_version": 0}, "p1")
    assert r2.status_code == 200 and r2.json() == r.json() and r2.headers["idempotent-replayed"] == "true"
    # a stale base_version without the key -> 409 + current (unchanged behaviour)
    r3 = st.put(f"/events/{EV}/picklists/main", {"data": {"stars": ["frc1"]}, "base_version": 0})
    assert r3.status_code == 409


def test_idempotency_without_session_or_csrf(client, team, data):
    v = team["scout"]
    h = {"Idempotency-Key": "abc"}
    r = v.tc.post(f"/app/v1/events/{EV}/notes", json={"team": "frc4911", "text": "x"}, headers=h)
    assert r.status_code == 403                                           # CSRF guard runs first


def test_idempotency_in_flight(team, data, monkeypatch):
    from server import idempotency
    import sqlite3
    _follow(team["admin"], data)
    s = team["scout"]
    body = {"team": "frc4911", "text": "slow"}
    assert _idem(s, "POST", f"/events/{EV}/notes", body, "fl").status_code == 201
    c = sqlite3.connect(os.path.join(str(data), "app.db"))
    c.execute("UPDATE idempotency SET status=NULL, body=NULL WHERE key='fl'")      # as if still running
    c.commit()
    monkeypatch.setattr(idempotency, "WAIT_S", 0.3)
    r = _idem(s, "POST", f"/events/{EV}/notes", body, "fl")
    assert r.status_code == 429 and r.headers["retry-after"]
    # a "running" row left by a crashed request is taken over after STALE_PENDING
    c.execute("UPDATE idempotency SET created_at=created_at-600 WHERE key='fl'")
    c.commit()
    c.close()
    assert _idem(s, "POST", f"/events/{EV}/notes", body, "fl").status_code == 201
