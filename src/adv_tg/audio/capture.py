"""``AudioCapture``: float32 ``sounddevice.InputStream`` that feeds a ``SampleStream``.

The device is opened with all its inputs and the loudest one is kept
(``adv_tg.audio.channel_select``). The PortAudio callback only writes that channel into the ``SampleStream`` together with
its clock stamps (``perf_counter_ns``, the driver's capture time of the block end and
``time.time_ns()``, for layer 4) and updates a few integer counters. It never logs, blocks, takes locks or
does I/O, and any exception inside it is swallowed and counted (an exception escaping the
callback would abort the PortAudio stream). Everything else (opening the device, checking the
effective sample rate, tracing) happens outside the callback and uses the ``Debugger``.

``sounddevice`` is imported lazily so that this module can be imported on machines without
PortAudio (tests, CI).

Diagnostics (phase 1c): the callback also estimates how long the audio thread waited before
the Python code could run (mostly waiting for the GIL). The very first thing the callback does
is read the PortAudio stream clock (``stream.time``, i.e. ``Pa_GetStreamTime``); PortAudio
stamps ``time_info.currentTime`` on the same clock right before invoking the C callback, so
``stream.time - time_info.currentTime`` is the delay between the native callback and the
Python code (GIL acquisition plus the small ``sounddevice``/cffi wrapper overhead). Only
counters and a preallocated histogram are updated (no logging, no significant allocation).
"""

from __future__ import annotations

import array
import ctypes
import os
import time
from bisect import bisect_right
from types import ModuleType
from typing import Any, Literal

from adv_tg import native_bridge as _nb
from adv_tg.audio.capture_process import _device_channels, adc_end_ns
from adv_tg.audio.channel_select import ChannelSelector
from adv_tg.audio.gil_stats import GIL_WAIT_EDGES_MS, hist_percentile_ms
from adv_tg.audio.gil_stats import GIL_WAIT_EDGES_US as _GIL_WAIT_EDGES_US
from adv_tg.core.sample_stream import SampleStream
from adv_tg.utils.debugger import Debugger

__all__ = ["GIL_WAIT_EDGES_MS", "AudioCapture", "hist_percentile_ms", "load_sounddevice"]

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
    """Wrapper around a float32 ``sounddevice.InputStream`` (one channel is delivered).

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
        self._native_cap: ctypes.c_void_p | None = None
        self.channels = 1
        self._selector: ChannelSelector | None = None
        self._callbacks = 0
        self._status_flags = 0
        self._callback_errors = 0
        self._max_callback_ns = 0
        # Diagnostics (phase 1c): GIL wait estimate, filled by the callback.
        self._pa_clock: Any = None
        self._gil_n = 0
        self._gil_sum_us = 0.0
        self._gil_max_us = 0.0
        self._gil_hist = array.array("q", [0] * (len(GIL_WAIT_EDGES_MS) + 1))
        self._gil_wait_over_block = 0
        self._gil_wait_invalid = 0

    def __del__(self) -> None:
        h = getattr(self, "_native_cap", None)
        if h is not None:
            self._native_cap = None
            try:
                cdll = _nb.lib()
                if cdll is not None:
                    cdll.advtg_cap_destroy(h)
            except Exception:
                pass

    def _cap_stats(self) -> _nb.NativeCaptureStatsStruct | None:
        if self._native_cap is None:
            return None
        st = _nb.NativeCaptureStatsStruct()
        _nb.lib().advtg_cap_stats(self._native_cap, ctypes.byref(st))
        return st

    # ------------------------------------------------------------------ counters
    @property
    def callbacks(self) -> int:
        st = self._cap_stats()
        return int(st.callbacks) if st is not None else self._callbacks

    @callbacks.setter
    def callbacks(self, val: int) -> None:
        self._callbacks = int(val)

    @property
    def status_flags(self) -> int:
        st = self._cap_stats()
        return int(st.status_flags) if st is not None else self._status_flags

    @status_flags.setter
    def status_flags(self, val: int) -> None:
        self._status_flags = int(val)

    @property
    def callback_errors(self) -> int:
        st = self._cap_stats()
        return int(st.callback_errors) if st is not None else self._callback_errors

    @callback_errors.setter
    def callback_errors(self, val: int) -> None:
        self._callback_errors = int(val)

    @property
    def gil_wait_over_block(self) -> int:
        st = self._cap_stats()
        return int(st.gil_wait_over_block) if st is not None else self._gil_wait_over_block

    @gil_wait_over_block.setter
    def gil_wait_over_block(self, val: int) -> None:
        self._gil_wait_over_block = int(val)

    @property
    def gil_wait_invalid(self) -> int:
        st = self._cap_stats()
        return int(st.gil_wait_invalid) if st is not None else self._gil_wait_invalid

    @gil_wait_invalid.setter
    def gil_wait_invalid(self, val: int) -> None:
        self._gil_wait_invalid = int(val)

    @property
    def max_callback_us(self) -> float:
        """Longest callback duration observed, in microseconds."""
        st = self._cap_stats()
        return float(st.max_callback_us) if st is not None else self._max_callback_ns / 1e3

    @property
    def gil_wait_max_us(self) -> float:
        """Longest estimated wait before the Python callback ran, in microseconds."""
        st = self._cap_stats()
        return float(st.gil_wait_max_us) if st is not None else self._gil_max_us

    @property
    def gil_wait_mean_us(self) -> float:
        """Mean estimated wait before the Python callback ran, in microseconds."""
        st = self._cap_stats()
        if st is not None:
            return float(st.gil_wait_sum_us) / st.gil_n if st.gil_n else 0.0
        return self._gil_sum_us / self._gil_n if self._gil_n else 0.0

    @property
    def gil_wait_hist(self) -> tuple[int, ...]:
        """Counts per bucket of ``GIL_WAIT_EDGES_MS`` (last bucket: ``>= 500 ms``)."""
        st = self._cap_stats()
        return tuple(int(v) for v in st.gil_hist) if st is not None else tuple(self._gil_hist)

    @property
    def gil_wait_p99_ms(self) -> float:
        """99th percentile of the wait (bucket upper edge, capped at the maximum), in ms."""
        st = self._cap_stats()
        if st is not None:
            hist = tuple(int(v) for v in st.gil_hist)
            return min(hist_percentile_ms(hist, 0.99), float(st.gil_wait_max_us) / 1000.0)
        return min(hist_percentile_ms(self._gil_hist, 0.99), self._gil_max_us / 1000.0)

    @property
    def is_active(self) -> bool:
        """True while the PortAudio stream is running."""
        if self._native_cap is not None:
            return bool(_nb.lib().advtg_cap_is_active(self._native_cap))
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
        try:  # diagnostics: read the PortAudio clock first (see module docstring)
            pa_now = self._pa_clock.time
        except Exception:
            pa_now = 0.0
        t0 = time.perf_counter_ns()
        wall = time.time_ns()
        try:
            self.callbacks += 1
            if status:
                self.status_flags += 1
            adc = adc_end_ns(time_info, frames, self.sample_rate)
            sel = self._selector
            ch = 0 if sel is None else sel.pick(indata)
            self._stream.write(indata[:, ch], t0, adc_ns=adc, wall_ns=wall)
        except Exception:
            self.callback_errors += 1
        dt = time.perf_counter_ns() - t0
        if dt > self._max_callback_ns:
            self._max_callback_ns = dt
        try:
            self._record_gil_wait(pa_now, time_info, frames)
        except Exception:
            self.gil_wait_invalid += 1

    def _record_gil_wait(self, pa_now: float, time_info: Any, frames: int) -> None:
        """Update the GIL-wait counters (callback thread; no logging, no allocation)."""
        current = time_info.currentTime
        if not pa_now or not current:
            self.gil_wait_invalid += 1
            return
        wait_us = (pa_now - current) * 1e6
        if wait_us < 0.0:
            wait_us = 0.0
        self._gil_n += 1
        self._gil_sum_us += wait_us
        if wait_us > self._gil_max_us:
            self._gil_max_us = wait_us
        self._gil_hist[bisect_right(_GIL_WAIT_EDGES_US, wait_us)] += 1
        if frames > 0 and wait_us > frames * 1e6 / self.sample_rate:
            self.gil_wait_over_block += 1

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
        if self._native_cap is None and self._sd_stream is None:
            self._open()
        with _dbg.phase("audio_capture_start", device=self.device):
            if self._native_cap is not None:
                rc = int(_nb.lib().advtg_cap_start(self._native_cap))
                if rc < 0:
                    err = _nb.lib().advtg_cap_last_error(self._native_cap)
                    raise RuntimeError(
                        err.decode("utf-8", errors="replace") if err else "PortAudio error"
                    )
            else:
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
            use_native = (
                _nb.have(
                    "advtg_cap_create",
                    "advtg_cap_open",
                    "advtg_cap_start",
                    "advtg_cap_stop",
                    "advtg_cap_close",
                    "advtg_cap_destroy",
                )
                and getattr(self._stream, "_native", None) is not None
                and getattr(getattr(sd, "InputStream", None), "__module__", "").startswith(
                    "sounddevice"
                )
                and os.environ.get("ADV_TG_FORCE_SD_CAPTURE") != "1"
            )
            if use_native:
                pa_hint = getattr(getattr(sd, "_lib", None), "_name", "") or ""
                dev_arg = -1 if self.device is None else int(self.device)
                req_ch = _device_channels(sd, self.device)
                cap_h = ctypes.c_void_p(
                    _nb.lib().advtg_cap_create(
                        self._stream._native,
                        dev_arg,
                        float(self.sample_rate),
                        int(self.blocksize),
                        1 if self.latency == "high" else 0,
                    )
                )
                if cap_h:
                    rc = int(_nb.lib().advtg_cap_open(cap_h, pa_hint.encode("utf-8"), int(req_ch)))
                    if rc == 0:
                        st = _nb.NativeCaptureStatsStruct()
                        _nb.lib().advtg_cap_stats(cap_h, ctypes.byref(st))
                        self.channels = int(st.channels)
                        self.actual_sample_rate = float(st.actual_sample_rate)
                        self._native_cap = cap_h
                        if not self.sample_rate_matches:
                            _dbg.logger.error(
                                "Tasa de muestreo efectiva %.3f Hz distinta de la pedida %d Hz",
                                self.actual_sample_rate,
                                self.sample_rate,
                            )
                        return
                    _dbg.logger.warning(
                        "Captura nativa C++ falló (%d); respaldo a sounddevice.InputStream", rc
                    )
                    _nb.lib().advtg_cap_destroy(cap_h)
            sd_stream = None
            last: Exception | None = None
            for channels in dict.fromkeys((_device_channels(sd, self.device), 1)):
                try:
                    sd.check_input_settings(
                        device=self.device,
                        channels=channels,
                        dtype="float32",
                        samplerate=self.sample_rate,
                    )
                    sd_stream = sd.InputStream(
                        device=self.device,
                        channels=channels,
                        samplerate=self.sample_rate,
                        blocksize=self.blocksize,
                        dtype="float32",
                        latency=self.latency,
                        callback=self._callback,
                    )
                except Exception as exc:
                    last = exc
                    continue
                self.channels = channels
                break
            if sd_stream is None:
                assert last is not None
                raise last
            self._selector = (
                ChannelSelector(self.channels, self.sample_rate) if self.channels > 1 else None
            )
            self.actual_sample_rate = float(sd_stream.samplerate)
            self._pa_clock = sd_stream
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
        if self._native_cap is not None:
            if not self.is_active:
                return
            try:
                _nb.lib().advtg_cap_stop(self._native_cap)
            except Exception as exc:
                _dbg.logger.error("Error al parar el stream de audio nativo: %s", exc)
            st = self._cap_stats()
            if st is not None:
                hist = [int(v) for v in st.gil_hist]
                mean_us = float(st.gil_wait_sum_us) / st.gil_n if st.gil_n else 0.0
                p99 = min(hist_percentile_ms(hist, 0.99), float(st.gil_wait_max_us) / 1000.0)
                _dbg.process_end(
                    "AudioCapture",
                    callbacks=int(st.callbacks),
                    status_flags=int(st.status_flags),
                    callback_errors=int(st.callback_errors),
                    max_callback_us=round(float(st.max_callback_us), 1),
                    gil_wait_mean_us=round(mean_us, 1),
                    gil_wait_p99_ms=p99,
                    gil_wait_max_us=round(float(st.gil_wait_max_us), 1),
                    gil_wait_over_block=int(st.gil_wait_over_block),
                    gil_wait_invalid=int(st.gil_wait_invalid),
                    gil_wait_hist=hist,
                )
            return
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
            gil_wait_mean_us=round(self.gil_wait_mean_us, 1),
            gil_wait_p99_ms=self.gil_wait_p99_ms,
            gil_wait_max_us=round(self.gil_wait_max_us, 1),
            gil_wait_over_block=self.gil_wait_over_block,
            gil_wait_invalid=self.gil_wait_invalid,
            gil_wait_hist=list(self._gil_hist),
        )

    def close(self) -> None:
        """Stop and close the stream. Idempotent."""
        self.stop()
        if self._native_cap is not None:
            cap_h = self._native_cap
            self._native_cap = None
            try:
                _nb.lib().advtg_cap_close(cap_h)
            except Exception as exc:
                _dbg.logger.error("Error al cerrar el stream de audio nativo: %s", exc)
            finally:
                _nb.lib().advtg_cap_destroy(cap_h)
            _dbg.state_change("AudioCapture", "ABIERTO", "CERRADO", device=self.device)
            return
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
