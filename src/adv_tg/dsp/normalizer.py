"""Software level normalization of the analysed audio (optional, off by default).

For inputs whose gain cannot be lowered (USB microphones with a hardware AGC or a minimum
capture gain of 0 dB, ALSA ``hw:`` devices that bypass the PipeWire volume...). The gain
brings the peaks to ``target`` (-6 dBFS by default), so beats whose input reached full scale
are no longer flagged ``SATURATED`` and excluded from the figures. It cannot undo clipping:
the flat tops stay flat (the timing of the onsets survives it, the shape of the sound does
not), which is why the option is meant only for inputs that cannot be adjusted.

Design
------
* **Fixed blocks.** The gain is constant (or ramps linearly) over blocks of ``block_s``
  anchored to the absolute sample index (block ``m`` = ``[m B, (m+1) B)``), so the output does
  not depend on how the audio is chunked (the beat detector relies on that). A block is
  emitted once it is complete, which delays the analysis by at most ``block_s``.
* **Peak follower with hold.** Instant attack, held ``hold_s``, then an exponential release
  with time constant ``release_s``. With beats closer than ``hold_s`` the gain is constant in
  steady state, so the beat shape is not modulated.
* **No overshoot.** A block whose gain drops (attack) uses the new gain over the whole block;
  a block whose gain rises ramps from the previous gain up to the new one. Either way every
  output sample is ``<= target`` in magnitude.
* Runs of exact zeros stay exact zeros (the detector takes them for data gaps).
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

__all__ = ["AudioNormalizer", "NormalizerConfig"]


@dataclass(frozen=True)
class NormalizerConfig:
    """Parameters of ``AudioNormalizer``.

    Attributes:
        sample_rate: Sample rate in Hz.
        target: Peak level of the output, linear full scale (default 0.5 = -6 dBFS).
        block_s: Length of the gain blocks in seconds (default 0.1 s).
        hold_s: Time the peak is held before it starts to decay (default 3 s).
        release_s: Time constant of the decay after the hold (default 10 s).
        max_gain_db: Largest gain applied to quiet inputs (default +24 dB).
        clip_level: |x| at or above this counts as a clipped input sample (default 0.99).
    """

    sample_rate: float
    target: float = 0.5
    block_s: float = 0.1
    hold_s: float = 3.0
    release_s: float = 10.0
    max_gain_db: float = 24.0
    clip_level: float = 0.99

    def __post_init__(self) -> None:
        if not (math.isfinite(self.sample_rate) and self.sample_rate > 0):
            raise ValueError(f"sample_rate no válido: {self.sample_rate}")
        if not 0.0 < self.target < 1.0:
            raise ValueError(f"target fuera de (0, 1): {self.target}")
        for name in ("block_s", "release_s"):
            if not getattr(self, name) > 0:
                raise ValueError(f"{name} debe ser positivo (recibido {getattr(self, name)})")
        if not self.hold_s >= 0:
            raise ValueError(f"hold_s debe ser >= 0 (recibido {self.hold_s})")
        if not self.max_gain_db >= 0:
            raise ValueError(f"max_gain_db debe ser >= 0 (recibido {self.max_gain_db})")
        if not 0.0 < self.clip_level <= 1.0:
            raise ValueError(f"clip_level fuera de (0, 1]: {self.clip_level}")


class AudioNormalizer:
    """Incremental block normalizer (see module docstring). Not thread-safe.

    ``process`` takes contiguous chunks with their absolute start index and yields
    ``(start_index, samples)`` segments ready for the detector; ``flush`` yields the last,
    incomplete block. A jump forward in the start index (data gap) emits the pending partial
    block first, so the segments keep the gap.
    """

    def __init__(self, config: NormalizerConfig) -> None:
        self.config = config
        fs = float(config.sample_rate)
        self.block = max(1, int(round(config.block_s * fs)))
        self._hold_blocks = int(math.ceil(config.hold_s / (self.block / fs) - 1e-9))
        self._decay = math.exp(-(self.block / fs) / config.release_s)
        self._max_gain = 10.0 ** (config.max_gain_db / 20.0)
        self._floor = config.target / self._max_gain
        self.reset()

    def reset(self) -> None:
        """Forget the level history and any pending samples."""
        self._pending: list[NDArray[np.float64]] = []
        self._pending_n = 0
        self._pending_start: int | None = None
        self._follower = 0.0
        self._hold = 0
        self._gain: float | None = None
        self.clipped_samples = 0
        self.samples_in = 0

    # ------------------------------------------------------------------ properties
    @property
    def gain(self) -> float:
        """Gain of the last emitted sample (1.0 before any block)."""
        return 1.0 if self._gain is None else self._gain

    @property
    def gain_db(self) -> float:
        return 20.0 * math.log10(self.gain)

    @property
    def next_index(self) -> int | None:
        """Absolute index the next chunk must start at (None before the first chunk)."""
        if self._pending_start is None:
            return None
        return self._pending_start + self._pending_n

    # ------------------------------------------------------------------ core
    def _gain_for(self, peak: float) -> float:
        if peak >= self._follower:
            self._follower = peak
            self._hold = self._hold_blocks
        elif self._hold > 0:
            self._hold -= 1
        else:
            self._follower *= self._decay
        return self.config.target / max(self._follower, self._floor)

    def _emit(self, x: NDArray[np.float64]) -> NDArray[np.float64]:
        a = np.abs(x)
        peak = float(a.max()) if a.size else 0.0
        self.clipped_samples += int(np.count_nonzero(a >= self.config.clip_level))
        g_new = self._gain_for(peak)
        g_old = self._gain
        self._gain = g_new
        if g_old is None or g_new <= g_old:
            return x * g_new
        # rising gain: linear ramp ending at g_new (every value <= g_new <= target / peak)
        ramp = g_old + (g_new - g_old) * (np.arange(1, x.size + 1) / x.size)
        return x * ramp

    def _take_pending(self) -> tuple[int, NDArray[np.float64]] | None:
        if self._pending_n == 0 or self._pending_start is None:
            return None
        x = self._pending[0] if len(self._pending) == 1 else np.concatenate(self._pending)
        start = self._pending_start
        self._pending = []
        self._pending_start = start + self._pending_n
        self._pending_n = 0
        return start, self._emit(x)

    def process(self, chunk: ArrayLike, start_index: int) -> Iterator[tuple[int, NDArray]]:
        """Feed contiguous samples starting at absolute index ``start_index``.

        Yields:
            ``(start, samples)`` of every block completed by this chunk (plus the pending
            partial block when ``start_index`` jumps forward).

        Raises:
            ValueError: If the chunk is not 1-D or overlaps samples already received.
        """
        x = np.asarray(chunk, dtype=np.float64)
        if x.ndim != 1:
            raise ValueError(f"se esperaba audio mono 1-D, forma recibida {x.shape}")
        start = int(start_index)
        expected = self.next_index
        if expected is not None and start < expected:
            raise ValueError(f"el bloque empieza en {start} pero ya se recibió hasta {expected}")
        if expected is not None and start > expected:  # data gap: close the partial block
            seg = self._take_pending()
            if seg is not None:
                yield seg
        if self._pending_n == 0:
            self._pending_start = start
        if x.size == 0:
            return
        self.samples_in += x.size
        B = self.block
        pos = 0
        while pos < x.size:
            room = B - ((start + pos) % B)  # samples left in the current block
            take = min(room, x.size - pos)
            self._pending.append(x[pos : pos + take])
            self._pending_n += take
            pos += take
            if take == room:
                seg = self._take_pending()
                if seg is not None:
                    yield seg

    def flush(self) -> Iterator[tuple[int, NDArray]]:
        """Yield the pending incomplete block (end of the audio)."""
        seg = self._take_pending()
        if seg is not None:
            yield seg
