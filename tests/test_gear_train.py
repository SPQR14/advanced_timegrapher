"""Gear-train analysis (``adv_tg.analysis.gear_train``) and the "Analyze Recording" window.

Truth: ``synth_watch`` with ``phase_mod`` (timing ripple, µs) and ``level_mod`` (loudness)
at the period of a wheel. Tolerances: amplitude within 15 % (30 % for the fourth wheel: a
200 s take holds only 3.3 turns, part of the sinusoid goes into the slow-drift fit), period
within one frequency resolution of the take.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from adv_tg.analysis.gear_train import (
    FOURTH_WHEEL_PERIOD_S,
    NotEnoughBeats,
    analyze_recording,
    escape_wheel_period_s,
    extract_series,
    lomb_scargle,
    spectral_report,
    teager_kaiser,
)
from tests.synth import synth_watch

FS = 48_000


def _by(report, name: str, series: str):
    return next(c for c in report.components if c.name == name and c.series == series)


@pytest.mark.parametrize(
    ("bph", "teeth", "period"),
    [(28800, 15, 3.75), (21600, 15, 5.0), (18000, 15, 6.0), (36000, 15, 3.0), (14400, 15, 7.5),
     (28800, 20, 5.0)],
)  # fmt: skip
def test_escape_wheel_period(bph: int, teeth: int, period: float) -> None:
    assert escape_wheel_period_s(bph, teeth) == pytest.approx(period)
    with pytest.raises(ValueError):
        escape_wheel_period_s(0)


def test_teager_kaiser_of_a_sinusoid() -> None:
    # Psi of A sin(w n) is A^2 sin^2(w): constant
    n = np.arange(1000)
    x = 0.5 * np.sin(0.3 * n)
    psi = teager_kaiser(x)
    assert psi.shape == x.shape
    np.testing.assert_allclose(psi, 0.25 * math.sin(0.3) ** 2, rtol=1e-9)
    assert teager_kaiser(np.zeros(2)).tolist() == [0.0, 0.0]


def test_lomb_scargle_finds_a_sinusoid_on_irregular_times() -> None:
    rng = np.random.default_rng(3)
    t = np.sort(rng.uniform(0, 100, 600))
    y = 2.0 * np.sin(2 * np.pi * t / 3.75) + rng.normal(0, 1.0, t.size)
    f = np.linspace(0.01, 1.0, 3000)
    p = lomb_scargle(t, y, f)
    assert 1.0 / f[np.argmax(p)] == pytest.approx(3.75, rel=0.01)
    assert lomb_scargle(t, np.ones_like(t), f).max() == 0.0  # no variance, no power


def test_escape_and_fourth_wheel_are_found() -> None:
    x, _ = synth_watch(bph=28800, rate_s_per_day=4.0, beat_error_ms=0.4, seconds=200, seed=7,
                       snr_db=30, jitter_us=20, phase_mod=((3.75, 40.0), (60.0, 150.0)),
                       level_mod=((3.75, 0.15),))  # fmt: skip
    r = analyze_recording(x, FS, 28800)
    assert r.warnings == ()
    assert r.series.rate_s_per_day == pytest.approx(4.0, abs=0.1)
    esc = _by(r, "escape_wheel", "timing")
    assert esc.significant and esc.period_s == pytest.approx(3.75, abs=0.02)
    assert esc.amplitude == pytest.approx(40e-6, rel=0.15)
    # 40 µs at 3.75 s is a rate ripple of 2 pi A / T x 86400 = 5.79 s/d
    assert esc.rate_ripple_s_per_day == pytest.approx(5.79, rel=0.15)
    esc_e = _by(r, "escape_wheel", "energy")
    # TKEO energy ~ amplitude^2: a 15 % loudness modulation is ~30 % in energy
    assert esc_e.significant and esc_e.amplitude == pytest.approx(0.30, rel=0.2)
    fourth = _by(r, "fourth_wheel", "timing")
    assert fourth.significant and fourth.amplitude == pytest.approx(150e-6, rel=0.3)
    assert fourth.period_s == pytest.approx(FOURTH_WHEEL_PERIOD_S, rel=0.1)
    assert not _by(r, "fourth_wheel", "energy").significant  # not injected


def test_clean_watch_has_no_component_and_short_take_warns() -> None:
    x, _ = synth_watch(bph=21600, rate_s_per_day=-2.0, seconds=60, seed=3, snr_db=30,
                       jitter_us=20)  # fmt: skip
    r = analyze_recording(x, FS, 21600)
    assert not any(c.significant for c in r.components)
    assert "short_for_fourth" in r.warnings and "short_for_escape" not in r.warnings
    assert not _by(r, "fourth_wheel", "timing").resolvable
    assert r.escape_period_s == pytest.approx(5.0)


def test_teeth_only_recompute_the_spectra() -> None:
    x, _ = synth_watch(bph=21600, seconds=60, seed=5, snr_db=30, jitter_us=20,
                       phase_mod=((5.0, 50.0),))  # fmt: skip
    series = extract_series(x, FS, 21600)
    assert _by(spectral_report(series, 15), "escape_wheel", "timing").significant
    r20 = spectral_report(series, 20)  # 6.67 s: nothing there
    assert r20.escape_period_s == pytest.approx(20 / 3)
    assert not _by(r20, "escape_wheel", "timing").significant


def test_silence_raises_not_enough_beats() -> None:
    with pytest.raises(NotEnoughBeats) as info:
        extract_series(np.zeros(10 * FS), FS, 28800)
    assert info.value.args[1] == 0


# ---------------------------------------------------------------------- GUI
def test_analysis_dialog(qtbot, tmp_path) -> None:
    pytest.importorskip("PyQt6")
    from adv_tg.i18n.translator import Translator
    from adv_tg.ui.analysis_dialog import AnalysisDialog, RecordingSource
    from adv_tg.utils.audio_files import write_audio

    x, _ = synth_watch(bph=21600, seconds=60, seed=5, snr_db=30, jitter_us=20,
                       phase_mod=((5.0, 50.0),))  # fmt: skip
    path = write_audio(tmp_path / "w.flac", x / np.max(np.abs(x)) * 0.5, FS,
                       tags={"ADV_TG_BPH": "21600", "ADV_TG_LIFT_ANGLE": "51"})  # fmt: skip
    dlg = AnalysisDialog(Translator("es"), default_bph=28800)
    qtbot.addWidget(dlg)
    assert dlg.open_file(path, blocking=True)
    assert dlg.source.bph == 21600 and dlg.source.lift_angle_deg == 51  # from the tags
    assert dlg.report is not None and dlg.table.rowCount() == 4
    cells = [dlg.table.item(0, j).text() for j in range(8)]
    assert cells[0] == "Rueda de escape" and cells[-1] == "Presente"
    assert dlg.table.item(2, 7).text().startswith("Graba ≥")  # fourth wheel: 60 s take
    dlg.spn_teeth.setValue(20)  # recompute without detecting again
    assert dlg.report.escape_teeth == 20 and dlg.table.item(0, 7).text() == "No detectada"

    quartz = RecordingSource(np.zeros(FS), FS, 3600, quartz=True, name="q")
    dlg.analyze(quartz, blocking=True)
    assert dlg.report is None and dlg.lbl_status.text() == dlg._tr("an_quartz")

    dlg.analyze(RecordingSource(np.zeros(10 * FS), FS, 28800, name="s"), blocking=True)
    assert dlg.lbl_status.text() == dlg._tr("an_too_few_beats").format(found=0)
