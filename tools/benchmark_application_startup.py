"""Measure one real product launch on an isolated APVC project and exit cleanly."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("surface", choices=("explorer", "control", "graphics", "station", "simulation"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native", action="store_true")
    parser.add_argument("--profile", action="store_true", help="Diagnostic call profile, not a timing qualification")
    parser.add_argument("--eager-diagrams", action="store_true",
                        help="Reproduce eager diagram construction as an isolated startup comparison")
    parser.add_argument("--reference-startup-directory", type=Path,
                        help="Trusted comparison sources: strategy_scene.py, designer_tab.py, tagdb.py; child process only")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not args.native:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["AZEO_LOG_DIR"] = str(args.output.parent / (args.surface + "-logs"))
    import_start = time.perf_counter()
    from PySide6 import QtWidgets
    from PySide6.QtCore import QEvent, QObject, QSettings, QTimer
    reference_hashes = {}
    if args.reference_startup_directory:
        from azeo_control_trainer.azeo_control_designer.canvas import strategy_scene
        from azeo_control_trainer.azeo_control_designer import designer_tab
        from azeo_control_trainer.core.strategy import tagdb
        for module in (strategy_scene, designer_tab, tagdb):
            path = args.reference_startup_directory / (module.__name__.rsplit(".", 1)[1] + ".py")
            source = path.read_bytes()
            reference_hashes[path.name] = hashlib.sha256(source).hexdigest()
            exec(compile(source, str(path), "exec"), module.__dict__)
    import azeo_control_trainer.app as application
    from azeo_control_trainer.config import paths
    from azeo_control_trainer.core.presentation import headless
    from azeo_control_trainer.core.strategy.serialization import strategy_io
    from azeo_control_trainer.azeo_control_designer.designer_tab import StrategyCanvas, StrategyDesignerTab

    report = dict(surface=args.surface, native=args.native, phases={}, errors=[],
                  diagnostic_profile=args.profile,
                  reference_source_hashes=reference_hashes,
                  eager_diagrams=args.eager_diagrams,
                  import_ms=(time.perf_counter() - import_start) * 1000)
    report["source_sha256"] = {
        relative: hashlib.sha256((ROOT / "src/azeo_control_trainer" / relative).read_bytes()).hexdigest()
        for relative in ("azeo_control_designer/designer_tab.py", "azeo_control_designer/canvas/strategy_scene.py",
                         "core/strategy/tagdb.py")}
    started = None
    expected = {"explorer": {"ExplorerWindow"}, "control": {"StrategyDesignerWindow", "ControlDesignerWindow"},
                "graphics": {"HmiStudioWindow"}, "station": {"LiveStation", "OperatorStationWindow"},
                "simulation": {"SimulationWorkbenchDialog"}}[args.surface]
    original_application = QtWidgets.QApplication
    original_exec = original_application.exec
    wrapped = []

    if args.eager_diagrams:
        create = StrategyDesignerTab._create_canvas

        def eager(self, *values, **kwargs):
            kwargs["defer_visuals"] = False
            canvas = create(self, *values, **kwargs)
            load = canvas.scene.load_graph

            def load_eager(*values, **kwargs):
                kwargs["defer_items"] = False
                load(*values, **kwargs)
                self._connect_all_block_signals(canvas.scene)

            canvas.scene.load_graph = load_eager
            return canvas

        StrategyDesignerTab._create_canvas = eager
        wrapped.append((StrategyDesignerTab, "_create_canvas", create))

    def phase(owner, name):
        original = getattr(owner, name)

        def call(*values, **kwargs):
            begin = time.perf_counter()
            try:
                return original(*values, **kwargs)
            finally:
                report["phases"].setdefault(name, []).append((time.perf_counter() - begin) * 1000)

        setattr(owner, name, call)
        wrapped.append((owner, name, original))

    for method in ("auto_load_project", "_create_canvas", "_on_canvas_tab_changed", "auto_go_online"):
        phase(StrategyDesignerTab, method)
    phase(application, "_attach_field_io")

    class PaintProbe(QObject):
        def eventFilter(self, watched, event):  # noqa: N802
            if event.type() != QEvent.Paint:
                return False
            if getattr(self.parent(), "done", False):
                return False
            if isinstance(watched, QtWidgets.QWidget):
                window = watched.window()
                if type(window).__name__ in expected:
                    report.setdefault("primary_first_paint_ms", (time.perf_counter() - started) * 1000)
                    report["primary_title"] = window.windowTitle()
            return False

    class ProbeApplication(original_application):
        def __init__(self, *values):
            super().__init__(*values)
            self.probe = PaintProbe(self)
            self.installEventFilter(self.probe)

        def exec(self):
            report["event_loop_entry_ms"] = (time.perf_counter() - started) * 1000
            self.setQuitOnLastWindowClosed(False)
            self.done = False

            def finish():
                self.done = True
                if "primary_first_paint_ms" not in report:
                    report["errors"].append("Primary window did not paint before the deadline")
                for widget in self.topLevelWidgets():
                    if type(widget).__name__ in expected:
                        widget.grab().save(str(args.output.with_suffix(".png")))
                for widget in list(self.topLevelWidgets()):
                    widget.close()
                self.quit()

            def check():
                if self.done:
                    return
                if "primary_first_paint_ms" in report:
                    report["interactive_check_ms"] = (time.perf_counter() - started) * 1000
                    canvases = [w for w in self.allWidgets() if isinstance(w, StrategyCanvas)]
                    report["module_models"] = sum(bool(canvas.scene.graph.blocks) for canvas in canvases)
                    report["diagram_items"] = sum(len(canvas.scene._block_items) for canvas in canvases)
                    QTimer.singleShot(500, finish)
                else:
                    QTimer.singleShot(100, check)

            QTimer.singleShot(100, check)
            QTimer.singleShot(60000, lambda: finish() if not self.done else None)
            return original_exec()

    with tempfile.TemporaryDirectory(prefix="azeo-startup-") as folder:
        temp = Path(folder)
        project = temp / "project"
        shutil.copytree(ROOT / "projects/AzeoPlantVirtualController", project,
                        ignore=shutil.ignore_patterns(".lock", ".lock.recover"))
        config_path = project / "_project.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))

        def relocate(value):
            if isinstance(value, dict):
                if value.get("factory") == "azeoplant.embedding:create_embedded_plant":
                    value["search_paths"] = [str(ROOT / "AzeoPlantSimulator")]
                for child in value.values():
                    relocate(child)
            elif isinstance(value, list):
                for child in value:
                    relocate(child)

        relocate(config)
        config_path.write_text(json.dumps(config), encoding="utf-8")
        paths.data_dir = lambda: temp / "data"
        strategy_io._SETTINGS_PATH = temp / "data/settings.json"
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(temp / "settings"))
        # Dedicated station presentation intentionally does nothing in headless
        # mode. Its offscreen benchmark still needs a shown Qt window to paint.
        headless.is_headless = lambda: args.surface != "station"
        QtWidgets.QApplication = ProbeApplication
        sys.argv = ["startup-benchmark", str(project)]
        from performance_host import BackgroundHostSampler
        host_sampler = BackgroundHostSampler()
        started = time.perf_counter()
        if args.profile:
            import cProfile
            profile = cProfile.Profile()
            profile.enable()
        try:
            report["exit_code"] = application.main(surface=args.surface)
        finally:
            if args.profile:
                profile.disable()
                profile.dump_stats(str(args.output.with_suffix(".prof")))
                import pstats
                with args.output.with_suffix(".profile.txt").open("w", encoding="utf-8") as stream:
                    pstats.Stats(profile, stream=stream).strip_dirs().sort_stats("cumulative").print_stats(75)
            QtWidgets.QApplication = original_application
            for owner, name, original in wrapped:
                setattr(owner, name, original)
            report["process_work_ms"] = (time.perf_counter() - started) * 1000
            host_sampler.close()
            report["host_samples"] = host_sampler.drain()
            report["host_sampling_error"] = host_sampler.error
            args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 1 if report["errors"] else report.get("exit_code", 1)


if __name__ == "__main__":
    raise SystemExit(main())
