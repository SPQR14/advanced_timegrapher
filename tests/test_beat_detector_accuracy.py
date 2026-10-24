"""Accuracy of the beat detector against the exact synthetic truth (``tests/synth.py``).

Uncertainty budget for the timing tolerances (48 kHz, SNR 25 dB, P3):

* noise on the envelope at the onset crossing: the envelope rises from 0 to its peak in
  ~0.15 ms (~7 samples), so noise of ~1/18 of the peak (25 dB) moves the 40 % crossing by
  ~0.2-0.4 samples (1 sigma);
* linear interpolation on a curved envelope: < 0.1 sample;
* the constant onset bias (burst shape + filters) is measured on clean signals and
  subtracted; its residual dependence on SNR/amplitude is checked to be < 0.5 sample.

So 1 sample (median) and 2 samples (p95) leave a margin of 2-3x over the expected spread.
"""

from __future__ import annotations

import numpy as np
import pytest

from tests.beat_helpers import detect, match
from tests.synth import REALISM, synth_watch

FS = 48_000


def _bias(fs: int = FS) -> tuple[float, float]:
    """Constant onset bias (P3, P1) in samples, measured on a clean signal."""
    x, truth = synth_watch(seconds=12, snr_db=40, beat_error_ms=0.3, seed=11, fs=fs)
    m = match(detect(x, fs, 28800)[0], truth)
    return float(np.nanmedian(m.err_p3)), float(np.nanmedian(m.err_p1))


@pytest.fixture(scope="module")
def bias() -> tuple[float, float]:
    return _bias()


def test_accuracy_30s_snr25(bias: tuple[float, float]) -> None:
    x, truth = synth_watch(
        bph=28800,
        snr_db=25,
        beat_error_ms=0.3,
        amplitude_deg=280,
        rate_s_per_day=5,
        seconds=30,
        seed=1,
    )
    beats, stats = detect(x, FS, 28800)
    m = match(beats, truth)
    assert len(beats) == truth.k.size and m.missed.size == 0 and m.false.size == 0
    assert m.k_consistent
    b3, b1 = bias
    e3 = np.abs(m.err_p3 - b3)
    assert np.median(e3) <= 1.0 and np.percentile(e3, 95) <= 2.0
    e1 = np.abs(m.err_p1[~np.isnan(m.err_p1)] - b1)
    assert e1.size >= 0.95 * truth.k.size  # P1 resolved almost always (first 2 need confirming)
    assert np.median(e1) <= 1.0 and np.percentile(e1, 95) <= 2.0
    tl_err = np.abs((m.err_p3 - m.err_p1) - (b3 - b1))
    tl_err = tl_err[~np.isnan(tl_err)]
    assert np.median(tl_err) <= 1.0 and np.percentile(tl_err, 95) <= 2.0
    assert stats.missed_beats == 0 and stats.rejected_events == 0
    assert stats.p3_fraction == 1.0
    assert stats.lock_time_s is not None and stats.lock_time_s <= 3.0
    assert stats.period_deviation == pytest.approx(-5 / 86400, abs=2e-5)


@pytest.mark.parametrize("snr_db", [30, 25, 20])
@pytest.mark.parametrize("amplitude", [160, 280, 320])
def test_bias_is_constant(bias: tuple[float, float], snr_db: float, amplitude: float) -> None:
    x, truth = synth_watch(snr_db=snr_db, amplitude_deg=amplitude, seconds=10, seed=5)
    m = match(detect(x, FS, 28800)[0], truth)
    assert np.nanmedian(m.err_p3) == pytest.approx(bias[0], abs=0.5)
    assert np.nanmedian(m.err_p1) == pytest.approx(bias[1], abs=0.5)


SWEEP = (
    [{"bph": b} for b in (18000, 21600, 28800, 36000)]
    + [{"beat_error_ms": be} for be in (0.0, 0.5, 1.5, 3.0)]
    + [{"amplitude_deg": a} for a in (160, 220, 280, 320)]
    + [{"rate_s_per_day": r} for r in (-300, 0, 300)]
    + [{"fs": f} for f in (44100, 48000, 96000)]
    + [{"snr_db": s} for s in (30, 25, 20)]
)


def _sweep_id(p: dict) -> str:
    return "-".join(f"{k}={v}" for k, v in p.items())


@pytest.mark.parametrize("realism", [False, True], ids=["clean", "realism"])
@pytest.mark.parametrize("params", SWEEP, ids=_sweep_id)
def test_sweep_no_false_no_missed(params: dict, realism: bool) -> None:
    kw = {"snr_db": 25, "beat_error_ms": 0.3, "seconds": 10, "seed": 3, **params}
    if realism:
        kw.update(REALISM)
    x, truth = synth_watch(**kw)
    fs = kw.get("fs", FS)
    beats, stats = detect(x, fs, kw.get("bph", 28800))
    m = match(beats, truth)
    assert m.missed.size == 0, f"missed {truth.p3[m.missed] / fs}"
    assert m.false.size == 0, f"false {[beats[i] for i in m.false]}"
    assert m.k_consistent
    e3 = np.abs(m.err_p3 - np.median(m.err_p3))
    assert np.percentile(e3, 95) <= 2.0
    assert stats.lock_time_s is not None and stats.lock_time_s <= 3.0
    assert not stats.warnings


@pytest.mark.parametrize("realism", [False, True], ids=["clean", "realism"])
@pytest.mark.parametrize("snr_db", [15, 10])
def test_low_snr_degrades_gracefully(snr_db: float, realism: bool) -> None:
    """Documented behaviour below 20 dB (P3 peak over in-band noise RMS):

    * 15 dB: the anchor is close to the 12 dB minimum, ~20 % of the beats are missed
      (counted as gaps in ``k``) and P1 is mostly NaN; no false beats.
    * 10 dB: the phase cannot be acquired; nothing is emitted and ``stats`` warns.
    """
    kw = {"snr_db": snr_db, "beat_error_ms": 0.3, "seconds": 10, "seed": 3}
    if realism:
        kw.update(REALISM)
    x, truth = synth_watch(**kw)
    beats, stats = detect(x, FS, 28800)
    m = match(beats, truth)
    assert m.false.size == 0
    if snr_db == 15:
        assert m.truth_idx.size >= 0.6 * truth.k.size and m.k_consistent
    else:
        assert m.truth_idx.size <= 0.2 * truth.k.size
        if not beats:
            assert any("sin enganche" in w for w in stats.warnings)
