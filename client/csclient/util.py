"""Small helpers: data-dir location, atomic writes, path safety, logging."""
import json
import logging
import logging.handlers
import os
import re
import sys
import tempfile
import time

log = logging.getLogger("cyberscout")

APP_NAME = "CyberScout"


def default_data_dir() -> str:
    """Per-OS default: ~/Library/Application Support/CyberScout, %APPDATA%\\CyberScout, ~/.local/share/cyberscout."""
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", APP_NAME)
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
        return os.path.join(base, APP_NAME)
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    return os.path.join(base, "cyberscout")


def ensure_dir(p: str, mode: int = 0o700) -> str:
    os.makedirs(p, mode=mode, exist_ok=True)
    return p


def fsync_dir(d: str):
    if os.name == "nt":
        return
    try:
        fd = os.open(d, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write(path: str, data: bytes, mode: int = 0o644, tmp_dir: str = None):
    """Write via a temp file in the same directory (or tmp_dir on the same filesystem), fsync, rename over."""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".cs-tmp-", dir=tmp_dir or d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    fsync_dir(d)


def write_json(path: str, obj, mode: int = 0o644):
    atomic_write(path, json.dumps(obj, indent=1, sort_keys=True).encode(), mode=mode)


def read_json(path: str, default=None):
    try:
        with open(path, "rb") as f:
            return json.loads(f.read().decode("utf-8"))
    except (OSError, ValueError):
        return default


# ------------------------------------------------------------------------------------------------ path safety
_WIN_BAD = re.compile(r'[<>:"|?*\x00-\x1f\\]')
_WIN_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.I)


def safe_relpath(name: str, roots=("api", "media")):
    """Normalised 'a/b/c' for a server-supplied path, or None if it is unsafe: absolute, drive-letter, backslashes,
    '..', control characters, Windows-reserved names, or not under one of `roots`."""
    if not isinstance(name, str) or not name or len(name) > 1024:
        return None
    if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name):
        return None
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts:
        return None
    for p in parts:
        if p == ".." or _WIN_BAD.search(p) or _WIN_RESERVED.match(p) or p.endswith((" ", ".")):
            return None
    if roots and parts[0] not in roots:
        return None
    return "/".join(parts)


def target_path(root: str, rel: str) -> str:
    """Absolute path for a safe relpath under root; raises ValueError if it would land outside (symlinked dirs)."""
    root_r = os.path.realpath(root)
    p = os.path.join(root_r, *rel.split("/"))
    parent = os.path.realpath(os.path.dirname(p))
    if parent != root_r and not parent.startswith(root_r + os.sep):
        raise ValueError("path escapes the mirror: " + rel)
    if os.path.islink(p):
        raise ValueError("refusing to write through a symlink: " + rel)
    return p


# ------------------------------------------------------------------------------------------------ logging
def setup_logging(log_dir: str, level: str = "INFO"):
    ensure_dir(log_dir)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger("cyberscout")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for h in list(root.handlers):
        root.removeHandler(h)
    fh = logging.handlers.RotatingFileHandler(os.path.join(log_dir, "client.log"), maxBytes=1_000_000, backupCount=5,
                                              encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)
    root.propagate = False
    return root


def human_bytes(n: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024.0
    return str(n)


def now() -> float:
    return time.time()
