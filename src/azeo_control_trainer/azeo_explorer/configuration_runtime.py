"""Isolated release target using Control Designer's executive and the real Live Station.

Its private tag store has no external transports. Losing the engineering API
does not stop a loaded controller or an operator's accepted display.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from uuid import uuid4

from PySide6.QtCore import QLockFile, QTimer, Qt
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from azeo_control_trainer.core.configuration.client import ConfigurationClient, ServiceUnavailable
from azeo_control_trainer.core.configuration.documents import ConfigurationError
from azeo_control_trainer.core.configuration.runtime_packages import RuntimeAdapter, materialize, read_package
from azeo_control_trainer.core.datastore.shared_data_store import SharedDataStore
from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore
from azeo_control_trainer.core.hmi.theme.fonts import apply_application_font
from azeo_control_trainer.core.presentation.application_style import apply_application_style
from azeo_control_trainer.core.presentation.authoring_style import AUTHORING_CHROME_QSS
from azeo_control_trainer.core.presentation.configuration_catalog import _table, _workers, _shutdown
from azeo_control_trainer.core.presentation.configuration_editing import _Command
from azeo_control_trainer.core.strategy.engine.pk_controller import PKController
from azeo_control_trainer.core.strategy.serialization.strategy_io import write_json_transactional
from azeo_control_trainer.azeo_control_designer import ControllerExecutive
from azeo_control_trainer.azeo_operator_station import LiveStation, PvmDeployment


class RuntimeWindow(QDialog):
    def __init__(self, directory):
        super().__init__(None, Qt.Window)
        self.directory = Path(directory)
        self.profile = json.loads((self.directory / "target.json").read_text(encoding="utf-8"))
        self.client = ConfigurationClient(self.profile["url"], self.profile["token"], timeout=8)
        self.boot = str(uuid4())
        self.prefix = f"/v1/runtime-targets/{self.profile['id']}"
        self.worker = None
        self.completion = None
        self.restored = False
        self.closing = False
        self.store = SharedDataStore()
        self.store.pk_controller = PKController(name=self.profile["name"])
        self.display_store = DisplayStore(self.directory / "station")
        self.display_store.root.mkdir(parents=True, exist_ok=True)
        self.deployment = PvmDeployment(self.display_store, "CON-01")
        self.adapter = RuntimeAdapter(self.store, self.display_store, self.deployment)
        self.executive = ControllerExecutive(self.store, parent=self)
        self.executive.start()
        self.station = None
        self.training_driver = None
        self.simulation_service = None
        self.inspectors = []
        self.setWindowTitle("Release runtime — " + self.profile["name"])
        self.setStyleSheet(AUTHORING_CHROME_QSS)
        self.resize(920, 500)
        layout = QVBoxLayout(self)
        self.status = QLabel("Connecting to configuration service…")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addWidget(QLabel("Isolated pilot • private tag store • external field I/O is not attached"))
        self.table, self.model = _table([("path", "Loaded module"), ("revision", "Revision"),
                                         ("scan_count", "Scans"), ("state", "Runtime")], "Loaded releases")
        layout.addWidget(self.table)
        row = QHBoxLayout()
        training = QPushButton("Start isolated training process")
        training.clicked.connect(self.start_training_process)
        row.addWidget(training)
        station = QPushButton("Open Operator Live")
        station.clicked.connect(self.open_station)
        row.addWidget(station)
        inspect = QPushButton("Inspect selected module")
        inspect.clicked.connect(self.inspect_selected)
        row.addWidget(inspect)
        self.keylock = QPushButton("Controller keylock: unlocked")
        self.keylock.setCheckable(True)
        self.keylock.toggled.connect(self.set_keylock)
        row.addWidget(self.keylock)
        layout.addLayout(row)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        app = QApplication.instance()
        if not getattr(app, "_configuration_catalog_shutdown", False):
            app.aboutToQuit.connect(_shutdown)
            app._configuration_catalog_shutdown = True
        QTimer.singleShot(0, self.restore)

    def work(self, operation, ready):
        if self.worker is not None or self.closing:
            return
        worker = _Command(operation)
        self.worker = worker
        _workers.add(worker)
        worker.ready.connect(ready)
        worker.failed.connect(lambda error: self.status.setText(f"Engineering connection: {error}. Loaded control continues."))
        def finished():
            self.worker = None
            _workers.discard(worker)
            worker.deleteLater()
        worker.finished.connect(finished)
        worker.start()

    def restore(self):
        def read():
            journal = self.directory / "runtime.json"
            modules = json.loads(journal.read_text(encoding="utf-8")).get("modules", []) if journal.exists() else []
            groups = {}
            for module in modules:
                groups.setdefault(module["package_hash"], []).append(module["path"])
            result = []
            for digest, paths in groups.items():
                root = self.display_store.root / "_release_packages" / digest
                result.append((read_package(root), root, paths))
            return result
        def restored(groups):
            try:
                for package, root, paths in groups:
                    self.adapter.install_controller(package, root, paths)
                self.restored = True
                self.status.setText("Recovered loaded releases. Connecting to deployment jobs…")
            except Exception as error:
                self.status.setText("Recovery refused: " + str(error))
        self.work(read, restored)

    def poll(self):
        if self.worker or not self.restored or self.closing:
            return
        observation = self.adapter.observation(scanning=self.executive.is_running)
        self.model.set_rows([{**row, "state": "Scanning" if row["active"] else "Inactive"}
                             for row in observation["objects"] if "scan_count" in row])
        completion = self.completion
        journal = self.adapter.journal()
        def synchronize():
            self.client.request(self.prefix + "/hello", {"boot": self.boot})
            if completion:
                # The durable local receipt precedes the remote acknowledgment.
                # On a lost response the same job is replayed without resetting
                # an already loaded module or creating another publication.
                write_json_transactional(self.directory / "runtime.json", journal)
                self.client.request(self.prefix + f"/jobs/{completion['job']['id']}",
                                    {"boot": self.boot, "state": completion["state"], "receipt": completion["receipt"]})
            self.client.request(self.prefix + "/report", {"boot": self.boot, "report": observation})
            pending = self.client.request(self.prefix + "/pending", {"boot": self.boot})
            if not pending:
                return None
            job = pending[0]
            try:
                package = self.client.request(f"/v1/projects/{self.profile['project_id']}/releases/{job['release_id']}")
                root = materialize(package, self.display_store.root / "_release_packages")
            except ServiceUnavailable:
                raise
            except (ConfigurationError, OSError) as error:
                receipt = {"message": str(error), "stage": "package verification"}
                self.client.request(self.prefix + f"/jobs/{job['id']}",
                                    {"boot": self.boot, "state": "failed", "receipt": receipt})
                return receipt
            write_json_transactional(self.directory / "intent.json", {"job": job, "package_hash": package["manifest"]["package_hash"]})
            if job["state"] == "requested":
                self.client.request(self.prefix + f"/jobs/{job['id']}", {"boot": self.boot, "state": "staged",
                                    "receipt": {"package_hash": package["manifest"]["package_hash"]}})
            return job, package, root
        def ready(result):
            if self.closing:
                return
            self.completion = None
            if result is None:
                self.status.setText("Connected • deployment queue synchronized • operator Refresh controls display updates")
                return
            if isinstance(result, dict):
                self.status.setText("Deployment stopped: " + result["message"])
                return
            job, package, root = result
            receipt = {"package_hash": package["manifest"]["package_hash"], "components": job["components"]}
            state = "delivered"
            try:
                if "controller" in job["components"]:
                    self.adapter.install_controller(package, root)
                    receipt["controller"] = "loaded"
                if "station" in job["components"]:
                    self.adapter.publish_station(package, job)
                    receipt["station"] = "published; operator Refresh remains explicit"
                    if self.station is not None:
                        self.station.sync_chrome()
                self.status.setText("Release delivered locally; recording target acknowledgment…")
            except Exception as error:
                state = "failed"
                receipt["message"] = str(error)
                self.status.setText("Deployment stopped: " + str(error))
            self.completion = {"job": job, "state": state, "receipt": receipt}
        self.work(synchronize, ready)

    def set_keylock(self, locked):
        self.store.pk_controller.keylock = locked
        self.keylock.setText("Controller keylock: " + ("locked" if locked else "unlocked"))

    def open_station(self):
        if self.station is None or getattr(self.station, "_closing", False):
            self.station = LiveStation(self.deployment, self.adapter.graphs,
                                       config_root=self.display_store.root,
                                       control_designer_opener=self.open_engineering,
                                       simulation_service=self.simulation_service,
                                       training_root=self.directory / "training",
                                       history_path=self.directory / "history.sqlite3")
            self.station.setWindowTitle("Operator Live — " + self.profile["name"])
            available = self.screen().availableGeometry()
            self.station.resize(min(1440, available.width()), min(900, available.height() - 40))
            names = self.deployment.displays()
            if names:
                self.station.show_display(names[0])
        self.station.show()
        self.station.raise_()

    def start_training_process(self):
        if self.training_driver is not None:
            self.status.setText("Isolated training process is already attached")
            return
        if self.station is not None and not getattr(self.station, "_closing", False):
            self.status.setText("Close Operator Live before attaching its training process, then reopen it")
            return
        packages = {entry["metadata"]["package_hash"] for entry in self.adapter.loaded.values()}
        if len(packages) != 1 or self.worker is not None:
            self.status.setText("Load one complete baseline release before starting its training process")
            return
        root = self.display_store.root / "_release_packages" / next(iter(packages))
        self.executive.stop()
        def start():
            from .configuration_training_runtime import attach_training_process
            return attach_training_process(self.store, root)
        def ready(driver):
            from azeo_control_trainer.core.simulation.workbench import SimulationWorkbenchService
            if self.closing:
                driver.stop()
                return
            self.training_driver = driver
            self.simulation_service = SimulationWorkbenchService(self.store, driver, self.directory,
                                                                executive=self.executive)
            self.status.setText("Isolated training process started at its default state. Open Operator Live to select or capture an exercise; Start restores its baseline.")
            self.executive.start()
        self.work(start, ready)
        if self.worker is not None:
            self.worker.failed.connect(lambda _error: self.executive.start() if not self.closing else None)

    def inspect_selected(self):
        index = self.table.currentIndex()
        if index.isValid():
            module = self.model.rows[index.row()]
            self.open_engineering(self.adapter.loaded[module["object_id"]]["runtime"].compiled.graph.name)

    def open_engineering(self, module):
        from azeo_control_trainer.azeo_control_designer import StrategyCanvas
        entry = next((item for item in self.adapter.loaded.values() if item["runtime"].compiled.graph.name == module), None)
        if entry is None:
            return
        dialog = QDialog(self, Qt.Window)
        dialog.setWindowTitle("Control Designer — loaded " + module)
        dialog.resize(1100, 750)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Released controller configuration • edit through Shared editing; capture tuning through Release Manager"))
        canvas = StrategyCanvas(dialog)
        canvas.scene.load_graph(entry["runtime"].compiled.graph)
        canvas.scene.set_structure_locked(True)
        canvas.scene.set_live_mode(True)
        canvas.scene.apply_exec_order(entry["runtime"].compiled.exec_order)
        layout.addWidget(canvas)
        self.inspectors.append(dialog)
        dialog.show()

    def closeEvent(self, event):
        if self.closing:
            return super().closeEvent(event)
        self.closing = True
        self.timer.stop()
        self.executive.stop()
        # Finish the session while its provider still supplies simulation time.
        # Detaching first rewinds the recorded shutdown event/duration to zero.
        if self.station is not None:
            self.station.close()
        if self.training_driver is not None:
            self.training_driver.stop()
        report = self.adapter.observation(scanning=False)
        pending = self.worker
        def disconnect():
            if pending is not None:
                try:
                    pending.wait()
                except RuntimeError:
                    pass  # Already finished and deleted by its Qt owner.
            try:
                self.client.request(self.prefix + "/report", {"boot": self.boot, "report": report, "online": False})
            except Exception:
                pass  # The server's heartbeat expiry covers an unavailable API.
        worker = _Command(disconnect)
        _workers.add(worker)
        def finished():
            _workers.discard(worker)
            worker.deleteLater()
        worker.finished.connect(finished)
        worker.start()
        for inspector in self.inspectors:
            inspector.close()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv[:1])
    apply_application_style(app)
    apply_application_font()
    directory = Path(sys.argv[1]).resolve()
    lock = QLockFile(str(directory / "runtime.lock"))
    if not lock.tryLock(0):
        return 2
    window = RuntimeWindow(directory)
    window.show()
    result = app.exec()
    lock.unlock()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
