"""Assembly templates and saved commissioning cases for the PvmDisplay stack."""
from __future__ import annotations

import copy
import hashlib
import json
import re

from .instances import TemplateStore
from .publishing import PvmDisplay


_PATH = re.compile(r"^[A-Za-z_][\w.-]*/[A-Za-z_][\w.-]*(?:/.*)?$")
_FIELDS = {"path", "paths", "path_prefix", "target", "expression", "source", "series_path"}
_QUOTED = re.compile(r"(['\"])([^'\"]+)\1")


def _installed_class(data):
    from .base import registry
    block_type, _, role = str(data.get("class", "")).partition("/")
    return registry.get(block_type, role, data.get("variant", ""))


def _parameter_types(data):
    cls = _installed_class(data)
    if cls is None:
        return {}
    return {name: getattr(cls, "PARAM_TYPES", {}).get(name, cls.block_type)
            for name in cls.PARAMS}


def _binding_tree(value, transform, binding=False):
    if isinstance(value, dict):
        expression = value.get("kind") == "expression"
        parameters = _parameter_types(value) if "class" in value else {}
        result = {}
        for key, child in value.items():
            if key == "params" and isinstance(child, dict) and parameters:
                result[key] = {name: _binding_tree(content, transform, name in parameters or name in _FIELDS)
                               for name, content in child.items()}
            else:
                result[key] = _binding_tree(child, transform, binding or key in _FIELDS
                                           or (expression and key in {"expr", "text", "refs"}))
        return result
    if isinstance(value, list):
        return [_binding_tree(child, transform, binding) for child in value]
    if binding and isinstance(value, str):
        if _PATH.fullmatch(value):
            return transform(value)
        # Keep quotes, arithmetic and DLSYS syntax intact; only remap paths.
        return _QUOTED.sub(lambda m: m[1] + transform(m[2]) + m[1]
                           if _PATH.fullmatch(m[2]) else m[0], value)
    return copy.deepcopy(value)


def control_roots(document):
    result = set()

    def collect(value):
        result.add("/".join(value.split("/")[:2]))
        return value
    _binding_tree(document, collect)
    return sorted(result)


def control_requirements(document):
    """Declared PVM interfaces, including a machine's independent run sequencer."""
    requirements = {root: set() for root in control_roots(document)}
    for pvm in document.get("pvms", []):
        for name, family in _parameter_types(pvm).items():
            path = str(pvm.get("params", {}).get(name, ""))
            if path in requirements:
                requirements[path].add(family)
    return requirements


def mapping_issues(document, mapping, graphs):
    """Return row-specific errors without letting one missing tag hide the others."""
    from ..binding.source import LiveGraphSource
    from ..binding.result import UNRESOLVED
    source = LiveGraphSource(lambda: graphs)
    requirements = control_requirements(document)
    blocks = {f"{module}/{block.instance_name}": block.block_type
              for module, graph in graphs.items() for block in graph.blocks.values()}
    blocks.update({f"{module}/PARAMETERS": "PARAMETERS" for module, graph in graphs.items()
                   if graph.module_parameters()})
    errors = {}
    for root, families in requirements.items():
        target = mapping.get(root, root)
        if target not in blocks:
            errors[root] = f"{root} → {target or '(empty)'}: select an existing control block"
        elif families and families != {blocks[target]}:
            errors[root] = f"{target}: requires {' + '.join(sorted(families))}, got {blocks[target]}"

    def resolve(path):
        root, suffix = "/".join(path.split("/")[:2]), path.split("/")[2:]
        if root not in errors and suffix:
            target = mapping.get(root, root) + "/" + "/".join(suffix)
            if source.read(target) is UNRESOLVED:
                errors[root] = f"{target}: target parameter does not resolve"
        return path
    _binding_tree(document, resolve)
    return errors


def remap_controls(document, mapping, graphs):
    """Resolve every replacement before changing any data; never touch IDs."""
    roots = control_roots(document)
    for pvm in document.get("pvms", []):
        cls = _installed_class(pvm)
        for name in _parameter_types(pvm):
            path = str(pvm.get("params", {}).get(name, "")).strip()
            label = getattr(cls, "PARAM_LABELS", {}).get(name, name)
            if not path:
                raise ValueError(f"{pvm.get('id', 'PVM')}: {label} is required")
            if len(path.split("/")) != 2 or not _PATH.fullmatch(path):
                raise ValueError(f"{pvm.get('id', 'PVM')}: {label} requires MODULE/BLOCK")
    errors = mapping_issues(document, mapping, graphs)
    if errors:
        raise ValueError("; ".join(errors.values()))

    def replace(value):
        parts = value.split("/")
        root = "/".join(parts[:2])
        return mapping.get(root, root) + ("/" + "/".join(parts[2:]) if len(parts) > 2 else "")
    result = _binding_tree(document, replace)
    def choices(node, inside=False, transformed=()):
        if isinstance(node, dict):
            return {key: value if key in _FIELDS or key in transformed else choices(
                value, inside or key in {"params", "choices", "pvm_choices", "instance_choices"},
                _parameter_types(node) if key == "params" else ()) for key, value in node.items()}
        if isinstance(node, list):
            return [choices(value, inside) for value in node]
        if inside and isinstance(node, str) and _PATH.fullmatch(node) and "/".join(node.split("/")[:2]) in roots:
            return replace(node)
        return node
    # Re-expanding a linked class reads its instance choices. Leaving those
    # on the old control tag would undo an otherwise correct canvas mapping.
    result = choices(result)
    from ..binding.source import LiveGraphSource
    from ..binding.result import UNRESOLVED
    source = LiveGraphSource(lambda: graphs)

    def resolve(path):
        if len(path.split("/")) >= 3 and source.read(path) is UNRESOLVED:
            raise ValueError(f"{path}: target parameter does not resolve")
        return path
    _binding_tree(result, resolve)
    return result


def assembly_document(name, payload):
    records = payload.get("records", [])
    if not records:
        raise ValueError("Select the objects to save as an assembly")
    groups = "assembly"
    pvms, items = [], []
    order = []
    for record in records:
        data = copy.deepcopy(record["data"])
        data["group"] = groups
        (pvms if record["type"] == "pvm" else items).append(data)
        order.append(data.get("id", ""))
    return PvmDisplay(name=name, pvms=pvms, items=items, stacking_order=order).to_dict()


def assembly_payload(document):
    from .rendering.renderer import DisplayRenderer
    records = []
    for is_pvm, data in DisplayRenderer.ordered_content(PvmDisplay.from_dict(document)):
        records.append({"type": "pvm" if is_pvm else "pipe" if data.get("kind") == "pipe" else "item",
                        "data": copy.deepcopy(data)})
    return {"schema": 1, "records": records}


def save_assembly(root, name, payload):
    name = name.strip()
    if not name:
        raise ValueError("Enter an assembly name")
    store = TemplateStore(root)
    key = "Assembly · " + name
    if key in store.entries:
        raise ValueError("An assembly with that name already exists")
    store.add(key, "display", assembly_document(key, payload))
    return key


def starter_assemblies():
    """Complete reusable arrangements built from the installed PVM classes."""
    from .control import PIDCompact
    from .device import DeviceSymbol
    from .process import VesselTrendPvm, ValveOpPvm
    from .process import PumpStatusPvm
    from .machines import VFDSpeedPvm, CompressorSpeedPvm, TurbineSpeedPvm
    from .rendering.chrome import ROLE_SIZES, PVM_W, PVM_H

    def pvm(cls, identity, path, x, y):
        width, height = cls.DEFAULT_SIZE or ROLE_SIZES.get(cls.role, (PVM_W, PVM_H))
        data = cls().place(identity, x=x, y=y, w=width, h=height, path=path).to_dict()
        data["group"] = "assembly"
        return data

    def drawing(kind, identity, **values):
        return {"kind": kind, "id": identity, "group": "assembly", **values}

    loop = [pvm(PIDCompact, "controller", "LOOP/PID", 140, 50),
            pvm(ValveOpPvm, "valve", "VALVE/AO", 140, 160)]
    vessel = [pvm(VesselTrendPvm, "vessel", "LEVEL/AI", 10, 55),
              pvm(ValveOpPvm, "valve", "VALVE/AO", 250, 160)]
    pump = [pvm(DeviceSymbol, "pump", "PUMP/DEV", 60, 70),
            pvm(PIDCompact, "flow", "FLOW/PID", 220, 25)]
    result = {}
    for name, pvms in (("Control loop", loop), ("Vessel and outlet", vessel), ("Pump and flow loop", pump)):
        items = [drawing("text", "title", x=10, y=0, w=300, h=20, text=name)]
        if name == "Vessel and outlet":
            items.append(drawing("pipe", "outlet", a="vessel", b="valve", a_side="e", b_side="w",
                                 route_mode="auto"))
        result[name] = PvmDisplay(name=name, pvms=pvms, items=items).to_dict()
    for name, cls in (("Pump and VFD", VFDSpeedPvm),
                      ("Compressor and speed control", CompressorSpeedPvm),
                      ("Turbine and speed control", TurbineSpeedPvm)):
        width, height = cls.DEFAULT_SIZE
        machine = cls().place("speed", x=180, y=50, w=width, h=height,
                              path="SPEED/PID", device="RUN/DEV").to_dict()
        machine["group"] = "assembly"
        pvms = [machine]
        if cls is VFDSpeedPvm:
            pvms.insert(0, pvm(PumpStatusPvm, "pump", "RUN/DEV", 30, 75))
        result[name] = PvmDisplay(name=name, level=2, pvms=pvms, items=[
            drawing("text", "title", x=10, y=0, w=370, h=22, text=name),
        ]).to_dict()
    return result


def default_cases(path):
    base = path.rsplit("/", 1)[0]
    cases = []
    for name, overrides, expected, target in (
        ("Normal", {"value": 50, "quality": "GOOD"}, {"quality": "GOOD", "value": 50}, path),
        ("Alarm", {"value": 90, "quality": "GOOD", "alarm_active": True, "alarm_priority": 15,
                   "alarm_condition": "HI_HI"}, {"alarm_active": True}, path),
        ("Bad quality", {"value": None, "quality": "BAD"}, {"quality": "BAD"}, path),
        ("Manual mode", {"quality": "GOOD", "mode_target": "MAN", "mode_actual": "MAN"}, {"mode_actual": "MAN"}, path),
        ("Interlocked", {"value": False, "quality": "GOOD"}, {"value": False}, base + "/INTERLOCK"),
        ("Communication loss", {"unresolved": True}, {"unresolved": True}, path),
    ):
        cases.append({"name": name, "path": target, "override": overrides, "expected": expected,
                      "visual_expectation": "", "review_note": "", "reviewed": False})
    return cases


def check_case(source, case):
    """Check the actual resolved preview result, without accepting invented tags."""
    from ..binding.result import UNRESOLVED
    path = case["path"]
    original = source.source.read(path)
    if original is UNRESOLVED or original.module_running is False:
        return False, f"Unresolved source: {path}. Choose the actual parameter for this case."
    actual = source.read(path)
    failures = []
    for field, expected in case["expected"].items():
        value = actual is UNRESOLVED if field == "unresolved" else getattr(actual, field, None)
        value = getattr(value, "name", value)
        if value != expected:
            failures.append(f"{field}: expected {expected!r}, observed {value!r}")
    return not failures, "; ".join(failures) or "Expected binding state observed"


def document_digest(document):
    data = copy.deepcopy(document)
    data.pop("commissioning", None)
    data.pop("test_sequences", None)
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def commissioning_progress(document):
    """Evidence belongs to the inspected draft, never just a display name."""
    digest = document_digest(document)
    cases = document.get("commissioning", [])
    checked = sum(c.get("check_digest") == digest and c.get("check_passed") is True for c in cases)
    reviewed = sum(bool(c.get("reviewed") and c.get("review_digest") == digest
                        and c.get("review_note", "").strip() and c.get("visual_expectation", "").strip()) for c in cases)
    complete = sum(c.get("check_digest") == digest and c.get("check_passed") is True
                   and bool(c.get("reviewed") and c.get("review_digest") == digest
                            and c.get("review_note", "").strip() and c.get("visual_expectation", "").strip()) for c in cases)
    return {"total": len(cases), "checked": checked, "reviewed": reviewed, "remaining": len(cases) - complete}


def applicable_cases(path, source):
    """Offer states this binding can represent, and preserve its actual type."""
    from ..binding.result import UNRESOLVED
    actual = source.read(path)
    if actual is UNRESOLVED:
        raise ValueError(f"Unresolved source: {path}")
    cases = []
    for case in default_cases(path):
        name = case["name"]
        if name == "Manual mode" and not actual.mode_actual:
            continue
        if name == "Interlocked" and source.read(case["path"]) is UNRESOLVED:
            continue
        if name == "Alarm" and (not isinstance(actual.value, (int, float)) or isinstance(actual.value, bool)):
            continue
        if name == "Normal":
            value = actual.value
            if not isinstance(value, (int, float, bool)) or value is None:
                continue
            case["override"]["value"] = value
            case["expected"]["value"] = value
        cases.append(case)
    return cases
