import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTableWidgetItem, QWidget

from azeo_control_trainer.core.hmi.pvms.elements import Action
from azeo_control_trainer.azeo_graphics_designer.studio.binding_editor import BindingCatalog
from azeo_control_trainer.azeo_graphics_designer.studio.structured_editors import (
    ActionListDialog,
    DataElementDialog,
    UserEntryDialog,
)
from azeo_control_trainer.azeo_graphics_designer.studio.panes import _ConfigPane


def _app():
    return QApplication.instance() or QApplication([])


def test_procedure_workflow_tuning_and_history_fields_roundtrip():
    from azeo_control_trainer.core.hmi.pvms.elements import validate_data_element, element_paths
    _app()
    for source, extra in (("STEPS", {"presentation": "workflow", "row_help": True}),
                           ("PARAMETERS", {"row_action": "tune", "command_context": "@procedure/{ProcedureRef}/CONTEXT"})):
        data = dict(kind="table", columns=[dict(key="id", title="ID")], rows=[],
                    rows_path="@procedure/{ProcedureRef}/" + source, **extra)
        dialog = DataElementDialog(data, BindingCatalog())
        result = dict(data, **dialog._payload())
        assert not validate_data_element(result)
        assert all(result[key] == value for key, value in extra.items())
        dialog.close()
    data = dict(kind="chart", pens=[], series_path="@procedure/{ProcedureRef}/TREND")
    dialog = DataElementDialog(data, BindingCatalog())
    result = dict(data, **dialog._payload())
    assert not validate_data_element(result)
    assert element_paths(result) == (data["series_path"],)
    dialog.close()


def test_table_editor_preserves_bound_cell_descriptor():
    _app()
    dialog = DataElementDialog(
        {
            "kind": "table",
            "columns": [{"key": "pv", "title": "PV"}],
            "rows": [{"pv": {"path": "M/B/PV", "type": "numeric"}}],
            "extension": "kept",
        },
        BindingCatalog(),
    )
    assert dialog.property("authoringDialog") is True
    assert dialog.findChild(QWidget, "authoring_dialog_header") is not None
    candidate = dialog._payload()
    assert candidate["rows"][0]["pv"] == {
        "path": "M/B/PV",
        "type": "numeric",
    }
    merged = dialog.original.copy()
    merged.update(candidate)
    assert merged["extension"] == "kept"


def test_user_entry_editor_builds_typed_option_rows():
    _app()
    dialog = UserEntryDialog(
        {"kind": "combo_box", "path": "M/B/MODE", "extension": "kept"},
        BindingCatalog(),
    )
    dialog.options.insertRow(0)
    dialog.options.setItem(0, 0, QTableWidgetItem("1"))
    dialog.options.setItem(0, 1, QTableWidgetItem("Run"))
    dialog._accept()
    assert dialog.result_entry().options == ((1, "Run"),)
    assert dialog.result_entry_data()["extension"] == "kept"


def test_action_editor_does_not_offer_dead_drag_event():
    _app()
    dialog = ActionListDialog([], BindingCatalog())
    dialog._append(Action(kind="open_display", target="Overview"))
    event = dialog.table.cellWidget(0, 0)
    assert event.findText("drag", Qt.MatchExactly) == -1


def test_structured_commit_is_one_checkpoint_and_one_rebind():
    class Item:
        data = {"kind": "chart", "pens": [{"path": "OLD/B/PV"}]}

        def update(self):
            calls.append("update")

    class Studio:
        def checkpoint(self):
            calls.append("checkpoint")

        def rebind_static(self, item):
            calls.append("rebind")

        def mark_unsaved(self):
            calls.append("dirty")

    calls = []

    class Pane:
        pass

    pane = Pane()
    pane.studio = Studio()
    pane._current_static = Item()
    pane.show_item = lambda item: calls.append("refresh")
    _ConfigPane._commit_structured_item(pane, {"kind": "chart", "pens": [{"path": "NEW/B/PV"}]})
    assert calls == ["checkpoint", "rebind", "dirty", "update", "refresh"]
