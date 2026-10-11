"""v2 per-match enrichment: fuel tracking -> shot reconstruction -> robot analytics.

Runs after process.process() on the same video (separate decode pass for now; the
fuel tracker can later ride process.detect_video's frame hooks to save the decode).

Outputs live next to the match JSON:
  data/events/<ev>/shots/<match>.json      Events (shots, intakes, reconcile) -- the fuel tracks
                                           themselves (~10 MB) are not kept
  data/events/<ev>/analytics/<match>.json  analytics.match_analytics() (team-agnostic; labels are
                                           joined at site build time)
  data/events/<ev>/fuel/<match>.json       airborne fuel tracks (image px) for the interactive player
"""
import json, os, time
import numpy as np

import camera as CAMM
import fuel as FUEL
import shots as SHOTS
import analytics as AN

HERE = os.path.dirname(os.path.abspath(__file__))
RULES = os.path.join(HERE, "field", "rebuilt_rules.json")


def match_camera(cam_path, res):
    raw = json.load(open(cam_path))
    if res.get("camera_pose"):
        raw = dict(raw, rvec=res["camera_pose"]["rvec"], tvec=res["camera_pose"]["tvec"])
    return CAMM.Camera(raw)


def run_fuel_and_shots(video, cam_path, res, tba_match, log=print):
    """-> Events dict (shots/intakes/reconcile/stats), timing in events['timing']."""
    cam = match_camera(cam_path, res)
    t0 = res["t_start"] - 1.0
    t1 = res["t_start"] - 0.5 + res["frames"] / 30.0 + 3.0       # balls still in flight at the buzzer
    a = time.time()
    fuel = FUEL.track_fuel(video, cam, t0, t1, log=log, cv_threads=1)
    b = time.time()
    ev = SHOTS.reconstruct(fuel, res, cam, RULES, tba_match, log=log)
    c = time.time()
    try:
        import intakes as INT                                    # optional module (in development)
        ev["intakes"] = INT.detect_intakes(video, fuel, res, cam, json.load(open(RULES)), log)
    except ImportError:
        pass
    except Exception as e:                                       # never lose a match over intakes
        log(f"  intakes failed: {e}")
    ev["timing"] = {"fuel_s": round(b - a), "shots_s": round(c - b), "fuel_tracks": len(fuel.get("tracks", []))}
    ev["_fuel_air"] = airborne_tracks(fuel)
    n_made = sum(1 for s in ev.get("shots", []) if s.get("outcome") == "made")
    log(f"  shots: {len(ev.get('shots', []))} ({n_made} made) fuel {b - a:.0f}s shots {c - b:.0f}s")
    return ev


def airborne_tracks(fuel, min_score=0.5):
    """Compact airborne fuel tracks for the interactive player: what the tracker actually saw."""
    out = []
    for t in fuel.get("tracks", []):
        if t.get("airborne_score", 0) < min_score or not t.get("view_ok", 1) or len(t.get("t", [])) < 3:
            continue
        out.append({"id": t["id"], "t": [round(x, 3) for x in t["t"]],
                    "uv": [[round(u), round(v)] for u, v in t["uv"]], "s": round(t["airborne_score"], 2)})
    return out


ID_CONF_MIN = float(os.environ.get("ID_CONF_MIN", "0.5"))


def quarantine(res, thr=ID_CONF_MIN):
    """Copy of the match where frames with identity conf < thr are unobserved: a team's
    stats then only use stretches where we are confident it is that team."""
    out = dict(res); tr = []
    for t in res["tracks"]:
        cf = t.get("conf")
        if cf and len(cf) == len(t["obs"]):
            t = dict(t, obs=[int(o and c >= thr) for o, c in zip(t["obs"], cf)])
        tr.append(t)
    out["tracks"] = tr
    return out


def analytics_for(res, events, tba_match):
    rules = json.load(open(RULES))
    return AN.match_analytics(quarantine(res), events, rules, tba_match)


def _jsave(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, separators=(",", ":"), default=lambda o: o.tolist() if isinstance(o, np.ndarray) else float(o))
    os.replace(tmp, p)


def enrich(ev_dir, key, video, cam_path, res, tba_match, log=print):
    """Full v2 enrichment for one processed match; writes shots/ and analytics/ files."""
    events = run_fuel_and_shots(video, cam_path, res, tba_match, log)
    _jsave(os.path.join(ev_dir, "fuel", f"{key}.json"), {"tracks": events.pop("_fuel_air", [])})
    _jsave(os.path.join(ev_dir, "shots", f"{key}.json"), events)
    _jsave(os.path.join(ev_dir, "analytics", f"{key}.json"), analytics_for(res, events, tba_match))
    return events


def station_pass(data_dir, media, ev, key, res, tba_match, meta_cache=None, log=print):
    """OPTIONAL (HUBCAM=1): driver-station cameras for one match -- fetch the "Match N" inset upload,
    export the station cameras for the Analyze camera switcher, verify shot outcomes from the hub
    side views (live/hubcam.py) and refresh this match's shots + analytics.  ~1-2 min per match."""
    import stationcam as SC, station_export as SX, hubcam as HC, videos
    evd = os.path.join(data_dir, "events", ev)
    wide = os.path.join(media, ev, f"{key}.mp4")
    inset = os.path.join(media, ev, "insets", f"{key}.mp4")
    if not os.path.exists(inset):
        v = SC.find_inset_video(tba_match, meta_cache if meta_cache is not None else {})
        if not v:
            log("  stations: no inset upload for this match"); return None
        os.makedirs(os.path.dirname(inset), exist_ok=True)
        videos.download(v["id"], inset)
    st = SX.export(data_dir, media, ev, key, log=log)                  # sync + per-match station poses
    shots_p, fuel_p = os.path.join(evd, "shots", f"{key}.json"), os.path.join(evd, "fuel", f"{key}.json")
    if not (os.path.exists(shots_p) and os.path.exists(fuel_p)):
        return st
    cal = evd if os.path.exists(os.path.join(evd, "station_red.json")) else os.path.join(HERE, "..", "events", ev)
    events = json.load(open(shots_p))
    ev2 = HC.run_match(cal, res, events, json.load(open(fuel_p)), wide, inset, tba_match, sync=st["sync"], log=log)
    _jsave(shots_p, ev2)
    _jsave(os.path.join(evd, "analytics", f"{key}.json"), analytics_for(res, ev2, tba_match))
    log(f"  hubcam: {ev2.get('hubcam')}")
    return st
