"""Tiny server-side TBA client: the key never leaves the server. Short in-memory cache."""
import json, secrets, threading, time, urllib.error, urllib.request

from .config import cfg


class TBAError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg); self.status = status


_cache: dict[tuple, tuple[float, object]] = {}
_lock = threading.Lock()


def get(key: str, path: str, ttl: float = 600):
    """GET {TBA_BASE}{path} with the key. Raises TBAError(401) for a bad key, TBAError(404) etc."""
    ck = (key[-8:] if key else "", path)       # cache per key (suffix is enough to separate keys in memory)
    now = time.time()
    with _lock:
        hit = _cache.get(ck)
        if hit and now - hit[0] < ttl:
            return hit[1]
    req = urllib.request.Request(cfg.TBA_BASE + path, headers={"X-TBA-Auth-Key": key, "User-Agent": "cyber-scout"})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise TBAError(e.code, f"TBA returned HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TBAError(502, "could not reach The Blue Alliance") from None
    if not ttl:
        return data
    with _lock:
        _cache[ck] = (now, data)
        if len(_cache) > 500:
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:250]:
                _cache.pop(k, None)
    return data


def validate_key(key: str) -> dict:
    """-> TBA /status (raises TBAError(401) when the key is rejected). The query string defeats TBA's CDN cache:
    a cached /status is served without checking the key."""
    return get(key, f"/status?cs={secrets.token_hex(6)}", ttl=0)


def clear_cache():
    with _lock:
        _cache.clear()
