"""Multi-threaded CPU 2D plot rasterizer bridge (``NativeGraphRenderer``) for PyQtGraph.

Experimental and OFF by default: set ``ADV_TG_NATIVE_PLOTS=1`` to use it. The frames are
rasterized at the logical size of the plot and drawn by Qt with nearest-neighbour scaling, so
with display scaling (HiDPI, fractional factors) the traces come out stair-stepped, and a frame
slightly smaller than the target loses pixel columns of the 1-px lines: the oscilloscope trace
of v2.0.0 was broken into dangling segments. PyQtGraph draws these plots (a few thousand points)
well within the frame budget, so it stays the default. ``smooth_median_array`` (numeric, not
drawing) keeps using the native engine.
"""

from __future__ import annotations

import ctypes
import os
import weakref
from typing import Any

import numpy as np
import pyqtgraph as pg
from numpy.typing import NDArray
from PyQt6.QtCore import QRectF, QTimer
from PyQt6.QtGui import QImage, QPainter

from adv_tg import native_bridge as _nb

__all__ = [
    "ENV_NATIVE_PLOTS",
    "NativeGraphEngine",
    "NativePlotOverlay",
    "native_plots_enabled",
    "smooth_median_array",
]

ENV_NATIVE_PLOTS = "ADV_TG_NATIVE_PLOTS"


def smooth_median_array(raw: list[float] | NDArray[np.float64], window: int) -> list[float]:
    """Running causal median over ``raw``; uses the C++20 sorting network when loaded."""
    n = len(raw)
    if n == 0:
        return []
    if _nb.have("advtg_smooth_median_f64"):
        rc = _nb.f64(raw)
        out = np.empty(n, dtype=np.float64)
        _nb.lib().advtg_smooth_median_f64(_nb.ptr(rc), n, int(window), _nb.ptr(out))
        return out.tolist()
    return [float(np.median(raw[max(0, i - window + 1) : i + 1])) for i in range(n)]


def native_plots_enabled() -> bool:
    """The experimental native rasterizer was requested (``ADV_TG_NATIVE_PLOTS=1``)."""
    return os.environ.get(ENV_NATIVE_PLOTS, "").strip().lower() in ("1", "true", "yes", "on")


class NativePlotOverlay(pg.GraphicsObject):
    """Lightweight GraphicsObject displaying pre-rendered C++ ARGB32 frames."""

    def __init__(self, engine: NativeGraphEngine, slot: int, plot_widget: pg.PlotWidget) -> None:
        super().__init__()
        self._engine: NativeGraphEngine | None = engine
        self._slot = int(slot)
        self._plot_ref = weakref.ref(plot_widget)
        self._last_seq = 0
        self._closed = False
        self._img: QImage | None = None
        self._bounds = QRectF(0.0, -1.0, 1.0, 2.0)
        self.setZValue(5.0)
        self.setFlag(pg.GraphicsObject.GraphicsItemFlag.ItemUsesExtendedStyleOption, False)

    def boundingRect(self) -> QRectF:  # noqa: N802 (Qt override)
        if self._closed:
            return self._bounds
        plot = self._plot_ref()
        if plot is not None:
            try:
                (x0, x1), (y0, y1) = plot.getViewBox().viewRange()
                if x1 > x0 and y1 > y0:
                    self._bounds = QRectF(x0, y0, x1 - x0, y1 - y0)
            except Exception:
                pass
        return self._bounds

    def check_new_frame(self) -> bool:
        if self._closed or self._engine is None:
            return False
        frame = self._engine.acquire_frame(self._slot)
        if frame is None:
            return False
        seq = frame[8]
        if seq != self._last_seq:
            self._last_seq = seq
            try:
                self.update()
            except RuntimeError:
                self._closed = True
            return True
        return False

    def paint(self, painter: QPainter, option: Any, widget: Any = None) -> None:  # noqa: ANN401
        if self._closed or self._engine is None:
            return
        frame = self._engine.acquire_frame(self._slot)
        if frame is None:
            return
        ptr, w, h, stride, x0, x1, y0, y1, seq = frame
        self._last_seq = seq
        if not ptr or w <= 0 or h <= 0 or x1 <= x0 or y1 <= y0:
            return
        buf_t = ctypes.c_uint8 * (stride * h)
        arr_view = buf_t.from_address(ptr)
        img = QImage(arr_view, w, h, stride, QImage.Format.Format_ARGB32_Premultiplied)
        dev_rect = painter.transform().mapRect(QRectF(x0, y0, x1 - x0, y1 - y0))
        if dev_rect.isEmpty():
            return
        painter.save()
        try:
            painter.resetTransform()
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            painter.drawImage(dev_rect, img)
        finally:
            painter.restore()
        self._img = img  # keep Python buffer reference alive across the paint scope


class NativeGraphEngine:
    """Owns one ``NativeGraphRenderer`` (2 C++ threads) and 3 plot overlays."""

    def __init__(self, parent: pg.PlotWidget | None = None) -> None:
        self._handle: ctypes.c_void_p | None = None
        if native_plots_enabled() and _nb.have(
            "advtg_gr_create",
            "advtg_gr_destroy",
            "advtg_gr_submit_tape",
            "advtg_gr_submit_osc",
            "advtg_gr_submit_wave",
            "advtg_gr_clear_slot",
            "advtg_gr_flush",
            "advtg_gr_acquire_frame",
        ):
            h = _nb.lib().advtg_gr_create()
            if h:
                self._handle = ctypes.c_void_p(h)
        self._overlays: dict[int, NativePlotOverlay] = {}
        self._timer: QTimer | None = None
        if self._handle is not None:
            self._timer = QTimer(parent)
            self._timer.setInterval(16)
            self._timer.timeout.connect(self._poll)

    @property
    def active(self) -> bool:
        return self._handle is not None

    def attach(self, slot: int, plot_widget: pg.PlotWidget) -> NativePlotOverlay | None:
        if self._handle is None:
            return None
        overlay = NativePlotOverlay(self, slot, plot_widget)
        plot_widget.addItem(overlay, ignoreBounds=True)
        self._overlays[slot] = overlay
        return overlay

    @staticmethod
    def _plot_dims_and_range(
        plot_widget: pg.PlotWidget,
    ) -> tuple[int, int, float, float, float, float]:
        vb = plot_widget.getViewBox()
        (x0, x1), (y0, y1) = vb.viewRange()
        geom = vb.screenGeometry()
        if geom is not None and geom.width() > 8 and geom.height() > 8:
            w, h = int(geom.width()), int(geom.height())
        else:
            w = max(int(plot_widget.width()), 320)
            h = max(int(plot_widget.height()), 180)
        return (
            max(32, min(w, 4096)),
            max(32, min(h, 2160)),
            float(x0),
            float(x1),
            float(y0),
            float(y1),
        )

    def _ensure_timer(self) -> None:
        if self._timer is not None and not self._timer.isActive():
            self._timer.start()

    def submit_tape(
        self,
        plot_widget: pg.PlotWidget,
        xe: NDArray[np.float64],
        ye: NDArray[np.float64],
        xo: NDArray[np.float64],
        yo: NDArray[np.float64],
    ) -> None:
        if self._handle is None:
            return
        w, h, x0, x1, y0, y1 = self._plot_dims_and_range(plot_widget)
        xec, yec = _nb.f64(xe), _nb.f64(ye)
        xoc, yoc = _nb.f64(xo), _nb.f64(yo)
        ne = min(xec.size, yec.size)
        no = min(xoc.size, yoc.size)
        _nb.lib().advtg_gr_submit_tape(
            self._handle,
            w,
            h,
            x0,
            x1,
            y0,
            y1,
            _nb.ptr(xec) if ne else 0,
            _nb.ptr(yec) if ne else 0,
            ne,
            _nb.ptr(xoc) if no else 0,
            _nb.ptr(yoc) if no else 0,
            no,
        )
        self._ensure_timer()

    def submit_osc(
        self,
        plot_widget: pg.PlotWidget,
        t: NDArray[np.float64],
        wave: NDArray[np.float64],
        env: NDArray[np.float64] | None,
    ) -> None:
        if self._handle is None:
            return
        w, h, x0, x1, y0, y1 = self._plot_dims_and_range(plot_widget)
        tc, wc = _nb.f64(t), _nb.f64(wave)
        n = min(tc.size, wc.size)
        ec = _nb.f64(env) if env is not None and len(env) == n else None
        _nb.lib().advtg_gr_submit_osc(
            self._handle,
            w,
            h,
            x0,
            x1,
            y0,
            y1,
            _nb.ptr(tc) if n else 0,
            _nb.ptr(wc) if n else 0,
            _nb.ptr(ec) if ec is not None and n else 0,
            n,
        )
        self._ensure_timer()

    def submit_wave(
        self, plot_widget: pg.PlotWidget, t: NDArray[np.float64], wave: NDArray[np.float64]
    ) -> None:
        if self._handle is None:
            return
        w, h, x0, x1, y0, y1 = self._plot_dims_and_range(plot_widget)
        tc, wc = _nb.f64(t), _nb.f64(wave)
        n = min(tc.size, wc.size)
        _nb.lib().advtg_gr_submit_wave(
            self._handle,
            w,
            h,
            x0,
            x1,
            y0,
            y1,
            _nb.ptr(tc) if n else 0,
            _nb.ptr(wc) if n else 0,
            n,
        )
        self._ensure_timer()

    def clear_slot(self, slot: int) -> None:
        if self._handle is None:
            return
        _nb.lib().advtg_gr_clear_slot(self._handle, int(slot))
        self._ensure_timer()

    def flush(self) -> None:
        if self._handle is None:
            return
        _nb.lib().advtg_gr_flush(self._handle)
        self._poll()

    def _poll(self) -> None:
        for ov in self._overlays.values():
            ov.check_new_frame()

    def acquire_frame(
        self, slot: int
    ) -> tuple[int, int, int, int, float, float, float, float, int] | None:
        if self._handle is None:
            return None
        w = ctypes.c_int(0)
        h = ctypes.c_int(0)
        stride = ctypes.c_int(0)
        x0 = ctypes.c_double(0.0)
        x1 = ctypes.c_double(0.0)
        y0 = ctypes.c_double(0.0)
        y1 = ctypes.c_double(0.0)
        seq = ctypes.c_uint64(0)
        ptr = _nb.lib().advtg_gr_acquire_frame(
            self._handle,
            int(slot),
            ctypes.byref(w),
            ctypes.byref(h),
            ctypes.byref(stride),
            ctypes.byref(x0),
            ctypes.byref(x1),
            ctypes.byref(y0),
            ctypes.byref(y1),
            ctypes.byref(seq),
        )
        if not ptr or seq.value == 0:
            return None
        return (
            int(ptr),
            int(w.value),
            int(h.value),
            int(stride.value),
            float(x0.value),
            float(x1.value),
            float(y0.value),
            float(y1.value),
            int(seq.value),
        )

    def close(self) -> None:
        timer = self._timer
        self._timer = None
        if timer is not None:
            try:
                timer.stop()
            except RuntimeError:
                pass
        for ov in self._overlays.values():
            ov._closed = True
            ov._engine = None
            ov._img = None
            plot = ov._plot_ref()
            if plot is not None:
                try:
                    plot.removeItem(ov)
                except Exception:
                    pass
        self._overlays.clear()
        h = self._handle
        self._handle = None
        if h is not None:
            try:
                cdll = _nb.lib()
                if cdll is not None:
                    cdll.advtg_gr_destroy(h)
            except Exception:
                pass

    def __del__(self) -> None:
        self.close()
