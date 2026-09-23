"""Operator numeric-value context menus drive the shared historian."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

import azeo_control_trainer.core.hmi.pvms  # noqa: E402,F401
from azeo_control_trainer.core.hmi.binding.engine import Binding  # noqa: E402
from azeo_control_trainer.core.hmi.binding.result import (  # noqa: E402
    BindingResult,
)
from azeo_control_trainer.core.hmi.pvms.base import Pvm  # noqa: E402
from azeo_control_trainer.core.hmi.pvms.publishing import (  # noqa: E402
    PvmDisplay,
)
from azeo_control_trainer.azeo_operator_station.console import (  # noqa: E402
    LiveStation,
)
from azeo_control_trainer.core.hmi.pvms.rendering.items import (  # noqa: E402
    PvmItem,
)
from azeo_control_trainer.core.hmi.pvms.rendering.viewer import (  # noqa: E402
    PvmDisplayView,
    chart_candidates,
)
from azeo_control_trainer.core.hmi.history import (  # noqa: E402
    ContinuousHistorian,
)
from azeo_control_trainer.core.hmi.history.view import (  # noqa: E402
    ProcessHistoryView,
)
from azeo_control_trainer.core.hmi.theme.tokens import THEMES  # noqa: E402


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _binding(path: str, value: float, *, units: str = "",
             eu_range=None) -> Binding:
    binding = Binding(path, "direct", path=path)
    binding.result = BindingResult(
        value=value, units=units, eu_range=eu_range)
    return binding


def test_value_context_menu_reaches_pvm_beneath_decoration() -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QContextMenuEvent

    app = _app()
    selected = []
    document = PvmDisplay(name="Decorated value", width=320, height=180, pvms=[Pvm(
        id="loop", pvm_class="", block_type="AI", role="dynamo_compact",
        params={"path": "UNIT/AI1"}, x=50, y=60, w=160, h=54).to_dict()], items=[
            {"id": "card", "kind": "round_rect", "x": 20, "y": 20,
             "w": 260, "h": 120, "fill": "none", "z": 10}])
    view = PvmDisplayView(document.to_dict(), lambda: {}, live=False,
                          chart_handler=lambda path, binding: selected.append(path))
    view.resize(640, 400)
    view.show()
    app.processEvents()
    item = next(candidate for candidate in view.scene().items() if isinstance(candidate, PvmItem))
    item.rows = {}
    item.binding = _binding("UNIT/AI1/OUT", 42.5)
    point = view.mapFromScene(item.mapToScene(item.rect().center()))
    event = QContextMenuEvent(QContextMenuEvent.Mouse, point,
                             view.viewport().mapToGlobal(point), Qt.NoModifier)
    QApplication.sendEvent(view.viewport(), event)
    menu = view._last_chart_context_menu
    assert menu is not None
    menu.actions()[0].trigger()
    assert selected == ["UNIT/AI1/OUT"]
    view.close()


def test_numeric_pvm_menu_carries_real_binding_path() -> None:
    _app()
    pv = _binding("U100/PID1/PV", 42.5, units="degC",
                  eu_range=(0.0, 200.0))
    item = PvmItem(
        Pvm(id="loop", pvm_class="", block_type="PID",
            role="dynamo_inline", params={"path": "U100/PID1"}),
        pv, None, THEMES["azeo_live"], rows={"PV": pv})

    candidates = chart_candidates(item)
    assert [(one.label, one.path) for one in candidates] == [
        ("PV", "U100/PID1/PV")]

    opened = []
    view = PvmDisplayView(
        PvmDisplay(name="Chart context").to_dict(), lambda: {}, live=False,
        chart_handler=lambda path, binding: opened.append((path, binding)))
    menu = view.chart_context_menu(item)
    assert menu is not None
    assert [action.text() for action in menu.actions()] == ["Add to Historian"]
    menu.actions()[0].trigger()
    assert opened == [("U100/PID1/PV", pv)]
    menu.deleteLater()

    # Runtime popups are transient top-level menus, not children retained for
    # the lifetime of the published display.
    for _index in range(20):
        transient = view.chart_context_menu(item)
        assert transient is not None
        assert transient.parent() is None
        transient.deleteLater()
    QApplication.sendPostedEvents()
    assert view.findChildren(QMenu) == []
    view.close()


def test_non_numeric_or_unbound_values_have_no_chart_lie() -> None:
    _app()
    text = _binding("U100/PID1/MODE", 1.0)
    text.result = BindingResult(value="AUTO")
    item = PvmItem(
        Pvm(id="loop", pvm_class="", block_type="PID",
            role="dynamo_inline", params={"path": "U100/PID1"}),
        text, None, THEMES["azeo_live"])

    assert chart_candidates(item) == ()


def test_multi_value_pvm_menu_keeps_primary_and_other_values_explicit() -> None:
    _app()
    bindings = {label: _binding(f"UNIT/PID/{label}", 20.0) for label in ("PV", "SP", "OUT")}
    item = PvmItem(Pvm(id="loop", pvm_class="", block_type="PID", role="dynamo_inline",
                       params={"path": "UNIT/PID"}),
                   bindings["PV"], None, THEMES["azeo_live"], rows=bindings)
    selected = []
    view = PvmDisplayView(PvmDisplay(name="Values").to_dict(), lambda: {}, live=False,
                          chart_batch_handler=lambda points: selected.append(points))
    menu = view.chart_context_menu(item)
    menu.actions()[0].trigger()
    assert [path for path, _ in selected[-1]] == ["UNIT/PID/PV"]
    other = menu.actions()[1].menu()
    other.actions()[1].trigger()
    assert [path for path, _ in selected[-1]] == ["UNIT/PID/SP"]
    other.actions()[-1].trigger()
    assert [path for path, _ in selected[-1]] == [f"UNIT/PID/{label}" for label in bindings]
    menu.deleteLater()
    view.close()


def test_station_registers_selected_binding_with_its_engineering_metadata() -> None:
    binding = _binding(
        "U100/PID1/PV", 38.25, units="degC", eu_range=(-20.0, 180.0))
    historian = ContinuousHistorian(None)
    station = SimpleNamespace(historian=historian, graphs_provider=lambda: {})

    answer = LiveStation._register_chart_point(
        station, "U100/PID1/PV", binding)

    point = historian.TAGS["U100/PID1/PV"]
    assert answer is True
    assert (point.module, point.label, point.unit, point.lo, point.hi) == (
        "U100", "PV", "degC", -20.0, 180.0)


def test_process_history_view_appends_selected_points() -> None:
    _app()
    historian = ContinuousHistorian(None)
    historian.add_point("U100/PID1/PV", module="U100")
    historian.add_point("U100/PID1/SP", module="U100")
    view = ProcessHistoryView(historian)
    view._timer.stop()

    assert view.show_point("U100/PID1/PV")
    assert view.add_point("U100/PID1/SP")
    assert view._pens == ["U100/PID1/PV", "U100/PID1/SP"]
    assert not view.add_point("U100/PID1/DOES_NOT_EXIST")
    view.close()


def test_process_history_view_labels_cross_module_custom_chart() -> None:
    _app()
    historian = ContinuousHistorian(None)
    historian.add_point("U100/PID1/PV", module="U100")
    historian.add_point("U200/PID2/PV", module="U200")
    view = ProcessHistoryView(historian)
    view._timer.stop()

    assert view.show_point("U100/PID1/PV")
    assert view._modules.currentText() == "U100"
    assert view.add_point("U200/PID2/PV")
    assert view._pens == ["U100/PID1/PV", "U200/PID2/PV"]
    assert view._modules.currentIndex() == -1
    assert view._modules.placeholderText() == "Selected points"
    view.close()
