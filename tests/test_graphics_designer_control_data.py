"""Focused contracts for the Graphics Designer engineering-data workflow."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

import azeo_control_trainer.core.strategy.blocks  # noqa: F401
from azeo_control_trainer.azeo_graphics_designer.window import HmiStudioWindow
from azeo_control_trainer.core.strategy.model.block_registry import BlockRegistry
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph


def _application():
    return QApplication.instance() or QApplication([])


def _graph():
    block = BlockRegistry().create("PID", "PID1")
    assert block is not None
    block._apply_config()
    graph = StrategyGraph(name="UNIT100")
    graph.add_block(block)
    return graph


def test_control_data_exposes_blocks_parameters_and_configuration(tmp_path):
    _application()
    graph = _graph()
    window = HmiStudioWindow(
        lambda: {"UNIT100": graph}, tmp_path, area_name="Plant")
    labels = [window.explorer_tabs.tabText(index)
              for index in range(window.explorer_tabs.count())]
    assert labels == ["Graphics Explorer", "Library Explorer",
                      "Control Data", "Selection", "Layers"]

    module = window.control_browser.topLevelItem(0)
    block = module.child(0)
    assert block.text(0) == "PID1"
    assert block.data(0, Qt.UserRole) == ("UNIT100/PID1", "PID")
    folders = [block.child(index).text(0)
               for index in range(block.childCount())]
    assert folders[:2] == ["Inputs", "Outputs"]
    assert "Configuration" in folders

    output_folder = next(block.child(index)
                         for index in range(block.childCount())
                         if block.child(index).text(0) == "Outputs")
    out_row = next(output_folder.child(index)
                   for index in range(output_folder.childCount())
                   if output_folder.child(index).text(0) == "OUT")
    assert out_row.data(0, Qt.UserRole + 1)["path"] \
        == "UNIT100/PID1/OUT"

    placed = window.place_parameter_link("UNIT100/PID1/OUT")
    assert placed is not None
    assert any(item.data.get("kind") == "datalink"
               and item.data.get("path") == "UNIT100/PID1/OUT"
               for item in window.current()._static_items())
    window.close()


def test_palette_families_and_modeless_problems(tmp_path):
    _application()
    graph = _graph()
    window = HmiStudioWindow(lambda: {"UNIT100": graph}, tmp_path)
    sections = [title for _header, _content, title
                in window._palette_sections]
    assert "Data" in sections
    assert "Function Block" in sections
    assert "High Performance PVMs" in sections
    assert "Process PVMs" in sections

    bad_pvm = window.current().place_block(
        "UNIT100/MISSING", "PID", 40, 40,
        role=("dynamo_compact", ""))
    window._validate()
    assert window.last_validation == ["UNIT100/MISSING"]
    assert window.problems_pane.topLevelItemCount() == 1
    assert window.problems_pane.topLevelItem(0).text(1) == "Overview"
    window._activate_problem("UNIT100/MISSING")
    bad_item = next(item for item in window.current()._items()
                    if item.pvm.id == bad_pvm.id)
    assert bad_item.isSelected()
    window.close()


def test_focus_canvas_restores_panes_and_align_uses_full_command(tmp_path):
    _application()
    graph = _graph()
    window = HmiStudioWindow(lambda: {"UNIT100": graph}, tmp_path)
    studio = window.current()
    first = studio.add_static("rect", 10, 20, 40, 30)
    second = studio.add_static("rect", 180, 90, 60, 30)
    first.setSelected(True)
    second.setSelected(True)
    window._align_selected()  # headless uses the deterministic left command
    assert first.pos().x() == second.pos().x()

    assert window.explorer_tabs.isHidden()
    assert not window.palette_box.isHidden()
    assert not studio.pane.isHidden()
    window.toggle_focus_mode()
    assert window.explorer_tabs.isHidden()
    assert window.palette_box.isHidden()
    assert studio.pane.isHidden()
    window.toggle_focus_mode()
    assert window.explorer_tabs.isHidden()
    assert not window.palette_box.isHidden()
    assert not studio.pane.isHidden()
    window.close()
