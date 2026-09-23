"""Qualify live mode feedback separately from a project's initial/normal modes."""
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.core.hmi.binding import BindingEngine, LiveGraphSource
from azeo_control_trainer.core.hmi.pvms.base import registry
from azeo_control_trainer.core.hmi.pvms.render import PvmFaceplateWidget
from azeo_control_trainer.core.strategy.blocks.pid_block import PIDBlock
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
from azeo_control_trainer.core.strategy.model.terminal import Quality


@pytest.fixture
def loop():
    app = QApplication.instance() or QApplication([])
    graph = StrategyGraph("LOOP")
    block = PIDBlock("PID1")
    block.config.params.update(mode="MAN", normal_mode="CAS", track_enable=True)
    block._apply_config()
    block.inputs["IN"].value = 50.0
    block.inputs["IN"].status = Quality.GOOD
    block.inputs["SP"].value = 50.0
    graph.add_block(block)
    block.execute(0.1)
    engine = BindingEngine(LiveGraphSource(lambda: {"LOOP": graph}))
    widget = PvmFaceplateWidget(registry.get("PID", "faceplate"), {"path": "LOOP/PID1"}, engine)
    widget.set_write_handler(engine.write, engine.can_write)
    widget.refresh()
    try:
        yield block, widget
    finally:
        widget.close()
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()


def test_mode_menu_commands_reach_the_painted_faceplate_state(loop):
    block, widget = loop
    assert widget.visual.state.actual_mode == "MAN"
    for mode in ("AUTO", "MAN", "AUTO"):
        widget.visual._activate("mode_actual")
        action = next(action for action in widget._mode_menu.actions() if action.text() == mode)
        assert action.isEnabled()
        action.trigger()
        assert block.mode_target == mode
        block.execute(0.1)
        widget.refresh()
        assert block.mode_actual == mode
        assert widget.visual.state.actual_mode == mode
        assert widget.visual.state.target_mode == mode
    # Normal is an engineering reference, never a replacement for live mode.
    assert widget.visual.normal_mode == "CAS"
    assert block.config.params["mode"] == "MAN"


def test_tracking_displays_actual_override_and_requested_auto_separately(loop):
    block, widget = loop
    assert widget.write_bound("mode.command", "AUTO")
    block.inputs["TRK_IN_D"].value = True
    block.inputs["TRK_VAL"].value = 25.0
    block.execute(0.1)
    widget.refresh()
    assert block.mode_actual == "LO"
    assert widget.visual.state.actual_mode == "LO"
    assert widget.visual.state.target_mode == "AUTO"
    block.inputs["TRK_IN_D"].value = False
    block.execute(0.1)
    widget.refresh()
    assert widget.visual.state.actual_mode == "AUTO"
    assert widget.visual.state.target_mode == "AUTO"


@pytest.mark.parametrize("target", [52.0, 0.0])
def test_operator_sp_continues_ramping_across_scans(loop, target):
    block, widget = loop
    block.config.params.update(sp_rate_up=1.0, sp_rate_dn=1.0)
    block._apply_config()
    assert widget.write_bound("mode.command", "AUTO")
    block.execute(.1)
    assert widget.write_bound("sp.value", target)
    observed = []
    for _ in range(int(abs(target - 50) * 10) + 5):
        block.execute(.1)
        observed.append(block.outputs["SP_WRK"].value)
    assert observed[0] == pytest.approx(50.1 if target > 50 else 49.9)
    assert observed == sorted(observed, reverse=target < 50)
    assert observed[-1] == pytest.approx(target)
    widget.refresh()
    assert widget.visual.state.sp == pytest.approx(target)


def test_operator_sp_cannot_override_a_wired_setpoint(loop):
    block, widget = loop
    block.inputs["SP"].connected = True
    assert not widget.write_bound("sp.value", 75.0)
    assert block.inputs["SP"].value == 50.0


def test_pinned_station_faceplates_follow_independent_live_modes_and_setpoints(tmp_path):
    from azeo_control_trainer.azeo_operator_station.console import LiveStation
    from azeo_control_trainer.azeo_operator_station.deployment import PvmDeployment
    from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore, PvmDisplay

    app = QApplication.instance() or QApplication([])
    graph = StrategyGraph("LOOP")
    blocks = []
    for index in range(4):
        block = PIDBlock(f"PID{index}")
        block.config.params.update(mode="MAN", normal_mode="AUTO", pv_scale_hi=500, sp_hi=500)
        block._apply_config()
        block.inputs["IN"].value = 100.0
        block.inputs["IN"].status = Quality.GOOD
        block.inputs["SP"].value = 50.0
        block.inputs["CAS_IN"].value = 120.0
        block.inputs["CAS_IN"].status = Quality.GOOD
        graph.add_block(block)
        block.execute(.1)
        blocks.append(block)
    store = DisplayStore(tmp_path / "pvm")
    document = PvmDisplay(name="Plant")
    store.save_draft(document)
    store.publish(document, by="engineer")
    station = LiveStation(PvmDeployment(store), lambda: {"LOOP": graph})
    try:
        station.show_display("Plant")
        station.show()
        for index in range(4):
            station.open_faceplate(registry.get("PID", "faceplate")().place(
                f"loop{index}", path=f"LOOP/PID{index}"))
            station.faceplates[-1][1].toggle_pin()
            station.faceplates[-1][1].show()
        widgets = [window for _, window in station.faceplates]
        assert len(widgets) == 4 and all(window.isWindow() and window.pinned for window in widgets)
        assert all(window._timer.isActive() for window in widgets)
        modes = ("AUTO", "CAS", "MAN", "OOS")
        for block, widget, mode in zip(blocks, widgets, modes):
            arrow = widget.visual.actual_mode_arrow_geometry().center()
            QTest.mouseClick(widget.visual, Qt.LeftButton, pos=QPoint(round(arrow.x()), round(arrow.y())))
            action = next(action for action in widget._mode_menu.actions() if action.text() == mode)
            assert action.isEnabled()
            action.trigger()
            block.execute(.1)
        # Use the running faceplate timers: a direct refresh would miss a
        # station integration bug that leaves pinned windows stuck at MAN.
        QTest.qWait(600)
        assert [block.mode_actual for block in blocks] == list(modes)
        assert [widget.visual.state.actual_mode for widget in widgets] == list(modes)
        assert [widget.visual.state.target_mode for widget in widgets] == list(modes)
        assert widgets[0].write_bound("sp.value", 175.0)
        assert blocks[0].pid_core_block.SP == 175.0
        blocks[0].execute(.1)
        assert blocks[0].pid_core_block.SP == 175.0
        assert blocks[0].outputs["SP"].value == 175.0
        QTest.qWait(600)
        binding = widgets[0].bound["sp.value"]
        assert binding.result.value == 175.0, (
            binding.path, widgets[0]._timer.isActive(), widgets[0].isVisible(),
            widgets[0].engine._source.read(binding.path).value)
        assert widgets[0].visual.state.sp == 175.0
        assert widgets[0].visual.state.pv == 100.0
        assert [widget.visual.state.actual_mode for widget in widgets] == list(modes)
        # A pointer gesture must use the same scale as the painted white SP
        # mark, and its accepted value must survive subsequent scans too.
        QTest.mouseClick(widgets[0].visual, Qt.LeftButton, pos=QPoint(110, 217))
        for _ in range(3):
            blocks[0].execute(.1)
        QTest.qWait(600)
        assert widgets[0].visual.state.sp == pytest.approx(250.0)
        assert blocks[0].inputs["SP"].value == pytest.approx(250.0)
        assert widgets[0].visual.state.pv == 100.0
    finally:
        station.close()
        station.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
