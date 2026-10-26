"""Layer 3: metrics (rate, beat error, amplitude) from the beats of layer 2.

Pure library: no Qt, no audio. Inputs are the ``Beat`` list and the two parity templates of
``BeatDetector.templates()``; every instant is an absolute sample index in the ADC clock.
Per-beat ``p1/p2/p3`` are NOT used (they are fragile when tic and tac sound differently; see
docs/resultados_pruebas_fase2.md, sections 6-7).

Model and conventions
---------------------
* **Beat selection.** A beat enters the regressions when ``t_fine`` is finite and it is not
  ``SATURATED``. ``TIMING_OUTLIER`` beats are kept: that flag judges the per-beat P3 anchor
  against the phase model, while ``t_fine`` comes from the template alignment and does not
  depend on it; true timing outliers of ``t_fine`` are removed by the robust per-parity
  trimming (on the real takes this keeps 11-30 % more beats with the same figures, see
  docs/fase3_resultados.md, 6.8). ``AFTER_GAP`` beats are kept too: the integer ``k``
  already absorbs the gap. Below ``min_beats_per_parity`` valid beats in either parity no
  figure is given (NaN).
* **Parity-weighted robust regression** ``t = a + P k + c (-1)^k`` (``k`` even = tic):
  start unweighted; then, per parity, sigma = 1.4826 * MAD of that parity's residuals, beats
  beyond ``outlier_sigma`` sigmas of their parity are trimmed and each parity is weighted by
  ``1 / sigma_parity^2``; repeat until the kept set does not change (at most
  ``max_iterations``). Why: one parity can be much noisier (H-50 / 4R36 tac, 500-830 µs vs
  110-220 µs); unweighted, it drags the rate (H-50 +8.1 -> +10.7 s/d). The covariance
  ``(X^T W X)^-1`` gives the statistical uncertainties (weights are inverse variances, so
  no extra scale factor).
* **Rate** ``= (P_nominal / P_true - 1) * 86400`` s/d, positive = gaining.
  ``P_true = P_samples / (fs * (1 + adc_drift_ppm * 1e-6))``: ``adc_drift_ppm`` > 0 means the
  ADC runs fast (more samples per true second). It is the hook for layer 4; with 0 the rate
  is flagged as not calibrated. ``adc_drift_sigma_ppm`` (its uncertainty) is added in
  quadrature to the rate uncertainty (``rate_clock_sigma_s_per_day``).
* **Windows and trend.** The rate is also fitted on consecutive, non-overlapping windows of
  ``window_s`` seconds (same regression), for display. The trend (s/d per minute) comes from
  a quadratic term in the same parity-weighted regression over the whole span,
  ``t = a + P k + c (-1)^k + q k^2`` (``dP/dk = 2 q``). Its uncertainty is a delete-one-block
  jackknife over ``trend_blocks`` contiguous time blocks: real recordings have correlated
  timing noise (lobe changes, template evolution) that a white-noise covariance ignores; with
  two 10 s windows on a 30 s take, a slope of window rates flagged clean watches (Miyota,
  H-50) at -23 and -39 s/d/min. The trend is flagged when ``|trend| > trend_sigma * sigma``
  (3 sigma by default).
* **Uncertainties.** Each figure has a covariance sigma (``*_sigma_*``, white-noise model, the
  one the synthetic coverage test checks) and a block-jackknife sigma (``*_sigma_block_*``,
  same contiguous blocks as the trend), which also captures the correlated timing noise of
  real recordings (2-4x larger there). The reported uncertainty (``*_uncertainty_*``) is the
  larger of the two. Each window reports its covariance sigma times ``window_sigma_factor``:
  ``sqrt(reduced chi^2)`` of the window rates around the trend line when there are >= 6
  windows, else the block/covariance ratio of the take; never below 1 (on the quartz
  references the covariance sigma of a window was 1.4-4.8x too small).
* **Beat error.** Each beat's P1 instant is projected from its parity template,
  ``t_p1 = t_fine - (onset_ref - onset_p1)``, and the same regression gives ``c``; the beat
  error is ``2 |c|`` (half the difference between the tic->tac and tac->tic intervals). If a
  template has no P1 onset, ``c`` of the ``t_fine`` regression is used and a warning says
  that the measuring point changed.
* **Amplitude.** ``A = LA / (2 sin(pi f t_lift))``, ``f = BPH / 7200``, with ``t_lift`` from
  the templates ONLY (``ParityTemplate.t_lift_samples``: P1 onset -> P3 onset). Tic, tac and
  their mean are reported; outside ``amplitude_range_deg`` a value is invalid (NaN). With the
  default lift angle (52°) the amplitude is flagged as indicative. The uncertainty for
  +-1° of LA is ``A(LA+1) - A(LA)`` (~ A / LA, ~2 %).
* **Confidence** in [0, 1] (a ranking, not a probability), product of:
  ``q_n = min(1, n_min / (3 * min_beats_per_parity))`` (n_min = valid beats of the poorer
  parity), ``q_sigma = 1 / (1 + sigma_max / 500 µs)``, ``q_excl = 1 - excluded_fraction`` and
  ``q_amp = 1 / (1 + |A_tic - A_tac| / 20°)`` (1 when the amplitude is not available).
  ``timing_confidence`` is the same product without ``q_amp``: the confidence of the rate and
  the beat error, which a tic/tac amplitude mismatch does not affect.

Batch and incremental: ``compute_metrics`` works on a list of beats; ``MetricsTracker`` keeps
the beats of the last ``span_s`` seconds (by ``t_fine``) and computes the same function on
them, so both modes give identical numbers on the same beats.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace

import numpy as np
from numpy.typing import NDArray

from adv_tg.core.beat_types import (
    Beat,
    BeatFlags,
    ParityTemplate,
    amplitude_from_lift_time,
    beat_period_s,
)

__all__ = [
    "DEFAULT_LIFT_ANGLE_DEG",
    "Metrics",
    "MetricsConfig",
    "MetricsTracker",
    "ParityFit",
    "WindowRate",
    "amplitude_from_templates",
    "beat_error_from_fit",
    "compute_metrics",
    "fit_parity_weighted",
    "format_beat_error_ms",
    "rate_from_period",
    "rate_trend",
    "select_beats",
    "timing_confidence",
    "window_rates",
]

DEFAULT_LIFT_ANGLE_DEG = 52.0
_EXCLUDE = BeatFlags.SATURATED
_SIGMA_FLOOR = 1e-3  # samples: avoids infinite weights on noiseless synthetic data


# ====================================================================== configuration
@dataclass(frozen=True)
class MetricsConfig:
    """Parameters of layer 3.

    Attributes:
        sample_rate: Nominal ADC sample rate in Hz.
        bph: Nominal beats per hour.
        lift_angle_deg: Lift angle; None = unknown (52° is used and the amplitude is flagged
            as indicative).
        quartz: Quartz movement: only the rate is computed (no beat error, no amplitude).
        min_beats_per_parity: Valid beats each parity needs for a figure (default 10).
        outlier_sigma: Per-parity trimming in robust sigmas (default 5).
        max_iterations: Iterations of the robust weighted regression (default 10).
        window_s: Length of the rate windows in seconds (default 10).
        window_step_s: Step between window starts (default None = ``window_s``, i.e.
            consecutive windows). A smaller step gives overlapping windows for plotting; the
            trend does not use the windows, so it is not affected.
        trend_sigma: The trend is flagged beyond this many sigmas of its slope (default 3).
        trend_blocks: Contiguous time blocks of the jackknife (default 6).
        amplitude_range_deg: Valid amplitudes (default 90-400°).
        amplitude_mismatch_deg: Tic/tac amplitude difference that raises a warning (20°).
        adc_drift_ppm: ADC clock error in ppm (> 0 = ADC fast); 0 = not calibrated.
        adc_drift_sigma_ppm: Total uncertainty of ``adc_drift_ppm`` (``AdcDrift.uncertainty_ppm``
            of layer 4). It is added in quadrature to the rate uncertainty; a value > 0 also
            marks the rate as calibrated when the measured drift is exactly 0.
    """

    sample_rate: float
    bph: float
    lift_angle_deg: float | None = None
    quartz: bool = False
    min_beats_per_parity: int = 10
    outlier_sigma: float = 5.0
    max_iterations: int = 10
    window_s: float = 10.0
    window_step_s: float | None = None
    trend_sigma: float = 3.0
    trend_blocks: int = 6
    amplitude_range_deg: tuple[float, float] = (90.0, 400.0)
    amplitude_mismatch_deg: float = 20.0
    adc_drift_ppm: float = 0.0
    adc_drift_sigma_ppm: float = 0.0

    def __post_init__(self) -> None:
        if not (math.isfinite(self.sample_rate) and self.sample_rate > 0):
            raise ValueError(f"sample_rate no válido: {self.sample_rate}")
        if not (math.isfinite(self.bph) and self.bph > 0):
            raise ValueError(f"bph no válido: {self.bph}")
        if self.lift_angle_deg is not None and not 0.0 < self.lift_angle_deg < 90.0:
            raise ValueError(f"lift_angle_deg fuera de (0, 90): {self.lift_angle_deg}")
        if self.min_beats_per_parity < 3:
            raise ValueError("min_beats_per_parity debe ser >= 3")
        if not self.outlier_sigma > 0:
            raise ValueError("outlier_sigma debe ser positivo")
        if self.max_iterations < 1:
            raise ValueError("max_iterations debe ser >= 1")
        if not self.window_s > 0:
            raise ValueError("window_s debe ser positivo")
        if self.window_step_s is not None and not 0 < self.window_step_s <= self.window_s:
            raise ValueError("window_step_s debe estar en (0, window_s]")
        if not self.trend_sigma > 0:
            raise ValueError("trend_sigma debe ser positivo")
        if self.trend_blocks < 3:
            raise ValueError("trend_blocks debe ser >= 3")
        lo, hi = self.amplitude_range_deg
        if not 0.0 < lo < hi:
            raise ValueError(f"amplitude_range_deg no válido: {self.amplitude_range_deg}")
        if not self.amplitude_mismatch_deg > 0:
            raise ValueError("amplitude_mismatch_deg debe ser positivo")
        if not math.isfinite(self.adc_drift_ppm):
            raise ValueError("adc_drift_ppm no válido")
        if not (math.isfinite(self.adc_drift_sigma_ppm) and self.adc_drift_sigma_ppm >= 0):
            raise ValueError("adc_drift_sigma_ppm no válido")

    @property
    def effective_lift_angle_deg(self) -> float:
        return self.lift_angle_deg if self.lift_angle_deg is not None else DEFAULT_LIFT_ANGLE_DEG

    @property
    def nominal_period_samples(self) -> float:
        return beat_period_s(self.bph) * self.sample_rate


# ====================================================================== results
@dataclass(frozen=True)
class ParityFit:
    """Result of the parity-weighted robust regression ``t = a + P k + c (-1)^k``.

    Instants and ``k`` are re-centred (``a`` refers to ``k = k_ref``); all values in samples.
    """

    a: float
    period: float
    c: float
    period_sigma: float
    c_sigma: float
    k_ref: float
    sigma_even: float
    sigma_odd: float
    n_even: int
    n_odd: int
    n_trimmed: int
    q: float = math.nan  # quadratic coefficient (samples per beat^2), quadratic fits only
    q_sigma: float = math.nan


@dataclass(frozen=True)
class WindowRate:
    """Rate over one window."""

    start_s: float
    end_s: float
    center_s: float
    rate_s_per_day: float
    rate_sigma_s_per_day: float  # covariance (white-noise model)
    n_beats: int
    rate_uncertainty_s_per_day: float = math.nan  # covariance x correlated-noise factor


@dataclass
class Metrics:
    """Layer-3 figures. NaN = not available (see ``warnings``)."""

    n_beats: int
    n_used_even: int
    n_used_odd: int
    n_excluded: int
    excluded_fraction: float
    rate_s_per_day: float = math.nan
    rate_sigma_s_per_day: float = math.nan
    rate_sigma_block_s_per_day: float = math.nan
    rate_uncertainty_s_per_day: float = math.nan  # max(covariance, block) (+) clock sigma
    rate_clock_sigma_s_per_day: float = 0.0  # from adc_drift_sigma_ppm (layer 4)
    rate_calibrated: bool = False
    beat_error_ms: float = math.nan
    beat_error_sigma_ms: float = math.nan
    beat_error_sigma_block_ms: float = math.nan
    beat_error_uncertainty_ms: float = math.nan  # max(covariance, block) sigma
    beat_error_point: str = "—"  # "p1" (template P1 onsets) or "t_fine" (fallback)
    amplitude_deg: float = math.nan
    amplitude_tic_deg: float = math.nan
    amplitude_tac_deg: float = math.nan
    amplitude_la_uncertainty_deg: float = math.nan
    amplitude_indicative: bool = False
    lift_angle_deg: float = DEFAULT_LIFT_ANGLE_DEG
    sigma_even_us: float = math.nan
    sigma_odd_us: float = math.nan
    windows: list[WindowRate] = field(default_factory=list)
    rate_trend_s_per_day_per_min: float = math.nan
    rate_trend_sigma_s_per_day_per_min: float = math.nan
    rate_trend_flag: bool = False
    window_sigma_factor: float = 1.0  # applied to the window covariance sigmas
    span_s: float = math.nan
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)


# ====================================================================== building blocks
def select_beats(beats: Iterable[Beat]) -> tuple[NDArray[np.float64], NDArray[np.float64], int]:
    """``(k, t_fine)`` of the beats usable for metrics, and how many were excluded.

    Excluded: ``t_fine`` NaN or ``SATURATED``. ``TIMING_OUTLIER`` and ``AFTER_GAP`` are kept.
    """
    ks: list[float] = []
    ts: list[float] = []
    excluded = 0
    for b in beats:
        if math.isnan(b.t_fine) or b.flags & _EXCLUDE:
            excluded += 1
            continue
        ks.append(float(b.k))
        ts.append(b.t_fine)
    return np.asarray(ks, dtype=np.float64), np.asarray(ts, dtype=np.float64), excluded


def _mad_sigma(r: NDArray[np.float64]) -> float:
    return 1.4826 * float(np.median(np.abs(r - np.median(r))))


def fit_parity_weighted(
    k: NDArray[np.float64],
    t: NDArray[np.float64],
    *,
    outlier_sigma: float = 5.0,
    max_iterations: int = 10,
    min_per_parity: int = 10,
    weighted: bool = True,
    quadratic: bool = False,
) -> ParityFit | None:
    """Robust regression ``t = a + P k + c (-1)^k`` with per-parity trimming and weights.

    ``weighted=False`` keeps the per-parity trimming but weighs both parities equally (only
    used to show why the weighting is needed). ``quadratic=True`` adds ``q (k - k_ref)^2``
    (``period`` is then the period at ``k_ref``). Returns None when a parity has fewer than
    ``min_per_parity`` beats (before or after trimming).
    """
    if k.size == 0:
        return None
    even = (k.astype(np.int64) % 2) == 0
    if min(np.count_nonzero(even), np.count_nonzero(~even)) < min_per_parity:
        return None
    k_ref = float(np.round(np.median(k)))
    kk = k - k_ref
    t_ref = float(np.median(t))
    y = t - t_ref  # re-centring keeps the normal equations well conditioned
    cols = [np.ones(k.size), kk, np.where(even, 1.0, -1.0)]
    if quadratic:
        cols.append(kk * kk)
    X = np.stack(cols, axis=1)
    keep = np.ones(k.size, dtype=bool)
    w = np.ones(k.size)
    sig = {0: 1.0, 1: 1.0}
    coef = np.zeros(X.shape[1])
    for _ in range(max_iterations):
        if min(np.count_nonzero(keep & even), np.count_nonzero(keep & ~even)) < min_per_parity:
            return None
        sw = np.sqrt(w[keep])
        coef, *_ = np.linalg.lstsq(X[keep] * sw[:, None], y[keep] * sw, rcond=None)
        r = y - X @ coef
        new_keep = np.zeros(k.size, dtype=bool)
        for parity, m in ((0, even), (1, ~even)):
            rp = r[m & keep]
            s = max(_mad_sigma(rp), _SIGMA_FLOOR)
            center = float(np.median(rp))
            new_keep[m] = np.abs(r[m] - center) <= outlier_sigma * s
            sig[parity] = s
        if weighted:
            w = np.where(even, 1.0 / sig[0] ** 2, 1.0 / sig[1] ** 2)
        if np.array_equal(new_keep, keep):
            break
        keep = new_keep
    if min(np.count_nonzero(keep & even), np.count_nonzero(keep & ~even)) < min_per_parity:
        return None
    # final fit on the kept set with the final weights
    sw = np.sqrt(w[keep])
    coef, *_ = np.linalg.lstsq(X[keep] * sw[:, None], y[keep] * sw, rcond=None)
    r = y - X @ coef
    s_even = max(float(np.std(r[keep & even])), _SIGMA_FLOOR)
    s_odd = max(float(np.std(r[keep & ~even])), _SIGMA_FLOOR)
    # covariance with the per-parity variances actually observed on the kept set
    var = np.where(even, s_even**2, s_odd**2)[keep]
    Xk = X[keep]
    if weighted:
        cov = np.linalg.inv(Xk.T @ (Xk / var[:, None]))
    else:  # ordinary least squares with heteroscedastic noise (sandwich form)
        inv = np.linalg.inv(Xk.T @ Xk)
        cov = inv @ (Xk.T @ (Xk * var[:, None])) @ inv
    return ParityFit(
        a=float(coef[0]) + t_ref,
        period=float(coef[1]),
        c=float(coef[2]),
        period_sigma=math.sqrt(max(float(cov[1, 1]), 0.0)),
        c_sigma=math.sqrt(max(float(cov[2, 2]), 0.0)),
        k_ref=k_ref,
        sigma_even=s_even,
        sigma_odd=s_odd,
        n_even=int(np.count_nonzero(keep & even)),
        n_odd=int(np.count_nonzero(keep & ~even)),
        n_trimmed=int(np.count_nonzero(~keep)),
        q=float(coef[3]) if quadratic else math.nan,
        q_sigma=math.sqrt(max(float(cov[3, 3]), 0.0)) if quadratic else math.nan,
    )


def _trend_from_fit(fit: ParityFit, config: MetricsConfig) -> float:
    """d(rate)/dt in s/d per minute from a quadratic fit."""
    d = 1.0 + config.adc_drift_ppm * 1e-6
    p = fit.period
    drate_dp = -config.nominal_period_samples * d / p**2 * 86400.0
    beats_per_min = 60.0 * config.sample_rate / p
    return drate_dp * 2.0 * fit.q * beats_per_min


def _blocks(t: NDArray[np.float64], n_blocks: int) -> list[NDArray[np.bool_]]:
    """Masks that leave out one contiguous time block each (delete-one-block jackknife)."""
    edges = np.linspace(float(t.min()), float(t.max()), n_blocks + 1)
    edges[-1] += 1.0
    return [~((t >= edges[i]) & (t < edges[i + 1])) for i in range(n_blocks)]


def _jackknife_sigma(values: Sequence[float]) -> float:
    a = np.asarray(values, dtype=np.float64)
    n = a.size
    return math.sqrt((n - 1) / n * float(np.sum((a - a.mean()) ** 2)))


def rate_trend(
    k: NDArray[np.float64], t: NDArray[np.float64], config: MetricsConfig
) -> tuple[float, float, float]:
    """``(trend_s_per_day_per_min, sigma, rate_sigma_block_s_per_day)``.

    Trend from the quadratic parity-weighted fit; both sigmas from a delete-one-block
    jackknife over ``trend_blocks`` contiguous time blocks. NaN when the data do not allow it.
    """
    nan = (math.nan, math.nan, math.nan)
    opts = {
        "outlier_sigma": config.outlier_sigma,
        "max_iterations": config.max_iterations,
        "min_per_parity": config.min_beats_per_parity,
    }
    full = fit_parity_weighted(k, t, quadratic=True, **opts)
    if full is None or t.size == 0:
        return nan
    trend = _trend_from_fit(full, config)
    jt: list[float] = []
    jr: list[float] = []
    for m in _blocks(t, config.trend_blocks):
        fq = fit_parity_weighted(k[m], t[m], quadratic=True, **opts)
        fl = fit_parity_weighted(k[m], t[m], **opts)
        if fq is None or fl is None:
            return trend, math.nan, math.nan
        jt.append(_trend_from_fit(fq, config))
        jr.append(rate_from_period(fl.period, 0.0, config)[0])
    return trend, _jackknife_sigma(jt), _jackknife_sigma(jr)


def rate_from_period(
    period_samples: float, period_sigma_samples: float, config: MetricsConfig
) -> tuple[float, float]:
    """``(rate_s_per_day, sigma)`` from a beat period in samples (ADC drift applied)."""
    true_period = period_samples / (1.0 + config.adc_drift_ppm * 1e-6)
    nominal = config.nominal_period_samples
    rate = (nominal / true_period - 1.0) * 86400.0
    # d(rate)/d(period) = -nominal / period^2 * 86400 (in ADC samples)
    sigma = nominal * (1.0 + config.adc_drift_ppm * 1e-6) / period_samples**2 * 86400.0
    return rate, sigma * period_sigma_samples


def beat_error_from_fit(fit: ParityFit, fs: float) -> tuple[float, float]:
    """``(beat_error_ms, sigma_ms)`` = ``2 |c|`` of a parity fit."""
    return 2.0 * abs(fit.c) / fs * 1e3, 2.0 * fit.c_sigma / fs * 1e3


def format_beat_error_ms(value: float) -> str:
    """Beat error at 0.1 ms resolution; below 0.05 ms -> ``"< 0.1 ms"``."""
    if not math.isfinite(value):
        return "—"
    if value < 0.05:
        return "< 0.1 ms"
    return f"{value:.1f} ms"


def window_rates(
    k: NDArray[np.float64], t: NDArray[np.float64], config: MetricsConfig
) -> list[WindowRate]:
    """Rate on windows of ``window_s`` seconds starting every ``window_step_s`` seconds.

    Windows start at the first valid beat and must fit in the span (a single window covers
    everything when the span is shorter); a window whose parities do not reach
    ``min_beats_per_parity`` beats is skipped.
    """
    if t.size == 0:
        return []
    fs = config.sample_rate
    t0 = float(t.min())
    t_end = float(t.max())
    w = config.window_s * fs
    step = (config.window_step_s or config.window_s) * fs
    out: list[WindowRate] = []
    n_win = max(1, int(math.floor((t_end - t0 - w) / step + 1e-9)) + 1)
    for i in range(n_win):
        a = t0 + i * step
        b = a + w
        last = i == n_win - 1 and a + w >= t_end - 1e-9
        m = (t >= a) & ((t <= b) if last else (t < b))
        fit = fit_parity_weighted(
            k[m],
            t[m],
            outlier_sigma=config.outlier_sigma,
            max_iterations=config.max_iterations,
            min_per_parity=config.min_beats_per_parity,
        )
        if fit is None:
            continue
        rate, sigma = rate_from_period(fit.period, fit.period_sigma, config)
        out.append(
            WindowRate(
                start_s=a / fs,
                end_s=b / fs,
                center_s=0.5 * (a + b) / fs,
                rate_s_per_day=rate,
                rate_sigma_s_per_day=sigma,
                n_beats=int(np.count_nonzero(m)),
            )
        )
    return out


def amplitude_from_templates(
    templates: tuple[ParityTemplate | None, ParityTemplate | None], config: MetricsConfig
) -> tuple[float, float, float, float]:
    """``(A_mean, A_tic, A_tac, la_uncertainty)`` from the template lift times (NaN if n/a).

    A parity whose amplitude falls outside ``amplitude_range_deg`` is invalid; the mean uses
    the valid parities only.
    """
    la = config.effective_lift_angle_deg
    lo, hi = config.amplitude_range_deg
    amps: list[float] = []
    for t in templates:
        a = math.nan
        if t is not None:
            tl = t.t_lift_samples / config.sample_rate
            if math.isfinite(tl) and tl > 0:
                v = amplitude_from_lift_time(tl, la, config.bph)
                if lo <= v <= hi:
                    a = v
        amps.append(a)
    valid = [a for a in amps if math.isfinite(a)]
    if not valid:
        return math.nan, amps[0], amps[1], math.nan
    mean = float(np.mean(valid))
    # +-1° of LA: A is ~proportional to LA (t_lift fixed)
    unc = abs(
        float(
            np.mean(
                [
                    amplitude_from_lift_time(
                        t.t_lift_samples / config.sample_rate, la + 1.0, config.bph
                    )
                    for t, a in zip(templates, amps, strict=True)
                    if t is not None and math.isfinite(a)
                ]
            )
        )
        - mean
    )
    return mean, amps[0], amps[1], unc


def _window_sigma_factor(m: Metrics) -> float:
    """Factor for the window sigmas: correlated timing noise makes the covariance sigma of a
    window too small (quartz references: real scatter 1.4-4.8x the declared one).

    With >= 6 windows: ``sqrt(reduced chi^2)`` of the window rates around the trend line,
    measured at the window scale itself (the correlation of the noise depends on the scale:
    TMI VH31 x3.6 at 10 s, x1.6 at 30 s). With fewer windows (too few degrees of freedom):
    the block-jackknife / covariance ratio of the whole take. Never below 1 (white noise).
    Taking the maximum of both over-inflated (6-block jackknife ~30 % scatter).
    """
    f = 1.0
    w = m.windows
    if len(w) < 6:
        if math.isfinite(m.rate_sigma_block_s_per_day) and m.rate_sigma_s_per_day > 0:
            f = max(f, m.rate_sigma_block_s_per_day / m.rate_sigma_s_per_day)
        return f
    if len(w) >= 6:
        x = np.array([v.center_s for v in w])
        y = np.array([v.rate_s_per_day for v in w])
        s = np.array([max(v.rate_sigma_s_per_day, 1e-9) for v in w])
        wt = 1.0 / s**2
        X = np.stack([np.ones(x.size), x - x.mean()], axis=1)
        coef = np.linalg.solve(X.T @ (X * wt[:, None]), X.T @ (wt * y))
        chi2 = float(np.sum(wt * (y - X @ coef) ** 2)) / (len(w) - 2)
        f = max(f, math.sqrt(max(chi2, 0.0)))
    return f


def timing_confidence(m: Metrics, config: MetricsConfig) -> float:
    """Confidence of the rate and beat error: ``q_n * q_sigma * q_excl`` (module docstring)."""
    if not math.isfinite(m.rate_s_per_day):
        return 0.0
    n_min = min(m.n_used_even, m.n_used_odd)
    q_n = min(1.0, n_min / (3.0 * config.min_beats_per_parity))
    s_max = max(m.sigma_even_us, m.sigma_odd_us)
    q_sigma = 1.0 / (1.0 + s_max / 500.0) if math.isfinite(s_max) else 0.0
    q_excl = max(0.0, 1.0 - m.excluded_fraction)
    return float(min(1.0, max(0.0, q_n * q_sigma * q_excl)))


def _confidence(m: Metrics, config: MetricsConfig) -> float:
    q_time = timing_confidence(m, config)
    q_amp = 1.0
    if math.isfinite(m.amplitude_tic_deg) and math.isfinite(m.amplitude_tac_deg):
        q_amp = 1.0 / (1.0 + abs(m.amplitude_tic_deg - m.amplitude_tac_deg) / 20.0)
    return float(min(1.0, max(0.0, q_time * q_amp)))


# ====================================================================== batch
def compute_metrics(
    beats: Sequence[Beat],
    templates: tuple[ParityTemplate | None, ParityTemplate | None],
    config: MetricsConfig,
) -> Metrics:
    """Rate, beat error and amplitude of ``beats`` (batch mode)."""
    fs = config.sample_rate
    k, t, excluded = select_beats(beats)
    n = len(beats)
    m = Metrics(
        n_beats=n,
        n_used_even=0,
        n_used_odd=0,
        n_excluded=excluded,
        excluded_fraction=excluded / n if n else 0.0,
        lift_angle_deg=config.effective_lift_angle_deg,
    )
    if t.size:
        m.span_s = float(t.max() - t.min()) / fs
    if config.adc_drift_ppm == 0.0 and config.adc_drift_sigma_ppm == 0.0:
        m.warnings.append(
            "marcha sin calibrar: no se ha corregido la deriva del reloj de la tarjeta (fase 4)"
        )
    else:
        m.rate_calibrated = True
    fit = fit_parity_weighted(
        k,
        t,
        outlier_sigma=config.outlier_sigma,
        max_iterations=config.max_iterations,
        min_per_parity=config.min_beats_per_parity,
    )
    if fit is None:
        m.warnings.append(
            f"menos de {config.min_beats_per_parity} batimientos válidos por paridad: sin cifras"
        )
        return m
    m.n_used_even, m.n_used_odd = fit.n_even, fit.n_odd
    m.sigma_even_us = fit.sigma_even / fs * 1e6
    m.sigma_odd_us = fit.sigma_odd / fs * 1e6
    m.rate_s_per_day, m.rate_sigma_s_per_day = rate_from_period(
        fit.period, fit.period_sigma, config
    )
    m.windows = window_rates(k, t, config)
    (
        m.rate_trend_s_per_day_per_min,
        m.rate_trend_sigma_s_per_day_per_min,
        m.rate_sigma_block_s_per_day,
    ) = rate_trend(k, t, config)
    # The clock error is a common offset: it enters the rate uncertainty, not the windows or
    # the trend.
    m.rate_clock_sigma_s_per_day = config.adc_drift_sigma_ppm * 1e-6 * 86400.0
    m.rate_uncertainty_s_per_day = math.hypot(
        float(np.nanmax([m.rate_sigma_s_per_day, m.rate_sigma_block_s_per_day])),
        m.rate_clock_sigma_s_per_day,
    )
    m.window_sigma_factor = _window_sigma_factor(m)
    m.windows = [
        replace(w, rate_uncertainty_s_per_day=w.rate_sigma_s_per_day * m.window_sigma_factor)
        for w in m.windows
    ]
    if (
        math.isfinite(m.rate_trend_sigma_s_per_day_per_min)
        and abs(m.rate_trend_s_per_day_per_min)
        > config.trend_sigma * m.rate_trend_sigma_s_per_day_per_min
    ):
        m.rate_trend_flag = True
        m.warnings.append(
            f"la marcha cambia durante la medida: {m.rate_trend_s_per_day_per_min:+.2f} s/d por "
            f"minuto (± {m.rate_trend_sigma_s_per_day_per_min:.2f}); el reloj no está estable"
        )
    if not config.quartz:
        _beat_error(m, k, t, templates, fit, config)
        _amplitude(m, templates, config)
    m.confidence = _confidence(m, config)
    return m


def _beat_error(
    m: Metrics,
    k: NDArray[np.float64],
    t: NDArray[np.float64],
    templates: tuple[ParityTemplate | None, ParityTemplate | None],
    fit: ParityFit,
    config: MetricsConfig,
) -> None:
    offsets = [tp.onset_ref - tp.onset_p1 if tp is not None else math.nan for tp in templates]
    p1_fit = None
    if all(math.isfinite(v) for v in offsets):
        even = (k.astype(np.int64) % 2) == 0
        t_p1 = t - np.where(even, offsets[0], offsets[1])
        p1_fit = fit_parity_weighted(
            k,
            t_p1,
            outlier_sigma=config.outlier_sigma,
            max_iterations=config.max_iterations,
            min_per_parity=config.min_beats_per_parity,
        )
    if p1_fit is not None:
        m.beat_error_ms, m.beat_error_sigma_ms = beat_error_from_fit(p1_fit, config.sample_rate)
        m.beat_error_point = "p1"
        t_be = t_p1
    else:
        m.beat_error_ms, m.beat_error_sigma_ms = beat_error_from_fit(fit, config.sample_rate)
        m.beat_error_point = "t_fine"
        t_be = t
        m.warnings.append(
            "error de batimiento medido en t_fine (inicio de P3): alguna plantilla no tiene P1"
        )
    # block jackknife of the beat error (same blocks as the trend)
    vals: list[float] = []
    for mask in _blocks(t_be, config.trend_blocks):
        f = fit_parity_weighted(
            k[mask],
            t_be[mask],
            outlier_sigma=config.outlier_sigma,
            max_iterations=config.max_iterations,
            min_per_parity=config.min_beats_per_parity,
        )
        if f is None:
            vals = []
            break
        vals.append(beat_error_from_fit(f, config.sample_rate)[0])
    if vals:
        m.beat_error_sigma_block_ms = _jackknife_sigma(vals)
    m.beat_error_uncertainty_ms = float(
        np.nanmax([m.beat_error_sigma_ms, m.beat_error_sigma_block_ms])
    )


def _amplitude(
    m: Metrics,
    templates: tuple[ParityTemplate | None, ParityTemplate | None],
    config: MetricsConfig,
) -> None:
    a, a_tic, a_tac, unc = amplitude_from_templates(templates, config)
    m.amplitude_deg, m.amplitude_tic_deg, m.amplitude_tac_deg = a, a_tic, a_tac
    m.amplitude_la_uncertainty_deg = unc
    if config.lift_angle_deg is None:
        m.amplitude_indicative = True
        m.warnings.append(
            f"ángulo de levantamiento desconocido: se usan {DEFAULT_LIFT_ANGLE_DEG:g}°; "
            "la amplitud es orientativa"
        )
    if not math.isfinite(a):
        m.warnings.append(
            "amplitud no disponible (sin t_lift en las plantillas o fuera de "
            f"{config.amplitude_range_deg[0]:g}-{config.amplitude_range_deg[1]:g}°)"
        )
    elif (
        math.isfinite(a_tic)
        and math.isfinite(a_tac)
        and abs(a_tic - a_tac) > config.amplitude_mismatch_deg
    ):
        m.warnings.append(
            f"amplitud de tic y tac distinta ({a_tic:.0f}° / {a_tac:.0f}°): revisar la "
            "identificación de P1/P3"
        )


# ====================================================================== incremental
class MetricsTracker:
    """Sliding-window metrics: keeps the beats of the last ``span_s`` seconds of ``t_fine``.

    ``result()`` applies ``compute_metrics`` to the kept beats and the latest templates, so it
    is identical to the batch mode on the same beats. Not thread-safe.

    Args:
        config: ``MetricsConfig``.
        span_s: Window length in seconds (None = keep everything).
    """

    def __init__(self, config: MetricsConfig, span_s: float | None = 30.0) -> None:
        if span_s is not None and not span_s > 0:
            raise ValueError("span_s debe ser positivo o None")
        self.config = config
        self.span_s = span_s
        self._beats: deque[Beat] = deque()
        self._templates: tuple[ParityTemplate | None, ParityTemplate | None] = (None, None)
        self._last_t = -math.inf

    def add(
        self,
        beats: Iterable[Beat],
        templates: tuple[ParityTemplate | None, ParityTemplate | None] | None = None,
    ) -> None:
        """Append new beats (in order) and, optionally, the current templates."""
        for b in beats:
            self._beats.append(b)
            if math.isfinite(b.t_fine):
                self._last_t = max(self._last_t, b.t_fine)
        if templates is not None:
            self._templates = templates
        self._trim()

    def _trim(self) -> None:
        if self.span_s is None or not math.isfinite(self._last_t):
            return
        start = self._last_t - self.span_s * self.config.sample_rate
        while self._beats:
            b = self._beats[0]
            ref = b.t_fine if math.isfinite(b.t_fine) else b.reference
            if ref < start:
                self._beats.popleft()
            else:
                break

    @property
    def beats(self) -> list[Beat]:
        return list(self._beats)

    def result(self) -> Metrics:
        return compute_metrics(list(self._beats), self._templates, self.config)
