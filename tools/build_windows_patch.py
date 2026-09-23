"""Build a patch Setup that upgrades one installed Azeo version to the next.

    python tools/build_windows_patch.py --base build/windows-app-<stamp>/Azeo-Windows-x64 --payload build/windows-app-<new>/Azeo-Windows-x64
    python tools/build_windows_patch.py --base dist/Azeo-Setup-0.3.0-x64.manifest.json --payload ...

A patch is the difference between two qualified payload manifests. It packages
only the files that changed or were added, plus the new manifest, the packaged
check record and the SDK component, and compiles a Setup with the same
application identity, version guard and running-process refusal as the full
Setup. On the workstation it applies to the exact base version only: the base
runtime's private Python copies every unchanged file into the new version
folder and verifies each copy before anything changes, Setup installs the
packaged files, and the whole folder is verified against the complete new
manifest before shortcuts, registry and installation record switch over. The
base folder stays for controlled rollback. A change under the private runtime
means a full Setup; ``--allow-runtime`` overrides that for a deliberately large
patch.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))
from build_windows_installer import (  # noqa: E402
    COMPONENTS, PRESETS, component_for_file, sha256, verify_payload, write_inputs,
)
from azeo_control_trainer.config.distribution import version_tuple  # noqa: E402

RUNTIME_PREFIXES = ("runtime/", "AzeoPlantSimulator/azeoplant/azeocore", "AzeoPlantSimulator/azeoplant/_azeocore")


@dataclass(frozen=True)
class PatchPlan:
    base_version: str
    new_version: str
    changed: tuple[str, ...]
    unchanged: tuple[str, ...]
    removed: tuple[str, ...]
    runtime_changes: tuple[str, ...]


def plan_patch(base_manifest: dict, new_manifest: dict, *, allow_runtime: bool = False) -> PatchPlan:
    base_version, new_version = base_manifest["version"], new_manifest["version"]
    if version_tuple(new_version) <= version_tuple(base_version):
        raise ValueError(f"Patch version {new_version} must be newer than its base {base_version}")
    if base_manifest.get("platform") != new_manifest.get("platform"):
        raise ValueError("Base and patch payloads target different platforms")
    base, new = base_manifest["files"], new_manifest["files"]
    changed = tuple(sorted(name for name, digest in new.items() if base.get(name) != digest))
    unchanged = tuple(sorted(name for name, digest in new.items() if base.get(name) == digest))
    removed = tuple(sorted(name for name in base if name not in new))
    runtime_changes = tuple(name for name in changed if name.startswith(RUNTIME_PREFIXES))
    if runtime_changes and not allow_runtime:
        raise ValueError(f"{len(runtime_changes)} private runtime file(s) changed, for example "
                         f"{runtime_changes[0]}: ship the full Setup, or pass --allow-runtime for a large patch")
    if not changed and not removed:
        raise ValueError("The payloads are identical; there is nothing to patch")
    return PatchPlan(base_version, new_version, changed, unchanged, removed, runtime_changes)


def write_file_list(plan: PatchPlan, new_manifest: dict, path: Path) -> None:
    """One line per manifest entry: path, SHA-256, component, copy|install."""
    changed = set(plan.changed)
    lines = [f"{name}\t{digest}\t{component_for_file(name)}\t{'install' if name in changed else 'copy'}"
             for name, digest in sorted(new_manifest["files"].items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_base(base: Path) -> tuple[dict, str]:
    """The base manifest and the hash of its bytes as installed under versions/<base>."""
    manifest_file = base / "portable-manifest.json" if base.is_dir() else base
    if not manifest_file.is_file():
        raise SystemExit(f"No base manifest at {manifest_file}")
    return json.loads(manifest_file.read_text(encoding="utf-8")), sha256(manifest_file)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--base", required=True, type=Path,
                        help="the base payload directory, or its saved portable manifest")
    parser.add_argument("--payload", required=True, type=Path, help="the new qualified payload directory")
    parser.add_argument("--iscc", type=Path, default=ROOT / "build/toolchain/inno/ISCC.exe")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--test-identity", action="store_true",
                        help="Isolated installer identity/registry for maintenance qualification only")
    parser.add_argument("--allow-runtime", action="store_true",
                        help="package private runtime changes instead of requiring the full Setup")
    args = parser.parse_args()
    payload = args.payload.resolve()
    new_manifest = verify_payload(payload)
    base_manifest, base_sha = load_base(args.base.resolve())
    plan = plan_patch(base_manifest, new_manifest, allow_runtime=args.allow_runtime)
    work = ROOT / "build" / (f"patch-{plan.new_version}-from-{plan.base_version}"
                             + ("-test" if args.test_identity else ""))
    work.mkdir(parents=True, exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    list_file = work / "patch-files.txt"
    write_file_list(plan, new_manifest, list_file)
    tool_file = work / "_azeo_patch.py"
    shutil.copy2(ROOT / "tools/windows/patch_assemble.py", tool_file)
    patch = {"base_version": plan.base_version, "base_manifest_sha256": base_sha,
             "new_manifest_sha256": sha256(payload / "portable-manifest.json"),
             "packaged": list(plan.changed), "list_file": list_file, "tool_file": tool_file}
    write_inputs(payload, new_manifest, work, plan.new_version, args.output.resolve(),
                 test_identity=args.test_identity, patch=patch)
    subprocess.run([str(args.iscc.resolve()), "/Qp", str(work / "azeo.iss")], check=True)
    installer = args.output / f"Azeo-Patch-{plan.new_version}-from-{plan.base_version}-x64.exe"
    installer.with_suffix(".sha256").write_text(sha256(installer) + "  " + installer.name + "\n")
    packaged_bytes = sum((payload / name).stat().st_size for name in plan.changed)
    probe = next((name for name in plan.unchanged if not name.startswith("runtime/")
                  and component_for_file(name) in {"runtime", "help"}), None)
    record = {"version": plan.new_version, "base_version": plan.base_version, "patch": True,
              "probe_file": probe,
              "base_manifest_sha256": base_sha, "installer_sha256": sha256(installer),
              "payload_manifest_sha256": patch["new_manifest_sha256"],
              "packaged_files": len(plan.changed), "packaged_bytes": packaged_bytes,
              "copied_files": len(plan.unchanged), "removed_files": len(plan.removed),
              "runtime_changes": len(plan.runtime_changes),
              "source_commit": new_manifest["source_commit"], "source_dirty": new_manifest["source_dirty"],
              "production": False, "test_identity": args.test_identity, "signing_requested": False,
              "sdk_sha256": sha256(ROOT / "dist/azeocore-sdk-windows-x64.zip"),
              "components": COMPONENTS, "presets": PRESETS}
    installer.with_suffix(".json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"{installer} ({packaged_bytes / 1e6:.1f} MB of changed files, {len(plan.unchanged)} copied from "
          f"{plan.base_version}, {len(plan.removed)} removed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
