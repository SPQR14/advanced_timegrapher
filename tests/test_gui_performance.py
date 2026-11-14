"""Display-side performance helpers: pixmap cache, restyling and smooth waveform scrolling."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtGui import QPixmapCache  # noqa: E402

from adv_tg.ui import capture_worker as cw  # noqa: E402
from adv_tg.ui import main_window as mw  # noqa: E402
from adv_tg.ui.capture_worker import _Playout  # noqa: E402

FS = 48_000


def test_pixmap_cache_has_room_for_high_dpi(qtbot) -> None:
    """Qt's default (10 MB) overflowed on scaled screens and the plot axes were redrawn on
    every telemetry update. The limit is never lowered."""
    QPixmapCache.setCacheLimit(10 * 1024)
    assert mw.configure_pixmap_cache() >= mw.PIXMAP_CACHE_MIN_KB
    assert QPixmapCache.cacheLimit() <= mw.PIXMAP_CACHE_MAX_KB
    QPixmapCache.setCacheLimit(mw.PIXMAP_CACHE_MAX_KB * 2)
    assert mw.configure_pixmap_cache() == mw.PIXMAP_CACHE_MAX_KB * 2
    QPixmapCache.setCacheLimit(mw.PIXMAP_CACHE_MIN_KB)


def test_quality_bar_is_restyled_only_when_its_class_changes(qtbot, monkeypatch) -> None:
    window = mw.MainWin()
    qtbot.addWidget(window)
    calls: list[str] = []
    monkeypatch.setattr(window.prg_snr, "setStyleSheet", calls.append)
    for pct in (40, 41, 42, 43):
        window._set_quality_bar(pct, "ok", f"{pct}")
    assert len(calls) == 1 and window.prg_snr.value() == 43
    window._set_quality_bar(20, "bad", "20")
    window._set_quality_bar(20, "bad", "20")
    window._set_quality_bar(50, None, "50")
    assert len(calls) == 3 and calls[-1] == ""
    assert window.lbl_snr_value.text() == "50"
    # the secondary line of a card: no layout work when nothing changes
    card = window.card_beat_error
    card.set_sub("P3: 0.20")
    assert not card.sub_label.isHidden() and card.sub_label.text() == "P3: 0.20"
    card.set_sub("")
    assert card.sub_label.isHidden()
    window.close()


def _arrivals(rng, block: int, burst: int, jitter_s: float, secs: float) -> np.ndarray:
    """``(time, total samples)`` of a producer that delivers ``burst`` blocks at once, each
    delivery a little late on its schedule (the device clock itself is exact)."""
    rows, total = [], 0
    step = burst * block
    while total / FS < secs:
        total += step
        rows.append((total / FS + abs(rng.normal(0.0, jitter_s)), total))
    return np.asarray(rows)


@pytest.mark.parametrize(
    ("block", "burst", "jitter_s"),
    [(1024, 1, 0.0005), (480, 1, 0.001), (1024, 4, 0.003), (1024, 8, 0.005)],
    ids=["alsa-1024", "wasapi-480", "rafagas-x4", "mme-x8"],
)
def test_playout_scrolls_evenly_behind_a_bursty_producer(block, burst, jitter_s) -> None:
    """The plot showed the newest samples: with blocks of 21 ms and frames of 33 ms the trace
    advanced 1 block, then 2... The playout position advances with the clock instead."""
    rng = np.random.default_rng(3)
    arr = _arrivals(rng, block, burst, jitter_s, 20.0)
    po = _Playout(FS)
    t, rows = 0.5, []
    while t < 19.0:
        t += 0.033 + abs(rng.normal(0.0, 0.002))
        total = int(arr[np.searchsorted(arr[:, 0], t, side="right") - 1, 1])
        pos = po.advance(t, total)
        assert pos <= total
        rows.append((t, pos, total))
    r = np.asarray(rows)[240:]  # after the loop settled (8 s)
    dt_ms = np.diff(r[:, 0]) * 1e3
    new = np.diff(r[:, 1]) / FS * 1e3 - dt_ms  # advance of the trace minus elapsed time
    old = np.diff(r[:, 2]) / FS * 1e3 - dt_ms
    assert np.all(np.diff(r[:, 1]) >= 0)
    assert np.std(new) < 1.5 and np.std(new) < 0.3 * np.std(old), (np.std(new), np.std(old))
    # it stays about one burst behind the newest sample, never far
    lag_ms = float(np.mean(r[:, 2] - r[:, 1])) / FS * 1e3
    assert lag_ms < 1.5 * burst * block / FS * 1e3 + 3.0 * cw.PLAYOUT_SAFETY_S * 1e3


def test_playout_recovers_from_a_stall_and_from_a_backlog() -> None:
    po = _Playout(FS)
    start = po.advance(0.0, 48_000)
    assert start == 48_000 - int(cw.PLAYOUT_START_S * FS)
    # the producer stalls: the position stops at the newest sample (never past it)
    for i in range(1, 6):
        pos = po.advance(0.1 * i, 48_000)
        assert start <= pos <= 48_000
    assert 48_000 - pos <= 2 * cw.PLAYOUT_SAFETY_S * FS
    # a long hiccup delivers 2 s at once: re-seated near the newest sample, not 2 s behind
    pos = po.advance(0.6, 48_000 + 96_000)
    assert 0 <= (48_000 + 96_000) - pos <= cw.PLAYOUT_RESYNC_S * FS


def test_waveform_x_range_is_fixed_to_the_window(qtbot) -> None:
    """With X auto-range the view box repainted the whole plot (grid and axes) on every
    frame, even when the range it computed was the same."""
    window = mw.MainWin()
    qtbot.addWidget(window)
    t = np.linspace(0.0, 2000.0, 600)
    window._on_waveform_ready(t, np.zeros_like(t))
    view_box = window.waveform_plot.getViewBox()
    assert not view_box.autoRangeEnabled()[0]
    (lo, hi), _ = view_box.viewRange()
    assert lo <= 0.0 and hi >= 2000.0 and (hi - lo) < 2000.0 * 1.2
    window._on_waveform_ready(t[:300], np.zeros(300))  # a shorter window: followed
    (lo, hi), _ = view_box.viewRange()
    assert hi < 1100.0
    window.close()
