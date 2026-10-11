"""v2 per-match enrichment: fuel tracking -> shot reconstruction -> robot analytics.

Runs after process.process() on the same video (separate decode pass for now; the
fuel tracker can later ride process.detect_video's frame hooks to save the decode).

Outputs live next to the match JSON:
  data/events/<ev>/shots/<match>.json      Events (shots, intakes, reconcile) -- the fuel tracks
                                           themselves (~10 MB) are not kept
  data/events/<ev>/analytics/<match>.json  analytics.match_analytics() (team-agnostic; labels are
                                           joined at site build time)
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
    n_made = sum(1 for s in ev.get("shots", []) if s.get("outcome") == "made")
    log(f"  shots: {len(ev.get('shots', []))} ({n_made} made) fuel {b - a:.0f}s shots {c - b:.0f}s")
    return ev


def analytics_for(res, events, tba_match):
    rules = json.load(open(RULES))
    return AN.match_analytics(res, events, rules, tba_match)


def _jsave(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, separators=(",", ":"), default=lambda o: o.tolist() if isinstance(o, np.ndarray) else float(o))
    os.replace(tmp, p)


def enrich(ev_dir, key, video, cam_path, res, tba_match, log=print):
    """Full v2 enrichment for one processed match; writes shots/ and analytics/ files."""
    events = run_fuel_and_shots(video, cam_path, res, tba_match, log)
    _jsave(os.path.join(ev_dir, "shots", f"{key}.json"), events)
    _jsave(os.path.join(ev_dir, "analytics", f"{key}.json"), analytics_for(res, events, tba_match))
    return events
