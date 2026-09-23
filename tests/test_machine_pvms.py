"""Machine PVMs use real bindings, checked commands and the shared faceplates."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import azeo_control_trainer.core.strategy.blocks  # noqa: F401
from azeo_control_trainer.core.hmi.binding import BindingEngine, LiveGraphSource
from azeo_control_trainer.core.hmi.binding.result import BindingResult
from azeo_control_trainer.core.hmi.pvms.base import registry
from azeo_control_trainer.core.hmi.pvms.machines import MACHINE_VARIANTS
from azeo_control_trainer.core.hmi.pvms.render import PvmFaceplateWidget
from azeo_control_trainer.core.hmi.pvms.symbols import connection_ports, renderer
from azeo_control_trainer.core.strategy.model.block_registry import BlockRegistry
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
from azeo_control_trainer.core.strategy.model.terminal import Quality


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def machine_graphs():
    pid = BlockRegistry().create("PID", "PID1")
    pid.config.params.update(mode="AUTO", normal_mode="AUTO")
    pid._apply_config()
    pid.inputs["IN"].value = 1500.0
    pid.inputs["IN"].status = Quality.GOOD
    pid.inputs["SP"].value = 1500.0
    dc = BlockRegistry().create("DEVCTL", "DC1")
    dc._apply_config()
    for key in ("PERMISSIVE_D", "INTERLOCK"):
        dc.inputs[key].value = True
        dc.inputs[key].status = Quality.GOOD
    graphs = {"SPEED": StrategyGraph("SPEED"), "DRIVE": StrategyGraph("DRIVE")}
    graphs["SPEED"].add_block(pid)
    graphs["DRIVE"].add_block(dc)
    pid.execute(0.1)
    dc.execute(0.1)
    return graphs


@pytest.mark.parametrize("variant", MACHINE_VARIANTS)
def test_faceplate_binds_both_blocks_routes_writes_and_releases_subscriptions(app, variant):
    graphs = machine_graphs()
    engine = BindingEngine(LiveGraphSource(lambda: graphs))
    widget = PvmFaceplateWidget(registry.get("PID", "faceplate", variant),
                                {"device": "DRIVE/DC1", "path": "SPEED/PID1"}, engine, live=False)
    try:
        assert widget.bound["sp.value"].path == "SPEED/PID1/SP"
        assert widget.bound["device.cmd.start"].path == "DRIVE/DC1/START_CMD"
        assert "detail" in widget.serviceable_actions()
        assert widget.faceplate_block() == ("SPEED", "PID1")
        writes = []
        widget.set_write_handler(lambda path, value: writes.append((path, value)) or True,
                                 lambda path: SimpleNamespace(success=not path.endswith("STOP_CMD"), error="Denied by role"))
        widget.show()
        app.processEvents()
        device = widget.visual.device
        QTest.mouseClick(device, Qt.LeftButton, pos=device.state_button_geometry("cmd.start").center().toPoint())
        assert writes == [("DRIVE/DC1/START_CMD", True)]
        QTest.mouseClick(device, Qt.LeftButton, pos=device.state_button_geometry("cmd.stop").center().toPoint())
        assert len(writes) == 1
        assert not widget.write_bound("device.cmd.stop", True)
        assert widget.visual.message.text() == "Denied by role"
        assert not widget.write_bound("device.interlock", True)
        widget.set_write_handler(engine.write, engine.can_write)
        assert widget.write_bound("sp.value", 1600.0)
        assert next(iter(graphs["SPEED"].blocks.values())).inputs["SP"].value == 1600.0
        widget.visual.loop._activate("mode_actual")
        action = next(action for action in widget._mode_menu.actions() if action.text() == "MAN")
        action.trigger()
        pid = next(iter(graphs["SPEED"].blocks.values()))
        pid.execute(0.1)
        widget.refresh()
        assert widget.visual.loop.state.actual_mode == "MAN"
        assert widget.visual.mode == "MAN"
        refs = tuple(widget.bound.values())
        for theme in ("silver", "dark", "hpgray", "azeo_live"):
            widget.apply_theme(theme)
            assert tuple(widget.bound.values()) == refs
            assert widget.visual.device.parentWidget() is widget.visual
    finally:
        widget.close()
        assert not widget.bound
        assert engine.monitored_count == 0
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_machine_status_does_not_show_old_healthy_values_when_quality_is_lost(app):
    from azeo_control_trainer.core.hmi.pvms.machine_surface import MachineFaceplateSurface
    from azeo_control_trainer.core.hmi.theme.tokens import THEMES
    surface = MachineFaceplateSurface(THEMES["dark"])
    for quality in (Quality.GOOD, Quality.BAD, Quality.UNCERTAIN):
        bound = {key: SimpleNamespace(result=BindingResult(value=value, quality=quality))
                 for key, value in (("device.state.value", 2), ("device.field.value", True),
                                    ("device.permit", True), ("device.interlock", True))}
        surface.refresh(bound)
        assert surface.device.running is (True if quality == Quality.GOOD else None)
        assert surface.device.no_permit is (False if quality == Quality.GOOD else None)
        assert surface.device.interlocked is (False if quality == Quality.GOOD else None)
    surface.close()


@pytest.mark.parametrize("name", ("turbine_tapered", "compressor_tapered"))
def test_tapered_symbols_have_valid_svg_and_visible_ports(app, name):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter
    from azeo_control_trainer.core.hmi.theme.tokens import THEMES
    from azeo_control_trainer.core.hmi.theme.roles import Role
    svg = renderer(name)
    assert svg is not None and svg.isValid()
    assert len(connection_ports(name)) == 4
    for theme in ("silver", "dark", "hpgray"):
        palette = THEMES[theme]
        svg = renderer(name, line=palette[Role.EQUIPMENT], fill=palette[Role.EQUIPMENT_FILL])
        image = QImage(100, 62, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        svg.render(painter, QRectF(0, 0, 100, 62))
        painter.end()
        assert image.pixelColor(50, 31).name() == palette[Role.EQUIPMENT_FILL].lower()


def test_studio_places_configures_and_opens_machine_faceplate(app, tmp_path):
    from azeo_control_trainer.azeo_graphics_designer.window import HmiStudioWindow
    graphs = machine_graphs()
    window = HmiStudioWindow(lambda: graphs, tmp_path, area_name="Machine test")
    try:
        studio = window._open_created_display("Machine test")
        pvm = studio.place_block("SPEED/PID1", "PID", 100, 100, role=("dynamo_inline", "turbine_speed"))
        item = next(item for item in studio._items() if item.pvm.id == pvm.id)
        studio.pane.show_pvm(item)
        from azeo_control_trainer.azeo_graphics_designer.studio.panes import _PathField
        field = next(field for field in studio.pane.findChildren(_PathField) if field.parameter == "device")
        field.edit.setText("DRIVE/DC1")
        QTest.keyClick(field.edit, Qt.Key_Return)
        item = next(item for item in studio._items() if item.pvm.id == pvm.id)
        assert item.pvm.params == {"path": "SPEED/PID1", "device": "DRIVE/DC1"}
        with pytest.raises(ValueError, match="DEVCTL"):
            studio.set_pvm_path(item, "SPEED/PID1", "device")
        studio.open_faceplate(item.pvm)
        assert any(fp.pvm.variant == "turbine_speed" for fp in studio._faceplates.values())
        studio.unsaved = False
    finally:
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_machine_command_cannot_override_a_wired_input_or_interlock(app):
    graphs = machine_graphs()
    device = next(iter(graphs["DRIVE"].blocks.values()))
    engine = BindingEngine(LiveGraphSource(lambda: graphs))
    widget = PvmFaceplateWidget(registry.get("PID", "faceplate", "vfd"),
                                {"path": "SPEED/PID1", "device": "DRIVE/DC1"}, engine, live=False)
    widget.set_write_handler(engine.write, engine.can_write)
    try:
        device.inputs["START_CMD"].connected = True
        widget.refresh()
        assert "cmd.start" not in widget.visual.device.writable_keys
        assert not widget.write_bound("device.cmd.start", True)
        assert not device.inputs["START_CMD"].value
        device.inputs["START_CMD"].connected = False
        device.inputs["INTERLOCK"].value = False
        assert widget.write_bound("device.cmd.start", True)
        device.execute(0.1)
        assert not device.outputs["DO_START"].value
    finally:
        widget.close()


@pytest.mark.parametrize("mirror", [False, True])
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_tapered_symbol_ports_follow_mirror_and_rotation(app, mirror, rotation):
    from azeo_control_trainer.core.hmi.pvms.rendering.items import StaticItem
    from azeo_control_trainer.core.hmi.pvms.rendering.pipe_scene import port_anchor
    from azeo_control_trainer.core.hmi.theme.tokens import THEMES
    item = StaticItem(dict(kind="symbol", symbol="turbine_tapered", mx=mirror,
                           rot=rotation, x=300, y=200, w=100, h=62), THEMES["dark"])
    anchor = port_anchor(item, "e")
    directions = ("e", "s", "w", "n")
    assert anchor.normal == directions[((2 if mirror else 0) + rotation // 90) % 4]
    assert anchor.point.x == pytest.approx(item.anchor("e").x())
    assert anchor.point.y == pytest.approx(item.anchor("e").y())
