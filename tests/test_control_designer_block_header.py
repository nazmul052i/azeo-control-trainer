"""Regression coverage for Control Designer function-block header layout."""
from __future__ import annotations

import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from azeo_control_trainer.core.strategy.blocks.io_blocks import DIBlock  # noqa: E402
from azeo_control_trainer.azeo_control_designer.items.block_item import (  # noqa: E402
    BLOCK_HEADER_HEIGHT,
    BLOCK_PIN_MARGIN,
    BlockItem,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_instance_name_stays_in_header_and_clear_of_first_pin_row() -> None:
    """A long DI name must not paint over SIMULATE_IN_D."""
    _app()
    item = BlockItem(DIBlock("XS-FD701-RUN"))

    title, block_type, status = item._header_text_layout()
    first_pin_band = QRectF(
        0,
        BLOCK_HEADER_HEIGHT + BLOCK_PIN_MARGIN - 5,
        item._width,
        10,
    )

    assert title.top() >= 0
    assert title.bottom() <= BLOCK_HEADER_HEIGHT
    assert not title.intersects(first_pin_band)
    assert title.right() < block_type.left()
    assert block_type.top() < BLOCK_HEADER_HEIGHT / 2
    assert block_type.center().x() > item._width / 2
    assert block_type.right() < status.x()
    assert item._width * 0.35 < title.center().x() < item._width * 0.65
