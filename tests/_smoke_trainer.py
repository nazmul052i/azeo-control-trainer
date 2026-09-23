"""The trainer stands up with no plant behind it.

This is the fork's contract test: Control Designer must open, register its whole
block library, and load, compile and scan every shipped example module without
a simulation engine anywhere in the process.

Run:  D:\\development\\GitHub\\vpy\\Scripts\\python.exe tests/_smoke_trainer.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"[ok]   {label}")
    else:
        failures.append(label)
        print(f"[FAIL] {label} {detail}")


# ---------------------------------------------------------------- no plant
import azeo_control_trainer  # noqa: E402

leaked = [m for m in sys.modules
          if m.startswith(("simulator.", "simulator"))
          and m != "simulator_does_not_exist"]
check("no `simulator` package is imported anywhere", not leaked, leaked[:5])

# ---------------------------------------------------------------- blocks
import azeo_control_trainer.core.strategy.blocks  # noqa: E402,F401
from azeo_control_trainer.core.strategy.model.block_registry import registry  # noqa: E402

types = registry.all_types()
check(f"block library registers {len(types)} types", len(types) >= 140, len(types))

broken = []
for bt in types:
    try:
        blk = registry.get(bt)("t")
        blk.execute(0.1)
    except Exception as exc:                          # noqa: BLE001
        broken.append((bt, type(exc).__name__))
check("every block constructs and executes", not broken, broken[:5])

# ---------------------------------------------------------------- app
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from azeo_control_trainer.app import _resolve_area, _strategies_root  # noqa: E402
from azeo_control_trainer.core.datastore.shared_data_store import SharedDataStore  # noqa: E402
from azeo_control_trainer.core.strategy.engine.bridge import DataBridge  # noqa: E402
from azeo_control_trainer.core.strategy.engine.compiler import compile_strategy  # noqa: E402
from azeo_control_trainer.core.strategy.engine.runtime import StrategyRuntime  # noqa: E402
from azeo_control_trainer.core.strategy.serialization import strategy_io  # noqa: E402
from azeo_control_trainer.core.strategy.serialization.strategy_io import load_strategy  # noqa: E402

check("strategies root is found", _strategies_root().is_dir(), _strategies_root())
default_area = _resolve_area(None)
check("bare startup defaults to the registered virtual-controller project",
      default_area.name == "AzeoPlantVirtualController"
      and (default_area / "_project.json").is_file(), str(default_area))

# **Named, not inherited.** This is the contract test for the shipped
# worked example, so it opens that example by name. Taking whatever
# `_resolve_area(None)` returns tied it to the launcher's default, and the
# day the default moved to the Modbus area this test started looking for
# MTR-102 in a project that has never contained it.
area = _resolve_area("azeo_training")
# STRATEGY_DIR must stay a Path: the project tree joins it with `/`.
strategy_io.STRATEGY_DIR = area
check("default example area resolves", area.is_dir(), area)
check("STRATEGY_DIR is a Path, not a str", hasattr(area, "joinpath"))

from azeo_control_trainer.azeo_control_designer.designer_window import (  # noqa: E402
    StrategyDesignerWindow,
)

window = StrategyDesignerWindow(store=SharedDataStore(), plugin=None)
window.show()
app.processEvents()
check("Control Designer opens with no plugin and no engine", window.isVisible())

# ---------------------------------------------------------------- modules
control = area / "control"
modules = sorted(p for p in control.iterdir() if p.suffix == ".json")
check(f"example area ships {len(modules)} control modules", len(modules) >= 7,
      len(modules))

for path in modules:
    try:
        graph, _ = load_strategy(str(path))
        compiled = compile_strategy(graph)
        rt = StrategyRuntime()
        rt.load(compiled, DataBridge(SharedDataStore()))
        rt.go_online()
        for _ in range(6):
            rt.execute_scan(0.25)
        rt.go_offline()
        check(f"{graph.name}: loads, compiles and scans "
              f"({len(graph.blocks)} blocks, {len(graph.wires)} wires)",
              len(compiled.exec_order) == len(graph.blocks))
    except Exception as exc:                          # noqa: BLE001
        check(f"{path.name}: loads, compiles and scans", False, repr(exc))

# ---------------------------------------------------------------- startup
# Control Designer must open onto the area's modules, not an empty canvas.
from azeo_control_trainer.app import AreaContext, _startup_surface  # noqa: E402

check("the normal launch opens the engineering Explorer",
      _startup_surface([]) == "explorer")
check("--classic opens Control Designer first",
      _startup_surface(["--classic"]) == "control")
check("--graphics opens Graphics Designer first",
      _startup_surface(["--graphics"]) == "graphics")
check("--simulation opens Simulation Workbench first",
      _startup_surface(["--simulation"]) == "simulation")
check("--station is a dedicated operator-first launch, even when another "
      "surface flag is also present",
      _startup_surface(["--classic", "--station"]) == "station")

window.close()
app.processEvents()

window = StrategyDesignerWindow(store=SharedDataStore(),
                                plugin=AreaContext(area))
window.show()
app.processEvents()
designer = window.designer
tabs = designer._canvas_tabs

check("nothing is open before the area is loaded", tabs.count() == 0, tabs.count())
check("auto_load_project opens the area", designer.auto_load_project())
app.processEvents()
check(f"every control module opens in its own tab ({tabs.count()})",
      tabs.count() >= len(modules), tabs.count())
opened = [tabs.tabText(i) for i in range(tabs.count())]
check("MTR-102 is among the opened modules", "MTR-102" in opened, opened)
# An equipment module is a descriptor, not a function block diagram. Opening
# one as a strategy canvas produced an empty graph that failed validation
# with "Strategy has no blocks" and then offered itself for download.
check("equipment modules are not opened as strategy canvases",
      not any("Startup" in name for name in opened), opened)

# A dedicated station keeps this window hidden, but the Loop_fp engineering
# icon must be able to reveal the exact module rather than opening a generic
# editor. This is the same handoff used by LiveStation's ``studio`` action.
window.hide()
check("a Live faceplate can raise Control Designer on its associated module",
      window.open_module("MTR-102")
      and window.isVisible()
      and tabs.tabText(tabs.currentIndex()) == "MTR-102",
      tabs.tabText(tabs.currentIndex()))

# The Applications/Graphics Designer command is a window-activation command.
# Reconstructing the project on every click both froze Control Designer and
# could leave the new window behind it, which looked exactly like no launch.
graphics_first = window._pvm_studio()
app.processEvents()
graphics_first.hide()
graphics_second = window._pvm_studio()
app.processEvents()
check("Graphics Designer launches once, then subsequent commands raise the "
      "same project window",
      graphics_first is graphics_second
      and graphics_second.isVisible()
      and graphics_second.tabs.count() == 1,
      (graphics_first is graphics_second, graphics_second.isVisible(),
       graphics_second.tabs.count()))
graphics_second.close()
app.processEvents()

from azeo_control_trainer.core.strategy.engine.validator import (  # noqa: E402
    validate_strategy,
)

issues = []
for index in range(tabs.count()):
    canvas = tabs.widget(index)
    graph = getattr(getattr(canvas, "scene", None), "graph", None)
    if graph is None:
        continue
    issues += [f"{graph.name}: {r.message}" for r in validate_strategy(graph)]
check("the shipped area validates clean, so Download does not warn",
      not issues, issues[:3])

# ---------------------------------------------------------------- File menu
file_menu = None
for act in window.menuBar().actions():
    if act.text() == "&File":
        file_menu = act.menu()
        break
check("the File menu exists", file_menu is not None)
labels = [a.text() for a in file_menu.actions() if a.text()]
for wanted in ("&Close Module", "Close A&ll Modules",
               "Open &Project...", "C&lose Project"):
    check(f"File menu offers {wanted.replace('&', '')}", wanted in labels, labels)

n = tabs.count()
window._close_module()
app.processEvents()
check("Close Module closes one tab", tabs.count() == n - 1, tabs.count())

window._close_all_modules()
app.processEvents()
check("Close All Modules closes the rest", tabs.count() == 0, tabs.count())

designer.auto_load_project()
app.processEvents()
check("the area reopens after being closed", tabs.count() >= len(modules),
      tabs.count())

window._close_project()
app.processEvents()
check("Close Project leaves no module open", tabs.count() == 0, tabs.count())

window.close()
app.processEvents()
check("window closes cleanly", not window.isVisible())

# ----------------------------------------------------------------
print()
if failures:
    print(f"{len(failures)} trainer check(s) FAILED:")
    for f in failures:
        print("   -", f)
    sys.exit(1)
print("All Azeo Control Trainer checks passed.")


def test_trainer_stands_up():
    """pytest entry point — the checks above run at import."""
    assert not failures, failures
