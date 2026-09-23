from __future__ import annotations

from PySide6.QtWidgets import QGraphicsItem

from azeo_control_trainer.core.strategy.model.terminal import (
    DataType,
    Quality,
    Terminal,
    TerminalDirection,
)
from azeo_control_trainer.core.strategy.model.wire import Wire
from azeo_control_trainer.azeo_control_designer.items.terminal_item import (
    TerminalItem,
)
from azeo_control_trainer.azeo_control_designer.items.wire_item import WireItem


class _BlockItem(QGraphicsItem):
    def __init__(self, live=True):
        super().__init__()
        self._live_mode = live
        self.block = type("Block", (), {"instance_name": "B"})()

    def boundingRect(self):
        from PySide6.QtCore import QRectF
        return QRectF(0, 0, 20, 20)

    def paint(self, painter, option, widget=None):
        return None


def _terminal(name, direction):
    return Terminal(name, direction, DataType.FLOAT)


def test_terminal_live_colour_exposes_quality_and_force(qapp):
    parent = _BlockItem(live=True)
    terminal = _terminal("IN", TerminalDirection.INPUT)
    item = TerminalItem(terminal, parent)

    ordinary = item._display_color().name()
    terminal.status = Quality.BAD
    assert item._display_color().name() == "#c62828"
    terminal.forced = True
    assert item._display_color().name() == "#9c27b0"
    assert ordinary not in {"#c62828", "#9c27b0"}


def test_wire_activity_and_quality_drive_live_appearance(qapp):
    src_parent = _BlockItem(live=True)
    dst_parent = _BlockItem(live=True)
    src = TerminalItem(_terminal("OUT", TerminalDirection.OUTPUT), src_parent)
    dst = TerminalItem(_terminal("IN", TerminalDirection.INPUT), dst_parent)
    wire = Wire("src", "OUT", "dst", "IN")
    item = WireItem(wire, src, dst)
    item.set_show_value(True)

    src.terminal.value = 0.0
    item.update_live_value()
    assert not item._live_active
    src.terminal.value = 1.25
    src.terminal.status = Quality.UNCERTAIN
    item.update_live_value()
    assert item._live_active
    assert item._live_quality is Quality.UNCERTAIN
    assert item._live_color().name() == "#d88700"
