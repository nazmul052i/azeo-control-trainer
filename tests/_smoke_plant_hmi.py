"""Plant -> controller -> shared history: the core training loop.

This test intentionally exercises no private display implementation. The
plant remains behind ``SharedDataStore``; Control Designer scans its modules;
the core historian samples the same namespace consumed by published PVMs.

Run: D:\\development\\GitHub\\vpy\\Scripts\\python.exe tests/_smoke_plant_hmi.py
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
logging.disable(logging.WARNING)

from PySide6.QtWidgets import QApplication  # noqa: E402

import azeo_control_trainer.core.strategy.blocks  # noqa: E402,F401
from azeo_control_trainer.app import AreaContext  # noqa: E402
from azeo_control_trainer.core.hmi.history import ContinuousHistorian  # noqa: E402
from azeo_control_trainer.plant import available, get_plant  # noqa: E402
from azeo_control_trainer.plant.driver import PlantDriver  # noqa: E402
from azeo_control_trainer.core.datastore.shared_data_store import (  # noqa: E402
    SharedDataStore,
)
from azeo_control_trainer.core.strategy.serialization import strategy_io  # noqa: E402
from azeo_control_trainer.core.strategy.tagdb import TagDatabase  # noqa: E402
from azeo_control_trainer.azeo_control_designer.designer_window import (  # noqa: E402
    StrategyDesignerWindow,
)

REPO = Path(__file__).resolve().parent.parent
AREA = REPO / "src" / "strategies" / "azeo_training"
strategy_io.STRATEGY_DIR = AREA
app = QApplication.instance() or QApplication([])
failures: list[str] = []


def check(label: str, ok: bool, detail: object = "") -> None:
    if ok:
        print(f"[ok]   {label}")
    else:
        failures.append(label)
        print(f"[FAIL] {label} {detail}")


def pump(seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def strategy_changes() -> set[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "--", "src/strategies/"],
        capture_output=True, text=True, timeout=30, cwd=REPO,
    )
    return set(result.stdout.splitlines()) if result.returncode == 0 else set()


baseline = strategy_changes()

# ------------------------------------------------------------ plant contract
check("the example plant is registered", "training_process" in available(),
      available())
plant = get_plant("training_process")
tagdb = TagDatabase.from_area(AREA)
problems = plant.verify_against(tagdb)
check("the plant contract resolves through the derived tag database",
      not problems, problems[:3])
check("measurements are plant-owned",
      {"LI-101.PV", "FT-102.PV", "MTR-102.run_fb"} <= set(plant.writes()),
      sorted(plant.writes()))
check("operator commands are not plant-owned",
      "MTR-102.cmd_start" not in plant.writes()
      and "MTR-102.cmd_start" in plant.operator_tags(tagdb))

# Open-loop physics proves that the simulated process is more than a tag
# generator and that controller output can materially change it.
inputs = {tag: 0.0 for tag in plant.reads()}
inputs.update({"MTR-102.do_start": True, "XV-101.solenoid": True,
               "FV-102.OUT": 50.0})
for _ in range(20):
    plant.step(0.5, inputs)
check("a running pump through a half-open valve develops flow",
      abs(plant.flow_a - 60.0) < 1.0, plant.flow_a)
inputs["XV-101.solenoid"] = False
for _ in range(20):
    plant.step(0.5, inputs)
check("closing the block valve stops flow", plant.flow_a < 0.1, plant.flow_a)

# ------------------------------------------------------ plant behind the store
plant = get_plant("training_process")
store = SharedDataStore()
store.tagdb = tagdb
driver = PlantDriver(plant, store)
driver.seed()
check("field values exist before the controller goes on scan",
      store.get("LI-101.PV") is not None)
driver.start()

window = StrategyDesignerWindow(store=store, plugin=AreaContext(AREA))
designer = window.designer
designer.auto_load_project()
designer.auto_go_online()
pump(2.0)

graph = next(graph for graph in designer.open_graphs()
             if graph.name == "MTR-102")
blocks = {block.instance_name: block for block in graph.blocks.values()}
ai = blocks["AI_LI101"]
check("a scanned I/O block reads Good quality from the plant",
      ai.outputs["OUT"].status.name == "GOOD",
      ai.outputs["OUT"].status.name)
check("the scanned value follows process state",
      abs(float(ai.outputs["OUT"].value) - plant.t101.level_gal) < 20.0,
      (ai.outputs["OUT"].value, plant.t101.level_gal))

# ---------------------------------------------------------- shared historian
historian = ContinuousHistorian(store)
count = historian.configure_from(tagdb, designer.open_graphs())
check("the core historian derives points from modules", count > 30, count)
for index in range(3):
    historian.collect(now=float(index + 1), force=True)
    pump(0.05)
times, values = historian.get_series("LI-101.PV")
check("history exposes the trend widget query contract",
      len(times) == len(values) == 3, (times, values))
check("faceplate trend defaults are module-specific",
      bool(historian.default_pens_for("MTR-102")),
      historian.default_pens_for("MTR-102"))
check("history exports engineering data as CSV",
      historian.to_csv(["LI-101.PV"]).startswith("time_min,LI-101.PV"))

# --------------------------------------------------------------- cleanup
designer.cleanup()
window.close()
driver.stop()
new_changes = strategy_changes() - baseline
check("running the core loop rewrites no shipped strategy", not new_changes,
      sorted(new_changes))

print()
if failures:
    print(f"{len(failures)} plant/HMI check(s) FAILED:")
    for failure in failures:
        print(f"  - {failure}")
    raise SystemExit(1)
print("All plant and core-HMI checks passed.")
