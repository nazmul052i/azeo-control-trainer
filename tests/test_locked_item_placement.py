"""Locked artwork must load at its saved location before movement is blocked."""
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.core.hmi.pvms.base import Pvm
from azeo_control_trainer.core.hmi.pvms.rendering.items import PvmItem, StaticItem
from azeo_control_trainer.core.hmi.theme.tokens import THEMES


@pytest.mark.parametrize("family", ["drawing", "pvm"])
def test_locked_item_restores_authored_position_and_still_blocks_movement(family):
    app = QApplication.instance() or QApplication([])
    palette = THEMES["silver"]
    if family == "drawing":
        item = StaticItem(dict(kind="rect", x=1060, y=214, w=440, h=566,
                               locked=True, fill_role="SURFACE_PANEL"), palette)
    else:
        item = PvmItem(Pvm(id="locked", pvm_class="AI/dynamo_compact", block_type="AI",
                           role="dynamo_compact", params={}, x=1060, y=214,
                           locked=True), None, None, palette)
    assert item.pos() == QPointF(1060, 214)
    item.setPos(25, 50)
    assert item.pos() == QPointF(1060, 214)
    if family == "drawing":
        assert (item.data["x"], item.data["y"]) == (1060, 214)
    else:
        assert (item.pvm.x, item.pvm.y) == (1060, 214)
    app.processEvents()
