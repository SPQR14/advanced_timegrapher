"""Parity and SIMD-dispatch tests for Step 2 native C++20 DSP kernels."""

from __future__ import annotations

import os
import subprocess
import sys

import numpy as np
import pytest

from adv_tg import native_bridge as _nb
from adv_tg.dsp import engine as eng
from adv_tg.dsp.filters import butter_bandpass_sos, sosfilt_zi, sosfiltfilt_padlen
from adv_tg.dsp.interference import InterferenceFilter
from adv_tg.dsp.normalizer import AudioNormalizer, NormalizerConfig
from adv_tg.dsp.onset import onset_after_quiet, robust_level


def _run_py(code: str) -> str:
    env = os.environ.copy()
    env["ADV_TG_DISABLE_NATIVE"] = "1"
    return subprocess.check_output([sys.executable, "-c", code], env=env, text=True).strip()


def test_rfft_irfft_parity() -> None:
    if not _nb.have("advtg_rfft", "advtg_irfft"):
        pytest.skip("native FFT symbols absent")

    rng = np.random.default_rng(7)
    for nfft in (1, 2, 64, 512, 2048, 4096):
        x = rng.standard_normal(nfft)
        spec_c = np.empty(nfft // 2 + 1, dtype=np.complex128)
        assert _nb.lib().advtg_rfft(_nb.ptr(x), x.size, nfft, _nb.ptr(spec_c)) == 0
        np.testing.assert_allclose(spec_c, np.fft.rfft(x, nfft), rtol=1e-11, atol=1e-11)
        x_back = np.empty(nfft, dtype=np.float64)
        assert _nb.lib().advtg_irfft(_nb.ptr(spec_c), nfft, _nb.ptr(x_back)) == 0
        np.testing.assert_allclose(x_back, np.fft.irfft(spec_c, nfft), rtol=1e-11, atol=1e-11)


def test_compute_cell_envelope_and_candidates_parity() -> None:
    if not _nb.is_native_available():
        pytest.skip("native engine not loaded")
    fs = 48000.0
    sos = butter_bandpass_sos(4, (1200.0, 12000.0), fs)
    zi = sosfilt_zi(sos)
    pad = sosfiltfilt_padlen(sos)
    half = 128
    m = np.arange(-half, half + 1, dtype=np.float64)
    hk = np.zeros_like(m)
    odd = (m.astype(np.int64) & 1) == 1
    hk[odd] = (2.0 / (np.pi * m[odd])) * np.blackman(m.size)[odd]
    smooth = np.full(13, 1.0 / 13.0)

    rng = np.random.default_rng(19)
    x = 0.02 * rng.standard_normal(4000)
    t = np.arange(80)
    x[1200:1280] += 0.6 * np.exp(-t / 15.0) * np.sin(2.0 * np.pi * 4200.0 * t / fs)
    x[2600:2680] += 0.45 * np.exp(-t / 12.0) * np.sin(2.0 * np.pi * 3800.0 * t / fs)

    env_c = eng.compute_cell_envelope(x, sos, zi, hk, smooth, 400, 3600, pad)
    f = np.full_like(env_c, 0.015)
    d = np.full_like(env_c, 0.005)
    c_on, c_pk, c_fl, c_sp = eng.detect_cell_candidates(
        env_c, f, d, 5.0, 2.0, 20, 1.8, 30.0, 0.2, 400, 3600, 400, 400
    )

    py_out = _run_py(
        "import json, numpy as np\n"
        "from adv_tg.dsp import engine as eng\n"
        "from adv_tg.dsp.filters import butter_bandpass_sos, sosfilt_zi, sosfiltfilt_padlen\n"
        "fs = 48000.0; sos = butter_bandpass_sos(4, (1200.0, 12000.0), fs); zi = sosfilt_zi(sos); pad = sosfiltfilt_padlen(sos)\n"
        "m = np.arange(-128, 129, dtype=np.float64); hk = np.zeros_like(m); odd = (m.astype(np.int64) & 1) == 1\n"
        "hk[odd] = (2.0 / (np.pi * m[odd])) * np.blackman(m.size)[odd]\n"
        "smooth = np.full(13, 1.0 / 13.0)\n"
        "rng = np.random.default_rng(19); x = 0.02 * rng.standard_normal(4000); t = np.arange(80)\n"
        "x[1200:1280] += 0.6 * np.exp(-t / 15.0) * np.sin(2.0 * np.pi * 4200.0 * t / fs)\n"
        "x[2600:2680] += 0.45 * np.exp(-t / 12.0) * np.sin(2.0 * np.pi * 3800.0 * t / fs)\n"
        "env = eng.compute_cell_envelope(x, sos, zi, hk, smooth, 400, 3600, pad)\n"
        "f = np.full_like(env, 0.015); d = np.full_like(env, 0.005)\n"
        "res = eng.detect_cell_candidates(env, f, d, 5.0, 2.0, 20, 1.8, 30.0, 0.2, 400, 3600, 400, 400)\n"
        "print(json.dumps({'max_env': float(env.max()), 'sum_env': float(env.sum()), 'res': res}))\n"
    )
    import json

    ref = json.loads(py_out)
    assert np.isclose(float(env_c.max()), ref["max_env"], rtol=1e-11)
    assert np.isclose(float(env_c.sum()), ref["sum_env"], rtol=1e-10)
    np.testing.assert_allclose(c_on, ref["res"][0], rtol=1e-10, atol=1e-10)
    np.testing.assert_allclose(c_pk, ref["res"][1], rtol=1e-10, atol=1e-10)
    np.testing.assert_allclose(c_fl, ref["res"][2], rtol=1e-10, atol=1e-10)
    np.testing.assert_allclose(c_sp, ref["res"][3], rtol=1e-10, atol=1e-10)


def test_onset_and_interference_and_normalizer_parity() -> None:
    if not _nb.is_native_available():
        pytest.skip("native engine not loaded")
    import json

    rng = np.random.default_rng(31)
    x = rng.normal(0.1, 0.02, 2000)
    x[1500:] += np.linspace(0.0, 1.0, 500)
    mu, sig = robust_level(x)
    idx = onset_after_quiet(np.abs(x - mu), 5.0 * sig, 40)
    assert idx is not None and 1490.0 <= idx <= 1580.0

    # Interference + Normalizer exact Python vs native comparison
    flt = InterferenceFilter(48000.0, (1200.0, 12000.0), min_frames=4)
    noise = 0.005 * rng.standard_normal(48000)
    t = np.arange(noise.size) / 48000.0
    noise += 0.2 * np.sin(2.0 * np.pi * 3000.0 * t)
    flt.add_frames(noise, 0, noise.size)
    g = flt.gains(noise.size)
    assert g is not None and flt.active_bins > 0
    y = flt.apply(noise[:8192], g)

    norm = AudioNormalizer(NormalizerConfig(sample_rate=48000.0, min_follower=0.0))
    segs = list(norm.process(0.1 * rng.standard_normal(12000), 0)) + list(norm.flush())
    assert sum(len(s) for _, s in segs) == 12000
    assert norm.samples_in == 12000

    py_ref = json.loads(
        _run_py(
            "import json, numpy as np\n"
            "from adv_tg.dsp.onset import robust_level, onset_after_quiet\n"
            "from adv_tg.dsp.interference import InterferenceFilter\n"
            "from adv_tg.dsp.normalizer import AudioNormalizer, NormalizerConfig\n"
            "rng = np.random.default_rng(31)\n"
            "x = rng.normal(0.1, 0.02, 2000); x[1500:] += np.linspace(0.0, 1.0, 500)\n"
            "mu, sig = robust_level(x); idx = onset_after_quiet(np.abs(x - mu), 5.0 * sig, 40)\n"
            "flt = InterferenceFilter(48000.0, (1200.0, 12000.0), min_frames=4)\n"
            "noise = 0.005 * rng.standard_normal(48000)\n"
            "t = np.arange(noise.size) / 48000.0\n"
            "noise += 0.2 * np.sin(2.0 * np.pi * 3000.0 * t)\n"
            "flt.add_frames(noise, 0, noise.size); g = flt.gains(noise.size)\n"
            "y = flt.apply(noise[:8192], g)\n"
            "norm = AudioNormalizer(NormalizerConfig(sample_rate=48000.0, min_follower=0.0))\n"
            "segs = list(norm.process(0.1 * rng.standard_normal(12000), 0)) + list(norm.flush())\n"
            "print(json.dumps({'mu': mu, 'sig': sig, 'idx': idx, 'ab': flt.active_bins,\n"
            "  'att': flt.max_attenuation_db, 'gsum': float(g.sum()), 'ysum': float(np.sum(np.abs(y))),\n"
            "  'ngain': norm.gain, 'nsum': float(sum(np.sum(np.abs(s)) for _, s in segs))}))\n"
        )
    )
    assert np.isclose(mu, py_ref["mu"], rtol=1e-14)
    assert np.isclose(sig, py_ref["sig"], rtol=1e-14)
    assert np.isclose(idx, py_ref["idx"], rtol=1e-12)
    assert flt.active_bins == py_ref["ab"]
    assert np.isclose(flt.max_attenuation_db, py_ref["att"], rtol=1e-11)
    assert np.isclose(float(g.sum()), py_ref["gsum"], rtol=1e-11)
    assert np.isclose(float(np.sum(np.abs(y))), py_ref["ysum"], rtol=1e-10)
    assert np.isclose(norm.gain, py_ref["ngain"], rtol=1e-14)
    assert np.isclose(float(sum(np.sum(np.abs(s)) for _, s in segs)), py_ref["nsum"], rtol=1e-12)
