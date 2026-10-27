"""Application entry point for Advanced Timegrapher (``adv-tg`` / ``python -m adv_tg``)."""

from __future__ import annotations

import faulthandler
import multiprocessing
import os
import platform
import sys
from collections.abc import Sequence
from pathlib import Path
from types import TracebackType
from typing import TextIO

from adv_tg import __version__
from adv_tg.utils.debugger import Debugger
from adv_tg.utils.logger_setup import LoggerSetup

dbg = Debugger.get("adv_tg.main")
logger = dbg.logger

# Kept referenced for the whole process lifetime so faulthandler can always write to it.
_FAULT_LOG_FH: TextIO | None = None


def _install_excepthook() -> None:
    """Route every uncaught exception to the log (KeyboardInterrupt passes through)."""

    def _hook(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: TracebackType | None,
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))

    sys.excepthook = _hook


def _enable_faulthandler() -> None:
    """Dump native crashes (segfaults in Qt/PortAudio) to ``logs/faulthandler.log``."""
    global _FAULT_LOG_FH
    LoggerSetup.LOG_DIR.mkdir(parents=True, exist_ok=True)
    fault_path = LoggerSetup.LOG_DIR / "faulthandler.log"
    _FAULT_LOG_FH = fault_path.open("a", encoding="utf-8")
    faulthandler.enable(file=_FAULT_LOG_FH, all_threads=True)
    logger.info("faulthandler enabled -> '%s'", fault_path)


def _log_environment() -> None:
    """Log version, platform and key library versions."""
    import numpy
    from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR

    logger.info(
        "Advanced Timegrapher %s | %s | Python %s | PyQt6 %s (Qt %s) | numpy %s",
        __version__,
        platform.platform(),
        platform.python_version(),
        PYQT_VERSION_STR,
        QT_VERSION_STR,
        numpy.__version__,
    )


def _find_sample_file(name: str = "miyota_9015.flac") -> Path | None:
    """Locate sample audio in development or frozen PyInstaller bundle."""
    candidates = []
    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS"):
            meipass = Path(sys._MEIPASS)
            candidates.extend(
                [
                    meipass / "docs" / "samples" / name,
                    meipass / "samples" / name,
                    meipass / "Resources" / "docs" / "samples" / name,
                ]
            )
        exe = Path(sys.executable).resolve()
        candidates.extend(
            [
                exe.parent / "docs" / "samples" / name,
                exe.parent.parent / "Resources" / "docs" / "samples" / name,
                exe.parent / "_internal" / "docs" / "samples" / name,
            ]
        )
    else:
        root = Path(__file__).resolve().parents[2]
        candidates.append(root / "docs" / "samples" / name)
    for c in candidates:
        if c.is_file():
            return c
    return None


def main(argv: Sequence[str] | None = None) -> int:
    """Start the Qt application and return its exit code."""
    # Needed by the isolated capture child ("spawn") in frozen builds (PyInstaller).
    multiprocessing.freeze_support()
    args = list(sys.argv if argv is None else argv)

    if "--version" in args or "-v" in args:
        print(f"Advanced Timegrapher {__version__}")
        return 0

    smoke_test = "--smoke-test" in args or os.environ.get("ADV_TG_SMOKE_TEST") == "1"

    _install_excepthook()
    _enable_faulthandler()

    with dbg.phase("arranque_aplicacion", version=__version__):
        _log_environment()

        from adv_tg.ui.main_window import configure_pyqtgraph_gpu

        try:
            configure_pyqtgraph_gpu()
        except Exception:
            logger.exception("configure_pyqtgraph_gpu() failed; continuing with defaults")

        from PyQt6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication(args)
        app.setApplicationName("Advanced Timegrapher")
        app.setOrganizationName("AdvancedTimegrapher")
        # Wayland ignores setWindowIcon and takes the icon from the matching .desktop file.
        app.setDesktopFileName("adv-tg")

        try:
            from adv_tg.ui.main_window import app_icon

            app.setWindowIcon(app_icon())
        except Exception:
            logger.exception("No se pudo cargar el icono de la aplicación; se continúa sin él")

        try:
            from adv_tg.i18n.translator import Translator
            from adv_tg.ui.main_window import MainWin

            with dbg.phase("crear_translator"):
                translator = Translator("en")
            with dbg.phase("crear_ventana"):
                window = MainWin(translator=translator)
        except Exception as exc:
            logger.critical("Fatal error while creating the main window", exc_info=True)
            QMessageBox.critical(None, "Fatal Startup Error", f"{type(exc).__name__}: {exc}")
            return 1

        window.show()

        if smoke_test:
            # 1. The pure-numpy DSP engine must import (it is all the analysis needs)
            try:
                import adv_tg.dsp.engine  # noqa: F401
            except Exception as exc:
                logger.critical(
                    "Smoke test failed: DSP engine import error: %s", exc, exc_info=True
                )
                window.close()
                return 1

            # 2. SciPy must NOT be loaded in sys.modules
            if "scipy" in sys.modules:
                logger.critical(
                    "Smoke test failed: 'scipy' is loaded in sys.modules: %s",
                    [m for m in sys.modules if "scipy" in m],
                )
                window.close()
                return 1

            # 3. Short measurement test without audio hardware (real sample or synthetic)
            try:
                import math

                import numpy as np
                import soundfile as sf

                from adv_tg.core.sample_stream import SampleStream
                from adv_tg.core.session import MeasurementSession, SessionConfig

                sample_file = _find_sample_file("miyota_9015.flac")
                if sample_file is not None:
                    x, fs = sf.read(sample_file)
                    bph = 28800.0
                else:
                    # Synthetic ticks fallback
                    fs = 48000.0
                    bph = 21600.0
                    period_s = 3600.0 / bph
                    n_samples = int(fs * 4.0)
                    x = np.zeros(n_samples, dtype=np.float32)
                    tick = (
                        np.sin(2 * np.pi * 4000 * np.arange(200) / fs)
                        * np.exp(-np.arange(200) / 30.0)
                    ).astype(np.float32)
                    for k in range(int(4.0 / period_s)):
                        pos = int(k * period_s * fs)
                        if pos + len(tick) <= len(x):
                            x[pos : pos + len(tick)] += tick

                cfg = SessionConfig(sample_rate=float(fs), bph=bph)
                session = MeasurementSession(cfg)
                stream = SampleStream(len(x), float(fs))
                stream.write(x.astype(np.float32), host_ns=1000)
                snap = session.update(stream)
                rate = snap.metrics.rate_s_per_day

                if not math.isfinite(rate):
                    logger.critical("Smoke test failed: measured rate is not finite (%s)", rate)
                    window.close()
                    return 1

                logger.info(
                    "Smoke test DSP verified: beats=%d, rate=%.2f s/d (no hardware, no scipy)",
                    snap.beats_total,
                    rate,
                )
            except Exception as exc:
                logger.critical("Smoke test failed during DSP verification: %s", exc, exc_info=True)
                window.close()
                return 1

            logger.info("Smoke test passed: application initialized and DSP verified successfully")
            window.close()
            return 0

    dbg.process_start("Qt event loop (app.exec)")
    exit_code = int(app.exec())
    dbg.process_end("Qt event loop (app.exec)", exit_code=exit_code)
    logger.info("Application exited with code %d", exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
