"""Noise-adaptive interference filter (numpy only): notches what the room adds to the watch.

A watch beat is a broadband click a few milliseconds long; between two beats the microphone
only hears the room. Whatever stands out of the noise floor *between beats* is therefore not
the watch: the whine of a switching power supply or a monitor (one or two narrow lines), the
harmonics of the mains that a dimmer or a charger pushes into the kHz range, a fan. A fixed
band-pass cannot remove them, because they sit in the same band as the beat.

``InterferenceFilter`` measures the spectrum of the quiet moments and attenuates only the
frequencies whose noise stands above the typical level of the band:

1. **Noise spectrum.** Power spectra of Hann frames of ``nfft`` samples every ``nfft / 2``,
   on a fixed grid of absolute sample indices. Per frequency, the ``percentile`` (25 %) over
   the frames of the last ``history_s`` seconds: beats occupy a minority of the frames, so a
   low percentile is the spectrum of the silences, whatever the beat rate.
2. **Gain.** ``reference`` = median of that spectrum inside the analysis band. A bin whose
   noise exceeds ``excess_db`` above the reference is brought down to that limit,
   ``g = sqrt(limit / noise)``, never by more than ``max_attenuation_db``; every other bin
   keeps ``g = 1``. The bins examined are the band and the skirt just below it (down to 0.7 of
   its lower edge, where the band-pass still lets a strong line through; their noise is
   weighed by the attenuation of the band-pass there, i.e. judged as the analysis sees it);
   the rest is left alone (the band-pass removes it).
   With a flat noise floor every gain is exactly 1 and the signal is returned untouched.
3. **Filter.** The gains become a linear-phase FIR of ``nfft`` taps (inverse FFT, centred,
   Hann-tapered) applied with its delay compensated, so the instants of the beats do not
   move. The taper bounds its response to +-``nfft / 2`` samples.

It only ever attenuates, it has no memory other than the noise frames, and the gains of a
stretch depend only on frames that ended before it: a caller that works on a fixed grid (the
beat detector) gets the same output whatever the chunking of the input.

Measured on the real takes with interference added: a tone as loud as the beats made every
take unreadable and 7 of 8 are now measured within 0.2 s/d of the clean take; the figures are
in ``BeatDetectorConfig.interference_filter``. Cost: the detector goes from ~90x to ~80x
real time.
"""

from __future__ import annotations

import math
from collections import deque

import numpy as np
from numpy.typing import NDArray

__all__ = ["InterferenceFilter"]

_TINY = 1e-30


class InterferenceFilter:
    """Adaptive notch of stationary interference (see the module docstring).

    Args:
        sample_rate: Sample rate in Hz.
        band_hz: Analysis band; only its bins are examined and attenuated.
        nfft: Frame and filter length in samples (a power of two).
        history_s: Seconds of frames the noise spectrum is taken from.
        percentile: Percentile over the frames, per frequency (0-100).
        excess_db: Noise above the band reference that is tolerated.
        max_attenuation_db: Largest attenuation of a bin.
        min_frames: Frames needed before anything is attenuated.
        skirt: The bins examined start at this fraction of the lower band edge.
        skirt_order: Butterworth order of the band-pass edge the skirt bins are judged
            through (its attenuation, applied twice, weighs their noise).
        widen_bins: Each notch is widened by this many bins on both sides (the taper of the
            filter smears a notch; one bin more keeps its depth).
    """

    def __init__(
        self,
        sample_rate: float,
        band_hz: tuple[float, float],
        *,
        nfft: int = 2048,
        history_s: float = 2.0,
        percentile: float = 25.0,
        excess_db: float = 6.0,
        max_attenuation_db: float = 40.0,
        min_frames: int = 16,
        skirt: float = 0.7,
        skirt_order: int = 4,
        widen_bins: int = 1,
    ) -> None:
        if nfft < 64 or nfft & (nfft - 1):
            raise ValueError(f"nfft debe ser una potencia de 2 >= 64 (recibido {nfft})")
        if not 0.0 <= percentile <= 100.0:
            raise ValueError(f"percentile fuera de [0, 100]: {percentile}")
        if not (history_s > 0 and excess_db >= 0 and max_attenuation_db > 0 and min_frames >= 2):
            raise ValueError("parámetros del filtro de interferencias no válidos")
        self.fs = float(sample_rate)
        self.nfft = int(nfft)
        self.hop = self.nfft // 2
        self.half = self.nfft // 2
        self.history = int(round(history_s * self.fs))
        self.percentile = float(percentile)
        self.min_frames = int(min_frames)
        self._excess = 10.0 ** (excess_db / 10.0)  # power ratio
        self._floor = 10.0 ** (-max_attenuation_db / 20.0)  # amplitude ratio
        f = np.fft.rfftfreq(self.nfft, 1.0 / self.fs)
        self._band = (f >= band_hz[0]) & (f <= band_hz[1])  # where the reference is taken
        # bins examined: the band and the skirt below it (a strong line there still leaks
        # through the band-pass: mains harmonics at 700-1000 Hz, 20 dB above the beats)
        self._watch = (f >= skirt * band_hz[0]) & (f <= band_hz[1])
        # below the band the analysis sees the noise through the high-pass side of its
        # band-pass (zero phase: the magnitude counts twice), so that is how it is judged
        # here: the rumble of a quiet room in the skirt is not interference
        fw = np.maximum(f[self._watch], 1e-9)
        self._weight = np.where(
            fw < band_hz[0], 1.0 / (1.0 + (band_hz[0] / fw) ** (2 * skirt_order)) ** 2, 1.0
        )
        self._widen = int(widen_bins)
        self._win = np.hanning(self.nfft)
        self._taper = np.hanning(self.nfft + 1)[: self.nfft]  # peak at nfft / 2
        self._frames: deque[tuple[int, NDArray[np.float64]]] = deque()  # (end index, PSD)
        self._next = 0  # index of the next frame of the grid
        self.active_bins = 0  # bins attenuated by the last gains (diagnostics)
        self.max_attenuation_db = 0.0  # largest attenuation of the last gains (diagnostics)
        self.peak_hz = math.nan  # frequency of the strongest line of the last gains
        self.peak_excess_db = 0.0  # its level over the band reference
        self._freqs = f

    def reset(self, start: int) -> None:
        """Forget the noise frames; the next frame starts at or after ``start``."""
        self._frames.clear()
        self._next = -(-int(start) // self.hop)  # ceil
        self.active_bins = 0
        self.max_attenuation_db = 0.0

    def next_frame(self) -> tuple[int, int]:
        """``[start, end)`` of the next frame of the grid (absolute indices)."""
        a = self._next * self.hop
        return a, a + self.nfft

    def skip_frame(self) -> None:
        """Leave out the next frame of the grid (its samples are not available)."""
        self._next += 1

    def add_frame(self, samples: NDArray[np.float64]) -> None:
        """Add the next frame of the grid (``nfft`` samples from ``next_frame()[0]``).

        A frame of digital silence (all zeros: a data gap) is skipped, it is not noise.
        """
        if samples.size != self.nfft:
            raise ValueError(f"se esperaban {self.nfft} muestras, llegaron {samples.size}")
        end = self._next * self.hop + self.nfft
        self._next += 1
        spec = np.fft.rfft(samples * self._win)
        psd = spec.real**2 + spec.imag**2
        if float(psd[self._band].sum()) <= 0.0:
            return
        self._frames.append((end, psd))

    def gains(self, at: int) -> NDArray[np.float64] | None:
        """Gains per FFT bin for audio from ``at`` on, or None when nothing is attenuated.

        Only frames that ended at or before ``at`` and started within ``history_s`` count.
        """
        while self._frames and self._frames[0][0] - self.nfft < at - self.history:
            self._frames.popleft()
        rows = [p for end, p in self._frames if end <= at]
        self.active_bins = 0
        self.max_attenuation_db = 0.0
        self.peak_hz, self.peak_excess_db = math.nan, 0.0
        if len(rows) < self.min_frames:
            return None
        watch = self._watch
        # percentile over the frames by partial sorting (np.percentile was half the cost)
        block = np.stack(rows)[:, watch]
        k = min(len(rows) - 1, int(round(self.percentile / 100.0 * (len(rows) - 1))))
        noise = np.partition(block, k, axis=0)[k] * self._weight
        ref = float(np.median(noise[self._band[watch]]))
        if not ref > 0.0:
            return None
        limit = self._excess * ref
        over = noise > limit
        n_over = int(np.count_nonzero(over))
        if n_over == 0:
            return None
        gb = np.ones(noise.size)
        gb[over] = np.maximum(np.sqrt(limit / np.maximum(noise[over], _TINY)), self._floor)
        g = np.ones(watch.size)
        g[watch] = gb
        for _ in range(self._widen):  # each notch one bin wider on both sides
            g[1:-1] = np.minimum(g[1:-1], np.minimum(g[:-2], g[2:]))
        self.active_bins = n_over
        self.max_attenuation_db = -20.0 * math.log10(float(gb.min()))
        top = int(np.argmax(noise))
        self.peak_hz = float(self._freqs[watch][top])
        self.peak_excess_db = 10.0 * math.log10(float(noise[top]) / ref)
        return g

    def apply(self, x: NDArray[np.float64], gains: NDArray[np.float64]) -> NDArray[np.float64]:
        """Filter ``x`` with ``gains`` (zero delay, same length).

        The first and last ``nfft / 2`` samples see zeros beyond the ends of ``x``: give it
        that much margin on both sides of the part that matters.
        """
        n = x.size
        if n == 0:
            return x.copy()
        h = np.fft.fftshift(np.fft.irfft(gains, self.nfft)) * self._taper
        size = 1 << (n + self.nfft - 2).bit_length()
        y = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(h, size), size)
        return y[self.half : self.half + n]
