"""Run the golden protocol (see ``protocol.py``) with the current implementation."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from adv_tg.core.adc_clock import AdcDrift
from adv_tg.core.beat_detector import BeatDetector, BeatDetectorConfig
from adv_tg.core.metrics import MetricsConfig, compute_metrics
from adv_tg.core.pps import PpsDetector
from adv_tg.core.sample_stream import SampleStream
from adv_tg.core.session import MeasurementSession, SessionConfig
from adv_tg.utils.audio_files import read_mono
from tests.golden import protocol as P

SAMPLES = Path(__file__).resolve().parents[2] / "docs" / "samples"


def plain(v: object) -> object:
    """JSON-friendly value: NaN/inf -> None, numpy scalars -> Python, enums -> int."""
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        f = float(v)
        return f if math.isfinite(f) else None
    return v


def read(name: str) -> tuple[int, np.ndarray]:
    fs, x = read_mono(SAMPLES / name)
    return int(fs), np.asarray(x, dtype=np.float64)


def run_batch(name: str) -> tuple[list[dict], dict]:
    bph, la, quartz = P.WATCHES[name]
    fs, x = read(name)
    det = BeatDetector(
        BeatDetectorConfig(sample_rate=fs, bph=bph, lift_angle_deg=la or 52.0, quartz=quartz)
    )
    beats = []
    for a in range(0, x.size, P.BATCH_CHUNK):
        beats += det.process(x[a : a + P.BATCH_CHUNK], a)
    beats += det.flush()
    m = compute_metrics(
        beats,
        det.templates(),
        MetricsConfig(sample_rate=fs, bph=bph, lift_angle_deg=la, quartz=quartz),
    )
    rows = [{f: plain(getattr(b, f)) for f in P.BEAT_FIELDS} for b in beats]
    for r, b in zip(rows, beats, strict=True):
        r["flags"] = int(b.flags)
    metrics = {f: plain(getattr(m, f)) for f in P.METRIC_FIELDS}
    return rows, metrics


def run_live(name: str) -> list[dict]:
    bph, la, quartz = P.WATCHES[name]
    fs, x = read(name)
    x32 = x[: P.LIVE_SECONDS * fs].astype(np.float32)
    ppm, sigma, systematic = P.LIVE_CALIBRATION
    cfg = SessionConfig(
        sample_rate=fs,
        bph=bph,
        lift_angle_deg=la,
        quartz=quartz,
        live_clock=False,
        calibration=AdcDrift.manual(ppm, systematic),
        calibration_source="golden",
    )
    assert sigma == 0.0  # AdcDrift.manual has no statistical sigma
    session = MeasurementSession(cfg)
    stream = SampleStream(P.LIVE_CAPACITY_S * fs, fs)
    every = P.live_every(fs)
    out = []
    for i, a in enumerate(range(0, x32.size, P.LIVE_BLOCK)):
        b = min(x32.size, a + P.LIVE_BLOCK)
        stamp = P.stamp_ns(b, fs)
        stream.write(x32[a:b], host_ns=stamp, wall_ns=stamp)
        if (i + 1) % every == 0 or b == x32.size:
            s = session.update(stream)
            snap = {f: plain(getattr(s, f)) for f in P.SNAPSHOT_FIELDS}
            snap["display"] = {f: plain(getattr(s.display, f)) for f in P.DISPLAY_FIELDS}
            snap["metrics"] = {f: plain(getattr(s.metrics, f)) for f in P.METRIC_FIELDS}
            snap["tape_len"] = int(s.tape_time_s.size)
            snap["waveform_len"] = int(s.waveform.size)
            out.append(snap)
    return out


def run_pps(name: str) -> list[dict]:
    fs, x = read(name)
    det = PpsDetector(fs)
    step = P.PPS_CHUNK_S * fs
    pulses = []
    for a in range(0, x.size, step):
        pulses += det.process(x[a : a + step], a)
    return [
        {"index": plain(p.index), "snr_db": plain(p.snr_db), "peak": plain(p.peak)} for p in pulses
    ]
