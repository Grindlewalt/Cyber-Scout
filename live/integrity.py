"""Match-level integrity checks from open-set bumper reads (ocr.read_many(open_cands=...)).

Closed-set OCR forces every read onto one of the 6 teams TBA lists for the match, so a wrong video
(another match's footage) or a substitute robot still produces "good" reads.  With open-set scoring
every text box is scored against all the event's teams; this module turns those reads into

    match["identity"]["integrity"] = {
        n_reads, n_confident,             # open-set reads / confident ones (best_lp, best_margin gates)
        out_of_match: {frcX: n},          # confident reads whose best team is NOT in this match
        out_of_match_frac,                # of confident reads
        in_match: {frcY: n},              # confident reads per listed team
        lineup: {score, frac, best_other: {key, score, frac}},   # with the event's match list
        flags: [{kind: "possible_wrong_video", ...} | {kind: "possible_substitution", ...}],
        possible_wrong_video: bool, possible_substitution: [..]}

and should_retry(integrity) -> bool tells the worker to try the next candidate video.

Pure functions (numpy only); reads are normalised rows
    [best_team (number), best_lp, best_margin, rejected, track (output track idx or -1), source (0 overhead, 1 station)]
"""
import os
import numpy as np

E = os.environ.get
CONF_LP = float(E("INTEG_CONF_LP", "-4.0"))      # best_lp above this ...
CONF_MARGIN = float(E("INTEG_CONF_MARGIN", "2.0"))  # ... and this far ahead of the runner-up team = confident
CAP = float(E("INTEG_CAP", "10"))                # reads per team that count fully as "this robot is here"
MIN_CONF = int(E("INTEG_MIN_CONF", "20"))        # confident reads needed before judging the video
WV_GAIN = float(E("INTEG_WV_GAIN", "1.5"))       # another lineup explains this many more robots ...
WV_FRAC = float(E("INTEG_WV_FRAC", "0.5"))       # ... or this lineup explains < this fraction of confident reads
SUB_MIN = int(E("INTEG_SUB_MIN", "6"))           # confident reads of a non-listed team on one robot
SUB_FRAC = float(E("INTEG_SUB_FRAC", "0.5"))     # share of that team's track-attributed reads on that one robot


def _num(k):
    d = "".join(ch for ch in str(k) if ch.isdigit())     # "frc254" / "254" -> 254 ("frc254B" -> 254)
    return int(d) if d else -1


def _key(n):
    return f"frc{int(n)}"


def lineups(matches):
    """TBA match dicts (or {"key", "red": [...], "blue": [...]}) -> [(key, [6 team numbers])]."""
    out = []
    for m in matches or []:
        if "alliances" in m:
            red, blue = m["alliances"]["red"]["team_keys"], m["alliances"]["blue"]["team_keys"]
        else:
            red, blue = m.get("red") or [], m.get("blue") or []
        ts = [_num(t) for t in list(red) + list(blue)]
        if ts: out.append((m.get("key", ""), ts))
    return out


def normalise(open_rows=None, station_rows=None, det_track=None, station_det=None):
    """Read rows of both sources -> [best_team, best_lp, best_margin, rejected, track, source].
    open_rows: overhead OcrSampler.open_rows() remapped to det rows (ocr.OPEN_COLS);
    station_rows: station_ocr rows with open-set columns (19+);
    det_track: {det row: output track idx}; station_det: per station row its det row (or -1)."""
    out = []
    if open_rows is not None and len(open_rows):
        R = np.asarray(open_rows, float)
        tr = [det_track.get(int(g), -1) if det_track else -1 for g in R[:, 0]]
        out.append(np.c_[R[:, 7], R[:, 8], R[:, 12], R[:, 11], tr, np.zeros(len(R))])
    if station_rows is not None and len(station_rows):
        S = np.asarray(station_rows, float)
        if S.shape[1] >= 19:
            tr = [det_track.get(int(g), -1) if (det_track and g >= 0) else -1
                  for g in (station_det if station_det is not None else [-1] * len(S))]
            out.append(np.c_[S[:, 13], S[:, 14], S[:, 18], S[:, 17], tr, np.ones(len(S))])
    return np.concatenate(out) if out else np.zeros((0, 6))


def check(teams, reads, matches=None, track_teams=None, match_key=None):
    """teams: the 6 listed team keys (red1-3, blue1-3); reads: normalise() rows; matches: the
    event's TBA matches (for the wrong-video check); track_teams: output track idx -> team key
    (for the substitution check); match_key: this match (excluded from the lineup search).
    -> integrity dict (see module doc)."""
    listed = [_num(t) for t in teams]
    R = np.asarray(reads, float).reshape(-1, 6)
    conf = (R[:, 1] > CONF_LP) & (R[:, 2] >= CONF_MARGIN)
    C = R[conf]
    cnt = {}
    for t in C[:, 0].astype(int): cnt[t] = cnt.get(t, 0) + 1
    n_conf = len(C)
    oom = {_key(t): n for t, n in sorted(cnt.items(), key=lambda x: -x[1]) if t not in listed}
    res = dict(n_reads=int(len(R)), n_rejected=int((R[:, 3] > 0).sum()), n_confident=int(n_conf),
               out_of_match=oom, out_of_match_frac=round(sum(oom.values()) / max(n_conf, 1), 3),
               in_match={_key(t): int(cnt.get(t, 0)) for t in listed},
               by_source={s: dict(n=int((R[:, 5] == i).sum()), confident=int((C[:, 5] == i).sum()),
                                  out_of_match=int(((C[:, 5] == i) & ~np.isin(C[:, 0], listed)).sum()))
                          for i, s in ((0, "overhead"), (1, "station"))},
               flags=[], possible_wrong_video=False, possible_substitution=[])

    # -------- wrong video: does another lineup of this event explain the confident reads better?
    def score(ts):
        return float(sum(min(cnt.get(t, 0), CAP) / CAP for t in ts)), sum(cnt.get(t, 0) for t in ts) / max(n_conf, 1)
    s_this, f_this = score(listed)
    lu = dict(score=round(s_this, 2), frac=round(f_this, 3), n_teams_seen=int(sum(cnt.get(t, 0) >= 3 for t in listed)))
    if matches:
        best = None
        for k, ts in lineups(matches):
            if (match_key and k == match_key) or sorted(ts) == sorted(listed): continue
            s, f = score(ts)
            if best is None or (s, f) > (best[1], best[2]): best = (k, s, f)
        if best:
            lu["best_other"] = dict(key=best[0], score=round(best[1], 2), frac=round(best[2], 3))
            if n_conf >= MIN_CONF and best[1] >= s_this + WV_GAIN and (f_this < WV_FRAC or best[2] > f_this + 0.2):
                res["possible_wrong_video"] = True
                res["flags"].append(dict(kind="possible_wrong_video", likely_match=best[0],
                                         this=dict(score=round(s_this, 2), frac=round(f_this, 3)),
                                         other=dict(score=round(best[1], 2), frac=round(best[2], 3)),
                                         n_confident=int(n_conf)))
    if n_conf >= MIN_CONF and f_this < WV_FRAC and not res["possible_wrong_video"]:
        res["flags"].append(dict(kind="lineup_mismatch", frac=round(f_this, 3), n_confident=int(n_conf),
                                 note="most confident reads name teams not in this match"))
    res["lineup"] = lu

    # -------- substitution: one non-listed team read repeatedly on one robot (output track)
    if track_teams is not None:
        for tk, n in oom.items():
            if n < SUB_MIN: continue
            m = C[:, 0].astype(int) == _num(tk)
            trk = C[m, 4].astype(int); trk = trk[trk >= 0]
            if not len(trk): continue
            v, c = np.unique(trk, return_counts=True); j = int(c.argmax())
            if c[j] >= SUB_MIN and c[j] >= SUB_FRAC * len(trk):
                lab = track_teams.get(int(v[j]))
                own = int(((C[:, 4] == v[j]) & (C[:, 0] == (_num(lab) if lab else -1))).sum())
                if lab and c[j] < 0.5 * own: continue                # mostly read as the listed team
                f = dict(kind="possible_substitution", read=tk, listed=lab, track=int(v[j]),
                         n_reads=int(c[j]), n_reads_total=int(n), n_listed_reads_on_track=own)
                res["flags"].append(f); res["possible_substitution"].append(f)
    return res


def should_retry(integrity):
    """Worker decision: try the next candidate video when this one looks like another match."""
    return bool(integrity and integrity.get("possible_wrong_video"))
