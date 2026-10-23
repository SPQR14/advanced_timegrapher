"""Data models for horological configuration, beat events, and telemetry results."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from adv_tg.utils.logger_setup import LoggerSetup

logger = LoggerSetup.setup_logger(__name__)


@dataclass(slots=True)
class WatchConfig:
    """Configuration parameters for a mechanical watch movement.

    Attributes:
        bph: Beats per hour (alternations per hour, A/h), e.g., 18000, 21600, 28800, 36000. (Default 21600)
        lift_angle: Escapement lift angle in degrees (typically 44.0 to 58.0, standard 52.0).
        sample_rate: Audio sampling rate in Hz (e.g., 96000 or 192000,
        default set at 48000).
    """

    bph: int = 21600
    lift_angle: float = 52.0
    sample_rate: int = 48000

    def __post_init__(self) -> None:
        if self.bph <= 0:
            logger.error("Invalid WatchConfig bph=%d (must be positive)", self.bph)
            raise ValueError("bph must be a positive integer")
        if self.lift_angle <= 0.0 or self.lift_angle >= 180.0:
            logger.error("Invalid WatchConfig lift_angle=%.2f", self.lift_angle)
            raise ValueError("lift_angle must be in (0, 180)")
        if self.sample_rate <= 0:
            logger.error("Invalid WatchConfig sample_rate=%d", self.sample_rate)
            raise ValueError("sample_rate must be a positive integer")
        logger.info(
            "WatchConfig initialized with bph=%d, lift_angle=%.1f, sample_rate=%d",
            self.bph,
            self.lift_angle,
            self.sample_rate,
        )

    @property
    def nominal_beat_period(self) -> float:
        """Return the nominal time interval between consecutive beats in seconds."""
        return 3600.0 / float(self.bph)

    @property
    def nominal_oscillation_frequency(self) -> float:
        """Return the nominal full oscillation frequency (Hz), i.e., BPH / 7200."""
        return float(self.bph) / 7200.0


@dataclass(slots=True)
class BeatEvent:
    """Acoustic event representing a single escapement alternation (tick or tock).

    Attributes:
        timestamp_p1: Timestamp of P1 (unlocking impulse) in seconds.
        timestamp_p2: Timestamp of P2 (impulse plane impact) in seconds.
        timestamp_p3: Timestamp of P3 (drop/locking impact) in seconds.
        peak_energy: Peak acoustic energy within the beat window.
        raw_waveform: Extracted filtered waveform segment for this beat.
    """

    timestamp_p1: float
    timestamp_p2: float
    timestamp_p3: float
    peak_energy: float
    raw_waveform: np.ndarray


@dataclass(slots=True)
class TelemetryResult:
    """Computed horological telemetry over a sequence of detected beats.

    Attributes:
        rate_spd: Rate deviation in seconds per day (s/d).
        beat_error_ms: Beat error (asymmetry between tick and tock) in milliseconds (ms).
        amplitude_deg: Balance wheel oscillation amplitude in degrees.
        confidence: Detection confidence score in the range [0.0, 1.0].
    """

    rate_spd: float
    beat_error_ms: float
    amplitude_deg: float
    confidence: float


@dataclass(slots=True)
class BeatTelemetry:
    """Structured horological telemetry and acoustic envelope from DSPEngine.

    Attributes:
        rate_spd: Rate deviation in seconds per day (s/d).
        beat_error_ms: Beat error (asymmetry between tick and tock) in milliseconds (ms).
        amplitude_deg: Balance wheel oscillation amplitude in degrees.
        beat_times_sec: Detected beat timestamps in seconds.
        envelope: Smoothed analytic amplitude envelope of the signal.
        confidence: Detection confidence score in the range [0.0, 1.0].
        snr_db: Acoustic Signal-to-Noise Ratio in decibels (dB).
        beats: Detected BeatEvent instances with P1, P2, and P3 timestamps.
        micro_envelope: High-resolution sub-millisecond envelope for P1/P2/P3 inspection.
        filtered_signal: High-pass filtered acoustic waveform.
        effective_fs: Effective sampling frequency in Hz used during analysis.
    """

    rate_spd: float
    beat_error_ms: float
    amplitude_deg: float
    beat_times_sec: NDArray[np.float64]
    envelope: NDArray[np.float64]
    confidence: float
    snr_db: float = 0.0
    beats: list[BeatEvent] = field(default_factory=list)
    micro_envelope: NDArray[np.float64] = field(
        default_factory=lambda: np.empty(0, dtype=np.float64)
    )
    filtered_signal: NDArray[np.float64] = field(
        default_factory=lambda: np.empty(0, dtype=np.float64)
    )
    effective_fs: float = 48000.0
