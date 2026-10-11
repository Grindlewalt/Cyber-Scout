"""Load everything the strategy engine needs from a Cyber-Scout data root.

Layout (production == local copy):
    DATA_DIR/tba/event__<ev>__matches.json            TBA matches (scores, hubScore per period, towers, times)
    DATA_DIR/site/api/<ev>/{event.json,m/*.json,t/*.json}   site JSON (team labels joined, quarantine applied)
    DATA_DIR/events/<ev>/{matches,shots,analytics}/*.json    (not needed: the site JSON carries tracks+shots+analytics)

Two kinds of records come out:
  * TBA match records  (`load_tba`)  -- alliance-level truth for every played match: fuel per period, schedule,
    per-robot tower levels (station order = team_keys order, so robot-level and trustworthy), fouls, score.
  * Video records      (`load_video`) -- per alliance-match soft allocation of counted FUEL to the three robots, plus
    per robot-match descriptive features.  Identity confidence is propagated, never trusted blindly:
      - a shot launched by track k at a frame whose identity conf is c gives P(team of track k) = max(c, 1/3) and
        (1-P)/2 to each teammate; c = per-frame conf (v2), else track team_conf, else id_conf (v1: ~0.2 -> uniform);
      - robot-match descriptive features carry weight w_id = ramp(team_conf; 0.3 -> 0, 0.8 -> 1) x coverage.
"""
import glob
import json
import os
import re

import numpy as np

PERIODS = ["auto", "transition", "shift1", "shift2", "shift3", "shift4", "endgame"]
PERIOD_T = {"auto": (0, 20), "transition": (20, 30), "shift1": (30, 55), "shift2": (55, 80), "shift3": (80, 105),
            "shift4": (105, 130), "endgame": (130, 160)}
SHIFTS = ["shift1", "shift2", "shift3", "shift4"]
# fuel components modelled per robot. "active" = counted fuel over the alliance's two active shifts (TBA counts 0 in
# inactive shifts at this event: fuel arriving within the 3 s grace is booked to the previous period).
COMPONENTS = ["auto", "trans", "active", "endgame"]
COMP_PERIODS = {"auto": ["auto"], "trans": ["transition"], "active": SHIFTS, "endgame": ["endgame"]}
LEN, WID = 16.541, 8.069
LEVEL = {"None": 0, None: 0, "": 0, "Level1": 1, "Level2": 2, "Level3": 3}
TOWER_TELEOP_PTS = [0, 10, 20, 30]
TOWER_AUTO_PTS = 15          # L1 in auto, max 2 robots per alliance

ID_RAMP = (0.3, 0.8)         # identity-confidence ramp for robot-match weights
MIN_TRACED = 3               # min traced counted fuel in an alliance-component to use the video split


def ramp(c, lo=ID_RAMP[0], hi=ID_RAMP[1]):
    if c is None:
        return 0.0
    return float(np.clip((c - lo) / (hi - lo), 0.0, 1.0))


def match_num(key):
    m = re.search(r"_(qm|ef|qf|sf|f)(\d+)(?:m(\d+))?$", key)
    if not m:
        return (9, 0, 0)
    lvl = {"qm": 0, "ef": 1, "qf": 2, "sf": 3, "f": 4}[m.group(1)]
    return (lvl, int(m.group(2)), int(m.group(3) or 0))


# ------------------------------------------------------------------------------------------------- paths
def resolve(data_dir, event=None):
    """Return {event, tba, site} paths. Accepts the production data root or any dir with the same layout."""
    d = os.path.abspath(data_dir)
    tba_files = sorted(glob.glob(os.path.join(d, "tba", "event__*__matches.json")))
    if event is None:
        if len(tba_files) == 1:
            event = re.search(r"event__(.+)__matches", tba_files[0]).group(1)
        else:
            evs = sorted(os.listdir(os.path.join(d, "site", "api"))) if os.path.isdir(os.path.join(d, "site", "api")) else []
            if len(evs) != 1:
                raise SystemExit(f"cannot infer event in {d}; pass --event (found {evs or tba_files})")
            event = evs[0]
    tba = os.path.join(d, "tba", f"event__{event}__matches.json")
    site = os.path.join(d, "site", "api", event)
    if not os.path.exists(tba):
        raise SystemExit(f"missing {tba}")
    return {"event": event, "root": d, "tba": tba, "site": site if os.path.isdir(site) else None}


# ------------------------------------------------------------------------------------------------- TBA
def load_tba(path):
    """All matches (any comp level), sorted by time. Unplayed matches have played=False (teams still known)."""
    raw = json.load(open(path))
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    out = []
    for m in data:
        al = m["alliances"]
        teams = {a: list(al[a]["team_keys"]) for a in ("red", "blue")}
        sb = m.get("score_breakdown") or {}
        played = bool(sb) and al["red"]["score"] is not None and al["red"]["score"] >= 0
        rec = {"key": m["key"], "comp_level": m["comp_level"], "order": match_num(m["key"]),
               "time": m.get("actual_time") or m.get("time") or m.get("predicted_time") or 0,
               "teams": teams, "played": played,
               "sched_time": m.get("time"), "predicted_time": m.get("predicted_time"),
               "post_time": m.get("post_result_time"),
               "surrogates": {a: list(al[a].get("surrogate_team_keys") or []) for a in ("red", "blue")},
               "dqs": {a: list(al[a].get("dq_team_keys") or []) for a in ("red", "blue")}}
        if played:
            hs = {a: sb[a].get("hubScore") or {} for a in ("red", "blue")}
            auto = {a: hs[a].get("autoCount", 0) for a in ("red", "blue")}
            for a in ("red", "blue"):
                h = hs[a]
                fuel = {"auto": h.get("autoCount", 0), "transition": h.get("transitionCount", 0),
                        "shift1": h.get("shift1Count", 0), "shift2": h.get("shift2Count", 0),
                        "shift3": h.get("shift3Count", 0), "shift4": h.get("shift4Count", 0),
                        "endgame": h.get("endgameCount", 0), "uncounted": h.get("uncounted", 0)}
                o = "blue" if a == "red" else "red"
                if auto[a] != auto[o]:
                    won_auto = auto[a] > auto[o]
                else:   # tie: FMS picked; infer from which shifts carried counted fuel
                    won_auto = (fuel["shift2"] + fuel["shift4"]) > (fuel["shift1"] + fuel["shift3"])
                sbA = sb[a]
                rec[a] = {
                    "fuel": fuel,
                    "comp": {"auto": fuel["auto"], "trans": fuel["transition"],
                             "active": sum(fuel[s] for s in SHIFTS), "endgame": fuel["endgame"]},
                    "won_auto": bool(won_auto),
                    "active_shifts": ["shift2", "shift4"] if won_auto else ["shift1", "shift3"],
                    "auto_tower": [LEVEL.get(sbA.get(f"autoTowerRobot{i}"), 0) for i in (1, 2, 3)],
                    "end_tower": [LEVEL.get(sbA.get(f"endGameTowerRobot{i}"), 0) for i in (1, 2, 3)],
                    "tower_pts": (sbA.get("autoTowerPoints") or 0) + (sbA.get("endGameTowerPoints") or 0),
                    "fouls": sbA.get("foulPoints") or 0,
                    "score": al[a]["score"],
                    "fuel_pts": h.get("totalPoints", h.get("totalCount", 0)),
                    "fuel_count": h.get("totalCount", sum(fuel[p] for p in PERIODS)),
                    "rp": sbA.get("rp"),
                    "bonus": {"energized": bool(sbA.get("energizedAchieved")),
                              "supercharged": bool(sbA.get("superchargedAchieved")),
                              "traversal": bool(sbA.get("traversalAchieved"))},
                    "tower_total": sbA.get("totalTowerPoints", (sbA.get("autoTowerPoints") or 0) + (sbA.get("endGameTowerPoints") or 0)),
                }
            rs, bs = al["red"]["score"], al["blue"]["score"]
            rec["red_win"] = 1.0 if rs > bs else 0.0 if rs < bs else 0.5
        out.append(rec)
    out.sort(key=lambda r: (r["time"], r["order"]))
    return out


# ------------------------------------------------------------------------------------------------- video
def _ident(track):
    for k in ("team_conf", "id_conf"):
        v = track.get(k)
        if v is not None:
            return float(v)
    return 0.0


def _frame_conf(track, match_t, hz):
    conf = track.get("conf")
    if isinstance(conf, list) and conf:
        i = int(round((match_t + 0.5) * hz))
        i = min(max(i, 0), len(conf) - 1)
        c = conf[i]
        return 0.0 if c is None else float(c)
    return _ident(track)


def _canon(x, y, alliance):
    return (LEN - x, WID - y) if alliance == "blue" else (x, y)


def match_clock(t_replay, t_teleop_start):
    T = float(t_teleop_start or 23.5) - 0.5
    t = np.asarray(t_replay, float)
    return np.where(t <= 20.0, t, np.where(t < T, 20.0, t - (T - 20.0)))


LANES = [(0.0, 1.28, "left trench"), (1.28, 3.40, "left bump"), (3.40, 4.67, "centre"),
         (4.67, 6.79, "right bump"), (6.79, WID + 1, "right trench")]


def shot_zone(xc, yc):
    """Named shooting zone in the canonical (own = red) frame. Left/right = as seen from the alliance's own driver
    station (looking down-field toward -x, so driver-left = low y)."""
    region = "own zone" if xc >= 12.513 else ("neutral" if xc >= 4.028 else "opp zone")
    yc = min(max(yc, 0.0), WID - 1e-6)
    lane = next(n for lo, hi, n in LANES if lo <= yc < hi)
    return f"{region} / {lane}"


def load_video(site_dir, tba_by_key):
    """-> {"alliance": [alliance-match video blocks], "robot": [robot-match feature records]}"""
    blocks, robots = [], []
    if not site_dir:
        return {"alliance": blocks, "robot": robots}
    files = [f for f in glob.glob(os.path.join(site_dir, "m", "*.json")) if not f.endswith(".fuel.json")]
    for f in sorted(files):
        try:
            m = json.load(open(f))
        except Exception:
            continue
        key = m.get("key")
        tm = tba_by_key.get(key)
        if not tm or not tm["played"] or not m.get("tracks"):
            continue
        hz = m.get("hz") or 10
        tracks = m["tracks"]
        shots = m.get("shots")
        has_shots = isinstance(shots, list) and len(shots) > 0
        per = {p["name"]: p for p in (m.get("periods") or [])}
        cov = (m.get("reconcile") or {}).get("coverage") or {}
        # ---- alliance soft allocation of counted fuel
        if has_shots:
            acc = {a: {p: {"robot_mean": np.zeros(3), "robot_cov": np.zeros((3, 3)), "hp": 0.0, "unknown": 0.0,
                           "n_shots": 0, "id_info": 0.0} for p in PERIODS} for a in ("red", "blue")}
            for s in shots:
                if s.get("outcome") != "made" or s.get("hub_active") is False:
                    continue
                p = s.get("period")
                tgt = s.get("target") or ""
                a = "red" if tgt.startswith("red") else "blue" if tgt.startswith("blue") else None
                if p not in PERIODS or a is None:
                    continue
                A = acc[a][p]
                sh = s.get("shooter") or {}
                kind = sh.get("kind")
                if kind == "human":
                    A["hp"] += 1
                    continue
                if kind != "robot":
                    A["unknown"] += 1
                    continue
                k = sh.get("track_idx")
                pv = np.full(3, 1.0 / 3)
                if k is not None and 0 <= k < len(tracks) and sh.get("alliance") == a:
                    tr = tracks[k]
                    team = tr.get("team")
                    if team in tm["teams"][a]:
                        c = _frame_conf(tr, s.get("match_t") or 0.0, hz)
                        pt = max(c, 1.0 / 3)
                        pv = np.full(3, (1 - pt) / 2)
                        pv[tm["teams"][a].index(team)] = pt
                A["robot_mean"] += pv
                A["robot_cov"] += np.diag(pv) - np.outer(pv, pv)
                A["n_shots"] += 1
                A["id_info"] += float(pv.max() - 1.0 / 3) / (2.0 / 3)
            for a in ("red", "blue"):
                comp = {}
                for c, ps in COMP_PERIODS.items():
                    if c == "active":
                        ps = tm[a]["active_shifts"]
                    cv = min([cov.get(p, 1.0) for p in ps] or [1.0])
                    mean = sum(acc[a][p]["robot_mean"] for p in ps)
                    cvm = sum(acc[a][p]["robot_cov"] for p in ps)
                    hp = sum(acc[a][p]["hp"] for p in ps)
                    n = sum(acc[a][p]["n_shots"] for p in ps)
                    info = sum(acc[a][p]["id_info"] for p in ps)
                    traced = float(mean.sum() + hp)
                    tba_c = tm[a]["comp"][c]
                    usable = traced >= MIN_TRACED and cv >= 0.8 and tba_c > 0
                    scale = tba_c / traced if traced > 0 else None
                    comp[c] = {"usable": bool(usable), "scale": scale, "coverage": cv, "traced": traced, "tba": tba_c,
                               "y": (mean * scale).tolist() if usable else None,
                               "V": (cvm * scale * scale).tolist() if usable else None,
                               "hp": hp * scale if usable else None, "id_info": info / max(n, 1)}
                blocks.append({"match": key, "time": tm["time"], "alliance": a, "teams": tm["teams"][a],
                               "won_auto": tm[a]["won_auto"], "comp": comp,
                               "by_period_hp": {p: acc[a][p]["hp"] for p in PERIODS},
                               "by_period_traced": {p: float(acc[a][p]["robot_mean"].sum() + acc[a][p]["hp"])
                                                    for p in PERIODS}})
        # ---- robot-match features
        an = m.get("analytics") or []
        for k, tr in enumerate(tracks):
            a = tr.get("alliance")
            team = tr.get("team")
            if a not in ("red", "blue") or team not in tm["teams"][a]:
                continue
            ident = _ident(tr)
            st = (tr.get("stats") or {})
            observed = st.get("observed")
            w = ramp(ident) * (min(1.0, (observed or 0) / 0.5) if observed is not None else 0.5)
            rec = {"match": key, "time": tm["time"], "team": team, "alliance": a,
                   "station": tm["teams"][a].index(team) + 1, "ident": ident, "w": w, "observed": observed,
                   "has_shots": has_shots, "won_auto": tm[a]["won_auto"]}
            if has_shots:
                rec.update(_robot_shots(shots, k, team, a, per, m))
            if k < len(an) and an[k]:
                rec["an"] = an[k]
            rec.update(_robot_positions(tr, m, per, a))
            robots.append(rec)
    return {"alliance": blocks, "robot": robots}


def _robot_shots(shots, k, team, a, per, m):
    """Hard-attributed (quarantine respected) shot stats for one robot-match."""
    by = {p: {"att": 0, "made": 0, "counted": 0, "uncounted": 0, "pass": 0} for p in PERIODS}
    origins = []
    for s in shots:
        sh = s.get("shooter") or {}
        if sh.get("kind") != "robot" or sh.get("track_idx") != k or s.get("team") != team or s.get("unattributed"):
            continue
        p = s.get("period")
        if p not in by:
            continue
        o = s.get("outcome")
        if o == "pass":
            by[p]["pass"] += 1
            continue
        if o not in ("made", "missed"):
            continue
        by[p]["att"] += 1
        if o == "made":
            by[p]["made"] += 1
            if s.get("hub_active") is False:
                by[p]["uncounted"] += 1
            else:
                by[p]["counted"] += 1
        org = s.get("origin")
        if org and len(org) >= 2:
            xc, yc = _canon(org[0], org[1], a)
            origins.append({"p": p, "xc": round(xc, 2), "yc": round(yc, 2), "made": o == "made",
                            "counted": o == "made" and s.get("hub_active") is not False})
    # active / inactive split of shifts for this alliance
    act = {p: bool((per.get(p) or {}).get("active", {}).get(a, True)) for p in PERIODS}
    hs = {"active": {"att": 0, "made": 0, "pass": 0, "secs": 0.0}, "inactive": {"att": 0, "made": 0, "pass": 0, "secs": 0.0}}
    for p in SHIFTS:
        st = "active" if act[p] else "inactive"
        hs[st]["att"] += by[p]["att"]
        hs[st]["made"] += by[p]["made"]
        hs[st]["pass"] += by[p]["pass"]
        hs[st]["secs"] += PERIOD_T[p][1] - PERIOD_T[p][0]
    return {"by_period": by, "origins": origins, "hub_state": hs, "period_active": act}


def _robot_positions(tr, m, per, a):
    """Zone time split by own-hub state over the shifts, from frames with identity conf >= 0.5."""
    xy = tr.get("xy")
    conf = tr.get("conf")
    obs = tr.get("obs")
    if not xy:
        return {}
    hz = m.get("hz") or 10
    xy = np.asarray(xy, float) / 100.0
    n = len(xy)
    ok = np.asarray(obs if obs else [1] * n, float) > 0
    if isinstance(conf, list) and len(conf) == n:
        ok &= np.asarray([c or 0 for c in conf], float) >= 0.5
    else:
        ok &= _ident(tr) >= 0.5
    tclk = match_clock(np.arange(n) / hz - 0.5, m.get("t_teleop_start"))
    xc = np.where(a == "blue", LEN - xy[:, 0], xy[:, 0])
    out = {}
    for st in ("active", "inactive"):
        sel = np.zeros(n, bool)
        for p in SHIFTS:
            pa = bool((per.get(p) or {}).get("active", {}).get(a, True))
            if pa == (st == "active"):
                t0, t1 = PERIOD_T[p]
                sel |= (tclk >= t0) & (tclk < t1)
        s = sel & ok
        if s.sum() < 20:
            continue
        x = xc[s]
        out[st] = {"own": float(np.mean(x >= 12.513)), "neutral": float(np.mean((x >= 4.028) & (x < 12.513))),
                   "opp": float(np.mean(x < 4.028)), "n_frames": int(s.sum())}
    return {"zones_by_hub_state": out} if out else {}


def load_routines(site_dir):
    """team -> list of {id, matches:[short]} from the site team files (clustered autos)."""
    out = {}
    if not site_dir:
        return out
    for f in glob.glob(os.path.join(site_dir, "t", "*.json")):
        try:
            t = json.load(open(f))
        except Exception:
            continue
        rs = t.get("routines") or []
        out[t.get("key")] = [{"id": r.get("id"), "name": r.get("name"), "matches": r.get("matches") or []} for r in rs]
    return out


def load_event(data_dir, event=None):
    p = resolve(data_dir, event)
    tba = load_tba(p["tba"])
    by_key = {m["key"]: m for m in tba}
    vid = load_video(p["site"], by_key)
    teams = sorted({t for m in tba for a in ("red", "blue") for t in m["teams"][a]}, key=lambda s: int(s[3:]))
    info = {}
    ip = os.path.join(p["root"], "tba", f"event__{p['event']}.json")
    if os.path.exists(ip):
        try:
            raw = json.load(open(ip))
            info = raw.get("data", raw) if isinstance(raw, dict) else {}
        except Exception:
            info = {}
    return {"event": p["event"], "paths": p, "tba": tba, "by_key": by_key, "video": vid, "teams": teams,
            "routines": load_routines(p["site"]), "info": info}
