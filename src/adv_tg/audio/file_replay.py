"""``FileReplay``: a recording played into a ``SampleStream`` in real time (no hardware).

Used by ``capture_check --file``, by the GUI replay mode (``ADV_TG_REPLAY_FILE``, see
``adv_tg.ui.measure_worker``) and by the tests. Blocks are stamped with their scheduled time
(an ideal device clock) as ``host_ns``; ``wall_ns`` is not provided.
"""

from __future__ import annotations

import threading
import time

import numpy as np
from numpy.typing import NDArray

from adv_tg.core.sample_stream import SampleStream
from adv_tg.utils.debugger import Debugger

__all__ = ["FileReplay"]

_dbg = Debugger.get(__name__)


class FileReplay:
    """Feed a sample array into a ``SampleStream`` in blocks, paced in real time.

    A background thread plays the role of the PortAudio callback: each block is written
    when its last sample would have been captured. It exposes the same counters as
    ``AudioCapture``; ``exhausted`` turns True once the whole array was delivered (the source
    then stops by itself, like a device that ends normally).

    Args:
        stream: Destination ``SampleStream``.
        samples: Mono samples to replay.
        blocksize: Samples per block (> 0).
        speed: Replay speed (1 = real time; tests use more). The stamps follow the
            accelerated schedule, so a speed other than 1 looks like a wrong clock.
    """

    def __init__(
        self,
        stream: SampleStream,
        samples: NDArray[np.float32],
        blocksize: int,
        speed: float = 1.0,
    ) -> None:
        if blocksize <= 0:
            raise ValueError(f"blocksize must be positive, got {blocksize}")
        if not speed > 0:
            raise ValueError(f"speed must be positive, got {speed}")
        self.speed = float(speed)
        self._stream = stream
        self._samples = samples
        self.blocksize = int(blocksize)
        self.actual_sample_rate: float | None = stream.sample_rate
        self.callbacks = 0
        self.status_flags = 0
        self.callback_errors = 0
        self._max_callback_ns = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.exhausted = False

    @property
    def max_callback_us(self) -> float:
        """Longest simulated callback (one ``write``), in microseconds."""
        st_write = self._stream.stats().max_write_us
        return st_write if st_write > 0 else self._max_callback_ns / 1e3

    @property
    def is_active(self) -> bool:
        """True while the replay thread is running."""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Start the replay thread. Idempotent."""
        if self.is_active:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="FileReplay", daemon=True)
        _dbg.process_start("FileReplay", samples=len(self._samples), blocksize=self.blocksize)
        self._thread.start()

    def _run(self) -> None:
        fs = self._stream.sample_rate
        n_total = len(self._samples)
        t0 = time.perf_counter_ns()
        pos = 0
        while pos < n_total and not self._stop.is_set():
            end = min(pos + self.blocksize, n_total)
            deadline = t0 + int(end / fs / self.speed * 1e9)
            wait_s = (deadline - time.perf_counter_ns()) / 1e9
            if wait_s > 0 and self._stop.wait(wait_s):
                break
            t_cb = time.perf_counter_ns()
            try:
                self.callbacks += 1
                # Stamp with the scheduled time (ideal device clock), not the wake-up time,
                # so OS timer jitter (large on macOS CI) does not show up as gaps.
                self._stream.write(self._samples[pos:end], deadline)
            except Exception:
                self.callback_errors += 1
            dt = time.perf_counter_ns() - t_cb
            if dt > self._max_callback_ns:
                self._max_callback_ns = dt
            pos = end
        self.exhausted = pos >= n_total

    def stop(self) -> None:
        """Stop the replay thread. Idempotent."""
        thread = self._thread
        if thread is None:
            return
        self._stop.set()
        thread.join()
        self._thread = None
        _dbg.process_end("FileReplay", callbacks=self.callbacks)

    def close(self) -> None:
        """Same as ``stop()``."""
        self.stop()
