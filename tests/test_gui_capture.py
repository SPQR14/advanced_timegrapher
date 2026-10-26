"""GUI flow with the hardware engine fed by an injected source (headless, no audio hardware)."""

from __future__ import annotations

import re

import pytest

pytest.importorskip("PyQt6")

from adv_tg.mock.worker import MockStreamWorker  # noqa: E402
from adv_tg.ui import main_window as mw  # noqa: E402
from adv_tg.ui.capture_worker import CaptureStreamWorker  # noqa: E402
from adv_tg.utils.audio_io import SYNTHETIC_DEVICE_NAME, AudioDeviceInfo  # noqa: E402
from tests.test_capture_worker import Factory, tone_and_ticks  # noqa: E402


@pytest.fixture
def window(qtbot, monkeypatch: pytest.MonkeyPatch):
    devices = [
        AudioDeviceInfo(None, SYNTHETIC_DEVICE_NAME, 1, 48000.0, is_synthetic=True),
        AudioDeviceInfo(7, "Fake CM18", 1, 48000.0),
        AudioDeviceInfo(8, "Fake mic 2", 1, 48000.0),
    ]
    signal = tone_and_ticks(20.0)
    created: list[CaptureStreamWorker] = []

    def fake_create_worker(device_id, config=None, *, seed=None, parent=None):
        if device_id is None:
            worker = MockStreamWorker(config, seed=seed, parent=parent)
        else:
            worker = CaptureStreamWorker(config, capture_factory=Factory(signal), parent=parent)
            created.append(worker)
        worker.set_input_device(device_id)
        return worker

    monkeypatch.setattr(mw, "list_input_devices", lambda: devices)
    monkeypatch.setattr(mw, "create_worker", fake_create_worker)
    win = mw.MainWin()
    qtbot.addWidget(win)
    win.show()
    win.set_session_timing(stabilization_s=0)
    win.created_workers = created
    yield win
    win.close()


def _capture_pct(win) -> float:
    m = re.search(r"([\d.]+)\s*%", win.lbl_capture_health.text())
    assert m, win.lbl_capture_health.text()
    return float(m.group(1))


def test_hardware_capture_flow(window, qtbot) -> None:
    window.cmb_device.setCurrentIndex(1)
    assert isinstance(window.dsp_worker, CaptureStreamWorker)
    assert not window.is_mock_active()

    window.start_capture()
    assert window.dsp_worker.isRunning()
    assert window.prg_snr.value() == 0
    qtbot.wait(1500)
    assert window.prg_snr.value() > 0
    assert "dBFS" in window.lbl_snr_value.text()
    assert _capture_pct(window) >= 99.0
    assert window.lbl_session_status.text() == window.translator.tr("capturing_no_measure")
    assert window.card_rate.value_label.text() == "---.-"
    x, _ = window.audio_waveform_curve.getData()
    assert x is not None and len(x) > 0

    assert window.record_audio() is None  # armed: a capture-only engine has nothing to save

    window.stop_capture()
    assert window._capture_state == "INACTIVO"
    assert not window.dsp_worker.isRunning()

    # Restart several times
    for _ in range(3):
        window.start_capture()
        qtbot.wait(300)
        assert window.dsp_worker.isRunning()
        window.stop_capture()
        assert not window.dsp_worker.isRunning()


def test_device_change_and_close_during_capture(window, qtbot) -> None:
    window.cmb_device.setCurrentIndex(1)
    window.start_capture()
    qtbot.wait(300)
    first = window.dsp_worker

    window.cmb_device.setCurrentIndex(2)  # hardware -> hardware
    assert window._capture_state == "INACTIVO" and not first.isRunning()

    window.start_capture()
    qtbot.wait(300)
    window.btn_mock.setChecked(True)  # hardware -> MOCK replaces the engine
    assert isinstance(window.dsp_worker, MockStreamWorker)
    assert not first.isRunning() and first._thread is None

    window.btn_mock.setChecked(False)
    window.start_capture()
    qtbot.wait(300)
    hw = window.dsp_worker
    assert isinstance(hw, CaptureStreamWorker) and hw.isRunning()
    window.close()
    assert not hw.isRunning() and hw._thread is None


def test_mock_flow_still_works(window, qtbot) -> None:
    window.btn_mock.setChecked(True)
    assert window.is_mock_active()
    window.start_capture()
    qtbot.wait(1000)
    assert window.card_rate.value_label.text() != "---.-"
    assert window._capture_state == "MIDIENDO"
    assert window.lbl_session_status.text() == window.translator.tr("status_running")
    assert window.prg_snr.value() > 0
    window.stop_capture()
    assert not window.dsp_worker.isRunning()


def test_close_window_during_isolated_capture_leaves_no_child(
    window, qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    import multiprocessing as mp

    from adv_tg.audio.process_capture import ProcessAudioCapture

    def isolated_factory(stream, **kw):
        return ProcessAudioCapture(stream, fake_source="tone", open_timeout_s=30.0, **kw)

    def create_isolated(device_id, config=None, *, seed=None, parent=None):
        if device_id is None:
            worker = MockStreamWorker(config, seed=seed, parent=parent)
        else:
            worker = CaptureStreamWorker(config, capture_factory=isolated_factory, parent=parent)
        worker.set_input_device(device_id)
        return worker

    window.cmb_device.setCurrentIndex(0)  # the window starts on the hardware device
    monkeypatch.setattr(mw, "create_worker", create_isolated)
    window.cmb_device.setCurrentIndex(1)
    window.start_capture()
    hw = window.dsp_worker
    qtbot.waitUntil(lambda: window.prg_snr.value() > 0, timeout=30_000)
    assert len(mp.active_children()) == 1
    window.close()
    assert not hw.isRunning()
    qtbot.waitUntil(lambda: mp.active_children() == [], timeout=5000)


def test_starts_on_the_default_input_not_mock(window, monkeypatch) -> None:
    # The fixture's devices: synthetic, Fake CM18 (7), Fake mic 2 (8)
    assert window.cmb_device.currentIndex() == 1 and not window.is_mock_active()
    assert not window.btn_mock.isChecked()
    monkeypatch.setattr(mw, "default_input_device_id", lambda: 8)
    window.cmb_device.clear()
    window._populate_audio_devices()
    assert window.selected_device_id() == 8
