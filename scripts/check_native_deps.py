#!/usr/bin/env python3
"""Check that the native engine library is self-contained enough to ship in the bundles.

    python scripts/check_native_deps.py src/adv_tg/_native/libadvtg_native.so
    python scripts/check_native_deps.py "dist/Advanced Timegrapher.app"   # finds the dylib
    python scripts/check_native_deps.py dist/adv-tg                        # finds the dll/so

The library may only depend on what every supported system has:

* Linux (ELF): glibc (libc, libm, libdl, libpthread, librt, the loader), libstdc++ and
  libgcc_s, which the deb/rpm declare and the AppImage takes from the system; the highest
  GLIBC symbol version must not exceed ``--max-glibc`` (2.34: Enterprise Linux 9, Debian 12,
  Ubuntu 22.04).
* macOS (Mach-O, via ``otool``): only ``/usr/lib`` and ``/System`` libraries (libc++ and
  libSystem) and a minimum macOS not above ``--max-macos`` (11.0, Info.plist).
* Windows (PE): only Windows system DLLs (the UCRT, ``api-ms-win-crt-*``, ships with
  Windows 10/11). MSVCP140 / VCRUNTIME140 and MinGW runtimes are rejected: a clean Windows
  does not have them and ctypes would not find them next to the DLL in the bundle (CMake
  links the MSVC runtime statically, /MT).

Exit code 0 when the library passes, 1 otherwise (with the reason), 2 when not found.
"""

from __future__ import annotations

import argparse
import re
import struct
import subprocess
import sys
from pathlib import Path

LIB_NAMES = ("libadvtg_native.so", "libadvtg_native.dylib", "advtg_native.dll")

LINUX_ALLOWED = re.compile(
    r"^(libc\.so\.6|libm\.so\.6|libdl\.so\.2|libpthread\.so\.0|librt\.so\.1|"
    r"libstdc\+\+\.so\.6|libgcc_s\.so\.1|ld-linux-(x86-64|aarch64)\.so\.[12])$"
)
WINDOWS_REJECTED = re.compile(
    r"^(msvcp\d+.*|vcruntime\d+.*|concrt\d+.*|vcomp\d+.*|"
    r"libstdc\+\+.*|libgcc.*|libwinpthread.*|libc\+\+.*|libomp.*)\.dll$",
    re.IGNORECASE,
)


def find_library(path: Path) -> Path | None:
    if path.is_file():
        return path
    for name in LIB_NAMES:
        hits = sorted(path.rglob(name))
        if hits:
            return hits[0]
    return None


# ---------------------------------------------------------------------- ELF
def elf_info(data: bytes) -> tuple[list[str], list[str]]:
    """(DT_NEEDED names, GLIBC_* versions referenced) of a 64-bit little-endian ELF."""
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise ValueError("not a 64-bit little-endian ELF file")
    (shoff,) = struct.unpack_from("<Q", data, 0x28)
    shentsize, shnum, _shstrndx = struct.unpack_from("<HHH", data, 0x3A)
    sections = []
    for i in range(shnum):
        off = shoff + i * shentsize
        _name, sh_type, _flags, _addr, sh_offset, sh_size, sh_link = struct.unpack_from(
            "<IIQQQQI", data, off
        )
        sections.append((sh_type, sh_offset, sh_size, sh_link))
    needed: list[str] = []
    versions: set[str] = set()
    for sh_type, off, size, link in sections:
        if sh_type == 6:  # SHT_DYNAMIC
            strtab_off = sections[link][1]
            for j in range(0, size, 16):
                tag, val = struct.unpack_from("<qQ", data, off + j)
                if tag == 0:
                    break
                if tag == 1:  # DT_NEEDED
                    end = data.index(b"\0", strtab_off + val)
                    needed.append(data[strtab_off + val : end].decode())
    for m in re.finditer(rb"GLIBC_(\d+)\.(\d+)(?:\.(\d+))?", data):
        versions.add(m.group(0).decode())
    return needed, sorted(versions, key=_ver_key)


def _ver_key(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v))


def check_linux(lib: Path, max_glibc: str) -> list[str]:
    needed, versions = elf_info(lib.read_bytes())
    print(f"  NEEDED: {', '.join(needed)}")
    print(f"  highest GLIBC symbol version: {versions[-1] if versions else '-'}")
    errors = [f"unexpected dependency {n}" for n in needed if not LINUX_ALLOWED.match(n)]
    limit = _ver_key(max_glibc)
    errors += [f"needs {v} > GLIBC_{max_glibc}" for v in versions if _ver_key(v) > limit]
    return errors


# ---------------------------------------------------------------------- PE
def pe_imports(data: bytes) -> list[str]:
    """Names of the DLLs imported by a PE32+ file (import directory)."""
    if data[:2] != b"MZ":
        raise ValueError("not a PE file")
    (pe,) = struct.unpack_from("<I", data, 0x3C)
    if data[pe : pe + 4] != b"PE\0\0":
        raise ValueError("PE signature missing")
    (n_sections,) = struct.unpack_from("<H", data, pe + 6)
    (opt_size,) = struct.unpack_from("<H", data, pe + 20)
    opt = pe + 24
    (magic,) = struct.unpack_from("<H", data, opt)
    dd = opt + (112 if magic == 0x20B else 96)  # data directories
    imp_rva, _imp_size = struct.unpack_from("<II", data, dd + 8)
    sec = opt + opt_size
    secs = []
    for i in range(n_sections):
        o = sec + 40 * i
        vsize, va, rawsize, rawptr = struct.unpack_from("<IIII", data, o + 8)
        secs.append((va, max(vsize, rawsize), rawptr))

    def off(rva: int) -> int:
        for va, size, raw in secs:
            if va <= rva < va + size:
                return rva - va + raw
        raise ValueError(f"RVA {rva:#x} outside the sections")

    names = []
    if imp_rva:
        p = off(imp_rva)
        while True:
            fields = struct.unpack_from("<IIIII", data, p)
            if not any(fields):
                break
            n = off(fields[3])
            names.append(data[n : data.index(b"\0", n)].decode())
            p += 20
    return names


def check_windows(lib: Path) -> list[str]:
    names = pe_imports(lib.read_bytes())
    print(f"  imports: {', '.join(names)}")
    return [f"needs {n}, not on a clean Windows" for n in names if WINDOWS_REJECTED.match(n)]


# ---------------------------------------------------------------------- Mach-O
def check_macos(lib: Path, max_macos: str) -> list[str]:
    out = subprocess.run(["otool", "-L", str(lib)], capture_output=True, text=True, check=True)
    deps = [ln.strip().split(" (")[0] for ln in out.stdout.splitlines()[1:] if ln.strip()]
    print(f"  links: {', '.join(deps)}")
    errors = [
        f"unexpected dependency {d}"
        for d in deps
        if not (d.startswith(("/usr/lib/", "/System/")) or d.endswith("libadvtg_native.dylib"))
    ]
    load = subprocess.run(["otool", "-l", str(lib)], capture_output=True, text=True, check=True)
    mins = re.findall(r"(?:minos|version)\s+(\d+(?:\.\d+)*)\s*\n\s*sdk", load.stdout)
    if not mins:
        errors.append("minimum macOS version not found (LC_BUILD_VERSION)")
    for v in mins:
        print(f"  minimum macOS: {v}")
        if _ver_key(v) > _ver_key(max_macos):
            errors.append(f"minimum macOS {v} > {max_macos} (set CMAKE_OSX_DEPLOYMENT_TARGET)")
    return errors


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("path", type=Path, help="library, or a directory / .app that contains it")
    p.add_argument("--max-glibc", default="2.34")
    p.add_argument("--max-macos", default="11.0")
    a = p.parse_args(argv)
    lib = find_library(a.path)
    if lib is None:
        print(f"ERROR: no native engine library under {a.path}", file=sys.stderr)
        return 2
    print(f"native engine: {lib} ({lib.stat().st_size / 1024:.0f} KiB)")
    head = lib.read_bytes()[:4]
    if head == b"\x7fELF":
        errors = check_linux(lib, a.max_glibc)
    elif head[:2] == b"MZ":
        errors = check_windows(lib)
    else:
        errors = check_macos(lib, a.max_macos)
    for e in errors:
        print(f"ERROR: {e}", file=sys.stderr)
    if not errors:
        print("OK: self-contained")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
