"""Video coverage / trust rule shared by every page (dashboard ratings, spec sheets, picklist, defense library).

A team's robot-level video number may only come from **verified** matches:
  * the match was tracked by the v2 tracker (identity.method starts with "ocr"; v1 tracks are chance-level and
    feed nothing except the match replay, labelled "unidentified"), and
  * that team's track has team_conf >= VERIFIED_CONF.
Shots / frames below the quarantine threshold are already uncredited by enrich.quarantine(); this rule is the
match-level gate on top (it also closes the hole where a match without per-frame confidence -- e.g. a v1 match
that went through enrich -- would credit everything).

Display policy (the strategist reviews, docs/STRATEGIST_REVIEW.md):
  n >= SHOW_N verified matches  -> show as fact
  1 <= n < SHOW_N               -> grey, labelled "n=2 (Q12, Q17)"
  n == 0                        -> hidden ("no verified video")
Every video number also carries "as of" the last verified match.

Works on either the pipeline's match JSON (DATA/events/<ev>/matches/<key>.json) or the site's m/<key>.json
(both have identity.method and tracks[].team / team_conf).
"""
import glob, json, os, re

VERIFIED_CONF = 0.6
SHOW_N = 3


def is_v2(match):
    return str(((match or {}).get("identity") or {}).get("method") or "").startswith("ocr")


def verified_teams(match):
    """{team_key: team_conf} for teams whose track in this match is verified (empty for v1 matches)."""
    if not is_v2(match):
        return {}
    out = {}
    for t in match.get("tracks") or []:
        tk, c = t.get("team"), t.get("team_conf")
        if tk and c is not None and c >= VERIFIED_CONF:
            out[tk] = max(out.get(tk, 0.0), float(c))
    return out


def team_coverage(matches, played=None):
    """matches: iterable of match dicts (with "key").  played: optional {team_key: n matches played (TBA)}.
    -> {team_key: {"n": verified matches, "keys": [...], "last": last key, "of": played or None,
                   "tier": "fact" | "grey" | "none"}}"""
    cov = {}
    for m in matches:
        for tk in verified_teams(m):
            c = cov.setdefault(tk, {"n": 0, "keys": []})
            c["n"] += 1; c["keys"].append(m.get("key"))
    for tk, c in cov.items():
        c["last"] = c["keys"][-1] if c["keys"] else None
        c["of"] = (played or {}).get(tk)
        c["tier"] = tier(c["n"])
    return cov


def tier(n):
    return "fact" if n >= SHOW_N else "grey" if n >= 1 else "none"


def match_order(key):
    """Sort key for match keys in play order: quals, then playoffs (sf/f) by set and match number."""
    k = str(key or "").split("_")[-1]

    m = re.match(r"([a-z]+)(\d+)(?:m(\d+))?", k)
    if not m:
        return (9, 0, 0)
    lvl = {"qm": 0, "ef": 1, "qf": 2, "sf": 3, "f": 4}.get(m.group(1), 5)
    return (lvl, int(m.group(2)), int(m.group(3) or 0))


GATE = float(os.environ.get("RECON_GATE", "0.2"))


def reconcile(match, unattributed_max=None, over_tol=0.03):
    """Credited robot FUEL vs the TBA alliance count for one verified (v2) match.
    -> {alliance: {"tba", "credited", "human", "unattributed", "lower_bound", "over", "scale", "teams": {team: made}}}
    `credited` = made shots credited to a verified team; `human` = made by the human player (from the shot reconcile),
    capped at tba - credited; unattributed = 1 - (credited + human_capped) / tba.  gate: "ok" | "under" (> 20% of the
    alliance FUEL uncredited) | "over" (robots credited > 120% of TBA) -- only "ok" results feed team FUEL stats.  lower_bound: > unattributed_max of the alliance's FUEL is not credited to
    any robot, so a robot's count is a floor ("≥X") and must never be read as "under-rated".  over: credited + human
    exceeds TBA (double counting) -> scale = tba / (credited + human) shrinks the robots' counts to the TBA total."""
    unattributed_max = GATE if unattributed_max is None else unattributed_max
    ver = verified_teams(match)
    rec = match.get("reconcile") or {}
    tracks = match.get("tracks") or []
    out = {}
    for al in ("red", "blue"):
        r = rec.get(al) or {}
        tba = (r.get("total") or {}).get("tba")
        human = int((r.get("by_shooter") or {}).get(f"human:{al}", 0) or 0)
        per = {}
        for s in match.get("shots") or []:
            sh = s.get("shooter") or {}
            if sh.get("kind") != "robot" or s.get("outcome") != "made":
                continue
            k = sh.get("track_idx")
            tm = s.get("team")
            if k is None or k >= len(tracks) or tracks[k].get("alliance") != al or not tm or tm not in ver:
                continue
            if tracks[k].get("team") != tm:
                continue
            per[tm] = per.get(tm, 0) + 1
        cred = sum(per.values())
        if not tba:
            out[al] = {"tba": tba, "credited": cred, "human": human, "unattributed": None, "lower_bound": True,
                       "over": False, "scale": 1.0, "teams": per, "gate": "no_tba"}
            continue
        hcap = max(0, min(human, tba - cred))            # human-player credit capped at TBA - credited robots
        ratio = (cred + hcap) / float(tba)
        un = 1.0 - ratio
        over = cred > tba * (1 + over_tol)
        gate = "under" if ratio < 1 - unattributed_max else "over" if cred > tba * (1 + unattributed_max) else "ok"
        out[al] = {"tba": tba, "credited": cred, "human": human, "human_capped": hcap, "ratio": round(ratio, 3),
                   "unattributed": round(max(0.0, un), 3), "lower_bound": un > unattributed_max, "over": bool(over),
                   "scale": round(tba / float(cred), 3) if over and cred else 1.0, "teams": per,
                   "gate": gate}   # per-match gate for team FUEL stats: only "ok" alliance results feed them
    return out


def load_matches(match_dir):
    """All match JSONs in a directory, sorted by file name order of play is NOT guaranteed -- callers that need
    "last" in play order should sort by the match's TBA time."""
    out = []
    for p in sorted(glob.glob(os.path.join(match_dir, "*.json"))):
        if p.endswith(".fuel.json"):          # fuel tracks sidecar, not a match
            continue
        try:
            with open(p) as f:
                out.append(json.load(f))
        except (OSError, ValueError):
            pass
    return out


def label(c, short=lambda k: (k or "").split("_")[-1].upper()):
    """'n=2 (QM12, QM17)' style label for a coverage row (None -> 'no verified video')."""
    if not c or not c.get("n"):
        return "no verified video"
    ks = ", ".join(short(k) for k in c["keys"][-4:])
    return f"n={c['n']}{'/' + str(c['of']) if c.get('of') else ''} ({ks})"
