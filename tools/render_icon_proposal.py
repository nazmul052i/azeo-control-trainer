"""Render the engineering icon set as one contact sheet.

Companion to docs/UI_MODERNIZATION_PLAN_RIBBON_TREE_ICONS.md. The shapes and
painters come from ``core/presentation/icon_set.py``, the module every product
draws from, so the sheet always shows what the applications show. Icons appear
at 3x and at the three optical sizes 16 / 20 / 24 px; tree-state badges at the
tree size.

    python tools/render_icon_proposal.py [output.png] [--style line|colour]

Run it under the normal Windows platform; the offscreen platform on a build
host may have no font glyphs, which turns every label into boxes.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from azeo_control_trainer.core.presentation import icon_set  # noqa: E402
from azeo_control_trainer.core.presentation.brand import AUTHORING_BLUE  # noqa: E402

DEFAULT_OUTPUT = ROOT / "docs" / "uiux" / "images" / "icon_proposal_sheet.png"

COMMANDS = ("new", "open", "save", "undo", "redo", "cut", "copy", "paste", "delete", "search", "zoom_in",
            "zoom_out", "zoom_fit", "compile", "download", "upload", "run_scan", "deactivate", "verify",
            "simulator", "publish", "properties", "settings", "help", "align", "group", "pipe", "pvm",
            "faceplate", "display", "library", "layers", "watch", "trend", "alarm", "procedure",
            "history", "datalog", "checkpoint", "restore", "connect", "disconnect", "io_config",
            "assign_io", "tagdb", "debugger", "pause", "step_block", "print", "comment", "xref",
            "params", "templates", "compare", "exec_order", "tools")
NODE_KINDS = ("project", "controller", "node", "module", "sfc", "equipment", "display", "pvm_class",
              "faceplate_class", "procedure", "library", "provider", "network")


def render(output: Path, style: str = icon_set.STYLE_LINE) -> Path:
    if QGuiApplication.instance() is None:
        QGuiApplication(sys.argv[:1])
    dpr = 2
    width, margin = 1180, 36
    col_w, row_h = 140, 118
    rows = math.ceil(len(COMMANDS) / 8) + math.ceil(len(NODE_KINDS) / 8)
    height = 190 + rows * row_h + 250
    image = QImage(width * dpr, height * dpr, QImage.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(dpr)
    image.fill(QColor("#FFFFFF"))
    p = QPainter(image)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    title = QFont("Segoe UI", 15, QFont.DemiBold)
    body = QFont("Segoe UI", 9)
    small = QFont("Segoe UI", 8)
    ink, muted = QColor("#1B2130"), QColor("#5B6B7C")

    def text(s, x, y, font, colour=ink):
        p.setFont(font)
        p.setPen(colour)
        p.drawText(QPointF(x, y), s)

    def paint(name, x, y, size):
        if style == icon_set.STYLE_COLOUR:
            icon_set.paint_colour(p, name, x, y, size)
        else:
            icon_set.paint_line(p, name, x, y, size, AUTHORING_BLUE)

    if style == icon_set.STYLE_COLOUR:
        text("Engineering icon set, style B: colour with depth", margin, 44, title)
        text("Same 24 px geometry as style A, filled: one colour family per command class, light-to-dark gradient, "
             "drop shadow, dark rim and top highlight. Shown at 3x, then at 16 / 20 / 24 px.", margin, 66, body, muted)
    else:
        text("Engineering icon set, style A: line", margin, 44, title)
        text("24 px grid, 2 px round stroke, one colour per icon (authoring blue " + AUTHORING_BLUE +
             "). Shown at 3x, then at the three optical sizes 16 / 20 / 24 px.", margin, 66, body, muted)
    text("Rendered by tools/render_icon_proposal.py from core/presentation/icon_set.py, the module the applications draw from.",
         margin, 84, small, muted)

    def section(name, icons, top):
        text(name, margin, top, QFont("Segoe UI", 11, QFont.DemiBold))
        p.setPen(QPen(QColor("#D5DCE4"), 1))
        p.drawLine(QPointF(margin, top + 8), QPointF(width - margin, top + 8))
        for index, label in enumerate(icons):
            cx = margin + (index % 8) * col_w
            cy = top + 22 + (index // 8) * row_h
            p.setPen(QPen(QColor("#EEF1F5"), 1))
            p.setBrush(QColor("#F7F9FB"))
            p.drawRoundedRect(QRectF(cx, cy, 72, 72), 6, 6)
            for ox, oy, sz in ((0, 0, 72), (82, 4, 16), (82, 26, 20), (82, 50, 24)):
                paint(label, cx + ox, cy + oy, sz)
            text(label, cx, cy + 90, small)
        return top + 22 + math.ceil(len(icons) / 8) * row_h

    y = section("Commands (ribbon, toolbars, context menus)", COMMANDS, 118)
    y = section("Navigation node kinds (Explorer, Control Designer and Graphics Designer trees)", NODE_KINDS, y + 10)
    text("State badges (bottom-right of a 16 px node icon; the type icon never changes colour)", margin, y + 10,
         QFont("Segoe UI", 11, QFont.DemiBold))
    p.setPen(QPen(QColor("#D5DCE4"), 1))
    p.drawLine(QPointF(margin, y + 18), QPointF(width - margin, y + 18))
    for index, name in enumerate(icon_set.STATE_BADGES):
        cx = margin + index * col_w
        cy = y + 34
        p.setPen(QPen(QColor("#EEF1F5"), 1))
        p.setBrush(QColor("#F7F9FB"))
        p.drawRoundedRect(QRectF(cx, cy, 72, 72), 6, 6)
        paint("module", cx + 4, cy + 4, 64)
        icon_set.paint_badge(p, name, cx + 46, cy + 46, 24)
        paint("module", cx + 82, cy + 24, 16)
        icon_set.paint_badge(p, name, cx + 92, cy + 34, 7)
        text(name, cx, cy + 90, small)
    note = ("Disabled commands desaturate to grey; checked buttons darken the family colour."
            if style == icon_set.STYLE_COLOUR else
            "Disabled commands use the brand's disabled grey; checked or selected ribbon buttons use a filled variant.")
    text(note, margin, y + 34 + 108, small, muted)
    p.end()
    output.parent.mkdir(parents=True, exist_ok=True)
    assert image.save(str(output)), output
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Render the engineering icon set as a contact sheet.")
    parser.add_argument("output", nargs="?", type=Path, help="PNG to write (default under docs/uiux/images)")
    parser.add_argument("--style", choices=(icon_set.STYLE_LINE, icon_set.STYLE_COLOUR), default=icon_set.STYLE_LINE)
    options = parser.parse_args()
    default = DEFAULT_OUTPUT if options.style == icon_set.STYLE_LINE \
        else DEFAULT_OUTPUT.with_name("icon_proposal_sheet_colour.png")
    print(render(options.output or default, options.style))
