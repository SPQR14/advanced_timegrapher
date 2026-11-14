"""Choice of the input channel of a multichannel device (``adv_tg.audio.channel_select``)."""

from __future__ import annotations

import multiprocessing as mp
import sys
import time
import types

import numpy as np
import pytest

from adv_tg.audio.capture import AudioCapture
from adv_tg.audio.channel_select import MAX_CHANNELS, ChannelSelector, channels_to_open
from adv_tg.audio.process_capture import ProcessAudioCapture
from adv_tg.core.sample_stream import SampleStream

FS = 48_000
BLOCK = 1024


def _blocks(levels: list[float], seconds: float, seed: int = 0) -> list[np.ndarray]:
    """White noise blocks with one rms level per channel."""
    rng = np.random.default_rng(seed)
    n = int(seconds * FS) // BLOCK
    return [
        (rng.standard_normal((BLOCK, len(levels))) * np.array(levels)).astype(np.float32)
        for _ in range(n)
    ]


def _run(sel: ChannelSelector, blocks: list[np.ndarray]) -> list[int]:
    return [sel.pick(b) for b in blocks]


def test_channels_to_open() -> None:
    assert channels_to_open(0) == 1
    assert channels_to_open(1) == 1
    assert channels_to_open(2) == 2
    assert channels_to_open(MAX_CHANNELS) == MAX_CHANNELS
    assert channels_to_open(64) == 2  # virtual mixer (PipeWire, JACK)


def test_bad_arguments() -> None:
    with pytest.raises(ValueError):
        ChannelSelector(0, FS)
    with pytest.raises(ValueError):
        ChannelSelector(2, 0)


def test_single_channel_is_channel_zero() -> None:
    sel = ChannelSelector(1, FS)
    assert _run(sel, _blocks([0.1], 1.0)) == [0] * (FS // BLOCK)
    assert sel.switches == 0


def test_microphone_on_second_input_is_found_in_the_first_window() -> None:
    sel = ChannelSelector(2, FS)
    picks = _run(sel, _blocks([1e-5, 0.1], 1.0))
    first = picks.index(1)
    assert first * BLOCK <= 0.3 * FS
    assert set(picks[first:]) == {1} and sel.switches == 1


def test_similar_channels_keep_channel_zero() -> None:
    # the two capsules of a stereo microphone, one 2 dB hotter than the other
    sel = ChannelSelector(2, FS)
    assert set(_run(sel, _blocks([0.1, 0.126], 5.0))) == {0}
    assert sel.switches == 0


def test_no_flip_flop_after_the_first_choice() -> None:
    sel = ChannelSelector(2, FS)
    _run(sel, _blocks([0.1, 0.1], 1.0))
    # 6 dB in favour of the other capsule: below the 10 dB a later change needs
    assert set(_run(sel, _blocks([0.1, 0.2], 5.0, seed=1))) == {0}
    assert sel.switches == 0


def test_microphone_plugged_later_is_found_within_a_second() -> None:
    sel = ChannelSelector(2, FS)
    _run(sel, _blocks([1e-4, 1e-4], 2.0))
    picks = _run(sel, _blocks([1e-4, 0.1], 2.0, seed=1))
    first = picks.index(1)
    assert first * BLOCK <= 1.0 * FS
    assert set(picks[first:]) == {1} and sel.switches == 1


def test_hum_and_dc_do_not_win_over_a_ticking_channel() -> None:
    # channel 0: unconnected input, DC offset + 50 Hz hum at -6 dBFS
    # channel 1: microphone, noise + clicks 30 dB below the hum
    rng = np.random.default_rng(3)
    n = 2 * FS
    t = np.arange(n) / FS
    hum = 0.2 + 0.5 * np.sin(2 * np.pi * 50.0 * t)
    mic = 1e-3 * rng.standard_normal(n)
    for k in range(0, n, FS // 6):
        mic[k : k + 96] += 0.016 * rng.standard_normal(96)
    x = np.stack([hum, mic], axis=1).astype(np.float32)
    sel = ChannelSelector(2, FS)
    picks = [sel.pick(x[i : i + BLOCK]) for i in range(0, n - BLOCK + 1, BLOCK)]
    assert picks[-1] == 1 and sel.switches == 1


# ---------------------------------------------------------------- in-process capture
class _Stream:
    instances: list[_Stream] = []
    refuse_above = 99

    def __init__(self, **kw) -> None:
        if kw["channels"] > self.refuse_above:
            raise ValueError("Invalid number of channels")
        self.kwargs = kw
        self.callback = kw["callback"]
        self.samplerate = kw["samplerate"]
        self.active = False
        self.time = 0.0
        _Stream.instances.append(self)

    def start(self) -> None:
        self.active = True

    def stop(self) -> None:
        self.active = False

    def close(self) -> None:
        self.active = False


@pytest.fixture
def stereo_sd(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    mod = types.ModuleType("sounddevice")
    mod.InputStream = _Stream
    mod.query_devices = lambda device=None, kind=None: {
        "name": "Fake interface",
        "default_samplerate": float(FS),
        "max_input_channels": 2,
    }
    mod.check_input_settings = lambda **kw: None
    _Stream.instances = []
    _Stream.refuse_above = 99
    monkeypatch.setitem(sys.modules, "sounddevice", mod)
    return mod


def _info() -> types.SimpleNamespace:
    return types.SimpleNamespace(currentTime=0.0, inputBufferAdcTime=0.0)


def test_audio_capture_opens_all_inputs_and_keeps_the_loud_one(stereo_sd) -> None:
    stream = SampleStream(4 * FS, FS)
    cap = AudioCapture(stream, device=None, sample_rate=FS, blocksize=BLOCK)
    cap.start()
    assert _Stream.instances[0].kwargs["channels"] == 2 and cap.channels == 2
    blocks = _blocks([1e-5, 0.1], 2.0)
    for b in blocks:
        cap._callback(b, BLOCK, _info(), None)
    cap.close()
    assert cap.callback_errors == 0
    got = stream.read(0, stream.total_samples)
    want = np.concatenate([b[:, 1] for b in blocks])
    tail = FS  # after the first window everything comes from the second input
    np.testing.assert_array_equal(got[-tail:], want[-tail:].astype(np.float64))


def test_audio_capture_falls_back_to_one_channel(stereo_sd) -> None:
    _Stream.refuse_above = 1
    stream = SampleStream(FS, FS)
    cap = AudioCapture(stream, device=None, sample_rate=FS, blocksize=BLOCK)
    cap.start()
    assert _Stream.instances[0].kwargs["channels"] == 1 and cap.channels == 1
    x = _blocks([0.1], 0.1)[0]
    cap._callback(x, BLOCK, _info(), None)
    cap.close()
    np.testing.assert_array_equal(stream.read(0, BLOCK), x[:, 0])


# ---------------------------------------------------------------- capture process
def test_capture_process_delivers_the_channel_with_signal() -> None:
    stream = SampleStream(FS * 20, FS)
    cap = ProcessAudioCapture(
        stream,
        device=None,
        sample_rate=FS,
        blocksize=BLOCK,
        fake_source="tone2",
        open_timeout_s=30.0,
    )
    try:
        cap.start()
        deadline = time.monotonic() + 20.0
        while stream.total_samples < 2 * FS and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        cap.close()
    assert stream.total_samples >= 2 * FS
    x = stream.read(FS, 2 * FS)  # the first channel is silent: this is the second one
    assert float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) > 0.02
    assert not mp.active_children()
