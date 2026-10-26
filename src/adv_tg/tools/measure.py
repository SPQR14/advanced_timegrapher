"""Rate, beat error and amplitude of a recording: ``python -m adv_tg.tools.measure FILE``.

Reads WAV or FLAC block by block (``adv_tg.utils.audio_files``), runs the beat detector
(layer 2) and computes the layer-3 metrics (``adv_tg.core.metrics``): rate with its
statistical uncertainty, rate per window (``--window``, 10 s) and its trend, beat error and
amplitude (tic, tac and mean), plus warnings and a confidence score.

Options: ``--json`` all figures, ``--csv`` one row per window, ``--plot`` a PNG (needs the
optional extra: ``pip install -e ".[plot]"``), ``--quartz`` rate only.

ADC clock correction (layer 4), first match wins (``resolve_adc_drift``):

1. ``--adc-drift-ppm`` / ``--adc-drift-sigma-ppm`` (``--adc-drift-ppm 0`` = force none);
2. the ``ADV_TG_ADC_DRIFT_*`` tags of the file and, with ``--device NAME``, the calibration
   stored for that device (``adv_tg.utils.adc_calibration``, aged to the recording date): the
   one with the smallest uncertainty;
3. none (rate flagged as not calibrated).

Exit codes: 0 = measured (even if some figure is not available), 2 = input error.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from adv_tg.core.adc_clock import ClockLevel, drift_from_tags
from adv_tg.core.beat_detector import BeatDetector, BeatDetectorConfig
from adv_tg.core.beat_types import Beat, ParityTemplate
from adv_tg.core.metrics import (
    Metrics,
    MetricsConfig,
    compute_metrics,
    fit_parity_weighted,
    format_beat_error_ms,
    select_beats,
)
from adv_tg.utils.adc_calibration import CalibrationStore, CalibrationStoreError
from adv_tg.utils.audio_files import AudioFileError, audio_info, iter_audio_blocks, read_tags
from adv_tg.utils.debugger import Debugger

__all__ = [
    "EXIT_ERROR",
    "EXIT_OK",
    "MeasureResult",
    "main",
    "measure_file",
    "print_report",
    "resolve_adc_drift",
    "write_csv",
    "write_json",
    "write_plot",
]

_dbg = Debugger.get(__name__)

EXIT_OK = 0
EXIT_ERROR = 2
DEFAULT_CHUNK = 4096


@dataclass
class MeasureResult:
    """Everything measured on one file."""

    file: str
    sample_rate: int
    duration_s: float
    bph: float
    lift_angle_deg: float | None
    quartz: bool
    metrics: Metrics
    beats: list[Beat]
    templates: tuple[ParityTemplate | None, ParityTemplate | None]
    detect_s: float
    metrics_s: float
    adc_drift_ppm: float = 0.0
    adc_drift_sigma_ppm: float = 0.0
    adc_drift_source: str = ""

    @property
    def realtime_factor(self) -> float:
        busy = self.detect_s + self.metrics_s
        return self.duration_s / busy if busy > 0 else math.inf


def measure_file(
    path: Path | str,
    bph: float,
    lift_angle_deg: float | None = None,
    *,
    quartz: bool = False,
    window_s: float = 10.0,
    window_step_s: float | None = None,
    adc_drift_ppm: float = 0.0,
    adc_drift_sigma_ppm: float = 0.0,
    adc_drift_source: str = "",
    chunk: int = DEFAULT_CHUNK,
) -> MeasureResult:
    """Detect beats block by block and compute the metrics of the whole file.

    Raises:
        AudioFileError: Missing, unsupported or corrupt file.
        ValueError: Invalid parameters.
    """
    p = Path(path)
    info = audio_info(p)
    det = BeatDetector(
        BeatDetectorConfig(
            sample_rate=info.sample_rate,
            bph=bph,
            lift_angle_deg=lift_angle_deg or 52.0,
            quartz=quartz,
        )
    )
    mcfg = MetricsConfig(
        sample_rate=info.sample_rate,
        bph=bph,
        lift_angle_deg=lift_angle_deg,
        quartz=quartz,
        window_s=window_s,
        window_step_s=window_step_s,
        adc_drift_ppm=adc_drift_ppm,
        adc_drift_sigma_ppm=adc_drift_sigma_ppm,
    )
    beats: list[Beat] = []
    frames = 0
    busy = 0.0
    for start, block in iter_audio_blocks(p, chunk, channel="max_energy"):
        t0 = time.perf_counter()
        beats += det.process(block, start)
        busy += time.perf_counter() - t0
        frames = start + block.shape[0]
    if frames == 0:
        raise AudioFileError(f"el archivo '{p}' no contiene muestras")
    t0 = time.perf_counter()
    beats += det.flush()
    busy += time.perf_counter() - t0
    templates = det.templates()
    t0 = time.perf_counter()
    metrics = compute_metrics(beats, templates, mcfg)
    metrics_s = time.perf_counter() - t0
    return MeasureResult(
        file=str(p),
        sample_rate=info.sample_rate,
        duration_s=frames / info.sample_rate,
        bph=bph,
        lift_angle_deg=lift_angle_deg,
        quartz=quartz,
        metrics=metrics,
        beats=beats,
        templates=templates,
        detect_s=busy,
        metrics_s=metrics_s,
        adc_drift_ppm=adc_drift_ppm,
        adc_drift_sigma_ppm=adc_drift_sigma_ppm,
        adc_drift_source=adc_drift_source,
    )


def _date(text: str | None) -> str:
    return text[:10] if text else "fecha desconocida"


def resolve_adc_drift(
    path: Path | str,
    *,
    ppm: float | None = None,
    sigma_ppm: float | None = None,
    device: str | None = None,
    store: CalibrationStore | None = None,
) -> tuple[float, float, str]:
    """ADC clock correction for a file (see the module docstring for the order).

    Returns:
        ``(ppm, total sigma in ppm, description of the source in Spanish)``.

    Raises:
        AudioFileError: Unreadable file.
        ValueError: ``sigma_ppm`` without ``ppm``, or malformed drift tags.
        CalibrationStoreError: ``device`` given and the store is unreadable.
    """
    if ppm is not None:
        return float(ppm), float(sigma_ppm or 0.0), "línea de órdenes"
    if sigma_ppm is not None:
        raise ValueError("--adc-drift-sigma-ppm requiere --adc-drift-ppm")
    tags = read_tags(path)
    candidates: list[tuple[float, float, str]] = []
    tagged = drift_from_tags(tags)
    if tagged is not None and tagged.level != ClockLevel.UNCALIBRATED:
        cal_at = tags.get("ADV_TG_ADC_DRIFT_CALIBRATED_AT")
        candidates.append(
            (
                tagged.ppm,
                tagged.uncertainty_ppm,
                f"etiquetas del archivo (nivel {int(tagged.level)}, {tagged.method}, "
                f"calibrado el {_date(cal_at)})",
            )
        )
    if device:
        fs = audio_info(path).sample_rate
        recorded = tags.get("ADV_TG_RECORDED_AT")
        when = None
        if recorded:
            try:
                when = datetime.fromisoformat(recorded)
                when = when if when.tzinfo else when.replace(tzinfo=UTC)
            except ValueError:
                when = None
        if when is None:
            when = datetime.fromtimestamp(Path(path).stat().st_mtime, UTC)
        cal = (store or CalibrationStore()).best(device, fs, when)
        if cal is not None:
            d = cal.effective(when)
            candidates.append(
                (
                    d.ppm,
                    d.uncertainty_ppm,
                    f"calibración guardada de '{cal.device}' (nivel {int(d.level)}, {d.method}, "
                    f"del {cal.measured_at:%Y-%m-%d})",
                )
            )
        elif not candidates:
            return 0.0, 0.0, f"sin calibrar (no hay calibración de '{device}' a {fs} Hz)"
    if not candidates:
        return 0.0, 0.0, "sin calibrar"
    return min(candidates, key=lambda c: c[1])


# ---------------------------------------------------------------------- output
def _f(v: float, spec: str, none: str = "—") -> str:
    return format(v, spec) if math.isfinite(v) else none


def print_report(r: MeasureResult) -> None:
    """Human-readable report (Spanish)."""
    m = r.metrics
    la = f"{r.lift_angle_deg:g}°" if r.lift_angle_deg else f"{m.lift_angle_deg:g}° (por defecto)"
    print("===== MEDIDA =====")
    print(f"Archivo            : {r.file}")
    print(f"Duración           : {r.duration_s:.2f} s ({r.sample_rate} Hz)")
    kind = "cuarzo (solo marcha)" if r.quartz else f"LA {la}"
    print(f"Calibre            : {r.bph:g} BPH, {kind}")
    print(
        f"Batimientos        : {m.n_beats} detectados; usados {m.n_used_even} par / "
        f"{m.n_used_odd} impar; excluidos {m.n_excluded} ({100 * m.excluded_fraction:.0f} %)"
    )
    cal = "calibrada" if m.rate_calibrated else "sin calibrar"
    print(
        f"Marcha             : {_f(m.rate_s_per_day, '+.2f')} ± "
        f"{_f(m.rate_uncertainty_s_per_day, '.2f')} s/d ({cal}; σ covarianza "
        f"{_f(m.rate_sigma_s_per_day, '.2f')}, σ bloques {_f(m.rate_sigma_block_s_per_day, '.2f')})"
    )
    if r.adc_drift_source:
        print(
            f"Reloj ADC          : {r.adc_drift_ppm:+.2f} ± {r.adc_drift_sigma_ppm:.2f} ppm "
            f"({r.adc_drift_source})"
        )
    if m.windows:
        ws = ", ".join(f"{w.rate_s_per_day:+.1f}" for w in m.windows)
        print(
            f"Marcha por ventanas: [{ws}] s/d (ventanas de {m.windows[0].end_s - m.windows[0].start_s:g} s)"
        )
    flag = "  ← AVISO" if m.rate_trend_flag else ""
    print(
        f"Tendencia          : {_f(m.rate_trend_s_per_day_per_min, '+.2f')} s/d por minuto "
        f"(± {_f(m.rate_trend_sigma_s_per_day_per_min, '.2f')}){flag}"
    )
    if not r.quartz:
        print(
            f"Error de batimiento: {format_beat_error_ms(m.beat_error_ms)} "
            f"({_f(m.beat_error_ms, '.2f')} ± {_f(m.beat_error_uncertainty_ms, '.2f')} ms, "
            f"medido en {m.beat_error_point})"
        )
        ind = " (orientativa)" if m.amplitude_indicative else ""
        print(
            f"Amplitud           : {_f(m.amplitude_deg, '.0f')}°{ind} "
            f"(tic {_f(m.amplitude_tic_deg, '.0f')}°, tac {_f(m.amplitude_tac_deg, '.0f')}°; "
            f"± {_f(m.amplitude_la_uncertainty_deg, '.0f')}° por ±1° de LA)"
        )
    print(f"Jitter par/impar   : {_f(m.sigma_even_us, '.0f')} / {_f(m.sigma_odd_us, '.0f')} µs")
    print(f"Confianza          : {m.confidence:.2f} (ordenación, no probabilidad)")
    print(f"Tiempo real        : {r.realtime_factor:.0f}x (detector + métricas)")
    for w in m.warnings:
        print(f"AVISO: {w}")


def _clean(v: object) -> object:
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v


def write_json(path: Path, r: MeasureResult) -> None:
    """All figures as JSON (NaN/inf as null)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "file": r.file,
        "sample_rate": r.sample_rate,
        "duration_s": r.duration_s,
        "bph": r.bph,
        "lift_angle_deg": r.lift_angle_deg,
        "quartz": r.quartz,
        "realtime_factor": r.realtime_factor,
        "adc_drift_ppm": r.adc_drift_ppm,
        "adc_drift_sigma_ppm": r.adc_drift_sigma_ppm,
        "adc_drift_source": r.adc_drift_source,
        "metrics": asdict(r.metrics),
    }
    path.write_text(json.dumps(_clean(data), indent=2, ensure_ascii=False), encoding="utf-8")
    _dbg.file_write(path)


def write_csv(path: Path, r: MeasureResult) -> None:
    """One row per rate window."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "start_s",
                "end_s",
                "center_s",
                "rate_s_per_day",
                "rate_sigma_s_per_day",
                "rate_uncertainty_s_per_day",
                "n_beats",
            ]
        )
        for win in r.metrics.windows:
            w.writerow(
                [
                    f"{win.start_s:.3f}",
                    f"{win.end_s:.3f}",
                    f"{win.center_s:.3f}",
                    f"{win.rate_s_per_day:.3f}",
                    f"{win.rate_sigma_s_per_day:.3f}",
                    f"{win.rate_uncertainty_s_per_day:.3f}",
                    win.n_beats,
                ]
            )
    _dbg.file_write(path, rows=len(r.metrics.windows))


def write_plot(path: Path, r: MeasureResult) -> bool:
    """Window rates with the trend, and the per-parity residuals. False if no matplotlib."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    m = r.metrics
    fs = float(r.sample_rate)
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 6))
    if m.windows:
        x = np.array([w.center_s for w in m.windows])
        y = np.array([w.rate_s_per_day for w in m.windows])
        e = np.array([w.rate_uncertainty_s_per_day for w in m.windows])
        a1.errorbar(x, y, yerr=e, fmt="o", capsize=3)
        if math.isfinite(m.rate_trend_s_per_day_per_min):
            xm = float(np.mean(x))
            a1.plot(x, np.mean(y) + m.rate_trend_s_per_day_per_min * (x - xm) / 60.0, "--")
    if math.isfinite(m.rate_s_per_day):
        a1.axhline(m.rate_s_per_day, color="0.5", lw=0.8)
    a1.set_ylabel("marcha (s/d)")
    a1.set_xlabel("tiempo (s)")
    k, t, _ = select_beats(r.beats)
    fit = fit_parity_weighted(k, t, min_per_parity=3)
    if fit is not None:
        res = t - (fit.a + fit.period * (k - fit.k_ref) + fit.c * np.where(k % 2 == 0, 1, -1))
        for parity, lab in ((0, "par (tic)"), (1, "impar (tac)")):
            sel = (k.astype(np.int64) % 2) == parity
            a2.plot(t[sel] / fs, res[sel] / fs * 1e6, ".", ms=3, label=lab)
        a2.legend()
    a2.set_ylabel("residuo de t_fine (µs)")
    a2.set_xlabel("tiempo (s)")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)
    _dbg.file_write(path)
    return True


# ---------------------------------------------------------------------- CLI
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m adv_tg.tools.measure",
        description="Mide marcha, error de batimiento y amplitud de un archivo WAV o FLAC.",
    )
    p.add_argument("file", type=Path, help="archivo de audio (WAV o FLAC)")
    p.add_argument("--bph", type=float, required=True, help="batimientos por hora nominales")
    p.add_argument("--lift-angle", type=float, default=None, help="ángulo de levantamiento (°)")
    p.add_argument("--quartz", action="store_true", help="reloj de cuarzo: solo la marcha")
    p.add_argument("--window", type=float, default=10.0, help="ventana de marcha en s (10)")
    p.add_argument(
        "--window-step",
        type=float,
        default=None,
        help="paso entre ventanas en s (por defecto = --window; menor = ventanas solapadas)",
    )
    p.add_argument(
        "--adc-drift-ppm",
        type=float,
        default=None,
        help="error del reloj de la tarjeta (ppm); por defecto, el de las etiquetas del archivo"
        " o de --device (0 = sin corrección)",
    )
    p.add_argument(
        "--adc-drift-sigma-ppm",
        type=float,
        default=None,
        help="incertidumbre del error del reloj de la tarjeta (ppm)",
    )
    p.add_argument(
        "--device",
        default=None,
        help="nombre del dispositivo con que se grabó: usa su calibración guardada",
    )
    p.add_argument(
        "--chunk", type=int, default=DEFAULT_CHUNK, help=f"muestras por bloque ({DEFAULT_CHUNK})"
    )
    p.add_argument("--json", type=Path, default=None, help="guarda todas las cifras en JSON")
    p.add_argument("--csv", type=Path, default=None, help="guarda una fila por ventana")
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
    try:
        ppm, sigma, source = resolve_adc_drift(
            args.file,
            ppm=args.adc_drift_ppm,
            sigma_ppm=args.adc_drift_sigma_ppm,
            device=args.device,
        )
    except (AudioFileError, ValueError, CalibrationStoreError) as exc:
        print(f"ERROR: {exc}")
        return EXIT_ERROR
    try:
        with _dbg.phase("measure", file=str(args.file), bph=args.bph):
            r = measure_file(
                args.file,
                args.bph,
                args.lift_angle,
                quartz=args.quartz,
                window_s=args.window,
                window_step_s=args.window_step,
                adc_drift_ppm=ppm,
                adc_drift_sigma_ppm=sigma,
                adc_drift_source=source,
                chunk=args.chunk,
            )
    except (AudioFileError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return EXIT_ERROR
    print_report(r)
    if args.json is not None:
        write_json(args.json, r)
        print(f"JSON guardado en '{args.json}'.")
    if args.csv is not None:
        write_csv(args.csv, r)
        print(f"CSV guardado en '{args.csv}' ({len(r.metrics.windows)} ventanas).")
    if args.plot is not None:
        if write_plot(args.plot, r):
            print(f"Gráfica guardada en '{args.plot}'.")
        else:
            print(
                "AVISO: --plot ignorado: matplotlib no está instalado "
                '(dependencia opcional: pip install -e ".[plot]").'
            )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
