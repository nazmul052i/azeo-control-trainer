from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from yaml.events import (
    AliasEvent,
    MappingEndEvent,
    MappingStartEvent,
    SequenceEndEvent,
    SequenceStartEvent,
)


MAX_YAML_BYTES = 4 * 1024 * 1024
MAX_YAML_ALIASES = 100
MAX_YAML_DEPTH = 100
MAX_YAML_VALUES = 100_000


class BoundedSafeLoader(yaml.SafeLoader):
    """SafeLoader with resource limits for controlled and editor YAML."""

    def __init__(self, stream: str):
        super().__init__(stream)
        self._alias_count = 0
        self._compose_depth = 0

    def compose_node(self, parent: Any, index: Any):
        if self.check_event(AliasEvent):
            self._alias_count += 1
            if self._alias_count > MAX_YAML_ALIASES:
                raise ValueError(f"YAML exceeds {MAX_YAML_ALIASES} aliases")
        self._compose_depth += 1
        try:
            if self._compose_depth > MAX_YAML_DEPTH:
                raise ValueError(f"YAML nesting exceeds {MAX_YAML_DEPTH} levels")
            return super().compose_node(parent, index)
        finally:
            self._compose_depth -= 1


# LibYAML's safe loader is typically two orders of magnitude faster for the
# procedure and block catalogs.  The C parser does not expose ``compose_node``
# for the limits above, so scan its event stream first and retain the value-
# graph validation below.  Falling back to SafeLoader keeps source installs
# without LibYAML functional and subject to the same checks.
_SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def _validate_yaml_events(text: str) -> None:
    aliases = 0
    depth = 0
    events = yaml.parse(text, Loader=_SAFE_LOADER)
    try:
        for event in events:
            if isinstance(event, AliasEvent):
                aliases += 1
                if aliases > MAX_YAML_ALIASES:
                    raise ValueError(
                        f"YAML exceeds {MAX_YAML_ALIASES} aliases")
            elif isinstance(event, (MappingStartEvent, SequenceStartEvent)):
                depth += 1
                if depth > MAX_YAML_DEPTH:
                    raise ValueError(
                        f"YAML nesting exceeds {MAX_YAML_DEPTH} levels")
            elif isinstance(event, (MappingEndEvent, SequenceEndEvent)):
                depth -= 1
    finally:
        events.close()


def _validate_value_graph(value: Any) -> None:
    count = 0
    active: set[int] = set()

    def visit(current: Any, depth: int) -> None:
        nonlocal count
        count += 1
        if count > MAX_YAML_VALUES:
            raise ValueError(f"YAML expands beyond {MAX_YAML_VALUES} values")
        if depth > MAX_YAML_DEPTH:
            raise ValueError(f"YAML data nesting exceeds {MAX_YAML_DEPTH} levels")
        if not isinstance(current, (dict, list, tuple, set)):
            return
        identity = id(current)
        if identity in active:
            raise ValueError("Recursive YAML aliases are not allowed")
        active.add(identity)
        try:
            if isinstance(current, dict):
                for key, item in current.items():
                    visit(key, depth + 1)
                    visit(item, depth + 1)
            else:
                for item in current:
                    visit(item, depth + 1)
        finally:
            active.remove(identity)

    visit(value, 0)


def load_bounded_yaml(text: str, *, label: str = "YAML", max_bytes: int = MAX_YAML_BYTES) -> Any:
    if len(text.encode("utf-8")) > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes} bytes")
    _validate_yaml_events(text)
    value = yaml.load(text, Loader=_SAFE_LOADER)
    _validate_value_graph(value)
    return value


def load_bounded_yaml_file(
    path: str | Path, *, label: str = "YAML", max_bytes: int = MAX_YAML_BYTES
) -> Any:
    source = Path(path)
    if source.stat().st_size > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes} bytes: {source}")
    return load_bounded_yaml(
        source.read_text(encoding="utf-8"), label=f"{label} {source}", max_bytes=max_bytes
    )
