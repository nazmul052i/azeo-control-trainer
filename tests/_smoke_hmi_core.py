"""Core HMI assets and services survive independently of authoring UI."""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtSvg import QSvgRenderer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from azeo_control_trainer.core.hmi.pvms import symbols  # noqa: E402
from azeo_control_trainer.core.hmi.history import (  # noqa: E402
    ContinuousHistorian,
    HistoryPoint,
)
from azeo_control_trainer.core.hmi.history.view import ProcessHistoryView  # noqa: E402
from azeo_control_trainer.core.hmi.theme import hphmi, vision  # noqa: E402

failures: list[str] = []
application = QApplication.instance() or QApplication([])


def check(label: str, ok: bool, detail: object = "") -> None:
    if ok:
        print(f"[ok]   {label}")
    else:
        failures.append(label)
        print(f"[FAIL] {label} {detail}")


asset_root = ROOT / "src" / "azeo_control_trainer" / "core" / "hmi" / "assets"
check("the P&ID catalog is a core HMI asset", asset_root in symbols.SYMBOLS_DIR.parents,
      symbols.SYMBOLS_DIR)
check("the catalog has commercial breadth", len(symbols.CATALOG) >= 100,
      len(symbols.CATALOG))

all_assets = sorted((asset_root / "symbols" / "symbols").rglob("*.svg"))
check("all 140 promoted and two tapered machine SVG assets are present", len(all_assets) == 142,
      len(all_assets))
invalid_assets = [str(path.relative_to(asset_root)) for path in all_assets
                  if not QSvgRenderer(str(path)).isValid()]
check("all promoted SVG assets render in Qt", not invalid_assets,
      invalid_assets[:3])

bad: list[str] = []
for name in symbols.CATALOG:
    if symbols.renderer(name) is None:
        bad.append(f"{name}: invalid SVG")
check("every catalogued symbol renders", not bad, bad[:3])

# A port in transparent viewport padding produces a connector that looks
# detached even though routing and persistence are technically valid. Audit
# the whole runtime palette against the same alpha outline the canvas uses.
bad_ports: list[str] = []
for name in symbols.CATALOG:
    ports = symbols.connection_ports(name)
    outline = symbols.outline_points(name)
    if set(ports) != {"n", "e", "s", "w"}:
        bad_ports.append(f"{name}: {sorted(ports)}")
        continue
    for side, point in ports.items():
        distance = min(
            math.hypot(point[0] - sample[0], point[1] - sample[1])
            for sample in outline
        )
        if distance > 0.035:
            bad_ports.append(f"{name}.{side}: gap={distance:.4f}")
check("every catalogued process port touches visible symbol geometry",
      not bad_ports, bad_ports[:8])

check("process history is a core service",
      ContinuousHistorian.__module__.endswith("core.hmi.history.historian")
      and HistoryPoint.__module__.endswith("core.hmi.history.historian")
      and ProcessHistoryView.__module__.endswith("core.hmi.history.view"))

reports = [vision.report(theme) for theme in hphmi.THEMES]
check("every HPHMI theme passes the vision checks",
      all(report["passes"] for report in reports),
      [(report["theme"], report["passes"]) for report in reports])
check("shape redundancy carries meaning when colour collapses",
      sum(len(report["rescued_by_shape"]) for report in reports) > 0)

# The removed package must never creep back as an accidental parallel stack.
legacy = ROOT / "src" / "azeo_control_trainer" / "core" / "hmi" / "builder_hmi"
check("there is no parallel builder package", not legacy.exists(), legacy)
active_refs: list[str] = []
for path in (ROOT / "src").rglob("*.py"):
    if "builder_hmi" in path.read_text(encoding="utf-8", errors="ignore"):
        active_refs.append(str(path.relative_to(ROOT)))
check("core Python has no builder imports", not active_refs, active_refs)

print()
if failures:
    print(f"{len(failures)} core-HMI check(s) FAILED:")
    for failure in failures:
        print(f"  - {failure}")
    raise SystemExit(1)
print("All core-HMI checks passed.")
