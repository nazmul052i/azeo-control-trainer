"""Render the complete Graphics Designer surface inventory for visual QA.

This is deliberately a production-widget harness, not a mock-up generator.
Every image is captured from the same Qt classes the application opens.  The
workspace is disposable so review never changes a shipped course display.
"""
# ruff: noqa: E402
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QAbstractButton,
    QAbstractSpinBox,
    QComboBox,
    QDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

import azeo_control_trainer.core.strategy.blocks  # noqa: F401
from azeo_control_trainer.azeo_graphics_designer.configurator.designer import (
    PreviewDialog,
    PvmConfigDesigner,
)
from azeo_control_trainer.azeo_graphics_designer.configurator.impact import (
    ImpactDialog,
)
from azeo_control_trainer.azeo_graphics_designer.engineering_tools import (
    AssemblyDialog,
    CommissioningDialog,
)
from azeo_control_trainer.azeo_graphics_designer.new_display import NewDisplayDialog
from azeo_control_trainer.azeo_graphics_designer.param_browser import (
    ParameterBrowserDialog,
)
from azeo_control_trainer.azeo_graphics_designer.quick_access import (
    CommandSearch,
    PropertySearch,
)
from azeo_control_trainer.azeo_graphics_designer.release_workflow import ReleaseDialog
from azeo_control_trainer.azeo_graphics_designer.revision_review import RevisionReview
from azeo_control_trainer.azeo_graphics_designer.script_editor import (
    ScriptAssistantDialog,
)
from azeo_control_trainer.azeo_graphics_designer.sequence_tools import SequenceDialog
from azeo_control_trainer.azeo_graphics_designer.studio.binding_editor import (
    BindingCatalog,
    BindingTarget,
    UnifiedBindingEditor,
    ValueType,
)
from azeo_control_trainer.azeo_graphics_designer.studio.display_properties import (
    DisplayPropertiesDialog,
)
from azeo_control_trainer.azeo_graphics_designer.studio.layout_editors import (
    DisplaySetEditor,
    LayoutEditor,
    WorkstationAssignmentDialog,
)
from azeo_control_trainer.azeo_graphics_designer.studio.structured_editors import (
    ActionListDialog,
    DataElementDialog,
    UserEntryDialog,
)
from azeo_control_trainer.azeo_graphics_designer.studio_help import (
    GraphicsDesignerAboutDialog,
    GraphicsDesignerHelpCenter,
    GraphicsDesignerTour,
)
from azeo_control_trainer.azeo_graphics_designer.window import (
    HmiStudioWindow,
    _PaletteCard,
)
from azeo_control_trainer.azeo_graphics_designer.worksheet import WorksheetDialog
from azeo_control_trainer.core.hmi.pvms.layout import (
    DisplaySet,
    LayoutStore,
    default_layout,
)
from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore, PvmDisplay
from azeo_control_trainer.core.hmi.theme.fonts import apply_application_font
from azeo_control_trainer.core.strategy.model.block_registry import BlockRegistry
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph


def flush(app: QApplication, rounds: int = 5) -> None:
    for _ in range(rounds):
        app.processEvents()


class Review:
    def __init__(self, output: Path, app: QApplication) -> None:
        self.output = output
        self.app = app
        self.output.mkdir(parents=True, exist_ok=True)
        self.rows: list[dict] = []
        self.failures: list[dict] = []

    def audit(self, widget: QWidget) -> list[str]:
        findings: list[str] = []
        widgets = [widget, *widget.findChildren(QWidget)]
        if isinstance(widget, QDialog):
            if not widget.property("authoringDialog"):
                findings.append("dialog does not use shared authoring chrome")
            if not widget.property("compactAuthoringDialog") \
                    and not widget.property("embeddedAuthoringShell") \
                    and widget.findChild(QWidget, "authoring_dialog_header") is None:
                findings.append("dialog does not use shared authoring header")
        bad_fonts = [
            f"{type(child).__name__}:{child.objectName() or '-'}"
            for child in widgets
            if child.font().pointSizeF() <= 0
        ]
        if bad_fonts:
            findings.append("non-positive point font: " + ", ".join(bad_fonts[:8]))
        if widget.width() > 1920 or widget.height() > 1080:
            findings.append(
                f"surface exceeds Full HD: {widget.width()} x {widget.height()}"
            )
        if widget.minimumSizeHint().width() > 1600:
            findings.append(
                f"minimum width exceeds review desktop: {widget.minimumSizeHint().width()}"
            )
        for child in widget.findChildren(QWidget):
            if not isinstance(child, (QPushButton, QComboBox, QLineEdit)):
                continue
            if isinstance(child, QLineEdit) \
                    and isinstance(child.parentWidget(), (QComboBox, QAbstractSpinBox)):
                continue
            if isinstance(child, QAbstractButton) and not child.text().strip():
                continue
            if child.isVisible() and child.height() < 20:
                findings.append(
                    f"undersized control: {type(child).__name__} "
                    f"{child.objectName() or child.toolTip() or '-'} ({child.height()} px)"
                )
                if len(findings) >= 12:
                    break
        for label in widget.findChildren(QLabel):
            text = label.text().strip()
            if (
                not label.isVisible()
                or not text
                or label.wordWrap()
                or "<" in text
                or label.pixmap() is not None
            ):
                continue
            required = label.fontMetrics().horizontalAdvance(text)
            if required > label.contentsRect().width() + 8:
                findings.append(
                    f"possibly clipped label: {text[:48]!r} "
                    f"({required}>{label.contentsRect().width()})"
                )
                if len(findings) >= 12:
                    break
        return findings

    def capture(
        self,
        widget: QWidget,
        name: str,
        *,
        size: tuple[int, int] | None = None,
    ) -> Path:
        if size is not None:
            widget.resize(*size)
        elif isinstance(widget, QDialog):
            hint = widget.sizeHint().expandedTo(widget.minimumSizeHint())
            desired = widget.size().expandedTo(hint) \
                if widget.testAttribute(Qt.WA_Resized) else hint
            widget.resize(min(max(desired.width(), 420), 1500),
                          min(max(desired.height(), 240), 1000))
        widget.show()
        widget.raise_()
        flush(self.app)
        path = self.output / f"{name}.png"
        pixmap = widget.grab()
        if pixmap.isNull() or not pixmap.save(str(path)):
            raise OSError(f"Could not capture {name}")
        findings = self.audit(widget)
        self.rows.append({
            "name": name,
            "file": path.name,
            "surface": type(widget).__name__,
            "size": [pixmap.width(), pixmap.height()],
            "findings": findings,
        })
        return path

    def attempt(self, name: str, factory, *, size=None, prepare=None) -> None:
        widget = None
        try:
            widget = factory()
            if prepare is not None:
                prepare(widget)
            self.capture(widget, name, size=size)
        except Exception as error:  # noqa: BLE001 - a review records all surfaces
            self.failures.append({
                "name": name,
                "error": str(error),
                "traceback": traceback.format_exc(),
            })
        finally:
            if widget is not None:
                widget.close()
                widget.deleteLater()
                flush(self.app, 2)

    def palette_sheets(self, window: HmiStudioWindow) -> None:
        cards = window.palette_box.findChildren(_PaletteCard)
        page_size = 24
        cell_w, cell_h = 230, 138
        columns = 4
        rows = 6
        for page, start in enumerate(range(0, len(cards), page_size), 1):
            subset = cards[start:start + page_size]
            image = QPixmap(columns * cell_w, rows * cell_h)
            image.fill(QColor("#F3F4F6"))
            painter = QPainter(image)
            try:
                for index, card in enumerate(subset):
                    preview = card.findChild(QLabel, "component_preview")
                    if preview is not None and hasattr(preview, "ensure_loaded"):
                        preview.ensure_loaded()
                    x = (index % columns) * cell_w
                    y = (index // columns) * cell_h
                    painter.fillRect(x + 5, y + 5, cell_w - 10, cell_h - 10,
                                     QColor("#FFFFFF"))
                    painter.setPen(QColor("#CBD2D9"))
                    painter.drawRect(x + 5, y + 5, cell_w - 11, cell_h - 11)
                    if preview is not None and preview.pixmap() is not None:
                        pix = preview.pixmap().scaled(
                            190, 88, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        painter.drawPixmap(
                            x + (cell_w - pix.width()) // 2,
                            y + 12,
                            pix,
                        )
                    painter.setPen(QColor("#17212B"))
                    font = QFont(window.font())
                    font.setPointSizeF(9.5)
                    font.setBold(True)
                    painter.setFont(font)
                    painter.drawText(
                        x + 12, y + 105, cell_w - 24, 24,
                        Qt.AlignHCenter | Qt.AlignVCenter,
                        card.title,
                    )
            finally:
                painter.end()
            path = self.output / f"palette-components-{page:02d}.png"
            if not image.save(str(path)):
                raise OSError(f"Could not save {path}")
            self.rows.append({
                "name": f"palette-components-{page:02d}",
                "file": path.name,
                "surface": "Component palette contact sheet",
                "size": [image.width(), image.height()],
                "findings": [],
                "components": [card.title for card in subset],
            })

    def write_manifest(self) -> None:
        payload = {
            "captures": self.rows,
            "failures": self.failures,
            "summary": {
                "capture_count": len(self.rows),
                "failure_count": len(self.failures),
                "finding_count": sum(len(row["findings"]) for row in self.rows),
            },
        }
        (self.output / "manifest.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        lines = [
            "# Graphics Designer visual review",
            "",
            f"Captured surfaces: {len(self.rows)}",
            f"Capture failures: {len(self.failures)}",
            "",
            "| Surface | Capture | Automated observations |",
            "| --- | --- | --- |",
        ]
        for row in self.rows:
            findings = "<br>".join(row["findings"]) or "None"
            lines.append(
                f"| {row['name']} | [{row['file']}]({row['file']}) | {findings} |"
            )
        if self.failures:
            lines.extend(["", "## Capture failures", ""])
            for failure in self.failures:
                lines.append(f"- **{failure['name']}**: {failure['error']}")
        (self.output / "REPORT.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def overview_sheet(self, name: str, captures: list[str]) -> Path:
        """Build a readable 3 x 3 review sheet from production captures."""
        cell_w, cell_h = 520, 330
        columns = 3
        rows = max(1, (len(captures) + columns - 1) // columns)
        sheet = QPixmap(columns * cell_w, rows * cell_h)
        sheet.fill(QColor("#D7DCE1"))
        painter = QPainter(sheet)
        try:
            for index, capture in enumerate(captures):
                path = self.output / f"{capture}.png"
                pixmap = QPixmap(str(path))
                if pixmap.isNull():
                    continue
                x = (index % columns) * cell_w
                y = (index // columns) * cell_h
                painter.fillRect(x + 5, y + 5, cell_w - 10, cell_h - 10,
                                 QColor("#FFFFFF"))
                thumb = pixmap.scaled(
                    cell_w - 20, cell_h - 44,
                    Qt.KeepAspectRatio, Qt.SmoothTransformation)
                painter.drawPixmap(x + (cell_w - thumb.width()) // 2,
                                   y + 10, thumb)
                painter.setPen(QColor("#17212B"))
                font = QFont()
                font.setPointSizeF(9.5)
                font.setBold(True)
                painter.setFont(font)
                painter.drawText(
                    x + 10, y + cell_h - 32, cell_w - 20, 24,
                    Qt.AlignCenter, capture.replace("-", " ").title())
        finally:
            painter.end()
        path = self.output / f"overview-{name}.png"
        if not sheet.save(str(path)):
            raise OSError(f"Could not save {path}")
        return path


def graph_fixture() -> tuple[dict[str, StrategyGraph], StrategyGraph]:
    graph = StrategyGraph(name="UNIT100")
    for type_name, tag in (("PID", "TIC101"), ("AI", "TI101"),
                           ("DEVCTL", "P101")):
        block = BlockRegistry().create(type_name, tag)
        if block is not None:
            apply_config = getattr(block, "_apply_config", None)
            if callable(apply_config):
                apply_config()
            graph.add_block(block)
    return {"UNIT100": graph}, graph


def seed_layouts(root: Path) -> LayoutStore:
    store = LayoutStore(root)
    display_set = DisplaySet("Console A")
    top = display_set.add_root("Overview")
    display_set.add_child(top, "Unit 100")
    display_set.add_non_hierarchical("Diagnostics")
    store.save_display_set(display_set)
    store.save_layout(default_layout("Wide"))
    return store


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    workspace = output / "workspace"
    display_root = workspace / "displays" / "pvm"
    display_root.mkdir(parents=True, exist_ok=True)
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope,
                      str(workspace / "settings"))

    app = QApplication.instance() or QApplication([])
    apply_application_font()
    review = Review(output, app)
    graphs, _graph = graph_fixture()
    store = DisplayStore(display_root)
    store.save_draft(PvmDisplay(
        "Overview",
        width=1600,
        height=900,
        description="Visual review display",
        items=[
            {"id": "title", "kind": "text", "x": 70, "y": 55,
             "w": 520, "h": 42, "text": "UNIT 100 · FEED CONTROL"},
            {"id": "vessel", "kind": "rect", "x": 470, "y": 260,
             "w": 210, "h": 280, "fill": "#DCE2E7", "line": "#465766"},
            {"id": "pipe", "kind": "line", "x": 250, "y": 400,
             "w": 570, "h": 0, "line": "#617384", "line_width": 3},
            {"id": "note", "kind": "text", "x": 710, "y": 510,
             "w": 360, "h": 58,
             "text": "Review area · live values use unresolved quality"},
        ],
    ))
    window = HmiStudioWindow(
        lambda: graphs,
        display_root,
        area_name="Graphics Designer visual review",
        configuration_root=workspace,
    )
    window.resize(1600, 900)
    window.show()
    flush(app, 8)
    studio = window.current()
    studio.enter_edit()
    placed = studio.place_block(
        "UNIT100/TIC101", "PID", 250, 245, role="dynamo_compact"
    )
    studio.place_block(
        "UNIT100/P101", "DEVCTL", 810, 360, role="dynamo_compact"
    )
    studio.mark_unsaved()
    flush(app)

    # Main chrome: every ribbon and every task-oriented left workspace.
    for index, tab_name in enumerate(window._RIBBON):
        window.ribbon_tabs.setCurrentIndex(index)
        flush(app)
        review.capture(window, f"main-ribbon-{tab_name.lower()}", size=(1600, 900))
    for key in ("components", "graphics", "library", "control",
                "selection", "layers", "split"):
        window.show_sidebar(key)
        flush(app)
        review.capture(window, f"main-sidebar-{key}", size=(1600, 900))

    window.ribbon_tabs.setCurrentIndex(list(window._RIBBON).index("Home"))
    window.show_sidebar("components")
    studio.canvas.scene().clearSelection()
    studio.pane.show_pvm(None)
    flush(app)
    review.capture(window, "inspector-display", size=(1600, 900))
    static = studio._static_items()[0]
    studio.selection.replace((static,))
    flush(app)
    review.capture(window, "inspector-static-design", size=(1600, 900))
    studio.pane.item_tabs.setCurrentIndex(1)
    flush(app)
    review.capture(window, "inspector-static-data", size=(1600, 900))
    studio.pane.item_tabs.setCurrentIndex(2)
    flush(app)
    review.capture(window, "inspector-static-behavior", size=(1600, 900))
    pvm_item = next(item for item in studio._items() if item.pvm.id == placed.id)
    studio.selection.replace((pvm_item,))
    studio.pane.show_pvm(pvm_item)
    flush(app)
    review.capture(window, "inspector-pvm", size=(1600, 900))

    window.problems_dock.show()
    flush(app)
    review.capture(window, "dock-problems", size=(1600, 900))
    window.problems_dock.hide()
    window.test_data_dock.show()
    flush(app)
    review.capture(window, "dock-test-data", size=(1600, 900))
    window.test_data_dock.hide()

    # Tool windows move the production widgets out of the main shell. Capture
    # them in that real floating state, then return the widgets to their owner.
    palette_dialog = window.detach_palette()
    review.capture(palette_dialog, "tool-component-palette", size=(360, 760))
    window.redock_palette()
    inspector_dialog = window.library.undock_inspector()
    review.capture(inspector_dialog, "tool-library-inspector", size=(620, 700))
    inspector_dialog.close()
    flush(app)

    alert_dialog = studio.alarm_banner.show_details()
    review.capture(alert_dialog, "dialog-display-alerts", size=(820, 460))
    alert_dialog.close()
    studio._choose_role("PID", studio.classes_for("PID"))
    chooser = studio._chooser
    review.capture(chooser, "dialog-pvm-class-picker", size=(680, 520))
    chooser.close()

    templates = window.library.templates
    hierarchy = {"Overview": (1, "")}
    review.attempt(
        "dialog-new-display-blank",
        lambda: NewDisplayDialog(
            templates, window, hierarchy=hierarchy, display_root=display_root),
        prepare=lambda dialog: dialog.name_edit.setText("Unit 100"),
    )
    review.attempt(
        "dialog-new-display-template",
        lambda: NewDisplayDialog(
            templates, window, prefer_template=True,
            hierarchy=hierarchy, display_root=display_root),
        prepare=lambda dialog: dialog.name_edit.setText("Unit 200"),
    )
    review.attempt(
        "dialog-display-properties",
        lambda: DisplayPropertiesDialog(studio, window),
    )
    review.attempt(
        "dialog-parameter-browser",
        lambda: ParameterBrowserDialog(lambda: graphs, parent=window),
        size=(980, 650),
    )
    review.attempt("dialog-command-search", lambda: CommandSearch(window))
    review.attempt("dialog-property-search", lambda: PropertySearch(studio.pane))
    review.attempt(
        "dialog-script-assistant",
        lambda: ScriptAssistantDialog(
            "const pv = Read('UNIT100/TIC101/PV');\n"
            "if (pv > 80) { SetFill('#F2C94C'); }\nreturn pv;",
            parent=window,
        ),
        size=(1100, 720),
    )

    for name, cls in (
        ("dialog-worksheet", WorksheetDialog),
        ("dialog-assemblies", AssemblyDialog),
        ("dialog-commissioning", CommissioningDialog),
        ("dialog-test-sequences", SequenceDialog),
        ("dialog-revision-review", RevisionReview),
        ("dialog-release-readiness", ReleaseDialog),
    ):
        review.attempt(name, lambda cls=cls: cls(studio))

    review.attempt(
        "dialog-help-center",
        lambda: GraphicsDesignerHelpCenter(window),
        size=(1180, 760),
    )
    review.attempt(
        "dialog-guided-tour",
        lambda: GraphicsDesignerTour(parent=window),
    )
    review.attempt(
        "dialog-about",
        lambda: GraphicsDesignerAboutDialog(
            window.area_name, "Project", display_root, window),
    )

    config_root = workspace / "_pvmcfg"
    designer = PvmConfigDesigner(config_root, pvm_class="HP_C_Valve")
    review.capture(designer, "configuration-designer", size=(1500, 880))
    designer_help = designer.show_help()
    review.capture(
        designer_help, "dialog-configuration-designer-help", size=(820, 650))
    designer_help.close()
    review.attempt(
        "dialog-configuration-preview",
        lambda: PreviewDialog(designer.config, designer),
        size=(1000, 700),
    )
    review.attempt(
        "dialog-configuration-impact",
        lambda: ImpactDialog(designer, saving=True),
        size=(980, 650),
    )

    catalog = BindingCatalog()
    catalog.add_graphs(graphs)
    catalog.add_component_properties([
        {"label": "Selected object · Visibility", "reference": "Object.Visible",
         "value_type": "Boolean", "group": "Selected object"},
        {"label": "Display · Level", "reference": "Display.Level",
         "value_type": "Numeric", "group": "Display"},
    ])
    target = BindingTarget(
        "Value", ValueType.NUMBER, object_name="Feed value", writable=True)
    binding = UnifiedBindingEditor(target, catalog, parent=window)
    for index, (kind, title) in enumerate(binding._PAGES):
        binding.kinds.setCurrentRow(index)
        flush(app)
        review.capture(binding, f"binding-{kind.value.lower().replace(' ', '-')}",
                       size=(1000, 680))
    binding.close()
    binding.deleteLater()

    review.attempt(
        "dialog-data-element",
        lambda: DataElementDialog({
            "kind": "table",
            "columns": [{"key": "tag", "title": "Tag"},
                        {"key": "value", "title": "Value"}],
            "rows": [{"tag": "TIC101", "value": {"path": "UNIT100/TIC101/PV",
                                                     "type": "numeric"}}],
        }, catalog, window),
        size=(1000, 720),
    )
    review.attempt(
        "dialog-user-entry",
        lambda: UserEntryDialog(
            {"kind": "combo_box", "path": "UNIT100/TIC101/MODE"},
            catalog, window),
        size=(900, 650),
    )
    review.attempt(
        "dialog-action-list",
        lambda: ActionListDialog([
            {"event": "click", "kind": "open_display", "target": "Overview"},
            {"event": "double_click", "kind": "acknowledge", "target": ""},
        ], catalog, window),
        size=(1050, 650),
    )

    layout_store = seed_layouts(display_root)
    review.attempt(
        "layout-display-set-editor",
        lambda: DisplaySetEditor(
            layout_store, "Console A",
            displays_provider=lambda: ("Overview", "Unit 100", "Diagnostics"),
            parent=window,
        ),
        size=(1200, 760),
    )
    review.attempt(
        "layout-screen-editor",
        lambda: LayoutEditor(layout_store, "Wide", parent=window),
        size=(1200, 760),
    )
    review.attempt(
        "layout-workstation-assignment",
        lambda: WorkstationAssignmentDialog(
            layout_store, layout_name="Wide", parent=window),
    )

    quick = window.open_quick_online()
    if quick is not None:
        review.capture(quick, "quick-online", size=(1500, 860))
        quick.close()
        quick.deleteLater()
    catalog_dialog = window.open_configuration_catalog()
    if catalog_dialog is not None:
        review.capture(catalog_dialog, "configuration-catalog", size=(1100, 720))
        catalog_dialog.close()

    # Release/history are built from the live disposable draft. In offscreen
    # review the publish operation targets only this harness workspace.
    studio.open_publish()
    review.capture(studio._publish_dialog, "dialog-publish", size=(760, 720))
    studio._publish_dialog.close()
    studio.open_history()
    review.capture(studio._history_dialog, "dialog-revision-history",
                   size=(820, 580))
    studio._history_dialog.close()

    review.palette_sheets(window)
    review.overview_sheet("ribbons", [
        f"main-ribbon-{name.lower()}" for name in window._RIBBON
    ])
    review.overview_sheet("sidebars", [
        f"main-sidebar-{name}" for name in
        ("components", "graphics", "library", "control", "selection",
         "layers", "split")
    ])
    review.overview_sheet("inspectors", [
        "inspector-display", "inspector-static-design",
        "inspector-static-data", "inspector-static-behavior",
        "inspector-pvm", "dock-problems", "dock-test-data",
    ])
    review.overview_sheet("authoring-dialogs", [
        "dialog-new-display-blank", "dialog-new-display-template",
        "dialog-display-properties", "dialog-parameter-browser",
        "dialog-command-search", "dialog-property-search",
        "dialog-script-assistant", "dialog-worksheet", "dialog-assemblies",
    ])
    review.overview_sheet("engineering-workflows", [
        "dialog-commissioning", "dialog-test-sequences",
        "dialog-revision-review", "dialog-release-readiness",
        "quick-online", "configuration-catalog",
    ])
    review.overview_sheet("configuration-and-help", [
        "dialog-help-center", "dialog-guided-tour", "dialog-about",
        "configuration-designer", "dialog-configuration-preview",
        "dialog-configuration-impact",
    ])
    review.overview_sheet("binding-and-elements", [
        "binding-direct_tag", "binding-indirect_tag",
        "binding-class_property", "binding-component_property",
        "binding-expression", "dialog-data-element", "dialog-user-entry",
        "dialog-action-list",
    ])
    review.overview_sheet("layouts", [
        "layout-display-set-editor", "layout-screen-editor",
        "layout-workstation-assignment",
    ])
    review.overview_sheet("secondary-tools", [
        "tool-component-palette", "tool-library-inspector",
        "dialog-display-alerts", "dialog-pvm-class-picker",
        "dialog-configuration-designer-help", "dialog-publish",
        "dialog-revision-history",
    ])
    designer.unsaved = False
    designer.close()
    designer.deleteLater()
    studio.unsaved = False
    window.close()
    window.deleteLater()
    flush(app)
    review.write_manifest()
    print(json.dumps({
        "output": str(output),
        "captures": len(review.rows),
        "failures": review.failures,
        "findings": sum(len(row["findings"]) for row in review.rows),
    }, indent=2))
    return 1 if review.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
