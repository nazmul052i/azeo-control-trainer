"""The generic PVM faceplate renderer — §6 on an actual screen.

One widget renders ANY registered PVM class from its declarations: the
class says what to bind, `resolve_state()` says how the current record
renders, and this file only does layout — which is exactly the division
I6 demands. Nothing here decides what Bad quality looks like.

Layout, fixed across every class so the eye lands in the same places:

    header        — display name, bound path, mode
    status strip  — marker + condition text + timestamp (ONE, fixed
                    position; §6: "not scattered indicators")
    value rows    — one per non-prop binding, dashes under Bad, an
                    explicit LAST GOOD row when history is showing
    action row    — primary button (Acknowledge when an alarm is
                    active, else Detail, §10.4) + the FORCED badge

Colours come from the DynaLive theme's roles — a PVM references tokens,
never literals, so retheming touches no class.
"""
from __future__ import annotations

from contextlib import nullcontext

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit, QMenu, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from ..theme.roles import Role
from ..theme.tokens import DEFAULT_THEME, THEMES
from .base import DisplayState, primary_path, resolve_state
from .faceplate_style import profile_for

#: PVM state token -> theme role. The contract names semantic tokens;
#: the theme decides the hex.
_TOKEN_ROLES = {
    "signal.critical": Role.ALARM_P1,
    "signal.warning": Role.ALARM_P2,
    "signal.advisory": Role.ALARM_P3,
    "signal.hold": Role.ACTION,
    "state.bad_quality": Role.TEXT_FAINT,
    "text.dimmed": Role.TEXT_DIM,
    "": Role.TEXT_DIM,
}


class _StateMarker(QWidget):
    """The §6 marker: shape backs up colour, so priority survives
    greyscale and deuteranopia. Square, triangle, hatched circle,
    hollow circle — or nothing at all when normal."""

    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self._palette = palette
        self._state: DisplayState | None = None
        self.setFixedSize(18, 18)

    def set_state(self, state: DisplayState) -> None:
        if state != self._state:
            self._state = state
            self.update()

    def paintEvent(self, event) -> None:            # noqa: N802
        state = self._state
        if state is None or not state.marker:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        colour = QColor(self._palette[_TOKEN_ROLES.get(
            state.token, Role.TEXT_DIM)])
        rect = self.rect().adjusted(3, 3, -3, -3)
        if state.marker == "square":
            painter.fillRect(rect, colour)
        elif state.marker == "triangle":
            painter.setBrush(QBrush(colour))
            painter.setPen(Qt.NoPen)
            painter.drawPolygon([rect.bottomLeft(), rect.bottomRight(),
                                 (rect.topLeft() + rect.topRight()) / 2])
        elif state.marker == "hatched_circle":
            painter.setPen(QPen(colour, 1.4))
            painter.setBrush(QBrush(colour, Qt.BDiagPattern))
            painter.drawEllipse(rect)
        elif state.marker == "hollow":
            painter.setPen(QPen(colour, 1.4))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(rect)
        painter.end()


class ContextualTitleBar(QFrame):
    """Icon, title, pin, minimize and close for contextual displays."""

    pin_requested = Signal()
    minimize_requested = Signal()
    close_requested = Signal()

    def __init__(self, title: str, path: str, parent=None):
        super().__init__(parent)
        self._drag_offset = None
        self.setCursor(Qt.OpenHandCursor)
        self.setObjectName("context_title")
        self.setStyleSheet(
            "#context_title { background: rgba(0,0,0,18); "
            "border-bottom: 1px solid rgba(0,0,0,45); }")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 3, 3, 3)
        self.icon = QLabel("◇")
        self.icon.setToolTip("Contextual display")
        layout.addWidget(self.icon)
        caption = "  ·  ".join(
            part for part in (str(title).strip(), str(path).strip()) if part)
        self.title = QLabel(caption)
        self.title.setFont(QFont("Segoe UI", 9, QFont.Bold))
        # A long module path must not resize the measured Azeo shell.  The
        # QLabel default minimum width is its entire text, which made a
        # 204-pixel AI faceplate balloon to nearly 500 pixels in Studio.
        self.title.setMinimumWidth(0)
        self.title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.title.setToolTip(caption)
        self.setToolTip(caption)
        # The complete caption is one drag target, rather than separate
        # QLabel mouse receivers with their own implicit mouse grab.
        self.icon.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.title.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.title, 1)
        self.pin = QPushButton("⌖")
        self.pin.setToolTip("Pin this display so another does not replace it")
        self.pin.clicked.connect(self.pin_requested.emit)
        self.minimize = QPushButton("—")
        self.minimize.setToolTip("Minimize to a mini faceplate")
        self.minimize.clicked.connect(self.minimize_requested.emit)
        close = QPushButton("×")
        close.setToolTip("Close")
        close.clicked.connect(self.close_requested.emit)
        for button in (self.pin, self.minimize, close):
            button.setCursor(Qt.ArrowCursor)
            # A stylesheet min-height of zero overrides setFixedSize when
            # Qt repolishes the pinned glyph, shrinking the window mid-drag.
            button.setStyleSheet("QPushButton { padding: 0; margin: 0; }")
            button.setFixedSize(20, 18)
            button.setAccessibleName(button.toolTip())
            layout.addWidget(button)

    def set_pinned(self, pinned: bool) -> None:
        self.pin.setText("●" if pinned else "⌖")
        self.pin.setToolTip(
            "Unpin this display" if pinned else
            "Pin this display so another does not replace it")

    def set_minimized(self, minimized: bool) -> None:
        self.minimize.setText("□" if minimized else "—")
        self.minimize.setToolTip("Expand" if minimized else
                                 "Minimize to a mini faceplate")

    def apply_theme(self, palette):
        self.setStyleSheet(
            f"#context_title {{ background: {palette[Role.SURFACE_PANEL_ALT]}; "
            f"border-bottom: 1px solid {palette[Role.LINE]}; }}"
            f"#context_title QLabel {{ background: transparent; color: {palette[Role.TEXT]}; }}"
            f"#context_title QPushButton {{ background: {palette[Role.SURFACE_PANEL_ALT]}; "
            f"color: {palette[Role.TEXT]}; border: 1px solid {palette[Role.LINE]}; }}"
            f"#context_title QPushButton:hover {{ background: {palette[Role.SELECTION]}; "
            f"color: {palette[Role.ON_SELECTION]}; }}")

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.parentWidget() is not None and not self.parentWidget().isWindow():
            # A docked card's window is the whole station. Its old native
            # drag gesture must never move the process workstation instead.
            event.ignore()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        window = self.window()
        # Windows' system-move handoff enters a separate native move loop.
        # Keep one Qt mouse gesture and one global anchor so quick moves,
        # reversals and releases do not stall or jump between handlers.
        # Wayland alone requires the compositor to position top-level windows.
        if QGuiApplication.platformName().startswith("wayland"):
            handle = window.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        self._drag_offset = (
            event.globalPosition().toPoint() - window.frameGeometry().topLeft()
        )
        self.setCursor(Qt.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None \
                and event.buttons() & Qt.MouseButton.LeftButton:
            position = event.globalPosition().toPoint() - self._drag_offset
            if self.window().pos() != position:
                self.window().move(position)
            event.accept()
            return
        if self._drag_offset is not None:
            self._cancel_drag()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self._drag_offset is not None:
            self._cancel_drag()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _cancel_drag(self) -> None:
        self._drag_offset = None
        self.setCursor(Qt.OpenHandCursor)

    def event(self, event) -> bool:
        if event.type() in (QEvent.Hide, QEvent.UngrabMouse, QEvent.WindowDeactivate):
            self._cancel_drag()
        return super().event(event)


class PvmFaceplateWidget(QWidget):
    """Renders one PVM class bound to one path, live.

    `pvm_cls` is any registered PvmClass; `params` its PARAMS values;
    `engine` a Phase 2 BindingEngine over the source of your choosing.
    Call `refresh()` per frame (the widget runs its own timer when
    shown; tests drive `refresh()` directly).
    """

    POLL_MS = 250

    #: Emitted with an icon-button key ("detail", "dcc", "primary",
    #: "studio", "history", "ack") when the operator presses one. The faceplate
    #: does not act on it: opening a detail display is the console's
    #: job, and a faceplate that opened its own windows would do it
    #: twice over in a host that already manages them (the same rule
    #: `faceplate_manager()` follows one layer up).
    action_requested = Signal(str)

    def __init__(self, pvm_cls: type, params: dict, engine,
                 theme: str = "azeo_live", config=None,
                 choices: dict | None = None, parent=None, *, live: bool = True,
                 window_flags=Qt.Widget):
        # Construct owned tools with their final flags so the host does not
        # need to recreate a native window handle before showing the faceplate.
        super().__init__(parent, window_flags)
        self._live_refresh = live
        # Faceplates provide their own contextual chrome. Keeping the native
        # Windows caption produced the highlighted, redundant title box above
        # it and made the measured shell look like a generic dialog.
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.pvm = pvm_cls() if isinstance(pvm_cls, type) else pvm_cls
        self.params = dict(params)
        self.engine = engine
        self.palette_roles = THEMES.get(theme, THEMES[DEFAULT_THEME])
        self.bound = self.pvm.bind_all(engine, self.params,
                                       config=config,
                                       choices=choices)
        self.state: DisplayState | None = None
        self.write_handler = None
        self.write_checker = None
        self.write_controls: dict = {}
        self.pinned = False
        self.minimized = False
        self.mini_faceplate = False
        self.faceplate_profile = profile_for(self.pvm)
        self._desktop_size = None
        self._surface_scroll = None

        path_text = " · ".join(str(v) for v in self.params.values())
        context_path = path_text if len(self.params) <= 1 \
            else "%d paths" % len(self.params)
        if self.faceplate_profile.family == "machine":
            context_path = str(self.params.get("path", ""))
        self.setWindowTitle(f"{self.pvm.display_name or self.pvm.block_type}"
                            f" — {path_text}")
        from ..theme.widgets import apply_widget_theme
        apply_widget_theme(self, theme if theme in THEMES else DEFAULT_THEME, basic=True)

        root = QVBoxLayout(self)
        self._root_layout = root
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.setSizeConstraint(QLayout.SetFixedSize)
        # Loop_fp already identifies the module prominently in its body. Its
        # contextual strip is therefore a concise path label, not a second
        # repetition of "PID Controller" in a 203-pixel window.
        context_name = "" if self.faceplate_profile.family == "loop" else (
            self.pvm.display_name or self.pvm.block_type)
        self.context_title = ContextualTitleBar(context_name, context_path)
        self.context_title.apply_theme(self.palette_roles)
        self.context_title.pin_requested.connect(self.toggle_pin)
        self.context_title.minimize_requested.connect(self.toggle_minimized)
        self.context_title.close_requested.connect(self.close)
        root.addWidget(self.context_title)

        # The CHM figures specify compact, fixed content shells.  Letting the
        # top-level layout expand turned a 204 px Loop_fp into the 600+ px
        # blank dialog visible in Studio, and moved the OUT/PV bars apart.
        self.faceplate_surface = QFrame()
        self.faceplate_surface.setObjectName("azeo_faceplate_surface")
        self.faceplate_surface.setFixedSize(
            self.faceplate_profile.width, self.faceplate_profile.height)
        from ..theme.widgets import set_surface_theme
        set_surface_theme(self.faceplate_surface, self.palette_roles)
        surface_root = QVBoxLayout(self.faceplate_surface)
        if self.pvm.role in ("faceplate", "detail"):
            # These are measured whole surfaces. Reflowing their parts through
            # layout margins displaced the PV channel in the faceplates and
            # narrowed the configuration columns in the details.  Every detail
            # profile is already its complete reference content size; applying
            # another 20 px of host margin made AO, PI and Device panels smaller
            # than the geometry their own controls were designed for.
            surface_root.setContentsMargins(0, 0, 0, 0)
            surface_root.setSpacing(0)
        else:
            surface_root.setContentsMargins(10, 4, 10, 5)
            surface_root.setSpacing(2)
        root.addWidget(self.faceplate_surface)

        # A class that declares FACEPLATE_LAYOUT gets Azeo's own
        # eight-part stack instead of the generic shell below. The
        # generic shell is not deprecated by this — it is what a class
        # with no declared layout still gets, so adding a layout to
        # one class cannot break the other ten.
        self.sections: dict = {}
        if self.pvm.role == "detail":
            self._build_detail_layout(surface_root)
            self._build_write_controls(surface_root, visible=False)
            self._timer = QTimer(self)
            self._timer.timeout.connect(self.refresh)
            self.refresh()
            return
        declared = tuple(getattr(self.pvm, "FACEPLATE_LAYOUT", ()) or ())
        if declared:
            self._build_azeo_layout(surface_root, declared, path_text)
            value = self.sections.get("value")
            if value is not None and hasattr(value, "mini_button"):
                value.mini_button.clicked.connect(self.toggle_minimized)
            self._build_write_controls(
                surface_root,
                # Azeo carries writes in class-owned controls (slews,
                # state buttons and reset controls), never as a generic
                # stack of key/QLineEdit/WRITE rows below the faceplate.
                visible=False)
            self._timer = QTimer(self)
            self._timer.timeout.connect(self.refresh)
            self.refresh()
            return

        # Detail classes without a declared Azeo section tuple still use
        # the measured surface; only their internal rows are generic.
        root = surface_root

        # Header
        header = QHBoxLayout()
        title = QLabel(self.pvm.display_name or self.pvm.block_type)
        title.setFont(QFont("Segoe UI", 11, QFont.Bold))
        header.addWidget(title)
        header.addStretch(1)
        self.mode_label = QLabel("")
        self.mode_label.setFont(QFont("Segoe UI", 9, QFont.Bold))
        header.addWidget(self.mode_label)
        root.addLayout(header)
        path_label = QLabel(path_text)
        path_label.setStyleSheet(
            f"color: {self.palette_roles[Role.TEXT_FAINT]};")
        root.addWidget(path_label)

        # Status strip — one, fixed position (§6).
        strip = QFrame()
        strip.setObjectName("status_strip")
        strip.setStyleSheet(
            f"#status_strip {{ background: "
            f"{self.palette_roles[Role.SURFACE_SUNK]}; "
            f"border-radius: 3px; }}")
        strip_layout = QHBoxLayout(strip)
        strip_layout.setContentsMargins(8, 4, 8, 4)
        self.marker = _StateMarker(self.palette_roles)
        strip_layout.addWidget(self.marker)
        self.condition_label = QLabel("")
        strip_layout.addWidget(self.condition_label, 1)
        self.since_label = QLabel("")
        self.since_label.setStyleSheet(
            f"color: {self.palette_roles[Role.TEXT_FAINT]};")
        strip_layout.addWidget(self.since_label)
        root.addWidget(strip)

        # Sheet-06 visual — the scale bars, above the value rows, for
        # classes that have one registered.
        from .render_bars import CLASS_VISUALS
        visual_factory = CLASS_VISUALS.get(type(self.pvm).__name__)
        self.visual = visual_factory(self.palette_roles) \
            if visual_factory else None
        if self.visual is not None:
            root.addWidget(self.visual, 1)

        # Value rows, one per non-prop, non-expression-hidden binding.
        # Document binds (the interlock table, the chart structure) are
        # panel food, not a value a row could show.
        _DOCUMENT_KEYS = ("conditions", "chart")
        self.value_labels: dict[str, QLabel] = {}
        for spec in self.pvm.bindings:
            if spec.prop or spec.key in _DOCUMENT_KEYS:
                continue
            if not spec.expr and spec.key not in self.bound:
                continue        # gated by Presence / Present Online
            row = QHBoxLayout()
            name = QLabel(spec.key)
            name.setStyleSheet(
                f"color: {self.palette_roles[Role.TEXT_DIM]};")
            row.addWidget(name)
            row.addStretch(1)
            value = QLabel("")
            value.setFont(QFont("Consolas", 10))
            row.addWidget(value)
            self.value_labels[spec.key] = value
            root.addLayout(row)

        self._build_write_controls(root)

        # LAST GOOD — appears only while Bad quality shows history,
        # explicitly labelled as history (display state 1).
        self.last_good_label = QLabel("")
        self.last_good_label.setStyleSheet(
            f"color: {self.palette_roles[Role.ALARM_P2_TEXT]};")
        self.last_good_label.hide()
        root.addWidget(self.last_good_label)

        # Per-class panel, where the generic shell is not enough — the
        # interlock table, the cascade stack, the running chart.
        from .render_panels import CLASS_PANELS
        panel_factory = CLASS_PANELS.get(type(self.pvm).__name__)
        self.panel = panel_factory(self.palette_roles) \
            if panel_factory else None
        if self.panel is not None:
            root.addWidget(self.panel, 1)

        root.addStretch(1)

        # Action row: fixed at the bottom; only the primary changes.
        actions = QHBoxLayout()
        self.forced_badge = QLabel("FORCED")
        self.forced_badge.setStyleSheet(
            f"border: 2px solid {self.palette_roles[Role.ACTION_DEEP]};"
            f"color: {self.palette_roles[Role.ACTION_DEEP]};"
            "padding: 1px 6px; font-weight: bold;")
        self.forced_badge.hide()
        actions.addWidget(self.forced_badge)
        actions.addStretch(1)
        self.primary_button = QPushButton("DETAIL")
        actions.addWidget(self.primary_button)
        root.addLayout(actions)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self.refresh()

    def toggle_pin(self) -> bool:
        self.pinned = not self.pinned
        self.context_title.set_pinned(self.pinned)
        return self.pinned

    def fit_to_desktop(self, available_size) -> None:
        """Keep the measured body reachable on a small logical desktop."""
        self._desktop_size = available_size
        self._fit_surface_to_desktop()

    def _fit_surface_to_desktop(self) -> None:
        if self._desktop_size is None:
            return
        width = self._desktop_size.width()
        height = max(1, self._desktop_size.height() - self.context_title.sizeHint().height())
        surface = self.faceplate_surface
        if self._surface_scroll is None:
            if surface.width() <= width and surface.height() <= height:
                return
            # At 200% DPI the full PID body can be taller than the desktop.
            # Scroll the body only; its identity, pin and close stay visible.
            index = self._root_layout.indexOf(surface)
            self._root_layout.removeWidget(surface)
            self._surface_scroll = QScrollArea()
            self._surface_scroll.setFrameShape(QFrame.NoFrame)
            self._surface_scroll.setWidget(surface)
            self._root_layout.insertWidget(index, self._surface_scroll)
        scroll = self._surface_scroll
        extent = scroll.verticalScrollBar().sizeHint().width()
        vertical = surface.height() > height
        horizontal = surface.width() + (extent if vertical else 0) > width
        vertical = surface.height() + (extent if horizontal else 0) > height
        horizontal = surface.width() + (extent if vertical else 0) > width
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn if vertical else Qt.ScrollBarAlwaysOff)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn if horizontal else Qt.ScrollBarAlwaysOff)
        scroll.setFixedSize(min(width, surface.width() + (extent if vertical else 0)),
                            min(height, surface.height() + (extent if horizontal else 0)))
        self._root_layout.activate()
        self.adjustSize()

    @staticmethod
    def _set_layout_visible(layout, visible: bool, keep=None) -> None:
        for index in range(layout.count()):
            item = layout.itemAt(index)
            widget = item.widget()
            if widget is not None:
                if widget is not keep:
                    widget.setVisible(visible)
                continue
            child = item.layout()
            if child is not None:
                PvmFaceplateWidget._set_layout_visible(
                    child, visible, keep=keep)

    def toggle_minimized(self) -> bool:
        """Collapse to the contextual title bar; bindings remain live."""
        self.minimized = not self.minimized
        self._set_layout_visible(
            self._root_layout, not self.minimized, keep=self.context_title)
        self.context_title.set_minimized(self.minimized)
        self.adjustSize()
        return self.minimized

    def toggle_mini_faceplate(self) -> bool:
        """Switch Loop_fp between its full and critical-values shells.

        This is distinct from the window-title minimize control.  Azeo's
        in-faceplate button retains the identity and live PV/OUT/mode values;
        collapsing the entire window made that documented control misleading.
        """
        self.mini_faceplate = not self.mini_faceplate
        setter = getattr(self.visual, "set_mini", None)
        if setter is not None:
            setter(self.mini_faceplate)
        if self.faceplate_profile.family in ("loop", "analog"):
            height = getattr(self.visual, "MINI_HEIGHT", 107) \
                if self.mini_faceplate else self.faceplate_profile.height
            self.faceplate_surface.setFixedSize(
                self.faceplate_profile.width, height)
            self._fit_surface_to_desktop()
            self.adjustSize()
            return self.mini_faceplate
        for name, section in self.sections.items():
            if name == "title":
                section.show()
            elif name in ("value", "mode"):
                section.hide()
            else:
                section.setVisible(not self.mini_faceplate)
        height = 117 if self.mini_faceplate \
            else self.faceplate_profile.height
        self.faceplate_surface.setFixedSize(
            self.faceplate_profile.width, height)
        self._fit_surface_to_desktop()
        self.adjustSize()
        return self.mini_faceplate

    def _build_write_controls(self, root, *, visible: bool = True) -> None:
        """Materialize every non-persistent writable binding."""
        # A writable declaration must reach an operator control. Before
        # this row, writable=True was metadata only: SP, OUT and device
        # commands rendered as values with no way to use them.
        for spec in self.pvm.bindings:
            binding = self.bound.get(spec.key)
            if not spec.writable or spec.persists \
                    or binding is None or isinstance(binding, tuple):
                continue
            row = QHBoxLayout()
            caption = QLabel(spec.key)
            caption.setStyleSheet(
                f"color: {self.palette_roles[Role.TEXT_DIM]};")
            row.addWidget(caption)
            editor = QLineEdit()
            editor.setFont(QFont("Consolas", 9))
            editor.setPlaceholderText("operator value")
            row.addWidget(editor, 1)
            write = QPushButton("WRITE")
            write.setEnabled(False)
            write.setToolTip("No operator write service is attached")
            write.clicked.connect(
                lambda _checked=False, key=spec.key:
                self.write_bound(key))
            row.addWidget(write)
            status = QLabel("")
            status.setStyleSheet(
                f"color: {self.palette_roles[Role.TEXT_DIM]};")
            row.addWidget(status)
            self.write_controls[spec.key] = (editor, write, status)
            root.addLayout(row)
            if not visible:
                for widget in (caption, editor, write, status):
                    widget.hide()

    # ------------------------------------------------- Azeo layout
    def _build_azeo_layout(self, root, declared, path_text) -> None:
        """The manual's eight-part stack, in its order.

        `faceplate_ui.build_sections` refuses an unknown section and a
        re-ordering, so a new faceplate is a tuple of section names and
        cannot quietly become a bespoke layout.
        """
        from PySide6.QtWidgets import QHBoxLayout as _HBox

        # Function-block section registration is UI work. Keeping this
        # import here stops registry-only tools from importing Qt merely
        # because SEQ/STD/ISEL faceplate declarations were registered.
        from . import faceplate_fb as _faceplate_fb  # noqa: F401
        from .faceplate_ui import BESIDE_PREVIOUS, build_sections
        from .render_bars import CLASS_VISUALS
        from .render_panels import CLASS_PANELS

        self.sections = build_sections(declared, self.palette_roles)
        for section in self.sections.values():
            if hasattr(section, "write_requested"):
                section.write_requested.connect(
                    lambda key, value: self.write_bound(key, value))
        visual_factory = CLASS_VISUALS.get(type(self.pvm).__name__)
        panel_factory = CLASS_PANELS.get(type(self.pvm).__name__)
        self.visual = visual_factory(self.palette_roles) \
            if visual_factory else None
        self.panel = panel_factory(self.palette_roles) \
            if panel_factory else None
        if self.visual is not None and hasattr(
                self.visual, "write_requested"):
            self.visual.write_requested.connect(
                lambda key, value: self.write_bound(key, value))
        if self.visual is not None and hasattr(
                self.visual, "mini_requested"):
            self.visual.mini_requested.connect(self.toggle_mini_faceplate)
        if self.visual is not None and hasattr(
                self.visual, "modeRequested"):
            self.visual.modeRequested.connect(self._show_mode_menu)
        if self.visual is not None and hasattr(
                self.visual, "action_requested"):
            self.visual.action_requested.connect(self.action_requested.emit)
        if self.panel is not None and hasattr(
                self.panel, "write_requested"):
            self.panel.write_requested.connect(
                lambda key, value: self.write_bound(key, value))
        if self.visual is not None and getattr(
                self.visual, "WHOLE_SURFACE", False):
            # Keep the declared section objects as the public, testable class
            # catalogue, but render these faceplates through one measured
            # surface. Splitting them across QSizeHints changes every gap
            # independently and moves the PV channel away from the reference.
            # These compatibility sections still carry signals and state.
            # Without a Qt owner, the button row survives every faceplate
            # close as a hidden top-level window through its signal connection.
            for section in self.sections.values():
                section.setParent(self.faceplate_surface)
                section.hide()
            if self.panel is not None:
                self.panel.setParent(self.faceplate_surface)
                self.panel.hide()
            root.addWidget(self.visual, 0)
            identity = getattr(self.visual, "set_identity", None)
            if identity is not None:
                description = "" if self.faceplate_profile.family == "loop" \
                    else self.pvm.display_name or self.pvm.block_type
                identity(path_text, description)
            parameters = getattr(self.visual, "set_parameters", None)
            if parameters is not None:
                parameters(self.params)
            buttons = self.sections.get("buttons")
            if buttons is not None:
                buttons.pressed.connect(self.action_requested.emit)
                buttons.set_visible_actions(self.faceplate_profile.actions)
                buttons.set_available(())
            visual_actions = getattr(
                self.visual, "set_available_actions", None)
            if visual_actions is not None:
                visual_actions(())
            self.marker = None
            self.condition_label = None
            self.mode_label = None
            self.value_labels = {}
            self.last_good_label = None
            self.forced_badge = None
            self.primary_button = None
            return
        visual_added = False
        panel_added = False
        pending_row = None
        top_action = (
            len(self.faceplate_profile.actions) == 1
            and "title" in self.sections and "buttons" in self.sections)
        integrated = set()
        if self.faceplate_profile.family in (
                "loop", "analog_output", "device"):
            # These values and mode marks are painted inside the class's
            # measured bar body.  Keeping the generic widgets visible as
            # well was the orphan 0.0 and duplicated scale in the old UI.
            integrated = {"value", "mode"}
        elif self.faceplate_profile.family in (
                "tracking", "calculation", "sequence_control"):
            integrated = {"value", "trend"}
        for name, widget in self.sections.items():
            if name in integrated:
                widget.hide()
                continue
            if name == "title" and top_action:
                header = _HBox()
                header.setContentsMargins(0, 0, 0, 0)
                header.setSpacing(2)
                header.addWidget(widget, 1)
                header.addWidget(self.sections["buttons"], 0, Qt.AlignTop)
                root.addLayout(header)
                continue
            if name == "buttons" and top_action:
                continue
            if self.visual is not None and not visual_added \
                    and name in ("mode", "trend", "alarms", "unit",
                                 "buttons"):
                root.addWidget(self.visual, 0)
                visual_added = True
            if self.panel is not None and not panel_added and name == "buttons":
                root.addWidget(self.panel, 1)
                panel_added = True
            if name == "buttons" and self.faceplate_profile.family != "loop":
                # Azeo's action row is anchored to the foot of every
                # shell, including sparse selector and sequence bodies.
                root.addStretch(1)
            if name in BESIDE_PREVIOUS and pending_row is not None:
                # Beside, not below — the bar is tall and this is one
                # short word (see BESIDE_PREVIOUS).
                pending_row.addWidget(widget, 0, Qt.AlignTop)
                continue
            if name == "pv_bar":
                pending_row = _HBox()
                pending_row.setSpacing(4)
                pending_row.addWidget(widget, 1)
                root.addLayout(pending_row, 0)
                continue
            pending_row = None
            root.addWidget(widget, 0)
        if self.visual is not None and not visual_added:
            root.addWidget(self.visual, 0)
        if self.panel is not None and not panel_added:
            root.addWidget(self.panel, 1)
        title = self.sections.get("title")
        if title is not None:
            if self.faceplate_profile.family == "loop":
                title.setFixedHeight(61)
            tag = path_text.split("/")[0] if "/" in path_text \
                else path_text
            title.set_identity(
                tag, description=self.pvm.display_name
                or self.pvm.block_type)
        unit = self.sections.get("unit")
        if unit is not None:
            unit.set_unit(getattr(self.pvm, "UNIT_NAME", ""))
        buttons = self.sections.get("buttons")
        if buttons is not None:
            buttons.pressed.connect(self.action_requested.emit)
            buttons.set_visible_actions(self.faceplate_profile.actions)
            # The widget emits intents; only its host can open another
            # window. Keep every action inert until that host connects the
            # signal and explicitly advertises what it can service.
            buttons.set_available(())
        # The generic shell's widgets are what `refresh` writes to;
        # give the declared layout the same names so one refresh path
        # serves both and neither drifts.
        self.marker = None
        self.condition_label = None
        self.mode_label = None
        self.value_labels = {}
        self.last_good_label = None
        self.forced_badge = None
        self.primary_button = None

    def _build_detail_layout(self, root) -> None:
        """Build a module detail display from the PDF-defined shell.

        Details are deliberately dispatched separately from faceplate
        sections. Loop_dt is a two-column configuration/diagnostic display,
        not a taller PID faceplate, and rendering its declarations as generic
        key/value rows is the mismatch the reference figures exposed.
        """
        from .detail_ui import create_detail_panel

        detail = create_detail_panel(
            self.pvm, self.params, self.palette_roles,
            parent=self.faceplate_surface)
        if detail is None:
            raise ValueError(
                f"no detail panel registered for {type(self.pvm).__name__}")
        detail.action_requested.connect(self.action_requested.emit)
        detail.write_requested.connect(
            lambda key, value: self.write_bound(key, value))
        root.addWidget(detail, 1)
        self.sections = {"detail": detail}
        self.detail_panel = detail
        self.visual = None
        self.panel = None
        self.marker = None
        self.condition_label = None
        self.mode_label = None
        self.value_labels = {}
        self.last_good_label = None
        self.forced_badge = None
        self.primary_button = None

    # -------------------------------------------------- icon buttons
    def serviceable_actions(self) -> set:
        """Which icon buttons can actually do something from here.

        Exactly one is answerable from here: whether a detail display
        class exists for this block type. The other actions need a host —
        displays, Control Designer, a historian, an alarm monitor — and
        an alarm LIST being drawn is not the same as there being
        something to acknowledge against, so `ack` is not claimed here
        either. A host adds what it can service via
        `set_available_actions`; until then a greyed button with a
        reason is a true answer, and a live one that swallows the
        click is not.
        """
        from .base import registry

        available = set()
        # A composite can share a block type with a registered detail class,
        # but it has no single block for that detail to address.  Selecting
        # the first parameter made Cascade Pair silently open its master PID.
        if (tuple(getattr(self.pvm, "PARAMS", ())) == ("path",)
                or getattr(self.pvm, "DETAIL_PARAM", "") == "path") \
                and registry.get(self.pvm.block_type, "detail") is not None:
            available.add("detail")
        return available

    def set_available_actions(self, keys) -> None:
        """Let the host widen what the icon row offers."""
        available = set(keys or ())
        setter = getattr(self.visual, "set_available_actions", None)
        if setter is not None:
            setter(available.intersection(self.faceplate_profile.actions))
        buttons = (self.sections or {}).get("buttons")
        if buttons is not None:
            # An unavailable icon is not useful operator information here.
            # Hide it together with its hotspot instead of leaving a row of
            # grey circles that looks like unfinished functionality.
            buttons.set_visible_actions(
                available.intersection(self.faceplate_profile.actions))
            buttons.set_available(available)

    def faceplate_block(self) -> tuple[str, str]:
        """Return the module/block addressed by this faceplate's first path."""
        path = primary_path(self.params)
        module, separator, remainder = path.partition("/")
        block = remainder.split("/", 1)[0] if separator else ""
        return module, block

    def _faceplate_alarm_records(self):
        """Complete alarm rows from the binding source's shared registry.

        `BindingResult` intentionally carries only the worst condition needed
        by a PVM mark.  Loop_fp's scrollable table needs every condition, so it
        reads the same registry the banner uses instead of inventing a second
        alarm list from four local comparisons.
        """
        source = getattr(self.engine, "_source", None)
        registry = getattr(source, "alarm_state", None)
        module, block = self.faceplate_block()
        if registry is None or not module or not block:
            return None
        return registry.records(blocks=(f"{module}/{block}",))

    def visible_alarm_keys(self) -> tuple[str, ...]:
        records = self._faceplate_alarm_records()
        return tuple(record.key for record in (records or ()))

    # ------------------------------------------------ operator writes
    def set_write_handler(self, handler, checker=None) -> None:
        """Attach the host's checked write service to declared controls."""
        self.write_handler = handler
        self.write_checker = checker
        for _editor, button, _status in self.write_controls.values():
            button.setEnabled(handler is not None and checker is None)
            button.setToolTip(
                "Write the entered value"
                if handler is not None
                else "No operator write service is attached")
        self._refresh_write_permissions()

    def _operator_mode_choices(self) -> tuple[str, ...]:
        """Modes an operator may request; internal fallback modes stay absent."""
        if self.pvm.block_type == "FLC":
            return ("OOS", "MAN", "AUTO")
        return ("OOS", "MAN", "AUTO", "CAS", "RCAS", "ROUT")

    def _show_mode_menu(self, _requested: str = "") -> QMenu:
        """Open Loop_fp's actual-mode selector through the checked write path."""
        menu = QMenu(self)
        menu.setObjectName("loop_mode_menu")
        current = str(getattr(self.visual, "mode", "") or "").upper()
        controls = self.write_controls.get("mode.command")
        writable = bool(controls and controls[1].isEnabled())
        refusal = "No operator mode-write service is attached"
        if controls and controls[1].toolTip():
            refusal = controls[1].toolTip()
        for mode in self._operator_mode_choices():
            action = menu.addAction(mode)
            action.setCheckable(True)
            action.setChecked(mode == current)
            action.setEnabled(writable)
            if not writable:
                action.setToolTip(refusal)
            action.triggered.connect(
                lambda _checked=False, value=mode:
                self.write_bound("mode.command", value))
        self._mode_menu = menu
        arrow = self.visual.actual_mode_arrow_geometry()
        anchor = self.visual.mapToGlobal(QPoint(
            round(arrow.left()), round(arrow.bottom() + 2)))
        from azeo_control_trainer.core.presentation.headless import is_headless
        if not is_headless():
            menu.popup(anchor)
        return menu

    def _refresh_write_permissions(self) -> None:
        """Keep controls aligned with the block's current CanWrite state."""
        # Every decision remains live. Only the module inventory is shared;
        # rebuilding it for each writable key multiplied 151-module lookups.
        engine = getattr(self, "engine", None)
        source = getattr(engine, "_source", None)
        with getattr(source, "snapshot", nullcontext)():
            PvmFaceplateWidget._update_write_permissions(self)

    def _update_write_permissions(self) -> None:
        allowed_keys: set[str] = set()
        refusal_reasons: dict[str, str] = {}
        for spec in self.pvm.bindings:
            if not spec.writable:
                continue
            binding = self.bound.get(spec.key)
            if self.write_handler is None or binding is None \
                    or isinstance(binding, tuple):
                continue
            if self.write_checker is None:
                allowed_keys.add(spec.key)
                continue
            allowed = self.write_checker(binding.path)
            success = bool(getattr(
                allowed, "success", allowed is not False))
            reason = str(getattr(allowed, "error", "") or "")
            if success:
                allowed_keys.add(spec.key)
            elif reason:
                refusal_reasons[spec.key] = reason

        for key, (_editor, button, status) in self.write_controls.items():
            success = key in allowed_keys
            reason = refusal_reasons.get(key, "")
            button.setEnabled(success)
            if success:
                button.setToolTip("Write the entered value")
            elif reason:
                button.setToolTip(reason)
                status.setText(reason)

        # Measured whole-surface faceplates paint their own commands, so the
        # hidden generic WRITE buttons cannot be their permission source. A
        # surface gets the same checked decision and removes both the active
        # styling and hotspot for every refused key.
        setter = getattr(self.visual, "set_write_permissions", None)
        if setter is not None:
            setter(allowed_keys)

    def write_bound(self, key: str, value=None) -> bool:
        """Write one writable binding by key and show any refusal inline."""
        controls = self.write_controls.get(key)
        binding = self.bound.get(key)
        spec = next((one for one in self.pvm.bindings if one.key == key), None)
        if spec is None or not spec.writable or binding is None \
                or isinstance(binding, tuple) or self.write_handler is None:
            self._show_detail_write_result(
                key, False, "This value has no online write target")
            return False
        editor, _button, status = controls or (None, None, None)
        if self.write_checker is not None:
            allowed = self.write_checker(binding.path)
            if not bool(getattr(allowed, "success", allowed is not False)):
                reason = str(getattr(allowed, "error", "Write refused"))
                if status is not None:
                    status.setText(reason)
                self._show_detail_write_result(key, False, reason)
                self._refresh_write_permissions()
                return False
        current = binding.result.value
        if value is None:
            if editor is None:
                return False
            text = editor.text().strip()
            try:
                if isinstance(current, bool):
                    lowered = text.lower()
                    if lowered not in ("true", "false", "on", "off",
                                       "1", "0"):
                        raise ValueError("enter true or false")
                    value = lowered in ("true", "on", "1")
                elif isinstance(current, int) and not isinstance(current, bool):
                    value = int(text)
                elif isinstance(current, float):
                    value = float(text)
                else:
                    value = text
            except ValueError as error:
                if status is not None:
                    status.setText(str(error))
                return False
        current = binding.result
        transition = f"{current.value} → {value} {current.units or ''}".rstrip()
        if status is not None:
            status.setText("Sending…")
            status.setToolTip(transition)
        result = self.write_handler(binding.path, value)
        success = bool(getattr(result, "success", result is not False))
        reason = str(getattr(result, "error", "Write refused"))
        if status is not None:
            status.setText("Accepted" if success else reason)
        self._show_detail_write_result(key, success, reason)
        if success:
            self.engine.poll()
            self.refresh()
        return success

    def _show_detail_write_result(self, key: str, success: bool,
                                  message: str = "") -> None:
        panel = getattr(self, "detail_panel", None)
        reporter = getattr(panel, "set_write_result", None)
        if reporter is not None:
            reporter(key, success, message)
        reporter = getattr(self.visual, "show_write_result", None)
        if reporter is not None:
            reporter(key, success, message)

    def detail_class(self):
        """The detail PVM class for this faceplate's block, if any."""
        from .base import registry

        return registry.get(self.pvm.block_type, "detail")

    # --------------------------------------------------------- theme
    def apply_theme(self, theme: str) -> bool:
        """Re-skin this faceplate in place. False if nothing moved.

        Azeo re-skins a running session without a restart, so an open
        faceplate has to follow — one that kept its old colours until
        it was closed and reopened would leave two palettes on screen
        at once, which is worse than not offering themes at all.

        Sections re-read through `apply_theme` rather than being
        rebuilt, so window position, scroll and the trend's history
        survive the change. A theme switch is a change of appearance,
        not of what the operator was looking at.
        """
        palette = THEMES.get(theme)
        if palette is None or palette is self.palette_roles:
            return False
        self.palette_roles = palette
        from ..theme.widgets import apply_widget_theme
        apply_widget_theme(self, theme, basic=True)
        self.context_title.apply_theme(palette)
        from ..theme.widgets import set_surface_theme
        set_surface_theme(self.faceplate_surface, palette)
        for section in (self.sections or {}).values():
            applier = getattr(section, "apply_theme", None)
            if applier is not None:
                applier(palette)
        for furniture in (self.visual, self.panel):
            if furniture is None:
                continue
            applier = getattr(furniture, "apply_theme", None)
            if applier is not None:
                applier(palette)
            elif hasattr(furniture, "_palette"):
                furniture._palette = palette
                furniture.update()
        # Restyling must not sample or refresh fields: doing so can replace a
        # partially entered value or add a fabricated point to an inline trend.
        self.update()
        return True

    # ------------------------------------------------------------ lifecycle
    def showEvent(self, event) -> None:             # noqa: N802
        if self._live_refresh:
            self._timer.start(self.POLL_MS)
        super().showEvent(event)

    def hideEvent(self, event) -> None:             # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:            # noqa: N802
        self._timer.stop()
        for binding in self.bound.values():
            if not isinstance(binding, tuple):
                self.engine.unbind(binding)
        # CLEAR it, don't just release it. `bound` is what makes
        # "does this faceplate still hold its binding names?"
        # answerable, and that is the question a caller has to ask
        # before opening a second faceplate on the same path — the
        # names are unique per (class, path, key), so re-binding
        # while the first still holds them raises BindingError.
        self.bound = {}
        super().closeEvent(event)

    # -------------------------------------------------------------- refresh
    def _primary_result(self):
        for spec in self.pvm.bindings:
            if not spec.prop and not spec.expr:
                binding = self.bound.get(spec.key)
                if binding is not None and not isinstance(binding, tuple):
                    return binding.result
        return None

    def refresh(self) -> None:
        # Pinned faceplates share their display's engine. Polling the entire
        # engine here refreshed every other faceplate and even a hidden display
        # once per window per tick. Expressions still pull their dependencies.
        with getattr(self.engine._source, "snapshot", nullcontext)():
            self.engine.poll(binding for binding in self.bound.values()
                             if not isinstance(binding, tuple))
            self._refresh_write_permissions()
        primary = self._primary_result()
        if primary is None:
            return
        state = resolve_state(primary)
        self.state = state
        for key, (editor, _button, _status) in self.write_controls.items():
            binding = self.bound.get(key)
            if binding is not None and not isinstance(binding, tuple) \
                    and not editor.hasFocus():
                editor.setText(str(binding.result.value))

        if self.sections:
            # A declared layout owns its own rendering: each section
            # reads the bound map it was handed. Nothing here reaches
            # into a section's widgets, which is what keeps a section
            # replaceable.
            for section in self.sections.values():
                section.refresh(self.bound)
            alarm_list = self.sections.get("alarms")
            if alarm_list is not None and hasattr(alarm_list, "set_records"):
                alarm_list.set_records(self._faceplate_alarm_records())
            # A specialized visual or panel is class furniture beside
            # the declared sections, not one of them. Retaining it in
            # `_build_azeo_layout` without refreshing it left cascade
            # state, SFC steps and PID bars frozen on their first frame.
            if self.visual is not None:
                self.visual.refresh(self.bound)
                alarm_setter = getattr(
                    self.visual, "set_alarm_records", None)
                if alarm_setter is not None:
                    alarm_setter(self._faceplate_alarm_records())
            if self.panel is not None:
                self.panel.refresh(self.bound)
            return

        self.marker.set_state(state)
        self.condition_label.setText(
            state.name.replace("_", " ").upper()
            if state.name != "normal" else "")
        token_colour = self.palette_roles[_TOKEN_ROLES.get(
            state.token, Role.TEXT_DIM)]
        self.condition_label.setStyleSheet(
            f"color: {token_colour}; font-weight: bold;"
            if state.name != "normal" else "")
        self.mode_label.setText(state.mode_text)

        for spec in self.pvm.bindings:
            label = self.value_labels.get(spec.key)
            binding = self.bound.get(spec.key)
            if label is None or binding is None \
                    or isinstance(binding, tuple):
                continue
            row_state = resolve_state(binding.result)
            label.setText(row_state.value_text)
            label.setStyleSheet(
                f"color: {self.palette_roles[Role.TEXT_FAINT]};"
                if row_state.name in ("bad", "uncertain") else "")
        if state.show_last_good and primary.last_good_value is not None:
            import datetime
            at = datetime.datetime.fromtimestamp(
                primary.last_good_at).strftime("%H:%M:%S") \
                if primary.last_good_at else "?"
            self.last_good_label.setText(
                f"LAST GOOD  {primary.last_good_value:g}  at {at}")
            self.last_good_label.show()
        else:
            self.last_good_label.hide()

        self.forced_badge.setVisible(state.forced)
        self.primary_button.setText(state.primary_action)
        if self.visual is not None:
            self.visual.refresh(self.bound)
        if self.panel is not None:
            self.panel.refresh(self.bound)


def open_pvm_faceplate(block_type: str, path: str, store,
                       role: str = "faceplate",
                       parent=None) -> PvmFaceplateWidget | None:
    """Convenience: registry lookup + engine over the store's runtimes.

    Returns None (rather than an empty window) when no PVM class is
    registered for the block type — the caller decides what that means.
    """
    from ..binding import BindingEngine, LiveGraphSource
    from .base import registry

    pvm_cls = registry.get(block_type, role)
    if pvm_cls is None:
        return None
    engine = BindingEngine(LiveGraphSource.from_store(store))
    widget = PvmFaceplateWidget(pvm_cls, {"path": path}, engine,
                                parent=parent)
    return widget
