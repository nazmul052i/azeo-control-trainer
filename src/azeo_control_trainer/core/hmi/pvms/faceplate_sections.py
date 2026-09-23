"""Insertable faceplate sections, expressed as ordinary document items.

Building an authored faceplate from scratch meant drawing every part from
rectangles: a PV bar is a track, a fill rectangle and an animation
descriptor that normalizes EU0..EU100, and getting that wrong is quiet.
``UserPvmLibrary.create_faceplate_blueprint`` already knew how to build all
of it — as one monolithic scaffold you could take or leave.

This module is that knowledge, cut into named pieces an author can insert
one at a time. The blueprint is now composed from exactly these sections,
so there is one definition of what a PV bar is rather than two.

**Documents where possible; one hosted widget where rules demand it.**
``faceplate_ui.SECTIONS`` holds the shipped QWidget sections. Purely
presentational title, value, bar, trend and alarm parts are ordinary display
documents. The rule-bearing condition and state-list parts are not copied:
``LIVE_SECTIONS`` hosts the shipped widgets through ``QGraphicsProxyWidget``
so their tabs receive events and their trip/quality rules remain singular.
Process command controls are suppressed because an authored placement has no
right to guess a command path. See ``docs/FACEPLATE_AUTHORING.md``.

Each section declares the typed ``Pvm.*`` properties its bindings read, so
a caller can tell the author which properties the class still needs rather
than leaving a binding silently unresolved.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class FaceplateSection:
    """One insertable piece of a faceplate."""

    key: str
    title: str
    description: str
    #: Typed class properties the section's bindings read. A class that
    #: does not declare one of these renders the binding unresolved,
    #: which is correct, but the author should be told at insert time.
    requires: tuple[str, ...]
    #: Items at their canonical blueprint coordinates. `items_for`
    #: applies the caller's offset and id prefix.
    build: Callable[[], list] = field(repr=False, default=lambda: [])

    def items(self, prefix: str = "fp_", dx: float = 0.0,
              dy: float = 0.0) -> list:
        return _offset(self.build(), prefix, dx, dy)


def _offset(items: list, prefix: str, dx: float, dy: float) -> list:
    out = []
    for item in items:
        copied = copy.deepcopy(item)
        copied["id"] = f"{prefix}{copied['id']}"
        # Only shift when asked: the blueprint composes at the canonical
        # coordinates and must stay byte-identical to what it emitted
        # before this module existed.
        if dx:
            copied["x"] = copied.get("x", 0) + dx
        if dy:
            copied["y"] = copied.get("y", 0) + dy
        out.append(copied)
    return out


# ------------------------------------------------------------ builders
def _surface() -> list:
    return [
        {"kind": "rect", "id": "surface", "x": 0, "y": 0,
         "w": 204, "h": 468, "fill": "#E2E6EF",
         "line": "#B5BBC6", "width": 1, "locked": True},
    ]


def _title() -> list:
    return [
        {"kind": "text", "id": "module", "x": 8, "y": 8,
         "w": 188, "h": 14, "text": "Pvm.ModuleName",
         "font_size": 8, "text_halign": "center"},
        {"kind": "text", "id": "title", "x": 8, "y": 23,
         "w": 188, "h": 20, "text": "Pvm.Title",
         "font_size": 12, "font_bold": True,
         "text_halign": "center"},
        {"kind": "text", "id": "desc", "x": 8, "y": 43,
         "w": 188, "h": 14, "text": "Pvm.Description",
         "font_size": 8, "text_halign": "center"},
    ]


def _value() -> list:
    return [
        {"kind": "text", "id": "pv_label", "x": 8, "y": 68,
         "w": 28, "h": 18, "text": "PV", "font_size": 9},
        {"kind": "datalink", "id": "pv", "x": 34, "y": 65,
         "w": 72, "h": 22, "path": "Pvm.PVPath",
         "datalink_type": "numeric", "decimals": 2, "units": True},
        {"kind": "text", "id": "out_label", "x": 113, "y": 68,
         "w": 34, "h": 18, "text": "OUT", "font_size": 9},
        {"kind": "datalink", "id": "out", "x": 146, "y": 65,
         "w": 52, "h": 22, "path": "Pvm.OUTPath",
         "datalink_type": "numeric", "decimals": 1, "units": True},
    ]


def _pv_bar() -> list:
    return [
        {"kind": "text", "id": "eu100", "x": 59, "y": 100,
         "w": 34, "h": 14, "text": "Pvm.EU100", "font_size": 7,
         "text_halign": "right"},
        {"kind": "rect", "id": "pv_track", "x": 96, "y": 104,
         "w": 20, "h": 146, "fill": "#C9D5E6",
         "line": "#A7AFBA", "width": 1},
        # The dark PV bar is intentionally centred inside the light
        # channel, matching the manual's anatomy rather than hugging an
        # edge.  A live scale descriptor normalizes EU0..EU100 to 0..100.
        {"kind": "rect", "id": "pv_bar", "x": 102, "y": 105,
         "w": 8, "h": 144, "fill": "#496F9B", "line": "#496F9B",
         "fill_pct": 50,
         "props": {"fill_pct": {
             "kind": "animation", "path": "Pvm.PVPath",
             "type": "number", "input_start": "Pvm.EU0",
             "input_end": "Pvm.EU100", "output_start": 0,
             "output_end": 100}}},
        {"kind": "text", "id": "eu0", "x": 59, "y": 240,
         "w": 34, "h": 14, "text": "Pvm.EU0", "font_size": 7,
         "text_halign": "right"},
    ]


def _setpoint() -> list:
    return [
        {"kind": "user_entry", "id": "sp", "x": 8, "y": 118,
         "w": 76, "h": 32, "entry": {
             "kind": "slew", "label": "SP",
             "path": "Pvm.SPPath", "lo": "Pvm.EU0",
             "hi": "Pvm.EU100"}},
    ]


def _out_bar() -> list:
    return [
        {"kind": "text", "id": "outbar_label", "x": 8, "y": 265,
         "w": 40, "h": 14, "text": "OUT", "font_size": 8},
        {"kind": "rect", "id": "out_track", "x": 8, "y": 282,
         "w": 188, "h": 10, "fill": "#D0D2D4",
         "line": "#A7AAAD", "width": 1},
        {"kind": "rect", "id": "out_bar", "x": 9, "y": 283,
         "w": 186, "h": 8, "fill": "#178A91", "line": "#178A91",
         "fill_pct": 50, "fill_direction": "left",
         "props": {"fill_pct": {
             "kind": "animation", "path": "Pvm.OUTPath",
             "type": "number", "input_start": 0, "input_end": 100,
             "output_start": 0, "output_end": 100}}},
    ]


def _trend() -> list:
    return [
        {"kind": "chart", "id": "trend", "x": 8, "y": 303,
         "w": 188, "h": 72, "pens": [
             {"path": "Pvm.PVPath", "color": "#496F9B"},
             {"path": "Pvm.SPPath", "color": "#178A91"}],
         "lo": "Pvm.EU0", "hi": "Pvm.EU100",
         "window_seconds": 120},
    ]


def _alarms() -> list:
    return [
        {"kind": "alarm_list", "id": "alarms", "x": 8, "y": 383,
         "w": 188, "h": 46, "path_prefix": "Pvm.ModulePath",
         "max_rows": 2},
    ]


def _unit() -> list:
    return [
        {"kind": "text", "id": "unit", "x": 8, "y": 438,
         "w": 188, "h": 18, "text": "Unit: Pvm.UnitName",
         "font_size": 8},
    ]


#: Ordered as `faceplate_ui.CANONICAL_ORDER` stacks the shipped sections,
#: so the two vocabularies read the same way even though one is widgets
#: and the other documents.
SECTIONS: dict[str, FaceplateSection] = {
    "surface": FaceplateSection(
        "surface", "Faceplate surface",
        "The measured 204 x 468 body panel, locked.", (), _surface),
    "title": FaceplateSection(
        "title", "Title block",
        "Module name, title and description.",
        ("ModuleName", "Title", "Description"), _title),
    "value": FaceplateSection(
        "value", "Value row",
        "PV and OUT numeric readouts with engineering units.",
        ("PVPath", "OUTPath"), _value),
    "pv_bar": FaceplateSection(
        "pv_bar", "PV bar",
        "Vertical PV channel with its EU scale labels, normalized "
        "EU0..EU100 to 0..100%.",
        ("PVPath", "EU0", "EU100"), _pv_bar),
    "setpoint": FaceplateSection(
        "setpoint", "Setpoint entry",
        "A checked slew entry for the setpoint, clamped to the EU range.",
        ("SPPath", "EU0", "EU100"), _setpoint),
    "out_bar": FaceplateSection(
        "out_bar", "Output bar",
        "Horizontal OUT bar filling left to right over 0..100%.",
        ("OUTPath",), _out_bar),
    "trend": FaceplateSection(
        "trend", "Trend",
        "A two-minute chart of PV and SP over the EU range.",
        ("PVPath", "SPPath", "EU0", "EU100"), _trend),
    "alarms": FaceplateSection(
        "alarms", "Alarm list",
        "The module's alarms, scoped by path prefix.",
        ("ModulePath",), _alarms),
    "unit": FaceplateSection(
        "unit", "Unit name", "The unit this module belongs to.",
        ("UnitName",), _unit),
}

#: The order the blueprint composes them in. Concatenating these
#: reproduces the scaffold item-for-item.
BLUEPRINT_ORDER = ("surface", "title", "value", "pv_bar", "setpoint",
                   "out_bar", "trend", "alarms", "unit")


def blueprint_items(prefix: str = "fp_") -> list:
    """The measured faceplate scaffold, composed from the sections."""
    items: list = []
    for key in BLUEPRINT_ORDER:
        items.extend(SECTIONS[key].items(prefix))
    return items


def items_for(key: str, *, prefix: str = "fp_", dx: float = 0.0,
              dy: float = 0.0) -> list:
    """One section's items, offset and prefixed for insertion."""
    section = SECTIONS.get(key)
    if section is None:
        raise KeyError(f"unknown faceplate section {key!r}")
    return section.items(prefix, dx, dy)


# ------------------------------------------------- rule-bearing parts
#
# The sections above are drawings. These are not: a condition table
# decides that a TRIPPED condition reads ACTIVE while a satisfied
# permissive reads OK, and a sequencer body decides which of sixteen
# rows exist and which are on. An author cannot draw those rules, and
# reimplementing them here would be the second condition table this
# repo has already had once — the two disagreed about the inversion.
#
# So a live section is hosted, not copied: the placement carries binding
# paths, and the SHIPPED widget renders itself into the scene. One
# implementation of the rule, one of the painting.
#
# Only sections safe for authored hosting are offered. The scene uses a real
# QGraphicsProxyWidget, so tabs and scrolling receive events. Process command
# controls are suppressed: the host has no guessed command path or authority.
LIVE_SECTIONS: dict[str, dict] = {
    "conditions": {
        "title": "Condition table",
        "description": "Permissives, interlocks and trips with the "
                       "first-out cause. A trip reads ACTIVE when it has "
                       "tripped; the table owns that inversion.",
        "module": "faceplate_fb",
        "attr": "ConditionTable",
        "size": (640.0, 420.0),
        "keys": ("conditions",),
        "kwargs": {"show_commands": False},
    },
    "state_list": {
        "title": "Sequencer states",
        "description": "SEQ_fp / STD_fp body: the current state, then "
                       "its labelled rows.",
        "module": "faceplate_fb",
        "attr": "StateList",
        "size": (300.0, 380.0),
        "keys": ("state.name",),
    },
}

#: How many rows a SEQ/STD body carries, matching `StateList.ROWS` and
#: the block's own OUT_D1..16 / DESC_OUT1..16.
SEQUENCE_ROWS = 16


def live_section_class(key: str):
    """The shipped Section subclass a live part hosts."""
    spec = LIVE_SECTIONS.get(key)
    if spec is None:
        raise KeyError(f"unknown live faceplate section {key!r}")
    from importlib import import_module

    module = import_module(f".{spec['module']}", __package__)
    return getattr(module, spec["attr"])


def sequence_paths(control_tag: str, block: str = "SEQ1", *,
                   rows: int = SEQUENCE_ROWS) -> dict:
    """Binding paths for a sequencer body, from one control tag.

    This is the reason the part exists. Binding a SEQ body by hand is
    thirty-three paths — a state, sixteen descriptions and sixteen
    output flags — and graphics scripts cannot loop, so there was no
    way to generate them from the display either.
    """
    tag = str(control_tag or "").strip().strip("/")
    if not tag:
        return {}
    root = f"{tag}/{block}" if block else tag
    paths = {"state.name": f"{root}/STATE"}
    for index in range(1, max(1, int(rows)) + 1):
        paths[f"row{index}"] = f"{root}/CONFIG/DESC_OUT{index}"
        paths[f"row{index}.state"] = f"{root}/OUT_D{index}"
    return paths


def origin(key: str) -> tuple[float, float]:
    """The section's own top-left, in blueprint coordinates."""
    items = SECTIONS[key].build()
    if not items:
        return (0.0, 0.0)
    return (float(min(item.get("x", 0) for item in items)),
            float(min(item.get("y", 0) for item in items)))


def items_at(key: str, x: float, y: float, *,
             prefix: str = "fp_") -> list:
    """One section's items with its top-left placed at ``x, y``.

    The builders carry blueprint coordinates — a trend lives at y=303 in
    the scaffold. Inserting one should put it where the author asked,
    not 303 pixels below it, so the section is normalized to its own
    bounding origin first.
    """
    start_x, start_y = origin(key)
    return items_for(key, prefix=prefix, dx=x - start_x, dy=y - start_y)


def missing_properties(key: str, declared) -> tuple[str, ...]:
    """Required typed properties the class does not declare.

    Returned rather than raised: the author may well be inserting the
    section before configuring it, and an unresolved binding is visible
    on the canvas. What must not happen is silence.
    """
    section = SECTIONS.get(key)
    if section is None:
        return ()
    names = {str(name) for name in declared}
    return tuple(name for name in section.requires if name not in names)


__all__ = [
    "BLUEPRINT_ORDER", "FaceplateSection", "LIVE_SECTIONS", "SECTIONS",
    "SEQUENCE_ROWS", "blueprint_items", "items_at", "items_for",
    "live_section_class", "missing_properties", "origin", "sequence_paths",
]
