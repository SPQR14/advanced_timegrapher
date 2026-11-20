"""DSP kernels of the analysis in pure Python + numpy (no SciPy, no compiled extensions).

Every function reproduces the ``scipy.signal`` behaviour the detector was designed with
(``sosfilt``, ``sosfiltfilt`` with odd padding, ``find_peaks`` without arguments, centred
``"same"`` convolutions), but is written so that the work is done by vectorised numpy/BLAS
calls instead of a per-sample Python loop:

* **IIR filtering (block state-space).** A biquad in transposed direct form II is the linear
  recurrence ``s[n+1] = A s[n] + B x[n]``, ``y[n] = b0 x[n] + C s[n]``. The signal is cut into
  blocks of ``_BLOCK`` samples; inside a block the zero-state output is a product with a
  precomputed lower-triangular Toeplitz matrix of the impulse response (one matrix product
  for ALL blocks), the state carried from block to block needs only ``n / _BLOCK`` steps of a
  2x2 recurrence (scalar Python floats), and its contribution is another matrix product. The
  result equals the sample-by-sample recursion to rounding (~1e-13 relative).
* **Envelope.** Hilbert FIR by direct (short kernel) or FFT convolution and the moving
  average by cumulative sums, both with the alignment of ``np.convolve(..., "same")``.
* **Peaks.** Local maxima with SciPy's plateau rule (middle sample, rounded down), vectorised.

Divisions are noise gated: a slope or denominator below ``GATE`` (1e-12) counts as zero.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from numpy.typing import NDArray

from adv_tg.dsp.filters import sosfilt_zi, sosfiltfilt_padlen

__all__ = [
    "GATE",
    "compute_cell_envelope",
    "detect_cell_candidates",
    "find_local_maxima",
    "sosfilt",
    "sosfiltfilt",
]

#: Noise gate of every division.
GATE = 1e-12

#: Block length of the state-space IIR filter (trade-off between the Toeplitz product,
#: O(n * _BLOCK), and the scalar recurrence between blocks, O(n / _BLOCK)).
_BLOCK = 64

#: Below this kernel length a direct ``np.convolve`` beats the FFT.
_FFT_MIN_KERNEL = 256


def _f64(a: NDArray) -> NDArray[np.float64]:
    return np.ascontiguousarray(a, dtype=np.float64)


def _check_sos(sos: NDArray) -> NDArray[np.float64]:
    sos = _f64(sos)
    if sos.ndim != 2 or sos.shape[1] != 6:
        raise ValueError("sos debe tener forma (n_secciones, 6)")
    if not np.all(np.abs(sos[:, 3]) > GATE):
        raise ValueError("sos: a0 es (casi) cero")
    return sos


# ---------------------------------------------------------------------- biquad kernels
@lru_cache(maxsize=64)
def _biquad_kernels(
    coeffs: tuple[float, float, float, float, float], block: int
) -> tuple[NDArray, NDArray, NDArray, tuple[float, float, float, float]]:
    """Precomputed matrices of one normalised biquad ``(b0, b1, b2, a1, a2)``.

    Returns ``(T, G, P, M)``:

    * ``T`` (block x block): ``Y_zs = X @ T`` is the zero-state output of each block.
    * ``G`` (block x 2): ``X @ G`` is the state reached at the end of a block from zero state.
    * ``P`` (2 x block): ``S0 @ P`` is the zero-input output of a block with initial state S0.
    * ``M``: ``A**block`` as ``(m00, m01, m10, m11)``.
    """
    b0, b1, b2, a1, a2 = coeffs
    A = np.array([[-a1, 1.0], [-a2, 0.0]])
    B = np.array([b1 - a1 * b0, b2 - a2 * b0])
    # powers of A applied to B (impulse response) and C A^n (zero-input response)
    h = np.empty(block)
    P = np.empty((2, block))
    AnB = B.copy()
    CAn = np.array([1.0, 0.0])
    h[0] = b0
    for n in range(block):
        P[:, n] = CAn
        if n + 1 < block:
            h[n + 1] = AnB[0]  # C A^n B
        CAn = CAn @ A
        AnB = A @ AnB
    # Toeplitz T[k, n] = h[n - k] for n >= k
    idx = np.arange(block)
    lag = idx[None, :] - idx[:, None]
    T = np.where(lag >= 0, h[np.clip(lag, 0, None)], 0.0)
    # G[k] = A^(block-1-k) B  -> rows built backwards
    G = np.empty((block, 2))
    v = B.copy()
    for k in range(block - 1, -1, -1):
        G[k] = v
        v = A @ v
    M = np.linalg.matrix_power(A, block)
    return T, G, P, (float(M[0, 0]), float(M[0, 1]), float(M[1, 0]), float(M[1, 1]))


def _sosfilt_core(
    sos: NDArray[np.float64], x: NDArray[np.float64], zi: NDArray | None
) -> NDArray[np.float64]:
    """Cascade all biquad sections reusing a single pair of ping-pong ``(nb, _BLOCK)`` buffers."""
    n = x.size
    if n == 0:
        return np.zeros(0, dtype=np.float64)
    L = _BLOCK
    nb = -(-n // L)
    rem = nb * L - n
    X = np.empty(nb * L, dtype=np.float64)
    X[:n] = x
    if rem:
        X[n:] = 0.0
    X2 = X.reshape(nb, L)
    Y2 = np.empty_like(X2)
    U = np.empty((nb, 2), dtype=np.float64)
    S = np.empty((nb, 2), dtype=np.float64)
    n_sec = sos.shape[0]
    for s in range(n_sec):
        b0, b1, b2, a0, a1, a2 = (float(v) for v in sos[s])
        coeffs = (b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)
        T, G, P, (m00, m01, m10, m11) = _biquad_kernels(coeffs, L)
        np.matmul(X2, T, out=Y2)
        np.matmul(X2, G, out=U)
        z1, z2 = (0.0, 0.0) if zi is None else (float(zi[s, 0]), float(zi[s, 1]))
        u1 = U[:, 0].tolist()
        u2 = U[:, 1].tolist()
        s1l = [0.0] * nb
        s2l = [0.0] * nb
        for b in range(nb):
            s1l[b] = z1
            s2l[b] = z2
            z1, z2 = m00 * z1 + m01 * z2 + u1[b], m10 * z1 + m11 * z2 + u2[b]
        S[:, 0] = s1l
        S[:, 1] = s2l
        Y2 += S @ P
        X2, Y2 = Y2, X2
        if rem and s + 1 < n_sec:
            X2[-1, L - rem :] = 0.0
    return X2.reshape(-1)[:n]


# ---------------------------------------------------------------------- filters
def sosfilt(sos: NDArray, x: NDArray, zi: NDArray | None = None) -> NDArray[np.float64]:
    """``scipy.signal.sosfilt(sos, x, zi=zi)[0]`` for 1-D ``x`` (zero state by default)."""
    sos = _check_sos(sos)
    x = _f64(x)
    if x.size == 0:
        return np.zeros(0)
    if zi is not None:
        zi = _f64(zi)
        if zi.shape != (sos.shape[0], 2):
            raise ValueError("zi debe tener forma (n_secciones, 2)")
    return _sosfilt_core(sos, x, zi)


def _odd_ext(x: NDArray[np.float64], n: int) -> NDArray[np.float64]:
    if n < 1:
        return x
    left = 2.0 * x[0] - x[n:0:-1]
    right = 2.0 * x[-1] - x[-2 : -(n + 2) : -1]
    return np.concatenate((left, x, right))


def _filtfilt(
    sos: NDArray[np.float64], x: NDArray[np.float64], zi: NDArray[np.float64], edge: int
) -> NDArray[np.float64]:
    ext = _odd_ext(x, edge)
    y = _sosfilt_core(sos, ext, zi * ext[0])
    y = _sosfilt_core(sos, y[::-1].copy(), zi * y[-1])[::-1]
    return y[edge : edge + x.size] if edge > 0 else y


def sosfiltfilt(
    sos: NDArray, x: NDArray, zi: NDArray | None = None, padlen: int | None = None
) -> NDArray[np.float64]:
    """``scipy.signal.sosfiltfilt(sos, x, padlen=padlen)`` (odd padding, 1-D).

    ``zi`` may pass a precomputed ``sosfilt_zi(sos)`` to save its computation.

    Raises:
        ValueError: ``x`` not longer than the padding.
    """
    sos = _check_sos(sos)
    x = _f64(x)
    edge = sosfiltfilt_padlen(sos) if padlen is None else int(padlen)
    if x.size <= edge:
        raise ValueError("la señal debe ser más larga que padlen")
    zi = sosfilt_zi(sos) if zi is None else _f64(zi)
    return np.array(_filtfilt(sos, x, zi, edge))


# ---------------------------------------------------------------------- convolutions
def _convolve_same(x: NDArray[np.float64], k: NDArray[np.float64]) -> NDArray[np.float64]:
    """Centred convolution with the length of ``x`` (``oaconvolve(x, k, "same")``).

    Same as ``np.convolve(x, k, "same")`` when ``x`` is the longer input; FFT for long kernels.
    """
    n, m = x.size, k.size
    start = (m - 1) // 2
    if m < _FFT_MIN_KERNEL:
        return np.convolve(x, k, mode="full")[start : start + n]
    full = n + m - 1
    nfft = 1 << (full - 1).bit_length()
    y = np.fft.irfft(np.fft.rfft(x, nfft) * np.fft.rfft(k, nfft), nfft)
    return y[start : start + n]


def _moving_average_same(x: NDArray[np.float64], m: int) -> NDArray[np.float64]:
    """``np.convolve(x, np.full(m, 1/m), "same")`` with cumulative sums (O(n))."""
    n = x.size
    if m <= 1:
        return x.copy()
    if n < m:
        return np.convolve(x, np.full(m, 1.0 / m), mode="full")[(m - 1) // 2 : (m - 1) // 2 + n]
    c = np.concatenate(([0.0], np.cumsum(x)))
    i = np.arange(n) + (m - 1) // 2  # index in the "full" convolution
    hi = np.minimum(i + 1, n)
    lo = np.maximum(i - m + 1, 0)
    return (c[hi] - c[lo]) / m


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

    ``smooth`` must be a uniform kernel (``1/len`` each); only its length is used.
    """
    x = _f64(x)
    if not (0 <= crop_start <= crop_end <= x.size):
        raise ValueError("crop indices fuera de límites")
    if smooth.size < 1:
        raise ValueError("smooth_len debe ser >= 1")
    if x.size <= padlen + 1:
        return np.zeros(crop_end - crop_start)
    y = _filtfilt(_check_sos(sos), x, _f64(zi), int(padlen))
    h = _convolve_same(y, _f64(hk))
    e = np.sqrt(y * y + h * h)
    e = _moving_average_same(e, int(smooth.size))
    return np.array(e[crop_start:crop_end])


def _find_local_maxima_runs(
    x: NDArray[np.float64],
) -> tuple[NDArray[np.intp], NDArray[np.intp], NDArray[np.intp]]:
    """Return ``(midpoints, run_starts, run_ends)`` of all strict local maxima of ``x``."""
    n = x.size
    empty = np.zeros(0, dtype=np.intp)
    if n < 3:
        return empty, empty, empty
    change = np.flatnonzero(x[1:] != x[:-1]) + 1
    starts = np.concatenate(([0], change))  # runs of equal values
    ends = np.concatenate((change - 1, [n - 1]))
    v = x[starts]
    if v.size < 3:
        return empty, empty, empty
    peak = (v[1:-1] > v[:-2]) & (v[1:-1] > v[2:])
    k = np.flatnonzero(peak) + 1
    rs = starts[k].astype(np.intp, copy=False)
    re = ends[k].astype(np.intp, copy=False)
    return ((rs + re) // 2).astype(np.intp), rs, re


def find_local_maxima(x: NDArray[np.float64]) -> NDArray[np.intp]:
    """``scipy.signal.find_peaks(x)[0]``: strict local maxima, plateaus at their middle sample.

    A plateau touching either end of ``x`` is not a peak (as in SciPy).
    """
    return _find_local_maxima_runs(x)[0]


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
    above_hi = env > thr_hi
    # Vectorised O(1) hysteresis pre-filter: drop runs that never cross threshold_hi
    cs_hi = np.empty(n + 1, dtype=np.intp)
    cs_hi[0] = 0
    np.cumsum(above_hi, out=cs_hi[1:])
    has_hi = (cs_hi[ends] - cs_hi[starts]) > 0
    if not has_hi.any():
        return [], [], [], []
    starts = starts[has_hi]
    ends = ends[has_hi]

    # One cell-wide peak pass instead of calling find_local_maxima per run; checking
    # run_start > s and run_end < t - 1 reproduces find_local_maxima(env[s:t]) exactly.
    all_peaks, all_rs, all_re = _find_local_maxima_runs(env)
    p_lo = np.searchsorted(all_peaks, starts + 1, side="left")
    p_hi = np.searchsorted(all_peaks, ends - 1, side="left")

    on_l: list[float] = []
    pk_l: list[float] = []
    fl_l: list[float] = []
    sp_l: list[float] = []
    min_sp_int = int(min_spacing_samples)
    for s, t, i0, i1 in zip(
        starts.tolist(), ends.tolist(), p_lo.tolist(), p_hi.tolist(), strict=True
    ):
        seg = env[s:t]
        if i1 > i0:
            pk_sub = all_peaks[i0:i1]
            interior = (all_rs[i0:i1] > s) & (all_re[i0:i1] < t - 1)
            peaks = pk_sub[interior] - s
        else:
            peaks = np.zeros(0, dtype=np.intp)
        if peaks.size == 0:
            peaks = np.array([int(seg.argmax())], dtype=np.intp)
        peaks = peaks[above_hi[s + peaks]]
        if peaks.size == 0:
            continue
        groups: list[list[int]] = []  # [peak index (rel to seg), valley index or -1]
        for p in peaks.tolist():
            if not groups:
                groups.append([p, -1])
                continue
            cur = groups[-1]
            v_rel = cur[0] + int(seg[cur[0] : p + 1].argmin())
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
            j0 = s + v_rel if v_rel >= 0 else max(0, s - min_sp_int)
            below = np.flatnonzero(env[j0:i] < level)
            if below.size == 0:
                onset = float(j0)
            else:
                j = j0 + int(below[-1])
                dd = env[j + 1] - env[j]
                onset = j + ((level - env[j]) / dd if dd > GATE else 0.0)
            o_abs = lo_abs + onset
            if cell_start_abs <= o_abs < cell_end_abs and o_abs >= run_start_abs:
                on_l.append(float(o_abs))
                pk_l.append(float(env[i]))
                fl_l.append(float(f[i]))
                sp_l.append(float(d[i]))
    return on_l, pk_l, fl_l, sp_l
