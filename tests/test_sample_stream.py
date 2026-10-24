"""Tests for SampleStream (pure numpy, no audio hardware)."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from adv_tg.core.sample_stream import (
    SamplesNotYetAvailable,
    SamplesOverwritten,
    SampleStream,
    StreamStats,
)

FS = 1000.0


def _ramp(start: int, stop: int) -> np.ndarray:
    return np.arange(start, stop, dtype=np.float32)


def test_invalid_arguments() -> None:
    with pytest.raises(ValueError):
        SampleStream(0, FS)
    with pytest.raises(ValueError):
        SampleStream(10, 0.0)
    with pytest.raises(ValueError):
        SampleStream(10, FS, block_log_size=0)


def test_simple_write_read() -> None:
    s = SampleStream(16, FS)
    assert (s.capacity, s.sample_rate, s.total_samples, s.oldest_available) == (16, FS, 0, 0)
    s.write(_ramp(0, 5))
    assert s.total_samples == 5
    out = s.read(1, 4)
    assert out.dtype == np.float64
    np.testing.assert_array_equal(out, [1, 2, 3])
    assert s.read(2, 2).size == 0
    assert s.read(0, 5, np.float32).dtype == np.float32


def test_write_converts_to_float32_and_accepts_strided_views() -> None:
    s = SampleStream(8, FS)
    two_ch = np.arange(8, dtype=np.float64).reshape(4, 2)
    s.write(two_ch[:, 0])
    np.testing.assert_array_equal(s.read(0, 4), [0, 2, 4, 6])
    s.write([])  # empty block is ignored
    assert s.total_samples == 4
    assert s.stats().blocks == 1


def test_wrap_around_read_crossing_edge() -> None:
    s = SampleStream(10, FS)
    for a in range(0, 23, 3):  # blocks of 3 -> crosses the edge several times
        s.write(_ramp(a, a + 3))
    assert s.total_samples == 24
    assert s.oldest_available == 14
    np.testing.assert_array_equal(s.read(14, 24), _ramp(14, 24))
    np.testing.assert_array_equal(s.read(18, 22), _ramp(18, 22))


def test_block_larger_than_capacity_keeps_tail() -> None:
    s = SampleStream(8, FS)
    s.write(_ramp(0, 3))
    s.write(_ramp(3, 23))
    assert s.total_samples == 23
    assert s.oldest_available == 15
    np.testing.assert_array_equal(s.read(15, 23), _ramp(15, 23))
    ends, _ = s.block_log()
    np.testing.assert_array_equal(ends, [3, 23])


def test_not_yet_available_and_invalid_ranges() -> None:
    s = SampleStream(8, FS)
    s.write(_ramp(0, 4))
    with pytest.raises(SamplesNotYetAvailable):
        s.read(2, 5)
    assert issubclass(SamplesNotYetAvailable, ValueError)
    with pytest.raises(ValueError):
        s.read(-1, 2)
    with pytest.raises(ValueError):
        s.read(3, 2)


def test_overwritten_range() -> None:
    s = SampleStream(8, FS)
    s.write(_ramp(0, 20))
    with pytest.raises(SamplesOverwritten):
        s.read(11, 15)
    assert issubclass(SamplesOverwritten, RuntimeError)
    np.testing.assert_array_equal(s.read(12, 20), _ramp(12, 20))


def test_read_latest_caps_to_half_capacity() -> None:
    s = SampleStream(10, FS)
    start, x = s.read_latest(4)
    assert start == 0 and x.size == 0
    s.write(_ramp(0, 3))
    start, x = s.read_latest(100)
    assert start == 0
    np.testing.assert_array_equal(x, _ramp(0, 3))
    s.write(_ramp(3, 30))
    start, x = s.read_latest(100, np.float32)
    assert start == 25 and x.dtype == np.float32
    np.testing.assert_array_equal(x, _ramp(25, 30))
    with pytest.raises(ValueError):
        s.read_latest(-1)


def test_block_log_end_indices_and_wrap() -> None:
    s = SampleStream(100, FS, block_log_size=4)
    sizes = [3, 5, 2, 7, 1, 4, 6]
    for i, n in enumerate(sizes):
        s.write(np.zeros(n), host_ns=1000 * i)
    ends_all = np.cumsum(sizes)
    ends, ns = s.block_log()
    assert ends.dtype == np.int64 and ns.dtype == np.int64
    np.testing.assert_array_equal(ends, ends_all[-4:])
    np.testing.assert_array_equal(ns, [3000, 4000, 5000, 6000])
    ends2, ns2 = s.block_log(last=2)
    np.testing.assert_array_equal(ends2, ends_all[-2:])
    np.testing.assert_array_equal(ns2, [5000, 6000])
    assert s.block_log(last=0)[0].size == 0


def test_block_log_less_than_size() -> None:
    s = SampleStream(100, FS, block_log_size=8)
    s.write(np.zeros(4), host_ns=10)
    s.write(np.zeros(6), host_ns=20)
    ends, ns = s.block_log()
    np.testing.assert_array_equal(ends, [4, 10])
    np.testing.assert_array_equal(ns, [10, 20])


def _feed(s: SampleStream, n_blocks: int, block: int, keep_every: int = 1) -> None:
    """Write blocks timestamped at their nominal time, keeping 1 of every ``keep_every``."""
    for k in range(n_blocks):
        if k % keep_every:
            continue
        t_ns = int((k + 1) * block / s.sample_rate * 1e9)
        s.write(np.zeros(block, np.float32), host_ns=t_ns)


def test_stats_full_delivery() -> None:
    s = SampleStream(10_000, 48_000.0)
    st0 = s.stats()
    assert isinstance(st0, StreamStats)
    assert st0.ingest_ratio == 1.0 and st0.blocks == 0
    s.write(np.zeros(480), host_ns=5)
    assert s.stats().ingest_ratio == 1.0  # only one block
    s.reset()
    _feed(s, 200, 480)
    st = s.stats()
    assert st.blocks == 200
    assert st.total_samples == 96_000
    assert st.audio_s == pytest.approx(2.0)
    assert st.ingest_ratio == pytest.approx(1.0, abs=1e-9)
    assert st.max_gap_ms == pytest.approx(10.0)
    assert st.max_write_us >= 0.0


def test_stats_half_lost() -> None:
    s = SampleStream(10_000, 48_000.0)
    _feed(s, 400, 480, keep_every=2)
    st = s.stats()
    assert st.ingest_ratio == pytest.approx(0.5, abs=0.01)
    assert st.max_gap_ms == pytest.approx(20.0)


def test_reset() -> None:
    s = SampleStream(8, FS)
    s.write(_ramp(0, 20), host_ns=1)
    s.write(_ramp(20, 22), host_ns=2)
    s.reset()
    assert s.total_samples == 0 and s.oldest_available == 0
    assert s.block_log()[0].size == 0
    st = s.stats()
    assert (st.blocks, st.max_gap_ms, st.max_write_us, st.ingest_ratio) == (0, 0.0, 0.0, 1.0)
    s.write(_ramp(0, 3))
    np.testing.assert_array_equal(s.read(0, 3), _ramp(0, 3))


def test_concurrent_producer_reader_never_returns_corrupt_data() -> None:
    """A producer writes an integer ramp; every successful read must be consecutive."""
    cap = 4096
    total_target = 3_000_000  # < 2**24, so float32 represents the ramp exactly
    s = SampleStream(cap, 48_000.0)
    rng = np.random.default_rng(1)
    sizes = rng.integers(1, 700, size=total_target // 100)
    done = threading.Event()

    def producer() -> None:
        pos = 0
        for n in sizes:
            if pos >= total_target:
                break
            s.write(np.arange(pos, pos + n, dtype=np.float32))
            pos += int(n)
        done.set()

    good = 0
    overwritten = 0
    t = threading.Thread(target=producer)
    t.start()
    deadline = time.monotonic() + 10.0
    while not done.is_set() and time.monotonic() < deadline:
        total = s.total_samples
        # Alternate between the risky oldest region and read_latest.
        if good % 2:
            start = max(0, total - cap)
            try:
                x = s.read(start, total)
            except SamplesOverwritten:
                overwritten += 1
                continue
        else:
            try:
                start, x = s.read_latest(cap // 2)
            except SamplesOverwritten:
                overwritten += 1
                continue
        np.testing.assert_array_equal(x, np.arange(start, start + x.size, dtype=np.float64))
        good += 1
    t.join()
    assert done.is_set()
    assert good > 10
