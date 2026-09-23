"""Bounded boot of the focused plant UI with the actual native provider."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from azeo_control_trainer import app as application
    from azeo_control_trainer.azeo_simulation_workbench.window import SimulationWorkbenchDialog

    original_exec = QApplication.exec
    captured = {}

    def run(_app):
        qt = QApplication.instance()
        deadline = time.monotonic() + 60

        def settled(predicate, timeout=5.0):
            """Wait on the condition, never on a fixed delay: the engine thread
            reports Pause, Step and Run transitions asynchronously, and a check
            made in the same event-loop turn as the click reads the old state."""
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                if predicate():
                    return True
                qt.processEvents()
                time.sleep(0.01)
            return bool(predicate())

        def verify():
            try:
                window = next((widget for widget in qt.topLevelWidgets()
                               if isinstance(widget, SimulationWorkbenchDialog)
                               and widget.plant_only), None)
                if window is not None:
                    driver = window.service.driver
                    runtimes = driver.store.get_strategy_runtimes()
                    if driver.running and len(runtimes) == 160 and all(runtime.scan_count for runtime in runtimes):
                        window.refresh()
                        assert window.plant_tools.units.rowCount() == 10
                        assert window.virtual_table.rowCount() == 612
                        assert window.plant_tools.parameters.rowCount() > 0
                        health = driver.health()["provider"]["detail"]
                        assert health["core"] == "cpp", health
                        assert health["native_units"] == 10
                        assert not health["bpcs_enabled"]
                        assert [widget for widget in qt.topLevelWidgets() if widget.isVisible()] == [window]
                        assert not any(name.startswith("azeoplant.ui") for name in sys.modules)
                        capabilities = window.service.process_capabilities
                        window.clock_buttons["Pause"].click()
                        assert settled(lambda: capabilities()["paused"]), capabilities()
                        before = capabilities()["sim_time"]
                        window.clock_buttons["Step"].click()
                        assert settled(lambda: capabilities()["sim_time"] > before), capabilities()
                        window.clock_buttons["Run"].click()
                        assert settled(lambda: capabilities()["running"]), capabilities()
                        assert settled(window.open_signal_button.isEnabled)
                        from PySide6.QtCore import Qt
                        from PySide6.QtTest import QTest
                        from PySide6.QtGui import QContextMenuEvent
                        units = window.plant_tools.units
                        pos = units.visualItemRect(units.item(0, 0)).center()
                        QApplication.sendEvent(units.viewport(), QContextMenuEvent(
                            QContextMenuEvent.Mouse, pos, units.viewport().mapToGlobal(pos)))
                        menu = window.menus.context_menu
                        assert menu.isVisible()
                        view_signals = next(action for action in menu.actions()
                                            if action.objectName() == "plant.signals")
                        QTest.mouseClick(menu, Qt.LeftButton, pos=menu.actionGeometry(view_signals).center())
                        assert window.tabs.currentWidget() is window.virtual_page
                        qt.processEvents()
                        table = window.virtual_table
                        row = next(i for i in range(table.rowCount()) if not table.isRowHidden(i))
                        tag = table.item(row, 0).text()
                        table.scrollToItem(table.item(row, 0))
                        pos = table.visualItemRect(table.item(row, 0)).center()
                        QApplication.sendEvent(table.viewport(), QContextMenuEvent(
                            QContextMenuEvent.Mouse, pos, table.viewport().mapToGlobal(pos)))
                        menu = window.menus.context_menu
                        signal_action = next(action for action in menu.actions()
                                             if action.objectName() == "virtual_io.inspect")
                        QTest.mouseClick(menu, Qt.LeftButton, pos=menu.actionGeometry(signal_action).center())
                        assert window._signal_simulator.isVisible()
                        assert window._signal_simulator._selected_row()["store_tag"] == tag
                        window._signal_simulator.close()
                        window.tabs.setCurrentWidget(window.plant_tools.unit_page)
                        output = ROOT / "logs/focused-simulator-ui.png"
                        output.parent.mkdir(exist_ok=True)
                        assert window.grab().save(str(output))
                        window.tabs.setCurrentWidget(window.plant_tools.fault_page)
                        window.plant_tools.faults.selectRow(0)
                        qt.processEvents()
                        assert window.grab().save(str(output.with_name("focused-simulator-disturbances.png")))
                        captured["driver"] = driver
                        exercise_controls(window, 0, time.monotonic())
                        return
                if time.monotonic() > deadline:
                    raise RuntimeError("Focused simulator did not reach a live plant")
                QTimer.singleShot(250, verify)
            except Exception:  # noqa: BLE001
                captured["error"] = traceback.format_exc()
                qt.exit(1)

        def exercise_controls(window, index, previous):
            # Include real logging and ordinary periodic refreshes. Suppressing
            # logging made the boot smoke miss the disk stalls seen by users.
            from PySide6.QtCore import Qt
            from PySide6.QtTest import QTest
            try:
                now = time.monotonic()
                if index:
                    captured.setdefault("click_gaps_ms", []).append((now - previous) * 1000)
                tab = index % window.tabs.count()
                QTest.mouseClick(window.tabs.tabBar(), Qt.LeftButton,
                                 pos=window.tabs.tabBar().tabRect(tab).center())
                assert window.isEnabled() and window.tabs.currentIndex() == tab
                assert qt.activeModalWidget() is None
                if index == 23:
                    gaps = captured["click_gaps_ms"]
                    # Best of three batches tolerates an unrelated Windows
                    # scheduling spike; the old I/O-tab stall hit every batch.
                    batches = [max(gaps[:7]), max(gaps[7:15]), max(gaps[15:])]
                    assert min(batches) < 2000, f"UI stalled in all click batches: {batches} ms"
                    captured["ok"] = True
                    qt.quit()
                else:
                    QTimer.singleShot(150, lambda: exercise_controls(window, index + 1, now))
            except Exception:  # noqa: BLE001
                captured["error"] = traceback.format_exc()
                qt.exit(1)
        QTimer.singleShot(250, verify)
        return original_exec()

    QApplication.exec = run
    watchdog = threading.Timer(90, lambda: os._exit(124))
    watchdog.daemon = True
    watchdog.start()
    previous_argv = sys.argv
    try:
        from azeo_control_trainer.azeo_explorer.project_registry import default_project_path
        # Installed builds must exercise the seeded workspace project. Using
        # the bundle copy here hid broken provider discovery from Setup QA.
        sys.argv = ["simulator-ui-smoke", str(default_project_path()), "--simulator"]
        result = application.main()
    finally:
        QApplication.exec = original_exec
        sys.argv = previous_argv
        watchdog.cancel()
    if result or not captured.get("ok"):
        print("[FAIL]", captured.get("error", result))
        return 1
    assert not captured["driver"].running
    print("[ok] Focused simulator: ten C++ units, 612 signals, 160 host modules; "
          "Run/Pause/Step work, unit/signal context menus open the selected signal, 24 tab clicks accepted with logging active; "
          "only the plant window is visible, clean shutdown.")
    print("[info] Largest scheduled-click interval:", round(max(captured["click_gaps_ms"])), "ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
