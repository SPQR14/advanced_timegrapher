# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec of Advanced Timegrapher (one-folder build; a .app bundle on macOS).
#
#   pip install pyinstaller
#   pyinstaller packaging/adv-tg.spec --noconfirm
#
import plistlib
import subprocess
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 (SPECPATH is injected by PyInstaller)
sys.path.insert(0, str(ROOT / "src"))
from adv_tg import __version__  # noqa: E402

# 1. Data files: assets (SVG, ICO, ICNS), watch caliber DB and sample calibration audios.
# Only docs/samples is packaged from the docs/ directory (markdown notes/reports are excluded).
datas = collect_data_files("adv_tg", includes=["ui/assets/*", "utils/db/*.csv"])
assets_dir = ROOT / "src" / "adv_tg" / "ui" / "assets"
db_dir = ROOT / "src" / "adv_tg" / "utils" / "db"
samples_dir = ROOT / "docs" / "samples"

if assets_dir.exists():
    datas.append((str(assets_dir), "adv_tg/ui/assets"))
if db_dir.exists():
    datas.append((str(db_dir), "adv_tg/utils/db"))
if samples_dir.exists():
    datas.append((str(samples_dir), "docs/samples"))

# 2. Native dynamic libraries:
# sounddevice bundles PortAudio on macOS and Windows, but Linux uses system PortAudio.
# If libportaudio is present on the Linux build host, bundle it directly into the package.
binaries = []
try:
    from PyInstaller.utils.hooks import collect_dynamic_libs
    binaries += collect_dynamic_libs("adv_tg")
except Exception:
    pass

dsp_dir = ROOT / "src" / "adv_tg" / "dsp"
if dsp_dir.exists():
    for ext_pattern in ("*.so", "*.pyd", "*.dylib"):
        for lib in dsp_dir.glob(ext_pattern):
            binaries.append((str(lib), "adv_tg/dsp"))

if sys.platform.startswith("linux"):
    try:
        out = subprocess.check_output(["ldconfig", "-p"], text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            if "libportaudio.so" in line and "=>" in line:
                candidate = Path(line.split("=>")[-1].strip())
                if candidate.exists():
                    binaries.append((str(candidate), "."))
                    break
    except Exception:
        pass
    if not binaries:
        for cand in (
            Path("/usr/lib/x86_64-linux-gnu/libportaudio.so.2"),
            Path("/usr/lib64/libportaudio.so.2"),
            Path("/usr/lib/libportaudio.so.2"),
        ):
            if cand.exists():
                binaries.append((str(cand), "."))
                break

# 3. Child processes and lazy imports:
# The capture and analysis processes are spawned independently and import these modules.
hiddenimports = [
    "adv_tg.dsp",
    "adv_tg.dsp.engine",
    "adv_tg.dsp.filters",
    "adv_tg.dsp.adv_tg_dsp_native",
    "adv_tg.audio.capture_process",
    "adv_tg.audio.analysis_process",
    "sounddevice",
    "soundfile",
    "cffi",
    "numpy",
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "PyQt6.QtSvg",
]

# 4. Platform icons
icon_icns = ROOT / "src" / "adv_tg" / "ui" / "assets" / "adv-tg.icns"
icon_ico = ROOT / "src" / "adv_tg" / "ui" / "assets" / "adv-tg.ico"

exe_icon = None
if sys.platform.startswith("win") and icon_ico.exists():
    exe_icon = str(icon_ico)
elif sys.platform == "darwin" and icon_icns.exists():
    exe_icon = str(icon_icns)

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # SciPy is not used at runtime (filter design in numpy, filtering in C++): never bundle it.
    excludes=["matplotlib", "pytest", "pytestqt", "tkinter", "IPython", "scipy"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="adv-tg",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=exe_icon,
)

coll = COLLECT(exe, a.binaries, a.datas, name="adv-tg")  # noqa: F821

if sys.platform == "darwin":
    info = plistlib.loads((ROOT / "packaging" / "macos" / "Info.plist").read_bytes())
    info["CFBundleShortVersionString"] = __version__
    info["CFBundleVersion"] = __version__
    if icon_icns.exists():
        info["CFBundleIconFile"] = "adv-tg.icns"
    app = BUNDLE(  # noqa: F821
        coll,
        name="Advanced Timegrapher.app",
        bundle_identifier=info["CFBundleIdentifier"],
        info_plist=info,  # includes NSMicrophoneUsageDescription (mandatory)
        icon=str(icon_icns) if icon_icns.exists() else None,
    )
