from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import Iterable

from .read_only_connector import ReadOnlyTagConnector
from .tag_value import BAD_QUALITY, GOOD_QUALITY, UNCERTAIN_QUALITY, TagValue


_MAX_FUTURE_SKEW_SECONDS = 5.0


def parse_tag_timestamp(value: str) -> datetime | None:
    """Parse an ISO timestamp from a TagValue. Returns None for invalid timestamps."""
    if not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            return None
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def tag_age_seconds(tag_value: TagValue, now: datetime | None = None) -> float | None:
    ts = parse_tag_timestamp(tag_value.timestamp)
    if ts is None:
        return None
    now = now or datetime.now(timezone.utc)
    return (now - ts).total_seconds()


@dataclass(frozen=True)
class MonitorRow:
    tag: str
    value: object
    quality: str
    timestamp: str
    age_seconds: float | None
    source: str
    message: str = ""

    @property
    def is_good(self) -> bool:
        return self.quality.lower() == GOOD_QUALITY.lower()

    @property
    def is_stale(self) -> bool:
        return self.quality.lower() == UNCERTAIN_QUALITY.lower() and "stale" in self.message.lower()

    def as_dict(self) -> dict[str, object]:
        return {
            "tag": self.tag,
            "value": self.value,
            "quality": self.quality,
            "timestamp": self.timestamp,
            "age_seconds": self.age_seconds,
            "source": self.source,
            "message": self.message,
        }


@dataclass(frozen=True)
class MonitorSnapshot:
    rows: list[MonitorRow]
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def good(self) -> int:
        return sum(1 for row in self.rows if row.quality.lower() == GOOD_QUALITY.lower())

    @property
    def bad(self) -> int:
        return sum(1 for row in self.rows if row.quality.lower() == BAD_QUALITY.lower())

    @property
    def uncertain(self) -> int:
        return sum(1 for row in self.rows if row.quality.lower() == UNCERTAIN_QUALITY.lower())

    @property
    def stale(self) -> int:
        return sum(1 for row in self.rows if row.is_stale)

    @property
    def overall_status(self) -> str:
        if self.bad:
            return "BAD"
        if self.stale or self.uncertain:
            return "UNCERTAIN"
        return "GOOD"

    def as_dict(self) -> dict[str, object]:
        return {
            "created_at": self.created_at,
            "overall_status": self.overall_status,
            "total": self.total,
            "good": self.good,
            "bad": self.bad,
            "uncertain": self.uncertain,
            "stale": self.stale,
            "rows": [row.as_dict() for row in self.rows],
        }


class LiveTagMonitor:
    """Poll a read-only connector and normalize tag quality, timestamps, and stale-data state."""

    def __init__(self, connector: ReadOnlyTagConnector, stale_after_seconds: float = 60.0):
        if not math.isfinite(stale_after_seconds) or stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be finite and positive")
        self.connector = connector
        self.stale_after_seconds = stale_after_seconds

    def read_once(self, tags: Iterable[str]) -> MonitorSnapshot:
        now = datetime.now(timezone.utc)
        requested = sorted(set(tags))
        values = self.connector.read_many(requested)
        rows: list[MonitorRow] = []
        for tag in requested:
            tag_value = values.get(tag)
            if tag_value is None:
                rows.append(MonitorRow(
                    tag=tag,
                    value=None,
                    quality=BAD_QUALITY,
                    timestamp="",
                    age_seconds=None,
                    source=getattr(self.connector, "name", type(self.connector).__name__),
                    message="connector omitted the requested tag",
                ))
                continue
            age = tag_age_seconds(tag_value, now=now)
            quality = tag_value.quality
            message = tag_value.message
            if tag_value.is_good and age is None:
                quality = UNCERTAIN_QUALITY
                suffix = "missing or invalid source timestamp"
                message = f"{message}; {suffix}" if message else suffix
            elif tag_value.is_good and age < -_MAX_FUTURE_SKEW_SECONDS:
                quality = UNCERTAIN_QUALITY
                suffix = f"source timestamp is {-age:.1f}s in the future"
                message = f"{message}; {suffix}" if message else suffix
            elif tag_value.is_good and age > self.stale_after_seconds:
                quality = UNCERTAIN_QUALITY
                suffix = f"stale: age {age:.1f}s exceeds {self.stale_after_seconds:.1f}s"
                message = f"{message}; {suffix}" if message else suffix
            if tag_value.is_good and isinstance(tag_value.value, float) and not math.isfinite(tag_value.value):
                quality = BAD_QUALITY
                suffix = "non-finite numeric value"
                message = f"{message}; {suffix}" if message else suffix
            rows.append(
                MonitorRow(
                    tag=tag,
                    value=tag_value.value,
                    quality=quality,
                    timestamp=tag_value.timestamp,
                    age_seconds=age,
                    source=tag_value.source,
                    message=message,
                )
            )
        return MonitorSnapshot(rows=rows)

    def poll(self, tags: Iterable[str], interval_seconds: float = 1.0, cycles: int = 1):
        """Yield monitor snapshots. cycles=0 means forever."""
        if not math.isfinite(interval_seconds) or interval_seconds <= 0:
            raise ValueError("interval_seconds must be finite and positive")
        if cycles < 0:
            raise ValueError("cycles must be zero (forever) or a positive count")
        count = 0
        while cycles <= 0 or count < cycles:
            yield self.read_once(tags)
            count += 1
            if cycles <= 0 or count < cycles:
                time.sleep(interval_seconds)
