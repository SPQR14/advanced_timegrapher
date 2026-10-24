"""Robustness of the beat detector: weak P1, clicks, losses, gaps, saturation, invariances,
acquisition, wrong BPH, live ``advance()`` and speed. All deterministic (seeded)."""

from __future__ import annotations

import math
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from adv_tg.core.beat_detector import BeatDetector, BeatDetectorConfig
from adv_tg.core.beat_types import BeatFlags
from adv_tg.core.sample_stream import SampleStream
from adv_tg.utils.audio_files import read_audio, write_audio
from tests.beat_helpers import beat_array, detect, match
from tests.synth import REALISM, synth_watch

FS = 48_000


# ---------------------------------------------------------------------- weak P1 / clicks
@pytest.mark.parametrize("p1_gain", [0.2, 0.1, 0.05])
@pytest.mark.parametrize("clicks", [0.0, 2.0], ids=["quiet", "clicks"])
def test_weak_p1_is_correct_or_nan(p1_gain: float, clicks: float) -> None:
    x, truth = synth_watch(
        p1_gain=p1_gain,
        snr_db=25,
        beat_error_ms=0.3,
        seconds=15,
        seed=21,
        impulsive_noise_rate_hz=clicks,
    )
    beats, _ = detect(x, FS, 28800)
    m = match(beats, truth)
    assert m.missed.size == 0 and m.false.size == 0 and m.k_consistent
    assert not np.isnan(m.err_p3).any()  # P3 always resolved
    e3 = np.abs(m.err_p3 - np.median(m.err_p3))
    assert np.percentile(e3, 95) <= 2.0
    # P1: either correct (bias ~ -2 samples, so |err| <= 2 + 2) or NaN with its flag
    for j, e in zip(m.det_idx, m.err_p1, strict=True):
        b = beats[j]
        if math.isnan(e):
            assert b.flags & BeatFlags.P1_MISSING
        else:
            assert abs(e - (-2.1)) <= 2.0, f"spurious P1 (error {e:.1f} samples)"


def test_weak_p1_with_full_realism_has_no_false_beats() -> None:
    x, truth = synth_watch(p1_gain=0.1, snr_db=25, beat_error_ms=0.3, seconds=15, seed=4, **REALISM)
    beats, _ = detect(x, FS, 28800)
    m = match(beats, truth)
    assert m.missed.size == 0 and m.false.size == 0 and m.k_consistent
    assert np.mean(np.isnan(m.err_p1)) >= 0.9


def test_impulsive_noise_makes_no_false_beats() -> None:
    x, truth = synth_watch(snr_db=25, seconds=20, seed=8, impulsive_noise_rate_hz=5.0)
    beats, stats = detect(x, FS, 28800)
    m = match(beats, truth)
    assert m.false.size == 0 and m.missed.size == 0
    assert stats.rejected_events > 0  # the clicks were seen and rejected


# ---------------------------------------------------------------------- losses and gaps
def test_missing_beats_are_not_invented() -> None:
    x, truth = synth_watch(snr_db=25, beat_error_ms=0.5, seconds=30, seed=9, miss_prob=0.05)
    assert truth.removed_k.size > 0
    beats, stats = detect(x, FS, 28800)
    m = match(beats, truth)
    assert m.false.size == 0 and m.missed.size == 0 and m.k_consistent
    ks = {b.k for b in beats}
    offset = int(m.k_offsets[0])
    assert not ks & {int(k) + offset for k in truth.removed_k}
    inside = [k for k in truth.removed_k if truth.k[0] < k < truth.k[-1]]
    assert stats.missed_beats == len(inside)


def test_dropouts_resync_with_consistent_k() -> None:
    drops = ((4.03, 0.4), (9.51, 1.3))
    x, truth = synth_watch(snr_db=25, beat_error_ms=0.5, seconds=16, seed=12, dropouts=drops)
    beats, stats = detect(x, FS, 28800)
    m = match(beats, truth)
    assert m.false.size == 0 and m.k_consistent
    assert m.missed.size <= 2  # at most the beats touching the edges of each dropout
    assert stats.gaps == 2
    after = [b for b in beats if b.flags & BeatFlags.AFTER_GAP]
    assert len(after) == 2
    for (s, d), b in zip(drops, after, strict=True):
        assert (s + d) * FS < b.p3 < (s + d + 0.3) * FS


def test_stream_overrun_in_advance_resyncs() -> None:
    x, truth = synth_watch(snr_db=25, beat_error_ms=0.5, seconds=12, seed=13)
    stream = SampleStream(FS, FS)  # 1 s ring buffer
    det = BeatDetector(BeatDetectorConfig(sample_rate=FS, bph=28800))
    beats = []
    block = 1024
    pos = 0
    while pos < x.size:
        stream.write(x[pos : pos + block])
        pos += block
        stalled = 5 * FS < pos < 7 * FS  # the reader stalls ~2 s: the ring buffer laps it
        if not stalled:
            beats += det.advance(stream)
    beats += det.advance(stream)
    beats += det.flush()
    m = match(beats, truth)
    assert m.false.size == 0 and m.k_consistent
    assert det.stats.gaps == 1 and det.stats.gap_beats > 0
    assert sum(bool(b.flags & BeatFlags.AFTER_GAP) for b in beats) == 1
    ks = [b.k for b in beats]
    assert ks == sorted(set(ks))


# ---------------------------------------------------------------------- saturation / gain
def test_saturation_is_flagged_without_breaking_detection() -> None:
    x, truth = synth_watch(snr_db=30, seconds=10, seed=14, clip_level=0.7)
    beats, _ = detect(x, FS, 28800)
    m = match(beats, truth)
    assert m.missed.size == 0 and m.false.size == 0
    sat = np.mean([bool(b.flags & BeatFlags.SATURATED) for b in beats])
    assert sat > 0.9


def test_gain_invariance() -> None:
    x, truth = synth_watch(snr_db=25, beat_error_ms=0.3, seconds=10, seed=15)
    x *= 0.3  # typical microphone level (P3 peak ~0.15), so x10 clips only the beat tops
    ref, _ = detect(x, FS, 28800)
    small, _ = detect(0.01 * x, FS, 28800)
    np.testing.assert_allclose(beat_array(small), beat_array(ref), atol=1e-6, rtol=0)
    loud, _ = detect(np.clip(10.0 * x, -1.0, 1.0), FS, 28800)
    assert [b.k for b in loud] == [b.k for b in ref]
    p3 = np.array([b.p3 for b in loud]) - np.array([b.p3 for b in ref])
    assert np.nanmax(np.abs(p3)) <= 2.0
    assert np.mean([bool(b.flags & BeatFlags.SATURATED) for b in loud]) > 0.9


@pytest.mark.parametrize(
    "name, subtype, tol",
    [("s.wav", "FLOAT", 0.5), ("s.flac", "PCM_24", 0.5), ("s.flac", "PCM_16", 1.0)],
)
def test_format_invariance(tmp_path: Path, name: str, subtype: str, tol: float) -> None:
    x, _ = synth_watch(snr_db=25, beat_error_ms=0.3, seconds=10, seed=16)
    ref, _ = detect(x, FS, 28800)
    y = read_audio(write_audio(tmp_path / name, x, FS, subtype=subtype)).samples
    got, _ = detect(y, FS, 28800)
    assert [b.k for b in got] == [b.k for b in ref]
    for a, b in zip(got, ref, strict=True):
        assert abs(a.p3 - b.p3) <= tol
        if not (math.isnan(a.p1) or math.isnan(b.p1)):
            assert abs(a.p1 - b.p1) <= tol


# ---------------------------------------------------------------------- invariance to chunking
@pytest.mark.parametrize("realism", [False, True], ids=["clean", "realism"])
def test_chunk_invariance(realism: bool) -> None:
    kw = {"snr_db": 22, "beat_error_ms": 0.8, "seconds": 9, "seed": 17, "miss_prob": 0.03}
    if realism:
        kw.update(REALISM)
    x, _ = synth_watch(**kw)
    ref = beat_array(detect(x, FS, 28800, chunk=None)[0])
    assert ref.shape[0] > 50
    for chunk in (64, 1024, 4096, 48000):
        got = beat_array(detect(x, FS, 28800, chunk=chunk)[0])
        assert got.shape == ref.shape
        np.testing.assert_allclose(got, ref, atol=1e-6, rtol=0)


def test_irregular_chunks_are_invariant() -> None:
    x, _ = synth_watch(snr_db=25, seconds=6, seed=18)
    ref = beat_array(detect(x, FS, 28800, chunk=None)[0])
    det = BeatDetector(BeatDetectorConfig(sample_rate=FS, bph=28800))
    rng = np.random.default_rng(0)
    out, pos = [], 0
    while pos < x.size:
        n = int(rng.integers(1, 9000))
        out += det.process(x[pos : pos + n], pos)
        pos += n
    out += det.flush()
    np.testing.assert_allclose(beat_array(out), ref, atol=1e-6, rtol=0)


# ---------------------------------------------------------------------- acquisition
@pytest.mark.parametrize("seed", [30, 31, 32, 33])
def test_lock_within_3s_from_random_phase(seed: int) -> None:
    x, truth = synth_watch(
        bph=21600, snr_db=25, beat_error_ms=1.0, seconds=8, seed=seed, start_s=None
    )
    beats, stats = detect(x, FS, 21600, lift_angle_deg=52)
    assert stats.lock_time_s is not None and stats.lock_time_s <= 3.0
    m = match(beats, truth)
    assert m.false.size == 0


def test_initial_noise_produces_no_beats() -> None:
    x, truth = synth_watch(snr_db=25, seconds=8, seed=34, noise_only_s=2.0)
    beats, stats = detect(x, FS, 28800)
    m = match(beats, truth)
    assert m.false.size == 0
    assert min(b.reference for b in beats) > 2.0 * FS
    assert stats.lock_time_s <= 2.0 + 3.0


def test_pure_noise_never_locks() -> None:
    rng = np.random.default_rng(1)
    beats, stats = detect(0.05 * rng.standard_normal(10 * FS), FS, 28800)
    assert beats == [] and stats.lock_time_s is None
    assert any("sin enganche" in w for w in stats.warnings)


def test_wrong_bph_does_not_invent_confident_beats() -> None:
    x, _ = synth_watch(bph=28800, snr_db=25, beat_error_ms=0.3, seconds=12, seed=35)
    beats, stats = detect(x, FS, 21600)
    confident = [b for b in beats if b.confidence > 0.5]
    assert len(confident) <= 2
    assert stats.warnings, "stats must warn about the configuration"


def test_period_offset_within_5_percent_keeps_lock_and_warns() -> None:
    # true rate +4 % (3456 s/d) with the nominal BPH: lock holds, warning above 1 %
    x, truth = synth_watch(snr_db=25, seconds=10, seed=36, rate_s_per_day=0.04 * 86400)
    beats, stats = detect(x, FS, 28800)
    m = match(beats, truth)
    assert m.missed.size == 0 and m.false.size == 0
    assert any("periodo observado" in w for w in stats.warnings)


# ---------------------------------------------------------------------- live advance()
def test_advance_from_realtime_producer_thread() -> None:
    x, truth = synth_watch(snr_db=25, beat_error_ms=0.4, seconds=8, seed=37)
    offline = beat_array(detect(x, FS, 28800, chunk=None)[0])
    stream = SampleStream(4 * FS, FS)
    det = BeatDetector(BeatDetectorConfig(sample_rate=FS, bph=28800))
    done = threading.Event()

    def producer() -> None:
        block = 480  # 10 ms blocks, paced 20x faster than real time
        for i in range(0, x.size, block):
            stream.write(x[i : i + block])
            time.sleep(0.0005)
        done.set()

    th = threading.Thread(target=producer)
    th.start()
    got = []
    while not done.is_set():
        got += det.advance(stream)
        time.sleep(0.002)
    th.join()
    got += det.advance(stream)
    got += det.flush()
    assert det.stats.gaps == 0
    np.testing.assert_allclose(beat_array(got), offline, atol=1e-6, rtol=0)


# ---------------------------------------------------------------------- speed
def test_realtime_factor_at_48k() -> None:
    x, _ = synth_watch(snr_db=25, seconds=20, seed=38, **REALISM)
    t0 = time.perf_counter()
    detect(x, FS, 28800)
    factor = 20.0 / (time.perf_counter() - t0)
    assert factor >= 5.0, f"only {factor:.1f}x real time"  # anti-flaky CI bound; ~100x locally
