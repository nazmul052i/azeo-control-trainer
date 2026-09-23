"""The simulator must not ship older nozzles than the engineering library."""

import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "AzeoPlantSimulator"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tools.sync_simulator_symbol_ports import (  # noqa: E402
    SIMULATOR,
    matching_symbols,
    synchronized_text,
)


def test_simulator_ports_match_identical_shared_geometry_without_changing_its_theme():
    pairs = matching_symbols()
    assert len(pairs) == 34
    for target, source in pairs:
        assert target.read_text(encoding="utf-8") == synchronized_text(target, source), target


@pytest.mark.parametrize("side", ["T", "B"])
@pytest.mark.parametrize(
    "rotation,mirror,normal",
    [
        (0, False, "R"),
        (0, True, "L"),
        (90, False, "B"),
        (270, True, "B"),
    ],
)
def test_splitter_branches_leave_the_horizontal_nozzles(side, rotation, mirror, normal):
    from PySide6.QtGui import QTransform
    from PySide6.QtWidgets import QApplication
    from azeoplant.ui.hmi import Connector, ImageItem

    app = QApplication.instance() or QApplication([])
    item = ImageItem(0, 0, str(SIMULATOR / "fittings/pid_splitter.svg"))
    item.setRotation(rotation)
    if mirror:
        item.setTransform(QTransform().scale(-1, 1))
    assert Connector._eff_side(side, item) == normal
    target = ImageItem(200, 160, str(SIMULATOR / "fittings/pid_splitter.svg"))
    connection = Connector(item, item.ports[side], target, target.ports["L"], a_side=side)
    route = connection._route()
    departure = next(point - route[0] for point in route[1:] if point != route[0])
    if normal in "LR":
        assert departure.y() == 0
    else:
        assert departure.x() == 0
    # A deliberately chosen free edge is still the engineer's own endpoint.
    if not mirror and not rotation:
        assert Connector._eff_side(side, item, (0.5, 0.0)) == side
    app.processEvents()
