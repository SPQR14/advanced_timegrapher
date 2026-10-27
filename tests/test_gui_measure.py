"""GUI with the real measuring engine (layer 5) fed by an accelerated synthetic replay."""

from __future__ import annotations

import math
import re

import numpy as np
import pytest

pytest.importorskip("PyQt6")

from adv_tg.mock.worker import MockStreamWorker  # noqa: E402
from adv_tg.ui import main_window as mw  # noqa: E402
from adv_tg.ui import measure_worker as mwk  # noqa: E402
from adv_tg.ui.measure_worker import MeasureStreamWorker  # noqa: E402
from adv_tg.utils.audio_files import read_tags  # noqa: E402
from adv_tg.utils.audio_io import SYNTHETIC_DEVICE_NAME, AudioDeviceInfo  # noqa: E402
from tests.synth import synth_watch  # noqa: E402
from tests.test_measure_worker import Factory  # noqa: E402


@pytest.fixture
def window(qtbot, monkeypatch: pytest.MonkeyPatch):
    devices = [
        AudioDeviceInfo(None, SYNTHETIC_DEVICE_NAME, 1, 48000.0, is_synthetic=True),
        AudioDeviceInfo(7, "Fake CM18", 1, 48000.0),
    ]
    x, _ = synth_watch(bph=28800, rate_s_per_day=4.0, beat_error_ms=0.3, amplitude_deg=280,
                       lift_angle_deg=52, seconds=24, seed=5)  # fmt: skip
    monkeypatch.setattr(mwk, "device_name", lambda device_id: "Fake CM18")

    def fake_create_worker(device_id, config=None, *, seed=None, parent=None):
        if device_id is None:
            worker = MockStreamWorker(config, seed=seed, parent=parent)
        else:
            worker = MeasureStreamWorker(
                config, capture_factory=Factory(x, speed=4.0), parent=parent
            )
        worker.set_input_device(device_id)
        return worker

    monkeypatch.setattr(mw, "list_input_devices", lambda: devices)
    monkeypatch.setattr(mw, "create_worker", fake_create_worker)
    win = mw.MainWin()
    qtbot.addWidget(win)
    win.show()
    win.set_session_timing(stabilization_s=0, duration_s=60)
    win.cmb_bph.setCurrentText("28800")
    win.spn_lift.setValue(52.0)
    yield win
    win.close()


def _number(text: str) -> float:
    m = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    assert m, text
    return float(m.group(0))


def test_real_engine_in_the_gui(window, qtbot, tmp_path, monkeypatch) -> None:
    window.cmb_device.setCurrentIndex(1)
    assert isinstance(window.dsp_worker, MeasureStreamWorker)
    window.start_capture()
    assert window.lbl_session_status.text() == window.translator.tr("status_running")
    # Before the lock the figures stay empty and the bar shows the input level.
    assert window.card_rate.value_label.text() == "---.-"
    qtbot.waitUntil(lambda: window.card_rate.value_label.text() != "---.-", timeout=8000)
    qtbot.wait(2500)
    assert _number(window.card_rate.value_label.text()) == pytest.approx(4.0, abs=1.0)
    assert _number(window.card_beat_error.value_label.text()) == pytest.approx(0.3, abs=0.1)
    assert _number(window.card_amplitude.value_label.text()) == pytest.approx(280, rel=0.05)
    # Quality bar now reflects detection (SNR and share of usable beats).
    assert "SNR" in window.lbl_snr_value.text() and window.prg_snr.value() > 50
    # Paper tape: one point per beat (tic and tac rows), slope = rate (+4 s/d = 4/86.4 ms/s),
    # rows apart by the tic/tac offset; the view contains them (autoscale).
    xe, ye = np.array(window._tape_x_even), np.array(window._tape_ms_even)
    xo, yo = np.array(window._tape_x_odd), np.array(window._tape_ms_odd)
    assert xe.size > 20 and xo.size > 20 and abs(xe.size - xo.size) <= 3
    assert np.all(np.isfinite(ye)) and np.all(np.diff(xe) > 0)
    for x_, y_ in ((xe, ye), (xo, yo)):
        assert np.polyfit(x_, y_, 1)[0] == pytest.approx(4.0 / 86.4, rel=0.25)
    lo, hi = window.paper_tape_plot.getViewBox().viewRange()[1]
    assert lo <= min(ye.min(), yo.min()) and max(ye.max(), yo.max()) <= hi
    # COSC limits drawn as slopes over the test duration (60 s)
    cx, cy = window.cosc_upper_line.getData()
    assert cx[-1] == pytest.approx(60.0) and cy[-1] == pytest.approx(6.0 * 60.0 / 86.4)
    # Oscilloscope markers at the real instants, inside the drawn beat.
    t, y = window.osc_wave_curve.getData()
    assert t is not None and len(t) > 0
    assert window.line_p3.isVisible() and 0.0 <= window.line_p3.value() <= float(t[-1])
    # Clock: no stored calibration -> uncalibrated label (live wall-clock estimate needs time).
    assert "ppm" in window.lbl_stat_drift_val.text()

    # Recording: armed during the session, saved when it ends.
    target = tmp_path / "toma.flac"
    monkeypatch.setattr(mw.QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    window.record_audio()
    window.stop_capture()
    assert target.exists()
    tags = read_tags(target)
    assert tags["ADV_TG_DEVICE"] == "Fake CM18" and tags["ADV_TG_BPH"] == "28800"
    assert not window.dsp_worker.has_recording


def test_low_confidence_figures_are_hidden(window) -> None:
    from adv_tg.core.models import BeatTelemetry

    window.cmb_device.setCurrentIndex(1)
    window._set_capture_state("MIDIENDO")
    low = BeatTelemetry(rate_spd=5.0, beat_error_ms=0.4, amplitude_deg=270.0,
                        beat_times_sec=np.empty(0), envelope=np.empty(0), confidence=0.1,
                        snr_db=30.0, usable_ratio=0.9)  # fmt: skip
    window._on_telemetry_ready(low)
    assert window.card_rate.value_label.text() == "---.-" and window._rate_history == []
    mid = BeatTelemetry(rate_spd=5.0, beat_error_ms=math.nan, amplitude_deg=270.0,
                        beat_times_sec=np.empty(0), envelope=np.empty(0), confidence=0.25,
                        snr_db=30.0, usable_ratio=0.9)  # fmt: skip
    window._on_telemetry_ready(mid)
    assert window.card_rate.value_label.text() == "+5.0" and window.card_rate.dimmed
    assert window.card_beat_error.value_label.text() == "---.-"
    assert window._rate_history == [5.0]
    high = BeatTelemetry(rate_spd=5.0, beat_error_ms=0.4, amplitude_deg=270.0,
                         beat_times_sec=np.empty(0), envelope=np.empty(0), confidence=0.6,
                         snr_db=30.0, usable_ratio=0.9, saturated_beats=2,
                         warnings=["2 batimientos saturados"])  # fmt: skip
    window._on_telemetry_ready(high)
    assert not window.card_rate.dimmed and window.card_amplitude.value_label.text() == "270°"
    assert not window.card_amplitude.dimmed
    assert "⚠" in window.lbl_snr_value.text()
    assert window.statusBar().currentMessage() == "2 batimientos saturados"
    # A tic/tac amplitude mismatch (overall confidence low, timing fine) greys out only the
    # amplitude.
    mismatch = BeatTelemetry(rate_spd=5.0, beat_error_ms=0.4, amplitude_deg=290.0,
                             beat_times_sec=np.empty(0), envelope=np.empty(0), confidence=0.08,
                             snr_db=30.0, usable_ratio=0.9, timing_confidence=0.5)  # fmt: skip
    window._on_telemetry_ready(mismatch)
    assert window.card_rate.value_label.text() == "+5.0" and not window.card_rate.dimmed
    assert window.card_amplitude.value_label.text() == "290°" and window.card_amplitude.dimmed
    window._set_capture_state("INACTIVO")


def test_clock_info_label(window) -> None:
    window._on_drift_updated(48000.4, 8.1, -0.7)
    window._on_clock_info(0.5, 3, "calibración guardada (nivel 3)")
    assert window.lbl_stat_drift_val.text() == "+8.10 ± 0.50 ppm (level 3)"
    assert "guardada" in window.lbl_stat_drift_val.toolTip()
    window._on_drift_updated(48000.0, 0.0, 0.0)
    window._on_clock_info(math.nan, 0, "sin calibrar")
    assert "uncalibrated" in window.lbl_stat_drift_val.text()


def test_manual_pan_pauses_autoscale(window) -> None:
    window._tape_ms_even[:] = [100.0]
    window._tape_ms_odd[:] = [101.0]
    window._on_paper_tape_manual_range()
    window._autoscale_paper_tape()
    lo, hi = window.paper_tape_plot.getViewBox().viewRange()[1]
    assert hi < 100.0  # not followed while paused
    window.center_paper_tape_trace()
    assert window._tape_autoscale
    lo, hi = window.paper_tape_plot.getViewBox().viewRange()[1]
    assert lo < 100.0 < hi
