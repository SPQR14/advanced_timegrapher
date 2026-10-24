"""Simulated watch signal and telemetry for MOCK mode (numpy only, no real DSP).

Nothing here analyses audio: values are *generated* from the configured watch
parameters so the GUI can be exercised before the measurement core exists.

Each beat is drawn as three short damped bursts (P1 unlock, P2 impulse, P3 drop, P3 the
largest) whose P1->P3 spacing follows the amplitude formula
``amplitude = lift / (2 * sin(pi * f0 * dt))``. Tic and tac alternate with slightly
different level and tone.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from adv_tg.utils.audio_files import write_audio

SECONDS_PER_DAY: float = 86400.0
PPM_TO_SPD: float = SECONDS_PER_DAY * 1e-6

# (position as a fraction of the P1->P3 interval, relative level, carrier Hz, decay s)
_BURSTS: tuple[tuple[float, float, float, float], ...] = (
    (0.00, 0.45, 7200.0, 0.00025),
    (0.35, 0.30, 5200.0, 0.00035),
    (1.00, 1.00, 4300.0, 0.00090),
)
_TOCK_GAIN: float = 0.85
_TOCK_TONE: float = 0.96


def beat_delta_t_s(amplitude_deg: float, lift_angle_deg: float, bph: int) -> float:
    """Return the P1->P3 interval (s) that corresponds to an amplitude and lift angle."""
    f0 = float(bph) / 7200.0
    ratio = min(0.999, max(1e-6, lift_angle_deg / (2.0 * max(amplitude_deg, 1.0))))
    return float(np.arcsin(ratio) / (np.pi * f0))


def beat_interval_s(bph: int, rate_spd: float) -> float:
    """Return the mean beat interval (s) of a watch running at ``rate_spd``."""
    return (3600.0 / float(bph)) / (1.0 + rate_spd / SECONDS_PER_DAY)


def add_beat(
    buf: NDArray[np.float64], fs: float, t_beat_s: float, dt_p3_s: float, is_tock: bool
) -> None:
    """Add one beat (P1, P2, P3 bursts) starting at ``t_beat_s`` into ``buf`` in place."""
    gain = _TOCK_GAIN if is_tock else 1.0
    tone = _TOCK_TONE if is_tock else 1.0
    n = len(buf)
    for frac, level, carrier, tau in _BURSTS:
        t0 = t_beat_s + frac * dt_p3_s
        lo = max(int(np.ceil(t0 * fs)), 0)
        hi = min(int(np.ceil((t0 + 8.0 * tau) * fs)), n)
        if lo >= hi:
            continue
        tt = np.arange(lo, hi, dtype=np.float64) / fs - t0
        buf[lo:hi] += gain * level * np.exp(-tt / tau) * np.sin(2.0 * np.pi * carrier * tone * tt)


def noise_sigma(snr_db: float) -> float:
    """Noise standard deviation for a unit-peak signal at ``snr_db``."""
    return float(10.0 ** (-snr_db / 20.0))


def beat_waveform(
    fs: float,
    dt_p3_s: float,
    is_tock: bool,
    snr_db: float,
    rng: np.random.Generator,
    pre_s: float = 0.0015,
    post_s: float = 0.006,
) -> tuple[NDArray[np.float64], float, float]:
    """Return ``(waveform, t_p1_s, t_p3_s)`` for one isolated beat (oscilloscope view)."""
    n = int(round((pre_s + dt_p3_s + post_s) * fs))
    buf = np.zeros(n, dtype=np.float64)
    add_beat(buf, fs, pre_s, dt_p3_s, is_tock)
    buf += rng.normal(0.0, noise_sigma(snr_db), size=n)
    return buf, pre_s, pre_s + dt_p3_s


def minmax_decimate(
    signal: NDArray[np.float64], fs: float, bins: int = 1000
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Peak-preserving decimation for plotting; returns ``(time_ms, values)``."""
    n = len(signal)
    if n <= 2 * bins:
        return np.arange(n, dtype=np.float64) / fs * 1000.0, signal.copy()
    size = n // bins
    trimmed = signal[: size * bins].reshape(bins, size)
    values = np.empty(2 * bins, dtype=np.float64)
    values[0::2] = trimmed.min(axis=1)
    values[1::2] = trimmed.max(axis=1)
    time_ms = np.linspace(0.0, size * bins / fs * 1000.0, 2 * bins)
    return time_ms, values


def generate_tick_train(
    duration_s: float,
    fs: int,
    bph: int,
    rate_spd: float = 0.0,
    beat_error_ms: float = 0.0,
    amplitude_deg: float = 280.0,
    lift_angle_deg: float = 52.0,
    snr_db: float = 32.0,
    seed: int | None = None,
) -> NDArray[np.float64]:
    """Generate a simple tic/tac train (impulse + decay) at the BPH frequency.

    Intervals alternate ``period + BE`` / ``period - BE``. Output peak is about 0.9.
    """
    rng = np.random.default_rng(seed)
    n = int(round(duration_s * fs))
    buf = np.zeros(n, dtype=np.float64)
    period = beat_interval_s(bph, rate_spd)
    be_s = beat_error_ms / 1000.0
    dt_p3 = beat_delta_t_s(amplitude_deg, lift_angle_deg, bph)
    t, k = 0.005, 0
    while t < duration_s:
        add_beat(buf, fs, t + float(rng.normal(0.0, 15e-6)), dt_p3, is_tock=bool(k % 2))
        t += period + (be_s if k % 2 == 0 else -be_s)
        k += 1
    buf += rng.normal(0.0, noise_sigma(snr_db), size=n)
    peak = float(np.max(np.abs(buf))) if n else 0.0
    return buf * (0.9 / peak) if peak > 0.0 else buf


def save_wav(file_path: Path | str, samples: NDArray[np.float64], fs: int) -> Path:
    """Save mono samples in [-1, 1] as 16-bit PCM WAV; returns the final path."""
    out_path = Path(file_path)
    if out_path.suffix.lower() != ".wav":
        out_path = out_path.with_suffix(".wav")
    x = np.clip(np.asarray(samples, dtype=np.float64), -1.0, 1.0)
    return write_audio(out_path, x, int(fs), subtype="PCM_16")


@dataclass(slots=True)
class MockSample:
    """One simulated telemetry reading."""

    rate_spd: float
    beat_error_ms: float
    amplitude_deg: float
    confidence: float
    snr_db: float


@dataclass(slots=True)
class MockDrift:
    """One simulated ADC clock drift estimate."""

    effective_fs: float
    drift_ppm: float
    rate_bias_spd: float
    sigma_ppm: float
    confidence: float


class MockTelemetryModel:
    """Deterministic (given a seed) evolution of the simulated readings.

    * During stabilization the readings start off-target (rate offset, low amplitude,
      low confidence, more noise) and settle exponentially.
    * While measuring: noise plus a slow random wander of the rate (s/d).
    * Signal level (SNR) oscillates slowly so the signal-quality bar moves.
    * Clock drift: a fixed "true" ppm whose estimate tightens over time.
    """

    def __init__(self, seed: int | None = None) -> None:
        tel_seed, drift_seed = np.random.SeedSequence(seed).spawn(2)
        self._rng = np.random.default_rng(tel_seed)
        self._drift_rng = np.random.default_rng(drift_seed)
        self.true_drift_ppm: float = float(self._drift_rng.uniform(-18.0, 18.0))
        self._settle_rate_spd: float = float(self._rng.normal(0.0, 4.0))
        self._settle_amp_deg: float = -abs(float(self._rng.normal(25.0, 5.0)))
        self._snr_phase: float = float(self._rng.uniform(0.0, 2.0 * np.pi))
        self._wander_spd: float = 0.0

    def sample(
        self,
        elapsed_s: float,
        dt_s: float,
        stabilization_s: float,
        base_rate_spd: float,
        base_beat_error_ms: float,
        base_amplitude_deg: float,
    ) -> MockSample:
        """Advance the model by ``dt_s`` and return the reading at ``elapsed_s``."""
        rng = self._rng
        tau = max(stabilization_s / 3.0, 0.3)
        settle = float(np.exp(-elapsed_s / tau))
        noise_gain = 1.0 + 1.5 * settle

        # Slow Ornstein-Uhlenbeck wander of the rate (tau 20 s, ~0.6 s/d stationary sigma)
        dt = max(dt_s, 0.0)
        self._wander_spd += -self._wander_spd * dt / 20.0 + 0.19 * np.sqrt(dt) * rng.normal()

        rate = (
            base_rate_spd
            + self._wander_spd
            + settle * self._settle_rate_spd
            + rng.normal(0.0, 0.35 * noise_gain)
        )
        beat_error = abs(base_beat_error_ms + rng.normal(0.0, 0.03 * noise_gain))
        amplitude = (
            base_amplitude_deg + settle * self._settle_amp_deg + rng.normal(0.0, 1.5 * noise_gain)
        )
        confidence = float(np.clip(0.97 - 0.45 * settle + rng.normal(0.0, 0.01), 0.0, 1.0))
        snr_db = 33.0 + 7.0 * np.sin(2.0 * np.pi * elapsed_s / 17.0 + self._snr_phase)
        snr_db += rng.normal(0.0, 0.8) - 6.0 * settle
        return MockSample(
            rate_spd=float(rate),
            beat_error_ms=float(beat_error),
            amplitude_deg=float(amplitude),
            confidence=confidence,
            snr_db=float(snr_db),
        )

    def drift(self, elapsed_s: float, nominal_fs: float) -> MockDrift:
        """Return the simulated clock drift estimate at ``elapsed_s``."""
        sigma = 8.0 / np.sqrt(1.0 + max(elapsed_s, 0.0)) + 0.2
        ppm = self.true_drift_ppm + float(self._drift_rng.normal(0.0, sigma))
        confidence = float(1.0 - np.exp(-max(elapsed_s, 0.0) / 12.0))
        return MockDrift(
            effective_fs=float(nominal_fs * (1.0 + ppm * 1e-6)),
            drift_ppm=ppm,
            rate_bias_spd=ppm * PPM_TO_SPD,
            sigma_ppm=float(sigma),
            confidence=confidence,
        )
