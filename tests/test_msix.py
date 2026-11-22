"""Windows MSIX / Microsoft Store runtime helpers, UI policy compliance and packaging assets."""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from adv_tg.utils import msix
from adv_tg.utils import update_check as uc

ROOT = Path(__file__).resolve().parents[1]
MSIX_DIR = ROOT / "packaging" / "windows" / "msix"
MANIFEST_TEMPLATE = MSIX_DIR / "AppxManifest.xml.in"


# ====================================================================== Win32 AppModel mock
class _FakeKernel32:
    """Minimal stand-in for kernel32's GetCurrentPackage*Name Win32 APIs."""

    def __init__(self, full: str | None = None, family: str | None = None) -> None:
        self._values = {
            "GetCurrentPackageFullName": full,
            "GetCurrentPackageFamilyName": family,
        }

    def _call(self, key: str, length_ptr, buf) -> int:
        value = self._values[key]
        if value is None:
            return 15700  # APPMODEL_ERROR_NO_PACKAGE
        needed = len(value) + 1  # UTF-16 characters including NUL terminator
        length_obj = length_ptr._obj
        if buf is None or length_obj.value < needed:
            length_obj.value = needed
            return 122  # ERROR_INSUFFICIENT_BUFFER
        buf.value = value
        length_obj.value = needed
        return 0  # ERROR_SUCCESS

    def GetCurrentPackageFullName(self, length_ptr, buf) -> int:  # noqa: N802
        return self._call("GetCurrentPackageFullName", length_ptr, buf)

    def GetCurrentPackageFamilyName(self, length_ptr, buf) -> int:  # noqa: N802
        return self._call("GetCurrentPackageFamilyName", length_ptr, buf)


def test_appmodel_query_packaged_and_unpackaged() -> None:
    fake = _FakeKernel32(
        full="SPQR14.AdvancedTimegrapher_1.1.1.0_x64__abc123def4567",
        family="SPQR14.AdvancedTimegrapher_abc123def4567",
    )
    assert (
        msix._query_appmodel_string("GetCurrentPackageFullName", _kernel32=fake)
        == "SPQR14.AdvancedTimegrapher_1.1.1.0_x64__abc123def4567"
    )
    assert (
        msix._query_appmodel_string("GetCurrentPackageFamilyName", _kernel32=fake)
        == "SPQR14.AdvancedTimegrapher_abc123def4567"
    )

    unpackaged = _FakeKernel32(full=None, family=None)
    assert msix._query_appmodel_string("GetCurrentPackageFullName", _kernel32=unpackaged) is None
    assert msix._query_appmodel_string("GetCurrentPackageFamilyName", _kernel32=unpackaged) is None
    assert msix._query_appmodel_string("MissingWin32Func", _kernel32=object()) is None


def test_msix_external_path_redirects_local_and_roaming_appdata() -> None:
    fam = "SPQR14.AdvancedTimegrapher_abc123"
    local = "/users/alice/AppData/Local"
    roaming = "/users/alice/AppData/Roaming"

    # Unpackaged -> untouched
    p_logs = Path(local) / "adv_tg" / "logs"
    assert (
        msix.msix_external_path(p_logs, family="", local_appdata=local, appdata=roaming) == p_logs
    )

    # LOCALAPPDATA -> LocalCache/Local
    assert msix.msix_external_path(p_logs, family=fam, local_appdata=local, appdata=roaming) == (
        Path(local) / "Packages" / fam / "LocalCache" / "Local" / "adv_tg" / "logs"
    )

    # APPDATA -> LocalCache/Roaming (case-insensitive prefix match)
    p_cfg = Path("/USERS/ALICE/APPDATA/ROAMING/adv_tg")
    assert msix.msix_external_path(p_cfg, family=fam, local_appdata=local, appdata=roaming) == (
        Path(local) / "Packages" / fam / "LocalCache" / "Roaming" / "adv_tg"
    )

    # Already inside Packages/<fam> -> never double-redirected
    already = Path(local) / "Packages" / fam / "LocalCache" / "Local" / "adv_tg" / "logs"
    assert (
        msix.msix_external_path(already, family=fam, local_appdata=local, appdata=roaming)
        == already
    )

    # Unrelated path -> untouched
    other = Path("/tmp/adv_tg_recording")
    assert msix.msix_external_path(other, family=fam, local_appdata=local, appdata=roaming) == other


# ====================================================================== Store runtime compliance
def test_msix_disables_automatic_update_check(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(uc.DISABLE_ENV, raising=False)
    monkeypatch.setattr(uc, "is_msix_packaged", lambda: True)
    state = uc.UpdateState(tmp_path / "update_check.json")
    assert state.enabled
    assert not state.due()


def test_msix_hides_update_and_donation_ui_and_resolves_open_folder(
    qtbot, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("PyQt6")
    from adv_tg.i18n.translator import Translator
    from adv_tg.ui import dialogs as dlg_mod
    from adv_tg.ui import main_window as mw

    monkeypatch.setattr(msix, "is_msix_packaged", lambda: True)
    monkeypatch.setattr(uc, "is_msix_packaged", lambda: True)
    monkeypatch.setattr(mw, "is_msix_packaged", lambda: True)
    monkeypatch.setattr(dlg_mod, "is_msix_packaged", lambda: True)

    win = mw.MainWin(translator=Translator("en"))
    qtbot.addWidget(win)
    try:
        assert not win.action_check_updates.isVisible()
        assert not win.action_auto_update.isVisible()
        assert not win.schedule_update_check()
        assert not win.check_for_updates()

        about = win.open_about_dialog(exec_dialog=False)
        try:
            assert not about.btn_check_updates.isVisible()
            assert not about.lbl_update.isVisible()
            assert not about.btn_buy_coffee.isVisible()
            assert not about.lbl_support_project.isVisible()
        finally:
            about.close()
    finally:
        win.close()

    # open_folder resolves virtualized LocalAppData into Packages/<fam>/LocalCache/Local
    local_root = tmp_path / "Local"
    logs_dir = local_root / "adv_tg" / "logs"
    expected = local_root / "Packages" / "Pkg_fam" / "LocalCache" / "Local" / "adv_tg" / "logs"
    monkeypatch.setattr(
        dlg_mod,
        "msix_external_path",
        lambda p: msix.msix_external_path(
            p, family="Pkg_fam", local_appdata=str(local_root), appdata=str(tmp_path / "Roaming")
        ),
    )
    opened: list[str] = []
    monkeypatch.setattr(
        dlg_mod.QDesktopServices,
        "openUrl",
        lambda url: opened.append(url.toLocalFile()) or True,
    )
    assert dlg_mod.open_folder(logs_dir)
    assert logs_dir.is_dir()
    assert Path(opened[-1]).resolve() == expected.resolve()


# ====================================================================== Manifest & assets
def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a valid PNG"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def test_msix_manifest_template_is_valid_and_store_compliant() -> None:
    identity_name = "AlbertoPico.AdvancedTimegrapher"
    publisher = "CN=2B353A7B-439C-4198-A61A-63000DFBF72B"
    publisher_display = "Alberto Pico"

    # Verify build script and CI workflow carry the exact Partner Center identity defaults
    ps1 = (ROOT / "packaging" / "windows" / "build_msix.ps1").read_text(encoding="utf-8")
    yml = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    for expected_val in (identity_name, publisher, publisher_display):
        assert expected_val in ps1
        assert expected_val in yml

    text = (
        MANIFEST_TEMPLATE.read_text(encoding="utf-8")
        .replace("@MSIX_IDENTITY_NAME@", identity_name)
        .replace("@MSIX_PUBLISHER@", publisher)
        .replace("@MSIX_PUBLISHER_DISPLAY_NAME@", publisher_display)
        .replace("@MSIX_VERSION@", "1.1.1.0")
    )
    root = ET.fromstring(text)
    ns_f = "http://schemas.microsoft.com/appx/manifest/foundation/windows10"
    ns_uap = "http://schemas.microsoft.com/appx/manifest/uap/windows10"
    ns_uap5 = "http://schemas.microsoft.com/appx/manifest/uap/windows10/5"
    ns_rescap = (
        "http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
    )

    assert root.tag == f"{{{ns_f}}}Package"
    identity = root.find(f"{{{ns_f}}}Identity")
    assert identity is not None
    assert identity.attrib["ProcessorArchitecture"] == "x64"
    assert identity.attrib["Version"] == "1.1.1.0"

    tdf = root.find(f"{{{ns_f}}}Dependencies/{{{ns_f}}}TargetDeviceFamily")
    assert tdf is not None and tdf.attrib["Name"] == "Windows.Desktop"

    app = root.find(f"{{{ns_f}}}Applications/{{{ns_f}}}Application")
    assert app is not None
    assert app.attrib["Executable"] == "adv-tg.exe"
    assert app.attrib["EntryPoint"] == "Windows.FullTrustApplication"

    alias = app.find(
        f"{{{ns_f}}}Extensions/{{{ns_uap5}}}Extension/{{{ns_uap5}}}AppExecutionAlias/{{{ns_uap5}}}ExecutionAlias"
    )
    assert alias is not None and alias.attrib["Alias"] == "adv-tg.exe"

    # Strict XSD order: <rescap:Capability Name="runFullTrust" /> MUST precede <DeviceCapability />
    caps = root.find(f"{{{ns_f}}}Capabilities")
    assert caps is not None
    children = list(caps)
    tags_and_names = [(c.tag, c.attrib.get("Name")) for c in children]
    assert tags_and_names == [
        (f"{{{ns_rescap}}}Capability", "runFullTrust"),
        (f"{{{ns_f}}}DeviceCapability", "microphone"),
    ]

    # Visual elements must reference Assets/*.png whose scale-100/200 files exist
    ve = app.find(f"{{{ns_uap}}}VisualElements")
    assert ve is not None
    assert ve.attrib["Square44x44Logo"] == r"Assets\Square44x44Logo.png"
    assert ve.attrib["Square150x150Logo"] == r"Assets\Square150x150Logo.png"


def test_msix_visual_assets_exist_with_exact_dimensions() -> None:
    expected = {
        "Assets/StoreLogo.scale-100.png": (50, 50),
        "Assets/StoreLogo.scale-200.png": (100, 100),
        "Assets/Square44x44Logo.scale-100.png": (44, 44),
        "Assets/Square44x44Logo.scale-200.png": (88, 88),
        "Assets/Square44x44Logo.targetsize-16.png": (16, 16),
        "Assets/Square44x44Logo.targetsize-24.png": (24, 24),
        "Assets/Square44x44Logo.targetsize-32.png": (32, 32),
        "Assets/Square44x44Logo.targetsize-48.png": (48, 48),
        "Assets/Square44x44Logo.targetsize-256.png": (256, 256),
        "Assets/Square44x44Logo.targetsize-16_altform-unplated.png": (16, 16),
        "Assets/Square44x44Logo.targetsize-24_altform-unplated.png": (24, 24),
        "Assets/Square44x44Logo.targetsize-32_altform-unplated.png": (32, 32),
        "Assets/Square44x44Logo.targetsize-48_altform-unplated.png": (48, 48),
        "Assets/Square44x44Logo.targetsize-256_altform-unplated.png": (256, 256),
        "Assets/Square150x150Logo.scale-100.png": (150, 150),
        "Assets/Square150x150Logo.scale-200.png": (300, 300),
        "Assets/Wide310x150Logo.scale-100.png": (310, 150),
        "Assets/Wide310x150Logo.scale-200.png": (620, 300),
        "StoreListing/AppIcon-300x300.png": (300, 300),
    }
    for rel, dims in expected.items():
        path = MSIX_DIR / rel
        assert path.is_file(), f"Missing MSIX visual asset: {path}"
        assert _png_size(path) == dims, f"Wrong dimensions for {path}"
