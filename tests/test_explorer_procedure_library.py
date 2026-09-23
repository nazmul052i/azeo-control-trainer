"""Explorer's PA catalog reaches the same editor, help and insertion commands."""
from copy import deepcopy
import os
from pathlib import Path
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QToolBar

from azeo_control_trainer.azeo_explorer import ExplorerWindow
from azeo_control_trainer.core.datastore.shared_data_store import SharedDataStore
from azeo_control_trainer.core.presentation.application_style import apply_application_style
from azeo_control_trainer.core.procedures.library import block_for_token, block_library


@pytest.fixture
def explorer(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    apply_application_style(app)
    designer = SimpleNamespace(open_graphs=lambda: [], controller_executive=lambda:
                               SimpleNamespace(online_runtimes=lambda: []))
    monkeypatch.setattr(ExplorerWindow, "_areas_on_disk", lambda self: [self.area])
    window = ExplorerWindow(SharedDataStore(), tmp_path, SimpleNamespace(designer=designer))
    window.show()
    app.processEvents()
    yield window
    if window._pa_designer_window is not None:
        editor = window._pa_designer_window
        editor._saved = editor.snapshot()
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def entries(explorer, kind):
    found = []
    def visit(item):
        payload = item.data(0, Qt.UserRole)
        if payload and payload[0] == kind:
            found.append(item)
        for i in range(item.childCount()):
            visit(item.child(i))
    visit(explorer.tree.topLevelItem(0))
    return found


def block_item(explorer, kind):
    block_id = block_for_token(kind).block_id
    return next(item for item in entries(explorer, "lib_procedure_block")
                if item.data(0, Qt.UserRole)[1] == block_id)


def test_pa_application_has_a_consistent_icon_in_menu_toolbar_and_library(explorer):
    menu = next(action.menu() for action in explorer.menuBar().actions()
                if action.text().replace("&", "") == "Applications")
    menu.aboutToShow.emit()
    action = next(action for action in menu.actions() if action.text() == "PA Designer")
    assert not action.icon().isNull()

    from azeo_control_trainer.core.presentation.studio_icons import studio_icon

    expected = studio_icon("procedure", 16).pixmap(16, 16).toImage()
    assert action.icon().pixmap(16, 16).toImage() == expected
    for payload in (("lib_procedure_blocks", None),
                    ("lib_procedure_block", "azeo.procedure.delay")):
        context = explorer._menu_for(payload)
        try:
            context.aboutToShow.emit()
            opener = next(action for action in context.actions() if "PA Designer" in action.text())
            assert opener.icon().pixmap(16, 16).toImage() == expected
        finally:
            context.deleteLater()

    toolbar = next(toolbar for toolbar in explorer.findChildren(QToolBar)
                   if toolbar.windowTitle() == "Applications")
    action = next(action for action in toolbar.actions() if action.text() == "PA Designer")
    library = entries(explorer, "lib_procedure_blocks")[0]
    # Compare silhouettes: Explorer's tree/toolbar and menus use different ink colors.
    def silhouette(icon, size):
        image = icon.pixmap(size, size).toImage()
        return [image.pixelColor(x, y).alpha()
                for y in range(image.height()) for x in range(image.width())]
    for icon, size in ((action.icon(), 18), (library.icon(0), 16)):
        assert silhouette(icon, size) == silhouette(studio_icon("procedure", size), size)
    assert expected != studio_icon("exec_order", 16).pixmap(16, 16).toImage()
    assert expected != studio_icon("unknown", 16).pixmap(16, 16).toImage()


def test_catalog_categories_icons_details_and_search_share_pa_definitions(explorer):
    blocks = block_library()
    nodes = entries(explorer, "lib_procedure_block")
    assert len(nodes) == len(blocks)
    assert {item.data(0, Qt.UserRole)[1] for item in nodes} == {b.block_id for b in blocks}
    assert {item.text(0) for item in entries(explorer, "lib_procedure_category")} == {b.category for b in blocks}
    for block in blocks:
        item = block_item(explorer, block.block_id)
        assert item.text(0) == block.label
        assert not item.icon(0).isNull()
        assert block.block_id in item.toolTip(0)
        assert block.version in item.toolTip(0)
    item = block_item(explorer, "wait_until")
    explorer._show_contents(item.parent())
    row = next(explorer.contents.topLevelItem(i) for i in range(explorer.contents.topLevelItemCount())
               if explorer.contents.topLevelItem(i).data(0, Qt.UserRole)[1] ==
               "azeo.procedure.wait_until")
    assert row.text(1) == "Procedure Block"
    assert row.text(2) == "1.0.0"
    assert "dwell" in row.text(3)
    explorer.search.setText("azeo.procedure.wait_until")
    assert not item.isHidden()
    assert block_item(explorer, "delay").isHidden()


def test_activation_focuses_existing_editor_without_altering_draft(explorer):
    editor = explorer.open_pa_designer()
    editor.properties["name"].setText("Unsaved operator procedure")
    before = deepcopy(editor.draft.data)
    editor.block_search.setText("no matching block")
    explorer._activate(block_item(explorer, "wait_until"))
    assert explorer._pa_designer_window is editor
    assert editor.library_tabs.currentIndex() == 1
    assert editor.block_tree.currentItem().data(0, Qt.UserRole) == "azeo.procedure.wait_until"
    assert not editor.block_tree.currentItem().isHidden()
    assert editor.add_block_button.isEnabled()
    assert editor.draft.data == before


def test_menu_insertion_is_undoable_and_block_help_is_specific(explorer):
    item = block_item(explorer, "delay")
    menu = explorer._menu_for(item.data(0, Qt.UserRole))
    menu.aboutToShow.emit()
    actions = {a.text(): a for a in menu.actions()}
    try:
        assert all(not actions[label].icon().isNull() for label in (
            "Open in PA Designer", "Insert into current procedure", "Block Help", "Copy block identity"))
        actions["Insert into current procedure"].trigger()
        editor = explorer._pa_designer_window
        assert editor.draft.data["steps"][1]["library_block_id"] == "azeo.procedure.delay"
        assert editor.draft.data["steps"][1]["library_block_version"] == "1.0.0"
        editor.undo()
        assert [s["type"] for s in editor.draft.data["steps"]] == ["instruction", "operator_comment", "complete"]
        actions["Block Help"].trigger()
        assert explorer._procedure_block_help.current_topic_key == "delay"
        actions["Copy block identity"].trigger()
        assert QApplication.clipboard().text() == "azeo.procedure.delay"
    finally:
        menu.deleteLater()


def test_block_help_and_f1_reuse_help_without_opening_a_procedure(explorer):
    item = block_item(explorer, "wait_until")
    menu = explorer._menu_for(item.data(0, Qt.UserRole))
    try:
        next(a for a in menu.actions() if a.text() == "Block Help").trigger()
        dialog = explorer._procedure_block_help
        assert dialog.current_topic_key == "wait_until"
        assert explorer._pa_designer_window is None
        dialog.close()
        explorer.tree.setCurrentItem(block_item(explorer, "calculate"))
        explorer.tree.setFocus()
        assert explorer.show_help() is dialog
        assert dialog.current_topic_key == "calculate"
        assert explorer._pa_designer_window is None
    finally:
        menu.deleteLater()


def test_contents_activation_and_library_menu_open_the_block_palette(explorer):
    item = block_item(explorer, "operator_input")
    explorer._show_contents(item.parent())
    row = next(explorer.contents.topLevelItem(i) for i in range(explorer.contents.topLevelItemCount())
               if explorer.contents.topLevelItem(i).data(0, Qt.UserRole)[1] ==
               "azeo.procedure.operator_input")
    explorer._activate(row)
    editor = explorer._pa_designer_window
    assert editor.block_tree.currentItem().data(0, Qt.UserRole) == "azeo.procedure.operator_input"
    menu = explorer._menu_for(("lib_procedure_blocks", None))
    try:
        editor.library_tabs.setCurrentIndex(0)
        next(a for a in menu.actions() if a.text() == "Open PA Designer").trigger()
        assert editor.library_tabs.currentIndex() == 1
    finally:
        menu.deleteLater()


def test_invalid_block_request_cannot_insert_the_previous_selection(explorer):
    editor = explorer.open_procedure_block("delay")
    before = deepcopy(editor.draft.data)
    assert explorer.open_procedure_block("unsupported", insert=True) is None
    assert editor.draft.data == before
