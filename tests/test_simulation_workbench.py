"""System-level simulation orchestration and Explorer workbench tests."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO = Path(__file__).resolve().parent.parent
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(REPO / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from azeo_control_trainer.core.simulation.workbench import (  # noqa: E402
    OperatorChangeJournal, SimulationScope, SimulationWorkbenchService,
)
from azeo_control_trainer.core.strategy.blocks.io_blocks import (  # noqa: E402
    AIBlock, DIBlock,
)
from azeo_control_trainer.core.strategy.model.strategy_graph import (  # noqa: E402
    StrategyGraph,
)
from azeo_control_trainer.azeo_simulation_workbench.window import (  # noqa: E402
    SimulationWorkbenchDialog,
)
from azeo_control_trainer.azeo_control_designer.executive import (  # noqa: E402
    ControllerExecutive,
)


class _Store:
    def __init__(self, runtimes):
        self.runtimes = list(runtimes)
        self.writes = []

    def get_strategy_runtimes(self):
        return list(self.runtimes)

    def queue_write(self, path, value):
        self.writes.append((path, value))


class _Driver:
    def __init__(self):
        self.running = True
        self.signals = (1, 2)
        self.paused = False
        self.speed = 1.0
        self.sim_time = 12.5
        self.process = {"level": 25.0}
        self.profiles = {}
        self.scans = 0

    def process_simulation_capabilities(self):
        return {
            "available": True, "running": not self.paused,
            "paused": self.paused, "speed_factor": self.speed,
            "sim_time": self.sim_time, "snapshot_supported": True,
            "step_supported": True, "heartbeat": 8, "reason": "",
        }

    def pause_process_simulation(self):
        self.paused = True

    def resume_process_simulation(self):
        self.paused = False

    def step_process_simulation(self, count=1):
        if not self.paused:
            raise RuntimeError("pause before stepping")
        self.sim_time += count * 0.1

    def set_process_simulation_speed(self, factor):
        self.speed = float(factor)
        return self.speed

    def save_process_simulation_snapshot(self, path):
        Path(path).write_text(json.dumps({
            "version": 2, "speed_factor": self.speed, **self.process,
        }), encoding="utf-8")
        return Path(path)

    def load_process_simulation_snapshot(self, path):
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        self.process = {"level": payload["level"]}
        self.speed = float(payload.get("speed_factor", 1.0))
        return {"tags": 1}

    def signal_simulation_snapshot(self):
        rows = []
        for tag in ("FIELD.AI", "FIELD.DI"):
            rows.append({
                "store_tag": tag, "signal": tag, "direction": "read",
                "kind": "DI" if tag.endswith("DI") else "AI",
                "plant_unit": "U100", "value": 1, "quality": "GOOD",
                "simulation": self.profiles.get(tag), "description": "",
            })
        return rows

    def clear_all_signal_simulations(self):
        count = len(self.profiles)
        self.profiles.clear()
        return count

    def configure_signal_simulation(self, tag, **values):
        self.profiles[tag] = {"store_tag": tag, **values}

    def health(self):
        return {"running": True, "source": "TEST"}

    def scan_once(self):
        self.scans += 1


class _Executive:
    def __init__(self):
        self.is_running = True
        self.steps = 0
        self.speed = 1.0

    def stop(self):
        self.is_running = False

    def start(self):
        self.is_running = True

    def simulation_step_once(self):
        self.steps += 1
        return 2

    def set_simulation_speed_factor(self, value):
        self.speed = float(value)
        return self.speed


def _runtime(name: str, block):
    graph = StrategyGraph(name)
    graph.add_block(block)
    return SimpleNamespace(
        compiled=SimpleNamespace(graph=graph), is_online=True,
        is_debug_paused=False,
    )


def _service(tmp_path):
    ai = AIBlock("AI-101")
    ai.config.params.update({
        "tag": "FIELD.AI", "mode": "AUTO", "normal_mode": "AUTO",
        "simulate_enabled": False, "SIMULATE": 0.0,
        "SIMULATE_STATUS_GOOD": True, "HI_LIM": float("inf"),
    })
    ai._apply_config()
    di = DIBlock("DI-201")
    di.config.params.update({
        "tag": "FIELD.DI", "mode": "AUTO", "normal_mode": "AUTO",
        "SIMULATE_D": False, "SIMULATE_VAL": False,
    })
    di._apply_config()
    store = _Store((_runtime("M100", ai), _runtime("M200", di)))
    driver = _Driver()
    return SimulationWorkbenchService(store, driver, tmp_path, "tester"), ai, di


def test_scope_io_modes_and_dynamic_initialization(tmp_path) -> None:
    service, ai, di = _service(tmp_path)
    assert [row["module"] for row in service.io_rows()] == ["M100", "M200"]
    assert service.set_simulation_enabled(SimulationScope(("M100",)), True) == 1
    assert ai.config.params["simulate_enabled"] is True
    assert di.config.params["SIMULATE_D"] is False
    service.set_io_value("M100", ai.id, 42.5, "BAD")
    assert ai.config.params["SIMULATE"] == 42.5
    assert ai.config.params["SIMULATE_STATUS_GOOD"] is False
    assert service.set_setup_mode(SimulationScope(("M100",))) == 1
    assert ai._actual_mode == "MAN"
    assert service.set_normal_mode(SimulationScope(("M100",))) == 1
    assert ai._actual_mode == "AUTO"
    assert service.initialize_dynamic_blocks(SimulationScope(("M100",))) == 1


def test_process_controls_coordinate_controller_and_field_exchange(tmp_path) -> None:
    service, _ai, _di = _service(tmp_path)
    executive = _Executive()
    service.executive = executive
    service.pause()
    assert service.driver.paused is True
    assert executive.is_running is False
    service.step(2)
    assert service.driver.sim_time == 12.7
    assert executive.steps == 2
    assert service.driver.scans == 4
    assert service.set_speed(3.0) == 3.0
    assert executive.speed == 3.0
    service.resume()
    assert service.driver.paused is False
    assert executive.is_running is True


def test_real_controller_executive_preserves_1x_floor_and_scales_explicitly() -> None:
    runtime = _runtime("FAST", AIBlock("AI-FAST"))
    runtime.compiled.graph.scan_ms = 50
    executive = ControllerExecutive(_Store((runtime,)), period_ms=500)
    assert executive._base_tick_ms() == 100
    assert executive.set_simulation_speed_factor(10.0) == 10.0
    assert executive._base_tick_ms() == 10
    executive.set_period_ms(1000)
    assert executive.period_ms == 1000


def test_unified_snapshot_selective_restore_and_nonfinite_limits(tmp_path) -> None:
    service, ai, _di = _service(tmp_path)
    executive = _Executive()
    service.executive = executive
    service.driver.profiles["FIELD.AI"] = {
        "store_tag": "FIELD.AI", "mode": "static", "value": 31.0,
        "low": 0.0, "high": 100.0, "period_s": 10.0, "quality": "GOOD",
    }
    path = service.save_snapshot(tmp_path / "simulation" / "snapshots" / "cold.json")
    assert service.driver.paused is False
    assert executive.is_running is True
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["type"] == "azeo.simulation_workbench_snapshot"
    assert payload["controller"][0]["blocks"][0]["parameters"]["tuning"][
        "HI_LIM"] == {"$float": "Infinity"}

    ai.config.params["SIMULATE"] = 87.0
    ai.config.params["HI_LIM"] = 70.0
    service.driver.process["level"] = 90.0
    service.driver.profiles.clear()
    service.set_speed(3.0)
    result = service.restore_snapshot(path, operating=True, tuning=False,
                                      process=True)
    assert result["process"] == 1
    assert ai.config.params["SIMULATE"] == 0.0
    assert ai.config.params["HI_LIM"] == 70.0
    assert service.driver.process["level"] == 25.0
    assert service.driver.speed == 1.0
    assert executive.speed == 1.0
    assert "FIELD.AI" in service.driver.profiles


def test_restore_resumes_entry_clocks_when_rollback_capture_fails(
        tmp_path, monkeypatch) -> None:
    service, _ai, _di = _service(tmp_path)
    executive = _Executive()
    service.executive = executive
    path = service.save_snapshot(tmp_path / "snapshot.json")

    def fail_capture(_path):
        raise OSError("snapshot disk unavailable")

    monkeypatch.setattr(
        service.driver, "save_process_simulation_snapshot", fail_capture)
    with pytest.raises(OSError, match="snapshot disk unavailable"):
        service.restore_snapshot(path)
    assert service.driver.paused is False
    assert executive.is_running is True


def test_recording_is_shared_with_operator_writer_and_replays(tmp_path) -> None:
    service, ai, _di = _service(tmp_path)
    assert service.record_tag_write("M100/AI-101/SP", 50.0) is None
    service.recording = True
    assert service.record_tag_write("M100/AI-101/SP", 50.0) is not None
    service.set_io_value("M100", ai.id, 55.0)
    journal = OperatorChangeJournal(
        tmp_path / "simulation" / "operator_changes.sqlite")
    assert journal.recording is True
    journal.append(sim_time=13, actor="operator", action="tag_write",
                   target="M100/AI-101/SP", value=61.0)
    journal.append(sim_time=14, actor="operator", action="tag_write",
                   target="M100/AI-101/HI_LIM", value=float("inf"))
    rows = journal.rows()
    assert journal.count() == 4
    assert rows[-1]["value"] == float("inf")
    ai.config.params["SIMULATE"] = 0.0
    assert service.replay_event(rows[1]) is True
    assert ai.config.params["SIMULATE"] == 55.0
    assert service.replay_event(rows[2]) is True
    assert service.store.writes[-1] == ("M100/AI-101/SP", 61.0)


def test_workbench_dialog_exposes_all_functional_tabs(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    service, _ai, _di = _service(tmp_path)
    dialog = SimulationWorkbenchDialog(
        service.store, service.driver, tmp_path)
    assert [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())] == [
        "Setup", "I/O Blocks", "Virtual I/O", "Other Modules", "Snapshots",
        "Operator Playback", "Diagnostics",
    ]
    assert dialog.scope_tree.topLevelItem(0).childCount() == 2
    assert dialog.io_table.rowCount() == 2
    assert dialog.clock_buttons["Pause"].isEnabled()
    assert not dialog.clock_buttons["Run"].isEnabled()
    dialog.pause()
    assert dialog.clock_buttons["Run"].isEnabled()
    assert dialog.clock_buttons["Step"].isEnabled()
    dialog.resume()
    dialog.close()
    app.processEvents()


@pytest.mark.parametrize("plant_only", [False, True])
def test_file_and_view_menus_keep_global_commands_without_actions_menu(tmp_path, plant_only):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    app = QApplication.instance() or QApplication([])
    service, _ai, _di = _service(tmp_path)
    service.driver.simulation_capabilities = lambda: {"available": True, "running": True}
    dialog = SimulationWorkbenchDialog(service.store, service.driver, tmp_path, plant_only=plant_only)
    opened, saved = [], []
    dialog._open_signal_simulator = lambda: opened.append(True)
    dialog.save_snapshot = lambda: saved.append(True)
    # The visible button was connected during construction; replace that slot
    # while preserving the menu's normal button-command route.
    dialog.open_signal_button.clicked.disconnect()
    dialog.open_signal_button.clicked.connect(dialog._open_signal_simulator)
    try:
        dialog.show()
        app.processEvents()
        assert "Actions" not in [a.text().replace("&", "") for a in dialog.menus.bar.actions()]
        for index in range(dialog.tabs.count()):
            dialog.tabs.setCurrentIndex(index)
            menu = dialog.menus.view
            QTest.mouseClick(dialog.menus.bar, Qt.LeftButton,
                             pos=dialog.menus.bar.actionGeometry(menu.menuAction()).center())
            app.processEvents()
            assert menu.isVisible()
            available = [action for action in menu.actions()
                         if not action.isSeparator() and action.isEnabled()]
            assert available
            signal = next(action for action in available if action.text() == "Open Signal Simulator…")
            signal.trigger()
            menu.close()
        assert len(opened) == dialog.tabs.count()
        dialog.menus.file.aboutToShow.emit()
        next(action for action in dialog.menus.file.actions()
             if action.text() == "Save Snapshot…").trigger()
        assert saved == [True]
    finally:
        dialog.menus.view.close()
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def _row_menu(dialog, table, row):
    from PySide6.QtGui import QContextMenuEvent
    table.scrollToItem(table.item(row, 0))
    QApplication.processEvents()
    point = table.visualItemRect(table.item(row, 0)).center()
    QApplication.sendEvent(table.viewport(), QContextMenuEvent(QContextMenuEvent.Mouse,
        point, table.viewport().mapToGlobal(point)))
    menu = dialog.menus.context_menu
    assert menu is not None and menu.isVisible()
    return menu


def test_focused_plant_ui_only_exposes_simulation_and_drives_disturbances(tmp_path):
    app = QApplication.instance() or QApplication([])
    service, _ai, _di = _service(tmp_path)
    driver = service.driver
    capabilities = driver.process_simulation_capabilities
    driver.process_simulation_capabilities = lambda: dict(
        capabilities(), model_diagnostics_supported=True, disturbances_supported=True, core="cpp")
    driver.process_model_catalog = lambda: {
        "units": [{"unit": "U100", "name": "Feed", "signals": 2}],
        "parameters": [{"unit": "U100", "name": "volume", "value": 100,
                        "eu": "m3", "description": "Drum capacity"}],
        "disturbances": [{"id": "TEST", "unit": "U100", "target": "P100",
                          "description": "Pump fault", "category": "Equipment",
                          "parameter": "Severity", "minimum": 0, "maximum": 100}],
    }
    live = {"id": "TEST", "active": False, "value": 0.0}
    driver.process_model_state = lambda: {
        "disturbances": [dict(live)], "units": [{"unit": "U100", "bad_signals": 0,
                                                "active_disturbances": int(live["active"])}]}
    def apply(identity, active, value):
        assert identity == "TEST"
        live["active"] = active
        if value is not None:
            live["value"] = value
    driver.set_process_disturbance = apply
    dialog = SimulationWorkbenchDialog(driver=driver, store=service.store,
                                       project_dir=tmp_path, plant_only=True)
    try:
        dialog.show()
        app.processEvents()
        assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == [
            "Plant units", "Disturbances", "Model parameters", "Virtual I/O", "Snapshots", "Diagnostics"]
        assert dialog.scope_tree.isHidden()
        assert not hasattr(dialog, "setup_commands")
        assert not hasattr(dialog, "io_table")
        assert not hasattr(dialog, "record_button")
        assert not dialog.restore_operating.isChecked()
        assert not dialog.restore_tuning.isChecked()
        assert dialog.plant_tools.units.rowCount() == 1
        tools = dialog.plant_tools
        for key, page in (("plant.signals", dialog.virtual_page),
                          ("plant.parameters", tools.parameter_page),
                          ("plant.disturbances", tools.fault_page)):
            dialog.tabs.setCurrentWidget(tools.unit_page)
            menu = _row_menu(dialog, tools.units, 0)
            next(action for action in menu.actions() if action.objectName() == key).trigger()
            menu.close()
            assert dialog.tabs.currentWidget() is page
        assert tools.fault_unit.currentData() == "U100"
        assert tools.search.text() == "U100"
        assert dialog.virtual_filter.text() == "U100"
        dialog.virtual_filter.setText("FIELD.DI")
        assert dialog.virtual_table.isRowHidden(0) and not dialog.virtual_table.isRowHidden(1)
        dialog.tabs.setCurrentWidget(tools.fault_page)
        tools.faults.selectRow(0)
        tools.active.setChecked(True)
        tools.value.setText("25")
        menu = _row_menu(dialog, tools.faults, 0)
        assert tools.value.text() == "25" and tools.active.isChecked()
        next(action for action in menu.actions()
             if action.text() == "Apply disturbance").trigger()
        menu.close()
        assert live == {"id": "TEST", "active": True, "value": 25}
        tools.value.setText("40")
        dialog.refresh_live()
        assert tools.value.text() == "40", "Polling must preserve the user's pending edit"
        menu = _row_menu(dialog, tools.faults, 0)
        next(action for action in menu.actions()
             if action.text() == "Clear selected").trigger()
        menu.close()
        assert not live["active"]
        dialog.pause()
        assert driver.paused
        cell = dialog.virtual_table.item(0, 0)
        dialog.virtual_table.setColumnWidth(0, 247)
        dialog.refresh_tables()
        app.processEvents()
        assert dialog.virtual_table.item(0, 0) is cell, "Refresh must reuse the live I/O cells"
        assert dialog.virtual_table.columnWidth(0) == 247, "Live refresh must preserve column widths"
        dialog.step()
        assert driver.sim_time == 12.6
        dialog.resume()
        assert not driver.paused
        def unexpected_journal_read():
            raise AssertionError("Focused status refresh must not open the playback database")
        dialog.service.journal._connect = unexpected_journal_read
        dialog.refresh_live()
        assert not any(name.startswith("azeoplant.ui") for name in sys.modules)
    finally:
        dialog.close()
        app.processEvents()
