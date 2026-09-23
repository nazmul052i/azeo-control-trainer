"""Build a component-selectable installer from a qualified Windows payload."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from azeo_control_trainer.config.distribution import (  # noqa: E402
    COMPONENTS, LAUNCHERS, PRESETS, resolve_components, version_tuple,
)

REQUIRED_CHECKS = {"environment", "help", "explorer", "graphics", "control",
                   "workbench", "procedures", "simulator", "station"}
USER_GUIDES = (
    ("Azeo Suite User Manual", "Azeo_Control_Trainer_User_Manual.pdf"),
    ("Azeo Explorer User Manual", "Azeo_Explorer_User_Manual.pdf"),
    ("Azeo Control Designer User Manual", "Azeo_Control_Designer_User_Manual.pdf"),
    ("Azeo Graphics Designer User Manual", "Azeo_Graphics_Designer_User_Manual.pdf"),
    ("Azeo Operator Station User Manual", "Azeo_Operator_Station_User_Manual.pdf"),
    ("Azeo Simulation Workbench User Manual", "Azeo_Simulation_Workbench_User_Manual.pdf"),
    ("Azeo PA Designer User Manual", "Azeo_PA_Designer_User_Manual.pdf"),
)


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_relative(name):
    path = PurePosixPath(name)
    if (not name or "\\" in name or re.search(r'[<>:"|?*\x00-\x1f]', name)
            or path.is_absolute() or any(part in {"", ".", ".."}
            or part.endswith((" ", "."))
            or re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", part.split(".")[0], re.I)
            for part in name.split("/"))):
        raise ValueError(f"Invalid payload path: {name!r}")
    return Path(*path.parts)


def verify_payload(payload):
    manifest = json.loads((payload / "portable-manifest.json").read_text(encoding="utf-8"))
    reports = json.loads((payload / "verification.json").read_text(encoding="utf-8"))
    if {report["mode"] for report in reports if report.get("ok")} != REQUIRED_CHECKS:
        raise ValueError("Payload must pass all nine packaged checks, including offline Help")
    seen = set()
    for name, expected in manifest["files"].items():
        relative = safe_relative(name)
        if name.casefold() in seen:
            raise ValueError(f"Duplicate case-insensitive payload path: {name}")
        seen.add(name.casefold())
        file = payload / relative
        if not file.resolve().is_relative_to(payload.resolve()) or sha256(file) != expected:
            raise ValueError(f"Payload hash mismatch: {name}; rebuild and qualify it")
    for name in ("_azeo_launch.py", "AzeoHelp.exe", "runtime/pythonw.exe",
                 "README.md", "AZEO_LICENSE.txt", "NOTICE", "legal/OPEN_SOURCE_COMPLIANCE.md",
                 "docs/INSTALLATION_GUIDE.md",
                 *(f"output/pdf/{filename}{suffix}" for _, filename in USER_GUIDES
                   for suffix in ("", ".manifest.json")),
                 *[value[0] for value in LAUNCHERS.values()]):
        if name not in manifest["files"]:
            raise ValueError(f"Required payload entry is missing: {name}")
    return manifest


def quote(value):
    value = str(value)
    if "\n" in value or "\r" in value:
        raise ValueError("Installer values cannot contain newlines")
    return '"' + value.replace('"', '""') + '"'


def component_for_file(name):
    for component, (filename, _) in LAUNCHERS.items():
        if name == filename:
            return component
    if name == "AzeoSimulator.exe":
        return "simulation_workbench"
    if (name == "AzeoHelp.exe" or name.startswith("docs/")
            or name.startswith("output/pdf/")):
        return "help"
    if name.startswith("sdk/"):
        return "sdk"
    return "runtime"


def write_inputs(payload, manifest, work, version, output, *, test_identity=False, sign_command=None,
                 patch=None):
    """Write the compiler inputs; ``patch`` (from build_windows_patch.py) packages only its
    changed files and switches over only after the assembled folder verifies."""
    definitions = [f'#define ProductVersion "{version}"',
                   f'#define OutputDirectory {quote(output)}',
                   '#define InstallId "{{EA34A0CE-8223-4B98-B5C7-56E5D32203D2}"',
                   '#define RegistryKey "Software\\Azeo\\Setup"',
                   '#define AppMutexName "Local\\AzeoSuite.Running"',
                   '#define SetupMutexName "Local\\AzeoSuite.Setup"']
    if test_identity:
        definitions[2:] = [
            '#define InstallId "{{082103AE-FCB4-4150-A4CA-42C0A126A936}"',
            '#define RegistryKey "Software\\Azeo\\SetupQualification"',
            '#define AppMutexName "Local\\AzeoSuite.SetupQualification.Running"',
            '#define SetupMutexName "Local\\AzeoSuite.SetupQualification.Setup"',
        ]
    definitions.append('#define ProgramGroup ' + quote("Azeo Setup Qualification" if test_identity else "Azeo"))
    if patch is not None:
        definitions += [f'#define PatchBase "{patch["base_version"]}"',
                        f'#define PatchBaseManifestHash "{patch["base_manifest_sha256"]}"',
                        f'#define PatchNewManifestHash "{patch["new_manifest_sha256"]}"',
                        '#define SwitchCheck "; Check: PatchVerified"']
        shutil.copy2(ROOT / "tools/windows/patch.iss", work / "patch.iss")
    else:
        definitions.append('#define SwitchCheck ""')
    if sign_command:
        definitions.append('#define SignCommand 1')
    if (payload / "Azeo.ico").is_file():
        definitions.append('#define AppIcon ' + quote(payload / "Azeo.ico"))
    template = (ROOT / "tools/windows/azeo.iss").read_text(encoding="utf-8")
    (work / "azeo.iss").write_text("\n".join(definitions) + "\n" + template, encoding="utf-8-sig")
    components = ["[Components]"]
    for component, description in COMPONENTS.items():
        types = [name for name, selected in PRESETS.items() if component in resolve_components(selected)]
        flags = "fixed" if component in {"runtime", "help"} else "disablenouninstallwarning"
        components.append(f'Name: {quote(component)}; Description: {quote(description)}; '
                          f'Types: {" ".join(types)}; Flags: {flags}')
    (work / "components.iss").write_text("\n".join(components), encoding="utf-8-sig")
    files = ["[Files]"]
    packaged = manifest["files"] if patch is None else patch["packaged"]
    sources = {name: payload / safe_relative(name) for name in packaged}
    sources.update({name: payload / name for name in ("portable-manifest.json", "verification.json")})
    sdk = work / "sdk"
    sdk.mkdir(exist_ok=True)
    with zipfile.ZipFile(ROOT / "dist/azeocore-sdk-windows-x64.zip") as archive:
        for item in archive.infolist():
            if item.is_dir():
                continue
            source = safe_relative(item.filename)
            if source.parts[0] != "azeocore-sdk-windows-x64":
                raise ValueError("Unexpected SDK archive root")
            relative = Path(*source.parts[1:])
            destination = sdk / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as src, destination.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            sources["sdk/" + relative.as_posix()] = destination
    if sha256(sdk / "bin/azeocore.dll") != sha256(payload / "AzeoPlantSimulator/azeoplant/azeocore.dll"):
        raise ValueError("The SDK DLL must match the application DLL")
    # Integration users get a discoverable copy of the exact Python bridge and
    # its app-local dependencies, without coupling a C++ host to Python itself.
    for path in (payload / "AzeoPlantSimulator/azeoplant").iterdir():
        if path.is_file() and (path.suffix == ".dll" or path.name.startswith("_azeocore.")
                               and path.suffix == ".pyd"):
            sources["sdk/python/" + path.name] = path
    for name, source in sorted(sources.items()):
        destination = "{app}\\versions\\" + version
        parent = safe_relative(name).parent
        if str(parent) != ".":
            destination += "\\" + str(parent)
        files.append(f'Source: {quote(source)}; DestDir: {quote(destination)}; '
                     f'Components: {component_for_file(name)}; Flags: ignoreversion')
    if patch is not None:
        # Extracted on demand by patch.iss; never installed.
        for tool in (patch["list_file"], patch["tool_file"]):
            files.append(f'Source: {quote(tool)}; DestDir: "{{tmp}}"; Flags: dontcopy')
    (work / "files.iss").write_text("\n".join(files), encoding="utf-8-sig")
    shortcuts = ["[Icons]"]
    launchers = {**LAUNCHERS, "help": ("AzeoHelp.exe", "")}
    for component, (filename, _) in launchers.items():
        title = "Azeo Help Center" if component == "help" else COMPONENTS[component]
        target = "{app}\\versions\\" + version + "\\" + filename
        for location, task in (("{group}", ""), ("{autodesktop}", "; Tasks: desktopicon")):
            shortcuts.append(f'Name: {quote(location + chr(92) + title)}; Filename: {quote(target)}; '
                             f'Components: {component}{task}{{#SwitchCheck}}')
    shortcuts.append(f'Name: "{{group}}\\Azeo Plant Simulator"; Filename: "{{app}}\\versions\\{version}\\AzeoSimulator.exe"; Components: simulation_workbench{{#SwitchCheck}}')
    for title, filename in USER_GUIDES:
        location = "{group}\\User Guide\\" + title
        target = "{app}\\versions\\" + version + "\\output\\pdf\\" + filename
        shortcuts.append(
            f'Name: {quote(location)}; Filename: {quote(target)}; '
            'Components: help{#SwitchCheck}')
    # Remove only known installer-owned launchers/shortcuts when deselected;
    # never use recursive wildcard cleanup of an application or user directory.
    shortcuts.append("\n[InstallDelete]")
    # 0.4 renamed the engineering applications. Remove only obsolete
    # installer-owned shortcut names and same-version legacy launchers; older
    # version folders remain intact for controlled rollback.
    legacy_launchers = (
        ("AzeoControlStudio.exe", "Azeo Control Studio.lnk"),
        ("AzeoGraphicsStudio.exe", "Azeo Graphics Studio.lnk"),
        ("AzeoProcedurePilot.exe", "Azeo Procedure Pilot.lnk"),
    )
    for filename, shortcut in legacy_launchers:
        shortcuts.append(
            f'Type: files; Name: "{{app}}\\versions\\{version}\\{filename}"')
        for location in ("{group}", "{autodesktop}"):
            shortcuts.append(
                f'Type: files; Name: {quote(location + chr(92) + shortcut)}')
    for name in sources:
        if name.startswith("sdk/"):
            target = "{app}\\versions\\" + version + "\\" + str(safe_relative(name))
            shortcuts.append(f'Type: files; Name: {quote(target)}; Check: not WizardIsComponentSelected(\'sdk\')')
    for component, (filename, _) in {**LAUNCHERS, "plant_launcher": ("AzeoSimulator.exe", "")}.items():
        selection = "simulation_workbench" if component == "plant_launcher" else component
        title = "Azeo Plant Simulator" if component == "plant_launcher" else COMPONENTS[component]
        shortcuts.append(f'Type: files; Name: "{{app}}\\versions\\{version}\\{filename}"; Check: not WizardIsComponentSelected({quote_pascal(selection)})')
        for location in ("{group}", "{autodesktop}"):
            shortcuts.append(f'Type: files; Name: {quote(location + chr(92) + title + ".lnk")}; Check: not WizardIsComponentSelected({quote_pascal(selection)})')
    (work / "shortcuts.iss").write_text("\n".join(shortcuts), encoding="utf-8-sig")


def quote_pascal(value):
    return "'" + value.replace("'", "''") + "'"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--version", default=tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])
    parser.add_argument("--iscc", type=Path, default=ROOT / "build/toolchain/inno/ISCC.exe")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--test-identity", action="store_true",
                        help="Isolated installer identity/registry for maintenance qualification only")
    parser.add_argument("--production", action="store_true")
    parser.add_argument("--sign-command", help="Trusted signing-tool command, including Inno's $f placeholder")
    parser.add_argument("--publisher-thumbprint", help="Expected Authenticode publisher certificate SHA-1 thumbprint")
    parser.add_argument("--qualification", type=Path,
                        help="Reviewed clean-machine evidence bound to this payload manifest hash")
    args = parser.parse_args()
    version_tuple(args.version)
    payload = args.payload.resolve()
    manifest = verify_payload(payload)
    if not args.test_identity and manifest.get("version") != args.version:
        parser.error("Installer version must match the qualified application payload")
    if args.production:
        if (args.test_identity or not args.sign_command or not args.publisher_thumbprint
                or not args.qualification or manifest["source_dirty"]):
            parser.error("Production needs a clean source payload, publisher signing and reviewed qualification evidence")
        evidence = json.loads(args.qualification.read_text(encoding="utf-8"))
        if (evidence.get("payload_sha256") != sha256(payload / "portable-manifest.json")
                or not all(evidence.get(key) is True for key in (
                    "clean_machine", "install_modify_repair_update_uninstall", "redistribution_review"))):
            parser.error("Qualification must cover this payload and all commercial release gates")
    work = ROOT / "build" / ("installer-" + args.version + ("-test" if args.test_identity else ""))
    work.mkdir(parents=True, exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    write_inputs(payload, manifest, work, args.version, args.output.resolve(),
                 test_identity=args.test_identity, sign_command=args.sign_command)
    command = [str(args.iscc.resolve()), "/Qp"]
    if args.sign_command:
        if "$f" not in args.sign_command:
            parser.error("Signing command must include the $f filename placeholder")
        command.append("/SAzeoRelease=" + args.sign_command)
    subprocess.run([*command, str(work / "azeo.iss")], check=True)
    installer = args.output / f"Azeo-Setup-{args.version}-x64.exe"
    if args.production:
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-File",
                        str(ROOT / "tools/windows/verify_signature.ps1"), "-File", str(installer),
                        "-PublisherThumbprint", args.publisher_thumbprint], check=True)
    (installer.with_suffix(".sha256")).write_text(sha256(installer) + "  " + installer.name + "\n")
    # The manifest is what a later patch Setup is built against; keep it beside the installer.
    shutil.copy2(payload / "portable-manifest.json", installer.with_name(installer.stem + ".manifest.json"))
    record = {"version": args.version, "installer_sha256": sha256(installer),
              "payload_manifest_sha256": sha256(payload / "portable-manifest.json"),
              "source_commit": manifest["source_commit"], "source_dirty": manifest["source_dirty"],
              "production": args.production, "test_identity": args.test_identity,
              "signing_requested": bool(args.sign_command),
              "publisher_thumbprint": args.publisher_thumbprint,
              "sdk_sha256": sha256(ROOT / "dist/azeocore-sdk-windows-x64.zip"),
              "components": COMPONENTS, "presets": PRESETS}
    installer.with_suffix(".json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(installer)


if __name__ == "__main__":
    main()
