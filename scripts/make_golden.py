#!/usr/bin/env python3
"""Write the golden files of the C++ migration (tests/golden/, protocol in protocol.py).

    python scripts/make_golden.py            # all recordings, native engine (required)
    python scripts/make_golden.py --check    # compare instead of writing (exit 1 on change)

The files freeze the figures of the current release: regenerate them ONLY when a change of
figures has been validated on purpose, and say so in the commit.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from adv_tg import __version__  # noqa: E402
from adv_tg import native_bridge as nb  # noqa: E402
from tests.golden import protocol as P  # noqa: E402
from tests.golden import runner as R  # noqa: E402

GOLDEN = ROOT / "tests" / "golden"


def _num(v: object) -> str:
    if v is None:
        return "nan"
    return repr(v)  # shortest round-trip text of a float (strtod gives the same double)


def write_csv(path: Path, rows: list[dict], fields: tuple[str, ...] | list[str]) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(fields)
        for r in rows:
            w.writerow([_num(r[k]) for k in fields])


def write_json(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, indent=1, allow_nan=False) + "\n")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, allow_nan=False) + "\n" for r in rows))


def produce() -> dict[Path, str]:
    """Every golden file as {path: text}."""
    import tempfile

    out: dict[Path, str] = {}
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        for name in P.WATCHES:
            stem = Path(name).stem
            beats, metrics = R.run_batch(name)
            live = R.run_live(name)
            write_csv(t / "b.csv", beats, P.BEAT_FIELDS)
            write_json(t / "m.json", metrics)
            write_jsonl(t / "l.jsonl", live)
            out[GOLDEN / stem / "beats.csv"] = (t / "b.csv").read_text()
            out[GOLDEN / stem / "metrics.json"] = (t / "m.json").read_text()
            out[GOLDEN / stem / "live.jsonl"] = (t / "l.jsonl").read_text()
            print(f"  {name}: {len(beats)} beats, {len(live)} live snapshots", flush=True)
        for name in P.PPS:
            pulses = R.run_pps(name)
            write_csv(t / "p.csv", pulses, ["index", "snr_db", "peak"])
            out[GOLDEN / Path(name).stem / "pulses.csv"] = (t / "p.csv").read_text()
            print(f"  {name}: {len(pulses)} pulses", flush=True)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--check", action="store_true", help="compare with the stored files")
    a = p.parse_args(argv)
    if not nb.is_native_available():
        print("ERROR: the golden files are produced with the native engine", file=sys.stderr)
        return 2
    files = produce()
    if a.check:
        changed = [str(f.relative_to(ROOT)) for f, text in files.items()
                   if not f.exists() or f.read_text() != text]  # fmt: skip
        for c in changed:
            print(f"CHANGED: {c}")
        return 1 if changed else 0
    for f, text in files.items():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    try:
        sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True)
    except (OSError, subprocess.CalledProcessError):
        sha = "?"
    write_json(
        GOLDEN / "manifest.json",
        {
            "app_version": __version__,
            "git": sha.strip(),
            "engine": nb.native_arch_info(),
            "watches": {k: list(v) for k, v in P.WATCHES.items()},
            "pps": list(P.PPS),
            "files": sorted(str(f.relative_to(GOLDEN)) for f in files),
        },
    )
    print(f"{len(files)} files written to {GOLDEN.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
