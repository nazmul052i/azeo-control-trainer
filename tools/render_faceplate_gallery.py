#!/usr/bin/env python3
"""Render every constructible registered faceplate for visual QA."""
from __future__ import annotations

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
    output = ROOT / "tmp" / "faceplates"
    output.mkdir(parents=True, exist_ok=True)
    graph = StrategyGraph(name="FACEPLATE_QA")
    blocks = BlockRegistry()
    constructible = []
    for (_block_type, role, variant), faceplate in sorted(
            registry.all_classes().items()):
        if role != "faceplate":
            continue
        block_type = "PID" if faceplate.__name__ == "CascadePairFaceplate" \
            else "AI" if faceplate.__name__ == "PassBalanceFaceplate" \
            else faceplate.block_type
        params = {}
        try:
            for parameter in faceplate.PARAMS:
                name = "QA_%s_%s" % (faceplate.__name__, parameter)
                block = blocks.create(block_type, name)
                apply_config = getattr(block, "_apply_config", None)
                if apply_config is not None:
                    apply_config()
                graph.add_block(block)
                params[parameter] = "%s/%s" % (graph.name, name)
        except Exception as error:  # a missing trainer block is reported
            print("skip %s: %s" % (faceplate.__name__, error))
            continue
        constructible.append((faceplate, params))

    engine = BindingEngine(LiveGraphSource(lambda: {graph.name: graph}))
    shots = []
    for faceplate, params in constructible:
        widget = PvmFaceplateWidget(
            faceplate, params, engine,
            theme="azeo_live")
        # The gallery documents each class's complete anatomy. Runtime hosts
        # still reduce this set to actions they can actually service.
        widget.set_available_actions(widget.faceplate_profile.actions)
        widget.setAttribute(Qt.WA_DontShowOnScreen, True)
        widget.show()
        app.processEvents()
        shot = widget.grab().toImage()
        path = output / (faceplate.__name__ + ".png")
        shot.save(str(path))
        shots.append((faceplate.__name__, shot))
        widget.close()

    if not shots:
        return 1
    cell_w = max(image.width() for _name, image in shots) + 24
    cell_h = max(image.height() for _name, image in shots) + 48
    columns = 3
    rows = (len(shots) + columns - 1) // columns
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
    print("rendered %d faceplates to %s" % (len(shots), gallery_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
