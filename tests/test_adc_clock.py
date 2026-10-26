"""Layer 4 (ADC clock): an injected ppm is recovered from synthetic block logs and references."""

from __future__ import annotations

import math

import numpy as np
import pytest

from adv_tg.core.adc_clock import (
    PPM_TO_S_PER_DAY,
    AdcDrift,
    ClockDriftTracker,
    ClockLevel,
    HostClock,
    detect_pps_edges,
    drift_from_ticks,
    estimate_drift,
    host_clock,
    lower_envelope,
)
from adv_tg.core.sample_stream import SampleStream

FS = 48000.0
BLOCK = 960  # 20 ms
RAW = HostClock("test raw", False, 30.0)
NTP = HostClock("test ntp", True, 0.5)


def block_log(
    ppm: float,
    duration_s: float,
    *,
    seed: int = 0,
    jitter_ms: float = 1.0,
    spike_prob: float = 0.01,
    steps: tuple[tuple[float, float], ...] = (),
    ppm_slope_per_s: float = 0.0,
    fs: float = FS,
) -> tuple[np.ndarray, np.ndarray]:
    """Block ends and host stamps for an ADC ``ppm`` fast; delays are positive only.

    ``steps``: ``(at_s, step_ms)`` host clock steps. ``ppm_slope_per_s``: slow drift of the ppm.
    """
    rng = np.random.default_rng(seed)
    nb = int(duration_s * fs / BLOCK)
    ends = (np.arange(1, nb + 1) * BLOCK).astype(np.int64)
    # Instantaneous ADC frequency fs (1 + d(t)); integrate dt = dn / (fs (1 + d)).
    dt = BLOCK / fs
    t_nom = np.arange(1, nb + 1) * dt
    d = (ppm + ppm_slope_per_s * t_nom) * 1e-6
    true_t = np.cumsum(dt / (1.0 + d))
    delay = 2e-3 + rng.exponential(jitter_ms * 1e-3, nb)
    spikes = rng.random(nb) < spike_prob
    delay[spikes] += rng.uniform(5e-3, 60e-3, int(spikes.sum()))
    host = 1000.0 + true_t + delay
    for at_s, step_ms in steps:
        host[true_t >= at_s] += step_ms * 1e-3
    return ends, np.round(host * 1e9).astype(np.int64)


# --------------------------------------------------------------------------- AdcDrift
def test_uncalibrated_and_manual():
    u = AdcDrift.uncalibrated("x")
    assert u.level == ClockLevel.UNCALIBRATED and u.ppm == 0.0
    assert u.uncertainty_ppm == pytest.approx(30.0)
    m = AdcDrift.manual(8.3, 0.2)
    assert m.level == ClockLevel.EXTERNAL and m.method == "manual"
    assert m.rate_bias_s_per_day == pytest.approx(-8.3 * PPM_TO_S_PER_DAY)
    with pytest.raises(ValueError):
        AdcDrift.manual(math.nan, 0.1)
    with pytest.raises(ValueError):
        AdcDrift.manual(1.0, -0.1)


def test_host_clock_descriptions():
    assert host_clock("time").disciplined
    assert host_clock("perf_counter").sigma_ppm > 0
    with pytest.raises(ValueError):
        host_clock("sundial")


# --------------------------------------------------------------------------- envelope
def test_lower_envelope_one_point_per_bin():
    ends, ns = block_log(0.0, 30.0)
    n, t = lower_envelope(ends, ns, FS)
    assert 27 <= n.size <= 29  # first and last bins dropped
    assert np.all(np.diff(n) > 0)


@pytest.mark.parametrize("ppm", [-45.0, 0.0, 8.3, 50.0])
def test_injected_ppm_is_recovered(ppm):
    ends, ns = block_log(ppm, 300.0, seed=int(ppm * 10) % 97)
    est = estimate_drift(ends, ns, FS, host=NTP)
    assert est.level == ClockLevel.HOST_DISCIPLINED
    assert est.method == "host-envelope"
    assert est.sigma_ppm < 0.3
    assert abs(est.ppm - ppm) < max(4 * est.sigma_ppm, 0.05)
    assert est.uncertainty_ppm == pytest.approx(math.hypot(est.sigma_ppm, 0.5))


def test_envelope_beats_mean_with_heavy_jitter():
    """Large positive jitter barely moves the envelope fit."""
    ends, ns = block_log(12.0, 600.0, seed=3, jitter_ms=8.0, spike_prob=0.05)
    est = estimate_drift(ends, ns, FS, host=RAW)
    assert est.level == ClockLevel.HOST_RAW
    assert abs(est.ppm - 12.0) < 0.5
    assert abs(est.ppm - 12.0) < 4 * est.sigma_ppm + 0.05


def test_clock_steps_cut_segments():
    ends, ns = block_log(-20.0, 400.0, seed=5, steps=((100.0, 40.0), (250.0, -25.0)))
    est = estimate_drift(ends, ns, FS, host=NTP)
    assert est.n_segments == 3
    assert abs(est.ppm + 20.0) < max(4 * est.sigma_ppm, 0.1)
    # Without segmenting, the steps would bias the slope by tens of ppm.
    naive = np.polyfit(ends / FS, (ns - ns[0]) * 1e-9 - ends / FS, 1)[0]
    assert abs((1 / (1 + naive) - 1) * 1e6 + 20.0) > 10.0


def test_short_log_is_uncalibrated():
    ends, ns = block_log(5.0, 8.0)
    est = estimate_drift(ends, ns, FS, host=NTP)
    assert est.level == ClockLevel.UNCALIBRATED
    assert est.note


def test_sigma_coverage():
    """The fit sigma is honest: ~95 % of runs within 2 sigma."""
    hits = 0
    runs = 40
    for seed in range(runs):
        ends, ns = block_log(7.0, 120.0, seed=100 + seed)
        est = estimate_drift(ends, ns, FS, host=NTP)
        hits += abs(est.ppm - 7.0) <= 2 * est.sigma_ppm
    assert hits >= 0.85 * runs


# --------------------------------------------------------------------------- tracker
def test_tracker_matches_batch_and_skips_duplicates():
    ends, ns = block_log(8.3, 400.0, seed=11)
    tr = ClockDriftTracker(FS, host=NTP)
    # Overlapping snapshots, as successive block_log() calls of a ring buffer give.
    for stop in range(500, ends.size + 500, 500):
        lo = max(0, stop - 800)
        tr.update(ends[lo:stop], ns[lo:stop])
    batch = estimate_drift(ends, ns, FS, host=NTP)
    live = tr.estimate()
    assert live.ppm == pytest.approx(batch.ppm, abs=0.05)
    assert abs(live.ppm - 8.3) < max(4 * live.sigma_ppm, 0.05)
    assert tr.update(ends[-10:], ns[-10:]) == 0


def test_tracker_with_sample_stream_block_log():
    """End to end through SampleStream.write / block_log with explicit host stamps."""
    ends, ns = block_log(-15.0, 240.0, seed=2)
    stream = SampleStream(FS * 4, FS, block_log_size=256)
    tr = ClockDriftTracker(FS, host=NTP)
    block = np.zeros(BLOCK, dtype=np.float32)
    for i, t in enumerate(ns.tolist()):
        stream.write(block, host_ns=t)
        if i % 100 == 99:
            tr.update(*stream.block_log())
    tr.update(*stream.block_log())
    est = tr.estimate()
    assert abs(est.ppm + 15.0) < max(4 * est.sigma_ppm, 0.1)


def test_tracker_resets_when_indices_go_back():
    ends, ns = block_log(0.0, 60.0)
    tr = ClockDriftTracker(FS, host=NTP)
    tr.update(ends, ns)
    assert tr.points > 50
    tr.update(ends[:100], ns[:100])
    assert tr.points < 5


def test_missing_stamps_are_ignored():
    ends, ns = block_log(6.0, 200.0, seed=8)
    ns = ns.copy()
    ns[::7] = 0  # e.g. host API without inputBufferAdcTime on some blocks
    tr = ClockDriftTracker(FS, host=NTP)
    tr.update(ends, ns)
    for est in (tr.estimate(), estimate_drift(ends, ns, FS, host=NTP)):
        assert abs(est.ppm - 6.0) < max(4 * est.sigma_ppm, 0.1)
    assert ClockDriftTracker(FS, host=NTP).update(ends, np.zeros_like(ns)) == 0


def test_pa_adc_source_follows_perf_counter():
    assert host_clock("pa_adc").disciplined == host_clock("perf_counter").disciplined


def test_slow_drift_is_flagged_and_stable_clock_is_not():
    drifting = ClockDriftTracker(FS, host=NTP)
    drifting.update(*block_log(5.0, 1200.0, seed=21, ppm_slope_per_s=3.0 / 1200.0))
    _, _, changed = drifting.stability(window_s=300.0)
    assert changed

    stable = ClockDriftTracker(FS, host=NTP)
    stable.update(*block_log(5.0, 1200.0, seed=22))
    delta, sigma, changed = stable.stability(window_s=300.0)
    assert not changed and math.isfinite(delta) and sigma > 0
    assert abs(stable.estimate(last_s=300.0).ppm - 5.0) < 0.3


# --------------------------------------------------------------------------- references
def ticks(ppm: float, n: int, *, period_s: float = 1.0, noise_s: float = 0.0, seed: int = 0):
    rng = np.random.default_rng(seed)
    t = 3.217 + np.arange(n) * period_s
    return t * FS * (1 + ppm * 1e-6) + rng.normal(0.0, noise_s * FS, n)


def test_acoustic_pps_with_missing_and_spurious_ticks():
    x = ticks(8.3, 300, noise_s=0.7e-3, seed=4)
    x = np.delete(x, [10, 11, 50, 200])
    x = np.append(x, [x[30] + 0.37 * FS, x[120] + 0.61 * FS])  # spurious clicks
    est = drift_from_ticks(x, FS, reference_sigma_ppm=0.01)
    assert est.level == ClockLevel.EXTERNAL and est.method == "reference"
    assert abs(est.ppm - 8.3) < max(4 * est.sigma_ppm, 0.1)
    assert est.sigma_ppm < 1.0


def test_reference_with_known_error_bulova_steps():
    # A reference 5 ppm fast (16 steps/s) seen by an ADC 8.3 ppm fast.
    ref_ppm, adc_ppm = 5.0, 8.3
    ratio = (1 + adc_ppm * 1e-6) / (1 + ref_ppm * 1e-6)
    x = (2.0 + np.arange(16 * 120) / 16.0) * FS * ratio
    est = drift_from_ticks(x, FS, nominal_period_s=1 / 16, reference_ppm=ref_ppm)
    assert est.ppm == pytest.approx(adc_ppm, abs=1e-3)


def test_too_few_ticks():
    assert drift_from_ticks(ticks(0.0, 5), FS).level == ClockLevel.UNCALIBRATED


@pytest.mark.parametrize("polarity", [1, -1])
def test_electrical_pps_edges_and_drift(polarity):
    ppm = -23.0
    dur_s = 120
    n = int(dur_s * FS)
    rng = np.random.default_rng(9)
    tau = np.arange(n) / (FS * (1 + ppm * 1e-6))  # true time of each sample
    phase = (tau - 0.4321) % 1.0
    high = (phase < 0.1) & (tau >= 0.4321)
    # Band-limited-ish edges: a 20 µs linear ramp so the crossing falls between samples.
    ramp = np.clip(phase / 20e-6, 0.0, 1.0) * high
    x = polarity * (0.8 * ramp - 0.4) + rng.normal(0.0, 0.002, n)
    edges = detect_pps_edges(x, FS, start_index=1000, polarity=polarity)
    assert edges.size == dur_s
    est = drift_from_ticks(edges, FS)
    assert abs(est.ppm - ppm) < 0.05
