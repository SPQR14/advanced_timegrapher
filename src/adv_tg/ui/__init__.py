"""User Interface (UI) module for Advanced Timegrapher."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from adv_tg.ui.main_window import MainWin

__all__ = ["MainWin"]


def __getattr__(name: str) -> Any:
    # Lazy import: lets engine modules import ``adv_tg.ui.stream_worker_base`` without
    # pulling in (and cyclically importing) the whole main window.
    if name == "MainWin":
        from adv_tg.ui.main_window import MainWin

        return MainWin
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
