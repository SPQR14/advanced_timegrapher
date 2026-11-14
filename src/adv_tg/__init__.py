"""Advanced Timegrapher (adv_tg) package."""

__version__ = "1.0.1"

import importlib.util
import os
import sys
from pathlib import Path

# One BLAS thread. The DSP only multiplies small matrices (64-sample biquad blocks); with the
# default thread pool OpenBLAS spins 3x the CPU for the same wall time and competes with the
# audio and GUI threads. Must be set before numpy is first imported; inherited by the "spawn"
# children (capture and analysis processes). ``setdefault``: an explicit user value wins.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")


def _bootstrap_windows_dlls() -> None:
    """
    Isolates the Windows runtime environment from "DLL Hell"
    (e.g., MiKTeX, Anaconda).
    Forces the Windows loader to use only the Qt6 and C++ DLLs
    located in the virtual environment (.venv) or the packaged application
    (PyInstaller).
    """
    if sys.platform != "win32":
        return

    spec = importlib.util.find_spec("PyQt6")
    if spec is None or spec.origin is None:
        return

    pyqt_dir = Path(spec.origin).parent
    qt_bin_dir = pyqt_dir / "Qt6" / "bin"
    qt_plugins_dir = pyqt_dir / "Qt6" / "plugins"

    if qt_bin_dir.exists():
        bin_str = str(qt_bin_dir.resolve())

        os.environ["PATH"] = bin_str + os.pathsep + os.environ.get("PATH", "")

        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(bin_str)

    if qt_plugins_dir.exists():
        os.environ["QT_PLUGIN_PATH"] = str(qt_plugins_dir.resolve())


_bootstrap_windows_dlls()
