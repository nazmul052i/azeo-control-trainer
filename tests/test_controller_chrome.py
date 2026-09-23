"""Engineering tools share chrome without changing controller or I/O ownership."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication, QMenuBar, QPushButton, QTableWidgetItem, QTabWidget

from azeo_control_trainer.azeo_explorer.virtual_io_simulator import VirtualIoSimulatorDialog


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class Signals:
    name = "Training I/O"
    available = True

    def __init__(self):
        self.rows = [dict(store_tag=tag, kind=kind, plant_unit=unit,
                          simulatable=kind == "AI", value=0, quality="GOOD",
                          simulation={"mode": "static"} if kind == "AI" else {})
                     for tag, kind, unit in (("AI-1", "AI", "U100"),
                                             ("AI-2", "AI", "U100"),
                                             ("AO-1", "AO", "U200"))]
        self.released = []

    def signal_simulation_snapshot(self):
        return [dict(row) for row in self.rows]

    def simulation_capabilities(self):
        return dict(available=self.available, running=self.available, active=2)

    def clear_signal_simulation(self, tag):
        self.released.append(tag)
        return True


def test_virtual_io_explains_unavailability_without_starting_provider(app, tmp_path):
    driver = Signals()
    driver.simulation_capabilities = lambda: dict(available=False, running=False,
                                                 reason="Training provider disconnected")
    dialog = VirtualIoSimulatorDialog(driver, tmp_path)
    try:
        dialog.show()
        app.processEvents()
        assert "Training provider disconnected" in dialog.availability_note.text()
        assert "Explorer > Tools > Virtual I/O" in dialog.availability_note.text()
        assert dialog.availability_note.isVisible()
        assert not dialog.configure_button.isEnabled()
    finally:
        dialog.close()
        dialog.deleteLater()


def test_explicit_icon_and_command_identity_survive_relabel_and_polish(app):
    from PySide6.QtWidgets import QDialog, QVBoxLayout
    from azeo_control_trainer.core.presentation.configuration_chrome import style_button
    from azeo_control_trainer.core.presentation.engineering_dialog import EngineeringMenus, button_command, polish_dialog
    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    button = QPushButton("Run")
    layout.addWidget(button)
    style_button(button, "pause", command_id="process.pause")
    button.setText("Pause training clock")
    polish_dialog(dialog)
    assert button.property("configurationIconName") == "pause"
    assert button.accessibleName() == "Pause training clock"
    menus = EngineeringMenus(dialog)
    action = menus.add(menus.view, button_command(button))
    assert action.objectName() == "process.pause"
    dialog.close()


@pytest.fixture
def vio(app, tmp_path):
    dialog = VirtualIoSimulatorDialog(Signals(), tmp_path)
    dialog.show()
    app.processEvents()
    yield dialog
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def test_virtual_io_has_shared_menus(vio):
    bar = vio.findChild(QMenuBar)
    assert bar is not None
    assert {a.text().replace("&", "") for a in bar.actions()} >= {"File", "View", "Signal", "Help"}


def test_refresh_retains_signal_identity_and_unit_expansion(vio):
    vio.table.selectRow(0)
    provider = vio.units.topLevelItem(0)
    provider.child(1).setExpanded(True)
    vio.driver.rows.reverse()
    vio.refresh()
    assert vio._selected_row()["store_tag"] == "AI-1"
    assert vio.units.topLevelItem(0).child(1).isExpanded()
    vio.filter.setText("AO-1")
    assert vio._selected_row() is None
    assert not vio.configure_button.isEnabled()


def test_focus_signal_replaces_previous_filter_and_scope(vio):
    vio.filter.setText("AO-1")
    vio.table.selectRow(0)
    assert vio.focus_signal("AI-2")
    assert vio._selected_row()["store_tag"] == "AI-2"
    assert vio.configure_button.isEnabled()


def test_context_preserves_multiple_selected_rows(vio, app):
    from PySide6.QtCore import QItemSelectionModel
    from PySide6.QtWidgets import QAbstractItemView
    vio.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
    selection = vio.table.selectionModel()
    for row in (0, 1):
        selection.select(vio.table.model().index(row, 0),
                         QItemSelectionModel.Select | QItemSelectionModel.Rows)
    pos = vio.table.visualItemRect(vio.table.item(1, 0)).center()
    QApplication.sendEvent(vio.table.viewport(), QContextMenuEvent(QContextMenuEvent.Mouse,
        pos, vio.table.viewport().mapToGlobal(pos)))
    menu = vio.menus.context_menu
    assert len(selection.selectedRows()) == 2
    next(a for a in menu.actions() if a.text() == "Copy selected rows").trigger()
    copied = QApplication.clipboard().text()
    assert "AI-1" in copied and "AI-2" in copied and "AO-1" not in copied
    next(a for a in menu.actions() if a.text() == "Release input simulation").trigger()
    assert vio.driver.released == ["AI-2"]
    assert len(selection.selectedRows()) == 2
    menu.close()


def test_context_targets_clicked_signal_and_rechecks_availability(vio, app):
    vio.table.selectRow(0)
    pos = vio.table.visualItemRect(vio.table.item(1, 0)).center()
    event = QContextMenuEvent(QContextMenuEvent.Mouse, pos,
                              vio.table.viewport().mapToGlobal(pos))
    QApplication.sendEvent(vio.table.viewport(), event)
    app.processEvents()
    menu = vio.menus.context_menu
    assert menu.isVisible()
    action = next(a for a in menu.actions() if a.text() == "Release input simulation")
    vio.refresh()
    action.trigger()
    assert vio.driver.released == ["AI-2"]
    vio.driver.available = False
    action.trigger()
    assert vio.driver.released == ["AI-2"]
    menu.close()


def test_hidden_virtual_io_stops_polling(vio, app):
    vio.hide()
    app.processEvents()
    assert not vio.timer.isActive()
    vio.show()
    app.processEvents()
    assert vio.timer.isActive()


def test_number_editor_retains_precision_and_uses_shared_controls(app):
    from azeo_control_trainer.azeo_explorer.virtual_io_simulator import _SimulationEditor
    editor = _SimulationEditor(dict(store_tag="AI-1", value=12.125, range=(0, 100)))
    assert "chevron-up.svg" in editor.styleSheet()
    assert editor.value.decimals() == 6
    assert editor.values()["value"] == 12.125
    assert editor.font().pointSizeF() > 0
    editor.close()


def test_discovery_context_uses_clicked_controller(app):
    from azeo_control_trainer.azeo_explorer.controller_discovery_dialog import ControllerDiscoveryDialog
    from azeo_control_trainer.core.strategy.engine.controller_discovery import ControllerAdvertisement
    calls = []
    dialog = ControllerDiscoveryDialog(add_controller=lambda ad, commission: calls.append((ad.name, commission)) or True)
    dialog._populate([ControllerAdvertisement("ONE", "Node-1"), ControllerAdvertisement("TWO", "Node-2")])
    dialog.show()
    app.processEvents()
    pos = dialog.table.visualItemRect(dialog.table.item(1, 0)).center()
    QApplication.sendEvent(dialog.table.viewport(), QContextMenuEvent(QContextMenuEvent.Mouse,
        pos, dialog.table.viewport().mapToGlobal(pos)))
    menu = dialog.menus.context_menu
    action = next(a for a in menu.actions() if a.text() == "Add as Decommissioned")
    action.trigger()
    assert calls == [("Node-2", False)]
    action.trigger()
    assert len(calls) == 1
    dialog.close()


def test_module_menu_keeps_canvas_target_when_another_tab_closes(app):
    from types import SimpleNamespace
    from azeo_control_trainer.azeo_control_designer.designer_tab import StrategyCanvas
    from azeo_control_trainer.azeo_control_designer.dialogs.controller_status import ControllerStatusDialog
    from azeo_control_trainer.core.datastore.shared_data_store import SharedDataStore
    from azeo_control_trainer.core.strategy.engine.pk_controller import PKController
    from azeo_control_trainer.core.strategy.blocks.utility_blocks import ConstantBlock

    tabs = QTabWidget()
    first, target = StrategyCanvas(), StrategyCanvas()
    target.scene.graph.add_block(ConstantBlock("B1"))
    tabs.addTab(first, "First")
    tabs.addTab(target, "Target")
    store = SharedDataStore()
    store.controller = PKController()
    dialog = ControllerStatusDialog(SimpleNamespace(_canvas_tabs=tabs), store)
    calls = []
    dialog._on_go_online = calls.append
    dialog._refresh()
    command = next(c for c in dialog._module_commands(1) if c.text == "Go Online")
    tabs.removeTab(0)
    assert command.enabled()
    command.callback()
    assert calls == [0]
    store.controller.keylock = True
    assert not command.enabled()
    tabs.removeTab(0)
    assert not command.enabled()
    dialog.close()
    tabs.close()
    first.close()
    target.close()


def test_keyboard_menu_copies_zero_and_rejects_replaced_signal(vio, app):
    vio.table.setCurrentCell(0, 3)
    pos = vio.table.visualItemRect(vio.table.item(0, 3)).center()
    QApplication.sendEvent(vio.table.viewport(), QContextMenuEvent(QContextMenuEvent.Keyboard,
        pos, vio.table.viewport().mapToGlobal(pos)))
    menu = vio.menus.context_menu
    copy = next(a for a in menu.actions() if a.text() == "Copy cell")
    copy.trigger()
    assert QApplication.clipboard().text() == "0"
    release = next(a for a in menu.actions() if a.text() == "Release input simulation")
    vio.driver.rows.reverse()
    vio.refresh()
    release.trigger()
    assert not vio.driver.released
    menu.close()


def test_workbench_menus_drive_clock_and_snapshot_buttons_keep_optional_paths(app, tmp_path, monkeypatch):
    from test_simulation_workbench import _service
    from azeo_control_trainer.azeo_simulation_workbench import window as module

    service, _, _ = _service(tmp_path)
    monkeypatch.setattr(module, "data_dir", lambda: tmp_path / "data")
    calls = []
    for name in ("save_snapshot", "restore_snapshot", "add_marker"):
        monkeypatch.setattr(module.SimulationWorkbenchDialog, name,
            lambda self, value=None, method=name: calls.append((method, value)))
    dialog = module.SimulationWorkbenchDialog(service.store, service.driver, tmp_path)
    dialog.show()
    app.processEvents()
    process = next(a.menu() for a in dialog.menus.bar.actions() if a.text() == "&Process")
    pause = next(a for a in process.actions() if a.text() == "Pause")
    pause.trigger()
    assert service.driver.paused
    assert dialog.clock_buttons["Run"].isEnabled()
    assert not pause.isEnabled()
    assert not dialog.restore_button.isEnabled()
    dialog.snapshot_table.setRowCount(1)
    dialog.snapshot_table.setItem(0, 0, QTableWidgetItem("Checkpoint"))
    dialog.snapshot_table.selectRow(0)
    assert dialog.restore_button.isEnabled()
    for label in ("Save Snapshot…", "Restore Selected", "Add Marker…"):
        next(b for b in dialog.findChildren(QPushButton) if b.text() == label).click()
    assert calls == [("save_snapshot", None), ("restore_snapshot", None), ("add_marker", None)]
    dialog.hide()
    assert not dialog.timer.isActive()
    dialog.close()
