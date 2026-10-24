"""GIL-wait statistics shared by the in-process and the isolated capture (stdlib only).

The wait is estimated as ``stream.time - time_info.currentTime``: the PortAudio stream clock
read as the very first thing in the Python callback minus the timestamp PortAudio stamps right
before invoking the native callback (see ``adv_tg.audio.capture``).

This module must stay importable without Qt, logging setup or ``sounddevice``: the isolated
capture child process uses it.
"""

from __future__ import annotations

import array
import math
from bisect import bisect_right
from collections.abc import Sequence
from typing import Any

__all__ = ["GIL_WAIT_EDGES_MS", "GIL_WAIT_EDGES_US", "GilWaitRecorder", "hist_percentile_ms"]

#: Upper edges (ms) of the GIL-wait histogram buckets; the last bucket is ``>= 500 ms``.
GIL_WAIT_EDGES_MS: tuple[float, ...] = (
    0.1,
    0.5,
    1.0,
    2.0,
    5.0,
    10.0,
    20.0,
    50.0,
    100.0,
    250.0,
    500.0,
)
GIL_WAIT_EDGES_US: tuple[float, ...] = tuple(1000.0 * e for e in GIL_WAIT_EDGES_MS)


def hist_percentile_ms(hist: Sequence[int], q: float) -> float:
    """Upper edge (ms) of the histogram bucket that contains quantile ``q``.

    Args:
        hist: Counts per bucket, ``len(GIL_WAIT_EDGES_MS) + 1`` entries.
        q: Quantile in [0, 1].

    Returns:
        The bucket's upper edge in ms, ``math.inf`` for the overflow bucket, or 0.0 when the
        histogram is empty.
    """
    total = sum(hist)
    if total == 0:
        return 0.0
    target = q * total
    acc = 0
    for i, count in enumerate(hist):
        acc += count
        if acc >= target and count:
            return GIL_WAIT_EDGES_MS[i] if i < len(GIL_WAIT_EDGES_MS) else math.inf
    return math.inf


class GilWaitRecorder:
    """Callback-safe GIL-wait counters (no logging, no locks, preallocated histogram).

    Args:
        sample_rate: Nominal sample rate, used to count waits longer than one block.
    """

    __slots__ = ("sample_rate", "n", "sum_us", "max_us", "hist", "over_block", "invalid")

    def __init__(self, sample_rate: float) -> None:
        self.sample_rate = float(sample_rate)
        self.n = 0
        self.sum_us = 0.0
        self.max_us = 0.0
        self.hist = array.array("q", [0] * (len(GIL_WAIT_EDGES_MS) + 1))
        self.over_block = 0
        self.invalid = 0

    def record(self, pa_now: float, current: float, frames: int) -> None:
        """Record one callback (call it from the audio callback)."""
        if not pa_now or not current:
            self.invalid += 1
            return
        wait_us = (pa_now - current) * 1e6
        if wait_us < 0.0:
            wait_us = 0.0
        self.n += 1
        self.sum_us += wait_us
        if wait_us > self.max_us:
            self.max_us = wait_us
        self.hist[bisect_right(GIL_WAIT_EDGES_US, wait_us)] += 1
        if frames > 0 and wait_us > frames * 1e6 / self.sample_rate:
            self.over_block += 1

    def snapshot(self) -> dict[str, Any]:
        """Plain picklable summary (call it outside the callback)."""
        return {
            "gil_wait_n": self.n,
            "gil_wait_mean_us": self.sum_us / self.n if self.n else 0.0,
            "gil_wait_max_us": self.max_us,
            "gil_wait_hist": list(self.hist),
            "gil_wait_over_block": self.over_block,
            "gil_wait_invalid": self.invalid,
        }
