from __future__ import annotations

import os
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImageReader
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.azeo_control_designer.dialogs.help_dialog import (
    ControlDesignerHelpDialog,
)
from azeo_control_trainer.core.presentation.product_help import manual_topics


ROOT = Path(__file__).resolve().parents[1]
TUTORIAL = ROOT / "docs" / "CONTROL_MODULE_CLASS_TUTORIAL.md"
IMAGES = ROOT / "docs" / "images" / "control_module_class_tutorial"
EXPECTED_IMAGES = (
    "01_create_master_class.png",
    "02_class_manager_current.png",
    "03_linked_instance_properties.png",
    "04_stale_instance.png",
    "05_review_class_update.png",
    "06_adopted_revision_history.png",
    "07_tutorial_in_help.png",
)


def test_control_module_class_tutorial_has_complete_real_screenshot_set():
    body = TUTORIAL.read_text(encoding="utf-8")
    assert "Creating a master Control Module Class" in body
    assert "Review / Adopt Update" in body
    assert "Preserve Non-conflicting Deviations" in body
    assert "render_control_module_class_tutorial.py" in body
    for filename in EXPECTED_IMAGES:
        path = IMAGES / filename
        size = QImageReader(str(path)).size()
        assert path.is_file()
        assert size.width() >= 850
        assert size.height() >= 450
        assert filename in body


def test_control_designer_help_renders_the_illustrated_class_workflow():
    app = QApplication.instance() or QApplication([])
    dialog = ControlDesignerHelpDialog()
    assert dialog.show_topic("Control Module Class Tutorial")
    app.processEvents()
    text = dialog._browser.toPlainText()
    assert "Create a Master Control Module Class" in text
    assert "Class Changes" in text
    assert "Instance Deviations" in text
    assert "Resolve conflicts explicitly" in text
    assert dialog._browser.document().baseUrl().toLocalFile().endswith("docs/")
    dialog.close()


def test_suite_help_indexes_the_control_module_class_manual():
    topics = manual_topics(ROOT)
    matches = [topic for topic in topics.values()
               if topic.source == "CONTROL_MODULE_CLASS_TUTORIAL.md"]
    assert matches
    assert any(topic.title == "Control Module Class Tutorial"
               for topic in matches)
    assert all(topic.category == "Control Module Class Tutorial"
               for topic in matches)
