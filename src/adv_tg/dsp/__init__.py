"""DSP of the analysis without SciPy: filter design in numpy, kernels in C++.

* ``filters``: Butterworth band-pass design, ``sosfilt_zi`` and the zero-phase padding rule,
  identical to ``scipy.signal``.
* ``engine``: native kernels (``adv_tg_dsp_native``, built from ``native_dsp.cpp`` by
  ``setup.py``) with a SciPy fallback when the extension is missing.
"""

from adv_tg.dsp.engine import (
    GATE,
    DspBackendError,
    compute_cell_envelope,
    detect_cell_candidates,
    is_native_dsp_available,
    sosfilt,
    sosfiltfilt,
)
from adv_tg.dsp.filters import butter_bandpass_sos, sosfilt_zi, sosfiltfilt_padlen

__all__ = [
    "GATE",
    "DspBackendError",
    "butter_bandpass_sos",
    "compute_cell_envelope",
    "detect_cell_candidates",
    "is_native_dsp_available",
    "sosfilt",
    "sosfilt_zi",
    "sosfiltfilt",
    "sosfiltfilt_padlen",
]
