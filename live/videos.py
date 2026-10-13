"""Find and download the full-frame field-camera video for a match.

Preference order (configurable per event in events/<key>/event.json):
  1. a TBA-linked video whose title matches `prefer` (e.g. "^Wide Match")
  2. a TBA-linked video from an `allow_channels` channel (FIRST HQ's per-match uploads)
  3. a YouTube search on that channel for "<match title> - <event name>"
Videos matching `reject` (e.g. layouts with picture-in-picture insets) are skipped.
"""
import json, os, re, subprocess

YTDLP = os.environ.get("YTDLP", "yt-dlp")
# The server's IPv6 egress gets "Sign in to confirm you're not a bot" while IPv4 works, so force IPv4
# first (YT_FORCE_IPV4=0 disables; the last download attempt always tries without it as a fallback).
IPV4 = os.environ.get("YT_FORCE_IPV4", "1") != "0"
POLITE = ["--sleep-requests", "1", "--retry-sleep", "5"]
YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")      # TBA video keys are only ever used as YouTube ids


def _vid(vid):
    if not isinstance(vid, str) or not YT_ID.match(vid):
        raise ValueError(f"not a YouTube video id: {str(vid)[:40]!r}")
    return vid


def _net(v4=True):
    return ["-4"] if (IPV4 and v4) else []


def meta(vid):
    if not isinstance(vid, str) or not YT_ID.match(vid): return None
    r = subprocess.run([YTDLP, *_net(), "--skip-download", "--no-warnings", "--print",
                        "%(channel)s\t%(title)s\t%(duration)s\t%(height)s", "--", f"https://youtu.be/{vid}"],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0 or "\t" not in r.stdout:
        return None
    ch, title, dur, h = r.stdout.strip().split("\n")[-1].split("\t")
    return dict(id=vid, channel=ch, title=title, duration=float(dur) if dur not in ("NA", "") else 0,
                height=int(h) if h.isdigit() else 0)


def search(query, n=6):
    r = subprocess.run([YTDLP, "--flat-playlist", "--no-warnings", "--print",
                        "%(id)s\t%(channel)s\t%(title)s", "--", f"ytsearch{int(n)}:{query}"],
                       capture_output=True, text=True, timeout=120)
    out = []
    for line in r.stdout.strip().split("\n"):
        p = line.split("\t")
        if len(p) == 3 and YT_ID.match(p[0]): out.append(dict(id=p[0], channel=p[1], title=p[2]))
    return out


def choose(match, cfg, cache):
    c = candidates(match, cfg, cache)
    return c[0] if c else None


def candidates(match, cfg, cache):
    """-> acceptable videos, best first ([] if none yet).  `cache` memoises metadata."""
    prefer = re.compile(cfg.get("prefer", r"^Wide Match"), re.I)
    reject = re.compile(cfg.get("reject", r"^Match \d"), re.I)
    chans = set(cfg.get("allow_channels", ["FIRSTRoboticsCompetition"]))
    cands = []
    for v in match.get("videos", []):
        if v.get("type") != "youtube": continue
        vid = str(v.get("key") or "").split("?")[0].strip()
        if not YT_ID.match(vid): continue             # never hand a non-id TBA value to yt-dlp
        if vid not in cache:
            cache[vid] = meta(vid)
        if cache[vid]: cands.append(cache[vid])
    ok = [c for c in cands if not reject.search(c["title"]) and c["duration"] < 600]
    ranked = [c for c in ok if prefer.search(c["title"])] + \
             [c for c in ok if not prefer.search(c["title"]) and c["channel"] in chans]
    if ranked:
        return ranked
    title = cfg.get("search_title")
    if title:
        mf = match_fields(match)
        q = title.format(**mf)
        head, _, tail = q.partition(mf["name"])
        # FIRST titles playoff videos like "Match 8 (R2) - <event>": allow a suffix after the name
        pat = re.compile("^" + re.escape(head + mf["name"]) + r"(\s*\([^)]*\))?" + re.escape(tail) + "$", re.I)
        return [c for c in search(q) if c["channel"] in chans and pat.match(c["title"].strip())][:2]
    return []


def match_fields(m):
    lv = m["comp_level"]
    name = {"qm": f"Qualification {m['match_number']}", "f": f"Final {m['match_number']}",
            "sf": f"Match {m['set_number']}"}.get(lv, m["key"])
    return dict(name=name, set=m["set_number"], num=m["match_number"], level=lv)


def download(vid, out, attempts=4):
    """yt-dlp with retries: YouTube intermittently answers 403 (rate limits, stale player JS)."""
    import time, random
    _vid(vid)
    tmp = out + ".part.mp4"; err = ""
    for i in range(attempts):
        r = subprocess.run([YTDLP, *_net(i < attempts - 1 or attempts == 1), *POLITE, "-q", "--no-warnings", "-f", "bv*[height<=1080][ext=mp4]/bv*[height<=1080]",
                            "-o", tmp, "--force-overwrites", "--", f"https://youtu.be/{vid}"],
                           capture_output=True, text=True, timeout=1800)
        if r.returncode == 0 and os.path.exists(tmp):
            os.replace(tmp, out)
            return out
        err = r.stderr[-400:]
        time.sleep(10 * (i + 1) + random.uniform(0, 10))
    raise RuntimeError(f"yt-dlp failed for {vid} after {attempts} attempts: {err}")
