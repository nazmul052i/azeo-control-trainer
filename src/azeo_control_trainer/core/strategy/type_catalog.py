"""The type catalog — every registered block type as data (HMI §7.4).

**This is the FHX equivalent**: the serializable artifact everything
downstream consumes — the OPC UA type projection builds ObjectTypes from
it, the PVM registry keys graphics off it, and the snapshot test makes
type drift visible in diffs instead of surfacing as a client that
silently stopped resolving a node.

Qt-free by contract (I2): importing this module — and calling
`build_catalog()` — must work with PySide6 uninstalled, which the smoke
suite enforces with an import blocker. Anything Qt has no business here;
the catalog describes types, it does not draw them.

Determinism matters as much as content: types, terminals and config
parameters are emitted in sorted order so two builds of the same code
are byte-identical and the snapshot diff shows only real drift.
"""
from __future__ import annotations

import json
from pathlib import Path

CATALOG_VERSION = 1


def _terminal_entry(terminal) -> dict:
    entry = {
        "name": terminal.name,
        "data_type": terminal.data_type.value,
        "default": terminal.default_value,
        "description": terminal.description,
    }
    # Absent-when-default (I1's habit applied to the emitted artifact):
    # a catalog line only carries what the terminal actually declares.
    if terminal.is_bkcal:
        entry["is_bkcal"] = True
    if terminal.units:
        entry["units"] = terminal.units
    if terminal.eu_range is not None:
        entry["eu_range"] = list(terminal.eu_range)
    return entry


def _config_entry(block_cls, block, name: str, spec) -> dict:
    try:
        kind, default, description = spec
    except Exception:                                   # noqa: BLE001
        kind, default, description = (type(spec), spec, "")
    entry = {
        "name": name,
        "type": getattr(kind, "__name__", str(kind)),
        "default": default if isinstance(
            default, (int, float, str, bool, type(None))) else str(default),
        "description": description,
    }
    unit = block_cls.unit_for(name)
    if unit:
        entry["unit"] = unit
    choices = block_cls.choices_for(name)
    if choices:
        entry["choices"] = list(choices)
    configured = block.config.params.get(name)
    if configured is not None and configured != default \
            and isinstance(configured, (int, float, str, bool)):
        entry["value"] = configured
    return entry


def describe_type(block_cls) -> dict:
    """One block type as a serializable definition.

    Instantiates the class with a throwaway name so the terminal set and
    any `_apply_config()`-stamped units/ranges reflect what a freshly
    placed block actually carries.
    """
    block = block_cls("__catalog__")
    try:
        block._apply_config()
    except Exception:                                   # noqa: BLE001
        pass  # a block that cannot apply an empty config still has pins

    schema = block.get_config_schema() or {}
    definition = {
        "block_type": block_cls.block_type,
        "category": block_cls.category.value
        if hasattr(block_cls.category, "value") else str(block_cls.category),
        "description": getattr(block_cls, "description", ""),
        "inputs": [_terminal_entry(t) for _, t
                   in sorted(block.inputs.items())],
        "outputs": [_terminal_entry(t) for _, t
                    in sorted(block.outputs.items())],
        "config": [_config_entry(block_cls, block, name, schema[name])
                   for name in sorted(schema)],
    }
    aliases = getattr(block_cls, "terminal_aliases", None)
    if aliases:
        definition["terminal_aliases"] = dict(sorted(aliases.items()))
    aliases = getattr(block_cls, "config_aliases", None)
    if aliases:
        definition["config_aliases"] = dict(sorted(aliases.items()))
    return definition


def build_catalog() -> dict:
    """Emit every registered block type as a serializable type definition."""
    # Importing the blocks package is what fills the registry; the model
    # layer alone registers nothing.
    from . import blocks  # noqa: F401
    from .model.block_registry import BlockRegistry

    registry = BlockRegistry()
    types = {}
    failures = {}
    for block_type in sorted(registry.all_types()):
        try:
            types[block_type] = describe_type(registry.get(block_type))
        except Exception as error:                      # noqa: BLE001
            failures[block_type] = f"{type(error).__name__}: {error}"
    catalog = {
        "catalog_version": CATALOG_VERSION,
        "type_count": len(types),
        "types": types,
    }
    if failures:
        # A type that cannot be described is a defect worth seeing in the
        # artifact, not a silent hole in the namespace.
        catalog["failures"] = failures
    return catalog


def catalog_json(catalog: dict | None = None) -> str:
    """The catalog as canonical JSON — the snapshot-test representation."""
    return json.dumps(catalog if catalog is not None else build_catalog(),
                      indent=1, ensure_ascii=False, sort_keys=True)


def save_catalog(path: str | Path, catalog: dict | None = None) -> Path:
    path = Path(path)
    path.write_text(catalog_json(catalog) + "\n", encoding="utf-8")
    return path
