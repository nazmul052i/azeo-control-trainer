"""Bounded demand and degraded-driver write-service contracts."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from azeo_control_trainer.core.datastore.shared_data_store import (  # noqa: E402
    SharedDataStore,
)
from azeo_control_trainer.connectivity.fieldio.dynamic_provider import (  # noqa: E402
    ProviderSession,
)
from azeo_control_trainer.connectivity.fieldio.local_virtual_io import (  # noqa: E402
    LocalVirtualIoDriver,
    UnavailableLocalVirtualIoDriver,
)
from azeo_control_trainer.azeo_control_designer.executive import (  # noqa: E402
    ControllerExecutive,
)


def test_frozen_transport_keeps_only_latest_demand_per_tag() -> None:
    store = SharedDataStore()

    for value in range(10_000):
        store.queue_write("FIELD.AO", float(value))

    assert store.write_queue_health() == {
        "pending": 1,
        "capacity": store.MAX_PENDING_WRITES,
        "peak": 1,
        "coalesced": 9_999,
        "rejected_capacity": 0,
    }
    assert store.drain_writes() == [("FIELD.AO", 9_999.0)]
    assert store.write_queue_health()["pending"] == 0


def test_distinct_tag_capacity_is_explicit_and_preserves_accepted_writes(
        ) -> None:
    store = SharedDataStore()
    store.MAX_PENDING_WRITES = 2
    store.queue_write("FIELD.AO-1", 11.0)
    store.queue_write("ctrl.LOOP.wb.SP", 22.0)

    with pytest.raises(BufferError, match="distinct-tag capacity"):
        store.queue_write("FIELD.AO-2", 33.0)

    assert store.write_queue_health() == {
        "pending": 2,
        "capacity": 2,
        "peak": 2,
        "coalesced": 0,
        "rejected_capacity": 1,
    }
    assert store.drain_writes() == [
        ("FIELD.AO-1", 11.0),
        ("ctrl.LOOP.wb.SP", 22.0),
    ]


def test_replacement_moves_to_latest_batch_position() -> None:
    store = SharedDataStore()
    store.queue_write("ctrl.LOOP.wb.SP", 10.0)
    store.queue_write("ctrl.LOOP.wb.Mode", "MAN")
    store.queue_write("ctrl.LOOP.wb.SP", 20.0)

    assert store.drain_writes() == [
        ("ctrl.LOOP.wb.Mode", "MAN"),
        ("ctrl.LOOP.wb.SP", 20.0),
    ]


def test_executive_invokes_degraded_driver_write_service() -> None:
    _app = QCoreApplication.instance() or QCoreApplication([])
    store = SharedDataStore()

    class DegradedDriver:
        running = False

        def __init__(self) -> None:
            self.calls = 0

        def service_pending_writes(self) -> int:
            self.calls += 1
            local = [(key, value) for key, value in store.drain_writes()
                     if key != "FIELD.AO"]
            store.set_many(dict(local))
            return len(local)

    driver = DegradedDriver()
    store.field_io_driver = driver
    store.queue_write("FIELD.AO", 55.0)
    store.queue_write("ctrl.LOOP.wb.SP", 61.0)
    executive = ControllerExecutive(store)

    assert executive.apply_field_writes() == 1
    assert driver.calls == 1
    assert store.get("FIELD.AO") is None
    assert store.get("ctrl.LOOP.wb.SP") == 61.0


def test_provider_session_unwinds_component_whose_start_raises() -> None:
    events = []

    class PartialStart:
        def start(self) -> None:
            events.append("start")
            raise RuntimeError("failed after acquiring resources")

        def stop(self) -> None:
            events.append("stop")

        @staticmethod
        def read(_tag):
            return 0.0

        @staticmethod
        def write(_tag, _value) -> None:
            pass

    component = PartialStart()
    session = ProviderSession(component, component)

    with pytest.raises(RuntimeError, match="failed after acquiring"):
        session.start()

    assert events == ["start", "stop"]
    assert session._started == []


def _driver_config(*, provider_manages_claim: bool = False) -> dict:
    options = {"source": "TEST-DCS"} if provider_manages_claim else {}
    return {
        "type": "local_virtual_io",
        "source": "TEST-DCS",
        "period_ms": 60_000,
        "claim_outputs": True,
        "provider_manages_claim": provider_manages_claim,
        "provider": {"factory": "unused:create", "options": options},
        "signals": {
            "FIELD.AO": {
                "signal": "AO-1", "direction": "write", "kind": "AO",
            },
        },
    }


class _LifecycleProvider:
    def __init__(self, *, fail_start: bool = False,
                 fail_read: bool = False) -> None:
        self.fail_start = fail_start
        self.fail_read = fail_read
        self.events: list[str] = []

    def claim(self, source: str) -> bool:
        self.events.append(f"claim:{source}")
        return True

    def release(self, source: str) -> None:
        self.events.append(f"release:{source}")

    def start(self) -> None:
        self.events.append("start")
        if self.fail_start:
            raise RuntimeError("provider start failed")

    def stop(self) -> None:
        self.events.append("stop")

    def read_sample(self, _tag: str):
        if self.fail_read:
            raise RuntimeError("readback unavailable")
        return {"value": 41.0, "quality": "GOOD"}

    @staticmethod
    def read(_tag: str) -> float:
        return 41.0

    @staticmethod
    def write(_tag: str, _value: object) -> None:
        pass


def test_driver_managed_claim_precedes_runtime_start_and_is_released(
        tmp_path, monkeypatch) -> None:
    component = _LifecycleProvider()
    session = ProviderSession(component, component)
    import azeo_control_trainer.connectivity.fieldio.local_virtual_io as local_module

    monkeypatch.setattr(local_module, "load_provider",
                        lambda _config, _project: session)
    driver = LocalVirtualIoDriver(
        SharedDataStore(), _driver_config(), tmp_path)

    assert driver.start()
    assert component.events[:2] == ["claim:TEST-DCS", "start"]
    driver.stop()
    assert component.events[-2:] == ["release:TEST-DCS", "stop"]


def test_failed_seed_releases_claim_and_services_only_non_field_writes(
        tmp_path, monkeypatch) -> None:
    component = _LifecycleProvider(fail_read=True)
    session = ProviderSession(component, component)
    import azeo_control_trainer.connectivity.fieldio.local_virtual_io as local_module

    monkeypatch.setattr(local_module, "load_provider",
                        lambda _config, _project: session)
    store = SharedDataStore()
    driver = LocalVirtualIoDriver(store, _driver_config(), tmp_path)
    store.field_io_driver = driver

    assert not driver.start()
    assert component.events == [
        "claim:TEST-DCS", "start", "release:TEST-DCS", "stop",
    ]
    store.queue_write("FIELD.AO", 55.0)
    store.queue_write("ctrl.LOOP.wb.SP", 62.0)
    assert driver.service_pending_writes() == 1
    assert store.get("FIELD.AO") is None
    assert store.get("ctrl.LOOP.wb.SP") == 62.0
    assert driver.write_failures == 1
    assert store.write_queue_health()["pending"] == 0


def test_stop_does_not_teardown_provider_while_scan_worker_is_alive(
        tmp_path) -> None:
    component = _LifecycleProvider()
    store = SharedDataStore()
    driver = LocalVirtualIoDriver(store, _driver_config(), tmp_path)
    session = ProviderSession(component, component)

    class BlockedWorker:
        def __init__(self) -> None:
            self.join_timeout = None

        def join(self, timeout=None) -> None:
            self.join_timeout = timeout

        @staticmethod
        def is_alive() -> bool:
            return True

    worker = BlockedWorker()
    driver._session = session
    driver._claimed = True
    driver._thread = worker
    driver.running = True
    store.field_io_driver = driver

    driver.stop()

    assert worker.join_timeout <= driver.STOP_JOIN_TIMEOUT_S
    assert driver.running
    assert driver._thread is worker
    assert driver._session is session
    assert driver._claimed
    assert store.field_io_driver is driver
    assert component.events == []


def test_invalid_driver_marker_applies_local_and_rejects_known_field_write(
        tmp_path) -> None:
    store = SharedDataStore()
    driver = UnavailableLocalVirtualIoDriver(
        store, _driver_config(), RuntimeError("bad provider"), tmp_path)
    store.field_io_driver = driver
    store.queue_write("FIELD.AO", 73.0)
    store.queue_write("ctrl.LOOP.wb.SP", 64.0)

    assert driver.service_pending_writes() == 1
    assert store.get("FIELD.AO") is None
    assert store.get("ctrl.LOOP.wb.SP") == 64.0
    assert driver.write_failures == 1
    assert driver.health()["outputs"] == 1
