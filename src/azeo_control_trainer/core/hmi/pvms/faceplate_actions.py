"""Resolve faceplate navigation targets without inventing associations.

Azeo function-block faceplates carry a single button that opens the
containing module's faceplate.  A block type alone cannot answer that
question: an AT block may belong to a loop module, while the same type in an
otherwise unrelated strategy has no module faceplate at all.  These helpers
therefore inspect the live module graph and return a target only when exactly
one compatible block exists.  Ambiguity is deliberately represented by
``None`` so the host can remove the icon and its hotspot.
"""
from __future__ import annotations

from .base import Pvm, registry
from .faceplate_style import PROFILES


# These are the module shells implemented by this product.  The ``device``
# family is also the best available module shell for a DCC function-block
# faceplate; if a richer device-module class is added later it belongs here,
# rather than in a painter or a station-specific heuristic.
_MODULE_FAMILIES = frozenset({
    "analog", "analog_output", "loop", "device",
})


def _single_source_path(pvm: Pvm) -> tuple[str, str] | None:
    """Return ``(module, block)`` only for a true single-block placement."""
    params = dict(getattr(pvm, "params", {}) or {})
    if set(params) != {"path"}:
        return None
    parts = str(params["path"] or "").strip("/").split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def _module_graph(engine, module: str):
    source = getattr(engine, "_source", None)
    provider = getattr(source, "_graphs", None)
    if not callable(provider):
        return None
    try:
        graphs = provider()
    except Exception:  # noqa: BLE001 - a disappearing runtime is not a fault
        return None
    return (graphs or {}).get(module)


def _target_for_block(module: str, block) -> Pvm | None:
    block_type = str(getattr(block, "block_type", "") or "")
    block_name = str(getattr(block, "instance_name", "") or "")
    pvm_class = registry.get(block_type, "faceplate")
    if pvm_class is None or not block_name \
            or tuple(getattr(pvm_class, "PARAMS", ())) != ("path",):
        return None
    return pvm_class().place(
        f"faceplate-action:{module}/{block_name}",
        path=f"{module}/{block_name}",
    )


def _unique_target(pvm: Pvm, engine, predicate) -> Pvm | None:
    source = _single_source_path(pvm)
    if source is None:
        return None
    module, source_block = source
    graph = _module_graph(engine, module)
    if graph is None:
        return None
    targets: list[Pvm] = []
    for block in getattr(graph, "blocks", {}).values():
        block_name = str(getattr(block, "instance_name", "") or "")
        if block_name == source_block or not predicate(block):
            continue
        target = _target_for_block(module, block)
        if target is not None:
            targets.append(target)
    return targets[0] if len(targets) == 1 else None


def resolve_module_faceplate(pvm: Pvm, engine) -> Pvm | None:
    """Resolve the one real module-faceplate target for an FB faceplate.

    Multiple plausible module shells are not ranked.  Without authored
    module metadata, choosing one would make a live icon lie about where it
    goes; hiding the action is the only deterministic answer.
    """
    def is_module_shell(block) -> bool:
        block_type = str(getattr(block, "block_type", "") or "")
        pvm_class = registry.get(block_type, "faceplate")
        profile = PROFILES.get(getattr(pvm_class, "__name__", ""))
        return profile is not None and profile.family in _MODULE_FAMILIES

    return _unique_target(pvm, engine, is_module_shell)


def resolve_associated_dcc(pvm: Pvm, engine) -> Pvm | None:
    """Resolve an unambiguous DCC/condition faceplate in the same module."""
    def is_condition_faceplate(block) -> bool:
        block_type = str(getattr(block, "block_type", "") or "")
        pvm_class = registry.get(block_type, "faceplate")
        profile = PROFILES.get(getattr(pvm_class, "__name__", ""))
        return profile is not None and profile.family == "conditions"

    return _unique_target(pvm, engine, is_condition_faceplate)


__all__ = ["resolve_associated_dcc", "resolve_module_faceplate"]
