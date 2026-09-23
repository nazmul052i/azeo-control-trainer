"""Authoring selects actual Tag DB points and commits logic as one edit."""
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QDialog
from azeo_control_trainer.azeo_pa_designer import create_window
from azeo_control_trainer.core.presentation.application_style import apply_application_style
from azeo_control_trainer.core.strategy.blocks.io_blocks import AIBlock
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
from azeo_control_trainer.core.procedures.authoring import ProcedureDraft


@pytest.fixture
def editor(tmp_path):
    app = QApplication.instance() or QApplication([])
    apply_application_style(app)
    graph = StrategyGraph("VESSEL-100")
    graph.add_block(AIBlock("LEVEL"))
    window = create_window(tmp_path, graphs_provider=lambda: [graph])
    window.show()
    app.processEvents()
    yield window
    window._saved = window.snapshot()
    window.close()
    window.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def add_wait(editor):
    editor.add_type.setCurrentIndex(editor.add_type.findData("azeo.procedure.wait_until"))
    editor.add_step()


def test_search_in_expression_maps_actual_tag_and_undo_removes_mapping(editor):
    from azeo_control_trainer.core.presentation.tagdb_browser import TagDatabaseBrowser
    add_wait(editor)
    field = editor.fields["condition"]
    field.setText("0 < level and True")
    field.setSelection(4, 5)
    before = editor.snapshot()
    field._browse_symbol()
    picker = editor.symbol_picker
    assert isinstance(picker.browser, TagDatabaseBrowser)
    picker.browser._search.setText("VESSEL-100/LEVEL/OUT")
    picker.browser._populate()
    assert "VESSEL-100/LEVEL/OUT" in picker.browser._rows
    picker.choose_tag("VESSEL-100/LEVEL/OUT")
    assert len(editor.draft.bindings) == 1
    token = next(iter(editor.draft.bindings))
    assert editor.draft.bindings[token] == "VESSEL-100/LEVEL/OUT"
    assert editor.fields["condition"].text() == f"0 < {token} and True"
    assert editor.validate_document()
    editor.undo()
    assert editor.snapshot() == before


def test_memory_created_from_input_picker_is_typed_bounded_and_cancellable(editor):
    editor.tag_database.memory.create(dict(name="existing", data_type="float", value=5))
    editor.tag_database.reload_memory()
    editor.add_type.setCurrentIndex(editor.add_type.findData("azeo.procedure.operator_input"))
    editor.add_step()
    editor.fields["variable"]._browse_symbol()
    picker = editor.symbol_picker
    assert picker.browser is None
    picker.create_memory()
    memory = picker.memory_dialog
    memory.name.setText("shutdown_limit")
    memory.dtype.setCurrentText("float")
    memory.value.setText("20")
    memory.minimum.setText("0")
    memory.maximum.setText("100")
    memory.apply()
    assert picker.memory.count() == 2
    picker.choose_memory()
    assert editor.draft.data["steps"][1]["variable"] == "shutdown_limit"
    assert editor.draft.data["variables"][0]["max_value"] == 100
    assert editor.validate_document()
    before = editor.snapshot()
    editor.fields["variable"]._browse_symbol()
    editor.symbol_picker.create_memory()
    memory = editor.symbol_picker.memory_dialog
    memory.name.setText("discarded")
    memory.apply()
    editor.symbol_picker.reject()
    assert editor.snapshot() == before
    # Saving a tag is independent of accepting a reference in a PA block.
    assert editor.tag_database.memory.read("MEMORY/discarded/VALUE") == 0


def test_logic_worksheet_adds_timed_rows_and_calculations_as_one_undo(editor, tmp_path):
    editor.draft.data["variables"] = [dict(name="limit", value=20, data_type="float"),
                                     dict(name="threshold", value=0, data_type="float")]
    add_wait(editor)
    before = editor.snapshot()
    editor.open_logic_editor()
    worksheet = editor.logic_editor
    worksheet.conditions.cellWidget(0, 1).setText("limit > 0")
    worksheet.conditions.item(0, 2).setText("3")
    worksheet.add_condition(dict(id="C2", expression="threshold < limit", stable_for_sec=2))
    worksheet.add_calculation(dict(variable="threshold", expression="limit * 0.5"))
    worksheet.logic.setText("C1 and C2")
    worksheet.dwell.setText("4")
    worksheet.timeout.setText("30")
    worksheet.apply()
    assert worksheet.result() == QDialog.Accepted, worksheet.error.text()
    assert len(editor.draft.data["steps"][1]["condition_rows"]) == 2
    assert editor.fields["condition"].isReadOnly()
    assert editor.validate_document()
    path = editor.save_revision()
    loaded = ProcedureDraft.load(path, tmp_path / "procedures").definition()
    step = loaded.procedure.steps[1]
    assert step.stable_for_sec == 4
    assert step.condition_rows[0].stable_for_sec == 3
    assert step.calculation_rows[0].variable == "threshold"
    editor.undo()
    # Saving adds revision metadata, but the one worksheet edit restores its
    # original block and its bindings in one undo operation.
    assert editor.draft.data["steps"] == before[0]["steps"]
    assert editor.draft.bindings == before[1]


def test_calculation_cells_share_catalog_model_and_validate_unknown_results(editor):
    add_wait(editor)
    editor.open_logic_editor()
    worksheet = editor.logic_editor
    worksheet.add_condition(dict(id="C2", expression="True"))
    a = worksheet.conditions.cellWidget(0, 1)
    b = worksheet.conditions.cellWidget(1, 1)
    assert a._symbol_model is b._symbol_model
    a.setText("True")
    worksheet.add_calculation(dict(variable="undeclared", expression="1"))
    worksheet.apply()
    assert worksheet.result() != QDialog.Accepted
    assert "memory" in worksheet.error.text().lower()
    worksheet.reject()


def test_automatic_completion_inserts_symbol_without_replacing_expression(editor):
    add_wait(editor)
    field = editor.fields["condition"]
    field.setText("1 < VESS")
    field.setCursorPosition(len(field.text()))
    field._symbol_completer.activated[str].emit("VESSEL-100/LEVEL/OUT · Vessel level")
    token = next(iter(editor.draft.bindings))
    assert editor.fields["condition"].text() == f"1 < {token}"
    assert editor.draft.data["tags"][0]["access"] == "read"
    assert editor.fields["condition"].font().pointSizeF() > 0
    assert editor.tag_database.lookup("VESSEL-100/LEVEL/OUT").kind == "terminal"
