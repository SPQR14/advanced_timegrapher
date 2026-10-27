"""Utility for loading watch caliber specifications from ``watch_db.csv``."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

from adv_tg.utils.debugger import Debugger
from adv_tg.utils.logger_setup import LoggerSetup

logger = LoggerSetup.setup_logger(__name__)
_dbg = Debugger.get(__name__)


def _resolve_watch_db_path() -> Path:
    """Resolve path to ``watch_db.csv`` in development or frozen PyInstaller builds."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        meipass = Path(sys._MEIPASS)
        for cand in (
            meipass / "adv_tg" / "utils" / "db" / "watch_db.csv",
            meipass / "utils" / "db" / "watch_db.csv",
            meipass / "db" / "watch_db.csv",
            meipass / "Resources" / "adv_tg" / "utils" / "db" / "watch_db.csv",
            meipass.parent / "Resources" / "adv_tg" / "utils" / "db" / "watch_db.csv",
        ):
            if cand.is_file():
                return cand
    return Path(__file__).resolve().parent / "db" / "watch_db.csv"


WATCH_DB_PATH: Path = _resolve_watch_db_path()


def load_watch_db(csv_path: Path | str | None = None) -> dict[str, tuple[int, float]]:
    """Load watch caliber presets from ``watch_db.csv``.

    Each row in ``watch_db.csv`` has the format:
        ``<caliber_name>,<lift_angle>,<bph>``

    Args:
        csv_path: Optional override path to the CSV file. Defaults to
            ``src/adv_tg/utils/db/watch_db.csv``.

    Returns:
        Ordered dictionary mapping ``caliber_name`` to ``(bph, lift_angle)``.
    """
    path = Path(csv_path) if csv_path is not None else WATCH_DB_PATH
    presets: dict[str, tuple[int, float]] = {}

    if not path.exists():
        _dbg.file_read(path, ok=False, error="not found; using fallback Custom preset")
        return {"Custom": (21600, 52.0)}

    try:
        with path.open(mode="r", encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            for line_num, row in enumerate(reader, start=1):
                if not row or all(not cell.strip() for cell in row):
                    continue
                if len(row) < 3:
                    logger.warning(
                        "Skipping malformed row %d in '%s': %s", line_num, path.name, row
                    )
                    continue
                name = row[0].strip()
                try:
                    lift_angle = float(row[1].strip())
                    bph = int(float(row[2].strip()))
                    if not (1000 <= bph <= 120000):
                        raise ValueError(f"bph fuera de rango válido [1000, 120000]: {bph}")
                    if not (10.0 <= lift_angle <= 90.0):
                        raise ValueError(
                            f"lift_angle fuera de rango válido [10.0, 90.0]: {lift_angle}"
                        )
                except ValueError as exc:
                    logger.warning(
                        "Skipping invalid or out-of-range row %d in '%s' (%s): %s",
                        line_num,
                        path.name,
                        row,
                        exc,
                    )
                    continue
                presets[name] = (bph, lift_angle)
    except Exception as exc:
        logger.error("Failed to read watch database '%s': %s", path, exc, exc_info=True)
        if not presets:
            presets["Custom"] = (21600, 52.0)

    if "Custom" not in presets:
        presets = {"Custom": (21600, 52.0), **presets}

    _dbg.file_read(path, calibers=len(presets))
    return presets
