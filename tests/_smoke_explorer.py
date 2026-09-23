"""Azeo Explorer smoke — the engineering-station shell.

The acceptance rule for the whole Explorer effort: a student following
the course workshops must find every step where the book says it is.
Phase 1 covers the anatomy — tree, contents pane, app launchers, verbs
that map to existing actions, and the honest download-status markers.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
# The other six smoke tests do this; without it a check label carrying
# a status glyph (▲) kills the run on a cp1252 console — the suite
# failing for a reason that has nothing to do with the code.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                          # noqa: BLE001
    pass

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
from azeo_control_trainer.core.strategy.engine.pk_controller import (  # noqa: E402
    PKController,
)
from azeo_control_trainer.core.strategy.engine.controller_discovery import (  # noqa: E402
    ControllerAdvertisement,
    ControllerDiscoveryResponder,
    discover_controllers,
)
from azeo_control_trainer.connectivity.fieldio.eioc import EthernetIoCard  # noqa: E402
from azeo_control_trainer.core.strategy.tagdb import TagDatabase  # noqa: E402
from azeo_control_trainer.azeo_explorer import ExplorerWindow  # noqa: E402
from azeo_control_trainer.azeo_control_designer.designer_window import (  # noqa: E402
    StrategyDesignerWindow,
)

# Discovery is a real network exchange, not a pre-populated UI list. An
# ephemeral port isolates the check from any trainer already running locally.
_advertisement = ControllerAdvertisement(
    hardware_id="AZEO-SMOKE-PK-0001",
    name="SMOKE-CTRL",
    model="PK100",
    serial="SMOKE-PK-0001",
    opcua_endpoint="opc.tcp://127.0.0.1:4840/azeo/pk",
)
_responder = ControllerDiscoveryResponder(
    _advertisement, host="127.0.0.1", port=0)
_responder_started = _responder.start()
_discovered = (discover_controllers(
    timeout=0.35, port=_responder.port, targets=("127.0.0.1",))
    if _responder_started else [])
_responder.stop()
check("controller discovery exchanges identity over the Control Network",
      len(_discovered) == 1
      and _discovered[0].hardware_id == _advertisement.hardware_id
      and _discovered[0].name == _advertisement.name,
      _discovered)

store = SharedDataStore()
store.tagdb = TagDatabase.from_area(AREA)
store.controller = PKController.from_config(None)
window = StrategyDesignerWindow(store=store, plugin=AreaContext(AREA))
window.designer.auto_load_project()

explorer = ExplorerWindow(store=store, area=AREA, designer=window)

from PySide6.QtGui import QAction  # noqa: E402
pilot_action = next(action for action in explorer.findChildren(QAction) if action.text() == "PA Designer")
pilot_action.trigger()
pilot = explorer._pa_designer_window
check("Explorer opens the independent PA Designer authoring app",
      pilot is not None and pilot.isVisible() and pilot.isWindow())
check("Repeated PA Designer activation retains the same project editor",
      explorer.open_pa_designer() is pilot)
if pilot is not None:
    check("PA Designer resolves the project's configured parameter catalog",
          bool(pilot._catalog_paths))
    pilot.close()
    app.processEvents()

# ------------------------------------------------------------- anatomy
root = explorer.tree.topLevelItem(0)
top = [root.child(i).text(0) for i in range(root.childCount())]
check("the system tree carries the Explorer's containers",
      any("Library" in t for t in top)
      and any("Control Strategies" in t for t in top)
      and any("Physical Network" in t for t in top)
      and any("Alarms and Events" in t for t in top)
      and any("Historian" in t for t in top), top)


def find(item, needle):
    for i in range(item.childCount()):
        child = item.child(i)
        if needle in child.text(0):
            return child
        found = find(child, needle)
        if found is not None:
            return found
    return None


def payload_items(item, kind):
    """All descendants carrying one Explorer payload kind."""
    found = []
    for i in range(item.childCount()):
        child = item.child(i)
        payload = child.data(0, 0x0100)
        if payload and payload[0] == kind:
            found.append(child)
        found.extend(payload_items(child, kind))
    return found


strategies = find(root, "Control Strategies")
live_area = find(strategies, "(this station)")
live_modules = payload_items(live_area, "module") if live_area else []
check("the live area is marked as this station's, with its modules",
      live_area is not None and len(live_modules) >= 5,
      len(live_modules))
check("every downloadable module starts with the not-downloaded "
      "marker (▲) — the blue triangle, computed not asserted; "
      "equipment descriptors wear none",
      all("▲" in module.text(0) or "●" not in module.text(0)
          for module in live_modules)
      and any("▲" in module.text(0) for module in live_modules))

controller_item = find(root, "PK-CTLR-1")
check("the controller node sits on the Control Network with its "
      "model", controller_item is not None
      and "PK100" in controller_item.text(0),
      controller_item.text(0) if controller_item else None)
assigned_io = find(controller_item, "Assigned I/O")
check("Assigned I/O lists the DSTs from the tag database",
      assigned_io is not None
      and assigned_io.childCount() == len(store.tagdb.field_tags()),
      (assigned_io.childCount() if assigned_io else None,
       len(store.tagdb.field_tags())))

simulator_area = (Path(__file__).resolve().parent.parent
                  / "projects" / "AzeoPlantVirtualController")
simulator_project = json.loads(
    (simulator_area / "_project.json").read_text(encoding="utf-8")
)
network_view = ExplorerWindow(
    store=store, area=simulator_area, designer=window)
network_root = network_view.tree.topLevelItem(0)
simulator_live_area = find(
    find(network_root, "Control Strategies"), "(this station)")
simulator_units = payload_items(simulator_live_area, "unit")
simulator_modules = payload_items(simulator_live_area, "module")
expected_units = simulator_project["areas"][0]["units"]
check("logical control strategies are grouped Area -> Unit -> type -> Module",
      len(simulator_units) == len(expected_units)
      and len(simulator_modules) == len(simulator_project["assignments"])
      and find(simulator_live_area, "Unassigned Modules") is None
      and not any(
          (simulator_live_area.child(i).data(0, 0x0100) or (None,))[0]
          == "module"
          for i in range(simulator_live_area.childCount())),
      (len(simulator_units), len(simulator_modules)))
u500 = next((item for item in simulator_units
             if item.text(0) == "U500"), None)
check("unit membership comes from project metadata, including sequences",
      u500 is not None and len(payload_items(u500, "module")) == 27
      and any("BMS-3001" in item.text(0)
              for item in payload_items(
                  next(item for item in simulator_units
                       if item.text(0) == "U300"), "module")),
      len(payload_items(u500, "module")) if u500 else None)
primary_controller = find(network_root, "APVC-CTRL-1")
assigned_modules = (find(primary_controller, "Assigned Modules")
                    if primary_controller else None)
check("the shipped virtual controller owns every assigned module",
      primary_controller is not None
      and "PK750" in primary_controller.text(0)
      and assigned_modules is not None
      and assigned_modules.childCount() == len(simulator_project["assignments"]),
      assigned_modules.childCount() if assigned_modules else None)
network_view.close()

# A project's controller record is the configuration placeholder; the
# hardware identity has its own lifecycle. Decommissioning must never delete
# the placeholder or module assignments.
with tempfile.TemporaryDirectory(prefix="azeo-controller-lifecycle-") as tmp:
    lifecycle_area = Path(tmp)
    lifecycle_project = {
        "areas": [{"name": "LIFECYCLE", "strategies": [],
                   "sfc_modules": [], "equipment_modules": []}],
        "nodes": [{
            "node_id": "ctrl_found",
            "name": "CTRL-FOUND",
            "type": "controller",
            "model": "PK100",
            "description": "Configured expansion controller",
        }],
        "assignments": {},
    }
    (lifecycle_area / "_project.json").write_text(
        json.dumps(lifecycle_project, indent=2) + "\n", encoding="utf-8")
    lifecycle_view = ExplorerWindow(
        store=store, area=lifecycle_area, designer=window)
    found_advertisement = ControllerAdvertisement(
        hardware_id="AZEO-FOUND-PK-0001",
        name="CTRL-FOUND",
        model="PK100",
        serial="FOUND-PK-0001",
        address="192.0.2.20",
        opcua_endpoint="opc.tcp://192.0.2.20:4840/azeo/pk",
    )
    check("a discovered controller can bind to a configured placeholder",
          lifecycle_view.add_discovered_controller(
              found_advertisement, commission=False))
    saved = json.loads((lifecycle_area / "_project.json").read_text(
        encoding="utf-8"))
    found_record = saved["nodes"][0]
    check("Add as Decommissioned persists the immutable hardware identity",
          found_record["hardware_id"] == "AZEO-FOUND-PK-0001"
          and found_record["serial"] == "FOUND-PK-0001"
          and found_record["commissioning"]["state"] == "decommissioned")
    lifecycle_root = lifecycle_view.tree.topLevelItem(0)
    placeholder = find(lifecycle_root, "CTRL-FOUND")
    decommissioned = find(lifecycle_root, "Decommissioned Nodes")
    physical_found = find(decommissioned, "FOUND-PK-0001")
    check("decommissioned hardware and its Control Network placeholder coexist",
          placeholder is not None and "placeholder" in placeholder.text(0)
          and physical_found is not None)
    check("the physical controller can be commissioned into its project node",
          lifecycle_view.commission_project_controller("ctrl_found"))
    lifecycle_root = lifecycle_view.tree.topLevelItem(0)
    commissioned = find(lifecycle_root, "CTRL-FOUND")
    check("commissioning removes the physical entry and marks the node active",
          commissioned is not None
          and "commissioned" in commissioned.text(0)
          and find(find(lifecycle_root, "Decommissioned Nodes"),
                   "FOUND-PK-0001") is None)
    check("any commissioned project controller can be decommissioned again",
          lifecycle_view.decommission_project_controller("ctrl_found"))
    saved = json.loads((lifecycle_area / "_project.json").read_text(
        encoding="utf-8"))
    check("decommissioning retains the configuration and hardware record",
          len(saved["nodes"]) == 1
          and saved["nodes"][0]["node_id"] == "ctrl_found"
          and saved["nodes"][0]["commissioning"]["state"]
          == "decommissioned")
    renamed_hardware = ControllerAdvertisement(
        hardware_id="AZEO-FOUND-PK-0001", name="OLD-FIELD-NAME")
    lifecycle_view.add_discovered_controller(renamed_hardware, True)
    saved = json.loads((lifecycle_area / "_project.json").read_text(
        encoding="utf-8"))
    check("rediscovery cannot rename an associated project placeholder",
          saved["nodes"][0]["name"] == "CTRL-FOUND")
    conflicting = ControllerAdvertisement(
        hardware_id="AZEO-DIFFERENT-PK-0001", name="CTRL-FOUND")
    check("a different hardware identity cannot replace a bound controller",
          not lifecycle_view.add_discovered_controller(conflicting, True))
    lifecycle_view.close()

with tempfile.TemporaryDirectory(prefix="azeo-primary-lifecycle-") as tmp:
    primary_area = Path(tmp)
    primary_project = {
        "areas": [{"name": "PRIMARY", "strategies": [],
                   "sfc_modules": [], "equipment_modules": []}],
        "nodes": [{
            "node_id": "primary_ctrl",
            "name": "PK-CTLR-1",
            "type": "controller",
            "model": "PK100",
            "hardware_id": "AZEO-PRIMARY-PK-0001",
            "serial": "PRIMARY-PK-0001",
        }],
        "assignments": {},
    }
    (primary_area / "_project.json").write_text(
        json.dumps(primary_project, indent=2) + "\n", encoding="utf-8")
    store.controller_discovery = ControllerDiscoveryResponder(
        ControllerAdvertisement(
            hardware_id="AZEO-PRIMARY-PK-0001", name="PK-CTLR-1"))
    primary_view = ExplorerWindow(
        store=store, area=primary_area, designer=window)
    check("the in-process primary controller persists decommission state",
          primary_view.decommission())
    primary_view.close()
    saved = json.loads((primary_area / "_project.json").read_text(
        encoding="utf-8"))
    check("primary hardware remains associated after decommission",
          saved["nodes"][0]["commissioning"]["state"]
          == "decommissioned"
          and saved["nodes"][0]["hardware_id"]
          == "AZEO-PRIMARY-PK-0001"
          and store.controller_discovery.advertisement.state
          == "decommissioned")
    restored_primary_view = ExplorerWindow(
        store=store, area=primary_area, designer=window)
    restored_root = restored_primary_view.tree.topLevelItem(0)
    check("a restarted Explorer restores primary placeholder and physical row",
          not restored_primary_view.commissioned
          and find(restored_root, "(placeholder)") is not None
          and find(find(restored_root, "Decommissioned Nodes"),
                   "PRIMARY-PK-0001") is not None)
    check("recommissioning the primary controller persists too",
          restored_primary_view.commission(
              name="PK-CTLR-1", auto_sense=False))
    saved = json.loads((primary_area / "_project.json").read_text(
        encoding="utf-8"))
    check("the restored primary state is commissioned",
          saved["nodes"][0]["commissioning"]["state"] == "commissioned"
          and store.controller_discovery.advertisement.state
          == "commissioned")
    restored_primary_view.close()
    del store.controller_discovery

# An external OPC UA client is its own physical node. Its signals must not be
# flattened under the PK controller or counted as native Assigned I/O.
store.eioc = EthernetIoCard.from_config({
    "name": "EIOC-TEST",
    "field_io": {"type": "opcua", "endpoint": "opc.tcp://plant:4840/",
                 "signals": {
                     "PT-1": {"kind": "AI", "direction": "read",
                              "plant_unit": "U100"},
                     "XV-1": {"kind": "DO", "direction": "write",
                              "plant_unit": "U100"}}},
})
explorer.refresh()
eioc_root = explorer.tree.topLevelItem(0)
eioc_item = find(eioc_root, "EIOC-TEST")
native_io = find(find(eioc_root, "PK-CTLR-1"), "Native I/O")
check("Explorer presents external OPC UA as a separate EIOC node",
      eioc_item is not None and "EIOC" in eioc_item.text(0)
      and find(eioc_item, "OPC UA Signals") is not None)
check("the PK shows only native I/O when an EIOC owns external signals",
      native_io is not None
      and find(native_io, "PT-1") is None
      and find(native_io, "XV-1") is None)
store.eioc = None
explorer.refresh()
root = explorer.tree.topLevelItem(0)
strategies = find(root, "Control Strategies")
live_area = find(strategies, "(this station)")
controller_item = find(root, "PK-CTLR-1")
assigned_io = find(controller_item, "Assigned I/O")
_decom = find(root, "Decommissioned Nodes")
check("Decommissioned Nodes shows only its empty-state hint",
      _decom is not None and _decom.childCount() == 1
      and "appear here" in _decom.child(0).text(0)
      and _decom.child(0).data(0, 32) is None)

# ------------------------------------------------- contents pane
explorer._show_contents(live_area)
check("the contents pane lists the area's units as a details view",
      explorer.contents.topLevelItemCount() == live_area.childCount()
      and explorer.contents.topLevelItem(0).text(1) == "Unit Module",
      explorer.contents.topLevelItem(0).text(1))
first_unit = payload_items(live_area, "unit")[0]
first_group = first_unit.child(0)
explorer._show_contents(first_group)
check("a unit's typed folder lists modules and their download status",
      explorer.contents.topLevelItemCount() == first_group.childCount()
      and explorer.contents.topLevelItem(0).text(1)
      in ("Control Module", "Sequence Module", "Equipment Module")
      and explorer.contents.topLevelItem(0).text(2) == "▲ not downloaded")

# ------------------------------------------------------------- verbs
first_module = payload_items(live_area, "module")[0]
explorer._activate(first_module)
from PySide6.QtCore import Qt as _Qt                        # noqa: E402

kind, data = first_module.data(0, _Qt.UserRole)
tab = window.designer
check("double-click opens the module in Control Designer",
      tab._find_tab_by_path(str(data[1]))
      == tab._canvas_tabs.currentIndex(),
      (tab._find_tab_by_path(str(data[1])),
       tab._canvas_tabs.currentIndex()))

menu = explorer._menu_for(("module", data))
labels = [a.text() for a in menu.actions() if a.text()]
check("a module's context menu carries the course's verbs",
      "Open" in labels and "Download" in labels, labels)
cmenu = explorer._menu_for(("controller", None))
clabels = [a.text() for a in cmenu.actions() if a.text()]
check("the controller's menu leads with Total Download",
      any("Total Download" in t for t in clabels)
      and any("Diagnose" in t for t in clabels), clabels)
network_menu = explorer._menu_for(("network", None))
check("Control Network exposes controller discovery as a working verb",
      any("Discover Controllers" in action.text()
          for action in network_menu.actions()))

# ---------------------------------------------------- total download
explorer._total_download()
online = explorer._online_names()
check("Total Download puts the area on scan",
      len(online) >= 5, online)
live_area = find(explorer.tree.topLevelItem(0), "(this station)")
live_modules = payload_items(live_area, "module")
check("and the download-status markers clear to on-scan (●) — no "
      "blue triangle survives a download",
      not any("▲" in module.text(0) for module in live_modules)
      and sum("●" in module.text(0) for module in live_modules) >= 5,
      [module.text(0) for module in live_modules
       if "●" not in module.text(0)])
first_unit = payload_items(live_area, "unit")[0]
explorer._show_contents(first_unit.child(0))
check("the contents pane agrees",
      explorer.contents.topLevelItem(0).text(2) == "● on scan")
assigned = find(explorer.tree.topLevelItem(0), "Assigned Modules")
check("Assigned Modules under the controller now lists them",
      assigned is not None and assigned.childCount() == len(online))

# -------------------------------------------- icons (one vocabulary)
lib_blocks = find(explorer.tree.topLevelItem(0), "Function Blocks")
first_cat = lib_blocks.child(0)
check("function blocks carry the designer's own vector icons",
      not first_cat.child(0).icon(0).isNull())
lib_pvms = find(explorer.tree.topLevelItem(0), "PVM Classes")
check("PVM classes carry icons too — symbol silhouettes for "
      "equipment variants",
      not lib_pvms.child(0).icon(0).isNull()
      and lib_pvms.childCount() >= 25, lib_pvms.childCount())

# ------------------------- the commission lifecycle (workshop pp.83–85)
store.controller.keylock = True
check("a locked carrier key refuses decommission (course + item: "
      "operation continues untouched)",
      not explorer.decommission() and len(explorer._online_names()) >= 5)
store.controller.keylock = False
check("unlocked, decommission takes the modules off scan",
      explorer.decommission() and explorer._online_names() == set())
root2 = explorer.tree.topLevelItem(0)
check("the node reappears under Decommissioned Nodes with the "
      "default name, and a placeholder keeps the configuration",
      find(root2, "CTLR-005034") is not None
      and find(root2, "(placeholder)") is not None
      and "PK-CTLR-1" in find(root2, "(placeholder)").text(0))
explorer.identify()
check("Identify starts the flashing-lights timer",
      explorer._identify_ticks > 0
      and explorer._identify_timer.isActive())
explorer._identify_timer.stop()
explorer._identify_ticks = 0
check("the naming rules refuse what the course refuses",
      not explorer.commission(name="THIS-NAME-IS-FAR-TOO-LONG")
      and not explorer.commission(name="12345")
      and not explorer.commission(name="BAD NAME"))
check("a valid name commissions; declining auto-sense leaves the "
      "I/O unsensed",
      explorer.commission(name="PK-CTLR-1", auto_sense=False)
      and explorer.commissioned and not explorer.io_sensed)
root2 = explorer.tree.topLevelItem(0)
check("the unsensed node says so, and Decommissioned Nodes is back "
      "to its hint",
      find(root2, "(not sensed)") is not None
      and all("appear here" in find(root2, "Decommissioned Nodes")
              .child(i).text(0)
              for i in range(find(root2, "Decommissioned Nodes")
                             .childCount())))
check("Auto-sense derives the channels from the tag database",
      explorer.auto_sense() == len(store.tagdb.field_tags())
      and find(explorer.tree.topLevelItem(0),
               "Assigned I/O").childCount()
      == len(store.tagdb.field_tags()))
cmenu2 = explorer._menu_for(("controller", None))
check("the controller menu carries the lifecycle verbs",
      any("Decommission" in a.text() for a in cmenu2.actions())
      and any("Identify" in a.text() for a in cmenu2.actions())
      and any("Auto-sense" in a.text() for a in cmenu2.actions()))
explorer._total_download()
check("the module 2 arc closes: commission, sense, download, on scan",
      len(explorer._online_names()) >= 5)

# ------------------------- I/O channels (workshop pp.86–91, phase 3)
from azeo_control_trainer.connectivity.fieldio.modbus_driver import (  # noqa: E402
    ModbusFieldDriver,
)

window.plant_driver = ModbusFieldDriver(store)
master = window.plant_driver
cards = explorer._io_cards()
check("auto-sense groups the Modbus map into cards, course order — "
      "C01 analog in, eight channels to a card",
      cards and cards[0].label == "C01"
      and cards[0].channel_type == "Analog Input Channel"
      and len(cards[0].channels) == 8,
      [(c.label, c.channel_type, len(c.channels)) for c in cards])
explorer.refresh()
c01 = find(explorer.tree.topLevelItem(0), "C01 —")
check("Assigned I/O shows the cards holding their channels",
      c01 is not None and c01.childCount() == 8)
_ch = cards[0].channels[0]
check("disabling a channel is real driver state — the tag stops "
      "being scanned",
      explorer.set_channel(_ch.tag, enabled=False)
      and _ch.tag in master.disabled_channels)
c01 = find(explorer.tree.topLevelItem(0), "C01 —")
check("and the row wears the disabled mark",
      any("⛔" in c01.child(i).text(0)
          for i in range(c01.childCount())))
check("re-tagging a channel publishes under the new Device Tag",
      explorer.set_channel(_ch.tag, enabled=True, device_tag="LT-1")
      and master.device_tags.get(_ch.tag) == "LT-1"
      and any(ch.device_tag == "LT-1"
              for ch in explorer._io_cards()[0].channels))
c01 = find(explorer.tree.topLevelItem(0), "C01 —")
check("an unreferenced Device Tag is marked, not hidden — the "
      "mismatch lesson",
      any("LT-1" in c01.child(i).text(0)
          and "⚠" in c01.child(i).text(0)
          for i in range(c01.childCount())))
explorer._show_contents(c01)
check("the contents pane carries type, enable state and address "
      "per channel",
      explorer.contents.topLevelItem(0).text(1)
      == "Analog Input Channel"
      and "addr 3" in explorer.contents.topLevelItem(0).text(3),
      (explorer.contents.topLevelItem(0).text(1),
       explorer.contents.topLevelItem(0).text(3)))
_chmenu = explorer._menu_for(("channel", explorer._io_cards()[0]
                              .channels[0]))
check("the channel menu carries Properties and the enable toggle",
      any("Properties" in a.text() for a in _chmenu.actions())
      and any(a.text() in ("Enable", "Disable")
              for a in _chmenu.actions()))
explorer.set_channel(_ch.tag, device_tag="")
window.plant_driver = None
explorer.refresh()

# --------------- context-menu coverage: every node answers (polish)
from PySide6.QtCore import QPoint as _QPoint  # noqa: E402

for needle in ("Library", "Function Blocks", "Procedure Blocks", "PVM Classes",
               "Alarms and Events", "Historian",
               "Decommissioned Nodes", "Control Strategies"):
    target = find(explorer.tree.topLevelItem(0), needle)
    explorer._context_menu = None
    rect = explorer.tree.visualItemRect(target)
    explorer._tree_menu(rect.center())
    check(f"right-click answers on {needle}",
          explorer._context_menu is not None
          and len(explorer._context_menu.actions()) > 0)
explorer._context_menu = None
explorer._tree_menu(_QPoint(5, 5000))
check("empty tree space gets the generic menu (Refresh / Expand / "
      "Collapse)",
      explorer._context_menu is not None
      and any("Expand all" in a.text()
              for a in explorer._context_menu.actions()))
explorer._context_menu = None
explorer._contents_menu(_QPoint(5, 5000))
check("empty contents space answers too",
      explorer._context_menu is not None)
_fb_menu = explorer._menu_for(("lib_block", "PID"))
check("a function block's menu can insert it into the open module",
      any("Insert into open module" in a.text()
          for a in _fb_menu.actions()))
_ht = payload_items(explorer.tree.topLevelItem(0), "hist_tag")[0]
_ht_menu = explorer._menu_for(_ht.data(0, _Qt.UserRole))
check("a historized parameter's menu opens its trend",
      any("Open trend" in a.text() for a in _ht_menu.actions()))

import shutil as _shutil  # noqa: E402

_new_area = explorer.new_area("PLANT_AREA_T")
check("New Area creates the folder with a minimal project file "
      "and it appears in the tree",
      _new_area is not None
      and (_new_area / "_project.json").exists()
      and find(explorer.tree.topLevelItem(0), "PLANT_AREA_T")
      is not None)
check("a second area of the same name is refused",
      explorer.new_area("PLANT_AREA_T") is None)
_shutil.rmtree(_new_area)
explorer.refresh()

# ---------------- phase 4: the staged Total Download (pp.100–102)
from azeo_control_trainer.azeo_explorer.download import (  # noqa: E402
    DONE, FAILED, SKIPPED,
)

_dl = explorer._total_download(verify=True)
check("the staged download runs every stage to completion",
      _dl["ok"] and all(s == DONE for s in _dl["states"]),
      _dl["states"])
check("and leaves an audit trail in the download log",
      (AREA / "_download.log").exists())
_dl2 = explorer._total_download(verify=False)
check("verify unchecked stipples the Verifying stage — the figure's "
      "own detail",
      _dl2["ok"] and _dl2["states"][4] == SKIPPED
      and _dl2["states"][5] == DONE, _dl2["states"])

store.controller.keylock = True
_dl3 = explorer._total_download(verify=True)
check("the keylock refuses at pre-download and the rest stipples",
      not _dl3["ok"] and _dl3["states"][2] == FAILED
      and all(s == SKIPPED for s in _dl3["states"][3:]),
      _dl3["states"])
store.controller.keylock = False

# The blue triangle is now a real config-vs-runtime diff.
_graph = next(g for g in window.designer.open_graphs() if g.blocks)
_blk = next(iter(_graph.blocks.values()))
_blk.config.params["_SMOKE_MARK"] = 1
explorer.refresh()
_row = find(find(explorer.tree.topLevelItem(0), "(this station)"),
            _graph.name)
check("a parameter edit after download raises the triangle on a "
      "RUNNING module",
      _row is not None and "●▲" in _row.text(0), _row.text(0))
explorer._total_download(verify=False)
_row = find(find(explorer.tree.topLevelItem(0), "(this station)"),
            _graph.name)
check("and the next download clears it",
      "▲" not in _row.text(0) and "●" in _row.text(0),
      _row.text(0))
del _blk.config.params["_SMOKE_MARK"]
explorer._total_download(verify=False)
(AREA / "_download.log").unlink(missing_ok=True)

# ------------------- the menubar dropdowns are studio menus with icons
from azeo_control_trainer.core.presentation.menu_style import (  # noqa: E402
    _attach_icons,
)

_file_menu = explorer.menuBar().actions()[0].menu()
_attach_icons(_file_menu)
check("the menubar dropdowns carry the studio styling and vector "
      "icons",
      "QMenu" in _file_menu.styleSheet()
      and any(not a.icon().isNull() for a in _file_menu.actions()
              if a.text()),
      [a.text() for a in _file_menu.actions()])

# ---------------------------------------------------------- help (F1)
_help = explorer.show_help()
check("F1 opens the detailed Explorer help — status marks, the "
      "commission cycle, discovery, and the download stages",
      "blue triangle" in _help.findChild(
          __import__("PySide6.QtWidgets",
                     fromlist=["QTextBrowser"]).QTextBrowser)
      .toPlainText()
      and "Discover Controllers" in _help.findChild(
          __import__("PySide6.QtWidgets",
                     fromlist=["QTextBrowser"]).QTextBrowser)
      .toPlainText()
      and "Total Download" in _help.findChild(
          __import__("PySide6.QtWidgets",
                     fromlist=["QTextBrowser"]).QTextBrowser)
      .toPlainText())
_help.close()
_about = explorer._about()
_about_text = _about.findChild(
    __import__("PySide6.QtWidgets", fromlist=["QTextBrowser"]).QTextBrowser
).toPlainText()
check("Explorer About exposes release, build and active project support identity",
      "Version" in _about_text and "Build" in _about_text
      and "0.4.0" in _about_text and explorer.area.name in _about_text)
_about.close()
from azeo_control_trainer.azeo_control_designer.dialogs.help_dialog \
    import _TOPICS as _HELP_TOPICS  # noqa: E402

check("Explorer owns its help instead of hiding a second copy in Control Designer",
      all(t[0] != "Azeo Explorer" for t in _HELP_TOPICS))

# ------------------ phase 2.5: remote nodes over OPC UA
from azeo_control_trainer.connectivity.opcua.pk_server import (  # noqa: E402
    PKOpcUaServer,
)

_ua = PKOpcUaServer(store, host="127.0.0.1", port=43417)
if _ua.start():
    _rn = explorer.add_remote_node(
        "PK-CTLR-2", "opc.tcp://127.0.0.1:43417/azeo/pk")
    check("Add Remote Node browses the peer's module namespace live",
          _rn is not None and _rn.connected
          and set(_rn.modules) == set(store.tagdb.modules()),
          (_rn.modules if _rn else None,
           sorted(store.tagdb.modules())))
    _rrow = find(explorer.tree.topLevelItem(0), "PK-CTLR-2")
    check("the remote node sits on the Control Network beside the "
          "local PK, modules listed",
          _rrow is not None and "(remote)" in _rrow.text(0)
          and find(_rrow, "Assigned Modules") is not None
          and find(_rrow, "Assigned Modules").childCount()
          == len(_rn.modules))
    _rmenu = explorer._menu_for(("remote_node", "PK-CTLR-2"))
    check("its menu carries Rebrowse / Properties / Remove and the "
          "monitor-only contract",
          any("Rebrowse" in a.text() for a in _rmenu.actions())
          and any("Remove" in a.text() for a in _rmenu.actions()))
    check("a duplicate name is refused",
          explorer.add_remote_node(
              "PK-CTLR-2", "opc.tcp://x/azeo/pk") is None)
    check("Remove takes it off the network",
          explorer.remove_remote_node("PK-CTLR-2")
          and find(explorer.tree.topLevelItem(0), "PK-CTLR-2")
          is None)
    _ua.stop()
else:
    print("[ok]   (OPC UA loopback unavailable — remote-node checks "
          "skipped)")
_bad = explorer.add_remote_node(
    "PK-CTLR-9", "opc.tcp://127.0.0.1:9/azeo/pk")
check("an unreachable node stays in the tree with its error spelled "
      "out",
      _bad is not None and not _bad.connected and _bad.error
      and find(explorer.tree.topLevelItem(0), "unreachable")
      is not None)
explorer.remove_remote_node("PK-CTLR-9")

# ---------------- phase 5: A&E assignment gate + unit / historian
_ae_row = find(explorer.tree.topLevelItem(0), "Alarms and Events")
check("the area boots assigned to this workstation",
      "(assigned" in _ae_row.text(0))
explorer.set_area_assigned(False)
check("removing the assignment gates the Operator Station (p.65)",
      not explorer.open_operator_station()
      and "NOT assigned" in find(explorer.tree.topLevelItem(0),
                                 "Alarms and Events").text(0))
explorer.set_area_assigned(True)
check("re-assigning restores it",
      "(assigned" in find(explorer.tree.topLevelItem(0),
                          "Alarms and Events").text(0))

_units = explorer._project_units()
if _units:
    _unit_name = _units[0]["name"]
    _pj = AREA / "_project.json"
    _orig = _pj.read_bytes()
    check("unit Properties writes description and consolidation "
          "back to _project.json, CRITICAL never consolidating",
          explorer.save_unit(_unit_name,
                             description="smoke-edited",
                             consolidate={"CRITICAL": True,
                                          "WARNING": False})
          and '"smoke-edited"' in _pj.read_text(encoding="utf-8")
          and any(u["name"] == _unit_name
                  and u["consolidate"]["CRITICAL"] is False
                  and u["consolidate"]["WARNING"] is False
                  for u in explorer._project_units()))
    _pj.write_bytes(_orig)
    explorer.refresh()
else:
    print("[ok]   (area declares no units — unit checks skipped)")
check("an unknown unit refuses", not explorer.save_unit("NOPE"))

import tempfile as _tf  # noqa: E402

_csv = explorer.export_historian_csv(
    Path(_tf.mkdtemp()) / "dataset.csv")
check("the historian dataset exports as CSV, one row per derived "
      "parameter",
      _csv is not None
      and len(_csv.read_text(encoding="utf-8").splitlines())
      == len(store.tagdb.field_tags()) + 1)
_ae_menu = explorer._menu_for(("alarms", None))
check("the A&E menu leads with the assignment toggle",
      any("workstation" in a.text() for a in _ae_menu.actions()))
_unit_menu = explorer._menu_for(("unit", _units[0] if _units
                                 else {"name": "U"}))
check("the unit menu leads with Properties",
      any("Properties" in a.text() for a in _unit_menu.actions()))

# ------------------------ the commercial-polish pass
check("the toolbar wears vector icons, not emoji",
      all(not a.icon().isNull() for a in
          explorer.findChild(__import__("PySide6.QtWidgets",
                                        fromlist=["QToolBar"])
                             .QToolBar).actions()
          if a.text()))
check("the status bar is fields — node identity and the DST gauge",
      "PK100" in explorer.node_field.text()
      and "/" in explorer.dst_field.text(),
      (explorer.node_field.text(), explorer.dst_field.text()))

explorer.search.setText("Decommissioned")
_visible = [find(explorer.tree.topLevelItem(0), "Decommissioned")]
check("search filters the system tree, keeping ancestors visible",
      not _visible[0].isHidden()
      and find(explorer.tree.topLevelItem(0),
               "Historian").isHidden())
explorer.search.setText("")

_target = find(explorer.tree.topLevelItem(0), "Physical Network")
_target.setExpanded(True)
explorer.tree.setCurrentItem(_target)
explorer.refresh()
check("refresh keeps expansion and selection — F5 does not lose "
      "your place",
      find(explorer.tree.topLevelItem(0),
           "Physical Network").isExpanded()
      and explorer.tree.currentItem() is not None
      and "Physical Network"
      in explorer.tree.currentItem().text(0))

explorer._show_contents(explorer.tree.topLevelItem(0))
_row0 = explorer.contents.topLevelItem(0)
_row0_key = _row0.data(0, _Qt.UserRole + 1)
explorer._activate(_row0)
check("double-clicking a container in the right pane drills into it",
      explorer.tree.currentItem() is not None
      and explorer._text_key(explorer.tree.currentItem())
      == _row0_key)
_before_up = explorer.tree.currentItem()
explorer.navigate_up()
check("and Backspace goes back up",
      explorer.tree.currentItem() is _before_up.parent())
check("contents rows carry icons",
      explorer._show_contents(
          find(explorer.tree.topLevelItem(0), "(this station)"))
      or not explorer.contents.topLevelItem(0).icon(0).isNull())
explorer._show_contents(
    find(explorer.tree.topLevelItem(0), "Decommissioned Nodes"))
check("an empty container states its empty-state, not a blank pane",
      "(" in explorer.contents.topLevelItem(0).text(0))

# ---------------- the Explorer as the studio's tag source
_live2 = find(explorer.tree.topLevelItem(0), "(this station)")
_mod_item = next(item for item in payload_items(_live2, "module")
                 if "▲" in item.text(0) or "●" in item.text(0))
explorer.tree.setCurrentItem(_mod_item)
explorer._show_contents(_mod_item)
check("a live module's contents are its blocks — the draggable tag "
      "source (the studio keeps no plant tree)",
      explorer.contents.topLevelItemCount() >= 1
      and explorer.contents.topLevelItem(0)
      .data(0, _Qt.UserRole)[0] == "mod_block"
      and "/" in explorer.contents.topLevelItem(0)
      .data(0, _Qt.UserRole)[1][0])

# --------------------------------------------------------- launchers
explorer.show()
app.processEvents()


def shell_alive(label: str) -> None:
    """Explorer stays open and responsive while another app owns focus."""
    app.processEvents()
    check(label,
          explorer.isVisible()
          and explorer.isEnabled()
          and explorer.parent() is None
          and explorer in QApplication.topLevelWidgets())
    probe = "shell-lifecycle-probe"
    explorer.search.setText(probe)
    app.processEvents()
    check(f"{label} (responsive)", explorer.search.text() == probe)
    explorer.search.clear()


shell_alive("Explorer starts as the retained engineering shell")
# The guard is deliberately exercised with an opener that hides the shell;
# this is the regression case an ordinary, well-behaved launcher cannot
# produce until it is already broken.
explorer._launch_with_shell_retained(explorer.hide)
shell_alive("an application launcher cannot accidentally hide Explorer")

explorer.open_tag_database()
check("the Tag Database launches from the Explorer",
      getattr(tab, "_tagdb_dialog", None) is not None)
shell_alive("Explorer remains open behind the Tag Database")

explorer.open_control_designer()
check("Control Designer raises from the Explorer (the shell owns the "
      "session)", window.isVisible())
shell_alive("Explorer remains open behind Control Designer")

explorer.open_graphics_designer()
check("Graphics Designer launches from the Explorer",
      getattr(window, "_pvm_studio_window", None) is not None)
shell_alive("Explorer remains open behind Graphics Designer")

check("Operator Station launches from the Explorer",
      explorer.open_operator_station()
      and getattr(window, "_live_station_window", None) is not None)
shell_alive("Explorer remains open behind Operator Station")

explorer.open_diagnostics()
check("Diagnostics launches from the Explorer",
      window.designer._command_dialogs.get("diagnostics") is not None)
shell_alive("Explorer remains open behind Diagnostics")

# Closing the secondary applications must not make Qt treat Explorer as a
# transient owner and close it with them.
for secondary in (
        getattr(tab, "_tagdb_dialog", None),
        getattr(window, "_pvm_studio_window", None),
        getattr(window, "_live_station_window", None),
        window.designer._command_dialogs.get("diagnostics")):
    if secondary is not None:
        secondary.close()
app.processEvents()
shell_alive("Explorer remains open after secondary applications close")

for runtime in window.designer.controller_executive().online_runtimes():
    runtime.go_offline()
window.close()
app.processEvents()
shell_alive("Explorer remains open after Control Designer closes")
explorer.close()

print()
if failures:
    print(f"{len(failures)} Explorer check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All Azeo Explorer checks passed.")
