"""Tests for AudioCapture with a fake ``sounddevice`` module (no PortAudio needed)."""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from adv_tg.audio.capture import AudioCapture
from adv_tg.core.sample_stream import SampleStream

FS = 48_000


class FakeInputStream:
    """Minimal stand-in for ``sounddevice.InputStream``."""

    instances: list[FakeInputStream] = []

    def __init__(self, *, samplerate: float, callback, **kwargs) -> None:
        self.samplerate = float(samplerate)
        self.callback = callback
        self.kwargs = kwargs
        self.active = False
        self.closed = False
        self.start_calls = 0
        self.stop_calls = 0
        FakeInputStream.instances.append(self)

    def start(self) -> None:
        self.start_calls += 1
        self.active = True

    def stop(self) -> None:
        self.stop_calls += 1
        self.active = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_sd(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    mod = types.ModuleType("sounddevice")
    mod.InputStream = FakeInputStream
    mod.query_devices = lambda device=None, kind=None: {
        "name": "Fake mic",
        "default_samplerate": float(FS),
    }
    mod.check_input_settings = lambda **kw: None
    FakeInputStream.instances = []
    monkeypatch.setitem(sys.modules, "sounddevice", mod)
    return mod


def _capture(**kw) -> tuple[SampleStream, AudioCapture]:
    stream = SampleStream(FS, FS)
    return stream, AudioCapture(stream, device=None, sample_rate=FS, **kw)


def test_constructor_validation() -> None:
    stream = SampleStream(10, FS)
    with pytest.raises(ValueError):
        AudioCapture(stream, device=None, sample_rate=44_100)
    with pytest.raises(ValueError):
        AudioCapture(stream, device=None, sample_rate=FS, blocksize=-1)
    with pytest.raises(ValueError):
        AudioCapture(stream, device=None, sample_rate=FS, latency="medium")  # type: ignore[arg-type]


def test_callback_writes_into_stream(fake_sd: types.ModuleType) -> None:
    stream, cap = _capture(blocksize=256, latency="low")
    cap.start()
    sd_stream = FakeInputStream.instances[0]
    assert sd_stream.kwargs["channels"] == 1
    assert sd_stream.kwargs["dtype"] == "float32"
    assert sd_stream.kwargs["blocksize"] == 256
    assert sd_stream.kwargs["latency"] == "low"
    assert cap.actual_sample_rate == FS and cap.sample_rate_matches

    block = np.arange(256, dtype=np.float32).reshape(-1, 1) / 1000.0
    sd_stream.callback(block, 256, None, None)
    sd_stream.callback(block, 256, None, None)
    assert stream.total_samples == 512
    np.testing.assert_allclose(stream.read(256, 512), block[:, 0])
    assert cap.callbacks == 2
    assert cap.callback_errors == 0 and cap.status_flags == 0
    assert cap.max_callback_us > 0.0
    cap.close()


def test_status_flags_counted(fake_sd: types.ModuleType) -> None:
    stream, cap = _capture()
    cap.start()
    cb = FakeInputStream.instances[0].callback
    block = np.zeros((64, 1), np.float32)
    cb(block, 64, None, "input overflow")
    cb(block, 64, None, "")
    cb(block, 64, None, None)
    assert cap.status_flags == 1
    assert stream.total_samples == 192
    cap.close()


def test_exception_in_callback_is_counted_not_raised(fake_sd: types.ModuleType) -> None:
    stream, cap = _capture()
    cap.start()
    cb = FakeInputStream.instances[0].callback
    cb(np.zeros(64, np.float32), 64, None, None)  # 1-D: indata[:, 0] raises IndexError
    cb(None, 0, None, None)
    assert cap.callback_errors == 2
    assert stream.total_samples == 0
    cap.close()


def test_start_stop_idempotent(fake_sd: types.ModuleType) -> None:
    _, cap = _capture()
    assert not cap.is_active
    cap.stop()  # stop before start is a no-op
    cap.start()
    cap.start()
    assert cap.is_active
    sd_stream = FakeInputStream.instances[0]
    assert len(FakeInputStream.instances) == 1 and sd_stream.start_calls == 1
    cap.stop()
    cap.stop()
    assert not cap.is_active and sd_stream.stop_calls == 1
    cap.start()  # restart reuses the open stream
    assert sd_stream.start_calls == 2 and len(FakeInputStream.instances) == 1
    cap.close()
    cap.close()
    assert sd_stream.closed and not cap.is_active


def test_sample_rate_mismatch_detected(fake_sd: types.ModuleType) -> None:
    class OddRateStream(FakeInputStream):
        def __init__(self, **kw) -> None:
            super().__init__(**kw)
            self.samplerate = 44_100.0

    fake_sd.InputStream = OddRateStream
    _, cap = _capture()
    cap.start()
    assert cap.actual_sample_rate == 44_100.0
    assert not cap.sample_rate_matches
    cap.close()


def test_module_imports_without_sounddevice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    _, cap = _capture()
    with pytest.raises(ImportError):
        cap.start()
    assert not cap.is_active
