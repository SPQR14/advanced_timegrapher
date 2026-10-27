"""DSP in pure Python + numpy (no SciPy): filter design and vectorised kernels.

The references are deliberately naive (sample-by-sample recursions and loops) so that the
vectorised engine is checked against the textbook definition, not against itself.
"""

from __future__ import annotations

import numpy as np
import pytest

from adv_tg.core.beat_detector import _hilbert_fir
from adv_tg.dsp.engine import (
    GATE,
    compute_cell_envelope,
    detect_cell_candidates,
    find_local_maxima,
    sosfilt,
    sosfiltfilt,
)
from adv_tg.dsp.filters import butter_bandpass_sos, sosfilt_zi, sosfiltfilt_padlen

FS = 48000


# ---------------------------------------------------------------------- references
def _ref_sosfilt(sos: np.ndarray, x: np.ndarray, zi: np.ndarray | None = None) -> np.ndarray:
    """Transposed direct form II, one sample at a time (the definition of scipy's sosfilt)."""
    y = np.array(x, dtype=np.float64)
    for s in range(sos.shape[0]):
        b0, b1, b2, a0, a1, a2 = sos[s] / sos[s, 3]
        z1, z2 = (0.0, 0.0) if zi is None else (float(zi[s, 0]), float(zi[s, 1]))
        out = np.empty_like(y)
        for n, xn in enumerate(y):
            yn = b0 * xn + z1
            z1 = b1 * xn - a1 * yn + z2
            z2 = b2 * xn - a2 * yn
            out[n] = yn
        y = out
    return y


def _ref_sosfiltfilt(sos: np.ndarray, x: np.ndarray, padlen: int) -> np.ndarray:
    zi = sosfilt_zi(sos)
    left = 2 * x[0] - x[padlen:0:-1]
    right = 2 * x[-1] - x[-2 : -(padlen + 2) : -1]
    ext = np.concatenate((left, x, right))
    y = _ref_sosfilt(sos, ext, zi * ext[0])
    y = _ref_sosfilt(sos, y[::-1], zi * y[-1])[::-1]
    return y[padlen:-padlen]


def _ref_local_maxima(x: np.ndarray) -> list[int]:
    """Loop port of scipy's ``_local_maxima_1d`` (plateau -> middle sample)."""
    peaks = []
    i, i_max = 1, x.size - 1
    while i < i_max:
        if x[i - 1] < x[i]:
            ahead = i + 1
            while ahead < i_max and x[ahead] == x[i]:
                ahead += 1
            if x[ahead] < x[i]:
                peaks.append((i + ahead - 1) // 2)
                i = ahead
        i += 1
    return peaks


def _gain(sos: np.ndarray, f_hz: float, fs: float) -> float:
    z = np.exp(1j * 2 * np.pi * f_hz / fs)
    h = 1.0 + 0j
    for b0, b1, b2, a0, a1, a2 in sos:
        h *= (b0 + b1 / z + b2 / z**2) / (a0 + a1 / z + a2 / z**2)
    return float(abs(h))


# ---------------------------------------------------------------------- filter design
@pytest.mark.parametrize("fs", [44100, 48000, 96000, 192000])
@pytest.mark.parametrize("order", [1, 2, 4, 6])
@pytest.mark.parametrize("band", [(1000, 15000), (500, 4000), (2000, 20000)])
def test_butterworth_band_pass_response(fs: int, order: int, band: tuple[float, float]) -> None:
    lo, hi = band[0], min(band[1], 0.45 * fs)
    sos = butter_bandpass_sos(order, (lo, hi), fs)
    assert sos.shape == (order, 6)
    for s in sos:  # stable: every pole inside the unit circle
        assert np.all(np.abs(np.roots(s[3:])) < 1.0)
    assert _gain(sos, lo, fs) == pytest.approx(2**-0.5, rel=1e-6)  # -3 dB at both edges
    assert _gain(sos, hi, fs) == pytest.approx(2**-0.5, rel=1e-6)
    assert _gain(sos, np.sqrt(lo * hi), fs) <= 1.0 + 1e-9  # maximally flat: never above 1
    zi = sosfilt_zi(sos)
    step = _ref_sosfilt(sos, np.ones(50), zi)
    np.testing.assert_allclose(step, 0.0, atol=1e-9)  # steady state of a DC step (DC gain 0)
    ntaps = 2 * order + 1 - min((sos[:, 2] == 0).sum(), (sos[:, 5] == 0).sum())
    assert sosfiltfilt_padlen(sos) == 3 * ntaps


def test_filter_design_errors() -> None:
    with pytest.raises(ValueError):
        butter_bandpass_sos(0, (1000, 2000), FS)
    with pytest.raises(ValueError):
        butter_bandpass_sos(4, (3000, 2000), FS)
    with pytest.raises(ValueError):
        butter_bandpass_sos(4, (1000, 30000), FS)


# ---------------------------------------------------------------------- filtering
@pytest.mark.parametrize(
    ("band", "order", "fs", "n"),
    [
        ((1000, 15000), 4, 48000, 5001),
        ((500, 4000), 6, 192000, 4096),
        ((100, 300), 4, 48000, 3000),  # poles close to the unit circle
        ((1000, 15000), 2, 48000, 7),  # shorter than one block
    ],
)
def test_sosfilt_matches_the_recursion(band, order, fs, n) -> None:
    sos = butter_bandpass_sos(order, band, fs)
    x = np.random.default_rng(1).standard_normal(n)
    ref = _ref_sosfilt(sos, x)
    np.testing.assert_allclose(sosfilt(sos, x), ref, rtol=0, atol=1e-10 * max(1, np.abs(ref).max()))
    zi = sosfilt_zi(sos) * 0.7
    np.testing.assert_allclose(sosfilt(sos, x, zi), _ref_sosfilt(sos, x, zi), rtol=0, atol=1e-10)


def test_sosfiltfilt_matches_the_recursion() -> None:
    sos = butter_bandpass_sos(4, (1000, 15000), FS)
    x = np.random.default_rng(2).standard_normal(3000)
    padlen = sosfiltfilt_padlen(sos)
    ref = _ref_sosfiltfilt(sos, x, padlen)
    np.testing.assert_allclose(sosfiltfilt(sos, x), ref, rtol=0, atol=1e-10)
    np.testing.assert_allclose(sosfiltfilt(sos, x, sosfilt_zi(sos)), ref, rtol=0, atol=1e-10)


def test_filter_errors() -> None:
    sos = butter_bandpass_sos(4, (1000, 15000), FS)
    with pytest.raises(ValueError):
        sosfiltfilt(sos, np.zeros(10))  # shorter than the padding
    bad = sos.copy()
    bad[0, 3] = 0.0  # a0 = 0: rejected by the noise gate instead of dividing by zero
    with pytest.raises(ValueError):
        sosfilt(bad, np.zeros(100))
    with pytest.raises(ValueError):
        sosfilt(sos[:, :5], np.zeros(100))
    assert sosfilt(sos, np.zeros(0)).size == 0


# ---------------------------------------------------------------------- envelope
@pytest.mark.parametrize(("half", "smooth_len"), [(72, 15), (72, 8), (400, 7)])
def test_compute_cell_envelope_matches_definition(half: int, smooth_len: int) -> None:
    sos = butter_bandpass_sos(4, (1000, 15000), FS)
    zi = sosfilt_zi(sos)
    hk = _hilbert_fir(half)  # 400 -> FFT convolution path
    smooth = np.full(smooth_len, 1.0 / smooth_len)
    padlen = sosfiltfilt_padlen(sos)
    x = np.random.default_rng(123).standard_normal(4000)
    env = compute_cell_envelope(x, sos, zi, hk, smooth, 300, 3700, padlen)
    y = _ref_sosfiltfilt(sos, x, padlen)
    h = np.convolve(y, hk, mode="same")
    ref = np.convolve(np.sqrt(y * y + h * h), smooth, mode="same")[300:3700]
    assert env.shape == (3400,)
    assert np.max(np.abs(env - ref)) < 1e-10


def test_compute_cell_envelope_short_input_and_errors() -> None:
    sos = butter_bandpass_sos(4, (1000, 15000), FS)
    zi = sosfilt_zi(sos)
    hk = _hilbert_fir(72)
    smooth = np.full(5, 0.2)
    padlen = sosfiltfilt_padlen(sos)
    out = compute_cell_envelope(np.ones(padlen), sos, zi, hk, smooth, 0, padlen, padlen)
    np.testing.assert_array_equal(out, np.zeros(padlen))
    with pytest.raises(ValueError):
        compute_cell_envelope(np.ones(100), sos, zi, hk, smooth, 50, 200, padlen)


# ---------------------------------------------------------------------- peaks / candidates
def test_find_local_maxima_matches_the_loop() -> None:
    rng = np.random.default_rng(7)
    for _ in range(3000):
        x = rng.integers(0, 4, size=int(rng.integers(0, 40))).astype(float)
        assert find_local_maxima(x).tolist() == _ref_local_maxima(x), x


def _candidates(env: np.ndarray, f: np.ndarray, d: np.ndarray) -> tuple:
    return detect_cell_candidates(env, f, d, 4.0, 2.0, 48, 1.5, 96, 0.4, 0, env.size, 0, 0)


def test_detect_cell_candidates_plateau_and_split() -> None:
    env = np.full(2000, 0.1)
    env[300:310] = 3.0  # flat top: its middle sample is the peak
    env[600:603] = [1.0, 2.0, 1.0]
    env[603:700] = 0.5  # valley, then a second sub-event far enough to split
    env[700:703] = [1.0, 2.5, 1.0]
    f = np.full(env.size, 0.2)
    d = np.full(env.size, 0.08)
    on, pk, fl, sp = _candidates(env, f, d)
    assert pk == [3.0, 2.0, 2.5]
    assert len(on) == len(fl) == len(sp) == 3
    assert on[0] == pytest.approx(299.0, abs=1.0)


def test_onset_slope_is_noise_gated() -> None:
    assert GATE == 1e-12
    env = np.zeros(300)
    env[100:200] = 1.0  # vertical edge: the slope before the crossing is gated, no 1/0
    f = np.zeros(300)
    d = np.full(300, 0.05)
    out = _candidates(env, f, d)
    assert all(np.isfinite(v) for col in out for v in col)


def test_analysis_never_imports_scipy() -> None:
    """The measurement chain must run with numpy alone (SciPy is not a dependency)."""
    import subprocess
    import sys

    code = (
        "import sys\n"
        "import adv_tg.dsp, adv_tg.core.beat_detector, adv_tg.core.session, adv_tg.core.metrics\n"
        "import adv_tg.audio.analysis_process\n"
        "bad = [m for m in sys.modules if m == 'scipy' or m.startswith('scipy.')]\n"
        "sys.exit('scipy importado: %s' % bad if bad else 0)\n"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr + res.stdout
