"""Shared, schema-checked online parameter capture and upload semantics."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ParameterChange:
    """One controller-to-project difference with an unambiguous block key."""

    module: str
    file_path: Path
    block_id: str
    instance_name: str
    block_type: str
    parameter: str
    file_value: object
    runtime_value: object
    category: str


_PID_TUNING = frozenset({
    "GAIN", "RESET", "RATE", "alpha", "beta", "gamma", "bias",
    "pv_ftime", "sp_ftime", "sp_rate_up", "sp_rate_dn", "ff_gain",
    "ideadband", "recovery_fltr", "nl_gap", "nl_hyst", "nl_tband",
    "nl_minmod",
})
_LIMIT_WORDS = ("lim", "min", "max", "cutoff", "hys")


def values_differ(left, right) -> bool:
    """Compare model values, tolerating insignificant float noise."""
    try:
        return abs(float(left) - float(right)) > 1e-6
    except (TypeError, ValueError):
        return str(left) != str(right)


def parameter_category(name: str) -> str:
    """Return the upload filter category for a canonical config key."""
    if name == "sp_init":
        return "sp"
    if name == "mode":
        return "mode"
    if name in _PID_TUNING:
        return "tuning"
    if any(word in name.lower() for word in _LIMIT_WORDS):
        return "limits"
    return "configuration"


def canonical_config_value(block, key, value):
    """Use the same PID enum identity for file names and live display labels."""
    if block.block_type == "PID" and key in {"form", "structure"}:
        normalized = block._normalize_enum(value)
        if key == "form":
            return {"ideal": "standard", "interacting": "series"}.get(normalized, normalized)
        from ...pid.core import Structure
        aliases = block._structure_map(Structure)
        member = aliases.get(normalized)
        if member is not None:
            return next(choice for choice in block.config_choices[key] if aliases[choice] == member)
    return value


def effective_configuration(block):
    """Missing serialized fields retain schema defaults, not an unknown value."""
    return {key: canonical_config_value(block, key, block.config.params.get(key, spec[1]))
            for key, spec in block.get_config_schema().items()}


def runtime_config_snapshot(block, store=None) -> dict:
    """Return configurable values as they exist in the running controller.

    Every block contributes its live ``BlockConfig``.  A block may expose a
    ``runtime_config_snapshot(store)`` hook when its algorithm owns mutable
    values elsewhere.  PID retains the established store overlay for online
    tuning, setpoint, and mode changes.
    """
    schema = set(block.get_config_schema())
    snapshot = effective_configuration(block)
    hook = getattr(block, "runtime_config_snapshot", None)
    if callable(hook):
        supplied = hook(store)
        if supplied:
            snapshot.update({key: value for key, value in supplied.items()
                             if key in schema})
    if store is not None and block.block_type == "PID":
        for key in schema:
            store_key = {"sp_init": "SP", "mode": "MODE", "alpha": "Alpha",
                         "beta": "Beta", "gamma": "Gamma"}.get(key, key)
            value = store.get(f"ctrl.{block.instance_name}.{store_key}")
            if value is not None:
                snapshot[key] = value
    return {key: canonical_config_value(block, key, value) for key, value in snapshot.items()}


def matching_block(graph, block_id: str, instance_name: str,
                   block_type: str):
    """Match by persistent UUID, then by the legacy name/type identity."""
    block = graph.blocks.get(block_id)
    if block is not None and block.block_type == block_type:
        return block
    return next((candidate for candidate in graph.blocks.values()
                 if candidate.instance_name == instance_name
                 and candidate.block_type == block_type), None)


def apply_parameter_changes(graph, changes: list[ParameterChange]) -> int:
    """Apply controller values to any compatible strategy graph.

    The file graph and an already-open canvas are separate objects.  Keeping
    this UUID/name/type matching and normalization in one routine prevents the
    two upload targets from acquiring subtly different parameter semantics.
    """
    updated = 0
    for change in changes:
        block = matching_block(
            graph, change.block_id, change.instance_name, change.block_type)
        if block is None or change.parameter not in block.get_config_schema():
            continue
        if values_differ(
                block.config.params.get(change.parameter),
                change.runtime_value):
            block.config.params[change.parameter] = change.runtime_value
            block.normalize_config()
            block._apply_config()
            updated += 1
    return updated


def save_parameter_changes(path: Path | str,
                           changes: list[ParameterChange]) -> int:
    """Apply selected changes through the graph serializer and versioner."""
    from azeo_control_trainer.core.strategy.serialization.strategy_io import (
        load_strategy, save_strategy,
    )
    graph, comments = load_strategy(path, remember=False)
    updated = apply_parameter_changes(graph, changes)
    if updated:
        save_strategy(graph, path=path, comments=comments)
    return updated
