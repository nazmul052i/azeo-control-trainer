"""Regression checks for legible single-bar display hierarchy navigation."""
from __future__ import annotations

import os
import sys
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PySide6.QtWidgets import QApplication  # noqa: E402

from azeo_control_trainer.core.hmi.pvms.layout import (  # noqa: E402
    DisplayFrame,
    DisplaySet,
    Layout,
    Screen,
)
from azeo_control_trainer.azeo_operator_station.layout_surface import (  # noqa: E402
    FrameNavigationBar,
    StationLayoutSurface,
)
from azeo_control_trainer.azeo_operator_station.console import (  # noqa: E402
    LiveStation,
)
from azeo_control_trainer.core.hmi.theme.tokens import THEMES  # noqa: E402


PALETTE = THEMES["azeo_live"]


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_disabled_navigation_strip_does_no_layout_work(monkeypatch):
    app = _app()
    layout = Layout("Single")
    screen = layout.add_screen(Screen("Screen 1"))
    screen.add_frame(DisplayFrame("Main", navigation_bar=True))
    surface = StationLayoutSurface(layout, PALETTE)
    host = surface.hosts["Main"]
    host.navigation_enabled = False
    calls = []
    monkeypatch.setattr(host.navigation, "set_entries", lambda *a, **kw: calls.append(a))
    surface.set_navigation(None, "Plant")
    assert not calls
    surface.close()
    surface.deleteLater()
    app.processEvents()


def test_active_branch_precedes_lateral_unit_destinations() -> None:
    app = _app()
    display_set = DisplaySet("Plant")
    root = display_set.add_root("L1 Plant")
    units = [display_set.add_child(root, f"U{index:03d} L2")
             for index in range(10)]
    three = display_set.add_child(units[3], "U300 L3")
    display_set.add_child(three, "U300 L4")

    layout = Layout("Single")
    screen = layout.add_screen(Screen("Screen 1"))
    screen.add_frame(DisplayFrame(
        "Main", levels=(1, 2, 3, 4), navigation_bar=True))
    surface = StationLayoutSurface(
        layout, PALETTE)
    surface.resize(900, 500)
    surface.set_navigation(display_set, "U003 L2")
    app.processEvents()

    targets = surface.hosts["Main"].navigation.targets
    assert targets[:3] == ("L1 Plant", "U003 L2", "U300 L3")


def test_long_hierarchy_uses_reachable_overflow_instead_of_crushed_tabs() -> None:
    app = _app()
    bar = FrameNavigationBar(PALETTE)
    names = tuple(f"U{index:03d} - L2 Long Unit Operation Name"
                  for index in range(10))
    bar.resize(620, 30)
    bar.set_entries(names, current=names[4])
    bar.show()
    app.processEvents()

    visible = [button for button in bar._buttons if button.isVisible()]
    hidden = [button for button in bar._buttons if not button.isVisible()]
    assert visible
    assert hidden
    assert bar._button_pool[names[4]].isVisible()
    assert bar._overflow.isVisible()
    assert bar._overflow.text() == "Navigate"
    assert [action.text() for action in bar._overflow.menu().actions()] == [
        bar._shown_text(name) for name in names
    ]

    replacement = ("L1 Plant", "U300 L2", "U300 L3", *names)
    bar.set_entries(replacement, current="U300 L2")
    app.processEvents()
    visible_indexes = [
        bar._row.indexOf(button) for button in bar._buttons
        if button.isVisible()
    ]
    assert visible_indexes
    assert bar._row.indexOf(bar._overflow) > max(visible_indexes)


def test_coordination_never_replaces_explicitly_navigated_frame() -> None:
    class Frame:
        prevent_external_coordination = False
        coordinates = True
        relocation = "none"

    class LayoutModel:
        def frame(self, _name):
            return Frame()

        def coordination_targets(self, *_args):
            # A remembered descendant can be right for a companion frame,
            # but never for the frame the operator explicitly called up.
            return {"Main": "L4", "Companion": "L2"}

    class View:
        class Display:
            name = "old"

        display = Display()

    class Subject:
        _coordinating = False
        layout_model = LayoutModel()
        active_display_set = object()
        _recent_children = {}
        views = {"Main": View()}

        def show_display(self, display, **kwargs):
            self.calls.append((display, kwargs))
            return True

    subject = Subject()
    subject.calls = []

    LiveStation._coordinate_displays(subject, "L1", "Main")

    assert subject.calls == [(
        "L2",
        {"record": False, "target_frame": "Companion",
         "coordinate": False},
    )]


def test_deferred_navigation_layout_cannot_outlive_its_widget(monkeypatch) -> None:
    from shiboken6 import delete

    app = _app()
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *error: errors.append(error))
    bar = FrameNavigationBar(PALETTE)
    bar.set_entries(("Plant", "Unit"), current="Plant")
    delete(bar)
    app.processEvents()
    assert not errors, [str(error[1]) for error in errors]
