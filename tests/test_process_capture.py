"""Isolated capture (child process, "spawn") with the fake source: no hardware, CI-safe.

Every wait is a condition with a deadline (no fixed sleeps), and an autouse fixture kills any
child left behind so a failing test never leaves orphans.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from adv_tg.audio.capture import AudioCapture
from adv_tg.audio.capture_process import FAKE_RAMP_MODULO
from adv_tg.audio.process_capture import CaptureChildError, ProcessAudioCapture
from adv_tg.core.sample_stream import SampleStream
from adv_tg.utils.logger_setup import LoggerSetup

FS = 48_000
BLOCK = 1024
OPEN_TIMEOUT_S = 30.0  # generous: spawning + importing numpy is slow on CI runners


def wait_until(cond: Callable[[], bool], timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError(f"timeout ({timeout} s) waiting for {what}")
        time.sleep(0.01)


@pytest.fixture(autouse=True)
def _no_orphans():
    yield
    leftovers = mp.active_children()
    for p in leftovers:
        p.kill()
        p.join(2)
    assert not leftovers, f"orphan capture processes: {leftovers}"


def make_capture(stream: SampleStream, **kw) -> ProcessAudioCapture:
    kw.setdefault("device", None)
    kw.setdefault("blocksize", BLOCK)
    kw.setdefault("fake_source", "ramp")
    kw.setdefault("open_timeout_s", OPEN_TIMEOUT_S)
    return ProcessAudioCapture(stream, sample_rate=FS, **kw)


def assert_ramp(stream: SampleStream) -> None:
    x = stream.read(0, stream.total_samples)
    expected = np.arange(x.size, dtype=np.int64) % FAKE_RAMP_MODULO
    np.testing.assert_array_equal(x, expected)


def _open_fds() -> int | None:
    for d in ("/proc/self/fd", "/dev/fd"):
        if os.path.isdir(d):
            return len(os.listdir(d))
    return None


def test_round_trip_ramp_without_gaps() -> None:
    stream = SampleStream(FS * 20, FS)
    cap = make_capture(stream)
    cap.start()
    assert cap.is_active and cap.child_alive
    assert cap.actual_sample_rate == FS and cap.sample_rate_matches
    assert cap.clock_comparable is True  # perf_counter_ns is shared between processes
    assert cap.child_qt_loaded is False  # the child never imports Qt
    wait_until(lambda: stream.total_samples >= FS, 10, "1 s of audio")
    cap.stop()
    assert not cap.is_active and not cap.child_alive
    assert cap.dropped_frames == 0 and cap.duplicate_frames == 0
    assert cap.frames_received == cap.seq_sent == cap.callbacks
    assert stream.total_samples == cap.seq_sent * BLOCK
    assert_ramp(stream)
    assert stream.stats().ingest_ratio == pytest.approx(1.0, abs=0.01)
    assert cap.callback_errors == 0 and cap.last_error is None
    cap.stop()  # idempotent


def test_parent_blocked_by_gil_catches_up() -> None:
    stream = SampleStream(FS * 30, FS)
    cap = make_capture(stream)
    cap.start()
    wait_until(lambda: stream.total_samples >= FS // 2, 10, "audio before blocking")

    # Hold the parent's GIL ~1.5 s: no other thread of this process may run meanwhile.
    old = sys.getswitchinterval()
    sys.setswitchinterval(30.0)
    try:
        t_end = time.perf_counter() + 1.5
        n = 0
        while time.perf_counter() < t_end:
            n += 1
    finally:
        sys.setswitchinterval(old)

    wait_until(lambda: cap.parent_lag_ms < 100, 10, "the parent to catch up")
    cap.stop()
    assert cap.parent_lag_max_ms > 1000.0, "the parent was not really blocked"
    assert cap.dropped_frames == 0
    assert_ramp(stream)
    st = stream.stats()
    assert st.ingest_ratio == pytest.approx(1.0, abs=0.01)
    # host_ns comes from the child: the parent's block does not widen the gaps
    assert st.max_gap_ms < 3 * 1000 * BLOCK / FS


def test_start_stop_five_times_no_orphans_no_fds() -> None:
    stream = SampleStream(FS * 10, FS)
    cap = make_capture(stream)
    cap.start()  # warm-up (first spawn may open long-lived resources)
    cap.stop()
    fds0 = _open_fds()
    for _ in range(5):
        cap.start()
        wait_until(lambda: cap.frames_received > 2, 10, "frames")
        cap.stop()
        assert mp.active_children() == []
        assert cap.dropped_frames == 0
    fds1 = _open_fds()
    if fds0 is not None:
        assert fds1 <= fds0, f"descriptors leaked: {fds0} -> {fds1}"


def test_open_failure_is_reported_clearly() -> None:
    cap = ProcessAudioCapture(
        SampleStream(FS, FS), device=9999, sample_rate=FS, open_timeout_s=OPEN_TIMEOUT_S
    )
    with pytest.raises((CaptureChildError, ImportError)) as info:
        cap.start()
    msg = str(info.value)
    assert "9999" in msg or "sounddevice/PortAudio" in msg
    assert cap.last_error == msg
    assert not cap.is_active
    assert mp.active_children() == []


def test_child_logs_to_its_own_file() -> None:
    log = LoggerSetup.LOG_DIR / "adv_tg_capture.log"
    size0 = log.stat().st_size if log.exists() else 0
    cap = make_capture(SampleStream(FS, FS))
    cap.start()
    cap.stop()
    wait_until(lambda: log.exists() and log.stat().st_size > size0, 5, "child log lines")
    tail = Path(log).read_text(encoding="utf-8", errors="replace")[size0:]
    assert "capture_child" in tail
    assert os.environ.get(LoggerSetup.LOG_NAME_ENV_VAR) is None  # parent env restored


def test_default_factory_and_inproc_override(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("PyQt6")
    from adv_tg.ui import capture_worker as cw

    stream = SampleStream(FS, FS)
    kw = {"device": None, "sample_rate": FS, "blocksize": BLOCK, "latency": "low"}
    monkeypatch.delenv(cw.INPROC_ENV_VAR, raising=False)
    assert cw.capture_in_separate_process()
    assert isinstance(cw._default_capture_factory(stream, **kw), ProcessAudioCapture)
    monkeypatch.setenv(cw.INPROC_ENV_VAR, "1")
    assert not cw.capture_in_separate_process()
    assert isinstance(cw._default_capture_factory(stream, **kw), AudioCapture)


def test_worker_reports_child_death(qtbot) -> None:
    pytest.importorskip("PyQt6")
    from adv_tg.core.models import WatchConfig
    from adv_tg.ui.capture_worker import CaptureStreamWorker

    def factory(stream, **kw):
        return ProcessAudioCapture(stream, fake_source="ramp", open_timeout_s=OPEN_TIMEOUT_S, **kw)

    worker = CaptureStreamWorker(WatchConfig(sample_rate=FS), capture_factory=factory)
    worker.set_input_device(3)
    errors: list[str] = []
    worker.error_occurred.connect(errors.append)
    try:
        worker.start_stream()
        qtbot.waitUntil(lambda: bool(mp.active_children()), timeout=int(OPEN_TIMEOUT_S * 1000))
        qtbot.waitUntil(
            lambda: worker.stream is not None and worker.stream.total_samples > FS // 4,
            timeout=10_000,
        )
        child = mp.active_children()[0]
        t0 = time.monotonic()
        child.kill()
        qtbot.waitUntil(lambda: bool(errors), timeout=2000)
        assert time.monotonic() - t0 < 2.0
        assert "terminó inesperadamente" in errors[0]
        assert not worker.isRunning()
    finally:
        worker.shutdown()
    wait_until(lambda: mp.active_children() == [], 5, "no children")
