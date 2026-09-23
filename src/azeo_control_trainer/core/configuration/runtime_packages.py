"""Verified local release packages and adapters to existing controller/station engines."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from .documents import ConfigurationError, catalog_value, export_project, safe_path
from .release_validation import implementation_digest
from .release_manifest import deployable, fingerprint
from .package_paths import MARKER, release_root  # noqa: F401 — public compatibility
from ..strategy.serialization.strategy_io import graph_from_document, write_json_transactional
from ..strategy.serialization.tuning import runtime_config_snapshot, parameter_category


def configuration_fingerprint(parameters):
    # Mode and setpoint are operating state. Their normal movement must not
    # make every healthy controller look as if its engineering tuning changed.
    return fingerprint([{**row, "values": {key: value for key, value in row["values"].items()
                                          if parameter_category(key) not in {"mode", "sp"}}} for row in parameters])

def verify_package(package):
    manifest = package["manifest"]
    if manifest["implementation"] != implementation_digest():
        raise ConfigurationError("Installed application/classes differ from the release; validate a new release with this build")
    if manifest["package_hash"] != fingerprint({k: v for k, v in manifest.items() if k != "package_hash"}):
        raise ConfigurationError("Release manifest integrity check failed")
    files = {safe_path(f["path"]): f["content"] for f in package["bundle"]["files"]}
    if len(files) != len(manifest["objects"]):
        raise ConfigurationError("Release file inventory differs from the pinned manifest")
    for obj in manifest["objects"]:
        if obj["path"] not in files or hashlib.sha256(base64.b64decode(files[obj["path"]], validate=True)).hexdigest() != obj["digest"]:
            raise ConfigurationError("Release file integrity check failed: " + obj["path"])


def materialize(package, package_directory):
    """Run on an I/O worker. Never mutate a package already referenced by a view."""
    verify_package(package)
    root = Path(package_directory) / package["manifest"]["package_hash"]
    if root.exists():
        for obj in package["manifest"]["objects"]:
            path = root / safe_path(obj["path"])
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != obj["digest"]:
                raise ConfigurationError("Local pinned package was changed: " + obj["path"])
    else:
        root.parent.mkdir(parents=True, exist_ok=True)
        temporary = root.parent / ("_staging_" + str(uuid4()))
        export_project(package["bundle"], temporary)
        write_json_transactional(temporary / MARKER, {"id": package["id"], "manifest": package["manifest"]})
        temporary.rename(root)
    return root


def read_package(root):
    """Read restart metadata; callers verify bytes before putting anything online."""
    root = Path(root)
    package = json.loads((root / MARKER).read_text(encoding="utf-8"))
    package["bundle"] = {"files": [{"path": obj["path"], "content": base64.b64encode((root / safe_path(obj["path"])).read_bytes()).decode()}
                                    for obj in package["manifest"]["objects"]]}
    verify_package(package)
    return package


class RuntimeAdapter:
    """GUI-thread handoff to the existing compiler, runtime, bridge and publication rules."""
    def __init__(self, store, display_store, deployment):
        self.store, self.display_store, self.deployment = store, display_store, deployment
        self.loaded = {}

    def install_controller(self, package, root, selected=None):
        from ..strategy import blocks  # noqa: F401
        from ..strategy.engine.compiler import compile_strategy
        from ..strategy.engine.validator import validate_strategy
        from ..strategy.engine.runtime import StrategyRuntime
        from ..strategy.engine.bridge import DataBridge
        from ...azeo_explorer.download import graph_fingerprint
        if getattr(getattr(self.store, "pk_controller", None), "keylock", False):
            raise ConfigurationError("Controller keylock refuses release download")
        manifest = package["manifest"]
        selected = set(selected or manifest["selected"])
        prepared = []
        files = {file["path"]: file["content"] for file in package["bundle"]["files"]}
        for obj in manifest["objects"]:
            if obj["path"] not in selected or deployable(obj) != "controller":
                continue
            previous = self.loaded.get(obj["id"])
            if previous and previous["metadata"]["digest"] == obj["digest"] and previous["runtime"].is_online:
                continue
            graph, _ = graph_from_document(json.loads(base64.b64decode(files[obj["path"]])), strict=True)
            graph._configuration_identity = {
                "project_id": manifest.get("project_id", ""), "object_id": obj["id"],
                "revision": obj["revision"], "release_id": package["id"],
                "package_hash": manifest["package_hash"],
                "object_digest": obj["digest"],
            }
            errors = [f.message for f in validate_strategy(graph) if f.severity == "ERROR"]
            if errors:
                raise ConfigurationError("; ".join(errors))
            runtime = StrategyRuntime()
            runtime.load(compile_strategy(graph), DataBridge(self.store))
            prepared.append((obj, runtime, previous, graph_fingerprint(graph)))
        # Preflight every selected module before replacing any runtime. The
        # executive is the sole scanner and resumes after this GUI callback.
        for obj, runtime, previous, digest in prepared:
            if previous:
                previous["runtime"].go_offline()
                self.store.remove_strategy_runtime(previous["runtime"])
            try:
                if not runtime.go_online():
                    raise ConfigurationError("Controller refused to put the released module online")
                self.store.add_strategy_runtime(runtime)
            except Exception:
                if previous:
                    previous["runtime"].go_online()
                    self.store.add_strategy_runtime(previous["runtime"])
                raise
            metadata = {**obj, "object_id": obj["id"], "release_id": package["id"],
                        "package_hash": manifest["package_hash"], "loaded_fingerprint": digest,
                        "loaded_parameters": configuration_fingerprint([
                            {"block_id": b.id, "block_type": b.block_type, "values": catalog_value(runtime_config_snapshot(b))}
                            for b in runtime.compiled.graph.blocks.values()])}
            self.loaded[obj["id"]] = {"metadata": metadata, "runtime": runtime}

    def publish_station(self, package, job):
        from ..hmi.pvms.publishing import PvmDisplay
        manifest = package["manifest"]
        files = {file["path"]: file["content"] for file in package["bundle"]["files"]}
        for obj in manifest["objects"]:
            if obj["path"] not in manifest["selected"] or deployable(obj) != "station":
                continue
            raw = json.loads(base64.b64decode(files[obj["path"]]))
            display = PvmDisplay.from_dict(raw)
            if display.name != Path(obj["path"]).parent.name:
                raise ConfigurationError("Display identity differs from its release path")
            self.display_store.publish(display, env="TEST", workstations=[self.deployment.workstation],
                by="Repository release", repository_release={
                    "release_id": package["id"], "package_hash": manifest["package_hash"],
                    "command_id": job["id"], "object_id": obj["id"], "revision": obj["revision"], "digest": obj["digest"]})

    def journal(self):
        return {"modules": [entry["metadata"] for entry in self.loaded.values()]}

    def graphs(self):
        return {entry["runtime"].compiled.graph.name: entry["runtime"].compiled.graph for entry in self.loaded.values()}

    def observation(self, *, scanning=True):
        from ...azeo_explorer.download import graph_fingerprint
        objects = []
        for entry in self.loaded.values():
            runtime, metadata = entry["runtime"], entry["metadata"]
            graph = runtime.compiled.graph
            parameters = [{"block_id": block.id, "block_type": block.block_type,
                           "values": catalog_value(runtime_config_snapshot(block, self.store))}
                          for block in graph.blocks.values()]
            objects.append({**metadata, "active": scanning and runtime.is_online and runtime.scan_count > 0 and not runtime.is_debug_paused,
                            "scan_count": runtime.scan_count, "current_fingerprint": graph_fingerprint(graph),
                            "parameters": parameters, "parameters_hash": configuration_fingerprint(parameters)})
        for name in self.deployment.displays():
            held = self.deployment._held(name)
            entry = next((e for e in self.display_store.history(name) if e["rev"] == (held or self.deployment._latest(name))), {})
            metadata = entry.get("repository_release")
            if metadata:
                objects.append({**metadata, "active": held is not None, "display": name,
                                "station_revision": held, "available": self.deployment._latest(name)})
        return {"objects": objects}
