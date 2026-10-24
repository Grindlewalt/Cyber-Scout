"""Admin -> Updates: the api only writes a fixed-schema request file and reads the host updater's status file.
Covers permissions, the request file (schema, no injection, round trip through the updater's own validator), status
parsing (whitelist / types / stale heartbeat), lockdown, CSRF, rate limit; plus the updater's pure helpers."""
import importlib.util, json, os, sys, time

import pytest

from server.routers import update as U

from .conftest import new_client

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHA_A, SHA_B = "a" * 40, "b" * 40


def _load_updater():
    spec = importlib.util.spec_from_file_location("cs_updater", os.path.join(ROOT, "deploy", "updater", "cs-updater.py"))
    m = importlib.util.module_from_spec(spec)
    dont, sys.dont_write_bytecode = sys.dont_write_bytecode, True          # no __pycache__ in deploy/updater/
    try:
        spec.loader.exec_module(m)
    finally:
        sys.dont_write_bytecode = dont
    return m


UPD = _load_updater()


@pytest.fixture(autouse=True)
def _reset_limiter():
    U.limiter.reset()
    yield
    U.limiter.reset()


def cfgdir(data):
    d = data / "config"
    d.mkdir(exist_ok=True)
    return d


def write_status(data, **over):
    st = {"v": 1, "updater_version": "1", "heartbeat": time.time(), "state": "idle", "action": None, "branch": "main",
          "current": {"sha": SHA_A, "date": 1791000000, "subject": "current one", "local": False},
          "latest": {"sha": SHA_B, "date": 1791100000, "subject": "newer"},
          "changelog": [{"sha": SHA_B, "date": 1791100000, "subject": "newer"}], "changelog_more": 0,
          "touches": {"worker": "light", "api": True, "web": False, "compose": False, "files": 3},
          "disk": {"free_gb": 3.0, "need_gb": 1.6, "ok": True, "path": "/var/lib/docker"},
          "previous": None, "progress": [], "result": None, "request": None, "rejected": None,
          "paused": True, "ytdlp": {"pinned": "2026.8.19"}, "ytdlp_allowed": True}
    st.update(over)
    (cfgdir(data) / U.STATUS_FILE).write_text(json.dumps(st))
    return st


def req_file(data):
    return cfgdir(data) / U.REQUEST_FILE


# ------------------------------------------------------------------ permissions
def test_admin_only(team, data):
    write_status(data)
    for role in ("strategist", "scout", "viewer"):
        c = team[role]
        assert c.get("/admin/update").status_code == 403
        assert c.post("/admin/update/check").status_code == 403
        assert c.post("/admin/update/apply", {}).status_code == 403
        assert c.post("/admin/update/rollback").status_code == 403
    assert not req_file(data).exists()


def test_signed_out_401(client, data):
    write_status(data)
    assert client.get("/admin/update").status_code == 401
    client.get("/session")
    assert client.post("/admin/update/check").status_code == 401
    assert not req_file(data).exists()


def test_csrf_required(admin, data):
    write_status(data)
    assert admin.post("/admin/update/check", csrf=False).status_code == 403
    assert not req_file(data).exists()


# ------------------------------------------------------------------ not installed
def test_not_installed(admin, data):
    st = admin.get("/admin/update").json()
    assert st["installed"] is False and "install-updater.sh" in st["install_hint"]
    r = admin.post("/admin/update/check")
    assert r.status_code == 409 and "isn't installed" in r.json()["detail"]
    # a stale heartbeat (updater stopped / uninstalled) counts as not installed
    write_status(data, heartbeat=time.time() - 600)
    assert admin.get("/admin/update").json()["installed"] is False
    assert admin.post("/admin/update/apply", {}).status_code == 409
    assert not req_file(data).exists()


# ------------------------------------------------------------------ the request file
def test_check_writes_fixed_schema_request_and_audits(admin, data):
    write_status(data)
    r = admin.post("/admin/update/check")
    assert r.status_code == 202, r.text
    req = json.loads(req_file(data).read_text())
    assert set(req) == {"v", "action", "nonce", "at", "by", "ytdlp"}
    assert req["v"] == 1 and req["action"] == "check" and req["ytdlp"] is False
    assert req["nonce"] == r.json()["nonce"] and len(req["nonce"]) == 32 and int(req["nonce"], 16) >= 0
    assert set(req["by"]) == {"id", "name"} and req["by"]["name"] == "Coach" and isinstance(req["by"]["id"], int)
    assert abs(req["at"] - time.time()) < 5
    # the host updater accepts exactly this
    assert UPD.validate_request(req_file(data).read_bytes(), time.time())["action"] == "check"
    e = admin.get("/audit?action=update").json()["entries"][0]
    assert e["action"] == "update.check" and e["after"]["nonce"] == req["nonce"] and e["before"]["commit"] == SHA_A
    assert e["revertible"] is False
    # shows up as pending until the updater consumes it; a second request is refused meanwhile
    assert admin.get("/admin/update").json()["pending"]["action"] == "check"
    assert admin.post("/admin/update/apply", {}).status_code == 409


def test_apply_body_is_strict_no_injection(admin, data):
    write_status(data)
    for body in ({"ytdlp": True, "cmd": "rm -rf /"}, {"ref": "evil"}, {"ytdlp": "yes"}, {"ytdlp": 1},
                 {"action": "rollback"}, {"remote": "https://evil.example/x.git"}):
        r = admin.post("/admin/update/apply", body)
        assert r.status_code == 422, (body, r.text)
        assert not req_file(data).exists()
    r = admin.post("/admin/update/apply", {"ytdlp": True})
    assert r.status_code == 202
    req = json.loads(req_file(data).read_text())
    assert req["action"] == "apply" and req["ytdlp"] is True
    assert set(req) == {"v", "action", "nonce", "at", "by", "ytdlp"}
    assert UPD.validate_request(json.dumps(req), time.time())["ytdlp"] is True


def test_display_name_cannot_break_the_request(admin, data, app):
    # an admin whose display name is hostile still produces a valid, plain-data request
    uid = [u for u in admin.get("/users").json() if u["username"] == "coach"][0]["id"]
    admin.patch(f"/users/{uid}", {"display_name": "x\"; rm -rf / #"})
    write_status(data)
    assert admin.post("/admin/update/check").status_code == 202
    req = json.loads(req_file(data).read_text())
    assert UPD.validate_request(json.dumps(req), time.time())["by"]["name"] == req["by"]["name"]


def test_busy_and_rollback_rules(admin, data):
    write_status(data, state="applying", action="apply")
    assert admin.post("/admin/update/check").status_code == 409
    write_status(data, state="done", previous=None)
    r = admin.post("/admin/update/rollback")
    assert r.status_code == 409 and "Nothing to roll back" in r.json()["detail"]
    write_status(data, state="done", previous={"sha": SHA_A, "date": 1, "subject": "older"})
    assert admin.post("/admin/update/rollback").status_code == 202
    assert json.loads(req_file(data).read_text())["action"] == "rollback"


def test_ytdlp_can_be_turned_off_on_the_host(admin, data):
    write_status(data, ytdlp_allowed=False)
    assert admin.post("/admin/update/apply", {"ytdlp": True}).status_code == 409
    assert admin.post("/admin/update/apply", {"ytdlp": False}).status_code == 202


def test_lockdown_does_not_block_admins(team, data):
    admin = team["admin"]
    assert admin.patch("/admin/security", {"lockdown": True}).status_code == 200
    write_status(data)
    assert admin.post("/admin/update/check").status_code == 202
    assert team["strategist"].post("/admin/update/check").status_code in (403, 423)


def test_rate_limited(admin, data):
    write_status(data)
    codes = []
    for _ in range(8):
        codes.append(admin.post("/admin/update/check").status_code)
        req_file(data).unlink(missing_ok=True)              # as if the updater picked it up at once
    assert codes[:6] == [202] * 6 and codes[6] == 429


# ------------------------------------------------------------------ status parsing
def test_status_parsing_whitelists_and_types(admin, data):
    write_status(data, state="hacked", evil="<script>", current={"sha": "not-a-sha", "subject": "x"},
                 changelog=[{"sha": SHA_B, "subject": "ok", "date": 5}, {"sha": "zz", "subject": "bad"}, "junk"],
                 progress=["line 1", 42, "x" * 1000] + ["p"] * 300,
                 result={"action": "apply", "ok": "yes", "message": "m" * 5000, "extra": 1},
                 touches={"worker": "everything", "api": 1, "web": True},
                 previous={"sha": SHA_A, "date": 1, "subject": "older", "images": {"x": "sha256:..."}})
    st = admin.get("/admin/update").json()
    assert st["installed"] is True and st["state"] == "idle" and "evil" not in st
    assert st["current"] is None
    assert [c["sha"] for c in st["changelog"]] == [SHA_B] and st["changelog"][0]["short"] == SHA_B[:7]
    assert len(st["progress"]) == 150 and all(isinstance(x, str) and len(x) <= 400 for x in st["progress"])
    assert st["result"]["ok"] is False and len(st["result"]["message"]) == 1200 and "extra" not in st["result"]
    assert st["touches"] == {"worker": None, "api": False, "web": True, "compose": False}
    assert st["previous"]["sha"] == SHA_A and "images" not in st["previous"]
    assert st["paused"] is True and st["ytdlp_pinned"] == "2026.8.19"


def test_status_garbage_and_symlink(admin, data, tmp_path):
    (cfgdir(data) / U.STATUS_FILE).write_text("{not json")
    assert admin.get("/admin/update").json()["installed"] is False
    (cfgdir(data) / U.STATUS_FILE).unlink()
    secret = tmp_path / "elsewhere.json"
    secret.write_text(json.dumps({"heartbeat": time.time(), "state": "done"}))
    os.symlink(secret, cfgdir(data) / U.STATUS_FILE)
    assert admin.get("/admin/update").json()["installed"] is False        # never followed


def test_parse_status_unit():
    t = 1_800_000_000
    assert U.parse_status(None, t) == {"installed": False, "state": "idle"}
    assert U.parse_status([], t)["installed"] is False
    s = U.parse_status({"heartbeat": t - 10, "state": "rolled_back", "action": "rm -rf", "heartbeat_x": 1}, t)
    assert s["installed"] and s["state"] == "rolled_back" and s["action"] is None
    assert U.parse_status({"heartbeat": True}, t)["installed"] is False                  # bool is not a number


# ------------------------------------------------------------------ the updater's own checks (host side)
def _req(**over):
    r = {"v": 1, "action": "apply", "nonce": "0" * 32, "at": time.time(), "by": {"id": 1, "name": "Coach"},
         "ytdlp": False}
    r.update(over)
    return json.dumps(r)


@pytest.mark.parametrize("bad", [
    {"action": "apply; rm -rf /"}, {"action": "shell"}, {"nonce": "../../etc"}, {"nonce": "A" * 32}, {"v": True},
    {"v": 2}, {"at": time.time() - 3600}, {"at": time.time() + 3600}, {"at": "now"}, {"ytdlp": "true"},
    {"by": {"id": 1, "name": "x\n$(reboot)"}}, {"by": {"id": 0, "name": "x"}}, {"by": {"id": 1, "name": "x", "cmd": 1}},
    {"by": "admin"}, {"ytdlp": True, "action": "check"}, {"cmd": "id"}])
def test_updater_rejects_off_schema_requests(bad):
    with pytest.raises(ValueError):
        UPD.validate_request(_req(**bad), time.time())


def test_updater_request_misc():
    assert UPD.validate_request(_req(), time.time())["action"] == "apply"
    with pytest.raises(ValueError):
        UPD.validate_request(_req(), time.time(), seen_nonces=["0" * 32])          # replay
    for raw in (b"\xff\xfe", b"[]", b"null", b'{"v":1}'):
        with pytest.raises(ValueError):
            UPD.validate_request(raw, time.time())


def test_updater_helpers():
    df = open(os.path.join(ROOT, "Dockerfile")).read()
    pre = UPD.heavy_prefix(df)
    assert pre and pre[0].startswith("FROM") and not any("COPY src/" in l for l in pre)
    assert UPD.heavy_prefix(df + "\nCOPY tools/ tools/\n") == pre                         # code-only change: light
    assert UPD.heavy_prefix(df.replace("numpy==", "numpy>=")) != pre                       # big layer: heavy
    assert UPD.rb_ref("cyber-scout-worker:latest") == "cyber-scout-worker:cs-rollback"
    assert UPD.rb_ref("reg.example:5000/x/api") == "reg.example:5000/x/api:cs-rollback"
    assert UPD.vtuple("2026.10.2") > UPD.vtuple("2026.8.19")
    assert UPD.redact("https://user:tok@github.com/x.git") == "https://***@github.com/x.git"
    assert UPD.parse_ps('{"Service":"api"}\n{"Service":"web"}') == [{"Service": "api"}, {"Service": "web"}]
    assert UPD.parse_ps('[{"Service":"api"}]') == [{"Service": "api"}]


def test_updater_ytdlp_lock_validation():
    body = open(os.path.join(ROOT, "live", "requirements-ytdlp.txt")).read()
    lock = body[body.index("# source:"):].split("\n", 1)[1]
    text, ver = UPD.validate_ytdlp_lock(lock)
    assert ver == UPD.pin_of(body)
    for evil in ("--index-url https://evil.example/simple\n", "-e git+https://evil.example/x\n",
                 "yt-dlp @ https://evil.example/x.whl\n", "requests==1.0 \\\n"):
        with pytest.raises(UPD.Fail):
            UPD.validate_ytdlp_lock(evil + lock if not evil.startswith("requests") else evil)


def test_updater_conf_is_strict(tmp_path):
    p = tmp_path / "updater.conf"
    p.write_text("REMOTE=git@github.com:Grindlewalt/Cyber-Scout.git\nBRANCH=main\nSSH_KEY=/opt/x/updater/deploy_key\n")
    c = UPD.load_conf(str(p))
    assert c["REMOTE"].startswith("git@") and c["MIN_FREE_GB"] == 1.0
    for bad in ("REMOTE=--upload-pack=touch /tmp/x", "BRANCH=-x", "BRANCH=a..b", "SSH_KEY=key with space",
                "NOPE=1", "MIN_FREE_GB=lots", "REMOTE=https://x/$(id)"):
        p.write_text(bad + "\n")
        with pytest.raises(UPD.Fail):
            UPD.load_conf(str(p))


# ------------------------------------------------------------------ containers (updater -> api: what Docker says)
def test_services_parsing_and_explanations():
    t = time.time()
    raw = {"heartbeat": t, "services_at": t, "services": [
        {"service": "worker", "name": "cs-worker", "state": "created", "exit_code": 128, "oom": False, "restarts": 0,
         "error": "x" * 5000, "started_at": None},
        {"service": "Bad Name; rm", "state": "running"}, {"service": "api", "state": "weird", "exit_code": "0"},
        "nope", {"service": "recorder", "state": "exited", "exit_code": 137, "oom": True},
        {"service": "web", "state": "running", "exit_code": 0, "error": ""}]}
    sv = {c["service"]: c for c in U.parse_status(raw, t)["services"]}
    assert set(sv) == {"worker", "api", "recorder", "web"}
    assert len(sv["worker"]["error"]) == 600 and sv["api"]["state"] is None and sv["api"]["exit_code"] is None
    assert sv["web"]["problem"] is None and sv["web"]["error"] is None
    assert "memory" in sv["recorder"]["problem"]["why"]
    assert "never started" in sv["worker"]["problem"]["why"]
    ex = lambda **c: U.explain({"service": "worker", **c})
    assert "CDI device list is out of date (it names /dev/dri/card0" in ex(
        state="created", error='CDI device injection failed: failed to stat CDI host device "/dev/dri/card0"')["why"]
    assert "nvidia-ctk cdi generate" in ex(state="created", error="unresolvable CDI devices nvidia.com/gpu=1")["fix"]
    assert "NVIDIA runtime" in ex(state="created", error='could not select device driver "nvidia"')["why"]
    assert "port" in ex(state="created", error="Bind for 0.0.0.0:7560 failed: port is already allocated")["why"]
    assert "disk is full" in ex(state="exited", exit_code=1, log="OSError: no space left on device")["why"]
    assert "exit code 2" in ex(state="exited", exit_code=2)["why"] and "logs" in ex(state="exited", exit_code=2)["fix"]
    assert ex(state="exited", exit_code=0)["why"] == "It was stopped."
    assert "restarting" in ex(state="restarting", restarts=7)["why"]
    assert "unpause" in ex(state="paused")["fix"]
    assert ex(state="running") is None


def test_server_env_lists_containers_only_while_fresh(admin, data):
    t = time.time()
    write_status(data, services_at=t, services=[{"service": "worker", "state": "created", "error": "boom"}])
    sv = admin.get("/admin/server-env").json()["services"]
    assert sv[0]["service"] == "worker" and sv[0]["problem"]["why"].startswith("It was created but never started")
    write_status(data, services_at=t - 3600, services=[{"service": "worker", "state": "created"}])
    assert admin.get("/admin/server-env").json()["services"] == []


def test_updater_reads_containers(tmp_path, monkeypatch):
    """cs-updater.py services(): docker compose ps + docker inspect -> the fixed shape, read-only."""
    for d in ("app", "data"):
        (tmp_path / d).mkdir()
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    u = UPD.Updater(str(tmp_path))
    calls = []
    inspect = [{"Name": "/cs-worker", "RestartCount": 0, "Config": {"Labels": {"com.docker.compose.service": "worker"}},
                "State": {"Status": "created", "ExitCode": 128, "Error": "nvidia-container-cli: mount error\x07",
                          "OOMKilled": False, "StartedAt": "0001-01-01T00:00:00Z", "FinishedAt": "0001-01-01T00:00:00Z"}},
               {"Name": "/cs-recorder", "RestartCount": 4, "Config": {"Labels": {"com.docker.compose.service": "recorder"}},
                "State": {"Status": "exited", "ExitCode": 1, "Error": "", "OOMKilled": False,
                          "StartedAt": "2026-10-24T16:22:10.123456789Z", "FinishedAt": "2026-10-24T16:25:00Z"}}]

    def run(argv, **kw):
        calls.append(argv)
        out = ""
        if argv[:3] == ["docker", "compose", "ps"]:
            out = "\n".join(json.dumps({"ID": i, "Service": s, "Labels": ""}) for i, s in (("c1", "worker"), ("c2", "recorder")))
            out += "\n" + json.dumps({"ID": "c3", "Service": "worker", "Labels": "com.docker.compose.oneoff=True"})
        elif argv[:2] == ["docker", "inspect"]:
            out = json.dumps(inspect)
        elif argv[:3] == ["docker", "compose", "logs"]:
            out = "cs-recorder  | starting\ncs-recorder  | Traceback\ncs-recorder  | RuntimeError: boom\n"
        return UPD.subprocess.CompletedProcess(argv, 0, out, "")
    monkeypatch.setattr(u, "run", run)
    sv = u.services()
    assert [e["service"] for e in sv] == ["recorder", "worker"]
    rec, wk = sv
    assert wk["state"] == "created" and wk["started_at"] is None and "\x07" not in wk["error"] and "log" not in wk
    assert rec["started_at"] == 1792858930 and rec["restarts"] == 4 and rec["log"].endswith("RuntimeError: boom")
    assert ["docker", "inspect", "c1", "c2"] in calls                  # the one-off container isn't looked at
    assert not any(a[:3] in (["docker", "compose", "up"], ["docker", "compose", "start"]) for a in calls)
    assert U.parse_status({"heartbeat": time.time(), "services": sv}, time.time())["services"][0]["problem"]
