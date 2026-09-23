"""Measured Azeo Loop_fp painter integrated with the PVM runtime.

The geometry originated in the user-supplied PySide6 PID faceplate package.
This module owns presentation only; live values, permissions, writes, alarms,
and navigation remain services of the existing PVM binding and station stack.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal, Slot
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QRadialGradient,
    QWheelEvent,
)
from PySide6.QtWidgets import QWidget

from ..theme.roles import Role
from .faceplate_icons import ACTION_ICON_NAMES, draw_faceplate_icon


def _value(bound, key):
    binding = bound.get(key)
    if binding is None or isinstance(binding, tuple):
        return None
    return binding.result.value


def _result(bound, key):
    binding = bound.get(key)
    return None if binding is None or isinstance(binding, tuple) \
        else binding.result


@dataclass(slots=True)
class LoopAlarmItem:
    acknowledged: bool = False
    parameter: str = ""
    help_text: str = ""
    priority: int = 0
    active: bool = True


@dataclass(slots=True)
class LoopMeterState:
    value: float | None = None
    eu_range: tuple[float, float] = (0.0, 100.0)
    ticks: dict[str, float] = field(default_factory=dict)
    bad: bool = False
    sp_value: float | None = None

    def _fraction_of(self, value) -> float | None:
        if value is None:
            return None
        low, high = self.eu_range
        if high <= low:
            return None
        return max(0.0, min(1.0, (float(value) - low) / (high - low)))

    @property
    def fraction(self) -> float | None:
        return None if self.bad else self._fraction_of(self.value)

    @property
    def sp_fraction(self) -> float | None:
        return self._fraction_of(self.sp_value)


@dataclass(slots=True)
class LoopFaceplateState:
    # Reference-text defaults intentionally match the supplied image.
    eyebrow: str = "DATADATADATADATA"
    title: str = "DATADATADATADATA"
    subtitle: str = "DATADATADATADATADATA"
    pv_caption: str = "DATADATADATA"
    out_caption: str = "DATADATA"
    unit_name: str = "DATADATADATADATA"

    pv: float = 140.0
    sp: float = 8.0
    output: float = 76.0
    pv_min: float = 0.0
    pv_max: float = 160.0
    output_min: float = 0.0
    output_max: float = 100.0
    sp_step: float = 1.0
    output_step: float = 1.0

    simulate_active: bool = True
    adaptive_state: str = "ADAPT"
    target_mode: str = "DATADA"
    actual_mode: str = "DAT."
    bypass: bool = True
    reference_placeholders: bool = True
    alarms: list[LoopAlarmItem] = field(default_factory=list)


class LoopFaceplateSurface(QWidget):
    """High-fidelity, scalable PySide6 PID loop faceplate.

    The painter works in the exact 203 x 537 coordinate system measured from
    the supplied reference. The widget scales uniformly without reflow.
    """

    # A measured surface owns the complete faceplate body.  The renderer must
    # not reflow its title, bars, alarm table or action row as independent Qt
    # widgets; doing so was the source of the large spacing and bar-position
    # drift visible in the older faceplates.
    WHOLE_SURFACE = True
    DESIGN_WIDTH = 203.0
    DESIGN_HEIGHT = 537.0
    FULL_HEIGHT = 537
    # Includes the in-faceplate restore chevron at y=107..126.
    MINI_HEIGHT = 127
    ACTIONS = ("detail", "dcc", "primary", "studio", "history", "ack")

    setpointChanged = Signal(float)
    outputChanged = Signal(float)
    bypassChanged = Signal(bool)
    simulateChanged = Signal(bool)
    modeRequested = Signal(str)
    miniFaceplateRequested = Signal()
    iconActivated = Signal(int)
    write_requested = Signal(str, object)
    mini_requested = Signal()
    action_requested = Signal(str)

    BG = QColor(224, 227, 233)
    BG_ALT = QColor(224, 226, 235)
    TEXT = QColor(64, 64, 64)
    MUTED = QColor(164, 164, 164)
    BLUE = QColor(59, 105, 151)
    TEAL = QColor(20, 105, 106)
    GREEN = QColor(102, 171, 104)
    GRID = QColor(198, 201, 207)
    PLOT_BG = QColor(210, 210, 210)

    def __init__(self, palette: dict, parent: QWidget | None = None,
                 state: LoopFaceplateState | None = None) -> None:
        super().__init__(parent)
        self.state = state or LoopFaceplateState()
        self._palette = palette
        self.pv = LoopMeterState()
        self.out = LoopMeterState()
        self.sp_value = None
        self.sp_working = None
        self.sp_lo = None
        self.sp_hi = None
        self.pv_units = ""
        self.out_units = "%"
        self.pv_status_bad = False
        self.out_status_bad = False
        self.simulate_active = False
        self.adaptive_visible = False
        self.bypass_visible = False
        self.bypass_active = False
        self.forced = False
        self.last_good_text = ""
        self.mode = ""
        self.target_mode = ""
        self.normal_mode = ""
        self._mini = False
        self._available_actions: set[str] = set()
        # ``None`` keeps a directly instantiated reference surface usable in
        # galleries and focused paint tests.  The live host always supplies a
        # concrete set (including the empty set) from its checked write
        # service before the operator can interact with the faceplate.
        self._write_permissions: frozenset[str] | None = None
        self._alarm_offset = 0
        self._simulate_writable = False
        self._trend: deque[float] = deque(maxlen=72)
        self._trend.extend(
            [8, 7, 7, 8, 11, 17, 23, 29, 27, 21, 20, 20, 19, 24, 24, 24,
             24, 24, 24, 24, 24, 24, 24, 24, 24, 24, 24, 24, 24, 24, 24, 24,
             24, 24, 24, 24, 24, 23, 22, 21, 21, 20, 20, 20, 20, 20, 20, 19,
             18, 17, 16, 15, 14, 12, 10, 9, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8,
             8, 8, 8, 8, 8, 8]
        )
        self._hover: str | None = None
        self._pressed: str | None = None
        self._drag_sp = False
        self._drag_out = False
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setFixedSize(203, self.FULL_HEIGHT)
        self.setpointChanged.connect(
            lambda value: self.write_requested.emit("sp.value", value))
        self.outputChanged.connect(
            lambda value: self.write_requested.emit("out.value", value))
        self.bypassChanged.connect(
            lambda value: self.write_requested.emit("bypass.value", value))
        self.simulateChanged.connect(
            lambda value: self.write_requested.emit("simulate", value))
        self.miniFaceplateRequested.connect(self.mini_requested.emit)
        self.iconActivated.connect(
            lambda index: self.action_requested.emit(self.ACTIONS[index]))
        self.apply_theme(palette)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(203, self.MINI_HEIGHT if self._mini else self.FULL_HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(203, self.MINI_HEIGHT if self._mini else self.FULL_HEIGHT)

    def apply_theme(self, palette: dict) -> None:
        """Apply runtime themes without losing the measured Azeo colors."""
        self._palette = palette
        from ..theme.tokens import THEMES
        self._role_colors = {role: QColor(value) for role, value in palette.items()}
        self._theme_furniture = palette not in (THEMES["silver"], THEMES["azeo_live"])
        if str(palette[Role.SURFACE_PANEL]).lower() == "#e0e2eb":
            self.BG = QColor(224, 227, 233)
            self.BG_ALT = QColor(224, 226, 235)
            self.TEXT = QColor(64, 64, 64)
            self.MUTED = QColor(164, 164, 164)
            self.BLUE = QColor(59, 105, 151)
            self.TEAL = QColor(20, 105, 106)
            self.GREEN = QColor(102, 171, 104)
            self.GRID = QColor(198, 201, 207)
            self.PLOT_BG = QColor(210, 210, 210)
        else:
            self.BG = QColor(palette[Role.SURFACE_PANEL])
            self.BG_ALT = QColor(palette[Role.SURFACE_PANEL_ALT])
            self.TEXT = QColor(palette[Role.TEXT])
            self.MUTED = QColor(palette[Role.TEXT_FAINT])
            self.BLUE = QColor(palette[Role.BAR_PV])
            self.TEAL = QColor(palette[Role.ACTION])
            self.GREEN = QColor(palette[Role.NORMAL_BAND])
            self.GRID = QColor(palette[Role.LINE_SOFT])
            self.PLOT_BG = QColor(palette[Role.SURFACE_SUNK])
        # A fixed measurement-bar blue is too faint for numeric text on gray.
        # Keep the bar/trace identity and resolve readable digits separately.
        self.VALUE = self._role_colors[Role.HEADING] if self._theme_furniture else self.BLUE
        self.update()

    def _furniture(self, role, *silver_rgb):
        """Retain commissioned silver artwork; new themes use neutral roles."""
        return self._role_colors[role] if self._theme_furniture else QColor(*silver_rgb)

    def set_identity(self, path: str, description: str = "") -> None:
        module, separator, block = str(path).partition("/")
        self.set_labels(
            eyebrow=str(path) if separator else "",
            title=module or description,
            subtitle=description,
            unit_name=module or block)

    def set_available_actions(self, keys) -> None:
        self._available_actions = set(keys or ()).intersection(self.ACTIONS)
        self.update()

    def set_write_permissions(self, keys) -> None:
        """Apply the host's checked writable-binding decisions."""
        self._write_permissions = frozenset(str(key) for key in (keys or ()))
        self.update()

    @property
    def writable_keys(self) -> frozenset[str]:
        return self._write_permissions or frozenset()

    def _write_allowed(self, key: str) -> bool:
        return self._write_permissions is None or key in self._write_permissions

    @property
    def available_actions(self) -> frozenset[str]:
        return frozenset(self._available_actions)

    def set_alarm_records(self, records) -> None:
        rows = []
        for record in records or ():
            rows.append(LoopAlarmItem(
                acknowledged=bool(record.acknowledged),
                parameter=str(record.condition),
                help_text="?" if getattr(record, "active", False) else "",
                priority=int(record.priority or 0),
                active=bool(record.active)))
        self.state.alarms = rows
        self._alarm_offset = min(
            self._alarm_offset, max(0, len(rows) - 1))
        self.update()

    def set_mini(self, mini: bool) -> None:
        self._mini = bool(mini)
        self.setFixedSize(203, self.MINI_HEIGHT if self._mini
                          else self.FULL_HEIGHT)
        self.updateGeometry()
        self.update()

    def pv_geometry(self) -> tuple[QRectF, QRectF]:
        """Return the reference's adjacent scale and PV-fill lanes."""
        return QRectF(79, 123, 20, 188), QRectF(99, 123, 22, 188)

    def inner_indicator_geometry(self) -> QRectF:
        """Current PV fill, centered inside the light-blue channel."""
        _scale, channel = self.pv_geometry()
        fraction = self._norm(
            self.state.pv, self.state.pv_min, self.state.pv_max)
        top = channel.bottom() - channel.height() * fraction
        # The help figure measures roughly 11 px of dark fill inside the
        # 22 px light-blue lane.  OUT owns its separate horizontal graph and
        # must never change this PV indication.
        width = 11.0
        return QRectF(channel.center().x() - width / 2, top,
                      width, channel.bottom() - top)

    def refresh(self, bound) -> None:
        """Translate one PVM binding snapshot into the measured painter."""
        pv = _result(bound, "pv.value")
        if pv is not None:
            eu_range = pv.eu_range or _value(bound, "pv.range") or (0, 100)
            self.pv.eu_range = tuple(float(value) for value in eu_range)
            self.pv.value = pv.value
            self.pv.bad = pv.quality.name != "GOOD"
            self.pv.ticks = {
                name: float(value)
                for name, key in (("HH", "limits.hi_hi"),
                                  ("H", "limits.hi"),
                                  ("L", "limits.lo"),
                                  ("LL", "limits.lo_lo"))
                if isinstance((value := _value(bound, key)), (int, float))
            }
            self.pv_units = str(pv.units or _value(bound, "pv.units") or "")
            self.pv_status_bad = self.pv.bad
            self.forced = bool(pv.forced)
            self.last_good_text = (
                "LAST GOOD  %s" % self._compact(float(pv.last_good_value))
                if self.pv.bad and pv.last_good_value is not None else "")
            if isinstance(pv.value, (int, float)):
                self.state.pv = float(pv.value)
                if not self.pv.bad:
                    self._trend.append(float(pv.value))
            self.state.pv_min, self.state.pv_max = self.pv.eu_range

        out = _result(bound, "out.value")
        if out is not None:
            eu_range = out.eu_range or _value(bound, "out.range") or (0, 100)
            self.out.eu_range = tuple(float(value) for value in eu_range)
            self.out.value = out.value
            self.out.bad = out.quality.name != "GOOD"
            self.out.ticks = {
                name: float(value)
                for name, key in (("H", "limits.out_hi"),
                                  ("L", "limits.out_lo"))
                if isinstance((value := _value(bound, key)), (int, float))
            }
            self.out_units = str(out.units or _value(bound, "out.units") or "%")
            self.out_status_bad = self.out.bad
            if isinstance(out.value, (int, float)):
                self.state.output = float(out.value)
            self.state.output_min, self.state.output_max = self.out.eu_range

        self.sp_value = _value(bound, "sp.value")
        self.sp_working = _value(bound, "sp.working")
        self.sp_lo = _value(bound, "limits.sp_lo")
        self.sp_hi = _value(bound, "limits.sp_hi")
        if isinstance(self.sp_value, (int, float)):
            self.state.sp = float(self.sp_value)
            self.pv.sp_value = float(self.sp_value)

        mode = _result(bound, "mode")
        self.mode = "" if mode is None else str(
            mode.mode_actual or mode.value or "").upper()
        self.target_mode = "" if mode is None else str(
            mode.mode_target or self.mode).upper()
        self.normal_mode = "" if mode is None else str(
            mode.mode_normal or "").upper()
        self._simulate_writable = "simulate" in bound
        self.simulate_active = bool(_value(bound, "simulate"))
        self.adaptive_visible = bool(_value(bound, "adaptive.enabled"))
        self.bypass_visible = bool(_value(bound, "bypass.enabled"))
        self.bypass_active = bool(_value(bound, "bypass.value"))
        self.state.simulate_active = self.simulate_active
        self.state.adaptive_state = "ADAPT" if self.adaptive_visible else ""
        self.state.target_mode = self.target_mode
        self.state.actual_mode = self.mode
        self.state.bypass = self.bypass_active
        self.state.pv_caption = "PV" + (
            f"  {self.pv_units}" if self.pv_units else "")
        self.state.out_caption = "OUT" + (
            f"  {self.out_units}" if self.out_units else "")
        self.state.reference_placeholders = False
        self.update()

    # ------------------------------- public API -------------------------------
    @Slot(bool)
    def set_reference_placeholders(self, enabled: bool) -> None:
        self.state.reference_placeholders = bool(enabled)
        self.update()

    def set_labels(
        self,
        *,
        eyebrow: str | None = None,
        title: str | None = None,
        subtitle: str | None = None,
        pv_caption: str | None = None,
        out_caption: str | None = None,
        unit_name: str | None = None,
    ) -> None:
        for key, value in {
            "eyebrow": eyebrow,
            "title": title,
            "subtitle": subtitle,
            "pv_caption": pv_caption,
            "out_caption": out_caption,
            "unit_name": unit_name,
        }.items():
            if value is not None:
                setattr(self.state, key, str(value))
        self.update()

    def set_ranges(
        self,
        *,
        pv_min: float,
        pv_max: float,
        output_min: float = 0.0,
        output_max: float = 100.0,
    ) -> None:
        if pv_max <= pv_min or output_max <= output_min:
            raise ValueError("Maximum values must be greater than minimum values")
        self.state.pv_min, self.state.pv_max = float(pv_min), float(pv_max)
        self.state.output_min, self.state.output_max = float(output_min), float(output_max)
        self.state.pv = self._clamp(self.state.pv, pv_min, pv_max)
        self.state.sp = self._clamp(self.state.sp, pv_min, pv_max)
        self.state.output = self._clamp(self.state.output, output_min, output_max)
        self.update()

    def set_process_values(
        self,
        *,
        pv: float | None = None,
        sp: float | None = None,
        output: float | None = None,
        append_trend: bool = True,
    ) -> None:
        if pv is not None:
            self.state.pv = self._clamp(pv, self.state.pv_min, self.state.pv_max)
            if append_trend:
                self._trend.append(self.state.pv)
        if sp is not None:
            self.state.sp = self._clamp(sp, self.state.pv_min, self.state.pv_max)
        if output is not None:
            self.state.output = self._clamp(output, self.state.output_min, self.state.output_max)
        self.update()

    @Slot(float)
    def set_pv(self, value: float) -> None:
        self.set_process_values(pv=value)

    @Slot(float)
    def set_setpoint(self, value: float, emit_signal: bool = False) -> None:
        low = float(self.sp_lo) if isinstance(self.sp_lo, (int, float)) \
            else self.state.pv_min
        high = float(self.sp_hi) if isinstance(self.sp_hi, (int, float)) \
            else self.state.pv_max
        value = self._clamp(value, low, high)
        if value != self.state.sp and not emit_signal:
            self.state.sp = value
            self.update()
        if emit_signal and value != self.state.sp:
            # The controller feedback owns the displayed value.  A refused
            # write must not leave the requested value painted as if accepted.
            self.setpointChanged.emit(value)

    @Slot(float)
    def set_output(self, value: float, emit_signal: bool = False) -> None:
        value = self._clamp(value, self.state.output_min, self.state.output_max)
        if value != self.state.output and not emit_signal:
            self.state.output = value
            self.update()
        if emit_signal and value != self.state.output:
            self.outputChanged.emit(value)

    def set_modes(
        self,
        *,
        adaptive_state: str | None = None,
        target_mode: str | None = None,
        actual_mode: str | None = None,
    ) -> None:
        if adaptive_state is not None:
            self.state.adaptive_state = adaptive_state
        if target_mode is not None:
            self.state.target_mode = target_mode
        if actual_mode is not None:
            self.state.actual_mode = actual_mode
        self.update()

    def set_alarms(self, alarms: Sequence[LoopAlarmItem]) -> None:
        self.state.alarms = list(alarms)
        self.update()

    def replace_trend(self, values: Iterable[float]) -> None:
        self._trend.clear()
        self._trend.extend(float(v) for v in values)
        self.update()

    @Slot(float)
    def append_trend(self, value: float) -> None:
        self._trend.append(float(value))
        self.update()

    # -------------------------------- painting --------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        scale, ox, oy = self._transform()
        p.fillRect(self.rect(), self.BG)
        p.translate(ox, oy)
        p.scale(scale, scale)

        design_height = self.MINI_HEIGHT if self._mini else self.DESIGN_HEIGHT
        bg = QLinearGradient(0, 0, self.DESIGN_WIDTH, 0)
        bg.setColorAt(0.0, self.BG)
        bg.setColorAt(0.52, self.BG_ALT)
        bg.setColorAt(1.0, self.BG)
        p.fillRect(QRectF(0, 0, 203, design_height), QBrush(bg))

        self._header(p)
        if self._mini:
            return
        self._slew(p)
        self._vertical_bar(p)
        self._modes(p)
        self._bypass(p)
        self._output_bar(p)
        self._trend_chart(p)
        self._alarm_table(p)
        self._footer(p)
        self._hover_overlay(p)

    def _header(self, p: QPainter) -> None:
        self._text(p, QRectF(2, -5, 199, 16), self.state.eyebrow, 10, self.TEXT)
        self._text(p, QRectF(1, 8, 201, 31), self.state.title, 18, self.TEXT)
        self._text(p, QRectF(3, 39, 197, 18), self.state.subtitle, 11, self.TEXT)
        p.setPen(QPen(self._furniture(Role.LINE_SOFT, 199, 202, 208), 1))
        p.drawLine(QPointF(101, 53), QPointF(101, 101))
        self._text(p, QRectF(3, 57, 94, 24),
                   "#######" if self.pv.bad else self._value(self.state.pv),
                   19, self.VALUE, bold=True)
        self._text(p, QRectF(106, 57, 94, 24),
                   "#######" if self.out.bad else self._value(self.state.output),
                   19, self.TEAL, bold=True)
        self._text(p, QRectF(3, 78, 95, 19), self.state.pv_caption, 12, self.TEXT)
        self._text(p, QRectF(106, 78, 91, 19), self.state.out_caption, 12, self.TEXT)

        # The simulation mark is the shared symbol; a strike shows it inactive.
        draw_faceplate_icon(p, QRectF(174.5, 85.5, 19, 19), "simulate",
                            enabled=self.state.simulate_active)
        if not self.state.simulate_active:
            p.setPen(QPen(self._furniture(Role.LINE, 173, 173, 173), 1))
            p.drawLine(QPointF(177, 88), QPointF(191, 102))

        p.setPen(QPen(self._furniture(Role.LINE, 154, 154, 154), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolyline(QPolygonF([QPointF(184, 119), QPointF(188, 114), QPointF(192, 119)]))

    def _slew(self, p: QPainter) -> None:
        self._text(p, QRectF(3, 124, 64, 11), "SP", 9, self.TEXT, bold=True)
        self._triangle(p, QPointF(36, 145), 24, 11, True, self._furniture(Role.SURFACE_FIELD, 244, 244, 244), self._furniture(Role.LINE, 110, 110, 110))
        self._text(p, QRectF(0, 151, 73, 26), self._value(self.state.sp, 6), 18, self._furniture(Role.TEXT, 32, 32, 32), bold=True)
        self._triangle(p, QPointF(36, 181), 24, 11, False, self._furniture(Role.SURFACE_FIELD, 244, 244, 244), self._furniture(Role.LINE, 110, 110, 110))
        self._text(p, QRectF(3, 207, 64, 11), "OUT", 9, self.TEAL, bold=True)
        self._triangle(p, QPointF(36, 228), 24, 12, True, self.TEAL, self.TEAL)
        self._text(p, QRectF(0, 235, 73, 27), self._value(self.state.output, 6), 18, self.TEAL, bold=True)
        self._triangle(p, QPointF(36, 267), 24, 12, False, QColor(28, 130, 128), QColor(28, 130, 128))

    def _vertical_bar(self, p: QPainter) -> None:
        top, bottom = 123.0, 311.0
        self._text(p, QRectF(43, 101, 77, 19), self._range(self.state.pv_max), 13, self.TEXT, bold=True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._furniture(Role.SURFACE_SUNK, 204, 207, 213))
        p.drawRect(QRectF(79, top, 20, bottom - top))
        channel = QLinearGradient(99, 0, 121, 0)
        light = QColor(
            round(self.BG.red() * 0.8 + self.BLUE.red() * 0.2),
            round(self.BG.green() * 0.8 + self.BLUE.green() * 0.2),
            round(self.BG.blue() * 0.8 + self.BLUE.blue() * 0.2))
        channel.setColorAt(0, light.lighter(105))
        channel.setColorAt(1, light)
        p.fillRect(QRectF(99, top, 22, bottom - top), QBrush(channel))
        p.setPen(QPen(self._furniture(Role.LINE, 181, 184, 190), 0.8))
        for i in range(11):
            y = top + (bottom - top) * i / 10
            length = 18 if i % 5 == 0 else (13 if i % 2 == 0 else 9)
            p.drawLine(QPointF(99 - length, y), QPointF(99, y))

        dark_rect = self.inner_indicator_geometry()
        # The dark PV indicator is inset concentrically in the 22 px
        # light-blue channel (99..121). The full channel stays visible as the
        # reference track; only this centered strip rises with PV.
        dark_left = dark_rect.left()
        dark = QLinearGradient(
            dark_left, 0, dark_left + dark_rect.width(), 0)
        dark.setColorAt(0, QColor(76, 119, 162))
        dark.setColorAt(1, QColor(64, 111, 159))
        if not self.pv.bad:
            p.fillRect(dark_rect, QBrush(dark))

        sp_f = self._norm(self.state.sp, self.state.pv_min, self.state.pv_max)
        sp_y = bottom - sp_f * (bottom - top)
        # Keep SP in the scale lane: the old white banner obscured the PV
        # fill and ran into the working-SP/alarm marks on its other side.
        p.setPen(QPen(self._furniture(Role.LINE, 110, 110, 110), 0.8))
        p.setBrush(self._furniture(Role.TEXT, 255, 255, 255))
        p.drawPolygon(QPolygonF([
            QPointF(86, sp_y - 5), QPointF(99, sp_y),
            QPointF(86, sp_y + 5),
        ]))
        p.setPen(QPen(self._furniture(Role.LINE, 220, 222, 226), 0.8))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(79, top, 42, bottom - top))
        for _name, limit in self.pv.ticks.items():
            y = bottom - self._norm(
                limit, self.state.pv_min, self.state.pv_max) * (bottom - top)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._furniture(Role.TEXT, 55, 55, 55))
            p.drawPolygon(QPolygonF([
                QPointF(121, y), QPointF(125, y - 2.5),
                QPointF(125, y + 2.5)]))
        if isinstance(self.sp_working, (int, float)) \
                and abs(self.sp_working - self.state.sp) > 1e-6:
            working_y = bottom - self._norm(
                float(self.sp_working), self.state.pv_min,
                self.state.pv_max) * (bottom - top)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._furniture(Role.TEXT, 45, 45, 45))
            p.drawPolygon(QPolygonF([
                QPointF(122, working_y), QPointF(128, working_y - 4),
                QPointF(128, working_y + 4)]))
        self._text(p, QRectF(42, 312, 80, 21), self._range(self.state.pv_min), 13, self.TEXT, bold=True)

    def _modes(self, p: QPainter) -> None:
        rows = [
            (132, self.state.adaptive_state, self.GREEN, False, "adaptive"),
            (151, self.state.target_mode, self.MUTED, False, "target"),
            (170, self.state.actual_mode, self.TEXT, True, "actual"),
        ]
        for y, text, color, bold, name in rows:
            if name == "adaptive" and not self.adaptive_visible:
                continue
            rect = QRectF(126, y + 1, 14, 17)
            grad = QLinearGradient(rect.left(), 0, rect.right(), 0)
            grad.setColorAt(0, QColor(0, 99, 164))
            grad.setColorAt(0.5, QColor(0, 137, 204))
            grad.setColorAt(1, QColor(0, 126, 190))
            p.setPen(QPen(QColor(183, 211, 225), 1))
            p.setBrush(QBrush(grad))
            p.drawRect(rect)
            self._text(p, rect.adjusted(0, -1, 0, 1), "!", 13, QColor(238, 246, 250), bold=True)
            text_x = 144 if name == "actual" else 146
            text_width = 44 if name == "actual" else 54
            self._text(p, QRectF(text_x, y - 1, text_width, 20), text, 16,
                       color, bold=bold, left=True)
            if name == "actual":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(self._furniture(Role.TEXT, 40, 40, 40))
                arrow = self.actual_mode_arrow_geometry()
                p.drawPolygon(QPolygonF([
                    arrow.topLeft(), arrow.topRight(),
                    QPointF(arrow.center().x(), arrow.bottom()),
                ]))

    @staticmethod
    def actual_mode_arrow_geometry() -> QRectF:
        """Return the visible AUTO/MAN selector arrow in design coordinates."""
        return QRectF(188, 175, 14, 9)

    def _bypass(self, p: QPainter) -> None:
        if not self.bypass_visible:
            return
        rect = QRectF(124, 230, 18, 55)
        grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
        grad.setColorAt(0, self._furniture(Role.SURFACE_PANEL_ALT, 225, 225, 225))
        grad.setColorAt(1, self._furniture(Role.SURFACE_SUNK, 165, 165, 165))
        p.setPen(QPen(self._furniture(Role.LINE, 190, 190, 190), 0.7))
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(rect, 9, 9)
        cy = 239 if self.state.bypass else 276
        knob = QRadialGradient(QPointF(131, cy - 3), 11)
        knob.setColorAt(0, self._furniture(Role.TEXT, 255, 255, 255))
        knob.setColorAt(1, self._furniture(Role.SURFACE_FIELD, 205, 205, 205))
        p.setBrush(QBrush(knob))
        p.drawEllipse(QPointF(133, cy), 8.5, 8.5)
        self._text(p, QRectF(145, 228, 58, 25), "Bypass", 14, self.TEXT if self.state.bypass else self.MUTED, left=True)
        self._text(p, QRectF(145, 254, 58, 25), "Normal", 14, self.MUTED if self.state.bypass else self.TEXT, left=True)

    def _output_bar(self, p: QPainter) -> None:
        self._text(p, QRectF(1, 335, 66, 17), self._range(self.state.output_min, 6), 13, self.TEXT, bold=True, left=True)
        self._text(p, QRectF(134, 335, 65, 17), self._range(self.state.output_max, 6), 13, self.TEXT, bold=True, right=True)
        outer = QRectF(4, 346, 195, 12)
        inner = outer.adjusted(3, 3, -3, -3)
        p.setPen(QPen(QColor(92, 163, 157), 1.2))
        p.setBrush(self._furniture(Role.SURFACE_PANEL_ALT, 221, 229, 226))
        p.drawRect(outer)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._furniture(Role.SURFACE_SUNK, 229, 222, 208))
        p.drawRect(inner)
        frac = self._norm(self.state.output, self.state.output_min, self.state.output_max)
        fill = QLinearGradient(inner.left(), 0, inner.right(), 0)
        fill.setColorAt(0, QColor(15, 116, 114))
        fill.setColorAt(1, QColor(19, 112, 111))
        if not self.out.bad:
            p.fillRect(QRectF(inner.left(), inner.top(),
                              inner.width() * frac, inner.height()),
                       QBrush(fill))
        p.setBrush(self._furniture(Role.SURFACE_PANEL_ALT, 216, 219, 225))
        p.drawRect(QRectF(4, 358, 195, 17))
        p.setPen(QPen(self._furniture(Role.LINE, 189, 192, 198), 0.8))
        for i in range(11):
            x = inner.left() + inner.width() * i / 10
            h = 17 if i in (0, 5, 10) else (12 if i % 2 == 0 else 7)
            p.drawLine(QPointF(x, 358), QPointF(x, 358 + h))
        if not self.out.bad:
            # Use the fill's scale for the pointer too. A fixed marker at the
            # left edge falsely indicated minimum OUT on every live loop.
            x = inner.left() + inner.width() * frac
            self._triangle(p, QPointF(x, 365), 13, 16, True,
                           QColor(26, 128, 126), QColor(26, 128, 126))
        p.setPen(QPen(self._furniture(Role.LINE, 193, 196, 201), 0.8))
        p.drawLine(QPointF(0, 376), QPointF(203, 376))

    def _trend_chart(self, p: QPainter) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.PLOT_BG)
        p.drawRect(QRectF(0, 377, 203, 50))
        self._text(p, QRectF(0, 384, 24, 18), self._compact(self.state.pv_max), 13, self.VALUE, left=True)
        self._text(p, QRectF(0, 404, 24, 18), self._compact(self.state.pv_min), 13, self.VALUE, left=True)
        plot = QRectF(25, 387, 176, 30)
        p.setPen(QPen(self._furniture(Role.LINE, 132, 132, 132), 1))
        p.setBrush(self.PLOT_BG)
        p.drawRect(plot)
        values = list(self._trend)
        if len(values) > 1:
            path = QPainterPath()
            for i, value in enumerate(values):
                x = plot.left() + plot.width() * i / (len(values) - 1)
                y = plot.bottom() - self._norm(value, self.state.pv_min, self.state.pv_max) * plot.height()
                path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
            p.setPen(QPen(QColor(104, 140, 175), 1.3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        p.setPen(QPen(self._furniture(Role.LINE_SOFT, 245, 245, 245), 1))
        p.drawLine(QPointF(0, 427), QPointF(203, 427))

    def _alarm_table(self, p: QPainter) -> None:
        p.setPen(QPen(self._furniture(Role.LINE, 67, 103, 139), 1))
        p.setBrush(self._furniture(Role.SURFACE_PANEL, 224, 227, 235))
        p.drawRect(QRectF(0, 428, 203, 52))
        p.setBrush(self._furniture(Role.SURFACE_PANEL_ALT, 232, 234, 239))
        p.drawRect(QRectF(0, 428, 203, 16))
        p.setPen(QPen(self._furniture(Role.LINE, 35, 35, 35), 0.9))
        p.drawLine(QPointF(28, 428), QPointF(28, 444))
        p.drawLine(QPointF(146, 428), QPointF(146, 444))
        p.drawLine(QPointF(0, 444), QPointF(203, 444))
        self._text(p, QRectF(1, 428, 27, 16), "Ack", 9, self.TEXT, left=True)
        self._text(p, QRectF(30, 428, 114, 16), "Param", 9, self.TEXT, left=True)
        self._text(p, QRectF(148, 428, 53, 16), "Help", 9, self.TEXT, left=True)
        if self.state.alarms:
            a = self.state.alarms[self._alarm_offset]
            ack = "✓" if a.active and a.acknowledged else (
                "□" if not a.active and not a.acknowledged else "")
            color = QColor(self._palette[
                Role.ALARM_P1_TEXT if a.priority >= 15 else
                Role.ALARM_P2_TEXT if a.priority >= 11 else
                Role.ALARM_P3_TEXT])
            self._text(p, QRectF(2, 446, 24, 17), ack, 10,
                       self.TEAL, bold=True)
            self._text(p, QRectF(30, 446, 115, 17), a.parameter, 9,
                       color, left=True)
            self._text(p, QRectF(148, 446, 36, 17), a.help_text, 9, self.TEXT, left=True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._furniture(Role.SURFACE_PANEL_ALT, 151, 153, 156))
        p.drawRect(QRectF(187, 447, 14, 14))
        p.drawRect(QRectF(187, 464, 14, 14))
        self._triangle(p, QPointF(194, 454), 8, 6, True, self._furniture(Role.TEXT, 242, 242, 242), self._furniture(Role.TEXT, 242, 242, 242))
        self._triangle(p, QPointF(194, 471), 8, 6, False, self._furniture(Role.TEXT, 242, 242, 242), self._furniture(Role.TEXT, 242, 242, 242))

    def _footer(self, p: QPainter) -> None:
        self._text(p, QRectF(1, 481, 201, 18), f"Unit: {self.state.unit_name}", 13, self.TEXT, left=True)
        for i, x in enumerate((17, 51, 85, 119, 153, 187)):
            if self.ACTIONS[i] in self._available_actions:
                self._round_button(p, QPointF(x, 516), i)

    def _round_button(self, p: QPainter, center: QPointF, index: int) -> None:
        """One footer action: a themed rounded-square shell and the shared symbol."""
        hover = self._hover == f"icon_{index}"
        pressed = self._pressed == f"icon_{index}"
        box = QRectF(center.x() - 16, center.y() - 16, 32, 32)
        grad = QLinearGradient(box.topLeft(), box.bottomLeft())
        grad.setColorAt(0, self._furniture(Role.SELECTION, 218, 218, 218) if pressed
                       else self._furniture(Role.SURFACE_FIELD, 255, 255, 255))
        grad.setColorAt(1, self._furniture(Role.SURFACE_FIELD, 242, 242, 242) if pressed
                       else self._furniture(Role.SURFACE_PANEL_ALT, 218, 218, 218))
        p.setPen(QPen(self._furniture(Role.FOCUS, 137, 168, 195) if hover
                      else self._furniture(Role.LINE, 182, 182, 182), 1))
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(box, 8, 8)
        draw_faceplate_icon(
            p, QRectF(center.x() - 13, center.y() - 13, 26, 26),
            ACTION_ICON_NAMES[self.ACTIONS[index]], button=False)

    def _hover_overlay(self, p: QPainter) -> None:
        if not self._hover or self._hover.startswith("icon_"):
            return
        rect = self._region(self._hover)
        if rect:
            p.setPen(QPen(QColor(72, 126, 168, 130), 0.8))
            p.setBrush(QColor(95, 155, 193, 22))
            p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 2, 2)

    # ------------------------------- interaction ------------------------------
    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pt = self._design_point(event.position())
        if self._drag_sp:
            self._set_sp_from_y(pt.y())
            return
        if self._drag_out:
            self._set_out_from_x(pt.x())
            return
        hit = self._hit(pt)
        if hit != self._hover:
            self._hover = hit
            self.setCursor(Qt.CursorShape.PointingHandCursor if hit else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, _event) -> None:  # noqa: N802
        self._hover = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        pt = self._design_point(event.position())
        self._pressed = self._hit(pt)
        if self._pressed == "sp_slider" and self._sp_writable():
            self._drag_sp = True
            self._set_sp_from_y(pt.y())
        elif self._pressed == "output_bar" and self._out_writable():
            self._drag_out = True
            self._set_out_from_x(pt.x())
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(event)
        pt = self._design_point(event.position())
        released = self._hit(pt)
        pressed = self._pressed
        dragged = self._drag_sp or self._drag_out
        self._pressed = None
        self._drag_sp = self._drag_out = False
        if pressed and pressed == released and not dragged:
            self._activate(pressed)
        self.update()
        event.accept()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        hit = self._hit(self._design_point(event.position()))
        steps = event.angleDelta().y() / 120.0
        if hit in {"sp_up", "sp_down", "sp_slider"} \
                and self._sp_writable():
            self.set_setpoint(self.state.sp + steps * self.state.sp_step, True)
            event.accept()
        elif hit in {"out_up", "out_down", "output_bar"} \
                and self._out_writable():
            self.set_output(self.state.output + steps * self.state.output_step, True)
            event.accept()
        else:
            super().wheelEvent(event)

    def _activate(self, name: str) -> None:
        if name == "sp_up" and self._sp_writable():
            self.set_setpoint(self.state.sp + self.state.sp_step, True)
        elif name == "sp_down" and self._sp_writable():
            self.set_setpoint(self.state.sp - self.state.sp_step, True)
        elif name == "out_up" and self._out_writable():
            self.set_output(self.state.output + self.state.output_step, True)
        elif name == "out_down" and self._out_writable():
            self.set_output(self.state.output - self.state.output_step, True)
        elif name == "simulate":
            self.simulateChanged.emit(not self.state.simulate_active)
        elif name == "mini":
            self.miniFaceplateRequested.emit()
        elif name.startswith("mode_"):
            self.modeRequested.emit(name.removeprefix("mode_"))
        elif name == "bypass":
            self.bypassChanged.emit(not self.state.bypass)
        elif name == "alarm_up":
            self._alarm_offset = max(0, self._alarm_offset - 1)
        elif name == "alarm_down":
            self._alarm_offset = min(
                max(0, len(self.state.alarms) - 1), self._alarm_offset + 1)
        elif name.startswith("icon_"):
            self.iconActivated.emit(int(name.split("_")[1]))
        self.update()

    def _sp_writable(self) -> bool:
        return self._write_allowed("sp.value") and self.target_mode in (
            "AUTO", "MAN", "MANUAL")

    def _out_writable(self) -> bool:
        return self._write_allowed("out.value") and self.target_mode in (
            "MAN", "MANUAL", "OOS")

    def _set_sp_from_y(self, y: float) -> None:
        fraction = 1 - self._clamp((y - 123) / (311 - 123), 0, 1)
        value = self.state.pv_min + fraction * (self.state.pv_max - self.state.pv_min)
        self.set_setpoint(value, True)

    def _set_out_from_x(self, x: float) -> None:
        fraction = self._clamp((x - 7) / (196 - 7), 0, 1)
        value = self.state.output_min + fraction * (self.state.output_max - self.state.output_min)
        self.set_output(value, True)

    # -------------------------------- helpers ---------------------------------
    def _transform(self) -> tuple[float, float, float]:
        design_height = self.MINI_HEIGHT if self._mini else self.DESIGN_HEIGHT
        scale = min(self.width() / self.DESIGN_WIDTH,
                    self.height() / design_height)
        return (scale, (self.width() - self.DESIGN_WIDTH * scale) / 2,
                (self.height() - design_height * scale) / 2)

    def _design_point(self, point: QPointF) -> QPointF:
        scale, ox, oy = self._transform()
        return QPointF((point.x() - ox) / scale, (point.y() - oy) / scale)

    @staticmethod
    def _region(name: str) -> QRectF | None:
        regions = {
            "sp_up": QRectF(6, 131, 62, 24),
            "sp_down": QRectF(6, 174, 62, 23),
            "out_up": QRectF(6, 216, 62, 24),
            "out_down": QRectF(6, 258, 62, 23),
            "sp_slider": QRectF(76, 120, 47, 194),
            "output_bar": QRectF(3, 344, 197, 32),
            "simulate": QRectF(173, 84, 22, 22),
            "mini": QRectF(178, 107, 24, 19),
            "mode_adaptive": QRectF(124, 130, 79, 20),
            "mode_target": QRectF(124, 149, 79, 20),
            "mode_actual": QRectF(124, 168, 79, 21),
            "bypass": QRectF(121, 226, 82, 63),
            "alarm_up": QRectF(187, 447, 14, 14),
            "alarm_down": QRectF(187, 464, 14, 14),
        }
        if name.startswith("icon_"):
            i = int(name.split("_")[1])
            return QRectF((17, 51, 85, 119, 153, 187)[i] - 17,
                          499, 34, 34)
        return regions.get(name)

    def _hit(self, point: QPointF) -> str | None:
        names = ["sp_up", "sp_down", "out_up", "out_down", "sp_slider",
                 "output_bar", "simulate", "mini", "mode_actual", "bypass",
                 "alarm_up", "alarm_down"]
        names += [f"icon_{i}" for i, action in enumerate(self.ACTIONS)
                  if action in self._available_actions]
        for name in names:
            if name == "bypass" and (
                    not self.bypass_visible
                    or not self._write_allowed("bypass.value")):
                continue
            if name == "simulate" and (
                    not self._simulate_writable
                    or not self._write_allowed("simulate")):
                continue
            if name == "mode_actual" \
                    and not self._write_allowed("mode.command"):
                continue
            if name in {"sp_up", "sp_down", "sp_slider"} \
                    and not self._sp_writable():
                continue
            if name in {"out_up", "out_down", "output_bar"} \
                    and not self._out_writable():
                continue
            if name == "alarm_up" and self._alarm_offset <= 0:
                continue
            if name == "alarm_down" \
                    and self._alarm_offset >= len(self.state.alarms) - 1:
                continue
            rect = self._region(name)
            if rect and rect.contains(point):
                return name
        return None

    def _text(
        self,
        p: QPainter,
        rect: QRectF,
        text: str,
        px: int,
        color: QColor,
        *,
        bold: bool = False,
        italic: bool = False,
        left: bool = False,
        right: bool = False,
    ) -> None:
        font = QFont("Arial")
        font.setPixelSize(px)
        font.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
        font.setItalic(italic)
        p.save()
        p.setFont(font)
        p.setPen(color)
        align = Qt.AlignmentFlag.AlignVCenter
        align |= Qt.AlignmentFlag.AlignLeft if left else Qt.AlignmentFlag.AlignRight if right else Qt.AlignmentFlag.AlignHCenter
        p.drawText(rect, int(align), text)
        p.restore()

    def _value(self, value: float, placeholders: int = 7) -> str:
        return "#" * placeholders if self.state.reference_placeholders else f"{value:.1f}"

    def _range(self, value: float, placeholders: int = 7) -> str:
        return "#" * placeholders if self.state.reference_placeholders else self._compact(value)

    @staticmethod
    def _compact(value: float) -> str:
        return str(int(value)) if float(value).is_integer() else f"{value:g}"

    @staticmethod
    def _triangle(p: QPainter, center: QPointF, width: float, height: float, up: bool, fill: QColor, outline: QColor) -> None:
        hw, hh = width / 2, height / 2
        points = (
            [QPointF(center.x(), center.y() - hh), QPointF(center.x() - hw, center.y() + hh), QPointF(center.x() + hw, center.y() + hh)]
            if up
            else [QPointF(center.x() - hw, center.y() - hh), QPointF(center.x() + hw, center.y() - hh), QPointF(center.x(), center.y() + hh)]
        )
        p.save()
        p.setPen(QPen(outline, 1))
        p.setBrush(fill)
        p.drawPolygon(QPolygonF(points))
        p.restore()

    @staticmethod
    def _clamp(value: float, minimum: float, maximum: float) -> float:
        return max(minimum, min(maximum, float(value)))

    @staticmethod
    def _norm(value: float, minimum: float, maximum: float) -> float:
        return 0.0 if maximum <= minimum else max(0.0, min(1.0, (value - minimum) / (maximum - minimum)))
