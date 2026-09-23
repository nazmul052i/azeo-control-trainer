"""Phase 2 of the type-driven HMI builder — the binding engine, proven.

Acceptance criteria from HMI_BUILDER_PROPOSAL_revB.md §9, adapted to the
in-process `LiveGraphSource` (the UA source shares the engine and slots
in later):

- an indirect binding `{path}/OUT` resolves and updates live;
- after quality goes Bad the binding still reports the last good value
  and its timestamp;
- teardown returns the monitored count to baseline;
- a cyclic expression binding is rejected AT BIND TIME, readably;
- 500 simultaneous bindings poll inside a 100 ms frame;
- collapsing to a float never happens: Bad in, Bad out.

Run:  D:\\development\\GitHub\\vpy\\Scripts\\python.exe tests/_smoke_binding.py
"""
from __future__ import annotations

import importlib.abc
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class _QtBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, name, *args, **kwargs):
        if name == "PySide6" or name.startswith("PySide6."):
            raise ImportError("PySide6 blocked — binding engine must be "
                              "Qt-free (I2)")


sys.meta_path.insert(0, _QtBlocker())

import azeo_control_trainer.core.strategy.blocks  # noqa: F401,E402
from azeo_control_trainer.core.hmi.binding import (  # noqa: E402
    BindingEngine, BindingError, LiveGraphSource,
)
from azeo_control_trainer.core.strategy.model.block_registry import (  # noqa: E402
    BlockRegistry,
)
from azeo_control_trainer.core.strategy.model.strategy_graph import (  # noqa: E402
    StrategyGraph,
)
from azeo_control_trainer.core.strategy.model.terminal import (  # noqa: E402
    Quality,
)

failures: list[str] = []


def check(label: str, ok: bool, detail="") -> None:
    if ok:
        print(f"[ok]   {label}")
    else:
        failures.append(label)
        print(f"[FAIL] {label} {detail}")


# One live-ish module: an AI whose OUT we drive by hand.
graph = StrategyGraph(name="FIC-900")
ai = BlockRegistry().create("AI", "AI1")
ai.config.params.update({"scale_lo": 0.0, "scale_hi": 200.0,
                         "eng_units": "gpm"})
ai._apply_config()
pid = BlockRegistry().create("PID", "PID1")
pid._apply_config()
graph.add_block(ai)
graph.add_block(pid)

source = LiveGraphSource(lambda: {"FIC-900": graph})
engine = BindingEngine(source)
baseline = engine.monitored_count

# ------------------------------------------------- indirect, resolving live
events: list = []
binding = engine.bind("{path}/OUT", {"path": "FIC-900/AI1"},
                      on_change=events.append)
ai.outputs["OUT"].value = 42.0
ai.outputs["OUT"].status = Quality.GOOD
engine.poll()
check("an indirect binding resolves and updates live",
      binding.result.value == 42.0
      and binding.result.quality == Quality.GOOD, binding.result)
check("the result carries EU metadata, not just a float",
      binding.result.units == "gpm"
      and binding.result.eu_range == (0.0, 200.0), binding.result)
check("a change notifies exactly once per poll",
      len(events) == 1, len(events))
engine.poll()
check("an unchanged poll notifies nothing", len(events) == 1)

# ------------------------------------------------------ last-good retention
ai.outputs["OUT"].status = Quality.BAD
engine.poll()
check("Bad quality reports Bad — never the stale number as live",
      binding.result.quality == Quality.BAD)
check("but retains the last good value and its timestamp",
      binding.result.last_good_value == 42.0
      and binding.result.last_good_at is not None, binding.result)

# ------------------------------------------------------------------ forced
ai.outputs["OUT"].status = Quality.GOOD
forced_binding = engine.bind("{path}/IN", {"path": "FIC-900/PID1"})
pid.inputs["IN"].status = Quality.GOOD
pid.force_terminal("IN", 77.0)
pid.apply_forces()
engine.poll()
check("forced state travels with the value (I5)",
      forced_binding.result.forced is True
      and forced_binding.result.value == 77.0)

# -------------------------------------------------------------- expressions
dev = engine.bind_expression("pv - sp",
                             {"pv": "{p}/OUT", "sp": "{p}/HI_ACT"},
                             {"p": "FIC-900/AI1"})
check("an expression binding evaluates (deviation shape)",
      dev.result.quality != Quality.BAD)

try:
    engine.bind_expression("a > b", {"a": "FIC-900/AI1/OUT",
                                     "b": "FIC-900/AI1/OUT"})
    check("comparisons are refused — display expressions are arithmetic "
          "only", False)
except BindingError as error:
    check("comparisons are refused — display expressions are arithmetic "
          "only", "not part of the display expression language"
          in str(error), error)

try:
    engine.bind("{path}/OUT", {})
    check("an unresolved placeholder is refused at bind time", False)
except BindingError as error:
    check("an unresolved placeholder is refused at bind time",
          "placeholder" in str(error))

# ------------------------------------------------------- cycles, bind time
x = engine.bind_expression("base + 1", {"base": "FIC-900/AI1/OUT"},
                           name="expr_x")
y = engine.bind_expression("expr_x * 2", {"expr_x": "expr_x"},
                           name="expr_y")
check("expressions may chain acyclically",
      y.result.quality != Quality.BAD, y.result)
try:
    engine.bind_expression("expr_z + expr_y",
                           {"expr_z": "expr_z", "expr_y": "expr_y"},
                           name="expr_z")
    check("a cyclic expression binding is rejected at bind time", False)
except BindingError as error:
    check("a cyclic expression binding is rejected at bind time",
          "cyclic" in str(error) and "expr_z" in str(error), error)

# Bad input -> Bad output, never a number.
ai.outputs["OUT"].status = Quality.BAD
engine.poll()
check("an expression over a Bad input is Bad, never a number",
      x.result.quality == Quality.BAD)
ai.outputs["OUT"].status = Quality.GOOD

# --------------------------------------------------- CONFIG path reads
cfg = engine.bind("{p}/CONFIG/scale_hi", {"p": "FIC-900/AI1"})
check("a CONFIG path binds — the §6 path scheme's config leg",
      cfg.result.value == 200.0
      and cfg.result.quality == Quality.GOOD, cfg.result)
missing = engine.bind("{p}/CONFIG/NO_SUCH_PARAM", {"p": "FIC-900/AI1"})
check("an unknown config parameter is Bad, not zero",
      missing.result.quality == Quality.BAD)
cfg_write = engine.write("FIC-900/AI1/CONFIG/HI_LIM", "123.5")
check("a declared CONFIG parameter is online configurable without a role "
      "gate", cfg_write.success
      and ai.config.params["HI_LIM"] == 123.5,
      (cfg_write, ai.config.params.get("HI_LIM")))
bad_choice = engine.write(
    "FIC-900/AI1/CONFIG/L_TYPE", "not-a-linearization")
check("online tuning validates the block's declared choices",
      not bad_choice.success
      and ai.config.params.get("L_TYPE") != "not-a-linearization",
      bad_choice)
engine.unbind(cfg)
engine.unbind(missing)

# ------------------------------------------------------------- teardown
for b in (binding, forced_binding, dev, x, y):
    engine.unbind(b)
check("teardown returns the monitored count to baseline",
      engine.monitored_count == baseline, engine.monitored_count)

# ----------------------------------------------------- 500 bindings, 100 ms
many = [engine.bind("{p}/OUT" if i % 2 else "{p}/PV_D",
                    {"p": "FIC-900/AI1"}) for i in range(500)]
samples_ms = []
for attempt in range(3):
    # Wall-clock checks use best-of-N so a neighbouring process cannot turn
    # one scheduler stall into a release failure. Change the value each time
    # so all three batches exercise notification work rather than a no-op poll.
    ai.outputs["OUT"].value = float(attempt + 1)
    start = time.perf_counter()
    engine.poll()
    samples_ms.append((time.perf_counter() - start) * 1000)
elapsed_ms = min(samples_ms)
check(f"500 simultaneous bindings poll inside a frame "
      f"({elapsed_ms:.1f} ms)", elapsed_ms < 100, elapsed_ms)
for b in many:
    engine.unbind(b)

check("the engine imported no Qt (I2)", "PySide6" not in sys.modules)

print()
if failures:
    print(f"{len(failures)} binding-engine check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All binding-engine checks passed.")
