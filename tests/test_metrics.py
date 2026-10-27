"""Unit tests of layer 3 (``adv_tg.core.metrics``) on hand-built beats and templates."""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import pytest

from adv_tg.core.beat_types import Beat, BeatFlags, ParityTemplate, lift_time_s
from adv_tg.core.metrics import (
    DEFAULT_LIFT_ANGLE_DEG,
    MetricsConfig,
    MetricsTracker,
    amplitude_from_templates,
    compute_metrics,
    fit_parity_weighted,
    format_beat_error_ms,
    rate_from_period,
    select_beats,
)

FS = 48_000
BPH = 21_600


def _beats(
    n: int = 200,
    *,
    rate: float = 4.0,
    be_ms: float = 0.4,
    jitter_us: tuple[float, float] = (0.0, 0.0),
    seed: int = 0,
    flags: dict[int, BeatFlags] | None = None,
) -> list[Beat]:
    rng = np.random.default_rng(seed)
    period = 3600.0 / BPH / (1.0 + rate / 86400.0) * FS
    out = []
    for k in range(n):
        t = 1000.0 + k * period + 0.5 * be_ms * 1e-3 * FS * (-1) ** k
        t += rng.normal(0.0, jitter_us[k % 2] * 1e-6 * FS) if jitter_us[k % 2] else 0.0
        f = (flags or {}).get(k, BeatFlags.NONE)
        out.append(Beat(k, t - 400, t - 200, t, 3, -6.0, 30.0, 0.0, 0.9, f, t_fine=t))
    return out


def _templates(amplitude: float = 280.0, la: float = 52.0, offset: float = 30.0):
    tl = lift_time_s(amplitude, la, BPH) * FS
    env = np.zeros(10)
    return tuple(
        ParityTemplate(
            p, 24, env, onset_p1=100.0, onset_p3=100.0 + tl, onset_ref=100.0 + tl + offset
        )
        for p in (0, 1)
    )


def _cfg(**kw) -> MetricsConfig:
    base = {"sample_rate": FS, "bph": BPH, "lift_angle_deg": 52.0}
    base.update(kw)
    return MetricsConfig(**base)


# ---------------------------------------------------------------------- selection
def test_select_excludes_saturated_and_nan_keeps_outliers_and_after_gap() -> None:
    """TIMING_OUTLIER judges the per-beat P3 anchor, not t_fine: those beats are kept (the
    robust trimming removes real t_fine outliers). SATURATED and t_fine NaN are excluded."""
    beats = _beats(
        40,
        flags={3: BeatFlags.SATURATED, 5: BeatFlags.TIMING_OUTLIER, 7: BeatFlags.AFTER_GAP},
    )
    beats[9] = Beat(9, 1.0, 2.0, 3.0, 3, -6.0, 30.0, 0.0, 0.9, BeatFlags.NONE)  # t_fine NaN
    k, t, excluded = select_beats(beats)
    assert excluded == 2
    assert 5.0 in k and 7.0 in k and 3.0 not in k and 9.0 not in k
    assert k.size == t.size == 38


def test_uncertainties_report_the_larger_of_covariance_and_blocks() -> None:
    m = compute_metrics(_beats(300, jitter_us=(50.0, 50.0), seed=6), _templates(), _cfg())
    for cov, block, rep in (
        (m.rate_sigma_s_per_day, m.rate_sigma_block_s_per_day, m.rate_uncertainty_s_per_day),
        (m.beat_error_sigma_ms, m.beat_error_sigma_block_ms, m.beat_error_uncertainty_ms),
    ):
        assert math.isfinite(cov) and math.isfinite(block)
        assert rep == max(cov, block)


def test_adc_drift_sigma_adds_to_rate_uncertainty() -> None:
    beats = _beats(300, jitter_us=(50.0, 50.0), seed=6)
    base = compute_metrics(beats, _templates(), _cfg())
    m = compute_metrics(beats, _templates(), _cfg(adc_drift_sigma_ppm=10.0))
    assert m.rate_calibrated and not base.rate_calibrated
    assert m.rate_clock_sigma_s_per_day == pytest.approx(0.864)
    stat = max(m.rate_sigma_s_per_day, m.rate_sigma_block_s_per_day)
    assert m.rate_uncertainty_s_per_day == pytest.approx(math.hypot(stat, 0.864))
    assert m.rate_s_per_day == base.rate_s_per_day
    assert [w.rate_uncertainty_s_per_day for w in m.windows] == [
        w.rate_uncertainty_s_per_day for w in base.windows
    ]
    with pytest.raises(ValueError):
        _cfg(adc_drift_sigma_ppm=-1.0)


def test_overlapping_windows() -> None:
    beats = _beats(6 * 60, jitter_us=(30.0, 30.0), seed=3)  # ~60 s
    m = compute_metrics(beats, _templates(), _cfg(window_step_s=5.0))
    assert len(m.windows) == 10  # 10 s windows every 5 s over ~59.8 s
    starts = [w.start_s for w in m.windows]
    assert np.allclose(np.diff(starts), 5.0)
    plain = compute_metrics(beats, _templates(), _cfg())
    assert m.rate_trend_s_per_day_per_min == plain.rate_trend_s_per_day_per_min


# ---------------------------------------------------------------------- regression
def test_fit_recovers_period_and_parity_term_exactly() -> None:
    beats = _beats(rate=7.0, be_ms=0.6)
    k, t, _ = select_beats(beats)
    fit = fit_parity_weighted(k, t)
    assert fit is not None
    period = 3600.0 / BPH / (1.0 + 7.0 / 86400.0) * FS
    assert fit.period == pytest.approx(period, rel=1e-12)
    assert 2 * abs(fit.c) / FS * 1e3 == pytest.approx(0.6, abs=1e-9)


def test_fit_needs_min_beats_per_parity() -> None:
    k, t, _ = select_beats(_beats(18))
    assert fit_parity_weighted(k, t, min_per_parity=10) is None
    assert fit_parity_weighted(k, t, min_per_parity=9) is not None


def test_fit_trims_outliers_per_parity() -> None:
    beats = _beats(200, jitter_us=(20.0, 20.0), seed=1)
    bad = beats[51]
    beats[51] = Beat(
        51,
        bad.p1,
        bad.p2,
        bad.p3,
        3,
        -6,
        30,
        0,
        0.9,
        BeatFlags.NONE,
        t_fine=bad.t_fine + 0.003 * FS,
    )
    k, t, _ = select_beats(beats)
    fit = fit_parity_weighted(k, t)
    assert fit is not None and fit.n_trimmed >= 1
    rate, _ = rate_from_period(fit.period, 0.0, _cfg())
    assert rate == pytest.approx(4.0, abs=0.2)


def test_weighting_follows_each_parity_noise() -> None:
    k, t, _ = select_beats(_beats(400, jitter_us=(50.0, 400.0), seed=2))
    fit = fit_parity_weighted(k, t)
    assert fit is not None
    assert fit.sigma_odd / fit.sigma_even == pytest.approx(8.0, rel=0.25)
    unw = fit_parity_weighted(k, t, weighted=False)
    assert unw is not None and unw.period_sigma > 2.0 * fit.period_sigma


# ---------------------------------------------------------------------- rate
def test_rate_sign_and_adc_drift_hook() -> None:
    cfg = _cfg()
    nominal = cfg.nominal_period_samples
    fast = nominal / (1.0 + 10.0 / 86400.0)
    assert rate_from_period(fast, 0.0, cfg)[0] == pytest.approx(10.0, abs=1e-9)
    # an ADC 5 ppm fast stretches the period in samples: the correction removes it
    stretched = nominal * (1.0 + 5e-6)
    assert rate_from_period(stretched, 0.0, _cfg(adc_drift_ppm=5.0))[0] == pytest.approx(
        0.0, abs=1e-6
    )
    assert rate_from_period(stretched, 0.0, cfg)[0] == pytest.approx(-0.432, abs=1e-3)


def test_uncalibrated_rate_is_flagged() -> None:
    m = compute_metrics(_beats(), _templates(), _cfg())
    assert not m.rate_calibrated and any("sin calibrar" in w for w in m.warnings)
    m = compute_metrics(_beats(), _templates(), _cfg(adc_drift_ppm=1.0))
    assert m.rate_calibrated and not any("sin calibrar" in w for w in m.warnings)


# ---------------------------------------------------------------------- beat error
def test_beat_error_is_measured_at_the_template_p1() -> None:
    m = compute_metrics(_beats(be_ms=0.5), _templates(), _cfg())
    assert m.beat_error_point == "p1"
    assert m.beat_error_ms == pytest.approx(0.5, abs=1e-6)


def test_beat_error_p1_uses_each_parity_offset() -> None:
    # tac template: P1 0.2 ms earlier relative to its reference -> c changes by 0.1 ms
    tic, tac = _templates()
    tac = ParityTemplate(
        1, 24, tac.envelope, tac.onset_p1 - 0.0002 * FS, tac.onset_p3, tac.onset_ref
    )
    m = compute_metrics(_beats(be_ms=0.0), (tic, tac), _cfg())
    assert m.beat_error_ms == pytest.approx(0.2, abs=1e-6)


def test_beat_error_uses_each_beats_p1_when_a_template_has_none() -> None:
    tic, tac = _templates()
    tac = ParityTemplate(1, 24, tac.envelope, math.nan, tac.onset_p3, tac.onset_ref)
    m = compute_metrics(_beats(be_ms=0.3), (tic, tac), _cfg())
    assert m.beat_error_point == "p1_beat"
    assert m.beat_error_ms == pytest.approx(0.3, abs=1e-6)


def test_beat_error_falls_back_to_t_fine_without_p1() -> None:
    tic, tac = _templates()
    tac = ParityTemplate(1, 24, tac.envelope, math.nan, tac.onset_p3, tac.onset_ref)
    beats = [dataclasses.replace(b, p1=math.nan) for b in _beats(be_ms=0.3)]
    m = compute_metrics(beats, (tic, tac), _cfg())
    assert m.beat_error_point == "t_fine"
    assert m.beat_error_ms == pytest.approx(0.3, abs=1e-6)
    assert any("t_fine" in w for w in m.warnings)


@pytest.mark.parametrize(
    ("value", "text"), [(0.04, "< 0.1 ms"), (0.06, "0.1 ms"), (0.34, "0.3 ms"), (math.nan, "—")]
)
def test_beat_error_format(value: float, text: str) -> None:
    assert format_beat_error_ms(value) == text


# ---------------------------------------------------------------------- amplitude
def test_amplitude_from_templates_and_la_uncertainty() -> None:
    a, a_tic, a_tac, unc = amplitude_from_templates(_templates(280.0), _cfg())
    assert a == pytest.approx(280.0, abs=1e-6) and a_tic == a_tac
    assert unc == pytest.approx(280.0 / 52.0, rel=0.05)  # ~2 % per degree of LA


def test_amplitude_default_lift_angle_is_indicative() -> None:
    m = compute_metrics(
        _beats(), _templates(280.0, la=DEFAULT_LIFT_ANGLE_DEG), _cfg(lift_angle_deg=None)
    )
    assert m.amplitude_indicative and m.amplitude_deg == pytest.approx(280.0, abs=1e-6)
    assert any("orientativa" in w for w in m.warnings)


def test_amplitude_out_of_range_is_invalid() -> None:
    a, a_tic, a_tac, _ = amplitude_from_templates(_templates(450.0), _cfg())
    assert math.isnan(a) and math.isnan(a_tic) and math.isnan(a_tac)


def test_tic_tac_amplitude_mismatch_warns() -> None:
    tic, _ = _templates(250.0)
    _, tac = _templates(300.0)
    m = compute_metrics(_beats(), (tic, tac), _cfg())
    assert m.amplitude_deg == pytest.approx(275.0, abs=0.5)
    assert any("tic y tac distinta" in w for w in m.warnings)
    m_ok = compute_metrics(_beats(), _templates(280.0), _cfg())
    assert m.confidence < m_ok.confidence


def test_quartz_reports_only_the_rate() -> None:
    m = compute_metrics(_beats(), (None, None), _cfg(quartz=True))
    assert math.isfinite(m.rate_s_per_day)
    assert math.isnan(m.beat_error_ms) and math.isnan(m.amplitude_deg)


# ---------------------------------------------------------------------- windows, trend
def test_window_rates_and_flat_trend() -> None:
    beats = _beats(6 * 60, jitter_us=(30.0, 30.0), seed=3)  # 60 s at 21600 BPH
    m = compute_metrics(beats, _templates(), _cfg())
    assert len(m.windows) == 5  # 10 s windows over ~59.8 s
    for w in m.windows:
        assert w.rate_s_per_day == pytest.approx(4.0, abs=5 * w.rate_sigma_s_per_day + 0.05)
    assert not m.rate_trend_flag
    assert abs(m.rate_trend_s_per_day_per_min) < 3 * m.rate_trend_sigma_s_per_day_per_min


def test_window_sigma_factor_is_about_one_for_white_noise() -> None:
    """White noise: sqrt(reduced chi^2) of 11 windows (9 dof) is ~1 and only exceeds ~1.55
    1 % of the time (99th percentile); it can inflate by chance, never deflate (>= 1)."""
    factors = []
    for seed in range(8, 16):
        beats = _beats(6 * 120, jitter_us=(40.0, 40.0), seed=seed)  # ~120 s, 11 windows
        m = compute_metrics(beats, _templates(), _cfg())
        factors.append(m.window_sigma_factor)
        for w in m.windows:
            assert w.rate_uncertainty_s_per_day == pytest.approx(
                w.rate_sigma_s_per_day * m.window_sigma_factor
            )
    assert min(factors) >= 1.0
    assert float(np.median(factors)) <= 1.15
    assert max(factors) <= 1.6


def test_window_sigma_factor_covers_correlated_noise() -> None:
    """A slow random walk on t_fine (correlated timing noise, as the template wander of a
    real take): the covariance sigma of each window is too small; with the factor the window
    rates fall within 2 sigma of the true rate."""
    beats = _beats(6 * 300, jitter_us=(40.0, 40.0), seed=9)  # ~300 s, 30 windows
    rng = np.random.default_rng(9)
    walk = np.cumsum(rng.normal(0.0, 20e-6 * FS, len(beats)))
    beats = [
        Beat(b.k, b.p1, b.p2, b.p3, 3, -6, 30, 0, 0.9, b.flags, t_fine=b.t_fine + w)
        for b, w in zip(beats, walk, strict=True)
    ]
    m = compute_metrics(beats, _templates(), _cfg())
    assert m.window_sigma_factor > 2.0
    rates = np.array([w.rate_s_per_day for w in m.windows])
    raw = np.array([w.rate_sigma_s_per_day for w in m.windows])
    unc = np.array([w.rate_uncertainty_s_per_day for w in m.windows])
    assert np.mean(np.abs(rates - 4.0) <= 2 * raw) < 0.7
    assert np.mean(np.abs(rates - 4.0) <= 2 * unc) >= 0.85


def test_too_few_beats_gives_no_figures() -> None:
    m = compute_metrics(_beats(12), _templates(), _cfg())
    assert math.isnan(m.rate_s_per_day) and m.confidence == 0.0
    assert any("menos de" in w for w in m.warnings)


def test_confidence_is_bounded() -> None:
    m = compute_metrics(_beats(jitter_us=(100.0, 300.0), seed=4), _templates(), _cfg())
    assert 0.0 < m.confidence <= 1.0


# ---------------------------------------------------------------------- incremental
def test_tracker_without_span_equals_batch() -> None:
    beats = _beats(300, jitter_us=(40.0, 160.0), seed=5)
    tpl = _templates()
    tracker = MetricsTracker(_cfg(), span_s=None)
    for i in range(0, len(beats), 7):
        tracker.add(beats[i : i + 7], tpl)
    a = tracker.result()
    b = compute_metrics(beats, tpl, _cfg())
    assert a.rate_s_per_day == pytest.approx(b.rate_s_per_day, abs=1e-9)
    assert a.beat_error_ms == pytest.approx(b.beat_error_ms, abs=1e-9)
    assert a.amplitude_deg == pytest.approx(b.amplitude_deg, abs=1e-9)


def test_tracker_span_keeps_the_last_seconds() -> None:
    beats = _beats(600)  # ~100 s
    tracker = MetricsTracker(_cfg(), span_s=30.0)
    tracker.add(beats, _templates())
    kept = tracker.beats
    span = (kept[-1].t_fine - kept[0].t_fine) / FS
    assert 29.5 <= span <= 30.0
    same = compute_metrics(kept, _templates(), _cfg())
    assert tracker.result().rate_s_per_day == pytest.approx(same.rate_s_per_day, abs=1e-9)


# ---------------------------------------------------------------------- config
@pytest.mark.parametrize(
    "kw",
    [
        {"sample_rate": 0.0},
        {"bph": -1.0},
        {"lift_angle_deg": 95.0},
        {"min_beats_per_parity": 2},
        {"outlier_sigma": 0.0},
        {"max_iterations": 0},
        {"window_s": 0.0},
        {"window_step_s": 0.0},
        {"window_step_s": 20.0},
        {"trend_sigma": 0.0},
        {"trend_blocks": 2},
        {"amplitude_range_deg": (300.0, 200.0)},
        {"amplitude_mismatch_deg": 0.0},
        {"adc_drift_ppm": math.inf},
    ],
)
def test_config_is_validated(kw: dict) -> None:
    with pytest.raises(ValueError):
        _cfg(**kw)


def test_tracker_span_is_validated() -> None:
    with pytest.raises(ValueError):
        MetricsTracker(_cfg(), span_s=0.0)
