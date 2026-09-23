"""Equipment packages must keep every declared reference on the chosen machine."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from azeo_control_trainer.core.hmi.pvms.engineering import (
    control_roots, remap_controls, starter_assemblies,
)
from azeo_control_trainer.core.hmi.pvms.machines import VFDSpeedPvm
from azeo_control_trainer.core.strategy.model.block_registry import BlockRegistry
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
import azeo_control_trainer.core.strategy.blocks  # noqa: F401


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def equipment_graphs():
    result = {}
    for module in ("PUMP_A", "PUMP_B"):
        graph = StrategyGraph(module)
        graph.description = "Cooling water transfer pump"
        for kind, name in (("PID", "SPEED"), ("DEVCTL", "RUN"), ("AI", "LEVEL"), ("AO", "VALVE")):
            graph.add_block(BlockRegistry().create(kind, name))
        result[module] = graph
    return result


def machine_document():
    return {"pvms": [VFDSpeedPvm().place(
        "drive", x=20, y=30, path="PUMP_A/SPEED", device="PUMP_A/RUN").to_dict()]}


def test_machine_mapping_discovers_and_updates_both_declared_references():
    original = machine_document()
    before = copy.deepcopy(original)
    assert control_roots(original) == ["PUMP_A/RUN", "PUMP_A/SPEED"]
    result = remap_controls(original, {"PUMP_A/SPEED": "PUMP_B/SPEED", "PUMP_A/RUN": "PUMP_B/RUN"}, equipment_graphs())
    assert result["pvms"][0]["params"] == {"path": "PUMP_B/SPEED", "device": "PUMP_B/RUN"}
    assert original == before


def test_machine_rejects_wrong_or_missing_run_control_before_placement():
    with pytest.raises(ValueError, match="requires DEVCTL"):
        remap_controls(machine_document(), {"PUMP_A/RUN": "PUMP_B/LEVEL"}, equipment_graphs())
    broken = machine_document()
    broken["pvms"][0]["params"]["device"] = ""
    with pytest.raises(ValueError, match="Run control"):
        remap_controls(broken, {}, equipment_graphs())


def test_swapping_two_machines_does_not_remap_the_secondary_reference_twice():
    document = machine_document()
    second = copy.deepcopy(document["pvms"][0])
    second.update(id="second", params={"path": "PUMP_B/SPEED", "device": "PUMP_B/RUN"})
    document["pvms"].append(second)
    mapping = {f"PUMP_{a}/{member}": f"PUMP_{b}/{member}"
               for a, b in (("A", "B"), ("B", "A")) for member in ("SPEED", "RUN")}
    result = remap_controls(document, mapping, equipment_graphs())
    assert result["pvms"][0]["params"] == {"path": "PUMP_B/SPEED", "device": "PUMP_B/RUN"}
    assert result["pvms"][1]["params"] == {"path": "PUMP_A/SPEED", "device": "PUMP_A/RUN"}


def test_expression_named_inputs_and_history_follow_mapping_without_rewriting_labels():
    document = {"items": [{"text": "PUMP_A/SPEED/PV", "id": "PUMP_A/RUN",
                           "series_path": "PUMP_A/SPEED/PV",
                           "props": {"value": {"kind": "expression", "expr": "pv * 2",
                                                "refs": {"pv": "PUMP_A/SPEED/PV"}}}}]}
    mapped = remap_controls(document, {"PUMP_A/SPEED": "PUMP_B/SPEED"}, equipment_graphs())
    item = mapped["items"][0]
    assert item["series_path"] == "PUMP_B/SPEED/PV"
    assert item["props"]["value"]["refs"]["pv"] == "PUMP_B/SPEED/PV"
    assert item["text"] == "PUMP_A/SPEED/PV" and item["id"] == "PUMP_A/RUN"


def test_equipment_starters_include_explicit_speed_and_run_contracts():
    starters = starter_assemblies()
    for document in starters.values():
        assert all(pvm["w"] > 0 and pvm["h"] > 0 for pvm in document["pvms"])
    for name in ("Pump and VFD", "Compressor and speed control", "Turbine and speed control"):
        document = starters[name]
        assert len(control_roots(document)) == 2
        assert any(pvm["params"].get("device") for pvm in document["pvms"])
    assert any(item.get("kind") == "pipe" for item in starters["Vessel and outlet"]["items"])


@pytest.fixture
def equipment_window(tmp_path, app):
    from azeo_control_trainer.azeo_graphics_designer.window import HmiStudioWindow
    graphs = equipment_graphs()
    window = HmiStudioWindow(lambda: graphs, tmp_path / "displays")
    yield window, graphs
    window.close()
    window.deleteLater()
    app.processEvents()


def mapped_dialog(window, name="Pump and VFD"):
    from azeo_control_trainer.azeo_graphics_designer.engineering_tools import AssemblyDialog
    dialog = AssemblyDialog(window.current())
    dialog.source.setCurrentIndex(dialog.source.findText(name))
    assign(dialog, {"RUN/DEV": "PUMP_A/RUN", "SPEED/PID": "PUMP_A/SPEED",
                    "LEVEL/AI": "PUMP_A/LEVEL", "VALVE/AO": "PUMP_A/VALVE"})
    return dialog


def assign(dialog, mapping):
    for row in range(dialog.mapping.rowCount()):
        dialog.mapping.cellWidget(row, 1).setCurrentText(mapping[dialog.mapping.item(row, 0).text()])


def test_compatible_choices_preview_and_checked_write_boundary(equipment_window):
    window, graphs = equipment_window
    dialog = mapped_dialog(window)
    for row in range(dialog.mapping.rowCount()):
        combo = dialog.mapping.cellWidget(row, 1)
        options = [combo.itemText(i) for i in range(combo.count()) if combo.itemText(i)]
        root = dialog.mapping.item(row, 0).text()
        assert len(options) == 2
        assert all(path.endswith("/RUN" if root == "RUN/DEV" else "/SPEED") for path in options)
    before = window.current()._document()
    document = dialog.preview()
    assert dialog.apply_button.isEnabled()
    assert document["pvms"][-1]["params"]["device"] == "PUMP_A/RUN"
    assert not dialog.preview_view.engine._source.can_write("PUMP_A/SPEED/SP").success
    assert not dialog.preview_view._timer.isActive()
    for index in range(dialog.theme.count()):
        dialog.theme.setCurrentIndex(index)
    assert window.current()._document() == before
    retained = dialog.preview_view
    dialog.close()
    assert retained._disposed


def test_duplicate_selected_machine_maps_all_references_and_undo_is_one_operation(equipment_window):
    window, _ = equipment_window
    studio = window.current()
    dialog = mapped_dialog(window)
    dialog.position_x.setValue(40)
    dialog.position_y.setValue(80)
    dialog.preview()
    dialog.apply_button.click()
    original = copy.deepcopy(studio._document())
    prior_undo = len(studio._undo_stack)
    dialog.use_selection()
    assign(dialog, {"PUMP_A/RUN": "PUMP_B/RUN", "PUMP_A/SPEED": "PUMP_B/SPEED"})
    dialog.preview()
    dialog.apply_button.click()
    after = studio._document()
    assert len(after["pvms"]) == 4
    assert len(studio._undo_stack) == prior_undo + 1
    before_ids = {pvm["id"] for pvm in original["pvms"]}
    copies = [pvm for pvm in after["pvms"] if pvm["id"] not in before_ids]
    assert all(path.startswith("PUMP_B/") for pvm in copies for path in pvm["params"].values())
    assert {pvm["group"] for pvm in copies}.isdisjoint({pvm["group"] for pvm in original["pvms"]})
    studio.undo()
    assert studio._document() == original
    dialog.close()


def test_selected_remap_preserves_other_equipment_and_manual_pipe_geometry(equipment_window):
    window, _ = equipment_window
    studio = window.current()
    dialog = mapped_dialog(window, "Vessel and outlet")
    dialog.preview()
    dialog.apply()
    selected_ids = {studio._endpoint_id(item) for item in studio.selection.snapshot().items}
    extra = studio.add_static("text", 700, 200, text="Keep this label")
    studio.selection.replace([item for item in studio._document_items() if studio._endpoint_id(item) in selected_ids])
    pipe = studio._pipe_items()[0]
    pipe.data.update(route_mode="manual", route_points=[[120, 360], [280, 360]])
    studio.mark_unsaved()
    original = studio._document()
    dialog.source.setCurrentIndex(dialog.source.findData(dialog.REMAP))
    assign(dialog, {"PUMP_A/LEVEL": "PUMP_B/LEVEL", "PUMP_A/VALVE": "PUMP_B/VALVE"})
    dialog.preview()
    dialog.apply()
    after = studio._document()
    assert len(after["pvms"]) == 2
    assert next(item for item in after["items"] if item["id"] == extra.data["id"])["text"] == "Keep this label"
    assert next(item for item in after["items"] if item["kind"] == "pipe")["route_points"] == [[120, 360], [280, 360]]
    studio.undo()
    assert studio._document() == original
    dialog.close()


def test_changed_controller_invalidates_preview_before_canvas_mutation(equipment_window):
    window, graphs = equipment_window
    dialog = mapped_dialog(window)
    dialog.preview()
    before = window.current()._document()
    undo = len(window.current()._undo_stack)
    graphs["PUMP_A"].blocks.clear()
    with pytest.raises(ValueError, match="existing control block"):
        dialog.apply()
    assert not dialog.apply_button.isEnabled()
    assert window.current()._document() == before
    assert len(window.current()._undo_stack) == undo
    dialog.close()


def test_each_mapping_row_reports_its_own_missing_reference(equipment_window):
    window, _ = equipment_window
    dialog = mapped_dialog(window)
    assign(dialog, {"RUN/DEV": "", "SPEED/PID": "MISSING/SPEED"})
    with pytest.raises(ValueError):
        dialog.preview()
    assert all("existing control block" in dialog.mapping.item(row, 2).text()
               for row in range(dialog.mapping.rowCount()))
    assert not dialog.apply_button.isEnabled()
    dialog.close()


def test_failed_selection_reload_cannot_apply_a_previous_template(equipment_window):
    window, _ = equipment_window
    dialog = mapped_dialog(window)
    dialog.preview()
    dialog.use_selection()
    assert not dialog.apply_button.isEnabled()
    with pytest.raises(ValueError, match="Reload"):
        dialog.preview()
    dialog.close()


def test_shared_browser_filters_family_and_searches_equipment_description(app):
    from azeo_control_trainer.azeo_graphics_designer.param_browser import ParameterBrowserDialog
    graphs = equipment_graphs()
    picker = ParameterBrowserDialog(lambda: graphs, block_types={"DEVCTL"})
    picker.search.setText("cooling transfer")
    assert picker.result_count.text() == "2 matching objects"
    picker._select_path("PUMP_A/SPEED")
    assert not picker.ok_button.isEnabled()
    picker._select_path("PUMP_B/RUN")
    assert picker.ok_button.isEnabled() and picker.selected_path == "PUMP_B/RUN"
    picker.search.setText("no matching equipment")
    assert not picker.ok_button.isEnabled() and not picker.selected_path
    picker.search.clear()
    picker.scope.setCurrentIndex(picker.scope.findData("all"))
    picker._select_path("PUMP_B/RUN/RUNNING")
    assert "Quality:" in picker.value_label.text()
    picker.setAttribute(Qt.WA_DeleteOnClose)
    picker.close()


def test_vessel_duplicate_preserves_ports_translates_bends_and_round_trips(equipment_window):
    window, _ = equipment_window
    studio = window.current()
    dialog = mapped_dialog(window, "Vessel and outlet")
    dialog.preview()
    dialog.apply()
    pipe = studio._pipe_items()[0]
    pipe.data.update(route_mode="manual", route_points=[[260, 320], [390, 320]])
    studio.mark_unsaved()
    original = studio._document()
    dialog.use_selection()
    assign(dialog, {"PUMP_A/LEVEL": "PUMP_B/LEVEL", "PUMP_A/VALVE": "PUMP_B/VALVE"})
    dialog.position_x.setValue(dialog.position_x.value() + 500)
    dialog.preview()
    dialog.apply()
    after = studio._document()
    old_ids = {item["id"] for item in original["items"]}
    copied_pipe = next(item for item in after["items"] if item["kind"] == "pipe" and item["id"] not in old_ids)
    old_vessel = next(item for item in original["pvms"] if item["variant"] == "vessel")
    new_vessel = next(item for item in after["pvms"] if item["params"]["path"] == "PUMP_B/LEVEL")
    dx, dy = new_vessel["x"] - old_vessel["x"], new_vessel["y"] - old_vessel["y"]
    assert copied_pipe["a"] == new_vessel["id"]
    assert copied_pipe["a_side"] == pipe.data["a_side"] and copied_pipe["b_side"] == pipe.data["b_side"]
    assert copied_pipe["route_points"] == [[260 + dx, 320 + dy], [390 + dx, 320 + dy]]
    assert studio.save_draft()
    stored = studio.store.load_draft(studio.display.name).to_dict()
    assert any(item.get("route_points") == copied_pipe["route_points"] for item in stored["items"])
    dialog.close()
