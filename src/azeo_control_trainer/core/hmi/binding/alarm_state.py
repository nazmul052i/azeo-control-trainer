"""Shared operator alarm state for live graph bindings.

The strategy graph reports conditions; acknowledgement and suppression are
operator-session facts. Keeping those facts in one registry shared by every
display view means a banner acknowledgement changes the PVM, faceplate and
rollup together instead of each window inventing its own answer.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from collections import deque
import math
import time


@dataclass
class RuntimeAlarm:
    module: str
    block: str
    condition: str
    priority: int
    breached_limit: float | None = None
    active: bool = True
    acknowledged: bool = False
    suppressed: bool = False
    raised_at: float = 0.0
    changed_at: float = 0.0
    shelved_until: float = 0.0
    shelf_reason: str = ""

    def __post_init__(self) -> None:
        now = time.time()
        if not self.raised_at:
            self.raised_at = now
        if not self.changed_at:
            self.changed_at = now

    @property
    def key(self) -> str:
        return f"{self.module}/{self.block}/{self.condition}"

    @property
    def visible(self) -> bool:
        return self.active or not self.acknowledged or self.suppressed


class RuntimeAlarmRegistry:
    """Merge scanned alarm conditions with console-owned alarm state."""

    def __init__(self, *, clock=None):
        self._records: dict[str, RuntimeAlarm] = {}
        self._by_block: dict[str, dict[str, RuntimeAlarm]] = {}
        self.clock = clock or time.time
        self.events = deque(maxlen=10000)
        self.event_sequence = 0
        self._next_shelf_expiry = math.inf

    def _event(self, action, record):
        self.event_sequence += 1
        self.events.append({"sequence": self.event_sequence,
                            "time": self.clock(), "action": action,
                            "key": record.key, **asdict(record)})

    def _remove(self, record):
        self._records.pop(record.key, None)
        block_key = f"{record.module}/{record.block}"
        rows = self._by_block.get(block_key)
        if rows is not None:
            rows.pop(record.key, None)
            if not rows:
                self._by_block.pop(block_key, None)

    def expire_shelves(self) -> None:
        now = self.clock()
        if now < self._next_shelf_expiry:
            return
        for record in tuple(self._records.values()):
            if record.shelved_until and record.shelved_until <= now:
                record.suppressed = False
                record.shelved_until = 0.0
                record.changed_at = now
                # An active condition demands attention again after expiry.
                if record.active:
                    record.acknowledged = False
                self._event("shelf_expired", record)
                if not record.active and record.acknowledged:
                    self._remove(record)
        self._next_shelf_expiry = min((r.shelved_until for r in self._records.values()
                                       if r.shelved_until), default=math.inf)

    def shelve(self, key: str, seconds: float, reason: str) -> bool:
        seconds = float(seconds)
        if not math.isfinite(seconds) or not 1 <= seconds <= 86400:
            raise ValueError("Shelving duration must be between 1 second and 24 hours")
        if not str(reason).strip():
            raise ValueError("Enter a reason for shelving this alarm")
        record = self._records.get(key)
        if record is None:
            return False
        record.suppressed = True
        record.shelved_until = self.clock() + seconds
        self._next_shelf_expiry = min(self._next_shelf_expiry, record.shelved_until)
        record.shelf_reason = str(reason).strip()
        record.changed_at = self.clock()
        self._event("shelved", record)
        return True

    def observe(self, module: str, block: str, conditions) -> None:
        """Advance one block from ``(condition, priority, limit)`` rows."""
        self.expire_shelves()
        prefix = f"{module}/{block}/"
        active = {str(row[0]): row for row in conditions}
        # Per-value refresh must remain independent of alarms elsewhere in
        # the plant; a global walk here made navigation slow after an upset.
        for record in tuple(self._by_block.get(f"{module}/{block}", {}).values()):
            if record.condition not in active and record.active:
                record.active = False
                record.changed_at = self.clock()
                self._event("returned", record)
                if record.acknowledged and not record.suppressed:
                    self._remove(record)
        for condition, row in active.items():
            key = prefix + condition
            priority = int(row[1] or 0)
            breached = row[2] if len(row) > 2 else None
            record = self._records.get(key)
            if record is None:
                record = RuntimeAlarm(module, block, condition, priority, breached,
                                      raised_at=self.clock(), changed_at=self.clock())
                self._records[key] = record
                self._by_block.setdefault(f"{module}/{block}", {})[key] = record
                self._event("raised", record)
                continue
            # An escalation is a new event and must demand acknowledgement.
            if not record.active or priority > record.priority:
                record.acknowledged = False
                record.raised_at = self.clock()
                record.changed_at = record.raised_at
                record.active = True
                record.priority = priority
                self._event("raised", record)
            record.priority = priority
            record.breached_limit = breached
            record.active = True

    def records(self, *, modules=(), blocks=()) -> tuple[RuntimeAlarm, ...]:
        self.expire_shelves()
        module_set, block_set = set(modules), set(blocks)
        candidates = (record for block in block_set
                      for record in self._by_block.get(block, {}).values()) \
            if block_set else self._records.values()
        rows = (record for record in candidates
                if record.visible
                and (not module_set or record.module in module_set)
                and (not block_set
                     or f"{record.module}/{record.block}" in block_set))
        return tuple(sorted(rows, key=lambda row: (-row.priority, row.key)))

    def block_summary(self, module: str, block: str) -> dict:
        rows = self.records(blocks=(f"{module}/{block}",))
        demanding = [row for row in rows
                     if not row.suppressed
                     and (row.active or not row.acknowledged)]
        top = (demanding or list(rows) or [None])[0]
        return {
            "active": bool(top and top.active and not top.suppressed),
            "acked": bool(top and top.acknowledged),
            "suppressed": bool(top and top.suppressed),
            "priority": int(top.priority if top else 0),
            "condition": str(top.condition if top else ""),
            "breached_limit": top.breached_limit if top else None,
            "count": len(rows),
        }

    def acknowledge(self, keys=()) -> tuple[str, ...]:
        selected = set(keys)
        changed = []
        for key, record in tuple(self._records.items()):
            if selected and key not in selected:
                continue
            if not record.acknowledged:
                record.acknowledged = True
                record.changed_at = self.clock()
                self._event("acknowledged", record)
                changed.append(key)
            if not record.active and not record.suppressed:
                self._remove(record)
        return tuple(sorted(changed))

    def suppress(self, key: str, on: bool = True) -> bool:
        record = self._records.get(key)
        if record is None:
            return False
        record.suppressed = bool(on)
        record.changed_at = self.clock()
        record.shelved_until = 0.0
        if on:
            record.acknowledged = True
        elif not record.active and record.acknowledged:
            self._remove(record)
        self._event("suppressed" if on else "unsuppressed", record)
        return True


__all__ = ["RuntimeAlarm", "RuntimeAlarmRegistry"]
