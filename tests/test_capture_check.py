"""Tests for the ``capture_check`` CLI (file replay mode and error paths, no hardware)."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np
import pytest
from scipy.io import wavfile

from adv_tg.tools import capture_check
from adv_tg.tools.capture_check import EXIT_ERROR, EXIT_OK, main, read_wav_mono

FS = 48_000


def _synthetic_wav(path: Path, secs: float = 2.0) -> np.ndarray:
    """Write a tone + 6 Hz ticks WAV (int16) and return its samples as float32."""
    t = np.arange(int(FS * secs)) / FS
    x = 0.05 * np.sin(2 * np.pi * 440.0 * t)
    for k in range(int(secs * 6)):
        i = int(k * FS / 6)
        x[i : i + 200] += 0.5 * np.exp(-np.arange(200) / 30.0)
    pcm = np.round(x * 32767).astype(np.int16)
    wavfile.write(str(path), FS, pcm)
    return pcm.astype(np.float32) / 32768.0


def test_read_wav_mono_formats(tmp_path: Path) -> None:
    ref = _synthetic_wav(tmp_path / "a.wav", secs=0.1)
    fs, x = read_wav_mono(tmp_path / "a.wav")
    assert fs == FS and x.dtype == np.float32
    np.testing.assert_array_equal(x, ref)
    stereo = np.stack([ref, -ref], axis=1)
    wavfile.write(str(tmp_path / "s.wav"), FS, stereo)
    _, xs = read_wav_mono(tmp_path / "s.wav")
    np.testing.assert_array_equal(xs, ref)


def test_file_replay_ok(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wav = tmp_path / "tick.wav"
    _synthetic_wav(wav)
    out_wav = tmp_path / "out.wav"
    code = main(["--file", str(wav), "--secs", "2", "--wav", str(out_wav)])
    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    assert "VEREDICTO: OK" in out
    assert "ingesta=" in out and "dBFS" in out
    fs, saved = wavfile.read(str(out_wav))
    assert fs == FS and saved.dtype == np.int16 and saved.size == 2 * FS


@pytest.mark.parametrize(
    "argv",
    [
        ["--fs", "-1"],
        ["--secs", "0"],
        ["--secs", "-3"],
        ["--blocksize", "-5"],
        ["--latency", "medium"],
        ["--file", "no/existe.wav"],
        ["--file", "x.wav", "--device", "1"],
    ],
)
def test_impossible_parameters_exit_2(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(argv) == EXIT_ERROR


def test_device_open_error_exit_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    mod = types.ModuleType("sounddevice")

    def boom(**kw):
        raise ValueError("Invalid device")

    mod.query_devices = lambda *a, **k: {}
    mod.check_input_settings = boom
    monkeypatch.setitem(sys.modules, "sounddevice", mod)
    assert main(["--device", "99", "--secs", "1"]) == EXIT_ERROR
    assert "no se pudo abrir" in capsys.readouterr().out


def test_list_without_portaudio(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_portaudio():
        raise OSError("PortAudio library not found")

    monkeypatch.setattr(capture_check, "load_sounddevice", no_portaudio)
    assert main(["--list"]) == EXIT_ERROR
    assert "PortAudio" in capsys.readouterr().out


def test_list_with_fake_devices(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    mod = types.ModuleType("sounddevice")
    mod.query_devices = lambda *a, **k: [
        {"name": "CM18 USB", "max_input_channels": 1, "default_samplerate": 48000.0},
        {"name": "Speakers", "max_input_channels": 0, "default_samplerate": 48000.0},
    ]
    mod.default = types.SimpleNamespace(device=(0, 1))
    monkeypatch.setitem(sys.modules, "sounddevice", mod)
    assert main(["--list"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "CM18 USB" in out and "predeterminado" in out and "Speakers" not in out
