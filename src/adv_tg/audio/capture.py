"""``AudioCapture``: mono float32 ``sounddevice.InputStream`` that feeds a ``SampleStream``.

The PortAudio callback only writes ``indata[:, 0]`` into the ``SampleStream`` together with
a host timestamp and updates a few integer counters. It never logs, blocks, takes locks or
does I/O, and any exception inside it is swallowed and counted (an exception escaping the
callback would abort the PortAudio stream). Everything else (opening the device, checking the
effective sample rate, tracing) happens outside the callback and uses the ``Debugger``.

``sounddevice`` is imported lazily so that this module can be imported on machines without
PortAudio (tests, CI).
"""

from __future__ import annotations

import time
from types import ModuleType
from typing import Any, Literal

from adv_tg.core.sample_stream import SampleStream
from adv_tg.utils.debugger import Debugger

__all__ = ["AudioCapture", "load_sounddevice"]

_dbg = Debugger.get(__name__)

#: Relative tolerance when comparing the effective and the requested sample rate.
SAMPLE_RATE_TOLERANCE = 1e-6


def load_sounddevice() -> ModuleType:
    """Import ``sounddevice`` lazily.

    Returns:
        The ``sounddevice`` module.

    Raises:
        ImportError: If the package is missing.
        OSError: If the PortAudio library cannot be loaded.
    """
    import sounddevice

    return sounddevice


class AudioCapture:
    """Wrapper around a 1-channel float32 ``sounddevice.InputStream``.

    Args:
        stream: Destination ``SampleStream`` (this object is its only producer).
        device: PortAudio input device index, or ``None`` for the default input.
        sample_rate: Requested sample rate in Hz; must match ``stream.sample_rate``.
        blocksize: Frames per callback (0 lets PortAudio choose a variable size).
        latency: PortAudio latency hint, ``"high"`` (safer) or ``"low"``.

    Attributes:
        callbacks: Number of callbacks received.
        status_flags: Number of callbacks whose ``status`` was not empty (e.g. input overflow).
        callback_errors: Number of exceptions swallowed inside the callback.
        actual_sample_rate: Effective sample rate reported by PortAudio after ``start()``.

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
    ) -> None:
        if not sample_rate > 0:
            raise ValueError(f"sample_rate must be positive, got {sample_rate}")
        if blocksize < 0:
            raise ValueError(f"blocksize must be >= 0, got {blocksize}")
        if latency not in ("high", "low"):
            raise ValueError(f"latency must be 'high' or 'low', got {latency!r}")
        if abs(stream.sample_rate - sample_rate) > SAMPLE_RATE_TOLERANCE * sample_rate:
            raise ValueError(
                f"sample_rate {sample_rate} does not match SampleStream rate {stream.sample_rate}"
            )
        self._stream = stream
        self.device = device
        self.sample_rate = int(sample_rate)
        self.blocksize = int(blocksize)
        self.latency = latency
        self.actual_sample_rate: float | None = None
        self._sd_stream: Any = None
        self.callbacks = 0
        self.status_flags = 0
        self.callback_errors = 0
        self._max_callback_ns = 0

    # ------------------------------------------------------------------ counters
    @property
    def max_callback_us(self) -> float:
        """Longest callback duration observed, in microseconds."""
        return self._max_callback_ns / 1e3

    @property
    def is_active(self) -> bool:
        """True while the PortAudio stream is running."""
        sd_stream = self._sd_stream
        if sd_stream is None:
            return False
        try:
            return bool(sd_stream.active)
        except Exception:
            return False

    # ------------------------------------------------------------------ callback
    def _callback(self, indata: Any, frames: int, time_info: Any, status: Any) -> None:
        """PortAudio callback (real-time thread): write, count, nothing else."""
        t0 = time.perf_counter_ns()
        try:
            self.callbacks += 1
            if status:
                self.status_flags += 1
            self._stream.write(indata[:, 0], t0)
        except Exception:
            self.callback_errors += 1
        dt = time.perf_counter_ns() - t0
        if dt > self._max_callback_ns:
            self._max_callback_ns = dt

    # ------------------------------------------------------------------ control
    def start(self) -> None:
        """Open (if needed) and start the input stream. Idempotent.

        Raises:
            ImportError, OSError: If ``sounddevice``/PortAudio is unavailable.
            Exception: Any ``sounddevice.PortAudioError`` / ``ValueError`` raised while
                opening the device (traced with the Debugger and re-raised).
        """
        if self.is_active:
            return
        if self._sd_stream is None:
            self._open()
        with _dbg.phase("audio_capture_start", device=self.device):
            self._sd_stream.start()
        _dbg.process_start(
            "AudioCapture",
            device=self.device,
            fs=self.actual_sample_rate,
            blocksize=self.blocksize,
            latency=self.latency,
        )

    def _open(self) -> None:
        sd = load_sounddevice()
        with _dbg.phase(
            "audio_capture_open",
            device=self.device,
            fs=self.sample_rate,
            blocksize=self.blocksize,
            latency=self.latency,
        ):
            try:
                info = sd.query_devices(self.device, "input")
                _dbg.logger.info(
                    "Dispositivo de entrada: %s (default_samplerate=%s)",
                    info.get("name") if isinstance(info, dict) else info,
                    info.get("default_samplerate") if isinstance(info, dict) else "?",
                )
            except Exception as exc:
                _dbg.logger.warning("query_devices(%s) falló: %s", self.device, exc)
            sd.check_input_settings(
                device=self.device, channels=1, dtype="float32", samplerate=self.sample_rate
            )
            sd_stream = sd.InputStream(
                device=self.device,
                channels=1,
                samplerate=self.sample_rate,
                blocksize=self.blocksize,
                dtype="float32",
                latency=self.latency,
                callback=self._callback,
            )
            self.actual_sample_rate = float(sd_stream.samplerate)
            self._sd_stream = sd_stream
            if not self.sample_rate_matches:
                _dbg.logger.error(
                    "Tasa de muestreo efectiva %.3f Hz distinta de la pedida %d Hz",
                    self.actual_sample_rate,
                    self.sample_rate,
                )

    @property
    def sample_rate_matches(self) -> bool:
        """True when the effective sample rate equals the requested one (or is unknown)."""
        if self.actual_sample_rate is None:
            return True
        return (
            abs(self.actual_sample_rate - self.sample_rate)
            <= SAMPLE_RATE_TOLERANCE * self.sample_rate
        )

    def stop(self) -> None:
        """Stop the stream (keeps it open so it can be restarted). Idempotent."""
        sd_stream = self._sd_stream
        if sd_stream is None or not self.is_active:
            return
        try:
            sd_stream.stop()
        except Exception as exc:
            _dbg.logger.error("Error al parar el stream de audio: %s", exc)
        _dbg.process_end(
            "AudioCapture",
            callbacks=self.callbacks,
            status_flags=self.status_flags,
            callback_errors=self.callback_errors,
            max_callback_us=round(self.max_callback_us, 1),
        )

    def close(self) -> None:
        """Stop and close the stream. Idempotent."""
        self.stop()
        sd_stream = self._sd_stream
        self._sd_stream = None
        if sd_stream is None:
            return
        try:
            sd_stream.close()
        except Exception as exc:
            _dbg.logger.error("Error al cerrar el stream de audio: %s", exc)
        _dbg.state_change("AudioCapture", "ABIERTO", "CERRADO", device=self.device)

    def __enter__(self) -> AudioCapture:
        """Start capturing on context entry."""
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the stream on context exit."""
        self.close()
