"""Synthetic inventories validate the gate; they are not a duration qualification."""
from copy import deepcopy
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from operator_soak import OperatorSoak


def result(change):
    inventory = dict(faceplates=4, historians=2, cached_views=2,
                     python_threads=["MainThread", "history-io_0"], qt_workers=[],
                     timers=[dict(owner="ControllerExecutive", active=False, interval_ms=0, single_shot=True)])
    later = deepcopy(inventory)
    change(later)
    run = SimpleNamespace(metadata={"inventories": {"soak-start": inventory, "soak-10": later}},
                          host_samples=[], station=SimpleNamespace(historian=SimpleNamespace(available_points=dict)))
    soak = OperatorSoak(run, 7200)
    soak.started = time.perf_counter() - 7201
    soak.baseline_host = 0
    soak.transitions, soak.cycles = 200, 50
    return soak.result()


def test_timer_active_phase_is_not_a_lifetime_leak():
    report = result(lambda row: row["timers"][0].update(active=True))
    assert report["bounded_ownership_passed"]
    assert report["duration_and_lifecycle_passed"]


@pytest.mark.parametrize("resource", ["stopped_timer", "worker", "window"])
def test_additional_retained_resources_fail_the_gate(resource):
    def add(row):
        if resource == "stopped_timer":
            row["timers"].append(dict(owner="PvmFaceplateWidget", active=False, interval_ms=200, single_shot=False))
        elif resource == "worker":
            row["python_threads"].append("history-io_0")
        else:
            row["faceplates"] += 1

    report = result(add)
    assert not report["bounded_ownership_passed"]
    assert not report["duration_and_lifecycle_passed"]
