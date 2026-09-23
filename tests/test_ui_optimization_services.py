"""Search and worker optimizations preserve literal matching and live authority."""
import os
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from azeo_control_trainer.core.configuration.catalog import CatalogIndex


def test_catalog_search_keeps_literal_unicode_and_multi_kind_paging():
    rows = [dict(path=path, name=path, kind=kind, description=description) for path, kind, description in (
        ("A/PV", "terminal", "Straße\npressure"), ("B/SP", "parameter", "Pressure [HIGH]"),
        ("C/PV", "terminal", "pressure [HIGH]"), ("D/OUT", "terminal", "no pressure"))]
    index = CatalogIndex(dict(project={"id": "test"}, tags=[], catalog=dict(nodes=rows, edges=[])))
    assert [r["path"] for r in index.search("STRASSE pressure")[0]] == ["A/PV"]
    assert index.search(".*")[1] == 0
    found, total = index.search("pressure [high]", kinds=("terminal", "parameter", "terminal"), offset=1, limit=1)
    assert total == 2 and [r["path"] for r in found] == ["C/PV"]
    assert index.search("pressure", kinds=("absent",))[1] == 0
    assert index.search("", offset=4)[0] == []
    assert index.search("pressure", limit=0) == ([], 4)
    assert index.search("PRESSURE\nSTRASSE", kinds=("terminal",))[1] == 1


def test_historian_query_captures_metadata_before_worker_runs():
    from azeo_control_trainer.core.hmi.history import ContinuousHistorian

    historian = ContinuousHistorian(None)
    point = historian.add_point("PV", unit="bar")
    transforms = []
    historian.archive = SimpleNamespace(query=lambda *args, **kwargs: transforms.append(args[-1]))
    historian.query_async(["PV"], 0, 1, as_snapshot=True)
    point.unit = "psi"
    snapshot = transforms[0]({"samples": {"PV": [(0, 20, "GOOD", 0, None, "")]}})
    assert snapshot.TAGS["PV"].unit == "bar"


def test_watch_retains_cells_while_force_and_unavailable_states_change():
    from PySide6.QtWidgets import QApplication
    from azeo_control_trainer.azeo_control_designer.widgets.watch_panel import WatchPanel

    app = QApplication.instance() or QApplication([])
    terminal = SimpleNamespace(value=1., forced=False, data_type=SimpleNamespace(value="FLOAT"))
    block = SimpleNamespace(id="loop", instance_name="Loop", inputs={}, outputs={"PV": terminal})
    panel = WatchPanel()
    panel.add(block, "PV", "OUT")
    cell = panel._table.item(0, 5)
    try:
        panel._refresh_values()
        assert panel._table.item(0, 5) is cell
        terminal.forced = True
        panel._refresh_values()
        assert cell.text()
        terminal.forced = False
        del block.outputs["PV"]
        panel._refresh_values()
        assert not cell.text()
        assert panel._table.item(0, 4).text() == "Unavailable"
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_archive_query_does_not_hold_the_sample_writer(tmp_path):
    from azeo_control_trainer.core.hmi.history.archive import HistoryArchive

    archive = HistoryArchive(tmp_path / "history.sqlite")
    entered, release = threading.Event(), threading.Event()

    def transform(result):
        entered.set()
        assert release.wait(10)
        return result

    query = archive.query(["PV"], 0, 10, transform=transform)
    try:
        assert entered.wait(5)
        write = archive.append({}, [("PV", 1, 42, "GOOD", archive.origin + 1, None, "")], [])
        write.result(timeout=2)
        assert not query.done(), "The test must overlap the read and the write"
    finally:
        release.set()
        query.result(timeout=10)
        archive.close()


def test_cancel_running_archive_query_releases_reader_for_latest_range(tmp_path, monkeypatch):
    from concurrent.futures import CancelledError
    from azeo_control_trainer.core.hmi.history.archive import HistoryArchive

    archive = HistoryArchive(tmp_path / "history.sqlite")
    entered = threading.Event()
    read = archive.read

    def blocked(paths, start, end, max_points, *, cancel_event, metadata):
        if start == 0:
            entered.set()
            assert cancel_event.wait(10), "Cancellation did not reach the running query"
        return read(paths, start, end, max_points, cancel_event=cancel_event, metadata=metadata)

    monkeypatch.setattr(archive, "read", blocked)
    old = archive.query(["PV"], 0, 1)
    try:
        assert entered.wait(5)
        assert not old.done()
        archive.cancel_query(old)
        latest = archive.query(["PV"], 2, 3)
        with pytest.raises(CancelledError):
            old.result(timeout=5)
        assert latest.result(timeout=5)["start"] == 2
        assert not archive.error, "A superseded range is not an archive failure"
    finally:
        archive.cancel_query(old)
        archive.close()


def test_workbench_reuses_diagnostics_snapshot_and_cells(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from test_simulation_workbench import _service
    from azeo_control_trainer.azeo_simulation_workbench import window as module

    app = QApplication.instance() or QApplication([])
    service, _, _ = _service(tmp_path)
    monkeypatch.setattr(module, "data_dir", lambda: tmp_path / "data")
    dialog = module.SimulationWorkbenchDialog(service.store, service.driver, tmp_path)
    cell = dialog.diagnostics_table.item(0, 1)
    calls = []
    diagnostics = dialog.service.diagnostics
    monkeypatch.setattr(
        dialog.service, "diagnostics",
        lambda **kwargs: (calls.append(1), diagnostics(**kwargs))[1])
    try:
        dialog.refresh_live()
        assert len(calls) == 1, "One refresh enumerated controller diagnostics twice"
        assert dialog.diagnostics_table.item(0, 1) is cell
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_station_rechecks_write_permission_without_a_new_process_value():
    from PySide6.QtWidgets import QApplication
    from test_faceplate_poll_scope import Source
    from azeo_control_trainer.core.hmi.pvms.publishing import PvmDisplay
    from azeo_control_trainer.core.hmi.pvms.rendering.viewer import PvmDisplayView
    from azeo_control_trainer.core.hmi.binding.source import WriteResult

    app = QApplication.instance() or QApplication([])
    allowed = [True]
    document = PvmDisplay(name="Permission", items=[dict(
        id="entry", kind="user_entry", x=0, y=0, w=100, h=40,
        entry=dict(kind="text_entry", path="LOOP/PID/SP"))])
    view = PvmDisplayView(document.to_dict(), lambda: {}, source=Source(), live=False,
                          write_checker=lambda _path: WriteResult(allowed[0]))
    item = next(item for item in view._refresh_items if item.data.get("id") == "entry")
    try:
        view.refresh()
        assert item.write_allowed
        allowed[0] = False
        view.refresh()
        assert not item.write_allowed
    finally:
        view.close()
        view.deleteLater()
        app.processEvents()


def test_project_load_activates_only_the_final_ready_module(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from azeo_control_trainer.azeo_control_designer.designer_tab import StrategyDesignerTab
    from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
    from azeo_control_trainer.core.strategy.blocks.io_blocks import AIBlock
    from azeo_control_trainer.core.strategy.serialization import strategy_io

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(strategy_io, "_SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(strategy_io, "get_strategy_dir", lambda *_: tmp_path)
    paths = []
    for index in range(3):
        graph = StrategyGraph(f"M{index}")
        graph.add_block(AIBlock("AI"))
        path = tmp_path / f"M{index}.json"
        strategy_io.save_strategy(graph, path)
        paths.append(path.name)
    (tmp_path / "_project.json").write_text(json.dumps({"areas": [{"strategies": paths}]}), encoding="utf-8")
    calls = []
    activate = StrategyDesignerTab._on_canvas_tab_changed
    monkeypatch.setattr(StrategyDesignerTab, "_on_canvas_tab_changed",
                        lambda self, index: (calls.append(index), activate(self, index))[1])
    tab = StrategyDesignerTab()
    tab._plugin = SimpleNamespace(strategy_subdir="test")
    calls.clear()
    try:
        assert tab.auto_load_project()
        assert len(calls) == 1
        assert tab._active_scene().graph.name == "M2"
        tab._canvas_tabs.setCurrentIndex(tab._canvas_tabs.count() - 2)
        assert tab._active_scene().graph.name == "M1"
    finally:
        tab.close()
        tab.deleteLater()
        app.processEvents()
