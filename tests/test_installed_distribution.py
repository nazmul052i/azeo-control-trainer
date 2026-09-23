"""Installed workspaces must survive repair, component changes and updates."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_installed_paths_never_use_application_directory(tmp_path, monkeypatch):
    from azeo_control_trainer.config import paths
    from azeo_control_trainer import app
    from azeo_control_trainer.azeo_explorer.project_registry import projects_root

    monkeypatch.setenv("AZEO_WORKSPACE_DIR", str(tmp_path))
    assert paths.data_dir() == tmp_path / "data"
    assert paths.strategies_dir() == tmp_path / "src/strategies"
    assert app._strategies_root() == tmp_path / "src/strategies"
    assert projects_root() == tmp_path / "projects"


def test_seed_is_idempotent_and_preserves_user_database(tmp_path):
    from azeo_control_trainer.config.distribution import prepare_workspace

    bundle, workspace = tmp_path / "bundle", tmp_path / "user"
    (bundle / "projects/Demo").mkdir(parents=True)
    (bundle / "projects/Demo/_project.json").write_text('{"seed": 1}')
    prepare_workspace(bundle, workspace)
    database = workspace / "projects/Demo/memory.db"
    database.write_bytes(b"user data")
    (workspace / "projects/Demo/_project.json").write_text('{"user": true}')
    (bundle / "projects/Demo/_project.json").write_text('{"seed": 2}')
    prepare_workspace(bundle, workspace)
    assert database.read_bytes() == b"user data"
    assert json.loads((workspace / "projects/Demo/_project.json").read_text()) == {"user": True}


def test_seed_publication_retries_transient_windows_directory_denial(tmp_path, monkeypatch):
    from azeo_control_trainer.config import distribution

    bundle, workspace = tmp_path / "bundle", tmp_path / "user"
    (bundle / "projects/Demo").mkdir(parents=True)
    (bundle / "projects/Demo/_project.json").write_text('{"seed": 1}')
    original_rename = Path.rename
    attempts = 0

    def transient_denial(path, destination):
        nonlocal attempts
        if path.name == "content" and Path(destination).name == "projects" and attempts < 2:
            attempts += 1
            raise PermissionError("temporary scanner handle")
        return original_rename(path, destination)

    monkeypatch.setattr(Path, "rename", transient_denial)
    monkeypatch.setattr(distribution.time, "sleep", lambda _seconds: None)
    distribution.prepare_workspace(bundle, workspace)

    assert attempts == 2
    assert (workspace / "projects/Demo/_project.json").is_file()


def test_future_workspace_format_is_refused_without_mutation(tmp_path):
    from azeo_control_trainer.config.distribution import prepare_workspace

    workspace = tmp_path / "user"
    workspace.mkdir()
    marker = workspace / "workspace.json"
    marker.write_text('{"format": 999}')
    before = list(workspace.iterdir())
    with pytest.raises(ValueError, match="format"):
        prepare_workspace(tmp_path / "bundle", workspace)
    assert list(workspace.iterdir()) == before
    assert marker.read_text() == '{"format": 999}'


def test_components_fail_closed_on_malformed_installation(tmp_path):
    from azeo_control_trainer.config.distribution import read_installation

    (tmp_path / "installation.ini").write_text('[Install]\nComponents=unknown\nVersion=0.1.0\n')
    with pytest.raises(ValueError):
        read_installation(tmp_path)


def test_operator_preset_has_runtime_without_engineering_apps():
    from azeo_control_trainer.config.distribution import PRESETS, resolve_components

    selected = resolve_components(PRESETS["operator"])
    assert {"runtime", "help", "operator_station"} <= selected
    assert "control_designer" not in selected
    assert "graphics_designer" not in selected
    assert "pa_designer" not in selected


def test_source_checkout_exposes_all_apps(tmp_path):
    from azeo_control_trainer.config.distribution import COMPONENTS, read_installation

    assert read_installation(tmp_path).components == frozenset(COMPONENTS)


def test_installation_record_normalizes_former_engineering_component_ids(tmp_path):
    from azeo_control_trainer.config.distribution import read_installation

    (tmp_path / "installation.ini").write_text(
        "[Install]\nVersion=0.4.0\n"
        "Components=runtime,help,control_studio,graphics_studio,procedure_pilot\n",
        encoding="utf-8",
    )
    assert read_installation(tmp_path).components == {
        "runtime", "help", "control_designer", "graphics_designer", "pa_designer",
    }


def test_release_manifest_supplies_version_build_and_channel(tmp_path):
    from azeo_control_trainer.config.distribution import read_build_information

    (tmp_path / "installation.ini").write_text(
        "[Install]\nVersion=0.4.0\nComponents=runtime,help\n",
        encoding="utf-8",
    )
    (tmp_path / "portable-manifest.json").write_text(json.dumps({
        "version": "0.4.0",
        "source_commit": "0123456789abcdef0123456789abcdef01234567",
        "source_dirty": False,
        "built_at_utc": "2026-09-21T15:30:00+00:00",
        "platform": "windows-x64",
        "python": "3.13.0",
    }), encoding="utf-8")
    build = read_build_information(tmp_path)
    assert build.version == "0.4.0"
    assert build.build == "0123456789ab"
    assert build.distribution == "Installed"
    assert build.source_dirty is False
    assert not build.warning


def test_damaged_release_manifest_keeps_about_diagnostics_available(tmp_path):
    from azeo_control_trainer.config.distribution import read_build_information

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="test"\nversion="0.4.0"\n', encoding="utf-8")
    (tmp_path / "portable-manifest.json").write_text(
        '{"version":"not-a-version","source_commit":"run me"}', encoding="utf-8")
    build = read_build_information(tmp_path)
    assert build.version == "0.4.0"
    assert build.distribution == "Portable"
    assert "repair" in build.warning.casefold()


def test_uninstalled_application_is_refused_before_qt_startup(monkeypatch):
    from azeo_control_trainer import app
    from azeo_control_trainer.config import distribution
    monkeypatch.setattr(distribution, "component_available", lambda component: False)
    assert app.main(surface="control") == 2


def test_cross_application_handoff_does_not_open_missing_component(monkeypatch):
    from azeo_control_trainer.core.presentation import installed_apps
    monkeypatch.setattr(installed_apps, "component_available", lambda component: False)
    from azeo_control_trainer.core.presentation import headless
    monkeypatch.setattr(headless, "is_headless", lambda: True)
    called = []

    class Window:
        @installed_apps.requires_component("control_designer")
        def open(self):
            called.append(True)

    window = Window()
    assert not installed_apps.action_available(window.open)
    assert window.open() is None
    assert not called
