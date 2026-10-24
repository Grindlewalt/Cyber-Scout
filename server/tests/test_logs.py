"""GET /admin/logs: admin only; the files in DATA/logs are written by the containers, so they are untrusted."""
import json, os, time

import pytest

from .conftest import GOOD_KEY, new_client


def put(data, role, n=3, at=None, **kw):
    d = data / "logs"; d.mkdir(exist_ok=True)
    now = time.time() if at is None else at
    doc = dict(role=role, pid=7, started=now - 600, at=now, doing="tracking Q3 (livestream clip)", doing_since=now - 130,
               seq=n, lines=[dict(seq=i + 1, t=now - 10 + i, text=f"line {i + 1}") for i in range(n)])
    doc.update(kw)
    (d / f"{role}.json").write_text(json.dumps(doc))


def test_admin_only(team, app):
    for role in ("strategist", "scout", "viewer"):
        assert team[role].get("/admin/logs").status_code == 403, role
    assert new_client(app).get("/admin/logs").status_code == 401
    assert team["admin"].get("/admin/logs").json() == {"roles": []}


def test_roles_and_since(team, data):
    put(data, "worker", 5); put(data, "recorder", 2); put(data, "processor-gpu0-x", 1)
    r = team["admin"].get("/admin/logs").json()["roles"]
    assert [x["role"] for x in r] == ["worker", "recorder", "processor-gpu0-x"]
    w = r[0]
    assert w["alive"] and w["doing"] == "tracking Q3 (livestream clip)" and w["seq"] == 5 and len(w["lines"]) == 5
    assert w["age_s"] < 5 and w["pid"] == 7 and w["doing_since"]
    r = team["admin"].get("/admin/logs?since=worker:3,recorder:2,bad,x:y").json()["roles"]
    assert [l["seq"] for l in r[0]["lines"]] == [4, 5] and r[1]["lines"] == [] and len(r[2]["lines"]) == 1


def test_stale_is_not_alive(team, data):
    put(data, "worker", 2, at=time.time() - 200)
    put(data, "processor-old", 1, at=time.time() - 3 * 86400)
    r = team["admin"].get("/admin/logs").json()["roles"]
    assert [x["role"] for x in r] == ["worker"] and r[0]["alive"] is False and r[0]["age_s"] >= 199
    assert len(r[0]["lines"]) == 2                           # a stopped worker's last lines are still the useful ones


def test_garbage_ignored(team, data):
    d = data / "logs"; d.mkdir()
    (d / "worker.json").write_text("{not json")
    (d / "recorder.json").write_text(json.dumps(["a list"]))
    (d / "processor-a.json").write_text(json.dumps({"at": "yesterday", "lines": []}))
    (d / "processor-b.json").write_text("x" * 2_100_000)
    (d / "bad name.json").write_text("{}")
    (d / "notes.txt").write_text("x")
    put(data, "processor-c", 0, doing=["not", "text"], lines=[{"seq": "1", "t": 1, "text": "s"}, 5, {"seq": 2, "t": 1, "text": 9},
                                                              {"seq": 3, "t": 1, "text": "ok"}], also="nope", pid="x", started=None)
    r = team["admin"].get("/admin/logs").json()["roles"]
    assert [x["role"] for x in r] == ["processor-c"]
    assert r[0]["doing"] == "" and r[0]["also"] == [] and r[0]["pid"] is None and [l["text"] for l in r[0]["lines"]] == ["ok"]


def test_symlink_not_followed(team, data, tmp_path_factory):
    secret = tmp_path_factory.mktemp("outside") / "s.json"
    secret.write_text(json.dumps({"at": time.time(), "doing": "LEAK", "lines": []}))
    d = data / "logs"; d.mkdir()
    os.symlink(secret, d / "worker.json")
    assert team["admin"].get("/admin/logs").json()["roles"] == []


def test_clipped(team, data):
    put(data, "worker", 1, doing="d" * 5000, lines=[dict(seq=1, t=1.0, text="t" * 5000)])
    w = team["admin"].get("/admin/logs").json()["roles"][0]
    assert len(w["doing"]) == 200 and len(w["lines"][0]["text"]) == 500
