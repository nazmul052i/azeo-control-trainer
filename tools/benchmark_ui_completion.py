"""Isolated, reproducible phase timings for the remaining UI work.

Fixtures are temporary and never open or save a shipped course display. The
callback numbers exclude paint; paint is reported separately. These are not
native input-to-paint qualification results.
"""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def measure(callback, samples=30):
    batches = []
    for batch in range(3):
        print(f"  batch {batch + 1}, {samples} samples", flush=True)
        values = []
        for step in range(samples):
            started = time.perf_counter()
            callback(batch * samples + step)
            values.append((time.perf_counter() - started) * 1000)
        ordered = sorted(values)
        batches.append(dict(median_ms=statistics.median(values),
                            p95_ms=ordered[math.ceil(.95 * len(ordered)) - 1],
                            max_ms=max(values), samples_ms=values))
    return batches


def history(app, _temp):
    import numpy as np
    from azeo_control_trainer.core.hmi.history import ContinuousHistorian
    from azeo_control_trainer.core.hmi.history.view import ProcessHistoryView

    source = ContinuousHistorian(None, capacity=86520)
    source.read_only = True
    count = 86400
    points = []
    for pen in range(10):
        point = source.add_point(f"LOOP/P{pen}/PV", module="LOOP", unit="bar")
        points.append(point)
        values = np.sin(np.arange(count) / 120) + pen
        for name, data in (("times", range(count)), ("values", values),
                           ("qualities", ["GOOD"] * count),
                           ("wall_times", [None] * count), ("sim_times", [None] * count),
                           ("runs", [""] * count), ("sample_units", ["bar"] * count)):
            setattr(point, name, deque(data, maxlen=86520))
        point.version = count
    print("History fixture populated; constructing window", flush=True)
    window = ProcessHistoryView(source, "LOOP")
    window._timer.stop()
    window.show()
    window._chart.set_time_window(20)
    window._refresh()
    print("History initial refresh complete", flush=True)
    app.processEvents()
    result = {"pens": len(window._pens), "samples_per_pen": count}
    print("Unchanged refresh", flush=True)
    result["unchanged_refresh"] = measure(lambda _: window._refresh())

    def changed(step):
        for point in points:
            point.sample(count + step, step % 100)
        window._refresh()

    print("Changed refresh", flush=True)
    result["changed_refresh"] = measure(changed)
    print("Cursor", flush=True)
    result["cursor"] = measure(lambda step: window._on_cursor_changed(
        (count - 100 + step) / 60, {}), samples=300)
    print("Paint", flush=True)
    result["paint"] = measure(lambda _: window.grab(), samples=10)
    window.close()
    window.deleteLater()
    app.processEvents()
    return result


def graphics(app, temp, *, mixed=False):
    from PySide6.QtCore import QPointF, Qt
    from azeo_control_trainer.azeo_graphics_designer.window import HmiStudioWindow

    window = HmiStudioWindow(lambda: {}, temp / "displays")
    window.resize(1440, 900)
    window.show()
    studio = window.current()
    studio.enter_edit()
    result = {}
    for count in (120, 500, 1000):
        boxes = count * 2 // 3
        boxes += boxes % 2
        items = [dict(id=f"b{i}", kind="rect" if i % 3 else "text",
                      text=f"Equipment {i}", x=(i % 20) * 100, y=(i // 20) * 65,
                      w=60, h=40) for i in range(boxes)]
        pvms = []
        if mixed:
            from azeo_control_trainer.core.hmi.pvms.base import registry
            cls = registry.get("PID", "dynamo_compact")
            for index, data in enumerate(items):
                if index % 4 == 0:
                    pvm = cls().place(data["id"], x=data["x"], y=data["y"], w=60, h=40,
                                      path=f"UNIT/PID{index}")
                    pvms.append(pvm.to_dict())
                elif index % 4 == 1:
                    data.update(kind="symbol", symbol="bubble_column", h=55)
            items = [data for index, data in enumerate(items) if index % 4 != 0]
        items += [dict(id=f"p{i}", kind="pipe", a=f"b{i * 2}", b=f"b{i * 2 + 1}",
                       a_side="e", b_side="w", route_mode="manual", route_points=[])
                  for i in range(count - boxes)]
        studio._load_document(dict(display=studio.display.name, items=items, pvms=pvms, width=2100, height=2400))
        studio.set_zoom(60)
        studio.snap_enabled = False
        studio.smart_guides_enabled = True
        app.processEvents()
        selected = sorted([*studio._static_items(), *studio._items()],
                          key=lambda item: item.pvm.id if hasattr(item, "pvm") else item.data["id"])
        item = selected[0]
        studio.selection.replace((item,))
        drag = studio.canvas.pointer_drag
        row = {}
        row["selection"] = measure(lambda step: studio.selection.replace((selected[step % len(selected)],)))
        studio.selection.replace((item,))
        start = item.mapToScene(item.rect().center())
        drag.press(item, start, Qt.NoModifier)
        tick = time.perf_counter()
        drag.move(start + QPointF(20, 20), Qt.NoModifier)
        row["prepare_and_first_move_ms"] = (time.perf_counter() - tick) * 1000
        row["move"] = measure(lambda step: drag.move(
            start + QPointF(40 + (step % 250), 50 + (step % 170)), Qt.NoModifier), samples=300)
        tick = time.perf_counter()
        drag.release()
        row["release_ms"] = (time.perf_counter() - tick) * 1000
        row["paint"] = measure(lambda _: studio.canvas.viewport().grab(), samples=10)
        row["document"] = measure(lambda _: studio._document(), samples=10)
        result[str(count)] = row
    studio.unsaved = False
    window.close()
    window.deleteLater()
    app.processEvents()
    return result


def designer(app, temp):
    from azeo_control_trainer.azeo_graphics_designer.configurator.designer import PvmConfigDesigner
    window = PvmConfigDesigner(temp, pvm_class="HP_C_Valve")
    window.show()
    app.processEvents()
    renders = []
    render = window._render_preview

    def tracked():
        renders.append(1)
        render()

    window._render_preview = tracked
    prop = window.config.groups[0].properties[0]

    def edit(step):
        prop.description = f"Description {step}"
        window._mark_unsaved()

    result = {"edit": measure(edit), "renders_during_90_edits": len(renders)}
    tick = time.perf_counter()
    from PySide6.QtTest import QTest
    QTest.qWait(100)
    app.processEvents()
    result["settle_ms"] = (time.perf_counter() - tick) * 1000
    result["total_renders"] = len(renders)
    window.unsaved = False
    window.close()
    window.deleteLater()
    app.processEvents()
    return result


def catalog(_app, _temp):
    from azeo_control_trainer.core.configuration.catalog import CatalogIndex
    result = {}
    for count in (10000, 100000):
        bundle = dict(project={"id": "fixture"}, tags=[], catalog=dict(edges=[], nodes=[
            dict(path=f"UNIT{i % 100}/LOOP{i:06}/PV", name=f"PV{i}", kind="terminal",
                 description="Process pressure", block_type="AI") for i in range(count)]))
        start = time.perf_counter()
        index = CatalogIndex(bundle)
        row = {"build_ms": (time.perf_counter() - start) * 1000}
        row["search"] = measure(lambda step: index.search(
            ["", "UNIT10", "pressure AI", "LOOP0001", "absent"][step % 5], kinds=("terminal",)))
        result[str(count)] = row
    return result


def main():
    import faulthandler
    faulthandler.enable()
    faulthandler.dump_traceback_later(60, repeat=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("history", "graphics", "designer", "catalog"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native", action="store_true")
    parser.add_argument("--mixed", action="store_true", help="Graphics fixture includes PVMs and process symbols")
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    if not args.native:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory(prefix="azeo-perf-completion-") as folder:
        temp = Path(folder)
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, folder)
        if args.profile:
            import cProfile
            profile = cProfile.Profile()
            profile.enable()
        result = graphics(app, temp, mixed=args.mixed) if args.mode == "graphics" else globals()[args.mode](app, temp)
        if args.profile:
            profile.disable()
            import pstats
            pstats.Stats(profile).sort_stats("cumtime").print_stats(30)
    report = dict(mode=args.mode, platform=os.environ.get("QT_QPA_PLATFORM", "native"),
                  profiled=args.profile, mixed=args.mixed, result=result,
                  source_sha256={str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in (ROOT / "src/azeo_control_trainer").rglob("*.py")})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    faulthandler.cancel_dump_traceback_later()
    print(f"Saved {args.mode} measurements: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
