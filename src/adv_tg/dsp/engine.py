"""DSP kernels of the analysis: native C++ (``adv_tg_dsp_native``) with a SciPy fallback.

The application does not depend on SciPy: the packaged builds and any install with a C++
compiler use the native extension, and the filter DESIGN is numpy only
(``adv_tg.dsp.filters``). When the extension is missing (source install without a compiler),
these functions fall back to SciPy if it is installed (``pip install scipy``); otherwise they
raise ``DspBackendError`` with instructions.

Numerical contract: the native code reproduces the SciPy reference (envelope within 1e-10;
the same beats on the real recordings, see ``tests/test_native_dsp.py``). Divisions are noise
gated: a slope or denominator below ``GATE`` (1e-12) counts as zero, in C++ and here.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray

from adv_tg.dsp.filters import sosfilt_zi, sosfiltfilt_padlen
from adv_tg.utils.debugger import Debugger

__all__ = [
    "GATE",
    "DspBackendError",
    "compute_cell_envelope",
    "detect_cell_candidates",
    "is_native_dsp_available",
    "sosfilt",
    "sosfiltfilt",
]

_dbg = Debugger.get(__name__)

#: Noise gate of every division (same value as ``kGate`` in ``native_dsp.cpp``).
GATE = 1e-12

try:
    from adv_tg.dsp import adv_tg_dsp_native as _native  # type: ignore[attr-defined]

    _HAS_NATIVE = True
    _dbg.logger.info("adv_tg.dsp: extensión C++ cargada")
except ImportError as err:  # pragma: no cover - depends on the build
    _native = None
    _HAS_NATIVE = False
    _dbg.logger.warning("adv_tg.dsp: sin extensión C++ (%s); se usará SciPy si está instalado", err)


class DspBackendError(RuntimeError):
    """Neither the C++ extension nor SciPy is available (message in Spanish)."""


def is_native_dsp_available() -> bool:
    """True if the C++ extension is compiled and loaded."""
    return _HAS_NATIVE


def _scipy_signal() -> Any:
    try:
        from scipy import signal
    except ImportError as exc:
        raise DspBackendError(
            "Falta la extensión C++ de adv_tg (adv_tg.dsp.adv_tg_dsp_native) y SciPy no está "
            "instalado. Reinstala con un compilador de C++ (pip install -e .) o instala SciPy "
            "(pip install scipy)."
        ) from exc
    return signal


def _f64(a: NDArray) -> NDArray[np.float64]:
    return np.ascontiguousarray(a, dtype=np.float64)


# ---------------------------------------------------------------------- filters
def sosfilt(sos: NDArray, x: NDArray) -> NDArray[np.float64]:
    """``scipy.signal.sosfilt(sos, x)`` (zero initial state, 1-D)."""
    if _HAS_NATIVE:
        return _native.sosfilt(_f64(x), _f64(sos))
    return np.asarray(_scipy_signal().sosfilt(sos, x), dtype=np.float64)


def sosfiltfilt(
    sos: NDArray, x: NDArray, zi: NDArray | None = None, padlen: int | None = None
) -> NDArray[np.float64]:
    """``scipy.signal.sosfiltfilt(sos, x)`` (odd padding; default padlen as SciPy)."""
    edge = sosfiltfilt_padlen(sos) if padlen is None else int(padlen)
    if _HAS_NATIVE:
        zi = sosfilt_zi(sos) if zi is None else zi
        return _native.sosfiltfilt(_f64(x), _f64(sos), _f64(zi), edge)
    return np.asarray(_scipy_signal().sosfiltfilt(sos, x, padlen=edge), dtype=np.float64)


# ---------------------------------------------------------------------- detector kernels
def compute_cell_envelope(
    x: NDArray[np.float64],
    sos: NDArray[np.float64],
    zi: NDArray[np.float64],
    hk: NDArray[np.float64],
    smooth: NDArray[np.float64],
    crop_start: int,
    crop_end: int,
    padlen: int,
) -> NDArray[np.float64]:
    """Zero-phase band-pass, Hilbert FIR envelope and moving average, cropped.

    ``smooth`` must be a uniform kernel (``1/len`` each); the native code uses its length.
    """
    if _HAS_NATIVE:
        return _native.compute_cell_envelope(
            _f64(x),
            _f64(sos),
            _f64(zi),
            _f64(hk),
            int(smooth.size),
            int(crop_start),
            int(crop_end),
            int(padlen),
        )
    signal = _scipy_signal()
    if x.size > padlen + 1:
        y = signal.sosfiltfilt(sos, x, padlen=padlen)
        h = signal.oaconvolve(y, hk, mode="same")
        e = np.sqrt(y * y + h * h)
        e = np.convolve(e, smooth, mode="same")
    else:
        e = np.zeros(x.size, dtype=np.float64)
    return e[crop_start:crop_end]


def detect_cell_candidates(
    env: NDArray[np.float64],
    f: NDArray[np.float64],
    d: NDArray[np.float64],
    threshold_hi: float,
    threshold_lo: float,
    merge_samples: int,
    split_ratio: float,
    min_spacing_samples: float,
    onset_fraction: float,
    cell_start_abs: int,
    cell_end_abs: int,
    lo_abs: int,
    run_start_abs: int,
) -> tuple[list[float], list[float], list[float], list[float]]:
    """Sub-event candidates of one cell (hysteresis, split at valleys, sub-sample onsets).

    Returns ``(onsets, peaks, floors, spreads)`` in absolute samples / envelope units.
    """
    if _HAS_NATIVE:
        return _native.detect_cell_candidates(
            _f64(env),
            _f64(f),
            _f64(d),
            float(threshold_hi),
            float(threshold_lo),
            int(merge_samples),
            float(split_ratio),
            float(min_spacing_samples),
            float(onset_fraction),
            int(cell_start_abs),
            int(cell_end_abs),
            int(lo_abs),
            int(run_start_abs),
        )
    find_peaks = _scipy_signal().find_peaks
    n = env.size
    if n < 3:
        return [], [], [], []
    thr_hi = f + threshold_hi * d
    thr_lo = f + threshold_lo * d
    mask = env > thr_lo
    diff = np.diff(np.concatenate(([0], mask.view(np.int8), [0])))
    starts = np.flatnonzero(diff == 1)
    ends = np.flatnonzero(diff == -1)
    if starts.size == 0:
        return [], [], [], []
    if starts.size > 1:  # merge runs separated by less than merge_samples
        keep = np.r_[True, (starts[1:] - ends[:-1]) >= merge_samples]
        ends = np.maximum.reduceat(ends, np.flatnonzero(keep))
        starts = starts[keep]
    on_l: list[float] = []
    pk_l: list[float] = []
    fl_l: list[float] = []
    sp_l: list[float] = []
    for s, t in zip(starts, ends, strict=True):
        if not np.any(env[s:t] > thr_hi[s:t]):  # hysteresis: must reach the high threshold
            continue
        seg = env[s:t]
        peaks, _ = find_peaks(seg)
        if peaks.size == 0:
            peaks = np.array([int(np.argmax(seg))])
        peaks = peaks[seg[peaks] > thr_hi[s + peaks]]
        if peaks.size == 0:
            continue
        groups: list[list[int]] = []  # [peak index (rel to seg), valley index or -1]
        for p in peaks:
            p = int(p)
            if not groups:
                groups.append([p, -1])
                continue
            cur = groups[-1]
            v_rel = cur[0] + int(np.argmin(seg[cur[0] : p + 1]))
            valley = max(float(seg[v_rel]), float(f[s + p]))
            if seg[p] >= split_ratio * valley and (p - cur[0]) >= min_spacing_samples:
                groups.append([p, v_rel])
            elif seg[p] > seg[cur[0]]:
                cur[0] = p
        for p, v_rel in groups:
            i = s + p
            base = 0.0 if v_rel < 0 else max(float(seg[v_rel]), float(f[i]))
            level = base + onset_fraction * (float(env[i]) - base)
            level = max(level, float(f[i] + threshold_lo * d[i]))
            j0 = s + v_rel if v_rel >= 0 else max(0, s - int(min_spacing_samples))
            below = np.flatnonzero(env[j0:i] < level)
            if below.size == 0:
                onset = float(j0)
            else:
                j = j0 + int(below[-1])
                dd = env[j + 1] - env[j]
                onset = j + ((level - env[j]) / dd if dd > GATE else 0.0)
            o_abs = lo_abs + onset
            if cell_start_abs <= o_abs < cell_end_abs and o_abs >= run_start_abs:
                on_l.append(o_abs)
                pk_l.append(float(env[i]))
                fl_l.append(float(f[i]))
                sp_l.append(float(d[i]))
    return on_l, pk_l, fl_l, sp_l
