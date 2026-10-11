"""Minimal The Blue Alliance v3 client with ETag caching (polite polling)."""
import json, os, time, urllib.request, urllib.error

BASE = "https://www.thebluealliance.com/api/v3"


class TBA:
    def __init__(self, key, cache_dir):
        self.key = key
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def _path(self, ep):
        return os.path.join(self.cache_dir, ep.strip("/").replace("/", "__") + ".json")

    def get(self, ep, max_age=0):
        """GET with If-None-Match.  Returns cached data on 304 or network error."""
        p = self._path(ep)
        cached = json.load(open(p)) if os.path.exists(p) else None
        if cached and max_age and time.time() - cached.get("fetched", 0) < max_age:
            return cached["data"]
        req = urllib.request.Request(BASE + ep, headers={"X-TBA-Auth-Key": self.key,
                                                         "User-Agent": "autoscout-live"})
        if cached and cached.get("etag"):
            req.add_header("If-None-Match", cached["etag"])
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
                json.dump({"etag": r.headers.get("ETag"), "fetched": time.time(), "data": data}, open(p, "w"))
                return data
        except urllib.error.HTTPError as e:
            if e.code == 304 and cached:
                cached["fetched"] = time.time(); json.dump(cached, open(p, "w"))
                return cached["data"]
            if cached: return cached["data"]
            raise
        except (urllib.error.URLError, TimeoutError):
            if cached: return cached["data"]
            raise

    def event(self, key): return self.get(f"/event/{key}", max_age=3600)
    def matches(self, key): return self.get(f"/event/{key}/matches")
    def teams(self, key): return self.get(f"/event/{key}/teams/simple", max_age=3600)
    def rankings(self, key): return self.get(f"/event/{key}/rankings", max_age=120)
    def oprs(self, key): return self.get(f"/event/{key}/oprs", max_age=120)


def match_label(m):
    lv = m["comp_level"]
    if lv == "qm": return f"Qualification {m['match_number']}"
    if lv == "f": return f"Final {m['match_number']}"
    if lv == "sf": return f"Playoff Match {m['set_number']}"
    return f"{lv.upper()} {m['set_number']}-{m['match_number']}"


def short_label(m):
    lv = m["comp_level"]
    if lv == "qm": return f"Q{m['match_number']}"
    if lv == "sf": return f"SF{m['set_number']}"
    if lv == "f": return f"F{m['match_number']}"
    return m["key"].split("_")[1].upper()


def sort_key(m):
    order = {"qm": 0, "ef": 1, "qf": 2, "sf": 3, "f": 4}
    return (order.get(m["comp_level"], 9), m["set_number"], m["match_number"])
