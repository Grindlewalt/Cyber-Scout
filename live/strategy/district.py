"""FRC district points (game-manual rules) + TBA district data loader.

Rules (verified against TBA /event/{key}/district_points and /district/{key}/rankings, see validate() / .work/goals/NOTES.md):
  qualification   ceil( erfinv((N - 2R + 2) / (1.07 N)) * 10 / erfinv(1/1.07) + 12 )     N teams ranked, R final rank
  alliance sel.   alliance n: captain 17-n, first pick 17-n, second pick n; backup/4th pick 0
  playoffs        (2023+ 8-alliance double elimination) winner 30, finalist 20, lost last lower-bracket match (TBA
                  "Round 5") 13, "Round 4" 7, earlier 0.  A team that played only part of its alliance's playoff
                  matches gets less (empirical table PARTIAL, derived from TBA data; only matters for backups).
  awards          Impact 10, Engineering Inspiration 8, Rookie All-Star 8, other judged awards 5 (summed per team)
  DCMP            every component x3 (Impact 30, EI/RAS 24, ...)
  season total    first TWO district events (by date) + district championship + rookie bonus + adjustments
  DCMP bids       award pre-qualifiers (Impact / EI / RAS winners at district events) + top teams by points; total size
                  is NOT in the TBA API -> CAPACITY table / env DCMP_CAPACITY / pass `capacity=`.

Data: TBA v3 via epa.cached_get (key from env TBA_KEY), cached under DATA_DIR/cache/tba_district/.
`load_district(data_dir, "2026pnw", asof=None)` -> dict used by goals.py.   Offline: only the cache is read.
"""
import json
import math
import os
import time

import numpy as np
from scipy.special import erfinv

from . import epa as EPA

ALPHA = 1.07
TBA_BASE = EPA.TBA_BASE
DCMP_TYPES = {2}                  # TBA event_type: 2 = district championship
DISTRICT_TYPES = {1, 2, 5}
MULT_DCMP = 3
# judged awards that earn points: everything except these TBA award types (winner, finalist, Woodie Flowers, Dean's List,
# Volunteer, Founders, Bart Kamen, Make It Loud, highest rookie seed, #1 seed, Incredible Play, People's Choice)
NO_POINT_AWARDS = {1, 2, 3, 4, 5, 6, 7, 8, 14, 39, 40, 41}
AWARD_PTS = {0: 10, 9: 8, 10: 8}  # Impact, Engineering Inspiration, Rookie All-Star; others 5
PREQUAL_AWARDS = (0, 9, 10)
# partial playoff participation: full value -> (value if played more than half the alliance's playoff matches, else)
PARTIAL = {30: (24, 7), 20: (15, 5), 13: (13, 7), 7: (7, 4)}
LEVEL_PTS = {"won": 30, "f": 20, "Round 5": 13, "Round 4": 7}
CAPACITY = {"2026pnw": 51}        # observed DCMP size (2026pncmp, TBA teams list); configurable below
DEFAULT_CAPACITY = 64
WORLDS_SLOTS = {"2026pnw": 14}    # assumption (not in TBA); configurable


# ------------------------------------------------------------------------------------------------- the rules
def qual_points(rank, n):
    """TBA/manual qualification points for final rank `rank` of `n` teams."""
    if not n or rank is None:
        return 0
    return int(math.ceil(float(erfinv((n - 2 * rank + 2) / (ALPHA * n))) * (10.0 / float(erfinv(1.0 / ALPHA))) + 12))


def alliance_points(alliances):
    """{team: pts} from TBA alliances (picks in selection order: captain, 1st pick, 2nd pick, [backup])."""
    out = {}
    for i, a in enumerate(alliances or []):
        n = i + 1
        for j, t in enumerate((a.get("picks") or [])[:3]):
            out[t] = (17 - n) if j < 2 else n
    return out


def elim_level_points(status):
    """playoff points of an alliance from its TBA status dict."""
    if not status:
        return 0
    if status.get("status") == "won":
        return LEVEL_PTS["won"]
    if status.get("level") == "f":
        return LEVEL_PTS["f"]
    return LEVEL_PTS.get(status.get("double_elim_round"), 0)


def elim_points(alliances, matches):
    """{team: pts} from TBA alliances (+status) and playoff matches (to detect partial participation)."""
    playoff = [m for m in matches if m["comp_level"] != "qm"]
    out = {}
    for a in alliances or []:
        full = elim_level_points(a.get("status"))
        picks = list(a.get("picks") or [])
        ps = set(picks)
        am = [m for m in playoff if ps & set(m["alliances"]["red"]["team_keys"] + m["alliances"]["blue"]["team_keys"])]
        for t in picks:
            mine = [m for m in am if t in m["alliances"]["red"]["team_keys"] + m["alliances"]["blue"]["team_keys"]]
            pts = full
            if full and am and len(mine) < len(am):
                hi, lo = PARTIAL.get(full, (full, 0))
                pts = hi if len(mine) * 2 > len(am) else lo
            out[t] = pts
    return out


def award_points(awards):
    """{team: pts} from TBA awards list of one event."""
    out = {}
    for a in awards or []:
        ty = a.get("award_type")
        if ty in NO_POINT_AWARDS:
            continue
        p = AWARD_PTS.get(ty, 5)
        for r in a.get("recipient_list") or []:
            t = r.get("team_key")
            if t:
                out[t] = out.get(t, 0) + p
    return out


def event_points(ev):
    """Actual district points at one event from its data dict (see load_district): {team: {qual, alliance, elim, award, total}}.
    Qualification rank order comes from TBA event rankings when present."""
    mult = MULT_DCMP if ev["dcmp"] else 1
    rk = ev.get("rankings") or []
    n = len(rk)
    q = {r["team_key"]: qual_points(r["rank"], n) for r in rk}
    al = alliance_points(ev.get("alliances"))
    el = elim_points(ev.get("alliances"), ev.get("matches") or [])
    aw = award_points(ev.get("awards"))
    teams = set(q) | set(al) | set(el) | set(aw)
    out = {}
    for t in teams:
        d = {"qual_points": q.get(t, 0) * mult, "alliance_points": al.get(t, 0) * mult,
             "elim_points": el.get(t, 0) * mult, "award_points": aw.get(t, 0) * mult}
        d["total"] = d["qual_points"] + d["alliance_points"] + d["elim_points"] + d["award_points"]
        out[t] = d
    return out


def season_totals(per_event, order, bonus=None, adjust=None, dcmp_events=()):
    """per_event {event: {team: pts dict}}, order = event keys sorted by date (district events then DCMP).
    -> {team: {"events": [(ev, total, counted)], "total", "pre_dcmp"}} using the first two district events + DCMP."""
    bonus, adjust = bonus or {}, adjust or {}
    teams = {t for e in per_event.values() for t in e} | set(bonus) | set(adjust)
    res = {}
    for t in teams:
        evs, n = [], 0
        for ek in order:
            e = per_event.get(ek, {}).get(t)
            if e is None:
                continue
            dc = ek in dcmp_events
            counted = dc or n < 2
            if not dc:
                n += 1
            evs.append({"event": ek, "total": e["total"], "counted": bool(counted), "dcmp": bool(dc)})
        pre = sum(x["total"] for x in evs if x["counted"] and not x["dcmp"]) + bonus.get(t, 0) + adjust.get(t, 0)
        res[t] = {"events": evs, "pre_dcmp": pre, "total": pre + sum(x["total"] for x in evs if x["dcmp"] and x["counted"])}
    return res


# ------------------------------------------------------------------------------------------------- TBA loading
def _hdr():
    k = EPA._tba_key()
    return {"X-TBA-Auth-Key": k} if k else {}


def tba_get(data_dir, ep, max_age=None, offline=False, valid=None, log=None):
    cdir = os.path.join(data_dir, "cache", "tba_district")
    can = bool(EPA._tba_key()) and not offline
    d, info = EPA.cached_get(f"{TBA_BASE}/{ep}", cdir, ep, headers=_hdr(), max_age=max_age, offline=not can,
                             valid=valid or (lambda x: x is not None and not (isinstance(x, dict) and "Error" in x)), log=log)
    return d


def _posted(m):
    return m.get("post_result_time") or ((m.get("actual_time") or m.get("time") or 0) + 300)


def load_district(data_dir, district_key, asof=None, offline=False, log=None):
    """Everything needed for district points + goal sims.  asof=None -> now.  Matches/results after `asof` are hidden
    (replay); events are then 'done' / 'live' / 'future' as of asof.  Finished events are cached forever."""
    now = time.time() if asof is None else asof
    evs = tba_get(data_dir, f"district/{district_key}/events", max_age=86400, offline=offline, log=log) or []
    evs = sorted(evs, key=lambda e: (e["start_date"], e["key"]))
    out_events = []
    for e in evs:
        k = e["key"]
        end_ts = time.mktime(time.strptime(e["end_date"], "%Y-%m-%d")) + 86400 + 6 * 3600
        finished = time.time() > end_ts + 2 * 86400
        age = None if finished else 120
        ms = tba_get(data_dir, f"event/{k}/matches", max_age=age, offline=offline, log=log) or []
        teams = tba_get(data_dir, f"event/{k}/teams/keys", max_age=age if age else 86400, offline=offline, log=log) or []
        shown = []
        for m in ms:
            m = dict(m)
            if asof is not None and m.get("alliances", {}).get("red", {}).get("score", -1) >= 0 and _posted(m) > asof:
                m = _hide(m)
            shown.append(m)
        ms = shown
        played = [m for m in ms if m["alliances"]["red"]["score"] is not None and m["alliances"]["red"]["score"] >= 0]
        quals = [m for m in ms if m["comp_level"] == "qm"]
        qp = [m for m in quals if m in played]
        finals = [m for m in played if m["comp_level"] == "f"]
        won_finals = False
        if finals:
            c = {"red": 0, "blue": 0}
            for m in finals:
                if m.get("winning_alliance") in c:
                    c[m["winning_alliance"]] += 1
            won_finals = max(c.values()) >= 2
        status = "future" if not played else "done" if (won_finals or (finished and asof is None)) else "live"
        d = {"key": k, "name": e.get("name"), "short_name": e.get("short_name"), "start_date": e["start_date"],
             "end_date": e["end_date"], "type": e.get("event_type"), "dcmp": e.get("event_type") in DCMP_TYPES,
             "week": e.get("week"), "teams": teams, "matches": ms, "status": status,
             "quals_total": len(quals), "quals_played": len(qp), "finished": finished}
        if status == "done" or (asof is None and played):
            d["rankings"] = _event_rankings(data_dir, k, finished, offline, log) if status == "done" else None
            d["alliances"] = tba_get(data_dir, f"event/{k}/alliances", max_age=age, offline=offline, log=log) or []
            d["awards"] = tba_get(data_dir, f"event/{k}/awards", max_age=age, offline=offline, log=log) or []
            dp = tba_get(data_dir, f"event/{k}/district_points", max_age=age, offline=offline, log=log)
            d["tba_points"] = (dp or {}).get("points") if isinstance(dp, dict) else None
        else:
            d["rankings"], d["alliances"], d["awards"] = None, [], []
        if asof is not None and status == "live":     # live at the replay point: nothing final yet
            d["rankings"], d["alliances"], d["awards"] = None, [], []
        out_events.append(d)
    rk = tba_get(data_dir, f"district/{district_key}/rankings", max_age=3600, offline=offline, log=log) or []
    dt = tba_get(data_dir, f"district/{district_key}/teams/keys", max_age=86400, offline=offline, log=log) or []
    return {"key": district_key, "year": int(district_key[:4]), "events": out_events, "tba_rankings": rk,
            "district_teams": dt, "asof": asof, "now": now}


def _hide(m):
    m = json.loads(json.dumps(m))
    for c in ("red", "blue"):
        m["alliances"][c]["score"] = -1
    m["score_breakdown"] = None
    m["winning_alliance"] = ""
    return m


def _event_rankings(data_dir, k, finished, offline, log):
    r = tba_get(data_dir, f"event/{k}/rankings", max_age=None if finished else 120, offline=offline, log=log)
    return (r or {}).get("rankings") if isinstance(r, dict) else None


# ------------------------------------------------------------------------------------------------- standings
def standings(D, upto=None):
    """Actual district standings from 'done' events (computed from raw data with the rules above).
    -> {team: {"pre_dcmp","total","events":[...]}} and per-event point dicts."""
    per, order, dc = {}, [], set()
    for ev in D["events"]:
        if ev["status"] != "done" or not ev.get("rankings"):
            continue
        per[ev["key"]] = event_points(ev)
        if ev["dcmp"]:
            dc.add(ev["key"])
        order.append(ev["key"])
    rookie = {r["team_key"]: r.get("rookie_bonus", 0) for r in D.get("tba_rankings", [])}
    adj = {r["team_key"]: r.get("adjustments", 0) for r in D.get("tba_rankings", [])}
    return season_totals(per, order, rookie, adj, dc), per


def dcmp_qualifiers(D, per_event_awards=None):
    """Award pre-qualifiers: recipients of Impact / EI / RAS at district (non-DCMP) events."""
    q = set()
    for ev in D["events"]:
        if ev["dcmp"]:
            continue
        for a in ev.get("awards") or []:
            if a.get("award_type") in PREQUAL_AWARDS:
                q.update(r["team_key"] for r in a.get("recipient_list") or [] if r.get("team_key"))
    return q


# ------------------------------------------------------------------------------------------------- validation
def validate(D, log=print):
    """Compare event_points() with TBA /event/{k}/district_points and standings with /district/{k}/rankings."""
    res = {"events": {}, "teams": 0, "exact": 0, "components": {c: [0, 0] for c in
                                                                 ("qual_points", "alliance_points", "elim_points", "award_points", "total")}}
    for ev in D["events"]:
        if ev["status"] != "done" or not ev.get("rankings"):
            continue
        tb = ev.get("tba_points")
        mine = event_points(ev)
        ok = n = 0
        bad = []
        for t, v in (tb or {}).items():
            n += 1
            m = mine.get(t, {"qual_points": 0, "alliance_points": 0, "elim_points": 0, "award_points": 0, "total": 0})
            same = all(m[c] == v[c] for c in res["components"])
            for c in res["components"]:
                res["components"][c][1] += 1
                res["components"][c][0] += m[c] == v[c]
            ok += same
            if not same:
                bad.append((t, {c: (m[c], v[c]) for c in res["components"] if m[c] != v[c]}))
        res["events"][ev["key"]] = {"teams": n, "exact": ok, "mismatch": bad}
        res["teams"] += n
        res["exact"] += ok
    return res
