"""``MockStreamWorker``: simulated measurement engine so the whole GUI can be tested.

Runs its own Python thread and emits, without any real DSP:

* ``telemetry_ready`` 4 times per second (``BeatTelemetry`` with the last beat waveform),
* ``waveform_ready`` at ~30 FPS (scrolling 0.6 s view of the simulated tic/tac train),
* ``level_updated`` 10 times per second (simulated plausible level, never saturated),
* ``drift_updated`` and ``capture_health`` once per second.

MOCK-only extras (not part of ``BaseStreamWorker``): ``set_session_timing()`` and the
``drift_detail(sigma_ppm, confidence)`` signal, emitted right after ``drift_updated``.

It stops by itself after ``stabilization_s + duration_s`` (plus a short grace period,
the GUI timer normally stops it first) or when ``stop_stream()`` is called.
"""

from __future__ import annotations

import threading
import time
from collections import deque

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from adv_tg.core.models import BeatEvent, BeatTelemetry, WatchConfig
from adv_tg.mock.generator import (
    MockSample,
    MockTelemetryModel,
    add_beat,
    beat_delta_t_s,
    beat_interval_s,
    beat_waveform,
    minmax_decimate,
    noise_sigma,
)
from adv_tg.ui.stream_worker_base import BaseStreamWorker
from adv_tg.utils.debugger import Debugger

_dbg = Debugger.get(__name__)
logger = _dbg.logger

TELEMETRY_PERIOD_S: float = 0.25
WAVEFORM_PERIOD_S: float = 1.0 / 30.0
SLOW_PERIOD_S: float = 1.0
LEVEL_PERIOD_S: float = 0.1
MOCK_PEAK_DBFS: float = -12.0
MOCK_RMS_DBFS: float = -32.0
WAVEFORM_WINDOW_S: float = 0.6
END_GRACE_S: float = 0.5
JOIN_TIMEOUT_S: float = 2.0


class MockStreamWorker(BaseStreamWorker):
    """Simulated engine implementing ``BaseStreamWorker`` (see module docstring)."""

    # MOCK-only: (drift_sigma_ppm, drift_confidence 0..1)
    drift_detail = pyqtSignal(float, float)

    def __init__(
        self,
        config: WatchConfig | None = None,
        *,
        seed: int | None = None,
        stabilization_s: float = 10.0,
        duration_s: float = 30.0,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(config, parent=parent)
        self.stabilization_s: float = float(stabilization_s)
        self.duration_s: float = float(duration_s)
        self.synthetic_mode = True
        self.synthetic_rate_spd = 4.5
        self.synthetic_beat_error_ms = 0.35
        self.synthetic_amplitude_deg = 282.0
        self.seed = seed
        self._session_count = 0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ MOCK-only
    def set_session_timing(self, stabilization_s: float, duration_s: float) -> None:
        """Store the stabilization time and test duration chosen in the GUI."""
        self.stabilization_s = max(0.0, float(stabilization_s))
        self.duration_s = max(0.0, float(duration_s))

    # ------------------------------------------------------------------ contract
    def start_stream(self) -> None:
        if self.isRunning():
            logger.warning("[MOCK] start_stream ignored: already running")
            return
        self._stop_event.clear()
        self._session_count += 1
        self._thread = threading.Thread(
            target=self._run, name=f"MockStreamWorker-{self._session_count}", daemon=True
        )
        self._thread.start()
        logger.info("[MOCK] start_stream: thread %s launched", self._thread.name)

    def stop_stream(self) -> None:
        thread = self._thread
        if thread is None:
            return
        logger.info("[MOCK] stop_stream requested (thread %s)", thread.name)
        self._stop_event.set()
        if thread is not threading.current_thread():
            t0 = time.perf_counter()
            thread.join(JOIN_TIMEOUT_S)
            if thread.is_alive():
                logger.error("[MOCK] worker thread did not stop within %.1f s", JOIN_TIMEOUT_S)
                return
            _dbg.process_end(
                f"join {thread.name}", wait_ms=round((time.perf_counter() - t0) * 1000.0, 1)
            )
        self._thread = None

    def isRunning(self) -> bool:  # noqa: N802
        thread = self._thread
        return thread is not None and thread.is_alive() and not self._stop_event.is_set()

    # ------------------------------------------------------------------ thread
    def _session_seed(self) -> int | None:
        return None if self.seed is None else int(self.seed) + self._session_count - 1

    def _run(self) -> None:
        cfg = self._config
        _dbg.process_start(
            "MockStreamWorker (DATOS SIMULADOS)",
            bph=cfg.bph,
            lift=cfg.lift_angle,
            fs=cfg.sample_rate,
            stab_s=self.stabilization_s,
            duration_s=self.duration_s,
            base_rate_spd=self.synthetic_rate_spd,
            base_be_ms=self.synthetic_beat_error_ms,
            base_amp_deg=self.synthetic_amplitude_deg,
            seed=self._session_seed(),
        )
        seed_seq = np.random.SeedSequence(self._session_seed())
        model_seed, wave_seed, beat_seed = seed_seq.spawn(3)
        model = MockTelemetryModel(seed=int(model_seed.generate_state(1)[0]))
        wave_rng = np.random.default_rng(wave_seed)
        beat_rng = np.random.default_rng(beat_seed)

        beats: deque[tuple[float, bool]] = deque()
        next_beat_t = 0.005
        next_is_tock = False
        sample = MockSample(
            self.synthetic_rate_spd,
            self.synthetic_beat_error_ms,
            self.synthetic_amplitude_deg,
            0.5,
            30.0,
        )
        n_telemetry = 0
        stab_reported = False
        reason = "stopped"

        t0 = time.monotonic()
        next_tel = next_wave = next_slow = next_level = t0
        last_tel_elapsed = 0.0
        try:
            while not self._stop_event.is_set():
                now = time.monotonic()
                elapsed = now - t0
                cfg = self._config
                fs = float(cfg.sample_rate)

                if not stab_reported and elapsed >= self.stabilization_s:
                    stab_reported = True
                    _dbg.state_change(
                        "MOCK", "ESTABILIZANDO", "MIDIENDO", elapsed_s=round(elapsed, 2)
                    )
                if elapsed >= self.stabilization_s + self.duration_s + END_GRACE_S:
                    reason = "test duration reached"
                    logger.info("[MOCK] end of test reached at %.2f s", elapsed)
                    break

                # Extend the simulated beat timeline up to "now"
                dt_p3 = beat_delta_t_s(sample.amplitude_deg, cfg.lift_angle, cfg.bph)
                period = beat_interval_s(cfg.bph, sample.rate_spd)
                be_s = sample.beat_error_ms / 1000.0
                while next_beat_t <= elapsed:
                    beats.append((next_beat_t, next_is_tock))
                    next_beat_t += (period + (-be_s if next_is_tock else be_s)) + float(
                        beat_rng.normal(0.0, 20e-6)
                    )
                    next_is_tock = not next_is_tock
                while beats and beats[0][0] < elapsed - WAVEFORM_WINDOW_S - 0.05:
                    beats.popleft()

                if now >= next_wave:
                    next_wave = max(next_wave + WAVEFORM_PERIOD_S, now)
                    self._emit_waveform(beats, elapsed, fs, dt_p3, sample.snr_db, wave_rng)

                if now >= next_tel:
                    next_tel = max(next_tel + TELEMETRY_PERIOD_S, now)
                    sample = model.sample(
                        elapsed,
                        elapsed - last_tel_elapsed,
                        self.stabilization_s,
                        self.synthetic_rate_spd,
                        self.synthetic_beat_error_ms,
                        self.synthetic_amplitude_deg,
                    )
                    last_tel_elapsed = elapsed
                    self._emit_telemetry(beats, sample, fs, dt_p3, wave_rng)
                    n_telemetry += 1
                    _dbg.throttled(
                        "mock_telemetry",
                        "[MOCK] telemetry #%d rate=%+.2f s/d be=%.2f ms amp=%.0f deg snr=%.1f dB",
                        n_telemetry,
                        sample.rate_spd,
                        sample.beat_error_ms,
                        sample.amplitude_deg,
                        sample.snr_db,
                        every_s=5.0,
                    )

                if now >= next_level:
                    next_level = max(next_level + LEVEL_PERIOD_S, now)
                    if not self._stop_event.is_set():
                        self.level_updated.emit(
                            MOCK_PEAK_DBFS + float(wave_rng.normal(0.0, 0.5)),
                            MOCK_RMS_DBFS + float(wave_rng.normal(0.0, 0.5)),
                            False,
                        )

                if now >= next_slow:
                    next_slow = max(next_slow + SLOW_PERIOD_S, now)
                    drift = model.drift(elapsed, fs)
                    if not self._stop_event.is_set():
                        self.drift_updated.emit(
                            drift.effective_fs, drift.drift_ppm, drift.rate_bias_spd
                        )
                        self.drift_detail.emit(drift.sigma_ppm, drift.confidence)
                        ingest = 100.0 - abs(float(wave_rng.normal(0.0, 0.03)))
                        self.capture_health.emit(ingest, 0)

                wait_s = min(next_wave, next_tel, next_slow, next_level) - time.monotonic()
                if wait_s > 0.0:
                    self._stop_event.wait(wait_s)
        except Exception as exc:
            reason = "error"
            logger.exception("[MOCK] simulated session failed")
            self.error_occurred.emit(f"MOCK: {type(exc).__name__}: {exc}")
        finally:
            _dbg.process_end(
                "MockStreamWorker (DATOS SIMULADOS)",
                reason=reason,
                elapsed_s=round(time.monotonic() - t0, 2),
                telemetry_updates=n_telemetry,
            )
            self.session_finished.emit()

    # ------------------------------------------------------------------ emitters
    def _emit_waveform(
        self,
        beats: deque[tuple[float, bool]],
        elapsed: float,
        fs: float,
        dt_p3: float,
        snr_db: float,
        rng: np.random.Generator,
    ) -> None:
        if self._stop_event.is_set():
            return
        window_start = elapsed - WAVEFORM_WINDOW_S
        buf = np.zeros(int(round(WAVEFORM_WINDOW_S * fs)), dtype=np.float64)
        for t_beat, is_tock in beats:
            add_beat(buf, fs, t_beat - window_start, dt_p3, is_tock)
        buf += rng.normal(0.0, noise_sigma(snr_db), size=len(buf))
        time_ms, values = minmax_decimate(buf, fs)
        self.waveform_ready.emit(time_ms, values)

    def _emit_telemetry(
        self,
        beats: deque[tuple[float, bool]],
        sample: MockSample,
        fs: float,
        dt_p3: float,
        rng: np.random.Generator,
    ) -> None:
        if self._stop_event.is_set():
            return
        events: list[BeatEvent] = []
        if beats:
            t_beat, is_tock = beats[-1]
            wave, t_p1, t_p3 = beat_waveform(fs, dt_p3, is_tock, sample.snr_db, rng)
            events.append(
                BeatEvent(
                    timestamp_p1=t_beat,
                    timestamp_p2=t_beat + 0.35 * (t_p3 - t_p1),
                    timestamp_p3=t_beat + (t_p3 - t_p1),
                    peak_energy=float(np.max(np.abs(wave))),
                    raw_waveform=wave,
                )
            )
        beat_times = np.fromiter((b[0] for b in beats), dtype=np.float64, count=len(beats))
        envelope = np.abs(events[-1].raw_waveform) if events else np.empty(0, dtype=np.float64)
        self.telemetry_ready.emit(
            BeatTelemetry(
                rate_spd=sample.rate_spd,
                beat_error_ms=sample.beat_error_ms,
                amplitude_deg=sample.amplitude_deg,
                beat_times_sec=beat_times,
                envelope=envelope,
                confidence=sample.confidence,
                snr_db=sample.snr_db,
                beats=events,
                effective_fs=fs,
            )
        )
