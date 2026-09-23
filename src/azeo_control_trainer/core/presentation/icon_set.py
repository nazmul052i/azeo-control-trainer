"""The engineering icon set: one vocabulary, one painter, two styles.

Every ribbon, toolbar, menu and navigation tree in the engineering products
asks for an icon by name and size. The shapes live here as primitives on a
24 px grid (the geometry of Lucide/Tabler line icons) and are painted in one
of two styles:

* ``colour`` (the default): filled shapes with one colour family per command
  class, a light-to-dark gradient, a drop shadow, a dark rim and a top
  highlight. Details inside a solid shape are cut in white so they survive at
  16 px. This is the look chosen in ``docs/UI_MODERNIZATION_PLAN_RIBBON_TREE_ICONS.md``.
* ``line``: the same geometry as a 2 px round stroke in one colour, the
  previous single-blue rule, kept for comparison and for places that need a
  quiet glyph.

Colour families follow what a command does (documents blue, containers amber,
run green, stop red, graphics teal, procedures purple, tools slate, hardware
navy, alarms orange), so a user learns the colour once. Alarm, validation and
process-state colours keep their semantic roles elsewhere; the alarm family is
used only by alarm commands. Disabled icons desaturate; they are never
recoloured. Function-block glyphs, PVM previews and faceplate action symbols
are domain symbols with their own painters and are not drawn here.

The proposal sheets under ``docs/uiux/images`` are rendered from these same
definitions by ``tools/render_icon_proposal.py``.
"""
from __future__ import annotations

import math
import os
import re

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPixmap,
    QTransform,
)
from PySide6.QtWidgets import QApplication

from .brand import AUTHORING_BLUE, ICON_FAMILIES

STYLE_COLOUR = "colour"
STYLE_LINE = "line"


def default_style() -> str:
    """``AZEO_ICON_STYLE`` selects ``line`` for comparison; colour is the product."""
    style = os.environ.get("AZEO_ICON_STYLE", STYLE_COLOUR).strip().lower()
    return STYLE_LINE if style == STYLE_LINE else STYLE_COLOUR


# --------------------------------------------------------------------------- vocabulary
# Primitives on a 24 x 24 grid:
#   ("p", svg path using M/L/H/V/C/S/Q/Z)   ("r", x, y, w, h, radius)   ("c", cx, cy, r)
#   ("gear",)
# Tags wrap a primitive:
#   ("f", prim)  filled in both styles          ("s", prim)  solid plate in the colour style
#   ("a", prim)  white accent (colour style)     ("o", prim)  dark outline (colour style)
_FOLDER = "M4 20h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-7.9c-.6 0-1.2-.3-1.6-.8L9.6 3.9C9.2 3.4 8.6 3 7.9 3H4C2.9 3 2 3.9 2 5v13c0 1.1.9 2 2 2z"
_GAUGE = "M3.3 19C1.9 16.1 1.8 12.6 3.4 9.6 5 6.6 8 4.5 11.5 4c3.5-.4 7 1 9.2 3.8 2.2 2.8 2.7 6.6 1.4 9.9"
_CUBE = "M21 8c0-.7-.4-1.4-1-1.7l-7-4c-.6-.4-1.4-.4-2 0l-7 4C3.4 6.6 3 7.3 3 8v8c0 .7.4 1.4 1 1.7l7 4c.6.4 1.4.4 2 0l7-4c.6-.3 1-1 1-1.7z"
_EYE = "M2 12s3-7 10-7 10 7 10 7-3 7-10 7S2 12 2 12z"
_TAG = "M20.6 13.4 13.4 20.6c-.8.8-2 .8-2.8 0L3 13V3h10l7.6 7.6c.8.8.8 2 0 2.8z"
_SAVE = "M19 21H5c-1.1 0-2-.9-2-2V5c0-1.1.9-2 2-2h11l5 5v11c0 1.1-.9 2-2 2z"
_SEARCH = [("c", 11, 11, 8), ("p", "M21 21l-4.3-4.3")]

PRIMITIVES: dict[str, list] = {
    # ---- files and documents
    "new": [("p", "M14 3H7C5.9 3 5 3.9 5 5v14c0 1.1.9 2 2 2h10c1.1 0 2-.9 2-2V8z"),
            ("p", "M14 3v5h5"), ("a", ("p", "M12 12v6")), ("a", ("p", "M9 15h6"))],
    "open": [("p", _FOLDER)],
    "save": [("p", _SAVE), ("p", "M17 21v-8H7v8"), ("p", "M7 3v5h8")],
    "save_as": [("p", _SAVE), ("p", "M17 21v-8H7v8"), ("p", "M7 3v5h8"),
                ("p", "M21 1v5"), ("p", "M18.5 3.5h5")],
    "copy": [("r", 9, 9, 12, 12, 2), ("o", ("p", "M5 15H4c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h9c1.1 0 2 .9 2 2v1"))],
    "paste": [("p", "M16 4h2c1.1 0 2 .9 2 2v14c0 1.1-.9 2-2 2H6c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2h2"),
              ("r", 8, 2, 8, 4, 1)],
    "cut": [("c", 6, 6, 3), ("c", 6, 18, 3), ("p", "M20 4 8.1 15.9"), ("p", "M14.5 14.5 20 20"), ("p", "M8.1 8.1 12 12")],
    "delete": [("p", "M3 6h18"), ("p", "M19 6v14c0 1.1-.9 2-2 2H7c-1.1 0-2-.9-2-2V6"),
               ("p", "M8 6V4c0-1.1.9-2 2-2h4c1.1 0 2 .9 2 2v2"), ("p", "M10 11v6"), ("p", "M14 11v6")],
    "print": [("s", ("p", "M4 9h16c1.1 0 2 .9 2 2v5c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2v-5c0-1.1.9-2 2-2z")),
              ("r", 6, 2, 12, 6, 1), ("a", ("r", 7, 14, 10, 6, 1))],
    "comment": [("p", "M21 15c0 1.1-.9 2-2 2H7l-4 4V5c0-1.1.9-2 2-2h14c1.1 0 2 .9 2 2z"),
                ("a", ("p", "M8 9h8")), ("a", ("p", "M8 13h5"))],
    "presets": [("p", "M6 3h12v18l-6-4-6 4z"), ("a", ("p", "M9 8h6")), ("a", ("p", "M9 12h6"))],
    "templates": [("r", 3, 3, 18, 7, 1.5), ("r", 3, 14, 7, 7, 1.5), ("r", 14, 14, 7, 7, 1.5)],
    "compare": [("r", 3, 3, 18, 18, 2), ("p", "M12 3v18")],
    "history": [("c", 12, 12, 9), ("p", "M12 7v5l3 2")],
    "datalog": [("p", "M3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5c0-1.7-4-3-9-3S3 3.3 3 5z"),
                ("a", ("p", "M3 5c0 1.7 4 3 9 3s9-1.3 9-3")), ("a", ("p", "M3 12c0 1.7 4 3 9 3s9-1.3 9-3"))],
    "checkpoint": [("p", "M4 22V4"), ("p", "M4 4c2-1.5 5-1.5 7 0s5 1.5 7 0v9c-2 1.5-5 1.5-7 0s-5-1.5-7 0z")],
    "xref": [("p", "M10 13c1.5 2 4 2.2 5.7.5l3-3c1.7-1.7 1.7-4.4 0-6s-4.3-1.7-6 0l-1.7 1.7"),
             ("p", "M14 11c-1.5-2-4-2.2-5.7-.5l-3 3c-1.7 1.7-1.7 4.4 0 6s4.3 1.7 6 0l1.7-1.7")],
    "add_property": [("p", "M4 6h10"), ("p", "M4 12h10"), ("p", "M4 18h7"), ("p", "M18 14v6"), ("p", "M15 17h6")],
    "help": [("s", ("c", 12, 12, 10)),
             ("a", ("p", "M9.1 9c.4-1.4 1.7-2.3 3.2-2 1.4.2 2.4 1.5 2.3 2.9 0 2-3 3-3 3")),
             ("a", ("f", ("c", 12, 17, 1)))],
    "trend": [("p", "M3 3v18h18"), ("p", "M7 15l4-5 3 3 5-6")],
    # ---- editing
    "undo": [("p", "M3 7v6h6"), ("p", "M21 17c0-5-4-9-9-9-2.3 0-4.4.9-6 2.3L3 13")],
    "redo": [("p", "M21 7v6h-6"), ("p", "M3 17c0-5 4-9 9-9 2.3 0 4.4.9 6 2.3L21 13")],
    "search": list(_SEARCH),
    "zoom_in": [*_SEARCH, ("p", "M11 8v6"), ("p", "M8 11h6")],
    "zoom_out": [*_SEARCH, ("p", "M8 11h6")],
    "zoom_fit": [("p", "M15 3h6v6"), ("p", "M9 21H3v-6"), ("p", "M21 3l-7 7"), ("p", "M3 21l7-7")],
    "select_all": [("p", "M3 8V5c0-1.1.9-2 2-2h3"), ("p", "M16 3h3c1.1 0 2 .9 2 2v3"),
                   ("p", "M21 16v3c0 1.1-.9 2-2 2h-3"), ("p", "M8 21H5c-1.1 0-2-.9-2-2v-3"),
                   ("p", "M8 12l3 3 5-6")],
    "align": [("p", "M21 6H3"), ("p", "M15 12H3"), ("p", "M17 18H3")],
    "auto": [("p", "M3 21L14 10"), ("p", "M15 4v3"), ("p", "M19 8h3"), ("p", "M17.5 5.5l2-2")],
    "properties": [("p", "M4 21v-7"), ("p", "M4 10V3"), ("p", "M12 21v-9"), ("p", "M12 8V3"), ("p", "M20 21v-5"),
                   ("p", "M20 12V3"), ("p", "M1 14h6"), ("p", "M9 8h6"), ("p", "M17 16h6")],
    "params": [("p", "M4 6h16"), ("p", "M4 12h16"), ("p", "M4 18h16"),
               ("f", ("c", 8, 6, 2)), ("f", ("c", 16, 12, 2)), ("f", ("c", 10, 18, 2))],
    "values": [("p", "M4 9h16"), ("p", "M4 15h16"), ("p", "M10 3L8 21"), ("p", "M16 3l-2 18")],
    "exec_order": [("p", "M10 6h11"), ("p", "M10 12h11"), ("p", "M10 18h11"),
                   ("f", ("r", 3, 4.5, 3.5, 3.5, 0.8)), ("f", ("r", 3, 10.5, 3.5, 3.5, 0.8)),
                   ("f", ("r", 3, 16.5, 3.5, 3.5, 0.8))],
    "exec_edit": [("p", "M4 7h9"), ("p", "M4 12h6"), ("p", "M4 17h7"),
                  ("p", "M20.5 4.5l-1-1c-.6-.6-1.5-.6-2.1 0L11 10v3h3l6.5-6.5c.6-.6.6-1.5 0-2z")],
    "settings": [("gear",)],
    "tools": [("p", "M21 6l-3 3-2.5-2.5 3-3c-1.8-.7-3.9-.3-5.3 1.1-1.5 1.5-1.8 3.6-1 5.4L3 19.2c-.6.6-.6 1.5 0 2.1s1.5.6 2.1 0l9.2-9.2c1.8.8 3.9.5 5.4-1 1.4-1.4 1.8-3.5 1.1-5.3z")],
    "utilities": [("r", 2, 8, 20, 12, 2), ("p", "M8 8V6c0-1.1.9-2 2-2h4c1.1 0 2 .9 2 2v2"), ("a", ("p", "M2 13h20"))],
    "designer": [("p", "M2 22l4-1 12-12-3-3L3 18z"), ("p", "M14.5 5.5l3 3"), ("p", "M20 8l2-2-4-4-2 2")],
    "watch": [("p", _EYE), ("c", 12, 12, 3)],
    "show": [("p", _EYE), ("c", 12, 12, 3)],
    "hide": [("p", _EYE), ("c", 12, 12, 3), ("o", ("p", "M3 3l18 18"))],
    "collapse_ribbon": [("p", "M6 15l6-6 6 6")],
    "expand_ribbon": [("p", "M6 9l6 6 6-6")],
    # ---- control and runtime
    "compile": [("r", 3, 3, 18, 18, 2), ("a", ("p", "M9 12l2.5 2.5L16 9.5"))],
    "download": [("p", "M12 3v12"), ("p", "M7 10l5 5 5-5"), ("p", "M5 21h14")],
    "upload": [("p", "M12 21V9"), ("p", "M7 14l5-5 5 5"), ("p", "M5 3h14")],
    "run_scan": [("s", ("c", 12, 12, 10)), ("a", ("f", ("p", "M10 8l6 4-6 4z")))],
    "pause": [("f", ("r", 6, 4, 4, 16, 1)), ("f", ("r", 14, 4, 4, 16, 1))],
    "step_block": [("f", ("p", "M5 4l11 8-11 8z")), ("p", "M19 5v14")],
    "deactivate": [("p", "M12 3v9"),
                   ("p", "M6.3 6.3C4.3 8.1 3 10.4 3 13c0 5 4 9 9 9s9-4 9-9c0-2.6-1.3-4.9-3.3-6.7")],
    "restore": [("p", "M3 12c0 5 4 9 9 9s9-4 9-9-4-9-9-9c-3.2 0-6 1.7-7.6 4.2"), ("p", "M3 3v6h6")],
    "debugger": [("s", ("p", "M12 20c-3.3 0-6-2.7-6-6v-3c0-3.3 2.7-6 6-6s6 2.7 6 6v3c0 3.3-2.7 6-6 6z")),
                 ("p", "M12 20v-9"), ("p", "M6 13H2"), ("p", "M22 13h-4"), ("p", "M4 6l3 2"), ("p", "M20 6l-3 2"),
                 ("p", "M4 20l3-3"), ("p", "M20 20l-3-3"), ("p", "M9 6c0-1.7 1.3-3 3-3s3 1.3 3 3")],
    "simulator": [("p", "M9 3h6"), ("p", "M10 3v6l-6 9c-1 1.5 0 3 2 3h12c2 0 3-1.5 2-3l-6-9V3z")],
    "status": [("s", ("c", 12, 12, 10)), ("a", ("p", "M7 12h2l2-4 3 8 2-4h1"))],
    "diagnostics": [("r", 2, 4, 20, 16, 2), ("a", ("p", "M5 12h3l2-4 3 8 2-4h4"))],
    "verify": [("p", "M20 13c0 5-3.5 7.5-7.7 9-.2.1-.5.1-.7 0C7.5 20.5 4 18 4 13V6c0-.6.4-1 1-1 2 0 4.5-1.2 6.2-2.7.4-.4 1.1-.4 1.5 0C14.5 3.8 17 5 19 5c.6 0 1 .4 1 1z"),
               ("a", ("p", "M9 12l2 2 4-4"))],
    "publish": [("p", "M22 2 15 22l-4-9-9-4z"), ("p", "M22 2 11 13")],
    # ---- I/O, connectivity, hardware
    "connect": [("p", "M12 22v-5"), ("p", "M9 8V2"), ("p", "M15 8V2"),
                ("p", "M18 8v5c0 3.3-2.7 6-6 6s-6-2.7-6-6V8z")],
    "disconnect": [("p", "M12 22v-5"), ("p", "M9 8V2"), ("p", "M15 8V2"),
                   ("p", "M18 8v5c0 3.3-2.7 6-6 6s-6-2.7-6-6V8z"), ("o", ("p", "M4 4l16 16"))],
    "io_config": [("r", 2, 6, 20, 12, 2), ("a", ("f", ("c", 6, 12, 1.3))), ("a", ("f", ("c", 10, 12, 1.3))),
                  ("a", ("f", ("c", 14, 12, 1.3))), ("a", ("f", ("c", 18, 12, 1.3)))],
    "assign_io": [("r", 10, 4, 12, 16, 2), ("p", "M2 12h8"), ("p", "M6 8l4 4-4 4")],
    "tagdb": [("p", _TAG), ("a", ("f", ("c", 7.5, 7.5, 1.2)))],
    "named_sets": [("p", _TAG), ("a", ("f", ("c", 7.5, 7.5, 1.2))), ("o", ("p", "M9 22l9-9"))],
    "module_props": [("p", _CUBE), ("p", "M3.3 7 12 12l8.7-5"), ("p", "M12 22V12"), ("a", ("f", ("c", 12, 15.5, 2)))],
    "lib_block": [("p", "M14 7c0-1.7-1.3-3-3-3S8 5.3 8 7H5c-.6 0-1 .4-1 1v3c1.7 0 3 1.3 3 3s-1.3 3-3 3v3c0 .6.4 1 1 1h3c0-1.7 1.3-3 3-3s3 1.3 3 3h3c.6 0 1-.4 1-1v-3c-1.7 0-3-1.3-3-3s1.3-3 3-3V8c0-.6-.4-1-1-1h-3z")],
    # ---- graphics
    "test": [("p", "M4.5 3h15"), ("p", "M6 3v16c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V3"), ("p", "M6 14h12")],
    "group": [("o", ("r", 3, 3, 12, 12, 2)), ("r", 9, 9, 12, 12, 2)],
    "pipe": [("c", 6, 19, 3), ("c", 18, 5, 3),
             ("p", "M12 19h4.5c1.9 0 3.5-1.6 3.5-3.5S18.4 12 16.5 12h-9C5.6 12 4 10.4 4 8.5S5.6 5 7.5 5H12")],
    "pvm": [("p", _GAUGE), ("p", "M12 14l4-4"), ("f", ("c", 12, 14, 1.5))],
    "faceplate": [("r", 4, 3, 16, 18, 2), ("p", "M8 8h8"), ("r", 8, 12, 8, 3, 0.5), ("p", "M8 18h5")],
    "display": [("r", 2, 3, 20, 14, 2), ("p", "M8 21h8"), ("p", "M12 17v4")],
    "library": [("p", "M16 6l4 14"), ("p", "M12 6v14"), ("p", "M8 8v12"), ("p", "M4 4v16")],
    "layers": [("p", "M12 2 2 7l10 5 10-5z"), ("p", "M2 17l10 5 10-5"), ("p", "M2 12l10 5 10-5")],
    "alarm": [("p", "M6 8c0-3.3 2.7-6 6-6s6 2.7 6 6c0 7 3 9 3 9H3s3-2 3-9"),
              ("p", "M10.3 21c.4.6 1 1 1.7 1s1.3-.4 1.7-1")],
    "procedure": [("p", "M3 17l2 2 4-4"), ("p", "M3 7l2 2 4-4"), ("p", "M13 6h8"), ("p", "M13 12h8"), ("p", "M13 18h8")],
    # ---- navigation node kinds
    "project": [("p", _FOLDER), ("a", ("f", ("r", 8, 11, 8, 4, 1)))],
    "folder": [("p", _FOLDER)],
    "area": [("p", _FOLDER), ("a", ("p", "M7 13h10"))],
    "add_group": [("p", _FOLDER), ("a", ("p", "M12 10v7")), ("a", ("p", "M8.5 13.5h7"))],
    "unassigned": [("o", ("p", _FOLDER)), ("p", "M9 16l6-6")],
    "controller": [("r", 4, 4, 16, 16, 2), ("r", 9, 9, 6, 6, 0.5), ("p", "M9 1v3"), ("p", "M15 1v3"),
                   ("p", "M9 20v3"), ("p", "M15 20v3"), ("p", "M20 9h3"), ("p", "M20 15h3"),
                   ("p", "M1 9h3"), ("p", "M1 15h3")],
    "node": [("r", 2, 2, 20, 8, 2), ("r", 2, 14, 20, 8, 2), ("f", ("c", 6, 6, 1)), ("f", ("c", 6, 18, 1))],
    "module": [("p", _CUBE), ("p", "M3.3 7 12 12l8.7-5"), ("p", "M12 22V12")],
    "sfc": [("r", 3, 3, 8, 8, 2), ("r", 13, 13, 8, 8, 2), ("o", ("p", "M7 11v4c0 1.1.9 2 2 2h4"))],
    "equipment": [("r", 3, 3, 18, 18, 2), ("p", "M3 9h18"), ("p", "M9 21V9")],
    "provider": [("r", 2, 6, 20, 12, 2), ("p", "M6 12h.01"), ("p", "M10 12h.01"), ("p", "M14 12h8")],
    "network": [("c", 12, 5, 3), ("c", 5, 19, 3), ("c", 19, 19, 3), ("p", "M12 8v3"), ("p", "M12 11l-7 5"),
                ("p", "M12 11l7 5")],
}
PRIMITIVES["em_child"] = PRIMITIVES["module"]
PRIMITIVES["pvm_class"] = PRIMITIVES["pvm"]
PRIMITIVES["faceplate_class"] = PRIMITIVES["faceplate"]

#: Colour family per icon. Families name what the command does, not the command.
FAMILY_OF: dict[str, str] = {
    **{name: "document" for name in ("new", "save", "save_as", "copy", "paste", "print", "comment", "presets",
                                     "compare", "history", "datalog", "checkpoint", "xref", "add_property",
                                     "help", "trend")},
    **{name: "container" for name in ("open", "folder", "project", "area", "add_group", "library", "named_sets",
                                      "pause")},
    **{name: "run" for name in ("compile", "run_scan", "step_block", "verify", "publish")},
    **{name: "stop" for name in ("delete", "deactivate", "disconnect")},
    **{name: "graphics" for name in ("test", "group", "pipe", "pvm", "pvm_class", "faceplate", "faceplate_class",
                                     "display", "layers", "templates", "designer", "simulator")},
    **{name: "procedure" for name in ("procedure", "sfc")},
    **{name: "tool" for name in ("undo", "redo", "cut", "search", "zoom_in", "zoom_out", "zoom_fit", "select_all",
                                 "align", "auto", "properties", "params", "values", "exec_order", "exec_edit",
                                 "settings", "tools", "utilities", "watch", "show", "hide", "collapse_ribbon",
                                 "expand_ribbon", "restore", "debugger", "unassigned")},
    **{name: "hardware" for name in ("download", "upload", "connect", "io_config", "assign_io", "tagdb",
                                     "module_props", "lib_block", "status", "diagnostics", "controller", "node",
                                     "module", "em_child", "equipment", "provider", "network")},
    "alarm": "alarm",
}

#: State badges for navigation trees: fill colour and glyph.
STATE_BADGES = {
    "online": ("#2D8E3C", "dot"),
    "modified": ("#B8962A", "dot"),
    "offline": ("#9BADC6", "dot"),
    "error": ("#B3261E", "excl"),
    "locked": ("#1B2130", "lock"),
    "editing": (AUTHORING_BLUE, "pen"),
}


def has_icon(name: str) -> bool:
    return name in PRIMITIVES


def names() -> tuple[str, ...]:
    return tuple(PRIMITIVES)


# --------------------------------------------------------------------------- geometry
def _path_from_svg(data: str) -> QPainterPath:
    """Support M/L/H/V/C/S/Q/Z in absolute and relative forms (enough for these icons)."""
    path = QPainterPath()
    tokens = re.findall(r"[MLHVCQSZmlhvcqsz]|-?\d*\.?\d+", data)
    i = 0
    cmd = ""
    prev = ""
    cx = cy = sx = sy = 0.0
    lx = ly = 0.0

    def take(count):
        nonlocal i
        values = [float(t) for t in tokens[i:i + count]]
        i += count
        return values

    while i < len(tokens):
        tok = tokens[i]
        if tok.isalpha():
            cmd = tok
            i += 1
            if cmd in "Zz":
                path.closeSubpath()
                cx, cy = sx, sy
            continue
        rel = cmd.islower()
        c = cmd.upper()
        cmd_prev, prev = prev, cmd
        if c == "M":
            x, y = take(2)
            if rel:
                x, y = cx + x, cy + y
            path.moveTo(x, y)
            cx, cy, sx, sy = x, y, x, y
            cmd = "l" if rel else "L"
        elif c == "L":
            x, y = take(2)
            if rel:
                x, y = cx + x, cy + y
            path.lineTo(x, y)
            cx, cy = x, y
        elif c == "H":
            (x,) = take(1)
            x = cx + x if rel else x
            path.lineTo(x, cy)
            cx = x
        elif c == "V":
            (y,) = take(1)
            y = cy + y if rel else y
            path.lineTo(cx, y)
            cy = y
        elif c == "C":
            v = take(6)
            if rel:
                v = [v[0] + cx, v[1] + cy, v[2] + cx, v[3] + cy, v[4] + cx, v[5] + cy]
            path.cubicTo(*v)
            lx, ly = v[2], v[3]
            cx, cy = v[4], v[5]
        elif c == "S":
            v = take(4)
            if rel:
                v = [v[0] + cx, v[1] + cy, v[2] + cx, v[3] + cy]
            if cmd_prev in "CcSs":
                c1x, c1y = 2 * cx - lx, 2 * cy - ly
            else:
                c1x, c1y = cx, cy
            path.cubicTo(c1x, c1y, v[0], v[1], v[2], v[3])
            lx, ly = v[0], v[1]
            cx, cy = v[2], v[3]
        elif c == "Q":
            v = take(4)
            if rel:
                v = [v[0] + cx, v[1] + cy, v[2] + cx, v[3] + cy]
            path.quadTo(*v)
            cx, cy = v[2], v[3]
        else:
            i += 1
    return path


def _gear_path() -> QPainterPath:
    outer, inner, teeth = 10.5, 8.2, 8
    poly = QPainterPath()
    for k in range(teeth * 2):
        a = math.pi * k / teeth
        r = outer if k % 2 == 0 else inner
        for ang in (a - 0.2, a + 0.2):
            point = QPointF(12 + r * math.cos(ang), 12 + r * math.sin(ang))
            (poly.moveTo if poly.elementCount() == 0 else poly.lineTo)(point)
    poly.closeSubpath()
    hole = QPainterPath()
    hole.addEllipse(QPointF(12, 12), 3.2, 3.2)
    return poly.subtracted(hole)


def _unwrap(prim):
    """Return (primitive, accent, force_fill, filled, outline) from the tagged forms."""
    accent = force = filled = outline = False
    while prim[0] in ("a", "s", "f", "o"):
        tag, prim = prim[0], prim[1]
        accent |= tag == "a"
        force |= tag == "s"
        filled |= tag == "f"
        outline |= tag == "o"
    return prim, accent, force, filled, outline


def _primitive_path(prim):
    """Path for one primitive and whether it is a closed shape."""
    kind = prim[0]
    if kind == "p":
        return _path_from_svg(prim[1]), prim[1].rstrip().lower().endswith("z")
    if kind == "r":
        _, rx, ry, w, h, rad = prim
        path = QPainterPath()
        path.addRoundedRect(QRectF(rx, ry, w, h), rad, rad)
        return path, True
    if kind == "c":
        _, cx, cy, r = prim
        path = QPainterPath()
        path.addEllipse(QPointF(cx, cy), r, r)
        return path, False  # circles read as rings unless tagged ("s", ...)
    if kind == "gear":
        return _gear_path(), True
    return None


def _stroker(width: float) -> QPainterPathStroker:
    stroker = QPainterPathStroker()
    stroker.setWidth(width)
    stroker.setCapStyle(Qt.RoundCap)
    stroker.setJoinStyle(Qt.RoundJoin)
    return stroker


def _region(prims, stroke: float):
    """Fill regions for the colour style: body, white detail, dark outline.

    Closed shapes become solid plates. Open strokes stay strokes in the family
    colour unless they lie inside a plate, in which case they are cut in white
    (the fold of a document, the label of a diskette). A closed shape nested in
    a larger plate is cut in white too. ``("o", ...)`` primitives are drawn as
    a dark outline so overlapping objects read as two objects.
    """
    full, thin = _stroker(stroke), _stroker(stroke * 0.7)
    body, detail, outline = QPainterPath(), QPainterPath(), QPainterPath()
    body.setFillRule(Qt.WindingFill)
    plates, strokes = [], []
    for raw in prims:
        prim, accent, force, filled, outlined = _unwrap(raw)
        built = _primitive_path(prim)
        if built is None:
            continue
        path, closed = built
        if accent:
            detail.addPath(thin.createStroke(path) if not filled else path)
        elif outlined:
            outline.addPath(full.createStroke(path))
        elif closed or force or filled:
            plates.append(path)
        else:
            strokes.append(path)
    tolerance = stroke
    rects = [plate.boundingRect() for plate in plates]

    def inside(rect):
        return any(host != rect and host.adjusted(-1, -1, 1, 1).contains(rect) for host in rects)

    for plate, rect in zip(plates, rects):
        if inside(rect):
            detail.addPath(thin.createStroke(plate))
        else:
            body.addPath(plate)
            body.addPath(full.createStroke(plate))
    for path in strokes:
        # a straight line has a zero-width or zero-height rectangle, which Qt never
        # reports as contained; give it a nominal size before the test
        rect = path.boundingRect().adjusted(0, 0, 0.01, 0.01)
        nested = any(host.adjusted(-tolerance, -tolerance, tolerance, tolerance).contains(rect)
                     for host in rects)
        (detail if nested else body).addPath((thin if nested else full).createStroke(path))
    return body.simplified(), detail.simplified(), outline.simplified()


# --------------------------------------------------------------------------- painters
def paint_line(p: QPainter, name: str, x: float, y: float, size: float, colour: str | None = None) -> None:
    """Style A: the 24-grid geometry as a 2 px round stroke in one colour."""
    prims = PRIMITIVES[name]
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    p.translate(x, y)
    p.scale(size / 24.0, size / 24.0)
    col = QColor(colour or AUTHORING_BLUE)
    stroke = 2.0 if size >= 20 else 1.75
    pen = QPen(col, stroke, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    for raw in prims:
        prim, _accent, _force, filled, _outline = _unwrap(raw)
        p.setPen(Qt.NoPen if filled else pen)
        p.setBrush(col if filled else Qt.NoBrush)
        kind = prim[0]
        if kind == "p":
            p.drawPath(_path_from_svg(prim[1]))
        elif kind == "r":
            _, rx, ry, w, h, rad = prim
            p.drawRoundedRect(QRectF(rx, ry, w, h), rad, rad)
        elif kind == "c":
            _, cx, cy, r = prim
            p.drawEllipse(QPointF(cx, cy), r, r)
        elif kind == "gear":
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(_gear_path())
    p.restore()


def paint_colour(p: QPainter, name: str, x: float, y: float, size: float) -> None:
    """Style B: family gradient plate, drop shadow, dark rim, top highlight, white details."""
    light, base, dark = ICON_FAMILIES[FAMILY_OF.get(name, "tool")]
    scale = size / 24.0
    stroke = 3.2 if size >= 20 else 3.6
    body, accent, outline = _region(PRIMITIVES[name], stroke)
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    p.translate(x, y)
    p.scale(scale, scale)
    p.setPen(Qt.NoPen)
    for dx, dy, alpha in ((0.9, 1.4, 45), (0.5, 0.8, 35)):
        p.setBrush(QColor(0, 0, 0, alpha))
        p.drawPath(QTransform().translate(dx, dy).map(body))
    gradient = QLinearGradient(3, 2, 21, 22)
    gradient.setColorAt(0.0, QColor(light))
    gradient.setColorAt(0.55, QColor(base))
    gradient.setColorAt(1.0, QColor(dark))
    p.setBrush(gradient)
    p.drawPath(body)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor(dark), 0.5 if size >= 20 else 0.7))
    p.drawPath(body)
    p.setClipPath(body)
    sheen = QLinearGradient(0, 2, 0, 13)
    sheen.setColorAt(0.0, QColor(255, 255, 255, 120))
    sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.setPen(Qt.NoPen)
    p.setBrush(sheen)
    p.drawRect(QRectF(0, 0, 24, 13))
    p.setClipping(False)
    if not outline.isEmpty():
        p.setBrush(QColor(dark))
        p.drawPath(outline)
    if not accent.isEmpty():
        p.setBrush(QColor(0, 0, 0, 40))
        p.drawPath(QTransform().translate(0.3, 0.5).map(accent))
        p.setBrush(QColor("#FFFFFF"))
        p.drawPath(accent)
    p.restore()


def paint_badge(p: QPainter, state: str, x: float, y: float, size: float) -> None:
    """A tree-state badge: filled disc with a white ring and a small glyph."""
    fill, glyph = STATE_BADGES[state]
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(QPen(QColor("#FFFFFF"), max(1.0, size * 0.12)))
    p.setBrush(QColor(fill))
    p.drawEllipse(QRectF(x, y, size, size))
    p.setPen(QPen(QColor("#FFFFFF"), max(1.0, size * 0.16), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    c = size / 2
    if glyph == "excl":
        p.drawLine(QPointF(x + c, y + size * 0.28), QPointF(x + c, y + size * 0.58))
        p.drawPoint(QPointF(x + c, y + size * 0.76))
    elif glyph == "lock":
        p.drawRoundedRect(QRectF(x + size * 0.3, y + size * 0.45, size * 0.4, size * 0.3), 1, 1)
        shackle = _path_from_svg("M0 0.45 V0.35 C0 0.2 0.1 0.15 0.2 0.15 S0.4 0.2 0.4 0.35 V0.45")
        p.drawPath(QTransform().translate(x + size * 0.3, y).scale(size, size).map(shackle))
    elif glyph == "pen":
        p.drawLine(QPointF(x + size * 0.3, y + size * 0.7), QPointF(x + size * 0.7, y + size * 0.3))
    p.restore()


# --------------------------------------------------------------------------- icons
_CACHE: dict[tuple, QIcon] = {}


def _device_pixel_ratio() -> float:
    app = QApplication.instance()
    return app.devicePixelRatio() if app else 1.0


def render_pixmap(name: str, size: int, style: str | None = None, colour: str | None = None,
                  state: str | None = None) -> QPixmap:
    """Paint one icon (and optional tree-state badge) into a DPI-scaled pixmap."""
    style = style or default_style()
    dpr = _device_pixel_ratio()
    pixmap = QPixmap(max(1, int(round(size * dpr))), max(1, int(round(size * dpr))))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    if style == STYLE_LINE:
        paint_line(painter, name, 0, 0, size, colour)
    else:
        paint_colour(painter, name, 0, 0, size)
    if state in STATE_BADGES:
        badge = max(5.0, size * 0.45)
        paint_badge(painter, state, size - badge, size - badge, badge)
    painter.end()
    return pixmap


def make_icon(name: str, size: int = 20, style: str | None = None, colour: str | None = None,
              state: str | None = None) -> QIcon:
    """Cached QIcon for ``name``; the colour argument only matters in the line style."""
    style = style or default_style()
    key = (name, size, style, colour if style == STYLE_LINE else None, state, _device_pixel_ratio())
    icon = _CACHE.get(key)
    if icon is None:
        icon = QIcon(render_pixmap(name, size, style, colour, state))
        _CACHE[key] = icon
    return icon
