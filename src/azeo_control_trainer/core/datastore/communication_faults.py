"""Provider-neutral communication-failure simulation contracts."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass


@dataclass
class CommunicationFailure:
    """One active simulated link failure.

    ``tag`` is an exact store tag or ``*`` for every point.  Input failures
    hold the last delivered value and publish Bad/stale quality; output
    failures discard controller demands before they reach a field provider.
    """

    tag: str
    direction: str
    reason: str = "Simulated communication failure"
    failure_id: str = ""
    injected_at: float = 0.0
    dropped_writes: int = 0

    def __post_init__(self):
        self.tag = str(self.tag or "*").strip() or "*"
        self.direction = str(self.direction).strip().lower()
        if self.direction not in {"input", "output", "both"}:
            raise ValueError("direction must be input, output, or both")
        if not self.failure_id:
            self.failure_id = uuid.uuid4().hex[:12]
        if not self.injected_at:
            self.injected_at = time.time()

    def affects(self, tag: str, direction: str) -> bool:
        return (
            (self.tag == "*" or self.tag == tag)
            and self.direction in {direction, "both"}
        )

    def to_dict(self) -> dict:
        return {
            "failure_id": self.failure_id,
            "tag": self.tag,
            "direction": self.direction,
            "reason": self.reason,
            "injected_at": self.injected_at,
            "dropped_writes": self.dropped_writes,
        }
