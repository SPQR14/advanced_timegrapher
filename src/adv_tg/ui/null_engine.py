"""Placeholder engine for hardware input devices (the real one arrives in layer 5)."""

from __future__ import annotations

from adv_tg.ui.stream_worker_base import BaseStreamWorker
from adv_tg.utils.debugger import Debugger

_dbg = Debugger.get(__name__)
logger = _dbg.logger

NOT_IMPLEMENTED_MESSAGE: str = "Motor de medición aún no implementado"


class NullStreamWorker(BaseStreamWorker):
    """Engine that processes no audio.

    ``start_stream()`` emits ``error_occurred`` with ``NOT_IMPLEMENTED_MESSAGE`` and then
    ``session_finished``; ``isRunning()`` is always False. Setters only store values.
    """

    def start_stream(self) -> None:
        logger.warning(
            "NullStreamWorker.start_stream on device %s: %s",
            self._device_id,
            NOT_IMPLEMENTED_MESSAGE,
        )
        self.error_occurred.emit(NOT_IMPLEMENTED_MESSAGE)
        self.session_finished.emit()

    def stop_stream(self) -> None:
        """Nothing to stop."""

    def isRunning(self) -> bool:  # noqa: N802
        return False
