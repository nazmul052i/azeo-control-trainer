"""Working draft recovery and reachable native-editor check-in workflow."""
import json
import threading
import time

import pytest

from test_configuration_catalog import app as app_fixture
from test_configuration_editing import pilot as pilot_fixture, database as database_fixture, files as files_fixture
from azeo_control_trainer.core.configuration.client import ServiceUnavailable
from azeo_control_trainer.core.configuration.documents import ConfigurationError, Conflict
from azeo_control_trainer.core.configuration.workspace import DraftWorkspace, MARKER

app, pilot, database, files = app_fixture, pilot_fixture, database_fixture, files_fixture
PROFILE = {"url": "http://127.0.0.1:8766", "token": "workspace-test-identity"}


class LocalClient:
    def __init__(self, pilot):
        self.repo, self.token, self.editing, _, self.project = pilot
        self.threads = []
        self.lose_response = False
        self.offline = False

    def request(self, path, payload=None):
        self.threads.append(threading.get_ident())
        if self.offline:
            raise ServiceUnavailable("Test outage")
        action = path.rsplit("/", 1)[-1]
        if action == "export":
            result = self.repo.export(self.token, self.project)
        elif action == "state":
            result = self.editing.state(self.token, self.project)
        elif action == "lease":
            result = self.editing.lease(self.token, self.project, payload["session"], payload["paths"], release=payload.get("release", False))
        elif action == "preview":
            result = self.editing.preview(self.token, self.project, payload["edits"])
        elif action == "rename":
            result = self.editing.rename_preview(self.token, self.project, payload["module"], payload["new_name"])
        elif action == "checkin":
            result = self.editing.checkin(self.token, self.project, payload["session"], payload["edits"],
                                         payload["reason"], payload["command_id"], expected_generation=payload.get("expected_generation"))
            if self.lose_response:
                self.lose_response = False
                raise ServiceUnavailable("Committed response was lost")
        else:
            raise AssertionError(action)
        return json.loads(json.dumps(result, default=str))


@pytest.fixture
def workspace(tmp_path, pilot):
    client = LocalClient(pilot)
    return DraftWorkspace.create(tmp_path / "session-a", client.project, profile=PROFILE, client=client)


def edit_description(workspace, value):
    path = workspace.root / "control/LOOP.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["description"] = value
    path.write_text(json.dumps(raw), encoding="utf-8")


def test_studio_save_stays_local_until_checkin_and_base_updates(workspace):
    before = workspace.client.repo.export(workspace.client.token, workspace.project)
    edit_description(workspace, "Draft value")
    assert (workspace.root / MARKER).exists()
    assert workspace.client.repo.export(workspace.client.token, workspace.project) == before
    review = workspace.preview()
    receipt = workspace.checkin(review, "Reviewed local draft")
    assert receipt["generation"] == 2
    assert workspace.changes() == []
    assert not workspace.pending()


def test_lost_response_recovers_after_reopening_without_duplicate_commit(workspace):
    edit_description(workspace, "Retain after crash")
    workspace.client.lose_response = True
    with pytest.raises(ServiceUnavailable):
        workspace.checkin(workspace.preview(), "Recover this change")
    command = workspace.pending()["command_id"]
    reopened = DraftWorkspace(workspace.directory, profile=PROFILE, client=workspace.client)
    receipt = reopened.retry()
    assert receipt["generation"] == 2 and not reopened.changes()
    assert len([r for r in workspace.client.repo.audit(workspace.client.token, workspace.project)
                if str(r["command_id"]) == command]) == 1


def test_outage_preserves_draft_and_blocks_shared_save(workspace):
    edit_description(workspace, "Offline work")
    before = workspace.changes()
    workspace.client.offline = True
    with pytest.raises(ServiceUnavailable):
        workspace.preview()
    assert workspace.changes() == before


def test_explicit_conflict_resolution_keeps_recovery_copy(workspace, tmp_path):
    other = DraftWorkspace.create(tmp_path / "session-b", workspace.project, profile=PROFILE, client=workspace.client)
    edit_description(workspace, "My draft")
    edit_description(other, "Their change")
    other.checkin(other.preview(), "Other engineer")
    other.release()
    with pytest.raises(Conflict):
        workspace.preview()
    comparison = workspace.comparison("control/LOOP.json")
    workspace.resolve(comparison, keep_draft=True)
    assert any(workspace.directory.glob("recovery/*/control/LOOP.json"))
    assert workspace.changes()[0]["expected_revision"] == 2
    receipt = workspace.checkin(workspace.preview(), "Reviewed both versions; retained local description")
    assert receipt["generation"] == 3


def test_rename_lost_reply_updates_files_on_retry_preserving_object_identity(workspace):
    old = next(o for o in workspace.base["objects"] if o["path"] == "control/LOOP.json")
    plan = workspace.rename_preview("LOOP", "RENAMED")
    workspace.client.lose_response = True
    with pytest.raises(ServiceUnavailable):
        workspace.rename_checkin(plan, "Reviewed rename")
    reopened = DraftWorkspace(workspace.directory, profile=PROFILE, client=workspace.client)
    reopened.retry()
    assert not (reopened.root / "control/LOOP.json").exists()
    new = next(o for o in reopened.base["objects"] if o["path"] == "control/RENAMED.json")
    assert new["id"] == old["id"] and not reopened.changes()


def test_rename_recovery_does_not_overwrite_edits_made_after_lost_reply(workspace):
    plan = workspace.rename_preview("LOOP", "RENAMED")
    workspace.client.lose_response = True
    with pytest.raises(ServiceUnavailable):
        workspace.rename_checkin(plan, "Reviewed rename")
    edit_description(workspace, "Newer local work")
    with pytest.raises(ConfigurationError, match="newer local edits"):
        workspace.retry()
    assert workspace.pending()
    raw = json.loads((workspace.root / "control/LOOP.json").read_text())
    assert raw["description"] == "Newer local work"


def test_other_identity_cannot_reuse_private_working_draft(workspace):
    with pytest.raises(ConfigurationError, match="different"):
        DraftWorkspace(workspace.directory, profile={**PROFILE, "token": "other"}, client=workspace.client)


def test_loaded_draft_cannot_run_or_publish(workspace):
    from azeo_control_trainer.core.strategy.serialization.strategy_io import load_strategy
    from azeo_control_trainer.core.strategy.engine.compiler import compile_strategy
    from azeo_control_trainer.core.strategy.engine.runtime import StrategyRuntime
    from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore, PublishRefused, PvmDisplay
    graph, _ = load_strategy(workspace.root / "control/LOOP.json", remember=False)
    assert graph._configuration_draft
    runtime = StrategyRuntime()
    runtime.load(compile_strategy(graph), None)
    assert not runtime.go_online() and not runtime.is_online
    with pytest.raises(PublishRefused, match="working draft|draft workspace"):
        DisplayStore(workspace.root / "displays/pvm").publish(PvmDisplay("Test"))


def wait(app, dialog):
    deadline = time.monotonic() + 30
    # Allow the initial singleShot to start the worker before testing completion.
    app.processEvents()
    while time.monotonic() < deadline:
        app.processEvents()
        if dialog.worker is None:
            return
        time.sleep(.01)
    pytest.fail("Engineering command worker timed out")


def test_native_control_editor_saves_draft_and_review_button_commits(app, workspace, monkeypatch):
    from PySide6.QtWidgets import QCheckBox, QLineEdit, QPushButton
    from azeo_control_trainer.core.presentation.configuration_editing import EngineeringChangesDialog
    from azeo_control_trainer.core.strategy.serialization import strategy_io
    monkeypatch.setattr(strategy_io, "STRATEGY_DIR", workspace.root)
    dialog = EngineeringChangesDialog(workspace)
    dialog.show()
    wait(app, dialog)
    dialog.search.setText("control/LOOP.json")
    dialog.table.selectRow(0)
    dialog.open_selected()
    wait(app, dialog)
    assert dialog.editors, dialog.status.text()
    editor = dialog.editors[0]
    canvas = editor.designer._active_canvas()
    canvas.scene.graph.description = "Changed in Control Designer"
    canvas.dirty = True
    assert dialog.save_editors()
    assert workspace.changes()
    assert workspace.client.repo.projects(workspace.client.token)[0]["generation"] == 1
    dialog.review()
    wait(app, dialog)
    review = dialog._dialogs[-1]
    review.findChild(QLineEdit).setText("UI check-in workflow")
    review.findChild(QCheckBox).setChecked(True)
    button = next(b for b in review.findChildren(QPushButton) if b.text() == "Check in change set")
    button.click()
    wait(app, dialog)
    assert workspace.changes() == [], dialog.status.text()
    assert workspace.remote["project"]["generation"] == 2
    dialog.close()


def test_revision_inventory_resync_reports_missed_changes_and_worker_is_off_ui(app, workspace, tmp_path):
    from azeo_control_trainer.core.presentation.configuration_editing import EngineeringChangesDialog
    dialog = EngineeringChangesDialog(workspace)
    dialog.show()
    wait(app, dialog)
    other = DraftWorkspace.create(tmp_path / "session-b", workspace.project, profile=PROFILE, client=workspace.client)
    edit_description(other, "While disconnected")
    other.checkin(other.preview(), "Other session")
    workspace.client.threads.clear()
    dialog.refresh()
    wait(app, dialog)
    row = next(r for r in dialog.model.rows if r["path"] == "control/LOOP.json")
    assert row["state"] == "New committed revision"
    assert workspace.client.threads and threading.get_ident() not in workspace.client.threads
    dialog.close()


def test_repeat_open_reuses_studio_and_failed_save_blocks_review(app, workspace, monkeypatch):
    from azeo_control_trainer.core.presentation.configuration_editing import EngineeringChangesDialog
    from azeo_control_trainer.core.strategy.serialization import strategy_io
    monkeypatch.setattr(strategy_io, "STRATEGY_DIR", workspace.root)
    dialog = EngineeringChangesDialog(workspace)
    dialog.show()
    wait(app, dialog)
    row = next(r for r in dialog.model.rows if r["path"] == "control/LOOP.json")
    dialog.open_editor(row)
    editor = dialog.editors[0]
    canvas = editor.designer._active_canvas()
    canvas.scene.graph.description = "Unsaved editor work"
    canvas.dirty = True
    dialog.open_editor(row)
    try:
        assert dialog.editors == [editor], "Opening twice must not create competing drafts"
        assert editor.designer._active_canvas() is canvas
        with monkeypatch.context() as failure:
            failure.setattr(editor.designer, "_save", lambda _: None)
            dialog.review()
            assert not dialog._dialogs and dialog.worker is None
            assert "could not be saved" in dialog.status.text()
        assert dialog.save_editors()
        editor.close()
        edit_description(workspace, "Newer saved work after editor closed")
        dialog.open_editor(row)
        assert len(dialog.editors) == 1 and dialog.editors[0] is not editor
        assert dialog.editors[0].designer._active_canvas().scene.graph.description == "Newer saved work after editor closed"
    finally:
        dialog.close()


def test_tree_rename_routes_to_review_without_moving_files(app, workspace, monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QTreeWidgetItem
    from azeo_control_trainer.azeo_control_designer.panels.project_tree import _ROLE_FILE_PATH
    from azeo_control_trainer.core.presentation.configuration_editing import EngineeringChangesDialog
    from azeo_control_trainer.core.strategy.serialization import strategy_io
    monkeypatch.setattr(strategy_io, "STRATEGY_DIR", workspace.root)
    dialog = EngineeringChangesDialog(workspace)
    dialog.show()
    wait(app, dialog)
    row = next(r for r in dialog.model.rows if r["path"] == "control/LOOP.json")
    dialog.open_editor(row)
    requested = []
    monkeypatch.setattr(dialog, "rename", lambda: requested.append(dialog.selected()["path"]))
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: ("", False))
    item = QTreeWidgetItem()
    item.setData(0, _ROLE_FILE_PATH, str(workspace.root / row["path"]))
    try:
        dialog.editors[0].designer._project_tree._rename_strategy(item)
        app.processEvents()
        assert requested == [row["path"]]
        assert not workspace.changes()
    finally:
        dialog.close()


def test_file_only_display_rename_refused_before_moving_history(workspace):
    from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore, PvmDisplay
    store = DisplayStore(workspace.root / "displays/pvm")
    store.save_draft(PvmDisplay("Local"))
    store.acquire_lock("Local")
    before = (store.root / "Local/draft.json").read_bytes()
    try:
        with pytest.raises(ValueError, match="Shared display rename"):
            store.rename_display("Local", "Moved")
        assert (store.root / "Local/draft.json").read_bytes() == before
        assert not (store.root / "Moved").exists()
    finally:
        store.release_lock("Local")


def test_comparison_detects_local_delete_while_review_is_open(workspace):
    comparison = workspace.comparison("control/LOOP.json")
    path = workspace.root / "control/LOOP.json"
    path.unlink()
    with pytest.raises(Conflict, match="Local draft changed"):
        workspace.resolve(comparison)
    assert not path.exists()


def test_rename_retry_preserves_new_work_at_destination(workspace):
    plan = workspace.rename_preview("LOOP", "RENAMED")
    workspace.client.lose_response = True
    with pytest.raises(ServiceUnavailable):
        workspace.rename_checkin(plan, "Reviewed rename")
    target = workspace.root / "control/RENAMED.json"
    target.write_bytes((workspace.root / "control/LOOP.json").read_bytes())
    newer = target.read_bytes()
    with pytest.raises(ConfigurationError, match="newer local edits"):
        workspace.retry()
    assert target.read_bytes() == newer and workspace.pending()


def test_session_entry_refuses_an_already_open_working_directory(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    from PySide6.QtCore import QLockFile
    lock = QLockFile(str(tmp_path / "session.lock"))
    assert lock.tryLock(0)
    try:
        env = {**os.environ, "QT_QPA_PLATFORM": "offscreen",
               "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
        result = subprocess.run([sys.executable, "-m",
                                 "azeo_control_trainer.azeo_explorer.configuration_workspace", str(tmp_path)],
                                env=env, capture_output=True, text=True, timeout=45)
        assert result.returncode == 1 and "Traceback" not in result.stderr, result.stderr
    finally:
        lock.unlock()
