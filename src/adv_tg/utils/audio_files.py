"""Central WAV/FLAC file I/O for adv_tg (the only module that touches ``soundfile``).

Every other module reads and writes audio files through this one, so formats, subtypes,
clipping and error messages are handled in a single place.

Formats and default subtypes (the extension decides the format, case-insensitive):

* ``.wav``  -> ``"FLOAT"`` (32-bit IEEE float): keeps the captured float32 signal bit-exact.
* ``.flac`` -> ``"PCM_24"``: FLAC has no float subtype; 24 bits is ~ -144 dBFS of
  quantization noise, far below any microphone.

Writing is atomic: data goes to a temporary file in the destination directory, which is
then renamed over the target (``os.replace``). An interrupted or failed write never leaves a
truncated file behind. If samples exceed [-1, 1] and the subtype is PCM, they are clipped
explicitly and a ``UserWarning`` is issued (never silently).

Comments: libsndfile drops non-ASCII characters from metadata, so they are stored escaped
(``\\uXXXX``) and unescaped again by ``read_audio``.

Tags (``KEY=value`` pairs, e.g. the ADC clock calibration of a recording): in FLAC they are
real Vorbis comments, read and rewritten here without new dependencies (``metaflac --list``
shows them); WAV has no free-form tags, so they are stored as extra ``KEY=value`` lines of the
comment and ``read_audio`` strips them from ``AudioData.comment``. Keys are upper case
(``[A-Z][A-Z0-9_]*``); values are one line of text.

Licences: ``soundfile`` is BSD-3-Clause; the ``libsndfile`` it bundles is LGPL-2.1 and is
loaded dynamically, which is compatible with this project's GPL-3.0-or-later.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import warnings
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Literal, Self

import numpy as np
from numpy.typing import ArrayLike, NDArray

from adv_tg.utils.debugger import Debugger

__all__ = [
    "AudioData",
    "AudioFileError",
    "AudioInfo",
    "AudioWriter",
    "DEFAULT_SUBTYPES",
    "MAX_IN_MEMORY_FRAMES",
    "SUPPORTED_EXTENSIONS",
    "audio_info",
    "iter_audio_blocks",
    "read_audio",
    "read_mono",
    "read_tags",
    "select_channel",
    "update_tags",
    "write_audio",
]

_dbg = Debugger.get(__name__)

SUPPORTED_EXTENSIONS: dict[str, str] = {".wav": "WAV", ".flac": "FLAC"}
DEFAULT_SUBTYPES: dict[str, str] = {"WAV": "FLOAT", "FLAC": "PCM_24"}
_FLOAT_SUBTYPES = frozenset({"FLOAT", "DOUBLE"})
#: Límite máximo de muestras para decodificación completa en memoria (2 horas a 48 kHz).
MAX_IN_MEMORY_FRAMES: int = 48000 * 7200

ChannelMode = Literal["max_energy"] | int


class AudioFileError(Exception):
    """Missing, unsupported or corrupt audio file (message in Spanish)."""


_ESCAPE_RE = re.compile(r"\\(x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8})")


def _escape_comment(text: str) -> str:
    return text.encode("ascii", "backslashreplace").decode("ascii")


def _unescape_comment(text: str) -> str:
    return _ESCAPE_RE.sub(lambda m: chr(int(m.group(1)[1:], 16)), text)


_TAG_KEY_RE = re.compile(r"[A-Z][A-Z0-9_]*")
_TAG_LINE_RE = re.compile(r"([A-Z][A-Z0-9_]*)=(.*)")


def _check_tags(tags: Mapping[str, str | None]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for key, value in tags.items():
        k = str(key).upper()
        if not _TAG_KEY_RE.fullmatch(k):
            raise AudioFileError(f"clave de etiqueta no válida: {key!r} (usa [A-Z][A-Z0-9_]*)")
        if value is not None:
            value = str(value)
            if "\n" in value or "\r" in value:
                raise AudioFileError(f"la etiqueta {k} no puede contener saltos de línea")
        out[k] = value
    return out


def _split_wav_comment(text: str | None) -> tuple[str | None, dict[str, str]]:
    """``(comment without tag lines, tags)`` of a WAV comment."""
    if not text:
        return None, {}
    lines, tags = [], {}
    for line in text.split("\n"):
        m = _TAG_LINE_RE.fullmatch(line)
        if m:
            tags[m.group(1)] = m.group(2)
        else:
            lines.append(line)
    rest = "\n".join(lines).strip("\n")
    return (rest or None), tags


def _join_wav_comment(comment: str | None, tags: Mapping[str, str | None]) -> str | None:
    lines = [comment] if comment else []
    lines += [f"{k}={v}" for k, v in tags.items() if v is not None]
    return "\n".join(lines) or None


# FLAC metadata (https://xiph.org/flac/format.html): "fLaC", then blocks with a 4-byte header
# (bit 7 = last block, bits 0-6 = type, 24-bit big-endian length). Type 4 = VORBIS_COMMENT,
# little-endian: vendor length + vendor, count, then count x (length + "KEY=value" UTF-8).
_FLAC_MAGIC = b"fLaC"
_FLAC_STREAMINFO = 0
_FLAC_VORBIS_COMMENT = 4
_FLAC_MAX_BLOCK = (1 << 24) - 1


def _read_flac_blocks(f) -> list[tuple[int, bytes]]:  # noqa: ANN001 - binary file object
    """Metadata blocks ``(type, body)``; leaves ``f`` at the first audio frame."""
    if f.read(4) != _FLAC_MAGIC:
        raise ValueError("no empieza por 'fLaC' (¿etiqueta ID3 delante?)")
    blocks: list[tuple[int, bytes]] = []
    while True:
        head = f.read(4)
        if len(head) != 4:
            raise ValueError("cabecera de metadatos FLAC truncada")
        n = int.from_bytes(head[1:4], "big")
        body = f.read(n)
        if len(body) != n:
            raise ValueError("bloque de metadatos FLAC truncado")
        blocks.append((head[0] & 0x7F, body))
        if head[0] & 0x80:
            return blocks


def _parse_vorbis(body: bytes) -> tuple[bytes, list[str]]:
    def u32(i: int) -> int:
        if i + 4 > len(body):
            raise ValueError("comentario Vorbis truncado")
        return int.from_bytes(body[i : i + 4], "little")

    n = u32(0)
    vendor, i = body[4 : 4 + n], 4 + n
    count, i = u32(i), i + 4
    comments = []
    for _ in range(count):
        n, i = u32(i), i + 4
        if i + n > len(body):
            raise ValueError("comentario Vorbis truncado")
        comments.append(body[i : i + n].decode("utf-8", "replace"))
        i += n
    return vendor, comments


def _build_vorbis(vendor: bytes, comments: list[str]) -> bytes:
    out = [len(vendor).to_bytes(4, "little"), vendor, len(comments).to_bytes(4, "little")]
    for c in comments:
        b = c.encode("utf-8")
        out += [len(b).to_bytes(4, "little"), b]
    return b"".join(out)


def _flac_tags(path: Path) -> dict[str, str]:
    with path.open("rb") as f:
        blocks = _read_flac_blocks(f)
    tags: dict[str, str] = {}
    for t, body in blocks:
        if t == _FLAC_VORBIS_COMMENT:
            for c in _parse_vorbis(body)[1]:
                key, sep, value = c.partition("=")
                if sep:
                    tags[key.upper()] = _unescape_comment(value)  # libsndfile escapes its own
    return tags


def _flac_set_tags(path: Path, tags: Mapping[str, str | None]) -> None:
    """Replace (``None`` = delete) Vorbis comments of a FLAC file; atomic rewrite."""
    tmp = _temp_path(path)
    try:
        with path.open("rb") as src:
            blocks = _read_flac_blocks(src)
            idx = next((i for i, (t, _) in enumerate(blocks) if t == _FLAC_VORBIS_COMMENT), None)
            if idx is None:
                vendor, comments = b"adv_tg", []
                idx = 1 if blocks and blocks[0][0] == _FLAC_STREAMINFO else 0
                blocks.insert(idx, (_FLAC_VORBIS_COMMENT, b""))
            else:
                vendor, comments = _parse_vorbis(blocks[idx][1])
            comments = [c for c in comments if c.partition("=")[0].upper() not in tags]
            comments += [f"{k}={v}" for k, v in tags.items() if v is not None]
            body = _build_vorbis(vendor, comments)
            if len(body) > _FLAC_MAX_BLOCK:
                raise ValueError("las etiquetas no caben en un bloque de metadatos FLAC")
            blocks[idx] = (_FLAC_VORBIS_COMMENT, body)
            with tmp.open("wb") as dst:
                dst.write(_FLAC_MAGIC)
                for i, (t, b) in enumerate(blocks):
                    last = 0x80 if i == len(blocks) - 1 else 0
                    dst.write(bytes([last | t]) + len(b).to_bytes(3, "big") + b)
                shutil.copyfileobj(src, dst, 1 << 20)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _sf():  # noqa: ANN202 - lazy import keeps ``import adv_tg`` light
    try:
        import soundfile
    except (ImportError, OSError) as exc:  # pragma: no cover - dependency is mandatory
        raise AudioFileError(f"no se pudo cargar 'soundfile'/libsndfile: {exc}") from exc
    return soundfile


@dataclass(frozen=True, slots=True)
class AudioData:
    """Decoded audio file.

    Attributes:
        samples: float64 in [-1, 1], shape ``(n,)`` (mono) or ``(n, channels)``.
        sample_rate: Sample rate in Hz.
        channels: Number of channels.
        duration_s: ``n / sample_rate``.
        format: Container, e.g. ``"WAV"`` or ``"FLAC"``.
        subtype: Sample encoding, e.g. ``"PCM_24"`` or ``"FLOAT"``.
        comment: Comment metadata, or ``None`` if the file has none.
    """

    samples: NDArray[np.float64]
    sample_rate: int
    channels: int
    duration_s: float
    format: str
    subtype: str
    comment: str | None

    @property
    def frames(self) -> int:
        """Number of sample frames."""
        return int(self.samples.shape[0])


@dataclass(frozen=True, slots=True)
class AudioInfo:
    """Header of an audio file (no samples decoded)."""

    sample_rate: int
    channels: int
    frames: int
    duration_s: float
    format: str
    subtype: str


# ---------------------------------------------------------------------- reading
def _check_readable(path: Path) -> None:
    if not path.exists():
        raise AudioFileError(f"el archivo de audio no existe: '{path}'")
    if not path.is_file():
        raise AudioFileError(f"no es un archivo: '{path}'")


def _open_error(path: Path, exc: BaseException) -> AudioFileError:
    return AudioFileError(
        f"no se pudo leer '{path}': formato no soportado o archivo corrupto ({exc})"
    )


def audio_info(path: Path | str) -> AudioInfo:
    """Read only the header of an audio file.

    Raises:
        AudioFileError: If the file is missing, unsupported or corrupt.
    """
    p = Path(path)
    _check_readable(p)
    try:
        i = _sf().info(str(p))
    except Exception as exc:
        raise _open_error(p, exc) from exc
    fs = int(i.samplerate)
    return AudioInfo(
        sample_rate=fs,
        channels=int(i.channels),
        frames=int(i.frames),
        duration_s=i.frames / fs if fs else 0.0,
        format=i.format,
        subtype=i.subtype,
    )


def read_audio(path: Path | str, max_frames: int | None = MAX_IN_MEMORY_FRAMES) -> AudioData:
    """Read a whole audio file (WAV, FLAC or any format libsndfile decodes).

    Args:
        path: File path.
        max_frames: Max sample frames to load in memory (default ``MAX_IN_MEMORY_FRAMES``).

    Returns:
        The decoded ``AudioData``.

    Raises:
        AudioFileError: If the file is missing, unsupported, too large or corrupt.
    """
    p = Path(path)
    sf = _sf()
    try:
        _check_readable(p)
        info = audio_info(p)
        if max_frames is not None and info.frames > max_frames:
            raise AudioFileError(
                f"el archivo '{p.name}' excede el límite de memoria para carga completa "
                f"({info.frames} muestras > {max_frames}); use iter_audio_blocks"
            )
        with sf.SoundFile(str(p)) as f:
            data = f.read(dtype="float64", always_2d=False)
            fs, ch, fmt, sub = int(f.samplerate), int(f.channels), f.format, f.subtype
            comment = _unescape_comment(f.comment) if f.comment else None
        if fmt == "WAV":
            comment = _split_wav_comment(comment)[0]
    except AudioFileError as exc:
        _dbg.file_read(p, ok=False, error=exc)
        raise
    except Exception as exc:
        err = _open_error(p, exc)
        _dbg.file_read(p, ok=False, error=err)
        raise err from exc
    samples = np.ascontiguousarray(data, dtype=np.float64)
    _dbg.file_read(p, frames=samples.shape[0], fs=fs, channels=ch, fmt=fmt, subtype=sub)
    return AudioData(
        samples=samples,
        sample_rate=fs,
        channels=ch,
        duration_s=samples.shape[0] / fs if fs else 0.0,
        format=fmt,
        subtype=sub,
        comment=comment,
    )


def _channel_index(energies: NDArray[np.float64], mode: ChannelMode) -> int:
    n = energies.shape[0]
    if isinstance(mode, str):
        if mode != "max_energy":
            raise ValueError(f"modo de canal desconocido: {mode!r}")
        return int(np.argmax(energies)) if n else 0
    idx = int(mode)
    if not 0 <= idx < n:
        raise ValueError(f"canal {idx} fuera de rango (el archivo tiene {n})")
    return idx


def select_channel(data: AudioData | ArrayLike, mode: ChannelMode = "max_energy") -> NDArray:
    """Return one channel as a 1-D float64 array.

    Args:
        data: ``AudioData`` or an array of shape ``(n,)`` / ``(n, channels)``.
        mode: ``"max_energy"`` (default: the channel with the largest sum of squares; the
            average of channels is avoided because it can cancel) or a channel index.

    Raises:
        ValueError: If the mode or index is invalid.
    """
    x = data.samples if isinstance(data, AudioData) else np.asarray(data, dtype=np.float64)
    if x.ndim == 1:
        if not isinstance(mode, str) and int(mode) != 0:
            raise ValueError(f"canal {mode} fuera de rango (la señal es mono)")
        return np.ascontiguousarray(x, dtype=np.float64)
    if x.ndim != 2:
        raise ValueError(f"forma de muestras no válida: {x.shape}")
    energies = np.einsum("ij,ij->j", x, x)
    return np.ascontiguousarray(x[:, _channel_index(energies, mode)], dtype=np.float64)


def read_mono(path: Path | str, mode: ChannelMode = "max_energy") -> tuple[int, NDArray]:
    """Convenience: ``(sample_rate, one channel as float64)`` of an audio file."""
    data = read_audio(path)
    return data.sample_rate, select_channel(data, mode)


def read_tags(path: Path | str) -> dict[str, str]:
    """``KEY=value`` tags of an audio file (keys upper case; see the module docstring).

    FLAC: every Vorbis comment (including libsndfile's own ``COMMENT``/``TITLE``...). WAV: the
    tag lines of the comment. Other formats: empty.

    Raises:
        AudioFileError: If the file is missing, unsupported or corrupt.
    """
    p = Path(path)
    _check_readable(p)
    fmt = audio_info(p).format
    try:
        if fmt == "FLAC":
            return _flac_tags(p)
        if fmt == "WAV":
            with _sf().SoundFile(str(p)) as f:
                text = _unescape_comment(f.comment) if f.comment else None
            return _split_wav_comment(text)[1]
    except AudioFileError:
        raise
    except Exception as exc:
        raise _open_error(p, exc) from exc
    return {}


def update_tags(path: Path | str, tags: Mapping[str, str | None]) -> Path:
    """Set (or delete with ``None``) tags of an existing WAV or FLAC file, atomically.

    FLAC: only the metadata is rewritten (audio frames copied byte for byte). WAV: the file is
    rewritten with the same subtype (lossless) and the tag lines of its comment replaced.

    Raises:
        AudioFileError: Missing/unsupported file, invalid tags or write failure.
    """
    p = Path(path)
    new = _check_tags(tags)
    fmt = audio_info(p).format
    try:
        if fmt == "FLAC":
            _flac_set_tags(p, new)
        elif fmt == "WAV":
            data = read_audio(p)
            with _sf().SoundFile(str(p)) as f:
                text = _unescape_comment(f.comment) if f.comment else None
            comment, old = _split_wav_comment(text)
            merged = {**old, **new}
            write_audio(
                p,
                data.samples,
                data.sample_rate,
                subtype=data.subtype,
                comment=comment,
                tags=merged,
            )
        else:
            raise AudioFileError(f"no se pueden etiquetar archivos {fmt}: '{p}'")
    except AudioFileError:
        raise
    except Exception as exc:
        err = AudioFileError(f"no se pudieron escribir las etiquetas de '{p}': {exc}")
        _dbg.file_write(p, ok=False, error=err)
        raise err from exc
    _dbg.file_write(p, tags=len(new))
    return p


def iter_audio_blocks(
    path: Path | str, blocksize: int, *, channel: ChannelMode | None = None
) -> Iterator[tuple[int, NDArray]]:
    """Read an audio file block by block without loading it whole.

    Args:
        path: File path.
        blocksize: Frames per block (the last block may be shorter).
        channel: ``None`` yields all channels (shape ``(n,)`` for mono files,
            ``(n, channels)`` otherwise); ``"max_energy"`` or an index yields one channel as
            1-D float64 (``"max_energy"`` costs one extra streaming pass to measure energy).

    Yields:
        ``(start_index, block)``: absolute index of the block's first frame, float64 data.

    Raises:
        AudioFileError: If the file is missing, unsupported or corrupt.
        ValueError: If ``blocksize`` is not positive or the channel is invalid.
    """
    if int(blocksize) <= 0:
        raise ValueError(f"blocksize debe ser positivo (recibido {blocksize})")
    p = Path(path)
    sf = _sf()
    try:
        _check_readable(p)
        f = sf.SoundFile(str(p))
    except AudioFileError as exc:
        _dbg.file_read(p, ok=False, error=exc)
        raise
    except Exception as exc:
        err = _open_error(p, exc)
        _dbg.file_read(p, ok=False, error=err)
        raise err from exc
    with f:
        idx: int | None = None
        if channel is not None:
            if f.channels == 1:
                idx = _channel_index(np.zeros(1), channel)
            elif isinstance(channel, str):
                energies = np.zeros(f.channels)
                for b in f.blocks(blocksize=int(blocksize), dtype="float64", always_2d=True):
                    energies += np.einsum("ij,ij->j", b, b)
                f.seek(0)
                idx = _channel_index(energies, channel)
            else:
                idx = _channel_index(np.zeros(f.channels), channel)
        _dbg.file_read(p, frames=f.frames, fs=f.samplerate, channels=f.channels, blocks=True)
        pos = 0
        try:
            for b in f.blocks(blocksize=int(blocksize), dtype="float64", always_2d=False):
                if idx is not None and b.ndim == 2:
                    b = np.ascontiguousarray(b[:, idx])
                yield pos, b
                pos += b.shape[0]
        except Exception as exc:
            if isinstance(exc, GeneratorExit):  # pragma: no cover
                raise
            err = _open_error(p, exc)
            _dbg.file_read(p, ok=False, error=err)
            raise err from exc


# ---------------------------------------------------------------------- writing
def _resolve_format(path: Path, subtype: str | None) -> tuple[str, str]:
    fmt = SUPPORTED_EXTENSIONS.get(path.suffix.lower())
    if fmt is None:
        exts = ", ".join(SUPPORTED_EXTENSIONS)
        raise AudioFileError(
            f"extensión no soportada '{path.suffix}' en '{path}' (usa una de: {exts})"
        )
    sub = (subtype or DEFAULT_SUBTYPES[fmt]).upper()
    if not _sf().check_format(fmt, sub):
        raise AudioFileError(f"el formato {fmt} no admite el subtipo {sub}")
    return fmt, sub


def _prepare(samples: ArrayLike, subtype: str, path: Path) -> tuple[NDArray[np.float64], int]:
    """Validate shape and clip explicitly for PCM. Returns (data, clipped_count)."""
    x = np.asarray(samples, dtype=np.float64)
    if x.ndim not in (1, 2):
        raise AudioFileError(f"forma de muestras no válida {x.shape} para '{path}'")
    if not np.all(np.isfinite(x)):
        raise AudioFileError(f"las muestras para '{path}' contienen NaN o infinitos")
    clipped = 0
    if subtype not in _FLOAT_SUBTYPES:
        over = np.abs(x) > 1.0
        clipped = int(np.count_nonzero(over))
        if clipped:
            x = np.clip(x, -1.0, 1.0)
    return x, clipped


def _warn_clipped(path: Path, clipped: int, subtype: str) -> None:
    msg = (
        f"'{path}': {clipped} muestras fuera de [-1, 1] recortadas al guardar como {subtype} "
        "(usa WAV FLOAT para conservarlas)"
    )
    _dbg.logger.warning(msg)
    warnings.warn(msg, UserWarning, stacklevel=3)


def _temp_path(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=path.suffix, dir=path.parent)
    os.close(fd)
    return Path(tmp)


def write_audio(
    path: Path | str,
    samples: ArrayLike,
    sample_rate: int,
    *,
    subtype: str | None = None,
    comment: str | None = None,
    tags: Mapping[str, str | None] | None = None,
) -> Path:
    """Write a whole signal to ``path`` (format from the extension; atomic).

    Args:
        path: Destination (``.wav`` or ``.flac``, case-insensitive). Parent directories are
            created if needed.
        samples: float samples in [-1, 1], shape ``(n,)`` or ``(n, channels)``.
        sample_rate: Sample rate in Hz.
        subtype: libsndfile subtype; default ``"FLOAT"`` for WAV and ``"PCM_24"`` for FLAC.
        comment: Optional comment metadata.
        tags: Optional ``KEY=value`` tags (see the module docstring; ``None`` values skipped).

    Returns:
        The destination path.

    Raises:
        AudioFileError: Unsupported extension/subtype, invalid samples or write failure.
    """
    p = Path(path)
    if int(sample_rate) <= 0:
        raise AudioFileError(f"tasa de muestreo no válida ({sample_rate}) para '{p}'")
    fmt, sub = _resolve_format(p, subtype)
    tag_map = _check_tags(tags or {})
    data, clipped = _prepare(samples, sub, p)
    if clipped:
        _warn_clipped(p, clipped, sub)
    channels = 1 if data.ndim == 1 else data.shape[1]
    tmp = _temp_path(p)
    try:
        with _sf().SoundFile(
            str(tmp), "w", samplerate=int(sample_rate), channels=channels, format=fmt, subtype=sub
        ) as f:
            text = _join_wav_comment(comment, tag_map) if fmt == "WAV" else comment
            if text:
                f.comment = _escape_comment(text)
            f.write(data)
        if fmt == "FLAC" and tag_map:
            _flac_set_tags(tmp, tag_map)
        os.replace(tmp, p)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        err = AudioFileError(f"no se pudo escribir '{p}': {exc}")
        _dbg.file_write(p, ok=False, error=err)
        raise err from exc
    _dbg.file_write(p, frames=data.shape[0], fs=int(sample_rate), fmt=fmt, subtype=sub)
    return p


class AudioWriter:
    """Block-wise writer (e.g. to record while capturing). Same format rules as
    ``write_audio``; the file appears at ``path`` only when the writer closes cleanly.

    Usage::

        with AudioWriter("take.flac", 48000) as w:
            for block in blocks:
                w.write(block)

    If the ``with`` body raises, the temporary file is deleted and ``path`` is untouched.

    Args:
        path: Destination (``.wav`` or ``.flac``).
        sample_rate: Sample rate in Hz.
        channels: Number of channels (blocks are ``(n,)`` for mono or ``(n, channels)``).
        subtype: libsndfile subtype (defaults as in ``write_audio``).
        comment: Optional comment metadata.
        tags: Optional ``KEY=value`` tags (as in ``write_audio``). In FLAC they are written on
            close, so the ``tags`` attribute can still be changed while recording; in WAV they go
            into the comment when the file is opened.
    """

    def __init__(
        self,
        path: Path | str,
        sample_rate: int,
        channels: int = 1,
        *,
        subtype: str | None = None,
        comment: str | None = None,
        tags: Mapping[str, str | None] | None = None,
    ) -> None:
        self.path = Path(path)
        if int(sample_rate) <= 0:
            raise AudioFileError(f"tasa de muestreo no válida ({sample_rate}) para '{self.path}'")
        if int(channels) <= 0:
            raise AudioFileError(f"número de canales no válido ({channels})")
        self.format, self.subtype = _resolve_format(self.path, subtype)
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.comment = comment
        self.tags: dict[str, str | None] = _check_tags(tags or {})
        self.frames_written = 0
        self.clipped_samples = 0
        self._warned = False
        self._tmp: Path | None = None
        self._file = None

    def open(self) -> Self:
        """Open the temporary file (called by ``__enter__``)."""
        if self._file is not None:
            return self
        self._tmp = _temp_path(self.path)
        try:
            self._file = _sf().SoundFile(
                str(self._tmp),
                "w",
                samplerate=self.sample_rate,
                channels=self.channels,
                format=self.format,
                subtype=self.subtype,
            )
            if self.format == "WAV":
                text = _join_wav_comment(self.comment, self.tags)
            else:
                text = self.comment
            if text:
                self._file.comment = _escape_comment(text)
        except Exception as exc:
            self._tmp.unlink(missing_ok=True)
            err = AudioFileError(f"no se pudo crear '{self.path}': {exc}")
            _dbg.file_write(self.path, ok=False, error=err)
            raise err from exc
        return self

    def write(self, block: ArrayLike) -> None:
        """Append a block of samples."""
        if self._file is None:
            raise AudioFileError("AudioWriter no está abierto (usa 'with AudioWriter(...)')")
        data, clipped = _prepare(block, self.subtype, self.path)
        expected = 1 if data.ndim == 1 else data.shape[1]
        if expected != self.channels:
            raise AudioFileError(f"bloque con {expected} canales; se esperaban {self.channels}")
        if clipped:
            self.clipped_samples += clipped
            if not self._warned:
                self._warned = True
                _warn_clipped(self.path, clipped, self.subtype)
        self._file.write(data)
        self.frames_written += data.shape[0]

    def close(self, *, discard: bool = False) -> None:
        """Finish the file (rename into place) or ``discard`` it. Idempotent."""
        f, tmp = self._file, self._tmp
        self._file = None
        self._tmp = None
        if f is None or tmp is None:
            return
        try:
            f.close()
            if discard:
                tmp.unlink(missing_ok=True)
                return
            if self.format == "FLAC" and self.tags:
                _flac_set_tags(tmp, _check_tags(self.tags))
            os.replace(tmp, self.path)
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            err = AudioFileError(f"no se pudo escribir '{self.path}': {exc}")
            _dbg.file_write(self.path, ok=False, error=err)
            raise err from exc
        _dbg.file_write(
            self.path,
            frames=self.frames_written,
            fs=self.sample_rate,
            fmt=self.format,
            subtype=self.subtype,
            clipped=self.clipped_samples,
        )

    def __enter__(self) -> Self:
        return self.open()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close(discard=exc_type is not None)
