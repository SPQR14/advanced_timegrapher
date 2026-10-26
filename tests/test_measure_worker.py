"""MeasureStreamWorker (layer 5) with an injected, accelerated replay (no audio hardware)."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PyQt6")

from adv_tg.audio.file_replay import FileReplay  # noqa: E402
from adv_tg.core.adc_clock import AdcDrift  # noqa: E402
from adv_tg.core.models import BeatTelemetry, WatchConfig  # noqa: E402
from adv_tg.ui import measure_worker as mw  # noqa: E402
from adv_tg.ui.measure_worker import MeasureStreamWorker  # noqa: E402
from adv_tg.ui.worker_factory import create_worker  # noqa: E402
from adv_tg.utils.adc_calibration import Calibration, CalibrationStore  # noqa: E402
from adv_tg.utils.audio_files import audio_info, read_tags, write_audio  # noqa: E402
from tests.synth import synth_watch  # noqa: E402

FS = 48_000
SPEED = 6.0


class Factory:
    def __init__(self, signal: np.ndarray, speed: float = SPEED) -> None:
        self.signal = signal.astype(np.float32)
        self.speed = speed

    def __call__(self, stream, *, device, sample_rate, blocksize, latency):
        return FileReplay(stream, self.signal, 1024, speed=self.speed)


class Recorder:
    def __init__(self, worker) -> None:
        self.telemetry: list[BeatTelemetry] = []
        self.drift: list[tuple[float, float, float]] = []
        self.clock: list[tuple[float, int, str]] = []
        self.errors: list[str] = []
        self.waveforms = 0
        worker.telemetry_ready.connect(self.telemetry.append)
        worker.drift_updated.connect(lambda *a: self.drift.append(a))
        worker.clock_info.connect(lambda *a: self.clock.append(a))
        worker.error_occurred.connect(self.errors.append)
        worker.waveform_ready.connect(lambda *_: setattr(self, "waveforms", self.waveforms + 1))


@pytest.fixture
def watch() -> np.ndarray:
    x, _ = synth_watch(bph=28800, rate_s_per_day=6.0, beat_error_ms=0.5, amplitude_deg=270,
                       lift_angle_deg=52, seconds=18, seed=11)  # fmt: skip
    return x


def _run(qtbot, worker, rec_timeout: int = 15000) -> None:
    with qtbot.waitSignal(worker.session_finished, timeout=rec_timeout):
        worker.start_stream()


def test_measures_records_and_reports_clock(qtbot, watch, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(mw, "device_name", lambda device_id: "Mic de prueba")
    store = CalibrationStore(tmp_path / "cal.json")
    store.add(Calibration("Mic de prueba", FS, AdcDrift.manual(10.0, 0.2), datetime.now(UTC)))
    worker = MeasureStreamWorker(
        WatchConfig(bph=28800, lift_angle=52.0, sample_rate=FS),
        capture_factory=Factory(watch),
        calibration_store=store,
    )
    worker.set_input_device(3)
    rec = Recorder(worker)
    _run(qtbot, worker)
    assert rec.errors == []
    # >= 3 updates per second of audio, figures with their uncertainties.
    audio_s = watch.size / FS
    assert len(rec.telemetry) >= 3 * audio_s / SPEED * 0.8
    last = rec.telemetry[-1]
    assert last.locked and last.detection_ratio > 0.95
    # The stored calibration (ADC 10 ppm fast) is applied: 6 + 10 * 0.0864 s/d.
    assert last.rate_spd == pytest.approx(6.0 + 0.864, abs=0.5)
    assert last.beat_error_ms == pytest.approx(0.5, abs=0.06)
    assert last.amplitude_deg == pytest.approx(270, rel=0.04)
    assert math.isfinite(last.rate_uncertainty_spd) and last.confidence > 0
    assert any(t.beats and t.beats[0].waveform_start_s is not None for t in rec.telemetry)
    ev = next(t.beats[0] for t in reversed(rec.telemetry) if t.beats)
    assert ev.waveform_start_s <= ev.timestamp_p3 < ev.waveform_start_s + ev.raw_waveform.size / FS
    assert rec.drift and rec.drift[-1][1] == pytest.approx(10.0)
    assert rec.clock[-1][1] == 3 and rec.clock[-1][2].startswith("calibración guardada")
    assert rec.waveforms > 0
    # The session was recorded with its tags.
    assert worker.has_recording
    path = worker.take_recording()
    assert path is not None and not worker.has_recording
    info = audio_info(path)
    assert abs(info.frames - watch.size) < 2048 and info.subtype == "PCM_24"
    tags = read_tags(path)
    assert tags["ADV_TG_DEVICE"] == "Mic de prueba" and tags["ADV_TG_BPH"] == "28800"
    assert float(tags["ADV_TG_ADC_DRIFT_PPM"]) == pytest.approx(10.0)
    path.unlink()
    worker.shutdown()


def test_next_session_discards_unsaved_recording(qtbot, watch, monkeypatch) -> None:
    monkeypatch.setattr(mw, "device_name", lambda device_id: None)
    worker = MeasureStreamWorker(
        WatchConfig(bph=28800, sample_rate=FS), capture_factory=Factory(watch[: FS * 2])
    )
    rec = Recorder(worker)
    _run(qtbot, worker)
    first = worker.take_recording()
    assert first is not None
    first.unlink()
    _run(qtbot, worker)
    assert worker.has_recording
    second = worker._recording
    _run(qtbot, worker)  # the unsaved second recording is deleted
    assert second is not None and not second.exists()
    worker.shutdown()
    assert not worker.has_recording
    assert rec.clock and rec.clock[-1][1] == 0 and math.isnan(rec.clock[-1][0])


def test_replay_env_var(qtbot, tmp_path, monkeypatch) -> None:
    x, _ = synth_watch(bph=21600, amplitude_deg=280, seconds=3, seed=3)
    f = write_audio(tmp_path / "toma.flac", x, FS, tags={"ADV_TG_ADC_DRIFT_PPM": "8.3",
                    "ADV_TG_ADC_DRIFT_SYSTEMATIC_PPM": "6", "ADV_TG_ADC_DRIFT_LEVEL": "3"})  # fmt: skip
    monkeypatch.setenv(mw.REPLAY_ENV_VAR, str(f))
    monkeypatch.setattr(mw, "FileReplay", lambda s, x, b: FileReplay(s, x, b, speed=SPEED))
    worker = MeasureStreamWorker(WatchConfig(bph=21600, sample_rate=FS))
    assert worker.replay_path == f
    rec = Recorder(worker)
    _run(qtbot, worker)
    assert rec.errors == [] and not worker.has_recording  # replay is never recorded
    assert rec.clock[-1][1] == 3 and "archivo" in rec.clock[-1][2]
    worker.shutdown()
    # A sample-rate mismatch is reported, not crashed on.
    worker = MeasureStreamWorker(WatchConfig(bph=21600, sample_rate=96000))
    rec = Recorder(worker)
    _run(qtbot, worker)
    assert rec.errors and "48000 Hz" in rec.errors[0]
    worker.shutdown()


def test_factory_returns_measuring_worker() -> None:
    w = create_worker(1, WatchConfig())
    assert isinstance(w, MeasureStreamWorker)
    w.shutdown()


def test_open_failure_leaves_no_temp_file(qtbot, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(mw, "device_name", lambda device_id: None)
    monkeypatch.setattr(mw.tempfile, "tempdir", str(tmp_path))

    def broken(stream, **_):
        raise RuntimeError("no hay dispositivo")

    worker = MeasureStreamWorker(WatchConfig(sample_rate=FS), capture_factory=broken)
    rec = Recorder(worker)
    _run(qtbot, worker, 5000)
    assert rec.errors and not worker.has_recording
    worker.shutdown()
    assert list(Path(tmp_path).glob("adv_tg_sesion_*")) == []


def test_config_change_during_session_is_warned(qtbot, watch, monkeypatch) -> None:
    monkeypatch.setattr(mw, "device_name", lambda device_id: None)
    worker = MeasureStreamWorker(
        WatchConfig(bph=28800, sample_rate=FS), capture_factory=Factory(watch, speed=1.0)
    )
    rec = Recorder(worker)
    worker.start_stream()
    worker.update_watch_config(WatchConfig(bph=28800, sample_rate=FS))  # unchanged: silent
    worker.update_watch_config(WatchConfig(bph=21600, sample_rate=FS))
    qtbot.waitUntil(lambda: bool(rec.errors), timeout=2000)
    assert "próxima sesión" in rec.errors[0] and len(rec.errors) == 1
    worker.shutdown()
