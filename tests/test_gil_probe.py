"""gil_probe bench: fake-source run in a subprocess and table rendering (no hardware)."""

from __future__ import annotations

import pytest

from adv_tg.tools import gil_probe


def test_fake_source_scenarios_produce_results() -> None:
    args = gil_probe._build_parser().parse_args(["--device", "1", "--secs", "1.5", "--fake-source"])
    rows = [gil_probe.run_scenario(args, sc, 1024, "low", None) for sc in ("main", "thread")]
    for r in rows:
        assert r["ok"], r.get("error")
        assert r["fake_source"] is True
        assert r["callbacks"] > 30
        assert r["ingest_pct"] > 90.0
        assert sum(r["gil_hist"]) == r["callbacks"]
    table = gil_probe.markdown_table(rows)
    assert table.count("\n") == 3 and "| main |" in table and "| thread |" in table


def test_isolated_fake_source_scenario() -> None:
    args = gil_probe._build_parser().parse_args(
        ["--device", "1", "--secs", "1.5", "--fake-source", "--isolated"]
    )
    r = gil_probe.run_scenario(args, "main", 1024, "low", None)
    assert r["ok"], r.get("error")
    assert r["isolated"] is True
    assert r["dropped_frames"] == 0
    assert r["parent_blocks"] == r["child_frames_sent"] == r["callbacks"]
    assert r["ingest_pct"] > 95.0
    assert "| aislado |" in gil_probe.markdown_table([r])


def test_markdown_table_marks_errors_and_silence() -> None:
    ok = {
        "scenario": "main",
        "ok": True,
        "callbacks": 10,
        "expected_blocks": 10.0,
        "ingest_pct": 100.0,
        "received_pct": 99.0,
        "max_gap_ms": 21.0,
        "gil_mean_ms": 0.1,
        "gil_p99_ms": 0.5,
        "gil_max_ms": 0.8,
        "gil_over_block": 0,
        "cpu_pct": 1.0,
        "threads": 5,
        "peak_dbfs": float("-inf"),
    }
    bad = {"scenario": "gui_real", "ok": False, "error": "boom"}
    table = gil_probe.markdown_table([ok, bad])
    assert "SILENCIO" in table
    assert "ERROR: boom" in table


@pytest.mark.parametrize("argv", [["--secs", "0"], [], ["--scenario", "nope"]])
def test_bad_arguments(argv: list[str]) -> None:
    assert gil_probe.main(argv) == 2
