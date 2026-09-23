"""Release inputs reject mixed payloads and unsafe archive paths."""
import importlib.util
import json
from pathlib import Path
import sys
import tomllib
from types import SimpleNamespace
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("installer_builder", ROOT / "tools/build_windows_installer.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


load("build_plant_core", ROOT / "tools/build_plant_core.py")
app_builder = load("windows_app_builder", ROOT / "tools/build_windows_app.py")


@pytest.mark.parametrize("name", ["../outside", "C:/outside", "/outside", "a\\..\\outside", "a/./b", "",
                                 "a//b", "a/.. /b", "a/*.dll", "a/CON.txt", "a/name."])
def test_unsafe_payload_names_rejected(name):
    with pytest.raises(ValueError):
        builder.safe_relative(name)


def test_payload_hash_checked_before_packaging(tmp_path):
    (tmp_path / "app.py").write_text("original")
    manifest = {"files": {"app.py": builder.sha256(tmp_path / "app.py")}}
    (tmp_path / "portable-manifest.json").write_text(json.dumps(manifest))
    reports = [{"mode": mode, "ok": True} for mode in builder.REQUIRED_CHECKS]
    (tmp_path / "verification.json").write_text(json.dumps(reports))
    (tmp_path / "app.py").write_text("modified")
    with pytest.raises(ValueError, match="hash mismatch"):
        builder.verify_payload(tmp_path)


def test_release_refuses_missing_help_qualification(tmp_path):
    (tmp_path / "portable-manifest.json").write_text('{"files": {}}')
    (tmp_path / "verification.json").write_text("[]")
    with pytest.raises(ValueError, match="all nine"):
        builder.verify_payload(tmp_path)


def test_portable_source_inventory_skips_tracked_paths_deleted_in_candidate(tmp_path, monkeypatch):
    (tmp_path / "present.py").write_text("present", encoding="utf-8")
    monkeypatch.setattr(app_builder, "ROOT", tmp_path)
    monkeypatch.setattr(app_builder, "SOURCE_ROOTS", (".",))
    monkeypatch.setattr(app_builder.subprocess, "check_output",
                        lambda *args, **kwargs: b"deleted.py\0present.py\0")
    assert app_builder.repository_source_paths() == (Path("present.py"),)


def test_private_runtime_includes_the_hardened_xml_parser():
    assert "defusedxml" in app_builder.REQUIREMENTS


def test_private_runtime_requires_the_audited_anyio_floor():
    assert "anyio>=4.14.2,<5" in app_builder.REQUIREMENTS


def test_private_runtime_uses_qt_essentials_without_addons_or_webengine():
    requirements = " ".join(app_builder.REQUIREMENTS).casefold()
    assert "pyside6-essentials" in requirements
    assert "pyside6-addons" not in requirements
    assert "qtwebengine" not in requirements
    assert app_builder.prohibited_qt_path(Path("PySide6/QtWebEngineWidgets.pyi"))
    assert app_builder.prohibited_qt_path(Path("PySide6/qml/QtQuick3D/plugin.dll"))
    assert app_builder.prohibited_qt_path(
        Path("PySide6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll"))
    assert not app_builder.prohibited_qt_path(Path("PySide6/QtWidgets.pyd"))


def test_pid_help_does_not_pull_chromium_into_the_release():
    source = (ROOT / "src/azeo_control_trainer/core/pid/widgets/help_dialog.py").read_text()
    assert "QtWebEngine" not in source
    assert "QTextBrowser" in source


def test_release_declares_one_root_licence_and_its_exceptions():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["license"] == {"file": "LICENSE"}
    for relative in ("NOTICE", "legal/GPL-3.0.txt", "legal/LGPL-3.0.txt",
                     "legal/OPEN_SOURCE_COMPLIANCE.md"):
        assert (ROOT / relative).is_file()
        assert relative.split("/", 1)[0] in app_builder.SOURCE_ROOTS
    assert (ROOT / "LICENSE").is_file()
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    assert "core/hmi/assets/symbols/LICENSE" in notice
    assert "LICENSE_DEJAVU" in notice


def test_vendor_reference_material_is_not_a_release_source():
    sources = "\n".join(app_builder.SOURCE_ROOTS)
    assert "PAReferences" not in sources
    assert "PlantPAX" not in sources


def test_searchable_user_manual_pdf_is_a_required_help_payload():
    manual = "output/pdf/Azeo_Control_Trainer_User_Manual.pdf"
    assert manual in app_builder.GENERATED_HELP_FILES
    assert manual in app_builder.SOURCE_ROOTS
    assert builder.component_for_file(manual) == "help"
    for product in ("Explorer", "Control_Designer", "Graphics_Designer",
                    "Operator_Station", "Simulation_Workbench", "PA_Designer"):
        product_pdf = f"output/pdf/Azeo_{product}_User_Manual.pdf"
        assert product_pdf in app_builder.GENERATED_HELP_FILES
        assert product_pdf in app_builder.SOURCE_ROOTS
        assert builder.component_for_file(product_pdf) == "help"


@pytest.mark.parametrize("initial_status,expected_scripts", [
    (0, ["document_release.py"]),
    (1, ["document_release.py", "build_user_manual.py",
         "build_product_manuals.py", "document_release.py"]),
])
def test_release_build_prepares_missing_or_stale_manuals(
        tmp_path, monkeypatch, initial_status, expected_scripts):
    monkeypatch.setattr(app_builder, "ROOT", tmp_path)
    statuses = iter((initial_status, 0))
    scripts = []

    def run(command, **_kwargs):
        script = Path(command[1]).name
        scripts.append(script)
        return SimpleNamespace(returncode=next(statuses) if script == "document_release.py" else 0)

    monkeypatch.setattr(app_builder.subprocess, "run", run)
    app_builder.prepare_generated_help()
    assert scripts == expected_scripts


def test_optional_launchers_and_sdk_have_real_file_components():
    assert builder.component_for_file("AzeoControlDesigner.exe") == "control_designer"
    assert builder.component_for_file("AzeoOperatorStation.exe") == "operator_station"
    assert builder.component_for_file("AzeoPADesigner.exe") == "pa_designer"
    assert builder.component_for_file("sdk/bin/azeocore.dll") == "sdk"
    assert builder.component_for_file("docs/USER_MANUAL.md") == "help"
    assert builder.component_for_file(
        "output/pdf/Azeo_Control_Trainer_User_Manual.pdf.manifest.json") == "help"
    assert builder.component_for_file("runtime/python313.dll") == "runtime"


@pytest.mark.parametrize("test_identity,group", [(False, "Azeo"), (True, "Azeo Setup Qualification")])
def test_qualification_shortcuts_use_an_isolated_compiled_group(tmp_path, monkeypatch, test_identity, group):
    # Inno ignores /GROUP when the program-group page is disabled. Isolation
    # therefore has to be compiled into Setup, not supplied by the test runner.
    root = tmp_path / "source"
    template = root / "tools/windows/azeo.iss"
    template.parent.mkdir(parents=True)
    template.write_text((ROOT / "tools/windows/azeo.iss").read_text(encoding="utf-8"), encoding="utf-8")
    archive_path = root / "dist/azeocore-sdk-windows-x64.zip"
    archive_path.parent.mkdir()
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("azeocore-sdk-windows-x64/bin/azeocore.dll", b"matching native payload")
    payload = tmp_path / "payload"
    dll = payload / "AzeoPlantSimulator/azeoplant/azeocore.dll"
    dll.parent.mkdir(parents=True)
    dll.write_bytes(b"matching native payload")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(builder, "ROOT", root)
    builder.write_inputs(payload, {"files": {}}, work, "0.2.0", tmp_path / "output",
                         test_identity=test_identity)
    script = (work / "azeo.iss").read_text(encoding="utf-8-sig")
    assert f'#define ProgramGroup "{group}"' in script
    assert "DefaultGroupName={#ProgramGroup}" in script
    assert "DisableProgramGroupPage=yes" in script
    expected_mutex = ("AzeoSuite.SetupQualification.Running" if test_identity
                      else "AzeoSuite.Running")
    assert expected_mutex in script
    assert "AppMutex={#AppMutexName}" in script
    shortcuts = (work / "shortcuts.iss").read_text(encoding="utf-8-sig")
    assert "Azeo Control Designer" in shortcuts
    assert "Azeo Graphics Designer" in shortcuts
    assert "Azeo PA Designer" in shortcuts
    assert 'Azeo Control Studio.lnk"' in shortcuts
    assert "AzeoControlStudio.exe" in shortcuts
    assert 'Azeo Graphics Studio.lnk"' in shortcuts
    assert "AzeoGraphicsStudio.exe" in shortcuts
    assert 'Azeo Procedure Pilot.lnk"' in shortcuts
    assert "AzeoProcedurePilot.exe" in shortcuts
    for title, filename in builder.USER_GUIDES:
        assert (f'Name: "{{group}}\\User Guide\\{title}"; '
                f'Filename: "{{app}}\\versions\\0.2.0\\output\\pdf\\{filename}"; '
                'Components: help{#SwitchCheck}') in shortcuts
