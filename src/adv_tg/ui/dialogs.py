"""Dialogs of the main window: ADC clock calibration, "About" and the report bundle.

``CalibrationDialog`` edits the calibrations stored per device (``adv_tg.utils.adc_calibration``,
the same file as ``python -m adv_tg.tools.adc_calibration``): it shows the one that applies
now and the history, and deletes the device's calibrations. A new calibration applies from the
next session. Three tabs give the value:

* GPS PPS: listens live to a GPS PPS made audible by a buzzer on the same microphone
  (``PpsCalibrationWorker`` + ``adv_tg.core.pps``) or analyses a recording of it, and saves
  the estimate (level 3, method ``pps``).
* Manual: a value typed in or the live estimate of the last session (level 3, ``manual``).
* Reference watch: turns the rate of a watch of known error (a quartz checked against a time
  signal) into ppm: ``ppm = (known rate - uncorrected measured rate) / 0.0864``.

``ReportDialog`` builds the ``.zip`` of ``adv_tg.utils.report`` (logs, system, settings,
calibrations and optionally the last recording) and opens the GitHub issue chooser.
"""

from __future__ import annotations

import html
import math
import platform
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pyqtgraph as pg
from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, Qt, QUrl
from PyQt6.QtGui import QCloseEvent, QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from adv_tg import __version__
from adv_tg.core.adc_clock import PPM_TO_S_PER_DAY, AdcDrift
from adv_tg.core.pps import READY_SIGMA_PPM, READY_SPAN_S, PpsEstimate, calibrate_samples
from adv_tg.i18n.translator import Translator
from adv_tg.ui.pps_worker import PpsCalibrationWorker
from adv_tg.utils.adc_calibration import (
    Calibration,
    CalibrationStore,
    CalibrationStoreError,
    config_dir,
    pick_best,
)
from adv_tg.utils.audio_files import read_mono
from adv_tg.utils.debugger import Debugger
from adv_tg.utils.logger_setup import LoggerSetup
from adv_tg.utils.report import ISSUES_URL, build_report, default_report_name

__all__ = [
    "REPO_URL",
    "AboutDialog",
    "CalibrationDialog",
    "LiveDrift",
    "ReportDialog",
    "open_folder",
]

_dbg = Debugger.get(__name__)
logger = _dbg.logger

REPO_URL = "https://github.com/SPQR14/advanced_timegrapher"
#: Colour of the PPS residual points.
PPS_COLOR = "#4FC3F7"


def open_folder(path: Path) -> bool:
    """Open a folder in the system's file manager (created if missing). True on success."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("No se pudo crear la carpeta %s: %s", path, exc)
    ok = bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve()))))
    if ok:
        logger.info("Carpeta abierta: %s", path)
    else:
        logger.warning("No se pudo abrir la carpeta: %s", path)
    return ok


class LiveDrift:
    """Live clock estimate of the last session (``ppm``, ``sigma_ppm``, confidence ``level``)."""

    def __init__(self, ppm: float, sigma_ppm: float, level: int) -> None:
        self.ppm = float(ppm)
        self.sigma_ppm = float(sigma_ppm)
        self.level = int(level)


class CalibrationDialog(QDialog):
    """Calibration of the ADC clock of one device (see module docstring).

    Three ways to get the value, one tab each: a GPS PPS heard live (or a recording of it),
    a manual value (or the live estimate of the last session) and a reference watch of known
    rate. The calibration that applies now and the history are shown below the tabs.

    Args:
        translator: UI texts.
        device: Device name (as PortAudio reports it).
        sample_rate: Sample rate the calibration applies to.
        live: Live estimate of the last session (level >= 2), or None.
        store: Calibration store (default: the user's).
        parent: Qt parent.
        measured_rate_spd: Rate of the last session WITHOUT any clock correction (s/d), used
            to prefill the reference-watch box; None if there is no reading.
        device_id: PortAudio index of the device, for the live PPS capture (None = not
            available).
        capture_busy: A measurement session holds the device: the live PPS cannot start.
        pps_worker_factory: ``(device_id, sample_rate) -> PpsCalibrationWorker`` (tests).
    """

    PPS_TAB, MANUAL_TAB, REFERENCE_TAB = range(3)

    def __init__(
        self,
        translator: Translator,
        device: str,
        sample_rate: int,
        live: LiveDrift | None = None,
        store: CalibrationStore | None = None,
        parent: QWidget | None = None,
        measured_rate_spd: float | None = None,
        device_id: int | None = None,
        capture_busy: bool = False,
        pps_worker_factory: Callable[[int | None, int], PpsCalibrationWorker] | None = None,
    ) -> None:
        super().__init__(parent)
        self._tr = translator.tr
        self.device = device
        self.sample_rate = int(sample_rate)
        self.live = live
        self.store = store or CalibrationStore()
        self.device_id = device_id
        self.capture_busy = capture_busy
        self._pps_factory = pps_worker_factory or (
            lambda dev, fs: PpsCalibrationWorker(dev, fs, parent=self)
        )
        self.pps_worker: PpsCalibrationWorker | None = None
        self.pps_estimate: PpsEstimate | None = None
        self.pps_source = ""  # "" = live, else the file name
        self._pps_listening = False
        tr = self._tr
        self.setWindowTitle(tr("cal_title"))
        self.setMinimumWidth(680)

        layout = QVBoxLayout(self)
        head = QLabel(tr("cal_device").format(device=device, fs=self.sample_rate))
        head.setObjectName("SectionHeader")
        layout.addWidget(head)
        self.lbl_current = QLabel()
        self.lbl_current.setWordWrap(True)
        layout.addWidget(self.lbl_current)

        help_lbl = QLabel(tr("cal_help"))
        help_lbl.setWordWrap(True)
        help_lbl.setObjectName("HelpText")
        layout.addWidget(help_lbl)

        self.tabs = QTabWidget()  # order: PPS_TAB, MANUAL_TAB, REFERENCE_TAB
        self.tabs.addTab(self._build_pps_tab(), tr("cal_tab_pps"))
        self.tabs.addTab(self._build_manual_tab(), tr("cal_tab_manual"))
        self.tabs.addTab(self._build_reference_tab(measured_rate_spd), tr("cal_tab_ref"))
        layout.addWidget(self.tabs)

        hist = QLabel(tr("cal_history"))
        hist.setObjectName("SectionHeader")
        layout.addWidget(hist)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            [tr("cal_col_date"), "ppm", "± ppm", tr("cal_col_level"), tr("cal_col_note")]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setMinimumHeight(110)
        layout.addWidget(self.table)

        bottom = QHBoxLayout()
        self.btn_forget = QPushButton(tr("cal_forget"))
        self.btn_forget.clicked.connect(self.forget)
        bottom.addWidget(self.btn_forget)
        bottom.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        bottom.addWidget(buttons)
        layout.addLayout(bottom)

        self._refresh_bias()
        self._refresh_reference()
        self._show_pps(None)
        self.refresh()

    # ------------------------------------------------------------------ tabs
    def _build_pps_tab(self) -> QWidget:
        tr = self._tr
        page = QWidget()
        lay = QVBoxLayout(page)
        help_lbl = QLabel(tr("cal_pps_help").format(
            min_s=int(READY_SPAN_S), sigma=READY_SIGMA_PPM, fs=self.sample_rate
        ))  # fmt: skip
        help_lbl.setWordWrap(True)
        help_lbl.setObjectName("HelpText")
        lay.addWidget(help_lbl)

        self.lbl_pps_status = QLabel()
        self.lbl_pps_status.setWordWrap(True)
        lay.addWidget(self.lbl_pps_status)
        form = QFormLayout()
        self.lbl_pps_ppm = QLabel()
        self.lbl_pps_pulses = QLabel()
        self.lbl_pps_jitter = QLabel()
        self.lbl_pps_level = QLabel("—")
        form.addRow(tr("cal_pps_result"), self.lbl_pps_ppm)
        form.addRow(tr("cal_pps_pulses"), self.lbl_pps_pulses)
        form.addRow(tr("cal_pps_jitter"), self.lbl_pps_jitter)
        form.addRow(tr("cal_pps_level"), self.lbl_pps_level)
        lay.addLayout(form)

        self.plot_pps = pg.PlotWidget()
        self.plot_pps.setMinimumHeight(140)
        self.plot_pps.setLabel("left", tr("cal_pps_plot_y"))
        self.plot_pps.setLabel("bottom", tr("cal_pps_plot_x"))
        self.plot_pps.showGrid(x=True, y=True, alpha=0.3)
        self.plot_pps.setMouseEnabled(x=False, y=False)
        self._pps_curve = self.plot_pps.plot(
            [], [], pen=None, symbol="o", symbolSize=4, symbolBrush=PPS_COLOR, symbolPen=None
        )
        lay.addWidget(self.plot_pps)

        row = QHBoxLayout()
        self.btn_pps_start = QPushButton(tr("cal_pps_start"))
        self.btn_pps_start.setCheckable(True)
        self.btn_pps_start.toggled.connect(self._toggle_pps)
        if self.device_id is None:
            self.btn_pps_start.setEnabled(False)
            self.btn_pps_start.setToolTip(tr("cal_pps_no_device"))
        elif self.capture_busy:
            self.btn_pps_start.setEnabled(False)
            self.btn_pps_start.setToolTip(tr("cal_pps_busy"))
        else:
            self.btn_pps_start.setToolTip(tr("cal_pps_start_tooltip"))
        self.btn_pps_file = QPushButton(tr("cal_pps_file"))
        self.btn_pps_file.setToolTip(tr("cal_pps_file_tooltip"))
        self.btn_pps_file.clicked.connect(lambda: self.analyze_pps_file())
        self.btn_pps_save = QPushButton(tr("cal_pps_save"))
        self.btn_pps_save.setToolTip(tr("cal_pps_save_tooltip"))
        self.btn_pps_save.clicked.connect(lambda: self.save_pps())
        row.addWidget(self.btn_pps_start)
        row.addWidget(self.btn_pps_file)
        row.addStretch()
        row.addWidget(self.btn_pps_save)
        lay.addLayout(row)
        return page

    def _build_manual_tab(self) -> QWidget:
        tr = self._tr
        page = QWidget()
        lay = QVBoxLayout(page)
        form = QFormLayout()
        self.spn_ppm = QDoubleSpinBox()
        self.spn_ppm.setRange(-500.0, 500.0)
        self.spn_ppm.setDecimals(3)
        self.spn_ppm.setSingleStep(0.1)
        self.spn_ppm.setSuffix(" ppm")
        self.spn_ppm.valueChanged.connect(self._refresh_bias)
        self.spn_sigma = QDoubleSpinBox()
        self.spn_sigma.setRange(0.001, 100.0)
        self.spn_sigma.setDecimals(3)
        self.spn_sigma.setSingleStep(0.1)
        self.spn_sigma.setValue(1.0)
        self.spn_sigma.setSuffix(" ppm")
        self.txt_note = QLineEdit()
        self.txt_note.setPlaceholderText(tr("cal_note_placeholder"))
        self.lbl_bias = QLabel()
        form.addRow(tr("cal_ppm"), self.spn_ppm)
        form.addRow(tr("cal_sigma"), self.spn_sigma)
        form.addRow(tr("cal_note"), self.txt_note)
        form.addRow("", self.lbl_bias)
        lay.addLayout(form)
        row = QHBoxLayout()
        self.btn_live = QPushButton(tr("cal_use_live"))
        self.btn_live.setEnabled(self.live is not None)
        if self.live is not None:
            self.btn_live.setToolTip(
                tr("cal_live_tooltip").format(
                    ppm=self.live.ppm, sigma=self.live.sigma_ppm, level=self.live.level
                )
            )
        self.btn_live.clicked.connect(self._use_live)
        self.btn_save = QPushButton(tr("cal_save"))
        self.btn_save.clicked.connect(self.save)
        row.addWidget(self.btn_live)
        row.addStretch()
        row.addWidget(self.btn_save)
        lay.addLayout(row)
        lay.addStretch()
        return page

    def _build_reference_tab(self, measured_rate_spd: float | None) -> QWidget:
        """Reference watch of known rate -> ppm (copied to the manual tab)."""
        tr = self._tr
        self.grp_ref = QWidget()
        ref_layout = QVBoxLayout(self.grp_ref)
        title = QLabel(tr("cal_ref_title"))
        title.setObjectName("SectionHeader")
        ref_layout.addWidget(title)
        ref_help = QLabel(tr("cal_ref_help"))
        ref_help.setWordWrap(True)
        ref_help.setObjectName("HelpText")
        ref_layout.addWidget(ref_help)
        ref_form = QFormLayout()
        self.spn_ref_measured = self._rate_spin()
        if measured_rate_spd is not None and math.isfinite(measured_rate_spd):
            self.spn_ref_measured.setValue(float(measured_rate_spd))
        self.spn_ref_known = self._rate_spin()
        ref_form.addRow(tr("cal_ref_measured"), self.spn_ref_measured)
        ref_form.addRow(tr("cal_ref_known"), self.spn_ref_known)
        ref_layout.addLayout(ref_form)
        ref_row = QHBoxLayout()
        self.lbl_ref_result = QLabel()
        self.btn_ref_apply = QPushButton(tr("cal_ref_apply"))
        self.btn_ref_apply.setToolTip(tr("cal_ref_apply_tooltip"))
        self.btn_ref_apply.clicked.connect(self._use_reference)
        ref_row.addWidget(self.lbl_ref_result)
        ref_row.addStretch()
        ref_row.addWidget(self.btn_ref_apply)
        ref_layout.addLayout(ref_row)
        ref_layout.addStretch()
        self.spn_ref_measured.valueChanged.connect(self._refresh_reference)
        self.spn_ref_known.valueChanged.connect(self._refresh_reference)
        return self.grp_ref

    # ------------------------------------------------------------------ GPS PPS
    def _toggle_pps(self, on: bool) -> None:
        if on:
            self.start_pps()
        else:
            self.stop_pps()

    def start_pps(self) -> bool:
        """Start listening to the PPS on the device (live calibration)."""
        if self.device_id is None or self.capture_busy:
            return False
        if self.pps_worker is not None and self.pps_worker.isRunning():
            return True
        worker = self._pps_factory(self.device_id, self.sample_rate)
        worker.pps_updated.connect(self._on_pps)
        worker.level_updated.connect(self._on_pps_level)
        worker.error_occurred.connect(self._on_pps_error)
        worker.session_finished.connect(self._on_pps_finished)
        self.pps_worker = worker
        self.pps_estimate = None
        self.pps_source = ""
        self._pps_listening = True
        self._show_pps(None)
        self.btn_pps_start.blockSignals(True)
        self.btn_pps_start.setChecked(True)
        self.btn_pps_start.blockSignals(False)
        self.btn_pps_start.setText(self._tr("cal_pps_stop"))
        self.btn_pps_file.setEnabled(False)
        _dbg.state_change("calibración PPS", "PARADA", "ESCUCHANDO", device=self.device)
        worker.start_stream()
        return True

    def stop_pps(self) -> None:
        """Stop listening (the last estimate stays on screen and can be saved)."""
        worker = self.pps_worker
        if worker is not None and worker.isRunning():
            worker.stop_stream()
        self._on_pps_finished()

    def _on_pps_finished(self) -> None:
        self._pps_listening = False
        self.btn_pps_start.blockSignals(True)
        self.btn_pps_start.setChecked(False)
        self.btn_pps_start.blockSignals(False)
        self.btn_pps_start.setText(self._tr("cal_pps_start"))
        self.btn_pps_file.setEnabled(True)
        self._show_pps(self.pps_estimate)

    def _on_pps(self, estimate: PpsEstimate) -> None:
        self.pps_estimate = estimate
        self._show_pps(estimate)

    def _on_pps_level(self, peak_dbfs: float, _rms_dbfs: float, saturated: bool) -> None:
        text = "—" if not math.isfinite(peak_dbfs) else f"{peak_dbfs:.0f} dBFS"
        if saturated:
            text += " · " + self._tr("cal_pps_clipping_ok")
        self.lbl_pps_level.setText(text)

    def _on_pps_error(self, message: str) -> None:
        logger.error("Calibración PPS: %s", message)
        self.lbl_pps_status.setText(self._tr("cal_pps_error").format(error=message))

    def _show_pps(self, est: PpsEstimate | None) -> None:
        """Refresh the GPS PPS tab from ``est`` (None = nothing yet)."""
        tr = self._tr
        listening = self._pps_listening
        if est is None:
            self.lbl_pps_status.setText(tr("cal_pps_waiting" if listening else "cal_pps_idle"))
            self.lbl_pps_ppm.setText("—")
            self.lbl_pps_pulses.setText("—")
            self.lbl_pps_jitter.setText("—")
            self._pps_curve.setData([], [])
            self.btn_pps_save.setEnabled(False)
            return
        if est.valid:
            d = est.drift
            self.lbl_pps_ppm.setText(
                tr("cal_pps_ppm").format(
                    ppm=d.ppm, sigma=d.uncertainty_ppm, bias=d.rate_bias_s_per_day
                )
            )
            self.lbl_pps_jitter.setText(f"{est.jitter_us:.1f} µs")
        else:
            self.lbl_pps_ppm.setText("—")
            self.lbl_pps_jitter.setText("—")
        self.lbl_pps_pulses.setText(
            tr("cal_pps_counts").format(
                span=est.span_s, used=est.used, missing=est.missing, jumps=est.jumps,
                rejected=est.rejected,
            )
        )  # fmt: skip
        if est.used == 0 and listening:
            status = tr("cal_pps_waiting")
        elif not est.valid:
            status = tr("cal_pps_collecting").format(n=est.used)
        elif est.ready:
            status = tr("cal_pps_ready")
        else:
            status = tr("cal_pps_converging").format(min_s=int(READY_SPAN_S))
        if est.jumps:
            status += " " + tr("cal_pps_jumped").format(n=est.jumps)
        if self.pps_source:
            status = f"{self.pps_source}: {status}"
        self.lbl_pps_status.setText(status)
        self._pps_curve.setData(est.t_s, est.residual_us)
        self.btn_pps_save.setEnabled(est.valid)

    def analyze_pps_file(self, path: Path | str | None = None) -> PpsEstimate | None:
        """Calibrate from a recording of the PPS made with this device at this sample rate."""
        tr = self._tr
        if path is None:
            chosen, _ = QFileDialog.getOpenFileName(
                self, tr("cal_pps_file"), str(Path.home()), tr("cal_pps_file_filter")
            )
            if not chosen:
                return None
            path = chosen
        path = Path(path)
        try:
            fs, x = read_mono(path)
        except Exception as exc:  # unreadable / unsupported file
            self._error(exc)
            return None
        if int(fs) != self.sample_rate:
            self._error(ValueError(tr("cal_pps_file_rate").format(file_fs=fs, fs=self.sample_rate)))
            return None
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            est = calibrate_samples(x, fs)
        finally:
            QApplication.restoreOverrideCursor()
        self.pps_estimate = est
        self.pps_source = path.name
        _dbg.file_read(path, pulses=est.pulses, ppm=round(est.drift.ppm, 3))
        self._show_pps(est)
        return est

    def save_pps(self, confirm: bool = True) -> bool:
        """Store the PPS estimate on screen (level 3, method ``pps``)."""
        est = self.pps_estimate
        if est is None or not est.valid:
            return False
        tr = self._tr
        if confirm and not est.ready:
            answer = QMessageBox.question(
                self,
                tr("cal_title"),
                tr("cal_pps_not_ready").format(
                    span=est.span_s, sigma=est.drift.uncertainty_ppm, min_s=int(READY_SPAN_S)
                ),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        note = tr("cal_pps_note").format(n=est.used, span=est.span_s, jitter=est.jitter_us)
        if self.pps_source:
            note += f" ({self.pps_source})"
        drift = replace(est.drift, note=note)
        try:
            self.store.add(Calibration(self.device, self.sample_rate, drift, datetime.now(UTC)))
        except (ValueError, CalibrationStoreError) as exc:
            self._error(exc)
            return False
        _dbg.state_change(
            "calibración PPS", "-", f"{drift.ppm:+.3f} ± {drift.uncertainty_ppm:.3f} ppm",
            device=self.device, fs=self.sample_rate, pulses=est.used,
        )  # fmt: skip
        self.refresh()
        return True

    def done(self, result: int) -> None:  # noqa: D102 - stop the capture with the window
        self.stop_pps()
        super().done(result)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.stop_pps()
        super().closeEvent(event)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _rate_spin() -> QDoubleSpinBox:
        spn = QDoubleSpinBox()
        spn.setRange(-999.0, 999.0)
        spn.setDecimals(3)
        spn.setSingleStep(0.1)
        spn.setSuffix(" s/d")
        return spn

    def reference_ppm(self) -> float:
        """Card clock error implied by the reference watch box.

        A card fast by ``p`` ppm makes a watch read ``0.0864 p`` s/d slow, so
        ``p = (known - measured) / 0.0864``.
        """
        return (self.spn_ref_known.value() - self.spn_ref_measured.value()) / PPM_TO_S_PER_DAY

    def _refresh_reference(self, *_args: object) -> None:
        self.lbl_ref_result.setText(self._tr("cal_ref_result").format(ppm=self.reference_ppm()))

    def _use_reference(self) -> None:
        """Copy the reference-watch result to the form (saved with "Save")."""
        ppm = self.reference_ppm()
        self.spn_ppm.setValue(ppm)
        self.tabs.setCurrentIndex(self.MANUAL_TAB)
        self.txt_note.setText(
            self._tr("cal_ref_note").format(
                known=self.spn_ref_known.value(), measured=self.spn_ref_measured.value()
            )
        )

    def _refresh_bias(self, *_args: object) -> None:
        bias = -PPM_TO_S_PER_DAY * self.spn_ppm.value()
        self.lbl_bias.setText(self._tr("cal_bias").format(bias=bias))

    def _use_live(self) -> None:
        if self.live is None:
            return
        self.spn_ppm.setValue(self.live.ppm)
        self.spn_sigma.setValue(max(self.live.sigma_ppm, self.spn_sigma.minimum()))
        if not self.txt_note.text().strip():
            self.txt_note.setText(self._tr("cal_live_note").format(level=self.live.level))

    def _error(self, exc: Exception) -> None:
        logger.error("Calibración: %s", exc)
        QMessageBox.warning(self, self._tr("cal_title"), str(exc))

    def records(self) -> list[Calibration]:
        try:
            return self.store.records(self.device, self.sample_rate)
        except CalibrationStoreError as exc:
            self._error(exc)
            return []

    def refresh(self) -> None:
        """Show the calibration that applies now and the history (newest first)."""
        recs = self.records()
        now = datetime.now(UTC)
        best = pick_best(recs, now)
        if best is None:
            self.lbl_current.setText(self._tr("cal_none"))
        else:
            d = best.effective(now)
            self.lbl_current.setText(
                self._tr("cal_current").format(
                    ppm=d.ppm,
                    sigma=d.uncertainty_ppm,
                    level=int(d.level),
                    date=f"{best.measured_at.astimezone():%Y-%m-%d}",
                )
            )
        self.table.setRowCount(len(recs))
        for i, r in enumerate(reversed(recs)):
            cells = (
                f"{r.measured_at.astimezone():%Y-%m-%d %H:%M}",
                f"{r.drift.ppm:+.3f}",
                f"{r.drift.uncertainty_ppm:.3f}",
                f"{int(r.drift.level)} ({r.drift.method})",
                r.drift.note,
            )
            for j, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if r is best:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.table.setItem(i, j, item)

    # ------------------------------------------------------------------ actions
    def save(self) -> bool:
        """Store the values in the form as a manual calibration (level 3)."""
        try:
            drift = AdcDrift.manual(
                self.spn_ppm.value(), self.spn_sigma.value(), note=self.txt_note.text().strip()
            )
            self.store.add(Calibration(self.device, self.sample_rate, drift, datetime.now(UTC)))
        except (ValueError, CalibrationStoreError) as exc:
            self._error(exc)
            return False
        _dbg.state_change(
            "calibración manual", "-", f"{drift.ppm:+.3f} ± {drift.systematic_ppm:.3f} ppm",
            device=self.device, fs=self.sample_rate,
        )  # fmt: skip
        self.refresh()
        return True

    def forget(self, confirm: bool = True) -> int:
        """Delete every calibration of this device and sample rate."""
        if confirm:
            answer = QMessageBox.question(
                self,
                self._tr("cal_title"),
                self._tr("cal_forget_confirm").format(device=self.device, fs=self.sample_rate),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return 0
        try:
            n = self.store.forget(self.device, self.sample_rate)
        except CalibrationStoreError as exc:
            self._error(exc)
            return 0
        self.refresh()
        return n


class AboutDialog(QDialog):
    """Version, licence, links and the folders a bug report needs."""

    def __init__(self, translator: Translator, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        tr = translator.tr
        self.setWindowTitle(tr("about_title"))
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        title = QLabel(f"<h2>Advanced Timegrapher {html.escape(__version__)}</h2>")
        title.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(title)
        body = QLabel(
            tr("about_body").format(
                repo=REPO_URL,
                license="GPL-3.0-or-later",
                python=html.escape(platform.python_version()),
                qt=html.escape(QT_VERSION_STR),
                pyqt=html.escape(PYQT_VERSION_STR),
                system=html.escape(f"{platform.system()} {platform.release()} ({sys.platform})"),
                config=html.escape(str(config_dir())),
                logs=html.escape(str(LoggerSetup.LOG_DIR)),
            )
        )
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setOpenExternalLinks(True)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        layout.addWidget(body)
        self.version_label = title

        folder_layout = QHBoxLayout()
        btn_open_logs = QPushButton(tr("open_logs_folder"))
        btn_open_logs.clicked.connect(lambda: open_folder(LoggerSetup.LOG_DIR))
        btn_open_config = QPushButton(tr("open_calibration_folder"))
        btn_open_config.clicked.connect(lambda: open_folder(config_dir()))
        folder_layout.addWidget(btn_open_logs)
        folder_layout.addWidget(btn_open_config)
        layout.addLayout(folder_layout)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class ReportDialog(QDialog):
    """Create the report ``.zip`` and open GitHub Issues (see module docstring).

    Args:
        translator: UI texts.
        settings: Current settings of the window, written into ``report.txt``.
        recording: Last session recording that may be included, or None.
        parent: Qt parent.
    """

    def __init__(
        self,
        translator: Translator,
        settings: dict[str, object],
        recording: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tr = translator.tr
        tr = self._tr
        self.settings = settings
        self.recording = recording
        self.created: Path | None = None
        self.setWindowTitle(tr("report_title"))
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        intro = QLabel(tr("report_intro").format(url=html.escape(ISSUES_URL)))
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        intro.setOpenExternalLinks(True)
        layout.addWidget(intro)
        layout.addWidget(QLabel(tr("report_description")))
        self.txt_description = QPlainTextEdit()
        self.txt_description.setPlaceholderText(tr("report_placeholder"))
        layout.addWidget(self.txt_description)
        self.chk_recording = QCheckBox(tr("report_include_recording"))
        self.chk_recording.setEnabled(recording is not None)
        self.chk_recording.setChecked(recording is not None)
        if recording is None:
            self.chk_recording.setToolTip(tr("report_no_recording"))
        layout.addWidget(self.chk_recording)
        privacy = QLabel(tr("report_privacy").format(logs=html.escape(str(LoggerSetup.LOG_DIR))))
        privacy.setWordWrap(True)
        privacy.setObjectName("HelpText")
        layout.addWidget(privacy)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.btn_create = buttons.addButton(
            tr("report_create"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.btn_create.clicked.connect(lambda: self.create())
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def create(self, path: Path | str | None = None, interactive: bool = True) -> Path | None:
        """Ask where to save (unless ``path``), write the ``.zip`` and offer to open GitHub."""
        tr = self._tr
        if path is None:
            start = Path.home() / "Desktop"
            start = start if start.is_dir() else Path.home()
            chosen, _ = QFileDialog.getSaveFileName(
                self, tr("report_title"), str(start / default_report_name()), "ZIP (*.zip)"
            )
            if not chosen:
                return None
            path = chosen
        recording = self.recording if self.chk_recording.isChecked() else None
        try:
            out, notes = build_report(
                path,
                description=self.txt_description.toPlainText(),
                settings=self.settings,
                recording=recording,
            )
        except OSError as exc:
            logger.error("No se pudo crear el reporte: %s", exc)
            if interactive:
                QMessageBox.warning(self, tr("report_title"), str(exc))
            return None
        self.created = out
        if interactive:
            box = QMessageBox(self)
            box.setWindowTitle(tr("report_title"))
            text = tr("report_done").format(path=out)
            if notes:
                text += "\n\n" + "\n".join(f"• {n}" for n in notes)
            box.setText(text)
            open_btn = box.addButton(tr("report_open_github"), QMessageBox.ButtonRole.AcceptRole)
            show_btn = box.addButton(tr("report_show_file"), QMessageBox.ButtonRole.ActionRole)
            box.addButton(QMessageBox.StandardButton.Close)
            box.exec()
            if box.clickedButton() is open_btn:
                QDesktopServices.openUrl(QUrl(ISSUES_URL))
            elif box.clickedButton() is show_btn:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(out.parent)))
            self.accept()
        return out
