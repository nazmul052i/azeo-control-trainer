"""Phase 0 of the type-driven HMI builder — model foundations, proven.

The proposal (docs/new_hmi_studio/HMI_BUILDER_PROPOSAL_revB.md §7) adds
units/range to Terminal, exposes the PID mode pair, makes duplicate block
names a hard validation error, and derives a Qt-free type catalog from
the registry. Its acceptance criteria are these checks:

- every shipped strategy loads and saves byte-identical (I1);
- the catalog builds with PySide6 blocked (I2);
- every registered type describes, with terminals;
- validate() rejects a graph with two blocks named alike;
- a PID reports target and actual mode independently;
- the emitted catalog matches its snapshot, so type drift is a diff.

Run:  D:\\development\\GitHub\\vpy\\Scripts\\python.exe tests/_smoke_typecatalog.py
Snapshot refresh:  set AZEO_UPDATE_SNAPSHOTS=1 and rerun.
"""
from __future__ import annotations

import importlib.abc
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# ---- I2 first: the blocker must be in place BEFORE anything imports, so
# a Qt import sneaking into the model layer fails here, not in review.
_QT_IMPORTED_EARLY = "PySide6" in sys.modules


class _QtBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, name, *args, **kwargs):
        if name == "PySide6" or name.startswith("PySide6."):
            raise ImportError("PySide6 blocked — model layer must be "
                              "importable without Qt (I2)")


sys.meta_path.insert(0, _QtBlocker())

from azeo_control_trainer.core.strategy.type_catalog import (  # noqa: E402
    build_catalog, catalog_json,
)
from azeo_control_trainer.core.strategy.model.strategy_graph import (  # noqa: E402
    StrategyGraph,
)
from azeo_control_trainer.core.strategy.model.block_registry import (  # noqa: E402
    BlockRegistry,
)
from azeo_control_trainer.core.strategy.serialization.strategy_io import (  # noqa: E402
    load_strategy, save_strategy,
)

REPO = Path(__file__).resolve().parent.parent
SNAPSHOT = Path(__file__).resolve().parent / "_snapshots" / "type_catalog.json"

failures: list[str] = []


def check(label: str, ok: bool, detail="") -> None:
    if ok:
        print(f"[ok]   {label}")
    else:
        failures.append(label)
        print(f"[FAIL] {label} {detail}")


# ------------------------------------------------------- I2: Qt-free build
check("harness itself imported no Qt beforehand", not _QT_IMPORTED_EARLY)
catalog = build_catalog()
check("catalog builds with PySide6 blocked (I2)", catalog["type_count"] > 0)
check("PySide6 still unimported after the build",
      "PySide6" not in sys.modules)

# -------------------------------------------------------- completeness
registered = sorted(BlockRegistry().all_types())
check(f"a definition for every registered type ({len(registered)})",
      sorted(catalog["types"]) == registered)
check("no type failed to describe", not catalog.get("failures"),
      catalog.get("failures"))
pinless = [t for t, d in catalog["types"].items()
           if not d["inputs"] and not d["outputs"] and t != "COMPOSITE"]
check("no empty terminal lists (COMPOSITE excepted — its pins are its "
      "interface blocks)", not pinless, pinless)

# --------------------------------------------- §7.1 EU metadata reaches out
ai_out = {o["name"]: o for o in catalog["types"]["AI"]["outputs"]}["OUT"]
check("AI OUT carries its engineering range",
      ai_out.get("eu_range") == [0.0, 100.0], ai_out)
pid_out = {o["name"]: o for o in catalog["types"]["PID"]["outputs"]}["OUT"]
check("PID OUT is the controller's own 0-100 % span",
      pid_out.get("units") == "%" and pid_out.get("eu_range") == [0.0, 100.0],
      pid_out)
pid_in = {i["name"]: i for i in catalog["types"]["PID"]["inputs"]}["IN"]
check("PID PV input carries the PV scale",
      pid_in.get("eu_range") == [0.0, 100.0], pid_in)
ao_out = {o["name"]: o for o in catalog["types"]["AO"]["outputs"]}["OUT"]
check("AO OUT carries its clamp range as EU range",
      ao_out.get("eu_range") is not None, ao_out)

# ------------------------------------------------ §7.2 the PID mode pair
pid = BlockRegistry().create("PID", "TIC_TEST")
pid._apply_config()
check("a PID reports target and actual mode independently",
      isinstance(pid.mode_target, str) and isinstance(pid.mode_actual, str)
      and pid.mode_target != "")
pid._pid_core.set_target_mode(
    type(pid._pid_core.target_mode).Man)
check("the pair can disagree (target moved, actual follows the core's "
      "own rules)", pid.mode_target == "MAN", pid.mode_target)

# --------------------------------------- §7.3 duplicate names: hard error
graph = StrategyGraph(name="DUP-TEST")
b1 = BlockRegistry().create("PID", "PID_1")
b2 = BlockRegistry().create("PID", "PID_1")
graph.add_block(b1)
graph.add_block(b2)
dup_errors = [e for e in graph.validate() if "Duplicate block name" in e]
check("validate() rejects two blocks named PID_1 as a hard error",
      len(dup_errors) == 1, graph.validate())
b2.instance_name = "PID_2"
check("and passes once renamed",
      not [e for e in graph.validate() if "Duplicate" in e])
auto = BlockRegistry().create("PID")
check("a block created with no name gets a unique default",
      auto.instance_name not in ("", "PID_1", "PID_2"), auto.instance_name)

# --------------------------------------------- I1: byte-idempotence sweep
modules = []
for area in sorted((REPO / "src" / "strategies").iterdir()):
    for sub in ("control", "sequence"):
        if (area / sub).is_dir():
            modules += sorted((area / sub).glob("*.json"))
churn = []
with tempfile.TemporaryDirectory() as tmp:
    for path in modules:
        result = load_strategy(path)
        graph = result[0] if isinstance(result, tuple) else result
        # The guarantee is IN-PLACE re-save idempotence (save_strategy
        # reads the existing file to preserve an empty "comments": []),
        # so save over a copy of the original, not onto a fresh path.
        out = Path(tmp) / path.name
        out.write_bytes(path.read_bytes())
        save_strategy(graph, path=out)
        if out.read_bytes() != path.read_bytes():
            churn.append(path.name)
check(f"every shipped module round-trips byte-identical "
      f"({len(modules)} modules, both areas)", not churn, churn)

# -------------------------------------------- §8.2 status mapping table
from azeo_control_trainer.connectivity.opcua.status_mapping import (  # noqa: E402
    from_ua_status, to_ua_status,
)
from azeo_control_trainer.core.strategy.model.terminal import (  # noqa: E402
    LimitStatus, Quality,
)

bad_pairs = []
for quality in Quality:
    for limit in LimitStatus:
        code = to_ua_status(quality, limit)
        if from_ua_status(code) != (quality, limit):
            bad_pairs.append((quality.name, limit.name, hex(code)))
check("to_ua_status round-trips all 12 quality × limit combinations "
      "(Qt-free, no server)", not bad_pairs, bad_pairs)
check("limit bits carry the DataValue InfoType flag",
      to_ua_status(Quality.GOOD, LimitStatus.HIGH_LIMITED) & 0x0400 != 0)
check("a clean Good is exactly zero",
      to_ua_status(Quality.GOOD, LimitStatus.NOT_LIMITED) == 0)
check("Bad keeps its limit bits — a clamped signal that goes Bad still "
      "says where it is clamped",
      to_ua_status(Quality.BAD, LimitStatus.LOW_LIMITED)
      == 0x80000000 | 0x0400 | 0x0100)

# ------------------------------------------------------- snapshot: drift
rendered = catalog_json(catalog) + "\n"
if not SNAPSHOT.exists() or os.environ.get("AZEO_UPDATE_SNAPSHOTS") == "1":
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(rendered, encoding="utf-8")
    print(f"[note] snapshot written: {SNAPSHOT.name} "
          f"({catalog['type_count']} types)")
check("catalog matches its snapshot — type drift must be a reviewed diff",
      SNAPSHOT.read_text(encoding="utf-8") == rendered,
      "run with AZEO_UPDATE_SNAPSHOTS=1 after reviewing the change")

print()
if failures:
    print(f"{len(failures)} type-catalog check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print(f"All type-catalog checks passed "
      f"({catalog['type_count']} types described).")
