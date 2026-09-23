"""Explorer presents Local Virtual I/O as its own physical-network node."""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from azeo_control_trainer.core.datastore.shared_data_store import (  # noqa: E402
    SharedDataStore,
)
from azeo_control_trainer.azeo_explorer.window import ExplorerWindow  # noqa: E402


PROJECT = ROOT / "projects" / "AzeoPlantVirtualController"


class _Driver:
    def __init__(self, running=True):
        self.running = running
        self.last_error = ""
        self.starts = 0
        self.stops = 0
        self.start_entered = threading.Event()
        self.start_release = threading.Event()
        self.start_release.set()
        self.stop_entered = threading.Event()
        self.stop_release = threading.Event()
        self.stop_release.set()

    def start(self):
        self.starts += 1
        self.start_entered.set()
        self.start_release.wait(2.0)
        self.running = True
        self.last_error = ""
        return True

    def stop(self):
        self.stops += 1
        self.stop_entered.set()
        self.stop_release.wait(2.0)
        self.running = False

    def health(self):
        return {
            "name": "APVC-VIO-1",
            "type": "local_virtual_io",
            "running": self.running,
            "can_start": True,
            "source": "APVC-CTRL-1",
            "signals": 612,
            "reads_published": 386,
            "writes_enqueued": 150,
            "output_readbacks_published": 150,
            "last_error": self.last_error,
        }


class _UnavailableDriver(_Driver):
    def __init__(self):
        super().__init__(running=False)
        self.last_error = "provider configuration is invalid"

    def health(self):
        health = super().health()
        health["can_start"] = False
        return health


class _StuckDriver(_Driver):
    def stop(self):
        self.stops += 1
        self.last_error = "field I/O scan worker did not stop"
        # It deliberately remains running: Restart must not create a second
        # provider session over the still-owned output lease.


class _Executive:
    @staticmethod
    def online_runtimes():
        return []


class _Designer:
    def __init__(self):
        self.online_calls = 0

    @staticmethod
    def controller_executive():
        return _Executive()

    @staticmethod
    def open_graphs():
        return []

    def auto_go_online(self):
        self.online_calls += 1
        return True


class _Host:
    def __init__(self, driver):
        self.designer = _Designer()
        self.plant_driver = driver
        self.station_calls = 0

    def _live_station(self):
        self.station_calls += 1
        return object()


def _find_payload(item, kind: str):
    payload = item.data(0, Qt.UserRole)
    if payload and payload[0] == kind:
        return item
    for index in range(item.childCount()):
        answer = _find_payload(item.child(index), kind)
        if answer is not None:
            return answer
    return None


def _wait_for_worker(app, explorer) -> None:
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        app.processEvents()
        worker = explorer._virtual_io_worker
        if worker is None:
            app.processEvents()
            return
        if not worker.isRunning():
            worker.wait(1000)
            app.processEvents()
            app.processEvents()
            return
        time.sleep(0.005)
    raise AssertionError("Virtual I/O lifecycle worker did not finish")


def _menu_labels(menu) -> list[str]:
    return [action.text().replace("&", "") for action in menu.actions()
            if not action.isSeparator()]


def test_virtual_io_is_not_drawn_as_eioc() -> None:
    app = QApplication.instance() or QApplication([])
    store = SharedDataStore()
    driver = _Driver()
    store.field_io_driver = driver
    host = SimpleNamespace(designer=_Designer(), plant_driver=driver)
    explorer = ExplorerWindow(store=store, area=PROJECT, designer=host)
    root = explorer.tree.topLevelItem(0)
    virtual_io = _find_payload(root, "virtual_io")
    assert virtual_io is not None
    assert "Local Virtual I/O" in virtual_io.text(0)
    assert "running" in virtual_io.text(0)
    assert _find_payload(root, "eioc") is None
    signals = _find_payload(virtual_io, "virtual_io_signals")
    assert signals is not None and signals.childCount() >= 10
    assert explorer.open_virtual_io_status()["signals"] == 612
    explorer.close()
    app.processEvents()


def test_virtual_io_tools_and_context_menu_drive_lifecycle() -> None:
    app = QApplication.instance() or QApplication([])
    store = SharedDataStore()
    driver = _Driver(running=False)
    store.field_io_driver = driver
    host = SimpleNamespace(designer=_Designer(), plant_driver=driver)
    explorer = ExplorerWindow(store=store, area=PROJECT, designer=host)

    tool_actions = explorer._virtual_io_actions
    explorer._sync_virtual_io_actions(tool_actions)
    assert tool_actions["start"].isEnabled()
    assert not tool_actions["stop"].isEnabled()
    assert not tool_actions["restart"].isEnabled()

    config = explorer._project_virtual_io()
    context = explorer._menu_for(("virtual_io", config))
    labels = _menu_labels(context)
    assert all(label in labels for label in (
        "Start Simulator", "Stop Simulator", "Restart Simulator",
        "Provider Status…"))
    tools = next(action.menu() for action in explorer.menuBar().actions()
                 if action.text().replace("&", "") == "Tools")
    virtual_io = next(action.menu() for action in tools.actions()
                      if action.text().replace("&", "") == "Virtual I/O")
    assert virtual_io is not None
    applications = next(
        action.menu() for action in explorer.menuBar().actions()
        if action.text().replace("&", "") == "Applications")
    assert "Start Plant Simulator" in _menu_labels(applications)
    assert "Virtual I/O Simulator…" in _menu_labels(applications)
    file_menu = next(
        action.menu() for action in explorer.menuBar().actions()
        if action.text().replace("&", "") == "File")
    assert "Project Administrator…" in _menu_labels(file_menu)
    assert explorer.open_virtual_io_simulator()["available"] is False
    assert explorer.open_project_administrator()
    assert len(explorer._virtual_io_launcher_actions) == 2
    assert all(action.isEnabled()
               for action in explorer._virtual_io_launcher_actions)
    from azeo_control_trainer.azeo_explorer.help_content import (
        EXPLORER_HELP,
    )
    assert "Start Plant Simulator" in EXPLORER_HELP
    assert "Plant Simulator Status" in EXPLORER_HELP
    assert "Virtual I/O Simulator" in EXPLORER_HELP
    assert "Project Administrator" in EXPLORER_HELP

    driver.start_release.clear()
    explorer._virtual_io_launcher_actions[0].trigger()
    assert driver.start_entered.wait(1.0)
    app.processEvents()
    root = explorer.tree.topLevelItem(0)
    assert "starting" in _find_payload(root, "virtual_io").text(0).lower()
    assert not tool_actions["start"].isEnabled()
    driver.start_release.set()
    _wait_for_worker(app, explorer)
    assert driver.running and driver.starts == 1
    assert explorer._virtual_io_health(driver, config)["state"] == "running"

    explorer._sync_virtual_io_actions(tool_actions)
    assert not tool_actions["start"].isEnabled()
    assert tool_actions["stop"].isEnabled()
    assert tool_actions["restart"].isEnabled()
    assert all(action.text() == "Plant Simulator Status…"
               and action.isEnabled()
               for action in explorer._virtual_io_launcher_actions)
    explorer._virtual_io_launcher_actions[0].trigger()
    app.processEvents()
    assert driver.starts == 1 and explorer._virtual_io_worker is None

    driver.stop_release.clear()
    assert explorer.stop_virtual_io()
    assert driver.stop_entered.wait(1.0)
    app.processEvents()
    root = explorer.tree.topLevelItem(0)
    assert "stopping" in _find_payload(root, "virtual_io").text(0).lower()
    driver.stop_release.set()
    _wait_for_worker(app, explorer)
    assert not driver.running and driver.stops == 1
    assert explorer._virtual_io_health(driver, config)["state"] == "stopped"

    explorer.close()
    app.processEvents()


def test_operator_station_command_starts_io_and_downloads_modules() -> None:
    """The visible Explorer workflow must produce live Station values."""
    app = QApplication.instance() or QApplication([])
    store = SharedDataStore()
    driver = _Driver(running=False)
    store.field_io_driver = driver
    host = _Host(driver)
    explorer = ExplorerWindow(
        store=store, area=PROJECT, designer=host)

    assert explorer.open_operator_station()
    _wait_for_worker(app, explorer)
    # Completion defers the download out of QThread's signal stack.
    app.processEvents()
    app.processEvents()

    assert driver.running and driver.starts == 1
    assert host.designer.online_calls == 1
    assert host.station_calls == 1
    assert not explorer._pending_operator_station

    driver.stop()
    explorer.close()
    app.processEvents()


def test_unavailable_provider_cannot_pretend_to_start() -> None:
    app = QApplication.instance() or QApplication([])
    store = SharedDataStore()
    driver = _UnavailableDriver()
    store.field_io_driver = driver
    host = SimpleNamespace(designer=_Designer(), plant_driver=driver)
    explorer = ExplorerWindow(store=store, area=PROJECT, designer=host)

    health = explorer._sync_virtual_io_actions()
    assert health["state"] == "faulted"
    assert not health["can_start"]
    assert not explorer._virtual_io_actions["start"].isEnabled()
    assert not explorer._virtual_io_actions["restart"].isEnabled()
    assert not explorer.start_virtual_io()
    assert driver.starts == 0

    explorer.close()
    app.processEvents()


def test_virtual_io_menu_never_controls_an_undeclared_plant_driver() -> None:
    app = QApplication.instance() or QApplication([])
    store = SharedDataStore()
    bundled_plant = _Driver(running=False)
    host = SimpleNamespace(designer=_Designer(), plant_driver=bundled_plant)
    explorer = ExplorerWindow(store=store, area=PROJECT, designer=host)
    explorer._project_virtual_io = lambda: {}

    explorer._sync_virtual_io_actions()

    assert not explorer._virtual_io_actions["start"].isEnabled()
    assert not explorer._virtual_io_actions["stop"].isEnabled()
    assert not explorer._virtual_io_actions["restart"].isEnabled()
    assert not explorer.start_virtual_io()
    assert bundled_plant.starts == 0
    assert all(not action.isVisible()
               for action in explorer._virtual_io_launcher_actions)

    explorer.close()
    app.processEvents()


def test_restart_aborts_when_provider_did_not_finish_stopping() -> None:
    app = QApplication.instance() or QApplication([])
    store = SharedDataStore()
    driver = _StuckDriver(running=True)
    store.field_io_driver = driver
    host = SimpleNamespace(designer=_Designer(), plant_driver=driver)
    explorer = ExplorerWindow(store=store, area=PROJECT, designer=host)

    assert explorer.restart_virtual_io()
    _wait_for_worker(app, explorer)

    assert driver.stops == 1
    assert driver.starts == 0
    health = explorer._virtual_io_health(
        driver, explorer._project_virtual_io())
    assert health["state"] == "faulted"
    assert "did not stop" in health["last_error"]

    explorer.close()
    app.processEvents()
