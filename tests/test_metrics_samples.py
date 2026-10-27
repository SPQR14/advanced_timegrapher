"""Layer 3 on the real recordings of ``docs/samples/`` (skipped when absent).

References: ``docs/samples/tests.csv`` (tg 0.8.0 and Watch Accuracy Meter; same microphone,
room, dial up, full wind) and, for the first H-50 take, the tg values of
``docs/fases-nucleo.md``. They are live measurements, not of these files.

Acceptance band = [min, max] of the references +- (1.5 s/d, 0.15 ms, 12°). "Not worse than
the phase-2 prototype": the distance to the band may not grow by more than 2 statistical
sigmas (block jackknife for the rate, covariance for the beat error) or 2° of amplitude.
See docs/fase3_resultados.md, section 4.2.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

import numpy as np
import pytest

from adv_tg.core.beat_detector import BeatDetector, BeatDetectorConfig
from adv_tg.core.metrics import MetricsConfig, MetricsTracker
from adv_tg.tools.measure import MeasureResult, measure_file
from adv_tg.utils.audio_files import read_audio, select_channel

SAMPLES = Path(__file__).resolve().parents[1] / "docs" / "samples"
TOL = (1.5, 0.15, 12.0)

# file -> (BPH, LA, CSV caliber name or None, phase-2 prototype rate / BE / amplitude)
FILES = {
    "miyota_9015.flac": (28800, 51.0, "Miyota 9015", (8.06, 0.15, 293.0)),
    "hamilton_h50new.flac": (21600, 50.0, "Hamilton H-50", (8.08, 0.36, 297.0)),
    "hamilton_h50.flac": (21600, 50.0, None, (11.31, 0.25, 291.0)),
    "4r36.flac": (21600, 53.0, "Seko 4R36", (-3.15, 0.34, 269.0)),
}
H50_FIRST_TAKE_TG = ((10.0, 10.0), (0.2, 0.2), (280.0, 280.0))


def _references() -> dict[str, tuple[tuple[float, float], ...]]:
    path = SAMPLES / "tests.csv"
    rows: dict[str, list[tuple[float, float, float]]] = {}
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if not r.get("caliber"):
                continue
            rows.setdefault(r["caliber"].strip(), []).append(
                (float(r["rate"]), float(r["beat error"]), float(r["amplitude"]))
            )
    return {
        name: tuple((min(v[i] for v in vals), max(v[i] for v in vals)) for i in range(3))
        for name, vals in rows.items()
    }


def _band_distance(value: float, band: tuple[float, float]) -> float:
    lo, hi = band
    return 0.0 if lo <= value <= hi else min(abs(value - lo), abs(value - hi))


_cache: dict[str, MeasureResult] = {}


def _measure(name: str, quartz: bool = False, bph: float = 57600) -> MeasureResult:
    if name not in _cache:
        if quartz:
            _cache[name] = measure_file(SAMPLES / name, bph, quartz=True)
        else:
            bph, la, _, _ = FILES[name]
            _cache[name] = measure_file(SAMPLES / name, bph, la)
    return _cache[name]


def _need(name: str) -> None:
    if not (SAMPLES / name).is_file():
        pytest.skip(f"no está {SAMPLES / name}")
    if not (SAMPLES / "tests.csv").is_file():
        pytest.skip("no está docs/samples/tests.csv")


def _bands(name: str) -> tuple[tuple[float, float], ...]:
    caliber = FILES[name][2]
    return H50_FIRST_TAKE_TG if caliber is None else _references()[caliber]


@pytest.mark.parametrize("name", list(FILES))
def test_beat_error_and_amplitude_within_reference_band(name: str) -> None:
    _need(name)
    m = _measure(name).metrics
    _, be_band, amp_band = _bands(name)
    assert m.beat_error_point in ("p1", "p1_beat")
    assert _band_distance(m.beat_error_ms, be_band) <= TOL[1], m.beat_error_ms
    assert _band_distance(m.amplitude_deg, amp_band) <= TOL[2], m.amplitude_deg


@pytest.mark.parametrize("name", [n for n in FILES if n != "4r36.flac"])
def test_rate_within_reference_band(name: str) -> None:
    _need(name)
    m = _measure(name).metrics
    assert _band_distance(m.rate_s_per_day, _bands(name)[0]) <= TOL[0], m.rate_s_per_day


@pytest.mark.xfail(
    strict=True,
    reason=(
        "la grabación del 4R36 tiene marcha negativa según todos los estimadores (P1, P2, P3, "
        "t_fine, autocorrelación, cronocomparador independiente) y tg/WAM midieron +3/+4 en "
        "otro momento; sin tendencia significativa dentro de la toma. Ver "
        "docs/fase3_resultados.md, 6.4"
    ),
)
def test_4r36_rate_within_reference_band() -> None:
    _need("4r36.flac")
    m = _measure("4r36.flac").metrics
    assert _band_distance(m.rate_s_per_day, _bands("4r36.flac")[0]) <= TOL[0]


@pytest.mark.parametrize("name", list(FILES))
def test_not_worse_than_the_phase2_prototype(name: str) -> None:
    _need(name)
    m = _measure(name).metrics
    bands = _bands(name)
    proto = FILES[name][3]
    eps = (
        2.0 * max(m.rate_sigma_block_s_per_day, m.rate_sigma_s_per_day),
        2.0 * m.beat_error_sigma_ms,
        2.0,
    )
    new = (m.rate_s_per_day, m.beat_error_ms, m.amplitude_deg)
    # A beat error measured on each beat's own P1 is a different (more precise) estimator than
    # the prototype's: only the reference band applies to it (test above). 4R36: 0.38 ms, the
    # same as timing every P1 front directly (0.37 ms, 25 µs scatter); prototype 0.34.
    checked = (0, 2) if m.beat_error_point == "p1_beat" else (0, 1, 2)
    for i in checked:
        assert _band_distance(new[i], bands[i]) <= _band_distance(proto[i], bands[i]) + eps[i], (
            i,
            new[i],
            proto[i],
        )


def test_bulova_quartz_reproduces_the_card_drift() -> None:
    _need("bulova_nm10.flac")
    m = _measure("bulova_nm10.flac", quartz=True, bph=57600).metrics
    assert m.rate_s_per_day == pytest.approx(-0.72, abs=0.3)
    assert math.isnan(m.beat_error_ms) and math.isnan(m.amplitude_deg)
    assert not m.rate_trend_flag


# Quartz references: the rate a quartz shows here is the sound-card drift (+8.3 ppm according
# to tg, i.e. -0.72 s/d) plus the quartz's own error, specified as +-15 s/month (0.49 s/d)
CARD_DRIFT_S_PER_DAY = -0.72
QUARTZ_TOL_S_PER_DAY = 15.0 / 30.4


@pytest.mark.parametrize(("name", "bph"), [("tmi_vh31.flac", 14400), ("citizen_H874.flac", 3600)])
def test_quartz_reference_rate(name: str, bph: float) -> None:
    """|rate - card drift| <= quartz specification + 2 x the reported uncertainty."""
    if not (SAMPLES / name).is_file():
        pytest.skip(f"no está {SAMPLES / name}")
    m = _measure(name, quartz=True, bph=bph).metrics
    assert math.isfinite(m.rate_s_per_day)
    bound = QUARTZ_TOL_S_PER_DAY + 2.0 * m.rate_uncertainty_s_per_day
    assert abs(m.rate_s_per_day - CARD_DRIFT_S_PER_DAY) <= bound, (m.rate_s_per_day, bound)
    assert math.isnan(m.beat_error_ms) and math.isnan(m.amplitude_deg)


@pytest.mark.parametrize("name", ["miyota_9015.flac", "hamilton_h50new.flac"])
def test_clean_watches_raise_no_trend_warning(name: str) -> None:
    _need(name)
    assert not _measure(name).metrics.rate_trend_flag


def _windows(name: str, span_s: float, step_s: float = 5.0) -> list:
    bph, la, _, _ = FILES[name]
    data = read_audio(SAMPLES / name)
    x = select_channel(data)
    fs = data.sample_rate
    det = BeatDetector(BeatDetectorConfig(sample_rate=fs, bph=bph, lift_angle_deg=la))
    tracker = MetricsTracker(MetricsConfig(sample_rate=fs, bph=bph, lift_angle_deg=la), span_s)
    step = int(step_s * fs)
    out = []
    for i in range(0, x.size, 4096):
        tracker.add(det.process(x[i : i + 4096], i), det.templates())
        end = i + 4096
        if end // step != i // step and end >= span_s * fs:
            out.append(tracker.result())
    return out


@pytest.mark.parametrize("name", ["miyota_9015.flac", "hamilton_h50new.flac"])
def test_stability_30s_windows_every_5s(name: str) -> None:
    """Criterion of docs/fase3_resultados.md: 30 s windows shifted 5 s vary <= 2 s/d,
    0.1 ms and 10°. Needs >= 2 windows, i.e. takes of >= ~40 s."""
    _need(name)
    res = _windows(name, 30.0)
    if len(res) < 2:
        pytest.skip(f"toma demasiado corta para ventanas de 30 s cada 5 s ({len(res)} ventana)")
    rate = np.array([r.rate_s_per_day for r in res])
    be = np.array([r.beat_error_ms for r in res])
    amp = np.array([r.amplitude_deg for r in res])
    assert np.ptp(rate) <= 2.0 and np.ptp(be) <= 0.1 and np.ptp(amp) <= 10.0


@pytest.mark.parametrize("name", ["miyota_9015.flac", "hamilton_h50new.flac"])
def test_stability_20s_windows_every_5s(name: str) -> None:
    """Adaptation to the current 30 s takes: 20 s windows every 5 s. Rate (2 s/d) and
    amplitude (10°) keep the criterion; the beat error of a 20 s window is noisier (measured
    variation 0.14 ms), so it is bounded at 0.2 ms here (the 0.1 ms criterion is for 30 s)."""
    _need(name)
    res = _windows(name, 20.0)
    assert len(res) >= 2
    rate = np.array([r.rate_s_per_day for r in res])
    be = np.array([r.beat_error_ms for r in res])
    amp = np.array([r.amplitude_deg for r in res])
    assert np.ptp(rate) <= 2.0
    assert np.ptp(amp) <= 10.0
    assert np.ptp(be) <= 0.2


def test_seiko_4r34_gmt_beat_error() -> None:
    """``nh34.flac``: Seiko 4R34 (GMT), 21600 A/h, LA 53°, 130 s with the CM18, full wind.

    Weishi 1900 within 5 min (``tests.csv``): -4..+10 s/d, 241-270°, 0.0-0.3 ms. Each beat's
    P1 front times this take to 25-41 µs and gives 1.18 ms of beat error (half the difference
    between the tic->tac and tac->tic intervals); the Weishi figure is not reproducible on this
    file, so the beat error is checked against that direct measurement. Before the per-beat
    P1, the template P1 read 3.07 ms (the tic drop wanders ~3 ms after a clean unlock).
    """
    name = "nh34.flac"
    if not (SAMPLES / name).is_file():
        pytest.skip(f"no está {SAMPLES / name}")
    m = measure_file(SAMPLES / name, 21600, 53.0).metrics
    assert m.beat_error_point == "p1_beat"
    assert m.beat_error_ms == pytest.approx(1.18, abs=0.35)
    assert _band_distance(m.rate_s_per_day, (-4.0, 10.0)) <= TOL[0]
    assert _band_distance(m.amplitude_deg, (241.0, 270.0)) <= TOL[2]


def test_seagull_st1901_beat_error_point() -> None:
    """``SeagullST1901.flac`` (21600 A/h, 30 s; Weishi 1900 and WAM: +1 s/d, 0.2 ms).

    P1 is weak (clicks pass for it in some beats) and the drop is clean. At the drop the
    tic/tac asymmetry is 0.20 ms, the figure of both instruments; at P1, where this app
    measures the beat error, it is ~0.4 ms because tic and tac have different lift times
    (8.80 vs 8.63 ms). The noisy per-beat P1 must not replace the template P1. The lift angle
    of the ST19 is not confirmed: the amplitude is not checked.
    """
    name = "SeagullST1901.flac"
    if not (SAMPLES / name).is_file():
        pytest.skip(f"no está {SAMPLES / name}")
    from adv_tg.core.metrics import beat_error_from_fit, fit_parity_weighted, select_beats

    r = measure_file(SAMPLES / name, 21600, 52.0)
    m = r.metrics
    assert m.beat_error_point == "p1"
    assert m.beat_error_ms == pytest.approx(0.39, abs=0.1)
    k, t, _ = select_beats(r.beats)
    at_drop = beat_error_from_fit(fit_parity_weighted(k, t), r.sample_rate)[0]
    assert at_drop == pytest.approx(0.2, abs=0.05)
    # uncalibrated rate (the CM18 runs ~+9 ppm: ~-0.8 s/d) vs +1 s/d live, in 30 s
    assert m.rate_s_per_day == pytest.approx(1.0, abs=3.0)


def test_second_bulova_take_reproduces_the_card_drift() -> None:
    """``boluva_nm10_new.flac``: another 300-s take of the Bulova, quieter (peak -12.7 dBFS).

    Same card drift as the first take within 1 ppm. ~1.5 % of the steps are too weak to time
    (about 0.65 x a normal step over a ~12 dB floor): they are left as gaps, never invented.
    The 10-s windows show a +2.5..+4.7 s/d step every 60 s: the watch, not the measurement.
    """
    name = "boluva_nm10_new.flac"
    if not (SAMPLES / name).is_file():
        pytest.skip(f"no está {SAMPLES / name}")
    r = measure_file(SAMPLES / name, 57600, quartz=True)
    assert r.metrics.rate_s_per_day == pytest.approx(-0.72, abs=0.3)
    assert len(r.beats) >= 0.97 * 300 * 16
