"""Resolve reusable revisions once, then execute one bounded advisory graph.

The worker never reloads child files mid-run. Namespaced steps and memory keep
the host's scan evidence, timers, tuning and audit semantics for every child.
"""
import ast
from copy import deepcopy
import hashlib
from pathlib import Path

import yaml
from azeo_control_trainer.core.pa_designer.connectors.tag_mapping import TagMappingTable
from azeo_control_trainer.core.pa_designer.core.yaml_loader import load_bounded_yaml_file

from .flow import linear_flow


def _child_path(value, source, library):
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or "runtime" in relative.parts:
        raise ValueError("Subprocedure revisions must be relative paths inside the project library")
    base = source.parent if source and relative.parts and relative.parts[0] == "dependencies" else library
    path = (base / relative).resolve()
    if not path.is_relative_to(library.resolve()) or not path.is_file():
        raise ValueError(f"Subprocedure revision is missing or outside the library: {value}")
    return path


class _ExpressionNames(ast.NodeTransformer):
    def __init__(self, variables, tags):
        self.variables, self.tags = variables, tags

    def visit_Attribute(self, node):
        name = ast.unparse(node)
        if name in self.tags:
            return ast.copy_location(ast.parse(self.tags[name], mode="eval").body, node)
        return self.generic_visit(node)

    def visit_Name(self, node):
        if node.id in self.variables:
            return ast.copy_location(ast.Name(self.variables[node.id], ctx=ast.Load()), node)
        return node


def _expression(value, variables, tags):
    if not value:
        return value
    return ast.unparse(_ExpressionNames(variables, tags).visit(ast.parse(value, mode="eval")))


def build_definition(procedure, bindings, source=None, library=None):
    from .model import AdvisoryProcedure, ProcedureDefinition
    if not any(s.type == "subprocedure" for s in procedure.steps):
        return ProcedureDefinition(procedure, bindings).validate()
    if library is None:
        raise ValueError("Select reusable subprocedures from this project's saved library revisions")
    library = Path(library).resolve()
    documents, steps, nodes, edges, variables, tags, all_bindings = {}, [], [], [], {}, {}, {}
    shared = {}
    call_count = 0

    def expand(proc, mapping, path, prefix, ancestry, alias_tags=None, inherited_failure=None):
        nonlocal call_count
        call_count += 1
        if len(ancestry) > 8 or call_count > 64:
            raise ValueError("Subprocedure composition exceeds eight levels or 64 calls")
        ProcedureDefinition(proc, mapping).validate(allow_calls=True)
        data = proc.model_dump(mode="json")
        token = "call_" + hashlib.sha256(prefix.encode()).hexdigest()[:10] + "_" if prefix else ""
        var_map, tag_map = {}, {}
        for spec in proc.variables:
            name = token + spec.name if prefix else spec.name
            if spec.tag_path:
                if spec.tag_path in shared:
                    name = shared[spec.tag_path]
                    previous = variables[name]
                    if any(previous.get(key) != getattr(spec, key) for key in ("data_type", "min_value", "max_value", "operator_tuning")):
                        raise ValueError("Shared memory contracts differ across subprocedures")
                else:
                    shared[spec.tag_path] = name
            var_map[spec.name] = name
            variables.setdefault(name, dict(spec.model_dump(mode="json"), name=name,
                                            description=f"{prefix}{spec.description}" if prefix else spec.description))
        for spec in proc.tags:
            name = (alias_tags or {}).get(spec.tag, token + spec.tag if prefix else spec.tag)
            tag_map[spec.tag] = name
            if name not in tags:
                tags[name] = dict(spec.model_dump(mode="json"), tag=name)
                all_bindings[name] = mapping[spec.tag]
            elif tags[name]["data_type"] != spec.data_type:
                raise ValueError(f"Subprocedure tag alias has a different type: {spec.tag}")
        graph = deepcopy(data.get("flow") or linear_flow(data["steps"]))
        node_map = {n["id"]: prefix + n["id"] for n in graph["nodes"]}
        start = next(n for n in graph["nodes"] if n["kind"] == "start")
        ends = [n for n in graph["nodes"] if n["kind"] == "end"]
        for node in graph["nodes"]:
            original = node["id"]
            node["id"] = node_map[original]
            if node.get("step_id"):
                node["step_id"] = prefix + node["step_id"]
            if prefix and node["kind"] in {"start", "end"}:
                node["kind"] = "merge"
            node["condition"] = _expression(node.get("condition"), var_map, tag_map)
        for edge in graph["edges"]:
            edge["source"], edge["target"] = node_map[edge["source"]], node_map[edge["target"]]
            edge["condition"] = _expression(edge.get("condition"), var_map, tag_map)
        nodes.extend(graph["nodes"])
        edges.extend(graph["edges"])
        for raw in data["steps"]:
            step = deepcopy(raw)
            step["id"] = prefix + raw["id"]
            step["unit_procedure"] = prefix.rstrip("/") or step.get("unit_procedure", "")
            for key in ("condition", "expression", "skip_if"):
                step[key] = _expression(step.get(key), var_map, tag_map)
            for key in ("tag", "equipment"):
                if step.get(key) in tag_map:
                    step[key] = tag_map[step[key]]
            if step.get("variable"):
                step["variable"] = var_map[step["variable"]]
            for row in step.get("condition_rows", []):
                row["expression"] = _expression(row["expression"], var_map, tag_map)
            for row in step.get("calculation_rows", []):
                row["expression"] = _expression(row["expression"], var_map, tag_map)
                row["variable"] = var_map[row["variable"]]
            if step["type"] != "subprocedure":
                if prefix and step["type"] == "complete":
                    step.update(type="user_event", event_name="Subprocedure verified",
                                library_block_id="", library_block_version="")
                steps.append(step)
                continue
            child_path = _child_path(raw["subprocedure_path"], path, library)
            if child_path in ancestry:
                raise ValueError("Recursive subprocedure calls are not allowed")
            child = AdvisoryProcedure.model_validate(load_bounded_yaml_file(child_path, label="Subprocedure"))
            mapping_path = (child_path.parent / child.connectivity.mapping_path).resolve()
            if not child.connectivity.mapping_path or not mapping_path.is_relative_to(library):
                raise ValueError("Subprocedure tag mapping must remain inside the project library")
            child_bindings = {r.logical_tag: r.connector_tag for r in TagMappingTable.from_yaml(mapping_path).rows}
            alias = {}
            for key, target in raw.get("tag_aliases", {}).items():
                if key not in child_bindings or target not in tag_map:
                    raise ValueError("Subprocedure tag aliases must name declared child and parent tags")
                alias[key] = tag_map[target]
                child_bindings[key] = mapping[target]
            documents[child_path.relative_to(library).as_posix()] = {
                "procedure": child.model_dump(mode="json"), "bindings": child_bindings}
            call_node = next(n for n in graph["nodes"] if n.get("step_id") == step["id"])
            exits = [e for e in list(edges) if e["source"] == call_node["id"]]
            normal = next((e for e in exits if e.get("outcome", "always") in {"always", "passed"}), None)
            if normal is None:
                raise ValueError(f"Subprocedure {step['id']} needs a success connection")
            failure = {e.get("outcome"): e["target"] for e in exits if e.get("outcome") in {"failed", "timeout"}}
            child_prefix = step["id"] + "/"
            for outcome, policy in (("failed", step["on_failure"]), ("timeout", step["on_timeout"])):
                if policy in {"hold", "abort"}:
                    target = child_prefix + "$" + outcome
                    failure[outcome] = target
                    nodes.append({"id": target, "kind": "action", "step_id": target})
                    steps.append({"id": target, "type": policy, "description": f"{child.name}: {outcome}; operator review required"})
                    edges.append({"source": target, "target": normal["target"]})
            child_start, child_ends, child_vars = expand(child, child_bindings, child_path, child_prefix,
                ancestry | {child_path}, alias, failure or inherited_failure)
            edges[:] = [e for e in edges if e not in exits]
            parameters = raw.get("parameters", {})
            if set(parameters) - set(child_vars):
                raise ValueError("Subprocedure parameters must name declared child memory")
            initializers = []
            for spec in child.variables:
                if spec.tag_path:
                    if spec.name in parameters:
                        raise ValueError("Call parameters cannot initialize shared memory; use a local input variable")
                    continue
                value = parameters.get(spec.name, spec.value)
                if isinstance(value, dict) and set(value) == {"expression"}:
                    expr = _expression(value["expression"], var_map, tag_map)
                else:
                    expr = repr(spec.checked(value))
                initializers.append({"variable": child_vars[spec.name], "expression": expr})
            # Reinitialization belongs to the call boundary, including retries.
            entry = {"id": step["id"], "type": "calculate" if initializers else "user_event",
                     "description": "Enter " + child.name, "unit_procedure": prefix.rstrip("/"),
                     "event_name": "SUBPROCEDURE_ENTER", "event_payload": {"scope": child_prefix}, "calculation_rows": initializers,
                     "on_failure": step["on_failure"]}
            if initializers:
                entry.update(initializers[0])
            steps.append(entry)
            edges.append({"source": call_node["id"], "target": child_start})
            return_rows = []
            for child_name, parent_name in raw.get("result_variables", {}).items():
                if child_name not in child_vars or parent_name not in var_map:
                    raise ValueError("Subprocedure results must map declared child memory to declared parent memory")
                return_rows.append({"variable": var_map[parent_name], "expression": child_vars[child_name]})
            exit_id = child_prefix + "$return"
            nodes.append({"id": exit_id, "kind": "action" if return_rows else "merge",
                          **({"step_id": exit_id} if return_rows else {})})
            if return_rows:
                steps.append({"id": exit_id, "type": "calculate", "description": "Return " + child.name,
                              "unit_procedure": prefix.rstrip("/"), "calculation_rows": return_rows, **return_rows[0]})
            edges.extend({"source": end, "target": exit_id} for end in child_ends)
            edges.append({"source": exit_id, "target": normal["target"]})
            if raw.get("skip_if"):
                gate = child_prefix + "$already_satisfied"
                nodes.append({"id": gate, "kind": "choice", "label": "Already satisfied?"})
                for edge in edges:
                    if edge["target"] == call_node["id"]:
                        edge["target"] = gate
                edges.extend([{"source": gate, "target": normal["target"], "condition": step["skip_if"]},
                              {"source": gate, "target": call_node["id"], "is_default": True}])
        if inherited_failure:
            for node in graph["nodes"]:
                if node["kind"] != "action":
                    continue
                outgoing = {e.get("outcome", "always") for e in edges if e["source"] == node["id"]}
                for outcome, target in inherited_failure.items():
                    if outcome not in outgoing:
                        edges.append({"source": node["id"], "target": target, "outcome": outcome})
        if len(nodes) > 1000 or len(variables) > 512:
            raise ValueError("Expanded procedure exceeds 1000 workflow nodes or 512 memory variables")
        return node_map[start["id"]] if start["id"] in node_map else start["id"], [n["id"] for n in ends], var_map

    expand(procedure, bindings, Path(source) if source else None, "", {Path(source).resolve()} if source else set())
    data = procedure.model_dump(mode="json")
    data.update(steps=steps, variables=list(variables.values()), tags=list(tags.values()),
                flow={"nodes": nodes, "edges": edges, "visit_limit": (procedure.flow.visit_limit if procedure.flow else 10000)})
    authored = {"procedure": procedure.model_dump(mode="json"), "bindings": bindings, "dependencies": documents}
    return ProcedureDefinition(AdvisoryProcedure.model_validate(data), all_bindings, authored).validate()


def bundle_dependencies(data, source, library, destination, *, ancestry=frozenset()):
    """Copy only resolved procedure/mapping documents, never whole directories."""
    if len(ancestry) > 8:
        raise ValueError("Subprocedure nesting exceeds eight levels")
    for index, step in enumerate(data["steps"]):
        if step["type"] != "subprocedure":
            continue
        path = _child_path(step["subprocedure_path"], source, library)
        if path in ancestry:
            raise ValueError("Recursive subprocedure calls are not allowed")
        child = load_bounded_yaml_file(path, label="Subprocedure")
        target = destination / "dependencies" / f"call-{index}"
        target.mkdir(parents=True)
        from .authoring import ProcedureDraft
        draft = ProcedureDraft.load(path, library)
        draft._copy_references(child, target, library)
        bundle_dependencies(child, path, library, target, ancestry=ancestry | {path})
        child.setdefault("connectivity", {})["mapping_path"] = "mapping.yaml"
        (target / "mapping.yaml").write_text(yaml.safe_dump({"mappings": [
            {"logical_tag": tag, "connector_tag": binding} for tag, binding in draft.bindings.items()]}, sort_keys=False), encoding="utf-8")
        (target / "procedure.yaml").write_text(yaml.safe_dump(child, sort_keys=False), encoding="utf-8")
        step["subprocedure_path"] = (target / "procedure.yaml").relative_to(destination).as_posix()
