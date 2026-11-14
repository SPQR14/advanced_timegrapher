"""Runs the update check off the GUI thread and reports the result with a Qt signal."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal

from adv_tg.utils.update_check import ReleaseInfo, UpdateCheckError, check_for_update

__all__ = ["UpdateNotifier"]

logger = logging.getLogger(__name__)


class UpdateNotifier(QObject):
    """One update check at a time, in a daemon thread (the network may take seconds).

    Args:
        check: Function that returns the newer release or None and raises
            ``UpdateCheckError`` (replaced in the tests: no network there).
        parent: Qt parent.

    Signals:
        finished(release, error, manual): ``release`` is a ``ReleaseInfo`` or None (up to
            date), ``error`` the failure message or None, ``manual`` whether the user asked
            for this check. Delivered in the thread of the receiver.
    """

    finished = pyqtSignal(object, object, bool)

    def __init__(
        self,
        check: Callable[[], ReleaseInfo | None] = check_for_update,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._check = check
        self._thread: threading.Thread | None = None

    @property
    def busy(self) -> bool:
        """True while a check is running."""
        return self._thread is not None and self._thread.is_alive()

    def start(self, manual: bool = False) -> bool:
        """Start a check; False when one is already running."""
        if self.busy:
            return False
        self._thread = threading.Thread(
            target=self._run, args=(manual,), name="update-check", daemon=True
        )
        self._thread.start()
        return True

    def _run(self, manual: bool) -> None:
        release: ReleaseInfo | None = None
        error: str | None = None
        try:
            release = self._check()
        except UpdateCheckError as exc:
            error = str(exc)
        except Exception as exc:  # never let the thread die silently
            logger.exception("Fallo inesperado al buscar actualizaciones")
            error = f"{type(exc).__name__}: {exc}"
        try:
            self.finished.emit(release, error, manual)
        except RuntimeError:  # the window was closed meanwhile
            pass
