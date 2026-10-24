"""Helpers shared by the beat-detector tests: run the detector and match it to the truth."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np

from adv_tg.core.beat_detector import BeatDetector, BeatDetectorConfig, DetectorStats
from adv_tg.core.beat_types import Beat
from tests.synth import Truth


def detect(
    x: np.ndarray,
    fs: float,
    bph: float,
    *,
    chunk: int | None = 4096,
    lift_angle_deg: float = 52.0,
    **cfg,
) -> tuple[list[Beat], DetectorStats]:
    """Run a fresh detector over ``x`` in ``chunk`` blocks (``None`` = all at once)."""
    det = BeatDetector(
        BeatDetectorConfig(sample_rate=fs, bph=bph, lift_angle_deg=lift_angle_deg, **cfg)
    )
    logging.getLogger("adv_tg.core.beat_detector").setLevel(logging.WARNING)
    out: list[Beat] = []
    if chunk is None:
        out += det.process(x, 0)
    else:
        for i in range(0, x.size, chunk):
            out += det.process(x[i : i + chunk], i)
    out += det.flush()
    return out, det.stats


@dataclass
class Match:
    """Detections paired with the truth."""

    truth_idx: np.ndarray  # matched truth beats
    det_idx: np.ndarray  # their detections
    missed: np.ndarray  # truth beats without detection
    false: np.ndarray  # detections without truth
    err_p3: np.ndarray  # p3 - true p3 (NaN where p3 was not resolved)
    err_p1: np.ndarray  # p1 - true p1 (NaN where p1 was not resolved)
    k_offsets: np.ndarray  # k_detected - k_true for every match

    @property
    def k_consistent(self) -> bool:
        return self.k_offsets.size > 0 and bool(np.all(self.k_offsets == self.k_offsets[0]))


def match(beats: list[Beat], truth: Truth, tol_ms: float = 1.0) -> Match:
    """Pair each true beat with the detection whose reference instant is within ``tol_ms``.

    The reference of a detection is P3, or P1 + true t_lift when P3 was not resolved.
    """
    fs = truth.fs
    ref = np.array([b.p3 if not math.isnan(b.p3) else b.p1 + truth.t_lift_samples for b in beats])
    tol = tol_ms * 1e-3 * fs
    used = np.zeros(len(beats), dtype=bool)
    t_idx, d_idx = [], []
    for i, t in enumerate(truth.p3):
        if ref.size == 0:
            break
        j = int(np.argmin(np.abs(ref - t)))
        if abs(ref[j] - t) <= tol and not used[j]:
            used[j] = True
            t_idx.append(i)
            d_idx.append(j)
    t_idx_a = np.array(t_idx, dtype=np.int64)
    d_idx_a = np.array(d_idx, dtype=np.int64)
    missed = np.setdiff1d(np.arange(truth.p3.size), t_idx_a)
    false = np.flatnonzero(~used)
    p3 = np.array([beats[j].p3 for j in d_idx_a])
    p1 = np.array([beats[j].p1 for j in d_idx_a])
    ks = np.array([beats[j].k for j in d_idx_a], dtype=np.int64)
    return Match(
        truth_idx=t_idx_a,
        det_idx=d_idx_a,
        missed=missed,
        false=false,
        err_p3=p3 - truth.p3[t_idx_a] if t_idx_a.size else np.zeros(0),
        err_p1=p1 - truth.p1[t_idx_a] if t_idx_a.size else np.zeros(0),
        k_offsets=ks - truth.k[t_idx_a] if t_idx_a.size else np.zeros(0, np.int64),
    )


def beat_array(beats: list[Beat]) -> np.ndarray:
    """``(k, p1, p2, p3, flags, t_fine)`` rows, NaN replaced by -1 for exact comparisons."""
    rows = [[b.k, b.p1, b.p2, b.p3, int(b.flags), b.t_fine] for b in beats]
    return np.nan_to_num(np.array(rows, dtype=np.float64).reshape(-1, 6), nan=-1.0)
