"""Cyber-Scout platform API.  Run: `uvicorn server.main:app --host 0.0.0.0 --port 8000` (ONE worker process:
the login and write rate limiters are in memory).  OpenAPI: /app/v1/openapi.json (admins only; no Swagger UI)."""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from . import audit, db as DB, workerconf
from .config import cfg, PREFIX, CSRF_COOKIE, CSRF_HEADER
from .deps import EVENT_RE, User, load_session, require_role
from .routers import admin, auth, events, scouting, settings, sources, update, users
from .security import consteq

log = logging.getLogger("cyberscout.api")
VERSION = "1.0.0"


def seed_from_env(db):
    """Upgrade path for stacks configured through .env: import TBA_KEY / EVENTS once into an empty DB."""
    if DB.get_setting(db, "seeded_from_env"):
        return
    with DB.tx(db):
        if cfg.SEED_TBA_KEY and not DB.get_setting(db, "tba_key"):
            DB.set_setting(db, "tba_key", cfg.SEED_TBA_KEY.strip())
            log.info("imported the TBA key from the environment")
        evs = [e.strip().lower() for e in cfg.SEED_EVENTS.split(",") if EVENT_RE.match(e.strip().lower())]
        if evs and not db.execute("SELECT 1 FROM events LIMIT 1").fetchone():
            import time
            for e in evs:
                db.execute("INSERT OR IGNORE INTO events(key,year,added_at) VALUES(?,?,?)", (e, int(e[:4]), time.time()))
            DB.set_setting(db, "events_configured", True)
            log.info("imported followed events from the environment: %s", ", ".join(evs))
        DB.set_setting(db, "seeded_from_env", True)


def startup():
    DB.init()
    c = DB.connect()
    try:
        seed_from_env(c)
        auth.ensure_setup_code(c)
        workerconf.write(c)
        if not DB.user_count(c):
            log.warning("no accounts yet: open the site to run first-time setup%s",
                        " (setup code: data/setup-code.txt on the server)" if cfg.SETUP_CODE_REQUIRED else "")
    finally:
        c.close()


@asynccontextmanager
async def lifespan(app):
    startup()
    yield


def create_app() -> FastAPI:
    # no public Swagger UI / schema (security audit): the schema is served to admins only, below
    app = FastAPI(title="Cyber-Scout API", version=VERSION, lifespan=lifespan,
                  openapi_url=None, docs_url=None, redoc_url=None,
                  description="Accounts, roles, settings and shared scouting data. Cookie session auth; "
                              "send X-CSRF-Token (= cs_csrf cookie) on mutating requests. Roles: viewer < scout < "
                              "strategist < admin. Site JSON (/api/*) and media (/media/*) are served by nginx, "
                              "gated by GET /app/v1/auth/check.")

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # Safety net: every mutating API call must carry the double-submit CSRF token (routes also check the
        # session-bound token via require_role).
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith(PREFIX):
            if not consteq(request.headers.get(CSRF_HEADER), request.cookies.get(CSRF_COOKIE)):
                return JSONResponse({"detail": "missing or bad CSRF token (send the cs_csrf cookie value as X-CSRF-Token)"},
                                    status_code=403)
        resp = await call_next(request)
        resp.headers.setdefault("Cache-Control", "no-store")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if (request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith(PREFIX)
                and resp.status_code < 400 and not getattr(request.state, "audited", False)):
            _fallback_audit(request, resp.status_code)
        return resp

    def _fallback_audit(request: Request, status: int):
        """Routers without explicit audit records (e.g. routers/sources.py) still leave a trace: who, what path."""
        c = DB.connect()
        try:
            u = getattr(request.state, "user", None) or load_session(c, request)
            if u:
                audit.record(c, request, u, "http." + request.method.lower(), request.url.path[:300], None, None,
                             {"status": status})
        except Exception:                        # never fail a request because of the fallback log
            log.exception("fallback audit failed")
        finally:
            c.close()

    @app.get(PREFIX + "/openapi.json", tags=["meta"], include_in_schema=False)
    def openapi_admin(user: User = Depends(require_role("admin"))):
        return app.openapi()

    @app.get(PREFIX + "/health", tags=["meta"], summary="Liveness (no auth)")
    def health():
        return {"ok": True, "version": VERSION}

    for r in (auth.router, users.router, settings.router, events.router, scouting.router, sources.router, admin.router,
              update.router):
        app.include_router(r, prefix=PREFIX)
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
