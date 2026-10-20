"""Label review server (live/train/review/server.py): token, path safety, atomic writes, queue order, validation."""
import http.client, importlib.util, json, os, threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("review_server", os.path.join(ROOT, "live", "train", "review", "server.py"))
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"


def _frame(i, split="train", event="2026x", match="qm1", t=0.0, path=None):
    return {"id": i, "path": path or "frames/img/%s.jpg" % i, "event": event, "match": match, "video": "v", "t": t,
            "w": 1280, "h": 720, "view": "F", "phash": "0", "src": "upload", "split": split}


def _auto(proj, i, boxes, u=None):
    d = {"id": i, "boxes": boxes, "teacher": "r2", "at": 1}
    if u is not None:
        d["u"] = u
    os.makedirs(os.path.join(proj, "labels", "auto"), exist_ok=True)
    with open(os.path.join(proj, "labels", "auto", i + ".json"), "w") as f:
        json.dump(d, f)


def _review(proj, i, by="ann", at=1.0, status="ok"):
    os.makedirs(os.path.join(proj, "labels", "review"), exist_ok=True)
    with open(os.path.join(proj, "labels", "review", i + ".json"), "w") as f:
        json.dump({"id": i, "boxes": [], "status": status, "by": by, "at": at}, f)


@pytest.fixture
def proj(tmp_path):
    p = str(tmp_path / "proj")
    os.makedirs(os.path.join(p, "frames", "img"))
    frames = [_frame("f1", t=1), _frame("f2", t=2), _frame("f3", t=3), _frame("f4", split="val", t=4),
              _frame("f5", split="test", t=5), _frame("f6", split="test", t=6), _frame("f7", t=7)]
    with open(os.path.join(p, "frames", "index.jsonl"), "w") as f:
        for r in frames:
            f.write(json.dumps(r) + "\n")
        f.write("not json\n")
        f.write(json.dumps(_frame("evil", path="../secret.jpg")) + "\n")
        f.write(json.dumps(_frame("abs", path="/etc/passwd.jpg")) + "\n")
    for r in frames:
        with open(os.path.join(p, r["path"]), "wb") as f:
            f.write(JPEG)
    with open(os.path.join(str(tmp_path), "secret.jpg"), "wb") as f:
        f.write(b"secret")
    with open(os.path.join(p, "project.json"), "w") as f:
        json.dump({"name": "test", "classes": ["robot", "piece"], "colors": ["#123456"]}, f)
    return p


@pytest.fixture
def srv(proj):
    s, project, token = rs.make_server(proj, "127.0.0.1", 0, token="tok-123")
    th = threading.Thread(target=s.serve_forever, daemon=True)
    th.start()
    yield s, project, token
    s.shutdown()
    s.server_close()


def req(s, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    h = dict(headers or {})
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    out = r.status, dict(r.getheaders()), r.read()
    c.close()
    return out


AUTH = {"Cookie": "csreview_0=tok-123"}


# ---- token
def test_token_required_and_cookie(srv):
    s, _, _ = srv
    assert req(s, "GET", "/api/project")[0] == 401
    assert req(s, "GET", "/")[0] == 401
    assert req(s, "GET", "/?token=wrong")[0] == 401
    st, h, _ = req(s, "GET", "/?token=tok-123")
    assert st == 302 and h["Location"] == "/"
    assert "csreview_0=tok-123" in h["Set-Cookie"] and "HttpOnly" in h["Set-Cookie"] and "SameSite=Strict" in h["Set-Cookie"]
    st, h, body = req(s, "GET", "/", headers=AUTH)
    assert st == 200 and b"review.js" in body and "script-src 'self'" in h["Content-Security-Policy"]
    assert req(s, "GET", "/api/project", headers={"Cookie": "csreview_0=nope"})[0] == 401
    assert req(s, "GET", "/api/project", headers={"X-Review-Token": "tok-123"})[0] == 200
    assert req(s, "POST", "/api/review/f1", {"status": "ok", "by": "a", "boxes": []})[0] == 401


def test_random_token_when_none(proj):
    s, _, token = rs.make_server(proj, "127.0.0.1", 0)
    s.server_close()
    assert len(token) >= 20


# ---- path safety
def test_image_traversal(srv):
    s, project, _ = srv
    st, h, body = req(s, "GET", "/img/f1", headers=AUTH)
    assert st == 200 and body == JPEG and h["Content-Type"] == "image/jpeg" and "max-age" in h["Cache-Control"]
    assert req(s, "GET", "/img/f1", headers=dict(AUTH, **{"If-None-Match": h["ETag"]}))[0] == 304
    assert req(s, "GET", "/img/evil", headers=AUTH)[0] == 404          # index path escapes the project
    assert req(s, "GET", "/img/abs", headers=AUTH)[0] == 404           # absolute index path
    for bad in ("/img/..%2Fsecret", "/img/f1.jpg", "/img/a%00b", "/api/frame/..", "/img/../../etc/passwd"):
        assert req(s, "GET", bad, headers=AUTH)[0] in (400, 404)
    assert project.image_path("evil") is None and project.image_path("nope") is None


# ---- atomic writes
def test_save_is_atomic_and_leaves_no_temp(srv, proj, monkeypatch):
    s, project, _ = srv
    st, _, body = req(s, "POST", "/api/review/f1", {"status": "fixed", "by": "Ann", "boxes": [{"c": 0, "b": [10, 10, 50, 60]}],
                                                    "diff": {"added": 1, "removed": 0, "moved": 2}, "conf": 0.3}, AUTH)
    assert st == 200
    path = os.path.join(proj, "labels", "review", "f1.json")
    rec = json.load(open(path))
    assert rec["status"] == "fixed" and rec["by"] == "Ann" and rec["boxes"] == [{"c": 0, "b": [10.0, 10.0, 50.0, 60.0]}]
    assert rec["diff"] == {"added": 1, "removed": 0, "moved": 2} and rec["conf"] == 0.3
    before = open(path).read()

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(rs.json, "dump", boom)
    with pytest.raises(OSError):
        project.save_review("f1", {"status": "ok", "by": "Ann", "boxes": []})
    assert open(path).read() == before
    assert [n for n in os.listdir(os.path.dirname(path)) if n.startswith(".tmp")] == []


# ---- validation
def test_validation(srv, proj):
    s, _, _ = srv
    post = lambda i, b, h=AUTH: req(s, "POST", "/api/review/" + i, b, h)[0]
    assert post("f1", {"status": "great", "by": "a", "boxes": []}) == 400
    assert post("f1", {"status": "ok", "by": "", "boxes": []}) == 400
    assert post("f1", {"status": "ok", "by": "a", "boxes": [{"c": 2, "b": [0, 0, 5, 5]}]}) == 400      # 2 classes
    assert post("f1", {"status": "ok", "by": "a", "boxes": [{"c": True, "b": [0, 0, 5, 5]}]}) == 400
    assert post("f1", {"status": "ok", "by": "a", "boxes": [{"c": 0, "b": [0, 0, 5]}]}) == 400
    assert post("f1", {"status": "ok", "by": "a", "boxes": [{"c": 0, "b": [0, 0, "x", 5]}]}) == 400
    assert post("nope", {"status": "ok", "by": "a", "boxes": []}) == 404
    assert post("f1", b"{bad", AUTH) == 400
    assert req(s, "POST", "/api/review/f1", b'{"status":"ok"}', dict(AUTH, **{"Content-Type": "text/plain"}))[0] == 415
    assert req(s, "POST", "/api/review/f1", {"status": "ok", "by": "a", "boxes": []},
               dict(AUTH, Origin="http://evil.example"))[0] == 403
    assert post("f2", {"status": "ok", "by": "a<script>", "boxes": [{"c": 1, "b": [1300, 700, 1200, -5]},
                                                                   {"c": 0, "b": [5, 5, 6, 6]}]}) == 200
    rec = json.load(open(os.path.join(proj, "labels", "review", "f2.json")))
    assert rec["by"] == "ascript" and rec["boxes"] == [{"c": 1, "b": [1200.0, 0.0, 1280.0, 700.0]}]   # sorted, clipped, tiny dropped
    assert post("f3", {"status": "skip", "by": "a", "boxes": [{"c": 0, "b": [0, 0, 50, 50]}]}) == 200
    assert json.load(open(os.path.join(proj, "labels", "review", "f3.json")))["boxes"] == []


# ---- queues
def test_uncertain_order(proj):
    _auto(proj, "f1", [{"c": 0, "b": [0, 0, 9, 9], "p": 0.9, "src": "teacher:r2"}], u=0.2)
    _auto(proj, "f2", [{"c": 0, "b": [0, 0, 9, 9], "p": 0.4, "src": "teacher:r2"}], u=0.7)
    _auto(proj, "f3", [{"c": 0, "b": [0, 0, 9, 9], "src": "bglabel"}], u=0.2)
    _auto(proj, "f4", [{"c": 0, "b": [0, 0, 9, 9], "p": 0.5, "src": "teacher:r2"},
                       {"c": 0, "b": [0, 0, 9, 9], "p": 0.3, "src": "teacher:r2"}], u=0.2)
    _auto(proj, "f5", [{"c": 0, "b": [0, 0, 9, 9], "p": 0.45, "src": "teacher:r2"}])          # no u: from p -> 0.9
    _auto(proj, "f7", [], u=0.2)
    p = rs.Project(proj)
    ids = [x["id"] for x in p.queue("uncertain")["items"]]
    # f6 has no teacher output (most uncertain); then u; ties: bglabel boxes, then mid-confidence boxes, then time
    assert ids == ["f6", "f5", "f2", "f3", "f4", "f1", "f7"]
    _review(proj, "f6")
    p.review.checked = 0
    assert [x["id"] for x in p.queue("uncertain")["items"]][0] == "f5"


def test_uncertain_puts_imported_human_labels_last(proj):
    _auto(proj, "f1", [{"c": 0, "b": [0, 0, 9, 9], "p": 0.9, "src": "teacher:r2"}], u=0.2)
    os.makedirs(os.path.join(proj, "labels", "import"), exist_ok=True)
    with open(os.path.join(proj, "labels", "import", "f6.json"), "w") as f:
        json.dump({"id": "f6", "boxes": [{"c": 0, "b": [0, 0, 9, 9]}]}, f)
    ids = [x["id"] for x in rs.Project(proj).queue("uncertain")["items"]]
    assert ids[0] != "f6" and ids[-1] == "f6"


def test_gold_random_mine_filters(proj):
    _review(proj, "f5", by="ann", at=10)
    _review(proj, "f1", by="bob", at=20)
    _review(proj, "f2", by="ann", at=30, status="fixed")
    p = rs.Project(proj)
    assert [x["id"] for x in p.queue("gold")["items"]] == ["f6", "f4"]                      # test before val, unreviewed
    r1 = [x["id"] for x in p.queue("random", seed=7)["items"]]
    assert r1 == [x["id"] for x in p.queue("random", seed=7)["items"]] and sorted(r1) == ["f3", "f4", "f6", "f7"]
    assert [x["id"] for x in p.queue("mine", by="ann")["items"]] == ["f2", "f5"]
    assert [x["id"] for x in p.queue("mine")["items"]] == ["f2", "f1", "f5"]
    assert [x["id"] for x in p.queue("all", split="test")["items"]] == ["f5", "f6"]
    q = p.queue("all", offset=2, limit=2)
    assert q["total"] == 7 and [x["id"] for x in q["items"]] == ["f3", "f4"]
    st = p.stats()
    assert st["total"] == 7 and st["reviewed"] == 3 and st["status"]["fixed"] == 1 and st["splits"]["test"] == {"total": 2, "reviewed": 1}
    info = p.info()
    assert info["classes"] == ["robot", "piece"] and info["colors"][0] == "#123456" and info["colors"][1].startswith("#")
    assert info["events"] == {"2026x": ["qm1"]}


def test_http_queue_and_frame(srv, proj):
    s, _, _ = srv
    _auto(proj, "f1", [{"c": 0, "b": [1, 2, 30, 40], "p": 0.8, "src": "teacher:r2"}], u=0.3)
    st, _, body = req(s, "GET", "/api/queue?q=uncertain&split=train", headers=AUTH)
    d = json.loads(body)
    assert st == 200 and d["total"] == 4 and all(x["split"] == "train" for x in d["items"])
    assert req(s, "GET", "/api/queue?q=bogus", headers=AUTH)[0] == 400
    st, _, body = req(s, "GET", "/api/frame/f1", headers=AUTH)
    d = json.loads(body)
    assert st == 200 and d["auto"]["boxes"][0]["p"] == 0.8 and d["review"] is None and d["frame"]["id"] == "f1"
    assert req(s, "GET", "/api/frame/zzz", headers=AUTH)[0] == 404
    for path in ("/review.js", "/review.css"):
        assert req(s, "GET", path, headers=AUTH)[0] == 200


# ---- review UI support: undo (DELETE), view correction, hidden count, names, match order, the no-token page
def test_delete_review_for_undo(srv, proj):
    s, project, _ = srv
    path = os.path.join(proj, "labels", "review", "f1.json")
    assert req(s, "POST", "/api/review/f1", {"status": "bad", "by": "Ann", "boxes": []}, AUTH)[0] == 200
    assert "f1" not in [x["id"] for x in project.queue("uncertain")["items"]]
    assert req(s, "DELETE", "/api/review/f1")[0] == 401
    assert req(s, "DELETE", "/api/review/f1", headers=dict(AUTH, Origin="http://evil.example"))[0] == 403
    assert os.path.exists(path)
    st, _, body = req(s, "DELETE", "/api/review/f1", headers=AUTH)
    assert st == 200 and json.loads(body) == {"id": "f1", "deleted": True} and not os.path.exists(path)
    assert "f1" in [x["id"] for x in project.queue("uncertain")["items"]]           # unreviewed again, at once
    assert json.loads(req(s, "DELETE", "/api/review/f1", headers=AUTH)[2])["deleted"] is False
    assert req(s, "DELETE", "/api/review/nope", headers=AUTH)[0] == 404
    assert req(s, "DELETE", "/api/review/..%2Fx", headers=AUTH)[0] == 404


def test_view_and_hidden_are_kept(srv, proj):
    s, _, _ = srv
    post = lambda b: req(s, "POST", "/api/review/f1", dict({"status": "ok", "by": "a", "boxes": []}, **b), AUTH)[0]
    assert post({"view": "M", "hidden": 3}) == 200
    rec = json.load(open(os.path.join(proj, "labels", "review", "f1.json")))
    assert rec["view"] == "M" and rec["hidden"] == 3
    assert post({"view": "X"}) == 400
    assert post({"hidden": -1, "view": None}) == 200
    rec = json.load(open(os.path.join(proj, "labels", "review", "f1.json")))
    assert "hidden" not in rec and "view" not in rec


def test_mine_uses_the_stored_name_and_natural_match_order(proj):
    _review(proj, "f1", by="ann")
    p = rs.Project(proj)
    assert [x["id"] for x in p.queue("mine", by="ann!")["items"]] == ["f1"]           # "ann!" is stored as "ann"
    assert rs.clean_name(" ann<b>! ") == "annb"
    with open(os.path.join(proj, "frames", "index.jsonl"), "a") as f:
        for m in ("qm10", "qm2", "sf1m1"):
            f.write(json.dumps(_frame("m" + m, match=m)) + "\n")
    p.refresh(force=True)
    assert p.info()["events"]["2026x"] == ["qm1", "qm2", "qm10", "sf1m1"]


def test_no_token_page_is_for_students(srv):
    s, _, _ = srv
    st, h, body = req(s, "GET", "/")
    assert st == 401 and b"Ask your mentor" in body and b"review.css" in body and "no-store" in h["Cache-Control"]
    assert req(s, "GET", "/review.css")[0] == 200                                     # styles the page above
    assert req(s, "GET", "/review.js")[0] == 401 and req(s, "GET", "/api/stats")[0] == 401


def test_empty_overlay_never_takes_the_pointer():
    """The empty-state layer covers the canvas: `display: flex` beats the hidden attribute unless [hidden] is restated,
    and it must let pointer events through (it once swallowed every draw, drag, wheel and pinch)."""
    css = open(os.path.join(ROOT, "live", "train", "review", "review.css")).read()
    rule = lambda sel: css.split("\n" + sel + " {", 1)[1].split("}", 1)[0]
    assert "display: none" in rule(".empty[hidden]") and "pointer-events: none" in rule(".empty")
