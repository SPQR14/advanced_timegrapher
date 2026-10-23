"""Shared utility exports for the ``adv_tg`` package."""

from adv_tg.utils.audio_io import (
    SYNTHETIC_DEVICE_NAME,
    AudioDeviceInfo,
    HardwareAudioCapture,
    is_flac_supported,
    is_vorbis_supported,
    list_input_devices,
    preferred_audio_format,
    save_audio_recording,
)
from adv_tg.utils.logger_setup import LoggerSetup
from adv_tg.utils.watch_db import WATCH_DB_PATH, load_watch_db

__all__ = [
    "AudioDeviceInfo",
    "HardwareAudioCapture",
    "LoggerSetup",
    "SYNTHETIC_DEVICE_NAME",
    "WATCH_DB_PATH",
    "is_flac_supported",
    "is_vorbis_supported",
    "list_input_devices",
    "load_watch_db",
    "preferred_audio_format",
    "save_audio_recording",
]
