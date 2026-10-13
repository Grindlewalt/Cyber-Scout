import os, sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi.testclient import TestClient  # noqa: E402

from server import tba  # noqa: E402
from server.config import cfg  # noqa: E402
from server.main import create_app  # noqa: E402
from server.security import login_limiter, write_limiter  # noqa: E402

LAN = ("10.0.0.23", 50000)          # test clients connect like a phone on the shop Wi-Fi (direct, private address)

GOOD_KEY = "k" * 60
FAKE_TBA = {
    "/status": {"current_season": 2026},
    "/team/frc4911/simple": {"key": "frc4911", "team_number": 4911, "nickname": "CyberKnights", "city": "Seattle"},
    "/events/2026/simple": [
        {"key": "2026wasam", "name": "PNW District Sammamish Event", "year": 2026, "start_date": "2026-03-06",
         "district": {"abbreviation": "pnw"}},
        {"key": "2026cmptx", "name": "Championship", "year": 2026, "start_date": "2026-04-15"}],
    "/team/frc4911/events/2026/simple": [
        {"key": "2026wasam", "name": "PNW District Sammamish Event", "year": 2026, "start_date": "2026-03-06"}],
    "/event/2026wasam/simple": {"key": "2026wasam", "name": "PNW District Sammamish Event", "year": 2026,
                                "start_date": "2026-03-06"},
}


def fake_get(key, path, ttl=600):
    if key != GOOD_KEY:
        raise tba.TBAError(401, "bad key")
    path = path.split("?")[0]
    if path not in FAKE_TBA:
        raise tba.TBAError(404, "nope")
    return FAKE_TBA[path]


class C:
    """TestClient wrapper that echoes the cs_csrf cookie as X-CSRF-Token (like web/setup.js does)."""
    def __init__(self, tc):
        self.tc = tc

    def _h(self, csrf=True):
        tok = self.tc.cookies.get("cs_csrf")
        return {"X-CSRF-Token": tok} if (csrf and tok) else {}

    def get(self, p, **kw): return self.tc.get("/app/v1" + p, **kw)
    def post(self, p, json=None, csrf=True, **kw): return self.tc.post("/app/v1" + p, json=json, headers=self._h(csrf), **kw)
    def put(self, p, json=None, csrf=True): return self.tc.put("/app/v1" + p, json=json, headers=self._h(csrf))
    def patch(self, p, json=None, csrf=True): return self.tc.patch("/app/v1" + p, json=json, headers=self._h(csrf))
    def delete(self, p, csrf=True): return self.tc.delete("/app/v1" + p, headers=self._h(csrf))

    def login(self, u, pw):
        self.get("/session")
        return self.post("/auth/login", {"username": u, "password": pw})


@pytest.fixture
def data(tmp_path, monkeypatch):
    for k in ("TBA_KEY", "EVENTS", "SETUP_CODE_REQUIRED"):
        monkeypatch.delenv(k, raising=False)
    cfg.configure(data=str(tmp_path))
    login_limiter.reset()
    write_limiter.reset()
    tba.clear_cache()
    monkeypatch.setattr(tba, "get", fake_get)
    yield tmp_path
    cfg.configure()


@pytest.fixture
def app(data):
    return create_app()


def new_client(app, client=LAN, headers=None):
    return C(TestClient(app, base_url="http://testserver", client=client, headers=headers))


@pytest.fixture
def client(app):
    with TestClient(app, base_url="http://testserver", client=LAN) as tc:
        yield C(tc)


@pytest.fixture
def admin(client, data):
    """Setup done: admin 'coach' / 'correct horse 1' signed in on `client`."""
    client.get("/session")
    code = (data / "setup-code.txt").read_text().strip()
    r = client.post("/setup", {"setup_code": code, "username": "coach", "password": "correct horse 1",
                               "display_name": "Coach"})
    assert r.status_code == 200, r.text
    return client


@pytest.fixture
def team(admin, app):
    """Admin plus one signed-in client per role -> dict role -> C."""
    out = {"admin": admin}
    for role in ("strategist", "scout", "viewer"):
        r = admin.post("/users", {"username": role + "1", "password": "password-" + role, "role": role})
        assert r.status_code == 201, r.text
        c = C(TestClient(app, base_url="http://testserver", client=LAN))
        assert c.login(role + "1", "password-" + role).status_code == 200
        out[role] = c
    return out
