"""Single contract between the GUI (``MainWin``) and any measurement engine.

``BaseStreamWorker`` declares exactly the members ``MainWin`` uses on its engine
(``dsp_worker``). Today these implementations exist:

* ``adv_tg.ui.capture_worker.CaptureStreamWorker``: hardware devices. Real audio (raw
  waveform, level, capture health) but NO measurement yet: it never emits
  ``telemetry_ready`` nor ``drift_updated``.
* ``adv_tg.ui.null_engine.NullStreamWorker``: fallback only, used if the capture worker
  cannot be created. It does not process audio.
* ``adv_tg.mock.worker.MockStreamWorker``: simulated data so the whole GUI can be tested.
  It has a few MOCK-only extras (test timing, drift sigma/confidence) that are NOT part
  of this contract; ``MainWin`` only uses them when the engine is a ``MockStreamWorker``.

The measuring worker (layer 5) must implement this contract too; ``create_worker`` in
``adv_tg.ui.worker_factory`` picks the engine for each device.

Signals (may be emitted from any thread; Qt queues them to the GUI thread):

* ``telemetry_ready(BeatTelemetry)``: at least 3 updates per second while running.
  ``beats[-1].raw_waveform`` feeds the oscilloscope (P3 = largest peak).
* ``waveform_ready(time_ms, waveform)``: two 1-D ``np.ndarray`` for the live signal plot
  (~30 FPS), normalized to roughly [-1, 1]. Arrays must not be mutated after emission.
* ``drift_updated(effective_fs_hz, drift_ppm, rate_bias_spd)``: ADC clock drift estimate.
* ``capture_health(ingest_pct, overflows)``: share of audio that reached the analysis.
* ``level_updated(peak_dbfs, rms_dbfs, saturated)``: input level (~10/s) over the samples
  received since the previous update; ``-inf`` for silence; ``saturated`` when the peak
  reached -1 dBFS. The GUI uses it for the signal-quality bar while no telemetry arrives.
* ``error_occurred(message)``: human-readable error.
* ``session_finished()``: emitted once when a stream ends (stopped or finished by itself).

Methods (called from the GUI thread; they must never block for long):

* ``set_input_device(device_id)``: ``None`` means the synthetic source.
* ``update_watch_config(config)``: live BPH / lift angle / sample rate changes.
* ``start_stream()`` / ``stop_stream()`` / ``isRunning()`` / ``shutdown()``.

Attributes read or written by the GUI:

* ``synthetic_mode``: True only for simulated engines (the GUI adds display jitter).
* ``synthetic_rate_spd``, ``synthetic_beat_error_ms``, ``synthetic_amplitude_deg``:
  per-position base values (``POSITION_VARIATION``) used by simulated engines.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from adv_tg.core.models import WatchConfig


class BaseStreamWorker(QObject):
    """Abstract measurement engine as seen by ``MainWin`` (see module docstring)."""

    telemetry_ready = pyqtSignal(object)
    waveform_ready = pyqtSignal(object, object)
    drift_updated = pyqtSignal(float, float, float)
    capture_health = pyqtSignal(float, int)
    level_updated = pyqtSignal(float, float, bool)
    error_occurred = pyqtSignal(str)
    session_finished = pyqtSignal()

    def __init__(self, config: WatchConfig | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # Values stored by the setters (implementation detail, not part of the contract)
        self._config: WatchConfig = config if config is not None else WatchConfig()
        self._device_id: int | None = None
        self.synthetic_mode: bool = False
        self.synthetic_rate_spd: float = 0.0
        self.synthetic_beat_error_ms: float = 0.0
        self.synthetic_amplitude_deg: float = 280.0

    def set_input_device(self, device_id: int | None) -> None:
        """Select the input device (``None`` = synthetic source)."""
        self._device_id = None if device_id is None else int(device_id)

    def update_watch_config(self, config: WatchConfig) -> None:
        """Apply a new ``WatchConfig`` (may happen while running)."""
        self._config = config

    def start_stream(self) -> None:
        """Start a measurement session (non-blocking)."""
        raise NotImplementedError

    def stop_stream(self) -> None:
        """Stop the running session; ``session_finished`` follows."""
        raise NotImplementedError

    def isRunning(self) -> bool:  # noqa: N802 - Qt-style name kept for compatibility
        """Return True while a session is active."""
        raise NotImplementedError

    def shutdown(self) -> None:
        """Release every resource before the application exits."""
        self.stop_stream()
