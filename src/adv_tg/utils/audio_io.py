"""Audio encoding, hardware device enumeration, and real-time PortAudio capture utilities."""

from __future__ import annotations

import ctypes
import ctypes.util
import shutil
import subprocess
import tempfile
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Self

import numpy as np
from numpy.typing import NDArray

from wea.utils.logger_setup import LoggerSetup

logger = LoggerSetup.setup_logger(__name__)

SYNTHETIC_DEVICE_NAME: str = "Synthetic Ground-Truth Generator"


@dataclass(slots=True)
class AudioDeviceInfo:
    """Metadata descriptor for an audio input device (synthetic or physical PortAudio hardware).

    Attributes:
        device_id: Numeric PortAudio device index, or ``None`` for the synthetic generator.
        name: Human-readable device name.
        max_input_channels: Maximum number of input channels supported.
        default_samplerate: Default sampling rate in Hz reported by PortAudio.
        is_synthetic: True if this entry represents the internal synthetic generator.
    """

    device_id: int | None
    name: str
    max_input_channels: int
    default_samplerate: float
    is_synthetic: bool = False


def list_input_devices() -> list[AudioDeviceInfo]:
    """Enumerate available audio input devices, always starting with the synthetic generator.

    Queries ``sounddevice.query_devices()`` and filters for physical/input-capable devices
    with ``max_input_channels >= 1``, deduplicating repeated host-API entries by name.
    Gracefully catches ``OSError`` / ``PortAudioError`` on headless CI runners.

    Returns:
        List of ``AudioDeviceInfo`` instances with index 0 guaranteed to be the synthetic source.
    """
    devices: list[AudioDeviceInfo] = [
        AudioDeviceInfo(
            device_id=None,
            name=SYNTHETIC_DEVICE_NAME,
            max_input_channels=1,
            default_samplerate=48000.0,
            is_synthetic=True,
        )
    ]

    try:
        import sounddevice as sd

        raw_devices = sd.query_devices()
        seen_names: set[str] = {SYNTHETIC_DEVICE_NAME.lower()}

        for idx, dev in enumerate(raw_devices):
            if not isinstance(dev, dict):
                continue
            max_in = int(dev.get("max_input_channels", 0) or 0)
            if max_in < 1:
                continue

            raw_name = str(dev.get("name", f"Input Device {idx}")).strip()
            if not raw_name:
                raw_name = f"Input Device {idx}"

            norm_name = raw_name.lower()
            # Filter out obvious output-only monitor/sink loopback virtual nodes if labeled as such
            if "output" in norm_name and "input" not in norm_name and "mic" not in norm_name:
                continue
            if norm_name in seen_names:
                continue
            seen_names.add(norm_name)

            dev_id = int(dev.get("index", idx))
            default_fs = float(dev.get("default_samplerate", 48000.0) or 48000.0)
            devices.append(
                AudioDeviceInfo(
                    device_id=dev_id,
                    name=raw_name,
                    max_input_channels=max_in,
                    default_samplerate=default_fs,
                    is_synthetic=False,
                )
            )

        logger.info(
            "Enumerated %d audio input source(s) (1 synthetic + %d hardware)",
            len(devices),
            len(devices) - 1,
        )
    except Exception as exc:
        logger.warning(
            "PortAudio device enumeration unavailable (%s); falling back to synthetic source only",
            exc,
        )

    return devices


class HardwareAudioCapture:
    """Real-time non-blocking mono PortAudio input stream wrapper using ``sounddevice``.

    Captures low-latency ``float32`` mono frames from a physical microphone, converts the
    incoming column ``indata[:, 0]`` into a contiguous 1D ``np.float64`` array, tracks
    ``status.input_overflow`` events, and forwards ``(audio_chunk, timestamp_ns)`` to
    the registered consumer callback.
    """

    def __init__(
        self,
        device_id: int | None,
        sample_rate: float = 48000.0,
        blocksize: int = 4096,
        callback: Callable[[NDArray[np.float64], int], None] | None = None,
        *,
        fs: float | None = None,
    ) -> None:
        """Initialize the hardware audio capture stream configuration.

        Args:
            device_id: PortAudio input device index (or ``None`` for system default input).
            sample_rate: Target sampling rate in Hz (default 48000.0).
            blocksize: Number of frames per PortAudio callback block (default 4096).
            callback: Consumer function invoked as ``callback(chunk_f64, timestamp_ns)``.
            fs: Optional alias for ``sample_rate``.
        """
        effective_fs = float(fs) if fs is not None else float(sample_rate)
        if effective_fs <= 0.0:
            raise ValueError(f"sample_rate must be positive, got {effective_fs}")
        if blocksize <= 0:
            raise ValueError(f"blocksize must be positive, got {blocksize}")

        self.device_id: int | None = int(device_id) if device_id is not None else None
        self.sample_rate: float = effective_fs
        self.blocksize: int = int(blocksize)
        self._callback: Callable[[NDArray[np.float64], int], None] | None = callback
        self._stream: object | None = None
        self._active: bool = False
        self.overflow_count: int = 0

    @property
    def is_active(self) -> bool:
        """Return True if the underlying PortAudio InputStream is currently active."""
        if self._stream is not None and hasattr(self._stream, "active"):
            try:
                return bool(self._stream.active) and self._active
            except Exception:
                return self._active
        return self._active

    def _portaudio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: object,
    ) -> None:
        """Non-blocking PortAudio C-callback executed on the real-time audio thread.

        Must stay allocation-light and never log or block: overflows are only counted here and
        reported later by the consumer (``overflow_count``).
        """
        timestamp_ns = time.perf_counter_ns()
        if status:
            if hasattr(status, "input_overflow"):
                is_overflow = bool(status.input_overflow)
            else:
                is_overflow = "overflow" in str(status).lower()
            if is_overflow:
                self.overflow_count += 1

        if self._callback is None or indata is None or frames <= 0:
            return

        mono_slice = indata[:, 0] if indata.ndim >= 2 else indata.ravel()
        chunk_f64: NDArray[np.float64] = np.ascontiguousarray(mono_slice, dtype=np.float64)
        self._callback(chunk_f64, timestamp_ns)

    def start(self) -> None:
        """Open and start the non-blocking ``sounddevice.InputStream``."""
        if self._active:
            return

        import sounddevice as sd

        logger.info(
            "Opening HardwareAudioCapture stream (device=%s, fs=%.1f Hz, blocksize=%d)",
            self.device_id,
            self.sample_rate,
            self.blocksize,
        )
        stream = sd.InputStream(
            device=self.device_id,
            channels=1,
            samplerate=self.sample_rate,
            blocksize=self.blocksize,
            dtype="float32",
            latency="high",
            callback=self._portaudio_callback,
        )
        stream.start()
        self._stream = stream
        self._active = True
        logger.info("HardwareAudioCapture stream started on device %s", self.device_id)

    def stop(self) -> None:
        """Stop and close the active PortAudio input stream cleanly."""
        stream = self._stream
        self._stream = None
        self._active = False
        if stream is not None:
            try:
                if hasattr(stream, "stop"):
                    stream.stop()
            except Exception as exc:
                logger.warning("Error stopping PortAudio stream: %s", exc)
            try:
                if hasattr(stream, "close"):
                    stream.close()
            except Exception as exc:
                logger.warning("Error closing PortAudio stream: %s", exc)
            logger.info("HardwareAudioCapture stream stopped on device %s", self.device_id)

    def __enter__(self) -> Self:
        """Start capture on context entry."""
        self.start()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """Stop capture on context exit."""
        self.stop()


# libsndfile format constants (from sndfile.h)
SF_FORMAT_FLAC: int = 0x170000
SF_FORMAT_OGG: int = 0x200000
SF_FORMAT_PCM_16: int = 0x0002
SF_FORMAT_VORBIS: int = 0x0060
SFM_WRITE: int = 0x20


class _SF_INFO(ctypes.Structure):  # noqa: N801
    """ctypes mapping for libsndfile ``SF_INFO`` struct."""

    _fields_ = [
        ("frames", ctypes.c_int64),
        ("samplerate", ctypes.c_int),
        ("channels", ctypes.c_int),
        ("format", ctypes.c_int),
        ("sections", ctypes.c_int),
        ("seekable", ctypes.c_int),
    ]


def _load_libsndfile() -> ctypes.CDLL | None:
    """Locate and load ``libsndfile`` shared library if available on the host system."""
    candidates: list[str] = []
    found = ctypes.util.find_library("sndfile")
    if found:
        candidates.append(found)
    candidates.extend(
        [
            "/opt/homebrew/lib/libsndfile.dylib",
            "/usr/local/lib/libsndfile.dylib",
            "libsndfile.dylib",
            "libsndfile.so.1",
            "libsndfile.so",
            "sndfile.dll",
        ]
    )
    for path in candidates:
        try:
            lib = ctypes.CDLL(path)
            lib.sf_format_check.argtypes = [ctypes.POINTER(_SF_INFO)]
            lib.sf_format_check.restype = ctypes.c_int
            lib.sf_open.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(_SF_INFO)]
            lib.sf_open.restype = ctypes.c_void_p
            lib.sf_writef_double.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_int64,
            ]
            lib.sf_writef_double.restype = ctypes.c_int64
            lib.sf_close.argtypes = [ctypes.c_void_p]
            lib.sf_close.restype = ctypes.c_int
            lib.sf_strerror.argtypes = [ctypes.c_void_p]
            lib.sf_strerror.restype = ctypes.c_char_p
            return lib
        except OSError:
            continue
    return None


_SNDFILE_LIB: ctypes.CDLL | None = _load_libsndfile()


def is_flac_supported() -> bool:
    """Return True if FLAC audio encoding is supported on the current system."""
    try:
        import soundfile as sf  # type: ignore[import-untyped]

        if "FLAC" in sf.available_formats():
            return True
    except Exception:
        pass

    if _SNDFILE_LIB is not None:
        info = _SF_INFO(
            frames=0,
            samplerate=48000,
            channels=1,
            format=SF_FORMAT_FLAC | SF_FORMAT_PCM_16,
            sections=0,
            seekable=0,
        )
        if _SNDFILE_LIB.sf_format_check(ctypes.byref(info)) != 0:
            return True

    if shutil.which("flac") or shutil.which("ffmpeg"):
        return True

    return False


def is_vorbis_supported() -> bool:
    """Return True if Ogg Vorbis audio encoding is supported on the current system."""
    try:
        import soundfile as sf  # type: ignore[import-untyped]

        if "OGG" in sf.available_formats() and "VORBIS" in sf.available_subtypes("OGG"):
            return True
    except Exception:
        pass

    if _SNDFILE_LIB is not None:
        info = _SF_INFO(
            frames=0,
            samplerate=48000,
            channels=1,
            format=SF_FORMAT_OGG | SF_FORMAT_VORBIS,
            sections=0,
            seekable=0,
        )
        if _SNDFILE_LIB.sf_format_check(ctypes.byref(info)) != 0:
            return True

    if shutil.which("ffmpeg") or shutil.which("oggenc"):
        return True

    return False


def preferred_audio_format() -> str:
    """Return ``'flac'`` if FLAC encoding is available, otherwise ``'ogg'`` (Vorbis)."""
    if is_flac_supported():
        return "flac"
    return "ogg"


def _normalize_samples(samples: np.ndarray) -> np.ndarray:
    """Ensure 1D contiguous float64 array scaled safely within [-1.0, 1.0]."""
    arr = np.asarray(samples, dtype=np.float64).ravel()
    if len(arr) == 0:
        return np.zeros(0, dtype=np.float64)
    peak = float(np.max(np.abs(arr)))
    if peak > 1.0:
        arr = arr / peak
    return np.ascontiguousarray(np.clip(arr, -1.0, 1.0), dtype=np.float64)


def _write_via_libsndfile(
    out_path: Path, samples: np.ndarray, sample_rate: int, fmt_flag: int
) -> bool:
    """Write audio samples using ``libsndfile`` via ctypes."""
    if _SNDFILE_LIB is None:
        return False
    info = _SF_INFO(
        frames=0,
        samplerate=int(sample_rate),
        channels=1,
        format=fmt_flag,
        sections=0,
        seekable=0,
    )
    if _SNDFILE_LIB.sf_format_check(ctypes.byref(info)) == 0:
        return False

    snd = _SNDFILE_LIB.sf_open(str(out_path).encode("utf-8"), SFM_WRITE, ctypes.byref(info))
    if not snd:
        err_msg = _SNDFILE_LIB.sf_strerror(None)
        logger.error(
            "libsndfile failed to open '%s': %s",
            out_path,
            err_msg.decode("utf-8", errors="replace") if err_msg else "unknown error",
        )
        return False

    try:
        ptr = samples.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
        written = _SNDFILE_LIB.sf_writef_double(snd, ptr, len(samples))
        if written != len(samples):
            logger.warning(
                "libsndfile partial write to '%s': %d / %d frames",
                out_path,
                written,
                len(samples),
            )
            return False
        return True
    finally:
        _SNDFILE_LIB.sf_close(snd)


def _write_via_cli(out_path: Path, samples: np.ndarray, sample_rate: int, target_fmt: str) -> bool:
    """Fallback encoder using ``flac``, ``oggenc``, or ``ffmpeg`` CLI tools."""
    pcm16 = np.clip(samples * 32767.0, -32768.0, 32767.0).astype("<i2")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_wav = Path(tmp.name)

    try:
        with wave.open(str(tmp_wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(int(sample_rate))
            wf.writeframes(pcm16.tobytes())

        if target_fmt == "flac" and shutil.which("flac"):
            cmd = ["flac", "-f", "-s", str(tmp_wav), "-o", str(out_path)]
            res = subprocess.run(cmd, capture_output=True, check=False)
            if res.returncode == 0 and out_path.exists():
                return True

        if target_fmt == "ogg" and shutil.which("oggenc"):
            cmd = ["oggenc", "-Q", str(tmp_wav), "-o", str(out_path)]
            res = subprocess.run(cmd, capture_output=True, check=False)
            if res.returncode == 0 and out_path.exists():
                return True

        if shutil.which("ffmpeg"):
            codec = "flac" if target_fmt == "flac" else "libvorbis"
            cmd = [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(tmp_wav),
                "-c:a",
                codec,
                str(out_path),
            ]
            res = subprocess.run(cmd, capture_output=True, check=False)
            if res.returncode == 0 and out_path.exists():
                return True
    finally:
        tmp_wav.unlink(missing_ok=True)

    return False


def save_audio_recording(
    file_path: Path | str,
    samples: np.ndarray,
    sample_rate: int,
    prefer_flac: bool = True,
) -> Path:
    """Encode and save a 1D audio buffer in FLAC (default) or Ogg Vorbis (fallback).

    Args:
        file_path: Destination file path.
        samples: 1D NumPy array of audio samples.
        sample_rate: Sampling frequency in Hz.
        prefer_flac: If True, defaults to FLAC when supported, falling back to Vorbis.

    Returns:
        Resolved ``Path`` of the saved audio file.
    """
    out_path = Path(file_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    norm = _normalize_samples(samples)

    ext = out_path.suffix.lower().lstrip(".")
    if ext not in ("flac", "ogg"):
        ext = "flac" if (prefer_flac and is_flac_supported()) else "ogg"
        out_path = out_path.with_suffix(f".{ext}")
    elif ext == "flac" and not is_flac_supported():
        logger.warning("FLAC encoding unavailable; falling back to Ogg Vorbis for '%s'", out_path)
        ext = "ogg"
        out_path = out_path.with_suffix(".ogg")

    logger.info(
        "Saving audio recording (%d samples @ %d Hz, %.2f s) to '%s' [format=%s]",
        len(norm),
        sample_rate,
        len(norm) / float(sample_rate) if sample_rate > 0 else 0.0,
        out_path,
        ext.upper(),
    )

    # 1. Try soundfile package if installed
    try:
        import soundfile as sf  # type: ignore[import-untyped]

        subtype = "PCM_16" if ext == "flac" else "VORBIS"
        sf.write(str(out_path), norm, int(sample_rate), format=ext.upper(), subtype=subtype)
        logger.info("Audio recording saved via soundfile to '%s'", out_path)
        return out_path
    except Exception:
        pass

    # 2. Try libsndfile via ctypes
    if ext == "flac":
        if _write_via_libsndfile(out_path, norm, sample_rate, SF_FORMAT_FLAC | SF_FORMAT_PCM_16):
            logger.info("Audio recording saved in FLAC via libsndfile to '%s'", out_path)
            return out_path
        if _write_via_cli(out_path, norm, sample_rate, "flac"):
            logger.info("Audio recording saved in FLAC via CLI encoder to '%s'", out_path)
            return out_path
        logger.warning("FLAC write failed; falling back to Ogg Vorbis")
        ext = "ogg"
        out_path = out_path.with_suffix(".ogg")

    if ext == "ogg":
        if _write_via_libsndfile(out_path, norm, sample_rate, SF_FORMAT_OGG | SF_FORMAT_VORBIS):
            logger.info("Audio recording saved in Ogg Vorbis via libsndfile to '%s'", out_path)
            return out_path
        if _write_via_cli(out_path, norm, sample_rate, "ogg"):
            logger.info("Audio recording saved in Ogg Vorbis via CLI encoder to '%s'", out_path)
            return out_path

    msg = f"Could not encode audio to FLAC or Ogg Vorbis at '{out_path}'"
    logger.error(msg)
    raise RuntimeError(msg)
