"""Layer 4 persistence: per-device calibration store, recording tags and the tools using them."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from adv_tg.core.adc_clock import AdcDrift, ClockLevel, drift_from_tags, drift_to_tags
from adv_tg.tools import adc_calibration as tool
from adv_tg.tools import measure
from adv_tg.tools.capture_check import main as capture_check_main
from adv_tg.utils.adc_calibration import (
    CONFIG_DIR_ENV,
    MAX_HISTORY,
    Calibration,
    CalibrationStore,
    CalibrationStoreError,
    config_dir,
    recording_tags,
)
from adv_tg.utils.audio_files import read_tags, update_tags, write_audio

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
DEV = "CM18 USB"


def _cal(ppm: float, sigma: float, when: datetime, device: str = DEV, fs: int = 48000):
    return Calibration(device, fs, AdcDrift.manual(ppm, sigma), when)


# ---------------------------------------------------------------------- tags <-> AdcDrift
def test_drift_tags_round_trip() -> None:
    d = AdcDrift(8.25, 0.12, 0.5, ClockLevel.HOST_DISCIPLINED, "host-envelope", span_s=600.0,
                 note="dos\nlíneas")  # fmt: skip
    tags = drift_to_tags(d, calibrated_at="2026-10-26T10:00:00+00:00")
    assert tags["ADV_TG_ADC_DRIFT_NOTE"] == "dos líneas"
    back = drift_from_tags({k.lower(): v for k, v in tags.items()})  # keys case-insensitive
    assert back is not None
    assert (back.ppm, back.sigma_ppm, back.systematic_ppm) == (8.25, 0.12, 0.5)
    assert back.level == ClockLevel.HOST_DISCIPLINED and back.method == "host-envelope"
    assert back.span_s == 600.0


def test_drift_from_tags_missing_and_malformed() -> None:
    assert drift_from_tags({"COMMENT": "x"}) is None
    u = drift_from_tags(drift_to_tags(AdcDrift.uncalibrated()))
    assert u is not None and u.level == ClockLevel.UNCALIBRATED and u.ppm == 0.0
    for bad in ({"ADV_TG_ADC_DRIFT_PPM": "abc"}, {"ADV_TG_ADC_DRIFT_PPM": "nan"},
                {"ADV_TG_ADC_DRIFT_PPM": "1", "ADV_TG_ADC_DRIFT_LEVEL": "7"},
                {"ADV_TG_ADC_DRIFT_PPM": "1", "ADV_TG_ADC_DRIFT_SIGMA_PPM": "-1"}):  # fmt: skip
        with pytest.raises(ValueError):
            drift_from_tags(bad)


# ---------------------------------------------------------------------- store
def test_config_dir_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path / "cfg"))
    assert config_dir() == tmp_path / "cfg"
    assert CalibrationStore().path == tmp_path / "cfg" / "adc_calibration.json"


def test_store_add_records_and_device_matching(tmp_path: Path) -> None:
    store = CalibrationStore(tmp_path / "c.json")
    assert store.records() == [] and store.best(DEV, 48000) is None
    store.add(_cal(8.3, 6.0, T0))
    store.add(_cal(8.1, 0.5, T0 + timedelta(days=1), device="  cm18 usb "))
    store.add(_cal(-3.0, 1.0, T0, device="Otra"))
    store.add(_cal(9.0, 1.0, T0, fs=44100))
    recs = store.records(" Cm18 USB", 48000)
    assert [r.drift.ppm for r in recs] == [8.3, 8.1]
    assert {r.device for r in recs} == {DEV}  # first spelling kept
    data = json.loads(store.path.read_text(encoding="utf-8"))
    assert data["version"] == 1 and len(data["calibrations"]) == 4
    assert store.forget("cm18 usb", 48000) == 2
    assert len(store.records()) == 2 and store.forget("nadie") == 0


def test_store_rejects_invalid_records(tmp_path: Path) -> None:
    store = CalibrationStore(tmp_path / "c.json")
    for bad in (
        Calibration(DEV, 48000, AdcDrift.uncalibrated(), T0),
        Calibration(" ", 48000, AdcDrift.manual(1, 1), T0),
        Calibration(DEV, 0, AdcDrift.manual(1, 1), T0),
        Calibration(DEV, 48000, AdcDrift.manual(1, 1), T0.replace(tzinfo=None)),
    ):
        with pytest.raises(ValueError):
            store.add(bad)
    assert not store.path.exists()


def test_best_accounts_for_crystal_aging(tmp_path: Path) -> None:
    store = CalibrationStore(tmp_path / "c.json")
    store.add(_cal(8.0, 0.05, T0))  # PPS-grade, a year old by T1
    store.add(_cal(9.0, 0.6, T0 + timedelta(days=360)))  # fresh NTP-grade estimate
    t1 = T0 + timedelta(days=365)
    best = store.best(DEV, 48000, t1)
    assert best is not None and best.drift.ppm == 9.0
    # A week after the PPS calibration the PPS one still wins.
    best = store.best(DEV, 48000, T0 + timedelta(days=7))
    assert best is not None and best.drift.ppm == 8.0
    aged = _cal(8.0, 0.05, T0).effective(t1)
    assert aged.systematic_ppm == pytest.approx(math.hypot(0.05, 365 / 365.25), rel=1e-6)
    # Age is symmetric: a calibration made after a recording also ages it.
    assert _cal(1, 1, T0).age_days(T0 - timedelta(days=10)) == pytest.approx(10)


def test_best_ties_go_to_newest(tmp_path: Path) -> None:
    store = CalibrationStore(tmp_path / "c.json")
    store.add(_cal(1.0, 0.5, T0))
    store.add(_cal(2.0, 0.5, T0 + timedelta(seconds=1)))
    best = store.best(DEV, 48000, T0, aging_ppm_per_year=0.0)
    assert best is not None and best.drift.ppm == 2.0


def test_history_is_capped(tmp_path: Path) -> None:
    store = CalibrationStore(tmp_path / "c.json")
    store.add(_cal(0.0, 1.0, T0, device="Otra"))
    for k in range(MAX_HISTORY + 5):
        store.add(_cal(float(k), 1.0, T0 + timedelta(hours=k)))
    recs = store.records(DEV)
    assert len(recs) == MAX_HISTORY and recs[0].drift.ppm == 5.0
    assert len(store.records("Otra")) == 1


@pytest.mark.parametrize("content", ["{no json", '{"version": 1}', '{"version": 99, "calibrations": []}',
                                     '{"version": 1, "calibrations": [{"device": "x"}]}'])  # fmt: skip
def test_corrupt_store_is_never_overwritten(tmp_path: Path, content: str) -> None:
    p = tmp_path / "c.json"
    p.write_text(content, encoding="utf-8")
    store = CalibrationStore(p)
    with pytest.raises(CalibrationStoreError):
        store.records()
    with pytest.raises(CalibrationStoreError):
        store.add(_cal(1.0, 1.0, T0))
    assert p.read_text(encoding="utf-8") == content


def test_recording_tags(tmp_path: Path) -> None:
    store = CalibrationStore(tmp_path / "c.json")
    tags, cal = recording_tags(DEV, 48000, store, T0)
    assert cal is None and tags["ADV_TG_ADC_DRIFT_LEVEL"] == "0"
    assert tags["ADV_TG_DEVICE"] == DEV and tags["ADV_TG_SAMPLE_RATE"] == "48000"
    store.add(_cal(8.3, 0.5, T0 - timedelta(days=30)))
    tags, cal = recording_tags(DEV, 48000, store, T0)
    assert cal is not None
    d = drift_from_tags(tags)
    assert d is not None and d.ppm == 8.3 and d.level == ClockLevel.EXTERNAL
    assert d.systematic_ppm > 0.5  # aged 30 days
    assert tags["ADV_TG_ADC_DRIFT_CALIBRATED_AT"].startswith("2026-09-01")
    assert tags["ADV_TG_RECORDED_AT"].startswith("2026-10-01")
    tags, cal = recording_tags(None, 48000, store, T0)
    assert cal is None and "ADV_TG_DEVICE" not in tags


# ---------------------------------------------------------------------- tools
@pytest.fixture
def tone_file(tmp_path: Path) -> Path:
    t = np.arange(48000) / 48000
    return write_audio(tmp_path / "toma.flac", 0.1 * np.sin(2 * np.pi * 440 * t), 48000)


def test_cli_set_list_show_tag_forget(tone_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert tool.main(["list"]) == tool.EXIT_OK
    assert "No hay calibraciones" in capsys.readouterr().out
    code = tool.main(["set", "--device", DEV, "--ppm", "8.3", "--sigma", "6", "--note", "Bulova"])
    assert code == tool.EXIT_OK
    assert tool.main(["set", "--device", DEV, "--ppm", "8.1", "--sigma", "0.4"]) == tool.EXIT_OK
    capsys.readouterr()
    assert tool.main(["list"]) == tool.EXIT_OK
    out = capsys.readouterr().out
    assert "CM18 USB @ 48000 Hz (2 registro(s))" in out
    assert "→" in out.split("+8.100")[0].splitlines()[-1]  # the 0.4 ppm one applies

    assert tool.main(["tag", str(tone_file), "--device", DEV, "--date", "2026-10-26"]) == 0
    tags = read_tags(tone_file)
    assert tags["ADV_TG_ADC_DRIFT_PPM"] == "8.1" and tags["ADV_TG_DEVICE"] == DEV
    capsys.readouterr()
    assert tool.main(["show", str(tone_file)]) == tool.EXIT_OK
    assert "→ reloj ADC: +8.100" in capsys.readouterr().out

    assert tool.main(["forget", "--device", "cm18 usb"]) == tool.EXIT_OK
    assert "Borradas 2" in capsys.readouterr().out
    assert tool.main(["tag", str(tone_file), "--device", DEV]) == tool.EXIT_ERROR


def test_cli_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert tool.main([]) == tool.EXIT_ERROR
    assert tool.main(["set", "--device", DEV, "--ppm", "1", "--sigma", "-1"]) == tool.EXIT_ERROR
    assert tool.main(["set", "--device", DEV, "--ppm", "1", "--sigma", "1", "--date", "ayer"]) == 2
    assert tool.main(["show", str(tmp_path / "no.flac")]) == tool.EXIT_ERROR
    CalibrationStore().path.parent.mkdir(parents=True, exist_ok=True)
    CalibrationStore().path.write_text("roto", encoding="utf-8")
    capsys.readouterr()
    assert tool.main(["list"]) == tool.EXIT_ERROR
    assert "no se pudo leer el archivo de calibraciones" in capsys.readouterr().out


def test_measure_resolves_drift(tmp_path: Path, tone_file: Path) -> None:
    # Nothing known.
    assert measure.resolve_adc_drift(tone_file) == (0.0, 0.0, "sin calibrar")
    ppm, sigma, src = measure.resolve_adc_drift(tone_file, device=DEV)
    assert (ppm, sigma) == (0.0, 0.0) and "no hay calibración" in src
    # Command line always wins; sigma alone is an error.
    assert measure.resolve_adc_drift(tone_file, ppm=2.0)[:2] == (2.0, 0.0)
    with pytest.raises(ValueError):
        measure.resolve_adc_drift(tone_file, sigma_ppm=1.0)
    # Level-0 tags are ignored; the file's calibration is used when present.
    update_tags(tone_file, {**drift_to_tags(AdcDrift.uncalibrated()), "ADV_TG_RECORDED_AT":
                            "2026-10-01T12:00:00+00:00"})  # fmt: skip
    assert measure.resolve_adc_drift(tone_file)[0] == 0.0
    update_tags(tone_file, drift_to_tags(AdcDrift.manual(8.0, 2.0)))
    ppm, sigma, src = measure.resolve_adc_drift(tone_file)
    assert (ppm, sigma) == (8.0, 2.0) and src.startswith("etiquetas del archivo")
    # With --device, the stored calibration (aged to the recording date) competes on sigma.
    CalibrationStore().add(_cal(8.4, 0.3, T0 + timedelta(days=5)))
    ppm, sigma, src = measure.resolve_adc_drift(tone_file, device="cm18 usb")
    assert ppm == 8.4 and src.startswith("calibración guardada")
    assert sigma == pytest.approx(math.hypot(0.3, 5 / 365.25), rel=1e-6)


def test_measure_cli_uses_file_tags(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.synth import synth_watch

    x, _ = synth_watch(bph=28800, rate_s_per_day=6.0, beat_error_ms=0.5, amplitude_deg=270,
                       seconds=15, seed=3)  # fmt: skip
    f = write_audio(tmp_path / "r.flac", x, 48000, tags=drift_to_tags(AdcDrift.manual(10.0, 1.0)))
    js = tmp_path / "m.json"
    assert measure.main([str(f), "--bph", "28800", "--json", str(js)]) == measure.EXIT_OK
    out = capsys.readouterr().out
    assert "Reloj ADC          : +10.00 ± 1.00 ppm (etiquetas del archivo" in out
    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["adc_drift_ppm"] == 10.0 and data["metrics"]["rate_calibrated"]
    # --adc-drift-ppm 0 forces no correction.
    assert measure.main([str(f), "--bph", "28800", "--adc-drift-ppm", "0"]) == measure.EXIT_OK
    assert "(línea de órdenes)" in capsys.readouterr().out


def test_capture_check_replay_keeps_source_tags(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    t = np.arange(48000) / 48000
    src = write_audio(
        tmp_path / "src.flac",
        0.1 * np.sin(2 * np.pi * 440 * t),
        48000,
        tags={**drift_to_tags(AdcDrift.manual(8.0, 0.5)), "ADV_TG_DEVICE": DEV, "OTRA": "x"},
    )
    out = tmp_path / "out.wav"
    code = capture_check_main(["--file", str(src), "--secs", "1", "--wav", str(out)])
    assert code == 0, capsys.readouterr().out
    tags = read_tags(out)
    assert tags["ADV_TG_ADC_DRIFT_PPM"] == "8" and tags["ADV_TG_DEVICE"] == DEV
    assert "OTRA" not in tags


def test_capture_check_stamps_stored_calibration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import argparse

    from adv_tg.tools import capture_check

    monkeypatch.setattr(capture_check, "_device_name", lambda device: DEV)
    args = argparse.Namespace(file=None, fake_source=False, device=3)
    tags = capture_check._recording_tags(args, 48000)
    assert tags["ADV_TG_ADC_DRIFT_LEVEL"] == "0" and tags["ADV_TG_DEVICE"] == DEV
    assert "nivel 0" in capsys.readouterr().out
    CalibrationStore().add(_cal(8.1, 0.4, datetime.now(UTC)))
    tags = capture_check._recording_tags(args, 48000)
    assert tags["ADV_TG_ADC_DRIFT_PPM"] == "8.1"
    assert "se guarda la calibración de 'CM18 USB'" in capsys.readouterr().out
    # An unreadable store never blocks a recording.
    CalibrationStore().path.write_text("roto", encoding="utf-8")
    tags = capture_check._recording_tags(args, 48000)
    assert tags["ADV_TG_ADC_DRIFT_LEVEL"] == "0" and tags["ADV_TG_DEVICE"] == DEV
    assert "AVISO" in capsys.readouterr().out
