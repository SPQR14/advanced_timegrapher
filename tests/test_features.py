"""Feature vectors, positional differentials and rule-based diagnosis (``analysis.features``).

Truth: ``synth_watch`` (sub-event spacing from the lift time: P2 at 0.45 t_lift after P1).
The rules are tested on hand-made feature vectors: their thresholds are provisional, so the
tests pin the logic (which signs make "probable" / "possible"), not the numbers.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace

import numpy as np
import pytest

from adv_tg.analysis import features as ft
from adv_tg.analysis.gear_train import (
    FOURTH_WHEEL_MIN_RECORDING_S,
    extract_series,
    spectral_report,
)
from adv_tg.core.beat_types import lift_time_s
from tests.synth import synth_watch

FS = 48_000


@pytest.fixture(scope="module")
def clean():
    x, _ = synth_watch(bph=28800, seconds=130, seed=3, snr_db=30, jitter_us=20)
    return extract_series(x, FS, 28800)


def test_moments_match_numpy() -> None:
    rng = np.random.default_rng(0)
    v = rng.exponential(1.0, 20000)  # skewness 2, excess kurtosis 6
    mean, var, skew, kurt = ft._moments(v)
    assert mean == pytest.approx(v.mean()) and var == pytest.approx(v.var())
    assert skew == pytest.approx(2.0, abs=0.15) and kurt == pytest.approx(6.0, abs=1.0)
    assert all(math.isnan(m) for m in ft._moments(np.array([1.0, np.nan, 2.0])))


def test_windows_of_60_s_and_whole_recording(clean) -> None:
    w = ft.window_features(clean)
    # 130 s from the first beat: 60 s + 68 s (the 8 s tail joins the second window)
    assert len(w) == 2 and not any(v.whole for v in w)
    assert w[0].window_end_s - w[0].window_start_s == pytest.approx(60.0)
    assert sum(v.n_beats for v in w) == clean.k.size
    whole = ft.recording_features(clean)
    assert whole.whole and whole.n_beats == clean.k.size
    # fourth wheel: never per window, and not for a whole recording under 180 s
    assert all(math.isnan(v.fourth_power) for v in [*w, whole])


def test_sub_impulse_statistics_match_the_truth(clean) -> None:
    whole = ft.recording_features(clean)
    t_lift_ms = lift_time_s(280.0, 52.0, 28800) * 1e3
    assert whole.d12_mean_ms == pytest.approx(0.45 * t_lift_ms, abs=0.02)
    assert whole.d23_mean_ms == pytest.approx(0.55 * t_lift_ms, abs=0.1)
    assert whole.d12_var_ms2 < 1e-3  # 20 µs jitter only
    assert whole.amplitude_deg == pytest.approx(280.0, abs=3.0)
    assert whole.n_subevents == clean.k.size
    assert whole.spurious_fraction == 0.0 and whole.timing_jumps == 0
    assert whole.rate_spd == pytest.approx(0.0, abs=0.1)
    assert ft.diagnose(whole, ft.window_features(clean)) == []


def test_energy_ratio_follows_a_weak_p1() -> None:
    x, _ = synth_watch(bph=28800, seconds=30, seed=3, snr_db=30, jitter_us=20, p1_gain=0.3)
    weak = ft.recording_features(extract_series(x, FS, 28800))
    assert weak.e1_e2 < 1.0 and weak.e3_e2 == pytest.approx(4.4, rel=0.15)


def test_escape_energy_per_window_and_fourth_from_180_s() -> None:
    x, _ = synth_watch(bph=28800, seconds=200, seed=7, snr_db=30, jitter_us=20,
                       phase_mod=((3.75, 40.0), (60.0, 150.0)))  # fmt: skip
    s = extract_series(x, FS, 28800)
    for v in ft.window_features(s):
        assert v.escape_amp_us == pytest.approx(40.0, rel=0.2) and v.escape_power > 10
    whole = ft.recording_features(s)
    assert whole.fourth_amp_us == pytest.approx(150.0, rel=0.3)
    # the 180 s condition is on the length of the recording
    short = ft.recording_features(replace(s, duration_s=FOURTH_WHEEL_MIN_RECORDING_S - 1))
    assert math.isnan(short.fourth_power)
    assert "short_for_fourth" in spectral_report(replace(s, duration_s=179.0)).warnings
    assert "short_for_fourth" not in spectral_report(replace(s, duration_s=180.0)).warnings
    codes = {f.code for f in ft.diagnose(whole, [], None, spectral_report(s))}
    assert codes == {"escape_wheel", "fourth_wheel"}


def test_positional_deltas() -> None:
    assert ft.positional_deltas({"DU": (1.0, 0.1, 300.0)}) is None  # no vertical
    p = ft.positional_deltas(
        {"DU": (4.0, 0.2, 300.0), "DD": (6.0, 0.3, 290.0), "CU": (-2.0, 0.9, 250.0),
         "CL": (0.0, 0.4, 260.0)}
    )  # fmt: skip
    assert p.positions == ("DU", "DD", "CU", "CL")
    assert p.delta_rate_hv_spd == pytest.approx(6.0) and p.delta_amp_hv_deg == pytest.approx(40.0)
    assert p.be_spread_ms == pytest.approx(0.7)
    assert json.dumps(p.as_dict())


def _vec(**kw) -> ft.FeatureVector:
    base = dict(
        window_start_s=0.0, window_end_s=60.0, whole=True, n_beats=480, rate_spd=2.0,
        beat_error_ms=0.2, amplitude_deg=280.0, escape_power=1.0, escape_amp_us=1.0,
        fourth_power=math.nan, fourth_amp_us=math.nan, d12_mean_ms=3.3, d12_var_ms2=1e-4,
        d12_skew=0.0, d12_kurt=0.0, d23_mean_ms=4.0, d23_var_ms2=1e-4, d23_skew=0.0,
        d23_kurt=0.0, n_subevents=480, e1_e2=3.0, e3_e2=4.0, spurious_fraction=0.0,
        timing_jumps=0,
    )  # fmt: skip
    base.update(kw)
    return ft.FeatureVector(**base)


def _levels(findings) -> dict[str, str]:
    return {f.code: f.level for f in findings}


def test_rebanking_rule() -> None:
    assert (
        _levels(ft.diagnose(_vec(amplitude_deg=300, spurious_fraction=0.2, timing_jumps=5), []))
        == {}
    )
    assert _levels(ft.diagnose(_vec(amplitude_deg=320), [])) == {}  # amplitude alone
    assert _levels(ft.diagnose(_vec(amplitude_deg=320, timing_jumps=3), [])) == {
        "rebanking": "possible"
    }
    assert _levels(
        ft.diagnose(_vec(amplitude_deg=320, timing_jumps=3, spurious_fraction=0.1), [])
    ) == {"rebanking": "probable"}


def test_lubrication_rule() -> None:
    unstable = {"d12_var_ms2": (0.1 * 3.3) ** 2}  # 10 % variation of t_P2 - t_P1
    assert _levels(ft.diagnose(_vec(e3_e2=15.0), [])) == {"pallet_lubrication": "possible"}
    assert _levels(ft.diagnose(_vec(**unstable), [])) == {"pallet_lubrication": "possible"}
    assert _levels(ft.diagnose(_vec(e3_e2=15.0, **unstable), [])) == {
        "pallet_lubrication": "probable"
    }


def test_magnetism_rule() -> None:
    fast = _vec(rate_spd=80.0)
    spread = ft.PositionalDeltas(("DU", "CU"), 10.0, 30.0, 0.9)
    steady = ft.PositionalDeltas(("DU", "CU"), 10.0, 30.0, 0.1)
    assert _levels(ft.diagnose(fast, [], spread)) == {"magnetism": "probable"}
    assert _levels(ft.diagnose(fast, [], steady)) == {}
    assert _levels(ft.diagnose(_vec(rate_spd=5.0), [], spread)) == {}
    # without positions, a beat error that wanders between windows is weaker evidence
    windows = [_vec(beat_error_ms=b) for b in (0.1, 0.9, 0.2, 0.8)]
    assert _levels(ft.diagnose(fast, windows)) == {"magnetism": "possible"}


# ---------------------------------------------------------------------- GUI
def test_dialog_tabs_and_export(qtbot, tmp_path) -> None:
    pytest.importorskip("PyQt6")
    from adv_tg.i18n.translator import Translator
    from adv_tg.ui.analysis_dialog import AnalysisDialog, RecordingSource

    x, _ = synth_watch(bph=28800, seconds=70, seed=3, snr_db=30, jitter_us=20,
                       amplitude_deg=325, odd_jitter_us=200)  # fmt: skip
    positions = {"DU": (4.0, 0.2, 320.0), "CU": (-2.0, 0.3, 280.0)}
    dlg = AnalysisDialog(Translator("en"), positions=positions)
    qtbot.addWidget(dlg)
    assert not dlg.btn_export.isEnabled()
    dlg.analyze(RecordingSource(x, FS, 28800, name="rb.flac"), blocking=True)
    assert dlg.tabs.count() == 3 and dlg.btn_export.isEnabled()
    assert dlg.tbl_features.columnCount() == len(dlg.windows) + 1
    assert dlg.tbl_features.horizontalHeaderItem(dlg.tbl_features.columnCount() - 1).text() == (
        "Whole recording"
    )
    assert dlg.tbl_features.rowCount() == len(dlg.FEATURE_ROWS)
    assert "ΔRate H−V +6.0 s/d" in dlg.lbl_positional.text()
    rows = [dlg.tbl_findings.item(i, 0).text() for i in range(dlg.tbl_findings.rowCount())]
    assert dlg._tr("an_finding_rebanking") in rows  # 325° + timing excursions
    out = dlg.export_features(tmp_path / "f.json")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["bph"] == 28800 and data["whole"]["amplitude_deg"] == pytest.approx(325, abs=3)
    assert data["whole"]["fourth_power"] is None and len(data["windows"]) == len(dlg.windows)
    assert data["positional"]["positions"] == ["DU", "CU"]
    assert any(f["code"] == "rebanking" for f in data["findings"])
