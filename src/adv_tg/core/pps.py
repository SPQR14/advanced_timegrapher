"""GPS PPS reference: pulse detector and ADC clock calibration (layer 4, level 3).

Pure library: no Qt, no audio. ``PpsDetector`` finds the pulses of a GPS PPS (one per second)
in the samples of the sound card, chunk by chunk; ``PpsCalibrator`` turns their instants into
an ``AdcDrift`` while the capture runs; ``calibrate_samples`` does both on a whole recording.

Signal chain it was designed for: PPS pin of a GPS module -> NPN transistor -> active buzzer
(~2.7 kHz) -> the same microphone used for watches. An electrical PPS on a line input works
the same way (the onset is the edge).

Reference recording: ``docs/samples/pps_48000Hz.flac`` (macOS, CM18 at 48 kHz, 150 pulses,
bursts of ~110 ms peaking at -55 dBFS, 28 dB SNR). The design choices below were first made on
an earlier, louder recording (40 dB SNR, clipped bursts) that is no longer in the repository.

Detector:

* Low-pass at ``LOWPASS_HZ`` (linear-phase FIR, streaming; its delay is subtracted, so the
  instants stay in input samples). At low recording levels the noise is mostly above 15 kHz
  (dither / noise shaping of the card); without it the first lobe sinks into that noise and
  the onset jumps 0.2-0.5 ms LATE on a fraction of the pulses (one-sided, so it biases the
  slope: 27 of 149 pulses and 12 µs on the reference; with it, 0 of 150 and 7.1 µs). On the
  louder recording it changed nothing. Only a low-pass: a high-pass (1 kHz) lifted a pre-pulse
  click there.
* Coarse trigger: ``|x - dc| >= TRIGGER_SIGMA`` noise sigmas. The noise sigma and the DC
  level are medians of the MAD / median of 100 ms blocks at absolute positions, the last 2 s
  of blocks FINISHED before the sample being searched, so they ignore the bursts (<15 % of the
  time) and the result does not depend on how the audio is chunked.
* Fine onset: baseline (median and MAD) over ``BASELINE_MS`` ending ``BASELINE_GAP_MS``
  before the trigger, then walk BACK from the trigger to the last stretch of ``QUIET_MS``
  below ``THRESHOLD_SIGMA`` local sigmas; the onset is the first crossing after it, with
  linear sub-sample interpolation.
* Why the first departure from silence and not the burst: the amplitude build-up of an active
  buzzer varies from pulse to pulse (its oscillator start-up), its first cycles do not.
  Residual timing scatter on the louder recording (std after a linear fit):

  ======================================================  ===========
  generic quartz ``BeatDetector`` at 3600 BPH (t_fine)     195 µs, lost lock 3 times
  fixed threshold on |x| at 25 % / 50 % of the plateau     100 / 137 µs (half-cycle jumps)
  smoothed envelope (0.2-1.5 ms) at 10-50 %                 26-155 µs
  waveform / envelope cross-correlation                    66-155 µs
  band-pass 1-10 kHz first (lifts a pre-pulse click)       79-308 µs
  this detector, 6-8 sigma                                 8.8 µs
  this detector after the 9 kHz low-pass                   7.5 µs
  ======================================================  ===========

  On the reference: 6.6-7.5 µs for any threshold from 5 to 12 sigma. Clipping does not
  matter: only the first cycles, well below full scale, are used.
* The buzzer must sound freely: damped with clay its start-up stopped being repeatable (whole
  bursts moving by about one cycle, ~300 µs).

Calibrator:

* Pulse numbering against the last accepted pulse (``rint(dt / 1 s)``), so missing pulses are
  fine. A pulse that misses the predicted phase by more than ``JUMP_MS`` is not accepted; if
  ``JUMP_CONFIRM`` such pulses in a row agree with each other, the phase has JUMPED and a new
  segment starts; isolated ones are stray clicks and are dropped.
* Phase jumps happen when the GPS gets its fix (before it, the module free-runs on its own
  oscillator: on the louder recording the first 18 pulses were 230 ms off the rest) and when
  the capture loses samples (overflow). Only the LAST segment is fitted: an earlier one may come from an
  unlocked GPS.
* The fit is ``drift_from_ticks`` (line ``t_k = a + P k``, robust). Its sigma assumes white
  timing noise; batch means of the residuals (blocks of ``BATCH_PULSES``) widen it by the
  measured excess (x3 on the reference: the card clock wanders ~0.2 ppm per minute while it
  warms up; let it run 10-15 min before calibrating).
* ``ready``: at least ``READY_SPAN_S`` and a total uncertainty below ``READY_SIGMA_PPM``.
  Reference: +7.56 ± 0.14 ppm at 30 s, +7.59 ± 0.05 at 60 s, +7.47 ± 0.06 at 120 s,
  +7.44 ± 0.04 at 149 s (the card runs fast: uncorrected watches read 0.64 s/d slow).
* The error belongs to the card AND the computer: a USB card can take its clock from the
  host (the same CM18 read +22 ppm on Linux, +24 on Windows and +7.4 on macOS, where 44.1
  and 48 kHz agreed). Calibrate on each computer.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field, replace

import numpy as np
from numpy.typing import ArrayLike, NDArray

from adv_tg.core.adc_clock import AdcDrift, ClockLevel, drift_from_ticks
from adv_tg.dsp.onset import MAD_TO_SIGMA, lowpass_fir, onset_after_quiet, robust_level

__all__ = [
    "BATCH_PULSES",
    "JUMP_MS",
    "LOWPASS_HZ",
    "READY_SIGMA_PPM",
    "READY_SPAN_S",
    "REFERENCE_SIGMA_PPM",
    "THRESHOLD_SIGMA",
    "PpsCalibrator",
    "PpsDetector",
    "PpsEstimate",
    "PpsPulse",
    "calibrate_samples",
]

#: Fine onset threshold, in local noise sigmas (on the louder recording 6-8 gave 8.8 µs, 5
#: caught noise and 10+ skipped the first lobe; the reference takes 5-12).
THRESHOLD_SIGMA = 8.0
#: Low-pass before the detector (Hz); 7-12 kHz give the same result on both recordings.
#: Skipped when it is above 0.45 fs.
LOWPASS_HZ = 9000.0
#: Coarse trigger, in noise sigmas.
TRIGGER_SIGMA = 30.0
#: Quiet stretch that must precede the onset (ms).
QUIET_MS = 0.5
#: Local baseline: length and gap before the trigger (ms).
BASELINE_MS = 60.0
BASELINE_GAP_MS = 10.0
#: The burst must reach this SNR (dB over the local noise) within ``CONFIRM_MS``.
MIN_SNR_DB = 26.0
CONFIRM_MS = 20.0
#: No new pulse within this time after a pulse (bursts last 110-130 ms).
HOLDOFF_S = 0.2
#: Noise tracker: block length and history (s).
NOISE_BLOCK_S = 0.1
NOISE_HISTORY_S = 2.0

#: A pulse further than this from the predicted phase is not accepted (ms).
JUMP_MS = 2.0
#: Consistent off-phase pulses in a row that make a phase jump.
JUMP_CONFIRM = 3
#: Pulses in a segment before an estimate is given.
MIN_PULSES = 10
#: Batch length for the excess-scatter factor (pulses) and minimum batches.
BATCH_PULSES = 30
MIN_BATCHES = 4
#: Declared error of the reference: the GPS is exact to 1e-12; this floor covers slow changes
#: of the acoustic delay (buzzer, temperature) that the residuals cannot show.
REFERENCE_SIGMA_PPM = 0.01
#: When the live estimate counts as good enough to save.
READY_SPAN_S = 120.0
READY_SIGMA_PPM = 0.05

_TINY = 1e-12


@dataclass(frozen=True, slots=True)
class PpsPulse:
    """One detected pulse.

    Attributes:
        index: Onset in absolute samples (sub-sample).
        snr_db: Burst peak over the local noise, in dB.
        peak: Peak ``|x - dc|`` of the burst (full scale = 1).
    """

    index: float
    snr_db: float
    peak: float


class PpsDetector:
    """Streaming PPS pulse detector (see module docstring).

    Args:
        sample_rate: Sample rate in Hz.
        threshold_sigma: Fine onset threshold in local noise sigmas.
    """

    def __init__(self, sample_rate: float, threshold_sigma: float = THRESHOLD_SIGMA) -> None:
        if not sample_rate > 0:
            raise ValueError("sample_rate debe ser positivo")
        if not threshold_sigma > 0:
            raise ValueError("threshold_sigma debe ser positivo")
        self.fs = float(sample_rate)
        self.threshold_sigma = float(threshold_sigma)
        ms = self.fs / 1000.0
        self._quiet = max(2, int(round(QUIET_MS * ms)))
        self._base = int(round(BASELINE_MS * ms))
        self._gap = int(round(BASELINE_GAP_MS * ms))
        self._confirm = int(round(CONFIRM_MS * ms))
        self._holdoff = int(round(HOLDOFF_S * self.fs))
        self._block = int(round(NOISE_BLOCK_S * self.fs))
        self._min_ratio = 10.0 ** (MIN_SNR_DB / 20.0)
        self._history = self._gap + self._base + self._quiet + 2
        self._n_hist = max(1, int(round(NOISE_HISTORY_S / NOISE_BLOCK_S)))
        self._fir = lowpass_fir(self.fs, LOWPASS_HZ)
        self._delay = (self._fir.size - 1) // 2
        self.reset(0)

    def reset(self, start_index: int) -> None:
        """Forget everything; the next chunk starts at ``start_index`` (after a gap)."""
        self._in_next = int(start_index)
        self._tail: NDArray[np.float64] | None = None  # last fir.size - 1 input samples
        # the detector works on the filtered stream, already moved back by the FIR delay
        start = self._in_next - self._delay
        self._buf = np.empty(0)
        self._buf_start = start
        self._scan = start
        # (end, dc, sigma) of each finished block; pruned in _trim
        self._blocks: deque[tuple[int, float, float]] = deque()
        self._next_block = (start // self._block + 1) * self._block
        self.rejected = 0

    # ------------------------------------------------------------------ noise
    def _update_noise(self) -> None:
        end = self._buf_start + self._buf.size
        while self._next_block <= end:
            a = self._next_block - self._block
            if a >= self._buf_start:
                b = self._buf[a - self._buf_start : self._next_block - self._buf_start]
                med = float(np.median(b))
                sg = MAD_TO_SIGMA * float(np.median(np.abs(b - med)))
                self._blocks.append((self._next_block, med, sg))
            self._next_block += self._block

    def _noise(self, at: int) -> tuple[float, float] | None:
        """DC and sigma from the blocks finished at ``at`` (the last ``NOISE_HISTORY_S``)."""
        done = [b for b in self._blocks if b[0] <= at][-self._n_hist :]
        if not done:
            return None
        arr = np.asarray(done)
        return float(np.median(arr[:, 1])), max(float(np.median(arr[:, 2])), _TINY)

    # ------------------------------------------------------------------ public
    def process(self, chunk: ArrayLike, start_index: int) -> list[PpsPulse]:
        """Feed the next contiguous chunk (``start_index`` = absolute index of ``chunk[0]``).

        A chunk that does not follow the previous one resets the detector first.
        """
        x = np.asarray(chunk, dtype=np.float64).reshape(-1)
        if int(start_index) != self._in_next:
            self.reset(int(start_index))
        if x.size == 0:
            return []
        self._in_next += x.size
        y = self._lowpass(x)  # always a new array
        self._buf = np.concatenate((self._buf, y)) if self._buf.size else y
        self._update_noise()
        out: list[PpsPulse] = []
        end = self._buf_start + self._buf.size
        while (p := self._next_pulse(end)) is not None:
            out.append(p)
        self._trim()
        return out

    def _lowpass(self, x: NDArray[np.float64]) -> NDArray[np.float64]:
        """Causal FIR over the stream; output i belongs to input i - delay."""
        n = self._fir.size - 1
        if n == 0:
            return x.copy()
        if self._tail is None:  # start of the stream: hold the first sample (no step)
            self._tail = np.full(n, x[0])
        ext = np.concatenate((self._tail, x))
        self._tail = ext[-n:].copy()
        # row by row, not np.convolve: its rounding depends on the chunk length and the
        # result must not depend on how the audio is chunked
        win = np.lib.stride_tricks.sliding_window_view(ext, self._fir.size)
        return np.sum(win * self._fir[::-1], axis=1)

    # ------------------------------------------------------------------ detection
    def _trim(self) -> None:
        end = self._buf_start + self._buf.size
        # keep the baseline history of the next trigger and the noise block still open
        keep_from = min(min(self._scan, end) - self._history, self._next_block - self._block)
        cut = keep_from - self._buf_start
        if cut > 0:
            self._buf = self._buf[cut:]
            self._buf_start += cut
        while len(self._blocks) > self._n_hist and self._blocks[self._n_hist][0] <= self._scan:
            self._blocks.popleft()

    def _next_pulse(self, end: int) -> PpsPulse | None:
        while True:
            if self._scan >= end:
                return None
            noise = self._noise(self._scan)
            if noise is None:  # start of the stream: wait for the first block
                if not self._blocks:
                    return None
                self._scan = self._blocks[0][0]
                continue
            dc, sigma = noise
            # search only up to the next block end: past it the noise estimate changes, and
            # this keeps the result independent of the chunking
            stop = min(end, (self._scan // self._block + 1) * self._block)
            a = self._scan - self._buf_start
            seg = self._buf[a : stop - self._buf_start]
            hit = np.flatnonzero(np.abs(seg - dc) >= TRIGGER_SIGMA * sigma)
            if hit.size == 0:
                self._scan = stop
                continue
            trig = self._scan + int(hit[0])
            if trig + self._confirm > end:  # wait for the rest of the burst
                self._scan = trig
                return None
            pulse = self._measure(trig)
            if pulse is None:
                self.rejected += 1
                self._scan = trig + self._confirm
                continue
            self._scan = trig + self._holdoff
            return pulse

    def _measure(self, trig: int) -> PpsPulse | None:
        b0 = trig - self._gap - self._base
        if b0 < self._buf_start:  # no clean history before it (start of the stream)
            return None
        o = self._buf_start
        mu, sg = robust_level(self._buf[b0 - o : trig - self._gap - o])
        peak = float(np.max(np.abs(self._buf[trig - o : trig + self._confirm - o] - mu)))
        if peak < self._min_ratio * sg:
            return None
        # walk back from the trigger to the last quiet stretch
        lo = trig - self._gap
        r = np.abs(self._buf[lo - o : trig + 1 - o] - mu)
        # None: nothing crosses (local noise far above the tracked one) or not quiet before
        j = onset_after_quiet(r, self.threshold_sigma * sg, self._quiet)
        if j is None:
            return None
        return PpsPulse(float(lo + j), 20.0 * math.log10(peak / sg), peak)


@dataclass(frozen=True, slots=True)
class PpsEstimate:
    """State of a PPS calibration.

    Attributes:
        drift: The ADC error (``ClockLevel.UNCALIBRATED`` with a note while there is too little
            data). ``method`` is ``"pps"``; ``sigma_ppm`` includes the excess-scatter factor.
        pulses: Pulses detected since the start.
        used: Pulses of the current segment.
        missing: Pulses missing inside the current segment.
        jumps: Phase jumps seen (each restarts the estimate).
        rejected: Pulses dropped as stray clicks.
        span_s: Time covered by the current segment (s).
        jitter_us: Robust sigma of the timing residuals (µs).
        excess: Excess-scatter factor applied to the sigma (>= 1).
        ready: Long and precise enough to save (``READY_SPAN_S``, ``READY_SIGMA_PPM``).
        t_s: Time of each pulse of the segment from its first one (s), for plots.
        residual_us: Timing residual of each pulse against the fit (µs), for plots.
    """

    drift: AdcDrift
    pulses: int = 0
    used: int = 0
    missing: int = 0
    jumps: int = 0
    rejected: int = 0
    span_s: float = 0.0
    jitter_us: float = math.nan
    excess: float = 1.0
    ready: bool = False
    t_s: NDArray[np.float64] = field(default_factory=lambda: np.empty(0))
    residual_us: NDArray[np.float64] = field(default_factory=lambda: np.empty(0))

    @property
    def valid(self) -> bool:
        """True once there is an estimate (enough pulses in the current segment)."""
        return self.drift.level == ClockLevel.EXTERNAL


class PpsCalibrator:
    """Accumulates PPS instants and estimates the ADC error (see module docstring).

    Args:
        sample_rate: Nominal sample rate in Hz.
        nominal_period_s: Period of the reference (1 s for a PPS).
        reference_sigma_ppm: Declared error of the reference (systematic part).
    """

    def __init__(
        self,
        sample_rate: float,
        nominal_period_s: float = 1.0,
        reference_sigma_ppm: float = REFERENCE_SIGMA_PPM,
    ) -> None:
        if not (sample_rate > 0 and nominal_period_s > 0):
            raise ValueError("sample_rate y nominal_period_s deben ser positivos")
        self.fs = float(sample_rate)
        self.period = float(nominal_period_s) * self.fs  # samples
        self.reference_sigma_ppm = float(reference_sigma_ppm)
        self._jump = JUMP_MS * 1e-3 * self.fs
        self.reset()

    def reset(self) -> None:
        self._seg_t: list[float] = []
        self._seg_k: list[int] = []
        self._cand_t: list[float] = []
        self._cand_k: list[int] = []
        self.pulses = 0
        self.jumps = 0
        self.rejected = 0

    def _predict_err(self, t: float, t_ref: float, k_drift: float) -> tuple[int, float]:
        n = int(round((t - t_ref) / self.period))
        return n, (t - t_ref) - n * (self.period + k_drift)

    def _drift_per_pulse(self) -> float:
        """Current period excess in samples (``P_measured - P_nominal``)."""
        if len(self._seg_t) < 2:
            return 0.0
        dk = self._seg_k[-1] - self._seg_k[0]
        return (self._seg_t[-1] - self._seg_t[0]) / dk - self.period if dk else 0.0

    def add(self, indices: ArrayLike) -> None:
        """Add pulse instants (absolute samples, increasing)."""
        for t in np.asarray(indices, dtype=np.float64).reshape(-1).tolist():
            if not math.isfinite(t):
                continue
            self.pulses += 1
            if not self._seg_t:
                self._seg_t, self._seg_k = [t], [0]
                continue
            n, err = self._predict_err(t, self._seg_t[-1], self._drift_per_pulse())
            if n >= 1 and abs(err) <= self._jump:
                self._seg_t.append(t)
                self._seg_k.append(self._seg_k[-1] + n)
                self.rejected += len(self._cand_t)
                self._cand_t, self._cand_k = [], []
                continue
            # off phase: a stray click, or the first pulses after a phase jump
            if self._cand_t:
                m, e2 = self._predict_err(t, self._cand_t[-1], 0.0)
                if m >= 1 and abs(e2) <= self._jump:
                    self._cand_t.append(t)
                    self._cand_k.append(self._cand_k[-1] + m)
                else:
                    self.rejected += len(self._cand_t)
                    self._cand_t, self._cand_k = [t], [0]
            else:
                self._cand_t, self._cand_k = [t], [0]
            if len(self._cand_t) >= JUMP_CONFIRM:
                if len(self._seg_t) < JUMP_CONFIRM:  # the "segment" was a stray click
                    self.rejected += len(self._seg_t)
                else:
                    self.jumps += 1
                self._seg_t, self._seg_k = self._cand_t, self._cand_k
                self._cand_t, self._cand_k = [], []

    def estimate(self) -> PpsEstimate:
        """Fit the current segment (cheap enough to call a few times per second)."""
        t = np.asarray(self._seg_t, dtype=np.float64)
        k = np.asarray(self._seg_k, dtype=np.float64)
        common = dict(pulses=self.pulses, jumps=self.jumps, rejected=self.rejected)
        if t.size < MIN_PULSES:
            note = f"pocos pulsos ({t.size} < {MIN_PULSES})"
            return PpsEstimate(AdcDrift.uncalibrated(note), used=int(t.size), **common)  # type: ignore[arg-type]
        nominal_s = self.period / self.fs
        drift = drift_from_ticks(
            t, self.fs, nominal_period_s=nominal_s, reference_sigma_ppm=self.reference_sigma_ppm,
            min_ticks=MIN_PULSES,
        )  # fmt: skip
        span_s = float(k[-1] - k[0]) * nominal_s
        missing = int(k[-1] - k[0] + 1 - k.size)
        # residuals of a plain line, for the plot and the excess factor
        kc = k - k.mean()
        slope = float(np.dot(kc, t - t.mean()) / max(float(np.dot(kc, kc)), _TINY))
        res = (t - t.mean() - slope * kc) / self.fs * 1e6
        med = float(np.median(res))
        jitter = MAD_TO_SIGMA * float(np.median(np.abs(res - med)))
        excess = _batch_excess(res, max(float(np.std(res, ddof=2)), _TINY))
        if drift.level != ClockLevel.EXTERNAL:
            return PpsEstimate(
                drift, used=int(t.size), missing=missing, span_s=span_s, jitter_us=jitter,
                t_s=(k - k[0]) * nominal_s, residual_us=res, **common,  # type: ignore[arg-type]
            )  # fmt: skip
        drift = replace(drift, method="pps", sigma_ppm=drift.sigma_ppm * excess)
        ready = span_s >= READY_SPAN_S and drift.uncertainty_ppm <= READY_SIGMA_PPM
        return PpsEstimate(
            drift, used=int(t.size), missing=missing, span_s=span_s, jitter_us=jitter,
            excess=excess, ready=ready, t_s=(k - k[0]) * nominal_s, residual_us=res,
            **common,  # type: ignore[arg-type]
        )  # fmt: skip


def _batch_excess(res: NDArray[np.float64], sigma: float) -> float:
    """sqrt(observed / expected variance of batch means), never below 1.

    The residuals come from a line fitted to them, which removes 2 degrees of freedom from
    the batch means: their variance is taken with ``B - 2``.
    """
    b = res.size // BATCH_PULSES
    if b < MIN_BATCHES:
        return 1.0
    means = res[: b * BATCH_PULSES].reshape(b, BATCH_PULSES).mean(axis=1)
    observed = float(np.sum((means - means.mean()) ** 2)) / (b - 2)
    expected = sigma**2 / BATCH_PULSES
    return max(1.0, math.sqrt(observed / expected))


def calibrate_samples(
    samples: ArrayLike, sample_rate: float, *, chunk_s: float = 1.0
) -> PpsEstimate:
    """Detect the PPS pulses of a whole recording and estimate the ADC error."""
    x = np.asarray(samples, dtype=np.float64)
    if x.ndim > 1:
        x = x[:, 0]
    det = PpsDetector(sample_rate)
    cal = PpsCalibrator(sample_rate)
    step = max(1, int(chunk_s * sample_rate))
    for a in range(0, x.size, step):
        cal.add([p.index for p in det.process(x[a : a + step], a)])
    return cal.estimate()
