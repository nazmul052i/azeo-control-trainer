"""The three things that live on the canvas.

- **PvmItem** — one placed PVM. Geometry plus fill/line appearance belong
  to the placement; process behavior still rebuilds from the class.
- **StaticItem** — a drawing or data item. Most are decoration;
  Data Links own a read binding and Display Links own navigation.
- **PipeItem** — an orthogonal run between two items' anchors,
  re-routed whenever either end moves.

All three reach the studio through `scene.studio`, which is how a
gesture on an item becomes an undo step on the document.
"""
from __future__ import annotations

import copy
import logging
import time
import uuid
import weakref

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPainterPathStroker, QPen,
    QTransform,
)
from PySide6.QtWidgets import (
    QGraphicsItem, QGraphicsProxyWidget, QGraphicsRectItem,
)

from azeo_control_trainer.core.hmi.pvms.base import Pvm, registry, resolve_state
from azeo_control_trainer.core.hmi.pvms.shapes import SHAPE_KINDS
from azeo_control_trainer.core.hmi.theme.roles import Role
from .chrome import PVM_W, PVM_H, MODE_EDIT, WF, _mode_chip_colour
from .crossing import (
    CROSSOVER_KINDS, draw_crossed_polyline, stroke_points,
)
from .interaction import (
    ConnectorGestureKernel, hit_rect_handle, rect_handle_points, scene_radius,
    visible_connector_sides,
)

#: The strip ABOVE a PVM that the HP display tag occupies.
#: `boundingRect` has to cover it or Qt never repaints it.
TAG_BAND = 22.0

# A connector may terminate anywhere along an object's perimeter. The token
# stores the owning edge and a normalized distance along it, rather than a
# scene coordinate, so the attachment survives move and resize operations.
FREE_EDGE_PREFIX = "edge:"
OUTLINE_PREFIX = "outline:"


def item_document_data(item) -> dict:
    """Return authored metadata without confusing it with Qt's ``data()``.

    ``QGraphicsItem`` exposes a built-in ``data(key)`` method. PVM items do
    not own our dictionary attribute, so callers must never use ``.get`` on
    an unchecked ``getattr(item, "data")`` result.
    """
    metadata = getattr(item, "data", None)
    return metadata if isinstance(metadata, dict) else {}


def item_group_id(item) -> str:
    """Return the logical group shared by PVMs, drawings, and pipes."""
    pvm = getattr(item, "pvm", None)
    if pvm is not None:
        return str(getattr(pvm, "group", "") or "")
    return str(item_document_data(item).get("group", "") or "")


def _authored_colour(data, palette, field, role_field, fallback):
    # Literal and animated colors keep precedence; only opted-in artwork follows roles.
    if data.get(field):
        return QColor(data[field])
    role = data.get(role_field)
    return QColor(palette[Role(role) if role in Role._value2member_map_ else fallback])


def _stream_connector_path(rect: QRectF) -> QPainterPath:
    """Compact off-page process continuation used by Studio and Runtime."""
    tip = min(max(rect.height() * 0.48, 8.0), rect.width() * 0.22)
    path = QPainterPath(QPointF(rect.left(), rect.top()))
    path.lineTo(rect.right() - tip, rect.top())
    path.lineTo(rect.right(), rect.center().y())
    path.lineTo(rect.right() - tip, rect.bottom())
    path.lineTo(rect.left(), rect.bottom())
    path.closeSubpath()
    return path


def _free_edge_token(normal: str, fraction: float) -> str:
    return f"{FREE_EDGE_PREFIX}{normal}:{max(0.0, min(1.0, fraction)):.4f}"


def _free_edge_spec(token: str) -> tuple[str, float] | None:
    if not str(token).startswith(FREE_EDGE_PREFIX):
        return None
    try:
        _prefix, normal, raw_fraction = str(token).split(":", 2)
        fraction = max(0.0, min(1.0, float(raw_fraction)))
    except (TypeError, ValueError):
        return None
    if normal not in ("n", "s", "e", "w"):
        return None
    return normal, fraction


def _edge_point(rect: QRectF, token: str) -> QPointF | None:
    spec = _free_edge_spec(token)
    if spec is None:
        return None
    normal, fraction = spec
    if normal == "n":
        return QPointF(rect.left() + rect.width() * fraction, rect.top())
    if normal == "s":
        return QPointF(rect.left() + rect.width() * fraction, rect.bottom())
    if normal == "w":
        return QPointF(rect.left(), rect.top() + rect.height() * fraction)
    return QPointF(rect.right(), rect.top() + rect.height() * fraction)


def _nearest_edge(rect: QRectF, point: QPointF) \
        -> tuple[str, QPointF, float]:
    """Nearest point on a rectangle's continuous perimeter.

    Distance is returned squared because callers only compare it with another
    squared hit radius. Segment projection, rather than merely choosing the
    nearest cardinal midpoint, is what makes every perimeter pixel usable.
    """
    x = max(rect.left(), min(rect.right(), point.x()))
    y = max(rect.top(), min(rect.bottom(), point.y()))
    candidates = (
        ("n", QPointF(x, rect.top())),
        ("s", QPointF(x, rect.bottom())),
        ("w", QPointF(rect.left(), y)),
        ("e", QPointF(rect.right(), y)),
    )
    normal, projected = min(
        candidates,
        key=lambda row: ((row[1].x() - point.x()) ** 2
                         + (row[1].y() - point.y()) ** 2))
    distance = ((projected.x() - point.x()) ** 2
                + (projected.y() - point.y()) ** 2)
    fraction = ((projected.x() - rect.left()) / max(rect.width(), 1e-9)) \
        if normal in ("n", "s") else \
        ((projected.y() - rect.top()) / max(rect.height(), 1e-9))
    return _free_edge_token(normal, fraction), projected, distance


def _outline_token(normal: str, fx: float, fy: float) -> str:
    return (f"{OUTLINE_PREFIX}{normal}:"
            f"{max(0.0, min(1.0, fx)):.4f}:"
            f"{max(0.0, min(1.0, fy)):.4f}")


def _outline_spec(token: str) -> tuple[str, float, float] | None:
    if not str(token).startswith(OUTLINE_PREFIX):
        return None
    try:
        _prefix, normal, raw_x, raw_y = str(token).split(":", 3)
        fx, fy = float(raw_x), float(raw_y)
    except (TypeError, ValueError):
        return None
    if normal not in ("n", "e", "s", "w") \
            or not (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0):
        return None
    return normal, fx, fy


def _transform_outline_point(rect: QRectF, fx: float, fy: float, *,
                             rotation: float = 0.0,
                             mirror_x: bool = False,
                             mirror_y: bool = False) -> QPointF:
    """Map an SVG-view-box fraction through its visual transforms."""
    import math
    x = rect.left() + rect.width() * (1.0 - fx if mirror_x else fx)
    y = rect.top() + rect.height() * (1.0 - fy if mirror_y else fy)
    if abs(rotation) <= 1e-9:
        return QPointF(x, y)
    centre = rect.center()
    radians = math.radians(rotation)
    dx, dy = x - centre.x(), y - centre.y()
    return QPointF(centre.x() + dx * math.cos(radians)
                   - dy * math.sin(radians),
                   centre.y() + dx * math.sin(radians)
                   + dy * math.cos(radians))


def _transform_normal(normal: str, *, rotation: float = 0.0,
                      mirror_x: bool = False,
                      mirror_y: bool = False) -> str:
    """Turn an SVG boundary normal into the router's cardinal direction."""
    import math
    x, y = {"n": (0.0, -1.0), "e": (1.0, 0.0),
            "s": (0.0, 1.0), "w": (-1.0, 0.0)}[normal]
    x *= -1.0 if mirror_x else 1.0
    y *= -1.0 if mirror_y else 1.0
    radians = math.radians(rotation)
    rx = x * math.cos(radians) - y * math.sin(radians)
    ry = x * math.sin(radians) + y * math.cos(radians)
    if abs(rx) >= abs(ry):
        return "e" if rx >= 0 else "w"
    return "s" if ry >= 0 else "n"


_SYMBOL_PORT_ALIASES = {
    "n": "n", "top": "n",
    "e": "e", "right": "e", "outlet": "e",
    "s": "s", "bottom": "s",
    "w": "w", "left": "w", "inlet": "w",
    "suction": "w", "discharge": "e", "vent": "n", "drain": "s",
}


def _declared_symbol_anchor(rect: QRectF, symbol: str, side: str, *,
                            rotation: float = 0.0,
                            mirror_x: bool = False,
                            mirror_y: bool = False) -> QPointF | None:
    """Resolve a semantic side through the SVG's authored process port.

    The SVG catalogue carries nozzle/discharge coordinates.  A pump's east
    port, for example, is intentionally above its visual centre.  Treating
    east as the middle of the host rectangle creates the short vertical
    doglegs engineers were seeing beside valves.
    """
    from azeo_control_trainer.core.hmi.pvms.symbols import connection_ports

    normal = _SYMBOL_PORT_ALIASES.get(str(side).lower())
    point = connection_ports(symbol).get(normal) if normal else None
    if point is None:
        return None
    return _transform_outline_point(
        rect, point[0], point[1], rotation=rotation,
        mirror_x=mirror_x, mirror_y=mirror_y)


def _symbol_outline_anchor(rect: QRectF, token: str, *,
                           rotation: float = 0.0,
                           mirror_x: bool = False,
                           mirror_y: bool = False) -> QPointF | None:
    spec = _outline_spec(token)
    if spec is None:
        return None
    _normal, fx, fy = spec
    return _transform_outline_point(
        rect, fx, fy, rotation=rotation,
        mirror_x=mirror_x, mirror_y=mirror_y)


def _nearest_symbol_outline(rect: QRectF, symbol: str, point: QPointF, *,
                            rotation: float = 0.0,
                            mirror_x: bool = False,
                            mirror_y: bool = False):
    """Nearest point on the visible SVG ink, not its hosting rectangle."""
    from azeo_control_trainer.core.hmi.pvms.symbols import outline_points
    candidates = outline_points(symbol)
    best = None
    for fx, fy, normal in candidates:
        projected = _transform_outline_point(
            rect, fx, fy, rotation=rotation,
            mirror_x=mirror_x, mirror_y=mirror_y)
        distance = ((projected.x() - point.x()) ** 2
                    + (projected.y() - point.y()) ** 2)
        if best is None or distance < best[0]:
            actual_normal = _transform_normal(
                normal, rotation=rotation,
                mirror_x=mirror_x, mirror_y=mirror_y)
            best = (distance,
                    _outline_token(actual_normal, fx, fy), projected)
    if best is None:
        return _nearest_edge(rect, point)
    distance, token, projected = best
    return token, projected, distance


def _axis_symbol_outline_token(rect: QRectF, symbol: str, point: QPointF,
                               side: str, *, horizontal: bool,
                               rotation: float = 0.0,
                               mirror_x: bool = False,
                               mirror_y: bool = False) -> str:
    """An outline token whose endpoint lies on an exact process axis.

    Alpha-outline samples are discrete. Choosing the nearest sample for each
    item independently can put two nominally horizontal endpoints several
    pixels apart. This finds the outside intersection on the nearest raster
    row/column, then preserves the requested axis coordinate in the normalized
    token. The adjustment is sub-raster visually but mathematically exact for
    the router.
    """
    import math

    from azeo_control_trainer.core.hmi.pvms.symbols import outline_points

    samples = []
    for fx, fy, raw_normal in outline_points(symbol):
        projected = _transform_outline_point(
            rect, fx, fy, rotation=rotation,
            mirror_x=mirror_x, mirror_y=mirror_y)
        delta = abs(projected.y() - point.y()) if horizontal \
            else abs(projected.x() - point.x())
        samples.append((delta, projected, raw_normal))
    if not samples:
        return _nearest_edge(rect, point)[0]
    nearest = min(row[0] for row in samples)
    # Keep a whole raster row/column. The SVG hit raster is at most 192 px,
    # so this tolerance is just over one sample at the authored size.
    tolerance = ((rect.height() if horizontal else rect.width()) / 190.0
                 + 1e-6)
    row = [entry for entry in samples
           if entry[0] <= nearest + tolerance]
    if side in ("e", "s"):
        chosen = max(row, key=lambda entry:
                     entry[1].x() if horizontal else entry[1].y())
    else:
        chosen = min(row, key=lambda entry:
                     entry[1].x() if horizontal else entry[1].y())
    projected = QPointF(chosen[1])
    if horizontal:
        projected.setY(point.y())
    else:
        projected.setX(point.x())

    # Invert `_transform_outline_point`: inverse rotation first, then mirror.
    centre = rect.center()
    radians = math.radians(-rotation)
    dx, dy = projected.x() - centre.x(), projected.y() - centre.y()
    unrotated = QPointF(
        centre.x() + dx * math.cos(radians) - dy * math.sin(radians),
        centre.y() + dx * math.sin(radians) + dy * math.cos(radians))
    fx = (unrotated.x() - rect.left()) / max(rect.width(), 1e-9)
    fy = (unrotated.y() - rect.top()) / max(rect.height(), 1e-9)
    if mirror_x:
        fx = 1.0 - fx
    if mirror_y:
        fy = 1.0 - fy
    return _outline_token(side, fx, fy)


def _nearest_path_outline(path: QPainterPath, rect: QRectF, point: QPointF):
    """Nearest point on a drawn primitive's real outline.

    Rectangle projection is correct for a panel, but visibly wrong for an
    ellipse, triangle, star, or rounded rectangle: the pipe appears glued to
    empty space in the item's host box.  Sampling the same ``QPainterPath``
    used by the painter keeps the attachment vocabulary normalized while
    making every visible point of those outlines addressable.
    """
    if path.isEmpty() or rect.width() <= 0 or rect.height() <= 0:
        return _nearest_edge(rect, point)
    # The acquisition radius is screen-sized, so 512 samples is well below a
    # pixel at normal authoring sizes and remains cheap during mouse motion.
    best = None
    samples = 512
    centre = rect.center()
    for index in range(samples):
        projected = path.pointAtPercent(index / samples)
        distance = ((projected.x() - point.x()) ** 2
                    + (projected.y() - point.y()) ** 2)
        if best is None or distance < best[0]:
            dx, dy = projected.x() - centre.x(), projected.y() - centre.y()
            normal = ("e" if dx >= 0 else "w") if abs(dx) >= abs(dy) \
                else ("s" if dy >= 0 else "n")
            fx = (projected.x() - rect.left()) / max(rect.width(), 1e-9)
            fy = (projected.y() - rect.top()) / max(rect.height(), 1e-9)
            best = (distance, _outline_token(normal, fx, fy), projected)
    return best[1], best[2], best[0]


def _resize_cursor(handle: str | None):
    """Native cursor for the edge(s) a transform handle owns."""
    return {
        "n": Qt.SizeVerCursor, "s": Qt.SizeVerCursor,
        "e": Qt.SizeHorCursor, "w": Qt.SizeHorCursor,
        "nw": Qt.SizeFDiagCursor, "se": Qt.SizeFDiagCursor,
        "ne": Qt.SizeBDiagCursor, "sw": Qt.SizeBDiagCursor,
        "rotate": Qt.CrossCursor,
    }.get(handle)

class PvmItem(QGraphicsRectItem):
    """One placed PVM: card face, tag row with a mode chip, value rows,
    a mini output bar, alarm border and triangle, dashes + FORCED under
    bad quality, square selection handles.

    **Every colour the operator sees comes from a theme role**, through
    `colour()` and `state_colour()` below. `WF` is studio chrome —
    selection borders, anchor dots, hover rings — and appears here only
    in paint paths that EDIT mode alone reaches. That division is what
    makes the canvas WYSIWYG: retheme the station and the card follows
    its own pipes instead of staying wireframe-white on a dark screen.
    """

    #: Card content -> theme role. The alarm entries are the fills and
    #: marks; text on a panel takes the `_TEXT` companion instead,
    #: because no one hue clears both a light panel and a dark one.
    FACE = Role.SURFACE_PANEL
    BORDER = Role.LINE_SOFT
    TRACK = Role.SURFACE_SUNK
    BAR = Role.BAR_OUT
    #: Appearance overrides affect ordinary material only. Alarm and status
    #: roles retain their semantic colours regardless of PVM decoration.
    FILL_ROLES = frozenset({Role.SURFACE_PANEL, Role.EQUIPMENT_FILL})
    LINE_ROLES = frozenset({Role.LINE, Role.LINE_SOFT, Role.EQUIPMENT})
    #: Every rectangular PVM uses the same eight authoring handles as a
    #: drawing element.  The squares were already painted, but until this
    #: gesture implementation they were a visual promise with no behavior.
    HANDLE_SPEC = {
        "nw": ("n", "w"), "n": ("n",), "ne": ("n", "e"),
        "w": ("w",), "e": ("e",),
        "sw": ("s", "w"), "s": ("s",), "se": ("s", "e"),
    }
    HANDLE_HIT = 9.0
    MIN_W = 28.0
    MIN_H = 24.0

    @staticmethod
    def shows_selection_frame() -> bool:
        """PVM artwork must not acquire a rectangular authoring cage."""
        return False

    def shows_operational_alarm_box(self) -> bool:
        """Operational state furniture belongs to Test and Station views.

        An unbound class is honestly Bad while being configured, but drawing
        its Level-1 alarm rectangle in Edit mode makes the PVM look as though
        it owns a permanent container. Test mode still previews the exact
        operator result, and the published renderer has no Studio back-link.
        """
        scene = self.scene()
        studio = getattr(scene, "studio", None) if scene else None
        return studio is None or studio.mode != MODE_EDIT

    #: PVM state token -> role, the same table `render.py` uses for the
    #: faceplate. A card and its own faceplate disagreeing about what
    #: an alarm looks like is the WYSIWYG failure in miniature.
    STATE_ROLES = {
        "signal.critical": Role.ALARM_P1,
        "signal.warning": Role.ALARM_P2,
        "signal.advisory": Role.ALARM_P3,
        "signal.hold": Role.ACTION,
        "state.bad_quality": Role.TEXT_FAINT,
        "text.dimmed": Role.TEXT_DIM,
    }
    STATE_TEXT_ROLES = {
        Role.ALARM_P1: Role.ALARM_P1_TEXT,
        Role.ALARM_P2: Role.ALARM_P2_TEXT,
        Role.ALARM_P3: Role.ALARM_P3_TEXT,
    }

    def __init__(self, pvm: Pvm, binding, mode_binding, palette,
                 rows: dict | None = None, connection_points=(),
                 status_conditions=(), typography=None):
        super().__init__(0, 0, pvm.w or PVM_W, pvm.h or PVM_H)
        self.pvm = pvm
        self.binding = binding
        self.mode_binding = mode_binding
        self.rows = rows or {}
        self.connection_points = tuple(dict(point)
                                       for point in connection_points)
        self.status_conditions = tuple(status_conditions or ())
        if typography is None:
            from azeo_control_trainer.core.hmi.pvms.typography import PvmTypography
            typography = PvmTypography()
        self.typography = typography
        from collections import deque
        #: Display-side sample history for sparkline painters.
        self.history = deque(maxlen=200)
        self.set_palette(palette)
        self._hover = False
        self._resizing = False
        # Painters describe a PVM in its class's canonical design units.
        # The placement rect may be any size; paint() maps the COMPLETE
        # composition into it.  Keeping the design size class-owned makes a
        # saved/resized PVM render identically after reopening the display.
        self._content_rect_override: QRectF | None = None
        self.pvm_visible = bool(pvm.visible)
        self.pvm_locked = bool(pvm.locked)
        self.setVisible(self.pvm_visible)
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.ItemIsMovable)
        self.setFlag(QGraphicsItem.ItemIsSelectable)
        # Restore saved geometry before the lock can reject position changes.
        # Otherwise locked PVMs reopen at the canvas origin.
        self.setPos(pvm.x, pvm.y)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges)
        self.setZValue(pvm.z)
        self._apply_pvm_rotation()
        self._refresh_machine_readout()

    def _refresh_machine_readout(self):
        if self.pvm.variant in ("vfd", "turbine_speed", "compressor_speed"):
            from ..machines import machine_readout
            self._machine_readout = machine_readout(
                self.pvm, self.rows, self.binding.result if self.binding else None)

    def sample_history(self):
        self._refresh_machine_readout()
        from ..vessel_trend import finite_good
        value = finite_good(self.binding.result if self.binding else None)
        # A gap prevents the vessel trace from drawing through lost evidence.
        if value is not None or self.pvm.variant == "vessel":
            self.history.append(value)

    # -------------------------------------------------- colour
    def set_palette(self, palette) -> None:
        """Apply a theme, then layer this placement's fill/line over it."""
        self._theme_palette = palette
        # Runtime viewers pass a RolePalette while older authoring callers
        # pass a role dictionary. Normalize both at this boundary so explicit
        # placement colours cannot make a valid theme fail during rendering.
        self._palette = {
            role: palette.hex(role) for role in Role
        } if callable(getattr(palette, "hex", None)) else dict(palette)
        for value, roles in ((self.pvm.fill, self.FILL_ROLES),
                             (self.pvm.line, self.LINE_ROLES)):
            colour = QColor(value)
            if value and colour.isValid():
                for role in roles:
                    self._palette[role] = value

    def colour(self, role: Role) -> QColor:
        """One role, resolved against the theme and placement appearance."""
        return QColor(self._palette[role])

    def state_colour(self, state, on_fill: bool = False) -> QColor:
        """The colour a DisplayState renders in.

        `on_fill` picks the mark colour (an alarm fill is theme-
        invariant); otherwise text gets the `_TEXT` companion so the
        word stays legible against whatever panel is behind it.
        """
        role = self.STATE_ROLES.get(
            getattr(state, "token", ""), Role.TEXT) \
            if state is not None else Role.TEXT
        if not on_fill:
            role = self.STATE_TEXT_ROLES.get(role, role)
        return self.colour(role)

    def typeface(self, role: str, family: str, point_size: float,
                 weight=QFont.Normal) -> QFont:
        """A class-profile font for one semantic part of the PVM.

        Painters still supply their measured family and size.  Standard keeps
        those values exactly; another declared profile may replace the family
        or express the size as points/percentage.  No placement owns either.
        """
        font = QFont(self.typography.family(role, family))
        font.setPointSizeF(self.typography.point_size(role, point_size))
        font.setWeight(weight)
        return font

    # -------------------------------------------------- rotation
    def _bar_swapped(self) -> bool:
        """An AI bar under a quarter turn becomes its horizontal /
        vertical counterpart — a LAYOUT swap with upright text,
        never numbers on their side."""
        return (self.pvm.block_type == "AI"
                and self.pvm.role == "dynamo_inline"
                and self.pvm.variant in ("", "hbar")
                and (self.pvm.rot or 0) % 180 == 90)

    def effective_variant(self) -> str:
        if self._bar_swapped():
            return "hbar" if self.pvm.variant == "" else ""
        return self.pvm.variant

    def _glyph_rotated(self) -> bool:
        """Compact equipment PVMs rotate the SILHOUETTE in place —
        the tag, state text and badge stay upright and in position
        (the DynaLive rule: gravity does not rotate with a drum)."""
        return (self.pvm.role == "dynamo_compact"
                and (self.pvm.rot or 0) % 360 != 0)

    def _apply_pvm_rotation(self) -> None:
        rot = (self.pvm.rot or 0) % 360
        if self._bar_swapped():
            # The footprint turns with the layout.
            self.setRotation(0)
            self.setRect(0, 0, self.pvm.h or PVM_H,
                         self.pvm.w or PVM_W)
            return
        self.setRect(0, 0, self.pvm.w or PVM_W, self.pvm.h or PVM_H)
        if self.pvm.role == "dynamo_compact":
            self.setRotation(0)     # glyph-only, in _paint_symbol
            return
        self.setTransformOriginPoint(self.rect().center())
        self.setRotation(rot)

    def rect(self) -> QRectF:
        """The painter's design rect, or the real placement rect.

        Custom PVM painters all call ``item.rect()``.  Supplying their
        canonical rect while paint() has its content transform installed is
        the one shared resize contract that covers every registered class;
        authoring geometry continues to see the real rect at every other
        time.
        """
        override = getattr(self, "_content_rect_override", None)
        return override if override is not None else super().rect()

    def _actual_rect(self) -> QRectF:
        """The persisted placement footprint, ignoring paint overrides."""
        return QGraphicsRectItem.rect(self)

    def content_design_size(self) -> tuple[float, float]:
        """Return this PVM class's stable, unscaled design footprint.

        This mirrors the placement rule in the assembler.  It must not use
        the current ``pvm.w/h``: doing that would make a resized PVM forget
        its scale after save/reopen and regress to fixed-size inner content.
        """
        pvm_cls = registry.get(self.pvm.block_type, self.pvm.role,
                               self.pvm.variant)
        size = getattr(pvm_cls, "DEFAULT_SIZE", None) \
            if pvm_cls is not None else None
        if size is None:
            from azeo_control_trainer.core.hmi.pvms.symbols import pvm_symbol
            if pvm_symbol(self.pvm.block_type) is not None:
                size = (84.0, 88.0)
            else:
                from .chrome import ROLE_SIZES
                size = ROLE_SIZES.get(self.pvm.role, (PVM_W, PVM_H))
        width, height = float(size[0]), float(size[1])
        if self._bar_swapped():
            width, height = height, width
        return max(width, 1.0), max(height, 1.0)

    def content_scale(self) -> tuple[float, float]:
        """Uniform scale applied to every painted child of this PVM.

        A placement may be wider or taller than its class's canonical
        footprint, but glyphs and type must never be stretched to match that
        aspect ratio.  The painter receives a responsive logical rectangle
        for the remaining width or height instead.
        """
        rect = self._actual_rect()
        design_w, design_h = self.content_design_size()
        scale = min(rect.width() / design_w, rect.height() / design_h)
        scale = max(scale, 1e-6)
        return scale, scale

    def content_layout_size(self) -> tuple[float, float]:
        """Responsive logical rectangle presented to a PVM painter.

        Extra width or height becomes layout space in canonical units rather
        than a second, independent painter scale.  Rect-relative bars and
        backgrounds still fill the placement while text, icons, strokes,
        alarm marks, and symbols keep their intended proportions.
        """
        rect = self._actual_rect()
        scale, _ = self.content_scale()
        return rect.width() / scale, rect.height() / scale

    def boundingRect(self) -> QRectF:               # noqa: N802
        # Selection anchors, hover rings and the dashed border all
        # paint a few pixels PAST the rect; without this margin a drag
        # leaves their outer slivers behind as a trail of dashes (the
        # same residue the pipes fixed with their 16 px margin).
        # The TOP needs more: the HP display tag is drawn ABOVE the
        # rect (13 px up, 12 px tall), so 8 px would leave Qt never
        # damaging the band the tag occupies — the tag would smear
        # behind a drag exactly as the rotation knob once did.
        rect = self._actual_rect()
        scale_x, scale_y = self.content_scale()
        # The HP tag is 13 design units above the PVM.  Once content scales,
        # that ink can extend farther than the old fixed TAG_BAND; report the
        # truth to Qt or enlarged tags smear behind a drag.
        # Selected items expose connector handles outside the resize frame.
        # Claim that authoring ink unconditionally: selection changes do not
        # call prepareGeometryChange(), so a conditional bounding rect would
        # leave stale handles behind after the item moves.
        side = max(24.0, 3.0 * abs(scale_x))
        bottom = max(24.0, 3.0 * abs(scale_y))
        top = max(TAG_BAND, 24.0, 16.0 * abs(scale_y))
        return rect.adjusted(-side, -top, side, bottom)

    def shape(self):
        """Include visible outer connector handles in Qt hit-testing."""
        path = QPainterPath()
        path.addRect(self._actual_rect())
        studio = getattr(self.scene(), "studio", None) \
            if self.scene() is not None else None
        sides = visible_connector_sides(self, studio)
        if sides:
            radius = self._anchor_hit_radius(8.0)
            for side in sides:
                point = self.connection_handle(side)
                path.addEllipse(point, radius, radius)
        return path

    def hoverEnterEvent(self, event) -> None:       # noqa: N802
        self._hover = True
        # Build it NOW: paint is not guaranteed to run before Qt asks
        # for the tooltip, and a first hover showing the previous
        # PVM's text would be worse than showing none.
        self._refresh_hover_text(self.hp_state())
        self.update()
        studio = getattr(self.scene(), "studio", None)
        if studio is not None:
            studio.show_snippet(self, event.screenPos())
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:       # noqa: N802
        self._hover = False
        self._hover_anchor = None
        self.unsetCursor()
        self.update()
        studio = getattr(self.scene(), "studio", None)
        if studio is not None:
            studio.hide_snippet()
        super().hoverLeaveEvent(event)

    def hoverMoveEvent(self, event) -> None:        # noqa: N802
        """Advertise a connector before the engineer has to find it.

        Ports are a screen interaction, so their hit target remains generous
        even while the page is fitted below 100%.  The routed endpoint still
        lands on the exact authored anchor; only the acquisition target is
        forgiving.
        """
        studio = getattr(self.scene(), "studio", None)
        editable = studio is not None and studio.mode == MODE_EDIT
        handle = self.handle_at(event.pos()) if editable \
            and self.isSelected() \
            and not getattr(studio, "connect_armed", False) else None
        connection_intent = bool(getattr(studio, "connect_armed", False)) \
            or not self.isSelected()
        side = self.connection_anchor_at(
            self.mapToScene(event.pos()), allow_inside=False) \
            if editable and handle is None and connection_intent else None
        if side != getattr(self, "_hover_anchor", None):
            self._hover_anchor = side
            self.update()
        cursor = _resize_cursor(handle)
        if cursor is not None:
            self.setCursor(cursor)
        elif side:
            self.setCursor(Qt.CrossCursor)
        else:
            self.unsetCursor()
        super().hoverMoveEvent(event)

    def itemChange(self, change, value):            # noqa: N802
        scene = self.scene()
        studio = getattr(scene, "studio", None) if scene else None
        if change == QGraphicsItem.ItemPositionChange \
                and getattr(self, "pvm_locked", False):
            return self.pos()
        if change == QGraphicsItem.ItemPositionChange \
                and studio is not None:
            return studio.canvas.snap_item_position(self, value)
        if change == QGraphicsItem.ItemPositionHasChanged:
            # Geometry is the ONE thing a placement owns (I7) — track it
            # on a fresh frozen record.
            self.pvm = Pvm(**{**self.pvm.__dict__,
                              "x": self.pos().x(), "y": self.pos().y()})
            if studio is not None:
                studio.mark_unsaved()
                studio.reroute_pipes(self)
        return super().itemChange(change, value)

    # ------------------------------------------------------------ resize
    def handle_points(self) -> dict[str, QPointF]:
        """The eight visible resize handles in item coordinates."""
        return rect_handle_points(self.rect())

    def handle_at(self, pos) -> str | None:
        if getattr(self, "pvm_locked", False):
            return None
        return hit_rect_handle(self, pos, pixels=self.HANDLE_HIT)

    def _resize_to(self, handle: str, scene_pos,
                   bypass_snap: bool = False) -> None:
        """Resize a PVM placement while keeping the opposite edges fixed."""
        edges = self.HANDLE_SPEC.get(handle)
        if not edges or getattr(self, "pvm_locked", False):
            return
        origin = self.pos()
        rect = self.rect()
        left, top = origin.x(), origin.y()
        right, bottom = left + rect.width(), top + rect.height()
        point = self.mapToParent(self.mapFromScene(scene_pos))
        if "w" in edges:
            left = min(point.x(), right - self.MIN_W)
        if "e" in edges:
            right = max(point.x(), left + self.MIN_W)
        if "n" in edges:
            top = min(point.y(), bottom - self.MIN_H)
        if "s" in edges:
            bottom = max(point.y(), top + self.MIN_H)
        width, height = right - left, bottom - top
        studio = getattr(self.scene(), "studio", None) \
            if self.scene() else None
        if not bypass_snap and studio is not None \
                and getattr(studio, "snap_enabled", False):
            grid = 8.0
            if "w" in edges or "e" in edges:
                width = max(self.MIN_W, round(width / grid) * grid)
                if "w" in edges:
                    left = right - width
            if "n" in edges or "s" in edges:
                height = max(self.MIN_H, round(height / grid) * grid)
                if "n" in edges:
                    top = bottom - height
        # A quarter-turned analog bar swaps its layout instead of rotating
        # text.  Persist logical w/h so reloading recreates this same visual
        # rectangle rather than swapping the engineer's new size twice.
        stored_w, stored_h = (height, width) \
            if self._bar_swapped() else (width, height)
        self.pvm = Pvm(**{
            **self.pvm.__dict__, "x": left, "y": top,
            "w": stored_w, "h": stored_h,
        })
        self.setPos(left, top)
        self.prepareGeometryChange()
        self._apply_pvm_rotation()
        self.update()

    def mousePressEvent(self, event) -> None:       # noqa: N802
        scene = self.scene()
        studio = getattr(scene, "studio", None) if scene else None
        if studio is not None:
            studio.gesture_checkpoint()   # one undo step per drag
        if getattr(self, "pvm_locked", False):
            super().mousePressEvent(event)
            return
        # A selected transform frame is authoritative. Named class ports can
        # occupy the exact same coordinate as an edge handle; allowing the
        # port to win made width/height resizing impossible on those PVMs.
        # The separate outer port remains available, and Connect mode may
        # intentionally choose a coincident semantic port.
        if self.isSelected() and not getattr(studio, "connect_armed", False):
            handle = self.handle_at(event.pos())
            if handle is not None:
                self._resize_handle = handle
                self._resizing = True
                event.accept()
                return
        # An unselected compact PVM can be smaller than the screen-space
        # connector acquisition band. Defer that ambiguous press: a release
        # selects the valve, while a real drag still starts a pipe from the
        # exact outline point. Once selected, normal pointer gestures own the
        # transform frame; the Line/Connector tool expresses connection intent.
        connection_intent = bool(getattr(studio, "connect_armed", False)) \
            or not self.isSelected()
        if studio is not None and studio.mode == MODE_EDIT \
                and connection_intent:
            side = self.connection_anchor_at(
                event.scenePos(), allow_inside=False)
            if side is not None and ConnectorGestureKernel.press(
                    self, studio, side, event.scenePos()):
                event.accept()
                return
        super().mousePressEvent(event)
        if studio is not None and self.isSelected():
            group = item_group_id(self)
            if group and not (event.modifiers() & Qt.AltModifier):
                members = [item for item in studio._groupable_items()
                           if item_group_id(item) == group]
                studio.selection.extend(members, primary=self)
            else:
                studio.selection.set_primary(self)

    def mouseMoveEvent(self, event) -> None:        # noqa: N802
        studio = getattr(self.scene(), "studio", None)
        if studio is not None and ConnectorGestureKernel.move(
                self, studio, event.scenePos()):
            event.accept()
            return
        if self._resizing:
            self._resize_to(
                getattr(self, "_resize_handle", "se"),
                event.scenePos(),
                bool(event.modifiers() & Qt.AltModifier))
            if studio is not None:
                studio.mark_unsaved()
                studio.reroute_pipes(self)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:     # noqa: N802
        studio = getattr(self.scene(), "studio", None)
        if self._resizing:
            self._resizing = False
            self._resize_handle = None
            if studio is not None:
                studio.mark_unsaved()
                studio.reroute_pipes(self)
                studio.pane.show_pvm(self)
                studio.end_gesture()
            event.accept()
            return
        if studio is not None and ConnectorGestureKernel.release(
                self, studio, event.scenePos()):
            event.accept()
            return
        super().mouseReleaseEvent(event)
        if studio is not None:
            studio.end_gesture()

    def anchor_sides(self) -> tuple[str, ...]:
        symbol = self._symbol_name() or ""
        semantic = ("suction", "discharge") if symbol in (
            "pump", "compressor", "compressor_tapered", "fan", "blower") else (
            ("inlet", "outlet", "vent", "drain") if symbol in (
                "tank", "vessel", "reactor") else (
                ("inlet", "outlet") if symbol else ()))
        return tuple(dict.fromkeys((*semantic, *(
            str(point.get("name", f"cp{index + 1}"))
            for index, point in enumerate(self.connection_points)), "n", "s", "e", "w")))

    def _symbol_rotation(self) -> float:
        # Inline PVMs already rotate as Qt items; rotating their nozzles here
        # too put a 90-degree connection on the opposite side of the vessel.
        return float(self.pvm.rot or 0.0) if self.pvm.role == "dynamo_compact" else 0.0

    def connection_normal(self, side: str) -> str:
        """Transform the departure direction by the same path as its nozzle."""
        from .routing import infer_port_normal
        spec = _outline_spec(side)
        edge = _free_edge_spec(side)
        if spec or edge:
            normal = (spec or edge)[0]
        else:
            point = next((p for p in self.connection_points if p.get("name") == side), {})
            normal = point.get("normal") or _SYMBOL_PORT_ALIASES.get(side)
            if normal not in ("n", "e", "s", "w"):
                normal = infer_port_normal(float(point.get("x", .5)), float(point.get("y", .5)))
            if self._symbol_name():
                normal = _transform_normal(normal, rotation=self._symbol_rotation())
        dx, dy = {"n": (0, -1), "e": (1, 0), "s": (0, 1), "w": (-1, 0)}[normal]
        origin = self.mapToScene(QPointF())
        delta = self.mapToScene(QPointF(dx, dy)) - origin
        return ("e" if delta.x() >= 0 else "w") if abs(delta.x()) >= abs(delta.y()) else ("s" if delta.y() >= 0 else "n")

    def _anchor_hit_radius(self, pixels: float = 12.0) -> float:
        return scene_radius(self, pixels)

    def connection_handle(self, side: str) -> QPointF:
        """Visible connector handle in item coordinates.

        Card PVMs keep cardinal handles outside their resize frame. Equipment
        handles sit on the visible symbol itself: an offset dot beneath a
        vessel looks like (and formerly behaved like) a false pipe endpoint.
        Named class ports always stay exactly where the class author put them.
        """
        point = self.mapFromScene(self.anchor(side))
        if self.isSelected() and side in ("n", "s", "e", "w") \
                and self._symbol_name() is None:
            offset = self._anchor_hit_radius(16.0)
            if side == "n":
                point.setY(point.y() - offset)
            elif side == "s":
                point.setY(point.y() + offset)
            elif side == "w":
                point.setX(point.x() - offset)
            else:
                point.setX(point.x() + offset)
        return point

    def anchor_hit(self, local_pos, radius: float = 12.0) -> str | None:
        """Return the magnetic port under a pointer-sized hit target."""
        radius = self._anchor_hit_radius(radius)
        for side in self.anchor_sides():
            point = self.connection_handle(side)
            if (point.x() - local_pos.x()) ** 2 \
                    + (point.y() - local_pos.y()) ** 2 <= radius ** 2:
                return side
        return None

    def anchor(self, side: str) -> QPointF:
        rect = self.rect()
        symbol = self._symbol_name()
        if symbol:
            target = self._symbol_view_rect(symbol)
            outline = _symbol_outline_anchor(
                target, side, rotation=self._symbol_rotation())
            if outline is not None:
                return self.mapToScene(outline)
            declared = _declared_symbol_anchor(
                target, symbol, side, rotation=self._symbol_rotation())
            if declared is not None:
                return self.mapToScene(declared)
        free_point = _edge_point(rect, side)
        if free_point is not None:
            return self.mapToScene(free_point)
        # Process PVMs often reserve most of their host rect for the tag,
        # value, or OP bar. Cardinal probes must use the symbol lane itself;
        # probing the host midpoint made a compact valve's west/east anchors
        # fall on the glyph's lower edge and introduced a visible pipe dogleg.
        attachment = self._symbol_view_rect(symbol) if symbol else rect
        offsets = {
            "n": QPointF(attachment.center().x(), attachment.top()),
            "s": QPointF(attachment.center().x(), attachment.bottom()),
            "w": QPointF(attachment.left(), attachment.center().y()),
            "e": QPointF(attachment.right(), attachment.center().y()),
        }
        if side in offsets:
            if symbol:
                target = self._symbol_view_rect(symbol)
                _token, point, _distance = _nearest_symbol_outline(
                    target, symbol, offsets[side],
                    rotation=self._symbol_rotation())
                return self.mapToScene(point)
            return self.mapToScene(offsets[side])
        point = next((one for one in self.connection_points
                      if str(one.get("name", "")) == side), None)
        if point is None:
            return self.mapToScene(offsets["e"])
        attachment_rect = self._symbol_view_rect(symbol) if symbol else rect
        requested = QPointF(
            attachment_rect.left()
            + attachment_rect.width() * float(point.get("x", 0.5)),
            attachment_rect.top()
            + attachment_rect.height() * float(point.get("y", 0.5)))
        if symbol:
            # Class ports such as vessel.bottom are semantic names, but their
            # old normalized coordinates were relative to the whole PVM and
            # landed in its readout band. Preserve the name while projecting
            # its intended direction onto the SVG's actual alpha outline.
            _token, requested, _distance = _nearest_symbol_outline(
                attachment_rect, symbol, requested,
                rotation=self._symbol_rotation())
        return self.mapToScene(requested)

    def nearest_side(self, scene_pos) -> str:
        free = self.connection_anchor_at(scene_pos, allow_inside=True)
        if free is not None:
            return free
        best, best_d = "e", None
        for side in self.anchor_sides():
            p = self.anchor(side)
            d = (p.x() - scene_pos.x()) ** 2 \
                + (p.y() - scene_pos.y()) ** 2
            if best_d is None or d < best_d:
                best, best_d = side, d
        return best

    def connection_anchor_at(self, scene_pos, *, pixels: float = 12.0,
                             allow_inside: bool = False) -> str | None:
        """Resolve a fixed port or an arbitrary perimeter attachment."""
        local = self.mapFromScene(scene_pos)
        fixed = self.anchor_hit(local, pixels)
        if fixed is not None:
            return fixed
        symbol = self._symbol_name()
        if symbol:
            token, _point, distance = _nearest_symbol_outline(
                self._symbol_view_rect(symbol), symbol, local,
                rotation=self._symbol_rotation())
            hit = self._anchor_hit_radius(pixels)
            if allow_inside and self._symbol_view_rect(symbol).contains(local):
                return token
            return token if distance <= hit ** 2 else None
        token, _point, distance = _nearest_edge(self.rect(), local)
        hit = self._anchor_hit_radius(pixels)
        if allow_inside and self.rect().contains(local):
            return token
        return token if distance <= hit ** 2 else None

    def connection_anchor_on_axis(self, side: str, coordinate: float, *,
                                  horizontal: bool) -> str:
        """Perimeter token on an exact scene X/Y for run alignment."""
        existing = self.anchor(side)
        scene_probe = QPointF(existing.x(), coordinate) if horizontal \
            else QPointF(coordinate, existing.y())
        local = self.mapFromScene(scene_probe)
        symbol = self._symbol_name()
        if symbol:
            return _axis_symbol_outline_token(
                self._symbol_view_rect(symbol), symbol, local, side,
                horizontal=horizontal, rotation=self._symbol_rotation())
        token, _point, _distance = _nearest_edge(self.rect(), local)
        spec = _free_edge_spec(token)
        if spec is None:
            return side
        _normal, fraction = spec
        return _free_edge_token(side, fraction)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        scene = self.scene()
        studio = getattr(scene, "studio", None) if scene else None
        if studio is not None:
            studio.open_faceplate(self.pvm)

    def contextMenuEvent(self, event) -> None:      # noqa: N802
        scene = self.scene()
        studio = getattr(scene, "studio", None) if scene else None
        if studio is not None:
            self.setSelected(True)
            studio.pvm_context_menu(self, event.screenPos())

    # ------------------------------------------------------------ drawing
    def _state(self):
        result = self.binding.result if self.binding else None
        return (result, resolve_state(result)
                if result is not None else None)

    def _symbol_name(self) -> str | None:
        built_in = self.built_in_symbol_name()
        if built_in is None or not self.pvm.symbol:
            return built_in
        # An authored artwork override outranks the built-in mapping, the
        # way an authored `fill` outranks the theme — but only for a PVM
        # that already draws equipment. Letting it turn a value card into
        # a silhouette would delete the PV/SP/OUT rows the operator reads,
        # which is a change of meaning, not of appearance.
        #
        # It is resolved against the catalog rather than trusted: a display
        # carrying a symbol this project does not have falls back to the
        # class silhouette instead of drawing nothing where equipment
        # belongs.
        from azeo_control_trainer.core.hmi.pvms.symbols import symbol_exists
        if not symbol_exists(self.pvm.symbol):
            return built_in
        return self.pvm.symbol

    def built_in_symbol_name(self) -> str | None:
        from azeo_control_trainer.core.hmi.pvms.symbols import SYMBOL_FOR_VARIANT, pvm_symbol
        # Process vessel painters render a configurable SVG shell. Connector
        # geometry must resolve that same shell instead of the host PVM rect,
        # whose lower band contains the tag/value rather than equipment ink.
        if self.pvm.variant in ("tank", "vessel"):
            return str(self.pvm.choices.get(
                "equipment_symbol", self.pvm.variant))
        # Process PVMs also own visible equipment.  Limiting this mapping to
        # compact status PVMs made the AO valve and AI reactor connect to
        # their large host rectangles rather than to the painted equipment.
        if self.pvm.variant in SYMBOL_FOR_VARIANT:
            return SYMBOL_FOR_VARIANT[self.pvm.variant]
        if self.pvm.variant == "reactor":
            return "reactor"
        kind_binding = self.rows.get("KIND")
        kind = str(kind_binding.result.value or "") \
            if kind_binding is not None else ""
        return pvm_symbol(self.pvm.block_type, kind)

    def _symbol_view_rect(self, symbol: str | None = None) -> QRectF:
        """Exact unrotated rectangle passed to QSvgRenderer.

        A compact equipment PVM reserves 24 pixels for its tag/status band.
        Connector geometry must use the remaining SVG target, otherwise a
        bottom connection visibly floats beneath a vessel as in the former
        rectangle-based implementation.
        """
        from azeo_control_trainer.core.hmi.pvms.symbols import renderer
        painting = getattr(self, "_content_rect_override", None) is not None
        if painting:
            rect = self.rect()
            output_scale = 1.0
        else:
            output_scale, _ = self.content_scale()
            layout_w, layout_h = self.content_layout_size()
            rect = QRectF(0.0, 0.0, layout_w, layout_h)
        symbol = symbol or self._symbol_name()
        if self.pvm.variant in ("tank", "vessel"):
            # Keep this geometry identical to paint_tank_trend and
            # paint_vessel_trend. QSvgRenderer receives this stretched body
            # directly, so its alpha outline is the true connection surface.
            show_readout = self.pvm.choices.get("show_readout", True)
            target = QRectF(rect.left() + 4.0, rect.top() + 6.0,
                            max(1.0, rect.width() - 8.0),
                            max(1.0, rect.height()
                                - (34.0 if show_readout else 12.0)))
            if painting:
                return target
            return QRectF(target.x() * output_scale,
                          target.y() * output_scale,
                          target.width() * output_scale,
                          target.height() * output_scale)
        if self.pvm.variant in ("vfd", "turbine_speed", "compressor_speed"):
            available = QRectF(rect.left(), rect.top(), rect.width(),
                               max(1.0, rect.height() - 48.0))
        elif self.pvm.variant == "valve" and self.pvm.role == "dynamo_inline":
            # Must match paint_valve_op exactly: its OP bar and readout are
            # instrumentation furniture, not a process-pipe surface.
            available = QRectF(rect.left(), rect.top(), rect.width(),
                               max(1.0, rect.height() - 40.0))
        elif self.pvm.variant == "reactor" \
                and self.pvm.role == "dynamo_inline":
            # Must match paint_reactor_bar exactly: the analog bar occupies
            # the right lane and must never attract a process connection.
            available = QRectF(rect.left(), rect.top() + 2.0,
                               max(1.0, rect.width() - 34.0),
                               max(1.0, rect.height() - 20.0))
        else:
            available = QRectF(rect.left(), rect.top(), rect.width(),
                               max(1.0, rect.height() - 24.0))
        svg = renderer(symbol) if symbol else None
        if svg is None:
            return available
        view = svg.viewBoxF()
        avail_w, avail_h = available.width(), available.height()
        if self._symbol_rotation() % 360 in (90, 270):
            avail_w, avail_h = avail_h, avail_w
        scale = min(avail_w / max(view.width(), 1.0),
                    avail_h / max(view.height(), 1.0))
        width, height = view.width() * scale, view.height() * scale
        target = QRectF(available.center().x() - width / 2.0,
                        available.center().y() - height / 2.0,
                        width, height)
        if painting:
            return target
        return QRectF(target.x() * output_scale,
                      target.y() * output_scale,
                      target.width() * output_scale,
                      target.height() * output_scale)

    def routing_rect(self) -> QRectF:
        # Compact equipment can have a wide selection frame around a tiny
        # aspect-fitted glyph. Treating that empty frame as equipment turned
        # valid manual bends red/dashed as soon as the drag was released.
        if self.pvm.role != "dynamo_compact":
            return self._actual_rect()
        symbol = self._symbol_name()
        if not symbol:
            return self._actual_rect()
        rect = self._symbol_view_rect(symbol)
        rotation = self._symbol_rotation()
        if not rotation:
            return rect
        transform = QTransform().translate(rect.center().x(), rect.center().y())
        transform.rotate(rotation)
        transform.translate(-rect.center().x(), -rect.center().y())
        return transform.mapRect(rect)

    def _paint_symbol(self, painter: QPainter, symbol: str) -> None:
        """A device PVM: the P&ID silhouette, tag beneath, state text —
        equipment on a P&ID, not a card."""
        from azeo_control_trainer.core.hmi.pvms.symbols import renderer
        rect = self.rect()
        result, state = self._state()
        running = bool(result.value) if result is not None else False
        fault = state is not None and state.name in ("alarm",)
        bad = state is not None and state.name == "bad"

        svg = renderer(symbol, line=self._palette[Role.EQUIPMENT],
                       fill=self._palette[Role.EQUIPMENT_FILL], text=self._palette[Role.TEXT])
        symbol_rect = QRectF(rect.left(), rect.top(),
                             rect.width(), rect.height() - 24)
        if svg is not None:
            painter.setRenderHint(QPainter.Antialiasing, True)
            rot = (self.pvm.rot or 0) % 360
            target = self._symbol_view_rect(symbol)
            w, h = target.width(), target.height()
            painter.setOpacity(0.45 if bad else 1.0
                               if running else 0.62)
            if rot:
                # Silhouette turns about its own centre; text below
                # stays upright and in place.
                painter.save()
                painter.translate(symbol_rect.center())
                painter.rotate(rot)
                svg.render(painter, QRectF(-w / 2, -h / 2, w, h))
                painter.restore()
            else:
                svg.render(painter, target)
            painter.setOpacity(1.0)
        tag = "/".join(str(v) for v in self.pvm.params.values())
        tag = tag.split("/")[-1] if "/" in tag else tag
        tag = self.pvm.label or tag
        painter.setFont(self.typeface("tag", "Consolas", 8, QFont.Bold))
        painter.setPen(self.colour(Role.ALARM_P1_TEXT) if fault
                       else self.colour(Role.TEXT))
        painter.drawText(QRectF(rect.left(), rect.bottom() - 24,
                                rect.width(), 12),
                         Qt.AlignCenter, tag)
        state_binding = self.rows.get("STATE")
        state_text = str(state_binding.result.value or "") \
            if state_binding is not None else \
            ("– – –" if bad else "RUNNING" if running else "STOPPED")
        painter.setFont(self.typeface("secondary", "Segoe UI", 7))
        painter.setPen(self.colour(Role.ALARM_P1_TEXT) if fault
                       else self.colour(Role.TEXT_DIM))
        painter.drawText(QRectF(rect.left(), rect.bottom() - 12,
                                rect.width(), 12),
                         Qt.AlignCenter,
                         "– – –" if bad else state_text.upper())
        # Alarm / trip badge, PlantPAx style: red square, numbered.
        fail = self.rows.get("FAIL")
        trip = self.rows.get("TRIP")
        if (fail is not None and bool(fail.result.value)) \
                or (trip is not None and bool(trip.result.value)) \
                or fault:
            from azeo_control_trainer.core.hmi.pvms.pvm_painters import draw_alarm_badge
            draw_alarm_badge(painter, rect.right() - 16, rect.top(),
                             15, "1", palette=self._palette,
                             font=self.typeface(
                                 "alarm", "Segoe UI", 8, QFont.Bold))

    def _paint_hover(self, painter: QPainter) -> None:
        rect = self.rect()
        studio = getattr(self.scene(), "studio", None)
        # Hover feedback is authoring chrome: it says "this is a thing
        # you can grab", which is only true in EDIT. Outside it the
        # card must render exactly as the operator's would.
        if studio is not None and studio.mode != MODE_EDIT:
            return
        # A normal selection owns the clean eight-handle transform frame.
        # Showing every magnetic port here used to cover compact valves with
        # circles. Hover shows one exact candidate; Connect mode shows all.
        connector_sides = visible_connector_sides(self, studio)
        if connector_sides:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setBrush(QBrush(QColor("#FFFFFF")))
            for side in connector_sides:
                point = self.connection_handle(side)
                hot = side in (getattr(self, "_hover_anchor", None),
                               getattr(self, "_connect_hot_anchor", None))
                colour = QColor(WF["cmd_teal"] if side not in
                                ("n", "s", "e", "w") else WF["lapis"])
                pen = QPen(colour, 1.6 if hot else 1.2)
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.setBrush(QBrush(colour if hot else QColor("#FFFFFF")))
                radius = self._anchor_hit_radius(5.0 if hot else 3.8)
                painter.drawEllipse(point, radius, radius)
                if self.isSelected() and side in ("n", "s", "e", "w") \
                        and self._symbol_name() is None:
                    edge = self.mapFromScene(self.anchor(side))
                    tether = QPen(colour, 0.8, Qt.DotLine)
                    tether.setCosmetic(True)
                    painter.setPen(tether)
                    painter.drawLine(edge, point)
            hot_anchor = getattr(self, "_connect_hot_anchor", None) \
                or getattr(self, "_hover_anchor", None)
            if _free_edge_spec(str(hot_anchor or "")) is not None \
                    or _outline_spec(str(hot_anchor or "")) is not None:
                point = self.mapFromScene(self.anchor(hot_anchor))
                colour = QColor(WF["cmd_teal"])
                pen = QPen(colour, 1.6)
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.setBrush(QBrush(colour))
                radius = self._anchor_hit_radius(5.0)
                painter.drawEllipse(point, radius, radius)
        elif self._hover and not self.isSelected() \
                and self.shows_selection_frame():
            painter.setPen(QPen(QColor(WF["lapis_lt"]), 1.0))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect)

    def _paint_selection(self, painter: QPainter) -> None:
        self._paint_hover(painter)
        if not self.isSelected():
            return
        rect = self.rect()
        if self.shows_selection_frame():
            painter.setPen(QPen(QColor(WF["sel_br"]), 1.2, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect)
        if getattr(self, "pvm_locked", False):
            return
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(QColor(WF["lapis"]), 1.2))
        painter.setBrush(QBrush(QColor("#FFFFFF")))
        half = self._anchor_hit_radius(4.5)
        for centre in self.handle_points().values():
            painter.drawRect(QRectF(centre.x() - half,
                                    centre.y() - half,
                                    half * 2.0, half * 2.0))
        # Connector handles are painted by _paint_hover so resize and
        # connection affordances use one source of truth.

    # ------------------------------------------------- HP anatomy
    def hp_state(self):
        """The outer furniture's state — alarm box, status icons.

        Resolved once per paint and shared by every branch below, so
        an inline bar and a compact card cannot disagree about whether
        the module is in alarm.
        """
        from azeo_control_trainer.core.hmi.pvms.hp import AlarmBoxState, resolve_alarm_box
        if self.pvm.block_type == "AREA" \
                and self.effective_variant() == "hp_alarms":
            # HP_Alarms has no point binding by design; its source is the
            # shared alarm registry.  Treating that intentional absence as a
            # failed binding lit Bad I/O permanently and painted a red X over
            # the roll-up count on every healthy L1 display.
            records = tuple(
                self.alarm_provider() if self.alarm_provider else ())
            demanding = tuple(
                row for row in records
                if not row.suppressed
                and (row.active or not row.acknowledged))
            top = max(demanding or records or (None,),
                      key=lambda row: int(row.priority) if row else 0)
            return AlarmBoxState(
                priority=int(top.priority) if top else 0,
                active=any(row.active and not row.suppressed
                           for row in records),
                acked=not any(not row.acknowledged and not row.suppressed
                              for row in records),
                # A suppressed alarm is the roll-up state only when no live
                # or unacknowledged alarm outranks it.  Mixing "any
                # suppressed" into an active roll-up would recolour a live
                # critical alarm as shelved.
                suppressed=not demanding
                and any(row.suppressed for row in records),
                alarm_count=len(records),
                condition=str(top.condition) if top else "",
            )
        return resolve_alarm_box(
            self.binding.result if self.binding else None,
            mode_result=(self.mode_binding.result
                         if self.mode_binding else None),
            simulate_result=(self.rows["SIMULATE"].result
                             if "SIMULATE" in self.rows else None),
            conditions=self._device_conditions(),
            enabled_conditions=self.status_conditions)

    def _row_value(self, key):
        """One bound row's value, or None when the class did not bind
        it. None means *unbound*, which is never the same as False —
        an unbound condition leaves its icon dark instead of asserting
        that the condition is absent."""
        binding = self.rows.get(key)
        if binding is None:
            return None
        result = binding.result
        return None if result is None else result.value

    def _device_conditions(self) -> dict:
        """The DC/EDC status-icon conditions, per PVMS+FB.pdf p14.

        Polarity is the trap: `PERMISSIVE_D` and `INTERLOCK` are True
        when the permit is GRANTED, so the icons show on the False
        case. Reading them the obvious way round lights "no permit" on
        every healthy device.
        """
        out = {}
        state = self._row_value("COND_STATE")
        if state is not None:
            # DeviceControlBlock._INTERLOCKED — DC_STATE
            # "Shutdown/Interlocked", which is what the manual keys on.
            out["interlocked"] = int(state or 0) == 5
        permit = self._row_value("COND_PERMISSIVE")
        if permit is not None:
            out["no_permit"] = not bool(permit)
        tracking = self._row_value("COND_TRACKING")
        if tracking is not None:
            out["tracking"] = bool(tracking)
        bypassed = self._row_value("COND_BYPASSED")
        if bypassed is not None:
            out["bypassed"] = bool(bypassed)
        return out

    @property
    def display_level(self) -> int:
        """1 (overview: alarm BOX) or 2 (detail: alarm ICON)."""
        scene = self.scene()
        display = getattr(scene, "display", None) if scene else None
        if display is None:
            studio = getattr(scene, "studio", None) if scene else None
            display = getattr(studio, "display", None)
        return int(getattr(display, "level", 2) or 2)

    @property
    def show_tag(self) -> str:
        """The display's `ShowTag` layout variable."""
        from azeo_control_trainer.core.hmi.pvms.hp import TAG_MODULE
        scene = self.scene()
        display = getattr(scene, "display", None) if scene else None
        if display is None:
            studio = getattr(scene, "studio", None) if scene else None
            display = getattr(studio, "display", None)
        return str(getattr(display, "show_tag", TAG_MODULE) or TAG_MODULE)

    def hp_tag_rect(self, *, painting=False) -> QRectF:
        """Share the printed tag band with runtime pointer hit-testing."""
        rect = self.rect()
        scale = 1.0 if painting else self.content_scale()[1]
        return QRectF(rect.left(), rect.top() - 13.0 * scale,
                      rect.width(), 12.0 * scale)

    def _paint_hp_anatomy(self, painter: QPainter) -> None:
        """The box, icons, count and tag every HP PVM shares.

        Drawn AFTER the class's own painter so the box surrounds the
        finished PVM rather than being painted over by it.
        """
        from azeo_control_trainer.core.hmi.pvms.hp import (
            display_tag, draw_alarm_box, draw_alarm_count,
            draw_alarm_icon, draw_status_icons, draw_status_indicator,
        )
        state = self.hp_state()
        rect = self.rect()
        level = self.display_level
        painter.setRenderHint(QPainter.Antialiasing, False)
        if self.shows_operational_alarm_box():
            draw_alarm_box(painter, rect, state, self._palette, level)
        painter.setRenderHint(QPainter.Antialiasing, True)
        # Icons along the bottom-left inside edge, as the figure has
        # them: alarm icon first (Level 2 only), then the abnormal
        # conditions, which show regardless of the box's precedence.
        x = rect.left() + 4.0
        y = rect.bottom() - 16.0
        x += draw_alarm_icon(painter, x, y, state, self._palette, level)
        # The status indicator shares the alarm icon's slot — it
        # REPLACES the icon when there is no alarm and WRAPS it when
        # there is, so an abnormal condition can never hide an alarm.
        x += draw_status_indicator(painter, x, y, state,
                                   self._palette, level)
        alarm_font = self.typeface("alarm", "Segoe UI", 7, QFont.Bold)
        draw_status_icons(painter, x, y, state, self._palette,
                          font=alarm_font)
        draw_alarm_count(painter, rect, state, self._palette,
                         font=alarm_font)
        # The tag rides ABOVE the PVM, outside its rect. The class
        # painter owns everything inside; a tag drawn within would
        # cost every one of the 28 painters a reserved strip.
        text = display_tag(self.pvm, self.show_tag)
        if text:
            painter.setPen(self.colour(Role.TEXT_DIM))
            painter.setFont(self.typeface("tag", "Segoe UI", 7))
            painter.drawText(
                self.hp_tag_rect(painting=True),
                Qt.AlignHCenter | Qt.AlignBottom, text)
        self._refresh_hover_text(state)

    def _refresh_hover_text(self, state) -> None:
        """The HP hover window, as this item's tooltip.

        **Only while the pointer is on the PVM.** Building it costs
        ~114 us, and paint runs for every PVM on every frame — on a
        display of forty that is 4.5 ms per frame spent composing a
        string nobody is reading. The pointer is over one PVM at a
        time, so one is all that needs a current tooltip.
        """
        if not self._hover:
            return
        from azeo_control_trainer.core.hmi.pvms.hp import hover_text
        text = hover_text(
            self.pvm,
            self.binding.result if self.binding else None,
            self.rows, state)
        if text != self.toolTip():
            self.setToolTip(text)

    def _paint_content(self, painter: QPainter) -> None:
        """Paint one complete PVM in canonical class design units."""
        # Custom dynamo painters first (bar, tank trend, ...).
        from azeo_control_trainer.core.hmi.pvms.pvm_painters import CLASS_PAINTERS
        custom = CLASS_PAINTERS.get((self.pvm.block_type,
                                     self.pvm.role,
                                     self.effective_variant()))
        if custom is not None:
            custom(painter, self)
            if self.pvm.choices.get("show_hp_anatomy", True):
                self._paint_hp_anatomy(painter)
            return
        symbol = self._symbol_name()
        if symbol is not None:
            self._paint_symbol(painter, symbol)
            self._paint_hp_anatomy(painter)
            return
        rect = self.rect()
        result, state = self._state()
        alarm = state is not None and state.name == "alarm"
        bad = state is not None and state.name == "bad"

        # Card face + border (alarm turns the border, per the mock).
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setBrush(QBrush(self.colour(self.FACE)))
        border = self.state_colour(state, on_fill=True) if alarm \
            else self.colour(self.BORDER)
        painter.setPen(QPen(border, 2.0 if alarm else 1.0,
                            Qt.DashLine if bad else Qt.SolidLine))
        painter.drawRect(rect)

        # Bad/alarm chip — Azeo's red circle-X, top-left.
        chip_offset = 0
        if bad or alarm:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            painter.setBrush(self.state_colour(state, on_fill=True))
            painter.drawEllipse(QRectF(rect.left() + 5, rect.top() + 4,
                                       11, 11))
            # White on an alarm fill, and that stays a literal on
            # purpose: the fill is theme-INVARIANT (one meaning, one
            # colour, every theme), so the mark on it is too.
            painter.setPen(QPen(QColor("#FFFFFF"), 1.6))
            painter.drawLine(rect.left() + 8, rect.top() + 7,
                             rect.left() + 13, rect.top() + 12)
            painter.drawLine(rect.left() + 13, rect.top() + 7,
                             rect.left() + 8, rect.top() + 12)
            painter.setRenderHint(QPainter.Antialiasing, False)
            chip_offset = 13

        # Title row: friendly name (or tag) left, mode chip right.
        tag = "/".join(str(v) for v in self.pvm.params.values())
        tag = tag.split("/")[-1] if "/" in tag else tag
        tag = self.pvm.label or tag
        painter.setFont(self.typeface("tag", "Consolas", 8, QFont.Bold))
        painter.setPen(self.colour(Role.TEXT))
        painter.drawText(rect.adjusted(7 + chip_offset, 4, -7, 0),
                         Qt.AlignTop | Qt.AlignLeft, tag)
        mode = str(self.mode_binding.result.value or "") \
            if self.mode_binding else ""
        if mode:
            chip = QRectF(rect.right() - 7 - 30, rect.top() + 4, 30, 12)
            painter.setPen(Qt.NoPen)
            # Mode colours are fixed across themes for the same reason
            # alarm priorities are: CAS / MAN / OOS is a meaning, and a
            # meaning that changed hue per theme would have to be
            # re-learned per station. White on them is therefore safe.
            painter.setBrush(QColor(_mode_chip_colour(mode)))
            painter.drawRect(chip)
            painter.setPen(QColor("#FFFFFF"))
            painter.setFont(self.typeface("mode", "Consolas", 6,
                                          QFont.Bold))
            painter.drawText(chip, Qt.AlignCenter, mode[:4].upper())

        # Value rows.
        painter.setFont(self.typeface("value", "Consolas", 8))
        y = rect.top() + 20
        if self.rows and rect.height() >= 80:
            for label, binding in self.rows.items():
                row_result = binding.result
                row_state = resolve_state(row_result)
                painter.setFont(self.typeface(
                    "secondary", "Consolas", 8))
                painter.setPen(self.colour(Role.TEXT_FAINT))
                painter.drawText(QRectF(rect.left() + 7, y, 30, 13),
                                 Qt.AlignLeft, label)
                painter.setPen(self.state_colour(state) if alarm
                               else self.colour(Role.TEXT))
                painter.setFont(self.typeface("value", "Consolas", 8))
                painter.drawText(
                    QRectF(rect.left() + 36, y,
                           rect.width() - 44, 13),
                    Qt.AlignLeft, row_state.value_text)
                y += 14
        elif state is not None:
            painter.setPen(self.state_colour(state) if alarm
                           else self.colour(Role.TEXT))
            painter.setFont(self.typeface("value", "Consolas", 9))
            painter.drawText(rect.adjusted(7, 18, -7, 0),
                             Qt.AlignTop | Qt.AlignLeft,
                             state.value_text)

        # Mini output bar along the bottom (greyscale).
        out_binding = self.rows.get("OUT") or self.binding
        if out_binding is not None and not bad:
            out_result = out_binding.result
            try:
                fraction = max(0.0, min(1.0, float(out_result.value)
                                        / 100.0))
            except (TypeError, ValueError):
                fraction = None
            if fraction is not None:
                track = QRectF(rect.left() + 7, rect.bottom() - 9,
                               rect.width() - 14, 4)
                painter.setPen(Qt.NoPen)
                painter.setBrush(self.colour(self.TRACK))
                painter.drawRect(track)
                painter.setBrush(self.colour(self.BAR))
                painter.drawRect(QRectF(track.left(), track.top(),
                                        track.width() * fraction,
                                        track.height()))

        # Alarm triangle / FORCED badge.
        if alarm:
            painter.setPen(Qt.NoPen)
            painter.setBrush(self.state_colour(state, on_fill=True))
            cx = rect.right() - 14
            cy = rect.bottom() - 16
            painter.drawPolygon([QPointF(cx, cy - 5),
                                 QPointF(cx - 6, cy + 5),
                                 QPointF(cx + 6, cy + 5)])
        if state is not None and state.forced:
            badge = QRectF(rect.right() - 7 - 42, rect.bottom() - 18,
                           42, 12)
            painter.setPen(Qt.NoPen)
            painter.setBrush(self.colour(Role.ALARM_P1))
            painter.drawRect(badge)
            painter.setPen(QColor("#FFFFFF"))
            painter.setFont(self.typeface("alarm", "Segoe UI", 6,
                                          QFont.Bold))
            painter.drawText(badge, Qt.AlignCenter, "FORCED")

        # The shared HP furniture goes on last so the box surrounds
        # the finished card rather than being painted over by it.
        self._paint_hp_anatomy(painter)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        """Scale the entire PVM composition into its placement rect.

        Painters use measured Azeo design units.  Applying one transform
        here means every existing and future class gets semantic resizing:
        type, strokes, bars, limits, trends, SVGs, badges, alarm furniture
        and the tag band all grow/shrink together.  Authoring chrome is
        deliberately painted afterwards in real item coordinates so handles
        remain a usable seven pixels at every zoom and PVM size.
        """
        scale_x, scale_y = self.content_scale()
        layout_w, layout_h = self.content_layout_size()
        painter.save()
        self._content_rect_override = QRectF(0.0, 0.0,
                                             layout_w, layout_h)
        try:
            painter.scale(scale_x, scale_y)
            self._paint_content(painter)
        finally:
            self._content_rect_override = None
            painter.restore()

        self._paint_selection(painter)


class StaticItem(QGraphicsRectItem):
    """A drawing item, Data Link, Display Link, or User Entry.

    Decoration takes style because I7 protects PVMs, not decoration.
    The two Azeo data elements deliberately stay in this shared item
    path: the Studio and Operator must paint and activate the same
    document object rather than growing a second widget renderer.
    """

    HANDLE = 12.0

    def __init__(self, data: dict, palette):
        super().__init__(0, 0, data.get("w", 120), data.get("h", 60))
        # DEEP copy: `points`, `cps` and `anim` are nested containers,
        # and a shallow dict() left a duplicated polyline sharing the
        # original's point list — dragging a vertex on the copy moved
        # the original.
        self.data = copy.deepcopy(dict(data))
        self.data.setdefault("id", f"itm_{uuid.uuid4().hex[:4]}")
        self._palette = palette
        self._resizing = False
        self._hover = False
        self._cp_drag = None
        self._cp_edit = False
        #: Filled by DisplayRenderer for a Data Link. Kept on the item
        #: so the same painter works in the Studio and Operator view.
        self.binding = None
        #: Compound data elements keep one binding per configured pen/axis.
        self.bindings: dict = {}
        from collections import defaultdict, deque
        self.histories = defaultdict(lambda: deque(maxlen=240))
        self.binding_error = ""
        #: Filled by DisplayRenderer for a Display Link. The item only
        #: identifies the target; navigation history belongs to its host.
        self.action_handler = None
        self.interaction_handler = None
        self.alarm_provider = None
        #: User Entries send writes through this checked host callback.
        #: The item never reaches into a graph directly.
        self.write_handler = None
        self.write_check = None
        self.write_allowed = False
        self.write_error = ""
        self._action_pressed = False
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.ItemIsMovable)
        self.setFlag(QGraphicsItem.ItemIsSelectable)
        # A locked background must load at its authored location, not cover
        # unrelated content at (0, 0) before the first operator repaint.
        self.setPos(data.get("x", 0), data.get("y", 0))
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges)
        self.setZValue(float(data.get("z", 0) or 0))
        self.setVisible(bool(data.get("visible", True)))
        self.setPen(QPen(Qt.NoPen))
        self.setBrush(Qt.NoBrush)
        self.apply_rotation()
        if self.data.get("kind") in ("icon_button", "special_symbol"):
            from azeo_control_trainer.core.hmi.pvms.faceplate_icons import ICON_TITLES
            title = self.data.get("tooltip") or ICON_TITLES.get(
                self.data.get("icon", ""), "Faceplate icon")
            self.setToolTip(str(title))
        if self.data.get("kind") == "faceplate_section":
            self._ensure_live_section()

    def boundingRect(self) -> QRectF:               # noqa: N802
        # Anchor handles, connection points and thick strokes paint
        # past the rect — cover them or a drag smears their remnants.
        extra = max(24.0, 8.0 + float(
            self.data.get("width", 2.0) or 2.0))
        # The TOP needs more than the others: the rotation knob sits
        # ROTATE_OFFSET above the edge and has its own radius, so the
        # ink reached ~12 px past what this claimed and Qt never
        # damaged that band — the trailing knob and tether behind a
        # dragged shape. Claimed unconditionally rather than only while
        # selected, because selection changes do not call
        # prepareGeometryChange(): a rect that grew on selection is a
        # rect Qt would still be caching at the old size.
        top = max(extra, self.ROTATE_OFFSET + self.KNOB_RADIUS + 2.0)
        return self.rect().adjusted(-extra, -top, extra, extra)

    def _fill_fraction(self) -> float:
        try:
            pct = float(self.data.get("fill_pct", 100))
        except (TypeError, ValueError):
            pct = 100.0
        return max(0.0, min(1.0, pct / 100.0))

    def _fill_clip(self, rect: QRectF, fraction: float) -> QRectF:
        """Return the authored fill band in its configured direction."""
        direction = self.data.get("fill_direction", "bottom")
        if direction == "top":
            return QRectF(rect.left(), rect.top(), rect.width(),
                          rect.height() * fraction)
        if direction == "left":
            return QRectF(rect.left(), rect.top(),
                          rect.width() * fraction, rect.height())
        if direction == "right":
            width = rect.width() * fraction
            return QRectF(rect.right() - width, rect.top(), width,
                          rect.height())
        height = rect.height() * fraction
        return QRectF(rect.left(), rect.bottom() - height,
                      rect.width(), height)

    # ------------------------------------------------------------- style
    def line_colour(self) -> QColor:
        return _authored_colour(self.data, self._palette, "line", "line_role", Role.EQUIPMENT)

    def text_colour(self, fallback=Role.TEXT) -> QColor:
        return _authored_colour(self.data, self._palette, "text_color", "text_role", fallback)

    def text_layout(self):
        """The router and painter must agree about the ink inside a label."""
        font = QFont(str(self.data.get("font_family") or "Segoe UI"))
        try:
            font.setPointSizeF(max(1.0, float(self.data.get("font_size", 9.0))))
        except (TypeError, ValueError):
            font.setPointSizeF(9.0)
        font.setBold(bool(self.data.get("font_bold", False)))
        font.setItalic(bool(self.data.get("font_italic", False)))
        font.setUnderline(bool(self.data.get("font_underline", False)))
        flags = {"left": Qt.AlignLeft, "center": Qt.AlignHCenter,
                 "right": Qt.AlignRight}.get(self.data.get("text_halign"), Qt.AlignLeft)
        flags |= {"top": Qt.AlignTop, "middle": Qt.AlignVCenter,
                  "bottom": Qt.AlignBottom}.get(self.data.get("text_valign"), Qt.AlignVCenter)
        if self.data.get("text_wrap", False):
            flags |= Qt.TextWordWrap
        return font, flags

    def routing_rect(self):
        rect = self.rect()
        if self.data.get("kind") != "text":
            return rect
        text = str(self.data.get("text", ""))
        if not text.strip():
            return QRectF()
        font, flags = self.text_layout()
        ink = QFontMetricsF(font).boundingRect(rect, int(flags), text)
        if self.data.get("mx"):
            ink.moveLeft(rect.left() + rect.right() - ink.right())
        if self.data.get("my"):
            ink.moveTop(rect.top() + rect.bottom() - ink.bottom())
        return ink

    def fill_colour(self) -> QColor | None:
        if self.data.get("fill"):
            return QColor(self.data["fill"])
        role = self.data.get("fill_role")
        return QColor(self._palette[Role(role)]) if role in Role._value2member_map_ else None

    def pen(self) -> QPen:
        from azeo_control_trainer.core.hmi.pvms.strokes import build_pen
        return build_pen(self.line_colour(),
                         float(self.data.get("width", 1.6)),
                         self.data.get("style", "solid"),
                         self.data.get("cap", "round"))

    def _draw_arrows(self, painter, points: list) -> None:
        """Independent start/end heads on any open stroke."""
        from azeo_control_trainer.core.hmi.pvms.strokes import draw_arrow_head, resolve_arrows
        if len(points) < 2:
            return
        start_head, end_head = resolve_arrows(self.data)
        if start_head == "none" and end_head == "none":
            return
        colour = self.line_colour()
        width = float(self.data.get("width", 1.6))
        size = self.data.get("arrow_size", "medium")
        painter.setRenderHint(QPainter.Antialiasing, True)
        if end_head != "none":
            draw_arrow_head(painter, end_head, points[-1],
                            points[-2], colour, width, size)
        if start_head != "none":
            draw_arrow_head(painter, start_head, points[0],
                            points[1], colour, width, size)

    def apply_rotation(self) -> None:
        rect = self.rect()
        self.setTransformOriginPoint(rect.center())
        self.setRotation(float(self.data.get("rot", 0.0)))

    # ------------------------------------------------------------ resize
    #: The eight resize handles, by the edges each one moves. Corner
    #: handles move two edges, edge handles one — which is what lets a
    #: tower grow taller without growing wider (the builder's
    #: semantic sizing) and what a single corner handle could not do.
    HANDLE_SPEC = {
        "nw": ("n", "w"), "n": ("n",), "ne": ("n", "e"),
        "w": ("w",), "e": ("e",),
        "sw": ("s", "w"), "s": ("s",), "se": ("s", "e"),
    }
    #: Distance above the top edge at which the rotation handle sits.
    ROTATE_OFFSET = 18.0
    #: The knob's drawn radius. A constant because `boundingRect` has
    #: to claim it and `paint` has to draw it, and the two disagreeing
    #: is exactly what leaves a knob behind on a drag.
    KNOB_RADIUS = 4.0
    HANDLE_HIT = 9.0
    #: Below these an item cannot be grabbed again — a shape dragged
    #: to zero would be unrecoverable without the object list. The
    #: builder's own numbers, per kind.
    MIN_W = 28.0
    MIN_H = 28.0
    MIN_SIZE = {"text": (60.0, 28.0), "line": (12.0, 12.0),
                "polyline": (12.0, 12.0), "freehand": (12.0, 12.0),
                "arc": (12.0, 12.0)}

    #: Equipment whose proportions carry meaning keeps its aspect on
    #: resize; a column or stack is *expected* to grow tall without
    #: growing wide, so those stretch freely. The builder's semantic
    #: sizing rule, by symbol name.
    FREE_ASPECT_WORDS = ("column", "tower", "distillation",
                         "absorber", "stripper", "scrubber", "stack",
                         "chimney", "vessel", "tank", "drum",
                         "reactor", "exchanger")

    def minimum_size(self) -> tuple:
        return self.MIN_SIZE.get(self.data.get("kind", ""),
                                 (self.MIN_W, self.MIN_H))

    def aspect_locked(self) -> bool:
        """True when free stretching would misrepresent the object.

        A property of the OBJECT, not a modifier the user must
        remember: a pump drawn wide is a wrong pump, so it locks by
        default and Ctrl breaks the lock for the one drag that needs
        it. An explicit `aspect` in the data always wins.
        """
        explicit = self.data.get("lock_aspect",
                                 self.data.get("aspect"))
        if explicit is not None:
            return bool(explicit)
        if self.data.get("kind") != "symbol":
            return False
        name = str(self.data.get("symbol", "")).lower()
        return not any(word in name
                       for word in self.FREE_ASPECT_WORDS)

    def handle_points(self) -> dict:
        """Handle name -> centre, in item coordinates."""
        return rect_handle_points(self.rect())

    def rotate_handle_point(self) -> QPointF:
        rect = self.rect()
        return QPointF(rect.center().x(),
                       rect.top() - self.ROTATE_OFFSET)

    # ------------------------------------------------- vertex editing
    #: Kinds whose shape is a point list the author edits directly,
    #: rather than a rectangle that gets stretched. These are the
    #: builder's "open strokes": they resize by moving their points.
    POINT_KINDS = ("polyline", "freehand")

    #: Elements with configurable start/end connection tips in Azeo Operator Station.
    #: Chords and pies are closed shapes and keep ordinary edge connection
    #: points only.
    TIP_KINDS = tuple(kind for kind in CROSSOVER_KINDS if kind != "pipe")

    def is_point_kind(self) -> bool:
        return self.data.get("kind") in self.POINT_KINDS

    def shows_selection_frame(self) -> bool:
        """Symbols expose handles and ports without a bounding rectangle."""
        return self.data.get("kind") != "symbol"

    def vertices(self) -> list:
        """The editable points, in item coordinates. A polyline with
        no stored points gets the default zig-zag its painter draws,
        so the first drag has something real to grab."""
        rect = self.rect()
        points = self.data.get("points")
        if not points and self.data.get("kind") == "polyline":
            points = [[0, rect.height()],
                      [rect.width() * 0.4, rect.height() * 0.3],
                      [rect.width(), rect.height() * 0.6]]
            self.data["points"] = points
        return [QPointF(p[0], p[1]) for p in (points or [])]

    def vertex_at(self, pos) -> int | None:
        for index, point in enumerate(self.vertices()):
            if (abs(pos.x() - point.x()) <= self.HANDLE_HIT
                    and abs(pos.y() - point.y()) <= self.HANDLE_HIT):
                return index
        return None

    def move_vertex(self, index: int, pos) -> None:
        points = self.data.get("points") or []
        if not (0 <= index < len(points)):
            return
        points[index] = [pos.x(), pos.y()]
        self._refit_points()

    def insert_vertex(self, pos) -> int | None:
        """Add a point on the segment nearest the click — the
        builder's 'Add polyline point here'. Inserted INTO the
        segment it was clicked on, never appended, or the shape would
        jump."""
        from azeo_control_trainer.core.hmi.pvms.shapes import point_segment_distance
        points = self.data.get("points") or []
        if len(points) < 2:
            return None
        best, best_distance = 1, None
        for index in range(len(points) - 1):
            distance = point_segment_distance(
                (pos.x(), pos.y()), points[index], points[index + 1])
            if best_distance is None or distance < best_distance:
                best_distance, best = distance, index + 1
        points.insert(best, [pos.x(), pos.y()])
        self._refit_points()
        return best

    def remove_vertex(self, index: int) -> bool:
        """A polyline needs two points to be a line at all."""
        points = self.data.get("points") or []
        if len(points) <= 2 or not (0 <= index < len(points)):
            return False
        del points[index]
        self._refit_points()
        return True

    def _refit_points(self) -> None:
        """Keep the item rectangle wrapped around its points: the
        bounding box IS the hit area and what pipes anchor to, so a
        vertex dragged outside a stale rect would be unclickable."""
        points = self.data.get("points") or []
        if not points:
            return
        min_x = min(p[0] for p in points)
        min_y = min(p[1] for p in points)
        if abs(min_x) > 0.01 or abs(min_y) > 0.01:
            # Re-origin so points stay non-negative and the item's
            # position absorbs the shift.
            for point in points:
                point[0] -= min_x
                point[1] -= min_y
            self.data["x"] = self.data.get("x", 0) + min_x
            self.data["y"] = self.data.get("y", 0) + min_y
            self.setPos(self.data["x"], self.data["y"])
        width = max(p[0] for p in points)
        height = max(p[1] for p in points)
        self.prepareGeometryChange()
        self.setRect(0, 0, max(width, 1.0), max(height, 1.0))
        self.data["w"], self.data["h"] = self.rect().width(), \
            self.rect().height()
        self.update()

    def handle_at(self, pos) -> str | None:
        """Which handle the pointer is on — 'rotate' or one of the
        eight, else None. Checked before the move, so a press on a
        handle resizes instead of dragging the object."""
        if self.data.get("locked"):
            return None
        if self.is_point_kind():
            # An open stroke resizes by moving its POINTS: a
            # bounding-box handle on a polyline would stretch the
            # shape the author drew rather than let them adjust it.
            return None
        point = self.rotate_handle_point()
        hit = self._anchor_hit_radius(self.HANDLE_HIT)
        if (abs(pos.x() - point.x()) <= hit
                and abs(pos.y() - point.y()) <= hit):
            return "rotate"
        return hit_rect_handle(self, pos, pixels=self.HANDLE_HIT)

    def _in_handle(self, pos) -> bool:
        rect = self.rect()
        return (pos.x() > rect.right() - self.HANDLE
                and pos.y() > rect.bottom() - self.HANDLE)

    def _resize_to(self, handle: str, scene_pos,
                   break_aspect: bool = False,
                   bypass_snap: bool = False) -> None:
        """Move the edges this handle owns to the pointer. Geometry is
        computed in the PARENT's frame and written back as pos+size,
        so dragging a west or north handle moves the origin instead of
        stretching the item the wrong way.

        Grid snap applies to the resulting SIZE rather than the edge
        (the builder's rule) so a run of shapes sized on the grid come
        out equal; Alt bypasses it.
        """
        edges = self.HANDLE_SPEC.get(handle)
        if not edges:
            return
        min_w, min_h = self.minimum_size()
        origin = self.pos()
        rect = self.rect()
        left, top = origin.x(), origin.y()
        right, bottom = left + rect.width(), top + rect.height()
        point = self.mapToParent(self.mapFromScene(scene_pos))
        if "w" in edges:
            left = min(point.x(), right - min_w)
        if "e" in edges:
            right = max(point.x(), left + min_w)
        if "n" in edges:
            top = min(point.y(), bottom - min_h)
        if "s" in edges:
            bottom = max(point.y(), top + min_h)
        width, height = right - left, bottom - top
        studio = getattr(self.scene(), "studio", None) \
            if self.scene() else None
        if (not bypass_snap and studio is not None
                and getattr(studio, "snap_enabled", False)):
            grid = 8.0
            if "w" in edges or "e" in edges:
                width = max(min_w, round(width / grid) * grid)
                if "w" in edges:
                    left = right - width
            if "n" in edges or "s" in edges:
                height = max(min_h, round(height / grid) * grid)
                if "n" in edges:
                    top = bottom - height
        # A side handle owns exactly one dimension. Aspect preservation is
        # useful on a pump's CORNER drag, but applying it to east/west or
        # north/south made an explicit width-only/height-only gesture change
        # both values. Ctrl still releases a corner's semantic aspect lock.
        if self.aspect_locked() and not break_aspect and len(edges) == 2:
            aspect = (self.data.get("h", height)
                      / max(self.data.get("w", width), 1e-6))
            if "w" in edges or "e" in edges:
                height = max(min_h, width * aspect)
                if "n" in edges:
                    top = bottom - height
            else:
                width = max(min_w, height / max(aspect, 1e-6))
                if "w" in edges:
                    left = right - width
        self.prepareGeometryChange()
        self.setRect(0, 0, width, height)
        self.data["w"], self.data["h"] = width, height
        if left != origin.x() or top != origin.y():
            self.data["x"], self.data["y"] = left, top
            self.setPos(left, top)
        if self.data.get("rot"):
            self.apply_rotation()

    def _rotate_to(self, scene_pos, snap: bool = False) -> None:
        """Lucid-style rotation: the handle follows the pointer about
        the item's centre. SHIFT snaps to 15° — fine enough to place a
        symbol on a sloped run, coarse enough to land on 90 exactly."""
        import math as _math
        centre = self.mapToScene(self.rect().center())
        angle = _math.degrees(_math.atan2(scene_pos.y() - centre.y(),
                                          scene_pos.x() - centre.x()))
        angle += 90.0                   # handle sits above the item
        if snap:
            angle = round(angle / 15.0) * 15.0
        self.data["rot"] = angle % 360
        self.apply_rotation()

    def mousePressEvent(self, event) -> None:       # noqa: N802
        studio = getattr(self.scene(), "studio", None)
        from azeo_control_trainer.core.hmi.pvms.elements import TAB, has_interaction_region
        interactive = self.data.get("kind") in (
            "display_link", "user_entry", TAB) \
            or has_interaction_region(self.data)
        if self.data.get("kind") == "table" and self.data.get("rows_path") and (studio is None or studio.mode != MODE_EDIT):
            if event.button() == Qt.LeftButton and event.pos().y() >= self.rect().bottom() - 24:
                if self.data.get("presentation") == "workflow" and self.rect().width() / 3 < event.pos().x() < self.rect().width() * 2 / 3:
                    self.focus_current_step()
                else:
                    self.page_table(event.pos().x() >= self.rect().center().x())
                event.accept()
                return
        if interactive \
                and (studio is None or studio.mode != MODE_EDIT) \
                and event.button() == Qt.LeftButton:
            self._action_pressed = True
            self._action_started = time.monotonic()
            event.accept()
            return
        if studio is not None:
            studio.gesture_checkpoint()
        if self.data.get("locked"):
            super().mousePressEvent(event)
            return
        # Connection-point editing: drag a custom point to move it.
        if getattr(self, "_cp_edit", False) and studio is not None:
            side = self.anchor_hit(event.pos())
            if side is not None and side.startswith("c"):
                self._cp_drag = int(side[1:])
                event.accept()
                return
        if self.isSelected() and self.is_point_kind():
            index = self.vertex_at(event.pos())
            if index is not None:
                self._vertex_drag = index
                event.accept()
                return
        # Transform handles win on a selected object. Process symbols expose
        # semantic inlet/outlet ports on the same edge; testing ports first
        # caused a width drag to begin a pipe instead. Outer magnetic ports
        # and explicit Connect mode remain the connection affordances.
        if self.isSelected() \
                and not getattr(studio, "connect_armed", False):
            handle = self.handle_at(event.pos())
            if handle == "rotate":
                self._rotating = True
                event.accept()
                return
            if handle is not None:
                self._resize_handle = handle
                self._resizing = True
                event.accept()
                return
        # Small symbols may be entirely covered by the screen-space magnetic
        # edge band. Defer an ambiguous first press so click selects and drag
        # connects. Once selected, normal pointer gestures move/resize it;
        # explicit Line/Connector mode still starts at any outline point.
        connection_intent = bool(getattr(studio, "connect_armed", False)) \
            or not self.isSelected()
        if studio is not None and studio.mode == MODE_EDIT \
                and not getattr(self, "_cp_edit", False) \
                and connection_intent:
            side = self.connection_anchor_at(
                event.scenePos(), allow_inside=False)
            if side is not None and ConnectorGestureKernel.press(
                    self, studio, side, event.scenePos()):
                event.accept()
                return
        # Press ON an anchor starts a pipe drag — the builder gesture:
        # grab a connection point, pull it to another symbol.
        super().mousePressEvent(event)
        # A group moves as one: clicking a member selects the group.
        # Alt-click reaches inside — and must win, because that is
        # exactly when someone wants the one thing.
        group = item_group_id(self)
        if group and not (event.modifiers() & Qt.AltModifier) \
                and studio is not None:
            members = [item for item in studio._groupable_items()
                       if item_group_id(item) == group]
            studio.selection.extend(members, primary=self)
        elif studio is not None and self.isSelected():
            studio.selection.set_primary(self)

    def mouseMoveEvent(self, event) -> None:        # noqa: N802
        studio = getattr(self.scene(), "studio", None)
        if studio is not None and ConnectorGestureKernel.move(
                self, studio, event.scenePos()):
            event.accept()
            return
        if getattr(self, "_cp_drag", None) is not None:
            rect = self.rect()
            fx = max(0.0, min(1.0, event.pos().x()
                              / max(rect.width(), 1)))
            fy = max(0.0, min(1.0, event.pos().y()
                              / max(rect.height(), 1)))
            self.data["cps"][self._cp_drag] = [round(fx, 3),
                                               round(fy, 3)]
            self.update()
            if studio is not None:
                studio.reroute_pipes(self)
                studio.mark_unsaved()
            return
        if getattr(self, "_vertex_drag", None) is not None:
            self.move_vertex(self._vertex_drag, event.pos())
            if studio is not None:
                studio.mark_unsaved()
                studio.reroute_pipes(self)
            return
        if getattr(self, "_rotating", False):
            self._rotate_to(event.scenePos(),
                            bool(event.modifiers() & Qt.ShiftModifier))
            if studio is not None:
                studio.mark_unsaved()
                studio.reroute_pipes(self)
            return
        if self._resizing:
            # Ctrl BREAKS a per-object aspect lock for one drag; Alt
            # bypasses snapping. Shift is free for the angle
            # constraint everywhere else.
            self._resize_to(
                getattr(self, "_resize_handle", "se"),
                event.scenePos(),
                bool(event.modifiers() & Qt.ControlModifier),
                bool(event.modifiers() & Qt.AltModifier))
            if studio is not None:
                studio.mark_unsaved()
                studio.reroute_pipes(self)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:     # noqa: N802
        studio = getattr(self.scene(), "studio", None)
        if self._action_pressed:
            self._action_pressed = False
            if self.rect().contains(event.pos()):
                elapsed = max(0.0, (time.monotonic() - getattr(
                    self, "_action_started", time.monotonic())) * 1000.0)
                self.activate(position=event.pos(), held_ms=elapsed)
            event.accept()
            return
        if studio is not None and ConnectorGestureKernel.release(
                self, studio, event.scenePos()):
            event.accept()
            return
        self._cp_drag = None
        self._resizing = False
        self._rotating = False
        self._resize_handle = None
        self._vertex_drag = None
        super().mouseReleaseEvent(event)
        if studio is not None:
            studio.end_gesture()

    def activate(self, value=None, *, position=None,
                 held_ms: float = 0.0) -> bool:
        """Activate a Display Link or checked User Entry.

        Returning a bool makes the navigation contract testable without
        inventing a second route around the real click handler.
        """
        kind = self.data.get("kind")
        if kind == "display_link":
            target = str(self.data.get("target", "") or "").strip()
            if not target or self.action_handler is None:
                return self._dispatch_actions("click")
            opened = self.action_handler(target) is not False
            return self._dispatch_actions("click") or opened
        from azeo_control_trainer.core.hmi.pvms.elements import TAB
        if kind == TAB:
            tabs = self.data.get("tabs", ())
            if not tabs:
                return False
            width = self.rect().width() / len(tabs)
            index = 0 if position is None else min(
                len(tabs) - 1, max(0, int(position.x() / max(width, 1))))
            self.data["active_tab"] = index
            self.update()
            return self._dispatch_actions("click") or True
        if kind != "user_entry":
            return self._dispatch_actions("click")
        from azeo_control_trainer.core.hmi.pvms.elements import (BUTTON, CHECK_BOX, COMBO_BOX,
                                RADIO_BUTTON, SLEW, SLIDER, TEXT_ENTRY,
                                UserEntry, slew_step)

        entry = UserEntry.from_dict(self.data.get("entry", {}))
        if entry.disabled_reason:
            self.write_error = entry.disabled_reason
            self.update()
            return False
        # Buttons are also the authored action surface.  An action-only
        # button has deliberately no write path, so asking CanWrite first
        # made a valid Open Display/Open Faceplate action look disabled and
        # swallowed its click in the operator station.
        if entry.kind == BUTTON and not entry.path:
            handled = self._dispatch_actions("click")
            self.write_error = "" if handled else "no click action configured"
            self.update()
            return handled
        self.refresh_write_permission()
        if not self.write_allowed or self.write_handler is None:
            self.write_error = self.write_error or "write is not available"
            self.update()
            return False
        current = self.binding.result.value if self.binding is not None \
            else None
        chosen = value
        if chosen is None and entry.kind == BUTTON:
            chosen = entry.value
        elif chosen is None and entry.kind == CHECK_BOX:
            chosen = not bool(current)
        elif chosen is None and entry.kind in (COMBO_BOX, RADIO_BUTTON):
            values = [option[0] for option in entry.options]
            if not values:
                self.write_error = "no options are configured"
                self.update()
                return False
            try:
                index = values.index(current)
            except ValueError:
                index = -1
            chosen = values[(index + 1) % len(values)]
        elif chosen is None and entry.kind == SLEW:
            try:
                current_number = float(current)
            except (TypeError, ValueError):
                current_number = entry.lo
            right = position is None \
                or position.x() >= self.rect().center().x()
            delta = slew_step(held_ms, entry.hi - entry.lo)
            chosen = entry.clamp(
                current_number + (delta if right else -delta))
        elif chosen is None and entry.kind == SLIDER:
            fraction = 0.5 if position is None else max(
                0.0, min(1.0, position.x()
                         / max(self.rect().width(), 1.0)))
            chosen = entry.lo + fraction * (entry.hi - entry.lo)
        elif chosen is None and entry.kind == TEXT_ENTRY:
            from azeo_control_trainer.core.presentation.headless import is_headless
            if is_headless():
                return False
            from PySide6.QtWidgets import QInputDialog
            chosen, accepted = QInputDialog.getText(
                None, entry.label or "Text entry", "Value:",
                text="" if current is None else str(current))
            if not accepted:
                return False
        result = self.write_handler(entry.path, chosen)
        success = bool(getattr(result, "success", result is not False))
        self.write_error = str(getattr(result, "error", "") or "")
        self.update()
        return self._dispatch_actions("click") or success

    def table_rows(self):
        binding = self.bindings.get(self.data.get("rows_path", ""))
        if binding is not None:
            result = binding.result
            return result.value if result.quality.name == "GOOD" and isinstance(result.value, (list, tuple)) else ()
        rows = self.data.get("rows", ())
        return rows if isinstance(rows, (list, tuple)) else ()

    def page_table(self, forward=True):
        rows = self.table_rows()
        from azeo_control_trainer.core.hmi.pvms.procedure_graphics import table_geometry
        _, _, count = table_geometry(self)
        offset = getattr(self, "_table_offset", 0)
        self._table_offset = max(0, min(max(0, ((len(rows) - 1) // count) * count),
                                        offset + (count if forward else -count)))
        self.update()

    def focus_current_step(self):
        from azeo_control_trainer.core.hmi.pvms.procedure_graphics import table_geometry
        _, _, count = table_geometry(self)
        index = next((i for i, row in enumerate(self.table_rows()) if row.get("state") == "ACTIVE"), 0)
        self._table_offset = index // count * count
        self.update()

    def procedure_row_action(self, command, row):
        if self.interaction_handler:
            return self.interaction_handler(dict(kind="procedure_command", target=command,
                                                 source=self.data.get("command_context", ""),
                                                 value=row.get("id", "") if command == "tune" else row.get("equipment", "")), self)
        return False

    def _dispatch_actions(self, event_name: str) -> bool:
        from azeo_control_trainer.core.hmi.pvms.elements import actions_of

        if self.interaction_handler is None or not self.data.get("enabled", True) or not self.data.get("visible", True):
            return False
        handled = False
        for action in actions_of(self.data):
            if action.active and action.event == event_name:
                handled = self.interaction_handler(action.to_dict(), self) \
                    is not False or handled
        return handled

    def refresh_write_permission(self) -> bool:
        """Re-evaluate CanWrite as mode and ownership change online."""
        if self.data.get("kind") != "user_entry":
            return False
        from azeo_control_trainer.core.hmi.pvms.elements import UserEntry

        before = (self.write_allowed, self.write_error)
        entry = UserEntry.from_dict(self.data.get("entry", {}))
        if entry.disabled_reason:
            self.write_allowed = False
            self.write_error = entry.disabled_reason
        elif not entry.path:
            from azeo_control_trainer.core.hmi.pvms.elements import BUTTON, CLICK, actions_of

            has_click_action = entry.kind == BUTTON and any(
                action.active and action.event == CLICK
                for action in actions_of(self.data)
            )
            self.write_allowed = has_click_action
            self.write_error = "" if has_click_action \
                else "write is not available"
        elif self.write_check is None:
            self.write_allowed = False
            self.write_error = "write is not available"
        else:
            result = self.write_check(entry.path)
            self.write_allowed = bool(
                getattr(result, "success", result is not False))
            self.write_error = str(getattr(result, "error", "") or "")
        if before != (self.write_allowed, self.write_error):
            self.update()
        return self.write_allowed

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """Double-click a stroke adds a point where it was clicked;
        double-click ON a point removes it — the builder's polyline
        editing, both directions from one gesture."""
        studio = getattr(self.scene(), "studio", None)
        if self.is_point_kind() and studio is not None \
                and studio.mode == MODE_EDIT \
                and not self.data.get("locked"):
            studio.checkpoint()
            index = self.vertex_at(event.pos())
            if index is not None:
                self.remove_vertex(index)
            else:
                self.insert_vertex(event.pos())
            studio.mark_unsaved()
            event.accept()
            return
        if studio is not None and studio.mode == MODE_EDIT:
            # Text, off-page streams and equipment all enter the same Format
            # inspector. Previously the event fell through to QGraphicsItem,
            # making a visible label appear immutable.
            studio.edit_drawing_format(self)
            event.accept()
            return
        if studio is None or studio.mode != MODE_EDIT:
            if self.data.get("kind") == "table" and self.data.get("row_action") == "tune":
                from azeo_control_trainer.core.hmi.pvms.procedure_graphics import row_at
                row = row_at(self, event.pos())
                if row:
                    self.procedure_row_action("tune", row)
                event.accept()
                return
            if self._dispatch_actions("double_click"):
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def hoverEnterEvent(self, event) -> None:       # noqa: N802
        """Expose edit handles without running the PVM hover contract.

        Static drawing/data elements have neither ``hp_state`` nor a live PVM
        binding.  Calling the PVM tooltip path here raised once per mouse-move
        over every template panel; uncaught exceptions crossing Qt's graphics
        callback boundary eventually terminated the process.
        """
        self._hover = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:       # noqa: N802
        self._hover = False
        self._hover_anchor = None
        self.unsetCursor()
        self.update()
        super().hoverLeaveEvent(event)

    def hoverMoveEvent(self, event) -> None:        # noqa: N802
        studio = getattr(self.scene(), "studio", None)
        if studio is None and self.data.get("kind") == "table" and self.data.get("rows_path"):
            from azeo_control_trainer.core.hmi.pvms.procedure_graphics import row_at
            row = row_at(self, event.pos()) or {}
            self.setToolTip(str(row.get("tooltip") or row.get("instruction") or row.get("description") or row.get("value") or ""))
        editable = studio is not None and studio.mode == MODE_EDIT
        handle = self.handle_at(event.pos()) if editable \
            and self.isSelected() \
            and not getattr(studio, "connect_armed", False) else None
        connection_intent = bool(getattr(studio, "connect_armed", False)) \
            or not self.isSelected()
        side = self.connection_anchor_at(
            self.mapToScene(event.pos()), allow_inside=False) \
            if editable and handle is None and connection_intent else None
        if side != getattr(self, "_hover_anchor", None):
            self._hover_anchor = side
            self.update()
        cursor = _resize_cursor(handle)
        if cursor is not None:
            self.setCursor(cursor)
        elif side:
            self.setCursor(Qt.CrossCursor)
        else:
            self.unsetCursor()
        super().hoverMoveEvent(event)

    def contextMenuEvent(self, event) -> None:      # noqa: N802
        studio = getattr(self.scene(), "studio", None)
        if studio is not None:
            self.setSelected(True)
            studio.drawing_context_menu(self, event.screenPos())
        elif self.data.get("kind") == "table" and (self.data.get("row_help") or self.data.get("row_action")):
            try:
                from azeo_control_trainer.core.presentation.menu_style import studio_menu
                from azeo_control_trainer.core.hmi.pvms.procedure_graphics import row_at
                row = row_at(self, event.pos())
                if row and self.interaction_handler:
                    owner = weakref.ref(self)
                    def show_help():
                        item = owner()
                        if item is not None and item.interaction_handler:
                            item.interaction_handler({"kind": "procedure_help", "target": row.get("type", "")}, item)
                    old_menu = getattr(self, "_row_menu", None)
                    if old_menu is not None:
                        old_menu.close()
                        old_menu.deleteLater()
                    views = self.scene().views()
                    self._row_menu = studio_menu(parent=views[0] if views else None)
                    from azeo_control_trainer.core.hmi.theme.widgets import bind_operator_theme
                    bind_operator_theme(self._row_menu)
                    if self.data.get("row_help"):
                        self._row_menu.addAction("Block Help", show_help)
                    if self.data.get("row_action") == "tune":
                        self._row_menu.addAction("Tune parameter…", lambda: owner() and owner().procedure_row_action("tune", row))
                    if self.data.get("presentation") == "workflow":
                        self._row_menu.addAction("Live workflow / locate step…", lambda: owner() and owner().procedure_row_action("workflow", row))
                        if row.get("equipment"):
                            self._row_menu.addAction("Equipment faceplate…", lambda: owner() and owner().procedure_row_action("equipment", row))
                        self._row_menu.addAction("Procedure trends…", lambda: owner() and owner().procedure_row_action("trends", row))
                    self._row_menu.popup(event.screenPos())
                event.accept()
            except Exception:
                logging.getLogger(__name__).exception("Procedure row help failed")
                event.accept()
        elif self._dispatch_actions("right_click"):
            event.accept()

    def itemChange(self, change, value):            # noqa: N802
        if change == QGraphicsItem.ItemPositionChange \
                and self.data.get("locked"):
            return self.pos()           # Lock: the object stays put
        scene = self.scene()
        studio = getattr(scene, "studio", None) if scene else None
        if change == QGraphicsItem.ItemPositionChange \
                and studio is not None:
            return studio.canvas.snap_item_position(self, value)
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.data["x"] = self.pos().x()
            self.data["y"] = self.pos().y()
            if scene is not None and hasattr(scene, "studio"):
                scene.studio.mark_unsaved()
                scene.studio.reroute_pipes(self)
        return super().itemChange(change, value)

    def anchor_sides(self) -> list:
        """Every connection point and configurable endpoint tip.

        Open strokes expose their actual start/end geometry, not merely the
        bounding box. A polyline whose last point sits halfway up its box
        therefore keeps a connector attached to visible ink.
        """
        automatic = ["n", "s", "e", "w"]
        if self.data.get("kind") == "stream_connector":
            # An off-page stream owns exactly one process continuation.  Four
            # decorative cardinal handles would let a pipe attach to a place
            # that does not describe whether flow enters or leaves the page.
            automatic = []
        if self.data.get("kind") in self.TIP_KINDS:
            # Start/end replace duplicate west/east points on a straight line
            # and remain exact for curves where west/east are not tips.
            automatic = ["n", "s", "start", "end"]
        named = [str(port.get("name", "")).strip()
                 for port in self.data.get("ports", ())
                 if isinstance(port, dict)
                 and str(port.get("name", "")).strip()]
        return list(dict.fromkeys(automatic + named + [
            f"c{i}" for i in range(len(self.data.get("cps", [])))]))

    def _anchor_hit_radius(self, pixels: float = 12.0) -> float:
        return scene_radius(self, pixels)

    def connection_handle(self, side: str) -> QPointF:
        """The grab handle; selected cardinal ports clear resize handles."""
        point = self.mapFromScene(self.anchor(side))
        if self.isSelected() and side in ("n", "s", "e", "w") \
                and self.data.get("kind") != "symbol":
            offset = self._anchor_hit_radius(16.0)
            if side == "n":
                point.setY(point.y() - offset)
            elif side == "s":
                point.setY(point.y() + offset)
            elif side == "w":
                point.setX(point.x() - offset)
            else:
                point.setX(point.x() + offset)
        return point

    def _content_rect(self) -> QRectF:
        """Where the ink is: for symbols, the artwork's bounds within
        the item — pipes must touch the DRAWING, not the whitespace
        the SVG carries around it."""
        rect = self.rect()
        if self.data.get("kind") == "symbol":
            from azeo_control_trainer.core.hmi.pvms.symbols import content_box
            fx, fy, fw, fh = content_box(self.data.get("symbol", ""))
            return QRectF(rect.x() + rect.width() * fx,
                          rect.y() + rect.height() * fy,
                          rect.width() * fw, rect.height() * fh)
        return rect

    def anchor(self, side: str) -> QPointF:
        """A connection point, scene coordinates."""
        rect = self.rect()
        if self.data.get("pipe_junction"):
            return self.mapToScene(rect.center())
        symbol = str(self.data.get("symbol", "")) \
            if self.data.get("kind") == "symbol" else ""
        if symbol:
            outline = _symbol_outline_anchor(
                rect, side,
                mirror_x=bool(self.data.get("mx")),
                mirror_y=bool(self.data.get("my")))
            if outline is not None:
                return self.mapToScene(outline)
        else:
            spec = _outline_spec(side)
            if spec is not None:
                _normal, fx, fy = spec
                return self.mapToScene(_transform_outline_point(
                    rect, fx, fy,
                    mirror_x=bool(self.data.get("mx")),
                    mirror_y=bool(self.data.get("my"))))
        free_point = _edge_point(self._content_rect(), side)
        if free_point is not None:
            return self.mapToScene(free_point)
        if side in ("start", "end") \
                and self.data.get("kind") in self.TIP_KINDS:
            points = stroke_points(self)
            if points:
                return self.mapToScene(points[0 if side == "start" else -1])
        if side.startswith("c"):
            try:
                fx, fy = self.data.get("cps", [])[int(side[1:])]
                return self.mapToScene(QPointF(rect.width() * fx,
                                               rect.height() * fy))
            except (IndexError, ValueError):
                pass
        port = next((one for one in self.data.get("ports", ())
                     if isinstance(one, dict)
                     and str(one.get("name", "")) == side), None)
        if port is not None:
            try:
                fx, fy = float(port.get("x", 0.5)), float(port.get("y", 0.5))
            except (TypeError, ValueError):
                fx, fy = 0.5, 0.5
            # Older display documents persisted every equipment nozzle at a
            # generic box midpoint. A later generator also persisted ports on
            # the SVG's transparent padding box. The plant editor instead
            # reads the real SVG-authored connection coordinate. Upgrade only
            # those recognizable catalog defaults in memory; an
            # engineer-authored custom port remains the stronger contract.
            legacy_defaults = {
                "inlet": (0.0, 0.5), "outlet": (1.0, 0.5),
                "top": (0.5, 0.0), "bottom": (0.5, 1.0),
            }
            expected = legacy_defaults.get(str(side).lower())
            normal = _SYMBOL_PORT_ALIASES.get(str(side).lower())
            old_viewport = None
            if symbol and normal:
                from azeo_control_trainer.core.hmi.pvms.symbols import legacy_viewport_ports
                old_viewport = legacy_viewport_ports(symbol).get(normal)
            catalog_managed = port.get("source") == "catalog"
            legacy_box = "normal" not in port and expected is not None \
                and abs(fx - expected[0]) < 1e-9 \
                and abs(fy - expected[1]) < 1e-9
            legacy_viewport = old_viewport is not None \
                and abs(fx - old_viewport[0]) < 1e-5 \
                and abs(fy - old_viewport[1]) < 1e-5
            if symbol and expected is not None \
                    and (catalog_managed or legacy_box or legacy_viewport):
                declared = _declared_symbol_anchor(
                    rect, symbol, side,
                    mirror_x=bool(self.data.get("mx")),
                    mirror_y=bool(self.data.get("my")))
                if declared is not None:
                    return self.mapToScene(declared)
            point = _transform_outline_point(
                rect, fx, fy,
                mirror_x=bool(self.data.get("mx")),
                mirror_y=bool(self.data.get("my")))
            return self.mapToScene(point)
        if symbol:
            declared = _declared_symbol_anchor(
                rect, symbol, side,
                mirror_x=bool(self.data.get("mx")),
                mirror_y=bool(self.data.get("my")))
            if declared is not None:
                return self.mapToScene(declared)
        content = self._content_rect()
        offsets = {"n": QPointF(content.center().x(), content.top()),
                   "s": QPointF(content.center().x(),
                                content.bottom()),
                   "w": QPointF(content.left(), content.center().y()),
                   "e": QPointF(content.right(),
                                content.center().y())}
        point = offsets.get(side, offsets["e"])
        if symbol and side in offsets:
            _token, point, _distance = _nearest_symbol_outline(
                rect, symbol, point,
                mirror_x=bool(self.data.get("mx")),
                mirror_y=bool(self.data.get("my")))
        return self.mapToScene(point)

    def nearest_side(self, scene_pos) -> str:
        """The connection point closest to the mouse — including the
        user's own added points."""
        best, best_d = "e", None
        for side in self.anchor_sides():
            p = self.anchor(side)
            d = (p.x() - scene_pos.x()) ** 2 \
                + (p.y() - scene_pos.y()) ** 2
            if best_d is None or d < best_d:
                best, best_d = side, d
        return best

    def connection_anchor_at(self, scene_pos, *, pixels: float = 12.0,
                             allow_inside: bool = False) -> str | None:
        """Resolve the exact point under the pointer on any object edge.

        Named and authored ports remain stronger magnetic targets. Otherwise
        the result is a normalized perimeter token that can be persisted by a
        pipe without adding configuration data to the connected symbol.
        """
        local = self.mapFromScene(scene_pos)
        fixed = self.anchor_hit(local, pixels)
        if fixed is not None:
            return fixed
        if self.data.get("kind") == "stream_connector":
            # Dropping anywhere on the compact chevron resolves to its one
            # semantic port.  This gives it a generous magnetic target while
            # preserving an exact saved endpoint on the visible outline.
            return "process" if allow_inside and self.rect().contains(local) \
                else None
        if self.data.get("kind") == "symbol":
            token, _point, distance = _nearest_symbol_outline(
                self.rect(), str(self.data.get("symbol", "")), local,
                mirror_x=bool(self.data.get("mx")),
                mirror_y=bool(self.data.get("my")))
            hit = self._anchor_hit_radius(pixels)
            if allow_inside and self.rect().contains(local):
                return token
            return token if distance <= hit ** 2 else None
        kind = str(self.data.get("kind", ""))
        path = None
        if kind == "ellipse":
            path = QPainterPath()
            path.addEllipse(self.rect())
        elif kind == "round_rect":
            from azeo_control_trainer.core.hmi.pvms.shapes import rounded_rect_path
            path = rounded_rect_path(
                self.rect(), float(self.data.get("radius", 12)))
        elif kind in SHAPE_KINDS:
            from azeo_control_trainer.core.hmi.pvms.shapes import shape_path
            path = shape_path(kind, self.rect(), self.data)
        if path is not None:
            token, _point, distance = _nearest_path_outline(
                path, self.rect(), local)
            hit = self._anchor_hit_radius(pixels)
            if allow_inside and path.contains(local):
                return token
            return token if distance <= hit ** 2 else None
        content = self._content_rect()
        token, _point, distance = _nearest_edge(content, local)
        hit = self._anchor_hit_radius(pixels)
        if allow_inside and content.contains(local):
            return token
        return token if distance <= hit ** 2 else None

    def connection_anchor_on_axis(self, side: str, coordinate: float, *,
                                  horizontal: bool) -> str:
        """Perimeter token on an exact scene X/Y for run alignment."""
        existing = self.anchor(side)
        scene_probe = QPointF(existing.x(), coordinate) if horizontal \
            else QPointF(coordinate, existing.y())
        local = self.mapFromScene(scene_probe)
        if self.data.get("kind") == "symbol":
            return _axis_symbol_outline_token(
                self.rect(), str(self.data.get("symbol", "")), local, side,
                horizontal=horizontal,
                mirror_x=bool(self.data.get("mx")),
                mirror_y=bool(self.data.get("my")))
        content = self._content_rect()
        token, _point, _distance = _nearest_edge(content, local)
        spec = _free_edge_spec(token)
        if spec is None:
            return side
        _normal, fraction = spec
        return _free_edge_token(side, fraction)

    def add_connection_point(self, scene_pos) -> str:
        """The builder's 'Add connection point here'."""
        local = self.mapFromScene(scene_pos)
        rect = self.rect()
        fx = max(0.0, min(1.0, local.x() / max(rect.width(), 1)))
        fy = max(0.0, min(1.0, local.y() / max(rect.height(), 1)))
        self.data.setdefault("cps", []).append(
            [round(fx, 3), round(fy, 3)])
        self.update()
        return f"c{len(self.data['cps']) - 1}"

    def anchor_hit(self, local_pos, radius: float = 12.0) -> str | None:
        """Which connection point (if any) a local press landed on —
        the anchors are drag SOURCES, like the builder's."""
        radius = self._anchor_hit_radius(radius)
        sides = self.anchor_sides()
        if self.isSelected():
            named = {str(port.get("name", ""))
                     for port in self.data.get("ports", ())
                     if isinstance(port, dict)}
            # A selected edge may carry both a semantic port and a compass
            # port. Prefer the engineer-authored name in that editing state;
            # an unselected click-connect keeps the simple N/S/E/W contract.
            sides = sorted(sides, key=lambda side: side not in named)
        for side in sides:
            p = self.connection_handle(side)
            if (p.x() - local_pos.x()) ** 2 \
                    + (p.y() - local_pos.y()) ** 2 <= radius ** 2:
                return side
        return None

    def remove_connection_point(self, side: str) -> bool:
        """Drop one custom point, renumbering the pipes attached here.

        `cps` is positional, so removing c0 turns c1 into c0 — a pipe
        still naming the old index silently lands on a different
        point, or off the item entirely. The renumbering happens HERE
        rather than in the caller because a caller that forgets it
        leaves the display quietly wrong.
        """
        if not side.startswith("c"):
            return False
        try:
            index = int(side[1:])
            self.data.get("cps", []).pop(index)
        except (IndexError, ValueError):
            return False
        scene = self.scene()
        studio = getattr(scene, "studio", None) if scene else None
        if studio is not None:
            studio.renumber_pipe_anchors(self, index)
        self.update()
        return True

    def _draw_connection_points(
            self, painter: QPainter, sides: tuple[str, ...] | None = None
    ) -> None:
        """Magnetic ports: blue automatic, teal semantic/authored."""
        painter.setRenderHint(QPainter.Antialiasing, True)
        named = {str(port.get("name", ""))
                 for port in self.data.get("ports", ())
                 if isinstance(port, dict)}
        studio = getattr(self.scene(), "studio", None) \
            if self.scene() is not None else None
        sides = sides if sides is not None \
            else visible_connector_sides(self, studio)
        for side in sides:
            p = self.connection_handle(side)
            hot = side in (getattr(self, "_hover_anchor", None),
                           getattr(self, "_connect_hot_anchor", None))
            if side.startswith("c") or side in named:
                colour = QColor(WF["cmd_teal"])
            elif side in ("start", "end"):
                colour = QColor(WF["lapis"])
            else:
                colour = QColor(WF["lapis"])
            pen = QPen(colour, 1.6 if hot else 1.2)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(QBrush(colour if hot else QColor("#FFFFFF")))
            radius = self._anchor_hit_radius(5.0 if hot else 3.8)
            painter.drawEllipse(p, radius, radius)
            if self.isSelected() and side in ("n", "s", "e", "w") \
                    and self.data.get("kind") != "symbol":
                edge = self.mapFromScene(self.anchor(side))
                tether = QPen(colour, 0.8, Qt.DotLine)
                tether.setCosmetic(True)
                painter.setPen(tether)
                painter.drawLine(edge, p)
        hot_anchor = getattr(self, "_connect_hot_anchor", None) \
            or getattr(self, "_hover_anchor", None)
        if _free_edge_spec(str(hot_anchor or "")) is not None \
                or _outline_spec(str(hot_anchor or "")) is not None:
            point = self.mapFromScene(self.anchor(hot_anchor))
            colour = QColor(WF["cmd_teal"])
            pen = QPen(colour, 1.6)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(QBrush(colour))
            radius = self._anchor_hit_radius(5.0)
            painter.drawEllipse(point, radius, radius)

    def shape(self):
        path = QPainterPath()
        path.addRect(self.rect())
        studio = getattr(self.scene(), "studio", None) \
            if self.scene() is not None else None
        sides = visible_connector_sides(self, studio)
        if sides:
            radius = self._anchor_hit_radius(8.0)
            for side in sides:
                point = self.connection_handle(side)
                path.addEllipse(point, radius, radius)
        return path

    def _effectively_hidden(self) -> bool:
        """Visibility is a property (paper: name, geometry,
        visibility). Hidden draws nothing outside EDIT; in EDIT it
        ghosts at low opacity so the author can still find it."""
        if self.data.get("visible", True):
            return False
        studio = getattr(self.scene(), "studio", None) \
            if self.scene() else None
        return studio is None or studio.mode != MODE_EDIT

    def paint(self, painter: QPainter, option, widget=None) -> None:
        kind = self.data["kind"]
        rect = self.rect()
        if self._effectively_hidden():
            return
        try:
            opacity = max(0.0, min(
                1.0, float(self.data.get("opacity", 100)) / 100.0))
        except (TypeError, ValueError):
            opacity = 1.0
        painter.setOpacity(opacity)
        if not self.data.get("visible", True):
            painter.setOpacity(opacity * 0.25)
        line = self.line_colour()
        fill = self.fill_colour()
        # Mirroring: a flipped symbol stays a symbol.
        if self.data.get("mx") or self.data.get("my"):
            painter.save()
            painter.translate(rect.center())
            painter.scale(-1 if self.data.get("mx") else 1,
                          -1 if self.data.get("my") else 1)
            painter.translate(-rect.center())
            self._mirror_saved = True
        else:
            self._mirror_saved = False
        if kind == "datalink":
            from azeo_control_trainer.core.hmi.pvms.elements import datalink_text
            result = self.binding.result if self.binding is not None \
                else None
            try:
                decimals = int(self.data.get("decimals", 1) or 0)
            except (TypeError, ValueError):
                decimals = 1
            shown = datalink_text(
                self.data.get("datalink_type", "numeric"), result,
                decimals=decimals,
                units=bool(self.data.get("units", False)))
            studio = getattr(self.scene(), "studio", None) \
                if self.scene() else None
            text = shown.text
            labels = self.data.get("boolean_labels")
            if isinstance(labels, dict) and not shown.is_error \
                    and result is not None:
                raw = result.value
                if isinstance(raw, str):
                    truth = raw.strip().lower() in (
                        "1", "true", "yes", "on", "active", "running",
                        "open")
                else:
                    truth = bool(raw)
                text = str(labels.get(
                    "true" if truth else "false",
                    "TRUE" if truth else "FALSE"))
            # An unconfigured new link says what it is while the author
            # is editing. Online it says @@@@@@@@, the manual's honest
            # no-communication mark, rather than leaking editor chrome.
            if self.binding_error and studio is not None \
                    and studio.mode == MODE_EDIT:
                text = self.data.get("path") or "Data Link"
            painter.setFont(QFont("Segoe UI", 10, QFont.Bold))
            role = Role.TEXT_FAINT if shown.is_error \
                or self.binding_error else Role.TEXT
            painter.setPen(QColor(self._palette[role]))
            flags = Qt.AlignLeft | Qt.AlignVCenter
            if self.data.get("word_wrap"):
                flags |= Qt.TextWordWrap
            painter.drawText(rect, flags, str(text))
        elif kind == "display_link":
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(QPen(QColor(self._palette[Role.ACTION]), 1.4))
            painter.setBrush(QBrush(QColor(
                self._palette[Role.SURFACE_FIELD])))
            painter.drawRoundedRect(rect, 3.0, 3.0)
            painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
            painter.setPen(QColor(self._palette[Role.ACTION_DEEP]))
            label = self.data.get("text") \
                or self.data.get("target") or "Display Link"
            painter.drawText(rect.adjusted(7, 0, -20, 0),
                             Qt.AlignLeft | Qt.AlignVCenter, str(label))
            painter.drawText(rect.adjusted(0, 0, -6, 0),
                             Qt.AlignRight | Qt.AlignVCenter, "→")
        elif kind == "user_entry":
            self._paint_user_entry(painter, rect)
        elif kind in ("chart", "alarm_list", "multi_point", "radar_plot",
                      "tab", "date_time", "table"):
            self._paint_data_element(painter, rect)
        elif kind == "faceplate_section":
            self._paint_live_section(painter, rect)
        elif kind == "icon_button":
            from azeo_control_trainer.core.hmi.pvms.elements import actions_of
            from azeo_control_trainer.core.hmi.pvms.faceplate_icons import draw_faceplate_icon
            draw_faceplate_icon(
                painter, rect, str(self.data.get("icon", "module_detail")),
                button=bool(self.data.get("button", False)
                            or actions_of(self.data)),
                pressed=bool(self._action_pressed),
                enabled=bool(self.data.get("enabled", True)))
        elif kind == "special_symbol":
            from azeo_control_trainer.core.hmi.pvms.faceplate_icons import (
                SPECIAL_SYMBOL_BUTTONS,
                draw_faceplate_icon,
            )
            name = str(self.data.get("icon", "module_detail"))
            draw_faceplate_icon(
                painter, rect, name,
                button=name in SPECIAL_SYMBOL_BUTTONS,
                pressed=bool(self._action_pressed),
                enabled=bool(self.data.get("enabled", True)))
        elif kind == "stream_connector":
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(self.pen())
            painter.setBrush(QBrush(
                fill if fill is not None
                else QColor(self._palette[Role.SURFACE_FIELD])))
            painter.drawPath(_stream_connector_path(rect))
            font = QFont(str(self.data.get("font_family") or "Segoe UI"))
            try:
                font.setPointSizeF(max(
                    1.0, float(self.data.get("font_size", 8.0))))
            except (TypeError, ValueError):
                font.setPointSizeF(8.0)
            font.setBold(bool(self.data.get("font_bold", True)))
            font.setItalic(bool(self.data.get("font_italic", False)))
            font.setUnderline(bool(self.data.get("font_underline", False)))
            painter.setFont(font)
            painter.setPen(self.text_colour())
            tip = min(max(rect.height() * 0.48, 8.0),
                      rect.width() * 0.22)
            horizontal = {"left": Qt.AlignLeft,
                          "center": Qt.AlignHCenter,
                          "right": Qt.AlignRight}.get(
                              self.data.get("text_halign", "center"),
                              Qt.AlignHCenter)
            vertical = {"top": Qt.AlignTop,
                        "middle": Qt.AlignVCenter,
                        "bottom": Qt.AlignBottom}.get(
                            self.data.get("text_valign", "middle"),
                            Qt.AlignVCenter)
            text_flags = horizontal | vertical
            text_flags |= Qt.TextWordWrap if self.data.get(
                "text_wrap", False) else Qt.TextSingleLine
            painter.drawText(
                rect.adjusted(7.0, 0.0, -(tip + 3.0), 0.0),
                text_flags,
                str(self.data.get("text", "PROCESS STREAM")))
        elif kind == "symbol":
            from azeo_control_trainer.core.hmi.pvms.symbols import renderer, tinted_pixmap
            name = self.data.get("symbol", "")
            if self.data.get("fill"):
                # An authored literal keeps precedence: the whole silhouette
                # is tinted with it, whatever the theme.
                pixmap = tinted_pixmap(name, int(rect.width()),
                                       int(rect.height()),
                                       fill.name())
                if pixmap is not None:
                    painter.drawPixmap(rect.toRect(), pixmap)
            else:
                # Everything else asks the theme. `line` already resolved to
                # the authored role or EQUIPMENT; an unauthored fill is
                # EQUIPMENT_FILL. Rendering the artwork's own grey here left
                # every vessel and pump the same colour on a dark station.
                svg = renderer(name, line=line.name(), fill=(
                    fill.name() if fill is not None
                    else self._palette[Role.EQUIPMENT_FILL]), text=self.text_colour().name())
                if svg is not None:
                    painter.setRenderHint(QPainter.Antialiasing, True)
                    svg.render(painter, rect)
                else:
                    painter.setPen(QPen(QColor(WF["isa_line"]), 1.0,
                                        Qt.DashLine))
                    painter.drawRect(rect)
            label = str(self.data.get("text", "") or "")
            if label:
                font = QFont(str(
                    self.data.get("font_family") or "Segoe UI"))
                try:
                    font.setPointSizeF(max(
                        1.0, float(self.data.get("font_size", 9.0))))
                except (TypeError, ValueError):
                    font.setPointSizeF(9.0)
                font.setBold(bool(self.data.get("font_bold", False)))
                font.setItalic(bool(self.data.get("font_italic", False)))
                font.setUnderline(bool(
                    self.data.get("font_underline", False)))
                painter.setFont(font)
                painter.setPen(self.text_colour())
                horizontal = {"left": Qt.AlignLeft,
                              "center": Qt.AlignHCenter,
                              "right": Qt.AlignRight}.get(
                                  self.data.get("text_halign", "center"),
                                  Qt.AlignHCenter)
                vertical = {"top": Qt.AlignTop,
                            "middle": Qt.AlignVCenter,
                            "bottom": Qt.AlignBottom}.get(
                                self.data.get("text_valign", "middle"),
                                Qt.AlignVCenter)
                flags = horizontal | vertical
                flags |= Qt.TextWordWrap if self.data.get(
                    "text_wrap", False) else Qt.TextSingleLine
                painter.drawText(rect.adjusted(4, 3, -4, -3), flags, label)
        elif kind in ("rect", "square"):
            painter.setPen(self.pen())
            fraction = self._fill_fraction()
            if fill is not None and fraction < 1.0:
                # Fill Percent (graphics paper p.31): any shape can
                # show a level — outline full, fill from the bottom.
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(rect)
                painter.fillRect(
                    self._fill_clip(rect.adjusted(1, 1, -1, -1), fraction),
                    fill)
            else:
                painter.setBrush(QBrush(fill) if fill is not None
                                 else Qt.NoBrush)
                painter.drawRect(rect)
        elif kind == "ellipse":
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(self.pen())
            fraction = self._fill_fraction()
            if fill is not None and fraction < 1.0:
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(rect)
                painter.save()
                painter.setClipRect(self._fill_clip(rect, fraction))
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(fill))
                painter.drawEllipse(rect)
                painter.restore()
            else:
                painter.setBrush(QBrush(fill) if fill is not None
                                 else Qt.NoBrush)
                painter.drawEllipse(rect)
        elif kind in SHAPE_KINDS:
            # The builder's primitive vocabulary, one path branch:
            # every closed shape is authored on a 100x100 grid and
            # stretched here, so a shape drawn in the builder and one
            # drawn in the studio are the same shape.
            from azeo_control_trainer.core.hmi.pvms.shapes import shape_path
            path = shape_path(kind, rect, self.data)
            if path is not None:
                painter.setRenderHint(QPainter.Antialiasing, True)
                painter.setPen(self.pen())
                fraction = self._fill_fraction()
                if fill is not None and fraction < 1.0:
                    # Fill Percent generalizes to any outline: draw
                    # the shape empty, then clip its own path to the
                    # level band.
                    painter.setBrush(Qt.NoBrush)
                    painter.drawPath(path)
                    painter.save()
                    painter.setClipRect(self._fill_clip(rect, fraction))
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QBrush(fill))
                    painter.drawPath(path)
                    painter.restore()
                else:
                    painter.setBrush(QBrush(fill) if fill is not None
                                     else Qt.NoBrush)
                    painter.drawPath(path)
        elif kind == "round_rect":
            from azeo_control_trainer.core.hmi.pvms.shapes import rounded_rect_path
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(self.pen())
            painter.setBrush(QBrush(fill) if fill is not None
                             else Qt.NoBrush)
            painter.drawPath(rounded_rect_path(
                rect, float(self.data.get("radius", 12))))
        elif kind == "freehand":
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(self.pen())
            painter.setBrush(Qt.NoBrush)
            ends = stroke_points(self)
            crossover = self.data.get("crossover", "")
            if len(ends) >= 2 and crossover in ("gap", "jump"):
                draw_crossed_polyline(
                    painter, self, ends, crossover,
                    float(self.data.get("width", 2.0)))
            elif len(self.data.get("points") or []) >= 2:
                from azeo_control_trainer.core.hmi.pvms.shapes import smoothed_path
                painter.drawPath(smoothed_path(
                    [(p[0], p[1])
                     for p in self.data.get("points", ())]))
            self._draw_arrows(painter, ends)
        elif kind == "arc":
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(self.pen())
            painter.setBrush(Qt.NoBrush)
            ends = stroke_points(self)
            crossover = self.data.get("crossover", "")
            if crossover in ("gap", "jump"):
                draw_crossed_polyline(
                    painter, self, ends, crossover,
                    float(self.data.get("width", 2.0)))
            else:
                painter.drawArc(rect,
                                int(self.data.get("start", 0)) * 16,
                                int(self.data.get("span", 180)) * 16)
            self._draw_arrows(painter, ends)
        elif kind == "polyline":
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(self.pen())
            ends = stroke_points(self)
            crossover = self.data.get("crossover", "")
            if crossover in ("gap", "jump"):
                draw_crossed_polyline(
                    painter, self, ends, crossover,
                    float(self.data.get("width", 2.0)))
            else:
                painter.drawPolyline(ends)
            self._draw_arrows(painter, ends)
        elif kind == "line":
            painter.setPen(self.pen())
            mid = rect.height() / 2
            ends = [QPointF(rect.left(), rect.top() + mid),
                    QPointF(rect.right(), rect.top() + mid)]
            crossover = self.data.get("crossover", "")
            if crossover in ("gap", "jump"):
                draw_crossed_polyline(
                    painter, self, ends, crossover,
                    float(self.data.get("width", 2.0)))
            else:
                painter.drawLine(ends[0], ends[1])
            self._draw_arrows(painter, ends)
        else:
            font, flags = self.text_layout()
            painter.setFont(font)
            painter.setPen(line if self.data.get("line")
                           and not self.data.get("text_color")
                           and not self.data.get("text_role")
                           else self.text_colour(Role.TEXT_DIM))
            painter.drawText(rect, flags, str(self.data.get("text", "")))
        if getattr(self, "_mirror_saved", False):
            painter.restore()
        if self.isSelected():
            if self.shows_selection_frame():
                painter.setPen(QPen(QColor(self._palette[Role.ACTION]),
                                    1.0, Qt.DashLine))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(rect)
            if self.is_point_kind() and not self.data.get("locked"):
                # An open stroke shows its POINTS, not a box frame:
                # that is how it is reshaped.
                action = QColor(self._palette[Role.ACTION])
                painter.setRenderHint(QPainter.Antialiasing, True)
                painter.setPen(QPen(action, 1.0))
                painter.setBrush(QColor("#FFFFFF"))
                for point in self.vertices():
                    painter.drawEllipse(point, 4.0, 4.0)
            elif not self.data.get("locked"):
                # Eight resize handles plus the rotation handle above
                # the top edge, on its tether — the builder's frame.
                action = QColor(self._palette[Role.ACTION])
                painter.setRenderHint(QPainter.Antialiasing, False)
                painter.setPen(QPen(action, 1.0))
                painter.setBrush(QColor("#FFFFFF"))
                half = self._anchor_hit_radius(4.5)
                for centre in self.handle_points().values():
                    painter.drawRect(QRectF(centre.x() - half,
                                            centre.y() - half,
                                            half * 2.0, half * 2.0))
                knob = self.rotate_handle_point()
                painter.setPen(QPen(action, 1.0, Qt.DotLine))
                painter.drawLine(QPointF(knob.x(), rect.top()), knob)
                painter.setRenderHint(QPainter.Antialiasing, True)
                painter.setPen(QPen(action, 1.0))
                painter.setBrush(QColor("#FFFFFF"))
                knob_radius = self._anchor_hit_radius(self.KNOB_RADIUS)
                painter.drawEllipse(knob, knob_radius, knob_radius)
                studio = getattr(self.scene(), "studio", None) \
                    if self.scene() else None
                connector_sides = visible_connector_sides(self, studio)
                if connector_sides:
                    self._draw_connection_points(painter, connector_sides)
        elif (self._hover or getattr(self, "_connect_hot_anchor", None)) \
                and not self.data.get("locked"):
            studio = getattr(self.scene(), "studio", None)
            if studio is not None and studio.mode == MODE_EDIT:
                connector_sides = visible_connector_sides(self, studio)
                if connector_sides:
                    self._draw_connection_points(painter, connector_sides)

    def _paint_user_entry(self, painter: QPainter, rect: QRectF) -> None:
        """Paint all seven User Entry controls from one configuration."""
        from azeo_control_trainer.core.hmi.pvms.elements import (BUTTON, CHECK_BOX, COMBO_BOX,
                                RADIO_BUTTON, SLEW, SLIDER, TEXT_ENTRY,
                                USER_ENTRY_TITLES, UserEntry)

        entry = UserEntry.from_dict(self.data.get("entry", {}))
        result = self.binding.result if self.binding is not None else None
        current = getattr(result, "value", None)
        disabled = bool(entry.disabled_reason or not self.write_allowed or not self.data.get("enabled", True))
        action = QColor(self._palette[
            Role.TEXT_FAINT if disabled else Role.ACTION])
        text = QColor(self._palette[
            Role.TEXT_DIM if disabled else Role.TEXT])
        field = QColor(self._palette[Role.SURFACE_FIELD])
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
        painter.setPen(QPen(action, 1.3))
        painter.setBrush(QBrush(field))
        label = entry.label or USER_ENTRY_TITLES.get(
            entry.kind, "User Entry")
        if entry.kind == BUTTON:
            painter.drawRoundedRect(rect, 3, 3)
            painter.setPen(text)
            painter.drawText(rect, Qt.AlignCenter, label)
        elif entry.kind == CHECK_BOX:
            box = QRectF(rect.left() + 4, rect.center().y() - 8, 16, 16)
            painter.drawRect(box)
            if bool(current):
                painter.drawLine(box.left() + 3, box.center().y(),
                                 box.center().x() - 1, box.bottom() - 3)
                painter.drawLine(box.center().x() - 1, box.bottom() - 3,
                                 box.right() - 2, box.top() + 3)
            painter.setPen(text)
            painter.drawText(rect.adjusted(26, 0, -3, 0),
                             Qt.AlignLeft | Qt.AlignVCenter, label)
        elif entry.kind in (COMBO_BOX, RADIO_BUTTON):
            if entry.kind == COMBO_BOX:
                painter.drawRoundedRect(rect, 2, 2)
                shown = next(
                    (str(caption) for one, caption in entry.options
                     if one == current), str(current or label))
                painter.setPen(text)
                painter.drawText(rect.adjusted(6, 0, -20, 0),
                                 Qt.AlignLeft | Qt.AlignVCenter, shown)
                painter.drawText(rect.adjusted(0, 0, -6, 0),
                                 Qt.AlignRight | Qt.AlignVCenter, "▾")
            else:
                x = rect.left() + 6
                painter.setFont(QFont("Segoe UI", 8))
                for one, caption in entry.options:
                    dot = QRectF(x, rect.center().y() - 6, 12, 12)
                    painter.setPen(QPen(action, 1.2))
                    painter.setBrush(QBrush(action) if one == current
                                     else Qt.NoBrush)
                    painter.drawEllipse(dot)
                    painter.setPen(text)
                    painter.drawText(QRectF(x + 16, rect.top(), 54,
                                            rect.height()),
                                     Qt.AlignLeft | Qt.AlignVCenter,
                                     str(caption))
                    x += 70
        elif entry.kind == SLEW:
            painter.drawRoundedRect(rect, 2, 2)
            quarter = rect.width() / 4
            painter.drawLine(int(rect.left() + quarter), int(rect.top()),
                             int(rect.left() + quarter), int(rect.bottom()))
            painter.drawLine(int(rect.right() - quarter), int(rect.top()),
                             int(rect.right() - quarter), int(rect.bottom()))
            painter.setPen(text)
            painter.drawText(QRectF(rect.left(), rect.top(), quarter,
                                    rect.height()), Qt.AlignCenter, "-")
            painter.drawText(QRectF(rect.right() - quarter, rect.top(),
                                    quarter, rect.height()),
                             Qt.AlignCenter, "+")
            painter.drawText(rect.adjusted(quarter, 0, -quarter, 0),
                             Qt.AlignCenter,
                             str(current if current is not None else label))
        elif entry.kind == SLIDER:
            try:
                fraction = ((float(current) - entry.lo)
                            / (entry.hi - entry.lo))
            except (TypeError, ValueError, ZeroDivisionError):
                fraction = 0.0
            fraction = max(0.0, min(1.0, fraction))
            y = rect.center().y()
            painter.setPen(QPen(action, 3))
            painter.drawLine(int(rect.left() + 8), int(y),
                             int(rect.right() - 8), int(y))
            x = rect.left() + 8 + fraction * max(0.0, rect.width() - 16)
            painter.setBrush(QBrush(action))
            painter.drawEllipse(QPointF(x, y), 6, 6)
        elif entry.kind == TEXT_ENTRY:
            painter.drawRoundedRect(rect, 2, 2)
            painter.setPen(text)
            painter.drawText(rect.adjusted(6, 0, -6, 0),
                             Qt.AlignLeft | Qt.AlignVCenter,
                             str(current if current is not None else label))

    def sample_compound(self) -> bool:
        """Append one sample for every chart/axis binding that can be read."""
        kind = self.data.get("kind")
        if kind not in ("chart", "multi_point", "radar_plot"):
            return False
        sampled = False
        for path, binding in self.bindings.items():
            result = binding.result
            if result is None or result.quality.name == "BAD":
                continue
            try:
                value = float(result.value)
            except (TypeError, ValueError):
                continue
            history = self.histories[path]
            if not history or history[-1] != value \
                    or kind == "chart":
                history.append(value)
                sampled = True
        return sampled

    def _paint_live_section(self, painter: QPainter, rect: QRectF) -> None:
        """Refresh the shipped section hosted by a graphics proxy.

        The proxy supplies real tab/scroll event delivery and owns the QWidget,
        so this is neither a dead painted control nor a native top-level window.
        """
        from ..faceplate_sections import LIVE_SECTIONS

        key = str(self.data.get("section", "") or "")
        spec = LIVE_SECTIONS.get(key)
        if spec is None:
            painter.setPen(QPen(QColor(WF["isa_line"]), 1.0, Qt.DashLine))
            painter.drawRect(rect)
            return
        self._ensure_live_section()
        widget = getattr(self, "_section_widget", None)
        proxy = getattr(self, "_section_proxy", None)
        if widget is None or proxy is None:
            painter.setPen(QPen(QColor(WF["crit"]), 1.0, Qt.DashLine))
            painter.drawRect(rect)
            return
        size = (max(8, int(rect.width())), max(8, int(rect.height())))
        if (widget.width(), widget.height()) != size:
            widget.resize(*size)
            if widget.layout() is not None:
                widget.layout().activate()
        proxy.setGeometry(rect)
        scene = self.scene()
        studio = getattr(scene, "studio", None) if scene is not None else None
        proxy.setAcceptedMouseButtons(
            Qt.NoButton if studio is not None and studio.mode == MODE_EDIT
            else Qt.AllButtons)
        bound = self._section_bound()
        # Refresh only when a value moved. `refresh` rebuilds table rows,
        # and paint runs per item per frame — the same arithmetic that
        # made a per-frame tooltip 4.5 ms on a display of forty.
        signature = tuple(
            self._section_result_signature(key_name, binding)
            for key_name, binding in sorted(bound.items()))
        if signature != getattr(self, "_section_signature", None):
            try:
                widget.refresh(bound)
            except Exception as error:              # noqa: BLE001
                logging.getLogger(__name__).exception(
                    "Could not refresh hosted faceplate section %s", key)
                self.binding_error = (
                    f"hosted section refresh failed: {error}")
                widget.setToolTip(self.binding_error)
                proxy.setOpacity(0.35)
            else:
                if self.binding_error.startswith(
                        "hosted section refresh failed:"):
                    self.binding_error = ""
                widget.setToolTip("")
                proxy.setOpacity(1.0)
                self._section_signature = signature

    def _ensure_live_section(self) -> None:
        """Create the real hosted widget once, owned by a graphics proxy."""
        from ..faceplate_sections import LIVE_SECTIONS, live_section_class

        key = str(self.data.get("section", "") or "")
        spec = LIVE_SECTIONS.get(key)
        if spec is None:
            return
        if getattr(self, "_section_widget", None) is not None \
                and getattr(self, "_section_key", "") == key:
            return
        try:
            widget = live_section_class(key)(
                self._palette, **dict(spec.get("kwargs", {})))
            # Explicit widget semantics keep QApplication from classifying the
            # proxy-owned surface as a native top-level window.
            widget.setWindowFlags(Qt.Widget)
            proxy = QGraphicsProxyWidget(self)
            proxy.setWidget(widget)
            proxy.setZValue(1.0)
            # Authoring is the safe default before the item joins a scene.
            # Operator paint enables events once no Studio owner is present.
            proxy.setAcceptedMouseButtons(Qt.NoButton)
        except Exception:                           # noqa: BLE001
            logging.getLogger(__name__).exception(
                "Could not create hosted faceplate section %s", key)
            self._section_widget = None
            self._section_proxy = None
            return
        self._section_widget = widget
        self._section_proxy = proxy
        self._section_key = key
        self._section_signature = None

    @staticmethod
    def _section_result_signature(key_name, binding) -> tuple:
        result = getattr(binding, "result", None)
        quality = getattr(getattr(result, "quality", None), "name", None)
        limit = getattr(getattr(result, "limit", None), "name", None)
        return (
            key_name,
            getattr(result, "value", None), quality, limit,
            bool(getattr(result, "forced", False)),
            getattr(result, "units", ""),
            getattr(result, "eu_range", None),
            bool(getattr(result, "alarm_active", False)),
            bool(getattr(result, "alarm_acked", False)),
            getattr(result, "alarm_priority", 0),
            getattr(result, "alarm_condition", ""),
            getattr(result, "alarm_count", 0),
            bool(getattr(result, "alarm_suppressed", False)),
            getattr(result, "module_running", None),
            getattr(result, "last_good_value", None),
            getattr(result, "last_good_at", None),
        )

    def _section_bound(self) -> dict:
        """`{binding key: binding}` for the hosted section's contract."""
        paths = self.data.get("paths") or {}
        if not isinstance(paths, dict):
            return {}
        bound = {}
        for key_name, path in paths.items():
            binding = self.bindings.get(str(path))
            if binding is not None:
                bound[str(key_name)] = binding
        return bound

    def _paint_data_element(self, painter: QPainter, rect: QRectF) -> None:
        """Paint the placeable compound data elements on the shared path."""
        import math
        from datetime import datetime, timezone

        kind = self.data.get("kind")
        panel = QColor(self._palette[Role.SURFACE_PANEL])
        field = QColor(self._palette[Role.SURFACE_SUNK])
        text = QColor(self._palette[Role.TEXT])
        dim = QColor(self._palette[Role.TEXT_DIM])
        line = QColor(self._palette[Role.LINE_SOFT])
        action = QColor(self._palette[Role.ACTION])
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(line, 1))
        painter.setBrush(QBrush(panel))
        painter.drawRect(rect)
        if kind == "date_time":
            zone = timezone.utc if self.data.get("timezone") == "utc" else None
            now = datetime.now(zone)
            fmt = str(self.data.get("format", "%Y-%m-%d  %H:%M:%S"))
            try:
                shown = now.strftime(fmt)
            except (ValueError, TypeError):
                shown = now.strftime("%Y-%m-%d  %H:%M:%S")
            painter.setFont(QFont("Segoe UI", 10, QFont.Bold))
            painter.setPen(text)
            painter.drawText(rect.adjusted(6, 3, -6, -3),
                             Qt.AlignCenter, shown)
            return
        if kind == "tab":
            tabs = list(self.data.get("tabs", ()))
            if not tabs:
                painter.setPen(dim)
                painter.drawText(rect, Qt.AlignCenter, "No tab items")
                return
            active = min(len(tabs) - 1, max(
                0, int(self.data.get("active_tab", 0) or 0)))
            strip_h, tab_w = min(28.0, rect.height() * 0.25), \
                rect.width() / len(tabs)
            for index, tab in enumerate(tabs):
                cell = QRectF(rect.left() + index * tab_w, rect.top(),
                              tab_w, strip_h)
                painter.setBrush(QBrush(field if index == active else panel))
                painter.setPen(QPen(action if index == active else line, 1))
                painter.drawRect(cell)
                painter.setPen(text if index == active else dim)
                title = tab.get("title", f"Tab {index + 1}") \
                    if isinstance(tab, dict) else str(tab)
                painter.drawText(cell.adjusted(4, 0, -4, 0),
                                 Qt.AlignCenter, str(title))
            tab = tabs[active]
            content = tab.get("text", "") if isinstance(tab, dict) else ""
            painter.setPen(text)
            painter.drawText(rect.adjusted(8, strip_h + 5, -8, -6),
                             Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
                             str(content))
            return
        if kind == "table":
            if self.data.get("presentation") == "workflow":
                from azeo_control_trainer.core.hmi.pvms.procedure_graphics import paint_workflow
                paint_workflow(self, painter, rect)
                return
            columns = [column for column in self.data.get("columns", ())
                       if isinstance(column, dict)]
            rows = self.table_rows()
            if not columns:
                painter.setPen(dim)
                painter.drawText(rect, Qt.AlignCenter, "No table columns")
                return
            header_h = min(24.0, max(16.0, float(
                self.data.get("header_height", 20) or 20)))
            row_h = min(36.0, max(14.0, float(
                self.data.get("row_height", 20) or 20)))
            weights = []
            for column in columns:
                try:
                    weights.append(max(.1, float(column.get("width", 1))))
                except (TypeError, ValueError):
                    weights.append(1.0)
            total = sum(weights) or 1.0
            x_positions = [rect.left()]
            for weight in weights:
                x_positions.append(x_positions[-1] + rect.width()
                                   * weight / total)
            painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
            for index, column in enumerate(columns):
                cell = QRectF(x_positions[index], rect.top(),
                              x_positions[index + 1] - x_positions[index],
                              header_h)
                painter.setPen(QPen(line, 1))
                painter.setBrush(QBrush(field))
                painter.drawRect(cell)
                painter.setPen(text)
                painter.drawText(cell.adjusted(4, 0, -4, 0),
                                 Qt.AlignLeft | Qt.AlignVCenter,
                                 str(column.get("title")
                                     or column.get("key", "")))
            painter.setFont(QFont("Segoe UI", 8))
            paging = bool(self.data.get("rows_path"))
            max_rows = max(1, int((rect.height() - header_h - (24 if paging else 0)) // row_h))
            offset = min(getattr(self, "_table_offset", 0), max(0, len(rows) - 1))
            if paging and len(rows) > max_rows:
                painter.setPen(text)
                painter.drawText(QRectF(rect.left(), rect.bottom() - 24, rect.width(), 24),
                                 Qt.AlignCenter, "◀ Previous                 Next ▶")
            for row_index, row in enumerate(rows[offset:offset + max_rows]):
                if not isinstance(row, dict):
                    continue
                top = rect.top() + header_h + row_index * row_h
                for column_index, column in enumerate(columns):
                    cell = QRectF(
                        x_positions[column_index], top,
                        x_positions[column_index + 1]
                        - x_positions[column_index], row_h)
                    painter.setPen(QPen(line, 1))
                    painter.setBrush(QBrush(
                        panel if row_index % 2 == 0 else field))
                    painter.drawRect(cell)
                    value = row.get(str(column.get("key", "")), "")
                    colour = text
                    if isinstance(value, dict):
                        value_spec = value
                        path = str(value.get("path", ""))
                        binding = self.bindings.get(path)
                        result = binding.result if binding is not None else None
                        from azeo_control_trainer.core.hmi.pvms.elements import datalink_text
                        try:
                            decimals = int(value.get("decimals", 1))
                        except (TypeError, ValueError):
                            decimals = 1
                        shown = datalink_text(
                            str(value.get("type", "numeric")), result,
                            decimals=decimals,
                            units=bool(value.get("units", False)))
                        value = shown.text
                        labels = value_spec.get("boolean_labels")
                        if isinstance(labels, dict) and not shown.is_error \
                                and result is not None:
                            raw = result.value
                            if isinstance(raw, str):
                                truth = raw.strip().lower() in (
                                    "1", "true", "yes", "on", "active",
                                    "running", "open")
                            else:
                                truth = bool(raw)
                            value = str(labels.get(
                                "true" if truth else "false",
                                "TRUE" if truth else "FALSE"))
                        colour = dim if shown.is_error else text
                    painter.setPen(colour)
                    painter.drawText(cell.adjusted(4, 0, -4, 0),
                                     Qt.AlignLeft | Qt.AlignVCenter,
                                     painter.fontMetrics().elidedText(str(value), Qt.ElideRight, max(1, int(cell.width() - 8))))
            if not rows:
                painter.setPen(dim)
                painter.drawText(rect.adjusted(0, header_h, 0, 0),
                                 Qt.AlignCenter, str(self.data.get("empty_text") or "No table rows"))
            return
        if kind == "chart" and self.data.get("series_path"):
            from azeo_control_trainer.core.hmi.pvms.procedure_graphics import paint_history
            paint_history(self, painter, rect)
            return
        if kind == "alarm_list":
            records = self.alarm_provider() if self.alarm_provider else ()
            priority_min = int(self.data.get("priority_min", 0) or 0)
            prefix = str(self.data.get("path_prefix", ""))
            rows = [row for row in records
                    if (not priority_min or row.priority >= priority_min)
                    and (not prefix or row.key.startswith(prefix))]
            painter.setFont(QFont("Segoe UI", 8))
            row_h = 18
            for index, record in enumerate(rows[:max(
                    1, int((rect.height() - 4) // row_h))]):
                cell = QRectF(rect.left() + 4, rect.top() + 2 + index * row_h,
                              rect.width() - 8, row_h)
                role = {15: Role.ALARM_P1, 11: Role.ALARM_P2,
                        7: Role.ALARM_P3}.get(record.priority, Role.TEXT_DIM)
                painter.setPen(QColor(self._palette[role]))
                state = "SUP" if record.suppressed else \
                    "UNACK" if not record.acknowledged else "ACK"
                painter.drawText(cell, Qt.AlignLeft | Qt.AlignVCenter,
                                 f"{state:<5}  {record.key}")
            if not rows:
                painter.setPen(dim)
                painter.drawText(rect, Qt.AlignCenter, "No active alarms")
            return

        rows = self.data.get(
            "pens" if kind == "chart" else "parameters", ())
        values = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            path = str(row.get("path", ""))
            binding = self.bindings.get(path)
            try:
                value = float(binding.result.value)
                good = binding.result.quality.name != "BAD"
            except (AttributeError, TypeError, ValueError):
                value, good = 0.0, False
            values.append((row, path, value, good, index))

        graph = rect.adjusted(30, 16, -12, -24)
        painter.setPen(QPen(line, 1))
        painter.setBrush(QBrush(field))
        painter.drawRect(graph)
        if kind == "chart":
            palette = (Role.BAR_PV, Role.ACTION, Role.BAR_OUT,
                       Role.ACTION, Role.ALARM_P2, Role.ALARM_P3)
            all_values = [number for _row, path, _value, _good, _index in values
                          for number in self.histories[path]]
            try:
                lo = float(self.data.get(
                    "lo", min(all_values, default=0.0)))
            except (TypeError, ValueError):
                lo = min(all_values, default=0.0)
            try:
                hi = float(self.data.get(
                    "hi", max(all_values, default=100.0)))
            except (TypeError, ValueError):
                hi = max(all_values, default=100.0)
            if hi <= lo:
                hi = lo + 1.0
            for row, path, _value, good, index in values:
                history = list(self.histories[path])
                if not good or len(history) < 2:
                    continue
                points = []
                for sample_index, value in enumerate(history):
                    x = graph.left() + sample_index / max(
                        len(history) - 1, 1) * graph.width()
                    y = graph.bottom() - max(0.0, min(
                        1.0, (value - lo) / (hi - lo))) * graph.height()
                    points.append(QPointF(x, y))
                colour = row.get("color") or self._palette[
                    palette[index % len(palette)]]
                painter.setPen(QPen(QColor(colour), 1.5))
                painter.drawPolyline(points)
            painter.setPen(dim)
            painter.drawText(QRectF(rect.left() + 4, rect.top(), 24,
                                    rect.height()), Qt.AlignCenter,
                             f"{hi:g}\n\n{lo:g}")
            return

        count = len(values)
        if not count:
            painter.setPen(dim)
            painter.drawText(rect, Qt.AlignCenter, "No parameters")
            return
        normalized = []
        for row, _path, value, good, index in values:
            lo, hi = float(row.get("lo", 0.0)), float(row.get("hi", 100.0))
            fraction = max(0.0, min(1.0, (value - lo) / max(hi - lo, 1e-9)))
            normalized.append((fraction if good else None, row, index))
        if kind == "multi_point":
            points = []
            for fraction, row, index in normalized:
                x = graph.left() + index / max(count - 1, 1) * graph.width()
                painter.setPen(QPen(line, 1))
                painter.drawLine(QPointF(x, graph.top()),
                                 QPointF(x, graph.bottom()))
                painter.setPen(dim)
                painter.drawText(QRectF(x - 30, graph.bottom() + 2, 60, 18),
                                 Qt.AlignCenter,
                                 str(row.get("label", index + 1)))
                if fraction is not None:
                    points.append(QPointF(
                        x, graph.bottom() - fraction * graph.height()))
            painter.setPen(QPen(action, 2))
            if len(points) > 1:
                painter.drawPolyline(points)
            return

        centre, radius = graph.center(), min(graph.width(), graph.height()) / 2
        outline, value_points = [], []
        for fraction, row, index in normalized:
            angle = -math.pi / 2 + 2 * math.pi * index / count
            edge = QPointF(centre.x() + math.cos(angle) * radius,
                           centre.y() + math.sin(angle) * radius)
            outline.append(edge)
            painter.setPen(QPen(line, 1))
            painter.drawLine(centre, edge)
            if fraction is not None:
                value_points.append(QPointF(
                    centre.x() + math.cos(angle) * radius * fraction,
                    centre.y() + math.sin(angle) * radius * fraction))
            painter.setPen(dim)
            painter.drawText(QRectF(edge.x() - 30, edge.y() - 9, 60, 18),
                             Qt.AlignCenter, str(row.get("label", index + 1)))
        if outline:
            painter.setPen(QPen(line, 1))
            painter.drawPolygon(outline)
        if len(value_points) == count:
            colour = QColor(action)
            colour.setAlpha(55)
            painter.setBrush(QBrush(colour))
            painter.setPen(QPen(action, 2))
            painter.drawPolygon(value_points)


def rounded_polyline_path(points, radius: float = 7.0) -> QPainterPath:
    """Build a clean orthogonal path with optional rounded elbows."""
    path = QPainterPath()
    if not points:
        return path
    path.moveTo(points[0])
    if len(points) < 3 or radius <= 0:
        for point in points[1:]:
            path.lineTo(point)
        return path
    import math

    for previous, corner, following in zip(
            points, points[1:-1], points[2:]):
        incoming = math.hypot(corner.x() - previous.x(),
                              corner.y() - previous.y())
        outgoing = math.hypot(following.x() - corner.x(),
                              following.y() - corner.y())
        bend = min(float(radius), incoming / 2.0, outgoing / 2.0)
        if bend <= 0.01:
            path.lineTo(corner)
            continue
        before = QPointF(
            corner.x() + (previous.x() - corner.x()) / incoming * bend,
            corner.y() + (previous.y() - corner.y()) / incoming * bend)
        after = QPointF(
            corner.x() + (following.x() - corner.x()) / outgoing * bend,
            corner.y() + (following.y() - corner.y()) / outgoing * bend)
        path.lineTo(before)
        path.quadTo(corner, after)
    path.lineTo(points[-1])
    return path


class PipeItem(QGraphicsRectItem):
    """An orthogonal pipe between two items' anchors.

    Attached endpoints are item ids plus the outline point the user's mouse
    chose.  A deliberately loose end stores ``a_point`` or ``b_point`` so an
    engineer can pull a run into open space and finish it later, as in a
    general-purpose diagrammer. Routing re-runs whenever either connected
    object moves or resizes.
    """

    def __init__(self, data: dict, palette):
        super().__init__()
        self.data = copy.deepcopy(dict(data))
        self.data.setdefault("id", f"itm_{uuid.uuid4().hex[:4]}")
        self._palette = palette
        self._points: list = []
        self.route_status = "unrouted"
        self.route_message = ""
        self.route_collisions: tuple[str, ...] = ()
        self._waypoint_drag = -1
        self._segment_drag = None
        self._endpoint_drag = ""
        self._endpoint_before = None
        self._endpoint_points_before = None
        self._endpoint_target = None
        self._endpoint_moved = False
        self.setFlag(QGraphicsItem.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self.setZValue(float(data["z"]) if "z" in data else -2.0)
        self.setVisible(bool(data.get("visible", True)))

    def line_colour(self) -> QColor:
        return _authored_colour(self.data, self._palette, "line", "line_role", Role.EQUIPMENT)

    @staticmethod
    def _facing_sides(endpoint_a, endpoint_b) -> tuple[str, str]:
        """Fallback sides for legacy connectors with missing port metadata."""
        a_rect = endpoint_a.mapToScene(
            endpoint_a.rect().center())
        b_rect = endpoint_b.mapToScene(
            endpoint_b.rect().center())
        dx = b_rect.x() - a_rect.x()
        dy = b_rect.y() - a_rect.y()
        if abs(dx) >= abs(dy):
            return ("e", "w") if dx >= 0 else ("w", "e")
        return ("s", "n") if dy >= 0 else ("n", "s")

    def route(self, endpoint_a, endpoint_b) -> None:
        """Compatibility entry point used by older callers.

        The scene adapter owns routing so Studio and Station cannot diverge.
        """
        from .pipe_scene import route_pipe
        route_pipe(self.scene(), self, endpoint_a, endpoint_b)

    def apply_route(self, result, *, a_side: str, b_side: str) -> None:
        """Install derived geometry without persisting an automatic path."""
        from .pipe_scene import qpoints
        points = qpoints(result.points)
        self.prepareGeometryChange()
        self._points = points
        self.route_status = result.status
        self.route_message = result.message
        self.route_collisions = result.collisions
        if result.status == "blocked":
            obstruction = ", ".join(result.collisions)
            self.setToolTip("Route blocked: " + result.message
                            + ("\nObstacles: " + obstruction if obstruction else "")
                            + "\nMove the obstruction, adjust the ports or bends, "
                              "or clear Routing obstacle for a background panel.")
        else:
            self.setToolTip("")
        # New connectors persist their selected ports. Keep legacy `auto`
        # records read-only here so merely opening/rerouting an older display
        # does not dirty its document.
        if not self.data.get("auto", True):
            self.data["a_side"], self.data["b_side"] = a_side, b_side
        xs = [p.x() for p in points]
        ys = [p.y() for p in points]
        # Margin must cover the pen width, selection bolding and the
        # arrowheads — a tight rect leaves paint residue on the canvas.
        margin = max(16.0, float(self.data.get("width", 2.0)) + 12.0)
        self.setRect(min(xs) - margin, min(ys) - margin,
                     max(xs) - min(xs) + 2 * margin,
                     max(ys) - min(ys) + 2 * margin)
        self.update()

    def shape(self):
        """A selectable stroked path, not the old whole bounding rectangle."""
        path = QPainterPath()
        if not self._points:
            return path
        path.moveTo(self._points[0])
        for point in self._points[1:]:
            path.lineTo(point)
        stroker = QPainterPathStroker()
        # Selection tolerance is a screen affordance. At Fit zoom an
        # eight-scene-unit stroke can collapse to three pixels and feel
        # impossible to pick, so preserve a twelve-pixel acquisition band.
        stroker.setWidth(max(self._endpoint_hit_radius(12.0) * 2.0,
                             float(self.data.get("width", 2.0)) + 6.0))
        result = stroker.createStroke(path)
        if self.isSelected():
            radius = self._endpoint_hit_radius()
            result.addEllipse(self._points[0], radius, radius)
            result.addEllipse(self._points[-1], radius, radius)
        return result

    def _endpoint_hit_radius(self, pixels: float = 7.0) -> float:
        studio = getattr(self.scene(), "studio", None) \
            if self.scene() else None
        scale = abs(studio.canvas.transform().m11()) \
            if studio is not None else 1.0
        return pixels / max(scale, 0.1)

    def endpoint_at(self, scene_pos) -> str:
        """Return ``a``/``b`` when a selected endpoint handle was grabbed."""
        if len(self._points) < 2:
            return ""
        radius = self._endpoint_hit_radius(10.0)
        for name, point in (("a", self._points[0]), ("b", self._points[-1])):
            if ((point.x() - scene_pos.x()) ** 2
                    + (point.y() - scene_pos.y()) ** 2 <= radius ** 2):
                return name
        return ""

    def segment_at(self, scene_pos):
        """A straight run can be grabbed at any zoom, in either direction."""
        best = min(((_point_segment_distance(scene_pos, a, b), index)
                    for index, (a, b) in enumerate(zip(self._points, self._points[1:]))
                    if (a - b).manhattanLength() > 1e-6), default=None)
        return best[1] if best and best[0] <= self._endpoint_hit_radius(10) else None

    def _arrow(self, painter, tip: QPointF, tail: QPointF,
               head: str = "") -> None:
        """One arrowhead, from the shared fourteen. Legacy
        `arrow_shape: open|filled` still selects the open or filled
        arrow so existing displays keep their look."""
        from azeo_control_trainer.core.hmi.pvms.strokes import draw_arrow_head
        if not head:
            head = "open_arrow" \
                if self.data.get("arrow_shape") == "open" \
                else "filled_arrow"
        painter.setRenderHint(QPainter.Antialiasing, True)
        draw_arrow_head(painter, head, tip, tail, self.line_colour(),
                        float(self.data.get("width", 2.0)),
                        self.data.get("arrow_size", "medium"))

    def paint(self, painter: QPainter, option, widget=None) -> None:
        if len(self._points) < 2:
            return
        from azeo_control_trainer.core.hmi.pvms.strokes import build_pen
        width = float(self.data.get("width", 2.0))
        route_blocked = self.route_status == "blocked"
        pen = build_pen(QColor(WF["crit"]) if route_blocked
                        else self.line_colour(),
                        width + (0.8 if self.isSelected() else 0.0),
                        "dash" if route_blocked
                        else self.data.get("style", "solid"),
                        self.data.get("cap", "round"))
        painter.setPen(pen)
        crossover = self.data.get("crossover", "")
        if crossover in ("gap", "jump"):
            draw_crossed_polyline(painter, self,
                                  list(self._points), crossover,
                                  width)
        else:
            painter.drawPath(rounded_polyline_path(
                self._points,
                float(self.data.get("corner_radius", 7.0) or 0.0)))
        # Independent start and end heads, from the fourteen.
        from azeo_control_trainer.core.hmi.pvms.strokes import resolve_arrows
        start_head, end_head = resolve_arrows(self.data)
        if end_head != "none":
            self._arrow(painter, self._points[-1], self._points[-2],
                        end_head)
        if start_head != "none":
            self._arrow(painter, self._points[0], self._points[1],
                        start_head)
        studio = getattr(self.scene(), "studio", None)
        if self.isSelected() and getattr(studio, "mode", None) == MODE_EDIT:
            # Endpoint handles are the familiar select-then-drag affordance.
            # They operate on the actual pipe ends, never on its bounding box.
            radius = self._endpoint_hit_radius(5.0)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(QPen(QColor(WF["cmd_teal"]), 1.4))
            painter.setBrush(QBrush(QColor("#FFFFFF")))
            painter.drawEllipse(self._points[0], radius, radius)
            painter.drawEllipse(self._points[-1], radius, radius)
        if self.isSelected() and getattr(studio, "mode", None) == MODE_EDIT \
                and self.data.get("route_mode") == "manual":
            painter.setPen(QPen(QColor(WF["lapis"]), 1.0))
            painter.setBrush(QBrush(QColor("white")))
            for raw in self.data.get("route_points", ()):
                point = QPointF(float(raw[0]), float(raw[1]))
                painter.drawRect(QRectF(point.x() - 4, point.y() - 4, 8, 8))

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """Double-click a segment to insert a persistent manual bend."""
        studio = getattr(self.scene(), "studio", None)
        if getattr(studio, "mode", None) != MODE_EDIT:
            return super().mouseDoubleClickEvent(event)
        point = event.scenePos()
        best = min(
            ((_point_segment_distance(point, a, b), index, a, b)
             for index, (a, b) in enumerate(zip(self._points,
                                                self._points[1:]))),
            default=None, key=lambda row: row[0])
        if best is None or best[0] > 10.0:
            return super().mouseDoubleClickEvent(event)
        _distance, index, a, b = best
        projected = QPointF(point.x(), a.y()) if abs(a.y() - b.y()) < 1e-6 \
            else QPointF(a.x(), point.y())
        studio.checkpoint()
        route_points = list(self.data.get("route_points", ()))
        insert_at = max(0, min(len(route_points), index - 1))
        route_points.insert(insert_at, [projected.x(), projected.y()])
        self.data["route_mode"] = "manual"
        self.data["route_points"] = route_points
        studio.reroute_pipes()
        studio.mark_unsaved()
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        studio = getattr(self.scene(), "studio", None)
        if event.button() != Qt.LeftButton or self.data.get("locked"):
            return super().mousePressEvent(event)
        if getattr(studio, "mode", None) == MODE_EDIT and self.isSelected():
            endpoint = self.endpoint_at(event.scenePos())
            if endpoint:
                studio.gesture_checkpoint()
                self._endpoint_drag = endpoint
                self._endpoint_before = copy.deepcopy(self.data)
                self._endpoint_points_before = [QPointF(point)
                                                for point in self._points]
                self._endpoint_target = None
                self._endpoint_moved = False
                event.accept()
                return
        if getattr(studio, "mode", None) == MODE_EDIT \
                and self.data.get("route_mode") == "manual":
            point = event.scenePos()
            for index, raw in enumerate(self.data.get("route_points", ())):
                if abs(point.x() - float(raw[0])) <= 7 \
                        and abs(point.y() - float(raw[1])) <= 7:
                    studio.checkpoint()
                    self._waypoint_drag = index
                    event.accept()
                    return
        if getattr(studio, "mode", None) == MODE_EDIT and not event.modifiers():
            index = self.segment_at(event.scenePos())
            if index is not None:
                group = item_group_id(self)
                members = [item for item in studio._groupable_items()
                           if group and item_group_id(item) == group] or [self]
                studio.selection.replace(members, primary=self)
                self._segment_drag = (index, QPointF(event.scenePos()),
                                      [QPointF(p) for p in self._points], False)
                event.accept()
                return
        super().mousePressEvent(event)
        if studio is not None and self.isSelected():
            group = item_group_id(self)
            if group and not (event.modifiers() & Qt.AltModifier):
                members = [item for item in studio._groupable_items()
                           if item_group_id(item) == group]
                studio.selection.extend(members, primary=self)
            else:
                studio.selection.set_primary(self)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._segment_drag is not None:
            index, press, original, started = self._segment_drag
            studio = self.scene().studio
            if not started and (event.scenePos() - press).manhattanLength() < self._endpoint_hit_radius(3):
                event.accept()
                return
            if not started:
                studio.gesture_checkpoint()
                self._segment_drag = (index, press, original, True)
            from .routing import Point, RouteResult, move_orthogonal_segment
            horizontal = abs(original[index].y() - original[index + 1].y()) < 1e-6
            point = studio.canvas.snap_route_point(event.scenePos(), source=self,
                                                    reference_points=(original[0], original[-1]))
            points = move_orthogonal_segment(
                tuple(Point(p.x(), p.y()) for p in original), index,
                point.y() if horizontal else point.x(),
                max(8.0, float(self.data.get("clearance", 12))))
            self.data["route_mode"] = "manual"
            self.data["route_points"] = [p.to_list() for p in points[1:-1]]
            self.apply_route(RouteResult(points), a_side=self.data.get("a_side", ""),
                             b_side=self.data.get("b_side", ""))
            studio.touch_geometry()
            event.accept()
            return
        if self._endpoint_drag:
            studio = getattr(self.scene(), "studio", None)
            self._endpoint_moved = True
            raw_point = event.scenePos()
            target = studio._connect_target_at(raw_point) \
                if studio is not None else None
            if target is not None and self._points:
                from .pipe_scene import junction_side
                opposite = self._points[-1] if self._endpoint_drag == "a" else self._points[0]
                target = (target[0], junction_side(target[0], target[1], opposite))
            if studio is not None:
                studio._set_connect_target(target)
            self._endpoint_target = target
            references = []
            if self._points:
                references.append(self._points[-1]
                                  if self._endpoint_drag == "a"
                                  else self._points[0])
            references.extend(
                QPointF(float(raw[0]), float(raw[1]))
                for raw in self.data.get("route_points", ()))
            if target is not None and studio is not None:
                end = self._endpoint_drag
                self.data[end] = studio._endpoint_id(target[0])
                self.data[f"{end}_side"] = target[1]
                self.data.pop(f"{end}_point", None)
                self.data["auto"] = False
                # An attached port remains exact; this call only exposes the
                # horizontal/vertical relationship as temporary guides.
                anchor = target[0].anchor(target[1])
                studio.canvas.snap_route_point(
                    anchor, source=self,
                    reference_points=(*references, anchor))
                studio.reroute_pipes()
            else:
                # A loose endpoint is real authored geometry, not a preview
                # that springs back on release.  This is what lets a user
                # pull a line past a valve, then grab that same endpoint and
                # glue it accurately on a second gesture.
                if self._endpoint_before is not None:
                    self.data.clear()
                    self.data.update(copy.deepcopy(self._endpoint_before))
                end = self._endpoint_drag
                self.data[end] = ""
                self.data.pop(f"{end}_instance_id", None)
                self.data.pop(f"{end}_source_element_id", None)
                point = studio.canvas.snap_route_point(
                    raw_point, source=self, reference_points=references) \
                    if studio is not None else raw_point
                self.data[f"{end}_point"] = [
                    point.x(), point.y()]
                self.data["auto"] = False
                if studio is not None:
                    studio.reroute_pipes()
            event.accept()
            return
        if self._waypoint_drag >= 0:
            points = list(self.data.get("route_points", ()))
            studio = self.scene().studio
            references = [self._points[0], self._points[-1]] \
                if len(self._points) >= 2 else []
            references.extend(
                QPointF(float(raw[0]), float(raw[1]))
                for index, raw in enumerate(points)
                if index != self._waypoint_drag)
            point = studio.canvas.snap_route_point(
                event.scenePos(), source=self,
                reference_points=references)
            points[self._waypoint_drag] = [point.x(), point.y()]
            self.data["route_points"] = points
            studio.reroute_pipes()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._segment_drag is not None:
            _index, _press, _original, started = self._segment_drag
            self._segment_drag = None
            studio = self.scene().studio
            studio.canvas.clear_smart_guides()
            if started:
                studio.reroute_pipes(only_ids={self.data["id"]})
                studio.mark_unsaved()
                studio.end_gesture()
            event.accept()
            return
        if self._endpoint_drag:
            studio = getattr(self.scene(), "studio", None)
            before = self._endpoint_before
            if studio is not None:
                studio._set_connect_target(None)
                studio.canvas.clear_smart_guides()
                studio.reroute_pipes()
                if self._endpoint_moved and before is not None \
                        and self.data != before:
                    studio.mark_unsaved()
                studio.end_gesture()
            self._endpoint_drag = ""
            self._endpoint_before = None
            self._endpoint_points_before = None
            self._endpoint_target = None
            self._endpoint_moved = False
            event.accept()
            return
        if self._waypoint_drag >= 0:
            self._waypoint_drag = -1
            studio = self.scene().studio
            studio.canvas.clear_smart_guides()
            studio.mark_unsaved()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def hoverMoveEvent(self, event) -> None:  # noqa: N802
        studio = getattr(self.scene(), "studio", None)
        if self.isSelected() and getattr(studio, "mode", None) == MODE_EDIT \
                and self.endpoint_at(event.scenePos()):
            self.setCursor(Qt.CrossCursor)
        elif getattr(studio, "mode", None) == MODE_EDIT and not self.data.get("locked") \
                and (index := self.segment_at(event.scenePos())) is not None:
            a, b = self._points[index:index + 2]
            self.setCursor(Qt.SizeVerCursor if abs(a.y() - b.y()) < 1e-6 else Qt.SizeHorCursor)
        else:
            self.unsetCursor()
        super().hoverMoveEvent(event)

    def contextMenuEvent(self, event) -> None:      # noqa: N802
        studio = getattr(self.scene(), "studio", None)
        if studio is not None:
            self.setSelected(True)
            studio.drawing_context_menu(self, event.screenPos())


def _point_segment_distance(point, start, end) -> float:
    dx, dy = end.x() - start.x(), end.y() - start.y()
    length_squared = dx * dx + dy * dy
    t = max(0.0, min(1.0, ((point.x() - start.x()) * dx
                           + (point.y() - start.y()) * dy) / length_squared)) if length_squared else 0
    return ((start.x() + t * dx - point.x()) ** 2
            + (start.y() + t * dy - point.y()) ** 2) ** .5
