"""PA Designer engineering workflows are semantic, immutable and reachable."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from azeo_control_trainer.azeo_pa_designer.extraction import extract_selection
from azeo_control_trainer.azeo_pa_designer.new_procedure import TEMPLATES, procedure_from_template
from azeo_control_trainer.azeo_pa_designer.problems import collect_findings
from azeo_control_trainer.azeo_pa_designer.refactoring import find_usages, rename_symbol
from azeo_control_trainer.azeo_pa_designer.revisions import compare_documents
from azeo_control_trainer.azeo_pa_designer.window import PADesignerWindow
from azeo_control_trainer.core.procedures.authoring import ProcedureDraft
from azeo_control_trainer.core.procedures.governance import read_governance, revision_digest, transition_revision
from azeo_control_trainer.core.procedures.model import AdvisoryProcedure
from azeo_control_trainer.core.presentation.engineering_dialog import ENGINEERING_QSS


def test_safe_rename_updates_semantic_references_without_touching_strings():
    draft = ProcedureDraft.new()
    draft.data["tags"] = [{"tag": "UNIT.PV", "data_type": "float", "access": "read"}]
    draft.bindings = {"UNIT.PV": "U100/AI/PV"}
    draft.data["variables"] = [{"name": "limit", "data_type": "float", "value": 10}]
    draft.data["steps"][0].update(condition="UNIT.PV > limit", description="Keep literal 'UNIT.PV' unchanged")
    assert len(find_usages(draft.data, draft.bindings, "tag", "UNIT.PV")) == 3
    data, bindings, _ = rename_symbol(draft.data, draft.bindings, "tag", "UNIT.PV", "UNIT.FLOW")
    assert data["steps"][0]["condition"] == "UNIT.FLOW > limit"
    assert "'UNIT.PV'" in data["steps"][0]["description"]
    assert bindings == {"UNIT.FLOW": "U100/AI/PV"}
    data, bindings, _ = rename_symbol(data, bindings, "variable", "limit", "high_limit")
    assert data["steps"][0]["condition"] == "UNIT.FLOW > high_limit"


def test_structured_problems_identify_exact_step_and_property():
    draft = ProcedureDraft.new()
    draft.data["steps"][0]["timeout_sec"] = "not-a-number"
    findings = collect_findings(draft)
    assert any(row.severity == "error" and row.location_kind == "step" and row.location_id == "review"
               and row.property_name == "timeout_sec" for row in findings)


@pytest.mark.parametrize("template", sorted(TEMPLATES))
def test_new_procedure_templates_produce_strict_documents(template):
    draft = procedure_from_template(template, f"test_{template}", f"Test {template}")
    AdvisoryProcedure.model_validate(draft.data)
    draft.definition()


def test_revision_compare_reports_mapping_and_execution_impact():
    before = ProcedureDraft.new()
    after = ProcedureDraft.new()
    after.data["steps"][0]["description"] = "Changed operator direction"
    after.data["tags"] = [{"tag": "UNIT.PV", "access": "read", "data_type": "float"}]
    after.bindings = {"UNIT.PV": "UNIT/AI/PV"}
    changes = compare_documents(before.data, before.bindings, after.data, after.bindings)
    assert any(row.area == "Steps" and row.identity == "review" and row.impact for row in changes)
    assert any(row.area == "Mappings" and row.identity == "UNIT.PV" for row in changes)


def test_governance_sidecar_never_rewrites_revision_and_detects_tampering(tmp_path):
    draft = ProcedureDraft.new()
    draft.data["metadata"]["author"] = "Author"
    path = draft.save_revision(tmp_path)
    original = path.read_bytes()
    digest = revision_digest(path)
    transition_revision(path, "review", actor="Reviewer", role="Operations", reason="Checked procedure")
    transition_revision(path, "approved", actor="Approver", role="Engineering", reason="Approved for training")
    transition_revision(path, "released", actor="Releaser", role="Configuration", reason="Released to stations")
    record = read_governance(path)
    assert record["status"] == "released"
    assert record["content_digest"] == digest
    assert path.read_bytes() == original
    assert len({event["event_hash"] for event in record["events"]}) == 3
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        read_governance(path)


def test_extract_selection_saves_child_before_replacing_parent(tmp_path):
    parent = ProcedureDraft.new()
    child, path, steps, call_id = extract_selection(
        parent, {"review", "record"}, tmp_path,
        procedure_id="shared_review", name="Shared review", author="Engineer",
    )
    assert path.is_file()
    assert [row["id"] for row in steps] == [call_id, "complete"]
    parent.data["steps"] = steps
    definition = parent.definition()
    assert definition.procedure.steps[0].id.startswith(call_id + "/")
    assert child.data["metadata"]["approval_status"] == "draft"


def test_annotations_persist_and_are_rendered_as_non_executable_context(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = PADesignerWindow(tmp_path)
    try:
        identity = window.add_annotation({"kind": "phase", "text": "Preparation", "width": 420,
                                          "height": 100, "color": "#336699"})
        assert identity in window.canvas.annotations
        assert len(window.draft.data["steps"]) == 3
        AdvisoryProcedure.model_validate(window.draft.data)
        window.update_annotation_geometry(identity, {"x": 25, "y": 40, "width": 420, "height": 100})
        assert next(row for row in window.draft.data["metadata"]["annotations"] if row["id"] == identity)["x"] == 25
    finally:
        window._saved = window.snapshot()
        window.close()
        window.deleteLater()
        app.processEvents()


def test_new_engineering_dialogs_use_shared_chrome(tmp_path):
    from azeo_control_trainer.azeo_pa_designer.engineering_tools import (
        AnnotationDialog, ExtractProcedureDialog, GovernanceDialog,
        RefactorDialog, RevisionCompareDialog,
    )
    from azeo_control_trainer.azeo_pa_designer.new_procedure import NewProcedureDialog

    app = QApplication.instance() or QApplication([])
    draft = ProcedureDraft.new()
    dialogs = [
        NewProcedureDialog(), RefactorDialog(draft.data, draft.bindings),
        RevisionCompareDialog([], draft), AnnotationDialog(),
        ExtractProcedureDialog(2), GovernanceDialog([]),
    ]
    try:
        assert all(dialog.styleSheet() == ENGINEERING_QSS for dialog in dialogs)
        assert all(dialog.findChild(QWidget, "configurationHeader") is not None for dialog in dialogs)
    finally:
        for dialog in dialogs:
            dialog.close()
            dialog.deleteLater()
        app.processEvents()


def test_typed_workflow_inspector_updates_the_same_native_graph(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = PADesignerWindow(tmp_path)
    try:
        window.flow_editor.enable_flow()
        window.flow_editor.add_node("transition")
        row = next(index for index, node in enumerate(window.draft.data["flow"]["nodes"])
                   if node["kind"] == "transition")
        window.flow_editor.nodes.selectRow(row)
        window.flow_editor.load_node_inspector()
        window.flow_editor.node_label.setText("Pressure established")
        window.flow_editor.node_condition.setText("True")
        window.flow_editor.node_timeout.setValue(45)
        window.flow_editor.apply_node_inspector()
        node = window.draft.data["flow"]["nodes"][row]
        assert node["label"] == "Pressure established"
        assert node["condition"] == "True"
        assert node["timeout_sec"] == 45
    finally:
        window._saved = window.snapshot()
        window.close()
        window.deleteLater()
        app.processEvents()


def test_inline_tag_rename_uses_the_same_safe_refactor(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = PADesignerWindow(tmp_path)
    try:
        window.add_tag()
        window.draft.data["steps"][0]["condition"] = "TAG1.PV > 0"
        window.render()
        window.tags.item(0, 0).setText("UNIT.PV")
        assert window.draft.data["steps"][0]["condition"] == "UNIT.PV > 0"
        assert "UNIT.PV" in window.draft.bindings
        assert "TAG1.PV" not in window.draft.bindings
    finally:
        window._saved = window.snapshot()
        window.close()
        window.deleteLater()
        app.processEvents()
