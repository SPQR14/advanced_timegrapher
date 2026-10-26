"""Bug / result report bundle: one ``.zip`` a user attaches to a GitHub issue.

Contents (``build_report``):

* ``report.txt``: what the user wrote, version, system, Python/Qt, the current settings of the
  window (passed in by the GUI) and the audio input devices PortAudio sees.
* ``logs/``: every log file of the log folder (``LoggerSetup.LOG_DIR``: ``adv_tg*.log`` and
  their rotations, ``faulthandler.log``), newest first, up to ``MAX_LOGS_BYTES``.
* ``adc_calibration.json``: the stored clock calibrations, if any.
* ``recording.flac``: the last session's recording, only if the user asks and it is smaller
  than ``MAX_RECORDING_BYTES`` (GitHub accepts attachments up to 25 MB).

No network access: the user reviews the file and attaches it. Logs contain file paths (with
the user name) and device names; the GUI says so before creating the report.
"""

from __future__ import annotations

import os
import platform
import sys
import zipfile
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from adv_tg import __version__
from adv_tg.utils.adc_calibration import config_dir
from adv_tg.utils.debugger import Debugger
from adv_tg.utils.logger_setup import LoggerSetup

__all__ = [
    "ISSUES_URL",
    "MAX_LOGS_BYTES",
    "MAX_RECORDING_BYTES",
    "audio_devices_text",
    "build_report",
    "default_report_name",
]

_dbg = Debugger.get(__name__)

ISSUES_URL = "https://github.com/SPQR14/advanced_timegrapher/issues/new/choose"
MAX_LOGS_BYTES = 30 * 1024 * 1024
MAX_RECORDING_BYTES = 20 * 1024 * 1024


def default_report_name(now: datetime | None = None) -> str:
    """``adv_tg_report_<version>_<date>_<time>.zip``."""
    return f"adv_tg_report_{__version__}_{(now or datetime.now()):%Y%m%d_%H%M%S}.zip"


def audio_devices_text() -> str:
    """One line per PortAudio device (inputs and outputs), or why they are not available."""
    try:
        from adv_tg.audio.capture import load_sounddevice

        sd = load_sounddevice()
        lines = []
        default_in = sd.default.device[0]
        for i, d in enumerate(sd.query_devices()):
            mark = " (default input)" if i == default_in else ""
            lines.append(
                f"[{i}] {d['name']} | in {d['max_input_channels']} / out "
                f"{d['max_output_channels']} | {d['default_samplerate']:.0f} Hz | "
                f"host API {d['hostapi']}{mark}"
            )
        return "\n".join(lines) or "(no devices)"
    except Exception as exc:
        return f"(PortAudio not available: {exc})"


def _log_files(log_dir: Path) -> list[Path]:
    """Log files, newest first, within ``MAX_LOGS_BYTES``."""
    if not log_dir.is_dir():
        return []
    files = [p for p in log_dir.iterdir() if p.is_file() and ".log" in p.name]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    out, total = [], 0
    for p in files:
        size = p.stat().st_size
        if total + size > MAX_LOGS_BYTES:
            continue
        out.append(p)
        total += size
    return out


def build_report(
    dest: Path | str,
    *,
    description: str = "",
    settings: Mapping[str, object] | None = None,
    recording: Path | None = None,
    log_dir: Path | None = None,
    calibrations: Path | None = None,
) -> tuple[Path, list[str]]:
    """Write the report ``.zip`` (atomically) and return ``(path, notes)``.

    Args:
        dest: Destination ``.zip``.
        description: What the user wrote (may be empty).
        settings: Current settings of the window (engine, device, BPH...), written as-is.
        recording: Last session recording to include (skipped if too big or missing).
        log_dir: Log folder (default ``LoggerSetup.LOG_DIR``).
        calibrations: Calibration file (default the user's ``adc_calibration.json``).

    Returns:
        The written path and human-readable notes (Spanish) about what was left out.
    """
    dest = Path(dest)
    if dest.suffix.lower() != ".zip":
        dest = dest.with_suffix(".zip")
    log_dir = log_dir or LoggerSetup.LOG_DIR
    calibrations = calibrations or config_dir() / "adc_calibration.json"
    notes: list[str] = []

    lines = [
        f"Advanced Timegrapher {__version__}",
        f"Created: {datetime.now().astimezone():%Y-%m-%d %H:%M:%S %z}",
        f"System: {platform.platform()} ({sys.platform}, {platform.machine()})",
        f"Python: {platform.python_version()} ({'frozen' if getattr(sys, 'frozen', False) else 'source'})",
    ]
    try:
        from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR

        lines.append(f"Qt: {QT_VERSION_STR} / PyQt6 {PYQT_VERSION_STR}")
    except Exception:  # pragma: no cover - Qt is a dependency
        pass
    lines += ["", "== Description ==", description.strip() or "(empty)", "", "== Settings =="]
    lines += [f"{k}: {v}" for k, v in (settings or {}).items()] or ["(none)"]
    lines += ["", "== Audio devices ==", audio_devices_text(), ""]

    logs = _log_files(log_dir)
    if not logs:
        notes.append(f"no se encontraron registros en {log_dir}")
    rec_ok = False
    if recording is not None:
        if not recording.is_file():
            notes.append("la grabación de la última sesión ya no existe")
        elif recording.stat().st_size > MAX_RECORDING_BYTES:
            mb = recording.stat().st_size / 1e6
            notes.append(
                f"la grabación ({mb:.0f} MB) es demasiado grande para GitHub: "
                "guárdala con Grabar Sonido y compártela aparte"
            )
        else:
            rec_ok = True
    lines += ["== Notes ==", *(notes or ["(none)"])]

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.tmp")
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("report.txt", "\n".join(lines) + "\n")
            for p in logs:
                z.write(p, f"logs/{p.name}")
            if calibrations.is_file():
                z.write(calibrations, "adc_calibration.json")
            if rec_ok and recording is not None:
                z.write(recording, "recording.flac", compress_type=zipfile.ZIP_STORED)
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _dbg.file_write(dest, logs=len(logs), recording=rec_ok)
    return dest, notes
