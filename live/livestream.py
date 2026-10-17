"""Live-stream ingest: turn an event's YouTube live stream into match clips + match records.

Independent of TBA and of FIRST's per-match uploads: match start/stop, name, teams and scores are all
read off the official 2026 FIRST broadcast overlay (top-centre scoreboard + post-match results screen).

  python live/livestream.py URL --out DIR                 # watch: record + analyse until the stream ends
  python live/livestream.py --event 2026wabe --out DIR    # URL from the event's sources.json, boxes from layout.json
  python live/livestream.py URL --out DIR --record-only   # just the recorder   (--start-sq/--end-sq: a past window)
  python live/livestream.py --out DIR --analyze-only      # just the analyser (follows DIR/chunks.jsonl; restartable)
  python live/livestream.py --replay FILE --out DIR       # offline: a recorded file (mp4/ts) or a glob of segments
  Managed at events by live/liveingest.py (compose service `recorder`): one process per followed event with
  `URL --event EV --source SID --exit-on-new-video`, output in $LIVE_DIR/<ev>/<video id>/ (meta.json names the
  event + source so the worker can find the stream's layout / calibration); exit code 3 = the URL moved to a
  new video id.

Recorder
  `yt-dlp -4 -g` resolves the stream's HLS media playlist (<=1080p, video only): 5 s MPEG-TS chunks addressed by
  media sequence number (sq), EXT-X-PROGRAM-DATE-TIME, ~1 h DVR.  We download chunks by sq (IPv4 only -- the
  server's IPv6 is bot-flagged and chunk URLs are bound to the resolving IP), append them to 60 s segment files
  DIR/seg/*.ts and index every chunk in DIR/chunks.jsonl (sq, file, byte offset, size, pts, wall time).  Chunk
  pts = 5 * sq, so everything works on one absolute stream-time axis.  Addressing by sq makes it gap-free and
  duplicate-free across drops / expired URLs / restarts; chunks older than the playlist window are still served
  (URL /sq/N/ substituted), so a restarted recorder backfills any gap, and after the stream ends (post_live) the
  rest is fetched as fragmented MP4 from the DASH URL (&sq=N) and remuxed to TS.  Retention: segments not
  overlapping a match are deleted --keep-hours after analysis, match segments after --keep-match-hours, and
  oldest-first beyond --budget-gb.

Analyser (per frame at SAMPLE_HZ = 10 Hz; OCR of clock/shift/scores at 2 Hz, title/names/teams every 4 s)
  * scoreboard present: white clock box + opposite red/blue score boxes (side colours are read, never assumed:
    Bordie put blue on the LEFT); playoff layout (team row under the bar) vs qualification layout (teams in the
    main bar) picked per frame; boxes can be moved with an event layout (--event, live/layout.py clock box);
  * clock ticks (digits change, as process.clock_start); OCR = PP-OCRv3 recogniser (live/models/ocr) with greedy
    CTC restricted to a per-field alphabet, digit crops stretched so repeated digits ("9988") survive;
  * every clock reading votes for an auto-start time T (auto: T = t - (20 - c); teleop, marked by the "n / 6"
    hub-shift box: T = t - 23 - (140 - c); without the shift box a reading <= 0:20 votes both ways); readings that
    agree form a match; T = median phase of the auto ticks (teleop runs on its own timer).  A frozen pre-match
    clock never forms a match.  A clock that stops counting early -> flag aborted_at_Ns / clock_lost_at_Ns.
  * post-match results screen (big scores + breakdown; 3 or 4 alliance teams in playoffs, team rows in quals):
    official score incl. penalties, team set (fixes one bad scoreboard read), breakdown.
  At T + 163 + 7 s the clip [chunk at/before T - LEAD_S, T + 163 + TAIL_S] is cut (stream copy; keyframe-aligned)
  and DIR/matches/<id>.json written; auto_start_in_clip is re-measured on the clip at 30 Hz.  The record is
  updated when the results screen appears (score_source "results_screen").  A second match with the same name
  (field-fault replay) gets id <id>_2 and a same_name_as_<id> / replay_of_aborted_<id> flag.

Record: {id, match_name, comp_level, set, number, teams {red, blue} (station order), score {red, blue},
  score_source, score_scoreboard, breakdown, alliance_names, t_start_stream (s, stream pts), t_start_utc,
  clip, auto_start_in_clip, confidence {match, name, teams, score}, flags, evidence, tba (optional cross-check)}
"""
import argparse, glob, io, json, os, re, shutil, socket, subprocess, sys, threading, time, traceback
import urllib.request, urllib.error
from collections import Counter, deque
from datetime import datetime, timezone

import numpy as np
import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

AUTO_S, PAUSE_S, TELEOP_S = 20.0, 3.0, 140.0            # 2026 REBUILT
MATCH_S = AUTO_S + PAUSE_S + TELEOP_S
LEAD_S = float(os.environ.get("LS_LEAD", "8"))           # clip starts >= this before auto start
TAIL_S = float(os.environ.get("LS_TAIL", "5"))           # ... and ends this after the final 0:00
SAMPLE_HZ = float(os.environ.get("LS_SAMPLE_HZ", "10"))  # cheap overlay checks + clock ticks
OCR_HZ = float(os.environ.get("LS_OCR_HZ", "2"))         # OCR of clock / shift / scores
FULL_EVERY_S = float(os.environ.get("LS_FULL_EVERY", "4"))  # ... + title, names, team numbers
YTDLP = os.environ.get("YTDLP", "yt-dlp")
FORMAT = os.environ.get("LS_FORMAT", "312/301/bv*[height<=1080][vcodec^=avc1]/bv*[height<=1080]")

# ---------------------------------------------------------------- overlay geometry (1920x1080)
# The 2026 FIRST broadcast overlay (top-centre scoreboard).  process.CLOCK_BOX = (912,72,1008,116).
CLOCK_BOX = (912, 72, 1008, 116)                         # tick detection (same as process.py)
SB = dict(
    title=(560, 24, 1360, 56),
    clock=(900, 69, 1020, 125),
    score_l=(779, 69, 900, 125), score_r=(1020, 69, 1140, 125),
    name_l=(450, 69, 779, 125), name_r=(1140, 69, 1470, 125),
    shift=(901, 128, 1020, 164),
    # (start just right of each team logo: a logo edge otherwise reads as a leading "1")
    team_l0=(492, 128, 565, 164), team_l1=(602, 128, 675, 164), team_l2=(712, 128, 785, 164),
    team_r0=(1182, 128, 1255, 164), team_r1=(1291, 128, 1364, 164), team_r2=(1400, 128, 1473, 164),
)
SB_COLOUR_L, SB_COLOUR_R = (785, 72, 895, 122), (1025, 72, 1135, 122)
# Qualification layout: no alliance names; the 3+3 team numbers sit in the main row instead of a second row.
SB_QUAL = {k: (v[0], 74, v[2], 120) for k, v in SB.items() if k.startswith("team_")}
SB_TEAMROW = (455, 132, 775, 160)                        # coloured band only in the playoff layout
# Post-match results screen (full-frame graphic after each match).
RS = dict(
    title=(320, 12, 1600, 68),
    name_l=(610, 112, 950, 178), name_r=(970, 112, 1310, 178),
    score_l=(610, 180, 950, 305), score_r=(970, 180, 1310, 305),
    team_l0=(128, 440, 290, 506), team_l1=(380, 440, 542, 506), team_l2=(253, 527, 415, 593),
    team_r0=(1448, 440, 1610, 506), team_r1=(1700, 440, 1862, 506), team_r2=(1574, 527, 1736, 593),
    team_l3=(128, 527, 290, 593), team_l4=(380, 527, 542, 593),
    team_r3=(1448, 527, 1610, 593), team_r4=(1700, 527, 1862, 593),
    auto_fuel_l=(605, 372, 760, 424), auto_fuel_r=(1160, 372, 1315, 424),
    tele_fuel_l=(605, 534, 760, 588), tele_fuel_r=(1160, 534, 1315, 588),
    tower_a_l=(605, 428, 760, 478), tower_a_r=(1160, 428, 1315, 478),
    tower_t_l=(605, 590, 760, 640), tower_t_r=(1160, 590, 1315, 640),
    penalty_l=(605, 644, 760, 694), penalty_r=(1160, 644, 1315, 694),
)
# Qualification results screen: teams listed in rows (rank / rank change boxes to their right).
RS_QUAL = dict(team_l0=(98, 450, 300, 500), team_l1=(98, 536, 300, 586), team_l2=(98, 622, 300, 672),
               team_r0=(1418, 450, 1620, 500), team_r1=(1418, 536, 1620, 586), team_r2=(1418, 622, 1620, 672))
RS_COLOUR_L, RS_COLOUR_R, RS_TABLE = (606, 120, 636, 300), (966, 120, 996, 300), (640, 322, 1280, 366)
# (edge strips of the big score boxes: a yellow "CARD" badge can sit over the alliance name)
DIGITS = "0123456789"


def apply_layout(L, source_id=None, log=print):
    """Move the scoreboard boxes to an event's layout (live/layout.py): the clock box of the livestream's
    source (else the processing source) anchors the whole top-centre scoreboard -- every SB box is mapped
    by the offset/scale that takes the default CLOCK_BOX onto it.  The full-frame results screen is not
    moved.  No layout / no clock box / same box -> the 2026 FIRST defaults (unchanged)."""
    global CLOCK_BOX, SB, SB_QUAL, SB_COLOUR_L, SB_COLOUR_R, SB_TEAMROW
    if L is None: return False
    sl = L.sources.get(source_id) if source_id else None
    if sl is None or not sl.has("clock"): sl = L.processing()
    b = sl.box("clock", 1920, 1080) if sl is not None else None
    if not b or tuple(b) == tuple(CLOCK_BOX): return False
    d0 = CLOCK_BOX
    sx, sy = (b[2] - b[0]) / (d0[2] - d0[0]), (b[3] - b[1]) / (d0[3] - d0[1])
    f = lambda q: (int(round(b[0] + (q[0] - d0[0]) * sx)), int(round(b[1] + (q[1] - d0[1]) * sy)),
                   int(round(b[0] + (q[2] - d0[0]) * sx)), int(round(b[1] + (q[3] - d0[1]) * sy)))
    SB = {k: f(v) for k, v in SB.items()}
    SB_QUAL = {k: f(v) for k, v in SB_QUAL.items()}
    SB_COLOUR_L, SB_COLOUR_R, SB_TEAMROW = f(SB_COLOUR_L), f(SB_COLOUR_R), f(SB_TEAMROW)
    CLOCK_BOX = tuple(b)
    log(f"layout {L.path}: clock box {tuple(b)} (source {sl.id}) -> scoreboard boxes moved ({sx:.2f}x{sy:.2f})")
    return True


def event_setup(ev, data=None, log=print, source=None):
    """--event: the livestream URL from <DATA>/events/<ev>/sources.json (sources.livestream_url, or the
    source `source` (id) when given) and the scoreboard geometry from the event's layout.json
    (layout.find(ev=...)).  Both optional.  -> (url, source id)."""
    if data: os.environ["DATA"] = data
    import layout as LAY, sources as SRC
    url, sid = None, None
    for d in LAY.event_dirs(ev):
        doc = SRC.load(d)
        if source:
            s = SRC.get(doc, source)
            url, sid = (s.get("url"), source) if s else (None, None)
        else:
            url = SRC.livestream_url(doc)
            sid = next((x["id"] for x in doc.get("sources", []) if x.get("url") == url), None) if url else None
        if url: break
    sid = sid or source
    L = LAY.find(ev=ev)
    if not apply_layout(L, sid, log):
        log(f"layout: {'none for ' + ev if L is None else 'clock box = default'}: 2026 FIRST overlay boxes")
    return url, sid


def utc(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-4] + "Z" if t else None


def jsave(p, obj):
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, p)


def colour_of(im, box):
    """'red' / 'blue' / None for a mostly saturated patch."""
    x0, y0, x1, y1 = box
    p = im[y0:y1, x0:x1].reshape(-1, 3).astype(np.int16)
    b, g, r = p[:, 0], p[:, 1], p[:, 2]
    blue = ((b - r > 60) & (b - g > 15)).mean()
    red = ((r - b > 60) & (r - g > 60)).mean()
    if blue > 0.5 and blue > 3 * red: return "blue"
    if red > 0.5 and red > 3 * blue: return "red"
    return None


# ---------------------------------------------------------------- OCR
class OverlayOCR:
    """PP-OCRv3 recogniser on fixed text-line crops, greedy CTC restricted to a per-field alphabet."""

    def __init__(self, device=None):
        import ocr as OCRM
        self.o = OCRM.BumperOCR(device=device)
        meta = self.o.rec.get_modelmeta().custom_metadata_map["character"].splitlines()
        self.chars = ["<b>"] + meta + [" "]
        self._masks = {}
        self.device = self.o.device

    def _mask(self, alphabet):
        if alphabet not in self._masks:
            m = np.zeros(len(self.chars), bool); m[0] = True
            if alphabet is None:
                m[:] = True
            else:
                for i, c in enumerate(self.chars):
                    if c in alphabet: m[i] = True
            self._masks[alphabet] = m
        return self._masks[alphabet]

    def read(self, im, boxes, alphabets, trim=None, stretch=None):
        """boxes: list of (x0,y0,x1,y1); alphabets: per box a string (allowed chars) or None;
        trim: per box True = cut the crop to the columns holding text (long, centred titles).
        -> list of (text, conf) where conf = min over emitted chars of the char probability."""
        lines, wide = [], []
        for k, (x0, y0, x1, y1) in enumerate(boxes):
            c = im[y0:y1, x0:x1]
            if alphabets[k] is not None:       # digits: stretch so repeated glyphs ("9988") get CTC steps
                sx = stretch[k] if stretch else 1.6
                c = cv2.resize(c, None, fx=sx, fy=1.0, interpolation=cv2.INTER_CUBIC)
            if trim and trim[k]:
                g = cv2.cvtColor(c, cv2.COLOR_BGR2GRAY).astype(np.int16)
                ink = np.abs(g - np.median(g)) > 60
                cols = np.where(ink.sum(0) >= 2)[0]
                if len(cols): c = c[:, max(0, cols[0] - 6):cols[-1] + 7]
            h = c.shape[0]
            pad = max(2, h // 6)
            c = cv2.copyMakeBorder(c, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
            (wide if c.shape[1] * 48 / c.shape[0] > 320 else lines).append((k, c))
        lps = [None] * len(boxes)
        for (k, _), lp in zip(lines, self.o._rec_batch([c for _, c in lines]) if lines else []):
            lps[k] = lp
        for k, c in wide:                      # long text lines: one variable-width run each
            w = int(min(1600, np.ceil(48 * c.shape[1] / c.shape[0])))
            W = next(b for b in (640, 960, 1280, 1600) if b >= w)     # fixed widths: no CUDA re-planning
            X = np.zeros((1, 3, 48, W), np.float32)
            X[0, :, :, :w] = ((cv2.resize(c, (w, 48)).astype(np.float32) / 255. - 0.5) / 0.5).transpose(2, 0, 1)
            P = self.o.rec.run(None, {self.o.rec_in: X})[0]
            lps[k] = np.log(np.clip(P[0, :int(np.ceil(w / 8)) + 1], 1e-9, 1))
        out = []
        for lp, al in zip(lps, alphabets):
            m = self._mask(al)
            l = np.where(m[None, :lp.shape[1]], lp, -1e9)
            idx = l.argmax(1); pr = np.exp(l.max(1))
            txt, confs, prev = [], [], 0
            for k, i in enumerate(idx):
                if i != 0 and i != prev:
                    txt.append(self.chars[i] if i < len(self.chars) else ""); confs.append(pr[k])
                prev = i
            out.append(("".join(txt).strip(), float(min(confs)) if confs else 0.0))
        return out


def parse_clock(s):
    m = re.fullmatch(r"(\d)[:.]?(\d\d)", s.replace(" ", ""))
    if not m: return None
    v = int(m.group(1)) * 60 + int(m.group(2))
    return v if int(m.group(2)) < 60 and v <= 180 else None


def parse_title(s):
    """'Qualification 12 of 58' / 'Upper Bracket - Round 1 - Match 1' / 'Final 2' ... ->
    (comp_level, set, number, canonical name) or None.  Space/case-insensitive (OCR drops spaces)."""
    c = re.sub(r"[^a-z0-9]", "", s.lower())
    m = re.search(r"qualification(\d{1,3})(?:of(\d{1,3}))?", c)
    if m: return "qm", 1, int(m.group(1)), f"Qualification {int(m.group(1))}"
    m = re.search(r"practice(\d{1,3})", c)
    if m: return "pm", 1, int(m.group(1)), f"Practice {int(m.group(1))}"
    m = re.search(r"(upper|lower)bracket.*?round(\d)match(\d{1,2})", c)
    if m:
        n = int(m.group(3))
        return "sf", n, 1, f"{m.group(1).capitalize()} Bracket - Round {m.group(2)} - Match {n}"
    m = re.search(r"(?:playoff|match)(\d{1,2})(?!\d)", c)
    if m and "final" not in c: return "sf", int(m.group(1)), 1, f"Match {int(m.group(1))}"
    m = re.search(r"finals?(?:match)?(\d)", c)
    if m: return "f", 1, int(m.group(1)), f"Final {int(m.group(1))}"
    m = re.search(r"(tiebreaker|overtime)(\d?)", c)
    if m: return "f", 1, int(m.group(2) or 4), "Final Tiebreaker"
    return None


def parse_int(s, lo=0, hi=999):
    s = re.sub(r"\D", "", s)
    if not s or len(s) > len(str(hi)): return None
    v = int(s)
    return v if lo <= v <= hi else None


def parse_team(s):
    s = re.sub(r"\D", "", s)
    return int(s) if 1 <= len(s) <= 5 and int(s) > 0 and not s.startswith("0") else None


def vote(vals, min_n=1):
    """Mode of non-None values -> (value, agreement fraction, n)."""
    vals = [v for v in vals if v is not None]
    if len(vals) < min_n: return None, 0.0, len(vals)
    v, k = Counter(vals).most_common(1)[0]
    return v, k / len(vals), len(vals)


# ---------------------------------------------------------------- frame scanner
class Scanner:
    """Per-frame overlay features.  push(t, bgr) at any rate; it subsamples itself."""

    def __init__(self, ocr, log=print, ev_dir=None, hz=SAMPLE_HZ):
        self.ocr = ocr; self.log = log; self.ev_dir = ev_dir; self.hz = hz; self.prev_bin_t = None
        self.next_s = -1e9; self.next_ocr = -1e9; self.next_rs = -1e9; self.next_full = -1e9
        self.prev_bin = None
        self.samples = deque()      # (t, sb_present, left_colour, rs_present, motion)
        self.ticks = []             # clock digit change times
        self.reads = []             # scoreboard OCR dicts
        self.results = []           # results-screen OCR dicts
        self.prev_small = None
        self.last_t = None
        self.thumbs = deque(maxlen=4000)   # (t, small jpeg of the overlay strip) for evidence

    def push(self, t, im):
        if t < self.next_s: return
        if self.last_t is not None and t < self.last_t - 1:     # time went backwards (new file)
            self.next_s = self.next_ocr = self.next_rs = self.next_full = -1e9
        self.last_t = t
        self.next_s = t + 1.0 / self.hz - 1e-3
        if im.shape[1] != 1920 or im.shape[0] != 1080:
            im = cv2.resize(im, (1920, 1080), interpolation=cv2.INTER_AREA)
        x0, y0, x1, y1 = CLOCK_BOX
        g = cv2.cvtColor(im[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
        white = g.mean() > 150
        cl, cr = colour_of(im, SB_COLOUR_L), colour_of(im, SB_COLOUR_R)
        sb = bool(white and cl and cr and cl != cr)
        rl, rr = colour_of(im, RS_COLOUR_L), colour_of(im, RS_COLOUR_R)
        tb = im[RS_TABLE[1]:RS_TABLE[3], RS_TABLE[0]:RS_TABLE[2]]
        rs = bool(rl and rr and rl != rr and tb.mean() > 170 and (tb.max(2).astype(int) - tb.min(2)).mean() < 30)
        small = im[204:1080:20, 0:1920:20, 1]                       # field-camera motion (green channel, 1/20)
        mot = float(np.abs(small.astype(np.int16) - self.prev_small).mean()) if self.prev_small is not None else 0.0
        self.prev_small = small
        self.samples.append((t, sb, cl if sb else None, rs, mot))
        b = g < 110
        if sb and self.prev_bin is not None and int((b ^ self.prev_bin).sum()) > 25:
            tc = 0.5 * (t + self.prev_bin_t)            # the change happened between the two samples
            if not self.ticks or tc - self.ticks[-1] > 0.2:
                self.ticks.append(tc)
        self.prev_bin = b if sb else None; self.prev_bin_t = t
        if sb and t >= self.next_ocr:
            self.next_ocr = t + 1.0 / OCR_HZ - 1e-3
            full = t >= self.next_full
            if full: self.next_full = t + FULL_EVERY_S - 1e-3
            self.reads.append(self.read_scoreboard(t, im, cl, full))
            self.thumbs.append((t, cv2.imencode(".jpg", im[0:170, 440:1480], [cv2.IMWRITE_JPEG_QUALITY, 60])[1]))
        if rs and t >= self.next_rs:
            self.next_rs = t + 1.0
            r = self.read_results(t, im, rl)
            self.results.append(r)
            if self.ev_dir and not any(abs(x["t"] - t) < 120 and x.get("img") for x in self.results[:-1]):
                p = os.path.join(self.ev_dir, f"results_{t:.0f}.jpg")
                cv2.imwrite(p, cv2.resize(im, (960, 540)), [cv2.IMWRITE_JPEG_QUALITY, 75]); r["img"] = p

    def read_scoreboard(self, t, im, left, full=True):
        """Clock, hub shift and scores every call; title, alliance names and team numbers when full."""
        keys = ["clock", "shift", "score_l", "score_r"]
        al = [DIGITS + ":", DIGITS + "/:", DIGITS, DIGITS]
        geo = SB
        if full:
            qual = colour_of(im, SB_TEAMROW) is None
            geo = dict(SB, **SB_QUAL) if qual else SB
            keys += ["title", "team_l0", "team_l1", "team_l2", "team_r0", "team_r1", "team_r2"]
            al += [None] + [DIGITS] * 6
            if not qual:
                keys += ["name_l", "name_r"]; al += [None, None]
        res = dict(zip(keys, self.ocr.read(im, [geo[k] for k in keys], al, trim=[k == "title" for k in keys],
                                           stretch=[2.0 if k.startswith("team") else 1.6 for k in keys])))
        right = "red" if left == "blue" else "blue"
        cl = parse_clock(res["clock"][0])
        sh = re.search(r"([1-6])/6", res["shift"][0])
        d = dict(t=t, left=left, clock=cl, clock_txt=res["clock"][0], clock_conf=res["clock"][1],
                 teleop=bool(sh), shift=int(sh.group(1)) if sh else None,
                 score={left: parse_int(res["score_l"][0]), right: parse_int(res["score_r"][0])},
                 score_conf=min(res["score_l"][1], res["score_r"][1]),
                 title=None, match=None, name={}, teams={}, full=full)
        if full:
            d.update(title=res["title"][0], match=parse_title(res["title"][0]),
                     name={left: res["name_l"][0], right: res["name_r"][0]} if "name_l" in res else {},
                     layout="qual" if "name_l" not in res else "playoff",
                     teams={left: [parse_team(res[f"team_l{i}"][0]) for i in range(3)],
                            right: [parse_team(res[f"team_r{i}"][0]) for i in range(3)]},
                     team_conf=[res[f"team_{s}{i}"][1] for s in "lr" for i in range(3)])
        return d

    def read_results(self, t, im, left):
        keys = list(RS)
        al = [None, None, None, DIGITS, DIGITS] + [DIGITS] * (len(keys) - 5)
        tt = self.ocr.read(im, [RS["title"]], [None], trim=[True])[0][0]
        geo = dict(RS, **RS_QUAL) if (parse_title(tt) or ("",))[0] in ("qm", "pm") else RS
        res = dict(zip(keys, self.ocr.read(im, [geo[k] for k in keys], al, trim=[k == "title" for k in keys])))
        right = "red" if left == "blue" else "blue"
        side = {"l": left, "r": right}
        d = dict(t=t, left=left, title=res["title"][0], match=parse_title(res["title"][0]),
                 score={left: parse_int(res["score_l"][0]), right: parse_int(res["score_r"][0])},
                 score_conf=min(res["score_l"][1], res["score_r"][1]),
                 name={left: res["name_l"][0], right: res["name_r"][0]},
                 teams={left: self._rs_teams(res, "l", geo is RS), right: self._rs_teams(res, "r", geo is RS)},
                 breakdown={})
        for k in ("auto_fuel", "tele_fuel", "tower_a", "tower_t", "penalty"):
            for s in "lr":
                d["breakdown"].setdefault(side[s], {})[k] = parse_int(res[f"{k}_{s}"][0])
        return d

    @staticmethod
    def _rs_teams(res, s, playoff):
        """Results-screen team set.  Playoff alliances can list 4 teams (2+2 boxes, incl. the backup)
        instead of 3 (2 + 1 centred box)."""
        g = lambda i: parse_team(res[f"team_{s}{i}"][0]) if f"team_{s}{i}" in res else None
        ok = lambda i: g(i) is not None and g(i) >= 10 and res[f"team_{s}{i}"][1] > 0.5
        idx = [0, 1, 3, 4] if playoff and ok(3) and ok(4) else [0, 1, 2]
        return sorted(x for x in (g(i) for i in idx) if x)

    def prune(self, before):
        while self.samples and self.samples[0][0] < before: self.samples.popleft()
        self.ticks = [x for x in self.ticks if x >= before]
        self.reads = [x for x in self.reads if x["t"] >= before]
        self.results = [x for x in self.results if x["t"] >= before]


# ---------------------------------------------------------------- match assembly
def t_votes(r):
    """Auto-start candidates implied by one clock reading: [(T, phase)].  The hub-shift box ("n / 6")
    marks teleop; without it a reading <= 0:20 is ambiguous (auto, or the last 20 s of teleop -- some
    broadcasts, e.g. Bordie's finals, never show the shift box), so it votes for both."""
    c = r["clock"]
    if c is None: return []
    tele = r["t"] - AUTO_S - PAUSE_S - (TELEOP_S - c) - 0.5
    if r["teleop"]:
        return [(tele, "teleop")] if c <= TELEOP_S else []
    out = []
    if c <= AUTO_S: out.append((r["t"] - (AUTO_S - c) - 0.5, "auto"))
    if c <= TELEOP_S: out.append((tele, "teleop?"))
    return out


def find_matches(reads, ticks, tol=0.9, min_n=6):
    """Cluster clock readings by implied auto start.  -> list of dicts {T, reads, n, span}."""
    cl, active = [], []
    for r in reads:
        active = [c for c in active if r["t"] - c["t_last"] < 60]
        for T, ph in t_votes(r):
            best = None
            for c in active:
                if abs(c["sum"] / len(c["Ts"]) - T) < tol:
                    best = c; break
            if best is None:
                best = dict(Ts=[], reads=[], phases=[], t_last=r["t"], sum=0.0); cl.append(best); active.append(best)
            best["Ts"].append(T); best["sum"] += T; best["reads"].append(r); best["phases"].append(ph); best["t_last"] = r["t"]
    out = []
    for c in cl:
        ts = [r["t"] for r in c["reads"]]
        clocks = set((ph, r["clock"]) for ph, r in zip(c["phases"], c["reads"]))
        # a real match: >= min_n readings of >= 4 distinct clock values (a frozen clock never qualifies)
        if len(c["reads"]) < min_n or len(clocks) < 4: continue
        # the integer second comes from the auto readings when there are enough: the teleop timer is not
        # phase-locked to auto (Bordie ~0.15 s, 2026wasam ~0.9 s off -> a teleop-dominated median can land a
        # whole tick late).  0:20 reads are left out (the static pre-match clock shows 0:20 too).
        au = [T for T, ph, r in zip(c["Ts"], c["phases"], c["reads"]) if ph == "auto" and r["clock"] < AUTO_S]
        T0 = float(np.median(au)) if len(au) >= 4 else float(np.median(c["Ts"]))
        T = refine_T(T0, ticks)
        out.append(dict(T=T, T_ocr=T0, reads=c["reads"], phases=c["phases"], n=len(c["reads"]),
                        t_first=min(ts), t_last=max(ts), n_values=len(clocks)))
    # one clock can only be one match: a cluster whose readings lie inside a bigger cluster's span is the
    # wrong reading of an ambiguous clock (auto vs end of teleop) -> drop it
    keep = []
    for m in sorted(out, key=lambda m: -m["n"]):
        span = m["t_last"] - m["t_first"] + 1e-6
        if any(min(m["t_last"], k["t_last"]) - max(m["t_first"], k["t_first"]) > 0.5 * span for k in keep):
            continue
        keep.append(m)
    out = sorted(keep, key=lambda m: m["T"])
    # merge clusters that are the same match (e.g. auto + teleop phases with a slightly off pause)
    merged = []
    for m in out:
        if merged and abs(m["T"] - merged[-1]["T"]) < 3.0:
            a = merged[-1]
            a["reads"] += m["reads"]; a["phases"] += m["phases"]; a["n"] += m["n"]
            a["t_first"] = min(a["t_first"], m["t_first"]); a["t_last"] = max(a["t_last"], m["t_last"])
            a["n_values"] += m["n_values"]
        else:
            merged.append(m)
    return merged


def refine_T(T0, ticks):
    """The clock ticks at T + k during auto (k = 1..20).  T = median phase of the auto ticks around the
    OCR estimate.  Teleop runs on its own timer (its ticks are NOT phase-locked to auto: measured
    ~0.15 s off on Bordie), so teleop ticks are only used when auto was not seen."""
    for lo, hi in ((T0 + 0.5, T0 + AUTO_S + 0.5), (T0 + AUTO_S + PAUSE_S + 0.5, T0 + MATCH_S + 0.5)):
        tk = np.array([x for x in ticks if lo < x < hi])
        if len(tk) < 4: continue
        off = tk - T0
        frac = off - np.round(off)
        good = np.abs(frac) < 0.45
        if good.sum() < 4: continue
        return T0 + float(np.median(frac[good]))
    return T0


class Assembler:
    """Turns scanner observations into match records, clips the video, writes JSON."""

    def __init__(self, out, scanner, clipper, log=print, tba=None, min_gap=60.0):
        self.out = out; self.sc = scanner; self.clipper = clipper; self.log = log; self.tba = tba
        os.makedirs(os.path.join(out, "matches"), exist_ok=True)
        os.makedirs(os.path.join(out, "clips"), exist_ok=True)
        os.makedirs(os.path.join(out, "evidence"), exist_ok=True)
        self.state_p = os.path.join(out, "assembler.json")
        st = json.load(open(self.state_p)) if os.path.exists(self.state_p) else {}
        self.done = st.get("done", {})          # match id -> {T, title, record path, results_done}

    def save(self):
        jsave(self.state_p, dict(done=self.done))

    def step(self, t_now, final=False):
        ms = find_matches(self.sc.reads, self.sc.ticks)
        for m in ms:
            if any(abs(d["T"] - m["T"]) < 5 for d in self.done.values()):
                continue
            ended = t_now > m["T"] + MATCH_S + TAIL_S + 2
            if not ended and not final: continue
            self.finish(m, t_now, final)
        # results screens: attach to the latest finished match with that title (or the latest one)
        for mid, d in list(self.done.items()):
            if d.get("results_done"): continue
            rs = [r for r in self.sc.results if d["T"] + MATCH_S - 5 < r["t"] < d.get("t_next", d["T"] + 900)]
            nxt = [x["T"] for x in self.done.values() if x["T"] > d["T"] + 30]
            nxt += [m["T"] for m in ms if m["T"] > d["T"] + 30]
            if nxt: rs = [r for r in rs if r["t"] < min(nxt)]
            if rs:
                self.attach_results(mid, rs)
            if (nxt and t_now > min(nxt)) or t_now > d["T"] + 900 or final:
                d["results_done"] = True
                rec = json.load(open(d["record"]))
                if rec.get("score_source") != "results_screen" and "zero_score_no_auto" in rec["flags"]:
                    rec["not_a_match"] = True; rec["flags"].append("no_results_screen")   # a clock run, not a match
                    jsave(d["record"], rec)
        self.save()
        self.sc.prune(t_now - 1800)

    # ---------------------------------------------------------------- one match
    def finish(self, m, t_now, final):
        reads = sorted(m["reads"], key=lambda r: r["t"])
        T = m["T"]
        allr = [r for r in self.sc.reads if T - 30 < r["t"] < T + MATCH_S + 15]
        fullr = [r for r in allr if r.get("full")]
        titles = [r["match"] for r in fullr if r["match"]]
        mt, mt_agree, _ = vote(titles)
        title_txt = vote([r["title"] for r in fullr if r["match"] == mt])[0] if mt else None
        lefts = vote([r["left"] for r in allr])[0] or "blue"
        teams, tconf, tevid = {}, [], {}
        for a in ("red", "blue"):
            teams[a] = []
            for i in range(3):
                v, f, n = vote([r["teams"][a][i] for r in fullr if a in r["teams"]], 1)
                teams[a].append(v); tconf.append(f if n >= 3 else f * n / 3)
                tevid[f"{a}{i + 1}"] = dict(value=v, agree=round(f, 2), n=n)
        # final scoreboard: readings after the last clock tick (0:00 shown) else the last few
        endr = [r for r in allr if r["t"] > T + MATCH_S - 0.5 and r["teleop"] or (r["clock"] == 0 and r["t"] > T + MATCH_S - 3)]
        if len(endr) < 2: endr = [r for r in allr if r["t"] > T + MATCH_S - 6]
        score_sb, sconf = {}, []
        for a in ("red", "blue"):
            v, f, n = vote([r["score"].get(a) for r in endr])
            score_sb[a] = v; sconf.append(f if n >= 2 else 0.3)
        # coverage: overlay present over the match window
        win = [s for s in self.sc.samples if T - 2 < s[0] < T + MATCH_S]
        cov = sum(1 for s in win if s[1]) / max(1.0, MATCH_S * SAMPLE_HZ)
        ticks = [x for x in self.sc.ticks if T - 1 < x < T + MATCH_S + 1]
        seen_auto = sum(ph == "auto" for ph in m["phases"]) >= 4
        seen_end = any(r["clock"] == 0 and r["teleop"] for r in allr if r["t"] > T + MATCH_S - 3) or \
            any(r["t"] > T + MATCH_S - 3 and r["clock"] is not None and r["clock"] <= 2 for r in allr)
        mot_pre = [s[4] for s in self.sc.samples if T - 8 < s[0] < T - 1]
        mot_auto = [s[4] for s in self.sc.samples if T + 1 < s[0] < T + 15]
        mid = self._id(mt, T)
        flags = []
        # a match whose clock stops counting early: aborted (field fault -> replayed later under the same
        # name; the overlay resets to 0:00 / 0-0) or the feed/overlay was lost
        progress = max(r["t"] for r in reads) - T
        aborted = progress < MATCH_S - 8
        if aborted:
            reset = any(r["clock"] == 0 and progress + T < r["t"] < T + MATCH_S for r in allr)
            flags.append(f"aborted_at_{progress:.0f}s" if reset else f"clock_lost_at_{progress:.0f}s")
            endr = []; score_sb = {"red": None, "blue": None}; sconf = [0.0]
            seen_end = False
        # the scoreboard was reset to 0-0 after points were scored: aborted match (field fault)
        peak = max([sum(v or 0 for v in r["score"].values()) for r in allr if T < r["t"] < T + MATCH_S] or [0])
        if not aborted and score_sb.get("red") == 0 and score_sb.get("blue") == 0 and peak > 20:
            aborted = True; flags.append("aborted_scores_reset")
            score_sb = {"red": None, "blue": None}; sconf = [0.0]; seen_end = False
        # not a match: the clock counted a timeout / field countdown (values above 2:20 on the same countdown,
        # e.g. a 3:00 alliance timeout in the finals) -- or 0-0 with nothing seen of auto (checked again when
        # no results screen follows)
        not_match = any(r["clock"] is not None and r["clock"] > TELEOP_S + 1 and abs(r["t"] + r["clock"] - (T + MATCH_S)) < 2.5
                        for r in self.sc.reads if T - 60 < r["t"] < T + AUTO_S + PAUSE_S + 5)
        if not_match: flags.append("timeout_countdown")
        elif score_sb.get("red") == 0 and score_sb.get("blue") == 0 and not seen_auto: flags.append("zero_score_no_auto")
        if not seen_auto: flags.append("auto_not_seen")
        if not seen_end and not aborted: flags.append("end_not_seen")
        if cov < 0.9: flags.append(f"overlay_coverage_{cov:.2f}")
        conf = float(np.clip(min(1.0, m["n"] / (MATCH_S * OCR_HZ * 0.5)) * min(1.0, cov / 0.95), 0, 1))
        conf_match = round(conf * (1.0 if seen_auto and seen_end else 0.6) * (0.3 if aborted else 1.0), 3)
        rec = dict(
            id=mid, not_a_match=bool(not_match), match_name=mt[3] if mt else None, title_ocr=title_txt,
            comp_level=mt[0] if mt else None, set=mt[1] if mt else None, number=mt[2] if mt else None,
            teams=teams, score=dict(score_sb), score_source="scoreboard",
            score_scoreboard=score_sb, alliance_names={a: vote([r["name"].get(a) for r in fullr])[0] for a in ("red", "blue")},
            blue_side=lefts == "blue" and "left" or "right",
            t_start_stream=round(T, 3), t_start_utc=utc(self.clipper.wall(T)) if self.clipper else None,
            t_end_stream=round(T + MATCH_S, 3), t_start_ocr_only=round(m["T_ocr"], 3),
            clip=None, auto_start_in_clip=None,
            confidence=dict(match=conf_match, name=round(mt_agree, 2) if mt else 0.0,
                            teams=round(float(np.mean(tconf)), 2), score=round(float(min(sconf)), 2)),
            flags=flags,
            evidence=dict(n_clock_reads=m["n"], n_clock_values=m["n_values"], n_ticks=len(ticks),
                          overlay_coverage=round(cov, 3), teams=tevid,
                          end_reads=[dict(t=round(r["t"], 1), clock=r["clock_txt"], score=r["score"]) for r in endr[-4:]],
                          motion_pre=round(float(np.mean(mot_pre)), 2) if mot_pre else None,
                          motion_auto=round(float(np.mean(mot_auto)), 2) if mot_auto else None),
        )
        # dedupe: same match name already recorded -> replay of the match / re-run of the stream
        for oid, d in self.done.items():
            if mt and d.get("title") == list(mt) and d["T"] < T:
                if d.get("aborted"):
                    rec["flags"].append(f"replay_of_aborted_{oid}")     # this one is the real result
                else:
                    rec["flags"].append(f"same_name_as_{oid}")           # replayed match / re-run footage:
                    if d.get("teams") == teams:                          # the later one is the official one
                        rec["flags"].append("possible_replay_or_rerun")
        if self.tba:
            try: self.tba.check(rec)
            except Exception as e: rec["tba"] = dict(error=repr(e))
        # evidence strips
        ev = os.path.join(self.out, "evidence")
        ims = []
        for tt in (T - 2, T + 5, T + 30, T + MATCH_S - 1, T + MATCH_S + 2):
            near = [x for x in self.sc.thumbs if abs(x[0] - tt) < 1.0]
            if near:
                im = cv2.imdecode(min(near, key=lambda x: abs(x[0] - tt))[1], cv2.IMREAD_COLOR)
                cv2.putText(im, f"T{tt - T:+.0f}s", (5, 160), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                ims.append(im)
        if ims:
            p = os.path.join(ev, f"{mid}_overlay.jpg"); cv2.imwrite(p, np.vstack(ims)); rec["evidence"]["overlay_img"] = p
        if self.clipper and not not_match:
            try:
                clip, a0 = self.clipper.cut(T - LEAD_S, T + MATCH_S + TAIL_S, os.path.join(self.out, "clips", f"{mid}.mp4"))
                pred = round(T - a0, 3) if a0 is not None else None
                meas = measure_auto_start(clip, self.sc.ocr)
                rec["clip"] = clip
                rec["auto_start_in_clip"] = meas if meas is not None else pred
                rec["evidence"]["auto_start_in_clip_predicted"] = pred
                rec["evidence"]["auto_start_in_clip_measured"] = meas
                if meas is None: rec["flags"].append("clip_start_unverified")
                elif pred is not None and abs(meas - pred) > 0.3: rec["flags"].append(f"clip_offset_{meas - pred:+.2f}s")
                if getattr(self.clipper, "last_gaps", 0): rec["flags"].append(f"clip_missing_chunks_{self.clipper.last_gaps}")
            except Exception as e:
                rec["flags"].append("clip_failed"); rec["clip_error"] = repr(e)[-300:]
        p = os.path.join(self.out, "matches", f"{mid}.json")
        jsave(p, rec)
        self.done[mid] = dict(T=T, title=list(mt) if mt else None, teams=teams, record=p, results_done=False,
                              aborted=aborted or bool(not_match))
        self.log(f"MATCH {mid}: {rec['match_name']} T={T:.2f} ({rec['t_start_utc']}) red {teams['red']} "
                 f"{score_sb.get('red')} - {score_sb.get('blue')} blue {teams['blue']} conf {rec['confidence']} "
                 f"flags {flags} clip {rec['clip']} auto@{rec['auto_start_in_clip']}")

    def _id(self, mt, T):
        base = (f"{mt[0]}{mt[1]}m{mt[2]}" if mt[0] in ("sf", "f") else f"{mt[0]}{mt[2]}") if mt else "unknown"
        mid = base
        k = 2
        while mid in self.done:
            mid = f"{base}_{k}"; k += 1
        return mid

    def attach_results(self, mid, rs):
        d = self.done[mid]
        rec = json.load(open(d["record"]))
        before = json.dumps(rec, sort_keys=True)
        if d.get("title"):
            same = [r for r in rs if r["match"] and list(r["match"]) == d["title"]]
            if same: rs = same
            elif any(r["match"] for r in rs): return      # results of a different match
        score, sconf = {}, []
        for a in ("red", "blue"):
            v, f, n = vote([r["score"].get(a) for r in rs])
            score[a] = v; sconf.append(f if n >= 2 else 0.5)
        if None in score.values(): return
        bd = {}
        for a in ("red", "blue"):
            bd[a] = {k: vote([r["breakdown"].get(a, {}).get(k) for r in rs])[0]
                     for k in ("auto_fuel", "tower_a", "tele_fuel", "tower_t", "penalty")}
        teams_rs = {a: vote([tuple(r["teams"].get(a, [])) for r in rs if len(r["teams"].get(a, [])) in (3, 4)])[0]
                    for a in ("red", "blue")}
        rec.update(score=score, score_source="results_screen", score_results=score, breakdown=bd)
        rec["confidence"]["score"] = round(float(min(sconf)), 2)
        rec["evidence"]["results"] = dict(n=len(rs), t=[round(rs[0]["t"], 1), round(rs[-1]["t"], 1)],
                                          img=next((r["img"] for r in rs if r.get("img")), None),
                                          teams=teams_rs, title=rs[0]["title"])
        # cross-check: results-screen teams (a set, larger font) vs scoreboard teams (station order).
        # A slot whose scoreboard read is not in the results set takes the one results team left over.
        rec["flags"] = [f for f in rec["flags"] if not f.startswith(("teams_mismatch", "teams_corrected"))]
        for a in ("red", "blue"):
            rs_set = list(teams_rs.get(a) or [])
            if len(rs_set) not in (3, 4): continue
            sb = list(rec.get("teams_scoreboard", rec["teams"])[a])
            bad = [i for i, v in enumerate(sb) if v not in rs_set]
            left = [v for v in rs_set if v not in sb]
            if not bad: continue
            if len(bad) == 1 and len(left) == 1 and len(rs_set) == 3:
                rec.setdefault("teams_scoreboard", {k: list(v) for k, v in rec["teams"].items()})
                rec["teams"][a][bad[0]] = left[0]
                rec["flags"].append(f"teams_corrected_{a}{bad[0] + 1}:{sb[bad[0]]}->{left[0]}")
            else:
                rec["flags"].append(f"teams_mismatch_{a}:{rs_set}")
        if rec["score_scoreboard"] and None not in rec["score_scoreboard"].values():
            for a in ("red", "blue"):
                if score[a] < rec["score_scoreboard"][a] - 5:
                    rec["flags"].append(f"results_score_below_scoreboard_{a}")
        if json.dumps(rec, sort_keys=True) != before:       # unchanged -> no rewrite (readers watch the files)
            jsave(d["record"], rec)
        if d.get("results_logged") == [score, len(rs) > 5]: return
        d["results_logged"] = [score, len(rs) > 5]
        self.log(f"RESULTS {mid}: red {score['red']} blue {score['blue']} (scoreboard {rec['score_scoreboard']}) "
                 f"teams {teams_rs}")
        if self.tba:
            try: self.tba.check(rec); jsave(d["record"], rec)
            except Exception: pass


# ---------------------------------------------------------------- chunk store (recorder output)
class ChunkStore:
    """DIR/chunks.jsonl: one line per 5 s HLS chunk {sq, file, off, size, pts, dur, wall}."""

    def __init__(self, out):
        self.out = out; self.idx_p = os.path.join(out, "chunks.jsonl"); self.rows = []; self._pos = 0

    def refresh(self):
        if not os.path.exists(self.idx_p): return []
        new = []
        with open(self.idx_p) as f:
            f.seek(self._pos)
            while True:
                line = f.readline()
                if not line or not line.endswith("\n"): break
                self._pos = f.tell()
                try: new.append(json.loads(line))
                except Exception: pass
        self.rows += new
        return new

    def wall(self, pts):
        """stream pts -> unix time (from the playlist's PROGRAM-DATE-TIME)."""
        best = None
        for r in reversed(self.rows):
            if r.get("wall") and r.get("pts") is not None:
                best = r
                if r["pts"] <= pts: break
        return best["wall"] + (pts - best["pts"]) if best else None

    def cut(self, t0, t1, dst):
        """Clip [t0, t1] (stream pts) -> mp4, keyframe/chunk aligned at or before t0 (stream copy).
        Returns (path, pts of the clip's first frame)."""
        rows = sorted((r for r in self.rows if r.get("pts") is not None and r["pts"] + r["dur"] > t0 and r["pts"] < t1
                       and os.path.exists(os.path.join(self.out, r["file"]))), key=lambda r: r["sq"])
        if not rows: raise RuntimeError("no chunks for clip")
        gaps = [b["sq"] - a["sq"] - 1 for a, b in zip(rows, rows[1:]) if b["sq"] - a["sq"] != 1]
        tmp = dst + ".ts"
        with open(tmp, "wb") as o:
            for r in rows:
                with open(os.path.join(self.out, r["file"]), "rb") as f:
                    f.seek(r["off"]); o.write(f.read(r["size"]))
        a0 = first_pts(tmp)
        r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", tmp, "-map", "0:v:0", "-c", "copy",
                            "-movflags", "+faststart", dst], capture_output=True, text=True)
        os.remove(tmp)
        if r.returncode != 0: raise RuntimeError(r.stderr[-300:])
        self.last_gaps = int(sum(gaps))        # chunks missing inside the clip (stream outage)
        return dst, a0


def measure_auto_start(clip, ocr, window=60.0):
    """Auto start (s into the clip) measured on the clip itself: clock OCR + 1 s ticks over its
    first `window` s (the same evidence the live analyser used).  None if no ticking clock."""
    sc = Scanner(ocr, log=lambda *a: None, hz=30.0)          # 30 Hz ticks: +-1 frame like process.clock_start
    import av
    with av.open(clip) as c:
        s = c.streams.video[0]; s.thread_type = "AUTO"
        t0 = None
        for f in c.decode(s):
            if f.time is None: continue
            if t0 is None: t0 = float(f.time)
            t = float(f.time) - t0
            if t > window: break
            if t >= sc.next_s: sc.push(t, f.to_ndarray(format="bgr24"))
    ms = find_matches(sc.reads, sc.ticks, min_n=4)
    return round(ms[0]["T"], 3) if ms else None


def first_pts(path):
    import av
    with av.open(path) as c:
        for f in c.decode(video=0):
            if f.time is not None: return float(f.time)
    return None


# ---------------------------------------------------------------- recorder
def _ipv4_only():
    orig = socket.getaddrinfo

    def gai(host, port, family=0, type=0, proto=0, flags=0):
        return orig(host, port, socket.AF_INET, type, proto, flags)
    socket.getaddrinfo = gai


def _public(url):
    """SSRF guard (live/sources.py safe_public_url): http(s) only, every resolved address public."""
    import sources as SRC
    return SRC.safe_public_url(url)


class _NoPrivateRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_NoPrivateRedirect)


def _http(url, timeout=20):
    _public(url)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with _OPENER.open(req, timeout=timeout) as r:
        return r.read()


def parse_playlist(text):
    seq, pdt, items, dur, endlist = 0, None, [], 5.0, False
    for line in text.splitlines():
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"): seq = int(line.split(":")[1])
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:") and pdt is None and not items:
            pdt = datetime.fromisoformat(line.split(":", 1)[1].replace("Z", "+00:00")).timestamp()
        elif line.startswith("#EXTINF:"): dur = float(line[8:].split(",")[0])
        elif line.startswith("#EXT-X-ENDLIST"): endlist = True
        elif line and not line.startswith("#"):
            m = re.search(r"/sq/(\d+)", line)
            sq = int(m.group(1)) if m else seq + len(items)
            items.append((sq, dur, line))
    # wall time of each chunk: PDT of the first + cumulative durations
    out, w = [], pdt
    for sq, d, u in items:
        out.append(dict(sq=sq, dur=d, url=u, wall=w))
        if w is not None: w += d
    return out, endlist


class Recorder:
    def __init__(self, url, out, log=print, seg_chunks=12, max_hours=4.0, budget_gb=20.0, keep_h=2.0,
                 keep_match_h=12.0, backfill_min=0.0, start_sq=None, end_sq=None, exit_on_new_video=False):
        self.url = url; self.out = out; self.log = log; self.seg_chunks = seg_chunks
        self.start_sq, self.end_sq = start_sq, end_sq
        self._dvr_logged = False
        self.exit_on_new_video = exit_on_new_video      # managed (live/liveingest.py): one out dir per video id
        self.new_video = None
        self.max_s = max_hours * 3600; self.budget = budget_gb * 1e9; self.keep_h = keep_h
        self.keep_match_h = keep_match_h; self.backfill = backfill_min * 60
        os.makedirs(os.path.join(out, "seg"), exist_ok=True)
        self.idx_p = os.path.join(out, "chunks.jsonl")
        self.state_p = os.path.join(out, "recorder.json")
        st = json.load(open(self.state_p)) if os.path.exists(self.state_p) else {}
        self.last_sq = st.get("last_sq"); self.video_id = st.get("video_id")
        self.stop = threading.Event()
        self.hls = None; self.hls_at = 0

    def resolve(self):
        try:
            _public(self.url)                       # http(s) to a public host only (SSRF guard)
        except ValueError as e:
            raise RuntimeError(f"livestream URL refused: {e}")
        r = subprocess.run([YTDLP, "-4", "--no-warnings", "-f", FORMAT, "--print", "%(id)s\t%(live_status)s\t%(format_id)s\t%(height)s",
                            "-g", "--", self.url], capture_output=True, text=True, timeout=120)
        lines = [l for l in r.stdout.strip().split("\n") if l]
        if r.returncode != 0 or len(lines) < 2:
            raise RuntimeError(f"yt-dlp: {r.stderr.strip()[-300:]}")
        vid, status, fmt, h = lines[0].split("\t")
        try:
            _public(lines[1])
        except ValueError as e:
            raise RuntimeError(f"stream URL refused: {e}")
        return vid, status, fmt, lines[1]

    def run(self):
        _ipv4_only()
        t_begin = time.time(); fails = 0; idle = 0
        seg_f, seg_name, seg_n, seg_off = None, None, 0, 0
        idx = open(self.idx_p, "a")
        while not self.stop.is_set():
            if time.time() - t_begin > self.max_s:
                self.log("recorder: max hours reached"); break
            try:
                if self.hls is None or time.time() - self.hls_at > 3 * 3600:
                    vid, status, fmt, hls = self.resolve()
                    if vid != self.video_id and self.video_id is not None and self.exit_on_new_video:
                        self.log(f"recorder: new video id {vid} (was {self.video_id}): exiting (the service "
                                 f"starts a new output dir for it)")
                        self.new_video = vid; break
                    if vid != self.video_id and self.video_id is not None:
                        self.log(f"recorder: new video id {vid} (was {self.video_id}): sequence restarts")
                        self.last_sq = None
                    self.video_id = vid; self.hls = hls; self.hls_at = time.time()
                    self.log(f"recorder: {vid} {status} itag {fmt}; resume after sq {self.last_sq}")
                    if status not in ("is_live", "is_upcoming", "post_live"):     # was_live/not_live: over
                        self.log(f"recorder: stream status {status}: stopping"); break
                if "m3u8" not in self.hls:
                    # post_live: no HLS any more; YouTube serves the same 5 s chunks as fragmented MP4 by
                    # sq from the DASH URL (base + &sq=N) -> fetch the rest of the stream, then stop
                    self.fetch_dash(idx)
                    break
                pl, endlist = parse_playlist(_http(self.hls).decode())
                fails = 0
                lo = self.last_sq + 1 if self.last_sq is not None else (
                    self.start_sq if self.start_sq is not None else pl[-1]["sq"] - int(self.backfill / (pl[-1]["dur"] or 5)))
                todo = [c for c in pl if c["sq"] >= lo]
                if pl and lo < pl[0]["sq"] and not can_synth(pl[0]):
                    if not self._dvr_logged:
                        self.log(f"recorder: chunks before sq {pl[0]['sq']} are not available from this stream (no "
                                 f"older-than-DVR fetch, e.g. Twitch): recording from the playlist start")
                        self._dvr_logged = True
                    lo = pl[0]["sq"]
                if pl and lo < pl[0]["sq"]:
                    # older than the playlist's DVR window: YouTube still serves those chunks by sq
                    # (same URL with /sq/N/ replaced); wall time extrapolated back from the head PDT
                    old = [synth_chunk(pl[0], q) for q in range(max(lo, 0), pl[0]["sq"])]
                    self.log(f"recorder: fetching {len(old)} chunks older than the DVR window (sq {lo}..{pl[0]['sq'] - 1})")
                    todo = old + todo
                if self.end_sq is not None:
                    todo = [c for c in todo if c["sq"] <= self.end_sq]
                for c in todo:
                    if self.stop.is_set(): break
                    data = None
                    for k in range(4):
                        try: data = _http(c["url"], timeout=30); break
                        except urllib.error.HTTPError as e:
                            if e.code in (404, 410) and k >= 1: break       # chunk gone: skip it
                            if k == 3: raise
                            time.sleep(1 + 2 * k)
                        except Exception as e:
                            if k == 3: raise
                            time.sleep(1 + 2 * k)
                    if data is None:
                        self.log(f"recorder: GAP chunk sq {c['sq']} unavailable; skipped")
                        self.last_sq = c["sq"]; continue
                    if seg_f is None or seg_n >= self.seg_chunks:
                        if seg_f: seg_f.close()
                        seg_name = f"seg/seg_{self.video_id}_{c['sq']:07d}.ts"
                        seg_f = open(os.path.join(self.out, seg_name), "ab"); seg_n = 0
                        seg_off = seg_f.tell()
                    seg_f.write(data); seg_f.flush()
                    pts = ts_first_pts(data)
                    idx.write(json.dumps(dict(sq=c["sq"], vid=self.video_id, file=seg_name, off=seg_off, size=len(data),
                                              pts=pts, dur=c["dur"], wall=c["wall"], got=time.time())) + "\n")
                    idx.flush()
                    seg_off += len(data); seg_n += 1
                    self.last_sq = c["sq"]
                    jsave(self.state_p, dict(last_sq=self.last_sq, video_id=self.video_id, at=time.time()))
                if endlist:
                    self.log("recorder: playlist ENDLIST: stream over"); break
                if self.end_sq is not None and self.last_sq is not None and self.last_sq >= self.end_sq:
                    self.log(f"recorder: reached end sq {self.end_sq}"); break
                idle = idle + 1 if not todo else 0
                if idle > 60:        # 5 min without a new chunk
                    self.log("recorder: no new chunks for 5 min; re-resolving"); self.hls = None; idle = 0
                self.retention()
                self.stop.wait(2.5 if todo else 4.0)
            except Exception as e:
                fails += 1
                self.log(f"recorder: error {repr(e)[:200]} (#{fails}); re-resolving")
                self.hls = None
                self.stop.wait(min(60, 5 * fails))      # upcoming / offline / network: keep trying until max hours
        if seg_f: seg_f.close()
        idx.close()
        self.log("recorder: stopped")

    def fetch_dash(self, idx):
        """After the stream ended (post_live): fetch chunks last_sq+1 .. end from the DASH URL, remuxed
        to MPEG-TS (timestamps kept) so the chunk store / clipper stay the same."""
        if self.last_sq is None:
            self.log("recorder: stream is over and nothing was recorded; use --replay on the VOD"); return
        off = None                                   # wall - pts from the HLS chunks recorded so far
        try:
            with open(self.idx_p) as f:
                for line in f:
                    r = json.loads(line)
                    if r.get("wall") and r.get("pts") is not None: off = r["wall"] - r["pts"]
        except Exception: pass
        q = self.last_sq + 1; n = 0; seg_f = None; seg_n = 99
        self.log(f"recorder: post_live: fetching chunks from sq {q} (DASH)")
        while not self.stop.is_set():
            try:
                data = _http(f"{self.hls}&sq={q}", timeout=30)
            except urllib.error.HTTPError as e:
                self.log(f"recorder: DASH sq {q}: HTTP {e.code}: end of stream"); break
            except Exception as e:
                self.log(f"recorder: DASH sq {q}: {repr(e)[:120]}: end"); break
            if len(data) < 1000: break
            r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", "pipe:0", "-map", "0:v:0", "-c", "copy",
                                "-copyts", "-muxdelay", "0", "-muxpreload", "0", "-f", "mpegts", "pipe:1"], input=data, capture_output=True)
            ts = r.stdout
            if r.returncode != 0 or not ts:
                self.log(f"recorder: DASH sq {q}: remux failed: end"); break
            if seg_f is None or seg_n >= self.seg_chunks:
                if seg_f: seg_f.close()
                seg_name = f"seg/seg_{self.video_id}_{q:07d}.ts"
                seg_f = open(os.path.join(self.out, seg_name), "ab"); seg_n = 0
            o = seg_f.tell(); seg_f.write(ts); seg_f.flush(); seg_n += 1
            pts = ts_first_pts(ts)
            idx.write(json.dumps(dict(sq=q, vid=self.video_id, file=seg_name, off=o, size=len(ts), pts=pts, dur=5.0,
                                      wall=(pts + off) if (off is not None and pts is not None) else None,
                                      got=time.time(), src="dash")) + "\n")
            idx.flush()
            self.last_sq = q; q += 1; n += 1
            jsave(self.state_p, dict(last_sq=self.last_sq, video_id=self.video_id, at=time.time()))
        if seg_f: seg_f.close()
        self.log(f"recorder: post_live: {n} chunks fetched")

    def retention(self):
        """Delete analysed segments that hold no match after keep_h; match segments after keep_match_h;
        then oldest-first while over the disk budget."""
        if getattr(self, "_ret_at", 0) > time.time() - 60: return
        self._ret_at = time.time()
        segs = sorted(glob.glob(os.path.join(self.out, "seg", "*.ts")))
        if len(segs) < 3: return
        an = {}
        p = os.path.join(self.out, "analyzer.json")
        if os.path.exists(p):
            try: an = json.load(open(p))
            except Exception: pass
        cursor = an.get("cursor_wall", 0)
        windows = []
        ap = os.path.join(self.out, "assembler.json")
        if os.path.exists(ap):
            try:
                for d in json.load(open(ap)).get("done", {}).values():
                    windows.append((d["T"] - 60, d["T"] + MATCH_S + 300))
            except Exception: pass
        # segment -> pts range from the index (tail of the file is enough)
        rng = {}
        try:
            with open(self.idx_p) as f:
                for line in f:
                    try: r = json.loads(line)
                    except Exception: continue
                    a = rng.setdefault(r["file"], [1e18, -1e18, 0])
                    if r.get("pts") is not None: a[0] = min(a[0], r["pts"]); a[1] = max(a[1], r["pts"] + r["dur"])
                    a[2] = max(a[2], r.get("got", 0))
        except Exception:
            return
        total = sum(os.path.getsize(s) for s in segs) + sum(os.path.getsize(c) for c in glob.glob(os.path.join(self.out, "clips", "*.mp4")))
        now = time.time()
        for s in segs[:-2]:
            rel = os.path.relpath(s, self.out)
            a = rng.get(rel)
            if not a: continue
            analysed = a[2] < cursor
            in_match = any(a[0] < w1 and a[1] > w0 for w0, w1 in windows)
            age_h = (now - a[2]) / 3600
            if analysed and ((not in_match and age_h > self.keep_h) or age_h > self.keep_match_h or total > self.budget):
                sz = os.path.getsize(s); os.remove(s); total -= sz
                self.log(f"retention: deleted {rel} ({'match' if in_match else 'no match'}, {age_h:.1f} h old)")


def can_synth(ref):
    """Can chunks older than the playlist be fetched by sequence number?  Only YouTube chunk URLs carry the
    number (/sq/N/); Twitch's do not, and the same URL would be fetched again for every older chunk."""
    return bool(re.search(r"/sq/\d+/", ref.get("url") or ""))


def synth_chunk(ref, sq):
    """Chunk entry for sequence number sq from a playlist entry (URL template + extrapolated wall time)."""
    u = re.sub(r"/sq/\d+/", f"/sq/{sq}/", ref["url"])
    return dict(sq=sq, dur=ref["dur"], url=u, wall=ref["wall"] - (ref["sq"] - sq) * ref["dur"] if ref["wall"] else None)


def ts_first_pts(data):
    """pts (s) of the first video frame in an MPEG-TS chunk (PES header parse; None if not found)."""
    for i in range(0, min(len(data), 188 * 400), 188):
        p = data[i:i + 188]
        if len(p) < 188 or p[0] != 0x47 or not (p[1] & 0x40): continue
        afc = (p[3] >> 4) & 3
        j = 4 + (1 + p[4] if afc & 2 else 0)
        if p[j:j + 3] != b"\x00\x00\x01" or not (0xE0 <= p[j + 3] <= 0xEF): continue
        if not (p[j + 7] & 0x80): continue
        b = p[j + 9:j + 14]
        pts = ((b[0] >> 1) & 7) << 30 | b[1] << 22 | (b[2] >> 1) << 15 | b[3] << 7 | b[4] >> 1
        return pts / 90000.0
    return None


# ---------------------------------------------------------------- analyser loops
def decode_into(scanner, src, t_off=0.0, start=None):
    """Decode a file / bytes into the scanner (all frames; scanner subsamples).  -> last t."""
    import av
    c = av.open(io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src)
    s = c.streams.video[0]; s.thread_type = "AUTO"
    last = None
    try:
        for f in c.decode(s):
            if f.time is None: continue
            t = float(f.time) + t_off
            if start is not None and t < start: continue
            if t < scanner.next_s and scanner.last_t is not None and t >= scanner.last_t - 1:
                last = t; continue
            scanner.push(t, f.to_ndarray(format="bgr24"))
            last = t
    finally:
        c.close()
    return last


def recorder_idle(out, s):
    p = os.path.join(out, "recorder.json")
    try: return time.time() - json.load(open(p)).get("at", 0) > s
    except Exception: return True


def analyse_live(out, log, ocr_dev, tba=None, stop=None, idle_exit_s=1800):
    store = ChunkStore(out)
    ev = os.path.join(out, "evidence"); os.makedirs(ev, exist_ok=True)
    sc = Scanner(OverlayOCR(ocr_dev), log, ev_dir=ev)
    asm = Assembler(out, sc, store, log, tba)
    st_p = os.path.join(out, "analyzer.json")
    st = json.load(open(st_p)) if os.path.exists(st_p) else {}
    done_sq = st.get("last_sq", -1)
    last_new = time.time(); t_last = None
    log(f"analyser: OCR on {sc.ocr.device}; resume after sq {done_sq}")
    while True:
        new = store.refresh()
        todo = [r for r in store.rows if r["sq"] > done_sq]
        # re-scan a few chunks before the resume point so ticks/reads for an in-progress match exist
        if done_sq >= 0 and t_last is None and todo:
            back = [r for r in store.rows if done_sq - 40 < r["sq"] <= done_sq]
            todo = back + todo
        for r in todo:
            p = os.path.join(out, r["file"])
            if not os.path.exists(p): continue
            with open(p, "rb") as f:
                f.seek(r["off"]); data = f.read(r["size"])
            if len(data) < r["size"]: break
            t0 = time.time()
            try:
                t_last = decode_into(sc, data) or t_last
            except Exception as e:
                log(f"analyser: chunk {r['sq']} decode error {e!r}")
            done_sq = max(done_sq, r["sq"]); last_new = time.time()
            if t_last is not None:
                asm.step(t_last)
            jsave(st_p, dict(last_sq=done_sq, cursor_wall=r.get("got", 0), t_last=t_last, at=time.time()))
        if stop is not None and stop.is_set() and not todo:
            break
        if stop is None and time.time() - last_new > idle_exit_s and recorder_idle(out, idle_exit_s):
            log("analyser: no new chunks and the recorder is idle; finishing"); break
        time.sleep(2)
    if t_last is not None:
        asm.step(t_last + 1e6, final=True)
    log("analyser: stopped")


def analyse_replay(files, out, log, ocr_dev, tba=None):
    """Offline: one recorded file (or a sorted glob of segments with continuous timestamps)."""
    ev = os.path.join(out, "evidence"); os.makedirs(ev, exist_ok=True)
    sc = Scanner(OverlayOCR(ocr_dev), log, ev_dir=ev)
    clipper = FileClipper(files)
    asm = Assembler(out, sc, clipper, log, tba)
    t_off = 0.0; t_last = None
    for f in files:
        t0 = time.time()
        if clipper.reset_ts:
            t_off = clipper.offset[f]
        t_last = decode_into(sc, f, t_off=t_off) or t_last
        log(f"replay: {os.path.basename(f)} -> t={t_last:.1f} ({time.time() - t0:.1f}s)")
        asm.step(t_last)
    asm.step((t_last or 0) + 1e6, final=True)


class FileClipper:
    """Clips for --replay: ffmpeg stream-copy from the source file(s) (keyframe-aligned start)."""

    def __init__(self, files):
        import av
        self.files = files; self.offset = {}; self.dur = {}
        o = 0.0; prev_end = None; self.reset_ts = False
        for f in files:
            with av.open(f) as c:
                s = c.streams.video[0]
                st = float(s.start_time * s.time_base) if s.start_time is not None else 0.0
                d = float(c.duration / 1e6) if c.duration else 0.0
            self.dur[f] = (st, d)
            if prev_end is not None and st < prev_end - 1: self.reset_ts = True
            prev_end = st + d
        for f in files:                       # segments with reset timestamps: lay them end to end
            self.offset[f] = o - self.dur[f][0]; o += self.dur[f][1]

    def wall(self, t):
        return None

    def cut(self, t0, t1, dst):
        segs = []
        for f in self.files:
            st, d = self.dur[f]
            a = (self.offset[f] + st) if self.reset_ts else st
            if a + d > t0 and a < t1: segs.append((f, a))
        if not segs: raise RuntimeError("no source for clip")
        src, tmp = segs[0][0], None
        if len(segs) > 1:                   # several files: remux them into one first (no seeking in concat)
            lst, tmp = dst + ".txt", dst + ".join.mkv"
            with open(lst, "w") as o:
                for f, a in segs: o.write(f"file '{os.path.abspath(f)}'\n")
            r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                                "-map", "0:v:0", "-c", "copy", tmp], capture_output=True, text=True)
            os.remove(lst)
            if r.returncode != 0: raise RuntimeError(r.stderr[-300:])
            src = tmp
        start_in = t0 - segs[0][1]
        r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", f"{max(0.0, start_in):.3f}", "-i", src,
                            "-t", f"{t1 - t0:.3f}", "-map", "0:v:0", "-c", "copy", "-avoid_negative_ts", "make_zero",
                            "-movflags", "+faststart", dst], capture_output=True, text=True)
        start_in = max(0.0, start_in)              # a clip asked to start before the file starts at its start
        k = keyframe_before(src, start_in) if r.returncode == 0 else None
        if tmp: os.remove(tmp)
        if r.returncode != 0: raise RuntimeError(r.stderr[-300:])
        return dst, segs[0][1] + (k if k is not None else start_in)



def keyframe_before(path, t):
    import av
    best = None
    with av.open(path) as c:
        s = c.streams.video[0]
        st = float(s.start_time * s.time_base) if s.start_time is not None else 0.0
        for p in c.demux(s):
            if p.pts is None: continue
            pt = float(p.pts * s.time_base) - st
            if pt > t + 0.001: break
            if p.is_keyframe: best = pt
    return best


# ---------------------------------------------------------------- TBA (optional)
class TBACheck:
    def __init__(self, event, key):
        import tba as TBAM
        self.api = TBAM.TBA(key, os.path.join(os.path.expanduser("~"), ".cache", "cyberscout_tba"))
        self.event = event

    def check(self, rec):
        ms = self.api.matches(self.event) or []
        key = None
        if rec["comp_level"] == "qm": key = f"{self.event}_qm{rec['number']}"
        elif rec["comp_level"] == "sf": key = f"{self.event}_sf{rec['set']}m1"
        elif rec["comp_level"] == "f": key = f"{self.event}_f1m{rec['number']}"
        m = next((x for x in ms if x["key"] == key), None)
        if not m:
            rec["tba"] = dict(key=key, found=False); return
        tt = {a: [int(k[3:]) for k in m["alliances"][a]["team_keys"]] for a in ("red", "blue")}
        sc = {a: m["alliances"][a]["score"] for a in ("red", "blue")}
        rec["tba"] = dict(key=key, found=True, teams=tt, score=sc,
                          teams_ok={a: sorted(tt[a]) == sorted(x for x in rec["teams"][a] if x) for a in tt},
                          order_ok={a: tt[a] == rec["teams"][a] for a in tt},
                          score_ok={a: sc[a] == rec["score"].get(a) for a in sc} if sc["red"] is not None and sc["red"] >= 0 else None)


def tba_key(env_path):
    k = os.environ.get("TBA_KEY")
    if k: return k
    if env_path and os.path.exists(env_path):
        for line in open(env_path):
            if line.startswith("TBA_KEY="): return line.split("=", 1)[1].strip().strip('"')
    return None


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", nargs="?", help="YouTube live URL (watch / channel /live URL)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--replay", nargs="+", help="recorded file(s) to process offline")
    ap.add_argument("--record-only", action="store_true")
    ap.add_argument("--analyze-only", action="store_true")
    ap.add_argument("--max-hours", type=float, default=4.0)
    ap.add_argument("--budget-gb", type=float, default=20.0)
    ap.add_argument("--keep-hours", type=float, default=2.0, help="keep non-match segments this long after analysis")
    ap.add_argument("--keep-match-hours", type=float, default=12.0)
    ap.add_argument("--backfill-min", type=float, default=0.0, help="on first start also fetch this much DVR (<= ~60)")
    ap.add_argument("--start-sq", type=int, help="record from this HLS sequence number (5 s chunks; older than the DVR window is ok)")
    ap.add_argument("--end-sq", type=int, help="stop recording after this sequence number")
    ap.add_argument("--gpu", type=int, default=int(os.environ.get("OCR_GPU", "1")))
    ap.add_argument("--event", help="event key: livestream URL from its sources.json (if no URL given), "
                                    "scoreboard boxes from its layout.json, and the TBA cross-check key")
    ap.add_argument("--data", help="data dir holding events/<key>/{sources,layout}.json (default $DATA)")
    ap.add_argument("--source", help="with --event: the sources.json source id (URL + layout of that source)")
    ap.add_argument("--exit-on-new-video", action="store_true",
                    help="stop when the URL starts serving another video id (managed mode: live/liveingest.py)")
    ap.add_argument("--tba-event", help="optional TBA event key to cross-check against (default --event)")
    ap.add_argument("--tba-env", default="/opt/stacks/cyber-scout/.env", help="file with TBA_KEY= (read only)")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    lock = threading.Lock()
    logf = open(os.path.join(a.out, "livestream.log"), "a")

    def log(*x):
        s = f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} " + " ".join(str(v) for v in x)
        with lock:
            print(s, flush=True); logf.write(s + "\n"); logf.flush()

    url = a.url
    sid = a.source
    if a.event:
        u, sid = event_setup(a.event, a.data, log, a.source)
        url = url or u
        a.tba_event = a.tba_event or a.event
    # who this output dir belongs to: the worker maps records to the source's layout / calibration
    mp = os.path.join(a.out, "meta.json")
    meta = json.load(open(mp)) if os.path.exists(mp) else {}
    meta.update(event=a.event or meta.get("event"), source=sid or meta.get("source"), url=url or meta.get("url"),
                mode="replay" if a.replay else "live", started=meta.get("started") or time.time(), pid=os.getpid(),
                replay=a.replay or meta.get("replay"))
    jsave(mp, meta)
    tba = None
    if a.tba_event:
        k = tba_key(a.tba_env)
        if k: tba = TBACheck(a.tba_event, k)
        else: log("no TBA key: cross-check off")
    if a.replay:
        files = []
        for p in a.replay: files += sorted(glob.glob(p)) or [p]
        analyse_replay(files, a.out, log, a.gpu, tba)
        return
    if a.analyze_only:
        analyse_live(a.out, log, a.gpu, tba)
        return
    if not url:
        ap.error("URL required for watch / record mode (or --event with a livestream in its sources.json)")
    rec = Recorder(url, a.out, log, max_hours=a.max_hours, budget_gb=a.budget_gb, keep_h=a.keep_hours,
                   keep_match_h=a.keep_match_hours, backfill_min=a.backfill_min, start_sq=a.start_sq, end_sq=a.end_sq,
                   exit_on_new_video=a.exit_on_new_video)
    if a.record_only:
        rec.run(); return 3 if rec.new_video else 0
    th = threading.Thread(target=rec.run, daemon=True); th.start()
    stop = threading.Event()

    def watch():
        th.join(); stop.set()
    threading.Thread(target=watch, daemon=True).start()
    analyse_live(a.out, log, a.gpu, tba, stop=stop)
    return 3 if rec.new_video else 0      # 3 = the URL moved to a new video (managed mode restarts at once)


if __name__ == "__main__":
    sys.exit(main() or 0)
