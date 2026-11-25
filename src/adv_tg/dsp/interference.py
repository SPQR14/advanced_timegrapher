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

import ctypes
import math

import numpy as np
from numpy.typing import NDArray

from adv_tg import native_bridge as _nb

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
        self._watch = (f >= min(skirt, 1.0) * band_hz[0]) & (f <= band_hz[1])
        self._band_in_watch = self._band[self._watch]
        self._watch_freqs = f[self._watch]
        # below the band the analysis sees the noise through the high-pass side of its
        # band-pass (zero phase: the magnitude counts twice), so that is how it is judged
        # here: the rumble of a quiet room in the skirt is not interference
        fw = np.maximum(self._watch_freqs, 1e-9)
        self._weight = np.where(
            fw < band_hz[0], 1.0 / (1.0 + (band_hz[0] / fw) ** (2 * skirt_order)) ** 2, 1.0
        )
        self._widen = int(widen_bins)
        self._win = np.hanning(self.nfft)
        self._taper = np.hanning(self.nfft + 1)[: self.nfft]  # peak at nfft / 2
        # Preallocated 2-D ring buffer storing only the watched bins (eliminates per-cell
        # np.stack copies of full 1025-bin spectra).
        n_watch = int(np.count_nonzero(self._watch))
        self._cap = max(self.min_frames + 8, int(math.ceil(self.history / self.hop)) + 32)
        self._buf = np.empty((self._cap, n_watch), dtype=np.float64)
        self._ends = np.empty(self._cap, dtype=np.int64)
        self._scratch = np.empty((self._cap, n_watch), dtype=np.float64)
        self._head = 0
        self._count = 0
        self._next = 0  # index of the next frame of the grid
        self.active_bins = 0  # bins attenuated by the last gains (diagnostics)
        self.max_attenuation_db = 0.0  # largest attenuation of the last gains (diagnostics)
        self.peak_hz = math.nan  # frequency of the strongest line of the last gains
        self.peak_excess_db = 0.0  # its level over the band reference
        self._freqs = f
        self._band_hz = (float(band_hz[0]), float(band_hz[1]))
        self._skirt = float(skirt)
        self._skirt_order = int(skirt_order)
        self._excess_db = float(excess_db)
        self._max_att_db = float(max_attenuation_db)
        self._history_s = float(history_s)
        self._handle: int | None = None
        if _nb.have(
            "advtg_interf_create",
            "advtg_interf_destroy",
            "advtg_interf_reset",
            "advtg_interf_next_frame",
            "advtg_interf_skip_frame",
            "advtg_interf_add_frame",
            "advtg_interf_add_frames",
            "advtg_interf_gains",
            "advtg_interf_apply",
        ):
            h = _nb.lib().advtg_interf_create(
                self.fs,
                float(band_hz[0]),
                float(band_hz[1]),
                self.nfft,
                self._history_s,
                self.percentile,
                self._excess_db,
                self._max_att_db,
                self.min_frames,
                self._skirt,
                self._skirt_order,
                self._widen,
            )
            if h:
                self._handle = int(h)

    def __del__(self) -> None:
        h = getattr(self, "_handle", None)
        if h and _nb is not None:
            cdll = _nb.lib()
            if cdll is not None and hasattr(cdll, "advtg_interf_destroy"):
                cdll.advtg_interf_destroy(ctypes.c_void_p(h))
            self._handle = None

    def reset(self, start: int) -> None:
        """Forget the noise frames; the next frame starts at or after ``start``."""
        if self._handle:
            _nb.lib().advtg_interf_reset(ctypes.c_void_p(self._handle), int(start))
            self.active_bins = 0
            self.max_attenuation_db = 0.0
            self.peak_hz = math.nan
            self.peak_excess_db = 0.0
            return
        self._head = 0
        self._count = 0
        self._next = -(-int(start) // self.hop)  # ceil
        self.active_bins = 0
        self.max_attenuation_db = 0.0

    def next_frame(self) -> tuple[int, int]:
        """``[start, end)`` of the next frame of the grid (absolute indices)."""
        if self._handle:
            a = ctypes.c_int64()
            b = ctypes.c_int64()
            _nb.lib().advtg_interf_next_frame(
                ctypes.c_void_p(self._handle), ctypes.byref(a), ctypes.byref(b)
            )
            return int(a.value), int(b.value)
        a = self._next * self.hop
        return a, a + self.nfft

    def skip_frame(self) -> None:
        """Leave out the next frame of the grid (its samples are not available)."""
        if self._handle:
            _nb.lib().advtg_interf_skip_frame(ctypes.c_void_p(self._handle))
            return
        self._next += 1

    def _grow_ring(self, min_cap: int) -> None:
        new_cap = max(2 * self._cap, min_cap)
        n_watch = self._buf.shape[1]
        new_buf = np.empty((new_cap, n_watch), dtype=np.float64)
        new_ends = np.empty(new_cap, dtype=np.int64)
        if self._count:
            h, c, cap = self._head, self._count, self._cap
            first = min(c, cap - h)
            new_buf[:first] = self._buf[h : h + first]
            new_ends[:first] = self._ends[h : h + first]
            if first < c:
                new_buf[first:c] = self._buf[: c - first]
                new_ends[first:c] = self._ends[: c - first]
        self._buf = new_buf
        self._ends = new_ends
        self._scratch = np.empty((new_cap, n_watch), dtype=np.float64)
        self._cap = new_cap
        self._head = 0

    def _push_psd(self, end: int, psd_w: NDArray[np.float64]) -> None:
        if self._count == self._cap:
            self._grow_ring(self._cap + 1)
        slot = (self._head + self._count) % self._cap
        self._buf[slot] = psd_w
        self._ends[slot] = end
        self._count += 1

    def add_frame(self, samples: NDArray[np.float64]) -> None:
        """Add the next frame of the grid (``nfft`` samples from ``next_frame()[0]``).

        A frame of digital silence (all zeros: a data gap) is skipped, it is not noise.
        """
        if samples.size != self.nfft:
            raise ValueError(f"se esperaban {self.nfft} muestras, llegaron {samples.size}")
        if self._handle:
            sc = _nb.f64(samples)
            rc = _nb.lib().advtg_interf_add_frame(
                ctypes.c_void_p(self._handle), _nb.ptr(sc), sc.size
            )
            if rc != 0:
                raise ValueError(f"se esperaban {self.nfft} muestras, llegaron {samples.size}")
            return
        end = self._next * self.hop + self.nfft
        self._next += 1
        spec_w = np.fft.rfft(samples * self._win)[self._watch]
        psd_w = spec_w.real**2 + spec_w.imag**2
        if float(psd_w[self._band_in_watch].sum()) <= 0.0:
            return
        self._push_psd(end, psd_w)

    def add_frames(self, buf: NDArray[np.float64], buf_start: int, up_to: int) -> None:
        """Add all pending grid frames ending at or before ``up_to`` in one batched 2-D rfft."""
        if self._handle:
            bc = _nb.f64(buf)
            _nb.lib().advtg_interf_add_frames(
                ctypes.c_void_p(self._handle), _nb.ptr(bc), bc.size, int(buf_start), int(up_to)
            )
            return
        hop, nfft = self.hop, self.nfft
        buf_end = buf_start + buf.size
        # Skip grid frames whose start fell before the available buffer
        if self._next * hop < buf_start:
            self._next = -(-buf_start // hop)
        first_idx = self._next
        last_start = min(up_to, buf_end) - nfft
        if first_idx * hop > last_start:
            return
        n_frames = (last_start - first_idx * hop) // hop + 1
        if n_frames == 1:
            fa = first_idx * hop - buf_start
            self.add_frame(buf[fa : fa + nfft])
            return
        fa0 = first_idx * hop - buf_start
        span = (n_frames - 1) * hop + nfft
        frames = np.lib.stride_tricks.sliding_window_view(buf[fa0 : fa0 + span], nfft)[::hop]
        spec_w = np.fft.rfft(frames * self._win, axis=1)[:, self._watch]
        psd_w = spec_w.real**2 + spec_w.imag**2
        band_sum = psd_w[:, self._band_in_watch].sum(axis=1)
        for i in range(n_frames):
            end = (first_idx + i) * hop + nfft
            if float(band_sum[i]) > 0.0:
                self._push_psd(end, psd_w[i])
        self._next = first_idx + n_frames

    def gains(self, at: int) -> NDArray[np.float64] | None:
        """Gains per FFT bin for audio from ``at`` on, or None when nothing is attenuated.

        Only frames that ended at or before ``at`` and started within ``history_s`` count.
        """
        if self._handle:
            g = np.empty(self.half + 1, dtype=np.float64)
            active_bins = ctypes.c_int()
            max_att = ctypes.c_double()
            peak_hz = ctypes.c_double()
            peak_excess = ctypes.c_double()
            rc = _nb.lib().advtg_interf_gains(
                ctypes.c_void_p(self._handle),
                int(at),
                _nb.ptr(g),
                ctypes.byref(active_bins),
                ctypes.byref(max_att),
                ctypes.byref(peak_hz),
                ctypes.byref(peak_excess),
            )
            self.active_bins = int(active_bins.value)
            self.max_attenuation_db = float(max_att.value)
            self.peak_hz = float(peak_hz.value)
            self.peak_excess_db = float(peak_excess.value)
            return g if rc == 1 else None
        cutoff = at - self.history + self.nfft
        cap = self._cap
        while self._count and int(self._ends[self._head]) < cutoff:
            self._head = (self._head + 1) % cap
            self._count -= 1
        self.active_bins = 0
        self.max_attenuation_db = 0.0
        self.peak_hz, self.peak_excess_db = math.nan, 0.0
        c = self._count
        if c < self.min_frames:
            return None
        # Copy the contiguous ring slice(s) into _scratch (usually all c rows satisfy end <= at)
        h = self._head
        first = min(c, cap - h)
        if int(self._ends[(h + c - 1) % cap]) <= at:
            m = c
            self._scratch[:first] = self._buf[h : h + first]
            if first < c:
                self._scratch[first:c] = self._buf[: c - first]
        else:
            m = 0
            for j in range(c):
                slot = (h + j) % cap
                if int(self._ends[slot]) <= at:
                    self._scratch[m] = self._buf[slot]
                    m += 1
        if m < self.min_frames:
            return None
        watch = self._watch
        block = self._scratch[:m]
        k = min(m - 1, int(round(self.percentile / 100.0 * (m - 1))))
        block.partition(k, axis=0)
        noise = block[k] * self._weight
        ref = float(np.median(noise[self._band_in_watch]))
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
        self.peak_hz = float(self._watch_freqs[top])
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
        if self._handle:
            xc = _nb.f64(x)
            gc = _nb.f64(gains)
            y = np.empty(n, dtype=np.float64)
            _nb.lib().advtg_interf_apply(
                ctypes.c_void_p(self._handle), _nb.ptr(xc), n, _nb.ptr(gc), _nb.ptr(y)
            )
            return y
        h = np.fft.fftshift(np.fft.irfft(gains, self.nfft)) * self._taper
        size = 1 << (n + self.nfft - 2).bit_length()
        y = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(h, size), size)
        return y[self.half : self.half + n]
