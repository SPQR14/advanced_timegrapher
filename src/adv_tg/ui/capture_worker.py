"""``CaptureStreamWorker``: real microphone audio for the GUI, WITHOUT measurement (phase 1b).

It opens the selected input device with ``AudioCapture`` + ``SampleStream`` and, from its own
``QThread`` driven by a ``QTimer`` (no busy loop), publishes:

* ``waveform_ready`` (~30/s): the newest ``WAVEFORM_WINDOW_S`` of raw audio, reduced with a
  min/max envelope (same format as ``MockStreamWorker``),
* ``level_updated`` (~10/s): peak dBFS, RMS dBFS and a saturation flag (peak >= -1 dBFS)
  over the samples received since the previous update,
* ``capture_health`` (~2/s): ingest ratio (%) and the number of PortAudio status flags.

Since phase 1d the PortAudio stream lives in a child process (``ProcessAudioCapture``): the
GUI repaint holds this process's GIL for long stretches and an in-process callback would lose
blocks. ``CAPTURE_IN_SEPARATE_PROCESS = False`` or ``ADV_TG_CAPTURE_INPROC=1`` forces the
in-process ``AudioCapture`` (debugging).

It never emits ``telemetry_ready`` nor ``drift_updated``: the measurement lives in the
subclass ``adv_tg.ui.measure_worker.MeasureStreamWorker``, which plugs into the reader thread
through ``_make_hook`` (an object with ``on_open(capture)``, ``tick(now)`` and ``close()``,
all called in the reader thread). A source that sets ``exhausted`` (file replay) ends the
session without an error.

Opening happens in the worker thread, so ``start_stream()`` never blocks the GUI. The device
is opened with ``PRIMARY_BLOCKSIZE``/``PRIMARY_LATENCY``; if that fails it retries once with
``FALLBACK_BLOCKSIZE``/``FALLBACK_LATENCY``. Any failure (``sounddevice``/PortAudio missing,
device not opening, effective sample rate mismatch, device stopping by itself) emits
``error_occurred`` (Spanish message) followed by ``session_finished``.

Threading: ``start_stream``/``stop_stream``/``shutdown`` run in the GUI thread. ``stop_stream``
asks the reader to stop the timer and close the capture inside its own thread, then waits at
most ``STOP_TIMEOUT_MS`` for the thread to end (logging an error if it takes longer).
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np
from PyQt6.QtCore import QMetaObject, QObject, Qt, QThread, QTimer, pyqtSlot

from adv_tg.audio.capture import AudioCapture
from adv_tg.audio.process_capture import ProcessAudioCapture
from adv_tg.core.models import WatchConfig
from adv_tg.core.sample_stream import SamplesOverwritten, SampleStream
from adv_tg.mock.generator import minmax_decimate
from adv_tg.ui.stream_worker_base import BaseStreamWorker
from adv_tg.utils.debugger import Debugger

__all__ = [
    "CaptureStreamWorker",
    "SlotProfiler",
    "level_text",
    "level_to_quality",
]

_dbg = Debugger.get(__name__)
logger = _dbg.logger

# Device opening (validated on hardware: 100 % ingest, max gap 21.7 ms).
PRIMARY_BLOCKSIZE: int = 1024
PRIMARY_LATENCY: str = "low"
FALLBACK_BLOCKSIZE: int = 0
FALLBACK_LATENCY: str = "high"

# Reader thread timing.
BUFFER_S: float = 10.0
TICK_MS: int = 33
WAVEFORM_WINDOW_S: float = 0.6
# 960 bins divide 0.6 s exactly at 48 and 96 kHz (no sample dropped by the envelope).
WAVEFORM_BINS: int = 960
LEVEL_PERIOD_S: float = 0.1
HEALTH_PERIOD_S: float = 0.5
STOP_TIMEOUT_MS: int = 2000
PERF_LOG_EVERY_S: float = 5.0
# Diagnostics (phase 1c): a tick interval above this factor x TICK_MS counts as reader lag.
READER_LAG_FACTOR: float = 2.0
LEVEL_LOG_EVERY_S: float = 1.0

# Level classification for the signal-quality bar (level/saturation only, no SNR).
SATURATION_DBFS: float = -1.0
LOW_LEVEL_DBFS: float = -40.0
NO_SIGNAL_DBFS: float = -70.0
BAR_FLOOR_DBFS: float = -60.0

# Capture isolation (phase 1d).
CAPTURE_IN_SEPARATE_PROCESS: bool = True
INPROC_ENV_VAR: str = "ADV_TG_CAPTURE_INPROC"

CaptureFactory = Callable[..., Any]
StreamFactory = Callable[[float], SampleStream]


def capture_in_separate_process() -> bool:
    """True unless ``CAPTURE_IN_SEPARATE_PROCESS`` is False or ``ADV_TG_CAPTURE_INPROC`` is set."""
    forced = os.environ.get(INPROC_ENV_VAR, "").strip().lower() in ("1", "true", "yes", "on")
    return CAPTURE_IN_SEPARATE_PROCESS and not forced


def _default_capture_factory(stream: SampleStream, **kwargs: Any) -> Any:
    """``ProcessAudioCapture`` by default, ``AudioCapture`` when forced in-process."""
    if capture_in_separate_process():
        return ProcessAudioCapture(stream, **kwargs)
    return AudioCapture(stream, **kwargs)


def _default_stream_factory(fs: float) -> SampleStream:
    return SampleStream(int(round(fs * BUFFER_S)), fs)


def _round_or_na(value: float | None) -> Any:
    return "n/a" if value is None else round(float(value), 1)


def _dbfs(value: float) -> float:
    return 20.0 * math.log10(value) if value > 0.0 else -math.inf


def level_to_quality(peak_dbfs: float, rms_dbfs: float, saturated: bool) -> tuple[int, str]:
    """Map a level reading to a bar value (0-100) and a class.

    Args:
        peak_dbfs: Peak level in dBFS (``-inf`` for silence).
        rms_dbfs: RMS level in dBFS (``-inf`` for silence).
        saturated: True when the peak reached ``SATURATION_DBFS``.

    Returns:
        ``(percent, kind)`` where ``kind`` is ``"ok"`` (usable level), ``"low"`` (low level)
        or ``"bad"`` (saturation or no signal).
    """
    if math.isfinite(peak_dbfs):
        pct = int(round(np.clip((peak_dbfs - BAR_FLOOR_DBFS) / -BAR_FLOOR_DBFS * 100.0, 0, 100)))
    else:
        pct = 0
    if saturated or not math.isfinite(rms_dbfs) or peak_dbfs < NO_SIGNAL_DBFS:
        return pct, "bad"
    if peak_dbfs < LOW_LEVEL_DBFS:
        return pct, "low"
    return pct, "ok"


def level_text(peak_dbfs: float, rms_dbfs: float, saturated: bool) -> str:
    """Short, language-neutral level readout, e.g. ``"-12.3 dBFS pk | RMS -30.1"``."""

    def fmt(v: float) -> str:
        return f"{v:.1f}" if math.isfinite(v) else "--"

    text = f"{fmt(peak_dbfs)} dBFS pk | RMS {fmt(rms_dbfs)}"
    return f"{text} ⚠" if saturated else text


class SlotProfiler:
    """Rate and max duration of a GUI slot (throttled DEBUG only; for performance checks)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.reset()

    def reset(self) -> None:
        """Forget the counters (call at the start of each session)."""
        self.count = 0
        self.max_ms = 0.0
        self._t_first: float | None = None

    @property
    def rate_hz(self) -> float:
        """Average calls per second since the first call."""
        if self._t_first is None or self.count < 2:
            return 0.0
        span = time.perf_counter() - self._t_first
        return (self.count - 1) / span if span > 0 else 0.0

    def record(self, t0: float) -> None:
        """Record one call that started at ``t0`` (``time.perf_counter()``)."""
        now = time.perf_counter()
        self.max_ms = max(self.max_ms, (now - t0) * 1000.0)
        self.count += 1
        if self._t_first is None:
            self._t_first = now
        _dbg.throttled(
            f"slot_{self.name}",
            "slot GUI %s: %.1f/s, máx %.2f ms",
            self.name,
            self.rate_hz,
            self.max_ms,
            every_s=PERF_LOG_EVERY_S,
        )


class _OpenError(Exception):
    """Capture could not be opened; ``args[0]`` is the user-facing (Spanish) message."""


class _CaptureReader(QObject):
    """Lives in the worker ``QThread``: opens the capture, ticks the timer, closes it."""

    def __init__(
        self,
        owner: CaptureStreamWorker,
        session: int,
        device: int | None,
        fs: float,
        stream: SampleStream,
        capture_factory: CaptureFactory,
        hook: Any = None,
    ) -> None:
        super().__init__()
        self._hook = hook
        self._owner = owner
        self._session = session
        self._device = device
        self._fs = fs
        self._stream = stream
        self._factory = capture_factory
        self._capture: Any = None
        self._timer: QTimer | None = None
        self._stopped = False
        self.open_params: tuple[int, str] | None = None
        # Counters (only touched in this thread)
        self._t_start = 0.0
        self._next_level = 0.0
        self._next_health = 0.0
        self._last_level_idx = 0
        self._waveforms = 0
        self._max_tick_ns = 0
        # Diagnostics (phase 1c)
        self._last_tick_ns = 0
        self._lag_total_ns = 0
        self._lag_count = 0
        self._lvl_next_log = 0.0
        self._lvl_peak = 0.0
        self._lvl_sumsq = 0.0
        self._lvl_n = 0
        self._lvl_sat = False
        self._lvl_blocks = 0

    # ------------------------------------------------------------------ open
    def _open(self) -> Any:
        attempts = [
            (PRIMARY_BLOCKSIZE, PRIMARY_LATENCY),
            (FALLBACK_BLOCKSIZE, FALLBACK_LATENCY),
        ]
        dev = self._device if self._device is not None else "predeterminado"
        last_exc: BaseException | None = None
        for i, (blocksize, latency) in enumerate(attempts):
            capture = None
            try:
                capture = self._factory(
                    self._stream,
                    device=self._device,
                    sample_rate=int(round(self._fs)),
                    blocksize=blocksize,
                    latency=latency,
                )
                capture.start()
            except (ImportError, OSError) as exc:
                self._safe_close(capture)
                raise _OpenError(
                    f"No se pudo cargar sounddevice/PortAudio ({exc}). "
                    "Instala PortAudio para capturar audio."
                ) from exc
            except Exception as exc:
                self._safe_close(capture)
                last_exc = exc
                if i + 1 < len(attempts):
                    nb, nl = attempts[i + 1]
                    _dbg.state_change(
                        "apertura de captura",
                        f"blocksize={blocksize}/latency={latency}",
                        f"blocksize={nb}/latency={nl}",
                        device=self._device,
                        error=str(exc),
                    )
                    logger.warning(
                        "Apertura con blocksize=%d latency=%s falló (%s); reintento con "
                        "blocksize=%d latency=%s",
                        blocksize,
                        latency,
                        exc,
                        nb,
                        nl,
                    )
                continue
            self.open_params = (blocksize, latency)
            if not getattr(capture, "sample_rate_matches", True):
                actual = getattr(capture, "actual_sample_rate", None)
                self._safe_close(capture)
                raise _OpenError(
                    f"La tasa de muestreo efectiva del dispositivo {dev} ({actual} Hz) no "
                    f"coincide con la pedida ({self._fs:.0f} Hz)."
                )
            return capture
        raise _OpenError(
            f"No se pudo abrir el dispositivo de audio {dev} a {self._fs:.0f} Hz: {last_exc}"
        )

    @staticmethod
    def _safe_close(capture: Any) -> None:
        if capture is None:
            return
        try:
            capture.close()
        except Exception as exc:
            logger.error("Error al cerrar la captura: %s", exc)

    # ------------------------------------------------------------------ slots
    @pyqtSlot()
    def run(self) -> None:
        """Open the device and start the timer (runs in the worker thread)."""
        if self._stopped:
            return
        _dbg.process_start("CaptureReader", session=self._session, device=self._device, fs=self._fs)
        try:
            self._capture = self._open()
        except _OpenError as exc:
            logger.error("Captura sin abrir: %s", exc.args[0])
            self._close_hook()
            self._owner._finish(self._session, exc.args[0])
            self._stopped = True
            QThread.currentThread().quit()
            return
        except Exception as exc:  # unexpected bug: report, never crash the thread
            logger.exception("Error inesperado al abrir la captura")
            self._close_hook()
            self._owner._finish(self._session, f"Error inesperado al abrir la captura: {exc}")
            self._stopped = True
            QThread.currentThread().quit()
            return
        if self._stopped:  # stop requested while opening
            self._teardown("detenida durante la apertura")
            return
        if self._hook is not None:
            try:
                self._hook.on_open(self._capture)
            except Exception:
                logger.exception("Error al preparar el análisis de la sesión")
        self._t_start = time.perf_counter()
        self._next_level = self._t_start + LEVEL_PERIOD_S
        self._next_health = self._t_start + HEALTH_PERIOD_S
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        _dbg.state_change(
            "CaptureStreamWorker",
            "ABRIENDO",
            "CAPTURANDO",
            blocksize=self.open_params[0] if self.open_params else None,
            latency=self.open_params[1] if self.open_params else None,
        )

    @pyqtSlot()
    def stop(self) -> None:
        """Stop the timer, close the capture and end the thread. Idempotent."""
        if self._stopped:
            QThread.currentThread().quit()
            return
        self._stopped = True
        self._teardown("detenida")

    def _close_hook(self) -> None:
        hook, self._hook = self._hook, None
        if hook is None:
            return
        try:
            hook.close()
        except Exception:
            logger.exception("Error al cerrar el análisis de la sesión")

    def _teardown(self, reason: str) -> None:
        if self._timer is not None:
            self._timer.stop()
        self._close_hook()
        capture = self._capture
        self._capture = None
        self._safe_close(capture)
        self._log_summary(reason, capture)
        QThread.currentThread().quit()

    def _log_summary(self, reason: str, capture: Any) -> None:
        elapsed = time.perf_counter() - self._t_start if self._t_start else 0.0
        st = self._stream.stats()
        logger.info(
            "Resumen captura (%s): %.1f s, plot %.1f FPS, tick máx %.2f ms, ingesta %.2f %%, "
            "overflows %d, errores callback %d, callback máx %.0f µs, apertura %s, "
            "callbacks %d, espera GIL media %.2f ms / p99 %.2f ms / máx %.2f ms, "
            "callbacks con espera > 1 bloque %d, histograma GIL %s, "
            "retraso del lector %.0f ms en %d ticks (> %.0fx %d ms), "
            "backlog máx %s, retraso del padre máx %s ms, tramas perdidas %s",
            reason,
            elapsed,
            self._waveforms / elapsed if elapsed > 0 else 0.0,
            self._max_tick_ns / 1e6,
            100.0 * st.ingest_ratio,
            getattr(capture, "status_flags", 0),
            getattr(capture, "callback_errors", 0),
            getattr(capture, "max_callback_us", 0.0),
            self.open_params,
            getattr(capture, "callbacks", 0),
            getattr(capture, "gil_wait_mean_us", 0.0) / 1000.0,
            getattr(capture, "gil_wait_p99_ms", 0.0),
            getattr(capture, "gil_wait_max_us", 0.0) / 1000.0,
            getattr(capture, "gil_wait_over_block", 0),
            list(getattr(capture, "gil_wait_hist", ())),
            self._lag_total_ns / 1e6,
            self._lag_count,
            READER_LAG_FACTOR,
            TICK_MS,
            getattr(capture, "backlog_max", "n/a"),
            _round_or_na(getattr(capture, "parent_lag_max_ms", None)),
            getattr(capture, "dropped_frames", "n/a"),
        )
        _dbg.process_end("CaptureReader", session=self._session, reason=reason)

    # ------------------------------------------------------------------ tick
    def _tick(self) -> None:
        t0 = time.perf_counter_ns()
        if self._last_tick_ns:  # diagnostics: reader lag
            interval = t0 - self._last_tick_ns
            if interval > READER_LAG_FACTOR * TICK_MS * 1_000_000:
                self._lag_total_ns += interval
                self._lag_count += 1
        self._last_tick_ns = t0
        capture = self._capture
        if capture is None or self._stopped:
            return
        try:
            if not capture.is_active:
                self._stopped = True
                if getattr(capture, "exhausted", False):
                    message = None  # a replayed file reached its end: a normal finish
                else:
                    message = getattr(capture, "last_error", None) or (
                        "El dispositivo de audio dejó de enviar datos."
                    )
                self._close_hook()  # last figures before session_finished
                self._owner._finish(self._session, message)
                self._teardown("fin del archivo" if message is None else "el dispositivo se detuvo")
                return
            self._emit_waveform()
            now = time.perf_counter()
            if now >= self._next_level:
                self._next_level = max(self._next_level + LEVEL_PERIOD_S, now)
                self._emit_level()
            if now >= self._next_health:
                self._next_health = max(self._next_health + HEALTH_PERIOD_S, now)
                st = self._stream.stats()
                self._owner.capture_health.emit(100.0 * st.ingest_ratio, int(capture.status_flags))
            if self._hook is not None:
                self._hook.tick(now)
        except Exception:
            _dbg.throttled(
                "capture_tick_error",
                "Error en el tick de captura",
                every_s=5.0,
                level=logging.ERROR,
            )
            logger.debug("Detalle del error en el tick", exc_info=True)
        dt = time.perf_counter_ns() - t0
        if dt > self._max_tick_ns:
            self._max_tick_ns = dt
        elapsed = time.perf_counter() - self._t_start
        _dbg.throttled(
            "capture_perf",
            "captura: plot %.1f FPS, tick máx %.2f ms",
            self._waveforms / elapsed if elapsed > 0 else 0.0,
            self._max_tick_ns / 1e6,
            every_s=PERF_LOG_EVERY_S,
        )

    def _emit_waveform(self) -> None:
        n = int(round(WAVEFORM_WINDOW_S * self._fs))
        _, x = self._stream.read_latest(n, np.float64)
        if x.size == 0:
            return
        if x.size > 2 * WAVEFORM_BINS:
            # minmax_decimate drops the tail remainder; drop the oldest samples instead.
            x = x[x.size % WAVEFORM_BINS :]
        time_ms, values = minmax_decimate(x, self._fs, WAVEFORM_BINS)
        self._owner.waveform_ready.emit(time_ms, values)
        self._waveforms += 1

    def _emit_level(self) -> None:
        stream = self._stream
        total = stream.total_samples
        start = max(self._last_level_idx, stream.oldest_available)
        if total <= start:
            return
        try:
            x = stream.read(start, total, np.float64)
        except SamplesOverwritten:
            self._last_level_idx = total
            return
        self._last_level_idx = total
        peak_db = _dbfs(float(np.max(np.abs(x))))
        rms_db = _dbfs(math.sqrt(float(np.mean(x * x))))
        self._owner.level_updated.emit(peak_db, rms_db, peak_db >= SATURATION_DBFS)
        self._log_level_1s(x, peak_db >= SATURATION_DBFS)

    def _log_level_1s(self, x: np.ndarray, saturated: bool) -> None:
        """Diagnostics: DEBUG level summary once per second (to calibrate the quality bar)."""
        self._lvl_peak = max(self._lvl_peak, float(np.max(np.abs(x))))
        self._lvl_sumsq += float(np.dot(x, x))
        self._lvl_n += x.size
        self._lvl_sat = self._lvl_sat or saturated
        now = time.perf_counter()
        if now < self._lvl_next_log:
            return
        self._lvl_next_log = now + LEVEL_LOG_EVERY_S
        blocks = self._stream.stats().blocks
        _dbg.throttled(
            "capture_level_1s",
            "nivel 1 s: pico %.1f dBFS, RMS %.1f dBFS, saturated=%s, bloques %d",
            _dbfs(self._lvl_peak),
            _dbfs(math.sqrt(self._lvl_sumsq / self._lvl_n)) if self._lvl_n else -math.inf,
            self._lvl_sat,
            blocks - self._lvl_blocks,
            every_s=LEVEL_LOG_EVERY_S * 0.5,
        )
        self._lvl_peak = 0.0
        self._lvl_sumsq = 0.0
        self._lvl_n = 0
        self._lvl_sat = False
        self._lvl_blocks = blocks


class CaptureStreamWorker(BaseStreamWorker):
    """Hardware engine: raw audio, level and capture health; no measurement yet.

    Args:
        config: Initial ``WatchConfig`` (its ``sample_rate`` is used at each start).
        capture_factory: Callable ``(stream, *, device, sample_rate, blocksize, latency)``
            returning an object with the ``AudioCapture`` interface (injectable for tests).
        stream_factory: Callable ``(fs) -> SampleStream`` (injectable for tests).
        parent: Qt parent.
    """

    def __init__(
        self,
        config: WatchConfig | None = None,
        *,
        capture_factory: CaptureFactory | None = None,
        stream_factory: StreamFactory | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(config, parent=parent)
        self._capture_factory: CaptureFactory = capture_factory or _default_capture_factory
        self._stream_factory: StreamFactory = stream_factory or _default_stream_factory
        self._lock = threading.Lock()
        self._session = 0
        self._active = False
        self._finished = True
        self._thread: QThread | None = None
        self._reader: _CaptureReader | None = None
        self._stuck_threads: list[QThread] = []
        self.stream: SampleStream | None = None

    # ------------------------------------------------------------------ helpers
    @property
    def open_params(self) -> tuple[int, str] | None:
        """``(blocksize, latency)`` actually used by the current/last session."""
        reader = self._reader
        return None if reader is None else reader.open_params

    def _make_hook(self, fs: float, stream: SampleStream) -> Any:
        """Per-session reader hook (see module docstring); None = capture only."""
        return None

    def _finish(self, session: int, error: str | None) -> None:
        """End ``session`` once: optional ``error_occurred`` then ``session_finished``."""
        with self._lock:
            if session != self._session or self._finished:
                return
            self._finished = True
            self._active = False
        if error:
            self.error_occurred.emit(error)
        self.session_finished.emit()

    def _join_thread(self) -> None:
        thread = self._thread
        reader = self._reader
        if thread is None:
            return
        if thread.isRunning() and reader is not None:
            QMetaObject.invokeMethod(reader, "stop", Qt.ConnectionType.QueuedConnection)
        t0 = time.perf_counter()
        if not thread.wait(STOP_TIMEOUT_MS):
            logger.error(
                "El hilo %s no terminó en %d ms; se abandona",
                thread.objectName(),
                STOP_TIMEOUT_MS,
            )
            self._stuck_threads.append(thread)  # keep it alive: destroying it would crash
        else:
            _dbg.process_end(
                f"join {thread.objectName()}",
                wait_ms=round((time.perf_counter() - t0) * 1000.0, 1),
            )
        self._thread = None

    # ------------------------------------------------------------------ contract
    def update_watch_config(self, config: WatchConfig) -> None:
        """Store the config; a sample-rate change only applies to the next session."""
        if self._active and config.sample_rate != self._config.sample_rate:
            logger.warning(
                "Cambio de frecuencia de muestreo (%s -> %s Hz) durante la captura: se aplicará "
                "en la próxima sesión",
                self._config.sample_rate,
                config.sample_rate,
            )
        super().update_watch_config(config)

    def start_stream(self) -> None:
        """Start capturing in a new worker thread (the device opens there)."""
        if self._active:
            logger.warning("CaptureStreamWorker.start_stream ignorado: ya está capturando")
            return
        self._join_thread()  # leftovers from a session that ended by itself
        fs = float(self._config.sample_rate)
        with self._lock:
            self._session += 1
            session = self._session
            self._finished = False
            self._active = True
        self.stream = self._stream_factory(fs)
        thread = QThread()
        thread.setObjectName(f"CaptureReader-{session}")
        reader = _CaptureReader(
            self,
            session,
            self._device_id,
            fs,
            self.stream,
            self._capture_factory,
            self._make_hook(fs, self.stream),
        )
        reader.moveToThread(thread)
        thread.finished.connect(reader.deleteLater)
        self._thread = thread
        self._reader = reader
        thread.start()
        QMetaObject.invokeMethod(reader, "run", Qt.ConnectionType.QueuedConnection)
        _dbg.process_start("CaptureStreamWorker", session=session, device=self._device_id, fs=fs)

    def stop_stream(self) -> None:
        """Stop capturing, close the device and end the thread (bounded wait)."""
        session = self._session
        self._join_thread()
        self._finish(session, None)

    def isRunning(self) -> bool:  # noqa: N802
        return self._active

    def shutdown(self) -> None:
        """Stop everything before the application exits."""
        self.stop_stream()
