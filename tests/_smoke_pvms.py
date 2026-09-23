"""Phase 3 of the type-driven HMI builder — the PVM class system, proven.

From HMI_BUILDER_PROPOSAL_revB.md §10/§14 and PVM_CLASS_CATALOG.md:

- a PVM class declaring a second parameter fails at registration —
  unless it declares a documented EXCEPTION (the cascade pair);
- a PVM accepts fill/line appearance but refuses process-data overrides (I7);
- the §6 display-state matrix: every state renders the right marker,
  token, and value text — dashes, never a number, under Bad quality;
  the forced badge is present in every state it applies to;
- dynamo and faceplate share ONE state resolution (I6);
- classes bind through the Phase 2 engine against live blocks.

Run:  D:\\development\\GitHub\\vpy\\Scripts\\python.exe tests/_smoke_pvms.py
"""
from __future__ import annotations

import importlib.abc
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class _QtBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, name, *args, **kwargs):
        if name == "PySide6" or name.startswith("PySide6."):
            raise ImportError("PySide6 blocked — PVM declarations must be "
                              "Qt-free (I2)")


sys.meta_path.insert(0, _QtBlocker())

import azeo_control_trainer.core.strategy.blocks  # noqa: F401,E402
from azeo_control_trainer.core.hmi.binding import (  # noqa: E402
    BindingEngine, LiveGraphSource,
)
from azeo_control_trainer.core.hmi.pvms import (  # noqa: E402
    BAD_VALUE_TEXT, PvmClass, registry, resolve_state,
)
from azeo_control_trainer.core.hmi.pvms.base import Bind  # noqa: E402
from azeo_control_trainer.core.hmi.binding.result import (  # noqa: E402
    BindingResult,
)
from azeo_control_trainer.core.hmi.pvms.control import (  # noqa: E402
    CascadePairFaceplate, PIDCompact, PIDFaceplate,
)
from azeo_control_trainer.core.strategy.model.block_registry import (  # noqa: E402
    BlockRegistry,
)
from azeo_control_trainer.core.strategy.model.strategy_graph import (  # noqa: E402
    StrategyGraph,
)
from azeo_control_trainer.core.strategy.model.terminal import (  # noqa: E402
    LimitStatus, Quality,
)

failures: list[str] = []


def check(label: str, ok: bool, detail="") -> None:
    if ok:
        print(f"[ok]   {label}")
    else:
        failures.append(label)
        print(f"[FAIL] {label} {detail}")


# ----------------------------------------------------------- registration
classes = registry.all_classes()
check(f"the shipped PVM classes registered ({len(classes)})",
      len(classes) >= 12, sorted(classes))
pid_roles = registry.for_block_type("PID")
check("PID carries all four roles",
      set(pid_roles) == {"dynamo_compact", "dynamo_inline", "faceplate",
                         "detail"}, sorted(pid_roles))
exceptions = sorted(c.__name__ for c in registry.exceptions())
expected_exceptions = sorted([
    "CascadePairFaceplate", "PassBalanceFaceplate",
    "VFDSpeedPvm", "VFDSpeedFaceplate", "TurbineSpeedPvm", "TurbineSpeedFaceplate",
    "CompressorSpeedPvm", "CompressorSpeedFaceplate",
])
check("only the documented cascade, balance and paired machine views have multiple tags",
      exceptions == expected_exceptions, exceptions)
report = registry.registration_report()
check("the startup registration log counts classes and lists every "
      "exception",
      report[0].startswith("registered")
      and sum(1 for line in report if line.startswith("exception")) == len(expected_exceptions)
      and report[-1] == "data overrides  0 (invariant I7 holds)", report)

# --------------------------------------------- §10.2 single-parameter rule
try:
    class TwoParamPvm(PvmClass):
        block_type = "AI"
        role = "faceplate"
        PARAMS = ("path", "range_hi")
    check("a PVM class declaring a second parameter fails at definition",
          False)
except TypeError as error:
    check("a PVM class declaring a second parameter fails at definition",
          "exactly one parameter" in str(error), error)

check("the cascade pair is allowed its two paths — because it says why",
      CascadePairFaceplate.PARAMS == ("master", "slave")
      and bool(CascadePairFaceplate.EXCEPTION))

try:
    @__import__("azeo_control_trainer.core.hmi.pvms.base",
                fromlist=["register_pvm"]).register_pvm
    class DuplicatePID(PvmClass):
        block_type = "PID"
        role = "faceplate"
    check("a second class for the same type+role is refused", False)
except ValueError as error:
    check("a second class for the same type+role is refused",
          "already registered" in str(error))

# ----------------------------------------- appearance versus data overrides
faceplate = PIDFaceplate()
pvm = faceplate.place("pvm_1", standard="hphmi.controller",
                      x=10, y=10, w=176, h=94,
                      fill="#d8e0e7", line="#425b70",
                      path="FIC-900/PID1")
check("a PVM carries class, path, standard, appearance and rectangle",
      pvm.params == {"path": "FIC-900/PID1"}
      and pvm.to_dict()["class"] == "PID/faceplate"
      and pvm.to_dict()["fill"] == "#d8e0e7"
      and pvm.to_dict()["line"] == "#425b70")
try:
    faceplate.place("pvm_2", path="FIC-900/PID1", color="orange")
    check("a per-instance process override is refused by name (I7)", False)
except TypeError as error:
    check("a per-instance process override is refused by name (I7)",
          "I7" in str(error) and "color" in str(error), error)
try:
    pvm.x = 999
    check("a placed PVM is frozen", False)
except Exception:
    check("a placed PVM is frozen", True)

# --------------------------------------- §6 / §14.6 display-state matrix
def result(**kw):
    base = dict(value=42.0, quality=Quality.GOOD,
                limit=LimitStatus.NOT_LIMITED, units="gpm")
    base.update(kw)
    return BindingResult(**base)


s = resolve_state(result(quality=Quality.BAD, value=None))
check("Bad: hatched circle, dashes — never a number",
      s.name == "bad" and s.marker == "hatched_circle"
      and s.value_text == BAD_VALUE_TEXT and s.show_last_good, s)
s = resolve_state(result(alarm_active=True, alarm_priority=15))
check("Alarm: filled square, priority token, Acknowledge as primary",
      s.name == "alarm" and s.marker == "square"
      and s.token == "signal.critical"
      and s.primary_action == "ACKNOWLEDGE", s)
s = resolve_state(result(limit=LimitStatus.HIGH_LIMITED))
check("Limited: triangle, warning token",
      s.name == "warning" and s.marker == "triangle"
      and s.token == "signal.warning", s)
s = resolve_state(result(mode_target="AUTO", mode_actual="MAN"))
check("Mode mismatch: hold token, TARGET · ACTUAL",
      s.name == "mode_mismatch" and s.mode_text == "AUTO · MAN"
      and s.token == "signal.hold", s)
s = resolve_state(result(quality=Quality.UNCERTAIN))
check("Uncertain: hollow marker, dimmed",
      s.name == "uncertain" and s.marker == "hollow", s)
s = resolve_state(result())
check("Normal: no colour anywhere",
      s.name == "normal" and s.marker == "" and s.token == ""
      and s.value_text == "42 gpm", s)
forced_states = [resolve_state(result(forced=True)),
                 resolve_state(result(forced=True, quality=Quality.BAD)),
                 resolve_state(result(forced=True, alarm_active=True,
                                      alarm_priority=11))]
check("Forced is additive — carried in EVERY state it applies to (I5)",
      all(st.forced for st in forced_states),
      [(st.name, st.forced) for st in forced_states])
check("priority order: Bad beats an active alarm",
      resolve_state(result(quality=Quality.BAD, alarm_active=True,
                           alarm_priority=15)).name == "bad")

# --------------------------------------------- I6: one resolution, shared
check("dynamo and faceplate consume the SAME state resolution (I6)",
      PIDCompact.resolve_state is PIDFaceplate.resolve_state
      is resolve_state)

# ---------------------------------------------- binding through the engine
graph = StrategyGraph(name="FIC-900")
pid_block = BlockRegistry().create("PID", "PID1")
pid_block._apply_config()
graph.add_block(pid_block)
engine = BindingEngine(LiveGraphSource(lambda: {"FIC-900": graph}))

bound = faceplate.bind_all(engine, {"path": "FIC-900/PID1"})
pid_block.outputs["PV"].value = 55.0
pid_block.outputs["SP"].value = 50.0
engine.poll()
check("the faceplate binds a live PID by path alone",
      bound["pv.value"].result.value == 55.0, bound["pv.value"].result)
check("the derived deviation evaluates over sibling binds",
      bound["deviation"].result.value == 5.0, bound["deviation"].result)
check("prop binds resolve as metadata of the same path",
      bound["pv.units"][0] == "prop"
      and bound["pv.units"][1] == "FIC-900/PID1/PV")

cascade = CascadePairFaceplate()
pair = cascade.bind_all(engine, {"master": "FIC-900/PID1",
                                 "slave": "FIC-900/PID1"})
check("the exception class binds both its paths",
      pair["master.pv"].result.value == 55.0
      and pair["slave.pv"].result.value == 55.0)

# -------------------------------------------- 7.4 classes, now present
for block_type in ("MIN_SELECT", "MAX_SELECT", "RATIO", "TOTALIZER"):
    check(f"{block_type} has its 7.4 faceplate class",
          registry.get(block_type, "faceplate") is not None)

# Pass balance: four paths, mean and spread as relationships.
from azeo_control_trainer.core.hmi.pvms.composite import (  # noqa: E402
    PassBalanceFaceplate,
)

ai_blocks = {}
for letter, value in (("a", 10.0), ("b", 12.0), ("c", 11.0),
                      ("d", 15.0)):
    block = BlockRegistry().create("AI", f"AI_{letter.upper()}")
    block._apply_config()
    block.outputs["OUT"].value = value
    graph.add_block(block)
    ai_blocks[letter] = block
balance = PassBalanceFaceplate().bind_all(
    engine, {letter: f"FIC-900/AI_{letter.upper()}"
             for letter in "abcd"})
engine.poll()
check("pass balance derives the mean across its four paths",
      balance["mean"].result.value == 12.0, balance["mean"].result)
check("and the spread — the relationship no single faceplate shows",
      balance["spread"].result.value == 5.0, balance["spread"].result)

# ------------------------------------- sheet 05: publish & revert, Qt-free
import tempfile as _tempfile

from azeo_control_trainer.core.hmi.pvms.publishing import (  # noqa: E402
    DisplayLocked, DisplayStore, PvmDisplay, PublishRefused,
)

with _tempfile.TemporaryDirectory() as tmp:
    store = DisplayStore(tmp)
    store.acquire_lock("Overview", who="n.islam")
    try:
        store.acquire_lock("Overview", who="a.rahman")
        check("a second editor is refused — single-writer per display",
              False)
    except DisplayLocked as error:
        check("a second editor is refused — single-writer per display",
              "n.islam" in str(error), error)
    store.release_lock("Overview", who="n.islam")

    display = PvmDisplay(name="Overview")
    check("new display documents begin at L1 and persist that level "
          "explicitly", display.level == 1
          and display.to_dict()["level"] == 1)
    check("legacy documents that omitted the old default remain L2",
          PvmDisplay.from_dict({"display": "Legacy", "pvms": []}).level
          == 2)
    display.pvms = [PIDFaceplate().place(
        "pvm_1", standard="hphmi.controller", x=100, y=100,
        path="FIC-900/PID1").to_dict()]
    store.save_draft(display)
    check("saving is not publishing — a draft leaves no history",
          store.history("Overview") == [])

    entry1 = store.publish(display, env="TEST", by="n.islam",
                           resolve=lambda p: True)
    display.pvms[0]["x"] = 220.0
    display.pvms.append(PIDFaceplate().place(
        "pvm_2", x=50, y=50, path="FIC-900/PID9").to_dict())
    entry2 = store.publish(display, env="PROD", by="n.islam",
                           resolve=lambda p: True)
    diff_text = " | ".join(entry2["diff"])
    check("the publish diff speaks display terms",
          "+ PVM" in diff_text and "~ moved" in diff_text, diff_text)

    try:
        store.publish(display, resolve=lambda p: "PID9" not in p)
        check("an unresolved binding path blocks the publish", False)
    except PublishRefused as error:
        check("an unresolved binding path blocks the publish",
              "PID9" in str(error), error)

    reverted = store.revert("Overview", entry1["rev"], by="n.islam")
    check("revert is one action and history stays append-only",
          reverted["reverted_from"] == entry1["rev"]
          and len(store.history("Overview")) == 3
          and len(store.load_draft("Overview").pvms) == 1)

# ------------- the PVM Configuration Designer's property model
# (Working with PVMs in Azeo Operator Station: Selection + Presence + Present
# Online, exercised on the paper's own figures.)
from azeo_control_trainer.azeo_graphics_designer.configurator import (
    PvmConfiguration, PvmProperty, PropertyGroup,
)
from azeo_control_trainer.core.hmi.pvms.configurator.samples import (
    sample_configurations,
)
from azeo_control_trainer.core.hmi.pvms.configurator.model import (
    Option as _CfgOption,
)

_cfgs = sample_configurations()
_hp = _cfgs["HP_C_Valve"]
check("a Selection resolves the named choice to its subproperty - "
      "'Left', never 270",
      _hp.subvalue("Orientation.BodyRot",
                   {"Orientation": "Left"}) == "270")
check("a subproperty may reference another property (Pvm.CustomAngle)",
      _hp.subvalue("OrientationExtended.BodyRot",
                   {"OrientationExtended": "Custom",
                    "CustomAngle": "37"}) == "37")
check("and that option's EnableCustom column gates the angle's "
      "Presence",
      _hp.is_present("CustomAngle",
                     {"OrientationExtended": "Custom"})
      and not _hp.is_present("CustomAngle",
                             {"OrientationExtended": "Up"}))
check("ShowTickMarks is present only while ShowBar shows",
      _hp.is_present("ShowTickMarks", {})
      and not _hp.is_present("ShowTickMarks", {"ShowBar": "Hide"}))
check("the Link selection swaps FB/Parameter/Scale together, and a "
      "DLSYS expression passes through untouched",
      _hp.subvalue("Link.Parameter", {"Link": "AO1"}) == "PV"
      and "Pvm.Link.FB" in _hp.subvalue("Link.Scale", {}))
_v3 = _cfgs["PCSD_3WCtrlValve_v01_"]
check("Port exists only on a three-way valve (Presence)",
      not _v3.is_present("Port", {})
      and _v3.is_present("Port", {"ValveType": "Three way"}))
check("resolved() drops absent properties entirely - not greyed, "
      "absent",
      "ShowTickMarks" not in _hp.resolved({"ShowBar": "Hide"}))
check("Present Online off means the group never subscribes",
      "Tag" in _v3.online_references({})
      and "Parameter1" not in _v3.online_references({}))
from pathlib import Path as _CfgPath
_cfg_root = _CfgPath(_tempfile.mkdtemp())
_cfg_path = _hp.save(_cfg_root)
_hp2 = PvmConfiguration.load(_cfg_path)
check("a configuration document round-trips byte-identically",
      _hp2.to_dict() == _hp.to_dict()
      and _hp2.save(_cfg_root).read_bytes()
      == _cfg_path.read_bytes())

_dot_cfg = PvmConfiguration("DottedNames", [
    PropertyGroup("Basic", [
        PvmProperty("pv.value", "Selection", default="Primary",
                    columns=["Units"],
                    options=[_CfgOption("Primary", ["psi"])]),
        PvmProperty("Caption", default="Pvm.pv.value.Units"),
    ])])
check("configuration references use longest-match property names, so a "
      "dotted property remains addressable",
      _dot_cfg.subvalue("pv.value.Units", {}) == "psi"
      and not _dot_cfg.issues())
check("renaming a property moves every exact PVM reference with it",
      _dot_cfg.rename_property("pv.value", "process.value")
      and _dot_cfg.property("Caption").default
      == "Pvm.process.value.Units"
      and not _dot_cfg.issues())
_broken_cfg = PvmConfiguration("Broken", [
    PropertyGroup("Basic", [PvmProperty("Same")]),
    PropertyGroup("Other", [PvmProperty("Same")]),
])
check("commercial preflight reports duplicate configuration fields and "
      "refuses to persist them",
      any("duplicated" in issue.message for issue in _broken_cfg.issues()))
try:
    _broken_cfg.save(_cfg_root)
    check("invalid configuration cannot be saved", False)
except ValueError:
    check("invalid configuration cannot be saved", True)

# ------------- configuration-aware binding (the research's gaps 1+2)
from azeo_control_trainer.core.hmi.binding import (
    BindingEngine as _CfgEngine, BindingError as _CfgBindingError,
    LiveGraphSource as _CfgSource,
)
from azeo_control_trainer.core.hmi.pvms.base import Bind as _CfgBind
from azeo_control_trainer.core.hmi.pvms.configurator.live import (
    bind_pvm as _bind_pvm, rebind_pvm as _rebind_pvm,
)

_ceng = _CfgEngine(_CfgSource(lambda: {}))
_cchoices = {"Tag": "FIC-101", "Link": "AO1"}
_cbind = _ceng.bind("{Tag}/{Link.FB}/OUT_SCALE",
                    _hp.binding_params(_cchoices))
check("a template composes its path from configuration properties, "
      "dotted subproperties included",
      _cbind.path == "FIC-101/AO1/OUT_SCALE", _cbind.path)
try:
    _ceng.bind("{Tag}/{Lnik.FB}/OUT", _hp.binding_params(_cchoices))
    check("a typo in a placeholder still refuses at bind time", False)
except _CfgBindingError as error:
    check("a typo in a placeholder still refuses at bind time",
          "Lnik" in str(error), error)
_ceng.unbind(_cbind)

_cspecs = (_CfgBind("out", "{Tag}/FB1/OUT"),
           _CfgBind("port", "{Tag}/FB1/PORT{Port.OpenPort}"),
           _CfgBind("chart", "{Tag}/{Parameter1}"))
_cbound, _cgated = _bind_pvm(_ceng, _cspecs, _v3,
                             {"Tag": "XV-101"})
check("a spec touching a Presence-off property is never bound - "
      "gated, not Bad",
      set(_cbound) == {"out"} and set(_cgated) == {"port", "chart"}
      and _ceng.monitored_count == 1,
      (sorted(_cbound), _cgated, _ceng.monitored_count))
_cbound, _cgated = _rebind_pvm(
    _ceng, _cbound, _cspecs, _v3,
    {"Tag": "XV-101", "ValveType": "Three way"})
check("flipping the Selection rebinds - Port comes alive with its "
      "resolved cell in the path",
      _cbound["port"].path == "XV-101/FB1/PORT3"
      and "chart" in _cgated and _ceng.monitored_count == 2,
      ({k: b.path for k, b in _cbound.items()}, _cgated))
for _b in _cbound.values():
    _ceng.unbind(_b)
check("Present Online keeps the offline group's spec gated in every "
      "plan (the subscription lever)",
      _ceng.monitored_count == 0)

# ------------- Standard.<Name> references: late, live resolution
from azeo_control_trainer.core.hmi.pvms.configurator.model import (
    is_standard_ref, resolve_standard_refs,
)

_lib = {"S_ValveBody": "#4472C4"}
_vals = {"Color": "Standard.S_ValveBody", "Angle": "270",
         "Missing": "Standard.S_Nope"}
_res = resolve_standard_refs(_vals, _lib)
check("a Standard.<Name> value resolves through the library at "
      "render time; literals pass through",
      _res["Color"] == "#4472C4" and _res["Angle"] == "270"
      and is_standard_ref("Standard.S_X")
      and not is_standard_ref("#FFF"))
check("an unknown standard keeps its reference text - visible, "
      "never an invented literal",
      _res["Missing"] == "Standard.S_Nope")
_lib["S_ValveBody"] = "#AA0000"
check("editing the standard changes the resolution with the class "
      "untouched - the no-republish asymmetry",
      resolve_standard_refs(_vals, _lib)["Color"] == "#AA0000")

# ------------- closure of the manual's reusable-library gaps
from azeo_control_trainer.core.hmi.binding.alarm_state import (  # noqa: E402
    RuntimeAlarmRegistry,
)
from azeo_control_trainer.core.hmi.binding.source import (  # noqa: E402
    indexed_value, split_parameter_path,
)
from azeo_control_trainer.core.hmi.pvms.configuration import (  # noqa: E402
    ConfigurationLibraryStore, InstalledItemError, InstalledItems,
    complexity_report, find_replace,
)
from azeo_control_trainer.core.hmi.pvms.functions import (  # noqa: E402
    FunctionStore, LOGIC_RULE,
)
from azeo_control_trainer.core.hmi.pvms.hp.classes import (  # noqa: E402
    HP_CLASS_KEYS,
)
from azeo_control_trainer.core.hmi.pvms.hp.fb_classes import (  # noqa: E402
    ADVANCED_CONTROL_KEYS, CEM_EFFECT_KEYS, VOTER_PVM_KEYS,
)
from azeo_control_trainer.core.hmi.pvms.instances import (  # noqa: E402
    TemplateStore,
)
from azeo_control_trainer.core.hmi.pvms.layout import (  # noqa: E402
    Layout, LayoutStore, Screen,
)
from azeo_control_trainer.core.hmi.pvms.user_library import (  # noqa: E402
    UserPvmLibrary,
)

with _tempfile.TemporaryDirectory() as _library_tmp:
    _fn_store = FunctionStore(_library_tmp)
    _fn_store.add_formula(
        "Ratio", [{"name": "A", "type": "Number"},
                  {"name": "B", "type": "Number", "default": 1}],
        calculations=[{"name": "R",
                       "expression": "A / max(B, 1)"}],
        expression="R")
    _fn_store.add_formula(
        "RatioState", [{"name": "A", "type": "Number"},
                       {"name": "B", "type": "Number"}],
        calculations=[{"name": "R", "expression": "A / max(B, 1)"}],
        rules=[{"when": "R >= 2", "value": "HIGH"}],
        default="NORMAL", logic=LOGIC_RULE, return_type="String")
    check("typed functions take several inputs and ordered calculations",
          _fn_store.eval("Ratio", {"A": 10, "B": 4}) == 2.5)
    check("rule logic is distinct from value expression logic",
          _fn_store.eval("RatioState", {"A": 10, "B": 4}) == "HIGH"
          and _fn_store.eval("RatioState", {"A": 3, "B": 4})
          == "NORMAL")

    _templates = TemplateStore(_library_tmp)
    _templates.add("Unit", "display",
                   {"display": "Template", "items": [{"kind": "text"}]})
    _templates = TemplateStore(_library_tmp)
    _instantiated = _templates.instantiate("Unit", "Unit 2")
    check("project display templates persist beside the protected built-ins "
          "and instantiate as detached copies",
          _instantiated["display"] == "Unit 2"
          and "Unit" in _templates.names("display")
          and not _templates.is_builtin("Unit"))

    _user_pvms = UserPvmLibrary(_library_tmp)
    _user_pvms.add("Child", [{"kind": "rect", "id": "child",
                              "x": 0, "y": 0, "w": 10, "h": 10}],
                   folder="Valves")
    _user_pvms.add("Parent", [{"kind": "rect", "id": "parent",
                               "x": 0, "y": 0, "w": 20, "h": 20}],
                   folder="Units")
    _user_pvms.add_nested("Parent", "Child", x=25, y=5)
    _nested = _user_pvms.instantiate(
        "Parent", 100, 200, link="unlinked", unlink_nested=True)
    check("library folders, nested classes and unlink-subtree placement "
          "share one persisted user-PVM model",
          _user_pvms.folders() == ("Units", "Valves")
          and len(_nested) == 2
          and all(row.get("pvm_link") == "unlinked" for row in _nested))

    _layout_store = LayoutStore(_library_tmp)
    _layout_store.save_layout(Layout("Two screens", [Screen("A"),
                                                       Screen("B")]))
    DisplayStore(_library_tmp).save_draft(PvmDisplay(
        "Overview", pvms=[PIDFaceplate().place(
            "cfg_pvm", path="FIC-900/PID1").to_dict()]))
    _package_file = _CfgPath(_library_tmp) / "package.json"
    _config_libraries = ConfigurationLibraryStore(_library_tmp)
    _config_libraries.export_package(_package_file)
    _config_libraries.import_package(_package_file,
                                     library_name="Replica")
    _replica = _config_libraries.library_root("Replica")
    check("configuration export/import carries displays, layouts, "
          "functions, templates and PVM classes into a named library",
          (_replica / "Overview" / "draft.json").exists()
          and (_replica / "_layouts.json").exists()
          and (_replica / "_functions.json").exists()
          and (_replica / "_templates.json").exists()
          and (_replica / "_library" / "user_pvms.json").exists())
    _replacements = find_replace(
        _replica, "FIC-900", "FIC-901", apply=True)
    check("Find/Replace searches every editable graphics document",
          any(row.occurrences for row in _replacements))
    check("the project-wide complexity report measures every draft",
          [row["display"] for row in complexity_report(_replica)]
          == ["Overview"])

    _protected = InstalledItems(_replica)
    _protected.protect("display", "Overview")
    try:
        DisplayStore(_replica).save_draft(PvmDisplay("Overview"))
        check("installed displays refuse in-place modification", False)
    except InstalledItemError:
        check("installed displays refuse in-place modification", True)

_normalized, _indexes = split_parameter_path(
    "AREA_A%FIC-900/PID1/TABLE[1][0].CV")
_indexed, _index_ok = indexed_value([[1], [7]], _indexes)
check("Azeo zone prefixes and integer array subscripts resolve without "
      "changing the module/block/parameter grammar",
      _normalized == "FIC-900/PID1/TABLE.CV"
      and _indexes == (1, 0) and _index_ok and _indexed == 7)

_alarms = RuntimeAlarmRegistry()
_alarms.observe("FIC-900", "PID1", [("HI", 11, 80.0)])
_alarm_key = "FIC-900/PID1/HI"
_alarms.acknowledge((_alarm_key,))
_alarms.suppress(_alarm_key)
_alarm_summary = _alarms.block_summary("FIC-900", "PID1")
check("one alarm registry carries acknowledgement and suppression into "
      "every binding consumer",
      _alarm_summary["suppressed"] and _alarm_summary["acked"]
      and not _alarm_summary["active"] and _alarm_summary["count"] == 1)

_cp_cfg = PvmConfiguration("Pump")
check("class connection points are named, normalized and persistent",
      _cp_cfg.add_connection_point(1.5, -1.0) == "cp1"
      and _cp_cfg.connection_points == [{"name": "cp1",
                                         "x": 1.0, "y": 0.0}]
      and PvmConfiguration.from_dict(_cp_cfg.to_dict()).connection_points
      == _cp_cfg.connection_points)

check("the full installed catalogues are concrete registry entries",
      len(HP_CLASS_KEYS) == 28
      and len(VOTER_PVM_KEYS) == 34
      and len(CEM_EFFECT_KEYS) == 16
      and set(ADVANCED_CONTROL_KEYS)
      == {"INSPECT", "LAB", "MPC", "MPCPlus", "MPCPro"}
      and all(registry.get(*key) is not None
              for key in (*HP_CLASS_KEYS.values(),
                          *VOTER_PVM_KEYS.values(),
                          *CEM_EFFECT_KEYS.values(),
                          *ADVANCED_CONTROL_KEYS.values())))

check("the PVM layer imported no Qt (I2)", "PySide6" not in sys.modules)

print()
if failures:
    print(f"{len(failures)} PVM check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print(f"All PVM-class checks passed ({len(classes)} classes registered).")
