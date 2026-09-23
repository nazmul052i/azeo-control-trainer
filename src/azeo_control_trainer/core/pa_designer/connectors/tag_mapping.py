from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.yaml_loader import load_bounded_yaml_file


@dataclass(frozen=True)
class TagMapping:
    logical_tag: str
    connector_tag: str
    description: str = ""


class TagMappingTable:
    """Maps procedure logical tags to connector-specific addresses/node IDs."""

    def __init__(self, mappings: dict[str, TagMapping] | None = None):
        self._mappings = mappings or {}

    @classmethod
    def from_yaml(cls, path: str | Path) -> "TagMappingTable":
        data = load_bounded_yaml_file(path, label="Tag mapping YAML") or {}
        if isinstance(data, dict):
            if set(data) != {"mappings"}:
                raise ValueError("Tag mapping root must contain only a mappings list")
            rows = data["mappings"]
        elif isinstance(data, list):
            rows = data
        else:
            raise ValueError("Tag mapping YAML root must be a mapping or list")
        if not isinstance(rows, list):
            raise ValueError("Tag mapping mappings must be a list")
        mappings: dict[str, TagMapping] = {}
        for row in rows:
            if not isinstance(row, dict) or not set(row) <= {"logical_tag", "connector_tag", "description"}:
                raise ValueError("Every tag mapping must contain only logical_tag, connector_tag, and description")
            item = TagMapping(
                logical_tag=str(row["logical_tag"]).strip(),
                connector_tag=str(row["connector_tag"]).strip(),
                description=str(row.get("description", "")),
            )
            if not item.logical_tag or not item.connector_tag:
                raise ValueError("Tag mapping logical_tag and connector_tag must not be blank")
            if item.logical_tag in mappings:
                raise ValueError(f"Duplicate logical tag mapping: {item.logical_tag}")
            mappings[item.logical_tag] = item
        return cls(mappings)

    @property
    def rows(self) -> list[TagMapping]:
        return list(self._mappings.values())

    def resolve(self, logical_tag: str) -> str:
        item = self._mappings.get(logical_tag)
        return item.connector_tag if item else logical_tag

    def as_dict(self) -> dict[str, Any]:
        return {k: v.__dict__ for k, v in self._mappings.items()}
