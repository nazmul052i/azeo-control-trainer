"""Build and qualify a complete Windows x64 app with a private Python runtime."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import sysconfig
import tomllib
from tempfile import TemporaryDirectory
import zipfile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from build_plant_core import build_environment

ROOT = Path(__file__).resolve().parents[1]
NAME = "Azeo-Windows-x64"
LAUNCHERS = ("Azeo", "AzeoSimulator", "AzeoControlDesigner", "AzeoGraphicsDesigner",
             "AzeoOperatorStation", "AzeoSimulationWorkbench", "AzeoPADesigner", "AzeoHelp")
LAUNCHER_ICONS = {
    "Azeo": ("explorer", "Azeo Explorer"),
    "AzeoSimulator": ("simulator", "Azeo Plant Simulator"),
    "AzeoControlDesigner": ("control_designer", "Azeo Control Designer"),
    "AzeoGraphicsDesigner": ("graphics_designer", "Azeo Graphics Designer"),
    "AzeoOperatorStation": ("operator_station", "Azeo Operator Station"),
    "AzeoSimulationWorkbench": ("simulation_workbench", "Azeo Simulation Workbench"),
    "AzeoPADesigner": ("pa_designer", "Azeo PA Designer"),
    "AzeoHelp": ("help", "Azeo Help Center"),
}
REQUIREMENTS = ("anyio>=4.14.2,<5", "numpy", "PySide6-Essentials", "pyqtgraph", "PyYAML", "pydantic",
                "defusedxml", "scipy", "psutil",
                "asyncua", "pymodbus", "psycopg[binary,pool]", "fastapi", "uvicorn")
PROHIBITED_QT_ARTIFACT_PREFIXES = (
    "QtGraphs", "Qt6Graphs", "QtHttpServer", "Qt6HttpServer",
    "QtNetworkAuth", "Qt6NetworkAuth", "QtQuick3D", "Qt6Quick3D",
    "QtVirtualKeyboard", "Qt6VirtualKeyboard", "QtWebEngine", "Qt6WebEngine",
)
GENERATED_HELP_FILES = (
    "output/pdf/Azeo_Control_Trainer_User_Manual.pdf",
    "output/pdf/Azeo_Control_Trainer_User_Manual.pdf.manifest.json",
    *(f"output/pdf/Azeo_{product}_User_Manual.pdf{suffix}"
      for product in ("Explorer", "Control_Designer", "Graphics_Designer",
                      "Operator_Station", "Simulation_Workbench", "PA_Designer")
      for suffix in ("", ".manifest.json")),
)
SOURCE_ROOTS = (
    "README.md", "NOTICE", "legal",
    "src/azeo_control_trainer", "src/strategies", "AzeoPlantSimulator/azeoplant",
    "AzeoPlantSimulator/data", "AzeoPlantSimulator/snapshots", "projects/_registry.json",
    "projects/AzeoPlantVirtualController", "run.py", "run_simulator.py",
    "tests/_smoke_simulator_ui.py", "tests/_smoke_virtual_controller_boot.py",
    "docs/PVM_FACEPLATE_TUTORIAL.md", "docs/images/pvm_faceplate_tutorial",
    "docs/CONTROL_MODULE_CLASS_TUTORIAL.md",
    "docs/images/control_module_class_tutorial",
    "docs/PA_DESIGNER.md",
    "docs/PROCEDURE_ADVANCED_WORKFLOWS.md",
    "docs/INSTALLATION_GUIDE.md", "docs/USER_MANUAL.md", "docs/images/user_manual",
    "docs/EXPLORER_HELP.md", "docs/CONTROL_DESIGNER_HELP.md",
    "docs/GRAPHICS_DESIGNER_HELP.md", "docs/OPERATOR_STATION_HELP.md",
    "docs/SIMULATION_WORKBENCH_HELP.md", "docs/PA_DESIGNER_HELP.md",
    *GENERATED_HELP_FILES,
    "docs/SIMULATION_WORKBENCH_GUIDE.md", "docs/HISTORIAN_WORKSPACE.md",
    "docs/OPERATOR_THEMES.md", "docs/images/operator_themes",
    "docs/CONFIGURATION_DATABASE.md", "docs/NATIVE_PLANT_CORE.md", "docs/RELEASE_NOTES.md",
)


def copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def repository_source_paths() -> tuple[Path, ...]:
    """Return current source files, excluding tracked paths deleted in this tree."""
    # Use repository inputs, including the current implementation under review,
    # without collecting ignored logs, credentials, recoveries or user caches.
    raw = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others",
                                   "--exclude-standard", "--", *SOURCE_ROOTS], cwd=ROOT)
    paths = tuple(Path(relative) for relative in
                  sorted(set(raw.decode("utf-8").split("\0")) - {""})
                  if (ROOT / relative).is_file())
    return paths


def prepare_generated_help() -> None:
    """A clean checkout has no ignored PDFs, so build them before payload staging."""
    pdfs = [str(ROOT / relative) for relative in GENERATED_HELP_FILES
            if relative.endswith(".pdf")]
    verifier = [sys.executable, str(ROOT / "tools/document_release.py"), *pdfs]
    checked = subprocess.run(verifier, cwd=ROOT, capture_output=True, text=True,
                             check=False)
    if checked.returncode:
        print("Generating missing or stale release user manuals", flush=True)
        for script in ("build_user_manual.py", "build_product_manuals.py"):
            subprocess.run([sys.executable, str(ROOT / "tools" / script)], cwd=ROOT,
                           check=True)
        subprocess.run(verifier, cwd=ROOT, check=True)


def copy_sources(stage: Path) -> None:
    for path in repository_source_paths():
        if path.name in {".recovery.json", "_accepted.json", "_download.log"} or "versions" in path.parts:
            continue
        if path.suffix in {".pyc", ".pyo"} or "__pycache__" in path.parts:
            continue
        if any(path.parts[index:index + 2] == ("procedures", "runtime")
               for index in range(len(path.parts) - 1)):
            continue
        if any(part.startswith(".pending-") for part in path.parts):
            continue
        copy_file(ROOT / path, stage / path)
    # Generated release help is intentionally ignored as a build artifact and
    # may also replace an index-staged deletion in a candidate tree. Copy it
    # explicitly so packaging cannot silently omit a freshly verified manual.
    for relative in GENERATED_HELP_FILES:
        source = ROOT / relative
        if not source.is_file():
            raise RuntimeError(f"Missing generated help artifact: {source}")
        copy_file(source, stage / relative)
    bridge = "_azeocore" + sysconfig.get_config_var("EXT_SUFFIX")
    for name in (bridge, "azeocore.dll"):
        source = ROOT / "AzeoPlantSimulator/azeoplant" / name
        if not source.is_file():
            raise RuntimeError(f"Missing {source}; run tools/build_plant_core.py --sdk first")
        copy_file(source, stage / "AzeoPlantSimulator/azeoplant" / name)
    copy_file(ROOT / "tools/windows/launch.py", stage / "_azeo_launch.py")
    copy_file(ROOT / "tools/windows/verify_runtime.py", stage / "_verify_runtime.py")
    copy_file(ROOT / "README.md", stage / "README.md")
    # Windows treats LICENSE and the per-seat license/ directory as the same
    # name. Keep both contracts by giving the MIT grant an unambiguous payload
    # filename while the source repository retains its conventional name.
    copy_file(ROOT / "LICENSE", stage / "AZEO_LICENSE.txt")


def copy_python(stage: Path) -> None:
    base = Path(sys.base_prefix)
    runtime = stage / "runtime"
    runtime.mkdir()
    for name in ("python.exe", "pythonw.exe", "python3.dll",
                 f"python{sys.version_info.major}{sys.version_info.minor}.dll", "LICENSE.txt"):
        copy_file(base / name, runtime / name)
    shutil.copytree(base / "DLLs", runtime / "DLLs",
                    ignore=shutil.ignore_patterns("*.pdb", "*.lib", "__pycache__"))
    shutil.copytree(base / "Lib", runtime / "Lib", ignore=shutil.ignore_patterns(
        "site-packages", "__pycache__", "test", "tests", "idlelib", "tkinter", "ensurepip"))
    configure_python_path(stage)


def configure_python_path(stage: Path) -> None:
    runtime = stage / "runtime"
    # Python 3.13.0's explicit 'import site' in ._pth still enabled user-site
    # .pth files in our relocation test. All vendored paths are explicit here;
    # omitting site startup prevents another machine's editable packages leaking in.
    (runtime / f"python{sys.version_info.major}{sys.version_info.minor}._pth").write_text(
        "DLLs\nLib\nLib/site-packages\n..\n../src\n../AzeoPlantSimulator\n",
        encoding="utf-8")


def runtime_distributions():
    pending = [Requirement(value) for value in REQUIREMENTS]
    found, visited = {}, set()
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        key = (name, tuple(sorted(requirement.extras)))
        if key in visited:
            continue
        visited.add(key)
        distribution = metadata.distribution(name)
        if requirement.specifier and not requirement.specifier.contains(distribution.version):
            raise RuntimeError(f"Installed {name} {distribution.version} does not satisfy {requirement}")
        found[name] = distribution
        for raw in distribution.requires or ():
            dependency = Requirement(raw)
            if dependency.marker is None or any(dependency.marker.evaluate({"extra": extra})
                                                for extra in {"", *requirement.extras}):
                pending.append(dependency)
    return found


def prohibited_qt_path(path: Path) -> bool:
    """Optional Addons/WebEngine declarations must not enter the payload."""

    return (bool(path.parts) and path.parts[0].casefold() == "pyside6"
            and any(part.casefold().startswith(tuple(
                prefix.casefold() for prefix in PROHIBITED_QT_ARTIFACT_PREFIXES))
                    for part in path.parts[1:]))


def prune_prohibited_qt(stage: Path) -> tuple[str, ...]:
    """Remove optional Qt artifacts from a resumable pre-fix build stage."""

    destination = stage / "runtime/Lib/site-packages"
    root = destination / "PySide6"
    removed = []
    if root.is_dir():
        for path in root.rglob("*"):
            if path.is_file() and prohibited_qt_path(path.relative_to(destination)):
                removed.append(path.relative_to(destination).as_posix())
                path.unlink()
    return tuple(sorted(removed))


def copy_dependencies(stage: Path) -> dict:
    installed = runtime_distributions()
    unexpected_qt = {"pyside6", "pyside6-addons"} & installed.keys()
    if unexpected_qt:
        raise RuntimeError(
            "The release dependency closure includes Qt Addons: "
            + ", ".join(sorted(unexpected_qt)))
    destination = stage / "runtime/Lib/site-packages"
    for name, distribution in sorted(installed.items()):
        origin = Path(distribution.locate_file("")).resolve()
        files = distribution.files
        if files is None:
            raise RuntimeError(f"{name} has no installed file inventory; install its wheel")
        for record in files:
            relative = Path(str(record))
            if ".." in relative.parts or "__pycache__" in relative.parts or relative.suffix == ".pyc":
                continue
            if prohibited_qt_path(relative):
                continue
            source = Path(distribution.locate_file(record)).resolve()
            if source.is_file() and source.is_relative_to(origin):
                copy_file(source, destination / relative)
        print(f"Bundled {name} {distribution.version}", flush=True)
    qt_root = destination / "PySide6"
    prohibited = sorted(
        path.relative_to(destination).as_posix()
        for path in qt_root.rglob("*")
        if path.is_file() and prohibited_qt_path(path.relative_to(destination))
    )
    if prohibited:
        raise RuntimeError(
            "The release includes Qt Addons/GPL-only or WebEngine artifacts: "
            + ", ".join(prohibited[:10]))
    notices = [
        "# Bundled dependency inventory",
        "",
        "This release selects LGPL-3.0-only for PySide6 Essentials and Shiboken6.",
        "PySide6 Addons and Qt WebEngine are not included.",
        "Replacement, debugging and corresponding-source information is in",
        "`legal/OPEN_SOURCE_COMPLIANCE.md`; GNU licence texts are in `legal/`.",
        "",
        "Exact wheel versions, declared licences and installed licence/notice files follow.",
        "The listed files remain in the runtime at the paths shown below.",
        "",
    ]
    for name, dist in sorted(installed.items()):
        notices.extend((f"## {name} {dist.version}", ""))
        declared = dist.metadata.get("License-Expression") or dist.metadata.get("License")
        notices.append(f"Declared licence: {declared or 'see retained package metadata'}")
        notices.append("")
        retained = [record for record in (dist.files or ())
                    if any(word in str(record).casefold()
                           for word in ("license", "copying", "notice"))]
        notices.extend(f"- `{record}`" for record in retained)
        if not retained:
            notices.append("- No separately named licence file in the wheel; see package metadata.")
        notices.append("")
    (stage / "THIRD_PARTY_NOTICES.md").write_text("\n".join(notices), encoding="utf-8")
    return {name: dist.version for name, dist in sorted(installed.items())}


def copy_crt(stage: Path) -> None:
    sdk = ROOT / "dist/azeocore-sdk-windows-x64.zip"
    with zipfile.ZipFile(sdk) as archive:
        prefix = "azeocore-sdk-windows-x64/bin/"
        dll = archive.read(prefix + "azeocore.dll")
        if dll != (stage / "AzeoPlantSimulator/azeoplant/azeocore.dll").read_bytes():
            raise RuntimeError("SDK and application DLL differ; rebuild with tools/build_plant_core.py --sdk")
        runtimes = {Path(name).name.lower(): archive.read(name) for name in archive.namelist()
                    if name.startswith(prefix) and name.endswith(".dll")
                    and Path(name).name.lower() != "azeocore.dll"}
    # Qt or another wheel may carry an older copy of the same CRT filename.
    # Normalizing every copy avoids load-order-dependent failures on clean PCs.
    for path in (stage / "runtime").rglob("*.dll"):
        if path.name.lower() in runtimes:
            path.write_bytes(runtimes[path.name.lower()])
    for directory in (stage / "runtime", stage / "AzeoPlantSimulator/azeoplant"):
        for name, content in runtimes.items():
            (directory / name).write_bytes(content)


def build_launchers(stage: Path, work: Path) -> None:
    env = build_environment()
    compiler = shutil.which("cl", path=env["PATH"])
    if compiler is None:
        raise RuntimeError("MSVC x64 compiler was not found")
    resource_compiler = shutil.which("rc", path=env["PATH"])
    if resource_compiler is None:
        raise RuntimeError("Windows SDK resource compiler was not found")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    numeric_version = version.replace(".", ",") + ",0"
    source = ROOT / "tools/windows/launcher.cpp"
    launcher_object = work / "launcher.obj"
    subprocess.run([
        compiler, "/nologo", "/std:c++20", "/EHsc", "/O2", "/MT", "/utf-8",
        "/c", str(source), f"/Fo:{launcher_object}",
    ], cwd=work, env=env, check=True)
    for name, (icon_key, description) in LAUNCHER_ICONS.items():
        icon_path = stage / f"{name}.ico"
        subprocess.run([
            sys.executable,
            str(ROOT / "tools/windows/build_icon.py"),
            str(icon_path),
            icon_key,
        ], check=True)
        resource = work / f"{name}.rc"
        resource.write_text(f'''#include <windows.h>
1 ICON "{icon_path.as_posix()}"
1 VERSIONINFO
FILEVERSION {numeric_version}
PRODUCTVERSION {numeric_version}
FILEOS VOS_NT_WINDOWS32
FILETYPE VFT_APP
BEGIN
  BLOCK "StringFileInfo"
  BEGIN
    BLOCK "040904b0"
    BEGIN
      VALUE "CompanyName", "Azeo"
      VALUE "FileDescription", "{description}"
      VALUE "FileVersion", "{version}"
      VALUE "ProductName", "Azeo"
      VALUE "ProductVersion", "{version}"
    END
  END
  BLOCK "VarFileInfo"
  BEGIN
    VALUE "Translation", 0x409, 1200
  END
END
''', encoding="utf-8")
        compiled_resource = work / f"{name}.res"
        subprocess.run([
            resource_compiler, "/nologo", f"/fo{compiled_resource}", str(resource),
        ], env=env, check=True)
        subprocess.run([
            compiler, "/nologo", str(launcher_object), str(compiled_resource),
            f"/Fe:{stage / f'{name}.exe'}", "/link", "/SUBSYSTEM:WINDOWS", "user32.lib",
        ], cwd=work, env=env, check=True)


def qualify(stage: Path, evidence: Path) -> list[dict]:
    evidence.mkdir(parents=True, exist_ok=True)
    reports = []
    with TemporaryDirectory(prefix="azeo verify ", dir=ROOT.parent) as temporary:
        moved = Path(temporary) / "Azeo Ω"
        def relocate(source, destination):
            # Runtime files are immutable during verification. A hardlink is a
            # self-contained file entry, unlike a symlink back into the build.
            # Mutable project/source files always get independent copies.
            if Path(source).is_relative_to(stage / "runtime"):
                try:
                    os.link(source, destination)
                    return destination
                except OSError:
                    pass
            return shutil.copy2(source, destination)
        shutil.copytree(stage, moved, copy_function=relocate)
        env = {key: value for key, value in os.environ.items() if key.upper() in {
            "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "LOCALAPPDATA", "APPDATA", "USERPROFILE",
            "USERNAME", "USERDOMAIN", "COMPUTERNAME"}}
        windows = Path(os.environ.get("SystemRoot", "C:/Windows"))
        env.update(PATH=os.pathsep.join((str(windows / "System32"), str(windows))),
                   PYTHONHOME="Z:/nonexistent-python", PYTHONPATH="Z:/nonexistent-packages",
                   PYTHONDONTWRITEBYTECODE="1", AZEO_NATIVE="1", QT_QPA_PLATFORM="windows")
        for mode in ("environment", "help", "explorer", "graphics", "control", "workbench", "procedures", "simulator", "station"):
            output = evidence / f"{mode}.json"
            output.unlink(missing_ok=True)
            executable = moved / {"simulator": "AzeoSimulator.exe", "procedures": "AzeoPADesigner.exe"}.get(mode, "Azeo.exe")
            process = subprocess.Popen([str(executable), "--verify-package", mode, str(output)],
                                       cwd=temporary, env=env)
            try:
                code = process.wait(timeout=120)
            except subprocess.TimeoutExpired:
                # Only the process tree this check started may be terminated.
                subprocess.run([str(windows / "System32/taskkill.exe"), "/PID", str(process.pid),
                                "/T", "/F"], capture_output=True, env=env, check=False)
                process.wait(timeout=10)
                raise RuntimeError(f"Packaged {mode} timed out") from None
            finally:
                if (moved / "logs").is_dir():
                    shutil.copytree(moved / "logs", evidence / "runtime-logs", dirs_exist_ok=True)
            if not output.is_file():
                raise RuntimeError(f"Packaged {mode} exited {code} without a report; see {evidence}")
            report = json.loads(output.read_text(encoding="utf-8"))
            reports.append(report)
            print(f"Packaged {mode}: {'PASS' if report['ok'] and not code else 'FAIL'}", flush=True)
            if not report["ok"] or code:
                raise RuntimeError(report.get("error", f"Native exit code: {code}"))
        shutil.copytree(moved / "logs", evidence / "runtime-logs", dirs_exist_ok=True)
    return reports


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", type=Path, help="Reuse a previous build's unchanged private runtime")
    parser.add_argument("--license", type=Path,
                        help="Signed evaluation licence (tools/license_tool.py issue) to ship as license/azeo.lic")
    args = parser.parse_args()
    if os.name != "nt" or struct.calcsize("P") != 8:
        parser.error("Use the repository's Windows x64 venv to build this package")
    work = (args.resume.resolve() if args.resume else
            ROOT / "build" / ("windows-app-" + datetime.now().strftime("%Y%m%d-%H%M%S")))
    if args.resume and (work.parent != (ROOT / "build").resolve() or
                        not work.name.startswith("windows-app-") or not work.is_dir()):
        parser.error("--resume must name this repository's build/windows-app-* directory")
    stage = work / NAME
    stage.mkdir(parents=True, exist_ok=bool(args.resume))
    # A failed resumed build must not retain a prior pass beside a new manifest.
    (stage / "verification.json").unlink(missing_ok=True)
    prepare_generated_help()
    copy_sources(stage)
    licence = None
    if args.license:
        sys.path.insert(0, str(ROOT / "src"))
        from azeo_control_trainer.config.licensing import LicenseError, load_license
        try:
            licence = load_license(args.license)
        except LicenseError as error:
            parser.error(f"--license is not a licence this build trusts: {error}")
        if licence is None:
            parser.error(f"--license file not found: {args.license}")
        copy_file(args.license, stage / "license" / "azeo.lic")
        (stage / "license" / "state.json").unlink(missing_ok=True)
    else:
        # A resumed stage must not carry a licence from an earlier build.
        for name in ("azeo.lic", "state.json"):
            (stage / "license" / name).unlink(missing_ok=True)
    if args.resume:
        versions = {name: dist.version for name, dist in sorted(runtime_distributions().items())}
        previous = json.loads((stage / "portable-manifest.json").read_text(encoding="utf-8"))
        if versions != previous["dependencies"] or sys.version != previous["python"]:
            parser.error("The build environment changed; make a fresh package")
    else:
        copy_python(stage)
        versions = copy_dependencies(stage)
    removed_qt = prune_prohibited_qt(stage)
    if removed_qt:
        print(f"Removed {len(removed_qt)} prohibited optional Qt artifact(s)", flush=True)
    configure_python_path(stage)
    copy_crt(stage)
    build_launchers(stage, work)
    manifest = {"platform": "windows-x64", "python": sys.version,
                "version": tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"],
                "built_at_utc": datetime.now(timezone.utc).isoformat(),
                "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)),
                "dependencies": versions, "launchers": [f"{name}.exe" for name in LAUNCHERS],
                "license": licence.body() if licence is not None else None}
    manifest["files"] = {path.relative_to(stage).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in sorted(stage.rglob("*")) if path.is_file()
                         and path.name not in {"portable-manifest.json", "verification.json"}}
    (stage / "portable-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    evidence = ROOT / "logs/diagnostics" / work.name
    reports = qualify(stage, evidence)
    (stage / "verification.json").write_text(json.dumps(reports, indent=2), encoding="utf-8")
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    archive = shutil.make_archive(str(output / "azeo-windows-x64"), "zip", work, NAME)
    print(f"Verified Windows application: {archive}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
