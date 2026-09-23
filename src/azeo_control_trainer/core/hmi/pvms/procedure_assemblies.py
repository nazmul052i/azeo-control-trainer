"""Typed procedure revision mappings for the existing authored PVM instances."""
from __future__ import annotations

import copy
from pathlib import Path

from ...procedures.hmi import PREFIX, reference, split_path
from ...procedures.model import load_definition

_BINDINGS = {"path", "source", "rows_path", "series_path", "paths", "expression", "refs"}
_CHOICES = {"choices", "pvm_choices", "instance_choices"}


def _cached_config(getter):
    cache = {}
    def read(name):
        if name not in cache:
            cache[name] = getter(name) if getter else None
        return cache[name]
    return read


def procedure_library(display_root):
    """The supported project layout, plus a direct display store used by clients."""
    root = Path(display_root).resolve()
    project = root.parent.parent if root.name == "pvm" and root.parent.name == "displays" else root.parent
    return project / "procedures"


def _walk(node, transform, config_getter=None, binding=False, fields=frozenset({"ProcedureRef"}), choices=False):
    if isinstance(node, dict):
        name = node.get("user_pvm") or node.get("instance_class")
        if name and config_getter:
            config = config_getter(name)
            if config:
                fields = frozenset(p.name for p in config.public_properties() if p.ptype == "Procedure Reference")
        result = {}
        for key, value in node.items():
            if choices and key in fields and isinstance(value, str):
                result[key] = transform(value, True)
            else:
                result[key] = _walk(value, transform, config_getter, binding or key in _BINDINGS, fields, key in _CHOICES)
        return result
    if isinstance(node, list):
        return [_walk(value, transform, config_getter, binding, fields, choices) for value in node]
    if binding and isinstance(node, str) and node.startswith(PREFIX):
        return transform(node, False)
    return copy.deepcopy(node)


def procedure_references(document, config_getter=None):
    refs = set()
    def collect(value, choice):
        refs.add(value if choice else split_path(value)[0])
        return value
    _walk(document, collect, _cached_config(config_getter))
    return sorted(refs)


def iter_revision_issues(document, mapping, library, config_getter=None):
    try:
        refs = procedure_references(document, config_getter)
    except ValueError as error:
        yield "ProcedureRef", str(error)
        return
    for ref in refs:
        try:
            target = reference(mapping.get(ref, ref))
            load_definition(Path(library) / target, Path(library))
            yield ref, ""
        except (ValueError, OSError) as error:
            yield ref, f"Procedure revision {mapping.get(ref, ref) or '(empty)'}: {error}"


def revision_issues(document, mapping, library, config_getter=None):
    return {ref: error for ref, error in iter_revision_issues(document, mapping, library, config_getter) if error}


def remap_procedures(document, mapping, library, config_getter=None):
    errors = revision_issues(document, mapping, library, config_getter)
    if errors:
        raise ValueError("; ".join(errors.values()))
    def replace(value, choice):
        if choice:
            return mapping.get(value, value)
        ref, field = split_path(value)
        return PREFIX + mapping.get(ref, ref) + "/" + field
    return _walk(document, replace, _cached_config(config_getter))


def procedure_catalog(library):
    from ...procedures.authoring import library_documents
    entries, errors = {}, []
    for path in library_documents(Path(library)):
        # mapping.yaml and unrelated YAML must not be offered as revisions.
        if path.name == "mapping.yaml":
            continue
        try:
            definition = load_definition(path, Path(library))
            entries[path.relative_to(library).as_posix()] = definition.procedure.name
        except (ValueError, OSError) as error:
            errors.append(f"{path.relative_to(library)}: {error}")
    return entries, errors


def pa_assemblies(library, config_getter):
    result = {}
    for name in library.names(definition_kind="pvm"):
        config = config_getter(name)
        if config is None:
            continue
        refs = [p for p in config.public_properties() if p.ptype == "Procedure Reference"]
        if not refs:
            continue
        choices = {p.name: p.default for p in config.public_properties()}
        choices.update({p.name: f"select/{p.name}/procedure.yaml" for p in refs})
        result[name] = library.instantiate(name, 20, 30, config=config, choices=choices)
    return result
