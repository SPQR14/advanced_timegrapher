"""GUI smoke test: build the main window, run the MOCK for ~1 s and close it."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from adv_tg.ui.main_window import MainWin  # noqa: E402


def test_app_icon_renders(qtbot) -> None:
    """The SVG icon must load through Qt's SVG engine and produce non-empty pixels."""
    from adv_tg.ui.main_window import APP_ICON_PATH, app_icon

    assert APP_ICON_PATH.is_file()
    icon = app_icon()
    assert not icon.isNull()
    img = icon.pixmap(64, 64).toImage()
    assert img.width() == 64
    # Transparent corner, opaque centre.
    assert img.pixelColor(0, 0).alpha() == 0
    assert img.pixelColor(32, 32).alpha() == 255


def test_main_window_with_mock(qtbot) -> None:
    window = MainWin()
    qtbot.addWidget(window)
    window.show()

    window.btn_mock.setChecked(True)
    assert window.is_mock_active()
    assert window.cmb_device.currentIndex() == 0
    assert window.lbl_mock_badge.isVisibleTo(window)

    window.spn_stab.setValue(0)
    window.start_capture()
    qtbot.wait(1000)
    assert window.isVisible()
    assert window.card_rate.value_label.text() != "---.-"
    assert window._capture_state == "MIDIENDO"

    window.close()
    assert window._capture_state == "INACTIVO"
    assert not window.dsp_worker.isRunning()


def test_late_telemetry_after_stop_is_ignored(qtbot) -> None:
    from adv_tg.core.models import TelemetryResult

    window = MainWin()
    qtbot.addWidget(window)
    window.btn_mock.setChecked(True)  # the default is the system's input device if any
    window.spn_stab.setValue(0)
    window.start_capture()
    qtbot.wait(600)
    window.stop_capture()
    n_tape = len(window._tape_times_even)
    n_hist = len(window._rate_history)
    assert n_tape > 0

    # Telemetry that was still queued when the session stopped
    window._on_telemetry_ready(TelemetryResult(9.9, 0.5, 250.0, 0.9))
    qtbot.wait(300)  # also drain anything the worker had queued
    assert len(window._tape_times_even) == n_tape
    assert len(window._rate_history) == n_hist
    window.close()


def test_buy_coffee_button_opens_url(qtbot, monkeypatch) -> None:
    from PyQt6.QtCore import Qt

    from adv_tg.ui import main_window as mw

    opened: list[str] = []
    result = {"ok": True}

    def fake_open(url) -> bool:
        opened.append(url.toString())
        return result["ok"]

    monkeypatch.setattr(mw.QDesktopServices, "openUrl", fake_open)
    window = MainWin()
    qtbot.addWidget(window)
    qtbot.mouseClick(window.btn_buy_coffee, Qt.MouseButton.LeftButton)
    assert opened == ["https://buymeacoffee.com/sqpr14"]
    assert "buymeacoffee.com/sqpr14" in window.btn_buy_coffee.toolTip()
    result["ok"] = False
    assert window._on_buy_coffee_clicked() is False
    assert "buymeacoffee.com/sqpr14" in window.statusBar().currentMessage()
