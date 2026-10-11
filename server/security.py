"""Password hashing (argon2id), session tokens, login rate limiting, atomic file writes."""
import hashlib, json, os, secrets, threading, time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_ph = PasswordHasher()   # argon2id, library defaults (RFC 9106 low-memory profile)
# verified against when the username doesn't exist, so timing doesn't reveal which usernames are real
_DUMMY = _ph.hash(secrets.token_urlsafe(16))


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw_hash: str | None, pw: str) -> tuple[bool, str | None]:
    """-> (ok, new_hash_if_rehash_needed)."""
    try:
        _ph.verify(pw_hash or _DUMMY, pw)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False, None
    if pw_hash is None:
        return False, None
    return True, (_ph.hash(pw) if _ph.check_needs_rehash(pw_hash) else None)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def consteq(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and secrets.compare_digest(a.encode(), b.encode())


class RateLimiter:
    """Sliding-window failure counter, in memory (the API runs as one process).

    Login: at most 10 failures per (ip, username) and 30 per ip in 15 minutes; a success clears the pair."""
    def __init__(self, window=900, per_pair=10, per_ip=30):
        self.window, self.per_pair, self.per_ip = window, per_pair, per_ip
        self._f: dict[tuple, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, k, now):
        v = [t for t in self._f.get(k, ()) if now - t < self.window]
        if v: self._f[k] = v
        else: self._f.pop(k, None)
        return v

    def blocked(self, ip, user) -> int:
        """Seconds until a retry is allowed (0 = not blocked)."""
        now = time.time()
        with self._lock:
            for k, lim in (((ip, user.lower()), self.per_pair), ((ip, None), self.per_ip)):
                v = self._recent(k, now)
                if len(v) >= lim:
                    return int(self.window - (now - v[0])) + 1
        return 0

    def fail(self, ip, user):
        now = time.time()
        with self._lock:
            for k in ((ip, user.lower()), (ip, None)):
                self._f.setdefault(k, []).append(now)
            if len(self._f) > 10000:                       # bound memory
                for k in list(self._f)[:5000]:
                    self._recent(k, now)

    def success(self, ip, user):
        with self._lock:
            self._f.pop((ip, user.lower()), None)

    def reset(self):
        with self._lock:
            self._f.clear()


login_limiter = RateLimiter()


def atomic_write_json(path, obj, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
    os.chmod(tmp, mode)
    os.replace(tmp, path)
