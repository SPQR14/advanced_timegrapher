"""MOCK engine tests (headless, no audio hardware)."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PyQt6")

from adv_tg.core.models import BeatTelemetry, WatchConfig  # noqa: E402
from adv_tg.mock.generator import (  # noqa: E402
    MockTelemetryModel,
    beat_delta_t_s,
    generate_tick_train,
    save_wav,
)
from adv_tg.mock.worker import MockStreamWorker  # noqa: E402
from adv_tg.ui.null_engine import NOT_IMPLEMENTED_MESSAGE, NullStreamWorker  # noqa: E402
from adv_tg.ui.worker_factory import create_worker  # noqa: E402


def test_mock_emits_telemetry_and_finishes(qtbot) -> None:
    worker = MockStreamWorker(WatchConfig(), seed=1234, stabilization_s=0.3, duration_s=30.0)
    received: list[BeatTelemetry] = []
    waveforms: list[int] = []
    worker.telemetry_ready.connect(received.append)
    worker.waveform_ready.connect(lambda t, y: waveforms.append(len(y)))
    levels: list[tuple[float, float, bool]] = []
    worker.level_updated.connect(lambda p, r, s: levels.append((p, r, s)))

    worker.start_stream()
    assert worker.isRunning()
    qtbot.wait(2000)
    assert len(received) >= 6, f"only {len(received)} telemetry updates in 2 s"
    assert len(waveforms) >= 25
    assert len(levels) >= 15
    assert all(-30.0 < p < -1.0 and r < p and not s for p, r, s in levels)
    last = received[-1]
    assert isinstance(last, BeatTelemetry)
    assert last.beats and len(last.beats[-1].raw_waveform) > 0
    assert 200.0 < last.amplitude_deg < 360.0

    with qtbot.waitSignal(worker.session_finished, timeout=3000):
        worker.stop_stream()
    assert not worker.isRunning()


def test_mock_model_is_deterministic_with_seed() -> None:
    def run(seed: int) -> list[float]:
        model = MockTelemetryModel(seed=seed)
        out = []
        for k in range(20):
            s = model.sample(k * 0.25, 0.25, 10.0, 4.5, 0.35, 282.0)
            out += [s.rate_spd, s.beat_error_ms, s.amplitude_deg, s.snr_db]
        return out

    assert run(7) == run(7)
    assert run(7) != run(8)


def test_tick_train_and_wav(tmp_path) -> None:
    a = generate_tick_train(2.0, 48000, 21600, rate_spd=5.0, beat_error_ms=0.4, seed=3)
    b = generate_tick_train(2.0, 48000, 21600, rate_spd=5.0, beat_error_ms=0.4, seed=3)
    assert len(a) == 96000
    np.testing.assert_array_equal(a, b)
    assert 0.5 < float(np.max(np.abs(a))) <= 0.9 + 1e-9
    out = save_wav(tmp_path / "mock", a, 48000)
    assert out.suffix == ".wav" and out.stat().st_size > 96000 * 2


def test_beat_delta_t_is_realistic() -> None:
    dt = beat_delta_t_s(amplitude_deg=280.0, lift_angle_deg=52.0, bph=21600)
    assert 0.008 < dt < 0.012


def test_factory_and_null_engine(qtbot) -> None:
    from adv_tg.ui.capture_worker import CaptureStreamWorker

    assert isinstance(create_worker(None), MockStreamWorker)
    from adv_tg.ui.measure_worker import MeasureStreamWorker

    hw = create_worker(3)
    assert isinstance(hw, MeasureStreamWorker) and isinstance(hw, CaptureStreamWorker)
    assert not hw.isRunning()
    null = NullStreamWorker()  # kept only as a fallback engine
    errors: list[str] = []
    null.error_occurred.connect(errors.append)
    with qtbot.waitSignal(null.session_finished, timeout=1000):
        null.start_stream()
    assert errors == [NOT_IMPLEMENTED_MESSAGE]
    assert not null.isRunning()


def test_base_contract_is_strict() -> None:
    from PyQt6.QtCore import QObject

    from adv_tg.ui.stream_worker_base import BaseStreamWorker

    own = {n for n in vars(BaseStreamWorker) if not n.startswith("_")}
    assert own == {
        "telemetry_ready",
        "waveform_ready",
        "drift_updated",
        "clock_info",
        "capture_health",
        "level_updated",
        "error_occurred",
        "session_finished",
        "set_input_device",
        "update_watch_config",
        "start_stream",
        "stop_stream",
        "isRunning",
        "shutdown",
    }
    base = BaseStreamWorker()
    public_attrs = {n for n in vars(base) if not n.startswith("_")} - set(dir(QObject))
    assert public_attrs == {
        "synthetic_mode",
        "synthetic_rate_spd",
        "synthetic_beat_error_ms",
        "synthetic_amplitude_deg",
    }
