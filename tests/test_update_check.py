"""Update notice: version ordering, choice of the release, state file and GUI wiring.

No test touches the network: the HTTP call and the check function are replaced.
"""

from __future__ import annotations

import io
import json
import time

import pytest

from adv_tg import __version__
from adv_tg.utils import update_check as uc
from adv_tg.utils.update_check import (
    CHECK_INTERVAL_S,
    RELEASES_PAGE_URL,
    ReleaseInfo,
    UpdateCheckError,
    UpdateState,
    is_newer,
    parse_version,
)


def test_version_order_of_the_project_tags() -> None:
    ordered = [
        "v0.7.0.beta4",
        "v0.8.0-beta",
        "0.8.0",
        "v0.8.1-beta5.1",
        "v0.8.1-beta6",
        "v0.8.1-rc1",
        "v0.8.1",
        "v0.8.2-beta",
        "v0.10",
        "v1.0.0.dev1",
        "v1.0.0a1",
        "V1.0.0",
    ]
    keys = [parse_version(v) for v in ordered]
    assert None not in keys
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)
    assert parse_version("v0.10") == parse_version("0.10.0")


@pytest.mark.parametrize("text", ["", "latest", "v1", "1.2.3.4", "v1.2.3-windows", "1.2.x"])
def test_unknown_versions_are_rejected(text: str) -> None:
    assert parse_version(text) is None
    assert not is_newer(text, "0.1.0")
    assert not is_newer("9.9.9", text)


def test_is_newer() -> None:
    assert is_newer("v1.0.1", "1.0.0")
    assert not is_newer("v1.0.0", "1.0.0")
    assert not is_newer("v1.0.0-rc1", "1.0.0")
    assert is_newer("1.0.0", "1.0.0-rc1")
    assert not is_newer("0.9.9", "1.0.0")
    assert not is_newer(__version__)  # the running version is never newer than itself


def _release(tag: str, **extra: object) -> dict[str, object]:
    return {"tag_name": tag, "html_url": f"{RELEASES_PAGE_URL}/tag/{tag}", **extra}


def test_pick_latest_ignores_drafts_prereleases_and_odd_tags() -> None:
    releases = [
        _release("v1.1.0"),
        _release("v9.0.0", draft=True),
        _release("v8.0.0-rc1", prerelease=True),
        _release("nightly"),
        _release("v1.2.0"),
        _release("v1.0.0"),
        "junk",
        {"html_url": "x"},
    ]
    best = uc._pick_latest(releases)
    assert best == ReleaseInfo("1.2.0", f"{RELEASES_PAGE_URL}/tag/v1.2.0")
    assert uc._pick_latest([]) is None
    with pytest.raises(UpdateCheckError):
        uc._pick_latest({"message": "rate limit"})


def test_release_link_never_leaves_the_repository() -> None:
    for bad in (
        "https://evil.example/x",
        "file:///etc/passwd",
        None,
        RELEASES_PAGE_URL + ".evil.io/",
    ):
        best = uc._pick_latest([{"tag_name": "v2.0.0", "html_url": bad}])
        assert best is not None and best.url == RELEASES_PAGE_URL


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_and_check(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def fake_urlopen(request, timeout, context):
        seen["url"] = request.full_url
        seen["agent"] = request.get_header("User-agent")
        return _Response(json.dumps([_release("v99.0.0"), _release("v0.0.1")]).encode())

    monkeypatch.setattr(uc.urllib.request, "urlopen", fake_urlopen)
    latest = uc.check_for_update()
    assert latest is not None and latest.version == "99.0.0"
    assert seen["url"].startswith("https://api.github.com/repos/")
    assert seen["agent"] == f"adv-tg/{__version__}"  # nothing about the user or the machine

    monkeypatch.setattr(
        uc.urllib.request,
        "urlopen",
        lambda *a, **k: _Response(json.dumps([_release("v0.0.1")]).encode()),
    )
    assert uc.check_for_update() is None  # older than the running version

    def offline(*a, **k):
        raise OSError("network unreachable")

    monkeypatch.setattr(uc.urllib.request, "urlopen", offline)
    with pytest.raises(UpdateCheckError):
        uc.check_for_update()
    monkeypatch.setattr(uc.urllib.request, "urlopen", lambda *a, **k: _Response(b"<html>"))
    with pytest.raises(UpdateCheckError):
        uc.check_for_update()


def test_ssl_context_has_certificates() -> None:
    assert uc.ssl_context().cert_store_stats()["x509_ca"] > 0


def test_state_file(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(uc.DISABLE_ENV, raising=False)
    path = tmp_path / "sub" / "update_check.json"
    state = UpdateState(path)
    assert state.enabled and state.due()
    now = time.time()
    state.mark_checked(now)
    assert path.exists()
    again = UpdateState(path)
    assert not again.due(now + 60.0)
    assert again.due(now + CHECK_INTERVAL_S + 1.0)
    assert again.due(now - 3600.0)  # clock set back
    again.enabled = False
    again.save()
    assert not UpdateState(path).due(now + 10 * CHECK_INTERVAL_S)

    path.write_text("{not json", encoding="utf-8")
    broken = UpdateState(path)
    assert broken.enabled and broken.last_check == 0.0

    monkeypatch.setenv(uc.DISABLE_ENV, "1")
    assert not UpdateState(tmp_path / "none.json").due()


# ---------------------------------------------------------------- GUI


@pytest.fixture
def window(qtbot, monkeypatch: pytest.MonkeyPatch):
    from adv_tg.i18n.translator import Translator
    from adv_tg.ui.main_window import MainWin

    monkeypatch.delenv(uc.DISABLE_ENV, raising=False)
    win = MainWin(translator=Translator("en"))
    qtbot.addWidget(win)
    yield win
    win.close()


def _run_check(qtbot, win, result, manual: bool = True):
    def check():
        if isinstance(result, Exception):
            raise result
        return result

    win.update_notifier._check = check
    with qtbot.waitSignal(win.update_notifier.finished, timeout=5000):
        assert win.check_for_updates() if manual else win.update_notifier.start(manual=False)


def test_window_does_not_check_by_itself(window) -> None:
    assert not window.update_notifier.busy
    assert window.lbl_update.isHidden()
    assert window.action_auto_update.isChecked()


def test_newer_release_is_announced_and_linked(qtbot, window, monkeypatch) -> None:
    release = ReleaseInfo("99.0.0", f"{RELEASES_PAGE_URL}/tag/v99.0.0")
    _run_check(qtbot, window, release, manual=False)
    assert not window.lbl_update.isHidden()
    assert "99.0.0" in window.lbl_update.text()
    assert not window.schedule_update_check()  # done for today

    opened = []
    from adv_tg.ui import main_window as mw

    monkeypatch.setattr(
        mw.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True
    )
    assert window.open_update_page()
    assert opened == [release.url]

    window.translator.set_locale("es")
    window.retranslate_ui()
    assert "disponible" in window.lbl_update.text()


def test_up_to_date_and_failure(qtbot, window) -> None:
    _run_check(qtbot, window, None)
    assert window.lbl_update.isHidden()
    assert __version__ in window.statusBar().currentMessage()
    assert not window.open_update_page()

    window._update_state.last_check = 0.0
    _run_check(qtbot, window, UpdateCheckError("sin red"))
    assert window.lbl_update.isHidden()
    assert window._update_state.last_check == 0.0  # a failed check is retried next start
    assert "updates" in window.statusBar().currentMessage()

    _run_check(qtbot, window, RuntimeError("boom"))  # unexpected errors are reported too
    assert window.lbl_update.isHidden()


def test_automatic_check_can_be_turned_off(window) -> None:
    assert window.schedule_update_check(delay_ms=10_000_000)
    window.action_auto_update.setChecked(False)
    assert not window.schedule_update_check()
    assert not UpdateState().enabled  # persisted in the (isolated) configuration folder
    window.action_auto_update.setChecked(True)
    assert UpdateState().enabled


def test_about_dialog_check(qtbot, window) -> None:
    about = window.open_about_dialog(exec_dialog=False)
    window.update_notifier._check = lambda: ReleaseInfo("99.0.0", RELEASES_PAGE_URL)
    with qtbot.waitSignal(window.update_notifier.finished, timeout=5000):
        about.btn_check_updates.click()
    assert "99.0.0" in about.lbl_update.text() and RELEASES_PAGE_URL in about.lbl_update.text()
    assert about.btn_check_updates.isEnabled()
    window.update_notifier._check = lambda: None
    with qtbot.waitSignal(window.update_notifier.finished, timeout=5000):
        about.check_updates()
    assert __version__ in about.lbl_update.text()
    about.close()
