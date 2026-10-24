"""Factory selecting the measurement engine for an input device."""

from __future__ import annotations

from PyQt6.QtCore import QObject

from adv_tg.core.models import WatchConfig
from adv_tg.mock.worker import MockStreamWorker
from adv_tg.ui.capture_worker import CaptureStreamWorker
from adv_tg.ui.null_engine import NullStreamWorker
from adv_tg.ui.stream_worker_base import BaseStreamWorker
from adv_tg.utils.debugger import Debugger

_dbg = Debugger.get(__name__)


def create_worker(
    device_id: int | None,
    config: WatchConfig | None = None,
    *,
    seed: int | None = None,
    parent: QObject | None = None,
) -> BaseStreamWorker:
    """Return the engine for ``device_id``.

    Synthetic source (``None``) -> ``MockStreamWorker``; hardware -> ``CaptureStreamWorker``
    (raw audio, no measurement yet). ``NullStreamWorker`` is only a fallback if the capture
    worker cannot be created.

    TODO(capa-5): return the measuring worker for hardware devices.
    """
    worker: BaseStreamWorker
    if device_id is None:
        worker = MockStreamWorker(config, seed=seed, parent=parent)
    else:
        try:
            worker = CaptureStreamWorker(config, parent=parent)
        except Exception:
            _dbg.logger.exception("CaptureStreamWorker no disponible; se usa NullStreamWorker")
            worker = NullStreamWorker(config, parent=parent)
    worker.set_input_device(device_id)
    return worker
