"""Fixed engineering rulers around the graphics canvas.

The rulers belong to the view, not the display document. Their ticks follow
the view transform and scroll position while object and snap markers read
scene coordinates. This prevents authoring chrome from entering a published
operator display.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.menu_style import retain_menu

import math
import weakref

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QToolButton, QWidget,
)

from azeo_control_trainer.core.hmi.pvms.rendering.chrome import MODE_EDIT, WF


RULER_THICKNESS = 27


def _nice_major_step(pixels_per_unit: float, target_pixels: float = 80.0) \
        -> float:
    """Return a 1/2/5 engineering interval legible at the current zoom."""
    raw = target_pixels / max(abs(pixels_per_unit), 1e-9)
    exponent = math.floor(math.log10(max(raw, 1e-12)))
    scale = 10.0 ** exponent
    fraction = raw / scale
    if fraction <= 1.0:
        nice = 1.0
    elif fraction <= 2.0:
        nice = 2.0
    elif fraction <= 5.0:
        nice = 5.0
    else:
        nice = 10.0
    return nice * scale


def _number(value: float, step: float) -> str:
    decimals = max(0, min(4, -int(math.floor(math.log10(step))))) \
        if 0 < step < 1 else 0
    if abs(value) < step / 1000.0:
        value = 0.0
    return f"{value:.{decimals}f}"


class CanvasRuler(QWidget):
    """One fixed ruler whose coordinates are supplied by a graphics view."""

    def __init__(self, canvas, orientation: Qt.Orientation, parent=None):
        super().__init__(parent)
        self._canvas = weakref.proxy(canvas)
        self.orientation = orientation
        if orientation == Qt.Horizontal:
            self.setFixedHeight(RULER_THICKNESS)
            self.setMinimumWidth(40)
            self.setAccessibleName("Horizontal display ruler")
        else:
            self.setFixedWidth(RULER_THICKNESS)
            self.setMinimumHeight(40)
            self.setAccessibleName("Vertical display ruler")
        self.setToolTip(
            "Display coordinates. Drag into the canvas to create an "
            "alignment guide; double-click a guide marker to remove it. "
            "Blue marks show selected edges and centre.")
        self._dragging_guide = False

    @property
    def guide_axis(self) -> str:
        """A horizontal ruler creates an X (vertical) guide, and vice versa."""
        return "x" if self.orientation == Qt.Horizontal else "y"

    def _coordinate_at_global(self, global_position) -> float:
        viewport_position = self._canvas.viewport().mapFromGlobal(
            global_position)
        scene_position = self._canvas.mapToScene(viewport_position)
        return float(scene_position.x() if self.guide_axis == "x"
                     else scene_position.y())

    def _is_over_canvas(self, global_position) -> bool:
        position = self._canvas.viewport().mapFromGlobal(global_position)
        return self._canvas.viewport().rect().contains(position)

    def marker_values(self) -> tuple[list[float], list[float]]:
        """Selection and active-guide coordinates, exposed for inspection."""
        selected: list[float] = []
        scene = self._canvas.scene()
        bounds = QRectF()
        for item in scene.selectedItems() if scene is not None else ():
            # The resize/rotation handles deliberately widen boundingRect().
            # Ruler marks describe authored geometry, not its edit chrome.
            item_bounds = item.mapRectToScene(item.rect()) \
                if hasattr(item, "rect") else item.sceneBoundingRect()
            bounds = item_bounds if bounds.isNull() else bounds.united(item_bounds)
        if not bounds.isNull():
            if self.orientation == Qt.Horizontal:
                selected = [bounds.left(), bounds.center().x(), bounds.right()]
            else:
                selected = [bounds.top(), bounds.center().y(), bounds.bottom()]
        axis = self.guide_axis
        guides = [float(value) for kind, value in (
            tuple(getattr(self._canvas.studio, "authoring_guides", ()))
            + tuple(getattr(self._canvas, "_smart_guides", ()))
            + ((getattr(self._canvas, "_ruler_guide_preview"),)
               if getattr(self._canvas, "_ruler_guide_preview", None)
               is not None else ())) if kind == axis]
        return selected, guides

    def _view_position(self, coordinate: float) -> float:
        if self.orientation == Qt.Horizontal:
            return float(self._canvas.mapFromScene(coordinate, 0.0).x())
        return float(self._canvas.mapFromScene(0.0, coordinate).y())

    def _visible_scene_range(self) -> tuple[float, float]:
        if self.orientation == Qt.Horizontal:
            first = self._canvas.mapToScene(0, 0).x()
            last = self._canvas.mapToScene(self.width(), 0).x()
        else:
            first = self._canvas.mapToScene(0, 0).y()
            last = self._canvas.mapToScene(0, self.height()).y()
        return min(first, last), max(first, last)

    def paintEvent(self, event) -> None:            # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#F7F9FA"))
        painter.setPen(QPen(QColor(WF["bd"]), 1.0))
        if self.orientation == Qt.Horizontal:
            painter.drawLine(0, self.height() - 1,
                             self.width(), self.height() - 1)
            pixels_per_unit = abs(self._canvas.transform().m11())
        else:
            painter.drawLine(self.width() - 1, 0,
                             self.width() - 1, self.height())
            pixels_per_unit = abs(self._canvas.transform().m22())

        major = _nice_major_step(pixels_per_unit)
        minor = major / 5.0
        start, end = self._visible_scene_range()
        first_index = math.floor(start / minor)
        last_index = math.ceil(end / minor)
        font = QFont(self.font())
        if font.pointSizeF() <= 0:
            font.setPixelSize(9)
        else:
            font.setPointSizeF(max(6.0, min(font.pointSizeF(), 8.0)))
        painter.setFont(font)
        painter.setPen(QPen(QColor(WF["tx2"]), 1.0))
        for index in range(first_index, last_index + 1):
            value = index * minor
            position = self._view_position(value)
            is_major = index % 5 == 0
            length = 10 if is_major else 5
            if self.orientation == Qt.Horizontal:
                painter.drawLine(round(position), self.height() - 1,
                                 round(position), self.height() - 1 - length)
                if is_major:
                    painter.drawText(round(position) + 3, 10,
                                     _number(value, major))
            else:
                painter.drawLine(self.width() - 1, round(position),
                                 self.width() - 1 - length, round(position))
                if is_major:
                    painter.save()
                    painter.translate(9, round(position) - 3)
                    painter.rotate(-90)
                    painter.drawText(0, 0, _number(value, major))
                    painter.restore()

        selected, _guides = self.marker_values()
        axis = self.guide_axis
        manual = [float(value) for kind, value in getattr(
            self._canvas.studio, "authoring_guides", ()) if kind == axis]
        active = [float(value) for kind, value in getattr(
            self._canvas, "_smart_guides", ()) if kind == axis]
        preview = getattr(self._canvas, "_ruler_guide_preview", None)
        if preview is not None and preview[0] == axis:
            active.append(float(preview[1]))
        self._paint_markers(painter, selected, QColor(WF["sel_br"]), 4)
        self._paint_markers(painter, manual, QColor(WF["sel_br"]), 6)
        self._paint_markers(painter, active, QColor(WF["lapis_lt"]), 7)

    def mousePressEvent(self, event) -> None:      # noqa: N802
        if event.button() != Qt.LeftButton \
                or self._canvas.studio.mode != MODE_EDIT:
            super().mousePressEvent(event)
            return
        self._dragging_guide = True
        coordinate = self._coordinate_at_global(
            event.globalPosition().toPoint())
        self._canvas.preview_ruler_guide(self.guide_axis, coordinate)
        self._canvas.studio.update_geometry_readout(
            QPointF(coordinate, 0.0) if self.guide_axis == "x"
            else QPointF(0.0, coordinate))
        event.accept()

    def mouseMoveEvent(self, event) -> None:       # noqa: N802
        if not self._dragging_guide \
                or not event.buttons() & Qt.LeftButton:
            super().mouseMoveEvent(event)
            return
        coordinate = self._coordinate_at_global(
            event.globalPosition().toPoint())
        self._canvas.preview_ruler_guide(self.guide_axis, coordinate)
        self._canvas.studio.update_geometry_readout(
            QPointF(coordinate, 0.0) if self.guide_axis == "x"
            else QPointF(0.0, coordinate))
        event.accept()

    def mouseReleaseEvent(self, event) -> None:    # noqa: N802
        if event.button() != Qt.LeftButton or not self._dragging_guide:
            super().mouseReleaseEvent(event)
            return
        self._dragging_guide = False
        global_position = event.globalPosition().toPoint()
        coordinate = self._coordinate_at_global(global_position)
        self._canvas.preview_ruler_guide(self.guide_axis, None)
        if self._is_over_canvas(global_position):
            self._canvas.studio.add_authoring_guide(
                self.guide_axis, coordinate,
                bypass_snap=bool(event.modifiers() & Qt.AltModifier))
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            coordinate = self._coordinate_at_global(
                event.globalPosition().toPoint())
            scene_per_pixel = 1.0 / max(
                abs(self._canvas.transform().m11()), 0.1)
            if self._canvas.studio.remove_authoring_guide(
                    self.guide_axis, coordinate,
                    tolerance=7.0 * scene_per_pixel):
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:     # noqa: N802
        from azeo_control_trainer.core.presentation.menu_style import \
            studio_menu
        title = "HORIZONTAL RULER" if self.orientation == Qt.Horizontal \
            else "VERTICAL RULER"
        menu = studio_menu(title, "Non-published alignment guides")
        axis_name = "vertical" if self.guide_axis == "x" else "horizontal"
        clear_axis = menu.addAction(f"Clear {axis_name} guides")
        clear_axis.setEnabled(any(
            kind == self.guide_axis for kind, _value
            in self._canvas.studio.authoring_guides))
        clear_axis.triggered.connect(
            lambda: self._canvas.studio.clear_authoring_guides(
                self.guide_axis))
        clear_all = menu.addAction("Clear all guides")
        clear_all.setEnabled(bool(self._canvas.studio.authoring_guides))
        clear_all.triggered.connect(
            lambda: self._canvas.studio.clear_authoring_guides())
        retain_menu(self, menu, "_context_menu_instance")
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            menu.exec_transient(event.globalPos())

    def _paint_markers(self, painter: QPainter, values: list[float],
                       colour: QColor, size: int) -> None:
        painter.setPen(Qt.NoPen)
        painter.setBrush(colour)
        for value in values:
            position = self._view_position(value)
            if self.orientation == Qt.Horizontal:
                triangle = QPolygonF([
                    QPointF(position, self.height() - 1),
                    QPointF(position - size, self.height() - 1 - size),
                    QPointF(position + size, self.height() - 1 - size),
                ])
            else:
                triangle = QPolygonF([
                    QPointF(self.width() - 1, position),
                    QPointF(self.width() - 1 - size, position - size),
                    QPointF(self.width() - 1 - size, position + size),
                ])
            painter.drawPolygon(triangle)


class CanvasFrame(QWidget):
    """Canvas plus fixed rulers, without changing the canvas public API."""

    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self.setObjectName("canvas_frame")
        self.setStyleSheet(
            f"QWidget#canvas_frame {{ background: {WF['work']}; }}")
        self.canvas = canvas
        self.corner = QWidget(self)
        self.corner.setObjectName("ruler_corner")
        self.corner.setFixedSize(RULER_THICKNESS, RULER_THICKNESS)
        self.corner.setStyleSheet(
            "background:#F7F9FA; "
            f"border-right:1px solid {WF['bd']}; "
            f"border-bottom:1px solid {WF['bd']};")
        self.horizontal_ruler = CanvasRuler(canvas, Qt.Horizontal, self)
        self.vertical_ruler = CanvasRuler(canvas, Qt.Vertical, self)
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.tools = QWidget(self)
        self.tools.setObjectName("canvas_tools")
        self.tools.setStyleSheet(f"QWidget#canvas_tools {{ background: {WF['pane']}; }}")
        row = QHBoxLayout(self.tools)
        row.setContentsMargins(4, 3, 4, 3)
        row.setSpacing(3)
        self.tool_buttons = {}

        def add(key, label, tooltip, callback, checkable=False, icon=""):
            button = QToolButton(self.tools)
            button.setText(label)
            button.setToolTip(tooltip)
            button.setAccessibleName(tooltip)
            button.setCheckable(checkable)
            button.setFocusPolicy(Qt.NoFocus)
            if icon:
                from ..component_icons import element_icon
                from PySide6.QtCore import QSize
                button.setIcon(element_icon(icon))
                button.setIconSize(QSize(22, 22))
                button.setToolButtonStyle(Qt.ToolButtonIconOnly)
                button.setFixedWidth(34)
            button.setMinimumHeight(30)
            button.setStyleSheet(
                "QToolButton { font-family: Segoe UI; font-size: 9pt; padding: 3px 6px; "
                "border: 1px solid transparent; border-radius: 5px; color: #394759; } "
                f"QToolButton:hover {{ background: {WF['hover']}; }} "
                f"QToolButton:checked {{ background: {WF['sel']}; border-color: {WF['sel_br']}; }}")
            button.clicked.connect(callback)
            self.tool_buttons[key] = button
            row.addWidget(button)

        add("select", "Select", "Select and move objects (V)",
            lambda: canvas.studio.select_tool(), True, "select")
        add("pan", "Pan", "Pan (H), or hold Space and drag temporarily",
            lambda: canvas.studio.set_pan_mode(True), True, "pan")
        for kind, caption, callback in (
                ("rect", "Rectangle (R)", lambda: canvas.studio.arm_shape("rect")),
                ("ellipse", "Ellipse", lambda: canvas.studio.arm_shape("ellipse")),
                ("line", "Line (L)", lambda: canvas.studio.arm_line()),
                ("pipe", "Connect equipment (C)", lambda: canvas.studio.arm_connect()),
                ("text", "Text (T)", lambda: canvas.studio.arm_place("text"))):
            add(kind, caption, caption, callback, icon=kind)
        for key, label, attribute, tip in (
            ("repeat", "Repeat", "repeat_placement", "Keep placing or drawing until Escape"),
            ("snap", "Snap", "snap_enabled", "Snap to grid; hold Alt for free placement"),
            ("guides", "Guides", "smart_guides_enabled", "Align to objects and ruler guides"),
        ):
            add(key, label, tip,
                lambda checked, name=attribute: setattr(canvas.studio, name, checked), True)
        add("fit", "Fit", "Fit the display in the canvas",
            lambda: canvas.studio.fit_drawing())
        self.tool_hint = QLabel("", self.tools)
        self.tool_hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.tool_hint.setMinimumWidth(0)
        self.tool_hint.setStyleSheet("font-size: 9pt; color: #596779;")
        row.addWidget(self.tool_hint, 1)
        from PySide6.QtWidgets import QScrollArea
        self.tools_scroll = QScrollArea(self)
        self.tools_scroll.setWidget(self.tools)
        self.tools_scroll.setWidgetResizable(True)
        self.tools_scroll.setFrameShape(QScrollArea.NoFrame)
        self.tools_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tools_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.tools_scroll.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.tools_scroll.setFixedHeight(44)
        layout.addWidget(self.tools_scroll, 0, 0, 1, 2)
        layout.addWidget(self.corner, 1, 0)
        layout.addWidget(self.horizontal_ruler, 1, 1)
        layout.addWidget(self.vertical_ruler, 2, 0)
        layout.addWidget(canvas, 2, 1)
        layout.setRowStretch(2, 1)
        layout.setColumnStretch(1, 1)

        canvas.horizontalScrollBar().valueChanged.connect(self.update_rulers)
        canvas.verticalScrollBar().valueChanged.connect(self.update_rulers)
        canvas.scene().selectionChanged.connect(self.update_rulers)

    def update_tools(self) -> None:
        studio = self.canvas.studio
        self.tools.setVisible(studio.mode == MODE_EDIT)
        self.tools_scroll.setVisible(studio.mode == MODE_EDIT)
        for key, checked in (
            ("select", studio.interaction_mode == "select"),
            ("pan", studio.interaction_mode == "pan"),
            ("repeat", studio.repeat_placement),
            ("snap", studio.snap_enabled),
            ("guides", studio.smart_guides_enabled),
        ):
            self.tool_buttons[key].setChecked(checked)
        text = "Ctrl+drag: copy · Shift: straight · Space: pan"
        if studio.interaction_mode == "draw":
            text = "Click or drag to draw · Esc / right-click: stop"
        self.tool_hint.setText(text)
        self.tool_hint.setToolTip(text)

    def set_rulers_visible(self, visible: bool) -> None:
        changed = self.horizontal_ruler.isVisible() != bool(visible)
        self.corner.setVisible(visible)
        self.horizontal_ruler.setVisible(visible)
        self.vertical_ruler.setVisible(visible)
        if changed:
            self.canvas.schedule_auto_fit()

    def update_rulers(self, *_args) -> None:
        self.horizontal_ruler.update()
        self.vertical_ruler.update()
