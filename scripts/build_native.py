#!/usr/bin/env python3
"""Build the native C++20 engine (libadvtg_native) with the strongest optimisation per CPU.

    python scripts/build_native.py                       # auto: portable build + SIMD dispatch
    python scripts/build_native.py --target native       # this machine only (-march=native)
    python scripts/build_native.py --target zen5         # AMD Zen 5 (Beelink SER9)
    python scripts/build_native.py --target apple_silicon
    python scripts/build_native.py --target rpi4|rpi5|arm64_universal|x86_64_universal
    python scripts/build_native.py --clean

The library is copied to ``src/adv_tg/_native/`` (picked up by ``adv_tg.native_bridge`` and
bundled by PyInstaller). Partial builds for development of one module:

    python scripts/build_native.py --sources fft.cpp,dsp_engine.cpp,c_api_dsp.cpp \\
        --build-dir build/native_dsp --lib-suffix _dsp --out build/native_dsp/lib

and use it with ``ADV_TG_NATIVE_LIB=build/native_dsp/lib/libadvtg_native_dsp.so``.

Floating point: never -ffast-math (NaN is a sentinel, compensated sums). CMake + Ninja/Make
/MSVC is the normal path; without CMake a direct g++/clang++ build is used (GCC/Clang only).
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "native"
#: Minimum macOS of the app bundle (packaging/macos/Info.plist); MACOSX_DEPLOYMENT_TARGET overrides.
MACOS_MIN = os.environ.get("MACOSX_DEPLOYMENT_TARGET", "").strip() or "11.0"
TARGETS = (
    "auto",
    "native",
    "zen5",
    "x86_64_universal",
    "apple_silicon",
    "rpi4",
    "rpi5",
    "arm64_universal",
)


def lib_filename(suffix: str = "") -> str:
    name = f"advtg_native{suffix}"
    if sys.platform == "win32":
        return f"{name}.dll"
    if sys.platform == "darwin":
        return f"lib{name}.dylib"
    return f"lib{name}.so"


def _run(cmd: list[str], **kw: object) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)  # type: ignore[arg-type]


def _is_arm64() -> bool:
    return platform.machine().lower() in ("arm64", "aarch64")


def build_cmake(a: argparse.Namespace, build_dir: Path) -> Path:
    cmake = shutil.which("cmake")
    assert cmake
    cfg = ["-S", str(NATIVE), "-B", str(build_dir), "-DCMAKE_BUILD_TYPE=Release"]
    cfg += [f"-DADVTG_TARGET_ARCH={a.target}", f"-DADVTG_LIB_SUFFIX={a.lib_suffix}"]
    cfg += [f"-DADVTG_LTO={'OFF' if a.no_lto else 'ON'}"]
    if a.sources:
        cfg += ["-DADVTG_SOURCES=" + ";".join(s.strip() for s in a.sources.split(",") if s.strip())]
    if a.generator:
        cfg += ["-G", a.generator]
    elif sys.platform == "win32":
        # Visual Studio generator (newest installed): always MSVC. Ninja outside a developer
        # prompt may pick MinGW/clang from PATH, whose DLL needs libstdc++-6.dll & co.
        cfg += ["-A", "x64"]
    elif shutil.which("ninja"):
        cfg += ["-G", "Ninja"]
    if sys.platform == "darwin":
        if _is_arm64():
            cfg += ["-DCMAKE_OSX_ARCHITECTURES=arm64"]
        # same minimum as packaging/macos/Info.plist (LSMinimumSystemVersion); otherwise the
        # dylib only loads on the macOS version of the build machine and newer
        cfg += [f"-DCMAKE_OSX_DEPLOYMENT_TARGET={MACOS_MIN}"]
    _run([cmake, *cfg])
    build = [cmake, "--build", str(build_dir), "--config", "Release", "-j", str(a.jobs)]
    _run(build)
    return find_built(build_dir, a.lib_suffix)


def find_built(build_dir: Path, suffix: str) -> Path:
    name = lib_filename(suffix)
    for sub in (".", "Release", "bin", "lib"):
        p = build_dir / sub / name
        if p.is_file():
            return p
    hits = list(build_dir.rglob(name))
    if not hits:
        raise SystemExit(f"build finished but {name} was not found under {build_dir}")
    return hits[0]


def build_direct(a: argparse.Namespace, build_dir: Path) -> Path:
    """Fallback without CMake: g++/clang++ only (x86_64 and arm64)."""
    cxx = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
    if not cxx:
        raise SystemExit("neither cmake nor g++/clang++ found")
    build_dir.mkdir(parents=True, exist_ok=True)
    src = sorted((NATIVE / "src").glob("*.cpp"))
    want = {s.strip() for s in a.sources.split(",") if s.strip()}
    x86 = platform.machine().lower() in ("x86_64", "amd64")
    base = [
        "-std=c++20", "-O3", "-DNDEBUG", "-fPIC", "-fvisibility=hidden", "-fno-math-errno",
        "-fno-trapping-math", "-ffp-contract=fast", "-funroll-loops", "-fno-fast-math",
        f"-I{NATIVE / 'include'}", f"-I{NATIVE / 'src'}", "-DADVTG_VERSION_STRING=\"2.0.0\"",
        "-DADVTG_BUILDING",
    ]  # fmt: skip
    if sys.platform == "darwin":
        base.append(f"-mmacosx-version-min={MACOS_MIN}")
    if x86:
        base += ["-DADVTG_HAVE_SSE42", "-DADVTG_HAVE_AVX2", "-DADVTG_HAVE_AVX512"]
        arch = {"native": ["-march=native"], "zen5": ["-march=znver5"]}.get(
            a.target, ["-march=x86-64-v2", "-mtune=generic"]
        )
    else:
        base += ["-DADVTG_HAVE_NEON"]
        arch = {
            "native": ["-mcpu=native"], "rpi4": ["-mcpu=cortex-a72"], "rpi5": ["-mcpu=cortex-a76"],
            "apple_silicon": ["-mcpu=apple-m1"],
        }.get(a.target, ["-march=armv8-a"])  # fmt: skip
    extra = {
        "simd_level_sse42.cpp": ["-msse4.2", "-mpopcnt"],
        "simd_level_avx2.cpp": ["-mavx2", "-mfma", "-mbmi", "-mbmi2", "-mf16c"],
        "simd_level_avx512.cpp": ["-mavx512f", "-mavx512dq", "-mavx512bw", "-mavx512vl",
                                  "-mavx2", "-mfma", "-mbmi2"],
        # bit-identical to numpy (see native/CMakeLists.txt)
        "session.cpp": ["-ffp-contract=off"],
        "filters.cpp": ["-ffp-contract=off"],
    }  # fmt: skip
    objs: list[str] = []
    for f in src:
        n = f.name
        is_core = n in ("c_api.cpp", "simd_dispatch.cpp") or n.startswith("simd_level_")
        if want and not is_core and n not in want:
            continue
        obj = build_dir / (f.stem + ".o")
        _run([cxx, *base, *arch, *extra.get(n, []), "-c", str(f), "-o", str(obj)])
        objs.append(str(obj))
    out = build_dir / lib_filename(a.lib_suffix)
    link = [cxx, "-shared", "-o", str(out), *objs, "-lpthread"]
    link += [f"-mmacosx-version-min={MACOS_MIN}"] if sys.platform == "darwin" else ["-ldl"]
    _run(link)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--target", choices=TARGETS, default="auto")
    p.add_argument("--clean", action="store_true", help="remove the build directory first")
    p.add_argument("--build-dir", type=Path, default=ROOT / "build" / "native")
    p.add_argument("--out", type=Path, default=ROOT / "src" / "adv_tg" / "_native",
                   help="directory the library is copied to")  # fmt: skip
    p.add_argument("--sources", default="", help="comma-separated subset of native/src/*.cpp")
    p.add_argument("--lib-suffix", default="", help="suffix of the library name")
    p.add_argument("--no-lto", action="store_true")
    p.add_argument("--generator", default="")
    p.add_argument("--direct", action="store_true", help="skip CMake, call the compiler")
    p.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 4)
    a = p.parse_args(argv)

    build_dir = a.build_dir if a.build_dir.is_absolute() else ROOT / a.build_dir
    out_dir = a.out if a.out.is_absolute() else ROOT / a.out
    if a.clean and build_dir.exists():
        shutil.rmtree(build_dir)
    if shutil.which("cmake") and not a.direct:
        built = build_cmake(a, build_dir)
    else:
        built = build_direct(a, build_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / built.name
    shutil.copy2(built, dest)
    print(f"native engine: {dest} ({dest.stat().st_size / 1024:.0f} KiB, target={a.target})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
