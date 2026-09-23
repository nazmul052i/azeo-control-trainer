"""One isolated draft root per process, hosting the existing engineering editors."""
from pathlib import Path
import sys


def main():
    from PySide6.QtCore import QLockFile
    from PySide6.QtWidgets import QApplication, QMessageBox
    from azeo_control_trainer.core.configuration.workspace import DraftWorkspace
    from azeo_control_trainer.core.hmi.theme.fonts import apply_application_font
    from azeo_control_trainer.core.presentation.application_style import apply_application_style
    from azeo_control_trainer.core.presentation.authoring_style import AUTHORING_CHROME_QSS
    from azeo_control_trainer.core.presentation.configuration_catalog import ConfigurationCatalogDialog
    from azeo_control_trainer.core.configuration.catalog_client import CatalogSession
    from azeo_control_trainer.core.presentation.headless import is_headless
    from azeo_control_trainer.core.strategy.serialization import strategy_io
    app = QApplication(sys.argv[:1])
    apply_application_style(app)
    apply_application_font()
    app.setStyleSheet(AUTHORING_CHROME_QSS)
    directory = Path(sys.argv[1]).resolve()
    lock = QLockFile(str(directory / "session.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        if not is_headless():
            QMessageBox.information(None, "Draft already open", "This working draft is already open in another window.")
        return 1
    try:
        workspace = DraftWorkspace(directory)
        strategy_io.STRATEGY_DIR = workspace.root
        window = ConfigurationCatalogDialog(workspace.root,
            session=CatalogSession(workspace.root, profile=workspace.profile),
            draft=workspace, initial_page="changes")
        window.show()
        return app.exec()
    finally:
        lock.unlock()


if __name__ == "__main__":
    raise SystemExit(main())
