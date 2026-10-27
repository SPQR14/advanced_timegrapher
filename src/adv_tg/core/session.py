"""Layer 5 core: one live measurement session over a ``SampleStream`` (no Qt).

``MeasurementSession`` joins the layers for the GUI worker (and any other live consumer):

* layer 2, ``BeatDetector.advance(stream)``: the new beats since the previous update;
* layer 3, ``MetricsTracker``: rate, beat error and amplitude over the last ``span_s``;
* layer 4, ``ClockDriftTracker`` over the block stamps of the stream, plus the stored
  calibration of the device: the drift actually applied to the rate is the one with the
  smallest uncertainty.

``update(stream)`` is cheap (~2 ms detection + ~2.5 ms metrics per 0.25 s of audio on the
real takes) and returns a ``SessionSnapshot`` with everything the GUI shows.

Clock stamps. ``ClockLog.wall_ns`` (``time.time_ns``, disciplined by the OS through NTP,
level 2) is used when the capture provides it; otherwise ``host_ns`` (``perf_counter``, only
disciplined on Linux with NTP). A live estimate replaces the stored calibration only when it
is at least level 2 and its total uncertainty is smaller: a level 1 estimate only measures
the ratio of two uncalibrated crystals.

Detection reliability. ``k`` counts the beats the phase model predicted, including the ones
that were not found, so over the metrics window ``len(beats) / (k_last - k_first + 1)`` is the
fraction of beats detected; times ``1 - excluded_fraction`` (beats without ``t_fine`` or
saturated) it is the fraction that actually feeds the figures.

Display waveform. The oscilloscope gets the last usable beat, band-passed (same band as the
detector) for display only; the measurement never sees this filtered copy. The window has a
FIXED length for the session (``display_window``: from the longest lift time down to
``DISPLAY_MIN_AMPLITUDE_DEG`` plus ``display_pre_ms`` before the reference instant, and
``display_post_ms`` after it), so the plot never rescales from one beat to the next.

Paper tape (``SessionSnapshot.tape_*``). For each new beat with a fine instant: its time in
seconds and its offset in ms against a grid of nominal periods corrected by the applied clock
drift, ``-(t_fine - t0 - (k - k0) P_nom (1 + ppm 1e-6)) / fs`` (sign: a gaining watch goes
up). A healthy watch draws two straight rows (tic and tac): the slope is the rate
(1 s/d = 1/86.4 ms per second) and their distance the tic/tac offset.

Displayed figures (``SessionSnapshot.display``). In live mode a parity template can give a
wrong P1 onset for one or two refreshes (Miyota: tac amplitude 295° -> 127° -> 295° within
0.6 s), which moves the amplitude and, through the tic/tac mismatch factor, drops the
confidence from 0.55 to 0.07 for an instant; the batch mode only uses the final templates and
never sees it. The figures shown are therefore the median of the last ``DISPLAY_UPDATES``
updates (1.75 s at 4 updates/s; tic and tac amplitudes separately, then their mean), which
removes glitches of up to 3 consecutive updates and lags ~0.9 s. ``metrics`` stays raw.
A longer mismatch (4R36: ~1.3 s) still lowers the overall confidence, so the rate and the beat
error are judged by ``timing_confidence`` (without the tic/tac amplitude factor) and only the
amplitude by the overall ``confidence``.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field, replace

import numpy as np
from numpy.typing import NDArray

from adv_tg.core.adc_clock import (
    AdcDrift,
    ClockDriftTracker,
    ClockLevel,
    host_clock,
)
from adv_tg.core.beat_detector import BeatDetector, BeatDetectorConfig, DetectorStats
from adv_tg.core.beat_types import Beat, BeatFlags, beat_period_s, lift_time_s
from adv_tg.core.metrics import (
    DEFAULT_LIFT_ANGLE_DEG,
    Metrics,
    MetricsConfig,
    MetricsTracker,
    timing_confidence,
)
from adv_tg.core.sample_stream import SamplesOverwritten, SampleStream
from adv_tg.dsp.engine import sosfiltfilt
from adv_tg.dsp.filters import butter_bandpass_sos, sosfilt_zi

__all__ = ["MeasurementSession", "SessionConfig", "SessionSnapshot"]

#: Seconds between two fits of the live clock estimate (a fit is cheap but not free).
DRIFT_REFIT_S = 1.0
#: Beats whose SNR is averaged for the quality bar.
SNR_BEATS = 16
#: Updates in the median of the displayed figures (see module docstring).
DISPLAY_UPDATES = 7
#: Longer median for the displayed beat error (stable on a healthy watch; 5 s at 4/s): a
#: step of the P3 timing point inside the 30 s window raised it for many updates (CM18
#: 4R36: displayed maximum 1.89 -> 1.72 ms, H-50 1.85 -> 1.14 ms). docs/resultados_beta2.md.
BE_DISPLAY_UPDATES = 20
#: The oscilloscope window shows P1 for amplitudes down to this value.
DISPLAY_MIN_AMPLITUDE_DEG = 150.0


@dataclass(frozen=True)
class DisplayFigures:
    """Figures to show: median of the last ``DISPLAY_UPDATES`` updates (NaN = not available)."""

    rate_s_per_day: float = math.nan
    beat_error_ms: float = math.nan
    amplitude_deg: float = math.nan
    amplitude_tic_deg: float = math.nan
    amplitude_tac_deg: float = math.nan
    confidence: float = 0.0
    timing_confidence: float = 0.0


def _nanmedian(values: list[float]) -> float:
    v = [x for x in values if math.isfinite(x)]
    return float(np.median(v)) if v else math.nan


@dataclass(frozen=True)
class SessionConfig:
    """Parameters of a live session.

    Attributes:
        sample_rate: Nominal sample rate in Hz.
        bph: Nominal beats per hour.
        lift_angle_deg: Lift angle; ``None`` = unknown (default 52°, amplitude indicative).
        quartz: Quartz watch (rate only, one event per step).
        span_s: Window of the figures in seconds (sliding).
        window_s: Length of the rate windows inside the span (trend and plotting).
        calibration: Stored calibration of the device (already aged), or None.
        calibration_source: Human-readable origin of ``calibration`` (Spanish).
        live_clock: Estimate the drift from the block stamps during the session.
        display_pre_ms: Oscilloscope window before P1 (or before the anchor if no P1).
        display_post_ms: Oscilloscope window after the reference instant (P3).
    """

    sample_rate: float
    bph: float
    lift_angle_deg: float | None = None
    quartz: bool = False
    span_s: float = 30.0
    window_s: float = 10.0
    calibration: AdcDrift | None = None
    calibration_source: str = ""
    live_clock: bool = True
    display_pre_ms: float = 3.0
    display_post_ms: float = 10.0


@dataclass(frozen=True)
class SessionSnapshot:
    """Everything a live view needs after one ``update``.

    Attributes:
        metrics: Layer-3 figures over the last ``span_s`` (NaN while not available).
        new_beats: Beats detected by this update.
        beats_total: Beats detected since the session started.
        last_beat: Newest beat with a resolved reference instant, or None.
        waveform: Band-passed samples around ``last_beat`` (display only; may be empty).
        waveform_start: Absolute index of ``waveform[0]``.
        snr_db: Mean anchor SNR of the last ``SNR_BEATS`` beats (NaN before any beat).
        detection_ratio: Detected / predicted beats over the metrics window (NaN if none).
        usable_ratio: ``detection_ratio * (1 - excluded_fraction)``.
        saturated_beats: Beats flagged ``SATURATED`` in the metrics window.
        locked: The detector has a phase lock.
        drift: Drift applied to the rate (stored calibration, live estimate or none).
        drift_source: Human-readable origin of ``drift`` (Spanish).
        live_drift: Live estimate from the block stamps (level 0 while too short).
        audio_s: Seconds of audio processed.
        detector_warnings: Warnings of the detector (e.g. probable wrong BPH).
        display: Figures to show (median of the last updates, see module docstring).
        tape_time_s, tape_offset_ms, tape_parity: Paper tape points of the new beats (see
            module docstring): ADC time in s, offset in ms, parity of ``k`` (0 = tic).
    """

    metrics: Metrics
    new_beats: int
    beats_total: int
    last_beat: Beat | None
    waveform: NDArray[np.float64]
    waveform_start: float
    snr_db: float
    detection_ratio: float
    usable_ratio: float
    saturated_beats: int
    locked: bool
    drift: AdcDrift
    drift_source: str
    live_drift: AdcDrift
    audio_s: float
    detector_warnings: tuple[str, ...] = field(default_factory=tuple)
    display: DisplayFigures = field(default_factory=DisplayFigures)
    tape_time_s: NDArray[np.float64] = field(default_factory=lambda: np.zeros(0))
    tape_offset_ms: NDArray[np.float64] = field(default_factory=lambda: np.zeros(0))
    tape_parity: NDArray[np.int8] = field(default_factory=lambda: np.zeros(0, np.int8))


class MeasurementSession:
    """Detector + metrics + clock over one ``SampleStream`` (see module docstring).

    Not thread-safe: call ``update`` from a single consumer thread.
    """

    def __init__(self, config: SessionConfig) -> None:
        self.config = config
        fs = float(config.sample_rate)
        self._fs = fs
        self._detector = BeatDetector(
            BeatDetectorConfig(
                sample_rate=fs,
                bph=config.bph,
                lift_angle_deg=config.lift_angle_deg or DEFAULT_LIFT_ANGLE_DEG,
                quartz=config.quartz,
            )
        )
        self._metrics_config = MetricsConfig(
            sample_rate=fs,
            bph=config.bph,
            lift_angle_deg=config.lift_angle_deg,
            quartz=config.quartz,
            window_s=min(config.window_s, config.span_s),
        )
        self._tracker = MetricsTracker(self._metrics_config, span_s=config.span_s)
        self._clock: ClockDriftTracker | None = None
        self._clock_field = ""
        self._live = AdcDrift.uncalibrated("sesión demasiado corta")
        self._next_refit = 0.0
        self._applied: tuple[AdcDrift, str] = (AdcDrift.uncalibrated(), "sin calibrar")
        self._beats_total = 0
        self._recent_snr: list[float] = []
        self._last_beat: Beat | None = None
        self._history: deque[Metrics] = deque(maxlen=DISPLAY_UPDATES)
        self._be_history: deque[float] = deque(maxlen=BE_DISPLAY_UPDATES)
        self._tape_origin: tuple[int, float] | None = None  # (k0, t0) of the tape grid
        lo, hi = self._detector.config.band_hz
        self._sos = butter_bandpass_sos(4, (lo, hi), fs)
        self._zi = sosfilt_zi(self._sos)
        la = config.lift_angle_deg or DEFAULT_LIFT_ANGLE_DEG
        pre_s = (
            lift_time_s(DISPLAY_MIN_AMPLITUDE_DEG, la, config.bph) + config.display_pre_ms * 1e-3
        )
        self._display_pre = int(round(pre_s * fs))
        self._display_len = self._display_pre + int(round(config.display_post_ms * 1e-3 * fs))
        self._apply_drift()

    # ------------------------------------------------------------------ properties
    @property
    def detector_stats(self) -> DetectorStats:
        return self._detector.stats

    @property
    def display_window(self) -> tuple[int, int]:
        """``(samples before the reference instant, total samples)`` of the oscilloscope."""
        return self._display_pre, self._display_len

    @property
    def metrics_config(self) -> MetricsConfig:
        """Config currently used by the metrics (includes the applied drift)."""
        return self._tracker.config

    # ------------------------------------------------------------------ clock
    def _update_clock(self, stream: SampleStream, audio_s: float) -> None:
        if not self.config.live_clock:
            return
        log = stream.clock_log()
        if log.end_indices.size == 0:
            return
        if self._clock is None:
            use_wall = bool(log.wall_ns[-1] > 0)
            self._clock_field = "wall_ns" if use_wall else "host_ns"
            host = host_clock("time" if use_wall else "perf_counter")
            self._clock = ClockDriftTracker(self._fs, host=host)
        stamps = log.wall_ns if self._clock_field == "wall_ns" else log.host_ns
        self._clock.update(log.end_indices, stamps)
        if audio_s >= self._next_refit:
            self._next_refit = audio_s + DRIFT_REFIT_S
            self._live = self._clock.estimate()
            self._apply_drift()

    def _apply_drift(self) -> None:
        candidates: list[tuple[AdcDrift, str]] = []
        cal = self.config.calibration
        if cal is not None and cal.level != ClockLevel.UNCALIBRATED:
            candidates.append((cal, self.config.calibration_source or "calibración guardada"))
        live = self._live
        if live.level >= ClockLevel.HOST_DISCIPLINED and math.isfinite(live.uncertainty_ppm):
            candidates.append((live, f"en vivo ({self._clock_field}, {live.span_s:.0f} s)"))
        drift, source = (
            min(candidates, key=lambda c: c[0].uncertainty_ppm)
            if candidates
            else (AdcDrift.uncalibrated(), "sin calibrar")
        )
        self._applied = (drift, source)
        sigma = drift.uncertainty_ppm if drift.level != ClockLevel.UNCALIBRATED else 0.0
        self._tracker.config = replace(
            self._metrics_config, adc_drift_ppm=drift.ppm, adc_drift_sigma_ppm=sigma
        )

    # ------------------------------------------------------------------ waveform
    def _waveform(self, stream: SampleStream, beat: Beat) -> tuple[NDArray[np.float64], float]:
        fs = self._fs
        a = int(math.floor(beat.reference)) - self._display_pre
        b = a + self._display_len
        pad = int(0.005 * fs)  # filter edge margin, cropped afterwards
        lo, hi = a - pad, b + pad
        if lo < stream.oldest_available or hi > stream.total_samples or b <= a:
            return np.zeros(0), float(a)
        try:
            x = stream.read(lo, hi, np.float64)
        except SamplesOverwritten:
            return np.zeros(0), float(a)
        y = sosfiltfilt(self._sos, x, self._zi)
        return np.ascontiguousarray(y[pad : pad + (b - a)]), float(a)

    # ------------------------------------------------------------------ display
    def _display(self, metrics: Metrics) -> DisplayFigures:
        if not math.isfinite(metrics.rate_s_per_day):
            self._history.clear()  # no figures now: never show stale ones
            self._be_history.clear()
            return DisplayFigures()
        self._history.append(metrics)
        self._be_history.append(metrics.beat_error_ms)
        h = list(self._history)
        tic = _nanmedian([m.amplitude_tic_deg for m in h])
        tac = _nanmedian([m.amplitude_tac_deg for m in h])
        if math.isfinite(tic) and math.isfinite(tac):
            amp = 0.5 * (tic + tac)
        else:
            amp = _nanmedian([m.amplitude_deg for m in h])
        lo, hi = self._metrics_config.amplitude_range_deg
        if not (math.isfinite(amp) and lo <= amp <= hi):
            amp = math.nan
        return DisplayFigures(
            rate_s_per_day=_nanmedian([m.rate_s_per_day for m in h]),
            beat_error_ms=_nanmedian(list(self._be_history)),
            amplitude_deg=amp,
            amplitude_tic_deg=tic,
            amplitude_tac_deg=tac,
            confidence=float(np.median([m.confidence for m in h])),
            timing_confidence=float(
                np.median([timing_confidence(m, self._tracker.config) for m in h])
            ),
        )

    # ------------------------------------------------------------------ paper tape
    def _tape(self, beats: list[Beat]) -> tuple[NDArray, NDArray, NDArray]:
        good = [b for b in beats if math.isfinite(b.t_fine)]
        if not good:
            return np.zeros(0), np.zeros(0), np.zeros(0, np.int8)
        if self._tape_origin is None:
            self._tape_origin = (good[0].k, good[0].t_fine)
        k0, t0 = self._tape_origin
        period = self._metrics_config.nominal_period_samples * (1.0 + self._applied[0].ppm * 1e-6)
        k = np.array([b.k for b in good], dtype=np.float64)
        t = np.array([b.t_fine for b in good])
        offset_ms = -(t - t0 - (k - k0) * period) / self._fs * 1e3
        parity = (np.array([b.k for b in good]) & 1).astype(np.int8)
        return t / self._fs, offset_ms, parity

    # ------------------------------------------------------------------ update
    def update(self, stream: SampleStream) -> SessionSnapshot:
        """Process the audio that arrived since the previous call and recompute the figures."""
        beats = self._detector.advance(stream)
        audio_s = self._detector.stats.samples_processed / self._fs
        self._update_clock(stream, audio_s)
        self._tracker.add(beats, self._detector.templates())
        self._beats_total += len(beats)
        for b in beats:
            if math.isfinite(b.snr_db):
                self._recent_snr.append(b.snr_db)
            if math.isfinite(b.reference):
                self._last_beat = b
        del self._recent_snr[:-SNR_BEATS]
        metrics = self._tracker.result()
        tape = self._tape(beats)

        window = self._tracker.beats
        det_ratio = math.nan
        saturated = 0
        if window:
            span_k = window[-1].k - window[0].k + 1
            det_ratio = len(window) / span_k if span_k > 0 else math.nan
            saturated = sum(1 for b in window if b.flags & BeatFlags.SATURATED)
        usable = det_ratio * (1.0 - metrics.excluded_fraction) if window else math.nan

        wave, w0 = np.zeros(0), math.nan
        if beats and self._last_beat is not None:
            wave, w0 = self._waveform(stream, self._last_beat)
        stats = self._detector.stats
        drift, source = self._applied
        return SessionSnapshot(
            metrics=metrics,
            new_beats=len(beats),
            beats_total=self._beats_total,
            last_beat=self._last_beat,
            waveform=wave,
            waveform_start=w0,
            snr_db=float(np.mean(self._recent_snr)) if self._recent_snr else math.nan,
            detection_ratio=det_ratio,
            usable_ratio=usable,
            saturated_beats=saturated,
            locked=self._detector.locked,
            drift=drift,
            drift_source=source,
            live_drift=self._live,
            audio_s=audio_s,
            detector_warnings=stats.warnings,
            display=self._display(metrics),
            tape_time_s=tape[0],
            tape_offset_ms=tape[1],
            tape_parity=tape[2],
        )

    @property
    def nominal_period_s(self) -> float:
        return beat_period_s(self.config.bph)
