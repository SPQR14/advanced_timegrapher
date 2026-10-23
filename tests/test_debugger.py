"""Tests for the Debugger facade (uses caplog, no Qt)."""

from __future__ import annotations

import logging

import pytest

from adv_tg.utils.debugger import Debugger

NAME = "adv_tg.tests.debugger"
# Configure logging during collection so it does not replace caplog's handler later.
DBG = Debugger.get(NAME)


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == NAME]


def test_get_is_cached() -> None:
    assert Debugger.get(NAME) is DBG


def test_phase_logs_start_and_end(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    with DBG.phase("ok_phase", item=1):
        pass
    msgs = _messages(caplog)
    assert any("▶ INICIO [ok_phase]" in m and "item=1" in m for m in msgs)
    assert any("■ FIN [ok_phase]" in m and "ms" in m for m in msgs)


def test_phase_reraises_and_logs_exception(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ValueError, match="boom"):
        with DBG.phase("bad_phase"):
            raise ValueError("boom")
    errors = [r for r in caplog.records if r.name == NAME and r.levelno == logging.ERROR]
    assert errors and "bad_phase" in errors[0].getMessage()
    assert errors[0].exc_info is not None


def test_state_change(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    DBG.state_change("capture", "INACTIVO", "MIDIENDO", device=3)
    assert any("ESTADO capture: INACTIVO -> MIDIENDO" in m for m in _messages(caplog))


def test_file_read_and_write(caplog: pytest.LogCaptureFixture, tmp_path) -> None:
    caplog.set_level(logging.DEBUG)
    f = tmp_path / "data.bin"
    f.write_bytes(b"12345")
    DBG.file_read(f)
    DBG.file_write(f, ok=False, error="disk full")
    recs = [r for r in caplog.records if r.name == NAME]
    assert recs[0].levelno == logging.INFO and "5 B" in recs[0].getMessage()
    assert recs[1].levelno == logging.ERROR and "disk full" in recs[1].getMessage()


def test_process_start_end(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    DBG.process_start("worker", seed=1)
    DBG.process_end("worker")
    msgs = _messages(caplog)
    assert any("PROCESO INICIO worker" in m for m in msgs)
    assert any("PROCESO FIN worker" in m for m in msgs)


def test_traced(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    @DBG.traced
    def add(a: int, b: int) -> int:
        return a + b

    assert add(2, 3) == 5
    assert add.__name__ == "add"
    msgs = _messages(caplog)
    assert any(m.startswith("→ ") and "add" in m for m in msgs)
    assert any(m.startswith("← ") and "add" in m for m in msgs)


def test_throttled_limits_repetitions(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    emitted = [DBG.throttled("k1", "tick %d", i, every_s=60.0) for i in range(5)]
    assert emitted == [True, False, False, False, False]
    assert len([m for m in _messages(caplog) if m.startswith("tick")]) == 1
    assert DBG.throttled("k1", "tick again", every_s=0.0)
    assert any("(+4 suprimidos)" in m for m in _messages(caplog))
