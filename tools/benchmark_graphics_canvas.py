"""Measure the real drag controller at 500/1000 retained objects, best of three.

--baseline loads only PointerDrag from HEAD to make its contribution comparable.
--regression runs the snap regression against that pre-change implementation.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"


def main():
    if "--baseline" in sys.argv or "--regression" in sys.argv:
        import azeo_control_trainer.azeo_graphics_designer.studio.pointer_drag as module
        source = subprocess.check_output(["git", "show", "HEAD:src/azeo_control_trainer/azeo_graphics_designer/studio/pointer_drag.py"], cwd=ROOT, text=True, encoding="utf-8")
        namespace = types.ModuleType(module.__name__)
        namespace.__package__ = module.__package__
        exec(compile(source, "baseline_pointer_drag.py", "exec"), namespace.__dict__)
        module.PointerDrag = namespace.PointerDrag
    if "--regression" in sys.argv:
        import pytest
        return pytest.main([str(ROOT / "tests/test_graphics_productivity.py"), "-q", "-k", "snap_holds or repeated_snapped"])
    from PySide6.QtCore import QPointF, QSettings, Qt
    from PySide6.QtWidgets import QApplication
    from azeo_control_trainer.azeo_graphics_designer.window import HmiStudioWindow
    app = QApplication.instance() or QApplication([])
    results = []
    with tempfile.TemporaryDirectory(prefix="azeo-canvas-benchmark-") as temp:
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, temp)
        window = HmiStudioWindow(lambda: {}, Path(temp) / "displays")
        studio = window.current()
        studio.enter_edit()
        for count in (500, 1000):
            boxes = count * 2 // 3
            if boxes % 2:
                boxes += 1
            items = []
            for index in range(boxes):
                items.append(dict(id=f"b{index}", kind="rect", x=(index % 20) * 140,
                                  y=(index // 20) * 100, w=60, h=40))
            for index in range(count - boxes):
                items.append(dict(id=f"p{index}", kind="pipe", a=f"b{index * 2}", b=f"b{index * 2 + 1}",
                                  a_side="e", b_side="w", route_mode="manual", route_points=[]))
            studio._load_document(dict(display=studio.display.name, items=items, width=3000, height=3500))
            studio.set_zoom(100)
            studio.snap_enabled = False
            studio.smart_guides_enabled = True
            item = next(item for item in studio._static_items() if item.data["id"] == "b0")
            studio.selection.replace((item,))
            drag = studio.canvas.pointer_drag
            start = item.mapToScene(item.rect().center())
            assert drag.press(item, start, Qt.NoModifier)
            drag.move(start + QPointF(20, 20), Qt.NoModifier)
            batches = []
            if "--profile" in sys.argv:
                import cProfile
                profile = cProfile.Profile()
                profile.enable()
            for batch in range(3):
                timings = []
                for step in range(15):
                    tick = time.perf_counter()
                    drag.move(start + QPointF(40 + step * 11, 50 + step * 9), Qt.NoModifier)
                    timings.append((time.perf_counter() - tick) * 1000)
                batches.append(statistics.mean(timings))
            if "--profile" in sys.argv:
                profile.disable()
                import pstats
                pstats.Stats(profile).sort_stats("cumtime").print_stats(22)
            drag.release()
            results.append(dict(objects=count, boxes=boxes, pipes=count - boxes,
                                move_ms=round(min(batches), 3), batches_ms=[round(v, 3) for v in batches]))
        window.close()
        window.deleteLater()
        app.processEvents()
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
