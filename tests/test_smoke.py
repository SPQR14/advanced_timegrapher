"""Basic import / data / translation checks (no Qt widgets, no audio hardware)."""

from __future__ import annotations

import adv_tg
import adv_tg.core.models
import adv_tg.i18n
import adv_tg.utils
from adv_tg.core.models import WatchConfig
from adv_tg.i18n.translator import Translator
from adv_tg.utils.watch_db import load_watch_db


def test_version() -> None:
    from importlib.metadata import version

    assert adv_tg.__version__ == version("advanced-timegrapher")


def test_watch_db_not_empty() -> None:
    presets = load_watch_db()
    assert isinstance(presets, dict)
    assert presets
    assert "Custom" in presets


def test_watch_config_defaults() -> None:
    cfg = WatchConfig()
    assert cfg.bph > 0
    assert 0.0 < cfg.lift_angle < 180.0


def test_translator_keys_symmetric() -> None:
    en = set(Translator.DICTIONARIES["en"])
    es = set(Translator.DICTIONARIES["es"])
    assert en - es == set(), f"missing in 'es': {sorted(en - es)}"
    assert es - en == set(), f"missing in 'en': {sorted(es - en)}"
    assert "mock_mode" in en


def test_translator_format_strings() -> None:
    for locale in ("en", "es"):
        tr = Translator(locale).tr
        tr("status_drift_format").format(ppm=1.0, fs=48000.0, bias=0.1)
        tr("status_capture_format").format(pct=100.0, ovf=0)
