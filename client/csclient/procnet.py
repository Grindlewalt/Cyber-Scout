"""How fast is the link to the server?  Processing here only helps when a match clip (~190 MB) comes down quickly
(and, for Clip only, goes up quickly).  Measured with a short speed test (GET/POST /app/v1/processing/speedtest)
and with every real clip transfer; the newest samples count (bytes / seconds over the last 30 min).

notice() -> the slow-connection text for the client page and the web app's Settings, combined with the metered-link
warning (process_warn_mb)."""
import threading
import time
from collections import deque

from .net import NetError

API = "/app/v1/processing/speedtest"
WINDOW_S = 1800
PROBE_EVERY_S = 1800
SLOW_DOWN_MIN = 3.0          # a clip taking longer than this to download: processing here isn't optimal
SLOW_UP_MIN = 5.0            # Clip only: a clip taking longer than this to upload
BEHIND_S = 90                # Clip only: the recording this far behind live = the Wi-Fi can't keep up with the stream
CLIP_BYTES = 191_000_000     # a typical livestream match clip (2026wagg: 191 MB each)


class NetMeter:
    def __init__(self):
        self.samples = deque(maxlen=64)          # (t, "down" | "up", bytes, seconds, kind)
        self.lock = threading.Lock()
        self.probe_at = 0.0
        self.probing = False
        self.error = None
        self.clip_bytes = None                   # typical clip size from the server (benchmark sample) / last job

    def add(self, d, n, secs, kind="clip"):
        if n <= 0 or secs <= 0:
            return
        with self.lock:
            self.samples.append((time.time(), d, int(n), float(secs), kind))

    def rate(self, d, now=None):
        """bytes/s over the samples of the last WINDOW_S (at least 256 KB in total), else None."""
        now = now or time.time()
        with self.lock:
            s = [x for x in self.samples if x[1] == d and now - x[0] < WINDOW_S]
        n, t = sum(x[2] for x in s), sum(x[3] for x in s)
        return n / t if n >= 256 * 1024 and t > 0 else None

    def last_real(self, now=None):
        now = now or time.time()
        with self.lock:
            return max([x[0] for x in self.samples if x[4] != "probe"] or [0.0])

    def due(self, up=False, now=None):
        """Time for a speed test: none for PROBE_EVERY_S and no real transfer meanwhile, or (up: Clip only, which
        uploads) no upload measured yet."""
        now = now or time.time()
        if self.probing or now - self.probe_at < PROBE_EVERY_S:
            return False
        if up and self.rate("up", now) is None:
            return True
        return now - self.last_real(now) > PROBE_EVERY_S

    def probe(self, server, count=None, up=True):
        """Speed test: 1 MB down (then 4 MB more on a fast link) and 1 MB up. -> (down B/s, up B/s)."""
        self.probing = True
        self.error = None
        try:
            got = self._down(server, 1 << 20, count)
            if got and got[1] < 1.5:
                self._down(server, 4 << 20, count)
            if up:
                body = bytes(1 << 20)
                t = time.time()
                r = server.call("POST", API, body, ctype="application/octet-stream", timeout=120)
                if r.ok:
                    self.add("up", len(body), time.time() - t, "probe")
                    if count:
                        count("up", len(body))
            self.probe_at = time.time()
        except NetError as e:
            self.error = str(e)
        finally:
            self.probing = False
        return self.rate("down"), self.rate("up")

    def _down(self, server, n, count):
        t = time.time()
        r = server.call("GET", f"{API}?bytes={n}", headers={"Accept": "*/*"}, stream=True, timeout=120)
        got = 0
        try:
            if r.status != 200:
                r.close()
                return None
            while True:
                b = r.read(256 * 1024)
                if not b:
                    break
                got += len(b)
            r.close()
        except BaseException:
            r.close(broken=True)
            raise
        el = max(1e-3, time.time() - t)
        self.add("down", got, el, "probe")
        if count:
            count("down", got)
        return got, el

    def view(self, mode="track", warn_mb=0, behind_s=None):
        down, up = self.rate("down"), self.rate("up")
        clip = self.clip_bytes or CLIP_BYTES
        out = {"down_bps": round(down) if down else None, "up_bps": round(up) if up else None,
               "clip_bytes": clip, "probing": self.probing, "probe_at": self.probe_at or None, "error": self.error,
               "clip_down_min": round(clip / down / 60, 1) if down else None,
               "clip_up_min": round(clip / up / 60, 1) if up else None}
        out["slow"], out["notice"] = notice(mode, down, up, clip, warn_mb, behind_s)
        return out


def _mbs(bps):
    v = bps / 1e6
    return f"{v:.1f}" if v < 10 else f"{v:.0f}"


def _mins(m):
    return f"{m:.0f} min" if m >= 1.5 else f"{m * 60:.0f} s"


def notice(mode, down, up, clip=CLIP_BYTES, warn_mb=0, behind_s=None):
    """(slow?, text or None).  Combined with the metered-link warning when clips are over warn_mb.  Clip only: the
    stream comes from YouTube / Twitch, so its health is how far the recording is behind live (behind_s); the link to
    the server matters for the uploads."""
    mb = clip / 1e6
    parts, slow = [], False
    if mode == "clip":
        if behind_s is not None and behind_s > BEHIND_S:
            slow = True
            parts.append(f"Slow Wi-Fi: the recording is {_mins(behind_s / 60)} behind the live stream, so this "
                         "connection can't keep up with it")
        if up:
            m = clip / up / 60
            if m > SLOW_UP_MIN:
                slow = True
                parts.append(f"{'Also slow' if parts else 'Slow Wi-Fi'}: about {_mbs(up)} MB/s up, so a match clip "
                             f"({mb:.0f} MB) takes about {_mins(m)} to upload")
    elif down:
        m = clip / down / 60
        if m > SLOW_DOWN_MIN:
            slow = True
            parts.append(f"Slow Wi-Fi: about {_mbs(down)} MB/s from the server, so a match clip ({mb:.0f} MB) takes "
                         f"about {_mins(m)} to download")
    if slow:
        parts[-1] += "; processing here won't be optimal (the server may be faster on its own)"
    if warn_mb and mb > warn_mb and mode != "clip":
        parts.append(f"each clip is also over your {warn_mb} MB data warning, so you're asked before every download"
                     if slow else f"Data: each match downloads a {mb:.0f} MB clip (over your {warn_mb} MB warning: "
                                  "you're asked first)")
    if not parts:
        return False, None
    t = ". ".join(p[0].upper() + p[1:] for p in parts) + "."
    return slow, t
