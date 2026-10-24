"""Beat records and the physical definitions shared by every layer.

Physical definitions (one definition for the whole system):

* Balance frequency ``f = BPH / 7200`` Hz; beat period ``P = 3600 / BPH`` s.
* Each beat produces up to three sounds: **P1** = unlocking (onset of the first sub-event),
  **P2** = impulse (~0.45 * t_lift after P1), **P3** = drop (onset of the last sub-event).
  ``t_lift = P3 - P1``.
* Amplitude ``A = LA / (2 sin(pi f t_lift))``, so ``t_lift = asin(LA / (2A)) / (pi f)``.
  Layer 2 does NOT measure amplitude with it; it only bounds where P1 and P3 can be.
* The instant of a sub-event is its ONSET (sub-sample threshold crossing on the
  envelope), not its peak.
* Every instant is an ABSOLUTE SAMPLE INDEX (float64, sub-sample) in the ADC clock domain,
  as defined by ``SampleStream``; never wall-clock seconds nor window-relative indices.

Phase-model reference instant: **P3** when it was resolved, otherwise **P1**. P3 (the drop
of the pallet on the escape wheel) is normally the loudest and most stable sound, while P1
can be weak (e.g. ETA C07): anchoring the timing on P1 would fail on such calibres.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntFlag

__all__ = [
    "Beat",
    "BeatFlags",
    "amplitude_from_lift_time",
    "balance_frequency_hz",
    "beat_period_s",
    "lift_time_s",
    "lift_time_window_s",
]


def balance_frequency_hz(bph: float) -> float:
    """Balance frequency ``f = BPH / 7200`` in Hz."""
    if not bph > 0:
        raise ValueError(f"BPH debe ser positivo (recibido {bph})")
    return float(bph) / 7200.0


def beat_period_s(bph: float) -> float:
    """Beat period ``P = 3600 / BPH`` in seconds."""
    if not bph > 0:
        raise ValueError(f"BPH debe ser positivo (recibido {bph})")
    return 3600.0 / float(bph)


def lift_time_s(amplitude_deg: float, lift_angle_deg: float, bph: float) -> float:
    """Time between P1 and P3: ``t_lift = asin(LA / (2A)) / (pi f)``, in seconds.

    Raises:
        ValueError: If the amplitude is not larger than half the lift angle.
    """
    if not lift_angle_deg > 0:
        raise ValueError(f"el ángulo de levantamiento debe ser positivo ({lift_angle_deg})")
    ratio = float(lift_angle_deg) / (2.0 * float(amplitude_deg))
    if not 0.0 < ratio < 1.0:
        raise ValueError(
            f"amplitud {amplitude_deg}° imposible para un levantamiento de {lift_angle_deg}°"
        )
    return math.asin(ratio) / (math.pi * balance_frequency_hz(bph))


def amplitude_from_lift_time(t_lift_s: float, lift_angle_deg: float, bph: float) -> float:
    """Amplitude implied by a lift time: ``A = LA / (2 sin(pi f t_lift))`` in degrees.

    Returns ``nan`` when ``t_lift`` is outside ``(0, P/2)``.
    """
    s = math.sin(math.pi * balance_frequency_hz(bph) * float(t_lift_s))
    if not (t_lift_s > 0 and s > 0):
        return math.nan
    return float(lift_angle_deg) / (2.0 * s)


def lift_time_window_s(
    bph: float, lift_angle_deg: float, amplitude_range_deg: tuple[float, float]
) -> tuple[float, float]:
    """``(t_lift_min, t_lift_max)`` in seconds for an amplitude search range.

    The largest amplitude gives the shortest lift time and vice versa.
    """
    a_lo, a_hi = amplitude_range_deg
    if not 0 < a_lo < a_hi:
        raise ValueError(f"rango de amplitud no válido: {amplitude_range_deg}")
    return lift_time_s(a_hi, lift_angle_deg, bph), lift_time_s(a_lo, lift_angle_deg, bph)


class BeatFlags(IntFlag):
    """Quality flags of a ``Beat`` (combinable)."""

    NONE = 0
    P1_MISSING = 1  # no evidence of P1 in its physical window: p1 is NaN
    P2_MISSING = 2  # no evidence of P2: p2 is NaN
    P3_MISSING = 4  # no evidence of P3: p3 is NaN (the reference instant is then P1)
    WEAK = 8  # anchor SNR below the "weak" threshold
    SATURATED = 16  # |x| >= saturation level somewhere in the beat
    TIMING_OUTLIER = 32  # within the phase tolerance but far from the phase model
    AFTER_GAP = 64  # first beat after a data gap (stream overrun or digital silence)


@dataclass(frozen=True, slots=True)
class Beat:
    """One detected beat. All instants are absolute sample indices (float64).

    Attributes:
        k: Integer beat number since detection started. Robust to losses: missing beats
            leave gaps in ``k`` (``round(dt / P)`` against the phase model), so the parity
            of ``k`` identifies tic vs. tac.
        p1: Onset of P1 (unlocking), NaN if not resolved.
        p2: Onset of P2 (impulse), NaN if not resolved.
        p3: Onset of P3 (drop), NaN if not resolved.
        n_subevents: Sub-events found between the first and last resolved instant.
        peak_dbfs: Peak |x| of the raw signal over the beat, in dBFS.
        snr_db: Envelope peak of the anchor sub-event over the local noise floor, in dB.
        residual_samples: Reference instant (P3, else P1 shifted by the current t_lift
            estimate) minus the phase-model prediction BEFORE the model was updated.
        confidence: Heuristic quality in [0, 1] (see ``beat_detector``); not a calibrated
            probability.
        flags: ``BeatFlags``.
    """

    k: int
    p1: float
    p2: float
    p3: float
    n_subevents: int
    peak_dbfs: float
    snr_db: float
    residual_samples: float
    confidence: float
    flags: BeatFlags

    @property
    def reference(self) -> float:
        """Reference instant of the phase model: P3 if resolved, else P1."""
        return self.p1 if math.isnan(self.p3) else self.p3

    @property
    def t_lift_samples(self) -> float:
        """``p3 - p1`` in samples (NaN unless both are resolved)."""
        return self.p3 - self.p1
