"""Dialogs of the main window: manual ADC clock calibration and "About".

``CalibrationDialog`` edits the calibrations stored per device (``adv_tg.utils.adc_calibration``,
the same file as ``python -m adv_tg.tools.adc_calibration``): it shows the one that applies
now and the history, saves a manual value (level 3) or the live estimate of the last session,
and deletes the device's calibrations. A new calibration applies from the next session.
"""

from __future__ import annotations

import platform
import sys
from datetime import UTC, datetime

from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from adv_tg import __version__
from adv_tg.core.adc_clock import PPM_TO_S_PER_DAY, AdcDrift
from adv_tg.i18n.translator import Translator
from adv_tg.utils.adc_calibration import (
    Calibration,
    CalibrationStore,
    CalibrationStoreError,
    config_dir,
    pick_best,
)
from adv_tg.utils.debugger import Debugger
from adv_tg.utils.logger_setup import LoggerSetup

__all__ = ["REPO_URL", "AboutDialog", "CalibrationDialog", "LiveDrift"]

_dbg = Debugger.get(__name__)
logger = _dbg.logger

REPO_URL = "https://github.com/SPQR14/advanced_timegrapher"


class LiveDrift:
    """Live clock estimate of the last session (``ppm``, ``sigma_ppm``, confidence ``level``)."""

    def __init__(self, ppm: float, sigma_ppm: float, level: int) -> None:
        self.ppm = float(ppm)
        self.sigma_ppm = float(sigma_ppm)
        self.level = int(level)


class CalibrationDialog(QDialog):
    """Manual calibration of the ADC clock of one device (see module docstring).

    Args:
        translator: UI texts.
        device: Device name (as PortAudio reports it).
        sample_rate: Sample rate the calibration applies to.
        live: Live estimate of the last session (level >= 2), or None.
        store: Calibration store (default: the user's).
        parent: Qt parent.
    """

    def __init__(
        self,
        translator: Translator,
        device: str,
        sample_rate: int,
        live: LiveDrift | None = None,
        store: CalibrationStore | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tr = translator.tr
        self.device = device
        self.sample_rate = int(sample_rate)
        self.live = live
        self.store = store or CalibrationStore()
        tr = self._tr
        self.setWindowTitle(tr("cal_title"))
        self.setMinimumWidth(620)

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
        layout.addLayout(form)

        row = QHBoxLayout()
        self.btn_live = QPushButton(tr("cal_use_live"))
        self.btn_live.setEnabled(live is not None)
        if live is not None:
            self.btn_live.setToolTip(
                tr("cal_live_tooltip").format(ppm=live.ppm, sigma=live.sigma_ppm, level=live.level)
            )
        self.btn_live.clicked.connect(self._use_live)
        self.btn_save = QPushButton(tr("cal_save"))
        self.btn_save.clicked.connect(self.save)
        self.btn_forget = QPushButton(tr("cal_forget"))
        self.btn_forget.clicked.connect(self.forget)
        row.addWidget(self.btn_live)
        row.addStretch()
        row.addWidget(self.btn_forget)
        row.addWidget(self.btn_save)
        layout.addLayout(row)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            [tr("cal_col_date"), "ppm", "± ppm", tr("cal_col_level"), tr("cal_col_note")]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh_bias()
        self.refresh()

    # ------------------------------------------------------------------ helpers
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
        title = QLabel(f"<h2>Advanced Timegrapher {__version__}</h2>")
        title.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(title)
        body = QLabel(
            tr("about_body").format(
                repo=REPO_URL,
                license="GPL-3.0-or-later",
                python=platform.python_version(),
                qt=QT_VERSION_STR,
                pyqt=PYQT_VERSION_STR,
                system=f"{platform.system()} {platform.release()} ({sys.platform})",
                config=config_dir(),
                logs=LoggerSetup.LOG_DIR,
            )
        )
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setOpenExternalLinks(True)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        layout.addWidget(body)
        self.version_label = title
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
