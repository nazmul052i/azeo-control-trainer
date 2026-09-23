"""Regression contracts for Control Designer document authoring lifecycle."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog  # noqa: E402

import azeo_control_trainer.core.strategy.blocks  # noqa: E402,F401
from azeo_control_trainer.core.strategy.model.block_registry import (  # noqa: E402
    registry,
)
from azeo_control_trainer.core.strategy.model.strategy_graph import (  # noqa: E402
    StrategyGraph,
)
from azeo_control_trainer.core.strategy.serialization import strategy_io  # noqa: E402
from azeo_control_trainer.azeo_control_designer import designer_tab as designer_module  # noqa: E402
from azeo_control_trainer.azeo_control_designer.designer_tab import (  # noqa: E402
    StrategyDesignerTab,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _tab_with_graph(graph: StrategyGraph, path: Path | None = None):
    _app()
    tab = StrategyDesignerTab()
    canvas = tab._create_canvas(graph.name, file_path=str(path) if path else None)
    canvas.scene.load_graph(graph)
    tab._connect_all_block_signals(canvas.scene)
    return tab, canvas


def _template(path: Path, *, destination_terminal: str = "IN") -> None:
    source = registry.create("ABS", "SOURCE")
    sink = registry.create("ABS", "SINK")
    assert source is not None and sink is not None
    document = {
        "name": "Atomic pair",
        "blocks": [source.to_dict(), sink.to_dict()],
        "wires": [{
            "id": "template-wire",
            "src_block_id": source.id,
            "src_terminal": "OUT",
            "dst_block_id": sink.id,
            "dst_terminal": destination_terminal,
            "is_bkcal": False,
        }],
    }
    path.write_text(json.dumps(document), encoding="utf-8")


def test_template_refused_wire_rolls_back_the_complete_macro(tmp_path) -> None:
    path = tmp_path / "refused-wire.json"
    _template(path, destination_terminal="DOES_NOT_EXIST")
    tab, canvas = _tab_with_graph(StrategyGraph("TARGET"))
    canvas.scene.undo_stack.clear()
    canvas.dirty = False

    assert not tab._place_template(str(path), QPointF(100.0, 100.0))
    assert canvas.scene.graph.blocks == {}
    assert canvas.scene.graph.wires == {}
    assert canvas.scene.undo_stack.index() == 0
    assert canvas.scene.undo_stack.count() == 0
    assert not canvas.scene.undo_stack.canRedo()
    assert not canvas.dirty

    tab.cleanup()
    tab.deleteLater()


def test_template_exception_after_first_add_rolls_back(monkeypatch, tmp_path) -> None:
    path = tmp_path / "raising-block.json"
    _template(path)
    tab, canvas = _tab_with_graph(StrategyGraph("TARGET"))
    canvas.scene.undo_stack.clear()
    original_add = canvas.scene.add_block
    calls = 0

    def raising_add(block):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic placement failure")
        return original_add(block)

    monkeypatch.setattr(canvas.scene, "add_block", raising_add)
    assert not tab._place_template(str(path), QPointF())
    assert canvas.scene.graph.blocks == {}
    assert canvas.scene.graph.wires == {}
    assert canvas.scene.undo_stack.index() == 0
    assert canvas.scene.undo_stack.count() == 0
    assert not canvas.scene.undo_stack.canRedo()

    tab.cleanup()
    tab.deleteLater()


def test_comment_cut_copy_and_paste_use_the_shared_selection_model() -> None:
    tab, canvas = _tab_with_graph(StrategyGraph("COMMENTS"))
    scene = canvas.scene
    comment = scene.add_comment("Commissioning note", QPointF(40.0, 60.0))
    scene.undo_stack.clear()
    comment.setSelected(True)

    tab._cut()
    clipboard = type(canvas.view)._clipboard
    assert clipboard is not None
    assert clipboard["blocks"] == []
    assert [entry["text"] for entry in clipboard["comments"]] == [
        "Commissioning note",
    ]
    assert scene._comment_items == []
    assert scene.undo_stack.count() == 1
    assert scene.undo_stack.undoText() == "Cut selection"

    scene.undo_stack.undo()
    assert len(scene._comment_items) == 1
    scene.undo_stack.redo()
    tab._paste()
    assert len(scene._comment_items) == 1
    pasted = scene._comment_items[0]
    assert pasted.text == "Commissioning note"
    assert pasted.pos() == QPointF(60.0, 80.0)
    assert pasted.isSelected()

    tab.cleanup()
    tab.deleteLater()


def test_standalone_wire_cut_and_paste_reconnects_and_keeps_manual_route() -> None:
    tab, canvas = _tab_with_graph(StrategyGraph("WIRE_CLIPBOARD"))
    scene = canvas.scene
    source = registry.create("ABS", "SOURCE")
    sink = registry.create("ABS", "SINK")
    assert source is not None and sink is not None
    scene.add_block(source, QPointF(0.0, 0.0))
    scene.add_block(sink, QPointF(240.0, 0.0))
    wire_item = scene.add_wire(source.id, "OUT", sink.id, "IN")
    assert wire_item is not None
    assert wire_item.set_manual_route([[120.0, 30.0], [120.0, 90.0]])
    manual_route = wire_item.manual_route_data()
    assert manual_route is not None
    wire_item.setSelected(True)
    scene.undo_stack.clear()

    tab._cut()
    clipboard = type(canvas.view)._clipboard
    assert clipboard is not None
    assert clipboard["blocks"] == []
    assert len(clipboard["wires"]) == 1
    assert clipboard["wires"][0]["_explicit"] is True
    assert scene.graph.wires == {}
    assert scene.undo_stack.count() == 1

    tab._paste()
    assert len(scene.graph.wires) == 1
    restored = next(iter(scene.graph.wires.values()))
    assert restored.src_block_id == source.id
    assert restored.dst_block_id == sink.id
    assert restored.route_points == manual_route
    assert next(iter(scene._wire_items.values())).isSelected()

    # Undo paste, then undo cut: both routes restore the same hand routing.
    scene.undo_stack.undo()
    scene.undo_stack.undo()
    restored = next(iter(scene.graph.wires.values()))
    assert restored.route_points == manual_route

    tab.cleanup()
    tab.deleteLater()


def test_save_as_clears_dirty_but_preserves_download_marker(
        monkeypatch, tmp_path) -> None:
    graph = StrategyGraph("SOURCE")
    block = registry.create("ABS", "ABS1")
    assert block is not None
    graph.add_block(block)
    tab, canvas = _tab_with_graph(graph)
    canvas.dirty = True
    canvas.downloaded_at = 1.0
    canvas.modified_since_download = True
    index = tab._canvas_tabs.indexOf(canvas)
    tab._canvas_tabs.setTabText(index, "SOURCE *")
    target = tmp_path / "saved-copy.json"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        lambda *_args, **_kwargs: (str(target), "Strategy modules (*.json)"),
    )

    assert tab._save_as()
    assert target.exists()
    assert canvas.file_path == str(target)
    assert not canvas.dirty
    assert canvas.modified_since_download
    assert tab._canvas_tabs.tabText(index) == "saved-copy *"

    tab.cleanup()
    tab.deleteLater()


def test_restore_replaces_same_canvas_and_versions_current_snapshot(
        monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(strategy_io, "_SETTINGS_PATH", tmp_path / "settings.json")
    target = tmp_path / "module.json"

    current = StrategyGraph("MODULE")
    current_block = registry.create("ABS", "CURRENT")
    assert current_block is not None
    current.add_block(current_block)
    strategy_io.save_strategy(current, path=target)
    tab, canvas = _tab_with_graph(current, target)
    current_block.x = 321.0  # unsaved state must remain recoverable
    canvas.dirty = True
    canvas.downloaded_at = 10.0
    canvas.modified_since_download = True

    restored = StrategyGraph("MODULE")
    restored_block = registry.create("ABS", "RESTORED")
    assert restored_block is not None
    restored.add_block(restored_block)
    selected = tmp_path / "selected-version.json"
    strategy_io.save_strategy(restored, path=selected)

    original_canvas_count = tab._canvas_tabs.count()
    assert tab._restore_version(canvas, selected)

    assert tab._canvas_tabs.count() == original_canvas_count
    assert tab._active_canvas() is canvas
    assert canvas.file_path == str(target)
    assert {block.instance_name for block in canvas.scene.graph.blocks.values()} == {
        "RESTORED",
    }
    assert not canvas.dirty
    assert canvas.modified_since_download
    assert tab._canvas_tabs.tabText(tab._canvas_tabs.indexOf(canvas)) == "module *"

    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["blocks"][0]["instance_name"] == "RESTORED"
    history = list((tmp_path / "versions").glob("module_*.json"))
    assert any(
        any(block.get("instance_name") == "CURRENT" and block.get("x") == 321.0
            for block in json.loads(version.read_text(encoding="utf-8"))["blocks"])
        for version in history
    )

    tab.cleanup()
    tab.deleteLater()


def test_version_restore_is_refused_while_module_is_online(
        monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(strategy_io, "_SETTINGS_PATH", tmp_path / "settings.json")
    graph = StrategyGraph("MODULE")
    block = registry.create("ABS", "CURRENT")
    assert block is not None
    graph.add_block(block)
    target = tmp_path / "module.json"
    strategy_io.save_strategy(graph, path=target)
    tab, canvas = _tab_with_graph(graph, target)
    before = target.read_bytes()
    canvas.runtime = SimpleNamespace(is_online=True)

    assert not tab._version_history()
    assert not tab._restore_version(canvas, target)
    assert target.read_bytes() == before
    assert canvas.scene.graph is graph

    canvas.runtime = None
    tab.cleanup()
    tab.deleteLater()


@pytest.mark.parametrize("preview", [False, True])
def test_headless_print_exits_before_canvas_or_print_services(
        monkeypatch, preview) -> None:
    _app()
    tab = StrategyDesignerTab()
    monkeypatch.setattr(designer_module, "is_headless", lambda: True)

    def should_not_inspect_canvas():
        raise AssertionError("headless print must exit before canvas inspection")

    monkeypatch.setattr(tab, "_active_canvas", should_not_inspect_canvas)
    assert tab._print_diagram(preview=preview) is False

    tab.cleanup()
    tab.deleteLater()
