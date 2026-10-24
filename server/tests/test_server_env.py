"""Admin -> Server: a few .env settings changed through the host updater (action "env").
Covers the api (permissions, strict values, request round trip through the updater's validator, busy rules, status
parsing), the updater's pure .env helpers, and a whole env run against a fake `docker` (apply, health with scaled
processors, automatic rollback of .env)."""
import json, os, re, stat, sys, time

import pytest

from server.routers import update as U

from .test_update import ROOT, UPD, req_file, write_status

ENV_VALUES = {"TZ": "America/Los_Angeles", "PROCESSORS_GPU1": None, "GPU_IDS": None, "CS_URL_ALLOW": None}


@pytest.fixture(autouse=True)
def _reset_limiter():
    U.limiter.reset()
    yield
    U.limiter.reset()


def write_v2(data, values=None, **over):
    vals = {k: None for k in UPD.ENV_KEYS}
    vals.update(ENV_VALUES if values is None else values)
    kw = dict(updater_version="2", features=["env"], env={"values": vals, "schema": UPD.ENV_KEYS}, env_progress=[],
              env_result=None)
    kw.update(over)
    return write_status(data, **kw)


# ------------------------------------------------------------------ api
def test_admin_only(team, data):
    write_v2(data)
    for role in ("strategist", "scout", "viewer"):
        assert team[role].get("/admin/server-env").status_code == 403
        assert team[role].post("/admin/server-env", {"changes": {"TZ": "UTC"}}).status_code == 403
    assert not req_file(data).exists()


def test_not_installed_or_old_updater(admin, data):
    st = admin.get("/admin/server-env").json()
    assert st["installed"] is False and "install-updater.sh" in st["install_hint"]
    assert admin.post("/admin/server-env", {"changes": {"TZ": "UTC"}}).status_code == 409
    write_status(data)                                     # a v1 updater: no "env" feature
    st = admin.get("/admin/server-env").json()
    assert st["installed"] is True and st["supported"] is False
    r = admin.post("/admin/server-env", {"changes": {"TZ": "UTC"}})
    assert r.status_code == 409 and "too old" in r.json()["detail"]
    assert not req_file(data).exists()


def test_status_lists_only_allowlisted_keys(admin, data):
    write_v2(data)
    st = admin.get("/admin/server-env").json()
    assert set(st["schema"]) == set(UPD.ENV_KEYS) == set(st["values"])
    assert st["values"]["TZ"] == "America/Los_Angeles" and st["values"]["GPU_IDS"] is None
    assert st["schema"]["PROCESSORS_GPU0"] == {"kind": "int", "default": "0", "api": False, "min": 0, "max": 8}
    for k in ("TBA_KEY", "WEB_PORT", "PUID", "MEDIA_DIR", "COMPOSE_FILE", "PAUSE_PROCESSING"):
        assert k not in st["schema"]


def test_post_writes_request_that_the_updater_accepts(admin, data):
    write_v2(data)
    r = admin.post("/admin/server-env", {"changes": {"PROCESSORS_GPU1": " 2 ", "TZ": "", "GPU_IDS": None,
                                                     "CS_URL_ALLOW": "10.0.0.20,192.168.1.0/24"}})
    assert r.status_code == 202, r.text
    req = json.loads(req_file(data).read_text())
    assert set(req) == {"v", "action", "nonce", "at", "by", "ytdlp", "env"} and req["action"] == "env"
    # unchanged keys (GPU_IDS already unset) are left out; "" = back to the default
    assert req["env"] == {"PROCESSORS_GPU1": "2", "TZ": None, "CS_URL_ALLOW": "10.0.0.20,192.168.1.0/24"}
    assert UPD.validate_request(req_file(data).read_bytes(), time.time())["env"] == req["env"]
    e = admin.get("/audit?action=server").json()["entries"][0]
    assert e["action"] == "server.env" and e["before"]["TZ"] == "America/Los_Angeles" and e["after"]["TZ"] is None
    assert e["revertible"] is False
    # pending: shown as running; another settings change or an update waits
    assert admin.get("/admin/server-env").json()["running"] is True
    assert admin.post("/admin/server-env", {"changes": {"TZ": "UTC"}}).status_code == 409
    assert admin.post("/admin/update/check").status_code == 409


@pytest.mark.parametrize("changes", [
    {"TBA_KEY": "abc"}, {"WEB_PORT": "80"}, {"PAUSE_PROCESSING": "0"}, {"COMPOSE_FILE": "x.yml"}, {"tz": "UTC"},
    {"GPU_IDS": "0;rm -rf /"}, {"GPU_IDS": "0\nTBA_KEY=x"}, {"GPU_IDS": "all"}, {"PROCESSORS_GPU0": "9"},
    {"PROCESSORS_GPU0": "-1"}, {"PROCESSORS_GPU0": "1.5"}, {"PROCESSORS_GPU0": "0x2"}, {"WORKER_MEM_LIMIT": "12gb"},
    {"WORKER_MEM_LIMIT": "0g"}, {"TZ": "../../etc/passwd"}, {"TZ": "UTC #x"}, {"CS_URL_ALLOW": "a b"},
    {"CS_URL_ALLOW": "x\"y"}, {"MEDIA_PROXIES": "yes"}, {"MEDIA_PROXIES": "true"}, {"WORKER_TRACK": "always"},
    {"PROXY_ENCODER": "$(id)"}, {"LIVE_BUDGET_GB": "0"}, {"SESSION_IDLE_DAYS": "1000"}])
def test_post_rejects_bad_keys_and_values(admin, data, changes):
    write_v2(data)
    r = admin.post("/admin/server-env", {"changes": changes})
    assert r.status_code == 422, (changes, r.text)
    assert not req_file(data).exists()
    with pytest.raises(ValueError):                         # the updater refuses them too
        UPD.validate_env(changes)


def test_post_body_and_no_op(admin, data):
    write_v2(data)
    for body in ({}, {"changes": {}}, {"changes": {"TZ": "UTC"}, "cmd": "id"}, {"changes": {"TZ": 5}},
                 {"changes": "TZ=UTC"}):
        assert admin.post("/admin/server-env", body).status_code == 422, body
    r = admin.post("/admin/server-env", {"changes": {"TZ": "America/Los_Angeles", "GPU_IDS": ""}})
    assert r.status_code == 400 and r.json()["detail"] == "Nothing changed."
    assert not req_file(data).exists()


def test_busy_updater(admin, data):
    write_v2(data, state="applying", action="apply")
    st = admin.get("/admin/server-env").json()
    assert st["busy"] and st["other_task"] and not st["running"]
    assert admin.post("/admin/server-env", {"changes": {"TZ": "UTC"}}).status_code == 409
    write_v2(data, state="applying", action="env", env_progress=["12:00:00 Changing .env: TZ"])
    st = admin.get("/admin/server-env").json()
    assert st["running"] and not st["other_task"] and st["progress"] == ["12:00:00 Changing .env: TZ"]
    assert admin.get("/admin/update").json()["action"] == "env"


def test_status_parsing_drops_odd_schema(admin, data):
    write_status(data, features=["env"], env={"values": {"TZ": "UTC", "X": "y", "BAD": 5}, "schema": {
        "TZ": {"kind": "tz", "default": "UTC"}, "lower": {"kind": "bool"}, "X": {"kind": "regex", "pattern": "(a+)+$"},
        "Y": {"kind": "choice", "choices": ["ok", "rm -rf"]}, "Z": {"kind": "choice", "choices": []},
        "N": {"kind": "int", "min": "0", "max": 3}, "BAD": {"kind": "bool"}}},
        env_result={"ok": "yes", "message": "m" * 5000, "rolled_back": 1})
    st = admin.get("/admin/server-env").json()
    assert set(st["schema"]) == {"TZ", "Y", "BAD"} and st["schema"]["Y"]["choices"] == ["ok"]
    assert st["values"] == {"TZ": "UTC", "Y": None, "BAD": None}
    assert st["result"]["ok"] is False and st["result"]["rolled_back"] is False and len(st["result"]["message"]) == 1200


def test_api_and_updater_agree():
    assert U.ENV_FORMATS == UPD.ENV_FORMATS
    assert all(sp["kind"] in U.ENV_KINDS for sp in UPD.ENV_KEYS.values())
    assert U._env_schema(UPD.ENV_KEYS).keys() == UPD.ENV_KEYS.keys()          # nothing the api would drop
    ui = open(os.path.join(ROOT, "web", "admin.js"), encoding="utf-8").read()
    for k in UPD.ENV_KEYS:                                                  # every key has a label in Admin -> Server
        assert re.search(r'\["%s", "' % k, ui), k
    for k, sp in UPD.ENV_KEYS.items():                                      # defaults are valid values
        assert UPD.env_check(k, sp["default"]) in (sp["default"] or None, None)


# ------------------------------------------------------------------ the updater's .env helpers
ENV_TEXT = """# Cyber-Scout
TZ=America/Los_Angeles
PUID=1000
export GPU_IDS=0
TBA_KEY="secret key"
PROCESSORS_GPU1=1
#PROCESSORS_GPU1=5
PROCESSORS_GPU1=3 # two jobs
"""


def test_env_values_and_edit():
    v = UPD.env_values(ENV_TEXT)
    assert v["GPU_IDS"] == "0" and v["TBA_KEY"] == "secret key" and v["PROCESSORS_GPU1"] == "3"   # last one wins
    out = UPD.env_edit(ENV_TEXT, {"PROCESSORS_GPU1": "2", "GPU_IDS": None, "LIVESTREAM": "0", "TZ": "UTC"})
    lines = out.splitlines()
    assert "PROCESSORS_GPU1=2" in lines and "PROCESSORS_GPU1=1" not in lines and "#PROCESSORS_GPU1=5" in lines
    assert not any(l.lstrip().startswith(("GPU_IDS", "export GPU_IDS")) for l in lines)
    assert lines[-2:] == [UPD.ENV_MARK, "LIVESTREAM=0"] and "TZ=UTC" in lines and "PUID=1000" in lines
    assert 'TBA_KEY="secret key"' in lines and lines[0] == "# Cyber-Scout"
    nv = UPD.env_values(out)
    assert "GPU_IDS" not in nv and nv["PROCESSORS_GPU1"] == "2" and nv["TBA_KEY"] == "secret key"
    # a second change reuses the marker; no change = same text
    out2 = UPD.env_edit(out, {"MEDIA_PROXIES": "0"})
    assert out2.count(UPD.ENV_MARK) == 1 and out2.endswith("LIVESTREAM=0\nMEDIA_PROXIES=0\n")
    assert UPD.env_edit(ENV_TEXT, {}) == ENV_TEXT
    assert UPD.env_edit("", {"TZ": "UTC"}) == UPD.ENV_MARK + "\nTZ=UTC\n"
    assert UPD.env_edit("TZ=UTC\n", {"TZ": None}) == ""
    # the last Admin-set key removed: the marker (and the blank line before it) go too, so .env is as it was
    base = "TZ=UTC\nPUID=1000\n"
    added = UPD.env_edit(base, {"YT_FORCE_IPV4": "1"})
    assert added == base + "\n" + UPD.ENV_MARK + "\nYT_FORCE_IPV4=1\n"
    assert UPD.env_edit(added, {"YT_FORCE_IPV4": None}) == base
    assert UPD.env_edit(UPD.ENV_MARK + "\nX=1\n", {"X": None}) == ""


def test_updater_env_requests():
    def req(**over):
        r = {"v": 1, "action": "env", "nonce": "1" * 32, "at": time.time(), "by": {"id": 1, "name": "Coach"},
             "ytdlp": False, "env": {"TZ": "UTC"}}
        r.update(over)
        return json.dumps(r)
    assert UPD.validate_request(req(), time.time())["env"] == {"TZ": "UTC"}
    assert UPD.validate_request(req(env={"TZ": ""}), time.time())["env"] == {"TZ": None}
    for bad in ({"env": {}}, {"env": []}, {"env": "TZ=UTC"}, {"env": {"TZ": 5}}, {"env": {"PUID": "0"}},
                {"env": {"TZ": "UTC", "x": None}}, {"action": "apply"}, {"ytdlp": True}):
        with pytest.raises(ValueError):
            UPD.validate_request(req(**bad), time.time())
    with pytest.raises(ValueError):                         # env without the field
        r = json.loads(req())
        del r["env"]
        UPD.validate_request(json.dumps(r), time.time())


# ------------------------------------------------------------------ a whole env run against a fake docker
FAKE_DOCKER = r'''#!%s
import json, os, sys
a = sys.argv[1:]
with open(os.environ["FAKE_DOCKER_LOG"], "a") as f:
    f.write(" ".join(a) + "\n")
vals = {}
if os.path.exists(".env"):
    for l in open(".env"):
        if "=" in l and not l.startswith("#"):
            k, v = l.strip().split("=", 1)
            vals[k] = v.split(" #")[0].strip()
n1 = int(vals.get("PROCESSORS_GPU1", "0"))
if a[:2] == ["compose", "config"]:
    if "--quiet" not in a:
        print(json.dumps({"name": "cs", "services": {"worker": {"image": "cs-worker:latest", "build": {}}, "api": {},
                                                     "processor-gpu1": {"deploy": {"replicas": n1}}}}))
elif a[:2] == ["compose", "ps"]:
    rows = [{"Service": "worker", "ID": "w1"}, {"Service": "api", "ID": "a1"},
            {"Service": "worker", "ID": "run1", "Labels": "com.docker.compose.oneoff=True"}]
    rows += [{"Service": "processor-gpu1", "ID": "p%%d" %% i} for i in range(n1)]
    print("\n".join(json.dumps(r) for r in rows))
elif a[:1] == ["inspect"]:
    bad = a[-1] == "run1" or (a[-1] == "w1" and vals.get("WORKER_MEM_LIMIT") == "1m")
    print(json.dumps({"Status": "exited" if bad else "running"}) + "|0")
elif a[:2] == ["compose", "logs"]:
    print("cs-worker  | killed: out of memory")
'''


@pytest.fixture
def stack(tmp_path, monkeypatch):
    st = tmp_path / "stack"
    (st / "app").mkdir(parents=True)
    (st / "data").mkdir()
    (st / "docker-compose.yml").write_text("services: {}\n")
    (st / ".env").write_text(ENV_TEXT)
    os.chmod(st / ".env", 0o600)
    b = tmp_path / "bin"
    b.mkdir()
    (b / "docker").write_text(FAKE_DOCKER % sys.executable)
    os.chmod(b / "docker", 0o755)
    monkeypatch.setenv("PATH", f"{b}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(tmp_path / "docker.log"))
    clock = [1_800_000_000.0]                                # health waits 10 s of "good": a fake clock, no real sleep
    monkeypatch.setattr(UPD, "now", lambda: clock[0])
    monkeypatch.setattr(UPD.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    return st


def test_env_run_applies_and_rolls_back(stack, tmp_path):
    u = UPD.Updater(str(stack))
    u.state["result"] = {"action": "apply", "ok": True, "message": "Updated to abc"}
    u.do("env", env=UPD.validate_env({"PROCESSORS_GPU1": "2", "CS_URL_ALLOW": "10.0.0.20"}))
    r = u.state["env_result"]
    assert r["ok"] is True and "PROCESSORS_GPU1 3 -> 2" in r["message"], r
    text = (stack / ".env").read_text()
    assert "PROCESSORS_GPU1=2" in text and text.endswith(UPD.ENV_MARK + "\nCS_URL_ALLOW=10.0.0.20\n")
    assert 'TBA_KEY="secret key"' in text and stat.S_IMODE(os.stat(stack / ".env").st_mode) == 0o600
    bdir = stack / "updater" / "env-backups"
    assert [p.read_text() for p in bdir.iterdir()] == [ENV_TEXT]
    assert stat.S_IMODE(os.stat(next(bdir.iterdir())).st_mode) == 0o600
    log = (tmp_path / "docker.log").read_text()
    assert "compose up -d --no-build" in log and "inspect" in log and "p1" in log      # waited for both processors
    # the status file: allowlisted values only, the update result untouched, state back to idle
    st = json.loads((stack / "data" / "config" / "update-status.json").read_text())
    assert st["env"]["values"]["PROCESSORS_GPU1"] == "2" and "TBA_KEY" not in json.dumps(st["env"])
    assert "secret" not in json.dumps(st)
    assert st["state"] == "idle" and st["action"] is None and st["result"]["message"] == "Updated to abc"
    assert st["env_progress"] and not st["progress"] and st["features"] == ["env", "services"]
    assert U.parse_status(st, UPD.now())["env_result"]["ok"] is True

    # a change that makes the worker crash: the old .env comes back by itself
    before = (stack / ".env").read_text()
    u.do("env", env=UPD.validate_env({"WORKER_MEM_LIMIT": "1m"}))
    r = u.state["env_result"]
    assert r["ok"] is False and r["rolled_back"] is True and "keeps crashing" in r["message"], r
    assert (stack / ".env").read_text() == before
    assert any("Putting the previous .env back" in l for l in u.state["env_progress"])

    # nothing to change: no restart
    n = (tmp_path / "docker.log").read_text().count("compose up")
    u.do("env", env={"PROCESSORS_GPU1": "2"})
    assert u.state["env_result"]["ok"] is True and "Nothing to change" in u.state["env_result"]["message"]
    assert (tmp_path / "docker.log").read_text().count("compose up") == n


def test_env_run_interrupted_is_reported(stack):
    u = UPD.Updater(str(stack))
    u.conf["AUTO_CHECK_HOURS"] = 0                          # no git fetch in a test
    u.state.update(state="applying", action="env", env_prev_state="ready")
    u.tick()
    assert u.state["state"] == "ready" and u.state["env_result"]["ok"] is False
    assert "interrupted" in u.state["env_result"]["message"]
    assert u.state.get("result") is None                    # the update result isn't touched


def test_env_cli(stack, capsys):
    assert UPD.main(["--stack", str(stack), "env", "PUID=0"]) == 2
    assert "can't be changed" in capsys.readouterr().err
    assert UPD.main(["--stack", str(stack), "env", "LIVESTREAM=0", "TZ="]) == 0, (stack / "updater" / "update.log").read_text()
    v = UPD.env_values((stack / ".env").read_text())
    assert v["LIVESTREAM"] == "0" and "TZ" not in v
