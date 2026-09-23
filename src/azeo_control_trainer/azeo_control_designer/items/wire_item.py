"""Wire graphics item — Manhattan orthogonal routing, ISA-101 Silver style.

Wires connect output terminals to input terminals with horizontal/vertical
segments only (industrial DCS style). Colored by source pin data type.
BKCAL (feedback) wires use dashed amber lines.

Connections are first-class objects on the canvas, matching the Azeo
reference design:

* they are selectable, report their own properties (direction, routing
  style, feedback flag, numeric route handles) and can be deleted with the
  Delete key;
* the route is orthogonal and every *interior* segment carries a draggable
  handle, so the engineer can push a vertical segment left/right or a
  horizontal segment up/down instead of accepting whatever the auto-router
  produced;
* "Auto Route" throws the manual route away and returns to the computed one;
* feedback (BKCAL) wires route back *beneath* the two blocks they join with a
  tight clearance instead of a full-width detour over the top of the diagram
  (which read like a selection marquee).

Route persistence
-----------------
A manual route is kept on the :class:`WireItem` and mirrored onto the
:class:`~azeo_control_trainer.core.strategy.model.wire.Wire` as ``wire.route_points`` (a list
of ``[x, y]`` pairs).  ``Wire.to_dict()`` / ``Wire.from_dict()`` currently
serialise a **fixed** key set and drop unknown keys, so the route does not yet
survive a save/load round trip — that needs a one-line addition to the model
(``wire.py``), which this module deliberately does not make.  As soon as the
model carries the key through, :meth:`WireItem.__init__` picks it up again
with no further change here.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QPainter, QPainterPath, QPainterPathStroker, QPen,
)
from PySide6.QtWidgets import QGraphicsPathItem, QGraphicsItem, QStyle

from azeo_control_trainer.core.strategy.model.wire import Wire
from azeo_control_trainer.core.strategy.model.terminal import DataType, Quality

# Wire colors by data type (ISA-101 Silver)
_TYPE_COLORS = {
    DataType.FLOAT:  QColor("#2868A8"),   # blue — analog
    DataType.BOOL:   QColor("#C89820"),   # gold — discrete
    DataType.INT:    QColor("#28884C"),   # green — integer
    DataType.STRING: QColor("#7A6AAA"),   # purple
    DataType.ENUM:   QColor("#7A6AAA"),   # purple
}

_WIRE_BKCAL = QColor("#f39c12")
_SELECT_HALO = QColor(25, 118, 210, 90)    # ISA-101 selection accent
_QUALITY_COLORS = {
    Quality.UNCERTAIN: QColor("#D88700"),
    Quality.BAD: QColor("#C62828"),
}

# Routing constants
MIN_SEGMENT = 20
GRID_SIZE = 10
WIRE_WIDTH = 2
WIRE_WIDTH_SEL = 3

# Feedback (BKCAL) wires hug the underside of the blocks they join.
FEEDBACK_CLEARANCE = 18
FEEDBACK_LANE = 6          # per-wire stagger so parallel returns don't overlap
FEEDBACK_LANES = 3

# Route handles (drawn at the midpoint of every interior segment)
HANDLE_SIZE = 7.0
HANDLE_GRAB = 9.0

# Click tolerance — the 2 px pen stroke alone is nearly impossible to hit.
HIT_WIDTH = 10.0

# Attribute used to mirror a manual route onto the Wire model object.
ROUTE_ATTR = "route_points"


def _snap(v: float) -> float:
    return round(v / GRID_SIZE) * GRID_SIZE


def _as_point(p) -> QPointF:
    """Accept QPointF, (x, y) tuple or [x, y] list."""
    if isinstance(p, QPointF):
        return QPointF(p)
    return QPointF(float(p[0]), float(p[1]))


class WireItem(QGraphicsPathItem):
    """Orthogonal (Manhattan-routed) wire connecting two terminal items."""

    def __init__(self, wire: Wire, src_terminal_item, dst_terminal_item, parent=None):
        super().__init__(parent)
        self.wire = wire
        self.src_terminal = src_terminal_item
        self.dst_terminal = dst_terminal_item

        # Route corner points (between start and end)
        self._route_points: list[QPointF] = []
        # Operator-pinned route; None means "let the auto-router decide"
        self._manual_route: list[QPointF] | None = None
        self._drag_handle: dict | None = None

        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(1)

        self._hovered = False
        self._show_value = False   # True when strategy is online
        self._value_text = ""      # formatted value string
        self._live_active = False
        self._live_quality = Quality.GOOD

        # Determine wire color from source data type
        src_type = getattr(src_terminal_item.terminal, 'data_type', DataType.FLOAT)
        if self.is_feedback:
            self._wire_color = _WIRE_BKCAL
        else:
            self._wire_color = _TYPE_COLORS.get(src_type, QColor("#2868A8"))

        # Initial route — a route stored on the model wins over the computed one
        stored = getattr(wire, ROUTE_ATTR, None)
        if not (stored and self.set_manual_route(stored)):
            self._compute_auto_route()
            self._update_path()
        self._update_pen()

        self.setToolTip(self._build_tooltip())

    # ---------------------------------------------------------------- identity

    @property
    def is_feedback(self) -> bool:
        """True for BKCAL / next-scan feedback connections.

        Derived, not merely stored: shipped module files predate the
        `is_bkcal` flag, so every BKCAL wire in them carries `False` and
        drew as an ordinary signal — solid blue, indistinguishable from
        forward flow (user screenshot). The wire *between two BKCAL
        terminals* is feedback whatever the file says, and this is UI
        classification only — the model flag and the files stay untouched
        (shipped modules are byte-identical, and the compiler has its own
        exemption rule).
        """
        wire = self.wire
        if bool(wire.is_bkcal):
            return True
        if getattr(self.src_terminal.terminal, "is_bkcal", False) \
                and getattr(self.dst_terminal.terminal, "is_bkcal", False):
            return True
        return ("BKCAL" in (wire.src_terminal or "")
                and "BKCAL" in (wire.dst_terminal or ""))

    @property
    def route_style(self) -> str:
        """``"manual"`` when the operator has pinned segments, else ``"auto"``."""
        return "manual" if self._manual_route else "auto"

    def _block_name(self, terminal_item) -> str:
        parent = getattr(terminal_item, "parent_block", None)
        block = getattr(parent, "block", None)
        return getattr(block, "instance_name", "") or "?"

    def endpoints(self) -> tuple[str, str]:
        """``("SRC.OUT", "DST.IN")`` — the connection's direction."""
        src = f"{self._block_name(self.src_terminal)}.{self.wire.src_terminal}"
        dst = f"{self._block_name(self.dst_terminal)}.{self.wire.dst_terminal}"
        return src, dst

    def properties(self) -> dict:
        """Everything a properties view needs to describe this connection."""
        src, dst = self.endpoints()
        return {
            "id": self.wire.id,
            "source": src,
            "destination": dst,
            "label": f"{src} → {dst}",
            "src_block": self._block_name(self.src_terminal),
            "src_terminal": self.wire.src_terminal,
            "dst_block": self._block_name(self.dst_terminal),
            "dst_terminal": self.wire.dst_terminal,
            "data_type": getattr(
                getattr(self.src_terminal, "terminal", None),
                "data_type", DataType.FLOAT),
            "feedback": self.is_feedback,
            "routing": "orthogonal",
            "route_style": self.route_style,
            "handles": [
                {"axis": h["axis"], "value": round(h["value"], 1)}
                for h in self.route_handles()
            ],
        }

    def _build_tooltip(self) -> str:
        src, dst = self.endpoints()
        tip = f"{src} → {dst}"
        if self.is_feedback:
            tip += " [BKCAL]"
        tip += f"\nRouting: orthogonal ({self.route_style})"
        handles = self.route_handles()
        if handles:
            tip += "\nHandles: " + ", ".join(
                f"{h['axis']}={h['value']:.0f}" for h in handles)
        return tip

    # ---------------------------------------------------------------- routing

    def _compute_auto_route(self):
        """Compute orthogonal route between source and destination pins."""
        start = self.src_terminal.get_scene_center()
        end = self.dst_terminal.get_scene_center()
        dx = end.x() - start.x()

        if dx >= MIN_SEGMENT * 2:
            # Simple case — destination is well to the right
            mid_x = _snap(start.x() + dx / 2)
            self._route_points = [
                QPointF(mid_x, start.y()),
                QPointF(mid_x, end.y()),
            ]
        elif dx >= 0:
            # Close — L-route
            off_x = _snap(start.x() + MIN_SEGMENT)
            self._route_points = [
                QPointF(off_x, start.y()),
                QPointF(off_x, end.y()),
            ]
        elif self.is_feedback:
            # Feedback — return tightly beneath the two blocks
            self._route_points = self._feedback_route(start, end)
        else:
            # Destination to the left — route around blocks
            off_right = _snap(start.x() + MIN_SEGMENT)
            off_left = _snap(end.x() - MIN_SEGMENT)

            # Decide whether to go above or below
            src_rect = self._block_rect(self.src_terminal)
            dst_rect = self._block_rect(self.dst_terminal)
            if src_rect is not None and dst_rect is not None:
                min_y = min(src_rect.top(), dst_rect.top()) - 30
                max_y = max(src_rect.bottom(), dst_rect.bottom()) + 30
                if abs(start.y() - min_y) < abs(start.y() - max_y):
                    route_y = _snap(min_y)
                else:
                    route_y = _snap(max_y)
            else:
                route_y = _snap(min(start.y(), end.y()) - 40)

            self._route_points = [
                QPointF(off_right, start.y()),
                QPointF(off_right, route_y),
                QPointF(off_left, route_y),
                QPointF(off_left, end.y()),
            ]

    def _block_rect(self, terminal_item) -> QRectF | None:
        block_item = getattr(terminal_item, "parent_block", None)
        if block_item is None:
            return None
        try:
            return block_item.sceneBoundingRect()
        except (AttributeError, RuntimeError):
            return None

    def _feedback_lane(self) -> float:
        """Deterministic per-wire lane offset so parallel returns don't merge."""
        wid = str(getattr(self.wire, "id", "") or "")
        return (sum(ord(c) for c in wid) % FEEDBACK_LANES) * FEEDBACK_LANE

    def _feedback_route(self, start: QPointF, end: QPointF) -> list[QPointF]:
        """Route a BKCAL wire back under the blocks it joins.

        The old behaviour picked whichever of "above both blocks" / "below both
        blocks" was nearer, and the *above* branch drew a tall rectangle over
        the whole diagram that reads like a rubber-band selection.  Feedback
        always drops below now, hugging the deeper of the two blocks with a
        small clearance plus a per-wire lane offset.
        """
        off_right = _snap(start.x() + MIN_SEGMENT)
        off_left = _snap(end.x() - MIN_SEGMENT)

        bottoms = [r.bottom() for r in (self._block_rect(self.src_terminal),
                                        self._block_rect(self.dst_terminal))
                   if r is not None]
        base = max(bottoms) if bottoms else max(start.y(), end.y())
        route_y = _snap(base + FEEDBACK_CLEARANCE + self._feedback_lane())
        # Never cut back up through the pins themselves
        floor_y = _snap(max(start.y(), end.y()) + FEEDBACK_CLEARANCE)
        route_y = max(route_y, floor_y)

        return [
            QPointF(off_right, start.y()),
            QPointF(off_right, route_y),
            QPointF(off_left, route_y),
            QPointF(off_left, end.y()),
        ]

    # ------------------------------------------------------------ manual route

    def set_manual_route(self, points) -> bool:
        """Pin an explicit orthogonal route.  Returns False if unusable.

        ``points`` are the *interior* corner points (between the two pins), as
        QPointF / (x, y) pairs.  The list must have an even length so the run
        alternates horizontal → vertical → … → horizontal and therefore meets
        both pins side-on.
        """
        try:
            pts = [_as_point(p) for p in points]
        except (TypeError, ValueError, IndexError):
            return False
        if len(pts) < 2 or len(pts) % 2:
            return False

        self._manual_route = pts
        self._normalize_manual_route()
        self._constrain_route_to_scene()
        self._route_points = [QPointF(p) for p in self._manual_route]
        self._update_path()
        self._store_route_on_wire()
        self.setToolTip(self._build_tooltip())
        self.update()
        return True

    def clear_manual_route(self):
        """Discard the pinned route and fall back to the auto-router."""
        self._manual_route = None
        self._store_route_on_wire()
        self._compute_auto_route()
        self._update_path()
        self.setToolTip(self._build_tooltip())
        self.update()

    def manual_route_data(self) -> list[list[float]] | None:
        """The pinned route as plain ``[[x, y], ...]`` (None when automatic)."""
        if not self._manual_route:
            return None
        return [[round(p.x(), 2), round(p.y(), 2)] for p in self._manual_route]

    def route_data(self) -> list[list[float]]:
        """The route actually in use (computed or pinned) as ``[[x, y], ...]``."""
        return [[round(p.x(), 2), round(p.y(), 2)] for p in self._route_points]

    def apply_route_data(self, data) -> bool:
        """Restore a route previously produced by :meth:`manual_route_data`."""
        if not data:
            self.clear_manual_route()
            return True
        return self.set_manual_route(data)

    def _store_route_on_wire(self):
        """Mirror the pinned route onto the Wire model object.

        ``Wire.to_dict()`` does not (yet) carry this key, so it is lost on
        save — see the module docstring.  Writing it anyway means the route
        starts persisting the moment the model gains the field.
        """
        try:
            setattr(self.wire, ROUTE_ATTR, self.manual_route_data())
        except (AttributeError, TypeError):
            pass

    def _normalize_manual_route(self):
        """Force the pinned route back to a legal orthogonal run.

        Blocks move after a route is pinned, so the first corner is re-anchored
        to the source pin's y, the last to the destination pin's y, and every
        intermediate corner is snapped onto its neighbour's axis.
        """
        pts = self._manual_route
        if not pts:
            return
        start = self.src_terminal.get_scene_center()
        end = self.dst_terminal.get_scene_center()

        pts[0] = QPointF(pts[0].x(), start.y())
        for i in range(1, len(pts)):
            if i % 2:                       # vertical segment -> share x
                pts[i] = QPointF(pts[i - 1].x(), pts[i].y())
            else:                           # horizontal segment -> share y
                pts[i] = QPointF(pts[i].x(), pts[i - 1].y())
        pts[-1] = QPointF(pts[-1].x(), end.y())

    def _constrain_route_to_scene(self):
        """Keep explicit and computed corners on the finite module sheet."""
        scene = self.scene()
        constrain = getattr(scene, "constrain_scene_point", None)
        if not callable(constrain):
            return
        if self._manual_route is not None:
            self._manual_route = [constrain(p) for p in self._manual_route]
        self._route_points = [constrain(p) for p in self._route_points]

    def _materialize_route(self):
        """Turn the current computed route into a pinned one (first drag)."""
        if self._manual_route is None:
            self._manual_route = [QPointF(p) for p in self._route_points]
            self._store_route_on_wire()

    # ------------------------------------------------------------ route handles

    def route_handles(self) -> list[dict]:
        """Draggable handles — one per *interior* orthogonal segment.

        Each entry is ``{"segment": k, "axis": "x"|"y", "value": float,
        "mid": QPointF}``.  ``axis`` is the coordinate the handle moves:
        vertical segments slide in x, horizontal segments slide in y.  The
        first and last segments are anchored to the pins and are not offered.
        """
        pts = self._path_points()
        n_route = len(self._route_points)
        handles: list[dict] = []
        for k in range(len(pts) - 1):
            a, b = pts[k], pts[k + 1]
            interior = 1 <= k <= n_route - 1
            if not interior:
                continue
            if abs(a.x() - b.x()) < 0.5 and abs(a.y() - b.y()) < 0.5:
                continue                      # degenerate
            if abs(a.x() - b.x()) < 0.5:      # vertical
                handles.append({
                    "segment": k, "axis": "x", "value": a.x(),
                    "mid": QPointF(a.x(), (a.y() + b.y()) / 2.0),
                })
            elif abs(a.y() - b.y()) < 0.5:    # horizontal
                handles.append({
                    "segment": k, "axis": "y", "value": a.y(),
                    "mid": QPointF((a.x() + b.x()) / 2.0, a.y()),
                })
        return handles

    def handle_at(self, pos: QPointF) -> dict | None:
        """Handle under ``pos`` (item == scene coordinates), or None."""
        for h in self.route_handles():
            mid = h["mid"]
            if (abs(mid.x() - pos.x()) <= HANDLE_GRAB
                    and abs(mid.y() - pos.y()) <= HANDLE_GRAB):
                return h
        return None

    def move_handle(self, handle: dict, pos: QPointF) -> bool:
        """Move one segment handle to ``pos`` (snapped to the routing grid).

        Purely visual: the graph is untouched, so this stays legal while the
        module is on scan.
        """
        if not handle:
            return False
        scene = self.scene()
        constrain = getattr(scene, "constrain_scene_point", None)
        if callable(constrain):
            pos = constrain(pos)
        self._materialize_route()
        seg = int(handle["segment"])
        i, j = seg - 1, seg               # indices into the interior route
        if not (0 <= i < len(self._manual_route)
                and 0 <= j < len(self._manual_route)):
            return False

        if handle["axis"] == "x":
            v = _snap(pos.x())
            self._manual_route[i] = QPointF(v, self._manual_route[i].y())
            self._manual_route[j] = QPointF(v, self._manual_route[j].y())
        else:
            v = _snap(pos.y())
            self._manual_route[i] = QPointF(self._manual_route[i].x(), v)
            self._manual_route[j] = QPointF(self._manual_route[j].x(), v)

        self._normalize_manual_route()
        self._constrain_route_to_scene()
        self._route_points = [QPointF(p) for p in self._manual_route]
        self._update_path()
        self._store_route_on_wire()
        self.setToolTip(self._build_tooltip())
        self.update()
        return True

    # ---------------------------------------------------------------- geometry

    def _path_points(self) -> list[QPointF]:
        start = self.src_terminal.get_scene_center()
        end = self.dst_terminal.get_scene_center()
        return [start] + list(self._route_points) + [end]

    def _update_path(self):
        """Build QPainterPath from route points."""
        points = self._path_points()

        path = QPainterPath()
        if points:
            path.moveTo(points[0])
            for pt in points[1:]:
                path.lineTo(pt)
        self.setPath(path)

    def update_position(self):
        """Recalculate route when blocks move (a pinned route is kept)."""
        if self._manual_route:
            self._normalize_manual_route()
            self._route_points = [QPointF(p) for p in self._manual_route]
            # Re-anchoring changed the stored corners — keep the mirror true
        else:
            self._compute_auto_route()
        self._constrain_route_to_scene()
        if self._manual_route:
            self._store_route_on_wire()
        self._update_path()

    def set_show_value(self, show: bool):
        """Enable/disable live value display on the wire."""
        if show != self._show_value:
            self._show_value = show
            if not show:
                self._value_text = ""
            self.update()

    def update_live_value(self):
        """Read the source terminal's current value and format it for display."""
        if not self._show_value:
            return
        terminal = self.src_terminal.terminal
        val = terminal.value
        if isinstance(val, bool):
            text = "T" if val else "F"
        elif isinstance(val, float):
            if abs(val) < 0.01 and val != 0.0:
                text = f"{val:.3e}"
            elif abs(val) >= 10000:
                text = f"{val:.0f}"
            elif abs(val) >= 100:
                text = f"{val:.1f}"
            else:
                text = f"{val:.2f}"
        elif isinstance(val, int):
            text = str(val)
        else:
            text = str(val)[:8]
        active = self._value_is_active(val)
        quality = getattr(terminal, "status", Quality.GOOD)
        if (text != self._value_text or active != self._live_active
                or quality != self._live_quality):
            self._value_text = text
            self._live_active = active
            self._live_quality = quality
            self.update()

    @staticmethod
    def _value_is_active(value) -> bool:
        """Whether a live signal should receive the heavier active stroke."""
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return abs(float(value)) > 1e-12
        return bool(value)

    def _live_color(self) -> QColor:
        if self._show_value:
            return _QUALITY_COLORS.get(self._live_quality, self._wire_color)
        return self._wire_color

    def boundingRect(self) -> QRectF:
        """Expanded bounding rect to include handles and value labels."""
        br = super().boundingRect()
        if self._show_value and self._value_text:
            # Add margin for the value label drawn above the wire
            return br.adjusted(-30, -20, 30, 5)
        m = HANDLE_SIZE + 2
        return br.adjusted(-m, -m, m, m)

    def shape(self) -> QPainterPath:
        """Fat hit area — a 2 px stroke is not a clickable target."""
        stroker = QPainterPathStroker()
        stroker.setWidth(HIT_WIDTH)
        path = stroker.createStroke(self.path())
        if self.isSelected():
            for h in self.route_handles():
                mid = h["mid"]
                path.addRect(QRectF(mid.x() - HANDLE_GRAB, mid.y() - HANDLE_GRAB,
                                    HANDLE_GRAB * 2, HANDLE_GRAB * 2))
        return path

    # ---------------------------------------------------------------- appearance

    def _update_pen(self):
        if self.isSelected():
            color = self._live_color()
            width = WIRE_WIDTH_SEL
        elif self._hovered:
            color = self._live_color().lighter(140)
            width = WIRE_WIDTH + 1
        else:
            color = self._live_color()
            width = WIRE_WIDTH + int(self._show_value and self._live_active)

        pen = QPen(color, width, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin)
        if self.is_feedback:
            pen.setDashPattern([6, 3])
        self.setPen(pen)

    def paint(self, painter: QPainter, option, widget=None):
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        width = (WIRE_WIDTH_SEL if is_selected else
                 WIRE_WIDTH + int(self._show_value and self._live_active))
        color = self._live_color()

        # Selection casing drawn *under* the wire so the type/BKCAL colour
        # stays true (dashed amber must remain recognisably dashed amber).
        if is_selected:
            painter.setPen(QPen(_SELECT_HALO, width + 6,
                                Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(self.path())

        pen = QPen(color, width, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin)
        # `is_feedback`, never the raw stored flag: shipped files carry
        # False on every BKCAL wire, and paint() rebuilding a solid pen
        # each frame was exactly where the derived dash kept dying.
        if self.is_feedback:
            pen.setDashPattern([6, 3])

        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(self.path())

        # Draggable segment handles + endpoint markers while selected
        if is_selected:
            self._paint_handles(painter)

        # Live value label at wire midpoint
        if self._show_value and self._value_text:
            path = self.path()
            mid_pt = path.pointAtPercent(0.5)

            font = QFont("Consolas", 7)
            font.setBold(True)
            painter.setFont(font)
            fm = painter.fontMetrics()
            tw = fm.horizontalAdvance(self._value_text) + 6
            th = fm.height() + 2

            label_rect = QRectF(mid_pt.x() - tw / 2,
                                mid_pt.y() - th - 2,
                                tw, th)

            # Background pill
            painter.setPen(Qt.NoPen)
            label_bg = {
                Quality.BAD: QColor(116, 22, 22, 230),
                Quality.UNCERTAIN: QColor(112, 72, 0, 230),
            }.get(self._live_quality, QColor(30, 30, 40, 210))
            painter.setBrush(QBrush(label_bg))
            painter.drawRoundedRect(label_rect, 3, 3)

            # Text
            painter.setPen(QPen(QColor("#E0E8F0"), 1))
            painter.drawText(label_rect, Qt.AlignCenter, self._value_text)

    def _paint_handles(self, painter: QPainter):
        pinned = self._manual_route is not None
        fill = QColor("#FFFFFF") if not pinned else QColor("#1976D2")
        edge = QColor("#1976D2") if not pinned else QColor("#FFFFFF")
        h = HANDLE_SIZE / 2.0
        painter.setPen(QPen(edge, 1.5))
        painter.setBrush(QBrush(fill))
        for handle in self.route_handles():
            mid = handle["mid"]
            painter.drawRect(QRectF(mid.x() - h, mid.y() - h,
                                    HANDLE_SIZE, HANDLE_SIZE))

        # Endpoint markers — make the direction unambiguous
        pts = self._path_points()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(self._wire_color))
        painter.drawEllipse(pts[0], 3.0, 3.0)
        painter.setBrush(QBrush(QColor("#1976D2")))
        painter.drawEllipse(pts[-1], 3.0, 3.0)

    # ---------------------------------------------------------------- interaction

    def hoverEnterEvent(self, event):
        self._hovered = True
        self._update_pen()
        super().hoverEnterEvent(event)

    def hoverMoveEvent(self, event):
        if self.isSelected():
            handle = self.handle_at(event.pos())
            if handle is not None:
                self.setCursor(Qt.SizeHorCursor if handle["axis"] == "x"
                               else Qt.SizeVerCursor)
            else:
                self.unsetCursor()
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event):
        self._hovered = False
        self.unsetCursor()
        self._update_pen()
        super().hoverLeaveEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemSelectedHasChanged:
            self._update_pen()
            self.prepareGeometryChange()
        return super().itemChange(change, value)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            handle = self.handle_at(event.pos())
            if handle is not None:
                self.setSelected(True)
                self._materialize_route()
                # Re-resolve after materialising (indices are stable, but the
                # handle dict carries a stale midpoint otherwise).
                self._drag_handle = self.handle_at(event.pos()) or handle
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_handle is not None:
            self.move_handle(self._drag_handle, event.pos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_handle is not None:
            self._drag_handle = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        from azeo_control_trainer.core.presentation.menu_style import studio_menu

        menu = studio_menu(
            "CONNECTION",
            f"{self.wire.src_terminal} → {self.wire.dst_terminal}"
            + ("  [BKCAL]" if self.is_feedback else ""))

        self.setSelected(True)

        # Connection properties (read-only header)
        props = self.properties()
        head = menu.addAction(props["label"])
        head.setEnabled(False)
        kind = menu.addAction(
            "Feedback / next scan" if props["feedback"] else "Signal flow")
        kind.setEnabled(False)
        style = menu.addAction(
            f"Routing: orthogonal ({props['route_style']})")
        style.setEnabled(False)
        for h in props["handles"]:
            act = menu.addAction(f"    handle  {h['axis']} = {h['value']:.0f}")
            act.setEnabled(False)
        menu.addSeparator()

        # Auto-route — discard the pinned waypoints
        act_route = menu.addAction("Auto Route")
        act_route.setEnabled(self._manual_route is not None)
        act_route.triggered.connect(self.auto_route)
        menu.addSeparator()

        # Structural refactoring keeps connection edits discoverable on the
        # object being changed, rather than requiring delete-and-redraw.
        scene = self.scene()
        refactor = menu.addMenu("Refactor Connection")
        insert = refactor.addAction("Insert Block into Wire…")
        reconnect_source = refactor.addAction("Reconnect Source…")
        reconnect_destination = refactor.addAction("Reconnect Destination…")
        branch = refactor.addAction("Start Branch from Source")
        locked = bool(scene and getattr(scene, "structure_locked", False))
        for action in (insert, reconnect_source, reconnect_destination, branch):
            action.setEnabled(not locked)
        insert.triggered.connect(lambda: scene and scene.open_wire_insert_dialog(
            self.wire.id, event.scenePos(), parent=scene.views()[0]
            if scene.views() else None))
        reconnect_source.triggered.connect(
            lambda: scene and scene.open_reconnect_dialog(
                self.wire.id, "source", parent=scene.views()[0]
                if scene.views() else None))
        reconnect_destination.triggered.connect(
            lambda: scene and scene.open_reconnect_dialog(
                self.wire.id, "destination", parent=scene.views()[0]
                if scene.views() else None))
        branch.triggered.connect(lambda: scene and scene.start_wiring(self.src_terminal))
        menu.addSeparator()

        # Delete
        act_del = menu.addAction("Delete Wire")
        act_del.triggered.connect(self._request_delete)

        if not self.wire.is_bkcal:
            act_bkcal = menu.addAction("Mark as BKCAL")
            act_bkcal.triggered.connect(self._toggle_bkcal)
        else:
            act_bkcal = menu.addAction("Unmark BKCAL")
            act_bkcal.triggered.connect(self._toggle_bkcal)

        menu.exec_transient(event.screenPos())

    def auto_route(self):
        """Discard any pinned route and return to computed routing."""
        self.clear_manual_route()

    # Backwards-compatible alias (older callers / menus)
    _auto_route = auto_route

    def _request_delete(self):
        scene = self.scene()
        if scene and hasattr(scene, 'delete_wire'):
            scene.delete_wire(self.wire.id)

    def _toggle_bkcal(self):
        """Flip the BKCAL flag through the scene so it is undoable.

        Mutating ``wire.is_bkcal`` directly changed what the compiler treats as
        a feedback edge with no undo entry and no dirty flag, and bypassed the
        on-scan structural lock.
        """
        scene = self.scene()
        setter = getattr(scene, "set_wire_bkcal", None) if scene else None
        if callable(setter):
            setter(self.wire.id, not self.wire.is_bkcal)
            return
        # No scene (detached item / older scene): fall back to a direct flip.
        self.wire.is_bkcal = not self.wire.is_bkcal
        self.refresh_bkcal_style()

    def refresh_bkcal_style(self):
        """Repaint after ``wire.is_bkcal`` changed elsewhere (undo/redo).

        Must never call back into :meth:`_toggle_bkcal` — the scene calls this
        *after* it has already flipped the flag.
        """
        if self.is_feedback:
            self._wire_color = _WIRE_BKCAL
        else:
            src_type = getattr(self.src_terminal.terminal, 'data_type', DataType.FLOAT)
            self._wire_color = _TYPE_COLORS.get(src_type, QColor("#2868A8"))
        self._update_pen()
        self.update_position()
        self.setToolTip(self._build_tooltip())
        self.update()


class TempWireItem(QGraphicsPathItem):
    """Temporary orthogonal wire shown during connection drag."""

    def __init__(self, start_pin, parent=None):
        super().__init__(parent)
        self._start_pin = start_pin
        self._end_point = start_pin.get_scene_center()

        src_type = getattr(start_pin.terminal, 'data_type', DataType.FLOAT)
        color = _TYPE_COLORS.get(src_type, QColor("#2868A8"))
        self.setPen(QPen(color, WIRE_WIDTH, Qt.DashLine, Qt.SquareCap))
        self.setZValue(20)
        self._update_path()

    @property
    def start_pin(self):
        return self._start_pin

    def set_end_point(self, point: QPointF):
        self._end_point = point
        self._update_path()

    def _update_path(self):
        start = self._start_pin.get_scene_center()
        end = self._end_point

        path = QPainterPath()
        path.moveTo(start)

        dx = end.x() - start.x()
        is_output = (self._start_pin.terminal.direction.value == "OUT")

        if is_output:
            mid_x = start.x() + max(dx / 2, MIN_SEGMENT) if dx >= MIN_SEGMENT else start.x() + MIN_SEGMENT
        else:
            mid_x = start.x() + min(dx / 2, -MIN_SEGMENT) if dx <= -MIN_SEGMENT else start.x() - MIN_SEGMENT

        path.lineTo(QPointF(mid_x, start.y()))
        path.lineTo(QPointF(mid_x, end.y()))
        path.lineTo(end)
        self.setPath(path)
