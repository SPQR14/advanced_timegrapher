"""Shared pytest setup: run Qt headless unless the caller chose a platform."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """Never read or write the user's real configuration (e.g. ADC calibrations)."""
    monkeypatch.setenv("ADV_TG_CONFIG_DIR", str(tmp_path_factory.mktemp("config")))
    # Analyse in the reader thread (spawning the analysis process per test is slow); the
    # process path has its own tests.
    monkeypatch.setenv("ADV_TG_ANALYSIS_INPROC", "1")
