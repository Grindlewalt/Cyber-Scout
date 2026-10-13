"""Runtime configuration from the environment (infra only; everything else lives in the DB).

Read through `cfg` at call time (tests point it at a throwaway data dir with `cfg.configure(data=...)`)."""
import os


class Config:
    def __init__(self):
        self.configure()

    def configure(self, data=None, **over):
        e = os.environ.get
        self.DATA = data or e("DATA", "/data")
        self.DB_PATH = os.path.join(self.DATA, "app.db")
        self.WORKER_CONF = os.path.join(self.DATA, "config", "worker.json")   # read by live/worker.py every poll
        self.SETUP_CODE_FILE = os.path.join(self.DATA, "setup-code.txt")      # one-time first-run code
        self.SETUP_CODE_REQUIRED = e("SETUP_CODE_REQUIRED", "1") != "0"
        self.SESSION_IDLE_DAYS = float(e("SESSION_IDLE_DAYS", "7"))      # signed out after a week unused
        self.SESSION_MAX_DAYS = float(e("SESSION_MAX_DAYS", "30"))       # ... and after 30 days regardless
        self.COOKIE_SECURE = e("COOKIE_SECURE", "auto")                  # auto: Secure iff X-Forwarded-Proto=https
        self.PASSWORD_MIN = int(e("PASSWORD_MIN", "10"))                 # admins
        self.MEMBER_PASSWORD_MIN = int(e("MEMBER_PASSWORD_MIN", "4"))    # everyone else: optional, but this long if set
        self.TBA_BASE = e("TBA_BASE", "https://www.thebluealliance.com/api/v3")
        # legacy .env values, imported into the DB once on first start (upgrade path for existing stacks)
        self.SEED_TBA_KEY = e("TBA_KEY", "")
        self.SEED_EVENTS = e("EVENTS", "")
        # client sync (server/routers/sync.py, docs/SYNC.md)
        self.SITE = os.path.join(self.DATA, "site")                       # site/api/<ev>/... = what nginx serves at /api/
        self.MEDIA = e("MEDIA") or os.path.join(self.DATA, "media")      # kept videos (compose mounts MEDIA_DIR read-only)
        self.SYNC_CACHE_DIR = e("SYNC_CACHE_DIR") or os.path.join(self.DATA, "cache", "sync")
        self.SYNC_CACHE_MB = float(e("SYNC_CACHE_MB", "256"))            # bundle cache budget (oldest first over it)
        self.SYNC_BUNDLE_MAX = int(float(e("SYNC_BUNDLE_MAX_MB", "64")) * 1024 * 1024)
        self.SERVER_NAME = e("SERVER_NAME", "")                          # /info display name (else team settings)
        self.VERSION = e("CS_VERSION", "")                               # else the VERSION file written at build time
        for k, v in over.items():
            setattr(self, k, v)
        return self


cfg = Config()

SESSION_COOKIE = "cs_session"
CSRF_COOKIE = "cs_csrf"
CSRF_HEADER = "x-csrf-token"
PREFIX = "/app/v1"
ROLES = ("viewer", "scout", "strategist", "admin")          # ascending privilege
RANK = {r: i for i, r in enumerate(ROLES)}
