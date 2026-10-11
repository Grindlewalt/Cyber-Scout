import os, sys, io, contextlib
KEY = os.environ["RF"]
from roboflow import Roboflow
JOBS = [("frcrobots","frc-robots-1zts0",5,"frc"), ("6036","6036-ai-htnmi",2,"t6036"),
        ("grain-dzomt","scouting-cqm98",7,"grain"), ("frc4419","frc-robots-equcn",3,"f4419"),
        ("capstone-roipa","frc-automatic-scouting",30,"capstone"), ("-gw5oi","scouting-v7cfq",9,"scout_v7")]
rf = Roboflow(api_key=KEY)
for ws, pr, ver, tag in JOBS:
    d = f"rf/{tag}"
    if os.path.isdir(d) and os.path.exists(f"{d}/data.yaml"): print("cached", tag); continue
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rf.workspace(ws).project(pr).version(ver).download("yolov8", location=d)
        print("ok", tag)
    except Exception as e:
        print("FAIL", tag, repr(e).replace(KEY, "<KEY>")[:300])
