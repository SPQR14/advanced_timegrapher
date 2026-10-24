"""GIL-wait diagnostics of AudioCapture (fake sounddevice, no hardware)."""

from __future__ import annotations

import math
import types

import numpy as np
import pytest

from adv_tg.audio.capture import GIL_WAIT_EDGES_MS, AudioCapture, hist_percentile_ms
from adv_tg.core.sample_stream import SampleStream
from tests.test_audio_capture import FakeInputStream, fake_sd  # noqa: F401

FS = 48_000
BLOCK = 1024  # 21.33 ms at 48 kHz


def test_hist_percentile_ms() -> None:
    n = len(GIL_WAIT_EDGES_MS) + 1
    assert hist_percentile_ms([0] * n, 0.99) == 0.0
    hist = [0] * n
    hist[0] = 99  # < 0.1 ms
    hist[6] = 1  # 10..20 ms
    assert hist_percentile_ms(hist, 0.5) == 0.1
    assert hist_percentile_ms(hist, 0.99) == 0.1
    assert hist_percentile_ms(hist, 1.0) == 20.0
    hist[-1] = 5  # >= 500 ms
    assert hist_percentile_ms(hist, 0.99) == math.inf


def test_callback_gil_wait_statistics(fake_sd: types.ModuleType) -> None:  # noqa: F811
    stream = SampleStream(FS, FS)
    cap = AudioCapture(stream, device=None, sample_rate=FS, blocksize=BLOCK)
    cap.start()
    sd_stream = FakeInputStream.instances[0]
    block = np.zeros((BLOCK, 1), np.float32)

    waits_ms = [0.05, 0.3, 3.0, 30.0, 700.0]
    for i, w in enumerate(waits_ms):
        current = 100.0 + i
        sd_stream.time = current + w / 1000.0  # PortAudio clock when Python starts running
        sd_stream.callback(block, BLOCK, types.SimpleNamespace(currentTime=current), None)

    # Clock slightly behind currentTime: clamped to 0, still counted
    sd_stream.time = 199.999
    sd_stream.callback(block, BLOCK, types.SimpleNamespace(currentTime=200.0), None)
    # currentTime 0 (host API without timestamps): not counted
    sd_stream.callback(block, BLOCK, types.SimpleNamespace(currentTime=0.0), None)
    # No time info at all: not counted, never raises
    sd_stream.callback(block, BLOCK, None, None)

    assert cap.callbacks == 8 and cap.callback_errors == 0
    assert stream.total_samples == 8 * BLOCK
    assert cap.gil_wait_invalid == 2
    hist = cap.gil_wait_hist
    assert sum(hist) == 6
    assert hist[0] == 2  # 0.05 ms and the clamped 0
    assert hist[1] == 1  # 0.3 ms  (< 0.5)
    assert hist[4] == 1  # 3 ms    (< 5)
    assert hist[7] == 1  # 30 ms   (< 50)
    assert hist[-1] == 1  # 700 ms (>= 500)
    assert cap.gil_wait_max_us == pytest.approx(700_000.0, rel=1e-6)
    assert cap.gil_wait_mean_us == pytest.approx(sum(waits_ms) * 1000.0 / 6, rel=1e-6)
    assert cap.gil_wait_over_block == 2  # 30 ms and 700 ms exceed one 21.3 ms block
    assert cap.gil_wait_p99_ms == pytest.approx(700.0)  # overflow bucket -> capped at max
    cap.close()


def test_no_waits_recorded_before_start() -> None:
    cap = AudioCapture(SampleStream(10, FS), device=None, sample_rate=FS)
    assert cap.gil_wait_hist == (0,) * (len(GIL_WAIT_EDGES_MS) + 1)
    assert cap.gil_wait_mean_us == 0.0 and cap.gil_wait_max_us == 0.0
    assert cap.gil_wait_p99_ms == 0.0
