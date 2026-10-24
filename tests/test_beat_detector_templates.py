"""Fine timing with per-parity templates (``Beat.t_fine``, ``BeatDetector.templates``).

Motivation (real ETA C07 / Hamilton H-50 recordings): the drop of one parity rings as two
lobes and the loudest one changes from beat to beat, so per-beat onsets time tic and tac at
different physical points (spurious beat error of ~1 ms, jittery residual). The synthetic
``odd_drop_lobe`` option reproduces it: on odd ``k`` a second drop lobe 1.5 ms later with a
random level of 0.6-1.6 x the first one.

Tolerances (48 kHz, SNR 30 dB, 30 s):

* ``t_fine`` carries a constant bias (template onset at 20 % of the burst, after the
  zero-phase filters) of ~-3.8 samples that is the same for both parities within ~0.2
  sample on clean signals and ~1 sample with lobes / realism. The beat error is a
  difference between parities, so 0.03 ms (1.4 samples) leaves margin;
* the rate comes from the slope over ~200 beats with ~0.5-1 sample of jitter, i.e. well
  under 0.1 s/d; 0.2 s/d is the tolerance;
* template t_lift: measured 1-3 samples off the truth (both onsets share most of the bias),
  i.e. < 1 % of amplitude; 2 % is the tolerance.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from adv_tg.core.beat_detector import BeatDetector, BeatDetectorConfig
from adv_tg.core.beat_types import amplitude_from_lift_time, beat_period_s
from tests.beat_helpers import detect, match
from tests.synth import REALISM, synth_watch

FS = 48_000
LOBE = (1.5, 0.6, 1.6)


def _fit(beats: list, attr: str) -> tuple[float, float, float]:
    """(period in samples, beat error in ms, residual std in µs) of ``a + P k + c (-1)^k``."""
    k = np.array([b.k for b in beats], dtype=np.float64)
    t = np.array([getattr(b, attr) for b in beats])
    f = np.isfinite(t)
    A = np.column_stack([np.ones(f.sum()), k[f], (-1.0) ** k[f]])
    coef, *_ = np.linalg.lstsq(A, t[f], rcond=None)
    r = t[f] - A @ coef
    return float(coef[1]), 2.0 * abs(float(coef[2])) / FS * 1e3, float(r.std()) / FS * 1e6


def _run(x: np.ndarray, bph: float, **cfg) -> tuple[list, BeatDetector]:
    det = BeatDetector(BeatDetectorConfig(sample_rate=FS, bph=bph, **cfg))
    beats = []
    for i in range(0, x.size, 4096):
        beats += det.process(x[i : i + 4096], i)
    beats += det.flush()
    return beats, det


@pytest.mark.parametrize("bph", [21600, 28800])
def test_t_fine_clean_recovers_rate_and_beat_error(bph: int) -> None:
    x, truth = synth_watch(
        bph=bph, beat_error_ms=0.5, rate_s_per_day=5, seconds=30, snr_db=30, seed=3
    )
    beats, _ = detect(x, FS, bph)
    tf = np.array([b.t_fine for b in beats])
    # only the warm-up lacks t_fine: the phase model must be mature (6 beats) before a
    # template is seeded, then each parity needs template_min_beats (6) beats
    missing = np.flatnonzero(~np.isfinite(tf))
    assert missing.size <= 6 + 2 * 6 + 2
    assert missing.size == 0 or missing[-1] < 6 + 2 * 6 + 2
    period, be_ms, sigma_us = _fit(beats, "t_fine")
    rate = (beat_period_s(bph) * FS / period - 1.0) * 86400.0
    assert rate == pytest.approx(5.0, abs=0.2)
    assert be_ms == pytest.approx(0.5, abs=0.03)
    assert sigma_us < 30.0
    # same bias for both parities: t_fine - true P3, median per parity
    m = match(beats, truth)
    err = np.array([beats[j].t_fine for j in m.det_idx]) - truth.p3[m.truth_idx]
    par = np.array([beats[j].k for j in m.det_idx]) % 2
    d = np.nanmedian(err[par == 0]) - np.nanmedian(err[par == 1])
    assert abs(d) < 0.5


@pytest.mark.parametrize("realism", [False, True], ids=["limpio", "realismo"])
@pytest.mark.parametrize("bph", [21600, 28800])
def test_t_fine_fixes_spurious_beat_error_of_a_two_lobe_drop(bph: int, realism: bool) -> None:
    extra = REALISM if realism else {}
    x, _ = synth_watch(
        bph=bph,
        beat_error_ms=0.3,
        seconds=30,
        snr_db=30,
        seed=3,
        odd_drop_lobe=LOBE,
        **extra,
    )
    beats, _ = detect(x, FS, bph)
    _, be_p3, _ = _fit(beats, "p3")
    _, be_fine, sigma_fine = _fit(beats, "t_fine")
    assert abs(be_p3 - 0.3) > 0.3  # the per-beat onset is fooled by the louder lobe ...
    assert be_fine == pytest.approx(0.3, abs=0.03)  # ... the template is not
    assert sigma_fine < 60.0


@pytest.mark.parametrize(("bph", "amplitude"), [(18000, 300.0), (21600, 220.0), (28800, 280.0)])
@pytest.mark.parametrize("lobe", [None, LOBE], ids=["simple", "dos-lobulos"])
def test_template_lift_time_gives_the_amplitude(bph: int, amplitude: float, lobe) -> None:
    x, truth = synth_watch(
        bph=bph,
        amplitude_deg=amplitude,
        beat_error_ms=0.4,
        seconds=30,
        snr_db=30,
        seed=5,
        odd_drop_lobe=lobe,
    )
    _, det = _run(x, bph)
    tic, tac = det.templates()
    assert tic is not None and tac is not None
    for t in (tic, tac):
        assert t.n_beats == det.config.template_beats
        assert abs(t.t_lift_samples - truth.t_lift_samples) <= 4.0
        amp = amplitude_from_lift_time(t.t_lift_samples / FS, 52.0, bph)
        assert amp == pytest.approx(amplitude, rel=0.02)


def test_weak_p1_template_reports_nan_not_a_wrong_instant() -> None:
    x, _ = synth_watch(bph=21600, amplitude_deg=220, seconds=20, snr_db=30, seed=5, p1_gain=0.1)
    beats, det = _run(x, 21600)
    for t in det.templates():
        assert t is not None
        assert math.isnan(t.onset_p1) or 0.0 < t.t_lift_samples < det.tmax
    # t_fine does not depend on P1
    _, be_ms, _ = _fit(beats, "t_fine")
    assert be_ms < 0.05


def test_templates_before_any_beat_are_none() -> None:
    det = BeatDetector(BeatDetectorConfig(sample_rate=FS, bph=28800))
    assert det.templates() == (None, None)
    det.process(np.zeros(FS // 2), 0)
    assert det.templates() == (None, None)


def test_templates_are_copies() -> None:
    x, _ = synth_watch(bph=28800, seconds=6, snr_db=30, seed=2)
    _, det = _run(x, 28800)
    tic, _ = det.templates()
    assert tic is not None
    tic.envelope[:] = 0.0
    again, _ = det.templates()
    assert again is not None and np.any(again.envelope != 0.0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"template_min_beats": 0},
        {"template_min_beats": 30, "template_beats": 24},
        {"template_search_ms": 0.0},
        {"template_post_ms": -1.0},
        {"template_p3_fraction": 1.0},
        {"template_p1_fraction": 0.0},
        {"template_min_corr": 1.5},
        {"template_lobe_fraction": 0.0},
        {"template_burst_ms": 0.0},
        {"template_front_fraction": 1.0},
        {"template_front_lift_fraction": 0.0},
        {"template_amp_mismatch_deg": 0.0},
    ],
)
def test_template_config_is_validated(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        BeatDetectorConfig(sample_rate=FS, bph=28800, **kwargs)


@pytest.mark.parametrize("ramp", [(3.0, 0.35), (3.0, 0.5)])
@pytest.mark.parametrize(("bph", "amplitude"), [(21600, 270.0), (28800, 290.0)])
def test_slow_ramp_before_the_drop_uses_the_steep_front(bph, amplitude, ramp) -> None:
    """Seiko 4R36 tac: a slow ramp precedes the drop. The level onset lands on the ramp
    (t_lift too short, amplitude too high); tic and tac then disagree in amplitude, which is
    not physical, and the steep-front onset is used instead."""
    x, truth = synth_watch(
        bph=bph,
        amplitude_deg=amplitude,
        beat_error_ms=0.3,
        seconds=30,
        snr_db=30,
        seed=4,
        odd_pre_ramp=ramp,
    )
    _, only_level = _run(x, bph, template_amp_mismatch_deg=1e6)
    lvl = only_level.templates()[1]
    assert lvl is not None and lvl.t_lift_samples < truth.t_lift_samples - 20  # fooled
    _, det = _run(x, bph)
    for t in det.templates():
        assert t is not None and t.p3_method == "frente"
        assert abs(t.t_lift_samples - truth.t_lift_samples) <= 5.0
        amp = amplitude_from_lift_time(t.t_lift_samples / FS, 52.0, bph)
        assert amp == pytest.approx(amplitude, rel=0.02)
        # t_fine keeps marking the level onset: the timing does not change with the choice
        assert math.isfinite(t.onset_ref)


def test_consistent_templates_keep_the_level_onset() -> None:
    x, _ = synth_watch(bph=28800, beat_error_ms=0.4, seconds=20, snr_db=30, seed=5)
    _, det = _run(x, 28800)
    for t in det.templates():
        assert t is not None
        assert t.p3_method == "nivel" and t.onset_p3 == t.onset_ref
