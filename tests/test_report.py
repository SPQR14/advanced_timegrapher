"""Report bundle (zip for GitHub issues) and log folder location."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from adv_tg import __version__
from adv_tg.utils import logger_setup, report
from adv_tg.utils.report import build_report, default_report_name


def test_build_report_contents(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "adv_tg.log").write_text("hola\n")
    (logs / "adv_tg.log.1").write_text("antiguo\n")
    (logs / "faulthandler.log").write_text("")
    (logs / "otro.txt").write_text("no")
    cal = tmp_path / "adc_calibration.json"
    cal.write_text('{"version": 1, "calibrations": []}')
    rec = tmp_path / "rec.flac"
    rec.write_bytes(b"x" * 1000)
    out, notes = build_report(
        tmp_path / "out" / "r",
        description="El tac se pierde",
        settings={"bph": 21600, "quartz": False},
        recording=rec,
        log_dir=logs,
        calibrations=cal,
    )
    assert out.suffix == ".zip" and out.exists() and notes == []
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        text = z.read("report.txt").decode()
    assert names == {
        "report.txt",
        "logs/adv_tg.log",
        "logs/adv_tg.log.1",
        "logs/faulthandler.log",
        "adc_calibration.json",
        "recording.flac",
    }
    assert f"Advanced Timegrapher {__version__}" in text
    assert "El tac se pierde" in text and "bph: 21600" in text and "== Audio devices ==" in text
    assert not list((tmp_path / "out").glob(".*tmp"))


def test_report_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report, "MAX_RECORDING_BYTES", 10)
    monkeypatch.setattr(report, "MAX_LOGS_BYTES", 10)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "adv_tg.log").write_text("x" * 100)
    rec = tmp_path / "rec.flac"
    rec.write_bytes(b"x" * 100)
    out, notes = build_report(
        tmp_path / "r.zip", recording=rec, log_dir=logs, calibrations=tmp_path / "none.json"
    )
    with zipfile.ZipFile(out) as z:
        assert z.namelist() == ["report.txt"]
    assert any("demasiado grande" in n for n in notes)
    out, notes = build_report(
        tmp_path / "r2.zip", recording=tmp_path / "gone.flac", log_dir=tmp_path / "nope"
    )
    assert any("ya no existe" in n for n in notes) and any("registros" in n for n in notes)


def test_default_report_name() -> None:
    assert default_report_name().startswith(f"adv_tg_report_{__version__}_")


def test_log_dir_per_platform(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ADV_TG_LOG_DIR", str(tmp_path / "x"))
    assert logger_setup.default_log_dir() == (tmp_path / "x").resolve()
    monkeypatch.delenv("ADV_TG_LOG_DIR")
    monkeypatch.setattr(logger_setup.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(logger_setup.sys, "platform", "darwin")
    assert logger_setup.default_log_dir() == tmp_path / "Library" / "Logs" / "adv_tg"
    monkeypatch.setattr(logger_setup.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData"))
    assert logger_setup.default_log_dir() == tmp_path / "AppData" / "adv_tg" / "logs"
    monkeypatch.setattr(logger_setup.sys, "platform", "linux")
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    assert logger_setup.default_log_dir() == tmp_path / ".local" / "state" / "adv_tg" / "logs"


def test_tests_never_log_into_the_repository() -> None:
    repo_logs = Path(__file__).resolve().parents[1] / "logs"
    assert logger_setup.LoggerSetup.LOG_DIR != repo_logs
