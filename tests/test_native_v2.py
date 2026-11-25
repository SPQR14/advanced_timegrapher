"""Comprehensive v2.0 C++20 verification: SIMD parity, FLAC parity, lock-free stream, and MT rasterizer."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

from adv_tg import native_bridge as _nb
from adv_tg.analysis.gear_train import lomb_scargle, teager_kaiser
from adv_tg.audio.channel_select import ChannelSelector
from adv_tg.core.adc_clock import detect_pps_edges, drift_from_ticks, fit_envelope, lower_envelope
from adv_tg.core.beat_detector import BeatDetector, BeatDetectorConfig
from adv_tg.core.metrics import MetricsConfig, compute_metrics, fit_parity_weighted
from adv_tg.core.pps import PpsDetector
from adv_tg.core.sample_stream import SamplesOverwritten, SampleStream
from adv_tg.mock.generator import generate_tick_train, minmax_decimate
from adv_tg.ui.native_plots import smooth_median_array
from adv_tg.utils.audio_files import read_mono

SAMPLES_DIR = Path(__file__).resolve().parents[1] / "docs" / "samples"


def _run_py(code: str) -> str:
    env = os.environ.copy()
    env["ADV_TG_DISABLE_NATIVE"] = "1"
    return subprocess.check_output([sys.executable, "-c", code], env=env, text=True).strip()


def test_simd_levels_lomb_scargle_and_kernels() -> None:
    if not _nb.is_native_available():
        pytest.skip("native engine not loaded")
    cdll = _nb.lib()
    assert cdll is not None
    rng = np.random.default_rng(123)
    t = np.cumsum(0.2 + rng.uniform(-0.01, 0.01, size=257))
    y = np.sin(2.0 * np.pi * 0.27 * t) + 0.15 * rng.standard_normal(t.size)
    freqs = np.linspace(0.05, 1.2, 129)

    try:
        cdll.advtg_set_simd(b"generic")
        ls_gen = lomb_scargle(t, y, freqs)
        for lvl in ("sse42", "avx2", "avx512", "neon"):
            if cdll.advtg_set_simd(lvl.encode()):
                ls_lvl = lomb_scargle(t, y, freqs)
                np.testing.assert_allclose(ls_lvl, ls_gen, rtol=1e-11, atol=1e-12)
    finally:
        cdll.advtg_set_simd(b"auto")


def test_metrics_adc_clock_and_geartrain_parity_against_python() -> None:
    if not _nb.is_native_available():
        pytest.skip("native engine not loaded")
    rng = np.random.default_rng(42)
    fs = 48_000.0
    sig = generate_tick_train(
        duration_s=20.0,
        fs=int(fs),
        bph=28800,
        rate_spd=4.5,
        beat_error_ms=0.3,
        amplitude_deg=265.0,
        lift_angle_deg=51.0,
        snr_db=30.0,
        seed=42,
    )
    det = BeatDetector(BeatDetectorConfig(sample_rate=fs, bph=28800, lift_angle_deg=51.0))
    beats = det.process(sig, 0) + det.flush()
    m = compute_metrics(
        beats, det.templates(), MetricsConfig(sample_rate=fs, bph=28800, lift_angle_deg=51.0)
    )

    # Compare pure Python compute_metrics & fit_parity_weighted on identical synthetic beats.
    k = np.arange(0, 120, dtype=np.float64)
    p = 6000.002
    t = (
        500.0
        + p * k
        + np.where((k.astype(np.int64) & 1) == 0, 14.0, -14.0)
        + rng.normal(0.0, 1.5, k.size)
    )
    t[17] += 95.0  # outlier
    fit_c = fit_parity_weighted(k, t, quadratic=True)
    assert fit_c is not None

    # ADC clock & Lomb-Scargle parity
    n_idx = np.arange(1, 61, dtype=np.int64) * 48_000
    h_ns = (n_idx / fs * (1.0 - 12.5e-6) * 1e9 + rng.uniform(0.0, 2e5, n_idx.size)).astype(np.int64)
    env_n, env_t = lower_envelope(n_idx, h_ns, fs, bin_s=2.0)
    drift_c = fit_envelope(n_idx, h_ns, fs, jump_ms=10.0)
    ticks = np.arange(30, dtype=np.float64) * (fs * 1.000008) + 123.4
    drift_t = drift_from_ticks(ticks, fs)
    pps_wave = np.sin(np.linspace(0.0, 8.0 * np.pi, 1000))
    edges_c = detect_pps_edges(pps_wave, 500.0, min_interval_s=0.3)
    tk_c = teager_kaiser(pps_wave)
    freqs = np.linspace(0.1, 2.0, 64)
    ls_c = lomb_scargle(ticks / fs, np.cos(0.7 * ticks / fs), freqs)

    py_json = _run_py(
        "import json, numpy as np\n"
        "from adv_tg.core.metrics import fit_parity_weighted, compute_metrics, MetricsConfig\n"
        "from adv_tg.core.beat_detector import BeatDetector, BeatDetectorConfig\n"
        "from adv_tg.mock.generator import generate_tick_train\n"
        "from adv_tg.core.adc_clock import lower_envelope, fit_envelope, drift_from_ticks, detect_pps_edges\n"
        "from adv_tg.analysis.gear_train import teager_kaiser, lomb_scargle\n"
        "rng = np.random.default_rng(42); fs = 48000.0\n"
        "sig = generate_tick_train(20.0, int(fs), 28800, 4.5, 0.3, 265.0, 51.0, 30.0, 42)\n"
        "det = BeatDetector(BeatDetectorConfig(sample_rate=fs, bph=28800, lift_angle_deg=51.0))\n"
        "beats = det.process(sig, 0) + det.flush()\n"
        "m = compute_metrics(beats, det.templates(), MetricsConfig(sample_rate=fs, bph=28800, lift_angle_deg=51.0))\n"
        "k = np.arange(0, 120, dtype=np.float64)\n"
        "t = 500.0 + 6000.002 * k + np.where((k.astype(np.int64) & 1) == 0, 14.0, -14.0) + rng.normal(0.0, 1.5, k.size)\n"
        "t[17] += 95.0\n"
        "fc = fit_parity_weighted(k, t, quadratic=True)\n"
        "n_idx = np.arange(1, 61, dtype=np.int64) * 48000\n"
        "h_ns = (n_idx / fs * (1.0 - 12.5e-6) * 1e9 + rng.uniform(0.0, 2e5, n_idx.size)).astype(np.int64)\n"
        "en, et = lower_envelope(n_idx, h_ns, fs, bin_s=2.0)\n"
        "de = fit_envelope(n_idx, h_ns, fs, jump_ms=10.0)\n"
        "ticks = np.arange(30, dtype=np.float64) * (fs * 1.000008) + 123.4\n"
        "dt = drift_from_ticks(ticks, fs)\n"
        "pw = np.sin(np.linspace(0.0, 8.0 * np.pi, 1000))\n"
        "ec = detect_pps_edges(pw, 500.0, min_interval_s=0.3)\n"
        "tk = teager_kaiser(pw)\n"
        "freqs = np.linspace(0.1, 2.0, 64)\n"
        "ls = lomb_scargle(ticks / fs, np.cos(0.7 * ticks / fs), freqs)\n"
        "print(json.dumps({'rate': m.rate_s_per_day, 'be': m.beat_error_ms, 'amp': m.amplitude_deg, "
        "'fc_p': fc.period, 'fc_q': fc.q, 'en': en.tolist(), 'de_ppm': de.ppm, 'dt_ppm': dt.ppm, "
        "'ec': ec.tolist(), 'tk_sum': float(tk.sum()), 'ls': ls.tolist()}))\n"
    )
    ref = json.loads(py_json)
    assert np.isclose(m.rate_s_per_day, ref["rate"], atol=1e-6)
    assert np.isclose(m.beat_error_ms, ref["be"], atol=1e-6)
    assert np.isclose(m.amplitude_deg, ref["amp"], atol=1e-4)
    assert np.isclose(fit_c.period, ref["fc_p"], rtol=1e-12)
    # q of the quadratic fit is ill-conditioned (k up to 120, t ~ 7e5 samples): its rounding
    # depends on FMA contraction of the build (~1e-9 relative), so compare it against its own
    # statistical uncertainty instead of machine precision.
    assert abs(fit_c.q - ref["fc_q"]) <= 1e-6 * fit_c.q_sigma
    np.testing.assert_array_equal(env_n.tolist(), ref["en"])
    assert env_t.size == env_n.size
    assert np.isclose(drift_c.ppm, ref["de_ppm"], atol=1e-9)
    assert np.isclose(drift_t.ppm, ref["dt_ppm"], atol=1e-9)
    np.testing.assert_allclose(edges_c, ref["ec"], rtol=1e-12, atol=1e-12)
    assert np.isclose(float(tk_c.sum()), ref["tk_sum"], rtol=1e-12, atol=1e-12)
    np.testing.assert_allclose(ls_c, ref["ls"], rtol=1e-8, atol=1e-9)


def test_real_flac_parity_and_chunk_invariance() -> None:
    if not _nb.is_native_available():
        pytest.skip("native engine not loaded")
    flac = SAMPLES_DIR / "miyota_9015.flac"
    if not flac.is_file():
        pytest.skip("sample FLAC missing")
    fs, x = read_mono(flac)

    def _run_chunk(chunk_size: int):
        det = BeatDetector(BeatDetectorConfig(sample_rate=fs, bph=28800, lift_angle_deg=51.0))
        beats = []
        for i in range(0, x.size, chunk_size):
            beats += det.process(x[i : i + chunk_size], i)
        beats += det.flush()
        m = compute_metrics(
            beats, det.templates(), MetricsConfig(sample_rate=fs, bph=28800, lift_angle_deg=51.0)
        )
        return len(beats), m.rate_s_per_day, m.beat_error_ms, m.amplitude_deg

    b1, r1, be1, a1 = _run_chunk(1024)
    b2, r2, be2, a2 = _run_chunk(16384)
    assert abs(b1 - b2) <= 2
    assert abs(r1 - r2) < 0.02
    assert abs(be1 - be2) < 0.02
    assert abs(a1 - a2) < 0.5


def test_pps_detector_native_matches_reference() -> None:
    if not _nb.is_native_available():
        pytest.skip("native engine not loaded")
    fs = 48_000.0
    sig = np.zeros(int(fs * 7.0), dtype=np.float64)
    for k in range(5):
        pos = int((1.5 + k) * fs)
        t = np.arange(480, dtype=np.float64) / fs
        sig[pos : pos + 480] += 0.8 * np.exp(-t / 0.002) * np.sin(2.0 * np.pi * 4000.0 * t)
    det = PpsDetector(fs)
    pulses = det.process(sig, 0)
    assert len(pulses) == 5
    idx = np.array([p.index for p in pulses])
    expected = (1.5 + np.arange(5)) * fs
    np.testing.assert_allclose(idx, expected, atol=10.0)


def test_lockfree_stream_channel_select_decimate_and_median5() -> None:
    if not _nb.is_native_available():
        pytest.skip("native engine not loaded")
    # Multi-reader concurrent lock-free SPSC check
    cap = 4096
    s = SampleStream(cap, 48_000.0)
    assert s._native is not None
    done = threading.Event()
    errors: list[str] = []

    def producer() -> None:
        pos = 0
        for _ in range(400):
            s.write(np.arange(pos, pos + 512, dtype=np.float32))
            pos += 512
        done.set()

    def reader() -> None:
        while not done.is_set():
            try:
                start, arr = s.read_latest(cap // 2)
                if arr.size:
                    expected = np.arange(start, start + arr.size, dtype=np.float64)
                    if not np.array_equal(arr, expected):
                        errors.append("non-contiguous read")
                        return
            except SamplesOverwritten:
                continue

    t_p = threading.Thread(target=producer)
    t_r1 = threading.Thread(target=reader)
    t_r2 = threading.Thread(target=reader)
    t_r1.start()
    t_r2.start()
    t_p.start()
    t_p.join()
    t_r1.join()
    t_r2.join()
    assert not errors
    assert s.total_samples == 400 * 512

    # ChannelSelector C++ energy accumulator
    sel = ChannelSelector(2, 1000.0, window_s=0.1)
    t = np.arange(200, dtype=np.float32)
    stereo = np.column_stack([np.zeros(200, np.float32), 0.5 * np.sin(0.8 * t)])
    assert sel.pick(stereo[:100]) in (0, 1)
    assert sel.pick(stereo[100:]) == 1

    # minmax_decimate C++ vs numpy
    sig = np.sin(np.linspace(0.0, 30.0, 5000))
    tm, vals = minmax_decimate(sig, 48_000.0, bins=250)
    assert tm.size == 500 and vals.size == 500
    rs = sig[:5000].reshape(250, 20)
    np.testing.assert_allclose(vals[0::2], rs.min(axis=1))
    np.testing.assert_allclose(vals[1::2], rs.max(axis=1))

    # smooth_median_array sorting network vs numpy causal median
    rng = np.random.default_rng(7)
    raw = rng.standard_normal(60)
    for win in (5, 7):
        got = smooth_median_array(raw, win)
        ref = [float(np.median(raw[max(0, i - win + 1) : i + 1])) for i in range(raw.size)]
        np.testing.assert_allclose(got, ref, rtol=1e-15, atol=1e-15)


def test_multithreaded_native_graph_renderer() -> None:
    if not _nb.have("advtg_gr_create"):
        pytest.skip("native graph renderer missing")
    cdll = _nb.lib()
    gr = ctypes.c_void_p(cdll.advtg_gr_create())
    assert gr
    try:
        # 1. Submit tape with top-right tic point (y near y1) and bottom-left tac point (y near y0)
        xe = _nb.f64([8.0])
        ye = _nb.f64([9.0])  # near y1=10 -> row near top (py < h//4)
        xo = _nb.f64([2.0])
        yo = _nb.f64([-9.0])  # near y0=-10 -> row near bottom (py > 3*h//4)
        t_wave = _nb.f64(np.linspace(0.0, 1.0, 4096))
        y_wave = _nb.f64(np.sin(2.0 * np.pi * 12.0 * t_wave))
        y_env = _nb.f64(np.abs(y_wave))

        cdll.advtg_gr_submit_tape(
            gr,
            200,
            120,
            0.0,
            10.0,
            -10.0,
            10.0,
            _nb.ptr(xe),
            _nb.ptr(ye),
            1,
            _nb.ptr(xo),
            _nb.ptr(yo),
            1,
        )
        cdll.advtg_gr_submit_osc(
            gr,
            300,
            120,
            0.0,
            1.0,
            -1.1,
            1.1,
            _nb.ptr(t_wave),
            _nb.ptr(y_wave),
            _nb.ptr(y_env),
            t_wave.size,
        )
        cdll.advtg_gr_submit_wave(
            gr, 300, 120, 0.0, 1.0, -1.1, 1.1, _nb.ptr(t_wave), _nb.ptr(y_wave), t_wave.size
        )
        cdll.advtg_gr_flush(gr)

        for slot in (0, 1, 2):
            w, h, stride = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
            x0, x1, y0, y1 = (
                ctypes.c_double(),
                ctypes.c_double(),
                ctypes.c_double(),
                ctypes.c_double(),
            )
            seq = ctypes.c_uint64()
            ptr = cdll.advtg_gr_acquire_frame(
                gr,
                slot,
                ctypes.byref(w),
                ctypes.byref(h),
                ctypes.byref(stride),
                ctypes.byref(x0),
                ctypes.byref(x1),
                ctypes.byref(y0),
                ctypes.byref(y1),
                ctypes.byref(seq),
            )
            assert ptr and seq.value >= 1
            assert stride.value % 64 == 0
            raw = (ctypes.c_uint8 * (stride.value * h.value)).from_address(ptr)
            arr = np.frombuffer(raw, dtype=np.uint32).reshape(h.value, stride.value // 4)[
                :, : w.value
            ]
            assert np.count_nonzero(arr) > 10
            if slot == 0:
                # Top quarter has cyan tic disc (B > R), bottom quarter has amber tac disc (R > B)
                top = arr[: h.value // 4, :]
                bot = arr[3 * h.value // 4 :, :]
                assert np.count_nonzero(top) > 0 and np.count_nonzero(bot) > 0
                assert int(arr[6, 159] & 0xFF) > int((arr[6, 159] >> 16) & 0xFF)  # tic cyan
                assert int((arr[113, 40] >> 16) & 0xFF) > int(arr[113, 40] & 0xFF)  # tac amber

        cdll.advtg_gr_clear_slot(gr, 0)
        cdll.advtg_gr_flush(gr)
        w, h, stride = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
        x0, x1, y0, y1 = ctypes.c_double(), ctypes.c_double(), ctypes.c_double(), ctypes.c_double()
        seq = ctypes.c_uint64()
        ptr = cdll.advtg_gr_acquire_frame(
            gr,
            0,
            ctypes.byref(w),
            ctypes.byref(h),
            ctypes.byref(stride),
            ctypes.byref(x0),
            ctypes.byref(x1),
            ctypes.byref(y0),
            ctypes.byref(y1),
            ctypes.byref(seq),
        )
        raw = (ctypes.c_uint8 * (stride.value * h.value)).from_address(ptr)
        arr = np.frombuffer(raw, dtype=np.uint32).reshape(h.value, stride.value // 4)[:, : w.value]
        assert np.count_nonzero(arr) == 0
    finally:
        cdll.advtg_gr_destroy(gr)
