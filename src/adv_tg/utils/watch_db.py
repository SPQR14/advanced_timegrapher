"""Utility for loading watch caliber specifications from ``watch_db.csv``."""

from __future__ import annotations

import csv
from pathlib import Path

from adv_tg.utils.debugger import Debugger
from adv_tg.utils.logger_setup import LoggerSetup

logger = LoggerSetup.setup_logger(__name__)
_dbg = Debugger.get(__name__)

WATCH_DB_PATH: Path = Path(__file__).resolve().parent / "db" / "watch_db.csv"


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
                except ValueError as exc:
                    logger.warning(
                        "Skipping non-numeric row %d in '%s' (%s): %s",
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
