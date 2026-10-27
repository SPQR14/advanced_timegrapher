"""Top-bar controls, quartz mode, manual clock calibration and About (headless)."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PyQt6")

from adv_tg import __version__  # noqa: E402
from adv_tg.core.adc_clock import AdcDrift  # noqa: E402
from adv_tg.core.models import WatchConfig  # noqa: E402
from adv_tg.mock.worker import MockStreamWorker  # noqa: E402
from adv_tg.ui import main_window as mw  # noqa: E402
from adv_tg.ui import measure_worker as mwk  # noqa: E402
from adv_tg.ui.dialogs import CalibrationDialog, LiveDrift  # noqa: E402
from adv_tg.ui.measure_worker import MeasureStreamWorker  # noqa: E402
from adv_tg.utils.adc_calibration import Calibration, CalibrationStore  # noqa: E402
from adv_tg.utils.audio_io import SYNTHETIC_DEVICE_NAME, AudioDeviceInfo  # noqa: E402
from tests.synth import synth_watch  # noqa: E402
from tests.test_measure_worker import Factory, Recorder  # noqa: E402

FS = 48_000


@pytest.fixture
def window(qtbot, monkeypatch: pytest.MonkeyPatch):
    devices = [
        AudioDeviceInfo(None, SYNTHETIC_DEVICE_NAME, 1, 48000.0, is_synthetic=True),
        AudioDeviceInfo(7, "Fake CM18", 1, 48000.0),
    ]

    def fake_create_worker(device_id, config=None, *, seed=None, parent=None):
        if device_id is None:
            worker = MockStreamWorker(config, seed=seed, parent=parent)
        else:
            x = np.zeros(FS, dtype=np.float32)
            worker = MeasureStreamWorker(config, capture_factory=Factory(x), parent=parent)
        worker.set_input_device(device_id)
        return worker

    monkeypatch.setattr(mw, "list_input_devices", lambda: devices)
    monkeypatch.setattr(mw, "create_worker", fake_create_worker)
    monkeypatch.setattr(mw, "measure_device_name", lambda device_id: "Fake CM18")
    win = mw.MainWin()
    qtbot.addWidget(win)
    yield win
    win.close()


def test_duration_and_stabilization_choices(window) -> None:
    durations = [window.cmb_duration.itemText(i) for i in range(window.cmb_duration.count())]
    assert durations == [
        "12 s",
        "24 s",
        "30 s",
        "45 s",
        "60 s",
        "120 s",
        "240 s",
        "5 min",
        "15 min",
        "30 min",
        "1 h",
    ]
    assert [window.cmb_stab.itemText(i) for i in range(window.cmb_stab.count())] == [
        "10 s",
        "15 s",
        "20 s",
    ]
    assert window.duration_s() == 60.0 and window.stabilization_s() == 10.0
    window.cmb_duration.setCurrentIndex(window.cmb_duration.count() - 1)
    assert window.duration_s() == 3600.0  # longest choice: 1 h
    window.set_session_timing(stabilization_s=15, duration_s=300)
    assert window.stabilization_s() == 15.0 and window.cmb_duration.currentText() == "5 min"
    n = window.cmb_stab.count()
    window.set_session_timing(stabilization_s=0)  # automation-only value is added
    assert window.stabilization_s() == 0.0 and window.cmb_stab.count() == n + 1
    assert mw._format_duration(90) == "90 s" and mw._format_duration(7200) == "2 h"


def test_quartz_mode_toggle(window) -> None:
    window.cmb_bph.setCurrentText("28800")
    window.chk_quartz.setChecked(True)
    assert [window.cmb_bph.itemText(i) for i in range(window.cmb_bph.count())] == [
        "1/s",
        "2/s",
        "4/s",
        "8/s",
        "16/s",
    ]
    assert not window.spn_lift.isEnabled() and not window.cmb_caliber.isEnabled()
    window.cmb_bph.setCurrentText("16/s")
    cfg = window.current_watch_config()
    assert cfg.quartz and cfg.bph == 57600
    assert window.lbl_bph.text() == window.translator.tr("quartz_steps")
    window.chk_quartz.setChecked(False)
    cfg = window.current_watch_config()
    assert not cfg.quartz and cfg.bph == 28800 and window.spn_lift.isEnabled()


def test_quartz_session_measures_rate_only(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(mwk, "device_name", lambda device_id: None)
    x, _ = synth_watch(bph=14400, rate_s_per_day=1.5, seconds=40, snr_db=30, seed=4, quartz=True)
    worker = MeasureStreamWorker(
        WatchConfig(bph=14400, sample_rate=FS, quartz=True), capture_factory=Factory(x, speed=8.0)
    )
    rec = Recorder(worker)
    with qtbot.waitSignal(worker.session_finished, timeout=20000):
        worker.start_stream()
    last = rec.telemetry[-1]
    assert last.rate_spd == pytest.approx(1.5, abs=0.5)
    assert math.isnan(last.beat_error_ms) and math.isnan(last.amplitude_deg)
    path = worker.take_recording()
    assert path is not None
    from adv_tg.utils.audio_files import read_tags

    tags = read_tags(path)
    assert tags["ADV_TG_QUARTZ"] == "1" and "ADV_TG_LIFT_ANGLE" not in tags
    path.unlink()
    worker.shutdown()


def test_calibration_dialog(qtbot, tmp_path) -> None:
    from adv_tg.i18n.translator import Translator

    store = CalibrationStore(tmp_path / "c.json")
    store.add(Calibration("Fake CM18", FS, AdcDrift.manual(8.3, 6.0), datetime.now(UTC)))
    dlg = CalibrationDialog(
        Translator("es"), "Fake CM18", FS, live=LiveDrift(9.46, 0.4, 2), store=store
    )
    qtbot.addWidget(dlg)
    assert dlg.table.rowCount() == 1 and "+8.300" in dlg.lbl_current.text()
    dlg.btn_live.click()
    assert dlg.spn_ppm.value() == pytest.approx(9.46) and dlg.spn_sigma.value() == 0.4
    assert "nivel 2" in dlg.txt_note.text()
    assert "-0.817" in dlg.lbl_bias.text()
    assert dlg.save()
    assert dlg.table.rowCount() == 2 and "+9.460" in dlg.lbl_current.text()
    rec = store.records("fake cm18", FS)
    assert rec[-1].drift.method == "manual" and rec[-1].drift.ppm == pytest.approx(9.46)
    assert dlg.forget(confirm=False) == 2
    assert dlg.table.rowCount() == 0 and dlg.lbl_current.text() == dlg._tr("cal_none")
    nolive = CalibrationDialog(Translator("en"), "Fake CM18", FS, store=store)
    qtbot.addWidget(nolive)
    assert not nolive.btn_live.isEnabled()


def test_calibration_button_and_about(window) -> None:
    window.btn_mock.setChecked(True)
    assert window.open_calibration_dialog(exec_dialog=False) is None  # MOCK: not available
    assert window.statusBar().currentMessage() == window.translator.tr("cal_unavailable")
    window.btn_mock.setChecked(False)
    window._on_drift_updated(48000.45, 9.46, -0.82)
    window._on_clock_info(0.4, 2, "en vivo (wall_ns, 60 s)")
    dlg = window.open_calibration_dialog(exec_dialog=False)
    assert dlg is not None and dlg.device == "Fake CM18" and dlg.sample_rate == 48000
    assert dlg.live is not None and dlg.live.ppm == pytest.approx(9.46)
    dlg.close()
    about = window.open_about_dialog(exec_dialog=False)
    assert __version__ in about.version_label.text()
    about.close()


def test_open_folders_actions(window, monkeypatch) -> None:
    from adv_tg.ui import dialogs as dialogs_mod
    from adv_tg.utils.adc_calibration import config_dir
    from adv_tg.utils.logger_setup import LoggerSetup

    opened_urls: list[str] = []

    def fake_open(url):
        opened_urls.append(url.toLocalFile())
        return True

    monkeypatch.setattr(dialogs_mod.QDesktopServices, "openUrl", fake_open)

    assert window.open_logs_folder() is True
    assert len(opened_urls) == 1
    assert Path(opened_urls[0]) == LoggerSetup.LOG_DIR.resolve()

    assert window.open_calibration_folder() is True
    assert len(opened_urls) == 2
    assert Path(opened_urls[1]) == config_dir().resolve()

    # The Tools menu triggers the same actions (the top bar has no folder buttons any more)
    window.action_open_logs.trigger()
    window.action_open_calibration.trigger()
    assert len(opened_urls) == 4
    assert not hasattr(window, "btn_open_logs")


def test_report_dialog_from_window(window, tmp_path) -> None:
    import zipfile

    window.btn_mock.setChecked(False)
    rec = tmp_path / "s.flac"
    rec.write_bytes(b"x" * 100)
    window.dsp_worker._recording = rec  # as if a session had finished
    dlg = window.open_report_dialog(exec_dialog=False)
    assert dlg.chk_recording.isEnabled() and dlg.chk_recording.isChecked()
    dlg.txt_description.setPlainText("prueba")
    out = dlg.create(tmp_path / "rep.zip", interactive=False)
    assert out is not None and dlg.created == out
    with zipfile.ZipFile(out) as z:
        assert "recording.flac" in z.namelist()
        text = z.read("report.txt").decode()
    assert "prueba" in text and "engine: MeasureStreamWorker" in text and "duration_s: 60.0" in text
    assert window.dsp_worker.recording_path == rec  # the worker still owns it
    window.btn_mock.setChecked(True)
    dlg = window.open_report_dialog(exec_dialog=False)
    assert not dlg.chk_recording.isEnabled()


def test_digital_silence_hint(window, monkeypatch) -> None:
    import time as _time

    window.btn_mock.setChecked(False)
    window._set_capture_state("MIDIENDO")
    clock = [100.0]
    monkeypatch.setattr(mw.time, "monotonic", lambda: clock[0])
    window._on_level_updated(-math.inf, -math.inf, False)
    clock[0] += 2.0
    window._on_level_updated(-math.inf, -math.inf, False)
    assert not window._silence_warned
    clock[0] += 1.5
    window._on_level_updated(-math.inf, -math.inf, False)
    assert window._silence_warned
    key = {"darwin": "no_signal_macos", "win32": "no_signal_windows"}.get(
        mw.sys.platform, "no_signal_other"
    )
    assert window.statusBar().currentMessage() == window.translator.tr(key)
    # Any real sound resets the timer; a MOCK session never warns.
    window._silence_warned = False
    window._on_level_updated(-40.0, -60.0, False)
    assert window._silence_since is None
    window._set_capture_state("INACTIVO")
    assert _time is not None


def test_recording_blocked_for_long_tests(window) -> None:
    assert [window.cmb_duration.itemData(i) for i in range(window.cmb_duration.count())][-1] == 3600
    window.set_session_timing(duration_s=240)
    assert window.recording_allowed() and window.btn_record.isEnabled()
    window.set_session_timing(duration_s=300)
    assert not window.recording_allowed() and not window.btn_record.isEnabled()
    assert "240" in window.btn_record.toolTip()
    assert window.record_audio() is None
    assert "240" in window.statusBar().currentMessage()
    window.btn_mock.setChecked(True)
    assert window.save_recorded_audio() is None  # never synthesizes a long MOCK train


def test_long_test_session_is_not_recorded(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(mwk, "device_name", lambda device_id: None)
    x = np.zeros(FS // 2, dtype=np.float32)
    worker = MeasureStreamWorker(WatchConfig(sample_rate=FS), capture_factory=Factory(x))
    worker.set_recording_enabled(False)
    with qtbot.waitSignal(worker.session_finished, timeout=10000):
        worker.start_stream()
    assert not worker.has_recording
    worker.set_recording_enabled(True)
    with qtbot.waitSignal(worker.session_finished, timeout=10000):
        worker.start_stream()
    assert worker.has_recording
    worker.shutdown()


def test_paper_tape_dots_with_bounded_redraw(window) -> None:
    for curve in (window.tape_curve_even, window.tape_curve_odd):
        assert curve.opts["autoDownsample"] and curve.opts["clipToView"]
        assert curve.opts["pen"] is None and curve.opts["symbol"] == "o"  # dots, no lines


def test_paper_tape_never_shows_negative_time(window) -> None:
    vb = window.paper_tape_plot.getViewBox()
    assert vb.state["limits"]["xLimits"][0] == 0.0
    vb.setXRange(-50.0, 10.0, padding=0.0)
    assert vb.viewRange()[0][0] >= 0.0


def _fill_tape(window, seconds: float, *, rate_spd: float = 4.0, jitter_ms: float = 0.0):
    """Raw tape rows like the engine's: 3 beats/s per row, slope = rate, optional scatter."""
    rng = np.random.default_rng(0)
    x = np.arange(0.0, seconds, 1.0 / 3.0)
    y = rate_spd * x / 86.4
    window._tape_x_even[:] = x.tolist()
    window._tape_ms_even[:] = (y + rng.normal(0.0, jitter_ms, x.size)).tolist()
    window._tape_x_odd[:] = (x + 1.0 / 6.0).tolist()
    window._tape_ms_odd[:] = (y + 0.2 + rng.normal(0.0, jitter_ms, x.size)).tolist()
    window._tape_disp_even.clear()
    window._tape_disp_odd.clear()
    window._smooth_tape_rows()
    window._tape_autoscale = True
    window._autoscale_paper_tape()


def test_paper_tape_window_starts_at_zero_then_scrolls(window) -> None:
    _fill_tape(window, 20.0)
    (x_lo, x_hi), (y_lo, y_hi) = window.paper_tape_plot.getViewBox().viewRange()
    assert x_lo == pytest.approx(0.0, abs=1e-6) and x_hi == pytest.approx(mw.TAPE_WINDOW_S)
    assert y_hi - y_lo >= 2 * mw.TAPE_ZOOM_HALF_SPANS_MS[0] - 1e-6  # flat row reads flat
    _fill_tape(window, 300.0)
    (x_lo, x_hi), _ = window.paper_tape_plot.getViewBox().viewRange()
    newest = max(window._tape_x_even[-1], window._tape_x_odd[-1])
    assert x_hi == pytest.approx(newest + mw.TAPE_LEAD_S)
    assert x_hi - x_lo == pytest.approx(mw.TAPE_WINDOW_S)
    # COSC guides start at the left edge of the view
    cx, _ = window.cosc_upper_line.getData()
    assert cx[0] == pytest.approx(x_lo) and cx[-1] == pytest.approx(x_hi)


def test_paper_tape_rows_are_smoothed_but_export_stays_raw(window) -> None:
    _fill_tape(window, 60.0, jitter_ms=0.5)
    raw = np.array(window._tape_ms_even)
    disp = np.array(window._tape_disp_even)
    assert disp.size == raw.size
    trend = 4.0 * np.array(window._tape_x_even) / 86.4
    assert np.std(disp - trend) < 0.6 * np.std(raw - trend)  # median of 7 beats
    # The drawn curve uses the smoothed rows; the CSV export keeps one raw value per beat.
    window.tape_curve_even.setData(window._tape_x_even, window._tape_disp_even)
    assert np.allclose(window.tape_curve_even.yData, disp)


def test_paper_tape_zoom_levels(window) -> None:
    _fill_tape(window, 30.0)
    spans = []
    for _ in mw.TAPE_ZOOM_HALF_SPANS_MS:
        lo, hi = window.paper_tape_plot.getViewBox().viewRange()[1]
        spans.append(hi - lo)
        assert window.btn_tape_zoom.text().startswith("Zoom")
        window.cycle_paper_tape_zoom()
    assert spans == pytest.approx([2 * h for h in mw.TAPE_ZOOM_HALF_SPANS_MS])
    assert window._tape_zoom_index == 0  # back to 1x


def test_mock_tape_scatter_does_not_grow_with_time(window, monkeypatch) -> None:
    """MOCK rows integrate the simulated rate; rate * t would scale its noise with t."""
    monkeypatch.setattr(window, "dsp_worker", None)  # the MOCK branch of _append_tape
    rng = np.random.default_rng(3)
    window._mock_tape_last = None
    for name in ("_tape_x_even", "_tape_ms_even", "_tape_x_odd", "_tape_ms_odd"):
        getattr(window, name).clear()
    rate, dt = 4.5, 0.25
    for i in range(1, 241):  # 60 s at 4 updates/s
        tel = mw.TelemetryResult(
            rate_spd=rate + rng.normal(0.0, 0.35),
            beat_error_ms=0.35,
            amplitude_deg=283.0,
            confidence=0.97,
        )
        window._append_tape(tel, i * dt)
    t = np.array(window._tape_x_even)
    resid = np.array(window._tape_ms_even) - rate * t / 86.4
    step = np.diff(resid)
    early, late = np.std(step[: step.size // 4]), np.std(step[-step.size // 4 :])
    assert late == pytest.approx(early, rel=0.5)  # constant beat-to-beat scatter
    assert np.ptp(np.array(window._tape_ms_odd) - np.array(window._tape_ms_even)) < 1e-9


def test_save_position_never_invents_a_reading(window) -> None:
    window._saved_positions.clear()
    window._last_telemetry = None
    window._update_save_position_enabled()
    assert not window.btn_save_position.isEnabled()
    window.save_current_position()  # e.g. a keyboard shortcut while disabled
    assert window._saved_positions == {}
    assert "reading" in window.statusBar().currentMessage()

    reading = mw.TelemetryResult(
        rate_spd=3.2, beat_error_ms=0.4, amplitude_deg=265.0, confidence=0.9
    )
    window._last_telemetry = reading
    window._update_save_position_enabled()
    assert window.btn_save_position.isEnabled()
    window.save_current_position()
    assert window._saved_positions[window.cmb_position.currentText()] is reading


def test_new_session_forgets_the_previous_reading(window) -> None:
    window._last_telemetry = mw.TelemetryResult(
        rate_spd=3.2, beat_error_ms=0.4, amplitude_deg=265.0, confidence=0.9
    )
    window.start_capture()
    try:
        assert window._last_telemetry is None
        assert not window.btn_save_position.isEnabled()
    finally:
        window.stop_capture()
