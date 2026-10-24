"""CaptureStreamWorker tests with an injected sample source (no audio hardware)."""

from __future__ import annotations

import math
import threading
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PyQt6")

from adv_tg.core.models import WatchConfig  # noqa: E402
from adv_tg.tools.capture_check import FileReplay, read_wav_mono  # noqa: E402
from adv_tg.ui.capture_worker import (  # noqa: E402
    FALLBACK_BLOCKSIZE,
    FALLBACK_LATENCY,
    PRIMARY_BLOCKSIZE,
    PRIMARY_LATENCY,
    WAVEFORM_BINS,
    CaptureStreamWorker,
    level_text,
    level_to_quality,
)

FS = 48_000
SAMPLES_DIR = Path(__file__).resolve().parents[1] / "docs" / "samples"


def tone_and_ticks(secs: float, tone_amp: float = 0.05, tick_amp: float = 0.4) -> np.ndarray:
    """Synthetic watch-like signal: weak tone + 6 Hz decaying ticks."""
    t = np.arange(int(FS * secs)) / FS
    x = tone_amp * np.sin(2 * np.pi * 440.0 * t)
    burst = (
        tick_amp * np.exp(-np.arange(300) / 40.0) * np.sin(2 * np.pi * 4000 * np.arange(300) / FS)
    )
    for k in range(int(secs * 6)):
        i = int(k * FS / 6)
        x[i : i + 300] += burst[: len(x) - i]
    return x.astype(np.float32)


def py_threads() -> int:
    """Python threads, excluding the dummy entries Python adds for foreign (Qt) threads."""
    return sum(1 for t in threading.enumerate() if not isinstance(t, threading._DummyThread))


class Factory:
    """``capture_factory`` that replays ``signal`` in real time via ``FileReplay``."""

    def __init__(self, signal: np.ndarray, fail: int = 0, exc: BaseException | None = None):
        self.signal = signal
        self.fail = fail  # number of calls that raise before succeeding
        self.exc = exc or RuntimeError("Invalid device")
        self.calls: list[tuple[int, str]] = []
        self.sources: list[FileReplay] = []

    def __call__(self, stream, *, device, sample_rate, blocksize, latency):
        self.calls.append((blocksize, latency))
        assert sample_rate == int(stream.sample_rate)
        if len(self.calls) <= self.fail:
            raise self.exc
        src = FileReplay(stream, self.signal, blocksize or 1024)
        self.sources.append(src)
        return src


class Recorder:
    def __init__(self, worker: CaptureStreamWorker) -> None:
        self.waveforms: list[tuple[np.ndarray, np.ndarray]] = []
        self.health: list[tuple[float, int]] = []
        self.levels: list[tuple[float, float, bool]] = []
        self.errors: list[str] = []
        self.finished = 0
        self.telemetry = 0
        worker.waveform_ready.connect(lambda t, y: self.waveforms.append((t, y)))
        worker.capture_health.connect(lambda p, o: self.health.append((p, o)))
        worker.level_updated.connect(lambda p, r, s: self.levels.append((p, r, s)))
        worker.error_occurred.connect(self.errors.append)
        worker.session_finished.connect(self._on_finished)
        worker.telemetry_ready.connect(lambda *_: setattr(self, "telemetry", self.telemetry + 1))

    def _on_finished(self) -> None:
        self.finished += 1


_LIVE: list[CaptureStreamWorker] = []


@pytest.fixture(autouse=True)
def _shutdown_workers():
    """Always stop workers, even when an assertion fails (a live QThread aborts Python)."""
    yield
    while _LIVE:
        _LIVE.pop().shutdown()


def make_worker(factory: Factory, fs: int = FS) -> tuple[CaptureStreamWorker, Recorder]:
    worker = CaptureStreamWorker(WatchConfig(sample_rate=fs), capture_factory=factory)
    worker.set_input_device(5)
    _LIVE.append(worker)
    return worker, Recorder(worker)


def test_level_helpers() -> None:
    assert level_to_quality(-12.0, -30.0, False) == (80, "ok")
    assert level_to_quality(-50.0, -65.0, False)[1] == "low"
    assert level_to_quality(-0.5, -10.0, True)[1] == "bad"
    assert level_to_quality(-math.inf, -math.inf, False) == (0, "bad")
    assert level_to_quality(-80.0, -95.0, False)[1] == "bad"
    assert level_text(-12.34, -30.0, False) == "-12.3 dBFS pk | RMS -30.0"
    assert level_text(-math.inf, -math.inf, True).startswith("-- dBFS")


def test_streams_waveform_health_and_level(qtbot) -> None:
    signal = tone_and_ticks(10.0)
    factory = Factory(signal)
    worker, rec = make_worker(factory)
    base_threads = py_threads()

    worker.start_stream()
    assert worker.isRunning()
    t0 = time.monotonic()
    qtbot.wait(1500)
    span = time.monotonic() - t0
    assert factory.calls == [(PRIMARY_BLOCKSIZE, PRIMARY_LATENCY)]
    assert worker.open_params == (PRIMARY_BLOCKSIZE, PRIMARY_LATENCY)

    assert len(rec.waveforms) / span >= 20.0, f"{len(rec.waveforms)} waveforms in {span:.2f} s"
    t_ms, y = rec.waveforms[-1]
    assert len(t_ms) == len(y) == 2 * WAVEFORM_BINS  # min/max envelope of 0.6 s
    assert t_ms[-1] == pytest.approx(600.0, rel=0.01)
    # The envelope keeps the true peaks: same amplitude as the source within 1 dB
    plot_peak = max(float(np.max(np.abs(w))) for _, w in rec.waveforms)
    src_peak = float(np.max(np.abs(signal[: worker.stream.total_samples])))
    assert abs(20 * math.log10(plot_peak / src_peak)) < 1.0

    assert rec.health and rec.health[-1][0] >= 99.0 and rec.health[-1][1] == 0
    assert len(rec.levels) >= 10
    assert not any(s for _, _, s in rec.levels)
    peaks = [p for p, _, _ in rec.levels]
    assert all(-30.0 < p < -1.0 for p in peaks)  # tone alone ~-26 dBFS, ticks ~-8 dBFS
    assert max(peaks) > -12.0
    assert rec.telemetry == 0 and rec.errors == []

    qthread = worker._thread
    t_stop = time.monotonic()
    worker.stop_stream()
    assert time.monotonic() - t_stop < 2.0
    assert not worker.isRunning()
    assert worker._thread is None and qthread.isFinished()
    qtbot.wait(50)
    assert rec.finished == 1
    assert not factory.sources[0].is_active
    assert py_threads() == base_threads


def test_clipped_signal_reports_saturation(qtbot) -> None:
    signal = np.clip(tone_and_ticks(5.0, tone_amp=1.5), -1.0, 1.0)
    worker, rec = make_worker(Factory(signal))
    worker.start_stream()
    qtbot.waitUntil(lambda: len(rec.levels) >= 3, timeout=3000)
    worker.stop_stream()
    assert all(s for _, _, s in rec.levels)
    assert level_to_quality(*rec.levels[-1])[1] == "bad"


def test_start_stop_five_times_without_leaks(qtbot) -> None:
    factory = Factory(tone_and_ticks(5.0))
    worker, rec = make_worker(factory)
    base_threads = py_threads()
    qthreads = []
    for _ in range(5):
        worker.start_stream()
        qtbot.wait(250)
        assert worker.isRunning()
        qthreads.append(worker._thread)
        worker.stop_stream()
        assert not worker.isRunning()
    qtbot.wait(50)
    assert all(t.isFinished() for t in qthreads)
    assert worker._stuck_threads == []
    assert rec.finished == 5
    assert len(factory.sources) == 5 and not any(s.is_active for s in factory.sources)
    assert py_threads() == base_threads


def test_open_failure_emits_error(qtbot) -> None:
    factory = Factory(tone_and_ticks(1.0), fail=99)
    worker, rec = make_worker(factory)
    with qtbot.waitSignal(worker.session_finished, timeout=3000):
        worker.start_stream()
    assert not worker.isRunning()
    assert len(rec.errors) == 1 and "No se pudo abrir el dispositivo de audio 5" in rec.errors[0]
    assert factory.calls == [
        (PRIMARY_BLOCKSIZE, PRIMARY_LATENCY),
        (FALLBACK_BLOCKSIZE, FALLBACK_LATENCY),
    ]
    worker.stop_stream()  # harmless after a failed session
    qtbot.wait(50)
    assert rec.finished == 1


def test_missing_sounddevice_is_not_retried(qtbot) -> None:
    factory = Factory(tone_and_ticks(1.0), fail=99, exc=ImportError("No module named x"))
    worker, rec = make_worker(factory)
    with qtbot.waitSignal(worker.session_finished, timeout=3000):
        worker.start_stream()
    assert len(factory.calls) == 1
    assert "sounddevice/PortAudio" in rec.errors[0]
    worker.shutdown()


def test_fallback_parameters_on_first_failure(qtbot) -> None:
    factory = Factory(tone_and_ticks(5.0), fail=1)
    worker, rec = make_worker(factory)
    worker.start_stream()
    qtbot.waitUntil(lambda: len(rec.waveforms) > 3, timeout=3000)
    assert worker.open_params == (FALLBACK_BLOCKSIZE, FALLBACK_LATENCY)
    assert rec.errors == []
    worker.stop_stream()


def test_sample_rate_mismatch_is_an_error(qtbot) -> None:
    class OddRate(Factory):
        def __call__(self, stream, **kw):
            src = super().__call__(stream, **kw)
            src.sample_rate_matches = False
            src.actual_sample_rate = 44_100.0
            return src

    factory = OddRate(tone_and_ticks(2.0))
    worker, rec = make_worker(factory)
    with qtbot.waitSignal(worker.session_finished, timeout=3000):
        worker.start_stream()
    assert "tasa de muestreo efectiva" in rec.errors[0] and "44100.0" in rec.errors[0]
    assert not factory.sources[0].is_active
    worker.shutdown()


def test_device_stopping_by_itself_ends_session(qtbot) -> None:
    worker, rec = make_worker(Factory(tone_and_ticks(0.4)))
    with qtbot.waitSignal(worker.session_finished, timeout=3000):
        worker.start_stream()
    assert rec.errors == ["El dispositivo de audio dejó de enviar datos."]
    assert not worker.isRunning()
    worker.shutdown()


def _readable_wavs() -> list[Path]:
    if not SAMPLES_DIR.is_dir():
        return []
    out = []
    for p in sorted(SAMPLES_DIR.glob("*")):
        if p.suffix.lower() not in (".wav", ".flac"):
            continue
        try:
            read_wav_mono(p)
        except Exception:
            continue
        out.append(p)
    return out


@pytest.mark.parametrize(
    "path", _readable_wavs()[:1] or [None], ids=lambda p: getattr(p, "name", "none")
)
def test_sample_wav_reaches_the_plot(qtbot, path: Path | None) -> None:
    if path is None:
        pytest.skip(f"no readable WAV files in {SAMPLES_DIR}")
    fs, x = read_wav_mono(path)
    if fs not in (48_000, 96_000):
        pytest.skip(f"{path.name}: {fs} Hz is not a GUI sample rate")
    worker, rec = make_worker(Factory(x), fs)
    worker.start_stream()
    qtbot.wait(2000)
    worker.stop_stream()
    plot_peak = max(float(np.max(np.abs(w))) for _, w in rec.waveforms)
    src_peak = float(np.max(np.abs(x[: worker.stream.total_samples])))
    assert abs(20 * math.log10(plot_peak / src_peak)) < 1.0
