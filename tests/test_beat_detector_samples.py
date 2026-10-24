"""Beat detector on the real recordings in ``docs/samples/`` (skipped when absent).

Calibres come from the file name (table of ``adv_tg.tools.check_samples``):

* c07 / h50 / hamilton (ETA C07, 21600 BPH, LA 50°): weak P1;
* 9015 / miyota (28800 BPH, LA 51°): the "normal" case;
* nm10 / bulova (quartz, 16 beats/s): informational, never fails.

Criteria for the mechanical ones: count within +-1 % of the beats expected from the first
detected beat (the lock/backfill time is discounted), tic/tac alternate without parity
jumps, median interval within 1 % of nominal, P3 in >= 95 % of the beats, median t_lift in
the physical range (when P1 is resolved) and chunk invariance.

Fine timing (per-parity templates): ``t_fine`` in >= 80 % of the beats and a tic/tac offset
at the template P1 onsets below 0.5 ms. Before the templates, the two-lobe drop of the H-50
tac gave ~1.5 ms there while tg measures 0.3 ms (see docs/resultados_pruebas_fase2.md).
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from adv_tg.core.beat_types import beat_period_s
from adv_tg.tools.check_samples import check_file, discover, resolve_config
from adv_tg.tools.detect_beats import analyze_file

SAMPLES_DIR = Path(__file__).resolve().parents[1] / "docs" / "samples"


def _files(informational: bool) -> list[Path]:
    if not SAMPLES_DIR.is_dir():
        return []
    out = []
    for p in discover(SAMPLES_DIR):
        cfg = resolve_config(p)
        if cfg is not None and cfg.informational == informational:
            out.append(p)
    return out


MECHANICAL = _files(False)
QUARTZ = _files(True)


@pytest.mark.parametrize("path", MECHANICAL or [None], ids=lambda p: p.name if p else "none")
def test_real_mechanical_recording(path: Path | None) -> None:
    if path is None:
        pytest.skip(f"no hay grabaciones de c07/9015 en {SAMPLES_DIR}")
    cfg = resolve_config(path)
    result = check_file(path, cfg)
    s = result.summary
    assert s is not None, result.reasons
    assert abs(s.found - result.expected_from_first) <= max(0.01 * result.expected_from_first, 1.5)
    assert s.p3_pct >= 95.0
    assert s.parity_ok is not False
    assert result.invariant
    assert s.fine_pct >= 80.0
    assert s.fine_beat_offset_ms < 0.5
    if s.p1_pct >= 50.0:
        assert math.isfinite(s.t_lift_median_ms)
        assert result.verdict == "OK", result.reasons
    _, beats = analyze_file(path, cfg.bph, cfg.lift_angle)
    ref = np.array([b.reference for b in beats])
    ks = np.array([b.k for b in beats])
    one = np.diff(ks) == 1
    interval = np.median(np.diff(ref)[one]) / s.sample_rate
    assert interval == pytest.approx(beat_period_s(cfg.bph), rel=0.01)


@pytest.mark.parametrize("path", QUARTZ or [None], ids=lambda p: p.name if p else "none")
def test_real_quartz_recording_is_informational(path: Path | None) -> None:
    if path is None:
        pytest.skip(f"no hay grabaciones de nm10 en {SAMPLES_DIR}")
    result = check_file(path, resolve_config(path))
    assert result.verdict in ("INFO", "ERROR")  # never fails the suite
