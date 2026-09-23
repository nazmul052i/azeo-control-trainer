"""Capture real PVM Designer and flat field states at an explicit Windows scale."""
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["QT_QPA_PLATFORM"] = "windows" if "--native" in sys.argv else "offscreen"


def main():
    from PySide6.QtCore import QSettings, qInstallMessageHandler
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget
    from azeo_control_trainer.core.hmi.theme.fonts import ensure_font_directory, apply_application_font
    from azeo_control_trainer.azeo_graphics_designer.configurator.designer import PvmConfigDesigner
    from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
    ensure_font_directory()
    app = QApplication([])
    apply_application_font()
    requested_dpr = (float(sys.argv[sys.argv.index("--effective-scale") + 1])
                     if "--effective-scale" in sys.argv else None)
    if requested_dpr is not None and "AZEO_VERIFY_EFFECTIVE_DPR" not in os.environ:
        # QT_SCALE_FACTOR multiplies the monitor's native DPR. A factor of 2
        # on a 150% desktop tests 300%, not the requested 200% configuration.
        native_dpr = app.primaryScreen().devicePixelRatio() / float(os.environ.get("QT_SCALE_FACTOR", "1"))
        env = dict(os.environ, QT_SCALE_FACTOR=str(requested_dpr / native_dpr),
                   AZEO_VERIFY_EFFECTIVE_DPR=str(requested_dpr))
        return subprocess.run([sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]], env=env).returncode
    messages = []
    qInstallMessageHandler(lambda kind, context, message: messages.append(message))
    scale = os.environ.get("QT_SCALE_FACTOR", "1")
    output = ROOT / "logs/ui-controls" / (f"effective-{requested_dpr:g}" if requested_dpr is not None else scale)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="azeo-fields-") as temporary:
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, temporary)
        window = PvmConfigDesigner(Path(temporary), pvm_class="HP_C_Valve")
        window.show()
        QTest.qWait(150)
        effective_dpr = window.devicePixelRatioF()
        if requested_dpr is not None:
            assert abs(effective_dpr - requested_dpr) < .01, (requested_dpr, effective_dpr)
        screen = window.screen().availableGeometry()
        assert screen.contains(window.frameGeometry()), (screen, window.frameGeometry())
        for widget in window.findChildren(QWidget):
            assert widget.font().pointSizeF() > 0, type(widget).__name__
        window.grab().save(str(output / "pvm-designer.png"))
        selector = next(w for w in window.findChildren(AuthoringComboBox)
                        if w.isVisible() and w.count() > 1)
        selector.showPopup()
        QTest.qWait(80)
        assert screen.contains(selector.view().window().frameGeometry())
        selector.view().window().grab().save(str(output / "selector-popup.png"))
        selector.hidePopup()
        window.unsaved = False
        window.close()
        app.processEvents()
    report = {"scale_factor": scale, "effective_dpr": effective_dpr, "requested_dpr": requested_dpr,
              "qt_messages": messages, "success": not messages}
    (output / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    return 0 if not messages else 1


if __name__ == "__main__":
    raise SystemExit(main())
