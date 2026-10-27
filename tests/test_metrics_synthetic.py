"""Layer 3 end to end on synthetic audio (``tests/synth.py``): detector + metrics vs truth.

Tolerances (docs/fase3_resultados.md, 4.1): clean 30 s at SNR 30 dB, rate +-0.2 s/d, beat
error +-0.03 ms, amplitude +-2 %; with realism, two-lobe drop or pre-drop ramp +-0.3 s/d,
+-0.05 ms, +-3 %. Measured margins are 3-10x (largest error: 0.17 s/d, 0.024 ms, 0.8 %).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from adv_tg.core.beat_detector import BeatDetector, BeatDetectorConfig
from adv_tg.core.metrics import (
    MetricsConfig,
    MetricsTracker,
    compute_metrics,
    fit_parity_weighted,
    rate_from_period,
    select_beats,
)
from tests.synth import REALISM, synth_watch

FS = 48_000
LA = 52.0


def _detect(x: np.ndarray, bph: float) -> tuple[list, BeatDetector]:
    det = BeatDetector(BeatDetectorConfig(sample_rate=FS, bph=bph, lift_angle_deg=LA))
    beats = []
    for i in range(0, x.size, 4096):
        beats += det.process(x[i : i + 4096], i)
    beats += det.flush()
    return beats, det


def _cfg(bph: float, **kw) -> MetricsConfig:
    return MetricsConfig(sample_rate=FS, bph=bph, lift_angle_deg=LA, **kw)


def _measure(bph: float, *, rate: float = 5.0, be: float = 0.4, amp: float = 280.0, **kw):
    x, _ = synth_watch(
        bph=bph,
        rate_s_per_day=rate,
        beat_error_ms=be,
        amplitude_deg=amp,
        seconds=kw.pop("seconds", 30),
        snr_db=30,
        seed=kw.pop("seed", 7),
        **kw,
    )
    beats, det = _detect(x, bph)
    return compute_metrics(beats, det.templates(), _cfg(bph))


def _check(m, rate: float, be: float, amp: float, tol: tuple[float, float, float]) -> None:
    assert m.rate_s_per_day == pytest.approx(rate, abs=tol[0])
    assert m.beat_error_ms == pytest.approx(be, abs=tol[1])
    assert m.amplitude_deg == pytest.approx(amp, rel=tol[2] / 100.0)
    assert m.beat_error_point in ("p1", "p1_beat")
    assert not m.rate_trend_flag  # constant rate: no trend warning


@pytest.mark.parametrize("bph", [18000, 21600, 28800, 36000])
def test_clean(bph: int) -> None:
    _check(_measure(bph), 5.0, 0.4, 280.0, (0.2, 0.03, 2.0))


@pytest.mark.parametrize("bph", [21600, 28800])
@pytest.mark.parametrize(
    "variant",
    [REALISM, {"odd_drop_lobe": (1.5, 0.6, 1.6)}, {"odd_pre_ramp": (3.0, 0.4)}],
    ids=["realismo", "dos-lobulos", "rampa"],
)
def test_realism_lobes_and_ramp(bph: int, variant: dict) -> None:
    _check(_measure(bph, **variant), 5.0, 0.4, 280.0, (0.3, 0.05, 3.0))


@pytest.mark.parametrize("rate", [-300.0, 0.0, 300.0])
def test_rate_sweep(rate: float) -> None:
    m = _measure(28800, rate=rate)
    assert m.rate_s_per_day == pytest.approx(rate, abs=0.3)


@pytest.mark.parametrize("be", [0.0, 0.5, 1.5, 3.0])
def test_beat_error_sweep(be: float) -> None:
    m = _measure(28800, be=be)
    assert m.beat_error_ms == pytest.approx(be, abs=0.05)


@pytest.mark.parametrize("amp", [160.0, 220.0, 280.0, 320.0])
def test_amplitude_sweep(amp: float) -> None:
    m = _measure(28800, amp=amp)
    assert m.amplitude_deg == pytest.approx(amp, rel=0.03)


def test_parity_weighting_is_needed_with_one_noisy_parity() -> None:
    """Odd beats 4x noisier (80 vs 320 µs). Weighted: every seed within +-0.3 s/d.
    Unweighted (same trimming, equal weights): several seeds fail (5 of 30) and the spread is
    ~2.4x (rms 0.183 vs 0.076 s/d)."""
    err_w, err_u = [], []
    cfg = _cfg(21600)
    for seed in range(30):
        x, _ = synth_watch(
            bph=21600,
            rate_s_per_day=3.0,
            seconds=30,
            snr_db=30,
            seed=200 + seed,
            jitter_us=80.0,
            odd_jitter_us=math.sqrt(320.0**2 - 80.0**2),
        )
        k, t, _ = select_beats(_detect(x, 21600)[0])
        fw = fit_parity_weighted(k, t)
        fu = fit_parity_weighted(k, t, weighted=False)
        assert fw is not None and fu is not None
        err_w.append(rate_from_period(fw.period, 0.0, cfg)[0] - 3.0)
        err_u.append(rate_from_period(fu.period, 0.0, cfg)[0] - 3.0)
    w, u = np.abs(err_w), np.abs(err_u)
    assert np.all(w <= 0.3)
    assert np.count_nonzero(u > 0.3) >= 3
    assert np.sqrt(np.mean(np.square(err_u))) > 1.8 * np.sqrt(np.mean(np.square(err_w)))


@pytest.mark.parametrize("drift", [15.0, 30.0])
def test_rate_drift_is_recovered_and_flagged(drift: float) -> None:
    x, _ = synth_watch(
        bph=28800,
        rate_s_per_day=5.0,
        seconds=40,
        snr_db=30,
        seed=3,
        jitter_us=50.0,
        rate_drift_s_per_day_per_min=drift,
    )
    beats, det = _detect(x, 28800)
    m = compute_metrics(beats, det.templates(), _cfg(28800))
    assert m.rate_trend_s_per_day_per_min == pytest.approx(drift, rel=0.2)
    assert m.rate_trend_flag
    assert any("cambia durante la medida" in w for w in m.warnings)


def test_consecutive_30s_windows_are_stable() -> None:
    """90 s, incremental mode: rates of consecutive 30 s windows agree within 3 sigma."""
    x, _ = synth_watch(bph=28800, rate_s_per_day=5.0, seconds=90, snr_db=30, seed=9, jitter_us=50)
    det = BeatDetector(BeatDetectorConfig(sample_rate=FS, bph=28800, lift_angle_deg=LA))
    tracker = MetricsTracker(_cfg(28800), span_s=30.0)
    results = []
    for i in range(0, x.size, 4096):
        tracker.add(det.process(x[i : i + 4096], i), det.templates())
        end = i + 4096
        if end // (30 * FS) != i // (30 * FS):
            results.append(tracker.result())
    assert len(results) >= 2
    for a, b in zip(results, results[1:], strict=False):
        s = math.hypot(a.rate_sigma_s_per_day, b.rate_sigma_s_per_day)
        assert abs(a.rate_s_per_day - b.rate_s_per_day) <= 3.0 * s
        assert abs(a.beat_error_ms - b.beat_error_ms) <= 3.0 * math.hypot(
            a.beat_error_sigma_ms, b.beat_error_sigma_ms
        )


def test_incremental_equals_batch_on_detector_output() -> None:
    x, _ = synth_watch(bph=21600, seconds=30, snr_db=30, seed=4, **REALISM)
    det = BeatDetector(BeatDetectorConfig(sample_rate=FS, bph=21600, lift_angle_deg=LA))
    tracker = MetricsTracker(_cfg(21600), span_s=None)
    beats = []
    for i in range(0, x.size, 1024):
        new = det.process(x[i : i + 1024], i)
        beats += new
        tracker.add(new, det.templates())
    tail = det.flush()
    beats += tail
    tracker.add(tail, det.templates())
    a = tracker.result()
    b = compute_metrics(beats, det.templates(), _cfg(21600))
    for name in (
        "rate_s_per_day",
        "beat_error_ms",
        "amplitude_deg",
        "rate_trend_s_per_day_per_min",
    ):
        assert getattr(a, name) == pytest.approx(getattr(b, name), abs=1e-9)


def test_declared_rate_uncertainty_covers_the_error() -> None:
    """50 seeds, 20 s, 50 µs jitter: the error is within 2 sigma in >= 90 % (95 % nominal)."""
    inside = 0
    for seed in range(50):
        x, _ = synth_watch(
            bph=28800, rate_s_per_day=5.0, seconds=20, snr_db=30, seed=100 + seed, jitter_us=50
        )
        beats, det = _detect(x, 28800)
        m = compute_metrics(beats, det.templates(), _cfg(28800))
        inside += abs(m.rate_s_per_day - 5.0) <= 2.0 * m.rate_sigma_s_per_day
    assert inside >= 45


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_beat_error_when_the_drop_of_one_parity_wanders(seed: int, monkeypatch) -> None:
    """Seiko 4R34: a clean P1 in tic and tac, but the tac drop arrives 0-3 ms late, beat by beat.

    The template P1 inherits the drop alignment (the old method read 0.3-1.0 ms for a true
    0.2 ms); each beat's own P1 is exact, and its far lower uncertainty selects it.
    """
    from adv_tg.core import metrics

    x, _ = synth_watch(
        bph=21600,
        rate_s_per_day=7.0,
        beat_error_ms=0.2,
        amplitude_deg=246,
        lift_angle_deg=53,
        seconds=90,
        snr_db=25,
        seed=seed,
        odd_drop_wander_ms=3.0,
        **REALISM,
    )
    det = BeatDetector(BeatDetectorConfig(sample_rate=FS, bph=21600, lift_angle_deg=53))
    beats = []
    for i in range(0, x.size, 4096):
        beats += det.process(x[i : i + 4096], i)
    beats += det.flush()
    cfg = MetricsConfig(sample_rate=FS, bph=21600, lift_angle_deg=53)
    m = compute_metrics(beats, det.templates(), cfg)
    assert m.beat_error_point == "p1_beat"
    assert m.beat_error_ms == pytest.approx(0.2, abs=0.05)
    assert m.rate_s_per_day == pytest.approx(7.0, abs=0.5)
    # the template P1 alone (the previous method) misses
    monkeypatch.setattr(metrics, "_BE_SWITCH_RATIO", 0.0)
    old = compute_metrics(beats, det.templates(), cfg)
    assert old.beat_error_point == "p1"
    assert abs(old.beat_error_ms - 0.2) > 0.08


def test_beat_error_keeps_the_template_p1_on_a_near_tie(monkeypatch) -> None:
    """A per-beat P1 only slightly more precise does not replace the validated template P1."""
    from adv_tg.core import metrics

    m = _measure(28800, seed=11, **REALISM)
    assert m.beat_error_point in ("p1", "p1_beat")
    monkeypatch.setattr(metrics, "_BE_SWITCH_RATIO", 1e-9)  # never switch
    ref = _measure(28800, seed=11, **REALISM)
    assert ref.beat_error_point == "p1"
    assert m.beat_error_ms == pytest.approx(ref.beat_error_ms, abs=0.05)
