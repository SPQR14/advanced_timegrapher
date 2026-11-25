"""Golden files of v2.0.1 (migration plan, phase 0): the figures must not change.

The files are the outputs of the native engine; only rounding-level differences are allowed
(FMA / libm of each compiler and CPU). Skipped in pure-numpy mode: the numpy reference is
checked against the engine by the parity tests (tests/test_native_*.py); the measured
numpy-vs-native differences on these recordings are documented in ``protocol.py``.
Protocol and tolerances: ``tests/golden/protocol.py``; regenerate with
``scripts/make_golden.py`` only for a validated change of figures.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from adv_tg import native_bridge as nb
from tests.golden import protocol as P
from tests.golden import runner as R

GOLDEN = Path(__file__).resolve().parent / "golden"
TOL = P.NATIVE
native_only = pytest.mark.skipif(
    not nb.is_native_available(), reason="archivos dorados = salidas del motor nativo"
)


def _nan(v: object) -> object:
    return float("nan") if v is None else v


def _compare(where: str, got: dict, want: dict, errors: list[str]) -> None:
    for k, w in want.items():
        g = got.get(k)
        if isinstance(w, dict):
            _compare(f"{where}.{k}", g or {}, w, errors)
        elif not P.close(k, _nan(g), _nan(w), TOL):
            errors.append(f"{where}.{k}: {g!r} != {w!r}")


def _read_csv(path: Path) -> list[dict]:
    with path.open() as f:
        rows = list(csv.DictReader(f))
    return [{k: (int(v) if k in ("k", "flags", "n_subevents") else float(v)) for k, v in r.items()}
            for r in rows]  # fmt: skip


def _skip_if_missing(name: str) -> None:
    if not (R.SAMPLES / name).exists():
        pytest.skip(f"falta docs/samples/{name}")


@native_only
@pytest.mark.parametrize("name", list(P.WATCHES))
def test_batch_matches_golden(name: str) -> None:
    _skip_if_missing(name)
    stem = Path(name).stem
    beats, metrics = R.run_batch(name)
    want = _read_csv(GOLDEN / stem / "beats.csv")
    assert [b["k"] for b in beats] == [w["k"] for w in want], "different beats (k)"
    errors: list[str] = []
    for i, (g, w) in enumerate(zip(beats, want, strict=True)):
        _compare(f"beat[{i}]", g, w, errors)
    _compare("metrics", metrics, json.loads((GOLDEN / stem / "metrics.json").read_text()), errors)
    assert not errors, f"{len(errors)} differences: " + "; ".join(errors[:10])


@native_only
@pytest.mark.parametrize("name", list(P.WATCHES))
def test_live_matches_golden(name: str) -> None:
    _skip_if_missing(name)
    got = R.run_live(name)
    want = [json.loads(ln) for ln in (GOLDEN / Path(name).stem / "live.jsonl").open()]
    assert len(got) == len(want)
    errors: list[str] = []
    for i, (g, w) in enumerate(zip(got, want, strict=True)):
        _compare(f"update[{i}]", g, w, errors)
    assert not errors, f"{len(errors)} differences: " + "; ".join(errors[:10])


@native_only
@pytest.mark.parametrize("name", P.PPS)
def test_pps_matches_golden(name: str) -> None:
    _skip_if_missing(name)
    got = R.run_pps(name)
    with (GOLDEN / Path(name).stem / "pulses.csv").open() as f:
        want = [{k: float(v) for k, v in r.items()} for r in csv.DictReader(f)]
    assert len(got) == len(want)
    errors: list[str] = []
    for i, (g, w) in enumerate(zip(got, want, strict=True)):
        if not P.close("t_fine", g["index"], w["index"], TOL):  # an instant, like a beat
            errors.append(f"pulse[{i}].index: {g['index']!r} != {w['index']!r}")
        for k in ("snr_db", "peak"):
            if not P.close(k, _nan(g[k]), w[k], TOL):
                errors.append(f"pulse[{i}].{k}: {g[k]!r} != {w[k]!r}")
    assert not errors, f"{len(errors)} differences: " + "; ".join(errors[:10])


def test_manifest_lists_every_file() -> None:
    manifest = json.loads((GOLDEN / "manifest.json").read_text())
    for rel in manifest["files"]:
        assert (GOLDEN / rel).is_file(), rel
