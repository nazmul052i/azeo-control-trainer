"""Every painted operator-station control must have a reachable action."""
from __future__ import annotations

import os
import sys
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtTest import QSignalSpy, QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from azeo_control_trainer.azeo_operator_station.shell.chrome import (  # noqa: E402
    ConsoleSettings,
)
from azeo_control_trainer.azeo_operator_station.shell.chrome_azeo import (  # noqa: E402
    ALL_BUTTONS,
    MenuBar,
    NavigationBar,
    OPERATOR_ICON_KEYS,
    draw_operator_icon,
)
from azeo_control_trainer.azeo_operator_station.shell.navigation import (  # noqa: E402
    NavigationStack,
)
from azeo_control_trainer.core.hmi.pvms.layout import (  # noqa: E402
    DisplaySet,
)
from azeo_control_trainer.core.hmi.pvms.base import Pvm  # noqa: E402
from azeo_control_trainer.core.hmi.pvms.publishing import (  # noqa: E402
    PvmDisplay,
)
from azeo_control_trainer.azeo_operator_station.console import (  # noqa: E402
    LiveStation,
)
from azeo_control_trainer.azeo_operator_station.dialogs import (  # noqa: E402
    AlarmBannerHelpDialog,
    DisplayTagSettingsDialog,
)
from azeo_control_trainer.core.hmi.pvms.rendering.viewer import (  # noqa: E402
    PvmDisplayView,
)
from azeo_control_trainer.core.hmi.theme.palette import (  # noqa: E402
    RolePalette,
)
from azeo_control_trainer.core.hmi.theme.roles import Role  # noqa: E402


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_every_painted_toolbar_button_emits_and_has_help() -> None:
    app = _app()
    bar = MenuBar(ConsoleSettings(azeo_chrome=True))
    bar.resize(1100, bar.HEIGHT)
    bar.show()
    spy = QSignalSpy(bar.activated)

    expected = []
    for key, rect in bar.button_rects().items():
        expected.append(key)
        assert bar.tooltip_at(rect.center())
        QTest.mousePress(bar, Qt.LeftButton, pos=rect.center())
        app.processEvents()
        assert spy.count() == len(expected) - 1
        QTest.mouseRelease(bar, Qt.LeftButton, pos=rect.center())
    app.processEvents()

    assert [spy.at(index)[0] for index in range(spy.count())] == expected


def test_display_selector_and_navigation_buttons_are_reachable() -> None:
    app = _app()
    bar = NavigationBar(ConsoleSettings(azeo_chrome=True))
    bar.resize(1000, bar.HEIGHT)
    bar.can_go.update(back=True, forward=True, home=True, up=True)
    bar.show()
    navigation = QSignalSpy(bar.navigate)
    selector = QSignalSpy(bar.select)

    for key in ("back", "forward", "home", "up"):
        rect = bar.button_rects()[key]
        assert bar.tooltip_at(rect.center())
        QTest.mouseClick(bar, Qt.LeftButton, pos=rect.center())
    selector_pos = bar.selector_rect().center()
    QTest.mousePress(bar, Qt.LeftButton, pos=selector_pos)
    app.processEvents()
    # Opening a QMenu on press lets the matching release dismiss it. The
    # station selector therefore follows native release-based activation.
    assert selector.count() == 0
    QTest.mouseRelease(bar, Qt.LeftButton, pos=selector_pos)
    menu_pos = bar.button_rects()[bar.MENU_KEY].center()
    assert bar.tooltip_at(menu_pos)
    QTest.mouseClick(bar, Qt.LeftButton, pos=menu_pos)
    QTest.keyClick(bar, Qt.Key_Down)
    app.processEvents()

    assert [navigation.at(index)[0]
            for index in range(navigation.count())] == [
                "back", "forward", "home", "up"]
    assert selector.count() == 3


def test_operator_icons_are_vector_marks_and_breadcrumbs_are_live() -> None:
    app = _app()
    visible_keys = {key for key, _glyph, _tip in ALL_BUTTONS}
    assert visible_keys <= OPERATOR_ICON_KEYS
    palette = RolePalette.for_theme("azeo_live")
    for key in sorted(OPERATOR_ICON_KEYS):
        image = QImage(28, 28, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        draw_operator_icon(painter, QRect(0, 0, 28, 28), key, palette)
        painter.end()
        assert any(image.pixelColor(x, y).alpha() for x in range(28)
                   for y in range(28)), key

    bar = NavigationBar(ConsoleSettings(azeo_chrome=True))
    bar.resize(1000, bar.HEIGHT)
    bar.set_context("Equipment", ("Plant", "Unit", "Equipment"), 3)
    bar.show()
    app.processEvents()
    opened = QSignalSpy(bar.open_display)
    plant_rect = next(rect for target, rect, _current
                      in bar.breadcrumb_rects() if target == "Plant")
    QTest.mouseClick(bar, Qt.LeftButton, pos=plant_rect.center())
    app.processEvents()

    assert bar.level == 3
    assert opened.count() == 1
    assert opened.at(0)[0] == "Plant"


def test_command_icon_colour_encodes_function_and_live_state() -> None:
    bar = MenuBar(ConsoleSettings(
        azeo_chrome=True, theme="azeo_live"))

    assert bar.icon_role("search") == Role.HEADING
    assert bar.icon_role("tools") == Role.ACTION
    assert bar.icon_role("exit") == Role.TEXT_DIM
    bar.bubbles["errors"] = True
    assert bar.icon_role("errors") == Role.ALARM_P2
    bar.enabled["search"] = False
    assert bar.icon_role("search") == Role.TEXT_FAINT

    # The live chrome must visibly contain both information blue and action
    # teal; a role mapping that never reaches paint would pass the checks above
    # while leaving the operator with the old monochrome row.
    bar.resize(1100, bar.HEIGHT)
    image = QImage(bar.size(), QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    bar.render(image)
    colours = {image.pixelColor(x, y).name()
               for x in range(image.width()) for y in range(image.height())}
    palette = RolePalette.for_theme("azeo_live")
    assert palette.color(Role.HEADING).name() in colours
    assert palette.color(Role.ACTION).name() in colours


def test_full_desktop_state_is_explicit_and_reversible() -> None:
    class Menu:
        selected = set()

        @staticmethod
        def update():
            return None

    class Subject:
        full_desktop = False
        menu = Menu()
        set_window_mode = LiveStation.set_window_mode

    subject = Subject()
    assert LiveStation.set_window_mode(subject, True) is True
    assert subject.menu.selected == {"mode"}
    assert LiveStation.set_window_mode(subject, False) is False
    assert not subject.menu.selected


def test_parent_and_recent_navigation_come_from_one_hierarchy_model() -> None:
    display_set = DisplaySet("Operations")
    plant = display_set.add_root("Plant")
    unit = display_set.add_child(plant, "Unit")
    display_set.add_child(unit, "Equipment")
    history = NavigationStack()
    for name in ("Plant", "Unit", "Equipment"):
        history.go(name)

    class Subject:
        active_display_set = display_set
        choose_display = staticmethod(lambda: tuple(display_set.displays()))
        display_menu_entries = LiveStation.display_menu_entries
        navigation_summary = LiveStation.navigation_summary

        def __init__(self):
            self.history = history
            self.opened = []

        def show_display(self, name, **options):
            self.opened.append((name, options))
            return True

        def sync_chrome(self):
            return None

    subject = Subject()
    summary = LiveStation.navigation_summary(subject)
    LiveStation.navigate(subject, "up")

    assert summary["branch"] == ("Plant", "Unit", "Equipment")
    assert summary["recent"] == ("Unit", "Plant")
    assert subject.opened == [("Unit", {"record": False})]


def test_display_menu_inventory_is_grouped_by_operating_level() -> None:
    class DisplaySet:
        @staticmethod
        def level_of(name):
            return {"Plant": 1, "Unit": 2, "Equipment": 3,
                    "Support": 4}[name]

    class Subject:
        active_display_set = DisplaySet()

        @staticmethod
        def choose_display():
            return ("Plant", "Unit", "Equipment", "Support")

    assert LiveStation.display_menu_entries(Subject()) == (
        (1, ("Plant",)), (2, ("Unit",)),
        (3, ("Equipment",)), (4, ("Support",)),
    )


def test_tag_settings_and_banner_help_are_real_dialogs() -> None:
    _app()
    settings = DisplayTagSettingsDialog("friendly")
    assert settings.selected_mode() == "friendly"
    assert settings.preview.text()
    help_dialog = AlarmBannerHelpDialog((
        ("ACK", "Acknowledge visible alarms"),
        ("?", "Open this help"),
    ))
    assert help_dialog.rows[0][0] == "ACK"

    view = PvmDisplayView(
        PvmDisplay(name="Tag mode").to_dict(), lambda: {}, live=False)
    assert view.set_show_tag("none")
    assert view.display.show_tag == "none"
    assert not view.set_show_tag("not-a-mode")
    view.close()
    settings.close()
    help_dialog.close()


def test_operator_pvm_faceplate_activation_is_one_left_click() -> None:
    app = _app()
    document = PvmDisplay(
        name="Operator activation", width=320, height=180,
        pvms=[Pvm(
            id="loop", pvm_class="", block_type="PID",
            role="dynamo_inline", variant="hp",
            params={"path": "UNIT/PID1"}, x=40, y=35,
            w=150, h=54,
        ).to_dict()],
    ).to_dict()
    activated = []
    view = PvmDisplayView(
        document, lambda: {}, live=False,
        pvm_activation_handler=lambda pvm, engine: activated.append(
            (pvm.id, engine)),
    )
    view.resize(420, 260)
    view.show()
    app.processEvents()
    item = next(candidate for candidate in view.scene().items()
                if getattr(candidate, "pvm", None) is not None)
    position = view.mapFromScene(
        item.mapToScene(item.boundingRect().center()))

    # The alternate button remains available for the station's value/chart
    # context menu and must never open a contextual faceplate.
    QTest.mouseClick(view.viewport(), Qt.RightButton, pos=position)
    app.processEvents()
    assert activated == []

    # A release over the PVM is not a click unless the press began there.
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=QPoint(5, 5))
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=position)
    app.processEvents()
    assert activated == []

    # Returning to the press point after a drag must not launch a faceplate.
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=position)
    QTest.mouseMove(view.viewport(), position + QPoint(30, 0))
    QTest.mouseMove(view.viewport(), position)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=position)
    app.processEvents()
    assert activated == []

    QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=position)
    app.processEvents()
    assert activated == [("loop", view.engine)]

    # Activation is explicitly opt-in. Generic published viewers therefore
    # do not acquire operator policy, and Studio's separate authoring canvas
    # retains its own selection/double-click behavior.
    plain = PvmDisplayView(document, lambda: {}, live=False)
    assert plain._pvm_activation_handler is None
    plain.close()
    view.close()
