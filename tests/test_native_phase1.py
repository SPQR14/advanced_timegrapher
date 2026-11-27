"""Phase 1 parity tests: native C++20 implementations via ctypes vs pure-Python fallbacks."""

from __future__ import annotations

import zipfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from adv_tg import native_bridge
from adv_tg.analysis import features, gear_train
from adv_tg.core import adc_clock, pps
from adv_tg.utils import adc_calibration, audio_files, report, security, watch_db


def _without_native(monkeypatch: pytest.MonkeyPatch, fn):  # noqa: ANN001
    monkeypatch.setenv(native_bridge.ENV_DISABLE, "1")
    native_bridge.reload()
    try:
        return fn()
    finally:
        monkeypatch.delenv(native_bridge.ENV_DISABLE, raising=False)
        native_bridge.reload()


def _synth_pps_signal(duration_s: float = 35.0, fs: int = 48000, ppm: float = 12.0) -> np.ndarray:
    n = int(round(duration_s * fs))
    rng = np.random.default_rng(123)
    x = 1e-4 * rng.standard_normal(n)
    period = fs * (1.0 + ppm * 1e-6)
    t = 0.25 * fs
    burst_n = int(0.012 * fs)
    tt = np.arange(burst_n, dtype=np.float64) / fs
    pulse = 0.7 * np.exp(-tt / 0.002) * np.sin(2.0 * np.pi * 2500.0 * tt)
    while t + burst_n < n:
        idx = int(round(t))
        x[idx : idx + burst_n] += pulse
        t += period
    return x


def _synth_watch_signal(
    duration_s: float = 40.0, fs: int = 48000, bph: int = 21600, lift_angle: float = 52.0
) -> np.ndarray:
    from adv_tg.mock.generator import add_beat, beat_delta_t_s, beat_interval_s

    n = int(round(duration_s * fs))
    rng = np.random.default_rng(77)
    x = 2e-4 * rng.standard_normal(n)
    dt_p3 = beat_delta_t_s(280.0, lift_angle, bph)
    step = beat_interval_s(bph, 5.0)
    t = 0.2
    k = 0
    while t + 0.05 < duration_s:
        t_mod = t + 140e-6 * np.sin(2.0 * np.pi * t / 5.0)
        add_beat(x, fs, t_mod, dt_p3, is_tock=(k % 2 == 1))
        t += step
        k += 1
    return x


@pytest.mark.skipif(not native_bridge.is_native_available(), reason="native engine required")
def test_audio_files_parity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fs = 48000
    t = np.arange(4800, dtype=np.float64) / fs
    stereo = np.stack(
        [0.05 * np.sin(2 * np.pi * 440 * t), 0.7 * np.sin(2 * np.pi * 880 * t)],
        axis=1,
    )
    tags = {"ADV_TG_DEVICE": "CM18 USB", "ADV_TG_NOTE": "parity"}

    p_nat = audio_files.write_audio(
        tmp_path / "nat.flac", stereo, fs, subtype="PCM_24", comment="hola ñ", tags=tags
    )
    p_py = _without_native(
        monkeypatch,
        lambda: audio_files.write_audio(
            tmp_path / "py.flac", stereo, fs, subtype="PCM_24", comment="hola ñ", tags=tags
        ),
    )

    d_nat = audio_files.read_audio(p_nat)
    d_py = _without_native(monkeypatch, lambda: audio_files.read_audio(p_py))
    assert d_nat.sample_rate == d_py.sample_rate == fs
    assert d_nat.channels == d_py.channels == 2
    assert d_nat.format == d_py.format == "FLAC"
    assert d_nat.comment == d_py.comment == "hola ñ"
    np.testing.assert_array_equal(d_nat.samples, d_py.samples)

    t_nat = audio_files.read_tags(p_nat)
    t_py = _without_native(monkeypatch, lambda: audio_files.read_tags(p_py))
    assert t_nat == t_py == {**tags, "COMMENT": "hola ñ"}

    blocks_nat = list(audio_files.iter_audio_blocks(p_nat, 1024, channel="max_energy"))
    blocks_py = _without_native(
        monkeypatch, lambda: list(audio_files.iter_audio_blocks(p_py, 1024, channel="max_energy"))
    )
    assert len(blocks_nat) == len(blocks_py)
    for (i_n, b_n), (i_p, b_p) in zip(blocks_nat, blocks_py, strict=True):
        assert i_n == i_p
        np.testing.assert_array_equal(b_n, b_p)

    with audio_files.AudioWriter(
        tmp_path / "stream.wav", fs, channels=1, tags={"ADV_TG_INITIAL": "1"}
    ) as w:
        w.write(stereo[:2400, 1])
        w.write(stereo[2400:, 1])
    assert audio_files.read_tags(tmp_path / "stream.wav")["ADV_TG_INITIAL"] == "1"

    with audio_files.AudioWriter(tmp_path / "stream.flac", fs, channels=1) as w:
        w.write(stereo[:2400, 1])
        w.write(stereo[2400:, 1])
        w.tags["ADV_TG_EXTRA"] = "ok"
    assert audio_files.read_tags(tmp_path / "stream.flac")["ADV_TG_EXTRA"] == "ok"


@pytest.mark.skipif(not native_bridge.is_native_available(), reason="native engine required")
def test_calibration_and_pps_parity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    cal = adc_calibration.Calibration(
        device="CM18 USB",
        sample_rate=48000,
        drift=adc_clock.AdcDrift(
            12.5, 0.02, 0.01, adc_clock.ClockLevel.EXTERNAL, "pps", note="ref"
        ),
        measured_at=now,
    )
    st_nat = adc_calibration.CalibrationStore(tmp_path / "nat.json")
    st_py = adc_calibration.CalibrationStore(tmp_path / "py.json")
    st_nat.add(cal)
    _without_native(monkeypatch, lambda: st_py.add(cal))

    recs_nat = st_nat.records("cm18 usb", 48000)
    recs_py = _without_native(monkeypatch, lambda: st_py.records("cm18 usb", 48000))
    assert [r.to_json() for r in recs_nat] == [r.to_json() for r in recs_py]

    tags_nat, used_nat = adc_calibration.recording_tags("CM18 USB", 48000, store=st_nat, now=now)
    tags_py, used_py = _without_native(
        monkeypatch, lambda: adc_calibration.recording_tags("CM18 USB", 48000, store=st_py, now=now)
    )
    assert tags_nat == tags_py
    assert (used_nat.to_json() if used_nat else None) == (used_py.to_json() if used_py else None)
    d_nat = adc_clock.drift_from_tags(tags_nat)
    d_py = _without_native(monkeypatch, lambda: adc_clock.drift_from_tags(tags_py))
    assert d_nat is not None and d_py is not None
    assert d_nat.ppm == pytest.approx(d_py.ppm)
    assert d_nat.method == d_py.method == "pps"

    sig = _synth_pps_signal()
    est_nat = pps.calibrate_samples(sig, 48000)
    est_py = _without_native(monkeypatch, lambda: pps.calibrate_samples(sig, 48000))
    assert est_nat.valid == est_py.valid == True  # noqa: E712
    assert est_nat.used == est_py.used
    assert est_nat.drift.method == est_py.drift.method == "pps"
    assert est_nat.drift.ppm == pytest.approx(est_py.drift.ppm, abs=1e-7)
    assert est_nat.drift.sigma_ppm == pytest.approx(est_py.drift.sigma_ppm, abs=1e-7)
    np.testing.assert_allclose(est_nat.t_s, est_py.t_s, atol=1e-9)
    np.testing.assert_allclose(est_nat.residual_us, est_py.residual_us, atol=1e-6)


@pytest.mark.skipif(not native_bridge.is_native_available(), reason="native engine required")
def test_gear_train_features_and_diagnosis_parity(monkeypatch: pytest.MonkeyPatch) -> None:
    sig = _synth_watch_signal()
    rep_nat = gear_train.analyze_recording(sig, 48000, 21600)
    rep_py = _without_native(monkeypatch, lambda: gear_train.analyze_recording(sig, 48000, 21600))

    assert rep_nat.series.beats_detected == rep_py.series.beats_detected
    assert rep_nat.series.outliers == rep_py.series.outliers
    assert rep_nat.warnings == rep_py.warnings
    assert rep_nat.series.rate_s_per_day == pytest.approx(rep_py.series.rate_s_per_day, abs=1e-6)
    np.testing.assert_allclose(
        rep_nat.timing.ls_power, rep_py.timing.ls_power, rtol=1e-6, atol=1e-6
    )
    np.testing.assert_allclose(
        rep_nat.energy.fft_amplitude, rep_py.energy.fft_amplitude, rtol=1e-5, atol=1e-7
    )

    wf_nat = features.window_features(rep_nat.series)
    wf_py = _without_native(monkeypatch, lambda: features.window_features(rep_py.series))
    assert len(wf_nat) == len(wf_py)
    whole_nat = features.recording_features(rep_nat.series).as_dict()
    whole_py = _without_native(
        monkeypatch, lambda: features.recording_features(rep_py.series)
    ).as_dict()
    assert whole_nat.keys() == whole_py.keys()
    for k, vn in whole_nat.items():
        vp = whole_py[k]
        if vp is None:
            assert vn is None, k
        elif isinstance(vp, (bool, int)):
            assert vn == vp, k
        else:
            assert vn == pytest.approx(vp, rel=1e-5, abs=1e-6), k

    pos = {"DU": (5.0, 0.1, 290.0), "CU": (-3.0, 0.4, 260.0)}
    pd_nat = features.positional_deltas(pos)
    pd_py = _without_native(monkeypatch, lambda: features.positional_deltas(pos))
    assert pd_nat == pd_py

    f_nat = features.diagnose(features.recording_features(rep_nat.series), wf_nat, pd_nat, rep_nat)
    f_py = _without_native(
        monkeypatch,
        lambda: features.diagnose(features.recording_features(rep_py.series), wf_py, pd_py, rep_py),
    )
    assert [(f.code, f.level) for f in f_nat] == [(f.code, f.level) for f in f_py]


@pytest.mark.skipif(not native_bridge.is_native_available(), reason="native engine required")
def test_security_watch_db_report_and_warnings_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for cell in ("=1+1", "+5.25", "-cmd", "normal"):
        assert security.sanitize_csv_cell(cell) == _without_native(
            monkeypatch, lambda c=cell: security.sanitize_csv_cell(c)
        )
    assert security.safe_filename_base("a/b:c_1") == _without_native(
        monkeypatch, lambda: security.safe_filename_base("a/b:c_1")
    )

    assert watch_db.load_watch_db() == _without_native(monkeypatch, watch_db.load_watch_db)

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "adv_tg.log").write_text("line\n", encoding="utf-8")
    cal_file = tmp_path / "cal.json"
    cal_file.write_text('{"version": 1, "calibrations": []}\n', encoding="utf-8")

    z_nat, n_nat = report.build_report(
        tmp_path / "nat.zip",
        description="test report",
        settings={"bph": 21600},
        log_dir=log_dir,
        calibrations=cal_file,
    )
    z_py, n_py = _without_native(
        monkeypatch,
        lambda: report.build_report(
            tmp_path / "py.zip",
            description="test report",
            settings={"bph": 21600},
            log_dir=log_dir,
            calibrations=cal_file,
        ),
    )
    assert n_nat == n_py
    with zipfile.ZipFile(z_nat) as zn, zipfile.ZipFile(z_py) as zp:
        assert sorted(zn.namelist()) == sorted(zp.namelist())

    for code in range(1, 14):
        w = native_bridge.NativeWarningStruct(code=code, param_int=10)
        w.args[0], w.args[1], w.args[2], w.args[3] = 4.5, 21600.0, 0.25, 4.2
        es = native_bridge.format_warning(w, "es")
        en = native_bridge.format_warning(w, "en")
        assert es and en and es != en
