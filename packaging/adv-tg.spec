# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec of Advanced Timegrapher (one-folder build; a .app bundle on macOS).
#
#   pip install pyinstaller
#   pyinstaller packaging/adv-tg.spec --noconfirm
#
# STARTING POINT, NOT VALIDATED YET: see packaging/README.md for the checklist.
import plistlib
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 (SPECPATH is injected by PyInstaller)
sys.path.insert(0, str(ROOT / "src"))
from adv_tg import __version__  # noqa: E402

datas = collect_data_files("adv_tg", includes=["ui/assets/*", "utils/db/*.csv"])
# The child processes are started by "spawn" and import these modules by name.
hiddenimports = ["adv_tg.audio.capture_process", "adv_tg.audio.analysis_process"]

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["matplotlib", "pytest", "pytestqt", "tkinter", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="adv-tg",
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="adv-tg")  # noqa: F821

if sys.platform == "darwin":
    info = plistlib.loads((ROOT / "packaging" / "macos" / "Info.plist").read_bytes())
    info["CFBundleShortVersionString"] = __version__
    info["CFBundleVersion"] = __version__
    app = BUNDLE(  # noqa: F821
        coll,
        name="Advanced Timegrapher.app",
        bundle_identifier=info["CFBundleIdentifier"],
        info_plist=info,  # includes NSMicrophoneUsageDescription (mandatory)
    )
