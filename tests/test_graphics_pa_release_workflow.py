"""PA remapping must carry the real revision into every authored runtime surface."""
import copy
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.core.hmi.pvms.procedure_assemblies import (
    procedure_references, remap_procedures, revision_issues,
)
from azeo_control_trainer.core.procedures.authoring import ProcedureDraft


def revisions(project):
    draft = ProcedureDraft.new()
    first = draft.save_revision(project).relative_to(project / "procedures").as_posix()
    draft.data["name"] = "Shutdown B"
    second = draft.save_revision(project).relative_to(project / "procedures").as_posix()
    return first, second


def test_mapping_swaps_revisions_in_all_binding_and_choice_locations(tmp_path):
    first, second = revisions(tmp_path)
    original = {"items": [
        {"id": "state", "path": f"@procedure/{first}/STATE", "text": first,
         "pvm_choices": {"ProcedureRef": first, "Title": first},
         "instance_choices": {"ProcedureRef": first},
         "actions": [{"kind": "procedure_command", "source": f"@procedure/{first}/CONTEXT", "target": "pause"}],
         "props": {"enabled": {"kind": "path", "path": f"@procedure/{first}/CAN_PAUSE"}}},
        {"id": "table", "rows_path": f"@procedure/{second}/CONDITIONS", "series_path": f"@procedure/{first}/TREND"}]}
    before = copy.deepcopy(original)
    result = remap_procedures(original, {first: second, second: first}, tmp_path / "procedures")
    assert procedure_references(original) == sorted((first, second))
    state, table = result["items"]
    assert state["path"] == f"@procedure/{second}/STATE"
    assert state["pvm_choices"]["ProcedureRef"] == second
    assert state["instance_choices"]["ProcedureRef"] == second
    assert state["text"] == state["pvm_choices"]["Title"] == first
    assert state["actions"][0]["target"] == "pause"
    assert state["actions"][0]["source"] == f"@procedure/{second}/CONTEXT"
    assert table["rows_path"] == f"@procedure/{first}/CONDITIONS"
    assert table["series_path"] == f"@procedure/{second}/TREND"
    assert original == before


def test_revision_mapping_rejects_missing_invalid_and_escaping_files(tmp_path):
    first, _ = revisions(tmp_path)
    doc = {"items": [{"path": f"@procedure/{first}/STATE"}]}
    for target in ("missing/procedure.yaml", "../procedure.yaml", "C:/outside.yaml"):
        assert revision_issues(doc, {first: target}, tmp_path / "procedures")
        with pytest.raises(ValueError):
            remap_procedures(doc, {first: target}, tmp_path / "procedures")


@pytest.fixture
def pa_window(tmp_path):
    from azeo_control_trainer.azeo_graphics_designer.window import HmiStudioWindow
    from azeo_control_trainer.core.hmi.pvms.procedure_blueprint import create_blueprint, configuration
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "displays" / "pvm"
    window = HmiStudioWindow(lambda: {}, root)
    studio = window.current()
    for name in create_blueprint(studio.user_library(), "Shutdown"):
        configuration(name).save(root / "_pvmcfg")
    refs = revisions(tmp_path)
    yield window, refs, tmp_path
    window.close()
    window.deleteLater()
    app.processEvents()


def test_pa_gallery_reaches_real_revision_and_preserves_faceplate_pair(pa_window):
    from azeo_control_trainer.azeo_graphics_designer.engineering_tools import AssemblyDialog
    window, (first, second), _ = pa_window
    studio = window.current()
    dialog = AssemblyDialog(studio)
    index = dialog.source.findText("PA · Shutdown_PVM")
    assert index >= 0
    dialog.source.setCurrentIndex(index)
    assert dialog.mapping.rowCount() == 1
    combo = dialog.mapping.cellWidget(0, 1)
    assert {combo.itemText(i) for i in range(combo.count())} >= {first, second}
    combo.setCurrentText(second)
    preview = dialog.preview()
    assert procedure_references(preview) == [second]
    assert all(row["pvm_choices"]["ProcedureRef"] == second for row in preview["items"])
    assert any(action.get("target") == "Shutdown" for row in preview["items"] for action in row.get("actions", []))
    before = studio._document()
    dialog.apply()
    assert procedure_references(studio._document()) == [second]
    assert not any(f.blocks_publish for f in studio.verification_findings())
    studio.undo()
    assert studio._document() == before
    dialog.close()


def test_revision_changed_since_preview_requires_new_preview(pa_window):
    from azeo_control_trainer.azeo_graphics_designer.engineering_tools import AssemblyDialog
    window, (first, _), project = pa_window
    dialog = AssemblyDialog(window.current())
    dialog.source.setCurrentIndex(dialog.source.findText("PA · Shutdown_PVM"))
    dialog.mapping.cellWidget(0, 1).setCurrentText(first)
    dialog.preview()
    path = project / "procedures" / first
    path.write_text(path.read_text(encoding="utf-8").replace("New procedure", "Changed procedure"), encoding="utf-8")
    with pytest.raises(ValueError, match="revision changed"):
        dialog.apply()
    assert not dialog.apply_button.isEnabled()
    dialog.close()


def test_release_readiness_requires_current_case_evidence_and_reruns_gate(pa_window, monkeypatch):
    from azeo_control_trainer.core.hmi.pvms.engineering import document_digest
    window, _, _ = pa_window
    studio = window.current()
    dialog = window.open_engineering_tool("release")
    studio.display.commissioning = [{"name": "Normal", "path": "TEST/PID/PV", "override": {}, "expected": {},
                                     "reviewed": False}]
    studio.mark_unsaved()
    dialog.refresh()
    assert not dialog.publish_button.isEnabled()
    digest = document_digest(studio._document())
    studio.display.commissioning[0].update(check_digest=digest, check_passed=True, review_digest=digest,
                                          reviewed=True, review_note="PV displayed", visual_expectation="Readable PV")
    dialog.refresh()
    assert dialog.publish_button.isEnabled()
    studio.add_static("text", 20, 20, 140, 25, text="Changed draft")
    assert not dialog.publish_button.isEnabled()
    with pytest.raises(ValueError, match="complete the saved"):
        dialog.review_publication()
    assert dialog.report["commissioning"]["remaining"] == 1
    studio.display.commissioning = []
    studio.mark_unsaved()
    calls = []
    monkeypatch.setattr(studio, "open_publish", lambda: calls.append(studio._document()))
    dialog.review_publication()
    assert len(calls) == 1 and not studio.unsaved
    report_path = dialog.save_report()
    assert report_path.is_file()
    dialog.close()
