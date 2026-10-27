"""Layer 5 core: ``MeasurementSession`` over a ``SampleStream`` fed block by block."""

from __future__ import annotations

import math

import numpy as np
import pytest

from adv_tg.core.adc_clock import AdcDrift, ClockLevel
from adv_tg.core.sample_stream import SampleStream
from adv_tg.core.session import MeasurementSession, SessionConfig
from tests.synth import synth_watch

FS = 48_000
BLOCK = 1024


def feed(
    session: MeasurementSession,
    x: np.ndarray,
    *,
    ppm: float = 0.0,
    wall: bool = True,
    update_every: int = 12,
    capacity_s: float = 10.0,
):
    """Write ``x`` in blocks with clock stamps of an ADC that runs ``ppm`` fast."""
    stream = SampleStream(int(capacity_s * FS), FS)
    rng = np.random.default_rng(1)
    snaps = []
    t0 = 1_700_000_000_000_000_000
    for i, a in enumerate(range(0, x.size, BLOCK)):
        b = min(x.size, a + BLOCK)
        true_s = b / FS / (1.0 + ppm * 1e-6)
        stamp = t0 + int(true_s * 1e9 + rng.exponential(0.5e6))  # callbacks arrive late
        stream.write(x[a:b], host_ns=stamp, wall_ns=stamp if wall else 0)
        if i % update_every == 0:
            snaps.append(session.update(stream))
    snaps.append(session.update(stream))
    return snaps


def test_session_measures_synthetic_watch() -> None:
    x, _ = synth_watch(bph=28800, rate_s_per_day=6.0, beat_error_ms=0.5, amplitude_deg=270,
                       lift_angle_deg=52, seconds=20, seed=4)  # fmt: skip
    s = MeasurementSession(SessionConfig(sample_rate=FS, bph=28800, lift_angle_deg=52))
    snaps = feed(s, x)
    last = snaps[-1]
    m = last.metrics
    assert m.rate_s_per_day == pytest.approx(6.0, abs=0.5)
    assert m.beat_error_ms == pytest.approx(0.5, abs=0.05)
    assert m.amplitude_deg == pytest.approx(270, rel=0.03)
    assert last.locked and last.beats_total > 100
    assert last.detection_ratio == pytest.approx(1.0, abs=0.02)
    assert 0.9 <= last.usable_ratio <= 1.0
    assert last.snr_db > 20 and last.saturated_beats == 0
    assert last.last_beat is not None
    # Figures appear after a few seconds, not at once.
    assert math.isnan(snaps[0].metrics.rate_s_per_day)
    first = next(i for i, sn in enumerate(snaps) if math.isfinite(sn.metrics.rate_s_per_day))
    assert snaps[first].audio_s < 10.0


def test_display_waveform_covers_the_last_beat() -> None:
    x, _ = synth_watch(bph=21600, amplitude_deg=280, seconds=8, seed=2)
    s = MeasurementSession(SessionConfig(sample_rate=FS, bph=21600, lift_angle_deg=52))
    snaps = [sn for sn in feed(s, x, update_every=4) if sn.waveform.size]
    assert snaps
    sn = snaps[-1]
    b = sn.last_beat
    assert b is not None
    p1 = b.p1 if math.isfinite(b.p1) else b.reference
    assert sn.waveform_start <= p1 <= b.reference < sn.waveform_start + sn.waveform.size
    peak = sn.waveform_start + int(np.argmax(np.abs(sn.waveform)))
    assert abs(peak - b.reference) < 0.004 * FS  # the drop is the loudest part
    assert abs(float(np.mean(sn.waveform))) < 0.05 * float(np.max(np.abs(sn.waveform)))


def test_live_clock_estimate_is_applied() -> None:
    # A perfect watch heard through an ADC 20 ppm fast looks slow by 20 * 0.0864 s/d.
    x, _ = synth_watch(bph=28800, rate_s_per_day=-20 * 0.0864, seconds=40, seed=6)
    s = MeasurementSession(SessionConfig(sample_rate=FS, bph=28800, lift_angle_deg=52))
    last = feed(s, x, ppm=20.0)[-1]
    live = last.live_drift
    assert live.level == ClockLevel.HOST_DISCIPLINED
    assert live.ppm == pytest.approx(20.0, abs=0.5)
    assert last.drift is live and last.drift_source.startswith("en vivo")
    assert s.metrics_config.adc_drift_ppm == pytest.approx(live.ppm)
    # The watch is perfect but the ADC is 20 ppm fast: corrected, the rate is ~0.
    assert last.metrics.rate_calibrated
    assert last.metrics.rate_s_per_day == pytest.approx(0.0, abs=0.5)


def test_stored_calibration_wins_when_more_precise() -> None:
    x, _ = synth_watch(bph=28800, rate_s_per_day=-20 * 0.0864, seconds=25, seed=7)
    cal = AdcDrift.manual(20.0, 0.05)
    cfg = SessionConfig(sample_rate=FS, bph=28800, lift_angle_deg=52, calibration=cal,
                        calibration_source="PPS")  # fmt: skip
    last = feed(MeasurementSession(cfg), x, ppm=20.0)[-1]
    assert last.drift is cal and last.drift_source == "PPS"
    assert last.metrics.rate_s_per_day == pytest.approx(0.0, abs=0.5)


def test_level1_live_estimate_is_never_applied() -> None:
    x, _ = synth_watch(bph=28800, seconds=25, seed=8)
    s = MeasurementSession(SessionConfig(sample_rate=FS, bph=28800, lift_angle_deg=52))
    last = feed(s, x, ppm=20.0, wall=False)[-1]
    if last.live_drift.level == ClockLevel.HOST_DISCIPLINED:  # Linux with NTP synchronized
        pytest.skip("perf_counter está disciplinado en este sistema")
    assert last.drift.level == ClockLevel.UNCALIBRATED and last.drift_source == "sin calibrar"
    assert not last.metrics.rate_calibrated


def test_missed_beats_lower_the_detection_ratio() -> None:
    x, _ = synth_watch(bph=28800, seconds=20, miss_prob=0.2, seed=9)
    s = MeasurementSession(SessionConfig(sample_rate=FS, bph=28800, lift_angle_deg=52))
    last = feed(s, x)[-1]
    assert 0.65 < last.detection_ratio < 0.92


def test_silence_gives_no_figures() -> None:
    rng = np.random.default_rng(0)
    s = MeasurementSession(SessionConfig(sample_rate=FS, bph=28800))
    last = feed(s, 1e-4 * rng.standard_normal(FS * 6))[-1]
    assert last.beats_total == 0 and not last.locked
    assert math.isnan(last.metrics.rate_s_per_day) and math.isnan(last.detection_ratio)
    assert last.waveform.size == 0 and last.last_beat is None


def test_display_rejects_short_glitches() -> None:
    from adv_tg.core.metrics import Metrics

    s = MeasurementSession(SessionConfig(sample_rate=FS, bph=28800, lift_angle_deg=52))

    def m(tac: float, conf: float) -> Metrics:
        return Metrics(n_beats=200, n_used_even=100, n_used_odd=100, n_excluded=0,
                       excluded_fraction=0.0, rate_s_per_day=8.0, beat_error_ms=0.2,
                       amplitude_tic_deg=295.0, amplitude_tac_deg=tac,
                       amplitude_deg=0.5 * (295.0 + tac), sigma_even_us=160.0,
                       sigma_odd_us=180.0, confidence=conf)  # fmt: skip

    for _ in range(5):
        d = s._display(m(295.0, 0.55))
    for _ in range(3):  # a 3-update glitch of the tac template (Miyota-like)
        d = s._display(m(127.0, 0.07))
        assert d.amplitude_deg == pytest.approx(295.0) and d.confidence == pytest.approx(0.55)
    assert d.timing_confidence == pytest.approx(1 / (1 + 180 / 500))  # no tic/tac factor
    # No figure now -> nothing stale is shown.
    nan = m(295.0, 0.0)
    nan.rate_s_per_day = math.nan
    d = s._display(nan)
    assert math.isnan(d.rate_s_per_day) and d.confidence == 0.0


def test_paper_tape_slope_is_the_rate_and_rows_the_beat_error() -> None:
    x, _ = synth_watch(bph=28800, rate_s_per_day=6.0, beat_error_ms=0.5, amplitude_deg=270,
                       lift_angle_deg=52, seconds=20, seed=12)  # fmt: skip
    s = MeasurementSession(SessionConfig(sample_rate=FS, bph=28800, lift_angle_deg=52))
    snaps = feed(s, x, wall=False)
    t = np.concatenate([sn.tape_time_s for sn in snaps])
    y = np.concatenate([sn.tape_offset_ms for sn in snaps])
    par = np.concatenate([sn.tape_parity for sn in snaps])
    assert t.size > 100 and np.all(np.diff(t) > 0)
    slopes, intercepts = [], []
    for p in (0, 1):
        m = par == p
        a, b = np.polyfit(t[m], y[m], 1)
        slopes.append(a)
        intercepts.append(b)
    # +6 s/d gains 6/86400 s per second = 0.0694 ms/s (a gaining watch goes up)
    assert slopes[0] == pytest.approx(6.0 / 86.4, rel=0.05)
    assert slopes[1] == pytest.approx(6.0 / 86.4, rel=0.05)
    assert abs(intercepts[0] - intercepts[1]) == pytest.approx(0.5, abs=0.05)
