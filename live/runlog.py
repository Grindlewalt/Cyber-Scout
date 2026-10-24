"""Live log of one worker-side process, for Admin -> Worker (the app shows what the worker is doing without `docker logs`).

A per-process ring buffer of the last LINES log lines (each <= LINE_MAX chars, with a sequence number and a unix time) and the
current activity ("doing"), written atomically to DATA/logs/<role>.json at most once a second (right away when the activity
changes) by a small daemon thread:

  {role, pid, started, at, doing, doing_since, also: [{text, since}], seq, lines: [{seq, t, text}]}

role = "worker", "recorder" or "processor-<id>".  `at` is refreshed every HEART_S even when nothing is logged (the process is
alive), so the app tells "alive but quiet" from "stopped".  `also` = other threads' activities (the Video-page job thread).
The api reads these files (server/routers/logs.py: GET /admin/logs).

start(data, role, secrets=()) tees sys.stdout / sys.stderr into the buffer (everything the process prints, tracebacks and other
modules' prints included; the original streams still get it, so `docker logs` is unchanged).  Secrets never reach the file:
redact() runs on every line (the TBA key given to add_secret, key=/token=/sig=/auth=/password= values, Bearer tokens, Discord
webhook URLs, long opaque tokens; match keys, YouTube / Twitch ids and paths stay)."""
import collections, json, os, re, sys, threading, time

LINES = 400                    # ring size
LINE_MAX = 400                 # chars per line
WRITE_S = 1.0                  # at most one file write per second
HEART_S = 15.0                 # ... and at least one this often (alive, nothing new)
MAIN = "main"

# ---------------------------------------------------------------- redaction
_SECRETS = set()               # exact strings (the TBA key), >= 8 chars
_PARAM = re.compile(r"(?i)(?<![A-Za-z0-9_])((?:api[_-]?|access[_-]?|auth[_-]?|x-tba-auth)?(?:key|token|sig|signature|auth|authkey|password|passwd|secret))(\s*[=:]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s&\"',;)\]}]+)")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{6,}")
_WEBHOOK = re.compile(r"(?i)https?://[a-z0-9.-]*discord(?:app)?\.com/api/(?:v\d+/)?webhooks/\S+")
_OPAQUE = re.compile(r"(?<![A-Za-z0-9_+=-])[A-Za-z0-9_+=-]{32,}(?![A-Za-z0-9_+=-])")
_URLUSER = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@")


def add_secret(s):
    """Register an exact secret (the TBA key) so it is blanked wherever it appears."""
    s = str(s or "").strip()
    if len(s) >= 8: _SECRETS.add(s)


def _opaque(m):
    t = m.group(0)
    if not (re.search(r"\d", t) and re.search(r"[A-Za-z]", t)): return t      # a long word / number is not a token
    if len(re.findall(r"[-_]", t)) >= 3: return t                             # words joined by - or _ (a file or key name)
    return "[redacted]"


def redact(s):
    s = str(s)
    for x in _SECRETS:
        if x in s: s = s.replace(x, "[redacted]")
    s = _WEBHOOK.sub("[webhook redacted]", s)
    s = _URLUSER.sub(r"\1[redacted]@", s)
    s = _BEARER.sub(lambda m: m.group(1) + " [redacted]", s)
    s = _PARAM.sub(lambda m: m.group(1) + m.group(2) + "[redacted]", s)
    return _OPAQUE.sub(_opaque, s)


# ---------------------------------------------------------------- the buffer
class _State:
    def __init__(self):
        self.lock = threading.RLock()
        self.buf = collections.deque(maxlen=LINES)
        self.seq = 0
        self.role = None
        self.path = None
        self.started = time.time()
        self.doing = {MAIN: ("", 0.0)}          # thread key -> (text, since)
        self.dirty = False
        self.urgent = False
        self.wrote = 0.0
        self.thread = None


S = _State()


def add(text, now=None):
    """One log line (or several: split on newlines) into the ring."""
    now = now or time.time()
    with S.lock:
        for ln in str(text).split("\n"):
            ln = ln.rstrip("\r")
            if "\r" in ln: ln = ln.rsplit("\r", 1)[-1]           # progress bars: the last redraw only
            if not ln.strip(): continue
            ln = redact(ln)
            if len(ln) > LINE_MAX: ln = ln[:LINE_MAX - 1] + "…"
            S.seq += 1
            S.buf.append({"seq": S.seq, "t": round(now, 2), "text": ln})
            S.dirty = True


def doing(text, key=MAIN):
    """The current activity ("tracking Q3 (livestream clip)").  key: another thread's own slot (shown as "also");
    text "" clears it.  Written to the file right away when it changes."""
    text = redact(str(text or ""))[:200]
    with S.lock:
        old = S.doing.get(key, ("", 0.0))
        if old[0] == text: return
        if text: S.doing[key] = (text, time.time())
        elif key != MAIN: S.doing.pop(key, None)
        else: S.doing[MAIN] = ("", 0.0)
        S.dirty = S.urgent = True


def snapshot():
    with S.lock:
        main = S.doing.get(MAIN, ("", 0.0))
        return {"role": S.role, "pid": os.getpid(), "started": round(S.started, 1), "at": round(time.time(), 1),
                "doing": main[0], "doing_since": round(main[1], 1) if main[0] else None,
                "also": [{"text": t, "since": round(s, 1)} for k, (t, s) in S.doing.items() if k != MAIN and t],
                "seq": S.seq, "lines": list(S.buf)}


def flush():
    """Write the file now (atomic: temp + replace)."""
    if not S.path: return
    try:
        snap = snapshot()
        tmp = S.path + ".tmp"
        with open(tmp, "w") as f: json.dump(snap, f, separators=(",", ":"))
        os.replace(tmp, S.path)
        S.wrote = time.time(); S.dirty = S.urgent = False
    except OSError:
        pass                                                      # a full or read-only disk must never stop the worker


def _writer():
    while True:
        time.sleep(0.25)
        now = time.time()
        if now - S.wrote < (0.2 if S.urgent else WRITE_S) and now - S.wrote < HEART_S: continue
        if S.dirty or now - S.wrote >= HEART_S: flush()


class _Tee:
    """File-like in front of sys.stdout / sys.stderr: everything goes on to the real stream and into the ring."""
    def __init__(self, real):
        self.real, self.part, self.lk = real, "", threading.Lock()

    def write(self, s):
        n = self.real.write(s)
        try:
            if isinstance(s, bytes): s = s.decode("utf-8", "replace")
            with self.lk:
                self.part += s
                if "\n" in self.part:
                    done, _, self.part = self.part.rpartition("\n")
                    add(done)
                elif len(self.part) > 4000:
                    add(self.part); self.part = ""
        except Exception:
            pass
        return n

    def flush(self): return self.real.flush()
    def __getattr__(self, k): return getattr(self.real, k)


def start(data, role, secrets=()):
    """Begin logging this process as `role` into DATA/logs/<role>.json and tee stdout / stderr.  Safe to call again
    (a processor learns its id after starting): the role and file change, the buffer stays."""
    role = re.sub(r"[^A-Za-z0-9._-]", "_", str(role or "worker"))[:80] or "worker"
    for s in secrets: add_secret(s)
    with S.lock:
        S.role = role
        try:
            d = os.path.join(data, "logs")
            os.makedirs(d, exist_ok=True)
            S.path = os.path.join(d, role + ".json")
        except OSError:
            S.path = None
        if not isinstance(sys.stdout, _Tee): sys.stdout = _Tee(sys.stdout)
        if not isinstance(sys.stderr, _Tee): sys.stderr = _Tee(sys.stderr)
        if S.thread is None:
            S.thread = threading.Thread(target=_writer, name="runlog", daemon=True); S.thread.start()
        S.dirty = S.urgent = True
