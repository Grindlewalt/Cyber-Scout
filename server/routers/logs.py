"""Live logs of the worker-side processes (Admin → Worker).  Mounted under /app/v1.  Admin only.

  GET /admin/logs?since=worker:120,recorder:40   -> {roles: [{role, alive, at, age_s, doing, doing_since, also, started,
                                                   pid, seq, lines}]}   lines = only those newer than the role's `since`

The worker, recorder and processors (live/runlog.py) each write DATA/logs/<role>.json (a ring of their last ~400 log lines, the
current activity, a heartbeat `at` refreshed every ~15 s).  The containers can write DATA, so every file is untrusted: opened
without following symlinks, size-capped, parsed as JSON and every field type-checked and clipped; anything else is ignored.
A role is `alive` while its `at` is under ALIVE_S old; roles that have been silent for over a day are left out."""
import json, os, re, stat, time

from fastapi import APIRouter, Depends, Query

from ..config import cfg
from ..deps import User, require_role

router = APIRouter(tags=["admin"])

ALIVE_S = 90                      # heartbeat older than this: stopped
FORGET_S = 86400                  # ... and a role silent this long (an old processor id) is not listed
FILE_MAX = 2_000_000
LINES_MAX = 500
TEXT_MAX = 500
ROLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
SINCE_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]{0,79}):(\d{1,12})$")


def _num(v, lo=0.0):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) < 1e13 and v >= lo else None


def _text(v, n=TEXT_MAX):
    return v[:n] if isinstance(v, str) else None


def read_role(path, role, since=0, now=None):
    """-> the cleaned role dict for DATA/logs/<role>.json, or None (missing, a symlink, too big, not JSON, wrong shape)."""
    now = now or time.time()
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > FILE_MAX: return None
        with os.fdopen(fd, "rb") as f:
            fd = None
            d = json.loads(f.read(FILE_MAX + 1))
    except (OSError, ValueError):
        return None
    finally:
        if fd is not None: os.close(fd)
    if not isinstance(d, dict): return None
    at = _num(d.get("at"))
    if at is None: return None
    seq = int(_num(d.get("seq")) or 0)
    lines = []
    raw = d.get("lines")
    for ln in (raw[-LINES_MAX:] if isinstance(raw, list) else []):
        if not isinstance(ln, dict): continue
        n, t, tx = _num(ln.get("seq")), _num(ln.get("t")), _text(ln.get("text"))
        if n is None or t is None or tx is None or n <= since: continue
        lines.append({"seq": int(n), "t": t, "text": tx})
    also = []
    for a in (d.get("also") if isinstance(d.get("also"), list) else [])[:8]:
        if isinstance(a, dict) and _text(a.get("text")) and _num(a.get("since")) is not None:
            also.append({"text": _text(a["text"], 200), "since": _num(a["since"])})
    since_doing = _num(d.get("doing_since"))
    return {"role": role, "alive": 0 <= now - at < ALIVE_S, "at": at, "age_s": max(0, round(now - at, 1)),
            "doing": _text(d.get("doing"), 200) or "", "doing_since": since_doing or None, "also": also,
            "started": _num(d.get("started")), "pid": int(_num(d.get("pid")) or 0) or None, "seq": seq, "lines": lines}


def _order(r):
    return (0 if r["role"] == "worker" else 1 if r["role"] == "recorder" else 2, r["role"])


@router.get("/admin/logs", summary="Live log + current activity of the worker, recorder and processors (admin)")
def get_logs(since: str = Query("", max_length=600, description="role:seq pairs, comma separated: only newer lines"),
             user: User = Depends(require_role("admin"))):
    want = {}
    for part in since.split(","):
        m = SINCE_RE.match(part.strip())
        if m: want[m.group(1)] = int(m.group(2))
    d = os.path.join(cfg.DATA, "logs")
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith(".json") and ROLE_RE.match(n[:-5]))[:40]
    except OSError:
        names = []
    now, out = time.time(), []
    for n in names:
        role = n[:-5]
        r = read_role(os.path.join(d, n), role, want.get(role, 0), now)
        if r is None: continue
        if not r["alive"] and r["age_s"] > FORGET_S and role not in ("worker", "recorder"): continue
        out.append(r)
    return {"roles": sorted(out, key=_order)}
