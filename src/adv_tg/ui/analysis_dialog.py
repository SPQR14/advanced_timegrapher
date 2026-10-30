""" "Analyze Recording" window: diagnosis of one recording, in three tabs.

* **Gear train** (``adv_tg.analysis.gear_train``): the per-beat timing residual and P3
  Teager-Kaiser energy, their Lomb-Scargle periodogram and, for the escape wheel and the
  fourth wheel (only from 180 s of audio), amplitude, rate ripple, false-alarm probability
  and verdict.
* **Features** (``adv_tg.analysis.features``): the feature vector of every 60 s window and
  of the whole recording; "Export features" saves them as JSON (a record for the ML).
* **Diagnosis**: the rules over those features (rebanking, pallet lubrication, magnetism,
  gear-train components) with their evidence, and the horizontal-vertical differentials of
  the saved positions. Provisional thresholds: indications to check, not a diagnosis.

The beat detection (the slow part, ~40x real time) runs in a ``QThread`` so the window
stays responsive; changing the escape-wheel teeth only recomputes spectra and features.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from numpy.typing import NDArray
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from adv_tg.analysis.features import (
    FeatureVector,
    Finding,
    PositionalDeltas,
    diagnose,
    positional_deltas,
    recording_features,
    window_features,
)
from adv_tg.analysis.gear_train import (
    COMPONENT_ESCAPE,
    DEFAULT_ESCAPE_TEETH,
    FOURTH_WHEEL_PERIOD_S,
    MIN_CYCLES,
    SERIES_TIMING,
    BeatSeries,
    Component,
    GearTrainReport,
    NotEnoughBeats,
    extract_series,
    spectral_report,
)
from adv_tg.i18n.translator import Translator
from adv_tg.utils.audio_files import read_mono, read_tags
from adv_tg.utils.debugger import Debugger

__all__ = ["AnalysisDialog", "RecordingSource"]

_dbg = Debugger.get(__name__)
logger = _dbg.logger

TIMING_COLOR = "#00e5ff"
ENERGY_COLOR = "#ffab00"
MARKER_COLOR = "#ff5252"
#: Labelled periods on the logarithmic axis of the periodogram (s).
PERIOD_TICKS_S: tuple[float, ...] = (0.5, 1, 2, 3, 5, 10, 20, 30, 60, 120, 240)


class RecordingSource:
    """Audio and calibre of the recording to analyse.

    Args:
        samples: Mono audio.
        sample_rate: Hz.
        bph: Beats per hour of the calibre.
        lift_angle_deg: Lift angle (only bounds the detector's P1 search).
        name: Shown in the window (file name or "last session").
        quartz: The recording is of a quartz watch (not analysable).
    """

    def __init__(
        self,
        samples: NDArray,
        sample_rate: float,
        bph: float,
        lift_angle_deg: float = 52.0,
        name: str = "",
        quartz: bool = False,
    ) -> None:
        self.samples = np.asarray(samples, dtype=np.float64)
        self.sample_rate = float(sample_rate)
        self.bph = float(bph)
        self.lift_angle_deg = float(lift_angle_deg)
        self.name = name
        self.quartz = bool(quartz)

    @classmethod
    def from_file(
        cls, path: Path | str, bph: float, lift_angle_deg: float, quartz: bool = False
    ) -> RecordingSource:
        """Read a WAV/FLAC; its ``ADV_TG_*`` tags override the given calibre."""
        p = Path(path)
        fs, x = read_mono(p)
        try:
            tags = read_tags(p)
        except Exception:  # untagged or unreadable tags: keep the top-bar calibre
            tags = {}
        if "ADV_TG_BPH" in tags:
            bph = float(tags["ADV_TG_BPH"])
            quartz = tags.get("ADV_TG_QUARTZ") == "1"
        if "ADV_TG_LIFT_ANGLE" in tags:
            lift_angle_deg = float(tags["ADV_TG_LIFT_ANGLE"])
        return cls(x, fs, bph, lift_angle_deg, p.name, quartz)


class _SeriesThread(QThread):
    """Runs ``extract_series`` (beat detection) off the GUI thread."""

    done = pyqtSignal(object)
    failed = pyqtSignal(object)

    def __init__(self, source: RecordingSource, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.source = source

    def run(self) -> None:  # noqa: D102 - QThread entry point
        s = self.source
        try:
            series = extract_series(s.samples, s.sample_rate, s.bph, s.lift_angle_deg)
        except Exception as exc:  # reported in the window, never raised in the thread
            self.failed.emit(exc)
            return
        self.done.emit(series)


def _period_axis_plot(title: str) -> pg.PlotWidget:
    plot = pg.PlotWidget()
    plot.setTitle(title)
    plot.showGrid(x=True, y=True, alpha=0.25)
    plot.setMinimumHeight(170)
    return plot


class AnalysisDialog(QDialog):
    """Gear-train analysis of one recording (see module docstring).

    Args:
        translator: UI texts.
        default_bph, default_lift_angle: Calibre for files without ``ADV_TG_*`` tags.
        parent: Qt parent.
        positions: Saved positions, name -> (rate s/d, beat error ms, amplitude deg), for the
            horizontal-vertical differentials of the diagnosis.
    """

    def __init__(
        self,
        translator: Translator,
        default_bph: float = 28800,
        default_lift_angle: float = 52.0,
        parent: QWidget | None = None,
        positions: Mapping[str, tuple[float, float, float]] | None = None,
    ) -> None:
        super().__init__(parent)
        self._tr = tr = translator.tr
        self.default_bph = float(default_bph)
        self.default_lift_angle = float(default_lift_angle)
        self.source: RecordingSource | None = None
        self.series: BeatSeries | None = None
        self.report: GearTrainReport | None = None
        self.windows: list[FeatureVector] = []
        self.whole: FeatureVector | None = None
        self.findings: list[Finding] = []
        self.positional: PositionalDeltas | None = (
            positional_deltas(positions) if positions else None
        )
        self._thread: _SeriesThread | None = None
        self.setWindowTitle(tr("an_title"))
        self.resize(980, 860)

        layout = QVBoxLayout(self)
        self.lbl_source = QLabel()
        self.lbl_source.setObjectName("SectionHeader")
        layout.addWidget(self.lbl_source)
        self.lbl_summary = QLabel()
        self.lbl_summary.setWordWrap(True)
        layout.addWidget(self.lbl_summary)

        row = QHBoxLayout()
        row.addWidget(QLabel(tr("an_teeth")))
        self.spn_teeth = QSpinBox()
        self.spn_teeth.setRange(6, 40)
        self.spn_teeth.setValue(DEFAULT_ESCAPE_TEETH)
        self.spn_teeth.setToolTip(tr("an_teeth_tooltip"))
        self.spn_teeth.valueChanged.connect(self._on_teeth_changed)
        row.addWidget(self.spn_teeth)
        self.lbl_escape_period = QLabel()
        row.addWidget(self.lbl_escape_period)
        row.addStretch()
        self.btn_export = QPushButton(tr("an_export"))
        self.btn_export.setToolTip(tr("an_export_tooltip"))
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(lambda: self.export_features())
        row.addWidget(self.btn_export)
        self.btn_open = QPushButton(tr("an_open_file"))
        self.btn_open.clicked.connect(lambda: self.open_file())
        row.addWidget(self.btn_open)
        layout.addLayout(row)

        self.lbl_status = QLabel()
        self.lbl_status.setWordWrap(True)
        layout.addWidget(self.lbl_status)

        self.plot_timing = _period_axis_plot(tr("an_plot_timing"))
        self.plot_timing.setLabel("bottom", tr("an_axis_time"))
        self.plot_timing.setLabel("left", "µs")
        self.plot_energy = _period_axis_plot(tr("an_plot_energy"))
        self.plot_energy.setLabel("bottom", tr("an_axis_time"))
        self.plot_energy.setLabel("left", "%")
        self.plot_spectrum = _period_axis_plot(tr("an_plot_spectrum"))
        self.plot_spectrum.setLabel("bottom", tr("an_axis_period"))
        self.plot_spectrum.setLogMode(x=True, y=False)
        self.plot_spectrum.getAxis("bottom").setTicks(
            [[(math.log10(v), f"{v:g}") for v in PERIOD_TICKS_S]]
        )
        self.plot_spectrum.addLegend(offset=(10, 10))

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        gear_tab = QWidget()
        gear_layout = QVBoxLayout(gear_tab)
        gear_layout.setContentsMargins(0, 6, 0, 0)
        for plot in (self.plot_timing, self.plot_energy, self.plot_spectrum):
            gear_layout.addWidget(plot)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            [
                tr("an_col_component"),
                tr("an_col_series"),
                tr("an_col_expected"),
                tr("an_col_measured"),
                tr("an_col_amplitude"),
                tr("an_col_ripple"),
                tr("an_col_fap"),
                tr("an_col_verdict"),
            ]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setMinimumHeight(150)
        gear_layout.addWidget(self.table)
        help_lbl = QLabel(tr("an_help"))
        help_lbl.setWordWrap(True)
        help_lbl.setObjectName("HelpText")
        gear_layout.addWidget(help_lbl)
        self.tabs.addTab(gear_tab, tr("an_tab_gear"))

        # Feature vectors: one column per 60 s window plus the whole recording
        feat_tab = QWidget()
        feat_layout = QVBoxLayout(feat_tab)
        feat_layout.setContentsMargins(0, 6, 0, 0)
        feat_help = QLabel(tr("an_features_help"))
        feat_help.setWordWrap(True)
        feat_help.setObjectName("HelpText")
        feat_layout.addWidget(feat_help)
        self.tbl_features = QTableWidget(0, 0)
        self.tbl_features.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        feat_layout.addWidget(self.tbl_features)
        self.tabs.addTab(feat_tab, tr("an_tab_features"))

        # Rule-based diagnosis
        diag_tab = QWidget()
        diag_layout = QVBoxLayout(diag_tab)
        diag_layout.setContentsMargins(0, 6, 0, 0)
        self.lbl_positional = QLabel()
        self.lbl_positional.setWordWrap(True)
        diag_layout.addWidget(self.lbl_positional)
        self.tbl_findings = QTableWidget(0, 3)
        self.tbl_findings.setHorizontalHeaderLabels(
            [tr("an_col_finding"), tr("an_col_level"), tr("an_col_evidence")]
        )
        header = self.tbl_findings.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.tbl_findings.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tbl_findings.setWordWrap(True)
        diag_layout.addWidget(self.tbl_findings)
        diag_help = QLabel(tr("an_diag_help"))
        diag_help.setWordWrap(True)
        diag_help.setObjectName("HelpText")
        diag_layout.addWidget(diag_help)
        self.tabs.addTab(diag_tab, tr("an_tab_diagnosis"))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh_escape_label()

    # ------------------------------------------------------------------ sources
    def open_file(self, path: Path | str | None = None, blocking: bool = False) -> bool:
        """Pick a WAV/FLAC (or use ``path``) and analyse it."""
        if path is None:
            chosen, _ = QFileDialog.getOpenFileName(
                self, self._tr("an_open_file"), "", "Audio (*.flac *.wav)"
            )
            if not chosen:
                return False
            path = chosen
        try:
            source = RecordingSource.from_file(path, self.default_bph, self.default_lift_angle)
        except Exception as exc:
            logger.error("Análisis: no se pudo leer %s: %s", path, exc)
            self._set_status(self._tr("an_failed").format(error=exc), error=True)
            return False
        self.analyze(source, blocking=blocking)
        return True

    def analyze(self, source: RecordingSource, blocking: bool = False) -> None:
        """Start the analysis of ``source`` (``blocking`` waits for it: tests, scripts)."""
        self._clear()
        self.source = source
        self.lbl_source.setText(self._tr("an_source").format(name=source.name or "—"))
        if source.quartz:
            self._set_status(self._tr("an_quartz"), error=True)
            return
        self._set_status(self._tr("an_running"))
        self.btn_open.setEnabled(False)
        _dbg.process_start(
            "análisis de rodaje", source=source.name, bph=source.bph, fs=source.sample_rate
        )
        thread = _SeriesThread(source, self)
        thread.done.connect(self._on_series)
        thread.failed.connect(self._on_failed)
        thread.finished.connect(lambda: self.btn_open.setEnabled(True))
        self._thread = thread
        thread.start()
        if blocking:
            thread.wait()
            from PyQt6.QtCore import QCoreApplication

            QCoreApplication.processEvents()  # deliver the queued done/failed signal

    def wait(self, timeout_ms: int = 60000) -> bool:
        """Wait for a running analysis (True when none is left)."""
        return self._thread is None or self._thread.wait(timeout_ms)

    # ------------------------------------------------------------------ results
    def _on_series(self, series: BeatSeries) -> None:
        self.series = series
        _dbg.process_end("análisis de rodaje", beats=int(series.k.size))
        self._recompute()

    def _on_failed(self, exc: Exception) -> None:
        _dbg.process_end("análisis de rodaje", error=str(exc))
        if isinstance(exc, NotEnoughBeats):
            msg = self._tr("an_too_few_beats").format(found=exc.args[1])
        else:
            logger.error("Análisis de rodaje fallido: %s", exc)
            msg = self._tr("an_failed").format(error=exc)
        self._set_status(msg, error=True)

    def _on_teeth_changed(self, _value: int) -> None:
        self._refresh_escape_label()
        if self.series is not None:
            self._recompute()

    def _refresh_escape_label(self) -> None:
        bph = (
            self.series.bph
            if self.series is not None
            else (self.source.bph if self.source is not None else self.default_bph)
        )
        period = 2.0 * self.spn_teeth.value() * 3600.0 / bph
        self.lbl_escape_period.setText(
            self._tr("an_escape_period").format(period=period, bph=f"{bph:.0f}")
        )

    def _recompute(self) -> None:
        assert self.series is not None
        teeth = self.spn_teeth.value()
        self.report = spectral_report(self.series, teeth)
        self.windows = window_features(self.series, teeth)
        self.whole = recording_features(self.series, teeth)
        self.findings = diagnose(self.whole, self.windows, self.positional, self.report)
        self._show(self.report)
        self._show_features()
        self._show_findings()
        self.btn_export.setEnabled(True)

    def _show(self, report: GearTrainReport) -> None:
        tr = self._tr
        s = report.series
        self.lbl_summary.setText(
            tr("an_summary").format(
                duration=s.duration_s,
                bph=f"{s.bph:.0f}",
                used=int(s.k.size),
                missing=s.missing_beats,
                rate=s.rate_s_per_day,
            )
        )
        warnings = [
            tr(f"an_warn_{w}").format(min_s=MIN_CYCLES * self._warn_period(w, report))
            for w in report.warnings
        ]
        found = [c for c in report.components if c.significant]
        status = tr("an_found").format(n=len(found)) if found else tr("an_none_found")
        self._set_status(" ".join([status, *warnings]), error=False)
        self._refresh_escape_label()

        self.plot_timing.clear()
        self.plot_timing.plot(s.t_s, s.timing_residual_s * 1e6, pen=pg.mkPen(TIMING_COLOR, width=1))
        self.plot_energy.clear()
        self.plot_energy.plot(s.t_s, s.energy_rel * 100.0, pen=pg.mkPen(ENERGY_COLOR, width=1))

        sp = self.plot_spectrum
        sp.clear()
        period = 1.0 / report.timing.freqs_hz
        for spec, color, key in (
            (report.timing, TIMING_COLOR, "an_series_timing"),
            (report.energy, ENERGY_COLOR, "an_series_energy"),
        ):
            p = spec.ls_power
            sp.plot(period, p / p.max() if p.max() > 0 else p, pen=pg.mkPen(color, width=1),
                    name=tr(key))  # fmt: skip
        for t_mark, key in (
            (report.escape_period_s, "an_escape_wheel"),
            (FOURTH_WHEEL_PERIOD_S, "an_fourth_wheel"),
        ):
            line = pg.InfiniteLine(
                pos=math.log10(t_mark),
                angle=90,
                pen=pg.mkPen(MARKER_COLOR, width=1, style=pg.QtCore.Qt.PenStyle.DashLine),
                label=f"{tr(key)} {t_mark:g} s",
                labelOpts={"position": 0.9, "color": MARKER_COLOR},
            )
            sp.addItem(line)

        self.table.setRowCount(len(report.components))
        for i, c in enumerate(report.components):
            for j, text in enumerate(self._row(c)):
                self.table.setItem(i, j, QTableWidgetItem(text))

    @staticmethod
    def _warn_period(code: str, report: GearTrainReport) -> float:
        if code == "short_for_escape":
            return report.escape_period_s
        if code == "short_for_fourth":
            return FOURTH_WHEEL_PERIOD_S
        return 0.0

    def _row(self, c: Component) -> list[str]:
        tr = self._tr
        name = tr("an_escape_wheel") if c.name == COMPONENT_ESCAPE else tr("an_fourth_wheel")
        timing = c.series == SERIES_TIMING
        series = tr("an_series_timing") if timing else tr("an_series_energy")
        expected = f"{c.expected_period_s:.2f} s"
        if not c.resolvable:
            verdict = tr("an_verdict_unresolvable").format(min_s=MIN_CYCLES * c.expected_period_s)
            return [name, series, expected, "—", "—", "—", "—", verdict]
        amp = f"{c.amplitude * 1e6:.1f} µs" if timing else f"{c.amplitude * 100:.1f} %"
        ripple = f"{c.rate_ripple_s_per_day:.2f} s/d" if timing else "—"
        fap = "< 1e-12" if c.fap < 1e-12 else f"{c.fap:.1e}"
        verdict = tr("an_verdict_present") if c.significant else tr("an_verdict_absent")
        return [name, series, expected, f"{c.period_s:.2f} s", amp, ripple, fap, verdict]

    # ------------------------------------------------------------------ features
    #: Rows of the features table: (FeatureVector field, label key, format).
    FEATURE_ROWS: tuple[tuple[str, str, str], ...] = (
        ("n_beats", "an_f_n_beats", "{:d}"),
        ("rate_spd", "an_f_rate", "{:+.2f}"),
        ("beat_error_ms", "an_f_be", "{:.3f}"),
        ("amplitude_deg", "an_f_amp", "{:.0f}"),
        ("escape_power", "an_f_escape_power", "{:.2f}"),
        ("escape_amp_us", "an_f_escape_amp", "{:.1f}"),
        ("fourth_power", "an_f_fourth_power", "{:.2f}"),
        ("fourth_amp_us", "an_f_fourth_amp", "{:.1f}"),
        ("d12_mean_ms", "an_f_d12_mean", "{:.3f}"),
        ("d12_var_ms2", "an_f_d12_var", "{:.2e}"),
        ("d12_skew", "an_f_d12_skew", "{:+.2f}"),
        ("d12_kurt", "an_f_d12_kurt", "{:+.2f}"),
        ("d23_mean_ms", "an_f_d23_mean", "{:.3f}"),
        ("d23_var_ms2", "an_f_d23_var", "{:.2e}"),
        ("d23_skew", "an_f_d23_skew", "{:+.2f}"),
        ("d23_kurt", "an_f_d23_kurt", "{:+.2f}"),
        ("e1_e2", "an_f_e1_e2", "{:.2f}"),
        ("e3_e2", "an_f_e3_e2", "{:.2f}"),
        ("spurious_fraction", "an_f_spurious", "{:.1%}"),
        ("timing_jumps", "an_f_jumps", "{:d}"),
    )

    def _show_features(self) -> None:
        tr = self._tr
        vectors = [*self.windows, *([self.whole] if self.whole is not None else [])]
        t = self.tbl_features
        t.clear()
        t.setRowCount(len(self.FEATURE_ROWS))
        t.setColumnCount(len(vectors))
        t.setHorizontalHeaderLabels(
            [
                tr("an_whole") if v.whole else f"{v.window_start_s:.0f}–{v.window_end_s:.0f} s"
                for v in vectors
            ]
        )
        t.setVerticalHeaderLabels([tr(key) for _, key, _ in self.FEATURE_ROWS])
        for i, (name, _key, fmt) in enumerate(self.FEATURE_ROWS):
            for j, v in enumerate(vectors):
                value = getattr(v, name)
                ok = not (isinstance(value, float) and not math.isfinite(value))
                t.setItem(i, j, QTableWidgetItem(fmt.format(value) if ok else "—"))
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

    def _show_findings(self) -> None:
        tr = self._tr
        p = self.positional
        if p is None:
            self.lbl_positional.setText(tr("an_positional_none"))
        else:
            self.lbl_positional.setText(
                tr("an_positional").format(
                    positions=", ".join(p.positions),
                    rate=p.delta_rate_hv_spd,
                    amp=p.delta_amp_hv_deg,
                    be=p.be_spread_ms,
                )
            )
        rows = self.findings or []
        self.tbl_findings.setRowCount(max(1, len(rows)))
        if not rows:
            self.tbl_findings.setItem(0, 0, QTableWidgetItem(tr("an_no_findings")))
            self.tbl_findings.setItem(0, 1, QTableWidgetItem(""))
            self.tbl_findings.setItem(0, 2, QTableWidgetItem(""))
            return
        for i, f in enumerate(rows):
            evidence = ", ".join(f"{k} = {v:.3g}" for k, v in f.evidence.items())
            self.tbl_findings.setItem(i, 0, QTableWidgetItem(tr(f"an_finding_{f.code}")))
            self.tbl_findings.setItem(i, 1, QTableWidgetItem(tr(f"an_level_{f.level}")))
            self.tbl_findings.setItem(i, 2, QTableWidgetItem(evidence))
        self.tbl_findings.resizeRowsToContents()

    def features_dict(self) -> dict[str, object]:
        """Everything the analysis found, as plain JSON values (one record for the ML)."""
        s = self.series
        assert s is not None and self.whole is not None and self.report is not None
        return {
            "source": self.source.name if self.source else "",
            "sample_rate": s.sample_rate,
            "bph": s.bph,
            "lift_angle_deg": s.lift_angle_deg,
            "duration_s": s.duration_s,
            "escape_teeth": self.report.escape_teeth,
            "windows": [v.as_dict() for v in self.windows],
            "whole": self.whole.as_dict(),
            "positional": self.positional.as_dict() if self.positional else None,
            "gear_train": [
                {
                    "wheel": c.name,
                    "series": c.series,
                    "expected_period_s": c.expected_period_s,
                    "period_s": c.period_s if math.isfinite(c.period_s) else None,
                    "amplitude": c.amplitude if math.isfinite(c.amplitude) else None,
                    "fap": c.fap,
                    "resolvable": c.resolvable,
                    "significant": c.significant,
                }
                for c in self.report.components
            ],
            "findings": [
                {"code": f.code, "level": f.level, "evidence": f.evidence} for f in self.findings
            ],
        }

    def export_features(self, path: Path | str | None = None) -> Path | None:
        """Save ``features_dict()`` as JSON (a dataset record for the ML)."""
        if self.series is None:
            return None
        if path is None:
            stem = Path(self.source.name).stem if self.source and self.source.name else "analysis"
            chosen, _ = QFileDialog.getSaveFileName(
                self, self._tr("an_export"), f"{stem}_features.json", "JSON (*.json)"
            )
            if not chosen:
                return None
            path = chosen
        target = Path(path)
        try:
            target.write_text(
                json.dumps(self.features_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            self._set_status(self._tr("an_failed").format(error=exc), error=True)
            return None
        _dbg.file_write(target, nbytes=target.stat().st_size)
        return target

    # ------------------------------------------------------------------ helpers
    def _set_status(self, text: str, error: bool = False) -> None:
        self.lbl_status.setText(text)
        self.lbl_status.setStyleSheet("color: #ff5252;" if error else "")

    def _clear(self) -> None:
        self.series = None
        self.report = None
        self.windows, self.whole, self.findings = [], None, []
        self.btn_export.setEnabled(False)
        self.lbl_summary.setText("")
        self.table.setRowCount(0)
        self.tbl_features.clear()
        self.tbl_features.setRowCount(0)
        self.tbl_findings.setRowCount(0)
        for plot in (self.plot_timing, self.plot_energy, self.plot_spectrum):
            plot.clear()

    def done(self, result: int) -> None:  # noqa: D102 - wait for the thread before closing
        self.wait()
        super().done(result)
