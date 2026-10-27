"""Application security utilities for data sanitization, privacy, and path safety."""

from __future__ import annotations

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


def anonymize_text(text: str) -> str:
    """Scrub personal identifying information (PII) such as local username and home path (CWE-200)."""
    if not text:
        return text
    try:
        import getpass

        username = getpass.getuser()
        if username and len(username) > 1 and username.lower() not in ("root", "nobody"):
            text = text.replace(username, "<USER>")
    except Exception:
        pass

    try:
        home = str(Path.home().resolve())
        if home and len(home) > 1 and home != "/":
            text = text.replace(home, "~")
    except Exception:
        pass

    return text
