"""Check the program against the GPS PPS reference: ``python -m adv_tg.tools.reference_check``.

``docs/samples/pps_48000Hz.flac`` (shipped with the program) is a GPS PPS made audible by a
buzzer: one pulse per second, exact to better than a microsecond. Its tags
(``ADV_TG_ADC_DRIFT_*``, written by ``adc_calibration pps --tag``) hold the error of the sound
card that recorded it, measured on the same pulses. Two independent paths must agree:

1. the PPS detector (``core.pps``) must find the same card error as the tags;
2. the watch engine (beat detector + metrics, as a 1/s quartz at 3600 BPH), corrected with
   that error, must read a rate of 0 s/d with a timing scatter of a few µs.

It runs in ``adv-tg --smoke-test`` (every packaged build, on every platform) and from a
terminal. Measured when the reference was made: +7.443 +- 0.043 ppm; watch engine
+0.003 +- 0.005 s/d, 8/7 µs.

Exit codes: 0 = passed, 1 = failed, 2 = the reference is missing or unreadable.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

from adv_tg.core.adc_clock import REFERENCE_TAG, drift_from_tags
from adv_tg.core.pps import calibrate_samples
from adv_tg.tools.measure import measure_file
from adv_tg.utils.audio_files import AudioFileError, read_mono, read_tags

__all__ = ["REFERENCE_NAME", "ReferenceCheck", "check_reference", "main"]

REFERENCE_NAME = "pps_48000Hz.flac"
BPH = 3600.0
#: The PPS detector must reproduce the tagged card error within this (ppm).
PPM_TOL = 0.05
#: |rate| allowed for the corrected watch engine: this, or 4 of its sigmas if larger (s/d).
RATE_TOL_S_PER_DAY = 0.02
#: Timing scatter of a beat (µs): 8/7 µs measured; the template timing alone gave 190.
MAX_SIGMA_US = 30.0

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ERROR = 2


@dataclass(frozen=True)
class ReferenceCheck:
    """Outcome of ``check_reference``."""

    passed: bool
    tagged_ppm: float
    pps_ppm: float
    pps_sigma_ppm: float
    rate_s_per_day: float
    rate_uncertainty_s_per_day: float
    sigma_us: float
    problems: tuple[str, ...]

    def summary(self) -> str:
        verdict = "OK" if self.passed else "FALLO"
        text = (
            f"{verdict}: PPS {self.pps_ppm:+.3f} ± {self.pps_sigma_ppm:.3f} ppm (etiqueta "
            f"{self.tagged_ppm:+.3f}); motor de relojes {self.rate_s_per_day:+.4f} ± "
            f"{self.rate_uncertainty_s_per_day:.4f} s/d, {self.sigma_us:.0f} µs"
        )
        return text + "".join(f"\n  - {p}" for p in self.problems)


def check_reference(path: Path | str) -> ReferenceCheck:
    """Run both checks on the reference recording.

    Raises:
        AudioFileError: Missing or unreadable file.
        ValueError: The file is not a tagged reference.
    """
    p = Path(path)
    tags = read_tags(p)
    drift = drift_from_tags(tags)
    if REFERENCE_TAG not in tags or drift is None:
        raise ValueError(f"'{p.name}' no es una grabación de referencia etiquetada")
    fs, x = read_mono(p)
    pps = calibrate_samples(x, fs).drift
    m = measure_file(
        p, BPH, quartz=True, adc_drift_ppm=drift.ppm, adc_drift_sigma_ppm=drift.uncertainty_ppm
    ).metrics
    sigma_us = max(m.sigma_even_us, m.sigma_odd_us)
    problems: list[str] = []
    if not abs(pps.ppm - drift.ppm) <= PPM_TOL:
        problems.append(f"el detector PPS da {pps.ppm:+.3f} ppm, la etiqueta {drift.ppm:+.3f}")
    bound = max(RATE_TOL_S_PER_DAY, 4.0 * m.rate_uncertainty_s_per_day)
    if not (math.isfinite(m.rate_s_per_day) and abs(m.rate_s_per_day) <= bound):
        problems.append(f"marcha corregida {m.rate_s_per_day:+.4f} s/d (máximo ± {bound:.3f})")
    if not sigma_us <= MAX_SIGMA_US:
        problems.append(f"dispersión por batimiento {sigma_us:.0f} µs (máximo {MAX_SIGMA_US:.0f})")
    return ReferenceCheck(
        passed=not problems,
        tagged_ppm=drift.ppm,
        pps_ppm=pps.ppm,
        pps_sigma_ppm=pps.uncertainty_ppm,
        rate_s_per_day=m.rate_s_per_day,
        rate_uncertainty_s_per_day=m.rate_uncertainty_s_per_day,
        sigma_us=sigma_us,
        problems=tuple(problems),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m adv_tg.tools.reference_check",
        description="Comprueba el programa con la grabación de referencia del PPS de GPS.",
    )
    parser.add_argument(
        "file", nargs="?", type=Path, default=None,
        help=f"grabación de referencia (por defecto docs/samples/{REFERENCE_NAME})",
    )  # fmt: skip
    args = parser.parse_args(argv)
    path = args.file or Path(__file__).resolve().parents[3] / "docs" / "samples" / REFERENCE_NAME
    try:
        result = check_reference(path)
    except (AudioFileError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return EXIT_ERROR
    print(f"{path.name}: {result.summary()}")
    return EXIT_OK if result.passed else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
