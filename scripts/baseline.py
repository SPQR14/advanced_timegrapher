#!/usr/bin/env python3
"""Baseline of the C++ migration (plan, phase 0): startup, memory, GUI smoothness, sizes.

    python scripts/baseline.py                          # this checkout (python -m adv_tg)
    python scripts/baseline.py --exe dist/adv-tg/adv-tg # a frozen build or the C++ app
    python scripts/baseline.py --release v2.0.0         # + installer sizes from GitHub

Measured the same way for every implementation (``adv_tg.tools.bench`` defines the in-app
contract, ``ADV_TG_BENCH``):

* startup: wall time from launching the process until it exits right after showing the main
  window (median of ``--runs``), plus the in-app time from ``main()`` to the window;
* live: ``--seconds`` of real-time replay of ``--replay`` (default nh34.flac): peak resident
  memory of the whole process tree (the 2.x app runs capture and analysis in child
  processes) and the lateness of a 16 ms GUI timer;
* sizes: the release assets on GitHub (public API, no token).

Memory of the process tree: /proc on Linux; psutil elsewhere (``pip install psutil``).
Prints a JSON document (``--json`` writes it to a file too).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "ADV_TG_BENCH "
REPO = "SPQR14/advanced_timegrapher"


def _tree_rss_linux(pid: int) -> int:
    children: dict[int, list[int]] = {}
    rss: dict[int, int] = {}
    page = os.sysconf("SC_PAGE_SIZE")
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            stat = (d / "stat").read_text()
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
            rss[int(d.name)] = int((d / "statm").read_text().split()[1]) * page
        except (OSError, ValueError, IndexError):
            continue
        children.setdefault(ppid, []).append(int(d.name))
    total, todo = 0, [pid]
    while todo:
        p = todo.pop()
        total += rss.get(p, 0)
        todo += children.get(p, [])
    return total


def tree_rss(pid: int) -> int:
    if sys.platform.startswith("linux"):
        return _tree_rss_linux(pid)
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        return 0
    try:
        p = psutil.Process(pid)
        procs = [p, *p.children(recursive=True)]
    except psutil.Error:
        return 0
    total = 0
    for q in procs:
        try:
            total += q.memory_info().rss
        except psutil.Error:
            pass
    return total


def run(cmd: list[str], env: dict[str, str], sample_rss: bool, timeout: float) -> dict:
    import tempfile

    fd, out_path = tempfile.mkstemp(prefix="advtg-bench-", suffix=".txt")
    os.close(fd)
    env = {**env, "ADV_TG_BENCH_OUT": out_path}
    t0 = time.perf_counter()
    proc = subprocess.Popen(
        cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True
    )
    peak = 0
    stop = threading.Event()

    def sampler() -> None:
        nonlocal peak
        while not stop.wait(0.25):
            peak = max(peak, tree_rss(proc.pid))

    th = threading.Thread(target=sampler, daemon=True)
    if sample_rss:
        th.start()
    try:
        proc.wait(timeout=timeout)
    finally:
        stop.set()
    wall = time.perf_counter() - t0
    out = Path(out_path).read_text(encoding="utf-8")
    Path(out_path).unlink(missing_ok=True)
    payload = {}
    for line in out.splitlines():
        if line.startswith(PREFIX):
            payload = json.loads(line[len(PREFIX) :])
    return {"exit": proc.returncode, "wall_s": wall, "peak_rss_mb": peak / 2**20, **payload}


def release_sizes(tag: str) -> dict[str, float]:
    url = f"https://api.github.com/repos/{REPO}/releases/tags/{tag}"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 (fixed https URL)
        data = json.load(r)
    return {a["name"]: round(a["size"] / 2**20, 1) for a in data.get("assets", [])}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--exe", help="executable to measure (default: python -m adv_tg)")
    p.add_argument("--runs", type=int, default=5)
    p.add_argument("--seconds", type=float, default=30.0)
    p.add_argument("--replay", default=str(ROOT / "docs" / "samples" / "nh34.flac"))
    p.add_argument("--release", help="GitHub release tag whose asset sizes are reported")
    p.add_argument("--json", type=Path, help="also write the result to this file")
    p.add_argument("--startup-only", action="store_true", help="skip the live measurement")
    a = p.parse_args(argv)

    cmd = [a.exe] if a.exe else [sys.executable, "-m", "adv_tg"]
    env = dict(os.environ)
    if not a.exe:
        env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result: dict = {
        "command": " ".join(cmd),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "qt_platform": env.get("QT_QPA_PLATFORM", "(default)"),
    }

    starts = []
    for _ in range(a.runs):
        r = run(cmd, {**env, "ADV_TG_BENCH": "startup"}, sample_rss=False, timeout=120)
        if r["exit"] != 0:
            print(f"startup run failed (exit {r['exit']})", file=sys.stderr)
            return 1
        starts.append(r)
    result["startup"] = {
        "runs": a.runs,
        "wall_ms_median": statistics.median(s["wall_s"] for s in starts) * 1e3,
        "wall_ms_min": min(s["wall_s"] for s in starts) * 1e3,
        "window_shown_ms_median": statistics.median(s["window_shown_ms"] for s in starts),
    }

    if not a.startup_only:
        live_env = {**env, "ADV_TG_BENCH": f"{a.seconds:g}", "ADV_TG_REPLAY_FILE": a.replay}
        r = run(cmd, live_env, sample_rss=True, timeout=a.seconds + 120)
        if r["exit"] != 0 or r.get("mode") != "live":
            print(f"live run failed (exit {r['exit']})", file=sys.stderr)
            return 1
        result["live"] = {k: v for k, v in r.items() if k not in ("exit", "mode")}
        result["live"]["replay"] = Path(a.replay).name

    if a.release:
        try:
            result["release_sizes_mb"] = release_sizes(a.release)
        except OSError as exc:
            result["release_sizes_mb"] = f"no disponible: {exc}"

    text = json.dumps(result, indent=1)
    print(text)
    if a.json:
        a.json.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
