"""Loader of the native C++20 engine (``libadvtg_native``) and ctypes helpers.

The engine is a shared library with a C ABI built by ``scripts/build_native.py`` and kept in
``adv_tg/_native/`` (also bundled by PyInstaller). Every accelerated module asks this one
for the library, checks that the symbols it needs exist (``have``) and otherwise keeps using
its pure-Python/numpy implementation, which stays available under the ``py_*`` / ``Py*``
names and is the reference of the parity tests.

Environment:

* ``ADV_TG_DISABLE_NATIVE=1``: never load the native engine (pure Python, as v1.x).
* ``ADV_TG_NATIVE_LIB=/path/libadvtg_native*.so``: load this exact file (partial builds, tests).
* ``ADVTG_SIMD=generic|sse42|avx2|avx512|neon``: force the SIMD level (default: best).

ctypes releases the GIL during every call, so the long native calls never block the GUI.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

__all__ = [
    "ABI_VERSION",
    "ENV_DISABLE",
    "ENV_LIB",
    "f64",
    "have",
    "i64",
    "is_native_available",
    "lib",
    "native_arch_info",
    "native_version",
    "ptr",
    "reload",
    "simd_level",
]

ABI_VERSION = 1
ENV_DISABLE = "ADV_TG_DISABLE_NATIVE"
ENV_LIB = "ADV_TG_NATIVE_LIB"

logger = logging.getLogger(__name__)


def _lib_name() -> str:
    if sys.platform == "win32":
        return "advtg_native.dll"
    if sys.platform == "darwin":
        return "libadvtg_native.dylib"
    return "libadvtg_native.so"


def _candidate_paths() -> list[Path]:
    out: list[Path] = []
    env = os.environ.get(ENV_LIB, "").strip()
    if env:
        out.append(Path(env).expanduser())
        return out
    name = _lib_name()
    out.append(Path(__file__).resolve().parent / "_native" / name)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        base = Path(meipass)
        out += [
            base / "adv_tg" / "_native" / name,
            base / name,
            base.parent / "Resources" / "adv_tg" / "_native" / name,
        ]
    return out


def _disabled() -> bool:
    return os.environ.get(ENV_DISABLE, "").strip().lower() in ("1", "true", "yes", "on")


def _declare(cdll: ctypes.CDLL) -> None:
    cdll.advtg_abi_version.restype = ctypes.c_int
    cdll.advtg_version.restype = ctypes.c_char_p
    cdll.advtg_simd_name.restype = ctypes.c_char_p
    cdll.advtg_cpu_features.argtypes = [ctypes.c_char_p, ctypes.c_int]
    cdll.advtg_cpu_features.restype = ctypes.c_int
    cdll.advtg_set_simd.argtypes = [ctypes.c_char_p]
    cdll.advtg_set_simd.restype = ctypes.c_int
    cdll.advtg_simd_available.argtypes = [ctypes.c_char_p]
    cdll.advtg_simd_available.restype = ctypes.c_int
    cdll.advtg_f64_key.argtypes = [ctypes.c_double]
    cdll.advtg_f64_key.restype = ctypes.c_uint64
    cdll.advtg_key_f64.argtypes = [ctypes.c_uint64]
    cdll.advtg_key_f64.restype = ctypes.c_double
    cdll.advtg_median_f64.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    cdll.advtg_median_f64.restype = ctypes.c_double
    cdll.advtg_dot_f64.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
    cdll.advtg_dot_f64.restype = ctypes.c_double
    cdll.advtg_median_segments_f64.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_size_t,
        ctypes.c_size_t,
        ctypes.c_void_p,
    ]
    cdll.advtg_median_segments_f64.restype = None
    cdll.advtg_hilbert_fold_f64.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
    ]
    cdll.advtg_hilbert_fold_f64.restype = None

    # ---- optional DSP / Detector / Metrics / Runtime declarations (checked via have()) ----
    if hasattr(cdll, "advtg_sosfilt"):
        cdll.advtg_sosfilt.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        cdll.advtg_sosfilt.restype = ctypes.c_int
    if hasattr(cdll, "advtg_sosfiltfilt"):
        cdll.advtg_sosfiltfilt.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_int64,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
        ]
        cdll.advtg_sosfiltfilt.restype = ctypes.c_int
    if hasattr(cdll, "advtg_compute_cell_envelope"):
        cdll.advtg_compute_cell_envelope.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_int64,
            ctypes.c_int64,
            ctypes.c_int64,
            ctypes.c_int64,
            ctypes.c_void_p,
        ]
        cdll.advtg_compute_cell_envelope.restype = ctypes.c_int
    if hasattr(cdll, "advtg_moving_average_same"):
        cdll.advtg_moving_average_same.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_int64,
            ctypes.c_void_p,
        ]
        cdll.advtg_moving_average_same.restype = None
    if hasattr(cdll, "advtg_find_local_maxima"):
        cdll.advtg_find_local_maxima.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
        cdll.advtg_find_local_maxima.restype = ctypes.c_size_t
    if hasattr(cdll, "advtg_detect_cell_candidates"):
        cdll.advtg_detect_cell_candidates.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_int64,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_int64,
            ctypes.c_int64,
            ctypes.c_int64,
            ctypes.c_int64,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        cdll.advtg_detect_cell_candidates.restype = ctypes.c_int64
    if hasattr(cdll, "advtg_robust_level"):
        cdll.advtg_robust_level.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        cdll.advtg_robust_level.restype = None
    if hasattr(cdll, "advtg_onset_after_quiet"):
        cdll.advtg_onset_after_quiet.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_double,
            ctypes.c_int64,
        ]
        cdll.advtg_onset_after_quiet.restype = ctypes.c_double
    if hasattr(cdll, "advtg_rfft"):
        cdll.advtg_rfft.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_size_t,
            ctypes.c_void_p,
        ]
        cdll.advtg_rfft.restype = ctypes.c_int
    if hasattr(cdll, "advtg_irfft"):
        cdll.advtg_irfft.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
        cdll.advtg_irfft.restype = ctypes.c_int
    if hasattr(cdll, "advtg_interf_create"):
        cdll.advtg_interf_create.argtypes = [
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_int64,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_int64,
            ctypes.c_double,
            ctypes.c_int64,
            ctypes.c_int64,
        ]
        cdll.advtg_interf_create.restype = ctypes.c_void_p
        cdll.advtg_interf_destroy.argtypes = [ctypes.c_void_p]
        cdll.advtg_interf_destroy.restype = None
        cdll.advtg_interf_reset.argtypes = [ctypes.c_void_p, ctypes.c_int64]
        cdll.advtg_interf_reset.restype = None
        cdll.advtg_interf_next_frame.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        cdll.advtg_interf_next_frame.restype = None
        cdll.advtg_interf_skip_frame.argtypes = [ctypes.c_void_p]
        cdll.advtg_interf_skip_frame.restype = None
        cdll.advtg_interf_add_frame.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
        cdll.advtg_interf_add_frame.restype = ctypes.c_int
        cdll.advtg_interf_add_frames.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_int64,
            ctypes.c_int64,
        ]
        cdll.advtg_interf_add_frames.restype = None
        cdll.advtg_interf_gains.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int64,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        cdll.advtg_interf_gains.restype = ctypes.c_int
        cdll.advtg_interf_apply.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        cdll.advtg_interf_apply.restype = None
    if hasattr(cdll, "advtg_norm_create"):
        cdll.advtg_norm_create.argtypes = [
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
        ]
        cdll.advtg_norm_create.restype = ctypes.c_void_p
        cdll.advtg_norm_destroy.argtypes = [ctypes.c_void_p]
        cdll.advtg_norm_destroy.restype = None
        cdll.advtg_norm_reset.argtypes = [ctypes.c_void_p]
        cdll.advtg_norm_reset.restype = None
        cdll.advtg_norm_process.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_int64,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        cdll.advtg_norm_process.restype = ctypes.c_int64
        cdll.advtg_norm_flush.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        cdll.advtg_norm_flush.restype = ctypes.c_int64
        cdll.advtg_norm_state.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        cdll.advtg_norm_state.restype = None
    if hasattr(cdll, "advtg_bd_create"):
        cdll.advtg_bd_create.argtypes = [ctypes.c_void_p]
        cdll.advtg_bd_create.restype = ctypes.c_void_p
        cdll.advtg_bd_destroy.argtypes = [ctypes.c_void_p]
        cdll.advtg_bd_destroy.restype = None
        cdll.advtg_bd_reset.argtypes = [ctypes.c_void_p]
        cdll.advtg_bd_reset.restype = None
        cdll.advtg_bd_process_buffered.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_int64,
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        cdll.advtg_bd_process_buffered.restype = ctypes.c_int64
        cdll.advtg_bd_flush_buffered.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        cdll.advtg_bd_flush_buffered.restype = ctypes.c_int64
        cdll.advtg_bd_take_overflow.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        cdll.advtg_bd_take_overflow.restype = ctypes.c_int64
        cdll.advtg_bd_stats.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        cdll.advtg_bd_stats.restype = ctypes.c_int
        cdll.advtg_bd_warnings.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int32]
        cdll.advtg_bd_warnings.restype = ctypes.c_int32
        cdll.advtg_bd_templates.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        cdll.advtg_bd_templates.restype = ctypes.c_int
        cdll.advtg_bd_quartz_timing.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        cdll.advtg_bd_quartz_timing.restype = None
        cdll.advtg_bd_stream_start_hint.argtypes = [ctypes.c_void_p]
        cdll.advtg_bd_stream_start_hint.restype = ctypes.c_int64
    if hasattr(cdll, "advtg_pps_create"):
        cdll.advtg_pps_create.argtypes = [ctypes.c_double, ctypes.c_double]
        cdll.advtg_pps_create.restype = ctypes.c_void_p
        cdll.advtg_pps_destroy.argtypes = [ctypes.c_void_p]
        cdll.advtg_pps_destroy.restype = None
        cdll.advtg_pps_reset.argtypes = [ctypes.c_void_p, ctypes.c_int64]
        cdll.advtg_pps_reset.restype = None
        cdll.advtg_pps_process.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_int64,
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        cdll.advtg_pps_process.restype = ctypes.c_int64
        cdll.advtg_pps_take_overflow.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        cdll.advtg_pps_take_overflow.restype = ctypes.c_int64
        cdll.advtg_pps_rejected.argtypes = [ctypes.c_void_p]
        cdll.advtg_pps_rejected.restype = ctypes.c_int64
    if hasattr(cdll, "advtg_fit_parity_weighted"):
        cdll.advtg_fit_parity_weighted.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_double,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
        ]
        cdll.advtg_fit_parity_weighted.restype = ctypes.c_int
        cdll.advtg_compute_metrics.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        cdll.advtg_compute_metrics.restype = ctypes.c_int
        cdll.advtg_take_metrics_extra.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_char_p,
            ctypes.c_int32,
        ]
        cdll.advtg_take_metrics_extra.restype = ctypes.c_int64
    if hasattr(cdll, "advtg_lower_envelope"):
        cdll.advtg_lower_envelope.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        cdll.advtg_lower_envelope.restype = ctypes.c_size_t
        cdll.advtg_fit_envelope.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_double,
            ctypes.c_int,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_double,
            ctypes.c_void_p,
        ]
        cdll.advtg_fit_envelope.restype = ctypes.c_int
        cdll.advtg_drift_from_ticks.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_int,
            ctypes.c_void_p,
        ]
        cdll.advtg_drift_from_ticks.restype = ctypes.c_int
        cdll.advtg_detect_pps_edges.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_double,
            ctypes.c_int64,
            ctypes.c_int,
            ctypes.c_double,
            ctypes.c_int,
            ctypes.c_double,
            ctypes.c_void_p,
        ]
        cdll.advtg_detect_pps_edges.restype = ctypes.c_size_t
    if hasattr(cdll, "advtg_lomb_scargle"):
        cdll.advtg_teager_kaiser.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
        cdll.advtg_teager_kaiser.restype = None
        cdll.advtg_lomb_scargle.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
        ]
        cdll.advtg_lomb_scargle.restype = None
    if hasattr(cdll, "advtg_session_create"):
        vp, i64, i32, dbl = ctypes.c_void_p, ctypes.c_int64, ctypes.c_int32, ctypes.c_double
        cdll.advtg_session_config_default.argtypes = [vp, dbl, dbl]
        cdll.advtg_session_config_default.restype = None
        cdll.advtg_session_create.argtypes = [vp, vp]
        cdll.advtg_session_create.restype = vp
        cdll.advtg_session_destroy.argtypes = [vp]
        cdll.advtg_session_destroy.restype = None
        cdll.advtg_session_stream.argtypes = [vp]
        cdll.advtg_session_stream.restype = vp
        cdll.advtg_session_update.argtypes = [vp, vp]
        cdll.advtg_session_update.restype = ctypes.c_int
        cdll.advtg_session_waveform.argtypes = [vp, vp, i64]
        cdll.advtg_session_waveform.restype = i64
        cdll.advtg_session_tape.argtypes = [vp, vp, vp, vp, i64]
        cdll.advtg_session_tape.restype = i64
        for name in ("advtg_session_new_beats", "advtg_session_window_beats"):
            getattr(cdll, name).argtypes = [vp, vp, i64]
            getattr(cdll, name).restype = i64
        cdll.advtg_session_metric_windows.argtypes = [vp, vp, i64]
        cdll.advtg_session_metric_windows.restype = i64
        for name in ("advtg_session_metric_warnings", "advtg_session_detector_warnings"):
            getattr(cdll, name).argtypes = [vp, ctypes.c_char_p, i32]
            getattr(cdll, name).restype = i32
        cdll.advtg_session_detector_stats.argtypes = [vp, vp]
        cdll.advtg_session_detector_stats.restype = ctypes.c_int
        cdll.advtg_session_display_window.argtypes = [vp, vp, vp]
        cdll.advtg_session_display_window.restype = None
        cdll.advtg_session_last_error.argtypes = [vp]
        cdll.advtg_session_last_error.restype = ctypes.c_char_p
        cdll.advtg_butter_bandpass_sos.argtypes = [ctypes.c_int, dbl, dbl, dbl, vp, ctypes.c_int]
        cdll.advtg_butter_bandpass_sos.restype = ctypes.c_int
        cdll.advtg_sosfilt_zi.argtypes = [vp, ctypes.c_int, vp]
        cdll.advtg_sosfilt_zi.restype = ctypes.c_int
        cdll.advtg_sosfiltfilt_padlen.argtypes = [vp, ctypes.c_int]
        cdll.advtg_sosfiltfilt_padlen.restype = i64
    if hasattr(cdll, "advtg_stream_create"):
        cdll.advtg_steady_now_ns.argtypes = []
        cdll.advtg_steady_now_ns.restype = ctypes.c_int64
        cdll.advtg_wall_now_ns.argtypes = []
        cdll.advtg_wall_now_ns.restype = ctypes.c_int64
        cdll.advtg_stream_create.argtypes = [
            ctypes.c_size_t,
            ctypes.c_double,
            ctypes.c_size_t,
            ctypes.c_int64,
        ]
        cdll.advtg_stream_create.restype = ctypes.c_void_p
        cdll.advtg_stream_destroy.argtypes = [ctypes.c_void_p]
        cdll.advtg_stream_destroy.restype = None
        cdll.advtg_stream_reset.argtypes = [ctypes.c_void_p]
        cdll.advtg_stream_reset.restype = None
        cdll.advtg_stream_total.argtypes = [ctypes.c_void_p]
        cdll.advtg_stream_total.restype = ctypes.c_int64
        cdll.advtg_stream_oldest.argtypes = [ctypes.c_void_p]
        cdll.advtg_stream_oldest.restype = ctypes.c_int64
        cdll.advtg_stream_write_f32.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_int64,
            ctypes.c_int,
            ctypes.c_int64,
            ctypes.c_int64,
        ]
        cdll.advtg_stream_write_f32.restype = None
        cdll.advtg_stream_write_f64.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_int64,
            ctypes.c_int,
            ctypes.c_int64,
            ctypes.c_int64,
        ]
        cdll.advtg_stream_write_f64.restype = None
        cdll.advtg_stream_read_f64.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int64,
            ctypes.c_int64,
            ctypes.c_void_p,
        ]
        cdll.advtg_stream_read_f64.restype = ctypes.c_int
        cdll.advtg_stream_read_f32.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int64,
            ctypes.c_int64,
            ctypes.c_void_p,
        ]
        cdll.advtg_stream_read_f32.restype = ctypes.c_int
        cdll.advtg_stream_clock_log.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int64,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        cdll.advtg_stream_clock_log.restype = ctypes.c_size_t
        cdll.advtg_stream_stats.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        cdll.advtg_stream_stats.restype = None
    if hasattr(cdll, "advtg_minmax_decimate_f64"):
        cdll.advtg_minmax_decimate_f64.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_size_t,
            ctypes.c_void_p,
        ]
        cdll.advtg_minmax_decimate_f64.restype = None
        cdll.advtg_channel_diff2_energy_f32.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_size_t,
            ctypes.c_void_p,
        ]
        cdll.advtg_channel_diff2_energy_f32.restype = None
        cdll.advtg_channel_diff2_energy_f64.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_size_t,
            ctypes.c_void_p,
        ]
        cdll.advtg_channel_diff2_energy_f64.restype = None
        cdll.advtg_smooth_median_f64.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_int,
            ctypes.c_void_p,
        ]
        cdll.advtg_smooth_median_f64.restype = None
    if hasattr(cdll, "advtg_cap_create"):
        cdll.advtg_cap_create.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_double,
            ctypes.c_size_t,
            ctypes.c_int,
        ]
        cdll.advtg_cap_create.restype = ctypes.c_void_p
        cdll.advtg_cap_destroy.argtypes = [ctypes.c_void_p]
        cdll.advtg_cap_destroy.restype = None
        cdll.advtg_cap_open.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        cdll.advtg_cap_open.restype = ctypes.c_int
        cdll.advtg_cap_start.argtypes = [ctypes.c_void_p]
        cdll.advtg_cap_start.restype = ctypes.c_int
        cdll.advtg_cap_stop.argtypes = [ctypes.c_void_p]
        cdll.advtg_cap_stop.restype = ctypes.c_int
        cdll.advtg_cap_close.argtypes = [ctypes.c_void_p]
        cdll.advtg_cap_close.restype = None
        cdll.advtg_cap_is_active.argtypes = [ctypes.c_void_p]
        cdll.advtg_cap_is_active.restype = ctypes.c_int
        cdll.advtg_cap_stats.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        cdll.advtg_cap_stats.restype = None
        cdll.advtg_cap_last_error.argtypes = [ctypes.c_void_p]
        cdll.advtg_cap_last_error.restype = ctypes.c_char_p
    if hasattr(cdll, "advtg_gr_create"):
        cdll.advtg_gr_create.argtypes = []
        cdll.advtg_gr_create.restype = ctypes.c_void_p
        cdll.advtg_gr_destroy.argtypes = [ctypes.c_void_p]
        cdll.advtg_gr_destroy.restype = None
        cdll.advtg_gr_submit_tape.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        cdll.advtg_gr_submit_tape.restype = None
        cdll.advtg_gr_submit_osc.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        cdll.advtg_gr_submit_osc.restype = None
        cdll.advtg_gr_submit_wave.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        cdll.advtg_gr_submit_wave.restype = None
        cdll.advtg_gr_clear_slot.argtypes = [ctypes.c_void_p, ctypes.c_int]
        cdll.advtg_gr_clear_slot.restype = None
        cdll.advtg_gr_flush.argtypes = [ctypes.c_void_p]
        cdll.advtg_gr_flush.restype = None
        cdll.advtg_gr_acquire_frame.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        cdll.advtg_gr_acquire_frame.restype = ctypes.c_void_p


class NativeBeatStruct(ctypes.Structure):
    _fields_ = [
        ("k", ctypes.c_int64),
        ("p1", ctypes.c_double),
        ("p2", ctypes.c_double),
        ("p3", ctypes.c_double),
        ("peak_dbfs", ctypes.c_double),
        ("snr_db", ctypes.c_double),
        ("residual_samples", ctypes.c_double),
        ("confidence", ctypes.c_double),
        ("t_fine", ctypes.c_double),
        ("t_p1", ctypes.c_double),
        ("t_p1_tpl", ctypes.c_double),
        ("n_subevents", ctypes.c_int32),
        ("flags", ctypes.c_uint32),
    ]


class NativeDetectorConfigStruct(ctypes.Structure):
    _fields_ = [
        ("sos", ctypes.c_void_p),
        ("zi", ctypes.c_void_p),
        ("padlen", ctypes.c_int64),
        ("sample_rate", ctypes.c_double),
        ("bph", ctypes.c_double),
        ("lift_angle_deg", ctypes.c_double),
        ("amp_lo_deg", ctypes.c_double),
        ("amp_hi_deg", ctypes.c_double),
        ("band_lo_hz", ctypes.c_double),
        ("band_hi_hz", ctypes.c_double),
        ("envelope_smooth_ms", ctypes.c_double),
        ("grid_step_s", ctypes.c_double),
        ("noise_window_s", ctypes.c_double),
        ("threshold_hi", ctypes.c_double),
        ("threshold_lo", ctypes.c_double),
        ("ripple_merge_ms", ctypes.c_double),
        ("min_subevent_spacing_ms", ctypes.c_double),
        ("split_ratio", ctypes.c_double),
        ("onset_fraction", ctypes.c_double),
        ("subevent_rel_threshold", ctypes.c_double),
        ("min_anchor_snr_db", ctypes.c_double),
        ("min_subevent_snr_db", ctypes.c_double),
        ("weak_snr_db", ctypes.c_double),
        ("phase_tolerance", ctypes.c_double),
        ("period_search", ctypes.c_double),
        ("acquisition_s", ctypes.c_double),
        ("acquisition_min_fraction", ctypes.c_double),
        ("outlier_sigma", ctypes.c_double),
        ("outlier_min_ms", ctypes.c_double),
        ("lift_margin", ctypes.c_double),
        ("lift_track_tolerance", ctypes.c_double),
        ("typical_amp_lo_deg", ctypes.c_double),
        ("typical_amp_hi_deg", ctypes.c_double),
        ("saturation_level", ctypes.c_double),
        ("dropout_min_ms", ctypes.c_double),
        ("period_warn", ctypes.c_double),
        ("post_group_ms", ctypes.c_double),
        ("template_search_ms", ctypes.c_double),
        ("template_post_ms", ctypes.c_double),
        ("template_p3_fraction", ctypes.c_double),
        ("template_lobe_fraction", ctypes.c_double),
        ("template_burst_ms", ctypes.c_double),
        ("template_p1_fraction", ctypes.c_double),
        ("template_p1_min_ms", ctypes.c_double),
        ("template_min_corr", ctypes.c_double),
        ("template_align_smooth_ms", ctypes.c_double),
        ("template_front_fraction", ctypes.c_double),
        ("template_front_lift_fraction", ctypes.c_double),
        ("template_amp_mismatch_deg", ctypes.c_double),
        ("template_refresh_min_s", ctypes.c_double),
        ("template_reanchor_corr", ctypes.c_double),
        ("quartz_template_ms", ctypes.c_double),
        ("quartz_outlier_min_ms", ctypes.c_double),
        ("quartz_onset_snr_db", ctypes.c_double),
        ("quartz_onset_beat_snr_db", ctypes.c_double),
        ("quartz_onset_sigma", ctypes.c_double),
        ("quartz_onset_lowpass_hz", ctypes.c_double),
        ("interference_ms", ctypes.c_double),
        ("interference_excess_db", ctypes.c_double),
        ("interference_max_db", ctypes.c_double),
        ("interference_history_s", ctypes.c_double),
        ("interference_percentile", ctypes.c_double),
        ("p1_onset_sigma", ctypes.c_double),
        ("p1_onset_fraction", ctypes.c_double),
        ("p1_onset_back_ms", ctypes.c_double),
        ("p1_onset_fwd_ms", ctypes.c_double),
        ("p1_onset_base_ms", ctypes.c_double),
        ("p1_onset_min_ms", ctypes.c_double),
        ("p1_onset_peak_ms", ctypes.c_double),
        ("n_sec", ctypes.c_int32),
        ("filter_order", ctypes.c_int32),
        ("model_beats", ctypes.c_int32),
        ("max_missed", ctypes.c_int32),
        ("template_beats", ctypes.c_int32),
        ("template_min_beats", ctypes.c_int32),
        ("template_refresh_beats", ctypes.c_int32),
        ("template_anchor_mode", ctypes.c_int32),
        ("quartz", ctypes.c_int32),
        ("quartz_reseed_beats", ctypes.c_int32),
        ("quartz_onset", ctypes.c_int32),
        ("interference_filter", ctypes.c_int32),
        ("p1_onset", ctypes.c_int32),
        ("normalize", ctypes.c_int32),
    ]


class NativeDetectorStatsStruct(ctypes.Structure):
    _fields_ = [
        ("samples_processed", ctypes.c_int64),
        ("beats_emitted", ctypes.c_int64),
        ("missed_beats", ctypes.c_int64),
        ("gap_beats", ctypes.c_int64),
        ("gaps", ctypes.c_int64),
        ("rejected_events", ctypes.c_int64),
        ("relocks", ctypes.c_int64),
        ("normalizer_clipped_samples", ctypes.c_int64),
        ("expected_index", ctypes.c_int64),
        ("norm_next_index", ctypes.c_int64),
        ("lock_sample", ctypes.c_double),
        ("lock_time_s", ctypes.c_double),
        ("p1_fraction", ctypes.c_double),
        ("p2_fraction", ctypes.c_double),
        ("p3_fraction", ctypes.c_double),
        ("period_samples", ctypes.c_double),
        ("nominal_period_samples", ctypes.c_double),
        ("interference_db", ctypes.c_double),
        ("interference_hz", ctypes.c_double),
        ("normalizer_gain_db", ctypes.c_double),
        ("has_lock_sample", ctypes.c_int32),
        ("locked", ctypes.c_int32),
        ("has_normalizer", ctypes.c_int32),
        ("started", ctypes.c_int32),
        ("finished", ctypes.c_int32),
        ("has_norm_next", ctypes.c_int32),
    ]


class NativeTemplateHeaderStruct(ctypes.Structure):
    _fields_ = [
        ("onset_p1", ctypes.c_double),
        ("onset_p3", ctypes.c_double),
        ("onset_ref", ctypes.c_double),
        ("valid", ctypes.c_int32),
        ("parity", ctypes.c_int32),
        ("n_beats", ctypes.c_int32),
        ("length", ctypes.c_int32),
        ("p3_is_frente", ctypes.c_int32),
        ("has_anchor", ctypes.c_int32),
    ]


class NativePpsPulseStruct(ctypes.Structure):
    _fields_ = [
        ("index", ctypes.c_double),
        ("snr_db", ctypes.c_double),
        ("peak", ctypes.c_double),
    ]


class NativeParityFitStruct(ctypes.Structure):
    _fields_ = [
        ("a", ctypes.c_double),
        ("period", ctypes.c_double),
        ("c", ctypes.c_double),
        ("period_sigma", ctypes.c_double),
        ("c_sigma", ctypes.c_double),
        ("k_ref", ctypes.c_double),
        ("sigma_even", ctypes.c_double),
        ("sigma_odd", ctypes.c_double),
        ("q", ctypes.c_double),
        ("q_sigma", ctypes.c_double),
        ("n_even", ctypes.c_int64),
        ("n_odd", ctypes.c_int64),
        ("n_trimmed", ctypes.c_int64),
        ("valid", ctypes.c_int32),
        ("_pad", ctypes.c_int32),
    ]


class NativeWindowRateStruct(ctypes.Structure):
    _fields_ = [
        ("start_s", ctypes.c_double),
        ("end_s", ctypes.c_double),
        ("center_s", ctypes.c_double),
        ("rate_s_per_day", ctypes.c_double),
        ("rate_sigma_s_per_day", ctypes.c_double),
        ("rate_uncertainty_s_per_day", ctypes.c_double),
        ("n_beats", ctypes.c_int64),
    ]


class NativeMetricsConfigStruct(ctypes.Structure):
    _fields_ = [
        ("sample_rate", ctypes.c_double),
        ("bph", ctypes.c_double),
        ("lift_angle_deg", ctypes.c_double),
        ("outlier_sigma", ctypes.c_double),
        ("window_s", ctypes.c_double),
        ("window_step_s", ctypes.c_double),
        ("trend_sigma", ctypes.c_double),
        ("trend_min_s_per_day_per_min", ctypes.c_double),
        ("trend_min_change_s_per_day", ctypes.c_double),
        ("amp_lo_deg", ctypes.c_double),
        ("amp_hi_deg", ctypes.c_double),
        ("amplitude_mismatch_deg", ctypes.c_double),
        ("drop_sharpness_ratio", ctypes.c_double),
        ("adc_drift_ppm", ctypes.c_double),
        ("adc_drift_sigma_ppm", ctypes.c_double),
        ("be_switch_ratio", ctypes.c_double),
        ("p1_keep_ratio", ctypes.c_double),
        ("p1_min_share", ctypes.c_double),
        ("has_lift_angle", ctypes.c_int32),
        ("quartz", ctypes.c_int32),
        ("min_beats_per_parity", ctypes.c_int32),
        ("max_iterations", ctypes.c_int32),
        ("trend_blocks", ctypes.c_int32),
        ("p1_timing", ctypes.c_int32),
        ("prev_p0_p1", ctypes.c_int32),
        ("prev_p1_p1", ctypes.c_int32),
        ("compute_windows", ctypes.c_int32),
        ("_pad", ctypes.c_int32),
    ]


class NativeMetricsResultStruct(ctypes.Structure):
    _fields_ = [
        ("n_beats", ctypes.c_int64),
        ("n_used_even", ctypes.c_int64),
        ("n_used_odd", ctypes.c_int64),
        ("n_excluded", ctypes.c_int64),
        ("excluded_fraction", ctypes.c_double),
        ("rate_s_per_day", ctypes.c_double),
        ("rate_sigma_s_per_day", ctypes.c_double),
        ("rate_sigma_block_s_per_day", ctypes.c_double),
        ("rate_uncertainty_s_per_day", ctypes.c_double),
        ("rate_clock_sigma_s_per_day", ctypes.c_double),
        ("beat_error_ms", ctypes.c_double),
        ("beat_error_sigma_ms", ctypes.c_double),
        ("beat_error_sigma_block_ms", ctypes.c_double),
        ("beat_error_uncertainty_ms", ctypes.c_double),
        ("beat_error_p3_ms", ctypes.c_double),
        ("amplitude_deg", ctypes.c_double),
        ("amplitude_tic_deg", ctypes.c_double),
        ("amplitude_tac_deg", ctypes.c_double),
        ("amplitude_la_uncertainty_deg", ctypes.c_double),
        ("lift_angle_deg", ctypes.c_double),
        ("sigma_even_us", ctypes.c_double),
        ("sigma_odd_us", ctypes.c_double),
        ("pair_sigma_even_us", ctypes.c_double),
        ("pair_sigma_odd_us", ctypes.c_double),
        ("rate_trend_s_per_day_per_min", ctypes.c_double),
        ("rate_trend_sigma_s_per_day_per_min", ctypes.c_double),
        ("window_sigma_factor", ctypes.c_double),
        ("span_s", ctypes.c_double),
        ("confidence", ctypes.c_double),
        ("rate_calibrated", ctypes.c_int32),
        ("rate_point_0_p1", ctypes.c_int32),
        ("rate_point_1_p1", ctypes.c_int32),
        ("beat_error_point", ctypes.c_int32),
        ("amplitude_parity", ctypes.c_int32),
        ("amplitude_indicative", ctypes.c_int32),
        ("rate_trend_flag", ctypes.c_int32),
        ("_pad", ctypes.c_int32),
    ]


class NativeTemplateInputStruct(ctypes.Structure):
    _fields_ = [
        ("valid", ctypes.c_int32),
        ("_pad", ctypes.c_int32),
        ("onset_p1", ctypes.c_double),
        ("onset_p3", ctypes.c_double),
        ("onset_ref", ctypes.c_double),
        ("env", ctypes.c_void_p),
        ("env_len", ctypes.c_size_t),
    ]


class NativeAdcDriftStruct(ctypes.Structure):
    _fields_ = [
        ("ppm", ctypes.c_double),
        ("sigma_ppm", ctypes.c_double),
        ("systematic_ppm", ctypes.c_double),
        ("span_s", ctypes.c_double),
        ("residual_us", ctypes.c_double),
        ("n_points", ctypes.c_int64),
        ("n_segments", ctypes.c_int64),
        ("level", ctypes.c_int32),
        ("method", ctypes.c_int32),
        ("note_kind", ctypes.c_int32),
        ("_pad", ctypes.c_int32),
    ]


class NativeSessionConfigStruct(ctypes.Structure):
    """``advtg_session_config`` (native/include/advtg.h)."""

    _fields_ = [
        ("sample_rate", ctypes.c_double),
        ("bph", ctypes.c_double),
        ("lift_angle_deg", ctypes.c_double),
        ("span_s", ctypes.c_double),
        ("window_s", ctypes.c_double),
        ("display_pre_ms", ctypes.c_double),
        ("display_post_ms", ctypes.c_double),
        ("wall_clock_sigma_ppm", ctypes.c_double),
        ("mono_clock_sigma_ppm", ctypes.c_double),
        ("calibration", NativeAdcDriftStruct),
        ("quartz", ctypes.c_int32),
        ("normalize", ctypes.c_int32),
        ("live_clock", ctypes.c_int32),
        ("has_calibration", ctypes.c_int32),
        ("wall_clock_disciplined", ctypes.c_int32),
        ("mono_clock_disciplined", ctypes.c_int32),
        ("stream_capacity", ctypes.c_int64),
    ]


class NativeDisplayStruct(ctypes.Structure):
    """``advtg_display``."""

    _fields_ = [
        ("rate_s_per_day", ctypes.c_double),
        ("beat_error_ms", ctypes.c_double),
        ("beat_error_p3_ms", ctypes.c_double),
        ("amplitude_deg", ctypes.c_double),
        ("amplitude_tic_deg", ctypes.c_double),
        ("amplitude_tac_deg", ctypes.c_double),
        ("confidence", ctypes.c_double),
        ("timing_confidence", ctypes.c_double),
    ]


class NativeSnapshotStruct(ctypes.Structure):
    """``advtg_snapshot``."""

    _fields_ = [
        ("metrics", NativeMetricsResultStruct),
        ("display", NativeDisplayStruct),
        ("last_beat", NativeBeatStruct),
        ("drift", NativeAdcDriftStruct),
        ("live_drift", NativeAdcDriftStruct),
        ("waveform_start", ctypes.c_double),
        ("snr_db", ctypes.c_double),
        ("detection_ratio", ctypes.c_double),
        ("usable_ratio", ctypes.c_double),
        ("audio_s", ctypes.c_double),
        ("real_bph", ctypes.c_double),
        ("tape_shift_ms", ctypes.c_double * 2),
        ("new_beats", ctypes.c_int64),
        ("beats_total", ctypes.c_int64),
        ("saturated_beats", ctypes.c_int64),
        ("waveform_len", ctypes.c_int64),
        ("tape_len", ctypes.c_int64),
        ("has_last_beat", ctypes.c_int32),
        ("locked", ctypes.c_int32),
        ("drift_source", ctypes.c_int32),
        ("clock_field", ctypes.c_int32),
        ("normalized", ctypes.c_int32),
        ("_pad", ctypes.c_int32),
    ]


class NativeStreamStatsStruct(ctypes.Structure):
    _fields_ = [
        ("total_samples", ctypes.c_int64),
        ("blocks", ctypes.c_int64),
        ("audio_s", ctypes.c_double),
        ("ingest_ratio", ctypes.c_double),
        ("max_gap_ms", ctypes.c_double),
        ("max_write_us", ctypes.c_double),
    ]


class NativeCaptureStatsStruct(ctypes.Structure):
    _fields_ = [
        ("callbacks", ctypes.c_int64),
        ("status_flags", ctypes.c_int64),
        ("callback_errors", ctypes.c_int64),
        ("gil_wait_over_block", ctypes.c_int64),
        ("gil_wait_invalid", ctypes.c_int64),
        ("gil_n", ctypes.c_int64),
        ("gil_hist", ctypes.c_int64 * 12),
        ("actual_sample_rate", ctypes.c_double),
        ("max_callback_us", ctypes.c_double),
        ("gil_wait_sum_us", ctypes.c_double),
        ("gil_wait_max_us", ctypes.c_double),
        ("active", ctypes.c_int32),
        ("channels", ctypes.c_int32),
    ]


@lru_cache(maxsize=1)
def lib() -> ctypes.CDLL | None:
    """The loaded native engine, or None (disabled, missing, wrong ABI or not loadable)."""
    if _disabled():
        return None
    for path in _candidate_paths():
        if not path.is_file():
            continue
        try:
            if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
                os.add_dll_directory(str(path.parent))
            cdll = ctypes.CDLL(str(path))
            _declare(cdll)
            if int(cdll.advtg_abi_version()) != ABI_VERSION:
                logger.warning(
                    "Motor nativo %s: ABI %s != %s; se usa Python",
                    path,
                    cdll.advtg_abi_version(),
                    ABI_VERSION,
                )
                continue
            return cdll
        except (OSError, AttributeError) as exc:
            logger.warning("No se pudo cargar el motor nativo %s: %s", path, exc)
    return None


def reload() -> ctypes.CDLL | None:
    """Forget the cached library (tests that change the environment)."""
    lib.cache_clear()
    return lib()


def is_native_available() -> bool:
    return lib() is not None


def have(*symbols: str) -> bool:
    """True when the native engine is loaded and exports every symbol in ``symbols``."""
    cdll = lib()
    return cdll is not None and all(hasattr(cdll, s) for s in symbols)


def native_version() -> str:
    cdll = lib()
    return "" if cdll is None else cdll.advtg_version().decode()


def simd_level() -> str:
    cdll = lib()
    return "" if cdll is None else cdll.advtg_simd_name().decode()


def native_arch_info() -> str:
    """e.g. ``"C++20 v2.0.0 [avx512] x86_64 SSE4.2 AVX2 FMA BMI2 AVX512F+DQ+BW+VL"``."""
    cdll = lib()
    if cdll is None:
        return "pure Python (native engine not loaded)"
    buf = ctypes.create_string_buffer(256)
    cdll.advtg_cpu_features(buf, len(buf))
    return f"C++20 v{native_version()} [{simd_level()}] {buf.value.decode()}"


def f64(a: object) -> NDArray[np.float64]:
    """C-contiguous float64 view/copy of ``a``."""
    return np.ascontiguousarray(a, dtype=np.float64)


def i64(a: object) -> NDArray[np.int64]:
    return np.ascontiguousarray(a, dtype=np.int64)


def ptr(a: NDArray) -> ctypes.c_void_p:
    """Address of a contiguous numpy array (keep the array alive while it is used)."""
    return ctypes.c_void_p(a.ctypes.data)
