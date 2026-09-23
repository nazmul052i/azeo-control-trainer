"""The LocalAdapter boundary keeps demand current and memory bounded."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from azeoplant.core.tags import TagDatabase  # noqa: E402
from azeoplant.io import VirtualIOBus  # noqa: E402
from azeoplant.io.adapters import LocalAdapter  # noqa: E402


def _bus(*names: str) -> VirtualIOBus:
    database = TagDatabase()
    for name in names:
        database.analog(name, "AO", "TEST", name, "%", 0.0, 100.0)
    return VirtualIOBus(database)


def test_frozen_engine_keeps_only_latest_demand_per_route() -> None:
    bus = _bus("AO-1")
    adapter = LocalAdapter(bus, source="TEST-DCS")
    adapter.start()

    for value in range(10_000):
        adapter.write("AO-1", value % 101)

    health = adapter.health()
    assert health.received == 10_000
    assert health.detail["write_queue"] == {
        "pending": 1,
        "capacity": bus.MAX_PENDING_WRITES,
        "peak": 1,
        "coalesced": 9_999,
        "rejected_capacity": 0,
    }
    assert bus.drain_writes() == 1
    assert float(bus.db["AO-1"].value) == pytest.approx(9_999 % 101)
    assert bus.queue_health()["pending"] == 0
    adapter.stop()


def test_distinct_route_capacity_rejects_without_dropping_accepted_demands(
        ) -> None:
    # Exercise the actual bound on both cores; the C++ constant is read-only.
    capacity = VirtualIOBus.MAX_PENDING_WRITES
    bus = _bus(*(f"AO-{index}" for index in range(capacity + 1)))
    for index in range(capacity):
        bus.queue_write(f"AO-{index}", float(index % 101), source="DCS")

    with pytest.raises(BufferError, match="distinct-route capacity"):
        bus.queue_write(f"AO-{capacity}", 33.0, source="DCS")

    assert bus.queue_health() == {
        "pending": capacity,
        "capacity": capacity,
        "peak": capacity,
        "coalesced": 0,
        "rejected_capacity": 1,
    }
    assert bus.drain_writes() == capacity
    assert all(float(bus.db[f"AO-{index}"].value) == float(index % 101)
               for index in range(capacity))
    assert float(bus.db[f"AO-{capacity}"].value) == pytest.approx(0.0)
