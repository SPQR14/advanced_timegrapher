"""Main window and real-time PyQtGraph visualization for Advanced Timegrapher."""

from __future__ import annotations

import csv
import math
import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QRectF, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QImage,
    QPageLayout,
    QPageSize,
    QPainter,
    QSurfaceFormat,
)
from PyQt6.QtPrintSupport import QPrintDialog, QPrinter
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListView,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from adv_tg.core.models import BeatEvent, BeatTelemetry, TelemetryResult, WatchConfig
from adv_tg.i18n.translator import Translator
from adv_tg.mock.generator import generate_tick_train, save_wav
from adv_tg.mock.worker import MockStreamWorker
from adv_tg.ui.capture_worker import (
    CaptureStreamWorker,
    SlotProfiler,
    level_text,
    level_to_quality,
)
from adv_tg.ui.dialogs import (
    AboutDialog,
    CalibrationDialog,
    LiveDrift,
    ReportDialog,
    open_folder,
)
from adv_tg.ui.measure_worker import MeasureStreamWorker
from adv_tg.ui.measure_worker import device_name as measure_device_name
from adv_tg.ui.stream_worker_base import BaseStreamWorker
from adv_tg.ui.worker_factory import create_worker
from adv_tg.utils.adc_calibration import config_dir
from adv_tg.utils.audio_io import AudioDeviceInfo, list_input_devices
from adv_tg.utils.debugger import Debugger
from adv_tg.utils.logger_setup import LoggerSetup
from adv_tg.utils.security import sanitize_csv_cell
from adv_tg.utils.watch_db import load_watch_db

logger = LoggerSetup.setup_logger(__name__)
_dbg = Debugger.get(__name__)


def configure_pyqtgraph_gpu(enable_opengl: bool = False) -> bool:
    """Configure PyQtGraph rendering options with experimental OpenGL disabled by default.

    Experimental OpenGL in PyQt6 causes multi-color vertex tearing on overlaid curves,
    so standard CPU rendering is used by default unless ``enable_opengl=True`` is
    explicitly passed in a non-headless session.
    """
    pg.setConfigOptions(background="#0d1015", foreground="#9aa5b4")
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen" or not enable_opengl:
        pg.setConfigOptions(useOpenGL=False, enableExperimental=False, antialias=True)
        logger.info(
            "Configured PyQtGraph standard CPU rendering (useOpenGL=False, enableExperimental=False)"
        )
        return False

    try:
        import OpenGL.GL  # noqa: F401

        fmt = QSurfaceFormat.defaultFormat()
        fmt.setSwapBehavior(QSurfaceFormat.SwapBehavior.DoubleBuffer)
        fmt.setSamples(0)
        QSurfaceFormat.setDefaultFormat(fmt)

        pg.setConfigOptions(useOpenGL=True, enableExperimental=False, antialias=True)
        logger.info("Enabled PyQtGraph OpenGL rendering with enableExperimental=False")
        return True
    except Exception as exc:
        logger.warning(
            "PyQtGraph OpenGL unavailable (%s); falling back to CPU rendering",
            exc,
        )
        pg.setConfigOptions(useOpenGL=False, enableExperimental=False, antialias=True)
        return False


configure_pyqtgraph_gpu()


def _resolve_assets_dir() -> Path:
    """Resolve directory of application assets (SVG arrows, icons).

    Supports standard source/package execution and PyInstaller frozen bundles
    (which extract data files to ``sys._MEIPASS`` or macOS ``Contents/Resources``).
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        meipass = Path(sys._MEIPASS)
        for cand in (
            meipass / "adv_tg" / "ui" / "assets",
            meipass / "ui" / "assets",
            meipass / "assets",
            meipass / "Resources" / "adv_tg" / "ui" / "assets",
            meipass.parent / "Resources" / "adv_tg" / "ui" / "assets",
        ):
            if cand.is_dir():
                return cand
    return Path(__file__).resolve().parent / "assets"


ASSETS_DIR = _resolve_assets_dir()
APP_ICON_PATH = ASSETS_DIR / "adv-tg.svg"
#: Page opened by the "Buy me a coffee" button.
BUY_ME_A_COFFEE_URL = "https://buymeacoffee.com/sqpr14"


#: Mechanical beats per hour offered in the top bar.
MECHANICAL_BPH: tuple[int, ...] = (18000, 19800, 21600, 25200, 28800, 36000)
#: Quartz motor steps per second (Citizen 1/s, TMI 4/s, Bulova Precisionist 16/s...).
QUARTZ_STEPS_PER_S: tuple[int, ...] = (1, 2, 4, 8, 16)
DEFAULT_QUARTZ_STEPS: int = 1

#: Test durations offered in the top bar (seconds).
DURATION_CHOICES_S: tuple[int, ...] = (
    12,
    24,
    30,
    45,
    60,
    120,
    240,
    300,
    900,
    1800,
    3600,
)
DEFAULT_DURATION_S: int = 60
#: The paper tape draws point symbols up to this many points per curve.
TAPE_SYMBOL_MAX_POINTS: int = 240
#: Smallest half height of the paper tape view, in ms.
TAPE_MIN_HALF_SPAN_MS: float = 1.0
#: Longest test whose audio is recorded (FLAC of the session; ~66 KB/s with a CM18). Longer
#: tests are not recorded: the temporary file would grow to hundreds of MB.
RECORDING_MAX_DURATION_S: int = 240
#: Stabilization times offered in the top bar (seconds).
STABILIZATION_CHOICES_S: tuple[int, ...] = (10, 15, 20)
DEFAULT_STABILIZATION_S: int = 10


def _format_duration(seconds: float) -> str:
    """``30 s``, ``120 s``, ``240 s``, ``5 min``, ``1 h`` (seconds below 5 minutes)."""
    secs = float(seconds)
    if secs >= 3600 and secs % 3600 == 0:
        return f"{int(secs // 3600)} h"
    if secs >= 300 and secs % 60 == 0:
        return f"{int(secs // 60)} min"
    return f"{secs:g} s"


def default_input_device_id() -> int | None:
    """PortAudio index of the system's default input device, or None if unknown."""
    try:
        from adv_tg.audio.capture import load_sounddevice

        idx = int(load_sounddevice().default.device[0])
    except Exception:
        return None
    return idx if idx >= 0 else None


def app_icon() -> QIcon:
    """Application icon (vectorised from ``assets/adv-tg.jpg``); null icon if missing."""
    if not APP_ICON_PATH.is_file():
        logger.warning("Icono de la aplicación no encontrado: %s", APP_ICON_PATH)
        return QIcon()
    return QIcon(str(APP_ICON_PATH))


CALIBER_PRESETS: dict[str, tuple[int, float]] = load_watch_db()

POSITION_VARIATION: dict[str, tuple[float, float, float]] = {
    "DU": (+4.5, 0.35, 282.0),
    "DD": (+3.8, 0.40, 278.0),
    "CU": (-1.5, 0.55, 248.0),
    "CD": (-2.2, 0.50, 245.0),
    "CL": (+1.2, 0.45, 255.0),
    "CR": (+0.6, 0.48, 252.0),
}


class ComboPopupItemDelegate(QStyledItemDelegate):
    """Custom item delegate ensuring generous row height for QComboBox popup items on macOS."""

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), 28))
        return size


class HudCard(QFrame):
    """Industrial monospaced numeric HUD display card for a primary horological metric."""

    def __init__(
        self,
        title: str,
        unit: str,
        accent_color: str = "#00e5ff",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("HudCard")
        self._accent_color = accent_color

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)

        header_layout = QHBoxLayout()
        self.title_label = QLabel(title)
        self.title_label.setObjectName("HudCardTitle")
        self.unit_label = QLabel(unit)
        self.unit_label.setObjectName("HudCardUnit")
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()
        header_layout.addWidget(self.unit_label)

        self.value_label = QLabel("---.-")
        self.value_label.setObjectName("HudCardValue")
        self.value_label.setMinimumHeight(58)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        mono_font = QFont("JetBrains Mono", 39, QFont.Weight.Bold)
        mono_font.setStyleHint(QFont.StyleHint.Monospace)
        self.value_label.setFont(mono_font)
        self.dimmed = False
        self._apply_value_style()

        layout.addLayout(header_layout)
        layout.addWidget(self.value_label)

    def set_title(self, title: str) -> None:
        """Update localized title text."""
        self.title_label.setText(title.upper())

    DIM_COLOR: str = "#5c6b80"

    def _apply_value_style(self) -> None:
        color = self.DIM_COLOR if self.dimmed else self._accent_color
        self.value_label.setStyleSheet(
            f"color: {color}; "
            f'font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace; '
            f"font-size: 39pt; "
            f"font-weight: 800; "
            f"padding-bottom: 2px;"
        )

    def set_value(self, text: str, dimmed: bool = False) -> None:
        """Update numeric readout string; ``dimmed`` greys it out (low confidence)."""
        self.value_label.setText(text)
        if dimmed != self.dimmed:
            self.dimmed = dimmed
            self._apply_value_style()


class MainWin(QMainWindow):
    """Primary application window for Advanced Timegrapher."""

    COSC_MIN_SPD: float = -4.0
    COSC_MAX_SPD: float = +6.0
    # Real engine: figures whose timing confidence is below CONFIDENCE_HIDE are not shown;
    # below CONFIDENCE_DIM they are greyed out (the amplitude also when the overall
    # confidence, which includes the tic/tac mismatch, is below it). Real takes range 0.23
    # (H-50, noisy tac) to 0.64 (Miyota), see docs/fase3_resultados.md; the confidence is a
    # ranking, not a probability.
    CONFIDENCE_HIDE: float = 0.15
    CONFIDENCE_DIM: float = 0.30
    # Quality bar of the real engine: anchor SNR mapped linearly from 12 dB (detection limit)
    # to 25 dB, times the share of beats that feed the figures. Calibrated on the real takes
    # (SNR 23-32 dB, 75-93 % usable -> 75-85 %, "ok"); a beat at the detector's "weak" SNR
    # (20 dB) gives at most 62 % ("low").
    QUALITY_SNR_FLOOR_DB: float = 12.0
    QUALITY_SNR_FULL_DB: float = 25.0
    SATURATION_HOLD_S: float = 2.0
    # Exact digital silence this long means the OS is not giving us the microphone (macOS and
    # Windows deliver zeros when the permission is denied): tell the user where to allow it.
    SILENCE_HINT_S: float = 3.0
    EMPTY_VALUE: str = "---.-"

    def __init__(self, translator: Translator | None = None) -> None:
        super().__init__()
        logger.info("Initializing MainWin UI")
        self._gpu_enabled: bool = configure_pyqtgraph_gpu()
        self.translator = translator if translator is not None else Translator("en")
        # Engine per device (create_worker): MockStreamWorker or MeasureStreamWorker
        self.dsp_worker: BaseStreamWorker | None = None
        self._capture_state: str = "INACTIVO"
        self._active_device_id: int | None = None
        self._active_watch_config: WatchConfig | None = None
        self._last_position: str = "DU"
        self._last_hw_device_index: int | None = None

        # Telemetry history for statistics & horizontal paper tape trace
        self._rate_history: list[float] = []
        self._tape_x_even: list[float] = []
        self._tape_ms_even: list[float] = []
        self._tape_x_odd: list[float] = []
        self._tape_ms_odd: list[float] = []
        self._last_telemetry: TelemetryResult | None = None
        self._session_records: list[dict[str, float | str | int]] = []
        self._capture_start_monotonic: float | None = None
        self._last_effective_fs: float = 48000.0
        self._last_drift_ppm: float = 0.0
        self._last_rate_bias_spd: float = 0.0
        # MOCK-only drift detail (None while the engine does not provide it)
        self._last_drift_sigma_ppm: float | None = None
        self._last_drift_confidence: float | None = None
        # Real engine clock detail (clock_info): level 0-3, None while unknown
        self._last_clock_level: int | None = None
        self._last_clock_source: str = ""
        self._last_saturated_monotonic: float = -math.inf
        self._last_warning: str = ""
        self._tape_autoscale: bool = True
        self._tape_y0: float | None = None  # offset of the first tape point (trace starts at 0)
        self._osc_span_ms: float | None = None
        self._silence_since: float | None = None
        self._silence_warned: bool = False
        self._osc_rng = np.random.default_rng(2026)

        # Audio recording state (same duration as test)
        self._recorded_audio: np.ndarray | None = None
        self._recorded_sample_rate: int = 48000
        self._save_audio_on_finish: bool = False
        # The quality bar follows the input level until the engine reports detection quality
        self._telemetry_seen: bool = False
        self._perf_waveform = SlotProfiler("waveform_ready")
        self._perf_level = SlotProfiler("level_updated")

        # Saved positional telemetry records (starts clean with no pre-populated data)
        self._saved_positions: dict[str, TelemetryResult] = {}

        self.setMinimumSize(1360, 840)
        self._apply_industrial_theme()
        self._build_ui()

        # Engine chosen per device by create_worker() (synthetic -> MockStreamWorker,
        # hardware -> MeasureStreamWorker); each engine owns its threads.
        self._apply_device_selection()

        # Wire UI controls to update the engine configuration in real time
        self.cmb_bph.currentTextChanged.connect(self._on_watch_config_changed)
        self.spn_lift.valueChanged.connect(self._on_watch_config_changed)
        self.cmb_srate.currentIndexChanged.connect(self._on_watch_config_changed)
        self.cmb_device.currentIndexChanged.connect(self._on_device_changed)
        self.cmb_position.currentTextChanged.connect(self._on_position_changed)
        self.btn_mock.toggled.connect(self._on_mock_toggled)

        # Timer enforcing automatic test completion at (stabilization_s + duration_s)
        self._session_timer = QTimer(self)
        self._session_timer.setInterval(100)
        self._session_timer.timeout.connect(self._check_session_duration)

        self.retranslate_ui()
        self._refresh_global_stats_table()
        logger.info(
            "MainWin initialized with default caliber='%s', bph=%s, lift_angle=%.1f",
            self.cmb_caliber.currentText(),
            self.cmb_bph.currentText(),
            self.spn_lift.value(),
        )

    def _apply_industrial_theme(self) -> None:
        """Apply dark industrial test-bench stylesheet with macOS QComboBox & QSpinBox fixes."""
        up_arrow = (ASSETS_DIR / "arrow_up.svg").as_posix()
        down_arrow = (ASSETS_DIR / "arrow_down.svg").as_posix()
        up_arrow_dis = (ASSETS_DIR / "arrow_up_disabled.svg").as_posix()
        down_arrow_dis = (ASSETS_DIR / "arrow_down_disabled.svg").as_posix()

        self.setStyleSheet(
            f"""
            QMainWindow, QWidget {{
                background-color: #0f1217;
                color: #d8dee9;
                font-family: "Inter", "Segoe UI", "DejaVu Sans", sans-serif;
                font-size: 12px;
            }}
            QFrame#TopBar, QFrame#ControlPanel, QFrame#PlotContainer {{
                background-color: #161b22;
                border: 1px solid #28303d;
                border-radius: 6px;
            }}
            QFrame#HudCard {{
                background-color: #11161d;
                border: 1px solid #2b3545;
                border-left: 4px solid #00e5ff;
                border-radius: 5px;
            }}
            QFrame#SubPanel {{
                background-color: #11161d;
                border: 1px solid #262f3d;
                border-radius: 5px;
            }}
            QLabel#HudCardTitle {{
                color: #8b96a8;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 1px;
            }}
            QLabel#HudCardUnit {{
                color: #6b778d;
                font-size: 11px;
                font-weight: 600;
            }}
            QLabel#SectionHeader {{
                color: #9aa5b4;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.8px;
            }}
            QLabel#TopBarLabel {{
                color: #8b96a8;
                font-size: 11px;
                font-weight: 600;
            }}
            QLabel#StatValue {{
                color: #eceff4;
                font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace;
                font-size: 12px;
                font-weight: 700;
            }}
            QLineEdit {{
                background-color: #0d1117;
                border: 1px solid #303947;
                border-radius: 4px;
                padding: 5px 8px;
                color: #e5e9f0;
                min-height: 24px;
            }}
            QComboBox {{
                background-color: #0d1117;
                border: 1px solid #303947;
                border-radius: 4px;
                padding: 4px 8px 4px 10px;
                color: #e5e9f0;
                min-height: 24px;
            }}
            QComboBox:hover {{
                border: 1px solid #48566a;
            }}
            QComboBox::drop-down {{
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 24px;
                background-color: #182230;
                border-left: 1px solid #303947;
                border-top-right-radius: 4px;
                border-bottom-right-radius: 4px;
            }}
            QComboBox::drop-down:hover {{
                background-color: #223044;
            }}
            QComboBox::down-arrow {{
                image: url("{down_arrow}");
                width: 10px;
                height: 10px;
            }}
            QComboBox::down-arrow:disabled {{
                image: url("{down_arrow_dis}");
            }}
            QComboBox QAbstractItemView {{
                background-color: #11161f;
                color: #e5e9f0;
                border: 1px solid #00e5ff;
                border-radius: 4px;
                selection-background-color: #1e354d;
                selection-color: #00e5ff;
                outline: 0px;
                padding: 4px;
            }}
            QComboBox QAbstractItemView::item {{
                min-height: 28px;
                padding: 5px 12px;
                border-radius: 3px;
                color: #e5e9f0;
                background-color: transparent;
            }}
            QComboBox QAbstractItemView::item:hover {{
                background-color: #1a2736;
                color: #00e5ff;
            }}
            QComboBox QAbstractItemView::item:selected {{
                background-color: #1e354d;
                color: #00e5ff;
                font-weight: 700;
            }}
            QSpinBox, QDoubleSpinBox {{
                background-color: #0d1117;
                border: 1px solid #303947;
                border-radius: 4px;
                padding: 4px 24px 4px 8px;
                color: #e5e9f0;
                min-height: 24px;
                min-width: 68px;
            }}
            QSpinBox:hover, QDoubleSpinBox:hover {{
                border: 1px solid #48566a;
            }}
            QSpinBox::up-button, QDoubleSpinBox::up-button {{
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 22px;
                background-color: #182230;
                border-left: 1px solid #303947;
                border-bottom: 1px solid #303947;
                border-top-right-radius: 4px;
            }}
            QSpinBox::down-button, QDoubleSpinBox::down-button {{
                subcontrol-origin: border;
                subcontrol-position: bottom right;
                width: 22px;
                background-color: #182230;
                border-left: 1px solid #303947;
                border-bottom-right-radius: 4px;
            }}
            QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
            QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
                background-color: #24344d;
            }}
            QSpinBox::up-button:pressed, QDoubleSpinBox::up-button:pressed,
            QSpinBox::down-button:pressed, QDoubleSpinBox::down-button:pressed {{
                background-color: #00e5ff;
            }}
            QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
                image: url("{up_arrow}");
                width: 10px;
                height: 10px;
            }}
            QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled {{
                image: url("{up_arrow_dis}");
            }}
            QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
                image: url("{down_arrow}");
                width: 10px;
                height: 10px;
            }}
            QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {{
                image: url("{down_arrow_dis}");
            }}
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
                border: 1px solid #00e5ff;
            }}
            QProgressBar {{
                background-color: #0b0e13;
                border: 1px solid #2c3545;
                border-radius: 4px;
                text-align: center;
                color: #e5e9f0;
                font-weight: bold;
                min-height: 16px;
            }}
            QProgressBar::chunk {{
                background-color: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #00b0ff, stop:1 #00e676
                );
                border-radius: 3px;
            }}
            QPushButton#ActionButton, QPushButton#CenterTraceButton {{
                background-color: #1c2430;
                color: #00e5ff;
                border: 1px solid #2f3e52;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 11px;
                font-weight: 700;
            }}
            QPushButton#ActionButton:hover, QPushButton#CenterTraceButton:hover {{
                background-color: #253142;
                border-color: #00e5ff;
            }}
            QPushButton#CollapsibleButton {{
                background-color: #151c25;
                color: #c8d1de;
                border: 1px solid #2b3545;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 11px;
                font-weight: 700;
                text-align: left;
            }}
            QPushButton#CollapsibleButton:hover {{
                border-color: #00e5ff;
                color: #ffffff;
            }}
            QPushButton#StartButton {{
                background-color: #00c853;
                color: #08120c;
                border: 1px solid #00e676;
                border-radius: 5px;
                padding: 9px 14px;
                font-size: 13px;
                font-weight: 800;
                letter-spacing: 1px;
            }}
            QPushButton#StartButton:hover {{
                background-color: #00e676;
            }}
            QPushButton#StartButton:disabled {{
                background-color: #1e3326;
                color: #556b5d;
                border-color: #294233;
            }}
            QPushButton#StopButton {{
                background-color: #d50000;
                color: #ffffff;
                border: 1px solid #ff1744;
                border-radius: 5px;
                padding: 9px 14px;
                font-size: 13px;
                font-weight: 800;
                letter-spacing: 1px;
            }}
            QPushButton#StopButton:hover {{
                background-color: #ff1744;
            }}
            QPushButton#StopButton:disabled {{
                background-color: #381a1e;
                color: #6e4f53;
                border-color: #4a2328;
            }}
            QPushButton#RecordButton {{
                background-color: #25161b;
                color: #ff5252;
                border: 1px solid #ff1744;
                border-radius: 5px;
                padding: 7px 12px;
                font-size: 12px;
                font-weight: 800;
                letter-spacing: 0.8px;
            }}
            QPushButton#RecordButton:hover {{
                background-color: #3b1b23;
                color: #ff8a80;
                border-color: #ff5252;
            }}
            QPushButton#CoffeeButton {{
                background-color: #2b2111;
                color: #ffab00;
                border: 1px solid #ffab00;
                border-radius: 5px;
                padding: 6px 12px;
                font-size: 11px;
                font-weight: 800;
            }}
            QPushButton#CoffeeButton:hover {{
                background-color: #3d2f18;
                color: #ffd740;
                border-color: #ffd740;
            }}
            QPushButton#MockButton {{
                background-color: #1c2430;
                color: #8b96a8;
                border: 1px solid #2f3e52;
                border-radius: 5px;
                padding: 6px 12px;
                font-size: 11px;
                font-weight: 800;
                letter-spacing: 1px;
            }}
            QPushButton#MockButton:hover {{
                border-color: #ff9e40;
            }}
            QPushButton#MockButton:checked {{
                background-color: #ff6d00;
                color: #120800;
                border-color: #ff9e40;
            }}
            QLabel#MockBadge {{
                background-color: #ff6d00;
                color: #120800;
                font-weight: 800;
                padding: 1px 8px;
                border-radius: 3px;
            }}
            QTableWidget {{
                background-color: #0d1117;
                border: 1px solid #262f3d;
                gridline-color: #1f2631;
                color: #d8dee9;
                font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace;
                font-size: 11px;
            }}
            QHeaderView::section {{
                background-color: #161b22;
                color: #8b96a8;
                border: 1px solid #262f3d;
                padding: 3px 4px;
                font-size: 10px;
                font-weight: 700;
            }}
            QSplitter::handle {{
                background-color: #1f2631;
            }}
            QSplitter::handle:horizontal {{
                width: 4px;
            }}
            QSplitter::handle:vertical {{
                height: 4px;
            }}
            """
        )

    def _configure_combo_box(self, combo: QComboBox, min_width: int = 90) -> None:
        """Configure a QComboBox with a QListView popup and sufficient item space on macOS."""
        list_view = QListView(combo)
        list_view.setSpacing(2)
        list_view.setUniformItemSizes(True)
        combo.setView(list_view)
        combo.setItemDelegate(ComboPopupItemDelegate(combo))
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        combo.setMinimumWidth(min_width)
        self._sync_combo_popup_width(combo, min_width)

    @staticmethod
    def _optimize_high_freq_curve(curve: pg.PlotDataItem) -> None:
        """Dense, often-redrawn curve: view clipping, peak-preserving auto-downsampling and no
        antialiasing (with 1-px pens). Antialiased pens wider than 1 px on ~1500-point curves
        made Qt's ``drawPath`` take most of the GUI thread (visible stutters)."""
        curve.setClipToView(True)
        curve.setDownsampling(auto=True, method="peak")
        curve.opts["antialias"] = False

    @staticmethod
    def _sync_combo_popup_width(combo: QComboBox, min_width: int = 90) -> None:
        """Ensure both the combo box and its dropdown popup fit the longest option text."""
        fm = combo.fontMetrics()
        max_text_w = max(
            (fm.horizontalAdvance(combo.itemText(i)) for i in range(combo.count())),
            default=min_width,
        )
        target_w = max(min_width, max_text_w + 52)
        combo.setMinimumWidth(target_w)
        if combo.view() is not None:
            combo.view().setMinimumWidth(target_w + 16)

    def _build_ui(self) -> None:
        """Construct the Top Bar, Left Control Panel, and Transposed Graph Panels."""
        central = QWidget(self)
        self.setCentralWidget(central)

        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(8)

        # 1. Top Configuration Bar
        self.top_bar = QFrame()
        self.top_bar.setObjectName("TopBar")
        top_layout = QHBoxLayout(self.top_bar)
        top_layout.setContentsMargins(12, 8, 12, 8)
        top_layout.setSpacing(10)

        # Test Name (QLineEdit)
        self.lbl_test_name = QLabel()
        self.lbl_test_name.setObjectName("TopBarLabel")
        self.txt_test_name = QLineEdit()
        self.txt_test_name.setMinimumWidth(150)
        self.txt_test_name.setText("Caliber-Test-01")

        # Caliber Preset
        self.lbl_caliber = QLabel()
        self.lbl_caliber.setObjectName("TopBarLabel")
        self.cmb_caliber = QComboBox()
        self.cmb_caliber.addItems(list(CALIBER_PRESETS.keys()))
        self._configure_combo_box(self.cmb_caliber, min_width=185)
        self.cmb_caliber.setCurrentText("Custom")
        self.cmb_caliber.currentTextChanged.connect(self._on_caliber_changed)

        # BPH
        self.lbl_bph = QLabel()
        self.lbl_bph.setObjectName("TopBarLabel")
        self.cmb_bph = QComboBox()
        self._last_mechanical_bph = 21600
        for bph in MECHANICAL_BPH:
            self.cmb_bph.addItem(str(bph), bph)
        self._configure_combo_box(self.cmb_bph, min_width=95)
        self.cmb_bph.setCurrentText("21600")

        # Quartz mode: steps per second instead of mechanical BPH, rate only
        self.lbl_quartz = QLabel()
        self.lbl_quartz.setObjectName("TopBarLabel")
        self.chk_quartz = QCheckBox()
        self.chk_quartz.setObjectName("QuartzCheck")
        self.chk_quartz.toggled.connect(self._on_quartz_toggled)

        # Lift Angle
        self.lbl_lift = QLabel()
        self.lbl_lift.setObjectName("TopBarLabel")
        self.spn_lift = QDoubleSpinBox()
        self.spn_lift.setRange(30.0, 70.0)
        self.spn_lift.setSingleStep(0.5)
        self.spn_lift.setDecimals(1)
        self.spn_lift.setSuffix("°")
        self.spn_lift.setValue(52.0)

        # Position
        self.lbl_position = QLabel()
        self.lbl_position.setObjectName("TopBarLabel")
        self.cmb_position = QComboBox()
        self.cmb_position.addItems(["DU", "DD", "CU", "CD", "CL", "CR"])
        self._configure_combo_box(self.cmb_position, min_width=78)

        # IN Device
        self.lbl_device = QLabel()
        self.lbl_device.setObjectName("TopBarLabel")
        self.cmb_device = QComboBox()
        self._populate_audio_devices()
        self._configure_combo_box(self.cmb_device, min_width=230)

        # Sample Rate (default 48 kHz)
        self.lbl_srate = QLabel()
        self.lbl_srate.setObjectName("TopBarLabel")
        self.cmb_srate = QComboBox()
        self.cmb_srate.addItem("48 kHz", 48000)
        self.cmb_srate.addItem("96 kHz", 96000)
        self._configure_combo_box(self.cmb_srate, min_width=96)
        self.cmb_srate.setCurrentIndex(0)

        # Stabilization time (fixed choices)
        self.lbl_stab = QLabel()
        self.lbl_stab.setObjectName("TopBarLabel")
        self.cmb_stab = QComboBox()
        for secs in STABILIZATION_CHOICES_S:
            self.cmb_stab.addItem(_format_duration(secs), float(secs))
        self._configure_combo_box(self.cmb_stab, min_width=78)
        self.cmb_stab.setCurrentIndex(STABILIZATION_CHOICES_S.index(DEFAULT_STABILIZATION_S))

        # Test duration (fixed choices)
        self.lbl_duration = QLabel()
        self.lbl_duration.setObjectName("TopBarLabel")
        self.cmb_duration = QComboBox()
        for secs in DURATION_CHOICES_S:
            self.cmb_duration.addItem(_format_duration(secs), float(secs))
        self._configure_combo_box(self.cmb_duration, min_width=90)
        self.cmb_duration.setCurrentIndex(DURATION_CHOICES_S.index(DEFAULT_DURATION_S))

        # Language selector (EN / ES)
        self.lbl_lang = QLabel()
        self.lbl_lang.setObjectName("TopBarLabel")
        self.cmb_lang = QComboBox()
        self.cmb_lang.addItem("EN", "en")
        self.cmb_lang.addItem("ES", "es")
        self._configure_combo_box(self.cmb_lang, min_width=74)
        idx_lang = 0 if self.translator.locale == "en" else 1
        self.cmb_lang.setCurrentIndex(idx_lang)
        self.cmb_lang.currentIndexChanged.connect(self._on_language_changed)

        for lbl, widget in (
            (self.lbl_test_name, self.txt_test_name),
            (self.lbl_caliber, self.cmb_caliber),
            (self.lbl_bph, self.cmb_bph),
            (self.lbl_quartz, self.chk_quartz),
            (self.lbl_lift, self.spn_lift),
            (self.lbl_position, self.cmb_position),
            (self.lbl_device, self.cmb_device),
            (self.lbl_srate, self.cmb_srate),
            (self.lbl_stab, self.cmb_stab),
            (self.lbl_duration, self.cmb_duration),
            (self.lbl_lang, self.cmb_lang),
        ):
            pair = QVBoxLayout()
            pair.setSpacing(2)
            pair.addWidget(lbl)
            pair.addWidget(widget)
            top_layout.addLayout(pair)

        top_layout.addStretch()

        # MOCK toggle: simulated engine to test the whole GUI (kept in sync with cmb_device)
        self.btn_mock = QPushButton("MOCK")
        self.btn_mock.setObjectName("MockButton")
        self.btn_mock.setCheckable(True)
        self.btn_mock.setCursor(Qt.CursorShape.PointingHandCursor)
        top_layout.addWidget(self.btn_mock, alignment=Qt.AlignmentFlag.AlignVCenter)

        # "About" (version, licence, folders for bug reports)
        self.btn_about = QPushButton()
        self.btn_about.setObjectName("MockButton")
        self.btn_about.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_about.clicked.connect(lambda: self.open_about_dialog())
        top_layout.addWidget(self.btn_about, alignment=Qt.AlignmentFlag.AlignVCenter)

        # "Report" (zip with logs, system, settings and the last recording -> GitHub Issues)
        self.btn_report = QPushButton()
        self.btn_report.setObjectName("MockButton")
        self.btn_report.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_report.clicked.connect(lambda: self.open_report_dialog())
        top_layout.addWidget(self.btn_report, alignment=Qt.AlignmentFlag.AlignVCenter)

        # "Buy me a coffee" button in the top-right corner (opens BUY_ME_A_COFFEE_URL)
        self.btn_buy_coffee = QPushButton("☕ Buy me a coffee")
        self.btn_buy_coffee.setObjectName("CoffeeButton")
        self.btn_buy_coffee.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_buy_coffee.clicked.connect(self._on_buy_coffee_clicked)
        top_layout.addWidget(self.btn_buy_coffee, alignment=Qt.AlignmentFlag.AlignVCenter)

        root_layout.addWidget(self.top_bar)

        # 2. Main Content Area (Left Control Panel + Right Transposed Graphs Area)
        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)

        # --- Left Column: Control Panel & HUD ---
        self.left_panel = QFrame()
        self.left_panel.setObjectName("ControlPanel")
        self.left_panel.setMinimumWidth(300)
        self.left_panel.setMaximumWidth(390)
        left_layout = QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(8)

        self.card_rate = HudCard("RATE", "s/d", accent_color="#00e5ff")
        self.card_beat_error = HudCard("BEAT ERROR", "ms", accent_color="#00e676")
        self.card_amplitude = HudCard("AMPLITUDE", "deg (°)", accent_color="#ffab00")

        left_layout.addWidget(self.card_rate)
        left_layout.addWidget(self.card_beat_error)
        left_layout.addWidget(self.card_amplitude)

        # Signal Quality / SNR Box
        self.snr_panel = QFrame()
        self.snr_panel.setObjectName("SubPanel")
        snr_layout = QVBoxLayout(self.snr_panel)
        snr_layout.setContentsMargins(10, 6, 10, 6)
        snr_layout.setSpacing(4)

        snr_header = QHBoxLayout()
        self.lbl_signal_quality = QLabel()
        self.lbl_signal_quality.setObjectName("SectionHeader")
        self.lbl_snr_value = QLabel("-- dB")
        self.lbl_snr_value.setObjectName("StatValue")
        snr_header.addWidget(self.lbl_signal_quality)
        snr_header.addStretch()
        snr_header.addWidget(self.lbl_snr_value)

        self.prg_snr = QProgressBar()
        self.prg_snr.setRange(0, 100)
        self.prg_snr.setValue(0)

        snr_layout.addLayout(snr_header)
        snr_layout.addWidget(self.prg_snr)
        left_layout.addWidget(self.snr_panel)

        # Statistical Panel (Max, Min, Delta, Mean, Elapsed Time)
        self.stats_panel = QFrame()
        self.stats_panel.setObjectName("SubPanel")
        stats_layout = QVBoxLayout(self.stats_panel)
        stats_layout.setContentsMargins(10, 6, 10, 6)
        stats_layout.setSpacing(4)

        self.lbl_stats_title = QLabel()
        self.lbl_stats_title.setObjectName("SectionHeader")
        stats_layout.addWidget(self.lbl_stats_title)

        stats_grid = QGridLayout()
        stats_grid.setHorizontalSpacing(8)
        stats_grid.setVerticalSpacing(3)

        self.lbl_stat_max_key = QLabel()
        self.lbl_stat_max_val = QLabel("")
        self.lbl_stat_max_val.setObjectName("StatValue")
        self.lbl_stat_max_val.setAlignment(Qt.AlignmentFlag.AlignRight)

        self.lbl_stat_min_key = QLabel()
        self.lbl_stat_min_val = QLabel("")
        self.lbl_stat_min_val.setObjectName("StatValue")
        self.lbl_stat_min_val.setAlignment(Qt.AlignmentFlag.AlignRight)

        self.lbl_stat_delta_key = QLabel()
        self.lbl_stat_delta_val = QLabel("")
        self.lbl_stat_delta_val.setObjectName("StatValue")
        self.lbl_stat_delta_val.setAlignment(Qt.AlignmentFlag.AlignRight)

        self.lbl_stat_mean_key = QLabel()
        self.lbl_stat_mean_val = QLabel("")
        self.lbl_stat_mean_val.setObjectName("StatValue")
        self.lbl_stat_mean_val.setAlignment(Qt.AlignmentFlag.AlignRight)

        self.lbl_stat_elapsed_key = QLabel()
        self.lbl_stat_elapsed_val = QLabel("")
        self.lbl_stat_elapsed_val.setObjectName("StatValue")
        self.lbl_stat_elapsed_val.setAlignment(Qt.AlignmentFlag.AlignRight)

        self.lbl_stat_drift_key = QLabel()
        self.lbl_stat_drift_val = QLabel("")
        self.lbl_stat_drift_val.setObjectName("StatValue")
        self.lbl_stat_drift_val.setAlignment(Qt.AlignmentFlag.AlignRight)

        for row_idx, (k_lbl, v_lbl) in enumerate(
            (
                (self.lbl_stat_max_key, self.lbl_stat_max_val),
                (self.lbl_stat_min_key, self.lbl_stat_min_val),
                (self.lbl_stat_delta_key, self.lbl_stat_delta_val),
                (self.lbl_stat_mean_key, self.lbl_stat_mean_val),
                (self.lbl_stat_elapsed_key, self.lbl_stat_elapsed_val),
                (self.lbl_stat_drift_key, self.lbl_stat_drift_val),
            )
        ):
            stats_grid.addWidget(k_lbl, row_idx, 0)
            stats_grid.addWidget(v_lbl, row_idx, 1)

        stats_layout.addLayout(stats_grid)
        self.btn_calibrate = QPushButton()
        self.btn_calibrate.setObjectName("ActionButton")
        self.btn_calibrate.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_calibrate.clicked.connect(lambda: self.open_calibration_dialog())
        stats_layout.addWidget(self.btn_calibrate)
        left_layout.addWidget(self.stats_panel)

        # Positional Test Management & CSV Export: "Guardar Posición", "Guardar CSV" & Collapsible "Estadísticas Globales"
        pos_csv_layout = QHBoxLayout()
        pos_csv_layout.setSpacing(6)
        self.btn_save_position = QPushButton()
        self.btn_save_position.setObjectName("ActionButton")
        self.btn_save_position.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save_position.clicked.connect(self.save_current_position)

        self.btn_save_csv = QPushButton()
        self.btn_save_csv.setObjectName("ActionButton")
        self.btn_save_csv.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save_csv.clicked.connect(lambda: self.save_results_csv())

        pos_csv_layout.addWidget(self.btn_save_position)
        pos_csv_layout.addWidget(self.btn_save_csv)
        left_layout.addLayout(pos_csv_layout)

        self.btn_global_stats = QPushButton()
        self.btn_global_stats.setObjectName("CollapsibleButton")
        self.btn_global_stats.setCheckable(True)
        self.btn_global_stats.setChecked(True)
        self.btn_global_stats.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_global_stats.toggled.connect(self._on_toggle_global_stats)
        left_layout.addWidget(self.btn_global_stats)

        self.global_stats_container = QFrame()
        self.global_stats_container.setObjectName("SubPanel")
        gstats_layout = QVBoxLayout(self.global_stats_container)
        gstats_layout.setContentsMargins(8, 6, 8, 6)
        gstats_layout.setSpacing(4)

        self.tbl_global_stats = QTableWidget(0, 4)
        self.tbl_global_stats.setMinimumHeight(95)
        self.tbl_global_stats.setMaximumHeight(130)
        self.tbl_global_stats.verticalHeader().setVisible(False)
        self.tbl_global_stats.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tbl_global_stats.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        header = self.tbl_global_stats.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        gstats_layout.addWidget(self.tbl_global_stats)

        summary_grid = QGridLayout()
        summary_grid.setHorizontalSpacing(8)
        summary_grid.setVerticalSpacing(2)
        self.lbl_global_mean_key = QLabel()
        self.lbl_global_mean_val = QLabel("")
        self.lbl_global_mean_val.setObjectName("StatValue")
        self.lbl_global_mean_val.setAlignment(Qt.AlignmentFlag.AlignRight)

        self.lbl_global_delta_key = QLabel()
        self.lbl_global_delta_val = QLabel("")
        self.lbl_global_delta_val.setObjectName("StatValue")
        self.lbl_global_delta_val.setAlignment(Qt.AlignmentFlag.AlignRight)

        summary_grid.addWidget(self.lbl_global_mean_key, 0, 0)
        summary_grid.addWidget(self.lbl_global_mean_val, 0, 1)
        summary_grid.addWidget(self.lbl_global_delta_key, 1, 0)
        summary_grid.addWidget(self.lbl_global_delta_val, 1, 1)
        gstats_layout.addLayout(summary_grid)

        left_layout.addWidget(self.global_stats_container)

        # Button right beneath the Global Statistics panel to export only global stats & positional delta
        self.btn_export_test_results = QPushButton()
        self.btn_export_test_results.setObjectName("ActionButton")
        self.btn_export_test_results.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_export_test_results.clicked.connect(lambda: self.export_test_results())
        left_layout.addWidget(self.btn_export_test_results)

        left_layout.addStretch()

        # Audio Recording Button (same duration as test, default FLAC or Vorbis)
        self.btn_record = QPushButton()
        self.btn_record.setObjectName("RecordButton")
        self.btn_record.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_record.clicked.connect(lambda: self.record_audio())
        self.cmb_duration.currentIndexChanged.connect(lambda _i: self._refresh_record_button())
        left_layout.addWidget(self.btn_record)

        # Start / Stop Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        self.btn_start = QPushButton()
        self.btn_start.setObjectName("StartButton")
        self.btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start.clicked.connect(self.start_capture)

        self.btn_stop = QPushButton()
        self.btn_stop.setObjectName("StopButton")
        self.btn_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_capture)

        btn_layout.addWidget(self.btn_start)
        btn_layout.addWidget(self.btn_stop)
        left_layout.addLayout(btn_layout)

        # --- Right Graphs Area: Vertical Splitter (Top: Paper Tape, Bottom: Oscilloscope + Audio Waveform) ---
        self.graphs_splitter = QSplitter(Qt.Orientation.Vertical)
        self.graphs_splitter.setChildrenCollapsible(False)

        # 2A. Top Graph Panel: Paper Tape Trace (Horizontal Time on X, Deviation/Rate on Y)
        self.center_container = QFrame()
        self.center_container.setObjectName("PlotContainer")
        center_layout = QVBoxLayout(self.center_container)
        center_layout.setContentsMargins(8, 8, 8, 8)
        center_layout.setSpacing(6)

        self.paper_tape_plot = pg.PlotWidget()
        self.paper_tape_plot.showGrid(x=True, y=True, alpha=0.25)
        self.paper_tape_plot.setXRange(0.0, 30.0)
        self.paper_tape_plot.setYRange(-TAPE_MIN_HALF_SPAN_MS, TAPE_MIN_HALF_SPAN_MS)
        self.paper_tape_plot.getViewBox().sigRangeChangedManually.connect(
            self._on_paper_tape_manual_range
        )

        # Zero-deviation horizontal reference line
        self.tape_zero_line = pg.InfiniteLine(
            pos=0.0,
            angle=0,
            pen=pg.mkPen(color="#3b4659", width=1.2, style=Qt.PenStyle.DashLine),
        )
        self.paper_tape_plot.addItem(self.tape_zero_line)

        # COSC limits (-4 / +6 s/d) as slopes from the origin: on a paper tape the rate is the
        # slope of the rows (1 s/d = 1/86.4 ms per second); see _update_cosc_slopes
        cosc_pen = pg.mkPen(color=(0, 230, 118, 135), width=1.0, style=Qt.PenStyle.DashLine)
        self.cosc_lower_line = self.paper_tape_plot.plot([], [], pen=cosc_pen)
        self.cosc_upper_line = self.paper_tape_plot.plot([], [], pen=cosc_pen)
        self.cosc_lower_label = pg.TextItem("-4 s/d", color=(0, 230, 118, 220), anchor=(1, 0))
        self.cosc_upper_label = pg.TextItem("+6 s/d", color=(0, 230, 118, 220), anchor=(1, 1))
        self.paper_tape_plot.addItem(self.cosc_lower_label)
        self.paper_tape_plot.addItem(self.cosc_upper_label)
        self._update_cosc_slopes(30.0)

        # Paper tape: one point per beat, tic (even k) and tac (odd k) rows timed at the drop
        # (P3, the point of t_fine): their distance is the tic/tac offset at P3, which differs
        # from the beat error card (measured at P1)
        self.paper_tape_plot.addLegend(offset=(10, 10))
        self.tape_curve_even = self.paper_tape_plot.plot(
            [],
            [],
            pen=pg.mkPen(color="#00e5ff", width=1.0),
            symbol="o",
            symbolSize=5,
            symbolBrush="#00e5ff",
            symbolPen=None,
            name="Tic (P3)",
        )
        self.tape_curve_odd = self.paper_tape_plot.plot(
            [],
            [],
            pen=pg.mkPen(color="#ffab00", width=1.0),
            symbol="o",
            symbolSize=5,
            symbolBrush="#ffab00",
            symbolPen=None,
            name="Tac (P3)",
        )
        # Long tests (1 h = 14 400 points per curve): 1-px pens, peak downsampling and view
        # clipping; symbols only while the trace is short (_update_tape_symbols). With symbols
        # and 1.8-px antialiased pens one redraw took 1.8 s at 1 h; now ~6 ms.
        for curve in (self.tape_curve_even, self.tape_curve_odd):
            curve.setDownsampling(auto=True, method="peak")
            curve.setClipToView(True)
        self._tape_symbols = True
        center_layout.addWidget(self.paper_tape_plot, stretch=1)

        # Footer bar right beneath Paper Tape Trace with "Center Trace", "Export Traces", and "Print Traces"
        tape_footer_layout = QHBoxLayout()
        tape_footer_layout.setContentsMargins(2, 0, 2, 0)
        tape_footer_layout.setSpacing(6)
        tape_footer_layout.addStretch()

        self.btn_export_traces = QPushButton()
        self.btn_export_traces.setObjectName("CenterTraceButton")
        self.btn_export_traces.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_export_traces.clicked.connect(lambda: self.export_traces())
        tape_footer_layout.addWidget(self.btn_export_traces)

        self.btn_print_traces = QPushButton()
        self.btn_print_traces.setObjectName("CenterTraceButton")
        self.btn_print_traces.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_print_traces.clicked.connect(lambda: self.print_traces())
        tape_footer_layout.addWidget(self.btn_print_traces)

        self.btn_center_trace = QPushButton()
        self.btn_center_trace.setObjectName("CenterTraceButton")
        self.btn_center_trace.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_center_trace.clicked.connect(self.center_paper_tape_trace)
        tape_footer_layout.addWidget(self.btn_center_trace)
        center_layout.addLayout(tape_footer_layout)

        # 2B. Bottom Graphs Row: Horizontal Splitter with (1) Oscilloscope and (2) Audio Waveform
        self.bottom_graphs_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.bottom_graphs_splitter.setChildrenCollapsible(False)

        # Bottom-Left: Real-Time Escapement Oscilloscope (P1 / P2 / P3)
        self.right_container = QFrame()
        self.right_container.setObjectName("PlotContainer")
        right_layout = QVBoxLayout(self.right_container)
        right_layout.setContentsMargins(8, 8, 8, 8)

        self.oscilloscope_plot = pg.PlotWidget()
        self.oscilloscope_plot.showGrid(x=True, y=True, alpha=0.25)
        self.oscilloscope_plot.setYRange(-1.1, 1.1)
        self.oscilloscope_plot.addLegend(offset=(10, 10))

        self.osc_wave_curve = self.oscilloscope_plot.plot(
            [],
            [],
            pen=pg.mkPen(color="#00e5ff", width=1.0),
            clipToView=True,
            autoDownsample=True,
            downsampleMethod="peak",
        )
        self._optimize_high_freq_curve(self.osc_wave_curve)

        self.osc_envelope_curve = self.oscilloscope_plot.plot(
            [],
            [],
            pen=pg.mkPen(color=(255, 171, 0, 180), width=1.0),
            clipToView=True,
            autoDownsample=True,
            downsampleMethod="peak",
        )
        self._optimize_high_freq_curve(self.osc_envelope_curve)

        # Vertical markers for P1 (Unlock), P2 (Impulse), P3 (Drop)
        self.line_p1 = pg.InfiniteLine(
            pos=0.0,
            angle=90,
            pen=pg.mkPen(color="#00e676", width=1.8, style=Qt.PenStyle.DashLine),
            label="P1",
            labelOpts={"position": 0.9, "color": "#00e676"},
        )
        self.line_p2 = pg.InfiniteLine(
            pos=0.0,
            angle=90,
            pen=pg.mkPen(color="#ffab00", width=1.8, style=Qt.PenStyle.DashLine),
            label="P2",
            labelOpts={"position": 0.82, "color": "#ffab00"},
        )
        self.line_p3 = pg.InfiniteLine(
            pos=0.0,
            angle=90,
            pen=pg.mkPen(color="#ff1744", width=1.8, style=Qt.PenStyle.DashLine),
            label="P3",
            labelOpts={"position": 0.9, "color": "#ff1744"},
        )
        self.oscilloscope_plot.addItem(self.line_p1)
        self.oscilloscope_plot.addItem(self.line_p2)
        self.oscilloscope_plot.addItem(self.line_p3)

        self.osc_impulse_markers = pg.ScatterPlotItem(size=10)
        self.oscilloscope_plot.addItem(self.osc_impulse_markers)

        right_layout.addWidget(self.oscilloscope_plot)

        # Bottom-Right: Real-Time Audio Waveform Representation
        self.waveform_container = QFrame()
        self.waveform_container.setObjectName("PlotContainer")
        waveform_layout = QVBoxLayout(self.waveform_container)
        waveform_layout.setContentsMargins(8, 8, 8, 8)

        self.waveform_plot = pg.PlotWidget()
        self.waveform_plot.showGrid(x=True, y=True, alpha=0.25)
        self.waveform_plot.setYRange(-1.1, 1.1)

        self.waveform_zero_line = pg.InfiniteLine(
            pos=0.0,
            angle=0,
            pen=pg.mkPen(color="#2e3846", width=1.0, style=Qt.PenStyle.DashLine),
        )
        self.waveform_plot.addItem(self.waveform_zero_line)

        self.audio_waveform_curve = self.waveform_plot.plot(
            [],
            [],
            pen=pg.mkPen(color="#00e676", width=1.0),
            clipToView=True,
            autoDownsample=True,
            downsampleMethod="peak",
        )
        self._optimize_high_freq_curve(self.audio_waveform_curve)
        waveform_layout.addWidget(self.waveform_plot)

        self.bottom_graphs_splitter.addWidget(self.right_container)
        self.bottom_graphs_splitter.addWidget(self.waveform_container)
        self.bottom_graphs_splitter.setStretchFactor(0, 1)
        self.bottom_graphs_splitter.setStretchFactor(1, 1)
        self.bottom_graphs_splitter.setSizes([515, 515])

        # Stack Paper Tape on top and (Oscilloscope + Audio Waveform) on bottom
        self.graphs_splitter.addWidget(self.center_container)
        self.graphs_splitter.addWidget(self.bottom_graphs_splitter)
        self.graphs_splitter.setStretchFactor(0, 1)
        self.graphs_splitter.setStretchFactor(1, 1)
        self.graphs_splitter.setSizes([380, 380])

        # Add Left Panel and Right Transposed Graphs Splitter to the main splitter
        self.splitter.addWidget(self.left_panel)
        self.splitter.addWidget(self.graphs_splitter)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([330, 1030])

        content_layout.addWidget(self.splitter)
        root_layout.addLayout(content_layout, stretch=1)

        # Status bar: MOCK badge (only visible while simulated data is shown) + session state
        self.lbl_mock_badge = QLabel()
        self.lbl_mock_badge.setObjectName("MockBadge")
        self.lbl_mock_badge.setVisible(False)
        self.statusBar().addWidget(self.lbl_mock_badge)
        self.lbl_session_status = QLabel()
        self.lbl_session_status.setObjectName("StatValue")
        self.statusBar().addPermanentWidget(self.lbl_session_status)

        # Status bar with live clock drift telemetry indicator
        self.lbl_drift_status = QLabel()
        self.lbl_drift_status.setObjectName("StatValue")
        self.statusBar().addPermanentWidget(self.lbl_drift_status)

        # Live capture health: share of the audio that actually reached the analysis
        self._last_capture_pct: float = 100.0
        self._last_capture_overflows: int = 0
        self.lbl_capture_health = QLabel()
        self.lbl_capture_health.setObjectName("StatValue")
        self.statusBar().addPermanentWidget(self.lbl_capture_health)

        self._build_menu_bar()

    def _build_menu_bar(self) -> None:
        """Construct standard multiplatform menu bar with Settings, Folders and Help."""
        mb = self.menuBar()
        mb.clear()

        # Tools Menu
        self.menu_tools = mb.addMenu(self.translator.tr("menu_tools"))
        self.action_open_logs = QAction(self)
        self.action_open_logs.triggered.connect(self.open_logs_folder)
        self.menu_tools.addAction(self.action_open_logs)

        self.action_open_calibration = QAction(self)
        self.action_open_calibration.triggered.connect(self.open_calibration_folder)
        self.menu_tools.addAction(self.action_open_calibration)

        # Help Menu
        self.menu_help = mb.addMenu(self.translator.tr("menu_help"))
        self.action_report = QAction(self)
        self.action_report.triggered.connect(lambda: self.open_report_dialog())
        self.menu_help.addAction(self.action_report)

        self.action_about = QAction(self)
        self.action_about.triggered.connect(lambda: self.open_about_dialog())
        self.menu_help.addAction(self.action_about)

    def _populate_audio_devices(self) -> list[AudioDeviceInfo]:
        """Populate input audio device selector dynamically via ``list_input_devices()``.

        On the first population the system's default input is selected (else the first
        hardware device); the simulated MOCK source only when there is no hardware.
        """
        first = self.cmb_device.count() == 0
        prev_data = None if first else self.cmb_device.currentData()
        devices = list_input_devices()
        hardware = [d for d in devices if not (d.is_synthetic or d.device_id is None)]
        if first and hardware:
            wanted = default_input_device_id()
            ids = [int(d.device_id) for d in hardware]
            prev_data = wanted if wanted in ids else ids[0]

        was_blocked = self.cmb_device.blockSignals(True)
        try:
            self.cmb_device.clear()
            restore_index = 0
            for idx, dev in enumerate(devices):
                if dev.is_synthetic or dev.device_id is None:
                    self.cmb_device.addItem(self.translator.tr("synthetic_device"), None)
                else:
                    label = f"[{dev.device_id}] {dev.name}"
                    self.cmb_device.addItem(label, int(dev.device_id))
                    if (
                        prev_data is not None
                        and prev_data != "synthetic"
                        and int(prev_data) == int(dev.device_id)
                    ):
                        restore_index = idx
            self.cmb_device.setCurrentIndex(restore_index)
        finally:
            self.cmb_device.blockSignals(was_blocked)

        self._sync_combo_popup_width(self.cmb_device, min_width=230)
        logger.info(
            "Populated audio input devices: %d total (1 synthetic + %d hardware)",
            len(devices),
            max(0, len(devices) - 1),
        )
        return devices

    def refresh_audio_devices(self) -> list[AudioDeviceInfo]:
        """Re-query available audio input devices and sync selection with the engine."""
        devices = self._populate_audio_devices()
        if hasattr(self, "dsp_worker") and self.dsp_worker is not None:
            self._apply_device_selection()
        return devices

    def selected_device_id(self) -> int | None:
        """Return the currently selected hardware ``device_id`` or ``None`` for synthetic mode."""
        data = self.cmb_device.currentData()
        if data is None or data == "synthetic" or data == "":
            return None
        try:
            return int(data)
        except (TypeError, ValueError):
            return None

    def _on_device_changed(self, _index: int) -> None:
        """Handle user changes to the IN Device selector and update the engine."""
        device_id = self.selected_device_id()
        _dbg.state_change(
            "IN device", self._active_device_id, device_id, label=self.cmb_device.currentText()
        )
        self._apply_device_selection()

    def is_mock_active(self) -> bool:
        """Return True while the simulated (MOCK) engine is selected."""
        return self.dsp_worker is not None and bool(self.dsp_worker.synthetic_mode)

    def _apply_device_selection(self) -> None:
        """Sync the MOCK button with ``cmb_device`` and install the matching engine."""
        device_id = self.selected_device_id()
        is_mock = device_id is None
        if not is_mock:
            self._last_hw_device_index = self.cmb_device.currentIndex()
        if self.btn_mock.isChecked() != is_mock:
            was_blocked = self.btn_mock.blockSignals(True)
            self.btn_mock.setChecked(is_mock)
            self.btn_mock.blockSignals(was_blocked)
        self._install_worker(device_id)

    def _on_mock_toggled(self, checked: bool) -> None:
        """MOCK on: select the synthetic device. MOCK off: go back to the previous device."""
        _dbg.state_change("MOCK", "ON" if not checked else "OFF", "ON" if checked else "OFF")
        if checked:
            target = 0
        else:
            target = self._last_hw_device_index
            if target is None or not (0 < target < self.cmb_device.count()):
                target = 1 if self.cmb_device.count() > 1 else None
            if target is None:
                # No hardware device to fall back to: MOCK stays on.
                was_blocked = self.btn_mock.blockSignals(True)
                self.btn_mock.setChecked(True)
                self.btn_mock.blockSignals(was_blocked)
                self.statusBar().showMessage(self.translator.tr("mock_no_hardware"), 6000)
                logger.warning("MOCK cannot be disabled: no hardware input device available")
                return
        if self.cmb_device.currentIndex() != target:
            was_blocked = self.cmb_device.blockSignals(True)
            self.cmb_device.setCurrentIndex(target)
            self.cmb_device.blockSignals(was_blocked)
        self._apply_device_selection()

    def _install_worker(self, device_id: int | None) -> None:
        """Create (via ``create_worker``) and wire the engine for ``device_id`` if it changes type."""
        want_mock = device_id is None
        old = self.dsp_worker
        if old is not None and bool(old.synthetic_mode) == want_mock:
            # Changing the hardware device during a session stops it
            if old.isRunning() and device_id != self._active_device_id:
                logger.info("Device change requested during a session; stopping capture first")
                self.stop_capture()
            old.set_input_device(device_id)
            self._active_device_id = device_id
            return
        if old is not None:
            if old.isRunning():
                logger.info("Engine change requested during a session; stopping capture first")
                self.stop_capture()
            old.shutdown()
            _dbg.process_end(f"engine {type(old).__name__}", device_id=self._active_device_id)
            if isinstance(old, MockStreamWorker):
                old.drift_detail.disconnect()
            for sig in (
                old.telemetry_ready,
                old.waveform_ready,
                old.drift_updated,
                old.clock_info,
                old.capture_health,
                old.level_updated,
                old.error_occurred,
                old.session_finished,
            ):
                sig.disconnect()
            old.deleteLater()

        self._active_watch_config = self.current_watch_config()
        worker = create_worker(device_id, config=self._active_watch_config, parent=self)
        worker.telemetry_ready.connect(self._on_telemetry_ready)
        worker.waveform_ready.connect(self._on_waveform_ready)
        worker.drift_updated.connect(self._on_drift_updated)
        worker.clock_info.connect(self._on_clock_info)
        worker.capture_health.connect(self._on_capture_health)
        worker.level_updated.connect(self._on_level_updated)
        worker.error_occurred.connect(self._on_worker_error)
        worker.session_finished.connect(self._on_session_finished)
        # MOCK-only extra (outside BaseStreamWorker): drift sigma / confidence
        self._last_drift_sigma_ppm = None
        self._last_drift_confidence = None
        self._last_clock_level = None
        if isinstance(worker, MockStreamWorker):
            worker.drift_detail.connect(self._on_mock_drift_detail)
        self.dsp_worker = worker
        self._active_device_id = device_id
        _dbg.process_start(
            f"engine {type(worker).__name__}",
            device_id=device_id,
            simulated=bool(worker.synthetic_mode),
        )
        self._update_mock_indicators()

    def _update_mock_indicators(self) -> None:
        """Show the MOCK badge (and title suffix) only while simulated data is in use."""
        active = self.is_mock_active()
        self.lbl_mock_badge.setVisible(active)
        title = self.translator.tr("app_title")
        self.setWindowTitle(f"{title} [MOCK]" if active else title)

    def _on_position_changed(self, position: str) -> None:
        """Feed the per-position base values to the engine (used by the MOCK)."""
        base_rate, base_be, base_amp = POSITION_VARIATION.get(position, (+4.5, 0.35, 280.0))
        _dbg.state_change("position", self._last_position, position)
        self._last_position = position
        if self.dsp_worker is not None:
            self.dsp_worker.synthetic_rate_spd = base_rate
            self.dsp_worker.synthetic_beat_error_ms = base_be
            self.dsp_worker.synthetic_amplitude_deg = base_amp

    _STATE_TR_KEYS: dict[str, str] = {
        "INACTIVO": "status_idle",
        "ESTABILIZANDO": "status_stabilizing",
        "MIDIENDO": "status_running",
        "DETENIENDO": "status_stopping",
    }
    _STATE_COLORS: dict[str, str] = {
        "ESTABILIZANDO": "color: #ffab00;",
        "MIDIENDO": "color: #00e676;",
        "DETENIENDO": "color: #ff5252;",
    }

    def _set_capture_state(self, new_state: str, **ctx: object) -> None:
        """Track INACTIVO -> ESTABILIZANDO -> MIDIENDO -> DETENIENDO -> INACTIVO."""
        old_state = self._capture_state
        if new_state == old_state:
            return
        self._capture_state = new_state
        _dbg.state_change("captura", old_state, new_state, **ctx)
        self._refresh_session_status_label()

    def _refresh_session_status_label(self) -> None:
        """Render the session state label in the status bar."""
        key = self._STATE_TR_KEYS.get(self._capture_state, "status_idle")
        color = self._STATE_COLORS.get(self._capture_state, "")
        # An engine that only captures (no measurement) never says "MIDIENDO"
        if self._capture_state in ("ESTABILIZANDO", "MIDIENDO") and (
            type(self.dsp_worker) is CaptureStreamWorker
        ):
            key, color = "capturing_no_measure", "color: #00e5ff;"
        self.lbl_session_status.setText(self.translator.tr(key))
        self.lbl_session_status.setStyleSheet(color)

    def _fill_bph_choices(self, quartz: bool) -> None:
        """Mechanical BPH, or quartz steps per second (data = steps per hour)."""
        was_blocked = self.cmb_bph.blockSignals(True)
        try:
            self.cmb_bph.clear()
            if quartz:
                for steps in QUARTZ_STEPS_PER_S:
                    self.cmb_bph.addItem(f"{steps}/s", steps * 3600)
                self.cmb_bph.setCurrentIndex(QUARTZ_STEPS_PER_S.index(DEFAULT_QUARTZ_STEPS))
            else:
                for bph in MECHANICAL_BPH:
                    self.cmb_bph.addItem(str(bph), bph)
                self.cmb_bph.setCurrentText(str(self._last_mechanical_bph))
        finally:
            self.cmb_bph.blockSignals(was_blocked)

    def _on_quartz_toggled(self, checked: bool) -> None:
        """Quartz: steps/s choices, no lift angle nor caliber presets; figures rate only."""
        if checked:
            self._last_mechanical_bph = int(self.cmb_bph.currentData() or 21600)
        self._fill_bph_choices(checked)
        self.spn_lift.setEnabled(not checked)
        self.cmb_caliber.setEnabled(not checked)
        if checked:
            self.cmb_caliber.setCurrentText("Custom")
        tr = self.translator.tr
        self.lbl_bph.setText(tr("quartz_steps") if checked else tr("bph"))
        _dbg.state_change("modo cuarzo", not checked, checked)
        self._on_watch_config_changed()

    def _on_caliber_changed(self, caliber_name: str) -> None:
        """Update BPH and Lift Angle automatically when a caliber preset is selected."""
        if caliber_name in CALIBER_PRESETS:
            bph, lift_angle = CALIBER_PRESETS[caliber_name]
            self.cmb_bph.setCurrentText(str(bph))
            self.spn_lift.setValue(lift_angle)
            self._on_watch_config_changed()
            logger.info(
                "Caliber selection changed to '%s' (bph=%d, lift_angle=%.1f deg)",
                caliber_name,
                bph,
                lift_angle,
            )

    def _on_watch_config_changed(self, *_args: object) -> None:
        """Forward live WatchConfig changes (BPH, Lift Angle, Sample Rate) to the engine."""
        if hasattr(self, "dsp_worker") and self.dsp_worker is not None:
            old = self._active_watch_config or self.current_watch_config()
            cfg = self.current_watch_config()
            self._active_watch_config = cfg
            _dbg.state_change(
                "WatchConfig",
                f"{old.bph} A/h, {old.lift_angle:.1f} deg, {old.sample_rate} Hz",
                f"{cfg.bph} A/h, {cfg.lift_angle:.1f} deg, {cfg.sample_rate} Hz",
            )
            self.dsp_worker.update_watch_config(cfg)

    def _on_language_changed(self, index: int) -> None:
        """Switch active language in Translator and refresh all UI texts."""
        lang_code = self.cmb_lang.itemData(index)
        if lang_code:
            logger.info("UI language changed to '%s'", lang_code)
            self.translator.set_locale(str(lang_code))
            self.retranslate_ui()

    def _on_toggle_global_stats(self, checked: bool) -> None:
        """Show or hide the collapsible positional statistics section."""
        self.global_stats_container.setVisible(checked)
        arrow = "▼" if checked else "▶"
        self.btn_global_stats.setText(f"{arrow} {self.translator.tr('btn_global_stats')}")
        logger.debug("Global statistics panel visibility set to %s", checked)

    def _update_cosc_slopes(self, span_s: float) -> None:
        """Draw the COSC limits as rows of a watch at -4 / +6 s/d over ``span_s`` seconds."""
        span = max(1.0, float(span_s))
        for line, label, rate in (
            (self.cosc_lower_line, self.cosc_lower_label, self.COSC_MIN_SPD),
            (self.cosc_upper_line, self.cosc_upper_label, self.COSC_MAX_SPD),
        ):
            y_end = rate * span / 86.4
            line.setData([0.0, span], [0.0, y_end])
            label.setPos(span, y_end)

    def _append_tape(self, telemetry: BeatTelemetry | TelemetryResult, t_meas: float) -> None:
        """Add the new beats to the paper tape (after the stabilization time).

        Real engine: one point per beat, its offset in ms against the nominal period
        (``BeatTelemetry.tape_*``). MOCK (no beats): the simulated rate integrated over the
        test, rows apart by the simulated beat error.
        """
        stab_s = self.stabilization_s()
        times = getattr(telemetry, "tape_time_s", None)
        if self.dsp_worker is not None and not self.dsp_worker.synthetic_mode:
            if times is None or len(times) == 0:
                return
            x = np.asarray(times, dtype=np.float64) - stab_s
            y = np.asarray(telemetry.tape_offset_ms, dtype=np.float64)
            par = np.asarray(telemetry.tape_parity)
            keep = x >= 0.0
            if not np.any(keep):
                return
            x, y, par = x[keep], y[keep], par[keep]
            if self._tape_y0 is None:
                self._tape_y0 = float(y[0])
            y = y - self._tape_y0
            even = par == 0
            self._tape_x_even.extend(x[even].tolist())
            self._tape_ms_even.extend(y[even].tolist())
            self._tape_x_odd.extend(x[~even].tolist())
            self._tape_ms_odd.extend(y[~even].tolist())
            return
        rate = float(telemetry.rate_spd)
        be = float(telemetry.beat_error_ms)
        if not math.isfinite(rate):
            return
        half_be = 0.5 * be if math.isfinite(be) else 0.0
        y = rate * t_meas / 86.4
        self._tape_x_even.append(t_meas)
        self._tape_ms_even.append(y - half_be)
        self._tape_x_odd.append(t_meas)
        self._tape_ms_odd.append(y + half_be)

    def _update_tape_symbols(self) -> None:
        """Point symbols only while the trace has at most ``TAPE_SYMBOL_MAX_POINTS`` points."""
        want = len(self._tape_x_even) <= TAPE_SYMBOL_MAX_POINTS
        if want == self._tape_symbols:
            return
        self._tape_symbols = want
        for curve, color in ((self.tape_curve_even, "#00e5ff"), (self.tape_curve_odd, "#ffab00")):
            if want:
                curve.setSymbol("o")
                curve.setSymbolSize(5)
                curve.setSymbolBrush(color)
                curve.setSymbolPen(None)
            else:
                curve.setSymbol(None)

    def _paper_tape_target_range(self) -> tuple[float, float] | None:
        """Y range that centres the trace (None when there is no data)."""
        all_y = self._tape_ms_even + self._tape_ms_odd
        if not all_y:
            return None
        y_min = float(min(all_y))
        y_max = float(max(all_y))
        y_center = 0.5 * (y_min + y_max)
        half_span = max(TAPE_MIN_HALF_SPAN_MS, 0.6 * (y_max - y_min) + 0.2)
        return y_center - half_span, y_center + half_span

    def _on_paper_tape_manual_range(self, *_args: object) -> None:
        """The user panned or zoomed the paper tape: stop autoscaling until re-centred."""
        if self._tape_autoscale:
            self._tape_autoscale = False
            logger.debug("Paper tape autoscale paused by user interaction")

    def _autoscale_paper_tape(self) -> None:
        """Follow the trace: re-centre when a point leaves the view or the view is far too wide.

        Paused while the user pans/zooms; "Center Trace" and a new session resume it.
        """
        if not self._tape_autoscale:
            return
        target = self._paper_tape_target_range()
        if target is None:
            return
        (lo, hi) = self.paper_tape_plot.getViewBox().viewRange()[1]
        all_y = self._tape_ms_even + self._tape_ms_odd
        margin = 0.05 * (hi - lo)
        outside = min(all_y) < lo + margin or max(all_y) > hi - margin
        too_wide = (hi - lo) > 3.0 * (target[1] - target[0])
        if outside or too_wide:
            self.paper_tape_plot.setYRange(*target, padding=0.0)

    def center_paper_tape_trace(self) -> None:
        """Auto-adjust the Paper Tape Y-axis range to center on current trace values."""
        self._tape_autoscale = True
        target = self._paper_tape_target_range()
        if target is None:
            target = (-TAPE_MIN_HALF_SPAN_MS, TAPE_MIN_HALF_SPAN_MS)
        self.paper_tape_plot.setYRange(*target, padding=0.0)
        logger.info("Paper tape centered on [%.3f, %.3f] ms", *target)

    def save_current_position(self) -> None:
        """Save current telemetry linked to the selected position (DU, DD, CU, CD, CL, CR)."""
        pos = self.cmb_position.currentText()
        if self._last_telemetry is not None:
            record = self._last_telemetry
        else:
            base_rate, base_be, base_amp = POSITION_VARIATION.get(pos, (+2.0, 0.4, 270.0))
            record = TelemetryResult(
                rate_spd=base_rate,
                beat_error_ms=base_be,
                amplitude_deg=base_amp,
                confidence=0.97,
            )
        self._saved_positions[pos] = record
        logger.info(
            "Saved positional telemetry for '%s': rate=%+.2f s/d, BE=%.2f ms, amp=%.1f deg",
            pos,
            record.rate_spd,
            record.beat_error_ms,
            record.amplitude_deg,
        )
        self._refresh_global_stats_table()

    def _refresh_global_stats_table(self) -> None:
        """Update the positional summary table and compute multi-position mean & max delta."""
        positions_order = [
            p for p in ("DU", "DD", "CU", "CD", "CL", "CR") if p in self._saved_positions
        ]
        self.tbl_global_stats.setRowCount(len(positions_order))

        rates: list[float] = []
        for row_idx, pos in enumerate(positions_order):
            rec = self._saved_positions[pos]
            rates.append(rec.rate_spd)
            vals = (
                pos,
                f"{rec.rate_spd:+.1f}",
                f"{rec.beat_error_ms:.2f}",
                f"{rec.amplitude_deg:.0f}°",
            )
            for col_idx, text in enumerate(vals):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.tbl_global_stats.setItem(row_idx, col_idx, item)

        if rates:
            mean_rate = float(np.mean(rates))
            max_delta = float(max(rates) - min(rates)) if len(rates) >= 2 else 0.0
            self.lbl_global_mean_val.setText(f"{mean_rate:+.2f} s/d ({len(rates)}P)")
            self.lbl_global_delta_val.setText(f"{max_delta:.2f} s/d")
        else:
            self.lbl_global_mean_val.setText("")
            self.lbl_global_delta_val.setText("")

    def save_results_csv(self, file_path: Path | str | None = None) -> Path | None:
        """Export session telemetry results, statistics, and saved positions to a CSV file."""
        test_name = self.txt_test_name.text().strip() or "watch_test"
        if file_path is None:
            default_name = f"{test_name}_results.csv"
            chosen, _ = QFileDialog.getSaveFileName(
                self,
                self.translator.tr("dialog_save_csv"),
                default_name,
                "CSV Files (*.csv)",
            )
            if not chosen:
                logger.info("CSV export cancelled by user")
                return None
            out_path = Path(chosen)
        else:
            out_path = Path(file_path)

        if out_path.suffix.lower() != ".csv":
            out_path = out_path.with_suffix(".csv")
        out_path.parent.mkdir(parents=True, exist_ok=True)

        cfg = self.current_watch_config()
        caliber = self.cmb_caliber.currentText()
        current_pos = self.cmb_position.currentText()

        headers = [
            "test_name",
            "caliber",
            "bph",
            "lift_angle",
            "sample_rate",
            "position",
            "record_type",
            "elapsed_s",
            "rate_spd",
            "beat_error_ms",
            "amplitude_deg",
            "confidence",
            "snr_db",
        ]

        rows_written = 0
        try:
            with out_path.open(mode="w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=headers)
                writer.writeheader()

                for rec in self._session_records:
                    writer.writerow({k: sanitize_csv_cell(v) for k, v in rec.items()})
                    rows_written += 1

                if self._last_telemetry is not None and not self._session_records:
                    summary_row = {
                        "test_name": test_name,
                        "caliber": caliber,
                        "bph": cfg.bph,
                        "lift_angle": f"{cfg.lift_angle:.1f}",
                        "sample_rate": cfg.sample_rate,
                        "position": current_pos,
                        "record_type": "sample",
                        "elapsed_s": "0.00",
                        "rate_spd": f"{self._last_telemetry.rate_spd:.2f}",
                        "beat_error_ms": f"{self._last_telemetry.beat_error_ms:.2f}",
                        "amplitude_deg": f"{self._last_telemetry.amplitude_deg:.1f}",
                        "confidence": f"{self._last_telemetry.confidence:.3f}",
                        "snr_db": "",
                    }
                    writer.writerow({k: sanitize_csv_cell(v) for k, v in summary_row.items()})
                    rows_written += 1

                for pos, pos_rec in self._saved_positions.items():
                    pos_row = {
                        "test_name": test_name,
                        "caliber": caliber,
                        "bph": cfg.bph,
                        "lift_angle": f"{cfg.lift_angle:.1f}",
                        "sample_rate": cfg.sample_rate,
                        "position": pos,
                        "record_type": "saved_position",
                        "elapsed_s": "",
                        "rate_spd": f"{pos_rec.rate_spd:.2f}",
                        "beat_error_ms": f"{pos_rec.beat_error_ms:.2f}",
                        "amplitude_deg": f"{pos_rec.amplitude_deg:.1f}",
                        "confidence": f"{pos_rec.confidence:.3f}",
                        "snr_db": "",
                    }
                    writer.writerow({k: sanitize_csv_cell(v) for k, v in pos_row.items()})
                    rows_written += 1

            _dbg.file_write(out_path, rows=rows_written)
            return out_path
        except Exception as exc:
            _dbg.file_write(out_path, ok=False, error=exc)
            raise

    def export_test_results(self, file_path: Path | str | None = None) -> Path | None:
        """Export only the global positional statistics and positional delta to a CSV file."""
        test_name = self.txt_test_name.text().strip() or "watch_test"
        if file_path is None:
            default_name = f"{test_name}_global_stats.csv"
            chosen, _ = QFileDialog.getSaveFileName(
                self,
                self.translator.tr("dialog_export_test_results"),
                default_name,
                "CSV Files (*.csv)",
            )
            if not chosen:
                logger.info("Export Test Results cancelled by user")
                return None
            out_path = Path(chosen)
        else:
            out_path = Path(file_path)

        if out_path.suffix.lower() != ".csv":
            out_path = out_path.with_suffix(".csv")
        out_path.parent.mkdir(parents=True, exist_ok=True)

        cfg = self.current_watch_config()
        caliber = self.cmb_caliber.currentText()
        positions_order = [
            p for p in ("DU", "DD", "CU", "CD", "CL", "CR") if p in self._saved_positions
        ]
        rates = [self._saved_positions[p].rate_spd for p in positions_order]
        mean_rate = float(np.mean(rates)) if rates else 0.0
        max_delta = float(max(rates) - min(rates)) if len(rates) >= 2 else 0.0

        headers = [
            "test_name",
            "caliber",
            "bph",
            "lift_angle",
            "position",
            "rate_spd",
            "beat_error_ms",
            "amplitude_deg",
            "confidence",
            "global_mean_spd",
            "positional_delta_spd",
        ]

        try:
            with out_path.open(mode="w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=headers)
                writer.writeheader()
                for pos in positions_order:
                    rec = self._saved_positions[pos]
                    row = {
                        "test_name": test_name,
                        "caliber": caliber,
                        "bph": cfg.bph,
                        "lift_angle": f"{cfg.lift_angle:.1f}",
                        "position": pos,
                        "rate_spd": f"{rec.rate_spd:.2f}",
                        "beat_error_ms": f"{rec.beat_error_ms:.2f}",
                        "amplitude_deg": f"{rec.amplitude_deg:.1f}",
                        "confidence": f"{rec.confidence:.3f}",
                        "global_mean_spd": "",
                        "positional_delta_spd": "",
                    }
                    writer.writerow({k: sanitize_csv_cell(v) for k, v in row.items()})
                summary = {
                    "test_name": test_name,
                    "caliber": caliber,
                    "bph": cfg.bph,
                    "lift_angle": f"{cfg.lift_angle:.1f}",
                    "position": "GLOBAL_SUMMARY",
                    "rate_spd": "",
                    "beat_error_ms": "",
                    "amplitude_deg": "",
                    "confidence": "",
                    "global_mean_spd": f"{mean_rate:.2f}" if rates else "",
                    "positional_delta_spd": f"{max_delta:.2f}" if rates else "",
                }
                writer.writerow({k: sanitize_csv_cell(v) for k, v in summary.items()})
            _dbg.file_write(out_path, positions=len(positions_order), delta_spd=round(max_delta, 2))
            return out_path
        except Exception as exc:
            _dbg.file_write(out_path, ok=False, error=exc)
            raise

    # ------------------------------------------------------------------ dialogs
    def _calibration_target(self) -> tuple[str, int] | None:
        """``(device name, sample rate)`` the calibration dialog edits, or None (MOCK/replay)."""
        worker = self.dsp_worker
        device_id = self.selected_device_id()
        if device_id is None or not isinstance(worker, MeasureStreamWorker):
            return None
        if worker.replay_path is not None:
            return None
        name = measure_device_name(device_id)
        if name is None:  # PortAudio not reachable: the label is "[id] name"
            text = self.cmb_device.currentText()
            name = text.split("] ", 1)[1] if "] " in text else text
        return name, int(self.current_watch_config().sample_rate)

    def open_calibration_dialog(self, exec_dialog: bool = True) -> CalibrationDialog | None:
        """Manual calibration of the selected device's clock (applies to the next session)."""
        target = self._calibration_target()
        if target is None:
            self.statusBar().showMessage(self.translator.tr("cal_unavailable"), 6000)
            return None
        live = None
        if (
            self._last_clock_level is not None
            and self._last_clock_level >= 2
            and self._last_clock_source.startswith("en vivo")
            and self._last_drift_sigma_ppm is not None
        ):
            live = LiveDrift(
                self._last_drift_ppm, self._last_drift_sigma_ppm, self._last_clock_level
            )
        dlg = CalibrationDialog(self.translator, target[0], target[1], live=live, parent=self)
        if exec_dialog:
            dlg.exec()
        return dlg

    def report_settings(self) -> dict[str, object]:
        """Settings of the window for the report (no personal data besides the device name)."""
        cfg = self.current_watch_config()
        return {
            "engine": type(self.dsp_worker).__name__,
            "device": self.cmb_device.currentText(),
            "caliber": self.cmb_caliber.currentText(),
            "bph": cfg.bph,
            "quartz": cfg.quartz,
            "lift_angle": cfg.lift_angle,
            "sample_rate": cfg.sample_rate,
            "position": self.cmb_position.currentText(),
            "stabilization_s": self.stabilization_s(),
            "duration_s": self.duration_s(),
            "state": self._capture_state,
            "rate_card": self.card_rate.value_label.text(),
            "beat_error_card": self.card_beat_error.value_label.text(),
            "amplitude_card": self.card_amplitude.value_label.text(),
            "quality": self.lbl_snr_value.text(),
            "clock": self.lbl_stat_drift_val.text(),
            "clock_source": self._last_clock_source,
            "capture": self.lbl_capture_health.text(),
            "language": self.translator.locale,
        }

    def open_report_dialog(self, exec_dialog: bool = True) -> ReportDialog:
        """Bug / result report: a zip to attach to a GitHub issue."""
        worker = self.dsp_worker
        recording = worker.recording_path if isinstance(worker, MeasureStreamWorker) else None
        dlg = ReportDialog(self.translator, self.report_settings(), recording, parent=self)
        if exec_dialog:
            dlg.exec()
        return dlg

    def open_about_dialog(self, exec_dialog: bool = True) -> AboutDialog:
        """Version, licence, links and the configuration and log folders."""
        dlg = AboutDialog(self.translator, parent=self)
        if exec_dialog:
            dlg.exec()
        return dlg

    def open_logs_folder(self) -> bool:
        """Open the log folder in the system's file manager (menu Tools)."""
        return self._open_folder(LoggerSetup.LOG_DIR, "open_logs_folder")

    def open_calibration_folder(self) -> bool:
        """Open the settings and calibrations folder in the file manager (menu Tools)."""
        return self._open_folder(config_dir(), "open_calibration_folder")

    def _open_folder(self, path: Path, label_key: str) -> bool:
        ok = open_folder(path)
        if not ok:  # show the path so the user can open it by hand
            self.statusBar().showMessage(f"{self.translator.tr(label_key)}: {path}", 8000)
        return ok

    def _on_buy_coffee_clicked(self) -> bool:
        """Open the 'Buy me a coffee' page in the default browser.

        Returns:
            True if the system accepted the URL; otherwise the URL is shown in the status bar
            so the user can copy it.
        """
        ok = QDesktopServices.openUrl(QUrl(BUY_ME_A_COFFEE_URL))
        if ok:
            logger.info("Opened '%s' in the default browser", BUY_ME_A_COFFEE_URL)
        else:
            logger.warning("Could not open '%s' in a browser", BUY_ME_A_COFFEE_URL)
            msg = self.translator.tr("coffee_open_failed").format(url=BUY_ME_A_COFFEE_URL)
            self.statusBar().showMessage(msg, 10000)
        return bool(ok)

    def _render_traces_image(self) -> QImage:
        """Render the three acoustic/telemetry trace graphs and metadata banner into a QImage."""
        self.graphs_splitter.repaint()
        graphs_pixmap = self.graphs_splitter.grab()
        gw = max(800, graphs_pixmap.width())
        gh = max(500, graphs_pixmap.height())
        banner_h = 64

        image = QImage(gw, gh + banner_h, QImage.Format.Format_ARGB32)
        image.fill(QColor("#0f1217"))

        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QColor("#00e5ff"))
        painter.setFont(QFont("Inter", 12, QFont.Weight.Bold))
        test_name = self.txt_test_name.text().strip() or "Caliber-Test"
        caliber = self.cmb_caliber.currentText()
        cfg = self.current_watch_config()
        pos = self.cmb_position.currentText()
        header_line = (
            f"{test_name} | {caliber} | {cfg.bph} BPH | Lift {cfg.lift_angle:.1f}° | Pos {pos}"
        )
        painter.drawText(16, 26, header_line)

        painter.setPen(QColor("#d8dee9"))
        painter.setFont(QFont("JetBrains Mono", 10))
        stats_line = (
            f"Rate: {self.card_rate.value_label.text()} s/d   "
            f"Beat Error: {self.card_beat_error.value_label.text()} ms   "
            f"Amplitude: {self.card_amplitude.value_label.text()}   "
            f"Mean: {self.lbl_stat_mean_val.text() or 'N/A'}   "
            f"Delta: {self.lbl_stat_delta_val.text() or 'N/A'}"
        )
        painter.drawText(16, 50, stats_line)
        painter.drawPixmap(0, banner_h, graphs_pixmap)
        painter.end()
        return image

    def export_traces(self, file_path: Path | str | None = None) -> Path | None:
        """Export the audio and telemetry traces to PNG, PDF, or CSV trace data."""
        test_name = self.txt_test_name.text().strip() or "watch_test"
        if file_path is None:
            default_name = f"{test_name}_traces.png"
            chosen, _ = QFileDialog.getSaveFileName(
                self,
                self.translator.tr("dialog_export_traces"),
                default_name,
                "PNG Image (*.png);;PDF Document (*.pdf);;CSV Trace Data (*.csv)",
            )
            if not chosen:
                logger.info("Trace export cancelled by user")
                return None
            out_path = Path(chosen)
        else:
            out_path = Path(file_path)

        ext = out_path.suffix.lower()
        if ext not in (".png", ".pdf", ".csv", ".jpg", ".jpeg"):
            out_path = out_path.with_suffix(".png")
            ext = ".png"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            if ext == ".csv":
                # the stored points, not the curves (which may be downsampled for display)
                x_even, y_even = self._tape_x_even, self._tape_ms_even
                x_odd, y_odd = self._tape_x_odd, self._tape_ms_odd
                x_osc, y_osc = self.osc_wave_curve.getData()
                x_wav, y_wav = self.audio_waveform_curve.getData()

                with out_path.open(mode="w", encoding="utf-8", newline="") as fh:
                    writer = csv.writer(fh)
                    writer.writerow(["trace_name", "x", "y"])
                    if x_even is not None and y_even is not None:
                        for x_val, y_val in zip(x_even, y_even):
                            writer.writerow(
                                ["paper_tape_even", f"{float(x_val):.6f}", f"{float(y_val):.4f}"]
                            )
                    if x_odd is not None and y_odd is not None:
                        for x_val, y_val in zip(x_odd, y_odd):
                            writer.writerow(
                                ["paper_tape_odd", f"{float(x_val):.6f}", f"{float(y_val):.4f}"]
                            )
                    if x_osc is not None and y_osc is not None:
                        for x_val, y_val in zip(x_osc, y_osc):
                            writer.writerow(
                                ["oscilloscope", f"{float(x_val):.6f}", f"{float(y_val):.6f}"]
                            )
                    if x_wav is not None and y_wav is not None:
                        for x_val, y_val in zip(x_wav, y_wav):
                            writer.writerow(
                                ["audio_waveform", f"{float(x_val):.6f}", f"{float(y_val):.6f}"]
                            )
            elif ext == ".pdf":
                printer = QPrinter(QPrinter.PrinterMode.HighResolution)
                printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
                printer.setOutputFileName(str(out_path))
                printer.setPageOrientation(QPageLayout.Orientation.Landscape)
                self._paint_traces_to_printer(printer)
            else:
                img = self._render_traces_image()
                if not img.save(str(out_path)):
                    raise RuntimeError(f"Failed to save trace image to '{out_path}'")

            _dbg.file_write(out_path, kind=ext.lstrip("."))
            return out_path
        except Exception as exc:
            _dbg.file_write(out_path, ok=False, error=exc)
            raise

    def _paint_traces_to_printer(self, printer: QPrinter) -> None:
        """Render the composite trace image onto a QPrinter page."""
        img = self._render_traces_image()
        painter = QPainter(printer)
        try:
            rect = painter.viewport()
            scaled = img.scaled(
                rect.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x_off = max(0, (rect.width() - scaled.width()) // 2)
            y_off = max(0, (rect.height() - scaled.height()) // 2)
            painter.drawImage(QRectF(x_off, y_off, scaled.width(), scaled.height()), scaled)
        finally:
            painter.end()

    def print_traces(self, printer: QPrinter | None = None) -> bool:
        """Print the audio and telemetry traces via QPrintDialog or a provided QPrinter."""
        try:
            if printer is None:
                active_printer = QPrinter(QPrinter.PrinterMode.HighResolution)
                active_printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
                active_printer.setPageOrientation(QPageLayout.Orientation.Landscape)
                dialog = QPrintDialog(active_printer, self)
                if dialog.exec() != QPrintDialog.DialogCode.Accepted:
                    logger.info("Print traces dialog cancelled by user")
                    return False
            else:
                active_printer = printer

            self._paint_traces_to_printer(active_printer)
            logger.info("Audio traces printed successfully")
            return True
        except Exception as exc:
            logger.error("Failed to print audio traces: %s", exc, exc_info=True)
            return False

    def _ensure_test_length_recording(self) -> tuple[np.ndarray, int]:
        """Return an audio recording matching the exact configured test duration."""
        cfg = self.current_watch_config()
        duration_s = self.duration_s()
        expected_samples = int(round(duration_s * cfg.sample_rate))

        if (
            self._recorded_audio is not None
            and self._recorded_sample_rate == cfg.sample_rate
            and len(self._recorded_audio) == expected_samples
        ):
            return self._recorded_audio, self._recorded_sample_rate

        # MOCK only: hardware sessions are recorded by MeasureStreamWorker
        pos = self.cmb_position.currentText()
        base_rate, base_be, base_amp = POSITION_VARIATION.get(pos, (+4.5, 0.35, 280.0))
        logger.info(
            "[MOCK] Synthesizing test-length tick train (duration=%.1f s, samples=%d, fs=%d Hz)",
            duration_s,
            expected_samples,
            cfg.sample_rate,
        )
        signal = generate_tick_train(
            duration_s=duration_s,
            fs=cfg.sample_rate,
            bph=cfg.bph,
            rate_spd=base_rate,
            beat_error_ms=base_be,
            amplitude_deg=base_amp,
            lift_angle_deg=cfg.lift_angle,
            snr_db=32.0,
            seed=2026,
        )
        if len(signal) > expected_samples:
            signal = signal[:expected_samples]
        elif len(signal) < expected_samples:
            signal = np.pad(signal, (0, expected_samples - len(signal)))

        self._recorded_audio = signal
        self._recorded_sample_rate = cfg.sample_rate
        return self._recorded_audio, self._recorded_sample_rate

    def recording_allowed(self) -> bool:
        """Tests up to ``RECORDING_MAX_DURATION_S`` are recorded; longer ones are not."""
        return self.duration_s() <= RECORDING_MAX_DURATION_S

    def _refresh_record_button(self) -> None:
        allowed = self.recording_allowed()
        self.btn_record.setEnabled(allowed)
        tr = self.translator.tr
        self.btn_record.setToolTip(
            "" if allowed else tr("recording_too_long").format(limit=RECORDING_MAX_DURATION_S)
        )

    def record_audio(self, file_path: Path | str | None = None) -> Path | None:
        """Save the session audio: during a session it is saved when the session ends.

        Hardware (``MeasureStreamWorker``): the engine records every session to a temporary
        FLAC with its tags; MOCK: a synthetic tick train of the test length. Tests longer
        than ``RECORDING_MAX_DURATION_S`` are not recorded.
        """
        if not self.recording_allowed():
            msg = self.translator.tr("recording_too_long").format(limit=RECORDING_MAX_DURATION_S)
            logger.warning("Recording refused: test of %.0f s", self.duration_s())
            self.statusBar().showMessage(msg, 8000)
            return None
        if file_path is None and self.dsp_worker is not None and self.dsp_worker.isRunning():
            self._save_audio_on_finish = True
            logger.info(
                "Audio recording armed for active test session; will prompt to save upon completion"
            )
            return None
        return self.save_recorded_audio(file_path=file_path)

    def save_recorded_audio(self, file_path: Path | str | None = None) -> Path | None:
        """Save the last session's audio (FLAC with tags; MOCK: synthetic WAV)."""
        if self.is_mock_active() and not self.recording_allowed():
            return None  # the synthetic train would be built for the whole (long) test
        if self.is_mock_active():
            return self._save_mock_recording(file_path)
        if isinstance(self.dsp_worker, MeasureStreamWorker):
            return self._save_session_recording(file_path)
        logger.warning("No recording available: this engine does not record")
        self.statusBar().showMessage(self.translator.tr("no_recording_available"), 6000)
        return None

    def _save_session_recording(self, file_path: Path | str | None = None) -> Path | None:
        """Move the hardware session's temporary FLAC (with its tags) to the chosen path."""
        worker = self.dsp_worker
        assert isinstance(worker, MeasureStreamWorker)
        if not worker.has_recording:
            key = "recording_replay" if worker.replay_path is not None else "no_recording_available"
            logger.warning("No session recording to save (%s)", key)
            self.statusBar().showMessage(self.translator.tr(key), 6000)
            return None
        test_name = self.txt_test_name.text().strip() or "watch_test"
        if file_path is None:
            chosen, _ = QFileDialog.getSaveFileName(
                self,
                self.translator.tr("dialog_save_audio"),
                f"{test_name}.flac",
                "FLAC Audio (*.flac)",
            )
            if not chosen:
                logger.info("Audio recording save dialog cancelled by user")
                return None
            file_path = chosen
        target = Path(file_path)
        if target.suffix.lower() != ".flac":
            target = target.with_suffix(".flac")
        source = worker.take_recording()
        if source is None:
            self.statusBar().showMessage(self.translator.tr("no_recording_available"), 6000)
            return None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
        except Exception as exc:
            _dbg.file_write(target, ok=False, error=exc)
            source.unlink(missing_ok=True)
            self.statusBar().showMessage(str(exc), 8000)
            return None
        _dbg.file_write(target, session=True)
        self.statusBar().showMessage(
            self.translator.tr("recording_saved").format(path=target.name), 6000
        )
        return target

    def _save_mock_recording(self, file_path: Path | str | None = None) -> Path | None:
        """Save the simulated MOCK tick train as a 16-bit WAV file."""
        audio_data, sample_rate = self._ensure_test_length_recording()
        test_name = self.txt_test_name.text().strip() or "watch_test"
        if file_path is None:
            chosen, _ = QFileDialog.getSaveFileName(
                self,
                self.translator.tr("dialog_save_audio"),
                f"{test_name}_MOCK.wav",
                "WAV Audio (*.wav)",
            )
            if not chosen:
                logger.info("Audio recording save dialog cancelled by user")
                return None
            file_path = chosen
        try:
            out_path = save_wav(file_path, audio_data, sample_rate)
        except Exception as exc:
            _dbg.file_write(file_path, ok=False, error=exc, mock=True)
            raise
        _dbg.file_write(out_path, samples=len(audio_data), fs=sample_rate, mock=True)
        return out_path

    def retranslate_ui(self) -> None:
        """Apply localized strings from `self.translator` to all widgets."""
        tr = self.translator.tr
        self.setWindowTitle(tr("app_title"))
        self.btn_mock.setText(tr("mock_mode"))
        self.btn_mock.setToolTip(tr("mock_tooltip"))
        self.lbl_mock_badge.setText(tr("mock_badge"))
        self._update_mock_indicators()
        self._refresh_session_status_label()

        self.lbl_test_name.setText(tr("test_name"))
        self.txt_test_name.setPlaceholderText(tr("test_name_placeholder"))
        self.lbl_caliber.setText(tr("caliber"))
        self.lbl_bph.setText(tr("quartz_steps") if self.chk_quartz.isChecked() else tr("bph"))
        self.lbl_quartz.setText(tr("quartz"))
        self.chk_quartz.setToolTip(tr("quartz_tooltip"))
        self.lbl_lift.setText(tr("lift_angle"))
        self.lbl_position.setText(tr("position"))
        self.lbl_device.setText(tr("in_device"))
        self.lbl_srate.setText(tr("sample_rate"))
        self.lbl_stab.setText(tr("stab_time"))
        self.lbl_duration.setText(tr("test_duration"))
        self.lbl_lang.setText(tr("language"))

        if self.cmb_device.count() > 0:
            self.cmb_device.setItemText(0, tr("synthetic_device"))
            self._sync_combo_popup_width(self.cmb_device, min_width=230)

        self.card_rate.set_title(tr("rate"))
        self.card_beat_error.set_title(tr("beat_error"))
        self.card_amplitude.set_title(tr("amplitude"))

        self.lbl_signal_quality.setText(tr("signal_quality").upper())
        self.lbl_stats_title.setText(tr("statistics").upper())

        self.lbl_stat_max_key.setText(f"{tr('stat_max')}:")
        self.lbl_stat_min_key.setText(f"{tr('stat_min')}:")
        self.lbl_stat_delta_key.setText(f"{tr('stat_delta')}:")
        self.lbl_stat_mean_key.setText(f"{tr('stat_mean')}:")
        self.lbl_stat_elapsed_key.setText(f"{tr('stat_elapsed')}:")
        self.lbl_stat_drift_key.setText(f"{tr('clock_drift')}:")
        self.lbl_drift_status.setText(
            tr("status_drift_format").format(
                ppm=self._last_drift_ppm,
                fs=self._last_effective_fs,
                bias=self._last_rate_bias_spd,
            )
        )
        self._refresh_capture_health_label()

        self.btn_save_position.setText(tr("btn_save_position"))
        self.btn_save_csv.setText(tr("btn_save_csv"))
        self.btn_export_test_results.setText(tr("btn_export_test_results"))
        self.btn_buy_coffee.setText(tr("btn_buy_coffee"))
        self.btn_about.setText(tr("btn_about"))
        self.btn_report.setText(tr("btn_report"))
        self.btn_report.setToolTip(tr("btn_report_tooltip"))

        self.menu_tools.setTitle(tr("menu_tools"))
        self.action_open_logs.setText(tr("open_logs_folder"))
        self.action_open_calibration.setText(tr("open_calibration_folder"))
        self.menu_help.setTitle(tr("menu_help"))
        self.action_report.setText(tr("btn_report"))
        self.action_about.setText(tr("btn_about"))
        self.btn_calibrate.setText(tr("btn_calibrate"))
        self.btn_calibrate.setToolTip(tr("btn_calibrate_tooltip"))
        self.btn_buy_coffee.setToolTip(tr("coffee_tooltip").format(url=BUY_ME_A_COFFEE_URL))
        arrow = "▼" if self.btn_global_stats.isChecked() else "▶"
        self.btn_global_stats.setText(f"{arrow} {tr('btn_global_stats')}")
        self.tbl_global_stats.setHorizontalHeaderLabels(
            [tr("col_pos"), tr("col_rate"), tr("col_be"), tr("col_amp")]
        )
        self.lbl_global_mean_key.setText(f"{tr('global_mean')}:")
        self.lbl_global_delta_key.setText(f"{tr('global_max_delta')}:")

        self.btn_export_traces.setText(tr("btn_export_traces"))
        self.btn_print_traces.setText(tr("btn_print_traces"))
        self.btn_center_trace.setText(tr("btn_center_trace"))
        self.btn_record.setText(f"● {tr('btn_record_audio').upper()}")
        self._refresh_record_button()
        self.btn_start.setText(tr("btn_start"))
        self.btn_stop.setText(tr("btn_stop"))

        self.paper_tape_plot.setTitle(tr("paper_tape_title"), color="#c8d1de", size="11pt")
        self.paper_tape_plot.setLabel("bottom", tr("paper_tape_x"))
        self.paper_tape_plot.setLabel("left", tr("paper_tape_y"))

        self.oscilloscope_plot.setTitle(tr("oscilloscope_title"), color="#c8d1de", size="11pt")
        self.oscilloscope_plot.setLabel("bottom", tr("oscilloscope_x"))
        self.oscilloscope_plot.setLabel("left", tr("oscilloscope_y"))

        self.waveform_plot.setTitle(tr("waveform_title"), color="#c8d1de", size="11pt")
        self.waveform_plot.setLabel("bottom", tr("waveform_x"))
        self.waveform_plot.setLabel("left", tr("waveform_y"))

    def stabilization_s(self) -> float:
        """Selected stabilization time in seconds."""
        return float(self.cmb_stab.currentData())

    def duration_s(self) -> float:
        """Selected test duration in seconds."""
        return float(self.cmb_duration.currentData())

    def set_session_timing(
        self, stabilization_s: float | None = None, duration_s: float | None = None
    ) -> None:
        """Select a stabilization time and/or test duration (automation and tests).

        A value that is not one of the choices is added to the list and selected.
        """
        for combo, value in ((self.cmb_stab, stabilization_s), (self.cmb_duration, duration_s)):
            if value is None:
                continue
            idx = combo.findData(float(value))
            if idx < 0:
                combo.addItem(_format_duration(value), float(value))
                idx = combo.count() - 1
            combo.setCurrentIndex(idx)

    def current_watch_config(self) -> WatchConfig:
        """Build a `WatchConfig` instance from the active Top Bar controls."""
        bph = int(self.cmb_bph.currentData() or int(self.cmb_bph.currentText()))
        lift_angle = float(self.spn_lift.value())
        sample_rate = int(self.cmb_srate.currentData() or 48000)
        return WatchConfig(
            bph=bph,
            lift_angle=lift_angle,
            sample_rate=sample_rate,
            quartz=self.chk_quartz.isChecked(),
        )

    def _check_session_duration(self) -> None:
        """Automatically stop capture once (stabilization_s + duration_s) has elapsed."""
        if self._capture_start_monotonic is None:
            return
        elapsed_s = max(0.0, time.monotonic() - self._capture_start_monotonic)
        stab_s = self.stabilization_s()
        duration_s = self.duration_s()
        total_limit_s = stab_s + duration_s

        if self._capture_state == "ESTABILIZANDO" and elapsed_s >= stab_s:
            self._set_capture_state("MIDIENDO", elapsed_s=round(elapsed_s, 2))

        display_s = min(elapsed_s, total_limit_s)
        mins = int(display_s) // 60
        secs = display_s % 60.0
        self.lbl_stat_elapsed_val.setText(f"{mins:02d}:{secs:04.1f}")

        if elapsed_s >= total_limit_s:
            logger.info(
                "Configured test duration reached (stab=%.1fs + duration=%.1fs); stopping capture",
                stab_s,
                duration_s,
            )
            self.stop_capture()

    def start_capture(self) -> None:
        """Start the real-time engine session and reset plots."""
        if self.dsp_worker is None or self.dsp_worker.isRunning():
            return

        logger.info("Starting telemetry capture session")
        self._rate_history.clear()
        self._session_records.clear()
        self._tape_x_even.clear()
        self._tape_ms_even.clear()
        self._tape_x_odd.clear()
        self._tape_ms_odd.clear()
        self._recorded_audio = None
        self._capture_start_monotonic = time.monotonic()

        self.lbl_stat_max_val.setText("")
        self.lbl_stat_min_val.setText("")
        self.lbl_stat_delta_val.setText("")
        self.lbl_stat_mean_val.setText("")
        self.lbl_stat_elapsed_val.setText("")

        self.tape_curve_even.setData([], [])
        self.tape_curve_odd.setData([], [])
        self._update_tape_symbols()
        self.osc_wave_curve.setData([], [])
        self.osc_envelope_curve.setData([], [])
        self.osc_impulse_markers.setData([], [])
        self.audio_waveform_curve.setData([], [])
        # The quality bar follows the input level until the engine reports detection quality
        self._telemetry_seen = False
        self._tape_autoscale = True
        self._last_warning = ""
        self._last_saturated_monotonic = -math.inf
        self._silence_since = None
        self._silence_warned = False
        self._perf_waveform.reset()
        self._perf_level.reset()
        self.prg_snr.setValue(0)
        self.prg_snr.setStyleSheet("")
        self.lbl_snr_value.setText("-- dB")
        if isinstance(self.dsp_worker, CaptureStreamWorker):
            for card in (self.card_rate, self.card_beat_error, self.card_amplitude):
                card.set_value(self.EMPTY_VALUE)
            self.lbl_drift_status.setText("")
            self.lbl_stat_drift_val.setText("")
            self._clear_oscilloscope_markers()

        duration_s = self.duration_s()
        self.paper_tape_plot.setXRange(0.0, max(10.0, duration_s))
        self._tape_y0 = None
        self._update_cosc_slopes(max(10.0, duration_s))
        self.paper_tape_plot.setYRange(-TAPE_MIN_HALF_SPAN_MS, TAPE_MIN_HALF_SPAN_MS)

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)

        config = self.current_watch_config()
        position = self.cmb_position.currentText()
        base_rate, base_be, base_amp = POSITION_VARIATION.get(position, (+4.5, 0.35, 280.0))

        stab_s = self.stabilization_s()
        self._set_capture_state(
            "ESTABILIZANDO" if stab_s > 0.0 else "MIDIENDO",
            engine=type(self.dsp_worker).__name__,
            position=position,
        )

        self.dsp_worker.synthetic_rate_spd = base_rate
        self.dsp_worker.synthetic_beat_error_ms = base_be
        self.dsp_worker.synthetic_amplitude_deg = base_amp
        if isinstance(self.dsp_worker, MockStreamWorker):
            # MOCK-only extra (outside BaseStreamWorker): test timing for the simulation
            self.dsp_worker.set_session_timing(stab_s, duration_s)
        self._active_watch_config = config
        self.dsp_worker.set_input_device(self.selected_device_id())
        self.dsp_worker.update_watch_config(config)
        if isinstance(self.dsp_worker, MeasureStreamWorker):
            self.dsp_worker.set_recording_enabled(self.recording_allowed())
        _dbg.process_start(
            "sesión de captura",
            engine=type(self.dsp_worker).__name__,
            bph=config.bph,
            lift=config.lift_angle,
            fs=config.sample_rate,
            stab_s=stab_s,
            duration_s=duration_s,
        )
        self.dsp_worker.start_stream()
        # NullStreamWorker finishes synchronously (session_finished already handled)
        if self.dsp_worker.isRunning():
            self._session_timer.start()
            _dbg.process_start("session_timer", interval_ms=self._session_timer.interval())

    def stop_capture(self) -> None:
        """Stop the running background engine."""
        if self._session_timer.isActive():
            _dbg.process_end("session_timer")
        self._session_timer.stop()
        self._capture_start_monotonic = None
        if self._capture_state != "INACTIVO":
            self._set_capture_state("DETENIENDO")
        if self.dsp_worker is not None and self.dsp_worker.isRunning():
            logger.info("Stopping active %s capture session", type(self.dsp_worker).__name__)
            self.dsp_worker.stop_stream()
        self._on_session_finished()

    def _on_session_finished(self) -> None:
        """Restore button and selector states once capture finishes."""
        if self._capture_state == "INACTIVO":
            return  # already handled (e.g. queued session_finished after stop_capture)
        self._session_timer.stop()
        self._capture_start_monotonic = None
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._set_top_controls_enabled(True)
        _dbg.process_end("sesión de captura", engine=type(self.dsp_worker).__name__)
        self._set_capture_state("INACTIVO")
        if self._save_audio_on_finish:
            self._save_audio_on_finish = False
            self.save_recorded_audio()

    def _set_top_controls_enabled(self, enabled: bool) -> None:
        """Enable or disable configuration controls during active measurement."""
        for widget in (
            self.txt_test_name,
            self.cmb_caliber,
            self.cmb_bph,
            self.spn_lift,
            self.cmb_position,
            self.cmb_device,
            self.cmb_srate,
            self.cmb_stab,
            self.cmb_duration,
        ):
            widget.setEnabled(enabled)

    def _figure_text(
        self, value: float, fmt: str, confidence: float, real: bool
    ) -> tuple[str, bool]:
        """Card text and dim flag: real-engine figures that are missing or of low confidence
        are not shown; the MOCK always shows its (simulated) figures."""
        if not math.isfinite(value) or (real and confidence < self.CONFIDENCE_HIDE):
            return self.EMPTY_VALUE, False
        return format(value, fmt), real and confidence < self.CONFIDENCE_DIM

    def _real_quality(self, telemetry: BeatTelemetry) -> tuple[int, str, str] | None:
        """``(percent, kind, text)`` of the quality bar from detection quality (real engine).

        None while the detector is not locked (the bar keeps showing the input level).
        """
        snr = float(telemetry.snr_db)
        usable = float(telemetry.usable_ratio)
        if not (telemetry.locked and math.isfinite(snr)):
            return None
        q_snr = float(
            np.clip(
                (snr - self.QUALITY_SNR_FLOOR_DB)
                / (self.QUALITY_SNR_FULL_DB - self.QUALITY_SNR_FLOOR_DB),
                0.0,
                1.0,
            )
        )
        share = usable if math.isfinite(usable) else 0.0
        pct = int(round(100.0 * q_snr * share))
        saturated = (
            telemetry.saturated_beats > 0
            or time.monotonic() - self._last_saturated_monotonic < self.SATURATION_HOLD_S
        )
        kind = "bad" if saturated or pct < 35 else ("low" if pct < 65 else "ok")
        text = f"SNR {snr:.0f} dB | {100.0 * share:.0f} %"
        return pct, kind, f"{text} ⚠" if saturated else text

    def _set_quality_bar(self, pct: int, kind: str | None, text: str) -> None:
        self.prg_snr.setValue(pct)
        if kind is None:
            self.prg_snr.setStyleSheet("")
        else:
            self.prg_snr.setStyleSheet(
                f"QProgressBar::chunk {{ background-color: {self._LEVEL_BAR_COLORS[kind]};"
                " border-radius: 3px; }"
            )
        self.lbl_snr_value.setText(text)

    def _clear_oscilloscope_markers(self) -> None:
        for line in (self.line_p1, self.line_p2, self.line_p3):
            line.setVisible(False)
        self.osc_impulse_markers.setData([])

    def _show_real_beat(self, beat: BeatEvent, fs: float) -> None:
        """Oscilloscope for the real engine: band-passed beat, markers at the real P1/P2/P3."""
        wave = np.asarray(beat.raw_waveform, dtype=np.float64)
        if wave.size == 0 or beat.waveform_start_s is None:
            return
        peak_abs = float(np.max(np.abs(wave))) or 1.0
        norm_wave = wave / peak_abs
        t_ms = (np.arange(wave.size, dtype=np.float64) / fs) * 1000.0
        # Fixed X range (the engine sends a fixed-length window): rescaling the axis on every
        # beat re-laid out the plot and caused visible stutters.
        span_ms = float(t_ms[-1])
        if self._osc_span_ms != span_ms:
            self._osc_span_ms = span_ms
            self.oscilloscope_plot.enableAutoRange(x=False)
            self.oscilloscope_plot.setXRange(0.0, span_ms, padding=0.0)
        self.osc_wave_curve.setData(t_ms, norm_wave)
        env_win = max(int(round(0.00015 * fs)), 3)
        env_kernel = np.ones(env_win, dtype=np.float64) / float(env_win)
        self.osc_envelope_curve.setData(t_ms, np.convolve(np.abs(norm_wave), env_kernel, "same"))
        spots = []
        colors = ("#00e676", "#ffab00", "#ff1744")
        stamps = (beat.timestamp_p1, beat.timestamp_p2, beat.timestamp_p3)
        for line, ts, color in zip((self.line_p1, self.line_p2, self.line_p3), stamps, colors):
            if not math.isfinite(ts):
                line.setVisible(False)
                continue
            t = (ts - beat.waveform_start_s) * 1000.0
            idx = int(np.clip(round(t / 1000.0 * fs), 0, wave.size - 1))
            line.setPos(t)
            line.setVisible(True)
            spots.append(
                {
                    "pos": (t, float(norm_wave[idx])),
                    "brush": pg.mkBrush(color),
                    "pen": pg.mkPen("#ffffff", width=1),
                }
            )
        self.osc_impulse_markers.setData(spots)

    def _on_telemetry_ready(self, telemetry: BeatTelemetry | TelemetryResult) -> None:
        """Slot receiving real-time BeatTelemetry from the engine."""
        if self._capture_state == "INACTIVO":
            # Late telemetry still queued when the session stopped: ignore it so no stray
            # point is added to the paper tape / statistics.
            return
        rate_spd = float(telemetry.rate_spd)
        beat_error_ms = float(telemetry.beat_error_ms)
        amplitude_deg = float(telemetry.amplitude_deg)
        confidence = float(telemetry.confidence)
        real = isinstance(telemetry, BeatTelemetry) and not self.dsp_worker.synthetic_mode
        # Rate and beat error are judged by the timing confidence (a tic/tac amplitude
        # mismatch does not affect them); the amplitude by the overall confidence.
        timing_conf = float(getattr(telemetry, "timing_confidence", math.nan))
        if not math.isfinite(timing_conf):
            timing_conf = confidence
        # A point is usable when the rate is available and (real engine) confident enough
        usable = math.isfinite(rate_spd) and (not real or timing_conf >= self.CONFIDENCE_HIDE)

        if usable:
            self._last_telemetry = TelemetryResult(
                rate_spd=rate_spd,
                beat_error_ms=beat_error_ms,
                amplitude_deg=amplitude_deg,
                confidence=confidence,
            )

        stab_s = self.stabilization_s()
        duration_s = self.duration_s()

        if self._capture_start_monotonic is not None:
            elapsed_s = max(0.0, time.monotonic() - self._capture_start_monotonic)
            should_record_tape = elapsed_s >= stab_s and usable
            t_meas = max(0.0, min(duration_s, elapsed_s - stab_s))
        else:
            elapsed_s = len(self._rate_history) * 0.25
            should_record_tape = usable
            t_meas = elapsed_s

        mins = int(elapsed_s) // 60
        secs = elapsed_s % 60.0
        self.lbl_stat_elapsed_val.setText(f"{mins:02d}:{secs:04.1f}")

        raw_snr_db = float(getattr(telemetry, "snr_db", 0.0))
        if real:
            assert isinstance(telemetry, BeatTelemetry)
            quality = self._real_quality(telemetry)
            self._telemetry_seen = quality is not None
            if quality is not None:
                self._set_quality_bar(*quality)
            if telemetry.warnings and telemetry.warnings[0] != self._last_warning:
                self._last_warning = telemetry.warnings[0]
                self.statusBar().showMessage(self._last_warning, 6000)
        else:
            if not self._telemetry_seen:
                self._telemetry_seen = True
                self.prg_snr.setStyleSheet("")
            if raw_snr_db <= 0.0 and confidence > 0.0:
                raw_snr_db = 38.0 * confidence
            quality_pct = int(round(np.clip((raw_snr_db / 45.0) * 100.0 * confidence, 0.0, 100.0)))
            self._set_quality_bar(quality_pct, None, f"{raw_snr_db:.1f} dB ({quality_pct}%)")

        text, dim = self._figure_text(rate_spd, "+.1f", timing_conf, real)
        self.card_rate.set_value(text, dim)
        text, dim = self._figure_text(beat_error_ms, ".2f", timing_conf, real)
        self.card_beat_error.set_value(text, dim)
        # Amplitude: hidden like the rate; a tic/tac mismatch (low overall confidence) only
        # greys it out, so it does not blink on calibres whose live templates disagree.
        text, dim = self._figure_text(amplitude_deg, ".0f", timing_conf, real)
        dim = dim or (real and confidence < self.CONFIDENCE_DIM)
        self.card_amplitude.set_value(text if text == self.EMPTY_VALUE else f"{text}°", dim)

        # Engine copy of the active config (avoids building a WatchConfig 4x/s)
        cfg_now = self._active_watch_config or self.current_watch_config()
        _dbg.throttled(
            "telemetry_ready",
            "telemetry rate=%+.2f s/d be=%.2f ms amp=%.0f deg conf=%.2f snr=%.1f dB",
            rate_spd,
            beat_error_ms,
            amplitude_deg,
            confidence,
            raw_snr_db,
            every_s=5.0,
        )
        if should_record_tape:
            self._rate_history.append(rate_spd)
            self._session_records.append(
                {
                    "test_name": self.txt_test_name.text().strip() or "watch_test",
                    "caliber": self.cmb_caliber.currentText(),
                    "bph": cfg_now.bph,
                    "lift_angle": f"{cfg_now.lift_angle:.1f}",
                    "sample_rate": cfg_now.sample_rate,
                    "position": self.cmb_position.currentText(),
                    "record_type": "sample",
                    "elapsed_s": f"{t_meas:.2f}",
                    "rate_spd": f"{rate_spd:.2f}",
                    "beat_error_ms": f"{beat_error_ms:.2f}",
                    "amplitude_deg": f"{amplitude_deg:.1f}",
                    "confidence": f"{confidence:.3f}",
                    "snr_db": f"{raw_snr_db:.1f}",
                }
            )

            r_max = max(self._rate_history)
            r_min = min(self._rate_history)
            r_delta = r_max - r_min
            r_mean = float(np.mean(self._rate_history))

            self.lbl_stat_max_val.setText(f"{r_max:+.2f} s/d")
            self.lbl_stat_min_val.setText(f"{r_min:+.2f} s/d")
            self.lbl_stat_delta_val.setText(f"{r_delta:.2f} s/d")
            self.lbl_stat_mean_val.setText(f"{r_mean:+.2f} s/d")

        if self._capture_start_monotonic is None or elapsed_s >= stab_s:
            self._append_tape(telemetry, t_meas)
            self._update_tape_symbols()
            self.tape_curve_even.setData(self._tape_x_even, self._tape_ms_even)
            self.tape_curve_odd.setData(self._tape_x_odd, self._tape_ms_odd)
            self._autoscale_paper_tape()

        if isinstance(telemetry, BeatTelemetry):
            fs = float(
                telemetry.effective_fs
                if getattr(telemetry, "effective_fs", 0.0) > 0.0
                else cfg_now.sample_rate
            )
            if real:
                if telemetry.beats:
                    self._show_real_beat(telemetry.beats[-1], float(cfg_now.sample_rate))
                elif not telemetry.locked:
                    self._clear_oscilloscope_markers()
            elif telemetry.beats:
                self._show_mock_beat(telemetry.beats[-1], fs)

            # The bottom-right live audio waveform is fed only by ``waveform_ready`` (decoupled
            # ~30 FPS display tick), so it never waits for the DSP analysis to finish.

        if self._capture_start_monotonic is not None and elapsed_s >= stab_s + duration_s:
            self.stop_capture()

    def _show_mock_beat(self, latest_beat: BeatEvent, fs: float) -> None:
        """MOCK oscilloscope: P3 = largest peak, P1/P2 placed by their offsets to it."""
        if self._osc_span_ms is not None:  # back from the real engine: auto X range again
            self._osc_span_ms = None
            self.oscilloscope_plot.enableAutoRange(x=True)
        # Bottom-Left Oscilloscope: beat-triggered bipolar waveform + envelope + P1/P2/P3
        wave = latest_beat.raw_waveform.copy()
        if len(wave) > 0:
            if self.dsp_worker.synthetic_mode:
                t_local = np.arange(len(wave), dtype=np.float64) / fs
                phase_shift = float(self._osc_rng.uniform(0.0, 2.0 * np.pi))
                mod = 1.0 + 0.14 * np.sin(2.0 * np.pi * 1100.0 * t_local + phase_shift)
                jitter_noise = self._osc_rng.normal(
                    0.0, 0.03 * (float(np.max(np.abs(wave))) or 1.0), size=len(wave)
                )
                wave = (wave * mod) + jitter_noise

            peak_abs = float(np.max(np.abs(wave))) or 1.0
            norm_wave = wave / peak_abs
            t_ms = (np.arange(len(wave), dtype=np.float64) / fs) * 1000.0

            dt_p2 = (latest_beat.timestamp_p2 - latest_beat.timestamp_p1) * 1000.0
            dt_p3 = (latest_beat.timestamp_p3 - latest_beat.timestamp_p1) * 1000.0

            p3_idx = int(np.argmax(np.abs(norm_wave)))
            t_p3_ms = float(t_ms[p3_idx])
            t_p1_ms = max(0.2, t_p3_ms - dt_p3)
            t_p2_ms = float(np.clip(t_p1_ms + dt_p2, t_p1_ms, t_p3_ms))

            self.osc_wave_curve.setData(t_ms, norm_wave)
            env_win = max(int(round(0.00015 * fs)), 3)
            env_kernel = np.ones(env_win, dtype=np.float64) / float(env_win)
            norm_env = np.convolve(np.abs(norm_wave), env_kernel, mode="same")
            self.osc_envelope_curve.setData(t_ms, norm_env)
            self.line_p1.setPos(t_p1_ms)
            self.line_p2.setPos(t_p2_ms)
            self.line_p3.setPos(t_p3_ms)

            p1_idx = int(np.clip(round((t_p1_ms / 1000.0) * fs), 0, len(norm_wave) - 1))
            p2_idx = int(np.clip(round((t_p2_ms / 1000.0) * fs), 0, len(norm_wave) - 1))

            spots = [
                {
                    "pos": (t_p1_ms, float(norm_wave[p1_idx])),
                    "brush": pg.mkBrush("#00e676"),
                    "pen": pg.mkPen("#ffffff", width=1),
                },
                {
                    "pos": (t_p2_ms, float(norm_wave[p2_idx])),
                    "brush": pg.mkBrush("#ffab00"),
                    "pen": pg.mkPen("#ffffff", width=1),
                },
                {
                    "pos": (t_p3_ms, float(norm_wave[p3_idx])),
                    "brush": pg.mkBrush("#ff1744"),
                    "pen": pg.mkPen("#ffffff", width=1),
                },
            ]
            self.osc_impulse_markers.setData(spots)
        for line in (self.line_p1, self.line_p2, self.line_p3):
            line.setVisible(True)

    def _on_waveform_ready(self, time_axis: np.ndarray, waveform: np.ndarray) -> None:
        """Slot updating the real-time acoustic waveform curve from the engine."""
        t0 = time.perf_counter()  # slot performance (SlotProfiler)
        if time_axis is not None and waveform is not None and len(time_axis) > 0:
            self.audio_waveform_curve.setData(time_axis, waveform)
        self._perf_waveform.record(t0)

    _LEVEL_BAR_COLORS: dict[str, str] = {"ok": "#00e676", "low": "#ffab00", "bad": "#ff1744"}

    def _on_level_updated(self, peak_dbfs: float, rms_dbfs: float, saturated: bool) -> None:
        """Signal-quality bar by input level/saturation while there is no detection quality.

        Once the engine reports detection quality (``_telemetry_seen``) the bar shows it, but
        a saturation is still remembered for ``SATURATION_HOLD_S`` and turns the bar red.
        """
        t0 = time.perf_counter()
        if self._capture_state == "INACTIVO":
            return
        if saturated:
            self._last_saturated_monotonic = time.monotonic()
        self._check_digital_silence(peak_dbfs)
        if self._telemetry_seen:
            return
        pct, kind = level_to_quality(peak_dbfs, rms_dbfs, saturated)
        self._set_quality_bar(pct, kind, level_text(peak_dbfs, rms_dbfs, saturated))
        self._perf_level.record(t0)

    def _on_drift_updated(
        self,
        effective_fs: float,
        drift_ppm: float,
        rate_bias_spd: float,
    ) -> None:
        """Slot updating clock drift telemetry in the statistics panel and status bar."""
        self._last_effective_fs = float(effective_fs)
        self._last_drift_ppm = float(drift_ppm)
        self._last_rate_bias_spd = float(rate_bias_spd)

        self._refresh_drift_stat_label()
        self.lbl_drift_status.setText(
            self.translator.tr("status_drift_format").format(
                ppm=drift_ppm,
                fs=effective_fs,
                bias=rate_bias_spd,
            )
        )
        _dbg.throttled(
            "drift_updated",
            "Clock drift telemetry updated: effective_fs=%.2f Hz, drift=%+.3f ppm, bias=%+.3f s/d",
            effective_fs,
            drift_ppm,
            rate_bias_spd,
            every_s=5.0,
        )

    def _check_digital_silence(self, peak_dbfs: float) -> None:
        """Warn once per session when a hardware input delivers nothing but exact zeros."""
        if self.dsp_worker is None or self.dsp_worker.synthetic_mode or self._silence_warned:
            return
        if math.isfinite(peak_dbfs):
            self._silence_since = None
            return
        now = time.monotonic()
        if self._silence_since is None:
            self._silence_since = now
        elif now - self._silence_since >= self.SILENCE_HINT_S:
            self._silence_warned = True
            key = {"darwin": "no_signal_macos", "win32": "no_signal_windows"}.get(
                sys.platform, "no_signal_other"
            )
            message = self.translator.tr(key)
            logger.warning("Digital silence from the input device: %s", message)
            self.statusBar().showMessage(message, 20000)

    def _on_clock_info(self, sigma_ppm: float, level: int, source: str) -> None:
        """Uncertainty, confidence level and origin of the drift applied to the rate."""
        self._last_drift_sigma_ppm = float(sigma_ppm) if math.isfinite(sigma_ppm) else None
        self._last_clock_level = int(level)
        self._last_clock_source = str(source)
        self.lbl_stat_drift_val.setToolTip(self._last_clock_source)
        self.lbl_drift_status.setToolTip(self._last_clock_source)
        self._refresh_drift_stat_label()

    def _on_mock_drift_detail(self, sigma_ppm: float, confidence: float) -> None:
        """MOCK-only slot: simulated drift uncertainty (± sigma) and confidence."""
        self._last_drift_sigma_ppm = float(sigma_ppm)
        self._last_drift_confidence = float(confidence)
        self._refresh_drift_stat_label()

    def _refresh_drift_stat_label(self) -> None:
        """Drift value in the statistics panel (with ± sigma and confidence when known)."""
        ppm = self._last_drift_ppm
        if self._last_clock_level is not None:
            tr = self.translator.tr
            if self._last_clock_level == 0 or self._last_drift_sigma_ppm is None:
                text = tr("drift_uncalibrated_format").format(ppm=ppm)
            else:
                text = tr("drift_level_format").format(
                    ppm=ppm, sigma=self._last_drift_sigma_ppm, level=self._last_clock_level
                )
        elif self._last_drift_sigma_ppm is None or self._last_drift_confidence is None:
            text = f"{ppm:+.2f} ppm ({self._last_effective_fs:.1f} Hz)"
        else:
            text = (
                f"{ppm:+.2f} ± {self._last_drift_sigma_ppm:.2f} ppm "
                f"({100.0 * self._last_drift_confidence:.0f} %)"
            )
        self.lbl_stat_drift_val.setText(text)

    def _on_capture_health(self, ingest_pct: float, overflows: int) -> None:
        """Slot updating the capture-health indicator (percentage of audio received)."""
        self._last_capture_pct = float(ingest_pct)
        self._last_capture_overflows = int(overflows)
        self._refresh_capture_health_label()

    def _refresh_capture_health_label(self) -> None:
        """Render the capture-health label; turns red when audio is being lost."""
        pct = self._last_capture_pct
        self.lbl_capture_health.setText(
            self.translator.tr("status_capture_format").format(
                pct=pct, ovf=self._last_capture_overflows
            )
        )
        self.lbl_capture_health.setStyleSheet("color: #ff1744;" if pct < 98.0 else "")

    def _on_worker_error(self, message: str) -> None:
        """Slot logging any error emitted by the engine safely on the UI thread."""
        logger.error("%s reported error: %s", type(self.dsp_worker).__name__, message)
        self.statusBar().showMessage(message, 8000)

    def closeEvent(self, event) -> None:  # noqa: N802
        """Ensure background worker thread is cleanly stopped before window closes."""
        with _dbg.phase("cierre_ventana", capture_state=self._capture_state):
            self.stop_capture()
            if hasattr(self, "dsp_worker") and self.dsp_worker is not None:
                self.dsp_worker.shutdown()
                _dbg.process_end(f"engine {type(self.dsp_worker).__name__}")
            super().closeEvent(event)
