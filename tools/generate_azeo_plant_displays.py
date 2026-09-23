#!/usr/bin/env python3
"""Generate the editable Azeo Plant Simulator L1-L4 operator hierarchy.

The source P&IDs in ``AzeoPlantSimulator/docs/PID`` describe process
relationships; they are not used as canvas backgrounds.  This generator turns
those relationships into native Graphics Designer objects so every symbol, pipe,
PVM, value, table, trend, and navigation link remains editable.

Run with the repository venv::

    D:\\development\\GitHub\\vpy\\Scripts\\python.exe \
        tools\\generate_azeo_plant_displays.py
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from azeo_control_trainer.core.hmi.pvms.layout import (  # noqa: E402
    DYNAMIC, DisplayFrame, DisplaySet, Layout, Screen,
    LayoutStore,
)
from azeo_control_trainer.core.hmi.pvms.elements import (  # noqa: E402
    default_symbol_ports,
)
from azeo_control_trainer.core.hmi.pvms.publishing import (  # noqa: E402
    CURRENT_DISPLAY_SCHEMA_VERSION,
    DisplayStore,
    PvmDisplay,
)


DISPLAY_ROOT = (
    ROOT / "projects" / "AzeoPlantVirtualController" / "displays" / "pvm"
)
DISPLAY_SET = "Azeo Plant - Operator Hierarchy"
LAYOUT = "Azeo Plant - Operator Layout"
CONSOLE_ID = "CON-01"
L1 = "Overview - L1 Plant"
L2 = "Overview - L2 Process Coordination"
L3 = "Overview - L3 Charge Heater"
L4 = "Overview - L4 Heater Safety"

WIDTH = 1600
HEIGHT = 900
BACKGROUND = "#E3E6EA"
PANEL = "#EEF1F3"
FIELD = "#F7F8F9"
LINE = "#8D9AA3"
TEXT = "#34434D"
TEXT_DIM = "#61717C"
ACTION = "#0B5E91"
TEAL = "#168B8F"
PIPE = "#526B7A"


def _base(kind: str, ident: str, x: float, y: float, w: float,
          h: float, **fields) -> dict:
    data = {"kind": kind, "id": ident, "x": x, "y": y, "w": w, "h": h}
    data.update(fields)
    return data


def _text(ident: str, value: str, x: float, y: float, w: float,
          h: float, *, size: float = 10, bold: bool = False,
          colour: str = TEXT, align: str = "left",
          valign: str = "middle", wrap: bool = False) -> dict:
    return _base(
        "text", ident, x, y, w, h, text=value, font_size=size,
        font_bold=bold, text_color=colour, text_halign=align,
        text_valign=valign, text_wrap=wrap,
    )


def _rect(ident: str, x: float, y: float, w: float, h: float, *,
          fill: str = "", line: str = LINE, width: float = 1,
          radius: float = 0) -> dict:
    fields = {"line": line, "width": width}
    if fill:
        fields["fill"] = fill
    if radius:
        fields["radius"] = radius
    return _base("round_rect" if radius else "rect", ident,
                 x, y, w, h, **fields)


def _line(ident: str, x: float, y: float, w: float, h: float = 1, *,
          colour: str = LINE, width: float = 1.5,
          end_arrow: str = "none", style: str = "solid") -> dict:
    return _base(
        "line", ident, x, y, w, h, line=colour, width=width,
        end_arrow=end_arrow, style=style,
    )


def _symbol(ident: str, name: str, x: float, y: float,
            w: float, h: float, *, rot: float = 0,
            ports: tuple[dict, ...] = ()) -> dict:
    data = _base("symbol", ident, x, y, w, h, symbol=name)
    if rot:
        data["rot"] = rot
    if ports:
        data["ports"] = [dict(port) for port in ports]
    return data


def _equipment_ports() -> tuple[dict, ...]:
    """Stable process names, normalized to the equipment footprint.

    Pipes bind to these names instead of whichever edge happened to be
    closest when a symbol was placed. The names survive resize, mirror and
    symbol replacement, which is what makes a process drawing maintainable.
    """
    return tuple(default_symbol_ports())


def _pipe(ident: str, a: str, b: str, *, a_side: str = "e",
          b_side: str = "w", width: float = 4,
          colour: str = PIPE, arrow: str = "filled_arrow",
          crossover: str = "jump", auto: bool = False) -> dict:
    return {
        "kind": "pipe", "id": ident, "a": a, "a_side": a_side,
        "b": b, "b_side": b_side, "line": colour, "width": width,
        "style": "solid", "end_arrow": arrow,
        "crossover": crossover, "auto": auto,
    }


def _link(ident: str, target: str, label: str, x: float, y: float,
          w: float = 150, h: float = 32) -> dict:
    return _base(
        "display_link", ident, x, y, w, h,
        target=target, text=label,
    )


def _datalink(ident: str, path: str, x: float, y: float,
              w: float, h: float = 26, *, decimals: int = 1,
              units: bool = True, dtype: str = "numeric",
              true_text: str | None = None,
              false_text: str | None = None) -> dict:
    item = _base(
        "datalink", ident, x, y, w, h, datalink_type=dtype,
        path=path, decimals=decimals, units=units,
    )
    if true_text is not None and false_text is not None:
        item["boolean_labels"] = {
            "true": true_text,
            "false": false_text,
        }
    return item


def _table(ident: str, x: float, y: float, w: float, h: float,
           columns: list[dict], rows: list[dict], *,
           row_height: int = 23, header_height: int = 22) -> dict:
    return _base(
        "table", ident, x, y, w, h, columns=columns, rows=rows,
        row_height=row_height, header_height=header_height,
    )


def _chart(ident: str, x: float, y: float, w: float, h: float,
           pens: list[tuple[str, str]], *, lo: float = 0,
           hi: float = 100) -> dict:
    return _base(
        "chart", ident, x, y, w, h,
        pens=[{"label": label, "path": path} for label, path in pens],
        lo=lo, hi=hi,
    )


def _alarm_list(ident: str, x: float, y: float, w: float, h: float,
                *, prefix: str = "", priority: int = 0) -> dict:
    return _base(
        "alarm_list", ident, x, y, w, h,
        path_prefix=prefix, priority_min=priority,
    )


def _pvm(ident: str, class_key: str, path: str, x: float, y: float,
         w: float, h: float, *, variant: str = "", label: str = "",
         choices: dict | None = None) -> dict:
    data = {
        "id": ident, "class": class_key, "params": {"path": path},
        "x": x, "y": y, "w": w, "h": h,
        "standard": "hphmi.controller",
    }
    if variant:
        data["variant"] = variant
    if label:
        data["label"] = label
    if choices:
        data["choices"] = dict(choices)
    return data


def _header(level: int, title: str, purpose: str, *,
            parent: str = "", child: str = "") -> list[dict]:
    items = [
        _text(f"l{level}_title", title, 38, 17, 930, 35,
              size=19, bold=True),
        _text(f"l{level}_purpose", purpose, 38, 54, 1045, 30,
              size=9, colour=TEXT_DIM, wrap=True),
        _rect(f"l{level}_badge", 1470, 20, 88, 54,
              fill=ACTION, line=ACTION, radius=5),
        _text(f"l{level}_badge_text", f"L{level}", 1470, 20, 88, 54,
              size=19, bold=True, colour="#FFFFFF", align="center"),
        _line(f"l{level}_rule", 38, 95, 1520, colour=LINE, width=1),
    ]
    x = 1130
    if parent:
        items.append(_link(f"l{level}_up", parent, "Up one level", x, 51))
        x += 160
    if child:
        items.append(_link(f"l{level}_down", child, "Open next level", x, 51))
    return items


def _section(ident: str, title: str, x: float, y: float,
             w: float, h: float) -> list[dict]:
    return [
        _rect(f"{ident}_panel", x, y, w, h,
              line=LINE, radius=5),
        _text(f"{ident}_title", title, x + 12, y + 6, w - 24, 24,
              size=10, bold=True),
    ]


def _live(path: str, *, decimals: int = 1, units: bool = True,
          dtype: str = "numeric", true_text: str = "ACTIVE",
          false_text: str = "CLEAR") -> dict:
    spec = {"path": path, "type": "numeric" if dtype == "boolean" else dtype,
            "decimals": decimals, "units": units}
    if dtype == "boolean":
        spec["boolean_labels"] = {
            "true": true_text,
            "false": false_text,
        }
    return spec


@dataclass(frozen=True)
class UnitDisplaySpec:
    unit: str
    name: str
    title: str
    purpose: str
    reference: str
    equipment: tuple[tuple[str, str, str, str, float, float, float, float], ...]
    pipes: tuple[tuple[str, str, str, str], ...]
    loops: tuple[tuple[str, str], ...]
    kpis: tuple[tuple[str, str], ...]


UNIT_SPECS = (
    UnitDisplaySpec(
        "U010", "U010 - L2 Fuel Gas and Utilities", "FUEL GAS AND UTILITIES",
        "Control the plant fuel-gas header and maintain reliable supply to H1, "
        "B1, flare, and utility consumers.", "AP-PID-U010",
        (
            ("u010_source", "stack", "SOURCE", "Natural-gas supply", 90, 240, 80, 150),
            ("u010_knockout", "drum", "D3 OFFGAS", "Recovered fuel gas", 285, 268, 115, 90),
            ("u010_header", "vessel", "FG HEADER", "Fuel-gas distribution", 510, 235, 100, 160),
            ("u010_h1", "furnace", "H1", "Charge heater", 750, 238, 95, 155),
            ("u010_b1", "heater", "B1", "Steam boiler", 945, 252, 95, 130),
        ),
        (("u010_source", "u010_header", "e", "w"),
         ("u010_knockout", "u010_header", "e", "w"),
         ("u010_header", "u010_h1", "e", "w"),
         ("u010_header", "u010_b1", "e", "w")),
        (("PIC-0101/PIC-0101", "Supply pressure"),
         ("PIC-0102/PIC-0102", "Fuel header"),
         ("PIC-0103/PIC-0103", "Flare header"),
         ("FIC-0101/FIC-0101", "Fuel flow")),
        (("Supply pressure", "PIC-0101/PIC-0101/PV"),
         ("Header pressure", "PIC-0102/PIC-0102/PV"),
         ("Fuel flow", "FIC-0101/FIC-0101/PV")),
    ),
    UnitDisplaySpec(
        "U100", "U100 - L2 Feed Preparation", "FEED PREPARATION",
        "Hold D1 inventory and deliver stable charge flow through the duty and "
        "standby feed-pump train.", "AP-PID-U100",
        (
            ("u100_feed", "valve", "FEED", "Battery-limit feed", 75, 283, 70, 70),
            ("u100_d1", "tank", "D1", "Feed surge drum", 260, 210, 120, 205),
            ("u100_p101a", "pump", "P101A", "Duty charge pump", 530, 230, 100, 95),
            ("u100_p101b", "pump", "P101B", "Standby charge pump", 530, 410, 100, 95),
            ("u100_out", "control_valve", "FCV-1002", "Charge to U200", 835, 280, 80, 65),
        ),
        (("u100_feed", "u100_d1", "e", "w"),
         ("u100_d1", "u100_p101a", "e", "w"),
         ("u100_d1", "u100_p101b", "e", "w"),
         ("u100_p101a", "u100_out", "e", "w"),
         ("u100_p101b", "u100_out", "e", "w")),
        (("LIC-1001/LIC-1001", "D1 level"),
         ("FIC-1001/FIC-1001", "Pump flow"),
         ("SIC-1001/SIC-1001", "Pump speed"),
         ("FIC-1002/FIC-1002", "Charge flow")),
        (("D1 level", "LIC-1001/LIC-1001/PV"),
         ("Charge flow", "FIC-1002/FIC-1002/PV"),
         ("D1 pressure", "PIC-1001/PIC-1001/PV")),
    ),
    UnitDisplaySpec(
        "U200", "U200 - L2 Recycle Compression", "RECYCLE GAS COMPRESSION",
        "Protect C1 surge margin while maintaining reactor-loop circulation, "
        "knockout-drum level, discharge pressure, and temperature.", "AP-PID-U200",
        (
            ("u200_in", "drum", "V201", "Suction knockout drum", 95, 225, 130, 105),
            ("u200_c1", "compressor", "C1", "Recycle compressor", 380, 240, 145, 115),
            ("u200_cooler", "air_cooler", "E201", "Discharge cooler", 650, 225, 120, 110),
            ("u200_out", "control_valve", "FCV-2001", "Anti-surge recycle", 900, 245, 90, 75),
        ),
        (("u200_in", "u200_c1", "e", "w"),
         ("u200_c1", "u200_cooler", "e", "w"),
         ("u200_cooler", "u200_out", "e", "w"),
         ("u200_out", "u200_in", "s", "s")),
        (("PIC-2001/PIC-2001", "Suction pressure"),
         ("SIC-2001/SIC-2001", "C1 speed"),
         ("UIC-2001/UIC-2001", "Anti-surge"),
         ("LIC-2001/LIC-2001", "V201 level")),
        (("Recycle flow", "UIC-2001/UIC-2001/PV"),
         ("Discharge pressure", "PIC-2002/PIC-2002/PV"),
         ("Discharge temperature", "TIC-2002/TIC-2002/PV")),
    ),
    UnitDisplaySpec(
        "U300", "U300 - L2 Charge Heating", "CHARGE HEATING",
        "Operate H1 inside fuel, air, draft, oxygen, flame, and reactor-inlet "
        "constraints; drill into L3 for pass and combustion diagnosis.", "AP-PID-U300",
        (
            ("u300_feed", "exchanger", "CHARGE", "From recycle compression", 75, 270, 115, 90),
            ("u300_h1", "furnace", "H1", "Charge heater", 360, 190, 180, 280),
            ("u300_fan", "fan", "ID-301", "Induced-draft fan", 665, 345, 100, 95),
            ("u300_stack", "stack", "STACK", "Flue gas", 835, 175, 82, 190),
            ("u300_r1", "reactor", "R1", "To reaction", 985, 240, 90, 155),
        ),
        (("u300_feed", "u300_h1", "e", "w"),
         ("u300_h1", "u300_r1", "e", "w"),
         ("u300_fan", "u300_h1", "w", "s"),
         ("u300_h1", "u300_stack", "n", "s")),
        (("TIC-3001/TIC-3001", "H1 outlet"),
         ("FIC-3001/FIC-3001", "Total fuel"),
         ("FIC-3003/FIC-3003", "Combustion air"),
         ("AIC-3001/AIC-3001", "Flue O2")),
        (("H1 outlet", "TIC-3001/TIC-3001/PV"),
         ("Draft pressure", "TIC-3001/PT-3001/PV"),
         ("Bridgewall temperature", "BMS-3001/TT-3005/PV")),
    ),
    UnitDisplaySpec(
        "U400", "U400 - L2 Reaction and Separation", "REACTION AND PRODUCT SEPARATION",
        "Control R1 thermal severity and quench, then stabilize E401/D3 pressure "
        "and liquid inventories before fractionation.", "AP-PID-U400",
        (
            ("u400_feed", "control_valve", "FCV-4001", "Reactor feed", 65, 280, 78, 65),
            ("u400_r1", "reactor", "R1", "Fixed-bed reactor", 265, 190, 120, 240),
            ("u400_e401", "exchanger", "E401", "Product cooler", 510, 260, 125, 100),
            ("u400_d3", "drum", "D3", "Product separator", 750, 250, 145, 110),
            ("u400_out", "control_valve", "LCV-4002", "Liquid to U500", 1000, 280, 80, 65),
        ),
        (("u400_feed", "u400_r1", "e", "w"),
         ("u400_r1", "u400_e401", "e", "w"),
         ("u400_e401", "u400_d3", "e", "w"),
         ("u400_d3", "u400_out", "e", "w")),
        (("TIC-4001/TIC-4001", "R1 temperature"),
         ("FIC-4001/FIC-4001", "Quench flow"),
         ("LIC-4001/LIC-4001", "D3 interface"),
         ("PIC-4001/PIC-4001", "D3 pressure")),
        (("R1 temperature", "TIC-4001/TIC-4001/PV"),
         ("R1 differential pressure", "TIC-4001/PDT-4001/PV"),
         ("D3 pressure", "PIC-4001/PIC-4001/PV")),
    ),
    UnitDisplaySpec(
        "U500", "U500 - L2 Primary Fractionation", "PRIMARY FRACTIONATION",
        "Maintain T1 pressure, reflux, bottoms inventory, reboiler duty, and "
        "overhead composition while routing stabilized feed to U600.", "AP-PID-U500/U500B",
        (
            ("u500_feed", "control_valve", "FEED", "From D3", 60, 310, 75, 60),
            ("u500_t1", "column", "T1", "Primary fractionator", 250, 170, 105, 300),
            ("u500_cond", "condenser", "E501", "Overhead condenser", 485, 175, 110, 85),
            ("u500_d501", "drum", "D501", "Reflux drum", 690, 185, 125, 90),
            ("u500_p501", "pump", "P501A/B", "Reflux pumps", 890, 215, 105, 90),
            ("u500_reb", "reboiler", "E502", "T1 reboiler", 480, 390, 115, 90),
        ),
        (("u500_feed", "u500_t1", "e", "w"),
         ("u500_t1", "u500_cond", "n", "w"),
         ("u500_cond", "u500_d501", "e", "w"),
         ("u500_d501", "u500_p501", "e", "w"),
         ("u500_reb", "u500_t1", "w", "s")),
        (("PIC-5001/PIC-5001", "T1 pressure"),
         ("LIC-5001/LIC-5001", "T1 bottoms"),
         ("LIC-5002/LIC-5002", "Reflux drum"),
         ("AIC-5001/AIC-5001", "Overhead quality")),
        (("T1 pressure", "PIC-5001/PIC-5001/PV"),
         ("Overhead quality", "AIC-5001/AIC-5001/PV"),
         ("Reboiler temperature", "TIC-5002/TIC-5002/PV")),
    ),
    UnitDisplaySpec(
        "U600", "U600 - L2 Product Fractionation", "PRODUCT FRACTIONATION",
        "Maintain T2 pressure, reflux and reboiler balance, inventories, and "
        "final overhead product quality.", "AP-PID-U600/U600B",
        (
            ("u600_feed", "control_valve", "FEED", "From T1", 60, 310, 75, 60),
            ("u600_t2", "column", "T2", "Product fractionator", 250, 170, 105, 300),
            ("u600_cond", "condenser", "E601", "Overhead condenser", 485, 175, 110, 85),
            ("u600_d601", "drum", "D601", "Reflux drum", 690, 185, 125, 90),
            ("u600_p601", "pump", "P601A/B", "Product/reflux pumps", 890, 215, 105, 90),
            ("u600_reb", "reboiler", "E602", "T2 reboiler", 480, 390, 115, 90),
        ),
        (("u600_feed", "u600_t2", "e", "w"),
         ("u600_t2", "u600_cond", "n", "w"),
         ("u600_cond", "u600_d601", "e", "w"),
         ("u600_d601", "u600_p601", "e", "w"),
         ("u600_reb", "u600_t2", "w", "s")),
        (("PIC-6001/PIC-6001", "T2 pressure"),
         ("LIC-6001/LIC-6001", "T2 bottoms"),
         ("LIC-6002/LIC-6002", "Reflux drum"),
         ("AIC-6001/AIC-6001", "Product quality")),
        (("T2 pressure", "PIC-6001/PIC-6001/PV"),
         ("Product quality", "AIC-6001/AIC-6001/PV"),
         ("Reboiler temperature", "TIC-6002/TIC-6002/PV")),
    ),
    UnitDisplaySpec(
        "U700", "U700 - L2 Steam Generation", "STEAM GENERATION AND DISTRIBUTION",
        "Balance deaerator/feedwater inventory, B1 firing, combustion air, drum "
        "level, and plant steam-header pressure.", "AP-PID-U700/U700B",
        (
            ("u700_da", "tank", "DA701", "Deaerator", 75, 230, 110, 170),
            ("u700_p701", "pump", "P701A/B", "Boiler-feedwater pumps", 290, 275, 110, 95),
            ("u700_b1", "heater", "B1", "Steam boiler", 520, 180, 180, 280),
            ("u700_fan", "fan", "FD-701", "Combustion-air fan", 790, 355, 95, 90),
            ("u700_stack", "stack", "STACK", "Flue gas", 930, 180, 80, 180),
        ),
        (("u700_da", "u700_p701", "e", "w"),
         ("u700_p701", "u700_b1", "e", "w"),
         ("u700_fan", "u700_b1", "w", "s"),
         ("u700_b1", "u700_stack", "n", "s")),
        (("LIC-7001/LIC-7001", "Drum level"),
         ("FIC-7001/FIC-7001", "Feedwater"),
         ("PIC-7001/PIC-7001", "Steam pressure"),
         ("AIC-7001/AIC-7001", "Flue O2")),
        (("Steam pressure", "PIC-7001/PIC-7001/PV"),
         ("Drum level", "LIC-7001/LIC-7001/PV"),
         ("Steam temperature", "TIC-7001/TIC-7001/PV")),
    ),
    UnitDisplaySpec(
        "U800", "U800 - L2 Effluent Treatment", "EFFLUENT NEUTRALIZATION",
        "Control NT801 pH and inventory, proportion acid/caustic addition, and "
        "verify compliant discharge to outfall.", "AP-PID-U800",
        (
            ("u800_in", "valve", "EFFLUENT", "Plant effluent", 65, 285, 70, 70),
            ("u800_nt", "dished_tank", "NT801", "Neutralization tank", 300, 190, 150, 245),
            ("u800_ag", "motor", "M801", "Agitator", 340, 125, 70, 60),
            ("u800_p801", "pump", "P801A/B", "Discharge pumps", 620, 270, 115, 100),
            ("u800_out", "control_valve", "OUTFALL", "Treated discharge", 900, 285, 85, 70),
        ),
        (("u800_in", "u800_nt", "e", "w"),
         ("u800_ag", "u800_nt", "s", "n"),
         ("u800_nt", "u800_p801", "e", "w"),
         ("u800_p801", "u800_out", "e", "w")),
        (("AIC-8001/AIC-8001", "Neutralization pH"),
         ("FIC-8001/FIC-8001", "Effluent flow"),
         ("LIC-8001/LIC-8001", "NT801 level")),
        (("Tank pH", "AIC-8001/AIC-8001/PV"),
         ("Discharge pH", "AIC-8001/AT-8003/PV"),
         ("Tank level", "LIC-8001/LIC-8001/PV")),
    ),
)
UNIT_L2 = {spec.unit: spec.name for spec in UNIT_SPECS}
DISPLAY_NAMES = (L1, L2, *(spec.name for spec in UNIT_SPECS), L3, L4)


def _unit_l2(spec: UnitDisplaySpec) -> PvmDisplay:
    child = L3 if spec.unit == "U300" else ""
    items = _header(
        2, f"{spec.unit} · {spec.title}", spec.purpose,
        parent=L1, child=child,
    )
    items.extend(_section(
        f"{spec.unit.lower()}_process",
        f"PROCESS ARRANGEMENT · {spec.reference}", 38, 116, 1100, 520,
    ))
    items.extend(_section(
        f"{spec.unit.lower()}_focus", "OPERATING ENVELOPE",
        1154, 116, 404, 520,
    ))
    vessel_symbols = {"tank", "vessel", "drum", "column", "dished_tank"}
    level_terms = ("level", "inventory", "interface", "bottoms", "drum")
    level_controls = iter(
        path for path, label in spec.loops
        if any(term in label.lower() for term in level_terms)
    )
    pvms = []
    embedded_controls = set()
    for ident, symbol, tag, label, x, y, w, h in spec.equipment:
        control = ""
        if symbol in vessel_symbols:
            control = next(level_controls, "")
        if control:
            embedded_controls.add(control)
            # One registered PVM owns the vessel shell, alarm-limit bar and
            # trend. Its placement preserves the intended equipment artwork,
            # and its PID identity opens the correct loop faceplate.
            pvms.append(_pvm(
                ident, "PID/dynamo_inline", control, x, y, w, h,
                variant="vessel", label=tag,
                choices={"equipment_symbol": symbol,
                         "show_hp_anatomy": False,
                         "show_readout": False},
            ))
        else:
            items.append(_symbol(
                ident, symbol, x, y, w, h, ports=_equipment_ports()))
        items.extend([
            _text(f"{ident}_tag", tag, x - 25, y - 27, w + 50, 22,
                  size=9, bold=True, align="center"),
            _text(f"{ident}_label", label, x - 30, y + h + 5, w + 60, 28,
                  size=8, colour=TEXT_DIM, align="center", wrap=True),
        ])
    port_name = {"w": "inlet", "e": "outlet",
                 "n": "top", "s": "bottom"}
    for index, (a, b, a_side, b_side) in enumerate(spec.pipes):
        items.append(_pipe(
            f"{spec.unit.lower()}_pipe_{index}", a, b,
            a_side=port_name.get(a_side, a_side),
            b_side=port_name.get(b_side, b_side), width=4,
        ))
    items.extend([
        _text(f"{spec.unit.lower()}_kpi_title", "LIVE CONSTRAINTS / KPIs",
              1170, 155, 372, 23, size=9, bold=True),
        _table(f"{spec.unit.lower()}_kpis", 1170, 182, 372, 142,
               [
                   {"key": "variable", "title": "Variable", "width": 1.8},
                   {"key": "value", "title": "Live", "width": 1},
               ],
               [{"variable": label, "value": _live(path)}
                for label, path in spec.kpis], row_height=34),
        _text(f"{spec.unit.lower()}_trend_title", "UNIT PROFILE",
              1170, 341, 372, 23, size=9, bold=True),
        _chart(f"{spec.unit.lower()}_trend", 1170, 368, 372, 145,
               list(spec.kpis), lo=0, hi=500),
        _text(f"{spec.unit.lower()}_alarm_title",
              f"ACTIVE {spec.loops[0][0].split('/', 1)[0]} ALARMS",
              1170, 530, 372, 23, size=9, bold=True),
        _alarm_list(f"{spec.unit.lower()}_alarms", 1170, 556, 372, 61,
                    prefix=spec.loops[0][0].split("/", 1)[0]),
        _rect(f"{spec.unit.lower()}_loop_panel", 38, 655, 1520, 207,
              line=LINE, radius=5),
        _text(f"{spec.unit.lower()}_loop_title", "PRIMARY OPERATING LOOPS",
              54, 666, 300, 23, size=10, bold=True, colour=ACTION),
    ])
    display_loops = tuple(
        row for row in spec.loops if row[0] not in embedded_controls)
    count = len(display_loops)
    gap = 14
    pvm_w = min(330, (1488 - gap * (count - 1)) / count)
    total_w = count * pvm_w + gap * (count - 1)
    x0 = 54 + (1488 - total_w) / 2
    for index, (path, label) in enumerate(display_loops):
        pvms.append(_pvm(
            f"{spec.unit.lower()}_loop_{index}", "PID/dynamo_inline", path,
            x0 + index * (pvm_w + gap), 720, pvm_w, 132,
            variant="hp", label=label,
        ))
    if spec.unit == "U300":
        items.append(_link(
            "u300_open_l3", L3, "Open H1 secondary operation",
            850, 662, 270, 32,
        ))
    return PvmDisplay(
        spec.name, pvms=pvms, items=items,
        description=(
            f"L2 primary-operation display for {spec.unit}, derived from "
            f"{spec.reference}. Native editable equipment with named process "
            "ports, smart pipes, registered live vessel PVMs, "
            "KPIs, alarms, and hierarchy navigation."
        ),
        background=BACKGROUND, width=WIDTH, height=HEIGHT, level=2, parent=L1,
    )


def _l1() -> PvmDisplay:
    items = _header(
        1, "AZEO PLANT · SITUATIONAL AWARENESS OVERVIEW",
        "In four seconds: is the plant safe, stable, and within its operating "
        "envelope? Only abnormal conditions, constraints, and decision KPIs "
        "belong on this display.", child=L2,
    )
    items.extend([
        _rect("l1_simulation_badge", 1015, 20, 132, 25,
              fill="#F4F6F7", line=LINE, radius=3),
        _text("l1_simulation_text", "SIMULATION", 1015, 20, 132, 25,
              size=8, bold=True, colour=TEXT_DIM, align="center"),
    ])
    units = (
        ("U010", "Fuel gas & utilities", "Fuel pressure / availability",
         "stack", "FIC-0101/FT-0102", "FIC-0101/FT-0102/PV", UNIT_L2["U010"]),
        ("U100", "Feed preparation", "D1 inventory / charge feed",
         "tank", "LIC-1001/LT-1001", "LIC-1001/LT-1001/PV", UNIT_L2["U100"]),
        ("U200", "Recycle compression", "C1 throughput / anti-surge",
         "compressor", "UIC-2001/FT-2001", "UIC-2001/FT-2001/PV", UNIT_L2["U200"]),
        ("U300", "Charge heating", "H1 outlet temperature",
         "furnace", "TIC-3001/TT-3001", "TIC-3001/TT-3001/PV", UNIT_L2["U300"]),
        ("U400", "Reaction & separation", "R1 temperature / D3 separation",
         "reactor", "TIC-4001/TT-4002", "TIC-4001/TT-4002/PV", UNIT_L2["U400"]),
        ("U500", "Primary fractionation", "T1 overhead composition",
         "column", "AIC-5001/AT-5001", "AIC-5001/AT-5001/PV", UNIT_L2["U500"]),
        ("U600", "Product fractionation", "T2 overhead composition",
         "column", "AIC-6001/AT-6001", "AIC-6001/AT-6001/PV", UNIT_L2["U600"]),
        ("U700", "Steam generation", "B1 steam header pressure",
         "heater", "PIC-7001/PT-7002", "PIC-7001/PT-7002/PV", UNIT_L2["U700"]),
        ("U800", "Effluent treatment", "NT801 discharge pH",
         "dished_tank", "AIC-8001/AT-8002", "AIC-8001/AT-8002/PV", UNIT_L2["U800"]),
        ("U900", "Safety instrumented system", "Plant ESD state",
         "actuated_valve", "ESD-9000/HS-9002", "ESD-9000/ESD-9000/TRIPPED", L4),
    )
    pvms: list[dict] = []
    card_w, card_h = 292, 280
    for index, (unit, title, focus, symbol, pvm_path, value_path,
                target) in enumerate(units):
        col, row = index % 5, index // 5
        x, y = 38 + col * 306, 116 + row * 294
        key = unit.lower()
        items.extend([
            _rect(f"{key}_card", x, y, card_w, card_h,
                  line=LINE, radius=5),
            _text(f"{key}_number", unit, x + 12, y + 8, 58, 25,
                  size=11, bold=True, colour=ACTION),
            _text(f"{key}_name", title.upper(), x + 72, y + 8,
                  card_w - 84, 25,
                  size=8 if unit == "U900" else 9.5, bold=True),
            _text(f"{key}_focus", focus, x + 12, y + 37,
                  card_w - 24, 25, size=8.5, colour=TEXT_DIM),
            _symbol(f"{key}_equipment", symbol, x + 16, y + 77, 78, 92),
            _text(f"{key}_pv_label", "DECISION KPI", x + 108, y + 76,
                  152, 20, size=7.5, bold=True, colour=TEXT_DIM),
            _datalink(f"{key}_pv", value_path, x + 108, y + 96,
                      152, 34, decimals=1, units=(unit != "U900"),
                      dtype="numeric",
                      true_text="TRIPPED" if unit == "U900" else None,
                      false_text="HEALTHY" if unit == "U900" else None),
            # The PVM below owns quality/alarm state.  A permanently teal
            # "NORMAL" stripe contradicted its Bad-I/O icon on the live U500
            # and U600 analysers, which is more dangerous than omitting state.
            _text(f"{key}_state", "LIVE KPI / STATUS",
                  x + 108, y + 151, 152, 16, size=7, colour=TEXT_DIM),
            _link(f"{key}_open", target,
                  "Open safety support" if unit == "U900"
                  else "Open unit operation",
                  x + 12, y + 239, card_w - 24, 32),
        ])
        block = pvm_path.rsplit("/", 1)[-1]
        block_type = "DI" if unit == "U900" else "AI"
        pvms.append(_pvm(
            f"{key}_pvm", f"{block_type}/dynamo_compact", pvm_path,
            x + 12, y + 181, card_w - 24, 52, label=block,
        ))
    items.extend([
        _rect("l1_footer", 38, 714, 1520, 148,
              line=LINE, radius=5),
        _text("l1_footer_title",
              "SITE KPI PROFILE · FEED / HEATER / REACTOR / STEAM",
              54, 724, 630, 25, size=10, bold=True),
        _chart("l1_profile", 54, 750, 650, 94,
               [("Charge feed", "LIC-1001/LT-1001/PV"),
                ("Heater outlet", "TIC-3001/TT-3001/PV"),
                ("Reactor temperature", "TIC-4001/TT-4002/PV"),
                ("Steam header", "PIC-7001/PT-7002/PV")],
               lo=0, hi=600),
        _text("l1_summary_title", "DOMAIN RESPONSE ROUTING",
              730, 724, 300, 25, size=10, bold=True),
        _table("l1_summary", 730, 750, 590, 94,
               [
                   {"key": "domain", "title": "Domain", "width": 1.6},
                   {"key": "question", "title": "Decision", "width": 1.3},
                   {"key": "owner", "title": "Next action", "width": 1.5},
               ],
               [
                   {"domain": "Process train", "question": "Alarm / constraint?",
                    "owner": "OPEN AFFECTED L2"},
                   {"domain": "Utilities", "question": "Fuel / steam?",
                    "owner": "U010 / U700"},
                   {"domain": "Safety systems", "question": "Trip / permit?",
                    "owner": "OPEN L4 SUPPORT"},
               ], row_height=23),
    ])
    pvms.append(_pvm(
        # AREA's alarm roll-up is provider-wide; an existing module path keeps
        # the placement verifiable without inventing a synthetic AREA module.
        "plant_alarm_summary", "AREA/dynamo_inline", "ESD-9000",
        1340, 788, 202, 54, variant="hp_alarms", label="PLANT ALARMS",
    ))
    return PvmDisplay(
        L1, pvms=pvms, items=items,
        description=(
            "L1 plant-wide situational-awareness display derived from P&IDs "
            "AP-PID-U010 through AP-PID-U900. Native editable objects; no "
            "flattened reference images."
        ),
        background=BACKGROUND, width=WIDTH, height=HEIGHT, level=1,
    )


def _l2() -> PvmDisplay:
    items = _header(
        2, "AZEO PLANT · PRIMARY OPERATION",
        "Daily operating display for the complete hydrocarbon train. The main "
        "material path is read left-to-right; fuel, steam, effluent, and SIS "
        "relationships remain visible without duplicating detailed P&IDs.",
        parent=L1,
    )
    items.extend(_section("l2_train", "MAIN PROCESS TRAIN", 38, 116, 1190, 560))
    items.extend(_section("l2_sidebar", "OPERATING FOCUS", 1244, 116, 314, 560))
    equipment = (
        ("d1", "tank", "U100", "D1", "Feed surge", 60, 250, 92, 135),
        ("c1", "compressor", "U200", "C1", "Recycle compressor", 225, 276, 100, 82),
        ("h1", "furnace", "U300", "H1", "Charge heater", 390, 230, 100, 165),
        ("r1", "reactor", "U400", "R1", "Reactor", 555, 230, 95, 165),
        ("d3", "drum", "U400", "D3", "Product separator", 702, 272, 110, 85),
        ("t1", "column", "U500", "T1", "Primary column", 864, 216, 72, 190),
        ("t2", "column", "U600", "T2", "Product column", 1018, 216, 72, 190),
        ("nt801", "dished_tank", "U800", "NT801", "Neutralization", 1133, 268, 72, 100),
    )
    for ident, symbol, unit, tag, label, x, y, w, h in equipment:
        items.extend([
            _symbol(ident, symbol, x, y, w, h),
            _text(f"{ident}_unit", unit, x - 3, y - 39, w + 6, 18,
                  size=8, bold=True, colour=ACTION, align="center"),
            _text(f"{ident}_tag", tag, x - 3, y - 22, w + 6, 20,
                  size=10, bold=True, align="center"),
            _text(f"{ident}_label", label, x - 25, y + h + 5, w + 50, 26,
                  size=7.5, colour=TEXT_DIM, align="center", wrap=True),
        ])
    for index, (a, b) in enumerate(zip(
            ("d1", "c1", "h1", "r1", "d3", "t1", "t2"),
            ("c1", "h1", "r1", "d3", "t1", "t2", "nt801"))):
        items.append(_pipe(f"l2_process_{index}", a, b, width=5))
    items.extend([
        _symbol("l2_fuel", "stack", 350, 130, 55, 65),
        _text("l2_fuel_label", "U010 FUEL GAS", 292, 135, 130, 20,
              size=8, bold=True, colour=ACTION, align="center"),
        _pipe("l2_fuel_to_h1", "l2_fuel", "h1", a_side="s", b_side="n",
              width=3, colour="#7B6F61"),
        _symbol("l2_boiler", "heater", 900, 125, 70, 75),
        _text("l2_boiler_label", "U700 STEAM", 870, 135, 130, 20,
              size=8, bold=True, colour=ACTION, align="center"),
        _pipe("l2_steam_to_t1", "l2_boiler", "t1", a_side="s", b_side="n",
              width=3, colour="#748998"),
        _pipe("l2_steam_to_t2", "l2_boiler", "t2", a_side="e", b_side="n",
              width=3, colour="#748998"),
        _rect("l2_sis_band", 58, 432, 1147, 34,
              fill="#DCE3E7", line="#B7C1C8", radius=3),
        _text("l2_sis_text",
              "U900 SIS · trip demand, first-out, and final-element status are "
              "diagnosed on L4; this band remains quiet while all permits are healthy.",
              70, 437, 960, 24, size=8.5, colour=TEXT_DIM),
        _link("l2_sis_link", L4, "Open safety support", 1038, 433, 155, 31),
    ])
    loops = (
        ("l2_lic1001", "LIC-1001/LIC-1001", "D1 level"),
        ("l2_uic2001", "UIC-2001/UIC-2001", "C1 anti-surge"),
        ("l2_tic3001", "TIC-3001/TIC-3001", "H1 outlet"),
        ("l2_tic4001", "TIC-4001/TIC-4001", "R1 temperature"),
        ("l2_aic5001", "AIC-5001/AIC-5001", "T1 composition"),
        ("l2_aic6001", "AIC-6001/AIC-6001", "T2 composition"),
        ("l2_pic7001", "PIC-7001/PIC-7001", "Steam pressure"),
        ("l2_aic8001", "AIC-8001/AIC-8001", "Effluent pH"),
    )
    pvms = []
    for index, (ident, path, label) in enumerate(loops):
        col, row = index % 4, index // 4
        x, y = 58 + col * 286, 480 + row * 90
        pvms.append(_pvm(
            ident, "PID/dynamo_inline", path, x, y, 266, 70,
            variant="hp", label=label,
        ))
    items.extend([
        _text("l2_alarm_title", "ACTIVE PLANT ALARMS", 1260, 157, 282, 24,
              size=9, bold=True),
        _alarm_list("l2_alarms", 1260, 184, 282, 215),
        _text("l2_target_title", "SHIFT TARGETS", 1260, 416, 282, 24,
              size=9, bold=True),
        _table("l2_targets", 1260, 444, 282, 202,
               [
                   {"key": "item", "title": "Variable", "width": 1.6},
                   {"key": "value", "title": "Live", "width": 1},
               ],
               [
                   {"item": "Charge heater outlet",
                    "value": _live("TIC-3001/TIC-3001/PV")},
                   {"item": "Reactor temperature",
                    "value": _live("TIC-4001/TIC-4001/PV")},
                   {"item": "T1 overhead quality",
                    "value": _live("AIC-5001/AIC-5001/PV")},
                   {"item": "T2 overhead quality",
                    "value": _live("AIC-6001/AIC-6001/PV")},
                   {"item": "Effluent pH",
                    "value": _live("AIC-8001/AIC-8001/PV")},
               ]),
        _rect("l2_footer", 38, 695, 1520, 167,
              line=LINE, radius=5),
        _text("l2_footer_title", "PRIMARY OPERATING INTENT", 54, 708,
              270, 25, size=10, bold=True, colour=ACTION),
        _text("l2_footer_text",
              "Maintain stable feed, protect compressor surge margin, hold H1/R1 "
              "thermal severity, meet both fractionator quality targets, and keep "
              "steam and effluent inside constraints. Drill into L3 only for heater "
              "diagnosis or non-routine work.",
              54, 738, 750, 78, size=9, colour=TEXT_DIM,
              wrap=True, valign="top"),
        _chart("l2_profile", 835, 710, 450, 132,
               [("H1", "TIC-3001/TIC-3001/PV"),
                ("R1", "TIC-4001/TIC-4001/PV")], lo=0, hi=500),
        _link("l2_open_h1", UNIT_L2["U300"], "Open U300 charge heating",
              1310, 752, 215, 40),
    ])
    return PvmDisplay(
        L2, pvms=pvms, items=items,
        description=(
            "L2 cross-unit coordination display derived from AP-PID-U010 "
            "through AP-PID-U800, with U900 SIS context. Unit-specific routine "
            "operation is handled by the sibling L2 displays."
        ),
        background=BACKGROUND, width=WIDTH, height=HEIGHT, level=2, parent=L1,
    )


def _l3() -> PvmDisplay:
    items = _header(
        3, "U300 · CHARGE HEATER H1 SECONDARY OPERATION",
        "Detailed process relationships for non-routine operation and fault "
        "isolation: charge passes, fuel split, combustion air, draft, stack, "
        "and the reactor inlet constraint.", parent=UNIT_L2["U300"], child=L4,
    )
    items.extend(_section("l3_process", "H1 PROCESS / COMBUSTION", 38, 116, 1040, 590))
    items.extend(_section("l3_right", "HEATER CONDITION", 1094, 116, 464, 590))
    items.extend([
        _symbol("l3_feed", "exchanger", 72, 279, 110, 88),
        _text("l3_feed_tag", "FROM U200 · CHARGE", 48, 245, 160, 24,
              size=8, bold=True, colour=ACTION, align="center"),
        _symbol("l3_fcv3001", "control_valve", 244, 291, 66, 55),
        _text("l3_fcv3001_tag", "FCV-3001", 225, 350, 102, 21,
              size=8, bold=True, align="center"),
        _symbol("l3_h1", "furnace", 420, 188, 220, 330),
        _text("l3_h1_tag", "H1", 482, 155, 96, 30,
              size=14, bold=True, align="center"),
        _text("l3_h1_desc", "CHARGE HEATER", 450, 522, 160, 23,
              size=9, bold=True, align="center"),
        _symbol("l3_fcv3002", "control_valve", 324, 579, 62, 50),
        _symbol("l3_fcv3003", "control_valve", 430, 579, 62, 50),
        _text("l3_fuel_gas", "FUEL GAS · FCV-3002", 272, 635, 160, 21,
              size=8, bold=True, align="center"),
        _text("l3_fuel_oil", "FUEL OIL · FCV-3003", 405, 635, 160, 21,
              size=8, bold=True, align="center"),
        _symbol("l3_fcv3004", "control_valve", 674, 579, 62, 50),
        _symbol("l3_id301", "fan", 793, 563, 74, 72),
        _text("l3_air", "COMBUSTION AIR", 650, 635, 130, 21,
              size=8, bold=True, align="center"),
        _text("l3_id301_tag", "ID-301", 790, 635, 82, 21,
              size=8, bold=True, align="center"),
        _symbol("l3_stack", "stack", 891, 153, 80, 175),
        _text("l3_stack_tag", "STACK", 885, 330, 92, 21,
              size=8, bold=True, align="center"),
        _symbol("l3_to_r1", "reactor", 891, 419, 84, 125),
        _text("l3_to_r1_tag", "TO U400 · R1", 861, 548, 145, 22,
              size=8, bold=True, colour=ACTION, align="center"),
        _pipe("l3_charge_in", "l3_feed", "l3_fcv3001", width=5),
        _pipe("l3_charge_h1", "l3_fcv3001", "l3_h1", width=5),
        _pipe("l3_h1_out", "l3_h1", "l3_to_r1", width=5),
        _pipe("l3_fg", "l3_fcv3002", "l3_h1", a_side="n", b_side="s",
              width=3, colour="#786B5F"),
        _pipe("l3_fo", "l3_fcv3003", "l3_h1", a_side="n", b_side="s",
              width=3, colour="#786B5F"),
        _pipe("l3_air_run", "l3_fcv3004", "l3_id301", width=3,
              colour="#6D8290"),
        _pipe("l3_air_h1", "l3_id301", "l3_h1", a_side="w", b_side="s",
              width=3, colour="#6D8290"),
        _pipe("l3_flue", "l3_h1", "l3_stack", a_side="n", b_side="s",
              width=4, colour="#6D7478"),
        _text("l3_passes", "FOUR PARALLEL CHARGE PASSES", 433, 277, 194, 22,
              size=8, bold=True, align="center"),
        _line("l3_pass1", 457, 307, 146, colour="#768996", width=3),
        _line("l3_pass2", 457, 330, 146, colour="#768996", width=3),
        _line("l3_pass3", 457, 353, 146, colour="#768996", width=3),
        _line("l3_pass4", 457, 376, 146, colour="#768996", width=3),
    ])
    pvms = [
        _pvm("l3_tic3001", "PID/dynamo_inline", "TIC-3001/TIC-3001",
             61, 158, 280, 84, variant="hp", label="H1 OUTLET TEMP"),
        _pvm("l3_fic3001", "PID/dynamo_inline", "FIC-3001/FIC-3001",
             54, 784, 348, 70, variant="hp", label="TOTAL FUEL"),
        _pvm("l3_pic3001", "PID/dynamo_inline", "PIC-3001/PIC-3001",
             422, 784, 348, 70, variant="hp", label="FUEL HEADER"),
        _pvm("l3_fic3003", "PID/dynamo_inline", "FIC-3003/FIC-3003",
             790, 784, 348, 70, variant="hp", label="COMBUSTION AIR"),
        _pvm("l3_aic3001", "PID/dynamo_inline", "AIC-3001/AIC-3001",
             1158, 784, 348, 70, variant="hp", label="FLUE O2 TRIM"),
        _pvm("l3_id301_pvm", "DEVCTL/dynamo_compact", "MC-ID301/MC-ID301",
             190, 455, 190, 62, variant="fan", label="ID-301"),
    ]
    items.extend([
        _text("l3_trend_title", "THERMAL PROFILE", 1110, 155, 432, 23,
              size=9, bold=True),
        _chart("l3_trend", 1110, 181, 432, 190,
               [("H1 outlet", "TIC-3001/TIC-3001/PV"),
                ("H1 setpoint", "TIC-3001/TIC-3001/SP_WRK"),
                ("Bridgewall", "BMS-3001/TT-3005/PV")], lo=0, hi=600),
        _text("l3_alarm_title", "ACTIVE U300 ALARMS", 1110, 388, 432, 23,
              size=9, bold=True),
        _alarm_list("l3_alarms", 1110, 414, 432, 150, prefix="TIC-3001"),
        _table("l3_constraints", 1110, 578, 432, 107,
               [
                   {"key": "condition", "title": "Constraint", "width": 1.8},
                   {"key": "value", "title": "Live", "width": 1},
               ],
               [
                   {"condition": "Draft pressure",
                    "value": _live("TIC-3001/PT-3001/PV")},
                   {"condition": "Flue O2",
                    "value": _live("AIC-3001/AIC-3001/PV")},
                    {"condition": "Burner flame",
                     "value": _live("BMS-3001/BS-3001/OUT", units=False,
                                    dtype="boolean", true_text="PROVEN",
                                    false_text="NO FLAME")},
               ]),
        _rect("l3_footer", 38, 725, 1520, 137,
              line=LINE, radius=5),
        _text("l3_footer_title", "PRIMARY HEATER LOOPS", 54, 728,
              300, 25, size=10, bold=True, colour=ACTION),
        _text("l3_footer_note",
              "For a lost permit, trip, or failed restart, use L4 first-out diagnostics.",
              850, 728, 655, 25, size=8.5, colour=TEXT_DIM, align="right"),
    ])
    return PvmDisplay(
        L3, pvms=pvms, items=items,
        description=(
            "L3 charge-heater secondary-operation display derived from "
            "AP-PID-U300. Native process symbols, smart piping, live loop PVMs, "
            "condition table, alarm scope, and thermal trend."
        ),
        background=BACKGROUND, width=WIDTH, height=HEIGHT, level=3,
        parent=UNIT_L2["U300"],
    )


def _l4() -> PvmDisplay:
    items = _header(
        4, "U300 / U900 · H1 BMS AND SIS SUPPORT",
        "Task and diagnostic display for heater permissives, first-out trip "
        "analysis, reset readiness, and final-element verification. This is a "
        "support surface, not a second process-control display.", parent=L3,
    )
    items.extend(_section("l4_causes", "TRIP CAUSES / PERMISSIVES", 38, 116, 510, 535))
    items.extend(_section("l4_sequence", "BURNER MANAGEMENT STATE", 564, 116, 475, 535))
    items.extend(_section("l4_response", "FINAL ELEMENTS / ALARMS", 1055, 116, 503, 535))
    cause_rows = [
        ("Emergency shutdown", "BMS-3001/HS-9002/OUT"),
        ("Local emergency stop", "BMS-3001/HS-9001/OUT"),
        ("Gas detected", "BMS-3001/GD-9001/OUT"),
        ("Fire detected", "BMS-3001/FD-9001/OUT"),
        ("Fuel-gas pressure low-low", "BMS-3001/PSLL-0101/OUT"),
        ("Feed drum level low-low", "BMS-3001/LSLL-1001/OUT"),
        ("Fuel pressure low-low", "BMS-3001/PSLL-3001/OUT"),
        ("Reactor temperature high-high", "BMS-3001/TSHH-4001/OUT"),
    ]
    items.append(_table(
        "l4_cause_table", 54, 154, 478, 260,
        [
            {"key": "cause", "title": "Cause / permit", "width": 2.5},
            {"key": "state", "title": "Live", "width": 1},
        ],
        [{"cause": label,
          "state": _live(path, units=False, dtype="boolean",
                         true_text="ACTIVE", false_text="CLEAR")}
         for label, path in cause_rows],
        row_height=28,
    ))
    items.extend([
        _text("l4_first_out_title", "FIRST-OUT / LATCH STATUS", 54, 432,
              478, 23, size=9, bold=True),
        _table("l4_first_out", 54, 459, 478, 170,
               [
                   {"key": "parameter", "title": "Parameter", "width": 2},
                   {"key": "state", "title": "State", "width": 1},
               ],
               [
                   {"parameter": "BMS trip latched",
                    "state": _live("BMS-3001/BMS-3001/TRIPPED",
                                   units=False, dtype="boolean")},
                    {"parameter": "BMS start permit",
                     "state": _live("BMS-3001/BMS-3001/PERMIT",
                                    units=False, dtype="boolean",
                                    true_text="GRANTED",
                                    false_text="BLOCKED")},
                   {"parameter": "Plant ESD tripped",
                    "state": _live("ESD-9000/ESD-9000/TRIPPED",
                                   units=False, dtype="boolean")},
                    {"parameter": "Plant ESD ready",
                     "state": _live("ESD-9000/ESD-9000/READY",
                                    units=False, dtype="boolean",
                                    true_text="READY",
                                    false_text="NOT READY")},
               ], row_height=31),
    ])
    items.extend([
        _text("l4_state_label", "SEQUENCE STATE", 580, 155, 205, 21,
              size=8, bold=True, colour=TEXT_DIM),
        _datalink("l4_state", "BMS-3001/BMS-3001/STATE",
                  580, 178, 205, 42, decimals=0, units=False),
        _text("l4_timer_label", "STEP TIMER", 802, 155, 205, 21,
              size=8, bold=True, colour=TEXT_DIM),
        _datalink("l4_timer", "BMS-3001/BMS-3001/TIMER",
                  802, 178, 205, 42, decimals=1),
        _rect("l4_sequence_flow", 580, 239, 443, 152,
              fill=FIELD, line=LINE, radius=4),
    ])
    states = ("IDLE", "PURGE", "PILOT", "MAIN FLAME", "RUN")
    for index, state in enumerate(states):
        x = 594 + index * 84
        items.extend([
            _rect(f"l4_state_{index}", x, 282, 70, 52,
                  fill="#D7DEE3", line=LINE, radius=3),
            _text(f"l4_state_{index}_label", state, x, 282, 70, 52,
                  size=7.5, bold=True, align="center", wrap=True),
        ])
        if index < len(states) - 1:
            items.append(_line(f"l4_state_arrow_{index}", x + 70, 307,
                               14, colour=PIPE, width=2,
                               end_arrow="filled_arrow"))
    items.extend([
        _text("l4_permit_title", "LIVE SEQUENCE CONDITIONS", 580, 411,
              443, 23, size=9, bold=True),
        _table("l4_permits", 580, 438, 443, 191,
               [
                   {"key": "condition", "title": "Condition", "width": 2},
                   {"key": "state", "title": "Live", "width": 1},
               ],
               [
                    {"condition": "Draft proven",
                     "state": _live("BMS-3001/BMS-3001/DRAFT_OK",
                                    units=False, dtype="boolean",
                                    true_text="PROVEN",
                                    false_text="NOT PROVEN")},
                    {"condition": "Pilot flame",
                     "state": _live("BMS-3001/BMS-3001/PILOT_FLAME",
                                    units=False, dtype="boolean",
                                    true_text="PROVEN",
                                    false_text="NO FLAME")},
                    {"condition": "Main flame",
                     "state": _live("BMS-3001/BMS-3001/MAIN_FLAME",
                                    units=False, dtype="boolean",
                                    true_text="PROVEN",
                                    false_text="NO FLAME")},
                    {"condition": "ID fan running",
                     "state": _live("BMS-3001/XS-ID301-RUN/OUT",
                                    units=False, dtype="boolean",
                                    true_text="RUNNING",
                                    false_text="STOPPED")},
                   {"condition": "Bridgewall temperature",
                    "state": _live("BMS-3001/TT-3005/PV")},
               ], row_height=31),
    ])
    output_rows = (
        ("Combustion blower command", "BMS-3001/BMS-3001/BLOWER_CMD"),
        ("Igniter command", "BMS-3001/BMS-3001/IGNITER_CMD"),
        ("Pilot fuel command", "BMS-3001/BMS-3001/PILOT_FUEL_CMD"),
        ("Main fuel command", "BMS-3001/BMS-3001/MAIN_FUEL_CMD"),
        ("Main fuel shutoff XY-3010", "BMS-3001/XY-3010/OUT_D"),
        ("Main fuel shutoff XY-3011", "BMS-3001/XY-3011/OUT_D"),
    )
    items.extend([
        _table("l4_outputs", 1071, 154, 471, 215,
               [
                   {"key": "element", "title": "Final element", "width": 2.5},
                   {"key": "state", "title": "Command", "width": 1},
               ],
                [{"element": label,
                  "state": _live(path, units=False, dtype="boolean",
                                 true_text="ON", false_text="OFF")}
                for label, path in output_rows], row_height=30),
        _text("l4_alarm_title", "ACTIVE BMS / SIS ALARMS", 1071, 388,
              471, 23, size=9, bold=True),
        _alarm_list("l4_bms_alarms", 1071, 414, 471, 100,
                    prefix="BMS-3001"),
        _alarm_list("l4_esd_alarms", 1071, 521, 471, 108,
                    prefix="ESD-9000"),
        _rect("l4_footer", 38, 671, 1520, 191,
              line=LINE, radius=5),
        _text("l4_footer_title", "DIAGNOSTIC EVIDENCE", 54, 684,
              280, 24, size=10, bold=True, colour=ACTION),
        _chart("l4_temperature", 54, 712, 520, 131,
               [("Bridgewall", "BMS-3001/TT-3005/PV"),
                ("H1 outlet", "TIC-3001/TIC-3001/PV")], lo=0, hi=700),
        _text("l4_reset_title", "RESET READINESS", 604, 698, 270, 23,
              size=9, bold=True),
        _text("l4_reset_rule",
              "A reset is appropriate only after the first-out cause is "
              "understood, every field condition is healthy, final elements "
              "are verified, and operating procedure authorization is complete.",
              604, 727, 510, 62, size=9, colour=TEXT_DIM,
              wrap=True, valign="top"),
        _link("l4_return", L3, "Return to H1 secondary operation",
              1220, 742, 300, 42),
    ])
    pvms = [
        _pvm("l4_esd_switch", "DI/dynamo_compact", "ESD-9000/HS-9002",
             604, 814, 180, 38, label="PLANT ESD"),
        _pvm("l4_flame", "DI/dynamo_compact", "BMS-3001/BS-3001",
             797, 814, 180, 38, label="FLAME"),
        _pvm("l4_fan", "DI/dynamo_compact", "BMS-3001/XS-ID301-RUN",
             990, 814, 180, 38, label="ID FAN"),
    ]
    return PvmDisplay(
        L4, pvms=pvms, items=items,
        description=(
            "L4 heater BMS/SIS support display derived from AP-PID-U300 and "
            "AP-PID-U900. Live cause/permissive and final-element tables, scoped "
            "alarms, sequence state, and diagnostic trends."
        ),
        background=BACKGROUND, width=WIDTH, height=HEIGHT, level=4, parent=L3,
    )


def build_displays() -> tuple[PvmDisplay, ...]:
    """Return one L1, the L2 coordination/unit set, and the H1 L3/L4 path."""
    displays = (_l1(), _l2(), *(_unit_l2(spec) for spec in UNIT_SPECS),
                _l3(), _l4())
    # Generated documents are new authoring artifacts, never legacy inputs.
    # Declare the schema up front so build -> save -> load is byte-stable.
    for display in displays:
        display.schema_version = CURRENT_DISPLAY_SCHEMA_VERSION
        _theme_example(display)
        _clear_example_nozzles(display)
        if display.name == UNIT_L2["U300"]:
            _layout_charge_heating(display)
    return displays


def _theme_example(display: PvmDisplay) -> None:
    """Theme the complete generated hierarchy, preserving process bindings.

    This map applies only to this generator's known furniture. Arbitrary
    imported artwork and unrelated project displays must not be recolored.
    """
    display.background = ""
    roles = {
        TEXT: "TEXT", TEXT_DIM: "TEXT_DIM", LINE: "LINE_SOFT",
        PIPE: "EQUIPMENT", ACTION: "HEADING", "#FFFFFF": "TEXT",
        BACKGROUND: "SURFACE_BG", PANEL: "SURFACE_PANEL", FIELD: "SURFACE_FIELD",
        "#F4F6F7": "SURFACE_PANEL", "#DCE3E7": "SURFACE_PANEL",
        "#B7C1C8": "LINE_SOFT", "#D7DEE3": "EQUIPMENT_FILL",
        "#7B6F61": "EQUIPMENT", "#748998": "EQUIPMENT",
        "#786B5F": "EQUIPMENT", "#6D8290": "EQUIPMENT",
        "#6D7478": "EQUIPMENT", "#768996": "EQUIPMENT",
    }
    for item in display.items:
        for field, role_field in (("text_color", "text_role"),
                                  ("line", "line_role"), ("fill", "fill_role")):
            literal = item.get(field)
            if literal in roles:
                item[role_field] = roles[literal]
                del item[field]
        if item["kind"] == "symbol":
            item.update(line_role="EQUIPMENT", fill_role="EQUIPMENT_FILL")
        if item["kind"] in ("rect", "round_rect"):
            # Generated rectangles are section/card furniture, not equipment.
            # Their filled bounding boxes must not trap the process nozzles.
            item["routing_obstacle"] = False
        if item["id"] == f"l{display.level}_badge":
            item.update(fill_role="SURFACE_PANEL", line_role="LINE")
        if item["id"].endswith("_loop_panel"):
            item["h"] = 237
    if display.name in UNIT_L2.values():
        for pvm in display.pvms:
            if pvm.get("variant") == "hp":
                pvm["h"] = 164


def _layout_charge_heating(display: PvmDisplay) -> None:
    for item in display.items:
        # These labels sat directly on the heater top and stack bottom exits.
        # Keep them legible without disabling collision checks on real labels.
        if item["id"] == "u300_h1_tag":
            item.update(x=365, w=60)
        if item["id"] == "u300_stack_label":
            item.update(x=835, w=82, y=435)
        if item["id"] == "u300_h1_label":
            item.update(x=295, w=120)
        if item["id"] == "u300_r1_label":
            item.update(x=1000, w=100, y=435)
        if item["id"] == "u300_fan_tag":
            item.update(x=650, w=115)
        routes = {
            "u300_pipe_1": [[620, 330], [620, 530], [960, 530], [960, 317.5]],
            "u300_pipe_2": [[575, 393.842065], [575, 490], [450, 490]],
            "u300_pipe_3": [[450, 175], [790, 175], [790, 415], [876, 415]],
        }
        if item["id"] in routes:
            # Keep the pipes outside both connected symbols, with visible
            # approaches to their authored inlet/bottom nozzles.
            item.update(route_mode="manual", route_points=routes[item["id"]])


def _clear_example_nozzles(display: PvmDisplay) -> None:
    """Keep identification text off process exits and route return legs outside equipment."""
    labels = {
        "u200_out_label": dict(x=996, y=325, w=125, h=42),
        "u200_in_label": dict(x=52, y=340, w=90, h=42),
        "u700_stack_label": dict(x=995, y=395, w=125),
        "u700_b1_label": dict(x=520, w=180),
        "u800_ag_tag": dict(x=265, y=142, w=60),
        "u800_ag_label": dict(x=440, y=155, w=110),
        "u800_nt_tag": dict(x=225, y=228, w=75),
        "l3_h1_tag": dict(x=425, y=170, w=65),
        "l3_stack_tag": dict(x=980, y=327, w=70),
        # Keep the C1-to-H1 west-nozzle corridor clear. The former y=264 tag
        # inflated across the only short route and made the shipped L2 pipe
        # report a blocked automatic path.
        "h1_unit": dict(x=500, y=191, w=58),
        "h1_tag": dict(x=500, y=210, w=58),
        "t1_unit": dict(x=790, y=188, w=60),
        "t1_tag": dict(x=790, y=207, w=60),
        "t2_unit": dict(x=960, y=210, w=50),
        "t2_tag": dict(x=960, y=230, w=50),
        "l2_boiler_label": dict(x=1030, y=135, w=155),
        "l2_fuel_label": dict(x=230, y=145, w=110),
    }
    routes = {
        "u200_pipe_3": [[932.79411, 500], [160, 500]],
        "u700_pipe_3": [[610.97092, 200], [740, 200], [740, 500], [970, 500]],
        "l3_flue": [[530, 170], [825, 170], [825, 385], [931, 385]],
    }
    for unit, column in (("u500", "t1"), ("u600", "t2")):
        labels.update({
            f"{unit}_{column}_tag": dict(x=190, y=167, w=80),
            f"{unit}_{column}_label": dict(x=125, y=465, w=120),
            f"{unit}_cond_tag": dict(x=530, y=145, w=60),
        })
        routes[f"{unit}_pipe_4"] = [[430, 439.99689], [430, 545], [302.622094, 545]]
    for item in display.items:
        ident = item["id"]
        if ident in labels:
            item.update(labels[ident])
        if ident in routes:
            item.update(route_mode="manual", route_points=routes[ident])


def operator_layout() -> Layout:
    """A Azeo-style 16:9 single-monitor surface for this hierarchy.

    ``LiveStation`` already owns the alarm banner, menu, navigation and status
    strips as fixed console chrome.  The authored layout therefore describes
    the remaining display surface: one 1920x1080-equivalent dynamic frame.
    Keeping that frame 16:9 matches the 1600x900 display documents exactly,
    so runtime scaling letterboxes neither axis and never distorts a symbol.

    L4 belongs in Main too.  The generic fallback layout's narrow Side frame
    is useful for contextual tools, but routing a full diagnostic page into
    22 percent of the monitor made the safety display unusable.
    """
    layout = Layout(LAYOUT)
    screen = layout.add_screen(Screen("Screen 1", width=1920, height=1080))
    screen.add_frame(DisplayFrame(
        "Main", rect=(0.0, 0.0, 1.0, 1.0), kind=DYNAMIC,
        levels=(1, 2, 3, 4), navigation_bar=True, coordinates=True,
    ))
    return layout


def install(root: Path = DISPLAY_ROOT, *, names: tuple[str, ...] = ()) -> tuple[Path, ...]:
    """Write editable drafts plus their display set and operator layout."""
    root = Path(root)
    store = DisplayStore(root)
    paths = tuple(store.save_draft(display) for display in build_displays()
                  if not names or display.name in names)
    if names:
        return paths

    display_set = DisplaySet(DISPLAY_SET)
    one = display_set.add_root(L1)
    display_set.add_child(one, L2)
    unit_nodes = {
        spec.unit: display_set.add_child(one, spec.name)
        for spec in UNIT_SPECS
    }
    three = display_set.add_child(unit_nodes["U300"], L3)
    display_set.add_child(three, L4)

    layouts = LayoutStore(root)
    layouts.save_display_set(display_set)
    layouts.save_layout(operator_layout())
    return paths


def publish_operator(root: Path = DISPLAY_ROOT, *, names: tuple[str, ...] = ()) -> tuple[dict, ...]:
    """Release changed drafts to PROD and assign them to the operator seat.

    Publishing is idempotent: regeneration does not create an empty revision
    merely because the command was run twice.  A station that already holds
    an older revision retains it until Refresh, preserving the deployment
    rule that a publish never redraws an operator's active screen.
    """
    root = Path(root)
    store = DisplayStore(root)
    released = []
    for display in build_displays():
        if names and display.name not in names:
            continue
        if store.published_document(
                display.name, workstation=CONSOLE_ID) == display.to_dict():
            continue
        released.append(store.publish(
            display, env="PROD", workstations=[CONSOLE_ID],
            by="Azeo Plant display generator",
        ))

    if names:
        return tuple(released)
    layouts = LayoutStore(root)
    layouts.assign(
        CONSOLE_ID, layout=LAYOUT, display_sets=[DISPLAY_SET],
        active_display_set=DISPLAY_SET,
    )
    return tuple(released)


def render_previews(output: Path) -> tuple[Path, ...]:
    """Render every generated draft through the shipping operator renderer."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication

    import azeo_control_trainer.core.strategy.blocks  # noqa: F401
    from azeo_control_trainer.core.hmi.pvms.studio.viewer import PvmDisplayView
    from azeo_control_trainer.core.hmi.theme.fonts import (
        apply_application_font,
        ensure_font_directory,
    )
    from azeo_control_trainer.core.hmi.theme.roles import Role
    from azeo_control_trainer.core.strategy.serialization.strategy_io import (
        load_strategy,
    )

    project = ROOT / "projects" / "AzeoPlantSimulator"
    graphs = {}
    for path in sorted(project.rglob("*.json")):
        if path.name == "_project.json" or "versions" in path.parts:
            continue
        try:
            graph, _comments = load_strategy(str(path))
        except Exception:  # noqa: BLE001
            continue
        if graph.blocks:
            graphs[graph.name] = graph

    ensure_font_directory()
    app = QApplication.instance() or QApplication([])
    apply_application_font()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    paths = []
    for display in build_displays():
        view = PvmDisplayView(
            display.to_dict(), lambda: graphs, live=False,
            config_root=DISPLAY_ROOT / "_pvmcfg",
        )
        image = QImage(WIDTH, HEIGHT, QImage.Format_ARGB32)
        image.fill(QColor(display.background or view.palette_roles[Role.SURFACE_BG]))
        painter = QPainter(image)
        view.scene().render(
            painter, QRectF(0, 0, WIDTH, HEIGHT),
            QRectF(0, 0, WIDTH, HEIGHT),
        )
        painter.end()
        path = output / f"{display.level}-{display.name}.png"
        if not image.save(str(path)):
            raise OSError(f"could not render {path}")
        paths.append(path)
        view.close()
        view.deleteLater()
    app.processEvents()
    return tuple(paths)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--render", type=Path, metavar="DIR",
        help="also render operator-view PNG previews into DIR",
    )
    parser.add_argument(
        "--publish", action="store_true",
        help=f"publish changed pages to PROD and assign them to {CONSOLE_ID}",
    )
    parser.add_argument("--display", choices=DISPLAY_NAMES,
                        help="update only this example; preserve other pages and station assignments")
    args = parser.parse_args()
    names = (args.display,) if args.display else ()
    paths = install(names=names)
    print("Generated editable Azeo Plant operator hierarchy:")
    for path in paths:
        print(f"  {path.relative_to(ROOT)}")
    print(f"  display set: {DISPLAY_SET}")
    print(f"  layout:      {LAYOUT}")
    if args.publish:
        released = publish_operator(names=names)
        print(f"  published:   {len(released)} changed revision(s) to {CONSOLE_ID}")
    if args.render:
        for path in render_previews(args.render):
            print(f"  preview:     {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
