"""Capture-layer check: ``python -m adv_tg.tools.capture_check``.

Captures audio from a PortAudio input device (or replays a WAV/FLAC file in real time
with ``--file``) into a ``SampleStream`` and verifies that ingestion is complete:

* ingest ratio >= 99 %,
* largest gap between blocks < 3 nominal blocks,
* longest callback < 2000 µs.

All statistics, peak levels and file I/O happen on the main thread by reading the
``SampleStream``; the audio callback (or the replay thread) only writes into it.

Exit codes: 0 = OK, 1 = a criterion failed, 2 = device or argument error.
"""

from __future__ import annotations

import argparse
import math
import sys
import threading
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from adv_tg.audio.capture import AudioCapture, load_sounddevice
from adv_tg.audio.process_capture import ProcessAudioCapture
from adv_tg.core.sample_stream import SamplesOverwritten, SampleStream
from adv_tg.utils.audio_files import SUPPORTED_EXTENSIONS, read_mono, write_audio
from adv_tg.utils.debugger import Debugger

_dbg = Debugger.get(__name__)

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_ERROR = 2

MIN_INGEST = 0.99
MAX_GAP_BLOCKS = 3.0
MAX_CALLBACK_US = 2000.0
CLIP_DBFS = -1.0
DEFAULT_FS = 48000
REPLAY_DEFAULT_BLOCKSIZE = 1024
BUFFER_MARGIN_S = 5.0
MONITOR_BUFFER_S = 60.0
REPLAY_DRAIN_S = 1.0  # max extra wait for the replay thread to deliver its last blocks


class _Source(Protocol):
    """What the monitor needs from a sample producer (``AudioCapture`` or ``FileReplay``)."""

    callbacks: int
    status_flags: int
    callback_errors: int

    @property
    def max_callback_us(self) -> float: ...

    @property
    def is_active(self) -> bool: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...


# ---------------------------------------------------------------------- audio helpers
_SUBTYPES = {"int16": "PCM_16", "int24": "PCM_24", "float32": "FLOAT"}


def read_wav_mono(path: Path | str) -> tuple[int, NDArray[np.float32]]:
    """Read a WAV or FLAC file as mono float32 in [-1, 1] (channel with most energy).

    Args:
        path: Audio file path.

    Returns:
        ``(sample_rate, samples)``.

    Raises:
        AudioFileError: If the file is missing, unsupported or corrupt.
    """
    fs, x = read_mono(path)
    return int(fs), np.ascontiguousarray(x, dtype=np.float32)


read_audio_mono = read_wav_mono


def _save_audio(path: Path, samples: NDArray[np.float32], fs: int, fmt: str | None) -> None:
    """Save through ``audio_files`` (format from the extension) and explain the subtype."""
    subtype = _SUBTYPES[fmt] if fmt else None
    sub = subtype or ("FLOAT" if path.suffix.lower() == ".wav" else "PCM_24")
    clipped = int(np.count_nonzero(np.abs(samples) > 1.0)) if sub != "FLOAT" else 0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # reported below in Spanish
        write_audio(path, samples, fs, subtype=subtype, comment="adv_tg capture_check")
    if sub == "FLOAT":
        print(
            "AVISO: guardado como float32 (IEEE, sin pérdida); algunos programas no abren "
            "WAV flotante. Usa --wav-format int16 si lo necesitas."
        )
    else:
        note = f"; {clipped} muestras fuera de [-1, 1] recortadas" if clipped else ""
        print(f"AVISO: guardado como {sub} (cuantizado{note}).")


# ---------------------------------------------------------------------- file replay
class FileReplay:
    """Feed a sample array into a ``SampleStream`` in blocks, paced in real time.

    A background thread plays the role of the PortAudio callback: each block is written
    when its last sample would have been captured. It exposes the same counters as
    ``AudioCapture``.

    Args:
        stream: Destination ``SampleStream``.
        samples: Mono samples to replay.
        blocksize: Samples per block (> 0).
    """

    def __init__(self, stream: SampleStream, samples: NDArray[np.float32], blocksize: int) -> None:
        if blocksize <= 0:
            raise ValueError(f"blocksize must be positive, got {blocksize}")
        self._stream = stream
        self._samples = samples
        self.blocksize = int(blocksize)
        self.actual_sample_rate: float | None = stream.sample_rate
        self.callbacks = 0
        self.status_flags = 0
        self.callback_errors = 0
        self._max_callback_ns = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def max_callback_us(self) -> float:
        """Longest simulated callback (one ``write``), in microseconds."""
        return self._max_callback_ns / 1e3

    @property
    def is_active(self) -> bool:
        """True while the replay thread is running."""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Start the replay thread. Idempotent."""
        if self.is_active:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="FileReplay", daemon=True)
        _dbg.process_start("FileReplay", samples=len(self._samples), blocksize=self.blocksize)
        self._thread.start()

    def _run(self) -> None:
        fs = self._stream.sample_rate
        n_total = len(self._samples)
        t0 = time.perf_counter_ns()
        pos = 0
        while pos < n_total and not self._stop.is_set():
            end = min(pos + self.blocksize, n_total)
            deadline = t0 + int(end / fs * 1e9)
            wait_s = (deadline - time.perf_counter_ns()) / 1e9
            if wait_s > 0 and self._stop.wait(wait_s):
                break
            t_cb = time.perf_counter_ns()
            try:
                self.callbacks += 1
                # Stamp with the scheduled time (ideal device clock), not the wake-up time,
                # so OS timer jitter (large on macOS CI) does not show up as gaps.
                self._stream.write(self._samples[pos:end], deadline)
            except Exception:
                self.callback_errors += 1
            dt = time.perf_counter_ns() - t_cb
            if dt > self._max_callback_ns:
                self._max_callback_ns = dt
            pos = end

    def stop(self) -> None:
        """Stop the replay thread. Idempotent."""
        thread = self._thread
        if thread is None:
            return
        self._stop.set()
        thread.join()
        self._thread = None
        _dbg.process_end("FileReplay", callbacks=self.callbacks)

    def close(self) -> None:
        """Same as ``stop()``."""
        self.stop()


# ---------------------------------------------------------------------- verdict
@dataclass(frozen=True, slots=True)
class Verdict:
    """Result of the capture check.

    Attributes:
        ok: True when every criterion passed.
        failures: Human-readable (Spanish) description of each failed criterion.
        block_ms: Nominal block duration used for the gap criterion (NaN if unknown).
    """

    ok: bool
    failures: tuple[str, ...]
    block_ms: float


def nominal_block_ms(stream: SampleStream, blocksize: int) -> float:
    """Nominal block length in ms: ``blocksize/fs``, or the median observed block if 0."""
    if blocksize > 0:
        return 1000.0 * blocksize / stream.sample_rate
    ends, _ = stream.block_log()
    if ends.size < 2:
        return math.nan
    return 1000.0 * float(np.median(np.diff(ends))) / stream.sample_rate


def evaluate(stream: SampleStream, source: _Source, blocksize: int) -> Verdict:
    """Apply the three capture criteria to the final state of ``stream`` and ``source``."""
    st = stream.stats()
    block_ms = nominal_block_ms(stream, blocksize)
    failures: list[str] = []
    if st.total_samples == 0:
        failures.append("no se recibió ninguna muestra")
    else:
        if st.ingest_ratio < MIN_INGEST:
            failures.append(f"ingesta {100 * st.ingest_ratio:.2f} % < {100 * MIN_INGEST:.0f} %")
        if math.isnan(block_ms):
            failures.append("hueco máximo: no hay bloques suficientes para estimar el bloque")
        elif st.max_gap_ms >= MAX_GAP_BLOCKS * block_ms:
            failures.append(
                f"hueco máximo {st.max_gap_ms:.1f} ms >= {MAX_GAP_BLOCKS:.0f} bloques "
                f"({MAX_GAP_BLOCKS * block_ms:.1f} ms)"
            )
        if source.max_callback_us >= MAX_CALLBACK_US:
            failures.append(
                f"callback máximo {source.max_callback_us:.0f} µs >= {MAX_CALLBACK_US:.0f} µs"
            )
    return Verdict(ok=not failures, failures=tuple(failures), block_ms=block_ms)


# ---------------------------------------------------------------------- monitor
def _peak_dbfs(x: NDArray[np.float32]) -> float:
    if x.size == 0:
        return -math.inf
    peak = float(np.max(np.abs(x)))
    return 20.0 * math.log10(peak) if peak > 0.0 else -math.inf


def _status_line(t_s: int, stream: SampleStream, source: _Source, peak_db: float) -> str:
    st = stream.stats()
    peak = "  sin datos" if peak_db == -math.inf else f"{peak_db:6.1f} dBFS"
    line = (
        f"[{t_s:4d} s] muestras={st.total_samples:>10d}  ingesta={100 * st.ingest_ratio:6.2f} %"
        f"  hueco máx={st.max_gap_ms:7.1f} ms  callback máx={source.max_callback_us:7.0f} µs"
        f"  pico={peak}"
    )
    if peak_db >= CLIP_DBFS:
        line += "  ¡AVISO: posible saturación!"
    return line


def _monitor(stream: SampleStream, source: _Source, secs: float, replay: bool) -> None:
    """Print one status line per second until ``secs`` elapse (main thread).

    In replay mode the last line waits for the replay thread to deliver every sample:
    its paced waits overshoot by up to one timer tick (~15.6 ms on Windows), so stopping
    on the wall clock alone would drop the last blocks.
    """
    t0 = time.perf_counter()
    tick = 1
    last_idx = 0
    while True:
        now = time.perf_counter() - t0
        target = min(float(tick), secs)
        if now < target:
            time.sleep(min(target - now, 0.25))
            continue
        if replay and target >= secs and source.is_active and now < secs + REPLAY_DRAIN_S:
            time.sleep(0.005)
            continue
        total = stream.total_samples
        start = max(last_idx, stream.oldest_available)
        try:
            new = stream.read(start, total, np.float32)
        except SamplesOverwritten:
            new = np.empty(0, dtype=np.float32)
        last_idx = total
        print(_status_line(int(round(target)), stream, source, _peak_dbfs(new)), flush=True)
        if target >= secs or (replay and not source.is_active):
            return
        tick += 1


def _print_summary(stream: SampleStream, source: _Source, verdict: Verdict) -> None:
    st = stream.stats()
    rate = getattr(source, "actual_sample_rate", None)
    print()
    print("===== RESUMEN DE CAPTURA =====")
    print(f"Muestras recibidas : {st.total_samples} ({st.audio_s:.2f} s de audio)")
    print(f"Bloques            : {st.blocks}  (callbacks: {source.callbacks})")
    if not math.isnan(verdict.block_ms):
        print(f"Bloque nominal     : {verdict.block_ms:.2f} ms")
    print(f"Tasa efectiva      : {rate if rate is not None else '?'} Hz")
    print(f"Ingesta            : {100 * st.ingest_ratio:.2f} %  (mínimo {100 * MIN_INGEST:.0f} %)")
    limit = MAX_GAP_BLOCKS * verdict.block_ms
    print(f"Hueco máximo       : {st.max_gap_ms:.1f} ms  (límite {limit:.1f} ms)")
    print(
        f"Callback máximo    : {source.max_callback_us:.0f} µs  (límite {MAX_CALLBACK_US:.0f} µs;"
        f" write máx {st.max_write_us:.0f} µs)"
    )
    print(f"Avisos de estado   : {source.status_flags}  (p. ej. input overflow)")
    print(f"Errores en callback: {source.callback_errors}")
    if isinstance(source, ProcessAudioCapture):
        print(f"Tramas perdidas    : {source.dropped_frames}  (debe ser 0; captura aislada)")
        print(f"Backlog máximo     : {source.backlog_max} bloques en el proceso hijo")
        print(f"Retraso padre máx  : {source.parent_lag_max_ms:.1f} ms")
        print(
            f"Bloques en el padre: {source.frames_received} (enviados por el hijo: {source.seq_sent})"
        )
    if verdict.ok:
        print("VEREDICTO: OK — la captura entrega el audio completo.")
        return
    print("VEREDICTO: NO CUMPLE")
    for f in verdict.failures:
        print(f"  - Falló: {f}")
    print("Sugerencias: prueba --latency high, --blocksize 1024 o --blocksize 4096, otro")
    print("dispositivo o puerto USB, y cierra otras aplicaciones que usen el micrófono.")


# ---------------------------------------------------------------------- CLI
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m adv_tg.tools.capture_check",
        description="Comprueba que la capa de captura recibe el audio completo.",
    )
    p.add_argument("--list", action="store_true", help="lista los dispositivos de entrada y sale")
    p.add_argument("--device", type=int, default=None, help="índice PortAudio del dispositivo")
    p.add_argument(
        "--fs", type=int, default=None, help=f"tasa de muestreo en Hz (por defecto {DEFAULT_FS})"
    )
    p.add_argument(
        "--blocksize", type=int, default=0, help="muestras por bloque (0 = las elige PortAudio)"
    )
    p.add_argument("--latency", choices=("low", "high"), default="high", help="latencia PortAudio")
    p.add_argument("--secs", type=float, default=30.0, help="duración de la prueba en segundos")
    p.add_argument(
        "--wav",
        "--out",
        dest="wav",
        type=Path,
        default=None,
        help="guarda lo capturado (WAV o FLAC según la extensión)",
    )
    p.add_argument(
        "--isolated",
        action="store_true",
        help="captura en un proceso aparte (ProcessAudioCapture), como la GUI",
    )
    p.add_argument(
        "--fake-source",
        action="store_true",
        help="con --isolated: fuente falsa en el proceso hijo (sin micrófono)",
    )
    p.add_argument(
        "--wav-format",
        choices=tuple(_SUBTYPES),
        default=None,
        help="muestra del archivo guardado (por defecto: WAV float32, FLAC int24)",
    )
    p.add_argument(
        "--file",
        type=Path,
        default=None,
        help="modo repetición sin hardware: reproduce este WAV o FLAC en tiempo real",
    )
    return p


def _list_devices() -> int:
    try:
        sd = load_sounddevice()
    except (ImportError, OSError) as exc:
        print(f"ERROR: PortAudio/sounddevice no está disponible en este sistema ({exc}).")
        print("Instala PortAudio (p. ej. 'brew install portaudio' o 'apt install libportaudio2').")
        return EXIT_ERROR
    from adv_tg.utils.audio_io import list_input_devices

    devices = [d for d in list_input_devices() if not d.is_synthetic]
    try:
        default_in = sd.default.device[0]
    except Exception:
        default_in = None
    if not devices:
        print("No se encontró ningún dispositivo de entrada de audio.")
        return EXIT_ERROR
    print("Dispositivos de entrada:")
    for d in devices:
        mark = "  (predeterminado)" if d.device_id == default_in else ""
        print(
            f"  [{d.device_id:>2}] {d.name}  — {d.max_input_channels} canal(es), "
            f"{d.default_samplerate:.0f} Hz{mark}"
        )
    print("Uso: python -m adv_tg.tools.capture_check --device N --secs 30")
    return EXIT_OK


def _validate(args: argparse.Namespace) -> str | None:
    if args.fs is not None and args.fs <= 0:
        return f"--fs debe ser positivo (recibido {args.fs})"
    if args.blocksize < 0:
        return f"--blocksize no puede ser negativo (recibido {args.blocksize})"
    if not (args.secs > 0 and math.isfinite(args.secs)):
        return f"--secs debe ser un número positivo (recibido {args.secs})"
    if args.file is not None and args.device is not None:
        return "--file y --device son incompatibles"
    if args.file is not None and args.isolated:
        return "--file y --isolated son incompatibles"
    if args.fake_source and not args.isolated:
        return "--fake-source requiere --isolated"
    if args.wav is not None:
        ext = args.wav.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            return f"--wav/--out debe terminar en .wav o .flac (recibido '{args.wav.name}')"
        if ext == ".flac" and args.wav_format == "float32":
            return "FLAC no admite float32; usa --wav-format int16 o int24"
    return None


def _capacity(fs: int, secs: float, keep_all: bool) -> int:
    """Buffer size: the whole run when it must be saved, otherwise at most 60 s."""
    span = secs + BUFFER_MARGIN_S
    return int(fs * (span if keep_all else min(span, MONITOR_BUFFER_S)))


def _make_replay(args: argparse.Namespace) -> tuple[SampleStream, FileReplay, float, int]:
    path: Path = args.file
    try:
        fs, samples = read_wav_mono(path)
    except Exception as exc:
        _dbg.file_read(path, ok=False, error=exc)
        raise
    _dbg.file_read(path, samples=len(samples), fs=fs)
    if args.fs is not None and args.fs != fs:
        print(f"AVISO: se ignora --fs {args.fs}; el archivo está a {fs} Hz.")
    file_s = len(samples) / fs
    secs = min(args.secs, file_s)
    samples = samples[: int(round(secs * fs))]
    blocksize = args.blocksize or REPLAY_DEFAULT_BLOCKSIZE
    stream = SampleStream(_capacity(fs, secs, args.wav is not None), fs)
    print(
        f"Repitiendo '{path}' ({fs} Hz, {file_s:.2f} s en el archivo; se usan {secs:.2f} s) "
        f"en bloques de {blocksize} muestras."
    )
    return stream, FileReplay(stream, samples, blocksize), secs, blocksize


def main(argv: list[str] | None = None) -> int:
    """Run the capture check; returns the process exit code."""
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else EXIT_ERROR

    if args.list:
        return _list_devices()
    error = _validate(args)
    if error:
        print(f"ERROR: {error}")
        return EXIT_ERROR

    replay = args.file is not None
    source: Any
    if replay:
        try:
            stream, source, secs, blocksize = _make_replay(args)
        except Exception as exc:
            print(f"ERROR: no se pudo leer el archivo '{args.file}': {exc}")
            return EXIT_ERROR
    else:
        fs = args.fs or DEFAULT_FS
        secs = args.secs
        blocksize = args.blocksize
        stream = SampleStream(_capacity(fs, secs, args.wav is not None), fs)
        if args.isolated:
            source = ProcessAudioCapture(
                stream,
                device=args.device,
                sample_rate=fs,
                blocksize=blocksize,
                latency=args.latency,
                fake_source="tone" if args.fake_source else None,
            )
        else:
            source = AudioCapture(
                stream,
                device=args.device,
                sample_rate=fs,
                blocksize=blocksize,
                latency=args.latency,
            )
        dev = args.device if args.device is not None else "predeterminado"
        if args.fake_source:
            dev = "FALSO (proceso hijo)"
        mode = "proceso aparte" if args.isolated else "en proceso"
        print(
            f"Capturando {secs:g} s del dispositivo {dev} a {fs} Hz "
            f"(blocksize={blocksize}, latency={args.latency}, {mode})."
        )

    with _dbg.phase("capture_check", replay=replay, secs=secs, blocksize=blocksize):
        try:
            source.start()
        except Exception as exc:
            print(f"ERROR: no se pudo abrir el dispositivo de audio: {exc}")
            source.close()
            return EXIT_ERROR
        if not replay and not source.sample_rate_matches:
            print(
                f"ERROR: la tasa de muestreo efectiva ({source.actual_sample_rate} Hz) no coincide"
                f" con la pedida ({source.sample_rate} Hz)."
            )
            source.close()
            return EXIT_ERROR
        try:
            _monitor(stream, source, secs, replay)
        except KeyboardInterrupt:
            print("\nInterrumpido por el usuario.")
        finally:
            source.close()

    verdict = evaluate(stream, source, blocksize)
    _print_summary(stream, source, verdict)

    if args.wav is not None:
        lost = stream.oldest_available
        data = stream.read(lost, stream.total_samples, np.float32)
        if lost:
            print(
                f"AVISO: el archivo no incluye las primeras {lost} muestras (buffer sobrescrito)."
            )
        try:
            _save_audio(args.wav, data, int(stream.sample_rate), args.wav_format)
        except Exception as exc:
            print(f"ERROR: no se pudo guardar '{args.wav}': {exc}")
            return EXIT_ERROR
        print(f"Audio guardado en '{args.wav}' ({len(data)} muestras).")

    return EXIT_OK if verdict.ok else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
