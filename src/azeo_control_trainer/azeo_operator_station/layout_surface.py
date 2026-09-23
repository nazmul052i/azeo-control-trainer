"""Runtime surfaces for every physical screen in an assigned layout."""
from __future__ import annotations

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QToolButton, QWidget

from azeo_control_trainer.core.hmi.theme.roles import Role
from azeo_control_trainer.core.hmi.pvms.layout import NAV_FLAT

NAV_HEIGHT = 30


class FrameNavigationBar(QWidget):
    """Display call-up buttons and their descendant alarm rollups."""

    navigate = Signal(str)

    def __init__(self, palette, parent=None):
        super().__init__(parent)
        self.palette = palette
        self.targets: tuple[str, ...] = ()
        self.rollups: dict[str, dict] = {}
        self.current = ""
        self._buttons: list[QToolButton] = []
        self._button_pool: dict[str, QToolButton] = {}
        self._overflow = QToolButton(self)
        self._overflow.setText("Navigate")
        self._overflow.setPopupMode(QToolButton.InstantPopup)
        self._overflow.setMenu(QMenu(self._overflow))
        self._overflow.hide()
        self._overflow_in_layout = False
        self._rebuilding = False
        # A free singleShot retains its Python callback after Qt deletes the
        # station. Own the timer so closing a window cancels deferred layout.
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.timeout.connect(self._place_entries)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(3, 2, 3, 2)
        self._row.setSpacing(2)
        # One persistent tail spacer. ``set_entries`` is called by station
        # chrome synchronisation, so adding a spacer on every refresh made the
        # native layout grow forever even when its buttons looked unchanged.
        self._row.addStretch(1)

    def set_entries(self, names, *, current="", rollups=None) -> None:
        targets = tuple(dict.fromkeys(names))
        if targets != self.targets:
            self._rebuilding = True
            # The overflow control belongs after every destination. Remove it
            # while the button row is rebuilt; leaving it in the layout made
            # ``More`` jump to the far left after changing hierarchy level.
            if self._overflow_in_layout:
                self._row.removeWidget(self._overflow)
                self._overflow_in_layout = False
                self._overflow.hide()
            for button in self._buttons:
                self._row.removeWidget(button)
                button.hide()
            self._buttons.clear()
            self.targets = targets
            for name in self.targets:
                button = self._button_pool.get(name)
                if button is None:
                    button = QToolButton(self)
                    button.setCheckable(True)
                    button.clicked.connect(
                        lambda _checked=False, target=name:
                        self.navigate.emit(target))
                    self._button_pool[name] = button
                # Insert before the one persistent tail spacer.
                self._row.insertWidget(self._row.count() - 1, button)
                button.show()
                self._buttons.append(button)
            self._rebuilding = False
        self.rollups = dict(rollups or {})
        self.current = current
        for button, name in zip(self._buttons, self.targets):
            summary = self.rollups.get(name, {})
            count = int(summary.get("count", 0) or 0)
            button.setText(f"{name}  {count}" if count else name)
            button.setChecked(name == current)
            button.setToolTip(
                f"{count} alarm(s), top priority "
                f"{summary.get('priority', 0)}" if count else name)
        self.apply_palette(self.palette)
        self._place_entries()
        # Replacing the display can briefly give this strip its transition
        # geometry. Re-evaluate after Qt has laid out the new frame so tabs do
        # not remain hidden from a stale, narrow width until the next resize.
        self._layout_timer.start(0)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not self._rebuilding:
            self._place_entries()

    def _shown_text(self, name: str) -> str:
        summary = self.rollups.get(name, {})
        count = int(summary.get("count", 0) or 0)
        return f"{name}  {count}" if count else name

    def _place_entries(self) -> None:
        """Keep the hierarchy legible and move excess destinations to More.

        Qt will otherwise compress every tool button until eleven long unit
        names become indistinguishable fragments.  Azeo's single-bar
        navigation remains a call-up surface, so overflow must stay reachable
        rather than merely being clipped.
        """
        if self._rebuilding or not self._buttons or self.width() <= 0:
            return
        spacing = max(0, self._row.spacing())
        margins = self._row.contentsMargins()
        available = max(0, self.width() - margins.left() - margins.right())
        widths = [max(1, button.sizeHint().width()) for button in self._buttons]
        browse_w = max(72, self._overflow.sizeHint().width())
        required = (sum(widths) + browse_w
                    + spacing * max(0, len(widths)))
        visible = set(range(len(self._buttons)))
        if required > available:
            budget = max(0, available - browse_w - spacing)
            # The breadcrumb is ordered first by StationLayoutSurface. Keep
            # root and current visible; fill the remaining width in hierarchy
            # order and put lateral destinations in the menu.
            mandatory = {0}
            if self.current in self.targets:
                mandatory.add(self.targets.index(self.current))
            visible = set()
            used = 0
            for index in sorted(mandatory):
                cost = widths[index] + (spacing if visible else 0)
                visible.add(index)
                used += cost
            for index, width in enumerate(widths):
                if index in visible:
                    continue
                cost = width + (spacing if visible else 0)
                if used + cost > budget:
                    continue
                visible.add(index)
                used += cost

        hidden_names = []
        for index, (button, name) in enumerate(zip(self._buttons, self.targets)):
            shown = index in visible
            button.setVisible(shown)
            if not shown:
                hidden_names.append(name)

        menu = self._overflow.menu()
        menu.clear()
        for name in self.targets:
            action = menu.addAction(self._shown_text(name))
            action.setCheckable(True)
            action.setChecked(name == self.current)
            action.triggered.connect(
                lambda _checked=False, target=name: self.navigate.emit(target))
        if self.targets and not self._overflow_in_layout:
            self._row.insertWidget(self._row.count() - 1, self._overflow)
            self._overflow_in_layout = True
        elif not self.targets and self._overflow_in_layout:
            self._row.removeWidget(self._overflow)
            self._overflow_in_layout = False
        self._overflow.setVisible(bool(self.targets))
        self._overflow.setToolTip(
            (f"Browse {len(self.targets)} hierarchy destinations"
             + (f" · {len(hidden_names)} hidden from the tab row"
                if hidden_names else "")) if self.targets else "")

    def apply_palette(self, palette) -> None:
        self.palette = palette
        self.setStyleSheet(
            "background: %s; border-bottom: 1px solid %s;"
            % (palette[Role.SURFACE_PANEL], palette[Role.LINE_SOFT]))
        roles = {15: Role.ALARM_P1, 11: Role.ALARM_P2, 7: Role.ALARM_P3}
        for button, name in zip(self._buttons, self.targets):
            priority = int(self.rollups.get(name, {}).get("priority", 0) or 0)
            foreground = palette[roles.get(priority, Role.TEXT)]
            button.setStyleSheet(
                "QToolButton { border: 0; padding: 2px 7px; color: %s; }"
                "QToolButton:checked { border-bottom: 2px solid %s; }"
                % (foreground, palette[Role.ACTION]))
        self._overflow.setStyleSheet(
            "QToolButton { border: 0; padding: 2px 8px; color: %s; }"
            "QToolButton::menu-indicator { image: none; }"
            % palette[Role.ACTION])


class FrameHost(QWidget):
    """One frame's optional navigation strip plus replaceable content."""

    navigate = Signal(str, str)

    def __init__(self, frame, palette, parent=None):
        super().__init__(parent)
        self.frame = frame
        self.content: QWidget | None = None
        self.navigation = FrameNavigationBar(palette, self) \
            if frame.navigation_bar else None
        self.navigation_enabled = self.navigation is not None
        if self.navigation is not None:
            self.navigation.navigate.connect(
                lambda target: self.navigate.emit(target, self.frame.name))
        self.placeholder = QLabel(frame.initial_display or frame.name, self)
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.apply_palette(palette)

    def set_content(self, widget: QWidget) -> None:
        if self.content is not None and self.content is not widget:
            self.content.hide()
            self.content.deleteLater()
        self.content = widget
        widget.setParent(self)
        widget.show()
        self.placeholder.hide()
        self._place()

    def clear_content(self) -> None:
        if self.content is not None:
            self.content.close()
            self.content.deleteLater()
            self.content = None
        self.placeholder.show()
        self._place()

    def take_content(self):
        widget, self.content = self.content, None
        if widget is not None:
            widget.hide()
        return widget

    def apply_palette(self, palette) -> None:
        self.placeholder.setStyleSheet(
            "border: 1px solid %s; color: %s; background: %s;"
            % (palette[Role.LINE], palette[Role.TEXT_DIM],
               palette[Role.SURFACE_SUNK]))
        if self.navigation is not None:
            self.navigation.apply_palette(palette)

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._place()
        super().resizeEvent(event)

    def _place(self) -> None:
        top = NAV_HEIGHT if self.navigation_enabled else 0
        if self.navigation is not None:
            self.navigation.setVisible(self.navigation_enabled)
            self.navigation.setGeometry(0, 0, self.width(), NAV_HEIGHT)
        target = self.content or self.placeholder
        target.setGeometry(0, top, self.width(), max(1, self.height() - top))


class StationLayoutSurface(QWidget):
    """Place child display views into one screen of a workstation layout."""

    navigate = Signal(str, str)

    def __init__(self, layout, palette, parent=None, *, screen_index=0):
        super().__init__(parent)
        self.layout_model = layout
        self.palette = palette
        self.screen_index = int(screen_index)
        self.widgets: dict[str, QWidget] = {}
        self.scale_percent = 100
        self._frames = self._visible_frames()
        self.hosts: dict[str, FrameHost] = {}
        # Compatibility for callers and tests that inspect placeholders.
        self.labels: dict[str, QLabel] = {}
        for frame in self._frames:
            host = FrameHost(frame, palette, self)
            host.navigate.connect(self.navigate)
            self.hosts[frame.name] = host
            self.labels[frame.name] = host.placeholder

    @property
    def screen_model(self):
        if 0 <= self.screen_index < len(self.layout_model.screens):
            return self.layout_model.screens[self.screen_index]
        return None

    def _visible_frames(self):
        screen = self.screen_model
        if screen is None:
            return []
        # The console owns the alarm banner as chrome. A layout's standard
        # AlarmBanner frame maps to that existing widget, not a second band.
        return [frame for frame in screen.frames
                if frame.initial_display != "AlarmBanner"]

    def frame_names(self) -> tuple:
        return tuple(frame.name for frame in self._frames)

    def set_frame_widget(self, frame_name: str, widget: QWidget) -> None:
        host = self.hosts.get(frame_name)
        if host is None:
            return
        previous = self.widgets.get(frame_name)
        if previous is not None and previous is not widget:
            previous.hide()
        host.set_content(widget)
        self.widgets[frame_name] = widget
        self._place_children()

    def clear_views(self) -> None:
        for host in self.hosts.values():
            host.clear_content()
        self.widgets.clear()
        self._place_children()

    def take_frame_widget(self, frame_name: str):
        self.widgets.pop(frame_name, None)
        host = self.hosts.get(frame_name)
        return host.take_content() if host is not None else None

    def set_scale(self, percent: int) -> None:
        self.scale_percent = max(50, min(200, int(percent)))
        self._place_children()

    def set_navigation(self, display_set, current: str,
                       rollups=None) -> None:
        if display_set is None:
            flat = hierarchical = ()
        else:
            flat = tuple(node.display for node in display_set.nodes())
            trail = display_set.breadcrumb(current)
            node = display_set.find(current)
            branch = [child.display for child in node.children] \
                if node is not None else []
            siblings = display_set.siblings(current) \
                if display_set.find(current) is not None else ()
            hierarchical = tuple(dict.fromkeys(
                [root.display for root in display_set.roots]
                + list(trail) + branch + list(siblings)))
        for host in self.hosts.values():
            if host.navigation is None or not host.navigation_enabled:
                continue
            names = flat if host.frame.navigation_style == NAV_FLAT \
                else hierarchical
            host.navigation.set_entries(
                names, current=current, rollups=rollups)

    def apply_palette(self, palette) -> None:
        self.palette = palette
        for host in self.hosts.values():
            host.apply_palette(palette)

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._place_children()
        super().resizeEvent(event)

    def _place_children(self) -> None:
        if not self._frames:
            return
        left = min(frame.rect[0] for frame in self._frames)
        top = min(frame.rect[1] for frame in self._frames)
        right = max(frame.rect[0] + frame.rect[2] for frame in self._frames)
        bottom = max(frame.rect[1] + frame.rect[3] for frame in self._frames)
        span_w, span_h = max(right - left, 0.001), max(bottom - top, 0.001)
        scale = self.scale_percent / 100.0
        area_w, area_h = self.width() * scale, self.height() * scale
        offset_x = (self.width() - area_w) / 2
        offset_y = (self.height() - area_h) / 2
        for frame in self._frames:
            x, y, width, height = frame.rect
            geometry = QRect(
                round(offset_x + (x - left) / span_w * area_w),
                round(offset_y + (y - top) / span_h * area_h),
                max(1, round(width / span_w * area_w)),
                max(1, round(height / span_h * area_h)),
            )
            self.hosts[frame.name].setGeometry(geometry)


__all__ = ["FrameHost", "FrameNavigationBar", "StationLayoutSurface"]
