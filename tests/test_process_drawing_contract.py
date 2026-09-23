"""Smart process drawing contracts shared by Studio and Operator."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PySide6.QtWidgets import QApplication  # noqa: E402

from azeo_control_trainer.core.hmi.pvms.rendering.items import (  # noqa: E402
    PipeItem,
    StaticItem,
)
from azeo_control_trainer.core.hmi.pvms.rendering.renderer import (  # noqa: E402
    DisplayRenderer,
    pvm_from_dict,
)
from azeo_control_trainer.core.hmi.pvms.symbols import (  # noqa: E402
    connection_ports,
)
from azeo_control_trainer.core.hmi.theme.palette import (  # noqa: E402
    palette_for,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


PORTS = [
    {"name": "inlet", "x": 0.0, "y": 0.5},
    {"name": "outlet", "x": 1.0, "y": 0.5},
    {"name": "top", "x": 0.5, "y": 0.0},
    {"name": "bottom", "x": 0.5, "y": 1.0},
]


def _symbol(ident: str, x: float, y: float) -> StaticItem:
    return StaticItem({
        "kind": "symbol", "id": ident, "symbol": "tank",
        "x": x, "y": y, "w": 100.0, "h": 100.0,
        "ports": PORTS,
    }, palette_for("azeo_live"))


def test_named_pipe_ports_follow_move_and_resize() -> None:
    _app()
    source = _symbol("source", 10.0, 20.0)
    target = _symbol("target", 300.0, 100.0)
    pipe = PipeItem({
        "kind": "pipe", "a": "source", "b": "target",
        "a_side": "outlet", "b_side": "inlet", "auto": False,
        "crossover": "jump",
    }, palette_for("azeo_live"))

    pipe.route(source, target)
    assert pipe._points[0] == source.anchor("outlet")
    assert pipe._points[-1] == target.anchor("inlet")

    source.setPos(40.0, 60.0)
    source.setRect(0.0, 0.0, 200.0, 120.0)
    pipe.route(source, target)
    assert pipe._points[0] == source.anchor("outlet")
    outlet_x, outlet_y = connection_ports("tank")["e"]
    assert abs(pipe._points[0].x() - (40.0 + 200.0 * outlet_x)) < 0.001
    assert abs(pipe._points[0].y() - (60.0 + 120.0 * outlet_y)) < 0.001
    assert pipe.data["a_side"] == "outlet"
    assert pipe.data["b_side"] == "inlet"


class _Engine:
    def bind(self, _path: str, _params=None):
        return SimpleNamespace(result=SimpleNamespace(
            value=50.0, quality=SimpleNamespace(name="GOOD")))

    def write(self, *_args):
        return False

    def can_write(self, *_args):
        return SimpleNamespace(success=False, error="read only")


def test_live_vessel_pvm_exposes_the_same_semantic_pipe_ports() -> None:
    _app()
    palette = palette_for("azeo_live")
    renderer = DisplayRenderer(_Engine(), palette)
    source = _symbol("source", 10.0, 20.0)
    vessel = renderer.build_pvm(pvm_from_dict({
        "id": "vessel", "class": "PID/dynamo_inline",
        "variant": "vessel", "params": {"path": "LIC-1001/LIC-1001"},
        "choices": {"equipment_symbol": "tank"},
        "x": 300.0, "y": 100.0, "w": 160.0, "h": 220.0,
    }))
    assert {"inlet", "outlet", "top", "bottom"} \
        <= set(vessel.anchor_sides())

    pipe = PipeItem({
        "kind": "pipe", "a": "source", "b": "vessel",
        "a_side": "outlet", "b_side": "inlet", "auto": False,
    }, palette)
    pipe.route(source, vessel)
    assert pipe._points[0] == source.anchor("outlet")
    assert pipe._points[-1] == vessel.anchor("inlet")
