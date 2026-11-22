"""Runtime detection and path helpers for Windows MSIX / Microsoft Store packages.

Pure standard library (``ctypes`` on Windows only; no Qt, no third-party dependencies).

When Advanced Timegrapher runs inside an MSIX package (installed from the Microsoft Store or
sideloaded with package identity):

* Windows delivers application updates directly through the Store, so the GitHub Releases
  update checker is disabled (Store Policy 10.2.2).
* External donation buttons (``Buy me a coffee``) are hidden inside the Store build to stay
  compliant with Store Policy 10.8.
* Writes to ``%APPDATA%`` and ``%LOCALAPPDATA%`` are transparently virtualized by Windows
  into ``%LOCALAPPDATA%\\Packages\\<PackageFamilyName>\\LocalCache\\{Roaming,Local}``.
  Inside the packaged process (and its ``spawn`` child processes) standard paths work
  transparently, but Windows Explorer runs outside the package container; ``msix_external_path``
  maps those folders to their physical ``LocalCache`` locations so "Open folder" opens the
  real directory.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path
from typing import Any

__all__ = [
    "is_msix_packaged",
    "msix_external_path",
    "package_family_name",
    "package_full_name",
]

# Win32 error codes returned by GetCurrentPackageFullName / GetCurrentPackageFamilyName
_ERROR_SUCCESS = 0
_ERROR_INSUFFICIENT_BUFFER = 122
_ERROR_MORE_DATA = 234


def _query_appmodel_string(func_name: str, *, _kernel32: Any = None) -> str | None:
    """Call a Win32 ``GetCurrentPackage*(UINT32 *length, PWSTR buffer)`` function."""
    if _kernel32 is None:
        if sys.platform != "win32":
            return None
        try:
            _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        except OSError:
            return None
    func = getattr(_kernel32, func_name, None)
    if func is None:
        return None
    length = ctypes.c_uint32(0)
    try:
        rc = int(func(ctypes.byref(length), None))
    except OSError:
        return None
    if rc not in (_ERROR_INSUFFICIENT_BUFFER, _ERROR_MORE_DATA) or length.value <= 1:
        return None
    buf = ctypes.create_unicode_buffer(length.value)
    try:
        rc = int(func(ctypes.byref(length), buf))
    except OSError:
        return None
    if rc != _ERROR_SUCCESS:
        return None
    value = buf.value.strip()
    return value or None


def package_full_name() -> str | None:
    """MSIX ``PackageFullName`` of the running process, or None when unpackaged."""
    return _query_appmodel_string("GetCurrentPackageFullName")


def package_family_name() -> str | None:
    """MSIX ``PackageFamilyName`` of the running process, or None when unpackaged."""
    return _query_appmodel_string("GetCurrentPackageFamilyName")


def is_msix_packaged() -> bool:
    """True when the process runs with a Windows MSIX package identity."""
    return package_full_name() is not None


def _relative_to_ci(path: Path, base: Path) -> Path | None:
    """Case-insensitive ``path.relative_to(base)`` for Windows paths (returns None if outside)."""
    try:
        p_parts = path.parts
        b_parts = base.parts
        if len(p_parts) < len(b_parts):
            return None
        if tuple(x.casefold() for x in p_parts[: len(b_parts)]) != tuple(
            x.casefold() for x in b_parts
        ):
            return None
        return Path(*p_parts[len(b_parts) :])
    except Exception:
        return None


def msix_external_path(
    path: Path,
    *,
    family: str | None = None,
    local_appdata: str | None = None,
    appdata: str | None = None,
) -> Path:
    """Map a virtualized ``%LOCALAPPDATA%`` / ``%APPDATA%`` path to its real MSIX ``LocalCache``.

    Returns ``path`` unchanged outside MSIX or when ``path`` is outside both AppData roots.
    """
    fam = package_family_name() if family is None else family
    if not fam:
        return path
    local_raw = os.environ.get("LOCALAPPDATA", "") if local_appdata is None else local_appdata
    roaming_raw = os.environ.get("APPDATA", "") if appdata is None else appdata
    if not local_raw:
        return path
    local_root = Path(local_raw)
    cache_root = local_root / "Packages" / fam / "LocalCache"

    rel = _relative_to_ci(path, local_root)
    if rel is not None:
        # Never double-redirect a path that already points inside Packages/<fam>
        if rel.parts and rel.parts[0].casefold() == "packages":
            return path
        return cache_root / "Local" / rel

    if roaming_raw:
        rel = _relative_to_ci(path, Path(roaming_raw))
        if rel is not None:
            return cache_root / "Roaming" / rel

    return path
