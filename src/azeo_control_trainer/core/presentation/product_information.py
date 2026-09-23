"""One truthful product/build identity for Help, About, and support export."""
from __future__ import annotations

from datetime import datetime
from html import escape
import json
import platform
import sys

from PySide6.QtCore import qVersion
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from azeo_control_trainer.config.applications import application
from azeo_control_trainer.config.distribution import (
    COMPONENTS,
    read_build_information,
    read_installation,
)
from azeo_control_trainer.config.paths import logs_dir, project_root, workspace_root
from .authoring_dialog import add_authoring_dialog_header, style_dialog_buttons
from .help_style import styled_help_html


def system_information() -> dict:
    """Return bounded diagnostics: product state, never environment/project data."""
    from azeo_control_trainer.config import licensing

    install = read_installation()
    build = read_build_information()
    return {
        "product": "Azeo Control Trainer",
        "version": build.version,
        "build": build.build,
        "build_commit": build.commit,
        "build_time_utc": build.built_at_utc,
        "source_dirty": build.source_dirty,
        "distribution": build.distribution,
        "target": build.target,
        "build_warning": build.warning,
        "components": sorted(install.components),
        "windows": platform.platform(),
        "python": sys.version,
        "qt": qVersion(),
        "application_path": str(project_root()),
        "workspace_path": str(workspace_root()),
        "logs_path": str(logs_dir()),
        "license": licensing.check(write=False).summary,
    }


def _built_display(value: str) -> str:
    if not value:
        return "Not recorded"
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:  # read_build_information normally filters this.
        return value
    return moment.astimezone().strftime("%Y-%m-%d %H:%M %Z")


def _source_state(value: bool | None, distribution: str) -> str:
    if value is True:
        return "Modified source (validation build)"
    if value is False:
        return "Clean release source"
    if distribution == "Development source checkout":
        return "Live development checkout"
    return "Not recorded"


def product_information_rows(application_id=None, context_rows=()):
    """Rows shared by every product About surface and the Help Center."""
    report = system_information()
    rows = [("Product", report["product"])]
    if application_id is not None:
        rows.append(("Application", application(application_id).title))
    rows.extend((
        ("Version", report["version"]),
        ("Build", report["build"]),
        ("Build time", _built_display(report["build_time_utc"])),
        ("Distribution", report["distribution"]),
        ("Source state", _source_state(
            report["source_dirty"], report["distribution"])),
        ("Target", report["target"] or platform.machine() or "Not recorded"),
        ("Runtime", f"Python {platform.python_version()} · Qt {report['qt']}"),
        ("Licence", report["license"]),
    ))
    rows.extend((str(label), str(value)) for label, value in context_rows)
    rows.extend((
        ("Installed components", ", ".join(
            COMPONENTS[key] for key in report["components"])),
        ("Application files", report["application_path"]),
        ("Workspace", report["workspace_path"]),
        ("Logs", report["logs_path"]),
    ))
    if report["build_warning"]:
        rows.append(("Release integrity", report["build_warning"]))
    return tuple(rows)


def product_information_html(application_id=None, context_rows=(), *, heading=True):
    """Render semantic product information for an existing help browser."""
    body = "<h2>Product and build information</h2>" if heading else ""
    body += "<table>" + "".join(
        f"<tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>"
        for label, value in product_information_rows(application_id, context_rows)
    ) + "</table>"
    body += (
        "<p>Use the version and build values when reporting a problem. "
        "System information is copied or exported locally; Azeo does not send it.</p>"
    )
    return body


class ProductAboutDialog(QDialog):
    """Styled About dialog shared by all engineering applications."""

    def __init__(self, application_id, parent=None, *, context_rows=()):
        super().__init__(parent)
        descriptor = application(application_id)
        from .app_icon import get_app_icon
        self.setWindowIcon(get_app_icon(descriptor.id))
        self.setWindowTitle(f"About {descriptor.title}")
        self.setObjectName("product_about_dialog")
        self.resize(700, 570)

        layout = QVBoxLayout(self)
        add_authoring_dialog_header(
            self,
            layout,
            descriptor.title,
            descriptor.description,
            product=descriptor.title.upper(),
        )
        self.information = QTextBrowser(self)
        self.information.setObjectName("product_information")
        self.information.setOpenExternalLinks(False)
        self.information.setHtml(styled_help_html(
            product_information_html(application_id, context_rows)))
        layout.addWidget(self.information, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        self.copy_button = QPushButton("Copy system information", buttons)
        buttons.addButton(self.copy_button, QDialogButtonBox.ActionRole)
        self.copy_button.clicked.connect(self.copy_system_information)
        buttons.rejected.connect(self.close)
        style_dialog_buttons(buttons)
        layout.addWidget(buttons)

    def copy_system_information(self):
        app = QApplication.instance()
        if app is not None:
            app.clipboard().setText(json.dumps(system_information(), indent=2))
            self.copy_button.setText("Copied")


__all__ = [
    "ProductAboutDialog",
    "product_information_html",
    "product_information_rows",
    "system_information",
]
