"""Configuration navigation must retain context and the shared engineering chrome."""
import os
from pathlib import Path
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog

from azeo_control_trainer.core.presentation.configuration_catalog import _table
from test_configuration_catalog import bundle as bundle_fixture, catalog_files, files, make_session  # noqa: F401

bundle = bundle_fixture


@pytest.fixture(scope="module")
def app():
    from azeo_control_trainer.core.hmi.theme.fonts import apply_application_font
    application = QApplication.instance() or QApplication([])
    apply_application_font()
    return application


def wait(app, predicate):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("Configuration request did not finish")


def test_refresh_keeps_selected_object_when_another_row_is_inserted(app):
    table, model = _table([("path", "Object"), ("revision", "Revision")], "Objects")
    model.set_rows([{"path": "LOOP", "revision": 1}, {"path": "PUMP", "revision": 1}])
    table.selectRow(1)
    model.set_rows([{"path": "NEW", "revision": 1}, {"path": "LOOP", "revision": 2},
                    {"path": "PUMP", "revision": 1}])
    assert table.currentIndex().isValid()
    assert model.rows[table.currentIndex().row()]["path"] == "PUMP"
    table.close()


def test_refresh_keeps_the_same_object_on_the_same_runtime(app):
    table, model = _table([("target", "Runtime"), ("path", "Object")], "Comparison")
    first = {"id": "module", "target_id": "a", "target": "A", "path": "LOOP"}
    second = {**first, "target_id": "b", "target": "B"}
    model.set_rows([first, second])
    table.selectRow(1)
    model.set_rows([first, {**second, "status": "Unknown"}])
    assert model.rows[table.currentIndex().row()]["target_id"] == "b"


@pytest.fixture
def workspace(app, tmp_path, bundle, monkeypatch):
    from azeo_control_trainer.core.presentation.configuration_catalog import ConfigurationCatalogDialog
    from azeo_control_trainer.core.presentation.configuration_editing import EditingLauncher
    from azeo_control_trainer.core.presentation.configuration_releases import ReleaseManager
    from azeo_control_trainer.core.presentation.configuration_libraries import LibraryManager
    from azeo_control_trainer.core.presentation.configuration_training import TrainingManager
    from azeo_control_trainer.core.presentation.configuration_recovery import RecoveryManager
    from azeo_control_trainer.azeo_explorer.configuration_database import ConfigurationDatabaseDialog
    for cls in (ReleaseManager, LibraryManager, TrainingManager, RecoveryManager):
        monkeypatch.setattr(cls, "refresh", lambda self: None)
    monkeypatch.setattr(EditingLauncher, "load_sessions", lambda self: None)
    monkeypatch.setattr(ConfigurationDatabaseDialog, "connect_service", lambda self: None)
    bundle["project"]["mode"] = "repository"
    dialog = ConfigurationCatalogDialog(session=make_session(tmp_path, bundle))
    dialog.show()
    wait(app, lambda: dialog.browser._started and not dialog.workspace.busy())
    yield dialog.workspace
    dialog.close()
    from azeo_control_trainer.core.presentation.configuration_catalog import _shutdown
    _shutdown()
    dialog.deleteLater()
    app.processEvents()


def test_pages_share_profile_and_preserve_navigation_and_reopen(app, workspace):
    from azeo_control_trainer.core.presentation.configuration_chrome import CONFIGURATION_QSS
    host = workspace
    host.browser.search.setText("LOOP")
    host.browser.results.selectRow(0)
    pages = {}
    for key in ("changes", "releases", "libraries", "training", "recovery", "capture"):
        page = host.open_page(key)
        app.processEvents()
        assert page is not None and not page.isWindow()
        assert page.styleSheet() == CONFIGURATION_QSS
        if hasattr(page, "profile"):
            assert page.profile == host.browser.session.profile
        pages[key] = page
    pages["releases"].comparison_search.setText("LOOP")
    pages["training"].trainee_name.setText("My trainee")
    assert host.open_page("releases") is pages["releases"]
    assert pages["releases"].comparison_search.text() == "LOOP"
    host.open_page("catalog")
    assert host.browser.search.text() == "LOOP"
    assert host.browser.results.currentIndex().isValid()
    wait(app, lambda: not host.busy())
    host.open_page("training")
    assert host.window().close()
    host.window().show()
    app.processEvents()
    assert pages["training"].isVisible()
    assert pages["training"].trainee_name.text() == "My trainee"
    assert host.window().minimumSizeHint().width() < 1000


def test_connection_cannot_change_during_a_review_or_worker(app, workspace):
    host = workspace
    review = QDialog(host)
    review.setWindowTitle("Review change")
    review.show()
    with pytest.raises(ValueError, match="active request"):
        host.switch_connection({"url": "http://localhost:9999", "token": "new"})
    review.close()
    page = host.open_page("training")
    page.run(lambda: time.sleep(.08), lambda _: None)
    assert not host.window().close()
    wait(app, lambda: not host.busy())
    with pytest.raises(ValueError):
        host.switch_connection({"url": "http://localhost:invalid", "token": "new"})
    assert host.window().close()


def test_access_failure_hides_retained_project_tools(app, workspace):
    host = workspace
    page = host.open_page("releases")
    host.browser._failed("Access denied")
    assert host.stack.currentWidget() is host.browser
    assert host.open_page("releases") is None
    assert not page.isVisible()
    assert not host.browser.result_model.rows


def test_capture_uses_global_project_selection(app, workspace):
    page = workspace.open_page("capture")
    page.projects.addItem("Other project", {"id": "other"})
    page.projects.addItem("Pilot", workspace._project)
    page.select_project(workspace._project["id"])
    assert page.projects.currentData()["id"] == workspace._project["id"]
    assert page.projects.isHidden()
    page.select_project("")
    assert page.projects.currentData() is None


def test_library_refresh_preserves_checked_instance_and_properties(app, workspace):
    page = workspace.open_page("libraries")
    row = {"id": "one", "class_id": "pvm", "name": "Pump A", "status": "Current", "revision": 1,
           "properties": [{"name": "Title", "value": "Pump A", "inherited": "Pump", "origin": "Override"}]}
    state = {"project": {"generation": 1}, "classes": [], "instances": [row]}
    page.loaded(state)
    page.instances.setCurrentCell(0, 1)
    page.instances.item(0, 0).setCheckState(Qt.Checked)
    page.loaded({**state, "instances": [{**row, "id": "new", "name": "Pump B"}, row]})
    assert page.rows[page.instances.currentRow()]["id"] == "one"
    assert page.instances.item(1, 0).checkState() == Qt.Checked
    assert page.property_model.rows == row["properties"]


def test_review_defaults_to_no_commit_until_required_fields_are_supplied(app):
    from azeo_control_trainer.core.presentation.configuration_training import BaselineReview
    page = BaselineReview("project", {"url": "http://localhost:8766", "token": "test"},
                          [{"id": "release", "number": 1, "reason": "Validated"}])
    assert not page.create_button.isEnabled()
    page.name.setText("Baseline")
    page.reason.setText("Operator exercise")
    assert page.create_button.isEnabled()
    page.close()


def test_loading_line_animates_only_while_visible(app):
    from PySide6.QtCore import QAbstractAnimation
    from azeo_control_trainer.core.presentation.configuration_chrome import LoadingLine
    line = LoadingLine()
    line.resize(400, 3)
    assert line._animation.state() == QAbstractAnimation.Stopped
    line.show()
    wait(app, lambda: line._phase > 0)
    assert line.height() == 3
    line.hide()
    assert line._animation.state() == QAbstractAnimation.Stopped
    line.close()


def test_selectors_reserve_a_clear_arrow_area(app, workspace):
    from PySide6.QtWidgets import QStyle, QStyleOptionComboBox
    releases = workspace.open_page("releases")
    for selector in (workspace.projects, workspace.browser.kind, releases.target):
        option = QStyleOptionComboBox()
        selector.initStyleOption(option)
        arrow = selector.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxArrow, selector)
        assert arrow.width() >= 28, (selector.accessibleName(), arrow.width())


def test_background_refresh_keeps_tables_usable_and_respects_selection_gates(app, workspace):
    page = workspace.open_page("training")
    wait(app, lambda: not workspace.busy())
    page.run(lambda: time.sleep(.12), lambda _: None)
    assert page.table.isEnabled()
    assert page.trainee_name.isEnabled()
    assert not page.clone_button.isEnabled()
    # Selection signals can run while a request is pending. They must not reopen
    # its command path, and their final gate must survive request completion.
    page.clone_button.setEnabled(True)
    assert not page.clone_button.isEnabled()
    wait(app, lambda: not workspace.busy())
    assert page.clone_button.isEnabled()
    page.run(lambda: time.sleep(.05), lambda _: page.clone_button.setEnabled(False))
    wait(app, lambda: not workspace.busy())
    assert not page.clone_button.isEnabled()


def test_compact_selector_keeps_keyboard_selection(app):
    from PySide6.QtTest import QTest
    from azeo_control_trainer.core.presentation.configuration_chrome import ConfigurationComboBox, CONFIGURATION_QSS
    selector = ConfigurationComboBox()
    selector.setStyleSheet(CONFIGURATION_QSS)
    selector.addItems(["First runtime", "Second runtime"])
    selector.show()
    selector.setFocus()
    QTest.keyClick(selector, Qt.Key_Down)
    assert selector.currentText() == "Second runtime"
    assert selector.toolTip() == "Second runtime"
    selector.close()
