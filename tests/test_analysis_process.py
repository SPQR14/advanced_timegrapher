"""Analysis isolated in a child process: same figures as in-thread, gaps, lifecycle."""

from __future__ import annotations

import time

import numpy as np
import pytest

from adv_tg.audio.analysis_process import LocalAnalyzer, ProcessAnalyzer
from adv_tg.core.sample_stream import SampleStream
from adv_tg.core.session import MeasurementSession, SessionConfig
from tests.synth import synth_watch

FS = 48_000
BLOCK = 960
CFG = SessionConfig(sample_rate=FS, bph=28800, lift_angle_deg=52)


def _parent_stream(x: np.ndarray, capacity_s: float = 10.0) -> tuple[SampleStream, list[int]]:
    """Empty parent stream plus the block boundaries to write ``x`` with."""
    return SampleStream(int(capacity_s * FS), FS), list(range(0, x.size, BLOCK)) + [x.size]


def _write(stream: SampleStream, x: np.ndarray, a: int, b: int) -> None:
    stamp = 1_700_000_000_000_000_000 + int(b / FS * 1e9) + 300_000
    stream.write(x[a:b], host_ns=stamp, wall_ns=stamp)


@pytest.fixture(scope="module")
def watch() -> np.ndarray:
    x, _ = synth_watch(bph=28800, rate_s_per_day=5.0, beat_error_ms=0.4, amplitude_deg=275,
                       lift_angle_deg=52, seconds=16, seed=21)  # fmt: skip
    return x


def test_local_analyzer_matches_direct_session(watch) -> None:
    parent, edges = _parent_stream(watch)
    direct = MeasurementSession(CFG)
    a = LocalAnalyzer()
    a.start(CFG, parent.capacity)
    for i in range(len(edges) - 1):
        _write(parent, watch, edges[i], edges[i + 1])
        if i % 9 == 0:  # forward in chunks spanning several blocks
            a.feed(parent)
    a.feed(parent)
    snap = a.finish()[-1]
    ref = direct.update(parent)
    assert snap.metrics.rate_s_per_day == pytest.approx(ref.metrics.rate_s_per_day, abs=1e-9)
    assert snap.beats_total == ref.beats_total
    assert snap.live_drift.ppm == pytest.approx(ref.live_drift.ppm, abs=1e-9)


def test_lost_samples_become_a_gap(watch) -> None:
    parent, edges = _parent_stream(watch, capacity_s=1.0)  # small buffer: the parent laps us
    a = LocalAnalyzer()
    a.start(CFG, parent.capacity)
    snaps = []
    for i in range(len(edges) - 1):
        _write(parent, watch, edges[i], edges[i + 1])
        if 300 <= i < 500:  # ~4 s without forwarding: the parent overwrites those samples
            continue
        a.feed(parent)
        if i % 12 == 0:
            a.request_update()
            snaps += a.poll()
    a.feed(parent)
    snaps += a.finish()
    last = snaps[-1]
    lost = 200 * BLOCK - parent.capacity
    # The lost samples are a gap (the detector also skips part of a gap longer than its
    # buffer): indices stay aligned, so the rate is still right.
    assert (watch.size - lost) / FS - 1.0 < last.audio_s <= (watch.size - lost) / FS + 0.05
    assert last.metrics.rate_s_per_day == pytest.approx(5.0, abs=1.0)
    assert last.detection_ratio < 0.85  # the beats inside the gap count as not detected


def test_process_analyzer_end_to_end(watch) -> None:
    parent, edges = _parent_stream(watch)
    a = ProcessAnalyzer()
    try:
        # Fed much faster than real time: the child may lag, so its buffer holds the whole
        # take (with the parent's 10 s it would rightly skip what it could not keep up with).
        a.start(CFG, FS * 30)
        snaps = []
        for i in range(len(edges) - 1):
            _write(parent, watch, edges[i], edges[i + 1])
            if i % 8 == 0:
                a.feed(parent)
            if i % 12 == 0:
                a.request_update()
            snaps += a.poll()
        a.feed(parent)
        snaps += a.finish(timeout_s=10.0)
        assert a.alive and not a.errors
        assert len(snaps) >= 3 and a.skipped > 0  # fed faster than real time: requests skipped
        last = snaps[-1]
        assert last.audio_s == pytest.approx(watch.size / FS, abs=0.05)
        assert last.metrics.rate_s_per_day == pytest.approx(5.0, abs=0.5)
        assert last.metrics.beat_error_ms == pytest.approx(0.4, abs=0.06)
        # A second session reuses the same child.
        pid = a._proc.pid
        a.start(CFG, parent.capacity)
        assert a._proc.pid == pid
        assert a.finish(timeout_s=10.0)
    finally:
        a.close()
    assert not a.alive


def test_process_analyzer_skips_when_behind(watch) -> None:
    a = ProcessAnalyzer()
    try:
        a.start(CFG, FS * 10)
        for _ in range(10):  # nothing fed yet, requests pile up: only MAX_PENDING are sent
            a.request_update()
        assert a.skipped == 8
        deadline = time.monotonic() + 15
        got = []
        while len(got) < 2 and time.monotonic() < deadline:
            got += a.poll()
            time.sleep(0.02)
        assert len(got) == 2
    finally:
        a.close()
