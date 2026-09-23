"""
ISA-101 faceplate popups for discrete devices (valves/motors),
analog inputs (AI), and analog outputs (AO).

Follows the same ISA-101 Silver Theme / ISA-101 patterns used by the
PID ``FaceplatePopup`` in ``faceplate_full.py``.

Four classes:
  - ValveFaceplatePopup      -- control valve with vertical bar graph
  - AIFaceplatePopup         -- analog input with alarm-band bar graph
  - AOFaceplatePopup         -- analog output with operator-settable spinbox
  - ValveDiagnosticsDialog   -- valve detail / diagnostics popup
"""

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QFont, QLinearGradient
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QDoubleSpinBox, QFrame,
    QDialog, QSizePolicy,
)

from ..theme.colors import (
    BG_FACEPLATE, BG_INSET, BG_DARK, BG_RAISED, BG_PANEL,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_ON_DARK,
    STATUS_AUTO, STATUS_MAN, STATUS_CAS, STATUS_OOS, STATUS_INIT,
    ALARM_CRITICAL, ALARM_WARN, ALARM_ADVISORY, ALARM_OK,
)

log = logging.getLogger(__name__)

# ── Local palette aliases ────────────────────────────────────────────
_HEADER_TOP = "#1976D2"
_HEADER_BOT = "#0D47A1"
_BODY_BG = BG_FACEPLATE          # #EBECF1
_TAG_COLOR = TEXT_SECONDARY       # dark blue
_VALUE_COLOR = TEXT_PRIMARY       # black
_BORDER_NORMAL = "#9BADC6"        # DV_ALARM_BAR1

_STATE_OPEN_COLOR = "#2E7D32"     # green
_STATE_CLOSED_COLOR = "#C62828"   # red

_BAR_BG = "#D0D2DB"
_BAR_NORMAL = "#3C6291"           # DV_PV_FG
_BAR_ALARM_HI = ALARM_CRITICAL
_BAR_ALARM_LO = ALARM_CRITICAL
_BAR_WARN_HI = ALARM_WARN
_BAR_WARN_LO = ALARM_WARN

_BTN_INACTIVE_BG = BG_INSET
_BTN_INACTIVE_FG = "#7B92AD"
_BTN_INACTIVE_BORDER = "#9BADC6"

_STATUS_GOOD_COLOR = "#2E7D32"
_STATUS_BAD_COLOR = ALARM_CRITICAL


# =====================================================================
# Shared helpers
# =====================================================================

def _make_header(tag: str, description: str,
                 pin_btn: QPushButton | None = None) -> QFrame:
    """Blue gradient header bar with tag + description + optional pin button."""
    header = QFrame()
    header.setFixedHeight(28)
    header.setStyleSheet(
        "QFrame {"
        f"  background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
        f"    stop:0 {_HEADER_TOP}, stop:1 {_HEADER_BOT});"
        "  border: none; border-radius: 3px;"
        "}"
    )
    lay = QHBoxLayout(header)
    lay.setContentsMargins(8, 2, 8, 2)
    lay.setSpacing(6)

    tag_lbl = QLabel(tag)
    tag_lbl.setStyleSheet(
        f"QLabel {{ color: {TEXT_ON_DARK}; font-size: 10pt; font-weight: bold;"
        " background: transparent; border: none; }}"
    )
    lay.addWidget(tag_lbl)
    if description:
        desc_lbl = QLabel(f"\u2014 {description}")
        desc_lbl.setStyleSheet(
            f"QLabel {{ color: #B3D4FC; font-size: 8pt;"
            " background: transparent; border: none; }}"
        )
        lay.addWidget(desc_lbl)
    lay.addStretch()
    if pin_btn is not None:
        lay.addWidget(pin_btn)
    return header


def _make_mode_badge(mode: str = "AUTO") -> QLabel:
    """Small mode badge label."""
    lbl = QLabel(mode)
    lbl.setFixedHeight(18)
    lbl.setFixedWidth(52)
    lbl.setAlignment(Qt.AlignCenter)
    _apply_mode_badge_style(lbl, mode)
    return lbl


def _apply_mode_badge_style(lbl: QLabel, mode: str) -> None:
    style_map = {
        "AUTO":   (TEXT_ON_DARK, STATUS_AUTO,  True),
        "MANUAL": (TEXT_ON_DARK, STATUS_MAN,   True),
        "CAS":    (TEXT_ON_DARK, STATUS_CAS,   True),
        "OOS":    (TEXT_ON_DARK, STATUS_OOS,   True),
        "INIT":   (TEXT_ON_DARK, STATUS_INIT,  True),
    }
    fg, bg, bold = style_map.get(mode.upper(), ("white", "#555555", True))
    lbl.setText(mode.upper())
    lbl.setStyleSheet(
        f"QLabel {{ color: {fg}; font-size: 7pt;"
        f" {'font-weight: bold;' if bold else ''}"
        f" background-color: {bg}; border: none;"
        f" border-radius: 2px; padding: 0 3px; }}"
    )


def _make_cmd_button(text: str, color: str, active: bool = False) -> QPushButton:
    """Styled command / mode button."""
    btn = QPushButton(text)
    btn.setFixedSize(72, 28)
    btn.setCheckable(True)
    btn.setAutoDefault(False)
    btn.setDefault(False)
    _apply_btn_style(btn, color, active)
    return btn


def _apply_btn_style(btn: QPushButton, color: str, active: bool) -> None:
    if active:
        c = QColor(color)
        glow = c.lighter(130).name()
        btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: qlineargradient(y1:0,y2:1,"
            f"    stop:0 {glow}, stop:1 {color});"
            f"  color: white; border: 2px solid {c.darker(140).name()};"
            f"  border-radius: 3px; font-size: 9pt; font-weight: bold;"
            f"  padding-bottom: 1px;"
            f"}}"
        )
    else:
        btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {_BTN_INACTIVE_BG};"
            f"  color: {_BTN_INACTIVE_FG};"
            f"  border: 1px solid {_BTN_INACTIVE_BORDER};"
            f"  border-radius: 3px; font-size: 9pt; font-weight: bold;"
            f"}}"
        )


class _Separator(QFrame):
    """Thin horizontal line."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.HLine)
        self.setFrameShadow(QFrame.Sunken)
        self.setFixedHeight(2)
        self.setStyleSheet(f"color: {_BORDER_NORMAL};")


# =====================================================================
# VerticalBarGraph — reusable vertical bar with optional alarm bands
# =====================================================================
class VerticalBarGraph(QWidget):
    """Custom-painted vertical bar graph with alarm-limit markers.

    Used by AIFaceplatePopup and AOFaceplatePopup.
    """

    def __init__(
        self,
        lo: float = 0.0,
        hi: float = 100.0,
        bar_color: str = _BAR_NORMAL,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._lo = lo
        self._hi = hi
        self._value: float = lo
        self._bar_color = QColor(bar_color)
        self._alarm_hi: float | None = None
        self._alarm_lo: float | None = None
        self._alarm_hihi: float | None = None
        self._alarm_lolo: float | None = None
        self.setFixedWidth(40)
        self.setMinimumHeight(120)

    # -- public API --
    def set_value(self, v: float) -> None:
        self._value = max(self._lo, min(self._hi, v))
        self.update()

    def set_alarm_limits(
        self,
        hi: float | None = None,
        lo: float | None = None,
        hihi: float | None = None,
        lolo: float | None = None,
    ) -> None:
        self._alarm_hi = hi
        self._alarm_lo = lo
        self._alarm_hihi = hihi
        self._alarm_lolo = lolo
        self.update()

    def _alarm_color(self) -> QColor:
        """Return bar color based on current alarm state."""
        v = self._value
        if self._alarm_hihi is not None and v >= self._alarm_hihi:
            return QColor(_BAR_ALARM_HI)
        if self._alarm_lolo is not None and v <= self._alarm_lolo:
            return QColor(_BAR_ALARM_LO)
        if self._alarm_hi is not None and v >= self._alarm_hi:
            return QColor(_BAR_WARN_HI)
        if self._alarm_lo is not None and v <= self._alarm_lo:
            return QColor(_BAR_WARN_LO)
        return self._bar_color

    # -- paint --
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        mx, my = 4, 4
        bx, by = mx, my
        bw = w - 2 * mx
        bh = h - 2 * my

        # Background
        p.fillRect(bx, by, bw, bh, QColor(_BAR_BG))
        p.setPen(QPen(QColor(_BORDER_NORMAL), 1))
        p.drawRect(bx, by, bw - 1, bh - 1)

        rng = max(1e-6, self._hi - self._lo)
        frac = max(0.0, min(1.0, (self._value - self._lo) / rng))
        fill_h = int(bh * frac)

        # Filled bar (from bottom up)
        if fill_h > 0:
            clr = self._alarm_color()
            p.fillRect(bx + 1, by + bh - fill_h, bw - 2, fill_h - 1, clr)

        # Alarm limit markers
        marker_pen = QPen(QColor(ALARM_CRITICAL), 2, Qt.DashLine)
        warn_pen = QPen(QColor(ALARM_WARN), 1, Qt.DashLine)
        for limit_val, pen in [
            (self._alarm_hihi, marker_pen),
            (self._alarm_lolo, marker_pen),
            (self._alarm_hi, warn_pen),
            (self._alarm_lo, warn_pen),
        ]:
            if limit_val is not None:
                lfrac = max(0.0, min(1.0, (limit_val - self._lo) / rng))
                ly = by + int(bh * (1.0 - lfrac))
                p.setPen(pen)
                p.drawLine(bx + 1, ly, bx + bw - 2, ly)

        p.end()


# =====================================================================
# 1. ValveFaceplatePopup — control valve (modulating 0-100%)
# =====================================================================
class ValveFaceplatePopup(QDialog):
    """ISA-101 control valve faceplate with vertical bar graph.

    Shows a vertical bar graph (0-100%), digital position readout,
    output spinbox for manual operation, and AUTO/MAN mode buttons.
    These are modulating control valves, not discrete on/off devices.
    """

    output_changed = Signal(str, float)   # (tag, value 0-100)
    mode_changed = Signal(str)            # "AUTO" or "MANUAL"

    def __init__(
        self,
        tag: str,
        description: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._tag = tag
        self._description = description
        self._position: float = 0.0     # 0..100%
        self._mode: str = "AUTO"

        self._pinned: bool = False

        self.setWindowTitle(f"{description or tag}")
        self.setWindowFlags(
            Qt.Window | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint
        )
        self.resize(180, 380)
        self.setStyleSheet(f"QDialog {{ background-color: {_BODY_BG}; }}")
        self._build_ui()

    # ---- pin support ----
    def _on_pin_toggled(self, checked: bool) -> None:
        self._pinned = checked

    @property
    def pinned(self) -> bool:
        return self._pinned

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if (event.type() == event.Type.ActivationChange
                and not self.isActiveWindow()
                and not self._pinned):
            self.close()

    # ---- build ----
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # Pin button
        self._pin_btn = QPushButton("Pin")
        self._pin_btn.setCheckable(True)
        self._pin_btn.setFixedSize(32, 20)
        self._pin_btn.setStyleSheet(
            "QPushButton { font-size: 7pt; background: transparent;"
            f" color: {TEXT_ON_DARK}; border: 1px solid {TEXT_ON_DARK};"
            " border-radius: 2px; }"
            " QPushButton:checked { background: #CBD9E2; color: #0E3260;"
            " border: 1px solid #0E3260; }"
        )
        self._pin_btn.setToolTip("Pin window (keep on top, prevent auto-close)")
        self._pin_btn.toggled.connect(self._on_pin_toggled)

        # Header — show label only, with pin button
        root.addWidget(_make_header(
            self._description or self._tag, "", pin_btn=self._pin_btn))

        # Mode badge row
        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_lbl = QLabel("Mode:")
        mode_lbl.setStyleSheet(
            f"QLabel {{ font-size: 8pt; color: {_TAG_COLOR}; }}"
        )
        self._mode_badge = _make_mode_badge(self._mode)
        mode_row.addWidget(mode_lbl)
        mode_row.addWidget(self._mode_badge)
        mode_row.addStretch()
        root.addLayout(mode_row)

        # Scale hi label
        hi_lbl = QLabel("100.0")
        hi_lbl.setAlignment(Qt.AlignCenter)
        hi_lbl.setStyleSheet(
            f"QLabel {{ font-size: 7pt; color: {_TAG_COLOR}; }}"
        )
        root.addWidget(hi_lbl)

        # Vertical bar graph (0-100%)
        self._bar = VerticalBarGraph(
            lo=0.0, hi=100.0,
            bar_color="#2196F3",   # ISA-101 blue for valve output
        )
        self._bar.setMinimumHeight(120)
        root.addWidget(self._bar, stretch=1, alignment=Qt.AlignHCenter)

        # Scale lo label
        lo_lbl = QLabel("0.0")
        lo_lbl.setAlignment(Qt.AlignCenter)
        lo_lbl.setStyleSheet(
            f"QLabel {{ font-size: 7pt; color: {_TAG_COLOR}; }}"
        )
        root.addWidget(lo_lbl)

        root.addWidget(_Separator())

        # Digital position readout with inline unit
        val_row = QHBoxLayout()
        val_row.setContentsMargins(0, 0, 0, 0)
        val_row.setSpacing(4)
        self._value_label = QLabel("0.0")
        self._value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._value_label.setFixedHeight(30)
        self._value_label.setStyleSheet(
            f"QLabel {{ font-size: 13pt; font-weight: bold; color: {_VALUE_COLOR};"
            f" background-color: {BG_INSET}; border: 1px solid {_BORDER_NORMAL};"
            f" border-radius: 3px; font-family: Consolas;"
            f" padding-right: 4px; }}"
        )
        units_lbl = QLabel("%")
        units_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        units_lbl.setFixedHeight(30)
        units_lbl.setStyleSheet(
            f"QLabel {{ font-size: 9pt; color: {_TAG_COLOR};"
            f" background: transparent; border: none; }}"
        )
        val_row.addWidget(self._value_label, stretch=1)
        val_row.addWidget(units_lbl)
        root.addLayout(val_row)

        root.addWidget(_Separator())

        # Output spinbox + Apply
        sp_row = QHBoxLayout()
        sp_row.setContentsMargins(0, 0, 0, 0)
        sp_row.setSpacing(3)
        sp_lbl = QLabel("OP:")
        sp_lbl.setStyleSheet(
            f"QLabel {{ font-size: 8pt; color: {_TAG_COLOR}; }}"
        )
        self._out_spin = QDoubleSpinBox()
        self._out_spin.setRange(0.0, 100.0)
        self._out_spin.setDecimals(1)
        self._out_spin.setSingleStep(1.0)
        self._out_spin.setValue(0.0)
        self._out_spin.setStyleSheet(
            f"QDoubleSpinBox {{"
            f"  background-color: white; color: {_VALUE_COLOR};"
            f"  border: 1px solid {_BORDER_NORMAL}; border-radius: 2px;"
            f"  font-size: 9pt; font-family: Consolas; padding: 1px 3px;"
            f"}}"
        )
        self._apply_btn = QPushButton("Apply")
        self._apply_btn.setFixedSize(44, 22)
        self._apply_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {_HEADER_TOP}; color: white;"
            f"  border: 1px solid {_HEADER_BOT}; border-radius: 3px;"
            f"  font-size: 7pt; font-weight: bold;"
            f"}}"
            f"QPushButton:hover {{"
            f"  background-color: {_HEADER_BOT};"
            f"}}"
        )
        self._apply_btn.clicked.connect(self._on_apply)
        sp_row.addWidget(sp_lbl)
        sp_row.addWidget(self._out_spin, stretch=1)
        sp_row.addWidget(self._apply_btn)
        root.addLayout(sp_row)

        root.addWidget(_Separator())

        # Mode buttons
        mode_btn_row = QHBoxLayout()
        mode_btn_row.setSpacing(8)
        self._btn_auto = _make_cmd_button("AUTO", STATUS_AUTO)
        self._btn_man = _make_cmd_button("MANUAL", STATUS_MAN)
        self._btn_auto.clicked.connect(lambda: self._request_mode("AUTO"))
        self._btn_man.clicked.connect(lambda: self._request_mode("MANUAL"))
        mode_btn_row.addStretch()
        mode_btn_row.addWidget(self._btn_auto)
        mode_btn_row.addWidget(self._btn_man)
        mode_btn_row.addStretch()
        root.addLayout(mode_btn_row)

        # Diagnostics button
        diag_row = QHBoxLayout()
        diag_row.setContentsMargins(0, 2, 0, 0)
        self._detail_btn = QPushButton("Diagnostics...")
        self._detail_btn.setFixedSize(100, 24)
        self._detail_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {BG_INSET}; color: {_TAG_COLOR};"
            f"  border: 1px solid {_BORDER_NORMAL}; border-radius: 3px;"
            f"  font-size: 8pt; font-weight: bold;"
            f"}}"
            f"QPushButton:hover {{"
            f"  background-color: {BG_RAISED}; color: {_VALUE_COLOR};"
            f"}}"
        )
        self._detail_btn.clicked.connect(self._open_diagnostics)
        diag_row.addStretch()
        diag_row.addWidget(self._detail_btn)
        diag_row.addStretch()
        root.addLayout(diag_row)

        root.addStretch()
        self._refresh_mode_buttons()

        self._diag_dialog: ValveDiagnosticsDialog | None = None
        self._last_data: dict = {}

    # ---- internal ----
    def _open_diagnostics(self) -> None:
        """Open (or raise) the valve diagnostics dialog."""
        if self._diag_dialog is None or not self._diag_dialog.isVisible():
            self._diag_dialog = ValveDiagnosticsDialog(
                self._tag, self._description, parent=self,
            )
            self._diag_dialog.update_data(self._last_data)
            self._diag_dialog.show()
        else:
            self._diag_dialog.raise_()
            self._diag_dialog.activateWindow()

    def _on_apply(self) -> None:
        val = self._out_spin.value()
        self.output_changed.emit(self._tag, val)

    def _request_mode(self, mode: str) -> None:
        self._mode = mode
        _apply_mode_badge_style(self._mode_badge, mode)
        self._refresh_mode_buttons()
        self.mode_changed.emit(mode)

    def _refresh_mode_buttons(self) -> None:
        is_auto = self._mode.upper() == "AUTO"
        _apply_btn_style(self._btn_auto, STATUS_AUTO, is_auto)
        _apply_btn_style(self._btn_man, STATUS_MAN, not is_auto)
        self._btn_auto.setChecked(is_auto)
        self._btn_man.setChecked(not is_auto)
        # Spinbox + apply only enabled in MANUAL
        self._out_spin.setEnabled(not is_auto)
        self._apply_btn.setEnabled(not is_auto)

    # ---- data update ----
    def update_data(self, data: dict) -> None:
        """Read valve position and mode from data dict.

        Reads the raw tag value (0-1 fractional) and converts to 0-100%.
        Also checks ``io.<tag>.value`` as fallback.
        Mode is read from ``valve.<tag>.mode`` (published by bridge).
        """
        # Try raw tag first (valve position 0-1), then io prefix
        raw = data.get(self._tag)
        if raw is None:
            raw = data.get(f"io.{self._tag}.value")
        if raw is not None:
            try:
                self._position = float(raw) * 100.0  # 0-1 -> 0-100%
            except (TypeError, ValueError):
                pass

        # Read mode from store (published by bridge for AO blocks)
        mode_val = data.get(f"valve.{self._tag}.mode")
        if mode_val is not None:
            new_mode = str(mode_val).upper()
            if new_mode in ("AUTO", "MANUAL") and new_mode != self._mode:
                self._mode = new_mode
                _apply_mode_badge_style(self._mode_badge, self._mode)
                self._refresh_mode_buttons()

        # Update widgets
        self._bar.set_value(self._position)
        self._value_label.setText(f"{self._position:.1f}")
        # Only update spinbox if user is not editing
        if not self._out_spin.hasFocus():
            self._out_spin.setValue(self._position)

        # Cache for diagnostics dialog and forward if open
        self._last_data = data
        if self._diag_dialog is not None and self._diag_dialog.isVisible():
            self._diag_dialog.update_data(data)


# =====================================================================
# 2. AIFaceplatePopup
# =====================================================================
class AIFaceplatePopup(QDialog):
    """ISA-101 analog input faceplate with alarm-band bar graph.

    Shows a vertical bar graph with alarm markers, large digital value
    readout, engineering units, and signal status.
    """

    ack_requested = Signal(str)   # tag

    def __init__(
        self,
        tag: str,
        description: str,
        scale_lo: float = 0.0,
        scale_hi: float = 100.0,
        units: str = "",
        decimals: int = 1,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._tag = tag
        self._description = description
        self._scale_lo = scale_lo
        self._scale_hi = scale_hi
        self._units = units
        self._decimals = decimals
        self._value: float = scale_lo
        self._status: str = "GOOD"
        self._pinned: bool = False

        self.setWindowTitle(f"{description or tag}")
        self.setWindowFlags(
            Qt.Window | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint
        )
        self.resize(160, 320)
        self.setStyleSheet(f"QDialog {{ background-color: {_BODY_BG}; }}")
        self._build_ui()

    # ---- pin support ----
    def _on_pin_toggled(self, checked: bool) -> None:
        self._pinned = checked

    @property
    def pinned(self) -> bool:
        return self._pinned

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if (event.type() == event.Type.ActivationChange
                and not self.isActiveWindow()
                and not self._pinned):
            self.close()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # Pin button
        self._pin_btn = QPushButton("Pin")
        self._pin_btn.setCheckable(True)
        self._pin_btn.setFixedSize(32, 20)
        self._pin_btn.setStyleSheet(
            "QPushButton { font-size: 7pt; background: transparent;"
            f" color: {TEXT_ON_DARK}; border: 1px solid {TEXT_ON_DARK};"
            " border-radius: 2px; }"
            " QPushButton:checked { background: #CBD9E2; color: #0E3260;"
            " border: 1px solid #0E3260; }"
        )
        self._pin_btn.setToolTip("Pin window (keep on top, prevent auto-close)")
        self._pin_btn.toggled.connect(self._on_pin_toggled)

        # Header -- show description as primary, tag as secondary
        root.addWidget(_make_header(
            self._description or self._tag, self._tag,
            pin_btn=self._pin_btn))

        # Scale hi label
        hi_lbl = QLabel(f"{self._scale_hi:.{self._decimals}f}")
        hi_lbl.setAlignment(Qt.AlignCenter)
        hi_lbl.setStyleSheet(
            f"QLabel {{ font-size: 7pt; color: {_TAG_COLOR}; }}"
        )
        root.addWidget(hi_lbl)

        # Bar graph
        self._bar = VerticalBarGraph(
            lo=self._scale_lo, hi=self._scale_hi,
            bar_color=_BAR_NORMAL,
        )
        self._bar.setMinimumHeight(140)
        root.addWidget(self._bar, stretch=1, alignment=Qt.AlignHCenter)

        # Scale lo label
        lo_lbl = QLabel(f"{self._scale_lo:.{self._decimals}f}")
        lo_lbl.setAlignment(Qt.AlignCenter)
        lo_lbl.setStyleSheet(
            f"QLabel {{ font-size: 7pt; color: {_TAG_COLOR}; }}"
        )
        root.addWidget(lo_lbl)

        root.addWidget(_Separator())

        # Digital value readout
        self._value_label = QLabel(f"{self._value:.{self._decimals}f}")
        self._value_label.setAlignment(Qt.AlignCenter)
        self._value_label.setFixedHeight(32)
        self._value_label.setStyleSheet(
            f"QLabel {{ font-size: 14pt; font-weight: bold; color: {_VALUE_COLOR};"
            f" background-color: {BG_INSET}; border: 1px solid {_BORDER_NORMAL};"
            f" border-radius: 3px; font-family: Consolas; }}"
        )
        root.addWidget(self._value_label)

        # Units label
        if self._units:
            units_lbl = QLabel(self._units)
            units_lbl.setAlignment(Qt.AlignCenter)
            units_lbl.setStyleSheet(
                f"QLabel {{ font-size: 8pt; color: {_TAG_COLOR}; }}"
            )
            root.addWidget(units_lbl)

        # Status label
        self._status_label = QLabel("GOOD")
        self._status_label.setAlignment(Qt.AlignCenter)
        self._status_label.setFixedHeight(18)
        self._status_label.setStyleSheet(
            f"QLabel {{ font-size: 8pt; font-weight: bold;"
            f" color: {_STATUS_GOOD_COLOR}; }}"
        )
        root.addWidget(self._status_label)

        # ACK button
        self._ack_btn = QPushButton("ACK")
        self._ack_btn.setFixedSize(60, 24)
        self._ack_btn.setStyleSheet(
            f"QPushButton {{ background-color: {BG_INSET}; color: {_TAG_COLOR};"
            f" border: 1px solid {_BORDER_NORMAL}; border-radius: 3px;"
            f" font-size: 8pt; font-weight: bold; }}"
        )
        self._ack_btn.clicked.connect(lambda: self.ack_requested.emit(self._tag))
        ack_row = QHBoxLayout()
        ack_row.addStretch()
        ack_row.addWidget(self._ack_btn)
        ack_row.addStretch()
        root.addLayout(ack_row)

    # ---- public API ----
    def set_alarm_limits(
        self,
        hi: float | None = None,
        lo: float | None = None,
        hihi: float | None = None,
        lolo: float | None = None,
    ) -> None:
        """Set alarm limit markers on the bar graph."""
        self._bar.set_alarm_limits(hi=hi, lo=lo, hihi=hihi, lolo=lolo)

    def update_data(self, data: dict) -> None:
        """Read analog input value from data dict.

        Looks for ``io.<tag>.value`` or falls back to the raw tag.
        """
        prefix = f"io.{self._tag}"
        raw = data.get(f"{prefix}.value", data.get(self._tag))
        if raw is not None:
            self._value = float(raw)

        status_raw = data.get(f"{prefix}.status")
        if status_raw is not None:
            self._status = str(status_raw).upper()

        # Update widgets
        self._bar.set_value(self._value)
        self._value_label.setText(f"{self._value:.{self._decimals}f}")

        # Alarm-aware value color
        bar_clr = self._bar._alarm_color()
        val_fg = bar_clr.name() if bar_clr.name() != QColor(_BAR_NORMAL).name() else _VALUE_COLOR
        self._value_label.setStyleSheet(
            f"QLabel {{ font-size: 14pt; font-weight: bold; color: {val_fg};"
            f" background-color: {BG_INSET}; border: 1px solid {_BORDER_NORMAL};"
            f" border-radius: 3px; font-family: Consolas; }}"
        )

        # Status
        if self._status in ("BAD", "ERROR"):
            self._status_label.setText(self._status)
            self._status_label.setStyleSheet(
                f"QLabel {{ font-size: 8pt; font-weight: bold;"
                f" color: {_STATUS_BAD_COLOR}; }}"
            )
        else:
            self._status_label.setText("GOOD")
            self._status_label.setStyleSheet(
                f"QLabel {{ font-size: 8pt; font-weight: bold;"
                f" color: {_STATUS_GOOD_COLOR}; }}"
            )


# =====================================================================
# 3. AOFaceplatePopup
# =====================================================================
class AOFaceplatePopup(QDialog):
    """ISA-101 analog output faceplate with operator-settable output.

    Shows a vertical bar graph, digital readout, output spinbox with
    Apply button, and mode indicator.
    """

    output_changed = Signal(str, float)   # (tag, value)
    mode_changed = Signal(str)            # "AUTO" or "MANUAL"

    def __init__(
        self,
        tag: str,
        description: str,
        out_lo: float = 0.0,
        out_hi: float = 100.0,
        units: str = "",
        decimals: int = 1,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._tag = tag
        self._description = description
        self._out_lo = out_lo
        self._out_hi = out_hi
        self._units = units
        self._decimals = decimals
        self._value: float = out_lo
        self._mode: str = "AUTO"
        self._pinned: bool = False

        self.setWindowTitle(f"{tag} \u2014 {description}")
        self.setWindowFlags(
            Qt.Window | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint
        )
        self.resize(180, 360)
        self.setStyleSheet(f"QDialog {{ background-color: {_BODY_BG}; }}")
        self._build_ui()

    # ---- pin support ----
    def _on_pin_toggled(self, checked: bool) -> None:
        self._pinned = checked

    @property
    def pinned(self) -> bool:
        return self._pinned

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if (event.type() == event.Type.ActivationChange
                and not self.isActiveWindow()
                and not self._pinned):
            self.close()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # Pin button
        self._pin_btn = QPushButton("Pin")
        self._pin_btn.setCheckable(True)
        self._pin_btn.setFixedSize(32, 20)
        self._pin_btn.setStyleSheet(
            "QPushButton { font-size: 7pt; background: transparent;"
            f" color: {TEXT_ON_DARK}; border: 1px solid {TEXT_ON_DARK};"
            " border-radius: 2px; }"
            " QPushButton:checked { background: #CBD9E2; color: #0E3260;"
            " border: 1px solid #0E3260; }"
        )
        self._pin_btn.setToolTip("Pin window (keep on top, prevent auto-close)")
        self._pin_btn.toggled.connect(self._on_pin_toggled)

        # Header
        root.addWidget(_make_header(self._tag, self._description,
                                    pin_btn=self._pin_btn))

        # Mode badge row
        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_lbl = QLabel("Mode:")
        mode_lbl.setStyleSheet(
            f"QLabel {{ font-size: 8pt; color: {_TAG_COLOR}; }}"
        )
        self._mode_badge = _make_mode_badge(self._mode)
        mode_row.addWidget(mode_lbl)
        mode_row.addWidget(self._mode_badge)
        mode_row.addStretch()
        root.addLayout(mode_row)

        # Scale hi label
        hi_lbl = QLabel(f"{self._out_hi:.{self._decimals}f}")
        hi_lbl.setAlignment(Qt.AlignCenter)
        hi_lbl.setStyleSheet(
            f"QLabel {{ font-size: 7pt; color: {_TAG_COLOR}; }}"
        )
        root.addWidget(hi_lbl)

        # Bar graph
        self._bar = VerticalBarGraph(
            lo=self._out_lo, hi=self._out_hi,
            bar_color="#14696A",   # DV_OUT_FG -- blue-green for outputs
        )
        self._bar.setMinimumHeight(120)
        root.addWidget(self._bar, stretch=1, alignment=Qt.AlignHCenter)

        # Scale lo label
        lo_lbl = QLabel(f"{self._out_lo:.{self._decimals}f}")
        lo_lbl.setAlignment(Qt.AlignCenter)
        lo_lbl.setStyleSheet(
            f"QLabel {{ font-size: 7pt; color: {_TAG_COLOR}; }}"
        )
        root.addWidget(lo_lbl)

        root.addWidget(_Separator())

        # Digital value readout
        self._value_label = QLabel(f"{self._value:.{self._decimals}f}")
        self._value_label.setAlignment(Qt.AlignCenter)
        self._value_label.setFixedHeight(30)
        self._value_label.setStyleSheet(
            f"QLabel {{ font-size: 13pt; font-weight: bold; color: {_VALUE_COLOR};"
            f" background-color: {BG_INSET}; border: 1px solid {_BORDER_NORMAL};"
            f" border-radius: 3px; font-family: Consolas; }}"
        )
        root.addWidget(self._value_label)

        # Units label
        if self._units:
            units_lbl = QLabel(self._units)
            units_lbl.setAlignment(Qt.AlignCenter)
            units_lbl.setStyleSheet(
                f"QLabel {{ font-size: 8pt; color: {_TAG_COLOR}; }}"
            )
            root.addWidget(units_lbl)

        root.addWidget(_Separator())

        # Output spinbox + Apply
        sp_row = QHBoxLayout()
        sp_row.setSpacing(4)
        sp_lbl = QLabel("Output:")
        sp_lbl.setStyleSheet(
            f"QLabel {{ font-size: 8pt; color: {_TAG_COLOR}; }}"
        )
        self._out_spin = QDoubleSpinBox()
        self._out_spin.setRange(self._out_lo, self._out_hi)
        self._out_spin.setDecimals(self._decimals)
        self._out_spin.setSingleStep(10.0 ** (-self._decimals))
        self._out_spin.setValue(self._value)
        self._out_spin.setFixedWidth(80)
        self._out_spin.setStyleSheet(
            f"QDoubleSpinBox {{"
            f"  background-color: white; color: {_VALUE_COLOR};"
            f"  border: 1px solid {_BORDER_NORMAL}; border-radius: 2px;"
            f"  font-size: 9pt; font-family: Consolas; padding: 1px 3px;"
            f"}}"
        )
        self._apply_btn = QPushButton("Apply")
        self._apply_btn.setFixedSize(52, 24)
        self._apply_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {_HEADER_TOP}; color: white;"
            f"  border: 1px solid {_HEADER_BOT}; border-radius: 3px;"
            f"  font-size: 8pt; font-weight: bold;"
            f"}}"
            f"QPushButton:hover {{"
            f"  background-color: {_HEADER_BOT};"
            f"}}"
        )
        self._apply_btn.clicked.connect(self._on_apply)
        sp_row.addWidget(sp_lbl)
        sp_row.addWidget(self._out_spin)
        sp_row.addWidget(self._apply_btn)
        root.addLayout(sp_row)

        root.addWidget(_Separator())

        # Mode buttons
        mode_btn_row = QHBoxLayout()
        mode_btn_row.setSpacing(8)
        self._btn_auto = _make_cmd_button("AUTO", STATUS_AUTO)
        self._btn_man = _make_cmd_button("MANUAL", STATUS_MAN)
        self._btn_auto.clicked.connect(lambda: self._request_mode("AUTO"))
        self._btn_man.clicked.connect(lambda: self._request_mode("MANUAL"))
        mode_btn_row.addStretch()
        mode_btn_row.addWidget(self._btn_auto)
        mode_btn_row.addWidget(self._btn_man)
        mode_btn_row.addStretch()
        root.addLayout(mode_btn_row)

        root.addStretch()
        self._refresh_mode_buttons()

    # ---- internal ----
    def _on_apply(self) -> None:
        val = self._out_spin.value()
        self.output_changed.emit(self._tag, val)

    def _request_mode(self, mode: str) -> None:
        self._mode = mode
        _apply_mode_badge_style(self._mode_badge, mode)
        self._refresh_mode_buttons()
        self.mode_changed.emit(mode)

    def _refresh_mode_buttons(self) -> None:
        is_auto = self._mode.upper() == "AUTO"
        _apply_btn_style(self._btn_auto, STATUS_AUTO, is_auto)
        _apply_btn_style(self._btn_man, STATUS_MAN, not is_auto)
        self._btn_auto.setChecked(is_auto)
        self._btn_man.setChecked(not is_auto)
        # Spinbox + apply only enabled in MANUAL
        self._out_spin.setEnabled(not is_auto)
        self._apply_btn.setEnabled(not is_auto)

    # ---- public API ----
    def update_data(self, data: dict) -> None:
        """Read analog output value from data dict.

        Looks for ``io.<tag>.value`` or falls back to the raw tag.
        """
        prefix = f"io.{self._tag}"
        raw = data.get(f"{prefix}.value", data.get(self._tag))
        if raw is not None:
            self._value = float(raw)

        mode_val = data.get(f"{prefix}.mode")
        if mode_val is not None:
            self._mode = str(mode_val).upper()
            _apply_mode_badge_style(self._mode_badge, self._mode)
            self._refresh_mode_buttons()

        # Update widgets
        self._bar.set_value(self._value)
        self._value_label.setText(f"{self._value:.{self._decimals}f}")
        # Only update spinbox if user is not editing
        if not self._out_spin.hasFocus():
            self._out_spin.setValue(self._value)


# =====================================================================
# 4. ValveDiagnosticsDialog — valve detail / diagnostics popup
# =====================================================================

class _HorizontalBarGraph(QWidget):
    """Simple custom-painted horizontal bar graph (0-100%)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._value: float = 0.0
        self.setFixedHeight(20)
        self.setMinimumWidth(180)

    def set_value(self, v: float) -> None:
        self._value = max(0.0, min(100.0, v))
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        mx, my = 2, 2
        bw, bh = w - 2 * mx, h - 2 * my

        # Background
        p.fillRect(mx, my, bw, bh, QColor(_BAR_BG))
        p.setPen(QPen(QColor(_BORDER_NORMAL), 1))
        p.drawRect(mx, my, bw - 1, bh - 1)

        # Filled portion (left to right)
        frac = self._value / 100.0
        fill_w = int(bw * frac)
        if fill_w > 0:
            p.fillRect(mx + 1, my + 1, fill_w - 1, bh - 2, QColor("#2196F3"))

        p.end()


class ValveDiagnosticsDialog(QDialog):
    """ISA-101 valve diagnostics dialog.

    Shows position readout, configuration parameters, diagnostic counters,
    and a health status indicator.  Data keys follow the convention:

    - Position:    ``valve_<type>``  (0-1 fractional)
    - Diagnostics: ``valve_diag.<type>.<param>``
    - Config:      ``valve_cfg.<type>.<param>``

    where *type* is derived from the tag (e.g. ``valve_fuel`` -> ``fuel``).
    """

    def __init__(
        self,
        tag: str,
        description: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._tag = tag
        self._description = description
        # Derive valve type name: valve_fuel -> fuel, valve_air -> air, etc.
        self._valve_type = tag.replace("valve_", "") if tag.startswith("valve_") else tag

        self.setWindowTitle(f"Valve Diagnostics \u2014 {tag}")
        self.setWindowFlags(
            Qt.Window | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint
        )
        self.resize(400, 500)
        self.setStyleSheet(f"QDialog {{ background-color: {_BODY_BG}; }}")
        self._build_ui()

    # ------------------------------------------------------------------ build
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ---- Header ----
        root.addWidget(_make_header(
            f"Valve Diagnostics", self._tag))

        # ---- Position section ----
        pos_frame = self._make_section("Position")
        pos_lay = QVBoxLayout(pos_frame)
        pos_lay.setContentsMargins(8, 24, 8, 8)
        pos_lay.setSpacing(4)

        # Large digital readout
        readout_row = QHBoxLayout()
        readout_row.setSpacing(4)
        self._pos_readout = QLabel("0.0")
        self._pos_readout.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._pos_readout.setFixedHeight(36)
        self._pos_readout.setStyleSheet(
            f"QLabel {{ font-size: 18pt; font-weight: bold; color: {_VALUE_COLOR};"
            f" background-color: {BG_INSET}; border: 1px solid {_BORDER_NORMAL};"
            f" border-radius: 3px; font-family: Consolas;"
            f" padding-right: 6px; }}"
        )
        pct_lbl = QLabel("%")
        pct_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        pct_lbl.setStyleSheet(
            f"QLabel {{ font-size: 10pt; color: {_TAG_COLOR};"
            f" background: transparent; border: none; }}"
        )
        readout_row.addWidget(self._pos_readout, stretch=1)
        readout_row.addWidget(pct_lbl)
        pos_lay.addLayout(readout_row)

        # Horizontal bar graph
        self._pos_bar = _HorizontalBarGraph()
        pos_lay.addWidget(self._pos_bar)

        root.addWidget(pos_frame)

        # ---- Configuration section ----
        cfg_frame = self._make_section("Configuration")
        cfg_grid = QGridLayout(cfg_frame)
        cfg_grid.setContentsMargins(8, 24, 8, 8)
        cfg_grid.setHorizontalSpacing(12)
        cfg_grid.setVerticalSpacing(4)

        cfg_fields = [
            ("Characteristic:", "_cfg_characteristic"),
            ("Time constant:", "_cfg_time_const"),
            ("Fail position:", "_cfg_fail_pos"),
            ("Nonlinearity:", "_cfg_nonlinearity"),
            ("Deadband:", "_cfg_deadband"),
            ("Stiction:", "_cfg_stiction"),
            ("Hysteresis:", "_cfg_hysteresis"),
        ]
        for row, (label_text, attr_name) in enumerate(cfg_fields):
            lbl = QLabel(label_text)
            lbl.setStyleSheet(
                f"QLabel {{ font-size: 8pt; color: {_TAG_COLOR}; border: none; }}"
            )
            val = QLabel("\u2014")
            val.setStyleSheet(
                f"QLabel {{ font-size: 8pt; font-weight: bold; color: {_VALUE_COLOR};"
                f" border: none; font-family: Consolas; }}"
            )
            setattr(self, attr_name, val)
            cfg_grid.addWidget(lbl, row, 0)
            cfg_grid.addWidget(val, row, 1)

        root.addWidget(cfg_frame)

        # ---- Diagnostics section ----
        diag_frame = self._make_section("Diagnostics")
        diag_grid = QGridLayout(diag_frame)
        diag_grid.setContentsMargins(8, 24, 8, 8)
        diag_grid.setHorizontalSpacing(12)
        diag_grid.setVerticalSpacing(4)

        diag_fields = [
            ("Total travel:", "_diag_travel"),
            ("Reversals:", "_diag_reversals"),
            ("Time at saturation:", "_diag_sat_time"),
            ("Avg velocity:", "_diag_avg_vel"),
        ]
        for row, (label_text, attr_name) in enumerate(diag_fields):
            lbl = QLabel(label_text)
            lbl.setStyleSheet(
                f"QLabel {{ font-size: 8pt; color: {_TAG_COLOR}; border: none; }}"
            )
            val = QLabel("\u2014")
            val.setStyleSheet(
                f"QLabel {{ font-size: 8pt; font-weight: bold; color: {_VALUE_COLOR};"
                f" border: none; font-family: Consolas; }}"
            )
            setattr(self, attr_name, val)
            diag_grid.addWidget(lbl, row, 0)
            diag_grid.addWidget(val, row, 1)

        root.addWidget(diag_frame)

        # ---- Health indicator ----
        health_frame = self._make_section("Health")
        health_lay = QHBoxLayout(health_frame)
        health_lay.setContentsMargins(8, 24, 8, 8)

        self._health_bar = QLabel("GOOD")
        self._health_bar.setAlignment(Qt.AlignCenter)
        self._health_bar.setFixedHeight(26)
        self._health_bar.setStyleSheet(
            f"QLabel {{ font-size: 10pt; font-weight: bold;"
            f" color: white; background-color: {_STATUS_GOOD_COLOR};"
            f" border-radius: 3px; }}"
        )
        health_lay.addWidget(self._health_bar)

        root.addWidget(health_frame)

        # ---- Close button ----
        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setFixedSize(72, 28)
        close_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {BG_INSET}; color: {_TAG_COLOR};"
            f"  border: 1px solid {_BORDER_NORMAL}; border-radius: 3px;"
            f"  font-size: 9pt; font-weight: bold;"
            f"}}"
            f"QPushButton:hover {{"
            f"  background-color: {BG_RAISED}; color: {_VALUE_COLOR};"
            f"}}"
        )
        close_btn.clicked.connect(self.close)
        close_row.addWidget(close_btn)
        close_row.addStretch()
        root.addLayout(close_row)

    def _make_section(self, title: str) -> QFrame:
        """Create a titled group frame in ISA-101 style."""
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame {{"
            f"  background-color: {BG_PANEL};"
            f"  border: 1px solid {_BORDER_NORMAL};"
            f"  border-radius: 3px;"
            f"}}"
        )
        title_lbl = QLabel(title, frame)
        title_lbl.move(8, 3)
        title_lbl.setStyleSheet(
            f"QLabel {{ font-size: 8pt; font-weight: bold; color: {_HEADER_TOP};"
            f" background: transparent; border: none; }}"
        )
        return frame

    # ---------------------------------------------------------- data update
    def update_data(self, data: dict) -> None:
        """Refresh all fields from a data snapshot dict."""
        vtype = self._valve_type

        # -- Position --
        raw = data.get(self._tag)
        if raw is None:
            raw = data.get(f"io.{self._tag}.value")
        position_pct = 0.0
        if raw is not None:
            try:
                position_pct = float(raw) * 100.0
            except (TypeError, ValueError):
                pass
        self._pos_readout.setText(f"{position_pct:.1f}")
        self._pos_bar.set_value(position_pct)

        # -- Configuration --
        cfg_prefix = f"valve_cfg.{vtype}"
        characteristic = data.get(f"{cfg_prefix}.characteristic", "\u2014")
        self._cfg_characteristic.setText(str(characteristic))

        tc = data.get(f"{cfg_prefix}.time_constant")
        self._cfg_time_const.setText(f"{float(tc):.1f} s" if tc is not None else "\u2014")

        fail_pos = data.get(f"{cfg_prefix}.fail_position", "\u2014")
        self._cfg_fail_pos.setText(str(fail_pos))

        nl_enabled = data.get(f"{cfg_prefix}.nonlinearity_enabled")
        self._cfg_nonlinearity.setText(
            "enabled" if nl_enabled else ("disabled" if nl_enabled is not None else "\u2014")
        )

        db = data.get(f"{cfg_prefix}.deadband")
        self._cfg_deadband.setText(f"{float(db):.4f}" if db is not None else "\u2014")

        st = data.get(f"{cfg_prefix}.stiction")
        self._cfg_stiction.setText(f"{float(st):.4f}" if st is not None else "\u2014")

        hy = data.get(f"{cfg_prefix}.hysteresis")
        self._cfg_hysteresis.setText(f"{float(hy):.4f}" if hy is not None else "\u2014")

        # -- Diagnostics --
        diag_prefix = f"valve_diag.{vtype}"

        travel = data.get(f"{diag_prefix}.total_travel")
        self._diag_travel.setText(f"{float(travel):.2f}" if travel is not None else "\u2014")

        reversals = data.get(f"{diag_prefix}.reversal_count")
        rev_val = int(reversals) if reversals is not None else None
        self._diag_reversals.setText(str(rev_val) if rev_val is not None else "\u2014")

        sat_time = data.get(f"{diag_prefix}.saturation_time")
        self._diag_sat_time.setText(
            f"{float(sat_time):.1f} s" if sat_time is not None else "\u2014"
        )

        avg_vel = data.get(f"{diag_prefix}.avg_velocity")
        self._diag_avg_vel.setText(
            f"{float(avg_vel):.3f} /s" if avg_vel is not None else "\u2014"
        )

        # -- Health indicator --
        travel_val = float(travel) if travel is not None else 0.0
        rev_count = rev_val if rev_val is not None else 0

        if rev_count >= 50000 or travel_val >= 500.0:
            health_text = "MAINTENANCE"
            health_color = ALARM_CRITICAL
        elif rev_count >= 10000 or travel_val >= 100.0:
            health_text = "MONITOR"
            health_color = ALARM_WARN
        else:
            health_text = "GOOD"
            health_color = _STATUS_GOOD_COLOR

        self._health_bar.setText(health_text)
        self._health_bar.setStyleSheet(
            f"QLabel {{ font-size: 10pt; font-weight: bold;"
            f" color: white; background-color: {health_color};"
            f" border-radius: 3px; }}"
        )
