"""Live analysis isolated in a child process (phase 5): the GUI never waits for the DSP.

Why. Running ``MeasurementSession.update`` (~9 ms per 0.25 s of audio on a CM18 take) in a
thread of the GUI process blocked the GUI thread up to 60 ms (7-11 frames > 33 ms in 40 s
of replay; none without the analysis): long numpy/scipy calls keep the GIL, and a shorter
``sys.setswitchinterval`` did not help. The same reason that moved the capture to a child
process in phase 1d. A child process has its own GIL.

Design (one persistent child per worker, started with the "spawn" context):

* Parent -> child, one duplex ``Pipe``:
  ``("start", SessionConfig, capacity)``, ``("data", start, samples, ends, host, adc, wall)``,
  ``("update",)``, ``("stop",)``, ``("quit",)``.
* Child -> parent: ``("snap", SessionSnapshot)`` for each ``update``; ``("error", text)``.
* ``data`` carries the new samples of the parent's ``SampleStream`` and the clock records of
  the blocks that ended in them; the child rewrites the same blocks with the same stamps into
  its own ``SampleStream``, so the live clock estimate is identical. Samples the parent lost
  (overwritten before being forwarded) are written as exact zeros with no stamp: the detector
  treats them as a data gap (``AFTER_GAP``), as with a stream overrun.
* At most ``MAX_PENDING`` updates are in flight: if the child falls behind, requests are
  skipped instead of queued.
* No deadlock on small pipe buffers (~8 KB on macOS): a reader thread in the child always
  drains the parent's messages into a queue, so the parent's ``send`` never waits for a child
  that is itself blocked sending a snapshot the parent is not reading yet.

Parent cost: one ``send`` per tick (a few KB, the pipe write releases the GIL) and unpickling
4 snapshots per second. The child logs to ``logs/adv_tg_analysis.log``.

``LocalAnalyzer`` offers the same interface running in the calling thread (fallback when the
child cannot be started, and for tests).
"""

from __future__ import annotations

import multiprocessing as mp
import queue
import threading
import time
from typing import Any

import numpy as np

from adv_tg.core.sample_stream import SamplesOverwritten, SampleStream
from adv_tg.core.session import MeasurementSession, SessionConfig, SessionSnapshot
from adv_tg.utils.debugger import Debugger

__all__ = ["LOG_NAME", "LocalAnalyzer", "ProcessAnalyzer", "analysis_child_main"]

_dbg = Debugger.get(__name__)
logger = _dbg.logger

LOG_NAME = "adv_tg_analysis"
MAX_PENDING = 2
JOIN_TIMEOUT_S = 2.0


# ---------------------------------------------------------------------- child
def _write_blocks(stream: SampleStream, start: int, samples: np.ndarray, rows: tuple) -> None:
    """Rewrite the parent's blocks (same boundaries and stamps) into ``stream``."""
    ends, host, adc, wall = rows
    gap = start - stream.total_samples
    if gap > 0:  # samples the parent lost: a data gap, no clock stamp
        stream.write(np.zeros(gap, dtype=np.float32), host_ns=0)
    pos = start
    for e, h, a, w in zip(ends.tolist(), host.tolist(), adc.tolist(), wall.tolist(), strict=True):
        if e <= pos:
            continue
        stream.write(
            samples[pos - start : e - start], host_ns=int(h), adc_ns=int(a), wall_ns=int(w)
        )
        pos = e


def _drain(conn: Any, inbox: queue.SimpleQueue) -> None:  # pragma: no cover - child
    """Child reader thread: move every message to ``inbox`` as soon as it arrives."""
    while True:
        try:
            inbox.put(conn.recv())
        except (EOFError, OSError):
            inbox.put(("quit",))
            return


def analysis_child_main(conn: Any) -> None:  # pragma: no cover - runs in the child process
    """Entry point of the analysis child (see module docstring)."""
    from adv_tg.utils.logger_setup import LoggerSetup

    log = LoggerSetup.setup_logger("adv_tg.analysis_child")
    log.info("Proceso de análisis iniciado")
    inbox: queue.SimpleQueue = queue.SimpleQueue()
    threading.Thread(target=_drain, args=(conn, inbox), name="analysis-inbox", daemon=True).start()
    session: MeasurementSession | None = None
    stream: SampleStream | None = None
    while True:
        msg = inbox.get()
        kind = msg[0]
        try:
            if kind == "start":
                _, cfg, capacity = msg
                stream = SampleStream(int(capacity), cfg.sample_rate)
                session = MeasurementSession(cfg)
            elif kind == "data" and stream is not None:
                _, start, samples, ends, host, adc, wall = msg
                _write_blocks(stream, start, samples, (ends, host, adc, wall))
            elif kind == "update" and session is not None and stream is not None:
                conn.send(("snap", session.update(stream)))
            elif kind == "stop":
                session = stream = None
            elif kind == "quit":
                break
        except Exception as exc:  # report, keep serving
            log.exception("Error en el proceso de análisis")
            try:
                conn.send(("error", f"{type(exc).__name__}: {exc}"))
            except (EOFError, OSError):
                break
    log.info("Proceso de análisis terminado")


# ---------------------------------------------------------------------- parent side
class _Forwarder:
    """Tracks what part of the parent's stream was already handed to the analysis."""

    def __init__(self) -> None:
        self.sent = 0

    def chunk(self, stream: SampleStream) -> tuple[int, np.ndarray, tuple] | None:
        log = stream.clock_log()
        new = log.end_indices > self.sent
        if not np.any(new):
            return None
        rows = (log.end_indices[new], log.host_ns[new], log.adc_ns[new], log.wall_ns[new])
        last = int(rows[0][-1])
        start = max(self.sent, stream.oldest_available)
        try:
            samples = stream.read(start, last, np.float32)
        except SamplesOverwritten:
            start = stream.oldest_available
            samples = stream.read(start, last, np.float32)
        self.sent = last
        return start, samples, rows


class LocalAnalyzer:
    """Same interface as ``ProcessAnalyzer``, analysing in the calling thread."""

    in_process = False

    def __init__(self) -> None:
        self._session: MeasurementSession | None = None
        self._stream: SampleStream | None = None
        self._fwd = _Forwarder()
        self._ready: list[SessionSnapshot] = []

    def start(self, config: SessionConfig, capacity: int) -> None:
        self._session = MeasurementSession(config)
        self._stream = SampleStream(int(capacity), config.sample_rate)
        self._fwd = _Forwarder()
        self._ready = []

    def feed(self, stream: SampleStream) -> None:
        c = self._fwd.chunk(stream)
        if c is not None and self._stream is not None:
            _write_blocks(self._stream, c[0], c[1], c[2])

    def request_update(self) -> None:
        if self._session is not None and self._stream is not None:
            self._ready.append(self._session.update(self._stream))

    def poll(self) -> list[SessionSnapshot]:
        out, self._ready = self._ready, []
        return out

    def finish(self, timeout_s: float = 0.0) -> list[SessionSnapshot]:
        """Last update of the session, then stop it."""
        self.request_update()
        out = self.poll()
        self._session = self._stream = None
        return out

    def close(self) -> None:
        self._session = self._stream = None


class ProcessAnalyzer:
    """Client of the analysis child process (persistent across sessions)."""

    in_process = True

    def __init__(self) -> None:
        self._proc: Any = None
        self._conn: Any = None
        self._fwd = _Forwarder()
        self._pending = 0
        self.skipped = 0
        self.errors: list[str] = []

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.is_alive() and self._conn is not None

    def _ensure(self) -> None:
        if self.alive:
            return
        from adv_tg.audio.process_capture import _child_log_env

        ctx = mp.get_context("spawn")
        parent, child = ctx.Pipe(duplex=True)
        proc = ctx.Process(
            target=analysis_child_main, args=(child,), name="adv_tg-analysis", daemon=True
        )
        with _child_log_env(LOG_NAME):
            proc.start()
        child.close()
        self._proc, self._conn = proc, parent
        _dbg.process_start("proceso de análisis", pid=proc.pid)

    def start(self, config: SessionConfig, capacity: int) -> None:
        """Start a session (spawns the child the first time; non-blocking)."""
        self._ensure()
        self._fwd = _Forwarder()
        self._pending = 0
        self.skipped = 0
        self._conn.send(("start", config, int(capacity)))

    def _send(self, msg: tuple) -> bool:
        conn = self._conn
        if conn is None:
            return False
        try:
            conn.send(msg)
            return True
        except (EOFError, OSError) as exc:
            logger.error("El proceso de análisis no acepta datos: %s", exc)
            self._conn = None
            return False

    def feed(self, stream: SampleStream) -> None:
        c = self._fwd.chunk(stream)
        if c is not None:
            start, samples, (ends, host, adc, wall) = c
            self._send(("data", start, samples, ends, host, adc, wall))

    def request_update(self) -> None:
        if self._pending >= MAX_PENDING:
            self.skipped += 1
            return
        if self._send(("update",)):
            self._pending += 1

    def poll(self) -> list[SessionSnapshot]:
        out: list[SessionSnapshot] = []
        conn = self._conn
        if conn is None:
            return out
        try:
            while conn.poll():
                kind, payload = conn.recv()
                if kind == "snap":
                    self._pending = max(0, self._pending - 1)
                    out.append(payload)
                elif kind == "error":
                    self._pending = max(0, self._pending - 1)
                    self.errors.append(payload)
                    logger.error("Proceso de análisis: %s", payload)
        except (EOFError, OSError) as exc:
            logger.error("El proceso de análisis se cerró: %s", exc)
            self._conn = None
        return out

    def finish(self, timeout_s: float = 1.5) -> list[SessionSnapshot]:
        """Ask for the last update, wait for every pending answer and stop the session."""
        out: list[SessionSnapshot] = []
        if self._conn is None:
            return out
        try:
            self._conn.send(("update",))
            self._pending += 1
            deadline = time.monotonic() + timeout_s
            while self._pending > 0 and time.monotonic() < deadline:
                if self._conn.poll(0.02):
                    out += self.poll()
            self._conn.send(("stop",))
        except (EOFError, OSError) as exc:
            logger.error("El proceso de análisis no respondió al terminar: %s", exc)
        if self._pending:
            logger.warning("Proceso de análisis: %d actualizaciones sin respuesta", self._pending)
        return out

    def close(self) -> None:
        """Stop the child (bounded wait)."""
        proc, conn = self._proc, self._conn
        self._proc = self._conn = None
        if conn is not None:
            try:
                conn.send(("quit",))
            except (EOFError, OSError):
                pass
        if proc is not None:
            proc.join(JOIN_TIMEOUT_S)
            if proc.is_alive():
                proc.terminate()
                proc.join(JOIN_TIMEOUT_S)
            _dbg.process_end("proceso de análisis", exitcode=proc.exitcode)
        if conn is not None:
            conn.close()
