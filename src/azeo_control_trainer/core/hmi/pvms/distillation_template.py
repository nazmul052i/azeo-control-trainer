"""Editable L2 column layout using the shared PVM, pipe and faceplate stack."""
from __future__ import annotations

from .base import Pvm
from .publishing import PvmDisplay
from .symbols import connection_ports


TEMPLATE_NAME = "L2 - Distillation Column"


def distillation_document(name: str, *, bindings: dict[str, str] | None = None,
                          parent: str = "") -> PvmDisplay:
    """Semantic binding slots let projects map the same authored arrangement."""
    supplied = bindings or {}
    items, pvms = [], []

    def text(ident, value, x, y, w=180, h=24, size=10, bold=False):
        items.append(dict(id=ident, kind="text", text=value, x=x, y=y, w=w, h=h,
                          font_size=size, font_bold=bold, text_role="TEXT",
                          routing_obstacle=False))

    def pvm(ident, slot, block, variant, x, y, w, h, *, role="dynamo_inline", **choices):
        pvm = Pvm(id=ident, pvm_class=f"{block}/{role}", block_type=block,
                  role=role, variant=variant, x=x, y=y, w=w, h=h,
                  params={"path": supplied.get(slot, f"CONFIGURE/{slot.upper()}")},
                  choices={"show_hp_anatomy": False, **choices})
        pvms.append(pvm)

    def symbol(ident, kind, x, y, w, h):
        items.append(dict(id=ident, kind="symbol", symbol=kind, x=x, y=y, w=w, h=h,
                          line_role="EQUIPMENT", fill_role="EQUIPMENT_FILL", text_role="TEXT"))

    def pipe(ident, a, a_side, b, b_side, points=()):
        record = dict(id=ident, kind="pipe", a=a, a_side=a_side, b=b, b_side=b_side,
                      auto=False, width=1.8, line_role="EQUIPMENT", corner_radius=3,
                      crossover="jump")
        if points:
            record.update(route_mode="manual", route_points=[list(p) for p in points])
        items.append(record)

    def loop(ident, slot, label, x, y):
        title = supplied[slot].rsplit("/", 1)[-1] if slot in supplied else label
        text(ident + "_title", title, x, y - 23, 184, 21, 9, True)
        pvm(ident, slot, "PID", "hp", x, y, 184, 100,
            show_hp_anatomy=True, Typography="Large")

    def column_side(side, fraction):
        # The straight shell wall is inset in the SVG viewport. A host-rect
        # edge token leaves a visible gap. These authored normalized wall
        # positions are checked against the painted shell by the geometry
        # test; loading a template must not rasterize SVGs or import Qt.
        fx = {"w": .0698, "e": .9535}[side]
        return f"outline:{side}:{fx:.4f}:{fraction:.4f}"

    # Author this template on process axes, not host-box midpoints. These
    # dimensions use the chosen PVM footprints; the rendered-route test
    # catches any class-layout change that would move one of their nozzles.
    feed_axis = 254 + 6 * (150 / 160) + (420 - 12 * (150 / 160)) * .55
    overhead_axis, reflux_axis, steam_axis, product_axis = 260, 64, 740, 586
    pump_glyph_height = 96 - 24 * (86 / 84)
    discharge_offset = pump_glyph_height * connection_ports("pump")["e"][1]
    bottom_axis = 766 + discharge_offset
    receiver_y = overhead_axis - (6 + 178 * connection_ports("drum")["w"][1]) * (130 / 190)

    text("title", "DISTILLATION COLUMN  /  PRIMARY OPERATION", 440, 6, 850, 28, 17, True)
    text("level", "L2", 1490, 6, 70, 28, 16, True)
    text("kpi_title", "OPERATING MEASUREMENTS", 32, 82, 240, 24, 10, True)
    for ident, slot, caption, y in (
            ("pressure_fan", "pressure_ai", "Column pressure", 144),
            ("quality_fan", "quality_ai", "Overhead composition", 378),
            ("temperature_fan", "temperature_ai", "Bottom temperature", 612)):
        text(ident + "_title", caption, 45, y - 29, 220, 24, 11, True)
        pvm(ident, slot, "AI", "fan_indicator", 45, y, 210, 171)

    # The column and receiver are the physical equipment; the three right
    # level PVMs are explicitly labelled inventory readouts, not extra tanks.
    pvm("column", "bottom_level", "PID", "vessel", 562, 254, 150, 420,
        equipment_symbol="column", show_readout=False)
    text("column_title", "T1  /  COLUMN", 572, 286, 130, 25, 10, True)
    pvm("receiver", "drum_level", "PID", "vessel", 950, receiver_y, 200, 130,
        equipment_symbol="drum", show_readout=False)
    text("receiver_title", "D5  /  REFLUX DRUM", 968, receiver_y + 26, 184, 24, 9, True)
    symbol("condenser", "condenser", 780,
           overhead_axis - 76 * connection_ports("condenser")["w"][1], 100, 76)
    text("condenser_title", "E501 / CONDENSER", 775, 193, 190, 24, 9, True)
    # The plant's U500B sheet depicts a thermosiphon exchanger, not the
    # library's kettle silhouette. Keep process feed/return and steam on
    # their respective exchanger ports, matching that operating drawing.
    symbol("reboiler", "exchanger", 835, 628, 100, 100)
    text("reboiler_title", "E502 / REBOILER", 704, 706, 135, 24, 9, True)
    pvm("reflux_pump", "reflux_pump", "DEVCTL", "pump", 1114, 387, 86, 96,
        role="dynamo_compact", show_hp_anatomy=True)
    pvm("bottom_pump", "bottom_pump", "DEVCTL", "pump", 692, 766, 86, 96,
        role="dynamo_compact", show_hp_anatomy=True)
    for ident, x, axis in (("feed", 418, feed_axis), ("reflux", 1135, reflux_axis),
                           ("product", 1270, product_axis), ("steam", 500, steam_axis),
                           ("bottom_product", 1002, bottom_axis)):
        symbol(ident, "control_valve", x,
               axis - 62 * connection_ports("control_valve")["w"][1], 70, 62)
    for ident, label, x, y in (("feed_source", "Feed from D3", 298, feed_axis - 14),
                             ("product_sink", "Distillate", 1390, product_axis - 14),
                             ("steam_source", "Steam", 370, steam_axis - 14),
                             ("bottom_sink", "Bottoms", 1190, bottom_axis - 14)):
        items.append(dict(id=ident, kind="stream_connector", direction="incoming" if "source" in ident else "outgoing",
                          text=label, x=x, y=y, w=104, h=28,
                          ports=[dict(name="process", x=1.0 if "source" in ident else 0.0, y=.5,
                                      normal="e" if "source" in ident else "w")]))
    pipe("feed_to_valve", "feed_source", "process", "feed", "w")
    pipe("feed_to_column", "feed", "e", "column", column_side("w", .55))
    pipe("overhead", "column", "n", "condenser", "w")
    pipe("condensate", "condenser", "e", "receiver", "w")
    pipe("receiver_to_pump", "receiver", "s", "reflux_pump", "w")
    items.append(dict(id="discharge_tee", kind="ellipse", x=1236, y=387 + discharge_offset - 4,
                      w=8, h=8, pipe_junction=True, routing_obstacle=False,
                      fill_role="EQUIPMENT", line_role="EQUIPMENT", width=1, z=1))
    pipe("pump_discharge", "reflux_pump", "e", "discharge_tee", "w")
    pipe("reflux_discharge", "discharge_tee", "n", "reflux", "e")
    pipe("reflux_return", "reflux", "w", "column", column_side("w", .16), ((530, reflux_axis),))
    pipe("distillate", "discharge_tee", "s", "product", "w")
    pipe("distillate_out", "product", "e", "product_sink", "process")
    pipe("sump_to_pump", "column", "s", "bottom_pump", "w")
    pipe("bottoms_outlet", "bottom_pump", "e", "bottom_product", "w")
    pipe("bottoms_export", "bottom_product", "e", "bottom_sink", "process")
    pipe("boil_feed", "column", column_side("e", .80), "reboiler", "w")
    pipe("boil_return", "reboiler", "n", "column", column_side("e", .64))
    pipe("steam_in", "steam_source", "process", "steam", "w")
    pipe("steam_to_reboiler", "steam", "e", "reboiler", "e", ((964, steam_axis),))

    loop("pressure_loop", "pressure", "PRESSURE / PIC", 604, 96)
    loop("reflux_loop", "reflux_flow", "REFLUX / FIC", 979, 96)
    loop("feed_loop", "feed_transfer", "FEED TRANSFER / LIC", 318, 343)
    loop("temperature_loop", "temperature", "BOTTOM TEMPERATURE", 784, 387)
    loop("distillate_loop", "drum_level", "DISTILLATE / LIC", 996, 510)
    loop("steam_loop", "steam_flow", "STEAM FLOW / FIC", 1000, 640)
    loop("bottoms_loop", "bottom_level", "BOTTOMS / LIC", 318, 570)

    text("inventory_title", "INVENTORY READOUTS", 1350, 82, 220, 24, 10, True)
    for ident, slot, label, y in (("receiver_level", "drum_level", "D5 drum level", 141),
                                  ("boot_level", "boot_level", "D5 water boot", 356),
                                  ("column_level", "bottom_level", "T1 sump level", 631)):
        text(ident + "_title", label, 1360, y - 25, 190, 23, 10, True)
        pvm(ident, slot, "PID", "vessel", 1362, y, 160, 190,
            show_readout=True, equipment_symbol="vessel")
    note = ("Open an indicator, vessel or pump for its faceplate, trend and permitted controls."
            if supplied else "TEMPLATE - Map tags, verify ranges and equipment connections before publication.")
    text("guidance", note, 290, 876, 1270, 20, 9)
    return PvmDisplay(name=name, items=items, pvms=pvms, width=1600, height=900,
                      background="", level=2, parent=parent, show_tag="none",
                      description="Distillation column with compact process PVMs and inventory readouts")
