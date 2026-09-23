#!/usr/bin/env python3
"""Export every P&ID scene as a vector SVG drawing.

The application draws its P&IDs as vector graphics scenes, so the same source
can produce publication-quality SVG files for documentation, training handouts
or the process description. Values on the exported sheets are live: the plant
is restored from the lined-up snapshot and stepped briefly first, so levels,
flows and status lamps show a running plant rather than zeros.

    python tools/export_pids.py [--out docs/Process_images] [--fresh]

``--fresh`` skips the snapshot and exports the built-in initial condition.
No window is shown; only files are written.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Export P&ID scenes as SVG")
    parser.add_argument("--out", default=str(ROOT / "docs" / "Process_images"),
                        help="output directory for the SVG files")
    parser.add_argument("--fresh", action="store_true",
                        help="export the built-in initial condition instead of "
                             "the lined-up snapshot")
    args = parser.parse_args(argv)

    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QPainter
    from PySide6.QtSvg import QSvgGenerator
    from PySide6.QtWidgets import QApplication

    from azeoplant.core.engine import SimulationEngine
    from azeoplant.core.tags import TagDatabase
    from azeoplant.models.flowsheet import Flowsheet

    db = TagDatabase()
    flowsheet = Flowsheet(db, dt=0.1)
    engine = SimulationEngine(db, flowsheet, dt=0.1)

    snapshot = ROOT / "snapshots" / "lined_up.json"
    if not args.fresh and snapshot.exists():
        engine.load_snapshot(snapshot)
    for _ in range(100):
        engine._execute_step()

    app = QApplication.instance() or QApplication([])
    from azeoplant.ui.pid_scenes import SCENE_BUILDERS

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    snap = db.snapshot()

    for code, builder in SCENE_BUILDERS.items():
        scene = builder()
        scene.refresh(snap)
        rect = scene.sceneRect()
        path = out / f"AzeoPlant_PID_{code.lower()}.svg"

        generator = QSvgGenerator()
        generator.setFileName(str(path))
        generator.setSize(rect.size().toSize())
        generator.setViewBox(QRectF(0, 0, rect.width(), rect.height()))
        generator.setTitle(scene.title)
        generator.setDescription("AzeoPlant open loop process simulator")

        painter = QPainter(generator)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(QRectF(0, 0, rect.width(), rect.height()),
                         scene.backgroundBrush())
        scene.render(painter, QRectF(0, 0, rect.width(), rect.height()), rect)
        painter.end()
        print(f"  {path.name}  ({rect.width():.0f} x {rect.height():.0f})")

    print(f"Exported {len(SCENE_BUILDERS)} drawings to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
