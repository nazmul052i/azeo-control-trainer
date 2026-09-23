"""The editor's saved revisions are actual inputs to the existing runner."""
import os
from pathlib import Path
import sys
from copy import deepcopy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QFileDialog, QDialogButtonBox, QWidget

from azeo_control_trainer.app import _startup_surface, _reject_unknown_flags
from azeo_control_trainer.azeo_pa_designer import PADesignerWindow
from azeo_control_trainer.core.presentation.application_style import apply_application_style
from azeo_control_trainer.core.presentation.engineering_dialog import ENGINEERING_QSS
from azeo_control_trainer.core.procedures.authoring import ProcedureDraft, catalog_resolves, library_documents
from azeo_control_trainer.core.procedures.model import load_definition, loop_verification
from azeo_control_trainer.core.procedures.runtime import ProcedureRun
from azeo_control_trainer.core.procedures.library import (
    block_for_type,
    block_library,
    comparison_block_library,
    core_block_library,
)
from azeo_control_trainer.core.strategy.blocks.pid_block import PIDBlock
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph


@pytest.fixture
def editor(tmp_path):
    app = QApplication.instance() or QApplication([])
    apply_application_style(app)
    graph = StrategyGraph("LOOP")
    graph.add_block(PIDBlock("PID"))
    window = PADesignerWindow(tmp_path, graphs_provider=lambda: [graph])
    window.show()
    app.processEvents()
    yield window
    window._saved = window.snapshot()
    window.close()
    window.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def test_launch_surface_and_shared_chrome(editor):
    assert _startup_surface(["--procedures"]) == "procedures"
    assert not _reject_unknown_flags(["--procedures"])
    assert editor.styleSheet() == ENGINEERING_QSS
    assert "LOOP/PID/PV" in editor._catalog_paths
    editor.resize(920, 650)
    QApplication.processEvents()
    assert editor.width() <= 920
    assert editor.minimumSizeHint().width() < 920
    assert all(widget.font().pointSizeF() > 0 for widget in editor.findChildren(QWidget))


def test_every_procedure_dialog_uses_shared_engineering_chrome(editor, tmp_path):
    from azeo_control_trainer.azeo_pa_designer.dialogs import (
        TrialInputsDialog, export_procedure_dialog, unsaved_procedure_dialog,
    )
    from azeo_control_trainer.azeo_pa_designer.logic_editor import LogicEditor
    from azeo_control_trainer.azeo_pa_designer.symbols import SymbolPicker
    from azeo_control_trainer.core.presentation.memory_tag_dialog import MemoryDialog
    from azeo_control_trainer.core.presentation.procedure_help import ProcedureHelpCenter

    dialogs = [
        ProcedureHelpCenter(editor),
        SymbolPicker(editor.draft, editor.tag_database, editor),
        MemoryDialog(None, editor),
        LogicEditor(editor),
        TrialInputsDialog("{}", editor),
        unsaved_procedure_dialog(editor),
        export_procedure_dialog(editor, str(tmp_path / "procedure.html")),
    ]
    try:
        assert all(dialog.styleSheet() == ENGINEERING_QSS for dialog in dialogs)
        assert all(dialog.findChild(QWidget, "configurationHeader") is not None
                   for dialog in dialogs[:5])
        assert dialogs[4].buttons.button(
            QDialogButtonBox.Ok).property("configurationPrimary") is True
        assert dialogs[-1].testOption(QFileDialog.DontUseNativeDialog)
        assert any(button.property("configurationPrimary") is True
                   for button in dialogs[-1].findChildren(QWidget))
    finally:
        for dialog in dialogs:
            dialog.close()
            dialog.deleteLater()
        QApplication.processEvents()


def test_editor_runs_isolated_trial_and_exports_executable_document(editor, tmp_path):
    result = editor.run_isolated_trial({}, tmp_path / "trial.sqlite")
    assert result.execution.status.value == "COMPLETE"
    assert "No controller or SharedDataStore output path" in editor.review.toPlainText()
    target = editor.export_document(tmp_path / "procedure.html")
    text = target.read_text(encoding="utf-8")
    assert "New procedure" in text
    assert editor.draft.definition(editor._catalog_paths).digest in text
    assert {"Run isolated trial", "Export procedure document"} <= set(editor.menu_actions)


def test_edit_reorder_undo_save_reopen_and_runner_accepts(editor, tmp_path):
    editor.properties["name"].setText("Verify flow")
    editor.add_type.setCurrentIndex(editor.add_type.findData("azeo.procedure.delay"))
    editor.add_step()
    editor.fields["delay_sec"].setText("2.5")
    delay = deepcopy(editor.draft.data["steps"][1])
    editor.move_step(1)
    assert editor.draft.data["steps"][2] == delay
    editor.undo()
    assert editor.draft.data["steps"][1] == delay
    editor.redo()
    editor.move_step(-1)
    assert editor.validate_document()
    first = editor.save_revision()
    assert first and first.is_file()
    first_bytes = first.read_bytes()
    editor.fields["description"].setPlainText("Allow the process to settle.")
    second = editor.save_revision()
    assert second != first
    assert first.read_bytes() == first_bytes
    reopened = ProcedureDraft.load(second, tmp_path / "procedures")
    assert reopened.data["steps"][1]["delay_sec"] == 2.5
    definition = load_definition(second, tmp_path / "procedures")
    run = ProcedureRun(tmp_path / "audit.sqlite")
    try:
        run.observe(0.0, {})
        run.start(definition, actor="author", context={})
        assert run.active
    finally:
        assert run.close()


def test_real_tag_mapping_roundtrip_and_proposal_readback(editor, tmp_path):
    editor.add_tag()
    editor.tags.item(0, 0).setText("FLOW.PV")
    editor.tags.item(0, 3).setText("LOOP/PID/PV")
    editor.add_type.setCurrentIndex(editor.add_type.findData("azeo.procedure.wait_until"))
    editor.add_step()
    editor.fields["condition"].setText("FLOW.PV >= 40 and FLOW.PV <= 60")
    editor.fields["stable_for_sec"].setText("5")
    assert editor.validate_document()
    saved = editor.save_revision()
    result = load_definition(saved, tmp_path / "procedures")
    assert result.bindings == {"FLOW.PV": "LOOP/PID/PV"}
    assert result.procedure.steps[1].stable_for_sec == 5
    editor.tags.item(0, 3).setText("LOOP/MISSING/PV")
    assert editor.save_revision() is False
    assert "not found" in editor.status.text()
    assert saved.is_file()


def test_invalid_typed_field_is_retained_and_reported(editor):
    editor.add_type.setCurrentIndex(editor.add_type.findData("azeo.procedure.delay"))
    editor.add_step()
    editor.fields["delay_sec"].setText("not a number")
    before = editor.snapshot()
    assert editor.validate_document() is False
    assert "delay_sec" in editor.review.toPlainText()
    assert editor.snapshot() == before
    assert editor.save_revision() is False
    assert not editor.library.exists()


def test_declared_variable_input_and_duplicate_step(editor):
    editor.add_variable()
    editor.variables.item(0, 0).setText("measured")
    editor.variables.item(0, 1).setText("1.5")
    editor.add_type.setCurrentIndex(editor.add_type.findData("azeo.procedure.operator_input"))
    editor.add_step()
    editor.fields["variable"].setText("measured")
    editor.fields["min_value"].setText("0")
    editor.fields["max_value"].setText("10")
    editor.duplicate_step()
    ids = [step["id"] for step in editor.draft.data["steps"]]
    assert len(ids) == len(set(ids))
    assert editor.validate_document()
    assert editor.draft.data["variables"][0]["value"] == 1.5


def test_close_and_new_do_not_discard_dirty_draft_headlessly(editor):
    editor.properties["name"].setText("Do not discard")
    assert editor.dirty
    assert editor.close() is False
    editor.new_procedure()
    assert editor.draft.data["name"] == "Do not discard"
    assert editor.save_revision()
    assert not editor.dirty
    assert editor.close()


def test_atomic_failure_never_exposes_partial_revision(tmp_path, monkeypatch):
    draft = ProcedureDraft.new()
    original = Path.write_text
    def fail(path, *args, **kwargs):
        if path.name == "procedure.yaml":
            raise OSError("disk full")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", fail)
    with pytest.raises(OSError, match="disk full"):
        draft.save_revision(tmp_path)
    assert list(library_documents(tmp_path / "procedures")) == []
    assert list((tmp_path / "procedures").iterdir()) == []
    assert draft.source is None


def test_new_revision_cannot_inherit_approval_or_mutate_release(tmp_path):
    draft = ProcedureDraft.new()
    draft.data["metadata"].update(approval_status="released", approved_by="approver", released_by="publisher")
    path = draft.save_revision(tmp_path)
    assert draft.data["metadata"]["approval_status"] == "draft"
    assert not draft.data["metadata"]["approved_by"]
    assert load_definition(path, tmp_path / "procedures")
    (tmp_path / ".repository-release.json").write_text("{}")
    with pytest.raises(ValueError, match="immutable"):
        draft.save_revision(tmp_path)


def test_save_retains_original_fields_and_local_references(tmp_path):
    definition = loop_verification("LOOP/PID", 45, 2, 5)
    draft = ProcedureDraft(definition.procedure.model_dump(mode="json"), definition.bindings)
    first = draft.save_revision(tmp_path)
    (first.parent / "sop.txt").write_text("Observed loop commissioning instructions", encoding="utf-8")
    draft.data["references"] = [{"reference_id": "SOP", "title": "Instructions", "path": "sop.txt"}]
    draft.data["steps"][0]["section"] = "Preparation"
    second = draft.save_revision(tmp_path)
    reloaded = ProcedureDraft.load(second, tmp_path / "procedures")
    assert reloaded.data["steps"][0]["section"] == "Preparation"
    assert (second.parent / "sop.txt").read_bytes() == (first.parent / "sop.txt").read_bytes()


def test_loop_template_mode_field_matches_actual_binding_source(editor):
    from azeo_control_trainer.core.hmi.binding.source import LiveGraphSource
    from azeo_control_trainer.core.hmi.binding.result import UNRESOLVED

    definition = loop_verification("LOOP/PID", 45, 2, 5)
    editor.draft = ProcedureDraft(definition.procedure.model_dump(mode="json"), definition.bindings)
    editor.render()
    assert editor.validate_document(), editor.status.text()
    source = LiveGraphSource(lambda: {graph.name: graph for graph in editor.graphs_provider()})
    assert all(source.read(path) is not UNRESOLVED for path in definition.bindings.values())
    assert "LOOP/PID/MODE.ACTUAL" in editor.path_model.stringList()
    assert editor.save_revision()
    editor.draft.bindings["LOOP.MODE"] = "LOOP/PID/PV.ACTUAL"
    assert editor.validate_document() is False


def test_undeclared_tag_and_proposal_without_readback_are_rejected(tmp_path):
    draft = ProcedureDraft.new()
    draft.data["steps"].insert(1, {"id": "wait", "type": "wait_until", "condition": "FLOW.PV > 0"})
    with pytest.raises(ValueError, match="Declare and map"):
        draft.save_revision(tmp_path)
    definition = loop_verification("LOOP/PID", 45, 2, 5)
    draft = ProcedureDraft(definition.procedure.model_dump(mode="json"), definition.bindings)
    draft.data["tags"][1]["access"] = "read_write"
    draft.data["steps"].insert(-1, {"id": "propose", "type": "write_tag", "tag": "LOOP.SP", "value": 50})
    with pytest.raises(ValueError, match="subsequent actual-value wait"):
        draft.save_revision(tmp_path)


def test_fields_are_terminal_fields_not_module_parameter_or_config_fields():
    paths = {"LOOP/PID/PV", "LOOP/PID/CONFIG/GAIN", "LOOP/PARAMETERS/SP"}
    assert catalog_resolves("LOOP/PID/PV.ST", paths)
    assert not catalog_resolves("LOOP/PARAMETERS/SP.ST", paths)
    assert not catalog_resolves("LOOP/PID/CONFIG/GAIN.CV", paths)
    assert catalog_resolves("LOOP/PARAMETERS/SP", paths)


def test_block_library_uses_supported_native_steps_and_shared_vector_icons():
    from azeo_control_trainer.core.presentation.studio_icons import _DRAW_MAP
    from azeo_control_trainer.core.procedures.model import AdvisoryStep

    expected = {"instruction", "operator_confirm", "operator_input", "operator_comment",
                "check", "permissive", "watchdog", "wait_until", "delay", "write_tag",
                "ramp_tag", "read_tag", "calculate", "user_event", "subprocedure", "warning", "alarm", "hold", "abort", "complete"}
    assert {block.step_type for block in core_block_library()} == expected
    assert len(comparison_block_library()) == 38
    assert len(block_library()) == len(core_block_library()) + 38
    for block in block_library():
        step = AdvisoryStep.model_validate(block.instantiate("test"))
        assert block.icon in _DRAW_MAP
        assert step.type == block.step_type
        assert step.library_block_id == block.block_id
        assert step.library_block_version == "1.0.0"
        assert block.source_version == "1.0.0"
    assert all(not AdvisoryStep.model_validate(block.instantiate("test")).audible
               for block in core_block_library())
    for block in core_block_library():
        assert block.source_id == f"core.{block.step_type}"
    event = block_for_type("user_event")
    event.instantiate("a")["event_payload"]["state"] = "edited"
    assert event.instantiate("b")["event_payload"]["state"] == "active"


def test_search_select_insert_duplicate_and_save_library_identity(editor, tmp_path):
    from PySide6.QtCore import Qt

    editor.library_tabs.setCurrentIndex(1)
    editor.block_search.setText("azeo.procedure.delay")
    visible = [editor.block_tree.topLevelItem(i).child(j)
               for i in range(editor.block_tree.topLevelItemCount())
               for j in range(editor.block_tree.topLevelItem(i).childCount())
               if not editor.block_tree.topLevelItem(i).child(j).isHidden()]
    assert len(visible) == 1
    assert visible[0].data(0, Qt.UserRole) == "azeo.procedure.delay"
    editor.block_tree.setCurrentItem(visible[0])
    assert editor.add_block_button.isEnabled()
    editor.add_block_button.click()
    assert editor.draft.data["steps"][1]["library_block_id"] == "azeo.procedure.delay"
    assert "1.0.0" in editor.block_identity.text()
    assert editor.block_identity.isReadOnly()
    editor.duplicate_step()
    path = editor.save_revision()
    definition = load_definition(path, tmp_path / "procedures")
    assert all(step.library_block_id.startswith("azeo.procedure.") for step in definition.procedure.steps)
    assert definition.procedure.steps[1].library_block_id == definition.procedure.steps[2].library_block_id
    editor.block_search.setText("nothing matches")
    assert not editor.add_block_button.isEnabled()


@pytest.mark.parametrize("change", [
    {"library_block_version": "99.0.0"}, {"library_block_id": "azeo.procedure.missing"},
    {"library_block_id": "azeo.procedure.complete"},
])
def test_editor_and_runner_reject_mismatched_azeo_block_identity(tmp_path, change):
    draft = ProcedureDraft.new()
    draft.data["steps"][0].update(change)
    with pytest.raises(ValueError, match="block identity/version"):
        draft.save_revision(tmp_path)


def test_inspector_only_exposes_timing_that_the_runner_uses(editor):
    for kind in ("check", "watchdog", "instruction", "warning", "operator_confirm"):
        editor.add_type.setCurrentIndex(editor.add_type.findData(f"azeo.procedure.{kind}"))
        editor.add_step()
        assert "timeout_sec" not in editor.fields
        assert "poll_sec" not in editor.fields
        if kind in {"instruction", "warning"}:
            assert "require_confirmation" in editor.fields
        else:
            assert "require_confirmation" not in editor.fields
    editor.add_type.setCurrentIndex(editor.add_type.findData("azeo.procedure.wait_until"))
    editor.add_step()
    assert {"timeout_sec", "poll_sec", "stable_for_sec", "on_timeout"} <= editor.fields.keys()
