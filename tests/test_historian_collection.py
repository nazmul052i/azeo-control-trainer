"""Every history collector shares the graph inventory without caching values."""
from __future__ import annotations

import math
import os
from pathlib import Path
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from azeo_control_trainer.core.hmi.binding import LiveGraphSource  # noqa: E402
from azeo_control_trainer.core.hmi.history import ContinuousHistorian  # noqa: E402
from azeo_control_trainer.core.strategy.blocks.io_blocks import AIBlock  # noqa: E402
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph  # noqa: E402
from azeo_control_trainer.core.strategy.model.terminal import Quality  # noqa: E402


def graph_with_value(value):
    graph = StrategyGraph(name="LOOP")
    block = AIBlock("AI1")
    block._apply_config()
    block.outputs["OUT"].value = value
    block.outputs["OUT"].status = Quality.GOOD
    graph.add_block(block)
    return graph


@pytest.fixture
def collection():
    state = SimpleNamespace(graphs={f"M{i}": graph_with_value(i) for i in range(24)}, calls=0)

    def graphs():
        state.calls += 1
        return dict(state.graphs)

    source = LiveGraphSource(graphs)
    historian = ContinuousHistorian(None, resolver=source)
    for name in state.graphs:
        historian.add_point(f"{name}/AI1/OUT", module=name, unit="bar")
    yield historian, source, state
    historian.close()


def test_direct_collection_reads_inventory_once_and_records_each_point(collection):
    historian, _, state = collection
    assert historian.collect(now=0) == 24
    assert state.calls == 1
    for i, point in enumerate(historian.TAGS.values()):
        assert list(point.values) == [i]
        assert list(point.qualities) == ["GOOD"]
        assert list(point.sample_units) == ["bar"]


def test_collection_reuses_outer_snapshot_and_skips_work_until_due(collection):
    historian, source, state = collection
    with source.snapshot():
        assert historian.collect(now=0) == 24
        assert historian.collect(now=.5) == 0
        assert historian.collect(now=1) == 24
    assert state.calls == 1
    assert historian.sample_count == 2
    assert historian.collect(now=1.5) == 0
    historian.read_only = True
    assert historian.collect(now=2, force=True) == 0
    assert state.calls == 1


def test_next_collection_observes_replacement_and_removal(collection):
    historian, _, state = collection
    historian.collect(now=0)
    state.graphs["M0"] = graph_with_value(81)
    state.graphs.pop("M1")
    historian.collect(now=1)
    assert list(historian.TAGS["M0/AI1/OUT"].values) == [0, 81]
    missing = historian.TAGS["M1/AI1/OUT"]
    assert math.isnan(missing.values[-1])
    assert list(missing.qualities) == ["GOOD", "BAD"]
    assert state.calls == 2


def test_failed_collection_releases_inventory_before_next_read(collection, monkeypatch):
    historian, source, state = collection
    read = source.read

    def fail(_):
        raise RuntimeError("resolver disconnected")

    monkeypatch.setattr(source, "read", fail)
    with pytest.raises(RuntimeError, match="disconnected"):
        historian.collect(now=0)
    monkeypatch.setattr(source, "read", read)
    state.graphs["M0"] = graph_with_value(92)
    assert source.read("M0/AI1/OUT").value == 92
    assert historian.collect(now=1) == 24
    assert historian.TAGS["M0/AI1/OUT"].values[-1] == 92
    assert state.calls == 3


def test_resolver_without_snapshot_keeps_uncertain_samples():
    source = SimpleNamespace(read=lambda _: (42, Quality.UNCERTAIN, False))
    historian = ContinuousHistorian(None, resolver=source)
    point = historian.add_point("LOOP/AI1/OUT")
    assert historian.collect(now=0) == 1
    assert point.values[-1] == 42
    assert point.qualities[-1] == "UNCERTAIN"
    historian.close()


def test_detached_history_windows_share_collection_and_preserve_bad_gaps(collection, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from azeo_control_trainer.core.hmi.history.view import ProcessHistoryView

    app = QApplication.instance() or QApplication([])
    historian, _, state = collection
    clock = [0]
    monkeypatch.setattr(historian, "now", lambda: clock[0])
    views = []
    try:
        # Construction may refresh. Hold sampling until both windows are ready.
        historian.read_only = True
        for _ in range(2):
            view = ProcessHistoryView(historian, "M0")
            views.append(view)
            view._timer.stop()
        historian.read_only = False
        state.calls = 0
        for view in views:
            view._refresh()
        assert state.calls == 1
        assert historian.sample_count == 1
        clock[0] = 1
        state.graphs.pop("M0")
        for view in views:
            view._refresh()
        point = historian.TAGS["M0/AI1/OUT"]
        assert historian.sample_count == 2
        assert state.calls == 2
        assert list(point.qualities) == ["GOOD", "BAD"]
        assert math.isnan(point.values[-1])
    finally:
        for view in views:
            view.close()
            view.deleteLater()
        app.processEvents()
