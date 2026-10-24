"""Validate the beat detector on real recordings: ``python -m adv_tg.tools.check_samples``.

Finds every ``.wav``/``.flac`` under ``--dir`` (default ``docs/samples``, recursive), picks the
calibre from the file name (case-insensitive) with a built-in table, runs the detector
block by block (incremental path) and repeats it with another chunking (1024 vs. whole
file) to check chunk invariance. Prints and saves a Markdown table with a per-file
verdict.

Built-in table (a JSON ``--manifest`` takes precedence): a list of objects with ``pattern``
(substring or glob of the file name) or ``file`` (exact name), plus ``bph``, optional
``lift_angle`` and optional ``informational``::

    [{"pattern": "c07", "bph": 21600, "lift_angle": 50}]

==================  ==========  =====  ===============
name contains       BPH         LA     note
==================  ==========  =====  ===============
c07 / h50 / hamilton  21600     50°    weak P1 (ETA C07)
9015 / miyota       28800       51°    "normal" case
4r36 / seiko        21600       53°    slow ramp before the tac drop
nm10 / bulova       57600       —      quartz 16/s, informational only
vh31 / tmi          14400       —      quartz 4/s, informational only
h874 / citizen      3600        —      quartz 1/s, informational only
==================  ==========  =====  ===============

Verdict OK (non-informational files) when: count within +-1 % of the expected beats from
the first detected beat to the end, P3 in >= 95 % of the beats, tic/tac alternate without
parity jumps, the median t_lift is physically possible (amplitude 90-400°; not judged when
P1 is resolved in < 50 % of the beats, e.g. the weak P1 of the ETA C07) and the result is
chunk-invariant. Otherwise REVISAR. Informational files never fail.

``--self-test`` generates three synthetic files (WAV and FLAC) whose names match the table
in a temporary directory and checks them, so the tool can be exercised without recordings.

Exit codes: 0 = every checked file OK, 1 = some file needs review, 2 = input error.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import math
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from adv_tg.core.beat_types import beat_period_s, lift_time_s
from adv_tg.tools.detect_beats import Summary, analyze_file, beats_equal, detect_whole_file
from adv_tg.utils.audio_files import SUPPORTED_EXTENSIONS, AudioFileError, write_audio
from adv_tg.utils.debugger import Debugger

__all__ = ["BUILTIN_TABLE", "FileResult", "check_file", "discover", "main", "resolve_config"]

_dbg = Debugger.get(__name__)

EXIT_OK = 0
EXIT_REVIEW = 1
EXIT_ERROR = 2
COUNT_TOLERANCE = 0.01
MIN_P3_PCT = 95.0
MIN_P1_PCT_FOR_LIFT = 50.0
MIN_FINE_PCT = 80.0
AMPLITUDE_RANGE = (90.0, 400.0)


@dataclass(frozen=True)
class CalibreConfig:
    """Calibre used for one file."""

    bph: float
    lift_angle: float | None
    informational: bool
    source: str


BUILTIN_TABLE: tuple[tuple[tuple[str, ...], CalibreConfig], ...] = (
    (("c07", "h50", "hamilton"), CalibreConfig(21600, 50.0, False, "tabla: ETA C07 / H-50")),
    (("9015", "miyota"), CalibreConfig(28800, 51.0, False, "tabla: Miyota 9015")),
    (("4r36", "seiko"), CalibreConfig(21600, 53.0, False, "tabla: Seiko 4R36")),
    (("nm10", "bulova"), CalibreConfig(57600, None, True, "tabla: Bulova cuarzo (informativo)")),
    (("vh31", "tmi"), CalibreConfig(14400, None, True, "tabla: TMI VH31 cuarzo 4/s (informativo)")),
    (
        ("h874", "citizen"),
        CalibreConfig(3600, None, True, "tabla: Citizen H874 cuarzo 1/s (informativo)"),
    ),
)


@dataclass
class FileResult:
    """Outcome for one file."""

    path: Path
    config: CalibreConfig
    summary: Summary | None
    invariant: bool | None
    expected_from_first: float
    verdict: str
    reasons: list[str]
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------- discovery
def discover(directory: Path) -> list[Path]:
    """Audio files under ``directory`` (recursive, sorted)."""
    return sorted(
        p for p in directory.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def _load_manifest(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _dbg.file_read(path, ok=False, error=exc)
        raise ValueError(f"no se pudo leer el manifiesto '{path}': {exc}") from exc
    _dbg.file_read(path, entries=len(data) if isinstance(data, list) else 0)
    if not isinstance(data, list):
        raise ValueError(f"el manifiesto '{path}' debe ser una lista de objetos")
    for i, e in enumerate(data):
        if not isinstance(e, dict) or "bph" not in e or not ({"pattern", "file"} & set(e)):
            raise ValueError(f"entrada {i} del manifiesto sin 'bph' y 'pattern'/'file': {e}")
    return data


def resolve_config(path: Path, manifest: list[dict] | None = None) -> CalibreConfig | None:
    """Calibre for a file: manifest first, then the built-in table, else None."""
    name = path.name.lower()
    for e in manifest or []:
        if "file" in e:
            hit = name == str(e["file"]).lower() or path.as_posix().endswith(str(e["file"]))
        else:
            pat = str(e["pattern"]).lower()
            hit = fnmatch.fnmatch(name, pat) if any(ch in pat for ch in "*?[") else pat in name
        if hit:
            la = e.get("lift_angle")
            return CalibreConfig(
                float(e["bph"]),
                float(la) if la is not None else None,
                bool(e.get("informational", False)),
                "manifiesto",
            )
    for keys, cfg in BUILTIN_TABLE:
        if any(k in name for k in keys):
            return cfg
    return None


# ---------------------------------------------------------------------- checks
def check_file(path: Path, cfg: CalibreConfig) -> FileResult:
    """Run the detector on one file and apply the verdict criteria."""
    try:
        summary, beats = analyze_file(path, cfg.bph, cfg.lift_angle, chunk=4096)
        b1024 = detect_whole_file(path, cfg.bph, cfg.lift_angle, chunk=1024)
        ball = detect_whole_file(path, cfg.bph, cfg.lift_angle, chunk=None)
    except (AudioFileError, ValueError) as exc:
        return FileResult(path, cfg, None, None, math.nan, "ERROR", [str(exc)])
    invariant = beats_equal(beats, ball) and beats_equal(b1024, ball)
    first = summary.first_beat_s if summary.first_beat_s is not None else summary.duration_s
    expected = max(0.0, summary.duration_s - first) / beat_period_s(cfg.bph)
    reasons: list[str] = []
    notes: list[str] = []
    if cfg.informational:
        return FileResult(path, cfg, summary, invariant, expected, "INFO", reasons)
    if abs(summary.found - expected) > max(COUNT_TOLERANCE * expected, 1.5):
        reasons.append(f"conteo {summary.found} vs {expected:.1f} esperados (>±1 %)")
    if summary.p3_pct < MIN_P3_PCT:
        reasons.append(f"P3 en {summary.p3_pct:.1f} % (< {MIN_P3_PCT:g} %)")
    if summary.parity_ok is False:
        reasons.append("saltos de paridad tic/tac")
    la = cfg.lift_angle or 52.0
    tl = summary.t_lift_median_ms / 1e3
    t_lo = lift_time_s(AMPLITUDE_RANGE[1], la, cfg.bph)
    t_hi = lift_time_s(AMPLITUDE_RANGE[0], la, cfg.bph)
    if summary.p1_pct < MIN_P1_PCT_FOR_LIFT or not math.isfinite(tl):
        # e.g. ETA C07: P1 too weak to resolve; t_lift cannot be judged (not a failure)
        notes.append(f"t_lift n/d: P1 solo en {summary.p1_pct:.0f} % de los batimientos")
    elif not t_lo <= tl <= t_hi:
        reasons.append("t_lift mediano fuera del rango físico")
    if summary.fine_pct < MIN_FINE_PCT or not math.isfinite(summary.fine_pct):
        reasons.append(
            f"alineación fina en {_f(summary.fine_pct, '.0f')} % de los batimientos "
            f"(< {MIN_FINE_PCT:g} %)"
        )
    amp_tpl = summary.template_amplitude_deg
    if amp_tpl is not None and not AMPLITUDE_RANGE[0] <= amp_tpl <= AMPLITUDE_RANGE[1]:
        reasons.append(f"t_lift de las plantillas fuera del rango físico ({amp_tpl:.0f}°)")
    if not invariant:
        reasons.append("no invariante al troceado")
    verdict = "REVISAR" if reasons else "OK"
    return FileResult(path, cfg, summary, invariant, expected, verdict, reasons, notes)


# ---------------------------------------------------------------------- report
def _f(v: float | None, spec: str) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "—"
    return format(v, spec)


_HEADER = (
    "| archivo | formato | fs | duración (s) | BPH/LA | esperados | encontrados | perdidos "
    "| rechazados | % P1/P2/P3 | t_lift med (ms) | amplitud implícita (°) [plausib.] "
    "| σ residuo (µs) | t_fine % · σ par/impar (µs) | desfase P1 tic/tac (ms) "
    "| t_lift plantillas (ms) · amplitud (°) "
    "| tic/tac sin saltos | invariante | tiempo real | avisos | veredicto |"
)


def _row(r: FileResult, base: Path) -> str:
    try:
        name = r.path.relative_to(base).as_posix()
    except ValueError:
        name = r.path.name
    la = f"{r.config.lift_angle:g}" if r.config.lift_angle else "—"
    s = r.summary
    if s is None:
        return f"| {name} | — | — | — | {r.config.bph:g}/{la} |" + " — |" * 15 + f" {r.verdict} |"
    par = {True: "sí", False: "no", None: "n/d"}[s.parity_ok]
    inv = {True: "sí", False: "no", None: "—"}[r.invariant]
    warn = "; ".join(s.warnings + r.reasons + r.notes) or "—"
    amp = _f(s.implied_amplitude_deg, ".0f") if r.config.lift_angle else "—"
    fine = (
        f"{_f(s.fine_pct, '.0f')} · {_f(s.fine_sigma_even_us, '.0f')}"
        f"/{_f(s.fine_sigma_odd_us, '.0f')}"
    )
    tpl = (
        f"{_f(s.template_t_lift_even_ms, '.2f')}/{_f(s.template_t_lift_odd_ms, '.2f')} · "
        f"{_f(s.template_amplitude_deg, '.0f') if r.config.lift_angle else '—'}"
        f" ({s.template_p3_method})"
    )
    return (
        f"| {name} | {s.format}/{s.subtype} | {s.sample_rate} | {s.duration_s:.1f} "
        f"| {r.config.bph:g}/{la} | {_f(r.expected_from_first, '.1f')} | {s.found} | {s.missed} "
        f"| {s.rejected} | {s.p1_pct:.0f}/{s.p2_pct:.0f}/{s.p3_pct:.0f} "
        f"| {_f(s.t_lift_median_ms, '.2f')} | {amp} | {_f(s.residual_sigma_us, '.1f')} "
        f"| {fine} | {_f(s.fine_beat_offset_ms, '.2f')} | {tpl} "
        f"| {par} | {inv} | {s.realtime_factor:.0f}x | {warn} | **{r.verdict}** |"
    )


def render(results: list[FileResult], unconfigured: list[Path], base: Path) -> str:
    """Markdown report."""
    lines = [
        f"# check_samples — {datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        f"Directorio: `{base}`",
        "",
        "Esperados = (duración - instante del primer batimiento) × BPH / 3600. "
        "La amplitud implícita y las columnas de alineación fina y plantillas son "
        "comprobaciones, no medidas (las métricas son la fase 3).",
        "",
        _HEADER,
        "|" + "---|" * 21,
    ]
    lines += [_row(r, base) for r in results]
    if unconfigured:
        lines += ["", "Sin configuración (no se analizaron):", ""]
        lines += [f"- `{p.name}`" for p in unconfigured]
    if not results and not unconfigured:
        lines += ["", "No se encontraron archivos .wav/.flac."]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- self-test
def _selftest_signal(
    *,
    bph: float,
    lift_angle: float,
    amplitude: float,
    fs: int,
    seconds: float,
    snr_db: float,
    p1_gain: float,
    beat_error_ms: float,
    seed: int,
    single_burst: bool = False,
) -> np.ndarray:
    """Small synthetic watch (three damped bursts per beat, evaluated in continuous time)."""
    rng = np.random.default_rng(seed)
    n = int(seconds * fs)
    x = np.zeros(n)
    period = beat_period_s(bph) * (1.0 - 3.0 / 86400.0)
    t_lift = lift_time_s(amplitude, lift_angle, bph)
    bursts = ((0.0, 0.6 * p1_gain, 6500.0), (0.45, 0.35, 4800.0), (1.0, 1.0, 3600.0))
    if single_burst:
        bursts = ((0.0, 1.0, 5000.0),)
    t = rng.uniform(0.02, period)
    k = 0
    while t + t_lift + 0.005 < seconds:
        t0 = t + 0.5e-3 * beat_error_ms * (-1) ** k
        for frac, level, f0 in bursts:
            tb = t0 + frac * t_lift
            i = np.arange(math.ceil(tb * fs), min(n, math.floor((tb + 0.004) * fs) + 1))
            tau = i / fs - tb
            x[i] += 0.5 * level * np.exp(-tau / 4e-4) * np.sin(2 * np.pi * f0 * tau)
        t += period
        k += 1
    return x + rng.standard_normal(n) * 0.5 * 10.0 ** (-snr_db / 20.0)


def make_selftest_files(directory: Path) -> list[Path]:
    """Write the three self-test recordings into ``directory``."""
    specs = [
        (
            "selftest_miyota_9015.wav",
            dict(
                bph=28800,
                lift_angle=51.0,
                amplitude=270.0,
                fs=48000,
                p1_gain=1.0,
                beat_error_ms=0.4,
            ),
            None,
        ),
        (
            "selftest_hamilton_c07_h50.flac",
            dict(
                bph=21600,
                lift_angle=50.0,
                amplitude=260.0,
                fs=44100,
                p1_gain=0.08,
                beat_error_ms=0.7,
            ),
            "PCM_24",
        ),
        (
            "selftest_bulova_nm10.flac",
            dict(
                bph=57600,
                lift_angle=52.0,
                amplitude=300.0,
                fs=48000,
                p1_gain=1.0,
                beat_error_ms=0.0,
                single_burst=True,
            ),
            "PCM_16",
        ),
    ]
    out = []
    for i, (name, kw, subtype) in enumerate(specs):
        x = _selftest_signal(seconds=20.0, snr_db=28.0, seed=100 + i, **kw)
        out.append(
            write_audio(
                directory / name,
                x,
                int(kw["fs"]),
                subtype=subtype,
                comment="adv_tg check_samples --self-test",
            )
        )
    return out


# ---------------------------------------------------------------------- CLI
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m adv_tg.tools.check_samples",
        description="Valida el detector de batimientos con grabaciones reales.",
    )
    p.add_argument("--dir", type=Path, default=Path("docs/samples"), help="directorio de audio")
    p.add_argument("--manifest", type=Path, default=None, help="JSON con BPH/LA por archivo")
    p.add_argument("--out", type=Path, default=None, help="informe Markdown de salida")
    p.add_argument("--self-test", action="store_true", help="genera sintéticos y los comprueba")
    return p


def run(directory: Path, manifest: list[dict] | None, out: Path) -> int:
    """Check every file in ``directory``, print and save the report; returns the exit code."""
    files = discover(directory)
    results: list[FileResult] = []
    unconfigured: list[Path] = []
    with _dbg.phase("check_samples", dir=str(directory), files=len(files)):
        for path in files:
            cfg = resolve_config(path, manifest)
            if cfg is None:
                unconfigured.append(path)
                continue
            print(f"Analizando {path.name} ({cfg.bph:g} BPH, {cfg.source}) ...")
            results.append(check_file(path, cfg))
    report = render(results, unconfigured, directory)
    print()
    print(report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    _dbg.file_write(out, files=len(results))
    print(f"Informe guardado en '{out}'.")
    bad = [r for r in results if r.verdict in ("REVISAR", "ERROR")]
    return EXIT_REVIEW if bad else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the exit code."""
    try:  # never crash on consoles that cannot print e.g. "σ" (Windows cp1252)
        sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) and exc.code else EXIT_ERROR
    out = args.out or Path("logs") / f"check_samples_{datetime.now():%Y%m%d_%H%M%S}.md"
    manifest = None
    if args.manifest is not None:
        try:
            manifest = _load_manifest(args.manifest)
        except ValueError as exc:
            print(f"ERROR: {exc}")
            return EXIT_ERROR
    if args.self_test:
        with tempfile.TemporaryDirectory(prefix="adv_tg_selftest_") as tmp:
            d = Path(tmp)
            make_selftest_files(d)
            return run(d, manifest, out)
    if not args.dir.is_dir():
        print(f"ERROR: no existe el directorio '{args.dir}'.")
        print("Copia ahí las grabaciones (.wav/.flac) o indica otro con --dir.")
        return EXIT_ERROR
    return run(args.dir, manifest, out)


if __name__ == "__main__":
    sys.exit(main())
