"""Azeo faceplate sections — the anatomy, as reusable parts.

Every Azeo module faceplate is the same eight-part stack in the same
order. AI_fp is the
plainest of them, so it is the one this module was built against; PID,
DL, EDC and the rest differ by *which* sections they carry and what
they bind, not by how any section is drawn.

    title  ▸  value  ▸  pv_bar  ▸  mode  ▸  trend  ▸  alarms
           ▸  unit   ▸  buttons

That fixed order is the point, and it is the same argument the repo
already makes about its own faceplates: the eye lands in the same place
on every one, so an operator reads the *plant* rather than re-reading
the layout. A faceplate that rearranged itself per module would make
every module a new thing to learn under stress.

**A class declares sections; it does not build them.** `FACEPLATE_LAYOUT`
on a PVM class is an ordered tuple of section names, and
`render.PvmFaceplateWidget` assembles exactly those. Adding PID's
faceplate is choosing a tuple, not writing a widget — which is what
stops the eleventh faceplate from being an eleventh layout.

Every section takes `(palette, ...)` and exposes `refresh(bound)`,
where `bound` is the `{key: Binding}` map the PVM class produced. No
section reaches for a block, a store or a graph: they render what they
are handed, which is what makes them testable without a plant.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from ..theme.roles import Role
from ..binding.result import UNRESOLVED
from azeo_control_trainer.core.strategy.model.terminal import Quality

#: `#######` — the shared placeholder for a value it cannot show.
#: Kept rather than a blank because a blank field reads as "nothing is
#: configured here" and this means "configured, no value yet".
NO_VALUE = "#######"

#: The six icon buttons on Loop_fp, in the manual's order.  The second
#: magnifier is deliberately a different action: it opens the associated DCC
#: block's faceplate, not the module detail display.
#:
#: **BMP symbols, never emoji.** This row shipped with 📈 and 🔔, which
#: are astral-plane and render as empty boxes in the faceplate font —
#: the same defect the console chrome had. And the check for it cannot
#: be `QFontMetrics.inFont`: headless, Qt resolves to DejaVu Sans and
#: reports False for glyphs that render fine on a console, so the test
#: asserts the code points are BMP instead.
ICON_BUTTONS = (
    ("detail", "▤", "Opens the module's detail display"),
    ("dcc", "▧", "Opens the associated DCC block faceplate"),
    ("primary", "▣", "Opens the module's primary control display"),
    ("studio", "⚙", "Opens the module in Control Designer"),
    ("history", "∿", "Opens Process History View"),
    ("ack", "✔", "Acknowledges all alarms in this faceplate's list"),
)


def result_of(bound, key):
    """One binding's result, or the inert Bad record."""
    binding = bound.get(key) if bound else None
    if binding is None or isinstance(binding, tuple):
        return UNRESOLVED
    return getattr(binding, "result", UNRESOLVED)


def _number(result, decimals: int = 1) -> str:
    if result is None or result.quality is Quality.BAD \
            or result.value is None:
        return NO_VALUE
    try:
        return f"{float(result.value):.{decimals}f}"
    except (TypeError, ValueError):
        return str(result.value)


class Section(QWidget):
    """Base: holds the palette, answers `refresh(bound)`."""

    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self._palette = palette
        self._styles: list = []

    def colour(self, role: Role) -> str:
        return self._palette[role]

    def style(self, widget, make) -> None:
        """Set a stylesheet, and remember HOW so a theme can replay it.

        `make` is a zero-argument callable that reads colours through
        `self.colour`, so replaying it after the palette moves yields
        the new theme's values. A stylesheet baked as a plain string at
        construction is a colour the widget can never be talked out of,
        which is exactly why nothing here re-skinned at runtime.
        """
        self._styles.append((widget, make))
        widget.setStyleSheet(make())

    def apply_theme(self, palette: dict) -> None:
        """Re-skin in place: swap the palette, replay every style."""
        self._palette = palette
        for widget, make in self._styles:
            widget.setStyleSheet(make())
        self.restyle()
        self.update()

    def restyle(self) -> None:                       # pragma: no cover
        """Hook for anything a stylesheet cannot carry (painted
        sections re-read `self._palette` on their next paint)."""

    def refresh(self, bound) -> None:                # pragma: no cover
        """Subclasses render `bound`; the base does nothing."""


# ----------------------------------------------------------- 1. title
class TitleBlock(Section):
    """Three centred lines: zone, module tag, description.

    The tag is the bold middle line because it is what an operator
    checks first — "am I looking at the right loop" precedes every
    other question a faceplate answers.
    """

    def __init__(self, palette, parent=None):
        super().__init__(palette, parent)
        self.setFixedHeight(48)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 3, 0, 2)
        lay.setSpacing(0)
        self.zone = QLabel("")
        self.tag = QLabel("")
        self.description = QLabel("")
        for label, font, role in (
                (self.zone, QFont("Segoe UI", 8), Role.TEXT_DIM),
                (self.tag, QFont("Segoe UI", 11, QFont.Bold), Role.TEXT),
                (self.description, QFont("Segoe UI", 8),
                 Role.TEXT_DIM)):
            label.setFont(font)
            label.setAlignment(Qt.AlignCenter)
            self.style(label,
                       lambda r=role: f"color: {self.colour(r)};")
            lay.addWidget(label)

    def set_identity(self, tag: str, zone: str = "",
                     description: str = "") -> None:
        self.zone.setText(zone)
        self.tag.setText(tag)
        self.description.setText(description)
        self.zone.setVisible(bool(zone))
        self.description.setVisible(bool(description))


# ----------------------------------------------------------- 2. value
class ValueBlock(Section):
    """PV, its engineering units, and a secondary value.

    **The PV text is coloured by its STATUS, not by its value** — blue
    when good, red when uncertain or bad (the manual states this
    explicitly). That is the one place on a faceplate where colour
    encodes trust rather than magnitude, and getting it backwards
    would make a healthy reading look like an alarm.
    """

    def __init__(self, palette, parent=None):
        super().__init__(palette, parent)
        self.setFixedHeight(50)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(4)
        column = QVBoxLayout()
        column.setSpacing(0)
        self.pv = QLabel(NO_VALUE)
        self.pv.setFont(QFont("Consolas", 14, QFont.Bold))
        self.units = QLabel("")
        self.units.setFont(QFont("Segoe UI", 8))
        self.style(self.units,
                   lambda: f"color: {self.colour(Role.TEXT_DIM)};")
        self.secondary = QLabel("")
        self.secondary.setFont(QFont("Consolas", 9))
        self.style(self.secondary,
                   lambda: f"color: {self.colour(Role.TEXT_DIM)};")
        self.last_good = QLabel("")
        self.last_good.setFont(QFont("Consolas", 8))
        self.style(self.last_good,
                   lambda: f"color: {self.colour(Role.ALARM_P2_TEXT)};")
        self.last_good.hide()
        for widget in (self.pv, self.units, self.secondary,
                       self.last_good):
            column.addWidget(widget)
        lay.addLayout(column, 1)

        badges = QVBoxLayout()
        badges.setSpacing(2)
        #: Ⓢ — visible only on Simulation Active, and never while Not
        #: Running or Bad IO is showing (the manual's own precedence).
        self.simulate = QLabel("Ⓢ")
        self.simulate.setToolTip("Simulation Active")
        self.simulate.setFont(QFont("Segoe UI", 10))
        self.style(self.simulate,
                   lambda: f"color: {self.colour(Role.ALARM_P2_TEXT)};")
        self.simulate.hide()
        badges.addWidget(self.simulate, 0, Qt.AlignRight)
        self.forced = QLabel("FORCED")
        self.forced.setFont(QFont("Segoe UI", 7, QFont.Bold))
        self.style(self.forced, lambda: (
            f"color: {self.colour(Role.ACTION_DEEP)};"
            f"border: 1px solid {self.colour(Role.ACTION_DEEP)};"
            "padding: 0 3px;"))
        self.forced.hide()
        badges.addWidget(self.forced, 0, Qt.AlignRight)
        self.mini_button = QPushButton("^")
        self.mini_button.setToolTip(
            "Mini faceplate — the module's critical values only")
        self.mini_button.setFixedSize(18, 14)
        badges.addWidget(self.mini_button, 0, Qt.AlignRight)
        badges.addStretch(1)
        lay.addLayout(badges)

    def refresh(self, bound) -> None:
        pv = result_of(bound, "pv.value")
        self.pv.setText(_number(pv))
        self.units.setText(pv.units or "")
        good = pv.quality is Quality.GOOD and pv.value is not None
        value_role = Role.HEADING if good else Role.ALARM_P1_TEXT
        self.pv.setStyleSheet(f"color: {self.colour(value_role)};")
        field = result_of(bound, "field.value")
        self.secondary.setText(
            _number(field) if field is not UNRESOLVED else "")
        self.secondary.setVisible(field is not UNRESOLVED)
        self.simulate.setVisible(
            bool(result_of(bound, "simulate").value))
        self.forced.setVisible(bool(pv.forced))
        if pv.quality is Quality.BAD and pv.last_good_value is not None:
            import datetime
            try:
                value = f"{float(pv.last_good_value):g}"
            except (TypeError, ValueError):
                value = str(pv.last_good_value)
            at = datetime.datetime.fromtimestamp(
                pv.last_good_at).strftime("%H:%M:%S") \
                if pv.last_good_at else "?"
            self.last_good.setText(f"LAST GOOD  {value}  at {at}")
            self.last_good.show()
        else:
            self.last_good.hide()


# ---------------------------------------------------------- 3. pv bar
class PvBarGraph(Section):
    """The PV bar: scale, limits, PV fill and the working setpoint.

    Drawn rather than composed, because every mark on it has to share
    one coordinate mapping — a limit tick placed by a different
    calculation from the bar it annotates eventually disagrees with
    it, and a limit drawn in the wrong place is worse than no limit.

    Anatomy, top to bottom (manual, AI_fp page 2): PV scale EU100,
    HIHI, HI, working SP, PV bar, LO, LOLO, PV scale EU0, with major
    and minor scale lines down the left.
    """

    MAJOR_DIVISIONS = 4
    MINOR_PER_MAJOR = 2

    def __init__(self, palette, parent=None):
        super().__init__(palette, parent)
        self.value = None
        self.sp_value = None
        self.eu_range = (0.0, 100.0)
        self.units = ""
        self.limits: dict = {}
        #: Which limit the PV has actually breached, "" at rest. The
        #: bar marks it with a heavier tick rather than flooding with
        #: colour — the limit locations stay neutral (see paintEvent).
        self.breached = ""
        self.bad = False
        # AI_fp's measured body leaves 190 logical pixels for the scale.
        # An expanding minimum here was the main reason the old faceplate
        # grew to almost the height of the Studio window.
        self.setMinimumWidth(92)
        self.setFixedHeight(210)

    @property
    def fraction(self):
        """PV as 0..1 of scale, or None when it cannot be placed.

        Exposed because "where does the bar say the PV is" is the one
        thing worth asserting about a bar, and reading it off pixels
        is a test that breaks when the padding changes.
        """
        lo, hi = self.eu_range
        if self.value is None or self.bad or hi <= lo:
            return None
        try:
            return max(0.0, min(1.0, (float(self.value) - lo)
                                / (hi - lo)))
        except (TypeError, ValueError):
            return None

    def refresh(self, bound) -> None:
        pv = result_of(bound, "pv.value")
        self.value = pv.value
        self.bad = pv.quality is Quality.BAD
        self.units = pv.units or ""
        if pv.eu_range:
            self.eu_range = (float(pv.eu_range[0]),
                             float(pv.eu_range[1]))
        sp = result_of(bound, "sp.value")
        self.sp_value = sp.value if sp is not UNRESOLVED else None
        self.limits = {}
        for key, name in (("limits.hi_hi", "HIHI"), ("limits.hi", "HI"),
                          ("limits.lo", "LO"),
                          ("limits.lo_lo", "LOLO")):
            found = result_of(bound, key)
            if found.value is not None \
                    and found.quality is not Quality.BAD:
                try:
                    self.limits[name] = float(found.value)
                except (TypeError, ValueError):
                    pass
        # Which limit is actually breached — from the alarm record,
        # not by re-comparing the value here. Re-deriving it would let
        # the bar and the alarm list disagree about the same event.
        condition = (pv.alarm_condition or "").upper().replace("_", "")
        self.breached = condition if pv.alarm_active and condition in (
            "HIHI", "HI", "LO", "LOLO") else ""
        self.update()

    def _y_of(self, value, top: float, height: float):
        lo, hi = self.eu_range
        if value is None or hi <= lo:
            return None
        fraction = max(0.0, min(1.0, (float(value) - lo) / (hi - lo)))
        return top + height * (1.0 - fraction)

    def paintEvent(self, event) -> None:            # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        rect = self.rect()
        # Room for the EU labels above and below the bar.
        top, bottom = 16.0, rect.height() - 16.0
        height = bottom - top
        bar_w = 22.0
        bar_x = rect.width() - bar_w - 10.0

        # Scale lines down the left of the bar: major with a longer
        # tick, minor between them.
        painter.setPen(QPen(QColor(self.colour(Role.LINE)), 1))
        steps = self.MAJOR_DIVISIONS * self.MINOR_PER_MAJOR
        for i in range(steps + 1):
            y = top + height * i / steps
            major = i % self.MINOR_PER_MAJOR == 0
            length = 9.0 if major else 5.0
            painter.drawLine(int(bar_x - length), int(y),
                             int(bar_x - 2), int(y))

        # Track.
        painter.setPen(QPen(QColor(self.colour(Role.LINE)), 1))
        painter.setBrush(QColor(self.colour(Role.SURFACE_SUNK)))
        painter.drawRect(QRectF(bar_x, top, bar_w, height))

        # The regions beyond HIHI and below LOLO, drawn as bands so
        # "how much headroom is left" reads at a glance — a line answers
        # only "where is the threshold".
        #
        # **Neutral, not alarm-coloured.** The manual is explicit that
        # alarm limit locations are "shown subtly (such as in grays),
        # providing alarm limit information without being distracting
        # or creating excessive visual clutter", and the AI_fp figure
        # draws them as a pale cap. Painting them red would put alarm
        # colour on a faceplate that is not in alarm, which is the one
        # thing the whole style guide forbids.
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self.colour(Role.SURFACE_PANEL_ALT)))
        for outer in ("HIHI", "LOLO"):
            edge = self.limits.get(outer)
            if edge is None:
                continue
            y = self._y_of(edge, top, height)
            if y is None:
                continue
            if outer == "HIHI":
                painter.drawRect(QRectF(bar_x, top, bar_w, y - top))
            else:
                painter.drawRect(QRectF(bar_x, y, bar_w, bottom - y))

        # PV bar, filling from the bottom.
        #
        # **Outlined, not just filled — and that is what lets Azeo's
        # own colour be theme-invariant.** #7B92AD is a muted slate
        # that clears WCAG's 3:1 non-text minimum against Azeo's own
        # light track by only 2.47:1, and against hpgray's mid-grey
        # track by 1.73:1, where a bare fill all but vanishes.
        #
        # The fix is not to re-tint Azeo's colour per theme — it is
        # to give the level an EDGE, so the boundary is identified by
        # the outline and the fill only carries the colour. In `TEXT`
        # that boundary measures 9.2:1 at worst across the four themes
        # against 1.7:1 for the bare fill. Same fix the alarm marks
        # already use (fill plus outline) when a priority colour fails
        # against a particular ground, and it is why one bar colour can
        # serve every theme.
        y_pv = self._y_of(self.value, top, height) if not self.bad \
            else None
        if y_pv is not None:
            painter.setPen(QPen(QColor(self.colour(Role.TEXT)), 1))
            painter.setBrush(QColor(self.colour(Role.BAR_PV)))
            painter.drawRect(QRectF(bar_x + 1, y_pv,
                                    bar_w - 2, bottom - y_pv))

        # Limit ticks ON the bar, so a threshold reads against the fill.
        # A breached limit gets a heavier, solid tick — the emphasis is
        # WEIGHT, not colour, so the mark survives greyscale and does
        # not put alarm colour on a bar that is merely near a limit.
        for name in ("HIHI", "HI", "LO", "LOLO"):
            edge = self.limits.get(name)
            y = self._y_of(edge, top, height) if edge is not None else None
            if y is None:
                continue
            hit = name == self.breached
            painter.setPen(QPen(
                QColor(self.colour(
                    Role.ALARM_P1 if hit else Role.LINE)),
                2 if hit else 1,
                Qt.SolidLine if hit else Qt.DashLine))
            painter.drawLine(int(bar_x - (3 if hit else 0)), int(y),
                             int(bar_x + bar_w + (3 if hit else 0)),
                             int(y))

        # Working SP: a perpendicular mark that the PV bar meets, so
        # PV == SP forms the manual's 'T' and the eye reads agreement
        # as a SHAPE rather than by comparing two numbers.
        y_sp = self._y_of(self.sp_value, top, height)
        if y_sp is not None:
            painter.setPen(QPen(QColor(self.colour(Role.BAR_OUT)), 3))
            painter.drawLine(int(bar_x - 4), int(y_sp),
                             int(bar_x + bar_w + 4), int(y_sp))

        # EU100 above, EU0 below — the scale ends, in the manual's own
        # `#######` when they cannot be read.
        painter.setPen(QColor(self.colour(Role.TEXT_DIM)))
        painter.setFont(QFont("Consolas", 8))
        lo, hi = self.eu_range
        painter.drawText(QRectF(0, 0, rect.width() - 6, 14),
                         Qt.AlignRight | Qt.AlignVCenter,
                         f"{hi:g}" if hi is not None else NO_VALUE)
        painter.drawText(QRectF(0, bottom + 1, rect.width() - 6, 14),
                         Qt.AlignRight | Qt.AlignVCenter,
                         f"{lo:g}" if lo is not None else NO_VALUE)
        painter.end()


# ------------------------------------------------------------ 4. mode
class ModeRow(Section):
    """Actual mode, with `!` when it disagrees with the target.

    The exclamation is the manual's own mark and it earns its place:
    a loop whose target is CAS and whose actual is MAN is not in the
    state the engineer configured, and only the pair says so.
    """

    def __init__(self, palette, parent=None):
        super().__init__(palette, parent)
        self.setFixedHeight(24)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(4)
        self.flag = QLabel("!")
        self.flag.setFont(QFont("Segoe UI", 9, QFont.Bold))
        self.flag.setStyleSheet(
            f"color: {self.colour(Role.ACTION)};"
            f"border: 1px solid {self.colour(Role.ACTION)};"
            "padding: 0px 3px;")
        self.flag.hide()
        lay.addWidget(self.flag)
        self.mode = QLabel("")
        self.mode.setFont(QFont("Segoe UI", 9, QFont.Bold))
        lay.addWidget(self.mode)
        lay.addStretch(1)

    def refresh(self, bound) -> None:
        result = result_of(bound, "mode")
        actual = str(result.mode_actual or result.value or "")
        target = str(result.mode_target or "")
        self.mode.setText(actual)
        self.mode.setStyleSheet(f"color: {self.colour(Role.TEXT)};")
        self.flag.setVisible(bool(target and actual and target != actual))
        self.setToolTip(f"target {target} / actual {actual}"
                        if target else "")


# ----------------------------------------------------------- 5. trend
class TrendChart(Section):
    """A condensed trend — the manual's own words for it.

    Display-side history, because the model has no business buffering
    pixels; a faceplate opened now legitimately has no past.
    """

    CAPACITY = 120

    def __init__(self, palette, parent=None):
        super().__init__(palette, parent)
        from collections import deque
        self.history = deque(maxlen=self.CAPACITY)
        self.eu_range = (0.0, 100.0)
        self.setFixedHeight(46)

    def refresh(self, bound) -> None:
        pv = result_of(bound, "pv.value")
        if pv.eu_range:
            self.eu_range = (float(pv.eu_range[0]),
                             float(pv.eu_range[1]))
        if pv.quality is not Quality.BAD \
                and isinstance(pv.value, (int, float)):
            self.history.append(float(pv.value))
        self.update()

    def paintEvent(self, event) -> None:            # noqa: N802
        painter = QPainter(self)
        rect = self.rect()
        plot = QRectF(30, 2, rect.width() - 34, rect.height() - 4)
        painter.setPen(QPen(QColor(self.colour(Role.LINE)), 1))
        painter.setBrush(QColor(self.colour(Role.SURFACE_FIELD)))
        painter.drawRect(plot)
        lo, hi = self.eu_range
        painter.setPen(QColor(self.colour(Role.TEXT_DIM)))
        painter.setFont(QFont("Consolas", 7))
        painter.drawText(QRectF(0, 0, 27, 12),
                         Qt.AlignRight, f"{hi:g}")
        painter.drawText(QRectF(0, rect.height() - 13, 27, 12),
                         Qt.AlignRight, f"{lo:g}")
        if len(self.history) >= 2 and hi > lo:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(QPen(QColor(self.colour(Role.BAR_PV)), 1.4))
            step = plot.width() / max(len(self.history) - 1, 1)
            points = []
            for i, value in enumerate(self.history):
                fraction = max(0.0, min(1.0, (value - lo) / (hi - lo)))
                points.append((plot.left() + i * step,
                               plot.bottom() - plot.height() * fraction))
            for (x1, y1), (x2, y2) in zip(points, points[1:]):
                painter.drawLine(int(x1), int(y1), int(x2), int(y2))
        painter.end()


# ---------------------------------------------------------- 6. alarms
class AlarmList(Section):
    """Ack | Param | Help — the manual's three columns, in its order.

    The Ack column is a SYMBOL, not a colour: empty for
    active/unacknowledged, a check for acknowledged, an empty box for
    inactive/unacknowledged. Priority is already carried elsewhere;
    what this column says is whether anyone has answered.
    """

    ACK_SYMBOLS = {"active_unacked": "", "active_acked": "✓",
                   "inactive_unacked": "☐"}

    def __init__(self, palette, parent=None):
        super().__init__(palette, parent)
        self._bound = None
        self._records = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Ack", "Param", "Help"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.horizontalHeader().setFixedHeight(17)
        self.table.verticalHeader().setDefaultSectionSize(16)
        self.table.setColumnWidth(0, 31)
        self.table.setColumnWidth(2, 31)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setFixedHeight(53)
        self.style(self.table, lambda: (
            f"QTableWidget {{ background: {self.colour(Role.SURFACE_PANEL)};"
            f"gridline-color: {self.colour(Role.LINE)};"
            f"border: 1px solid {self.colour(Role.LINE)}; font-size: 6.75pt; }}"
            f"QHeaderView::section {{ background: "
            f"{self.colour(Role.SURFACE_PANEL)}; color: "
            f"{self.colour(Role.TEXT)}; border: 0; border-right: 1px solid "
            f"{self.colour(Role.LINE)}; border-bottom: 1px solid "
            f"{self.colour(Role.LINE)}; padding: 0 2px; }}"
            f"QScrollBar:vertical {{ width: 16px; background: "
            f"{self.colour(Role.SURFACE_FIELD)}; }}"))
        lay.addWidget(self.table)

    def refresh(self, bound) -> None:
        self._bound = bound
        self._render_rows()

    def set_records(self, records) -> None:
        """Use the host's complete module alarm rows when it has them.

        A BindingResult carries the worst alarm for the PVM mark; the compact
        list must carry *all* current alarms.  The shared runtime registry is
        therefore an optional second input rather than another alarm model.
        """
        self._records = tuple(records) if records is not None else None
        self._render_rows()

    def resizeEvent(self, event) -> None:           # noqa: N802
        fixed = self.table.columnWidth(0) + self.table.columnWidth(2)
        scrollbar = self.table.verticalScrollBar().sizeHint().width() \
            if self.table.verticalScrollBar().isVisible() else 0
        self.table.setColumnWidth(
            1, max(40, self.table.viewport().width() - fixed - scrollbar))
        super().resizeEvent(event)

    def _render_rows(self) -> None:
        if self._bound is None:
            return
        pv = result_of(self._bound, "pv.value")
        rows = []
        if self._records is not None:
            for record in self._records:
                state = "active_acked" if record.active \
                    and record.acknowledged else (
                        "active_unacked" if record.active
                        else "inactive_unacked")
                rows.append((self.ACK_SYMBOLS[state], record.condition,
                             "", int(record.priority or 0)))
        elif pv.alarm_active:
            rows.append((
                self.ACK_SYMBOLS["active_acked" if pv.alarm_acked
                                 else "active_unacked"],
                pv.alarm_condition or "ALARM", "",
                int(pv.alarm_priority or 0)))
        if pv.quality is Quality.BAD:
            rows.append((self.ACK_SYMBOLS["active_unacked"],
                         "BAD IO", "", 15))
        self.table.setRowCount(len(rows))
        for i, (ack, param, help_icon, priority) in enumerate(rows):
            for column, text in enumerate((ack, param, help_icon)):
                cell = QTableWidgetItem(text)
                if column == 1:
                    role = Role.ALARM_P1_TEXT if priority >= 15 else (
                        Role.ALARM_P2_TEXT if priority >= 11
                        else Role.ALARM_P3_TEXT)
                    cell.setForeground(QColor(self.colour(role)))
                self.table.setItem(i, column, cell)


# ------------------------------------------------------------ 7. unit
class UnitName(Section):
    """`Unit: <name>` — which plant unit this module belongs to."""

    def __init__(self, palette, parent=None):
        super().__init__(palette, parent)
        self.setFixedHeight(18)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 0, 2, 0)
        self.label = QLabel("Unit:")
        self.label.setFont(QFont("Segoe UI", 8))
        self.style(self.label,
                   lambda: f"color: {self.colour(Role.TEXT_DIM)};")
        lay.addWidget(self.label)
        lay.addStretch(1)

    def set_unit(self, name: str) -> None:
        self.label.setText(f"Unit: {name}" if name else "Unit:")


# --------------------------------------------------------- 8. buttons
class FaceplateActionButton(QPushButton):
    """A 30 px faceplate action button: a themed rounded-square shell around the
    shared symbol set in ``faceplate_icons``, so every faceplate row shows the
    same mark for the same action."""

    def __init__(self, key: str, tip: str, palette: dict, parent=None):
        super().__init__("", parent)
        self.key = key
        self._palette = palette
        self.setToolTip(tip)
        self.setAccessibleName(tip)
        self.setFixedSize(30, 30)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)

    def apply_theme(self, palette: dict) -> None:
        self._palette = palette
        self.update()

    def paintEvent(self, event) -> None:            # noqa: N802
        from .faceplate_icons import ACTION_ICON_NAMES, draw_faceplate_icon

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        if not self.isEnabled():
            painter.setOpacity(0.42)
        if self.isDown():
            painter.translate(0, 1)
        box = QRectF(1.5, 1.5, 27.0, 27.0)
        painter.setPen(QPen(QColor(self._palette[Role.LINE]), 1))
        painter.setBrush(QColor(self._palette[Role.SURFACE_FIELD]))
        painter.drawRoundedRect(box, 7, 7)
        if self.underMouse() and self.isEnabled():
            painter.setPen(QPen(QColor(self._palette[Role.ACTION]), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(box.adjusted(1, 1, -1, -1), 6, 6)
        draw_faceplate_icon(painter, QRectF(2, 2, 26, 26),
                            ACTION_ICON_NAMES.get(self.key, "module_detail"), button=False)
        if self.hasFocus():
            painter.setPen(QPen(QColor(self._palette[Role.TEXT_DIM]), 1,
                                Qt.DotLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(box.adjusted(3, 3, -3, -3), 5, 5)
        painter.end()

class IconButtonRow(Section):
    """The faceplate action catalogue, filtered by profile and host.

    Order is fixed across every faceplate for the same reason the
    section order is: an operator's hand learns a position, and a
    button that moves per module is a button that gets mis-hit.
    """

    #: Emitted with the button's key when one is pressed. The row does
    #: not act on its own — what "open the detail display" means is the
    #: host's business, not a section's.
    pressed = Signal(str)

    def __init__(self, palette, parent=None):
        super().__init__(palette, parent)
        self.setFixedHeight(31)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 1, 0, 0)
        lay.setSpacing(3)
        self.buttons: dict = {}
        self._tips: dict = {}
        for key, glyph, tip in ICON_BUTTONS:
            button = FaceplateActionButton(key, tip, palette, self)
            button.clicked.connect(
                lambda _checked=False, k=key: self.pressed.emit(k))
            self.buttons[key] = button
            self._tips[key] = tip
            lay.addWidget(button)
        lay.addStretch(1)
        # Nothing is serviceable until a host says so. Every one of
        # these was drawn live and wired to nothing, which is the
        # failure the repo already names for command terminals: a
        # button that looks live and does nothing is worse than one
        # that is visibly out of reach.
        self.set_available(())

    def restyle(self) -> None:
        for button in self.buttons.values():
            button.apply_theme(self._palette)

    def set_visible_actions(self, keys) -> None:
        """Show only the buttons carried by this Azeo faceplate.

        Loop_fp defines the six-action catalogue; the host reduces that set to
        actions it can actually service. Other compact function-block
        faceplates generally carry only Detail. Keeping unavailable actions
        on screen made the compact shells look unfinished.
        """
        keys = set(keys or ())
        for key, button in self.buttons.items():
            button.setVisible(key in keys)

    def set_available(self, keys) -> None:
        """Enable exactly the actions the host can service.

        A disabled button keeps its glyph and says WHY in its tooltip —
        the operator's question is "can I get the detail display from
        here", and a greyed button with a reason answers it where a
        live button that swallows the click does not.
        """
        keys = set(keys or ())
        for key, button in self.buttons.items():
            ok = key in keys
            button.setEnabled(ok)
            button.setToolTip(
                self._tips[key] if ok
                else f"{self._tips[key]} — not available here")


#: name -> section class. `render.PvmFaceplateWidget` builds a class's
#: `FACEPLATE_LAYOUT` from this, so a new faceplate is a tuple of these
#: names rather than a new widget.
SECTIONS = {
    "title": TitleBlock,
    "value": ValueBlock,
    "pv_bar": PvBarGraph,
    "mode": ModeRow,
    "trend": TrendChart,
    "alarms": AlarmList,
    "unit": UnitName,
    "buttons": IconButtonRow,
}

#: The order Azeo stacks them in. A class may omit sections; it may
#: not reorder them, and `build_sections` enforces that — see the
#: module docstring for why.
CANONICAL_ORDER = ("title", "value", "pv_bar", "mode", "trend",
                   "alarms", "unit", "buttons")

#: Sections that sit BESIDE the one before them rather than under it.
#: In the AI_fp figure the actual mode is annotated at the bar's right
#: shoulder, not below it — the bar is tall and the mode is one short
#: word, so stacking them wastes the height the bar needs. The renderer
#: pairs these into a row; everything else stacks.
BESIDE_PREVIOUS = {"mode"}


class LayoutError(ValueError):
    """A faceplate layout that would not read like the others."""


def register_section(name: str, section_cls, after: str) -> None:
    """Add a section to the shared library, at a fixed place in the order.

    Sections may be contributed from other modules (`faceplate_fb` adds
    the function-block bodies), but the CANONICAL ORDER stays one list
    with one owner. A contributor names what it sits *after* rather
    than an index, so inserting one does not silently renumber the
    others — and `build_sections` keeps refusing any layout that
    reorders them, which is the whole value of a section library.
    """
    if name in SECTIONS:
        raise LayoutError("section %r is already registered" % name)
    if after not in CANONICAL_ORDER:
        raise LayoutError("cannot place %r after unknown section %r"
                          % (name, after))
    SECTIONS[name] = section_cls
    order = list(CANONICAL_ORDER)
    order.insert(order.index(after) + 1, name)
    globals()["CANONICAL_ORDER"] = tuple(order)


def build_sections(names, palette) -> dict:
    """{name: widget} for a class's declared layout.

    Refuses an unknown section and refuses a re-ordering: the shared
    order is the whole value of a section library, and a faceplate
    that puts its trend above its bar has quietly become a bespoke
    layout again.
    """
    wanted = tuple(names or ())
    unknown = [n for n in wanted if n not in SECTIONS]
    if unknown:
        raise LayoutError(f"unknown faceplate section(s): {unknown}")
    ordered = [n for n in CANONICAL_ORDER if n in wanted]
    if ordered != list(wanted):
        raise LayoutError(
            f"faceplate sections must keep the canonical order "
            f"{CANONICAL_ORDER}; got {wanted}")
    return {name: SECTIONS[name](palette) for name in ordered}
