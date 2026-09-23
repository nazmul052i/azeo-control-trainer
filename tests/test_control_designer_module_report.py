"""Focused contract tests for Control Designer's engineering module report."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from types import SimpleNamespace
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication, QAbstractButton  # noqa: E402

import azeo_control_trainer.core.strategy.blocks  # noqa: E402,F401
from azeo_control_trainer.core.strategy.engine.compiler import (  # noqa: E402
    compile_strategy,
)
from azeo_control_trainer.core.strategy.model.block_registry import (  # noqa: E402
    registry,
)
from azeo_control_trainer.core.strategy.model.strategy_graph import (  # noqa: E402
    StrategyGraph,
)
from azeo_control_trainer.core.strategy.model.terminal import Quality  # noqa: E402
from azeo_control_trainer.azeo_control_designer.designer_tab import (  # noqa: E402
    StrategyDesignerTab,
)
from azeo_control_trainer.azeo_control_designer.designer_window import (  # noqa: E402
    StrategyDesignerWindow,
)
from azeo_control_trainer.azeo_control_designer.dialogs.module_report_dialog import (  # noqa: E402
    ModuleReportDialog,
)
from azeo_control_trainer.azeo_control_designer.module_report import (  # noqa: E402
    ModuleReportOptions,
    build_module_report,
    render_module_report_html,
    write_module_report_html,
)
from azeo_control_trainer.azeo_control_designer.panels.ribbon_bar import (  # noqa: E402
    RibbonBar,
)


NOW = datetime(2026, 9, 1, 15, 30, tzinfo=timezone.utc)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _graph() -> tuple[StrategyGraph, object, object]:
    graph = StrategyGraph("U100_FEED")
    graph.description = "Feed & conditioning <module>"
    graph.scan_ms = 250
    graph.set_module_parameter(
        "MAX_MOVE", 12.5, access="internal_write",
        description="Maximum operator move")

    ai = registry.create("AI", "FT_101")
    absolute = registry.create("ABS", "PV_ABS")
    assert ai is not None and absolute is not None
    ai.config.params.update({
        "tag": "plant.feed.flow",
        "eng_units": "kg/h",
        "scale_lo": 0.0,
        "scale_hi": 200.0,
        "HI_HI_LIM": 190.0,
        "HI_LIM": 175.0,
        "LO_LIM": 15.0,
        "LO_LO_LIM": 5.0,
        "HI_HI_ENAB": True,
        "HI_ENAB": True,
        "LO_ENAB": True,
        "LO_LO_ENAB": True,
    })
    ai._apply_config()
    graph.add_block(ai)
    graph.add_block(absolute)
    assert graph.add_wire(ai.id, "OUT", absolute.id, "IN")
    return graph, ai, absolute


def test_report_is_a_complete_offline_engineering_snapshot(tmp_path) -> None:
    graph, _ai, _absolute = _graph()
    report = build_module_report(
        graph,
        module_name="U100/FEED",
        file_path=tmp_path / "feed.json",
        project_name="Azeo Plant",
        controller_name="PK-100",
        dirty=True,
        generated_at=NOW,
    )

    identity = dict(report.identity)
    assert identity["Module"] == "U100/FEED"
    assert identity["Project / area"] == "Azeo Plant"
    assert identity["Controller"] == "PK-100"
    assert identity["Unsaved edits"] == "Yes"
    assert {row.path for row in report.hierarchy} == {"FT_101", "PV_ABS"}
    assert [row.execution_order for row in report.hierarchy] == [0, 1]
    assert any(row.name == "MAX_MOVE" and row.origin == "Module parameter"
               for row in report.configuration)
    assert any(row.block_path == "FT_101" and row.name == "HI_LIM"
               and row.value == 175.0 and row.origin == "Configured"
               for row in report.configuration)
    assert report.live_available is False
    assert report.live_values == ()
    assert report.execution.compilable
    assert report.execution.module_scan_ms == 250
    assert [step.block_path for step in report.execution.steps] == [
        "FT_101", "PV_ABS"]
    assert {row.alarm for row in report.alarms} >= {
        "Hi Hi", "Hi", "Lo", "Lo Lo"}

    html = render_module_report_html(report)
    assert "Engineering Module Report" in html
    assert "Live values are unavailable" in html
    assert "Feed &amp; conditioning &lt;module&gt;" in html
    assert "Configuration defaults are not" in html

    target = write_module_report_html(report, tmp_path / "reports" / "feed.html")
    assert target.exists()
    assert target.read_text(encoding="utf-8") == html


def test_online_report_exposes_quality_alarm_state_and_execution_timing() -> None:
    graph, ai, absolute = _graph()
    ai.outputs["OUT"].value = 181.25
    ai.outputs["OUT"].status = Quality.BAD
    ai.outputs["HI_ACT"].value = True
    ai._last_exec_us = 11.0
    ai._exec_total_us = 30.0
    ai._max_exec_us = 19.0
    ai._exec_count = 2
    absolute._last_exec_us = 4.0
    compiled = compile_strategy(graph)
    runtime = SimpleNamespace(
        is_online=True,
        compiled=compiled,
        scan_count=400,
        last_scan_ms=0.73,
    )

    report = build_module_report(graph, runtime=runtime, generated_at=NOW)
    out = next(row for row in report.live_values
               if row.block_path == "FT_101" and row.name == "OUT")
    assert out.value == 181.25
    assert out.quality == "BAD"
    assert out.unit == "kg/h"
    high = next(row for row in report.alarms
                if row.block_path == "FT_101" and row.alarm == "Hi")
    assert high.enabled and high.state == "ACTIVE"
    assert report.execution.scan_count == 400
    assert report.execution.last_scan_ms == 0.73
    first = report.execution.steps[0]
    assert (first.last_us, first.average_us, first.maximum_us,
            first.execution_count) == (11.0, 15.0, 19.0, 2)

    html = render_module_report_html(report)
    assert "ONLINE" in html
    assert "ACTIVE" in html
    assert "BAD" in html


def test_nested_hierarchy_and_validation_are_not_flattened_or_hidden() -> None:
    graph = StrategyGraph("ROOT")
    composite = registry.create("COMPOSITE", "LOOP")
    duplicate_a = registry.create("ABS", "DUPLICATE")
    duplicate_b = registry.create("ABS", "DUPLICATE")
    inner = registry.create("ABS", "INNER_CALC")
    assert all((composite, duplicate_a, duplicate_b, inner))
    composite.inner_graph.add_block(inner)
    duplicate_a.config.params["UNKNOWN_SETTING"] = 7
    graph.add_block(composite)
    graph.add_block(duplicate_a)
    graph.add_block(duplicate_b)

    report = build_module_report(graph, generated_at=NOW)
    nested = next(row for row in report.hierarchy
                  if row.path == "LOOP/INNER_CALC")
    assert nested.parent == "LOOP" and nested.depth == 1
    assert not report.execution.compilable
    assert report.error_count == 1
    assert any("Duplicate block name" in row.text for row in report.validation)
    assert any("UNKNOWN_SETTING" in row.text and row.severity == "WARNING"
               for row in report.validation)

    html = render_module_report_html(
        report,
        ModuleReportOptions(
            configuration=False,
            live_values=False,
            alarms=False,
            execution=False,
        ),
    )
    assert "Block hierarchy" in html
    assert "Validation messages" in html
    assert "Configurable values" not in html
    assert "Live values and quality" not in html


def test_report_dialog_exports_headlessly_and_never_opens_native_dialogs(
    tmp_path,
) -> None:
    _app()
    graph, _ai, _absolute = _graph()
    report = build_module_report(graph, generated_at=NOW)
    dialog = ModuleReportDialog(lambda: report)

    assert dialog.show_print_preview() is False
    assert dialog.print_report() is False
    assert dialog.choose_export_html() is None
    assert dialog.choose_export_pdf() is None
    html_path = dialog.export_html(tmp_path / "module.html")
    pdf_path = dialog.export_pdf(tmp_path / "module.pdf")
    assert html_path.read_text(encoding="utf-8") == dialog.html
    assert pdf_path.read_bytes().startswith(b"%PDF")

    dialog._checks["configuration"].setChecked(False)
    assert "Configurable values" not in dialog.html
    dialog.close()
    dialog.deleteLater()


def test_report_export_chooser_reports_file_errors_instead_of_escaping_qt_slot(
    tmp_path, monkeypatch,
) -> None:
    _app()
    from azeo_control_trainer.azeo_control_designer.dialogs import (
        module_report_dialog as report_ui,
    )

    graph, _ai, _absolute = _graph()
    dialog = ModuleReportDialog(
        lambda: build_module_report(graph, generated_at=NOW))
    errors: list[tuple[str, str]] = []
    monkeypatch.setattr(report_ui, "is_headless", lambda: False)
    monkeypatch.setattr(
        report_ui.QFileDialog, "getSaveFileName",
        lambda *_args, **_kwargs: (str(tmp_path / "report.html"), ""),
    )
    monkeypatch.setattr(
        dialog, "export_html",
        lambda _path: (_ for _ in ()).throw(OSError("disk full")),
    )
    monkeypatch.setattr(
        dialog, "_show_error",
        lambda title, error: errors.append((title, str(error))),
    )

    assert dialog.choose_export_html() is None
    assert errors == [("HTML Export Failed", "disk full")]
    dialog.close()
    dialog.deleteLater()


def test_one_report_command_is_shared_by_ribbon_file_menu_and_active_canvas() -> None:
    _app()
    ribbon = RibbonBar()
    emitted: list[str] = []
    ribbon.moduleReportRequested.connect(lambda: emitted.append("report"))
    button = next(widget for widget in ribbon.findChildren(QAbstractButton)
                  if widget.text() == "Module Report")
    button.click()
    assert emitted == ["report"]
    ribbon.deleteLater()

    window = StrategyDesignerWindow()
    # Keep the QAction wrapper alive while reading its Qt-owned menu.
    file_action = next(action for action in window.menuBar().actions()
                       if action.text() == "&File")
    file_menu = file_action.menu()
    assert sum(action.text().replace("&", "") == "Module Report..."
               for action in file_menu.actions()) == 1
    window.close()
    window.deleteLater()

    tab = StrategyDesignerTab(plugin=SimpleNamespace(display_name="Plant A"))
    canvas = tab._create_canvas("MODULE")
    canvas.scene.graph.name = "MODULE"
    block = registry.create("ABS", "ABS1")
    assert block is not None
    canvas.scene.graph.add_block(block)
    dialog = tab._show_module_report()
    assert dialog is tab._module_report_dialog
    assert dialog.report.module_name == "MODULE"
    assert tab._show_module_report() is dialog
    dialog.close()
    tab.deleteLater()
