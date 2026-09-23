"""The shared workspace's menus must reach the existing, guarded commands."""
import time

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenuBar, QTableWidgetItem

from test_configuration_chrome import app as app_fixture, workspace as workspace_fixture, bundle, wait  # noqa: F401
from test_configuration_catalog import catalog_files, files  # noqa: F401

app, workspace = app_fixture, workspace_fixture


def test_workspace_has_the_engineering_menu_bar(app, workspace):
    bar = workspace.findChild(QMenuBar)
    assert bar is not None
    assert [a.text().replace("&", "") for a in bar.actions()] == [
        "File", "Edit", "View", "Object", "Tools", "Help"]


def action(menu, text):
    return next(a for a in menu.actions() if a.text() == text)


def test_menu_navigation_find_and_refresh_reach_current_page(app, workspace, monkeypatch):
    bar = workspace.menu_bar
    bar.sync()
    action(bar.menus["View"], "Releases").trigger()
    assert workspace.current_page_key() == "releases"
    page = workspace.stack.currentWidget()
    calls = []
    monkeypatch.setattr(page, "refresh", lambda: calls.append("refresh"))
    page.search.setFocus()
    QTest.keyClick(page.search, Qt.Key_F, Qt.ControlModifier)
    assert page.search.hasFocus()
    QTest.keyClick(page.search, Qt.Key_F5)
    assert calls == ["refresh"]
    page.run(lambda: time.sleep(.08), lambda _: None)
    action(bar.menus["View"], "Refresh").trigger()
    assert calls == ["refresh"]
    wait(app, lambda: not workspace.busy())


def test_catalog_right_click_uses_clicked_row_and_copies_its_path(app, workspace):
    page = workspace.browser
    assert page.result_model.rowCount() > 1
    page.results.selectRow(0)
    controller = page._configuration_table_menus[page.results]
    menu = controller.build(page.result_model.index(1, 0))
    expected = page.result_model.rows[1]["path"]
    assert page._path == expected
    action(menu, "Copy object path").trigger()
    assert QApplication.clipboard().text() == expected
    action(menu, "References").trigger()
    assert page.tabs.currentIndex() == 2


def test_context_command_does_not_survive_a_model_reset(app, workspace):
    page = workspace.browser
    menu = page._configuration_table_menus[page.results].build(page.result_model.index(0, 0))
    copy = action(menu, "Copy row")
    QApplication.clipboard().setText("unchanged")
    page.result_model.set_rows(page.result_model.rows[1:])
    copy.trigger()
    assert QApplication.clipboard().text() == "unchanged"


def test_release_context_can_include_object_and_uses_guarded_review_button(app, workspace):
    page = workspace.open_page("releases")
    app.processEvents()
    page.objects.setRowCount(1)
    check = QTableWidgetItem()
    check.setCheckState(Qt.Unchecked)
    page.objects.setItem(0, 0, check)
    page.objects.setItem(0, 1, QTableWidgetItem("control/LOOP.json"))
    page.objects.setItem(0, 2, QTableWidgetItem("1"))
    controller = page._configuration_table_menus[page.objects]
    menu = controller.build(page.objects.model().index(0, 1))
    assert not action(menu, "Validate and review…").isEnabled()
    action(menu, "Include in review").trigger()
    assert check.checkState() == Qt.Checked
    assert page.review_button.isEnabled()
    called = []
    page.review_button.clicked.disconnect()
    page.review_button.clicked.connect(lambda: called.append(True))
    menu = controller.build(page.objects.model().index(0, 1))
    review = action(menu, "Validate and review…")
    review.trigger()
    assert called == [True]
    page.run(lambda: time.sleep(.1), lambda _: None)
    review.trigger()
    assert called == [True]
    wait(app, lambda: not workspace.busy())


def test_keyboard_context_menu_and_blank_space_have_safe_actions(app, workspace):
    page = workspace.browser
    page.results.selectRow(0)
    page.results.setFocus()
    controller = page._configuration_table_menus[page.results]
    event = QContextMenuEvent(QContextMenuEvent.Keyboard, QPoint(), QPoint())
    QApplication.sendEvent(page.results, event)
    assert controller.menu.isVisible()
    controller.close()
    from PySide6.QtCore import QModelIndex
    menu = controller.build(QModelIndex())
    assert not any(a.text() == "Copy row" for a in menu.actions())
    assert action(menu, "Refresh").isEnabled()


def test_object_menu_tracks_buttons_and_help_is_modeless(app, workspace):
    workspace.open_page("training")
    app.processEvents()
    workspace.menu_bar._object_commands()
    menu = workspace.menu_bar.menus["Object"]
    assert not action(menu, "Create isolated trainee copy").isEnabled()
    assert not action(menu, "New baseline…").icon().isNull()
    action(workspace.menu_bar.menus["Help"], "Configuration help").trigger()
    assert workspace.menu_bar._help.isVisible()
    assert not workspace.menu_bar._help.isModal()
    assert not workspace.has_review()
    workspace.menu_bar._help.close()


def test_returning_to_same_result_restores_its_context(app, workspace):
    page = workspace.browser
    page.results.selectRow(0)
    first, other = (page.result_model.rows[i]["path"] for i in (0, 1))
    page.inspect(other)
    controller = page._configuration_table_menus[page.results]
    menu = controller.build(page.result_model.index(0, 0))
    action(menu, "Copy object path").trigger()
    assert QApplication.clipboard().text() == first


def test_edit_menu_copies_the_focused_field_and_does_not_steal_hidden_shortcuts(app, workspace):
    bar = workspace.menu_bar
    field = workspace.browser.search
    field.setText("Selected text")
    field.setFocus()
    field.selectAll()
    bar._remember_focus()
    bar.sync()
    menu = bar.menus["Edit"]
    menu.popup(field.mapToGlobal(QPoint(0, field.height())))
    app.processEvents()
    assert action(menu, "Copy").isEnabled()
    action(menu, "Copy").trigger()
    assert QApplication.clipboard().text() == "Selected text"
    menu.close()
    workspace.browser.results.setFocus()
    app.processEvents()
    bar.sync()
    assert not action(menu, "Select all").isEnabled()
    assert all(a.shortcutContext() == Qt.WidgetWithChildrenShortcut
               for a, _ in bar.actions_by_name.values() if not a.shortcut().isEmpty())
