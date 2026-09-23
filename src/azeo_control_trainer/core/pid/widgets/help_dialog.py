"""PidHelpDialog -- in-app HTML parameter reference browser."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QPushButton, QTextBrowser,
)

__all__ = ["PidHelpDialog"]

# Resolve the HTML help file path at module level
from azeo_control_trainer.config.paths import package_dir as _pkg_dir
_HELP_HTML_PATH = str(_pkg_dir() / "core" / "pid" / "help" / "pid_reference.html")


class PidHelpDialog(QDialog):
    """Embedded PID parameter help browser.

    Loads the local, trusted parameter reference into ``QTextBrowser``.
    The help page does not need JavaScript; keeping it on Qt Widgets avoids
    shipping a Chromium runtime solely for one offline document.
    """

    _instance = None  # singleton so only one help window at a time

    @classmethod
    def show_help(cls, parent=None) -> None:
        """Show (or raise) the singleton help dialog."""
        if cls._instance is None or not cls._instance.isVisible():
            cls._instance = cls(parent)
        cls._instance.show()
        cls._instance.raise_()
        cls._instance.activateWindow()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PID Function Block \u2014 Parameter Reference")
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowMinimizeButtonHint
        )
        self.resize(900, 860)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setReadOnly(True)
        try:
            with open(_HELP_HTML_PATH, encoding="utf-8") as fh:
                browser.setHtml(fh.read())
        except FileNotFoundError:
            browser.setPlainText("Help file not found:\n" + _HELP_HTML_PATH)
        root.addWidget(browser, 1)

        # Close button
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(8, 4, 8, 6)
        btn_row.addStretch()
        btn_close = QPushButton("Close")
        btn_close.setFixedWidth(90)
        btn_close.clicked.connect(self.close)
        btn_row.addWidget(btn_close)
        root.addLayout(btn_row)
