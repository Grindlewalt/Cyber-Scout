"""Match plans: api/<ev>/mp/<match>.json + mp/index.json (contract: .work/redesign/CONTRACT.md, live/strategy/README.md).

For every match with known teams: where each robot starts, its predicted auto path / points, a typical teleop cycle
and transition path, the job while its hub is inactive, and per-alliance per-period score predictions with a range.
Predictions use ONLY tracked matches that started before this match (so a finished event gives honest
predicted-vs-actual); `actual` is filled once the match is played.  Per-team behaviour is in behavior.py.

    python -m live.strategy.matchplan DATA_DIR EVENT [--team frc4911]
"""
import json
import math
import os
import re
import sys
import time

from . import behavior as B
from . import epa as EPAMOD
from . import timeline as TLM
from .data import LEVEL, TOWER_AUTO_PTS, TOWER_TELEOP_PTS, load_tba

PERIODS = B.PERIODS
LABEL = {"auto": "Auto", "transition": "Transition", "shift1": "Shift 1", "shift2": "Shift 2", "shift3": "Shift 3",
         "shift4": "Shift 4", "endgame": "Endgame"}
Z80 = 1.2816
SD_INFLATE = 1.9      # calibrated on 2026wasam: tuned so ~80 % of 2026wasam finals fall inside lo..hi
# Default start spots (field metres, blue wall x=0, red wall x=LEN, y 0..WID).  Robots start inside their alliance zone
# (ends at x=4.028 / 12.513); 2026wasam's 150 tracked starts per alliance sit at x~3.4-3.5 (blue) and ~13.2 (red).  Trench lanes
# are the y 0..1.19 and 6.88..8.07 bands (live/field/rebuilt_rules.json), so "trench" = y 0.75 / WID-0.75, "middle" = the field's
# centre line.  Station 1 is the driver's LEFT, 3 the RIGHT; the driver stands at the alliance wall facing the field, so
# left = low y for red (faces -x) and high y for blue (faces +x), matching scouting.py's side() (canonical low y = left).
LEN, WID = 16.541, 8.069
DEF_X, DEF_TRENCH_Y = 3.5, 0.75
DS_DY = 2.0          # driver stations sit on the alliance wall centred on y=WID/2, 2.0 m apart (assumed layout)
STATION_NAME = {1: "left trench", 2: "middle", 3: "right trench"}
_TLC = {}            # (team, station, al, hist keys, partners, prior keys, version) -> timeline result
_CACHE = {}          # path -> (mtime, size, extracted records)


def _jload(p, default=None):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return default


def _r(x, nd=2):
    return None if x is None else round(float(x), nd)


def _phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _pt(p):
    return [round(float(p[0]), 2), round(float(p[1]), 2)]


def _path(a):
    return None if a is None else [_pt(p) for p in a]      # canonical == field frame after canon() is applied twice


def _fld(xy, al):
    return None if xy is None else _pt(B.canon(xy[0], xy[1], al))


def _fpath(path, al):
    return None if path is None else [_pt(B.canon(p[0], p[1], al)) for p in path]


def _yt(v):
    i = v.get("id") or ""
    return i if v.get("channel") != "livestream" and re.fullmatch(r"[A-Za-z0-9_-]{11}", i) and not i.startswith("live-") else None


def _stream(v):
    return v.get("id") if v.get("channel") == "livestream" or str(v.get("id") or "").startswith("live-") else None


def _load_records(site, defense_eps):
    """key -> (m summary, robot records); cached per file by mtime."""
    out = {}
    d = os.path.join(site, "m")
    if not os.path.isdir(d):
        return out
    for fn in os.listdir(d):
        if not fn.endswith(".json") or fn.endswith(".fuel.json"):
            continue
        p = os.path.join(d, fn)
        st = os.stat(p)
        c = _CACHE.get(p)
        if c and c[0] == st.st_mtime and c[1] == st.st_size:
            out[fn[:-5]] = c[2]
            continue
        m = _jload(p)
        if not m or not m.get("tracks"):
            continue
        m["_defense_eps"] = defense_eps.get(m["key"], [])
        vid = m.get("video") or {}
        info = dict(video=dict(youtube=_yt(vid), stream=_stream(vid),
                               local=m.get("video_local"), clip=(m.get("live") or {}).get("clip") if isinstance(m.get("live"), dict) else None),
                    t_start=m.get("t_start"), recs=B.extract(m))
        _CACHE[p] = (st.st_mtime, st.st_size, info)
        out[fn[:-5]] = info
    return out


def _tower_hist(tba):
    """team -> [(time, auto_pts, end_pts)] from TBA's per-station tower levels (every played match, tracked or not)."""
    h = {}
    for r in tba:
        if not r["played"]:
            continue
        for a in ("red", "blue"):
            for i, t in enumerate(r["teams"][a]):
                al_, el = r[a]["auto_tower"][i], r[a]["end_tower"][i]
                h.setdefault(t, []).append((r["time"], TOWER_AUTO_PTS if al_ > 0 else 0, TOWER_TELEOP_PTS[el]))
    return h


def default_spot(al, station):
    """Last-resort start spot: station 1 left trench, 2 middle, 3 right trench, in the alliance's starting area."""
    fy = {1: DEF_TRENCH_Y, 2: WID / 2, 3: WID - DEF_TRENCH_Y}[station]      # left->right as seen by a RED driver (low y = left)
    y = fy if al == "red" else WID - fy                                      # a blue driver faces the other way
    return [round(LEN - DEF_X if al == "red" else DEF_X, 2), round(y, 2)]


def driver_station(al, station):
    """Where this station's driver stands: on the alliance wall (x=0 blue / x=LEN red), station 1 on the driver's left."""
    fy = WID / 2 + {1: -DS_DY, 2: 0.0, 3: DS_DY}[station]
    return dict(xy=[round(LEN if al == "red" else 0.0, 2), round(fy if al == "red" else WID - fy, 2)])


SB_SRC = "Statbotics EPA (no tracked matches here)"
_DUR = {"transition": 10, "shift1": 25, "shift2": 25, "shift3": 25, "shift4": 25, "endgame": 30}


def _sb_stand(bd, stand, scale):
    """Statbotics {auto, teleop, endgame[, total]} points -> ({period: (mean, sd)}, tower pts, auto pts) in the units
    _predict_alliance expects (it multiplies by `scale`, which turns video-credited fuel into counted fuel; Statbotics
    points are already counted, so they are divided by it first).  Teleop is spread over the periods with the event's
    shape when known (else by time), a shift counting half (only one hub is active)."""
    if not bd:
        return None
    tot, au, te, en = bd.get("total"), bd.get("auto"), bd.get("teleop"), bd.get("endgame")
    split = None
    if te is None or au is None:                       # pre-event EPA: a total only, split by generic shares
        if tot is None:
            return None
        au, te, en = tot * 0.25, tot * 0.6, tot * 0.15
        split = "total only, split 25/60/15"
    en = en or 0.0
    scale = {p: (v if v and v > 0.05 else 1.0) for p, v in scale.items()}
    f = {p: (0.5 if p.startswith("shift") else 1.0) for p in _DUR}
    c = {}
    for p in _DUR:
        m_ = stand.get(p, (None, None))[0]
        c[p] = (m_ if m_ is not None else 0.0) * f[p]
    if sum(c.values()) <= 0:
        c = {p: _DUR[p] * f[p] for p in _DUR}
    cs = sum(c.values())
    bp = {"auto": (max(0.0, au) / scale["auto"], None)}
    for p in _DUR:
        bp[p] = (max(0.0, te) * c[p] / cs / f[p] / scale[p], None)
    return dict(by_period=bp, tower=max(0.0, en), auto=max(0.0, au), teleop=max(0.0, te), split=split)


def _stand_in(hist_all):
    """Event-average robot per-period (mean, sd) when a team has no model: labelled in the output, never silent."""
    out = {}
    for p in PERIODS:
        v = [r["made"][p] for r in hist_all if r["active"].get(p, True)]
        out[p] = (B._wmean(v, [1] * len(v)), B._wsd(v, [1] * len(v))) if v else (None, None)
    return out


def _scale(before, tbk):
    """Per-period ratio TBA counted fuel / video-credited fuel over earlier tracked matches (video undercounts what
    the hub counted: unattributed shots, misses of the shot detector).  Applied to every robot rate."""
    num = {p: 0.0 for p in PERIODS}
    den = {p: 0.0 for p in PERIODS}
    for k, rs in before:
        tr = tbk.get(k)
        if not tr or not tr["played"]:
            continue
        for al in ("red", "blue"):
            vs = [x for x in rs if x["alliance"] == al]
            if len(vs) != 3:
                continue
            for p in PERIODS:
                num[p] += tr[al]["fuel"][p]
                den[p] += sum(x["made"][p] for x in vs if x["active"].get(p, True))
                if not vs[0]["active"].get(p, True):
                    num[p] -= tr[al]["fuel"][p]          # TBA books ~0 in inactive shifts: nothing to compare
    return {p: (num[p] / den[p] if den[p] >= 20 else 1.0) for p in PERIODS}


def _predict_alliance(robots, tower_hist, t0, scale, stand, sbm=None):
    """robots: list of (team, plan or None).  Returns pred dict (period means as sums of the robots' means)."""
    mean = {p: 0.0 for p in PERIODS}
    var = {p: 0.0 for p in PERIODS}
    auto_fuel = auto_var = 0.0
    standins = 0
    for team, P in robots:
        sb = (sbm or {}).get(team) if not P else None
        bp = P["by_period"] if P else (sb["by_period"] if sb else stand)
        if not P:
            standins += 1
        th = [x for x in tower_hist.get(team, []) if x[0] < t0]
        ta = B._wmean([x[1] for x in th], [1] * len(th)) if th else 0.0
        te = B._wmean([x[2] for x in th], [1] * len(th)) if th else 0.0
        if sb and not th:
            te = sb["tower"]                                 # Statbotics' endgame points are tower points
        if sb and ta:                                        # its auto points already include the tower's
            bp = dict(bp, auto=(max(0.0, sb["auto"] - ta) / scale["auto"], None))
        for p in PERIODS:
            m_, s_ = bp[p]
            m_ = (m_ or 0.0) * scale[p]
            s_ = (s_ if s_ is not None else max(1.0, 0.6 * (m_ / scale[p]))) * scale[p]
            mean[p] += m_
            var[p] += s_ ** 2
        mean["auto"] += ta
        mean["endgame"] += te
        auto_fuel += (bp["auto"][0] or 0.0) * scale["auto"]
    return mean, var, standins, auto_fuel


def _finish(mA, vA, mB, vB, a_stand, b_stand):
    """Apply the hub schedule (loser of auto is active first) and totals with an 80 % range."""
    sa = math.sqrt(vA["auto"] + vB["auto"] + 1.0)
    p_win_auto = _phi((mA["auto"] - mB["auto"]) / sa)
    res = {}
    for who, m, v, pw, st in (("A", mA, vA, p_win_auto, a_stand), ("B", mB, vB, 1 - p_win_auto, b_stand)):
        periods, tot, tv = [], 0.0, 0.0
        for p in PERIODS:
            row = {"period": p, "label": LABEL[p], "mean": None}
            mu, var = m[p], v[p]
            if p.startswith("shift"):
                n = int(p[-1])
                ap = (1 - pw) if n % 2 == 1 else pw            # shift 1/3 active for the auto loser
                var = ap * var + ap * (1 - ap) * mu ** 2 / 9.0
                mu = mu * ap
                row["active_p"] = round(ap, 2)
            row["mean"] = round(mu, 1)
            periods.append(row)
            tot += mu
            tv += var
        sd = math.sqrt(tv) * SD_INFLATE + (1.5 if st else 0.0)
        res[who] = dict(score=dict(mean=round(tot, 1), lo=round(max(0, tot - Z80 * sd), 1), hi=round(tot + Z80 * sd, 1)),
                        auto=dict(mean=round(m["auto"], 1), lo=round(max(0, m["auto"] - Z80 * math.sqrt(v["auto"]) * SD_INFLATE), 1),
                                  hi=round(m["auto"] + Z80 * math.sqrt(v["auto"]) * SD_INFLATE, 1)),
                        periods=periods, _sd=sd, _tot=tot)
    sd = math.sqrt(res["A"]["_sd"] ** 2 + res["B"]["_sd"] ** 2)
    pa = _phi((res["A"]["_tot"] - res["B"]["_tot"]) / sd) if sd > 0 else 0.5
    res["A"]["win_p"], res["B"]["win_p"] = round(pa, 3), round(1 - pa, 3)
    for k in "AB":
        res[k].pop("_sd"), res[k].pop("_tot")
    return res["A"], res["B"]


def _actual_alliance(tr, al):
    if not tr or not tr["played"] or al not in tr:
        return None
    a = tr[al]
    f = a["fuel"]
    ta = sum(TOWER_AUTO_PTS for x in a["auto_tower"] if x > 0)
    te = sum(TOWER_TELEOP_PTS[x] for x in a["end_tower"])
    pts = {p: f[p] for p in PERIODS}
    pts["auto"] += ta
    pts["endgame"] += te
    return dict(score=a["score"], auto=pts["auto"], periods=[{"period": p, "pts": pts[p]} for p in PERIODS],
                rp=a["rp"], won=bool(a["score"] > tr[("blue" if al == "red" else "red")]["score"]))


def _actual_robot(rec, al):
    if not rec:
        return None
    ct = [(x, y) for t, x, y, n in rec["intakes"] if t >= 20]
    cx = None
    if ct:
        xs = sorted(c[0] for c in ct); ys = sorted(c[1] for c in ct)
        cx = _fld([xs[len(xs) // 2], ys[len(ys) // 2]], al)
    ap = rec.get("auto_path")
    return dict(start=dict(xy=_fld(rec.get("start"), al), side=B.side_of(rec["start"][1]) if rec.get("start") else None),
                auto=dict(path=_fpath(B.resample(ap, 24).tolist() if ap else None, al), fuel=rec["made"]["auto"]),
                transition=dict(path=_fpath(B.resample(rec["trans_path"], 16).tolist() if rec.get("trans_path") else None, al),
                                scored=bool(rec["trans_scored"])),
                inactive=dict(role=(rec.get("inactive") or {}).get("role")), collect=dict(xy=cx))


def _robot_out(team, al, station, P, hist_n, rec_actual, stand, scale, spot=None, sb=None, tlr=None):
    base = dict(team=team, alliance=al, station=station, ds=driver_station(al, station), n_video=hist_n)
    if not P or not P.get("enough"):
        base.update(enough=False, why=(P or {}).get("why") or "no tracked matches yet", start=None, auto=None,
                    tele=None, partners=[])
        am, asd = stand["auto"]
        if sb:                                # Statbotics' EPA breakdown stands in for the missing model
            b_ = SB_SRC + (f" [{sb['split']}]" if sb.get("split") else "")
            ap_ = _r(sb["auto"], 1)
            base["auto"] = dict(path=None, pts=dict(mean=ap_, sd=None, basis=b_), fuel=dict(mean=ap_, sd=None), routine=None,
                                n=0, stand_in=True, basis=b_)
            base["stand_in"] = True
            base["stand_in_src"] = "statbotics"
            base["epa"] = dict(auto=_r(sb["auto"], 1), teleop=_r(sb["teleop"], 1), tower=_r(sb["tower"], 1), basis=b_)
        elif am is not None:                    # event-average numbers the alliance prediction used: marked, not known
            b_ = f"event average (no model: {base['why']})"
            base["auto"] = dict(path=None, pts=dict(mean=_r(am * scale["auto"], 1), sd=None, basis=b_),
                                fuel=dict(mean=_r(am * scale["auto"], 1), sd=None), routine=None, n=0, stand_in=True, basis=b_)
            base["stand_in"] = True
            base["stand_in_src"] = "event_average"
        if spot:                              # not observed for this team: the station's usual spot at this event
            base["start"] = dict(xy=_fld(spot[0], al), side=B.side_of(spot[0][1]), n=0, stand_in=True,
                                 basis=f"station {station}'s usual spot at this event ({spot[1]} starts), not observed for this team")
        else:                                 # nothing seen: a fixed spot by station, from the driver's view
            xy = default_spot(al, station)
            base["start"] = dict(xy=xy, side=B.side_of(xy[1] if al == "red" else WID - xy[1]), n=0, stand_in=True, default=True,
                                 basis=f"default spot for station {station} ({STATION_NAME[station]}): no starts seen yet")
    else:
        a = dict(P["auto"])
        a["path"] = _fpath(B.resample(a["path"], 24).tolist(), al)
        a["pts"] = dict(mean=_r(a["pts"]["mean"], 1), sd=_r(a["pts"]["sd"], 1))
        a["fuel"] = dict(mean=_r(a["fuel"]["mean"], 1), sd=_r(a["fuel"]["sd"], 1))
        s = dict(P["start"]); s["xy"] = _fld(s["xy"], al)
        t = None
        cyc = P.get("cycle")
        if cyc:
            cyc = dict(cyc)
            cyc["path"] = _fpath(cyc["path"], al)
            cyc["per_min"] = _r(cyc["per_min"], 2)
            cyc["collect"] = dict(xy=_fld(cyc["collect"]["xy"], al), spread_m=_r(cyc["collect"]["spread_m"], 1))
            h = cyc["heat"]
            h = dict(h)
            h["cells"] = [[ix, iy, w] if al == "red" else [33 - ix, 16 - iy, w] for ix, iy, w in h["cells"]]
            cyc["heat"] = h
        tr = P.get("transition")
        if tr:
            tr = dict(tr); tr["path"] = _fpath(tr["path"], al); tr["scores_p"] = _r(tr["scores_p"], 2)
        base.update(enough=True, why=None, start=s, auto=a,
                    tele=dict(transition=tr, cycle=cyc, inactive=P.get("inactive")), partners=P["partners"])
    base["timeline"] = None
    if tlr:
        base["timeline"] = {k: v for k, v in tlr["timeline"].items() if not k.startswith("_")}
        if base.get("tele") is None:
            base["tele"] = dict(transition=None, cycle=None, inactive=None)
        base["tele"] = dict(base["tele"], cycles_per_shift=tlr["cycles_per_shift"], path_heat=tlr["path_heat"])
    base["actual"] = _actual_robot(rec_actual, al)
    if base["actual"] is not None:
        base["actual"]["timeline"] = TLM.actual_timeline(rec_actual, al)
    return base


def _timeline(tm, al, st, hist, flat, smap, teams, before):
    """Play-through for one robot (timeline.py), cached: the inputs of a played match never change."""
    h = hist.get(tm, [])
    if not h:
        return None
    partners = set(teams) - {tm}
    prior = [x for x in flat if x["station"] == st and x["team"] != tm]
    ck = (tm, st, al, tuple(x["key"] for x in h), tuple(sorted(partners)), tuple(x["key"] + x["team"] for x in prior), TLM.VERSION)
    if ck not in _TLC:
        if len(_TLC) > 6000:
            _TLC.clear()
        try:
            _TLC[ck] = TLM.team_timeline(h, st, partners, smap, al, prior=prior)
        except Exception:                      # a model failure must never stop the site build
            _TLC[ck] = None
    return _TLC[ck]


def _load_sb(data_dir, event, log=None):
    """Statbotics team data for stand-ins: short timeouts, the cache (even stale) when the API is down, None when there is
    nothing; never raises, so the build never waits on Statbotics for long."""
    try:
        sb = EPAMOD.fetch_statbotics(data_dir, event, max_age=3600, log=log, timeout=6)
    except Exception as e:                 # network/parse trouble must not stop the site build
        if log:
            log(f"[matchplan] statbotics unavailable: {e}")
        return None
    return sb if sb and (sb.get("team_events") or sb.get("team_matches")) else None


def build(data_dir, event, team="frc4911", out_dir=None, log=None, tba_path=None):
    """Write mp/*.json and patch schedule.json rows (video, mp).  Returns a small stats dict."""
    t_start = time.time()
    site = out_dir or os.path.join(data_dir, "site", "api", event)
    sched = _jload(os.path.join(site, "schedule.json"))
    if not sched or not sched.get("matches"):
        return {"matches": 0}
    tpath = tba_path or os.path.join(data_dir, "tba", f"event__{event}__matches.json")
    try:
        tba = load_tba(tpath) if os.path.exists(tpath) else []
    except Exception:
        tba = []
    tbk = {r["key"]: r for r in tba}
    th = _tower_hist(tba)
    eps = {}
    for e in (_jload(os.path.join(site, "defense.json"), {}) or {}).get("episodes", []):
        eps.setdefault(e.get("key"), []).append(e)
    info = _load_records(site, eps)
    sb = _load_sb(data_dir, event, log)
    rows = sorted(sched["matches"], key=lambda r: (r.get("time") or 0, r["key"]))
    # per-team history, built chronologically (a record only ever sees earlier matches)
    allrecs = [(info[r["key"]]["t_start"], r["key"], info[r["key"]]["recs"]) for r in rows if r["key"] in info]
    rtime = {x['key']: x.get('time') or 0 for x in rows}
    outdir = os.path.join(site, "mp")
    os.makedirs(outdir, exist_ok=True)
    index, vids = [], {}
    n_enough = n_rob = side_ok = side_n = role_ok = role_n = 0
    auto_err = []
    tl_scores, tl_matches = [], set()
    sc_err, cover, win = [], [], []
    for r in rows:
        key, t0 = r["key"], r.get("time") or 0
        before = [(k, rs) for _, k, rs in allrecs if k != key and rtime.get(k, 0) < t0]
        hist = {}
        flat = []
        for k, rs in before:
            for x in rs:
                hist.setdefault(x["team"], []).append(x)
                flat.append(x)
        smap = B.station_sides(flat)
        spots = {}
        for st_ in (1, 2, 3):
            ss = sorted((x["start"] for x in flat if x["station"] == st_ and x.get("start")), key=lambda q: q[0])
            if ss:
                spots[st_] = ([sorted(q[0] for q in ss)[len(ss) // 2], sorted(q[1] for q in ss)[len(ss) // 2]], len(ss))
        scale = _scale(before, tbk)
        stand = _stand_in(flat) if flat else {p: (None, None) for p in PERIODS}
        tr_rec = tbk.get(key)
        played = bool(r.get("played")) or bool(tr_rec and tr_rec["played"])
        cur = {x["team"]: x for x in (info.get(key) or {}).get("recs", [])}
        robots, side_pred = [], {}
        for al in ("red", "blue"):
            teams = r[al]
            for i, tm in enumerate(teams):
                P = B.team_plan(hist.get(tm, []), i + 1, set(teams) - {tm}, smap)
                robots.append((tm, al, i + 1, P))
        sbm = {}
        for tm, al, st, P in robots:
            if not P.get("enough") and sb:
                x = _sb_stand(EPAMOD.sb_asof(sb, tm, t0), stand, scale)
                if x:
                    sbm[tm] = x
        alls = {}
        for al in ("red", "blue"):
            rr = [(tm, P if P.get("enough") else None) for tm, a, _, P in robots if a == al]
            alls[al] = _predict_alliance(rr, th, t0, scale, stand, sbm)
        A, Bp = _finish(alls["red"][0], alls["red"][1], alls["blue"][0], alls["blue"][1],
                        alls["red"][2], alls["blue"][2])
        fl = [tbk[k][a]["fouls"] for k, _ in before for a in ("red", "blue") if k in tbk and tbk[k]["played"]]
        fouls = sum(fl) / len(fl) if fl else 0.0           # event-mean foul points an alliance receives
        for pr_ in (A, Bp):
            for kk in ("mean", "lo", "hi"):
                pr_["score"][kk] = round(pr_["score"][kk] + fouls, 1)
            pr_["fouls"] = round(fouls, 1)
        preds = {"red": A, "blue": Bp}
        ro = []
        for tm, al, st, P in robots:
            o = _robot_out(tm, al, st, P, len(hist.get(tm, [])), cur.get(tm) if played else None, stand, scale, spots.get(st), sbm.get(tm),
                           _timeline(tm, al, st, hist, flat, smap, r[al], before))
            ro.append(o)
            n_rob += 1
            n_enough += 1 if o["enough"] else 0
            c = cur.get(tm)
            if played and c and o.get("timeline"):
                sc_ = TLM.score(_timeline(tm, al, st, hist, flat, smap, r[al], before), c)
                if sc_:
                    tl_scores.append(sc_); tl_matches.add(key)
            if o["enough"] and c and played:
                if c.get("start"):
                    side_n += 1; side_ok += o["start"]["side"] == B.side_of(c["start"][1])
                if (c.get("inactive") or {}).get("role") and o["tele"]["inactive"]:
                    role_n += 1; role_ok += o["tele"]["inactive"]["role"] == c["inactive"]["role"]
                auto_err.append(abs((o["auto"]["fuel"]["mean"] or 0) - c["made"]["auto"]))
        al_out = {}
        for al in ("red", "blue"):
            miss = [dict(team=o["team"], why=o["why"]) for o in ro if o["alliance"] == al and not o["enough"]]
            pr = dict(preds[al])
            known = sum(1 for tm, a, _, P in robots if a == al and (P.get("enough") or sbm.get(tm)))
            if not known and all(v[0] is None for v in stand.values()):
                # nothing at all to go on (no tracked matches here, no Statbotics EPA, no event average yet): unknown, not 0
                pr = dict(score=dict(mean=None, lo=None, hi=None), auto=dict(mean=None, lo=None, hi=None), win_p=None,
                          periods=[dict(row, mean=None) for row in pr.get("periods") or []], unknown=True,
                          partial="no data yet: no tracked matches here and no Statbotics EPA")
                al_out[al] = dict(teams=r[al], enough=False, missing=miss, pred=pr,
                                  actual=_actual_alliance(tr_rec, al) if played else None)
                continue
            if alls[al][2]:
                nsb = sum(1 for o in ro if o["alliance"] == al and o.get("stand_in_src") == "statbotics")
                pr["partial"] = (f"{alls[al][2]} robot(s) without a model use " +
                                 (f"Statbotics EPA ({nsb}) / the event average ({alls[al][2] - nsb})" if nsb else "the event average"))
            al_out[al] = dict(teams=r[al], enough=not miss, missing=miss, pred=pr,
                              actual=_actual_alliance(tr_rec, al) if played else None)
        if played:
            for al in ("red", "blue"):
                a_ = al_out[al]
                if a_["enough"] and a_["actual"]:
                    sc_err.append(abs(a_["pred"]["score"]["mean"] - a_["actual"]["score"]))
                    cover.append(a_["pred"]["score"]["lo"] <= a_["actual"]["score"] <= a_["pred"]["score"]["hi"])
                    win.append((a_["pred"]["win_p"] > 0.5) == a_["actual"]["won"])
        us = "red" if team in r["red"] else ("blue" if team in r["blue"] else None)
        vid = (info.get(key) or {}).get("video") or r.get("video") or dict(youtube=None, local=None, clip=None)
        doc = dict(v=1, key=key, short=r.get("short"), label=r.get("label"), time=t0, played=played, us=us,
                   as_of=t0, video=vid, tracked=key in info, alliances=al_out, robots=ro)
        _dump(os.path.join(outdir, key + ".json"), doc)
        vids[key] = vid
        index.append(dict(key=key, short=r.get("short"), time=t0, played=played,
                          enough={a: al_out[a]["enough"] for a in al_out}, tracked=key in info,
                          video=any(vid.get(k) for k in ('youtube', 'local', 'clip', 'stream'))))
    f_ = lambda v: round(sum(v) / len(v), 3) if v else None
    calib = dict(n=len(sc_err), start_side_hit=f_([1.0] * side_ok + [0.0] * (side_n - side_ok)),
                 role_hit=f_([1.0] * role_ok + [0.0] * (role_n - role_ok)),
                 auto_fuel_mae=f_(auto_err), alliance_score_mae=round(sum(sc_err) / len(sc_err), 1) if sc_err else None,
                 score_range_cover=f_([float(x) for x in cover]), win_hit=f_([float(x) for x in win]),
                 n_robots_start=side_n, n_robots_role=role_n)
    calib["timeline"] = TLM.calib_summary(tl_scores)
    if calib["timeline"]:
        calib["timeline"]["n_matches"] = len(tl_matches)
    _dump(os.path.join(outdir, "index.json"), dict(v=1, generated=int(time.time()), calib=calib, matches=index))
    for r in sched["matches"]:
        r["video"] = vids.get(r["key"])
        r["mp"] = True
    _dump(os.path.join(site, "schedule.json"), sched, indent=None)
    st = dict(calib=calib, matches=len(rows), robots=n_rob, enough_pct=round(100 * n_enough / max(1, n_rob), 1),
              side_acc=round(side_ok / side_n, 3) if side_n else None, side_n=side_n,
              role_acc=round(role_ok / role_n, 3) if role_n else None, role_n=role_n,
              auto_fuel_mae=round(sum(auto_err) / len(auto_err), 2) if auto_err else None, secs=round(time.time() - t_start, 2))
    (log or (lambda s: None))(f"{event}: match plans {st}")
    return st


def _dump(p, o, indent=None):
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(o, f, separators=(",", ":"))
    os.replace(tmp, p)


def main(argv=None):
    a = list(argv if argv is not None else sys.argv[1:])
    team = "frc4911"
    if "--team" in a:
        i = a.index("--team"); team = a[i + 1]; del a[i:i + 2]
    print(json.dumps(build(a[0], a[1], team, log=print), indent=1))


if __name__ == "__main__":
    main()
