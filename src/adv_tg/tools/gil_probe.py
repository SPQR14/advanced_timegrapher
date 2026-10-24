"""GIL / threading test bench for the capture layer (phase 1c diagnostics).

``python -m adv_tg.tools.gil_probe --scenario NAME`` or ``--all``.

Each scenario runs in its own subprocess (clean interpreter) for ``--secs`` seconds with a
real ``AudioCapture`` + ``SampleStream`` and prints one JSON result line. ``--all`` runs every
scenario for three variants (1024/low, 4096/high, 1024/low with ``sys.setswitchinterval``
0.5 ms), prints Markdown tables and saves them to ``logs/gil_probe_<date>.md``.

Scenarios:

* ``main``: stream opened and started in the main thread, nothing else.
* ``thread``: opened and started inside a ``threading.Thread``; main thread idle.
* ``qthread``: opened inside a ``QThread`` with an event loop (``QCoreApplication``) and a
  ~30 Hz ``QTimer`` that reads ``read_latest`` and builds the min/max envelope.
* ``gui_idle``: ``QApplication`` + visible ``MainWin`` (not capturing); the probe opens the
  capture in the main thread.
* ``gui_mock``: same with the MOCK engine running in the GUI.
* ``gui_real``: the user's path: ``MainWin`` with the hardware device, capture started
  programmatically (``CaptureStreamWorker``).
* ``gui_real_nopaint``: like ``gui_real`` but the main window is never shown.

``--isolated`` runs the capture in a child process (``ProcessAudioCapture``, phase 1d) in
every scenario; ``--all`` always adds variant (iv) "1024/low aislado" for the GUI scenarios.
The table then separates the ingest measured in the PARENT (``SampleStream``) from the
child's callbacks.

GUI scenarios: the window is shown, raised and activated, and its visibility is sampled
during the whole measurement (``isVisible()`` and ``windowHandle().isExposed()``). A run whose
window was covered or minimized is NOT representative (column "Ventana visible").

``--fake-source`` replaces ``sounddevice`` with a pure-Python fake device so the bench can be
checked without hardware (its GIL-wait figures are meaningless: the fake "audio thread" is a
Python thread that already holds the GIL when it measures).

This tool only measures; it does not change the product. In the GUI scenarios with variants
other than 1024/low it overrides ``capture_worker.PRIMARY_BLOCKSIZE``/``PRIMARY_LATENCY``
inside the probe subprocess only.
"""

from __future__ import annotations

import argparse
import faulthandler
import json
import math
import os
import platform
import subprocess
import sys
import threading
import time
import traceback
import types
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from adv_tg.audio.capture import AudioCapture
from adv_tg.audio.process_capture import ProcessAudioCapture
from adv_tg.core.sample_stream import SampleStream

SCENARIOS: tuple[str, ...] = (
    "main",
    "thread",
    "qthread",
    "gui_idle",
    "gui_mock",
    "gui_real",
    "gui_real_nopaint",
)
#: (label, blocksize, latency, switchinterval)
VARIANTS: tuple[tuple[str, int, str, float | None], ...] = (
    ("(i) 1024/low (base)", 1024, "low", None),
    ("(ii) 4096/high", 4096, "high", None),
    ("(iii) 1024/low + switchinterval 0.5 ms", 1024, "low", 0.0005),
)
#: Variant (iv): always isolated, GUI scenarios only.
ISOLATED_VARIANT: tuple[str, int, str, float | None] = ("(iv) 1024/low aislado", 1024, "low", None)
GUI_SCENARIOS: tuple[str, ...] = ("gui_idle", "gui_mock", "gui_real", "gui_real_nopaint")
#: GUI scenarios whose window must stay visible for the run to be representative.
VISIBLE_SCENARIOS: tuple[str, ...] = ("gui_idle", "gui_mock", "gui_real")
VISIBILITY_SAMPLE_MS = 250
EXPOSE_TIMEOUT_S = 5.0
VISIBILITY_END_GUARD_S = 0.3
COVER_WARNING = "No cubras ni minimices la ventana de la GUI durante la prueba"
JSON_MARK = "GIL_PROBE_JSON:"
BUFFER_S = 30.0
SAFETY_MARGIN_S = 25.0
LAG_TIMER_MS = 10
READER_TICK_MS = 33
WAVEFORM_WINDOW_S = 0.6
WAVEFORM_BINS = 960
SILENCE_DBFS = -90.0


# ---------------------------------------------------------------------- capture helpers
class _ProbeTiming:
    """Mixin recording when the capture started and stopped (probe only)."""

    instances: list[Any] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.t_start: float | None = None
        self.t_stop: float | None = None
        _ProbeTiming.instances.append(self)

    def start(self) -> None:
        super().start()  # type: ignore[misc]
        if self.t_start is None:
            self.t_start = time.perf_counter()

    def stop(self) -> None:
        if self.t_start is not None and self.t_stop is None:
            self.t_stop = time.perf_counter()
        super().stop()  # type: ignore[misc]


class ProbeCapture(_ProbeTiming, AudioCapture):
    """In-process ``AudioCapture`` with start/stop times."""


class ProbeProcessCapture(_ProbeTiming, ProcessAudioCapture):
    """Isolated ``ProcessAudioCapture`` with start/stop times."""

    default_fake: str | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("open_timeout_s", 15.0)
        kwargs.setdefault("fake_source", ProbeProcessCapture.default_fake)
        super().__init__(*args, **kwargs)


def _new_capture(args: argparse.Namespace) -> tuple[SampleStream, Any]:
    stream = SampleStream(int(args.fs * BUFFER_S), args.fs)
    cls = ProbeProcessCapture if args.isolated else ProbeCapture
    cap = cls(
        stream,
        device=args.device,
        sample_rate=args.fs,
        blocksize=args.blocksize,
        latency=args.latency,
    )
    return stream, cap


def _os_threads() -> int:
    """Number of OS threads of this process (falls back to Python threads)."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(
                ["ps", "-M", "-p", str(os.getpid())], capture_output=True, text=True, timeout=5
            ).stdout
            return max(0, len(out.strip().splitlines()) - 1)
        if sys.platform.startswith("linux"):
            for line in Path("/proc/self/status").read_text().splitlines():
                if line.startswith("Threads:"):
                    return int(line.split()[1])
    except Exception:
        pass
    return threading.active_count()


def _metrics(
    stream: SampleStream, cap: ProbeCapture, cpu_s: float, extra: dict[str, Any]
) -> dict[str, Any]:
    """Collect the common metrics once the capture has stopped."""
    t0 = cap.t_start or time.perf_counter()
    wall = max(1e-9, (cap.t_stop or time.perf_counter()) - t0)
    st = stream.stats()
    ends, _ = stream.block_log()
    if cap.blocksize > 0:
        block = float(cap.blocksize)
    elif ends.size >= 2:
        block = float(np.median(np.diff(ends)))
    else:
        block = math.nan
    total = stream.total_samples
    peak = -math.inf
    if total:
        x = stream.read(stream.oldest_available, total, np.float32)
        p = float(np.max(np.abs(x)))
        peak = 20.0 * math.log10(p) if p > 0 else -math.inf
    out: dict[str, Any] = {
        "wall_s": wall,
        "callbacks": cap.callbacks,
        "expected_blocks": (stream.sample_rate * wall / block) if block == block else None,
        "block_samples": block if block == block else None,
        "total_samples": total,
        "received_pct": 100.0 * total / (stream.sample_rate * wall),
        "ingest_pct": 100.0 * st.ingest_ratio,
        "max_gap_ms": st.max_gap_ms,
        "gil_mean_ms": cap.gil_wait_mean_us / 1000.0,
        "gil_p99_ms": cap.gil_wait_p99_ms,
        "gil_max_ms": cap.gil_wait_max_us / 1000.0,
        "gil_over_block": cap.gil_wait_over_block,
        "gil_invalid": cap.gil_wait_invalid,
        "gil_hist": list(cap.gil_wait_hist),
        "status_flags": cap.status_flags,
        "callback_errors": cap.callback_errors,
        "max_callback_us": cap.max_callback_us,
        "isolated": isinstance(cap, ProcessAudioCapture),
        "parent_blocks": st.blocks,
        "dropped_frames": getattr(cap, "dropped_frames", None),
        "backlog_max": getattr(cap, "backlog_max", None),
        "parent_lag_max_ms": getattr(cap, "parent_lag_max_ms", None),
        "child_frames_sent": getattr(cap, "seq_sent", None),
        "window_visible_pct": None,
        "window_expected_visible": None,
        "cpu_pct": 100.0 * cpu_s / wall,
        "peak_dbfs": peak,
        "actual_fs": cap.actual_sample_rate,
        "loop_lag_max_ms": None,
        "reader_fps": None,
    }
    out.update(extra)
    return out


class _LoopLag:
    """Main-thread QTimer (10 ms) that measures its own lateness."""

    def __init__(self) -> None:
        from PyQt6.QtCore import Qt, QTimer

        self.max_ms = 0.0
        self.over_50 = 0
        self._last = time.perf_counter()
        self._timer = QTimer()
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(LAG_TIMER_MS)
        self._timer.timeout.connect(self._on)
        self._timer.start()

    def _on(self) -> None:
        now = time.perf_counter()
        lag = (now - self._last) * 1000.0 - LAG_TIMER_MS
        self._last = now
        self.max_ms = max(self.max_ms, lag)
        if lag > 50.0:
            self.over_50 += 1

    def stop(self) -> None:
        self._timer.stop()


def _run_qt_loop(app: Any, secs: float, threads_box: list[int]) -> None:
    from PyQt6.QtCore import QTimer

    QTimer.singleShot(int(max(0.0, secs - 1.0) * 1000), lambda: threads_box.append(_os_threads()))
    QTimer.singleShot(int(secs * 1000), app.quit)
    app.exec()


# ---------------------------------------------------------------------- scenarios
def _scenario_plain(args: argparse.Namespace, in_thread: bool) -> dict[str, Any]:
    stream, cap = _new_capture(args)
    threads_box: list[int] = []
    errors: list[BaseException] = []

    def body() -> None:
        try:
            cap.start()
            time.sleep(max(0.0, args.secs - 1.0))
            threads_box.append(_os_threads())
            time.sleep(min(1.0, args.secs))
        except BaseException as exc:  # reported by the main thread
            errors.append(exc)
        finally:
            cap.close()

    c0 = time.process_time()
    if in_thread:
        th = threading.Thread(target=body, name="ProbeCaptureThread")
        th.start()
        th.join(args.secs + SAFETY_MARGIN_S)
    else:
        body()
    cpu = time.process_time() - c0
    if errors:
        raise errors[0]
    return _metrics(stream, cap, cpu, {"threads": threads_box[0] if threads_box else None})


def _scenario_qthread(args: argparse.Namespace) -> dict[str, Any]:
    from PyQt6.QtCore import (
        QCoreApplication,
        QMetaObject,
        QObject,
        Qt,
        QThread,
        QTimer,
        pyqtSlot,
    )

    from adv_tg.mock.generator import minmax_decimate

    app = QCoreApplication.instance() or QCoreApplication([])
    stream, cap = _new_capture(args)
    fs = float(args.fs)

    class Reader(QObject):
        def __init__(self) -> None:
            super().__init__()
            self.ticks = 0
            self.error: BaseException | None = None
            self.timer: QTimer | None = None

        @pyqtSlot()
        def run(self) -> None:
            try:
                cap.start()
            except BaseException as exc:
                self.error = exc
                app.quit()
                return
            self.timer = QTimer(self)
            self.timer.setTimerType(Qt.TimerType.PreciseTimer)
            self.timer.setInterval(READER_TICK_MS)
            self.timer.timeout.connect(self._tick)
            self.timer.start()

        def _tick(self) -> None:
            _, x = stream.read_latest(int(round(WAVEFORM_WINDOW_S * fs)), np.float64)
            if x.size > 2 * WAVEFORM_BINS:
                x = x[x.size % WAVEFORM_BINS :]
            if x.size:
                minmax_decimate(x, fs, WAVEFORM_BINS)
                self.ticks += 1

        @pyqtSlot()
        def stop(self) -> None:
            if self.timer is not None:
                self.timer.stop()
            cap.close()
            QThread.currentThread().quit()

    thread = QThread()
    reader = Reader()
    reader.moveToThread(thread)
    lag = _LoopLag()
    threads_box: list[int] = []
    c0 = time.process_time()
    thread.start()
    try:
        QMetaObject.invokeMethod(reader, "run", Qt.ConnectionType.QueuedConnection)
        _run_qt_loop(app, args.secs, threads_box)
    finally:
        lag.stop()
        QMetaObject.invokeMethod(reader, "stop", Qt.ConnectionType.QueuedConnection)
        if not thread.wait(5000):
            cap.close()
    cpu = time.process_time() - c0
    if reader.error is not None:
        raise reader.error
    wall = (cap.t_stop or time.perf_counter()) - (cap.t_start or time.perf_counter())
    return _metrics(
        stream,
        cap,
        cpu,
        {
            "threads": threads_box[0] if threads_box else None,
            "loop_lag_max_ms": lag.max_ms,
            "loop_lag_over_50ms": lag.over_50,
            "reader_fps": reader.ticks / wall if wall > 0 else None,
        },
    )


def _make_window(show: bool) -> tuple[Any, Any]:
    """QApplication + MainWin, created like ``adv_tg.main.main`` does."""
    from adv_tg.ui.main_window import configure_pyqtgraph_gpu

    configure_pyqtgraph_gpu()
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([sys.argv[0]])
    from adv_tg.i18n.translator import Translator
    from adv_tg.ui.main_window import MainWin

    win = MainWin(translator=Translator("en"))
    if show:
        win.show()
        win.raise_()
        win.activateWindow()
        # Do not start measuring before the window is actually on screen.
        deadline = time.monotonic() + EXPOSE_TIMEOUT_S
        while time.monotonic() < deadline:
            app.processEvents()
            handle = win.windowHandle()
            if handle is not None and handle.isExposed():
                break
            time.sleep(0.02)
    return app, win


class _VisibilityProbe:
    """Samples whether the window is visible and exposed (not covered/minimized)."""

    def __init__(self, win: Any, duration_s: float) -> None:
        from PyQt6.QtCore import QTimer

        self._win = win
        self.samples = 0
        self.visible = 0
        self.hidden_at_s: list[float] = []
        self._t0 = time.perf_counter()
        self._timer = QTimer()
        self._timer.setInterval(VISIBILITY_SAMPLE_MS)
        self._timer.timeout.connect(self._sample)
        self._timer.start()
        # Stop sampling just before the run ends: closing the event loop hides the window.
        QTimer.singleShot(int(max(0.0, duration_s - VISIBILITY_END_GUARD_S) * 1000), self.stop)

    def _sample(self) -> None:
        handle = self._win.windowHandle()
        exposed = bool(handle is not None and handle.isExposed())
        self.samples += 1
        if self._win.isVisible() and exposed:
            self.visible += 1
        elif len(self.hidden_at_s) < 40:
            self.hidden_at_s.append(round(time.perf_counter() - self._t0, 2))

    @property
    def pct(self) -> float | None:
        return 100.0 * self.visible / self.samples if self.samples else None

    def stop(self) -> None:
        self._timer.stop()


def _scenario_gui_probe_capture(args: argparse.Namespace, mock: bool) -> dict[str, Any]:
    app, win = _make_window(show=True)
    stream, cap = _new_capture(args)
    threads_box: list[int] = []
    lag = None
    c0 = time.process_time()
    try:
        if mock:
            win.btn_mock.setChecked(True)
            win.spn_stab.setValue(0)
            win.spn_duration.setValue(win.spn_duration.maximum())
            win.start_capture()
            if not win.dsp_worker.isRunning():
                raise RuntimeError("el MOCK no arrancó")
        cap.start()
        lag = _LoopLag()
        vis = _VisibilityProbe(win, args.secs)
        _run_qt_loop(app, args.secs, threads_box)
        vis.stop()
    finally:
        if lag is not None:
            lag.stop()
        cap.close()
        win.stop_capture()
        win.close()
    cpu = time.process_time() - c0
    return _metrics(
        stream,
        cap,
        cpu,
        {
            "threads": threads_box[0] if threads_box else None,
            "loop_lag_max_ms": lag.max_ms if lag else None,
            "loop_lag_over_50ms": lag.over_50 if lag else None,
            "window_visible_pct": vis.pct,
            "window_hidden_at_s": vis.hidden_at_s,
            "window_expected_visible": True,
        },
    )


def _scenario_gui_real(args: argparse.Namespace, show: bool) -> dict[str, Any]:
    from adv_tg.ui import capture_worker

    # Same code path as the user; only record the AudioCapture instance (and, for the
    # non-base variants, the open parameters) inside this probe process.
    capture_worker.AudioCapture = ProbeCapture  # type: ignore[misc]
    capture_worker.ProcessAudioCapture = ProbeProcessCapture  # type: ignore[misc]
    capture_worker.CAPTURE_IN_SEPARATE_PROCESS = bool(args.isolated)
    capture_worker.PRIMARY_BLOCKSIZE = args.blocksize
    capture_worker.PRIMARY_LATENCY = args.latency

    app, win = _make_window(show=show)
    threads_box: list[int] = []
    lag = None
    c0 = time.process_time()
    worker = None
    waveforms = 0
    try:
        idx = win.cmb_device.findData(args.device)
        if idx < 0:
            raise RuntimeError(f"el dispositivo {args.device} no aparece en el selector de la GUI")
        win.cmb_device.setCurrentIndex(idx)
        sidx = win.cmb_srate.findData(int(args.fs))
        if sidx < 0:
            raise RuntimeError(f"la GUI no ofrece {args.fs} Hz")
        win.cmb_srate.setCurrentIndex(sidx)
        win.spn_stab.setValue(0)
        win.spn_duration.setValue(win.spn_duration.maximum())
        errors: list[str] = []
        win.dsp_worker.error_occurred.connect(errors.append)
        win.start_capture()
        worker = win.dsp_worker
        lag = _LoopLag()
        vis = _VisibilityProbe(win, args.secs)
        _run_qt_loop(app, args.secs, threads_box)
        vis.stop()
        if errors:
            raise RuntimeError(f"la GUI emitió error: {errors[0]}")
        reader = getattr(worker, "_reader", None)
        waveforms = getattr(reader, "_waveforms", 0)
    finally:
        if lag is not None:
            lag.stop()
        win.stop_capture()
        win.close()
    cpu = time.process_time() - c0
    if not _ProbeTiming.instances or worker is None or worker.stream is None:
        raise RuntimeError("la GUI no llegó a abrir la captura")
    cap = _ProbeTiming.instances[-1]
    wall = (cap.t_stop or time.perf_counter()) - (cap.t_start or time.perf_counter())
    return _metrics(
        worker.stream,
        cap,
        cpu,
        {
            "threads": threads_box[0] if threads_box else None,
            "loop_lag_max_ms": lag.max_ms if lag else None,
            "loop_lag_over_50ms": lag.over_50 if lag else None,
            "reader_fps": waveforms / wall if wall > 0 else None,
            "open_params": list(worker.open_params) if worker.open_params else None,
            "window_visible_pct": vis.pct,
            "window_hidden_at_s": vis.hidden_at_s,
            "window_expected_visible": show,
        },
    )


RUNNERS: dict[str, Callable[[argparse.Namespace], dict[str, Any]]] = {
    "main": lambda a: _scenario_plain(a, in_thread=False),
    "thread": lambda a: _scenario_plain(a, in_thread=True),
    "qthread": _scenario_qthread,
    "gui_idle": lambda a: _scenario_gui_probe_capture(a, mock=False),
    "gui_mock": lambda a: _scenario_gui_probe_capture(a, mock=True),
    "gui_real": lambda a: _scenario_gui_real(a, show=True),
    "gui_real_nopaint": lambda a: _scenario_gui_real(a, show=False),
}


# ---------------------------------------------------------------------- fake device
def install_fake_sounddevice(device: int | None) -> None:
    """Install a pure-Python fake ``sounddevice`` module (tone + ticks at real-time pace)."""
    dev = 0 if device is None else int(device)
    mod = types.ModuleType("sounddevice")

    def query_devices(device: Any = None, kind: Any = None) -> Any:
        devs = [
            {
                "name": f"Fake input {i}" if i == dev else f"Fake output {i}",
                "index": i,
                "max_input_channels": 1 if i == dev else 0,
                "default_samplerate": 48000.0,
            }
            for i in range(dev + 1)
        ]
        if device is None and kind is None:
            return devs
        return devs[dev]

    class FakeInputStream:
        def __init__(self, *, samplerate: float, blocksize: int, callback: Any, **_: Any) -> None:
            self.samplerate = float(samplerate)
            self.blocksize = int(blocksize) or 512
            self._callback = callback
            self._stop = threading.Event()
            self._thread: threading.Thread | None = None

        @property
        def time(self) -> float:
            return time.perf_counter()

        @property
        def active(self) -> bool:
            return self._thread is not None and self._thread.is_alive()

        def start(self) -> None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="FakeAudio", daemon=True)
            self._thread.start()

        def _run(self) -> None:
            fs, n = self.samplerate, self.blocksize
            t0 = time.perf_counter()
            k = 0
            phase = np.arange(n) / fs
            while not self._stop.is_set():
                k += 1
                wait = t0 + k * n / fs - time.perf_counter()
                if wait > 0 and self._stop.wait(wait):
                    break
                t = (k - 1) * n / fs + phase
                x = 0.02 * np.sin(2 * np.pi * 440.0 * t)
                x += 0.3 * (np.mod(t, 1 / 6) < 0.002)
                info = types.SimpleNamespace(currentTime=time.perf_counter())
                self._callback(x.astype(np.float32).reshape(-1, 1), n, info, 0)

        def stop(self) -> None:
            self._stop.set()
            if self._thread is not None:
                self._thread.join(2.0)

        def close(self) -> None:
            self.stop()

    mod.query_devices = query_devices
    mod.check_input_settings = lambda **kw: None
    mod.InputStream = FakeInputStream
    mod.default = types.SimpleNamespace(device=(dev, None))
    sys.modules["sounddevice"] = mod


# ---------------------------------------------------------------------- child / parent
def _child(args: argparse.Namespace) -> int:
    faulthandler.dump_traceback_later(args.secs + SAFETY_MARGIN_S, exit=True)
    if args.fake_source:
        install_fake_sounddevice(args.device)
        ProbeProcessCapture.default_fake = "tone"  # the capture child cannot see the fake module
    if args.switchinterval:
        sys.setswitchinterval(args.switchinterval)
    result: dict[str, Any] = {
        "scenario": args.scenario,
        "blocksize": args.blocksize,
        "latency": args.latency,
        "switchinterval": sys.getswitchinterval(),
        "fake_source": bool(args.fake_source),
        "isolated": bool(args.isolated),
        "ok": False,
    }
    try:
        result.update(RUNNERS[args.scenario](args))
        result["ok"] = True
    except BaseException as exc:
        traceback.print_exc()
        result["error"] = f"{type(exc).__name__}: {exc}"
    print(JSON_MARK + json.dumps(result, default=str), flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)  # skip Qt/PortAudio teardown: the result is already out


def _child_cmd(
    args: argparse.Namespace,
    scenario: str,
    blocksize: int,
    latency: str,
    switchinterval: float | None,
    isolated: bool,
) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "adv_tg.tools.gil_probe",
        "--run-child",
        "--scenario",
        scenario,
        "--fs",
        str(args.fs),
        "--blocksize",
        str(blocksize),
        "--latency",
        latency,
        "--secs",
        str(args.secs),
    ]
    if args.device is not None:
        cmd += ["--device", str(args.device)]
    if switchinterval:
        cmd += ["--switchinterval", str(switchinterval)]
    if args.fake_source:
        cmd.append("--fake-source")
    if isolated:
        cmd.append("--isolated")
    return cmd


def run_scenario(
    args: argparse.Namespace,
    scenario: str,
    blocksize: int,
    latency: str,
    switchinterval: float | None,
    isolated: bool | None = None,
) -> dict[str, Any]:
    """Run one scenario in a subprocess and return its JSON result."""
    env = dict(os.environ)
    if args.qpa:
        env["QT_QPA_PLATFORM"] = args.qpa
    iso = bool(args.isolated) if isolated is None else isolated
    cmd = _child_cmd(args, scenario, blocksize, latency, switchinterval, iso)
    base = {"scenario": scenario, "ok": False, "isolated": iso}
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=args.secs + 2 * SAFETY_MARGIN_S,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {**base, "error": "tiempo máximo de seguridad excedido (proceso terminado)"}
    for line in proc.stdout.splitlines():
        if line.startswith(JSON_MARK):
            return json.loads(line[len(JSON_MARK) :])
    tail = " | ".join(proc.stderr.strip().splitlines()[-3:])
    return {**base, "error": f"sin resultado (código {proc.returncode}): {tail}"}


def _fmt(v: Any, spec: str = ".1f") -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        if math.isinf(v):
            return "-inf" if v < 0 else "inf"
        if math.isnan(v):
            return "—"
    return format(v, spec)


def _visibility_cell(r: dict[str, Any]) -> str:
    expected = r.get("window_expected_visible")
    pct = r.get("window_visible_pct")
    if expected is None:
        return "—"
    if not expected:
        return "no (oculta a propósito)"
    if pct is None:
        return "?"
    return f"sí ({pct:.0f} %)" if pct >= 99.5 else f"NO ({pct:.0f} %)"


TABLE_COLUMNS = (
    "Escenario",
    "Modo",
    "Ventana visible",
    "Callbacks (hijo/stream)",
    "Bloques en padre",
    "Esperados",
    "Ingesta padre %",
    "Recibido %",
    "Tramas perdidas",
    "Backlog máx",
    "Retraso padre máx (ms)",
    "Hueco máx (ms)",
    "GIL media / p99 / máx (ms)",
    ">1 bloque",
    "Lag bucle máx (ms)",
    "Lector FPS",
    "CPU % (padre)",
    "Hilos",
    "Pico dBFS",
    "Estado",
)


def markdown_table(rows: list[dict[str, Any]]) -> str:
    """Comparison table (one row per scenario).

    "Callbacks" are counted where the stream runs (the child when isolated); "Bloques en
    padre" and "Ingesta padre" come from the parent's ``SampleStream``.
    """
    lines = [
        "| " + " | ".join(TABLE_COLUMNS) + " |",
        "|" + "---|" * len(TABLE_COLUMNS),
    ]
    for r in rows:
        mode = "aislado" if r.get("isolated") else "en proceso"
        if not r.get("ok"):
            empty = "— | " * (len(TABLE_COLUMNS) - 3)
            lines.append(f"| {r['scenario']} | {mode} | {empty}ERROR: {r.get('error', '?')} |")
            continue
        state = "OK"
        vis = _visibility_cell(r)
        if r.get("peak_dbfs") is None or r["peak_dbfs"] < SILENCE_DBFS:
            state = "SILENCIO (¿permiso de micrófono?)"
        elif vis.startswith("NO") or vis == "?":
            state = "NO REPRESENTATIVA (ventana tapada/minimizada)"
        lines.append(
            f"| {r['scenario']} | {mode} | {vis} | {r['callbacks']} "
            f"| {_fmt(r.get('parent_blocks'), 'd')} | {_fmt(r['expected_blocks'], '.0f')} "
            f"| {_fmt(r['ingest_pct'])} | {_fmt(r['received_pct'])} "
            f"| {_fmt(r.get('dropped_frames'), 'd')} | {_fmt(r.get('backlog_max'), 'd')} "
            f"| {_fmt(r.get('parent_lag_max_ms'))} | {_fmt(r['max_gap_ms'])} "
            f"| {_fmt(r['gil_mean_ms'], '.2f')} / {_fmt(r['gil_p99_ms'], '.1f')} / "
            f"{_fmt(r['gil_max_ms'], '.1f')} | {r['gil_over_block']} "
            f"| {_fmt(r.get('loop_lag_max_ms'))} | {_fmt(r.get('reader_fps'))} "
            f"| {_fmt(r['cpu_pct'], '.0f')} | {_fmt(r.get('threads'), 'd')} "
            f"| {_fmt(r['peak_dbfs'])} | {state} |"
        )
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m adv_tg.tools.gil_probe",
        description="Banco de pruebas de captura: GIL, hilos y GUI (diagnóstico, fases 1c/1d).",
    )
    p.add_argument("--scenario", choices=SCENARIOS, help="escenario a ejecutar")
    p.add_argument("--all", action="store_true", help="todos los escenarios y variantes")
    p.add_argument("--device", type=int, default=None, help="índice PortAudio del dispositivo")
    p.add_argument("--fs", type=int, default=48000, help="tasa de muestreo (Hz)")
    p.add_argument("--blocksize", type=int, default=1024, help="muestras por bloque")
    p.add_argument("--latency", choices=("low", "high"), default="low")
    p.add_argument("--secs", type=float, default=20.0, help="duración de cada escenario")
    p.add_argument("--switchinterval", type=float, default=None, help="sys.setswitchinterval")
    p.add_argument(
        "--isolated", action="store_true", help="captura en proceso aparte (ProcessAudioCapture)"
    )
    p.add_argument("--fake-source", action="store_true", help="dispositivo falso (sin hardware)")
    p.add_argument("--qpa", default=None, help="QT_QPA_PLATFORM para los subprocesos")
    p.add_argument("--run-child", action="store_true", help=argparse.SUPPRESS)
    return p


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns the exit code (0 ok, 1 some scenario failed, 2 bad arguments)."""
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    if args.secs <= 0 or args.fs <= 0 or args.blocksize < 0:
        print("ERROR: --secs y --fs deben ser positivos y --blocksize >= 0")
        return 2
    if args.run_child:
        if not args.scenario:
            return 2
        return _child(args)
    if not args.all and not args.scenario:
        print("ERROR: indica --scenario NOMBRE o --all")
        return 2

    # (label, blocksize, latency, switchinterval, isolated, scenarios)
    plan: list[tuple[str, int, str, float | None, bool, tuple[str, ...]]]
    if args.all:
        suffix = " · aislado" if args.isolated else ""
        plan = [(f"{v[0]}{suffix}", *v[1:], bool(args.isolated), SCENARIOS) for v in VARIANTS]
        plan.append((*ISOLATED_VARIANT, True, GUI_SCENARIOS))
    else:
        plan = [
            (
                "personalizado",
                args.blocksize,
                args.latency,
                args.switchinterval,
                bool(args.isolated),
                (args.scenario,),
            )
        ]
    if any(sc in GUI_SCENARIOS for *_, scs in plan for sc in scs):
        print(f"AVISO: {COVER_WARNING}.", flush=True)

    started = datetime.now()
    sections = [
        f"# gil_probe — {started:%Y-%m-%d %H:%M:%S}",
        "",
        f"- Comando: `python -m adv_tg.tools.gil_probe {' '.join(argv or sys.argv[1:])}`",
        f"- Máquina: {platform.platform()} | Python {platform.python_version()}",
        f"- Dispositivo: {args.device} | fs {args.fs} Hz | {args.secs:g} s por escenario"
        + (" | FUENTE FALSA (sin hardware)" if args.fake_source else ""),
        f"- {COVER_WARNING}: una medición con la ventana tapada no es representativa.",
        "",
    ]
    any_failed = False
    all_rows: list[dict[str, Any]] = []
    for label, blocksize, latency, si, isolated, scenarios in plan:
        rows = []
        for sc in scenarios:
            print(f"[{datetime.now():%H:%M:%S}] {label} · {sc} …", flush=True)
            r = run_scenario(args, sc, blocksize, latency, si, isolated)
            r["variant"] = label
            rows.append(r)
            any_failed |= not r.get("ok")
            if not r.get("ok"):
                print(f"   ERROR: {r.get('error')}", flush=True)
        table = markdown_table(rows)
        sections += [f"## Variante {label}", "", table, ""]
        all_rows += rows
        print(f"\n## Variante {label}\n\n{table}\n", flush=True)

    sections += [
        "## Datos crudos (JSON)",
        "",
        "```json",
        json.dumps(all_rows, indent=1, default=str),
        "```",
        "",
    ]
    if args.all:
        from adv_tg.utils.logger_setup import LoggerSetup

        LoggerSetup.LOG_DIR.mkdir(parents=True, exist_ok=True)
        out = LoggerSetup.LOG_DIR / f"gil_probe_{started:%Y%m%d_%H%M%S}.md"
        out.write_text("\n".join(sections), encoding="utf-8")
        print(f"Tabla guardada en '{out}'")
    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())
