"""EPA: the prior for the strategy model, and the EPA column of the dashboard.

Two different things come out of here (do not mix them up):

1. PRE-EVENT component prior (`pre_event_prior`) -- what we believed about every team *before the event started*:
   per robot and per FUEL component (auto / trans / active / endgame, schedule-neutral counted FUEL per match), a
   mean and sd, plus climb pseudo-counts.  This is the prior mean/uncertainty that model.fit_event_model starts
   from; the event's TBA results and Cyber-Scout video update it.  It must contain NO information from the event
   itself (otherwise the event's TBA rows would be counted twice), so it comes from:
     a) "local": the same hierarchical model (model.fit_event_model, TBA only) fitted to each of the team's earlier
        events this season (TBA results, fetched once and cached in DATA_DIR/cache/tba_prior/; needs TBA_KEY), or
     b) "statbotics": Statbotics' pre-event EPA (team_event.epa.stats.start), mapped onto the components with the
        event-level component shares (approximate; used only for teams without a local prior).
   Between-event drift (robots change from week to week) is added to the prior variance: drift sd = 0.5 x tau of the
   prior event (week-to-week correlation of true strength ~0.8).  Fixed a priori, not tuned on the backtest.

2. AS-OF EPA for display (`epa_asof`) -- {total, auto, teleop, endgame} in points, as it was at `asof`:
     a) Statbotics: last team_match post-EPA before asof (else team_event start EPA, else current team_event EPA);
     b) local: Statbotics-style running EPA (baselines.EPA update rule) per component, started from the team's
        prior-event EPA (or the field mean) and updated through this event's results posted before asof.

Network: public Statbotics v3 API (no key) and TBA v3 (TBA_KEY env).  IPv4 only, >= 0.25 s between requests,
responses cached under DATA_DIR/cache/{statbotics,tba_prior}/; failures fall back to the cache, then to "local".
`offline=True` never touches the network.
"""
import contextlib
import json
import math
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np

from .data import COMPONENTS, SHIFTS, TOWER_AUTO_PTS, TOWER_TELEOP_PTS

SB_BASE = "https://api.statbotics.io/v3"
TBA_BASE = "https://www.thebluealliance.com/api/v3"
UA = "cyber-scout strategy dashboard (FRC 4911; github.com/samdev-7/autoscout fork)"
MIN_INTERVAL = 0.25
DRIFT_FRAC = 0.5            # between-event drift sd as a fraction of the prior event's tau
PRIOR_CLIMB_W = 0.5         # one prior-event match counts as this many matches of climb evidence
OFFICIAL_TYPES = {0, 1, 2, 3, 4, 5}     # regional, district, dcmp, cmp division, cmp finals, dcmp division
_last_req = [0.0]


# ------------------------------------------------------------------------------------------------- HTTP + cache
@contextlib.contextmanager
def _ipv4():
    orig = socket.getaddrinfo

    def v4(host, *a, **k):
        res = orig(host, *a, **k)
        r4 = [r for r in res if r[0] == socket.AF_INET]
        return r4 or res
    socket.getaddrinfo = v4
    try:
        yield
    finally:
        socket.getaddrinfo = orig


def _cache_path(cache_dir, ep):
    safe = ep.strip("/").replace("/", "__").replace("?", "__").replace("&", "_").replace("=", "-")
    return os.path.join(cache_dir, safe + ".json")


def cached_get(url, cache_dir, ep, headers=None, max_age=600, offline=False, valid=None, log=None):
    """GET url with a JSON file cache.  Returns (data, info) where info = {"from": "net"|"cache"|None, "fetched", "error"}.
    A response that fails `valid` (e.g. Statbotics' empty {} during outages) is not cached and counts as an error."""
    p = _cache_path(cache_dir, ep)
    cached = None
    if os.path.exists(p):
        try:
            cached = json.load(open(p))
        except Exception:
            cached = None
    if cached is not None and (offline or (max_age is not None and time.time() - cached.get("fetched", 0) < max_age)):
        return cached["data"], {"from": "cache", "fetched": cached.get("fetched"), "error": None}
    if offline:
        return None, {"from": None, "fetched": None, "error": "offline, not cached"}
    err = None
    try:
        dt = time.time() - _last_req[0]
        if dt < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - dt)
        _last_req[0] = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json", **(headers or {})})
        with _ipv4(), urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
        if valid is not None and not valid(data):
            raise ValueError(f"empty/invalid response from {url}")
        os.makedirs(cache_dir, exist_ok=True)
        tmp = p + ".tmp"
        json.dump({"fetched": time.time(), "url": url, "data": data}, open(tmp, "w"))
        os.replace(tmp, p)
        return data, {"from": "net", "fetched": time.time(), "error": None}
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError, json.JSONDecodeError) as e:
        err = f"{type(e).__name__}: {e}"
        if log:
            log(f"[epa] {url}: {err}")
    if cached is not None:
        return cached["data"], {"from": "cache", "fetched": cached.get("fetched"), "error": err}
    return None, {"from": None, "fetched": None, "error": err}


# ------------------------------------------------------------------------------------------------- Statbotics
def _nonempty(d):
    return bool(d) and not (isinstance(d, dict) and set(d.keys()) <= {"detail"})


def fetch_statbotics(data_dir, event, max_age=600, offline=False, log=None):
    """team_events + team_matches for the event (2-3 requests).  -> {"team_events": {team: rec}, "team_matches":
    {team: [rec sorted by time]}, "info": {...}} with empty dicts when unavailable."""
    cdir = os.path.join(data_dir, "cache", "statbotics")
    out = {"team_events": {}, "team_matches": {}, "info": {}}
    te, info = cached_get(f"{SB_BASE}/team_events?event={event}&limit=1000", cdir, f"team_events_{event}",
                          max_age=max_age, offline=offline, valid=_nonempty, log=log)
    out["info"]["team_events"] = info
    for r in te or []:
        t = r.get("team")
        if t is not None:
            out["team_events"][f"frc{t}" if not str(t).startswith("frc") else str(t)] = r
    tms, off = [], 0
    while True:
        tm, info = cached_get(f"{SB_BASE}/team_matches?event={event}&limit=1000&offset={off}", cdir,
                              f"team_matches_{event}_{off}", max_age=max_age, offline=offline, valid=_nonempty, log=log)
        out["info"][f"team_matches_{off}"] = info
        if not tm:
            break
        tms += tm
        if len(tm) < 1000:
            break
        off += 1000
    for r in tms:
        t = r.get("team")
        if t is None:
            continue
        out["team_matches"].setdefault(f"frc{t}" if not str(t).startswith("frc") else str(t), []).append(r)
    for v in out["team_matches"].values():
        v.sort(key=lambda r: (r.get("time") or 0))
    out["ok"] = bool(out["team_events"])
    return out


def _sb_num(x):
    if isinstance(x, dict):
        x = x.get("mean", x.get("total_points"))
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def _sb_breakdown(epa):
    """Statbotics epa dict -> {total, auto, teleop, endgame, sd} in points (keys are tolerant to schema drift)."""
    if not isinstance(epa, dict):
        return None
    bd = epa.get("breakdown") or {}
    tot = _sb_num(epa.get("total_points")) if epa.get("total_points") is not None else _sb_num(bd.get("total_points"))
    if tot is None:
        tot = _sb_num(epa.get("post")) if epa.get("post") is not None else None
    if tot is None:
        return None
    sd = None
    if isinstance(epa.get("total_points"), dict):
        sd = _sb_num(epa["total_points"].get("sd"))
    return {"total": tot, "auto": _sb_num(bd.get("auto_points")), "teleop": _sb_num(bd.get("teleop_points")),
            "endgame": _sb_num(bd.get("endgame_points")), "sd": sd}


def sb_asof(sb, team, asof):
    """Statbotics EPA for `team` as of `asof` (None = latest)."""
    te = sb["team_events"].get(team)
    tms = sb["team_matches"].get(team) or []
    if asof is not None:
        before = [r for r in tms if (r.get("time") or 0) and r["time"] + 300 <= asof and r.get("status", "Completed") != "Upcoming"]
        if before:
            e = before[-1].get("epa") or {}
            post = e.get("post")
            if post is not None:
                b = _sb_breakdown({"total_points": post, "breakdown": e.get("post_breakdown") or {}})
                if b:
                    return b
        if te:      # nothing played yet: the pre-event EPA
            st = ((te.get("epa") or {}).get("stats") or {}).get("start")
            if st is not None:
                return {"total": float(st), "auto": None, "teleop": None, "endgame": None, "sd": None}
        return None
    if te:
        return _sb_breakdown(te.get("epa"))
    return None


# ------------------------------------------------------------------------------------------------- TBA prior events
def _tba_key():
    return os.environ.get("TBA_KEY") or os.environ.get("X_TBA_AUTH_KEY")


def fetch_prior_events(data_dir, event, teams, start_date, year, offline=False, log=None):
    """{event_key: raw TBA match list} for official events that ended before `start_date`, played by any of `teams`;
    plus {team: [event keys]}.  Cached under DATA_DIR/cache/tba_prior/ (team lists for a day, finished events forever)."""
    cdir = os.path.join(data_dir, "cache", "tba_prior")
    key = _tba_key()
    hdr = {"X-TBA-Auth-Key": key} if key else {}
    can_net = bool(key) and not offline
    team_events = {}
    for t in teams:
        evs, _ = cached_get(f"{TBA_BASE}/team/{t}/events/{year}/simple", cdir, f"team__{t}__events__{year}",
                            headers=hdr, max_age=86400, offline=not can_net, valid=lambda d: isinstance(d, list), log=log)
        keep = []
        for e in evs or []:
            if e.get("key") == event or e.get("event_type") not in OFFICIAL_TYPES:
                continue
            if (e.get("end_date") or "9999") < start_date:
                keep.append((e.get("start_date") or "", e["key"]))
        team_events[t] = [k for _, k in sorted(keep)]
    events = sorted({k for v in team_events.values() for k in v})
    matches = {}
    for ek in events:
        ms, _ = cached_get(f"{TBA_BASE}/event/{ek}/matches", cdir, f"event__{ek}__matches", headers=hdr,
                           max_age=None, offline=not can_net, valid=lambda d: isinstance(d, list) and len(d) > 0, log=log)
        if ms:
            matches[ek] = ms
    return {"team_events": team_events, "matches": matches}


def _event_struct(ek, raw):
    """E-like dict (see data.load_event) from a raw TBA match list, TBA only."""
    import tempfile
    from .data import load_tba
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(raw, f)
        p = f.name
    try:
        tba = load_tba(p)
    finally:
        os.unlink(p)
    tba = [m for m in tba if m["played"]]
    teams = sorted({t for m in tba for a in ("red", "blue") for t in m["teams"][a]}, key=lambda s: int(s[3:]))
    return {"event": ek, "tba": tba, "by_key": {m["key"]: m for m in tba}, "video": {"alliance": [], "robot": []},
            "teams": teams, "routines": {}}


# ------------------------------------------------------------------------------------------------- pre-event prior
def pre_event_prior(data_dir, E, sb=None, offline=False, log=None, use_local=True, use_statbotics=True):
    """-> {"team": {team: {"comp": {c: [mean, sd]}, "climb": {...}, "source", "events", "n"}},
           "event_mean": {c: mean robot FUEL}, "hyper": {c: {"sigma2","tau2"}}, "sources": {...}} or None."""
    from .model import fit_event_model, team_theta
    teams = E["teams"]
    ev_info = E.get("info") or {}
    start = ev_info.get("start_date") or time.strftime("%Y-%m-%d", time.gmtime(min(m["time"] for m in E["tba"])))
    year = int(ev_info.get("year") or start[:4])
    out = {"team": {}, "event_mean": {}, "hyper": {}, "sources": {"local_events": [], "statbotics": False}}
    fits = {}
    if use_local:
        pe = fetch_prior_events(data_dir, E["event"], teams, start, year, offline=offline, log=log)
        for ek, raw in sorted(pe["matches"].items()):
            Ep = _event_struct(ek, raw)
            if sum(1 for m in Ep["tba"] if m["comp_level"] == "qm") < 10:
                continue
            Mp = fit_event_model(Ep, None, use_video=False)
            fits[ek] = (Mp, Ep)
        out["sources"]["local_events"] = sorted(fits)
        for t in teams:
            evs = [ek for ek in pe["team_events"].get(t, []) if ek in fits]
            if not evs:
                continue
            comp = {}
            for c in COMPONENTS:
                # precision-weighted over the team's prior events; older events get extra drift per event of age
                num = den = 0.0
                for age, ek in enumerate(reversed(evs)):
                    Mp = fits[ek][0]
                    m, sd = team_theta(Mp.comp[c], t)
                    v = sd ** 2 + age * (DRIFT_FRAC ** 2) * Mp.comp[c]["tau2"]
                    num += m / v
                    den += 1.0 / v
                Ml = fits[evs[-1]][0]
                var = 1.0 / den + (DRIFT_FRAC ** 2) * Ml.comp[c]["tau2"]
                comp[c] = [num / den, math.sqrt(var)]
            # climbs: prior-event counts, down-weighted
            n = 0
            auto_k = 0.0
            end_k = np.zeros(4)
            for ek in evs:
                cl = fits[ek][0].climb["team"].get(t) or {}
                n += cl.get("n", 0)
                auto_k += cl.get("auto_obs", 0)
                end_k += np.asarray(cl.get("end_obs") or [0, 0, 0, 0], float)
            out["team"][t] = {"comp": comp, "source": "local", "events": evs,
                              "n": int(sum(len([m for m in fits[ek][1]["tba"] if t in m["teams"]["red"] + m["teams"]["blue"]])
                                           for ek in evs)),
                              "climb": {"n": PRIOR_CLIMB_W * n, "auto": PRIOR_CLIMB_W * auto_k,
                                        "end": (PRIOR_CLIMB_W * end_k).tolist()}}
        if fits:
            for c in COMPONENTS:
                out["hyper"][c] = {"sigma2": float(np.mean([f[0].comp[c]["sigma2"] for f in fits.values()])),
                                   "tau2": float(np.mean([f[0].comp[c]["tau2"] for f in fits.values()]))}
                if c != "auto":     # schedule effect of winning auto, from the prior events (wide)
                    b = float(np.mean([f[0].comp[c]["beta"] for f in fits.values()]))
                    bsd = float(np.mean([f[0].comp[c]["beta_sd"] for f in fits.values()]))
                    out["hyper"][c]["beta"] = [b, max(bsd, 0.5 * abs(b), 3.0)]
    # Statbotics pre-event EPA for teams without a local prior (approximate component mapping)
    if use_statbotics and sb and sb.get("team_events"):
        out["sources"]["statbotics"] = True
        have = [t for t in teams if t in out["team"]]
        if have:
            share = {c: float(np.mean([out["team"][t]["comp"][c][0] for t in have])) for c in COMPONENTS}
        else:
            from .model import DEFAULT_ALLIANCE
            share = {c: DEFAULT_ALLIANCE[c] / 3.0 for c in COMPONENTS}
        tot_share = sum(share.values())
        tower_guess = 3.0      # robot tower points per match (rare at week-1/2 PNW events)
        for t in teams:
            if t in out["team"]:
                continue
            te = sb["team_events"].get(t) or {}
            st = ((te.get("epa") or {}).get("stats") or {}).get("start")
            if st is None:
                continue
            fuel = max(float(st) - tower_guess, 1.0)
            sd_tot = _sb_num(((te.get("epa") or {}).get("total_points") or {}).get("sd") if isinstance(
                (te.get("epa") or {}).get("total_points"), dict) else None) or 0.35 * fuel
            comp = {}
            for c in COMPONENTS:
                f = share[c] / tot_share
                tau_c = math.sqrt(out["hyper"][c]["tau2"]) if c in out["hyper"] else 0.5 * f * fuel
                comp[c] = [f * fuel, math.sqrt((f * sd_tot) ** 2 + (DRIFT_FRAC * tau_c) ** 2)]
            out["team"][t] = {"comp": comp, "source": "statbotics", "events": [], "n": None, "climb": None}
    if not out["team"]:
        return None
    have = list(out["team"])
    for c in COMPONENTS:
        out["event_mean"][c] = float(np.mean([out["team"][t]["comp"][c][0] for t in have]))
    out["n_teams"] = len(have)
    return out


# ------------------------------------------------------------------------------------------------- display EPA
class ComponentEPA:
    """Statbotics-style running EPA per points component: auto (FUEL + auto tower), teleop (teleop FUEL),
    endgame (endgame tower).  Same update rule as baselines.EPA (K 0.5 -> 1/3 over a team's first 6 matches)."""
    KEYS = ("auto", "teleop", "endgame")

    def __init__(self, init=None, default=None):
        self.r = {t: dict(v) for t, v in (init or {}).items()}
        self.n = {t: 6 for t in self.r}           # carried-over teams update at the settled rate
        self.default = default or {"auto": 10.0, "teleop": 40.0, "endgame": 2.0}

    @staticmethod
    def alliance_vals(m, a):
        f = m[a]["fuel"]
        at = sum(1 for x in m[a]["auto_tower"] if x >= 1)
        teleop = f["transition"] + sum(f[s] for s in SHIFTS) + f["endgame"]
        end = sum(TOWER_TELEOP_PTS[x] for x in m[a]["end_tower"])
        return {"auto": f["auto"] + min(at, 2) * TOWER_AUTO_PTS, "teleop": teleop, "endgame": end}

    def get(self, t):
        if t not in self.r:
            self.r[t] = dict(self.default)
            self.n[t] = 0
        return self.r[t]

    def update(self, m):
        for a in ("red", "blue"):
            tl = m["teams"][a]
            act = self.alliance_vals(m, a)
            pred = {k: sum(self.get(t)[k] for t in tl) for k in self.KEYS}
            for t in tl:
                k = 1.0 / 3.0 + (0.5 - 1.0 / 3.0) * max(0.0, 1.0 - self.n[t] / 6.0)
                for c in self.KEYS:
                    self.r[t][c] += k * (act[c] - pred[c]) / 3.0
                self.n[t] += 1

    def value(self, t):
        v = self.get(t)
        return {"total": sum(v.values()), **v}


def local_epa(E, prior_matches, played_before, prior_events=None):
    """Running component EPA: through the teams' prior events (if given), then this event's matches in `played_before`."""
    epa = ComponentEPA()
    if prior_events:
        allm = []
        for ek, raw in prior_events.items():
            allm += _event_struct(ek, raw)["tba"]
        allm.sort(key=lambda m: m["time"])
        for m in allm:
            epa.update(m)
        if epa.r:
            epa.default = {k: float(np.mean([v[k] for v in epa.r.values()])) for k in ComponentEPA.KEYS}
        epa.n = {t: 6 for t in epa.r}
    elif prior_matches:
        vals = [ComponentEPA.alliance_vals(m, a) for m in prior_matches for a in ("red", "blue")]
        epa.default = {k: float(np.mean([v[k] for v in vals])) / 3.0 for k in ComponentEPA.KEYS}
    for m in played_before:
        epa.update(m)
    return epa
