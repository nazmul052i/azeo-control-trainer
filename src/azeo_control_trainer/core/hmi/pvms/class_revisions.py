"""Per-instance pins reuse the existing class files and display renderer."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import re

DIRECTORY = "_class_revisions"
MARKER = ".class-revision.json"


def standards_root(root):
    root = Path(root)
    return root if (root / MARKER).exists() else root.parent


def revision_root(root, revision=""):
    if not revision:
        return root
    if root is None or not re.fullmatch(r"[0-9a-f]{64}", str(revision)):
        raise ValueError("Invalid pinned class revision")
    candidate = Path(root) / DIRECTORY / revision
    if not (candidate / MARKER).is_file():
        raise ValueError("Pinned class revision is unavailable; restore its source files")
    return candidate


def item_revision(item):
    from .rendering.items import item_document_data
    data = item_document_data(item)
    return data.get("class_revision", "") or getattr(getattr(item, "pvm", None), "class_revision", "")


def capture_files(bundle, root):
    """Freeze supporting graphics files; published display histories stay separate."""
    prefix = root.rstrip("/") + "/"
    objects = {row["path"]: row for row in bundle["objects"]}
    files, records = [], []
    for file in bundle["files"]:
        parent_standard = str(Path(root).parent.as_posix()) + "/_standards.json"
        if file["path"] == parent_standard:
            relative = "_standards.json"
        elif file["path"].startswith(prefix):
            relative = file["path"][len(prefix):]
        else:
            continue
        parts = relative.split("/")
        if DIRECTORY in parts or "revisions" in parts or parts[-1] in {"draft.json", "history.json"}:
            continue
        content = base64.b64decode(file["content"], validate=True)
        source = objects[file["path"]]
        records.append({"path": relative, "id": str(source["id"]), "revision": source["revision"],
                        "digest": hashlib.sha256(content).hexdigest()})
        files.append({"path": relative, "content": file["content"]})
    records.sort(key=lambda row: row["path"])
    manifest = {"schema": 1, "files": records}
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    revision = hashlib.sha256(encoded).hexdigest()
    files.append({"path": MARKER, "content": base64.b64encode(encoded).decode()})
    return revision, [{**file, "path": prefix + DIRECTORY + "/" + revision + "/" + file["path"]} for file in files]


def verify_files(files):
    """A corrupt or incomplete pin must not fall back to the latest class."""
    content = {row["path"]: row["content"] for row in files}
    for path in content:
        parts = path.split("/")
        if DIRECTORY in parts:
            index = parts.index(DIRECTORY)
            prefix = "/".join(parts[:index + 2]) + "/"
            if len(parts) < index + 3 or prefix + MARKER not in content:
                raise ValueError("Pinned class manifest is missing: " + path)
    for path, encoded in content.items():
        if not path.endswith("/" + MARKER):
            continue
        root, revision, _ = path.rsplit("/", 2)
        if not root.endswith("/" + DIRECTORY):
            raise ValueError("Class revision marker is outside its storage directory")
        raw = base64.b64decode(encoded, validate=True)
        if hashlib.sha256(raw).hexdigest() != revision:
            raise ValueError("Pinned class manifest integrity check failed")
        prefix = root + "/" + revision + "/"
        manifest = json.loads(raw)
        if manifest.get("schema") != 1 or not isinstance(manifest.get("files"), list):
            raise ValueError("Unsupported pinned class manifest")
        expected = {prefix + MARKER}
        for record in manifest["files"]:
            from ...configuration.documents import safe_path
            name = prefix + safe_path(record["path"])
            if name in expected:
                raise ValueError("Duplicate pinned class file: " + name)
            expected.add(name)
            value = content.get(name)
            if value is None or hashlib.sha256(base64.b64decode(value, validate=True)).hexdigest() != record["digest"]:
                raise ValueError("Pinned class file is missing or changed: " + name)
        if {name for name in content if name.startswith(prefix)} != expected:
            raise ValueError("Pinned class folder contains unreviewed files")
