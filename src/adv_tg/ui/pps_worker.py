"""``PpsCalibrationWorker``: live ADC clock calibration with a GPS PPS.

A ``CaptureStreamWorker`` (same device opening, capture process, buffer and level/waveform
signals as a measurement session) whose reader hook runs ``adv_tg.core.pps`` on the new
samples at every tick and emits ``pps_updated(PpsEstimate)`` every ``EMIT_PERIOD_S``.

Samples overwritten before the hook read them (a stalled reader) restart the detector at the
oldest sample still available; the calibrator sees it as a phase jump only if the stream
indices skipped, which they do not (the ring buffer keeps absolute indices), so a stall just
loses a few pulses.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from adv_tg.core.models import WatchConfig
from adv_tg.core.pps import PpsCalibrator, PpsDetector, PpsEstimate
from adv_tg.core.sample_stream import SamplesOverwritten, SampleStream
from adv_tg.ui.capture_worker import CaptureFactory, CaptureStreamWorker, StreamFactory
from adv_tg.utils.debugger import Debugger

__all__ = ["EMIT_PERIOD_S", "PpsCalibrationWorker"]

_dbg = Debugger.get(__name__)
logger = _dbg.logger

#: How often the estimate is sent to the GUI (s).
EMIT_PERIOD_S = 0.5


class _PpsHook:
    """Detector + calibrator of one session; every method runs in the reader thread."""

    def __init__(self, owner: PpsCalibrationWorker, fs: float, stream: SampleStream) -> None:
        self._owner = owner
        self._stream = stream
        self._det = PpsDetector(fs)
        self._cal = PpsCalibrator(fs)
        self._idx = 0
        self._next_emit = 0.0
        self.lost_samples = 0

    def on_open(self, capture: Any) -> None:
        self._idx = self._stream.total_samples

    def _feed(self) -> None:
        stream = self._stream
        total = stream.total_samples
        start = max(self._idx, stream.oldest_available)
        if start > self._idx:
            self.lost_samples += start - self._idx
        if total <= start:
            return
        try:
            x = stream.read(start, total, np.float64)
        except SamplesOverwritten:
            self.lost_samples += total - start
            self._idx = total
            return
        self._cal.add([p.index for p in self._det.process(x, start)])
        self._idx = total

    def _emit(self) -> None:
        self._owner.pps_updated.emit(self._cal.estimate())

    def tick(self, now: float) -> None:
        self._feed()
        if now >= self._next_emit:
            self._next_emit = max(self._next_emit + EMIT_PERIOD_S, now)
            self._emit()

    def close(self) -> None:
        self._feed()
        self._emit()
        est = self._cal.estimate()
        d = est.drift
        logger.info(
            "Calibración PPS terminada: %+.3f ± %.3f ppm, %d pulsos (%d usados, %d saltos de "
            "fase, %d descartados), %.0f s, jitter %.1f µs, muestras perdidas %d",
            d.ppm, d.uncertainty_ppm, est.pulses, est.used, est.jumps, est.rejected,
            est.span_s, est.jitter_us, self.lost_samples,
        )  # fmt: skip


class PpsCalibrationWorker(CaptureStreamWorker):
    """Live GPS PPS calibration of one input device (see module docstring).

    Signals:
        pps_updated(PpsEstimate): current estimate, every ``EMIT_PERIOD_S`` and at the end.

    Args:
        device_id: PortAudio input device index.
        sample_rate: Sample rate in Hz (the one the calibration is for).
        capture_factory / stream_factory: Injectable for tests (see ``CaptureStreamWorker``).
        parent: Qt parent.
    """

    pps_updated = pyqtSignal(object)

    def __init__(
        self,
        device_id: int | None,
        sample_rate: int,
        *,
        capture_factory: CaptureFactory | None = None,
        stream_factory: StreamFactory | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(
            WatchConfig(sample_rate=int(sample_rate)),
            capture_factory=capture_factory,
            stream_factory=stream_factory,
            parent=parent,
        )
        self.set_input_device(device_id)
        self.last_estimate: PpsEstimate | None = None
        self.pps_updated.connect(self._remember)

    def _remember(self, estimate: PpsEstimate) -> None:
        self.last_estimate = estimate

    def _make_hook(self, fs: float, stream: SampleStream) -> _PpsHook:
        return _PpsHook(self, fs, stream)
