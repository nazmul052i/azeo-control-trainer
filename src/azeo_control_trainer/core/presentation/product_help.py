"""Suite help reuses maintained manuals and the existing modeless help shell."""
from dataclasses import dataclass
import json
from pathlib import Path
import re
from urllib.parse import unquote

from PySide6.QtCore import QUrl
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QPushButton

from azeo_control_trainer.config.paths import project_root
from .headless import is_headless
from .procedure_help import ProcedureHelpCenter
from .product_information import product_information_html, system_information

# Deliberate product inputs; do not sweep third-party reference manuals into a build.
MANUALS = (
    ("INSTALLATION_GUIDE.md", "Installation and maintenance"),
    ("USER_MANUAL.md", "Engineering and operation"),
    ("EXPLORER_HELP.md", "Explorer user manual"),
    ("CONTROL_DESIGNER_HELP.md", "Control Designer user manual"),
    ("GRAPHICS_DESIGNER_HELP.md", "Graphics Designer user manual"),
    ("OPERATOR_STATION_HELP.md", "Operator Station user manual"),
    ("SIMULATION_WORKBENCH_HELP.md", "Simulation Workbench user manual"),
    ("PA_DESIGNER_HELP.md", "PA Designer user manual"),
    ("CONTROL_MODULE_CLASS_TUTORIAL.md", "Control Module Class Tutorial"),
    ("PA_DESIGNER.md", "Procedure automation"),
    ("PVM_FACEPLATE_TUTORIAL.md", "Graphics tutorials"),
    ("SIMULATION_WORKBENCH_GUIDE.md", "Simulation"),
    ("HISTORIAN_WORKSPACE.md", "Process history"),
    ("OPERATOR_THEMES.md", "Operator appearance"),
    ("CONFIGURATION_DATABASE.md", "Configuration administration"),
    ("NATIVE_PLANT_CORE.md", "C++ and Python integration"),
    ("RELEASE_NOTES.md", "Release information"),
)


def slug(text):
    return re.sub(r"[^\w\s-]", "", text.casefold()).strip().replace(" ", "-")


@dataclass(frozen=True)
class ManualTopic:
    key: str
    title: str
    category: str
    markdown: str
    source: str
    anchor: str = ""

    @property
    def search_text(self):
        return f"{self.title} {self.category} {self.markdown}".casefold()

    @property
    def html(self):
        document = QTextDocument()
        document.setMarkdown(self.markdown)
        return document.toHtml()


def manual_topics(root=None):
    directory = Path(root or project_root()) / "docs"
    topics = {}
    for filename, category in MANUALS:
        path = directory / filename
        if not path.is_file():
            raise FileNotFoundError(f"Help is incomplete: {path}. Run Setup to repair.")
        body = path.read_text(encoding="utf-8")
        sections = re.split(r"(?m)^## ", body)
        for index, section in enumerate(sections):
            if index == 0:
                title, content, anchor = category, section, ""
            else:
                title, _, content = section.partition("\n")
                anchor = slug(title)
            key = f"{filename}#{anchor}"
            topics[key] = ManualTopic(key, title, category, content, filename, anchor)
    return topics


class ProductHelpCenter(ProcedureHelpCenter):
    def __init__(self, parent=None):
        from .app_icon import get_app_icon
        from azeo_control_trainer.core.procedures.help_content import HelpTopic, help_topics
        topics = manual_topics()
        topics = {"getting_started": HelpTopic("getting_started", "Welcome to Azeo Help",
            "Start here", "<p>Choose a task in the contents, or search across the installed manuals.</p>"
            '<p><a href="help:INSTALLATION_GUIDE.md#2-choose-your-applications">Choose applications</a> · '
            '<a href="help:INSTALLATION_GUIDE.md#4-first-launch-and-acceptance">First launch</a> · '
            '<a href="help:INSTALLATION_GUIDE.md#8-update-and-repair">Update and repair</a> · '
            '<a href="help:installation">Version and build information</a></p>'
            '<p><a href="help:EXPLORER_HELP.md#start-with-a-task">Explorer manual</a> · '
            '<a href="help:CONTROL_DESIGNER_HELP.md#start-with-a-task">Control Designer manual</a> · '
            '<a href="help:GRAPHICS_DESIGNER_HELP.md#start-with-a-task">Graphics Designer manual</a> · '
            '<a href="help:OPERATOR_STATION_HELP.md#start-with-a-task">Operator Station manual</a> · '
            '<a href="help:SIMULATION_WORKBENCH_HELP.md#start-with-a-task">Simulation manual</a> · '
            '<a href="help:PA_DESIGNER_HELP.md#start-with-a-task">PA manual</a></p>'
            "<p><b>PVM</b> means <b>Process Visualization Module</b>: a reusable HMI component "
            "with configurable graphics, live tag bindings and an optional faceplate.</p>"
            "<p>Product-specific F1 and right-click Block Help remain available while you work. "
            "PA step references below describe the same installed block library.</p>"), **topics}
        for key, topic in help_topics().items():
            topics[f"pa:{key}"] = HelpTopic(f"pa:{key}", topic.title,
                                           "PA · " + topic.category, topic.html)
        topics["installation"] = HelpTopic(
            "installation",
            "Version, build and support information",
            "Start here",
            product_information_html(),
        )
        super().__init__(parent, topics=topics, title="Azeo Help Center")
        self.setWindowIcon(get_app_icon(application_id="help"))
        self.search.setPlaceholderText("Search installation, applications, procedures, trends or recovery…")
        self.browser.document().setBaseUrl(QUrl.fromLocalFile(str(project_root() / "docs") + "/"))
        self.browser.setSearchPaths([str(project_root() / "docs")])
        # Procedure help owns its header and may add further top-level chrome;
        # the command row is intentionally the final layout item.
        row = self.layout().itemAt(self.layout().count() - 1).layout()
        for label, callback in (("System information", lambda: self.show_topic("installation")),
                                ("Export system information…", self.export_information),
                                ("Print topic…", self.print_topic)):
            button = QPushButton(label, self)
            button.clicked.connect(callback)
            row.insertWidget(row.count() - 2, button)

    def open_link(self, url):
        raw = unquote(url.toString())
        if raw.startswith("help:"):
            target = raw[5:]
            if target in self.topics:
                self.show_topic(target)
            elif "pa:" + target in self.topics:
                self.show_topic("pa:" + target)
            return
        if url.scheme() in {"http", "https"}:
            # Manuals remain offline; opening remote references is a deliberate user action.
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(url)
            return
        current = self.topics[self.current_topic_key]
        filename = Path(url.path()).name or getattr(current, "source", "")
        target = filename + "#" + url.fragment()
        if target in self.topics:
            self.show_topic(target)
        elif filename + "#" in self.topics:
            self.show_topic(filename + "#")
            self.browser.scrollToAnchor(url.fragment())
        else:
            # Never let a reference execute a local file or escape into arbitrary filesystem URLs.
            self.results.setText("Reference is not in this help edition; use the topic search.")

    def export_information(self):
        if is_headless():
            return
        name, _ = QFileDialog.getSaveFileName(self, "Export system information",
                                              "Azeo-system-information.json", "JSON (*.json)")
        if not name:
            return
        try:
            Path(name).write_text(json.dumps(system_information(), indent=2), encoding="utf-8")
        except OSError as error:
            QMessageBox.warning(self, "Could not export", str(error))

    def print_topic(self):
        if is_headless():
            return
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter
        printer = QPrinter(QPrinter.HighResolution)
        dialog = QPrintDialog(printer, self)
        if dialog.exec() == QPrintDialog.Accepted:
            self.browser.document().print_(printer)


def open_product_help(parent=None, topic="getting_started"):
    dialog = getattr(parent, "_product_help", None) if parent else None
    if dialog is None:
        try:
            dialog = ProductHelpCenter(parent)
        except Exception as error:
            import logging
            logging.getLogger(__name__).exception("Offline help could not open")
            if not is_headless():
                QMessageBox.warning(parent, "Help unavailable", str(error))
            return None
        if parent:
            parent._product_help = dialog
    dialog.show_topic(topic)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog


def add_help_action(menu, parent):
    from .studio_icons import studio_icon
    action = menu.addAction(studio_icon("help", 16), "Azeo Help Center…")
    action.triggered.connect(lambda: open_product_help(parent))
    return action


def main():
    from .application_style import apply_application_style
    from .app_icon import apply_branding
    from azeo_control_trainer.core.hmi.theme.fonts import apply_application_font, ensure_font_directory
    ensure_font_directory()
    app = QApplication(["Azeo Help"])
    apply_application_style(app)
    apply_application_font()
    apply_branding(app, "help")
    dialog = open_product_help()
    if dialog is None:
        return 1
    dialog.finished.connect(app.quit)
    return app.exec()
