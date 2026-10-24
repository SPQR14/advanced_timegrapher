"""Replay the real recordings in ``docs/samples/`` through a SampleStream (skipped if absent).

Only WAV files are used (``scipy.io.wavfile``); other formats are skipped.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from adv_tg.core.sample_stream import SampleStream
from adv_tg.tools.capture_check import read_wav_mono

SAMPLES_DIR = Path(__file__).resolve().parents[1] / "docs" / "samples"
BLOCK = 1024
LSB = 1.0 / 32768.0


def _readable_wavs() -> list[Path]:
    if not SAMPLES_DIR.is_dir():
        return []
    found = []
    for p in sorted(SAMPLES_DIR.glob("*.wav")):
        try:
            read_wav_mono(p)
        except Exception:
            continue
        found.append(p)
    return found


WAVS = _readable_wavs()


@pytest.mark.parametrize("path", WAVS or [None], ids=lambda p: p.name if p else "none")
def test_replay_sample(path: Path | None) -> None:
    if path is None:
        pytest.skip(f"no readable WAV files in {SAMPLES_DIR}")
    fs, x = read_wav_mono(path)
    capacity = min(x.size, 10 * fs)
    s = SampleStream(capacity, fs)
    for pos in range(0, x.size, BLOCK):
        end = min(pos + BLOCK, x.size)
        s.write(x[pos:end], host_ns=int(round(end / fs * 1e9)))
    assert s.total_samples == x.size

    n = min(fs, capacity // 2)
    start = x.size - capacity + (capacity - n) // 2
    got = s.read(start, start + n)
    assert np.max(np.abs(got - x[start : start + n])) <= LSB

    if s.stats().blocks >= 3:
        # Last block may be partial, so allow one block of slack.
        assert s.stats().ingest_ratio == pytest.approx(1.0, abs=BLOCK / x.size * 2 + 1e-6)
