"""DSP without SciPy: numpy filter design and C++ kernels against the SciPy reference."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest
from scipy import signal

from adv_tg.core.beat_detector import _hilbert_fir
from adv_tg.dsp import engine
from adv_tg.dsp.engine import (
    GATE,
    DspBackendError,
    compute_cell_envelope,
    detect_cell_candidates,
    is_native_dsp_available,
    sosfilt,
    sosfiltfilt,
)
from adv_tg.dsp.filters import butter_bandpass_sos, sosfilt_zi, sosfiltfilt_padlen

native = pytest.mark.skipif(
    not is_native_dsp_available(), reason="extensión C++ no compilada (pip install -e .)"
)
FS = 48000


@pytest.mark.parametrize("fs", [44100, 48000, 96000, 192000])
@pytest.mark.parametrize("order", [1, 2, 4, 6])
@pytest.mark.parametrize("band", [(1000, 15000), (500, 4000), (2000, 20000)])
def test_filter_design_is_scipy(fs: int, order: int, band: tuple[float, float]) -> None:
    band = (band[0], min(band[1], 0.45 * fs))
    ref = signal.butter(order, band, "bandpass", fs=fs, output="sos")
    sos = butter_bandpass_sos(order, band, fs)
    np.testing.assert_array_equal(sos, ref)
    np.testing.assert_allclose(sosfilt_zi(sos), signal.sosfilt_zi(ref), rtol=0, atol=1e-12)
    ntaps = 2 * len(ref) + 1 - min((ref[:, 2] == 0).sum(), (ref[:, 5] == 0).sum())
    assert sosfiltfilt_padlen(sos) == 3 * ntaps


def test_filter_design_errors() -> None:
    with pytest.raises(ValueError):
        butter_bandpass_sos(0, (1000, 2000), FS)
    with pytest.raises(ValueError):
        butter_bandpass_sos(4, (3000, 2000), FS)
    with pytest.raises(ValueError):
        butter_bandpass_sos(4, (1000, 30000), FS)


@native
def test_native_filters_match_scipy() -> None:
    rng = np.random.default_rng(1)
    x = rng.standard_normal(20000)
    sos = butter_bandpass_sos(4, (1000, 15000), FS)
    np.testing.assert_allclose(sosfilt(sos, x), signal.sosfilt(sos, x), rtol=0, atol=1e-10)
    np.testing.assert_allclose(sosfiltfilt(sos, x), signal.sosfiltfilt(sos, x), rtol=0, atol=1e-10)
    with pytest.raises(ValueError):
        sosfiltfilt(sos, x[:10])  # shorter than the padding
    bad = sos.copy()
    bad[0, 3] = 0.0  # a0 = 0: rejected by the noise gate instead of dividing by zero
    with pytest.raises(ValueError):
        sosfilt(bad, x)


@native
def test_compute_cell_envelope_matches_scipy() -> None:
    sos = butter_bandpass_sos(4, (1000, 15000), FS)
    zi = sosfilt_zi(sos)
    hk = _hilbert_fir(int(np.ceil(1.5 * FS / 1000.0)))
    smooth = np.full(15, 1.0 / 15)
    padlen = sosfiltfilt_padlen(sos)
    x = np.random.default_rng(123).standard_normal(13800)
    env = compute_cell_envelope(x, sos, zi, hk, smooth, 883, 12883, padlen)
    y = signal.sosfiltfilt(sos, x)
    h = signal.oaconvolve(y, hk, mode="same")
    ref = np.convolve(np.sqrt(y * y + h * h), smooth, mode="same")[883:12883]
    assert env.shape == (12000,)
    assert np.max(np.abs(env - ref)) < 1e-10


def _candidates(env: np.ndarray, f: np.ndarray, d: np.ndarray) -> tuple:
    return detect_cell_candidates(env, f, d, 4.0, 2.0, 48, 1.5, 96, 0.4, 0, env.size, 0, 0)


@native
def test_detect_cell_candidates_matches_fallback() -> None:
    rng = np.random.default_rng(42)
    env = np.abs(rng.standard_normal(12000))
    env[3000:3010] = 3.0  # a flat top (plateau): find_peaks takes its middle sample
    env[7000:7003] = [2.0, 2.0, 0.5]
    f = np.full(env.size, 0.2)
    d = np.full(env.size, 0.08)
    nat = _candidates(env, f, d)
    with patch.object(engine, "_HAS_NATIVE", False):
        ref = _candidates(env, f, d)
    assert len(nat[0]) == len(ref[0]) > 0
    for a, b in zip(nat, ref, strict=True):
        np.testing.assert_allclose(a, b, rtol=0, atol=1e-9)


@native
def test_no_local_maximum_uses_argmax_but_low_peaks_do_not() -> None:
    f = np.zeros(400)
    d = np.full(400, 0.1)
    ramp = np.zeros(400)
    ramp[100:200] = np.linspace(0.0, 1.0, 100)  # monotonic run: no local max, argmax used
    ramp[200:] = 1.0
    low = np.zeros(400)
    low[100:300] = 0.3  # above thr_lo (0.2) but under thr_hi (0.4) except one low bump
    low[150] = 0.35
    for env in (ramp, low):
        nat = _candidates(env, f, d)
        with patch.object(engine, "_HAS_NATIVE", False):
            ref = _candidates(env, f, d)
        assert nat == ref


def test_onset_slope_is_noise_gated() -> None:
    assert GATE == 1e-12
    env = np.zeros(300)
    env[100:200] = 1.0  # vertical edge: the slope before the crossing is gated, no 1/0
    f = np.zeros(300)
    d = np.full(300, 0.05)
    with patch.object(engine, "_HAS_NATIVE", False):
        out = _candidates(env, f, d)
    assert all(np.isfinite(v) for col in out for v in col)


def test_without_native_nor_scipy_the_error_is_clear(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def no_scipy(name, *a, **k):
        if name.startswith("scipy"):
            raise ImportError("sin scipy")
        return real_import(name, *a, **k)

    monkeypatch.setattr(engine, "_HAS_NATIVE", False)
    monkeypatch.setattr(builtins, "__import__", no_scipy)
    with pytest.raises(DspBackendError, match="extensión C\\+\\+"):
        sosfilt(butter_bandpass_sos(2, (1000, 5000), FS), np.zeros(100))


def test_fallback_matches_native_when_both_exist() -> None:
    sos = butter_bandpass_sos(4, (1000, 15000), FS)
    x = np.random.default_rng(3).standard_normal(5000)
    with patch.object(engine, "_HAS_NATIVE", False):
        ref = sosfiltfilt(sos, x)
    if is_native_dsp_available():
        np.testing.assert_allclose(sosfiltfilt(sos, x), ref, rtol=0, atol=1e-10)


@native
def test_native_dsp_source_hash_matches_cpp() -> None:
    """If native_dsp.cpp exists in the workspace, its SHA-256 must match the compiled module."""
    import hashlib
    from pathlib import Path

    from adv_tg.dsp import adv_tg_dsp_native

    cpp_path = Path(__file__).resolve().parent.parent / "src" / "adv_tg" / "dsp" / "native_dsp.cpp"
    if cpp_path.is_file():
        expected_sha = hashlib.sha256(cpp_path.read_bytes()).hexdigest()
        actual_sha = getattr(adv_tg_dsp_native, "SOURCE_SHA256", None)
        assert actual_sha == expected_sha, (
            f"El hash compilado ({actual_sha}) no coincide con el código fuente ({expected_sha}). "
            "Recompila la extensión con: pip install -e ."
        )


def test_template_lag_is_unbiased() -> None:
    """The refresh lag uses a normalised correlation: a template against itself (or shifted)
    gives the exact lag (the plain correlation gave -9 samples on a sloping envelope)."""
    from adv_tg.core.beat_detector import BeatDetector, BeatDetectorConfig, _Tpl

    det = BeatDetector(BeatDetectorConfig(sample_rate=FS, bph=28800, lift_angle_deg=51))
    n = det.tpl_pre + det.tpl_post
    i = np.arange(n, dtype=float)
    env = np.exp(-(((i - det.tpl_pre) / 40.0) ** 2)) + np.clip((i - det.tpl_pre) / 300.0, 0, 0.6)
    t = _Tpl(24)
    t.env = env
    assert det._refresh_lag(t) == 0.0  # first call: nothing to compare with
    t.env = env.copy()
    assert abs(det._refresh_lag(t)) < 0.05
    t.env = np.roll(env, 3)
    assert abs(det._refresh_lag(t) - 3.0) < 0.3


def test_onset_hold_blocks_jumps_and_accepts_persistent_moves() -> None:
    from adv_tg.core.beat_detector import BeatDetector, BeatDetectorConfig, _Tpl

    det = BeatDetector(BeatDetectorConfig(sample_rate=FS, bph=21600, lift_angle_deg=53))
    t = _Tpl(24)
    assert det._hold_onset(t, "p1", 100.0, 0.0) == 100.0
    assert det._hold_onset(t, "p1", 102.0, 0.0) == 102.0  # 2 samples < 0.12 ms: drift
    assert det._hold_onset(t, "p1", 145.0, 0.0) == 102.0  # 0.9 ms jump: held
    assert det._hold_onset(t, "p1", 102.5, 1.0) == 102.5  # back near the held point + lag
    for _ in range(det.config.template_hold_refreshes - 1):
        assert det._hold_onset(t, "p1", 150.0, 0.0) == 102.5
    assert det._hold_onset(t, "p1", 150.0, 0.0) == 150.0  # persisted: the sound changed
    assert t.hold_jumps == 2
