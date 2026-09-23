"""Protected, theme-aware L1-L4 operator starting documents.

Each level supports a different operator task. Widgets are real retained PVMs
and trends with explicit CONFIGURE paths, never painted healthy sample values.
The linked installer below reuses the existing display and deployment models.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .base import Pvm
from .layout import DisplaySet, LayoutStore, default_layout
from .publishing import DisplayStore, PvmDisplay

TEMPLATE_PREFIX = "ISA-101"
L1_TEMPLATE = f"{TEMPLATE_PREFIX} - L1 High Level Overview"
L2_TEMPLATE = f"{TEMPLATE_PREFIX} - L2 Primary Operation"
L3_TEMPLATE = f"{TEMPLATE_PREFIX} - L3 Secondary Operation"
L4_TEMPLATE = f"{TEMPLATE_PREFIX} - L4 Support Display"
BUILTIN_TEMPLATE_NAMES = (L1_TEMPLATE, L2_TEMPLATE, L3_TEMPLATE, L4_TEMPLATE)
WIDTH, HEIGHT = 1600, 900


class HierarchySampleExists(ValueError):
    """The requested pack would replace an existing engineering artifact."""


@dataclass(frozen=True)
class HierarchySample:
    prefix: str
    displays: tuple[str, str, str, str]
    display_set: str
    layout: str


def _base(kind, ident, x, y, w, h, **fields):
    return dict(kind=kind, id=ident, x=x, y=y, w=w, h=h, **fields)


def _text(ident, text, x, y, w, h=26, *, size=11, role="TEXT", bold=False):
    return _base("text", ident, x, y, w, h, text=text, font_size=size,
                 font_bold=bold, text_role=role, text_valign="middle", text_wrap=True,
                 routing_obstacle=False)


def _panel(ident, title, x, y, w, h):
    return [_base("rect", ident, x, y, w, h, fill_role="SURFACE_PANEL",
                  line_role="LINE_SOFT", width=1, routing_obstacle=False, z=-5),
            _text(ident + "_title", title, x + 16, y + 10, w - 32, bold=True)]


def _trend(ident, title, x, y, w, h, paths=()):
    return [_text(ident + "_title", title, x, y, w, role="TEXT_DIM", size=10),
            _base("chart", ident, x, y + 32, w, h - 32,
                  pens=[dict(path=path, label=label) for label, path in
                        (paths or (("Parameter", "CONFIGURE/DIAGNOSTIC/OUT"),))],
                  routing_obstacle=False)]


def _pvm(ident, block, variant, x, y, w, h, tag, *, role="dynamo_inline"):
    return Pvm(id=ident, pvm_class=f"{block}/{role}", block_type=block, role=role, variant=variant,
               x=x, y=y, w=w, h=h, label=tag.rsplit("_", 1)[-1].title(),
               params={"path": "CONFIGURE/" + tag})


def _symbol(ident, symbol, x, y, w, h):
    return _base("symbol", ident, x, y, w, h, symbol=symbol)


def _pipe(ident, a, a_side, b, b_side):
    return dict(kind="pipe", id=ident, a=a, a_side=a_side, b=b, b_side=b_side,
                auto=False, crossover="jump", width=2, corner_radius=6)


def _header(level, title, purpose, parent, child):
    items = [_text("title", title, 24, 10, 1090, 35, size=19, bold=True),
             _text("purpose", purpose, 24, 49, 1090, 25, size=10, role="TEXT_DIM"),
             _text("level", f"L{level}", 1484, 14, 88, 36, size=19, bold=True),
             _text("configure_note", "TEMPLATE - Configure tags, ranges, alarm scope and navigation before release.",
                   24, 864, 1536, 25, size=10, role="TEXT_DIM")]
    if parent:
        items.append(_base("display_link", "up", 1128, 32, 148, 34,
                           target=parent, text="Up one level"))
    if child:
        items.append(_base("display_link", "down", 1292, 32, 172, 34,
                           target=child, text="Open next level"))
    return items


def _alarms(level, x, y, w, h):
    return [_text(f"alarms_title_{level}", "STATION ALARMS - configure unit scope", x, y, w, size=10, bold=True),
            _base("alarm_list", f"alarms_{level}", x, y + 30, w, h - 30,
                  path_prefix="", priority_min=7 if level == 1 else 0)]


def _l1(parent, child):
    items = _header(1, "PROCESS OVERVIEW", "Entire operating responsibility | performance, constraints and exceptions", parent, child)
    pvms = []
    for index, area in enumerate(("FEED", "REACTION", "SEPARATION", "UTILITIES")):
        x, ident = 24 + index * 392, f"area_{index}"
        items += _panel(ident, area, x, 90, 376, 448)
        items.append(_text(ident + "_state", "KPI / operating envelope", x + 16, 131, 344, size=10, role="TEXT_DIM"))
        for row, label in enumerate(("Throughput", "Quality", "Constraint")):
            tag, y = area + "_" + label.upper(), 176 + row * 78
            items.append(_text(ident + f"_label_{row}", label, x + 16, y, 100, size=10))
            pvms.append(_pvm(ident + f"_bar_{row}", "AI", "hbar", x + 120, y, 232, 68, tag))
        items += _trend(ident + "_trend", "Key parameter | recent samples", x + 16, 417, 344, 104,
                        (("Throughput", f"CONFIGURE/{area}_THROUGHPUT/OUT"),))
    items += _panel("performance", "DOMAIN PERFORMANCE AND UPSTREAM / DOWNSTREAM CONTEXT", 24, 558, 1020, 286)
    items += _trend("production", "Production / demand | recent samples", 40, 604, 480, 220,
                    (("Production", "CONFIGURE/PRODUCTION/OUT"),))
    items += _trend("constraint", "Constraint margin | recent samples", 544, 604, 484, 220,
                    (("Margin", "CONFIGURE/MARGIN/OUT"),))
    items += _panel("exceptions", "PRIORITY EXCEPTIONS", 1060, 558, 516, 286)
    items += _alarms(1, 1076, 602, 484, 224)
    return items, pvms


def _l2(parent, child):
    items = _header(2, "PROCESS UNIT - PRIMARY OPERATION", "Routine monitoring and control | feed, conversion, product and utilities", parent, child)
    items += _panel("process", "PROCESS PATH", 24, 90, 1030, 446)
    items += _panel("envelope", "OPERATING ENVELOPE", 1070, 90, 506, 446)
    items += [_symbol("feed", "vessel", 76, 304, 104, 144),
              _symbol("pump", "pump", 312, 348, 92, 78),
              _symbol("reactor", "reactor", 572, 300, 128, 172),
              _symbol("product", "vessel", 878, 304, 104, 144),
              _pipe("feed_run", "feed", "e", "pump", "w"),
              _pipe("charge_run", "pump", "e", "reactor", "w"),
              _pipe("product_run", "reactor", "e", "product", "w")]
    for ident, label, x, w in (("feed", "Feed vessel", 54, 148), ("pump", "Feed pump", 284, 150),
                              ("reactor", "Reactor", 540, 192), ("product", "Product vessel", 846, 166)):
        items.append(_text(ident + "_tag", label, x, 490, w, size=10))
    pvms = []
    for index, tag in enumerate(("FEED_FLOW", "TEMPERATURE", "LEVEL")):
        x = 52 + index * 326
        items.append(_text("loop_label_" + tag, tag.replace("_", " "), x, 142, 285, size=10, bold=True))
        pvms.append(_pvm("loop_" + tag, "PID", "", x, 176, 284, 94, tag))
    for index, label in enumerate(("PRESSURE", "QUALITY", "UTILITY")):
        y = 146 + index * 112
        items.append(_text("envelope_" + label, label, 1090, y, 152, size=10))
        pvms.append(_pvm("kpi_" + label, "AI", "hbar", 1250, y, 302, 90, label))
    items += _panel("operating_trends", "UNIT TRENDS", 24, 556, 1030, 288)
    items += _trend("unit_flow", "Feed / product | recent samples", 40, 598, 482, 226,
                    (("Feed", "CONFIGURE/FEED_FLOW/PV"), ("Product", "CONFIGURE/PRODUCT_FLOW/OUT")))
    items += _trend("unit_temp", "Temperature / pressure | recent samples", 546, 598, 490, 226,
                    (("Temperature", "CONFIGURE/TEMPERATURE/PV"), ("Pressure", "CONFIGURE/PRESSURE/OUT")))
    items += _panel("unit_alarms", "UNIT EXCEPTIONS", 1070, 556, 506, 288)
    items += _alarms(2, 1086, 598, 474, 230)
    return items, pvms


def _l3(parent, child):
    items = _header(3, "REACTOR - EQUIPMENT DETAIL", "Investigate deviations | related loops, process detail and non-routine intervention", parent, child)
    items += _panel("detail", "REACTOR AND RECIRCULATION", 422, 90, 756, 754)
    items += _panel("left", "FEED AND TEMPERATURE", 24, 90, 382, 754)
    items += _panel("right", "PRESSURE AND LEVEL", 1194, 90, 382, 754)
    from dataclasses import replace
    pvms = [replace(_pvm("vessel", "PID", "vessel", 702, 284, 186, 222, "LEVEL"),
                    choices={"show_readout": False})]
    items += [_symbol("inlet", "control_valve", 484, 328, 92, 72),
              _symbol("outlet", "control_valve", 1000, 328, 92, 72),
              _symbol("recirc", "pump", 982, 646, 96, 90),
              _symbol("cooler", "exchanger", 522, 638, 144, 98),
              _pipe("inlet_run", "inlet", "e", "vessel", "w"),
              _pipe("outlet_run", "vessel", "e", "outlet", "w"),
              _pipe("drain_run", "vessel", "drain", "recirc", "w"),
              _pipe("cooling_run", "cooler", "e", "recirc", "s")]
    for ident, label, x, y in (("inlet_tag", "Feed valve", 478, 290), ("outlet_tag", "Product valve", 990, 290),
                              ("vessel_tag", "Reactor level / history", 450, 530),
                              ("recirc_tag", "Recirculation", 964, 758), ("cooler_tag", "Cooling exchanger", 500, 758)):
        items.append(_text(ident, label, x, y, 210, size=10))
    for x, tags in ((40, ("FEED_FLOW", "TEMPERATURE")), (1210, ("PRESSURE", "LEVEL"))):
        for index, tag in enumerate(tags):
            y = 148 + index * 322
            items.append(_text("label_" + tag, tag.replace("_", " "), x, y, 350, size=11, bold=True))
            pvms.append(_pvm("detail_" + tag, "PID", "", x, y + 36, 350, 116, tag))
            items += _trend("history_" + tag, "PV / SP / OUT | recent samples", x, y + 168, 350, 138,
                            tuple((key, f"CONFIGURE/{tag}/{key}") for key in ("PV", "SP", "OUT")))
    items += _alarms(3, 442, 138, 712, 112)
    return items, pvms


def _table(ident, labels, x, y, w, h):
    return _base("table", ident, x, y, w, h,
                 columns=[dict(key="name", title="Parameter / condition", width=2.3),
                          dict(key="state", title="State / value", width=1.3)],
                 rows=[dict(name=label, state="Not configured") for label in labels],
                 row_height=36, header_height=30)


def _l4(parent, child):
    items = _header(4, "EQUIPMENT SUPPORT AND DIAGNOSTICS", "Permits, interlocks, instrumentation and approved operating guidance", parent, child)
    for ident, title, x, y, w, h in (
            ("permits", "PERMITS / INTERLOCKS / FIRST OUT", 24, 90, 748, 340),
            ("diagnostics", "INSTRUMENT AND CONTROLLER DIAGNOSTICS", 792, 90, 784, 340),
            ("procedure", "PROCEDURE / OPERATOR GUIDANCE", 24, 450, 748, 394),
            ("support", "RELATED HISTORY AND SUPPORT", 792, 450, 784, 394)):
        items += _panel(ident, title, x, y, w, h)
    items += [_table("permit_table", ("Start permissive", "Process interlock", "First-out source", "Reset readiness", "Bypass / suppression"),
                     40, 142, 716, 248),
              _table("diagnostic_table", ("Input quality", "Device communication", "Command / feedback", "Mode ownership", "Controller availability"),
                     808, 142, 752, 248),
              _text("permit_note", "Bind actual conditions. An unbound permit is unknown, never FALSE.", 40, 398, 716, size=10, role="TEXT_DIM"),
              _text("diagnostic_note", "Use the equipment PVM faceplate for authorized detail and tuning.", 808, 398, 752, size=10, role="TEXT_DIM"),
              _text("procedure_state", "Procedure reference: Not configured", 40, 500, 716, bold=True),
              _text("procedure_guidance", "Place a PA PVM assembly here and select its saved procedure revision. The paired faceplate provides progress, timers, holds and operator takeover.",
                    40, 548, 716, 82, size=12),
              _text("approved_guidance", "Approved operating guidance / contextual help", 40, 672, 716, bold=True),
              _text("guidance_placeholder", "Configure the equipment-specific instructions and documentation links.", 40, 716, 716, 64, role="TEXT_DIM")]
    items += _trend("support_history", "Diagnostic history | configure related parameters", 808, 500, 752, 162)
    items += _alarms(4, 808, 690, 752, 136)
    return items, []


def hierarchy_document(level: int, name: str, *, parent: str = "", child: str = "") -> PvmDisplay:
    builders = {1: _l1, 2: _l2, 3: _l3, 4: _l4}
    if level not in builders:
        raise ValueError("operator display hierarchy levels are 1 through 4")
    items, pvms = builders[level](parent, child)
    return PvmDisplay(name=name, items=items, pvms=pvms, background="", width=WIDTH,
                       height=HEIGHT, level=level, parent=parent, show_tag="none",
                       description=f"L{level} operator starting template - configure before release")


def builtin_hierarchy_templates() -> tuple[tuple[str, dict], ...]:
    """Protected documents exposed by every project TemplateStore."""
    names = ("L1 Overview", "L2 Primary", "L3 Secondary", "L4 Support")
    templates = []
    for level, template_name in enumerate(BUILTIN_TEMPLATE_NAMES, start=1):
        parent = names[level - 2] if level > 1 else ""
        child = names[level] if level < 4 else ""
        document = hierarchy_document(
            level, template_name, parent=parent, child=child,
        ).to_dict()
        templates.append((template_name, document))
    return tuple(templates)


def sample_names(prefix: str) -> HierarchySample:
    prefix = str(prefix or "").strip()
    if not prefix:
        raise ValueError("a hierarchy sample needs a name")
    displays = (
        f"{prefix} - L1 Overview",
        f"{prefix} - L2 Primary Operation",
        f"{prefix} - L3 Secondary Operation",
        f"{prefix} - L4 Support Display",
    )
    return HierarchySample(
        prefix=prefix,
        displays=displays,
        display_set=f"{prefix} - Operator Hierarchy",
        layout=f"{prefix} - Operator Layout",
    )


def install_hierarchy_sample(root: str | Path,
                             prefix: str) -> HierarchySample:
    """Create four linked drafts plus their display set and routed layout.

    Collision checking happens before the first write.  A training pack must
    never replace a real display merely because the same friendly prefix was
    entered twice.
    """
    sample = sample_names(prefix)
    display_store = DisplayStore(root)
    layout_store = LayoutStore(root)
    collisions = [name for name in sample.displays
                  if display_store.load_draft(name) is not None]
    if layout_store.display_set(sample.display_set) is not None:
        collisions.append(sample.display_set)
    # LayoutStore supplies a virtual Default when no file exists.  A named
    # sample layout only collides when that exact authored row is present.
    if layout_store.has_authored_layout(sample.layout):
        collisions.append(sample.layout)
    if collisions:
        raise HierarchySampleExists(
            "hierarchy artifacts already exist: " + ", ".join(collisions)
        )

    for level, name in enumerate(sample.displays, start=1):
        parent = sample.displays[level - 2] if level > 1 else ""
        child = sample.displays[level] if level < 4 else ""
        display_store.save_draft(
            hierarchy_document(level, name, parent=parent, child=child)
        )

    display_set = DisplaySet(sample.display_set)
    node = display_set.add_root(sample.displays[0])
    for display in sample.displays[1:]:
        node = display_set.add_child(node, display)
    layout_store.save_display_set(display_set)
    layout_store.save_layout(default_layout(sample.layout))
    return sample


__all__ = [
    "BUILTIN_TEMPLATE_NAMES",
    "HierarchySample",
    "HierarchySampleExists",
    "builtin_hierarchy_templates",
    "hierarchy_document",
    "install_hierarchy_sample",
    "sample_names",
]
