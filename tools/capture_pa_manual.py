"""Capture current PA Designer controls from an isolated copy of the APVC procedure.

The project copy is disposable. This tool opens and renders documents only; it
does not save a revision, execute a procedure, or change the shipped project.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ["QT_SCALE_FACTOR"] = "1"


def main() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from azeo_control_trainer.azeo_pa_designer.new_procedure import NewProcedureDialog
    from azeo_control_trainer.azeo_pa_designer.window import PADesignerWindow
    import azeo_control_trainer.core.strategy.blocks  # noqa: F401
    from azeo_control_trainer.core.hmi.theme.fonts import apply_application_font, ensure_font_directory
    from azeo_control_trainer.core.presentation.application_style import apply_application_style

    app = QApplication.instance() or QApplication([])
    apply_application_style(app)
    ensure_font_directory()
    apply_application_font()
    output = ROOT / "docs/images/user_manual"
    output.mkdir(parents=True, exist_ok=True)

    def capture(widget, name: str) -> None:
        app.processEvents()
        QTest.qWait(180)
        target = output / name
        if not widget.grab().save(str(target)):
            raise RuntimeError(f"Could not capture {target}")
        print(target.relative_to(ROOT), flush=True)

    with TemporaryDirectory(prefix="azeo-pa-manual-") as temporary:
        project = Path(temporary) / "ManualProject"
        source = ROOT / "projects/AzeoPlantVirtualController"
        shutil.copytree(source, project, ignore=shutil.ignore_patterns(".lock", ".lock.recover"))
        window = PADesignerWindow(project, graphs_provider=lambda: [])
        window.resize(1440, 860)
        window.show()
        app.processEvents()
        for row in range(window.library_list.count()):
            item = window.library_list.item(row)
            if "rev-003" in str(item.data(Qt.UserRole)):
                window.library_list.setCurrentRow(row)
                window.open_selected()
                break
        else:
            raise RuntimeError("The APVC rev-003 procedure was not found in the disposable library")
        window.status.setText("Documentation example - isolated APVC project copy")

        window.tabs.setCurrentIndex(0)
        window.library_tabs.setCurrentIndex(1)
        window.canvas.select_step("review_scope", reveal=True)
        capture(window, "pa-designer-workflow.png")

        window.library_tabs.setCurrentIndex(0)
        window.tabs.setCurrentIndex(2)
        capture(window, "pa-designer-tag-mappings.png")

        dialog = NewProcedureDialog(window)
        dialog.resize(690, 560)
        dialog.show()
        capture(dialog, "pa-designer-new-procedure.png")
        dialog.close()
        window._saved = window.snapshot()
        window.close()
        app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
