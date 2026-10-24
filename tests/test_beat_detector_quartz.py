"""Quartz mode of the detector and the robustness fixes found with the quartz references.

* Quartz step motors (16, 4 and 1 steps/s) have no escapement structure: one event per step.
* Low beat rates: the acquisition window spans >= 8 beat periods (a 1/s quartz never locked
  with the fixed 2 s window) and the buffers keep it, so the result stays chunk-invariant.
* Frame anchoring (quartz only by default): the template frame cannot random-walk.

Tolerances: synthetic quartz with 20 µs jitter, SNR 30 dB; the rate over 60-120 s is known to
~0.01 s/d (measured errors 0.002-0.005 s/d), 0.03 s/d is the tolerance; the documented limit
for a change of the step sound is 0.07 s/d (0.1 tolerance).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from adv_tg.core.beat_detector import BeatDetector, BeatDetectorConfig
from adv_tg.core.metrics import MetricsConfig, compute_metrics
from tests.beat_helpers import beat_array
from tests.synth import synth_watch

FS = 48_000
TRUE_RATE = -0.7


def _run(x: np.ndarray, bph: float, chunk: int | None = 4096, **cfg) -> tuple[list, BeatDetector]:
    det = BeatDetector(BeatDetectorConfig(sample_rate=FS, bph=bph, quartz=True, **cfg))
    beats = []
    if chunk is None:
        beats += det.process(x, 0)
    else:
        for i in range(0, x.size, chunk):
            beats += det.process(x[i : i + chunk], i)
    beats += det.flush()
    return beats, det


def _rate(beats: list, det: BeatDetector, bph: float) -> float:
    m = compute_metrics(beats, det.templates(), MetricsConfig(sample_rate=FS, bph=bph, quartz=True))
    return m.rate_s_per_day


@pytest.mark.parametrize(("bph", "seconds"), [(57600, 60), (14400, 60), (3600, 120)])
def test_quartz_steps_are_counted_and_timed(bph: int, seconds: int) -> None:
    x, truth = synth_watch(
        bph=bph,
        rate_s_per_day=TRUE_RATE,
        seconds=seconds,
        snr_db=30,
        seed=5,
        quartz=True,
        jitter_us=20,
    )
    beats, det = _run(x, bph)
    assert len(beats) == truth.k.size
    assert det.stats.lock_time_s is not None and det.stats.lock_time_s <= 10.0
    assert all(math.isnan(b.p1) and math.isnan(b.p2) for b in beats)
    tf = np.array([b.t_fine for b in beats])
    assert np.mean(np.isfinite(tf)) >= 0.85
    assert _rate(beats, det, bph) == pytest.approx(TRUE_RATE, abs=0.03)


def test_one_step_per_second_is_chunk_invariant() -> None:
    """At 1/s the acquisition window is 8 s and its beats are examined retroactively; with
    the buffers trimmed to ~1-2 s the chunked run lost their t_fine (now kept)."""
    x, _ = synth_watch(bph=3600, seconds=60, snr_db=30, seed=6, quartz=True, jitter_us=20)
    ref = beat_array(_run(x, 3600, chunk=None)[0])
    for chunk in (512, 4096):
        np.testing.assert_allclose(beat_array(_run(x, 3600, chunk=chunk)[0]), ref, atol=1e-6)


def test_acquisition_window_spans_eight_beat_periods() -> None:
    assert BeatDetector(BeatDetectorConfig(sample_rate=FS, bph=3600)).W_acq == 8 * FS
    assert BeatDetector(BeatDetectorConfig(sample_rate=FS, bph=28800)).W_acq == 2 * FS


def test_frame_anchoring_with_a_variable_step_shape() -> None:
    """A precursor of random level before each step changes its shape (TMI VH31): the
    threshold onset of the template moves; the anchored frame does not."""
    x, _ = synth_watch(
        bph=14400,
        rate_s_per_day=TRUE_RATE,
        seconds=60,
        snr_db=30,
        seed=5,
        quartz=True,
        jitter_us=20,
        quartz_precursor=(6.0, 0.2, 1.2),
    )
    anchored = _rate(*_run(x, 14400), 14400)
    live = _rate(*_run(x, 14400, template_anchor=False), 14400)
    assert anchored == pytest.approx(TRUE_RATE, abs=0.02)
    assert abs(anchored - TRUE_RATE) < abs(live - TRUE_RATE)


def test_change_of_step_sound_stays_within_the_documented_limit() -> None:
    x, _ = synth_watch(
        bph=14400,
        rate_s_per_day=TRUE_RATE,
        seconds=90,
        snr_db=30,
        seed=5,
        quartz=True,
        jitter_us=20,
        shape_change_at_s=45.0,
    )
    assert _rate(*_run(x, 14400), 14400) == pytest.approx(TRUE_RATE, abs=0.1)


def test_mechanical_mode_does_not_anchor_by_default() -> None:
    x, _ = synth_watch(bph=28800, seconds=20, snr_db=30, seed=2)
    det = BeatDetector(BeatDetectorConfig(sample_rate=FS, bph=28800))
    for i in range(0, x.size, 4096):
        det.process(x[i : i + 4096], i)
    det.flush()
    assert all(t.anchor is None for t in det._tpl)
    tic, tac = det.templates()
    assert tic is not None and tic.onset_ref == tic.onset_p3


def test_template_refresh_interval_follows_the_beat_rate() -> None:
    refresh = {
        bph: BeatDetector(BeatDetectorConfig(sample_rate=FS, bph=bph)).tpl_refresh
        for bph in (3600, 21600, 28800, 36000, 57600)
    }
    assert refresh == {3600: 2, 21600: 2, 28800: 2, 36000: 3, 57600: 4}


@pytest.mark.parametrize(
    "kw",
    [
        {"template_reanchor_corr": 0.0},
        {"template_reanchor_corr": 1.0},
        {"quartz_template_ms": 0.0},
        {"template_refresh_min_s": -1.0},
    ],
)
def test_quartz_config_is_validated(kw: dict) -> None:
    with pytest.raises(ValueError):
        BeatDetectorConfig(sample_rate=FS, bph=14400, **kw)
