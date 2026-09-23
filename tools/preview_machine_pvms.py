"""Open a disposable machine PVM display in the real Operator Station.

Uses simulated PID/DEVCTL tags; never reads or changes a configured project.
Optional --capture-dir records all themes and the three paired faceplates.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def preview_graphs():
    import azeo_control_trainer.core.strategy.blocks  # noqa: F401
    from azeo_control_trainer.core.strategy.model.block_registry import BlockRegistry
    from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
    from azeo_control_trainer.core.strategy.model.terminal import Quality

    graphs = {}
    for module, speed in (("VFD", 1500.0), ("TURBINE", 4500.0), ("COMPRESSOR", 9000.0)):
        graph = StrategyGraph(module)
        pid = BlockRegistry().create("PID", "SPEED")
        pid.config.params.update(mode="AUTO", normal_mode="AUTO", pv_eng_units="rpm",
                                  pv_scale_lo=0.0, pv_scale_hi=12000.0, sp_hi=12000.0)
        pid._apply_config()
        pid.inputs["IN"].value = speed
        pid.inputs["IN"].status = Quality.GOOD
        pid.inputs["SP"].value = speed
        device = BlockRegistry().create("DEVCTL", "RUN")
        device._apply_config()
        for key in ("INTERLOCK", "PERMISSIVE_D", "RUN_FB", "START_CMD"):
            device.inputs[key].value = True
            device.inputs[key].status = Quality.GOOD
        graph.add_block(pid)
        graph.add_block(device)
        graphs[module] = graph
    return graphs


def preview_display():
    from azeo_control_trainer.core.hmi.pvms.base import registry
    from azeo_control_trainer.core.hmi.pvms.publishing import PvmDisplay

    pvms = []
    for x, module, variant in ((160, "VFD", "vfd"), (480, "TURBINE", "turbine_speed"),
                                (800, "COMPRESSOR", "compressor_speed")):
        pvm = registry.get("PID", "dynamo_inline", variant)().place(
            module.lower(), path=f"{module}/SPEED", device=f"{module}/RUN",
            x=x, y=155, w=220, h=175)
        pvms.append(pvm.to_dict())
    return PvmDisplay(name="Machine PVM preview", width=1200, height=560, pvms=pvms, items=[
        dict(id="title", kind="text", x=40, y=22, w=1100, h=42, text="MACHINE SPEED AND RUN CONTROL",
             font_size=20, font_bold=True, text_role="TEXT"),
        dict(id="note", kind="text", x=40, y=72, w=1100, h=36,
             text="SIMULATED TAGS  |  Double-click a machine for speed controls, run status and trends.",
             font_size=11, text_role="TEXT_DIM"),
        dict(id="turbine_art", kind="symbol", symbol="turbine_tapered", x=430, y=405, w=120, h=75,
             line_role="EQUIPMENT", fill_role="EQUIPMENT_FILL"),
        dict(id="compressor_art", kind="symbol", symbol="compressor_tapered", x=700, y=405, w=120, h=75,
             line_role="EQUIPMENT", fill_role="EQUIPMENT_FILL"),
        dict(id="pipe", kind="pipe", a="turbine_art", b="compressor_art", a_side="e", b_side="w"),
        dict(id="caption", kind="text", x=390, y=492, w=460, h=32, text_halign="center",
             text="Tapered turbine / mirrored compressor drawing symbols", text_role="TEXT_DIM"),
    ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-dir", type=Path)
    parser.add_argument("--close", action="store_true")
    args = parser.parse_args()
    from PySide6.QtCore import QCoreApplication, QEvent, QSettings, QTimer
    from PySide6.QtWidgets import QApplication
    from azeo_control_trainer.azeo_operator_station.console import LiveStation
    from azeo_control_trainer.azeo_operator_station.deployment import PvmDeployment
    from azeo_control_trainer.azeo_operator_station.shell.chrome import ConsoleSettings
    from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore
    from azeo_control_trainer.core.hmi.pvms.rendering.renderer import pvm_from_dict

    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory(prefix="azeo-machine-preview-") as directory:
        root = Path(directory)
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(root / "preferences"))
        graphs = preview_graphs()

        def scan():
            for graph in graphs.values():
                for block in graph.blocks.values():
                    if block.block_type == "DEVCTL":
                        block.inputs["RUN_FB"].value = bool(block.outputs["DO_START"].value)
                    block.execute(0.25)

        scan()
        scan()
        document = preview_display()
        store = DisplayStore(root / "displays" / "pvm")
        store.save_draft(document)
        store.publish(document, by="Preview", resolve=lambda path: bool(path))
        station = LiveStation(PvmDeployment(store), lambda: graphs, config_root=root,
                              settings=ConsoleSettings(theme="dark"))
        station.setWindowTitle("Machine PVM preview — simulated tags")
        station.resize(1400, 880)
        station.show_display(document.name)
        station.show()
        timer = QTimer(station)
        timer.timeout.connect(scan)
        timer.start(250)

        def capture():
            try:
                if args.capture_dir:
                    args.capture_dir.mkdir(parents=True, exist_ok=True)
                    for theme in ("silver", "dark", "hpgray", "azeo_live"):
                        station.apply_theme(theme)
                        station._close_unpinned_faceplates()
                        station.view.refresh()
                        app.processEvents()
                        station.grab().save(str(args.capture_dir / f"{theme}-machines.png"))
                        for raw in document.pvms:
                            station.open_faceplate(pvm_from_dict(raw))
                            face = station.faceplates[-1][1]
                            face.refresh()
                            app.processEvents()
                            face.grab().save(str(args.capture_dir / f"{theme}-{raw['variant']}.png"))
                            print(f"Captured {theme}: {raw['variant']}", flush=True)
                station.apply_theme("dark")
                station.open_faceplate(pvm_from_dict(document.pvms[1]))
                station.raise_()
                station.faceplates[-1][1].raise_()
            except Exception:
                traceback.print_exc()
                app.exit(1)
                return
            if args.close:
                station.close()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                app.quit()

        QTimer.singleShot(1000, capture)
        return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
