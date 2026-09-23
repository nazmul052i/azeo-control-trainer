"""A display value must not scan alarms belonging to the rest of the plant."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from azeo_control_trainer.core.hmi.binding.alarm_state import RuntimeAlarmRegistry


@pytest.mark.parametrize("operation", ["observe", "summary"])
def test_block_alarm_lookup_does_not_traverse_unrelated_alarms(operation):
    registry = RuntimeAlarmRegistry()
    for index in range(500):
        registry.observe(f"M{index}", "AI", [("HI", 11, 80)])

    class NoGlobalWalk(dict):
        def values(self):
            pytest.fail("Per-block refresh walked the entire alarm registry")

    registry._records = NoGlobalWalk(registry._records)
    if operation == "observe":
        registry.observe("M0", "AI", [("HI", 11, 80)])
    else:
        assert registry.block_summary("M0", "AI")["count"] == 1


@pytest.mark.parametrize("removal", ["acknowledge", "return", "unsuppress", "expiry"])
def test_block_alarm_index_tracks_every_removal_path(removal):
    now = [100.0]
    registry = RuntimeAlarmRegistry(clock=lambda: now[0])
    registry.observe("M", "AI", [("HI", 11, 80)])
    key = "M/AI/HI"
    if removal in ("unsuppress", "expiry"):
        registry.shelve(key, 10, "Test") if removal == "expiry" else registry.suppress(key)
    if removal in ("return", "expiry"):
        registry.acknowledge((key,))
    registry.observe("M", "AI", [])
    if removal == "acknowledge":
        registry.acknowledge((key,))
    elif removal == "unsuppress":
        registry.suppress(key, False)
    elif removal == "expiry":
        now[0] = 111
    assert registry.records() == ()
    assert registry.block_summary("M", "AI")["count"] == 0
    registry.observe("M", "AI", [("HI", 11, 80)])
    assert registry.block_summary("M", "AI")["active"]
