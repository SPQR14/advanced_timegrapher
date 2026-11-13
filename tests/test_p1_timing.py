"""Timing at the unlocking front: ``Beat.t_p1`` (detector, step 8c) and the choice of the
timing point of each parity in the metrics (``select_timing``, ``Metrics.rate_point``).

The real takes are covered in ``test_metrics_samples.py``.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from adv_tg.core.beat_types import Beat, BeatFlags
from adv_tg.core.metrics import MetricsConfig, MetricsTracker, compute_metrics, select_timing
from adv_tg.core.sample_stream import SampleStream
from adv_tg.core.session import MeasurementSession, SessionConfig
from tests.beat_helpers import detect, match
from tests.synth import synth_watch

FS = 48_000
BPH = 21_600
PERIOD = 3600.0 / BPH * FS
LIFT = 500.0  # samples between the unlocking and the drop in the hand-built beats


# ====================================================================== detector
@pytest.mark.parametrize("fs", [44_100, 48_000, 96_000])
def test_t_p1_marks_the_true_unlocking_front(fs: int) -> None:
    """Synthetic watch with exact truth: ``t_p1`` follows the true P1 of every beat with a
    constant bias (the same for tic and tac) and a scatter of a few µs."""
    x, truth = synth_watch(bph=28800, rate_s_per_day=5.0, beat_error_ms=0.4, amplitude_deg=280,
                           fs=fs, seconds=20, snr_db=35.0, seed=3)  # fmt: skip
    beats, _ = detect(x, fs, 28800)
    m = match(beats, truth)
    err = np.array([beats[j].t_p1 for j in m.det_idx]) - truth.p1[m.truth_idx]
    par = np.array([beats[j].k & 1 for j in m.det_idx])
    ok = np.isfinite(err)
    assert ok.mean() >= 0.95
    us = 1e6 / fs
    bias = [float(np.median(err[ok & (par == p)])) * us for p in (0, 1)]
    scatter = [float(np.std(err[ok & (par == p)])) * us for p in (0, 1)]
    assert max(scatter) <= 12.0, scatter
    assert abs(bias[0] - bias[1]) <= 6.0, bias  # no spurious beat error
    assert max(abs(b) for b in bias) <= 120.0, bias  # the front, not a later lobe


def test_t_p1_is_off_in_quartz_mode_and_when_disabled() -> None:
    x, _ = synth_watch(bph=14400, seconds=12, snr_db=30.0, seed=1, quartz=True)
    beats, _ = detect(x, FS, 14400, quartz=True)
    assert beats and all(math.isnan(b.t_p1) for b in beats)
    x, _ = synth_watch(bph=28800, seconds=8, snr_db=30.0, seed=1)
    beats, _ = detect(x, FS, 28800, p1_onset=False)
    assert beats and all(math.isnan(b.t_p1) for b in beats)


def test_t_p1_needs_silence_before_the_front() -> None:
    """A noise floor as loud as P1 leaves no front to time: NaN, never an invented instant."""
    x, _ = synth_watch(bph=28800, seconds=12, snr_db=30.0, seed=2, p1_gain=0.02)
    beats, _ = detect(x, FS, 28800)
    timed = [b for b in beats if math.isfinite(b.t_p1)]
    # whatever is timed lies before the drop of its own beat, inside the physical window
    for b in timed:
        if math.isfinite(b.t_fine):
            assert 0.0 < b.t_fine - b.t_p1 < 0.03 * FS


# ====================================================================== metrics
def _beats(
    n: int = 180,
    *,
    rate: float = 4.0,
    be_ms: float = 0.4,
    drop_us: tuple[float, float] = (200.0, 200.0),
    p1_us: tuple[float, float] = (20.0, 20.0),
    seed: int = 0,
    drop_step: tuple[int, float] | None = None,
    p1_flip: tuple[float, float] | None = None,
    p1_scale: float | None = None,
) -> list[Beat]:
    """Beats whose drop (``t_fine``) and unlocking (``t_p1``) have their own timing noise.

    ``drop_step = (k, ms)``: the drop of the odd beats jumps by ``ms`` from beat ``k`` on (a
    template that changes shape). ``p1_flip = (share, ms)``: that share of the odd unlocking
    fronts lands ``ms`` later (a front that flips between two sounds). ``p1_scale``: the noise
    of the front is this multiple of the drop's noise of the same beat (an exact scatter ratio,
    for the thresholds). ``Beat.p1`` (the sub-event onset) is left out: only the fine instants.
    """
    rng = np.random.default_rng(seed)
    period = PERIOD / (1.0 + rate / 86400.0)
    out = []
    for k in range(n):
        t0 = 5000.0 + k * period + 0.5 * be_ms * 1e-3 * FS * (-1) ** k
        noise = rng.normal(0.0, drop_us[k & 1] * 1e-6 * FS)
        tf = t0 + LIFT + noise
        tp = t0 + rng.normal(0.0, p1_us[k & 1] * 1e-6 * FS)
        if p1_scale is not None:
            tp = t0 + p1_scale * noise
        if drop_step is not None and k & 1 and k >= drop_step[0]:
            tf += drop_step[1] * 1e-3 * FS
        if p1_flip is not None and k & 1 and rng.uniform() < p1_flip[0]:
            tp += p1_flip[1] * 1e-3 * FS
        out.append(
            Beat(
                k,
                math.nan,
                math.nan,
                tf,
                1,
                -6.0,
                30.0,
                0.0,
                0.9,
                BeatFlags.NONE,
                t_fine=tf,
                t_p1=tp,
            )  # fmt: skip
        )
    return out


def _cfg(**kw) -> MetricsConfig:
    return MetricsConfig(sample_rate=FS, bph=BPH, lift_angle_deg=52.0, **kw)


def test_clean_front_times_both_parities() -> None:
    beats = _beats()
    m = compute_metrics(beats, (None, None), _cfg())
    assert m.rate_point == ("p1", "p1")
    assert max(m.sigma_even_us, m.sigma_odd_us) <= 30.0
    assert m.rate_s_per_day == pytest.approx(4.0, abs=0.08)
    assert m.beat_error_point == "p1_fine"
    assert m.beat_error_ms == pytest.approx(0.4, abs=0.01)
    # ten times less timing noise: the rate uncertainty follows
    drop = compute_metrics(beats, (None, None), _cfg(p1_timing=False))
    assert drop.rate_point == ("t_fine", "t_fine")
    assert m.rate_uncertainty_s_per_day < 0.3 * drop.rate_uncertainty_s_per_day


def test_p1_timing_can_be_disabled_and_needs_t_p1() -> None:
    beats = _beats()
    assert compute_metrics(beats, (None, None), _cfg(p1_timing=False)).rate_point == (
        "t_fine",
        "t_fine",
    )
    bare = [
        Beat(b.k, b.p1, b.p2, b.p3, 2, -6.0, 30.0, 0.0, 0.9, b.flags, t_fine=b.t_fine)
        for b in beats
    ]
    m = compute_metrics(bare, (None, None), _cfg())
    assert m.rate_point == ("t_fine", "t_fine") and math.isfinite(m.rate_s_per_day)
    assert compute_metrics(beats, (None, None), _cfg(quartz=True)).rate_point == (
        "t_fine",
        "t_fine",
    )


def test_a_front_that_flips_keeps_the_drop_for_that_parity() -> None:
    """30 % of the odd fronts land 1 ms late (a weak P1 before a louder sound, as the tac of
    the ETA C07): that parity stays on the drop, the even one is timed at its front."""
    beats = _beats(p1_flip=(0.3, 1.0))
    m = compute_metrics(beats, (None, None), _cfg())
    assert m.rate_point == ("p1", "t_fine")
    assert m.beat_error_point != "p1_fine"  # the beat error needs the front in both parities
    assert m.rate_s_per_day == pytest.approx(4.0, abs=0.3)


def test_a_noisier_front_keeps_the_drop() -> None:
    m = compute_metrics(_beats(drop_us=(60.0, 60.0), p1_us=(150.0, 150.0)), (None, None), _cfg())
    assert m.rate_point == ("t_fine", "t_fine")


def test_step_of_the_drop_template_no_longer_moves_the_rate() -> None:
    """The drop of one parity jumps 1.5 ms in the middle of a 60 s take (the template changed
    shape; Seiko 4R34). At the drop the rate is off by several s/d; at P1 it is not."""
    beats = _beats(n=360, drop_step=(180, 1.5), seed=4)
    at_drop = compute_metrics(beats, (None, None), _cfg(p1_timing=False))
    m = compute_metrics(beats, (None, None), _cfg())
    assert m.rate_point == ("p1", "p1")
    assert abs(at_drop.rate_s_per_day - 4.0) > 0.5
    assert m.rate_s_per_day == pytest.approx(4.0, abs=0.05)


def test_too_few_fronts_keep_the_drop() -> None:
    beats = _beats()
    thin = [
        b
        if b.k % 3
        else Beat(b.k, b.p1, b.p2, b.p3, 2, -6.0, 30.0, 0.0, 0.9, b.flags, t_fine=b.t_fine)
        for b in beats
    ]
    k, t, excluded, points = select_timing(thin, _cfg())
    assert points == ("t_fine", "t_fine") and excluded == 0 and k.size == len(beats)


def test_selection_has_hysteresis() -> None:
    """Front scatter at 75 % of the drop's: not enough to leave the drop (62.5 %), but enough
    to stay at P1 once there (90 %), so a live reading does not flip between the points."""
    beats = _beats(n=400, drop_us=(100.0, 100.0), p1_scale=0.75, seed=7)
    cfg = _cfg()
    assert select_timing(beats, cfg)[3] == ("t_fine", "t_fine")
    assert select_timing(beats, cfg, ("p1", "p1"))[3] == ("p1", "p1")
    assert select_timing(beats, cfg, ("p1", "t_fine"))[3] == ("p1", "t_fine")


def test_tracker_remembers_the_point_between_results() -> None:
    cfg = _cfg()
    clean = _beats(n=120, seed=1)
    tr = MetricsTracker(cfg, span_s=None)
    tr.add(clean)
    assert tr.result().rate_point == ("p1", "p1")
    # the following beats have a front only a little better than the drop: still at P1
    more = _beats(n=400, drop_us=(100.0, 100.0), p1_scale=0.75, seed=7)
    tr2 = MetricsTracker(cfg, span_s=None)
    tr2.add(more)
    assert tr2.result().rate_point == ("t_fine", "t_fine")
    tr2._points = ("p1", "p1")
    assert tr2.result().rate_point == ("p1", "p1")


def test_saturated_beats_are_excluded_from_the_front_too() -> None:
    beats = _beats()
    b = beats[40]
    beats[40] = Beat(b.k, b.p1, b.p2, b.p3, 2, -6.0, 30.0, 0.0, 0.9, BeatFlags.SATURATED,
                     t_fine=b.t_fine, t_p1=b.t_p1 + 5000.0)  # fmt: skip
    k, t, excluded, points = select_timing(beats, _cfg())
    assert points == ("p1", "p1") and excluded == 1 and 40.0 not in k


# ====================================================================== live session
def test_live_tape_follows_the_point_without_a_step() -> None:
    """The tape draws each parity at its timing point. When the point changes from the drop to
    the front (a few seconds after the lock) the row must go on where it was."""
    x, _ = synth_watch(bph=28800, rate_s_per_day=6.0, beat_error_ms=0.5, amplitude_deg=270,
                       lift_angle_deg=52, seconds=24, snr_db=35.0, seed=4)  # fmt: skip
    s = MeasurementSession(SessionConfig(sample_rate=FS, bph=28800, lift_angle_deg=52))
    stream = SampleStream(10 * FS, FS)
    snaps = []
    for i, a in enumerate(range(0, x.size, 1024)):
        stream.write(x[a : a + 1024], host_ns=1 + int(a / FS * 1e9))
        if i % 12 == 0:
            snaps.append(s.update(stream))
    snaps.append(s.update(stream))
    points = [sn.metrics.rate_point for sn in snaps if math.isfinite(sn.metrics.rate_s_per_day)]
    assert points[-1] == ("p1", "p1")
    last = snaps[-1].metrics
    assert last.rate_s_per_day == pytest.approx(6.0, abs=0.2)
    assert last.beat_error_ms == pytest.approx(0.5, abs=0.03)
    t = np.concatenate([sn.tape_time_s for sn in snaps])
    y = np.concatenate([sn.tape_offset_ms for sn in snaps])
    par = np.concatenate([sn.tape_parity for sn in snaps])
    for p in (0, 1):
        tt, yy = t[par == p], y[par == p]
        assert tt.size > 60
        res = yy - np.polyval(np.polyfit(tt, yy, 1), tt)
        # one row, one line: no step of the lift time (~7 ms) nor of a fraction of it
        assert float(np.max(np.abs(res))) < 0.15, float(np.max(np.abs(res)))
        # slope of the row = rate (1 s/d = 1/86.4 ms per s)
        assert np.polyfit(tt, yy, 1)[0] * 86.4 == pytest.approx(6.0, abs=0.5)
    # rows drawn at the beat error shown
    se, so = snaps[-1].tape_shift_ms
    gap = abs((np.median(y[par == 0][-20:]) + se) - (np.median(y[par == 1][-20:]) + so))
    assert gap == pytest.approx(0.5, abs=0.08)
