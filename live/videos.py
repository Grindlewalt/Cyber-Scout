"""Find and download the full-frame field-camera video for a match.

Preference order (configurable per event in events/<key>/event.json):
  1. a TBA-linked video whose title matches `prefer` (e.g. "^Wide Match")
  2. a TBA-linked video from an `allow_channels` channel (FIRST HQ's per-match uploads)
  3. a YouTube search on that channel for "<match title> - <event name>"
Videos matching `reject` (e.g. layouts with picture-in-picture insets) are skipped.
"""
import json, os, re, subprocess

YTDLP = os.environ.get("YTDLP", "yt-dlp")


def meta(vid):
    r = subprocess.run([YTDLP, "--skip-download", "--no-warnings", "--print",
                        "%(channel)s\t%(title)s\t%(duration)s\t%(height)s", f"https://youtu.be/{vid}"],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0 or "\t" not in r.stdout:
        return None
    ch, title, dur, h = r.stdout.strip().split("\n")[-1].split("\t")
    return dict(id=vid, channel=ch, title=title, duration=float(dur) if dur not in ("NA", "") else 0,
                height=int(h) if h.isdigit() else 0)


def search(query, n=6):
    r = subprocess.run([YTDLP, "--flat-playlist", "--no-warnings", "--print",
                        "%(id)s\t%(channel)s\t%(title)s", f"ytsearch{n}:{query}"],
                       capture_output=True, text=True, timeout=120)
    out = []
    for line in r.stdout.strip().split("\n"):
        p = line.split("\t")
        if len(p) == 3: out.append(dict(id=p[0], channel=p[1], title=p[2]))
    return out


def choose(match, cfg, cache):
    """-> dict(id, title, channel) or None.  `cache` memoises yt-dlp metadata."""
    prefer = re.compile(cfg.get("prefer", r"^Wide Match"), re.I)
    reject = re.compile(cfg.get("reject", r"^Match \d"), re.I)
    chans = set(cfg.get("allow_channels", ["FIRSTRoboticsCompetition"]))
    cands = []
    for v in match.get("videos", []):
        if v.get("type") != "youtube": continue
        vid = v["key"].split("?")[0]
        if vid not in cache:
            cache[vid] = meta(vid)
        if cache[vid]: cands.append(cache[vid])
    ok = [c for c in cands if not reject.search(c["title"]) and c["duration"] < 600]
    for c in ok:
        if prefer.search(c["title"]): return c
    for c in ok:
        if c["channel"] in chans: return c
    title = cfg.get("search_title")
    if title:
        mf = match_fields(match)
        q = title.format(**mf)
        head, _, tail = q.partition(mf["name"])
        # FIRST titles playoff videos like "Match 8 (R2) - <event>": allow a suffix after the name
        pat = re.compile("^" + re.escape(head + mf["name"]) + r"(\s*\([^)]*\))?" + re.escape(tail) + "$", re.I)
        for c in search(q):
            if c["channel"] in chans and pat.match(c["title"].strip()):
                return c
    return None


def match_fields(m):
    lv = m["comp_level"]
    name = {"qm": f"Qualification {m['match_number']}", "f": f"Final {m['match_number']}",
            "sf": f"Match {m['set_number']}"}.get(lv, m["key"])
    return dict(name=name, set=m["set_number"], num=m["match_number"], level=lv)


def download(vid, out):
    tmp = out + ".part.mp4"
    r = subprocess.run([YTDLP, "-q", "--no-warnings", "-f", "bv*[height<=1080][ext=mp4]/bv*[height<=1080]",
                        "-o", tmp, "--force-overwrites", f"https://youtu.be/{vid}"],
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0 or not os.path.exists(tmp):
        raise RuntimeError(f"yt-dlp failed for {vid}: {r.stderr[-400:]}")
    os.replace(tmp, out)
    return out
