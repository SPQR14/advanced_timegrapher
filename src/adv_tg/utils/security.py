"""Application security utilities for data sanitization, privacy, and path safety."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

__all__ = ["anonymize_text", "safe_filename_base", "sanitize_csv_cell"]


def sanitize_csv_cell(value: Any) -> Any:
    """Neutralize formula injection elements in CSV exports (CWE-1236).

    Prepends a single quote to string values starting with '=', '+', '-', '@', '\\t', or '\\r',
    unless the string represents a valid finite number (e.g. "+1.25", "-0.40").
    """
    if isinstance(value, str):
        s = value.lstrip(" ")
        if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
            try:
                float(s)
                return value
            except ValueError:
                pass
            return f"'{value}"
    return value


def safe_filename_base(name: str, fallback: str = "watch_test") -> str:
    """Sanitize a user-provided name for use in filenames (CWE-22 / CWE-73).

    Restricts characters to alphanumeric, spaces, dashes, and underscores.
    """
    safe = "".join(c for c in str(name) if c.isalnum() or c in ("-", "_", " ")).strip()
    return safe or fallback


def _home_variants() -> list[str]:
    """Spellings of the home folder that may appear in logs, longest first."""
    out: set[str] = set()
    try:
        home = Path.home()
        for h in (str(home), str(home.resolve()), home.as_posix(), home.resolve().as_posix()):
            if len(h) > 1 and h != "/":
                out.add(h)
    except Exception:
        pass
    return sorted(out, key=len, reverse=True)


def anonymize_text(text: str) -> str:
    """Scrub personal identifying information (PII) such as local username and home path (CWE-200).

    The home folder becomes ``~``. The username becomes ``<USER>`` when it is a whole path
    component (``/Users/<USER>/...``, ``C:\\Users\\<USER>``) and, only if it has 4+
    characters, also as a standalone word; short names like ``al`` would otherwise hit
    ordinary words (Spanish "al", "del") and parts of words ("calibration").
    """
    if not text:
        return text
    for home in _home_variants():
        text = text.replace(home, "~")
    try:
        import getpass

        username = getpass.getuser()
    except Exception:
        return text
    if not username or len(username) < 2 or username.lower() in ("root", "nobody"):
        return text
    u = re.escape(username)
    text = re.sub(rf"(?<=[/\\]){u}(?![\w.-])", "<USER>", text, flags=re.IGNORECASE)
    if len(username) >= 4:
        text = re.sub(rf"(?<![\w.-]){u}(?![\w.-])", "<USER>", text, flags=re.IGNORECASE)
    return text
