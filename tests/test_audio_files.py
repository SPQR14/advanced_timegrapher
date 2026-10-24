"""WAV/FLAC I/O through ``adv_tg.utils.audio_files`` (round trips, blocks, errors)."""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pytest

from adv_tg.utils.audio_files import (
    AudioFileError,
    AudioWriter,
    audio_info,
    iter_audio_blocks,
    read_audio,
    read_mono,
    select_channel,
    write_audio,
)


def _signal(n: int, channels: int = 1, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = 0.8 * np.sin(np.linspace(0.0, 300.0, n))[:, None] * np.linspace(1.0, 0.5, channels)
    x = x + np.clip(0.03 * rng.standard_normal((n, channels)), -0.15, 0.15)  # peak < 1
    return x[:, 0] if channels == 1 else x


def _no_temp_left(d: Path) -> bool:
    return not [p for p in d.iterdir() if p.name.startswith(".")]


@pytest.mark.parametrize("fs", [44100, 48000, 96000])
@pytest.mark.parametrize("channels", [1, 2])
def test_wav_float_is_bit_exact_in_float32(tmp_path: Path, fs: int, channels: int) -> None:
    x = _signal(fs // 10, channels).astype(np.float32)
    p = write_audio(tmp_path / "a.wav", x, fs)
    d = read_audio(p)
    assert (d.format, d.subtype, d.sample_rate, d.channels) == ("WAV", "FLOAT", fs, channels)
    assert d.samples.dtype == np.float64 and d.samples.shape == x.shape
    np.testing.assert_array_equal(d.samples.astype(np.float32), x)
    assert d.duration_s == pytest.approx(x.shape[0] / fs)


@pytest.mark.parametrize(
    "name, subtype, bits",
    [
        ("a.wav", "PCM_16", 16),
        ("a.flac", None, 24),
        ("a.flac", "PCM_24", 24),
        ("a.flac", "PCM_16", 16),
    ],
)
@pytest.mark.parametrize("fs", [44100, 48000, 96000])
def test_pcm_round_trip_within_one_lsb(
    tmp_path: Path, name: str, subtype: str | None, bits: int, fs: int
) -> None:
    x = _signal(fs // 10, 2)
    d = read_audio(write_audio(tmp_path / name, x, fs, subtype=subtype))
    assert d.subtype == (subtype or "PCM_24")
    lsb = 2.0 ** -(bits - 1)
    # libsndfile scales by 2^(b-1)-1 when writing and 2^(b-1) when reading: <= 1 LSB
    assert np.max(np.abs(d.samples - x)) <= lsb * (1.0 + 1e-9)


def test_select_channel_max_energy_and_index(tmp_path: Path) -> None:
    left = 0.1 * _signal(4800, seed=1)
    right = 0.8 * _signal(4800, seed=2)
    p = write_audio(tmp_path / "st.wav", np.stack([left, right], axis=1), 48000)
    d = read_audio(p)
    np.testing.assert_allclose(select_channel(d), right, atol=1e-7)
    np.testing.assert_allclose(select_channel(d, 0), left, atol=1e-7)
    np.testing.assert_allclose(read_mono(p)[1], right, atol=1e-7)
    with pytest.raises(ValueError):
        select_channel(d, 2)
    with pytest.raises(ValueError):
        select_channel(d, "average")  # type: ignore[arg-type]
    mono = select_channel(left)
    assert mono.ndim == 1


@pytest.mark.parametrize("name", ["b.wav", "b.flac"])
@pytest.mark.parametrize("channel", [None, "max_energy", 0])
def test_iter_blocks_reconstructs_read_audio(tmp_path: Path, name: str, channel) -> None:
    x = _signal(10_007, 2)
    p = write_audio(tmp_path / name, x, 48000)
    full = read_audio(p)
    ref = full.samples if channel is None else select_channel(full, channel)
    starts, parts = [], []
    for start, block in iter_audio_blocks(p, 1000, channel=channel):
        starts.append(start)
        parts.append(block)
    assert starts == list(range(0, 10_007, 1000))
    assert all(b.dtype == np.float64 for b in parts)
    np.testing.assert_array_equal(np.concatenate(parts), ref)


def test_iter_blocks_rejects_bad_blocksize(tmp_path: Path) -> None:
    p = write_audio(tmp_path / "c.wav", np.zeros(10), 48000)
    with pytest.raises(ValueError):
        next(iter_audio_blocks(p, 0))


@pytest.mark.parametrize("name", ["clip.wav", "clip.flac"])
def test_pcm_clipping_is_explicit(tmp_path: Path, name: str) -> None:
    x = np.array([0.0, 0.5, 1.5, -2.0, 0.25])
    with pytest.warns(UserWarning, match="recortadas"):
        p = write_audio(tmp_path / name, x, 48000, subtype="PCM_16")
    d = read_audio(p)
    assert d.samples[2] == pytest.approx(1.0, abs=1e-4)
    assert d.samples[3] == pytest.approx(-1.0, abs=1e-4)


def test_float_wav_keeps_values_beyond_full_scale(tmp_path: Path) -> None:
    x = np.array([0.0, 1.5, -2.0], dtype=np.float32)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        p = write_audio(tmp_path / "f.wav", x, 48000)
    np.testing.assert_array_equal(read_audio(p).samples, x)


@pytest.mark.parametrize("name", ["m.wav", "m.flac"])
def test_comment_metadata(tmp_path: Path, name: str) -> None:
    text = "Miyota 9015 — posición CH, 28800 BPH"
    d = read_audio(write_audio(tmp_path / name, np.zeros(100), 48000, comment=text))
    assert d.comment == text
    assert read_audio(write_audio(tmp_path / ("n" + name), np.zeros(10), 48000)).comment is None


def test_missing_and_corrupt_files(tmp_path: Path) -> None:
    with pytest.raises(AudioFileError, match="no existe"):
        read_audio(tmp_path / "nope.wav")
    bad = tmp_path / "bad.flac"
    bad.write_bytes(b"this is not audio at all" * 10)
    with pytest.raises(AudioFileError, match="corrupto"):
        read_audio(bad)
    with pytest.raises(AudioFileError):
        list(iter_audio_blocks(bad, 100))
    with pytest.raises(AudioFileError):
        audio_info(tmp_path / "nope.flac")


def test_unsupported_extension_or_subtype(tmp_path: Path) -> None:
    with pytest.raises(AudioFileError, match="extensión"):
        write_audio(tmp_path / "x.mp3", np.zeros(10), 48000)
    with pytest.raises(AudioFileError, match="subtipo"):
        write_audio(tmp_path / "x.flac", np.zeros(10), 48000, subtype="FLOAT")
    with pytest.raises(AudioFileError):
        write_audio(tmp_path / "x.wav", np.array([0.0, np.nan]), 48000)
    assert _no_temp_left(tmp_path)


def test_atomic_write_leaves_no_temp_and_keeps_old_file(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "keep.wav"
    write_audio(target, np.full(10, 0.25), 48000)
    import soundfile

    real = soundfile.SoundFile.write

    def boom(self, data):
        real(self, data[:2])
        raise OSError("disco lleno (simulado)")

    monkeypatch.setattr(soundfile.SoundFile, "write", boom)
    with pytest.raises(AudioFileError):
        write_audio(target, np.zeros(1000), 48000)
    monkeypatch.undo()
    assert _no_temp_left(tmp_path)
    np.testing.assert_allclose(read_audio(target).samples, 0.25)


def test_uppercase_extension_and_parent_dirs(tmp_path: Path) -> None:
    p = write_audio(tmp_path / "a" / "b" / "UP.FLAC", np.zeros(48), 48000)
    assert p.exists() and read_audio(p).format == "FLAC"
    p2 = write_audio(tmp_path / "UP.WAV", np.zeros(48), 48000)
    assert read_audio(p2).subtype == "FLOAT"


def test_audio_writer_blocks_and_discard(tmp_path: Path) -> None:
    x = _signal(9000)
    p = tmp_path / "rec.flac"
    with AudioWriter(p, 48000, comment="toma 1") as w:
        for i in range(0, x.size, 1000):
            w.write(x[i : i + 1000])
    d = read_audio(p)
    assert d.frames == x.size and d.subtype == "PCM_24" and d.comment == "toma 1"
    np.testing.assert_allclose(d.samples, x, atol=2.0**-23)
    assert w.frames_written == x.size

    q = tmp_path / "fail.wav"
    with pytest.raises(RuntimeError):
        with AudioWriter(q, 48000) as w2:
            w2.write(x[:100])
            raise RuntimeError("captura interrumpida")
    assert not q.exists() and _no_temp_left(tmp_path)


def test_audio_writer_clips_with_one_warning(tmp_path: Path) -> None:
    p = tmp_path / "clip.wav"
    with pytest.warns(UserWarning) as rec:
        with AudioWriter(p, 48000, subtype="PCM_16") as w:
            w.write(np.array([2.0, 0.0]))
            w.write(np.array([-3.0, 0.1]))
    assert len([r for r in rec if "recortadas" in str(r.message)]) == 1
    assert w.clipped_samples == 2
    with pytest.raises(AudioFileError):
        with AudioWriter(tmp_path / "st.wav", 48000, channels=2) as w3:
            w3.write(np.zeros(10))


def test_audio_info(tmp_path: Path) -> None:
    p = write_audio(tmp_path / "i.flac", np.zeros((480, 2)), 48000)
    i = audio_info(p)
    assert (i.sample_rate, i.channels, i.frames, i.format, i.subtype) == (
        48000,
        2,
        480,
        "FLAC",
        "PCM_24",
    )
    assert i.duration_s == pytest.approx(0.01)
