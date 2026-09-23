"""The five named products expose stable, lazy composition roots."""

from __future__ import annotations

import ast
import importlib
import os
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from azeo_control_trainer.app import _startup_surface  # noqa: E402
from azeo_control_trainer.config.applications import (  # noqa: E402
    APPLICATIONS,
    ApplicationId,
    application,
    application_for_surface,
)
from azeo_control_trainer.config.paths import package_dir, project_root  # noqa: E402


def test_catalog_has_one_complete_descriptor_per_product() -> None:
    assert {item.id for item in APPLICATIONS} == set(ApplicationId)
    for field in ("title", "package", "command", "surface", "log_name"):
        values = [getattr(item, field) for item in APPLICATIONS]
        assert len(values) == len(set(values)), field
        assert all(values), field

    assert application("graphics_designer").title == "Azeo Graphics Designer"
    assert application_for_surface("station").id is ApplicationId.OPERATOR_STATION
    with pytest.raises(ValueError):
        application("unknown")


@pytest.mark.parametrize("descriptor", APPLICATIONS, ids=lambda item: item.id.value)
def test_application_package_import_is_qt_free(descriptor) -> None:
    script = (
        f"import {descriptor.package}; import sys; "
        "raise SystemExit('PySide6' in sys.modules)"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_console_entry_points_follow_the_catalog() -> None:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        scripts = tomllib.load(stream)["project"]["scripts"]

    for descriptor in APPLICATIONS:
        assert scripts[descriptor.command] == f"{descriptor.package}:main"


def test_public_window_aliases_resolve_to_the_existing_implementations() -> None:
    aliases = {
        "azeo_control_trainer.azeo_explorer": (
            "ExplorerWindow",
            "azeo_control_trainer.azeo_explorer.window",
            "ExplorerWindow",
        ),
        "azeo_control_trainer.azeo_control_designer": (
            "ControlDesignerWindow",
            "azeo_control_trainer.azeo_control_designer.designer_window",
            "StrategyDesignerWindow",
        ),
        "azeo_control_trainer.azeo_graphics_designer": (
            "GraphicsDesignerWindow",
            "azeo_control_trainer.azeo_graphics_designer.window",
            "HmiStudioWindow",
        ),
        "azeo_control_trainer.azeo_operator_station": (
            "OperatorStationWindow",
            "azeo_control_trainer.azeo_operator_station",
            "LiveStation",
        ),
        "azeo_control_trainer.azeo_simulation_workbench": (
            "SimulationWorkbenchWindow",
            "azeo_control_trainer.azeo_simulation_workbench.window",
            "SimulationWorkbenchDialog",
        ),
    }
    for package_name, (public_name, implementation_name, concrete_name) in aliases.items():
        public = importlib.import_module(package_name)
        implementation = importlib.import_module(implementation_name)
        assert getattr(public, public_name) is getattr(implementation, concrete_name)


def test_launcher_selects_every_public_product_surface() -> None:
    assert _startup_surface([]) == "explorer"
    assert _startup_surface(["--classic"]) == "control"
    assert _startup_surface(["--graphics"]) == "graphics"
    assert _startup_surface(["--simulation"]) == "simulation"
    assert _startup_surface(["--simulator"]) == "simulation"
    assert _startup_surface(["--classic", "--station"]) == "station"
    for descriptor in APPLICATIONS:
        assert _startup_surface([], requested=descriptor.surface) == descriptor.surface
    with pytest.raises(ValueError):
        _startup_surface([], requested="unknown")


def test_cross_product_calls_use_public_application_packages() -> None:
    app_source = (SRC / "azeo_control_trainer" / "app.py").read_text(encoding="utf-8")
    designer_source = (
        SRC
        / "azeo_control_trainer"
        / "azeo_control_designer"
        / "designer_window.py"
    ).read_text(encoding="utf-8")
    explorer_source = (
        SRC
        / "azeo_control_trainer"
        / "azeo_explorer"
        / "window.py"
    ).read_text(encoding="utf-8")

    assert "azeo_control_trainer.azeo_control_designer" in app_source
    assert "azeo_control_trainer.azeo_graphics_designer" in designer_source
    assert "azeo_control_trainer.azeo_operator_station" in designer_source
    assert "azeo_control_trainer.azeo_simulation_workbench" in explorer_source


def test_obsolete_generic_ui_tree_has_been_retired() -> None:
    """Product implementations belong to named packages, not a catch-all UI."""

    assert not (SRC / "azeo_control_trainer" / "ui").exists()


def test_source_root_uses_the_product_oriented_layout() -> None:
    package_root = SRC / "azeo_control_trainer"
    required = {
        "azeo_explorer",
        "azeo_control_designer",
        "azeo_graphics_designer",
        "azeo_operator_station",
        "azeo_simulation_workbench",
        "core",
        "connectivity",
        "plant",
        "config",
    }
    retired = {
        "applications",
        "azeo_hmi",
        "fieldio",
        "opcua_server",
        "pid_widgets",
        "presentation",
        "simulation",
        "strategy",
        "ui",
    }

    assert all((package_root / name).is_dir() for name in required)
    assert all(not (package_root / name).exists() for name in retired)
    assert (package_root / "core" / "strategy").is_dir()
    assert (package_root / "core" / "simulation").is_dir()
    assert (package_root / "core" / "datastore").is_dir()
    assert (package_root / "core" / "pid").is_dir()
    assert (package_root / "connectivity" / "fieldio").is_dir()
    assert (package_root / "connectivity" / "opcua").is_dir()


def test_relocated_path_service_resolves_the_checkout_and_package() -> None:
    """Moving the path service must not redirect data beneath ``src``."""

    assert project_root() == ROOT
    assert package_dir() == SRC / "azeo_control_trainer"


def test_each_product_physically_owns_its_primary_window() -> None:
    package_root = SRC / "azeo_control_trainer"
    expected = {
        "azeo_explorer": "window.py",
        "azeo_control_designer": "designer_window.py",
        "azeo_graphics_designer": "window.py",
        "azeo_operator_station": "console.py",
        "azeo_simulation_workbench": "window.py",
    }
    for package, primary_window in expected.items():
        assert (package_root / package / primary_window).is_file()


def test_shared_layers_do_not_depend_on_product_packages() -> None:
    """Dependency direction stays product -> shared, never shared -> product."""

    shared_roots = ("core", "connectivity", "plant", "config")
    forbidden = tuple(item.package for item in APPLICATIONS)
    offenders = []
    for root_name in shared_roots:
        for source in (SRC / "azeo_control_trainer" / root_name).rglob("*.py"):
            module = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            imported = []
            for node in ast.walk(module):
                if isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.append(node.module)
            if any(name == prefix or name.startswith(prefix + ".")
                   for name in imported for prefix in forbidden):
                offenders.append(source.relative_to(SRC).as_posix())
    assert offenders == []


def test_products_do_not_import_another_products_private_modules() -> None:
    """Cross-product navigation uses the public package, never its internals."""

    package_root = SRC / "azeo_control_trainer"
    product_names = {item.package.rsplit(".", 1)[-1] for item in APPLICATIONS}
    prefix = "azeo_control_trainer."
    offenders: list[str] = []

    for owner in product_names:
        for source in (package_root / owner).rglob("*.py"):
            module = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(module):
                if not isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                if isinstance(node, ast.ImportFrom) and node.level >= 2 and node.module:
                    target = node.module.split(".")[0]
                    if target != owner and target in product_names:
                        offenders.append(
                            f"{source.relative_to(SRC).as_posix()}:{node.lineno} -> "
                            f"{'.' * node.level}{node.module}"
                        )
                imported = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                for name in imported:
                    if not name.startswith(prefix):
                        continue
                    parts = name[len(prefix):].split(".")
                    target = parts[0]
                    if target != owner and target in product_names and len(parts) > 1:
                        offenders.append(
                            f"{source.relative_to(SRC).as_posix()}:{node.lineno} -> {name}"
                        )

    assert offenders == []
