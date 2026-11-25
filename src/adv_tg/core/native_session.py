"""``MeasurementSession`` run entirely by the native engine (``advtg_session_*``, advtg.h).

Same API and same ``SessionSnapshot`` as ``adv_tg.core.session.PyMeasurementSession``, but the
whole update (detector advance, sliding metrics, live ADC clock, display medians, paper tape
and oscilloscope waveform) runs in C++ (``native/src/session.cpp``) without touching Python
objects in between. That C++ session is the one a mobile app links (Android NDK / iOS), so
running the desktop app on it keeps a single, tested measurement engine for every platform.

The session reads the samples straight from the native ring buffer of the ``SampleStream`` it
is first updated with (``SampleStream._native``), so that stream must be native and stay the
same for the whole session (as the Python session assumes too).
"""

from __future__ import annotations

import ctypes
import math
from dataclasses import replace

import numpy as np

from adv_tg import native_bridge as _nb
from adv_tg.core.adc_clock import AdcDrift, ClockLevel, host_clock
from adv_tg.core.beat_detector import BeatDetector, DetectorStats, detector_stats_from_native
from adv_tg.core.beat_types import Beat, beat_period_s
from adv_tg.core.metrics import MetricsConfig, metrics_from_native
from adv_tg.core.sample_stream import SampleStream

__all__ = ["NATIVE_SESSION_SYMBOLS", "NativeMeasurementSession", "native_session_available"]

NATIVE_SESSION_SYMBOLS = (
    "advtg_session_config_default",
    "advtg_session_create",
    "advtg_session_destroy",
    "advtg_session_update",
    "advtg_session_waveform",
    "advtg_session_tape",
    "advtg_session_window_beats",
    "advtg_session_metric_windows",
    "advtg_session_metric_warnings",
    "advtg_session_detector_warnings",
    "advtg_session_detector_stats",
    "advtg_session_display_window",
    "advtg_session_last_error",
)

_METHODS = ("none", "host-envelope", "reference", "manual")
_NOTE_SESSION_TOO_SHORT = 6
_MIN_POINTS = 10  # adc_clock.fit_envelope defaults (ClockDriftTracker.estimate)
_MIN_SPAN_S = 20.0


def native_session_available() -> bool:
    """The loaded engine exports the session API."""
    return _nb.have(*NATIVE_SESSION_SYMBOLS)


def _drift_to_struct(d: AdcDrift) -> _nb.NativeAdcDriftStruct:
    method = _METHODS.index(d.method) if d.method in _METHODS else 0
    return _nb.NativeAdcDriftStruct(
        ppm=float(d.ppm),
        sigma_ppm=float(d.sigma_ppm),
        systematic_ppm=float(d.systematic_ppm),
        span_s=float(d.span_s),
        residual_us=float(d.residual_us),
        n_points=int(d.n_points),
        n_segments=int(d.n_segments),
        level=int(d.level),
        method=method,
    )


def _drift_from_struct(s: _nb.NativeAdcDriftStruct) -> AdcDrift:
    """``AdcDrift`` with the same note as ``ClockDriftTracker.estimate`` would give."""
    nk = int(s.note_kind)
    if nk == _NOTE_SESSION_TOO_SHORT:
        return AdcDrift.uncalibrated("sesión demasiado corta")
    if nk == 1:
        return AdcDrift.uncalibrated(f"pocos puntos ({int(s.n_points)} < {_MIN_POINTS})")
    if nk == 2:
        return AdcDrift.uncalibrated("demasiados saltos de reloj")
    if nk == 3:
        return AdcDrift.uncalibrated(
            f"base de tiempo corta ({float(s.span_s):.1f} s < {_MIN_SPAN_S:g} s)"
        )
    if int(s.level) == ClockLevel.UNCALIBRATED:
        return AdcDrift.uncalibrated()
    return AdcDrift(
        ppm=float(s.ppm),
        sigma_ppm=float(s.sigma_ppm),
        systematic_ppm=float(s.systematic_ppm),
        level=ClockLevel(int(s.level)),
        method=_METHODS[int(s.method)] if 0 <= int(s.method) < len(_METHODS) else "none",
        span_s=float(s.span_s),
        n_points=int(s.n_points),
        n_segments=int(s.n_segments),
        residual_us=float(s.residual_us),
        note=f"{int(s.n_segments)} tramos (saltos de reloj)" if nk == 5 else "",
    )


def _text(fn: object, h: ctypes.c_void_p) -> tuple[str, ...]:
    need = int(fn(h, None, 0))  # type: ignore[operator]
    if need <= 0:
        return ()
    buf = ctypes.create_string_buffer(need + 1)
    fn(h, buf, need + 1)  # type: ignore[operator]
    return tuple(buf.value.decode("utf-8", errors="replace").split("\n"))


class NativeMeasurementSession:
    """Detector + metrics + clock over one native ``SampleStream``, all in C++.

    Not thread-safe: call ``update`` from a single consumer thread.
    """

    def __init__(self, config: object) -> None:
        from adv_tg.core.session import SessionConfig  # circular at import time

        if not isinstance(config, SessionConfig):
            raise TypeError("se esperaba un SessionConfig")
        if not native_session_available():
            raise RuntimeError("el motor nativo no exporta la API de sesión")
        self.config = config
        fs = float(config.sample_rate)
        self._fs = fs
        lib = _nb.lib()
        c = _nb.NativeSessionConfigStruct()
        lib.advtg_session_config_default(ctypes.byref(c), fs, float(config.bph))
        c.lift_angle_deg = (
            float(config.lift_angle_deg) if config.lift_angle_deg is not None else math.nan
        )
        c.quartz = int(bool(config.quartz))
        c.normalize = int(bool(config.normalize))
        c.span_s = float(config.span_s)
        c.window_s = float(config.window_s)
        c.live_clock = int(bool(config.live_clock))
        c.display_pre_ms = float(config.display_pre_ms)
        c.display_post_ms = float(config.display_post_ms)
        wall, mono = host_clock("time"), host_clock("perf_counter")
        c.wall_clock_disciplined = int(wall.disciplined)
        c.wall_clock_sigma_ppm = float(wall.sigma_ppm)
        c.mono_clock_disciplined = int(mono.disciplined)
        c.mono_clock_sigma_ppm = float(mono.sigma_ppm)
        if config.calibration is not None:
            c.has_calibration = 1
            c.calibration = _drift_to_struct(config.calibration)
        self._cfg = c
        self._metrics_config = MetricsConfig(
            sample_rate=fs,
            bph=config.bph,
            lift_angle_deg=config.lift_angle_deg,
            quartz=config.quartz,
            window_s=min(config.window_s, config.span_s),
        )
        self._handle: ctypes.c_void_p | None = None
        self._stream: SampleStream | None = None
        self._py = None  # PyMeasurementSession fallback (stream without a native buffer)
        self._applied: tuple[AdcDrift, str] = (AdcDrift.uncalibrated(), "sin calibrar")
        # validate now (same errors as the Python session) with a probe session on a tiny
        # stream of its own; it also answers the properties until the first update
        probe = _nb.NativeSessionConfigStruct.from_buffer_copy(c)
        probe.stream_capacity = 4096
        h = lib.advtg_session_create(ctypes.byref(probe), None)
        if not h:
            raise ValueError(lib.advtg_session_last_error(None).decode("utf-8", "replace"))
        self._probe = ctypes.c_void_p(h)

    def __del__(self) -> None:
        lib = _nb.lib()
        for name in ("_handle", "_probe"):
            h = getattr(self, name, None)
            if h is not None and lib is not None:
                lib.advtg_session_destroy(h)
                setattr(self, name, None)

    # ------------------------------------------------------------------ binding
    def _bind(self, stream: SampleStream) -> ctypes.c_void_p | None:
        if self._py is not None:
            return None
        if self._handle is not None:
            if stream is not self._stream:
                raise ValueError("la sesión nativa está ligada a otro SampleStream")
            return self._handle
        native = getattr(stream, "_native", None)
        if native is None:
            return None  # not a native ring buffer: the Python session takes over
        lib = _nb.lib()
        h = lib.advtg_session_create(ctypes.byref(self._cfg), native)
        if not h:
            raise ValueError(lib.advtg_session_last_error(None).decode("utf-8", "replace"))
        self._handle = ctypes.c_void_p(h)
        self._stream = stream
        self._release_probe()
        return self._handle

    def _release_probe(self) -> None:
        if self._probe is not None:
            _nb.lib().advtg_session_destroy(self._probe)
            self._probe = None

    def _any_handle(self) -> ctypes.c_void_p:
        return self._handle if self._handle is not None else self._probe  # type: ignore[return-value]

    # ------------------------------------------------------------------ properties
    @property
    def detector_stats(self) -> DetectorStats:
        if self._py is not None:
            return self._py.detector_stats
        lib = _nb.lib()
        h = self._any_handle()
        st = _nb.NativeDetectorStatsStruct()
        lib.advtg_session_detector_stats(h, ctypes.byref(st))
        return detector_stats_from_native(st, _text(lib.advtg_session_detector_warnings, h))

    @property
    def display_window(self) -> tuple[int, int]:
        if self._py is not None:
            return self._py.display_window
        pre, n = ctypes.c_int64(0), ctypes.c_int64(0)
        _nb.lib().advtg_session_display_window(
            self._any_handle(), ctypes.byref(pre), ctypes.byref(n)
        )
        return int(pre.value), int(n.value)

    @property
    def metrics_config(self) -> MetricsConfig:
        if self._py is not None:
            return self._py.metrics_config
        drift = self._applied[0]
        sigma = drift.uncertainty_ppm if drift.level != ClockLevel.UNCALIBRATED else 0.0
        return replace(self._metrics_config, adc_drift_ppm=drift.ppm, adc_drift_sigma_ppm=sigma)

    @property
    def nominal_period_s(self) -> float:
        return beat_period_s(self.config.bph)

    # ------------------------------------------------------------------ update
    def update(self, stream: SampleStream):  # -> SessionSnapshot
        """Process the audio that arrived since the previous call and recompute the figures."""
        from adv_tg.core.session import DisplayFigures, SessionSnapshot

        h = self._bind(stream)
        if h is None:
            if self._py is None:
                from adv_tg.core.session import PyMeasurementSession

                self._py = PyMeasurementSession(self.config)
                self._release_probe()
            return self._py.update(stream)
        lib = _nb.lib()
        snap = _nb.NativeSnapshotStruct()
        if lib.advtg_session_update(h, ctypes.byref(snap)) != 0:
            raise RuntimeError(lib.advtg_session_last_error(h).decode("utf-8", "replace"))

        nw = int(lib.advtg_session_metric_windows(h, None, 0))
        wbuf = (_nb.NativeWindowRateStruct * max(1, nw))()
        if nw:
            lib.advtg_session_metric_windows(h, wbuf, nw)
        need = int(lib.advtg_session_metric_warnings(h, None, 0))
        mbuf = ctypes.create_string_buffer(max(1, need + 1))
        if need:
            lib.advtg_session_metric_warnings(h, mbuf, need + 1)
        metrics = metrics_from_native(snap.metrics, [wbuf[i] for i in range(nw)], mbuf.value)

        n_wave = int(snap.waveform_len)
        wave = np.empty(n_wave, dtype=np.float64)
        if n_wave:
            lib.advtg_session_waveform(h, _nb.ptr(wave), n_wave)
        n_tape = int(snap.tape_len)
        tt = np.empty(n_tape, dtype=np.float64)
        to = np.empty(n_tape, dtype=np.float64)
        tp = np.empty(n_tape, dtype=np.int8)
        if n_tape:
            lib.advtg_session_tape(h, _nb.ptr(tt), _nb.ptr(to), _nb.ptr(tp), n_tape)

        last: Beat | None = None
        if snap.has_last_beat:
            one = (_nb.NativeBeatStruct * 1)(snap.last_beat)
            last = BeatDetector._native_unpack_beats(one, 1)[0]

        live = _drift_from_struct(snap.live_drift)
        src = int(snap.drift_source)
        if src == 1 and self.config.calibration is not None:
            drift, source = (
                self.config.calibration,
                self.config.calibration_source or "calibración guardada",
            )
        elif src == 2:
            field = "wall_ns" if int(snap.clock_field) == 1 else "host_ns"
            drift, source = live, f"en vivo ({field}, {live.span_s:.0f} s)"
        else:
            drift, source = AdcDrift.uncalibrated(), "sin calibrar"
        self._applied = (drift, source)

        d = snap.display
        display = DisplayFigures(
            rate_s_per_day=float(d.rate_s_per_day),
            beat_error_ms=float(d.beat_error_ms),
            beat_error_p3_ms=float(d.beat_error_p3_ms),
            amplitude_deg=float(d.amplitude_deg),
            amplitude_tic_deg=float(d.amplitude_tic_deg),
            amplitude_tac_deg=float(d.amplitude_tac_deg),
            confidence=float(d.confidence),
            timing_confidence=float(d.timing_confidence),
        )
        return SessionSnapshot(
            metrics=metrics,
            new_beats=int(snap.new_beats),
            beats_total=int(snap.beats_total),
            last_beat=last,
            waveform=wave,
            waveform_start=float(snap.waveform_start),
            snr_db=float(snap.snr_db),
            detection_ratio=float(snap.detection_ratio),
            usable_ratio=float(snap.usable_ratio),
            saturated_beats=int(snap.saturated_beats),
            locked=bool(snap.locked),
            drift=drift,
            drift_source=source,
            live_drift=live,
            audio_s=float(snap.audio_s),
            detector_warnings=_text(lib.advtg_session_detector_warnings, h),
            real_bph=float(snap.real_bph),
            display=display,
            tape_time_s=tt,
            tape_offset_ms=to,
            tape_parity=tp,
            tape_shift_ms=(float(snap.tape_shift_ms[0]), float(snap.tape_shift_ms[1])),
            normalized=bool(snap.normalized),
        )
