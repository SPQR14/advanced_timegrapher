"""Lock-free single-producer ring buffer of mono samples with absolute indexing.

Every sample ever written gets an *absolute index*: the first sample written after
construction (or ``reset()``) is index 0, the next one is 1, and so on. Later layers refer to
instants as absolute sample indices, i.e. in the time domain of the ADC clock, and only use
host timestamps (``block_log``) to relate that clock to the host clock.

Threading contract (SPSC, no locks):

* Exactly ONE producer thread calls ``write()`` (normally the PortAudio callback). ``write()``
  never blocks, never logs, never raises for lack of space (old samples are overwritten) and
  only allocates small Python objects.
* Any number of reader threads may call ``read()``, ``read_latest()``, ``block_log()`` and
  ``stats()``. Readers never block the producer.
* The producer first announces the range it is about to write (``_reserved``), then copies
  the samples, then publishes ``_total`` as its LAST step. Readers only trust indices
  ``< total_samples``. After copying, ``read()`` re-checks the reservation: if the producer
  started overwriting the requested range during the copy, ``SamplesOverwritten`` is raised
  instead of returning corrupted data.
* ``reset()`` must only be called while the producer is stopped.

The block log entry of the newest block may become visible to ``block_log()`` slightly
before ``total_samples`` includes it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, DTypeLike, NDArray

__all__ = [
    "SampleStream",
    "SamplesNotYetAvailable",
    "SamplesOverwritten",
    "StreamStats",
]

_READ_LATEST_RETRIES = 8


class SamplesOverwritten(RuntimeError):
    """The requested samples are no longer in the ring buffer (too old or overwritten)."""


class SamplesNotYetAvailable(ValueError):
    """The requested range ends beyond the newest published sample."""


@dataclass(frozen=True, slots=True)
class StreamStats:
    """Snapshot of the ingestion counters of a ``SampleStream``.

    Attributes:
        total_samples: Absolute index one past the newest sample.
        blocks: Number of blocks written since construction or ``reset()``.
        audio_s: ``total_samples / sample_rate`` in seconds.
        ingest_ratio: Samples received after the first block divided by the samples the
            nominal sample rate predicts for the host time elapsed between the first and the
            last block. 1.0 until at least two blocks have been written.
        max_gap_ms: Largest host-time gap between two consecutive blocks, in milliseconds.
        max_write_us: Largest cost of a single ``write()`` call, in microseconds.
    """

    total_samples: int
    blocks: int
    audio_s: float
    ingest_ratio: float
    max_gap_ms: float
    max_write_us: float


class SampleStream:
    """Ring buffer of float32 mono samples addressed by absolute sample index.

    See the module docstring for the threading contract.

    Args:
        capacity: Number of samples kept in memory.
        sample_rate: Nominal sample rate in Hz (used for ``stats()``).
        block_log_size: Number of ``(end_index, host_ns)`` block records kept.

    Raises:
        ValueError: If any argument is not positive.
    """

    def __init__(self, capacity: int, sample_rate: float, *, block_log_size: int = 8192) -> None:
        if int(capacity) <= 0:
            raise ValueError(f"capacity must be positive, got {capacity}")
        if not float(sample_rate) > 0.0:
            raise ValueError(f"sample_rate must be positive, got {sample_rate}")
        if int(block_log_size) <= 0:
            raise ValueError(f"block_log_size must be positive, got {block_log_size}")
        self._capacity = int(capacity)
        self._sample_rate = float(sample_rate)
        self._log_size = int(block_log_size)
        self._buf = np.zeros(self._capacity, dtype=np.float32)
        self._log_end = np.zeros(self._log_size, dtype=np.int64)
        self._log_ns = np.zeros(self._log_size, dtype=np.int64)
        self._init_counters()

    def _init_counters(self) -> None:
        self._total = 0
        self._reserved = 0
        self._blocks = 0
        self._first_ns = 0
        self._first_block_n = 0
        self._last_ns = 0
        self._max_gap_ns = 0
        self._max_write_ns = 0

    # ------------------------------------------------------------------ properties
    @property
    def capacity(self) -> int:
        """Number of samples the ring buffer keeps."""
        return self._capacity

    @property
    def sample_rate(self) -> float:
        """Nominal sample rate in Hz."""
        return self._sample_rate

    @property
    def total_samples(self) -> int:
        """Absolute index one past the newest published sample."""
        return self._total

    @property
    def oldest_available(self) -> int:
        """Absolute index of the oldest sample still in the buffer."""
        return max(0, self._total - self._capacity)

    # ------------------------------------------------------------------ producer
    def reset(self) -> None:
        """Forget all samples and counters. Only call it while the producer is stopped."""
        self._buf.fill(0.0)
        self._log_end.fill(0)
        self._log_ns.fill(0)
        self._init_counters()

    def write(self, block: ArrayLike, host_ns: int | None = None) -> None:
        """Append a block of samples (single producer thread only).

        If the block is larger than the capacity only its tail is stored, but
        ``total_samples`` still advances by the full block length.

        Args:
            block: 1-D samples (any real dtype; stored as float32).
            host_ns: Host timestamp of the block in ns; defaults to ``time.perf_counter_ns()``.
        """
        t0 = time.perf_counter_ns()
        if host_ns is None:
            host_ns = t0
        x = np.asarray(block)
        if x.ndim != 1:
            x = x.reshape(-1)
        n = x.shape[0]
        if n == 0:
            return
        cap = self._capacity
        total = self._total
        new_total = total + n
        if n > cap:
            x = x[n - cap :]
        m = x.shape[0]

        # Announce the range being written before touching the buffer (see read()).
        self._reserved = new_total
        start = (new_total - m) % cap
        first = min(m, cap - start)
        self._buf[start : start + first] = x[:first]
        if first < m:
            self._buf[: m - first] = x[first:]

        k = self._blocks % self._log_size
        self._log_end[k] = new_total
        self._log_ns[k] = host_ns
        if self._blocks == 0:
            self._first_ns = host_ns
            self._first_block_n = n
        else:
            gap = host_ns - self._last_ns
            if gap > self._max_gap_ns:
                self._max_gap_ns = gap
        self._last_ns = host_ns
        self._blocks += 1

        cost = time.perf_counter_ns() - t0
        if cost > self._max_write_ns:
            self._max_write_ns = cost
        # Publish last: readers only trust indices < _total.
        self._total = new_total

    # ------------------------------------------------------------------ readers
    def read(self, start: int, stop: int, dtype: DTypeLike = np.float64) -> NDArray:
        """Copy the samples ``[start, stop)`` (absolute indices).

        Args:
            start: First absolute index (inclusive).
            stop: Last absolute index (exclusive).
            dtype: Output dtype.

        Returns:
            A new array of length ``stop - start``.

        Raises:
            ValueError: If ``start < 0`` or ``stop < start``.
            SamplesNotYetAvailable: If ``stop > total_samples``.
            SamplesOverwritten: If the range is no longer in the buffer, or the producer
                overwrote it while it was being copied.
        """
        start = int(start)
        stop = int(stop)
        if start < 0 or stop < start:
            raise ValueError(f"invalid range [{start}, {stop})")
        if stop == start:
            return np.empty(0, dtype=dtype)
        cap = self._capacity
        total = self._total
        if stop > total:
            raise SamplesNotYetAvailable(f"range ends at {stop} but only {total} samples exist")
        if total - start > cap:
            raise SamplesOverwritten(f"sample {start} is older than {total - cap} (overwritten)")

        n = stop - start
        out = np.empty(n, dtype=dtype)
        i0 = start % cap
        first = min(n, cap - i0)
        out[:first] = self._buf[i0 : i0 + first]
        if first < n:
            out[first:] = self._buf[: n - first]

        # Re-check AFTER copying: the producer may have wrapped around during the copy.
        if self._reserved - start > cap:
            raise SamplesOverwritten(f"samples from {start} were overwritten during the read")
        return out

    def read_latest(self, n: int, dtype: DTypeLike = np.float64) -> tuple[int, NDArray]:
        """Copy the newest ``n`` samples (at most ``capacity // 2``).

        Args:
            n: Number of samples requested.
            dtype: Output dtype.

        Returns:
            ``(start_index, samples)``; fewer than ``n`` samples while the stream is young.

        Raises:
            ValueError: If ``n`` is negative.
            SamplesOverwritten: If the producer lapped the reader on every retry.
        """
        n = int(n)
        if n < 0:
            raise ValueError(f"n must be >= 0, got {n}")
        n = min(n, self._capacity // 2)
        for _ in range(_READ_LATEST_RETRIES):
            total = self._total
            start = max(0, total - n)
            try:
                return start, self.read(start, total, dtype)
            except SamplesOverwritten:
                continue
        raise SamplesOverwritten(f"producer lapped the reader {_READ_LATEST_RETRIES} times")

    def block_log(self, last: int | None = None) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
        """Return the recorded blocks, oldest first.

        Args:
            last: Return at most this many of the newest records (``None`` = all kept).

        Returns:
            ``(end_indices, host_ns)`` as int64 arrays. Each end index is the absolute index
            one past the last sample of its block.
        """
        size = self._log_size
        blocks = self._blocks
        avail = min(blocks, size)
        if last is not None:
            avail = max(0, min(avail, int(last)))
        idx = (np.arange(blocks - avail, blocks, dtype=np.int64)) % size
        ends = self._log_end[idx]
        ns = self._log_ns[idx]
        # Drop records the producer may have recycled while we were copying.
        lapped = (self._blocks - blocks) - (size - avail)
        if lapped > 0:
            ends = ends[lapped:]
            ns = ns[lapped:]
        return ends, ns

    def stats(self) -> StreamStats:
        """Return a snapshot of the ingestion counters (see ``StreamStats``)."""
        total = self._total
        blocks = self._blocks
        first_ns = self._first_ns
        last_ns = self._last_ns
        ratio = 1.0
        if blocks >= 2 and last_ns > first_ns:
            expected = self._sample_rate * (last_ns - first_ns) * 1e-9
            ratio = (total - self._first_block_n) / expected
        return StreamStats(
            total_samples=total,
            blocks=blocks,
            audio_s=total / self._sample_rate,
            ingest_ratio=ratio,
            max_gap_ms=self._max_gap_ns / 1e6,
            max_write_us=self._max_write_ns / 1e3,
        )
