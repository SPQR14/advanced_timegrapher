"""``Debugger``: thin facade over ``logging`` / ``LoggerSetup`` for structured debug traces.

Usage::

    dbg = Debugger.get(__name__)
    with dbg.phase("load_db", path=str(path)):
        ...
    dbg.state_change("capture", "INACTIVO", "ESTABILIZANDO", device=3)
    dbg.process_start("MockStreamWorker", seed=7)
    dbg.file_write(path, nbytes=1024)
    dbg.throttled("telemetry", "telemetry rate=%+.2f", rate, every_s=2.0)

Levels: the log file always receives DEBUG; the console shows INFO, or DEBUG when the
environment variable ``ADV_TG_DEBUG=1`` is set (see ``LoggerSetup``).

RULES (mandatory):

* It is FORBIDDEN to use the Debugger (or ``logging`` at all) inside audio callbacks
  (e.g. PortAudio/sounddevice callbacks) or per-sample loops. The audio thread must never
  log, block or allocate memory. Count events there and report them later from another
  thread.
* On high-frequency paths (telemetry slots, 30 FPS timers, worker loops) use only
  ``throttled(...)`` at DEBUG level.
* ``traced`` is only for functions that are NOT performance-critical.
"""

from __future__ import annotations

import functools
import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ClassVar, ParamSpec, TypeVar

from adv_tg.utils.logger_setup import LoggerSetup

P = ParamSpec("P")
R = TypeVar("R")


def _fmt_ctx(ctx: dict[str, Any]) -> str:
    """Render ``key=value`` pairs (empty string when there is no context)."""
    if not ctx:
        return ""
    return " " + " ".join(
        f"{k}={v!r}" if isinstance(v, str) else f"{k}={v}" for k, v in ctx.items()
    )


class Debugger:
    """Named, cached debug-trace helper (see module docstring for the rules)."""

    _instances: ClassVar[dict[str, Debugger]] = {}
    _instances_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, name: str) -> None:
        self.name = name
        self.logger: logging.Logger = LoggerSetup.setup_logger(name)
        self._throttle_lock = threading.Lock()
        self._throttle_last: dict[str, float] = {}
        self._throttle_suppressed: dict[str, int] = {}

    @classmethod
    def get(cls, name: str = "adv_tg") -> Debugger:
        """Return the cached ``Debugger`` for ``name`` (created on first use)."""
        with cls._instances_lock:
            inst = cls._instances.get(name)
            if inst is None:
                inst = cls(name)
                cls._instances[name] = inst
            return inst

    # ------------------------------------------------------------------ phases
    @contextmanager
    def phase(self, name: str, **ctx: Any) -> Iterator[None]:
        """Log ``▶ INICIO`` / ``■ FIN (X ms)``; on error log the traceback and re-raise."""
        self.logger.info("▶ INICIO [%s]%s", name, _fmt_ctx(ctx))
        t0 = time.perf_counter()
        try:
            yield
        except BaseException:
            ms = (time.perf_counter() - t0) * 1000.0
            self.logger.exception("✖ ERROR [%s] (%.1f ms)%s", name, ms, _fmt_ctx(ctx))
            raise
        ms = (time.perf_counter() - t0) * 1000.0
        self.logger.info("■ FIN [%s] (%.1f ms)", name, ms)

    def state_change(self, owner: str, old: Any, new: Any, **ctx: Any) -> None:
        """Log a state transition ``owner: old -> new``."""
        self.logger.info("ESTADO %s: %s -> %s%s", owner, old, new, _fmt_ctx(ctx))

    def process_start(self, name: str, **ctx: Any) -> None:
        """Log the start of a thread, worker, timer or stream."""
        self.logger.info("⏵ PROCESO INICIO %s%s", name, _fmt_ctx(ctx))

    def process_end(self, name: str, **ctx: Any) -> None:
        """Log the end of a thread, worker, timer or stream."""
        self.logger.info("⏹ PROCESO FIN %s%s", name, _fmt_ctx(ctx))

    # ------------------------------------------------------------------ files
    def file_read(
        self,
        path: Path | str,
        ok: bool = True,
        nbytes: int | None = None,
        error: BaseException | str | None = None,
        **ctx: Any,
    ) -> None:
        """Log a file read (INFO when ``ok``, ERROR otherwise)."""
        self._file_op("LECTURA", path, ok, nbytes, error, ctx)

    def file_write(
        self,
        path: Path | str,
        ok: bool = True,
        nbytes: int | None = None,
        error: BaseException | str | None = None,
        **ctx: Any,
    ) -> None:
        """Log a file write (INFO when ``ok``, ERROR otherwise)."""
        self._file_op("ESCRITURA", path, ok, nbytes, error, ctx)

    def _file_op(
        self,
        op: str,
        path: Path | str,
        ok: bool,
        nbytes: int | None,
        error: BaseException | str | None,
        ctx: dict[str, Any],
    ) -> None:
        if nbytes is None:
            try:
                nbytes = Path(path).stat().st_size
            except OSError:
                nbytes = None
        size = f"{nbytes} B" if nbytes is not None else "? B"
        if ok:
            self.logger.info("ARCHIVO %s OK '%s' (%s)%s", op, path, size, _fmt_ctx(ctx))
        else:
            self.logger.error(
                "ARCHIVO %s FALLÓ '%s' (%s): %s%s", op, path, size, error, _fmt_ctx(ctx)
            )

    # ------------------------------------------------------------------ helpers
    def traced(self, func: Callable[P, R]) -> Callable[P, R]:
        """Decorator logging entry/exit at DEBUG (not for performance-critical code)."""

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            qual = func.__qualname__
            self.logger.debug("→ %s", qual)
            t0 = time.perf_counter()
            try:
                result = func(*args, **kwargs)
            except BaseException as exc:
                self.logger.debug("✖ %s raised %s", qual, type(exc).__name__)
                raise
            self.logger.debug("← %s (%.1f ms)", qual, (time.perf_counter() - t0) * 1000.0)
            return result

        return wrapper

    def throttled(
        self,
        key: str,
        msg: str,
        *args: Any,
        every_s: float = 1.0,
        level: int = logging.DEBUG,
    ) -> bool:
        """Log ``msg % args`` at most once every ``every_s`` seconds per ``key``.

        Returns True when the message was emitted. Suppressed repetitions are counted
        and reported with the next emitted message.
        """
        now = time.monotonic()
        with self._throttle_lock:
            last = self._throttle_last.get(key)
            if last is not None and (now - last) < every_s:
                self._throttle_suppressed[key] = self._throttle_suppressed.get(key, 0) + 1
                return False
            self._throttle_last[key] = now
            suppressed = self._throttle_suppressed.pop(key, 0)
        if suppressed:
            msg = f"{msg} (+{suppressed} suprimidos)"
        self.logger.log(level, msg, *args)
        return True
