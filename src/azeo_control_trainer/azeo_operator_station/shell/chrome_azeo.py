"""Azeo Operator Station's own console chrome: menu bar and navigation bar.

Built to the annotated figure in `azeolive.chm`, "Azeo Operator Station desktop"
— menu bar, navigation bar, display window, alarm banner.

    ┌──────────────────────────────────────────────────────────────┐
    │ ⌕ ⚠ ⚒ ⟳ ▤ ▽ ∿ ⚙ ⛭     08-Aug-18 15:55  CON-01\\user  ⛶ ☺ ⏻ │  menu
    │ ◀ ▶ ⌂  Discharge Train                                    ☰ │  nav
    ├──────────────────────────────────────────────────────────────┤
    │                        display window                        │
    ├──────────────────────────────────────────────────────────────┤
    │ ▲FIC-101  ◆LIC-201                                        │  banner
    └──────────────────────────────────────────────────────────────┘

**This is opt-in, and the reason is a number.** `docs/console/07` costs chrome at
12 % of vertical pixels and the classic arrangement spends 106 px — 9.8 %
of a 1080 console. Azeo's arrangement replaces the identity row with a
menu bar *and* adds a navigation bar; `chrome_height` reports the real
figure and `within_budget` will say plainly whether it fits. It is
offered because a trainee who will sit at Azeo should learn Azeo's
furniture, not because it is the better HMI.

`docs/console/07` also names two things it deliberately omits that this brings
back: a **menu bar**, and an **unlabelled icon toolbar**. Both are
departures, recorded as such. The mitigation is that every button here
carries a tooltip and a name — `MENU_BUTTONS` is the list, and a test
asserts none of them is nameless.

**Two buttons carry a bubble, and that is the interesting part.** The
manual: a yellow bubble on *Errors* means one or more errors exist on an
open display, and on *Refresh Configuration* it means "configuration has
been updated offline". The second is the visible end of
`runtime/deployment.py` — publishing marks configuration available, the
bubble lights, and the operator chooses when to accept it.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QFontMetrics, QPainter, QPainterPath, QPolygonF
from PySide6.QtWidgets import QToolTip

from azeo_control_trainer.core.hmi.theme.fonts import FontRole, font_for
from azeo_control_trainer.core.hmi.theme.roles import Role
from .chrome import ConsoleSettings, _ChromeWidget, stamp

#: Zone heights. The menu bar REPLACES the identity row (Azeo carries
#: node and user on its right-hand side), so the added cost is the
#: navigation bar plus the difference — not both rows outright.
MENU_H = 30
NAV_H = 26

#: (key, glyph, tooltip). Unlabelled icons are a departure from
#: `docs/console/07`; the tooltip is the mitigation, and every entry must have
#: one. Batch buttons from the manual are absent because there is no
#: batch product here — an icon that opens nothing is worse than a gap.
#: **Glyphs are checked against the chrome font, not chosen by eye.**
#: The first draft used 🔍 🔧 📈 🏷 👤 and every one of them fell back to
#: an empty box: the chrome font has no emoji coverage, so the toolbar
#: rendered as five blanks and four symbols. `QFontMetrics.inFont` is
#: the check, and `tests/test_station_controls.py` guards the replacement — a
#: glyph nobody can
#: see is worse than a word, and this is exactly the failure an
#: unlabelled icon toolbar is prone to.
MENU_BUTTONS = (
    ("search", "⌕", "Search for and open a display or control tag (Ctrl+K)"),
    ("errors", "⚠", "Errors on an open display"),
    ("tools", "⚒", "Print, reset layout, scale, display sets, themes"),
    ("refresh", "⟳", "Refresh configuration published since this "
                     "session started (F5)"),
    ("alarm_list", "▤", "Open the Alarm List"),
    ("alarm_filter", "▽", "Open the Alarm Filter display"),
    ("history", "∿", "Open Process History View"),
    ("utilities", "⚙", "Open other Azeo applications"),
    ("tag_settings", "⛭", "Display Tag Settings"),
)

#: Buttons that may show a bubble, and what it means.
BUBBLE_BUTTONS = {
    "errors": "one or more errors exist on an open display",
    "refresh": "configuration has been updated offline",
}

#: Right-hand buttons, after the identity text.
RIGHT_BUTTONS = (
    ("mode", "⛶", "Toggle Window / Full Desktop mode (F11)"),
    ("logon", "☺", "Open Azeo Logon and switch user"),
    ("exit", "⏻", "Exit the operator station"),
)

#: Every legacy glyph remains part of the public inventory because published
#: training material names it.  The live chrome no longer depends on font
#: coverage, however: :func:`draw_operator_icon` paints every command from
#: geometry, so Windows font substitution cannot turn a command into a box.
ALL_BUTTONS = MENU_BUTTONS + RIGHT_BUTTONS
NAV_GLYPHS = ("◀", "▶", "⌂", "↑", "☰", "▾")

BUTTON_W = 32
BUBBLE_R = 5
ICON_INSET = 4.0
MENU_GROUP_STARTS = frozenset({"alarm_list", "utilities"})
OPERATOR_ICON_KEYS = frozenset(
    {key for key, _glyph, _tip in ALL_BUTTONS}
    | {"back", "forward", "home", "up", "menu", "down"}
)

# Colour is functional, never confetti.  Blue identifies navigation and
# information tools; teal identifies an operator action.  Alarm colour is
# applied dynamically only when a command actually has something abnormal to
# report.  This gives the operator distinct, modern marks without spending the
# alarm palette on decoration.
MENU_ICON_ROLES = {
    "search": Role.HEADING,
    "errors": Role.HEADING,
    "tools": Role.ACTION,
    "refresh": Role.ACTION,
    "alarm_list": Role.HEADING,
    "alarm_filter": Role.ACTION,
    "history": Role.HEADING,
    "utilities": Role.ACTION,
    "tag_settings": Role.HEADING,
    "mode": Role.ACTION,
    "logon": Role.HEADING,
    "exit": Role.TEXT_DIM,
}


def _icon_rect(rect: QRect, inset: float = ICON_INSET) -> QRectF:
    """Square drawing box centred in one stable command target."""
    side = max(2.0, min(rect.width(), rect.height()) - inset * 2.0)
    return QRectF(rect.center().x() - side / 2.0,
                  rect.center().y() - side / 2.0, side, side)


def draw_operator_icon(painter: QPainter, rect: QRect, key: str,
                       palette, role: Role = Role.TEXT_DIM) -> None:
    """Paint one DPI-safe operator command icon without a font glyph.

    These marks are deliberately simple.  Chrome is scanned peripherally;
    the icon identifies the family and the tooltip supplies the exact verb.
    Alarm colour is never borrowed here — a toolbar command is not an alarm.
    """
    box = _icon_rect(rect)
    x, y, w, h = box.x(), box.y(), box.width(), box.height()
    cx, cy = box.center().x(), box.center().y()
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    # A low-alpha role backplate makes the command family visible at operator
    # distance without turning the toolbar into a row of alarm-coloured tiles.
    # At the real 24 px button height this grows the mark from 10 to 16 px.
    painter.setPen(Qt.NoPen)
    painter.setBrush(palette.alpha(role, .14))
    painter.drawRoundedRect(box.adjusted(-2.0, -2.0, 2.0, 2.0), 3.0, 3.0)
    painter.setPen(palette.pen(role, 1.70))
    painter.setBrush(Qt.NoBrush)

    if key == "search":
        lens = QRectF(x + w * .08, y + h * .05, w * .58, h * .58)
        painter.drawEllipse(lens)
        painter.drawLine(QPointF(x + w * .58, y + h * .58),
                         QPointF(x + w * .94, y + h * .94))
    elif key == "errors":
        painter.setBrush(palette.alpha(role, .20))
        painter.drawPolygon(QPolygonF((
            QPointF(cx, y), QPointF(x + w, y + h), QPointF(x, y + h))))
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(QPointF(cx, y + h * .30),
                         QPointF(cx, y + h * .64))
        painter.drawPoint(QPointF(cx, y + h * .80))
    elif key == "tools":
        for offset, knob in ((.22, .68), (.50, .34), (.78, .58)):
            yy = y + h * offset
            painter.drawLine(QPointF(x, yy), QPointF(x + w, yy))
            painter.setBrush(palette.brush(role))
            painter.drawEllipse(QPointF(x + w * knob, yy), w * .09, w * .09)
            painter.setBrush(Qt.NoBrush)
    elif key == "refresh":
        arc = QRectF(x + w * .10, y + h * .10, w * .78, h * .78)
        painter.drawArc(arc, 35 * 16, 280 * 16)
        painter.setBrush(palette.brush(role))
        painter.drawPolygon(QPolygonF((
            QPointF(x + w * .78, y), QPointF(x + w, y + h * .23),
            QPointF(x + w * .70, y + h * .28))))
    elif key == "alarm_list":
        bell = QPainterPath()
        bell.moveTo(x + w * .18, y + h * .72)
        bell.quadTo(x + w * .28, y + h * .58, x + w * .28, y + h * .36)
        bell.quadTo(cx, y + h * .05, x + w * .72, y + h * .36)
        bell.quadTo(x + w * .72, y + h * .58, x + w * .82, y + h * .72)
        bell.closeSubpath()
        painter.setBrush(palette.alpha(role, .18))
        painter.drawPath(bell)
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(QPointF(x + w * .40, y + h * .86),
                         QPointF(x + w * .60, y + h * .86))
    elif key == "alarm_filter":
        painter.setBrush(palette.alpha(role, .16))
        painter.drawPolygon(QPolygonF((
            QPointF(x, y + h * .08), QPointF(x + w, y + h * .08),
            QPointF(x + w * .61, y + h * .51),
            QPointF(x + w * .61, y + h * .86),
            QPointF(x + w * .39, y + h),
            QPointF(x + w * .39, y + h * .51))))
        painter.setBrush(Qt.NoBrush)
    elif key == "history":
        painter.drawLine(QPointF(x, y), QPointF(x, y + h))
        painter.drawLine(QPointF(x, y + h), QPointF(x + w, y + h))
        painter.drawPolyline(QPolygonF((
            QPointF(x + w * .08, y + h * .72),
            QPointF(x + w * .30, y + h * .48),
            QPointF(x + w * .50, y + h * .61),
            QPointF(x + w * .72, y + h * .24),
            QPointF(x + w, y + h * .36))))
    elif key == "utilities":
        cell = w * .30
        for row in range(2):
            for column in range(2):
                painter.setBrush(
                    palette.alpha(role, .24)
                    if row == column else Qt.NoBrush)
                painter.drawRoundedRect(QRectF(
                    x + column * w * .56, y + row * h * .56,
                    cell, cell), 1.5, 1.5)
        painter.setBrush(Qt.NoBrush)
    elif key == "tag_settings":
        tag = QPainterPath()
        tag.moveTo(x, y + h * .20)
        tag.lineTo(x + w * .58, y + h * .20)
        tag.lineTo(x + w, cy)
        tag.lineTo(x + w * .58, y + h * .80)
        tag.lineTo(x, y + h * .80)
        tag.closeSubpath()
        painter.setBrush(palette.alpha(role, .17))
        painter.drawPath(tag)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(x + w * .18, cy), w * .06, w * .06)
    elif key == "mode":
        d = w * .34
        painter.drawLine(QPointF(x, y + d), QPointF(x, y))
        painter.drawLine(QPointF(x, y), QPointF(x + d, y))
        painter.drawLine(QPointF(x + w - d, y), QPointF(x + w, y))
        painter.drawLine(QPointF(x + w, y), QPointF(x + w, y + d))
        painter.drawLine(QPointF(x, y + h - d), QPointF(x, y + h))
        painter.drawLine(QPointF(x, y + h), QPointF(x + d, y + h))
        painter.drawLine(QPointF(x + w - d, y + h), QPointF(x + w, y + h))
        painter.drawLine(QPointF(x + w, y + h), QPointF(x + w, y + h - d))
    elif key == "logon":
        painter.setBrush(palette.alpha(role, .22))
        painter.drawEllipse(QRectF(x + w * .32, y, w * .36, h * .36))
        painter.setBrush(Qt.NoBrush)
        shoulders = QPainterPath()
        shoulders.moveTo(x + w * .08, y + h)
        shoulders.quadTo(cx, y + h * .42, x + w * .92, y + h)
        painter.drawPath(shoulders)
    elif key == "exit":
        painter.drawArc(QRectF(x + w * .10, y + h * .14,
                               w * .80, h * .80), 40 * 16, 280 * 16)
        painter.drawLine(QPointF(cx, y), QPointF(cx, y + h * .52))
    elif key in {"back", "forward"}:
        direction = -1 if key == "back" else 1
        left = x + (w * .66 if direction < 0 else w * .34)
        tip = x + (w * .24 if direction < 0 else w * .76)
        painter.drawPolyline(QPolygonF((
            QPointF(left, y + h * .08), QPointF(tip, cy),
            QPointF(left, y + h * .92))))
    elif key == "home":
        painter.setBrush(palette.alpha(role, .18))
        painter.drawPolygon(QPolygonF((
            QPointF(x, y + h * .44), QPointF(cx, y),
            QPointF(x + w, y + h * .44))))
        painter.drawRect(QRectF(x + w * .18, y + h * .42,
                                w * .64, h * .54))
        painter.setBrush(Qt.NoBrush)
    elif key == "up":
        painter.drawLine(QPointF(cx, y + h), QPointF(cx, y + h * .14))
        painter.drawPolyline(QPolygonF((
            QPointF(x + w * .18, y + h * .42), QPointF(cx, y + h * .08),
            QPointF(x + w * .82, y + h * .42))))
    elif key == "menu":
        for offset in (.22, .50, .78):
            yy = y + h * offset
            painter.drawLine(QPointF(x + w * .25, yy), QPointF(x + w, yy))
            painter.setBrush(palette.brush(role))
            painter.drawEllipse(QPointF(x + w * .06, yy), w * .05, w * .05)
            painter.setBrush(Qt.NoBrush)
    elif key == "down":
        painter.drawPolyline(QPolygonF((
            QPointF(x + w * .18, y + h * .34), QPointF(cx, y + h * .68),
            QPointF(x + w * .82, y + h * .34))))
    painter.restore()


def _paint_button_ground(painter: QPainter, rect: QRect, palette, *,
                         hovered: bool = False, pressed: bool = False,
                         selected: bool = False, enabled: bool = True) -> None:
    """Modern, restrained button states; status colour stays out of chrome."""
    if not (hovered or pressed or selected):
        return
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setBrush(palette.brush(
        Role.SURFACE_SUNK if pressed else Role.SURFACE_FIELD))
    painter.setPen(palette.pen(
        Role.ACTION if selected and enabled else Role.LINE_SOFT, 1.0))
    painter.drawRoundedRect(QRectF(rect.adjusted(1, 1, -1, -1)), 3.0, 3.0)
    painter.restore()


class MenuBar(_ChromeWidget):
    """Azeo's menu bar: tools on the left, identity on the right.

    The identity half is the same information the classic `IdentityRow`
    carries — this is why the two do not stack. Showing node and user
    twice would spend 30 px saying nothing new.
    """

    HEIGHT = MENU_H
    activated = Signal(str)

    def __init__(self, settings: ConsoleSettings, parent=None):
        super().__init__(settings, parent)
        self.setMouseTracking(True)
        self.setAccessibleName("Operator command bar")
        self.setAccessibleDescription(
            "Search, diagnostics, alarm, history and station commands")
        self.server_time = 0.0
        #: key -> bool. A bubble is a *count of things to act on*, not
        #: decoration: it lights only when the operator has something to
        #: do, which is what keeps it worth looking at.
        self.bubbles = {key: False for key in BUBBLE_BUTTONS}
        #: The live button set. `drop_buttons` narrows it; everything
        #: that paints or hit-tests reads these, never the module
        #: constants, so a dropped button leaves no hotspot behind.
        self.buttons = MENU_BUTTONS
        self.right_buttons = RIGHT_BUTTONS
        self.enabled = {key: True for key, _g, _t in MENU_BUTTONS}
        self._pressed_key: str | None = None
        self._hover_key: str | None = None
        self.selected: set[str] = set()

    # ------------------------------------------------------ geometry
    def drop_buttons(self, keys) -> None:
        """Remove buttons this build has nothing behind.

        **Dropped, not greyed.** An inert control is a worse lie than a
        missing one: it invites the click and then does nothing, and a
        toolbar with no labels has no other way to say so. The rects
        close up, so no hotspot survives for a button that is gone.
        """
        drop = set(keys or ())
        self.buttons = tuple(b for b in MENU_BUTTONS if b[0] not in drop)
        self.right_buttons = tuple(b for b in RIGHT_BUTTONS
                                   if b[0] not in drop)
        self.enabled = {k: True for k, _g, _t in self.buttons}
        self.update()

    def button_rects(self) -> dict:
        rects = {}
        left = 4
        for key, _glyph, _tip in self.buttons:
            if rects and key in MENU_GROUP_STARTS:
                left += 5
            rects[key] = QRect(left, 3, BUTTON_W - 2, self.HEIGHT - 6)
            left += BUTTON_W
        right = self.width() - 4
        for key, _glyph, _tip in reversed(self.right_buttons):
            right -= BUTTON_W
            rects[key] = QRect(right, 3, BUTTON_W - 2, self.HEIGHT - 6)
        return rects

    def identity_text(self) -> str:
        r"""`08-Aug-18 15:55  NODE\USER` — the manual's right-hand pair.

        The timestamp comes from the value the console was handed, never
        from a clock of its own: operators date log entries from what is
        on screen, and a workstation with drifted NTP writes a shift log
        that disagrees with the historian.
        """
        return "%s   %s\\%s" % (stamp(self.server_time),
                                self.settings.console_id,
                                self.settings.user)

    def hit_test(self, position) -> str | None:
        for key, rect in self.button_rects().items():
            if rect.contains(position):
                return key
        return None

    def tooltip_at(self, position) -> str:
        key = self.hit_test(position)
        row = next((row for row in self.buttons + self.right_buttons
                    if row[0] == key), None)
        return row[2] if row is not None else ""

    def icon_role(self, key: str) -> Role:
        """Semantic command colour, including live exception state."""
        if not self.enabled.get(key, True):
            return Role.TEXT_FAINT
        if key in {"errors", "refresh"} and self.bubbles.get(key):
            return Role.ALARM_P2
        return MENU_ICON_ROLES.get(key, Role.ACTION)

    def event(self, event):
        if event.type() == QEvent.ToolTip:
            tip = self.tooltip_at(event.pos())
            if tip:
                QToolTip.showText(event.globalPos(), tip, self)
                return True
            QToolTip.hideText()
        return super().event(event)

    def mouseMoveEvent(self, event):              # noqa: N802
        key = self.hit_test(event.position().toPoint())
        if key != self._hover_key:
            self._hover_key = key
            self.update()
        self.setCursor(Qt.PointingHandCursor if key else Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):                  # noqa: N802
        self._hover_key = None
        self.setCursor(Qt.ArrowCursor)
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):              # noqa: N802
        key = self.hit_test(event.position().toPoint())
        self._pressed_key = key if event.button() == Qt.LeftButton \
            and key and self.enabled.get(key, True) else None
        self.update()

    def mouseReleaseEvent(self, event):            # noqa: N802
        key = self.hit_test(event.position().toPoint())
        activate = event.button() == Qt.LeftButton \
            and key == self._pressed_key
        self._pressed_key = None
        self.update()
        if activate and key is not None:
            # Menus and modal dialogs must be opened after mouse-up. Emitting
            # on press let this same release immediately dismiss a new QMenu,
            # so the toolbar looked inert even though its handler had run.
            self.activated.emit(key)

    # ------------------------------------------------------- painting
    def paintEvent(self, event):                   # noqa: N802
        painter = QPainter(self)
        self._ground(painter)
        rects = self.button_rects()
        for key, _glyph, _tip in self.buttons + self.right_buttons:
            rect = rects[key]
            enabled = self.enabled.get(key, True)
            _paint_button_ground(
                painter, rect, self.palette_,
                hovered=key == self._hover_key,
                pressed=key == self._pressed_key,
                selected=key in self.selected,
                enabled=enabled)
            role = self.icon_role(key)
            draw_operator_icon(painter, rect, key, self.palette_, role)
            if self.bubbles.get(key):
                self._bubble(painter, rect, self.bubbles[key])
        # Identity, right-aligned, ending where the right buttons start.
        end = (min(rects[key].left()
                   for key, _g, _t in self.right_buttons) - 10
               if self.right_buttons else self.width() - 4)
        start = 4 + len(self.buttons) * BUTTON_W + 10
        authority = "OPERATE" if self.settings.write_authority else "VIEW ONLY"
        authority_w = 68 if self.settings.write_authority else 78
        authority_rect = QRect(max(start, end - authority_w), 4,
                               max(0, min(authority_w, end - start)),
                               self.HEIGHT - 8)
        identity_end = max(start, authority_rect.left() - 8)
        self._text(painter, QRect(start, 0, max(0, identity_end - start),
                                  self.HEIGHT), self.identity_text(),
                   Role.TEXT_DIM, Qt.AlignRight | Qt.AlignVCenter)
        if authority_rect.width() > 20:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setBrush(self.palette_.brush(Role.SURFACE_FIELD))
            painter.setPen(self.palette_.pen(
                Role.ACTION if self.settings.write_authority
                else Role.LINE_SOFT, 1.0))
            painter.drawRoundedRect(QRectF(authority_rect), 3.0, 3.0)
            self._text(
                painter, authority_rect, authority,
                Role.ACTION_DEEP if self.settings.write_authority
                else Role.TEXT_DIM, Qt.AlignCenter)
        painter.end()

    def _bubble(self, painter: QPainter, rect: QRect, value=True) -> None:
        """The yellow bubble. Colour AND position carry it — a bubble is
        at the corner of one button, so which button it belongs to is
        never ambiguous the way a colour change alone would be."""
        count = int(value) if isinstance(value, int) else int(bool(value))
        diameter = BUBBLE_R * 2 + (4 if count > 1 else 0)
        badge = QRect(rect.right() - diameter + 2, rect.top() - 1,
                      diameter, diameter)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self.palette_.brush(Role.ALARM_P2))
        painter.drawEllipse(badge)
        if count > 1:
            self._text(painter, badge, "9+" if count > 9 else str(count),
                       Role.TEXT, Qt.AlignCenter, FontRole.TINY)


class NavigationBar(_ChromeWidget):
    """History, hierarchy breadcrumbs and the published-display browser.

    The selector remains one compact row, but it no longer makes the operator
    remember where a display sits.  Ancestors are clickable, the current
    operating level is explicit, Up has browser semantics, and the labelled
    Displays button exposes the complete L1-L4 inventory.
    """

    HEIGHT = NAV_H
    navigate = Signal(str)
    select = Signal()
    open_display = Signal(str)

    BUTTONS = (("back", "◀"), ("forward", "▶"),
               ("home", "⌂"), ("up", "↑"))
    MENU_KEY = "menu"
    BROWSE_W = 88
    LEVEL_W = 34

    def __init__(self, settings: ConsoleSettings, parent=None):
        super().__init__(settings, parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("Display navigation")
        self.setAccessibleDescription(
            "Back, forward, home, parent, hierarchy path and display browser")
        self.display_name = ""
        self.breadcrumb: tuple[str, ...] = ()
        self.level = 0
        self.can_go = {"back": False, "forward": False,
                       "home": True, "up": False}
        self._pressed_hit: str | None = None
        self._hover_hit: str | None = None

    def set_display(self, name: str) -> None:
        self.set_context(name)

    def set_context(self, name: str, breadcrumb=(), level: int = 0) -> None:
        """Update all navigation context atomically to avoid mixed frames."""
        self.display_name = name or ""
        trail = tuple(str(value) for value in (breadcrumb or ()) if value)
        self.breadcrumb = trail or ((self.display_name,) if self.display_name
                                    else ())
        self.level = int(level or 0)
        self.update()

    def button_rects(self) -> dict:
        rects = {name: QRect(4 + index * BUTTON_W, 2, BUTTON_W - 2,
                             self.HEIGHT - 4)
                 for index, (name, _g) in enumerate(self.BUTTONS)}
        rects[self.MENU_KEY] = QRect(
            max(0, self.width() - self.BROWSE_W - 4), 2,
            self.BROWSE_W, self.HEIGHT - 4)
        return rects

    def level_rect(self) -> QRect:
        left = 4 + len(self.BUTTONS) * BUTTON_W + 5
        return QRect(left, 3, self.LEVEL_W, self.HEIGHT - 6)

    def selector_rect(self) -> QRect:
        left = self.level_rect().right() + 6
        right = self.button_rects()[self.MENU_KEY].left() - 7
        return QRect(left, 2, max(0, right - left), self.HEIGHT - 4)

    def breadcrumb_rects(self) -> tuple[tuple[str, QRect, bool], ...]:
        """Visible root-to-current segments, compressed from the root first."""
        box = self.selector_rect().adjusted(7, 1, -7, -1)
        trail = list(self.breadcrumb)
        if not trail or box.width() <= 10:
            return ()
        metrics = QFontMetrics(font_for(FontRole.CHROME))
        separator = metrics.horizontalAdvance("›") + 10
        widths = [min(190, max(36, metrics.horizontalAdvance(name) + 14))
                  for name in trail]
        hidden = False
        while len(trail) > 2 and sum(widths) + separator * (len(trail) - 1) \
                > box.width():
            trail.pop(0)
            widths.pop(0)
            hidden = True
        if hidden:
            trail.insert(0, "…")
            widths.insert(0, 28)
        total = sum(widths) + separator * (len(trail) - 1)
        if total > box.width() and widths:
            widths[-1] = max(24, widths[-1] - (total - box.width()))
        result = []
        left = box.left()
        real_trail = self.breadcrumb[-len(trail) + int(hidden):] \
            if hidden else self.breadcrumb
        real_index = 0
        for index, (label, width) in enumerate(zip(trail, widths)):
            rect = QRect(left, box.top(), max(0, width), box.height())
            current = index == len(trail) - 1
            target = "" if label == "…" else str(real_trail[real_index])
            if label != "…":
                real_index += 1
            result.append((target, rect, current))
            left = rect.right() + separator
        return tuple(result)

    def hit_test(self, position) -> str | None:
        for name, rect in self.button_rects().items():
            if rect.contains(position):
                return name
        for target, rect, current in self.breadcrumb_rects():
            if rect.contains(position) and target and not current:
                return f"crumb:{target}"
        if self.selector_rect().contains(position):
            return "selector"
        return None

    def tooltip_at(self, position) -> str:
        return {
            "back": "Back to the previous display (Alt+Left)",
            "forward": "Forward to the next display (Alt+Right)",
            "home": "Open the L1 home display (Alt+Home)",
            "up": "Open the parent operating display (Alt+Up)",
            "selector": "Browse all displays by operating level (Ctrl+L)",
            self.MENU_KEY: "Browse all displays by operating level (Ctrl+L)",
        }.get(self.hit_test(position), "") or (
            "Open " + self.hit_test(position).partition(":")[2]
            if str(self.hit_test(position)).startswith("crumb:") else "")

    def event(self, event):
        if event.type() == QEvent.ToolTip:
            tip = self.tooltip_at(event.pos())
            if tip:
                QToolTip.showText(event.globalPos(), tip, self)
                return True
            QToolTip.hideText()
        return super().event(event)

    def mouseMoveEvent(self, event):              # noqa: N802
        hit = self.hit_test(event.position().toPoint())
        if hit != self._hover_hit:
            self._hover_hit = hit
            self.update()
        enabled = hit in ("selector", self.MENU_KEY) \
            or bool(hit and hit.startswith("crumb:")) \
            or bool(hit and self.can_go.get(hit, False))
        self.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):                  # noqa: N802
        self._hover_hit = None
        self.setCursor(Qt.ArrowCursor)
        self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event):               # noqa: N802
        if event.key() in (Qt.Key_Down, Qt.Key_Return, Qt.Key_Enter,
                           Qt.Key_Space):
            self.select.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):              # noqa: N802
        hit = self.hit_test(event.position().toPoint())
        enabled = hit in ("selector", self.MENU_KEY) \
            or bool(hit and hit.startswith("crumb:")) \
            or bool(hit and self.can_go.get(hit, False))
        self._pressed_hit = hit if event.button() == Qt.LeftButton \
            and enabled else None
        if self._pressed_hit:
            self.setFocus(Qt.MouseFocusReason)

    def mouseReleaseEvent(self, event):            # noqa: N802
        hit = self.hit_test(event.position().toPoint())
        activate = event.button() == Qt.LeftButton \
            and hit == self._pressed_hit
        self._pressed_hit = None
        if not activate:
            return
        if hit in ("selector", self.MENU_KEY):
            self.select.emit()
        elif hit and hit.startswith("crumb:"):
            self.open_display.emit(hit.partition(":")[2])
        elif hit:
            self.navigate.emit(hit)

    def paintEvent(self, event):                   # noqa: N802
        painter = QPainter(self)
        self._ground(painter)
        rects = self.button_rects()
        for name, _glyph in self.BUTTONS:
            # A navigation button that cannot go anywhere is FAINT, not
            # hidden: a control that vanishes moves the ones beside it,
            # and a moving target is the thing a hand learns wrong.
            role = (Role.ACTION if self.can_go.get(name, False)
                    else Role.TEXT_FAINT)
            _paint_button_ground(
                painter, rects[name], self.palette_,
                hovered=name == self._hover_hit,
                pressed=name == self._pressed_hit,
                enabled=self.can_go.get(name, False))
            draw_operator_icon(painter, rects[name], name, self.palette_, role)

        menu_rect = rects[self.MENU_KEY]
        _paint_button_ground(
            painter, menu_rect, self.palette_,
            hovered=self.MENU_KEY == self._hover_hit,
            pressed=self.MENU_KEY == self._pressed_hit)
        painter.setPen(self.palette_.pen(Role.LINE, 1.0))
        painter.setBrush(self.palette_.brush(Role.SURFACE_FIELD))
        painter.drawRoundedRect(QRectF(menu_rect), 3.0, 3.0)
        icon_rect = QRect(menu_rect.left() + 4, menu_rect.top(),
                          24, menu_rect.height())
        draw_operator_icon(
            painter, icon_rect, "menu", self.palette_, Role.ACTION)
        self._text(painter, menu_rect.adjusted(28, 0, -5, 0), "Displays",
                   Role.TEXT, Qt.AlignLeft | Qt.AlignVCenter)

        level_box = self.level_rect()
        painter.setPen(self.palette_.pen(Role.LINE_SOFT, 1.0))
        painter.setBrush(self.palette_.brush(Role.SURFACE_SUNK))
        painter.drawRoundedRect(QRectF(level_box), 3.0, 3.0)
        self._text(painter, level_box,
                   f"L{self.level}" if self.level in (1, 2, 3, 4) else "—",
                   Role.HEADING, Qt.AlignCenter)

        box = self.selector_rect()
        painter.setPen(self.palette_.pen(Role.LINE, 1.0))
        painter.setBrush(self.palette_.brush(Role.SURFACE_FIELD))
        painter.drawRoundedRect(QRectF(box), 3.0, 3.0)
        metrics = QFontMetrics(font_for(FontRole.CHROME))
        segments = self.breadcrumb_rects()
        if segments:
            for index, (target, segment, current) in enumerate(segments):
                if target:
                    label = metrics.elidedText(target, Qt.ElideRight,
                                               max(0, segment.width() - 8))
                else:
                    label = "…"
                self._text(
                    painter, segment, label,
                    Role.TEXT if current else Role.HEADING,
                    Qt.AlignLeft | Qt.AlignVCenter)
                if index < len(segments) - 1:
                    separator = QRect(segment.right() + 2, segment.top(),
                                      12, segment.height())
                    self._text(painter, separator, "›", Role.TEXT_FAINT,
                               Qt.AlignCenter)
        else:
            self._text(painter, box.adjusted(7, 0, -26, 0),
                       self.display_name or "No display", Role.TEXT_DIM,
                       Qt.AlignLeft | Qt.AlignVCenter)
        down_rect = QRect(box.right() - 22, box.top(), 20, box.height())
        draw_operator_icon(
            painter, down_rect, "down", self.palette_, Role.HEADING)
        painter.end()
