"""Software audio normalization (``adv_tg.dsp.normalizer``) and its use by the detector.

The reference case is a loud quartz (Timex-like) on an input whose gain cannot be lowered:
every step reaches full scale, so without normalization every beat is ``SATURATED`` and the
rate is not available.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from adv_tg.core.beat_detector import BeatDetector, BeatDetectorConfig
from adv_tg.core.beat_types import BeatFlags
from adv_tg.core.metrics import MetricsConfig, compute_metrics
from adv_tg.core.sample_stream import SampleStream
from adv_tg.core.session import MeasurementSession, PyMeasurementSession, SessionConfig
from adv_tg.dsp.normalizer import AudioNormalizer, NormalizerConfig
from tests.synth import synth_watch

TRUE_RATE = -0.7


def _normalize(x: np.ndarray, fs: float, chunk: int | None, **cfg) -> tuple[np.ndarray, int]:
    norm = AudioNormalizer(NormalizerConfig(fs, **cfg))
    out = np.zeros_like(x, dtype=np.float64)
    segs = []
    if chunk is None:
        segs += list(norm.process(x, 0))
    else:
        for i in range(0, x.size, chunk):
            segs += list(norm.process(x[i : i + chunk], i))
    segs += list(norm.flush())
    n = 0
    for start, seg in segs:
        assert start == n  # contiguous, in order, nothing lost
        out[start : start + seg.size] = seg
        n += seg.size
    assert n == x.size
    return out, norm.clipped_samples


def _clipped_quartz(fs: int = 48_000, seconds: float = 90.0) -> np.ndarray:
    x, _ = synth_watch(bph=3600, rate_s_per_day=TRUE_RATE, seconds=seconds, snr_db=30, seed=5,
                       quartz=True, jitter_us=20, clip_level=0.3, fs=fs)  # fmt: skip
    return x


def test_config_validation() -> None:
    with pytest.raises(ValueError):
        NormalizerConfig(0.0)
    with pytest.raises(ValueError):
        NormalizerConfig(48000.0, target=1.0)
    with pytest.raises(ValueError):
        NormalizerConfig(48000.0, block_s=0.0)
    with pytest.raises(ValueError):
        NormalizerConfig(48000.0, block_s=math.inf)
    with pytest.raises(ValueError):
        NormalizerConfig(48000.0, min_follower=-0.1)
    with pytest.raises(ValueError):
        NormalizerConfig(48000.0, min_follower=math.inf)


def test_min_follower_and_infinite_release_keep_constant_gain() -> None:
    fs = 48_000
    rng = np.random.default_rng(9)
    # quiet start, loud burst, quiet middle, float overshoot > 1.0, then quiet tail
    x = np.concatenate(
        [
            0.005 * rng.standard_normal(fs),
            0.9 * rng.standard_normal(fs).clip(-1.0, 1.0),
            0.02 * rng.standard_normal(fs),
            1.6 * np.sin(2 * np.pi * 1000 * np.arange(fs) / fs),
            0.01 * rng.standard_normal(fs),
        ]
    )
    y, clipped = _normalize(x, fs, 1024, min_follower=1.0, release_s=math.inf)
    assert np.max(np.abs(y)) <= 0.5 + 1e-12
    # before the >1.0 burst the gain is strictly constant 0.5 (no quiet boost, no pumping)
    np.testing.assert_allclose(y[: 3 * fs], 0.5 * x[: 3 * fs], rtol=0, atol=1e-15)
    # after the 1.6 peak the gain stays monotonically at 0.5 / 1.6 (no release pumping)
    np.testing.assert_allclose(y[4 * fs :], (0.5 / 1.6) * x[4 * fs :], rtol=1e-4, atol=1e-12)
    assert clipped == int(np.count_nonzero(np.abs(x) >= 0.99))


@pytest.mark.parametrize("fs", [44_100, 48_000])
def test_output_never_exceeds_target_and_is_chunk_invariant(fs: int) -> None:
    rng = np.random.default_rng(1)
    t = np.arange(int(20 * fs)) / fs
    # level that jumps up and down by 30 dB plus clipped bursts
    level = np.where((t % 6.0) < 3.0, 0.02, 0.9)
    x = np.clip(level * rng.standard_normal(t.size), -1.0, 1.0)
    ref, clipped = _normalize(x, fs, None)
    assert np.max(np.abs(ref)) <= 0.5 + 1e-12
    assert clipped == int(np.count_nonzero(np.abs(x) >= 0.99))
    for chunk in (64, 1000, 4096, fs):
        y, _ = _normalize(x, fs, chunk)
        np.testing.assert_array_equal(y, ref)


def test_gain_is_constant_in_steady_state() -> None:
    fs = 48_000
    x = _clipped_quartz(fs, seconds=20)
    norm = AudioNormalizer(NormalizerConfig(fs))
    gains = []
    for i in range(0, x.size, 4800):
        for _ in norm.process(x[i : i + 4800], i):
            gains.append(norm.gain)
    # after the first step the peak is held (steps every 1 s < hold 3 s): one gain only
    assert len(set(gains[20:])) == 1
    assert norm.gain_db == pytest.approx(-6.02, abs=0.05)


def test_quiet_input_is_raised_up_to_the_limit() -> None:
    fs = 48_000
    x = 1e-4 * np.sin(2 * np.pi * 1000 * np.arange(5 * fs) / fs)
    y, _ = _normalize(x, fs, 4096)
    assert np.max(np.abs(y[-fs:])) == pytest.approx(1e-4 * 10 ** (24 / 20), rel=1e-3)


def test_zeros_stay_zeros_and_gaps_are_kept() -> None:
    fs = 48_000
    norm = AudioNormalizer(NormalizerConfig(fs))
    x = np.ones(7000) * 0.8
    x[1000:3000] = 0.0
    segs = list(norm.process(x[:5000], 0))
    segs += list(norm.process(x[5000:], 20_000))  # forward jump = data gap
    segs += list(norm.flush())
    starts = [s for s, _ in segs]
    assert starts == [0, 4800, 20_000]  # partial block closed before the gap
    y0 = segs[0][1]
    assert np.all(y0[1000:3000] == 0.0)
    with pytest.raises(ValueError):
        list(norm.process(np.zeros(10), 100))  # overlap with what was received


@pytest.mark.parametrize("fs", [44_100, 48_000])
def test_clipped_quartz_is_measured_only_with_normalization(fs: int) -> None:
    x = _clipped_quartz(fs)
    rates = {}
    for normalize in (False, True):
        cfg = BeatDetectorConfig(sample_rate=fs, bph=3600, quartz=True, normalize=normalize)
        det = BeatDetector(cfg)
        beats = []
        for i in range(0, x.size, 4096):
            beats += det.process(x[i : i + 4096], i)
        beats += det.flush()
        saturated = sum(1 for b in beats if b.flags & BeatFlags.SATURATED)
        m = compute_metrics(
            beats, det.templates(), MetricsConfig(sample_rate=fs, bph=3600, quartz=True)
        )
        rates[normalize] = m.rate_s_per_day
        if normalize:
            assert saturated == 0 and det.normalizer_clipped_samples > 0
        else:
            assert saturated == len(beats) and det.normalizer_clipped_samples is None
    assert math.isnan(rates[False])
    assert rates[True] == pytest.approx(TRUE_RATE, abs=0.03)


def test_normalization_does_not_change_the_timing() -> None:
    fs = 48_000
    x, _ = synth_watch(bph=28800, rate_s_per_day=3.0, seconds=20, snr_db=30, seed=2, fs=fs)
    x = 0.05 * x / np.max(np.abs(x))  # quiet, not clipped
    t = {}
    for normalize in (False, True):
        det = BeatDetector(BeatDetectorConfig(sample_rate=fs, bph=28800, normalize=normalize))
        beats = det.process(x, 0) + det.flush()
        t[normalize] = np.array([b.t_fine for b in beats])
    assert t[False].size == t[True].size
    # the detector is scale-invariant; only the few gain changes of the first seconds (and a
    # louder beat now and then) move the template slightly: measured max 0.09 samples (2 µs)
    np.testing.assert_allclose(t[True], t[False], atol=0.25, equal_nan=True)


def test_session_passes_the_option_to_the_detector() -> None:
    cfg = SessionConfig(sample_rate=44_100, bph=3600, quartz=True, normalize=True)
    s = PyMeasurementSession(cfg)
    assert s._detector.config.normalize and s._detector.normalizer_gain_db == 0.0
    # the default session (native when available) reports it in every snapshot
    snap = MeasurementSession(cfg).update(SampleStream(44_100, 44_100))
    assert snap.normalized


def test_normalize_preserves_p1_and_amplitude_with_startup_silence_and_variation() -> None:
    """With quiet startup, a loud transient and beat-to-beat level variation, software
    normalization must keep exact scale-invariant parity (no P1 loss, no amplitude bias)."""
    from tests.synth import REALISM

    fs = 48_000
    x, _ = synth_watch(
        bph=21600,
        rate_s_per_day=2.0,
        beat_error_ms=0.2,
        amplitude_deg=280.0,
        lift_angle_deg=51.0,
        seconds=25,
        snr_db=30,
        p1_gain=0.22,
        seed=12,
        **REALISM,
    )
    # scale below saturation, prepend quiet startup + handling bump (as in a live session)
    x = 0.6 * x / np.max(np.abs(x))
    rng = np.random.default_rng(12)
    lead = 0.002 * rng.standard_normal(int(0.6 * fs))
    lead[int(0.25 * fs) : int(0.26 * fs)] += 0.85 * np.sin(
        2 * np.pi * 1500 * np.arange(int(0.01 * fs)) / fs
    )
    sig = np.concatenate([lead, x])
    out = {}
    for normalize in (False, True):
        det = BeatDetector(
            BeatDetectorConfig(sample_rate=fs, bph=21600, lift_angle_deg=51.0, normalize=normalize)
        )
        beats = []
        for i in range(0, sig.size, 4096):
            beats += det.process(sig[i : i + 4096], i)
        beats += det.flush()
        m = compute_metrics(
            beats, det.templates(), MetricsConfig(sample_rate=fs, bph=21600, lift_angle_deg=51.0)
        )
        out[normalize] = (beats, m)
    b0, m0 = out[False]
    b1, m1 = out[True]
    assert len(b1) == len(b0)
    assert sum(math.isfinite(b.p1) for b in b1) == sum(math.isfinite(b.p1) for b in b0)
    np.testing.assert_allclose(
        [b.t_fine for b in b1], [b.t_fine for b in b0], atol=1e-9, equal_nan=True
    )
    assert m1.rate_s_per_day == pytest.approx(m0.rate_s_per_day, abs=1e-6)
    assert m1.beat_error_ms == pytest.approx(m0.beat_error_ms, abs=1e-6)
    assert m1.amplitude_deg == pytest.approx(m0.amplitude_deg, abs=1e-6)


def test_clipped_mechanical_watch_recovers_p1_and_metrics_with_normalization() -> None:
    """ETA C07-like watch clipping at full scale: normalization removes SATURATED flags and
    recovers rate, beat error and amplitude with P1 detected."""
    fs = 48_000
    x, _ = synth_watch(
        bph=21600,
        rate_s_per_day=1.5,
        beat_error_ms=0.2,
        amplitude_deg=280.0,
        lift_angle_deg=51.0,
        seconds=30,
        snr_db=32,
        p1_gain=0.25,
        clip_level=0.35,
        seed=15,
    )
    det = BeatDetector(
        BeatDetectorConfig(sample_rate=fs, bph=21600, lift_angle_deg=51.0, normalize=True)
    )
    beats = []
    for i in range(0, x.size, 4096):
        beats += det.process(x[i : i + 4096], i)
    beats += det.flush()
    assert det.normalizer_clipped_samples and det.normalizer_clipped_samples > 0
    assert sum(1 for b in beats if b.flags & BeatFlags.SATURATED) == 0
    m = compute_metrics(
        beats, det.templates(), MetricsConfig(sample_rate=fs, bph=21600, lift_angle_deg=51.0)
    )
    assert m.beat_error_point in ("p1", "p1_beat", "p1_fine")
    assert m.rate_s_per_day == pytest.approx(1.5, abs=0.4)
    assert m.beat_error_ms == pytest.approx(0.2, abs=0.08)
    assert m.amplitude_deg == pytest.approx(280.0, rel=0.05)
