"""Physical definitions, configuration validation and the ``Beat`` record."""

from __future__ import annotations

import math

import pytest

from adv_tg.core.beat_detector import BeatDetector, BeatDetectorConfig
from adv_tg.core.beat_types import (
    Beat,
    BeatFlags,
    amplitude_from_lift_time,
    balance_frequency_hz,
    beat_period_s,
    lift_time_s,
    lift_time_window_s,
)


@pytest.mark.parametrize(
    "bph, la, amp, expected_ms",
    [
        (28800, 51, 300, 6.8),
        (28800, 51, 100, 20.5),
        (21600, 50, 280, 9.5),
        (21600, 50, 360, 7.4),
        (21600, 50, 100, 26.8),
    ],
)
def test_lift_time_examples(bph: int, la: float, amp: float, expected_ms: float) -> None:
    assert lift_time_s(amp, la, bph) * 1e3 == pytest.approx(expected_ms, abs=0.1)
    assert amplitude_from_lift_time(lift_time_s(amp, la, bph), la, bph) == pytest.approx(amp)


def test_lift_window_uses_amplitude_range() -> None:
    lo, hi = lift_time_window_s(21600, 50, (100.0, 360.0))
    assert lo * 1e3 == pytest.approx(7.4, abs=0.1)
    assert hi * 1e3 == pytest.approx(26.8, abs=0.1)
    with pytest.raises(ValueError):
        lift_time_window_s(21600, 50, (300.0, 200.0))


def test_basic_definitions() -> None:
    assert balance_frequency_hz(28800) == 4.0
    assert beat_period_s(21600) == pytest.approx(1 / 6)
    assert math.isnan(amplitude_from_lift_time(0.0, 52, 28800))
    with pytest.raises(ValueError):
        lift_time_s(20, 52, 28800)  # amplitude below LA/2 is impossible
    with pytest.raises(ValueError):
        beat_period_s(0)


def test_beat_reference_and_lift() -> None:
    nan = math.nan
    b = Beat(1, 100.0, 140.0, 190.0, 3, -6.0, 25.0, 0.5, 0.9, BeatFlags.NONE)
    assert b.reference == 190.0 and b.t_lift_samples == 90.0
    weak = Beat(2, 100.0, nan, nan, 1, -6.0, 25.0, 0.5, 0.5, BeatFlags.P2_MISSING)
    assert weak.reference == 100.0 and math.isnan(weak.t_lift_samples)
    flags = BeatFlags.P1_MISSING | BeatFlags.AFTER_GAP
    assert flags & BeatFlags.AFTER_GAP and not flags & BeatFlags.SATURATED


@pytest.mark.parametrize(
    "kw",
    [
        {"sample_rate": 4000},
        {"bph": 0},
        {"lift_angle_deg": 0},
        {"amplitude_range_deg": (20.0, 400.0)},
        {"amplitude_range_deg": (300.0, 200.0)},
        {"band_hz": (1000.0, 30000.0)},
        {"threshold_lo": 7.0},
        {"onset_fraction": 1.5},
        {"phase_tolerance": 0.5},
        {"model_beats": 3},
        {"grid_step_s": 0.0},
        {"split_ratio": 1.0},
    ],
)
def test_config_validation(kw: dict) -> None:
    base = {"sample_rate": 48000, "bph": 28800}
    with pytest.raises(ValueError):
        BeatDetectorConfig(**{**base, **kw})


def test_config_defaults() -> None:
    c = BeatDetectorConfig(sample_rate=48000, bph=28800)
    assert c.band_hz == (1000.0, 15000.0)
    assert BeatDetectorConfig(sample_rate=22050, bph=28800).band_hz[1] == pytest.approx(9922.5)
    det = BeatDetector(c)
    # margin = 3 x filter memory + Hilbert half-length + smoothing half-length
    assert det.margin > 3 * det.filter_memory
    assert det.margin < 0.05 * 48000


def test_process_rejects_bad_input() -> None:
    det = BeatDetector(BeatDetectorConfig(sample_rate=48000, bph=28800))
    with pytest.raises(ValueError):
        det.process([[0.0, 1.0]], 0)
    det.process([0.0] * 100, 0)
    with pytest.raises(ValueError):
        det.process([0.0] * 10, 50)  # overlaps what was processed
    det.flush()
    with pytest.raises(RuntimeError):
        det.process([0.0], 100)
    det.reset()
    assert det.process([0.0] * 10, 0) == []
