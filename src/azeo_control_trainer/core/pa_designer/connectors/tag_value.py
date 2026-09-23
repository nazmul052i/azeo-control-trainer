from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

GOOD_QUALITY = "Good"
BAD_QUALITY = "Bad"
UNCERTAIN_QUALITY = "Uncertain"


@dataclass(frozen=True)
class TagValue:
    """One online tag read with value, quality, timestamp, and source metadata."""

    tag: str
    value: Any
    quality: str = GOOD_QUALITY
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source: str = "unknown"
    message: str = ""

    @property
    def is_good(self) -> bool:
        return self.quality.lower() == GOOD_QUALITY.lower()

    def as_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "value": self.value,
            "quality": self.quality,
            "timestamp": self.timestamp,
            "source": self.source,
            "message": self.message,
        }
