"""Patch Setups: the manifest diff, the assembly tool and the compiler inputs."""
import importlib.util
import json
from pathlib import Path
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = load("installer_builder", ROOT / "tools/build_windows_installer.py")
patcher = load("patch_builder", ROOT / "tools/build_windows_patch.py")
assembler = load("patch_assemble", ROOT / "tools/windows/patch_assemble.py")


def manifest(version, files, **extra):
    return {"version": version, "platform": "windows-x64", "python": "3.13", "files": files, **extra}


def test_plan_classifies_changed_unchanged_and_removed_files():
    base = manifest("0.3.0", {"src/a.py": "1" * 64, "src/b.py": "2" * 64, "docs/old.md": "3" * 64})
    new = manifest("0.3.1", {"src/a.py": "1" * 64, "src/b.py": "9" * 64, "docs/new.md": "4" * 64})
    plan = patcher.plan_patch(base, new)
    assert plan.changed == ("docs/new.md", "src/b.py")
    assert plan.unchanged == ("src/a.py",)
    assert plan.removed == ("docs/old.md",)
    assert plan.runtime_changes == ()


def test_plan_refuses_runtime_changes_older_versions_and_identical_payloads():
    base = manifest("0.3.0", {"runtime/python313.dll": "1" * 64, "src/a.py": "2" * 64})
    with pytest.raises(ValueError, match="full Setup"):
        patcher.plan_patch(base, manifest("0.3.1", {"runtime/python313.dll": "5" * 64, "src/a.py": "2" * 64}))
    large = patcher.plan_patch(base, manifest("0.3.1", {"runtime/python313.dll": "5" * 64, "src/a.py": "2" * 64}),
                               allow_runtime=True)
    assert large.runtime_changes == ("runtime/python313.dll",)
    with pytest.raises(ValueError, match="newer than its base"):
        patcher.plan_patch(base, manifest("0.3.0", {"src/a.py": "3" * 64}))
    with pytest.raises(ValueError, match="nothing to patch"):
        patcher.plan_patch(base, manifest("0.3.1", base["files"]))


def digest(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def make_base(root, files):
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    body = {name: digest(data) for name, data in files.items()}
    (root / "portable-manifest.json").write_text(json.dumps({"version": "0.3.0", "files": body}), encoding="utf-8")
    return body


def test_assembly_copies_installed_components_only_and_verifies_every_copy(tmp_path):
    base = tmp_path / "versions" / "0.3.0"
    new = tmp_path / "versions" / "0.3.1"
    files = {"runtime/python.exe": b"py", "docs/USER_MANUAL.md": b"manual",
             "AzeoOperatorStation.exe": b"station", "AzeoControlDesigner.exe": b"studio", "src/app.py": b"old"}
    hashes = make_base(base, files)
    base_sha = assembler.digest(base / "portable-manifest.json")
    plan = patcher.plan_patch({"version": "0.3.0", "platform": "windows-x64", "files": hashes},
                              {"version": "0.3.1", "platform": "windows-x64",
                               "files": {**hashes, "src/app.py": digest(b"new")}})
    list_file = tmp_path / "patch-files.txt"
    patcher.write_file_list(plan, {"files": {**hashes, "src/app.py": digest(b"new")}}, list_file)
    entries = assembler.read_list(list_file)
    assert {action for *_, action in entries} == {"copy", "install"}
    report = assembler.assemble(base, new, entries, assembler.selected("operator_station"), base_sha)
    assert report["copied"] == 3 and report["skipped"] == 1          # studio not installed
    assert (new / "AzeoOperatorStation.exe").read_bytes() == b"station"
    assert not (new / "AzeoControlDesigner.exe").exists()
    assert not (new / "src/app.py").exists()                          # packaged, installed by Setup
    # A base file that no longer matches the manifest refuses and leaves nothing behind.
    (base / "docs/USER_MANUAL.md").write_bytes(b"edited")
    with pytest.raises(RuntimeError, match="does not match"):
        assembler.assemble(base, new, entries, assembler.selected("operator_station"), base_sha)
    assert not new.exists()
    with pytest.raises(RuntimeError, match="release this patch was built against"):
        assembler.assemble(base, new, entries, assembler.selected(""), "0" * 64)


def test_verification_checks_the_assembled_folder_against_the_new_manifest(tmp_path):
    new = tmp_path / "versions" / "0.3.1"
    files = {"runtime/python.exe": b"py", "src/app.py": b"new", "AzeoOperatorStation.exe": b"station"}
    hashes = make_base(new, files)
    (new / "verification.json").write_text("[]", encoding="utf-8")
    new_sha = assembler.digest(new / "portable-manifest.json")
    list_file = tmp_path / "patch-files.txt"
    list_file.write_text("".join(f"{name}\t{sha}\t{builder.component_for_file(name)}\tcopy\n"
                                 for name, sha in hashes.items()), encoding="utf-8")
    entries = assembler.read_list(list_file)
    assert assembler.verify(new, entries, assembler.selected("operator_station"), new_sha)["checked"] == 3
    (new / "src/app.py").write_bytes(b"corrupt")
    with pytest.raises(RuntimeError, match="1 mismatched"):
        assembler.verify(new, entries, assembler.selected("operator_station"), new_sha)
    (new / "src/app.py").unlink()
    with pytest.raises(RuntimeError, match="1 missing"):
        assembler.verify(new, entries, assembler.selected("operator_station"), new_sha)


def test_patch_inputs_package_only_changed_files_and_guard_the_switch_over(tmp_path, monkeypatch):
    root = tmp_path / "source"
    for name in ("azeo.iss", "patch.iss"):
        target = root / "tools/windows" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((ROOT / "tools/windows" / name).read_text(encoding="utf-8"), encoding="utf-8")
    archive_path = root / "dist/azeocore-sdk-windows-x64.zip"
    archive_path.parent.mkdir()
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("azeocore-sdk-windows-x64/bin/azeocore.dll", b"native")
    payload = tmp_path / "payload"
    (payload / "AzeoPlantSimulator/azeoplant").mkdir(parents=True)
    (payload / "AzeoPlantSimulator/azeoplant/azeocore.dll").write_bytes(b"native")
    for name in ("src/changed.py", "src/same.py"):
        (payload / name).parent.mkdir(parents=True, exist_ok=True)
        (payload / name).write_text(name)
    work = tmp_path / "work"
    work.mkdir()
    (work / "patch-files.txt").write_text("", encoding="utf-8")
    (work / "_azeo_patch.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(builder, "ROOT", root)
    files = {"src/changed.py": "1" * 64, "src/same.py": "2" * 64}
    builder.write_inputs(payload, {"files": files}, work, "0.3.1", tmp_path / "output", test_identity=True,
                         patch={"base_version": "0.3.0", "base_manifest_sha256": "a" * 64,
                                "new_manifest_sha256": "b" * 64, "packaged": ["src/changed.py"],
                                "list_file": work / "patch-files.txt", "tool_file": work / "_azeo_patch.py"})
    script = (work / "azeo.iss").read_text(encoding="utf-8-sig")
    assert '#define PatchBase "0.3.0"' in script and '#define SwitchCheck "; Check: PatchVerified"' in script
    assert (work / "patch.iss").exists()
    entries = (work / "files.iss").read_text(encoding="utf-8-sig")
    assert "changed.py" in entries and "same.py" not in entries
    assert entries.count('DestDir: "{tmp}"; Flags: dontcopy') == 2
    shortcuts = (work / "shortcuts.iss").read_text(encoding="utf-8-sig")
    assert "{#SwitchCheck}" in shortcuts
    plain = tmp_path / "plain"
    plain.mkdir()
    builder.write_inputs(payload, {"files": files}, plain, "0.3.1", tmp_path / "output")
    assert '#define SwitchCheck ""' in (plain / "azeo.iss").read_text(encoding="utf-8-sig")
    assert "same.py" in (plain / "files.iss").read_text(encoding="utf-8-sig")
