#!/usr/bin/env python3
"""Build site/demo/: an offline, no-login copy of the real Cyber-Scout web app with frozen event data.

    python3 site/tools/build_demo.py                       # web/ + .work/site_staging/api -> site/demo
    python3 site/tools/build_demo.py --data /path/to/data/site/api --matches qm12,qm17,qm40

What it does
  * copies the app from web/ unchanged (index.html, page scripts, CSS, theme fonts, field image), minus the
    service worker and the sync engine (sw.js, sync.js): the demo has no server to sync with;
  * adds three small files from site/tools/demo/ (demo-boot.js answers the app's API calls in the browser with a
    signed-in "Demo visitor", demo-tail.js shows "not in the demo" for pages whose data isn't copied, demo.css)
    plus a generated demo-config.js (event, replay time, which teams / matches / snapshots are included);
  * copies the event's site JSON (minified): event, schedule, rankings, selection, scout, defense, every team page
    and spec sheet, the replay snapshots (dash/, selection/, scout/), and a few match pages with the video
    references removed (the match page then opens in field view).

Stdlib only. Re-run it after changing web/ or to refresh the data; the output folder is replaced.
"""
import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SITE = HERE.parent
REPO = SITE.parent
MARK = ".cyberscout-demo"          # marker file: only a folder this script made is ever deleted

SKIP_WEB = {"sw.js", "sync.js", "Dockerfile", "nginx.conf", "manifest.webmanifest", ".dockerignore"}
DEFAULT_MATCHES = "qm12,qm17,qm40"


def minify(src: Path, dst: Path, edit=None) -> int:
    d = json.loads(src.read_text(encoding="utf-8"))
    if edit:
        d = edit(d)
    dst.parent.mkdir(parents=True, exist_ok=True)
    txt = json.dumps(d, separators=(",", ":"), ensure_ascii=False)
    dst.write_text(txt, encoding="utf-8")
    return len(txt.encode("utf-8"))


def strip_video(d: dict) -> dict:
    """Match JSON without local media: the app then shows the field view (the YouTube link stays)."""
    d.pop("video_local", None)
    st = d.get("stations")
    if isinstance(st, dict):
        st.pop("video", None)
    lv = d.get("live")
    if isinstance(lv, dict):
        lv.pop("clip", None)
    return d


def patch_index(html: str) -> str:
    # no sync engine / service worker, and not installable as the app
    html = re.sub(r'\s*<script src="sync\.js"></script>', "", html)
    html = re.sub(r'\s*<link rel="manifest"[^>]*>', "", html)
    # boot shim right after theme.js (before any page script), tail after the last script
    html = html.replace('<script src="theme.js"></script>',
                        '<script src="theme.js"></script>\n<script src="demo-config.js"></script>\n<script src="demo-boot.js"></script>', 1)
    html = html.replace('<link rel="stylesheet" href="themes.css">',
                        '<link rel="stylesheet" href="themes.css">\n<link rel="stylesheet" href="demo.css">', 1)
    html = html.replace("</body>", '<script src="demo-tail.js"></script>\n</body>', 1)
    html = html.replace("<title>Cyber-Scout</title>", "<title>Cyber-Scout demo</title>", 1)
    html = html.replace('<meta name="viewport"',
                        '<meta name="description" content="Live demo of Cyber-Scout, autonomous FRC scouting from the broadcast video, with frozen data from PNW District Sammamish 2026.">\n<meta name="robots" content="noindex">\n<meta name="viewport"', 1)
    for need in ("demo-boot.js", "demo-tail.js", "demo.css"):
        if need not in html:
            sys.exit(f"build_demo: could not patch web/index.html for {need}: its structure changed; update patch_index()")
    return html


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--web", default=str(REPO / "web"), help="the app's web/ folder (default: repo web/)")
    ap.add_argument("--data", default=str(REPO / ".work" / "site_staging" / "api"),
                    help="a site api/ folder: events.json + <event>/... (e.g. a copy of data/site/api from the server)")
    ap.add_argument("--out", default=str(SITE / "demo"), help="output folder (default: site/demo)")
    ap.add_argument("--event", default="2026wasam")
    ap.add_argument("--ts", default="1774195200", help="replay snapshot the demo opens at (default Sun 09:00)")
    ap.add_argument("--matches", default=DEFAULT_MATCHES, help=f"match pages to include, comma separated (default {DEFAULT_MATCHES})")
    ap.add_argument("--snapshots", choices=["all", "one"], default="all",
                    help="all = every replay snapshot (the time control works), one = only --ts")
    a = ap.parse_args()

    web, data, out, ev = Path(a.web), Path(a.data), Path(a.out), a.event
    evd = data / ev
    for need in (web / "index.html", data / "events.json", evd / "event.json", evd / "dash" / f"{a.ts}.json"):
        if not need.exists():
            sys.exit(f"build_demo: missing {need}")

    if out.exists():
        if not (out / MARK).exists() and any(out.iterdir()):
            sys.exit(f"build_demo: {out} exists and was not made by this script; refusing to replace it")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / MARK).write_text("made by site/tools/build_demo.py; replaced on every build\n")

    # ---- the app
    for f in sorted(web.iterdir()):
        if f.name in SKIP_WEB or f.name.startswith("."):
            continue
        if f.is_dir():
            shutil.copytree(f, out / f.name)          # fonts/, icons/, ...
        elif f.suffix in (".js", ".css", ".jpg", ".png", ".webp", ".svg", ".ico", ".woff2"):
            shutil.copy2(f, out / f.name)
    (out / "index.html").write_text(patch_index((web / "index.html").read_text(encoding="utf-8")), encoding="utf-8")
    for f in ("demo-boot.js", "demo-tail.js", "demo.css"):
        shutil.copy2(HERE / "demo" / f, out / f)

    # ---- data
    api = out / "api"
    n = minify(data / "events.json", api / "events.json")
    for f in ("event.json", "schedule.json", "rankings.json", "selection.json", "scout.json", "defense.json", "dashboard.json"):
        if (evd / f).exists():
            n += minify(evd / f, api / ev / f)

    # replay snapshots: dash/<ts>.json drives the time control; selection/ and scout/ have matching files
    dash_idx = json.loads((evd / "dash" / "index.json").read_text())
    if a.snapshots == "one":
        dash_idx = [t for t in dash_idx if str(t["ts"]) == a.ts]
    times = [str(t["ts"]) for t in dash_idx if (evd / "dash" / f"{t['ts']}.json").exists()]
    dash_idx = [t for t in dash_idx if str(t["ts"]) in times]
    (api / ev / "dash").mkdir(parents=True, exist_ok=True)
    (api / ev / "dash" / "index.json").write_text(json.dumps(dash_idx, separators=(",", ":")))
    for t in times:
        n += minify(evd / "dash" / f"{t}.json", api / ev / "dash" / f"{t}.json")
        for sub in ("selection", "scout"):
            if (evd / sub / f"{t}.json").exists():
                n += minify(evd / sub / f"{t}.json", api / ev / sub / f"{t}.json")
    for sub in ("selection", "scout"):
        idx = evd / sub / "index.json"
        if idx.exists():
            def keep(d, have=set(times)):
                if isinstance(d, dict) and isinstance(d.get("times"), list):
                    d["times"] = [x for x in d["times"] if str(x) in have]
                return d
            n += minify(idx, api / ev / sub / "index.json", keep)

    teams = []
    for sub in ("t", "spec"):
        for f in sorted((evd / sub).glob("*.json")):
            n += minify(f, api / ev / sub / f.name)
            if sub == "t":
                teams.append(f.stem)

    matches = []
    for m in [x.strip() for x in a.matches.split(",") if x.strip()]:
        key = m if m.startswith(ev + "_") else f"{ev}_{m}"
        src = evd / "m" / f"{key}.json"
        if not src.exists():
            print(f"build_demo: no match file for {key}, skipped", file=sys.stderr)
            continue
        n += minify(src, api / ev / "m" / src.name, strip_video)
        fuel = evd / "m" / f"{key}.fuel.json"
        if fuel.exists():
            n += minify(fuel, api / ev / "m" / fuel.name)
        matches.append(key)

    # ---- config for the boot / tail scripts
    e = json.loads((evd / "event.json").read_text())
    comp = {"key": ev, "name": e.get("name"), "short_name": e.get("short_name"), "start_date": e.get("start"),
            "end_date": e.get("end"), "city": e.get("city"), "state_prov": e.get("state_prov"),
            "n_done": e.get("n_done"), "n_total": e.get("n_total"), "needs_setup": False, "updated": e.get("updated")}
    cfg = {"ev": ev, "ts": a.ts, "times": times, "teams": teams, "matches": matches, "events": [comp],
           "built": time.strftime("%Y-%m-%d %H:%M", time.localtime())}
    (out / "demo-config.js").write_text("/* generated by site/tools/build_demo.py */\nwindow.DEMO = " +
                                       json.dumps(cfg, separators=(",", ":")) + ";\n")

    total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"site/demo: {len(times)} snapshots, {len(teams)} teams, {len(matches)} match pages; "
          f"data {n / 1e6:.1f} MB, total {total / 1e6:.1f} MB -> {out}")


if __name__ == "__main__":
    main()
