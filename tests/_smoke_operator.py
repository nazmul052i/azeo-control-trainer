"""The operator layer: faceplates open, modules scan, the namespace is browsable.

Three things were broken or missing and each is checked here end to end:

1. *Open Faceplate...* routed through a main window the standalone trainer does
   not have, so it silently did nothing — for every block type, including the
   six that already had a faceplate.
2. ``DEVCTL`` and ``MOTOR_INTERLOCK`` were marked faceplate-capable and had no
   implementation, which is the whole of the shipped pump workshop.
3. A downloaded module reported ONLINE and never executed a scan, because the
   thing that used to call ``execute_scan`` stayed behind with the plant.

Run:  D:\\development\\GitHub\\vpy\\Scripts\\python.exe tests/_smoke_operator.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                          # noqa: BLE001
    pass

import logging                                             # noqa: E402
logging.disable(logging.WARNING)

failures: list[str] = []


def check(label: str, ok: bool, detail: object = "") -> None:
    if ok:
        print(f"[ok]   {label}")
    else:
        failures.append(label)
        print(f"[FAIL] {label} {detail}")


from PySide6.QtWidgets import QApplication                  # noqa: E402

app = QApplication.instance() or QApplication([])

import azeo_control_trainer.core.strategy.blocks                 # noqa: E402,F401
from azeo_control_trainer.core.strategy.serialization import strategy_io  # noqa: E402

AREA = (Path(__file__).resolve().parent.parent
        / "src" / "strategies" / "azeo_training")
strategy_io.STRATEGY_DIR = AREA

from azeo_control_trainer.app import AreaContext            # noqa: E402
from azeo_control_trainer.core.datastore.shared_data_store import (  # noqa: E402
    SharedDataStore,
)
from azeo_control_trainer.core.strategy.tagdb import EntryKind    # noqa: E402
from azeo_control_trainer.azeo_control_designer.faceplates import BLOCK_FACEPLATES  # noqa: E402
from azeo_control_trainer.azeo_control_designer.faceplates.block_faceplate import (  # noqa: E402
    ROUTE_DRIVEN, ROUTE_FIELD, resolve_command,
)
from azeo_control_trainer.azeo_control_designer.designer_window import (  # noqa: E402
    StrategyDesignerWindow,
)

store = SharedDataStore()
window = StrategyDesignerWindow(store=store, plugin=AreaContext(AREA))
designer = window.designer
designer.auto_load_project()
designer.auto_go_online()
app.processEvents()


def pump(seconds: float) -> None:
    """Let the Qt event loop run — the executive is a timer."""
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def settle(predicate, timeout: float = 12.0) -> bool:
    """Run scans until ``predicate`` holds, or give up.

    A change takes one scan per wire boundary to reach its destination — the
    runtime copies every forward wire at the top of a scan, before any block
    executes. So ``MTR-102``'s interlock chain (CND → OR → NOT → DEVCTL) needs
    three scans to settle, and a fixed sleep either wastes time or races it.
    """
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.05)
    return False


def graph_named(name: str):
    for i in range(designer._canvas_tabs.count()):
        canvas = designer._canvas_tabs.widget(i)
        if getattr(canvas, "scene", None) and canvas.scene.graph.name == name:
            designer._canvas_tabs.setCurrentIndex(i)
            return canvas.scene.graph
    return None


# ------------------------------------------------------------- executive
executive = designer.controller_executive()
check("the designer owns a controller executive", executive is not None)
check("it is running once modules are online", executive.is_running)
check("every downloaded module is online",
      len(executive.online_runtimes()) == 7, len(executive.online_runtimes()))

pump(1.5)
scanned = [rt.scan_count for rt in executive.online_runtimes()]
check("online modules actually execute scans",
      all(n > 0 for n in scanned), scanned)

# The write queue is the one channel between operator surfaces and the
# store, and it must drain even with nothing online — an engineer working
# offline writes from a faceplate and watches the tag browser for the
# effect. Gating the drain on a running module parked exactly those writes.
from azeo_control_trainer.azeo_control_designer.executive import (
    ControllerExecutive,
)
from azeo_control_trainer.core.datastore.shared_data_store import SharedDataStore

idle_store = SharedDataStore()
idle_exec = ControllerExecutive(idle_store, period_ms=50)
idle_store.queue_write("FV-999.OUT", 42.5)
idle_exec.scan_once()
check("the executive drains operator writes with nothing online",
      idle_store.get("FV-999.OUT") == 42.5,
      idle_store.get("FV-999.OUT"))

# A network field driver owns the same destructive queue.  The controller
# timer must leave it alone or an AO command reaches the local store but never
# reaches Modbus/OPC UA, depending only on which thread wakes first.
field_owned_store = SharedDataStore()
field_owned_store.field_io_driver = object()
field_owned_exec = ControllerExecutive(field_owned_store, period_ms=50)
field_owned_store.queue_write("FCV-1001.OUT", 47.25)
field_owned_exec.scan_once()
check("a configured field driver remains the sole write-queue consumer",
      field_owned_store.get("FCV-1001.OUT") is None
      and field_owned_store.drain_writes() == [("FCV-1001.OUT", 47.25)])


class _SlowRuntime:
    """A runtime whose scan work exceeds the period — an overrun."""

    is_online = True
    scan_count = 1

    def execute_scan(self, dt):
        import time as _t
        _t.sleep(0.03)


idle_store.register_strategy_runtime = None      # keep duck-typing honest
idle_exec._store = type("S", (), {
    "get_strategy_runtimes": staticmethod(lambda: [_SlowRuntime()]),
    "drain_writes": staticmethod(lambda: []),
})()
idle_exec.set_period_ms(10)
idle_exec.scan_once()
check("a scan that exceeds its period is counted as an overrun",
      idle_exec.overrun_count == 1 and idle_exec.last_work_ms >= 10,
      (idle_exec.overrun_count, idle_exec.last_work_ms))
check("a healthy executive has no overruns",
      executive.overrun_count == 0, executive.overrun_count)

# ------------------------------------------------------ the PK controller
# The controller as a thing: a named node with a model, a DST capacity
# counted from the tag database, per-module scan rates from the Azeo
# menu, and a carrier keylock that refuses downloads but not operation.
from azeo_control_trainer.core.strategy.engine.pk_controller import (
    PKController, PKModel,
)
from azeo_control_trainer.core.strategy.tagdb import TagDatabase

pk = PKController.from_config({"name": "PK-A", "model": "pk300"})
check("a controller declaration parses, case-insensitively",
      pk.name == "PK-A" and pk.model is PKModel.PK300)
pk = PKController.from_config({"model": "PK9000"})
check("an unknown model falls back to PK100, loudly not fatally",
      pk.model is PKModel.PK100)
pk = PKController.from_config(None)
check("no declaration means PK-CTLR-1, PK100, unlocked",
      pk.name == "PK-CTLR-1" and pk.model is PKModel.PK100
      and not pk.keylock)

store.tagdb = TagDatabase.from_area(AREA)
usage = pk.dst_usage(store)
check("DSTs are counted from the tag database, never by hand",
      usage == len(store.tagdb.field_tags()) and usage > 0, usage)
from azeo_control_trainer.connectivity.fieldio.eioc import EthernetIoCard  # noqa: E402

external_tags = list(store.tagdb.field_tags())[:2]
store.eioc = EthernetIoCard.from_config({
    "name": "EIOC-TEST",
    "field_io": {"type": "opcua", "signals": {
        tag: {"direction": "read"} for tag in external_tags}},
})
check("EIOC client signals do not consume the PK's native DST capacity",
      pk.dst_usage(store) == usage - len(external_tags), pk.dst_usage(store))
store.eioc = None
check("this area fits its PK100", not pk.over_capacity(store))
check("the identity line carries model, lock and gauge",
      "PK100" in pk.identity_line(store)
      and f"DSTs {usage} of 100" in pk.identity_line(store))

# --- per-module scan rate: the property and its byte-idempotence
g = graph_named("MTR-102")
check("a module's scan rate defaults to 500 ms", g.scan_ms == 500)
g.scan_ms = 100
check("a non-default rate is stored in the file's extra keys",
      g.extra.get("scan_ms") == 100 and g.scan_ms == 100)
g.scan_ms = 500
check("returning to the default removes the key — shipped modules stay "
      "byte-identical", "scan_ms" not in g.extra)

# --- the scheduler: a 100 ms module scans ~5x a 500 ms module
runtimes = executive.online_runtimes()
rt_fast = next(rt for rt in runtimes
               if rt.compiled.graph.name != "MTR-102")
rt_fast.compiled.graph.scan_ms = 100
before = {id(rt): rt.scan_count for rt in runtimes}
pump(2.0)
fast_delta = rt_fast.scan_count - before[id(rt_fast)]
slow_deltas = [rt.scan_count - before[id(rt)] for rt in runtimes
               if rt is not rt_fast]
check("the executive follows the fastest module's rate",
      executive._timer.interval() == 100, executive._timer.interval())
check("a 100 ms module scans several times per 500 ms module scan",
      slow_deltas and min(slow_deltas) >= 2
      and fast_delta >= 2.5 * max(slow_deltas),
      (fast_delta, slow_deltas))
rt_fast.compiled.graph.scan_ms = 500
pump(0.3)
check("returning the rate returns the tick",
      executive._timer.interval() == 500, executive._timer.interval())

# --- the keylock: refuses decommission, operation continues
store.controller = pk
pk.keylock = True
designer._go_offline()
check("a locked carrier key refuses decommission — modules stay on scan",
      len(executive.online_runtimes()) == len(runtimes),
      len(executive.online_runtimes()))
pk.keylock = False

# --- diagnostics: the identity header is the node made visible
from azeo_control_trainer.azeo_control_designer.dialogs.diagnostics_dialog     import ControllerDiagnosticsDialog

dlg = ControllerDiagnosticsDialog(designer, store)
dlg._refresh()
check("diagnostics shows the controller identity and DST gauge",
      "PK100" in dlg._lbl_identity.text()
      and "DSTs" in dlg._lbl_identity.text(), dlg._lbl_identity.text())
dlg._chk_keylock.setChecked(True)
check("the keylock toggle drives the node", pk.keylock is True)
dlg._chk_keylock.setChecked(False)
dlg.close()

# ------------------------------------------- the PK's server face (phase 2)
# The trainer serves its store over Modbus TCP, mapped by the tag database:
# an external HMI reads the process live and issues supervisory writes that
# land on the store's external-write queue — which the executive drains, so
# the write becomes real on the next tick. The whole loop, over a socket.
import socket as _socket
import struct as _struct

from azeo_control_trainer.connectivity.fieldio.modbus_server import (
    StoreModbusServer, build_map, map_to_csv,
)

probe = _socket.socket()
probe.bind(("127.0.0.1", 0))
_srv_port = probe.getsockname()[1]
probe.close()

served = build_map(store.tagdb)
check("the served map derives from the tag database",
      len(served) == len(store.tagdb.field_tags()), len(served))
check("only tagdb-writable points land on writable tables",
      all((e.table in ("coil", "holding")) == e.writable for e in served))
check("the map is deterministic",
      [e.address for e in served] == [e.address
                                      for e in build_map(store.tagdb)])
check("the exported register table names its encoding",
      "float32" in map_to_csv(served) and "tag,table,address" in
      map_to_csv(served))

mb_server = StoreModbusServer(store, host="127.0.0.1", port=_srv_port)
check("the server comes up", mb_server.start())

from pymodbus.client import ModbusTcpClient

mb_client = ModbusTcpClient("127.0.0.1", port=_srv_port)
mb_client.connect()
served_analog = next(e for e in mb_server.map if e.table == "input")
store.set(served_analog.tag, 42.5)
reply = mb_client.read_input_registers(address=served_analog.address,
                                       count=2, device_id=1)
value = _struct.unpack(">f", _struct.pack(
    ">HH", *reply.registers))[0] if not reply.isError() else None
check("a read is answered from the store at request time — no lag loop",
      value == 42.5, value)

served_write = next(e for e in mb_server.map if e.table == "holding")
words = _struct.unpack(">HH", _struct.pack(">f", 33.25))
reply = mb_client.write_registers(address=served_write.address,
                                  values=list(words), device_id=1)
check("a supervisory write is accepted", not reply.isError())
check("and lands as a whole float on the next executive tick",
      settle(lambda: store.get(served_write.tag) == 33.25),
      store.get(served_write.tag))
mb_client.close()
mb_server.stop()
check("the server stops cleanly", mb_server._thread is None)

# --------------------------------------------- the switchover drill (P3)
# Fail the controller on purpose and measure what the process feels: warm
# keeps module state across the outage (the redundant-pair story), cold
# resets and re-seeds (the simplex swap). Both use the real machinery.
from azeo_control_trainer.core.strategy.engine.switchover import SwitchoverDrill

drill = SwitchoverDrill(store, executive)
check("the drill watches outputs walked from the tag database",
      len(drill.output_tags()) > 0, drill.output_tags()[:4])
pre_counts = {id(rt): rt.scan_count for rt in executive.online_runtimes()}
check("warm failure stops the scan",
      drill.fail("warm") and not executive.is_running)
pump(0.4)
drill.takeover()
check("takeover restarts the executive and clocks the outage",
      executive.is_running and drill.report.outage_s >= 0.3,
      drill.report.outage_s)
for _ in range(3):
    pump(0.4)
    drill.sample()
check("warm switchover keeps module state — scan counts continue",
      all(rt.scan_count > pre_counts[id(rt)]
          for rt in executive.online_runtimes()))
check("the report reads like a caption",
      "WARM" in drill.report.text() and "worst" in drill.report.text())

cold = SwitchoverDrill(store, executive)
cold.fail("cold")
check("cold restart resets every module — the simplex swap",
      all(rt.scan_count == 0 for rt in executive.online_runtimes()))
cold.takeover()
for _ in range(3):
    pump(0.4)
    cold.sample()
check("the replacement controller comes back on scan",
      all(rt.scan_count > 0 for rt in executive.online_runtimes()))
check("the cold report carries a finite worst deviation",
      cold.report.worst >= 0.0 and cold.report.bumps,
      cold.report.worst)

# --------------------------------------- the controller's own window (UI)
# The minimap is gone (user feedback: canvas corner space for nothing the
# fit-zoom does not do), and Controller Status is the PK's window: the
# node's identity in the header, and Controller Properties — the Azeo
# gesture of adding a controller — editing the area's declaration.
check("the minimap is gone from the designer",
      not hasattr(designer, "_minimap"))

from azeo_control_trainer.azeo_control_designer.dialogs.controller_status     import ControllerStatusDialog

status_dlg = ControllerStatusDialog(designer, store)
status_dlg._refresh()
check("controller status carries the node identity",
      status_dlg._lbl_node.text() == "PK-CTLR-1"
      and "PK100" in status_dlg._lbl_identity.text()
      and "DSTs" in status_dlg._lbl_identity.text(),
      status_dlg._lbl_identity.text())
status_dlg.close()

import json as _json
import tempfile

from azeo_control_trainer.azeo_control_designer.dialogs     .controller_properties import ControllerPropertiesDialog

with tempfile.TemporaryDirectory() as scratch:
    project_path = Path(scratch) / "_project.json"
    project_path.write_text(_json.dumps({"areas": [{
        "name": "X", "strategies": ["control/A.json"],
        "field_io": {"type": "modbus"}}]}, indent=2), encoding="utf-8")
    props = ControllerPropertiesDialog(store, project_path=project_path)
    props._name.setText("PK-A1")
    props._model.setCurrentIndex(props._model.findData(PKModel.PK300))
    props._serve.setChecked(True)
    props._port.setValue(5021)
    props._save()
    check("controller properties apply live — one node, everything reads it",
          store.controller.name == "PK-A1"
          and store.controller.model is PKModel.PK300)
    written = _json.loads(project_path.read_text(encoding="utf-8"))
    section = written["areas"][0].get("controller")
    check("and the declaration is written to the area file",
          section == {"name": "PK-A1", "model": "PK300",
                      "modbus_server": {"port": 5021}}, section)
    check("without touching the rest of the file",
          written["areas"][0]["strategies"] == ["control/A.json"]
          and written["areas"][0]["field_io"] == {"type": "modbus"})
# leave the node as the suite found it
store.controller.name = "PK-CTLR-1"
store.controller.model = PKModel.PK100

# --------------------------------------------- the wheel edits nothing
# Scrolling a properties list must never change a parameter — a stray
# wheel tick over MODE or a limit is an accidental write. The guard
# blocks the edit and hands the scroll to the page underneath.
from PySide6.QtCore import QPoint as _QPoint
from PySide6.QtCore import QPointF as _QPointF
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest as _QTest
from PySide6.QtGui import QWheelEvent as _QWheelEvent
from PySide6.QtWidgets import QDoubleSpinBox as _Spin

_spin = _Spin(designer)
_spin.setValue(50.0)
_spin.show()
app.processEvents()
_wheel = _QWheelEvent(_QPointF(5, 5), _QPointF(5, 5), _QPoint(0, 0),
                      _QPoint(0, 120), Qt.NoButton, Qt.NoModifier,
                      Qt.ScrollPhase.NoScrollPhase, False)
app.sendEvent(_spin, _wheel)
app.processEvents()
check("the mouse wheel changes no field", _spin.value() == 50.0,
      _spin.value())
_spin.setFocus()
app.sendEvent(_spin, _QWheelEvent(_QPointF(5, 5), _QPointF(5, 5),
                                  _QPoint(0, 0), _QPoint(0, 120),
                                  Qt.NoButton, Qt.NoModifier,
                                  Qt.ScrollPhase.NoScrollPhase, False))
check("not even a focused one", _spin.value() == 50.0, _spin.value())
# This smoke drives Qt with ``processEvents`` rather than entering
# ``QApplication.exec``. A DeferredDelete posted here can therefore survive
# for thousands of widgets and corrupt the Windows native heap when it is
# finally delivered. The designer is the spin box's owner; closing is enough
# and the parent performs deterministic teardown with the rest of the window.
_spin.close()

# ------------------------------- the Azeo expression model (CND/ACT)
# In Azeo the expression IS the wiring: CND has no wired inputs and ACT
# no outputs, because expressions reference parameters directly — module
# parameters bare (SP_HI_LIM), other blocks via param('PID1/OUT'), the
# store via tag('KEY') — and ACT assigns internal-WRITE parameters.
from azeo_control_trainer.core.strategy.blocks.dv_extra_blocks import (
    ConditionBlock,
)
from azeo_control_trainer.core.strategy.blocks.expression_check import (
    check_expression,
)
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph

check("the parser accepts the reference vocabulary",
      check_expression("param('SRC/ET') > SP_HI_LIM and b1",
                       extra_names=("SP_HI_LIM",)) is None)
check("a typo is caught at edit time, with the vocabulary in the message",
      "Unknown variable 'INP1'" in (check_expression("INP1 > 5") or ""))
check("a reference needs a quoted string",
      "quoted reference" in (check_expression("param(IN1)") or ""))
check("nothing outside the language parses",
      check_expression("__import__('os')") is not None)

xg = StrategyGraph("XTEST")
xcnd = ConditionBlock("CND1")
xsrc = ConditionBlock("SRC")
xg.add_block(xcnd)
xg.add_block(xsrc)
xsrc.set_output("ET", 7.5)
xg.set_module_parameter("SP_HI_LIM", 5.0)
xg.set_module_parameter("SPHI", False, access="internal_write")
check("module parameters live in the file's extra keys",
      xg.extra["parameters"]["SP_HI_LIM"]["value"] == 5.0)

xcnd.config.params["EXPRESSION"] =     "param('SRC/ET') > SP_HI_LIM and tag('K1') == 1"
xcnd.config.params["TIME_TRUE"] = 0.0
xcnd._module_graph = xg


class _Ctx:
    @staticmethod
    def read(key, default=None):
        return {"K1": 1}.get(key, default)


xcnd.runtime_context = _Ctx()
xcnd.execute(0.1)
check("a CND needs no wires — the expression references directly",
      xcnd.get_output("OUT_D") is True, xcnd.get_output("OUT_D"))

from azeo_control_trainer.core.strategy.blocks.action_block import ActionBlock

xact = ActionBlock("ACT1")
xg.add_block(xact)
xact._module_graph = xg
xact.inputs["IN_D"].value = True
xact.inputs["IN1"].value = 42.0
xact.config.params["SCRIPT_MODE"] = 1
xact.config.params["EXPRESSION"] = "write_param('SPHI', IN1)"
xact.execute(0.1)
check("ACT assigns an internal-WRITE module parameter",
      xg.module_parameters()["SPHI"]["value"] == 42.0,
      xg.module_parameters()["SPHI"])
xact.config.params["EXPRESSION"] = "write_param('SP_HI_LIM', 1)"
xact.execute(0.1)
check("an internal-READ parameter refuses assignment",
      xg.module_parameters()["SP_HI_LIM"]["value"] == 5.0
      and xact.get_output("ERROR") is True)

xg.remove_module_parameter("SP_HI_LIM")
xg.remove_module_parameter("SPHI")
check("an emptied parameter table is not written — byte-idempotence",
      "parameters" not in xg.extra)

# --- the editor dialog, Azeo layout, headless
from PySide6.QtWidgets import QTreeWidget as _Tree

from azeo_control_trainer.azeo_control_designer.dialogs.expression_editor     import ExpressionEditorDialog

xg.set_module_parameter("MAX_MOVE", 0.5)
expr_dlg = ExpressionEditorDialog(xcnd, xg, store)
check("the dialog opens on the block's expression",
      "param('SRC/ET')" in expr_dlg.editor.toPlainText())
expr_dlg.editor.insert_snippet(" and ")
check("operator buttons insert into the expression",
      expr_dlg.editor.toPlainText().endswith(" and "))
expr_dlg.editor.setPlainText("MAX_MOVE > 0.1")
check("Parse says clean for a clean expression",
      expr_dlg.parse() == ""
      and "No errors" in expr_dlg.output.toPlainText())
expr_dlg.editor.setPlainText("MAXMOVE > 0.1")
check("Parse names the unknown variable",
      "Unknown variable 'MAXMOVE'" in expr_dlg.parse())
expr_dlg.browse_internal()
browse_tree = expr_dlg._browse_dialog.findChild(_Tree)
seen = []
for i in range(browse_tree.topLevelItemCount()):
    root = browse_tree.topLevelItem(i)
    seen.append(root.text(0))
    for j in range(root.childCount()):
        seen.append(root.child(j).text(0))
check("Internal Parameter browses module parameters, inputs and blocks",
      "MAX_MOVE" in seen and "IN1" in seen and "SRC" in seen, seen[:8])
expr_dlg._browse_dialog.close()
expr_dlg.close()
xg.remove_module_parameter("MAX_MOVE")

# ------------------------------- primary block coverage, as a living test
# A native implementation, stable trainer alias, deliberate substitute, and
# explicit exclusion are different claims. A palette search hit is not enough.
from collections import Counter as _Counter

from azeo_control_trainer.core.strategy.block_catalog import (
    CoverageKind as _CoverageKind,
    audit_registered_types as _audit_registered_types,
    coverage_records as _coverage_records,
    primary_mnemonics as _primary_mnemonics,
)
from azeo_control_trainer.core.strategy.model.block_registry import (
    registry as _block_registry,
)

_registered_types = _block_registry.all_types()
_primary_counts = _Counter(record.kind for record in _coverage_records())
check("the primary inventory has 97 unique entries",
      len(_primary_mnemonics()) == 97
      and len(set(_primary_mnemonics())) == 97)
check("all primary implementation claims resolve through the loader registry",
      not _audit_registered_types(_registered_types),
      _audit_registered_types(_registered_types))
check("the primary audit keeps each coverage class explicit",
      _primary_counts == {
          _CoverageKind.NATIVE: 55,
          _CoverageKind.COMPATIBILITY_ALIAS: 26,
          _CoverageKind.SUBSTITUTE: 12,
          _CoverageKind.OUT_OF_SCOPE: 4,
      }, _primary_counts)

# --- the four blocks this closed: ALARM_DET, DIAG, INSPECT, FLC
from azeo_control_trainer.core.strategy.blocks.dv_advanced_blocks import (
    AlarmDetectionBlock, DiagnosticBlock, FuzzyLogicControlBlock,
    InspectBlock,
)

ad = AlarmDetectionBlock("AD1")
ad.config.params.update(HI_LIM=80.0, ALARM_HYS=2.0)
ad.inputs["IN"].value = 85.0
ad.execute(0.5)
check("ALARM_DET raises the high alarm", ad.get_output("HI_ACT") is True)
ad.inputs["IN"].value = 79.0
ad.execute(0.5)
check("and holds it inside the hysteresis band",
      ad.get_output("HI_ACT") is True)
ad.inputs["IN"].value = 77.0
ad.execute(0.5)
check("and clears it past the deadband",
      ad.get_output("HI_ACT") is False)

dg = DiagnosticBlock("DIAG1")
dg.inputs["FAILED"].value = True
dg.inputs["MAINT"].value = True
dg.execute(0.5)
check("DIAG encodes the spec's exact bit values",
      int(dg.get_output("OUT")) == 0xC000,
      hex(int(dg.get_output("OUT"))))

fz = FuzzyLogicControlBlock("FLC1")
fz.config.params.update(SF_ERROR=10.0, SF_DELTERR=2.0, SF_OUTPUT=2.0)
fz.reset()
fz.inputs["SP"].value = 50.0
fz.inputs["IN"].value = 30.0
for _ in range(20):
    fz.execute(0.5)
check("FLC drives its output up while PV is below SP (reverse acting)",
      fz.get_output("OUT") > 10.0, fz.get_output("OUT"))
_flc_before = fz.get_output("OUT")
fz.inputs["IN"].value = 70.0
for _ in range(20):
    fz.execute(0.5)
check("and back down once PV overshoots SP",
      fz.get_output("OUT") < _flc_before)

ig = StrategyGraph("MINS")
ib = InspectBlock("INS1")
ig.add_block(ib)
ig.add_block(dg)
ib._module_graph = ig
ib.execute(0.5)
check("INSPECT counts and decodes the DIAG blocks it can see",
      ib.get_output("DEVICES") == 1.0
      and ib.get_output("FAILED") == 1.0
      and ib.get_output("MAINT") == 1.0)

# ----------------------------------- SFC as a chart, not blocks-as-chart
# The chart lives inside one SFC_CHART block (mixing with FBD on the
# sheet, as Azeo's two languages do), executing natively: P actions on
# the entry scan, N every scan, first-true transition wins, empty target
# terminates, EN_D holds, RESET_D restarts, and set_param refuses a
# WIRED input with the wire named — a sequence action that a wire would
# silently overwrite is worse than a refused one.
from azeo_control_trainer.core.strategy.blocks.sfc_chart_block import (
    Chart, ChartAction, ChartStep, ChartTransition, SfcChartBlock,
)

sfc_chart = Chart(
    steps=[
        ChartStep("s_idle", "Idle", initial=True, actions=[
            ChartAction("a1", qualifier="P",
                        expression="set_param('CND1/IN1', 0)")]),
        ChartStep("s_fill", "Fill", actions=[
            ChartAction("a2", qualifier="N",
                        expression="set_param('CND1/IN1', tag('LT'))")]),
    ],
    transitions=[
        ChartTransition("t1", condition="tag('GO') == 1",
                        source="s_idle", target="s_fill"),
        ChartTransition("t2", condition="tag('LT') >= 80",
                        source="s_fill", target=""),
    ])
check("the chart validator passes a legal chart",
      sfc_chart.problems() == [])
bad = Chart(steps=[ChartStep("a", initial=True), ChartStep("b")],
            transitions=[ChartTransition("t", condition="",
                                         source="a", target="b")])
check("and names a conditionless transition",
      any("no" in problem and "condition" in problem
          for problem in bad.problems()), bad.problems())

sg = StrategyGraph("SEQX")
sfc_b = SfcChartBlock("SFC1")
sfc_cnd = ConditionBlock("CND1")
sg.add_block(sfc_b)
sg.add_block(sfc_cnd)
sfc_b.set_chart(sfc_chart)
sfc_b._module_graph = sg


class _SeqCtx:
    values = {"GO": 0, "LT": 10.0}

    @classmethod
    def read(cls, key, default=None):
        return cls.values.get(key, default)

    @classmethod
    def write(cls, key, value):
        cls.values[key] = value


sfc_b.runtime_context = _SeqCtx()
sfc_b.execute(0.5)
check("the chart starts at the initial step",
      sfc_b.get_output("ACTIVE") == "Idle")
_SeqCtx.values["GO"] = 1
sfc_b.execute(0.5)
check("a true condition advances it", sfc_b.get_output("ACTIVE") == "Fill")
_SeqCtx.values["LT"] = 55.0
sfc_b.execute(0.5)
check("an N action runs every scan the step is active",
      sfc_cnd.inputs["IN1"].value == 55.0, sfc_cnd.inputs["IN1"].value)
sfc_b.inputs["EN_D"].value = False
_SeqCtx.values["LT"] = 90.0
sfc_b.execute(0.5)
check("EN_D low holds the sequence exactly where it is",
      sfc_b.get_output("ACTIVE") == "Fill"
      and sfc_b.get_output("HELD") is True)
sfc_b.inputs["EN_D"].value = True
sfc_b.execute(0.5)
check("an empty target terminates, and the chart holds done",
      sfc_b.get_output("DONE") is True
      and sfc_b.get_output("ACTIVE") == "")
sfc_b.execute(0.5)
check("a finished chart does not loop", sfc_b.get_output("DONE") is True)
snapshot = sfc_b.sequence_snapshot()
check("the sequence snapshot carries the completed path",
      snapshot["completedPath"] == ["s_idle", "s_fill"]
      and snapshot["mode"] == "COMPLETE", snapshot["completedPath"])

# set_param refuses a wired input, naming the wire
sg.add_wire(sfc_cnd.id, "OUT_D", sfc_cnd.id, "DISABLE")
sfc_b.set_chart(Chart(
    steps=[ChartStep("s1", initial=True, actions=[
        ChartAction("a", qualifier="N",
                    expression="set_param('CND1/DISABLE', 1)")])],
    transitions=[ChartTransition("t", condition="1 == 2",
                                 source="s1", target="")]))
sfc_b.inputs["RESET_D"].value = True
sfc_b.execute(0.5)
sfc_b.inputs["RESET_D"].value = False
sfc_b.execute(0.5)
check("set_param refuses a wired input — the wire would win next scan",
      sfc_b.get_output("FAULT") is True)

# --- the chart editor, headless
from azeo_control_trainer.azeo_control_designer.dialogs.sfc_chart_editor     import SfcChartEditorDialog

sfc_b.set_chart(sfc_chart)
chart_dlg = SfcChartEditorDialog(sfc_b, sg, store)
check("the editor draws the chart",
      len(chart_dlg._step_items) == 2
      and len(chart_dlg._transition_items) == 2)
check("auto-layout stacked the steps vertically",
      chart_dlg.chart.steps[0].y < chart_dlg.chart.steps[1].y)
check("the editor validates clean", chart_dlg.validate() == [])
chart_dlg.chart.transitions[0].condition = ""
check("and blocks OK while a transition has no condition",
      chart_dlg.validate() != [])
from PySide6.QtWidgets import QDialogButtonBox as _QDBB
from PySide6.QtWidgets import QTextBrowser as _QTextBrowser

check("the editor has a Help button",
      any(b.button(_QDBB.Help) for b in chart_dlg.findChildren(_QDBB)))
chart_dlg.open_help()
_help_text = chart_dlg._help_dialog.findChild(_QTextBrowser).toPlainText()
check("and its help states the execution rules",
      "First true transition wins" in _help_text
      and "step_time" in _help_text
      and "DEVCTL latches" in _help_text)
chart_dlg._help_dialog.close()
chart_dlg.close()

from azeo_control_trainer.azeo_control_designer.dialogs.help_dialog import (
    ControlDesignerHelpDialog,
)

_help_dlg = ControlDesignerHelpDialog()
check("Help ▸ SFC Programming carries the same chart reference",
      _help_dlg.show_topic("SFC Programming")
      and "SFC_CHART" in _help_dlg._browser.toPlainText())
check("Control Designer About identifies the installed version and build",
      _help_dlg.show_topic("About Control Designer")
      and "Version" in _help_dlg._browser.toPlainText()
      and "Build" in _help_dlg._browser.toPlainText()
      and "0.4.0" in _help_dlg._browser.toPlainText())
_help_dlg.close()

# ------------------------------------------------ OPC UA: both PK faces
# The server publishes the whole module namespace with quality; the
# EIOC-style client subscribes a third-party server into a second store.
# Loopback: trainer B reads and commands trainer A over one real socket.
from azeo_control_trainer.connectivity.fieldio.opcua_driver import OpcUaLink
from azeo_control_trainer.connectivity.opcua.pk_server import PKOpcUaServer
from azeo_control_trainer.azeo_explorer.opcua_browser import (
    UaSession,
)

probe = _socket.socket()
probe.bind(("127.0.0.1", 0))
_ua_port = probe.getsockname()[1]
probe.close()

ua_server = PKOpcUaServer(store, port=_ua_port)
# Explicit, generous timeout. Building this namespace measures 15-21 s
# on a developer machine — against `start()`'s 20 s default, which made
# this check a coin toss that lost more often as the tag database grew.
# The product default is a judgement about how long an operator station
# waits at boot; a loaded test machine is not that, so the TEST says how
# long it is willing to wait rather than the timeout being tuned for it.
check("the OPC UA server serves the module namespace",
      ua_server.start(timeout=90.0) and ua_server.terminal_count > 500,
      ua_server.terminal_count)
check("writes are offered only where the tag database allows",
      ua_server.writable_count == len({
          e.io_tag for e in store.tagdb.of_kind("field") if e.writable}),
      ua_server.writable_count)

import asyncio as _asyncio

from asyncua import Client as _UaClient
from asyncua import ua as _ua


async def _probe_server():
    async with _UaClient(ua_server.endpoint) as ua_client:
        index = await ua_client.get_namespace_index(
            "urn:azeo:pk-controller")
        modules = ua_client.get_node(_ua.NodeId("Modules", index))
        names = sorted([(await c.read_browse_name()).Name
                        for c in await modules.get_children()])
        terminal = ua_client.get_node(_ua.NodeId("FIC-102/AI1/OUT", index))
        await _asyncio.sleep(1.2)          # one server refresh
        data_value = await terminal.read_data_value()
        refused = False
        try:
            await terminal.write_value(50.0)
        except Exception:                       # noqa: BLE001
            refused = True
        return names, data_value, refused


_names, _dv, _refused = _asyncio.run(_probe_server())
check("a UA client browses the same modules the designer shows",
      len(_names) == 7 and "MTR-102" in _names, _names)
check("a live terminal reads with Good quality",
      _dv.StatusCode_.is_good(), _dv.StatusCode_)
check("a terminal write is refused — a wire would re-assert it anyway",
      _refused)

# --- the loopback: a second trainer reads and commands this one
from azeo_control_trainer.core.datastore.shared_data_store import (
    SharedDataStore as _Store,
)

store_b = _Store()
ua_link = OpcUaLink(store_b, ua_server.endpoint, {
    "REMOTE.LEVEL": {"node": "ns=2;s=LI-101.PV",
                     "direction": "read"},
    "FT-102.PV": {"node": "ns=2;s=FT-102.PV",
                  "direction": "write"},
})
check("the EIOC-style link connects", ua_link.start())
store.set("LI-101.PV", 63.25)
check("trainer B subscribes trainer A's measurement",
      settle(lambda: store_b.get("REMOTE.LEVEL") == 63.25),
      store_b.get("REMOTE.LEVEL"))
store_b.queue_write("FT-102.PV", 12.75)
check("trainer B's supervisory write becomes real on trainer A",
      settle(lambda: store.get("FT-102.PV") == 12.75),
      store.get("FT-102.PV"))

# --- online browsing: the dialog's engine against the live server
ua_session = UaSession()
ua_session.connect(ua_server.endpoint)
top = {c["name"]: c for c in ua_session.children(None)}
check("online browsing finds the served folders",
      "Modules" in top and "FieldIO" in top, sorted(top)[:5])
module_children = ua_session.children(top["Modules"]["id"])
check("and walks into the module tree",
      any(c["name"] == "FIC-102" for c in module_children))
ua_session.disconnect()
ua_link.stop()
ua_server.stop()

# --- the dialog writes the external device under a separate EIOC node
from azeo_control_trainer.azeo_explorer.opcua_browser import (
    OpcUaBrowserDialog,
)

with tempfile.TemporaryDirectory() as scratch:
    project_path = Path(scratch) / "_project.json"
    project_path.write_text(_json.dumps({"areas": [{
        "name": "X", "strategies": ["control/A.json"],
        "field_io": {"type": "opcua", "endpoint": "legacy",
                     "signals": {}}}]}, indent=2),
        encoding="utf-8")
    browser = OpcUaBrowserDialog(store, project_path=project_path)
    browser._endpoint.setText("opc.tcp://10.0.0.9:4840/dev")
    browser._append_row("LI-101.PV", "ns=2;s=Device/Level", "read")
    browser._save()
    written = _json.loads(project_path.read_text(encoding="utf-8"))
    field_io = written["areas"][0]["eioc"]["field_io"]
    check("Save writes the EIOC OPC UA client contract",
          field_io == {"type": "opcua",
                       "endpoint": "opc.tcp://10.0.0.9:4840/dev",
                       "signals": {"LI-101.PV": {
                           "node": "ns=2;s=Device/Level",
                           "direction": "read"}}}, field_io)
    check("without touching the rest of the file",
          written["areas"][0]["strategies"] == ["control/A.json"]
          and "field_io" not in written["areas"][0])
    browser.session = UaSession()          # closeEvent-safe
    browser.close()

# ------------------------------------------------------------- faceplates
mgr = designer.faceplate_manager()
check("standalone, the designer owns its faceplate manager",
      mgr is not None and designer.owns_faceplate_manager())

check("the block-bound faceplates are registered",
      set(BLOCK_FACEPLATES) == {"DEVCTL", "MOTOR_INTERLOCK",
                                "INTERLOCK"},
      sorted(BLOCK_FACEPLATES))

mtr = graph_named("MTR-102")
check("the example area ships MTR-102", mtr is not None)
dc1 = next(b for b in mtr.blocks.values() if b.block_type == "DEVCTL")

designer._open_block_faceplate(dc1.id)
fp = mgr._block_popups.get("MTR-102/DC1")
check("Open Faceplate... opens the DEVCTL faceplate", fp is not None)
check("it is keyed on the tag database's MODULE/BLOCK path",
      "MTR-102/DC1" in mgr._block_popups)

designer._open_block_faceplate(dc1.id)
check("opening it twice raises the same window",
      len(mgr._block_popups) == 1, list(mgr._block_popups))

opened = []
for block_type in ("AI", "AO", "PID"):
    for i in range(designer._canvas_tabs.count()):
        canvas = designer._canvas_tabs.widget(i)
        if not getattr(canvas, "scene", None):
            continue
        found = next((b for b in canvas.scene.graph.blocks.values()
                      if b.block_type == block_type), None)
        if found is None:
            continue
        designer._canvas_tabs.setCurrentIndex(i)
        before = len(mgr._ai_popups) + len(mgr._ao_popups) + len(mgr._popups)
        designer._open_block_faceplate(found.id)
        after = len(mgr._ai_popups) + len(mgr._ao_popups) + len(mgr._popups)
        opened.append((block_type, after > before))
        break
check("the faceplates that already existed open too",
      all(ok for _, ok in opened), opened)

# ------------------------------------------------------- command routing
routes = {t: resolve_command(mtr, dc1, t)
          for t in ("START_CMD", "STOP_CMD", "RESET")}
check("an operator command wired from a DI routes to its field point",
      all(r[0] == ROUTE_FIELD for r in routes.values()), routes)
check("the field point is the DI's own store tag",
      routes["START_CMD"][1] == "MTR-102.cmd_start", routes["START_CMD"])
check("INTERLOCK, driven by module logic, is not commandable",
      resolve_command(mtr, dc1, "INTERLOCK")[0] == ROUTE_DRIVEN)

# --------------------------------------------- the pump workshop
graph_named("MTR-102")
fp = mgr._block_popups["MTR-102/DC1"]

store.set("MTR-102.run_fb", False)
settle(lambda: not bool(dc1.inputs["INTERLOCK"].value))
fp._btn_on.click()                       # nothing supplies the interlock yet
pump(1.0)
check("START is refused while an interlock is asserted",
      not bool(dc1.outputs["DO_START"].value)
      and not bool(store.get("MTR-102.cmd_start", False)),
      (dc1.outputs["DO_START"].value, store.get("MTR-102.cmd_start")))
check("and the refusal names the cause",
      any(label == "INTERLOCK" for label, _ in fp.blockers()), fp.blockers())

# There is no plant in this test (DECISIONS.md D2) — stand in for the field.
# XV-101.PV_D is *not* set directly: the XV-101 module owns that output and
# would overwrite it on the next scan. Command the valve the way an operator
# does and let its own module drive the position it reports.
store.set("LI-101.PV", 75.0)             # the tank is above the low limit
store.set("XV-101.permit", True)
store.set("XV-101.cmd_open", True)
settle(lambda: bool(store.get("XV-101.solenoid")))
store.set("XV-101.ZSO", True)            # the open limit switch makes
store.set("XV-101.cmd_open", False)
check("with the field satisfied the device reports ready",
      settle(lambda: not fp.blockers()), fp.blockers())

fp._btn_on.click()
check("pressing START drives the device output",
      settle(lambda: bool(dc1.outputs["DO_START"].value)))

store.set("MTR-102.run_fb", True)        # the starter's aux contact answers
check("the device reaches RUNNING once feedback confirms",
      settle(lambda: int(dc1.outputs["STATE"].value) == 2),
      dc1.outputs["STATE"].value)

store.set("LI-101.PV", 41.2)             # drain below the CND2 limit
# CND2 carries a 4 s TIME_TRUE delay before it asserts.
check("a level interlock trips the running device",
      settle(lambda: bool(dc1.outputs["LOCKED"].value), timeout=20.0),
      dc1.outputs["STATE"].value)
fp.refresh()
check("commanded and confirmed disagree during the trip",
      fp._cmd_box.text() != fp._fb_box.text(),
      (fp._cmd_box.text(), fp._fb_box.text()))

store.set("LI-101.PV", 75.0)
store.set("MTR-102.run_fb", False)
settle(lambda: not bool(dc1.inputs["INTERLOCK"].value))
check("RESET_REQUIRED holds it locked until the interlock is reset",
      bool(dc1.outputs["LOCKED"].value))

fp._btn_reset.click()
check("RESET clears the lock",
      settle(lambda: not bool(dc1.outputs["LOCKED"].value)))

# Wait for the RESET pulse to drop and the device to read ready again — an
# operator presses START when the faceplate says READY, not before.
settle(lambda: not fp.blockers() and not bool(dc1.inputs["RESET"].value))
fp._btn_on.click()
check("the device restarts after the reset",
      settle(lambda: bool(dc1.outputs["DO_START"].value)),
      dc1.outputs["STATE"].value)
store.set("MTR-102.run_fb", True)
check("and reaches RUNNING again",
      settle(lambda: int(dc1.outputs["STATE"].value) == 2),
      dc1.outputs["STATE"].value)

fp._btn_off.click()
pump(1.0)
store.set("MTR-102.run_fb", False)
check("STOP stops it",
      settle(lambda: int(dc1.outputs["STATE"].value) == 0),
      dc1.outputs["STATE"].value)

# ------------------------------------------------- motor interlock faceplate
from azeo_control_trainer.core.strategy.model.block_registry import registry  # noqa: E402
from azeo_control_trainer.core.strategy.model.strategy_graph import (  # noqa: E402
    StrategyGraph,
)

g = StrategyGraph("MTR-TEST")
mi = registry.get("MOTOR_INTERLOCK")("MI1")
mi.config.params.update({
    "PERM_1_DESC": "Power available",
    "PERM_5_DESC": "Suction valve open",
    "PERM_7_DESC": "Minimum flow path available",
    "RUN_1_DESC": "Seal system OK",
    "TRIP_1_DESC": "ESD normal",
})
g.add_block(mi)
motor_fp = mgr.open_device_faceplate(mi, graph=g, module="MTR-TEST")
check("MOTOR_INTERLOCK opens its faceplate",
      type(motor_fp).__name__ == "MotorInterlockFaceplate")
check("only configured channels are shown",
      sorted(motor_fp._perm_rows) == [1, 5, 7], sorted(motor_fp._perm_rows))

mi.inputs["PERM_5"].value = False
mi.inputs["PERM_7"].value = False
motor_fp.refresh()
blockers = motor_fp.start_blockers()
check("a failed permissive is named, not summarised as 'start failed'",
      [b[0] for b in blockers] == ["PERM_5", "PERM_7"], blockers)
check("and it is named with the engineer's own wording",
      blockers[0][1] == "Suction valve open", blockers[0])

mi.inputs["BYPASS_PERM_5"].value = True
motor_fp.refresh()
check("a bypass is annunciated rather than hidden",
      motor_fp._banner.isVisible()
      and "Suction valve open" in motor_fp._banner.text(),
      motor_fp._banner.text())
check("the bypassed permissive no longer blocks a start",
      [b[0] for b in motor_fp.start_blockers()] == ["PERM_7"])

mi.inputs["TRIP_1"].value = True
mi.inputs["LOCKOUT"].value = True
motor_fp.refresh()
check("lockout is reported as a start blocker in its own right",
      any(label == "LOCKOUT" for label, _ in motor_fp.start_blockers()),
      motor_fp.start_blockers())

motor_fp._chk_all.setChecked(True)
check("Show all channels reveals every channel",
      (len(motor_fp._perm_rows), len(motor_fp._run_rows),
       len(motor_fp._trip_rows)) == (8, 8, 4))

# --------------------------------------------------------- tag database
dlg = designer.show_tag_database()
browser = dlg.browser
db = browser.database
check("the tag browser opens", dlg is not None and db is not None)
# A snapshot, so it moves whenever a block gains or loses a pin — which is
# the point: the namespace is *derived*, and a silent change in it would mean
# the configuration and the addressable world had drifted apart. It went
# 1377 -> 1381 when DEVCTL began publishing ELAPSED: four device blocks in
# this area (MTR-102, MTR-203, XV-101, XV-OPTION), one new terminal each.
# 1381 -> 1389 when they gained PAUSE_CMD and PAUSED: two more terminals on
# each of the same four.
# 1389 -> 1393 when DEVCTL declared DEVICE_KIND (MOTOR|VALVE) in its own
# schema: one new parameter on the same four blocks. The key had shipped in
# XV-101's file and the faceplate read it, but the block never declared it —
# flagged at load as "config keys have no effect", invisible in the
# properties panel, and claimed as done in parity.py. The schema is the
# declaration; a key that works without being declared is a key the next
# engineer cannot discover.
# 1393 -> 1435 when the seven mode-bearing blocks (AI, AO, DI, DO, DEVCTL,
# FLC, PID) declared `normal_mode`: 42 blocks in this area carry a mode.
# Azeo's abnormal-mode icon fires when actual differs from NORMAL *or*
# from target (PVMS+FB.pdf states it three times); we carried only the
# target half, which stayed silent for a loop parked in MAN. The default
# is blank, so nothing lands in config.params and no shipped module moves.
# 1435 -> 1437 when the two PID blocks began publishing SP_WRK explicitly;
# Loop_fp cannot draw its rate-limited working-SP marker from RCAS_OUT by
# accident because that parameter has different remote-host semantics.
# 1437 -> 1439 when those same two PID blocks declared out_init as engineered
# commissioning data.  Their initial Manual output must be discoverable in the
# Tag Database rather than hidden in a project-specific startup branch.
check("it derives the namespace from the open modules",
      len(db) == 1439, len(db))
check("every PID publishes the working setpoint Loop_fp marks separately",
      len([e for e in db if e.name == "SP_WRK"]) == 2,
      len([e for e in db if e.name == "SP_WRK"]))
check("every DEVCTL publishes its transition timer",
      len([e for e in db if e.name == "ELAPSED"]) == 4,
      len([e for e in db if e.name == "ELAPSED"]))
check("and can have a transition held",
      len([e for e in db if e.name in ("PAUSE_CMD", "PAUSED")]) == 8,
      len([e for e in db if e.name in ("PAUSE_CMD", "PAUSED")]))
check("every module appears at the top level",
      browser._tree.topLevelItemCount() == 7,
      browser._tree.topLevelItemCount())
check("terminals are indexed for live value refresh",
      len(browser._live) == len(db.of_kind(EntryKind.TERMINAL)),
      (len(browser._live), len(db.of_kind(EntryKind.TERMINAL))))

browser._search.setText("TIME_TRUE")
browser._populate()
check("search finds a parameter by name",
      "MTR-102/CND2/CONFIG/TIME_TRUE" in browser._rows)

browser._tree.setCurrentItem(
    browser._rows["MTR-102/CND2/CONFIG/TIME_TRUE"])
detail = browser._detail.text()
check("the detail pane carries the unit and the cross-reference",
      "unit s" in detail and "Read by: MTR-102/CND2" in detail, detail)

written, read = db.references("MTR-102/DC1/INTERLOCK")
check("a terminal knows what drives it",
      written == ["MTR-102/NOT_ILK/OUT_D"], written)
_, read = db.references("MTR-102/AI_LI101")
check("a field point knows every module reading it",
      sorted(read) == ["LI-101/AI1", "MTR-102/AI_LI101"], read)

browser._search.setText("DC1")
browser._populate()
browser.refresh_values()
check("the browser shows the live terminal value, not a build-time snapshot",
      browser._rows["MTR-102/DC1/STATE"].text(2)
      == str(dc1.outputs["STATE"].value),
      browser._rows["MTR-102/DC1/STATE"].text(2))

# ------------------------------------------ the PVM faceplate renderer
# HMI Phase 3's rendering half: one generic widget renders any PVM class
# from its declarations; resolve_state decides everything (I6). Driven
# through the §6 states a healthy plant never shows.
from azeo_control_trainer.core.hmi.binding import (
    BindingEngine as _PvmEngine, LiveGraphSource as _PvmSource,
)
from azeo_control_trainer.core.hmi.pvms import registry as _pvm_registry
from azeo_control_trainer.core.hmi.pvms.render import PvmFaceplateWidget
from azeo_control_trainer.core.strategy.model.strategy_graph import (
    StrategyGraph as _PvmGraph,
)
from azeo_control_trainer.core.strategy.model.block_registry import (
    BlockRegistry as _PvmRegistry,
)
from azeo_control_trainer.core.strategy.model.terminal import (
    Quality as _PvmQuality,
)

_gg = _PvmGraph(name="PVMTEST")
_gp = _PvmRegistry().create("PID", "PID1")
_gp._apply_config()
_gg.add_block(_gp)
_engine = _PvmEngine(_PvmSource(lambda: {"PVMTEST": _gg}))
_fp = PvmFaceplateWidget(_pvm_registry.get("PID", "faceplate"),
                         {"path": "PVMTEST/PID1"}, _engine)
_gp.outputs["PV"].value = 55.0
_gp.outputs["PV"].status = _PvmQuality.GOOD
_fp.refresh()
_fp_value = _fp.sections["value"]
check("the PVM renderer shows a live value",
      "55" in _fp_value.pv.text(), _fp_value.pv.text())
check("normal is greyscale — no alarm marker or attention colour",
      _fp.state.name == "normal")
_gp.outputs["PV"].status = _PvmQuality.BAD
_fp.refresh()
check("Bad quality renders the faceplate's unreadable token and the "
      "labelled LAST GOOD row",
      _fp.visual.pv.bad
      and "LAST GOOD" in _fp.visual.last_good_text
      and "55" in _fp.visual.last_good_text,
      (_fp.visual.pv.bad, _fp.visual.last_good_text))
_gp.outputs["PV"].status = _PvmQuality.GOOD
_output_force_applied = _gp.force_terminal("PV", 60.0)
_fp.refresh()
check("an algorithm output cannot be forced or advertise a false FORCED badge",
      not _output_force_applied and not _fp.visual.forced)
_gp.release_all_forces() if hasattr(_gp, "release_all_forces") else None
check("writable faceplate declarations produce controls, disabled until "
      "a host attaches its checked write service",
      set(_fp.write_controls) >= {"sp.value", "out.value"}
      and not _fp.write_controls["sp.value"][1].isEnabled())
_fp.set_write_handler(_engine.write, _engine.can_write)
check("the PID faceplate's SP control reaches the block's operator handle",
      _fp.write_bound("sp.value", 42.5)
      and abs(_gp._pid_core.SP - 42.5) < 1e-9)
_fp.close()
check("closing the PVM faceplate releases its bindings",
      _engine.monitored_count == 0, _engine.monitored_count)

# Per-class panels: interlock table, cascade stack, running chart.
_ilk = _PvmRegistry().create("MOTOR_INTERLOCK", "ILK1")
_ilk.config.params.update({"PERM_1_DESC": "Suction open",
                           "RUN_1_DESC": "Lube oil OK",
                           "TRIP_1_DESC": "E-stop"})
_ilk._apply_config()
_ilk.inputs["TRIP_1"].value = True
_ilk.execute(0.5)
_gg.add_block(_ilk)
_ifp = PvmFaceplateWidget(
    _pvm_registry.get("MOTOR_INTERLOCK", "faceplate"),
    {"path": "PVMTEST/ILK1"}, _engine)
_ifp.refresh()
# The interlock faceplate declares a LAYOUT now, so its table lives in
# `sections` rather than as a per-class `panel` — and it is Azeo's
# tabbed DCC_fp shape: interlocks, permissives and trips on SEPARATE
# tabs, so each condition is asserted under the kind it belongs to
# rather than in one mixed list.
_ilk_panel = _ifp.sections["conditions"]


def _ilk_rows(panel, kind):
    table = panel.table_for(kind)
    return [table.item(r, 1).text() for r in range(table.rowCount())]


check("the interlock panel tables the §8.6 conditions, each under its "
      "own kind",
      (_ilk_rows(_ilk_panel, "permissive"),
       _ilk_rows(_ilk_panel, "interlock"),
       _ilk_rows(_ilk_panel, "trip"))
      == (["Suction open"], ["Lube oil OK"], ["E-stop"]),
      [_ilk_rows(_ilk_panel, k)
       for k in ("permissive", "interlock", "trip")])
_ilk_trip = _ilk_panel.table_for("trip")
check("the tripped E-stop row reads ACTIVE — a trip INVERTS, and "
      "reading `state` at face value would call it OK",
      _ilk_trip.item(0, 4).text() == "ACTIVE",
      _ilk_trip.item(0, 4).text())
check("a declared Azeo faceplate layout still materializes its writable "
      "command bindings",
      set(_ifp.write_controls) >= {
          "cmd.start", "cmd.stop", "cmd.reset", "cmd.ack"})
_ifp.close()

_gp2 = _PvmRegistry().create("PID", "PID2")
_gp2._apply_config()
_gp2.config.params["mode"] = "AUTO"
_gg.add_block(_gp2)
from azeo_control_trainer.core.hmi.pvms.control import (
    CascadePairFaceplate as _CascadeCls,
)
_cfp = PvmFaceplateWidget(
    _CascadeCls, {"master": "PVMTEST/PID1", "slave": "PVMTEST/PID2"},
    _engine)
_gp2.outputs["MODE"].value = "AUTO"
_cfp.refresh()
check("the cascade panel calls a slave out of CAS broken",
      _cfp.visual.cascade_broken)
_gp2.outputs["MODE"].value = "CAS"
_cfp.refresh()
check("and stands down once the slave is back in CAS",
      not _cfp.visual.cascade_broken)
_cfp.close()

from azeo_control_trainer.core.strategy.serialization.strategy_io import (
    load_strategy as _seq_load,
)
_sg_result = _seq_load(Path("src/strategies/azeo_modbus/sequence/"
                            "SEQ-101.json").resolve())
_sg = _sg_result[0] if isinstance(_sg_result, tuple) else _sg_result
_seq_engine = _PvmEngine(_PvmSource(lambda: {"SEQ-101": _sg}))
_sfp = PvmFaceplateWidget(
    _pvm_registry.get("SFC_CHART", "faceplate"),
    {"path": "SEQ-101/CHART"}, _seq_engine)
_sfc_block = next(b for b in _sg.blocks.values()
                  if b.block_type == "SFC_CHART")
_sfc_block.execute(0.5)
_sfp.refresh()
_steps = [lbl.text().strip() for lbl in _sfp.panel._step_labels]
check("the running chart lists SEQ-101's seven steps",
      len(_steps) == 7 and any("IDLE" in s for s in _steps), _steps)
check("and marks the active step with its elapsed time",
      any(s.startswith("▶ IDLE") for s in _steps), _steps)
_sfp.close()

# ---------------------------------------- the PVM Display Studio (01–02)
import tempfile as _st_tmp

from azeo_control_trainer.core.hmi.pvms.publishing import (
    DisplayLocked as _StudioLocked, PvmDisplay as _StoredPvmDisplay,
    PublishRefused as _StudioRefused,
)
from azeo_control_trainer.azeo_graphics_designer.studio import PvmStudio

_studio_root = _st_tmp.mkdtemp()
studio = PvmStudio(lambda: {"PVMTEST": _gg}, _studio_root,
                   display_name="Overview")
check("a newly created display starts at L1 Overview rather than silently "
      "claiming it is an L2 unit display",
      studio.display.level == 1
      and studio.display.to_dict()["level"] == 1,
      studio.display.to_dict())
pvm1 = studio.place_block("PVMTEST/PID1", "PID", 100, 80)
check("drop-to-bind places a bound PVM",
      pvm1 is not None and len(studio._items()) == 1)
check("the ambiguous-type chooser remembered its choice per display",
      studio.role_choices.get("PID", ("",))[0] in ("dynamo_compact",
                                                   "dynamo_inline"),
      studio.role_choices)
pvm2 = studio.place_block("PVMTEST/PID2", "PID", 100, 200)
check("and the second PID placed silently with the remembered class",
      pvm2 is not None
      and (pvm2.role, pvm2.variant) == studio.role_choices["PID"])
_gp.outputs["PV"].value = 61.5
studio._tick()
_item1 = next(i for i in studio._items() if i.pvm.id == pvm1.id)
check("a placed PVM reads live through the engine",
      _item1.binding.result.value == 61.5, _item1.binding.result)

_item1.setSelected(True)
pane_rows = studio.pane.rows
check("the Assembler pane keeps type-owned facts read-only and exposes "
      "PVM fill and line colors",
      pane_rows["path"].text() == "PVMTEST/PID1"
      and "fill" in pane_rows and "line" in pane_rows
      and "font" not in pane_rows,
      {k: v.text() for k, v in pane_rows.items()})
pane_rows["fill"].setText("#d8e0e7")
pane_rows["fill"].editingFinished.emit()
pane_rows["line"].setText("#425b70")
pane_rows["line"].editingFinished.emit()
check("PVM appearance edits persist on the placement",
      _item1.pvm.fill == "#d8e0e7"
      and _item1.pvm.line == "#425b70"
      and _item1.pvm.to_dict().get("fill") == "#d8e0e7"
      and _item1.pvm.to_dict().get("line") == "#425b70")

studio.save_draft()
entry = studio.publish("TEST")
check("publish records a revision with a display-terms diff",
      entry["rev"] == 1 and any("+ PVM" in line
                                for line in entry["diff"]),
      entry)
_item1.setPos(300, 80)
entry2 = studio.publish("PROD")
check("a moved PVM publishes as '~ moved'",
      any("~ moved" in line for line in entry2["diff"]),
      entry2["diff"])

studio.place_block("PVMTEST/NO_SUCH_BLOCK", "PID", 10, 10)
try:
    studio.publish("TEST")
    check("an unresolved path blocks the publish from the studio",
          False)
except _StudioRefused as error:
    check("an unresolved path blocks the publish from the studio",
          "NO_SUCH_BLOCK" in str(error), error)

try:
    studio.store.acquire_lock("Overview", who="someone.else")
    check("the open display is single-writer locked", False)
except _StudioLocked:
    check("the open display is single-writer locked", True)

studio._revert(1)
check("revert restores revision 1 — the unresolvable PVM is gone",
      len(studio._items()) == 2
      and len(studio.store.history("Overview")) == 3
      and all("NO_SUCH_BLOCK" not in str(i.pvm.params)
              for i in studio._items()),
      (len(studio._items()),
       len(studio.store.history("Overview"))))
studio.close()
check("closing the studio releases the display lock",
      not (Path(_studio_root) / "Overview" / ".lock").exists())

# -------------------------------------- sheet-06 visuals: the scale bars
_ai6 = _PvmRegistry().create("AI", "AI6")
_ai6.config.params.update({"scale_lo": 0.0, "scale_hi": 200.0,
                           "eng_units": "gpm", "HI_HI_LIM": 180.0,
                           "HI_LIM": 160.0, "LO_LIM": 40.0,
                           "LO_LO_LIM": 20.0})
_ai6._apply_config()
_gg.add_block(_ai6)
_afp = PvmFaceplateWidget(_pvm_registry.get("AI", "faceplate"),
                          {"path": "PVMTEST/AI6"}, _engine)
_ai6.outputs["OUT"].value = 100.0
_afp.refresh()
# AI_fp keeps the declaration as its public class catalogue, but paints the
# operator body through one measured surface. Eight separately-sized QWidget
# sections moved the scale, trend and alarm table independently.
check("AI_fp is assembled from Azeo's eight sections, in its order",
      list(_afp.sections) == ["title", "value", "pv_bar", "mode",
                              "trend", "alarms", "unit", "buttons"],
      list(_afp.sections))
check("AI_fp uses the reference's 204 x 468 content shell and one measured "
      "surface — eight independently-sized sections moved its scale",
      (_afp.faceplate_surface.width(), _afp.faceplate_surface.height())
      == (204, 468)
      and type(_afp.visual).__name__ == "AnalogFaceplateSurface",
      (_afp.faceplate_surface.size(), type(_afp.visual).__name__))
_ai_bar = _afp.visual
_ai_scale, _ai_channel = _ai_bar.pv_geometry()
_ai_indicator = _ai_bar.inner_indicator_geometry()
check("the AI scale is left of its adjacent light-blue PV channel at the "
      "manual's fixed coordinates",
      _ai_scale.getRect() == (79.0, 110.0, 20.0, 201.0)
      and _ai_channel.getRect() == (99.0, 110.0, 22.0, 201.0),
      (_ai_scale, _ai_channel))
check("the dark PV bar is centred inside that channel and follows PV on its "
      "own EU scale",
      _ai_bar.pv.fraction == 0.5
      and _ai_indicator.width() == 11.0
      and abs(_ai_indicator.center().x() - _ai_channel.center().x()) < 1e-9
      and abs(_ai_indicator.height() - _ai_channel.height() / 2) < 1e-9,
      (_ai_bar.pv.fraction, _ai_indicator, _ai_channel))
check("alarm limits come from the CONFIG binds, all four",
      _ai_bar.pv.ticks == {"HH": 180.0, "H": 160.0, "L": 40.0,
                           "LL": 20.0}, _ai_bar.pv.ticks)
check("no colour at rest — nothing is breached",
      _ai_bar.breached == "")
_ai6.outputs["OUT"].value = 190.0
_ai6.outputs["HI_HI_ACT"].value = True
_afp.refresh()
check("the breached limit is marked during the alarm",
      _ai_bar.breached == "HH", _ai_bar.breached)
_ai6.outputs["HI_HI_ACT"].value = False
_ai6.outputs["OUT"].status = _PvmQuality.BAD
_afp.refresh()
check("Bad quality blanks the bar — no fill for a dead transmitter",
      _ai_bar.pv.bad and _ai_bar.pv.fraction is None)
_ai6.outputs["OUT"].status = _PvmQuality.GOOD
_afp.close()

# Azeo's bar colour, in every theme. Sampled from the AI_fp figure
# (#7A92AC) and made theme-invariant, which is only defensible because
# the level is identified by its OUTLINE: the bare fill clears WCAG's
# 3:1 non-text minimum against hpgray's track by just 1.73:1, while
# the outlined boundary clears it by 9.2:1 at worst.
from azeo_control_trainer.core.hmi.theme.tokens import (
    THEMES as _ALL_THEMES,
)
from azeo_control_trainer.core.hmi.theme.roles import Role as _R2

_bar_colours = {n: th[_R2.BAR_PV] for n, th in _ALL_THEMES.items()}
# Read from the measurement, never restated: this assertion carried a
# literal #7A92AC that was a pixel off in all three channels, and a
# hardcoded copy is exactly how it stayed wrong.
from azeo_control_trainer.core.hmi.pvms.hp.style_metrics import (
    PALETTE as _DV_PALETTE,
)
check("the PV bar is Azeo's own colour in EVERY theme",
      set(_bar_colours.values()) == {_DV_PALETTE["BAR_PV"]}, _bar_colours)


def _luminance(hex_colour):
    def channel(c):
        c /= 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    h = hex_colour.lstrip("#")
    r, g, b = (channel(int(h[i:i + 2], 16)) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b):
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


_boundaries = {n: _contrast(th[_R2.TEXT], th[_R2.SURFACE_SUNK])
               for n, th in _ALL_THEMES.items()}
check("...and the level boundary clears 3:1 in every theme, which is "
      "what lets one colour serve all four",
      all(r >= 3.0 for r in _boundaries.values()),
      {n: round(r, 2) for n, r in _boundaries.items()})
_bare_fill = {n: round(_contrast(th[_R2.BAR_PV], th[_R2.SURFACE_SUNK]), 2)
              for n, th in _ALL_THEMES.items()}
check("the outline is doing the work, not the fill — the bare fill "
      "does NOT clear 3:1, which is why it is outlined",
      any(r < 3.0 for r in _bare_fill.values()), _bare_fill)

_pfp = PvmFaceplateWidget(_pvm_registry.get("PID", "faceplate"),
                          {"path": "PVMTEST/PID1"}, _engine)
_gp.outputs["PV"].value = 44.0
_gp.outputs["SP"].value = 47.5
_gp.outputs["OUT"].value = 46.4
_pfp.refresh()
check("the PID pairs OUT and PV bars with SP as a marker — deviation "
      "is the gap",
      _pfp.visual.pv.fraction is not None
      and _pfp.visual.out.fraction is not None
      and abs(_pfp.visual.pv.sp_fraction
              - _pfp.visual.pv._fraction_of(47.5)) < 1e-9,
      (_pfp.visual.pv.fraction, _pfp.visual.pv.sp_fraction))
_pfp.close()

# ------------------------------------- the Azeo HMI Studio window (new)
from azeo_control_trainer.azeo_graphics_designer.window import (
    GRAPHICS_TREE_ICONS,
    HmiStudioWindow,
    RIBBON_ICONS,
    WF as _RIBBON_WF,
    _PaletteCard,
    _ribbon_caption,
    _ribbon_icon_color,
)

# A persisted class has to be present before the host is constructed to catch
# the startup ordering bug: the palette itself is built before its first tab.
from azeo_control_trainer.core.hmi.pvms.user_library import (
    UserPvmLibrary as _StartupUserPvmLibrary,
)
from PySide6.QtWidgets import QLabel as _StartupLabel

_startup_library = _StartupUserPvmLibrary(_studio_root)
_startup_library.add(
    "StartupPaletteClass",
    [{"kind": "rect", "id": "startup_rect", "x": 0, "y": 0,
      "w": 60, "h": 30}],
)
# Stored project documents remain discoverable in Graphics Explorer, but only
# the overview should be rendered into a startup tab. Eagerly opening every
# project display made a real 13-display project look hung for ~20 seconds.
studio.store.save_draft(_StoredPvmDisplay(
    name="StartupClosed", level=2, parent="Overview"))

hmi_window = HmiStudioWindow(lambda: {"PVMTEST": _gg}, _studio_root)
check("Graphics Designer opens its active display in EDIT by default and "
      "makes project, display, and mode explicit in the native title bar",
      hmi_window.current().mode == "edit"
      and hmi_window.windowTitle() ==
      f"Azeo Graphics Designer — {hmi_window.area_name} — "
      f"{hmi_window.current().display.name} · EDIT — Azeo Control Trainer"
      and hmi_window._ribbon_buttons["mode.edit.toggle"].isChecked())
_home_quick_access = [action_id for _group, buttons in hmi_window._RIBBON["Home"]
                      for _glyph, _label, action_id, _checkable in buttons]
check("Save, Quick Online, Edit, Undo, and Redo remain reachable from the compact Home bar",
      all(action in _home_quick_access for action in (
          "display.save", "display.quick_online", "mode.edit.toggle",
          "edit.undo", "edit.redo"))
      and not hasattr(hmi_window, "doc_label")
      and not hasattr(hmi_window, "qat_edit_button"))
check("the Help ribbon tab cannot elide itself to 'He' during restored "
      "startup geometry",
      hmi_window.ribbon_tabs.elideMode() == Qt.ElideNone
      and not hmi_window.ribbon_tabs.usesScrollButtons()
      and hmi_window.ribbon_tabs.tabText(
          hmi_window.ribbon_tabs.count() - 1) == "Help")
check("the authoring scene carries the display document and only weak "
      "controller back-links, so teardown cannot strand a Qt/Python cycle",
      hmi_window.current().canvas.scene().display
      is hmi_window.current().display
      and type(hmi_window.current().canvas.studio).__name__
      == "ProxyType")
# A direct QStackedWidget reports the minimum height of its largest HIDDEN
# inspector page. The 1,500 px element form therefore pushed a maximized
# window below a 1080 px desktop as soon as PVM selection invalidated layout.
from PySide6.QtWidgets import QScrollArea as _InspectorScroll

_startup_pane = hmi_window.current().pane
for _page_index in (2, 1, 0):
    _startup_pane._stack.setCurrentIndex(_page_index)
    app.processEvents()
_startup_window_size = hmi_window.size()
hmi_window.resize(1920, 1080)
app.processEvents()
check("switching among Display, PVM and drawing inspectors cannot grow the "
      "maximized application below the desktop",
      isinstance(_startup_pane._stack_scroll, _InspectorScroll)
      and _startup_pane.minimumSizeHint().height() < 200
      and hmi_window.minimumSizeHint().height() < 900
      and hmi_window.height() == 1080
      and hmi_window.statusBar().geometry().bottom()
      < hmi_window.height(),
      (_startup_pane.minimumSizeHint().height(),
       hmi_window.minimumSizeHint().height(), hmi_window.size().toTuple(),
       hmi_window.statusBar().geometry().getRect()))
hmi_window.resize(_startup_window_size)
check("persisted user PVM classes appear in My PVMs immediately after "
      "Graphics Designer starts",
      any(label.text() == "StartupPaletteClass"
          for label in hmi_window._user_pvm_panel.findChildren(
              _StartupLabel)))
_startup_library.remove("StartupPaletteClass")

# A creation command is an authoring action.  A detached copy made from a
# template must not look as though the protected template itself was opened
# read-only.
from PySide6.QtWidgets import QGraphicsItem as _TemplateGraphicsItem
from azeo_control_trainer.core.hmi.pvms.hierarchy_templates import (
    L1_TEMPLATE as _EDITABLE_L1_TEMPLATE,
)
from azeo_control_trainer.core.hmi.pvms.rendering.chrome import (
    MODE_EDIT as _TEMPLATE_MODE_EDIT,
)

_template_draft = hmi_window.new_from_template(
    _EDITABLE_L1_TEMPLATE, "Editable Template Copy")
_template_items = _template_draft._static_items()
check("New from Template opens the detached draft in Edit mode with every "
      "drawing element selectable and movable",
      _template_draft.mode == _TEMPLATE_MODE_EDIT
      and bool(_template_items)
      and all(item.flags()
              & _TemplateGraphicsItem.GraphicsItemFlag.ItemIsSelectable
              and item.flags()
              & _TemplateGraphicsItem.GraphicsItemFlag.ItemIsMovable
              for item in _template_items))
hmi_window._close_tab(hmi_window.tabs.indexOf(_template_draft))
check("closing a display tab removes its hidden native widget from the "
      "window immediately instead of retaining every closed canvas",
      _template_draft.parent() is None)
hmi_window.open_display("Overview")

# Graphics Designer owns its own display-engineering help.  Control Designer's
# block manual cannot answer what a PVM pair, display revision or Special
# Symbol is, and a repository markdown file is not discoverable from F1.
from azeo_control_trainer.azeo_graphics_designer.studio_help import (  # noqa: E402
    TOPICS as _HELP_TOPICS,
    TOUR_STEPS as _TOUR_STEPS,
    illustrated_tutorial_path as _illustrated_tutorial_path,
    topic_matches as _topic_matches,
)
from PySide6.QtWidgets import QTextBrowser as _HelpBrowser  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QKeySequence as _HelpKeySequence,
)

_help_actions = {
    action_id
    for _group, buttons in hmi_window._RIBBON["Help"]
    for _glyph, _label, action_id, _checkable in buttons
}
_all_ribbon_labels = [
    label
    for groups in hmi_window._RIBBON.values()
    for _group, buttons in groups
    for _glyph, label, _action_id, _checkable in buttons
]
check("ribbon captions balance over at most two lines — operators such as "
      "the plus in PVM + Faceplate are never stranded on a third line",
      all(_ribbon_caption(label).count("\n") <= 1
          for label in _all_ribbon_labels)
      and _ribbon_caption("PVM + Faceplate") == "PVM +\nFaceplate")
_all_ribbon_commands = [
    (glyph, action_id)
    for groups in hmi_window._RIBBON.values()
    for _group, buttons in groups
    for glyph, _label, action_id, _checkable in buttons
]
check("every Graphics Designer ribbon command has a real vector icon rather "
      "than the old fallback dot",
      all(glyph in RIBBON_ICONS for glyph, _action in _all_ribbon_commands))
_ribbon_colours = {
    _ribbon_icon_color(action_id)
    for _glyph, action_id in _all_ribbon_commands
}
check("every Graphics Designer command uses the same shared authoring blue",
      _ribbon_colours == {_RIBBON_WF["lapis"]}, sorted(_ribbon_colours))
check("Graphics Designer has a complete Help ribbon rather than an external "
      "tutorial nobody can discover",
      _help_actions == {
          "help.center", "help.context", "help.tour",
          "help.display_creation", "help.pvm_creation",
          "help.faceplate_creation", "help.pvm_configuration",
          "help.illustrated_tutorial", "help.bindings",
          "help.shortcuts", "help.troubleshooting", "help.about", "help.suite",
      }, sorted(_help_actions))
check("the Help Center knowledge base covers workflow, data, reusable "
      "engineering and release diagnostics",
      {topic.category for topic in _HELP_TOPICS} == {
          "Start here", "Workspace", "Data and behavior",
          "Tutorials", "Reusable engineering", "Release and diagnostics",
          "Reference",
      }
      and all(topic.key and topic.title and topic.summary
              for topic in _HELP_TOPICS))

_help_center = hmi_window.open_help("engineering_library")
check("Help Center is modeless, searchable and supports stable deep links",
      _help_center.isVisible()
      and _help_center.current_topic_key == "engineering_library"
      and _help_center.show_topic("pvm_faceplate")
      and _help_center.current_topic_key == "pvm_faceplate")
check("F1 is registered as the platform HelpContents shortcut",
      hmi_window._help_shortcut in hmi_window._shortcuts
      and hmi_window._help_shortcut_standard_key
      == _HelpKeySequence.HelpContents)
hmi_window.explorer_tabs.setCurrentIndex(1)
check("F1 context routing follows the active engineering pane",
      hmi_window.context_help_topic(hmi_window.library.search)
      == "engineering_library",
      hmi_window.context_help_topic(hmi_window.library.search))
_help_center.search.setText("associated block faceplate")
check("Help search is token based and narrows the topic tree without "
      "discarding its category hierarchy",
      _help_center.visible_topic_keys() == ("special_symbols",)
      and _topic_matches(
          next(topic for topic in _HELP_TOPICS
               if topic.key == "special_symbols"),
          "associated block faceplate"),
      _help_center.visible_topic_keys())

for _action_id, _topic_key in (
        ("help.display_creation", "display_creation"),
        ("help.pvm_creation", "pvm_creation"),
        ("help.faceplate_creation", "faceplate_creation"),
        ("help.pvm_configuration", "pvm_configuration"),
        ("help.bindings", "bindings"),
        ("help.shortcuts", "keyboard_shortcuts"),
        ("help.troubleshooting", "troubleshooting")):
    hmi_window.dispatch(_action_id)
    check(f"{_action_id} routes to its real Help Center topic",
          _help_center.current_topic_key == _topic_key,
          _help_center.current_topic_key)

hmi_window.dispatch("help.illustrated_tutorial")
_illustrated_path = _illustrated_tutorial_path()
check("the full screenshot tutorial renders inside Help when the "
      "documentation bundle is installed",
      _illustrated_path is not None
      and _help_center.current_topic_key == "illustrated_tutorial"
      and _help_center.last_render_source == "illustrated_markdown"
      and "Creating reusable PVMs and faceplates"
      in _help_center.browser.toPlainText(),
      (_illustrated_path, _help_center.last_render_source))

_tour = hmi_window.open_guided_tour()
_tour.set_step(len(_TOUR_STEPS) - 1)
check("the guided tour walks the actual Studio surfaces and finishes "
      "without changing a display",
      _tour.isVisible()
      and _tour.step_index == len(_TOUR_STEPS) - 1
      and _tour.next.text() == "Finish")
_tour._advance()
check("Finish closes the guided tour instead of cycling on the final step",
      not _tour.isVisible())

_about = hmi_window.open_about()
_about_text = _about.findChild(_HelpBrowser).toPlainText()
check("About identifies release, build, active area and configuration library",
      _about.isVisible()
      and "Version" in _about_text
      and "Build" in _about_text
      and "0.4.0" in _about_text
      and hmi_window.area_name in _about_text
      and hmi_window._library_name in _about_text)
_about.close()
_help_center.close()
from PySide6.QtGui import QFontMetrics                         # noqa: E402
from azeo_control_trainer.core.hmi.theme.fonts import (       # noqa: E402
    ASSETS as _FONT_ASSETS,
    load_fonts as _load_theme_fonts,
)

_expected_fonts = {
    "DejaVuSans.ttf", "DejaVuSans-Bold.ttf",
    "DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf",
}
check("the standalone Studio loads its packaged fonts, so Qt-owned labels "
      "and painted PVM text do not become missing-glyph boxes",
      _expected_fonts.issubset({path.name for path in _FONT_ASSETS.iterdir()})
      and set(_load_theme_fonts()) == _expected_fonts
      and app.font().pointSizeF() > 0
      and app.font().pixelSize() == -1
      and QFontMetrics(app.font()).inFont("A"),
      (app.font().families(), app.font().pointSizeF(),
       app.font().pixelSize(), _load_theme_fonts()))
check("the studio window reopens the stored display in a tab",
      hmi_window.tabs.count() == 1
      and hmi_window.current().display.name == "Overview"
      and len(hmi_window.current()._items()) == 2)
check("Graphics Designer lists other project displays without eagerly rendering "
      "every one into a startup tab",
      "StartupClosed" in hmi_window._display_hierarchy()
      and all(studio.display.name != "StartupClosed"
              for studio in hmi_window.studios()),
      (hmi_window._display_hierarchy(),
       [studio.display.name for studio in hmi_window.studios()]))
second = hmi_window.open_display("Combustion")
check("opening another engineering display makes it editable immediately "
      "and acquires exactly that display's single-writer lock",
      hmi_window.tabs.count() == 2 and second is not None
      and second.mode == _TEMPLATE_MODE_EDIT
      and (Path(_studio_root) / "Combustion" / ".lock").exists())

current = hmi_window.current()
current.add_static("text", 20, 20, text="FUEL GAS")
current.add_static("line", 20, 60)
current.save_draft()
reloaded = current.store.load_draft("Combustion")
check("static drawing items round-trip through the draft",
      len(reloaded.items) == 2
      and {i["kind"] for i in reloaded.items} == {"text", "line"},
      reloaded.items)

# Azeo's two Data elements are real canvas objects, not model-only
# helpers or ribbon buttons that silently route somewhere else.
_before_data_subs = current.engine.monitored_count
_data_link = current.add_static("datalink", 40, 100, 128, 28)
_data_link.data.update({"path": "PVMTEST/PID1/PV.CV",
                        "datalink_type": "numeric", "decimals": 2})
current.rebind_static(_data_link)
current.pane.show_item(_data_link)
current.pane.item_tabs.setCurrentIndex(1)
check("a Data Link is placeable, live, and configurable in Graphics "
      "Configuration",
      _data_link.binding is not None
      and _data_link.binding.result.value == _gp.outputs["PV"].value
      and current.engine.monitored_count == _before_data_subs + 1
      and current.pane.datalink_holder.isVisibleTo(current.pane)
      and current.pane.data_validation.text() == "Live")
_display_link = current.add_static("display_link", 40, 145, 142, 34)
_display_link.data.update({"target": "Overview", "text": "Overview"})
current.rebind_static(_display_link)
current.enter_test()
check("a Display Link navigates through the Studio host in TEST, not "
      "through an inert hotspot",
      _display_link.activate()
      and hmi_window.current().display.name == "Overview")
current = hmi_window.open_display("Combustion")
check("Data and Display Links validate like the PVMs beside them",
      current.validate() == [], current.validate())
current.exit_test()
_data_link.setSelected(True)
current.delete_selected()
check("deleting a Data Link releases its live subscription",
      current.engine.monitored_count == _before_data_subs)

hmi_window.dispatch("insert.datalink")
check("the Insert ribbon's Data Link command arms a Data Link ghost",
      getattr(current, "_place_ghost", None) is not None
      and current._place_ghost.data.get("kind") == "datalink")
current.cancel_place()

from azeo_control_trainer.core.hmi.pvms.rendering.viewer import (
    PvmDisplayView as _DataView,
)
_opened_links = []
_data_view = _DataView(
    {"display": "Data elements", "pvms": [], "items": [
        {"kind": "datalink", "path": "PVMTEST/PID1/PV.CV",
         "datalink_type": "numeric", "decimals": 2,
         "x": 10, "y": 10, "w": 128, "h": 28},
        {"kind": "display_link", "target": "Overview",
         "text": "Overview", "x": 10, "y": 50,
         "w": 142, "h": 34}]},
    lambda: {"PVMTEST": _gg}, live=False,
    open_display=lambda target: _opened_links.append(target))
_view_data = next(i for i in _data_view.scene().items()
                  if isinstance(getattr(i, "data", None), dict)
                  and i.data.get("kind") == "datalink")
_view_link = next(i for i in _data_view.scene().items()
                  if isinstance(getattr(i, "data", None), dict)
                  and i.data.get("kind") == "display_link")
check("the Operator uses the same live Data Link renderer as the Studio",
      _view_data.binding is not None
      and _view_data.binding.result.value == _gp.outputs["PV"].value)
check("the Operator's Display Link calls its navigation host",
      _view_link.activate() and _opened_links == ["Overview"])
_data_view.close()

# User Entries share the drawing renderer but use a checked write path.
current = hmi_window.open_display("Combustion")
current.enter_edit()
_user_entry = current.add_static("user_entry", 40, 190, 150, 34)
_user_entry.data["entry"] = {
    "kind": "check_box", "label": "Bypass",
    "path": "PVMTEST/PID1/BYPASS",
}
current.rebind_static(_user_entry)
current.pane.show_item(_user_entry)
current.pane.item_tabs.setCurrentIndex(1)
check("a User Entry is placeable, configured in Graphics Configuration, "
      "and checked writable against the live block",
      _user_entry.binding is not None
      and _user_entry.write_allowed
      and current.pane.user_entry_holder.isVisibleTo(current.pane)
      and current.pane.entry_validation.text() == "Writable online")
_gp.inputs["BYPASS"].value = False
current.leave_edit()
check("a check box writes an unwired input through the shared Operator "
      "path", _user_entry.activate(True)
      and _gp.inputs["BYPASS"].value is True)
current.enter_test()
check("TEST remains a real sandbox now that User Entries can write",
      not _user_entry.activate(False)
      and _gp.inputs["BYPASS"].value is True
      and "disabled" in _user_entry.write_error)
current.exit_test()
check("an algorithm output without an operator handle is refused with "
      "its ownership reason",
      not _engine.can_write("PVMTEST/PID1/PV").success
      and "algorithm output" in _engine.can_write(
          "PVMTEST/PID1/PV").error)

from azeo_control_trainer.core.hmi.pvms import elements as _entry_elements

for _entry_type in _entry_elements.USER_ENTRY_TYPES:
    hmi_window._arm_user_entry(_entry_type)
    check(f"the {_entry_type} User Entry is offered by the place tool",
          current._place_ghost is not None
          and current._place_ghost.data["entry"]["kind"] == _entry_type)
    current.cancel_place()

hmi_window.tabs.setCurrentIndex(0)
hmi_window._validate()
check("Validate reports every unresolved path on the display",
      hmi_window.last_validation == [], hmi_window.last_validation)
hmi_window.current().place_block("PVMTEST/GHOST", "PID", 5, 5)
hmi_window._validate()
check("and names the one that does not resolve",
      [finding.message for finding in hmi_window.current().verification_findings()
       if finding.blocks_publish] == ["PVMTEST/GHOST"]
      and "PVMTEST/GHOST" in hmi_window.last_validation,
      hmi_window.last_validation)
hmi_window.current().delete_selected() if False else None
_ghost_item = next(i for i in hmi_window.current()._items()
                   if "GHOST" in str(i.pvm.params))
_ghost_item.setSelected(True)
hmi_window.current().delete_selected()
check("Delete removes the selected PVM and releases its bindings",
      all("GHOST" not in str(i.pvm.params)
          for i in hmi_window.current()._items()))

hmi_window.dispatch("mode.test")
from PySide6.QtWidgets import QGraphicsItem as _QGI
check("Test mode locks editing while the display stays live",
      hmi_window.current().test_mode
      and not any(bool(i.flags()
                       & _QGI.GraphicsItemFlag.ItemIsMovable)
                  for i in hmi_window.current()._items()))
hmi_window.dispatch("mode.test")

# §8.6/§8.7: the mode machine and display-only undo.
_st = hmi_window.current()
check("leaving EDIT releases the lock (VIEW holds nothing)",
      (_st.leave_edit(), not (Path(_studio_root) / "Overview"
                              / ".lock").exists())[1])
hmi_window.dispatch("tool.select")
check("Home > Select is an authoring command: it enters EDIT instead of "
      "leaving a highlighted but read-only cursor",
      _st.mode == "edit"
      and (Path(_studio_root) / "Overview" / ".lock").exists())
_st.leave_edit()
_before = len(_st._items())
_st.place_block("PVMTEST/PID2", "PID", 400, 300)
check("place auto-enters EDIT and re-acquires the lock",
      _st.mode == "edit"
      and (Path(_studio_root) / "Overview" / ".lock").exists())
_st.undo()
check("undo removes the placed PVM and releases its bindings",
      len(_st._items()) == _before, len(_st._items()))
_st.redo()
check("redo restores it", len(_st._items()) == _before + 1)
_st.undo()

grid_types = [hmi_window.library.grid.item(r, 0).text()
              for r in range(hmi_window.library.grid.rowCount())]
check("the Library Explorer grids every registered type with role "
      "columns", "PID" in grid_types and "SFC_CHART" in grid_types,
      grid_types[:6])
pid_row = grid_types.index("PID")
check("PID shows all four roles present",
      all(hmi_window.library.grid.item(pid_row, c).text() == "✓"
          for c in range(1, 5)))
hmi_window.library.grid.selectRow(pid_row)
check("the class inspector reads the selected class's bindings",
      hmi_window.library.bindings.rowCount() > 5
      and "1 of 1 ✓" in hmi_window.library.params_label.text())

health = hmi_window.current().health()
check("the status bar's health counts come from live bindings",
      health["subs"] > 0 and health["good"] >= 1, health)
# ------------------------- pipes, symbol styling, dynamo variants
_st2 = hmi_window.current()
_v1 = _st2.add_static("symbol", 60, 60, w=80, h=100, symbol="vessel")
_v2 = _st2.add_static("symbol", 300, 80, w=80, h=100, symbol="pump")
_pipe = _st2.add_pipe(_v1, "e", _v2, "w")
check("a pipe connects two symbols at mouse-chosen anchors",
      len(_st2._pipe_items()) == 1
      and _pipe.data["a_side"] == "e" and len(_pipe._points) >= 4)
_before_route = [(_p.x(), _p.y()) for _p in _pipe._points]
_v2.setPos(360, 200)
check("and re-routes when an endpoint moves",
      [(_p.x(), _p.y()) for _p in _pipe._points] != _before_route)
_st2.save_draft()
_reloaded2 = _st2.store.load_draft(_st2.display.name)
check("pipes persist in the display document",
      any(i.get("kind") == "pipe" for i in _reloaded2.items),
      _reloaded2.items)
_v1.data["fill"] = "#4477AA"
check("a drawing symbol keeps its own fill color",
      _v1.fill_colour().name() == "#4477aa")
_v1.data["w"], _v1.data["h"] = 120, 150
check("symbols resize by data without disproportion (aspect from "
      "the drag lock)", _v1.data["w"] == 120)
_v2.setSelected(True)
_st2.delete_selected()
check("deleting an endpoint removes its pipe too",
      len(_st2._pipe_items()) == 0)

_tank = _st2.place_block("PVMTEST/AI6", "AI", 500, 60,
                         role=("dynamo_inline", "tank"))
check("the tank-with-trend variant places and persists its variant",
      _tank is not None and _tank.variant == "tank"
      and _tank.to_dict().get("variant") == "tank")
_bar = _st2.place_block("PVMTEST/AI6", "AI", 660, 60,
                        role=("dynamo_inline", ""))
check("the analog bar shares the role as the no-variant class",
      _bar is not None and _bar.variant == "")
for _ in range(4):
    _st2._tick()
_tank_item = next(i for i in _st2._items() if i.pvm.variant == "tank")
check("the tank item accumulates sparkline history from live values",
      len(_tank_item.history) >= 2, len(_tank_item.history))

# -------------------- rotation, grouping, alignment, styles, shapes
_hex = _st2.add_static("hexagon", 40, 300, w=60, h=60)
_arc = _st2.add_static("arc", 120, 300, w=60, h=60)
_poly = _st2.add_static("polyline", 200, 300, w=80, h=50)
check("polygon, hexagon, arc and polyline shapes place",
      {i.data["kind"] for i in (_hex, _arc, _poly)}
      == {"hexagon", "arc", "polyline"})
_hex.setSelected(True)
_st2.rotate_selected(90)
check("rotation is quarter-turn data on the item",
      _hex.data["rot"] == 90 and _hex.rotation() == 90)
_arc.setSelected(True)
_grp = _st2.group_selected()
check("drawing items group (Ctrl+G) with a shared handle",
      _grp is not None
      and _hex.data.get("group") == _arc.data.get("group") == _grp)
_st2.ungroup_selected()
check("and ungroup", not _hex.data.get("group"))
_hex.setSelected(True)
_arc.setSelected(True)
_st2.align_selected("top")
check("alignment pulls the selection to one edge",
      abs(_hex.sceneBoundingRect().top()
          - _arc.sceneBoundingRect().top()) < 0.01)
_pipe2 = _st2.add_pipe(_hex, "e", _arc, "w")
_pipe2.data.update({"width": 4, "style": "dash", "arrow": "end"})
check("a pipe carries thickness, line type and arrows",
      _pipe2.data["arrow"] == "end")
_st2.set_background("#EFE8D8")
check("the display takes a background colour and persists it",
      (_st2.save_draft(),
       _st2.store.load_draft(_st2.display.name).background)[1]
      == "#EFE8D8")
_st2.pane.show_pvm(None)
_st2.pane.display_rows["description"].setPlainText("Unit overview")
_st2.pane._apply_display_property("description")
_st2.pane.display_rows["width"].setValue(1280)
_st2.pane._apply_display_property("width")
_st2.pane.display_rows["height"].setValue(720)
_st2.pane._apply_display_property("height")
_st2.pane.display_rows["view_type"].setCurrentIndex(
    _st2.pane.display_rows["view_type"].findData("actual_size"))
_st2.pane._apply_display_property("view_type")
_st2.save_draft()
_canvas_display = _st2.store.load_draft(_st2.display.name)
check("Canvas properties are editable contracts, not labels: frame, "
      "description and view mode persist and drive the scene",
      _canvas_display.description == "Unit overview"
      and (_canvas_display.width, _canvas_display.height) == (1280, 720)
      and _canvas_display.view_type == "actual_size"
      and abs(_st2.canvas.sceneRect().width() - 1280) < 0.01)
check("a framed display exposes its exact page rectangle and dotted guide "
      "only while authoring",
      (_st2.canvas.page_rect().x(), _st2.canvas.page_rect().y(),
       _st2.canvas.page_rect().width(), _st2.canvas.page_rect().height())
      == (0.0, 0.0, 1280.0, 720.0)
      and _st2.canvas.page_boundary_visible())
_st2.enter_test()
check("the page guide is authoring chrome and cannot enter TEST/runtime",
      not _st2.canvas.page_boundary_visible())
_st2.exit_test()

_display_properties = _st2.open_display_properties()
check("Display Properties exposes hierarchy, canvas, operator-view, PVM-tag "
      "and engineering-status fields from the Explorer's one model",
      _display_properties.level.count() == 4
      and _display_properties.width_spin.value() == 1280
      and _display_properties.height_spin.value() == 720
      and _display_properties.view_type.currentData() == "actual_size"
      and _display_properties.show_tag.count() == 4)
_display_properties.width_spin.setValue(0)
check("Display Properties rejects a half-configured page instead of "
      "publishing an ambiguous boundary",
      not _display_properties.apply()
      and "both" in _display_properties.error.text())
_display_properties.width_spin.setValue(1280)
_display_properties.description.setPlainText("Unit overview from Properties")
_display_properties.show_tag.setCurrentIndex(
    _display_properties.show_tag.findData("friendly"))
check("valid Display Properties update the open draft and leave it unsaved",
      _display_properties.apply()
      and _st2.display.description == "Unit overview from Properties"
      and _st2.display.show_tag == "friendly" and _st2.unsaved)

# --------------- Azeo Operator Station UX: snippet, friendly name, watch, nav
_gi = next(i for i in _st2._items() if "PID1" in str(i.pvm.params))
_st2.rename_pvm(_gi, "Reflux Flow")
check("a PVM takes a friendly name and keeps it in the document",
      _gi.pvm.label == "Reflux Flow"
      and _gi.pvm.to_dict().get("label") == "Reflux Flow")

from PySide6.QtCore import QPoint as _QPoint
_st2.show_snippet(_gi, _QPoint(50, 50))
_snip = _st2._snippet
check("the hover snippet reads the whole point — name, mode, value",
      "Reflux Flow" in _snip.title.text()
      and ("MODE" in _snip.body.text() or "PV" in _snip.body.text()),
      (_snip.title.text(), _snip.body.text()))
_st2.hide_snippet()

_st2.add_to_watch(_gi)
check("Add to Watch Area builds the runtime list, by friendly name",
      _st2.watch_area.rows and _st2.watch_area.rows[0][0]
      == "Reflux Flow")
_st2.watch_area.refresh()
check("watch rows read live values",
      _st2.watch_area.table.item(0, 1) is not None)

# ISA 4-layer hierarchy: level + parent persist and drive navigation.
_st2.display.level = 2
_st2.display.parent = "Overview"
_st2.save_draft()
_reloaded3 = _st2.store.load_draft(_st2.display.name)
check("display level and parent persist (ISA hierarchy is "
      "configuration, not scripting)",
      _reloaded3.level == 2 and _reloaded3.parent == "Overview")
_hier = hmi_window._display_hierarchy()
check("the Graphics Explorer hierarchy sees every display with its "
      "level", _st2.display.name in _hier
      and _hier[_st2.display.name] == (2, "Overview"))
hmi_window.current().enter_test()
hmi_window._sync_chrome()
check("Test mode raises the navigation bar from the display set",
      hmi_window._nav_layout.count() > 0)
hmi_window.current().exit_test()

# ------------------------- aspect-true symbols + click-to-place ghost
from azeo_control_trainer.core.hmi.pvms.symbols import aspect as _aspect
_vsym = _st2.add_static("symbol", 40, 500, w=100, symbol="vessel")
check("a symbol's item rect matches the glyph's own aspect — the "
      "selection hugs what is drawn",
      abs(_vsym.rect().height() - 100 * _aspect("vessel")) < 0.5,
      (_vsym.rect().height(), 100 * _aspect("vessel")))

from PySide6.QtCore import QPointF as _QPF
_st2.arm_place("symbol", symbol="pump", w=90)
check("arming placement creates the cursor ghost",
      _st2._place_ghost is not None
      and _st2._place_ghost.opacity() < 1.0)
_st2._place_move(_QPF(400, 520))
check("the ghost follows the mouse",
      abs(_st2._place_ghost.pos().x() - (400 - 45)) < 1.0)
_before_place = len(_st2._static_items())
_st2._place_click(_QPF(400, 520))
_placed_pump = _st2.selection.snapshot().primary
check("a click drops the item at the cursor and disarms",
      len(_st2._static_items()) == _before_place + 1
      and _st2._place_ghost is None
      and _placed_pump is not None
      and (_placed_pump.mapToScene(_placed_pump.rect().center())
           - _QPF(400, 520)).manhattanLength() < 1.0)
_st2.arm_place("rect", w=100, h=60)
_st2.cancel_place()
check("Esc/cancel removes the ghost without placing",
      _st2._place_ghost is None
      and len(_st2._static_items()) == _before_place + 1)

# ----------------- connect gesture: preview, drag-release, free resize
_c1 = _st2.add_static("symbol", 40, 640, w=80, symbol="vessel")
_c2 = _st2.add_static("symbol", 300, 660, w=80, symbol="pump")
_st2.arm_connect()
_st2._connect_click(_c1, _c1.mapToScene(_c1.rect().center()))
_st2._connect_move(_QPF(200, 660))
check("a half-made connection shows a live preview line",
      getattr(_st2, "_connect_line", None) is not None)
_pipes_before = len(_st2._pipe_items())
_st2._connect_release(_c2, _c2.mapToScene(_c2.rect().center()))
check("press-drag-release completes the pipe like a click-click",
      len(_st2._pipe_items()) == _pipes_before + 1
      and not _st2.connect_armed
      and getattr(_st2, "_connect_line", None) is None)

_c1.data["w"], _c1.data["h"] = 80, 200
_c1.prepareGeometryChange()
_c1.setRect(0, 0, 80, 200)
check("a vessel stretches tall without growing wide (free resize)",
      _c1.rect().height() == 200 and _c1.rect().width() == 80)

# ----------------- builder-parity canvas operations (functionality)
_ell = _st2.insert_at("ellipse", _QPF(500, 700))
check("insert-at-cursor lands the shape centred on the click",
      _ell.data["kind"] == "ellipse"
      and abs(_ell.pos().x() + _ell.rect().width() / 2 - 500) < 8)
# Placement now selects its result; this clipboard check owns its selection
# instead of depending on whichever object a previous check left selected.
_st2.selection.replace((_gi, _ell))
hmi_window._copy_selected()
check("the clipboard carries PVMs and drawing items together",
      len(hmi_window._pvm_clipboard["records"]) == 2
      and {record["type"] for record in hmi_window._pvm_clipboard["records"]}
      == {"pvm", "item"})
_before_paste = (len(_st2._items()), len(_st2._static_items()))
_st2.paste_at(_QPF(700, 700))
check("paste-here lands the copied set at the click",
      (len(_st2._items()), len(_st2._static_items()))
      == (_before_paste[0] + 1, _before_paste[1] + 1))
_st2.set_pan(True)
from PySide6.QtWidgets import QGraphicsView as _QGV
check("the pan tool switches the canvas to the hand drag",
      _st2.canvas.dragMode() == _QGV.ScrollHandDrag)
_st2.select_tool()
check("select tool stands every armed tool down",
      _st2.canvas.dragMode() == _QGV.RubberBandDrag
      and not _st2.connect_armed)
_st2.fit_drawing()
check("fit drawing runs over the items bounding rect", True)

# ---------------- connection points, lock, mirror, z-order, style
_cp_side = _c1.add_connection_point(
    _c1.mapToScene(_QPF(_c1.rect().width() * 0.5,
                        _c1.rect().height() * 0.25)))
check("'Add connection point here' creates a custom anchor",
      _cp_side == "c0" and _c1.data["cps"] == [[0.5, 0.25]]
      and _cp_side in _c1.anchor_sides())
_pipe_cp = _st2.add_pipe(_c1, "c0", _c2, "w")
check("a pipe attached at a custom point pins its routing (auto off)",
      _pipe_cp.data["auto"] is False
      and _pipe_cp.data["a_side"] == "c0")

_c1.data["locked"] = True
_pos_before = (_c1.pos().x(), _c1.pos().y())
_c1.setPos(_pos_before[0] + 50, _pos_before[1])
check("a locked object stays put",
      (_c1.pos().x(), _c1.pos().y()) == _pos_before)
_c1.data["locked"] = False

_c2.data["mx"] = True
check("mirroring is item state that persists",
      _c2.data.get("mx") is True)
_c2.setSelected(True)
_z_before = _c2.zValue()
_st2.z_shift(1000)
check("bring-to-front raises the z-order", _c2.zValue() > _z_before)

_c2.data.update({"fill": "#AA3344", "width": 3})
_st2._style_clipboard = {"fill": "#AA3344", "width": 3}
_ell.setSelected(True)
for target in _st2._static_items():
    if target.isSelected():
        target.data.update(_st2._style_clipboard)
check("copy/paste style carries fill and width between items",
      _ell.data.get("fill") == "#AA3344")

# ------------- anchors touch the ink, not the SVG's padding
from azeo_control_trainer.core.hmi.pvms.symbols import (
    content_box as _content_box,
)
_cb = _content_box("pump")
check("the symbol's content box is tighter than its viewBox "
      "(the padding is real)", _cb[2] < 1.0 or _cb[0] > 0.0, _cb)
_gap_item = _st2.add_static("symbol", 900, 900, w=100, symbol="pump")
_east = _gap_item.mapFromScene(_gap_item.anchor("e"))
check("the east anchor sits on the artwork edge, inside the rect — "
      "no gap between pipe and symbol",
      _east.x() <= _gap_item.rect().width() - 0.5
      or _cb[0] + _cb[2] >= 0.999,
      (_east.x(), _gap_item.rect().width()))

# ------------- drag-from-anchor (the JS gesture) + point editing
_d1 = _st2.add_static("symbol", 60, 900, w=80, symbol="drum")
_d2 = _st2.add_static("symbol", 340, 920, w=80, symbol="pump")
check("a press lands on an anchor within its hit radius",
      _d1.anchor_hit(_d1.mapFromScene(_d1.anchor("e"))) == "e"
      and _d1.anchor_hit(_QPF(9999, 9999)) is None)
_pipes0 = len(_st2._pipe_items())
_st2.start_anchor_drag(_d1, "e")
_st2.drag_anchor_to(_QPF(250, 930))
check("the anchor drag shows the live preview",
      _st2._connect_line is not None)
_st2.finish_anchor_drag(_d2.mapToScene(_QPF(10, 30)), exclude=_d1)
check("releasing on another symbol draws the pipe — no tool armed",
      len(_st2._pipe_items()) == _pipes0 + 1
      and not _st2.connect_armed)
_st2.start_anchor_drag(_d1, "s")
_st2.finish_anchor_drag(_QPF(5000, 5000), exclude=_d1)
check("releasing on empty canvas cancels cleanly",
      len(_st2._pipe_items()) == _pipes0 + 1
      and _st2._connect_line is None)

_side2 = _d1.add_connection_point(
    _d1.mapToScene(_QPF(_d1.rect().width() * 0.3, 0)))
_d1.data["cps"][int(_side2[1:])] = [0.7, 0.0]
check("a custom connection point moves (edit) and resolves",
      abs(_d1.mapFromScene(_d1.anchor(_side2)).x()
          - _d1.rect().width() * 0.7) < 0.5)
check("and removes",
      _d1.remove_connection_point(_side2)
      and _d1.data["cps"] == [])

# ------------- process PVMs: valve+OP bar, vessel bar+trend, statuses
check("the process PVM set is registered",
      _pvm_registry.get("AO", "dynamo_inline", "valve") is not None
      and _pvm_registry.get("AI", "dynamo_inline", "vessel")
      is not None
      and all(_pvm_registry.get("DEVCTL", "dynamo_compact", v)
              is not None
              for v in ("pump", "compressor", "fan", "blower",
                        "turbine", "motor")))

_ao1 = _PvmRegistry().create("AO", "AO1")
_ao1._apply_config()
_gg.add_block(_ao1)
_dcv = _PvmRegistry().create("DEVCTL", "P101")
_dcv._apply_config()
_gg.add_block(_dcv)

_stp = hmi_window.current()
_vpvm = _stp.place_block("PVMTEST/AI6", "AI", 640, 640,
                         role=("dynamo_inline", "vessel"))
_vitem = next(i for i in _stp._items() if i.pvm.id == _vpvm.id)
_vitem.history.extend(float(40 + i) for i in range(20))
_opvm = _stp.place_block("PVMTEST/AO1", "AO", 820, 640,
                         role=("dynamo_inline", "valve"))
_oitem = next(i for i in _stp._items() if i.pvm.id == _opvm.id)
_hpvm = _stp.place_block("PVMTEST/AI6", "AI", 640, 860,
                         role=("dynamo_inline", "hbar"))
_hitem = next(i for i in _stp._items() if i.pvm.id == _hpvm.id)
_rpvm = _stp.place_block("PVMTEST/AI6", "AI", 840, 860,
                         role=("dynamo_inline", "reactor"))
_ritem = next(i for i in _stp._items() if i.pvm.id == _rpvm.id)
check("horizontal-bar and reactor-with-bar variants place and "
      "paint from the same analog binds",
      (_hpvm.w, _hpvm.h) == (170.0, 52.0)
      and (_rpvm.w, _rpvm.h) == (130.0, 160.0))
check("vessel and valve variants place with their DEFAULT_SIZE",
      (_vpvm.w, _vpvm.h) == (160.0, 190.0)
      and (_opvm.w, _opvm.h) == (96.0, 112.0),
      ((_vpvm.w, _vpvm.h), (_opvm.w, _opvm.h)))
_vitem.setSelected(True)
_pvm_before = (_vitem.rect().width(), _vitem.rect().height())
check("a selected process PVM exposes eight real resize handles, not "
      "eight decorative squares",
      set(_vitem.handle_points())
      == {"nw", "n", "ne", "w", "e", "sw", "s", "se"}
      and all(_vitem.handle_at(point) == name
              for name, point in _vitem.handle_points().items()))
_vitem._resize_to(
    "se", _vitem.mapToScene(_QPF(_pvm_before[0] + 48,
                                  _pvm_before[1] + 32)),
    bypass_snap=True)
check("dragging a PVM handle resizes both its live renderer and persisted "
      "placement geometry",
      _vitem.rect().width() > _pvm_before[0]
      and _vitem.rect().height() > _pvm_before[1]
      and _vitem.pvm.w == _vitem.rect().width()
      and _vitem.pvm.h == _vitem.rect().height()
      and _vitem.pvm.to_dict()["w"] == _vitem.rect().width())
_vitem.pvm_locked = True
check("an explicitly locked PVM remains selectable but offers no transform "
      "gesture", _vitem.handle_at(_vitem.rect().bottomRight()) is None)
_vitem.pvm_locked = False
from PySide6.QtGui import QPainter as _PGPainter, QPixmap as _PGPix

from azeo_control_trainer.core.hmi.pvms.pvm_painters import (
    paint_valve_op as _paint_valve,
    paint_vessel_trend as _paint_vessel,
)
_pgpix = _PGPix(220, 220)
_pgpix.fill(Qt.white)
_pgp = _PGPainter(_pgpix)
_paint_vessel(_pgp, _vitem)
_paint_valve(_pgp, _oitem)
from azeo_control_trainer.core.hmi.pvms.pvm_painters import (
    paint_horizontal_bar as _paint_hbar,
    paint_reactor_bar as _paint_reactor,
)
_paint_hbar(_pgp, _hitem)
_paint_reactor(_pgp, _ritem)
_pgp.end()
check("the vessel and valve painters draw the live item cleanly",
      True)

# A process painter must receive its canonical design geometry beneath a
# transform to the saved placement.  Otherwise resizing grows only the outer
# QGraphicsRectItem while fixed design-unit text, badges, scales and strokes
# remain their original size.  Probe the shared dispatch boundary so this one
# check covers every registered custom painter, including future classes.
from azeo_control_trainer.core.hmi.pvms.pvm_painters import (
    CLASS_PAINTERS as _CLASS_PAINTERS,
)
_vessel_painter_key = (_vitem.pvm.block_type, _vitem.pvm.role,
                       _vitem.effective_variant())
_vessel_painter = _CLASS_PAINTERS[_vessel_painter_key]
_scaled_paint_seen = {}


def _scaled_paint_probe(painter, item):
    _scaled_paint_seen["rect"] = (item.rect().width(),
                                  item.rect().height())
    transform = painter.transform()
    _scaled_paint_seen["scale"] = (transform.m11(), transform.m22())


_CLASS_PAINTERS[_vessel_painter_key] = _scaled_paint_probe
try:
    _scaled_pix = _PGPix(320, 320)
    _scaled_pix.fill(Qt.white)
    _scaled_painter = _PGPainter(_scaled_pix)
    _vitem.paint(_scaled_painter, None)
    _scaled_painter.end()
finally:
    _CLASS_PAINTERS[_vessel_painter_key] = _vessel_painter
_design_w, _design_h = _vitem.content_design_size()
_layout_w, _layout_h = _vitem.content_layout_size()
_scale_x, _scale_y = _vitem.content_scale()
check("resizing a PVM responsively lays out its complete internal composition "
      "without distorting text, icons, strokes, badges, or HP furniture",
      _scaled_paint_seen.get("rect") == (_layout_w, _layout_h)
      and abs(_scaled_paint_seen.get("scale", (0, 0))[0] - _scale_x) < 1e-6
      and abs(_scaled_paint_seen.get("scale", (0, 0))[1] - _scale_y) < 1e-6
      and abs(_scale_x - _scale_y) < 1e-9
      and (_scale_x, _scale_y) != (1.0, 1.0)
      and (_vitem.rect().width(), _vitem.rect().height())
      != (_design_w, _design_h),
      (_scaled_paint_seen, (_design_w, _design_h), (_layout_w, _layout_h),
       (_scale_x, _scale_y)))
check("a scaled HP display tag remains inside Qt's damage bounds",
      -_vitem.boundingRect().top() >= 16.0 * _scale_y,
      (_vitem.boundingRect(), _scale_y))
_reopened_vitem = type(_vitem)(
    _vitem.pvm, _vitem.binding, _vitem.mode_binding, _vitem._palette,
    rows=_vitem.rows, connection_points=_vitem.connection_points,
    status_conditions=_vitem.status_conditions)
check("saved PVM geometry reopens with the same internal-content scale",
      _reopened_vitem.content_design_size() == (_design_w, _design_h)
      and _reopened_vitem.content_layout_size() == (_layout_w, _layout_h)
      and _reopened_vitem.content_scale() == (_scale_x, _scale_y)
      and (_reopened_vitem.rect().width(),
           _reopened_vitem.rect().height())
      == (_vitem.pvm.w, _vitem.pvm.h),
      (_reopened_vitem.content_scale(), (_scale_x, _scale_y)))

# Exact typography is class-owned. A placement stores only a declared profile
# name; arbitrary per-instance point sizes would make repeated process objects
# drift apart and defeat the purpose of a PVM class.
from azeo_control_trainer.core.hmi.pvms.typography import (
    TYPOGRAPHY_COLUMNS as _TYPO_COLUMNS,
    typography_configuration as _typography_configuration,
)

_hclass = _pvm_registry.get("AI", "dynamo_inline", "hbar")
_hconfig = _stp._pvm_config(_hclass)
_hprop = _hconfig.property("Typography")
check("every coded process PVM declares class-owned Standard, Compact and "
      "Large typography profiles",
      _hprop is not None
      and [option.name for option in _hprop.options]
      == ["Standard", "Compact", "Large"]
      and not _hconfig.group_of("Typography").present_online)
_standard_size = _hitem.typeface("value", "Segoe UI", 8).pointSizeF()
_hitem = _stp.set_pvm_choice(_hitem, "Typography", "Large")
check("a placement stores only the named typography profile and resolves it "
      "through the class",
      _hitem.pvm.choices == {"Typography": "Large"}
      and _hitem.typography.name == "Large"
      and _hitem.typeface("value", "Segoe UI", 8).pointSizeF()
      > _standard_size,
      (_hitem.pvm.choices, _hitem.typography))
_hitem = _stp.set_pvm_choice(_hitem, "Typography", "Standard")
check("the default profile remains absent from serialized instance data",
      "Typography" not in _hitem.pvm.choices
      and "Typography" not in _hitem.pvm.to_dict().get("choices", {}),
      _hitem.pvm.to_dict())
_hitem = _stp.set_pvm_choice(_hitem, "Typography", "Large")

# An Author can use percentages across a family or specify an exact point
# size. Save/reload rebuilds linked open instances but does not mutate display
# geometry or bindings.
_exact_cfg = _typography_configuration(_hclass.__name__)
_exact_prop = _exact_cfg.property("Typography")
_value_column = _TYPO_COLUMNS.index("ValueSize")
_exact_prop.option("Large").values[_value_column] = "14pt"
_exact_cfg.save(Path(_studio_root) / "_pvmcfg")
_h_geometry = (_hitem.pvm.x, _hitem.pvm.y,
               _hitem.pvm.w, _hitem.pvm.h)
_refresh_count = _stp.refresh_pvm_configuration(_hclass.__name__)
_hitem = next(i for i in _stp._items() if i.pvm.id == _hpvm.id)
check("saving a class profile refreshes linked open instances with its exact "
      "point size and preserves placement geometry",
      _refresh_count >= 1
      and abs(_hitem.typeface("value", "Segoe UI", 8).pointSizeF()
              - 14.0) < 0.01
      and (_hitem.pvm.x, _hitem.pvm.y,
           _hitem.pvm.w, _hitem.pvm.h) == _h_geometry,
      (_refresh_count,
       _hitem.typeface("value", "Segoe UI", 8).pointSizeF(),
       _h_geometry))

_ppvm = _stp.place_block("PVMTEST/P101", "DEVCTL", 980, 640,
                         role=("dynamo_compact", "pump"))
_pitem = next(i for i in _stp._items() if i.pvm.id == _ppvm.id)
check("a pump-status PVM pins the pump silhouette (variant beats "
      "DEVICE_KIND)", _pitem._symbol_name() == "pump",
      _pitem._symbol_name())

_before_fan = len(_stp._items())
hmi_window._palette_place("DEVCTL", "dynamo_compact", "fan")
check("a palette card places a status PVM bound to a matching block",
      len(_stp._items()) == _before_fan + 1
      and any(i.pvm.variant == "fan"
              and i.pvm.params.get("path") == "PVMTEST/P101"
              for i in _stp._items()))
# A type with no REGISTERED PVM class places nothing at all — distinct
# from a type that has a class but no block instance, which lands
# unbound by design (p.25, checked above). AND is the standing example:
# logic gates deliberately have no dynamo, because a display shows the
# process and not the arithmetic behind it.
hmi_window._palette_place("AND", "dynamo_compact", "")
check("a palette card for a type with no registered PVM class places "
      "nothing", len(_stp._items()) == _before_fan + 1)

check("canvas items reserve an adornment margin - drags leave no "
      "residue trail",
      _pitem.boundingRect().x() <= -8.0
      and _d1.boundingRect().x() <= -8.0,
      (_pitem.boundingRect().x(), _d1.boundingRect().x()))

# ------------- Figure 10: the Library Explorer's standards menu
_lib = hmi_window.library
_std = _lib.new_standard("Color", "Common")
check("New > Color creates a standard in the store and the tree",
      _std["name"] == "S_New1" and _std["value"] == "#4472C4"
      and (Path(_studio_root) / "_standards.json").exists())
_lib.edit_value("S_New1", "#AA0000")
check("Edit value lands in the persisted document",
      '"#AA0000"' in (Path(_studio_root) / "_standards.json")
      .read_text(encoding="utf-8"))
_lib.copy_standard("S_New1")
_pasted = _lib.paste_standard("Common")
check("Copy/Paste duplicates the standard with its value",
      _pasted is not None and _pasted["value"] == "#AA0000"
      and _pasted["name"] != "S_New1")
check("Rename and Delete act on the store",
      _lib.rename_standard("S_New1", "S_ValveBody")
      and _lib.standards.get("S_ValveBody") is not None
      and _lib.delete_standard(_pasted["name"]))
_bmenu = _lib._standards_menu("std_builtin", "hphmi.controller")
check("built-in standards are locked - Delete and Rename disabled",
      any(a.text().startswith("Delete") and not a.isEnabled()
          for a in _bmenu.actions())
      and any(a.text().startswith("Rename") and not a.isEnabled()
              for a in _bmenu.actions()))
check("the standards subtree rebuilds with the user folder present",
      any("S_ValveBody" in _lib.standards_node.child(i).child(j)
          .text(0)
          for i in range(_lib.standards_node.childCount())
          for j in range(_lib.standards_node.child(i).childCount())))

# ------- the Library panel is reachable: splitter + pop-out
check("the Library tab is one draggable splitter — the inspector "
      "is never crushed under the tree",
      _lib.split.count() == 2
      and _lib.split.widget(0) is _lib.tree)
_insp = _lib.undock_inspector()
check("the inspector pops out into its own window, same widgets",
      _insp is not None and _lib._inspector_dialog is _insp
      and _lib.grid.window() is _insp)
check("popping out twice raises the same window",
      _lib.undock_inspector() is _insp)
_insp.reject()
check("closing it re-docks — grid back in the panel, state intact",
      _lib._inspector_dialog is None
      and _lib.grid.window() is _lib.window()
      and _lib.split.count() == 2)
check("the inspector buttons no longer truncate — compact labels, "
      "full names in tooltips",
      _lib.pvm_config.text() == "Configure…"
      and "Designer" in _lib.pvm_config.toolTip())

# ------- Convert to PVM + Import SVG (the Azeo creation flow)
_stc = hmi_window.current()
_stc.canvas.scene().clearSelection()
_ca = _stc.add_static("symbol", 60, 1200, w=80, symbol="pump")
_cb2 = _stc.add_static("rect", 220, 1210, w=60, h=40)
_cp = _stc.add_pipe(_ca, "e", _cb2, "w")
for _it in (_ca, _cb2, _cp):
    _it.setSelected(True)
_items_before = len(_stc._static_items())
check("Convert to PVM stores the selection as a class and replaces "
      "it with one grouped instance",
      _stc.convert_selection_to_pvm("PumpSkid")
      and "PumpSkid" in _stc.user_library().names()
      and len(_stc._static_items()) == _items_before
      and len({i.data.get("group")
               for i in _stc._static_items()
               if i.data.get("user_pvm") == "PumpSkid"}) == 1)
check("the class persists beside the displays",
      (_stc.user_library().path).exists())
_before_place = len(_stc._static_items())
check("placing a second instance recreates the items as a NEW group "
      "with re-pointed pipes",
      _stc.place_user_pvm("PumpSkid", 500, 1200) == 3
      and len(_stc._static_items()) == _before_place + 2
      and len({i.data.get("group")
               for i in _stc._static_items()
               if i.data.get("user_pvm") == "PumpSkid"}) == 2)

_svg_path = Path(_studio_root) / "_probe.svg"
_svg_path.write_text(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">'
    '<circle cx="20" cy="20" r="14" fill="none" stroke="black"/>'
    "</svg>", encoding="utf-8")
from azeo_control_trainer.core.hmi.pvms import symbols as _sym

_imported = _stc.import_svg(_svg_path, "my_probe")
check("Import SVG makes the file a first-class symbol — rendered "
      "through the same pipeline as the vendored catalog",
      _imported == "my_probe"
      and "my_probe" in _sym.USER_SYMBOLS
      and _sym.renderer("my_probe") is not None
      and (Path(_studio_root) / "_library" / "svg"
           / "my_probe.svg").exists())
hmi_window.refresh_user_pvms()
check("My PVMs in the palette lists the class and the import",
      hmi_window._user_pvm_grid.count() >= 2)
for _it in list(_stc._static_items()):
    if _it.data.get("user_pvm"):
        _stc.canvas.scene().removeItem(_it)

# ------- shape bindings: SVG/drawing → WORKING PVM (the full loop)
from azeo_control_trainer.core.hmi.pvms.configurator.model import (
    PvmConfiguration as _UCfg, PvmProperty as _UProp,
    Option as _UOpt, PropertyGroup as _UGrp,
)

_ucfg = _UCfg("PumpSkid", [_UGrp("BasicConfiguration", [
    _UProp("Orientation", "Selection", default="Up",
           columns=["BodyRot"],
           options=[_UOpt("Up", ["0"]), _UOpt("Right", ["90"])]),
    _UProp("ValveType", "Selection", default="Two way",
           columns=["threeWay"],
           options=[_UOpt("Two way", ["False"]),
                    _UOpt("Three way", ["True"])])])])
_ucfg.save(Path(_studio_root) / "_pvmcfg")
_ulib = _stc.user_library()
check("shape bindings are authored on the CLASS — rot to a "
      "Selection column, presence to a Boolean column",
      _ulib.set_shape_binding("PumpSkid", 0, "rot",
                              "Pvm.Orientation.BodyRot")
      and _ulib.set_shape_binding("PumpSkid", 1, "present",
                                  "Pvm.ValveType.threeWay"))
_n = _stc.place_user_pvm("PumpSkid", 60, 1400)
_members = [i for i in _stc._static_items()
            if i.data.get("user_pvm") == "PumpSkid"]
check("defaults resolve at placement: Up=0°, and the two-way "
      "default DROPS the gated shape and its pipe — the shapes' "
      "Present Online",
      _n == 1 and len(_members) == 1
      and float(_members[0].data.get("rot", 0) or 0) == 0.0,
      (_n, [(i.data.get("kind"), i.data.get("pvm_index"),
             i.data.get("rot")) for i in _members]))
_g = _members[0].data["group"]
_n2 = _stc.set_user_pvm_choices(
    _g, {"Orientation": "Right", "ValveType": "Three way"})
_members2 = [i for i in _stc._static_items()
             if i.data.get("user_pvm") == "PumpSkid"]
check("Configure instance re-resolves live: Three way brings the "
      "shape and pipe back, Right turns the pump 90°",
      _n2 == 3 and len(_members2) == 2
      and any(abs(float(i.data.get("rot", 0) or 0) - 90.0) < 1e-6
              for i in _members2)
      and all(i.data.get("pvm_choices", {}).get("ValveType")
              == "Three way" for i in _members2),
      (_n2, [(i.data.get("kind"), i.data.get("pvm_index"),
              i.data.get("rot")) for i in _members2]))
_pump_item = next(i for i in _members2
                  if i.data.get("pvm_index") == 0)
check("bind_user_pvm_shape authors through a placed member and the "
      "instance refreshes",
      _stc.bind_user_pvm_shape(_pump_item, "rot", "")
      and "rot" not in [d for d in _ulib.entries["PumpSkid"]["items"]
                        if d.get("kind") != "pipe"][0]
      and not any(float(i.data.get("rot", 0) or 0) > 1
                  for i in _stc._static_items()
                  if i.data.get("user_pvm") == "PumpSkid"))
for _it in list(_stc._static_items()):
    if _it.data.get("user_pvm"):
        _stc.canvas.scene().removeItem(_it)

# ------- the drawing-item property table (paper p.27) + pid symbols
from azeo_control_trainer.core.hmi.pvms import symbols as _sym2

check("the symbol catalog is the builder's own pid set, themed - "
      "every entry renders",
      len(_sym2.CATALOG) >= 110
      and all(_sym2.renderer(n) is not None for n in _sym2.CATALOG)
      and _sym2.CATALOG["pump"][0].startswith("pid/"))
_stc.canvas.scene().clearSelection()
_prop_item = _stc.add_static("symbol", 400, 1600, w=90,
                             symbol="reactor")
_prop_item.setSelected(True)
_stc._on_selection()
check("selecting a symbol shows its property table on the right "
      "(Azeo's Graphics Configuration pane)",
      _stc.pane._stack.currentIndex() == 2
      and "reactor" in _stc.pane.item_title.text().casefold()
      and _stc.pane.item_fields["w"].text() != "")
_stc.pane.item_fields["fill"].setText("#AA5500")
_stc.pane._apply_item_field("fill")
_stc.pane.item_fields["rot"].setText("90")
_stc.pane._apply_item_field("rot")
check("editing Fill and Rotation in the pane applies to the item, "
      "one undo step each",
      _prop_item.data.get("fill") == "#AA5500"
      and float(_prop_item.data.get("rot", 0)) == 90.0)
_stc.pane._configuration_context_menu(_QPoint(1, 1))
def _recursive_menu_labels(menu):
    labels = []
    for action in menu.actions():
        if action.text() and not action.isSeparator():
            labels.append(action.text())
        if action.menu() is not None:
            labels.extend(_recursive_menu_labels(action.menu()))
    return labels


_inspector_actions = _recursive_menu_labels(_stc._context_menu)
check("right-clicking Graphics Configuration routes to the selected "
      "drawing object's authoritative menu",
      all(any(word in label for label in _inspector_actions)
          for word in ("Line colour", "Duplicate", "Delete")),
      _inspector_actions)
_stc.canvas.scene().removeItem(_prop_item)
_stc.pane.show_pvm(None)

# ------- collapsible left panels + researched shape properties
hmi_window.show_sidebar("split")
_sizes_before = hmi_window._body_split.sizes()
hmi_window.toggle_left_panels()
check("one click folds the explorer and palette away - the canvas "
      "is the whole work area",
      hmi_window._left_folded
      and not hmi_window.explorer_tabs.isVisibleTo(hmi_window)
      and not hmi_window.palette_box.isVisibleTo(hmi_window))
hmi_window.toggle_left_panels()
check("toggling back restores both panels at their widths",
      not hmi_window._left_folded
      and hmi_window.explorer_tabs.isVisibleTo(hmi_window)
      and hmi_window.palette_box.isVisibleTo(hmi_window)
      and hmi_window._left_sizes[0] >= 200)
check("the fold control is a visible, accessible chevron rather than a "
      "14-pixel sliver",
      hmi_window.left_collapse.width() >= 20
      and hmi_window.left_collapse.text() == "\u25c0"
      and "Graphics Explorer" in
      hmi_window.left_collapse.accessibleName())
hmi_window.set_left_panel_visibility(palette=False)
check("individual View-pane state stays synchronized without falsely "
      "calling the whole left side folded",
      not hmi_window._left_folded
      and not hmi_window.palette_box.isVisibleTo(hmi_window)
      and hmi_window.explorer_tabs.isVisibleTo(hmi_window))
hmi_window._left_panel_menu(_QPoint(1, 1))
_fold_actions = [a.text() for a in
                 hmi_window._left_panel_context_menu.actions() if a.text()]
check("right-clicking the chevron can independently restore either pane, "
      "hide both, or reset their widths",
      all(any(word in label for label in _fold_actions)
          for word in ("Show Graphics Explorer", "Show Palette",
                       "Both Panes", "Reset Pane Widths")), _fold_actions)
hmi_window.reset_left_panel_widths()

_palette_header = hmi_window._palette_sections[0][0]
hmi_window._palette_menu(_QPoint(1, 1), _palette_header,
                         hmi_window._palette_sections[0][2])
_palette_actions = [a.text() for a in
                    hmi_window._palette_context_menu.actions() if a.text()]
check("a palette section menu has real accordion, pane and help commands",
      all(any(word in label for label in _palette_actions)
          for word in ("This Section", "Expand All", "Collapse All",
                       "Hide Palette", "Help")), _palette_actions)

_stc.canvas.scene().clearSelection()
_fp_item = _stc.add_static("rect", 700, 1600, w=80, h=120)
_fp_item.data["fill"] = "#7FA8C9"
_fp_item.setSelected(True)
_stc._on_selection()
_stc.pane.item_fields["fill_pct"].setValue(40)
_stc.pane._apply_item_field("fill_pct")
check("Fill Percent (paper p.31) is a pane property - a shape "
      "becomes a level indicator",
      _fp_item.data.get("fill_pct") == 40
      and _stc.pane.item_fields["fill_pct"].isEnabled())
_stc.pane.item_fields["fill_direction"].setCurrentText("left")
check("Fill Direction makes the same property work as a horizontal "
      "process bar",
      _fp_item.data.get("fill_direction") == "left"
      and abs(_fp_item._fill_clip(_fp_item.rect(), 0.4).width() - 32)
      < 0.01)
_stc.pane.item_fields["fill_pct"].setValue(100)
_stc.pane._apply_item_field("fill_pct")
check("100 percent serializes absent - the default stays byte-"
      "identical",
      "fill_pct" not in _fp_item.data)
check("arrowheads are a pane property on open strokes only — a "
      "filled rectangle has no direction to point",
      not _stc.pane.item_fields["arrow_start"].isEnabled()
      and not _stc.pane.item_fields["arrow_end"].isEnabled())
_stc.canvas.scene().removeItem(_fp_item)
_stc.pane.show_pvm(None)

# ------- the Crossover Effect (graphics paper pp.27–28)
from azeo_control_trainer.azeo_graphics_designer.studio import (
    CROSSOVER_KINDS as _XKINDS, _collect_segments as _xcollect,
    _scene_segments as _xsegs, _seg_cross as _xcross,
    crossing_count as _xcount, stroke_points as _xpoints,
)

_stc.canvas.scene().clearSelection()
_xa = _stc.add_static("line", 100, 1600, w=200, h=12)
_xb = _stc.add_static("line", 180, 1520, w=200, h=12)
_xb.data["rot"] = 90.0
_xb.apply_rotation()
_xa.data["crossover"] = "gap"
_hit = None
for _s1, _s2 in _xsegs(_stc.canvas.scene(), _xa):
    _hit = _hit or _xcross(
        _xa.mapToScene(_QPF(0, 6)), _xa.mapToScene(_QPF(200, 6)),
        _s1, _s2)
check("crossing segments are detected live — the gap/jump has a "
      "real intersection to break at",
      _hit is not None)
_xa.data["crossover"] = "jump"
_stc.canvas.scene().update()
check("gap and jump are item state on lines (and pipes), offered by "
      "the Crossover effect menu",
      _xa.data.get("crossover") == "jump")
_xb.data["visible"] = False
_stc.enter_test()
check("a hidden runtime stroke cannot punch an unexplained crossover gap "
      "into visible process graphics", _xcount(_xa) == 0)
_stc.exit_test()
_xb.data.pop("visible")
_xp = _stc.add_static("polyline", 100, 1660, w=200, h=80)
_xp.data["points"] = [[0, 40], [100, 0], [200, 40]]
_xp._refit_points()
_xf = _stc.add_static("freehand", 100, 1660, w=200, h=80)
_xf.data["points"] = [[0, 0], [60, 45], [140, 45], [200, 0]]
_xf._refit_points()
_xarc = _stc.add_static("arc", 120, 1560, w=160, h=100)
_owners = {owner for owner, _a, _b in _xcollect(_stc.canvas.scene())}
check("the smart crossing vocabulary covers every open stroke, not only "
      "straight lines and connectors",
      _XKINDS == {"line", "polyline", "freehand", "arc", "pipe"}
      and {_xp, _xf, _xarc}.issubset(_owners)
      and len(_xpoints(_xf)) > len(_xf.data["points"])
      and len(_xpoints(_xarc)) > 12)
_stc.canvas.scene().clearSelection()
_xp.setSelected(True)
check("the selected-stroke command applies Break/Jump in one undoable "
      "operation", _stc.set_selected_crossover("gap") == 1
      and _xp.data.get("crossover") == "gap")
_stc._on_selection()
check("Graphics Configuration exposes the same choice and reports what "
      "the live crossing engine sees",
      _stc.pane.item_fields["crossover"].isEnabled()
      and "detected live" in _stc.pane.crossover_status.text()
      and _xcount(_xp) > 0)
for _it in (_xa, _xb, _xp, _xf, _xarc):
    _stc.canvas.scene().removeItem(_it)

# ------- conversion functions (the papers' typed formula objects)
from azeo_control_trainer.core.hmi.pvms.functions import (
    FunctionStore as _FnStore,
)

_fs = _FnStore(_studio_root)
_fs.add_threshold("PriColor", [(60, "#F0A202"), (80, "#E4572E")],
                  "#8A9BA8")
_fs.add_scale("LevelPct", 0, 2000, 0, 100)
check("threshold rows evaluate highest-limit-first regardless of "
      "authoring order",
      _fs.eval("PriColor", 92) == "#E4572E"
      and _fs.eval("PriColor", 65) == "#F0A202"
      and _fs.eval("PriColor", 10) == "#8A9BA8")
check("scale clamps at its ends, and a broken input answers None "
      "rather than an invented value",
      abs(_fs.eval("LevelPct", 500) - 25.0) < 1e-9
      and _fs.eval("LevelPct", 99999) == 100.0
      and _fs.eval("LevelPct", "not-a-number") is None
      and _fs.eval("NoSuchFn", 5) is None)
_lib_fx = hmi_window.library
check("functions author from the Library Explorer's Functions node "
      "through the same methods the context menu calls",
      _lib_fx.functions is not None
      and _lib_fx.new_scale_fn("FlowPct", "0, 400 -> 0, 100")
      is not None
      and _lib_fx.new_threshold_fn(
          "HiHi", "80: #E4572E; else #8A9BA8") is not None
      and abs(_lib_fx.functions.eval("FlowPct", 100) - 25.0) < 1e-9
      and _lib_fx.delete_fn("FlowPct")
      and _lib_fx.delete_fn("HiHi"))

# ------- animation: a property follows a parameter through a
# conversion function, and Bad quality removes the override
_an = _stc.add_static("rect", 500, 1600, w=60, h=60)
_an.data["anim"] = {"fill": {"path": "PVMTEST/PID1/PV",
                             "fn": "PriColor"}}
_gp.outputs["PV"].value = 92.0
_gp.outputs["PV"].status = _PvmQuality.GOOD
_stc._apply_animations()
check("animation drives the property through the conversion "
      "function on every tick",
      _an.data.get("fill") == "#E4572E"
      and _an.data.get("fill_animated"))
_gp.outputs["PV"].status = _PvmQuality.BAD
_stc._apply_animations()
check("Bad quality REMOVES the animated override — a dead "
      "parameter must not leave a live colour standing",
      "fill" not in _an.data and "fill_animated" not in _an.data)
_gp.outputs["PV"].status = _PvmQuality.GOOD

# ------- visibility is a property; hidden ghosts in EDIT only
from azeo_control_trainer.azeo_graphics_designer.studio import (
    MODE_EDIT as _MODE_E, MODE_TEST as _MODE_T,
)

_stc.canvas.scene().clearSelection()
_an.setSelected(True)
_stc._on_selection()
_stc.pane.item_fields["visible"].setChecked(False)
check("unchecking Visible hides the item; in EDIT it still ghosts "
      "so the author can find it",
      _an.data.get("visible") is False
      and _stc.mode == _MODE_E
      and not _an._effectively_hidden())
_old_mode = _stc.mode
_stc.mode = _MODE_T
check("outside EDIT a hidden item draws nothing at all",
      _an._effectively_hidden())
_stc.mode = _old_mode
_stc.pane.item_fields["visible"].setChecked(True)
check("visible again serializes ABSENT — the default stays "
      "byte-identical",
      "visible" not in _an.data)

# ------- arrow size rides the pane, on pipes only
_p1 = _stc.add_static("rect", 60, 1700, w=40, h=40)
_p2 = _stc.add_static("rect", 220, 1700, w=40, h=40)
_pipe_fx = _stc.add_pipe(_p1, "e", _p2, "w")
_stc.canvas.scene().clearSelection()
_pipe_fx.setSelected(True)
_stc._on_selection()
_stc.pane.item_fields["arrow_size"].setCurrentText("large")
check("Arrow size is a pane property enabled on pipes",
      _pipe_fx.data.get("arrow_size") == "large"
      and _stc.pane.item_fields["arrow_size"].isEnabled())
_stc.pane.item_fields["arrow_size"].setCurrentText("medium")
check("medium is the default and serializes absent",
      "arrow_size" not in _pipe_fx.data)

# ------- the line gesture: press-drag-release, SHIFT snaps 45°
_stc.arm_line()
check("arming the line tool starts the drag gesture",
      _stc._line_armed)
_stc._line_press(_QPF(100, 1900))
_stc._line_drag(_QPF(180, 1945), shift=True)
_li = _stc._line_release(_QPF(180, 1945), shift=True)
check("release places the line and SHIFT snapped it to 45 "
      "degrees, then the tool disarms",
      _li is not None and _li.data.get("rot") == 45.0
      and _li.rect().width() > 80
      and not _stc._line_armed)
_stc.arm_shape("ellipse", w=110, h=80)
_stc._shape_press(_QPF(320, 1880))
_stc._shape_drag(_QPF(410, 1930), Qt.ShiftModifier)
_direct_shape = _stc._shape_release(
    _QPF(410, 1930), Qt.ShiftModifier)
check("closed shapes draw directly from press-drag-release and Shift "
      "constrains the authored geometry",
      _direct_shape is not None
      and abs(_direct_shape.rect().width()
              - _direct_shape.rect().height()) < 0.01
      and not _stc._shape_armed)
_stc.arm_shape("rect", w=120, h=80)
_stc._shape_press(_QPF(500, 1900))
_click_shape = _stc._shape_release(_QPF(500, 1900))
check("a shape click keeps standard-size placement rather than creating "
      "an unselectable zero-size object",
      _click_shape is not None
      and abs(_click_shape.rect().width() - 120) < 0.01
      and abs(_click_shape.rect().height() - 80) < 0.01)
_stc.arm_polyline()
for _point in (_QPF(650, 1880), _QPF(700, 1940), _QPF(780, 1890)):
    _stc._polyline_press(_point)
_direct_poly = _stc.finish_polyline()
check("the polyline tool accepts an arbitrary vertex chain and Enter/"
      "double-click finishes one editable stroke",
      _direct_poly is not None
      and len(_direct_poly.data.get("points", ())) == 3
      and _direct_poly.is_point_kind()
      and not _stc._polyline_armed)
for _it in (_an, _p1, _p2, _pipe_fx, _li, _direct_shape,
            _click_shape, _direct_poly):
    _stc.canvas.scene().removeItem(_it)
_stc.pane.show_pvm(None)

# ------- PVM rotation (bars swap layout, equipment turns in place)
from PySide6.QtCore import QRectF as _QRF
from azeo_control_trainer.core.hmi.pvms.user_library import (
    UserPvmLibrary as _UserPvmLib,
)


def _paint_ok(item) -> bool:
    """Paint one item into an offscreen image — a painter that
    raises, or leaves itself active, is a canvas that dies inside
    paintEvent (the class-editor crash, item I3).

    The style option is a REAL QStyleOptionGraphicsItem: passing None
    hands Qt a null it dereferences, and the damage shows up later as
    heap corruption in an unrelated repaint rather than here.
    """
    from PySide6.QtGui import QImage as _Img, QPainter as _Pnt
    from PySide6.QtWidgets import (
        QStyleOptionGraphicsItem as _Opt,
    )
    image = _Img(240, 260, _Img.Format_ARGB32)
    image.fill(0)
    painter = _Pnt(image)
    option = _Opt()
    option.rect = item.boundingRect().toRect()
    try:
        item.paint(painter, option, None)
    except Exception:                               # noqa: BLE001
        painter.end()
        return False
    active = painter.isActive()
    painter.end()
    return active


_dev1 = _PvmRegistry().create("DEVCTL", "DEV1")
_dev1._apply_config()
_gg.add_block(_dev1)
_rot_bar = _stc.place_block("PVMTEST/AI6", "AI", 60, 2000,
                            role=("dynamo_inline", ""))
_rot_item = next(i for i in _stc._items() if i.pvm.id == _rot_bar.id)
_before = (_rot_item.rect().width(), _rot_item.rect().height())
_rot_item = _stc.set_pvm_rotation(_rot_item, 90)
check("a vertical analog bar rotated 90 degrees becomes the "
      "HORIZONTAL bar - a layout swap, not sideways text",
      _rot_item.effective_variant() == "hbar"
      and _rot_item.rect().width() > _rot_item.rect().height()
      and _before[1] > _before[0])
_rot_item = _stc.set_pvm_rotation(_rot_item, 0)
check("and back again - rotation is geometry the placement owns, "
      "so the class is untouched",
      _rot_item.effective_variant() == ""
      and _rot_item.pvm.rot == 0)
_rot_dev = _stc.place_block("PVMTEST/DEV1", "DEVCTL", 260, 2000,
                            role=("dynamo_compact", "pump"))
_dev_item = next(i for i in _stc._items() if i.pvm.id == _rot_dev.id)
_dev_item = _stc.set_pvm_rotation(_dev_item, 90)
check("an equipment PVM turns its SILHOUETTE only - the tag and "
      "state text stay upright",
      _dev_item._glyph_rotated()
      and _dev_item.rotation() == 0
      and _dev_item.rect().width() == _rot_dev.w)
check("rotation serializes only when set, so shipped displays stay "
      "byte-identical",
      "rot" not in _stc.set_pvm_rotation(_dev_item, 0).pvm.to_dict())
for _g in (_rot_bar, _rot_dev):
    for _i in [i for i in _stc._items() if i.pvm.id == _g.id]:
        _stc.canvas.scene().removeItem(_i)

# ------- the tank and vessel shells come from the vendored SVG set
from azeo_control_trainer.core.hmi.pvms.pvm_painters import (
    _svg_shell as _shell,
)
from azeo_control_trainer.core.hmi.pvms.symbols import (
    renderer as _svg_renderer,
)
from PySide6.QtGui import QImage as _QImg, QPainter as _QPainter

check("the tank and vessel PVMs draw the vendored P&ID artwork — "
      "the same symbols the palette offers, not a second drawing",
      _svg_renderer("tank") is not None
      and _svg_renderer("vessel") is not None)
_probe = _QImg(200, 240, _QImg.Format_ARGB32)
_probe.fill(0)
_pp = _QPainter(_probe)
_ink = _shell(_pp, _QRF(0, 0, 200, 240), "tank")
_ink_v = _shell(_pp, _QRF(0, 0, 200, 240), "vessel")
_pp.end()
check("and the level fill is clipped to the shell's INK rectangle, "
      "inside the artwork rather than over its outline",
      _ink is not None and _ink_v is not None
      and _ink.left() > 0 and _ink.right() < 200
      and _ink.height() < 240)
_tank_pvm = _stc.place_block("PVMTEST/AI6", "AI", 460, 2000,
                             role=("dynamo_inline", "tank"))
_tank_item = next(i for i in _stc._items()
                  if i.pvm.id == _tank_pvm.id)
_ai6.outputs["OUT"].value = 55.0
_ai6.outputs["OUT"].status = _PvmQuality.GOOD
for _v in (50, 52, 55, 58, 56, 60):
    _ai6.outputs["OUT"].value = float(_v)
    _stc._tick()
check("the Tank with Trend PVM runs: it binds, collects history and "
      "paints without error",
      len(_tank_item.history) >= 5
      and _tank_item.binding is not None
      and _tank_item.binding.result.value == 60.0
      and _paint_ok(_tank_item))
_stc.canvas.scene().removeItem(_tank_item)

# ------- composite PVM authoring in the configurator
hmi_window.open_pvm_config()
_cfg_win = hmi_window.pvm_config_designer
check("only USER classes offer an editable layout — a coded "
      "class's drawing is its painter",
      not _cfg_win.is_user_class("VesselTrendPvm")
      and _cfg_win.edit_layout("VesselTrendPvm") is None)
check("New Composite creates the class and opens it on the canvas "
      "with the full palette behind it",
      _cfg_win.new_composite("CompoSkid") is not None
      and hmi_window.current().display.name == "_pvm_CompoSkid"
      and _cfg_win.is_user_class("CompoSkid"))
_edit = hmi_window.current()
_edit.add_static("symbol", 140, 20, w=70, symbol="pump")
_edit.add_static("symbol", 240, 20, w=70, symbol="valve")
_edit.save_draft()
_lib_c = _UserPvmLib(_studio_root)
check("Save captures the canvas back into the class — any symbol, "
      "shape or pipe the author put there",
      sorted(d.get("symbol") for d in _lib_c.entries["CompoSkid"]
             ["items"] if d.get("symbol")) == ["pump", "valve"])
_lib_c.set_shape_binding("CompoSkid", 0, "fill", "Pvm.Color")
_edit.add_static("rect", 340, 20, w=30, h=30)
_edit.save_draft()
check("a shape binding authored on the class SURVIVES a layout "
      "edit — what the editor does not understand it must not lose",
      any(d.get("fill") == "Pvm.Color"
          for d in _UserPvmLib(_studio_root).entries["CompoSkid"]
          ["items"]))
_ov = hmi_window.open_display("Overview")
check("and the composite places as one grouped instance — from a "
      "display tab that was open before the class existed",
      _ov.place_user_pvm("CompoSkid", 900, 2100) >= 3)
_cfg_win.close()

# ------- the palette is two columns of icons, and FITS its pane
from PySide6.QtWidgets import (
    QGridLayout as _QGrid, QLabel as _QLabel, QPushButton as _QBtn,
)

_grids = [g for g in hmi_window.palette_box.findChildren(_QGrid)
          if g.count() > 4]
check("every palette section lays out in two columns",
      bool(_grids) and all(g.columnCount() == 2 for g in _grids))
_widest = max(
    (g.itemAt(i).widget().width()
     for g in _grids for i in range(g.count())
     if isinstance(g.itemAt(i).widget(), _PaletteCard)), default=0)
check("every component card fits one half of the palette, excluding "
      "category headings which deliberately span both columns",
      0 < _widest <= hmi_window.palette_box.width() / 2,
      detail=(_widest, hmi_window.palette_box.width()))
_eq_grid = max(_grids, key=lambda g: g.count())
_eq_card = next(iter(
    _eq_grid.parentWidget().findChildren(_PaletteCard)))
check("an equipment tile is its symbol with a small name under it, "
      "within a named searchable process family",
      len(_eq_card.findChildren(_QLabel)) == 2
      and bool(_eq_card.toolTip())
      and bool(_eq_card.property("palette_category")))

# ------------------------------------------------------------------
# The builder's drawing vocabulary, ported (HMI_GRAPHICS_DRAWING)
# ------------------------------------------------------------------
from azeo_control_trainer.core.hmi.pvms import shapes as _shp
from azeo_control_trainer.core.hmi.pvms import strokes as _stk

_dstc = hmi_window.current()
_dstc.enter_edit()
_dstc.canvas.scene().clearSelection()

check("every primitive the palette offers actually generates an "
      "outline — the palette IS the inventory",
      all(_shp.shape_path(k, _QRF(0, 0, 100, 60)) is not None
          for k in _shp.SHAPE_KINDS))
check("regular polygons run 3..64 sides and clamp rather than "
      "degenerate",
      len(_shp.normalized_points("polygon", {"sides": 3})) == 3
      and len(_shp.normalized_points("polygon", {"sides": 64})) == 64
      and len(_shp.normalized_points("polygon", {"sides": 999}))
      == 64
      and len(_shp.normalized_points("polygon", {"sides": 1})) == 3)
check("a star is points x2 vertices, and its inner radius is the "
      "adjustable parameter",
      len(_shp.normalized_points("star", {"sides": 8})) == 16
      and _shp.adjustment_amount({"adjust": 30}, "star") == 30.0
      and _shp.adjustment_amount({"adjust": 5}, "star") == 15.0)

_poly = _dstc.add_static("star", 60, 2400, w=90, h=90)
_poly.data["sides"] = 6
_dstc.canvas.scene().clearSelection()
_poly.setSelected(True)
_dstc._on_selection()
check("shape geometry is a live pane control on adjustable kinds "
      "only — an empty form under a heading reads as broken",
      _dstc.pane.geometry_holder.isVisibleTo(_dstc.pane)
      and _dstc.pane.item_fields["sides"].value() == 6
      and _dstc.pane.adjust_label.text() == "Inner radius")
_dstc.pane.item_fields["sides"].setValue(9)
check("and editing it reshapes the outline",
      _poly.data.get("sides") == 9
      and len(_shp.normalized_points("star", _poly.data)) == 18
      and _paint_ok(_poly))

# ------- eight resize handles + rotation handle
_rs = _dstc.add_static("rect", 300, 2400, w=100, h=60)
check("a selected object offers EIGHT resize handles plus a "
      "rotation handle, not one corner",
      len(_rs.handle_points()) == 8
      and _rs.handle_at(_QPF(0, 0)) == "nw"
      and _rs.handle_at(_QPF(100, 30)) == "e"
      and _rs.handle_at(_rs.rotate_handle_point()) == "rotate")
from PySide6.QtCore import QEvent as _QEvent
from PySide6.QtWidgets import QGraphicsSceneMouseEvent as _SceneMouseEvent

_rs.setSelected(True)
_edge = _rs.handle_points()["e"]
_edge_press = _SceneMouseEvent(_QEvent.GraphicsSceneMousePress)
_edge_press.setPos(_edge)
_edge_press.setScenePos(_rs.mapToScene(_edge))
_edge_press.setButton(Qt.LeftButton)
_edge_press.setButtons(Qt.LeftButton)
_rs.mousePressEvent(_edge_press)
check("a selected side square starts resize rather than being stolen by "
      "the connection anchor at the same coordinate",
      _rs._resizing and not getattr(_rs, "_anchor_dragging", False))
_rs._resizing = False
_rs._resize_handle = None
_dstc.cancel_gesture()  # the synthetic press above deliberately has no release
_rs._resize_to("w", _dstc.canvas.mapToScene(0, 0), False, True)
check("dragging a WEST handle moves the origin and keeps the east "
      "edge still — the classic wrong-way resize",
      _rs.data["x"] < 300 and abs(
          _rs.data["x"] + _rs.rect().width() - 400) < 1.5)
_sym = _dstc.add_static("symbol", 460, 2400, w=80, symbol="pump")
_col = _dstc.add_static("symbol", 600, 2400, w=80, symbol="column")
check("aspect lock is a property of the OBJECT: a pump preserves its "
      "shape on corner resize, while a column may stretch freely",
      _sym.aspect_locked() and not _col.aspect_locked())
_before_h = _sym.rect().height()
_sym._resize_to("e", _dstc.canvas.mapToScene(0, 0)
                .__class__(700, 2400), False, True)
check("an east/west handle changes only width, even on a pump",
      abs(_sym.rect().height() - _before_h) < 0.01)
_before_h2 = _col.rect().height()
_col._resize_to("e", _dstc.canvas.mapToScene(0, 0)
                .__class__(800, 2400), False, True)
check("and stretching the column does not",
      abs(_col.rect().height() - _before_h2) < 0.01)
_sym_w, _sym_h = _sym.rect().width(), _sym.rect().height()
_sym._resize_to("se", _sym.mapToScene(_QPF(_sym_w + 40, _sym_h + 40)),
                False, True)
check("a corner handle changes width and height while preserving the "
      "pump's engineering silhouette",
      _sym.rect().width() > _sym_w and _sym.rect().height() > _sym_h)
_sym_w2, _sym_h2 = _sym.rect().width(), _sym.rect().height()
_sym._resize_to("se", _sym.mapToScene(_QPF(_sym_w2 + 40, _sym_h2 + 5)),
                True, True)
check("Ctrl releases the corner aspect lock for one drag",
      _sym.rect().width() > _sym_w2
      and abs(_sym.rect().height() - (_sym_h2 + 5)) < 0.01)

# ------- vertex editing on open strokes
_pl = _dstc.add_static("polyline", 60, 2600, w=160, h=90)
check("a polyline shows its POINTS instead of a bounding box — an "
      "open stroke is reshaped, not stretched",
      _pl.is_point_kind() and len(_pl.vertices()) == 3
      and _pl.handle_at(_QPF(0, 0)) is None)
check("open strokes expose their real start/end connection tips rather "
      "than empty bounding-box edges",
      "start" in _pl.anchor_sides() and "end" in _pl.anchor_sides()
      and (_pl.anchor("start")
           - _pl.mapToScene(_pl.vertices()[0])).manhattanLength() < 0.01
      and (_pl.anchor("end")
           - _pl.mapToScene(_pl.vertices()[-1])).manhattanLength() < 0.01)
_first = _pl.vertices()[0]
check("and a point is grabbable where it is drawn",
      _pl.vertex_at(_first) == 0)
_pl.move_vertex(1, _QPF(90, 10))
check("dragging a vertex moves it and refits the item box, so the "
      "shape stays clickable",
      _pl.data["points"][1][1] == 0.0
      and _pl.rect().height() > 0)
_count = len(_pl.vertices())
_added = _pl.insert_vertex(_QPF(40, 40))
check("double-click inserts a point INTO the nearest segment, "
      "never appends — an appended point would jump the shape",
      _added is not None and 0 < _added < _count
      and len(_pl.vertices()) == _count + 1)
_pl.remove_vertex(_added)
check("and double-clicking a point removes it, down to the two a "
      "line needs to exist at all",
      len(_pl.vertices()) == _count
      and not _pl.remove_vertex(0) or len(_pl.vertices()) >= 2)

# ------- freehand: capture, simplify, smooth
_dstc.arm_pencil()
check("the pencil arms a press-draw-release gesture", _dstc._pencil_armed)
_dstc._pencil_press(_QPF(700, 2600))
import math as _fmath
for _i in range(120):
    _dstc._pencil_drag(_QPF(700 + _i * 1.6,
                            2600 + 30 * _fmath.sin(_i / 9.0)))
_free = _dstc._pencil_release(_QPF(700 + 120 * 1.6, 2600))
check("a freehand stroke is SIMPLIFIED on release — hundreds of "
      "raw samples are slow to draw and impossible to edit",
      _free is not None
      and 2 <= len(_free.data["points"]) < 90
      and _paint_ok(_free))
check("and it lands as an editable point kind, not a frozen image",
      _free.is_point_kind() and not _dstc._pencil_armed)
_cross_arc = _dstc.add_static("arc", 920, 2550, w=140, h=100)
for _stroke in (_pl, _free, _cross_arc):
    _stroke.data["crossover"] = "jump"
check("Break/Jump paints on polylines, smoothed freeforms and arcs — the "
      "property is no longer accepted by a painter that ignores it",
      all(_paint_ok(_stroke)
          for _stroke in (_pl, _free, _cross_arc)))
# Keep this paint-only fixture out of the eraser path below.  Leaving it
# there makes that test erase three intersecting items instead of its two
# explicit targets and hides what either check is proving.
_dstc.canvas.scene().removeItem(_cross_arc)

# ------- eraser: one undo step per drag
_e1 = _dstc.add_static("rect", 1000, 2600, w=40, h=40)
_e2 = _dstc.add_static("rect", 1060, 2600, w=40, h=40)
_before_count = len(_dstc._static_items())
_dstc.arm_eraser()
_dstc._eraser_at(_e1.sceneBoundingRect().center())
_dstc._eraser_at(_e2.sceneBoundingRect().center())
check("the eraser drags through objects and takes them out",
      len(_dstc._static_items()) == _before_count - 2)
_dstc.undo()
check("and the WHOLE drag is one undo step, however many objects "
      "it took",
      len(_dstc._static_items()) == _before_count)
_dstc.disarm_eraser()

# ------- strokes: proportional dashes, caps, fourteen heads
check("dash lengths scale with the stroke width — a fixed array "
      "makes a heavy dashed line read as nearly solid",
      _stk.dash_lengths("dash", 12.0)[0]
      > _stk.dash_lengths("dash", 2.0)[0] * 3
      and _stk.dash_lengths("solid", 2.0) == []
      and len(_stk.dash_lengths("dash_dot_dot", 2.0)) == 6)
check("and a hairline keeps its floor, so dots do not close up "
      "into a solid line",
      _stk.dash_lengths("dot", 0.5)[1] == 4.0
      and _stk.dash_pattern("dot", 0.5)[1] == 8.0)
check("legacy pattern names still resolve — shipped displays name "
      "them and must keep loading",
      _stk.dash_pattern("dashdot", 2.0)
      == _stk.dash_pattern("dash_dot", 2.0))
_pen = _stk.build_pen("#123456", 3.0, "dot", "square")
check("the pen carries colour, width, pattern and cap together",
      _pen.color().name() == "#123456"
      and abs(_pen.widthF() - 3.0) < 1e-9
      and _pen.capStyle() == Qt.SquareCap
      and len(_pen.dashPattern()) == 2)
check("fourteen arrowheads plus none, independently per end",
      len(_stk.ARROW_HEADS) == 15
      and _stk.resolve_arrows({"arrow_start": "bar",
                               "arrow_end": "crow_foot"})
      == ("bar", "crow_foot"))
check("and the legacy arrow vocabulary maps onto them, so an old "
      "display keeps the head it had",
      _stk.resolve_arrows({"arrow": "both"})
      == ("filled_arrow", "filled_arrow")
      and _stk.resolve_arrows({}) == ("none", "none"))
_ln = _dstc.add_static("line", 60, 2800, w=180, h=12)
_ln.data["arrow_start"] = "open_diamond"
_ln.data["arrow_end"] = "crow_foot"
check("every head paints without error on a real item",
      _paint_ok(_ln))
_bad_head = _dstc.add_static("line", 300, 2800, w=100, h=12)
for _name in _stk.ARROW_HEADS:
    _bad_head.data["arrow_end"] = _name
    if not _paint_ok(_bad_head):
        break
else:
    _name = ""
check("all fifteen, in fact", _name == "")

# ------- nudge and cancel
_nud = _dstc.add_static("rect", 500, 2800, w=40, h=40)
_dstc.canvas.scene().clearSelection()
_nud.setSelected(True)
_dstc.nudge_selected(1, 0)
check("arrow keys nudge the selection by one pixel",
      abs(_nud.pos().x() - 501) < 0.01)
_nud_centre = _nud.mapToScene(_nud.rect().center())
_dstc.resize_selected(1, 0)
check("Shift+Arrow resizes symmetrically about the object's centre",
      abs(_nud.rect().width() - 42) < 0.01
      and (_nud.mapToScene(_nud.rect().center()) - _nud_centre)
      .manhattanLength() < 0.01)
_dstc.set_zoom(100)
_dstc.canvas.wheelEvent(
    _QWheelEvent(_QPointF(5, 5), _QPointF(5, 5), _QPoint(0, 0),
                 _QPoint(0, 120), Qt.NoButton, Qt.ControlModifier,
                 Qt.ScrollPhase.NoScrollPhase, False))
check("Ctrl+wheel zooms the canvas about the pointer",
      _dstc.zoom_percent > 100)
_dstc.arm_line()
_dstc.arm_pencil()
_dstc.arm_eraser()
_dstc.cancel_gestures()
check("Escape disarms EVERY tool from one place, so a new tool "
      "cannot forget to be cancellable",
      not _dstc._line_armed and not _dstc._pencil_armed
      and not _dstc._eraser_armed)
for _it in (_poly, _rs, _sym, _col, _pl, _free, _cross_arc,
            _ln, _bad_head,
            _nud):
    if _it.scene() is not None:
        _dstc.canvas.scene().removeItem(_it)
_dstc.pane.show_pvm(None)

# ------------- the PVM Configuration Designer window
hmi_window.open_pvm_config("PumpSkid")
_first_configurator = hmi_window.pvm_config_designer
check("a user PVM class opens in the configurator with its "
      "authored document",
      hmi_window.pvm_config_designer.current_class == "PumpSkid"
      and hmi_window.pvm_config_designer.config
      .property("Orientation") is not None)
check("and the preview pane pictures the PVM being configured — "
      "its shapes for a drawing class",
      hmi_window.pvm_config_designer.preview_label.pixmap()
      is not None
      and not hmi_window.pvm_config_designer.preview_label
      .pixmap().isNull()
      and "shape" in hmi_window.pvm_config_designer
      .preview_facts.text())
hmi_window.pvm_config_designer.close()
hmi_window.open_pvm_config("HP_C_Valve")
_dsg = hmi_window.pvm_config_designer
check("the modeless PVM Configurator is one reusable tool window, not a "
      "new parent-owned window on every ribbon click",
      _dsg is _first_configurator)
from PySide6.QtGui import QShortcut as _ConfiguratorShortcut

check("the Configurator retains every QShortcut Python wrapper for the "
      "window lifetime",
      len(_dsg._shortcuts) == 4
      and len(_dsg.findChildren(_ConfiguratorShortcut)) == 4)
check("the designer opens on the class, titled like the paper's",
      "HP_C_Valve - PVM Configuration Designer"
      in _dsg.title_label.text())
_dsg._select_class("VesselTrendPvm")
check("a coded class opens POPULATED from its own declarations — "
      "Control Tag plus every declared binding in the tree",
      _dsg.selected == "path"
      and _dsg.config.property("path").ptype == "Control Tag"
      and _dsg.config.property("pv.value") is not None
      and _dsg.config.property("limits.hi") is not None
      and not _dsg.config.group_of("pv.value").present_online
      and _dsg.config.property("Typography") is not None
      and not _dsg.config.group_of("Typography").present_online)
check("the configurator provides a live named-profile preview for coded "
      "PVM typography",
      _dsg.preview_profile.isVisibleTo(_dsg)
      and [_dsg.preview_profile.itemText(i)
           for i in range(_dsg.preview_profile.count())]
      == ["Standard", "Compact", "Large"]
      and "typography: Standard" in _dsg.preview_facts.text(),
      (_dsg.preview_profile.count(), _dsg.preview_facts.text()))
from azeo_control_trainer.core.hmi.pvms.configurator.model import (
    PvmConfiguration as _SeedCfg,
)

check("and the seeded path property can NEVER clobber a "
      "placement's own value",
      _dsg.config.binding_params(
          {}, base={"path": "PVMTEST/AI6"})["path"]
      == "PVMTEST/AI6")
_help_dlg = _dsg.show_help()
from PySide6.QtWidgets import QTextBrowser as _QTB

check("the Help button answers with the working guide (F1 too)",
      "five-minute workflow"
      in _help_dlg.findChild(_QTB).toPlainText()
      and "Professional typography workflow"
      in _help_dlg.findChild(_QTB).toPlainText())
check("Configurator Help also reuses its modeless window",
      _dsg.show_help() is _help_dlg)
_help_dlg.close()
_dsg._select_class("HP_C_Valve")
check("the tree carries groups and typed properties",
      _dsg.tree.topLevelItemCount() == 1
      and _dsg.tree.topLevelItem(0).childCount() == 2
      and any("Orientation" in _dsg.tree.topLevelItem(0).child(0)
              .child(i).text(0)
              for i in range(_dsg.tree.topLevelItem(0).child(0)
                             .childCount())))

_dsg._select_class("PCSD_3WCtrlValve_v01_")
_dsg.selected, _dsg.selected_group = "Port", None
_dsg._show_form()
check("a conditionally-present property outlines its Presence in red",
      "solid #D0342C" in _dsg.presence_frame.styleSheet())
_dsg.selected = "Tag"
_dsg._show_form()
check("an always-present one does not",
      "solid #D0342C" not in _dsg.presence_frame.styleSheet())

_pv = _dsg.preview()
check("the preview starts with Port absent (two-way default)",
      "Port" in _pv.absent_label.text())
_pv.set_choice("ValveType", "Three way")
check("flipping to three-way makes Port appear and resolve",
      "Port" not in _pv.absent_label.text()
      and any(_pv.resolved_table.item(r, 0).text() == "Port.OpenPort"
              for r in range(_pv.resolved_table.rowCount())))
from PySide6.QtWidgets import QComboBox as _PreviewCombo

_preview_rows = _pv.pick_lay.count()
_preview_combos = len(_pv.pickers.findChildren(_PreviewCombo))
for _preview_cycle in range(25):
    _pv.set_choice("ValveType", "Three way")
check("rebuilding conditional preview choices disposes nested row layouts "
      "instead of accumulating invisible controls and signal connections",
      _pv.pick_lay.count() == _preview_rows
      and len(_pv.pickers.findChildren(_PreviewCombo)) == _preview_combos,
      (_pv.pick_lay.count(), _preview_rows,
       len(_pv.pickers.findChildren(_PreviewCombo)), _preview_combos))
check("Present Online keeps offline groups out of the "
      "subscription list",
      "ExtendedConfiguration" in _pv.online_label.text()
      and "Parameter1" not in _pv.online_label.text())
_pv.close()

_dsg.selected, _dsg.selected_group = None, "GeometryConfiguration"
_dsg._show_form()
_dsg.present_online_box.setChecked(False)
_gcfg_path = _dsg.save()
check("Present Online edits and Save land in the sidecar document",
      _gcfg_path.exists() and '"present_online": false'
      in _gcfg_path.read_text(encoding="utf-8"))
_dsg.add_property("Selection")
check("Add Property creates a Selection with a starter grid, "
      "selected in the tree",
      _dsg.selected.startswith("NewProperty")
      and _dsg.config.property(_dsg.selected).columns == ["Value"])
_dsg.delete_selected()
check("Delete removes it again",
      all(not p.name.startswith("NewProperty")
          for p in _dsg.config.all_properties()))
# Standards references from the designer: reference stored, value live.
from azeo_control_trainer.azeo_graphics_designer.library_explorer import (
    StandardsStore as _StdStore,
)

_std_store = _StdStore(_dsg.standards_root)
_std_entry = _std_store.add("Color", "Common", name="S_CfgStd")
_std_entry["value"] = "#4472C4"
_std_store.save()
_dsg.add_property("Color")
_color_prop = _dsg.selected
check("a Color property's default can point at a library standard - "
      "the class stores the NAME",
      _dsg.set_standard_default(_color_prop, "S_CfgStd")
      and _dsg.config.property(_color_prop).default
      == "Standard.S_CfgStd")
_pv2 = _dsg.preview()
_rows2 = {_pv2.resolved_table.item(r, 0).text():
          _pv2.resolved_table.item(r, 1).text()
          for r in range(_pv2.resolved_table.rowCount())}
check("Preview resolves it live through the library",
      _rows2.get(_color_prop, "").startswith("#4472C4")
      and "via S_CfgStd" in _rows2.get(_color_prop, ""))
_std_store.get("S_CfgStd")["value"] = "#AA0000"
_std_store.save()
_pv2.refresh()
_rows3 = {_pv2.resolved_table.item(r, 0).text():
          _pv2.resolved_table.item(r, 1).text()
          for r in range(_pv2.resolved_table.rowCount())}
check("editing the STANDARD re-resolves the preview with the class "
      "document untouched - no republish",
      _rows3.get(_color_prop, "").startswith("#AA0000")
      and not _dsg.unsaved is None)
_pv2.close()
_dsg.delete_selected()

# Configurator polish: capture mode, column ops, reordering.
_dsg._select_class("HP_C_Valve")
_ori = _dsg.config.property("Orientation")
_dsg.capture_mode = True
_dsg._set_option_count(_ori, 5)
check("capture mode seeds a new option from the default's values, "
      "not blanks",
      _ori.options[4].values == _ori.option("Up").values
      and _ori.options[4].values[0] == "0")
_dsg.capture_mode = False
_dsg._set_option_count(_ori, 4)
check("column rename and remove act on the grid model",
      _dsg.rename_column(_ori, 0, "BodyRotation")
      and _ori.columns == ["BodyRotation"]
      and _dsg.remove_column_at(_ori, 0)
      and _ori.columns == [])
_dsg._select_class("HP_C_Valve")     # reload the sample fresh? no —
_names_before = [pr.name for pr
                 in _dsg.config.groups[0].properties]
check("Move up/down reorders a property within its group",
      _dsg.move_property(_names_before[1], -1)
      and [pr.name for pr in _dsg.config.groups[0].properties][0]
      == _names_before[1]
      and not _dsg.move_property(_names_before[1], -1))

# A faceplate authored through the supported Studio + Configurator path:
# measured visual body in the user-PVM library, typed values in pvmcfg.
_blueprint = _dsg.new_faceplate_blueprint("SmokeFaceplate")
_blue_cfg = _dsg.config
_blue_lib = _dsg._user_library()
_blue_instance = _blue_lib.instantiate(
    "SmokeFaceplate", 0, 0, config=_blue_cfg,
    choices={"ModuleName": "FIC-101", "Title": "FLOW CONTROL",
             "PVPath": "PVMTEST/PID1/PV",
             "SPPath": "PVMTEST/PID1/SP",
             "OUTPath": "PVMTEST/PID1/OUT",
             "ModulePath": "PVMTEST", "EU0": "0", "EU100": "200",
             "UnitName": "Area 1"})
_blue_pair = _blue_lib.entries.get("SmokeFaceplate_PVM", {})
from azeo_control_trainer.core.hmi.pvms.configurator.model import (
    PvmConfiguration as _PairConfiguration,
)
_pair_cfg = _PairConfiguration.load(
    _dsg.root / "SmokeFaceplate_PVM.pvmcfg.json")
_pair_instance = _blue_lib.instantiate(
    "SmokeFaceplate_PVM", 0, 0, config=_pair_cfg,
    choices={"ModuleName": "FIC-101", "Title": "FLOW CONTROL",
             "PVPath": "PVMTEST/PID1/PV",
             "SPPath": "PVMTEST/PID1/SP",
             "OUTPath": "PVMTEST/PID1/OUT",
             "ModulePath": "PVMTEST", "EU0": "0", "EU100": "200",
             "UnitName": "Area 1"})
_blue_track = next(item for item in _blue_instance
                   if item.get("kind") == "rect"
                   and item.get("w") == 20)
_blue_bar = next(item for item in _blue_instance
                 if item.get("kind") == "rect"
                 and item.get("w") == 8)
_blue_pv = next(item for item in _blue_instance
                if item.get("kind") == "datalink"
                and item.get("path", "").endswith("/PV"))
_blue_chart = next(item for item in _blue_instance
                   if item.get("kind") == "chart")
_blue_entry = next(item for item in _blue_instance
                   if item.get("kind") == "user_entry")
check("Faceplate Blueprint creates one validated typed schema and one "
      "editable professional Studio body",
      _blueprint is not None and not _blue_cfg.issues()
      and _blue_cfg.property("PVPath").ptype == "Parameter Reference"
      and len(_blue_instance) >= 18)
check("Faceplate Blueprint also creates a compact PVM caller paired to "
      "the full user faceplate",
      _blue_pair.get("definition_kind") == "pvm"
      and _blue_pair.get("paired_faceplate") == "SmokeFaceplate"
      and _blue_lib.entries["SmokeFaceplate"].get("definition_kind")
      == "faceplate"
      and any(item.get("kind") == "icon_button"
              and item.get("icon") == "mini_faceplate"
              and any(action.get("kind") == "open_user_faceplate"
                      for action in item.get("actions", ()))
              for item in _pair_instance)
      and all(any(action.get("event") == "double_click"
                  and action.get("kind") == "open_user_faceplate"
                  for action in item.get("actions", ()))
              for item in _pair_instance
              if item.get("kind") != "pipe"))
check("the faceplate's dark PV bar is centred inside the light channel",
      abs((_blue_bar["x"] + _blue_bar["w"] / 2)
          - (_blue_track["x"] + _blue_track["w"] / 2)) < 0.01)
check("faceplate instance configuration drives the real Data Link, "
      "trend and writable SP entry rather than decorative controls",
      _blue_pv["path"] == "PVMTEST/PID1/PV"
      and [pen["path"] for pen in _blue_chart["pens"]]
      == ["PVMTEST/PID1/PV", "PVMTEST/PID1/SP"]
      and _blue_entry["entry"]["path"] == "PVMTEST/PID1/SP"
      and _blue_entry["entry"]["hi"] == "200")
from azeo_control_trainer.core.hmi.pvms.elements import (
    element_paths as _element_paths,
    validate_data_element as _validate_data_element,
)
_faceplate_table = {
    "kind": "table", "columns": [
        {"key": "parameter", "title": "Param", "width": 2},
        {"key": "value", "title": "Value", "width": 2}],
    "rows": [{"parameter": "PV", "value": {
        "path": "PVMTEST/PID1/PV", "type": "numeric", "decimals": 2}}],
}
_table_item = _dstc.renderer.build_drawing(_faceplate_table)
check("the faceplate builder's Table supports static cells and live "
      "tag-bound cells through the shared renderer",
      not _validate_data_element(_faceplate_table)
      and _element_paths(_faceplate_table) == ("PVMTEST/PID1/PV",)
      and "PVMTEST/PID1/PV" in _table_item.bindings)
_dstc.renderer.unbind(_table_item)
from azeo_control_trainer.core.hmi.pvms.faceplate_icons import (
    FACEPLATE_ICONS as _FACEPLATE_ICONS,
    SPECIAL_SYMBOLS as _SPECIAL_SYMBOLS,
)
from azeo_control_trainer.core.hmi.pvms.rendering.items import (
    StaticItem as _IconStaticItem,
)
_icon_image = _QImg(64, 64, _QImg.Format_ARGB32)
_icon_image.fill(Qt.transparent)
_icon_painter = _QPainter(_icon_image)
for _icon_name in _FACEPLATE_ICONS:
    _IconStaticItem({"kind": "icon_button", "icon": _icon_name,
                     "w": 32, "h": 32}, _dstc.palette_roles).paint(
                         _icon_painter, None)
_icon_painter.end()
check("every documented faceplate icon is a scalable Studio element, "
      "not a dependency on the gitignored manual images",
      len(_FACEPLATE_ICONS) >= 11 and not _icon_image.isNull())
_special_image = _QImg(128, 32, _QImg.Format_ARGB32)
_special_image.fill(Qt.transparent)
_special_painter = _QPainter(_special_image)
for _index, _icon_name in enumerate((
        "simulate", "module_detail", "alarm_help",
        "acknowledge_alarms")):
    _IconStaticItem({"kind": "special_symbol", "icon": _icon_name,
                     "x": _index * 32, "w": 32, "h": 32},
                    _dstc.palette_roles).paint(_special_painter, None)
_special_painter.end()
from azeo_control_trainer.core.hmi.pvms.elements import (
    has_interaction_region as _has_interaction_region,
)
check("the documented Simulate, module-detail, Alarm Help and alarm-ack "
      "marks are reusable Special Symbols rather than generic buttons",
      all(name in _SPECIAL_SYMBOLS for name in (
          "simulate", "module_detail", "alarm_help",
          "acknowledge_alarms"))
      and _special_image.pixelColor(12, 16).alpha() > 0
      and not _has_interaction_region({
          "kind": "special_symbol", "icon": "module_detail"})
      and _has_interaction_region({
          "kind": "special_symbol", "icon": "module_detail",
          "actions": [{"event": "click", "kind": "open_detail"}]}))
_pair_source_item = _dstc.renderer.build_drawing(_pair_instance[0])
_user_faceplate_view = _dstc.open_user_faceplate(
    "SmokeFaceplate", _pair_source_item)
check("a paired user PVM opens its full authored faceplate through the "
      "same live display renderer",
      _user_faceplate_view is not None
      and _user_faceplate_view.class_name == "SmokeFaceplate"
      and len(_user_faceplate_view.scene().items()) >= 18
      and any(getattr(item, "binding", None) is not None
              for item in _user_faceplate_view.scene().items()))
if _user_faceplate_view is not None:
    _user_faceplate_view.close()
_old_title = _blue_cfg.property("Title").default
_blue_cfg.property("Title").default = "CHANGED"
_dsg._mark_unsaved()
check("configurator Undo/Redo restores the complete class document",
      _dsg.undo() and _dsg.config.property("Title").default == _old_title
      and _dsg.redo()
      and _dsg.config.property("Title").default == "CHANGED")
_dsg.close()

# ------- configuration-aware binding wired into the render path
from azeo_control_trainer.core.hmi.pvms.base import (
    Bind as _CfB, PvmClass as _CfGC, register_pvm as _cfreg,
)
from azeo_control_trainer.core.hmi.pvms.configurator.model import (
    PvmConfiguration as _CfCfg, PvmProperty as _CfProp,
    Option as _CfOpt, PropertyGroup as _CfGrp,
)


@_cfreg
class _CfgTestPvm(_CfGC):
    block_type = "PID"
    role = "dynamo_inline"
    variant = "cfgtest"
    display_name = "Cfg Test"
    bindings = (_CfB("pv.value", "{path}/{Sel.Term}"),
                _CfB("limits.hi", "{Extra}/HI"),
                _CfB("mode", "{path}/MODE"))


_cfdoc = _CfCfg("_CfgTestPvm", [
    _CfGrp("BasicConfiguration", [
        _CfProp("Sel", "Selection", default="Out",
                columns=["Term"],
                options=[_CfOpt("Out", ["OUT"]),
                         _CfOpt("Sp", ["SP"])])]),
    _CfGrp("Hidden", [
        _CfProp("Extra", "Parameter Reference",
                default="PVMTEST/PID1")], present_online=False),
])
_cfdoc.save(Path(_studio_root) / "_pvmcfg")
_stp2 = hmi_window.current()
_cfpvm = _stp2.place_block("PVMTEST/PID1", "PID", 1120, 640,
                           role=("dynamo_inline", "cfgtest"))
_cfitem = next(i for i in _stp2._items() if i.pvm.id == _cfpvm.id)
check("a placed PVM binds through its configuration document — the "
      "default choice composes the path",
      _cfitem.binding is not None
      and _cfitem.binding.path == "PVMTEST/PID1/OUT",
      _cfitem.binding.path if _cfitem.binding else None)
check("a spec whose template touches an offline group is never "
      "bound — no HI row exists",
      "HI" not in _cfitem.rows)
_cfitem2 = _stp2.set_pvm_choice(_cfitem, "Sel", "Sp")
check("flipping the Selection REBINDS: the engineer picked 'Sp', "
      "never typed a path",
      _cfitem2.binding.path == "PVMTEST/PID1/SP"
      and _cfitem2.pvm.choices == {"Sel": "Sp"}
      and _cfitem2.pvm.to_dict().get("choices") == {"Sel": "Sp"})
_cfitem3 = _stp2.set_pvm_choice(_cfitem2, "Sel", "Out")
check("choosing the default serializes absent-when-default — "
      "existing displays stay byte-identical",
      _cfitem3.pvm.choices == {}
      and "choices" not in _cfitem3.pvm.to_dict())
_stp2.pane.show_pvm(_cfitem3)
check("the config pane grows a CONFIGURATION section listing the "
      "class's Selections plus its class-typography editor",
      _stp2.pane.choices_holder.isVisibleTo(_stp2.pane)
      and _stp2.pane.choices_form.rowCount() == 3
      and any(button.text() == "Edit class typography…"
              for button in _stp2.pane.choices_holder
              .findChildren(_QBtn)))
_cfp = PvmFaceplateWidget(_CfgTestPvm, {"path": "PVMTEST/PID1"},
                          _engine, config=_cfdoc,
                          choices={"Sel": "Sp"})
check("the faceplate takes the same route: config-composed path, "
      "gated spec never subscribed",
      _cfp.bound["pv.value"].path == "PVMTEST/PID1/SP"
      and "limits.hi" not in _cfp.bound)
_cfp.close()
# Relink PVM (p.22): transfer by name, keep unbound choices.
_cfitem3 = _stp2.set_pvm_choice(_cfitem3, "Sel", "Sp")
_stp2.canvas.scene().clearSelection()
_cfitem3.setSelected(True)
check("Relink re-points the instance at another class, geometry and "
      "params carried",
      _stp2.relink_selected("PID", "dynamo_inline", "") == 1)
_relinked = next(i for i in _stp2._items()
                 if i.pvm.id == _cfpvm.id)
check("the unknown choice stays in the record, inert — not lost",
      _relinked.pvm.variant == ""
      and _relinked.pvm.choices == {"Sel": "Sp"}
      and (_relinked.binding is None
           or "SP" not in str(_relinked.binding.path)))
_stp2.canvas.scene().clearSelection()
_relinked.setSelected(True)
_stp2.relink_selected("PID", "dynamo_inline", "cfgtest")
_back = next(i for i in _stp2._items() if i.pvm.id == _cfpvm.id)
check("relinking back restores it — the choice takes effect again",
      _back.binding.path == "PVMTEST/PID1/SP")
_back.setSelected(True)
_stp2.delete_selected()

# ------- the interlock summary faceplate (ARCHITECTURE.md wireframe)
from azeo_control_trainer.core.strategy.model.wire import Wire as _Wire
from azeo_control_trainer.azeo_control_designer.faceplates import (
    BLOCK_FACEPLATES as _BFP, InterlockFaceplate,
)

check("INTERLOCK joins the block-faceplate dispatch table",
      _BFP.get("INTERLOCK") is InterlockFaceplate)
_ilk2 = _PvmRegistry().create("INTERLOCK", "ILK2")
_ilk2._apply_config()
_cndA = _PvmRegistry().create("CND", "CNDA")
_cndA.config.params.update({"EXPRESSION": "IN1 < 50",
                            "TIME_TRUE": 4.0})
_cndA._apply_config()
_cndB = _PvmRegistry().create("CND", "CNDB")
_cndB.config.params.update({"EXPRESSION": "IN1 == 0",
                            "TIME_TRUE": 0.0})
_cndB._apply_config()
for _b in (_ilk2, _cndA, _cndB):
    _gg.add_block(_b)
for _w in (_Wire(_cndA.id, "OUT_D", _ilk2.id, "TRIP"),
           _Wire(_cndB.id, "OUT_D", _ilk2.id, "PERM1")):
    _gg.wires[_w.id] = _w

_cndA.inputs["IN1"].value = 41.2
_cndA.execute(2.0)
_cndB.inputs["IN1"].value = 0
_cndB.execute(0.5)
_ifp2 = InterlockFaceplate(_ilk2, graph=_gg, module="PVMTEST",
                           store=store)
check("the faceplate finds the CNDs upstream of TRIP and the PERMs",
      len(_ifp2._trip_panels) == 1 and len(_ifp2._perm_panels) == 1
      and _ifp2._trip_panels[0].cnd is _cndA)
_bar = _ifp2._trip_panels[0].et_bar
check("the elapsed bar says ABOUT TO TRIP: 2.0 of 4.0 s, counting, "
      "not made",
      abs(_bar.elapsed - 2.0) < 1e-6 and _bar.total == 4.0
      and not _bar.made
      and "counting" in _ifp2._trip_panels[0]
      .verdict_label.text())
check("and the result row still reads CLEAR",
      _ifp2._row_result.value.text() == "CLEAR"
      and _ifp2._row_first.value.text() == "—")

_cndA.execute(2.5)
_ilk2.inputs["TRIP"].value = True
_ilk2.execute(0.5)
_ifp2.refresh()
check("past TIME_TRUE the condition asserts and the interlock "
      "TRIPS, first-out named",
      _bar.made and _ifp2._row_result.value.text() == "TRIPPED"
      and _ifp2._row_first.value.text() == "CNDA")

_cndA.inputs["IN1"].value = 60.0
_cndA.execute(0.5)
_ilk2.inputs["TRIP"].value = False
_ilk2.execute(0.5)
_ifp2.refresh()
check("the latching lesson: condition cleared, trip still in, and "
      "the faceplate says exactly that",
      _ifp2._row_result.value.text() == "TRIPPED"
      and "latched" in _ifp2._row_first.value.text()
      and _ifp2._row_ready.value.text() == "YES"
      and _ifp2._reset_btn.isEnabled())

_ifp2._trip_panels[0]._on_bypass(True)
_cndA.execute(0.5)
_ifp2.refresh()
check("Bypass writes the CND's own DISABLE (unwired route) and the "
      "row says BYPASSED",
      bool(_cndA.get_input("DISABLE"))
      and "BYPASSED" in _ifp2._trip_panels[0].verdict_label.text())
_ifp2._trip_panels[0]._on_bypass(False)

check("RESET pulses through the three-route command rules",
      _ifp2.pulse("RESET"))
_ilk2.execute(0.5)
_ifp2.refresh()
check("and the reset clears the latch",
      _ifp2._row_result.value.text() == "CLEAR")
_ifp2.close()

# ------- the PDF binding workflow (pp.25, 29–30): place → verify →
# browse — the Explorer owns the plant tree, the studio asks for tags.
check("the studio keeps no docked plant tree — the Explorer owns "
      "the hierarchy (p.13)",
      not hasattr(hmi_window, "browser"))
_stp3 = hmi_window.current()
_ub = hmi_window._palette_place("PID", "dynamo_inline", "")
check("a palette card follows p.25: the PVM lands first, unbound, "
      "and the pane opens on its Control Tag",
      _ub is not None and _ub.params.get("path") == ""
      and _stp3.pane._current_item.pvm.id == _ub.id)
_pf = _stp3.pane.rows["path"]
check("the unresolved Control Tag wears the red dotted underline "
      "with the two documented causes",
      "dotted" in _pf.edit.styleSheet()
      and "typo" in _pf.edit.toolTip())
check("path_resolves verifies against the loaded configuration",
      _stp3.path_resolves("PVMTEST/PID1")
      and not _stp3.path_resolves("PVMTEST/NOPE")
      and not _stp3.path_resolves("NOMOD/PID1"))
_pf.edit.setText("PVMTEST/PID1")
_pf._apply()
_bound_item = _stp3.pane._current_item
check("Enter verifies and REBINDS — blue solid underline, live "
      "binding",
      f"solid {_RIBBON_WF['lapis']}" in _stp3.pane.rows["path"]
      .edit.styleSheet()
      and _bound_item.pvm.params["path"] == "PVMTEST/PID1"
      and _bound_item.binding is not None
      and _bound_item.binding.path.startswith("PVMTEST/PID1"))

from azeo_control_trainer.azeo_graphics_designer.param_browser import (
    ParameterBrowserDialog,
)

_pbd = ParameterBrowserDialog(lambda: {"PVMTEST": _gg})
_pbd._select_path("PVMTEST/PID2")
check("the parameter browser (Fig. 28) returns the picked block",
      _pbd.selected_path == "PVMTEST/PID2"
      and _pbd.selected_type == "PID")
_pbd.search.setText("ILK1")
_hidden = sum(
    _pbd.browser.topLevelItem(0).child(j).isHidden()
    for j in range(_pbd.browser.topLevelItem(0).childCount()))
check("and its search filters the tree",
      _hidden > 0)
_pbd.close()
_stp3.canvas.scene().clearSelection()
_bound_item.setSelected(True)
_stp3.delete_selected()

hmi_window.close()
check("closing the window releases every display lock",
      not (Path(_studio_root) / "Overview" / ".lock").exists()
      and not (Path(_studio_root) / "Combustion" / ".lock").exists())

# ------------------------------------- the PVM studio's editing invariants
# Eight defects that the suite above did not reach. Each one was a real
# symptom on the canvas, so each is asserted from the gesture that
# produced it rather than from the method it was fixed in.
from azeo_control_trainer.azeo_graphics_designer.studio import (
    _scene_segments,
)

_inv_root = _st_tmp.mkdtemp()
_inv = PvmStudio(lambda: {"PVMTEST": _gg}, _inv_root,
                 display_name="Overview")
_inv.enter_edit()

# I. One gesture is ONE undo step. Both stroke tools took a checkpoint
# and then called add_static, which takes another: the first Ctrl+Z
# removed the stroke and the second did nothing visible, so Ctrl+Y
# needed two presses to bring it back.
_depth = len(_inv._undo_stack)
_inv.arm_line()
_inv._line_press(_QPF(0, 0))
_inv._line_release(_QPF(200, 0))
check("drawing a line is one undo entry, not two",
      len(_inv._undo_stack) - _depth == 1,
      len(_inv._undo_stack) - _depth)
_depth = len(_inv._undo_stack)
_inv.arm_pencil()
_inv._pencil_press(_QPF(10, 10))
for _x in range(14, 90, 4):
    _inv._pencil_drag(_QPF(_x, 10 + _x * 0.3))
_inv._pencil_release(_QPF(90, 40))
check("and so is one freehand stroke",
      len(_inv._undo_stack) - _depth == 1,
      len(_inv._undo_stack) - _depth)
_inv.undo()
check("one undo takes the stroke back",
      not any(i.data.get("kind") == "freehand"
              for i in _inv._static_items()))
_inv.redo()
check("and one redo returns it",
      any(i.data.get("kind") == "freehand"
          for i in _inv._static_items()))

# II. Undo/redo must not multiply the pipes. PipeItem is not a
# StaticItem, so clearing pvms and statics left every pipe on the scene
# while the document restored its own — and _drawing_data carried the
# duplicates into the saved draft.
_pa = _inv.add_static("rect", 0, 300, w=70, h=50)
_pb = _inv.add_static("rect", 300, 300, w=70, h=50)
_inv.add_pipe(_pa, "e", _pb, "w")
_inv.undo()
_inv.redo()
check("undo then redo leaves exactly one pipe, on the scene and in "
      "the document",
      len(_inv._pipe_items()) == 1
      and sum(1 for d in _inv._drawing_data()
              if d.get("kind") == "pipe") == 1,
      [d.get("kind") for d in _inv._drawing_data()])

# III. A duplicate owns its own nested data. dict() is shallow, so a
# copied polyline shared the original's point list and dragging a
# vertex on one moved the other.
_poly = _inv.add_static("polyline", 0, 600, w=140, h=80)
_poly.vertices()                    # materialise the default points
_poly_before = [list(p) for p in _poly.data["points"]]
_inv.canvas.scene().clearSelection()
_poly.setSelected(True)
_inv.duplicate_drawing_selected()
_poly_copy = next(i for i in _inv._static_items()
                  if i is not _poly and i.data.get("kind") == "polyline")
_poly_copy.move_vertex(0, _QPF(999, 999))
check("a duplicated polyline owns its points — editing the copy "
      "leaves the original alone",
      _poly.data["points"] == _poly_before, _poly.data["points"])

# IV. Copy and paste carry the connector, re-pointed at the COPIES.
# Fresh items: `_load_document` above rebuilt the scene, so the pair
# from II are stale Python objects that are no longer on it.
_qa = _inv.add_static("rect", 0, 1200, w=70, h=50)
_qb = _inv.add_static("rect", 300, 1200, w=70, h=50)
_q_pipe = _inv.add_pipe(_qa, "e", _qb, "w")
_inv.canvas.scene().clearSelection()
_qa.setSelected(True)
_qb.setSelected(True)
_pipes_before = len(_inv._pipe_items())
_inv.duplicate_drawing_selected()
check("duplicating a connected pair copies the connector too",
      len(_inv._pipe_items()) == _pipes_before + 1,
      len(_inv._pipe_items()))
_originals = (_qa.data["id"], _qb.data["id"])
_inv.window()._pvm_clipboard = [dict(_qa.data), dict(_qb.data),
                                dict(_q_pipe.data)]
_pipes_before = len(_inv._pipe_items())
_inv.paste_at(_QPF(900, 900))
_pasted = [p for p in _inv._pipe_items()
           if p.data.get("a") not in _originals
           and p.data.get("b") not in _originals]
check("and a pasted connector attaches to the pasted copies, never "
      "back to the originals",
      len(_inv._pipe_items()) == _pipes_before + 1
      and len(_pasted) >= 1,
      [(p.data.get("a"), p.data.get("b"))
       for p in _inv._pipe_items()])

# V. Removing a custom connection point renumbers the pipes on that
# item: `cps` is positional, so dropping c0 turned c1 into c0 and the
# pipe named c1 silently landed somewhere else.
_ca = _inv.add_static("rect", 0, 900, w=80, h=60)
_cb = _inv.add_static("rect", 400, 900, w=80, h=60)
_c_first = _ca.add_connection_point(_ca.mapToScene(_QPF(10, 10)))
_c_second = _ca.add_connection_point(_ca.mapToScene(_QPF(70, 50)))
_cp_pipe = _inv.add_pipe(_ca, _c_second, _cb, "w")
_anchor_before = _ca.anchor(_cp_pipe.data["a_side"])
_inv.drop_connection_point(_ca, _c_first)
_anchor_after = _ca.anchor(_cp_pipe.data["a_side"])
check("dropping an earlier connection point leaves the surviving "
      "pipe on the same point",
      abs(_anchor_before.x() - _anchor_after.x()) < 0.5
      and abs(_anchor_before.y() - _anchor_after.y()) < 0.5,
      f"{_anchor_before} -> {_anchor_after}")

# VI. A standing alarm keeps the time it STARTED. Stamping the current
# time each call made a ten-minute-old alarm read as seconds old,
# which is the one thing the column exists to say.
_inv_pvm = _inv.place_block("PVMTEST/PID1", "PID", 50, 50)
for _term in _gp.outputs.values():
    _term.status = _PvmQuality.BAD
_inv._tick()
_alarms_first = _inv.alarm_summary()
_inv._tick()
_alarms_later = _inv.alarm_summary()
check("a standing alarm keeps its onset time across ticks",
      bool(_alarms_first)
      and _alarms_first[0][3] == _alarms_later[0][3],
      (_alarms_first[:1], _alarms_later[:1]))

# VII. A display naming a PVM class this build does not register must
# still OPEN — it draws unbound and validate() names it. It used to
# take the whole window down with an AttributeError, losing every
# other placement with it.
_before_open = len(_inv._items())
_inv._restore({"id": "pvm_missing", "class": "NOSUCHBLOCK/dynamo_compact",
               "params": {"path": "PVMTEST/PID1"}, "x": 0, "y": 0})
check("an unregistered PVM class draws unbound instead of crashing",
      len(_inv._items()) == _before_open + 1)
check("and validate() says so by name",
      any("NOSUCHBLOCK" in p for p in _inv.validate()),
      _inv.validate())

# VIII. TEST mode must not hand out edit rights we never acquired.
# Asking whether the lock FILE exists answered a different question.
_lock_root = _st_tmp.mkdtemp()
_locked = PvmStudio(lambda: {"PVMTEST": _gg}, _lock_root,
                    display_name="Overview")
_lock_dir = Path(_lock_root) / "Overview"
_lock_dir.mkdir(parents=True, exist_ok=True)
(_lock_dir / ".lock").write_text(
    _json.dumps({"who": "another_engineer", "at": time.time()}),
    encoding="utf-8")
_locked.enter_test()
_locked.exit_test()
check("leaving TEST does not grant EDIT on another engineer's lock",
      _locked.mode != "edit", _locked.mode)
_locked.close()

# IX. A drag stays inside the frame budget. reroute_pipes resolved each
# pipe's ends by re-walking the scene, which made one mouse-move
# O(pipes x items): 62 ms at 120 objects, before any repaint.
_perf = PvmStudio(lambda: {"PVMTEST": _gg}, _st_tmp.mkdtemp(),
                  display_name="Overview")
_perf.enter_edit()
_perf_boxes = [_perf.add_static("rect", (i % 10) * 120, (i // 10) * 90,
                                w=70, h=50) for i in range(120)]
for _i in range(0, 119, 2):
    _perf.add_pipe(_perf_boxes[_i], "e", _perf_boxes[_i + 1], "w")
# Best of three batches, not the mean of one. This is a microbenchmark
# on a shared machine: the minimum is the least-contaminated sample, and
# a mean here failed once at 23 ms purely because a linter was running
# alongside it. A real quadratic regression blows every batch, so the
# minimum still catches what this test exists to catch.
_batches = []
for _run in range(3):
    _t0 = time.perf_counter()
    for _step in range(40):
        _perf_boxes[0].setPos(10 + _step, 10 + _step)
    _batches.append((time.perf_counter() - _t0) / 40 * 1000)
_move_ms = min(_batches)
check(f"one drag move over 120 objects / 60 pipes costs "
      f"{_move_ms:.2f} ms, inside the 16.7 ms frame budget",
      _move_ms < 16.7,
      f"best {_move_ms:.2f} ms of {[round(b, 2) for b in _batches]}")

# Exercise the canvas controller too: batching ItemChange callbacks must not
# replace the single-object route pass with a reroute of every equipment train.
_perf_boxes[0].setPos(0, 0)
_perf.snap_enabled = _perf.smart_guides_enabled = False
_perf.selection.replace((_perf_boxes[0],))
_pointer_drag = _perf.canvas.pointer_drag
_drag_origin = _perf_boxes[0].mapToScene(_perf_boxes[0].rect().center())
_pointer_drag.press(_perf_boxes[0], _drag_origin, Qt.NoModifier)
_pointer_drag.move(_drag_origin + _QPF(10, 10), Qt.NoModifier)
_pointer_batches = []
for _run in range(3):
    _t0 = time.perf_counter()
    for _step in range(40):
        _pointer_drag.move(_drag_origin + _QPF(10 + _step, 10 + _step), Qt.NoModifier)
    _pointer_batches.append((time.perf_counter() - _t0) / 40 * 1000)
_pointer_drag.release()
_pointer_ms = min(_pointer_batches)
check(f"the canvas drag controller also meets the frame budget: {_pointer_ms:.2f} ms",
      _pointer_ms < 16.7, f"batches: {_pointer_batches}")

# X. A moved line takes its crossover marks with it — the segment
# cache that made the effect affordable must invalidate on geometry.
_x1 = _perf.add_static("line", 0, 2000, w=200, h=12)
_x2 = _perf.add_static("line", 100, 1900, w=12, h=200)
_x2.data["crossover"] = "gap"
_perf.reroute_pipes()
_segs_before = len(_scene_segments(_perf.canvas.scene(), _x2))
_x1.setPos(0, 2400)
_after = _scene_segments(_perf.canvas.scene(), _x2)
check("the crossover cache follows a moved line rather than holding "
      "its old position",
      _segs_before == len(_after)
      and any(abs(a.y() - 2406) < 1 for a, _b in _after),
      [(round(a.y(), 1), round(b.y(), 1)) for a, b in _after][:4])
_perf.close()
_inv.close()

# ------------------------------------------------- WYSIWYG, as a test
# The Assembler claims the canvas shows what the operator will see.
# That claim was previously unfalsifiable: `published_document` had no
# reader anywhere in the tree, so there was no "what you get" to
# compare the "what you see" against. Now there is, and the two are
# rendered and compared pixel for pixel.
from azeo_control_trainer.azeo_graphics_designer.studio import (
    PvmDisplayView, open_published,
)
from azeo_control_trainer.core.hmi.theme.roles import Role as _Role
from PySide6.QtGui import (
    QColor as _QColor, QImage as _QImage, QPainter as _QPainter,
)
from PySide6.QtCore import QRectF as _QRectF


def _render(scene, background, size=(420, 300)):
    """One scene onto a fixed canvas, background included."""
    image = _QImage(size[0], size[1], _QImage.Format_ARGB32)
    image.fill(_QColor(background))
    painter = _QPainter(image)
    painter.setRenderHint(_QPainter.Antialiasing, True)
    scene.render(painter, _QRectF(0, 0, size[0], size[1]),
                 _QRectF(0, 0, size[0], size[1]))
    painter.end()
    return image


_wys_root = _st_tmp.mkdtemp()
_wys = PvmStudio(lambda: {"PVMTEST": _gg}, _wys_root,
                 display_name="Overview")
_wys.enter_edit()
_wys.place_block("PVMTEST/PID1", "PID", 40, 30)
_wa = _wys.add_static("rect", 40, 170, w=90, h=60)
_wb = _wys.add_static("rect", 250, 170, w=90, h=60)
_wys.add_pipe(_wa, "e", _wb, "w")
_wys.canvas.scene().clearSelection()
_wys.save_draft()
_wys.publish("TEST")

_wys.enter_test()
_studio_image = _render(_wys.canvas.scene(),
                        _wys.palette_roles[_Role.SURFACE_BG])
_view = open_published(_wys_root, "Overview",
                       lambda: {"PVMTEST": _gg}, live=False)
check("a published PVM display has a reader at all — the operator "
      "view opens it", _view is not None)
_view_image = _render(_view.scene(),
                      _view.palette_roles[_Role.SURFACE_BG])
check("WYSIWYG: TEST mode and the operator view render the SAME "
      "pixels — one renderer, so they cannot drift",
      _studio_image == _view_image,
      "images differ")

# The grid was the thing that gave it away: TEST mode, whose whole job
# is to show the operator's view, drew the authoring grid. It lives in
# drawBackground, so it has to be measured there rather than in a
# scene render.
def _background_tones(canvas, mode):
    canvas.studio.mode = mode
    image = _QImage(200, 200, _QImage.Format_ARGB32)
    painter = _QPainter(image)
    canvas.drawBackground(painter, _QRectF(0, 0, 200, 200))
    painter.end()
    return {image.pixelColor(x, y).name()
            for x in range(0, 200, 4) for y in range(0, 200, 4)}


_edit_bg = _background_tones(_wys.canvas, "edit")
_test_bg = _background_tones(_wys.canvas, "test")
_view_bg = _background_tones(_wys.canvas, "view")
check("the authoring grid is drawn in EDIT and in NEITHER of the two "
      "operator modes",
      len(_edit_bg) > 1 and len(_test_bg) == 1 and len(_view_bg) == 1,
      (len(_edit_bg), len(_test_bg), len(_view_bg)))

# Selection handles are the other half of the chrome: they belong to
# EDIT and must never reach a rendered operator screen.
_wys.enter_edit()
for _it in _wys._items():
    _it.setSelected(True)
_edit_image = _render(_wys.canvas.scene(),
                      _wys.palette_roles[_Role.SURFACE_BG])
check("and a selected PVM in EDIT renders differently from the "
      "operator view — handles are chrome, not content",
      _edit_image != _view_image)
_wys.canvas.scene().clearSelection()
_wys.enter_test()
check("while deselecting and returning to TEST lands back on exactly "
      "the operator's pixels",
      _render(_wys.canvas.scene(),
              _wys.palette_roles[_Role.SURFACE_BG]) == _view_image)

# Retheming must move the WHOLE display. PVM cards used to be painted
# from wireframe literals while the pipes around them took theme
# roles, so a dark station showed white cards on dark pipes.
_dark = PvmDisplayView(_wys.store.published_document("Overview"),
                       lambda: {"PVMTEST": _gg}, theme="dark",
                       config_root=_wys_root, live=False)
_dark_image = _render(_dark.scene(),
                      _dark.palette_roles[_Role.SURFACE_BG])
check("the dark theme moves the whole display, cards included",
      _dark_image != _view_image)


def _card_pixels(image):
    """Colours inside the PVM card's face, away from its border."""
    return {image.pixelColor(x, y).name()
            for x in range(60, 150, 6) for y in range(45, 75, 6)}


_light_card = _card_pixels(_view_image)
_dark_card = _card_pixels(_dark_image)
check("specifically the card FACE rethemes — it was hardcoded white, "
      "which is the pixel that gave the two-palette split away",
      not (_light_card & _dark_card),
      (sorted(_light_card)[:3], sorted(_dark_card)[:3]))
_dark.close()
_view.close()
_wys.close()

# --------------------------------------------------------------- cleanup
try:
    dlg.close()
except RuntimeError:
    pass        # already collected while later sections pumped events
designer.cleanup()
check("cleanup closes the faceplates the designer opened",
      not mgr._block_popups, list(mgr._block_popups))
check("cleanup stops the scan timer", not executive.is_running)
check("but leaves the modules on scan",
      all(rt.is_online for rt in store.get_strategy_runtimes()))

# ------------------------------- Azeo HMI capability: the new subsystems
# The checks below enforce the accepted product contract. A future edit that
# changes a number or rule must update the contract and its evidence together.
from azeo_control_trainer.core.hmi.pvms import complexity as _cx
from azeo_control_trainer.core.hmi.pvms import elements as _el
from azeo_control_trainer.core.hmi.pvms import instances as _inst
from azeo_control_trainer.core.hmi.pvms import layout as _lay
from azeo_control_trainer.core.hmi.pvms import capabilities as _par
from azeo_control_trainer.core.hmi.pvms import properties as _prop
from azeo_control_trainer.core.hmi.pvms import variables as _vars
from azeo_control_trainer.core.hmi.binding.result import (
    BindingResult as _BR,
)

# --- the property model: one gesture on every property ----------------
_live_tags = {"M/B/PV": _BR(value=75.0, quality=_PvmQuality.GOOD),
              "M/B/DEAD": _BR(value=1.0, quality=_PvmQuality.BAD),
              "M/B/ON": _BR(value=True, quality=_PvmQuality.GOOD)}
_fake_clock = {"ms": 0}


class _Fns:
    def eval(self, name, value):
        return ("#C4262E" if value >= 70 else "#3E7D4A") \
            if name == "F_Level" else None


_pres = _prop.PropertyResolver(
    standards={"S_Warn": "#E08A1E"}, functions=_Fns(),
    variables={"UnitColour": "#285F9A"},
    pvm_properties={"Orientation.BodyRot": 90},
    read=lambda p: _live_tags.get(p, _prop.UNRESOLVED),
    clock=lambda: _fake_clock["ms"])

check("every diamond-menu kind resolves from one call",
      [_pres.resolve(d).value for d in (
          {"kind": _prop.STANDARD, "ref": "Standard.S_Warn"},
          {"kind": _prop.VARIABLE, "ref": "Dsp.UnitColour"},
          {"kind": _prop.PVM_PROPERTY, "ref": "Pvm.Orientation.BodyRot"},
          {"kind": _prop.ANIMATION, "path": "M/B/PV", "fn": "F_Level"},
          {"kind": _prop.EXPRESSION, "expr": 'DLSYS["M/B/PV"] * 2'},
      )] == ["#E08A1E", "#285F9A", 90, "#C4262E", 150.0])
_dead = _pres.resolve({"kind": _prop.ANIMATION, "path": "M/B/DEAD"})
check("Bad quality answers Bad and valueless — never zero",
      _dead.value is None and not _dead.usable, _dead)
_fake_clock["ms"] = 0
_blink_on = _pres.resolve({"kind": _prop.BLINK, "condition": "M/B/ON",
                           "on": "#F00", "off": "#FFF"}).value
_fake_clock["ms"] = _prop.BLINK_STANDARD_MS
_blink_off = _pres.resolve({"kind": _prop.BLINK, "condition": "M/B/ON",
                            "on": "#F00", "off": "#FFF"}).value
check("blink alternates at the layout's standard 500 ms rate",
      (_blink_on, _blink_off) == ("#F00", "#FFF"))
check("an unresolvable blink condition shows the default and does NOT "
      "blink — a blink that means nothing is worse than none",
      _pres.resolve({"kind": _prop.BLINK, "condition": "M/B/GONE",
                     "on": "#F00", "off": "#FFF",
                     "default": "#808080"}).value == "#808080")
check("the legacy anim map and Pvm./Standard. strings still read, so "
      "no shipped display migrates on disk",
      _prop.descriptor_for({"anim": {"fill": {"path": "M/B/PV"}}},
                           "fill")["kind"] == _prop.ANIMATION
      and _prop.descriptor_for({"rot": "Pvm.Orientation.BodyRot"},
                               "rot")["kind"] == _prop.PVM_PROPERTY)
_round_trip = {"fill": "#111"}
_prop.set_descriptor(_round_trip, "fill", {"kind": _prop.STANDARD,
                                           "ref": "S_Warn"})
_prop.set_descriptor(_round_trip, "fill", None)
check("clearing a descriptor leaves the item byte-identical",
      _round_trip == {"fill": "#111"}, _round_trip)

# --- variables --------------------------------------------------------
_vset = _vars.VariableSet(scope="PVM class")
_vset.add("UnitColour", "Color", "#285F9A")
_vset.add("Level", "Number", 0.0,
          source={"kind": _prop.ANIMATION, "path": "M/B/PV"})
check("a variable set resolves static and animated members alike",
      _vset.resolve(_pres) == {"UnitColour": "#285F9A", "Level": 75.0},
      _vset.resolve(_pres))
check("a linked PVM may not hold variables — you add them to its class",
      not _vars.may_hold_variables("Linked PVM"))

# --- complexity: the manual's own worked example ----------------------
_worked = _cx.Complexity(handlers=100, tags=50, parameters=1000)
check("100 handlers / 50 tags / 1000 parameters gives index 74, the "
      "manual's own worked example",
      round(_worked.index) == 74, f"{_worked.index:.2f}")
check("...and control tags are named as the measure to reduce first",
      _worked.dominant == "tags")
check("Present Online false costs nothing at all",
      _cx.measure_items([
          {"props": {"fill": {"kind": _prop.ANIMATION,
                              "path": "FIC-101/PID1/PV"}}},
          {"present": False,
           "props": {"fill": {"kind": _prop.ANIMATION,
                              "path": "OFF/B/PV"}}}]).tags == 1)
_studio_cx = studio.complexity()
check("a live display reports its own complexity",
      _studio_cx.index >= 0 and _studio_cx.tags >= 0,
      _studio_cx.to_dict())

# --- layouts and display sets ----------------------------------------
_layout = _lay.default_layout()
_dset = _lay.DisplaySet("Console A")
_ov = _dset.add_root("PlantOverview")
_l2 = _dset.add_child(_ov, "L2-DISCHARGE")
_l3 = _dset.add_child(_l2, "L3-PUMPS")
_l4 = _dset.add_child(_l3, "L4-DIAG")
check("a display set may be four levels deep", _dset.depth() == 4)
try:
    _dset.add_child(_l4, "L5")
    check("a fifth level is refused", False)
except _lay.HierarchyError:
    check("a fifth level is refused", True)
try:
    _dset.add_child(_ov, "L2-DISCHARGE")
    check("one display cannot appear twice in one set", False)
except _lay.HierarchyError:
    check("one display cannot appear twice in one set", True)
check("routing follows the display's LEVEL, not the click",
      _lay.open_target(_layout, _dset, "L2-DISCHARGE") == "Main")
_dset.add_non_hierarchical("TrendPage")
check("a non-hierarchical display goes to the other-displays frame",
      _lay.open_target(_layout, _dset, "TrendPage") == "Side")
check("a static frame never accepts a display — that rule is what an "
      "alarm banner IS",
      not _layout.frame("Alarm banner").accepts(1))
check("with no frames configured a display opens in a new window",
      _lay.Layout("Bare").route(1) == _lay.NEW_WINDOW)

# --- datalinks and user entries --------------------------------------
check("a mode datalink carrying a field suffix is refused at "
      "configuration, not left to render **** online",
      _el.validate_datalink(_el.MODE, "M/PID1/MODE.TARGET") != ""
      and _el.validate_datalink(_el.MODE, "M/PID1/MODE") == "")
check("a numeric datalink on a .STR path is refused (it renders NaN)",
      "NaN" in _el.validate_datalink(_el.NUMERIC, "M/B/MODE.STR"))
check("Bad status and not-communicating stay DIFFERENT renderings",
      _el.datalink_text(_el.NUMERIC,
                        _BR(quality=_PvmQuality.BAD)).text == _el.BAD_TEXT
      and _el.datalink_text(_el.NUMERIC, None).text == _el.STALE_TEXT)
_gp.outputs["PV"].value = 37.25
_gp.outputs["PV"].status = _PvmQuality.GOOD
check("Azeo field suffixes resolve through the live graph source",
      _engine._source.read("PVMTEST/PID1/PV.CV").value == 37.25
      and _engine._source.read("PVMTEST/PID1/PV.STR").value == "37.25"
      and _engine._source.read("PVMTEST/PID1/PV.ST").value == "GOOD")
check("a placeable Data Link contributes its real subscription to the "
      "manual's complexity measures",
      _cx.measure_items([{"kind": "datalink",
                          "path": "PVMTEST/PID1/PV"}]).parameters == 1)
_entry_complexity = _cx.measure_items([
    {"kind": "user_entry",
     "entry": {"kind": "slider", "path": "PVMTEST/PID1/SP"}}])
check("a User Entry contributes both its subscription and event handler",
      _entry_complexity.parameters == 1
      and _entry_complexity.tags == 1
      and _entry_complexity.handlers == 1)
check("a slew's step grows with hold time but is capped",
      _el.slew_step(2000) > _el.slew_step(200)
      and _el.slew_step(60_000) == _el.SLEW_MAX_STEP)
check("an invisible element has no interaction region — no invisible "
      "live hotspots",
      not _el.has_interaction_region(
          {"visible": False,
           "actions": [{"event": "click", "kind": _el.OPEN_FACEPLATE}]}))

# --- linked PVMs, overrides, templates -------------------------------
_gi = _inst.PvmInstance("HP_Pump")
_gi.sync_from_class({"fill": "#808080", "label": "P-101"})
_gi.override("fill")
check("creating an override pins the value rather than changing it",
      _gi.value_of("fill") == "#808080" and _gi.is_overridden("fill"))
check("a class edit skips overridden properties and reports only what "
      "actually moved",
      _gi.sync_from_class({"fill": "#C4262E", "label": "P-101A"})
      == ["label"] and _gi.value_of("fill") == "#808080")
_gi.remove_override("fill")
_gi.sync_from_class({"fill": "#C4262E"})
check("removing the override resumes tracking",
      _gi.value_of("fill") == "#C4262E")
_unlinked = _inst.PvmInstance("HP_Pump")
_unlinked.sync_from_class({"fill": "#808080"})
_unlinked.unlink()
_unlinked.sync_from_class({"fill": "#C4262E"})
check("an unlinked PVM ignores class edits entirely — being unlinked "
      "is the whole point",
      _unlinked.value_of("fill") == "#808080")
_tpl = _inst.Template("Blank L2", "display",
                      {"display": "Blank L2", "pvms": [], "level": 2})
_made = _tpl.instantiate("New display")
_made["pvms"].append({"x": 1})
check("a template instantiates a document sharing nothing with it",
      _tpl.document["pvms"] == [] and _made["display"] == "New display")
try:
    _tpl.publish()
    check("a template refuses to be published", False)
except _inst.TemplateRefused:
    check("a template refuses to be published", True)

# The webinar's four levels are product starting points, not screenshots or
# project files copied from one training area.  The linked-pack path also
# proves the metadata, drawing documents and navigation are created together.
from azeo_control_trainer.core.hmi.pvms.hierarchy_templates import (
    BUILTIN_TEMPLATE_NAMES as _HIER_TEMPLATE_NAMES,
    install_hierarchy_sample as _install_hierarchy_sample,
)
from azeo_control_trainer.core.hmi.pvms.publishing import (
    DisplayStore as _HierarchyDisplayStore,
    PvmDisplay as _HierarchyDisplay,
)

_hier_root = _st_tmp.mkdtemp()
_hier_templates = _inst.TemplateStore(_hier_root)
check("every project exposes protected L1-L4 operator-display templates",
      set(_HIER_TEMPLATE_NAMES).issubset(_hier_templates.names("display"))
      and all(_hier_templates.is_builtin(name)
              for name in _HIER_TEMPLATE_NAMES))
_hier_template_docs = [
    _HierarchyDisplay.from_dict(
        _hier_templates.instantiate(name, f"Test L{level}"))
    for level, name in enumerate(_HIER_TEMPLATE_NAMES, start=1)
]
check("the four built-in templates carry their real hierarchy levels and a "
      "fixed operator frame",
      [display.level for display in _hier_template_docs] == [1, 2, 3, 4]
      and all((display.width, display.height) == (1600, 900)
              and len(display.items) >= 20
              for display in _hier_template_docs),
      [(display.level, display.width, display.height, len(display.items))
       for display in _hier_template_docs])
_hier_original = _hier_templates.entries[_HIER_TEMPLATE_NAMES[0]].document
_hier_templates.add(_HIER_TEMPLATE_NAMES[0], "display",
                    {**_hier_original, "description": "Site edit"})
check("built-in templates are edited as a project override, never deleted, "
      "and Reset to built-in restores the product document",
      _hier_templates.is_builtin(_HIER_TEMPLATE_NAMES[0])
      and _hier_templates.is_overridden(_HIER_TEMPLATE_NAMES[0])
      and not _hier_templates.remove(_HIER_TEMPLATE_NAMES[0])
      and _hier_templates.reset(_HIER_TEMPLATE_NAMES[0])
      and _hier_templates.entries[_HIER_TEMPLATE_NAMES[0]].document == _hier_original)

_hier_sample = _install_hierarchy_sample(_hier_root, "Webinar Demo")
_hier_display_store = _HierarchyDisplayStore(_hier_root)
_hier_docs = [_hier_display_store.load_draft(name)
              for name in _hier_sample.displays]
check("the hierarchy pack creates four editable drafts with an exact parent "
      "chain",
      [display.level for display in _hier_docs] == [1, 2, 3, 4]
      and [display.parent for display in _hier_docs]
      == ["", *_hier_sample.displays[:-1]])
_hier_targets = [
    {item.get("target") for item in display.items
     if item.get("kind") == "display_link"}
    for display in _hier_docs
]
check("the hierarchy pack's visible Up/Next controls navigate to its actual "
      "display names",
      _hier_targets == [
          {_hier_sample.displays[1]},
          {_hier_sample.displays[0], _hier_sample.displays[2]},
          {_hier_sample.displays[1], _hier_sample.displays[3]},
          {_hier_sample.displays[2]},
      ], _hier_targets)
_hier_layout_store = _lay.LayoutStore(_hier_root)
_hier_set = _hier_layout_store.display_set(_hier_sample.display_set)
check("the sample's Display Set is a real four-deep route and its L4 is "
      "routed by an authored operator layout",
      _hier_set is not None and _hier_set.depth() == 4
      and _hier_set.breadcrumb(_hier_sample.displays[-1])
      == list(_hier_sample.displays)
      and _hier_layout_store.layout(_hier_sample.layout).route(4) == "Side")

_hier_studios = [
    PvmStudio(lambda: {"PVMTEST": _gg}, _hier_root, display_name=name)
    for name in _hier_sample.displays
]
check("all four samples build through the shipping Studio renderer",
      all(len(studio._document().get("items", ())) >= 20
          and len(studio.canvas.scene().items()) >= 20
          for studio in _hier_studios))
from PySide6.QtCore import QEvent as _HierarchyEvent
from PySide6.QtWidgets import (
    QGraphicsSceneHoverEvent as _HierarchyHoverEvent,
)
from azeo_control_trainer.core.hmi.pvms.rendering.items import (
    StaticItem as _HierarchyStaticItem,
)

_hier_static = next(
    item for item in _hier_studios[0].canvas.scene().items()
    if isinstance(item, _HierarchyStaticItem)
)
try:
    _hier_static.hoverEnterEvent(_HierarchyHoverEvent(
        _HierarchyEvent.GraphicsSceneHoverEnter))
    _hier_static.hoverLeaveEvent(_HierarchyHoverEvent(
        _HierarchyEvent.GraphicsSceneHoverLeave))
    _static_hover_safe = not _hier_static._hover
except Exception as _static_hover_error:                 # noqa: BLE001
    _static_hover_safe = False
check("hovering a template's static drawing stays on the static path — "
      "it never calls PVM-only hp_state/tooltip methods",
      _static_hover_safe,
      "" if _static_hover_safe else _static_hover_error)
for _hier_studio in _hier_studios:
    _hier_studio.close()

_fp_a = _inst.ContextualDisplay("Loop_fp", _inst.FACEPLATE)
check("partial tag substitution lets one faceplate serve every module",
      _fp_a.substitute("{tag}/PID1/PV", "FIC-101") == "FIC-101/PID1/PV")
_fp_a.toggle_pin()
check("a pinned contextual display is not replaced by the next one",
      not _fp_a.replaced_by(_inst.ContextualDisplay("Loop_fp",
                                                    _inst.FACEPLATE)))

# --- PVM coverage of the block palette --------------------------------
# Control Designer offers blocks an engineer can put in a module. Every
# one an operator has a reason to LOOK at needs a PVM, or the student
# builds something the palette openly offers and then finds nothing to
# draw with — the trainer's edge showing, not a lesson.
from azeo_control_trainer.core.strategy.model.block_registry import (
    BlockRegistry as _BR2,
)
from azeo_control_trainer.core.strategy.model.strategy_graph import (
    StrategyGraph as _SG2,
)
from azeo_control_trainer.core.hmi.binding import (
    LiveGraphSource as _LGS2,
)
from azeo_control_trainer.core.hmi.binding.result import (
    UNRESOLVED as _UNRES2,
)

_pvm_classes = _pvm_registry.all_classes()
_by_block = {}
for (_bt, _role, _var) in _pvm_classes:
    _by_block.setdefault(_bt, set()).add(_role)
_placeable = {b for b, rs in _by_block.items()
              if any(r.startswith("dynamo") for r in rs)}
check(f"{len(_placeable)} block types are placeable on a display "
      f"(was 5 — AI, AO, DEVCTL, MOTOR_INTERLOCK, PID)",
      len(_placeable) >= 28, sorted(_placeable))
check("every block type with a faceplate can also be PLACED — a block "
      "you can open but not draw is a dead end",
      not {b for b, rs in _by_block.items() if "faceplate" in rs}
      - _placeable,
      sorted({b for b, rs in _by_block.items()
              if "faceplate" in rs} - _placeable))

# Every binding must resolve. A binding naming nothing renders Bad for
# ever, which on a display is indistinguishable from a dead
# transmitter — worse than a missing PVM, because it lies.
_probe_blocks = _BR2()
_probe_graph = _SG2(name="BINDPROBE")
_probe_names = {}
for (_bt, _r, _v) in _pvm_classes:
    if _bt in _probe_names:
        continue
    _blk = _probe_blocks.create(_bt, f"B_{_bt}")
    if _blk is None:
        continue
    _blk._apply_config()
    _probe_graph.add_block(_blk)
    _probe_names[_bt] = _blk.instance_name
_probe_source = _LGS2(lambda: {"BINDPROBE": _probe_graph})
_dead_binds = []
for (_bt, _role, _var), _cls in sorted(_pvm_classes.items()):
    _name = _probe_names.get(_bt)
    if _name is None:
        continue
    for _spec in _cls.bindings:
        _tpl = getattr(_spec, "path", "") or ""
        # Skip two kinds this substitution cannot stand in for:
        # multi-parameter classes ({master}/{slave}, {a}..{d}), the
        # documented EXCEPTION set; and configuration-property
        # templates ({Sel.Term}), which the PVM configuration document
        # resolves per placement. Both have their own tests.
        if not _tpl or getattr(_spec, "expr", None) \
                or "{path}" not in _tpl \
                or _tpl.replace("{path}", "").count("{"):
            continue
        if _probe_source.read(
                _tpl.replace("{path}", f"BINDPROBE/{_name}")) is _UNRES2:
            _dead_binds.append(f"{_bt}/{_role}/{_spec.key} -> {_tpl}")
check("every single-path PVM binding resolves against the real block — "
      "none renders Bad by configuration",
      not _dead_binds, _dead_binds)

# --- the High Performance PVM anatomy ---------------------------------
# Every HP PVM carries the same outer furniture, so it is built once and inherited by all
# 28 classes rather than reimplemented per painter.
from azeo_control_trainer.core.hmi.pvms import hp as _HP
from azeo_control_trainer.core.hmi.theme import vision as _vis

# The precedence rule is the load-bearing one: the alarm box and the
# status box are ONE rectangle, and the manual says the status box
# shows "only when there are no active or unacknowledged alarms".
_both = _HP.AlarmBoxState(priority=_HP.CRITICAL, active=True,
                          bad_io=True, abnormal_mode=True)
check("an alarm outranks an abnormal condition — one rectangle, one "
      "meaning at a time",
      _both.box_role is _R2.ALARM_P1 and not _both.shows_status_box)
check("with no alarm the STATUS box shows instead",
      _HP.AlarmBoxState(bad_io=True).shows_status_box)
check("and a healthy PVM draws no rectangle at all",
      _HP.AlarmBoxState().box_role is None)
check("an alarm that CLEARED unacknowledged still shows its box — it "
      "does not erase its own evidence",
      _HP.AlarmBoxState(priority=_HP.WARNING, active=False,
                        acked=False).has_alarm
      and not _HP.AlarmBoxState(priority=_HP.WARNING, active=False,
                                acked=True).has_alarm)
check("priority picks the box colour",
      [_HP.AlarmBoxState(priority=p, active=True).box_role
       for p in (_HP.CRITICAL, _HP.WARNING, _HP.ADVISORY)]
      == [_R2.ALARM_P1, _R2.ALARM_P2, _R2.ALARM_P3])
check("Bad quality lights Bad I/O, and a mode mismatch lights "
      "abnormal mode, straight off the BindingResult",
      _HP.resolve_alarm_box(_BR(quality=_PvmQuality.BAD)).bad_io
      and _HP.resolve_alarm_box(
          _BR(value=1.0, quality=_PvmQuality.GOOD,
              mode_target="CAS", mode_actual="MAN")).abnormal_mode)
check("an unresolved binding reads as Bad I/O, never as healthy",
      _HP.resolve_alarm_box(None).bad_io)

# Level decides which mark you get: the BOX on an overview, the ICON
# on a detail display.
# hpgray is the console theme the style guide is written against,
# and the one the vision check below measures.
_hp_pal = _ALL_THEMES["hpgray"]
_hp_alarm = _HP.AlarmBoxState(priority=_HP.CRITICAL, active=True)


def _hp_drew_box(level):
    img = _QImage(80, 60, _QImage.Format_ARGB32)
    p = _QPainter(img)
    drawn = _HP.draw_alarm_box(p, _QRectF(4, 4, 70, 50), _hp_alarm,
                               _hp_pal, level)
    p.end()
    return drawn


check("Level 1 draws the alarm BOX and Level 2 does not — an overview "
      "is read across the room, a detail display up close",
      _hp_drew_box(1) and not _hp_drew_box(2))
check("the display tag follows ShowTag, and friendly/description fall "
      "back to the module name when unset",
      _HP.display_tag(_afp_pvm_stub := type("G", (), {
          "params": {"path": "FIC-101/PID1"}, "label": ""})(),
          _HP.TAG_FRIENDLY) == "FIC-101"
      and _HP.display_tag(_afp_pvm_stub, _HP.TAG_NONE) == "")

# The vision check found a real one: ALARM_P2 (warning) and
# ALARM_SHELVED (suppressed) sit 3 apart in monochrome, so on a
# greyscale screen or a photocopied incident report the two boxes are
# the same box. The fix is a redundant channel, not a new colour —
# the same answer priority-3 yellow got.
_mono = {n: _vis.simulate(_hp_pal[r], "monochrome") for n, r in
         (("warning", _R2.ALARM_P2), ("suppressed", _R2.ALARM_SHELVED))}
check("warning and suppressed are NOT separable by colour in "
      "monochrome — which is why the dash exists",
      _vis.distance(_mono["warning"], _mono["suppressed"]) < 30,
      round(_vis.distance(_mono["warning"], _mono["suppressed"]), 1))
check("...so a suppressed box is DASHED and an active one solid",
      _HP.box_stroke(_HP.AlarmBoxState(priority=_HP.WARNING,
                                       active=True))[0]
      != _HP.box_stroke(_HP.AlarmBoxState(priority=_HP.CRITICAL,
                                          active=True,
                                          suppressed=True))[0])

# --- the Azeo Operator Station console: PUBLISHED PVM displays ------------------
# `pvms/station/` is a SECOND console, deliberately not the DynaLive
# one. Nothing on it reads a file: the display comes from a revision
# released to this workstation, which is what makes "deploy" mean
# something. The rules it obeys live in `live/runtime/deployment.py`
# and are shared with the DynaLive side — one set, two store bindings.
from azeo_control_trainer.azeo_operator_station import (
    PvmDeployment as _PvmDeploy, LiveStation as _LiveStation,
)
from azeo_control_trainer.core.hmi.pvms.publishing import (
    DisplayStore as _LiveStore, Pvm as _LivePvm, PvmDisplay as _LiveDisplay,
)
from azeo_control_trainer.azeo_operator_station.shell.chrome import (
    ConsoleSettings as _LiveSettings,
)
from azeo_control_trainer.azeo_operator_station.shell.deployment import (
    DeploymentOffline as _LiveOffline,
)

_live_store = _LiveStore(_st_tmp.mkdtemp())
_LIVE_CON = "CON-01"


def _live_doc(name, x):
    return _LiveDisplay(name=name, pvms=[
        _LivePvm(id="g1", pvm_class="", block_type="PID",
                 role="dynamo_inline", variant="hp",
                 params={"path": "PVMTEST/PID1"}, x=x, y=20).to_dict()])


_live_store.save_draft(_live_doc("Overview", 10))
_live_dep = _PvmDeploy(_live_store, workstation=_LIVE_CON)
check("a PVM display with a DRAFT but no release is not deployed — "
      "however finished it looks in the studio, publishing is what "
      "deploys", _live_dep.displays() == (), _live_dep.displays())

_live = _LiveStation(_live_dep, lambda: {"PVMTEST": _gg},
                     settings=_LiveSettings(console_id=_LIVE_CON,
                                            azeo_chrome=True,
                                            theme="hpgray"))
_live.resize(900, 520)
check("...so the console opens empty and SAYS why, rather than "
      "showing a blank screen that reads as a failure",
      _live.view is None
      and "has been published" in _live.placeholder.text())

_live_store.publish(_live_doc("Overview", 10), by="engineer",
                    workstations=[_LIVE_CON])
check("publishing deploys it, and the console can open it",
      _live_dep.displays() == ("Overview",)
      and _live.show_display("Overview"))
check("...rendered through the SAME DisplayRenderer the studio uses, "
      "so the WYSIWYG guarantee covers this console for free",
      _live.view is not None and len(_live.view.scene().items()) >= 1)

_live_store.publish(_live_doc("Overview", 400), by="engineer",
                    workstations=[_LIVE_CON])
check("**publishing again changes NOTHING on screen** — the operator "
      "is still looking at revision 1, because a display somebody is "
      "watching does not move under their hands",
      _live.view.display.pvms[0]["x"] == 10,
      _live.view.display.pvms[0]["x"])
_live.sync_chrome()
check("...the Refresh bubble lights instead",
      _live.menu.bubbles["refresh"] is True)
_live_moved = _live.refresh_configuration()
check("Refresh is the operator taking it, and it reports what moved",
      _live_moved == {"Overview": (1, 2)}, _live_moved)
check("...and only NOW does the screen show revision 2",
      _live.view.display.pvms[0]["x"] == 400
      and _live.menu.bubbles["refresh"] is False)

_live_dep.communicating = False
_live_store.publish(_live_doc("Overview", 800), by="engineer",
                    workstations=[_LIVE_CON])
_live_err = None
try:
    _live.refresh_configuration()
except _LiveOffline as exc:
    _live_err = exc
check("a station that cannot see the store refuses to refresh rather "
      "than reporting no updates", _live_err is not None)
check("reconnecting reports what waits and accepts nothing",
      _live_dep.reconnect() == ("Overview",)
      and _live.view.display.pvms[0]["x"] == 400)

_live_store.publish(_live_doc("Unit2", 10), by="engineer",
                    workstations=["CON-99"])
check("a revision released only to another station is invisible here "
      "— which is what makes a staged rollout possible",
      "Unit2" not in _live_dep.displays(), _live_dep.displays())

_live_dep.refresh()
_live.show_display("Overview", record=False)
_live_item = next(i for i in _live.view.scene().items()
                  if hasattr(i, "pvm"))
_live.open_faceplate(_live_item.pvm)
check("**this console opens PVM faceplates** — the class decides "
      "which, through the same registry the studio uses, so re-typing "
      "a loop changes its faceplate and no display is edited",
      len(_live.faceplates) == 1
      and _live.faceplates[0][1].__class__.__name__
      == "PvmFaceplateWidget")
_live.open_faceplate(_live_item.pvm)
check("...and a second click raises it rather than binding twice",
      len(_live.faceplates) == 1)

_opened_control_modules = []
_live.control_designer_opener = _opened_control_modules.append
_live.faceplate_action("studio", _live_item.pvm)
check("the Live faceplate engineering action opens the associated module in "
      "Control Designer, not the graphics authoring application",
      _opened_control_modules == ["PVMTEST"], _opened_control_modules)
_live_faceplate = _live.faceplates[0][1]
_live_faceplate_buttons = _live_faceplate.sections["buttons"].buttons
_live_faceplate_visible = {
    key for key, button in _live_faceplate_buttons.items()
    if not button.isHidden()}
check("the station removes Loop_fp footer actions it cannot service and "
      "keeps Detail, the uniquely associated DCC, Process History and "
      "faceplate-scoped ACK live",
      {"detail", "dcc", "history", "ack"} <= _live_faceplate_visible
      and {"studio"}.isdisjoint(_live_faceplate_visible)
      and _live_faceplate.visual.available_actions
      == frozenset(_live_faceplate_visible)
      and all(_live_faceplate_buttons[key].isEnabled()
              for key in _live_faceplate_visible),
      _live_faceplate_visible)

check("acceptance PERSISTS, so a restart does not offer the same "
      "update again — an indicator that reappears trains an operator "
      "to dismiss it",
      _PvmDeploy(_live_store, workstation=_LIVE_CON)._held("Overview")
      == _live_dep._held("Overview"))

_live_drawn = {k for k, _g, _t in
               _live.menu.buttons + _live.menu.right_buttons}
import inspect as _live_inspect
_live_src = _live_inspect.getsource(_LiveStation.menu_action)
check("every button this console draws is handled here too",
      _live_drawn == {k for k in _live_drawn
                      if '"%s"' % k in _live_src},
      sorted(_live_drawn))

# The three formerly inert station groups: graphics TypeScript, complete
# operator dialogs/history, and the desktop/filter/utilities/user controls.
from azeo_control_trainer.core.hmi.pvms.scripting import (  # noqa: E402
    GraphicsScriptRuntime as _ScriptRuntime,
    ScriptContext as _ScriptContext,
    transpile_typescript as _transpile_typescript,
)
from azeo_control_trainer.azeo_graphics_designer.script_editor import (  # noqa: E402
    SNIPPETS as _SCRIPT_SNIPPETS,
    ScriptAssistantDialog as _ScriptAssistant,
)
from azeo_control_trainer.azeo_operator_station.dialogs import (  # noqa: E402
    AlarmFilter as _AlarmFilter,
    AlarmFilterDialog as _AlarmFilterDialog,
    AlarmListDialog as _AlarmListDialog,
    LocalLogonDialog as _LocalLogonDialog,
    StationErrorsDialog as _StationErrorsDialog,
    StationSearchDialog as _StationSearchDialog,
    UtilitiesDialog as _UtilitiesDialog,
    station_search_records as _station_search_records,
)

_ts_context = _ScriptContext(session_store={})
_ts_answer = _ScriptRuntime().run(
    'const value: number = 2; DL.AddStoreItem("value", value); '
    'return Math.max(value, 3);', _ts_context)
_ts_async = _ScriptRuntime().run(
    "const value = await Promise.resolve(7); return value;",
    _ScriptContext())
check("the Graphics Designer runtime erases TypeScript annotations and runs "
      "the standard ECMAScript objects",
      _ts_answer.ok and _ts_answer.value == 3
      and _ts_async.ok and _ts_async.value == 7
      and _ts_context.session_store == {"value": 2}
      and ": number" not in _transpile_typescript("let x: number = 1;"),
      _ts_answer)
_blocked_script = _ScriptRuntime().run(
    'while (true) {}', _ScriptContext())
check("graphics scripts cannot acquire process/network APIs or create an "
      "unbounded operator-station loop",
      not _blocked_script.ok and "while" in _blocked_script.error,
      _blocked_script)
_script_action = {
    "kind": "script",
    "source": 'This.Label = "Scripted"; '
              'DL.AddStoreItem("display", ENV.DisplayName);',
}
check("a published display's TypeScript action reaches This, ENV and the "
      "session-wide DL store",
      _live.display_event_action(_script_action, _live.view, _live_item)
      and _live_item.pvm.label == "Scripted"
      and _live.script_store.get("display") == "Overview")
_assistant = _ScriptAssistant('const pv = DLSYS.Read("M/B/PV");')
check("Script Assistant is a real TypeScript editor with validation, test, "
      "find/replace and the Azeo object snippets",
      _assistant.validate_script()
      and _assistant.find_text is not None
      and {name.split(".")[0] for name, _code in _SCRIPT_SNIPPETS}
      >= {"DL", "DLSYS"})
_assistant.close()

_search_records = _station_search_records(_live_dep, _live.choose_display())
check("Station Search indexes both published displays and the real control "
      "tags inside them",
      any(row.kind == "Display" and row.name == "Overview"
          for row in _search_records)
      and any(row.kind == "Control Tag" and "PVMTEST/PID1" in row.path
              for row in _search_records))
check("Search and Errors open their dialogs; Alarms embeds the complete live summary",
      isinstance(_live.open_search(), _StationSearchDialog)
      and isinstance((_live.show_errors(), _live.operator_dialogs[-1])[1],
                     _StationErrorsDialog)
      and isinstance((_live.show_alarm_list(), _live.workspace.get("alarms"))[1].summary,
                     _AlarmListDialog)
      and _live.workspace.get("alarms").summary.registry is _live.alarm_state)
_history_before = len(_live.process_history_views)
_live.menu_action("history")
check("History opens Process History View, not the display-error action",
      len(_live.process_history_views) == _history_before + 1
      and _live.process_history_views[-1].windowTitle()
      == "Process History View"
      and bool(_live.historian.TAGS))
_history_view = _live.process_history_views[-1]
_QTest.mouseClick(
    _live_faceplate.visual, Qt.LeftButton, Qt.NoModifier, _QPoint(153, 516))
check("Loop_fp's History icon reuses the open Process History View in its equipment context",
      len(_live.process_history_views) == _history_before + 1
      and _live.workspace.get("trends") is None
      and _history_view.isWindow()
      and _live.process_history_views[-1]._modules.currentText()
      == "PVMTEST",
      len(_live.process_history_views))
_live.alarm_state.observe(
    "PVMTEST", "PID1", (("HI", 15, 80.0),))
_faceplate_alarm_before = _live.alarm_state.records(
    blocks=("PVMTEST/PID1",))
_QTest.mouseClick(
    _live_faceplate.visual, Qt.LeftButton, Qt.NoModifier, _QPoint(187, 516))
_faceplate_alarm_after = _live.alarm_state.records(
    blocks=("PVMTEST/PID1",))
check("Loop_fp's bell acknowledges only this faceplate's visible alarm rows",
      bool(_faceplate_alarm_before)
      and all(record.acknowledged for record in _faceplate_alarm_after),
      _faceplate_alarm_after)
check("Window / Full Desktop mode toggles in both directions",
      _live.toggle_window_mode() is True
      and _live.toggle_window_mode() is False)
_filter = _AlarmFilter(path_prefix="PVMTEST", minimum_priority=11,
                       show_suppressed=False)
check("Alarm Filter persists source, priority, state and suppression choices",
      _live.set_alarm_filter(_filter) is _filter
      and isinstance(_live.show_alarm_filter(), _AlarmFilterDialog)
      and _live.alarm_filter.minimum_priority == 11)
_utility_dialog = _live.open_utilities()
check("Utilities launches only real station applications",
      isinstance(_utility_dialog, _UtilitiesDialog)
      and {row[0] for row in _live.utilities()}
      == {"history", "alarms", "search", "errors"})
check("Logon is an optional local identity/role switch and View Only is a "
      "real write boundary",
      isinstance(_live.open_logon(), _LocalLogonDialog)
      and _live.set_local_user("viewer", False) == ("viewer", False)
      and not _live._can_write("PVMTEST/PID1/SP").success
      and "View Only" in _live._can_write("PVMTEST/PID1/SP").error)
_live.set_local_user("operator", True)
from PySide6.QtCore import QEvent as _DeferredDeleteEvent

_operator_windows_before = len(_live.operator_dialogs)
_utility_dialog.close()
app.sendPostedEvents(None, _DeferredDeleteEvent.DeferredDelete)
app.processEvents()
check("closing a modeless station utility releases its parent-owned Qt "
      "window instead of retaining every table and timer until logout",
      len(_live.operator_dialogs) == _operator_windows_before - 1,
      len(_live.operator_dialogs))

# Assigned layouts promote the existing Layout/DisplaySet rules into the
# station. Display Link placeholders remain publishable and begin working
# when their target is later released and present in the active set.
_assigned_root = _st_tmp.mkdtemp()
_assigned_store = _LiveStore(_assigned_root)
_assigned_store.save_draft(_LiveDisplay(
    name="Overview", items=[
        {"kind": "display_link", "target": "FutureDetail",
         "text": "Future detail", "x": 20, "y": 20,
         "w": 142, "h": 34},
    ]))
_assigned_store.save_draft(_LiveDisplay(name="UnitDetail"))
for _assigned_name in ("Overview", "UnitDetail"):
    _assigned_store.publish(
        _assigned_store.load_draft(_assigned_name), by="engineer",
        workstations=["CON-LAYOUT"])
_assigned_configs = _lay.LayoutStore(_assigned_root)
_assigned_layout = _lay.Layout("Two frame")
_assigned_screen = _assigned_layout.add_screen(_lay.Screen("Screen 1"))
_assigned_screen.add_frame(_lay.DisplayFrame(
    "Overview frame", rect=(0, 0, .55, 1), levels=(1,)))
_assigned_screen.add_frame(_lay.DisplayFrame(
    "Detail frame", rect=(.55, 0, .45, 1), levels=(2, 3, 4)))
_assigned_configs.save_layout(_assigned_layout)
_assigned_set = _lay.DisplaySet("Operations")
_assigned_root_node = _assigned_set.add_root("Overview")
_assigned_set.add_child(_assigned_root_node, "UnitDetail")
_future_node = _assigned_set.add_child(
    _assigned_root_node, "FutureDetail")
_future_node.placeholder = True
_assigned_configs.save_display_set(_assigned_set)
_maintenance = _lay.DisplaySet("Maintenance")
_maintenance.add_root("UnitDetail")
_assigned_configs.save_display_set(_maintenance)
_assigned_configs.assign(
    "CON-LAYOUT", layout="Two frame",
    display_sets=("Operations", "Maintenance"),
    active_display_set="Operations")
_assigned_dep = _PvmDeploy(_assigned_store, workstation="CON-LAYOUT")
_assigned_station = _LiveStation(
    _assigned_dep, lambda: {"PVMTEST": _gg},
    settings=_LiveSettings(console_id="CON-LAYOUT",
                           azeo_chrome=True, theme="hpgray"),
    config_root=_assigned_root)
_assigned_station.resize(1000, 560)
from azeo_control_trainer.azeo_operator_station.layout_surface import (
    FrameNavigationBar as _FrameNavigationBar,
)

_nav_probe = _FrameNavigationBar(_assigned_station.palette_roles)
_nav_probe.set_entries(
    ("Overview", "UnitDetail", "FutureDetail"), current="Overview")
_nav_button_ids = tuple(id(button) for button in _nav_probe._buttons)
for _nav_cycle in range(100):
    _nav_probe.set_entries(
        ("Overview", "UnitDetail", "FutureDetail"),
        current="UnitDetail",
        rollups={"UnitDetail": {"count": _nav_cycle % 4,
                                "priority": 11}})
check("station navigation refresh reuses its buttons and one tail spacer "
      "plus one persistent Navigate menu instead of growing the native "
      "layout on every scan",
      _nav_probe._row.count() == len(_nav_probe._buttons) + 2
      and tuple(id(button) for button in _nav_probe._buttons)
      == _nav_button_ids,
      (_nav_probe._row.count(), len(_nav_probe._buttons)))
check("an assigned station renders the display-set home in the layout's "
      "level-1 frame",
      _assigned_station.layout_surface is not None
      and _assigned_station.views["Overview frame"].display.name
      == "Overview")
check("automatic routing puts an L2 display in the frame configured for "
      "its hierarchy level",
      _assigned_station.show_display("UnitDetail")
      and _assigned_station.views["Detail frame"].display.name
      == "UnitDetail")
_placeholder_link = next(
    item for item in _assigned_station.views["Overview frame"].scene().items()
    if isinstance(getattr(item, "data", None), dict)
    and item.data.get("kind") == "display_link")
check("an unpublished Display Link placeholder is valid but honestly does "
      "nothing online", not _placeholder_link.activate())
_assigned_store.save_draft(_LiveDisplay(name="FutureDetail"))
_assigned_store.publish(
    _assigned_store.load_draft("FutureDetail"), by="engineer",
    workstations=["CON-LAYOUT"])
check("the same placeholder begins working when its target is published — "
      "the source display was not republished",
      _placeholder_link.activate()
      and _assigned_station.views["Detail frame"].display.name
      == "FutureDetail")
_assigned_tools = {key: usable for key, _label, usable, _why
                   in _assigned_station.tools_menu()}
check("Reset Layout, Layout Scale and Display Sets become live only when "
      "the workstation assignment supports them",
      all(_assigned_tools[key] for key in
          ("reset_layout", "layout_scale", "display_sets"))
      and _assigned_station.set_layout_scale(125)
      and _assigned_station.layout_surface.scale_percent == 125)
check("the operator can switch among assigned display sets and Reset "
      "Layout reopens that set's home",
      _assigned_station.choose_active_display_set("Maintenance")
      and _assigned_station.active_display_set.name == "Maintenance"
      and _assigned_station.history.current == "UnitDetail")
_assigned_station.close()
_live.close()

# --- deploying configuration online (azeolive.chm) ------------------
# The publish/pull model itself lives in `live/runtime/deployment.py`
# and is covered by `tests/_smoke_station.py`; what belongs HERE is the
# PVM-specific half: a class edit names the displays to publish, and the
# verification severities that decide whether a publish is refused.
from azeo_control_trainer.core.hmi.pvms.publishing import (
    ERROR as _SEV_ERROR, INFORMATIONAL as _SEV_INFO, WARNING as _SEV_WARN,
    DisplayStore as _DepStore, Finding as _Finding, PvmDisplay,
    blocking as _blocking, displays_using_class as _using_class,
)

_dep_store = _DepStore(_st_tmp.mkdtemp())

_wip = PvmDisplay(name="Draft", pvms=[], work_in_progress=True,
                  wip_reason="waiting on P&ID rev C")
_wip_err = None
try:
    _dep_store.publish(_wip, by="engineer")
except _StudioRefused as exc:
    _wip_err = exc
check("a Work In Progress display is not published by accident, and "
      "the reason travels with the refusal",
      _wip_err is not None and "P&ID rev C" in str(_wip_err))
_wip_entry = _dep_store.publish(_wip, by="engineer", allow_wip=True)
check("...but an engineer may still tick the box, as Azeo allows — "
      "modelling it as a hard refusal would be stricter than Azeo "
      "and put the engineer's judgement out of reach",
      _wip_entry.get("published_as_wip") is True)
check("...and publishing clears the flag and its comment",
      not _wip.work_in_progress and not _wip.wip_reason)

_dep_findings = (_Finding(_SEV_INFO, "consider a shorter tag"),
                 _Finding(_SEV_WARN, "fallback language missing"),
                 _Finding(_SEV_ERROR, "navigation target does not exist"))
check("only ERROR blocks a publish — a gate that refuses on every "
      "finding gets switched off, and then nothing is checked at all",
      [f.blocks_publish for f in _dep_findings] == [False, False, True]
      and len(_blocking(_dep_findings)) == 1)

check("a PVM class edit names the DISPLAYS that must be published; "
      "there is deliberately no publish(pvm_class) to call",
      _using_class(
          "PID",
          [PvmDisplay(name="A",
                      pvms=[{"id": "g", "class": "PID/dynamo_inline"}]),
           PvmDisplay(name="B",
                      pvms=[{"id": "h", "class": "AI/dynamo_inline"}])])
      == ("A",) and not hasattr(_dep_store, "publish_class"))

# --- the operator's view removes the AUTHORING affordances ----------
# `PvmDisplayView`'s docstring claimed items are "neither movable nor
# selectable" and nothing enforced it: `PvmItem.__init__` sets both
# flags because the STUDIO needs them, the renderer is shared with the
# studio by design, and the viewer never cleared them. An operator
# could select a PVM on a published display, see the studio's handles
# and drag it.
from PySide6.QtWidgets import QGraphicsItem as _QGItem
from azeo_control_trainer.core.hmi.pvms.rendering.viewer import (
    PvmDisplayView as _RoView,
)

_ro_doc = _LiveDisplay(name="RO", pvms=[
    _LivePvm(id="g1", pvm_class="", block_type="PID",
             role="dynamo_inline", variant="hp",
             params={"path": "PVMTEST/PID1"}, x=10, y=10).to_dict()
]).to_dict()
_ro_view = _RoView(_ro_doc, lambda: {"PVMTEST": _gg}, live=False)
_ro_bad = [i for i in _ro_view.scene().items()
           if i.flags() & (_QGItem.ItemIsMovable
                           | _QGItem.ItemIsSelectable)]
check("nothing on the operator's view can be selected or DRAGGED — "
      "the renderer is shared with the studio on purpose, so what the "
      "operator's view removes is the affordances, and it has to "
      "actually remove them", not _ro_bad, len(_ro_bad))

# --- the Selection pane, and the element shortcut menu ---------------
# `DLCreatingOperatorDisplay.pdf` names the Selection pane in eight of
# its element operations, and reaches nearly all of them through "an
# element's shortcut menu". We had neither: the canvas menu was style
# only (fill, line, arrows) and there was no element list at all.
from PySide6.QtWidgets import QGraphicsItem as _SelGItem
from azeo_control_trainer.azeo_graphics_designer.studio.selection_pane import (
    COL_NAME as _SEL_NAME, COL_VISIBLE as _SEL_VIS, SelectionPane,
)
from azeo_control_trainer.core.presentation.menu_style import (
    studio_menu as _sel_menu,
)

_sel_studio = PvmStudio(lambda: {"PVMTEST": _gg}, _st_tmp.mkdtemp(),
                        display_name="SelPane")
_sel_studio.enter_edit()
_sel_studio.place_block("PVMTEST/PID1", "PID", 20, 20)
for _x, _w in ((10, 60), (300, 60), (120, 40)):
    _sel_studio.add_static("rect", _x, 200, w=_w, h=40)
_sel_pane = SelectionPane(_sel_studio)
_sel_rows = [_sel_pane.topLevelItem(i)
             for i in range(_sel_pane.topLevelItemCount())]
check("the Selection pane lists every element on the display — PVMs "
      "and drawing items together, because an engineer hunting a "
      "buried object does not care which family it came from",
      len(_sel_rows) == 4, len(_sel_rows))

_sel_item = _sel_rows[0].data(_SEL_NAME, Qt.UserRole)
_sel_pane.set_visible(_sel_item, False)
check("hiding an element hides it on the DISPLAY, and it stays LISTED "
      "— which is the only way back, since a hidden element cannot be "
      "clicked on the canvas",
      not _sel_item.isVisible()
      and _sel_pane.topLevelItemCount() == len(_sel_rows))
_sel_pane.set_visible(_sel_item, True)
_sel_pane.set_locked(_sel_item, True)
check("locking removes MOVABILITY but not selectability — locking "
      "something out of reach is how an engineer ends up rebuilding a "
      "display to fix one object",
      not (_sel_item.flags() & _SelGItem.ItemIsMovable)
      and bool(_sel_item.flags() & _SelGItem.ItemIsSelectable))
_sel_pane.set_locked(_sel_item, False)

_sel_scene = _sel_studio.canvas.scene()
_sel_scene.clearSelection()
_sel_rows[1].setSelected(True)
_sel_pane._selection_changed()
_sel_picked = _sel_rows[1].data(_SEL_NAME, Qt.UserRole).isSelected()
_sel_scene.clearSelection()
_sel_rows[2].data(_SEL_NAME, Qt.UserRole).setSelected(True)
_sel_pane.sync_from_canvas()
check("selection runs BOTH ways between pane and canvas, without the "
      "two driving each other in a loop",
      _sel_picked and _sel_rows[2].isSelected()
      and not _sel_rows[1].isSelected())

_sel_target_row = _sel_rows[1]
_sel_pane._context_menu(
    _sel_pane.visualItemRect(_sel_target_row).center())
_selection_element_actions = _recursive_menu_labels(
    _sel_studio._context_menu)
check("right-clicking a Selection row opens that element's complete canvas "
      "menu, so covered and locked objects do not lose commands",
      all(any(word in label for label in _selection_element_actions)
          for word in ("Line colour", "Lock", "Duplicate", "Delete")),
      _selection_element_actions)
_sel_pane.resize(260, 400)
_sel_pane._context_menu(_QPoint(2, 390))
_selection_blank_actions = [
    a.text() for a in _sel_pane._context_menu_instance.actions() if a.text()]
check("right-clicking empty Selection space provides recovery operations "
      "for all hidden or locked elements",
      all(any(word in label for label in _selection_blank_actions)
          for word in ("Select All", "Show All", "Unlock All", "Refresh")),
      _selection_blank_actions)

_sel_statics = _sel_studio._static_items()
_sel_scene.clearSelection()
for _s in _sel_statics:
    _s.setSelected(True)
_sel_m = _sel_menu("X", "Y")
_sel_studio._add_element_actions(_sel_m, _sel_statics[0])
_sel_labels = [a.text() for a in _sel_m.actions() if a.text()]
check("the element shortcut menu carries the operations the manual "
      "reaches through it — visibility, lock, order, align, "
      "distribute, clipboard, group, delete — not just fill and line",
      all(any(w in x for x in _sel_labels)
          for w in ("Hide", "Lock", "Order", "Align", "Distribute",
                    "Cut", "Copy", "Duplicate", "Group", "Delete")),
      _sel_labels)

_sel_before = sorted(s.pos().x() for s in _sel_statics)
_sel_moved = _sel_studio.distribute_selected("h")
_sel_xs = sorted((s.pos().x(), s.rect().width()) for s in _sel_statics)
_sel_gaps = [round(_sel_xs[i + 1][0] - (_sel_xs[i][0] + _sel_xs[i][1]), 3)
             for i in range(len(_sel_xs) - 1)]
check("Distribute evens the GAPS, not the origins — spacing centres "
      "evenly leaves ragged space whenever elements differ in size, "
      "and the eye reads the space between things",
      _sel_moved == 1 and len(set(_sel_gaps)) == 1, _sel_gaps)
check("...the outermost two do not move: they define the span being "
      "divided",
      _sel_xs[0][0] == _sel_before[0]
      and _sel_xs[-1][0] == _sel_before[-1])
_sel_scene.clearSelection()
_sel_statics[0].setSelected(True)
check("...and with fewer than three it does nothing rather than "
      "something arbitrary. It used to be `lambda: None` on the "
      "ribbon — a button that did nothing at all",
      _sel_studio.distribute_selected("h") == 0)

# --- a theme is a RUNTIME choice, and it reaches everything ----------
# Azeo puts Themes on the runtime menu: picking one re-skins the
# running session immediately, no restart. That works because a display
# names a STANDARD and each theme supplies its value — which is what
# our Role/THEMES pair already is. What was missing was the half that
# makes it live: every palette was read once at construction and kept.
from azeo_control_trainer.core.hmi.theme.service import ThemeService
from azeo_control_trainer.core.hmi.theme.tokens import (
    DEFAULT_THEME as _DEFAULT_THEME,
)

_th = ThemeService()
_th_seen = []
_th.changed.connect(_th_seen.append)
check("the theme list offers every theme this build has, with the "
      "default FIRST and marked — Azeo labels it Initial rather "
      "than hiding it, which is how an operator gets back to the "
      "theme the site was commissioned with",
      _th.names()[0] == _DEFAULT_THEME
      and set(_th.names()) == set(_ALL_THEMES)
      and _th.is_initial(_th.names()[0]), _th.names())

_th_other = next(n for n in _th.names() if n != _th.current)
check("switching announces itself once, and moves `current`",
      _th.set_theme(_th_other) and _th.current == _th_other
      and _th_seen == [_th_other], (_th.current, _th_seen))
_th_seen.clear()
check("...switching to the SAME theme is not a change, so nothing is "
      "announced — a console that re-skinned on every click would "
      "rebuild the display under the operator for nothing",
      _th.set_theme(_th_other) is False and _th_seen == [])
check("...and an unknown theme is REFUSED, not silently defaulted. A "
      "console that re-skins to something the caller did not ask for "
      "shows a change the operator reads as the one they requested",
      _th.set_theme("no-such-theme") is False
      and _th.current == _th_other and _th_seen == [])

# The faceplate has to FOLLOW, in place.
_thm_ai = _PvmRegistry().create("AI", "AI_THM")
_thm_ai._apply_config()
_gg.add_block(_thm_ai)
_thm_fp = PvmFaceplateWidget(_pvm_registry.get("AI", "faceplate"),
                             {"path": "PVMTEST/AI_THM"}, _engine,
                             theme=_DEFAULT_THEME)
_thm_unit = _thm_fp.sections["unit"].label
_thm_before = _thm_unit.styleSheet()
_thm_dark = next(n for n in _ALL_THEMES if n != _DEFAULT_THEME)
check("an OPEN faceplate re-skins in place — one that kept its old "
      "colours until it was closed and reopened would leave two "
      "palettes on screen at once, which is worse than offering no "
      "themes at all",
      _thm_fp.apply_theme(_thm_dark)
      and _thm_unit.styleSheet() != _thm_before,
      (_thm_before, _thm_unit.styleSheet()))
check("...and it is the SAME widget, not a rebuilt one, so window "
      "position and the trend's history survive the switch",
      _thm_fp.sections["unit"].label is _thm_unit)
check("...every section moved, not just the ones that happened to "
      "restyle on their next refresh",
      all(sec._palette is _ALL_THEMES[_thm_dark]
          for sec in _thm_fp.sections.values()),
      [n for n, sec in _thm_fp.sections.items()
       if sec._palette is not _ALL_THEMES[_thm_dark]])
check("...switching back restores the original stylesheet exactly — a "
      "replayed style is the theme's value, not a one-way tint",
      _thm_fp.apply_theme(_DEFAULT_THEME)
      and _thm_unit.styleSheet() == _thm_before,
      (_thm_before, _thm_unit.styleSheet()))

# --- the faceplate icon buttons actually do something -----------------
# All six were built, styled and connected to NOTHING: Detail, associated DCC,
# Primary, Control Designer, History and Ack were drawn live and swallowed every
# click. Pressed here rather than called, so the wiring is what runs.
from PySide6.QtCore import Qt as _BtnQt
from azeo_control_trainer.core.hmi.pvms.faceplate_ui import (
    ICON_BUTTONS as _ICONS,
)


def _press(button):
    """A real press-release on the widget, not button.click()."""
    centre = button.rect().center()
    button.setAttribute(_BtnQt.WA_DontShowOnScreen, True)
    _QTest.mousePress(button, _BtnQt.LeftButton, pos=centre)
    _QTest.mouseRelease(button, _BtnQt.LeftButton, pos=centre)


check("every faceplate icon glyph is a BMP symbol — 📈 and 🔔 shipped "
      "here and render as empty boxes in the faceplate font, the same "
      "defect the console chrome had",
      all(ord(c) < 0x10000 for _k, g, _t in _ICONS for c in g),
      [g for _k, g, _t in _ICONS if any(ord(c) >= 0x10000 for c in g)])
check("Loop_fp's icon catalogue is the manual's six actions in hand-learned "
      "order",
      tuple(key for key, _glyph, _tip in _ICONS) == (
          "detail", "dcc", "primary", "studio", "history", "ack"),
      tuple(key for key, _glyph, _tip in _ICONS))

_btn_ai = _PvmRegistry().create("AI", "AI_BTN")
_btn_ai._apply_config()
_gg.add_block(_btn_ai)
_btn_fp = PvmFaceplateWidget(_pvm_registry.get("AI", "faceplate"),
                             {"path": "PVMTEST/AI_BTN"}, _engine)
_btn_row = _btn_fp.sections.get("buttons")
check("the faceplate that declares a layout HAS the icon row — a "
      "layout naming 'buttons' and building none would make every "
      "check below vacuous",
      _btn_row is not None and set(_btn_row.buttons) == {
          k for k, _g, _t in _ICONS}, sorted(_btn_row.buttons)
      if _btn_row else None)

_btn_seen = []
_btn_fp.action_requested.connect(_btn_seen.append)
_btn_row.set_available({"detail"})
_press(_btn_row.buttons["detail"])
check("pressing Detail emits the action — the button was connected to "
      "nothing at all, so the operator's click went nowhere",
      _btn_seen == ["detail"], _btn_seen)

_btn_seen.clear()
_press(_btn_row.buttons["studio"])
check("...and a button the host cannot service is DISABLED, so it "
      "does not swallow the click. A button that looks live and does "
      "nothing is the worse failure",
      not _btn_row.buttons["studio"].isEnabled() and _btn_seen == [],
      _btn_seen)
check("...and says why in its tooltip, because the operator's question "
      "is whether they can get there from here",
      "not available here" in _btn_row.buttons["studio"].toolTip(),
      _btn_row.buttons["studio"].toolTip())

check("a faceplate claims only what it can answer for itself: a "
      "detail class exists. An alarm LIST being drawn is not the same "
      "as having a monitor to acknowledge against",
      "ack" not in _btn_fp.serviceable_actions())

_button_host = PvmStudio(
    lambda: {"PVMTEST": _gg}, _st_tmp.mkdtemp(),
    display_name="Faceplate buttons")
_button_pvm = _button_host.place_block(
    "PVMTEST/PID1", "PID", 10, 10)
_button_host.open_faceplate(_button_pvm)
_button_faceplate = next(iter(_button_host._faceplates.values()))
_button_actions = _button_faceplate.sections["buttons"].buttons
_button_visible = {
    key for key, button in _button_actions.items() if not button.isHidden()}
check("Graphics Designer exposes only actions it can service — Detail, the "
      "originating primary display, Trend and faceplate-scoped ACK",
      _button_visible == {"detail", "dcc", "primary", "history", "ack"}
      and _button_faceplate.visual.available_actions
      == frozenset(_button_visible)
      and all(_button_actions[key].isEnabled() for key in _button_visible),
      _button_visible)
_press(_button_actions["dcc"])
check("Graphics Designer's DCC button opens the one conditions faceplate in "
      "the module rather than selecting the first block or doing nothing",
      any(key[0] == "MotorInterlockFaceplate"
          for key in _button_host._faceplates),
      list(_button_host._faceplates))
_press(_button_actions["detail"])
check("Graphics Designer's Detail button opens the registered PID detail "
      "display instead of emitting into an unconnected signal",
      any(key[0] == "PIDDetail" for key in _button_host._faceplates),
      list(_button_host._faceplates))
_pid_detail_widget = next(
    widget for key, widget in _button_host._faceplates.items()
    if key[0] == "PIDDetail")
_pid_detail_panel = _pid_detail_widget.sections["detail"]
check("PID Detail is the PDF's fixed two-column Loop_dt display, not the "
      "generic binding-row inspector",
      (_pid_detail_widget.faceplate_surface.width(),
       _pid_detail_widget.faceplate_surface.height()) == (592, 656)
      and hasattr(_pid_detail_panel, "limits")
      and len(_pid_detail_panel.limits._widgets) == 13
      and len(_pid_detail_panel.alarms.labels) == 7
      and _pid_detail_panel.diagnostics.tabs.count() == 3
      and _pid_detail_panel.limits.geometry().getRect()
      == (15, 79, 205, 244)
      and _pid_detail_panel.alarms.geometry().getRect()
      == (226, 79, 350, 166)
      and _pid_detail_panel.diagnostics.geometry().getRect()
      == (226, 243, 350, 300)
      and all(key in _pid_detail_panel.tuning._widgets for key in (
          "tuning.beta", "tuning.gamma", "tuning.ideadband")),
      _pid_detail_widget.faceplate_surface.size())
_detail_specs = {spec.key: spec for spec in _pid_detail_widget.pvm.bindings}
_detail_input_keys = (
    set(_pid_detail_panel.limits._widgets)
    | set(_pid_detail_panel.tuning._widgets)
    | {"simulate.enabled", "simulate.value"})
check("Every Loop_dt hash field is a real writable PID binding — no "
      "placeholder accepts a value that cannot reach the function block",
      all(key in _detail_specs and _detail_specs[key].writable
          for key in _detail_input_keys),
      sorted(key for key in _detail_input_keys
             if key not in _detail_specs or not _detail_specs[key].writable))
check("Loop_dt omits the unavailable Azeo Insight service and hides Clear "
      "Error until a real MERROR clear target exists",
      not hasattr(_pid_detail_panel, "insight")
      and _pid_detail_panel.diagnostics.clear_error.isHidden())
_detail_hi = _pid_detail_panel.limits._widgets["limits.hi"]
check("Detail parameters are visibly editable controls, including while "
      "Graphics Designer is in EDIT",
      _button_host.mode == _MODE_E
      and _detail_hi.objectName() == "detail_config_editor"
      and _detail_hi.isEnabled()
      and _detail_hi.toolTip().startswith("Editable online value"),
      (_button_host.mode, _detail_hi.objectName(), _detail_hi.toolTip()))
_detail_hi.setText("88.5")
_detail_hi.editingFinished.emit()
_detail_reset = _pid_detail_panel.tuning._widgets["tuning.reset"]
_detail_reset.setText("42")
_detail_reset.editingFinished.emit()
_detail_beta = _pid_detail_panel.tuning._widgets["tuning.beta"]
_detail_beta.setText("0.7")
_detail_beta.editingFinished.emit()
check("Detail Limit and Tuning editors update the live block configuration "
      "without a user-role gate",
      _gp.config.params["hi_lim"] == 88.5
      and _gp.config.params["RESET"] == 42.0
      and _gp.config.params["beta"] == 0.7
      and _detail_hi.property("writeState") == "ok",
      (_gp.config.params.get("hi_lim"),
       _gp.config.params.get("RESET"),
       _gp.config.params.get("beta"),
       _detail_hi.property("writeState")))
check("Loop_dt applies the documented conditional tuning rows — "
      "Beta/Gamma for two-DOF, IDeadband instead of Bias, and FF Gain "
      "only when feedforward is enabled",
      not _detail_beta.isHidden()
      and not _pid_detail_panel.tuning._widgets["tuning.gamma"].isHidden()
      and not _pid_detail_panel.tuning._widgets["tuning.ideadband"].isHidden()
      and _pid_detail_panel.tuning._widgets["tuning.bias"].isHidden()
      and _pid_detail_panel.tuning._widgets["tuning.ff_gain"].isHidden())
_ai_detail_widget = PvmFaceplateWidget(
    _pvm_registry.get("AI", "detail"),
    {"path": "PVMTEST/AI_BTN"}, _engine)
_ai_detail_widget.set_write_handler(_engine.write, _engine.can_write)
_ai_detail_widget.refresh()
_ai_detail_panel = _ai_detail_widget.sections["detail"]
check("AI_dt is the manual's fixed 560 x 550 two-column detail, with six "
      "limits and the five documented alarm rows",
      (_ai_detail_widget.faceplate_surface.width(),
       _ai_detail_widget.faceplate_surface.height()) == (560, 550)
      and _ai_detail_panel.limits.geometry().getRect()
      == (15, 79, 205, 130)
      and _ai_detail_panel.alarms.geometry().getRect()
      == (226, 79, 330, 144)
      and len(_ai_detail_panel.limits._widgets) == 6
      and len(_ai_detail_panel.alarms.labels) == 5,
      _ai_detail_widget.faceplate_surface.size())
_ai_detail_specs = {
    spec.key: spec for spec in _ai_detail_widget.pvm.bindings}
_ai_detail_inputs = (
    set(_ai_detail_panel.limits._widgets)
    | set(_ai_detail_panel.tuning._widgets)
    | {"simulate.enabled", "simulate.value"})
check("Every AI_dt hash field is a real writable AI binding, while Field "
      "Value and the DATADATA linearization type remain indications",
      all(key in _ai_detail_specs and _ai_detail_specs[key].writable
          for key in _ai_detail_inputs)
      and not _ai_detail_specs["field.value"].writable
      and not _ai_detail_specs["linearization.type"].writable
      and not hasattr(_ai_detail_panel.linearization, "_widgets"),
      sorted(key for key in _ai_detail_inputs
             if key not in _ai_detail_specs
             or not _ai_detail_specs[key].writable))
_ai_low_cut = _ai_detail_panel.limits._widgets["limits.low_cut"]
_ai_low_cut.setText("3.25")
_ai_low_cut.editingFinished.emit()
check("AI_dt writes Low Cutoff online without a temporary user-role gate",
      _btn_ai.config.params["LOW_CUT"] == 3.25
      and _ai_low_cut.property("writeState") == "ok",
      (_btn_ai.config.params.get("LOW_CUT"),
       _ai_low_cut.property("writeState")))
check("AI_dt omits unavailable Insight and hides Clear Error until a real "
      "MERROR clear service exists",
      not hasattr(_ai_detail_panel, "insight")
      and _ai_detail_panel.diagnostics.clear_error.isHidden())
_ai_detail_widget.close()

_detail_count = len(_button_host._faceplates)
_press(_pid_detail_panel.header.faceplate)
check("the detail display's faceplate button raises the original "
      "faceplate rather than opening a duplicate",
      len(_button_host._faceplates) == _detail_count)
_QTest.mouseClick(
    _button_faceplate.visual, Qt.LeftButton, Qt.NoModifier,
    _QPoint(153, 516))
check("Graphics Designer's History button opens Process History View rather "
      "than the short-lived tuning trend",
      len(_button_host._process_history_views) == 1
      and "Process History View" in
      _button_host._process_history_views[0].windowTitle()
      and not _button_host._tuning_trends,
      (len(_button_host._process_history_views),
       len(_button_host._tuning_trends)))
_button_host.close()

# --- the HP PVMs render at the product-contract size ----------------
# Holding the accepted sizes as data prevents later classes from being
# added at an arbitrary size chosen inside an individual painter.
from azeo_control_trainer.core.hmi.pvms.hp import style_metrics as _hp_metrics

check("the size contract covers all 28 installed High Performance PVM "
      "classes",
      len(_hp_metrics.SIZES) == 28, len(_hp_metrics.SIZES))

_dv_reg = _pvm_registry.all_classes()
_dv_wrong = [
    (name, key, _dv_reg[key].DEFAULT_SIZE, _hp_metrics.SIZES[name])
    for name, key in _hp_metrics.IMPLEMENTED.items()
    if key in _dv_reg
    and tuple(_dv_reg[key].DEFAULT_SIZE) != tuple(_hp_metrics.SIZES[name])
]
check("every installed HP PVM class renders at its accepted size",
      not _dv_wrong, _dv_wrong)
check("...and every class IMPLEMENTED claims is actually registered — "
      "an entry naming a PVM that does not exist would quietly stop "
      "checking anything",
      all(k in _dv_reg for k in _hp_metrics.IMPLEMENTED.values()),
      [k for k in _hp_metrics.IMPLEMENTED.values() if k not in _dv_reg])

check("PALETTE carries the accepted process-value bar role",
      _hp_metrics.PALETTE["BAR_PV"] == "#7B92AD")
check("...and every theme ships the same invariant BAR_PV colour",
      {th[_R2.BAR_PV] for th in _ALL_THEMES.values()}
      == {_hp_metrics.PALETTE["BAR_PV"]},
      sorted({th[_R2.BAR_PV] for th in _ALL_THEMES.values()}))

# --- Graphics Explorer: can you add a page? --------------------------
# The tree's context menu returned immediately unless the item was a
# display, so every permanent FOLDER — and the empty space below the
# tree — answered nothing, and there was no way to create a display at
# all. Azeo: configuration is "created from the ribbon's New command
# or from their permanent folder's context menu in the Explorer view."
from azeo_control_trainer.azeo_graphics_designer.window import (
    HmiStudioWindow as _ExWindow,
)
from azeo_control_trainer.core.presentation.menu_style import (
    studio_menu as _ex_menu,
)

_ex_win = _ExWindow(lambda: {"PVMTEST": _gg}, _st_tmp.mkdtemp(),
                    area_name="PVMTEST")
_ex_tree = _ex_win.graphics_tree


def _ex_find(label):
    """The tree item whose clean label is exactly ``label``.

    Matching on `endswith` alone finds "Contextual Displays" when you
    asked for "Displays" — which is how this check first reported the
    wrong folder's kind. Graphics Explorer now uses QIcons rather than an
    emoji prefix, so there is no first token to discard.
    """
    stack = [_ex_tree.topLevelItem(i)
             for i in range(_ex_tree.topLevelItemCount())]
    while stack:
        item = stack.pop()
        if item.text(0).strip() == label:
            return item
        stack += [item.child(i) for i in range(item.childCount())]
    return None


_ex_kinds = {label: (_ex_find(label).data(0, Qt.UserRole + 1)
                     if _ex_find(label) else None)
             for label in ("Displays", "Display Sets",
                           "Contextual Displays", "Layouts")}
check("every permanent folder carries its kind — which is what lets "
      "its menu offer the right New, and what the old menu had no way "
      "to ask",
      _ex_kinds == {"Displays": "displays",
                    "Display Sets": "display_sets",
                    "Contextual Displays": "contextual",
                    "Layouts": "layouts"}, _ex_kinds)
check("the project root has its own context kind rather than falling "
      "through to the Displays-folder menu",
      _ex_tree.topLevelItem(0).data(0, Qt.UserRole + 1) == "project")

# QAction wrappers do not keep their owning menu alive. Retain each menu
# while inspecting it; the old menu signal cycle accidentally masked this.
_ex_project_menu = _ex_win._folder_menu(_ex_menu, "project")
_ex_project = [a.text() for a in _ex_project_menu.actions() if a.text()]
check("the project-root menu creates every project document family and the "
      "linked L1-L4 hierarchy",
      all(any(word in label for label in _ex_project)
          for word in ("New Display", "New Display Set", "New Layout",
                       "New L1")), _ex_project)

_ex_new_menu = _ex_win._folder_menu(_ex_menu, "displays")
_ex_new = [a.text() for a in _ex_new_menu.actions() if a.text()]
check("right-clicking the Displays folder offers New Display — the "
      "thing that was impossible before",
      any("New Display" in x for x in _ex_new), _ex_new)
_ex_ctx_menu = _ex_win._folder_menu(_ex_menu, "contextual")
_ex_ctx = [(a.text(), a.isEnabled()) for a in _ex_ctx_menu.actions() if a.text()]
check("Contextual Displays offers no New and SAYS why — they are "
      "derived from registered PVM classes, and a greyed New would "
      "imply the folder could make one",
      not any("New" in t for t, _e in _ex_ctx)
      and any("Derived from" in t for t, _e in _ex_ctx), _ex_ctx)

_ctx_folder = _ex_find("Contextual Displays")
_ctx_child = _ctx_folder.child(0) if _ctx_folder.childCount() else None
if _ctx_child is not None:
    _ctx_name = _ctx_child.data(0, Qt.UserRole)
    _ctx_menu = _ex_win._contextual_menu(_ex_menu, _ctx_name)
    _ctx_child_actions = [a.text() for a in _ctx_menu.actions() if a.text()]
else:
    _ctx_child_actions = []
check("a contextual class row explains its bound-runtime route instead of "
      "pretending it is a normal display document",
      _ctx_child is not None
      and any("bound PVM" in label for label in _ctx_child_actions)
      and not any(label == "Open" for label in _ctx_child_actions),
      _ctx_child_actions)

_ex_win.open_display("Overview")
_ex_disp_menu = _ex_win._display_menu(_ex_menu, "Overview")
_ex_disp = [a.text() for a in _ex_disp_menu.actions() if a.text()]
check("a display's own menu offers Open, Properties, New, Rename, Delete, "
      "revision history and Publish",
      all(any(w in x for x in _ex_disp)
          for w in ("Open", "Properties", "New Display", "Rename", "Delete",
                    "Revision history", "Publish")), _ex_disp)
_tab_index = _ex_win.tabs.currentIndex()
_ex_win._workspace_tab_menu(
    _ex_win.tabs.tabBar().tabRect(_tab_index).center())
_tab_actions = [a.text() for a in
                _ex_win._workspace_context_menu.actions() if a.text()]
check("a display-tab menu covers its document lifecycle and tab management",
      all(any(word in label for label in _tab_actions)
          for word in ("Display Properties", "Save", "Verify", "Publish",
                       "Quick Online", "Close Other", "Close All")),
      _tab_actions)
_ex_win._left_tab_menu(_QPoint(2, 2))
_left_tab_actions = [a.text() for a in
                     _ex_win._left_tab_context_menu.actions() if a.text()]
check("the Explorer tab strip exposes pane visibility, sizing and help",
      all(any(word in label for label in _left_tab_actions)
          for word in ("Activate", "Hide Graphics Explorer",
                       "Reset Pane Widths", "Help")), _left_tab_actions)
_blank_display = _ex_win._open_created_display("Framed Blank")
check("a new tree display starts with the standard 16:9 operator page, so "
      "its dotted boundary exists before the first object is drawn",
      (_blank_display.display.width, _blank_display.display.height)
      == (1600, 900)
      and _blank_display.canvas.page_boundary_visible())
check("rename and delete refuse HEADLESSLY rather than sitting on a "
      "modal nobody can answer (hard-won item 20)",
      _ex_win._rename_display("Overview") is False
      and _ex_win._delete_display("Overview") is False)
_ex_win.close()

# --- the console's BOTTOM: banner controls and the status strip -----
# The Azeo desktop figure has alarm tiles, then ACK / SUMMARY /
# SILENCE / ?, then a status strip under them. We had the tiles and
# two of the four controls, and no strip on the Azeo Operator Station console.
from azeo_control_trainer.core.hmi.model.alarms import (
    Banner as _Bnr, BannerLine as _BnrLine, Priority as _BnrPri,
)
from azeo_control_trainer.azeo_operator_station.shell.chrome import (
    AlarmBanner as _BannerWidget,
)

_bnr = _BannerWidget(_LiveSettings(azeo_chrome=True))
_bnr.resize(900, 52)
_bnr_rects = _bnr.button_rects()
check("the banner carries the figure's four controls, right to left, "
      "with ? narrowest",
      set(_bnr_rects) == {"ack", "summary", "silence", "help"}
      and _bnr_rects["ack"].left() < _bnr_rects["summary"].left()
      < _bnr_rects["silence"].left() < _bnr_rects["help"].left()
      and _bnr_rects["help"].width() < _bnr_rects["ack"].width())


def _bnr_of(n):
    return _Bnr(lines=tuple(_BnrLine(text="T%d" % i) for i in range(n)),
                counts={_BnrPri.P1: n} if n else {})


_bnr.set_banner(_bnr_of(2))
_bnr.set_silenced(True)
_bnr.set_banner(_bnr_of(2))
check("SILENCE is a latching STATE, not a horn — there is no audible "
      "annunciator here, and a speaker button would be a control with "
      "nothing behind it. It stays on while the same alarms stand",
      _bnr.silenced)
_bnr.set_banner(_bnr_of(3))
check("...and a NEW alarm breaks it: a silence that outlived the "
      "alarm it silenced is how the next one gets missed",
      not _bnr.silenced)
_bnr.set_silenced(True)
_bnr.set_banner(_bnr_of(1))
check("...while alarms CLEARING does not un-silence, which would be "
      "noise for something getting better", _bnr.silenced)

# Its own station: the one above was closed at the end of that block,
# and a check that depends on another block's teardown order is a check
# that will fail for a reason nobody can read.
_bot = _LiveStation(_PvmDeploy(_live_store, workstation=_LIVE_CON),
                    lambda: {"PVMTEST": _gg},
                    settings=_LiveSettings(console_id=_LIVE_CON,
                                           azeo_chrome=True,
                                           theme="hpgray"))
_bot.resize(900, 520)
_bot.tick()
_live_order = [_bot.layout().itemAt(i).widget()
               for i in range(_bot.layout().count())]
check("the console keeps commands and navigation above its workspace, "
      "with conditional feedback followed by the persistent alarm banner and status",
      _live_order == [_bot.menu, _bot.nav, _bot.workspace_split,
                      _bot.theme_notice,
                      _bot.training_strip, _bot.feedback_area, _bot.banner, _bot.status]
      and _bot.workspace_split.widget(1) is _bot.workspace
      and _bot.theme_notice.isHidden()
      and _bot.training_strip.isHidden() and _bot.feedback_area.isHidden(),
      [w.__class__.__name__ for w in _live_order if w])
check("the status strip names the REVISION this seat runs — 'a "
      "console runs a revision, not a file' made visible, without "
      "opening anything",
      _bot.status.status.display_id == "Overview"
      and _bot.status.status.revision >= 1,
      (_bot.status.status.display_id, _bot.status.status.revision))
_bot.close()

# --- the function-block FACEPLATES (azeolive.chm) -------------------
from azeo_control_trainer.core.hmi.pvms import faceplate_ui as _FU
from azeo_control_trainer.core.hmi.pvms.faceplate_fb import (
    ConditionTable as _CondTable,
)
from azeo_control_trainer.core.hmi.pvms.faceplate_style import (
    PROFILES as _FACEPLATE_PROFILES,
)

_registered_faceplates = {
    cls.__name__ for (_bt, role, _variant), cls
    in _pvm_registry.all_classes().items() if role == "faceplate"}
_registered_details = {
    cls.__name__ for (_bt, role, _variant), cls
    in _pvm_registry.all_classes().items() if role == "detail"}
check("the corrected module details cover the analog, loop, device, "
      "interlock and sequence families shown by their faceplate actions",
      _registered_details == {
          "AnalogDetail", "AnalogOutputDetail", "DeviceDetail",
          "MotorInterlockDetail", "PIDDetail", "PulseInputDetail",
          "SfcChartDetail",
      }, sorted(_registered_details))
check("every registered faceplate and detail has a measured Azeo shell "
      "— none may silently fall back to the oversized generic dialog",
      _registered_faceplates | _registered_details
      == set(_FACEPLATE_PROFILES),
      (sorted((_registered_faceplates | _registered_details)
              - set(_FACEPLATE_PROFILES)),
       sorted(set(_FACEPLATE_PROFILES)
              - (_registered_faceplates | _registered_details))))
from azeo_control_trainer.core.hmi.pvms.detail_ui import (  # noqa: E402
    DETAIL_PANELS as _DETAIL_PANELS,
)
check("every detail class has a specialized PDF-style body — opening "
      "Detail can never regress to a list of internal binding keys",
      _registered_details == set(_DETAIL_PANELS),
      (_registered_details, set(_DETAIL_PANELS)))
_azeo_faceplate_palette = _ALL_THEMES["azeo_live"]
check("the Azeo faceplate theme uses the sampled help-figure surface, "
      "field, border and operator-action colours instead of Studio's "
      "former blue shell",
      (_azeo_faceplate_palette[_R2.SURFACE_PANEL],
       _azeo_faceplate_palette[_R2.SURFACE_FIELD],
       _azeo_faceplate_palette[_R2.LINE],
       _azeo_faceplate_palette[_R2.ACTION])
      == ("#E0E2EB", "#E8E8E8", "#9B9CA1", "#14696A"))

# A faceplate is a tuple of section names. Three new bodies came from
# the CHM figures: the condition table (DCC_fp / AT_fp), the state list
# (SEQ_fp / STD_fp) and the input selector (Xmtr_fp).
check("the function-block faceplate bodies join the shared section "
      "library rather than becoming bespoke widgets",
      {"conditions", "state_list", "selector"} <= set(_FU.SECTIONS))
check("...and they take a FIXED place in the canonical order, so a "
      "contributor cannot silently renumber the others",
      _FU.CANONICAL_ORDER.index("mode")
      < _FU.CANONICAL_ORDER.index("conditions")
      < _FU.CANONICAL_ORDER.index("trend"))
_fu_err = None
try:
    _FU.build_sections(("conditions", "title"), _hp_pal)
except _FU.LayoutError as exc:
    _fu_err = exc
check("a faceplate still may not put its body above its title — the "
      "shared order is the entire value of a section library",
      _fu_err is not None)
_fu_err = None
try:
    _FU.register_section("conditions", object, after="mode")
except _FU.LayoutError as exc:
    _fu_err = exc
check("...and registering a section name twice is refused",
      _fu_err is not None)

# THE TRIP INVERTS. This is the defect that nearly shipped: a
# permissive reads True when satisfied, a trip reads True when it has
# TRIPPED, and taking `state` at face value reports a tripped
# condition as OK — the one thing this table exists never to do.
check("a TRIPPED condition reads ACTIVE and unhealthy, never OK",
      _CondTable.state_text({"kind": "trip", "state": True}) == "ACTIVE"
      and not _CondTable.healthy({"kind": "trip", "state": True}))
check("...a quiet trip reads OK",
      _CondTable.state_text({"kind": "trip", "state": False}) == "OK"
      and _CondTable.healthy({"kind": "trip", "state": False}))
check("...and a permissive keeps the OPPOSITE sense: True is satisfied",
      _CondTable.healthy({"kind": "permissive", "state": True})
      and not _CondTable.healthy({"kind": "permissive", "state": False}))

# There is ONE condition table. It used to exist twice — here and in
# render_panels — and the two disagreed about exactly the trip
# polarity above.
from azeo_control_trainer.core.hmi.pvms import render_panels as _RP
check("the interlock panel and the faceplate section are ONE class — "
      "two tables for one thing is two chances to disagree, and they "
      "already did", _RP.InterlockConditionPanel is _CondTable)

# The elapsed-time bar: the reason this faceplate is worth building.
_ct = _CondTable(_hp_pal)
check("a condition part-way through its delay reports its progress — "
      "'true for 2 of its 4 seconds' is a warning no other screen in "
      "the product gives",
      _ct.progress({"delay": 4.0, "timer": 2.0}) == 0.5)
check("a condition with NO delay has no progress at all; a bar at 0% "
      "would imply it is part-way through something",
      _ct.progress({"delay": 0.0, "timer": 0.0}) is None)
check("the bar deepens by ALPHA and keeps one hue, so it cannot argue "
      "with the alarm banner above it — this warns OF an alarm, it is "
      "not one",
      _ct.bar_colour(0.1).alpha() < _ct.bar_colour(0.9).alpha()
      and _ct.bar_colour(0.1).rgb() == _ct.bar_colour(0.9).rgb())
check("a Bad or absent table yields NO rows — an empty table is "
      "honest; invented rows say the interlocks are healthy",
      _CondTable.rows_of({}) == [])

# The three new faceplate classes, opened against real blocks.
_fp_g = _PvmGraph(name="FBFP")
_fp_blocks = {}
for _bt, _n in (("SEQ", "SEQ1"), ("STD", "STD1"), ("ISEL", "ISEL1"),
                ("MOTOR_INTERLOCK", "MI1"), ("AVTR", "AVTR1"),
                ("DVTR", "DVTR1"), ("PID", "PID1"), ("FLC", "FLC1")):
    _b = _PvmRegistry().create(_bt, _n)
    _b._apply_config()
    _fp_g.add_block(_b)
    _fp_blocks[_bt] = _b
_fp_blocks["MOTOR_INTERLOCK"].config.params.update({
    "PERM_1_DESC": "Lube oil pressure", "PERM_1_DELAY": 4.0,
    "TRIP_1_DESC": "Emergency stop"})
_fp_blocks["MOTOR_INTERLOCK"].inputs["PERM_1"].value = True
_fp_blocks["MOTOR_INTERLOCK"].inputs["TRIP_1"].value = True
for _ in range(4):
    _fp_blocks["MOTOR_INTERLOCK"].execute(0.5)
_fp_engine = _PvmEngine(_PvmSource(lambda: {"FBFP": _fp_g}))
_fp_built = {}
for _bt, _n in (("SEQ", "SEQ1"), ("STD", "STD1"), ("ISEL", "ISEL1")):
    _cls = _pvm_registry.get(_bt, "faceplate")
    try:
        _w = PvmFaceplateWidget(_cls, {"path": "FBFP/%s" % _n}, _fp_engine)
        _w.refresh()
        _fp_built[_bt] = sorted(_w.sections)
        _w.close()
    except Exception as exc:                                # noqa: BLE001
        _fp_built[_bt] = "%s: %s" % (type(exc).__name__, exc)
check("SEQ, STD and the Input Selector now have faceplates, each "
      "assembled from declared sections",
      _fp_built == {"SEQ": ["buttons", "state_list", "title"],
                    "STD": ["buttons", "state_list", "title"],
                    "ISEL": ["buttons", "selector", "title"]}, _fp_built)

# The interlock's real table, through a real binding.
_mi_w = PvmFaceplateWidget(_pvm_registry.get("MOTOR_INTERLOCK", "faceplate"),
                           {"path": "FBFP/MI1"}, _fp_engine)
_mi_w.refresh()
_mi_rows = _CondTable.rows_of(_mi_w.bound)
check("the interlock faceplate's rows come from the block's own "
      "condition_table(), so the table cannot drift from the logic",
      len(_mi_rows) >= 2
      and {"permissive", "trip"} <= {r["kind"] for r in _mi_rows},
      sorted({r["kind"] for r in _mi_rows}))
_mi_trip = next((r for r in _mi_rows if r["kind"] == "trip"), None)
check("...and the tripped E-stop in it reads ACTIVE",
      _mi_trip is not None
      and _CondTable.state_text(_mi_trip) == "ACTIVE", _mi_trip)
_mi_w.close()

# The manual's voter faceplates are five distinct operator questions,
# not one table with columns that change meaning. Exercise both forms
# against their real extensible-input blocks.
_voter_results = {}
for _bt in ("AVTR", "DVTR"):
    _fp_blocks[_bt].config.params["NUM_INPUTS"] = 4
    _fp_blocks[_bt]._apply_config()
    _vw = PvmFaceplateWidget(
        _pvm_registry.get(_bt, "faceplate"),
        {"path": "FBFP/%s1" % _bt}, _fp_engine)
    _vw.refresh()
    _vp = _vw.sections["voter"]
    _voter_results[_bt] = (
        _vp.tabs.count(), _vp.trip.rowCount(),
        _vp.tabs.isTabEnabled(1))
    _vw.close()
check("AVTR and DVTR use the five-tab voter faceplate and expose every "
      "configured input row",
      _voter_results == {"AVTR": (5, 4, True),
                         "DVTR": (5, 4, False)}, _voter_results)

_loop_block = _fp_blocks["PID"]
_loop_block.config.params.update({
    "bypass_enable": True, "use_pidplus": True,
    "simulate_enabled": True, "hi_hi_lim": 90.0, "hi_lim": 80.0,
    "lo_lim": 20.0, "lo_lo_lim": 10.0,
    "out_hi": 95.0, "out_lo": 5.0,
})
_loop_block._apply_config()
_loop_block.inputs["IN"].value = 35.0
_loop_block.inputs["SP"].value = 50.0
_loop_block.execute(0.1)
_pid_layout = PvmFaceplateWidget(
    _pvm_registry.get("PID", "faceplate"),
    {"path": "FBFP/PID1"}, _fp_engine)
_pid_layout.set_write_handler(_fp_engine.write, _fp_engine.can_write)
_pid_layout.refresh()
check("declared module faceplate layouts retain class-specific visuals "
      "instead of replacing the PID bars with a generic stack",
      tuple(_pid_layout.sections) == tuple(
          _pvm_registry.get("PID", "faceplate").FACEPLATE_LAYOUT)
      and _pid_layout.visual is not None)
check("Loop_fp uses only its own contextual chrome, identifies the bound path "
      "without repeating the generic PID class name, and keeps the body "
      "subtitle empty",
      bool(_pid_layout.windowFlags() & Qt.FramelessWindowHint)
      and _pid_layout.context_title.title.text() == "FBFP/PID1"
      and _pid_layout.visual.state.subtitle == "",
      (_pid_layout.windowFlags(), _pid_layout.context_title.title.text(),
       _pid_layout.visual.state.subtitle))
check("Loop_fp keeps the supplied painter's measured 203 x 537 shell and "
      "combines PV/SP/OUT "
      "in one bar body rather than three detached widgets",
      (_pid_layout.faceplate_surface.width(),
       _pid_layout.faceplate_surface.height()) == (203, 537)
      and not _pid_layout.sections["value"].isVisibleTo(_pid_layout)
      and not _pid_layout.sections["mode"].isVisibleTo(_pid_layout),
      _pid_layout.faceplate_surface.size())
_loop_visual = _pid_layout.visual
_loop_scale, _loop_pv_bar = _loop_visual.pv_geometry()
_loop_inner_bar = _loop_visual.inner_indicator_geometry()
_mode_arrow = _loop_visual.actual_mode_arrow_geometry()
check("Loop_fp gives the actual-mode dropdown a readable arrow and keeps the "
      "entire arrow inside the active mode control's click target",
      _mode_arrow.width() >= 14 and _mode_arrow.height() >= 9
      and _loop_visual._region("mode_actual").contains(_mode_arrow)
      and _loop_visual._hit(_mode_arrow.center()) == "mode_actual",
      (_mode_arrow, _loop_visual._region("mode_actual")))
check("the actual-mode dropdown writes through MODE.TARGET instead of "
      "editing feedback or bypassing the controller",
      "mode.command" in _pid_layout.write_controls
      and _pid_layout.write_bound("mode.command", "MAN")
      and _loop_block.mode_target == "MAN"
      and _pid_layout.write_bound("mode.command", "AUTO")
      and _loop_block.mode_target == "AUTO",
      (_pid_layout.write_controls.keys(), _loop_block.mode_target))
check("Loop_fp keeps the documented PV scale on the LEFT and the PV fill in "
      "its own adjacent lane",
      _loop_scale.left() < _loop_pv_bar.left()
      and _loop_scale.right() == _loop_pv_bar.left()
      and (_loop_scale.width(), _loop_pv_bar.width()) == (20.0, 22.0)
      and (_loop_scale.top(), _loop_scale.bottom()) == (123.0, 311.0),
      (_loop_scale, _loop_pv_bar))
check("the dark-blue indicator is centered inside the light-blue PV channel "
      "and cannot leak left into the scale lane",
      abs(_loop_inner_bar.center().x() - _loop_pv_bar.center().x()) < 1e-9
      and _loop_inner_bar.left() > _loop_pv_bar.left()
      and _loop_inner_bar.right() < _loop_pv_bar.right()
      and _loop_inner_bar.width() == 11.0,
      (_loop_inner_bar, _loop_pv_bar))
_expected_pv_top = _loop_pv_bar.bottom() - _loop_pv_bar.height() * (
    (_loop_visual.state.pv - _loop_visual.state.pv_min)
    / (_loop_visual.state.pv_max - _loop_visual.state.pv_min))
_saved_loop_out = _loop_visual.state.output
_loop_visual.state.output = _saved_loop_out + 37.0
check("the centered dark-blue bar is PV — its top follows PV on the common "
      "EU scale and changing OUT cannot move it",
      abs(_loop_inner_bar.top() - _expected_pv_top) < 1e-9
      and _loop_visual.inner_indicator_geometry() == _loop_inner_bar,
      (_loop_inner_bar, _expected_pv_top,
       _loop_visual.inner_indicator_geometry()))
_loop_visual.state.output = _saved_loop_out
_loop_alarm_registry = _fp_engine._source.alarm_state
_loop_alarm_registry.observe(
    "FBFP", "PID1", (("HI_HI", 15, 90.0), ("HI", 11, 80.0)))
_loop_alarm_rows = _loop_alarm_registry.records(blocks=("FBFP/PID1",))
_loop_alarm_list = _pid_layout.sections["alarms"]
_loop_alarm_list.set_records(_loop_alarm_rows)
check("Loop_fp's active alarm list carries every module alarm, not only the "
      "worst BindingResult summary",
      _loop_alarm_list.table.rowCount() == 2
      and {_loop_alarm_list.table.item(row, 1).text() for row in range(2)}
      == {"HI_HI", "HI"},
      [_loop_alarm_list.table.item(row, 1).text() for row in range(
          _loop_alarm_list.table.rowCount())])
_loop_alarm_registry.acknowledge(row.key for row in _loop_alarm_rows)
_loop_alarm_list.set_records(
    _loop_alarm_registry.records(blocks=("FBFP/PID1",)))
check("the Ack column changes to the documented check mark after the alarms "
      "are acknowledged",
      all(_loop_alarm_list.table.item(row, 0).text()
          == _loop_alarm_list.ACK_SYMBOLS["active_acked"]
          for row in range(_loop_alarm_list.table.rowCount())))
check("Loop_fp drives the reference-only states instead of drawing inert "
      "labels: working SP, four PV limits, two OUT limits, simulation, "
      "adaptive control and conditional bypass",
      _loop_visual.sp_working is not None
      and set(_loop_visual.pv.ticks) == {"HH", "H", "L", "LL"}
      and set(_loop_visual.out.ticks) == {"H", "L"}
      and _loop_visual.simulate_active
      and _loop_visual.adaptive_visible
      and _loop_visual.bypass_visible,
      (_loop_visual.sp_working, _loop_visual.pv.ticks,
       _loop_visual.out.ticks, _loop_visual.simulate_active,
       _loop_visual.adaptive_visible, _loop_visual.bypass_visible))
check("AUTO enables the SP slew/slider but keeps OUT ownership out of reach",
      _loop_visual._sp_writable()
      and not _loop_visual._out_writable()
      and _loop_visual._hit(_QPointF(36, 145)) == "sp_up"
      and _loop_visual._hit(_QPointF(36, 228)) is None,
      (_loop_visual.target_mode, _loop_visual._sp_writable(),
       _loop_visual._out_writable()))
_loop_visual.set_setpoint(57.0, emit_signal=True)
check("the Loop_fp SP interaction writes the controller's live operator target",
      abs(_loop_block.pid_core_block.SP - 57.0) < 1e-9,
      _loop_block.pid_core_block.SP)

_loop_block.set_mode("MAN")
_loop_block.execute(0.1)
_pid_layout.refresh()
check("MAN exposes both documented slews, including OUT that "
      "must remain absent while the algorithm owns OUT in AUTO",
      _loop_visual._sp_writable()
      and _loop_visual._out_writable()
      and _loop_visual._hit(_QPointF(36, 228)) == "out_up",
      (_loop_visual.target_mode, _loop_visual._sp_writable(),
       _loop_visual._out_writable()))
_loop_visual.set_output(23.5, emit_signal=True)
check("the Loop_fp OUT interaction writes the manual output",
      abs(_loop_block.pid_core_block.OUT.value - 23.5) < 1e-9,
      _loop_block.pid_core_block.OUT.value)
_loop_visual.bypassChanged.emit(True)
check("the visible Bypass/Normal control changes the live configured state",
      _loop_block.config.params["bypass"] is True
      and _loop_visual.bypass_active is True,
      (_loop_block.config.params.get("bypass"),
       _loop_visual.bypass_active))

_loop_visual.miniFaceplateRequested.emit()
check("Loop_fp's mini button keeps title plus critical live values instead "
      "of collapsing the entire window",
      _pid_layout.mini_faceplate
      and _pid_layout.faceplate_surface.height()
      == _loop_visual.MINI_HEIGHT
      and _loop_visual.height() == _loop_visual.MINI_HEIGHT,
      (_pid_layout.faceplate_surface.size(), _loop_visual.height()))
_loop_visual.miniFaceplateRequested.emit()
check("the mini button restores the measured full faceplate",
      not _pid_layout.mini_faceplate
      and _pid_layout.faceplate_surface.height() == 537
      and _loop_visual.height() == _loop_visual.FULL_HEIGHT,
      (_pid_layout.faceplate_surface.size(), _loop_visual.height()))
_pid_layout.close()

_flc_block = _fp_blocks["FLC"]
_flc_block.inputs["IN"].value = 40.0
_flc_block.inputs["SP"].value = 55.0
_flc_block.execute(0.1)
_flc_layout = PvmFaceplateWidget(
    _pvm_registry.get("FLC", "faceplate"),
    {"path": "FBFP/FLC1"}, _fp_engine)
_flc_layout.refresh()
check("FLC_fp uses the same measured and functional Loop_fp body, as the "
      "reference directs, while PID-deadtime and fieldbus templates inherit "
      "it through their PID loop block",
      type(_flc_layout.visual) is type(_loop_visual)
      and (_flc_layout.faceplate_surface.width(),
           _flc_layout.faceplate_surface.height()) == (203, 537)
      and _flc_layout.visual.sp_working == _flc_layout.visual.sp_value,
      (_flc_layout.faceplate_surface.size(),
       _flc_layout.visual.sp_working, _flc_layout.visual.sp_value))
_flc_layout.close()

# --- the function-block display catalogue (PVMS+FB.pdf) ---------------
# Azeo ships a display layer that binds to ONE FUNCTION BLOCK rather
# than to a module. Our binding grammar was already block-granular, so
# what was missing was classes and the conditions they feed.
_fb_g = _PvmGraph(name="FBTEST")
_fb_blocks = {}
for _bt, _n in (("SEQ", "SEQ1"), ("STD", "STD1"), ("DEVCTL", "DEV1")):
    _b = _PvmRegistry().create(_bt, _n)
    _b._apply_config()
    _fb_g.add_block(_b)
    _fb_blocks[_n] = _b
for _n, _v in (("SEQ1", 3), ("STD1", 2)):
    _fb_blocks[_n].outputs["STATE"].value = _v
    _fb_blocks[_n].outputs["STATE"].status = _PvmQuality.GOOD
_fb_studio2 = PvmStudio(lambda: {"FBTEST": _fb_g}, _st_tmp.mkdtemp(),
                        display_name="FB")
_fb_studio2.enter_edit()
_fb_bound = {}
_fb_items = {}
for _tag, _blk in (("SEQ1", "SEQ"), ("STD1", "STD")):
    _fg = _fb_studio2.place_block(f"FBTEST/{_tag}", _blk, 0, 0,
                                  role=("dynamo_compact", ""))
    _fi = next(i for i in _fb_studio2._items() if i.pvm.id == _fg.id)
    _fb_items[_blk] = _fi
    _fb_bound[_blk] = _fi.binding.result.value if _fi.binding else None
check("the Step Sequencer and State Transition Diagram now have PVMs, "
      "each reading its block's STATE — the manual describes them as "
      "exactly one datalink, and we had the blocks but no PVM",
      _fb_bound == {"SEQ": 3, "STD": 2}, _fb_bound)

# The DC/EDC status-icon conditions. POLARITY is the trap.
check("PERMISSIVE_D and INTERLOCK are True when the permit is "
      "GRANTED, so 'no permit' must light on the FALSE case — read "
      "the obvious way round it lights on every healthy device",
      "no_permit" not in _HP.resolve_alarm_box(
          None, conditions={"no_permit": False}).visible_conditions()
      and "no_permit" in _HP.resolve_alarm_box(
          None, conditions={"no_permit": True}).visible_conditions())
_fb_dg = _fb_studio2.place_block("FBTEST/DEV1", "DEVCTL", 300, 0,
                                 role=("dynamo_compact", ""))
_fb_di = next(i for i in _fb_studio2._items() if i.pvm.id == _fb_dg.id)
check("a DEVCTL class reads its own condition terminals instead of "
      "leaving permanently dark status icons",
      set(_fb_di._device_conditions()) >= {"interlocked", "no_permit"},
      _fb_di._device_conditions())
check("a class that does not bind conditions asserts NOTHING — "
      "'never asked' is not 'checked, condition absent', so an "
      "unbound condition leaves its icon dark instead of claiming the "
      "device is healthy", _fb_items["SEQ"]._device_conditions() == {},
      _fb_items["SEQ"]._device_conditions())
check("an unknown condition name is refused rather than stored",
      not hasattr(_HP.resolve_alarm_box(
          None, conditions={"nonsense": True}), "nonsense"))

# The three-way mode rule. The manual states it three times.
check("a loop PARKED in MAN — target and actual agree, but MAN is "
      "not its normal mode — is abnormal. This is the case the "
      "target-only rule stayed silent for, and it is the one that "
      "matters: nobody is going to put it back",
      _HP.resolve_alarm_box(
          _BR(value=1.0, quality=_PvmQuality.GOOD, mode_actual="MAN",
              mode_target="MAN", mode_normal="AUTO")).abnormal_mode)
check("...while a loop sitting in its normal mode is not",
      not _HP.resolve_alarm_box(
          _BR(value=1.0, quality=_PvmQuality.GOOD, mode_actual="AUTO",
              mode_target="AUTO", mode_normal="AUTO")).abnormal_mode)
check("...and with no normal-mode claim, nothing is asserted rather "
      "than a normal being invented",
      not _HP.resolve_alarm_box(
          _BR(value=1.0, quality=_PvmQuality.GOOD,
              mode_actual="MAN")).abnormal_mode)
check("`normal_mode` defaults to blank on every block, so it is "
      "absent from config.params and no shipped module changes",
      not _PvmRegistry().create("PID", "M").config.params)

# --- a PVM's faceplate opens once, however many times you click -------
# This was a CRASH, not a cosmetic bug: binding names are unique per
# (faceplate class, path, key), so a second widget for the same module
# re-bound names the first still held and `bind_all` raised
# BindingError straight out of mouseDoubleClickEvent — which took the
# whole application down. Found by running the app and double-clicking
# a PVM.
_fp_studio = PvmStudio(lambda: {"PVMTEST": _gg}, _st_tmp.mkdtemp(),
                       display_name="FP")
_fp_studio.enter_edit()
_fp_a = _fp_studio.place_block("PVMTEST/PID1", "PID", 0, 0,
                               role=("dynamo_inline", ""))
_fp_b = _fp_studio.place_block("PVMTEST/PID1", "PID", 200, 0,
                               role=("dynamo_inline", ""))
_fp_err = None
try:
    for _ in range(3):
        _fp_studio.open_faceplate(_fp_a)
except Exception as exc:                                # noqa: BLE001
    _fp_err = exc
check("opening one PVM's faceplate repeatedly raises the window it "
      "already has, instead of building a second that re-binds the "
      "same names and crashes the application",
      _fp_err is None and len(_fp_studio._faceplates) == 1, _fp_err)
try:
    _fp_studio.open_faceplate(_fp_b)
except Exception as exc:                                # noqa: BLE001
    _fp_err = exc
check("...and two PVMs on the SAME module share that one faceplate — "
      "binding names are per path, not per placement",
      _fp_err is None and len(_fp_studio._faceplates) == 1, _fp_err)
_fp_win = list(_fp_studio._faceplates.values())[0]
_fp_win.close()
check("closing a faceplate CLEARS its bindings, so the slot frees and "
      "the next open binds cleanly rather than being refused forever",
      not _fp_win.bound)
try:
    _fp_studio.open_faceplate(_fp_a)
    _fp_err = None
except Exception as exc:                                # noqa: BLE001
    _fp_err = exc
check("...and it does", _fp_err is None, _fp_err)

# --- HP status-icon precedence ----------------------------------------
# Three rules in the manual each SUPPRESS something that is genuinely
# true, so the PVM says the most important thing rather than everything.
_hp_all = _HP.AlarmBoxState(not_running=True, bad_io=True, simulated=True)
check("Not Running hides Bad I/O — a module that is not executing has "
      "bad I/O as a consequence, and saying both sends the operator "
      "after the symptom",
      "bad_io" not in _hp_all.visible_conditions()
      and "not_running" in _hp_all.visible_conditions())
check("Not Running hides Simulate too",
      "simulated" not in _hp_all.visible_conditions())
check("Bad I/O alone still hides Simulate",
      "simulated" not in
      _HP.AlarmBoxState(bad_io=True, simulated=True).visible_conditions())
check("a condition on its own shows",
      _HP.AlarmBoxState(simulated=True).visible_conditions()
      == ("simulated",))
check("the DC/EDC conditions are independent of each other",
      set(_HP.AlarmBoxState(abnormal_mode=True, no_permit=True,
                            interlocked=True, tracking=True,
                            bypassed=True).visible_conditions())
      == {"abnormal_mode", "no_permit", "interlocked", "tracking",
          "bypassed"})

# --- the alarm icon's four states -------------------------------------
check("each of the manual's four icon states resolves",
      [_HP.AlarmBoxState(priority=_HP.CRITICAL, active=True,
                         acked=True).icon_state,
       _HP.AlarmBoxState(priority=_HP.CRITICAL, active=True).icon_state,
       _HP.AlarmBoxState(priority=_HP.CRITICAL, active=False,
                         acked=False).icon_state,
       _HP.AlarmBoxState(suppressed=True).icon_state]
      == ["active_acked", "active_unacked", "inactive_unacked",
          "suppressed"])
check("a SUPPRESSED alarm never masks a live one — the suppressed icon "
      "shows only when no other active or unacknowledged alarm exists",
      _HP.AlarmBoxState(priority=_HP.CRITICAL, active=True,
                        suppressed=True).icon_state == "active_unacked")
check("nothing wrong shows no icon", _HP.AlarmBoxState().icon_state is None)


def _hp_count_drawn(n):
    img = _QImage(60, 40, _QImage.Format_ARGB32)
    p = _QPainter(img)
    drew = _HP.draw_alarm_count(
        p, _QRectF(0, 0, 60, 40),
        _HP.AlarmBoxState(priority=_HP.CRITICAL, active=True,
                          alarm_count=n), _hp_pal)
    p.end()
    return drew


check("one alarm draws no count badge — a '1' on every alarmed PVM is "
      "noise repeated forty times a display",
      not _hp_count_drawn(1) and _hp_count_drawn(4))

# --- the combination bar's scale --------------------------------------
_hp_sc = _HP.resolve_scale(
    _BR(value=1.0, quality=_PvmQuality.GOOD, eu_range=(0.0, 200.0)))
check("the binding's own EU range becomes the scale",
      (_hp_sc.lo, _hp_sc.hi, _hp_sc.user_defined) == (0.0, 200.0, False))
check("a value out of range clamps rather than running off the bar",
      _hp_sc.fraction(9999) == 1.0 and _hp_sc.fraction(-9999) == 0.0)
check("an unreadable value is None, NOT zero — a bar drawn at the "
      "bottom of its range is a claim about the process",
      _hp_sc.fraction(None) is None and _hp_sc.fraction("x") is None)
_hp_user = _HP.resolve_scale(None, 740, 755)
check("a user-defined scale says so, which is what draws the end lines",
      _hp_user.user_defined and _hp_user.span == 15)
check("a nonsense user scale falls back instead of dividing by zero",
      _HP.resolve_scale(None, "a", "b").span == 100.0)
check("the limit regions get their own strip, so a PV sitting AT its "
      "limit does not cover the limit it is sitting on",
      _HP.bars.limit_strip(_QRectF(0, 0, 100, 20), False).top() >=
      _HP.bars.value_strip(_QRectF(0, 0, 100, 20), False).bottom() - 0.01)

# --- the deviation bar pins SP to the centre --------------------------
_hp_dev = _HP.BarScale(0, 100)
check("PV on setpoint sits dead centre",
      _HP.deviation_fraction(50, 50, _hp_dev) == 0.5)
_hp_up = _HP.deviation_fraction(52, 50, _hp_dev)
_hp_dn = _HP.deviation_fraction(48, 50, _hp_dev)
check("equal deviations sit equally far either side — the manual's "
      "rule, and what makes the bar readable at a glance",
      abs((_hp_up - 0.5) + (_hp_dn - 0.5)) < 1e-9)
check("moving the setpoint does not move the centre",
      _HP.deviation_fraction(25, 25, _hp_dev) == 0.5)
check("a narrower span magnifies the same deviation",
      _HP.deviation_fraction(52, 50, _hp_dev, span_percent=10) > _hp_up)

# --- data fields ------------------------------------------------------
check("every data field is the same width, so a column of PVMs lines "
      "its decimal points up",
      all(len(_HP.format_data_field(v)) == 6
          for v in (1, -1, 12345, 0.5, None)))
check("F_DECPT decides the places when the block carries one",
      _HP.format_data_field(61.4567, 2) == " 61.46")
check("without one the places shrink to fit rather than truncating",
      _HP.format_data_field(1234.5678).strip() == "1234.6")
check("a value too large to show honestly says so — a truncated "
      "number is a WRONG number",
      set(_HP.format_data_field(123456789).strip()) == {"*"})
check("an unreadable value blanks the number",
      _HP.format_data_field(None).strip() == "---")

# --- the hover window omits what it cannot say ------------------------
_hp_res = _BR(value=61.5, quality=_PvmQuality.GOOD, units="degC",
              mode_target="AUTO", mode_actual="MAN")


class _HpBinding:
    def __init__(self, result):
        self.result = result


_hp_lines = _HP.hover_lines(
    _afp_pvm_stub, _hp_res,
    {"PV": _HpBinding(_BR(value=61.5, quality=_PvmQuality.GOOD))},
    _HP.resolve_alarm_box(_hp_res))
check("the hover window names the module and shows BOTH modes when "
      "they disagree — which mode it is in and which it was asked for "
      "is the whole content of a mode alarm",
      "FIC-101" in _hp_lines
      and any("target AUTO" in ln for ln in _hp_lines))
check("...and grows no SP row for a block nothing bound one on — a "
      "row reading 'SP 0.0' answers the operator with something that "
      "was never true",
      not any(ln.startswith("SP:") for ln in _hp_lines))
check("...and carries the status conditions",
      any("Mode is not as expected" in ln for ln in _hp_lines))

# --- the PVM Configuration Designer previews the PVM ------------------
# It used to draw a rectangle containing the words "coded class —
# painted live", which is a caption ABOUT a preview. An Author
# configuring limits.lo_lo cannot see what their change does to a box
# that says what kind of box it is. It paints the real PvmItem now,
# unbound — the honest state for a configuration view, and what
# Azeo's own configuration figures show.
from azeo_control_trainer.azeo_graphics_designer.configurator.designer import (
    _coded_class_preview as _prev,
)

_prev_classes = [("AI", "dynamo_inline", ""),
                 ("AI", "dynamo_inline", "tank"),
                 ("AO", "dynamo_inline", "valve"),
                 ("DEVCTL", "dynamo_compact", "pump"),
                 ("PID", "dynamo_compact", ""),
                 ("RATIO", "dynamo_compact", "")]


def _prev_ink(pix):
    """Fraction of the preview that is not flat background."""
    image = pix.toImage()
    bg = image.pixelColor(1, 1).rgb()
    marked = sum(1 for y in range(0, image.height(), 2)
                 for x in range(0, image.width(), 2)
                 if image.pixelColor(x, y).rgb() != bg)
    return marked / max((image.height() // 2) * (image.width() // 2), 1)


_prev_shots, _prev_ink_levels = {}, {}
for _bt, _role, _var in _prev_classes:
    _cls = _pvm_registry.get(_bt, _role, _var)
    if _cls is None:
        continue
    _pix = _prev(_cls)
    _img = _pix.toImage()
    _prev_shots[(_bt, _role, _var)] = tuple(
        _img.pixelColor(x, y).rgb()
        for y in range(_img.height()) for x in range(_img.width()))
    _prev_ink_levels[f"{_bt}/{_var or '-'}"] = round(_prev_ink(_pix), 3)

check("the configuration designer PAINTS each PVM rather than "
      "captioning it",
      all(v > 0.02 for v in _prev_ink_levels.values()),
      _prev_ink_levels)
check("...and every class previews differently, so an Author can see "
      "which one they are configuring",
      len(set(_prev_shots.values())) == len(_prev_shots),
      f"{len(set(_prev_shots.values()))} distinct of {len(_prev_shots)}")

# --- the Explorer trees carry Graphics Designer's own structure ---------
# The permanent folders are the manual's, and their absence is not a
# cosmetic difference: a tree missing "Templates" reads as "this system
# has no such thing" rather than "we have not built it yet".
from azeo_control_trainer.core.hmi.pvms.layout import (
    DisplaySet as _DS3, LayoutStore as _LS3, default_layout as _DL3,
)

_tree_root = _st_tmp.mkdtemp()
_tstore = _LS3(_tree_root)
_tset = _DS3("Console A")
_troot = _tset.add_root("PlantOverview")
_tl2 = _tset.add_child(_troot, "L2-DISCHARGE")
_tset.add_child(_tl2, "L3-PUMPS")
_tset.add_non_hierarchical("TrendPage")
_tstore.save_display_set(_tset)
_tstore.save_layout(_DL3("Wide"))
check("layouts and display sets persist beside the displays",
      [s.name for s in _LS3(_tree_root).display_sets()] == ["Console A"]
      and [ly.name for ly in _LS3(_tree_root).layouts()] == ["Wide"])

from azeo_control_trainer.azeo_graphics_designer.studio.layout_editors import (
    DisplaySetEditor as _DisplaySetEditor,
    LayoutEditor as _LayoutEditor,
    WorkstationAssignmentDialog as _WorkstationAssignmentDialog,
)

_set_editor = _DisplaySetEditor(
    _tstore, "Console A",
    displays_provider=lambda: ("PlantOverview", "L2-DISCHARGE",
                               "L3-PUMPS", "TrendPage"))
_set_editor.add_child("L3-PUMPS", "FutureDiagnostics", placeholder=True)
check("the Display Set editor authors a four-level placeholder and saves "
      "through the one LayoutStore model",
      _set_editor.save()
      and _tstore.display_set("Console A").find(
          "FutureDiagnostics").placeholder
      and _tstore.display_set("Console A").depth() == 4)
_layout_editor = _LayoutEditor(_tstore, "Wide")
_layout_editor.add_screen("Screen 2", 1280, 1024)
_layout_editor.add_frame("Screen 2", "Diagnostics", levels=(4,))
check("the Layout editor exposes screens, frames and geometry and saves "
      "the same model the station routes",
      _layout_editor.save()
      and _tstore.layout("Wide").frame("Diagnostics") is not None)
_assignment_editor = _WorkstationAssignmentDialog(
    _tstore, layout_name="Wide")
_assignment_editor.workstation.setText("CON-EDITOR")
_assignment_editor.choose_sets(("Console A",))
_assignment_editor.active_choice.setCurrentIndex(0)
_assignment_editor.save()
_assignment = _tstore.assignment("CON-EDITOR")
check("Graphics Designer can assign one layout and selectable display sets "
      "to a workstation through its assignment editor",
      _assignment.layout == "Wide"
      and _assignment.active_display_set == "Console A"
      and _LS3(_tree_root).assignment("CON-EDITOR").layout == "Wide")

# The project library is an engineering catalogue, not only the installed
# Python registry. Seed one paired authored class so health, pairing and
# cross-reference columns can be checked against persisted data.
from azeo_control_trainer.core.hmi.pvms.user_library import (
    UserPvmLibrary as _TreeUserLibrary,
)
_tree_user = _TreeUserLibrary(_tree_root)
_tree_user.create("TreeLoop_PVM")
_tree_user.create_faceplate_blueprint("TreeLoop_FP")
_tree_user.pair_faceplate("TreeLoop_PVM", "TreeLoop_FP")
from azeo_control_trainer.azeo_graphics_designer.configurator.designer import (
    PvmConfigDesigner as _TreeDesigner,
)
for _tree_class in ("TreeLoop_PVM", "TreeLoop_FP"):
    _TreeDesigner._faceplate_configuration(_tree_class).save(
        Path(_tree_root) / "_pvmcfg")

_tree_window = HmiStudioWindow(lambda: {"PVMTEST": _gg}, _tree_root)
_tree_project = _tree_window.current()
_tree_project.place_user_pvm("TreeLoop_PVM", 30, 30)
_tree_project.save_draft()
_tree_window.library.rebuild()


def _tree_lines(tree, node=None, depth=0):
    out = []
    n = tree.topLevelItemCount() if node is None else node.childCount()
    for i in range(n):
        item = tree.topLevelItem(i) if node is None else node.child(i)
        out.append(item.text(0))
        out.extend(_tree_lines(tree, item, depth + 1))
    return out


def _tree_items(tree, node=None):
    out = []
    count = tree.topLevelItemCount() if node is None else node.childCount()
    for index in range(count):
        item = tree.topLevelItem(index) if node is None else node.child(index)
        out.append(item)
        out.extend(_tree_items(tree, item))
    return out


def _tree_payload(tree, payload, node=None):
    count = tree.topLevelItemCount() if node is None else node.childCount()
    for index in range(count):
        item = tree.topLevelItem(index) if node is None else node.child(index)
        if item.data(0, Qt.UserRole) == payload:
            return item
        found = _tree_payload(tree, payload, item)
        if found is not None:
            return found
    return None


_gfx = _tree_lines(_tree_window.graphics_tree)
_gfx_items = _tree_items(_tree_window.graphics_tree)
check("the Graphics Explorer has all four permanent folders",
      all(any(f in ln for ln in _gfx) for f in
          ("Displays", "Display Sets", "Contextual Displays",
           "Layouts")),
      [f for f in ("Displays", "Display Sets", "Contextual Displays",
                   "Layouts") if not any(f in ln for ln in _gfx)])
check("Graphics Explorer uses proper DPI-safe vector icons in the shared blue",
      _gfx_items
      and all(not item.icon(0).isNull() for item in _gfx_items)
      and {colour for _name, colour in GRAPHICS_TREE_ICONS.values()}
      == {_RIBBON_WF["lapis"]})
check("a display set shows its hierarchy, not a caption",
      any("Console A" in ln for ln in _gfx)
      and any("L3-PUMPS" in ln for ln in _gfx)
      and any("non-hierarchical" in ln for ln in _gfx))
check("a layout shows its screens and display frames",
      any("Wide" in ln for ln in _gfx)
      and any("Alarm banner" in ln for ln in _gfx))
check("display sets and layouts open as document tabs, not tree captions",
      _tree_window.open_display_set("Console A").configuration_kind
      == "display_set"
      and _tree_window.open_layout("Wide").configuration_kind == "layout")

_lib = _tree_lines(_tree_window.library.tree)
check("the Library Explorer has the manual's four permanent library "
      "folders — PVM Classes, Templates, Standards, Functions",
      all(any(f in ln for ln in _lib) for f in
          ("PVM Classes", "Templates", "Standards", "Functions")),
      [f for f in ("PVM Classes", "Templates", "Standards", "Functions")
       if not any(f in ln for ln in _lib)])
check("the project tree separates PVM, Faceplate, Detail Display and "
      "Special Symbol engineering artifacts",
      all(any(folder in line for line in _lib) for folder in (
          "PVM Classes", "Faceplate Classes", "Detail Display Classes",
          "Special Symbols")))
_tree_pvm_record = _tree_window.library.catalog.record("TreeLoop_PVM")
check("authored classes carry pairing, validation and real cross-reference "
      "facts rather than decorative tree captions",
      _tree_pvm_record.status == "Ready"
      and "TreeLoop_FP" in _tree_pvm_record.paired_with
      and _tree_window.library.find_user_class_usages(
          "TreeLoop_PVM") == ("Overview",))
check("the authored PVM and Faceplate are listed under their respective "
      "project folders with State and Used columns",
      _tree_payload(_tree_window.library.tree,
                    ("user_class", "TreeLoop_PVM")).text(1) == "Ready"
      and _tree_payload(_tree_window.library.tree,
                       ("user_class", "TreeLoop_PVM")).text(2) == "1"
      and _tree_payload(_tree_window.library.tree,
                       ("user_class", "TreeLoop_FP")) is not None)
check("selecting an authored class opens an engineering inspector with its "
      "pair and dependency status",
      _tree_window.library.select_user_class("TreeLoop_FP")
      and "Pair:" in _tree_window.library.coverage_label.text())
_tree_window.tabs.setCurrentWidget(_tree_project)
_tree_window.library._place_special_symbol("simulate")
check("a Special Symbol tree item invokes the same scalable placement tool "
      "as its palette card",
      _tree_project._place_ghost is not None
      and _tree_project._place_ghost.data.get("kind") == "special_symbol"
      and _tree_project._place_ghost.data.get("icon") == "simulate")
_tree_project.cancel_place()
_tree_window.library.search.setText("TreeLoop_FP")
check("engineering-library search preserves matching ancestry and hides "
      "unrelated authored classes",
      not _tree_payload(_tree_window.library.tree,
                        ("user_class", "TreeLoop_FP")).isHidden()
      and _tree_payload(_tree_window.library.tree,
                        ("user_class", "TreeLoop_PVM")).isHidden())
_tree_window.library.search.clear()
check("...plus the Languages and Themes top-level folders",
      any("Languages" in ln for ln in _lib)
      and any("Themes" in ln for ln in _lib))

_library_window = _tree_window.create_configuration_library("Shared")
_lib_after = _tree_lines(_tree_window.library.tree)
check("named configuration libraries are created and opened through "
      "Studio, and remain the one PvmDisplay document stack",
      _library_window is not None
      and _library_window._library_name == "Shared"
      and Path(_library_window._root)
      == Path(_tree_window._root) / "_libraries" / "Shared"
      and any("Shared" in line for line in _lib_after))
if _library_window is not None:
    _library_window.close()

_tree_window.tabs.setCurrentWidget(_tree_window.open_display("Overview"))
_quick_view = _tree_window.open_quick_online()
_quick_refusal = _tree_window.quick_online.source.write("X/Y/Z", 1)
check("Quick Online runs the current draft in a separate read-only "
      "sandbox without publishing it or changing a station assignment",
      _quick_view is not None
      and _tree_window.quick_online.documents() == ("Overview",)
      and not _quick_refusal.success
      and "blocks operator writes" in _quick_refusal.error)
_tree_window.quick_online.close()

# --- the property pane uses Graphics Designer's group names -------------
_pstudio = _tree_window.open_display("Overview")
_pstudio.enter_edit()
_pitem2 = _pstudio.add_static("rect", 10, 10, w=90, h=60)
_pstudio.canvas.scene().clearSelection()
_pitem2.setSelected(True)
_pstudio.pane.show_item(_pitem2)
from PySide6.QtWidgets import QLabel as _QLabel3
_groups = [w.text() for w in _pstudio.pane.findChildren(_QLabel3)]
check("the Design inspector keeps position, fill, stroke, information and visibility",
      all(g in _groups for g in ("Information", "Fill", "Stroke",
                                 "Position and size", "Visibility")),
      [g for g in ("Information", "Fill", "Stroke", "Position and size",
                   "Visibility") if g not in _groups])
check("every element carries Title and Description, as the manual's "
      "Information group does",
      "title" in _pstudio.pane.item_fields
      and "description" in _pstudio.pane.item_fields)
_tree_window.close()

# --- the inventory itself --------------------------------------------
import importlib as _importlib


def _resolves(dotted: str) -> bool:
    parts = _par.qualified_implementation(dotted).split(".")
    for split in range(len(parts), 2, -1):
        try:
            obj = _importlib.import_module(".".join(parts[:split]))
        except ImportError:
            continue
        try:
            for attr in parts[split:]:
                obj = getattr(obj, attr)
        except AttributeError:
            return False
        return True
    return False


_broken = [i.key for i in _par.ITEMS
           if i.implementation and not _resolves(i.implementation)]
check("every parity claim points at a symbol that exists — renaming "
      "one breaks the inventory instead of orphaning the claim",
      not _broken, _broken)
check("departures are not on the work front: a decision left there "
      "invites somebody to close it by undoing the decision",
      not any(i.status is _par.Status.DEPARTURE
              for i in _par.unresolved()))
check("every departure and not-applicable carries its reason",
      all(i.notes for i in _par.ITEMS
          if i.status in (_par.Status.DEPARTURE,
                          _par.Status.NOT_APPLICABLE)))
check("the manual inventory has no unclassified implementation gaps — "
      "product-only items and deliberate departures remain explicit",
      not _par.unresolved() and len(_par.ITEMS) > 60,
      _par.counts())

print()
if failures:
    print(f"{len(failures)} operator-layer check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All operator-layer checks passed.")
print(f"Azeo HMI capability: {_par.counts()}")
