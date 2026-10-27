"""CLI of layer 3: ``python -m adv_tg.tools.measure``."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from adv_tg.tools import measure
from adv_tg.utils.audio_files import write_audio
from tests.synth import synth_watch

FS = 48_000


@pytest.fixture(scope="module")
def watch_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("measure")
    x, _ = synth_watch(
        bph=28800, rate_s_per_day=6.0, beat_error_ms=0.5, amplitude_deg=270, seconds=25, seed=8
    )
    return write_audio(d / "reloj.flac", x, FS)


def test_measure_cli_report_json_csv(
    watch_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    js, cs = tmp_path / "m.json", tmp_path / "v.csv"
    code = measure.main(
        [
            str(watch_file),
            "--bph",
            "28800",
            "--lift-angle",
            "52",
            "--json",
            str(js),
            "--csv",
            str(cs),
        ]
    )
    out = capsys.readouterr().out
    assert code == measure.EXIT_OK
    assert "Marcha" in out and "Error de batimiento" in out and "Amplitud" in out
    assert "sin calibrar" in out
    data = json.loads(js.read_text(encoding="utf-8"))
    m = data["metrics"]
    assert m["rate_s_per_day"] == pytest.approx(6.0, abs=0.3)
    assert m["beat_error_ms"] == pytest.approx(0.5, abs=0.05)
    assert m["amplitude_deg"] == pytest.approx(270.0, rel=0.03)
    assert m["beat_error_point"] in ("p1", "p1_beat") and isinstance(m["windows"], list)
    rows = list(csv.DictReader(cs.open(encoding="utf-8")))
    assert len(rows) == len(m["windows"]) >= 2
    assert float(rows[0]["rate_s_per_day"]) == pytest.approx(6.0, abs=1.0)


def test_measure_cli_quartz_and_drift(watch_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = measure.main([str(watch_file), "--bph", "28800", "--quartz", "--adc-drift-ppm", "2"])
    out = capsys.readouterr().out
    assert code == measure.EXIT_OK
    assert "cuarzo" in out and "Error de batimiento" not in out and "calibrada" in out


def test_measure_default_lift_angle_is_indicative(
    watch_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert measure.main([str(watch_file), "--bph", "28800"]) == measure.EXIT_OK
    assert "orientativa" in capsys.readouterr().out


def test_measure_plot_is_optional(
    watch_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    png = tmp_path / "m.png"
    assert measure.main([str(watch_file), "--bph", "28800", "--plot", str(png)]) == measure.EXIT_OK
    out = capsys.readouterr().out
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        assert "matplotlib no está instalado" in out and not png.exists()
    else:
        assert png.exists()


@pytest.mark.parametrize(
    "args",
    [
        ["no-existe.wav", "--bph", "28800"],
        ["{f}", "--bph", "28800", "--chunk", "0"],
        ["{f}"],
        ["{f}", "--bph", "28800", "--window", "-1"],
    ],
    ids=["sin-archivo", "chunk", "sin-bph", "ventana"],
)
def test_measure_cli_errors(
    watch_file: Path, args: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    argv = [a.replace("{f}", str(watch_file)) for a in args]
    assert measure.main(argv) == measure.EXIT_ERROR
    capsys.readouterr()
