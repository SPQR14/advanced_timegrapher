"""In-app measurements for the baseline of the C++ migration (``scripts/baseline.py``).

Enabled by the environment (the C++ application of 3.0 implements the same contract, so both
are measured the same way):

* ``ADV_TG_BENCH=startup``: as soon as the main window has been shown and the event loop is
  running, print ``ADV_TG_BENCH {"mode": "startup", "window_shown_ms": ...}`` and quit.
  ``window_shown_ms`` counts from the entry of ``main()``; the total time from process launch
  is measured from outside by ``scripts/baseline.py``.
* ``ADV_TG_BENCH=<seconds>``: start a measurement (with ``ADV_TG_REPLAY_FILE`` it replays a
  file in real time), sample the GUI event loop with a 16 ms timer for that many seconds and
  print ``ADV_TG_BENCH {"mode": "live", ...}`` with the timer lateness (how late each tick
  fired: p50 / p99 / max, ticks later than 33 and 100 ms), then quit.

The output line is the only stdout line starting with ``ADV_TG_BENCH``; with
``ADV_TG_BENCH_OUT=<file>`` it is (also) appended to that file, for windowed builds that have
no stdout (the Windows ``.exe``).
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

ENV = "ADV_TG_BENCH"
ENV_OUT = "ADV_TG_BENCH_OUT"
TICK_MS = 16.0
PREFIX = "ADV_TG_BENCH "


def requested() -> str | None:
    v = os.environ.get(ENV, "").strip()
    return v or None


def _emit(payload: dict[str, Any]) -> None:
    line = PREFIX + json.dumps(payload)
    out = os.environ.get(ENV_OUT, "").strip()
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    if sys.stdout is not None:  # None in windowed (no console) builds
        print(line, flush=True)


def _percentile(v: list[float], q: float) -> float:
    if not v:
        return float("nan")
    s = sorted(v)
    i = min(len(s) - 1, max(0, round(q / 100.0 * (len(s) - 1))))
    return s[i]


def install(app: Any, window: Any, t_main: float) -> None:  # noqa: ANN401 (Qt objects)
    """Arm the requested measurement; ``t_main`` is ``time.perf_counter()`` at ``main()``."""
    from PyQt6.QtCore import QTimer

    mode = requested()
    if mode is None:
        return
    if mode == "startup":

        def shown() -> None:
            _emit({"mode": "startup", "window_shown_ms": (time.perf_counter() - t_main) * 1e3})
            app.quit()

        QTimer.singleShot(0, shown)
        return

    seconds = float(mode)
    late: list[float] = []
    state = {"last": 0.0}
    timer = QTimer(window)
    timer.setInterval(int(TICK_MS))

    def tick() -> None:
        now = time.perf_counter()
        if state["last"]:
            late.append(max(0.0, (now - state["last"]) * 1e3 - TICK_MS))
        state["last"] = now

    def finish() -> None:
        timer.stop()
        _emit(
            {
                "mode": "live",
                "seconds": seconds,
                "ticks": len(late),
                "late_p50_ms": _percentile(late, 50),
                "late_p99_ms": _percentile(late, 99),
                "late_max_ms": max(late) if late else float("nan"),
                "late_over_33ms": sum(1 for v in late if v > 33.0),
                "late_over_100ms": sum(1 for v in late if v > 100.0),
            }
        )
        try:
            window.stop_capture()
        except Exception:  # noqa: BLE001 - measuring, not testing the stop path
            pass
        app.quit()

    def start() -> None:
        window.start_capture()
        timer.timeout.connect(tick)
        timer.start()
        QTimer.singleShot(int(seconds * 1000), finish)

    QTimer.singleShot(500, start)
