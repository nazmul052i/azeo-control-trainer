"""OPC UA acquisition quality reaches controller-facing TAG monitors."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from asyncua import ua

from azeo_control_trainer.connectivity.fieldio.opcua_driver import OpcUaLink, _ReadHandler
from azeo_control_trainer.core.datastore.shared_data_store import SharedDataStore
from azeo_control_trainer.core.strategy.blocks.dv_tag_io_blocks import (
    TagAnalogInputBlock,
)
from azeo_control_trainer.core.strategy.engine.bridge import DataBridge
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
from azeo_control_trainer.core.strategy.model.terminal import Quality


def _notification(
    handler: _ReadHandler,
    node,
    value: float,
    status: int,
    timestamp: datetime,
) -> None:
    data_value = ua.DataValue(
        ua.Variant(value, ua.VariantType.Double),
        StatusCode_=ua.StatusCode(status),
        SourceTimestamp=timestamp,
    )
    data = SimpleNamespace(
        monitored_item=SimpleNamespace(Value=data_value),
    )
    handler.datachange_notification(node, value, data)


def _tag_monitor(store: SharedDataStore) -> TagAnalogInputBlock:
    block = TagAnalogInputBlock("AI_MON")
    block.config.params["tag"] = "PLC.AI"
    block._apply_config()
    graph = StrategyGraph("OPC QUALITY")
    graph.add_block(block)
    DataBridge(store).read_inputs(graph)
    return block


def test_subscription_callback_carries_value_quality_and_source_timestamp() \
        -> None:
    store = SharedDataStore()
    link = OpcUaLink(store, "opc.tcp://unused", {
        "PLC.AI.Val": {"node": "ns=2;s=PLC.AI.Val", "direction": "read"},
    })
    node = SimpleNamespace(nodeid=ua.NodeId("PLC.AI.Val", 2))
    handler = _ReadHandler(link, {node.nodeid.to_string(): "PLC.AI.Val"})

    good_at = datetime(2026, 9, 1, 12, 30, tzinfo=timezone.utc)
    _notification(handler, node, 42.5, ua.StatusCodes.Good, good_at)

    sample = store.get_sample("PLC.AI.Val")
    assert sample is not None
    assert sample.value == 42.5
    assert sample.quality == "GOOD"
    assert sample.timestamp == good_at.timestamp()
    assert sample.stale is False
    monitor = _tag_monitor(store)
    assert monitor.get_output("OUT") == 42.5
    assert monitor.outputs["OUT"].status is Quality.GOOD

    uncertain_at = datetime(2026, 9, 1, 12, 31, tzinfo=timezone.utc)
    _notification(
        handler,
        node,
        43.0,
        ua.StatusCodes.Uncertain,
        uncertain_at,
    )
    sample = store.get_sample("PLC.AI.Val")
    assert sample is not None
    assert sample.value == 43.0
    assert sample.quality == "UNCERTAIN"
    assert sample.timestamp == uncertain_at.timestamp()
    monitor = _tag_monitor(store)
    assert monitor.get_output("OUT") == 43.0
    assert monitor.outputs["OUT"].status is Quality.UNCERTAIN
    assert monitor.get_output("BLOCK_ERR") == ""
    assert link.reads_published == 2


def test_bad_subscription_status_holds_value_and_drives_bad_pv() -> None:
    store = SharedDataStore()
    link = OpcUaLink(store, "opc.tcp://unused", {
        "PLC.AI.Val": {"node": "ns=2;s=PLC.AI.Val", "direction": "read"},
    })
    node = SimpleNamespace(nodeid=ua.NodeId("PLC.AI.Val", 2))
    handler = _ReadHandler(link, {node.nodeid.to_string(): "PLC.AI.Val"})
    source_at = datetime(2026, 9, 1, 12, 30, tzinfo=timezone.utc)
    _notification(handler, node, 42.5, ua.StatusCodes.Good, source_at)

    _notification(
        handler,
        node,
        999.0,
        ua.StatusCodes.BadNoCommunication,
        datetime(2026, 9, 1, 12, 32, tzinfo=timezone.utc),
    )

    # The Bad payload does not replace the last trustworthy process value.
    assert store.get("PLC.AI.Val") == 42.5
    sample = store.get_sample("PLC.AI.Val")
    assert sample is not None
    assert sample.value == 42.5
    assert sample.quality == "BAD"
    assert sample.timestamp == source_at.timestamp()
    assert sample.stale is True
    monitor = _tag_monitor(store)
    assert monitor.get_output("OUT") == 42.5
    assert monitor.outputs["OUT"].status is Quality.BAD
    assert monitor.get_output("BLOCK_ERR") == "Bad PV"
    assert link.reads_published == 1


def test_first_bad_notification_is_an_explicit_stale_sample() -> None:
    store = SharedDataStore()
    link = OpcUaLink(store, "opc.tcp://unused", {
        "PLC.AI.Val": {"node": "ns=2;s=PLC.AI.Val", "direction": "read"},
    })
    node = SimpleNamespace(nodeid=ua.NodeId("PLC.AI.Val", 2))
    handler = _ReadHandler(link, {node.nodeid.to_string(): "PLC.AI.Val"})
    failed_at = datetime(2026, 9, 1, 12, 32, tzinfo=timezone.utc)

    _notification(
        handler,
        node,
        99.0,
        ua.StatusCodes.BadSensorFailure,
        failed_at,
    )

    sample = store.get_sample("PLC.AI.Val")
    assert sample is not None
    assert sample.value == 99.0
    assert sample.quality == "BAD"
    assert sample.timestamp == failed_at.timestamp()
    assert sample.stale is True
    assert _tag_monitor(store).outputs["OUT"].status is Quality.BAD


def test_lost_session_marks_every_observed_read_stale(monkeypatch) -> None:
    store = SharedDataStore()
    store.set_sample("PLC.AI.Val", 17.0, quality="GOOD", timestamp=100.0)
    store.set_sample("PLC.DI.Sts", True, quality="GOOD", timestamp=101.0)
    link = OpcUaLink(store, "opc.tcp://unused", {
        "PLC.AI.Val": {"node": "ns=2;s=PLC.AI.Val", "direction": "read"},
        "PLC.DI.Sts": {"node": "ns=2;s=PLC.DI.Sts", "direction": "read"},
        "PLC.NEW.Val": {"node": "ns=2;s=PLC.NEW.Val", "direction": "read"},
    })

    class FailingSession:
        disconnected = False

        def __init__(self, _endpoint: str) -> None:
            pass

        async def connect(self) -> None:
            return None

        async def disconnect(self) -> None:
            self.disconnected = True

    session = FailingSession("unused")
    monkeypatch.setattr("asyncua.Client", lambda _endpoint: session)

    async def lose_session(_client) -> None:
        link._stopping.set()
        raise ConnectionError("test session loss")

    monkeypatch.setattr(link, "_serve_session", lose_session)
    asyncio.run(link._session_loop())

    assert session.disconnected is True
    for tag, timestamp in (("PLC.AI.Val", 100.0), ("PLC.DI.Sts", 101.0)):
        sample = store.get_sample(tag)
        assert sample is not None
        assert sample.quality == "BAD"
        assert sample.timestamp == timestamp
        assert sample.stale is True
    never_observed = store.get_sample("PLC.NEW.Val")
    assert never_observed is not None
    assert never_observed.value is None
    assert never_observed.quality == "BAD"
    assert never_observed.stale is True
