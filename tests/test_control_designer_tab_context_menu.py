"""Contracts for module-tab commands in Control Designer."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import azeo_control_trainer.core.strategy.blocks  # noqa: E402,F401
from azeo_control_trainer.core.strategy.model.block_registry import registry  # noqa: E402
from azeo_control_trainer.azeo_control_designer import designer_tab  # noqa: E402
from azeo_control_trainer.azeo_control_designer.designer_tab import (  # noqa: E402
    StrategyDesignerTab,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _designer(names=("A", "B", "C", "D")):
    _app()
    tab = StrategyDesignerTab(store=SimpleNamespace(controller=None))
    canvases = [tab._create_canvas(name) for name in names]
    tab._canvas_tabs.setCurrentWidget(canvases[0])
    return tab, canvases


def _action(menu, label):
    return next(action for action in menu.actions()
                if action.text().split("\t", 1)[0] == label)


def _labels(tab):
    return [tab._canvas_label(tab._canvas_tabs.widget(index))
            for index in range(tab._canvas_tabs.count())]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("Close Module", ["A", "C", "D"]),
        ("Close Other Modules", ["B"]),
        ("Close Modules to the Right", ["A", "B"]),
        ("Close All Modules", []),
    ],
)
def test_tab_menu_close_commands_use_the_clicked_tab_scope(command, expected):
    tab, canvases = _designer()
    clicked = canvases[1]
    menu = tab._build_canvas_tab_context_menu(clicked)

    _action(menu, command).trigger()

    assert _labels(tab) == expected
    tab.cleanup()
    tab.deleteLater()


def test_tab_close_cancel_preserves_every_target_and_discard_closes(
    monkeypatch,
):
    tab, canvases = _designer(("A", "B", "C"))
    canvases[1].dirty = True
    prompted = []
    monkeypatch.setattr(designer_tab, "is_headless", lambda: False)

    def cancel(_parent, title, text, *_args):
        prompted.append((title, text))
        return designer_tab.QMessageBox.Cancel

    monkeypatch.setattr(designer_tab.QMessageBox, "question", cancel)
    assert not tab._close_other_canvas_tabs(canvases[0])
    assert _labels(tab) == ["A", "B", "C"]
    assert len(prompted) == 1
    assert "B" in prompted[0][1]

    monkeypatch.setattr(
        designer_tab.QMessageBox,
        "question",
        lambda *_args: designer_tab.QMessageBox.Discard,
    )
    assert tab._close_other_canvas_tabs(canvases[0])
    assert _labels(tab) == ["A"]
    tab.cleanup()
    tab.deleteLater()


def test_tab_menu_lifecycle_action_is_state_aware_and_targets_clicked_module(
    monkeypatch,
):
    tab, canvases = _designer(("ACTIVE_TAB", "CLICKED_TAB"))
    clicked = canvases[1]
    block = registry.create("ABS", "ABS1")
    assert block is not None
    clicked.scene.graph.add_block(block)
    monkeypatch.setattr(tab, "_keylock_refuses", lambda _action: False)

    online_indices = []
    monkeypatch.setattr(
        tab,
        "go_online_single",
        lambda index: online_indices.append(index) or True,
    )
    menu = tab._build_canvas_tab_context_menu(clicked)
    go_online = _action(menu, "Go Online")
    assert go_online.isEnabled()
    go_online.trigger()
    assert online_indices == [1]
    assert tab._canvas_tabs.currentWidget() is canvases[0]

    clicked.runtime = SimpleNamespace(is_online=True)
    offline_indices = []
    monkeypatch.setattr(
        tab,
        "take_canvas_offline_by_index",
        lambda index: offline_indices.append(index),
    )
    menu = tab._build_canvas_tab_context_menu(clicked)
    go_offline = _action(menu, "Go Offline")
    assert go_offline.isEnabled()
    go_offline.trigger()
    assert offline_indices == [1]
    assert tab._canvas_tabs.currentWidget() is canvases[0]
    tab.cleanup()
    tab.deleteLater()


def test_go_online_is_disabled_for_an_empty_module():
    tab, canvases = _designer(("EMPTY",))
    menu = tab._build_canvas_tab_context_menu(canvases[0])

    assert not _action(menu, "Go Online").isEnabled()
    assert not _action(menu, "Close Other Modules").isEnabled()
    assert not _action(menu, "Close Modules to the Right").isEnabled()
    tab.cleanup()
    tab.deleteLater()
