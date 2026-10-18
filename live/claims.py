"""Per-match processing claims and the processing order: one processor per match, shared by live/worker.py (the
server's own worker), tools/helper.py (ssh helper) and the api (server/routers/processing.py: Cyber-Scout Client
"Help process matches").  Stdlib only: the api image has no numpy.

  events/<ev>/claims/<key>.json   {"by": <processor id>, "at": <unix s>}, created with O_EXCL.  A claim older than
                                  CLAIM_S is stale (its processor is gone) and is taken over; a processor working on
                                  a long job refreshes "at" (heartbeat).
  events/<ev>/priority.json       {"order": [match keys]}: those first, in that order (tools/priority.py); the rest
                                  keep their order.

Same rules as the functions of the same names that live/worker.py had inline (prioritized, claimed_elsewhere, claim,
release), with the data dir passed in; plus heartbeat() and one fix: a claim file that can't be parsed yet (another
processor is between O_EXCL and its write) counts as held for a few seconds instead of being deleted."""
import json, os, time

CLAIM_S = float(os.environ.get("CLAIM_S", "1800"))      # a claim older than this is stale (processor gone)
UNREADABLE_S = 10.0                                      # a just-created, not yet written claim file is held this long


def ev_dir(data, ev): return os.path.join(data, "events", ev)


def claim_path(data, ev, key): return os.path.join(ev_dir(data, ev), "claims", f"{key}.json")


def read(data, ev, key):
    """The claim of a match: {"by", "at"} | None (no claim).  An unparseable file -> {"by": None, "at": its mtime}."""
    p = claim_path(data, ev, key)
    try:
        with open(p) as f:
            c = json.load(f)
        if isinstance(c, dict):
            return c
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        pass
    try:
        return {"by": None, "at": os.path.getmtime(p), "unreadable": True}
    except OSError:
        return None


def _age(c, now=None):
    try:
        return (now or time.time()) - float(c.get("at") or 0)
    except (TypeError, ValueError):
        return float("inf")


def is_stale(c, now=None, claim_s=None):
    lim = UNREADABLE_S if c.get("unreadable") else (CLAIM_S if claim_s is None else claim_s)
    return _age(c, now) >= lim


def claimed_elsewhere(data, ev, key, by, now=None, claim_s=None):
    """Someone other than `by` holds a live (not stale) claim on the match."""
    c = read(data, ev, key)
    return bool(c) and (c.get("by") != by or c.get("unreadable")) and not is_stale(c, now, claim_s)


def holds(data, ev, key, by, now=None, claim_s=None):
    """`by` holds a live claim on the match."""
    c = read(data, ev, key)
    return bool(c) and not c.get("unreadable") and c.get("by") == by and not is_stale(c, now, claim_s)


def claim(data, ev, key, by, claim_s=None):
    """-> True if `by` holds the claim now (new, its own from before, or taken over from a stale one)."""
    p = claim_path(data, ev, key)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            with os.fdopen(fd, "w") as f:
                json.dump(dict(by=by, at=time.time()), f)
            return True
        except FileExistsError:
            if claimed_elsewhere(data, ev, key, by, claim_s=claim_s):
                return False
            try:
                os.remove(p)                               # ours from before, or stale
            except OSError:
                pass
    return False


def heartbeat(data, ev, key, by, claim_s=None):
    """Refresh "at" of a live claim `by` holds (atomic replace) -> True; False = the claim is gone / stale / someone
    else's (stop working on the match)."""
    if not holds(data, ev, key, by, claim_s=claim_s):
        return False
    p = claim_path(data, ev, key)
    tmp = f"{p}.hb{os.getpid()}"
    try:
        with open(tmp, "w") as f:
            json.dump(dict(by=by, at=time.time()), f)
        os.replace(tmp, p)
        return True
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def release(data, ev, key, by):
    """Remove the claim if `by` holds it (or it names nobody).  -> True if a claim file was removed."""
    c = read(data, ev, key)
    if c is not None and not c.get("unreadable") and c.get("by") in (None, by):
        try:
            os.remove(claim_path(data, ev, key))
            return True
        except OSError:
            pass
    return False


# ------------------------------------------------------------------ order
def priority_order(data, ev):
    try:
        with open(os.path.join(ev_dir(data, ev), "priority.json")) as f:
            o = (json.load(f) or {}).get("order") or []
        return [k for k in o if isinstance(k, str)]
    except (OSError, ValueError, AttributeError):
        return []


def prioritized(data, ev, ms):
    """priority.json's matches first, in that order; the rest keep their order (stable).  No file = unchanged."""
    rank = {k: i for i, k in enumerate(priority_order(data, ev))}
    return sorted(ms, key=lambda m: rank.get(m["key"], len(rank)))


_LEVEL = {"qm": 0, "ef": 1, "qf": 2, "sf": 3, "f": 4}


def time_order(ms):
    """The worker's base order (live/worker.py sync): by actual/scheduled time, then level / set / number."""
    return sorted(ms, key=lambda m: (m.get("actual_time") or m.get("time") or 0, _LEVEL.get(m.get("comp_level"), 9),
                                     m.get("set_number") or 0, m.get("match_number") or 0))


def order(data, ev, ms):
    """The order processors work down an event: time order, then priority.json first."""
    return prioritized(data, ev, time_order(ms))
