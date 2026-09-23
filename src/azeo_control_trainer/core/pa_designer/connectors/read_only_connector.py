from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Iterable

from .tag_value import TagValue, GOOD_QUALITY, BAD_QUALITY


class ReadOnlyTagConnector(ABC):
    """Online connector contract for read-only tag access.

    Phase 2A intentionally has no live write method. Write-like procedure steps
    are handled as advisory proposals by AdvisoryTagProvider.
    """

    name: str = "read_only"

    @abstractmethod
    def read_value(self, tag: str) -> TagValue:
        raise NotImplementedError

    def read_many(self, tags: Iterable[str]) -> dict[str, TagValue]:
        return {tag: self.read_value(tag) for tag in tags}

    def snapshot_values(self) -> dict[str, TagValue]:
        return {}

    @property
    def can_write(self) -> bool:
        return False


class SimulatedReadOnlyConnector(ReadOnlyTagConnector):
    """Read-only connector backed by an in-memory tag dictionary."""

    name = "simulated_read_only"

    def __init__(self, tags: dict[str, Any], source: str = "simulated"):
        self._tags = dict(tags)
        self.source = source

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def read_value(self, tag: str) -> TagValue:
        if tag not in self._tags:
            return TagValue(tag=tag, value=None, quality=BAD_QUALITY, timestamp=self._now(), source=self.source, message="Tag not found")
        return TagValue(tag=tag, value=self._tags[tag], quality=GOOD_QUALITY, timestamp=self._now(), source=self.source)

    def snapshot_values(self) -> dict[str, TagValue]:
        return {tag: self.read_value(tag) for tag in sorted(self._tags)}

    def set_simulated_value(self, tag: str, value: Any) -> None:
        self._tags[tag] = value
