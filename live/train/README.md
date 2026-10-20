# Training the robot detector locally (`cstrain.py`)

Everything runs on our own machines: frames from our own match videos, labels from our own models plus human review,
training on atomosserver's GPUs (GPU 0 = RTX 3060 Ti 8 GB, GPU 1 = RTX 3060 12 GB). No Roboflow, no Kaggle. A
**project** is one season (its class list is fixed: a new game = a new project), so the same commands train the
2027 detector.

```bash
bash live/train/setup_env.sh                        # once: /mnt/nas/autoscout-scratch/trainenv (NAS only)
export TRAIN_DIR=/mnt/nas/autoscout-scratch/train   # the default
T="/mnt/nas/autoscout-scratch/trainenv/bin/python live/train/cstrain.py"
$T status --project 2026rebuilt
```

Run it from a checkout on the server (it imports `live/camera.py`, `live/bglabel.py`, `live/detector.py`). Prefix
long jobs with `ulimit -c 0;`. Base weights (`yolo11s.pt`, `yolo11m.pt`) download once into `$TRAIN_DIR/weights`.

## Layout of a project

```
$TRAIN_DIR/<project>/
  project.json             {name, classes, created, notes}
  frames/<event>/<id>.jpg  full video frames (q92); id = <event>_<match>_<t ms>, e.g. 2026wasam_qm12_043500
  frames/index.jsonl       one line per frame: id, path, event, match, video, t, w, h,
                           view (F = the calibrated fixed camera, M = a moving camera, ? = unknown),
                           phash, src (upload | livestream | public), split (train | val | test)
  cameras/<event>.json     the event's camera.json, copied at harvest (ROI crops, bglabel)
  labels/auto/<id>.json    teacher pseudo-labels: boxes [{c, b: [x0,y0,x1,y1] px, p, n, src}], u (0..1), teacher
  labels/import/<id>.json  human labels from an imported public set
  labels/review/<id>.json  human gold from the review UI: boxes, status ok | fixed | bad (skip: older files), by, at,
                           diff, conf, hidden (auto boxes the slider left out), view (the reviewer's F / M fix)
  datasets/<name>/         YOLO layout (hard links), data.yaml, manifest.json
  runs/<name>/             ultralytics run, cstrain.json, report.json (eval)
  models/<name>/           robot.onnx (production format), robot.pt, card.json
```

Tools never overwrite `labels/review` (only the review UI writes it). Splits are by **match** (every frame of one match
lands in one split, a hash of event/match), so near-identical neighbouring frames never leak from train into val.

## The loop

1. **init** — `$T init 2026rebuilt --classes robot` (a later season: `--classes robot,<piece>`).
2. **import-yolo** (optional bootstrap) — `$T import-yolo /opt/stacks/cyber-scout/data/training/rf --project P`
   imports every data.yaml set under it as human labels (`src=public`, event `pub_<set>`). Class names map by name;
   the public robot aliases (`red_alliance`, `Robot`, …) map to `robot`; `--map NAME=INDEX` for the rest. Roboflow's
   augmented copies of one image (`*.rf.<hash>.jpg`) are imported once. `DATA/training/rf` is the cached download
   (free tier): never download it again.
3. **harvest** — one event at a time:
   `$T harvest --project P --event 2026wasam --videos "/mnt/nas/autoscout-media/2026wasam/*.mp4" --every 1`.
   Full frames (not the field ROI, so moving cameras train too) every `--every` s up to `--t1` (200 s). A frame whose
   96×54 grey thumbnail barely differs from the last kept one (`--min-change`, 1.0 grey levels) is skipped (idle
   field, held graphic, frozen feed); across the event's videos a frame is a duplicate only when its hash is near a
   kept one **and** its pixels barely differ (a hash alone can't tell two matches apart on a fixed camera).
   Views: `F`/`M` from a dynamic match file's `dynamic.view` (10 Hz; `--matches` or `DATA/events/EV/matches`), `F`
   for uploads of an event with a `camera.json`, else `?`. Livestream clips are recognised by path
   (`MEDIA/live/…`, `tba-qm20.mp4`). `--max-per-match N` spreads N frames over a video.
4. **autolabel** — teachers predict, weighted box fusion merges them:
   `$T autolabel --project P --teacher DATA/models/robot.pt,other.pt --tta --device 1`. A fused box's `p` is the
   teachers' weighted best scores averaged over all teachers (a box only half of them see scores about half); `n`
   is how many saw it. Frame `u` = the most doubtful box (`1 - |2p - 1|`, or teachers disagreeing): the review UI's
   Uncertain queue sorts by it. `--shard 0/2` + `--shard 1/2` = one process per GPU. `--bglabel` adds model-free
   bumper-colour proposals the teachers missed on fixed-camera **uploads** (`live/bglabel.py` against the match's
   median plate; on program feeds it over-proposes, so it is skipped there); they are never labels, they only flag
   the frame for review. `.onnx` teachers run through `live/detector.py` exactly like the worker.
5. **review** — `$T review --project P --host 0.0.0.0` on the training box, then open the printed token URL from the
   LAN / Tailscale (never through the public tunnel). No `--token` = a random one; the first visit sets a cookie
   (30 days); curl sends `X-Review-Token`. A student without the cookie gets "ask your mentor for the review link".
   Also `python live/train/review/server.py PROJECT --host 0.0.0.0 --port 7590 [--token T]`.
   - **Queues:** **Uncertain** (by `u`; frames with no labels at all first, imported human labels last), **Gold set**
     (unreviewed test, then val frames: the only honest yardstick), **Random**, **Mine**, **All**, with event / match /
     split filters. The frame card shows how many are left and how many you saved this session.
   - **What to box** (the Guide button; the team's convention, ask the lead mentor if unsure): one box around the
     whole visible robot, bumpers included, tight to its edges; partly hidden robots: the visible part; cut off by the
     frame edge: up to the edge; never people, carts, field structures or the score overlay's team icons. Robots off
     the field (queue line) and broadcast close-ups: ask the lead mentor, or Skip.
   - **Accept** (`A`) saves the boxes shown: `ok` if unchanged, `fixed` if edited. Saving **no boxes** needs a second,
     separate confirmation ("No robots here", `N`): an empty frame teaches the model that nothing in it is a robot.
     **Skip** (`S` / `→`) saves nothing: the frame stays in the queue and comes back after a reload. **Bad** (`B`)
     saves `bad`: replay, graphics, black or unusable, kept out of training. Every save's message has **Undo** (also
     `Ctrl+Z` while it shows): the review file goes back to what it was (or is deleted) and the frame reopens with
     your edits. A frame marked bad (or an old `skip`) reopens on its imported / auto boxes, so Accept keeps them.
   - **Hide auto under** (starts at 0.25 every visit) hides teacher boxes below it, and a save leaves them out: the
     side panel says how many, auto boxes from 0.25 up that the slider hides count as `removed`, and the review file
     records `hidden` (how many were left out) and `conf`.
   - **Bumper guesses** (bglabel proposals, grey dotted "guess?") are never saved unless the reviewer keeps one (`K`,
     the Keep button, or moving / resizing it), which saves it as a drawn box.
   - Leaving a frame with unsaved edits (Skip, Prev, a queue or filter change) keeps them as a **draft** in the page
     (listed in the side panel; closing the page asks first). Held keys never save: `A` / `B` / `N` ignore auto-repeat.
   - **View**: the Fixed / Moving toggle corrects a wrong camera tag; it is saved as `view` in the review file (only
     when it differs from the index). *build / eval still read the index's `view`.*
   - Touch (iPad): one finger draws / moves, two fingers pinch and pan; Undo, Reset and Delete box stay in the bottom
     bar. Trackpad: pinch zooms, two-finger scroll pans when zoomed in; a mouse wheel zooms. `Shift`+drag draws a box
     even when it starts inside another box.
6. **build** — `$T build d1 --project P --test-events 2026wagg --dup review=3`. Labels per frame: review
   (`ok` / `fixed`) > import > auto (boxes with `p >= --auto-min-conf` 0.45). A review marked `bad` (or `skip`, written
   before Skip stopped saving) drops the frame (extra review fields such as `diff`, `conf`, `hidden`, `view` are
   ignored). For **train** only, auto frames are left
   out when a box sits just under the bar (`--auto-mid` 0.3 ≤ p < 0.45: probably a robot the model would learn as
   background), when an unreviewed bglabel proposal is in them, or when `u > --auto-max-u` (off). Val / test keep every
   frame (filtering them would flatter the model). Public sets go to train (`--public-val` keeps their val split).
   Empty train frames: at most `--neg-max` (0.1) of the rest. `--test-events` holds whole events out;
   `--resplit --val-frac` re-hashes train / val. `--exclude-reviewers NAME…` ignores those reviewers' reviews (a test
   session, or a reviewer whose work is being redone). A dataset is never replaced without `--force`.
7. **train** — `$T train r3 --project P --dataset d1 --model yolo11m.pt --epochs 150 --device 1 --batch 4 --detach`
   (log `runs/r3.log`, `status` shows the epoch and best val mAP; `--resume` continues). Defaults: imgsz 1280,
   mosaic, scale 0.5, no rotation, cosine LR, patience 40. `--hyp KEY=VALUE` for any ultralytics override, e.g.
   `lr0=0.003`. `--model` can be an earlier `best.pt` to fine-tune.
8. **eval** — `$T eval r3 --project P --dataset d1 --baseline /opt/stacks/cyber-scout/data/models/robot.onnx`.
   mAP50, mAP50-95 and P / R / F1 at the worker's `CONF_TRACK` (0.30), ms per frame, overall and per view
   (`F` / `M` / `?`) and per label source. Fixed-camera frames are cropped to the field ROI the way the worker
   crops them (`--full-frame` turns that off); `.onnx` models run through `live/detector.py` (544×1280 letterbox,
   NMS 0.6, max 40), `.pt` through ultralytics at `--imgsz`. **Auto labels are silver**: a teacher graded on its own
   pseudo-labels scores itself high (eval prints a note); compare models on `labels:review` (the Gold set). The
   report goes to `runs/<name>/report.json` (or `models/<name>/`).
9. **export** — `$T export r3 --project P --device 1` → `models/r3/robot.onnx`: rectangular 544×1280, fp16, opset
   17, simplified (the production format, about 3× faster than square fp32), checked with `live/detector.py`.
   The ONNX carries `COREML_CACHE_KEY` (from the weights), so a Mac server's Core ML cache (`DATA/cache/coreml`) never
   serves the old model for the same path. `card.json`: data, hyperparameters, metrics, sha256.
10. **install** — `$T install r3 --project P --data /opt/stacks/cyber-scout/data` (production: the owner's call).
    Refuses without an eval report or when the report says it is worse than the baseline (`--force`). The old
    `robot.onnx` / `robot.pt` / `card.json` go to `DATA/models/backup/<time>/`; `robot.onnx` is replaced last, by
    rename. **No restart:** the worker and processors open `DATA/models/robot.onnx` for every match
    (`process.process`), and Client processors re-download it (the API hashes it by size + mtime).

Then the new model is the next round's teacher: autolabel again, review the Uncertain queue, build, train.

## A new game

`init 2027<game> --classes robot,<piece>`. Robots: the 2026 model is a good first teacher (bumpers look alike from
season to season). Game pieces have no teacher: draw a few hundred frames by hand in the review UI, train a first
model, autolabel with it, review the Uncertain queue, repeat. **The worker is single-class**: `live/detector.py`
takes the max over classes and calls every box a robot, so a multi-class model needs a class-aware decoder in the
worker before it is installed (export prints a reminder). FUEL tracking (`live/fuel.py`) is not a neural net.

## Speed on atomosserver

Measured 2026-10-19 (`yolo detect train`, imgsz 1280, AMP; `img/s` = training images per second, so
`s/epoch = train images / img/s`):

| model | GPU | batch | img/s | peak VRAM (card total) |
|---|---|---|---|---|
| yolo11s | 1 (3060 12 GB) | 8 | 25.8 | 8.7 GB |
| yolo11s | 0 (3060 Ti 8 GB) | 4 | 25.5 | 7.2 GB (2.5 GB of it the paused worker) |
| yolo11s | 0,1 (DDP) | 8 (4 per card) | 31.1 | 7.4 / 4.9 GB |
| yolo11m | 1 | 4 (8 runs out of memory; ultralytics drops to 4 by itself) | 11.2 | 8.6 GB |
| yolo11m | 0 | 2 (4 out of memory) | 12.0 | 7.8 GB |
| yolo11m | 0,1 (DDP) | 8 | out of memory on GPU 0 | — |

DDP across the two unequal cards is only 1.2× one card, so **two separate runs, one per GPU** (e.g. yolo11m on GPU 1
and yolo11s on GPU 0, or two seeds / settings) get ~2× the work done. For ~11k train images: yolo11s ≈ 7 min per
epoch, yolo11m ≈ 16 min per epoch (+ about a minute of val), so 150 epochs ≈ 18 h / 41 h.

Autolabel: two .pt teachers with TTA at 1280 take 84 ms per frame on GPU 1. Harvest: one 1080p60 match video
(200 s, `--every 1` → ~200 frames) takes ~12 s in one job; 6 jobs did 6 videos in 38 s. GPU 0 is production: train there only while `PAUSE_PROCESSING=1` (the paused worker keeps ~2.5 GB on
it) and hand it back before a competition.

## 2026 history

The first detector (`DATA/models/robot.onnx`, Oct 10) came from a one-off flow kept here: `dl.py` downloaded 6
public FRC robot sets from Roboflow once (`RF=<key>`, cached in `DATA/training/rf/`), `pool.py` pooled them into one
class, round 1 = `yolo11s.pt` at 960 for 40 epochs, `../make_labels.py` auto-labelled Sammamish field-ROI crops
(round-1 boxes + `../bglabel.py` bumper proposals; `DATA/training/dom/`), and `r2.sh` fine-tuned at 1280 on both GPUs
(pool + event labels ×2, 30 epochs), exported as 544×1280 fp16.
