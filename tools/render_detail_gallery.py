#!/usr/bin/env python3
"""Render every registered module detail display for visual QA."""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import azeo_control_trainer.core.hmi.pvms  # noqa: E402,F401
import azeo_control_trainer.core.strategy.blocks  # noqa: E402,F401
from azeo_control_trainer.core.hmi.binding import (  # noqa: E402
    BindingEngine,
    LiveGraphSource,
)
from azeo_control_trainer.core.hmi.pvms.base import registry  # noqa: E402
from azeo_control_trainer.core.hmi.pvms.render import (  # noqa: E402
    PvmFaceplateWidget,
)
from azeo_control_trainer.core.hmi.theme.fonts import load_fonts  # noqa: E402
from azeo_control_trainer.core.strategy.model.block_registry import (  # noqa: E402
    BlockRegistry,
)
from azeo_control_trainer.core.strategy.model.strategy_graph import (  # noqa: E402
    StrategyGraph,
)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    load_fonts()
    output = ROOT / "tmp" / "details"
    output.mkdir(parents=True, exist_ok=True)
    graph = StrategyGraph(name="DETAIL_QA")
    blocks = BlockRegistry()
    details = []
    for (_block_type, role, _variant), detail in sorted(
            registry.all_classes().items()):
        if role != "detail":
            continue
        try:
            block = blocks.create(detail.block_type, detail.__name__)
            apply_config = getattr(block, "_apply_config", None)
            if apply_config is not None:
                apply_config()
            graph.add_block(block)
        except Exception as error:
            print("skip %s: %s" % (detail.__name__, error))
            continue
        details.append((detail, {"path": f"{graph.name}/{detail.__name__}"}))

    engine = BindingEngine(LiveGraphSource(lambda: {graph.name: graph}))
    shots = []
    for detail, params in details:
        widget = PvmFaceplateWidget(detail, params, engine,
                                    theme="azeo_live")
        widget.setAttribute(Qt.WA_DontShowOnScreen, True)
        widget.show()
        app.processEvents()
        shot = widget.grab().toImage()
        shot.save(str(output / (detail.__name__ + ".png")))
        shots.append((detail.__name__, shot))
        widget.close()

    if not shots:
        return 1
    cell_w = max(image.width() for _name, image in shots) + 24
    cell_h = max(image.height() for _name, image in shots) + 48
    columns = 2
    rows = math.ceil(len(shots) / columns)
    gallery = QImage(cell_w * columns, cell_h * rows,
                     QImage.Format_ARGB32_Premultiplied)
    gallery.fill(QColor("#F4F4F4"))
    painter = QPainter(gallery)
    for index, (name, image) in enumerate(shots):
        x = index % columns * cell_w
        y = index // columns * cell_h
        painter.setPen(QColor("#202020"))
        painter.drawText(x + 8, y + 18, name)
        painter.drawImage(x + 8, y + 26, image)
    painter.end()
    gallery_path = output / "gallery.png"
    gallery.save(str(gallery_path))
    print("rendered %d detail displays to %s" % (
        len(shots), gallery_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
