"""Onset of a sound by its first departure from silence (numpy only).

Shared by the GPS PPS detector (``adv_tg.core.pps``) and the quartz mode of the beat detector
(``adv_tg.core.beat_detector``): both time a sound whose first cycles repeat from one event to
the next while the rest of it does not (the amplitude build-up of a buzzer, the ringing of a
step motor). The onset is the first threshold crossing after the last quiet stretch, with
linear sub-sample interpolation; ``lowpass_fir`` removes the noise above the sound first.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

__all__ = ["MAD_TO_SIGMA", "lowpass_fir", "onset_after_quiet", "robust_level"]

MAD_TO_SIGMA = 1.4826
_TINY = 1e-12


def lowpass_fir(fs: float, fc: float, periods: float = 4.0) -> NDArray[np.float64]:
    """Windowed-sinc low-pass (Blackman, odd length, unit DC gain); ``[1]`` above 0.45 fs.

    Its length is ``periods`` periods of ``fc``; the delay of the (linear-phase) filter is
    ``(size - 1) / 2`` samples.
    """
    if fc >= 0.45 * fs:
        return np.ones(1)
    n = int(periods * fs / fc) | 1
    m = np.arange(n) - n // 2
    h = np.sinc(2.0 * fc / fs * m) * np.blackman(n)
    return h / h.sum()


def robust_level(x: NDArray[np.float64]) -> tuple[float, float]:
    """``(median, sigma)`` of ``x``, sigma from the MAD (never below a tiny floor)."""
    mu = float(np.median(x))
    return mu, max(MAD_TO_SIGMA * float(np.median(np.abs(x - mu))), _TINY)


def onset_after_quiet(r: NDArray[np.float64], threshold: float, quiet: int) -> float | None:
    """Onset in ``r`` (``|x - baseline|``, ending where the sound is known to be on).

    Walks back from the end to the last stretch of at least ``quiet`` samples below
    ``threshold``; the onset is the first crossing after it, linearly interpolated (index into
    ``r``). None when nothing crosses or when ``r`` is not quiet before the crossing.
    """
    act = np.flatnonzero(r >= threshold)
    if act.size == 0:
        return None
    gaps = np.flatnonzero(np.diff(act) - 1 >= quiet)
    if gaps.size:
        j = int(act[gaps[-1] + 1])
    elif act[0] >= quiet:
        j = int(act[0])
    else:
        return None
    frac = (threshold - r[j - 1]) / max(float(r[j] - r[j - 1]), _TINY)
    return j - 1 + min(max(float(frac), 0.0), 1.0)
