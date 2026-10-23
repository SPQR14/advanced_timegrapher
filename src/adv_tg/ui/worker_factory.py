"""Factory selecting the measurement engine for an input device."""

from __future__ import annotations

from PyQt6.QtCore import QObject

from adv_tg.core.models import WatchConfig
from adv_tg.mock.worker import MockStreamWorker
from adv_tg.ui.null_engine import NullStreamWorker
from adv_tg.ui.stream_worker_base import BaseStreamWorker


def create_worker(
    device_id: int | None,
    config: WatchConfig | None = None,
    *,
    seed: int | None = None,
    parent: QObject | None = None,
) -> BaseStreamWorker:
    """Return ``MockStreamWorker`` for the synthetic source (``None``), else ``NullStreamWorker``.

    TODO(capa-5): return the real worker for hardware devices.
    """
    worker: BaseStreamWorker
    if device_id is None:
        worker = MockStreamWorker(config, seed=seed, parent=parent)
    else:
        worker = NullStreamWorker(config, parent=parent)
    worker.set_input_device(device_id)
    return worker
