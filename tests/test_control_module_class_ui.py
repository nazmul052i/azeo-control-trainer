from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

import azeo_control_trainer.core.strategy.blocks  # noqa: F401
from azeo_control_trainer.azeo_control_designer.dialogs.module_class_manager import (
    ModuleClassDefinitionDialog,
    ModuleClassManagerDialog,
    ModuleClassUpdateReviewDialog,
    ModuleInstanceCreationDialog,
)
from azeo_control_trainer.core.strategy.composites import PublicParameter
from azeo_control_trainer.core.strategy.module_classes import (
    ModuleClassLibrary,
    analyze_instance,
)
from azeo_control_trainer.core.strategy.serialization import strategy_io
from azeo_control_trainer.core.strategy.serialization.strategy_io import (
    graph_from_document,
)


@pytest.fixture(scope="module", autouse=True)
def app():
    return QApplication.instance() or QApplication([])


def _document():
    return {
        "name": "LOOP-101",
        "blocks": [{
            "id": "constant", "block_type": "CONSTANT",
            "instance_name": "K-101", "x": 10.0, "y": 20.0,
            "config": {"value": 1.0, "label": "Reference"},
        }],
        "wires": [],
    }


class _ProjectTree:
    def __init__(self):
        self.registered = []
        self.refreshes = 0

    def register_control_modules(self, paths, area_id=None, unit_name=None):
        self.registered.append((list(paths), area_id, unit_name))

    def refresh(self):
        self.refreshes += 1

    @staticmethod
    def areas():
        return [{
            "area_id": "area-a",
            "name": "Process Area",
            "units": [{"name": "U100", "modules": []}],
        }]


def _canvas(document, path=None):
    graph, _comments = graph_from_document(document, strict=True)
    return SimpleNamespace(
        scene=SimpleNamespace(graph=graph, get_comments_data=lambda: []),
        runtime=None,
        file_path=str(path) if path else None,
    )


def test_manager_creates_updates_reviews_and_instantiates_module_class(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(strategy_io, "STRATEGY_DIR", tmp_path)
    control = tmp_path / "control"
    control.mkdir()
    source = _canvas(_document(), control / "LOOP-101.json")
    project_tree = _ProjectTree()
    library = ModuleClassLibrary(tmp_path / "_module_classes")
    manager = ModuleClassManagerDialog(
        project_tree, [source], active_canvas=source, library=library)

    def apply_document(canvas, document, _reason):
        graph, _comments = graph_from_document(document, strict=True)
        canvas.scene.graph = graph

    manager.documentRequested.connect(apply_document)
    manager.create_from_active(
        "ANALOG_LOOP", "Standard loop", [PublicParameter(
            "Setpoint", "K-101/CONFIG/value", "FLOAT", 1.0)])

    assert manager.class_count_metric.text() == "1"
    assert manager.instance_count_metric.text() == "1"
    manager.class_search.setText("missing class")
    assert manager.classes.item(0).isHidden()
    manager.class_search.clear()
    assert not manager.classes.item(0).isHidden()
    assert analyze_instance(source.scene.graph.to_dict(), library).state == "current"
    source.scene.graph.blocks["constant"].config.params["label"] = "Revised"
    updated = manager.update_from_active(note="Reviewed revision")
    assert updated.revision == 2
    assert analyze_instance(source.scene.graph.to_dict(), library).state == "stale"
    with pytest.raises(ValueError, match="Adopt the current"):
        manager.update_from_active(note="Must not skip r2")
    with pytest.raises(ValueError, match="Adopt the current"):
        manager.set_active_overrides({"Setpoint": 9.0})

    plan = manager.update_plan()
    review = ModuleClassUpdateReviewDialog(plan)
    assert not plan.has_conflicts
    assert review.windowTitle().startswith("Review Class Update")
    manager.refresh_active(preserve_deviations=True)
    assert analyze_instance(source.scene.graph.to_dict(), library).state == "current"

    manager.set_active_overrides({"Setpoint": 3.5})
    assert source.scene.graph.blocks["constant"].config.params["value"] == 3.5
    created = manager.create_instance(
        "LOOP-202", overrides={"Setpoint": 2.0},
        area_id="area-a", unit_name="U100",
        target_directory=control)
    created_graph, _comments = strategy_io.load_strategy(
        created, remember=False)
    assert created_graph.name == "LOOP-202"
    assert created_graph.blocks["constant"].config.params["value"] == 2.0
    assert project_tree.registered[-1][0] == [created]
    assert project_tree.registered[-1][1:] == ("area-a", "U100")
    with pytest.raises(ValueError, match="already exists"):
        manager.create_instance(
            "LOOP-202", target_directory=tmp_path / "other")

    definition_dialog = ModuleClassDefinitionDialog(
        source.scene.graph.to_dict(), definition=updated)
    assert definition_dialog.parameters.rowCount() >= 2
    assert not (definition_dialog.parameters.item(0, 3).flags()
                & Qt.ItemIsEditable)
    definition_dialog.exposed_only.setChecked(True)
    assert definition_dialog.public_count.text().startswith("1 exposed")

    instance_dialog = ModuleInstanceCreationDialog(
        updated, project_tree.areas(), existing_names={"LOOP-101"})
    instance_dialog.name_edit.setText("LOOP-101")
    assert not instance_dialog.create_button.isEnabled()
    assert "already exists" in instance_dialog._error.text()
    instance_dialog.name_edit.setText("LOOP-303")
    instance_dialog.unit_combo.setCurrentIndex(1)
    assert instance_dialog.create_button.isEnabled()
    assert instance_dialog.area_id() == "area-a"
    assert instance_dialog.unit_name() == "U100"
    instance_dialog.table.item(0, 0).setCheckState(Qt.Checked)
    instance_dialog.table.item(0, 4).setText("4.25")
    assert instance_dialog.overrides() == {"Setpoint": 4.25}
    instance_dialog.close()
    definition_dialog.close()
    review.close()
    manager.close()


def test_manager_refuses_class_adoption_while_module_is_online(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(strategy_io, "STRATEGY_DIR", tmp_path)
    canvas = _canvas(_document())
    project_tree = _ProjectTree()
    library = ModuleClassLibrary(tmp_path / "_module_classes")
    manager = ModuleClassManagerDialog(
        project_tree, [canvas], active_canvas=canvas, library=library)
    definition = manager.create_from_active("LOOP_CLASS")
    changed = definition.graph.copy()
    changed["description"] = "revision"
    library.update(definition.id, expected_revision=1, graph=changed)
    manager.reload()
    canvas.runtime = SimpleNamespace(is_online=True)

    with pytest.raises(ValueError, match="offline"):
        manager.refresh_active()
    with pytest.raises(ValueError, match="offline"):
        manager.set_active_overrides({})

    manager.close()
