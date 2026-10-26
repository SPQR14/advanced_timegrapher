"""Application entry point for Advanced Timegrapher (``adv-tg`` / ``python -m adv_tg``)."""

from __future__ import annotations

import faulthandler
import multiprocessing
import platform
import sys
from collections.abc import Sequence
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


def main(argv: Sequence[str] | None = None) -> int:
    """Start the Qt application and return its exit code."""
    # Needed by the isolated capture child ("spawn") in frozen builds (PyInstaller).
    multiprocessing.freeze_support()
    args = list(sys.argv if argv is None else argv)
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

    dbg.process_start("Qt event loop (app.exec)")
    exit_code = int(app.exec())
    dbg.process_end("Qt event loop (app.exec)", exit_code=exit_code)
    logger.info("Application exited with code %d", exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
