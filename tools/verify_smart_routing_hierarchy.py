"""Render the four templates and exercise native routing in an isolated project."""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["QT_QPA_PLATFORM"] = "windows" if "--native" in sys.argv else "offscreen"
OUT = ROOT / "logs" / "routing-hierarchy"
OUT.mkdir(parents=True, exist_ok=True)


def main():
    from PySide6.QtCore import QEvent, QPointF, QSettings, Qt, qInstallMessageHandler
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    from azeo_control_trainer.azeo_graphics_designer.window import HmiStudioWindow
    from azeo_control_trainer.azeo_operator_station.console import LiveStation
    from azeo_control_trainer.azeo_operator_station.deployment import PvmDeployment
    from azeo_control_trainer.core.hmi.pvms.hierarchy_templates import install_hierarchy_sample
    from azeo_control_trainer.core.hmi.pvms.rendering.viewer import PvmDisplayView
    from azeo_control_trainer.core.hmi.pvms.rendering.renderer import pvm_from_dict
    from azeo_control_trainer.core.hmi.pvms.rendering.items import PipeItem
    from azeo_control_trainer.core.hmi.pvms.elements import element_paths
    from azeo_control_trainer.core.hmi.pvms.visual_quality import visual_findings
    from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore
    from azeo_control_trainer.core.presentation import headless
    from azeo_control_trainer.core.strategy.model.block_registry import BlockRegistry
    from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
    from azeo_control_trainer.core.strategy.model.terminal import Quality
    import azeo_control_trainer.core.strategy.blocks  # noqa: F401

    app = QApplication([])
    headless.is_headless = lambda: True
    messages, result = [], {"views": [], "findings": {}}
    previous = qInstallMessageHandler(lambda kind, context, text: messages.append(text))
    with tempfile.TemporaryDirectory(prefix="azeo-routing-hierarchy-") as directory:
        root = Path(directory)
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, directory)
        sample = install_hierarchy_sample(root, "Template QA")
        store = DisplayStore(root)
        documents = [store.load_draft(name) for name in sample.displays]
        for document in documents:
            document.pvms = [pvm_from_dict(g) if isinstance(g, dict) else g for g in document.pvms]
        graphs = {}
        for document in documents:
            module = f"QA_L{document.level}"
            graph = StrategyGraph(module)
            kinds = {g.params["path"].split("/")[1]: g.block_type for g in document.pvms}
            for data in document.items:
                for path in element_paths(data):
                    kinds.setdefault(path.split("/")[1], "AI")
            for index, (tag, kind) in enumerate(kinds.items()):
                block = BlockRegistry().create(kind, tag)
                block._apply_config()
                graph.add_block(block)
                for terminal in (*block.inputs.values(), *block.outputs.values()):
                    terminal.status = Quality.GOOD
                    if not isinstance(terminal.value, bool):
                        terminal.value = 35.0 + index * 2
            graphs[module] = graph
            document.pvms = [replace(g, params={k: v.replace("CONFIGURE/", module + "/") for k, v in g.params.items()}) for g in document.pvms]
            for data in document.items:
                for pen in data.get("pens", ()):
                    pen["path"] = pen["path"].replace("CONFIGURE/", module + "/")
            store.save_draft(document)
        window = HmiStudioWindow(lambda: graphs, root)
        window.resize(1560, 960)
        window.show()
        station = None
        errors = []
        try:
            for document in documents:
                studio = window._open_created_display(document.name)
                studio.uiError.connect(errors.append)
                studio._load_document(document.to_dict())
                studio.fit_drawing()
                assert studio.save_draft()
                findings = studio.verification_findings()
                result["findings"][str(document.level)] = [f.message for f in findings]
                assert not [f.message for f in findings if f.blocks_publish], result["findings"][str(document.level)]
                studio.publish("TEST")
                for theme in ("silver", "dark", "hpgray", "azeo_live"):
                    view = PvmDisplayView(studio._document(), lambda: graphs, theme=theme, config_root=root, live=False)
                    view.resize(1610, 940)
                    view.show()
                    app.processEvents()
                    try:
                        for _ in range(3):
                            view.refresh()
                        bad_routes = [i.route_message for i in view.scene().items() if isinstance(i, PipeItem) and i.route_status != "ok"]
                        assert not bad_routes, bad_routes
                        file = f"l{document.level}-{theme}.png"
                        assert view.grab().save(str(OUT / file))
                        rows = visual_findings(view.scene(), view.display, theme)
                        result["views"].append(dict(file=file, findings=[f.message for f in rows]))
                    finally:
                        view.close()
                        view.deleteLater()
                        app.processEvents()
            station = LiveStation(PvmDeployment(store), lambda: graphs, config_root=root, history_path=root / "history.sqlite")
            station.resize(1580, 980)
            assert station.show_display(sample.displays[1])
            station.show()
            app.processEvents()
            assert station.grab().save(str(OUT / "operator-station.png"))
            pvm = next(g for g in documents[1].pvms if g.block_type == "PID")
            station.open_faceplate(pvm)
            app.processEvents()
            result["station_opened"] = True
            studio = window._open_created_display("Pipe interaction QA")
            studio.uiError.connect(errors.append)
            studio.snap_enabled = studio.smart_guides_enabled = False
            first = studio.add_static("rect", 100, 100, 60, 60)
            last = studio.add_static("rect", 500, 100, 60, 60)
            pipe = studio.add_pipe(first, "e", last, "w")
            studio.selection.replace((pipe,))
            studio.set_zoom(100)
            studio.canvas.centerOn(350, 280)
            window.raise_()
            app.processEvents()
            original_ends = (QPointF(pipe._points[0]), QPointF(pipe._points[-1]))
            undo_count = len(studio._undo_stack)
            def mouse(kind, x, y):
                viewport = studio.canvas.viewport()
                position = studio.canvas.mapFromScene(QPointF(x, y))
                event = QMouseEvent(kind, QPointF(position), QPointF(viewport.mapToGlobal(position)),
                                    Qt.NoButton if kind == QEvent.MouseMove else Qt.LeftButton,
                                    Qt.NoButton if kind == QEvent.MouseButtonRelease else Qt.LeftButton,
                                    Qt.NoModifier)
                QApplication.sendEvent(viewport, event)
                app.processEvents()
            mouse(QEvent.MouseButtonPress, 310, 130)
            for y in (150, 180, 210):
                mouse(QEvent.MouseMove, 310, y)
            mouse(QEvent.MouseButtonRelease, 310, 210)
            assert (pipe._points[0], pipe._points[-1]) == original_ends
            assert any(a.y() == b.y() == 210 for a, b in zip(pipe._points, pipe._points[1:]))
            assert len(studio._undo_stack) == undo_count + 1
            assert studio.undo()
            pipe = studio._pipe_items()[0]
            junction = studio.insert_pipe_junction(pipe, QPointF(330, 130))
            outlet = studio.add_static("rect", 300, 420, 60, 60)
            branch = studio.add_pipe(junction, "n", outlet, "n")
            assert branch.data["a_side"] == "s"
            # Close parallel crossings exercise the merged bridge in native
            # paint, in addition to the pure endpoint regression assertion.
            for y in (240, 246):
                studio.add_pipe(None, "e", None, "w", point_a=QPointF(150, y), point_b=QPointF(550, y))
            branch.setZValue(2)
            studio.touch_geometry()
            studio.selection.clear()
            app.processEvents()
            studio.set_zoom(140)
            studio.canvas.centerOn(330, 280)
            app.processEvents()
            assert studio.canvas.grab().save(str(OUT / "pipe-interaction.png"))
            assert all(p.route_status == "ok" for p in studio._pipe_items())
            result["native_drag_undo_and_branch"] = True
            # Isolate segment-edit geometry from A* and paint; report best of
            # three batches as a measurement rather than a frame-rate claim.
            from azeo_control_trainer.core.hmi.pvms.rendering.routing import Point, move_orthogonal_segment
            timings = []
            for _ in range(3):
                start = time.perf_counter()
                for n in range(1000):
                    move_orthogonal_segment((Point(0, 0), Point(400, 0)), 0, n % 100)
                timings.append((time.perf_counter() - start) * 1000)
            result["segment_geometry_1000_ms"] = timings
            result["success"] = not errors and not messages
        except Exception:
            import traceback
            result["error"] = traceback.format_exc()
        finally:
            if station:
                station.close()
            for studio in window.studios():
                studio.unsaved = False
            window.close()
            app.processEvents()
            result["qt_messages"], result["ui_errors"] = messages, errors
            result["success"] = result.get("success", False) and not errors and not messages
            qInstallMessageHandler(previous)
    (OUT / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
