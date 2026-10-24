"""Layer 2: beat detector (P1 / P2 / P3 onsets in absolute sample indices).

Pure library: no Qt, no threads, no processes, no per-beat logging. **Not thread-safe**:
use one ``BeatDetector`` per thread.

Pipeline
--------
1. **Fixed grid.** Audio is processed in cells of ``grid_step_s`` (0.25 s) anchored to the
   absolute sample index (cell ``m`` = ``[m S, (m+1) S)``). Every computation for a cell
   uses a window whose bounds depend only on absolute indices, so the output is identical
   no matter how the audio is chunked (64 samples, 1 s or the whole file). Adaptive state
   (noise floor, phase model) is updated on that grid, never per call.
2. **Band-pass** (Butterworth, ``sos``, zero phase with ``sosfiltfilt``) of the cell plus a
   margin on both sides; only the centre is kept. The margin is
   ``3 x`` the length after which the band-pass impulse response falls below 1e-6 of its
   peak (``sosfiltfilt`` runs the filter twice, so the influence of a sample reaches one
   impulse-response length on each side; 3x makes the truncation error < 1e-6 relative),
   plus the Hilbert half-length and the smoothing half-length. With the default band the
   filter memory is ~5.6 ms (269 samples at 48 kHz), so the margin is ~18 ms (883 samples).
   The margin only controls accuracy versus filtering the whole file at once; invariance to
   chunking is exact by construction because every window is fixed.
3. **Envelope** = magnitude of the analytic signal, computed with a finite (Blackman-windowed)
   Hilbert FIR (``2 * ceil(1.5 fs / f_low) + 1`` taps, |H| = 1.000 from 1 to 15 kHz) instead
   of an FFT, so it is strictly local (no circular edge effects), then a
   centred 0.15 ms moving average. Chosen over Teager-Kaiser because it is amplitude-linear
   (thresholds relative to the noise floor stay meaningful) and insensitive to the carrier
   frequency of each sub-event.
4. **Noise floor** per cell: median and MAD of the envelope over the previous
   ``noise_window_s``, excluding beats already detected and digital silence. It is computed
   from data that ends one cell before the cell it applies to, so it never depends on the
   beats of that same cell.
5. **Sub-events**: runs of the envelope above ``floor + threshold_lo * spread`` that reach
   ``floor + threshold_hi * spread`` (hysteresis), merged across gaps < ``ripple_merge_ms``
   and split at valleys followed by a rise of ``split_ratio`` (at least
   ``min_subevent_spacing_ms`` apart). The onset is the linearly interpolated crossing of
   ``baseline + onset_fraction * (peak - baseline)`` before the peak (baseline = 0 for an
   isolated sub-event, so the crossing does not move with the noise floor; the preceding
   valley for one riding on another's tail; never below ``floor + threshold_lo * spread``).
   Sub-events other than the anchor need ``min_subevent_snr_db``: weaker ones would give
   unreliable onsets and are reported as NaN. A threshold relative to each sub-event's own
   peak makes the onset bias a property of the burst shape, independent of its amplitude
   and of the gain.
6. **Phase acquisition**: strong sub-events of the last ``acquisition_s`` are folded over
   ``2P`` (tic + tac) for periods within ``+-period_search`` of the nominal one; the best
   cluster must hold ``acquisition_min_fraction`` of the expected beats. Tracking then starts
   at the beginning of that window, so the beats used to lock are reported too.
7. **Tracking**: a robust (Huber) regression ``t = a + b k + c (-1)^k`` over the last
   ``model_beats`` reference instants predicts the next beat. Around the prediction
   (``+- phase_tolerance * P``) the strongest sub-event is the anchor; P1 is searched
   backwards and P3 forwards within the physical lift-time window derived from
   ``amplitude_range_deg`` (narrowed to the running median t_lift once it is known). P1/P3
   are never filled in from the model: without evidence they are NaN with a flag.
   Safeguards against stray clicks: once the model is mature the anchor is taken near the
   prediction (outlier gate); a P1 (or a P3 found after the anchor) is accepted only when
   its lift time agrees with at least 2 of the last 8 beats; a lone early sub-event is
   classified as P1 or P2 by the amplitude each hypothesis implies (``typical_amplitude_deg``);
   three consecutive outliers that agree re-seed the model (the timing really moved).
8. **Fine timing (per-parity templates)**: for each beat, the envelope around the
   predicted instant is cross-correlated with the template of its parity (``k`` even / odd;
   median of the last ``template_beats`` aligned beats), only over the drop burst. The P3
   onset is measured once on the template (backward crossing of ``template_p3_fraction``
   of the burst maximum) and carried to the beat: ``Beat.t_fine``. The onset is the live one
   of the current template: the template frame drifts slowly on real takes and the live
   onset follows the physical point (freezing it was tried and broke the real beat errors).
   The price is that on an ambiguous drop (slow ramp) ``t_fine`` jumps a little when the
   onset moves, which adds rate noise (synthetic ramp at 21600 BPH: rms 0.14 s/d). The P1 onset is measured
   on the template too (first rise above the template noise). Averaging removes the
   beat-to-beat changes of which lobe of the drop is loudest, so tic and tac are timed at
   the same physical point (no spurious beat error) with much less jitter than the per-beat
   onsets. Templates survive relocks (same watch); they only depend on the beats in order,
   so chunk invariance holds.
9. **Losses**: a beat without anchor leaves a gap in ``k`` (``round(dt / P)``); after
   ``max_missed`` consecutive losses the lock is dropped and re-acquired, ``k`` continuing
   from the last beat. Stream overruns (``advance``) and runs of digital silence are data
   gaps: beats predicted inside them are not counted as losses toward re-acquisition and the
   next beat carries ``AFTER_GAP``.

Onset bias
----------
The threshold-crossing onset precedes the true burst start by a constant that depends on the
burst shape and the filters, not on SNR or amplitude (the zero-phase filters spread the
burst start symmetrically, so the 40 % crossing happens slightly before it). Measured on the
synthetic generator (``tests/synth.py``): ~2.1 samples at 48 kHz for P3 and P1; see
``ONSET_BIAS_NOTE``. P1/P3 share it, so t_lift is unbiased to within ~0.5 sample.

Performance: ~90-140x real time on one core at 48 kHz (vectorised per cell; one Python
loop per sub-event run and per beat, none per sample).

Confidence
----------
``confidence = q_snr * q_struct * q_res * q_flags`` with

* ``q_snr = 0.2 + 0.8 * clip((snr_db - min_anchor_snr_db) / 18, 0, 1)``,
* ``q_struct = 0.4 + 0.3 [P3] + 0.2 [P1] + 0.1 [P2]``,
* ``q_res = exp(-0.5 (residual / (phase_tolerance * P / 3))^2)``,
* ``q_flags = 0.5`` if TIMING_OUTLIER, ``0.8`` if SATURATED (both multiply).

It is a heuristic ranking, not a calibrated probability.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy import signal

from adv_tg.core.beat_types import (
    Beat,
    BeatFlags,
    ParityTemplate,
    amplitude_from_lift_time,
    beat_period_s,
    lift_time_window_s,
)
from adv_tg.core.sample_stream import SamplesOverwritten, SampleStream
from adv_tg.utils.debugger import Debugger

__all__ = [
    "BeatDetector",
    "BeatDetectorConfig",
    "DetectorStats",
    "ONSET_BIAS_NOTE",
]

_dbg = Debugger.get(__name__)

ONSET_BIAS_NOTE = (
    "Sesgo medido en sintético limpio (banda 1-15 kHz, onset_fraction 0.4): el inicio "
    "detectado adelanta al verdadero ~2.1 muestras en P3 y en P1 a 44.1/48 kHz (~44 µs) y "
    "~4.5 muestras a 96 kHz (el mismo tiempo). Con colas de resonancia y modos variables "
    "(realismo) es ~2.6 (P3) y ~3.0 (P1) a 48 kHz. Es constante: no depende del SNR "
    "(30-20 dB) ni de la amplitud (160-320°) en más de 0.5 muestras "
    "(tests/test_beat_detector_accuracy.py). Se resta al calcular métricas (fase 3)."
)

_P2_FRACTION = 0.45
_TINY = 1e-12


# ====================================================================== configuration
@dataclass
class BeatDetectorConfig:
    """Detector parameters. All have documented defaults except ``sample_rate``/``bph``.

    Attributes:
        sample_rate: Sample rate in Hz.
        bph: Nominal beats per hour of the calibre.
        lift_angle_deg: Lift angle (only bounds the P1-P3 search window here).
        amplitude_range_deg: Amplitudes searched (bounds of t_lift), default 90-400°.
        band_hz: Band-pass in Hz; default ``(1000, min(15000, 0.45 fs))``.
        filter_order: Butterworth order of each band edge (default 4).
        envelope_smooth_ms: Centred moving average of the envelope (default 0.15 ms).
        grid_step_s: Grid step for cells and adaptive updates (default 0.25 s).
        noise_window_s: History used for the noise floor (default 1 s).
        threshold_hi: Sub-event detection threshold in robust spreads above the floor (6).
        threshold_lo: Hysteresis (end of a sub-event) in spreads above the floor (3).
        ripple_merge_ms: Gaps shorter than this merge two runs (default 0.3 ms).
        min_subevent_spacing_ms: Minimum peak spacing to split a run in two (1 ms).
        split_ratio: Peak / preceding valley needed to split a run (default 2 = 6 dB).
        onset_fraction: Onset level between baseline and peak (default 0.4).
        subevent_rel_threshold: A group member must reach this fraction of the anchor's
            height above the floor (default 0.05 = -26 dB).
        min_anchor_snr_db: Minimum anchor envelope peak over the floor (default 12 dB).
        min_subevent_snr_db: Minimum SNR of P1/P2/P3 other than the anchor (default
            12 dB): weaker sub-events give unreliable onsets and are reported as NaN.
        weak_snr_db: Below this anchor SNR the beat is flagged WEAK (default 20 dB).
        phase_tolerance: Anchor search half-window as a fraction of P (default 0.1).
        period_search: Acquisition searches periods within +- this fraction (0.06).
        acquisition_s: Window folded to acquire the phase (default 2 s).
        acquisition_min_fraction: Fraction of expected beats the cluster must hold (0.6).
        model_beats: Beats in the phase-model regression (default 16).
        outlier_sigma: TIMING_OUTLIER beyond this many robust sigmas of the model (5).
        outlier_min_ms: Lower bound of the outlier gate (default 0.3 ms).
        max_missed: Consecutive missing beats before the lock is dropped (default 8).
        lift_margin: Relative margin added to the physical t_lift window (default 0.05).
        lift_track_tolerance: Window around the running median t_lift (default +-15 %).
        typical_amplitude_deg: Amplitude band of healthy watches, used only to decide
            whether a lone early sub-event is P1 or P2 (default 200-330°).
        saturation_level: |x| at or above this is saturation (default 0.99).
        dropout_min_ms: Runs of exact zeros this long are data gaps (default 10 ms).
        period_warn: Warn when the observed period deviates more than this (default 1 %).
        post_group_ms: Span after the last sub-event that still belongs to the beat (6 ms).
        template_beats: Beats per parity in the template median (default 24).
        template_min_beats: Aligned beats a template needs before ``t_fine`` is given (6).
        template_search_ms: Alignment search half-range around the expected position (1.5).
        template_post_ms: Template span after the anchor (default 6 ms); before it, the span
            covers the longest physical lift time plus 2 ms, so P1 is inside.
        template_p3_fraction: P3 onset on the template: backward crossing of this fraction of
            the drop burst (from its preceding minimum to its maximum) (default 0.2), starting
            from the first lobe of the burst (see ``template_lobe_fraction``).
        template_lobe_fraction: The first lobe of the drop is the earliest point that reaches
            this fraction of the burst maximum within ``template_burst_ms`` before the maximum
            (default 0.4), so a later, louder lobe does not move the onset.
        template_burst_ms: Span before the burst maximum where its first lobe is searched
            (default 2.5 ms; shorter than the P2-P3 spacing of real calibres).
        template_p1_fraction: P1 onset on the template: first rise above the template noise
            by this fraction of the burst maximum, or 6 robust sigmas if larger (0.04).
        template_min_corr: Normalised correlation a beat needs to be aligned and added to the
            template (default 0.5); below it ``t_fine`` is NaN.
        template_front_fraction: Second definition of the P3 onset on a template, the
            "steep front": the earliest stretch whose slope reaches this fraction of the
            steepest slope of the drop (default 0.5); the onset is the foot of the tangent at
            its steepest point. A slow ramp before the drop (Seiko 4R36 tac) is not a front.
        template_front_lift_fraction: The front is searched within this fraction of the
            running t_lift before the drop maximum (default 0.4), which keeps P2 out.
        template_amp_mismatch_deg: The balance has the same amplitude in tic and tac. When the
            two templates (level onsets) imply amplitudes further apart than this (default
            20°), the level/front combination whose amplitudes agree best is used for the
            template t_lift. ``t_fine`` always marks ``onset_ref`` (the level onset).
        template_refresh_beats: Once a template holds ``template_beats`` beats, its median
            (and onsets) are recomputed every this many beats of its parity instead of every
            beat (default 2). The median of 24 beats moves little from one beat to the next
            and it is the dominant cost at high beat rates (quartz, 16 beats/s): 2 is the
            smallest value that keeps detector + metrics >= 90x real time on the quartz take
            (84x -> 100x; mechanical ~100x -> ~110x). 1 = every beat.
        template_align_smooth_ms: Moving average applied to the envelope ONLY for the
            alignment (default 1 ms): it fuses the lobes of the drop into one burst, so the
            correlation does not jump between lobes whose relative level changes from beat to
            beat. Onsets are still measured on the unsmoothed template.
    """

    sample_rate: float
    bph: float
    lift_angle_deg: float = 52.0
    amplitude_range_deg: tuple[float, float] = (90.0, 400.0)
    band_hz: tuple[float, float] | None = None
    filter_order: int = 4
    envelope_smooth_ms: float = 0.15
    grid_step_s: float = 0.25
    noise_window_s: float = 1.0
    threshold_hi: float = 6.0
    threshold_lo: float = 3.0
    ripple_merge_ms: float = 0.3
    min_subevent_spacing_ms: float = 1.0
    split_ratio: float = 2.0
    onset_fraction: float = 0.4
    subevent_rel_threshold: float = 0.05
    min_anchor_snr_db: float = 12.0
    min_subevent_snr_db: float = 12.0
    weak_snr_db: float = 20.0
    phase_tolerance: float = 0.1
    period_search: float = 0.06
    acquisition_s: float = 2.0
    acquisition_min_fraction: float = 0.6
    model_beats: int = 16
    outlier_sigma: float = 5.0
    outlier_min_ms: float = 0.3
    max_missed: int = 8
    lift_margin: float = 0.05
    lift_track_tolerance: float = 0.15
    typical_amplitude_deg: tuple[float, float] = (200.0, 330.0)
    saturation_level: float = 0.99
    dropout_min_ms: float = 10.0
    period_warn: float = 0.01
    post_group_ms: float = 6.0
    template_beats: int = 24
    template_min_beats: int = 6
    template_search_ms: float = 1.5
    template_post_ms: float = 6.0
    template_p3_fraction: float = 0.2
    template_lobe_fraction: float = 0.4
    template_burst_ms: float = 2.5
    template_p1_fraction: float = 0.04
    template_min_corr: float = 0.5
    template_align_smooth_ms: float = 1.0
    template_front_fraction: float = 0.5
    template_front_lift_fraction: float = 0.4
    template_amp_mismatch_deg: float = 20.0
    template_refresh_beats: int = 2

    def __post_init__(self) -> None:
        fs = float(self.sample_rate)
        if not (math.isfinite(fs) and fs >= 8000.0):
            raise ValueError(f"sample_rate debe ser >= 8000 Hz (recibido {self.sample_rate})")
        if not (math.isfinite(float(self.bph)) and 3600.0 <= float(self.bph) <= 360000.0):
            raise ValueError(f"bph fuera de rango [3600, 360000] (recibido {self.bph})")
        if not 0.0 < self.lift_angle_deg < 90.0:
            raise ValueError(f"lift_angle_deg fuera de (0, 90) (recibido {self.lift_angle_deg})")
        a_lo, a_hi = self.amplitude_range_deg
        if not (self.lift_angle_deg / 2.0 < a_lo < a_hi <= 720.0):
            raise ValueError(f"amplitude_range_deg no válido: {self.amplitude_range_deg}")
        if self.band_hz is None:
            self.band_hz = (1000.0, min(15000.0, 0.45 * fs))
        lo, hi = self.band_hz
        if not 0.0 < lo < hi < fs / 2.0:
            raise ValueError(f"band_hz no válida para fs={fs}: {self.band_hz}")
        positives = {
            "filter_order": self.filter_order,
            "envelope_smooth_ms": self.envelope_smooth_ms,
            "grid_step_s": self.grid_step_s,
            "noise_window_s": self.noise_window_s,
            "threshold_hi": self.threshold_hi,
            "threshold_lo": self.threshold_lo,
            "ripple_merge_ms": self.ripple_merge_ms,
            "min_subevent_spacing_ms": self.min_subevent_spacing_ms,
            "phase_tolerance": self.phase_tolerance,
            "period_search": self.period_search,
            "acquisition_s": self.acquisition_s,
            "model_beats": self.model_beats,
            "outlier_sigma": self.outlier_sigma,
            "max_missed": self.max_missed,
            "dropout_min_ms": self.dropout_min_ms,
            "post_group_ms": self.post_group_ms,
        }
        for name, value in positives.items():
            if not value > 0:
                raise ValueError(f"{name} debe ser positivo (recibido {value})")
        if self.threshold_lo >= self.threshold_hi:
            raise ValueError("threshold_lo debe ser menor que threshold_hi")
        if not self.split_ratio > 1.0:
            raise ValueError(f"split_ratio debe ser > 1 (recibido {self.split_ratio})")
        if not 0.0 < self.onset_fraction < 1.0:
            raise ValueError(f"onset_fraction fuera de (0, 1): {self.onset_fraction}")
        if not 0.0 <= self.subevent_rel_threshold < 1.0:
            raise ValueError(
                f"subevent_rel_threshold fuera de [0, 1): {self.subevent_rel_threshold}"
            )
        if not 0.0 < self.phase_tolerance < 0.3:
            raise ValueError(f"phase_tolerance fuera de (0, 0.3): {self.phase_tolerance}")
        if not 0.0 < self.acquisition_min_fraction <= 1.0:
            raise ValueError("acquisition_min_fraction fuera de (0, 1]")
        if self.model_beats < 6:
            raise ValueError("model_beats debe ser >= 6")
        if not 0.0 < self.saturation_level <= 1.0:
            raise ValueError("saturation_level fuera de (0, 1]")
        if self.template_refresh_beats < 1:
            raise ValueError("template_refresh_beats debe ser >= 1")
        if not 1 <= self.template_min_beats <= self.template_beats:
            raise ValueError("se requiere 1 <= template_min_beats <= template_beats")
        for name in ("template_search_ms", "template_post_ms"):
            if not getattr(self, name) > 0:
                raise ValueError(f"{name} debe ser positivo (recibido {getattr(self, name)})")
        if not self.template_amp_mismatch_deg > 0:
            raise ValueError(
                f"template_amp_mismatch_deg debe ser positivo: {self.template_amp_mismatch_deg}"
            )
        if not self.template_burst_ms > 0:
            raise ValueError(f"template_burst_ms debe ser positivo: {self.template_burst_ms}")
        for name in (
            "template_p3_fraction",
            "template_p1_fraction",
            "template_min_corr",
            "template_lobe_fraction",
            "template_front_fraction",
            "template_front_lift_fraction",
        ):
            if not 0.0 < getattr(self, name) < 1.0:
                raise ValueError(f"{name} fuera de (0, 1): {getattr(self, name)}")


@dataclass(frozen=True)
class DetectorStats:
    """Snapshot of the detector counters.

    Attributes:
        samples_processed: Samples received.
        beats_emitted: Beats returned.
        missed_beats: Gaps in ``k`` between emitted beats (beats predicted but not found,
            including those inside data gaps).
        gap_beats: Beats predicted inside data gaps (subset of ``missed_beats``).
        gaps: Data gaps (stream overruns and runs of digital silence).
        rejected_events: Strong sub-events outside any beat (or lone timing outliers).
        relocks: Times the lock was lost and acquired again.
        lock_sample: Absolute index at which the first lock was achieved (None if never).
        lock_time_s: ``lock_sample`` relative to the first sample, in seconds.
        p1_fraction, p2_fraction, p3_fraction: Fraction of emitted beats with each instant.
        period_samples: Observed beat period (phase model), NaN before lock.
        nominal_period_samples: ``3600 / BPH * fs``.
        warnings: Human-readable warnings (Spanish), e.g. a probable wrong BPH.
    """

    samples_processed: int
    beats_emitted: int
    missed_beats: int
    gap_beats: int
    gaps: int
    rejected_events: int
    relocks: int
    lock_sample: float | None
    lock_time_s: float | None
    p1_fraction: float
    p2_fraction: float
    p3_fraction: float
    period_samples: float
    nominal_period_samples: float
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def period_deviation(self) -> float:
        """Observed / nominal period - 1 (NaN before lock)."""
        return self.period_samples / self.nominal_period_samples - 1.0


# ====================================================================== internals
class _Buf:
    """Growable float64 buffer addressed by absolute index (amortised append/trim)."""

    def __init__(self, capacity: int) -> None:
        self._a = np.empty(max(1024, int(capacity)))
        self._n = 0
        self.start = 0

    def reset(self, start: int) -> None:
        self._n = 0
        self.start = int(start)

    @property
    def end(self) -> int:
        return self.start + self._n

    def append(self, x: NDArray) -> None:
        m = x.shape[0]
        need = self._n + m
        if need > self._a.shape[0]:
            new = np.empty(max(2 * self._a.shape[0], need))
            new[: self._n] = self._a[: self._n]
            self._a = new
        self._a[self._n : need] = x
        self._n = need

    def get(self, a: int, b: int) -> NDArray:
        return self._a[a - self.start : b - self.start]

    def trim(self, keep_from: int) -> None:
        drop = int(keep_from) - self.start
        if drop <= 0 or drop < self._n // 2:
            return
        drop = min(drop, self._n)
        self._a[: self._n - drop] = self._a[drop : self._n]
        self._n -= drop
        self.start += drop


class _Cands:
    """Sub-event candidates as parallel arrays sorted by onset."""

    def __init__(self) -> None:
        self.onset = np.zeros(0)
        self.peak = np.zeros(0)
        self.floor = np.zeros(0)
        self.spread = np.zeros(0)
        self.used = np.zeros(0, dtype=bool)

    def extend(self, onset: list, peak: list, floor: list, spread: list) -> None:
        if not onset:
            return
        self.onset = np.concatenate([self.onset, onset])
        self.peak = np.concatenate([self.peak, peak])
        self.floor = np.concatenate([self.floor, floor])
        self.spread = np.concatenate([self.spread, spread])
        self.used = np.concatenate([self.used, np.zeros(len(onset), dtype=bool)])

    def trim(self, keep_from: float) -> None:
        i = int(np.searchsorted(self.onset, keep_from))
        if i > 256:
            for name in ("onset", "peak", "floor", "spread", "used"):
                setattr(self, name, getattr(self, name)[i:])

    def range(self, a: float, b: float) -> tuple[int, int]:
        return int(np.searchsorted(self.onset, a)), int(np.searchsorted(self.onset, b))


class _Tpl:
    """Template of one parity: last aligned envelope segments, their median and onsets."""

    def __init__(self, n: int) -> None:
        self.segs: deque[NDArray] = deque(maxlen=n)
        self.env: NDArray | None = None
        self.onset_p1 = math.nan
        self.onset_p3 = math.nan  # level onset: the point t_fine marks
        self.onset_front = math.nan  # steep-front onset (alternative for the t_lift)
        # template origin + pre minus the phase-model prediction, last beats (centres the
        # next search on the prediction instead of on the jittery per-beat anchor)
        self.off: deque[float] = deque(maxlen=16)
        self.pending = 0  # beats added since the last median


def _hilbert_fir(half: int) -> NDArray:
    n = np.arange(-half, half + 1)
    h = np.zeros(n.size)
    odd = n % 2 != 0
    h[odd] = 2.0 / (np.pi * n[odd])
    return h * np.blackman(n.size)


def _runs(mask: NDArray[np.bool_]) -> tuple[NDArray, NDArray]:
    d = np.diff(np.concatenate(([0], mask.view(np.int8), [0])))
    return np.flatnonzero(d == 1), np.flatnonzero(d == -1)


# ====================================================================== detector
class BeatDetector:
    """Incremental beat detector (see module docstring). Not thread-safe.

    Args:
        config: ``BeatDetectorConfig``.
    """

    def __init__(self, config: BeatDetectorConfig) -> None:
        self.config = config
        c = config
        fs = float(c.sample_rate)
        self.fs = fs
        ms = fs / 1000.0
        self.S = max(1, int(round(c.grid_step_s * fs)))
        self.sos = signal.butter(c.filter_order, c.band_hz, btype="bandpass", fs=fs, output="sos")
        self._hk = _hilbert_fir(int(math.ceil(1.5 * fs / c.band_hz[0])))
        sm = max(1, int(round(c.envelope_smooth_ms * ms)))
        sm += 1 - sm % 2  # odd -> centred
        self._smooth = np.full(sm, 1.0 / sm)
        imp = signal.sosfilt(self.sos, np.r_[1.0, np.zeros(int(0.2 * fs))])
        big = np.flatnonzero(np.abs(imp) > 1e-6 * np.max(np.abs(imp)))
        self.filter_memory = int(big[-1]) + 1 if big.size else 1
        self.margin = 3 * self.filter_memory + self._hk.size // 2 + sm // 2 + 1
        self._padlen = 3 * (2 * len(self.sos) + 1)
        self.OV = int(round(0.04 * fs))
        self.period_nom = beat_period_s(c.bph) * fs
        tmin, tmax = lift_time_window_s(c.bph, c.lift_angle_deg, c.amplitude_range_deg)
        self.tmin = tmin * fs * (1.0 - c.lift_margin)
        self.tmax = tmax * fs * (1.0 + c.lift_margin)
        self.slack = 0.3 * ms
        self.min_sp = c.min_subevent_spacing_ms * ms
        self.merge = max(1, int(round(c.ripple_merge_ms * ms)))
        self.post = c.post_group_ms * ms
        self.pre = 0.5 * ms
        self.dropout_min = max(1, int(round(c.dropout_min_ms * ms)))
        self.W_noise = int(round(c.noise_window_s * fs))
        self.W_acq = int(round(c.acquisition_s * fs))
        self.tpl_pre = int(math.ceil(self.tmax + 2.0 * ms))
        self.tpl_post = int(math.ceil(c.template_post_ms * ms))
        self.tpl_search = max(1, int(round(c.template_search_ms * ms)))
        self.tpl_burst = int(round(3.0 * ms))  # alignment uses the template from 3 ms before
        n_al = max(1, int(round(c.template_align_smooth_ms * ms)))
        self._align_kernel = np.full(n_al, 1.0 / n_al)
        self.reset()

    # ------------------------------------------------------------------ public API
    def reset(self) -> None:
        """Forget everything (audio, lock, counters)."""
        self._started = False
        self._finished = False
        self._expected = 0
        self._first_index = 0
        self._raw = _Buf(4 * self.S)
        self._env = _Buf(8 * self.S)
        self._run_start = 0
        self._samples = 0
        self._emitted = 0
        self._missed = 0
        self._gap_beats = 0
        self._gaps = 0
        self._rejected = 0
        self._relocks = 0
        self._lock_sample: float | None = None
        self._n_p = [0, 0, 0]
        self._warnings: list[str] = []
        self._locked = False
        self._ever_locked = False
        self._period = self.period_nom
        self._last_k: int | None = None
        self._last_ref: float | None = None
        self._tlift2: deque[float] = deque(maxlen=32)
        self._tlift3: deque[float] = deque(maxlen=32)
        self._tlift_cache: tuple[float | None] | None = None
        self._tl_obs: deque[float] = deque(maxlen=8)
        self._tl_fwd: deque[float] = deque(maxlen=8)
        self._after_gap = False
        self._tpl = [_Tpl(self.config.template_beats), _Tpl(self.config.template_beats)]
        self._start_run(0)

    def process(self, chunk: ArrayLike, start_index: int) -> list[Beat]:
        """Feed contiguous samples whose first one has absolute index ``start_index``.

        Returns only NEW and DEFINITIVE beats (never re-emitted or modified). A jump forward
        in ``start_index`` is treated as a data gap (``AFTER_GAP`` on the next beat, ``k``
        keeps counting).

        Raises:
            ValueError: If the chunk is not 1-D or overlaps samples already processed.
            RuntimeError: If called after ``flush()`` without ``reset()``.
        """
        if self._finished:
            raise RuntimeError("el detector ya terminó (flush); llama a reset() para reutilizarlo")
        x = np.asarray(chunk, dtype=np.float64)
        if x.ndim != 1:
            raise ValueError(f"se esperaba audio mono 1-D, forma recibida {x.shape}")
        start_index = int(start_index)
        out: list[Beat] = []
        if not self._started:
            self._started = True
            self._first_index = start_index
            self._expected = start_index
            self._start_run(start_index)
            _dbg.process_start("BeatDetector", fs=self.fs, bph=self.config.bph, start=start_index)
        elif start_index > self._expected:
            out += self._handle_gap(start_index)
        elif start_index < self._expected:
            raise ValueError(
                f"el bloque empieza en {start_index} pero ya se procesó hasta {self._expected}"
            )
        if x.size == 0:
            return out
        self._raw.append(x)
        self._samples += x.size
        self._expected = start_index + x.size
        out += self._pump(final=False)
        return out

    def advance(self, stream: SampleStream) -> list[Beat]:
        """Process what arrived in ``stream`` since the last call.

        If the reader fell behind (``SamplesOverwritten``) the lost range is a data gap:
        it is recorded, the next beat gets ``AFTER_GAP`` and ``k`` keeps counting.
        """
        total = stream.total_samples
        start = self._expected if self._started else stream.oldest_available
        if total <= start:
            return []
        for _ in range(8):
            try:
                x = stream.read(start, total)
                break
            except SamplesOverwritten:
                total = stream.total_samples
                start = min(total, max(start, stream.oldest_available + stream.capacity // 8))
        else:  # pragma: no cover - producer lapping us 8 times in a row
            raise SamplesOverwritten("el productor adelantó al detector repetidamente")
        return self.process(x, start)

    def flush(self) -> list[Beat]:
        """Process the remaining audio (end of file) and return the last beats.

        After ``flush()`` the detector must be ``reset()`` before reuse.
        """
        if self._finished:
            return []
        out = self._pump(final=True) if self._started else []
        self._close_zero_run(self._raw.end)
        self._finished = True
        _dbg.process_end(
            "BeatDetector",
            samples=self._samples,
            beats=self._emitted,
            missed=self._missed,
            gaps=self._gaps,
            rejected=self._rejected,
        )
        return out

    @property
    def stats(self) -> DetectorStats:
        """Counters snapshot (see ``DetectorStats``)."""
        n = max(1, self._emitted)
        lock_t = None
        if self._lock_sample is not None:
            lock_t = (self._lock_sample - self._first_index) / self.fs
        return DetectorStats(
            samples_processed=self._samples,
            beats_emitted=self._emitted,
            missed_beats=self._missed,
            gap_beats=self._gap_beats,
            gaps=self._gaps,
            rejected_events=self._rejected,
            relocks=self._relocks,
            lock_sample=self._lock_sample,
            lock_time_s=lock_t,
            p1_fraction=self._n_p[0] / n,
            p2_fraction=self._n_p[1] / n,
            p3_fraction=self._n_p[2] / n,
            period_samples=self._period if self._ever_locked else math.nan,
            nominal_period_samples=self.period_nom,
            warnings=tuple(self._warnings),
        )

    def templates(self) -> tuple[ParityTemplate | None, ParityTemplate | None]:
        """Current templates for even and odd ``k`` (None until a parity has one).

        ``onset_p3`` (used for the t_lift) is the level onset of both templates unless their
        implied amplitudes disagree by more than ``template_amp_mismatch_deg``; then each
        parity takes the level or the steep-front onset, whichever combination makes the two
        amplitudes agree best (ties prefer the level onset). ``onset_ref`` is the point
        ``t_fine`` marks: the level onset of the current template.
        """
        methods = self._p3_methods()
        out: list[ParityTemplate | None] = []
        for parity, t in enumerate(self._tpl):
            if t.env is None:
                out.append(None)
                continue
            method = methods[parity]
            p3 = t.onset_front if method == "frente" else t.onset_p3
            out.append(
                ParityTemplate(
                    parity=parity,
                    n_beats=len(t.segs),
                    envelope=t.env.copy(),
                    onset_p1=t.onset_p1,
                    onset_p3=p3,
                    onset_ref=t.onset_p3,
                    p3_method=method,
                )
            )
        return out[0], out[1]

    def _p3_methods(self) -> tuple[str, str]:
        """P3 onset definition per parity for the t_lift ("nivel" or "frente")."""
        c = self.config
        t0, t1 = self._tpl
        default = ("nivel", "nivel")
        if t0.env is None or t1.env is None:
            return default

        def amp(p3: float, p1: float) -> float:
            tl = (p3 - p1) / self.fs
            if not (math.isfinite(tl) and tl > 0):
                return math.nan
            return amplitude_from_lift_time(tl, c.lift_angle_deg, c.bph)

        a0, a1 = amp(t0.onset_p3, t0.onset_p1), amp(t1.onset_p3, t1.onset_p1)
        if not (math.isfinite(a0) and math.isfinite(a1)):
            return default
        if abs(a0 - a1) <= c.template_amp_mismatch_deg:
            return default
        best: tuple[float, int, tuple[str, str]] | None = None
        for m0 in ("nivel", "frente"):
            for m1 in ("nivel", "frente"):
                b0 = amp(t0.onset_front if m0 == "frente" else t0.onset_p3, t0.onset_p1)
                b1 = amp(t1.onset_front if m1 == "frente" else t1.onset_p3, t1.onset_p1)
                if not (math.isfinite(b0) and math.isfinite(b1)):
                    continue
                key = (abs(b0 - b1), (m0 == "frente") + (m1 == "frente"), (m0, m1))
                if best is None or key[:2] < best[:2]:
                    best = key
        return best[2] if best is not None else default

    # ------------------------------------------------------------------ runs and gaps
    def _start_run(self, start: int) -> None:
        self._run_start = int(start)
        self._raw.reset(start)
        self._env.reset(start)
        self._next_cell = start // self.S
        self._first_cell = self._next_cell
        self._cand_cell = self._next_cell
        self._cand_end = start
        self._cands = _Cands()
        self._floors: dict[int, tuple[float, float]] = {}
        self._zones: deque[tuple[float, float]] = deque()
        self._dropouts: list[tuple[int, int]] = []
        self._zero_start: int | None = None
        self._acq_from = start + self.filter_memory
        self._rej_from = float(start)

    def _handle_gap(self, new_start: int) -> list[Beat]:
        out = self._pump(final=True)
        self._close_zero_run(self._raw.end)
        lost = new_start - self._expected
        self._gaps += 1
        self._after_gap = True
        _dbg.logger.warning(
            "BeatDetector: hueco de datos de %d muestras (%.3f s) en %d; resincronizando",
            lost,
            lost / self.fs,
            self._expected,
        )
        self._start_run(new_start)
        if self._locked:
            while self._predict(self._j) < new_start + self.filter_memory:
                self._j += 1
                self._gap_beats += 1
        return out

    def _close_zero_run(self, end: int) -> None:
        if self._zero_start is not None:
            self._register_dropout(self._zero_start, end)
            self._zero_start = None

    def _register_dropout(self, a: int, b: int) -> None:
        if b - a < self.dropout_min:
            return
        self._dropouts.append((a, b))
        self._gaps += 1
        self._after_gap = True
        _dbg.logger.warning(
            "BeatDetector: silencio digital de %.1f ms en %d (tratado como hueco)",
            (b - a) / self.fs * 1000.0,
            a,
        )

    def _scan_zeros(self, a: int, x: NDArray) -> None:
        z = x == 0.0
        if not z.any():
            self._close_zero_run(a)
            return
        starts, ends = _runs(z)
        for s, e in zip(starts, ends, strict=True):
            s_abs, e_abs = a + int(s), a + int(e)
            if s == 0 and self._zero_start is not None:
                s_abs = self._zero_start  # run continues from the previous cell
            elif self._zero_start is not None:
                self._register_dropout(self._zero_start, a)
            self._zero_start = None
            if e == x.size:
                self._zero_start = s_abs  # still open at the end of the cell
            else:
                self._register_dropout(s_abs, e_abs)

    def _in_dropout(self, a: float, b: float) -> bool:
        for s, e in self._dropouts:
            if s < b and a < e:
                return True
        return self._zero_start is not None and b > self._zero_start

    # ------------------------------------------------------------------ pipeline
    def _pump(self, final: bool) -> list[Beat]:
        out: list[Beat] = []
        S = self.S
        while True:
            m = self._next_cell
            c0 = max(m * S, self._run_start)
            if self._raw.end >= (m + 1) * S + self.margin or (final and self._raw.end > c0):
                self._env_cell(m)
                self._next_cell += 1
                out += self._after_cell(final=False)
            else:
                break
        if final:
            out += self._after_cell(final=True)
        self._trim()
        return out

    def _env_cell(self, m: int) -> None:
        S = self.S
        a = max(m * S - self.margin, self._run_start)
        b = min((m + 1) * S + self.margin, self._raw.end)
        c0 = max(m * S, self._run_start)
        c1 = min((m + 1) * S, self._raw.end)
        x = self._raw.get(a, b)
        if x.size > self._padlen + 1:
            y = signal.sosfiltfilt(self.sos, x)
            h = signal.oaconvolve(y, self._hk, mode="same")
            e = np.sqrt(y * y + h * h)
            e = np.convolve(e, self._smooth, mode="same")
        else:
            e = np.zeros(x.size)
        self._env.append(e[c0 - a : c1 - a])
        self._scan_zeros(c0, self._raw.get(c0, c1))

    def _after_cell(self, final: bool) -> list[Beat]:
        last = self._next_cell - (1 if final else 2)
        while self._cand_cell <= last:
            self._detect_cell(self._cand_cell)
            self._cand_cell += 1
            self._cand_end = min((self._cand_cell) * self.S, self._env.end)
        if final:
            self._cand_end = self._env.end
        out: list[Beat] = []
        if not self._locked:
            self._try_acquire()
        if self._locked:
            out = self._track(final)
            if not self._locked:  # lost during tracking: try again on the same data
                self._try_acquire()
                if self._locked:
                    out += self._track(final)
        return out

    def _trim(self) -> None:
        keep = min(self._next_cell * self.S - self.margin, int(self._cand_end) - int(self.fs))
        self._raw.trim(keep)
        self._env.trim(self._cand_cell * self.S - self.W_noise - 3 * self.S)
        self._cands.trim(self._cand_end - self.W_acq - self.fs)
        while self._zones and self._zones[0][1] < self._env.start:
            self._zones.popleft()
        old = self._next_cell - 16
        for key in [k for k in self._floors if k < old]:
            del self._floors[key]

    # ------------------------------------------------------------------ noise floor
    def _floor(self, cell: int) -> tuple[float, float]:
        got = self._floors.get(cell)
        if got is not None:
            return got
        S = self.S
        hi = (cell - 1) * S
        lo = max(self._run_start, hi - self.W_noise, self._env.start)
        if hi - lo < S // 2:
            lo = max(self._run_start, self._env.start)
            hi = min(lo + S, self._env.end)
        e = self._env.get(lo, hi)
        keep = e > 0.0
        for za, zb in self._zones:
            if zb <= lo or za >= hi:
                continue
            keep[max(0, int(za) - lo) : max(0, int(math.ceil(zb)) - lo)] = False
        if np.count_nonzero(keep) < 0.25 * e.size:
            keep = e > 0.0
        v = e[keep]
        if v.size == 0:
            res = (_TINY, _TINY)
        else:
            med = float(np.median(v))
            mad = float(np.median(np.abs(v - med)))
            res = (max(med, _TINY), max(1.4826 * mad, 0.05 * med, _TINY))
        self._floors[cell] = res
        return res

    # ------------------------------------------------------------------ sub-events
    def _detect_cell(self, cell: int) -> None:
        c = self.config
        S = self.S
        lo = max(cell * S - self.OV, self._run_start, self._env.start)
        hi = min((cell + 1) * S + self.OV, self._env.end)
        if hi - lo < 3:
            return
        e = self._env.get(lo, hi)
        cells = np.arange(lo // S, (hi - 1) // S + 1)
        fl = np.array([self._floor(int(k)) for k in cells])
        idx = (np.arange(lo, hi) // S) - cells[0]
        F = fl[idx, 0]
        D = fl[idx, 1]
        thr_hi = F + c.threshold_hi * D
        thr_lo = F + c.threshold_lo * D
        starts, ends = _runs(e > thr_lo)
        if starts.size == 0:
            return
        if starts.size > 1:  # merge runs separated by less than ripple_merge_ms
            keep = np.r_[True, (starts[1:] - ends[:-1]) >= self.merge]
            ends = np.maximum.reduceat(ends, np.flatnonzero(keep))
            starts = starts[keep]
        on_l: list[float] = []
        pk_l: list[float] = []
        fl_l: list[float] = []
        sp_l: list[float] = []
        cell_a, cell_b = cell * S, (cell + 1) * S
        for s, t in zip(starts, ends, strict=True):
            if not np.any(e[s:t] > thr_hi[s:t]):  # hysteresis: must reach the high threshold
                continue
            for onset, peak, pidx in self._split(e, s, t, F, D, thr_hi):
                o_abs = lo + onset
                if cell_a <= o_abs < cell_b and o_abs >= self._run_start:
                    on_l.append(o_abs)
                    pk_l.append(peak)
                    fl_l.append(float(F[pidx]))
                    sp_l.append(float(D[pidx]))
        self._cands.extend(on_l, pk_l, fl_l, sp_l)

    def _split(
        self, e: NDArray, s: int, t: int, F: NDArray, D: NDArray, thr_hi: NDArray
    ) -> list[tuple[float, float, int]]:
        """Sub-events of the run ``e[s:t]``: list of (onset, peak value, peak index)."""
        c = self.config
        seg = e[s:t]
        peaks, _ = signal.find_peaks(seg)
        if peaks.size == 0:
            peaks = np.array([int(np.argmax(seg))])
        peaks = peaks[seg[peaks] > thr_hi[s + peaks]]
        if peaks.size == 0:
            return []
        groups: list[list[int]] = []  # [peak index (rel to seg), baseline-valley index or -1]
        for p in peaks:
            p = int(p)
            if not groups:
                groups.append([p, -1])
                continue
            cur = groups[-1]
            v_rel = cur[0] + int(np.argmin(seg[cur[0] : p + 1]))
            valley = max(float(seg[v_rel]), float(F[s + p]))
            if seg[p] >= c.split_ratio * valley and (p - cur[0]) >= self.min_sp:
                groups.append([p, v_rel])
            elif seg[p] > seg[cur[0]]:
                cur[0] = p
        out = []
        for p, v_rel in groups:
            i = s + p
            # baseline 0 for an isolated sub-event (so the crossing does not move with the noise
            # floor), the preceding valley for one riding on the tail of another
            base = 0.0 if v_rel < 0 else max(float(seg[v_rel]), float(F[i]))
            level = base + c.onset_fraction * (float(e[i]) - base)
            level = max(level, float(F[i] + c.threshold_lo * D[i]))
            # search back from the peak to the preceding valley (or 1 ms before the run)
            j0 = s + v_rel if v_rel >= 0 else max(0, s - int(self.min_sp))
            below = np.flatnonzero(e[j0:i] < level)
            if below.size == 0:
                onset = float(j0)
            else:
                j = j0 + int(below[-1])
                d = e[j + 1] - e[j]
                onset = j + ((level - e[j]) / d if d > 0 else 0.0)
            out.append((onset, float(e[i]), i))
        return out

    # ------------------------------------------------------------------ acquisition
    def _try_acquire(self) -> None:
        c = self.config
        end = self._cand_end
        start = max(self._acq_from, end - self.W_acq)
        if end - start < self.W_acq:
            return
        cd = self._cands
        i0, i1 = cd.range(start, end)
        snr = 20.0 * np.log10(np.maximum(cd.peak[i0:i1], _TINY) / cd.floor[i0:i1])
        strong = snr >= c.min_anchor_snr_db
        t = cd.onset[i0:i1][strong]
        w = np.minimum(cd.peak[i0:i1][strong] / cd.floor[i0:i1][strong], 1e3)
        P2n = 2.0 * self.period_nom
        need = c.acquisition_min_fraction * self.W_acq / P2n
        if t.size < max(3, need):
            self._warn_no_lock(end)
            return
        bin_ = 0.5e-3 * self.fs
        step = bin_ * P2n / (2.0 * self.W_acq)
        n_p = int(math.ceil(2.0 * c.period_search * P2n / step)) + 1
        P2 = P2n * (1.0 + np.linspace(-c.period_search, c.period_search, n_p))
        ph = np.mod(t[None, :] - start, P2[:, None])
        nb = np.ceil(P2 / bin_).astype(np.int64)
        nbmax = int(nb.max())
        bi = np.minimum((ph / bin_).astype(np.int64), nb[:, None] - 1)
        rows = (np.arange(n_p, dtype=np.int64) * nbmax)[:, None]
        wt = np.broadcast_to(w, bi.shape).ravel()
        H = np.zeros(n_p * nbmax)
        for o in (-1, 0, 1):
            flat = (rows + np.mod(bi + o, nb[:, None])).ravel()
            H += np.bincount(flat, weights=wt, minlength=n_p * nbmax)
        H = H.reshape(n_p, nbmax)
        best = int(np.argmax(H))
        r, b = divmod(best, nbmax)
        center = (b + 0.5) * bin_
        d = np.abs(np.mod(ph[r] - center + P2[r] / 2.0, P2[r]) - P2[r] / 2.0)
        member = d <= 1.5 * bin_
        if np.count_nonzero(member) < max(3, c.acquisition_min_fraction * self.W_acq / P2[r]):
            self._warn_no_lock(end)
            return
        # refine phase: weighted circular mean of the members
        dev = np.mod(ph[r][member] - center + P2[r] / 2.0, P2[r]) - P2[r] / 2.0
        phase = start + center + float(np.average(dev, weights=w[member]))
        self._period = float(P2[r] / 2.0)
        self._phase0 = phase
        self._locked = True
        self._model: deque[tuple[int, float]] = deque(maxlen=c.model_beats)
        self._coef: tuple[float, float, float, int, float] | None = None
        self._missed_run = 0
        self._outliers: deque[tuple[int, float, float]] = deque(maxlen=3)
        self._k_offset: int | None = None
        for t in self._tpl:
            t.off.clear()
        tol = self._tol()
        j = int(math.ceil((self._acq_from - phase) / self._period))
        self._j = j
        self._rej_from = max(self._rej_from, phase + j * self._period - tol)
        if self._ever_locked:
            self._relocks += 1
        else:
            self._lock_sample = float(end)
        self._ever_locked = True
        _dbg.state_change(
            "BeatDetector",
            "SIN ENGANCHE",
            "ENGANCHADO",
            at=int(end),
            period=round(self._period, 2),
            members=int(np.count_nonzero(member)),
        )

    def _warn_no_lock(self, end: float) -> None:
        if self._ever_locked or end - self._first_index < 5.0 * self.fs:
            return
        if any(w.startswith("sin enganche") for w in self._warnings):
            return
        msg = (
            f"sin enganche tras {(end - self._first_index) / self.fs:.1f} s: "
            f"¿BPH mal configurado ({self.config.bph:g}) o señal sin batimientos?"
        )
        self._warnings.append(msg)
        _dbg.logger.warning("BeatDetector: %s", msg)

    def _lose_lock(self) -> None:
        self._locked = False
        last = self._last_ref if self._last_ref is not None else self._cand_end
        self._acq_from = max(self._acq_from, last + self._period / 2.0)
        _dbg.state_change(
            "BeatDetector",
            "ENGANCHADO",
            "SIN ENGANCHE",
            at=int(self._cand_end),
            missed=self.config.max_missed,
        )

    # ------------------------------------------------------------------ phase model
    def _tol(self) -> float:
        return self.config.phase_tolerance * self._period

    def _predict(self, j: int) -> float:
        if self._coef is None:
            if self._model:
                jl, tl = self._model[-1]
                return tl + (j - jl) * self._period
            return self._phase0 + j * self._period
        a, b, cpar, jl, _ = self._coef
        return a + b * (j - jl) + cpar * (1.0 if j % 2 == 0 else -1.0)

    def _refit(self) -> None:
        n = len(self._model)
        if n < 3:
            self._coef = None
            return
        js = np.array([m[0] for m in self._model], dtype=np.float64)
        ts = np.array([m[1] for m in self._model])
        jl = int(js[-1])
        t0 = ts[-1]
        cols = [np.ones(n), js - jl]
        parity = n >= 6
        if parity:
            cols.append(np.where(js % 2 == 0, 1.0, -1.0))
        X = np.stack(cols, axis=1)
        y = ts - t0
        w = np.ones(n)
        floor = 0.02e-3 * self.fs
        scale = floor
        for _ in range(3):
            sw = np.sqrt(w)
            coef, *_ = np.linalg.lstsq(X * sw[:, None], y * sw, rcond=None)
            r = y - X @ coef
            scale = max(1.4826 * float(np.median(np.abs(r))), floor)
            w = np.minimum(1.0, 1.5 * scale / np.maximum(np.abs(r), _TINY))
        a = float(coef[0]) + t0
        b = float(coef[1])
        cpar = float(coef[2]) if parity else 0.0
        self._coef = (a, b, cpar, jl, scale)
        if 0.5 * self.period_nom < b < 1.5 * self.period_nom:
            self._period = b
        if parity:
            dev = self._period / self.period_nom - 1.0
            if abs(dev) > self.config.period_warn and not any(
                w_.startswith("periodo observado") for w_ in self._warnings
            ):
                msg = (
                    f"periodo observado {dev * 100:+.2f} % respecto al nominal "
                    f"({self.config.bph:g} BPH): ¿BPH mal configurado?"
                )
                self._warnings.append(msg)
                _dbg.logger.warning("BeatDetector: %s", msg)

    # ------------------------------------------------------------------ tracking
    def _tlift_ref(self) -> float | None:
        # cached: it is asked several times per beat and only changes when a t_lift is added
        if self._tlift_cache is None:
            self._tlift_cache = (self._tlift_ref_compute(),)
        return self._tlift_cache[0]

    def _tlift_ref_compute(self) -> float | None:
        if len(self._tlift3) >= 8:
            return float(np.median(self._tlift3))
        if len(self._tlift2) >= 8:
            return float(np.median(self._tlift2))
        return None

    def _maybe_reseed(self) -> None:
        """Three consecutive outliers that agree with each other mean the timing really
        moved (e.g. the reference switched sub-event): restart the model from them."""
        gate = self._gate()
        if len(self._outliers) < 3 or gate is None:
            return
        res = np.array([o[2] for o in self._outliers])
        if np.ptp(res) <= 2.0 * gate:
            self._model.clear()
            self._model.extend((o[0], o[1]) for o in self._outliers)
            self._outliers.clear()
            self._refit()
            _dbg.logger.info("BeatDetector: modelo de fase re-sembrado (desplazamiento estable)")

    def _gate(self) -> float | None:
        """Outlier gate around the prediction (None until the model is mature)."""
        if self._coef is None or len(self._model) < 6:
            return None
        c = self.config
        return max(c.outlier_sigma * self._coef[4], c.outlier_min_ms * 1e-3 * self.fs)

    def _confirm_lift(self, tl: float, history: deque[float]) -> bool:
        """Record a P1 candidate's lift time; True if >= 2 of the last 8 beats agree.

        Stray clicks land at random lift times and are not confirmed, so a weak or absent
        P1 yields NaN instead of a spurious instant.
        """
        tol = max(self.config.lift_track_tolerance * tl, 0.5e-3 * self.fs)
        prev = np.array(history, dtype=np.float64)
        history.append(tl)
        if self._tlift_ref() is not None:
            return True
        return int(np.count_nonzero(np.abs(prev - tl) <= tol)) >= 2

    def _lift_window(self) -> tuple[float, float]:
        ref = self._tlift_ref()
        if ref is None:
            return self.tmin, self.tmax
        g = max(self.config.lift_track_tolerance * ref, 0.5e-3 * self.fs)
        return max(self.tmin, ref - g), min(self.tmax, ref + g)

    def _track(self, final: bool) -> list[Beat]:
        out: list[Beat] = []
        fwd = self.tmax + self.slack + self.post
        back = self.tmax + self.slack + self.pre
        while self._locked:
            j = self._j
            tp = self._predict(j)
            tol = self._tol()
            if final:
                if tp + 1e-3 * self.fs > self._cand_end:
                    break
            elif tp + tol + fwd > self._cand_end:
                break
            self._j += 1
            if self._in_dropout(tp - tol - back, tp + tol + fwd):
                self._after_gap = True
                self._gap_beats += 1
                self._count_rejected(tp + tol)
                continue
            beat = self._examine(j, tp, tol)
            self._count_rejected(tp + tol)
            if beat is None:
                self._missed_run += 1
                if self._missed_run > self.config.max_missed:
                    self._lose_lock()
                continue
            self._missed_run = 0
            out.append(beat)
        return out

    def _count_rejected(self, until: float) -> None:
        cd = self._cands
        i0, i1 = cd.range(self._rej_from, until)
        if i1 > i0:
            snr = 20.0 * np.log10(np.maximum(cd.peak[i0:i1], _TINY) / cd.floor[i0:i1])
            bad = (~cd.used[i0:i1]) & (snr >= self.config.min_anchor_snr_db)
            self._rejected += int(np.count_nonzero(bad))
            cd.used[i0:i1] = True
        self._rej_from = max(self._rej_from, until)

    def _examine(self, j: int, tp: float, tol: float) -> Beat | None:
        c = self.config
        cd = self._cands
        i0, i1 = cd.range(tp - tol, tp + tol)
        if i1 <= i0:
            return None
        pk = cd.peak[i0:i1]
        snr_all = 20.0 * np.log10(np.maximum(pk, _TINY) / cd.floor[i0:i1])
        ok = (snr_all >= c.min_anchor_snr_db) & (~cd.used[i0:i1])
        if not ok.any():
            return None
        gate = self._gate()
        if gate is not None:
            near = ok & (np.abs(cd.onset[i0:i1] - tp) <= gate)
            if near.any():
                ok = near  # mature model: prefer the strongest sub-event near the prediction
        ai = i0 + int(np.argmax(np.where(ok, pk, -np.inf)))
        a = float(cd.onset[ai])
        fa = float(cd.floor[ai])
        height = float(cd.peak[ai]) - fa
        snr = float(snr_all[ai - i0])

        min_ratio = 10.0 ** (c.min_subevent_snr_db / 20.0)

        def members(lo_t: float, hi_t: float) -> NDArray[np.int64]:
            k0, k1 = cd.range(lo_t, hi_t)
            if k1 <= k0:
                return np.zeros(0, dtype=np.int64)
            rel = (cd.peak[k0:k1] - cd.floor[k0:k1]) >= c.subevent_rel_threshold * height
            loud = cd.peak[k0:k1] >= cd.floor[k0:k1] * min_ratio
            free = ~cd.used[k0:k1]
            idx = np.arange(k0, k1)[rel & loud & free]
            return idx[idx != ai]

        w_lo, w_hi = self._lift_window()
        ref = self._tlift_ref()
        p1 = p2 = p3 = math.nan
        back = members(a - self.tmax - self.slack, a - self.min_sp)
        b_on = cd.onset[back]
        in_p1 = back[(b_on >= a - w_hi - self.slack) & (b_on <= a - w_lo + self.slack)]
        if in_p1.size:
            if ref is not None:  # closest to the running t_lift (robust to stray clicks)
                x = float(cd.onset[in_p1[np.argmin(np.abs(a - cd.onset[in_p1] - ref))]])
            else:
                x = float(cd.onset[in_p1[0]])
            confirmed = self._confirm_lift(a - x, self._tl_obs)
            if not self._is_p1(a, x, cd.onset[back]):
                p2, p3 = x, a
            elif confirmed:
                p1, p3 = x, a
            else:
                p3 = a
        else:
            self._tl_obs.append(math.nan)
            fwd = members(a + self.min_sp, a + self.tmax + self.slack)
            f_on = cd.onset[fwd]
            in_p3 = fwd[(f_on >= a + w_lo - self.slack) & (f_on <= a + w_hi + self.slack)]
            y = math.nan
            if in_p3.size:
                if ref is not None:
                    y = float(cd.onset[in_p3[np.argmin(np.abs(cd.onset[in_p3] - a - ref))]])
                else:
                    y = float(cd.onset[in_p3[-1]])
            # the anchor is P1 only if the later sub-event sits at a confirmed lift time
            # (a stray click after a lone P3 must not turn P3 into P1)
            if not math.isnan(y) and self._confirm_lift(y - a, self._tl_fwd):
                p1, p3 = a, y
            elif ref is not None and abs(a + ref - tp) < abs(a - tp):
                p1 = a
            else:
                p3 = a
        # P2
        if math.isnan(p2):
            if not math.isnan(p1) and not math.isnan(p3):
                tl = p3 - p1
                target, half = p1 + _P2_FRACTION * tl, 0.25 * tl
            elif ref is not None and not math.isnan(p3):
                target, half = p3 - (1.0 - _P2_FRACTION) * ref, 0.25 * ref
            elif ref is not None:
                target, half = p1 + _P2_FRACTION * ref, 0.25 * ref
            elif not math.isnan(p3):  # physical P2 window before P3
                lo_t = p3 - (1.0 - _P2_FRACTION) * self.tmax
                hi_t = p3 - (1.0 - _P2_FRACTION) * self.tmin
                target, half = 0.5 * (lo_t + hi_t), 0.5 * (hi_t - lo_t)
            else:
                target = half = math.nan
            if not math.isnan(target):
                cand = members(target - half, target + half)
                if cand.size:
                    p2 = float(cd.onset[cand[np.argmin(np.abs(cd.onset[cand] - target))]])
        # reference instant and residual
        if not math.isnan(p3):
            t_ref = p3
        else:
            t_ref = p1 + (ref if ref is not None else 0.0)
        residual = t_ref - tp
        flags = BeatFlags.NONE
        outlier = gate is not None and abs(residual) > gate
        pts = [v for v in (p1, p2, p3) if not math.isnan(v)]
        g0, g1 = min(pts), max(pts)
        k0, k1 = cd.range(g0 - self.slack, g1 + self.slack)
        rel = (cd.peak[k0:k1] - cd.floor[k0:k1]) >= c.subevent_rel_threshold * height
        n_sub = int(np.count_nonzero(rel & ~cd.used[k0:k1]))
        if outlier and len(pts) == 1:
            cd.used[ai] = True
            self._rejected += 1
            return None
        # mark the whole group as used
        u0, u1 = cd.range(min(g0, a - self.tmax) - self.slack, g1 + self.post)
        cd.used[u0:u1] = True
        self._zones.append((g0 - self.pre, g1 + self.post))
        if outlier:
            flags |= BeatFlags.TIMING_OUTLIER
            self._outliers.append((j, t_ref, residual))
            self._maybe_reseed()
        else:
            self._outliers.clear()
            self._model.append((j, t_ref))
            self._refit()
        if math.isnan(p1):
            flags |= BeatFlags.P1_MISSING
        if math.isnan(p2):
            flags |= BeatFlags.P2_MISSING
        if math.isnan(p3):
            flags |= BeatFlags.P3_MISSING
        if snr < c.weak_snr_db:
            flags |= BeatFlags.WEAK
        r0 = max(int(g0 - self.pre), self._raw.start)
        r1 = min(int(g1 + 4e-3 * self.fs) + 1, self._raw.end)
        raw = self._raw.get(r0, r1) if r1 > r0 else np.zeros(1)
        peak = float(np.max(np.abs(raw))) if raw.size else 0.0
        if peak >= c.saturation_level:
            flags |= BeatFlags.SATURATED
        if self._after_gap:
            flags |= BeatFlags.AFTER_GAP
            self._after_gap = False
        if not math.isnan(p1) and not math.isnan(p3):
            self._tlift2.append(p3 - p1)
            if not math.isnan(p2):
                self._tlift3.append(p3 - p1)
            self._tlift_cache = None
        # beat number
        if self._k_offset is None:
            if self._last_k is None:
                k = 0
            else:
                k = self._last_k + int(round((t_ref - self._last_ref) / self._period))
                k = max(k, self._last_k + 1)
            self._k_offset = k - j
        k = j + self._k_offset
        if self._last_k is not None:
            self._missed += max(0, k - self._last_k - 1)
        self._last_k = k
        self._last_ref = t_ref
        t_fine = self._fine(k, a, tp)
        conf = self._confidence(snr, p1, p2, p3, residual, flags)
        self._emitted += 1
        self._n_p[0] += not math.isnan(p1)
        self._n_p[1] += not math.isnan(p2)
        self._n_p[2] += not math.isnan(p3)
        return Beat(
            k=k,
            p1=p1,
            p2=p2,
            p3=p3,
            n_subevents=max(n_sub, len(pts)),
            peak_dbfs=20.0 * math.log10(max(peak, _TINY)),
            snr_db=snr,
            residual_samples=residual,
            confidence=conf,
            flags=flags,
            t_fine=t_fine,
        )

    # ------------------------------------------------------------------ fine timing
    def _fine(self, k: int, a: float, tp: float) -> float:
        """Align this beat to the template of its parity; return ``t_fine`` (or NaN)."""
        c = self.config
        t = self._tpl[k & 1]
        pre, post, s = self.tpl_pre, self.tpl_post, self.tpl_search
        if t.env is None:
            # seed on the prediction of a mature phase model (robust P3), never on the anchor
            # of a single beat: early on the anchor can be P2 or a late lobe, and the template
            # would keep that frame for good
            if self._gate() is None:
                return math.nan
            center = tp
        else:
            center = tp + float(np.median(t.off)) if t.off else tp
        ci = int(round(center))
        s0, s1 = ci - pre - s, ci + post + s
        if s0 < self._env.start or s1 > self._env.end or self._in_dropout(s0, s1):
            return math.nan
        seg = self._env.get(s0, s1)
        if t.env is None:
            lag = float(s)
        else:
            # correlate only the drop burst: P1/P2 move with the amplitude, P3 is the clock
            b0 = pre - self.tpl_burst
            kern = self._align_kernel
            burst = np.convolve(t.env, kern, mode="same")[b0:]
            bz = burst - burst.mean()
            nb = float(np.sqrt(np.dot(bz, bz)))
            region = np.convolve(seg, kern, mode="same")[b0 : b0 + burst.size + 2 * s]
            cc = np.correlate(region, bz, mode="valid")
            i = int(np.argmax(cc))
            w = region[i : i + burst.size]
            wz = w - w.mean()
            den = nb * float(np.sqrt(np.dot(wz, wz)))
            warming = len(t.segs) < c.template_min_beats  # median of a few beats: accept all
            if den <= _TINY or (not warming and cc[i] / den < c.template_min_corr):
                return math.nan
            d = 0.0
            if 0 < i < cc.size - 1:
                q = cc[i - 1] - 2.0 * cc[i] + cc[i + 1]
                d = 0.5 * (cc[i - 1] - cc[i + 1]) / q if q < 0 else 0.0
            lag = i + d
        origin = s0 + lag  # absolute index of the template's first sample on this beat
        li = int(round(lag))
        t.segs.append(seg[li : li + pre + post].copy())
        t.off.append(origin + pre - tp)
        t.pending += 1
        full = len(t.segs) == c.template_beats
        if t.env is None or not full or t.pending >= c.template_refresh_beats:
            t.env = np.median(np.stack(t.segs), axis=0)
            t.onset_p3, t.onset_p1 = self._template_onsets(t.env)
            t.onset_front = self._front_onset(t.env)
            t.pending = 0
        if len(t.segs) < c.template_min_beats or math.isnan(t.onset_p3):
            return math.nan
        # the live onset, not a frozen one: the template frame drifts slowly on real takes
        # (a frozen reference ended ~0.4 ms off on the Miyota and broke the beat error);
        # the live onset follows the physical point
        return origin + t.onset_p3

    def _front_onset(self, tm: NDArray) -> float:
        """P3 onset on a template as the foot of its first steep front (NaN if none).

        Arrival-time picking: the drop is an impact, so its sound starts with a steep front.
        The earliest stretch whose slope reaches ``template_front_fraction`` of the steepest
        slope within ``template_front_lift_fraction`` x t_lift before the drop maximum is the
        front; the onset is where the tangent at its steepest point meets the level just
        before it. A slow ramp before the drop does not qualify as a front.
        """
        c = self.config
        ms = self.fs / 1000.0
        m0 = max(0, self.tpl_pre - int(round(1.5 * ms)))
        mx = m0 + int(np.argmax(tm[m0:]))
        ref = self._tlift_ref()
        back_ms = 2.0 * c.template_burst_ms
        if ref is not None:
            back_ms = min(back_ms, max(1.5, c.template_front_lift_fraction * ref / ms))
        h = max(1, int(round(0.1 * ms)))  # slope over +-0.1 ms
        lo = max(h, mx - int(round(back_ms * ms)))
        mx = min(mx, tm.size - 1 - h)
        if mx - lo < 2:
            return math.nan
        g = (tm[lo + h : mx + 1 + h] - tm[lo - h : mx + 1 - h]) / (2 * h)
        gmax = float(np.max(g))
        if gmax <= 0.0:
            return math.nan
        steep = g >= c.template_front_fraction * gmax
        s = int(np.argmax(steep))
        e = s
        while e + 1 < g.size and steep[e + 1]:
            e += 1
        i = lo + s + int(np.argmax(g[s : e + 1]))
        p0 = max(0, i - int(round(1.0 * ms)))
        floor = float(np.min(tm[p0 : i + 1]))
        slope = float(g[i - lo])
        return max(float(p0), i - (float(tm[i]) - floor) / slope)

    def _template_onsets(self, tm: NDArray) -> tuple[float, float]:
        """(P3 onset, P1 onset) on a template, in template indices (NaN if not found)."""
        c = self.config
        ms = self.fs / 1000.0
        pre = self.tpl_pre
        m0 = max(0, pre - int(round(1.5 * ms)))
        mx = m0 + int(np.argmax(tm[m0:]))
        burst = int(round(c.template_burst_ms * ms))
        b0 = max(0, mx - burst)  # where the first lobe is searched
        bb = max(0, mx - 2 * burst)  # where the burst starts from (baseline, low crossing)
        base = float(np.min(tm[bb : mx + 1]))
        height = float(tm[mx]) - base
        # first lobe: earliest point of the burst window at template_lobe_fraction of the max
        first = b0 + int(np.argmax(tm[b0 : mx + 1] >= base + c.template_lobe_fraction * height))
        level = base + c.template_p3_fraction * height
        below = np.flatnonzero(tm[bb:first] < level)
        if below.size == 0:
            return math.nan, math.nan
        j = bb + int(below[-1])
        d = float(tm[j + 1] - tm[j])
        p3 = j + ((level - float(tm[j])) / d if d > 0 else 0.0)
        # P1: first rise above the template noise inside the physical lift-time window
        n0 = max(2, int(round(1.0 * ms)))
        quiet = tm[:n0]
        b1 = float(np.median(quiet))
        spread = 1.4826 * float(np.median(np.abs(quiet - b1)))
        thr = b1 + max(c.template_p1_fraction * (float(tm[mx]) - b1), 6.0 * spread)
        lo = max(n0, int(math.floor(p3 - self.tmax)))
        hi = int(math.floor(p3 - self.tmin))
        if hi <= lo:
            return p3, math.nan
        above = np.flatnonzero(tm[lo:hi] > thr)
        if above.size == 0 or above[0] == 0:
            # nothing rises above the noise, or the window already starts above it (no clean
            # rise to time): P1 is not resolvable on this template
            return p3, math.nan
        j = lo + int(above[0]) - 1
        d = float(tm[j + 1] - tm[j])
        p1 = j + ((thr - float(tm[j])) / d if d > 0 else 0.0)
        return p3, p1

    def _is_p1(self, a: float, x: float, back_onsets: NDArray) -> bool:
        """Decide whether the earliest sub-event ``x`` before the anchor ``a`` is P1 or P2."""
        c = self.config
        if self._tlift_ref() is not None:
            return True  # window already narrowed around the running t_lift
        tl = a - x
        mid = x + _P2_FRACTION * tl
        if np.any(np.abs(back_onsets - mid) <= 0.2 * tl):
            return True  # three-part structure P1 .. P2 .. P3
        lo, hi = c.amplitude_range_deg
        a1 = amplitude_from_lift_time(tl / self.fs, c.lift_angle_deg, c.bph)
        a2 = amplitude_from_lift_time(tl / (1.0 - _P2_FRACTION) / self.fs, c.lift_angle_deg, c.bph)
        v1 = lo <= a1 <= hi
        v2 = lo <= a2 <= hi
        if v1 != v2:
            return v1
        if not v1:
            return True
        t_lo, t_hi = c.typical_amplitude_deg

        def dist(amp: float) -> float:
            return max(0.0, t_lo - amp, amp - t_hi)

        return dist(a1) <= dist(a2)

    def _confidence(
        self, snr: float, p1: float, p2: float, p3: float, residual: float, flags: BeatFlags
    ) -> float:
        c = self.config
        q_snr = 0.2 + 0.8 * min(1.0, max(0.0, (snr - c.min_anchor_snr_db) / 18.0))
        q_struct = (
            0.4
            + 0.3 * (not math.isnan(p3))
            + 0.2 * (not math.isnan(p1))
            + 0.1 * (not math.isnan(p2))
        )
        sigma = self._tol() / 3.0
        q_res = math.exp(-0.5 * (residual / sigma) ** 2)
        q = q_snr * q_struct * q_res
        if flags & BeatFlags.TIMING_OUTLIER:
            q *= 0.5
        if flags & BeatFlags.SATURATED:
            q *= 0.8
        return float(min(1.0, max(0.0, q)))
