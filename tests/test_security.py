from __future__ import annotations

from adv_tg.utils import security
from adv_tg.utils.security import anonymize_text, safe_filename_base, sanitize_csv_cell


def test_sanitize_csv_cell_formula_injection() -> None:
    assert sanitize_csv_cell("=cmd|'/C calc'!A0") == "'=cmd|'/C calc'!A0"
    assert sanitize_csv_cell("@SUM(1+1)") == "'@SUM(1+1)"
    assert sanitize_csv_cell("\tTabbed") == "'\tTabbed"
    assert sanitize_csv_cell("\rReturn") == "'\rReturn"
    assert sanitize_csv_cell("+cmd") == "'+cmd"
    assert sanitize_csv_cell("-cmd") == "'-cmd"


def test_sanitize_csv_cell_preserves_numbers_and_safe_strings() -> None:
    assert sanitize_csv_cell("+1.25") == "+1.25"
    assert sanitize_csv_cell("-0.40") == "-0.40"
    assert sanitize_csv_cell("21600") == "21600"
    assert sanitize_csv_cell("Caliber-Test") == "Caliber-Test"
    assert sanitize_csv_cell(1234) == 1234
    assert sanitize_csv_cell(52.0) == 52.0


def test_safe_filename_base() -> None:
    assert safe_filename_base("../../etc/passwd") == "etcpasswd"
    assert safe_filename_base("Test*Name:01?") == "TestName01"
    assert safe_filename_base("   ") == "watch_test"
    assert safe_filename_base("Caliber-Test_01") == "Caliber-Test_01"


def test_anonymize_text() -> None:
    import getpass
    from pathlib import Path

    user = getpass.getuser()
    home = str(Path.home().resolve())
    sample = f"Logs in {home}/Library/Logs and user {user}"
    anonymized = anonymize_text(sample)
    if home != "/":
        assert home not in anonymized
        assert "~/Library/Logs" in anonymized


def test_anonymize_short_username_keeps_words(monkeypatch) -> None:
    import getpass

    monkeypatch.setattr(getpass, "getuser", lambda: "al")
    # No home replacement: isolates the username rule on every OS (on Windows a fake POSIX
    # home becomes "\\Users\\al", which would also match inside "C:\\Users\\al").
    monkeypatch.setattr(security, "_home_variants", lambda: [])
    text = "Error al abrir /home/al/x.flac; calibration ok; C:\\Users\\al\\logs"
    out = anonymize_text(text)
    assert "Error al abrir" in out and "calibration" in out
    assert "/home/<USER>/x.flac" in out and "C:\\Users\\<USER>\\logs" in out


def test_anonymize_long_username_as_word(monkeypatch) -> None:
    import getpass

    monkeypatch.setattr(getpass, "getuser", lambda: "sqpr14")
    out = anonymize_text("owner=sqpr14 path=/Volumes/sqpr14/rec.flac sqpr14x")
    assert out == "owner=<USER> path=/Volumes/<USER>/rec.flac sqpr14x"
