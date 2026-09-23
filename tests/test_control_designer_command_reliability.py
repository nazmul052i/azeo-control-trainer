"""Regression coverage for Control Designer's visible command contract."""
from __future__ import annotations

import inspect
import os
from pathlib import Path
from types import MethodType, SimpleNamespace
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QAbstractButton, QDialog, QLabel, QToolButton,
)

import azeo_control_trainer.core.strategy.blocks  # noqa: E402,F401
from azeo_control_trainer.core.strategy.model.block_registry import (  # noqa: E402
    registry,
)
from azeo_control_trainer.azeo_control_designer.canvas.strategy_scene import (  # noqa: E402
    StrategyScene,
)
from azeo_control_trainer.azeo_control_designer.designer_tab import (  # noqa: E402
    StrategyDesignerTab,
)
from azeo_control_trainer.azeo_control_designer.designer_window import (  # noqa: E402
    StrategyDesignerWindow,
)
from azeo_control_trainer.azeo_control_designer.items.block_item import (  # noqa: E402
    BlockItem,
)
from azeo_control_trainer.azeo_control_designer.items.comment_item import (  # noqa: E402
    CommentItem,
)
from azeo_control_trainer.azeo_control_designer.items.wire_item import (  # noqa: E402
    WireItem,
)
from azeo_control_trainer.azeo_control_designer.panels.ribbon_bar import (  # noqa: E402
    RibbonBar,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _button(ribbon: RibbonBar, label: str):
    return next(button for button in ribbon.findChildren(QAbstractButton)
                if button.text() == label)


def test_ribbon_tabs_use_available_width_without_orphan_scroll_arrows() -> None:
    """A wide Control Designer window must not look like its tabs overflow."""
    app = _app()
    ribbon = RibbonBar()
    ribbon.resize(1500, 170)
    ribbon.show()
    app.processEvents()

    arrows = [button for button in ribbon._tab_bar.findChildren(QToolButton)
              if button.arrowType() != Qt.NoArrow]
    assert ribbon._tab_bar.width() > ribbon._tab_bar.sizeHint().width()
    assert not any(button.isVisible() for button in arrows)
    ribbon.close()


def test_control_designer_chrome_uses_branded_menu_and_shared_icons() -> None:
    """Menus and ribbon must read as one product, not two UI generations."""
    _app()
    window = StrategyDesignerWindow()

    top_actions = list(window.menuBar().actions())
    menus = [action.menu() for action in top_actions]
    assert [action.text().replace("&", "") for action in top_actions] == [
        "File", "Edit", "Insert", "Module", "View", "Tools", "Help",
    ]
    assert all(menu is not None and "QMenu" in menu.styleSheet()
               for menu in menus)

    from azeo_control_trainer.core.presentation.menu_style import _attach_icons

    for menu in menus:
        _attach_icons(menu)

    key_commands = {
        "New Strategy", "Undo", "Compile", "Zoom to Fit",
        "Compare Strategies...", "Control Designer Help...",
    }
    command_actions = {
        action.text().replace("&", "").split("\t", 1)[0]: action
        for menu in menus
        for action in menu.actions()
    }
    assert key_commands <= command_actions.keys()
    assert all(not command_actions[label].icon().isNull()
               for label in key_commands)
    for menu in menus:
        for action in menu.actions():
            if action.isSeparator() or not action.text():
                continue
            assert not action.icon().isNull(), action.text()

    title = window._ribbon.findChild(QLabel, "ControlDesignerTitle")
    mark = window._ribbon.findChild(QLabel, "ControlDesignerMark")
    assert title is not None and title.text() == "AZEO CONTROL DESIGNER"
    assert mark is not None and mark.pixmap() is not None
    assert "#004487" in window.styleSheet()
    window.close()


def test_parameter_compare_has_a_distinct_route_and_shared_online_state() -> None:
    _app()
    ribbon = RibbonBar()
    routed: list[str] = []
    ribbon.compareRequested.connect(lambda: routed.append("strategy"))
    ribbon.compareParametersRequested.connect(lambda: routed.append("parameters"))

    _button(ribbon, "Cmp Params").click()
    assert routed == ["parameters"]

    ribbon.set_online_state(True)
    assert not ribbon._btn_download.isEnabled()
    assert not ribbon._btn_module_download.isEnabled()
    assert not ribbon._btn_module_compile.isEnabled()
    assert ribbon._btn_module_offline.isEnabled()

    ribbon.set_online_state(False)
    assert ribbon._btn_download.isEnabled()
    assert ribbon._btn_module_download.isEnabled()
    assert ribbon._btn_module_compile.isEnabled()
    assert not ribbon._btn_module_offline.isEnabled()
    ribbon.deleteLater()


def test_inert_controller_and_execution_order_commands_are_not_advertised() -> None:
    _app()
    window = StrategyDesignerWindow()
    ribbon_labels = {
        button.text().replace("\n", " ")
        for button in window._ribbon.findChildren(QAbstractButton)
    }
    assert not {"Connect", "Disconnect", "I/O Config"} & ribbon_labels

    module_labels = {
        action.text().replace("&", "")
        for action in window._ribbon._module_menu.actions()
        if action.text()
    }
    assert "Order of Execution..." not in module_labels
    assert "Assign I/O..." not in module_labels

    # Keep the QAction wrapper alive while inspecting its Qt-owned menu.
    top_module_action = next(action for action in window.menuBar().actions()
                             if action.text() == "&Module")
    top_module = top_module_action.menu()
    top_labels = {action.text().replace("&", "")
                  for action in top_module.actions() if action.text()}
    assert "Order of Execution..." not in top_labels
    assert "Assign I/O..." not in top_labels

    # Keep the QAction wrappers alive while traversing their menus.  PySide's
    # Python wrapper owns the menu returned by ``QAction.menu()`` on some Qt
    # builds, so a comprehension that immediately discards the action can make
    # the otherwise valid QMenu wrapper appear deleted mid-assertion.
    top_actions = list(window.menuBar().actions())
    top_menus = {
        action.text().replace("&", ""): action.menu()
        for action in top_actions
    }
    assert "Window" not in top_menus
    operator_actions = [
        action.text().replace("&", "")
        for menu in top_menus.values() if menu is not None
        for action in menu.actions()
        if "Operator" in action.text()
    ]
    assert operator_actions == ["Operator Station..."]
    window.close()
    window.deleteLater()


def test_duplicate_is_exactly_one_copy_and_one_paste() -> None:
    calls: list[str] = []
    subject = SimpleNamespace(
        _cmd_copy=lambda: calls.append("copy") or True,
        _cmd_paste=lambda: calls.append("paste"),
    )
    BlockItem._cmd_duplicate(subject)
    assert calls == ["copy", "paste"]

    calls.clear()
    subject._cmd_copy = lambda: calls.append("copy") or False
    BlockItem._cmd_duplicate(subject)
    assert calls == ["copy"]


def test_duplicate_dialog_commands_delegate_to_one_modeless_route() -> None:
    calls: list[tuple[str, object]] = []
    subject = SimpleNamespace(
        _show_upload_dialog=lambda: calls.append(("upload", None)) or "upload",
        _show_checkpoint_dialog=lambda mode: calls.append(
            ("checkpoint", mode)) or mode,
        _show_controller_simulator=lambda: calls.append(
            ("simulator", None)) or "simulator",
        _show_diagnostics=lambda: calls.append(
            ("diagnostics", None)) or "diagnostics",
        _show_datalog_config=lambda: calls.append(
            ("datalog", None)) or "datalog",
    )

    assert StrategyDesignerTab._upload(subject) == "upload"
    assert StrategyDesignerTab._checkpoints(subject, "restore") == "restore"
    assert StrategyDesignerTab._controller_simulator(subject) == "simulator"
    assert StrategyDesignerTab._controller_diagnostics(subject) == "diagnostics"
    assert StrategyDesignerTab._datalog_config(subject) == "datalog"
    assert calls == [
        ("upload", None),
        ("checkpoint", "restore"),
        ("simulator", None),
        ("diagnostics", None),
        ("datalog", None),
    ]

    calls.clear()
    window = SimpleNamespace(_designer=subject)
    StrategyDesignerWindow._save_checkpoint(window)
    StrategyDesignerWindow._restore_checkpoint(window)
    assert calls == [("checkpoint", "save"), ("checkpoint", "restore")]


def test_datalog_shared_route_preserves_plugin(monkeypatch) -> None:
    from azeo_control_trainer.azeo_control_designer.dialogs import datalog_config

    captured = {}

    class _Dialog:
        def __init__(self, *, store, plugin, parent):
            captured.update(store=store, plugin=plugin, parent=parent)

    monkeypatch.setattr(datalog_config, "DataLogConfigDialog", _Dialog)
    store, plugin = object(), object()
    subject = SimpleNamespace(
        _store=store,
        _plugin=plugin,
        _show_retained_dialog=lambda key, factory: (
            captured.update(key=key) or factory()),
    )
    dialog = StrategyDesignerTab._show_datalog_config(subject)
    assert isinstance(dialog, _Dialog)
    assert captured == {
        "key": "datalog", "store": store, "plugin": plugin,
        "parent": subject,
    }


def test_modeless_command_dialog_is_parent_owned_and_reused() -> None:
    _app()
    tab = StrategyDesignerTab()
    created: list[QDialog] = []

    def factory():
        dialog = QDialog(tab)
        created.append(dialog)
        return dialog

    first = tab._show_retained_dialog("test-command", factory)
    second = tab._show_retained_dialog("test-command", factory)
    assert first is second
    assert created == [first]
    assert first.parent() is tab
    assert first.testAttribute(Qt.WA_DontShowOnScreen)

    tab.cleanup()
    tab.deleteLater()


def test_select_all_and_delete_cover_blocks_wires_and_comments() -> None:
    _app()
    scene = StrategyScene()
    source = registry.create("AI", "AI1")
    sink = registry.create("ABS", "ABS1")
    assert source is not None and sink is not None
    scene.add_block(source, QPointF(0, 0))
    scene.add_block(sink, QPointF(240, 0))
    wire = scene.add_wire(source.id, "OUT", sink.id, "IN")
    comment = scene.add_comment("note", QPointF(40, 120))
    assert wire is not None

    subject = SimpleNamespace(_active_scene=lambda: scene)
    StrategyDesignerTab._select_all(subject)
    selected = scene.selectedItems()
    assert sum(isinstance(item, BlockItem) for item in selected) == 2
    assert sum(isinstance(item, WireItem) for item in selected) == 1
    assert sum(isinstance(item, CommentItem) for item in selected) == 1

    undo_before = scene.undo_stack.count()
    StrategyDesignerTab._delete_selection(subject)
    assert scene.graph.blocks == {}
    assert scene.graph.wires == {}
    assert comment not in scene._comment_items
    assert scene.undo_stack.count() == undo_before + 1
    scene.deleteLater()


def test_operator_and_history_commands_use_the_workstation_delegate() -> None:
    opened: list[tuple] = []
    station = SimpleNamespace(
        open_process_history=lambda module, paths=None: opened.append(
            ("history", module, tuple(paths or ()))) or "history-window")
    explorer = SimpleNamespace(
        open_operator_station=lambda: opened.append(("operator", "")) or True)
    subject = SimpleNamespace(
        explorer=explorer,
        _live_station_window=station,
        _main_window=None,
        _live_station=lambda: station,
    )
    subject._show_main_window = MethodType(
        StrategyDesignerWindow._show_main_window, subject)

    assert subject._show_main_window() is station
    assert StrategyDesignerWindow._open_historian_popup(
        subject, tag="ctrl.PID1.PV", module="U100") == "history-window"
    assert opened == [
        ("operator", ""),
        ("operator", ""),
        ("history", "U100", ("ctrl.PID1.PV",)),
    ]

    forwarded = []
    host = SimpleNamespace(
        _open_historian_popup=lambda **kw: forwarded.append(kw) or "view")
    scene = SimpleNamespace(graph=SimpleNamespace(name="MODULE-1"))
    tab = SimpleNamespace(window=lambda: host, _active_scene=lambda: scene)
    assert StrategyDesignerTab._open_block_trend(
        tab, "block", ["ctrl.PID1.PV"]) == "view"
    assert forwarded == [{
        "tags": ["ctrl.PID1.PV"],
        "module": "MODULE-1",
    }]


def test_version_history_uses_the_active_canvas_file(tmp_path, monkeypatch) -> None:
    from azeo_control_trainer.azeo_control_designer.dialogs import version_history

    active = tmp_path / "ACTIVE.json"
    active.write_text("{}", encoding="utf-8")
    captured = {}

    class _Accepted:
        def connect(self, slot):
            captured["slot"] = slot

    class _Dialog:
        def __init__(self, strategy_path, parent=None):
            captured["path"] = Path(strategy_path)
            self.accepted = _Accepted()

        def setAttribute(self, *_args):
            pass

        def show(self):
            captured["shown"] = True

        def get_restore_path(self):
            return None

    monkeypatch.setattr(version_history, "VersionHistoryDialog", _Dialog)
    subject = SimpleNamespace(
        _active_canvas=lambda: SimpleNamespace(file_path=str(active)),
        _load=lambda _path: None,
    )
    StrategyDesignerTab._version_history(subject)
    assert captured == {
        "path": active,
        "slot": captured["slot"],
        "shown": True,
    }


def test_only_the_production_pvm_faceplate_is_visible() -> None:
    source = inspect.getsource(BlockItem.contextMenuEvent)
    assert "Open Faceplate..." in source
    assert "PVM Faceplate (Preview)" not in source

    opened = []
    item = SimpleNamespace(
        block=SimpleNamespace(block_type="PID"),
        _pvm_faceplate_available=lambda _block_type: True,
        _open_pvm_faceplate=lambda: opened.append("pvm") or "faceplate",
    )
    scene = SimpleNamespace(
        _block_items={"pid": item},
        graph=SimpleNamespace(blocks={"pid": item.block}),
    )
    subject = SimpleNamespace(_active_scene=lambda: scene)
    assert StrategyDesignerTab._open_pvm_block_faceplate(
        subject, "pid") == "faceplate"
    assert opened == ["pvm"]


def test_strategy_directory_is_resolved_at_command_time() -> None:
    for method in (
        StrategyDesignerWindow._open_strategy,
        RibbonBar._on_load,
        StrategyDesignerTab._save_as,
    ):
        source = inspect.getsource(method)
        assert "strategy_io.STRATEGY_DIR" in source
        assert "import STRATEGY_DIR" not in source
