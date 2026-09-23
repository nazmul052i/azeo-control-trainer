"""A project loads all executable graphs without building every hidden diagram."""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.azeo_control_designer.designer_tab import StrategyDesignerTab
from azeo_control_trainer.azeo_control_designer.canvas.strategy_view import StrategyView
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
from azeo_control_trainer.core.strategy.blocks.io_blocks import AIBlock
from azeo_control_trainer.core.strategy.serialization import strategy_io


@pytest.fixture
def project(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(strategy_io, "_SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(strategy_io, "get_strategy_dir", lambda *_: tmp_path)
    paths = []
    for index in range(3):
        graph = StrategyGraph(f"M{index}")
        graph.add_block(AIBlock("AI"))
        path = tmp_path / f"M{index}.json"
        strategy_io.save_strategy(graph, path, comments=[dict(text=f"Note {index}", x=40, y=50)])
        paths.append(path.name)
    (tmp_path / "_project.json").write_text(json.dumps({"areas": [{"strategies": paths}]}), encoding="utf-8")
    tab = StrategyDesignerTab()
    tab._plugin = SimpleNamespace(strategy_subdir="test")
    tab._startup_remembered = []
    remember = strategy_io.set_last_strategy
    monkeypatch.setattr(strategy_io, "set_last_strategy",
                        lambda path: (tab._startup_remembered.append(path), remember(path))[1])
    tab._startup_selections = []
    select = tab._canvas_tabs.setCurrentIndex
    monkeypatch.setattr(tab._canvas_tabs, "setCurrentIndex",
                        lambda index: (tab._startup_selections.append(index), select(index))[1])
    assert tab.auto_load_project()
    yield tab, tmp_path
    tab.cleanup()
    tab.close()
    tab.deleteLater()
    app.processEvents()


def canvases(tab):
    return [tab._canvas_tabs.widget(i) for i in range(tab._canvas_tabs.count())
            if tab._canvas_tabs.widget(i).scene.graph.blocks]


def test_only_active_project_diagram_has_visual_items(project):
    tab, _ = project
    loaded = canvases(tab)
    assert len(loaded) == 3
    assert sum(bool(c.scene._block_items) for c in loaded) == 1
    assert sum(len(c.findChildren(StrategyView)) for c in loaded) == 1
    assert all(not c.dirty for c in loaded)


def test_project_load_remembers_and_selects_only_the_final_module(project):
    tab, _ = project
    assert len(tab._startup_remembered) == 1
    assert Path(tab._startup_remembered[0]).name == "M2.json"
    assert len(tab._startup_selections) == 1
    assert not tab._canvas_tabs.tabBar().isHidden()
    assert tab._canvas_tabs.updatesEnabled()


def test_read_only_tag_inventory_does_not_change_recent_module(project):
    from azeo_control_trainer.core.strategy.tagdb import TagDatabase

    tab, root = project
    tab._startup_remembered.clear()
    TagDatabase.from_area(root)
    assert tab._startup_remembered == []


def test_seat_settings_failure_does_not_abort_the_loaded_project(project, monkeypatch, caplog):
    tab, _ = project

    def denied(_path):
        raise PermissionError("seat settings are read-only")

    monkeypatch.setattr(strategy_io, "set_last_strategy", denied)
    assert tab.auto_load_project()
    assert tab._active_scene().graph.name == "M2"
    assert tab._canvas_tabs.updatesEnabled() and not tab._canvas_tabs.tabBar().isHidden()
    assert "seat settings are read-only" in caplog.text


def test_unopened_comments_survive_save_and_selection_reuses_graph(project):
    tab, root = project
    first = canvases(tab)[0]
    graph = first.scene.graph
    block = next(iter(graph.blocks.values()))
    assert first.scene.get_comments_data()[0]["text"] == "Note 0"
    strategy_io.save_strategy(graph, root / "copy.json", comments=first.scene.get_comments_data())
    assert json.loads((root / "copy.json").read_text(encoding="utf-8"))["comments"][0]["text"] == "Note 0"
    tab._canvas_tabs.setCurrentWidget(first)
    assert first.scene.graph is graph
    assert first.scene._block_items[block.id].block is block
    assert first.scene.get_comments_data()[0]["text"] == "Note 0"
    assert not first.dirty and not first.scene._undo_stack.canUndo()


def test_online_diagram_materialization_preserves_runtime_and_locks(project):
    from azeo_control_trainer.core.strategy.engine.compiler import compile_strategy
    from azeo_control_trainer.core.strategy.engine.runtime import StrategyRuntime

    tab, _ = project
    first = canvases(tab)[0]
    graph = first.scene.graph
    compiled = compile_strategy(graph)
    runtime = StrategyRuntime()
    runtime.load(compiled, None)
    first.runtime = runtime
    assert runtime.go_online()
    first.scene.set_structure_locked(True)
    first.scene.set_live_mode(True)
    first.scene.set_show_wire_values(True)
    first.scene.apply_exec_order(compiled.exec_order)
    try:
        tab._canvas_tabs.setCurrentWidget(first)
        assert first.runtime is runtime and runtime.compiled.graph is graph
        assert first.scene.graph is graph and first.scene._structure_locked
        assert first.scene.is_live_mode()
        assert len(first.scene._block_items) == len(graph.blocks)
        assert not first.dirty
    finally:
        runtime.go_offline()


def test_hidden_diagrams_do_not_refresh_and_switch_refreshes_immediately(project, monkeypatch):
    from azeo_control_trainer.core.strategy.engine.compiler import compile_strategy
    from azeo_control_trainer.core.strategy.engine.runtime import StrategyRuntime

    tab, _ = project
    counts = {}
    loaded = canvases(tab)
    for index, canvas in enumerate(loaded):
        tab._canvas_tabs.setCurrentWidget(canvas)
        runtime = StrategyRuntime()
        runtime.load(compile_strategy(canvas.scene.graph), None)
        assert runtime.go_online()
        canvas.runtime = runtime
        counts[index] = 0

        def refresh(index=index):
            counts[index] += 1

        for item in canvas.scene._block_items.values():
            monkeypatch.setattr(item, "refresh", refresh)
    tab.show()
    QApplication.instance().processEvents()
    counts.update({index: 0 for index in counts})
    try:
        tab.on_tick()
        assert counts == {0: 0, 1: 0, 2: 1}, "Hidden diagrams consumed live UI refresh work"
        tab._canvas_tabs.setCurrentWidget(loaded[0])
        assert counts[0] == 1, "Newly selected diagram waited for a periodic refresh"
        assert all(canvas.runtime.is_online for canvas in loaded)
    finally:
        for canvas in loaded:
            canvas.runtime.go_offline()
