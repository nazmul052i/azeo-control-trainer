"""Advisory checks of retained HMI geometry, shared by Studio and operator preview.

Only foreground readouts participate in overlap checks. Equipment, pipework and
panel backgrounds intentionally overlap and must not drown out actionable advice.
No item or palette is mutated, and every item/pair yields back to the caller.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import islice
import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFontMetricsF

from .publishing import Finding, WARNING
from .rendering.items import PvmItem, PipeItem, StaticItem, item_document_data
from ..theme.tokens import Role, THEMES, DEFAULT_THEME


@dataclass(frozen=True)
class VisualFinding(Finding):
    code: str = ""
    fix: str = ""


def drain(iterator):
    while True:
        try:
            next(iterator)
        except StopIteration as done:
            return done.value


def contrast(first, second):
    def luminance(value):
        color = QColor(value)
        channels = (color.redF(), color.greenF(), color.blueF())
        linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
        return sum(a * b for a, b in zip(linear, (.2126, .7152, .0722)))
    a, b = sorted((luminance(first), luminance(second)))
    return (b + .05) / (a + .05)


def text_size(item):
    """Use the painter's font and wrap policy; never shrink type to hide clipping."""
    font, flags = item.text_layout()
    metrics = QFontMetricsF(font)
    rect = item.rect()
    text = str(item.data.get("text", ""))
    if item.data.get("text_wrap"):
        width = max(rect.width(), max((metrics.horizontalAdvance(word) for word in text.split()), default=1))
        ink = metrics.boundingRect(QRectF(0, 0, width, 100000), int(Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap), text)
        return math.ceil(width), math.ceil(ink.height())
    ink = metrics.boundingRect(QRectF(0, 0, 100000, 100000), int(Qt.AlignLeft | Qt.AlignTop), text)
    return math.ceil(ink.width()), math.ceil(ink.height())


def _color(data, key, role_key, fallback, palette):
    if data.get(key):
        return QColor(data[key])
    role = data.get(role_key)
    return QColor(palette[Role(role)] if role in Role._value2member_map_ else palette[fallback])


def iter_visual_findings(scene, display, theme=DEFAULT_THEME, viewport=None):
    palette = THEMES.get(theme, THEMES[DEFAULT_THEME])
    context = f"{theme}, L{display.level}" + (f", {viewport[0]}×{viewport[1]}" if viewport else ", design size")
    found, foreground, backgrounds = [], [], []
    items = scene.items(Qt.AscendingOrder)
    page = QRectF(0, 0, display.width, display.height)
    if page.isEmpty():
        for item in items:
            yield None
            if isinstance(item, (StaticItem, PvmItem, PipeItem)):
                page = page.united(item.sceneBoundingRect())
    scale = 1.0
    if viewport and display.view_type == "scale_to_frame" and not page.isEmpty():
        # QGraphicsView.fitInView leaves two logical pixels at each edge.
        scale = min(max(1, viewport[0] - 4) / page.width(), max(1, viewport[1] - 4) / page.height())
    elif viewport and (page.width() > viewport[0] or page.height() > viewport[1]):
        found.append(VisualFinding(WARNING, f"Actual-size display exceeds the viewport ({context}); some content requires scrolling", "display", "viewport_clipping"))
    # Backgrounds are indexed spatially along with readouts below. Only opaque,
    # unrotated rectangles can establish a known background for text contrast.
    for item in items:
        yield None
        if not isinstance(item, (StaticItem, PvmItem)):
            continue
        data = item_document_data(item)
        if not item.isVisible() or not data.get("visible", True) or not getattr(item, "pvm_visible", True):
            continue
        identity = str(getattr(getattr(item, "pvm", None), "id", "") or data.get("id", ""))
        rect = item.rect()
        bounds = item.mapRectToScene(rect)
        if isinstance(item, PvmItem):
            width, height = item.content_design_size()
            if rect.width() + .5 < width or rect.height() + .5 < height:
                found.append(VisualFinding(WARNING, f"PVM below its standard {width:g} × {height:g} size ({context})", identity, "small_pvm", "Restore standard size"))
            if min(rect.width() / width, rect.height() / height) * scale < .75:
                found.append(VisualFinding(WARNING, f"PVM readout scales below 75% of standard size ({context}); enlarge the layout or use a closer display level", identity, "viewport_readability"))
            foreground.append((identity, bounds, item))
        elif data.get("kind") == "rect" and not item.rotation():
            color = _color(data, "fill", "fill_role", Role.SURFACE_PANEL, palette)
            if (data.get("fill") or data.get("fill_role")) and color.isValid() and color.alpha() == 255 and not data.get("props"):
                backgrounds.append((bounds, color, item))
        elif data.get("kind") in {"text", "datalink"}:
            if data.get("kind") == "text":
                if not str(data.get("text", "")).strip():
                    continue
                width, height = text_size(item)
                if rect.width() + .5 < width or rect.height() + .5 < height:
                    found.append(VisualFinding(WARNING, f"Label needs at least {width:g} × {height:g} drawing units ({context})", identity, "clipped_text", "Fit text"))
                font, _ = item.text_layout()
                if font.pointSizeF() * scale < 7:
                    found.append(VisualFinding(WARNING, f"Text smaller than 7 pt at this viewport ({context})", identity, "viewport_readability"))
                bounds = item.mapRectToScene(item.routing_rect()).intersected(bounds)
            foreground.append((identity, bounds, item))
    # Sweep on X excludes distant objects before checking their actual ink.
    ordered = sorted(foreground, key=lambda row: row[1].left())
    for index, (identity, bounds, item) in enumerate(ordered):
        yield None
        if bounds.isEmpty():
            continue
        for other_id, other_bounds, _other in islice(ordered, index + 1, None):
            yield None
            if other_bounds.left() >= bounds.right():
                break
            intersection = bounds.intersected(other_bounds)
            if intersection.width() > 2 and intersection.height() > 2:
                found.append(VisualFinding(WARNING, f"Readout overlaps {other_id} ({context}); check their spacing", identity, "readout_overlap"))
                # One actionable collision per readout avoids quadratic reports
                # for accidentally stacked duplicates. The next edit rechecks.
                break
        data = item_document_data(item)
        if data.get("kind") != "text" or data.get("props"):
            continue
        background = QColor(display.background or palette[Role.SURFACE_BG])
        for panel_bounds, color, panel in backgrounds:
            yield None
            if panel_bounds.contains(bounds) and panel.zValue() <= item.zValue():
                background = color
        foreground_color = _color(data, "text_color", "text_role", Role.TEXT_DIM, palette)
        if data.get("line") and not (data.get("text_color") or data.get("text_role")):
            foreground_color = QColor(data["line"])
        if foreground_color.alpha() == 255 and background.isValid():
            ratio = contrast(foreground_color, background)
            if ratio < 4.5:
                found.append(VisualFinding(WARNING, f"Text contrast {ratio:.2f}:1 below the 4.5:1 authoring target ({context}); use text and surface roles", identity, "low_contrast"))
    from .rendering.crossing import iter_connection_issues
    for identity, message in (yield from iter_connection_issues(scene)):
        found.append(VisualFinding(WARNING, message, identity, "pipe_contact"))
    return tuple(found)


def visual_findings(scene, display, theme=DEFAULT_THEME, viewport=None):
    return drain(iter_visual_findings(scene, display, theme, viewport))
