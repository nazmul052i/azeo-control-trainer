"""Console chrome: identity row, alarm banner, status bar, soft keys.

Invariant I5: chrome is a **runtime component**. It is never serialised into
a display file, so a display cannot draw its own header and an operator
cannot lose the alarm banner by opening the wrong graphic. In Studio the same
widgets render as a locked frame around the canvas.

The budget rule from `docs/console/07` is the one number to keep in
view: **chrome must be at most 12 % of vertical pixels.** Header
pixels are spent on every
display, permanently, and the default configuration here comes to 106 px —
9.8 % of a 1920×1080 console. Turning the soft keys on costs another 32 px
and takes it to 12.8 %, which is why they are opt-in and why
:func:`chrome_height` exists as a function anyone can assert on.

What is deliberately absent, per the same doc: logo bar, menu bar, unlabelled
icon toolbar, breadcrumb as its own strip, print button, and a green
"system OK" lamp. That last one is the instructive omission — a lamp that is
green 99.9 % of the time trains the eye to skip exactly where the red will
appear.

**Two of those absences are restored by `ConsoleSettings.azeo_chrome`**,
which swaps this arrangement for Azeo Operator Station's own — a menu bar and an
unlabelled icon toolbar, both named above as things `docs/console/07`
omits. It is opt-in and it is a *training* choice: a trainee who will
sit at Azeo
should learn Azeo's furniture. `chrome_height` reports the real cost of
either arrangement, so the budget stays assertable in both.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QPainter, QPolygon
from PySide6.QtWidgets import QWidget

from azeo_control_trainer.core.hmi.model.alarms import Banner, Priority
from azeo_control_trainer.core.hmi.theme.fonts import FontRole, font_for
from azeo_control_trainer.core.hmi.theme.palette import RolePalette
from azeo_control_trainer.core.hmi.theme.roles import Role

#: Zone heights (`docs/console/07`). Fixed: chrome geometry is
#: byte-identical on every display, so no per-display variation is
#: possible.
IDENTITY_H = 30
BANNER_LINE_H = 26
STATUS_H = 24
SOFT_KEY_H = 32

#: Fraction of vertical pixels chrome may occupy before it needs a written
#: justification.
CHROME_BUDGET = 0.12


@dataclass(frozen=True)
class ConsoleSettings:
    """Everything about the console that is not about the display."""

    console_id: str = "CON-01"
    #: Span of control — which units this seat is responsible for. Shown
    #: because control rooms are shared and "who has this" is not obvious.
    span: str = "NGL TRAIN"
    user: str = "operator"
    #: `OPERATE` or `VIEW ONLY`. The latter has to be unmistakable: an
    #: operator who thinks they can act and cannot is worse off than one who
    #: knows they cannot.
    write_authority: bool = True
    #: Banner depth. One line fits the budget; two survive a flood without
    #: navigating away; three is a summary display in costume
    #: (`docs/console/07`).
    banner_lines: int = 2
    soft_keys: bool = False
    #: When off, unacknowledged alarms take a 2 px outline instead. Never
    #: leave unacknowledged looking the same as acknowledged.
    blink_enabled: bool = True
    theme: str = "silver"
    #: Azeo Operator Station's own furniture: a menu bar (which REPLACES the
    #: identity row — Azeo carries node and user on its right) plus a
    #: navigation bar. Opt-in, because it costs budget: see
    #: `chrome_height`, and `runtime/chrome_azeo.py` for what it is
    #: and which parts of `docs/console/07` it deliberately contradicts.
    azeo_chrome: bool = False
    comfortable: bool = True
    dock_context: bool = True


#: Azeo's rows (`runtime/chrome_azeo.py`). Declared here so
#: `chrome_height` stays the ONE place that knows what chrome costs —
#: a second height function is how a budget stops being enforceable.
AZEO_MENU_H = 30
AZEO_NAV_H = 26
OPERATOR_COMFORTABLE_H = 42
OPERATOR_COMPACT_H = 34


def chrome_height(settings: ConsoleSettings) -> int:
    """Total vertical pixels chrome takes from the process content.

    The Azeo arrangement swaps the identity row for a menu bar and
    adds a navigation bar, so it costs the navigation bar plus the
    difference between the two — not both rows outright.
    """
    menu_height = OPERATOR_COMFORTABLE_H if settings.comfortable else OPERATOR_COMPACT_H
    top = (menu_height + AZEO_NAV_H if settings.azeo_chrome
           else IDENTITY_H)
    return (top + BANNER_LINE_H * settings.banner_lines + STATUS_H
            + (SOFT_KEY_H if settings.soft_keys else 0))


def within_budget(settings: ConsoleSettings, screen_height: int) -> bool:
    return chrome_height(settings) <= CHROME_BUDGET * screen_height


# ------------------------------------------------------------------- status
class Exception_(str, Enum):
    """Status-bar states that are silent until abnormal.

    Keeping these apart from the always-present items is the whole design of
    the status bar. Something that is always there says nothing by being
    there; something that appears means something appeared. Mixing the two
    populations is the usual mistake.

    Controller failover is the instructive case: control is maintained, so it
    is not an alarm — but redundancy is gone, so it cannot be silent. That is
    exactly what a status bar is for and what an alarm list handles badly.
    """

    FAILOVER = "CONTROLLER FAILOVER — REDUNDANCY LOST"
    HISTORIAN = "HISTORIAN LAG"
    MOC = "PENDING MOC ON THIS DISPLAY"
    DEGRADED = "NETWORK DEGRADED"
    SIMULATION = "SIMULATION MODE — NOT THE PLANT"


@dataclass(frozen=True)
class ConsoleStatus:
    """What the status bar knows. A plain record, so it can be asserted on."""

    server_time: float = 0.0
    #: Server time of the last snapshot. Age is computed from it, and age is
    #: the most important item on the bar.
    last_update: float | None = None
    scan_ms: int = 1000
    display_id: str = ""
    revision: int = 0
    moc: str | None = None
    #: Alarms per hour over the rolling window. `None` means not measured —
    #: which is different from zero, and is shown by leaving the cell out.
    alarm_rate: float | None = None
    exceptions: tuple[Exception_, ...] = field(default_factory=tuple)
    source_state: str = ""

    @property
    def age(self) -> float | None:
        if self.last_update is None:
            return None
        return max(0.0, self.server_time - self.last_update)

    @property
    def stale(self) -> bool:
        """Three missed scans, floor two seconds.

        A threshold rather than a boolean from the driver: the console can
        see staleness the driver cannot, because the driver is the thing that
        has stopped.
        """
        if self.source_state in {"STALE", "DISCONNECTED", "PARTLY PAUSED"}:
            return True
        if self.source_state == "PAUSED":
            return False
        age = self.age
        if age is None:
            return True
        return age > max(2.0, 3.0 * self.scan_ms / 1000.0)

    def liveness(self) -> str:
        age = self.age
        if self.source_state in {"PAUSED", "DISCONNECTED", "PARTLY PAUSED"}:
            return self.source_state
        if age is None:
            return "NO DATA"
        return (f"DATA STALE {age:.0f} s" if self.stale
                else f"LIVE {age:.1f} s")


def clock(server_time: float) -> str:
    """Controller clock as `HH:MM:SS`, without asking the workstation.

    Operators timestamp log entries from displayed time and those entries end
    up in incident timelines. A workstation with drifted NTP writes a shift
    log that disagrees with the historian, so this formats the *server* value
    it was handed and never reads a clock of its own.
    """
    seconds = int(server_time) % 86400
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


# ------------------------------------------------------------------ widgets
class _ChromeWidget(QWidget):
    """Shared painting setup. Chrome does not scroll and does not resize its
    text — its height is fixed by `docs/console/07`."""

    HEIGHT = 24

    def __init__(self, settings: ConsoleSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.palette_ = RolePalette.for_theme(settings.theme)
        self.setFixedHeight(self.HEIGHT)
        self.setAutoFillBackground(False)

    def _ground(self, painter: QPainter) -> QRect:
        rect = self.rect()
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(rect, self.palette_.brush(Role.SURFACE_PANEL))
        painter.setPen(self.palette_.pen(Role.LINE, 1.0))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(),
                         rect.bottom())
        return rect

    def _text(self, painter: QPainter, rect: QRect, text: str,
              role: Role = Role.TEXT, align=Qt.AlignLeft | Qt.AlignVCenter,
              font: FontRole = FontRole.CHROME) -> None:
        painter.setFont(font_for(font))
        painter.setPen(self.palette_.pen(role, 1.0))
        painter.drawText(rect, align, text)

def stamp(server_time: float) -> str:
    """Server time as a date *and* a clock, when the value is a real one.

    A shift log entry is dated as well as timed, so the bar carries both —
    but only when the number it was handed is plainly wall-clock. A console
    driven from a counter gets the counter rendered as elapsed time instead;
    inventing a date for it would put a wrong date in an incident timeline,
    which is worse than showing no date at all.
    """
    if server_time < 1e9:                                  # not an epoch
        return clock(server_time)
    from time import localtime, strftime

    return strftime("%Y-%m-%d %H:%M:%S", localtime(server_time))


def _mark(painter: QPainter, palette: RolePalette, rect: QRect,
          priority: int, *, filled: bool = True, hollow: bool = False,
          heavy: bool = False) -> None:
    """The priority silhouette: ▲ P1, ◆ P2, ■ P3 — invariant I4.

    Shape as well as colour, in the same three silhouettes the dynamo mark
    uses, so the banner and the graphic agree without an operator having to
    learn two vocabularies. Shape is what survives greyscale, a failing
    projector lamp, and a photograph in an incident report.

    Two independent channels, and keeping them independent is the point:

    - ``heavy`` — a 2 px outline, and it means **unacknowledged**. It is
      structural, so an incident-report screenshot still says which alarms
      nobody had answered. A phase has no meaning in a still image.
    - ``hollow`` — the dark half of the blink. Cosmetic, and safe to lose:
      turning blink off leaves the heavy outline doing the work on its own.
    """
    size = min(14, rect.height() - 2)
    left = rect.left()
    top = rect.top() + (rect.height() - size) // 2
    role = {1: Role.ALARM_P1, 2: Role.ALARM_P2}.get(priority, Role.ALARM_P3)

    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(palette.pen(role, 2.0 if heavy else 1.0))
    painter.setBrush(Qt.NoBrush if hollow or not filled
                     else palette.brush(role))
    half = size // 2
    if priority == 1:
        painter.drawPolygon(QPolygon([
            QPoint(left + half, top), QPoint(left + size, top + size),
            QPoint(left, top + size)]))
    elif priority == 2:
        painter.drawPolygon(QPolygon([
            QPoint(left + half, top), QPoint(left + size, top + half),
            QPoint(left + half, top + size), QPoint(left, top + half)]))
    else:
        painter.drawRect(QRect(left, top, size, size))
    painter.restore()


class IdentityRow(_ChromeWidget):
    """Where am I, and how do I get somewhere else.

    Navigation lives here rather than in its own strip: a breadcrumb bar is
    another 24 px on every display forever, and the same information fits
    beside the display name.
    """

    HEIGHT = IDENTITY_H
    navigate = Signal(str)          # "back" | "forward" | "parent" | "home"

    BUTTONS = (("back", "◀"), ("forward", "▶"), ("parent", "▲"), ("home", "⌂"))
    BUTTON_W = 26
    BADGE_W = 24

    def __init__(self, settings: ConsoleSettings, parent=None):
        super().__init__(settings, parent)
        self.meta = None
        self.can_go = {"back": False, "forward": False, "parent": False,
                       "home": True}

    def set_display(self, meta) -> None:
        self.meta = meta
        self.update()

    def button_rects(self) -> dict[str, QRect]:
        return {name: QRect(6 + index * self.BUTTON_W, 3, self.BUTTON_W - 2,
                            self.HEIGHT - 6)
                for index, (name, _) in enumerate(self.BUTTONS)}

    def hit_test(self, position) -> str | None:
        for name, rect in self.button_rects().items():
            if rect.contains(position):
                return name
        return None

    def mousePressEvent(self, event):             # noqa: N802 (Qt naming)
        action = self.hit_test(event.position().toPoint())
        if action and self.can_go.get(action, False):
            self.navigate.emit(action)

    def meta_text(self) -> str:
        """Area, span of control and console id. Public so a test can read it.

        Span is only worth the pixels when it differs from the area:
        `AZEO_MODBUS · AZEO MODBUS · CON-01` reads as a rendering fault
        rather than as a span of control.
        """
        parts = [self.meta.area] if self.meta is not None else []
        if self.settings.span and (self.meta is None
                                   or self.settings.span != self.meta.area):
            parts.append(self.settings.span)
        parts.append(self.settings.console_id)
        return " · ".join(parts)

    def paintEvent(self, event):                  # noqa: N802
        painter = QPainter(self)
        rect = self._ground(painter)

        for name, glyph in self.BUTTONS:
            cell = self.button_rects()[name]
            enabled = self.can_go.get(name, False)
            painter.setBrush(self.palette_.brush(Role.SURFACE_FIELD))
            painter.setPen(self.palette_.pen(Role.LINE, 1.0))
            painter.drawRect(cell)
            self._text(painter, cell, glyph,
                       Role.TEXT if enabled else Role.TEXT_FAINT,
                       Qt.AlignCenter)

        left = 6 + len(self.BUTTONS) * self.BUTTON_W + 6
        if self.meta is None:
            painter.end()
            return

        # Outlined in the action colour rather than filled: the badge says
        # what *kind* of answer this screen can give, and it has to read as a
        # label rather than as a state.
        badge = QRect(left, 6, self.BADGE_W, self.HEIGHT - 12)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(self.palette_.pen(Role.ACTION, 1.0))
        painter.drawRect(badge)
        self._text(painter, badge, f"L{self.meta.level}", Role.ACTION,
                   Qt.AlignCenter, FontRole.CHROME_MONO)

        meta = self.meta_text()
        painter.setFont(font_for(FontRole.CHROME_MONO))
        meta_w = painter.fontMetrics().horizontalAdvance(meta) + 16

        name_rect = QRect(left + self.BADGE_W + 8, 0,
                          max(40, rect.width() - left - self.BADGE_W
                              - meta_w - 16), self.HEIGHT)
        painter.setFont(font_for(FontRole.CHROME_BOLD))
        # Elided, not clipped: a name that runs under the meta column looks
        # like two fields overlapping rather than like one long name.
        shown = painter.fontMetrics().elidedText(
            self.meta.name, Qt.ElideRight, name_rect.width())
        self._text(painter, name_rect, shown, Role.TEXT,
                   font=FontRole.CHROME_BOLD)

        self._text(painter, QRect(rect.width() - meta_w - 8, 0, meta_w,
                                  self.HEIGHT),
                   meta, Role.TEXT_DIM, Qt.AlignRight | Qt.AlignVCenter,
                   FontRole.CHROME_MONO)
        painter.end()


class AlarmBanner(_ChromeWidget):
    """Counts by priority, then the highest-priority unacknowledged lines.

    Three columns, and the split is the point. **Counts** answer "how bad is
    it" without reading; **lines** answer "what is it"; **actions** are in
    the same place on every display so acknowledging never involves aiming.

    Never blank: `No active alarms` is a statement, and blank is ambiguous
    with a banner that has failed.
    """

    acknowledge = Signal()
    open_summary = Signal()
    silence = Signal(bool)
    help_requested = Signal()

    COUNT_W = 62
    BUTTON_W = 84
    MARK_W = 16

    def __init__(self, settings: ConsoleSettings, parent=None):
        super().__init__(settings, parent)
        self.setFixedHeight(BANNER_LINE_H * settings.banner_lines)
        self.banner: Banner | None = None
        self.blink_on = True
        #: **Silence is a STATE, not a horn.** There is no audible
        #: annunciator in this trainer, so a speaker button would be a
        #: control with nothing behind it — the exact thing an
        #: unlabelled toolbar gets wrong. Instead it latches, the
        #: banner SAYS so, and it clears itself when a new alarm
        #: arrives: a silence that outlived the alarm it silenced is
        #: how the next one gets missed.
        self.silenced = False
        self._silenced_at_count = 0

    def set_silenced(self, silenced: bool) -> None:
        self.silenced = bool(silenced)
        self._silenced_at_count = self._active_count()
        self.silence.emit(self.silenced)
        self.update()

    def _active_count(self) -> int:
        """How many alarms the banner is currently carrying.

        `Banner.total` already sums the priority counts. Using it
        rather than re-summing keeps ONE answer to "how many alarms" —
        the same reason the console shares one alarm model with the
        client that evaluates it.
        """
        return self.banner.total if self.banner is not None else 0

    def set_banner(self, banner: Banner, blink_on: bool = True) -> None:
        self.banner, self.blink_on = banner, blink_on
        # A NEW alarm un-silences. Silence answers "I have seen these";
        # it cannot answer for one that had not happened yet, and a
        # silence that outlives its alarm is how the next is missed.
        if self.silenced and self._active_count() > self._silenced_at_count:
            self.silenced = False
            self.silence.emit(False)
        self.update()

    #: The banner's own controls, right to left, as the Azeo figure
    #: has them. HELP lives here rather than on the menu bar because
    #: the manual puts it here — "Click ? on the Alarm Banner to see
    #: descriptions for the Alarm Banner buttons and controls" — and
    #: help about these controls belongs beside them.
    ACTIONS = (("ack", "ACK"), ("summary", "SUMMARY"),
               ("silence", "SILENCE"), ("help", "?"))
    HELP_W = 30

    def button_rects(self) -> dict[str, QRect]:
        rects, right = {}, self.width()
        for name, _label in reversed(self.ACTIONS):
            span = self.HELP_W if name == "help" else self.BUTTON_W
            right -= span
            rects[name] = QRect(right, 0, span, self.height())
        return rects

    def mousePressEvent(self, event):             # noqa: N802
        rects = self.button_rects()
        point = event.position().toPoint()
        if rects["silence"].contains(point):
            self.set_silenced(not self.silenced)
            return
        if rects["help"].contains(point):
            self.help_requested.emit()
            return
        if rects["ack"].contains(point):
            self.acknowledge.emit()
        elif rects["summary"].contains(point):
            self.open_summary.emit()

    def line_text(self, line) -> str:
        """One banner line, with the time it was raised in front.

        The time is prepended here rather than in the model because it is
        presentation: the summary list shows the same record with its columns
        sorted differently, and neither should own the other's layout.
        """
        record = line.record
        if record is None or record.raised_at is None:
            return line.text
        return f"{clock(record.raised_at)}  {line.text}"

    def paintEvent(self, event):                  # noqa: N802
        painter = QPainter(self)
        rect = self.rect()
        # Sunk, not panel: the banner is the one strip that must not read as
        # part of the surrounding frame.
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(rect, self.palette_.brush(Role.SURFACE_SUNK))
        painter.setPen(self.palette_.pen(Role.LINE, 1.0))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(),
                         rect.bottom())

        counts = self.banner.counts if self.banner else {}
        buttons = self.button_rects()

        # ------------------------------------------------------- the counts
        shown = [p for p in Priority if counts.get(p, 0)] or [Priority(1)]
        step = max(12, self.height() // max(1, len(shown)))
        for index, priority in enumerate(shown):
            count = counts.get(priority, 0)
            row = QRect(8, index * step, self.COUNT_W - 12, step)
            _mark(painter, self.palette_,
                  QRect(row.left(), row.top(), self.MARK_W, row.height()),
                  int(priority), filled=bool(count))
            self._text(painter,
                       QRect(row.left() + self.MARK_W + 5, row.top(),
                             row.width() - self.MARK_W - 5, row.height()),
                       str(count), Role.TEXT if count else Role.TEXT_FAINT,
                       Qt.AlignLeft | Qt.AlignVCenter,
                       FontRole.CHROME_MONO_BOLD)
        painter.setPen(self.palette_.pen(Role.LINE_SOFT, 1.0))
        painter.drawLine(self.COUNT_W, 0, self.COUNT_W, rect.bottom())

        # -------------------------------------------------------- the lines
        lines = self.banner.lines if self.banner else ()
        text_left = self.COUNT_W + 9
        text_right = buttons["ack"].left() - 8
        for index in range(self.settings.banner_lines):
            top = index * BANNER_LINE_H
            if index:
                painter.setPen(self.palette_.pen(Role.LINE_SOFT, 1.0))
                painter.drawLine(self.COUNT_W + 1, top, text_right + 8, top)
            if index >= len(lines):
                continue
            line = lines[index]
            record = line.record
            if record is not None:
                # **The mark carries the priority, not the text.** Colouring
                # a whole line by priority spends the one channel reserved
                # for abnormal on something the mark already says, and it
                # puts P2 amber and P3 yellow on a light ground — the pair
                # that fails the greyscale check. The line stays maximally
                # legible and the redundant colour-plus-shape sits in the
                # 16 px the eye is already trained on.
                unacked = not line.acknowledged
                _mark(painter, self.palette_,
                      QRect(text_left, top, self.MARK_W, BANNER_LINE_H),
                      int(record.priority), heavy=unacked,
                      hollow=unacked and self.settings.blink_enabled
                      and not self.blink_on)

            role = Role.TEXT
            offset = self.MARK_W + 8 if record is not None else 0
            cell = QRect(text_left + offset, top,
                         max(20, text_right - text_left - offset),
                         BANNER_LINE_H)
            painter.setFont(font_for(FontRole.CHROME_MONO))
            self._text(painter, cell,
                       painter.fontMetrics().elidedText(
                           self.line_text(line), Qt.ElideRight, cell.width()),
                       role, font=FontRole.CHROME_MONO)

        # ------------------------------------------------------ the actions
        for name, label in self.ACTIONS:
            cell = buttons[name]
            painter.setPen(self.palette_.pen(Role.LINE_SOFT, 1.0))
            painter.drawLine(cell.left(), 0, cell.left(), rect.bottom())
            # A latched SILENCE says so in the alarm-2 colour: an
            # operator walking up to this console has to be able to see
            # that alarms are being held quiet, without pressing
            # anything to find out.
            role = (Role.ALARM_P2_TEXT
                    if name == "silence" and self.silenced else Role.TEXT)
            text = ("SILENCED" if name == "silence" and self.silenced
                    else label)
            self._text(painter, cell, text, role, Qt.AlignCenter,
                       FontRole.CHROME_MONO)
        painter.end()


class StatusBar(_ChromeWidget):
    """Two populations, and mixing them is the usual mistake.

    Always present, because absence would be ambiguous: liveness and age,
    write authority, console id and span, server time, display id and
    revision. Exception only, so that lighting up means something: failover,
    historian lag, pending MOC, degradation, simulation mode.

    The always-present items sit left and right; the gap between them is the
    exception cell, and it is **empty** in normal operation. That emptiness
    is the design — a bar with something in every slot has nowhere for an
    exception to appear that the eye would notice.
    """

    HEIGHT = STATUS_H

    def __init__(self, settings: ConsoleSettings, parent=None):
        super().__init__(settings, parent)
        self.status = ConsoleStatus()

    def set_status(self, status: ConsoleStatus) -> None:
        self.status = status
        self.update()

    def segments(self) -> list[tuple[str, Role]]:
        """The bar as text, in order. Public so a test can read it."""
        return [(text, role) for text, role, _ in self._cells()] +             [(exception.value, Role.ALARM_P2)
             for exception in self.status.exceptions]

    def _cells(self) -> list[tuple[str, Role, str]]:
        """`(text, role, kind)` for the always-present cells, left then right.

        `kind` is what the painter has to decorate: a liveness dot, a write
        authority chip, or nothing.
        """
        status = self.status
        authority = "OPERATE" if self.settings.write_authority else "VIEW ONLY"
        left = [
            (status.liveness(),
             Role.ALARM_P1_TEXT if status.stale else Role.TEXT, "live"),
            (f"{self.settings.user.upper()} · {authority}", Role.TEXT_DIM,
             "auth"),
            (f"{self.settings.console_id} · {self.settings.span}",
             Role.TEXT_DIM, ""),
        ]
        right = [
            (stamp(status.server_time), Role.TEXT, ""),
        ]
        if status.alarm_rate is not None:
            right.insert(0, (f"ALM {status.alarm_rate:.0f}/h", Role.TEXT_DIM,
                             ""))
        if status.display_id:
            right.insert(0, (
                f"{status.display_id} r{status.revision}"
                + (f" · {status.moc}" if status.moc else ""),
                Role.TEXT_DIM, ""))
        return left + right

    def paintEvent(self, event):                  # noqa: N802
        painter = QPainter(self)
        rect = self._ground(painter)
        painter.setFont(font_for(FontRole.CHROME_MONO))
        metrics = painter.fontMetrics()
        cells = self._cells()
        # Everything from the server-time cell rightwards is anchored to the
        # right edge, so those readings never move when a message on the left
        # changes length.
        split = 3
        left_cells, right_cells = cells[:split], cells[split:]

        def draw(cell: QRect, text: str, role: Role, kind: str) -> None:
            inner = cell.adjusted(9, 0, -9, 0)
            if kind == "live":
                dot = QRect(inner.left(), inner.top() + (self.HEIGHT - 8) // 2,
                            8, 8)
                painter.setPen(Qt.NoPen)
                painter.setBrush(self.palette_.brush(
                    Role.ALARM_P1 if self.status.stale else Role.TEXT_DIM))
                if self.status.stale:
                    painter.drawRect(dot)          # square: not a heartbeat
                else:
                    painter.drawEllipse(dot)
                inner = inner.adjusted(13, 0, 0, 0)
            elif kind == "auth" and not self.settings.write_authority:
                # View-only has to be unmistakable, so it is a filled chip
                # rather than a word among words.
                chip = QRect(inner.left(), 4, inner.width(), self.HEIGHT - 8)
                painter.setPen(self.palette_.pen(Role.ALARM_P2, 1.0))
                painter.setBrush(self.palette_.brush(Role.ALARM_P2))
                painter.drawRect(chip)
            self._text(painter, inner, text, role, font=FontRole.CHROME_MONO)

        x = 0
        for text, role, kind in left_cells:
            width = metrics.horizontalAdvance(text) + 18                 + (13 if kind == "live" else 0)
            draw(QRect(x, 0, width, self.HEIGHT), text, role, kind)
            x += width
            painter.setPen(self.palette_.pen(Role.LINE_SOFT, 1.0))
            painter.drawLine(x, 0, x, self.HEIGHT)
        left_edge = x

        widths = [metrics.horizontalAdvance(t) + 18 for t, _, _ in right_cells]
        x = rect.width() - sum(widths)
        exception_left = x
        for (text, role, kind), width in zip(right_cells, widths):
            painter.setPen(self.palette_.pen(Role.LINE_SOFT, 1.0))
            painter.drawLine(x, 0, x, self.HEIGHT)
            draw(QRect(x, 0, width, self.HEIGHT), text, role, kind)
            x += width

        # ------------------------------------------------------ exceptions
        exceptions = self.status.exceptions
        if not exceptions:
            painter.end()
            return
        band = QRect(left_edge, 0, max(0, exception_left - left_edge),
                     self.HEIGHT)
        critical = any(e is Exception_.FAILOVER for e in exceptions)
        role = Role.ALARM_P1 if critical else Role.ALARM_P2
        painter.setPen(Qt.NoPen)
        painter.setBrush(self.palette_.brush(role))
        painter.drawRect(band)
        glyph = "▲" if critical else "◆"
        text = f"  {glyph}  " + "   ".join(e.value for e in exceptions)
        painter.setFont(font_for(FontRole.CHROME_MONO_BOLD))
        self._text(painter, band,
                   painter.fontMetrics().elidedText(text, Qt.ElideRight,
                                                    band.width()),
                   Role.ALARM_P1_TEXT if critical else Role.ALARM_P2_TEXT,
                   font=FontRole.CHROME_MONO_BOLD)
        painter.end()


class SoftKeys(_ChromeWidget):
    """Only for actions reachable from anywhere. Four to six, with words.

    Words, not icons: an unlabelled icon is re-learned every shift, and the
    sixteen-key row other consoles ship is muscle memory from a physical
    keyboard that no longer exists.
    """

    HEIGHT = SOFT_KEY_H
    activated = Signal(str)

    KEYS = ("ACK", "ALARM SUMMARY", "PREVIOUS", "LEVEL 1", "TREND",
            "FACEPLATE")

    def key_rects(self) -> dict[str, QRect]:
        width = max(1, (self.width() - 8) // len(self.KEYS))
        return {key: QRect(4 + index * width, 2, width - 2, self.HEIGHT - 4)
                for index, key in enumerate(self.KEYS)}

    def mousePressEvent(self, event):             # noqa: N802
        for key, rect in self.key_rects().items():
            if rect.contains(event.position().toPoint()):
                self.activated.emit(key)

    def paintEvent(self, event):                  # noqa: N802
        painter = QPainter(self)
        rect = self.rect()
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(rect, self.palette_.brush(Role.SURFACE_PANEL_ALT))
        painter.setPen(self.palette_.pen(Role.LINE, 1.0))
        painter.drawLine(rect.left(), rect.top(), rect.right(), rect.top())
        for key, cell in self.key_rects().items():
            painter.setBrush(self.palette_.brush(Role.SURFACE_FIELD))
            painter.setPen(self.palette_.pen(Role.LINE, 1.0))
            painter.drawRect(cell)
            self._text(painter, cell, key, Role.TEXT, Qt.AlignCenter,
                       FontRole.CHROME_MONO)
        painter.end()
