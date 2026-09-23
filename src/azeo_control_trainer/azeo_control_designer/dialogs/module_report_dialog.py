"""Professional preview, print, and export shell for module reports."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QPageLayout, QPdfWriter, QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrintPreviewDialog, QPrinter
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from azeo_control_trainer.core.presentation.headless import is_headless
from ..module_report import (
    ModuleReport,
    ModuleReportOptions,
    render_module_report_html,
    write_module_report_html,
)

log = logging.getLogger("strategy.module_report")


class ModuleReportDialog(QDialog):
    """Configurable report viewer sharing one HTML snapshot across outputs."""

    def __init__(
        self,
        report_factory: Callable[[], ModuleReport],
        *,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._report_factory = report_factory
        self._report = report_factory()
        self._html = ""
        self.setWindowTitle(f"Module Report - {self._report.module_name}")
        self.resize(1180, 760)
        self.setMinimumSize(840, 560)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._build_ui()
        self._refresh_html()

    @property
    def report(self) -> ModuleReport:
        return self._report

    @property
    def html(self) -> str:
        return self._html

    @property
    def options(self) -> ModuleReportOptions:
        return ModuleReportOptions(
            identity=self._checks["identity"].isChecked(),
            hierarchy=self._checks["hierarchy"].isChecked(),
            configuration=self._checks["configuration"].isChecked(),
            live_values=self._checks["live_values"].isChecked(),
            alarms=self._checks["alarms"].isChecked(),
            execution=self._checks["execution"].isChecked(),
            validation=self._checks["validation"].isChecked(),
            include_default_configuration=self._include_defaults.isChecked(),
            include_inactive_alarms=self._include_inactive.isChecked(),
        )

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(7)

        header = QFrame()
        header.setObjectName("reportHeader")
        header_layout = QHBoxLayout(header)
        title = QLabel("ENGINEERING MODULE REPORT")
        title.setStyleSheet("font-weight: 700; color: #163F63; font-size: 11pt;")
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        self._status = QLabel()
        self._status.setStyleSheet("color: #526879;")
        header_layout.addWidget(self._status)
        root.addWidget(header)

        body = QHBoxLayout()
        body.setSpacing(8)
        controls = QWidget()
        controls.setFixedWidth(220)
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)

        sections = QGroupBox("Report sections")
        section_layout = QVBoxLayout(sections)
        self._checks: dict[str, QCheckBox] = {}
        for key, caption in (
            ("identity", "Module identity"),
            ("hierarchy", "Block hierarchy"),
            ("configuration", "Configurable values"),
            ("live_values", "Live values and quality"),
            ("alarms", "Alarm configuration/state"),
            ("execution", "Algorithm/execution"),
            ("validation", "Validation messages"),
        ):
            check = QCheckBox(caption)
            check.setChecked(True)
            check.toggled.connect(self._refresh_html)
            self._checks[key] = check
            section_layout.addWidget(check)
        controls_layout.addWidget(sections)

        detail = QGroupBox("Detail")
        detail_layout = QVBoxLayout(detail)
        self._include_defaults = QCheckBox("Include schema defaults")
        self._include_defaults.setChecked(True)
        self._include_defaults.toggled.connect(self._refresh_html)
        detail_layout.addWidget(self._include_defaults)
        self._include_inactive = QCheckBox("Include inactive alarms")
        self._include_inactive.setChecked(True)
        self._include_inactive.toggled.connect(self._refresh_html)
        detail_layout.addWidget(self._include_inactive)
        controls_layout.addWidget(detail)

        refresh = QPushButton("Refresh live snapshot")
        refresh.setToolTip("Re-read live values, quality, alarms, and scan statistics")
        refresh.clicked.connect(self.refresh_report)
        controls_layout.addWidget(refresh)
        controls_layout.addStretch(1)
        body.addWidget(controls)

        self._preview = QTextBrowser()
        self._preview.setOpenExternalLinks(False)
        self._preview.setStyleSheet(
            "QTextBrowser { background: white; border: 1px solid #9DABB6; }")
        body.addWidget(self._preview, 1)
        root.addLayout(body, 1)

        buttons = QHBoxLayout()
        preview = QPushButton("Print Preview...")
        preview.clicked.connect(self.show_print_preview)
        buttons.addWidget(preview)
        print_button = QPushButton("Print...")
        print_button.clicked.connect(self.print_report)
        buttons.addWidget(print_button)
        html_button = QPushButton("Export HTML...")
        html_button.clicked.connect(self.choose_export_html)
        buttons.addWidget(html_button)
        pdf_button = QPushButton("Export PDF...")
        pdf_button.clicked.connect(self.choose_export_pdf)
        buttons.addWidget(pdf_button)
        buttons.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        root.addLayout(buttons)

    def refresh_report(self) -> ModuleReport:
        """Take a new point-in-time snapshot and keep current section choices."""
        self._report = self._report_factory()
        self.setWindowTitle(f"Module Report - {self._report.module_name}")
        self._refresh_html()
        return self._report

    def _refresh_html(self, *_args) -> None:
        self._html = render_module_report_html(self._report, self.options)
        self._preview.setHtml(self._html)
        self._status.setText(
            f"{self._report.error_count} error(s)  |  "
            f"{self._report.warning_count} warning(s)  |  "
            f"{'ONLINE' if self._report.live_available else 'OFFLINE'}")

    def _document(self) -> QTextDocument:
        document = QTextDocument(self)
        document.setHtml(self._html)
        return document

    def _printer(self) -> QPrinter:
        printer = QPrinter(QPrinter.HighResolution)
        printer.setDocName(f"{self._report.module_name} Module Report")
        printer.setPageOrientation(printer.pageLayout().Orientation.Landscape)
        return printer

    def show_print_preview(self) -> bool:
        """Open native preview; return False instead of blocking headless CI."""
        if is_headless():
            log.info("Module report print preview skipped in headless mode")
            return False
        printer = self._printer()
        document = self._document()
        dialog = QPrintPreviewDialog(printer, self)
        dialog.setWindowTitle(f"Print Preview - {self._report.module_name}")
        dialog.paintRequested.connect(document.print_)
        dialog.exec()
        return True

    def print_report(self) -> bool:
        """Print through the native dialog; never display it headlessly."""
        if is_headless():
            log.info("Module report print dialog skipped in headless mode")
            return False
        printer = self._printer()
        if QPrintDialog(printer, self).exec() != QDialog.Accepted:
            return False
        self._document().print_(printer)
        return True

    def choose_export_html(self) -> Path | None:
        if is_headless():
            log.info("Module report HTML chooser skipped in headless mode")
            return None
        path, _selected = QFileDialog.getSaveFileName(
            self, "Export Module Report", f"{self._report.module_name}.html",
            "HTML report (*.html);;All files (*)")
        if not path:
            return None
        try:
            return self.export_html(path)
        except (OSError, RuntimeError, ValueError) as error:
            self._show_error("HTML Export Failed", error)
            return None

    def choose_export_pdf(self) -> Path | None:
        if is_headless():
            log.info("Module report PDF chooser skipped in headless mode")
            return None
        path, _selected = QFileDialog.getSaveFileName(
            self, "Export Module Report", f"{self._report.module_name}.pdf",
            "PDF report (*.pdf);;All files (*)")
        if not path:
            return None
        try:
            return self.export_pdf(path)
        except (OSError, RuntimeError, ValueError) as error:
            self._show_error("PDF Export Failed", error)
            return None

    def export_html(self, path: str | Path) -> Path:
        """Export to an explicit path; usable without a display server."""
        return write_module_report_html(self._report, path, self.options)

    def export_pdf(self, path: str | Path) -> Path:
        """Export the same HTML snapshot to PDF, including headless sessions."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # QPrinter asks Windows for a native print-engine COM service even
        # when its output format is PDF. Offscreen qualification hosts do not
        # register that service (0x80040155), so an ostensibly headless export
        # emitted a fatal native exception. QPdfWriter is a pure file-backed
        # QPagedPaintDevice and accepts the same QTextDocument print path.
        writer = QPdfWriter(str(target))
        writer.setTitle(f"{self._report.module_name} Module Report")
        writer.setCreator("Azeo Control Designer")
        writer.setResolution(144)
        writer.setPageOrientation(QPageLayout.Landscape)
        self._document().print_(writer)
        return target

    def _show_error(self, title: str, error: Exception) -> None:
        log.exception("%s: %s", title, error)
        if not is_headless():
            QMessageBox.critical(self, title, str(error))


__all__ = ["ModuleReportDialog"]
