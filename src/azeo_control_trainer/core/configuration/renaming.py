"""Reviewable module-address renames; history and opaque scripts stay intact."""
from __future__ import annotations

import base64
import copy
import json
from pathlib import PurePosixPath
import re

from .documents import ConfigurationError, Conflict, prepare_import, safe_path

REFERENCE_KEYS = {"path", "tag", "target", "quality_path", "limit_path", "remote_path",
                  "controller_tag", "binding", "expression", "expr", "readExpr", "writeExpr",
                  "source_tag", "tag_path", "module", "module_name"}
SCRIPT_KEYS = {"script", "scripts", "SCRIPT", "code", "typescript"}


def rename_module(bundle, module, new_name):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,159}", new_name):
        raise ConfigurationError("Use a module name beginning with a letter, followed by letters, digits, _ or -")
    prepared = prepare_import(bundle["files"])
    documents = [d for d in prepared.documents if d.kind == "module" and d.payload["name"] == module]
    if len(documents) != 1:
        raise ConfigurationError("Select one existing module to rename")
    source = documents[0]
    target = safe_path(str(PurePosixPath(source.path).with_name(new_name + ".json")))
    if any(d.path.casefold() == target.casefold() or (
            d.kind == "module" and d.payload["name"].casefold() == new_name.casefold())
           for d in prepared.documents):
        raise Conflict("A module or file already uses that name")
    objects = {r["path"]: r for r in bundle["objects"]}
    edits, changed_fields, unknown = [], [], []
    address = re.compile(r"(?<![\w./-])" + re.escape(module) + r"(?=/)")
    # The module identity changes; block instance names and ctrl.<block> store
    # publications retain their identity. Renaming a block is a separate operation.
    def walk(value, location, *, key="", reference=False, script=False):
        if isinstance(value, dict):
            return {k: walk(v, f"{location}/{k}", key=k,
                            reference=reference or k in {"params", "bindings"},
                            script=script or k in SCRIPT_KEYS) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v, f"{location}/{i}", key=key, reference=reference, script=script)
                    for i, v in enumerate(value)]
        if not isinstance(value, str):
            return value
        replacement = value
        if not script and (reference or key in REFERENCE_KEYS):
            replacement = new_name if value == module else address.sub(new_name, value)
        if replacement != value:
            changed_fields.append({"location": location, "before": value, "after": replacement})
        elif module in value:
            unknown.append({"location": location, "value": value,
                            "reason": "Script or unclassified text; review manually"})
        return replacement

    for document in prepared.documents:
        if document.kind == "legacy_revision" or not isinstance(document.payload, (dict, list)):
            continue
        original = document.payload
        raw = copy.deepcopy(original)
        if document.path == source.path:
            raw["name"] = new_name
            changed_fields.append({"location": source.path + "/name", "before": module, "after": new_name})
        if document.path == "_project.json":
            for area in raw.get("areas", []):
                area["strategies"] = [target if p == source.path else p for p in area.get("strategies", [])]
        raw = walk(raw, document.path)
        if raw != original:
            edits.append({"path": document.path, "new_path": target if document.path == source.path else document.path,
                          "expected_revision": objects[document.path]["revision"],
                          "content": base64.b64encode((json.dumps(raw, indent=2, ensure_ascii=False,
                                                                  allow_nan=False) + "\n").encode()).decode()})
    return {"edits": edits, "fields": changed_fields, "unknown": unknown,
            "generation": bundle["project"]["generation"], "module": module, "new_name": new_name,
            "note": "Module address changes; block names, field tags and historical revisions retain their identities."}
