"""Beat detection on an audio file: ``python -m adv_tg.tools.detect_beats FILE --bph N``.

Reads WAV or FLAC through ``adv_tg.utils.audio_files`` (channel with the largest energy by
default), feeds the detector in ``--chunk`` blocks (exercising the incremental path without
loading the whole file) and prints a summary:

duration, expected beats (duration x BPH / 3600), found, missed (gaps in ``k``), rejected,
% with P1/P2/P3, median and p5-p95 t_lift (ms), sigma of the phase residual (µs), lock
time, real-time factor, observed vs nominal period and warnings. As a PHYSICAL
PLAUSIBILITY CHECK (not a metric) it also prints the amplitude implied by the median
t_lift and the given lift angle (~200-330° on healthy watches).

Options: ``--csv`` one row per beat, ``--json`` the summary, ``--plot`` a PNG (needs
matplotlib, optional extra: ``pip install -e ".[plot]"``).

Exit codes: 0 = processed, 2 = input error.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from adv_tg.core.beat_detector import BeatDetector, BeatDetectorConfig, DetectorStats
from adv_tg.core.beat_types import Beat, BeatFlags, amplitude_from_lift_time, beat_period_s
from adv_tg.utils.audio_files import (
    AudioFileError,
    audio_info,
    iter_audio_blocks,
    read_audio,
    select_channel,
)
from adv_tg.utils.debugger import Debugger

__all__ = [
    "EXIT_ERROR",
    "EXIT_OK",
    "Summary",
    "analyze_file",
    "beats_equal",
    "detect_array",
    "main",
    "parity_check",
    "summarize",
]

_dbg = Debugger.get(__name__)

EXIT_OK = 0
EXIT_ERROR = 2
DEFAULT_CHUNK = 4096


@dataclass
class Summary:
    """Result of running the detector over one file (all times in the units named)."""

    file: str
    format: str
    subtype: str
    sample_rate: int
    channels: int
    duration_s: float
    bph: float
    lift_angle_deg: float | None
    expected_beats: float
    found: int
    missed: int
    rejected: int
    gaps: int
    p1_pct: float
    p2_pct: float
    p3_pct: float
    t_lift_median_ms: float
    t_lift_p5_ms: float
    t_lift_p95_ms: float
    residual_sigma_us: float
    lock_time_s: float | None
    first_beat_s: float | None
    realtime_factor: float
    period_ms: float
    nominal_period_ms: float
    period_deviation_pct: float
    implied_amplitude_deg: float | None
    parity_ok: bool | None
    warnings: list[str] = field(default_factory=list)


def detect_array(
    samples: np.ndarray, config: BeatDetectorConfig, chunk: int | None = None
) -> tuple[list[Beat], DetectorStats, float]:
    """Run a detector over an in-memory signal. ``chunk=None`` feeds it all at once.

    Returns ``(beats, stats, elapsed_s)``.
    """
    det = BeatDetector(config)
    t0 = time.perf_counter()
    beats: list[Beat] = []
    if chunk is None:
        beats += det.process(samples, 0)
    else:
        for i in range(0, samples.size, chunk):
            beats += det.process(samples[i : i + chunk], i)
    beats += det.flush()
    return beats, det.stats, time.perf_counter() - t0


def beats_equal(a: list[Beat], b: list[Beat], tol: float = 1e-6) -> bool:
    """True if both lists have the same ``k``, flags and instants (within ``tol`` samples)."""
    if len(a) != len(b):
        return False
    for x, y in zip(a, b, strict=True):
        if x.k != y.k or x.flags != y.flags:
            return False
        for u, v in ((x.p1, y.p1), (x.p2, y.p2), (x.p3, y.p3)):
            if math.isnan(u) != math.isnan(v) or (not math.isnan(u) and abs(u - v) > tol):
                return False
    return True


def parity_check(beats: list[Beat], window: int = 30) -> bool | None:
    """Tic/tac alternation without parity jumps.

    Fits ``t = a + b k + c (-1)^k`` on consecutive windows of beats. A parity jump (``k``
    assigned with the wrong parity after a loss) flips the sign of ``c``. Returns ``None``
    when the beat error is too small to tell tic from tac.
    """
    k = np.array([b.k for b in beats], dtype=np.float64)
    t = np.array([b.reference for b in beats])
    if k.size < 2 * window:
        return None
    cs, ses = [], []
    for s in range(0, k.size - window + 1, window // 2):
        kk, tt = k[s : s + window], t[s : s + window]
        X = np.stack([np.ones(window), kk - kk[0], np.where(kk % 2 == 0, 1.0, -1.0)], axis=1)
        coef, res, *_ = np.linalg.lstsq(X, tt - tt[0], rcond=None)
        r = tt - tt[0] - X @ coef
        se = 1.4826 * float(np.median(np.abs(r))) / math.sqrt(window) + 1e-9
        cs.append(coef[2])
        ses.append(se)
    c = np.array(cs)
    se = np.array(ses)
    sig = np.abs(c) > 4.0 * se
    if np.count_nonzero(sig) < max(2, sig.size // 2):
        return None
    signs = np.sign(c[sig])
    return bool(np.all(signs == signs[0]))


def summarize(
    beats: list[Beat],
    stats: DetectorStats,
    elapsed_s: float,
    *,
    file: str,
    format: str,
    subtype: str,
    sample_rate: int,
    channels: int,
    duration_s: float,
    bph: float,
    lift_angle_deg: float | None,
) -> Summary:
    """Build the ``Summary`` of a detection run."""
    fs = float(sample_rate)
    n = len(beats)
    tl = np.array([b.t_lift_samples for b in beats if not math.isnan(b.t_lift_samples)]) / fs
    res = np.array([b.residual_samples for b in beats if not b.flags & BeatFlags.TIMING_OUTLIER])
    sigma = 1.4826 * float(np.median(np.abs(res - np.median(res)))) if res.size > 2 else math.nan
    med_tl = float(np.median(tl)) if tl.size else math.nan
    amp = None
    if lift_angle_deg and not math.isnan(med_tl):
        amp = amplitude_from_lift_time(med_tl, lift_angle_deg, bph)
    pct = (lambda v: 100.0 * v) if n else (lambda v: 0.0)
    period = stats.period_samples / fs
    nominal = beat_period_s(bph)
    return Summary(
        file=file,
        format=format,
        subtype=subtype,
        sample_rate=int(sample_rate),
        channels=channels,
        duration_s=duration_s,
        bph=bph,
        lift_angle_deg=lift_angle_deg,
        expected_beats=duration_s * bph / 3600.0,
        found=n,
        missed=stats.missed_beats,
        rejected=stats.rejected_events,
        gaps=stats.gaps,
        p1_pct=pct(stats.p1_fraction),
        p2_pct=pct(stats.p2_fraction),
        p3_pct=pct(stats.p3_fraction),
        t_lift_median_ms=med_tl * 1e3,
        t_lift_p5_ms=float(np.percentile(tl, 5)) * 1e3 if tl.size else math.nan,
        t_lift_p95_ms=float(np.percentile(tl, 95)) * 1e3 if tl.size else math.nan,
        residual_sigma_us=sigma / fs * 1e6,
        lock_time_s=stats.lock_time_s,
        first_beat_s=beats[0].reference / fs if beats else None,
        realtime_factor=duration_s / elapsed_s if elapsed_s > 0 else math.inf,
        period_ms=period * 1e3,
        nominal_period_ms=nominal * 1e3,
        period_deviation_pct=(period / nominal - 1.0) * 100.0,
        implied_amplitude_deg=amp,
        parity_ok=parity_check(beats),
        warnings=list(stats.warnings),
    )


def analyze_file(
    path: Path | str,
    bph: float,
    lift_angle_deg: float | None = None,
    *,
    chunk: int = DEFAULT_CHUNK,
    channel: str | int = "max_energy",
) -> tuple[Summary, list[Beat]]:
    """Detect beats in a file reading it block by block (incremental path).

    ``lift_angle_deg=None`` uses the detector default (52°) only to bound the search
    window, and no implied amplitude is reported.

    Raises:
        AudioFileError: Missing, unsupported or corrupt file.
    """
    p = Path(path)
    info = audio_info(p)
    det: BeatDetector | None = None
    beats: list[Beat] = []
    elapsed = 0.0
    frames = 0
    for start, block in iter_audio_blocks(p, chunk, channel=channel):
        if det is None:
            cfg = BeatDetectorConfig(
                sample_rate=info.sample_rate,
                bph=bph,
                lift_angle_deg=lift_angle_deg or 52.0,
            )
            det = BeatDetector(cfg)
        t0 = time.perf_counter()
        beats += det.process(block, start)
        elapsed += time.perf_counter() - t0
        frames = start + block.shape[0]
    if det is None:
        raise AudioFileError(f"el archivo '{p}' no contiene muestras")
    t0 = time.perf_counter()
    beats += det.flush()
    elapsed += time.perf_counter() - t0
    summary = summarize(
        beats,
        det.stats,
        elapsed,
        file=str(p),
        format=info.format,
        subtype=info.subtype,
        sample_rate=info.sample_rate,
        channels=info.channels,
        duration_s=frames / info.sample_rate,
        bph=bph,
        lift_angle_deg=lift_angle_deg,
    )
    return summary, beats


def detect_whole_file(
    path: Path | str, bph: float, lift_angle_deg: float | None = None, chunk: int | None = None
) -> list[Beat]:
    """Read the file at once and detect (``chunk=None``: whole signal in one call)."""
    data = read_audio(path)
    x = select_channel(data)
    cfg = BeatDetectorConfig(
        sample_rate=data.sample_rate, bph=bph, lift_angle_deg=lift_angle_deg or 52.0
    )
    beats, _, _ = detect_array(x, cfg, chunk)
    return beats


# ---------------------------------------------------------------------- output
def _fmt(v: float | None, spec: str, none: str = "—") -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return none
    return format(v, spec)


def print_summary(s: Summary) -> None:
    """Print a human-readable summary (Spanish)."""
    print("===== DETECCIÓN DE BATIMIENTOS =====")
    print(f"Archivo            : {s.file}")
    print(
        f"Formato            : {s.format}/{s.subtype}, {s.sample_rate} Hz, {s.channels} canal(es)"
    )
    print(f"Duración           : {s.duration_s:.2f} s")
    la = f"{s.lift_angle_deg:g}°" if s.lift_angle_deg else "— (ventanas con 52°)"
    print(f"Calibre            : {s.bph:g} BPH, levantamiento {la}")
    print(f"Esperados          : {s.expected_beats:.1f}  (duración × BPH / 3600)")
    print(f"Encontrados        : {s.found}")
    print(f"Perdidos (k)       : {s.missed}")
    print(f"Rechazados         : {s.rejected}")
    print(f"Huecos de datos    : {s.gaps}")
    print(f"Con P1/P2/P3       : {s.p1_pct:.1f} % / {s.p2_pct:.1f} % / {s.p3_pct:.1f} %")
    print(
        f"t_lift             : mediana {_fmt(s.t_lift_median_ms, '.2f')} ms "
        f"(p5 {_fmt(s.t_lift_p5_ms, '.2f')} – p95 {_fmt(s.t_lift_p95_ms, '.2f')} ms)"
    )
    print(f"σ residuo de fase  : {_fmt(s.residual_sigma_us, '.1f')} µs")
    print(f"Enganche           : {_fmt(s.lock_time_s, '.2f')} s")
    print(f"Tiempo real        : {s.realtime_factor:.0f}x")
    print(
        f"Periodo            : {_fmt(s.period_ms, '.4f')} ms observado vs "
        f"{s.nominal_period_ms:.4f} ms nominal ({_fmt(s.period_deviation_pct, '+.3f')} %)"
    )
    par = {True: "sí", False: "NO", None: "n/d (error de batimiento ~0)"}[s.parity_ok]
    print(f"Tic/tac alternan   : {par}")
    if s.implied_amplitude_deg is not None:
        print(
            f"Plausibilidad      : amplitud implícita {_fmt(s.implied_amplitude_deg, '.0f')}° "
            "con el t_lift mediano (comprobación física, NO es una medida; ~200–330° sano)"
        )
    elif s.lift_angle_deg:
        print("Plausibilidad      : n/d (sin batimientos con P1 y P3 resueltos)")
    for w in s.warnings:
        print(f"AVISO: {w}")


def write_csv(path: Path, beats: list[Beat], fs: float) -> None:
    """One row per beat."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "k",
                "p1",
                "p2",
                "p3",
                "t_lift_ms",
                "n_subevents",
                "peak_dbfs",
                "snr_db",
                "residual_us",
                "confidence",
                "flags",
                "flag_names",
            ]
        )
        for b in beats:
            names = "|".join(f.name for f in BeatFlags if f and b.flags & f)
            w.writerow(
                [
                    b.k,
                    f"{b.p1:.4f}",
                    f"{b.p2:.4f}",
                    f"{b.p3:.4f}",
                    f"{b.t_lift_samples / fs * 1e3:.4f}",
                    b.n_subevents,
                    f"{b.peak_dbfs:.2f}",
                    f"{b.snr_db:.2f}",
                    f"{b.residual_samples / fs * 1e6:.2f}",
                    f"{b.confidence:.3f}",
                    int(b.flags),
                    names,
                ]
            )
    _dbg.file_write(path, rows=len(beats))


def write_json(path: Path, summary: Summary) -> None:
    """The summary as JSON (NaN/inf as null)."""
    path.parent.mkdir(parents=True, exist_ok=True)

    def clean(v: object) -> object:
        if isinstance(v, float) and not math.isfinite(v):
            return None
        return v

    data = {k: clean(v) for k, v in asdict(summary).items()}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    _dbg.file_write(path)


def write_plot(path: Path, beats: list[Beat], fs: float) -> bool:
    """Residual and t_lift over time as a PNG. Returns False if matplotlib is missing."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    t = np.array([b.reference for b in beats]) / fs
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    a1.plot(t, [b.residual_samples / fs * 1e6 for b in beats], ".", ms=3)
    a1.set_ylabel("residuo (µs)")
    a2.plot(t, [b.t_lift_samples / fs * 1e3 for b in beats], ".", ms=3)
    a2.set_ylabel("t_lift (ms)")
    a2.set_xlabel("tiempo (s)")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)
    _dbg.file_write(path)
    return True


# ---------------------------------------------------------------------- CLI
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m adv_tg.tools.detect_beats",
        description="Detecta batimientos (P1/P2/P3) en un archivo WAV o FLAC.",
    )
    p.add_argument("file", type=Path, help="archivo de audio (WAV o FLAC)")
    p.add_argument("--bph", type=float, required=True, help="batimientos por hora nominales")
    p.add_argument("--lift-angle", type=float, default=None, help="ángulo de levantamiento (°)")
    p.add_argument(
        "--chunk", type=int, default=DEFAULT_CHUNK, help=f"muestras por bloque ({DEFAULT_CHUNK})"
    )
    p.add_argument("--csv", type=Path, default=None, help="guarda una fila por batimiento")
    p.add_argument("--json", type=Path, default=None, help="guarda el resumen en JSON")
    p.add_argument("--plot", type=Path, default=None, help="PNG (requiere matplotlib)")
    return p


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the exit code."""
    try:  # never crash on consoles that cannot print e.g. "σ" (Windows cp1252)
        sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) and exc.code else EXIT_ERROR
    if args.chunk <= 0:
        print(f"ERROR: --chunk debe ser positivo (recibido {args.chunk})")
        return EXIT_ERROR
    if args.lift_angle is not None and not 0 < args.lift_angle < 90:
        print(f"ERROR: --lift-angle fuera de (0, 90): {args.lift_angle}")
        return EXIT_ERROR
    try:
        with _dbg.phase("detect_beats", file=str(args.file), bph=args.bph, chunk=args.chunk):
            summary, beats = analyze_file(args.file, args.bph, args.lift_angle, chunk=args.chunk)
    except (AudioFileError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return EXIT_ERROR
    print_summary(summary)
    fs = float(summary.sample_rate)
    if args.csv is not None:
        write_csv(args.csv, beats, fs)
        print(f"CSV guardado en '{args.csv}' ({len(beats)} filas).")
    if args.json is not None:
        write_json(args.json, summary)
        print(f"JSON guardado en '{args.json}'.")
    if args.plot is not None:
        if write_plot(args.plot, beats, fs):
            print(f"Gráfica guardada en '{args.plot}'.")
        else:
            print(
                "AVISO: --plot ignorado: matplotlib no está instalado "
                '(dependencia opcional: pip install -e ".[plot]").'
            )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
