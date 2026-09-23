"""Checks run by the packaged executable, using only its private runtime."""
import importlib
import json
from pathlib import Path
import runpy
import sys


def verify(root, mode, output):
    output.mkdir(parents=True, exist_ok=True)
    assert sys.flags.isolated and sys.flags.ignore_environment and sys.flags.no_site, sys.flags
    assert Path(sys.prefix).resolve() == (root / "runtime").resolve(), sys.prefix
    assert all(Path(path).resolve().is_relative_to(root) for path in sys.path), sys.path
    if mode == "help":
        from PySide6.QtWidgets import QApplication
        from azeo_control_trainer.core.presentation.application_style import apply_application_style
        from azeo_control_trainer.core.presentation.product_help import ProductHelpCenter
        app = QApplication([])
        apply_application_style(app)
        help_window = ProductHelpCenter()
        help_window.show()
        app.processEvents()
        help_window.search.setText("stable_for_sec")
        assert "0 topics" != help_window.results.text()
        assert help_window.show_topic("INSTALLATION_GUIDE.md#8-update-and-repair")
        assert "offline update installers" in help_window.browser.toPlainText()
        manifest = json.loads((root / "portable-manifest.json").read_text(encoding="utf-8"))
        assert help_window.show_topic("installation")
        product_text = help_window.browser.toPlainText()
        assert manifest["version"] in product_text
        assert manifest["source_commit"][:12] in product_text
        assert "Build time" in product_text and "Installed components" in product_text
        from azeo_control_trainer.core.presentation.app_icon import get_app_icon
        assert help_window.windowIcon().pixmap(32, 32).toImage() == get_app_icon(
            application_id="help").pixmap(32, 32).toImage()
        assert help_window.grab().save(str(output / "help.png"))
        help_window.close()
        return {"topics": len(help_window.topics), "offline": True}
    if mode == "environment":
        for name in ("numpy", "scipy", "PySide6", "pyqtgraph", "asyncua", "pymodbus",
                     "yaml", "pydantic", "azeo_control_trainer.core.pa_designer", "psutil", "psycopg", "psycopg_pool", "fastapi", "uvicorn"):
            module = importlib.import_module(name)
            assert Path(module.__file__).resolve().is_relative_to(root), module.__file__
        from azeoplant.core import native
        assert native.enabled(), native.unavailable_message()
        from azeo_control_trainer.azeo_explorer.project_registry import default_project_path
        project = default_project_path()
        from azeo_control_trainer.config.paths import workspace_root
        assert project == workspace_root() / "projects/AzeoPlantVirtualController", project
        return {"native": native.diagnostics(), "project": str(project), "sys_path": sys.path}
    if mode in {"simulator", "station"}:
        script = "_smoke_simulator_ui.py" if mode == "simulator" else "_smoke_virtual_controller_boot.py"
        try:
            runpy.run_path(str(root / "tests" / script), run_name="__main__")
        except SystemExit as result:
            assert result.code in (None, 0), result.code
        return {"boot_check": script}
    return verify_window(root, mode.removeprefix("installed_"), output)


def verify_window(root, mode, output):
    import time
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication, QFormLayout
    from azeo_control_trainer import app as application

    flags = {"explorer": [], "graphics": ["--graphics"], "control": ["--classic"],
             "workbench": ["--simulation"], "procedures": ["--procedures"], "station": ["--station"]}
    expected = {"explorer": "ExplorerWindow", "graphics": "HmiStudioWindow",
                "control": "StrategyDesignerWindow", "workbench": "SimulationWorkbenchDialog",
                "procedures": "PADesignerWindow", "station": "LiveStation"}
    icon_identity = {"explorer": "explorer", "graphics": "graphics_designer",
                     "control": "control_designer", "workbench": "simulation_workbench",
                     "procedures": "pa_designer", "station": "operator_station"}
    original_exec = QApplication.exec
    observed = {}

    def run(_app):
        qt = QApplication.instance()
        deadline = time.monotonic() + 30

        def check():
            try:
                windows = [widget for widget in qt.topLevelWidgets() if widget.isVisible()]
                match = next((w for w in windows if expected[mode] in
                              [kind.__name__ for kind in type(w).__mro__]), None)
                if match is None and time.monotonic() < deadline:
                    QTimer.singleShot(250, check)
                    return
                assert match is not None, [type(w).__name__ for w in windows]
                assert qt.activeModalWidget() is None
                assert qt.palette().color(QPalette.Window).lightness() > 150
                assert qt.palette().color(QPalette.WindowText).lightness() < 150
                from azeo_control_trainer.core.presentation.app_icon import get_app_icon
                assert match.windowIcon().pixmap(32, 32).toImage() == get_app_icon(
                    application_id=icon_identity[mode]).pixmap(32, 32).toImage()
                if mode == "procedures":
                    from azeo_control_trainer.core.procedures.library import block_library
                    # 20 core primitives plus the 38 first-class catalog blocks.
                    assert len(block_library()) == 58, len(block_library())
                    match.library_tabs.setCurrentIndex(1)
                    match.add_type.setCurrentIndex(match.add_type.findData("azeo.procedure.delay"))
                    match.add_step()
                    assert match.draft.data["steps"][1]["library_block_id"] == "azeo.procedure.delay"
                    assert len(match.canvas.nodes) == len(match.draft.data["steps"]) + 2
                    match.canvas.nodes[match.canvas.key("delay_1")].moveBy(24, 12)
                    match.canvas.commit_layout()
                    assert match.draft.data["metadata"]["canvas_layout"]
                    from PySide6.QtGui import QContextMenuEvent
                    canvas = match.canvas
                    point = canvas.mapFromScene(canvas.nodes[canvas.key("delay_1")].sceneBoundingRect().center())
                    context = QContextMenuEvent(QContextMenuEvent.Mouse, point, canvas.viewport().mapToGlobal(point))
                    QApplication.sendEvent(canvas.viewport(), context)
                    assert match._context_menu.actions()
                    match._context_menu.close()
                    assert "Timing" in match.property_groups
                    match.property_filter.setText("delay")
                    assert not match.fields["delay_sec"].isHidden()
                    match.property_filter.clear()
                    assert list(match.ribbon.pages) == ["Home", "Procedure", "View", "Help"]
                    match.open_help_topic("delay")
                    assert match.help_center.current_topic_key == "delay"
                    assert "delay_sec" in match.help_center.browser.toPlainText()
                    assert not match.help_center.isModal()
                    match.help_center.close()
                    assert match.validate_document(), match.status.text()
                    match.tabs.setCurrentIndex(0)
                    match._saved = match.snapshot()
                    observed["library_blocks"] = len(block_library())
                    # Clearing the property filter posts nested layout updates;
                    # capturing inside this callback otherwise records stale geometry.
                    qt.processEvents()
                    qt.processEvents()
                    for group in match.property_groups.values():
                        form = group.form()
                        previous_bottom = -1
                        for row in range(form.rowCount()):
                            label_item = form.itemAt(row, QFormLayout.LabelRole)
                            field_item = form.itemAt(row, QFormLayout.FieldRole)
                            if not label_item or not field_item:
                                continue
                            label, field = label_item.widget(), field_item.widget()
                            if not label.isVisible() or not field.isVisible():
                                continue
                            assert label.geometry().top() > previous_bottom, label.text()
                            assert field.geometry().top() > label.geometry().bottom(), label.text()
                            assert field.height() >= min(field.maximumHeight(), field.minimumSizeHint().height()), label.text()
                            previous_bottom = field.geometry().bottom()
                    observed["inspector_geometry"] = "verified after filtering"
                assert match.grab().save(str(output / f"{mode}.png"))
                observed.update(window=type(match).__name__, title=match.windowTitle(),
                                palette="light", platform=qt.platformName())
                qt.quit()
            except Exception as error:
                observed["error"] = repr(error)
                qt.exit(1)
        QTimer.singleShot(1500, check)
        return original_exec()

    QApplication.exec = run
    from azeo_control_trainer.azeo_explorer.project_registry import default_project_path
    sys.argv = ["azeo-package-check", str(default_project_path()), *flags[mode]]
    try:
        result = application.main()
    finally:
        QApplication.exec = original_exec
    assert not result and "error" not in observed, observed
    assert observed, "No window was inspected"
    return observed
