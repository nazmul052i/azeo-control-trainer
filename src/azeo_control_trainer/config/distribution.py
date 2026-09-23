"""Installed features and version-independent user data; source runs stay unchanged.

Components describe supported application entry points, not an authorization
boundary. The controller's hidden Qt host remains a shared runtime dependency.
"""
from configparser import ConfigParser, Error as ConfigError
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
import time
import tomllib

from .applications import APPLICATIONS

WORKSPACE_FORMAT = 1
COMPONENTS = {
    "runtime": "Shared Azeo runtime (required)",
    "help": "Offline Help Center and manuals (required)",
    **{app.id.value: app.title for app in APPLICATIONS},
    "sdk": "C++ SDK and Python integration reference",
}
PRESETS = {
    "full": tuple(COMPONENTS),
    "engineering": ("explorer", "control_designer", "graphics_designer", "pa_designer"),
    "operator": ("operator_station",),
    "custom": ("explorer",),
}
LAUNCHERS = {
    "explorer": ("Azeo.exe", ""),
    "control_designer": ("AzeoControlDesigner.exe", "--classic"),
    "graphics_designer": ("AzeoGraphicsDesigner.exe", "--graphics"),
    "operator_station": ("AzeoOperatorStation.exe", "--station"),
    "pa_designer": ("AzeoPADesigner.exe", "--procedures"),
    "simulation_workbench": ("AzeoSimulationWorkbench.exe", "--simulation"),
}

# Setup 0.4 renamed the engineering component identifiers. Installation records
# written by an earlier build remain valid and are normalized at the boundary.
_LEGACY_COMPONENT_IDS = {
    "control_studio": "control_designer",
    "graphics_studio": "graphics_designer",
    "procedure_pilot": "pa_designer",
}


def version_tuple(version):
    if not re.fullmatch(r"\d{1,4}\.\d{1,4}\.\d{1,4}", version):
        raise ValueError("Version must be major.minor.patch (each part 0..9999)")
    return tuple(map(int, version.split(".")))


def resolve_components(components):
    result = frozenset(components) | {"runtime", "help"}
    unknown = result - COMPONENTS.keys()
    if unknown:
        raise ValueError(f"Unknown installation components: {', '.join(sorted(unknown))}")
    return result


@dataclass(frozen=True)
class Installation:
    version: str
    components: frozenset[str]
    installed: bool


@dataclass(frozen=True)
class BuildInformation:
    """Release identity shown by Help/About without trusting free-form data."""

    version: str
    distribution: str
    commit: str = ""
    source_dirty: bool | None = None
    built_at_utc: str = ""
    target: str = ""
    packaged_python: str = ""
    warning: str = ""

    @property
    def build(self):
        if self.commit:
            return self.commit[:12]
        if self.distribution == "Development source checkout":
            return "Not packaged"
        return "Unavailable"


def read_installation(root=None):
    from .paths import project_root
    path = Path(root or project_root()) / "installation.ini"
    if not path.is_file():
        return Installation("source / portable", frozenset(COMPONENTS), False)
    config = ConfigParser(interpolation=None)
    try:
        config.read_string(path.read_text(encoding="utf-8-sig"))
        version = config["Install"]["Version"]
        version_tuple(version)
        components = frozenset(
            _LEGACY_COMPONENT_IDS.get(component, component)
            for component in config["Install"]["Components"].split(",")
        )
        if resolve_components(components) != components:
            raise ValueError("Required runtime/help component is missing")
        return Installation(version, components, True)
    except (KeyError, ConfigError, OSError) as error:
        raise ValueError(f"Invalid installation record: {path}. Run Setup to repair.") from error


def _source_version(root: Path) -> str:
    """Read the checkout's canonical version; packaged builds use their manifest."""
    try:
        value = tomllib.loads(
            (root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        version_tuple(value)
        return value
    except (KeyError, OSError, TypeError, tomllib.TOMLDecodeError, ValueError):
        return "unknown"


def read_build_information(root=None) -> BuildInformation:
    """Return the build identity for installed, portable, and source layouts.

    ``portable-manifest.json`` is release evidence, but it is still a local
    file.  Only bounded, validated scalar fields are exposed to UI or support
    reports; dependency/file maps and arbitrary manifest content never are.
    A damaged manifest must leave About usable so it can tell the operator to
    repair the installation.
    """
    from .paths import project_root

    base = Path(root or project_root())
    installation = read_installation(base)
    manifest_path = base / "portable-manifest.json"
    if not manifest_path.is_file():
        return BuildInformation(
            installation.version if installation.installed else _source_version(base),
            "Installed" if installation.installed else "Development source checkout",
            warning=("Release manifest is missing; run Setup to repair."
                     if installation.installed else ""),
        )

    distribution = "Installed" if installation.installed else "Portable"
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("manifest root is not an object")
        version = str(document["version"])
        version_tuple(version)
        if installation.installed and version != installation.version:
            raise ValueError("installation and manifest versions disagree")
        commit = str(document.get("source_commit", ""))
        if commit and not re.fullmatch(r"[0-9a-fA-F]{7,64}", commit):
            raise ValueError("source commit is invalid")
        dirty = document.get("source_dirty")
        if dirty is not None and type(dirty) is not bool:
            raise ValueError("source state is invalid")
        built = str(document.get("built_at_utc", ""))
        if built:
            datetime.fromisoformat(built.replace("Z", "+00:00"))
        target = str(document.get("platform", ""))
        runtime = str(document.get("python", ""))
        if any(len(value) > 256 for value in (built, target, runtime)):
            raise ValueError("manifest identity field is too long")
        return BuildInformation(
            version, distribution, commit.lower(), dirty, built, target, runtime)
    except (KeyError, OSError, TypeError, json.JSONDecodeError, ValueError):
        return BuildInformation(
            installation.version if installation.installed else _source_version(base),
            distribution,
            warning="Release manifest is invalid; run Setup to repair.",
        )


def component_available(component):
    return component in read_installation().components


def default_workspace():
    override = os.environ.get("AZEO_WORKSPACE_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Azeo/Workspace"


def _check_workspace_format(workspace):
    marker = workspace / "workspace.json"
    if marker.exists():
        value = json.loads(marker.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or type(value.get("format")) is not int
                or value["format"] != WORKSPACE_FORMAT):
            raise ValueError("Unsupported workspace format. Restore a compatible backup or use the matching Azeo version.")


def _publish_workspace_seed(seed, destination, *, attempts=20):
    """Atomically publish a copied seed despite short-lived Windows file handles."""

    for attempt in range(attempts):
        try:
            seed.rename(destination)
            return
        except PermissionError:
            if attempt + 1 == attempts:
                raise
            # Defender, Search Indexer and Explorer may briefly open a file in
            # the copied tree without delete sharing, which blocks renaming its
            # parent on Windows. The seed remains private until this succeeds.
            time.sleep(min(0.05 * (attempt + 1), 0.5))


def prepare_workspace(bundle, workspace):
    """Seed a writable workspace once; upgrades never merge over user projects.

    QLockFile handles dead-process recovery and concurrent first launches. Qt
    needs no QApplication for this file lock. Directory rename publishes a
    complete seed, so an interrupted copy cannot become a half-created project.
    """
    from PySide6.QtCore import QLockFile
    bundle, workspace = Path(bundle).resolve(), Path(workspace).resolve()
    if workspace == bundle or workspace.is_relative_to(bundle):
        raise ValueError("The workspace must be outside the application installation")
    _check_workspace_format(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(workspace / ".initialize.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(30000):
        raise RuntimeError("Another Azeo launch is preparing this workspace. Try again after it finishes.")
    try:
        _check_workspace_format(workspace)
        for name in ("projects", "src/strategies"):
            source, destination = bundle / name, workspace / name
            if destination.exists() or not source.is_dir():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix=".seed-", dir=destination.parent) as temporary:
                seed = Path(temporary) / "content"
                shutil.copytree(source, seed)
                _publish_workspace_seed(seed, destination)
        for name in ("logs", "data"):
            (workspace / name).mkdir(exist_ok=True)
        marker = workspace / "workspace.json"
        if not marker.exists():
            pending = workspace / "workspace.json.tmp"
            pending.write_text(json.dumps({"format": WORKSPACE_FORMAT}, indent=2), encoding="utf-8")
            pending.replace(marker)
    finally:
        lock.unlock()
    return workspace
