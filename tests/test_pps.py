"""GPS PPS calibration: detector, calibrator, live worker and the calibration dialog.

Truth: ``synth_pps`` (pulses at known instants of a card off by a known ppm). Each beep has a
deterministic first lobe and a RANDOM amplitude build-up, like the active buzzer of
``docs/samples/PPS.flac``, and clips; a detector that times the burst instead of its start
fails here. The real recording is checked too when it is present.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from adv_tg.core import pps
from adv_tg.core.adc_clock import ClockLevel
from adv_tg.core.pps import PpsCalibrator, PpsDetector, calibrate_samples

FS = 44_100
SAMPLE = Path(__file__).resolve().parents[1] / "docs" / "samples" / "PPS.flac"


def synth_pps(
    seconds: float = 150.0,
    *,
    ppm: float = 22.0,
    fs: int = FS,
    seed: int = 0,
    noise: float = 1e-3,
    jitter_us: float = 5.0,
    offset_s: float = 0.37,
    drop: tuple[int, ...] = (),
    jump: tuple[int, float] | None = None,
    clicks: tuple[float, ...] = (),
) -> tuple[np.ndarray, np.ndarray]:
    """A buzzer PPS heard by a card ``ppm`` fast. Returns ``(x, true onsets in samples)``.

    ``jump=(n, shift_s)``: the first ``n`` pulses are ``shift_s`` early (GPS before its fix).
    ``clicks``: stray clicks at these times (s).
    """
    rng = np.random.default_rng(seed)
    n = int(seconds * fs)
    x = rng.normal(0.0, noise, n)
    scale = fs * (1.0 + ppm * 1e-6)  # samples per true second
    k = np.arange(int(seconds - offset_s - 0.2))
    t_true = offset_s + k + rng.normal(0.0, jitter_us * 1e-6, k.size)
    if jump is not None:
        t_true[: jump[0]] -= jump[1]
    keep = np.ones(k.size, dtype=bool)
    keep[list(drop)] = False
    onsets = t_true[keep] * scale
    f0 = 2700.0
    dur = int(0.12 * fs)
    for o in onsets:
        i0 = int(math.ceil(o))
        tt = (np.arange(dur) + i0 - o) / fs  # time since the true onset
        rise = rng.uniform(1e-3, 4e-3)  # random build-up of the oscillator
        env = 0.08 * (1.0 - np.exp(-tt / 5e-5)) + 0.5 * np.clip(tt / rise, 0.0, 1.0)
        burst = np.clip(env * np.sin(2 * np.pi * f0 * tt), -0.3, 0.3)
        x[i0 : i0 + dur] += burst[: max(0, min(dur, n - i0))]
    for c in clicks:
        i = int(c * fs)
        x[i : i + 40] += 0.2 * np.exp(-np.arange(40) / 8.0) * np.sign(rng.normal(size=40))
    return x, onsets


def _ppm_tol(est) -> float:
    return max(4 * est.drift.uncertainty_ppm, 0.05)


# ---------------------------------------------------------------------- detector
def test_detector_times_the_first_lobe() -> None:
    x, truth = synth_pps(40.0, ppm=0.0, jitter_us=0.0)
    det = PpsDetector(FS)
    found = np.array([p.index for p in det.process(x, 0)])
    assert found.size == truth.size
    err_us = (found - truth) / FS * 1e6
    # constant delay (threshold on the first lobe), same for every pulse
    assert abs(np.median(err_us)) < 30 and np.std(err_us) < 3.0
    assert det.rejected == 0


def test_detector_is_chunk_invariant() -> None:
    x, _ = synth_pps(30.0, seed=1)

    def run(step: int) -> np.ndarray:
        det = PpsDetector(FS)
        out = []
        for a in range(0, x.size, step):
            out += [p.index for p in det.process(x[a : a + step], a)]
        return np.array(out)

    whole, small = run(x.size), run(1024)
    assert whole.size == small.size and np.array_equal(whole, small)


def test_detector_validation() -> None:
    with pytest.raises(ValueError):
        PpsDetector(0)
    with pytest.raises(ValueError):
        PpsDetector(FS, threshold_sigma=0)


# ---------------------------------------------------------------------- calibrator
@pytest.mark.parametrize("ppm", [-35.0, 0.0, 22.0, 120.0])
def test_recovers_the_card_error(ppm: float) -> None:
    x, _ = synth_pps(150.0, ppm=ppm, seed=2)
    est = calibrate_samples(x, FS)
    assert est.valid and est.drift.method == "pps" and est.drift.level == ClockLevel.EXTERNAL
    assert est.drift.ppm == pytest.approx(ppm, abs=_ppm_tol(est))
    assert est.jumps == 0 and est.missing == 0 and est.rejected == 0
    assert est.jitter_us < 10 and est.ready
    assert est.drift.rate_bias_s_per_day == pytest.approx(-0.0864 * ppm, abs=0.01)


def test_phase_jump_restarts_on_the_locked_pulses() -> None:
    # 18 pulses 230 ms early, as in the sample before the GPS fix
    x, _ = synth_pps(150.0, ppm=22.0, seed=3, jump=(18, 0.23))
    est = calibrate_samples(x, FS)
    assert est.jumps == 1 and est.used == est.pulses - 18
    assert est.drift.ppm == pytest.approx(22.0, abs=_ppm_tol(est))


def test_stray_clicks_and_missing_pulses() -> None:
    x, _ = synth_pps(150.0, ppm=-10.0, seed=4, drop=(20, 21, 60), clicks=(30.8, 75.75))
    est = calibrate_samples(x, FS)
    assert est.jumps == 0 and est.missing == 3 and est.rejected == 2
    assert est.drift.ppm == pytest.approx(-10.0, abs=_ppm_tol(est))


def test_not_enough_pulses_and_readiness() -> None:
    x, _ = synth_pps(8.0)
    est = calibrate_samples(x, FS)
    assert not est.valid and not est.ready and est.drift.level == ClockLevel.UNCALIBRATED
    x, _ = synth_pps(60.0)
    est = calibrate_samples(x, FS)
    assert est.valid and not est.ready  # precise, but shorter than READY_SPAN_S


def test_excess_scatter_widens_the_sigma() -> None:
    rng = np.random.default_rng(5)
    k = np.arange(300.0)
    white = k * FS + rng.normal(0, 0.3, k.size)
    wander = white + np.cumsum(rng.normal(0, 0.15, k.size))  # slow delay changes
    est_w, est_r = PpsCalibrator(FS), PpsCalibrator(FS)
    est_w.add(white)
    est_r.add(wander)
    a, b = est_w.estimate(), est_r.estimate()
    assert a.excess < 1.5 and b.excess > 2.0
    assert b.drift.sigma_ppm > a.drift.sigma_ppm


def test_calibrator_counts_a_lone_click_before_the_first_pulse() -> None:
    cal = PpsCalibrator(FS)
    cal.add([0.42 * FS] + [(1.0 + i) * FS for i in range(20)])
    est = cal.estimate()
    assert est.jumps == 0 and est.rejected == 1 and est.used == 20


@pytest.mark.skipif(not SAMPLE.is_file(), reason="docs/samples/PPS.flac no está")
def test_real_recording() -> None:
    from adv_tg.utils.audio_files import read_mono

    fs, x = read_mono(SAMPLE)
    est = calibrate_samples(x, fs)
    # measured: +22.187 ± 0.016 ppm, 296 pulses after one phase jump (GPS fix), 6.8 µs
    assert est.jumps == 1 and est.used >= 290 and est.missing == 0
    assert est.drift.ppm == pytest.approx(22.19, abs=0.05)
    assert est.jitter_us < 15 and est.ready


# ---------------------------------------------------------------------- GUI
def _factory(x):
    from tests.test_measure_worker import Factory

    return Factory(x.astype(np.float32), speed=16.0)


def test_live_worker_emits_estimates(qtbot) -> None:
    pytest.importorskip("PyQt6")
    from adv_tg.ui.pps_worker import PpsCalibrationWorker

    x, _ = synth_pps(40.0, ppm=15.0, seed=6)
    worker = PpsCalibrationWorker(3, FS, capture_factory=_factory(x))
    got = []
    worker.pps_updated.connect(got.append)
    with qtbot.waitSignal(worker.session_finished, timeout=20000):
        worker.start_stream()
    qtbot.waitUntil(lambda: worker.last_estimate is not None and worker.last_estimate.valid)
    est = worker.last_estimate
    assert len(got) >= 2 and est.used >= 35
    assert est.drift.ppm == pytest.approx(15.0, abs=_ppm_tol(est))
    worker.shutdown()


def test_dialog_pps_tab(qtbot, tmp_path) -> None:
    pytest.importorskip("PyQt6")
    from adv_tg.i18n.translator import Translator
    from adv_tg.ui.dialogs import CalibrationDialog
    from adv_tg.ui.pps_worker import PpsCalibrationWorker
    from adv_tg.utils.adc_calibration import CalibrationStore
    from adv_tg.utils.audio_files import write_audio

    store = CalibrationStore(tmp_path / "c.json")
    x, _ = synth_pps(150.0, ppm=-8.0, seed=7)
    wav = write_audio(tmp_path / "pps.flac", x, FS, subtype="PCM_24")

    # no device / device busy: live start disabled, file analysis still possible
    nodev = CalibrationDialog(Translator("en"), "Fake CM18", FS, store=store)
    qtbot.addWidget(nodev)
    assert nodev.tabs.count() == 3 and not nodev.btn_pps_start.isEnabled()
    busy = CalibrationDialog(Translator("es"), "Fake CM18", FS, store=store, device_id=3,
                             capture_busy=True)  # fmt: skip
    qtbot.addWidget(busy)
    assert not busy.btn_pps_start.isEnabled()
    assert busy.btn_pps_start.toolTip() == busy._tr("cal_pps_busy")

    dlg = CalibrationDialog(Translator("es"), "Fake CM18", FS, store=store)
    qtbot.addWidget(dlg)
    assert not dlg.btn_pps_save.isEnabled()
    est = dlg.analyze_pps_file(wav)
    assert est is not None and est.ready and dlg.btn_pps_save.isEnabled()
    assert dlg.lbl_pps_ppm.text().startswith(f"{est.drift.ppm:+.3f} ±")
    assert est.drift.ppm == pytest.approx(-8.0, abs=0.1)
    assert dlg.lbl_pps_status.text().startswith("pps.flac")
    assert dlg.save_pps(confirm=False)
    rec = store.records("Fake CM18", FS)[-1]
    assert rec.drift.method == "pps" and rec.drift.ppm == pytest.approx(-8.0, abs=0.1)
    assert "GPS PPS" in rec.drift.note and "pps.flac" in rec.drift.note
    assert dlg.table.rowCount() == 1 and f"{rec.drift.ppm:+.3f}" in dlg.lbl_current.text()
    # a recording at another sample rate is refused (calibrations are per rate)
    other = CalibrationDialog(Translator("en"), "Fake CM18", 48000, store=store)
    qtbot.addWidget(other)
    other._error = lambda exc: setattr(other, "last_error", str(exc))  # no message box
    assert other.analyze_pps_file(wav) is None and "44100" in other.last_error

    # live: the worker comes from the factory (a replayed PPS instead of a device)
    y, _ = synth_pps(30.0, ppm=5.0, seed=8)
    live = CalibrationDialog(
        Translator("en"), "Fake CM18", FS, store=store, device_id=3,
        pps_worker_factory=lambda dev, fs: PpsCalibrationWorker(
            dev, fs, capture_factory=_factory(y)
        ),
    )  # fmt: skip
    qtbot.addWidget(live)
    assert live.btn_pps_start.isEnabled()
    live.btn_pps_start.click()
    assert live.btn_pps_start.text() == live._tr("cal_pps_stop")
    qtbot.waitUntil(lambda: not live.pps_worker.isRunning(), timeout=20000)
    qtbot.waitUntil(lambda: live.pps_estimate is not None and live.pps_estimate.valid)
    qtbot.waitUntil(lambda: live.btn_pps_start.text() == live._tr("cal_pps_start"))
    assert live.pps_estimate.drift.ppm == pytest.approx(5.0, abs=_ppm_tol(live.pps_estimate))
    assert live.btn_pps_save.isEnabled() and not live.pps_estimate.ready
    live.close()


def test_reference_result_goes_to_the_manual_tab(qtbot, tmp_path) -> None:
    pytest.importorskip("PyQt6")
    from adv_tg.i18n.translator import Translator
    from adv_tg.ui.dialogs import CalibrationDialog
    from adv_tg.utils.adc_calibration import CalibrationStore

    dlg = CalibrationDialog(Translator("en"), "Fake CM18", FS,
                            store=CalibrationStore(tmp_path / "c.json"), measured_rate_spd=-0.564)  # fmt: skip
    qtbot.addWidget(dlg)
    dlg.tabs.setCurrentIndex(dlg.REFERENCE_TAB)
    dlg.spn_ref_known.setValue(0.3)
    dlg.btn_ref_apply.click()
    assert dlg.tabs.currentIndex() == dlg.MANUAL_TAB
    assert dlg.spn_ppm.value() == pytest.approx(10.0)


def test_constants_are_consistent() -> None:
    assert pps.READY_SIGMA_PPM > pps.REFERENCE_SIGMA_PPM
    assert pps.HOLDOFF_S < 0.5 < pps.READY_SPAN_S
