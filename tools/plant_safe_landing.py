"""Engineering recipe and ordinary Graphics Designer documents for the APVC plant.

This module is an offline authoring tool. It never writes process values.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re

from azeo_control_trainer.core.procedures.library import block_for_type
from azeo_control_trainer.core.procedures.model import AdvisoryProcedure, ProcedureDefinition

REFERENCE = "pa_safe_landing/rev-003/procedure.yaml"
CLASS = "PlantSafeLanding"
MONITOR = "Plant - L2 Shutdown Monitoring"
WORKFLOW = "Plant - L3 Safe Landing"


@dataclass(frozen=True)
class Stage:
    key: str
    title: str
    instruction: str
    actions: tuple
    condition: str
    dwell: float = 5
    timeout: float = 180


def logical(tag):
    return "P." + tag.replace("-", "_")


def stages():
    heat = (("FCV-3001", 0), ("FCV-3002", 0), ("FCV-3003", 0),
            ("XY-XV3001-OPN", False), ("XY-XV3002-OPN", False))
    feed = (("FCV-1001", 0), ("XY-XV1001-OPN", False), ("LCV-1001", 0),
            ("XY-MOV1002-OPN", False), ("XY-MOV1002-CLS", True))
    columns = (("FCV-5004", 0), ("FCV-6004", 0), ("XY-XV5001-OPN", False),
               ("XY-XV6001-OPN", False), ("LCV-4001", 0), ("LCV-4002", 0))
    boiler = (("FCV-7002", 0), ("XY-XV7001-OPN", False), ("FCV-7001", 0),
              ("FCV-7004", 0), ("TCV-7001", 0))
    rotating = tuple(pair for motor in ("P101A", "P101B", "C1", "P501A", "P501B", "P502A", "P502B",
                                        "P601A", "P601B", "P602A", "P602B")
                     for pair in ((f"XY-{motor}-STR", False), (f"XY-{motor}-STP", True)))
    return (
        Stage("upstream", "Isolate upstream fresh feed", "Arrange upstream feed isolation. In this simulator the battery-limit source has no DCS valve: use Simulation Workbench's Fresh feed rate step (MF-031), active with value 0. The procedure waits for actual FT-1004 readback; confirmation alone cannot complete isolation.", (),
              "P.FT_1004 <= 3", 5, 300),
        Stage("heater", "Remove H1 firing", "Use Heat controls to put each listed output in MAN and remove gas/oil demand; close both fuel shutdown valves. Keep ID-301 ventilation available. The simulator may latch its reaction trip after flame disappears; do not bypass or reset that protection.", heat,
              "P.FT_3001 <= 50 and P.BS_3001 == False and P.ZSC_XV3001 == True and P.FCV_3002 <= 0.1 and P.FCV_3003 <= 0.1"),
        Stage("feed", "Stop fresh charge and recycle import", "Use Feed controls to close the charge demand and XV-1001, stop off-spec import, and close MOV-1002 recycle import. Verify actual closed switches and charge flow; an accepted command alone is insufficient.", feed,
              "P.FT_1001 <= 4 and P.ZSC_XV1001 == True and P.ZSC_MOV1002 == True", 8, 240),
        Stage("columns", "Remove column heat and isolate transfers", "Use Columns controls to remove both reboiler steam demands, close T1/T2 feed valves and stop D3 liquid/water transfer. Retain condenser and D3 cooling. Verify both steam flows and both feed valves.", columns,
              "P.FT_5005 <= 0.5 and P.FT_6005 <= 0.5 and P.ZSC_XV5001 == True and P.ZSC_XV6001 == True", 10, 300),
        Stage("boiler", "Remove B1 firing and balance the held inventory", "Use Boiler controls to remove fuel and close XV-7001. With steam demand removed, stop feedwater, blowdown and spray demand for this training hold. Retain water and cooling services; monitor drum level and pressure. Expected flame-failure protection remains active.", boiler,
              "P.FT_7003 <= 5 and P.BS_7001 == False and P.ZSC_XV7001 == True and P.FT_7002 <= 0.5", 10, 300),
        Stage("rotating", "Stop process pumps and compressor", "Use Rotating controls to remove START and apply STOP for the charge, reflux, product pumps and C1. Retain P-201/P-202 lube service, cooling and effluent treatment. Check actual RUN feedback, not only command readback.", rotating,
              " and ".join(f"P.XS_{motor}_RUN == False" for motor in ("P101A", "P101B", "C1", "P501A", "P501B", "P502A", "P502B", "P601A", "P601B", "P602A", "P602B")), 10, 240),
        Stage("containment", "Retain hot inventories and support services", "Close R1 feed/off-gas commands and remove quench demand after feed and firing have stopped. Retain pressure-control/relief paths, lube, D3 cooling, condensers and effluent services. Do not depressure or reset SIS as part of this procedure.",
              (("XY-XV4001-OPN", False), ("XY-XV4002-OPN", False), ("FCV-4001", 0)),
              "P.ZSC_XV4001 == True and P.ZSC_XV4002 == True and (P.XS_P201_RUN or P.XS_P202_RUN) and P.FT_5006 > 0 and P.FT_6006 > 0 and P.FCV_4002 >= 50", 10, 180),
    )


def route_index(project):
    routes = {}
    for path in sorted((project / "control").glob("*.json")) + sorted((project / "sequence").glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        for block in document["blocks"]:
            kind = block["block_type"]
            tag = block.get("config", {}).get("tag")
            if kind in {"AI", "DI", "AO", "DO"} and tag:
                routes.setdefault(tag, (f"{document['name']}/{block['instance_name']}", kind))
    return routes


def definition(project):
    routes = route_index(project)
    tagged = set()
    steps = []

    def add(kind, key, **fields):
        item = dict(block_for_type(kind).instantiate(key), **fields)
        if kind == "wait_until":
            item["timer_tuning"] = "next_run"
        steps.append(item)
        tagged.update(re.findall(r"P\.([A-Z0-9_]+)", item.get("condition", "")))
        return item

    add("instruction", "review_scope", require_confirmation=True, section="Prepare",
        description="Safe Landing is operator-guided training for APVC. You retain all equipment controls. Pause guidance or End guidance at any time to shut down manually. This ends in a monitored HOT, PRESSURIZED hold, not maintenance isolation. Review alarms and utilities before proceeding.")
    add("wait_until", "support_ready", section="Prepare", stable_for_sec=5, timeout_sec=180,
        poll_sec=.1, on_timeout="hold", description="Verify lube and cooling remain available.",
        condition="(P.XS_P201_RUN or P.XS_P202_RUN) and P.FT_5006 > 0 and P.FT_6006 > 0 and P.FCV_4002 >= 50")
    for stage in stages():
        for tag, _ in stage.actions:
            tagged.add(tag.replace("-", "_"))
        equipment = logical(stage.actions[0][0]) if stage.actions else "P.FT_1004"
        add("instruction", stage.key + "_operate", section=stage.title, equipment=equipment,
            require_confirmation=True, description=stage.instruction,
            notes="Process actions are manual, through checked HMI writes. Guidance never executes these commands.")
        add("wait_until", stage.key + "_prove", section=stage.title, equipment=equipment,
            description=stage.title + " — verify all readbacks continuously.", condition=stage.condition,
            stable_for_sec=stage.dwell, timeout_sec=stage.timeout, poll_sec=.1, on_timeout="hold")
    final = ("P.FT_1004 <= 3 and P.FT_1001 <= 4 and P.FT_3001 <= 50 and P.FT_7003 <= 5 and "
             "P.FT_5005 <= 0.5 and P.FT_6005 <= 0.5 and P.BS_3001 == False and P.BS_7001 == False and "
             "P.ZSC_XV1001 == True and P.ZSC_XV4001 == True and P.ZSC_XV5001 == True and P.ZSC_XV6001 == True and "
             "P.TT_3005 < 600 and P.TT_4002 < 430 and P.TT_4003 < 430 and P.PT_4001 < 50 and P.PT_7001 < 50 and "
             "15 < P.LT_1001 < 80 and 15 < P.LT_4001 < 80 and 15 < P.LT_5001 < 80 and 15 < P.LT_6001 < 80 and "
             "15 < P.LT_5002 < 80 and 15 < P.LT_6002 < 80 and "
             "-150 < P.LT_7001 < 150 and (P.XS_P201_RUN or P.XS_P202_RUN) and P.FCV_4002 >= 50 and P.FT_5006 > 0 and P.FT_6006 > 0")
    final += " and " + next(stage.condition for stage in stages() if stage.key == "rotating")
    add("wait_until", "hot_hold_prove", section="Handover", stable_for_sec=30, timeout_sec=600,
        poll_sec=.1, on_timeout="hold", description="Prove the monitored hot-hold envelope for 30 continuous simulation seconds. If any condition fails, keep control manually; a timeout holds this run for review.", condition=final)
    add("operator_comment", "handover", section="Handover", description="Record retained heat, pressure, utilities, active SIS causes and the next operator's monitoring responsibility.",
        comment_prompt="Record the hot/pressurized handover and any deviations. This is not a cold, gas-free or maintenance release.")
    add("complete", "complete", section="Handover", description="Training safe landing verified: feed and heat stopped, process rotation stopped, monitored hot inventories retained. Continue monitoring; no restart or maintenance release is granted.")
    aliases = {tag.replace("-", "_"): tag for tag in routes}
    missing = tagged - aliases.keys()
    if missing:
        raise ValueError(f"Plant route evidence is missing for {sorted(missing)}")
    bindings = {}
    tags = []
    for alias in sorted(tagged):
        tag = aliases[alias]
        path, kind = routes[tag]
        field = {"AI": "PV", "DI": "PV_D", "AO": "READBACK", "DO": "READBACK_D"}[kind]
        bindings["P." + alias] = path + "/" + field
        tags.append(dict(tag="P." + alias, data_type="bool" if kind in {"DI", "DO"} else "float", access="read"))
    data = dict(procedure_id="pa_safe_landing", name="Plant Safe Landing — guided hot shutdown", unit="APVC",
                mode="advisory", connectivity={"mapping_path": "mapping.yaml", "read_only": True},
                metadata={"version": "1.0.1", "safety_class": "training", "approval_status": "draft",
                          "revision_note": "Operator-guided simulation recipe. Retained heat/pressure and independent SIS are explicit.",
                          "canvas_layout": [{"step_id": step["id"], "x": 120 + (index // 8) * 470,
                                             "y": 100 + (index % 8) * 240} for index, step in enumerate(steps)],
                          "canvas_page": {"title": "Plant Safe Landing", "width": 1900, "height": 2300}},
                tags=tags, steps=steps)
    return ProcedureDefinition(AdvisoryProcedure.model_validate(data), bindings).validate()
