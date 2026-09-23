"""Azeo module detail displays.

The module detail figures in ``PID_FB_Details.pdf`` are not enlarged
faceplates and not generic binding inspectors.  They share a dense, fixed
two-column shell: configuration groups on the left, alarm configuration and
diagnostic tabs on the right, with the module identity and return-to-
faceplate action across the top.

Only controls backed by a declared writable binding are interactive.  Alarm
enable/OOS/shelve marks remain read-only indicators until those states exist
in the control model; painting them as live check boxes would repeat the
inert-control defect that prompted this implementation.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSizePolicy, QTabWidget, QVBoxLayout, QWidget,
)

from azeo_control_trainer.core.strategy.model.terminal import Quality
from ..theme.roles import Role
from .faceplate_fb import ConditionTable
from .render_panels import SfcRunningChartPanel
from ..theme.widgets import set_stylesheet, set_surface_theme


def _result(bound, key):
    binding = bound.get(key) if bound else None
    return None if binding is None or isinstance(binding, tuple) \
        else binding.result


def _text(result, suffix: str = "") -> str:
    if result is None or result.quality is Quality.BAD \
            or result.value is None:
        return "#######"
    value = result.value
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        if not math.isfinite(value):
            return "+INF" if value > 0 else "-INF"
        return f"{value:.6g}{suffix}"
    return f"{value}{suffix}"


class DetailHeader(QWidget):
    """The common three-line identity, alarm mark and faceplate return."""

    faceplate_requested = Signal()

    def __init__(self, title: str, path: str, palette: dict, parent=None):
        super().__init__(parent)
        self._palette = palette
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)
        layout.addStretch(1)

        identity = QVBoxLayout()
        identity.setSpacing(0)
        self.area = QLabel(path)
        self.area.setAlignment(Qt.AlignCenter)
        self.area.setFont(QFont("Segoe UI", 7))
        module = path.split("/")[0] if path else title
        self.module = QLabel(module)
        self.module.setAlignment(Qt.AlignCenter)
        self.module.setFont(QFont("Segoe UI", 12, QFont.Bold))
        self.description = QLabel(title)
        self.description.setAlignment(Qt.AlignCenter)
        self.description.setFont(QFont("Segoe UI", 8))
        identity.addWidget(self.area)
        identity.addWidget(self.module)
        identity.addWidget(self.description)
        layout.addLayout(identity, 5)

        self.alarm = QLabel("×")
        self.alarm.setAlignment(Qt.AlignCenter)
        self.alarm.setFixedSize(18, 18)
        self.alarm.setToolTip("Highest-priority module alarm")
        self.alarm.hide()
        layout.addWidget(self.alarm, 0, Qt.AlignTop)
        self.faceplate = QPushButton("▥")
        self.faceplate.setFixedSize(30, 30)
        self.faceplate.setToolTip("Return to the module faceplate")
        self.faceplate.clicked.connect(self.faceplate_requested.emit)
        layout.addWidget(self.faceplate, 0, Qt.AlignTop)
        self.apply_theme(palette)

    def apply_theme(self, palette: dict) -> None:
        self._palette = palette
        set_stylesheet(self.module,
            f"color: {palette[Role.TEXT]}; font-weight: bold;")
        set_stylesheet(self.area, f"color: {palette[Role.TEXT_DIM]};")
        set_stylesheet(self.description,
            f"color: {palette[Role.TEXT_DIM]};")
        set_stylesheet(self.alarm,
            f"color: white; background: {palette[Role.ALARM_P1]};"
            "border-radius: 9px; font-weight: bold;")
        set_stylesheet(self.faceplate,
            f"background: {palette[Role.SURFACE_FIELD]};"
            f"border: 1px solid {palette[Role.LINE]};"
            "border-radius: 15px; color: #138A91; font-weight: bold;")

    def refresh(self, bound) -> None:
        primary = next((binding.result for binding in bound.values()
                        if not isinstance(binding, tuple)), None)
        alarm = primary is not None and (
            primary.alarm_active or primary.alarm_count > 0)
        self.alarm.setVisible(alarm)


class BoundFieldGroups(QWidget):
    """Dense left-column configuration groups from the detail figures."""

    write_requested = Signal(str, object)

    def __init__(self, groups, writable, palette: dict, parent=None):
        super().__init__(parent)
        self._palette = palette
        self._writable = set(writable)
        self._widgets: dict[str, QWidget] = {}
        self._row_widgets: dict[str, tuple[QWidget, ...]] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)
        for heading, rows in groups:
            title = QLabel(heading)
            title.setFont(QFont("Segoe UI", 8, QFont.Bold))
            root.addWidget(title)
            grid = QGridLayout()
            grid.setContentsMargins(5, 0, 0, 0)
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(0)
            for row, (key, caption, suffix) in enumerate(rows):
                label = QLabel(caption)
                label.setFont(QFont("Segoe UI", 7))
                grid.addWidget(label, row, 0)
                if key in self._writable:
                    editor = QLineEdit()
                    editor.setObjectName("detail_config_editor")
                    editor.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    editor.setFont(QFont("Consolas", 8))
                    editor.setFixedHeight(19)
                    editor.setToolTip(
                        "Editable online value — type a value and press Enter")
                    editor.editingFinished.connect(
                        lambda k=key, one=editor:
                        self.write_requested.emit(k, one.text()))
                    widget = editor
                else:
                    value = QLabel("#######")
                    value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    value.setFont(QFont("Consolas", 8))
                    widget = value
                widget.setMinimumWidth(72)
                widget.setSizePolicy(
                    QSizePolicy.MinimumExpanding, QSizePolicy.Fixed)
                grid.addWidget(widget, row, 1)
                row_widgets = [label, widget]
                if suffix:
                    unit = QLabel(suffix.strip())
                    unit.setFont(QFont("Segoe UI", 7))
                    grid.addWidget(unit, row, 2)
                    row_widgets.append(unit)
                self._widgets[key] = widget
                self._row_widgets[key] = tuple(row_widgets)
            root.addLayout(grid)
        root.addStretch(1)
        self.apply_theme(palette)

    def apply_theme(self, palette: dict) -> None:
        self._palette = palette
        set_stylesheet(self,
            f"QLabel {{ color: {palette[Role.TEXT]}; }}"
            f"QLineEdit#detail_config_editor {{"
            f" color: {palette[Role.TEXT]};"
            f" background: {palette[Role.SURFACE_FIELD]};"
            f" border: 1px solid {palette[Role.LINE]};"
            " border-radius: 1px; padding: 0 3px; }"
            f"QLineEdit#detail_config_editor:focus {{"
            f" border: 1px solid {palette[Role.ACTION]}; }}"
            f'QLineEdit#detail_config_editor[writeState="ok"] {{'
            f" border: 1px solid {palette[Role.ACTION]}; }}"
            f'QLineEdit#detail_config_editor[writeState="error"] {{'
            f" border: 1px solid {palette[Role.ALARM_P1]}; }}")

    def set_write_result(self, key: str, success: bool,
                         message: str = "") -> None:
        """Expose the result where the operator acted, not in a hidden log."""
        widget = self._widgets.get(key)
        if not isinstance(widget, QLineEdit):
            return
        widget.setProperty("writeState", "ok" if success else "error")
        widget.setToolTip(
            "Value written online" if success else
            (message or "Online write was refused"))
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def set_row_visible(self, key: str, visible: bool) -> None:
        for widget in self._row_widgets.get(key, ()):
            widget.setVisible(visible)

    def refresh(self, bound) -> None:
        for key, widget in self._widgets.items():
            if isinstance(widget, QLineEdit) and widget.hasFocus():
                continue
            text = _text(_result(bound, key))
            widget.setText(text)


class SimulationGroup(QWidget):
    write_requested = Signal(str, object)

    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        root = QGridLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setVerticalSpacing(1)
        title = QLabel("Simulate")
        title.setFont(QFont("Segoe UI", 8, QFont.Bold))
        root.addWidget(title, 0, 0, 1, 2)
        self.enabled = QCheckBox("Simulate")
        self.enabled.setFont(QFont("Segoe UI", 7))
        self.enabled.clicked.connect(
            lambda checked: self.write_requested.emit(
                "simulate.enabled", checked))
        root.addWidget(self.enabled, 1, 0)
        self.value = QLineEdit()
        self.value.setFrame(False)
        self.value.setAlignment(Qt.AlignRight)
        self.value.setFont(QFont("Consolas", 8))
        self.value.editingFinished.connect(
            lambda: self.write_requested.emit(
                "simulate.value", self.value.text()))
        root.addWidget(self.value, 1, 1)
        root.addWidget(QLabel("Field Value"), 2, 0)
        self.field = QLabel("#######")
        self.field.setAlignment(Qt.AlignRight)
        self.field.setFont(QFont("Consolas", 8))
        root.addWidget(self.field, 2, 1)
        self.apply_theme(palette)

    def apply_theme(self, palette: dict) -> None:
        set_stylesheet(self,
            f"color: {palette[Role.TEXT]}; background: transparent;")

    def refresh(self, bound) -> None:
        enabled = _result(bound, "simulate.enabled")
        self.enabled.blockSignals(True)
        self.enabled.setChecked(bool(enabled.value) if enabled else False)
        self.enabled.blockSignals(False)
        if not self.value.hasFocus():
            self.value.setText(_text(_result(bound, "simulate.value")))
        self.field.setText(_text(_result(bound, "field.value")))


class AlarmMatrix(QWidget):
    """Alarm configuration rendered as indicators, never fake controls."""

    def __init__(self, rows, palette: dict, parent=None):
        super().__init__(parent)
        self._palette = palette
        self._rows = rows
        root = QGridLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setHorizontalSpacing(5)
        root.setVerticalSpacing(1)
        title = QLabel("Alarms")
        title.setFont(QFont("Segoe UI", 8, QFont.Bold))
        root.addWidget(title, 0, 0)
        for column, text in enumerate(("Priority", "Enab", "OOS",
                                       "Shlv", "Help"), 1):
            label = QLabel(text)
            label.setFont(QFont("Segoe UI", 7))
            # Qt otherwise treats every full heading and alarm caption as a
            # hard minimum.  In the measured 560 px detail shell those hints
            # add up to nearly 400 px and squeeze the adjacent limits bank.
            # Azeo's grid columns are fixed and clip long configured names,
            # so let the grid allocate the available column width instead.
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            root.addWidget(label, 0, column)
        self.labels = {}
        self.enabled = {}
        for row, (key, caption) in enumerate(rows, 1):
            label = QLabel(caption)
            label.setFont(QFont("Segoe UI", 7))
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            label.setToolTip(caption)
            root.addWidget(label, row, 0)
            priority = QLabel("-")
            priority.setFont(QFont("Consolas", 7))
            root.addWidget(priority, row, 1)
            self.labels[key] = (label, priority)
            indicator = QCheckBox()
            indicator.setEnabled(False)
            root.addWidget(indicator, row, 2, Qt.AlignCenter)
            self.enabled[key] = indicator
            for column in (3, 4):
                mark = QCheckBox()
                mark.setEnabled(False)
                root.addWidget(mark, row, column, Qt.AlignCenter)
            help_mark = QLabel("?")
            help_mark.setAlignment(Qt.AlignCenter)
            help_mark.setFixedSize(16, 16)
            help_mark.setToolTip(f"Alarm help: {caption}")
            root.addWidget(help_mark, row, 5, Qt.AlignCenter)
        self.apply_theme(palette)

    def apply_theme(self, palette: dict) -> None:
        self._palette = palette
        set_stylesheet(self,
            f"color: {palette[Role.TEXT]}; background: transparent;")
        for label, _priority in self.labels.values():
            set_stylesheet(label, f"color: {palette[Role.TEXT]};")

    def refresh(self, bound) -> None:
        primary = _result(bound, "pv.value")
        for key, (label, priority) in self.labels.items():
            limit = _result(bound, key)
            enabled = limit is not None and limit.value is not None
            if enabled and isinstance(limit.value, float):
                enabled = math.isfinite(limit.value)
            self.enabled[key].setChecked(enabled)
            active = bool(primary and primary.alarm_active
                          and primary.alarm_condition.lower()
                          in label.text().lower())
            label.setStyleSheet(
                f"color: {self._palette[Role.ALARM_P1_TEXT]};"
                "font-weight: bold;" if active else
                f"color: {self._palette[Role.TEXT]};")
            priority.setText(
                str(primary.alarm_priority)
                if active and primary.alarm_priority else "-")


class DiagnosticsTabs(QWidget):
    """MERROR/MSTATUS/BLOCKERR tabs driven by available quality state."""

    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self._palette = palette
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)
        title = QLabel("Diagnostics")
        title.setFont(QFont("Segoe UI", 8, QFont.Bold))
        root.addWidget(title)
        self.tabs = QTabWidget()
        self.tabs.setMinimumWidth(260)
        self.messages = {}
        for name in ("MERROR", "MSTATUS", "BLOCKERR"):
            page = QWidget()
            page_lay = QVBoxLayout(page)
            page_lay.setContentsMargins(8, 6, 8, 6)
            message = QLabel("Module OK")
            message.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            message.setWordWrap(True)
            message.setFont(QFont("Segoe UI", 8))
            page_lay.addWidget(message)
            page_lay.addStretch(1)
            self.tabs.addTab(page, name)
            self.messages[name] = message
        root.addWidget(self.tabs, 1)
        self.apply_theme(palette)

    def apply_theme(self, palette: dict) -> None:
        self._palette = palette
        set_stylesheet(self,
            f"QTabWidget::pane {{ border: 1px solid {palette[Role.LINE]}; }}"
            f"QTabBar::tab {{ background: {palette[Role.SURFACE_FIELD]};"
            f" color: {palette[Role.TEXT]};"
            f" border: 1px solid {palette[Role.LINE]}; padding: 3px 10px; }}")

    def refresh(self, bound) -> None:
        direct = [one.result for one in bound.values()
                  if not isinstance(one, tuple)]
        bad = [one for one in direct if one.quality is Quality.BAD]
        forced = [one for one in direct if one.forced]
        stopped = any(one.module_running is False for one in direct)
        alarms = [one for one in direct if one.alarm_active]
        errors = []
        if stopped:
            errors.append("Module Not Running")
        if bad:
            errors.append("I/O or Parameter Bad")
        if alarms:
            errors.append("Alarm Processing Active")
        status = ["Function Block Forced"] if forced else ["Module OK"]
        self.messages["MERROR"].setText(
            "\n".join(errors) if errors else "Module OK")
        self.messages["MSTATUS"].setText("\n".join(status))
        self.messages["BLOCKERR"].setText(
            "Configuration or Binding Error" if bad else "Module OK")
        for name, message in self.messages.items():
            active = message.text() != "Module OK"
            message.setStyleSheet(
                f"color: {self._palette[Role.ALARM_P1_TEXT]};"
                if active and name != "MSTATUS" else
                f"color: {self._palette[Role.TEXT]};")


class ModuleDetailBase(QWidget):
    action_requested = Signal(str)
    write_requested = Signal(str, object)

    def __init__(self, title: str, path: str, palette: dict, parent=None):
        super().__init__(parent)
        self._palette = palette
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(4, 2, 4, 4)
        self.root.setSpacing(3)
        self.header = DetailHeader(title, path, palette)
        self.header.faceplate_requested.connect(
            lambda: self.action_requested.emit("faceplate"))
        self.root.addWidget(self.header)

    def apply_theme(self, palette: dict) -> None:
        self._palette = palette
        self.header.apply_theme(palette)
        pending = self.findChildren(QWidget, options=Qt.FindDirectChildrenOnly)
        while pending:
            child = pending.pop()
            if child is self.header:
                continue
            applier = getattr(child, "apply_theme", None)
            if applier is not None:
                applier(palette)
            else:
                # A themed container owns its descendants' propagation. Calling
                # both it and those descendants repolished the same fields twice.
                pending.extend(child.findChildren(QWidget, options=Qt.FindDirectChildrenOnly))

    def set_write_result(self, key: str, success: bool,
                         message: str = "") -> None:
        for fields in self.findChildren(BoundFieldGroups):
            fields.set_write_result(key, success, message)

    def refresh(self, bound) -> None:
        self.header.refresh(bound)


PID_LIMITS = (
    ("limits.hi_hi", "Hi Hi Lim", ""),
    ("limits.hi", "Hi Lim", ""),
    ("limits.dev_hi", "Dev Hi Lim", ""),
    ("limits.dev_lo", "Dev Lo Lim", ""),
    ("limits.lo", "Lo Lim", ""),
    ("limits.lo_lo", "Lo Lo Lim", ""),
    ("limits.out_hi", "Out Hi Lim", ""),
    ("limits.out_lo", "Out Lo Lim", ""),
    ("limits.arw_hi", "ARW Hi Lim", ""),
    ("limits.arw_lo", "ARW Lo Lim", ""),
    ("limits.sp_hi", "SP Hi Lim", ""),
    ("limits.sp_lo", "SP Lo Lim", ""),
    ("limits.hys", "Alarm Hysteresis", " %"),
)
PID_TUNING = (
    ("tuning.gain", "Gain", ""),
    ("tuning.reset", "Reset", " s"),
    ("tuning.rate", "Rate", " s"),
    ("tuning.pv_filter", "PV Filter TC", " s"),
    ("tuning.sp_filter", "SP Filter TC", " s"),
    ("tuning.sp_rate_dn", "SP Rate DN", " EU/s"),
    ("tuning.sp_rate_up", "SP Rate UP", " EU/s"),
    ("tuning.structure", "Structure", ""),
    ("tuning.beta", "Beta", ""),
    ("tuning.gamma", "Gamma", ""),
    ("tuning.ideadband", "IDeadband", ""),
    ("tuning.bias", "Bias", ""),
    ("tuning.ff_gain", "FF Gain", ""),
    ("tuning.adaptive", "Adaptive Mode", ""),
)


class _FaceplateReturnButton(QPushButton):
    """Paint the circular Loop_dt return icon without font-dependent glyphs."""

    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self._palette = palette
        self.setFixedSize(34, 34)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Return to the module faceplate")
        self.setStyleSheet("border: none; background: transparent;")

    def apply_theme(self, palette: dict) -> None:
        self._palette = palette
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        inset = 2.5 if not self.isDown() else 3.5
        ring = QRectF(inset, inset, self.width() - 2 * inset,
                      self.height() - 2 * inset)
        p.setPen(QPen(QColor(self._palette[Role.LINE]), 1.0))
        p.setBrush(QColor(self._palette[Role.SURFACE_FIELD]))
        p.drawEllipse(ring)
        bar_y = ring.top() + 7
        bar_h = ring.height() - 14
        bar_w = 5
        p.setPen(Qt.NoPen)
        for x, role in (
                (ring.left() + 7, Role.BAR_PV),
                (ring.left() + 13, Role.ACTION),
                (ring.left() + 19, Role.ALARM_P3)):
            p.setBrush(QColor(self._palette[role]))
            p.drawRect(QRectF(x, bar_y, bar_w, bar_h))


class _PIDDetailHeader(QWidget):
    """Measured 592 px Loop_dt identity band and working return action."""

    faceplate_requested = Signal()

    def __init__(self, title: str, path: str, palette: dict, parent=None):
        super().__init__(parent)
        self._fallback_description = title.replace(" Detail", " Controller")
        module, separator, _block = path.partition("/")
        module = module if separator else (path or title)

        self.area = QLabel(path, self)
        self.area.setAlignment(Qt.AlignCenter)
        self.area.setFont(QFont("Arial", 8))
        self.area.setGeometry(135, 7, 315, 17)
        self.module = QLabel(module, self)
        self.module.setAlignment(Qt.AlignCenter)
        self.module.setFont(QFont("Arial", 13, QFont.Bold))
        self.module.setGeometry(115, 24, 355, 25)
        self.description = QLabel(self._fallback_description, self)
        self.description.setAlignment(Qt.AlignCenter)
        self.description.setFont(QFont("Arial", 8))
        self.description.setGeometry(120, 49, 350, 18)

        self.alarm = QLabel("x", self)
        self.alarm.setAlignment(Qt.AlignCenter)
        self.alarm.setFont(QFont("Arial", 8, QFont.Bold))
        self.alarm.setFixedSize(16, 16)
        self.alarm.move(500, 38)
        self.alarm.setToolTip("Highest-priority module alarm")
        self.alarm.hide()
        self.faceplate = _FaceplateReturnButton(palette, self)
        self.faceplate.move(535, 29)
        self.faceplate.clicked.connect(self.faceplate_requested.emit)
        self.apply_theme(palette)

    def apply_theme(self, palette: dict) -> None:
        self._palette = palette
        set_stylesheet(self.area, f"color: {palette[Role.TEXT_DIM]};")
        set_stylesheet(self.module,
            f"color: {palette[Role.TEXT]}; font-weight: bold;")
        set_stylesheet(self.description, f"color: {palette[Role.TEXT_DIM]};")
        set_stylesheet(self.alarm,
            f"color: {palette[Role.SURFACE_FIELD]};"
            f"background: {palette[Role.ALARM_P1]};"
            "border-radius: 8px; font-weight: bold;")
        self.faceplate.apply_theme(palette)

    def refresh(self, bound) -> None:
        primary = _result(bound, "pv.value")
        alarm = primary is not None and (
            primary.alarm_active or primary.alarm_count > 0)
        self.alarm.setVisible(alarm)
        description = _result(bound, "module.description")
        text = str(description.value).strip() \
            if description is not None and description.value is not None \
            else ""
        self.description.setText(text or self._fallback_description)


class _PIDFieldBank(QWidget):
    """Flat online fields at the coordinates measured from Loop_dt."""

    write_requested = Signal(str, object)

    def __init__(self, rows, palette: dict, *, heading: str,
                 positions: dict | None = None, parent=None):
        super().__init__(parent)
        self._palette = palette
        self._widgets: dict[str, QWidget] = {}
        self._row_widgets: dict[str, tuple[QWidget, ...]] = {}
        self.heading = QLabel(heading, self)
        self.heading.setFont(QFont("Arial", 8, QFont.Bold))
        self.heading.setGeometry(0, 0, 100, 18)
        positions = positions or {}
        for index, (key, caption, suffix) in enumerate(rows):
            geometry = positions.get(key, (5, 108, 76, 23 + index * 17, 186))
            label_x, value_x, value_w, y, suffix_x = geometry
            label = QLabel(caption, self)
            label.setFont(QFont("Arial", 8))
            label.setGeometry(
                label_x, y, max(25, value_x - label_x - 3), 17)
            editor = QLineEdit("#######", self)
            editor.setObjectName("detail_config_editor")
            editor.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            editor.setFont(QFont("Consolas", 8))
            editor.setGeometry(value_x, y, value_w, 17)
            editor.setToolTip(
                "Editable online value — type a value and press Enter")
            editor.editingFinished.connect(
                lambda k=key, one=editor: self._commit(k, one))
            row_widgets: list[QWidget] = [label, editor]
            if suffix:
                unit = QLabel(suffix.strip(), self)
                unit.setFont(QFont("Arial", 8))
                unit.setGeometry(suffix_x, y, max(25, self.width() - suffix_x), 17)
                row_widgets.append(unit)
            self._widgets[key] = editor
            self._row_widgets[key] = tuple(row_widgets)
        self.apply_theme(palette)

    def _commit(self, key: str, editor: QLineEdit) -> None:
        text = editor.text().strip()
        if text and "#" not in text:
            self.write_requested.emit(key, text)

    def apply_theme(self, palette: dict) -> None:
        self._palette = palette
        set_stylesheet(self,
            f"QLabel {{ color: {palette[Role.TEXT]}; }}"
            f"QLineEdit#detail_config_editor {{"
            f" color: {palette[Role.TEXT]}; background: transparent;"
            " border: 1px solid transparent; padding: 0 2px; }"
            f"QLineEdit#detail_config_editor:hover {{"
            f" background: {palette[Role.SURFACE_FIELD]};"
            f" border-color: {palette[Role.LINE_SOFT]}; }}"
            f"QLineEdit#detail_config_editor:focus {{"
            f" background: {palette[Role.SURFACE_FIELD]};"
            f" border-color: {palette[Role.ACTION]}; }}"
            f'QLineEdit#detail_config_editor[writeState="ok"] {{'
            f" border-color: {palette[Role.ACTION]}; }}"
            f'QLineEdit#detail_config_editor[writeState="error"] {{'
            f" border-color: {palette[Role.ALARM_P1]}; }}")

    def set_write_result(self, key: str, success: bool,
                         message: str = "") -> None:
        widget = self._widgets.get(key)
        if not isinstance(widget, QLineEdit):
            return
        widget.setProperty("writeState", "ok" if success else "error")
        widget.setToolTip(
            "Value written online" if success else
            (message or "Online write was refused"))
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def set_row_visible(self, key: str, visible: bool) -> None:
        for widget in self._row_widgets.get(key, ()):
            widget.setVisible(visible)

    def refresh(self, bound) -> None:
        for key, widget in self._widgets.items():
            if widget.hasFocus():
                continue
            widget.setText(_text(_result(bound, key)))


class _PIDLimitsBank(_PIDFieldBank):
    """The thirteen online limit entries and their real focus control."""

    def __init__(self, palette: dict, parent=None):
        super().__init__(PID_LIMITS, palette, heading="Limits", parent=parent)
        self.more = QPushButton("...", self)
        self.more.setGeometry(58, 0, 25, 18)
        self.more.setToolTip("Focus the first configurable alarm limit")
        self.more.clicked.connect(
            lambda: self._widgets["limits.hi_hi"].setFocus())
        self.apply_theme(palette)

    def apply_theme(self, palette: dict) -> None:
        super().apply_theme(palette)
        if hasattr(self, "more"):
            set_stylesheet(self.more,
                f"color: {palette[Role.TEXT]};"
                f"background: {palette[Role.SURFACE_FIELD]};"
                f"border: 1px solid {palette[Role.LINE_SOFT]};"
                "border-radius: 2px; padding: 0;")


class _PIDSimulationGroup(QWidget):
    write_requested = Signal(str, object)

    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self.heading = QLabel("Simulate", self)
        self.heading.setFont(QFont("Arial", 8, QFont.Bold))
        self.heading.setGeometry(0, 0, 100, 18)
        self.enabled = QCheckBox("Simulate", self)
        self.enabled.setFont(QFont("Arial", 8))
        self.enabled.setGeometry(4, 21, 94, 18)
        self.enabled.clicked.connect(
            lambda checked: self.write_requested.emit(
                "simulate.enabled", checked))
        self.value = QLineEdit("#######", self)
        self.value.setObjectName("detail_config_editor")
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.value.setFont(QFont("Consolas", 8))
        self.value.setGeometry(108, 21, 68, 17)
        self.value.setToolTip(
            "Editable online value — type a value and press Enter")
        self.value.editingFinished.connect(self._commit_value)
        self.value_unit = QLabel("", self)
        self.value_unit.setFont(QFont("Arial", 8))
        self.value_unit.setGeometry(178, 21, 27, 17)
        field_label = QLabel("Field Value", self)
        field_label.setFont(QFont("Arial", 8))
        field_label.setGeometry(5, 42, 90, 17)
        self.field = QLabel("#######", self)
        self.field.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.field.setFont(QFont("Consolas", 8))
        self.field.setGeometry(108, 42, 68, 17)
        self.field_unit = QLabel("", self)
        self.field_unit.setFont(QFont("Arial", 8))
        self.field_unit.setGeometry(178, 42, 27, 17)
        self.apply_theme(palette)

    def _commit_value(self) -> None:
        text = self.value.text().strip()
        if text and "#" not in text:
            self.write_requested.emit("simulate.value", text)

    def apply_theme(self, palette: dict) -> None:
        self._palette = palette
        set_stylesheet(self,
            f"QLabel, QCheckBox {{ color: {palette[Role.TEXT]}; }}"
            f"QLineEdit#detail_config_editor {{"
            f" color: {palette[Role.TEXT]}; background: transparent;"
            " border: 1px solid transparent; padding: 0 2px; }"
            f"QLineEdit#detail_config_editor:hover {{"
            f" background: {palette[Role.SURFACE_FIELD]};"
            f" border-color: {palette[Role.LINE_SOFT]}; }}"
            f"QLineEdit#detail_config_editor:focus {{"
            f" background: {palette[Role.SURFACE_FIELD]};"
            f" border-color: {palette[Role.ACTION]}; }}")

    def set_write_result(self, key: str, success: bool,
                         message: str = "") -> None:
        if key != "simulate.value":
            return
        self.value.setToolTip(
            "Value written online" if success else
            (message or "Online write was refused"))

    def refresh(self, bound) -> None:
        enabled = _result(bound, "simulate.enabled")
        self.enabled.blockSignals(True)
        self.enabled.setChecked(bool(enabled.value) if enabled else False)
        self.enabled.blockSignals(False)
        if not self.value.hasFocus():
            self.value.setText(_text(_result(bound, "simulate.value")))
        self.field.setText(_text(_result(bound, "field.value")))
        units = _result(bound, "pv.units")
        unit_text = str(units.value) if units and units.value is not None else ""
        self.value_unit.setText(unit_text)
        self.field_unit.setText(unit_text)


class _PIDTuningBank(_PIDFieldBank):
    """Loop_dt tuning geometry, including conditional Beta/Gamma/Bias rows."""

    _POSITIONS = {
        "tuning.gain": (5, 108, 76, 23, 186),
        "tuning.reset": (5, 108, 76, 43, 186),
        "tuning.rate": (5, 108, 76, 63, 186),
        "tuning.pv_filter": (5, 108, 76, 83, 186),
        "tuning.sp_filter": (5, 108, 76, 103, 186),
        "tuning.sp_rate_dn": (5, 108, 76, 123, 186),
        "tuning.sp_rate_up": (5, 108, 76, 143, 186),
        "tuning.structure": (5, 108, 260, 163, 372),
        "tuning.beta": (378, 412, 48, 163, 462),
        "tuning.gamma": (466, 513, 45, 163, 560),
        "tuning.ideadband": (5, 108, 76, 183, 186),
        "tuning.bias": (5, 108, 76, 183, 186),
        "tuning.ff_gain": (5, 108, 76, 203, 186),
        "tuning.adaptive": (5, 108, 76, 223, 186),
    }

    def __init__(self, palette: dict, parent=None):
        super().__init__(PID_TUNING, palette, heading="Tuning",
                         positions=self._POSITIONS, parent=parent)
        self._widgets["tuning.structure"].setAlignment(
            Qt.AlignLeft | Qt.AlignVCenter)
        self.adaptive_state = QLabel("", self)
        self.adaptive_state.setFont(QFont("Arial", 8))
        self.adaptive_state.setGeometry(72, 0, 100, 18)
        self.apply_theme(palette)

    def apply_theme(self, palette: dict) -> None:
        super().apply_theme(palette)
        if hasattr(self, "adaptive_state"):
            set_stylesheet(self.adaptive_state,
                f"color: {palette[Role.TEXT_DIM]};")

    def refresh(self, bound) -> None:
        super().refresh(bound)
        adaptive = _result(bound, "tuning.adaptive")
        self.adaptive_state.setText(
            "ADAPT" if adaptive is not None and adaptive.value else "")


class _ReadOnlyCheck(QWidget):
    """Small Azeo check indicator; native disabled boxes render as arcs."""

    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self._palette = palette
        self._checked = False
        self.setFixedSize(16, 16)

    def setChecked(self, checked: bool) -> None:  # noqa: N802
        checked = bool(checked)
        if checked != self._checked:
            self._checked = checked
            self.update()

    def isChecked(self) -> bool:  # noqa: N802
        return self._checked

    def apply_theme(self, palette: dict) -> None:
        self._palette = palette
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(self._palette[Role.LINE]), 1.0))
        p.setBrush(QColor(self._palette[Role.SURFACE_FIELD]))
        p.drawRect(QRectF(3.5, 3.5, 9, 9))
        if self._checked:
            p.setPen(QPen(QColor(self._palette[Role.TEXT_DIM]), 1.2))
            p.drawLine(5, 8, 7, 10)
            p.drawLine(7, 10, 11, 5)


class _PIDAlarmMatrix(QWidget):
    """The PDF alarm matrix, driven only by states the PID currently owns."""

    _ROWS = (
        ("limits.hi_hi", "Hi Hi"),
        ("limits.hi", "Hi"),
        ("limits.dev_hi", "Dev Hi"),
        ("limits.dev_lo", "Dev Lo"),
        ("limits.lo", "Lo"),
        ("limits.lo_lo", "Lo Lo"),
        ("pv.value", "PV Bad"),
    )
    _HEADINGS = (
        ("Priority", 110, 92), ("Enab", 217, 38),
        ("OOS", 254, 34), ("Shlv", 287, 36), ("Help", 322, 30),
    )
    _PRIORITY_GEOMETRY = (108, 94)
    _CHECK_X = (228, 263, 296)
    _HELP_X = 327

    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self._palette = palette
        heading = QLabel("Alarms", self)
        heading.setFont(QFont("Arial", 8, QFont.Bold))
        heading.setGeometry(0, 0, 95, 18)
        for text, x, width in self._HEADINGS:
            label = QLabel(text, self)
            label.setAlignment(Qt.AlignCenter)
            label.setFont(QFont("Arial", 8))
            label.setGeometry(x, 0, width, 18)
        self.labels = {}
        self.enabled = {}
        self.oos = {}
        self.shelved = {}
        self.help = {}
        for index, (key, caption) in enumerate(self._ROWS):
            y = 22 + index * 18
            label = QLabel(caption, self)
            label.setFont(QFont("Arial", 8))
            label.setGeometry(6, y, 100, 17)
            priority = QLabel("-", self)
            priority.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            priority.setFont(QFont("Consolas", 8))
            priority_x, priority_width = self._PRIORITY_GEOMETRY
            priority.setGeometry(priority_x, y, priority_width, 17)
            self.labels[key] = (label, priority)
            for store, x in zip(
                    (self.enabled, self.oos, self.shelved), self._CHECK_X):
                mark = _ReadOnlyCheck(palette, self)
                mark.move(x, y)
                store[key] = mark
            help_mark = QLabel("?", self)
            help_mark.setAlignment(Qt.AlignCenter)
            help_mark.setFont(QFont("Arial", 8, QFont.Bold))
            help_mark.setGeometry(self._HELP_X, y, 17, 17)
            help_mark.setToolTip(f"Alarm help: {caption}")
            self.help[key] = help_mark
        adjust_y = 22 + len(self._ROWS) * 18
        adjust = QLabel("Priority Adj", self)
        adjust.setFont(QFont("Arial", 8))
        adjust.setGeometry(6, adjust_y, 100, 17)
        self.priority_adjust = QLabel("0", self)
        self.priority_adjust.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.priority_adjust.setFont(QFont("Consolas", 8))
        self.priority_adjust.setGeometry(108, adjust_y, 45, 17)
        self.apply_theme(palette)

    def apply_theme(self, palette: dict) -> None:
        self._palette = palette
        set_stylesheet(self,
            f"QLabel, QCheckBox {{ color: {palette[Role.TEXT]};"
            " background: transparent; }")
        for help_mark in self.help.values():
            set_stylesheet(help_mark,
                f"color: {palette[Role.SURFACE_FIELD]};"
                f"background: {palette[Role.ACTION_DEEP]};"
                "border-radius: 8px; font-weight: bold;")
        for marks in (self.enabled, self.oos, self.shelved):
            for mark in marks.values():
                mark.apply_theme(palette)

    def refresh(self, bound) -> None:
        primary = _result(bound, "pv.value")
        condition = str(
            primary.alarm_condition if primary is not None else "").lower()
        for key, (label, priority) in self.labels.items():
            limit = _result(bound, key)
            enabled = limit is not None and limit.value is not None
            if enabled and isinstance(limit.value, float):
                enabled = math.isfinite(limit.value)
            self.enabled[key].setChecked(enabled)
            active = bool(primary and primary.alarm_active
                          and label.text().lower() in condition)
            label.setStyleSheet(
                f"color: {self._palette[Role.ALARM_P1_TEXT]};"
                "font-weight: bold;" if active else
                f"color: {self._palette[Role.TEXT]};")
            priority.setText(
                str(primary.alarm_priority)
                if active and primary.alarm_priority else "-")


class _PIDDiagnosticsTabs(QWidget):
    """MERROR/MSTATUS/BLOCKERR at the reference size and tab order."""

    def __init__(self, palette: dict, parent=None, *, width: int = 350,
                 height: int = 300):
        super().__init__(parent)
        self._palette = palette
        title = QLabel("Diagnostics", self)
        title.setFont(QFont("Arial", 8, QFont.Bold))
        title.setGeometry(0, 0, 120, 18)
        self.tabs = QTabWidget(self)
        self.tabs.setGeometry(0, 20, width, height - 20)
        self.messages = {}
        for name in ("MERROR", "MSTATUS", "BLOCKERR"):
            page = QWidget()
            message = QLabel("Module OK", page)
            message.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            message.setWordWrap(True)
            message.setFont(QFont("Arial", 8))
            message.setGeometry(12, 12, width - 32, height - 70)
            self.tabs.addTab(page, name)
            self.messages[name] = message
        # Azeo exposes this only when a real MERROR clear service exists.
        # Hiding it is truthful today; showing an inert clear button is not.
        self.clear_error = QPushButton("Clear Error", self.tabs.widget(0))
        self.clear_error.setGeometry(width - 145, 8, 125, 29)
        self.clear_error.hide()
        self.apply_theme(palette)

    def apply_theme(self, palette: dict) -> None:
        self._palette = palette
        set_stylesheet(self,
            f"QLabel {{ color: {palette[Role.TEXT]}; }}"
            f"QTabWidget::pane {{ border: 1px solid {palette[Role.LINE]}; }}"
            f"QTabBar::tab {{ background: {palette[Role.SURFACE_FIELD]};"
            f" color: {palette[Role.TEXT]};"
            f" border: 1px solid {palette[Role.LINE]};"
            " min-width: 69px; padding: 3px 8px; }"
            f"QTabBar::tab:selected {{"
            f" background: {palette[Role.SURFACE_PANEL]}; }}")

    def refresh(self, bound) -> None:
        direct = [one.result for one in bound.values()
                  if not isinstance(one, tuple)]
        bad = [one for one in direct if one.quality is Quality.BAD]
        forced = [one for one in direct if one.forced]
        stopped = any(one.module_running is False for one in direct)
        alarms = [one for one in direct if one.alarm_active]
        errors = []
        if stopped:
            errors.append("Module Not Running")
        if bad:
            errors.extend(("I/O Input Error", "Function Block Bad Active"))
        if alarms:
            errors.append("Alarm Processing Error")
        status = ["Function Block Forced"] if forced else ["Module OK"]
        self.messages["MERROR"].setText(
            "\n".join(errors) if errors else "Module OK")
        self.messages["MSTATUS"].setText("\n".join(status))
        self.messages["BLOCKERR"].setText(
            "Configuration or Binding Error" if bad else "Module OK")
        for name, message in self.messages.items():
            active = message.text() != "Module OK"
            message.setStyleSheet(
                f"color: {self._palette[Role.ALARM_P1_TEXT]};"
                if active and name != "MSTATUS" else
                f"color: {self._palette[Role.TEXT]};")


class PIDDetailPanel(QWidget):
    """Loop_dt reproduced as the reference's fixed 592 x 656 surface."""

    action_requested = Signal(str)
    write_requested = Signal(str, object)

    WIDTH = 592
    HEIGHT = 656

    def __init__(self, title, path, palette, parent=None):
        super().__init__(parent)
        self._palette = palette
        self.setFixedSize(self.WIDTH, self.HEIGHT)

        self.header = _PIDDetailHeader(title, path, palette, self)
        self.header.setGeometry(0, 0, self.WIDTH, 74)
        self.header.faceplate_requested.connect(
            lambda: self.action_requested.emit("faceplate"))

        self.limits = _PIDLimitsBank(palette, self)
        self.limits.setGeometry(15, 79, 205, 244)
        self.limits.write_requested.connect(self.write_requested.emit)

        self.simulation = _PIDSimulationGroup(palette, self)
        self.simulation.setGeometry(15, 339, 205, 62)
        self.simulation.write_requested.connect(self.write_requested.emit)

        self.tuning = _PIDTuningBank(palette, self)
        self.tuning.setGeometry(15, 407, 562, 245)
        self.tuning.write_requested.connect(self.write_requested.emit)

        self.alarms = _PIDAlarmMatrix(palette, self)
        self.alarms.setGeometry(226, 79, 350, 166)

        self.diagnostics = _PIDDiagnosticsTabs(palette, self)
        self.diagnostics.setGeometry(226, 243, 350, 300)
        self.apply_theme(palette)

    def apply_theme(self, palette: dict) -> None:
        self._palette = palette
        set_surface_theme(self, palette)
        for child in (self.header, self.limits, self.simulation,
                      self.tuning, self.alarms, self.diagnostics):
            child.apply_theme(palette)

    def set_write_result(self, key: str, success: bool,
                         message: str = "") -> None:
        self.limits.set_write_result(key, success, message)
        self.tuning.set_write_result(key, success, message)
        self.simulation.set_write_result(key, success, message)

    def refresh(self, bound) -> None:
        self.header.refresh(bound)
        self.limits.refresh(bound)
        self.simulation.refresh(bound)
        self.tuning.refresh(bound)
        structure = _result(bound, "tuning.structure")
        structure_name = str(
            structure.value if structure is not None else "").lower()
        two_dof = structure_name in (
            "two_dof", "two_degrees_of_freedom",
            "two degrees of freedom controller")
        pd_structure = structure_name in (
            "pd", "p-d", "pd_on_error", "p_error_d_pv",
            "pd action on error", "p action on error d action on pv")
        self.tuning.set_row_visible("tuning.beta", two_dof)
        self.tuning.set_row_visible("tuning.gamma", two_dof)
        self.tuning.set_row_visible("tuning.bias", pd_structure)
        self.tuning.set_row_visible("tuning.ideadband", not pd_structure)
        ff_enabled = _result(bound, "tuning.ff_enabled")
        self.tuning.set_row_visible(
            "tuning.ff_gain", bool(ff_enabled and ff_enabled.value))
        self.alarms.refresh(bound)
        self.diagnostics.refresh(bound)


ANALOG_LIMITS = (
    ("limits.hi_hi", "Hi Hi Lim", ""),
    ("limits.hi", "Hi Lim", ""),
    ("limits.lo", "Lo Lim", ""),
    ("limits.lo_lo", "Lo Lo Lim", ""),
    ("limits.hys", "Alarm Hysteresis", " %"),
)
AI_DETAIL_LIMITS = ANALOG_LIMITS + (
    ("limits.low_cut", "Low Cutoff", ""),
)
ANALOG_ALARMS = (
    ("limits.hi_hi", "Hi Hi"),
    ("limits.hi", "Hi"),
    ("limits.lo", "Lo"),
    ("limits.lo_lo", "Lo Lo"),
    ("pv.value", "PV Bad"),
)


class _AnalogDetailHeader(_PIDDetailHeader):
    """The 560-pixel AI_dt identity band measured from the manual."""

    def __init__(self, title, path, palette, parent=None):
        super().__init__(title, path, palette, parent)
        self.area.setGeometry(118, 7, 305, 17)
        self.module.setGeometry(100, 24, 355, 25)
        self.description.setGeometry(105, 49, 350, 18)
        self.alarm.move(490, 38)
        self.faceplate.move(520, 29)


class _AnalogLimitsBank(_PIDFieldBank):
    """AI_dt's six online limit fields, including Low Cutoff."""

    def __init__(self, palette, parent=None):
        super().__init__(
            AI_DETAIL_LIMITS, palette, heading="Limits", parent=parent)
        self.more = QPushButton("...", self)
        self.more.setGeometry(58, 0, 25, 18)
        self.more.setToolTip("Focus the first configurable alarm limit")
        self.more.clicked.connect(
            lambda: self._widgets["limits.hi_hi"].setFocus())
        self.apply_theme(palette)

    def apply_theme(self, palette: dict) -> None:
        super().apply_theme(palette)
        if hasattr(self, "more"):
            set_stylesheet(self.more,
                f"color: {palette[Role.TEXT]};"
                f"background: {palette[Role.SURFACE_FIELD]};"
                f"border: 1px solid {palette[Role.LINE_SOFT]};"
                "border-radius: 2px; padding: 0;")


class _AnalogAlarmMatrix(_PIDAlarmMatrix):
    _ROWS = ANALOG_ALARMS
    _HEADINGS = (
        ("Priority", 100, 94), ("Enab", 198, 38),
        ("OOS", 233, 34), ("Shlv", 264, 36), ("Help", 297, 30),
    )
    _PRIORITY_GEOMETRY = (98, 94)
    _CHECK_X = (209, 242, 275)
    _HELP_X = 306


class _AnalogLinearization(QWidget):
    """AI_dt shows L_TYPE as read-only DATADATA, not a hash entry."""

    def __init__(self, palette, parent=None):
        super().__init__(parent)
        self.heading = QLabel("Linearization", self)
        self.heading.setFont(QFont("Arial", 8, QFont.Bold))
        self.heading.setGeometry(0, 0, 110, 18)
        self.value = QLabel("DATADATADATA", self)
        self.value.setFont(QFont("Arial", 8))
        self.value.setGeometry(5, 22, 190, 18)
        self.apply_theme(palette)

    def apply_theme(self, palette):
        set_stylesheet(self, f"QLabel {{ color: {palette[Role.TEXT]}; }}")

    def refresh(self, bound):
        result = _result(bound, "linearization.type")
        self.value.setText(_text(result))


class AnalogDetailPanel(QWidget):
    """AI_dt reproduced as the reference's fixed 560 x 550 surface."""

    action_requested = Signal(str)
    write_requested = Signal(str, object)

    WIDTH = 560
    HEIGHT = 550

    def __init__(self, title, path, palette, parent=None):
        super().__init__(parent)
        self._palette = palette
        self.setFixedSize(self.WIDTH, self.HEIGHT)

        self.header = _AnalogDetailHeader(title, path, palette, self)
        self.header.setGeometry(0, 0, self.WIDTH, 74)
        self.header.faceplate_requested.connect(
            lambda: self.action_requested.emit("faceplate"))

        self.limits = _AnalogLimitsBank(palette, self)
        self.limits.setGeometry(15, 79, 205, 130)
        self.limits.write_requested.connect(self.write_requested.emit)

        self.simulation = _PIDSimulationGroup(palette, self)
        self.simulation.setGeometry(15, 220, 205, 62)
        self.simulation.write_requested.connect(self.write_requested.emit)

        self.tuning = _PIDFieldBank(
            (("tuning.pv_filter", "PV Filter TC", " s"),),
            palette, heading="Tuning", parent=self)
        self.tuning.setGeometry(15, 305, 205, 45)
        self.tuning.write_requested.connect(self.write_requested.emit)

        self.linearization = _AnalogLinearization(palette, self)
        self.linearization.setGeometry(15, 372, 205, 45)

        self.alarms = _AnalogAlarmMatrix(palette, self)
        self.alarms.setGeometry(226, 79, 330, 144)

        self.diagnostics = _PIDDiagnosticsTabs(
            palette, self, width=330, height=310)
        self.diagnostics.setGeometry(226, 222, 330, 310)
        self.apply_theme(palette)

    def apply_theme(self, palette: dict) -> None:
        self._palette = palette
        set_surface_theme(self, palette)
        for child in (
                self.header, self.limits, self.simulation, self.tuning,
                self.linearization, self.alarms, self.diagnostics):
            child.apply_theme(palette)

    def set_write_result(self, key: str, success: bool,
                         message: str = "") -> None:
        self.limits.set_write_result(key, success, message)
        self.tuning.set_write_result(key, success, message)
        self.simulation.set_write_result(key, success, message)

    def refresh(self, bound) -> None:
        self.header.refresh(bound)
        self.limits.refresh(bound)
        self.simulation.refresh(bound)
        self.tuning.refresh(bound)
        self.linearization.refresh(bound)
        self.alarms.refresh(bound)
        self.diagnostics.refresh(bound)


class _GenericAnalogDetailPanel(ModuleDetailBase):
    """AI_dt: the monitoring detail shown in the manual."""

    tuning_rows = (("tuning.pv_filter", "PV Filter TC", " s"),)
    linearization_rows = (("linearization.type", "Type", ""),)

    def __init__(self, title, path, palette, parent=None):
        super().__init__(title, path, palette, parent)
        body = QHBoxLayout()
        # Keep the two banks inside the measured 560 px reference shell even
        # when the platform font makes the alarm captions slightly wider.
        body.setSpacing(10)
        left = QVBoxLayout()
        left.setSpacing(5)
        self.limits = BoundFieldGroups(
            (("Limits", ANALOG_LIMITS),),
            {key for key, _caption, _suffix in ANALOG_LIMITS}, palette)
        self.limits.write_requested.connect(self.write_requested.emit)
        left.addWidget(self.limits)
        self.simulation = SimulationGroup(palette)
        self.simulation.write_requested.connect(self.write_requested.emit)
        left.addWidget(self.simulation)
        self.tuning = BoundFieldGroups(
            (("Tuning", self.tuning_rows),),
            {key for key, _caption, _suffix in self.tuning_rows}, palette)
        self.tuning.write_requested.connect(self.write_requested.emit)
        left.addWidget(self.tuning)
        self.linearization = BoundFieldGroups(
            (("Linearization", self.linearization_rows),),
            {key for key, _caption, _suffix in self.linearization_rows},
            palette)
        self.linearization.write_requested.connect(self.write_requested.emit)
        left.addWidget(self.linearization, 1)
        body.addLayout(left, 5)

        right = QVBoxLayout()
        right.setSpacing(5)
        self.alarms = AlarmMatrix(ANALOG_ALARMS, palette)
        right.addWidget(self.alarms)
        self.diagnostics = DiagnosticsTabs(palette)
        self.diagnostics.setFixedHeight(300)
        right.addWidget(self.diagnostics)
        right.addStretch(1)
        body.addLayout(right, 7)
        self.root.addLayout(body, 1)

    def refresh(self, bound) -> None:
        super().refresh(bound)
        self.limits.refresh(bound)
        self.simulation.refresh(bound)
        self.tuning.refresh(bound)
        self.linearization.refresh(bound)
        self.alarms.refresh(bound)
        self.diagnostics.refresh(bound)


class PulseInputDetailPanel(_GenericAnalogDetailPanel):
    """PI_dt: AI detail anatomy with pulse conversion tuning."""

    tuning_rows = (
        ("tuning.pulse", "Pulse Value", " EU/pulse"),
        ("tuning.time_units", "Time Units", ""),
        ("tuning.pv_filter", "PV Filter TC", " s"),
    )
    linearization_rows = (
        ("linearization.type", "Input Type", ""),
    )


AO_LIMITS = (
    ("limits.out_hi", "Out Hi Lim", ""),
    ("limits.out_lo", "Out Lo Lim", ""),
    ("limits.sp_hi", "SP Hi Lim", ""),
    ("limits.sp_lo", "SP Lo Lim", ""),
)
AO_TUNING = (
    ("tuning.sp_rate_up", "SP Rate UP", " EU/s"),
    ("tuning.sp_rate_dn", "SP Rate DN", " EU/s"),
    ("tuning.fstate_value", "Fault State Value", ""),
    ("tuning.fstate_time", "Fault State Time", " s"),
)


class AnalogOutputDetailPanel(ModuleDetailBase):
    """TAGAO-style output limits, simulation and diagnostic context."""

    def __init__(self, title, path, palette, parent=None):
        super().__init__(title, path, palette, parent)
        body = QHBoxLayout()
        # Two extra pixels pushed the AO panel's minimum width beyond its
        # measured 560 px shell on Windows and compressed the right column.
        body.setSpacing(10)
        left = QVBoxLayout()
        left.setSpacing(5)
        self.limits = BoundFieldGroups(
            (("Limits", AO_LIMITS),),
            {key for key, _caption, _suffix in AO_LIMITS}, palette)
        self.limits.write_requested.connect(self.write_requested.emit)
        left.addWidget(self.limits)
        self.simulation = SimulationGroup(palette)
        self.simulation.write_requested.connect(self.write_requested.emit)
        left.addWidget(self.simulation)
        self.tuning = BoundFieldGroups(
            (("Tuning", AO_TUNING),),
            {key for key, _caption, _suffix in AO_TUNING}, palette)
        self.tuning.write_requested.connect(self.write_requested.emit)
        left.addWidget(self.tuning, 1)
        body.addLayout(left, 5)

        right = QVBoxLayout()
        right.setSpacing(5)
        self.alarms = AlarmMatrix((
            ("pv.value", "PV Bad"),
            ("divergence", "Readback Deviation"),
        ), palette)
        right.addWidget(self.alarms)
        self.diagnostics = DiagnosticsTabs(palette)
        right.addWidget(self.diagnostics, 1)
        body.addLayout(right, 7)
        self.root.addLayout(body, 1)

    def refresh(self, bound) -> None:
        super().refresh(bound)
        self.limits.refresh(bound)
        self.simulation.refresh(bound)
        self.tuning.refresh(bound)
        self.alarms.refresh(bound)
        self.diagnostics.refresh(bound)


DEVICE_GROUPS = (
    ("Limits", (
        ("time.start_limit", "Confirm Active", " s"),
        ("time.stop_limit", "Confirm Passive", " s"),
        ("elapsed", "Elapsed Time", " s"),
    )),
    ("Device", (
        ("state.value", "Device State", ""),
        ("command.output", "Output Value", ""),
        ("feedback.running", "Field Value", ""),
        ("mode", "Actual Mode", ""),
        ("failure.code", "Failure Code", ""),
        ("paused", "Transition Held", ""),
    )),
    ("Permits", (
        ("permit.start", "Start Permit", ""),
        ("permit.interlock", "Interlock", ""),
        ("shutdown", "Shutdown", ""),
    )),
)


class DeviceDetailPanel(ModuleDetailBase):
    """DC/EDC-style details and diagnostics for DEVCTL."""

    def __init__(self, title, path, palette, parent=None):
        super().__init__(title, path, palette, parent)
        body = QHBoxLayout()
        body.setSpacing(12)
        self.fields = BoundFieldGroups(
            DEVICE_GROUPS, {"time.start_limit", "time.stop_limit"}, palette)
        self.fields.write_requested.connect(self.write_requested.emit)
        body.addWidget(self.fields, 5)
        right = QVBoxLayout()
        self.alarms = AlarmMatrix((
            ("state.fail", "Failed"),
            ("pv.value", "PV Bad"),
        ), palette)
        right.addWidget(self.alarms)
        self.diagnostics = DiagnosticsTabs(palette)
        right.addWidget(self.diagnostics, 1)
        body.addLayout(right, 7)
        self.root.addLayout(body, 1)

    def refresh(self, bound) -> None:
        super().refresh(bound)
        self.fields.refresh(bound)
        self.alarms.refresh(bound)
        self.diagnostics.refresh(bound)


class InterlockDetailPanel(ModuleDetailBase):
    """Condition table plus the shared module diagnostic tabs."""

    def __init__(self, title, path, palette, parent=None):
        super().__init__(title, path, palette, parent)
        body = QHBoxLayout()
        body.setSpacing(8)
        self.conditions = ConditionTable(palette)
        self.conditions.write_requested.connect(self.write_requested.emit)
        body.addWidget(self.conditions, 3)
        self.diagnostics = DiagnosticsTabs(palette)
        body.addWidget(self.diagnostics, 2)
        self.root.addLayout(body, 1)

    def refresh(self, bound) -> None:
        super().refresh(bound)
        self.conditions.refresh(bound)
        self.diagnostics.refresh(bound)


class SequenceDetailPanel(ModuleDetailBase):
    """The sequence's running chart with state and diagnostic context."""

    def __init__(self, title, path, palette, parent=None):
        super().__init__(title, path, palette, parent)
        body = QHBoxLayout()
        body.setSpacing(8)
        self.chart = SfcRunningChartPanel(palette)
        body.addWidget(self.chart, 3)
        self.diagnostics = DiagnosticsTabs(palette)
        body.addWidget(self.diagnostics, 2)
        self.root.addLayout(body, 1)

    def refresh(self, bound) -> None:
        super().refresh(bound)
        self.chart.refresh(bound)
        self.diagnostics.refresh(bound)


DETAIL_PANELS = {
    "AnalogDetail": AnalogDetailPanel,
    "AnalogOutputDetail": AnalogOutputDetailPanel,
    "PIDDetail": PIDDetailPanel,
    "PulseInputDetail": PulseInputDetailPanel,
    "DeviceDetail": DeviceDetailPanel,
    "MotorInterlockDetail": InterlockDetailPanel,
    "SfcChartDetail": SequenceDetailPanel,
}


def create_detail_panel(pvm, params, palette, parent=None):
    factory = DETAIL_PANELS.get(type(pvm).__name__)
    if factory is None:
        return None
    path = " · ".join(str(params.get(key, "")) for key in pvm.PARAMS)
    return factory(pvm.display_name or pvm.block_type, path, palette, parent)


__all__ = [
    "AnalogDetailPanel", "AnalogOutputDetailPanel", "DETAIL_PANELS",
    "DeviceDetailPanel", "InterlockDetailPanel", "PIDDetailPanel",
    "PulseInputDetailPanel", "SequenceDetailPanel", "create_detail_panel",
]
