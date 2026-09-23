"""The installed help must be reachable, searchable, local and self-contained."""
import os
from pathlib import Path
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.core.presentation.product_help import (
    ProductHelpCenter, manual_topics, open_product_help, system_information,
)


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


def test_manuals_have_substantive_install_operate_recover_topics():
    topics = manual_topics()
    for section in ("2-choose-your-applications", "4-first-launch-and-acceptance",
                    "8-update-and-repair", "9-backup-recovery-and-rollback",
                    "11-troubleshooting-by-symptom", "14-native-integration-and-compatibility"):
        assert len(topics[f"INSTALLATION_GUIDE.md#{section}"].markdown) > 650
    assert len(topics) > 65


def test_each_product_has_an_illustrated_offline_manual():
    topics = manual_topics()
    for filename in ("EXPLORER_HELP.md", "CONTROL_DESIGNER_HELP.md",
                     "GRAPHICS_DESIGNER_HELP.md", "OPERATOR_STATION_HELP.md",
                     "SIMULATION_WORKBENCH_HELP.md", "PA_DESIGNER_HELP.md"):
        source = Path(__file__).resolve().parents[1] / "docs" / filename
        document = source.read_text(encoding="utf-8")
        assert len(document.split()) > 3_000
        assert document.count("![") >= 2
        assert filename + "#start-with-a-task" in topics
        for relative in re.findall(r"!\[[^]]*\]\(([^)]+)\)", document):
            assert (source.parent / relative).is_file(), (filename, relative)


def test_help_search_deep_links_history_and_reuse(app):
    from PySide6.QtWidgets import QWidget
    parent = QWidget()
    dialog = open_product_help(parent)
    assert open_product_help(parent) is dialog
    assert not dialog.isModal()
    dialog.search.setText("stable_for_sec")
    assert any(not item.isHidden() for item in dialog._items.values())
    dialog.open_link(QUrl("help:INSTALLATION_GUIDE.md#8-update-and-repair"))
    assert "offline update installers" in dialog.browser.toPlainText()
    dialog.open_link(QUrl("help:timing"))
    assert dialog.current_topic_key == "pa:timing"
    dialog.navigate(-1)
    assert dialog.current_topic_key == "INSTALLATION_GUIDE.md#8-update-and-repair"
    dialog.search.setText("no-such-azeo-topic-abc123")
    assert "No matching topic" in dialog.browser.toPlainText()
    dialog.search.clear()
    assert dialog.show_topic("installation")
    dialog.open_link(QUrl("file:///C:/Windows/System32/cmd.exe"))
    assert dialog.current_topic_key == "installation"
    assert "not in this help edition" in dialog.results.text()
    parent.close()
    parent.deleteLater()
    app.processEvents()


def test_help_navigation_is_bounded(app):
    dialog = ProductHelpCenter()
    for index in range(300):
        dialog.show_topic("installation" if index % 2 else "getting_started")
    assert len(dialog._history) == 128
    dialog.close()
    dialog.deleteLater()


def test_system_report_has_no_project_contents_or_environment(app):
    report = system_information()
    assert set(report) == {
        "product", "version", "build", "build_commit", "build_time_utc",
        "source_dirty", "distribution", "target", "build_warning",
        "components", "windows", "python", "qt", "application_path",
        "workspace_path", "logs_path", "license",
    }
    assert report["product"] == "Azeo Control Trainer"
    assert report["version"] == "0.4.0"


def test_help_exposes_release_and_support_identity(app):
    dialog = ProductHelpCenter()
    try:
        assert dialog.show_topic("installation")
        text = dialog.browser.toPlainText()
        for label in ("Version", "Build", "Distribution", "Source state",
                      "Runtime", "Installed components", "Workspace", "Logs"):
            assert label in text
        assert "0.4.0" in text
    finally:
        dialog.close()
        dialog.deleteLater()
