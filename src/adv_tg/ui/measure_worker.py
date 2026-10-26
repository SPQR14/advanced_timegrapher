"""``MeasureStreamWorker``: the real measuring engine of the GUI (layer 5).

It is a ``CaptureStreamWorker`` (same device handling, waveform ~30/s, level ~10/s and
capture health ~2/s, capture in a child process) plus a per-session hook that runs in the
reader thread:

* every ``ANALYSIS_PERIOD_S`` (0.25 s, i.e. 4 updates per second) a ``MeasurementSession``
  (``adv_tg.core.session``) processes the new audio and ``telemetry_ready`` carries the
  figures (``BeatTelemetry``; NaN while a figure is not available) and the last beat for the
  oscilloscope (band-passed for display only, with its real P1/P2/P3 instants);
* every ``DRIFT_PERIOD_S`` (1 s) ``drift_updated`` and ``clock_info`` report the ADC clock
  correction applied to the rate: the stored calibration of the device
  (``adv_tg.utils.adc_calibration``) or the live estimate against the OS clock, whichever
  is more precise;
* the session is recorded to a temporary FLAC (24 bits, tags with the device, the date and
  the calibration) that ``take_recording`` hands to the GUI when the user saves it; it is
  deleted at the next start and at shutdown.

Cost: ~5 ms per update on the real takes (detector + metrics), i.e. ~2 % of one core.

Replay mode (no microphone): ``ADV_TG_REPLAY_FILE=/path/take.flac adv-tg`` makes every
hardware device replay that file in real time (its sample rate must match the one selected
in the GUI). The calibration stored in the file's tags is applied and nothing is recorded.
"""

from __future__ import annotations

import math
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
from PyQt6.QtCore import QObject

from adv_tg.audio.file_replay import FileReplay
from adv_tg.core.adc_clock import PPM_TO_S_PER_DAY, AdcDrift, ClockLevel, drift_from_tags
from adv_tg.core.models import BeatEvent, BeatTelemetry, WatchConfig
from adv_tg.core.sample_stream import SamplesOverwritten, SampleStream
from adv_tg.core.session import MeasurementSession, SessionConfig, SessionSnapshot
from adv_tg.ui.capture_worker import (
    CaptureFactory,
    CaptureStreamWorker,
    StreamFactory,
    _default_capture_factory,
)
from adv_tg.utils.adc_calibration import CalibrationStore, CalibrationStoreError, recording_tags
from adv_tg.utils.audio_files import AudioWriter, read_mono, read_tags
from adv_tg.utils.debugger import Debugger

__all__ = [
    "ANALYSIS_PERIOD_S",
    "DRIFT_PERIOD_S",
    "REPLAY_ENV_VAR",
    "MeasureStreamWorker",
    "snapshot_to_telemetry",
]

_dbg = Debugger.get(__name__)
logger = _dbg.logger

ANALYSIS_PERIOD_S: float = 0.25
DRIFT_PERIOD_S: float = 1.0
REPLAY_ENV_VAR: str = "ADV_TG_REPLAY_FILE"
SPAN_S: float = 30.0


def replay_file() -> Path | None:
    """File to replay instead of the microphone (``ADV_TG_REPLAY_FILE``), or None."""
    value = os.environ.get(REPLAY_ENV_VAR, "").strip()
    return Path(value).expanduser() if value else None


def device_name(device_id: int | None) -> str | None:
    """PortAudio name of an input device (``None`` = default input), or None if unknown."""
    try:
        from adv_tg.audio.capture import load_sounddevice

        info = load_sounddevice().query_devices(device_id, "input")
        return str(info["name"]).strip() or None
    except Exception:
        return None


def snapshot_to_telemetry(snap: SessionSnapshot, fs: float) -> BeatTelemetry:
    """GUI model of a session snapshot (instants in seconds of the ADC clock).

    The figures are the displayed ones (``SessionSnapshot.display``, median of the last
    updates); the uncertainties come from the latest metrics.
    """
    m = snap.metrics
    shown = snap.display
    beats: list[BeatEvent] = []
    b = snap.last_beat
    if b is not None and snap.waveform.size:
        beats.append(
            BeatEvent(
                timestamp_p1=b.p1 / fs,
                timestamp_p2=b.p2 / fs,
                timestamp_p3=b.p3 / fs,
                peak_energy=float(np.max(np.abs(snap.waveform))),
                raw_waveform=snap.waveform,
                waveform_start_s=snap.waveform_start / fs,
            )
        )
    warnings = list(m.warnings) + list(snap.detector_warnings)
    if snap.saturated_beats:
        warnings.append(
            f"{snap.saturated_beats} batimientos saturados: baja la ganancia del micrófono"
        )
    return BeatTelemetry(
        rate_spd=shown.rate_s_per_day,
        beat_error_ms=shown.beat_error_ms,
        amplitude_deg=shown.amplitude_deg,
        beat_times_sec=np.empty(0),
        envelope=np.empty(0),
        confidence=float(shown.confidence),
        snr_db=snap.snr_db,
        beats=beats,
        effective_fs=fs * (1.0 + snap.drift.ppm * 1e-6),
        rate_uncertainty_spd=m.rate_uncertainty_s_per_day,
        beat_error_uncertainty_ms=m.beat_error_uncertainty_ms,
        amplitude_indicative=m.amplitude_indicative,
        detection_ratio=snap.detection_ratio,
        usable_ratio=snap.usable_ratio,
        locked=snap.locked,
        saturated_beats=snap.saturated_beats,
        span_s=m.span_s,
        warnings=warnings,
        timing_confidence=float(shown.timing_confidence),
    )


class _SessionHook:
    """Analysis and recording of one session; every method runs in the reader thread."""

    def __init__(
        self,
        owner: MeasureStreamWorker,
        stream: SampleStream,
        config: SessionConfig,
        record_path: Path | None,
        record_tags: dict[str, str],
    ) -> None:
        self._owner = owner
        self._stream = stream
        self._fs = float(config.sample_rate)
        self._session = MeasurementSession(config)
        self._next_analysis = 0.0
        self._next_drift = 0.0
        self._record_path = record_path
        self._record_tags = record_tags
        self._writer: AudioWriter | None = None
        self._rec_idx = 0
        self._rec_lost = 0
        self._last: SessionSnapshot | None = None
        self.updates = 0
        self.max_update_ms = 0.0

    # ------------------------------------------------------------------ recording
    def on_open(self, capture: Any) -> None:
        if self._record_path is None:
            return
        try:
            self._writer = AudioWriter(
                self._record_path,
                int(round(self._fs)),
                subtype="PCM_24",
                comment="adv_tg GUI",
                tags=self._record_tags,
            ).open()
        except Exception as exc:
            logger.error("No se pudo crear la grabación de la sesión: %s", exc)
            self._writer = None

    def _record(self) -> None:
        w = self._writer
        if w is None:
            return
        total = self._stream.total_samples
        start = max(self._rec_idx, self._stream.oldest_available)
        if start > self._rec_idx:
            self._rec_lost += start - self._rec_idx
        if total <= start:
            return
        try:
            x = self._stream.read(start, total, np.float32)
        except SamplesOverwritten:
            self._rec_lost += total - start
            self._rec_idx = total
            return
        w.write(x)
        self._rec_idx = total

    # ------------------------------------------------------------------ analysis
    def _analyze(self) -> None:
        t0 = time.perf_counter()
        snap = self._session.update(self._stream)
        self._owner.telemetry_ready.emit(snapshot_to_telemetry(snap, self._fs))
        self.updates += 1
        self.max_update_ms = max(self.max_update_ms, (time.perf_counter() - t0) * 1e3)
        self._last = snap

    def _emit_drift(self) -> None:
        snap = self._last
        if snap is None:
            return
        d: AdcDrift = snap.drift
        self._owner.drift_updated.emit(
            self._fs * (1.0 + d.ppm * 1e-6), d.ppm, -PPM_TO_S_PER_DAY * d.ppm
        )
        sigma = d.uncertainty_ppm if d.level != ClockLevel.UNCALIBRATED else math.nan
        self._owner.clock_info.emit(sigma, int(d.level), snap.drift_source)

    def tick(self, now: float) -> None:
        self._record()
        if now >= self._next_analysis:
            self._next_analysis = max(self._next_analysis + ANALYSIS_PERIOD_S, now)
            self._analyze()
        if now >= self._next_drift:
            self._next_drift = max(self._next_drift + DRIFT_PERIOD_S, now)
            self._emit_drift()

    def close(self) -> None:
        try:
            self._record()
            self._analyze()  # the audio that arrived since the last update
            self._emit_drift()
        finally:
            w, self._writer = self._writer, None
            if w is None and self._record_path is not None:  # never opened (device failed)
                self._record_path.unlink(missing_ok=True)
                self._owner._recording_done(self._record_path, 0)
            if w is not None:
                if self._rec_lost:
                    w.tags["ADV_TG_LOST_SAMPLES"] = str(self._rec_lost)
                try:
                    w.close()
                    self._owner._recording_done(self._record_path, w.frames_written)
                except Exception as exc:
                    logger.error("No se pudo cerrar la grabación de la sesión: %s", exc)
            _dbg.process_end(
                "análisis de sesión",
                updates=self.updates,
                max_update_ms=round(self.max_update_ms, 2),
                recorded_lost=self._rec_lost,
            )


class MeasureStreamWorker(CaptureStreamWorker):
    """Hardware engine with measurement (see module docstring).

    Args:
        config: Initial ``WatchConfig``.
        capture_factory: As in ``CaptureStreamWorker`` (default: the device, or the replay
            file when ``ADV_TG_REPLAY_FILE`` is set).
        stream_factory: As in ``CaptureStreamWorker``.
        calibration_store: Where the stored calibrations are read (default: the user's).
        record: Record each session to a temporary FLAC (default True; never in replay).
        parent: Qt parent.
    """

    def __init__(
        self,
        config: WatchConfig | None = None,
        *,
        capture_factory: CaptureFactory | None = None,
        stream_factory: StreamFactory | None = None,
        calibration_store: CalibrationStore | None = None,
        record: bool = True,
        parent: QObject | None = None,
    ) -> None:
        self._replay = replay_file() if capture_factory is None else None
        if capture_factory is None:
            capture_factory = (
                self._replay_factory if self._replay is not None else _default_capture_factory
            )
        super().__init__(
            config,
            capture_factory=capture_factory,
            stream_factory=stream_factory,
            parent=parent,
        )
        self._store = calibration_store
        self._record = record and self._replay is None
        self._rec_lock = threading.Lock()
        self._recording: Path | None = None
        self._recording_frames = 0
        self._pending_recording: Path | None = None

    def update_watch_config(self, config: WatchConfig) -> None:
        """Store the config; BPH / lift angle changes apply to the next session (warned)."""
        old = self._config
        if self._active and (config.bph != old.bph or config.lift_angle != old.lift_angle):
            message = (
                f"El cambio a {config.bph} A/h y {config.lift_angle:g}° se aplicará en la "
                "próxima sesión"
            )
            logger.warning("%s", message)
            self.error_occurred.emit(message)
        super().update_watch_config(config)

    # ------------------------------------------------------------------ replay
    def _replay_factory(self, stream: SampleStream, **_kwargs: Any) -> FileReplay:
        path = self._replay
        assert path is not None
        fs, x = read_mono(path)
        if int(fs) != int(round(stream.sample_rate)):
            raise ValueError(
                f"el archivo '{path.name}' está a {fs} Hz; elige esa frecuencia de muestreo"
            )
        return FileReplay(stream, np.ascontiguousarray(x, dtype=np.float32), 1024)

    @property
    def replay_path(self) -> Path | None:
        """File replayed instead of the microphone (``ADV_TG_REPLAY_FILE``), or None."""
        return self._replay

    # ------------------------------------------------------------------ calibration
    def _calibration(self, fs: float) -> tuple[AdcDrift | None, str, str | None]:
        """``(calibration, source, device name)`` for this session."""
        if self._replay is not None:
            try:
                d = drift_from_tags(read_tags(self._replay))
            except Exception as exc:
                logger.warning("Etiquetas del archivo repetido ilegibles: %s", exc)
                d = None
            if d is None or d.level == ClockLevel.UNCALIBRATED:
                return None, "sin calibrar", f"archivo {self._replay.name}"
            return (
                d,
                f"etiquetas del archivo (nivel {int(d.level)})",
                f"archivo {self._replay.name}",
            )
        name = device_name(self._device_id)
        if name is None:
            return None, "sin calibrar", None
        try:
            cal = (self._store or CalibrationStore()).best(name, int(round(fs)))
        except CalibrationStoreError as exc:
            logger.warning("%s", exc)
            return None, "sin calibrar", name
        if cal is None:
            return None, "sin calibrar", name
        d = cal.effective()
        return d, f"calibración guardada (nivel {int(d.level)}, {cal.measured_at:%Y-%m-%d})", name

    # ------------------------------------------------------------------ hook
    def _make_hook(self, fs: float, stream: SampleStream) -> _SessionHook:
        cal, source, name = self._calibration(fs)
        cfg = self._config
        session_cfg = SessionConfig(
            sample_rate=fs,
            bph=float(cfg.bph),
            lift_angle_deg=float(cfg.lift_angle),
            span_s=SPAN_S,
            calibration=cal,
            calibration_source=source,
        )
        self.discard_recording()
        path: Path | None = None
        tags: dict[str, str] = {}
        if self._record:
            fd, tmp = tempfile.mkstemp(prefix="adv_tg_sesion_", suffix=".flac")
            os.close(fd)
            path = Path(tmp)
            try:
                tags, _ = recording_tags(name, int(round(fs)), self._store)
            except CalibrationStoreError:
                tags, _ = recording_tags(None, int(round(fs)))
            tags.update({"ADV_TG_BPH": str(cfg.bph), "ADV_TG_LIFT_ANGLE": f"{cfg.lift_angle:g}"})
            with self._rec_lock:
                self._pending_recording = path
        _dbg.process_start(
            "análisis de sesión",
            device=name,
            bph=cfg.bph,
            lift=cfg.lift_angle,
            fs=fs,
            calibration=source,
            recording=str(path) if path else None,
        )
        return _SessionHook(self, stream, session_cfg, path, tags)

    # ------------------------------------------------------------------ recording
    def _recording_done(self, path: Path | None, frames: int) -> None:
        """Called from the reader thread when the session file is complete."""
        with self._rec_lock:
            if path is None or path != self._pending_recording:
                return
            self._pending_recording = None
            self._recording = path if frames > 0 else None
            self._recording_frames = frames
        if frames <= 0 and path is not None:
            path.unlink(missing_ok=True)

    @property
    def has_recording(self) -> bool:
        """True when the last finished session left a recording to save."""
        with self._rec_lock:
            return self._recording is not None and self._recording.exists()

    def take_recording(self) -> Path | None:
        """Hand over the last session's FLAC (the caller moves or deletes it)."""
        with self._rec_lock:
            path, self._recording = self._recording, None
        return path if path is not None and path.exists() else None

    def discard_recording(self) -> None:
        """Delete the last session's temporary recording, if any."""
        path = self.take_recording()
        if path is not None:
            path.unlink(missing_ok=True)

    def shutdown(self) -> None:
        super().shutdown()
        self.discard_recording()
        with self._rec_lock:
            pending, self._pending_recording = self._pending_recording, None
        if pending is not None:
            pending.unlink(missing_ok=True)
