"""Controller Properties — how a PK controller is added to Control Designer.

The controller node has always been *declared*, not instantiated: the area's
`_project.json` carries a `controller` section beside `field_io`, and an
area that declares nothing runs the defaults (PK-CTLR-1, PK100, unlocked).
This dialog is that declaration with a face — the Azeo gesture of adding
a controller to the system, done the trainer's way:

- **Name and model apply live.** The node on `store.controller` is updated
  in place; the DST gauge and every keylock check follow immediately,
  because there is only one node object and everything reads it.
- **The declaration is written back to `_project.json`**, so the choice
  survives the session. The file is read, the one section replaced, and
  everything else preserved byte-for-byte in spirit — this dialog owns one
  key, not the file.
- **The Modbus server setting is honest about its timing**: the endpoint
  binds at launch, so enabling it here says so rather than pretending to
  hot-plug a socket.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

import json
import logging
from pathlib import Path

from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
    QLineEdit, QSpinBox, QVBoxLayout,
)
from azeo_control_trainer.core.presentation.engineering_dialog import (
    EngineeringMenus, button_command, polish_dialog,
)

from azeo_control_trainer.core.strategy.engine.pk_controller import (
    PKController, PKModel,
)

log = logging.getLogger("strategy.controller_properties")


def _default_project_path() -> Path:
    # Read through the module, never a from-imported copy (hard-won item
    # 19): the launcher rebinds STRATEGY_DIR after import.
    from azeo_control_trainer.core.strategy.serialization import strategy_io

    return Path(strategy_io.STRATEGY_DIR) / "_project.json"


class ControllerPropertiesDialog(QDialog):
    """Declare the area's controller: name, model, server endpoint."""

    def __init__(self, store, project_path: Path | str | None = None,
                 parent=None):
        super().__init__(parent)
        self._store = store
        self._project_path = (Path(project_path) if project_path
                              else _default_project_path())
        controller = getattr(store, "controller", None) or PKController()
        self.setWindowTitle("Controller Properties")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._name = QLineEdit(controller.name)
        form.addRow("Node name:", self._name)

        usage = controller.dst_usage(store)
        self._model = AuthoringComboBox()
        for model in PKModel:
            self._model.addItem(f"{model.name} — {model.value} DSTs",
                                model)
        self._model.setCurrentIndex(
            self._model.findData(controller.model))
        form.addRow("Model:", self._model)
        self._usage = QLabel(
            f"{usage} DST(s) in use by this area's field points — "
            "counted from the tag database.")
        self._usage.setWordWrap(True)
        form.addRow("", self._usage)

        server_config = self._current_server_config()
        self._serve = QCheckBox("Serve the store over Modbus TCP "
                                "(external HMIs, a second trainer)")
        self._serve.setChecked(bool(server_config))
        form.addRow("Server:", self._serve)
        self._port = QSpinBox()
        self._port.setRange(1024, 65535)
        self._port.setValue(int(server_config.get("port", 5020) or 5020))
        form.addRow("Server port:", self._port)
        note = QLabel("The server endpoint binds at launch — enabling it "
                      "here takes effect the next time the trainer opens.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {UI.text_muted}; font-size: 9pt;")
        form.addRow("", note)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Save
                                   | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        polish_dialog(self, title="Controller Properties",
                      subtitle="Configure the node identity, capacity and external access.", mark="io_config")
        self._port.setEnabled(self._serve.isChecked())
        self._serve.toggled.connect(self._port.setEnabled)
        self.menus = EngineeringMenus(self, search=self._name,
            help_text="Name and model apply to the existing controller when you save. "
            "The Modbus server setting takes effect at the next application launch. Cancel leaves the declaration unchanged.")
        self.menus.add(self.menus.file, button_command(buttons.button(QDialogButtonBox.Save), shortcut="Ctrl+S"))
        self.resize(590, 430)

    # ------------------------------------------------------------ the file
    def _read_project(self) -> dict | None:
        try:
            return json.loads(self._project_path.read_text(
                encoding="utf-8"))
        except (OSError, ValueError) as error:
            log.warning("Could not read %s: %s", self._project_path, error)
            return None

    def _current_server_config(self) -> dict:
        project = self._read_project() or {}
        for entry in project.get("areas", ()):
            config = entry.get("controller") or {}
            if config:
                return dict(config.get("modbus_server") or {})
        return {}

    def _save(self) -> None:
        name = self._name.text().strip() or "PK-CTLR-1"
        model = self._model.currentData() or PKModel.PK100

        # Live first: one node object, everything reads it.
        controller = getattr(self._store, "controller", None)
        if controller is None:
            controller = PKController()
            self._store.controller = controller
        controller.name = name
        controller.model = model

        # Then durable: the declaration into the area file, and only the
        # declaration — this dialog owns one key of that file.
        project = self._read_project()
        if project is not None and project.get("areas"):
            section: dict = {"name": name, "model": model.name}
            if self._serve.isChecked():
                section["modbus_server"] = {"port": int(self._port.value())}
            project["areas"][0]["controller"] = section
            try:
                self._project_path.write_text(
                    json.dumps(project, indent=2, ensure_ascii=False)
                    + "\n", encoding="utf-8")
                log.info("Controller declared in %s: %s (%s)",
                         self._project_path.name, name, model.name)
            except OSError as error:
                log.warning("Could not write %s: %s",
                            self._project_path, error)
        self.accept()
