"""Check for a newer release on GitHub (standard library only; no Qt).

The application only *tells* the user that a newer version exists and where to download it;
it never downloads or installs anything.

* ``parse_version`` orders the tags the project uses (``v0.8.1-beta5.1``, ``v0.7.0.beta4``,
  ``v1.0.0``): a pre-release sorts before the final version of the same number.
* ``fetch_latest`` asks the public GitHub API for the releases of the repository (one HTTPS
  GET, no token; the request carries nothing but the program version in the User-Agent) and
  returns the highest published one. Drafts and releases marked as pre-release are ignored.
* ``UpdateState`` is the small JSON file in the configuration folder that remembers whether
  the automatic check is enabled and when it last ran (at most once per ``CHECK_INTERVAL_S``).

Certificates: a packaged build carries its own OpenSSL, which looks for the CA certificates
where the *build* system keeps them (the Linux bundle is built on Enterprise Linux and runs on
Debian). ``certifi`` is therefore used when it is installed; the system store otherwise.
"""

from __future__ import annotations

import json
import logging
import os
import re
import ssl
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adv_tg import __version__
from adv_tg.utils.adc_calibration import config_dir

__all__ = [
    "CHECK_INTERVAL_S",
    "DISABLE_ENV",
    "RELEASES_API_URL",
    "RELEASES_PAGE_URL",
    "ReleaseInfo",
    "UpdateCheckError",
    "UpdateState",
    "check_for_update",
    "fetch_latest",
    "is_newer",
    "parse_version",
    "ssl_context",
]

logger = logging.getLogger(__name__)

_REPO = "SPQR14/advanced_timegrapher"
RELEASES_API_URL = f"https://api.github.com/repos/{_REPO}/releases?per_page=20"
RELEASES_PAGE_URL = f"https://github.com/{_REPO}/releases"
#: Set to ``1`` to disable every automatic check (packagers, managed installations).
DISABLE_ENV = "ADV_TG_NO_UPDATE_CHECK"
#: Minimum time between two automatic checks.
CHECK_INTERVAL_S = 24 * 3600.0

_STATE_FILE = "update_check.json"
_MAX_RESPONSE_BYTES = 2_000_000
_STAGES = {"dev": 0, "a": 1, "alpha": 1, "b": 2, "beta": 2, "pre": 3, "rc": 3, "": 4}
_VERSION_RE = re.compile(
    r"""^v?(\d+)\.(\d+)(?:\.(\d+))?        # 1.2 or 1.2.3
        (?:[-._]?(dev|alpha|beta|a|b|rc|pre)  # stage
           [-._]?(\d+(?:\.\d+)*)?)?$          # its number(s): beta5.1
    """,
    re.VERBOSE | re.IGNORECASE,
)

Version = tuple[tuple[int, int, int], int, tuple[int, ...]]


class UpdateCheckError(Exception):
    """The release list could not be obtained (message in Spanish)."""


@dataclass(frozen=True)
class ReleaseInfo:
    """A published release.

    Attributes:
        version: Its version as text, without the leading ``v``.
        url: Page of the release (always under ``RELEASES_PAGE_URL``).
    """

    version: str
    url: str


def parse_version(text: str) -> Version | None:
    """Sortable key of a version or tag, or None when it is not one of the known forms."""
    m = _VERSION_RE.match(text.strip())
    if m is None:
        return None
    stage = (m.group(4) or "").lower()
    numbers = tuple(int(n) for n in m.group(5).split(".")) if m.group(5) else ()
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)), _STAGES[stage], numbers


def is_newer(candidate: str, current: str = __version__) -> bool:
    """True when ``candidate`` is a later version than ``current`` (False if either is odd)."""
    a, b = parse_version(candidate), parse_version(current)
    return a is not None and b is not None and a > b


def ssl_context() -> ssl.SSLContext:
    """TLS context with the certifi certificates when available (see the module docstring)."""
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def _pick_latest(releases: Any) -> ReleaseInfo | None:
    """Highest published version of the JSON list of the API (None when there is none)."""
    if not isinstance(releases, list):
        raise UpdateCheckError("respuesta inesperada del servidor de versiones")
    best: tuple[Version, ReleaseInfo] | None = None
    for rel in releases:
        if not isinstance(rel, dict) or rel.get("draft") or rel.get("prerelease"):
            continue
        tag = rel.get("tag_name")
        key = parse_version(tag) if isinstance(tag, str) else None
        if key is None:
            continue
        # the link is opened in the browser: only ever a page of this repository
        url = rel.get("html_url")
        if not (isinstance(url, str) and url.startswith(RELEASES_PAGE_URL + "/")):
            url = RELEASES_PAGE_URL
        if best is None or key > best[0]:
            best = (key, ReleaseInfo(tag.strip().lstrip("vV"), url))
    return None if best is None else best[1]


def fetch_latest(timeout_s: float = 6.0) -> ReleaseInfo | None:
    """Latest published release, or None when the repository has none.

    Raises:
        UpdateCheckError: No network, an HTTP error or an unreadable answer.
    """
    request = urllib.request.Request(  # noqa: S310 (constant https URL)
        RELEASES_API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"adv-tg/{__version__}",
        },
    )
    try:
        # B310 (file:/ or custom schemes): the URL is the https constant above, never user input
        with urllib.request.urlopen(  # noqa: S310  # nosec B310
            request, timeout=timeout_s, context=ssl_context()
        ) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise UpdateCheckError("respuesta demasiado grande del servidor de versiones")
        return _pick_latest(json.loads(raw.decode("utf-8")))
    except UpdateCheckError:
        raise
    except Exception as exc:  # URLError, timeout, SSL, JSON...
        raise UpdateCheckError(f"no se pudo consultar la última versión: {exc}") from exc


def check_for_update(timeout_s: float = 6.0) -> ReleaseInfo | None:
    """The latest release if it is newer than the running program, else None.

    Raises:
        UpdateCheckError: See ``fetch_latest``.
    """
    latest = fetch_latest(timeout_s)
    if latest is not None and is_newer(latest.version):
        return latest
    return None


class UpdateState:
    """Preferences and memory of the update check (``update_check.json``).

    Args:
        path: File to use (default ``config_dir() / "update_check.json"``).

    Attributes:
        enabled: Whether the check at start-up is wanted.
        last_check: Unix time of the last completed check (0 = never).
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else config_dir() / _STATE_FILE
        self.enabled = True
        self.last_check = 0.0
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.enabled = bool(data.get("enabled", True))
            self.last_check = float(data.get("last_check", 0.0))
        except FileNotFoundError:
            pass
        except Exception as exc:  # corrupt file: start again with the defaults
            logger.warning(
                "No se pudo leer %s (%s); se usan los valores por defecto", self.path, exc
            )

    def save(self) -> None:
        """Write the file (a failure is logged, never raised: this is only a convenience)."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"enabled": self.enabled, "last_check": self.last_check}),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
        except OSError as exc:
            logger.warning("No se pudo guardar %s: %s", self.path, exc)

    def due(self, now: float | None = None) -> bool:
        """True when an automatic check should run now."""
        if not self.enabled or os.environ.get(DISABLE_ENV) == "1":
            return False
        now = time.time() if now is None else now
        # a clock set back must not silence the check for ever
        return not 0.0 <= now - self.last_check < CHECK_INTERVAL_S

    def mark_checked(self, now: float | None = None) -> None:
        """Record a completed check and save."""
        self.last_check = time.time() if now is None else now
        self.save()
