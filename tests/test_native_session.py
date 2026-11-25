"""Native ``MeasurementSession`` (advtg.h ``advtg_session_*``) against the Python session.

The C++ session is the engine a mobile app links, so it must give the SAME snapshots as the
Python composition of the layers: every field, every update, with zero tolerance.
"""

from __future__ import annotations

import ctypes
import math
from dataclasses import fields, is_dataclass
from pathlib import Path

import numpy as np
import pytest

from adv_tg import native_bridge as nb
from adv_tg.core.adc_clock import AdcDrift, ClockLevel
from adv_tg.core.native_session import NativeMeasurementSession, native_session_available
from adv_tg.core.sample_stream import SampleStream
from adv_tg.core.session import MeasurementSession, PyMeasurementSession, SessionConfig
from adv_tg.dsp.filters import (
    butter_bandpass_sos,
    py_butter_bandpass_sos,
    py_sosfilt_zi,
    sosfilt_zi,
    sosfiltfilt_padlen,
)
from tests.synth import synth_watch

pytestmark = pytest.mark.skipif(
    not native_session_available(), reason="motor nativo sin API de sesión"
)

FS = 48_000
SAMPLES = Path(__file__).resolve().parents[1] / "docs" / "samples"


def _diffs(path: str, a: object, b: object, out: list[str]) -> None:
    if is_dataclass(a):
        for f in fields(a):
            _diffs(f"{path}.{f.name}", getattr(a, f.name), getattr(b, f.name), out)
    elif isinstance(a, np.ndarray):
        assert isinstance(b, np.ndarray)
        if a.shape != b.shape or a.dtype != b.dtype:
            out.append(f"{path}: {a.shape}/{a.dtype} != {b.shape}/{b.dtype}")
        elif not np.array_equal(a, b, equal_nan=True):
            out.append(f"{path}: arrays differ")
    elif isinstance(a, (list, tuple)):
        if len(a) != len(b):  # type: ignore[arg-type]
            out.append(f"{path}: len {len(a)} != {len(b)}")  # type: ignore[arg-type]
        else:
            for i, (x, y) in enumerate(zip(a, b, strict=True)):  # type: ignore[call-overload]
                _diffs(f"{path}[{i}]", x, y, out)
    elif isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        pass
    elif a != b:
        out.append(f"{path}: {a!r} != {b!r}")


def _run_both(x: np.ndarray, fs: int, cfg: SessionConfig, *, wall: bool = True) -> int:
    """Feed both sessions from ONE stream; return the number of snapshots compared."""
    py, na = PyMeasurementSession(cfg), NativeMeasurementSession(cfg)
    stream = SampleStream(int(20 * fs), fs)
    rng = np.random.default_rng(1)
    t0 = 1_700_000_000_000_000_000
    block, every = 1024, max(1, int(0.25 * fs) // 1024)
    n = 0
    for i, a in enumerate(range(0, x.size, block)):
        b = min(x.size, a + block)
        stamp = t0 + int(b / fs / (1.0 + 12e-6) * 1e9 + rng.exponential(0.5e6))
        stream.write(x[a:b], host_ns=stamp, wall_ns=stamp if wall else 0)
        if i % every == 0 or b == x.size:
            sp, sn = py.update(stream), na.update(stream)
            d: list[str] = []
            _diffs("snapshot", sp, sn, d)
            assert not d, f"update {n}: " + "; ".join(d[:8])
            n += 1
    assert py.display_window == na.display_window
    d = []
    _diffs("metrics_config", py.metrics_config, na.metrics_config, d)
    _diffs("detector_stats", py.detector_stats, na.detector_stats, d)
    assert not d, "; ".join(d)
    return n


# ---------------------------------------------------------------------- filter design
@pytest.mark.parametrize("fs", [8000.0, 22050.0, 44100.0, 48000.0, 96000.0, 192000.0])
@pytest.mark.parametrize("order", [1, 2, 4, 6])
def test_native_butterworth_matches_numpy_port(fs: float, order: int) -> None:
    band = (300.0 if fs < 16000 else 1000.0, min(15000.0, 0.45 * fs))
    ref = py_butter_bandpass_sos(order, band, fs)
    got = butter_bandpass_sos(order, band, fs)  # native when loaded
    assert got.shape == ref.shape
    np.testing.assert_allclose(got, ref, rtol=1e-13, atol=1e-15)
    np.testing.assert_allclose(sosfilt_zi(got), py_sosfilt_zi(got), rtol=1e-13, atol=1e-15)
    pad = int(nb.lib().advtg_sosfiltfilt_padlen(nb.ptr(np.ascontiguousarray(got)), order))
    assert pad == sosfiltfilt_padlen(got)


def test_native_butterworth_rejects_invalid_band() -> None:
    out = np.zeros(24)
    assert nb.lib().advtg_butter_bandpass_sos(4, 1000.0, 30000.0, 48000.0, nb.ptr(out), 24) < 0
    assert nb.lib().advtg_butter_bandpass_sos(0, 1000.0, 3000.0, 48000.0, nb.ptr(out), 24) < 0
    assert nb.lib().advtg_butter_bandpass_sos(4, 1000.0, 3000.0, 48000.0, nb.ptr(out), 12) < 0


# ---------------------------------------------------------------------- session parity
def test_session_parity_synthetic_watch() -> None:
    x, _ = synth_watch(bph=28800, rate_s_per_day=6.0, beat_error_ms=0.5, amplitude_deg=270,
                       lift_angle_deg=52, seconds=40, seed=4)  # fmt: skip
    cfg = SessionConfig(sample_rate=FS, bph=28800, lift_angle_deg=52)
    assert _run_both(np.asarray(x, np.float32), FS, cfg) > 100


def test_session_parity_without_lift_angle_and_host_clock() -> None:
    x, _ = synth_watch(bph=21600, rate_s_per_day=-12.0, beat_error_ms=1.2, amplitude_deg=230,
                       lift_angle_deg=50, seconds=25, seed=9)  # fmt: skip
    cfg = SessionConfig(sample_rate=FS, bph=21600, span_s=20.0, window_s=5.0)
    _run_both(np.asarray(x, np.float32), FS, cfg, wall=False)


def test_session_parity_with_stored_calibration() -> None:
    x, _ = synth_watch(bph=28800, rate_s_per_day=3.0, beat_error_ms=0.2, amplitude_deg=280,
                       lift_angle_deg=52, seconds=30, seed=2)  # fmt: skip
    cal = AdcDrift(7.44, 0.04, 0.01, ClockLevel.EXTERNAL, "reference", span_s=149.0)
    cfg = SessionConfig(sample_rate=FS, bph=28800, lift_angle_deg=52, calibration=cal,
                        calibration_source="GPS PPS")  # fmt: skip
    _run_both(np.asarray(x, np.float32), FS, cfg)
    snap = MeasurementSession(cfg).update(SampleStream(FS, FS))
    assert snap.drift_source == "GPS PPS" and snap.drift.ppm == pytest.approx(7.44)


@pytest.mark.parametrize(
    ("name", "bph", "la", "quartz"),
    [
        ("miyota_9015.flac", 28800, 51.0, False),
        ("hamilton_h50.flac", 21600, 50.0, False),  # interference (notch) active
        ("tmi_vh31.flac", 14400, None, True),
    ],
)
def test_session_parity_real_takes(name: str, bph: int, la: float | None, quartz: bool) -> None:
    path = SAMPLES / name
    if not path.exists():
        pytest.skip(f"falta {name}")
    from adv_tg.utils.audio_files import read_mono

    fs, x = read_mono(path)
    x = np.asarray(x, dtype=np.float32)[: 30 * fs]
    cfg = SessionConfig(sample_rate=fs, bph=bph, lift_angle_deg=la, quartz=quartz)
    _run_both(x, fs, cfg)


# ---------------------------------------------------------------------- API behaviour
def test_measurement_session_defaults_to_native() -> None:
    s = MeasurementSession(SessionConfig(sample_rate=FS, bph=28800))
    assert isinstance(s, NativeMeasurementSession)


def test_native_session_falls_back_for_a_python_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    import adv_tg.core.sample_stream as ss

    with monkeypatch.context() as m:
        m.setattr(ss._nb, "have", lambda *names: False)  # numpy ring buffer
        stream = SampleStream(10 * FS, FS)
    assert stream._native is None
    x, _ = synth_watch(bph=28800, rate_s_per_day=6.0, beat_error_ms=0.5, amplitude_deg=270,
                       lift_angle_deg=52, seconds=8, seed=4)  # fmt: skip
    stream.write(np.asarray(x, np.float32))
    s = NativeMeasurementSession(SessionConfig(sample_rate=FS, bph=28800, lift_angle_deg=52))
    snap = s.update(stream)
    assert s._py is not None and snap.beats_total > 0


def test_invalid_config_raises_like_python() -> None:
    with pytest.raises(ValueError):
        NativeMeasurementSession(SessionConfig(sample_rate=4000, bph=28800))
    with pytest.raises(ValueError):
        NativeMeasurementSession(SessionConfig(sample_rate=FS, bph=100))


def test_c_api_owned_stream_like_a_mobile_app() -> None:
    """advtg_session_create(cfg, NULL) + advtg_session_stream: what an Android/iOS app does."""
    x, _ = synth_watch(bph=28800, rate_s_per_day=6.0, beat_error_ms=0.5, amplitude_deg=270,
                       lift_angle_deg=52, seconds=20, seed=4)  # fmt: skip
    x = np.ascontiguousarray(x, dtype=np.float32)
    lib = nb.lib()
    cfg = nb.NativeSessionConfigStruct()
    lib.advtg_session_config_default(ctypes.byref(cfg), float(FS), 28800.0)
    defaults = SessionConfig(sample_rate=FS, bph=28800)
    assert (cfg.span_s, cfg.window_s) == (defaults.span_s, defaults.window_s)
    assert (cfg.display_pre_ms, cfg.display_post_ms) == (
        defaults.display_pre_ms,
        defaults.display_post_ms,
    )
    assert cfg.live_clock == 1 and math.isnan(cfg.lift_angle_deg)
    cfg.lift_angle_deg = 52.0
    h = lib.advtg_session_create(ctypes.byref(cfg), None)
    assert h
    try:
        stream = lib.advtg_session_stream(h)
        snap = nb.NativeSnapshotStruct()
        for a in range(0, x.size, 960):  # 20 ms callbacks
            blk = x[a : a + 960]
            lib.advtg_stream_write_f32(stream, nb.ptr(blk), blk.size, 0, 0, 0, 0)
            if (a // 960) % 12 == 0:
                assert lib.advtg_session_update(h, ctypes.byref(snap)) == 0
        assert lib.advtg_session_update(h, ctypes.byref(snap)) == 0
        assert snap.locked and snap.beats_total > 100
        assert snap.display.rate_s_per_day == pytest.approx(6.0, abs=1.0)
        assert snap.display.beat_error_ms == pytest.approx(0.5, abs=0.15)
        assert snap.display.amplitude_deg == pytest.approx(270.0, abs=15.0)
        n = int(lib.advtg_session_window_beats(h, None, 0))
        beats = (nb.NativeBeatStruct * n)()
        assert lib.advtg_session_window_beats(h, beats, n) == n
        assert all(beats[i].k < beats[i + 1].k for i in range(n - 1))
    finally:
        lib.advtg_session_destroy(h)
