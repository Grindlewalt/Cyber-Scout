"""Export one match's station-camera data for the site (Analyze view camera switcher).

    python live/station_export.py DATA_DIR MEDIA_DIR EVENT MATCH_KEY

Writes data/events/<ev>/stations/<key>.json: the inset video (media path), sync to the overhead
video (seconds to ADD to inset time to get overhead-video time; piecewise knots when it drifts),
and both station cameras (calibration + this match's pose, in inset-crop pixels).
"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
import stationcam as SC


def export(data, media, ev, key, log=print):
    evd = os.path.join(data, "events", ev)
    inset = os.path.join(media, ev, "insets", f"{key}.mp4")
    wide = os.path.join(media, ev, f"{key}.mp4")
    if not (os.path.exists(inset) and os.path.exists(wide)):
        raise FileNotFoundError(f"need {wide} and {inset}")
    edir = evd if os.path.exists(os.path.join(evd, "station_red.json")) else os.path.join(os.path.dirname(HERE), "events", ev)
    sc = SC.StationCams(edir, os.path.join(evd, "matches", f"{key}.json"), inset, wide_video=wide, log=log)
    cams = {}
    for w, cam in sc.cam.items():
        r = cam.raw
        cams[w] = {"W": cam.W, "H": cam.H, "K": cam.K.tolist(), "dist": cam.D.tolist(),
                   "rvec": cam.rvec.tolist(), "tvec": cam.tvec.tolist(), "crop": r.get("crop"),
                   "usable": bool(sc.usable.get(w)), "pose": {k: v for k, v in sc.pose_info.get(w, {}).items()
                                                             if isinstance(v, (int, float, bool, str))}}
    s = sc.sync
    out = {"video": f"media/{ev}/insets/{key}.mp4",
           "sync": {"offset": s["offset"], "knots": s["knots"], "constant": s["constant"]} if isinstance(s, dict)
                   else {"offset": float(s), "knots": [[0.0, float(s)]], "constant": True},
           "cams": cams}
    os.makedirs(os.path.join(evd, "stations"), exist_ok=True)
    json.dump(out, open(os.path.join(evd, "stations", f"{key}.json"), "w"))
    log(f"{key}: offset {out['sync']['offset']} usable {[(w, c['usable']) for w, c in cams.items()]}")
    return out


if __name__ == "__main__":
    export(*sys.argv[1:5])
