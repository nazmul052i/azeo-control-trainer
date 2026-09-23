"""Contract for the isolated AzeoPlantVirtualController engineering project.

Run::

    python tests/_smoke_virtual_io_project.py
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace


REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from azeo_control_trainer.connectivity.fieldio.opcua_catalog import (  # noqa: E402
    load_signal_catalog,
)
from azeo_control_trainer.connectivity.fieldio.local_virtual_io import (  # noqa: E402
    load_signal_routes,
)
import azeo_control_trainer.core.strategy.blocks  # noqa: E402,F401
from azeo_control_trainer.core.strategy.engine.compiler import (  # noqa: E402
    compile_strategy,
)
from azeo_control_trainer.core.strategy.engine.pk_controller import (  # noqa: E402
    PKController,
    PKModel,
)
from azeo_control_trainer.core.strategy.engine.validator import (  # noqa: E402
    validate_strategy,
)
from azeo_control_trainer.core.strategy.serialization.strategy_io import (  # noqa: E402
    load_strategy,
)
from azeo_control_trainer.core.strategy.tagdb import TagDatabase  # noqa: E402


TARGET = REPO / "projects" / "AzeoPlantVirtualController"
failures: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"[ok]   {label}")
    else:
        failures.append(label)
        print(f"[FAIL] {label}: {detail}")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_control_modules_markdown(path: Path) -> dict[str, dict]:
    """Independently project the specification table for drift detection."""
    lines = path.read_text(encoding="utf-8").splitlines()
    header = "| Module | PV | Output | Acting | Mode | Master | Gain | Reset | Role |"
    start = lines.index(header) + 2
    result: dict[str, dict] = {}
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        name, pv, output, action, mode, master, gain, reset, role = cells
        result[name] = {
            "pv": pv,
            "output": output,
            "action": action.lower(),
            "normal_mode": mode.upper(),
            "master": None if master == "-" else master,
            "gain": float(gain),
            "reset": float(reset),
            "role": role,
        }
    return result


project = read_json(TARGET / "_project.json")
area = project["areas"][0]
vio = area.get("virtual_io", {})
provider = vio.get("provider", {})
options = provider.get("options", {})

# --------------------------------------------------------- distinct identity
check("project has a distinct product identity",
      project.get("name") == "AzeoPlantVirtualController"
      and project.get("project_id") == "azeo_plant_virtual_controller")
check("area has a distinct identity",
      area.get("name") == "AZEO_PLANT_VIRTUAL_CONTROLLER"
      and area.get("area_id") == "azeo_plant_virtual_controller")
check("primary controller identity is distinct",
      area.get("controller", {}).get("name") == "APVC-CTRL-1")
controller_nodes = [node for node in project.get("nodes", [])
                    if node.get("type") == "controller"]
configured_controller = PKController.from_config(area.get("controller"))
check("controller model is the smallest size that holds all 612 native DSTs",
      configured_controller.model is PKModel.PK750
      and configured_controller.dst_limit == 750
      and PKModel.PK300.value < 612 <= configured_controller.dst_limit
      and len(controller_nodes) == 1
      and controller_nodes[0].get("model") == "PK750",
      {"area_model": configured_controller.model.name,
       "node_model": (controller_nodes[0].get("model")
                      if controller_nodes else None),
       "capacity": configured_controller.dst_limit})
check("one configured virtual controller is one execution boundary",
      len(controller_nodes) == 1
      and controller_nodes[0].get("node_id") == "apvc_ctrl_1"
      and set(project.get("assignments", {}).values()) == {"apvc_ctrl_1"},
      {"controllers": [node.get("node_id") for node in controller_nodes],
       "assignment_targets": sorted(set(
           project.get("assignments", {}).values()))})
check("local virtual I/O is not represented as an EIOC",
      "eioc" not in area
      and vio.get("type") == "local_virtual_io"
      and not any(n.get("type") == "eioc" for n in project.get("nodes", []))
      and any(n.get("type") == "virtual_io" for n in project.get("nodes", [])))

# ---------------------------------------------------------- provider contract
check("embedded provider factory is explicit",
      provider.get("factory") ==
      "azeoplant.embedding:create_embedded_plant")
check("embedded provider owns holder arbitration for the configured source",
      vio.get("startup_mode") == "manual"
      and vio.get("claim_outputs") is True
      and vio.get("provider_manages_claim") is True
      and vio.get("source") == "APVC-CTRL-1"
      and options.get("source") == vio.get("source"))
check("provider invocation is selected by configuration",
      provider.get("call_style") == "mapping")
check("provider search is repository-local only",
      provider.get("search_paths") == ["../../AzeoPlantSimulator"])
provider_root = (TARGET / provider["search_paths"][0]).resolve()
provider_module = (
    provider_root
    / Path(*provider["factory"].partition(":")[0].split(".")).with_suffix(".py")
)
check("repository owns the configured process-provider implementation",
      provider_root == (REPO / "AzeoPlantSimulator").resolve()
      and provider_module.is_file(),
      {"root": str(provider_root), "module": str(provider_module)})
check("provider owns its source and startup options",
      options == {
          "source": "APVC-CTRL-1",
          "dt": 0.1,
          "snapshot": "${PROJECT_DIR}/virtual_io/lined_up.snapshot.json",
          "catalog": "${PROJECT_DIR}/virtual_io/opcua_tag_catalog.json",
          "stale_timeout_s": 2.0,
          "speed_factor": 1.0,
          "autorun": True,
          "open_loop": True,
      }, options)
check("100 ms plant exchange and 200 ms regulatory scan are explicit",
      vio.get("dt") == 0.1
      and vio.get("period_ms") == 100
      and vio.get("input_stale_timeout_s") == 2.0
      and vio.get("timing", {}).get("integration_step_s") == 0.1
      and vio.get("timing", {}).get("exchange_period_ms") == 100
      and vio.get("timing", {}).get("regulatory_scan_rate_ms") == 200)

# ------------------------------------------------------------ 612-point map
project_catalog = TARGET / vio["catalog"]
project_snapshot = TARGET / vio["snapshot"]
control_module_path = TARGET / vio["control_module_basis"]
control_modules_markdown = control_module_path.with_name("Control_Modules.md")
catalog_document = read_json(project_catalog)
catalog_signals = load_signal_catalog(project_catalog)
control_module_document = read_json(control_module_path)
control_module_basis = control_module_document.get("modules", {})
markdown_control_modules = read_control_modules_markdown(
    control_modules_markdown
)
expected_signals = {
    tag: {"signal": tag, **spec}
    for tag, spec in catalog_signals.items()
}
resolved_routes = load_signal_routes(vio, TARGET)
check("project carries its catalog and startup snapshot",
      project_catalog.is_file() and project_snapshot.is_file())
check("project carries an auditable 80-module basis and matching schedule",
      control_module_path.is_file() and control_modules_markdown.is_file()
      and control_module_document.get("schema_version") == 1
      and control_module_document.get("companion") ==
      "engineering/Control_Modules.md"
      and control_module_document.get("companion_sha256") == hashlib.sha256(
          control_modules_markdown.read_bytes()).hexdigest()
      and control_module_basis == markdown_control_modules
      and len(control_module_basis) == 80)
check("local catalog carries no active network endpoint",
      "endpoint" not in catalog_document
      and "opc.tcp://" not in json.dumps(catalog_document),
      catalog_document.get("endpoint"))
snapshot_document = read_json(project_snapshot)
check("startup snapshot is the current stabilized plant state",
      snapshot_document.get("version") == 2
      and float(snapshot_document.get("sim_time") or 0.0) > 1000.0
      and isinstance(snapshot_document.get("bus"), dict),
      {"version": snapshot_document.get("version"),
       "sim_time": snapshot_document.get("sim_time"),
       "has_bus": isinstance(snapshot_document.get("bus"), dict)})
snapshot_bus = snapshot_document.get("bus", {})
snapshot_tags = snapshot_document.get("tags", {})
check("T2 bottoms leave through the heavy-product rundown while T1 still recycles",
      float(snapshot_bus.get("t2_heavy_rundown") or 0.0) > 0.0
      and float(snapshot_bus.get("t2_recycle_flow") or 0.0) == 0.0
      and float(snapshot_tags.get("FT-1002") or 0.0) > 0.0,
      {"heavy_rundown": snapshot_bus.get("t2_heavy_rundown"),
       "recycle": snapshot_bus.get("t2_recycle_flow"),
       "FT-1002": snapshot_tags.get("FT-1002")})
check("Local-VIO engineering names the revised T2 product path",
      catalog_signals.get("FT-6004", {}).get("description") ==
      "T2 heavy product rundown"
      and catalog_signals.get("FCV-6003", {}).get("description") ==
      "T2 heavy product rundown control valve",
      {tag: catalog_signals.get(tag, {}).get("description")
       for tag in ("FT-6004", "FCV-6003")})
lic6002 = read_json(TARGET / "control" / "LIC-6002.json")
pic6001 = read_json(TARGET / "control" / "PIC-6001.json")
motor602 = [read_json(TARGET / "control" / f"MC-P602{unit}.json")
            for unit in ("A", "B")]
check("affected controller metadata follows the revised topology",
      "heavy product rundown" in lic6002["engineering"][
          "strategy_demonstrated"]
      and any(
          block.get("config", {}).get("label") ==
          "T2 heavy product rundown"
          for block in pic6001.get("blocks", [])
      )
      and all("heavy product rundown" in document.get("description", "")
              for document in motor602))
check("project catalog contains exactly 612 points",
      len(expected_signals) == 612, len(expected_signals))
check("the project catalog is the sole authority for all 612 routes",
      "signals" not in vio
      and len(resolved_routes) == 612
      and set(resolved_routes) == set(expected_signals),
      {"inline_map_present": "signals" in vio,
       "resolved": len(resolved_routes), "expected": len(expected_signals)})
check("every LocalAdapter route has a non-vacuous catalog address/direction",
      len(resolved_routes) == 612
      and all(
          route.signal == tag
          and route.kind == str(catalog_signals[tag].get("kind") or "").upper()
          and route.direction
          == str(catalog_signals[tag].get("direction") or "").lower()
          for tag, route in resolved_routes.items()
      ))
tag_database = TagDatabase.from_area(TARGET)
configured_field_tags = tag_database.field_tags()
check("Tag Database and Explorer discover all 612 Local Virtual-I/O points",
      set(configured_field_tags) == set(expected_signals),
      {"configured": len(expected_signals),
       "indexed": len(configured_field_tags),
       "missing": sorted(set(expected_signals) - set(configured_field_tags))[:5],
       "extra": sorted(set(configured_field_tags) - set(expected_signals))[:5]})
capacity_store = SimpleNamespace(tagdb=tag_database, eioc=None)
check("the configured PK750 reports 612 of 750 DSTs without over-capacity",
      configured_controller.dst_usage(capacity_store) == 612
      and configured_controller.dst_limit == 750
      and not configured_controller.over_capacity(capacity_store),
      configured_controller.identity_line(capacity_store))

# ------------------------------------------------------- 80 module scan rates
regulatory = set(markdown_control_modules)
configured = set()
for path in (TARGET / "control").glob("*.json"):
    module = read_json(path)
    if module.get("scan_ms") == 200:
        configured.add(path.stem)
check("Control_Modules.md inventory has exactly 80 modules",
      len(regulatory) == 80, len(regulatory))
check("exactly the 80 specified modules execute at 200 ms",
      configured == regulatory,
      {"missing": sorted(regulatory - configured),
       "extra": sorted(configured - regulatory)})
check("documented TIC-4005 loop is present and assigned",
      (TARGET / "control" / "TIC-4005.json").is_file()
      and area["strategies"].count("control/TIC-4005.json") == 1
      and project["assignments"].get("control/TIC-4005.json") ==
      "apvc_ctrl_1")

# -------------------------------------------- engineering-data reconciliation
strategy_documents = [
    read_json(path)
    for folder in ("control", "sequence")
    for path in sorted((TARGET / folder).glob("*.json"))
]

# Every writable route must have one real producer.  Merely declaring a
# catalog point DCS-owned describes direction, not whether an executable
# strategy, the independent simulator SIS, or an engineering hold writes it.
strategy_output_owners: dict[str, str] = {}
duplicate_strategy_outputs: list[tuple[str, str, str]] = []
for document in strategy_documents:
    reference = f"control/{document['name']}.json"
    if (TARGET / "sequence" / f"{document['name']}.json").is_file():
        reference = f"sequence/{document['name']}.json"
    for block in document.get("blocks", []):
        if block.get("block_type") not in {"AO", "DO"}:
            continue
        tag = str(block.get("config", {}).get("tag")
                  or block.get("instance_name") or "")
        previous = strategy_output_owners.setdefault(tag, reference)
        if previous != reference:
            duplicate_strategy_outputs.append((tag, previous, reference))

ownership = vio.get("output_ownership", {})
ownership_routes = ownership.get("routes", {})
writable_routes = {
    tag for tag, spec in catalog_signals.items()
    if spec.get("direction") == "write"
}
sis_routes = {f"XY-900{index}" for index in range(1, 7)}
reserved_routes = {
    "FCV-2002", "FCV-2004", "FCV-4003", "FCV-7003", "FCV-7005",
    "GV-2001", "PCV-2003", "SC-7002", "TCV-3002", "TCV-8001",
}
owned_by_type = {
    owner: {
        tag for tag, record in ownership_routes.items()
        if record.get("owner") == owner
    }
    for owner in ("strategy", "simulator_sis", "reserved_inactive")
}
check("all 173 writable routes have one explicit executable owner",
      set(ownership_routes) == writable_routes
      and len(ownership_routes) == 173
      and not duplicate_strategy_outputs,
      {"missing": sorted(writable_routes - set(ownership_routes)),
       "extra": sorted(set(ownership_routes) - writable_routes),
       "duplicates": duplicate_strategy_outputs})
check("writable ownership is the evidenced 157/6/10 partition",
      owned_by_type["strategy"] == set(strategy_output_owners)
      and all(
          ownership_routes[tag].get("reference") == reference
          for tag, reference in strategy_output_owners.items()
      )
      and len(owned_by_type["strategy"]) == 157
      and owned_by_type["simulator_sis"] == sis_routes
      and owned_by_type["reserved_inactive"] == reserved_routes,
      {owner: sorted(tags) for owner, tags in owned_by_type.items()})

stale_unavailable: list[tuple[str, str, str]] = []
for document in strategy_documents:
    engineering = document.get("engineering", {})
    for field in ("unavailable_input_tags", "unavailable_output_tags"):
        for tag in engineering.get(field, []):
            if tag in catalog_signals:
                stale_unavailable.append((document.get("name"), field, tag))
check("module metadata has no catalogued point marked unavailable",
      not stale_unavailable, stale_unavailable)

pic_5001 = next(document for document in strategy_documents
                if document.get("name") == "PIC-5001")
pic_blocks = {block.get("instance_name"): block
              for block in pic_5001.get("blocks", [])}
pic_wires = {
    (wire.get("src_block_id"), wire.get("src_terminal"),
     wire.get("dst_block_id"), wire.get("dst_terminal"))
    for wire in pic_5001.get("wires", [])
}
splitter = pic_blocks.get("SPLIT_RANGE", {})
check("PIC-5001 implements its documented two-valve split range",
      splitter.get("block_type") == "SPLITTER"
      and pic_blocks.get("PCV-5002", {}).get("block_type") == "AO"
      and pic_blocks.get("PCV-5001", {}).get("block_type") == "AO"
      and (splitter.get("id"), "OUT1", pic_blocks["PCV-5002"]["id"],
           "CAS_IN") in pic_wires
      and (splitter.get("id"), "OUT2", pic_blocks["PCV-5001"]["id"],
           "CAS_IN") in pic_wires,
      {"blocks": sorted(pic_blocks), "wires": sorted(pic_wires)})

restored_valves = {
    "XC-MOV1002": (
        {"ZSO-MOV1002", "ZSC-MOV1002"},
        {"XY-MOV1002-OPN", "XY-MOV1002-CLS"},
        "COMMISSIONED",
    ),
    "XC-XV0101": (
        {"ZSO-XV0101", "ZSC-XV0101"},
        {"XY-XV0101-OPN"},
        "COMMISSIONED",
    ),
    "XC-XV3002": (
        {"ZSO-XV3002", "ZSC-XV3002"},
        {"XY-XV3002-OPN"},
        "COMMISSIONED",
    ),
    "HC-HV1002": ({"ZSO-HV1002", "ZSC-HV1002"}, set(), "MONITOR_ONLY"),
    "HC-HV5001": ({"ZSO-HV5001", "ZSC-HV5001"}, set(), "MONITOR_ONLY"),
    "HC-HV5002": ({"ZSO-HV5002", "ZSC-HV5002"}, set(), "MONITOR_ONLY"),
    "HC-HV6001": ({"ZSO-HV6001", "ZSC-HV6001"}, set(), "MONITOR_ONLY"),
    "HC-HV7001": ({"ZSO-HV7001", "ZSC-HV7001"}, set(), "MONITOR_ONLY"),
    "HC-HV8001": ({"ZSO-HV8001", "ZSC-HV8001"}, set(), "MONITOR_ONLY"),
}
valve_mismatches: list[tuple] = []
for name, (inputs, outputs, state) in restored_valves.items():
    document = next(item for item in strategy_documents
                    if item.get("name") == name)
    engineering = document.get("engineering", {})
    blocks = document.get("blocks", [])
    block_inputs = {
        block.get("config", {}).get("tag")
        for block in blocks if block.get("block_type") == "DI"
    }
    block_outputs = {
        block.get("config", {}).get("tag")
        for block in blocks if block.get("block_type") == "DO"
    }
    if not (
        set(engineering.get("mapped_input_tags", [])) == inputs
        and set(engineering.get("mapped_output_tags", [])) == outputs
        and engineering.get("implementation_state") == state
        and inputs <= block_inputs
        and outputs <= block_outputs
    ):
        valve_mismatches.append((
            name, engineering.get("implementation_state"),
            sorted(block_inputs), sorted(block_outputs),
        ))
check("current-catalog valve I/O is executable or explicitly monitor-only",
      not valve_mismatches, valve_mismatches)

reconciled_module_errors: list[tuple[str, object]] = []
for name in {"PIC-5001", *restored_valves}:
    try:
        graph, _comments = load_strategy(TARGET / "control" / f"{name}.json")
        compiled = compile_strategy(graph)
        errors = [item for item in validate_strategy(graph)
                  if item.severity == "ERROR"]
        if len(compiled.exec_order) != len(graph.blocks) or errors:
            reconciled_module_errors.append((name, errors))
    except Exception as error:  # noqa: BLE001 - report every graph defect
        reconciled_module_errors.append((name, repr(error)))
check("reconciled control modules load, validate, and compile",
      not reconciled_module_errors, reconciled_module_errors)

ai_mismatches: list[tuple] = []
ai_usage_count = 0
for module in strategy_documents:
    for block in module.get("blocks", []):
        if block.get("block_type") != "AI":
            continue
        config = block.get("config", {})
        tag = str(config.get("tag") or block.get("instance_name") or "")
        spec = catalog_signals.get(tag)
        if spec is None or spec.get("kind") != "AI":
            continue
        ai_usage_count += 1
        lo, hi = (float(value) for value in spec["range"])
        expected = (lo, hi, lo, hi, str(spec.get("unit") or ""))
        actual = (
            config.get("scale_lo"), config.get("scale_hi"),
            config.get("XD_SCALE_LO"), config.get("XD_SCALE_HI"),
            config.get("eng_units"),
        )
        if actual != expected:
            ai_mismatches.append((module.get("name"), tag, actual, expected))
check("every catalog-backed AI uses catalog EU range and units",
      ai_usage_count > 0 and not ai_mismatches,
      ai_mismatches[:5])

pid_mismatches: list[tuple] = []
documents_by_name = {document.get("name"): document
                     for document in strategy_documents}
for name, row in control_module_basis.items():
    document = documents_by_name.get(name, {})
    pids = [block for block in document.get("blocks", [])
            if block.get("block_type") == "PID"
            and block.get("instance_name") == name]
    pv_spec = catalog_signals.get(str(row.get("pv")), {})
    if len(pids) != 1 or pv_spec.get("kind") != "AI":
        pid_mismatches.append((name, "missing PID or catalog PV"))
        continue
    lo, hi = (float(value) for value in pv_spec["range"])
    expected = {
        "GAIN": float(row["gain"]),
        "RESET": float(row["reset"]),
        "action": row["action"],
        "normal_mode": row["normal_mode"],
        "pv_scale_lo": lo,
        "pv_scale_hi": hi,
        "sp_lo": lo,
        "sp_hi": hi,
        "pv_unit": str(pv_spec.get("unit") or ""),
    }
    if name in {"FIC-5002", "FIC-6002"}:
        expected["sp_hi"] = 16.5
    if name in {"TIC-5001", "TIC-5002", "TIC-6001", "TIC-6002"}:
        startup_pv = float(snapshot_document["tags"][str(row["pv"])])
        expected["sp_lo"] = startup_pv - 32.0
        expected["sp_hi"] = startup_pv + 20.0
    config = pids[0].get("config", {})
    actual = {key: config.get(key) for key in expected}
    if actual != expected:
        pid_mismatches.append((name, actual, expected))
check("all 80 PID engineering records match catalog and control-module basis",
      set(control_module_basis) == regulatory and not pid_mismatches,
      pid_mismatches[:5])

cascade_mismatches: list[tuple] = []
for document in strategy_documents:
    blocks = {block.get("id"): block for block in document.get("blocks", [])}
    for handoff in (block for block in blocks.values()
                    if block.get("block_type") == "SP_HANDOFF"):
        target = str(handoff.get("config", {}).get("controller_tag") or "")
        target_basis = control_module_basis.get(target)
        required_modes = handoff.get("config", {}).get("required_modes")
        if target_basis is None or required_modes != target_basis["normal_mode"]:
            cascade_mismatches.append((
                document.get("name"), handoff.get("instance_name"),
                required_modes,
                None if target_basis is None else target_basis["normal_mode"],
            ))
        active_wires = [wire for wire in document.get("wires", [])
                        if wire.get("dst_block_id") == handoff.get("id")
                        and wire.get("dst_terminal") == "ACTIVE"]
        if not active_wires:
            cascade_mismatches.append((
                document.get("name"), handoff.get("instance_name"),
                "missing ACTIVE wire",
            ))
        for wire in active_wires:
            source = blocks.get(wire.get("src_block_id"), {})
            if source.get("instance_name") == "CASCADE_ENABLE" and not (
                    source.get("block_type") == "MEM_BOOL"
                    and source.get("config", {}).get("initial_value") is True):
                cascade_mismatches.append((document.get("name"), source))
check("cascade SP handoffs are armed and require engineered slave mode",
      not cascade_mismatches, cascade_mismatches)

# ------------------------------------------------------------- portability
serialized_vio = json.dumps(vio, sort_keys=True)
absolute_windows_path = re.search(r"(?i)(?:^|[\"'])\s*[a-z]:[\\/]", serialized_vio)
check("virtual-I/O configuration has no absolute filesystem paths",
      absolute_windows_path is None, absolute_windows_path)
check("local project has no configured network endpoint",
      "://" not in serialized_vio and "endpoint" not in serialized_vio.lower())

runtime_sources = "\n".join(
    (REPO / relative).read_text(encoding="utf-8")
    for relative in (
        "src/azeo_control_trainer/connectivity/fieldio/dynamic_provider.py",
        "src/azeo_control_trainer/connectivity/fieldio/local_virtual_io.py",
    )
)
check("trainer Local-VIO runtime contains no APVC-specific communication data",
      not any(token in runtime_sources for token in (
          "AzeoPlantSimulator", "AzeoPlantVirtualController", "APVC-CTRL",
          "azeoplant.embedding", "../../../AzeoPlantSimulator",
          "../../AzeoPlantSimulator", "FCV-1001",
          "opc.tcp://", "48420",
      )))
app_loader_source = (
    REPO / "src/azeo_control_trainer/app.py"
).read_text(encoding="utf-8")
check("application loader selects Local-VIO generically from project data",
      not any(token in app_loader_source for token in (
          "AzeoPlantVirtualController", "APVC-CTRL", "APVC-VIO",
          "azeoplant.embedding", "../../../AzeoPlantSimulator",
          "../../AzeoPlantSimulator", "FCV-1001",
          "opc.tcp://0.0.0.0:48420",
      )))

# ---------------------------------------------------------- project docs
readme = (TARGET / "README.md").read_text(encoding="utf-8")
basis = (TARGET / "CONTROL_STRATEGY_BASIS.md").read_text(encoding="utf-8")
topology_doc = (TARGET / "CONTROL_TOPOLOGY.md").read_text(encoding="utf-8")
documentation = readme + "\n" + basis + "\n" + topology_doc
donor_claims = (
    "492",
    "APS-CTRL-2",
    "APS-EIOC-1",
    "EIOC owns",
    "owned by one EIOC",
)
check("project docs reject the donor I/O and controller topology",
      not any(claim.lower() in documentation.lower()
              for claim in donor_claims),
      [claim for claim in donor_claims
       if claim.lower() in documentation.lower()])
required_doc_claims = (
    "160 assigned",
    "158 control",
    "2 sequence",
    "APVC-CTRL-1",
    "PK750",
    "750-DST capacity",
    "APVC-VIO-1",
    "LocalAdapter",
    "612",
    "100 ms",
    "200 ms",
    "nine linked CT1 module-class instances",
    "open_loop: true",
    "independent SIS",
    "../../AzeoPlantSimulator",
    "${PROJECT_DIR}",
    "clean stop",
    "logs/system/",
)
check("project docs state the executable local-VIO contract",
      all(claim.lower() in documentation.lower()
          for claim in required_doc_claims),
      [claim for claim in required_doc_claims
       if claim.lower() not in documentation.lower()])
check("README launches Explorer and owns simulator lifecycle there",
      "run.py projects\\AzeoPlantVirtualController" in readme
      and "Tools > Virtual I/O > Start Simulator" in readme
      and "--station" not in readme)
check("OPC interoperability is documented as disabled and optional",
      re.search(r"optional\s+OPC UA", documentation, re.IGNORECASE) is not None
      and "disabled" in documentation
      and "no separately started simulator" in readme.lower()
      and "opc server" in readme.lower()
      and "eioc" in readme.lower())

if failures:
    raise SystemExit(f"{len(failures)} virtual-I/O project check(s) failed")
print("All AzeoPlantVirtualController project checks passed.")
