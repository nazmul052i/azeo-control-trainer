"""Read projections over captured configuration, never a second document authority.

References retain their source location and resolution state. A dynamic script
is an explicit coverage gap, not evidence that its target has no consumers.
This module creates no Qt objects and never reads service-side project files.
"""
from __future__ import annotations

from ..hmi.compatibility import (
    PVM_SCOPE_PREFIXES, is_configuration_document_path, normalize_display_document,
)

from dataclasses import asdict
from array import array
from bisect import bisect_right
from heapq import merge
from pathlib import Path

from .documents import ConfigurationError, catalog_value, safe_path

VERSION = 1


def build_catalog(documents, tags, graphs):
    documents = [d for d in documents if d.kind != "legacy_revision"]
    from ...connectivity.fieldio.local_virtual_io import load_signal_routes
    from ..hmi.pvms import registry
    from ..hmi.binding.engine import format_template
    from ..strategy.tagdb import TagEntry

    by_file = {d.path: d for d in documents}
    project = by_file["_project.json"].payload
    nodes, edges, issues = {}, [], []

    def node(key, kind, label, source="", **data):
        nodes[key] = {"path": key, "kind": kind, "name": label, "source": source, **data}
        return key

    def edge(source, target, kind, location="", *, dynamic=False):
        if target:
            dynamic = dynamic or "{" in str(target) or str(target).startswith(
                (*PVM_SCOPE_PREFIXES, "Dsp.", "Lyt.", "Grp.", "Standard.", "GL."))
            edges.append({"source": source, "target": str(target), "kind": kind,
                          "location": location, "status": "dynamic" if dynamic else "pending"})

    def issue(source, location, message):
        issues.append({"source": source, "location": location, "message": message})

    def captured_catalog(raw_path):
        path = safe_path(raw_path.removeprefix("${PROJECT_DIR}/"))
        if path not in by_file or by_file[path].payload is None:
            raise ConfigurationError(f"I/O catalog is absent from captured files: {path}")
        return by_file[path].payload

    used_io = {t["io_tag"] for t in tags if t.get("io_tag")}
    for index, area in enumerate(project.get("areas", [])):
        controller = area.get("controller") or {}
        controller = controller if isinstance(controller, dict) else {"name": str(controller)}
        owner = node("controller:" + str(controller.get("name") or index), "controller",
                     str(controller.get("name") or area.get("name") or index), "_project.json",
                     configuration=controller)
        for strategy in area.get("strategies", []):
            path = strategy if isinstance(strategy, str) else strategy.get("path", "")
            graph = graphs.get(path)
            edge(graph.name if graph else "file:" + str(path), owner, "controller_assignment",
                 f"areas/{index}/strategies")
        config = area.get("virtual_io") or (area.get("eioc") or {}).get("field_io") \
            or area.get("field_io") or {}
        if not config:
            continue
        location = f"areas/{index}"
        catalog_spec = config.get("signal_catalog") or config.get("catalog")
        catalog_path = catalog_spec.get("path", "") if isinstance(catalog_spec, dict) else catalog_spec
        route_source = str(catalog_path or "_project.json").removeprefix("${PROJECT_DIR}/")
        try:
            routes = load_signal_routes(config, Path("."), catalog_loader=captured_catalog)
        except (ValueError, ConfigurationError) as error:
            issue("_project.json", location, f"Configured I/O could not be indexed: {error}")
            continue
        for name, route in routes.items():
            key = "io:" + name
            record = catalog_value(asdict(route))
            if key in nodes and nodes[key].get("route") != record:
                issue("_project.json", location, f"Conflicting I/O definitions for {name}")
            node(key, "io", name, route_source, route=record, io_tag=name,
                 project_location=location, route_sources=list(dict.fromkeys(["_project.json", route_source])),
                 description=route.description, data_type=route.data_type or (
                     "BOOL" if route.kind in {"DI", "DO"} else "FLOAT"), unit=route.unit)
            if name not in used_io:
                tags.append({"source": route_source, **catalog_value(asdict(TagEntry(
                    path=f"FIELD/{name}", kind="field", module="", name=name,
                    block_type=route.kind, data_type=nodes[key]["data_type"], unit=route.unit,
                    description=route.description, io_tag=name,
                    direction="input" if route.direction == "read" else "output",
                    writable=route.direction == "read", eu_range=route.eu_range)))})
                used_io.add(name)

    def class_key(bt, role, variant=""):
        return f"class:{bt}/{role}" + (f"@{variant}" if variant else "")

    from ..hmi.pvms.configurator.model import PvmConfiguration, REFERENCE_TYPES
    configurations = {}
    for document in documents:
        if not is_configuration_document_path(document.path):
            continue
        key = node("configuration:" + document.path, "class_configuration", document.name,
                   document.path, document=document.payload)
        try:
            config = PvmConfiguration.from_dict(document.payload)
            config.validate()
        except (TypeError, KeyError, AttributeError, ValueError) as error:
            issue(key, "", f"Invalid PVM configuration: {error}")
            continue
        if config.pvm_class in configurations:
            issue(key, "", "Duplicate PVM configuration identity; instance resolution is ambiguous")
            configurations[config.pvm_class] = None
            continue
        configurations[config.pvm_class] = (config, key)
        for prop in config.all_properties():
            if prop.ptype in REFERENCE_TYPES:
                target = config.value_of(prop.name, {})
                edge(key, target, "default_reference", prop.name,
                     dynamic="{" in target or target.startswith(PVM_SCOPE_PREFIXES))

    for (bt, role, variant), cls in sorted(registry.all_classes().items()):
        key = class_key(bt, role, variant)
        node(key, "faceplate" if role == "faceplate" else "class", cls.display_name or cls.__name__,
             origin="installed", block_type=bt, role=role, variant=variant,
             parameters=list(cls.PARAMS), bindings=[asdict(b) for b in cls.bindings],
             preferred_size=cls.DEFAULT_SIZE, description=cls.EXCEPTION)
        if configurations.get(cls.__name__):
            edge(key, configurations[cls.__name__][1], "class_configuration")
        if role.startswith("dynamo") and registry.get(bt, "faceplate"):
            edge(key, class_key(bt, "faceplate"), "faceplate_class")

    for source, graph in graphs.items():
        for block in graph.blocks.values():
            base = f"{graph.name}/{block.instance_name}"
            node(base, "block", block.instance_name, source, module=graph.name,
                 block_type=block.block_type, block_id=block.id,
                 config=catalog_value(block.config.params))
            if registry.get(block.block_type, "faceplate"):
                edge(base, class_key(block.block_type, "faceplate"), "faceplate_class")
            if block.block_type == "PID":
                from ..strategy.engine.bridge_contract import PID_SIGNAL_EXPORTS
                for field, terminal in PID_SIGNAL_EXPORTS.items():
                    key = "io:ctrl." + block.instance_name + "." + field
                    if key in nodes:
                        issue(base, "", f"Ambiguous controller store publication: {key}")
                        nodes[key]["ambiguous"] = True
                    else:
                        node(key, "store_publication", key.removeprefix("io:"), source,
                             module=graph.name, terminal=f"{base}/{terminal}",
                             description="Controller publication; OUT is normalized to percent")
                    edge(key, f"{base}/{terminal}", "controller_publication")
            for key, value in block.config.params.items():
                if key in {"path", "quality_path", "limit_path", "remote_path"} and isinstance(value, str):
                    edge(base, "io:" + value if value.startswith("ctrl.") else value,
                         "remote_reference", f"config/{key}", dynamic="{" in value)
                elif key == "controller_tag" and value:
                    targets = [f"{g.name}/{b.instance_name}" for g in graphs.values()
                               for b in g.blocks.values() if b.block_type == "PID" and b.instance_name == value]
                    edge(base, targets[0] if len(targets) == 1 else f"controller_tag:{value}",
                         "controller_reference", f"config/{key}")
                elif key.upper() == "SCRIPT" and value:
                    issue(base, f"config/{key}", "Control script dependencies require runtime context")
        from ..strategy.tagdb import TagDatabase
        db = TagDatabase.from_graphs([graph])
        for source_path, targets in db._readers.items():
            for target in targets:
                edge(source_path, target, "wire")

    for tag in tags:
        if tag.get("io_tag"):
            edge(tag["path"], "io:" + tag["io_tag"], "field_binding")

    # Duplicate names in different library roots remain distinct definitions.
    definitions = {}
    for document in documents:
        if document.path.endswith("/_library/user_pvms.json") and isinstance(document.payload, dict):
            for name, definition in document.payload.items():
                if not isinstance(definition, dict):
                    continue
                definitions.setdefault(name, []).append((document.path, definition))
    for name, entries in definitions.items():
        for source, definition in entries:
            key = "user:" + (name if len(entries) == 1 else source + "#" + name)
            node(key, definition.get("definition_kind", "pvm"), name,
                 source, origin="authored", definition=definition)
            if definition.get("paired_faceplate"):
                edge(key, "user:" + definition["paired_faceplate"], "paired_faceplate")

    def walk(value, owner, location):
        if isinstance(value, list):
            for i, child in enumerate(value):
                walk(child, owner, f"{location}/{i}")
        elif isinstance(value, dict):
            kind = value.get("kind")
            from ..hmi.pvms.elements import element_paths
            for target in element_paths(value):
                edge(owner, target, "element_binding", location, dynamic="{" in target)
            if kind in {"datalink", "data_link", "user_entry"}:
                target = (value.get("entry") or {}).get("path") if kind == "user_entry" else value.get("path")
                edge(owner, target, "binding", location, dynamic="{" in str(target))
            if kind in {"display_link", "open_display"}:
                target = str(value.get("target", ""))
                candidates = [d for d in documents if d.kind == "display"
                              and d.payload.get("display") == target]
                edge(owner, "display:" + candidates[0].path if len(candidates) == 1 else "display:" + target,
                     "navigation", location)
            if kind in {"open_faceplate", "open_detail", "write_value", "add_to_watch"}:
                edge(owner, value.get("target"), kind, location,
                     dynamic="{" in str(value.get("target", "")))
            if kind in {"open_user_faceplate", "open_user_detail"}:
                edge(owner, "user:" + str(value.get("target", "")), "open_user_faceplate", location)
            if kind == "script":
                issue(owner, location, "Script dependencies cannot be fully resolved statically")
            if kind in {"animation", "blink"}:
                edge(owner, value.get("path") or value.get("condition"), "binding", location,
                     dynamic=bool(value.get("indirect")))
            if kind == "expression":
                refs = value.get("refs")
                if isinstance(refs, dict):
                    for target in refs.values():
                        edge(owner, target, "expression", location, dynamic="{" in str(target))
                else:
                    # Reuse the runtime's legacy DLSYS literal-path grammar.
                    from ..hmi.pvms.properties import paths_in_expression
                    paths = paths_in_expression(str(value.get("expr") or value.get("text") or ""))
                    for target in paths:
                        edge(owner, target, "expression", location)
                    if not paths:
                        issue(owner, location, "Expression has no statically indexed tag references")
            if kind == "nested_pvm" and value.get("class"):
                edge(owner, "user:" + value["class"], "nested_class", location)
            if value.get("user_pvm"):
                edge(owner, "user:" + value["user_pvm"], "uses_class", location)
            legacy = value.get("anim")
            if isinstance(legacy, dict):
                for name, descriptor in legacy.items():
                    if isinstance(descriptor, dict) and "kind" not in descriptor:
                        walk({"kind": "animation", **descriptor}, owner, f"{location}/anim/{name}")
            if value.get("indirect") or kind in {"pvm", "property"}:
                issue(owner, location, "Property/indirect reference requires instance or runtime context")
            for key, child in value.items():
                if key in {"script", "scripts", "typescript", "event_scripts"} and child:
                    issue(owner, f"{location}/{key}", "Script dependencies cannot be fully resolved statically")
                walk(child, owner, f"{location}/{key}")

    for document in documents:
        if document.kind == "display":
            data = normalize_display_document(document.payload)
            owner = node("display:" + document.path, "display", data.get("display", document.name),
                         document.path, level=data.get("level", "L2"), document=data)
            for index, pvm in enumerate(data.get("pvms", [])):
                location = f"pvms/{index}"
                key = node(owner + "#" + str(pvm.get("id", index)), "pvm_instance",
                           str(pvm.get("label") or pvm.get("id", index)), document.path,
                           display=owner, placement=pvm)
                edge(owner, key, "contains", location)
                parts = str(pvm.get("class", "")).split("/")
                if len(parts) != 2:
                    issue(key, location, "Unknown installed PVM class key")
                    continue
                bt, role = parts
                variant = pvm.get("variant", "")
                cls = registry.get(bt, role, variant) or registry.get(bt, role)
                edge(key, class_key(bt, role, variant if registry.get(bt, role, variant) else ""),
                     "uses_class", location)
                params = pvm.get("params", {})
                for target in params.values():
                    edge(key, target, "instance_parameter", location, dynamic="{" in str(target))
                if cls is not None:
                    configured = configurations.get(cls.__name__)
                    if configured:
                        config, config_key = configured
                        edge(key, config_key, "class_configuration")
                        try:
                            params = config.binding_params(pvm.get("choices", {}), base=params)
                        except ValueError as error:
                            issue(key, location, f"Instance configuration cannot be resolved: {error}")
                    for binding in cls.bindings:
                        if not binding.path:
                            continue
                        try:
                            target = format_template(binding.path, params)
                        except (KeyError, ValueError, TypeError):
                            edge(key, binding.path, "binding", binding.key, dynamic=True)
                        else:
                            edge(key, target, "binding", binding.key)
            walk(data.get("items", []), owner, "items")
            walk(data.get("events", {}), owner, "events")
            if data.get("parent"):
                walk({"kind": "display_link", "target": data["parent"]}, owner, "parent")
            walk(data.get("scripts", {}), owner, "scripts")
            if data.get("scripts"):
                issue(owner, "scripts", "Script dependencies cannot be fully resolved statically")
        elif is_configuration_document_path(document.path):
            walk(document.payload, "configuration:" + document.path, "")

    for key, data in list(nodes.items()):
        if data.get("origin") == "authored":
            walk(data["definition"], key, "definition")

    known = set(nodes) | {t["path"] for t in tags}
    for ref in edges:
        if ref["target"].startswith("user:") and ref["target"] not in known:
            name = ref["target"].removeprefix("user:")
            source_file = nodes.get(ref["source"], {}).get("source", "")
            candidates = [n for n in nodes.values() if n.get("origin") == "authored"
                          and n["name"] == name and source_file.startswith(
                              n["source"].split("/_library/", 1)[0] + "/")]
            if candidates:
                candidates.sort(key=lambda n: len(n["source"]), reverse=True)
                ref["target"] = candidates[0]["path"]
        if ref["kind"] == "remote_reference" and "io:" + ref["target"] in nodes:
            ref["target"] = "io:" + ref["target"]
        publication = nodes.get(ref["target"], {})
        if ref["kind"] in {"remote_reference", "field_binding"} and publication.get("ambiguous"):
            ref["status"] = "unresolved"
        elif ref["kind"] in {"remote_reference", "field_binding"} and publication.get("terminal"):
            ref["via"] = ref["target"]
            ref["target"] = publication["terminal"]
        if ref["status"] == "pending":
            ref["status"] = "resolved" if ref["target"] in known else "unresolved"
        if ref["source"] not in known:
            ref["status"] = "unresolved"
    return catalog_value({"version": VERSION, "source_project_id": project.get("project_id", ""),
                          "source_name": project.get("name", ""), "nodes": list(nodes.values()),
                          "edges": edges, "issues": issues,
                          "coverage": "Static configured references; scripts and runtime indirection remain explicit gaps."})


class _SearchText:
    """A generation's normalized text searched in C, with bounded result rows."""

    def __init__(self, records):
        self.rows = [row for row, _ in records]
        self.starts = array("Q")
        position = 0
        texts = []
        common = {key: str(records[0][0].get(key, "")) for key in (
            "path", "name", "description", "io_tag", "block_type")} if records else {}
        for row, text in records:
            common = {key: value for key, value in common.items() if str(row.get(key, "")) == value}
            text = text.replace("\n", " ")
            self.starts.append(position)
            texts.append(text)
            position += len(text) + 1
        self.text = "\n".join(texts)
        self.common = " ".join(common.values()).casefold()

    def find(self, words, offset, limit):
        words = [word for word in words if word not in self.common]
        if not words:
            return self.rows[offset:offset + limit], len(self.rows)
        # Search the least frequent literal in C, then inspect only matching
        # records. A missing word does not run a Python callback per object.
        first = min(words, key=self.text.count) if len(words) > 1 else words[0]
        others = [word for word in words if word != first]
        rows, total = [], 0
        position = self.text.find(first)
        while position >= 0:
            end = self.text.find("\n", position)
            if end < 0:
                end = len(self.text)
            start = self.text.rfind("\n", 0, position) + 1
            if not others or all(word in self.text[start:end] for word in others):
                if offset <= total < offset + limit:
                    rows.append(self.rows[bisect_right(self.starts, position) - 1])
                total += 1
            position = self.text.find(first, end + 1)
        return rows, total


class CatalogIndex:
    """One coherent generation, searched locally with bounded result pages."""

    def __init__(self, bundle):
        self.bundle = bundle
        self.project = bundle["project"]
        self.catalog = bundle["catalog"]
        self.entries = {r["path"]: r for r in self.catalog["nodes"]}
        for tag in bundle["tags"]:
            self.entries[tag["path"]] = {**self.entries.get(tag["path"], {}), **tag}
        objects = {r["path"]: r for r in bundle.get("objects", [])}
        self.entries = {key: {**row, "object_revision": objects.get(row.get("source"), {}).get("revision"),
                             "object_id": objects.get(row.get("source"), {}).get("id")}
                        for key, row in self.entries.items()}
        from .identities import point_identity
        for row in self.entries.values():
            block = self.entries.get(str(row.get("module", "")) + "/" + str(row.get("block", "")), {})
            if row.get("kind") in {"terminal", "parameter"} and row.get("object_id") and block.get("block_id"):
                row["point_id"] = point_identity(self.project["id"], row["object_id"], block["block_id"],
                                                 row["name"], row["kind"])
        records = [(r, " ".join(str(r.get(k, "")) for k in (
            "path", "name", "description", "io_tag", "block_type")).casefold())
            for r in sorted(self.entries.values(), key=lambda r: r["path"])]
        self._search_text = _SearchText(records)
        grouped = {}
        for row, text in records:
            grouped.setdefault(row.get("kind"), []).append((row, text))
        self._kind_text = {kind: _SearchText(rows) for kind, rows in grouped.items()}

    def search(self, query="", *, kinds=(), offset=0, limit=200):
        words = query.casefold().split()
        if not kinds:
            return self._search_text.find(words, offset, limit)
        groups = [self._kind_text[kind] for kind in set(kinds) if kind in self._kind_text]
        if len(groups) == 1:
            return groups[0].find(words, offset, limit)
        pages = [group.find(words, 0, offset + limit) for group in groups]
        rows = list(merge(*(page for page, _ in pages), key=lambda row: row["path"]))
        return rows[offset:offset + limit], sum(total for _, total in pages)

    def references(self, path, *, incoming=False):
        # Block/module inspection includes descendants; exact parameters stay exact.
        def matches(value):
            return value == path or value.startswith(path + "/")
        return [e for e in self.catalog["edges"] if matches(e["target"]) or (
            not incoming and matches(e["source"]))]

    def loop(self, path):
        row = self.entries.get(path, {})
        module = row.get("module") or (path.split("/")[0] if path.split("/")[0] in self.entries else "")
        return module or path
