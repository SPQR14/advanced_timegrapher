"""Audio input device enumeration for the GUI (the synthetic MOCK source plus PortAudio inputs).

Audio files are read and written by ``adv_tg.utils.audio_files``; capture lives in
``adv_tg.audio``.
"""

from __future__ import annotations

from dataclasses import dataclass

from adv_tg.utils.debugger import Debugger
from adv_tg.utils.logger_setup import LoggerSetup

logger = LoggerSetup.setup_logger(__name__)
_dbg = Debugger.get(__name__)

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

    Traced wrapper around ``_enumerate_input_devices`` (see its docstring).
    """
    with _dbg.phase("enumerar_dispositivos_audio"):
        devices = _enumerate_input_devices()
        n_hw = len(devices) - 1
        logger.info(
            "Audio devices: %d total (%d hardware)%s",
            len(devices),
            n_hw,
            "; fallback: synthetic source only" if n_hw == 0 else "",
        )
    return devices


def _enumerate_input_devices() -> list[AudioDeviceInfo]:
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
