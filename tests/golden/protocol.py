"""Protocol of the golden files (migration plan, phase 0): how they are produced and compared.

The golden files freeze the outputs of v2.0.1 on the versioned recordings of ``docs/samples/``
so that every later implementation (the C++ application of 3.0 included) is checked against
the figures validated today. The protocol is written so that any language can reproduce it
bit for bit:

* audio: the mono samples read as float64 (libsndfile ``sf_readf_double`` / ``soundfile``);
* batch: ``BeatDetector`` (default configuration) fed with consecutive float64 chunks of
  ``BATCH_CHUNK`` samples, then ``flush``; ``compute_metrics`` over all the beats;
* live: a ``MeasurementSession`` over a ``SampleStream`` of ``LIVE_CAPACITY_S`` seconds; the
  samples are written as float32 blocks of ``LIVE_BLOCK``; the stamp of the block ending at
  sample ``b`` is ``stamp_ns(b, fs)`` (integer arithmetic, an ADC 12 ppm slow), written as
  host and wall time; ``update`` after every ``live_every(fs)`` blocks and after the last one;
  only the first ``LIVE_SECONDS`` seconds. The live clock estimate is off and a fixed
  calibration (``LIVE_CALIBRATION``) is applied, so the result does not depend on the host
  clock of the machine;
* PPS: ``PpsDetector`` over consecutive float64 chunks of one second.

Files (``tests/golden/<recording stem>/``): ``beats.csv``, ``metrics.json``, ``live.jsonl``
(watches) or ``pulses.csv`` (PPS), plus ``tests/golden/manifest.json``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: Watch recordings: file name -> (BPH, lift angle or None, quartz).
WATCHES: dict[str, tuple[int, float | None, bool]] = {
    "miyota_9015.flac": (28800, 51.0, False),
    "4r36.flac": (21600, 53.0, False),
    "SeagullST1901.flac": (21600, 47.0, False),
    "hamilton_h50.flac": (21600, 50.0, False),
    "hamilton_h50new.flac": (21600, 50.0, False),
    "nh34.flac": (21600, 53.0, False),
    "tmi_vh31.flac": (14400, None, True),
    "citizen_H874.flac": (3600, None, True),
    "bulova_nm10.flac": (57600, None, True),
    "boluva_nm10_new.flac": (57600, None, True),
}
#: GPS PPS recordings.
PPS: tuple[str, ...] = ("pps_48000Hz.flac", "pps_44100Hz.flac")

BATCH_CHUNK = 4096
PPS_CHUNK_S = 1
LIVE_BLOCK = 1024
LIVE_SECONDS = 60
LIVE_CAPACITY_S = 20
LIVE_T0_NS = 1_700_000_000_000_000_000
#: ADC slower than the host clock by this many ppm in the synthetic stamps.
LIVE_STAMP_PPM = 12
#: Fixed calibration of the live session: (ppm, sigma_ppm, systematic_ppm), manual (level 3).
LIVE_CALIBRATION = (12.0, 0.0, 0.5)

#: Beat fields stored in beats.csv (in this order).
BEAT_FIELDS = (
    "k", "flags", "n_subevents", "p1", "p2", "p3", "t_fine", "t_p1", "t_p1_tpl",
    "snr_db", "peak_dbfs", "confidence",
)  # fmt: skip
#: Metrics fields stored in metrics.json and in each live snapshot.
METRIC_FIELDS = (
    "n_beats", "n_used_even", "n_used_odd", "n_excluded", "excluded_fraction",
    "rate_s_per_day", "rate_sigma_s_per_day", "rate_uncertainty_s_per_day",
    "beat_error_ms", "beat_error_uncertainty_ms", "beat_error_p3_ms", "beat_error_point",
    "amplitude_deg", "amplitude_tic_deg", "amplitude_tac_deg", "amplitude_parity",
    "sigma_even_us", "sigma_odd_us", "confidence", "span_s",
)  # fmt: skip
#: Display fields of each live snapshot.
DISPLAY_FIELDS = (
    "rate_s_per_day", "beat_error_ms", "beat_error_p3_ms", "amplitude_deg",
    "amplitude_tic_deg", "amplitude_tac_deg", "confidence", "timing_confidence",
)  # fmt: skip
#: Scalar fields of each live snapshot.
SNAPSHOT_FIELDS = (
    "audio_s", "new_beats", "beats_total", "locked", "saturated_beats", "snr_db",
    "detection_ratio", "usable_ratio", "real_bph",
)  # fmt: skip


def stamp_ns(end_index: int, fs: int) -> int:
    """Host/wall stamp of the block that ends at ``end_index`` (exact integer arithmetic)."""
    return (
        LIVE_T0_NS + (end_index * 1_000_000_000) // fs - (end_index * LIVE_STAMP_PPM * 1000) // fs
    )


def live_every(fs: int) -> int:
    """Blocks between two live updates (0.25 s)."""
    return max(1, int(0.25 * fs) // LIVE_BLOCK)


@dataclass(frozen=True)
class Tolerance:
    """Allowed difference against the golden files (0 = exact)."""

    beat_time_samples: float  # p1, p2, p3, t_fine, t_p1, t_p1_tpl
    rate_s_per_day: float
    beat_error_ms: float
    amplitude_deg: float
    relative: float  # every other float (sigmas, SNR, confidence, ...)
    other_abs: float


#: Native engine (the one that produced the files): differences only from FMA contraction and
#: libm between compilers / CPUs.
NATIVE = Tolerance(1e-4, 1e-6, 1e-6, 1e-6, 1e-7, 1e-9)

# The golden files are the outputs of the NATIVE engine of v2.0.1, the implementation the C++
# application must reproduce; they are not checked in pure-numpy mode. Measured numpy vs native
# on these recordings (v2.0.1): same beats and t_fine within 3.4 us everywhere, but on the
# Hamilton H-50 takes (weak P1, notch filter active) the unlocking-front instant t_p1 picks a
# different candidate on some beats (up to 36 samples), which moves the LIVE figures by up to
# 0.006 s/d and 1 deg on some updates; the batch figures agree within their uncertainty
# (rate 1.7e-4 s/d, beat error 0.011 ms for +-0.022 ms). See docs/PLAN_MIGRACION_CPP.md.

FIELD_KIND = {
    **{f: "time" for f in ("p1", "p2", "p3", "t_fine", "t_p1", "t_p1_tpl")},
    **{f: "rate" for f in ("rate_s_per_day",)},
    **{f: "be" for f in ("beat_error_ms", "beat_error_p3_ms")},
    **{f: "amp" for f in ("amplitude_deg", "amplitude_tic_deg", "amplitude_tac_deg")},
}


def close(field: str, got: object, want: object, tol: Tolerance) -> bool:
    """Compare one stored value (NaN == NaN; ints and strings exactly)."""
    if isinstance(want, str) or isinstance(got, str) or isinstance(want, bool):
        return got == want
    g, w = float(got), float(want)  # type: ignore[arg-type]
    if math.isnan(g) or math.isnan(w):
        return math.isnan(g) and math.isnan(w)
    if isinstance(want, int) and not isinstance(want, bool) and float(want).is_integer():
        if field not in FIELD_KIND:
            return g == w
    kind = FIELD_KIND.get(field)
    if kind == "time":
        return abs(g - w) <= tol.beat_time_samples
    if kind == "rate":
        return abs(g - w) <= tol.rate_s_per_day
    if kind == "be":
        return abs(g - w) <= tol.beat_error_ms
    if kind == "amp":
        return abs(g - w) <= tol.amplitude_deg
    return abs(g - w) <= tol.other_abs + tol.relative * abs(w)
