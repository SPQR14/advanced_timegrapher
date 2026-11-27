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
        from adv_tg import native_bridge

        if native_bridge.have("advtg_sanitize_csv_cell"):
            return native_bridge.call_str_buf(
                native_bridge.lib().advtg_sanitize_csv_cell,  # type: ignore[union-attr]
                value.encode("utf-8"),
                initial=max(256, len(value) * 2 + 8),
            )
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
    from adv_tg import native_bridge

    if native_bridge.have("advtg_safe_filename_base"):
        raw = str(name)
        return native_bridge.call_str_buf(
            native_bridge.lib().advtg_safe_filename_base,  # type: ignore[union-attr]
            raw.encode("utf-8"),
            str(fallback).encode("utf-8"),
            initial=max(256, len(raw) * 2 + len(fallback) + 16),
        )
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
    homes = _home_variants()
    try:
        import getpass

        username = getpass.getuser()
    except Exception:
        username = ""
    from adv_tg import native_bridge

    if native_bridge.have("advtg_anonymize_text"):
        return native_bridge.call_str_buf(
            native_bridge.lib().advtg_anonymize_text,  # type: ignore[union-attr]
            text.encode("utf-8"),
            username.encode("utf-8"),
            "\n".join(homes).encode("utf-8"),
            initial=max(256, len(text.encode("utf-8")) * 2 + 64),
        )
    for home in homes:
        text = text.replace(home, "~")
    if not username or len(username) < 2 or username.lower() in ("root", "nobody"):
        return text
    u = re.escape(username)
    text = re.sub(rf"(?<=[/\\]){u}(?![\w.-])", "<USER>", text, flags=re.IGNORECASE)
    if len(username) >= 4:
        text = re.sub(rf"(?<![\w.-]){u}(?![\w.-])", "<USER>", text, flags=re.IGNORECASE)
    return text
