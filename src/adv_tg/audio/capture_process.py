"""Child-process side of the isolated capture (phase 1d).

Why a separate process: while the GUI repaints, the GUI process's main thread holds the GIL
for long stretches (long C/C++ calls), so an audio callback living in that process waits up
to hundreds of ms and CoreAudio silently drops blocks. A child process has its own GIL, so its
callback is never delayed by the GUI.

``capture_child_main`` is the child's entry point (``multiprocessing`` "spawn" context). It:

* opens ``sounddevice.InputStream`` (1 channel, float32) in the child's main thread, or a
  pure-Python fake source for tests;
* runs a callback that only stamps ``time.perf_counter_ns()``, ``time.time_ns()`` and the
  driver's capture time (``adc_end_ns``), appends ``indata[:, 0]`` as bytes to a
  ``collections.deque`` (thread-safe ``append``) and updates cheap counters (no logging, no
  locks, exceptions only counted);
* runs a sender thread that drains the deque into ``data_conn.send_bytes`` frames
  ``FRAME_HEADER`` (seq uint64, host_ns int64, adc_ns int64, wall_ns int64, n_samples uint32)
  + float32 samples. If the pipe is full the sender blocks and the deque grows
  (``backlog_max``);
* reports events on ``evt_conn``: ``("opened", dict)``, ``("error", dict)``, ``("stats",
  dict)`` at 1 Hz and ``("stopped", dict)``;
* obeys ``"stop"`` (drain, then exit) and ``"shutdown"`` (exit now) on ``ctrl_conn``, and
  exits by itself when the parent disappears (EOF on ``ctrl_conn``).

Logging: the parent launches the child with ``ADV_TG_LOG_NAME=adv_tg_capture``, so every
logger in the child (``Debugger``/``LoggerSetup``) writes to ``logs/adv_tg_capture.log`` and
never shares a handler with the GUI process.

Imports: stdlib, numpy, ``adv_tg.audio.gil_stats`` and ``adv_tg.utils.debugger`` (neither
pulls Qt); ``sounddevice`` is imported lazily. Nothing from ``adv_tg.ui``.
"""

from __future__ import annotations

import collections
import struct
import sys
import threading
import time
import types
from typing import Any

import numpy as np

from adv_tg.audio.gil_stats import GilWaitRecorder
from adv_tg.utils.debugger import Debugger

__all__ = ["FRAME_HEADER", "LOG_NAME", "adc_end_ns", "capture_child_main"]

#: Frame header: sequence (uint64), host_ns, adc_ns, wall_ns (int64), float32 samples (uint32).
FRAME_HEADER = struct.Struct("<QqqqI")
#: Log file name used by the child (``logs/adv_tg_capture.log``).
LOG_NAME = "adv_tg_capture"
STATS_PERIOD_S = 1.0
SENDER_IDLE_S = 0.002
DRAIN_TIMEOUT_S = 2.0
FAKE_BLOCKSIZE = 512
FAKE_RAMP_MODULO = 1 << 24  # float32 represents integers exactly up to 2**24


def adc_end_ns(time_info: Any, frames: int, sample_rate: float) -> int:
    """Driver capture time of the END of a block, in ns of the PortAudio stream clock.

    ``time_info.inputBufferAdcTime`` stamps the FIRST sample; adding ``frames / fs`` refers it
    to the block end, like the ``end_index`` of the block log (blocks may vary in size).
    Callback-safe: no logging, never raises.

    Returns:
        The stamp in ns, or 0 when the host API does not provide it.
    """
    try:
        t = float(getattr(time_info, "inputBufferAdcTime", 0.0) or 0.0)
    except Exception:
        return 0
    if not t > 0.0:
        return 0
    return round((t + frames / sample_rate) * 1e9)


class _OpenFailure(Exception):
    """Opening failed; ``kind`` is ``"import"`` (no sounddevice/PortAudio) or ``"open"``."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class _FakeStream:
    """Real-time paced pure-Python source with the bits of ``InputStream`` the child uses.

    ``kind`` is ``"ramp"`` (sample k = k mod 2**24, to check continuity) or ``"tone"``.
    """

    def __init__(self, kind: str, samplerate: float, blocksize: int, callback: Any) -> None:
        self.kind = kind
        self.samplerate = float(samplerate)
        self.blocksize = int(blocksize) or FAKE_BLOCKSIZE
        self._callback = callback
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def time(self) -> float:
        return time.perf_counter()

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="FakeAudioSource", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        fs, n = self.samplerate, self.blocksize
        idx = np.arange(n, dtype=np.int64)
        t0_ns = time.perf_counter_ns()
        pa_t0 = self.time
        t0 = t0_ns / 1e9
        k = 0
        while not self._stop.is_set():
            wait = t0 + (k + 1) * n / fs - time.perf_counter()
            if wait > 0 and self._stop.wait(wait):
                break
            first = k * n
            if self.kind == "ramp":
                x = ((first + idx) % FAKE_RAMP_MODULO).astype(np.float32)
            else:
                t = (first + idx) / fs
                x = (
                    0.05 * np.sin(2 * np.pi * 440.0 * t) + 0.4 * (np.mod(t, 1 / 6) < 0.002)
                ).astype(np.float32)
            # host_ns: scheduled time of this block (ideal device clock), so OS timer
            # jitter (large on macOS CI) does not show up as gaps or ingest error.
            info = types.SimpleNamespace(
                currentTime=time.perf_counter(),
                host_ns=t0_ns + (k + 1) * n * 10**9 // int(fs),
                inputBufferAdcTime=pa_t0 + first / fs,
            )
            self._callback(x.reshape(-1, 1), n, info, 0)
            k += 1

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2.0)

    def close(self) -> None:
        self.stop()


class _ChildCapture:
    """State of the capture inside the child process."""

    def __init__(self, params: dict[str, Any], data_conn: Any) -> None:
        self.params = params
        self.fs = float(params["sample_rate"])
        self.data_conn = data_conn
        self.queue: collections.deque[tuple[int, int, int, bytes]] = collections.deque()
        self.stream: Any = None
        self._pa_clock: Any = None
        # Callback counters (written only by the audio thread)
        self.callbacks = 0
        self.status_flags = 0
        self.callback_errors = 0
        self.max_callback_ns = 0
        self.backlog_max = 0
        self.gil = GilWaitRecorder(self.fs)
        # Sender state (written only by the sender thread)
        self.seq_sent = 0
        self.pipe_broken = False
        self._sender_stop = threading.Event()
        self._sender: threading.Thread | None = None

    # ------------------------------------------------------------------ callback
    def callback(self, indata: Any, frames: int, time_info: Any, status: Any) -> None:
        """Audio callback: stamp, copy, append, count. Nothing else."""
        try:
            pa_now = self._pa_clock.time
        except Exception:
            pa_now = 0.0
        t0 = time.perf_counter_ns()
        wall = time.time_ns()
        try:
            self.callbacks += 1
            if status:
                self.status_flags += 1
            # The fake source supplies its scheduled time; real hardware does not.
            stamp = getattr(time_info, "host_ns", None) or t0
            adc = adc_end_ns(time_info, frames, self.fs)
            self.queue.append((stamp, adc, wall, indata[:, 0].tobytes()))
            backlog = len(self.queue)
            if backlog > self.backlog_max:
                self.backlog_max = backlog
        except Exception:
            self.callback_errors += 1
        dt = time.perf_counter_ns() - t0
        if dt > self.max_callback_ns:
            self.max_callback_ns = dt
        try:
            self.gil.record(pa_now, time_info.currentTime, frames)
        except Exception:
            self.gil.invalid += 1

    # ------------------------------------------------------------------ open/close
    def open(self) -> None:
        p = self.params
        fake = p.get("fake_source")
        if fake:
            self.stream = _FakeStream(str(fake), self.fs, int(p["blocksize"]), self.callback)
        else:
            try:
                import sounddevice as sd
            except (ImportError, OSError) as exc:
                raise _OpenFailure(
                    "import",
                    f"No se pudo cargar sounddevice/PortAudio en el proceso de captura: {exc}",
                ) from exc
            try:
                sd.check_input_settings(
                    device=p["device"], channels=1, dtype="float32", samplerate=self.fs
                )
                self.stream = sd.InputStream(
                    device=p["device"],
                    channels=1,
                    samplerate=self.fs,
                    blocksize=int(p["blocksize"]),
                    dtype="float32",
                    latency=p["latency"],
                    callback=self.callback,
                )
            except Exception as exc:
                raise _OpenFailure(
                    "open",
                    f"No se pudo abrir el dispositivo de audio {p['device']} a {self.fs:.0f} Hz: "
                    f"{exc}",
                ) from exc
        self._pa_clock = self.stream

    def start(self) -> None:
        self._sender = threading.Thread(target=self._send_loop, name="CaptureSender", daemon=True)
        self._sender.start()
        self.stream.start()

    def stream_active(self) -> bool:
        try:
            return bool(self.stream.active)
        except Exception:
            return False

    def stop(self, drain: bool) -> None:
        """Stop the stream, then drain (or drop) the backlog and end the sender."""
        stream = self.stream
        if stream is not None:
            for op in (stream.stop, stream.close):
                try:
                    op()
                except Exception:
                    pass
        if not drain:
            self.queue.clear()
        self._sender_stop.set()
        if self._sender is not None:
            self._sender.join(DRAIN_TIMEOUT_S)

    # ------------------------------------------------------------------ sender
    def _send_loop(self) -> None:
        q = self.queue
        pack = FRAME_HEADER.pack
        send = self.data_conn.send_bytes
        while True:
            try:
                host_ns, adc_ns, wall_ns, payload = q.popleft()
            except IndexError:
                if self._sender_stop.is_set():
                    return
                time.sleep(SENDER_IDLE_S)
                continue
            try:
                send(pack(self.seq_sent, host_ns, adc_ns, wall_ns, len(payload) // 4) + payload)
            except (OSError, ValueError):
                self.pipe_broken = True
                return
            self.seq_sent += 1

    # ------------------------------------------------------------------ stats
    def stats(self) -> dict[str, Any]:
        out = {
            "callbacks": self.callbacks,
            "status_flags": self.status_flags,
            "callback_errors": self.callback_errors,
            "max_callback_us": self.max_callback_ns / 1e3,
            "backlog_max": self.backlog_max,
            "backlog_now": len(self.queue),
            "seq_sent": self.seq_sent,
        }
        out.update(self.gil.snapshot())
        return out


def _send_event(evt_conn: Any, kind: str, payload: dict[str, Any]) -> bool:
    try:
        evt_conn.send((kind, payload))
        return True
    except (OSError, ValueError):
        return False


def capture_child_main(
    ctrl_conn: Any, data_conn: Any, evt_conn: Any, params: dict[str, Any]
) -> None:
    """Entry point of the capture child process (see module docstring).

    Args:
        ctrl_conn: Receives ``"stop"`` / ``"shutdown"`` from the parent.
        data_conn: Sends audio frames to the parent.
        evt_conn: Sends events to the parent.
        params: ``device``, ``sample_rate``, ``blocksize``, ``latency``, ``fake_source``.
    """
    dbg = Debugger.get("adv_tg.capture_child")
    log = dbg.logger
    cap = _ChildCapture(params, data_conn)
    reason = "stop"
    dbg.process_start("capture_child", **{k: params.get(k) for k in sorted(params)})
    try:
        try:
            cap.open()
        except _OpenFailure as exc:
            log.error("Apertura fallida (%s): %s", exc.kind, exc)
            _send_event(evt_conn, "error", {"kind": exc.kind, "message": str(exc)})
            reason = "open_failed"
            return
        cap.start()
        _send_event(
            evt_conn,
            "opened",
            {
                "actual_fs": float(getattr(cap.stream, "samplerate", cap.fs)),
                "blocksize": int(params["blocksize"]),
                "latency": params["latency"],
                "child_ns": time.perf_counter_ns(),
                "qt_loaded": any(m.startswith(("PyQt6", "pyqtgraph")) for m in sys.modules),
                "fake_source": params.get("fake_source"),
            },
        )
        dbg.state_change("capture_child", "ABRIENDO", "CAPTURANDO")
        next_stats = time.monotonic() + STATS_PERIOD_S
        while True:
            timeout = max(0.0, next_stats - time.monotonic())
            try:
                if ctrl_conn.poll(timeout):
                    msg = ctrl_conn.recv()
                    if msg in ("stop", "shutdown"):
                        reason = msg
                        break
            except (EOFError, OSError):
                reason = "parent_gone"
                break
            if time.monotonic() >= next_stats:
                next_stats += STATS_PERIOD_S
                if not _send_event(evt_conn, "stats", cap.stats()):
                    reason = "parent_gone"
                    break
            if cap.pipe_broken:
                reason = "data_pipe_closed"
                break
            if not cap.stream_active():
                reason = "stream_stopped"
                _send_event(
                    evt_conn,
                    "error",
                    {
                        "kind": "runtime",
                        "message": "El stream de audio se detuvo en el proceso de captura.",
                    },
                )
                break
    except Exception as exc:  # never die silently
        reason = "error"
        log.exception("Error en el proceso de captura")
        _send_event(
            evt_conn, "error", {"kind": "runtime", "message": f"{type(exc).__name__}: {exc}"}
        )
    finally:
        cap.stop(drain=reason == "stop")
        final = cap.stats()
        final["reason"] = reason
        _send_event(evt_conn, "stopped", final)
        dbg.process_end("capture_child", **final)
        for conn in (data_conn, evt_conn, ctrl_conn):
            try:
                conn.close()
            except Exception:
                pass
