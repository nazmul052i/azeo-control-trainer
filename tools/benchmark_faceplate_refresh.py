"""Measure pinned faceplate refresh on saved APVC graphs; no controller runs."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import logging
import os
from pathlib import Path
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402
import azeo_control_trainer.core.strategy.blocks  # noqa: E402,F401
from azeo_control_trainer.core.strategy.serialization.strategy_io import load_strategy  # noqa: E402
from azeo_control_trainer.core.hmi.binding import LiveGraphSource  # noqa: E402
from azeo_control_trainer.core.hmi.pvms.base import registry  # noqa: E402
from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore  # noqa: E402
from azeo_control_trainer.core.hmi.pvms.render import PvmFaceplateWidget  # noqa: E402
from azeo_control_trainer.core.hmi.pvms.rendering.items import PvmItem  # noqa: E402
from azeo_control_trainer.core.hmi.pvms.rendering.viewer import PvmDisplayView  # noqa: E402
from operator_responsiveness import distribution  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--writable", action="store_true",
                        help="Include the station's permission checks (no writes are sent)")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    logging.disable(logging.WARNING)
    app = QApplication.instance() or QApplication([])
    project = ROOT / "projects/AzeoPlantVirtualController"
    graphs = [load_strategy(path, remember=False)[0] for folder in ("control", "sequence")
              for path in sorted((project / folder).glob("*.json"))]
    inventories = []
    def graph_map():
        inventories.append(True)
        return {graph.name: graph for graph in graphs}
    source = LiveGraphSource(graph_map)
    reads = Counter()
    read = source.read

    def observed_read(path):
        reads[path] += 1
        return read(path)

    source.read = observed_read
    store = DisplayStore(project / "displays/pvm")
    document = store.published_document("U300 - L2 Charge Heating", workstation="CON-01")
    assert document is not None
    view = PvmDisplayView(document, source._graphs, config_root=store.root, source=source, live=False)
    windows = []
    try:
        pvms = [item.pvm for item in view.scene().items()
                if isinstance(item, PvmItem) and item.pvm.block_type == "PID"][:4]
        assert len(pvms) == 4
        for pvm in pvms:
            kind = registry.get(pvm.block_type, "faceplate")
            config = view.renderer.pvm_config(kind, pvm.class_revision)
            windows.append(PvmFaceplateWidget(kind, pvm.params, view.engine, config=config,
                                              choices=pvm.choices, live=False))
            if args.writable:
                windows[-1].set_write_handler(view.engine.write, view.engine.can_write)
        result = {"modules": len(graphs), "faceplates": len(windows),
                  "engine_bindings": view.engine.monitored_count,
                  "faceplate_bindings": [sum(not isinstance(b, tuple) for b in w.bound.values()) for w in windows],
                  "fixture": "saved APVC U300 display and graphs; no provider/controller/archive; offscreen widget refresh, no paint",
                  "write_permission_checks": args.writable,
                  "batches": {}}
        for mode in ("pinned_only", "display_and_pinned"):
            batches = []
            for _ in range(3):
                times = []
                reads.clear()
                inventories.clear()
                for _ in range(30):
                    start = time.perf_counter()
                    if mode == "display_and_pinned":
                        view.refresh()
                    for window in windows:
                        window.refresh()
                    times.append((time.perf_counter() - start) * 1000)
                row = distribution(times)
                row["reads_per_cycle"] = sum(reads.values()) / 30
                row["inventories_per_cycle"] = len(inventories) / 30
                row["distinct_paths"] = len(reads)
                batches.append(row)
            result["batches"][mode] = batches
            print(mode, "p95", [round(b["p95_ms"], 2) for b in batches],
                  "reads/cycle", batches[0]["reads_per_cycle"], flush=True)
        (args.output_dir / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    finally:
        for window in windows:
            window.close()
        view.close()
        app.processEvents()


if __name__ == "__main__":
    main()
