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
# CA certificates for the update notice (HTTPS to the GitHub API): the bundled OpenSSL does
# not know where the host system keeps its own
datas += collect_data_files("certifi")
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
    # Linux: sounddevice must find the bundled PortAudio (see the hook)
    runtime_hooks=[str(ROOT / "packaging" / "rthook_portaudio.py")],
    # SciPy is not used at all (filter design and filtering in pure numpy): never bundle it.
    excludes=["matplotlib", "pytest", "pytestqt", "tkinter", "IPython", "scipy"],
    noarchive=False,
)

# 5. Linux: libraries that must come from the system, never from the build host.
# PyInstaller bundles every shared library the build host resolves. These belong to the base
# system of the machine that RUNS the application and break it when an older copy shadows them:
#   - libstdc++ / libgcc_s: the system's OpenGL drivers (Mesa, LLVM) need the system's, newer
#     ones; libgcc_s of the build host can also ask for a newer glibc than the target has.
#   - libmvec: part of glibc, must match the system's libc.
#   - libasound: finds its plugins (PipeWire / PulseAudio "default" device) and its
#     configuration in distribution-specific paths.
#   - glib: Qt's GTK platform theme loads the system's GTK, which needs the system's glib.
#   - fontconfig / freetype / harfbuzz: read the system's font configuration and caches.
#   - libX11 and friends: must match the system's libxcb (never bundled by PyInstaller).
# The rpm and deb packages declare them as dependencies (packaging/linux); the AppImage
# relies on them like every AppImage does (they are in the AppImage "excludelist").
if sys.platform.startswith("linux"):
    _SYSTEM_LIBS = (
        "libstdc++.so",
        "libgcc_s.so",
        "libmvec.so",
        "libasound.so",
        "libglib-2.0.so",
        "libgio-2.0.so",
        "libgobject-2.0.so",
        "libgmodule-2.0.so",
        "libgthread-2.0.so",
        "libfontconfig.so",
        "libfreetype.so",
        "libharfbuzz.so",
        "libX11.so",
        "libX11-xcb.so",
        "libXau.so",
        "libXext.so",
    )
    a.binaries = [
        b for b in a.binaries if not Path(b[0]).name.startswith(_SYSTEM_LIBS)
    ]

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
    upx=False,  # UPX-packed DLLs trip antivirus heuristics and can break Qt plugins
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
