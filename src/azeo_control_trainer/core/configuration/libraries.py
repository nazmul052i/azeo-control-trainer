"""Review adoption of existing class documents without inventing a second library."""
from __future__ import annotations

from ..hmi.compatibility import (
    USER_DEFINITION_ID_SEED, configuration_documents, find_configuration_document,
    find_library_document, is_configuration_document_path, is_display_document_path,
    is_pvm_library_path, normalize_configuration_document, normalize_display_document,
    normalize_library_entries, split_configuration_document_path,
)

import base64
from copy import deepcopy
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from .documents import ConfigurationError, Forbidden
from .editing import EditingRepository, checkin_hash, normalize_edits
from .release_manifest import fingerprint, plain
from .repository import identifier
from ..hmi.pvms.class_revisions import DIRECTORY, capture_files
from ..hmi.pvms.configurator.model import PvmConfiguration
from ..hmi.pvms.user_library import UserPvmLibrary


def decode(bundle):
    result = {}
    for file in bundle["files"]:
        if file["path"].endswith(".json"):
            result[file["path"]] = _normalized(
                file["path"], json.loads(base64.b64decode(file["content"])))
    return result


def _normalized(path, document):
    """Captured documents are read under the current spelling.

    Check-in only sends documents that differ from this decoded baseline, so
    an untouched pre-rename document is never re-sent merely for its spelling.
    """
    if is_display_document_path(path):
        return normalize_display_document(document)
    if is_pvm_library_path(path):
        return normalize_library_entries(document)
    if is_configuration_document_path(path):
        return normalize_configuration_document(document)
    return document


def configuration(documents, root, name):
    return PvmConfiguration.from_dict(find_configuration_document(documents, root, name))


def retained_name(documents, root, cls):
    definition = cls["id"].split(":user:", 1)
    if len(definition) == 2:
        for name, entry in find_library_document(documents, root).items():
            identity = entry.get("definition_id") or str(uuid5(NAMESPACE_URL, USER_DEFINITION_ID_SEED + str(name)))
            if identity == definition[1]:
                return name
    return cls["name"]


def class_contract(documents, root, name):
    entries = find_library_document(documents, root)
    captured = {}

    def visit(current):
        if current in captured:
            return
        entry = entries.get(current, {})
        captured[current] = [entry, find_configuration_document(documents, root, current)]
        if entry.get("paired_faceplate"):
            visit(entry["paired_faceplate"])
        for row in entry.get("items", []):
            if row.get("kind") == "nested_pvm":
                visit(row.get("class", ""))
    visit(name)
    from ..hmi.pvms.base import registry
    installed = next((cls for cls in registry.all_classes().values() if cls.__name__ == name), None)
    if installed:
        for role in ("faceplate", "detail"):
            paired = registry.get(installed.block_type, role)
            if paired:
                visit(paired.__name__)
    standard = root + "/_standards.json" if DIRECTORY in root.split("/") else str(Path(root).parent.as_posix()) + "/_standards.json"
    return {"classes": captured, "standards": documents.get(standard, {}),
            "functions": documents.get(root + "/_functions.json", {})}


def class_stamp(documents, root, name):
    return fingerprint(class_contract(documents, root, name))


def inventory(bundle):
    documents = decode(bundle)
    classes, instances, authored_keys = {}, [], {}
    for path, doc in documents.items():
        if is_pvm_library_path(path) and DIRECTORY not in path.split("/"):
            root = path.rsplit("/_library/", 1)[0]
            for name, entry in doc.items():
                definition = entry.get("definition_id") or str(uuid5(NAMESPACE_URL, USER_DEFINITION_ID_SEED + str(name)))
                authored_keys[root + ":" + name] = root + ":user:" + definition
    for obj in bundle["objects"]:
        path = obj["path"]
        if obj["kind"] == "legacy_revision":
            continue
        doc = documents.get(path, {})
        if not isinstance(doc, dict):
            continue
        if is_pvm_library_path(path):
            root = path.rsplit("/_library/", 1)[0]
            for name, entry in doc.items():
                definition = entry.get("definition_id") or str(uuid5(NAMESPACE_URL, USER_DEFINITION_ID_SEED + str(name)))
                key = root + ":user:" + definition
                authored_keys[root + ":" + name] = key
                classes[key] = {"id": key, "name": name, "kind": entry.get("definition_kind", "pvm"),
                                "source": path, "root": root, "revision": entry.get("definition_revision", 1),
                                "object_id": str(obj["id"]), "object_revision": obj["revision"]}
        elif is_configuration_document_path(path):
            root, name = split_configuration_document_path(path)
            key = authored_keys.get(root + ":" + name, root + ":" + name)
            classes.setdefault(key, {"id": key, "name": name,
                               "kind": "installed PVM", "source": path, "root": root,
                               "revision": obj["revision"], "object_id": str(obj["id"]),
                               "object_revision": obj["revision"]})
        elif "graph" in doc and "public_parameters" in doc and doc.get("id"):
            module_class = doc.get("definition_kind") == "control_module_class"
            prefix = "module_class:" if module_class else "composite:"
            key = prefix + doc["id"]
            classes[key] = {"id": key, "name": doc["name"],
                            "kind": "control module class" if module_class else "composite", "root": "",
                            "source": path, "revision": doc["revision"], "object_id": str(obj["id"]),
                            "object_revision": obj["revision"]}
    for obj in bundle["objects"]:
        doc = documents.get(obj["path"], {})
        if obj["kind"] == "module":
            module_link = doc.get("module_class") or {}
            if module_link.get("definition_id"):
                instances.append({
                    "id": str(obj["id"]) + "/module_class",
                    "class_id": "module_class:" + module_link["definition_id"],
                    "source": obj["path"],
                    "location": "/",
                    "kind": "module_class",
                    "name": doc.get("name", ""),
                    "revision": module_link.get("definition_revision", 0),
                    "data": doc,
                })
            def blocks(graph, location=""):
                for index, block in enumerate(graph.get("blocks", [])):
                    pointer = location + f"/blocks/{index}"
                    if block.get("definition_id"):
                        instances.append({"id": str(obj["id"]) + pointer, "class_id": "composite:" + block["definition_id"],
                                          "source": obj["path"], "location": pointer, "kind": "composite",
                                          "name": doc.get("name", "") + "/" + block.get("instance_name", block["id"]),
                                          "revision": block.get("definition_revision", 0), "data": block})
                    # Nested linked instances are governed by their owning class;
                    # editing them would detach the parent's effective graph.
                    elif block.get("inner_graph"):
                        blocks(block["inner_graph"], pointer + "/inner_graph")
            blocks(doc)
        elif obj["kind"] == "display":
            root = obj["path"].rsplit("/", 2)[0]
            seen = set()
            for item in doc.get("items", []):
                name = item.get("instance_definition") or item.get("user_pvm")
                identity = item.get("instance_id") or item.get("group")
                if name and identity and identity not in seen:
                    seen.add(identity)
                    key = (root + ":user:" + item["definition_id"]) if item.get("definition_id") else authored_keys.get(root + ":" + name, root + ":" + name)
                    instances.append({"id": str(obj["id"]) + ":" + identity, "class_id": key,
                                      "source": obj["path"], "location": identity, "kind": "authored",
                                      "name": doc.get("display", doc.get("name", "")) + " / " + name + " / " + identity[:8],
                                      "revision": item.get("class_revision", ""), "data": item})
            from ..hmi.pvms.base import registry
            for item in doc.get("pvms", []):
                block, _, role = item.get("class", "").partition("/")
                cls = registry.get(block, role, item.get("variant", "")) or registry.get(block, role)
                if cls:
                    name = cls.__name__
                    key = root + ":" + name
                    classes.setdefault(key, {"id": key, "name": name, "kind": "installed PVM", "source": "",
                                            "root": root, "revision": 0, "object_id": "", "object_revision": 0})
                    instances.append({"id": str(obj["id"]) + ":" + item["id"], "class_id": key,
                                      "source": obj["path"], "location": item["id"], "kind": "installed",
                                      "name": doc.get("display", doc.get("name", "")) + " / " + item["id"],
                                      "revision": item.get("class_revision", ""), "data": item})
    for instance in instances:
        cls = classes.get(instance["class_id"])
        instance["status"] = "Class unavailable"
        instance["properties"] = []
        if not cls:
            continue
        row = instance["data"]
        if instance["kind"] in {"composite", "module_class"}:
            link = (row.get("module_class") or {}
                    if instance["kind"] == "module_class" else row)
            current = link.get("definition_snapshot") or {}
            desired = documents[cls["source"]]
            instance["status"] = "Current" if current.get("digest") == desired.get("digest") else "Update available"
            overrides = link.get("public_parameter_overrides", {})
            instance["properties"] = [{"name": p["name"], "inherited": p.get("default"),
                                       "value": overrides.get(p["name"], p.get("default")),
                                       "origin": "Override" if p["name"] in overrides else "Inherited"}
                                      for p in current.get("public_parameters", [])]
            if instance["kind"] == "module_class":
                from ..strategy.module_classes import analyze_instance
                try:
                    instance["deviations"] = len(
                        analyze_instance(row).deviations)
                except (TypeError, ValueError):
                    instance["deviations"] = "Invalid"
        else:
            root = cls["root"]
            current_root = root + "/" + DIRECTORY + "/" + str(instance["revision"]) if instance["revision"] else root
            old_name = retained_name(documents, current_root, cls)
            instance["status"] = ("Unpinned" if not instance["revision"] else "Current" if
                                  class_stamp(documents, root, cls["name"]) == class_stamp(documents, current_root, old_name)
                                  else "Update available")
            cfg = configuration(documents, current_root, old_name)
            choices = row.get("choices", {}) if instance["kind"] == "installed" else row.get("instance_choices", row.get("pvm_choices", {}))
            instance["properties"] = [{"name": p.name, "inherited": cfg.value_of(p.name, {}),
                                       "value": cfg.value_of(p.name, choices), "origin": "Override" if p.name in choices else "Inherited"}
                                      for p in cfg.all_properties()]
            instance["properties"] += [{"name": name, "value": value, "inherited": "Shape default", "origin": "Override"}
                                       for name, value in row.get("instance_overrides", row.get("pvm_overrides", {})).items()]
    return {"project": plain(bundle["project"]), "classes": list(classes.values()), "instances": instances}


def _locate(document, pointer):
    result = document
    for component in pointer.strip("/").split("/"):
        result = result[int(component)] if isinstance(result, list) else result[component]
    return result


def adoption(bundle, selected, *, pin=False, target=None):
    """Return ordinary document edits plus immutable supporting class files."""
    state = inventory(bundle)
    rows = {r["id"]: r for r in state["instances"]}
    if not selected or len(selected) != len(set(selected)) or set(selected) - rows.keys():
        raise ConfigurationError("Select existing, unique class instances")
    target = target or bundle
    classes = {r["id"]: r for r in inventory(target)["classes"]}
    documents, target_documents = decode(bundle), decode(target)
    original = deepcopy(documents)
    added, review, captures = {}, [], {}
    for identity in selected:
        instance = rows[identity]
        cls = classes.get(instance["class_id"])
        if not cls:
            raise ConfigurationError("Class is unavailable in the selected generation: " + instance["name"])
        doc = documents[instance["source"]]
        if instance["kind"] == "module_class":
            if pin:
                continue
            from ..strategy.module_classes import (
                ModuleClassDefinition,
                apply_update,
            )
            definition = ModuleClassDefinition.from_dict(
                deepcopy(target_documents[cls["source"]]))
            try:
                updated = apply_update(
                    doc, definition, preserve_deviations=True)
            except (TypeError, ValueError, KeyError) as error:
                raise ConfigurationError(
                    "Module-class adoption needs conflict review: "
                    + str(error)) from error
            doc.clear()
            doc.update(updated)
            new_revision = definition.revision
        elif instance["kind"] == "composite":
            if pin:
                continue
            from ..strategy.blocks.composite_blocks import CompositeBlock
            from ..strategy.composites.library import CompositeDefinition
            raw = _locate(doc, instance["location"])
            block = CompositeBlock.from_dict(raw)
            definition = CompositeDefinition.from_dict(deepcopy(target_documents[cls["source"]]))
            names = {p.name: p for p in definition.public_parameters}
            for p in block.definition_snapshot.get("public_parameters", []):
                if p["name"] in block.public_parameter_overrides and (p["name"] not in names or names[p["name"]].data_type != p["data_type"]):
                    raise ConfigurationError("Adoption would invalidate an override: " + p["name"])
            ports = {("in", k): v.data_type for k, v in block.inputs.items()} | {("out", k): v.data_type for k, v in block.outputs.items()}
            block.refresh_from_definition(definition, expected_revision=block.definition_revision)
            updated_ports = {("in", k): v.data_type for k, v in block.inputs.items()} | {("out", k): v.data_type for k, v in block.outputs.items()}
            if any(updated_ports.get(key) != value for key, value in ports.items()):
                raise ConfigurationError("Composite boundary changed; review its wiring before adoption")
            raw.update(block.to_dict())
            new_revision = definition.revision
        else:
            if pin and instance["revision"]:
                continue
            graphics_root = cls["root"]
            if graphics_root not in captures:
                captures[graphics_root] = capture_files(target, graphics_root)
            new_revision, files = captures[graphics_root]
            added.update({f["path"]: f for f in files})
            if instance["kind"] == "installed":
                item = next(item for item in doc["pvms"] if item["id"] == instance["location"])
                before_root = graphics_root + "/" + DIRECTORY + "/" + str(instance["revision"]) if instance["revision"] else graphics_root
                _check_choices(configuration(documents, before_root, cls["name"]),
                               configuration(target_documents, graphics_root, cls["name"]), item.get("choices", {}))
                item["class_revision"] = new_revision
            else:
                before_root = graphics_root + "/" + DIRECTORY + "/" + str(instance["revision"]) if instance["revision"] else graphics_root
                _adopt_authored(doc, instance, cls, target_documents, new_revision, pin=pin,
                                before=configuration(documents, before_root, retained_name(documents, before_root, cls)))
        review.append({"instance": instance["name"], "before": instance["revision"] or "Unpinned",
                       "after": new_revision, "action": "Pin current geometry and contract" if pin else "Adopt class; retain instance overrides"})
    objects = {o["path"]: o for o in bundle["objects"]}
    edits = [{"path": path, "content": base64.b64encode(json.dumps(doc, ensure_ascii=False, indent=2).encode()).decode(),
              "expected_revision": objects[path]["revision"]} for path, doc in documents.items() if doc != original[path]]
    edits += [{**file, "expected_revision": 0} for path, file in added.items() if path not in objects]
    if not edits:
        raise ConfigurationError("Selected instances already retain this class version")
    return normalize_edits(edits), review


def _check_choices(before, after, choices):
    for name, value in choices.items():
        old, new = before.property(name), after.property(name)
        if (old and (not new or old.ptype != new.ptype)) or (new and new.ptype == "Selection" and new.option(value) is None):
            raise ConfigurationError(f"Incompatible instance choice: {name} = {value}")


def _adopt_authored(document, instance, cls, documents, revision, *, pin, before):
    members = [item for item in document["items"] if (item.get("instance_id") or item.get("group")) == instance["location"]]
    first = next(item for item in members if item.get("kind") != "pipe")
    if pin:
        for member in members:
            member["class_revision"] = revision
        return
    if first.get("instance_link", first.get("pvm_link", "linked")) != "linked":
        raise ConfigurationError("Unlinked instance has independent geometry; relink explicitly before adoption")
    root = cls["root"]
    configurations = configuration_documents(documents, root)
    library = UserPvmLibrary.from_snapshot(root, find_library_document(documents, root), configurations)
    cfg = configuration(documents, root, cls["name"])
    choices = first.get("instance_choices", first.get("pvm_choices", {}))
    _check_choices(before, cfg, choices)
    origin = (float(first.get("x", 0)) - float(first.get("source_x", 0)),
              float(first.get("y", 0)) - float(first.get("source_y", 0)))
    standard_path = str(Path(root).parent.as_posix()) + "/_standards.json"
    standards = {row["name"]: row["value"] for row in documents.get(standard_path, {}).get("standards", [])}
    # Legacy numeric overrides refer to the placed member order. Resolving
    # them against the new class can move an override to a different shape.
    source_by_index = {}
    for member in members:
        try:
            index = int(member.get("pvm_index", -1))
        except (TypeError, ValueError):
            continue
        if member.get("source_element_id"):
            source_by_index[index] = member["source_element_id"]
    overrides = library.normalize_overrides(
        cls["name"], first.get("instance_overrides", first.get("pvm_overrides", {})),
        source_by_index=source_by_index)
    created = library.instantiate(cls["name"], *origin, config=cfg, choices=choices,
                                  standards=standards.get,
                                  link="linked", overrides=overrides,
                                  instance_id=first.get("instance_id"), group_id=first.get("group"),
                                  instance_origin=(first.get("instance_origin_x", origin[0]), first.get("instance_origin_y", origin[1])),
                                  unlink_nested=bool(first.get("instance_detached_nested")),
                                  detached_nested=first.get("instance_detached_nested", {}))
    if not created:
        raise ConfigurationError("Class did not produce an instance: " + cls["name"])
    replacements = {item.get("source_element_id"): item for item in created if item.get("source_element_id")}
    remap = {}
    for old in members:
        new = replacements.get(old.get("source_element_id"))
        if new:
            remap[new["id"]] = old["id"]
            # Preserve engineer placement/transforms; class geometry still supplies
            # dimensions unless explicitly overridden through the class contract.
            for key in ("rot", "z", "layer", "visible", "locked"):
                if key in old:
                    new[key] = old[key]
    removed = {item["id"] for item in members} - set(remap.values())
    for item in document["items"]:
        if item not in members and item.get("kind") == "pipe" and any(item.get(end) in removed for end in ("a", "b")):
            raise ConfigurationError("Class removes an externally connected shape; reconnect before adoption")
    for item in created:
        item["class_revision"] = revision
        for key in ("id", "a", "b"):
            if item.get(key) in remap:
                item[key] = remap[item[key]]
    first_index = document["items"].index(members[0])
    document["items"] = [item for item in document["items"] if item not in members]
    document["items"][first_index:first_index] = created


class LibraryRepository:
    def __init__(self, repository):
        self.repo = repository
        self.editing = EditingRepository(repository)

    def state(self, token, project):
        result = inventory(self.repo.export(token, project))
        for instance in result["instances"]:
            instance.pop("data", None)
        return result

    def preview(self, token, project, selected, *, pin=False, generation=None):
        from psycopg.types.json import Jsonb
        with self.repo.connection() as c:
            actor, _ = self.editing._project(c, token, project, edit=True)
        bundle = self.repo.export(token, project)
        target = self.repo.export(token, project, generation=generation) if generation else bundle
        try:
            edits, rows = adoption(bundle, selected, pin=pin, target=target)
        except ConfigurationError:
            raise
        except (ValueError, KeyError, TypeError, OSError) as error:
            raise ConfigurationError("Cannot adopt this class configuration: " + str(error)) from error
        self.editing._candidate(bundle, edits, allow_class_snapshots=True)
        import difflib
        documents, target_documents = decode(bundle), decode(target)
        classes = {row["id"]: row for row in inventory(target)["classes"]}
        diffs = []
        for instance in inventory(bundle)["instances"]:
            if instance["id"] not in selected:
                continue
            cls = classes[instance["class_id"]]
            if instance["kind"] == "composite":
                before = instance["data"].get("definition_snapshot", {})
                after = target_documents[cls["source"]]
            else:
                root = cls["root"]
                previous_root = root + "/" + DIRECTORY + "/" + str(instance["revision"]) if instance["revision"] else root
                before = class_contract(documents, previous_root, retained_name(documents, previous_root, cls))
                after = class_contract(target_documents, root, cls["name"])
            diff = "\n".join(difflib.unified_diff(json.dumps(before, indent=2, sort_keys=True).splitlines(),
                                                 json.dumps(after, indent=2, sort_keys=True).splitlines(),
                                                 fromfile="Retained class", tofile="Proposed class", lineterm=""))
            diffs.append({"instance": instance["name"], "text": diff or "Class contract unchanged; current geometry and class files are retained."})
        if sum(len(row["text"]) for row in diffs) > 2_000_000:
            raise ConfigurationError("Class comparison is too large; review fewer instances together")
        preview = {"generation": bundle["project"]["generation"], "target_generation": target["project"]["generation"],
                   "edits": edits, "rows": rows, "diffs": diffs, "selected": selected, "pin": pin}
        key = str(uuid4())
        with self.repo.connection() as c:
            self.editing._project(c, token, project, edit=True)
            c.execute("INSERT INTO library_previews(id,project_id,actor,document) VALUES (%s,%s,%s,%s)",
                      (key, project, actor["name"], Jsonb(plain(preview))))
        return {"id": key, **{k: v for k, v in preview.items() if k != "edits"}}

    def commit(self, token, project, preview, reason, command):
        project, preview, command = identifier(project), identifier(preview), identifier(command)
        with self.repo.connection() as c:
            actor, _ = self.editing._project(c, token, project, edit=True)
            row = c.execute("SELECT * FROM library_previews WHERE id=%s AND project_id=%s",
                            (identifier(preview), identifier(project))).fetchone()
            if not row or row["actor"] != actor["name"]:
                raise Forbidden("This class review belongs to another engineering session")
            document = row["document"]
            digest = checkin_hash(project, preview, document["edits"], reason.strip(), document["generation"])
            retry = self.editing._retry(c, actor, project, command, digest)
            if retry:
                return retry
        document = row["document"]
        session = str(preview)
        self.editing.lease(token, project, session, [edit["path"] for edit in document["edits"]])
        try:
            return self.editing.checkin(token, project, session, document["edits"], reason, command,
                                        expected_generation=document["generation"], allow_class_snapshots=True)
        finally:
            self.editing.lease(token, project, session, [], release=True)
