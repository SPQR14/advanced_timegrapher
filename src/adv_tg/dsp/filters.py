"""IIR filter design without SciPy (numpy only), identical to ``scipy.signal``.

Only what the application needs: Butterworth band-pass filters as second-order sections
(``butter_bandpass_sos``), their steady-state initial conditions (``sosfilt_zi``) and the
default padding of a zero-phase filter (``sosfiltfilt_padlen``). The filtering itself is done
in vectorised numpy (``adv_tg.dsp.engine``).

Each function is a line-by-line port of SciPy 1.18 (``buttap``, ``lp2bp_zpk``,
``bilinear_zpk``, ``zpk2sos`` with the default ``"nearest"`` pairing, ``_cplxreal``,
``sosfilt_zi``/``lfilter_zi``, the ``ntaps`` rule of ``sosfiltfilt``), so the coefficients are
the same as SciPy's to machine precision; ``tests/test_dsp.py`` checks the frequency response
and the filtering against plain-Python reference recursions (SciPy is not used anywhere).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

__all__ = ["butter_bandpass_sos", "sosfilt_zi", "sosfiltfilt_padlen"]


# ---------------------------------------------------------------------- zpk helpers
def _buttap(n: int) -> tuple[NDArray, NDArray, float]:
    m = np.arange(-n + 1, n, 2, dtype=np.float64)
    return np.array([], dtype=np.float64), -np.exp(1j * np.pi * m / (2 * n)), 1.0


def _lp2bp_zpk(z: NDArray, p: NDArray, k: float, wo: float, bw: float) -> tuple:
    degree = p.size - z.size
    z_lp = (z * bw / 2).astype(np.complex128)
    p_lp = (p * bw / 2).astype(np.complex128)
    z_bp = np.concatenate((z_lp + np.sqrt(z_lp**2 - wo**2), z_lp - np.sqrt(z_lp**2 - wo**2)))
    p_bp = np.concatenate((p_lp + np.sqrt(p_lp**2 - wo**2), p_lp - np.sqrt(p_lp**2 - wo**2)))
    z_bp = np.concatenate((z_bp, np.zeros(degree)))
    return z_bp, p_bp, k * bw**degree


def _bilinear_zpk(z: NDArray, p: NDArray, k: float, fs: float) -> tuple:
    degree = p.size - z.size
    fs2 = 2.0 * fs
    z_z = (fs2 + z) / (fs2 - z)
    p_z = (fs2 + p) / (fs2 - p)
    z_z = np.concatenate((z_z, -np.ones(degree)))
    k_z = k * np.real(np.prod(fs2 - z) / np.prod(fs2 - p))
    return z_z, p_z, k_z


def _cplxreal(z: NDArray) -> tuple[NDArray, NDArray]:
    z = np.atleast_1d(z)
    if z.size == 0:
        return z, z
    tol = 100 * np.finfo((1.0 * z).dtype).eps
    z = z[np.lexsort((abs(z.imag), z.real))]
    real_indices = abs(z.imag) <= tol * abs(z)
    zr = z[real_indices].real
    if len(zr) == len(z):
        return np.array([]), zr
    z = z[~real_indices]
    zp = z[z.imag > 0]
    zn = z[z.imag < 0]
    if len(zp) != len(zn):
        raise ValueError("valor complejo sin su conjugado")
    same_real = np.diff(zp.real) <= tol * abs(zp[:-1])
    diffs = np.diff(np.concatenate(([0], same_real, [0])))
    run_starts = np.nonzero(diffs > 0)[0]
    run_stops = np.nonzero(diffs < 0)[0]
    for i in range(len(run_starts)):
        start = run_starts[i]
        stop = run_stops[i] + 1
        for chunk in (zp[start:stop], zn[start:stop]):
            chunk[...] = chunk[np.lexsort([abs(chunk.imag)])]
    if any(abs(zp - zn.conj()) > tol * abs(zn)):
        raise ValueError("valor complejo sin su conjugado")
    return (zp + zn.conj()) / 2, zr


def _nearest_real_complex_idx(fro: NDArray, to: complex, which: str) -> int:
    order = np.argsort(np.abs(fro - to))
    if which == "any":
        return int(order[0])
    mask = np.isreal(fro[order])
    if which == "complex":
        mask = ~mask
    return int(order[np.nonzero(mask)[0][0]])


def _single_zpksos(z: list, p: list, k: float) -> NDArray:
    sos = np.zeros(6)
    b = k * np.poly(z) if len(z) else np.array([k], dtype=np.float64)
    a = np.poly(p) if len(p) else np.array([1.0])
    b = np.atleast_1d(b)
    a = np.atleast_1d(a)
    sos[3 - len(b) : 3] = np.real(b) if np.all(np.isreal(b)) else b
    sos[6 - len(a) : 6] = np.real(a) if np.all(np.isreal(a)) else a
    return sos


def _zpk2sos_nearest(z: NDArray, p: NDArray, k: float) -> NDArray:
    """``scipy.signal.zpk2sos(z, p, k)`` for a digital filter (pairing ``"nearest"``)."""
    z = np.asarray(z)
    p = np.asarray(p)
    if len(z) == len(p) == 0:
        return np.array([[k, 0.0, 0.0, 1.0, 0.0, 0.0]])
    p = np.concatenate((p, np.zeros(max(len(z) - len(p), 0))))
    z = np.concatenate((z, np.zeros(max(len(p) - len(z), 0))))
    n_sections = (max(len(p), len(z)) + 1) // 2
    if len(p) % 2 == 1:
        p = np.concatenate((p, [0.0]))
        z = np.concatenate((z, [0.0]))
    z = np.concatenate(_cplxreal(z))
    p = np.concatenate(_cplxreal(p))
    k = float(np.real(k))

    def idx_worst(q: NDArray) -> int:
        return int(np.argmin(np.abs(1 - np.abs(q))))

    sos = np.zeros((n_sections, 6))
    for si in range(n_sections - 1, -1, -1):
        p1_idx = idx_worst(p)
        p1 = p[p1_idx]
        p = np.delete(p, p1_idx)
        if np.isreal(p1) and np.isreal(p).sum() == 0:
            z1_idx = _nearest_real_complex_idx(z, p1, "real")
            z1 = z[z1_idx]
            z = np.delete(z, z1_idx)
            sos[si] = _single_zpksos([z1, 0], [p1, 0], 1)
        elif (
            len(p) + 1 == len(z)
            and not np.isreal(p1)
            and np.isreal(p).sum() == 1
            and np.isreal(z).sum() == 1
        ):
            z1_idx = _nearest_real_complex_idx(z, p1, "complex")
            z1 = z[z1_idx]
            z = np.delete(z, z1_idx)
            sos[si] = _single_zpksos([z1, z1.conj()], [p1, p1.conj()], 1)
        else:
            if np.isreal(p1):
                prealidx = np.flatnonzero(np.isreal(p))
                p2_idx = prealidx[idx_worst(p[prealidx])]
                p2 = p[p2_idx]
                p = np.delete(p, p2_idx)
            else:
                p2 = p1.conj()
            if len(z) > 0:
                z1_idx = _nearest_real_complex_idx(z, p1, "any")
                z1 = z[z1_idx]
                z = np.delete(z, z1_idx)
                if not np.isreal(z1):
                    sos[si] = _single_zpksos([z1, z1.conj()], [p1, p2], 1)
                elif len(z) > 0:
                    z2_idx = _nearest_real_complex_idx(z, p1, "real")
                    z2 = z[z2_idx]
                    z = np.delete(z, z2_idx)
                    sos[si] = _single_zpksos([z1, z2], [p1, p2], 1)
                else:
                    sos[si] = _single_zpksos([z1], [p1, p2], 1)
            else:
                sos[si] = _single_zpksos([], [p1, p2], 1)
    sos[0][:3] *= k
    return sos


# ---------------------------------------------------------------------- public
def butter_bandpass_sos(order: int, band_hz: tuple[float, float], fs: float) -> NDArray:
    """``scipy.signal.butter(order, band_hz, "bandpass", fs=fs, output="sos")``.

    Raises:
        ValueError: Invalid order or band (``0 < low < high < fs/2``).
    """
    if int(order) != order or order < 1:
        raise ValueError(f"orden de filtro no válido: {order}")
    lo, hi = (float(band_hz[0]), float(band_hz[1]))
    wn = np.array([lo, hi]) / (float(fs) / 2)
    if not (0 < wn[0] < wn[1] < 1):
        raise ValueError(f"banda no válida para fs={fs}: {band_hz}")
    fs_pre = 2.0
    warped = 2 * fs_pre * np.tan(np.pi * wn / fs_pre)
    bw = warped[1] - warped[0]
    wo = np.sqrt(warped[0] * warped[1])
    z, p, k = _buttap(int(order))
    z, p, k = _lp2bp_zpk(z, p, k, wo=float(wo), bw=float(bw))
    z, p, k = _bilinear_zpk(z, p, k, fs=fs_pre)
    return _zpk2sos_nearest(z, p, k)


def _lfilter_zi(b: NDArray, a: NDArray) -> NDArray:
    if a[0] == 0:
        raise ValueError("el primer coeficiente de a no puede ser 0")
    if a[0] != 1:
        b, a = b / a[0], a / a[0]
    sum_a = np.sum(a)
    if sum_a == 0:
        raise ValueError("filtro inestable: polo en z = 1")
    y_inf = np.sum(b) / sum_a
    n = max(a.size, b.size)
    b = np.pad(b, (0, n - b.size))
    a = np.pad(a, (0, n - a.size))
    return np.flip(np.cumsum(np.flip(b - y_inf * a)))[1:]


def sosfilt_zi(sos: NDArray) -> NDArray:
    """``scipy.signal.sosfilt_zi(sos)``: step-response initial state of each section."""
    sos = np.asarray(sos, dtype=np.float64)
    if sos.ndim != 2 or sos.shape[1] != 6:
        raise ValueError("sos debe tener forma (n_secciones, 6)")
    zi = np.empty((sos.shape[0], 2))
    scale = 1.0
    for s in range(sos.shape[0]):
        b, a = sos[s, :3], sos[s, 3:]
        zi[s] = scale * _lfilter_zi(b, a)
        scale *= np.sum(b) / np.sum(a)
    return zi


def sosfiltfilt_padlen(sos: NDArray) -> int:
    """Default odd-extension length of ``scipy.signal.sosfiltfilt`` for ``sos``."""
    sos = np.asarray(sos)
    ntaps = 2 * sos.shape[0] + 1
    ntaps -= min(int((sos[:, 2] == 0).sum()), int((sos[:, 5] == 0).sum()))
    return 3 * ntaps
