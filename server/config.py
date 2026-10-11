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
