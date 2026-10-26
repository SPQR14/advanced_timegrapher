"""Stored ADC clock calibrations: ``python -m adv_tg.tools.adc_calibration``.

Subcommands::

    list  [--device D]                     history per device (and the one that applies now)
    set   --device D --ppm X --sigma S     store a manual calibration (level 3)
          [--fs 48000] [--date ISO] [--note TEXT]
    forget --device D [--fs N]             delete the calibrations of a device
    show  FILE                             ADV_TG_* tags of a recording
    tag   FILE --device D                  stamp the stored calibration on an existing
                                           recording (aged to its recording date)

``--device`` takes the PortAudio name (``capture_check --list``) or its index. The file lives
in the user configuration folder (``adv_tg.utils.adc_calibration.config_dir``).

Exit codes: 0 = OK, 2 = input error.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from adv_tg.core.adc_clock import PPM_TO_S_PER_DAY, AdcDrift, drift_from_tags
from adv_tg.utils.adc_calibration import (
    Calibration,
    CalibrationStore,
    CalibrationStoreError,
    pick_best,
    recording_tags,
)
from adv_tg.utils.audio_files import AudioFileError, audio_info, read_tags, update_tags

__all__ = ["EXIT_ERROR", "EXIT_OK", "main", "resolve_device"]

EXIT_OK = 0
EXIT_ERROR = 2
DEFAULT_FS = 48000


def resolve_device(text: str) -> str:
    """A PortAudio index (digits) becomes its device name; anything else is the name."""
    t = text.strip()
    if not t.isdigit():
        return t
    from adv_tg.audio.capture import load_sounddevice

    try:
        return str(load_sounddevice().query_devices(int(t), "input")["name"]).strip()
    except Exception as exc:
        raise ValueError(f"no se encontró el dispositivo de entrada {t}: {exc}") from exc


def _parse_date(text: str | None) -> datetime:
    if not text:
        return datetime.now(UTC)
    try:
        t = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"fecha no válida '{text}' (usa ISO 8601, p. ej. 2026-10-26)") from exc
    return t if t.tzinfo else t.astimezone(UTC)


def _fmt(cal: Calibration, now: datetime) -> str:
    d, e = cal.drift, cal.effective(now)
    note = f" — {d.note}" if d.note else ""
    return (
        f"{cal.measured_at.astimezone():%Y-%m-%d %H:%M}  {cal.sample_rate} Hz  {d.ppm:+8.3f} ppm "
        f"± {d.uncertainty_ppm:.3f} (hoy ± {e.uncertainty_ppm:.3f})  nivel {int(d.level)} "
        f"{d.method}  [{-PPM_TO_S_PER_DAY * d.ppm:+.3f} s/d]{note}"
    )


def _cmd_list(store: CalibrationStore, device: str | None) -> int:
    recs = store.records(device)
    print(f"Archivo: {store.path}")
    if not recs:
        print("No hay calibraciones guardadas.")
        return EXIT_OK
    now = datetime.now(UTC)
    groups: dict[tuple[str, int], list[Calibration]] = {}
    for r in recs:
        groups.setdefault((r.device.strip().casefold(), r.sample_rate), []).append(r)
    for group in groups.values():
        best = pick_best(group, now)
        print(f"\n{group[0].device} @ {group[0].sample_rate} Hz ({len(group)} registro(s)):")
        for r in group:
            mark = "→" if r is best else " "
            print(f" {mark} {_fmt(r, now)}")
    print("\n→ = la que se aplica hoy (menor incertidumbre tras el envejecimiento del cristal).")
    return EXIT_OK


def _cmd_set(store: CalibrationStore, args: argparse.Namespace) -> int:
    drift = AdcDrift.manual(args.ppm, args.sigma, note=args.note or "")
    cal = Calibration(resolve_device(args.device), int(args.fs), drift, _parse_date(args.date))
    store.add(cal)
    print(f"Guardada: {cal.device} — {_fmt(cal, datetime.now(UTC))}")
    return EXIT_OK


def _cmd_forget(store: CalibrationStore, args: argparse.Namespace) -> int:
    device = resolve_device(args.device)
    n = store.forget(device, args.fs)
    print(f"Borradas {n} calibración(es) de '{device}'.")
    return EXIT_OK


def _cmd_show(path: Path) -> int:
    tags = {k: v for k, v in read_tags(path).items() if k.startswith("ADV_TG_")}
    if not tags:
        print(f"'{path}' no tiene etiquetas ADV_TG_*.")
        return EXIT_OK
    for k, v in sorted(tags.items()):
        print(f"{k}={v}")
    d = drift_from_tags(tags)
    if d is not None:
        print(
            f"→ reloj ADC: {d.ppm:+.3f} ± {d.uncertainty_ppm:.3f} ppm (nivel {int(d.level)}, "
            f"{d.method}); sesgo sin corregir {d.rate_bias_s_per_day:+.3f} s/d"
        )
    return EXIT_OK


def _cmd_tag(store: CalibrationStore, args: argparse.Namespace) -> int:
    path: Path = args.file
    fs = audio_info(path).sample_rate
    device = resolve_device(args.device)
    existing = read_tags(path)
    if args.date:
        when = _parse_date(args.date)
    elif "ADV_TG_RECORDED_AT" in existing:
        when = _parse_date(existing["ADV_TG_RECORDED_AT"])
    else:
        when = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        print(f"AVISO: sin fecha de grabación; se usa la de modificación ({when:%Y-%m-%d}).")
    tags, cal = recording_tags(device, fs, store, when)
    if cal is None:
        print(f"ERROR: no hay calibración guardada de '{device}' a {fs} Hz.")
        return EXIT_ERROR
    update_tags(path, tags)
    print(f"Etiquetado '{path}' con {cal.device} — {_fmt(cal, when)}")
    return EXIT_OK


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m adv_tg.tools.adc_calibration",
        description="Calibraciones guardadas del reloj de la tarjeta de sonido (ppm por dispositivo).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list", help="muestra las calibraciones guardadas")
    s.add_argument("--device", default=None, help="solo este dispositivo (nombre)")
    s = sub.add_parser("set", help="guarda una calibración manual (nivel 3)")
    s.add_argument("--device", required=True, help="nombre o índice PortAudio")
    s.add_argument("--ppm", type=float, required=True, help="error del ADC en ppm (> 0 = rápido)")
    s.add_argument("--sigma", type=float, required=True, help="incertidumbre total en ppm")
    s.add_argument("--fs", type=int, default=DEFAULT_FS, help=f"tasa de muestreo ({DEFAULT_FS})")
    s.add_argument("--date", default=None, help="fecha de la medida (ISO 8601; por defecto ahora)")
    s.add_argument("--note", default=None, help="nota libre (método, temperatura...)")
    s = sub.add_parser("forget", help="borra las calibraciones de un dispositivo")
    s.add_argument("--device", required=True, help="nombre o índice PortAudio")
    s.add_argument("--fs", type=int, default=None, help="solo esta tasa de muestreo")
    s = sub.add_parser("show", help="muestra las etiquetas ADV_TG_* de una grabación")
    s.add_argument("file", type=Path)
    s = sub.add_parser("tag", help="estampa la calibración guardada en una grabación existente")
    s.add_argument("file", type=Path)
    s.add_argument("--device", required=True, help="dispositivo con que se grabó")
    s.add_argument("--date", default=None, help="fecha de la grabación si el archivo no la lleva")
    return p


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the exit code."""
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) and exc.code else EXIT_ERROR
    store = CalibrationStore()
    try:
        if args.cmd == "list":
            return _cmd_list(store, args.device)
        if args.cmd == "set":
            return _cmd_set(store, args)
        if args.cmd == "forget":
            return _cmd_forget(store, args)
        if args.cmd == "show":
            return _cmd_show(args.file)
        return _cmd_tag(store, args)
    except (AudioFileError, CalibrationStoreError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
