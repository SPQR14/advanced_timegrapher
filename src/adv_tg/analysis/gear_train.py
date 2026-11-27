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

from adv_tg import native_bridge as _nb
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
    if _nb.have("advtg_escape_wheel_period_s"):
        return float(_nb.lib().advtg_escape_wheel_period_s(float(bph), int(teeth)))  # type: ignore[union-attr]
    return 2.0 * teeth * 3600.0 / float(bph)


def teager_kaiser(x: NDArray) -> NDArray[np.float64]:
    """Teager-Kaiser energy ``x[n]^2 - x[n-1] x[n+1]`` (edges repeat their neighbour)."""
    x = np.asarray(x, dtype=np.float64)
    if x.size < 3:
        return np.zeros(x.size)
    if _nb.have("advtg_teager_kaiser"):
        xc = _nb.f64(x.reshape(-1))
        out = np.empty(xc.size, dtype=np.float64)
        _nb.lib().advtg_teager_kaiser(_nb.ptr(xc), xc.size, _nb.ptr(out))
        return out.reshape(x.shape)
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
    y_in = np.asarray(y, dtype=np.float64)
    freqs = np.asarray(freqs, dtype=np.float64)
    if _nb.have("advtg_lomb_scargle") and t.ndim <= 1 and y_in.ndim <= 1 and t.size == y_in.size:
        tc = _nb.f64(t.reshape(-1))
        yc = _nb.f64(y_in.reshape(-1))
        fc = _nb.f64(freqs.reshape(-1))
        out = np.zeros(fc.size, dtype=np.float64)
        if tc.size >= 3 and fc.size > 0:
            _nb.lib().advtg_lomb_scargle(
                _nb.ptr(tc), _nb.ptr(yc), tc.size, _nb.ptr(fc), fc.size, _nb.ptr(out)
            )
        return out.reshape(freqs.shape)
    y = y_in - np.mean(y_in)
    var = float(np.var(y))
    out = np.zeros(freqs.size)
    if var <= 0.0 or t.size < 3:
        return out
    n_t = t.size
    for a in range(0, freqs.size, chunk):
        w = 2.0 * np.pi * freqs[a : a + chunk, None]
        wt = w * t
        c0 = np.cos(wt)
        s0 = np.sin(wt)
        # Double-angle identities reuse c0/s0 instead of evaluating 4 trig matrices per chunk.
        s2 = 2.0 * np.einsum("ij,ij->i", s0, c0)
        c2 = 2.0 * np.einsum("ij,ij->i", c0, c0) - n_t
        wt_tau = 0.5 * np.arctan2(s2, c2)
        ct = np.cos(wt_tau)
        st = np.sin(wt_tau)
        cy = c0 @ y
        sy = s0 @ y
        yc = cy * ct + sy * st
        ys = sy * ct - cy * st
        r = np.hypot(c2, s2)
        cc = 0.5 * (n_t + r)
        ss = 0.5 * (n_t - r)
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


class _NativeGearHandle:
    __slots__ = ("ptr",)

    def __init__(self, ptr: int) -> None:
        self.ptr = int(ptr)

    def __del__(self) -> None:
        p = getattr(self, "ptr", 0)
        if p and _nb is not None:
            cdll = _nb.lib()
            if cdll is not None and hasattr(cdll, "advtg_gear_destroy"):
                import ctypes

                cdll.advtg_gear_destroy(ctypes.c_void_p(p))
            self.ptr = 0


def _native_gear_handle(series: BeatSeries) -> _NativeGearHandle | None:
    meta = getattr(series, "_native", None)
    if meta is None:
        return None
    handle, fp = meta
    if fp != (series.duration_s, int(series.k.size), float(series.rate_s_per_day)):
        return None
    return handle if getattr(handle, "ptr", 0) else None


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
    _native: tuple[_NativeGearHandle, tuple[float, int, float]] | None = field(
        default=None, repr=False, compare=False
    )


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
    if _nb.have(
        "advtg_gear_extract",
        "advtg_gear_destroy",
        "advtg_gear_get_summary",
        "advtg_gear_series_copy",
        "advtg_gear_outliers_copy",
    ):
        import ctypes

        cdll = _nb.lib()
        assert cdll is not None
        xc = _nb.f64(x.reshape(-1))
        err_c = ctypes.c_int32(0)
        beats_c = ctypes.c_int64(0)
        h = cdll.advtg_gear_extract(
            _nb.ptr(xc),
            xc.size,
            fs,
            float(bph),
            float(lift_angle_deg),
            DEFAULT_ESCAPE_TEETH,
            ctypes.byref(err_c),
            ctypes.byref(beats_c),
        )
        if not h:
            msg = cdll.advtg_gear_last_error().decode("utf-8", errors="replace")
            if err_c.value == -5:
                raise NotEnoughBeats(
                    msg
                    or f"solo {int(beats_c.value)} batimientos con tiempo fino (mínimo {MIN_BEATS})",
                    int(beats_c.value),
                )
            raise ValueError(msg or f"error nativo gear_extract ({err_c.value})")
        handle = _NativeGearHandle(int(h))
        summary = _nb.NativeGearSummaryStruct()
        cdll.advtg_gear_get_summary(ctypes.c_void_p(handle.ptr), ctypes.byref(summary))
        n_s = int(summary.n_series)
        k = np.empty(n_s, dtype=np.int64)
        t_s = np.empty(n_s, dtype=np.float64)
        timing_res = np.empty(n_s, dtype=np.float64)
        energy_rel = np.empty(n_s, dtype=np.float64)
        p1_s = np.empty(n_s, dtype=np.float64)
        p2_s = np.empty(n_s, dtype=np.float64)
        p3_s = np.empty(n_s, dtype=np.float64)
        e1 = np.empty(n_s, dtype=np.float64)
        e2 = np.empty(n_s, dtype=np.float64)
        e3 = np.empty(n_s, dtype=np.float64)
        pre_p1_db = np.empty(n_s, dtype=np.float64)
        amp_deg = np.empty(n_s, dtype=np.float64)
        cdll.advtg_gear_series_copy(
            ctypes.c_void_p(handle.ptr),
            _nb.ptr(k),
            _nb.ptr(t_s),
            _nb.ptr(timing_res),
            _nb.ptr(energy_rel),
            _nb.ptr(p1_s),
            _nb.ptr(p2_s),
            _nb.ptr(p3_s),
            _nb.ptr(e1),
            _nb.ptr(e2),
            _nb.ptr(e3),
            _nb.ptr(pre_p1_db),
            _nb.ptr(amp_deg),
            n_s,
        )
        n_out = max(0, int(summary.outliers))
        outlier_t = np.empty(n_out, dtype=np.float64)
        if n_out > 0:
            cdll.advtg_gear_outliers_copy(ctypes.c_void_p(handle.ptr), _nb.ptr(outlier_t), n_out)
        fp = (float(summary.duration_s), int(k.size), float(summary.rate_s_per_day))
        return BeatSeries(
            sample_rate=float(summary.sample_rate),
            bph=float(summary.bph),
            duration_s=float(summary.duration_s),
            beats_detected=int(summary.beats_detected),
            missing_beats=int(summary.missing_beats),
            rate_s_per_day=float(summary.rate_s_per_day),
            k=k,
            t_s=t_s,
            timing_residual_s=timing_res,
            energy_rel=energy_rel,
            outliers=n_out,
            outlier_t_s=outlier_t,
            lift_angle_deg=float(summary.lift_angle_deg),
            p1_s=p1_s,
            p2_s=p2_s,
            p3_s=p3_s,
            e1=e1,
            e2=e2,
            e3=e3,
            pre_p1_db=pre_p1_db,
            amplitude_deg=amp_deg,
            _native=(handle, fp),
        )
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

    # P3 energy: Teager-Kaiser of the band-passed signal over the drop (O(1) per beat via cumsum)
    sos = butter_bandpass_sos(4, (1000.0, min(15000.0, 0.45 * fs)), fs)
    psi = teager_kaiser(sosfiltfilt(sos, x))
    psi_cs = np.empty(psi.size + 1, dtype=np.float64)
    psi_cs[0] = 0.0
    np.cumsum(psi, out=psi_cs[1:])
    pre, post = (int(round(v * 1e-3 * fs)) for v in ENERGY_WINDOW_MS)
    idx = np.round(t * fs).astype(np.int64)
    lo_e = np.clip(idx - pre, 0, psi.size)
    hi_e = np.clip(idx + post, 0, psi.size)
    energy = psi_cs[hi_e] - psi_cs[lo_e]
    rel = np.empty_like(energy)
    for p in (0, 1):
        sel = k % 2 == p
        med = float(np.median(energy[sel])) if sel.any() else 0.0
        rel[sel] = energy[sel] / med - 1.0 if med > 0 else 0.0
    rel, _ = _detrend(k.astype(float), rel, parity=False)
    sub = _sub_events(kept, psi, psi_cs, fs, float(bph), float(lift_angle_deg))

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
    beats: list[Beat],
    psi: NDArray,
    psi_cs: NDArray,
    fs: float,
    bph: float,
    lift_angle_deg: float,
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
    psi_n = psi.size
    for i, (a, b, c) in enumerate(p):
        if math.isfinite(a):
            i1 = int(round(a))
            seg = psi[max(0, i1 - pre_a) : max(0, i1 - pre_b)]
            if seg.size:
                pre_db[i] = 10.0 * math.log10(max(float(seg.max()), 1e-30) / noise)
            if math.isfinite(c):
                amp[i] = amplitude_from_lift_time((c - a) / fs, lift_angle_deg, bph)
        if math.isfinite(a) and math.isfinite(b) and math.isfinite(c) and a < b < c:
            i1, i2, i3 = (
                min(max(int(round(a)), 0), psi_n),
                min(max(int(round(b)), 0), psi_n),
                min(max(int(round(c)), 0), psi_n),
            )
            i4 = min(psi_n, i3 + post)
            e[i] = (psi_cs[i2] - psi_cs[i1], psi_cs[i3] - psi_cs[i2], psi_cs[i4] - psi_cs[i3])
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
    handle = _native_gear_handle(series)
    if handle is not None and _nb.have(
        "advtg_gear_recompute",
        "advtg_gear_get_summary",
        "advtg_gear_get_components",
        "advtg_gear_spectra_len",
        "advtg_gear_spectra_copy",
    ):
        import ctypes

        cdll = _nb.lib()
        assert cdll is not None
        hp = ctypes.c_void_p(handle.ptr)
        if int(cdll.advtg_gear_recompute(hp, int(escape_teeth))) == 0:
            summary = _nb.NativeGearSummaryStruct()
            cdll.advtg_gear_get_summary(hp, ctypes.byref(summary))
            comps_c = (_nb.NativeGearComponentStruct * 4)()
            cdll.advtg_gear_get_components(hp, comps_c)
            ls_len = ctypes.c_int64(0)
            fft_len = ctypes.c_int64(0)
            cdll.advtg_gear_spectra_len(hp, ctypes.byref(ls_len), ctypes.byref(fft_len))
            n_ls = max(0, int(ls_len.value))
            n_fft = max(0, int(fft_len.value))

            def _copy_spec(kind: int) -> Spectrum:
                freqs = np.empty(n_ls, dtype=np.float64)
                lsp = np.empty(n_ls, dtype=np.float64)
                ff = np.empty(n_fft, dtype=np.float64)
                fa = np.empty(n_fft, dtype=np.float64)
                cdll.advtg_gear_spectra_copy(
                    hp,
                    kind,
                    _nb.ptr(freqs),
                    _nb.ptr(lsp),
                    n_ls,
                    _nb.ptr(ff),
                    _nb.ptr(fa),
                    n_fft,
                )
                return Spectrum(freqs_hz=freqs, ls_power=lsp, fft_freqs_hz=ff, fft_amplitude=fa)

            timing = _copy_spec(0)
            energy = _copy_spec(1)
            comps = tuple(
                Component(
                    name=COMPONENT_ESCAPE if comps_c[i].name == 0 else COMPONENT_FOURTH,
                    series=SERIES_TIMING if comps_c[i].series == 0 else SERIES_ENERGY,
                    expected_period_s=float(comps_c[i].expected_period_s),
                    period_s=float(comps_c[i].period_s),
                    amplitude=float(comps_c[i].amplitude),
                    rate_ripple_s_per_day=float(comps_c[i].rate_ripple_s_per_day),
                    fap=float(comps_c[i].fap),
                    resolvable=bool(comps_c[i].resolvable),
                )
                for i in range(4)
            )
            wmask = int(summary.warning_mask)
            warns = []
            if wmask & 1:
                warns.append("short_for_escape")
            if wmask & 2:
                warns.append("short_for_fourth")
            if wmask & 4:
                warns.append("many_missing")
            return GearTrainReport(
                series=series,
                escape_teeth=int(escape_teeth),
                escape_period_s=float(summary.escape_period_s),
                timing=timing,
                energy=energy,
                components=comps,
                warnings=tuple(warns),
            )
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
