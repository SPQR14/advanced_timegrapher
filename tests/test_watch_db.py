from __future__ import annotations

from pathlib import Path

from adv_tg.utils.watch_db import load_watch_db


def test_load_watch_db_default() -> None:
    presets = load_watch_db()
    assert "Custom" in presets
    assert presets["Custom"] == (21600, 52.0)
    assert len(presets) > 1


def test_load_watch_db_filters_out_of_range(tmp_path: Path) -> None:
    csv_file = tmp_path / "test_db.csv"
    csv_file.write_text(
        "Valid,52.0,28800\n"
        "ZeroBph,52.0,0\n"
        "NegativeBph,52.0,-21600\n"
        "ExtremeBph,52.0,999999\n"
        "ZeroLiftAngle,0.0,21600\n"
        "HighLiftAngle,95.0,21600\n"
        "Malformed,not_a_number,21600\n",
        encoding="utf-8",
    )
    presets = load_watch_db(csv_file)
    assert "Valid" in presets
    assert presets["Valid"] == (28800, 52.0)
    assert "ZeroBph" not in presets
    assert "NegativeBph" not in presets
    assert "ExtremeBph" not in presets
    assert "ZeroLiftAngle" not in presets
    assert "HighLiftAngle" not in presets
    assert "Malformed" not in presets
