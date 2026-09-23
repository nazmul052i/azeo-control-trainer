"""Sheet-06 faceplate visuals — the bars.

The conversion's whole point is visible here: **at rest there is no
colour at all**. Alarm limits are ticks outside the scale rather than
coloured arrows, so the bar stays readable; a tick gains colour only
while its limit is the one breached. Bad quality blanks the bar —
drawing a fill for a dead transmitter claims a measurement nobody made.

The PID pairing puts OUT and PV bars adjacent so deviation reads as a
physical gap between the SP marker and the bar top — the number is for
precision, the gap is for the two-second scan.

Widgets expose their computed state (`fraction`, `ticks`, `breached`,
`sp_fraction`) so the smoke suite asserts geometry, not pixels.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..theme.roles import Role
from .analog_fb_surfaces import WHOLE_SURFACES as _ANALOG_FB_SURFACES
from .analog_surface import AnalogFaceplateSurface as AnalogBars
from .condition_surfaces import WHOLE_SURFACES as _CONDITION_SURFACES
from .device_surface import WHOLE_SURFACES as _DEVICE_SURFACES
from .loop_surface import LoopFaceplateSurface as PidBars
from .selector_voter_surfaces import WHOLE_SURFACES as _SELECTOR_VOTER_SURFACES
from .trainer_faceplate_surfaces import WHOLE_SURFACES as _TRAINER_SURFACES

_TICK_ORDER = ("HH", "H", "L", "LL")
_TICK_TOKENS = {"HH": Role.ALARM_P1, "LL": Role.ALARM_P1,
                "H": Role.ALARM_P2, "L": Role.ALARM_P2}


class ScaleBar(QWidget):
    """One vertical bar: track, fill, EU labels, limit ticks, SP marker."""

    def __init__(self, palette: dict, label: str = "", parent=None):
        super().__init__(parent)
        self._palette = palette
        self.label = label
        self.value: float | None = None
        self.eu_range: tuple[float, float] = (0.0, 100.0)
        self.ticks: dict[str, float] = {}
        self.breached: str = ""
        self.sp_value: float | None = None
        self.bad: bool = False
        self.setMinimumSize(64, 170)

    # ------------------------------------------------------------ state
    def set_state(self, value, eu_range=None, ticks=None,
                  breached: str = "", sp_value=None,
                  bad: bool = False) -> None:
        self.value = value
        if eu_range:
            self.eu_range = (float(eu_range[0]), float(eu_range[1]))
        self.ticks = dict(ticks or {})
        self.breached = breached
        self.sp_value = sp_value
        self.bad = bad
        self.update()

    def _fraction_of(self, value) -> float | None:
        lo, hi = self.eu_range
        if value is None or hi <= lo:
            return None
        return max(0.0, min(1.0, (float(value) - lo) / (hi - lo)))

    @property
    def fraction(self) -> float | None:
        return None if self.bad else self._fraction_of(self.value)

    @property
    def sp_fraction(self) -> float | None:
        return self._fraction_of(self.sp_value)

    # ------------------------------------------------------------ paint
    def paintEvent(self, event) -> None:            # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        palette = self._palette
        width, height = self.width(), self.height()
        top, bottom = 16.0, height - 16.0
        bar = QRectF(width / 2 - 9, top, 18, bottom - top)

        # Track.
        painter.setPen(QPen(QColor(palette[Role.LINE]), 1.0))
        painter.setBrush(QBrush(QColor(palette[Role.SURFACE_SUNK])))
        painter.drawRect(bar)

        # Fill — greyscale at rest; nothing at all when Bad.
        fraction = self.fraction
        if fraction is not None:
            fill_h = bar.height() * fraction
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(palette[Role.BAR_PV])))
            painter.drawRect(QRectF(bar.left(),
                                    bar.bottom() - fill_h,
                                    bar.width(), fill_h))
        elif self.bad:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(palette[Role.LINE_SOFT]),
                                    Qt.BDiagPattern))
            painter.drawRect(bar)

        # Limit ticks, OUTSIDE the scale; colour only on the breach.
        painter.setFont(QFont("Segoe UI", 6))
        for name in _TICK_ORDER:
            if name not in self.ticks:
                continue
            tick_fraction = self._fraction_of(self.ticks[name])
            if tick_fraction is None:
                continue
            y = bar.bottom() - bar.height() * tick_fraction
            colour = QColor(palette[_TICK_TOKENS[name]]) \
                if self.breached == name \
                else QColor(palette[Role.TEXT_FAINT])
            painter.setPen(QPen(colour,
                                2.0 if self.breached == name else 1.0))
            painter.drawLine(bar.right() + 2, y, bar.right() + 9, y)
            painter.drawText(QRectF(bar.right() + 10, y - 6, 20, 12),
                             Qt.AlignLeft | Qt.AlignVCenter, name)

        # SP marker on the left edge — position, not colour.
        sp_fraction = self.sp_fraction
        if sp_fraction is not None:
            y = bar.bottom() - bar.height() * sp_fraction
            painter.setPen(QPen(QColor(palette[Role.TEXT]), 1.4))
            painter.setBrush(QBrush(QColor(palette[Role.TEXT])))
            painter.drawPolygon([
                QRectF(bar.left() - 9, y - 4, 8, 8).topLeft(),
                QRectF(bar.left() - 9, y - 4, 8, 8).bottomLeft(),
                QRectF(bar.left() - 1, y, 0, 0).topLeft(),
            ])

        # Range labels + the column label.
        painter.setFont(QFont("Segoe UI", 7))
        painter.setPen(QColor(palette[Role.TEXT_FAINT]))
        painter.drawText(QRectF(0, 0, width, 14), Qt.AlignCenter,
                         f"{self.eu_range[1]:g}")
        painter.drawText(QRectF(0, height - 14, width, 14),
                         Qt.AlignCenter, f"{self.eu_range[0]:g}")
        if self.label:
            painter.setPen(QColor(palette[Role.TEXT_DIM]))
            painter.drawText(QRectF(0, height - 14, width, 14),
                             Qt.AlignLeft | Qt.AlignVCenter, self.label)
        painter.end()


def _value(bound, key):
    binding = bound.get(key)
    if binding is None or isinstance(binding, tuple):
        return None
    return binding.result.value


def _result(bound, key):
    binding = bound.get(key)
    return None if binding is None or isinstance(binding, tuple) \
        else binding.result


def _format_value(value, decimals=1):
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return "#######"


class AoBars(QWidget):
    """OUT beside the readback — divergence is the story."""

    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self._palette = palette
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)
        header = QHBoxLayout()
        self.value_label = QLabel("OUT  #######")
        self.value_label.setFont(QFont("Consolas", 11, QFont.Bold))
        self.readback_label = QLabel("RB  #######")
        self.readback_label.setFont(QFont("Consolas", 8))
        header.addWidget(self.value_label)
        header.addStretch(1)
        header.addWidget(self.readback_label)
        root.addLayout(header)
        layout = QHBoxLayout()
        layout.addStretch(1)
        self.out = ScaleBar(palette, label="OUT")
        self.out.setFixedHeight(200)
        layout.addWidget(self.out)
        self.readback = ScaleBar(palette, label="RB", parent=self)
        self.readback.hide()
        layout.addStretch(1)
        root.addLayout(layout)
        self.setFixedHeight(235)
        self.apply_theme(palette)

    def apply_theme(self, palette: dict) -> None:
        self._palette = palette
        self.out._palette = palette
        self.readback._palette = palette
        self.value_label.setStyleSheet("color: %s;" % palette[Role.HEADING])
        self.readback_label.setStyleSheet(
            "color: %s;" % palette[Role.TEXT_DIM])
        self.out.update()

    def refresh(self, bound) -> None:
        out = _result(bound, "out.value")
        if out is not None:
            self.out.set_state(out.value, out.eu_range or (0.0, 100.0),
                               {}, bad=out.quality.name == "BAD")
            self.value_label.setText(
                "OUT  %s" % _format_value(
                    None if out.quality.name == "BAD" else out.value))
        readback = _result(bound, "readback.value")
        if readback is not None:
            self.readback.set_state(
                readback.value,
                (out.eu_range if out is not None and out.eu_range
                 else (0.0, 100.0)),
                {}, bad=readback.quality.name == "BAD")
            # TAGAO_fp uses the second value as a marker on the one output
            # scale; two adjacent vertical bars are not its anatomy.
            self.out.sp_value = readback.value
            self.out.update()
            self.readback_label.setText(
                "RB  %s" % _format_value(
                    None if readback.quality.name == "BAD"
                    else readback.value))


#: PvmClass name -> visual factory, same pattern as CLASS_PANELS.
CLASS_VISUALS = {
    "AnalogFaceplate": AnalogBars,
    "PIDFaceplate": PidBars,
    "FLCFaceplate": PidBars,
    "PIDDetail": PidBars,
    "AnalogOutputFaceplate": AoBars,
}

# Whole-faceplate painters live with their family rather than growing this
# already mixed module into another monolith.  Updating after the legacy map
# intentionally replaces AO's old two-bar fragment with the measured TAGAO
# surface, and adds PI's complete function-block faceplate.
CLASS_VISUALS.update(_ANALOG_FB_SURFACES)
CLASS_VISUALS.update(_CONDITION_SURFACES)
CLASS_VISUALS.update(_DEVICE_SURFACES)
CLASS_VISUALS.update(_SELECTOR_VOTER_SURFACES)
CLASS_VISUALS.update(_TRAINER_SURFACES)
from .machine_surface import WHOLE_SURFACES as _MACHINE_SURFACES  # noqa: E402
CLASS_VISUALS.update(_MACHINE_SURFACES)
