"""CLI tools of layer 2: ``detect_beats`` and ``check_samples``."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from adv_tg.tools import check_samples, detect_beats
from adv_tg.utils.audio_files import write_audio
from tests.synth import synth_watch

FS = 48_000


@pytest.fixture(scope="module")
def weak_p1_files(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    d = tmp_path_factory.mktemp("weak")
    x, _ = synth_watch(
        bph=21600, lift_angle_deg=50, p1_gain=0.1, snr_db=26, beat_error_ms=0.6, seconds=12, seed=2
    )
    return write_audio(d / "weak.wav", x, FS), write_audio(d / "weak.flac", x, FS)


@pytest.mark.parametrize("which", [0, 1], ids=["wav", "flac"])
def test_detect_beats_cli(
    weak_p1_files: tuple[Path, Path], which: int, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = weak_p1_files[which]
    csv_p, json_p = tmp_path / "b.csv", tmp_path / "s.json"
    code = detect_beats.main(
        [
            str(path),
            "--bph",
            "21600",
            "--lift-angle",
            "50",
            "--csv",
            str(csv_p),
            "--json",
            str(json_p),
        ]
    )
    out = capsys.readouterr().out
    assert code == detect_beats.EXIT_OK, out
    assert "Encontrados" in out and "Perdidos (k)" in out and "Tiempo real" in out
    summary = json.loads(json_p.read_text(encoding="utf-8"))
    assert summary["found"] == 72 and summary["missed"] == 0
    assert summary["p3_pct"] == 100.0 and summary["p1_pct"] < 10.0
    assert summary["lock_time_s"] <= 3.0
    with csv_p.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 72 and "P1_MISSING" in rows[10]["flag_names"]


def test_detect_beats_plot_is_optional(
    weak_p1_files: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    png = tmp_path / "p.png"
    code = detect_beats.main([str(weak_p1_files[0]), "--bph", "21600", "--plot", str(png)])
    out = capsys.readouterr().out
    assert code == detect_beats.EXIT_OK
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        assert "matplotlib no está instalado" in out and not png.exists()
    else:
        assert png.exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["no/existe.wav", "--bph", "28800"],
        ["x.wav"],
        ["x.wav", "--bph", "28800", "--chunk", "0"],
        ["x.wav", "--bph", "28800", "--lift-angle", "120"],
    ],
)
def test_detect_beats_input_errors(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert detect_beats.main(argv) == detect_beats.EXIT_ERROR


def test_check_samples_self_test(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out_md = tmp_path / "report.md"
    code = check_samples.main(["--self-test", "--out", str(out_md)])
    out = capsys.readouterr().out
    assert code == check_samples.EXIT_OK, out
    report = out_md.read_text(encoding="utf-8")
    rows = {
        line.split("|")[1].strip(): line
        for line in report.splitlines()
        if line.startswith("| selftest_")
    }
    assert set(rows) == {
        "selftest_miyota_9015.wav",
        "selftest_hamilton_c07_h50.flac",
        "selftest_bulova_nm10.flac",
    }
    assert "**OK**" in rows["selftest_miyota_9015.wav"]
    assert "**OK**" in rows["selftest_hamilton_c07_h50.flac"]
    assert "t_lift n/d" in rows["selftest_hamilton_c07_h50.flac"]  # weak P1, like the C07
    assert "**INFO**" in rows["selftest_bulova_nm10.flac"]
    assert "| sí |" in rows["selftest_miyota_9015.wav"]  # tic/tac and invariance


def test_check_samples_table_manifest_and_unconfigured(tmp_path: Path) -> None:
    d = tmp_path / "samples"
    (d / "sub").mkdir(parents=True)
    for name in ("A_C07.WAV", "sub/miyota.flac", "other.wav", "notes.txt"):
        (d / name).write_bytes(b"")
    assert [p.name for p in check_samples.discover(d)] == ["A_C07.WAV", "other.wav", "miyota.flac"]
    assert check_samples.resolve_config(d / "A_C07.WAV").bph == 21600
    assert check_samples.resolve_config(d / "sub" / "miyota.flac").lift_angle == 51.0
    assert check_samples.resolve_config(d / "other.wav") is None
    manifest = [
        {"pattern": "oth*", "bph": 18000, "lift_angle": 49},
        {"file": "A_C07.WAV", "bph": 19800},
    ]
    assert check_samples.resolve_config(d / "other.wav", manifest).bph == 18000
    assert check_samples.resolve_config(d / "A_C07.WAV", manifest).bph == 19800


def test_check_samples_input_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert check_samples.main(["--dir", str(tmp_path / "missing")]) == check_samples.EXIT_ERROR
    bad = tmp_path / "m.json"
    bad.write_text('{"not": "a list"}', encoding="utf-8")
    assert check_samples.main(["--manifest", str(bad), "--dir", str(tmp_path)]) == 2


def test_check_samples_lists_unconfigured_without_failing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    x, _ = synth_watch(seconds=3, seed=1)
    write_audio(tmp_path / "unknown_watch.wav", x, FS)
    code = check_samples.main(["--dir", str(tmp_path), "--out", str(tmp_path / "r.md")])
    assert code == check_samples.EXIT_OK
    assert "unknown_watch.wav" in (tmp_path / "r.md").read_text(encoding="utf-8")
