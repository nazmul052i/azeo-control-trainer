"""Function block graphics item — ISA-101 / Azeo-inspired style.

Each block is drawn as a rounded rectangle with:
- Colored header bar (metallic gradient per category)
- Silver body with subtle gradient
- Input terminals on the left, output terminals on the right
- Instance name and type in the header, plus body pin labels and status dot
- **Live value overlays** (PV/SP/OUT/mode badge when online)
- **Execution order badge** (after compilation)
- **Status border accents** (green/red/yellow stripe)
- Resize handles on right and bottom edges
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QCursor, QFont, QFontMetrics, QLinearGradient,
    QPainter, QPen,
)
from PySide6.QtWidgets import (
    QGraphicsItem, QGraphicsObject, QMenu,
    QGraphicsDropShadowEffect, QStyle,
    QGraphicsSceneMouseEvent,
)

from azeo_control_trainer.core.strategy.model.block_base import (
    FunctionBlock, BlockStatus,
)
from azeo_control_trainer.core.presentation.function_block_style import (
    FUNCTION_BLOCK_BODY_BOTTOM,
    FUNCTION_BLOCK_BODY_TOP,
    FUNCTION_BLOCK_CATEGORY_COLORS,
    FUNCTION_BLOCK_DEFAULT_HEADER,
    FUNCTION_BLOCK_NORMAL_BORDER,
    FUNCTION_BLOCK_SELECTED_BORDER,
)
from .terminal_item import TerminalItem, TERM_RADIUS


# ── ISA-101 / ISA-101 Silver Theme ──

class ISA101Colors:
    """ISA-101 Silver theme color scheme."""
    BACKGROUND_LIGHT = QColor("#E4E7EC")
    NORMAL_BORDER = QColor(FUNCTION_BLOCK_NORMAL_BORDER)
    NORMAL_TEXT = QColor("#1B2130")
    SELECTED = QColor(FUNCTION_BLOCK_SELECTED_BORDER)
    RUNNING = QColor("#2D8E3C")
    ALARM_HIGH = QColor("#E8272C")
    ALARM_MEDIUM = QColor("#E87722")


# Block sizing
BLOCK_MIN_WIDTH = 140
BLOCK_MAX_WIDTH = 400
BLOCK_HEADER_HEIGHT = 22
BLOCK_PIN_SPACING = 14   # tighter rows to match the smaller pins
BLOCK_PIN_MARGIN = 10
BLOCK_CORNER_RADIUS = 4
GRID_SIZE = 10

# Pin-label layout. The label font must match _draw_pin_labels(), because the
# block width is derived from it: a fixed width clipped long input names (224
# labels across 59 of the 140 block types) and let long output names overflow
# outside the block — on AVTR the output column was 240px wide on a 140px
# block, painting over the inputs and the neighbouring block.
PIN_LABEL_FAMILY = "Segoe UI"
PIN_LABEL_SIZE = 6
PIN_LABEL_PAD = 8           # gap from the block edge to the label
PIN_LABEL_GUTTER = 12       # minimum clear space between the two columns
PIN_LABEL_MAX = 96          # per-column cap; longer names are elided

# Header typography is deliberately separate from the pin typography.  The
# instance name is the diagram's primary identifier, while the algorithm type
# is supporting information at the upper-right.  Painting the instance name
# below the header put it directly over the first pin row (most visibly on DI
# blocks with SIMULATE_IN_D).
HEADER_NAME_SIZE = 7
HEADER_TYPE_SIZE = 5
HEADER_TYPE_MAX = 52

# Live overlay sizing
_LIVE_PANEL_HEIGHT = 52     # extra height added for live data display
_LIVE_PANEL_HEIGHT_IO = 28  # smaller overlay for AI/AO blocks

# Resize handle
_HANDLE_SIZE = 8     # size of the resize grip region (px)

# Mode submenu per block type — ISA mode order. Blocks not listed get
# no Mode submenu (a "mode" param can still live in their properties).
_MODE_MENU: dict[str, tuple[str, ...]] = {
    "PID":   ("OOS", "IMAN", "LO", "MAN", "AUTO", "CAS", "RCAS", "ROUT"),
    "AO":    ("OOS", "MAN", "AUTO", "CAS"),
    "AI":    ("OOS", "MAN", "AUTO"),
    "MANLD": ("OOS", "MAN", "AUTO"),
    "DEVCTL": ("OOS", "MAN", "AUTO"),
    "MOTOR_INTERLOCK": ("MAINT", "AUTO"),
}

# Mode badge colors (ISA-101 convention)
_MODE_COLORS = {
    "AUTO":  QColor("#2D8E3C"),   # green
    "CAS":   QColor("#3574C4"),   # blue
    "RCAS":  QColor("#3574C4"),   # blue
    "ROUT":  QColor("#3574C4"),   # blue
    "MAN":   QColor("#E8C822"),   # gold
    "LO":    QColor("#E87722"),   # orange
    "IMAN":  QColor("#E87722"),   # orange
    "OOS":   QColor("#9AA5B4"),   # gray
}

# Category → header color (ISA-101 Silver — muted tones)
_CAT_COLORS = {
    category: QColor(color)
    for category, color in FUNCTION_BLOCK_CATEGORY_COLORS.items()
}

# Status dot colors
_STATUS_COLORS = {
    BlockStatus.GOOD:      QColor("#2D8E3C"),
    BlockStatus.BAD:       QColor("#E8272C"),
    BlockStatus.UNCERTAIN: QColor("#E8C822"),
    BlockStatus.OOS:       QColor("#9AA5B4"),
}


class BlockItem(QGraphicsObject):
    """QGraphicsItem representing a function block on the strategy canvas.

    Supports interactive resizing: drag the right edge to change width,
    the bottom edge to change height, or the bottom-right corner for both.
    """

    # Signals
    positionChanged = Signal(str, float, float)  # block_id, x, y
    blockSelected = Signal(str)                   # block_id
    blockDoubleClicked = Signal(str)              # block_id
    blockResized = Signal(str)                    # block_id (after resize)

    def __init__(self, block: FunctionBlock, parent=None):
        super().__init__(parent)
        self.block = block
        # Parameter special items expose a module boundary; they are not
        # algorithms that can run at a reduced rate or be bypassed.
        self._is_special_palette_item = bool(
            getattr(type(block), "is_special_palette_item", False))
        self.terminal_items: dict[str, TerminalItem] = {}

        # Flags
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(5)

        # ── Azeo-style live monitoring ──
        self._live_mode: bool = False
        # Execution order is a primary editing concern in Azeo (it is shown on
        # the block and in the hierarchy), so show the badge by default. It only
        # paints once a compile has assigned an order, so an uncompiled diagram
        # is unaffected.
        self._show_exec_order: bool = True
        self._bypassed: bool = False
        self._live_values: dict = {}  # cached live values for display

        # Compute default size based on visible terminal count
        n_inputs = sum(1 for t in block.inputs.values() if not t.hidden)
        n_outputs = sum(1 for t in block.outputs.values() if not t.hidden)
        n_terms = max(n_inputs, n_outputs, 1)
        self._base_min_height = (
            36.0 if self._is_special_palette_item else
            BLOCK_HEADER_HEIGHT + n_terms * BLOCK_PIN_SPACING
            + BLOCK_PIN_MARGIN * 2
        )
        # Always reserve the live-panel space (blocks that show a live overlay)
        # so going online/offline never changes the block footprint — keeps the
        # canvas layout stable instead of "dynamically resizing" on download.
        self._min_height = self._base_min_height + self._live_extra()
        self._min_width = self._natural_width()
        self._width = getattr(block, '_ui_width', None) or self._min_width
        self._height = getattr(block, '_ui_height', None) or self._min_height

        # Resize state
        self._resizing = False
        self._resize_edge = 0   # bitmask: 1=right, 2=bottom
        self._resize_origin = QPointF()
        self._resize_start_w = 0.0
        self._resize_start_h = 0.0

        # Drag tracking for undo
        self._drag_start_pos: QPointF | None = None

        # Create terminal items
        self._create_terminals()

        # Drop shadow
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(8)
        shadow.setColor(QColor(0, 0, 0, 40))
        shadow.setOffset(2, 2)
        self.setGraphicsEffect(shadow)

        # Set initial position (snap to grid)
        self.setPos(self._snap(block.x), self._snap(block.y))

    # ------------------------------------------------------------ pin metrics
    @staticmethod
    def _pin_font_metrics() -> QFontMetrics:
        return QFontMetrics(QFont(PIN_LABEL_FAMILY, PIN_LABEL_SIZE))

    def _visible_pin_names(self) -> tuple[list[str], list[str]]:
        ins = [n for n, t in self.block.inputs.items() if not t.hidden]
        outs = [n for n, t in self.block.outputs.items() if not t.hidden]
        return ins, outs

    def _pin_column_widths(self) -> tuple[float, float]:
        """Rendered width of each label column, capped at PIN_LABEL_MAX."""
        fm = self._pin_font_metrics()
        ins, outs = self._visible_pin_names()
        wi = max((fm.horizontalAdvance(n) for n in ins), default=0.0)
        wo = max((fm.horizontalAdvance(n) for n in outs), default=0.0)
        return min(wi, PIN_LABEL_MAX), min(wo, PIN_LABEL_MAX)

    def _natural_width(self) -> float:
        """Width that fits both label columns without clipping or overlap."""
        if self._is_special_palette_item:
            font = QFont(PIN_LABEL_FAMILY, 8, QFont.Bold)
            label = getattr(self.block, "parameter_name", "") \
                or self.block.instance_name
            return min(260.0, max(96.0,
                                  QFontMetrics(font).horizontalAdvance(label)
                                  + 34.0))
        wi, wo = self._pin_column_widths()
        needed = PIN_LABEL_PAD + wi + PIN_LABEL_GUTTER + wo + PIN_LABEL_PAD
        # The header carries the instance name as its title and the block type
        # as a compact right-hand classification.  Size for both so ordinary
        # names do not need to compete with pin labels for body space.
        fm_name = QFontMetrics(QFont(
            PIN_LABEL_FAMILY, HEADER_NAME_SIZE, QFont.Bold))
        fm_type = QFontMetrics(QFont(
            PIN_LABEL_FAMILY, HEADER_TYPE_SIZE, QFont.Bold))
        icon_and_status = (BLOCK_HEADER_HEIGHT - 4) + 28
        header = (
            fm_name.horizontalAdvance(self.block.instance_name)
            + min(fm_type.horizontalAdvance(self.block.block_type) + 4,
                  HEADER_TYPE_MAX)
            + icon_and_status
        )
        natural = max(BLOCK_MIN_WIDTH, needed, header)
        return min(natural, BLOCK_MAX_WIDTH)

    def _header_text_layout(
        self, badge_count: int = 0,
    ) -> tuple[QRectF, QRectF, QPointF]:
        """Return non-overlapping name, type, and status-dot geometry.

        The geometry is calculated in one place because forced and debugger
        badges consume header space dynamically.  Eliding the title inside
        its remaining rectangle is preferable to ever letting it fall into
        the connector rows below.
        """
        icon_size = BLOCK_HEADER_HEIGHT - 4
        fm_type = QFontMetrics(QFont(
            PIN_LABEL_FAMILY, HEADER_TYPE_SIZE, QFont.Bold))
        type_width = min(
            HEADER_TYPE_MAX,
            max(14, fm_type.horizontalAdvance(self.block.block_type) + 4),
        )
        # The type occupies the upper-right; the status dot owns the narrow
        # strip to its right and sits lower, so the two meanings stay distinct.
        type_rect = QRectF(
            self._width - type_width - 17, 1,
            type_width, BLOCK_HEADER_HEIGHT - 12,
        )
        title_left = 5 + icon_size
        title_right = type_rect.left() - 3 - badge_count * 16
        title_rect = QRectF(
            title_left, 1,
            max(0.0, title_right - title_left), BLOCK_HEADER_HEIGHT - 2,
        )
        status_center = QPointF(self._width - 9, BLOCK_HEADER_HEIGHT - 7)
        return title_rect, type_rect, status_center

    def _elide(self, fm: QFontMetrics, name: str, avail: float) -> str:
        if fm.horizontalAdvance(name) <= avail:
            return name
        return fm.elidedText(name, Qt.ElideRight, int(avail))

    # ---------------------------------------------------------------- terminals

    def _create_terminals(self):
        """Create TerminalItem children for each visible block terminal."""
        y_start = (self._height / 2 if self._is_special_palette_item
                   else BLOCK_HEADER_HEIGHT + BLOCK_PIN_MARGIN)

        # Inputs — left edge (skip hidden)
        idx = 0
        for name, terminal in self.block.inputs.items():
            if terminal.hidden:
                continue
            ti = TerminalItem(terminal, self)
            ti.setPos(0, y_start + idx * BLOCK_PIN_SPACING)
            self.terminal_items[f"in:{name}"] = ti
            idx += 1

        # Outputs — right edge (skip hidden)
        idx = 0
        for name, terminal in self.block.outputs.items():
            if terminal.hidden:
                continue
            ti = TerminalItem(terminal, self)
            ti.setPos(self._width, y_start + idx * BLOCK_PIN_SPACING)
            self.terminal_items[f"out:{name}"] = ti
            idx += 1

    def _reposition_outputs(self):
        """Move output terminals to the current right edge."""
        y_start = (self._height / 2 if self._is_special_palette_item
                   else BLOCK_HEADER_HEIGHT + BLOCK_PIN_MARGIN)
        if self._is_special_palette_item:
            for name, terminal in self.block.inputs.items():
                if terminal.hidden:
                    continue
                item = self.terminal_items.get(f"in:{name}")
                if item:
                    item.setPos(0, y_start)
        idx = 0
        for name, terminal in self.block.outputs.items():
            if terminal.hidden:
                continue
            ti = self.terminal_items.get(f"out:{name}")
            if ti:
                ti.setPos(self._width, y_start + idx * BLOCK_PIN_SPACING)
            idx += 1

    def get_terminal_item(self, direction: str, name: str) -> TerminalItem | None:
        return self.terminal_items.get(f"{direction}:{name}")

    # ---------------------------------------------------------------- geometry

    def _snap(self, v: float) -> float:
        return round(v / GRID_SIZE) * GRID_SIZE

    def boundingRect(self) -> QRectF:
        m = TERM_RADIUS + 2
        return QRectF(-m, -2, self._width + 2 * m, self._height + 4)

    # ---------------------------------------------------------------- resize hit-test

    def _hit_edge(self, pos: QPointF) -> int:
        """Return bitmask: 1=right edge, 2=bottom edge, 3=corner."""
        edge = 0
        if pos.x() >= self._width - _HANDLE_SIZE:
            edge |= 1
        if pos.y() >= self._height - _HANDLE_SIZE:
            edge |= 2
        return edge

    def _cursor_for_edge(self, edge: int) -> Qt.CursorShape:
        if edge == 3:
            return Qt.SizeFDiagCursor
        elif edge == 1:
            return Qt.SizeHorCursor
        elif edge == 2:
            return Qt.SizeVerCursor
        return Qt.ArrowCursor

    # ---------------------------------------------------------------- paint

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0, 0, self._width, self._height)
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)

        if self._is_special_palette_item:
            self._paint_parameter_item(painter, rect, is_selected)
            return

        cat_color = _CAT_COLORS.get(
            self.block.category, QColor(FUNCTION_BLOCK_DEFAULT_HEADER)
        )

        runtime = self._debug_runtime()
        debug_marker = runtime.debug_marker_for(self.block.id) if runtime else None

        # ── Determine border color (status-aware when online) ──
        if debug_marker == "current":
            border_color = QColor("#1769AA")
        elif debug_marker == "next":
            border_color = QColor("#D17A00")
        elif is_selected:
            border_color = ISA101Colors.SELECTED
        elif self._live_mode and self.block.status == BlockStatus.BAD:
            border_color = ISA101Colors.ALARM_HIGH
        elif self._live_mode and self.block.status == BlockStatus.UNCERTAIN:
            border_color = ISA101Colors.ALARM_MEDIUM
        else:
            border_color = ISA101Colors.NORMAL_BORDER

        # ── Silver body with subtle gradient ──
        body_grad = QLinearGradient(0, 0, 0, self._height)
        if self._bypassed:
            body_grad.setColorAt(0, QColor("#F0E8E8"))
            body_grad.setColorAt(1, QColor("#E8DEDE"))
        else:
            body_grad.setColorAt(0, QColor(FUNCTION_BLOCK_BODY_TOP))
            body_grad.setColorAt(1, QColor(FUNCTION_BLOCK_BODY_BOTTOM))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(body_grad))
        painter.drawRoundedRect(rect, BLOCK_CORNER_RADIUS, BLOCK_CORNER_RADIUS)

        # ── Header with metallic gradient ──
        header_rect = QRectF(0, 0, self._width, BLOCK_HEADER_HEIGHT)
        header_grad = QLinearGradient(0, 0, 0, BLOCK_HEADER_HEIGHT)
        if self._bypassed:
            header_grad.setColorAt(0, QColor("#A0A0A0"))
            header_grad.setColorAt(1, QColor("#808080"))
        else:
            header_grad.setColorAt(0, cat_color.lighter(120))
            header_grad.setColorAt(0.5, cat_color)
            header_grad.setColorAt(1, cat_color.darker(110))
        painter.setBrush(QBrush(header_grad))
        painter.setPen(Qt.NoPen)
        # Rounded top, square bottom
        painter.drawRoundedRect(
            header_rect.adjusted(0, 0, 0, BLOCK_CORNER_RADIUS),
            BLOCK_CORNER_RADIUS, BLOCK_CORNER_RADIUS,
        )
        painter.drawRect(QRectF(
            0, BLOCK_HEADER_HEIGHT - BLOCK_CORNER_RADIUS,
            self._width, BLOCK_CORNER_RADIUS,
        ))

        # Header divider
        painter.setPen(QPen(cat_color.darker(130), 1))
        painter.drawLine(0, BLOCK_HEADER_HEIGHT, int(self._width), BLOCK_HEADER_HEIGHT)

        # ── Status accent stripe (left edge, Azeo-style) ──
        if self._live_mode:
            status_color = _STATUS_COLORS.get(self.block.status, QColor("#9AA5B4"))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(status_color))
            painter.drawRoundedRect(
                QRectF(0, 0, 3, self._height),
                BLOCK_CORNER_RADIUS, 0,
            )

        # ── Border ──
        pen_w = 3 if debug_marker is not None \
            else (2 if is_selected else 1)
        painter.setPen(QPen(border_color, pen_w))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(
            rect.adjusted(pen_w / 2, pen_w / 2, -pen_w / 2, -pen_w / 2),
            BLOCK_CORNER_RADIUS, BLOCK_CORNER_RADIUS,
        )

        has_breakpoint = bool(
            runtime and runtime.has_debug_breakpoint(self.block.id))
        badge_count = int(self.block.any_forced()) + int(has_breakpoint)
        title_rect, type_rect, status_center = self._header_text_layout(
            badge_count)

        # ── Status dot ──
        status_color = _STATUS_COLORS.get(self.block.status, QColor("#9AA5B4"))
        painter.setBrush(QBrush(status_color))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(status_center, 3.5, 3.5)

        # Dynamic badges occupy explicit slots immediately left of the type.
        # The title rectangle was shortened by the same number of slots, so a
        # forced/debugged block cannot turn its header into overlapping text.
        badge_x = type_rect.left() - 16

        # ── Force ("F") badge — DCS-standard indicator that one or more
        # terminals on this block are being overridden by an operator
        # force / parameter pulse.
        if self.block.any_forced():
            badge_w, badge_h = 14, 12
            bx = badge_x
            by = 4
            painter.setPen(QPen(QColor("#000000"), 0.6))
            painter.setBrush(QBrush(QColor("#C62828")))
            painter.drawRoundedRect(QRectF(bx, by, badge_w, badge_h), 2, 2)
            painter.setFont(QFont("Consolas", 7, QFont.Bold))
            painter.setPen(QPen(QColor("#FFFFFF"), 1))
            painter.drawText(QRectF(bx, by, badge_w, badge_h),
                              Qt.AlignCenter, "F")
            badge_x -= 16

        # Persistent online-debug breakpoint. It occupies its own header slot
        # rather than borrowing alarm/status colour, so commissioning state
        # can never be mistaken for process state.
        if has_breakpoint:
            bx = badge_x
            by = 4
            painter.setPen(QPen(QColor("#7A0A16"), 0.8))
            painter.setBrush(QBrush(QColor("#D52235")))
            painter.drawEllipse(QRectF(bx, by, 12, 12))
            painter.setFont(QFont("Segoe UI", 6, QFont.Bold))
            painter.setPen(QPen(QColor("#FFFFFF"), 1))
            painter.drawText(QRectF(bx, by, 12, 12), Qt.AlignCenter, "B")

        # ── Block-type icon (left side of header) ──
        from azeo_control_trainer.core.presentation.function_block_icons import draw_block_icon
        icon_size = BLOCK_HEADER_HEIGHT - 4
        icon_rect = QRectF(3, 2, icon_size, icon_size)
        draw_block_icon(painter, icon_rect,
                         self.block.block_type, self.block.category,
                         QColor("#FFFFFF"))

        # ── Header title (instance name, white, centred) ──
        painter.setPen(QPen(QColor("#FFFFFF")))
        name_font = QFont("Segoe UI", HEADER_NAME_SIZE, QFont.Bold)
        name_font.setStyleStrategy(QFont.PreferAntialias)
        painter.setFont(name_font)
        name_metrics = QFontMetrics(name_font)
        painter.drawText(
            title_rect,
            Qt.AlignVCenter | Qt.AlignCenter,
            self._elide(name_metrics, self.block.instance_name,
                        title_rect.width()),
        )

        # ── Algorithm type (supporting label, upper-right) ──
        type_font = QFont("Segoe UI", HEADER_TYPE_SIZE, QFont.Bold)
        type_font.setStyleStrategy(QFont.PreferAntialias)
        painter.setFont(type_font)
        type_metrics = QFontMetrics(type_font)
        painter.setPen(QPen(QColor(255, 255, 255, 215)))
        painter.drawText(
            type_rect,
            Qt.AlignTop | Qt.AlignRight,
            self._elide(type_metrics, self.block.block_type,
                        type_rect.width()),
        )

        # ── Pin labels ──
        self._draw_pin_labels(painter)

        # ── Live value overlay (Azeo-style) ──
        if self._live_mode:
            self._draw_live_overlay(painter)

        # ── Execution order badge ──
        if self._show_exec_order and self.block._exec_order >= 0:
            self._draw_exec_order_badge(painter)

        # ── Block scan rate — Azeo shows it only when it is not 1 ──
        if (not self._is_special_palette_item
                and int(getattr(self.block, "scan_rate", 1)) > 1):
            self._draw_scan_rate_badge(painter)

        # ── Bypass slash ──
        if self._bypassed and not self._is_special_palette_item:
            self._draw_bypass_overlay(painter)

        # ── Resize grip (bottom-right corner) ──
        if is_selected:
            self._draw_resize_grip(painter)

    def _paint_parameter_item(self, painter: QPainter, rect: QRectF,
                              selected: bool) -> None:
        """Compact Azeo-style token for a module parameter Special Item."""
        bad = self._live_mode and self.block.status == BlockStatus.BAD
        border = (ISA101Colors.SELECTED if selected else
                  ISA101Colors.ALARM_HIGH if bad else
                  QColor("#7B8491"))
        painter.setPen(QPen(border, 2 if selected else 1))
        painter.setBrush(QBrush(QColor("#E3E5E9")))
        painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))

        access = str(getattr(self.block, "parameter_access", ""))
        source = bool(getattr(self.block, "parameter_source", False))
        public = access in ("input", "output")
        accent = QColor("#B42B8D") if public else QColor("#2D78B7")
        edge_x = rect.right() - 4 if source else rect.left() + 1
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(accent))
        painter.drawRect(QRectF(edge_x, rect.top() + 1, 3, rect.height() - 2))

        name = getattr(self.block, "parameter_name", "") \
            or self.block.instance_name
        dtype = str(self.block.config.params.get("data_type", "FLOAT")).upper()
        painter.setPen(QPen(ISA101Colors.NORMAL_TEXT))
        painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
        painter.drawText(QRectF(8, 3, rect.width() - 16, 16),
                         Qt.AlignVCenter | Qt.AlignLeft, name)
        painter.setPen(QPen(QColor("#5E6672")))
        painter.setFont(QFont("Segoe UI", 6))
        direction = "SOURCE" if source else "SINK"
        painter.drawText(QRectF(8, 19, rect.width() - 16, 12),
                         Qt.AlignVCenter | Qt.AlignLeft,
                         f"{dtype}  ·  {direction}")
        if selected:
            self._draw_resize_grip(painter)

    def _draw_pin_labels(self, painter: QPainter):
        """Draw pin names next to each visible terminal.

        Each column gets a share of the block width and is elided rather than
        clipped, so a long name reads as ``CLEAR_PREV_F…`` instead of being cut
        mid-glyph, and the two columns can never paint over each other.
        """
        painter.setPen(QPen(ISA101Colors.NORMAL_TEXT))
        font = QFont(PIN_LABEL_FAMILY, PIN_LABEL_SIZE)
        painter.setFont(font)
        fm = QFontMetrics(font)

        wi, wo = self._pin_column_widths()
        # Share out whatever width the block actually has (the user may have
        # resized it narrower than natural).
        budget = max(0.0, self._width - 2 * PIN_LABEL_PAD - PIN_LABEL_GUTTER)
        if wi + wo > budget and (wi + wo) > 0:
            scale = budget / (wi + wo)
            wi, wo = wi * scale, wo * scale

        y_start = BLOCK_HEADER_HEIGHT + BLOCK_PIN_MARGIN

        # Inputs — left column, left-aligned
        for idx, name in enumerate(self._visible_pin_names()[0]):
            y = y_start + idx * BLOCK_PIN_SPACING
            painter.drawText(
                QRectF(PIN_LABEL_PAD, y - 5, wi, 10),
                Qt.AlignLeft | Qt.AlignVCenter,
                self._elide(fm, name, wi),
            )

        # Outputs — right column, right-aligned to the block edge
        for idx, name in enumerate(self._visible_pin_names()[1]):
            y = y_start + idx * BLOCK_PIN_SPACING
            painter.drawText(
                QRectF(self._width - PIN_LABEL_PAD - wo, y - 5, wo, 10),
                Qt.AlignRight | Qt.AlignVCenter,
                self._elide(fm, name, wo),
            )

    # ── Azeo-style live value overlay ──

    def _draw_live_overlay(self, painter: QPainter):
        """Draw live PV/SP/OUT and mode badge on the block face."""
        bt = self.block.block_type
        if bt == "PID":
            self._draw_pid_live_overlay(painter)
        elif bt in ("AI", "DI"):
            self._draw_io_live_overlay(painter, is_output=True)
        elif bt in ("AO", "DO"):
            self._draw_io_live_overlay(painter, is_output=False)
        else:
            self._draw_generic_live_overlay(painter)

    def _draw_pid_live_overlay(self, painter: QPainter):
        """PID block: show PV, SP, OUT values + mode badge + OUT bar."""
        y0 = self._height - _LIVE_PANEL_HEIGHT
        w = self._width

        # Dark panel background
        panel = QRectF(2, y0, w - 4, _LIVE_PANEL_HEIGHT - 2)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(20, 25, 35, 220)))
        painter.drawRoundedRect(panel, 3, 3)

        # Get values from block terminals
        pv = self.block.outputs.get("PV")
        sp_term = self.block.outputs.get("SP")
        out = self.block.outputs.get("OUT")
        mode_val = self.block.config.params.get("mode", "AUTO")

        pv_val = pv.value if pv else 0.0
        sp_val = sp_term.value if sp_term else 0.0
        out_val = out.value if out else 0.0

        font_label = QFont("Consolas", 6)
        font_value = QFont("Consolas", 7, QFont.Bold)

        lx = 6  # left margin
        rx = w - 6  # right margin
        row_h = 10

        # Row 1: PV
        painter.setFont(font_label)
        painter.setPen(QPen(QColor("#90A4AE")))
        painter.drawText(QRectF(lx, y0 + 2, 20, row_h), Qt.AlignLeft | Qt.AlignVCenter, "PV")
        painter.setFont(font_value)
        painter.setPen(QPen(QColor("#80CBC4")))  # teal
        painter.drawText(QRectF(lx + 18, y0 + 2, rx - lx - 18, row_h),
                         Qt.AlignRight | Qt.AlignVCenter, f"{pv_val:.2f}")

        # Row 2: SP
        painter.setFont(font_label)
        painter.setPen(QPen(QColor("#90A4AE")))
        painter.drawText(QRectF(lx, y0 + 13, 20, row_h), Qt.AlignLeft | Qt.AlignVCenter, "SP")
        painter.setFont(font_value)
        painter.setPen(QPen(QColor("#CE93D8")))  # purple
        painter.drawText(QRectF(lx + 18, y0 + 13, rx - lx - 18, row_h),
                         Qt.AlignRight | Qt.AlignVCenter, f"{sp_val:.2f}")

        # Row 3: OUT bar + value
        painter.setFont(font_label)
        painter.setPen(QPen(QColor("#90A4AE")))
        painter.drawText(QRectF(lx, y0 + 24, 24, row_h), Qt.AlignLeft | Qt.AlignVCenter, "OUT")

        # OUT progress bar
        bar_x = lx + 22
        bar_w = w - bar_x - 36
        bar_y = y0 + 26
        bar_h = 6
        if bar_w > 10:
            # Background
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(60, 60, 80)))
            painter.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)
            # Fill
            fill_w = max(0, min(bar_w, bar_w * out_val))
            if fill_w > 0:
                painter.setBrush(QBrush(QColor("#4FC3F7")))  # light blue
                painter.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)

        painter.setFont(font_value)
        painter.setPen(QPen(QColor("#4FC3F7")))
        painter.drawText(QRectF(rx - 34, y0 + 24, 34, row_h),
                         Qt.AlignRight | Qt.AlignVCenter,
                         f"{out_val * 100:.0f}%")

        # Mode badge (bottom-right of panel)
        mode_str = str(mode_val).upper() if mode_val else "AUTO"
        mode_color = _MODE_COLORS.get(mode_str, QColor("#9AA5B4"))
        badge_w = max(28, len(mode_str) * 6 + 8)
        badge_rect = QRectF(w - badge_w - 4, y0 + 36, badge_w, 12)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(mode_color))
        painter.drawRoundedRect(badge_rect, 3, 3)
        painter.setPen(QPen(QColor("#FFFFFF")))
        painter.setFont(QFont("Segoe UI", 6, QFont.Bold))
        painter.drawText(badge_rect, Qt.AlignCenter, mode_str)

    def _draw_io_live_overlay(self, painter: QPainter, is_output: bool):
        """AI/AO block: show current value with unit."""
        y0 = self._height - _LIVE_PANEL_HEIGHT_IO
        w = self._width

        # Dark panel
        panel = QRectF(2, y0, w - 4, _LIVE_PANEL_HEIGHT_IO - 2)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(20, 25, 35, 220)))
        painter.drawRoundedRect(panel, 3, 3)

        # Get value
        if is_output:
            term = self.block.outputs.get("OUT")
        else:
            term = self.block.inputs.get("IN")
        val = term.value if term else 0.0

        # Tag name
        tag = self.block.config.params.get("tag", "")
        short_tag = tag.split(".")[-1] if tag else ""

        font_value = QFont("Consolas", 8, QFont.Bold)
        font_tag = QFont("Consolas", 6)

        # Tag label (left)
        if short_tag:
            painter.setFont(font_tag)
            painter.setPen(QPen(QColor("#90A4AE")))
            painter.drawText(QRectF(6, y0 + 2, w * 0.4, 12),
                             Qt.AlignLeft | Qt.AlignVCenter, short_tag)

        # Value (right)
        painter.setFont(font_value)
        painter.setPen(QPen(QColor("#80CBC4")))
        if isinstance(val, float):
            if abs(val) >= 1000:
                txt = f"{val:.0f}"
            elif abs(val) >= 10:
                txt = f"{val:.1f}"
            else:
                txt = f"{val:.2f}"
        else:
            txt = str(val)
        painter.drawText(QRectF(6, y0 + 2, w - 14, _LIVE_PANEL_HEIGHT_IO - 6),
                         Qt.AlignRight | Qt.AlignVCenter, txt)

        # Mode badge for AO
        if not is_output:
            mode_val = self.block.config.params.get("mode", "CAS")
            mode_str = str(mode_val).upper()
            mode_color = _MODE_COLORS.get(mode_str, QColor("#9AA5B4"))
            badge_rect = QRectF(4, y0 + 15, 24, 10)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(mode_color))
            painter.drawRoundedRect(badge_rect, 2, 2)
            painter.setPen(QPen(QColor("#FFFFFF")))
            painter.setFont(QFont("Segoe UI", 5, QFont.Bold))
            painter.drawText(badge_rect, Qt.AlignCenter, mode_str)

    def _draw_generic_live_overlay(self, painter: QPainter):
        """Generic block: show primary output value."""
        out_term = None
        for name, term in self.block.outputs.items():
            if name == "OUT" or not out_term:
                out_term = term
                if name == "OUT":
                    break

        if out_term is None:
            return

        y0 = self._height - _LIVE_PANEL_HEIGHT_IO
        w = self._width

        panel = QRectF(2, y0, w - 4, _LIVE_PANEL_HEIGHT_IO - 2)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(20, 25, 35, 200)))
        painter.drawRoundedRect(panel, 3, 3)

        val = out_term.value
        if isinstance(val, float):
            txt = f"{val:.3f}" if abs(val) < 10 else f"{val:.1f}"
        elif isinstance(val, bool):
            txt = "TRUE" if val else "FALSE"
        else:
            txt = str(val)[:12]

        painter.setFont(QFont("Consolas", 7, QFont.Bold))
        painter.setPen(QPen(QColor("#B0BEC5")))
        painter.drawText(QRectF(6, y0 + 2, w - 14, _LIVE_PANEL_HEIGHT_IO - 6),
                         Qt.AlignCenter, txt)

    # ── Execution order badge ──

    def _draw_exec_order_badge(self, painter: QPainter):
        """Draw a small numbered badge showing scan execution order."""
        order = self.block._exec_order
        txt = str(order + 1)  # 1-based display

        font = QFont("Segoe UI", 6, QFont.Bold)
        painter.setFont(font)
        fm = QFontMetrics(font)
        tw = fm.horizontalAdvance(txt) + 6
        th = fm.height() + 2
        badge_size = max(tw, th, 16)

        badge_rect = QRectF(-badge_size / 2 + 2, -badge_size / 2 + 2,
                            badge_size, badge_size)

        # Dark circle with white text
        painter.setPen(QPen(QColor("#1A1A1A"), 1))
        painter.setBrush(QBrush(QColor(30, 40, 60, 230)))
        painter.drawRoundedRect(badge_rect, badge_size / 2, badge_size / 2)

        painter.setPen(QPen(QColor("#E0E8F0")))
        painter.drawText(badge_rect, Qt.AlignCenter, txt)

    def _draw_scan_rate_badge(self, painter: QPainter):
        """Draw the Azeo block scan rate (``1:N``) in the top-right corner.

        Azeo: "If the block scan rate is one, it is not displayed on the
        block" — the caller gates on that, so anything drawn here is a real
        divider the engineer needs to see.
        """
        txt = f"1:{int(self.block.scan_rate)}"
        font = QFont("Segoe UI", 6, QFont.Bold)
        painter.setFont(font)
        fm = QFontMetrics(font)
        tw = fm.horizontalAdvance(txt) + 8
        th = fm.height() + 2
        rect = QRectF(self._width - tw - 2, 2, tw, th)

        painter.setPen(QPen(QColor("#8A6D1A"), 1))
        painter.setBrush(QBrush(QColor(255, 214, 102, 235)))
        painter.drawRoundedRect(rect, 3, 3)
        painter.setPen(QPen(QColor("#3D3000")))
        painter.drawText(rect, Qt.AlignCenter, txt)

    # ── Bypass overlay ──

    def _draw_bypass_overlay(self, painter: QPainter):
        """Draw diagonal slash indicating block is bypassed."""
        painter.setPen(QPen(QColor(200, 40, 40, 160), 3, Qt.SolidLine))
        painter.drawLine(QPointF(4, 4), QPointF(self._width - 4, self._height - 4))
        painter.drawLine(QPointF(self._width - 4, 4), QPointF(4, self._height - 4))

    def _draw_resize_grip(self, painter: QPainter):
        """Draw a subtle resize grip at the bottom-right corner."""
        x0 = self._width - 3
        y0 = self._height - 3
        painter.setPen(QPen(QColor("#9AA5B4"), 1))
        for i in range(3):
            offset = i * 3
            painter.drawPoint(QPointF(x0 - offset, y0))
            painter.drawPoint(QPointF(x0, y0 - offset))
            if i > 0:
                painter.drawPoint(QPointF(x0 - offset, y0 - (3 - offset) if offset < 3 else y0))

    # ---------------------------------------------------------------- interaction

    def hoverMoveEvent(self, event):
        edge = self._hit_edge(event.pos())
        if edge and self.isSelected():
            self.setCursor(QCursor(self._cursor_for_edge(edge)))
        else:
            self.unsetCursor()
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event):
        self.unsetCursor()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent):
        if event.button() == Qt.LeftButton and self.isSelected():
            edge = self._hit_edge(event.pos())
            if edge:
                self._resizing = True
                self._resize_edge = edge
                self._resize_origin = event.scenePos()
                self._resize_start_w = self._width
                self._resize_start_h = self._height
                # Suppress move while resizing
                self.setFlag(QGraphicsItem.ItemIsMovable, False)
                event.accept()
                return
        # Track drag start for undo
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = QPointF(self.pos())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent):
        if self._resizing:
            delta = event.scenePos() - self._resize_origin
            new_w = self._resize_start_w
            new_h = self._resize_start_h

            if self._resize_edge & 1:   # right
                new_w = max(getattr(self, '_min_width', BLOCK_MIN_WIDTH),
                            self._resize_start_w + delta.x())
                new_w = min(new_w, BLOCK_MAX_WIDTH)
                new_w = self._snap(new_w)
            if self._resize_edge & 2:   # bottom
                new_h = max(self._min_height, self._resize_start_h + delta.y())
                new_h = self._snap(new_h)

            scene = self.scene()
            sheet = getattr(scene, "sheet_rect", None) if scene else None
            if sheet is not None:
                # Resizing is an authoring operation too: keeping only the
                # origin on-page still lets the block grow beyond the finite
                # sheet and hide terminals where they cannot be recovered.
                term_margin = TERM_RADIUS + 2
                available_w = sheet.right() - self.pos().x() - term_margin
                available_h = sheet.bottom() - self.pos().y() - 2
                max_w = max(
                    getattr(self, '_min_width', BLOCK_MIN_WIDTH),
                    int(available_w // GRID_SIZE) * GRID_SIZE,
                )
                max_h = max(
                    self._min_height,
                    int(available_h // GRID_SIZE) * GRID_SIZE,
                )
                new_w = min(new_w, max_w)
                new_h = min(new_h, max_h)

            if new_w != self._width or new_h != self._height:
                self.prepareGeometryChange()
                self._width = new_w
                self._height = new_h
                self._reposition_outputs()
                # Persist to block model
                self.block._ui_width = self._width
                self.block._ui_height = self._height
                self.update()
                # Update connected wires
                self._notify_wires_changed()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent):
        if self._resizing:
            self._resizing = False
            self._resize_edge = 0
            self.setFlag(QGraphicsItem.ItemIsMovable, True)
            self.blockResized.emit(self.block.id)
            event.accept()
            return
        # Push move undo command if position changed
        if (event.button() == Qt.LeftButton
                and self._drag_start_pos is not None
                and self._drag_start_pos != self.pos()):
            scene = self.scene()
            if scene and hasattr(scene, '_undo_stack'):
                from ..undo import MoveBlockCommand
                scene._undo_stack.push(MoveBlockCommand(
                    scene, self.block.id,
                    self._drag_start_pos, QPointF(self.pos())))
            self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def _notify_wires_changed(self):
        """Tell the scene to update wires connected to this block."""
        scene = self.scene()
        if scene and hasattr(scene, '_on_block_moved'):
            scene._on_block_moved(self.block.id, self.pos().x(), self.pos().y())

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            candidate = QPointF(self._snap(value.x()), self._snap(value.y()))
            scene = self.scene()
            constrain = getattr(scene, "constrain_item_position", None)
            if callable(constrain):
                candidate = constrain(self, candidate)
            return candidate

        if change == QGraphicsItem.ItemPositionHasChanged:
            pos = self.pos()
            self.block.x = pos.x()
            self.block.y = pos.y()
            self.positionChanged.emit(self.block.id, pos.x(), pos.y())

        elif change == QGraphicsItem.ItemSelectedHasChanged and value:
            self.blockSelected.emit(self.block.id)

        return super().itemChange(change, value)

    def mouseDoubleClickEvent(self, event):
        # PID blocks in live mode → open faceplate instead of properties
        if self._live_mode and self.block.block_type == "PID":
            self.faceplateRequested.emit(self.block.id)
            event.accept()
            return
        self.blockDoubleClicked.emit(self.block.id)
        super().mouseDoubleClickEvent(event)

    # Signal for faceplate request (caught by designer_tab)
    faceplateRequested = Signal(str)   # block_id
    trendRequested = Signal(str, list) # block_id, [tag_keys]
    bypassToggled = Signal(str, bool)  # block_id, bypassed

    def contextMenuEvent(self, event):
        """Azeo-style block context menu with enhanced actions."""
        from azeo_control_trainer.core.presentation.menu_style import studio_menu

        bt = self.block.block_type
        doc = (type(self.block).__doc__ or "").strip().splitlines()
        subtitle = doc[0].rstrip(".") if doc else ""
        menu = studio_menu(f"{bt}  {self.block.instance_name}",
                           subtitle[:48])

        # A marquee selection is one engineering object for this command.
        # Put it at the top rather than burying it below single-block actions,
        # and share the exact route with the empty-canvas context menu.
        scene = self.scene()
        if scene is not None and scene._add_create_composite_action(menu):
            menu.addSeparator()

        # ONE production faceplate path: the registered PVM class used by the
        # operator station. The former adjacent "PVM ... (Preview)" entry made
        # users choose between two incompatible implementations.
        if self._pvm_faceplate_available(bt):
            act_fp = menu.addAction("Open Faceplate...")
            act_fp.triggered.connect(
                lambda: self.faceplateRequested.emit(self.block.id))

        # ── Properties ──
        act_props = menu.addAction("Properties...")
        act_props.triggered.connect(lambda: self.blockDoubleClicked.emit(self.block.id))
        if bt == "COMPOSITE":
            act_definition = menu.addAction(
                self._composite_definition_action_text())
            act_definition.triggered.connect(
                lambda: self._open_composite_definition_dialog())
        menu.addSeparator()

        # ── Mode — submenu for any block whose config carries a 'mode' param ──
        modes_for_type = _MODE_MENU.get(bt)
        if modes_for_type:
            mode_menu = menu.addMenu("Mode")
            current_mode = str(self.block.config.params.get("mode", modes_for_type[0])).upper()
            for m in modes_for_type:
                act = mode_menu.addAction(m)
                act.setCheckable(True)
                act.setChecked(m == current_mode)
                act.triggered.connect(
                    lambda checked, mode=m: self._set_pid_mode(mode))
            menu.addSeparator()

        # ── Force value... — DCS-standard parameter override ──
        any_forced = self.block.any_forced()
        force_label = (f"Force value...  ({len(self.block.forced_terminals())} active)"
                        if any_forced else "Force value...")
        act_force = menu.addAction(force_label)
        act_force.triggered.connect(self._open_force_dialog)
        if any_forced:
            act_rel_all = menu.addAction("Release all forces")
            act_rel_all.triggered.connect(self._release_all_forces)
        menu.addSeparator()

        # ── Diagnostics — block status + scan timing + terminals + config ──
        act_diag = menu.addAction("Diagnostics...")
        act_diag.triggered.connect(self._open_diagnostics)

        # This debugger stops at compiled FBD block boundaries. SFC chart
        # state keeps its separate chart debugger.
        runtime = self._debug_runtime()
        if runtime is not None and runtime.is_online:
            debug_menu = menu.addMenu("FBD Debugger")
            breakpoint = debug_menu.addAction("Breakpoint")
            breakpoint.setCheckable(True)
            breakpoint.setChecked(self.block.id in runtime.debug_breakpoints)
            breakpoint.toggled.connect(self._set_debug_breakpoint)
            run_to = debug_menu.addAction("Run to This Block")
            run_to.triggered.connect(self._debug_run_to_here)
            debug_menu.addSeparator()
            pause = debug_menu.addAction(
                "Resume Module" if runtime.is_debug_paused else "Pause Module")
            pause.triggered.connect(self._toggle_debug_pause)
            step = debug_menu.addAction("Step One Block")
            step.setEnabled(runtime.is_debug_paused)
            step.triggered.connect(lambda: runtime.debug_step_block())
            run_scan = debug_menu.addAction("Run One Scan")
            run_scan.setEnabled(runtime.is_debug_paused)
            run_scan.triggered.connect(lambda: runtime.debug_run_scan())
            debug_menu.addSeparator()
            debugger_label = (
                "Open SFC Chart Debugger..."
                if bt == "SFC_CHART" else "Open FBD Debugger...")
            open_debugger = debug_menu.addAction(debugger_label)
            open_debugger.triggered.connect(self._open_runtime_debugger)

        # ── Find References — every upstream / downstream block ──
        act_refs = menu.addAction("Find References...")
        act_refs.triggered.connect(self._open_find_references)

        # ── Add to Watch — submenu of every terminal ──
        watch_menu = menu.addMenu("Add to Watch")
        self._build_watch_submenu(watch_menu)

        # ── Block Help — reference for the block that was right-clicked ──
        act_doc = menu.addAction("Block Help")
        act_doc.triggered.connect(self._open_documentation)
        menu.addSeparator()

        # ── Add to Trend ──
        trend_tags = self._collect_trend_tags()
        if trend_tags:
            act_trend = menu.addAction("Add to Trend...")
            act_trend.triggered.connect(
                lambda: self.trendRequested.emit(self.block.id, trend_tags))

        # ── Navigate to connected blocks ──
        connected = self._get_connected_blocks()
        if connected:
            nav_menu = menu.addMenu("Go to Block")
            for bid, bname, direction in connected:
                icon_prefix = "\u2190 " if direction == "input" else "\u2192 "
                act = nav_menu.addAction(f"{icon_prefix}{bname}")
                act.triggered.connect(
                    lambda checked, b=bid: self._navigate_to_block(b))
            menu.addSeparator()

        # ── Bypass (when online) ──
        if self._live_mode and not self._is_special_palette_item:
            act_bypass = menu.addAction(
                "Remove Bypass" if self._bypassed else "Bypass Block")
            act_bypass.triggered.connect(self._toggle_bypass)
            menu.addSeparator()

        # ── Show/Hide Pins ──
        pins_menu = menu.addMenu("Show/Hide Pins")
        self._build_pins_menu(pins_menu)
        menu.addSeparator()

        # ── Reset size ──
        act_reset = menu.addAction("Reset Size")
        act_reset.triggered.connect(self._reset_size)

        # ── Rename ──
        act_rename = menu.addAction("Rename...")
        act_rename.triggered.connect(self._rename_block)

        # ── Block Scan Rate — Azeo right-click entry ──
        if not self._is_special_palette_item:
            rate = getattr(self.block, "scan_rate", 1)
            rate_label = ("Block Scan Rate..." if rate == 1
                          else f"Block Scan Rate...  (1:{rate})")
            act_rate = menu.addAction(rate_label)
            act_rate.triggered.connect(self._set_block_scan_rate)
        menu.addSeparator()

        # ── Composite navigation ──
        if scene is not None:
            # ── Ungroup composite — only when this block is a COMPOSITE
            if bt == "COMPOSITE":
                act_ungroup = menu.addAction("Ungroup Composite Block")
                act_ungroup.setEnabled(not scene.structure_locked)
                act_ungroup.triggered.connect(self._ungroup_self)
                act_drill = menu.addAction("Open Interior in New Tab")
                act_drill.triggered.connect(
                    lambda: self.blockDoubleClicked.emit(self.block.id))
            menu.addSeparator()

        if scene is not None and not self._is_special_palette_item:
            heal = menu.addAction("Remove Block and Heal Connection")
            ordinary = [wire for wire in scene.graph.get_wires_for_block(self.block.id)
                        if not wire.is_bkcal]
            heal.setEnabled(not scene.structure_locked and len(ordinary) == 2)
            heal.setToolTip(
                "Available when the block has exactly one ordinary input and output")
            heal.triggered.connect(
                lambda: scene.remove_block_and_heal(self.block.id))
            menu.addSeparator()

        # ── Clipboard ops — promoted from keyboard shortcuts ─────────
        clip_menu = menu.addMenu("Clipboard")
        act_cut = clip_menu.addAction("Cut\tCtrl+X")
        act_cut.triggered.connect(self._cmd_cut)
        act_copy = clip_menu.addAction("Copy\tCtrl+C")
        act_copy.triggered.connect(self._cmd_copy)
        act_paste = clip_menu.addAction("Paste\tCtrl+V")
        act_paste.triggered.connect(self._cmd_paste)
        act_dup = clip_menu.addAction("Duplicate\tCtrl+D")
        act_dup.triggered.connect(self._cmd_duplicate)
        menu.addSeparator()

        # ── Delete ──
        act_del = menu.addAction("Delete Block")
        act_del.triggered.connect(self._request_delete)

        menu.exec_transient(event.screenPos())

    # ─── Phase 2 helpers ───────────────────────────────────────────
    def _composite_definition_action_text(self, library=None) -> str:
        """Context label that makes a linked instance's state visible."""
        state = "embedded"
        if self.block.block_type == "COMPOSITE":
            try:
                state = self.block.definition_state(library)
            except (OSError, TypeError, ValueError):
                state = "missing" if self.block.is_linked else "embedded"
        return f"Composite Definition...  [{state.upper()}]"

    def _open_composite_definition_dialog(self, library=None):
        """Open the reusable-definition editor modelessly and retain it."""
        existing = getattr(self, "_composite_definition_dialog", None)
        if existing is not None:
            try:
                existing.reload()
                existing.show()
                existing.raise_()
                existing.activateWindow()
                return existing
            except RuntimeError:
                self._composite_definition_dialog = None

        import weakref

        from ..dialogs.composite_library_dialog import CompositeLibraryDialog
        from azeo_control_trainer.core.presentation.headless import is_headless

        scene = self.scene()
        parent = scene.views()[0] if scene is not None and scene.views() else None
        dialog = CompositeLibraryDialog(
            self.block,
            library=library,
            parent=parent,
            change_guard=self._apply_composite_definition_change,
        )
        self._composite_definition_dialog = dialog
        self_ref = weakref.ref(self)

        def clear_dialog_reference():
            owner = self_ref()
            if owner is not None:
                owner._composite_definition_dialog = None

        dialog.destroyed.connect(clear_dialog_reference)
        if is_headless():
            dialog.setAttribute(Qt.WA_DontShowOnScreen, True)
        dialog.show()
        return dialog

    def _apply_composite_definition_change(self, mutation):
        """Apply a definition edit without corrupting the outer module.

        Definition operations replace the composite's dynamic terminals. A
        connected port may not disappear or become type-incompatible, and an
        on-scan module may not swap its compiled interior. The mutation is
        therefore transactional and rolls back before the dialog shows an
        error.
        """
        scene = self.scene()
        before_attributes = dict(self.block.__dict__)
        # Composite._rebuild_terminals() clears these dictionaries in place;
        # retain their entries as well as the owning attributes so rollback
        # restores the exact Terminal objects already held by WireItems.
        for name in ("inputs", "outputs", "_port_map_in", "_port_map_out"):
            before_attributes[name] = dict(before_attributes.get(name, {}))
        before_payload = self.block.to_dict()
        before_inner = self.block.inner_graph
        try:
            result = mutation()
            after_payload = self.block.to_dict()
            payload_changed = before_payload != after_payload
            graph_replaced = self.block.inner_graph is not before_inner
            if not payload_changed and not graph_replaced:
                return result

            reason = self._outer_composite_interface_error()
            if reason is None and scene is not None \
                    and getattr(scene, "structure_locked", False) \
                    and graph_replaced:
                reason = (
                    "the module is on scan; take it off scan before changing "
                    "a composite definition or public property")
            if reason is not None:
                if scene is not None and hasattr(scene, "_notice"):
                    scene._notice(reason)
                raise ValueError(reason)
        except Exception:
            self.block.__dict__.clear()
            self.block.__dict__.update(before_attributes)
            self.update()
            raise

        if graph_replaced:
            self._sync_outer_composite_connections()
            self.rebuild_terminals()
        if payload_changed and scene is not None \
                and hasattr(scene, "notify_modified"):
            scene.notify_modified()
        return result

    def _outer_composite_interface_error(self) -> str | None:
        """Explain why current outer wires cannot use the new boundary."""
        scene = self.scene()
        graph = getattr(scene, "graph", None) if scene is not None else None
        if graph is None:
            return None

        from azeo_control_trainer.core.strategy.model.strategy_graph import (
            types_compatible,
        )

        for wire in graph.get_wires_for_block(self.block.id):
            if wire.src_block_id == self.block.id:
                terminal = self.block.outputs.get(wire.src_terminal)
                other = graph.blocks.get(wire.dst_block_id)
                peer = other.inputs.get(wire.dst_terminal) if other else None
                label = f"output {self.block.instance_name}.{wire.src_terminal}"
            else:
                terminal = self.block.inputs.get(wire.dst_terminal)
                other = graph.blocks.get(wire.src_block_id)
                peer = other.outputs.get(wire.src_terminal) if other else None
                label = f"input {self.block.instance_name}.{wire.dst_terminal}"
            if terminal is None:
                return f"refresh rejected: connected {label} would be removed"
            if peer is None:
                return "refresh rejected: a connected outer terminal is missing"
            source, destination = ((terminal, peer)
                                   if wire.src_block_id == self.block.id
                                   else (peer, terminal))
            if not types_compatible(source.data_type, destination.data_type):
                return (
                    f"refresh rejected: connected {label} would change to "
                    f"{terminal.data_type.value}, which is incompatible with "
                    f"{peer.data_type.value}")
        return None

    def _sync_outer_composite_connections(self) -> None:
        """Restore connected flags after dynamic terminals were recreated."""
        for terminal in (*self.block.inputs.values(),
                         *self.block.outputs.values()):
            terminal.connected = False
        scene = self.scene()
        graph = getattr(scene, "graph", None) if scene is not None else None
        if graph is None:
            return
        for wire in graph.get_wires_for_block(self.block.id):
            if wire.src_block_id == self.block.id:
                terminal = self.block.outputs.get(wire.src_terminal)
            else:
                terminal = self.block.inputs.get(wire.dst_terminal)
            if terminal is not None:
                terminal.connected = True

    @staticmethod
    def _pvm_faceplate_available(block_type: str) -> bool:
        try:
            from azeo_control_trainer.core.hmi.pvms import registry
        except Exception:                           # noqa: BLE001
            return False
        return registry.get(block_type, "faceplate") is not None

    def _open_pvm_faceplate(self):
        """Open the type-driven production PVM faceplate, bound by path.

        The engine reads THIS scene's graph — the canvas block is the
        scanned block (the compiler does not copy), so the faceplate is
        live whenever the module is."""
        from azeo_control_trainer.core.hmi.binding import (
            BindingEngine, LiveGraphSource,
        )
        from azeo_control_trainer.core.hmi.pvms import registry
        from azeo_control_trainer.core.hmi.pvms.render import (
            PvmFaceplateWidget,
        )
        from azeo_control_trainer.core.presentation.headless import is_headless

        scene = self.scene()
        graph = getattr(scene, "graph", None)
        pvm_cls = registry.get(self.block.block_type, "faceplate")
        if graph is None or pvm_cls is None:
            return
        existing = getattr(self, "_pvm_faceplate", None)
        if existing is not None:
            try:
                existing.show()
                existing.raise_()
                existing.activateWindow()
                return existing
            except RuntimeError:
                # Its Qt owner was destroyed; build a fresh window.
                self._pvm_faceplate = None
        engine = BindingEngine(
            LiveGraphSource(lambda: {graph.name: graph}))
        path = f"{graph.name}/{self.block.instance_name}"
        parent = scene.views()[0] if scene.views() else None
        widget = PvmFaceplateWidget(pvm_cls, {"path": path}, engine,
                                    parent=parent)
        widget.setWindowFlag(Qt.Window, True)
        widget.resize(300, 420)
        self._pvm_faceplate = widget        # keep it from the GC
        if not is_headless():
            widget.show()
        return widget

    def _debug_canvas(self):
        """Find the StrategyCanvas that owns this scene without a back-cycle."""
        scene = self.scene()
        views = scene.views() if scene is not None else []
        widget = views[0] if views else None
        while widget is not None:
            if hasattr(widget, "runtime") and hasattr(widget, "scene"):
                return widget
            widget = widget.parentWidget()
        return None

    def _debug_runtime(self):
        canvas = self._debug_canvas()
        runtime = getattr(canvas, "runtime", None) if canvas else None
        return runtime if runtime and runtime.compiled else None

    def _set_debug_breakpoint(self, enabled: bool):
        runtime = self._debug_runtime()
        if runtime is not None:
            runtime.set_debug_breakpoint(self.block.id, enabled)
            self.update()

    def _debug_run_to_here(self):
        runtime = self._debug_runtime()
        if runtime is not None:
            runtime.debug_run_to_block(self.block.id)
            self.update()

    def _toggle_debug_pause(self):
        runtime = self._debug_runtime()
        if runtime is None:
            return
        if runtime.is_debug_paused:
            runtime.debug_resume()
        else:
            runtime.debug_pause()
        self.update()

    def _open_runtime_debugger(self):
        # A compiled SFC chart is one block to the FBD scheduler, but its
        # useful commissioning boundaries are chart steps, actions and
        # transitions.  Route its block-level context action to that existing
        # debugger instead of presenting only the outer FBD cursor.
        if self.block.block_type == "SFC_CHART":
            return self._open_sfc_debugger()

        import weakref

        canvas = self._debug_canvas()
        if canvas is None:
            return None
        existing = getattr(canvas, "_runtime_debugger_dialog", None)
        if existing is not None:
            try:
                existing.show()
                existing.raise_()
                existing.activateWindow()
                return existing
            except RuntimeError:
                canvas._runtime_debugger_dialog = None
        from ..dialogs.runtime_debugger import RuntimeDebuggerDialog
        parent = canvas.window()
        dialog = RuntimeDebuggerDialog(canvas, parent=parent)
        canvas._runtime_debugger_dialog = dialog
        canvas_ref = weakref.ref(canvas)

        def clear_dialog_reference():
            owner = canvas_ref()
            if owner is not None:
                owner._runtime_debugger_dialog = None

        dialog.destroyed.connect(clear_dialog_reference)
        from azeo_control_trainer.core.presentation.headless import is_headless
        if is_headless():
            dialog.setAttribute(Qt.WA_DontShowOnScreen, True)
        dialog.show()
        return dialog

    def _open_sfc_debugger(self):
        import weakref

        existing = getattr(self, "_sfc_debugger_dialog", None)
        if existing is not None:
            try:
                existing.show()
                existing.raise_()
                existing.activateWindow()
                return existing
            except RuntimeError:
                self._sfc_debugger_dialog = None
        from ..dialogs.sfc_debug_dialog import SfcDebugDialog
        canvas = self._debug_canvas()
        parent = canvas
        dialog = SfcDebugDialog(self.block, parent=parent)
        self._sfc_debugger_dialog = dialog
        owner_ref = weakref.ref(self)

        def forgotten(*_):
            owner = owner_ref()
            if owner is not None:
                owner._sfc_debugger_dialog = None

        dialog.destroyed.connect(forgotten)
        from azeo_control_trainer.core.presentation.headless import is_headless
        if is_headless():
            dialog.setAttribute(Qt.WA_DontShowOnScreen, True)
        dialog.show()
        return dialog

    def _open_diagnostics(self):
        from ..dialogs.block_diagnostics_dialog import BlockDiagnosticsDialog
        parent = self.scene().views()[0] if self.scene() and self.scene().views() else None
        dlg = BlockDiagnosticsDialog(self.block, parent=parent)
        dlg.show()

    def _open_find_references(self):
        from ..dialogs.find_references_dialog import FindReferencesDialog
        scene = self.scene()
        graph = getattr(scene, "_graph", None) if scene is not None else None
        if graph is None:
            return
        parent = scene.views()[0] if scene.views() else None
        dlg = FindReferencesDialog(self.block.id, graph, scene=scene, parent=parent)
        dlg.blockNavigateRequested.connect(self._navigate_to_block)
        dlg.show()

    def _open_documentation(self):
        from ..dialogs.block_documentation_dialog import open_block_documentation
        parent = self.scene().views()[0] if self.scene() and self.scene().views() else None
        open_block_documentation(self.block, parent=parent)

    def _build_watch_submenu(self, watch_menu):
        """Populate the Add-to-Watch submenu with every terminal on this block."""
        # Header: "Add all inputs / outputs" shortcuts
        act_all_in = watch_menu.addAction("All inputs")
        act_all_in.triggered.connect(
            lambda _checked=False: self._watch_add_all("IN"))
        act_all_out = watch_menu.addAction("All outputs")
        act_all_out.triggered.connect(
            lambda _checked=False: self._watch_add_all("OUT"))
        watch_menu.addSeparator()
        # Per-terminal entries
        for name, t in self.block.inputs.items():
            act = watch_menu.addAction(f"IN  ·  {name}  ({t.data_type.value})")
            act.triggered.connect(
                lambda _checked=False, n=name: self._watch_add(n, "IN"))
        if self.block.inputs and self.block.outputs:
            watch_menu.addSeparator()
        for name, t in self.block.outputs.items():
            act = watch_menu.addAction(f"OUT ·  {name}  ({t.data_type.value})")
            act.triggered.connect(
                lambda _checked=False, n=name: self._watch_add(n, "OUT"))

    def _watch_add(self, terminal: str, direction: str):
        from ..widgets.watch_panel import open_watch_panel
        panel = open_watch_panel()
        panel.add(self.block, terminal, direction)

    def _watch_add_all(self, direction: str):
        from ..widgets.watch_panel import open_watch_panel
        panel = open_watch_panel()
        terminals = self.block.inputs if direction == "IN" else self.block.outputs
        for name in terminals:
            panel.add(self.block, name, direction)

    # ─── Force / clipboard helpers ─────────────────────────────────
    def _open_force_dialog(self):
        from ..dialogs.force_value_dialog import ForceValueDialog
        dlg = ForceValueDialog(self.block, parent=self.scene().views()[0]
                                                  if self.scene() and self.scene().views()
                                                  else None)
        dlg.forcesChanged.connect(lambda _bid: self.update())
        dlg.exec()

    def _release_all_forces(self):
        if self.block.release_all_forces():
            self.update()

    # Clipboard commands. These drive the real scene/view API —
    # StrategyScene.copy_selected() / paste_clipboard(data, ...) with the
    # clipboard held on StrategyView. The previous duck-typed name guessing
    # looked for methods that do not exist (copy_selection, cut_selection), so
    # Cut/Copy silently did nothing, and it invoked paste_clipboard() with no
    # clipboard_data, which raised TypeError.
    def _clipboard_view(self):
        scene = self.scene()
        views = scene.views() if scene is not None else []
        return views[0] if views else None

    def _cmd_copy(self) -> bool:
        scene = self.scene()
        view = self._clipboard_view()
        if scene is None or view is None:
            return False
        data = scene.copy_selected()
        if not data:
            return False
        type(view)._clipboard = data
        return True

    def _cmd_cut(self):
        if self._cmd_copy():
            scene = self.scene()
            if scene is not None and hasattr(scene, "delete_selected"):
                scene.delete_selected("Cut selection")
            else:
                self._request_delete()

    def _cmd_paste(self):
        from PySide6.QtCore import QPointF
        scene = self.scene()
        view = self._clipboard_view()
        if scene is None or view is None:
            return
        data = getattr(type(view), "_clipboard", None)
        if not data:
            return
        scene.paste_clipboard(data, offset=QPointF(20, 20))

    def _cmd_duplicate(self):
        # Duplicate is "copy + paste with offset" against the same API.
        if self._cmd_copy():
            self._cmd_paste()

    def _invoke_scene_method(self, *names: str, fallback_clip: str | None = None):
        scene = self.scene()
        if scene is None:
            return
        for name in names + ((fallback_clip,) if fallback_clip else ()):
            fn = getattr(scene, name, None)
            if callable(fn):
                fn()
                return

    def _ungroup_self(self):
        """Replace this composite with its interior on the parent canvas."""
        scene = self.scene()
        if scene is None:
            return
        scene.ungroup_composite(self.block.id)

    def _collect_trend_tags(self) -> list:
        """Collect canonical historian paths relevant to this block.

        Process History configures controller points as
        ``MODULE/BLOCK/TERMINAL``. The former ``ctrl.BLOCK.PV`` aliases and
        field-tag guesses were not registered historian keys, so the command
        opened a chart but silently dropped every requested pen.
        """
        bt = self.block.block_type
        scene = self.scene()
        graph = getattr(scene, "graph", None) if scene is not None else None
        module = str(getattr(graph, "name", "") or "").strip("/")
        name = str(self.block.instance_name or self.block.id).strip("/")
        if not module or not name:
            return []
        if bt == "PID":
            terminals = ("PV", "SP", "OUT")
        elif bt in {"AI", "AO"}:
            terminals = ("OUT",)
        else:
            return []
        available = self.block.inputs.keys() | self.block.outputs.keys()
        return [f"{module}/{name}/{terminal}" for terminal in terminals
                if terminal in available]

    def _get_connected_blocks(self) -> list:
        """Return [(block_id, instance_name, direction)] for connected blocks."""
        scene = self.scene()
        if not scene or not hasattr(scene, '_graph'):
            return []
        result = []
        for wire in scene._graph.get_wires_for_block(self.block.id):
            if wire.src_block_id == self.block.id:
                other = scene._graph.blocks.get(wire.dst_block_id)
                if other:
                    result.append((other.id, other.instance_name, "output"))
            else:
                other = scene._graph.blocks.get(wire.src_block_id)
                if other:
                    result.append((other.id, other.instance_name, "input"))
        return result

    def _navigate_to_block(self, block_id: str):
        """Center the view on the target block and select it."""
        scene = self.scene()
        if not scene:
            return
        item = scene._block_items.get(block_id)
        if not item:
            return
        # Clear current selection and select target
        scene.clearSelection()
        item.setSelected(True)
        # Center view on target
        for view in scene.views():
            view.centerOn(item)
            break

    def _toggle_bypass(self):
        """Toggle bypass state and notify."""
        self._bypassed = not self._bypassed
        self.bypassToggled.emit(self.block.id, self._bypassed)
        self.update()

    def _set_pid_mode(self, mode: str):
        if hasattr(self.block, 'set_mode'):
            self.block.set_mode(mode)

    def _rename_block(self):
        from PySide6.QtWidgets import QInputDialog
        old_name = self.block.instance_name
        name, ok = QInputDialog.getText(
            None, "Rename Block", "Instance name:",
            text=old_name)
        if ok and name and name != old_name:
            self.block.instance_name = name
            scene = self.scene()
            if scene and hasattr(scene, '_undo_stack'):
                from ..undo import RenameBlockCommand
                scene._undo_stack.push(RenameBlockCommand(
                    scene, self.block.id, old_name, name))
            self.update()

    def _set_block_scan_rate(self):
        """Azeo 'Block Scan Rate' — execute this block every Nth scan.

        "The block scan rate indicates the number of times the module's
        algorithm is executed compared to the block ... If the block scan
        rate is 3, there is a 1:3 ratio and the block is executed every
        third scan."  A rate of 1 is the default and is not shown on the
        block.
        """
        from PySide6.QtWidgets import QInputDialog
        current = int(getattr(self.block, "scan_rate", 1))
        rate, ok = QInputDialog.getInt(
            None, "Block Scan Rate",
            f"Execute {self.block.instance_name} once every N module scans\n"
            f"(1 = every scan, the default):",
            current, 1, 1000, 1)
        if ok and rate != current:
            self.block.scan_rate = rate
            scene = self.scene()
            if scene is not None and hasattr(scene, "strategyModified"):
                scene.strategyModified.emit()
            self.update()

    def _reset_size(self):
        """Reset block to its default auto-calculated size."""
        self.prepareGeometryChange()
        self._min_width = self._natural_width()
        self._width = self._min_width
        self._height = self._min_height
        self.block._ui_width = None
        self.block._ui_height = None
        self._reposition_outputs()
        self.refresh_terminals()
        self.update()
        self._notify_wires_changed()
        self.blockResized.emit(self.block.id)

    def _request_delete(self):
        scene = self.scene()
        if scene and hasattr(scene, 'delete_block'):
            scene.delete_block(self.block.id)

    # ---------------------------------------------------------------- pin show/hide

    def _build_pins_menu(self, menu: QMenu):
        """Build a submenu with checkable entries for each terminal."""
        from PySide6.QtGui import QAction

        # Show All / Hide Unused shortcut actions
        act_show_all = menu.addAction("Show All")
        act_show_all.triggered.connect(self._show_all_pins)
        act_hide_unused = menu.addAction("Hide Unused")
        act_hide_unused.triggered.connect(self._hide_unused_pins)
        menu.addSeparator()

        # Input terminals
        if self.block.inputs:
            menu.addSection("Inputs")
            for name, terminal in self.block.inputs.items():
                act = QAction(name, menu)
                act.setCheckable(True)
                act.setChecked(not terminal.hidden)
                # Disable hiding if the terminal has a wire connected
                if terminal.connected:
                    act.setEnabled(False)
                    act.setToolTip("Cannot hide — wire connected")
                act.toggled.connect(
                    lambda checked, n=name: self._toggle_terminal("input", n, not checked))
                menu.addAction(act)

        # Output terminals
        if self.block.outputs:
            menu.addSection("Outputs")
            for name, terminal in self.block.outputs.items():
                act = QAction(name, menu)
                act.setCheckable(True)
                act.setChecked(not terminal.hidden)
                if terminal.connected:
                    act.setEnabled(False)
                    act.setToolTip("Cannot hide — wire connected")
                act.toggled.connect(
                    lambda checked, n=name: self._toggle_terminal("output", n, not checked))
                menu.addAction(act)

    def _toggle_terminal(self, direction: str, name: str, hidden: bool):
        """Show or hide one terminal as an undoable engineering edit."""
        terminals = (
            self.block.inputs if direction == "input"
            else self.block.outputs if direction == "output"
            else None
        )
        terminal = terminals.get(name) if terminals is not None else None
        if terminal is None:
            return
        # Don't hide connected terminals
        if hidden and terminal.connected:
            return
        visibility = self._pin_visibility_state()
        visibility[(direction, name)] = bool(hidden)
        action = "Hide" if hidden else "Show"
        self._set_pin_visibility(
            visibility, f"{action} {direction.title()} Pin {name}")

    def _show_all_pins(self):
        """Unhide all terminals in one undoable operation."""
        visibility = {
            key: False for key in self._pin_visibility_state()
        }
        return self._set_pin_visibility(visibility, "Show All Pins")

    def _hide_unused_pins(self):
        """Hide every unconnected terminal in one undoable operation."""
        visibility = {
            (direction, name): not terminal.connected
            for direction, terminals in (
                ("input", self.block.inputs),
                ("output", self.block.outputs),
            )
            for name, terminal in terminals.items()
        }
        return self._set_pin_visibility(visibility, "Hide Unused Pins")

    def _pin_visibility_state(self) -> dict[tuple[str, str], bool]:
        """Snapshot terminal hidden flags using stable direction/name keys."""
        return {
            (direction, name): bool(terminal.hidden)
            for direction, terminals in (
                ("input", self.block.inputs),
                ("output", self.block.outputs),
            )
            for name, terminal in terminals.items()
        }

    def _set_pin_visibility(
        self,
        visibility: dict[tuple[str, str], bool],
        description: str,
    ) -> bool:
        """Apply a terminal-visibility snapshot through the scene undo stack."""
        old_visibility = self._pin_visibility_state()
        new_visibility = dict(old_visibility)
        for key, hidden in visibility.items():
            direction, name = key
            terminals = (
                self.block.inputs if direction == "input"
                else self.block.outputs if direction == "output"
                else None
            )
            terminal = terminals.get(name) if terminals is not None else None
            if terminal is not None:
                new_visibility[key] = bool(hidden and not terminal.connected)
        if new_visibility == old_visibility:
            return False

        scene = self.scene()
        if scene is not None and hasattr(scene, "undo_stack"):
            from ..undo import ChangeTerminalVisibilityCommand
            scene.undo_stack.push(ChangeTerminalVisibilityCommand(
                scene,
                self.block.id,
                old_visibility,
                new_visibility,
                description,
            ))
        else:
            # A detached BlockItem has no engineering document or undo stack;
            # retain deterministic behaviour for previews and unit tests.
            for (direction, name), hidden in new_visibility.items():
                terminals = (
                    self.block.inputs if direction == "input"
                    else self.block.outputs
                )
                terminals[name].hidden = hidden
            self.rebuild_terminals()
        return True

    # ---------------------------------------------------------------- terminal visibility

    def rebuild_terminals(self):
        """Rebuild terminal items after visibility changes.

        Removes all existing terminal items, recalculates block size,
        recreates visible terminal items, and reconnects existing wires
        to the new TerminalItem objects.
        """
        # Remove old terminal items from scene
        for ti in list(self.terminal_items.values()):
            if ti.scene():
                ti.scene().removeItem(ti)
        self.terminal_items.clear()

        # Recalculate size
        n_inputs = sum(1 for t in self.block.inputs.values() if not t.hidden)
        n_outputs = sum(1 for t in self.block.outputs.values() if not t.hidden)
        n_terms = max(n_inputs, n_outputs, 1)
        self._base_min_height = (
            36.0 if self._is_special_palette_item else
            BLOCK_HEADER_HEIGHT + n_terms * BLOCK_PIN_SPACING
            + BLOCK_PIN_MARGIN * 2
        )
        # Reserve live-panel space regardless of mode (stable footprint).
        self._min_height = self._base_min_height + self._live_extra()
        self._min_width = self._natural_width()

        self.prepareGeometryChange()
        # Only shrink height if current height is based on old terminal count
        if self._height < self._min_height or not getattr(self.block, '_ui_height', None):
            self._height = self._min_height
        # Re-fit the width to the now-visible pin labels unless the user set one
        if not getattr(self.block, '_ui_width', None):
            self._width = self._min_width

        # Recreate terminal items
        self._create_terminals()

        # Reconnect wire references to the new TerminalItems
        scene = self.scene()
        if scene and hasattr(scene, '_graph') and hasattr(scene, '_wire_items'):
            for wire in scene._graph.get_wires_for_block(self.block.id):
                wire_item = scene._wire_items.get(wire.id)
                if not wire_item:
                    continue
                if wire.src_block_id == self.block.id:
                    new_ti = self.terminal_items.get(f"out:{wire.src_terminal}")
                    if new_ti:
                        wire_item.src_terminal = new_ti
                if wire.dst_block_id == self.block.id:
                    new_ti = self.terminal_items.get(f"in:{wire.dst_terminal}")
                    if new_ti:
                        wire_item.dst_terminal = new_ti

        self.update()
        self._notify_wires_changed()

    # ---------------------------------------------------------------- live mode

    def _live_extra(self) -> float:
        """Vertical space for the live-data overlay, by block type.

        Only claimed while the overlay is actually shown. Reserving it
        permanently kept a blank 52px band (28px on AI/AO) under every PID
        offline, which dominates the block once unused pins are hidden.
        """
        if not self._live_mode:
            return 0.0
        bt = self.block.block_type
        if bt == "PID":
            return _LIVE_PANEL_HEIGHT
        if bt in ("AI", "AO"):
            return _LIVE_PANEL_HEIGHT_IO
        return 0.0

    def set_live_mode(self, enabled: bool):
        """Toggle the Azeo-style live monitoring overlay.

        Grows/shrinks the block by the overlay height so an offline diagram
        carries no blank band. Only auto-sized blocks are re-fitted; a block the
        user resized by hand keeps its size.
        """
        if enabled == self._live_mode:
            return
        self._live_mode = enabled
        self.prepareGeometryChange()
        self._min_height = self._base_min_height + self._live_extra()
        if not getattr(self.block, '_ui_height', None):
            self._height = self._min_height
        elif self._height < self._min_height:
            self._height = self._min_height
        self._reposition_outputs()
        self.refresh_terminals()
        self.update()
        self._notify_wires_changed()

    def set_show_exec_order(self, show: bool):
        """Toggle execution order badge display."""
        if show != self._show_exec_order:
            self._show_exec_order = show
            self.update()

    def set_bypassed(self, bypassed: bool):
        """Toggle block bypass (skip execution)."""
        if bypassed != self._bypassed:
            self._bypassed = bypassed
            self.update()

    # ---------------------------------------------------------------- refresh

    def refresh_terminals(self):
        for ti in self.terminal_items.values():
            ti.refresh()

    # ---------------------------------------------------------------- search highlight

    def set_search_highlight(self, enabled: bool):
        """Toggle a gold drop-shadow highlight used by block search."""
        if enabled:
            effect = QGraphicsDropShadowEffect()
            effect.setColor(QColor(255, 215, 0, 200))
            effect.setBlurRadius(15)
            effect.setOffset(0, 0)
            self.setGraphicsEffect(effect)
        else:
            # Restore the default subtle shadow
            shadow = QGraphicsDropShadowEffect()
            shadow.setBlurRadius(8)
            shadow.setColor(QColor(0, 0, 0, 40))
            shadow.setOffset(2, 2)
            self.setGraphicsEffect(shadow)

    def refresh(self):
        self.refresh_terminals()
        self.update()
