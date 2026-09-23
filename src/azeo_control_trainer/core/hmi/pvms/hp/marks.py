"""Drawing the High Performance PVM's outer furniture.

The box, the alarm icon, the status icons, the alarm count. Every
decision about *what* shows was made in `state.py`; this module only
puts it on a painter.

**Level decides between box and icon.** On a Level 1 display the alarm
BOX shows and the icon is automatically hidden; on Level 2 the icon
shows and the box does not. An overview carries dozens of PVMs, so a
box the eye can catch across the room is the right mark there, and a
per-module icon is the right mark up close.

**Every mark carries a redundant channel**, because colour alone does
not survive a greyscale print or a red-green deficiency:

- priority carries a **shape** — triangle critical, diamond warning,
  square advisory (invariant I4);
- unacknowledged carries **weight** — a second outer stroke, so a
  screenshot in an incident report still says who nobody answered;
- suppressed carries a **dash**, because `theme/vision.py`
  measures ALARM_P2 against ALARM_SHELVED at 3 apart in monochrome.
  The repo has been here before with priority-3 yellow and the answer
  was the same: add a channel rather than change a colour. A broken
  line also *means* the right thing — this alarm is not reporting.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPen

from ...theme.roles import Role
from .state import (
    ADVISORY, CRITICAL, ICON_ACTIVE_UNACKED, ICON_INACTIVE_UNACKED,
    ICON_SUPPRESSED, PRIORITY_ROLES, STATUS_CONDITIONS, WARNING,
    AlarmBoxState,
)

#: Box thickness. "A thick-colored rectangle" — thick enough to read as
#: a box rather than as a border on the card.
BOX_WIDTH = 3.0
#: An unacknowledged alarm gets a second, outer stroke.
UNACKED_EXTRA = 2.0

ICON_SIZE = 12.0
ICON_GAP = 2.0

#: Glyphs standing in for Azeo's status bitmaps. The identities and
#: the conditions are the manual's; the glyph is ours because a bitmap
#: we do not have is worse than a letter that reads.
CONDITION_GLYPHS = {
    "abnormal_mode": ("!", Role.ACTION),
    "not_running": ("▮▮", Role.ALARM_P2),
    "bad_io": ("✖", Role.ALARM_P1),
    "simulated": ("S", Role.ALARM_P2),
    "no_permit": ("P", Role.ALARM_P2),
    "interlocked": ("\U0001f512", Role.ALARM_P1),
    "tracking": ("T", Role.ACTION),
    "bypassed": ("B", Role.ALARM_P3),
}
CONDITION_TIPS = dict(STATUS_CONDITIONS)


def box_stroke(state: AlarmBoxState):
    """(Qt pen style, width) for a state's box.

    Exposed so a test can assert the redundant channel exists without
    reading pixels: suppression is carried by the DASH, not by the
    colour, because the colour does not survive greyscale.
    """
    return (Qt.DashLine if state.suppressed else Qt.SolidLine, BOX_WIDTH)


def draw_alarm_box(painter, rect: QRectF, state: AlarmBoxState,
                   palette: dict, level: int = 1) -> bool:
    """The rectangle around the PVM. True when one was drawn.

    Level 1 only — on Level 2 the alarm icon carries the priority.
    """
    role = state.box_role
    if role is None or level != 1:
        return False
    colour = QColor(palette[role])
    style, width = box_stroke(state)
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(colour, width, style))
    painter.drawRect(rect.adjusted(1.5, 1.5, -1.5, -1.5))
    if state.has_alarm and not state.acked:
        # Second stroke outside the first: unacknowledged, by weight.
        painter.setPen(QPen(colour, 1.0))
        painter.drawRect(rect.adjusted(-1.0, -1.0, 1.0, 1.0))
    return True


def _priority_shape(x: float, y: float, priority: int, size: float):
    """The polygon for a priority, or None for the square case."""
    if priority >= CRITICAL:
        return [QPointF(x + size / 2, y), QPointF(x, y + size),
                QPointF(x + size, y + size)]
    if priority >= WARNING:
        return [QPointF(x + size / 2, y), QPointF(x + size, y + size / 2),
                QPointF(x + size / 2, y + size), QPointF(x, y + size / 2)]
    return None


def draw_alarm_icon(painter, x: float, y: float, state: AlarmBoxState,
                    palette: dict, level: int = 2) -> float:
    """The priority icon, Level 2's mark. Returns the width used.

    All four of the manual's states are distinguishable without
    colour: shape gives the priority, fill gives active vs inactive,
    the outer ring gives unacknowledged, and the dash gives suppressed.
    """
    icon = state.icon_state
    if icon is None or level == 1:
        return 0.0
    priority = state.priority or ADVISORY
    role = (Role.ALARM_SHELVED if icon == ICON_SUPPRESSED
            else PRIORITY_ROLES.get(priority, Role.ALARM_P3))
    colour = QColor(palette[role])
    size = ICON_SIZE
    # Inactive-unacknowledged draws HOLLOW: the condition has gone but
    # nobody has said so. Filled would claim it is still happening.
    hollow = icon in (ICON_INACTIVE_UNACKED, ICON_SUPPRESSED)
    painter.setBrush(Qt.NoBrush if hollow else colour)
    painter.setPen(QPen(colour if hollow else QColor(palette[Role.TEXT]),
                        1.5 if hollow else 1.0,
                        Qt.DashLine if icon == ICON_SUPPRESSED
                        else Qt.SolidLine))
    shape = _priority_shape(x, y, priority, size)
    if shape is not None:
        painter.drawPolygon(shape)
    else:
        painter.drawRect(QRectF(x, y, size, size))
    if icon == ICON_ACTIVE_UNACKED:
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(colour, 1.0))
        outer = _priority_shape(x - 2, y - 2, priority, size + 4)
        if outer is not None:
            painter.drawPolygon(outer)
        else:
            painter.drawRect(QRectF(x - 2, y - 2, size + 4, size + 4))
    return size + ICON_GAP


def draw_status_icons(painter, x: float, y: float,
                      state: AlarmBoxState, palette: dict,
                      font: QFont | None = None) -> float:
    """The abnormal-condition icons, left to right. Returns width used.

    Precedence is `state.visible_conditions()`, not this function —
    what is drawn here is exactly what the manual says shows. They are
    drawn whether or not the status BOX shows: the box is outranked by
    an active alarm, but the condition has not gone away and the
    operator still needs to know the mode is wrong.
    """
    cursor = x
    for field in state.visible_conditions():
        glyph, role = CONDITION_GLYPHS.get(field, ("?", Role.ACTION))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(palette[role]))
        painter.drawRect(QRectF(cursor, y, ICON_SIZE, ICON_SIZE))
        painter.setPen(QColor(palette[Role.SURFACE_FIELD]))
        painter.setFont(font or QFont("Segoe UI", 7, QFont.Bold))
        painter.drawText(QRectF(cursor, y, ICON_SIZE, ICON_SIZE),
                         Qt.AlignCenter, glyph)
        cursor += ICON_SIZE + ICON_GAP
    return cursor - x


def draw_alarm_count(painter, rect: QRectF, state: AlarmBoxState,
                     palette: dict, font: QFont | None = None) -> bool:
    """The count of unacknowledged, active and suppressed alarms.

    Drawn only past 1: a PVM already showing one alarm's box and icon
    does not need to be told it has one alarm, and a badge reading "1"
    on every alarmed PVM is forty pixels of noise per display.
    """
    if state.alarm_count <= 1:
        return False
    role = state.box_role or Role.ALARM_P3
    text = str(state.alarm_count)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(palette[role]))
    size = ICON_SIZE + 2
    box = QRectF(rect.right() - size - 2, rect.top() + 2, size, size)
    painter.drawEllipse(box)
    painter.setPen(QColor(palette[Role.SURFACE_FIELD]))
    painter.setFont(font or QFont("Segoe UI", 7, QFont.Bold))
    painter.drawText(box, Qt.AlignCenter, text)
    return True


def draw_status_indicator(painter, x: float, y: float,
                          state: AlarmBoxState, palette: dict,
                          level: int = 2) -> float:
    """The abnormal-status indicator, which shares the icon's slot.

    "When no active alarms exist, the abnormal status indicator
    replaces the alarm icon and is fully visible. When active alarms do
    exist, the indicator wraps around the alarm icon and is partly
    visible." So it is one position on the PVM with two readings, and
    the wrap is what stops an abnormal condition from hiding an alarm.
    """
    if level == 1 or not state.abnormal:
        return 0.0
    colour = QColor(palette[Role.ACTION])
    if state.icon_state is None:
        painter.setPen(Qt.NoPen)
        painter.setBrush(colour)
        painter.drawRect(QRectF(x, y, ICON_SIZE, ICON_SIZE))
        return ICON_SIZE + ICON_GAP
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(colour, 1.5))
    painter.drawRect(QRectF(x - 2, y - 2, ICON_SIZE + 4, ICON_SIZE + 4))
    return 0.0
