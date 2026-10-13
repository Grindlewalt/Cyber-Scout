"""Validation for user-written text: notes (plain text), display names (no impersonation), short labels.

Everything a user types is stored as plain text and must never carry markup: the web client escapes on render,
and the server refuses markup too, so one missed esc() in a page can't turn a note into stored XSS
(security audit finding 1, same pattern as the picklist schema)."""
import re
import unicodedata

# zero-width / bidi / invisible format characters: used to fake names ("ad​min") or flip text direction
_INVISIBLE = re.compile("[­͏؜ᅟᅠ឴឵᠋-᠏​-‏‪-‮"
                        "⁠-⁯ㅤ︀-️﻿ﾠ￰-￻]")
# something that would be a tag / comment / doctype if rendered as HTML ("<3" and "a < b" stay allowed)
_TAG = re.compile(r"<(?:[/!?]?[A-Za-z]|!--)")
# link schemes that execute or embed content if anything ever auto-links note text
_SCHEME = re.compile(r"\b(?:javascript|vbscript|data|file)\s*:", re.I)
_ENTITY = re.compile(r"&#?[A-Za-z0-9]{1,10};")

MAX_NOTE_LINES = 60


class TextError(ValueError):
    pass


def _bad_chars(s: str, allow_newlines: bool) -> bool:
    for ch in s:
        if ch in "\n\t" and allow_newlines:
            continue
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"):
            return True
    return bool(_INVISIBLE.search(s))


def plain_text(s: str, max_len: int, multiline: bool = True, what: str = "text") -> str:
    """Normalise (NFC, \\r\\n -> \\n, trim) and validate plain text. Raises TextError with a user-facing message."""
    s = unicodedata.normalize("NFC", (s or "").replace("\r\n", "\n").replace("\r", "\n")).strip()
    if not s:
        raise TextError(f"{what} is empty")
    if len(s) > max_len:
        raise TextError(f"{what} is too long (max {max_len} characters)")
    if _bad_chars(s, multiline):
        raise TextError(f"{what} contains invisible or control characters")
    if _TAG.search(s) or _ENTITY.search(s):
        raise TextError(f"{what} must be plain text (no HTML tags or entities)")
    if _SCHEME.search(s):
        raise TextError(f"{what} must not contain javascript:/data:/file: links")
    if multiline:
        s = re.sub(r"\n{3,}", "\n\n", s)
        if s.count("\n") >= MAX_NOTE_LINES:
            raise TextError(f"{what} has too many lines (max {MAX_NOTE_LINES})")
    return s


# ------------------------------------------------------------------ display names
DISPLAY_MAX = 40
_NAME_OK = re.compile(r"^[\w .'()\-]+$")      # \w is Unicode letters/digits/_ ; plus space . ' ( ) -
# words a teammate may not put in their own display name (staff / system impersonation). Admins can still give a
# mentor such a name from the Admin page.
RESERVED = ("admin", "administrator", "sysadmin", "moderator", "staff", "system", "root", "owner", "official",
            "cyberscout", "security", "server", "mentor", "coach", "teacher", "support", "superuser")
# common look-alikes (Cyrillic / Greek / leetspeak) -> latin, so "Аdmin" or "4dm1n" match "admin"
_CONFUSE = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d",
    "ӏ": "l", "к": "k", "м": "m", "т": "t", "н": "h", "в": "b", "г": "r", "п": "n", "ԛ": "q", "ԝ": "w",
    "α": "a", "β": "b", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "τ": "t", "υ": "u", "χ": "x",
    "ο": "o", "μ": "m", "η": "n",
    "0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "9": "g", "@": "a", "$": "s", "!": "l",
    "|": "l", "i": "l", "ı": "l", "ł": "l", "ø": "o", "đ": "d",
})


def skeleton(s: str) -> str:
    """Case-, accent- and look-alike-insensitive form for comparing names ("C0ach", "coach", "Cоach" -> same)."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).casefold().translate(_CONFUSE)
    s = s.replace("rn", "m").replace("vv", "w")
    return re.sub(r"[\W_]+", "", s)


_RESERVED_SK = {skeleton(w) for w in RESERVED}


def has_reserved_word(name: str) -> bool:
    words = [w for w in re.split(r"[\s._()'\-]+", name) if w]
    whole = skeleton(name)
    if whole in _RESERVED_SK or any(skeleton(w) in _RESERVED_SK for w in words):
        return True
    # "SiteAdmin", "TheCoach": a reserved word glued to the start or end of the whole name
    return any(len(r) >= 5 and (whole.startswith(r) or whole.endswith(r)) for r in _RESERVED_SK)


def display_name(s: str, allow_reserved: bool = False) -> str:
    """Validate a display name: 1-40 visible characters, letters/digits/space . ' ( ) - _ only, no invisible or
    control characters, and (unless allow_reserved) no staff words like admin/mentor/coach. '' stays ''."""
    s = unicodedata.normalize("NFC", s or "")
    if _bad_chars(s, False):
        raise TextError("display name contains invisible or control characters")
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return ""
    if len(s) > DISPLAY_MAX:
        raise TextError(f"display name: at most {DISPLAY_MAX} characters")
    if not _NAME_OK.match(s):
        raise TextError("display name: letters, digits, spaces and . ' ( ) - _ only")
    if not skeleton(s):
        raise TextError("display name needs at least one letter or digit")
    if not allow_reserved and has_reserved_word(s):
        raise TextError("display name can't contain staff words like admin, mentor or coach")
    return s


# short single-line labels (season goal, ...): a strict allow-list, no quotes / angle brackets / ampersands
_LABEL_OK = re.compile(r"^[\w .,()/+#!:-]+$")


def label(s: str, max_len: int, what: str = "value") -> str:
    s = re.sub(r"\s+", " ", unicodedata.normalize("NFC", s or "")).strip()
    if len(s) > max_len:
        raise TextError(f"{what}: at most {max_len} characters")
    if s and (_bad_chars(s, False) or not _LABEL_OK.match(s) or _SCHEME.search(s)):
        raise TextError(f"{what}: letters, digits, spaces and . , ( ) / + # ! : - only")
    return s
