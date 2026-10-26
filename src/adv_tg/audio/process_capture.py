"""``ProcessAudioCapture``: audio capture isolated in a child process (phase 1d).

Same public interface as ``AudioCapture`` (``start``/``stop``/``close``, ``is_active``,
``actual_sample_rate``, ``sample_rate_matches``, ``callbacks``, ``status_flags``,
``callback_errors``, ``max_callback_us``, ``gil_wait_*``) plus ``child_alive``, ``last_error``,
``backlog_max``, ``parent_lag_ms``/``parent_lag_max_ms`` and ``dropped_frames``.

The child (``adv_tg.audio.capture_process.capture_child_main``) is started with the
``multiprocessing`` "spawn" context on every platform and talks through three one-way pipes
(no shared memory): control (parent -> child), data and events (child -> parent).

A daemon receiver thread is the ONLY producer of the parent's ``SampleStream``: it writes each
frame with the ``host_ns`` stamped by the CHILD's callback, so ingest statistics (and the future
clock model) reflect the capture timing, not the parent's delays.

``time.perf_counter_ns()`` is a system-wide clock, comparable between processes of the same
machine: CLOCK_UPTIME_RAW / ``mach_absolute_time`` on macOS, CLOCK_MONOTONIC on Linux and
``QueryPerformanceCounter`` on Windows (all with an origin common to every process, unlike
``time.process_time``). This is also checked at run time: the child's clock reading in the
``opened`` event must fall between the parent's readings before spawning and after receiving
it (``clock_comparable``).
"""

from __future__ import annotations

import contextlib
import multiprocessing as mp
import os
import threading
import time
from collections.abc import Iterator
from multiprocessing.connection import wait as mp_wait
from typing import Any, Literal

import numpy as np

from adv_tg.audio.capture_process import FRAME_HEADER, LOG_NAME, capture_child_main
from adv_tg.audio.gil_stats import GIL_WAIT_EDGES_MS, hist_percentile_ms
from adv_tg.core.sample_stream import SampleStream
from adv_tg.utils.debugger import Debugger
from adv_tg.utils.logger_setup import LoggerSetup

__all__ = ["CaptureChildError", "ProcessAudioCapture"]

_dbg = Debugger.get(__name__)

OPEN_TIMEOUT_S = 5.0
STOP_TIMEOUT_S = 3.0
KILL_TIMEOUT_S = 1.0
SAMPLE_RATE_TOLERANCE = 1e-6

_spawn_lock = threading.Lock()


class CaptureChildError(RuntimeError):
    """The capture child could not be started or failed (message in Spanish)."""


@contextlib.contextmanager
def _child_log_env(name: str = LOG_NAME) -> Iterator[None]:
    """Make the spawned child log to ``logs/<name>.log`` (env is inherited at spawn)."""
    key = LoggerSetup.LOG_NAME_ENV_VAR
    with _spawn_lock:
        old = os.environ.get(key)
        os.environ[key] = name
        try:
            yield
        finally:
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


class ProcessAudioCapture:
    """``AudioCapture`` look-alike whose PortAudio stream lives in a child process.

    Args:
        stream: Destination ``SampleStream`` (the receiver thread is its only producer).
        device: PortAudio input device index, or ``None`` for the default input.
        sample_rate: Requested sample rate in Hz; must match ``stream.sample_rate``.
        blocksize: Frames per callback (0 lets PortAudio choose).
        latency: ``"high"`` or ``"low"``.
        fake_source: ``None`` for real audio, or ``"ramp"`` / ``"tone"`` for a fake source
            (tests and CI, no ``sounddevice`` needed).
        open_timeout_s: Max wait for the child's ``opened`` event.
        stop_timeout_s: Max wait for the child to stop before ``terminate()``/``kill()``.

    Raises:
        ValueError: On invalid arguments.
    """

    def __init__(
        self,
        stream: SampleStream,
        *,
        device: int | None,
        sample_rate: int,
        blocksize: int = 0,
        latency: Literal["high", "low"] = "high",
        fake_source: str | None = None,
        open_timeout_s: float = OPEN_TIMEOUT_S,
        stop_timeout_s: float = STOP_TIMEOUT_S,
    ) -> None:
        if not sample_rate > 0:
            raise ValueError(f"sample_rate must be positive, got {sample_rate}")
        if blocksize < 0:
            raise ValueError(f"blocksize must be >= 0, got {blocksize}")
        if latency not in ("high", "low"):
            raise ValueError(f"latency must be 'high' or 'low', got {latency!r}")
        if fake_source not in (None, "ramp", "tone"):
            raise ValueError(f"fake_source must be None, 'ramp' or 'tone', got {fake_source!r}")
        if abs(stream.sample_rate - sample_rate) > SAMPLE_RATE_TOLERANCE * sample_rate:
            raise ValueError(
                f"sample_rate {sample_rate} does not match SampleStream rate {stream.sample_rate}"
            )
        self._stream = stream
        self.device = device
        self.sample_rate = int(sample_rate)
        self.blocksize = int(blocksize)
        self.latency = latency
        self.fake_source = fake_source
        self.open_timeout_s = float(open_timeout_s)
        self.stop_timeout_s = float(stop_timeout_s)
        self.actual_sample_rate: float | None = None
        self.clock_comparable: bool | None = None
        self.child_qt_loaded: bool | None = None
        self.last_error: str | None = None
        self._proc: Any = None
        self._ctrl: Any = None
        self._data: Any = None
        self._evt: Any = None
        self._receiver: threading.Thread | None = None
        self._lock = threading.Lock()
        self._reset_counters()

    def _reset_counters(self) -> None:
        self._child_alive = False
        self._stopping = False
        self._stats: dict[str, Any] = {}
        self._next_seq = 0
        self.dropped_frames = 0
        self.duplicate_frames = 0
        self.frames_received = 0
        self._last_host_ns = 0
        self._parent_lag_max_ns = 0

    # ------------------------------------------------------------------ properties
    @property
    def child_alive(self) -> bool:
        """True while the child process is running and delivering."""
        return self._child_alive

    @property
    def is_active(self) -> bool:
        """True while capturing (child alive and not stopping)."""
        return self._proc is not None and self._child_alive and not self._stopping

    @property
    def pid(self) -> int | None:
        """PID of the current child (None when not started)."""
        return None if self._proc is None else self._proc.pid

    def _stat(self, key: str, default: Any = 0) -> Any:
        return self._stats.get(key, default)

    @property
    def callbacks(self) -> int:
        """Callbacks in the child (from the latest 1 Hz stats or the final report)."""
        return int(self._stat("callbacks"))

    @property
    def status_flags(self) -> int:
        """Callbacks with a non-empty PortAudio status in the child."""
        return int(self._stat("status_flags"))

    @property
    def callback_errors(self) -> int:
        """Exceptions swallowed by the child's callback."""
        return int(self._stat("callback_errors"))

    @property
    def max_callback_us(self) -> float:
        """Longest child callback, in microseconds."""
        return float(self._stat("max_callback_us", 0.0))

    @property
    def gil_wait_max_us(self) -> float:
        """Longest estimated GIL wait in the child, in microseconds."""
        return float(self._stat("gil_wait_max_us", 0.0))

    @property
    def gil_wait_mean_us(self) -> float:
        """Mean estimated GIL wait in the child, in microseconds."""
        return float(self._stat("gil_wait_mean_us", 0.0))

    @property
    def gil_wait_hist(self) -> tuple[int, ...]:
        """Child GIL-wait histogram (buckets of ``GIL_WAIT_EDGES_MS``)."""
        return tuple(self._stat("gil_wait_hist", [0] * (len(GIL_WAIT_EDGES_MS) + 1)))

    @property
    def gil_wait_p99_ms(self) -> float:
        """99th percentile of the child GIL wait (bucket edge capped at the max), in ms."""
        return min(hist_percentile_ms(self.gil_wait_hist, 0.99), self.gil_wait_max_us / 1000.0)

    @property
    def gil_wait_over_block(self) -> int:
        """Child callbacks whose wait exceeded one block."""
        return int(self._stat("gil_wait_over_block"))

    @property
    def gil_wait_invalid(self) -> int:
        """Child callbacks without a usable timestamp."""
        return int(self._stat("gil_wait_invalid"))

    @property
    def backlog_max(self) -> int:
        """Largest number of blocks queued in the child waiting for the pipe."""
        return int(self._stat("backlog_max"))

    @property
    def seq_sent(self) -> int:
        """Frames sent by the child (latest report)."""
        return int(self._stat("seq_sent"))

    @property
    def parent_lag_ms(self) -> float:
        """Now minus the child timestamp of the newest block written in the parent (ms)."""
        if not self._last_host_ns:
            return 0.0
        return (time.perf_counter_ns() - self._last_host_ns) / 1e6

    @property
    def parent_lag_max_ms(self) -> float:
        """Largest delay between a block's child timestamp and its write in the parent (ms)."""
        return self._parent_lag_max_ns / 1e6

    @property
    def sample_rate_matches(self) -> bool:
        """True when the child's effective sample rate equals the requested one (or unknown)."""
        if self.actual_sample_rate is None:
            return True
        return (
            abs(self.actual_sample_rate - self.sample_rate)
            <= SAMPLE_RATE_TOLERANCE * self.sample_rate
        )

    # ------------------------------------------------------------------ start
    def start(self) -> None:
        """Spawn the child and wait for it to open the device. Idempotent.

        Raises:
            ImportError: The child could not load ``sounddevice``/PortAudio.
            CaptureChildError: The child failed to open the device, died or timed out.
        """
        if self.is_active:
            return
        self._teardown()  # leftovers of a previous child
        self._reset_counters()
        self.last_error = None
        ctx = mp.get_context("spawn")
        ctrl_r, ctrl_w = ctx.Pipe(duplex=False)
        data_r, data_w = ctx.Pipe(duplex=False)
        evt_r, evt_w = ctx.Pipe(duplex=False)
        params = {
            "device": self.device,
            "sample_rate": self.sample_rate,
            "blocksize": self.blocksize,
            "latency": self.latency,
            "fake_source": self.fake_source,
        }
        proc = ctx.Process(
            target=capture_child_main,
            args=(ctrl_r, data_w, evt_w, params),
            name="adv_tg-capture",
            daemon=True,
        )
        t_before = time.perf_counter_ns()
        with _dbg.phase("process_capture_spawn", **params):
            with _child_log_env():
                proc.start()
        # Close the child's ends here so EOF is seen when the child exits.
        for conn in (ctrl_r, data_w, evt_w):
            conn.close()
        self._proc, self._ctrl, self._data, self._evt = proc, ctrl_w, data_r, evt_r
        try:
            opened = self._wait_opened(t_before)
        except BaseException:
            self._stopping = True
            self._teardown()
            raise
        self.actual_sample_rate = float(opened["actual_fs"])
        self.child_qt_loaded = bool(opened.get("qt_loaded"))
        self._child_alive = True
        self._receiver = threading.Thread(
            target=self._receive_loop, name="ProcessCaptureReceiver", daemon=True
        )
        self._receiver.start()
        _dbg.process_start(
            "ProcessAudioCapture",
            pid=proc.pid,
            device=self.device,
            fs=self.actual_sample_rate,
            blocksize=self.blocksize,
            latency=self.latency,
            fake=self.fake_source,
            clock_comparable=self.clock_comparable,
            child_qt_loaded=opened.get("qt_loaded"),
        )

    def _wait_opened(self, t_before: int) -> dict[str, Any]:
        deadline = time.monotonic() + self.open_timeout_s
        evt, proc = self._evt, self._proc
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CaptureChildError(
                    f"El proceso de captura no respondió en {self.open_timeout_s:.0f} s."
                )
            try:
                ready = evt.poll(min(0.1, remaining))
                msg = evt.recv() if ready else None
            except (EOFError, OSError):
                proc.join(KILL_TIMEOUT_S)
                raise CaptureChildError(
                    f"El proceso de captura terminó al arrancar (código {proc.exitcode})."
                ) from None
            if msg is None:
                if not proc.is_alive() and not evt.poll():
                    raise CaptureChildError(
                        f"El proceso de captura terminó al arrancar (código {proc.exitcode})."
                    )
                continue
            kind, payload = msg
            if kind == "opened":
                t_after = time.perf_counter_ns()
                child_ns = int(payload.get("child_ns", 0))
                self.clock_comparable = t_before <= child_ns <= t_after
                if not self.clock_comparable:
                    _dbg.logger.warning(
                        "perf_counter_ns del hijo (%d) fuera de la ventana del padre [%d, %d]",
                        child_ns,
                        t_before,
                        t_after,
                    )
                return payload
            if kind == "error":
                text = str(payload.get("message", "error desconocido"))
                self.last_error = text
                if payload.get("kind") == "import":
                    raise ImportError(text)
                raise CaptureChildError(text)

    # ------------------------------------------------------------------ receiver
    def _receive_loop(self) -> None:
        data, evt = self._data, self._evt
        conns = [data, evt]
        while conns:
            try:
                ready = mp_wait(conns, timeout=0.5)
            except OSError:
                break
            for conn in ready:
                try:
                    if conn is data:
                        self._on_frame(conn.recv_bytes())
                    else:
                        self._on_event(*conn.recv())
                except (EOFError, OSError):
                    conns.remove(conn)
                    if conn is data:
                        self._on_data_closed()
                except Exception:
                    _dbg.logger.exception("Trama o evento inválido del proceso de captura")
        self._child_alive = False

    def _on_frame(self, buf: bytes) -> None:
        seq, host_ns, adc_ns, wall_ns, n = FRAME_HEADER.unpack_from(buf, 0)
        if seq < self._next_seq:
            self.duplicate_frames += 1
            return
        if seq > self._next_seq:
            self.dropped_frames += seq - self._next_seq
        self._next_seq = seq + 1
        samples = np.frombuffer(buf, dtype=np.float32, count=n, offset=FRAME_HEADER.size)
        self._stream.write(samples, host_ns, adc_ns=adc_ns, wall_ns=wall_ns)
        self.frames_received += 1
        self._last_host_ns = host_ns
        lag = time.perf_counter_ns() - host_ns
        if lag > self._parent_lag_max_ns:
            self._parent_lag_max_ns = lag

    def _on_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind in ("stats", "stopped"):
            self._stats = payload
        elif kind == "error":
            self.last_error = str(payload.get("message", "error desconocido"))
            _dbg.logger.error("Proceso de captura: %s", self.last_error)

    def _on_data_closed(self) -> None:
        self._child_alive = False
        if self._stopping:
            return
        proc = self._proc
        code = None
        if proc is not None:
            proc.join(0.5)
            code = proc.exitcode
        if self.last_error is None:
            self.last_error = (
                f"El proceso de captura de audio terminó inesperadamente (código {code})."
            )
        _dbg.logger.error("%s", self.last_error)

    # ------------------------------------------------------------------ stop
    def stop(self) -> None:
        """Stop the child (drain, join, then terminate/kill as a last resort). Idempotent."""
        with self._lock:
            if self._proc is None:
                return
            self._stopping = True
            ctrl = self._ctrl
            if ctrl is not None:
                try:
                    ctrl.send("stop")
                except (OSError, ValueError):
                    pass
            receiver = self._receiver
            if receiver is not None:
                receiver.join(self.stop_timeout_s)
            self._teardown()

    def close(self) -> None:
        """Same as ``stop()`` (the child is always released)."""
        self.stop()

    def _teardown(self) -> None:
        """Join/terminate/kill the child and close every pipe (no zombies, no open fds)."""
        proc = self._proc
        if proc is not None:
            proc.join(self.stop_timeout_s)
            if proc.is_alive():
                _dbg.logger.warning("El proceso de captura no terminó; terminate()")
                proc.terminate()
                proc.join(KILL_TIMEOUT_S)
            if proc.is_alive():
                _dbg.logger.error("El proceso de captura sigue vivo; kill()")
                proc.kill()
                proc.join(KILL_TIMEOUT_S)
        receiver = self._receiver
        if receiver is not None and receiver is not threading.current_thread():
            receiver.join(KILL_TIMEOUT_S)
        for conn in (self._ctrl, self._data, self._evt):
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        if proc is not None:
            code = proc.exitcode
            with contextlib.suppress(ValueError):
                proc.close()
            _dbg.process_end(
                "ProcessAudioCapture",
                exitcode=code,
                frames=self.frames_received,
                dropped_frames=self.dropped_frames,
                duplicate_frames=self.duplicate_frames,
                callbacks=self.callbacks,
                status_flags=self.status_flags,
                callback_errors=self.callback_errors,
                backlog_max=self.backlog_max,
                parent_lag_max_ms=round(self.parent_lag_max_ms, 1),
                gil_wait_max_us=round(self.gil_wait_max_us, 1),
                last_error=self.last_error,
            )
        self._proc = self._ctrl = self._data = self._evt = None
        self._receiver = None
        self._child_alive = False

    def __enter__(self) -> ProcessAudioCapture:
        """Start capturing on context entry."""
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        """Stop the child on context exit."""
        self.close()
