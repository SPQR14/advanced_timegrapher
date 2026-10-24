"""Synthetic mechanical-watch signal with EXACT sub-sample ground truth (tests only).

Every burst is evaluated analytically in continuous time at each sample instant
(``tau = n / fs - t0``), never by pasting an already-sampled burst at an integer index, so
the true onsets of P1/P2/P3 in absolute samples are exact.

Beat model: three damped bursts (two modes each, distinct f0 per sub-event, tau ~0.4 ms,
~4 ms long): unlocking at ``t0`` (amplitude 0.6), impulse at ``t0 + 0.45 t_lift`` (0.35)
and drop at ``t0 + t_lift`` (1.0), with ``t_lift`` from the physical formula. True period
``P = P_nom / (1 + rate / 86400)``; beat error ``t0_k = k P + (be / 2) (-1)^k``.

SNR is the P3 burst peak over the RMS of the noise **inside the detection band**
(1 kHz .. min(15 kHz, 0.45 fs)), so white, pink and brown noise are comparable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from adv_tg.core.beat_types import beat_period_s, lift_time_s

# (relative level, ((f0 Hz, relative amplitude, tau s), ...)) per sub-event
_BURSTS: tuple[tuple[float, tuple[tuple[float, float, float], ...]], ...] = (
    (0.60, ((6500.0, 1.0, 0.40e-3), (9800.0, 0.4, 0.30e-3))),  # P1 unlocking
    (0.35, ((4800.0, 1.0, 0.40e-3), (7400.0, 0.4, 0.30e-3))),  # P2 impulse
    (1.00, ((3600.0, 1.0, 0.45e-3), (5900.0, 0.4, 0.35e-3))),  # P3 drop
)
_P2_FRACTION = 0.45
_BURST_S = 4.0e-3
_SCALE = 0.5  # P3 peak before noise (headroom)


@dataclass(frozen=True)
class Truth:
    """Ground truth in absolute samples. Beats removed by ``miss_prob`` or dropouts are not
    listed; ``k`` keeps the true beat numbering (so gaps show as jumps in ``k``)."""

    k: NDArray[np.int64]
    p1: NDArray[np.float64]
    p2: NDArray[np.float64]
    p3: NDArray[np.float64]
    period_samples: float
    t_lift_samples: float
    fs: int
    removed_k: NDArray[np.int64] = field(default_factory=lambda: np.zeros(0, np.int64))


def _burst(
    out: NDArray[np.float64],
    fs: float,
    t0: float,
    level: float,
    modes: tuple[tuple[float, float, float], ...],
    duration_s: float,
) -> None:
    n0 = math.ceil(t0 * fs)
    n1 = min(out.size, math.floor((t0 + duration_s) * fs) + 1)
    if n1 <= max(n0, 0):
        return
    n = np.arange(max(n0, 0), n1)
    tau = n / fs - t0
    y = np.zeros(n.size)
    for f0, amp, decay in modes:
        y += amp * np.exp(-tau / decay) * np.sin(2.0 * np.pi * f0 * tau)
    out[n] += level * y


def _ramp(
    out: NDArray[np.float64],
    fs: float,
    t_end: float,
    ramp: tuple[float, float],
    level: float,
    rng: np.random.Generator,
) -> None:
    """Band noise whose envelope rises linearly over ``ramp[0]`` ms up to ``t_end``."""
    dur = ramp[0] * 1e-3
    n0 = max(0, math.ceil((t_end - dur) * fs))
    n1 = min(out.size, math.ceil(t_end * fs))
    if n1 <= n0:
        return
    tau = np.arange(n0, n1) / fs - (t_end - dur)
    y = np.zeros(tau.size)
    for f0 in (2500.0, 4200.0, 6100.0, 8300.0):
        y += np.sin(2.0 * np.pi * f0 * tau + rng.uniform(0, 2 * np.pi))
    out[n0:n1] += level * ramp[1] * (tau / dur) * y / 2.0


def _band_rms(x: NDArray[np.float64], fs: float) -> float:
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(x.size, 1.0 / fs)
    band = (f >= 1000.0) & (f <= min(15000.0, 0.45 * fs))
    return math.sqrt(2.0 * float(np.sum(np.abs(spec[band]) ** 2))) / x.size


def _noise(rng: np.random.Generator, n: int, fs: float, color: str | None) -> NDArray:
    w = rng.standard_normal(n)
    if color in (None, "white"):
        return w
    spec = np.fft.rfft(w)
    f = np.fft.rfftfreq(n, 1.0 / fs)
    f[0] = f[1]
    if color == "pink":
        spec /= np.sqrt(f)
    elif color == "brown":
        spec /= f
    else:
        raise ValueError(f"unknown noise color {color!r}")
    return np.fft.irfft(spec, n)


def synth_watch(
    *,
    bph: float = 28800,
    rate_s_per_day: float = 0.0,
    beat_error_ms: float = 0.0,
    amplitude_deg: float = 280.0,
    lift_angle_deg: float = 52.0,
    fs: int = 48000,
    seconds: float = 10.0,
    snr_db: float | None = 30.0,
    jitter_us: float = 0.0,
    mains_hz: float | None = None,
    mains_level: float = 0.3,
    p1_gain: float = 1.0,
    miss_prob: float = 0.0,
    dropouts: tuple[tuple[float, float], ...] = (),
    clip_level: float | None = None,
    seed: int = 0,
    start_s: float | None = None,
    noise_only_s: float = 0.0,
    amp_variation: float = 0.0,
    f0_jitter: float = 0.0,
    colored_noise: str | None = None,
    impulsive_noise_rate_hz: float = 0.0,
    slow_gain_drift: float = 0.0,
    tail_modes: int = 0,
    odd_drop_lobe: tuple[float, float, float] | None = None,
    odd_pre_ramp: tuple[float, float] | None = None,
) -> tuple[NDArray[np.float64], Truth]:
    """Generate a watch signal and its exact truth.

    Args:
        bph, rate_s_per_day, beat_error_ms, amplitude_deg, lift_angle_deg: Watch.
        fs, seconds: Sampling.
        snr_db: P3 peak over in-band noise RMS (``None`` = no noise).
        jitter_us: Gaussian jitter (std) of each beat's t0.
        mains_hz, mains_level: Mains hum (fundamental + 3 harmonics), level relative to P3.
        p1_gain: Extra gain of P1 (weak P1 down to 0.05).
        miss_prob: Probability that a beat is entirely silent.
        dropouts: ``(start_s, duration_s)`` stretches replaced by exact zeros.
        clip_level: Clip at full scale when the signal reaches this fraction of its peak
            (0.7 = the top 30 % of the peak is clipped at +-1).
        seed: RNG seed.
        start_s: Time of the first beat's P1 (default: random phase within one period).
        noise_only_s: Seconds of noise (no beats) at the beginning.
        amp_variation: +- fraction of random amplitude change per burst.
        f0_jitter: +- fraction of random f0 change per burst.
        colored_noise: ``None``/"white", "pink" or "brown".
        impulsive_noise_rate_hz: Poisson rate of spurious clicks (P1-like amplitude).
        slow_gain_drift: +- fraction of slow (7 s) sinusoidal gain drift.
        tail_modes: 0-3 extra low resonance modes with longer tails per burst.
        odd_drop_lobe: ``(delay_ms, level_min, level_max)``: on odd ``k`` the drop rings as
            two lobes, the second ``delay_ms`` after P3 with a random level (relative to P3)
            in ``[level_min, level_max]``. The loudest lobe then changes from beat to beat and
            differs from even ``k`` (as in the ETA C07 / H-50). The true P3 stays the onset of
            the first lobe.
        odd_pre_ramp: ``(duration_ms, level)``: on odd ``k`` a slowly rising, noise-like
            ramp (level relative to P3) ends at the true P3 onset, as the friction before the
            drop of the Seiko 4R36 tac. The true P3 is still the onset of the drop itself.
    """
    rng = np.random.default_rng(seed)
    n = int(round(seconds * fs))
    x = np.zeros(n)
    period = beat_period_s(bph) / (1.0 + rate_s_per_day / 86400.0)
    t_lift = lift_time_s(amplitude_deg, lift_angle_deg, bph)
    be = beat_error_ms * 1e-3
    first = noise_only_s + (rng.uniform(0.0, period) if start_s is None else start_s)
    offsets = (0.0, _P2_FRACTION * t_lift, t_lift)
    tails = [
        (rng.uniform(1500.0, 4000.0), rng.uniform(0.15, 0.25), rng.uniform(1.0e-3, 2.0e-3))
        for _ in range(int(tail_modes))
    ]
    duration = max(_BURST_S, 5.0 * max((t[2] for t in tails), default=0.0))

    ks, p1s, p2s, p3s, removed = [], [], [], [], []
    k = 0
    while True:
        t0 = first + k * period + 0.5 * be * (-1) ** k
        if jitter_us:
            t0 += rng.normal(0.0, jitter_us * 1e-6)
        if t0 + t_lift + duration > seconds:
            break
        silent = miss_prob > 0 and rng.uniform() < miss_prob
        in_drop = any(s - duration < t0 + t_lift and t0 < s + d for s, d in dropouts)
        if silent or in_drop:
            removed.append(k)
        else:
            for i, (level, modes) in enumerate(_BURSTS):
                lv = level * (p1_gain if i == 0 else 1.0)
                if amp_variation:
                    lv *= 1.0 + rng.uniform(-amp_variation, amp_variation)
                ms = modes
                if f0_jitter:
                    ms = tuple(
                        (f * (1.0 + rng.uniform(-f0_jitter, f0_jitter)), a, d) for f, a, d in ms
                    )
                ms = ms + tuple(tails)
                _burst(x, fs, t0 + offsets[i], lv * _SCALE, ms, duration)
                if i == 2 and odd_pre_ramp is not None and k % 2 == 1:
                    _ramp(x, fs, t0 + offsets[i], odd_pre_ramp, lv * _SCALE, rng)
                if i == 2 and odd_drop_lobe is not None and k % 2 == 1:
                    delay_ms, lo, hi = odd_drop_lobe
                    lobe = lv * rng.uniform(lo, hi) * _SCALE
                    _burst(x, fs, t0 + offsets[i] + delay_ms * 1e-3, lobe, ms, duration)
            ks.append(k)
            p1s.append((t0 + offsets[0]) * fs)
            p2s.append((t0 + offsets[1]) * fs)
            p3s.append((t0 + offsets[2]) * fs)
        k += 1

    if impulsive_noise_rate_hz > 0:
        count = rng.poisson(impulsive_noise_rate_hz * seconds)
        for tc in rng.uniform(0.0, seconds, count):
            f0 = rng.uniform(2000.0, 10000.0)
            level = 0.6 * _SCALE * rng.uniform(0.5, 1.0)
            _burst(x, fs, float(tc), level, ((f0, 1.0, 0.2e-3),), 2.0e-3)

    if slow_gain_drift:
        t = np.arange(n) / fs
        x *= 1.0 + slow_gain_drift * np.sin(2 * np.pi * t / 7.0 + rng.uniform(0, 2 * np.pi))

    if snr_db is not None and n:
        noise = _noise(rng, n, fs, colored_noise)
        target = _SCALE * 10.0 ** (-snr_db / 20.0)
        x += noise * (target / _band_rms(noise, fs))
    if mains_hz:
        t = np.arange(n) / fs
        for h in range(1, 5):
            x += mains_level * _SCALE / h * np.sin(2 * np.pi * mains_hz * h * t + h)

    for s, d in dropouts:
        x[int(round(s * fs)) : int(round((s + d) * fs))] = 0.0

    if clip_level is not None:
        peak = float(np.max(np.abs(x)))
        x = np.clip(x / (clip_level * peak), -1.0, 1.0)

    truth = Truth(
        k=np.asarray(ks, dtype=np.int64),
        p1=np.asarray(p1s),
        p2=np.asarray(p2s),
        p3=np.asarray(p3s),
        period_samples=period * fs,
        t_lift_samples=t_lift * fs,
        fs=fs,
        removed_k=np.asarray(removed, dtype=np.int64),
    )
    return x, truth


REALISM: dict[str, object] = {
    "amp_variation": 0.15,
    "f0_jitter": 0.08,
    "colored_noise": "pink",
    "impulsive_noise_rate_hz": 1.0,
    "slow_gain_drift": 0.3,
    "tail_modes": 2,
    "mains_hz": 50.0,
    "jitter_us": 20.0,
}
"""Realistic variations used by the "with realism" variant of the main tests."""
