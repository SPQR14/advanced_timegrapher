"""Shared pytest setup: run Qt headless unless the caller chose a platform."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
