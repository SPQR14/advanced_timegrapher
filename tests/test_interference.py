"""Noise-adaptive interference filter (``adv_tg.dsp.interference``) and its use by the detector."""

from __future__ import annotations

import math

import numpy as np
import pytest

from adv_tg.dsp.interference import InterferenceFilter
from tests.beat_helpers import beat_array, detect, match
from tests.synth import synth_watch

FS = 48_000
BAND = (1000.0, 15000.0)


def _feed(f: InterferenceFilter, x: np.ndarray, at: int) -> np.ndarray | None:
    """Add every frame of the grid that ends at or before ``at`` and return the gains."""
    while True:
        a, b = f.next_frame()
        if b > at:
            break
        f.add_frame(x[a:b])
    return f.gains(at)


def _tone(n: int, hz: float, level: float) -> np.ndarray:
    return level * np.sin(2 * np.pi * hz * np.arange(n) / FS)


def test_flat_noise_is_left_untouched() -> None:
    rng = np.random.default_rng(0)
    x = 0.01 * rng.standard_normal(4 * FS)
    f = InterferenceFilter(FS, BAND)
    f.reset(0)
    assert _feed(f, x, 3 * FS) is None  # every gain is 1: the caller skips the filter
    assert f.active_bins == 0 and f.max_attenuation_db == 0.0 and math.isnan(f.peak_hz)


def test_no_gains_before_enough_frames_or_on_digital_silence() -> None:
    f = InterferenceFilter(FS, BAND)
    f.reset(0)
    x = _tone(FS, 8300.0, 0.3)
    assert _feed(f, x, 8 * f.hop) is None  # 7 frames < min_frames
    f.reset(0)
    assert _feed(f, np.zeros(4 * FS), 3 * FS) is None  # a data gap is not noise


def test_tone_is_notched_and_a_click_keeps_its_instant() -> None:
    """A steady tone 30 dB above the floor is brought down to the floor; a click (a beat)
    passes with its position and almost all of its height."""
    rng = np.random.default_rng(1)
    n = 4 * FS
    x = 0.01 * rng.standard_normal(n) + _tone(n, 8300.0, 0.3)
    f = InterferenceFilter(FS, BAND)
    f.reset(0)
    g = _feed(f, x, 3 * FS)
    assert g is not None
    assert f.peak_hz == pytest.approx(8300.0, abs=FS / f.nfft)
    assert f.peak_excess_db > 25.0 and f.max_attenuation_db > 20.0
    seg = x[3 * FS : 3 * FS + 20_000].copy()
    seg[10_000] += 1.0
    y = f.apply(seg, g)
    core = slice(f.half, -f.half)
    assert int(np.argmax(np.abs(y))) == 10_000  # zero delay
    assert y[10_000] == pytest.approx(1.0, abs=0.08)
    quiet = np.r_[y[f.half : 9_000], y[11_000 : -f.half]]
    assert np.std(quiet) < 0.15 * np.std(seg[core])  # tone 0.21 rms -> near the 0.01 floor
    # outside the notch nothing changes: gains are 1 away from the line
    freqs = np.fft.rfftfreq(f.nfft, 1 / FS)
    far = np.abs(freqs - 8300.0) > 300.0
    assert np.all(g[far] == 1.0)


def test_only_attenuates_and_respects_the_limits() -> None:
    rng = np.random.default_rng(2)
    n = 4 * FS
    x = 1e-4 * rng.standard_normal(n) + _tone(n, 5000.0, 0.5) + _tone(n, 300.0, 0.5)
    f = InterferenceFilter(FS, BAND, max_attenuation_db=40.0)
    f.reset(0)
    g = _feed(f, x, 3 * FS)
    assert g is not None
    assert g.max() == 1.0 and g.min() >= 10 ** (-40 / 20) - 1e-12
    freqs = np.fft.rfftfreq(f.nfft, 1 / FS)
    # 300 Hz is outside the examined bins (a notch may reach one bin past their edge)
    assert np.all(g[freqs < 0.7 * BAND[0] - 2 * FS / f.nfft] == 1.0)
    assert np.all(g[freqs > BAND[1]] == 1.0)


def test_gains_use_only_past_frames() -> None:
    """The gains for audio from ``at`` on must not depend on what comes after ``at``."""
    rng = np.random.default_rng(3)
    x = 0.01 * rng.standard_normal(6 * FS)
    x[3 * FS :] += _tone(3 * FS, 6000.0, 0.3)  # the tone starts exactly at 3 s
    f = InterferenceFilter(FS, BAND)
    f.reset(0)
    assert _feed(f, x, 3 * FS) is None
    assert _feed(f, x, 5 * FS) is not None  # 2 s later the history holds it


def test_bad_arguments() -> None:
    with pytest.raises(ValueError):
        InterferenceFilter(FS, BAND, nfft=1000)
    with pytest.raises(ValueError):
        InterferenceFilter(FS, BAND, percentile=120.0)
    f = InterferenceFilter(FS, BAND)
    with pytest.raises(ValueError):
        f.add_frame(np.zeros(10))


# ====================================================================== in the detector
def _watch_with_tone(level: float, seconds: float = 20.0, seed: int = 5):
    x, truth = synth_watch(bph=28800, rate_s_per_day=5.0, beat_error_ms=0.4, amplitude_deg=280,
                           seconds=seconds, snr_db=35.0, seed=seed)  # fmt: skip
    return x + _tone(x.size, 8300.0, level * 0.5), truth  # P3 peaks at 0.5


def test_detector_ignores_the_filter_on_a_clean_signal() -> None:
    """White noise: every gain is 1, so the beats are bit-identical with and without it."""
    x, _ = synth_watch(bph=28800, seconds=12, snr_db=30.0, seed=1)
    on, st = detect(x, FS, 28800)
    off, _ = detect(x, FS, 28800, interference_filter=False)
    np.testing.assert_array_equal(beat_array(on), beat_array(off))
    assert st.interference_db == 0.0 and math.isnan(st.interference_hz)


def test_detector_measures_through_a_tone_as_loud_as_the_beats() -> None:
    """A whine at the level of the beats hides them from the plain band-pass; with the filter
    the beats are found and timed as without the tone."""
    x, truth = _watch_with_tone(1.0)
    off, _ = detect(x, FS, 28800, interference_filter=False)
    assert len(match(off, truth).det_idx) < 0.2 * truth.k.size
    on, st = detect(x, FS, 28800)
    m = match(on, truth)
    late = truth.p3[m.truth_idx] > 4 * FS  # after the 2 s of history and the lock
    assert np.count_nonzero(late) >= 0.95 * np.count_nonzero(truth.p3 > 4 * FS)
    err = np.array([on[j].t_p1 for j in m.det_idx])[late] - truth.p1[m.truth_idx][late]
    ok = np.isfinite(err)
    assert ok.mean() > 0.9
    assert float(np.std(err[ok])) / FS * 1e6 < 25.0  # µs
    assert st.interference_hz == pytest.approx(8300.0, abs=50.0) and st.interference_db > 20.0
    assert any(w.startswith("interferencia a 8.3 kHz") for w in st.warnings)


@pytest.mark.parametrize("chunk", [64, 1000, 4096, 48_000])
def test_detector_with_interference_is_chunk_invariant(chunk: int) -> None:
    x, _ = _watch_with_tone(0.3, seconds=8.0)
    ref = beat_array(detect(x, FS, 28800, chunk=None)[0])
    got = beat_array(detect(x, FS, 28800, chunk=chunk)[0])
    np.testing.assert_allclose(got, ref, atol=1e-6, rtol=0)
    assert ref.shape[0] > 30
