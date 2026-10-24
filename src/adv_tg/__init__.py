"""Advanced Timegrapher (adv_tg) package."""

__version__ = "0.2.0"

import importlib.util
import os
import sys
from pathlib import Path


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
