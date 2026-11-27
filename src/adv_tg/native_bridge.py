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
    "TAG_CB_TYPE",
    "call_str_buf",
    "ensure_portaudio_hint",
    "ensure_sndfile_hint",
    "f64",
    "format_warning",
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

    vp = ctypes.c_void_p
    cp = ctypes.c_char_p
    sz = ctypes.c_size_t
    i32 = ctypes.c_int32
    i64 = ctypes.c_int64
    u64 = ctypes.c_uint64
    dbl = ctypes.c_double
    cint = ctypes.c_int

    if hasattr(cdll, "advtg_format_warning"):
        cdll.advtg_format_warning.argtypes = [vp, cp, ctypes.c_char_p, i32]
        cdll.advtg_format_warning.restype = i32
    if hasattr(cdll, "advtg_session_metric_warning_codes"):
        cdll.advtg_session_metric_warning_codes.argtypes = [vp, vp, i32]
        cdll.advtg_session_metric_warning_codes.restype = i32
        cdll.advtg_session_detector_warning_codes.argtypes = [vp, vp, i32]
        cdll.advtg_session_detector_warning_codes.restype = i32

    if hasattr(cdll, "advtg_detector_create"):
        cdll.advtg_detector_config_default.argtypes = [vp, dbl, dbl]
        cdll.advtg_detector_config_default.restype = None
        cdll.advtg_detector_create.argtypes = [vp]
        cdll.advtg_detector_create.restype = vp
        cdll.advtg_detector_destroy.argtypes = [vp]
        cdll.advtg_detector_destroy.restype = None
        cdll.advtg_detector_reset.argtypes = [vp]
        cdll.advtg_detector_reset.restype = None
        cdll.advtg_detector_process.argtypes = [vp, vp, sz, i64, vp, i64]
        cdll.advtg_detector_process.restype = i64
        cdll.advtg_detector_flush.argtypes = [vp, vp, i64]
        cdll.advtg_detector_flush.restype = i64
        cdll.advtg_detector_last_beats.argtypes = [vp, vp, i64]
        cdll.advtg_detector_last_beats.restype = i64
        cdll.advtg_detector_stats_get.argtypes = [vp, vp]
        cdll.advtg_detector_stats_get.restype = cint
        cdll.advtg_detector_warnings.argtypes = [vp, vp, i32]
        cdll.advtg_detector_warnings.restype = i32
        cdll.advtg_detector_warning_codes.argtypes = [vp, vp, i32]
        cdll.advtg_detector_warning_codes.restype = i32
        cdll.advtg_metrics_config_default.argtypes = [vp, dbl, dbl]
        cdll.advtg_metrics_config_default.restype = None
        cdll.advtg_metrics_compute.argtypes = [vp, vp, sz, vp, vp]
        cdll.advtg_metrics_compute.restype = cint
        cdll.advtg_metrics_last_windows.argtypes = [vp, i64]
        cdll.advtg_metrics_last_windows.restype = i64
        cdll.advtg_metrics_last_warnings.argtypes = [vp, i32]
        cdll.advtg_metrics_last_warnings.restype = i32
        cdll.advtg_metrics_last_warning_codes.argtypes = [vp, i32]
        cdll.advtg_metrics_last_warning_codes.restype = i32

    if hasattr(cdll, "advtg_pps_detector_create"):
        cdll.advtg_pps_detector_create.argtypes = [dbl, dbl]
        cdll.advtg_pps_detector_create.restype = vp
        cdll.advtg_pps_detector_destroy.argtypes = [vp]
        cdll.advtg_pps_detector_destroy.restype = None
        cdll.advtg_pps_detector_reset.argtypes = [vp, i64]
        cdll.advtg_pps_detector_reset.restype = None
        cdll.advtg_pps_detector_process.argtypes = [vp, vp, sz, i64, vp, i64]
        cdll.advtg_pps_detector_process.restype = i64
        cdll.advtg_pps_detector_last_pulses.argtypes = [vp, vp, i64]
        cdll.advtg_pps_detector_last_pulses.restype = i64
        cdll.advtg_pps_detector_rejected.argtypes = [vp]
        cdll.advtg_pps_detector_rejected.restype = i64
        cdll.advtg_pps_calibrator_create.argtypes = [dbl, dbl, dbl]
        cdll.advtg_pps_calibrator_create.restype = vp
        cdll.advtg_pps_calibrator_destroy.argtypes = [vp]
        cdll.advtg_pps_calibrator_destroy.restype = None
        cdll.advtg_pps_calibrator_reset.argtypes = [vp]
        cdll.advtg_pps_calibrator_reset.restype = None
        cdll.advtg_pps_calibrator_add.argtypes = [vp, vp, sz]
        cdll.advtg_pps_calibrator_add.restype = cint
        cdll.advtg_pps_calibrator_estimate.argtypes = [vp, vp]
        cdll.advtg_pps_calibrator_estimate.restype = cint
        cdll.advtg_pps_calibrator_residuals.argtypes = [vp, vp, vp, i64]
        cdll.advtg_pps_calibrator_residuals.restype = i64
        cdll.advtg_pps_calibrate_samples.argtypes = [vp, sz, dbl, dbl, vp]
        cdll.advtg_pps_calibrate_samples.restype = cint

    if hasattr(cdll, "advtg_audio_info"):
        cdll.advtg_set_sndfile_library.argtypes = [cp]
        cdll.advtg_set_sndfile_library.restype = cint
        cdll.advtg_audio_last_error.argtypes = []
        cdll.advtg_audio_last_error.restype = cp
        cdll.advtg_audio_info.argtypes = [cp, vp]
        cdll.advtg_audio_info.restype = cint
        cdll.advtg_audio_read_f64.argtypes = [cp, i64, vp, sz, vp, vp, i32]
        cdll.advtg_audio_read_f64.restype = cint
        cdll.advtg_audio_select_channel.argtypes = [vp, i64, i32, i32, vp]
        cdll.advtg_audio_select_channel.restype = cint
        cdll.advtg_audio_read_mono.argtypes = [cp, i32, i64, vp, sz, vp]
        cdll.advtg_audio_read_mono.restype = cint
        cdll.advtg_audio_read_tags.argtypes = [cp, TAG_CB_TYPE, vp]
        cdll.advtg_audio_read_tags.restype = cint
        cdll.advtg_audio_update_tags.argtypes = [cp, vp, sz]
        cdll.advtg_audio_update_tags.restype = cint
        cdll.advtg_audio_write_f64.argtypes = [cp, vp, i64, i32, i32, cp, cp, vp, sz, vp]
        cdll.advtg_audio_write_f64.restype = cint
        cdll.advtg_audio_reader_open.argtypes = [cp, i64, i32]
        cdll.advtg_audio_reader_open.restype = vp
        cdll.advtg_audio_reader_info.argtypes = [vp, vp, vp]
        cdll.advtg_audio_reader_info.restype = cint
        cdll.advtg_audio_reader_read_block.argtypes = [vp, vp, sz, vp]
        cdll.advtg_audio_reader_read_block.restype = i64
        cdll.advtg_audio_reader_close.argtypes = [vp]
        cdll.advtg_audio_reader_close.restype = None
        cdll.advtg_audio_writer_open.argtypes = [cp, i32, i32, cp, cp, vp, sz]
        cdll.advtg_audio_writer_open.restype = vp
        cdll.advtg_audio_writer_write.argtypes = [vp, vp, i64, i32, vp]
        cdll.advtg_audio_writer_write.restype = cint
        cdll.advtg_audio_writer_set_tag.argtypes = [vp, cp, cp]
        cdll.advtg_audio_writer_set_tag.restype = cint
        cdll.advtg_audio_writer_close.argtypes = [vp, cint, vp, vp]
        cdll.advtg_audio_writer_close.restype = cint

    if hasattr(cdll, "advtg_config_dir"):
        cdll.advtg_config_dir.argtypes = [vp, i32]
        cdll.advtg_config_dir.restype = i32
        cdll.advtg_default_log_dir.argtypes = [vp, i32]
        cdll.advtg_default_log_dir.restype = i32
        cdll.advtg_log_init.argtypes = [cp, cp]
        cdll.advtg_log_init.restype = cint
        cdll.advtg_log_write.argtypes = [cint, cp, cp]
        cdll.advtg_log_write.restype = cint
        cdll.advtg_log_file_path.argtypes = [vp, i32]
        cdll.advtg_log_file_path.restype = i32

    if hasattr(cdll, "advtg_sanitize_csv_cell"):
        cdll.advtg_sanitize_csv_cell.argtypes = [cp, vp, i32]
        cdll.advtg_sanitize_csv_cell.restype = i32
        cdll.advtg_safe_filename_base.argtypes = [cp, cp, vp, i32]
        cdll.advtg_safe_filename_base.restype = i32
        cdll.advtg_anonymize_text.argtypes = [cp, cp, cp, vp, i32]
        cdll.advtg_anonymize_text.restype = i32

    if hasattr(cdll, "advtg_load_watch_db"):
        cdll.advtg_load_watch_db.argtypes = [cp, vp, i32]
        cdll.advtg_load_watch_db.restype = i32

    if hasattr(cdll, "advtg_cal_store_best"):
        cdll.advtg_cal_last_error.argtypes = []
        cdll.advtg_cal_last_error.restype = cp
        cdll.advtg_cal_format_iso.argtypes = [i64, vp, i32]
        cdll.advtg_cal_format_iso.restype = i32
        cdll.advtg_cal_parse_iso.argtypes = [cp, vp]
        cdll.advtg_cal_parse_iso.restype = cint
        cdll.advtg_cal_age_days.argtypes = [i64, i64]
        cdll.advtg_cal_age_days.restype = dbl
        cdll.advtg_cal_effective.argtypes = [vp, i64, dbl, vp]
        cdll.advtg_cal_effective.restype = None
        cdll.advtg_drift_to_tags.argtypes = [vp, cp, cp, TAG_CB_TYPE, vp]
        cdll.advtg_drift_to_tags.restype = cint
        cdll.advtg_drift_from_tags.argtypes = [vp, sz, vp, vp, i32]
        cdll.advtg_drift_from_tags.restype = cint
        cdll.advtg_cal_store_records.argtypes = [cp, cp, i32, vp, i32]
        cdll.advtg_cal_store_records.restype = i32
        cdll.advtg_cal_store_best.argtypes = [cp, cp, i32, i64, dbl, vp]
        cdll.advtg_cal_store_best.restype = cint
        cdll.advtg_cal_store_add.argtypes = [cp, vp]
        cdll.advtg_cal_store_add.restype = cint
        cdll.advtg_cal_store_forget.argtypes = [cp, cp, i32]
        cdll.advtg_cal_store_forget.restype = i32
        cdll.advtg_cal_recording_tags.argtypes = [cp, cp, i32, i64, TAG_CB_TYPE, vp, vp, vp]
        cdll.advtg_cal_recording_tags.restype = cint

    if hasattr(cdll, "advtg_list_input_devices"):
        cdll.advtg_set_portaudio_library.argtypes = [cp]
        cdll.advtg_set_portaudio_library.restype = cint
        cdll.advtg_list_input_devices.argtypes = [vp, i32]
        cdll.advtg_list_input_devices.restype = i32
        cdll.advtg_audio_devices_text.argtypes = [vp, i32]
        cdll.advtg_audio_devices_text.restype = i32

    if hasattr(cdll, "advtg_write_results_csv"):
        cdll.advtg_write_results_csv.argtypes = [cp, vp, sz]
        cdll.advtg_write_results_csv.restype = cint
        cdll.advtg_write_global_stats_csv.argtypes = [cp, vp, sz]
        cdll.advtg_write_global_stats_csv.restype = cint
        cdll.advtg_write_traces_csv.argtypes = [cp, vp, vp, sz, vp, vp, sz, vp, vp, sz, vp, vp, sz]
        cdll.advtg_write_traces_csv.restype = cint

    if hasattr(cdll, "advtg_report_build"):
        cdll.advtg_report_default_name.argtypes = [cp, i64, vp, i32]
        cdll.advtg_report_default_name.restype = i32
        cdll.advtg_report_build.argtypes = [
            cp,
            cp,
            cp,
            cp,
            cp,
            vp,
            sz,
            cp,
            cp,
            cp,
            cp,
            cp,
            i64,
            i64,
            vp,
            i32,
        ]
        cdll.advtg_report_build.restype = cint

    if hasattr(cdll, "advtg_gear_extract"):
        cdll.advtg_escape_wheel_period_s.argtypes = [dbl, i32]
        cdll.advtg_escape_wheel_period_s.restype = dbl
        cdll.advtg_gear_last_error.argtypes = []
        cdll.advtg_gear_last_error.restype = cp
        cdll.advtg_gear_extract.argtypes = [vp, sz, dbl, dbl, dbl, i32, vp, vp]
        cdll.advtg_gear_extract.restype = vp
        cdll.advtg_gear_recompute.argtypes = [vp, i32]
        cdll.advtg_gear_recompute.restype = cint
        cdll.advtg_gear_destroy.argtypes = [vp]
        cdll.advtg_gear_destroy.restype = None
        cdll.advtg_gear_get_summary.argtypes = [vp, vp]
        cdll.advtg_gear_get_summary.restype = cint
        cdll.advtg_gear_get_components.argtypes = [vp, vp]
        cdll.advtg_gear_get_components.restype = cint
        cdll.advtg_gear_series_copy.argtypes = [
            vp,
            vp,
            vp,
            vp,
            vp,
            vp,
            vp,
            vp,
            vp,
            vp,
            vp,
            vp,
            vp,
            i64,
        ]
        cdll.advtg_gear_series_copy.restype = cint
        cdll.advtg_gear_outliers_copy.argtypes = [vp, vp, i64]
        cdll.advtg_gear_outliers_copy.restype = i64
        cdll.advtg_gear_spectra_len.argtypes = [vp, vp, vp]
        cdll.advtg_gear_spectra_len.restype = None
        cdll.advtg_gear_spectra_copy.argtypes = [vp, i32, vp, vp, i64, vp, vp, i64]
        cdll.advtg_gear_spectra_copy.restype = cint
        cdll.advtg_gear_window_features.argtypes = [vp, i32, dbl, vp, i32]
        cdll.advtg_gear_window_features.restype = i32
        cdll.advtg_gear_recording_features.argtypes = [vp, i32, vp]
        cdll.advtg_gear_recording_features.restype = cint
        cdll.advtg_compute_positional_deltas.argtypes = [vp, sz, vp]
        cdll.advtg_compute_positional_deltas.restype = cint
        cdll.advtg_diagnose.argtypes = [vp, vp, sz, vp, vp, sz, vp, i32]
        cdll.advtg_diagnose.restype = i32

    if hasattr(cdll, "advtg_mock_generate_tick_train"):
        cdll.advtg_mock_beat_delta_t_s.argtypes = [dbl, dbl, i32]
        cdll.advtg_mock_beat_delta_t_s.restype = dbl
        cdll.advtg_mock_beat_interval_s.argtypes = [i32, dbl]
        cdll.advtg_mock_beat_interval_s.restype = dbl
        cdll.advtg_mock_add_beat.argtypes = [vp, sz, dbl, dbl, dbl, cint]
        cdll.advtg_mock_add_beat.restype = None
        cdll.advtg_mock_generate_tick_train.argtypes = [
            dbl,
            i32,
            i32,
            dbl,
            dbl,
            dbl,
            dbl,
            dbl,
            u64,
            vp,
            sz,
        ]
        cdll.advtg_mock_generate_tick_train.restype = cint
        cdll.advtg_mock_telemetry_create.argtypes = [u64]
        cdll.advtg_mock_telemetry_create.restype = vp
        cdll.advtg_mock_telemetry_destroy.argtypes = [vp]
        cdll.advtg_mock_telemetry_destroy.restype = None
        cdll.advtg_mock_telemetry_step.argtypes = [vp, dbl, dbl, dbl, dbl, dbl, dbl, vp]
        cdll.advtg_mock_telemetry_step.restype = None
        cdll.advtg_mock_telemetry_get_drift.argtypes = [vp, dbl, dbl, vp]
        cdll.advtg_mock_telemetry_get_drift.restype = None
        cdll.advtg_synth_config_default.argtypes = [vp]
        cdll.advtg_synth_config_default.restype = None
        cdll.advtg_synth_watch.argtypes = [vp, vp, sz]
        cdll.advtg_synth_watch.restype = i64


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


TAG_CB_TYPE = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p)


class NativeWarningStruct(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_int32),
        ("param_int", ctypes.c_int32),
        ("args", ctypes.c_double * 4),
    ]


class NativeBatchDetectorConfigStruct(ctypes.Structure):
    _fields_ = [
        ("sample_rate", ctypes.c_double),
        ("bph", ctypes.c_double),
        ("lift_angle_deg", ctypes.c_double),
        ("quartz", ctypes.c_int32),
        ("normalize", ctypes.c_int32),
    ]


class NativeBatchMetricsConfigStruct(ctypes.Structure):
    _fields_ = [
        ("sample_rate", ctypes.c_double),
        ("bph", ctypes.c_double),
        ("lift_angle_deg", ctypes.c_double),
        ("adc_drift_ppm", ctypes.c_double),
        ("adc_drift_sigma_ppm", ctypes.c_double),
        ("window_s", ctypes.c_double),
        ("window_step_s", ctypes.c_double),
        ("quartz", ctypes.c_int32),
        ("has_lift_angle", ctypes.c_int32),
        ("compute_windows", ctypes.c_int32),
        ("_pad", ctypes.c_int32),
    ]


class NativePpsEstimateStruct(ctypes.Structure):
    _fields_ = [
        ("drift", NativeAdcDriftStruct),
        ("pulses", ctypes.c_int64),
        ("used", ctypes.c_int64),
        ("missing", ctypes.c_int64),
        ("jumps", ctypes.c_int64),
        ("rejected", ctypes.c_int64),
        ("span_s", ctypes.c_double),
        ("jitter_us", ctypes.c_double),
        ("excess", ctypes.c_double),
        ("valid", ctypes.c_int32),
        ("ready", ctypes.c_int32),
        ("note", ctypes.c_char * 256),
    ]


class NativeAudioFileInfoStruct(ctypes.Structure):
    _fields_ = [
        ("frames", ctypes.c_int64),
        ("sample_rate", ctypes.c_int32),
        ("channels", ctypes.c_int32),
        ("duration_s", ctypes.c_double),
        ("format", ctypes.c_char * 16),
        ("subtype", ctypes.c_char * 16),
    ]


class NativeAudioTagStruct(ctypes.Structure):
    _fields_ = [
        ("key", ctypes.c_char_p),
        ("value", ctypes.c_char_p),
    ]


class NativeWatchCaliberStruct(ctypes.Structure):
    _fields_ = [
        ("name", ctypes.c_char * 128),
        ("bph", ctypes.c_int32),
        ("_pad", ctypes.c_int32),
        ("lift_angle_deg", ctypes.c_double),
    ]


class NativeCalibrationStruct(ctypes.Structure):
    _fields_ = [
        ("device", ctypes.c_char * 256),
        ("sample_rate", ctypes.c_int32),
        ("_pad", ctypes.c_int32),
        ("drift", NativeAdcDriftStruct),
        ("measured_at_epoch_s", ctypes.c_int64),
        ("note", ctypes.c_char * 256),
    ]


class NativeAudioDeviceInfoStruct(ctypes.Structure):
    _fields_ = [
        ("device_id", ctypes.c_int32),
        ("max_input_channels", ctypes.c_int32),
        ("default_samplerate", ctypes.c_double),
        ("is_synthetic", ctypes.c_int32),
        ("_pad", ctypes.c_int32),
        ("name", ctypes.c_char * 256),
    ]


class NativeCsvResultRowStruct(ctypes.Structure):
    _fields_ = [
        ("test_name", ctypes.c_char_p),
        ("caliber", ctypes.c_char_p),
        ("bph", ctypes.c_int32),
        ("lift_angle", ctypes.c_double),
        ("sample_rate", ctypes.c_int32),
        ("position", ctypes.c_char_p),
        ("record_type", ctypes.c_char_p),
        ("elapsed_s", ctypes.c_char_p),
        ("rate_spd", ctypes.c_char_p),
        ("beat_error_ms", ctypes.c_char_p),
        ("amplitude_deg", ctypes.c_char_p),
        ("confidence", ctypes.c_char_p),
        ("snr_db", ctypes.c_char_p),
    ]


class NativeCsvGlobalRowStruct(ctypes.Structure):
    _fields_ = [
        ("test_name", ctypes.c_char_p),
        ("caliber", ctypes.c_char_p),
        ("bph", ctypes.c_int32),
        ("lift_angle", ctypes.c_double),
        ("position", ctypes.c_char_p),
        ("rate_spd", ctypes.c_char_p),
        ("beat_error_ms", ctypes.c_char_p),
        ("amplitude_deg", ctypes.c_char_p),
        ("confidence", ctypes.c_char_p),
        ("global_mean_spd", ctypes.c_char_p),
        ("positional_delta_spd", ctypes.c_char_p),
    ]


class NativeGearComponentStruct(ctypes.Structure):
    _fields_ = [
        ("name", ctypes.c_int32),
        ("series", ctypes.c_int32),
        ("expected_period_s", ctypes.c_double),
        ("period_s", ctypes.c_double),
        ("amplitude", ctypes.c_double),
        ("rate_ripple_s_per_day", ctypes.c_double),
        ("fap", ctypes.c_double),
        ("resolvable", ctypes.c_int32),
        ("significant", ctypes.c_int32),
    ]


class NativeGearSummaryStruct(ctypes.Structure):
    _fields_ = [
        ("sample_rate", ctypes.c_double),
        ("bph", ctypes.c_double),
        ("duration_s", ctypes.c_double),
        ("rate_s_per_day", ctypes.c_double),
        ("lift_angle_deg", ctypes.c_double),
        ("escape_period_s", ctypes.c_double),
        ("beats_detected", ctypes.c_int64),
        ("missing_beats", ctypes.c_int64),
        ("outliers", ctypes.c_int64),
        ("n_series", ctypes.c_int64),
        ("escape_teeth", ctypes.c_int32),
        ("warning_mask", ctypes.c_uint32),
    ]


class NativeFeatureVectorStruct(ctypes.Structure):
    _fields_ = [
        ("window_start_s", ctypes.c_double),
        ("window_end_s", ctypes.c_double),
        ("rate_spd", ctypes.c_double),
        ("beat_error_ms", ctypes.c_double),
        ("amplitude_deg", ctypes.c_double),
        ("escape_power", ctypes.c_double),
        ("escape_amp_us", ctypes.c_double),
        ("fourth_power", ctypes.c_double),
        ("fourth_amp_us", ctypes.c_double),
        ("d12_mean_ms", ctypes.c_double),
        ("d12_var_ms2", ctypes.c_double),
        ("d12_skew", ctypes.c_double),
        ("d12_kurt", ctypes.c_double),
        ("d23_mean_ms", ctypes.c_double),
        ("d23_var_ms2", ctypes.c_double),
        ("d23_skew", ctypes.c_double),
        ("d23_kurt", ctypes.c_double),
        ("e1_e2", ctypes.c_double),
        ("e3_e2", ctypes.c_double),
        ("spurious_fraction", ctypes.c_double),
        ("n_beats", ctypes.c_int64),
        ("n_subevents", ctypes.c_int64),
        ("timing_jumps", ctypes.c_int64),
        ("whole", ctypes.c_int32),
        ("_pad", ctypes.c_int32),
    ]


class NativeDiagnosticFindingStruct(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_int32),
        ("level", ctypes.c_int32),
        ("values", ctypes.c_double * 4),
        ("keys", (ctypes.c_char * 32) * 4),
        ("n_evidence", ctypes.c_int32),
        ("_pad", ctypes.c_int32),
    ]


class NativePositionalRecordStruct(ctypes.Structure):
    _fields_ = [
        ("name", ctypes.c_char * 8),
        ("rate_spd", ctypes.c_double),
        ("beat_error_ms", ctypes.c_double),
        ("amplitude_deg", ctypes.c_double),
    ]


class NativePositionalDeltasStruct(ctypes.Structure):
    _fields_ = [
        ("delta_rate_hv_spd", ctypes.c_double),
        ("delta_amp_hv_deg", ctypes.c_double),
        ("be_spread_ms", ctypes.c_double),
        ("valid", ctypes.c_int32),
        ("n_positions", ctypes.c_int32),
        ("positions", (ctypes.c_char * 8) * 6),
    ]


class NativeMockTelemetrySampleStruct(ctypes.Structure):
    _fields_ = [
        ("rate_spd", ctypes.c_double),
        ("beat_error_ms", ctypes.c_double),
        ("amplitude_deg", ctypes.c_double),
        ("confidence", ctypes.c_double),
        ("snr_db", ctypes.c_double),
    ]


class NativeMockTelemetryDriftStruct(ctypes.Structure):
    _fields_ = [
        ("effective_fs", ctypes.c_double),
        ("drift_ppm", ctypes.c_double),
        ("rate_bias_spd", ctypes.c_double),
        ("sigma_ppm", ctypes.c_double),
        ("confidence", ctypes.c_double),
    ]


class NativeSynthConfigStruct(ctypes.Structure):
    _fields_ = [
        ("bph", ctypes.c_double),
        ("rate_s_per_day", ctypes.c_double),
        ("beat_error_ms", ctypes.c_double),
        ("amplitude_deg", ctypes.c_double),
        ("lift_angle_deg", ctypes.c_double),
        ("snr_db", ctypes.c_double),
        ("jitter_us", ctypes.c_double),
        ("odd_jitter_us", ctypes.c_double),
        ("p1_gain", ctypes.c_double),
        ("phase_mod_period_s", ctypes.c_double * 2),
        ("phase_mod_amp_us", ctypes.c_double * 2),
        ("level_mod_period_s", ctypes.c_double * 2),
        ("level_mod_depth", ctypes.c_double * 2),
        ("sample_rate", ctypes.c_int32),
        ("quartz", ctypes.c_int32),
        ("seconds", ctypes.c_double),
        ("seed", ctypes.c_uint64),
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


_HINTED_LIBS: set[tuple[int, str]] = set()


def _find_bundled_lib(pkg_name: str, patterns: tuple[str, ...]) -> str | None:
    try:
        import importlib.util

        spec = importlib.util.find_spec(pkg_name)
        if spec and spec.submodule_search_locations:
            base = Path(next(iter(spec.submodule_search_locations)))
            for pat in patterns:
                matches = sorted(base.rglob(pat))
                if matches:
                    return str(matches[0])
    except Exception:
        pass
    return None


def ensure_sndfile_hint() -> None:
    """Point native audio I/O to bundled libsndfile (pip soundfile / PyInstaller) if present."""
    cdll = lib()
    if cdll is None or not hasattr(cdll, "advtg_set_sndfile_library"):
        return
    key = (id(cdll), "sf")
    if key in _HINTED_LIBS:
        return
    _HINTED_LIBS.add(key)
    path = _find_bundled_lib(
        "_soundfile_data", ("*sndfile*.so*", "*sndfile*.dylib", "*sndfile*.dll")
    )
    if path:
        cdll.advtg_set_sndfile_library(path.encode("utf-8"))


def ensure_portaudio_hint() -> None:
    """Point native device enumeration to bundled PortAudio if present."""
    cdll = lib()
    if cdll is None or not hasattr(cdll, "advtg_set_portaudio_library"):
        return
    key = (id(cdll), "pa")
    if key in _HINTED_LIBS:
        return
    _HINTED_LIBS.add(key)
    path = _find_bundled_lib(
        "_sounddevice_data", ("*portaudio*.so*", "*portaudio*.dylib", "*portaudio*.dll")
    )
    if path:
        cdll.advtg_set_portaudio_library(path.encode("utf-8"))


def call_str_buf(fn: object, *args: object, initial: int = 512) -> str:
    """Call a C string-returning function `(args..., char* buf, int32_t cap) -> int32_t`."""
    cap = max(16, int(initial))
    buf = ctypes.create_string_buffer(cap)
    n = int(fn(*args, buf, cap))  # type: ignore[operator]
    if n < 0:
        raise RuntimeError(f"native call returned error {n}")
    if n >= cap:
        buf = ctypes.create_string_buffer(n + 1)
        fn(*args, buf, n + 1)  # type: ignore[operator]
    return buf.value.decode("utf-8", errors="replace")


def format_warning(warning: NativeWarningStruct, lang: str = "es") -> str:
    """Format an ``advtg_warning`` in ``"es"`` or ``"en"`` via native library."""
    cdll = lib()
    if cdll is None or not hasattr(cdll, "advtg_format_warning"):
        return ""
    return call_str_buf(
        cdll.advtg_format_warning,
        ctypes.byref(warning),
        lang.encode("utf-8"),
        initial=256,
    )
