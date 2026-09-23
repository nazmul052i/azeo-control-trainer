"""Explicit field selection uploads online tuning through the existing check-in writer."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from uuid import uuid4, uuid5, NAMESPACE_URL

from psycopg.types.json import Jsonb

from .documents import ConfigurationError, Conflict, Missing, catalog_value
from .editing import EditingRepository
from .releases import fingerprint
from .repository import identifier
from ..strategy.serialization.strategy_io import graph_from_document
from ..strategy.serialization.tuning import (
    ParameterChange, apply_parameter_changes, canonical_config_value,
    effective_configuration, parameter_category, values_differ,
)


def decode_value(value):
    if isinstance(value, dict) and set(value) == {"$number"}:
        return float(value["$number"])
    if isinstance(value, dict):
        return {k: decode_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode_value(v) for v in value]
    return value


class UploadRepository:
    def __init__(self, repository):
        self.repo = repository
        self.editing = EditingRepository(repository)

    def _observation(self, c, project, target):
        row = c.execute("SELECT *, online AND heartbeat > clock_timestamp()-interval '30 seconds' AS fresh "
                        "FROM runtime_targets WHERE id=%s AND project_id=%s", (identifier(target), project)).fetchone()
        if not row or not row["fresh"]:
            raise Conflict("A fresh controller observation is required before uploading tuning")
        return row

    def preview(self, token, project, target):
        with self.repo.connection(snapshot=True) as c:
            actor, _ = self.editing._project(c, token, project, edit=True)
            observation = self._observation(c, project, target)
        bundle = self.repo.export(token, project)
        files = {f["path"]: f for f in bundle["files"]}
        objects = {str(o["id"]): o for o in bundle["objects"]}
        rows, bases, evidence = [], {}, {}
        for loaded in observation["report"].get("objects", []):
            obj = objects.get(loaded.get("object_id"))
            if not obj or obj["kind"] != "module" or not loaded.get("active"):
                continue
            raw = json.loads(base64.b64decode(files[obj["path"]]["content"]))
            graph, _ = graph_from_document(raw, strict=True)
            for sample in loaded.get("parameters", []):
                block = graph.blocks.get(sample["block_id"])
                if block is None or block.block_type != sample["block_type"]:
                    continue
                configured = effective_configuration(block)
                for name, encoded_value in sample["values"].items():
                    value = decode_value(encoded_value)
                    if name not in configured or not values_differ(configured[name], canonical_config_value(block, name, value)):
                        continue
                    # Non-finite limits may describe defaults, but are not an
                    # online tuning value that may be written into canonical JSON.
                    try:
                        json.dumps(value, allow_nan=False)
                    except ValueError:
                        continue
                    rows.append({"id": str(uuid4()), "path": obj["path"], "object_id": str(obj["id"]),
                                 "block_id": block.id, "instance_name": block.instance_name,
                                 "block_type": block.block_type, "module": graph.name, "parameter": name,
                                 "file_value": catalog_value(configured[name]),
                                 "runtime_value": encoded_value, "category": parameter_category(name)})
                    bases[obj["path"]] = {**files[obj["path"]], "expected_revision": obj["revision"]}
                    evidence[str(obj["id"])] = fingerprint(loaded.get("parameters", []))
        document = {"rows": rows, "bases": bases, "evidence": evidence, "boot": str(observation["boot"])}
        preview = str(uuid4())
        with self.repo.connection() as c:
            actor, _ = self.editing._project(c, token, project, edit=True)
            c.execute("INSERT INTO upload_previews(id,project_id,target_id,actor,document) VALUES (%s,%s,%s,%s,%s)",
                      (preview, project, target, actor["name"], Jsonb(document)))
        return {"id": preview, "rows": rows, "observed_at": observation["heartbeat"], "boot": document["boot"]}

    def commit(self, token, project, preview, selected, reason, command):
        preview, command = identifier(preview), identifier(command)
        with self.repo.connection() as c:
            actor, _ = self.editing._project(c, token, project, edit=True)
            row = c.execute("SELECT * FROM upload_previews WHERE id=%s AND project_id=%s AND actor=%s",
                            (preview, project, actor["name"])).fetchone()
            if not row:
                raise Missing("Upload review does not exist for this engineer")
            # A lost response may be retried after disconnection. The common
            # check-in writer below still verifies the exact original command.
            committed = c.execute("SELECT id FROM changes WHERE command_id=%s", (command,)).fetchone()
            review = row["document"]
            selected = set(selected)
            known = {item["id"] for item in review["rows"]}
            if not selected or selected - known:
                raise ConfigurationError("Select fields from the reviewed online differences")
            chosen = [item for item in review["rows"] if item["id"] in selected]
            if not committed:
                target = self._observation(c, project, row["target_id"])
                current = {o.get("object_id"): o
                           for o in target["report"].get("objects", []) if o.get("active")}
                def still_matches(item):
                    observed = current.get(item["object_id"], {})
                    sample = next((row for row in observed.get("parameters", []) if row["block_id"] == item["block_id"]
                                   and row["block_type"] == item["block_type"]), {})
                    values = sample.get("values", {})
                    return item["parameter"] in values and fingerprint(values[item["parameter"]]) == fingerprint(item["runtime_value"])
                if str(target["boot"]) != review["boot"] or not all(still_matches(item) for item in chosen):
                    raise Conflict("Controller tuning changed after review; compare again")
        edits = []
        for path in sorted({item["path"] for item in chosen}):
            base = review["bases"][path]
            raw = json.loads(base64.b64decode(base["content"]))
            graph, _ = graph_from_document(raw, strict=True)
            fields = [item for item in chosen if item["path"] == path]
            changes = [ParameterChange(file_path=Path(path), **{k: decode_value(item[k]) for k in (
                "module", "block_id", "instance_name", "block_type", "parameter", "file_value", "runtime_value", "category")}) for item in fields]
            apply_parameter_changes(graph, changes)
            for field in fields:
                block = next(block for block in raw["blocks"] if block["id"] == field["block_id"])
                block.setdefault("config", {})[field["parameter"]] = graph.blocks[field["block_id"]].config.params[field["parameter"]]
            content = base64.b64encode(json.dumps(raw, ensure_ascii=False, indent=2, allow_nan=False).encode()).decode()
            edits.append({"path": path, "content": content, "expected_revision": base["expected_revision"]})
        session = str(uuid5(NAMESPACE_URL, "azeo-upload:" + command))
        if not committed:
            self.editing.lease(token, project, session, [edit["path"] for edit in edits])
        try:
            return self.editing.checkin(token, project, session, edits,
                                        f"Online tuning upload ({preview}): {reason}", command)
        finally:
            self.editing.lease(token, project, session, [], release=True)
