#!/usr/bin/env python
"""Render the Control Module Class tutorial from the real Qt dialogs.

The capture uses an isolated copy of the shipped training flow loop and a
temporary class library.  It never edits a project module, downloads control
logic, or changes the user's configured strategy root.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QPainter, QPen  # noqa: E402
from PySide6.QtWidgets import QAbstractItemView, QApplication  # noqa: E402

import azeo_control_trainer.core.strategy.blocks  # noqa: E402,F401
from azeo_control_trainer.azeo_control_designer.dialogs.help_dialog import (  # noqa: E402
    ControlDesignerHelpDialog,
)
from azeo_control_trainer.azeo_control_designer.dialogs.module_class_manager import (  # noqa: E402
    ModuleClassDefinitionDialog,
    ModuleClassManagerDialog,
    ModuleClassUpdateReviewDialog,
    ModuleInstanceCreationDialog,
)
from azeo_control_trainer.core.presentation.application_style import (  # noqa: E402
    apply_application_style,
)
from azeo_control_trainer.core.hmi.theme.fonts import (  # noqa: E402
    apply_application_font,
    ensure_font_directory,
)
from azeo_control_trainer.core.strategy.module_classes import (  # noqa: E402
    ModuleClassLibrary,
)
from azeo_control_trainer.core.strategy.serialization import strategy_io  # noqa: E402
from azeo_control_trainer.core.strategy.serialization.strategy_io import (  # noqa: E402
    graph_from_document,
)


OUT = ROOT / "docs" / "images" / "control_module_class_tutorial"
SOURCE = ROOT / "src" / "strategies" / "azeo_training" / "control" / "FIC-102.json"


class _ProjectTree:
    def __init__(self):
        self.registered = []

    def register_control_modules(self, paths, area_id=None, unit_name=None):
        self.registered.append((list(paths), area_id, unit_name))

    def refresh(self):
        pass

    @staticmethod
    def areas():
        return [{
            "area_id": "tutorial",
            "name": "Process Area",
            "units": [
                {"name": "U100 · Feed Preparation", "modules": []},
                {"name": "U200 · Reaction", "modules": []},
            ],
        }]


def _canvas(document: dict, path: Path):
    graph, comments = graph_from_document(document, strict=True)
    return SimpleNamespace(
        scene=SimpleNamespace(
            graph=graph,
            get_comments_data=lambda: list(comments),
        ),
        runtime=None,
        file_path=str(path),
    )


def _pump(rounds: int = 5) -> None:
    app = QApplication.instance()
    for _ in range(rounds):
        app.processEvents()


def _capture(widget, filename: str, callouts: list[tuple[float, float, str]]) -> None:
    """Capture one real widget and add restrained numbered annotations."""
    widget.show()
    _pump()
    image = widget.grab().toImage()
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setFont(QFont("DejaVu Sans", 10, QFont.DemiBold))
    metrics = painter.fontMetrics()
    for number, (x_fraction, y_fraction, label) in enumerate(callouts, start=1):
        x = int(image.width() * x_fraction)
        y = int(image.height() * y_fraction)
        circle = QRectF(x, y, 28, 28)
        label_width = min(620, metrics.horizontalAdvance(label) + 22)
        label_x = x + 35
        if label_x + label_width > image.width() - 12:
            label_x = max(12, x - label_width - 8)
        label_box = QRectF(label_x, y - 1, label_width, 30)
        painter.setPen(QPen(QColor("#FFFFFF"), 1.0))
        painter.setBrush(QColor(15, 67, 110, 238))
        painter.drawEllipse(circle)
        painter.drawRoundedRect(label_box, 4, 4)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(circle, Qt.AlignCenter, str(number))
        painter.drawText(
            label_box.adjusted(9, 0, -6, 0),
            Qt.AlignVCenter | Qt.AlignLeft,
            label,
        )
    painter.end()
    path = OUT / filename
    if not image.save(str(path)):
        raise RuntimeError(f"Could not save {path}")
    print(path.relative_to(ROOT))
    widget.hide()
    _pump(2)


def _select_public_properties(dialog: ModuleClassDefinitionDialog) -> int:
    selected = {
        "AI1/CONFIG/tag": "PVTag",
        "PID1/CONFIG/sp_init": "InitialSetpoint",
        "PID1/CONFIG/GAIN": "ControllerGain",
        "AO1/CONFIG/tag": "OutputTag",
    }
    selected_rows = []
    for row in range(dialog.parameters.rowCount()):
        path = dialog.parameters.item(row, 2).text()
        if path not in selected:
            continue
        dialog.parameters.item(row, 0).setCheckState(Qt.Checked)
        dialog.parameters.item(row, 1).setText(selected[path])
        selected_rows.append(row)
    if not selected_rows:
        raise RuntimeError("Tutorial public properties were not found")
    return selected_rows[0]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ensure_font_directory()
    app = QApplication.instance() or QApplication(sys.argv[:1])
    apply_application_style(app)
    apply_application_font()

    source_document = json.loads(SOURCE.read_text(encoding="utf-8"))
    source_document["name"] = "FIC-201"
    source_document["description"] = (
        "Tutorial source for a reusable master flow-control module class.")

    with tempfile.TemporaryDirectory(prefix="azeo_module_class_tutorial_") as folder:
        workspace = Path(folder)
        control = workspace / "control"
        control.mkdir()
        source_path = control / "FIC-201.json"
        strategy_io.write_json_transactional(source_path, source_document)
        strategy_io.STRATEGY_DIR = workspace

        source_canvas = _canvas(source_document, source_path)
        definition_dialog = ModuleClassDefinitionDialog(source_document)
        definition_dialog.resize(1180, 720)
        definition_dialog.name_edit.setText("STANDARD_FLOW_LOOP")
        definition_dialog.description_edit.setText(
            "Standard AI-PID-scaler-AO regulatory flow loop")
        first_public_row = _select_public_properties(definition_dialog)
        definition_dialog.show()
        _pump()
        definition_dialog.parameters.scrollToItem(
            definition_dialog.parameters.item(first_public_row, 0),
            QAbstractItemView.PositionAtCenter,
        )
        _pump()
        _capture(
            definition_dialog,
            "01_create_master_class.png",
            [
                (0.02, 0.03, "Name the governed master class"),
                (0.02, 0.18, "Expose only approved instance properties"),
                (0.52, 0.18, "Defaults are captured from the source module"),
            ],
        )
        public_parameters = definition_dialog.declared_parameters()
        definition_dialog.close()

        tree = _ProjectTree()
        library = ModuleClassLibrary(workspace / "_module_classes")
        manager = ModuleClassManagerDialog(
            tree, [source_canvas], active_canvas=source_canvas, library=library)

        def apply_document(canvas, document, _reason):
            graph, comments = graph_from_document(document, strict=True)
            canvas.scene.graph = graph
            canvas.scene.get_comments_data = lambda: list(comments)

        manager.documentRequested.connect(apply_document)
        definition = manager.create_from_active(
            "STANDARD_FLOW_LOOP",
            "Standard AI-PID-scaler-AO regulatory flow loop",
            public_parameters,
        )
        manager.resize(1220, 760)
        _capture(
            manager,
            "02_class_manager_current.png",
            [
                (0.02, 0.14, "Published class and immutable revision"),
                (0.31, 0.13, "Active source is linked and current"),
                (0.31, 0.23, "Public values show inherited or override origin"),
                (0.01, 0.86, "Lifecycle commands never update instances silently"),
            ],
        )

        creation = ModuleInstanceCreationDialog(
            definition, tree.areas(), existing_names={"FIC-201"})
        creation.name_edit.setText("FIC-202")
        creation.unit_combo.setCurrentIndex(1)
        override_values = {
            "PVTag": "FT-202.PV",
            "InitialSetpoint": "92.0",
            "OutputTag": "FV-202.OUT",
        }
        for row in range(creation.table.rowCount()):
            name = creation.table.item(row, 1).text()
            if name not in override_values:
                continue
            creation.table.item(row, 0).setCheckState(Qt.Checked)
            creation.table.item(row, 4).setText(override_values[name])
        creation.resize(1040, 720)
        _capture(
            creation,
            "03_linked_instance_properties.png",
            [
                (0.02, 0.18, "Name and place the module in one workflow"),
                (0.02, 0.42, "Checked rows are explicit instance overrides"),
                (0.52, 0.42, "Unchecked rows continue to inherit the class"),
            ],
        )
        target = manager.create_instance(
            creation.module_name(),
            overrides=creation.overrides(),
            area_id=creation.area_id(),
            unit_name=creation.unit_name(),
            target_directory=control,
        )
        creation.close()
        instance_document = json.loads(target.read_text(encoding="utf-8"))

        # Publish a reviewed class change from the current source instance.
        source_canvas.scene.graph.blocks["pid"].config.params["RESET"] = 25.0
        manager.active_canvas = source_canvas
        manager.reload()
        updated = manager.update_from_active(note="Standard reset tuning")

        # Keep a non-conflicting local documentation deviation on FIC-202 so
        # the review screenshot demonstrates all three update inputs.
        instance_canvas = _canvas(instance_document, target)
        instance_canvas.scene.graph.blocks["ai"].config.params["label"] = \
            "North Train Flow"
        manager.canvases.append(instance_canvas)
        manager.active_canvas = instance_canvas
        manager.reload()
        manager.tabs.setCurrentWidget(manager.instances)
        _capture(
            manager,
            "04_stale_instance.png",
            [
                (0.31, 0.13, "STALE means the library has a newer revision"),
                (0.31, 0.24, "Every linked instance reports revision and deviations"),
                (0.49, 0.87, "Review before adopting the new revision"),
            ],
        )

        plan = manager.update_plan()
        review = ModuleClassUpdateReviewDialog(plan)
        review.resize(1120, 700)
        _capture(
            review,
            "05_review_class_update.png",
            [
                (0.02, 0.07, "Summary separates class changes and local deviations"),
                (0.02, 0.15, "Review class changes, deviations, and exact conflicts"),
                (0.46, 0.86, "Adopt or preserve non-conflicting deviations explicitly"),
            ],
        )
        review.close()

        manager.refresh_active(preserve_deviations=True)
        manager.tabs.setCurrentWidget(manager.revisions)
        _capture(
            manager,
            "06_adopted_revision_history.png",
            [
                (0.31, 0.13, "The linked instance is current at revision 2"),
                (0.31, 0.24, "History retains both current and archived revisions"),
                (0.01, 0.86, "Unlink only when future inheritance is unwanted"),
            ],
        )
        if updated.revision != 2:
            raise RuntimeError("Tutorial expected revision 2")
        manager.close()

        help_dialog = ControlDesignerHelpDialog()
        help_dialog.resize(1120, 760)
        if not help_dialog.show_topic("Control Module Class Tutorial"):
            raise RuntimeError("Control Module Class Tutorial help topic is missing")
        _capture(
            help_dialog,
            "07_tutorial_in_help.png",
            [
                (0.01, 0.11, "The tutorial is available from Control Designer Help"),
                (0.22, 0.10, "The in-app workflow matches the maintained guide"),
            ],
        )
        help_dialog.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
