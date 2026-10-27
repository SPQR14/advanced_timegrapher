"""DSP of the analysis in pure Python + numpy (no SciPy, no compiled extensions).

* ``filters``: Butterworth band-pass design, ``sosfilt_zi`` and the zero-phase padding rule,
  identical to ``scipy.signal``.
* ``engine``: vectorised filtering, envelope and candidate kernels (see its docstring).
"""

from adv_tg.dsp.engine import (
    GATE,
    compute_cell_envelope,
    detect_cell_candidates,
    find_local_maxima,
    sosfilt,
    sosfiltfilt,
)
from adv_tg.dsp.filters import butter_bandpass_sos, sosfilt_zi, sosfiltfilt_padlen

__all__ = [
    "GATE",
    "butter_bandpass_sos",
    "compute_cell_envelope",
    "detect_cell_candidates",
    "find_local_maxima",
    "sosfilt",
    "sosfilt_zi",
    "sosfiltfilt",
    "sosfiltfilt_padlen",
]
