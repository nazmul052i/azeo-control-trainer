from __future__ import annotations

from typing import Any
import math

from .tag_provider import TagProvider
from .read_only_connector import ReadOnlyTagConnector
from .tag_value import TagValue
from .live_monitor import _MAX_FUTURE_SKEW_SECONDS, tag_age_seconds


class BadTagQualityError(RuntimeError):
    pass


class AdvisoryTagProvider(TagProvider):
    """TagProvider used for safe advisory runtime.

    Reads come from a read-only connector. Procedure writes are captured as
    proposed actions and never forwarded to a live endpoint.
    """

    def __init__(
        self,
        connector: ReadOnlyTagConnector,
        require_good_quality: bool = True,
        *,
        stale_after_seconds: float = 60.0,
        require_timestamp: bool = True,
        allow_stale: bool = False,
        allow_uncertain_quality: bool = False,
    ):
        if not math.isfinite(stale_after_seconds) or stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be finite and positive")
        self.connector = connector
        self.require_good_quality = require_good_quality
        self.stale_after_seconds = stale_after_seconds
        self.require_timestamp = require_timestamp
        self.allow_stale = allow_stale
        self.allow_uncertain_quality = allow_uncertain_quality
        self.read_cache: dict[str, TagValue] = {}
        self.proposed_writes: list[dict[str, Any]] = []

    def read_value(self, tag: str) -> TagValue:
        tag_value = self.connector.read_value(tag)
        self.read_cache[tag] = tag_value
        uncertain_allowed = (
            self.allow_uncertain_quality
            and tag_value.quality.strip().casefold() == "uncertain"
        )
        if self.require_good_quality and not tag_value.is_good and not uncertain_allowed:
            raise BadTagQualityError(f"Bad quality for tag {tag}: {tag_value.quality} {tag_value.message}".strip())
        age = tag_age_seconds(tag_value)
        if self.require_timestamp and age is None:
            raise BadTagQualityError(f"Missing or invalid source timestamp for tag {tag}")
        if age is not None and age < -_MAX_FUTURE_SKEW_SECONDS:
            raise BadTagQualityError(
                f"Future source timestamp for tag {tag}: {-age:.1f}s ahead of the local clock"
            )
        if not self.allow_stale and age is not None and age > self.stale_after_seconds:
            raise BadTagQualityError(
                f"Stale value for tag {tag}: age {age:.1f}s exceeds "
                f"{self.stale_after_seconds:.1f}s"
            )
        if isinstance(tag_value.value, float) and not math.isfinite(tag_value.value):
            raise BadTagQualityError(f"Non-finite numeric value for tag {tag}")
        return tag_value

    def read(self, tag: str) -> Any:
        return self.read_value(tag).value

    def write(self, tag: str, value: Any) -> None:
        self.proposed_writes.append({"tag": tag, "value": value, "mode": "advisory_only"})

    def snapshot(self) -> dict[str, Any]:
        values = self.connector.snapshot_values()
        if not values:
            values = dict(self.read_cache)
        return {tag: tv.value for tag, tv in values.items()}

    def quality_snapshot(self) -> dict[str, dict[str, Any]]:
        values = self.connector.snapshot_values()
        if not values:
            values = dict(self.read_cache)
        return {tag: tv.as_dict() for tag, tv in values.items()}
