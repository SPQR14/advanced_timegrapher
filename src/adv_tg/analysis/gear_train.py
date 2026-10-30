"""Cyclical gear-train defects from a recording (offline; no Qt, no threads).

A wheel that is eccentric, has a damaged tooth or a bent pivot repeats its effect once per
turn. In a mechanical watch two wheels turn fast enough to show up in a few minutes:

* **Escape wheel**: it advances one tooth per oscillation of the balance (two beats), so one
  turn takes ``T = 2 Z 3600 / BPH`` seconds (``Z`` teeth; 15 in almost every Swiss and
  Japanese calibre): 3.75 s at 28 800 A/h, 5 s at 21 600, 6 s at 18 000, 3 s at 36 000.
* **Fourth wheel**: it carries the seconds hand, one turn per minute (60 s).

Two series per beat ``k`` carry the signature:

1. **Timing residual** of ``t_fine`` (the P3 onset of the parity template placed on the beat)
   after removing ``a + b k + c k^2 + d (-1)^k`` (mean rate, slow drift of the rate with the
   state of wind, and the beat error). A periodic torque change modulates the amplitude and,
   through the isochronism error, the instantaneous rate: the beats arrive early and late
   with the period of the wheel. A phase ripple of amplitude ``A`` at period ``T`` is a rate
   ripple of ``2 pi A / T x 86400`` s/d.
2. **P3 energy**: the drop (P3) is the escape-wheel tooth landing on the pallet stone, so its
   loudness follows the tooth. The Teager-Kaiser operator ``Psi[n] = x[n]^2 - x[n-1] x[n+1]``
   of the band-passed signal, summed over the drop, gives one energy per beat; each parity is
   normalised by its own median (tic and tac differ in loudness) and the slow trend removed.

Spectra: Lomb-Scargle on the real beat instants (missing beats leave holes, no
interpolation), and an FFT on the uniform beat grid (holes filled linearly) as a cross-check.
For each wheel the strongest Lomb-Scargle peak within one frequency resolution of the
expected frequency is fitted by least squares (amplitude, phase) and its false-alarm
probability computed (Horne & Baliunas 1986 for the normalised periodogram, with as many
independent frequencies as the search window holds). A component is *significant* below
``FAP_SIGNIFICANT``, and only *resolvable* when the recording holds ``MIN_CYCLES`` turns.

Limits: a significant component is a symptom, not a diagnosis (an eccentric wheel, a damaged
tooth, a dirty pivot or a magnetised part can give the same ripple); its amplitude depends
on the position and the state of wind. The ADC clock error (ppm) scales every period by
the same tiny factor and is irrelevant here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from adv_tg.core.beat_detector import BeatDetector, BeatDetectorConfig
from adv_tg.core.beat_types import Beat, amplitude_from_lift_time, beat_period_s
from adv_tg.dsp.engine import sosfiltfilt
from adv_tg.dsp.filters import butter_bandpass_sos

__all__ = [
    "DEFAULT_ESCAPE_TEETH",
    "FAP_SIGNIFICANT",
    "FOURTH_WHEEL_MIN_RECORDING_S",
    "FOURTH_WHEEL_PERIOD_S",
    "MIN_CYCLES",
    "BeatSeries",
    "Component",
    "GearTrainReport",
    "NotEnoughBeats",
    "Spectrum",
    "analyze_recording",
    "escape_wheel_period_s",
    "extract_series",
    "lomb_scargle",
    "sine_fit",
    "spectral_report",
    "teager_kaiser",
]

DEFAULT_ESCAPE_TEETH: int = 15
FOURTH_WHEEL_PERIOD_S: float = 60.0
#: Turns of a wheel the recording must hold for its component to be resolvable.
MIN_CYCLES: float = 3.0
#: Shortest recording that can diagnose the fourth wheel: 3 turns of 60 s. Shorter takes
#: report it as not resolvable (in the GUI only the 240 s test qualifies).
FOURTH_WHEEL_MIN_RECORDING_S: float = MIN_CYCLES * 60.0
#: False-alarm probability below which a component is reported as present.
FAP_SIGNIFICANT: float = 0.01
#: Beats with a resolved timing needed for any analysis.
MIN_BEATS: int = 64
#: Lomb-Scargle oversampling (grid step = 1 / (OVERSAMPLE x recording length)).
OVERSAMPLE: int = 5
#: Shortest period of the spectra, in beats (4 = half the tic/tac frequency).
MIN_PERIOD_BEATS: float = 4.0
#: Span of the P3 energy window around ``t_fine`` (ms before, ms after).
ENERGY_WINDOW_MS: tuple[float, float] = (1.0, 6.0)
#: Window before P1 searched for spurious sounds (ms before P1: from, to).
PRE_P1_WINDOW_MS: tuple[float, float] = (6.0, 0.5)
#: Robust rejection of timing outliers before the spectra (MAD sigmas).
OUTLIER_SIGMA: float = 5.0

SERIES_TIMING = "timing"
SERIES_ENERGY = "energy"
COMPONENT_ESCAPE = "escape_wheel"
COMPONENT_FOURTH = "fourth_wheel"


class NotEnoughBeats(ValueError):
    """Fewer than ``MIN_BEATS`` beats with a fine timing (``args[1]`` = beats found)."""


# ---------------------------------------------------------------------- primitives
def escape_wheel_period_s(bph: float, teeth: int = DEFAULT_ESCAPE_TEETH) -> float:
    """One turn of the escape wheel: one tooth per oscillation (2 beats) -> ``2 Z 3600 / BPH``."""
    if not bph > 0 or teeth < 1:
        raise ValueError(f"bph y dientes deben ser positivos (bph={bph}, dientes={teeth})")
    return 2.0 * teeth * 3600.0 / float(bph)


def teager_kaiser(x: NDArray) -> NDArray[np.float64]:
    """Teager-Kaiser energy ``x[n]^2 - x[n-1] x[n+1]`` (edges repeat their neighbour)."""
    x = np.asarray(x, dtype=np.float64)
    if x.size < 3:
        return np.zeros(x.size)
    psi = np.empty_like(x)
    psi[1:-1] = x[1:-1] ** 2 - x[:-2] * x[2:]
    psi[0], psi[-1] = psi[1], psi[-2]
    return psi


def lomb_scargle(t: NDArray, y: NDArray, freqs: NDArray, chunk: int = 512) -> NDArray:
    """Normalised Lomb-Scargle periodogram (Scargle 1982, power / variance of ``y``).

    The mean of ``y`` is removed. A sinusoid of amplitude ``A`` in white noise of variance
    ``s^2`` over ``N`` samples peaks near ``N A^2 / (4 s^2)``.
    """
    t = np.asarray(t, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64) - np.mean(y)
    var = float(np.var(y))
    freqs = np.asarray(freqs, dtype=np.float64)
    out = np.zeros(freqs.size)
    if var <= 0.0 or t.size < 3:
        return out
    for a in range(0, freqs.size, chunk):
        w = 2.0 * np.pi * freqs[a : a + chunk, None]
        tau = np.arctan2(np.sin(2 * w * t).sum(1), np.cos(2 * w * t).sum(1))[:, None] / (2 * w)
        arg = w * (t - tau)
        c, s = np.cos(arg), np.sin(arg)
        yc, ys = (c * y).sum(1), (s * y).sum(1)
        cc, ss = (c * c).sum(1), (s * s).sum(1)
        out[a : a + chunk] = 0.5 * (yc**2 / np.maximum(cc, 1e-300) + ys**2 / np.maximum(ss, 1e-300))
    return out / var


def _fap(z: float, n: int, m: float) -> float:
    """False-alarm probability of normalised power ``z`` over ``m`` independent frequencies."""
    if not (math.isfinite(z) and n > 3):
        return 1.0
    single = max(0.0, 1.0 - 2.0 * z / n) ** ((n - 3) / 2.0)
    return float(-math.expm1(max(1.0, m) * math.log1p(-min(single, 1.0 - 1e-16))))


def sine_fit(t: NDArray, y: NDArray, f: float) -> tuple[float, float]:
    """Least-squares ``(amplitude, phase)`` of a sinusoid at ``f`` plus an offset."""
    w = 2.0 * np.pi * f
    m = np.column_stack((np.cos(w * t), np.sin(w * t), np.ones_like(t)))
    (a, b, _), *_ = np.linalg.lstsq(m, y, rcond=None)
    return float(math.hypot(a, b)), float(math.atan2(-b, a))


# ---------------------------------------------------------------------- data
@dataclass(frozen=True)
class BeatSeries:
    """Per-beat series of one recording (output of ``extract_series``).

    Attributes:
        sample_rate, bph: Recording and calibre.
        duration_s: Length of the recording.
        beats_detected: Beats returned by the detector.
        missing_beats: Beats predicted but not found (gaps in ``k``).
        rate_s_per_day: Mean rate of the fit (uncorrected for the ADC clock).
        k: Beat numbers used (finite ``t_fine``, outliers removed).
        t_s: Their instants in seconds.
        timing_residual_s: Timing residual (see module docstring), seconds.
        energy_rel: P3 Teager-Kaiser energy relative to its parity median, trend removed
            (0.1 = 10 % louder than usual).
        outliers: Beats removed as timing outliers.
        outlier_t_s: Instants of those beats (abrupt timing excursions, e.g. rebanking).
        lift_angle_deg: Lift angle used for ``amplitude_deg``.
        p1_s, p2_s, p3_s: Sub-event onsets of each beat in ``k`` (s; NaN if not resolved).
        e1, e2, e3: Teager-Kaiser energy of each sub-event: ``[P1, P2)``, ``[P2, P3)`` and
            ``[P3, P3 + 6 ms)`` (NaN unless P1, P2 and P3 are resolved).
        pre_p1_db: Strongest Teager-Kaiser peak in ``[P1 - 6 ms, P1 - 0.5 ms]`` over the
            median of the whole recording, in dB (a spurious sound before the unlocking).
        amplitude_deg: Amplitude of each beat from its lift time ``P3 - P1``.
    """

    sample_rate: float
    bph: float
    duration_s: float
    beats_detected: int
    missing_beats: int
    rate_s_per_day: float
    k: NDArray[np.int64]
    t_s: NDArray[np.float64]
    timing_residual_s: NDArray[np.float64]
    energy_rel: NDArray[np.float64]
    outliers: int = 0
    outlier_t_s: NDArray[np.float64] = field(default_factory=lambda: np.zeros(0))
    lift_angle_deg: float = 52.0
    p1_s: NDArray[np.float64] = field(default_factory=lambda: np.zeros(0))
    p2_s: NDArray[np.float64] = field(default_factory=lambda: np.zeros(0))
    p3_s: NDArray[np.float64] = field(default_factory=lambda: np.zeros(0))
    e1: NDArray[np.float64] = field(default_factory=lambda: np.zeros(0))
    e2: NDArray[np.float64] = field(default_factory=lambda: np.zeros(0))
    e3: NDArray[np.float64] = field(default_factory=lambda: np.zeros(0))
    pre_p1_db: NDArray[np.float64] = field(default_factory=lambda: np.zeros(0))
    amplitude_deg: NDArray[np.float64] = field(default_factory=lambda: np.zeros(0))


@dataclass(frozen=True)
class Spectrum:
    """Spectra of one series: Lomb-Scargle on ``freqs_hz`` and FFT on its own grid."""

    freqs_hz: NDArray[np.float64]
    ls_power: NDArray[np.float64]
    fft_freqs_hz: NDArray[np.float64]
    fft_amplitude: NDArray[np.float64]


@dataclass(frozen=True)
class Component:
    """One wheel looked for in one series.

    Attributes:
        name: ``"escape_wheel"`` or ``"fourth_wheel"``.
        series: ``"timing"`` or ``"energy"``.
        expected_period_s: Period of one turn of the wheel.
        period_s: Period of the strongest peak near it (NaN if not resolvable).
        amplitude: Sinusoid amplitude: seconds (timing) or relative energy (energy).
        rate_ripple_s_per_day: Rate ripple equivalent to a timing amplitude (NaN for energy).
        fap: False-alarm probability of the peak.
        resolvable: The recording holds ``MIN_CYCLES`` turns of the wheel.
    """

    name: str
    series: str
    expected_period_s: float
    period_s: float
    amplitude: float
    rate_ripple_s_per_day: float
    fap: float
    resolvable: bool

    @property
    def significant(self) -> bool:
        return self.resolvable and self.fap < FAP_SIGNIFICANT


@dataclass(frozen=True)
class GearTrainReport:
    """Everything the analysis window shows (``spectral_report``)."""

    series: BeatSeries
    escape_teeth: int
    escape_period_s: float
    timing: Spectrum
    energy: Spectrum
    components: tuple[Component, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------- series
def _detect(x: NDArray, fs: float, bph: float, lift_angle_deg: float) -> tuple[list[Beat], int]:
    det = BeatDetector(BeatDetectorConfig(sample_rate=fs, bph=bph, lift_angle_deg=lift_angle_deg))
    beats = det.process(x, 0) + det.flush()
    return beats, det.stats.missed_beats


def _detrend(k: NDArray, y: NDArray, parity: bool) -> tuple[NDArray, NDArray]:
    """Residual of ``y`` after ``a + b k + c k^2 (+ d (-1)^k)`` and the coefficients."""
    kc = (k - k.mean()) / max(1.0, float(np.ptp(k)))
    cols = [np.ones_like(kc), kc, kc**2]
    if parity:
        cols.append(np.where(k % 2 == 0, 1.0, -1.0))
    m = np.column_stack(cols)
    coef, *_ = np.linalg.lstsq(m, y, rcond=None)
    return y - m @ coef, coef


def extract_series(
    x: NDArray, sample_rate: float, bph: float, lift_angle_deg: float = 52.0
) -> BeatSeries:
    """Detect the beats of a mechanical watch and build the per-beat series.

    Raises:
        NotEnoughBeats: If fewer than ``MIN_BEATS`` beats have a fine timing.
    """
    fs = float(sample_rate)
    x = np.asarray(x, dtype=np.float64)
    beats, missed = _detect(x, fs, bph, lift_angle_deg)
    good = [b for b in beats if math.isfinite(b.t_fine)]
    if len(good) < MIN_BEATS:
        raise NotEnoughBeats(
            f"solo {len(good)} batimientos con tiempo fino (mínimo {MIN_BEATS})", len(good)
        )
    k = np.array([b.k for b in good], dtype=np.int64)
    t = np.array([b.t_fine for b in good]) / fs

    # timing: mean rate, slow drift and beat error removed; robust outlier rejection
    keep = np.ones(k.size, dtype=bool)
    for _ in range(3):
        res, _ = _detrend(k[keep].astype(float), t[keep], parity=True)
        dev = np.abs(res - np.median(res))
        ok = dev <= OUTLIER_SIGMA * max(1.4826 * float(np.median(dev)), 1e-9)
        if ok.all():
            break
        keep[np.flatnonzero(keep)[~ok]] = False
    outlier_t = t[~keep]
    kept = [b for b, kp in zip(good, keep, strict=True) if kp]
    k, t = k[keep], t[keep]
    res, _ = _detrend(k.astype(float), t, parity=True)
    slope = np.polyfit(k.astype(float), t, 1)[0]
    rate = (beat_period_s(bph) / slope - 1.0) * 86400.0

    # P3 energy: Teager-Kaiser of the band-passed signal over the drop
    sos = butter_bandpass_sos(4, (1000.0, min(15000.0, 0.45 * fs)), fs)
    psi = teager_kaiser(sosfiltfilt(sos, x))
    pre, post = (int(round(v * 1e-3 * fs)) for v in ENERGY_WINDOW_MS)
    idx = np.round(t * fs).astype(np.int64)
    energy = np.array(
        [psi[max(0, i - pre) : min(psi.size, i + post)].sum() for i in idx], dtype=np.float64
    )
    rel = np.empty_like(energy)
    for p in (0, 1):
        sel = k % 2 == p
        med = float(np.median(energy[sel])) if sel.any() else 0.0
        rel[sel] = energy[sel] / med - 1.0 if med > 0 else 0.0
    rel, _ = _detrend(k.astype(float), rel, parity=False)
    sub = _sub_events(kept, psi, fs, float(bph), float(lift_angle_deg))

    return BeatSeries(
        sample_rate=fs,
        bph=float(bph),
        duration_s=x.size / fs,
        beats_detected=len(beats),
        missing_beats=int(missed),
        rate_s_per_day=float(rate),
        k=k,
        t_s=t,
        timing_residual_s=res,
        energy_rel=rel,
        outliers=int(len(good) - k.size),
        outlier_t_s=outlier_t,
        lift_angle_deg=float(lift_angle_deg),
        **sub,
    )


def _sub_events(
    beats: list[Beat], psi: NDArray, fs: float, bph: float, lift_angle_deg: float
) -> dict[str, NDArray]:
    """Per-beat onsets (s), sub-event energies, pre-P1 peak (dB) and amplitude."""
    p = np.array([[b.p1, b.p2, b.p3] for b in beats], dtype=np.float64).reshape(-1, 3)
    n = p.shape[0]
    e = np.full((n, 3), np.nan)
    pre_db = np.full(n, np.nan)
    amp = np.full(n, np.nan)
    noise = float(np.median(np.abs(psi))) or 1e-30
    post = int(round(ENERGY_WINDOW_MS[1] * 1e-3 * fs))
    pre_a, pre_b = (
        int(round(PRE_P1_WINDOW_MS[0] * 1e-3 * fs)),
        int(round(PRE_P1_WINDOW_MS[1] * 1e-3 * fs)),
    )
    for i, (a, b, c) in enumerate(p):
        if math.isfinite(a):
            i1 = int(round(a))
            seg = psi[max(0, i1 - pre_a) : max(0, i1 - pre_b)]
            if seg.size:
                pre_db[i] = 10.0 * math.log10(max(float(seg.max()), 1e-30) / noise)
            if math.isfinite(c):
                amp[i] = amplitude_from_lift_time((c - a) / fs, lift_angle_deg, bph)
        if math.isfinite(a) and math.isfinite(b) and math.isfinite(c) and a < b < c:
            i1, i2, i3 = int(round(a)), int(round(b)), int(round(c))
            e[i] = (psi[i1:i2].sum(), psi[i2:i3].sum(), psi[i3 : min(psi.size, i3 + post)].sum())
    return {
        "p1_s": p[:, 0] / fs,
        "p2_s": p[:, 1] / fs,
        "p3_s": p[:, 2] / fs,
        "e1": e[:, 0],
        "e2": e[:, 1],
        "e3": e[:, 2],
        "pre_p1_db": pre_db,
        "amplitude_deg": amp,
    }


# ---------------------------------------------------------------------- spectra
def _fft(series: BeatSeries, y: NDArray) -> tuple[NDArray, NDArray]:
    """Amplitude spectrum on the uniform beat grid (holes filled linearly, Hann window)."""
    k = series.k
    grid = np.arange(k[0], k[-1] + 1)
    yu = np.interp(grid, k, y)
    n = yu.size
    win = np.hanning(n)
    spec = np.fft.rfft((yu - yu.mean()) * win)
    amp = 2.0 * np.abs(spec) / win.sum()
    dt = beat_period_s(series.bph)
    return np.fft.rfftfreq(n, dt), amp


def _spectrum(series: BeatSeries, y: NDArray, freqs: NDArray) -> Spectrum:
    fft_f, fft_a = _fft(series, y)
    return Spectrum(freqs, lomb_scargle(series.t_s, y, freqs), fft_f, fft_a)


def _resolvable(series: BeatSeries, period: float) -> bool:
    """The recording holds ``MIN_CYCLES`` turns of the wheel (fourth wheel: >= 180 s)."""
    return series.duration_s >= MIN_CYCLES * period


def _component(
    name: str, series_name: str, period: float, s: BeatSeries, y: NDArray, spec: Spectrum
) -> Component:
    span = float(s.t_s[-1] - s.t_s[0])
    resolvable = _resolvable(s, period)
    f0 = 1.0 / period
    df = 1.0 / span  # frequency resolution
    sel = np.abs(spec.freqs_hz - f0) <= df
    if not resolvable or not sel.any():
        return Component(name, series_name, period, math.nan, math.nan, math.nan, 1.0, False)
    i = np.flatnonzero(sel)[int(np.argmax(spec.ls_power[sel]))]
    f = float(spec.freqs_hz[i])
    amp, _ = sine_fit(s.t_s, y, f)
    m = 2.0 * df * span  # independent frequencies in the search window (~2)
    fap = _fap(float(spec.ls_power[i]), y.size, m)
    ripple = 2.0 * math.pi * amp * f * 86400.0 if series_name == SERIES_TIMING else math.nan
    return Component(name, series_name, period, 1.0 / f, amp, ripple, fap, True)


def spectral_report(
    series: BeatSeries, escape_teeth: int = DEFAULT_ESCAPE_TEETH
) -> GearTrainReport:
    """Spectra of both series and the escape / fourth wheel components (cheap: no detection)."""
    span = float(series.t_s[-1] - series.t_s[0])
    f_hi = 1.0 / (MIN_PERIOD_BEATS * beat_period_s(series.bph))
    df = 1.0 / (OVERSAMPLE * span)
    freqs = np.arange(1.0 / span, f_hi + df / 2, df)  # periods longer than the take mean nothing
    timing = _spectrum(series, series.timing_residual_s, freqs)
    energy = _spectrum(series, series.energy_rel, freqs)
    t_esc = escape_wheel_period_s(series.bph, escape_teeth)
    comps = []
    for name, period in ((COMPONENT_ESCAPE, t_esc), (COMPONENT_FOURTH, FOURTH_WHEEL_PERIOD_S)):
        comps.append(
            _component(name, SERIES_TIMING, period, series, series.timing_residual_s, timing)
        )
        comps.append(_component(name, SERIES_ENERGY, period, series, series.energy_rel, energy))
    warnings = []
    if not _resolvable(series, t_esc):
        warnings.append("short_for_escape")
    if not _resolvable(series, FOURTH_WHEEL_PERIOD_S):
        warnings.append("short_for_fourth")
    if series.missing_beats > 0.1 * (series.beats_detected + series.missing_beats):
        warnings.append("many_missing")
    return GearTrainReport(
        series=series,
        escape_teeth=int(escape_teeth),
        escape_period_s=t_esc,
        timing=timing,
        energy=energy,
        components=tuple(comps),
        warnings=tuple(warnings),
    )


def analyze_recording(
    x: NDArray,
    sample_rate: float,
    bph: float,
    lift_angle_deg: float = 52.0,
    escape_teeth: int = DEFAULT_ESCAPE_TEETH,
) -> GearTrainReport:
    """``extract_series`` + ``spectral_report`` (mechanical watches only)."""
    return spectral_report(extract_series(x, sample_rate, bph, lift_angle_deg), escape_teeth)
