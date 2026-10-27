"""Layer 4: real frequency of the ADC clock (ppm +- sigma) and its confidence level.

Pure library: no Qt, no audio, no network. It turns the block log of ``SampleStream``
(``(end_index, host_ns)`` pairs), or the instants of an external reference (GPS PPS, a quartz
watch of known error), into an ``AdcDrift`` that feeds ``MetricsConfig.adc_drift_ppm``.

Conventions
-----------
* ``ppm`` > 0 means the ADC runs FAST: ``fs_true = fs_nominal * (1 + ppm * 1e-6)``. A fast ADC
  stretches every period measured in samples, so an uncorrected watch looks slow by
  ``0.0864 * ppm`` s/d. Same sign as ``MetricsConfig.adc_drift_ppm``.
* Host model. ``n`` samples last ``n / fs_true`` true seconds, so the host stamps follow
  ``t_host = a + (n / fs) / (1 + d) + e``, ``d = ppm * 1e-6``, where ``e >= 0`` is the callback
  delay (a block can only be stamped LATE). Working with the residual against the nominal line,
  ``r = t_host - n / fs = a + c * (n / fs) + e``, gives ``ppm = (1 / (1 + c) - 1) * 1e6``.
* Lower envelope. Since ``e >= 0``, the mean of the residuals is biased by the mean delay and
  its scatter is the full jitter. Instead, the residuals are binned by ADC time (``bin_s``,
  1 s by default) and only the EARLIEST block of each bin is kept: those points sit close to
  ``e = 0``. A Theil-Sen slope over the minima (pairs within the same segment only) gives a
  robust line; minima farther than ``outlier_sigma`` robust sigmas are dropped and a
  least-squares fit with a common slope and one intercept per segment gives ``ppm`` and its
  statistical sigma.
* Segments. A step of more than ``jump_ms`` between consecutive minima (host clock stepped by
  NTP, laptop sleep, lost samples on an input overflow, a stall longer than a bin) cuts the
  log into segments; each keeps its own intercept and segments shorter than ``min_segment``
  points are dropped.
* Confidence levels (``ClockLevel``):

  ===  ============================================  =====================================
  0    not calibrated                                 +-30 ppm declared (+-2.6 s/d)
  1    ADC vs an undisciplined host clock             fit sigma (+) host crystal (+-30 ppm)
  2    ADC vs a host clock disciplined by NTP         fit sigma (+) ~0.5-1 ppm
  3    external reference (PPS, HAQ) or manual        reference sigma (+) fit sigma
  ===  ============================================  =====================================

  The total uncertainty is ``hypot(sigma_ppm, systematic_ppm)``: the statistical sigma of the
  fit and the declared error of the time base it was measured against.
* External references. ``drift_from_ticks`` fits ``t_k = a + P k`` to the instants (absolute
  samples) of a reference with a known nominal period (1 s for a PPS, 1/16 s for the Bulova
  NM10 steps...) and gives ``ppm = P / (P_nom * fs) - 1`` (in ppm) minus the reference's own
  error. ``detect_pps_edges`` finds the rising edges of an electrical PPS on a line input with
  sub-sample interpolation; an acoustic PPS (piezo buzzer) goes through ``BeatDetector`` as a
  3600 BPH quartz and its beat instants feed ``drift_from_ticks`` directly.
* Recordings. ``drift_to_tags`` / ``drift_from_tags`` turn an ``AdcDrift`` into the
  ``ADV_TG_ADC_DRIFT_*`` tags stored in a recording (offline files carry no host stamps, so the
  calibration valid when they were captured travels with them).
"""

from __future__ import annotations

import ctypes
import math
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum

import numpy as np
from numpy.typing import ArrayLike, NDArray

__all__ = [
    "DRIFT_TAG_PREFIX",
    "PPM_TO_S_PER_DAY",
    "UNCALIBRATED_SIGMA_PPM",
    "AdcDrift",
    "ClockDriftTracker",
    "ClockLevel",
    "HostClock",
    "detect_pps_edges",
    "drift_from_tags",
    "drift_from_ticks",
    "drift_to_tags",
    "estimate_drift",
    "fit_envelope",
    "host_clock",
    "linux_ntp_synchronized",
    "lower_envelope",
]

#: 1 ppm of clock error, in seconds per day.
PPM_TO_S_PER_DAY = 0.0864
#: Declared uncertainty when nothing is calibrated (typical USB audio crystal).
UNCALIBRATED_SIGMA_PPM = 30.0

#: Prefix of the recording tags written by ``drift_to_tags``.
DRIFT_TAG_PREFIX = "ADV_TG_ADC_DRIFT_"

_MAD_TO_SIGMA = 1.4826
_RESIDUAL_FLOOR_S = 20e-6  # robust sigma floor of the envelope minima (20 µs)
_THEIL_SEN_MAX_PAIRS = 200_000


class ClockLevel(IntEnum):
    """Confidence level of an ``AdcDrift`` (see the module docstring)."""

    UNCALIBRATED = 0
    HOST_RAW = 1
    HOST_DISCIPLINED = 2
    EXTERNAL = 3


@dataclass(frozen=True, slots=True)
class HostClock:
    """Host time base the block stamps were taken with.

    Attributes:
        name: Human-readable description.
        disciplined: True if its FREQUENCY is steered by NTP (level 2), else level 1.
        sigma_ppm: Declared frequency error of this clock, in ppm.
    """

    name: str
    disciplined: bool
    sigma_ppm: float

    @property
    def level(self) -> ClockLevel:
        return ClockLevel.HOST_DISCIPLINED if self.disciplined else ClockLevel.HOST_RAW


@dataclass(frozen=True, slots=True)
class AdcDrift:
    """Estimated ADC clock error.

    Attributes:
        ppm: ADC frequency error in ppm (> 0 = ADC fast). 0 when not calibrated.
        sigma_ppm: Statistical sigma of the fit, in ppm.
        systematic_ppm: Declared error of the time base it was measured against, in ppm.
        level: Confidence level.
        method: ``"none"``, ``"host-envelope"``, ``"reference"`` or ``"manual"``.
        span_s: ADC time covered by the points used, in seconds.
        n_points: Points used in the final fit.
        n_segments: Segments with their own intercept.
        residual_us: Robust sigma of the residuals of the final fit, in µs.
        note: Why the estimate is missing or degraded (empty when fine).
    """

    ppm: float
    sigma_ppm: float
    systematic_ppm: float
    level: ClockLevel
    method: str
    span_s: float = 0.0
    n_points: int = 0
    n_segments: int = 0
    residual_us: float = math.nan
    note: str = ""

    @property
    def uncertainty_ppm(self) -> float:
        """Total uncertainty ``hypot(sigma_ppm, systematic_ppm)`` in ppm."""
        return math.hypot(self.sigma_ppm, self.systematic_ppm)

    @property
    def rate_bias_s_per_day(self) -> float:
        """Apparent rate an uncorrected watch shows because of this error (s/d)."""
        return -PPM_TO_S_PER_DAY * self.ppm

    @classmethod
    def uncalibrated(cls, note: str = "") -> AdcDrift:
        """No estimate: ppm 0 with the declared +-30 ppm."""
        return cls(0.0, 0.0, UNCALIBRATED_SIGMA_PPM, ClockLevel.UNCALIBRATED, "none", note=note)

    @classmethod
    def manual(cls, ppm: float, sigma_ppm: float, note: str = "") -> AdcDrift:
        """Value entered by the user (level 3)."""
        if not math.isfinite(ppm) or not (math.isfinite(sigma_ppm) and sigma_ppm >= 0):
            raise ValueError("ppm y sigma_ppm deben ser finitos (sigma >= 0)")
        return cls(float(ppm), 0.0, float(sigma_ppm), ClockLevel.EXTERNAL, "manual", note=note)


# --------------------------------------------------------------------------- recording tags
def drift_to_tags(drift: AdcDrift, *, calibrated_at: str | None = None) -> dict[str, str]:
    """Tags that store ``drift`` in a recording (``ADV_TG_ADC_DRIFT_*``).

    Args:
        drift: The calibration valid when the recording was made (level 0 is stored too, so
            a reader knows the file was explicitly NOT calibrated).
        calibrated_at: When the calibration was measured (ISO 8601), if known.

    Returns:
        ``{key: value}`` with ``PPM``, ``SIGMA_PPM``, ``SYSTEMATIC_PPM``, ``LEVEL``, ``METHOD``
        and, when available, ``SPAN_S``, ``CALIBRATED_AT`` and ``NOTE``.
    """
    p = DRIFT_TAG_PREFIX
    tags = {
        f"{p}PPM": format(drift.ppm, ".9g"),
        f"{p}SIGMA_PPM": format(drift.sigma_ppm, ".6g"),
        f"{p}SYSTEMATIC_PPM": format(drift.systematic_ppm, ".6g"),
        f"{p}LEVEL": str(int(drift.level)),
        f"{p}METHOD": drift.method,
    }
    if drift.span_s > 0:
        tags[f"{p}SPAN_S"] = format(drift.span_s, ".6g")
    if calibrated_at:
        tags[f"{p}CALIBRATED_AT"] = calibrated_at
    if drift.note:
        tags[f"{p}NOTE"] = " ".join(drift.note.split())  # one line
    return tags


def drift_from_tags(tags: Mapping[str, str]) -> AdcDrift | None:
    """Inverse of ``drift_to_tags`` (keys case-insensitive).

    Returns:
        The stored ``AdcDrift``, or ``None`` if the tags carry no ``ADV_TG_ADC_DRIFT_PPM``.

    Raises:
        ValueError: If the drift tags are present but malformed.
    """
    up = {str(k).upper(): v for k, v in tags.items()}
    p = DRIFT_TAG_PREFIX
    if f"{p}PPM" not in up:
        return None
    try:
        ppm = float(up[f"{p}PPM"])
        sigma = float(up.get(f"{p}SIGMA_PPM", "0"))
        systematic = float(up.get(f"{p}SYSTEMATIC_PPM", "0"))
        level = ClockLevel(int(up.get(f"{p}LEVEL", "0")))
        span = float(up.get(f"{p}SPAN_S", "0"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"etiquetas de calibración del reloj no válidas: {exc}") from exc
    if (
        not all(math.isfinite(v) for v in (ppm, sigma, systematic, span))
        or min(sigma, systematic) < 0
    ):
        raise ValueError("etiquetas de calibración del reloj no válidas (valores no finitos)")
    return AdcDrift(
        ppm,
        sigma,
        systematic,
        level,
        up.get(f"{p}METHOD", "none"),
        span_s=span,
        note=up.get(f"{p}NOTE", ""),
    )


# --------------------------------------------------------------------------- host clocks
def linux_ntp_synchronized() -> bool | None:
    """Whether the Linux kernel reports the clock as NTP-synchronized (``adjtimex``).

    Returns:
        True / False on Linux, None elsewhere or if the call is not available.
    """
    if not sys.platform.startswith("linux"):
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.adjtimex.argtypes = [ctypes.c_void_p]
        libc.adjtimex.restype = ctypes.c_int
        buf = ctypes.create_string_buffer(512)  # struct timex, modes = 0 (read only)
        state = libc.adjtimex(buf)
    except (OSError, AttributeError):
        return None
    if state < 0:
        return None
    return state != 5  # TIME_ERROR: clock not synchronized


def host_clock(source: str = "perf_counter") -> HostClock:
    """Describe the host clock used for the block stamps on this platform.

    Args:
        source: ``"perf_counter"`` (``time.perf_counter_ns``, ``ClockLog.host_ns``),
            ``"pa_adc"`` (driver capture time, ``ClockLog.adc_ns``: PortAudio's stream clock,
            which is ``mach_absolute_time`` on macOS, ``CLOCK_MONOTONIC`` on Linux and QPC on
            Windows, so it is disciplined exactly when ``perf_counter`` is) or ``"time"``
            (``time.time_ns``, ``ClockLog.wall_ns``: disciplined but it can step).

    Returns:
        A conservative ``HostClock``: only Linux ``CLOCK_MONOTONIC`` with NTP synchronized, or
        the wall clock, count as disciplined.
    """
    if source in ("perf_counter", "pa_adc"):
        label = "perf_counter" if source == "perf_counter" else "inputBufferAdcTime"
        if sys.platform.startswith("linux") and linux_ntp_synchronized():
            return HostClock(f"{label} (CLOCK_MONOTONIC, NTP)", True, 0.5)
        return HostClock(f"{label} ({sys.platform}, sin disciplinar)", False, 30.0)
    if source == "time":
        sigma = 2.0 if sys.platform.startswith("win") else 1.0  # W32Time is coarse by default
        return HostClock(f"time.time_ns ({sys.platform}, NTP del sistema)", True, sigma)
    raise ValueError(f"fuente de reloj desconocida: {source!r}")


# --------------------------------------------------------------------------- envelope fit
def _as_int64(a: ArrayLike, name: str) -> NDArray[np.int64]:
    x = np.asarray(a)
    if x.ndim != 1:
        raise ValueError(f"{name} debe ser 1-D")
    return x.astype(np.int64, copy=False)


def lower_envelope(
    end_indices: ArrayLike, host_ns: ArrayLike, sample_rate: float, *, bin_s: float = 1.0
) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """Earliest block of each ``bin_s`` bin of ADC time.

    Args:
        end_indices: Absolute end index of each block (increasing).
        host_ns: Host stamp of each block in ns.
        sample_rate: Nominal sample rate in Hz.
        bin_s: Bin length in ADC seconds.

    Returns:
        ``(end_indices, host_ns)`` of the kept blocks, in order. The first and last bins are
        dropped when the log covers more than three bins (they are usually partial).
    """
    n = _as_int64(end_indices, "end_indices")
    t = _as_int64(host_ns, "host_ns")
    if n.shape != t.shape:
        raise ValueError("end_indices y host_ns deben tener la misma longitud")
    if not (sample_rate > 0 and bin_s > 0):
        raise ValueError("sample_rate y bin_s deben ser positivos")
    if n.size == 0:
        return n, t
    r = (t - t[0]).astype(np.float64) * 1e-9 - (n - n[0]).astype(np.float64) / sample_rate
    bins = (n // max(1, int(round(bin_s * sample_rate)))).astype(np.int64)
    # Stable sort by (bin, residual): the first element of each bin is its minimum.
    order = np.lexsort((r, bins))
    first = np.ones(order.size, dtype=bool)
    first[1:] = bins[order[1:]] != bins[order[:-1]]
    keep = np.sort(order[first])
    if keep.size > 3:
        keep = keep[1:-1]
    return n[keep], t[keep]


def _segments(r: NDArray[np.float64], jump_s: float) -> NDArray[np.int64]:
    """Segment id of each point: a new segment starts after a step larger than ``jump_s``."""
    steps = np.abs(np.diff(r)) > jump_s
    return np.concatenate(([0], np.cumsum(steps))).astype(np.int64)


def _theil_sen(x: NDArray[np.float64], y: NDArray[np.float64], seg: NDArray[np.int64]) -> float:
    """Median of pairwise slopes, only between points of the same segment."""
    m = x.size
    if m * (m - 1) // 2 <= _THEIL_SEN_MAX_PAIRS:
        i, j = np.triu_indices(m, k=1)
    else:
        rng = np.random.default_rng(0)
        i = rng.integers(0, m, _THEIL_SEN_MAX_PAIRS)
        j = rng.integers(0, m, _THEIL_SEN_MAX_PAIRS)
    ok = (seg[i] == seg[j]) & (x[j] != x[i])
    if not np.any(ok):
        return 0.0
    return float(np.median((y[j[ok]] - y[i[ok]]) / (x[j[ok]] - x[i[ok]])))


def _fit_common_slope(
    x: NDArray[np.float64], y: NDArray[np.float64], seg: NDArray[np.int64]
) -> tuple[float, float, NDArray[np.float64]]:
    """LS with a common slope and one intercept per segment: ``(slope, sigma, residuals)``."""
    uniq, inv = np.unique(seg, return_inverse=True)
    xc = x.copy()
    yc = y.copy()
    for s in range(uniq.size):
        m = inv == s
        xc[m] -= x[m].mean()
        yc[m] -= y[m].mean()
    sxx = float(np.dot(xc, xc))
    if sxx <= 0.0:
        return 0.0, math.inf, yc
    slope = float(np.dot(xc, yc)) / sxx
    res = yc - slope * xc
    dof = x.size - uniq.size - 1
    s2 = float(np.dot(res, res)) / dof if dof > 0 else math.inf
    return slope, math.sqrt(s2 / sxx), res


def _slope_to_ppm(c: float) -> float:
    return (1.0 / (1.0 + c) - 1.0) * 1e6


def fit_envelope(
    end_indices: ArrayLike,
    host_ns: ArrayLike,
    sample_rate: float,
    *,
    host: HostClock | None = None,
    jump_ms: float = 5.0,
    outlier_sigma: float = 5.0,
    min_points: int = 10,
    min_segment: int = 3,
    min_span_s: float = 20.0,
) -> AdcDrift:
    """Fit the ADC drift to envelope points (see ``lower_envelope``).

    Args:
        end_indices: Absolute end index of each point.
        host_ns: Host stamp of each point in ns.
        sample_rate: Nominal sample rate in Hz.
        host: Host time base (default ``host_clock()``).
        jump_ms: Step between consecutive points that starts a new segment.
        outlier_sigma: Points beyond this many robust sigmas of the Theil-Sen line are dropped.
        min_points: Minimum points in the final fit.
        min_segment: Segments with fewer points are dropped.
        min_span_s: Minimum ADC time covered by the kept points.

    Returns:
        The estimate, or ``AdcDrift.uncalibrated`` (with a note) if there is not enough data.
    """
    host = host or host_clock()
    n = _as_int64(end_indices, "end_indices")
    t = _as_int64(host_ns, "host_ns")
    if n.size < min_points:
        return AdcDrift.uncalibrated(f"pocos puntos ({n.size} < {min_points})")
    x = (n - n[0]).astype(np.float64) / sample_rate
    y = (t - t[0]).astype(np.float64) * 1e-9 - x

    seg = _segments(y, jump_ms * 1e-3)
    counts = np.bincount(seg)
    ok = counts[seg] >= min_segment
    x, y, seg = x[ok], y[ok], seg[ok]
    if x.size < min_points:
        return AdcDrift.uncalibrated("demasiados saltos de reloj")

    c0 = _theil_sen(x, y, seg)
    # Intercept per segment from the median of the detrended points.
    e = y - c0 * x
    for s in np.unique(seg):
        m = seg == s
        e[m] -= np.median(e[m])
    sig = max(_MAD_TO_SIGMA * float(np.median(np.abs(e))), _RESIDUAL_FLOOR_S)
    keep = np.abs(e) <= outlier_sigma * sig
    x, y, seg = x[keep], y[keep], seg[keep]
    counts = np.bincount(seg)
    ok = counts[seg] >= 2
    x, y, seg = x[ok], y[ok], seg[ok]
    span = float(sum(np.ptp(x[seg == s]) for s in np.unique(seg))) if x.size else 0.0
    if x.size < min_points or span < min_span_s:
        return AdcDrift.uncalibrated(f"base de tiempo corta ({span:.1f} s < {min_span_s:g} s)")

    c, c_sigma, res = _fit_common_slope(x, y, seg)
    ppm = _slope_to_ppm(c)
    sigma_ppm = c_sigma / (1.0 + c) ** 2 * 1e6
    resid = _MAD_TO_SIGMA * float(np.median(np.abs(res - np.median(res))))
    n_seg = int(np.unique(seg).size)
    note = f"{n_seg} tramos (saltos de reloj)" if n_seg > 1 else ""
    return AdcDrift(
        ppm=ppm,
        sigma_ppm=sigma_ppm,
        systematic_ppm=host.sigma_ppm,
        level=host.level,
        method="host-envelope",
        span_s=span,
        n_points=int(x.size),
        n_segments=n_seg,
        residual_us=resid * 1e6,
        note=note,
    )


def estimate_drift(
    end_indices: ArrayLike,
    host_ns: ArrayLike,
    sample_rate: float,
    *,
    bin_s: float = 1.0,
    **kwargs: object,
) -> AdcDrift:
    """``lower_envelope`` + ``fit_envelope`` over a whole block log (see both).

    Records with a stamp <= 0 (``ClockLog`` uses 0 for "not available") are ignored.
    """
    n = _as_int64(end_indices, "end_indices")
    t = _as_int64(host_ns, "host_ns")
    ok = t > 0
    n, t = lower_envelope(n[ok], t[ok], sample_rate, bin_s=bin_s)
    return fit_envelope(n, t, sample_rate, **kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- live tracker
class ClockDriftTracker:
    """Incremental lower envelope over an unbounded session.

    ``SampleStream.block_log()`` only keeps the newest blocks (8192 by default, ~3 min with
    20 ms blocks), so this keeps one point per ``bin_s`` of ADC time instead (O(session
    seconds) memory) and fits on demand. Not thread-safe: call it from one consumer thread.

    Args:
        sample_rate: Nominal sample rate in Hz.
        host: Host time base the stamps come from (default ``host_clock()``).
        bin_s: Envelope bin length in ADC seconds.
        **fit_kwargs: Passed to ``fit_envelope``.
    """

    def __init__(
        self,
        sample_rate: float,
        *,
        host: HostClock | None = None,
        bin_s: float = 1.0,
        **fit_kwargs: object,
    ) -> None:
        if not (sample_rate > 0 and bin_s > 0):
            raise ValueError("sample_rate y bin_s deben ser positivos")
        self._fs = float(sample_rate)
        self._host = host or host_clock()
        self._bin = max(1, int(round(bin_s * sample_rate)))
        self._fit_kwargs = fit_kwargs
        self.reset()

    def reset(self) -> None:
        """Forget everything (e.g. after the stream was reset)."""
        self._n: list[int] = []
        self._t: list[int] = []
        self._last_end = -1
        self._cur_bin = -1
        self._cur_n = 0
        self._cur_t = 0
        self._cur_v = math.inf
        self._bins_seen = 0

    @property
    def host(self) -> HostClock:
        return self._host

    @property
    def points(self) -> int:
        """Completed envelope points."""
        return len(self._n)

    def update(self, end_indices: ArrayLike, host_ns: ArrayLike) -> int:
        """Consume a block log snapshot; blocks already seen are skipped.

        Args:
            end_indices: Absolute end indices (``SampleStream.block_log()[0]``).
            host_ns: Host stamps in ns (``SampleStream.block_log()[1]``).

        Returns:
            Number of new blocks consumed (records with a stamp <= 0 are skipped). If the
            indices go backwards the tracker resets.
        """
        n = _as_int64(end_indices, "end_indices")
        t = _as_int64(host_ns, "host_ns")
        if n.size == 0:
            return 0
        if n[-1] < self._last_end:
            self.reset()
        new = (n > self._last_end) & (t > 0)  # 0 = stamp not available
        n, t = n[new], t[new]
        if n.size == 0:
            return 0
        k = 1e9 / self._fs
        for ni, ti in zip(n.tolist(), t.tolist(), strict=True):
            b = ni // self._bin
            v = ti - ni * k
            if b != self._cur_bin:
                if self._cur_bin >= 0 and self._bins_seen > 1:  # the first bin is partial
                    self._n.append(self._cur_n)
                    self._t.append(self._cur_t)
                self._cur_bin = b
                self._cur_v = math.inf
                self._bins_seen += 1
            if v < self._cur_v:
                self._cur_v, self._cur_n, self._cur_t = v, ni, ti
        self._last_end = int(n[-1])
        return int(n.size)

    def envelope(self, last_s: float | None = None) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
        """Completed envelope points (optionally only the last ``last_s`` ADC seconds)."""
        n = np.asarray(self._n, dtype=np.int64)
        t = np.asarray(self._t, dtype=np.int64)
        if last_s is not None and n.size:
            m = n >= n[-1] - int(last_s * self._fs)
            n, t = n[m], t[m]
        return n, t

    def estimate(self, last_s: float | None = None) -> AdcDrift:
        """Fit the envelope (optionally only the last ``last_s`` ADC seconds)."""
        n, t = self.envelope(last_s)
        return fit_envelope(n, t, self._fs, host=self._host, **self._fit_kwargs)  # type: ignore[arg-type]

    def stability(self, window_s: float = 120.0, n_sigma: float = 3.0) -> tuple[float, float, bool]:
        """Compare the last ``window_s`` seconds with the earlier part of the session.

        Returns:
            ``(delta_ppm, sigma_ppm, changed)``: recent minus earlier ppm, the combined
            statistical sigma, and whether ``|delta| > n_sigma * sigma``. NaN / False while
            either part has no estimate.
        """
        n, t = self.envelope()
        if n.size == 0:
            return math.nan, math.nan, False
        cut = n[-1] - int(window_s * self._fs)
        kw = self._fit_kwargs
        early = fit_envelope(n[n < cut], t[n < cut], self._fs, host=self._host, **kw)  # type: ignore[arg-type]
        late = fit_envelope(n[n >= cut], t[n >= cut], self._fs, host=self._host, **kw)  # type: ignore[arg-type]
        if early.level == ClockLevel.UNCALIBRATED or late.level == ClockLevel.UNCALIBRATED:
            return math.nan, math.nan, False
        delta = late.ppm - early.ppm
        sigma = math.hypot(early.sigma_ppm, late.sigma_ppm)
        return delta, sigma, abs(delta) > n_sigma * sigma


# --------------------------------------------------------------------------- references
def drift_from_ticks(
    tick_indices: ArrayLike,
    sample_rate: float,
    *,
    nominal_period_s: float = 1.0,
    reference_ppm: float = 0.0,
    reference_sigma_ppm: float = 0.0,
    outlier_sigma: float = 5.0,
    min_ticks: int = 10,
) -> AdcDrift:
    """ADC drift from the instants of a reference with a known period (level 3).

    Missing ticks are allowed: each tick is numbered by rounding its distance to the first one
    in nominal periods (valid while ``|ppm| * span`` stays far below half a period).

    Args:
        tick_indices: Instants of the reference in absolute samples (float, sub-sample).
        sample_rate: Nominal sample rate in Hz.
        nominal_period_s: Nominal period of the reference (1 s for a PPS).
        reference_ppm: The reference's own frequency error (> 0 = reference fast).
        reference_sigma_ppm: Uncertainty of ``reference_ppm``.
        outlier_sigma: Ticks beyond this many robust sigmas are dropped.
        min_ticks: Minimum ticks kept.

    Returns:
        The estimate, or ``AdcDrift.uncalibrated`` (with a note) if there is not enough data.
    """
    x = np.asarray(tick_indices, dtype=np.float64).reshape(-1)
    x = np.sort(x[np.isfinite(x)])
    if not (sample_rate > 0 and nominal_period_s > 0):
        raise ValueError("sample_rate y nominal_period_s deben ser positivos")
    if x.size < min_ticks:
        return AdcDrift.uncalibrated(f"pocos pulsos ({x.size} < {min_ticks})")
    p_nom = nominal_period_s * sample_rate
    k = np.rint((x - x[0]) / p_nom)
    # Duplicated numbers (spurious ticks): keep the first of each.
    _, first = np.unique(k, return_index=True)
    k, x = k[first], x[first]
    seg = np.zeros(k.size, dtype=np.int64)
    y = x - x[0] - k * p_nom
    keep = np.ones(k.size, dtype=bool)
    for _ in range(5):
        p, _, _ = _fit_common_slope(k[keep], y[keep], seg[keep])
        res = y - p * k
        res -= np.median(res[keep])
        sig = max(_MAD_TO_SIGMA * float(np.median(np.abs(res[keep]))), 1e-3)  # samples
        new_keep = np.abs(res) <= outlier_sigma * sig
        if np.array_equal(new_keep, keep):
            break
        keep = new_keep
    if int(keep.sum()) < min_ticks:
        return AdcDrift.uncalibrated("demasiados pulsos atípicos")
    dp, dp_sigma, res = _fit_common_slope(k[keep], y[keep], seg[keep])
    ratio = (p_nom + dp) / p_nom  # = (1 + d_adc) / (1 + d_ref)
    ppm = (ratio * (1.0 + reference_ppm * 1e-6) - 1.0) * 1e6
    sigma_ppm = dp_sigma / p_nom * 1e6
    resid = _MAD_TO_SIGMA * float(np.median(np.abs(res))) / sample_rate
    span = float(np.ptp(k[keep])) * nominal_period_s
    return AdcDrift(
        ppm=ppm,
        sigma_ppm=sigma_ppm,
        systematic_ppm=float(reference_sigma_ppm),
        level=ClockLevel.EXTERNAL,
        method="reference",
        span_s=span,
        n_points=int(keep.sum()),
        n_segments=1,
        residual_us=resid * 1e6,
    )


def detect_pps_edges(
    samples: ArrayLike,
    sample_rate: float,
    *,
    start_index: int = 0,
    threshold: float | None = None,
    polarity: int = 1,
    min_interval_s: float = 0.5,
) -> NDArray[np.float64]:
    """Rising (or falling) edges of an electrical PPS, with linear sub-sample interpolation.

    Args:
        samples: Line-input samples carrying the PPS.
        sample_rate: Nominal sample rate in Hz.
        start_index: Absolute index of ``samples[0]``.
        threshold: Crossing level; default halfway between the 1st and 99th percentiles.
        polarity: +1 for rising edges, -1 for falling edges (AC-coupled inputs may invert).
        min_interval_s: Crossings closer than this to the previous edge are ignored (ringing).

    Returns:
        Edge instants in absolute samples.
    """
    x = np.asarray(samples, dtype=np.float64).reshape(-1)
    if polarity not in (1, -1):
        raise ValueError("polarity debe ser +1 o -1")
    if x.size < 2:
        return np.empty(0)
    x = x * polarity
    if threshold is None:
        lo, hi = np.percentile(x, [1.0, 99.0])
        if hi - lo <= 0:
            return np.empty(0)
        thr = 0.5 * (lo + hi)
    else:
        thr = float(threshold) * polarity
    i = np.flatnonzero((x[:-1] < thr) & (x[1:] >= thr))
    if i.size == 0:
        return np.empty(0)
    frac = (thr - x[i]) / (x[i + 1] - x[i])
    edges = i + frac
    min_gap = min_interval_s * sample_rate
    kept: list[float] = []
    for e in edges.tolist():
        if not kept or e - kept[-1] >= min_gap:
            kept.append(e)
    return np.asarray(kept, dtype=np.float64) + start_index
