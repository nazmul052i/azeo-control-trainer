"""Native APVC editing acceptance using separate engineering processes and drafts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "logs/configuration-editing-ui"
OUT.mkdir(exist_ok=True)


def worker(directory, action):
    os.environ["QT_QPA_PLATFORM"] = "windows"
    from PySide6.QtCore import QSettings, qInstallMessageHandler
    from PySide6.QtWidgets import QApplication, QCheckBox, QLineEdit, QPushButton, QWidget
    from azeo_control_trainer.core.configuration.workspace import DraftWorkspace
    from azeo_control_trainer.core.hmi.theme.fonts import apply_application_font
    from azeo_control_trainer.core.presentation import headless
    from azeo_control_trainer.core.presentation.authoring_style import AUTHORING_CHROME_QSS
    from azeo_control_trainer.core.presentation.configuration_catalog import _shutdown
    from azeo_control_trainer.core.presentation.configuration_editing import EngineeringChangesDialog
    from azeo_control_trainer.core.strategy.serialization import strategy_io

    app = QApplication([])
    apply_application_font()
    app.setStyleSheet(AUTHORING_CHROME_QSS)
    headless.is_headless = lambda: True
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(Path(directory) / "settings"))
    messages = []
    qInstallMessageHandler(lambda kind, context, message: messages.append(str(message)))
    workspace = DraftWorkspace(directory)
    strategy_io.STRATEGY_DIR = workspace.root
    dialog = EngineeringChangesDialog(workspace)
    dialog.show()
    def wait():
        deadline = time.monotonic() + 90
        app.processEvents()
        while time.monotonic() < deadline:
            app.processEvents()
            if dialog.worker is None:
                return
            time.sleep(.02)
        raise AssertionError("Editing worker timed out: " + dialog.status.text())
    def select(module):
        dialog.search.setText(f"control/{module}.json")
        dialog.table.selectRow(0)
        assert dialog.selected()["path"] == f"control/{module}.json"
    def capture(widget, name):
        app.processEvents()
        widget.grab().save(str(OUT / (name + ".png")))
    def accept_review(review, reason, button_text):
        review.findChild(QLineEdit).setText(reason)
        review.findChild(QCheckBox).setChecked(True)
        button = next(b for b in review.findChildren(QPushButton) if b.text() == button_text)
        assert button.isEnabled()
        button.click()
        wait()

    try:
        wait()
        assert dialog.state, dialog.status.text()
        if action in {"a", "b", "conflict"}:
            module = "SIC-2001" if action == "b" else "PIC-2001"
            select(module)
            dialog.open_selected()
            wait()
            assert dialog.editors, dialog.status.text()
            editor = dialog.editors[0]
            canvas = editor.designer._active_canvas()
            canvas.scene.graph.description = "Shared editing verification — " + action
            canvas.dirty = True
            dialog.open_selected()
            wait()
            assert dialog.editors == [editor] and editor.designer._active_canvas() is canvas
            capture(editor, "control-designer-" + action)
            assert dialog.save_editors()
            dialog.review()
            wait()
            if action == "conflict":
                assert "newer revision" in dialog.status.text(), dialog.status.text()
                dialog.compare()
                wait()
                comparison = dialog._dialogs[-1]
                capture(comparison, "conflict-comparison")
                comparison.findChild(QCheckBox).setChecked(True)
                next(b for b in comparison.findChildren(QPushButton) if b.text() == "Reload current revision").click()
                wait()
                assert not workspace.changes()
            else:
                assert dialog._dialogs, dialog.status.text()
                review = dialog._dialogs[-1]
                capture(review, "checkin-" + action)
                accept_review(review, "Native acceptance: independent " + module + " draft",
                              "Check in change set")
                assert not workspace.changes(), dialog.status.text()
                capture(dialog, "shared-changes-" + action)
        elif action in {"rename", "restore-name"}:
            module, target = ("PIC-2001", "PIC-2001-ENG") if action == "rename" else ("PIC-2001-ENG", "PIC-2001")
            select(module)
            dialog.rename(target)
            wait()
            assert dialog._dialogs, dialog.status.text()
            review = dialog._dialogs[-1]
            capture(review, action + "-impact")
            accept_review(review, "Native acceptance: reviewed module address rename",
                          "Check in reviewed rename")
            assert (workspace.root / f"control/{target}.json").exists(), dialog.status.text()
            assert not workspace.changes()
        elif action == "graphics":
            dialog.search.setText("U200 - L2 Recycle Compression/draft.json")
            dialog.table.selectRow(0)
            dialog.open_selected()
            wait()
            assert dialog.editors, dialog.status.text()
            editor = dialog.editors[0]
            studio = editor.studios()[0]
            studio.display.description = "Shared editing verification — graphic"
            studio.unsaved = True
            capture(editor, "graphics-designer")
            dialog.review()
            wait()
            accept_review(dialog._dialogs[-1], "Native acceptance: graphics draft", "Check in change set")
            assert not workspace.changes(), dialog.status.text()
        assert all(w.font().pointSizeF() > 0 for w in dialog.findChildren(QWidget))
    finally:
        dialog.close()
        workspace.release()
        _shutdown()
        app.processEvents()
    result = {"action": action, "qt_messages": messages, "success": not messages}
    (OUT / (action + ".json")).write_text(json.dumps(result), encoding="utf-8")
    assert not messages, messages
    print(json.dumps(result), flush=True)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        worker(sys.argv[2], sys.argv[3])
        return
    from azeo_control_trainer.core.configuration.client import ConfigurationClient, read_profile
    from azeo_control_trainer.core.configuration.documents import read_project
    from azeo_control_trainer.core.configuration.workspace import DraftWorkspace
    client = ConfigurationClient(**read_profile())
    source = next(p for p in client.request("/v1/projects") if p["name"] == "APVC Database Pilot")
    before = client.request(f"/v1/projects/{source['id']}/export")
    source_files = read_project(ROOT / "projects/AzeoPlantVirtualController")
    name = "APVC Shared Editing Pilot"
    existing = next((p for p in client.request("/v1/projects") if p["name"] == name), None)
    if existing:
        project = existing["id"]
    else:
        project = client.request(f"/v1/projects/{source['id']}/editing/fork",
                                 {"name": name, "command_id": str(uuid4())})["project_id"]
    run = OUT / str(uuid4())
    a = DraftWorkspace.create(run / "session-a", project)
    b = DraftWorkspace.create(run / "session-b", project)
    handles = []
    def start(workspace, action):
        log = (OUT / (action + ".log")).open("w", encoding="utf-8")
        handles.append(log)
        return subprocess.Popen([sys.executable, "-u", "-X", "faulthandler", str(Path(__file__).resolve()),
                                 "--worker", str(workspace.directory), action], cwd=ROOT,
                                stdout=log, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    def finish(process, action):
        code = process.wait(timeout=180)
        assert code == 0, f"{action} failed; see logs/configuration-editing-ui/{action}.log"
        print(action + " passed", flush=True)
    try:
        first, second = start(a, "a"), start(b, "b")
        finish(first, "a")
        finish(second, "b")
        finish(start(b, "conflict"), "conflict")
        # Reload the session's durable bases after its child process checked in.
        a = DraftWorkspace(a.directory)
        before_rename = client.request(f"/v1/projects/{project}/editing/state")
        old = next(o for o in before_rename["objects"] if o["path"] == "control/PIC-2001.json")
        finish(start(a, "rename"), "rename")
        state = client.request(f"/v1/projects/{project}/editing/state")
        renamed = next(o for o in state["objects"] if o["path"] == "control/PIC-2001-ENG.json")
        assert renamed["id"] == old["id"]
        a = DraftWorkspace(a.directory)
        finish(start(a, "restore-name"), "restore-name")
        graphics = DraftWorkspace.create(run / "graphics", project)
        finish(start(graphics, "graphics"), "graphics")
    finally:
        for handle in handles:
            handle.close()
    # Restore the isolated pilot's source configuration through a new audited
    # change set. The original project and its file-shadow snapshot are untouched.
    current = client.request(f"/v1/projects/{project}/export")
    originals = {f["path"]: f["content"] for f in before["files"]}
    objects = {o["path"]: o for o in current["objects"]}
    edits = [{"path": f["path"], "content": originals[f["path"]],
              "expected_revision": objects[f["path"]]["revision"]}
             for f in current["files"] if f["content"] != originals[f["path"]]]
    if edits:
        session = str(uuid4())
        client.request(f"/v1/projects/{project}/editing/lease", {"session": session, "paths": [e["path"] for e in edits]})
        client.request(f"/v1/projects/{project}/editing/checkin", {"session": session, "edits": edits,
                       "reason": "Restore APVC baseline after native editing acceptance", "command_id": str(uuid4())})
        client.request(f"/v1/projects/{project}/editing/lease", {"session": session, "paths": [], "release": True})
    assert client.request(f"/v1/projects/{project}/export")["digest"] == before["digest"]
    assert client.request(f"/v1/projects/{source['id']}/export") == before
    assert read_project(ROOT / "projects/AzeoPlantVirtualController") == source_files
    result = {"project": project, "native_processes": True, "source_unchanged": True,
              "pilot_restored": True, "rename_identity_preserved": True, "success": True}
    (OUT / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
