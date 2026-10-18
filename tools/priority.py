#!/usr/bin/env python3
"""Processing order for the worker (events/<ev>/priority.json, live/worker.py prioritized()): the played matches
that show the teams of our next quals first.  Greedy: each team in our k-th upcoming match weighs 2^(n-1-k) (the
next match the most), halved for every match of it already in the list or tracked; a match scores the sum over its
teams.  The rest follow in time order (the worker's own order).

  cd <server>/app && DATA=<server>/data ../venv/bin/python ../app/tools/priority.py <event> [team]
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "live"))
from tba import TBA  # noqa: E402

EV = sys.argv[1]
TEAM = f"frc{sys.argv[2] if len(sys.argv) > 2 else 4911}"
DATA = os.environ["DATA"]
evd = os.path.join(DATA, "events", EV)
key = json.load(open(os.path.join(DATA, "config", "worker.json"))).get("tba_key")
ms = [m for m in TBA(key, os.path.join(DATA, "tba")).matches(EV)]
teams = lambda m: m["alliances"]["red"]["team_keys"] + m["alliances"]["blue"]["team_keys"]
played = lambda m: m["alliances"]["red"]["score"] is not None and m["alliances"]["red"]["score"] >= 0
t = lambda m: m.get("actual_time") or m.get("predicted_time") or m.get("time") or 0
ours = sorted([m for m in ms if TEAM in teams(m) and not played(m)], key=t)
n = len(ours)
w = {}
for i, m in enumerate(ours):
    for k in teams(m):
        if k != TEAM: w[k] = w.get(k, 0) + 2 ** (n - 1 - i)
done = {f[:-5] for f in os.listdir(os.path.join(evd, "matches"))} if os.path.isdir(os.path.join(evd, "matches")) else set()
seen = {}
for m in ms:
    if m["key"] in done:
        for k in teams(m): seen[k] = seen.get(k, 0) + 1
cands = [m for m in ms if played(m) and m["key"] not in done]
order = []
while cands:
    sc = lambda m: sum(w.get(k, 0) * 0.5 ** seen.get(k, 0) for k in teams(m))
    best = max(cands, key=lambda m: (sc(m), -t(m)))
    if sc(best) <= 0: break
    order.append(best["key"]); cands.remove(best)
    for k in teams(best): seen[k] = seen.get(k, 0) + 1
json.dump({"order": order, "for": [m["key"] for m in ours], "team": TEAM}, open(os.path.join(evd, "priority.json.tmp"), "w"), indent=1)
os.replace(os.path.join(evd, "priority.json.tmp"), os.path.join(evd, "priority.json"))
print(f"{len(order)} matches first, for {', '.join(m['key'].split('_')[1] for m in ours)}: {' '.join(k.split('_')[1] for k in order[:15])}"
      f"{' ...' if len(order) > 15 else ''}")
