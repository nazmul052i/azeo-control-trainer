"""Assemble and verify a patched Azeo version folder from the installed base.

Setup extracts this file and runs it with the base version's private runtime
(``versions/<base>/runtime/python.exe -I``), so it uses the standard library
only and never imports the application. Two modes, both writing a JSON report
as their last argument and exiting 0 on success or 2 on any failure:

    assemble <base_dir> <new_dir> <list_file> <components> <base_manifest_sha256> <new_manifest_sha256> <report>
    verify   <base_dir> <new_dir> <list_file> <components> <base_manifest_sha256> <new_manifest_sha256> <report>

``list_file`` holds one manifest entry per line, tab separated: relative path,
SHA-256, component, and ``copy`` (unchanged, taken from the base) or
``install`` (packaged in the patch and copied by Setup itself). ``components``
is Setup's comma-separated selection; runtime and help are always present.

``assemble`` refuses unless the installed base manifest matches the hash the
patch was built against, copies every unchanged file of an installed
component into the new version folder and verifies each copy, and removes the
new folder completely on any failure so nothing half-assembled remains.
``verify`` checks every entry of an installed component in the new folder,
plus the new manifest itself, after Setup has installed the packaged files.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

ALWAYS = {"runtime", "help"}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def relative(name: str) -> Path:
    parts = name.split("/")
    if not name or "\\" in name or any(part in {"", ".", ".."} for part in parts) or name.startswith("/"):
        raise ValueError(f"unsafe manifest path: {name!r}")
    return Path(*parts)


def read_list(list_file: Path) -> list[tuple[str, str, str, str]]:
    entries = []
    for line in list_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        name, sha, component, action = line.split("\t")
        if action not in {"copy", "install"} or len(sha) != 64:
            raise ValueError(f"malformed patch list entry: {line!r}")
        relative(name)
        entries.append((name, sha, component, action))
    return entries


def selected(components: str) -> set[str]:
    return {part.strip() for part in components.split(",") if part.strip()} | ALWAYS


def check_manifest(folder: Path, expected: str, label: str) -> None:
    manifest = folder / "portable-manifest.json"
    if not manifest.is_file():
        raise RuntimeError(f"the {label} release manifest is missing")
    if digest(manifest) != expected:
        raise RuntimeError(f"the {label} files do not match the release this patch was built against")


def assemble(base: Path, new: Path, entries, components: set[str], base_sha: str) -> dict:
    if new.resolve() == base.resolve() or new.resolve().parent != base.resolve().parent:
        raise RuntimeError("the new version folder must sit beside the base version folder")
    check_manifest(base, base_sha, "installed base")
    if new.exists():
        shutil.rmtree(new)
    copied = skipped = 0
    size = 0
    try:
        for name, sha, component, action in entries:
            if action != "copy":
                continue
            if component not in components:
                skipped += 1
                continue
            source = base / relative(name)
            if not source.is_file():
                raise RuntimeError(f"the installed base is missing {name}")
            target = new / relative(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if digest(target) != sha:
                raise RuntimeError(f"the installed base file {name} does not match the release manifest")
            copied += 1
            size += target.stat().st_size
    except Exception:
        shutil.rmtree(new, ignore_errors=True)
        raise
    return {"copied": copied, "skipped": skipped, "bytes": size}


def verify(new: Path, entries, components: set[str], new_sha: str) -> dict:
    check_manifest(new, new_sha, "assembled")
    if not (new / "verification.json").is_file():
        raise RuntimeError("the assembled version has no packaged-check record")
    missing, mismatched, checked = [], [], 0
    for name, sha, component, _action in entries:
        if component not in components:
            continue
        path = new / relative(name)
        if not path.is_file():
            missing.append(name)
        elif digest(path) != sha:
            mismatched.append(name)
        checked += 1
    if missing or mismatched:
        raise RuntimeError(f"{len(missing)} missing and {len(mismatched)} mismatched file(s), "
                           f"for example {(missing + mismatched)[0]}")
    return {"checked": checked}


def main(argv: list[str]) -> int:
    if len(argv) != 8 or argv[0] not in {"assemble", "verify"}:
        print(__doc__, file=sys.stderr)
        return 2
    mode, base, new, list_file, components, base_sha, new_sha, report = argv
    report_path = Path(report)
    try:
        entries = read_list(Path(list_file))
        chosen = selected(components)
        if mode == "assemble":
            result = assemble(Path(base), Path(new), entries, chosen, base_sha)
        else:
            result = verify(Path(new), entries, chosen, new_sha)
        result.update(ok=True, mode=mode)
        code = 0
    except Exception as error:                        # noqa: BLE001 - reported, never hidden
        result = {"ok": False, "mode": mode, "error": str(error)}
        code = 2
    report_path.write_text(json.dumps(result), encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
