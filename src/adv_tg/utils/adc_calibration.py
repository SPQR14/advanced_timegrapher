"""Per-device store of ADC clock calibrations (layer 4 persistence).

Every audio interface has its own crystal, so its ``AdcDrift`` is saved under its name and
sample rate in a small JSON file (``adc_calibration.json`` in the user configuration folder,
see ``config_dir``). The history is kept (the newest ``MAX_HISTORY`` per device) to follow
the crystal over months.

Choosing the calibration to apply (``CalibrationStore.best``): a crystal ages, so a
calibration loses accuracy over time. Each record's systematic error is widened by
``CRYSTAL_AGING_PPM_PER_YEAR`` times its age (``Calibration.effective``) and the record with
the smallest total uncertainty wins. A fresh NTP estimate can therefore beat a PPS calibration
from a year ago, but not one from last week.

The file is written atomically (temporary file + ``os.replace``). A corrupt file is never
overwritten: loading it raises ``CalibrationStoreError`` and the user decides what to do.
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from adv_tg.core.adc_clock import AdcDrift, ClockLevel, drift_to_tags

_INT_TO_METHOD = {0: "none", 1: "host-envelope", 2: "reference", 3: "manual", 4: "pps"}
_METHOD_TO_INT = {v: k for k, v in _INT_TO_METHOD.items()}


def _cal_from_c(c: object) -> Calibration:
    d = c.drift  # type: ignore[attr-defined]
    note = c.note.decode("utf-8", errors="replace")  # type: ignore[attr-defined]
    return Calibration(
        device=c.device.decode("utf-8", errors="replace"),  # type: ignore[attr-defined]
        sample_rate=int(c.sample_rate),  # type: ignore[attr-defined]
        drift=AdcDrift(
            ppm=float(d.ppm),
            sigma_ppm=float(d.sigma_ppm),
            systematic_ppm=float(d.systematic_ppm),
            level=ClockLevel(int(d.level)),
            method=_INT_TO_METHOD.get(int(d.method), "none"),
            span_s=float(d.span_s),
            n_points=int(d.n_points),
            n_segments=int(d.n_segments),
            residual_us=float(d.residual_us),
            note=note,
        ),
        measured_at=datetime.fromtimestamp(int(c.measured_at_epoch_s), tz=UTC),  # type: ignore[attr-defined]
    )


def _cal_to_c(cal: Calibration, nb: object) -> object:
    d = cal.drift
    drift_c = nb.NativeAdcDriftStruct(  # type: ignore[attr-defined]
        ppm=float(d.ppm),
        sigma_ppm=float(d.sigma_ppm),
        systematic_ppm=float(d.systematic_ppm),
        span_s=float(d.span_s),
        residual_us=float(d.residual_us),
        n_points=int(d.n_points),
        n_segments=int(d.n_segments),
        level=int(d.level),
        method=_METHOD_TO_INT.get(d.method, 0),
        note_kind=0,
        _pad=0,
    )
    return nb.NativeCalibrationStruct(  # type: ignore[attr-defined]
        device=cal.device.encode("utf-8")[:255],
        sample_rate=int(cal.sample_rate),
        _pad=0,
        drift=drift_c,
        measured_at_epoch_s=int(round(cal.measured_at.astimezone(UTC).timestamp())),
        note=d.note.encode("utf-8")[:255],
    )


__all__ = [
    "CONFIG_DIR_ENV",
    "CRYSTAL_AGING_PPM_PER_YEAR",
    "MAX_HISTORY",
    "Calibration",
    "CalibrationStore",
    "CalibrationStoreError",
    "config_dir",
    "pick_best",
    "recording_tags",
]

#: Environment variable that overrides the configuration folder (tests, portable installs).
CONFIG_DIR_ENV = "ADV_TG_CONFIG_DIR"
#: Assumed crystal aging, added in quadrature to old calibrations (typical USB audio crystals
#: drift 1-3 ppm in their first year and less afterwards).
CRYSTAL_AGING_PPM_PER_YEAR = 1.0
#: Records kept per device and sample rate.
MAX_HISTORY = 50

_FILE_NAME = "adc_calibration.json"
_FORMAT_VERSION = 1
_DAYS_PER_YEAR = 365.25
_ORIG_PLATFORM = sys.platform
_ORIG_HOME = Path.home


class CalibrationStoreError(Exception):
    """Unreadable or invalid calibration file (message in Spanish)."""


def config_dir() -> Path:
    """User configuration folder of adv_tg (not created here).

    ``$ADV_TG_CONFIG_DIR`` if set; otherwise ``~/Library/Application Support/adv_tg`` on
    macOS, ``%APPDATA%\\adv_tg`` on Windows and ``$XDG_CONFIG_HOME/adv_tg`` (default
    ``~/.config/adv_tg``) elsewhere.
    """
    if sys.platform == _ORIG_PLATFORM and Path.home is _ORIG_HOME:
        from adv_tg import native_bridge

        if native_bridge.have("advtg_config_dir"):
            cdll = native_bridge.lib()
            assert cdll is not None
            return Path(native_bridge.call_str_buf(cdll.advtg_config_dir))
    env = os.environ.get(CONFIG_DIR_ENV)
    if env:
        return Path(env).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "adv_tg"
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Roaming") / "adv_tg"
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "adv_tg"


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat(timespec="seconds")


def _parse_time(text: str) -> datetime:
    t = datetime.fromisoformat(text)
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _same_device(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


@dataclass(frozen=True, slots=True)
class Calibration:
    """One calibration of one device.

    Attributes:
        device: Device name as PortAudio reports it (matched ignoring case and spaces at
            the ends).
        sample_rate: Nominal sample rate in Hz it was measured at.
        drift: The measured (or entered) ADC error. Never level 0.
        measured_at: When it was measured (timezone-aware, UTC).
    """

    device: str
    sample_rate: int
    drift: AdcDrift
    measured_at: datetime

    def age_days(self, now: datetime | None = None) -> float:
        """Days between the calibration and ``now`` (default: the current time), in absolute
        value: a calibration made after a recording ages it the same way."""
        dt = (now or _now()) - self.measured_at
        return abs(dt.total_seconds()) / 86400.0

    def effective(
        self, now: datetime | None = None, aging_ppm_per_year: float = CRYSTAL_AGING_PPM_PER_YEAR
    ) -> AdcDrift:
        """The drift to apply ``now``: systematic error widened by the crystal aging."""
        aging = aging_ppm_per_year * self.age_days(now) / _DAYS_PER_YEAR
        if aging <= 0:
            return self.drift
        return replace(self.drift, systematic_ppm=math.hypot(self.drift.systematic_ppm, aging))

    def to_json(self) -> dict[str, object]:
        d = self.drift
        return {
            "device": self.device,
            "sample_rate": self.sample_rate,
            "measured_at": _iso(self.measured_at),
            "ppm": d.ppm,
            "sigma_ppm": d.sigma_ppm,
            "systematic_ppm": d.systematic_ppm,
            "level": int(d.level),
            "method": d.method,
            "span_s": d.span_s,
            "n_points": d.n_points,
            "n_segments": d.n_segments,
            "residual_us": d.residual_us if math.isfinite(d.residual_us) else None,
            "note": d.note,
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> Calibration:
        """Inverse of ``to_json``.

        Raises:
            ValueError: Missing or invalid fields.
        """
        try:
            residual = data.get("residual_us")
            drift = AdcDrift(
                ppm=float(data["ppm"]),  # type: ignore[arg-type]
                sigma_ppm=float(data["sigma_ppm"]),  # type: ignore[arg-type]
                systematic_ppm=float(data["systematic_ppm"]),  # type: ignore[arg-type]
                level=ClockLevel(int(data["level"])),  # type: ignore[call-overload]
                method=str(data["method"]),
                span_s=float(data.get("span_s", 0.0)),  # type: ignore[arg-type]
                n_points=int(data.get("n_points", 0)),  # type: ignore[call-overload]
                n_segments=int(data.get("n_segments", 0)),  # type: ignore[call-overload]
                residual_us=math.nan if residual is None else float(residual),  # type: ignore[arg-type]
                note=str(data.get("note", "")),
            )
            cal = cls(
                device=str(data["device"]),
                sample_rate=int(data["sample_rate"]),  # type: ignore[call-overload]
                drift=drift,
                measured_at=_parse_time(str(data["measured_at"])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"registro de calibración no válido: {exc}") from exc
        cal.validate()
        return cal

    def validate(self) -> None:
        """Raise ``ValueError`` if the record cannot be stored."""
        d = self.drift
        if not self.device.strip():
            raise ValueError("el nombre del dispositivo está vacío")
        if self.sample_rate <= 0:
            raise ValueError(f"tasa de muestreo no válida: {self.sample_rate}")
        if d.level == ClockLevel.UNCALIBRATED:
            raise ValueError("no se guarda una calibración de nivel 0 (sin calibrar)")
        if not (math.isfinite(d.ppm) and math.isfinite(d.uncertainty_ppm)):
            raise ValueError("ppm o incertidumbre no finitos")
        if d.sigma_ppm < 0 or d.systematic_ppm < 0:
            raise ValueError("las incertidumbres no pueden ser negativas")
        if self.measured_at.tzinfo is None:
            raise ValueError("measured_at debe llevar zona horaria")


class CalibrationStore:
    """JSON file with the calibration history of every device.

    Args:
        path: File to use (default ``config_dir() / "adc_calibration.json"``).
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else config_dir() / _FILE_NAME

    # ------------------------------------------------------------------ file
    def _load(self) -> list[Calibration]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("calibrations"), list):
                raise ValueError("falta la lista 'calibrations'")
            if int(data.get("version", 0)) > _FORMAT_VERSION:
                raise ValueError(f"versión {data['version']} más nueva que la soportada")
            return [Calibration.from_json(r) for r in data["calibrations"]]
        except (OSError, ValueError, TypeError) as exc:
            raise CalibrationStoreError(
                f"no se pudo leer el archivo de calibraciones '{self.path}': {exc}. "
                "Corrígelo o muévelo a otro sitio para empezar de cero."
            ) from exc

    def _save(self, records: list[Calibration]) -> None:
        payload = {
            "version": _FORMAT_VERSION,
            "calibrations": [r.to_json() for r in records],
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if sys.platform != "win32" and self.path.parent.is_dir():
            try:
                self.path.parent.chmod(0o700)
            except OSError:
                pass
        fd, tmp = tempfile.mkstemp(prefix=".adc_calibration.", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    # ------------------------------------------------------------------ queries
    def records(
        self, device: str | None = None, sample_rate: int | None = None
    ) -> list[Calibration]:
        """Stored calibrations, oldest first (optionally of one device / sample rate)."""
        from adv_tg import native_bridge

        if native_bridge.have("advtg_cal_store_records"):
            cdll = native_bridge.lib()
            assert cdll is not None
            cap = 64
            buf = (native_bridge.NativeCalibrationStruct * cap)()
            path_b = str(self.path).encode("utf-8")
            dev_b = device.encode("utf-8") if device is not None else None
            sr = int(sample_rate) if sample_rate is not None else 0
            n = int(cdll.advtg_cal_store_records(path_b, dev_b, sr, buf, cap))
            if n < 0:
                err = cdll.advtg_cal_last_error().decode("utf-8", errors="replace")
                raise CalibrationStoreError(
                    f"no se pudo leer el archivo de calibraciones '{self.path}': {err}"
                )
            if n > cap:
                buf = (native_bridge.NativeCalibrationStruct * n)()
                n = int(cdll.advtg_cal_store_records(path_b, dev_b, sr, buf, n))
            return [_cal_from_c(buf[i]) for i in range(n)]

        out = self._load()
        if device is not None:
            out = [r for r in out if _same_device(r.device, device)]
        if sample_rate is not None:
            out = [r for r in out if r.sample_rate == int(sample_rate)]
        return sorted(out, key=lambda r: r.measured_at)

    def best(
        self,
        device: str,
        sample_rate: int,
        now: datetime | None = None,
        aging_ppm_per_year: float = CRYSTAL_AGING_PPM_PER_YEAR,
    ) -> Calibration | None:
        """The calibration to apply now (smallest uncertainty after aging), or ``None``.

        Ties go to the most recent record.
        """
        from adv_tg import native_bridge

        if native_bridge.have("advtg_cal_store_best"):
            import ctypes

            cdll = native_bridge.lib()
            assert cdll is not None
            out = native_bridge.NativeCalibrationStruct()
            now_s = int(round((now or _now()).astimezone(UTC).timestamp()))
            rc = int(
                cdll.advtg_cal_store_best(
                    str(self.path).encode("utf-8"),
                    device.encode("utf-8"),
                    int(sample_rate),
                    now_s,
                    float(aging_ppm_per_year),
                    ctypes.byref(out),
                )
            )
            if rc < 0:
                err = cdll.advtg_cal_last_error().decode("utf-8", errors="replace")
                raise CalibrationStoreError(
                    f"no se pudo leer el archivo de calibraciones '{self.path}': {err}"
                )
            return _cal_from_c(out) if rc > 0 else None

        return pick_best(self.records(device, sample_rate), now, aging_ppm_per_year)

    # ------------------------------------------------------------------ changes
    def add(self, calibration: Calibration) -> None:
        """Append a calibration (keeps the newest ``MAX_HISTORY`` of that device and rate).

        Raises:
            ValueError: Invalid record (e.g. level 0).
            CalibrationStoreError: The existing file is unreadable.
        """
        calibration.validate()
        from adv_tg import native_bridge

        if native_bridge.have("advtg_cal_store_add"):
            import ctypes

            cdll = native_bridge.lib()
            assert cdll is not None
            cal_c = _cal_to_c(calibration, native_bridge)
            rc = int(cdll.advtg_cal_store_add(str(self.path).encode("utf-8"), ctypes.byref(cal_c)))
            if rc == 0:
                return
            err = cdll.advtg_cal_last_error().decode("utf-8", errors="replace")
            if rc == -1:
                raise ValueError(err)
            raise CalibrationStoreError(
                f"no se pudo leer el archivo de calibraciones '{self.path}': {err}"
            )

        records = self._load()
        known = next(
            (r.device for r in records if _same_device(r.device, calibration.device)), None
        )
        if known is not None:  # keep the first spelling of the name
            calibration = replace(calibration, device=known)
        else:
            calibration = replace(calibration, device=calibration.device.strip())
        records.append(calibration)
        mine = sorted(
            (
                r
                for r in records
                if _same_device(r.device, calibration.device)
                and r.sample_rate == calibration.sample_rate
            ),
            key=lambda r: r.measured_at,
        )
        drop = {id(r) for r in mine[:-MAX_HISTORY]}
        self._save([r for r in records if id(r) not in drop])

    def forget(self, device: str, sample_rate: int | None = None) -> int:
        """Delete the calibrations of a device (optionally one sample rate). Returns how many."""
        from adv_tg import native_bridge

        if native_bridge.have("advtg_cal_store_forget"):
            cdll = native_bridge.lib()
            assert cdll is not None
            sr = int(sample_rate) if sample_rate is not None else 0
            rc = int(
                cdll.advtg_cal_store_forget(
                    str(self.path).encode("utf-8"), device.encode("utf-8"), sr
                )
            )
            if rc < 0:
                err = cdll.advtg_cal_last_error().decode("utf-8", errors="replace")
                raise CalibrationStoreError(
                    f"no se pudo leer el archivo de calibraciones '{self.path}': {err}"
                )
            return rc

        records = self._load()
        keep = [
            r
            for r in records
            if not (
                _same_device(r.device, device)
                and (sample_rate is None or r.sample_rate == int(sample_rate))
            )
        ]
        removed = len(records) - len(keep)
        if removed:
            self._save(keep)
        return removed


def pick_best(
    records: list[Calibration],
    now: datetime | None = None,
    aging_ppm_per_year: float = CRYSTAL_AGING_PPM_PER_YEAR,
) -> Calibration | None:
    """The record with the smallest uncertainty after aging to ``now``; ties go to the newest."""
    if not records:
        return None
    now = now or _now()
    newest_first = sorted(records, key=lambda r: r.measured_at, reverse=True)
    return min(newest_first, key=lambda r: r.effective(now, aging_ppm_per_year).uncertainty_ppm)


def recording_tags(
    device: str | None,
    sample_rate: int,
    store: CalibrationStore | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, str], Calibration | None]:
    """Tags to stamp on a recording made now with ``device``.

    The best stored calibration (aged to ``now``) is written; without one, an explicit level 0
    so a reader knows the file was not calibrated.

    Returns:
        ``(tags, calibration used or None)``.

    Raises:
        CalibrationStoreError: The store file is unreadable.
    """
    now = now or _now()
    if store is None or type(store) is CalibrationStore:
        from adv_tg import native_bridge

        if native_bridge.have("advtg_cal_recording_tags"):
            import ctypes

            cdll = native_bridge.lib()
            assert cdll is not None
            st = store or CalibrationStore()
            out_tags: dict[str, str] = {}

            def _on_tag(k: bytes | None, v: bytes | None, _u: object) -> None:
                if k is not None and v is not None:
                    out_tags[k.decode("utf-8", errors="replace")] = v.decode(
                        "utf-8", errors="replace"
                    )

            cb = native_bridge.TAG_CB_TYPE(_on_tag)
            has_cal = ctypes.c_int(0)
            cal_c = native_bridge.NativeCalibrationStruct()
            now_s = int(round(now.astimezone(UTC).timestamp()))
            rc = int(
                cdll.advtg_cal_recording_tags(
                    str(st.path).encode("utf-8"),
                    device.encode("utf-8") if device else None,
                    int(sample_rate),
                    now_s,
                    cb,
                    None,
                    ctypes.byref(has_cal),
                    ctypes.byref(cal_c),
                )
            )
            if rc < 0:
                err = cdll.advtg_cal_last_error().decode("utf-8", errors="replace")
                raise CalibrationStoreError(
                    f"no se pudo leer el archivo de calibraciones '{st.path}': {err}"
                )
            return out_tags, (_cal_from_c(cal_c) if has_cal.value else None)

    tags = {"ADV_TG_RECORDED_AT": _iso(now), "ADV_TG_SAMPLE_RATE": str(int(sample_rate))}
    if device:
        tags["ADV_TG_DEVICE"] = " ".join(device.split())
    cal = (store or CalibrationStore()).best(device, sample_rate, now) if device else None
    if cal is None:
        tags |= drift_to_tags(AdcDrift.uncalibrated())
    else:
        tags |= drift_to_tags(cal.effective(now), calibrated_at=_iso(cal.measured_at))
    return tags, cal
