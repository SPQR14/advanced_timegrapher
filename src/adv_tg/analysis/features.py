"""Feature vectors per 60 s window and rule-based diagnosis (no Qt; input for the future ML).

Built on ``gear_train.BeatSeries`` (one beat detection per recording).

Feature vector (``FeatureVector``), per 60 s window and for the whole recording:

* **Gear-train spectral energy** of the timing residual ``Delta t_k`` (Lomb-Scargle,
  normalised power, and the fitted amplitude) exactly at
  ``f_escape = f_beat / (2 Z)`` (``f_beat / 30`` for the usual 15 teeth) and at
  ``f_fourth = 1/60 Hz``. The fourth wheel is only given for the whole recording and only
  from ``FOURTH_WHEEL_MIN_RECORDING_S`` (180 s): one 60 s window holds a single turn, which
  cannot be told apart from the slow drift of the rate.
* **Higher-order statistics of the sub-impulses**: mean, variance, skewness and excess
  kurtosis of ``t_P2 - t_P1`` and ``t_P3 - t_P2``, and the median energy ratios
  ``E_P1 / E_P2`` and ``E_P3 / E_P2`` (Teager-Kaiser energy of each sub-event).
* Rate, beat error and median amplitude of the window, the fraction of beats with a
  spurious sound before P1 and the number of abrupt timing excursions.

Positional differentials (``PositionalDeltas``) from the saved positions:
``Delta Rate_H-V``, ``Delta Amp_H-V`` (horizontal DU/DD minus vertical CU/CD/CL/CR) and the
spread of the beat error between positions.

Diagnosis (``diagnose``): transparent rules over those features, each finding with its
evidence. **The thresholds are provisional** (``*_THRESHOLD`` constants below): they encode
the watchmaking criterion of each defect, not a calibration against labelled watches. That
calibration is precisely what the ML stage has to provide; until then a finding is an
indication to check on the bench, never a diagnosis.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field

import numpy as np
from numpy.typing import NDArray

from adv_tg.analysis.gear_train import (
    DEFAULT_ESCAPE_TEETH,
    FOURTH_WHEEL_MIN_RECORDING_S,
    FOURTH_WHEEL_PERIOD_S,
    BeatSeries,
    GearTrainReport,
    lomb_scargle,
    sine_fit,
)
from adv_tg.core.beat_types import beat_period_s

__all__ = [
    "HORIZONTAL_POSITIONS",
    "VERTICAL_POSITIONS",
    "WINDOW_S",
    "FeatureVector",
    "Finding",
    "PositionalDeltas",
    "diagnose",
    "positional_deltas",
    "recording_features",
    "window_features",
]

WINDOW_S: float = 60.0
#: A trailing window shorter than this joins the previous one.
MIN_WINDOW_S: float = 30.0
#: Beats a window needs for its statistics.
MIN_WINDOW_BEATS: int = 32
#: A sound this far (dB) above the median Teager-Kaiser level before P1 is spurious. The
#: noise reaches ~11 dB (95th percentile) on clean takes; 20 dB leaves a clear margin.
SPURIOUS_DB: float = 20.0

HORIZONTAL_POSITIONS: tuple[str, ...] = ("DU", "DD")
VERTICAL_POSITIONS: tuple[str, ...] = ("CU", "CD", "CL", "CR")

# --- provisional thresholds of the rules (to be calibrated with labelled watches / ML) ---
#: Rebanking: the ellipse can hit the outside of the fork horns above ~310-320 degrees.
REBANKING_AMPLITUDE_THRESHOLD_DEG: float = 310.0
#: Rebanking: fraction of beats with a spurious sound before P1.
REBANKING_SPURIOUS_THRESHOLD: float = 0.05
#: Pallet lubrication: P3 at least this many times the P2 energy (P2 >= 10 dB below P3).
LUBRICATION_E3_E2_THRESHOLD: float = 10.0
#: Pallet lubrication: coefficient of variation of t_P2 - t_P1 (clean takes: < 1 %).
LUBRICATION_D12_CV_THRESHOLD: float = 0.05
#: Magnetism / off-centre hairspring: strongly positive rate (s/d; above any ADC bias).
MAGNETISM_RATE_THRESHOLD_SPD: float = 30.0
#: ... with a beat error that changes with the position (max - min, ms) ...
MAGNETISM_BE_SPREAD_THRESHOLD_MS: float = 0.5
#: ... or, without positions, between 60 s windows of the recording (std, ms).
MAGNETISM_BE_WINDOW_STD_THRESHOLD_MS: float = 0.2

LEVEL_PROBABLE = "probable"
LEVEL_POSSIBLE = "possible"


@dataclass(frozen=True)
class FeatureVector:
    """Features of one window (or of the whole recording, ``whole=True``).

    Units in the names: ``_s``, ``_ms``, ``_ms2``, ``_us``, ``_deg``, ``_spd`` (s/d). Powers
    are normalised Lomb-Scargle powers (dimensionless); ``nan`` = not available.
    """

    window_start_s: float
    window_end_s: float
    whole: bool
    n_beats: int
    rate_spd: float
    beat_error_ms: float
    amplitude_deg: float
    escape_power: float
    escape_amp_us: float
    fourth_power: float
    fourth_amp_us: float
    d12_mean_ms: float
    d12_var_ms2: float
    d12_skew: float
    d12_kurt: float
    d23_mean_ms: float
    d23_var_ms2: float
    d23_skew: float
    d23_kurt: float
    n_subevents: int
    e1_e2: float
    e3_e2: float
    spurious_fraction: float
    timing_jumps: int

    def as_dict(self) -> dict[str, float | int | bool | None]:
        """Plain values for JSON (NaN -> None)."""
        return {
            k: (None if isinstance(v, float) and not math.isfinite(v) else v)
            for k, v in asdict(self).items()
        }


@dataclass(frozen=True)
class PositionalDeltas:
    """Horizontal (DU, DD) minus vertical (CU, CD, CL, CR) differentials."""

    positions: tuple[str, ...]
    delta_rate_hv_spd: float
    delta_amp_hv_deg: float
    be_spread_ms: float

    def as_dict(self) -> dict[str, object]:
        d = asdict(self)
        return {
            k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in d.items()
        }


@dataclass(frozen=True)
class Finding:
    """One rule that fired: ``code`` (rebanking, pallet_lubrication, magnetism,
    escape_wheel, fourth_wheel), ``level`` (probable / possible) and the evidence."""

    code: str
    level: str
    evidence: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------- statistics
def _moments(v: NDArray) -> tuple[float, float, float, float]:
    """Mean, variance, skewness and excess kurtosis (NaN below 4 values or no spread)."""
    v = v[np.isfinite(v)]
    if v.size < 4:
        return (math.nan,) * 4
    mean = float(v.mean())
    c = v - mean
    m2 = float(np.mean(c**2))
    if m2 <= 0.0:
        return mean, 0.0, math.nan, math.nan
    skew = float(np.mean(c**3)) / m2**1.5
    kurt = float(np.mean(c**4)) / m2**2 - 3.0
    return mean, m2, skew, kurt


def _median_ratio(a: NDArray, b: NDArray) -> float:
    ok = np.isfinite(a) & np.isfinite(b) & (b > 0) & (a > 0)
    return float(np.median(a[ok] / b[ok])) if ok.any() else math.nan


def _rate_and_be(k: NDArray, t: NDArray, bph: float) -> tuple[float, float]:
    """Rate (s/d) and beat error (ms) of ``t = a + b k + d (-1)^k``."""
    if k.size < 8:
        return math.nan, math.nan
    sign = np.where(k % 2 == 0, 1.0, -1.0)
    m = np.column_stack((np.ones(k.size), k.astype(float), sign))
    (_, slope, d), *_ = np.linalg.lstsq(m, t, rcond=None)
    rate = (beat_period_s(bph) / slope - 1.0) * 86400.0
    # intervals alternate P +- 4d: the beat error (half the difference) is 2|d|
    return float(rate), float(2.0 * abs(d) * 1e3)


def _power_at(t: NDArray, y: NDArray, f: float) -> tuple[float, float]:
    """Normalised Lomb-Scargle power and fitted amplitude (µs if ``y`` in s) at ``f``."""
    if t.size < 8 or not math.isfinite(f):
        return math.nan, math.nan
    power = float(lomb_scargle(t, y, np.array([f]))[0])
    amp, _ = sine_fit(t, y, f)
    return power, amp * 1e6


# ---------------------------------------------------------------------- vectors
def _vector(
    s: BeatSeries, sel: NDArray[np.bool_], t0: float, t1: float, whole: bool, teeth: int
) -> FeatureVector:
    k, t = s.k[sel], s.t_s[sel]
    res = s.timing_residual_s[sel]
    f_beat = 1.0 / beat_period_s(s.bph)  # beats per second
    rate, be = _rate_and_be(k, t, s.bph)
    esc_p, esc_a = _power_at(t, res, f_beat / (2.0 * teeth))
    four_p = four_a = math.nan
    if whole and s.duration_s >= FOURTH_WHEEL_MIN_RECORDING_S:
        four_p, four_a = _power_at(t, res, 1.0 / FOURTH_WHEEL_PERIOD_S)

    p1, p2, p3 = (a[sel] if a.size else np.zeros(0) for a in (s.p1_s, s.p2_s, s.p3_s))
    d12 = (p2 - p1) * 1e3 if p1.size else np.zeros(0)
    d23 = (p3 - p2) * 1e3 if p1.size else np.zeros(0)
    m12, m23 = _moments(d12), _moments(d23)
    e1, e2, e3 = (a[sel] if a.size else np.zeros(0) for a in (s.e1, s.e2, s.e3))
    pre = s.pre_p1_db[sel] if s.pre_p1_db.size else np.zeros(0)
    pre = pre[np.isfinite(pre)]
    amp = s.amplitude_deg[sel] if s.amplitude_deg.size else np.zeros(0)
    amp = amp[np.isfinite(amp)]
    jumps = int(np.count_nonzero((s.outlier_t_s >= t0) & (s.outlier_t_s < t1)))
    return FeatureVector(
        window_start_s=float(t0),
        window_end_s=float(t1),
        whole=whole,
        n_beats=int(k.size),
        rate_spd=rate,
        beat_error_ms=be,
        amplitude_deg=float(np.median(amp)) if amp.size else math.nan,
        escape_power=esc_p,
        escape_amp_us=esc_a,
        fourth_power=four_p,
        fourth_amp_us=four_a,
        d12_mean_ms=m12[0],
        d12_var_ms2=m12[1],
        d12_skew=m12[2],
        d12_kurt=m12[3],
        d23_mean_ms=m23[0],
        d23_var_ms2=m23[1],
        d23_skew=m23[2],
        d23_kurt=m23[3],
        n_subevents=int(np.count_nonzero(np.isfinite(e1))),
        e1_e2=_median_ratio(e1, e2),
        e3_e2=_median_ratio(e3, e2),
        spurious_fraction=float(np.mean(pre > SPURIOUS_DB)) if pre.size else math.nan,
        timing_jumps=jumps,
    )


def window_features(
    series: BeatSeries, teeth: int = DEFAULT_ESCAPE_TEETH, window_s: float = WINDOW_S
) -> list[FeatureVector]:
    """One ``FeatureVector`` per ``window_s`` of the recording (from its first beat)."""
    t = series.t_s
    if t.size == 0:
        return []
    start, end = float(t[0]), float(t[-1])
    edges = list(np.arange(start, end, window_s)) + [end]
    if len(edges) > 2 and edges[-1] - edges[-2] < MIN_WINDOW_S:
        del edges[-2]  # a short tail joins the previous window
    out = []
    for i, (a, b) in enumerate(zip(edges[:-1], edges[1:], strict=True)):
        last = i == len(edges) - 2
        sel = (t >= a) & ((t <= b) if last else (t < b))
        if np.count_nonzero(sel) >= MIN_WINDOW_BEATS:
            out.append(_vector(series, sel, a, b if not last else b + 1e-9, False, teeth))
    return out


def recording_features(series: BeatSeries, teeth: int = DEFAULT_ESCAPE_TEETH) -> FeatureVector:
    """``FeatureVector`` of the whole recording (with the fourth wheel from 180 s)."""
    sel = np.ones(series.k.size, dtype=bool)
    end = float(series.t_s[-1]) + 1e-9 if series.t_s.size else 0.0
    start = float(series.t_s[0]) if series.t_s.size else 0.0
    return _vector(series, sel, start, end, True, teeth)


def positional_deltas(
    positions: Mapping[str, tuple[float, float, float]],
) -> PositionalDeltas | None:
    """``positions``: name -> (rate s/d, beat error ms, amplitude deg). None without H and V."""
    h = [positions[p] for p in HORIZONTAL_POSITIONS if p in positions]
    v = [positions[p] for p in VERTICAL_POSITIONS if p in positions]
    if not h or not v:
        return None
    bes = [rec[1] for rec in positions.values() if math.isfinite(rec[1])]
    return PositionalDeltas(
        positions=tuple(p for p in (*HORIZONTAL_POSITIONS, *VERTICAL_POSITIONS) if p in positions),
        delta_rate_hv_spd=float(np.mean([r[0] for r in h]) - np.mean([r[0] for r in v])),
        delta_amp_hv_deg=float(np.nanmean([r[2] for r in h]) - np.nanmean([r[2] for r in v])),
        be_spread_ms=float(max(bes) - min(bes)) if len(bes) >= 2 else math.nan,
    )


# ---------------------------------------------------------------------- diagnosis
def _level(n_signs: int, needed: int) -> str | None:
    if n_signs >= needed:
        return LEVEL_PROBABLE
    if n_signs == needed - 1 and n_signs > 0:
        return LEVEL_POSSIBLE
    return None


def diagnose(
    whole: FeatureVector,
    windows: list[FeatureVector],
    positional: PositionalDeltas | None = None,
    gear: GearTrainReport | None = None,
) -> list[Finding]:
    """Rules over the features (see the module docstring: provisional thresholds)."""
    out: list[Finding] = []

    # Rebanking / knocking: amplitude > 310 deg AND spurious sound before P1 AND abrupt
    # timing excursions. The amplitude is required; each other sign adds one.
    if (
        math.isfinite(whole.amplitude_deg)
        and whole.amplitude_deg > REBANKING_AMPLITUDE_THRESHOLD_DEG
    ):
        spur = math.isfinite(whole.spurious_fraction) and (
            whole.spurious_fraction > REBANKING_SPURIOUS_THRESHOLD
        )
        signs = int(spur) + int(whole.timing_jumps > 0)
        level = LEVEL_PROBABLE if signs == 2 else (LEVEL_POSSIBLE if signs == 1 else None)
        if level:
            out.append(Finding("rebanking", level, {
                "amplitude_deg": whole.amplitude_deg,
                "spurious_fraction": whole.spurious_fraction,
                "timing_jumps": float(whole.timing_jumps),
            }))  # fmt: skip

    # Pallet lubrication: weak impulse (P2) against the drop (P3) AND unstable P1 -> P2.
    cv12 = (
        math.sqrt(whole.d12_var_ms2) / whole.d12_mean_ms
        if math.isfinite(whole.d12_var_ms2) and whole.d12_mean_ms > 0
        else math.nan
    )
    signs = int(math.isfinite(whole.e3_e2) and whole.e3_e2 > LUBRICATION_E3_E2_THRESHOLD) + int(
        math.isfinite(cv12) and cv12 > LUBRICATION_D12_CV_THRESHOLD
    )
    level = _level(signs, 2)
    if level:
        out.append(Finding("pallet_lubrication", level, {"e3_e2": whole.e3_e2, "d12_cv": cv12}))

    # Magnetism / off-centre hairspring: strongly positive rate AND a beat error that changes
    # with the position (or, without positions, between windows: weaker evidence).
    if math.isfinite(whole.rate_spd) and whole.rate_spd > MAGNETISM_RATE_THRESHOLD_SPD:
        if positional is not None and math.isfinite(positional.be_spread_ms):
            if positional.be_spread_ms > MAGNETISM_BE_SPREAD_THRESHOLD_MS:
                out.append(Finding("magnetism", LEVEL_PROBABLE, {
                    "rate_spd": whole.rate_spd, "be_spread_ms": positional.be_spread_ms,
                }))  # fmt: skip
        else:
            bes = [w.beat_error_ms for w in windows if math.isfinite(w.beat_error_ms)]
            be_std = float(np.std(bes)) if len(bes) >= 2 else math.nan
            if math.isfinite(be_std) and be_std > MAGNETISM_BE_WINDOW_STD_THRESHOLD_MS:
                out.append(Finding("magnetism", LEVEL_POSSIBLE, {
                    "rate_spd": whole.rate_spd, "be_window_std_ms": be_std,
                }))  # fmt: skip

    # Gear train: one finding per wheel with a significant component (gear_train)
    if gear is not None:
        for name in ("escape_wheel", "fourth_wheel"):
            evidence: dict[str, float] = {}
            for c in gear.components:
                if c.name != name or not c.significant:
                    continue
                evidence["period_s"] = c.period_s
                if c.series == "timing":
                    evidence["amplitude_us"] = c.amplitude * 1e6
                    evidence["rate_ripple_spd"] = c.rate_ripple_s_per_day
                else:
                    evidence["energy_mod"] = c.amplitude
                evidence["fap"] = min(evidence.get("fap", 1.0), c.fap)
            if evidence:
                out.append(Finding(name, LEVEL_PROBABLE, evidence))
    return out
