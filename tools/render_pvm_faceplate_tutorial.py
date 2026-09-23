#!/usr/bin/env python
"""Render the PVM/faceplate authoring tutorial from the real Qt widgets.

The screenshots in ``docs/images/pvm_faceplate_tutorial`` are not mockups.
They are deterministic captures of Graphics Designer, PVM Configuration
Designer, and the shared operator renderer over an isolated in-memory PID.
The isolated temporary display library keeps documentation generation from
touching a commissioned strategy or display revision.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QFont,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QTabBar,
)

import azeo_control_trainer.core.strategy.blocks  # noqa: E402,F401
from azeo_control_trainer.azeo_graphics_designer.configurator.designer import (  # noqa: E402
    PvmConfigDesigner,
)
from azeo_control_trainer.core.hmi.pvms.configurator.model import (  # noqa: E402
    INTERNAL, PvmProperty,
)
from azeo_control_trainer.azeo_graphics_designer.window import (  # noqa: E402
    HmiStudioWindow,
)
from azeo_control_trainer.core.hmi.pvms.user_faceplate import (  # noqa: E402
    UserFaceplateView,
)
from azeo_control_trainer.core.hmi.pvms.user_library import (  # noqa: E402
    UserPvmLibrary,
)
from azeo_control_trainer.core.hmi.theme.fonts import (  # noqa: E402
    apply_application_font,
    ensure_font_directory,
)
from azeo_control_trainer.core.strategy.model.block_registry import (  # noqa: E402
    BlockRegistry,
)
from azeo_control_trainer.core.strategy.model.strategy_graph import (  # noqa: E402
    StrategyGraph,
)
from azeo_control_trainer.core.strategy.model.terminal import Quality  # noqa: E402


OUT = ROOT / "docs" / "images" / "pvm_faceplate_tutorial"
FACEPLATE = "TutorialLoop"
PVM = f"{FACEPLATE}_PVM"


def _pump(app: QApplication, rounds: int = 4) -> None:
    for _ in range(rounds):
        app.processEvents()


def _expand(window: HmiStudioWindow, title: str) -> None:
    """Open one palette accordion and close the other large sections."""
    window.show_sidebar("components")
    selected = None
    for button, _content, name in window._palette_sections:
        button.setChecked(name == title)
        if name == title:
            selected = button
    _pump(QApplication.instance())
    if selected is not None:
        window.palette_box.ensureWidgetVisible(selected)


def _capture(widget, filename: str, callouts: list[tuple[float, float, str]]) -> None:
    """Capture a real widget and add restrained numbered tutorial notes."""
    _pump(QApplication.instance())
    if isinstance(widget, HmiStudioWindow):
        widget.current().fit_drawing()
        _pump(QApplication.instance())
    elif isinstance(widget, PvmConfigDesigner):
        widget._update_preview()
    image = widget.grab().toImage()
    manual_name = {
        "01_graphics_designer_anatomy.png": "graphics-designer.png",
        "11_class_master_canvas.png": "class-master.png",
        "12_public_internal_properties.png": "pvm-designer.png",
        "18_assembly_mapping.png": "assemblies.png",
    }.get(filename)
    if manual_name:
        manual = ROOT / "docs/images/user_manual" / manual_name
        if not image.save(str(manual)):
            raise RuntimeError(f"could not save {manual}")
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    font = QFont("DejaVu Sans", 10, QFont.DemiBold)
    painter.setFont(font)
    metrics = painter.fontMetrics()
    for number, (x_frac, y_frac, label) in enumerate(callouts, start=1):
        x = int(image.width() * x_frac)
        y = int(image.height() * y_frac)
        circle = QRectF(x, y, 28, 28)
        label_width = min(700, metrics.horizontalAdvance(label) + 22)
        box_x = x + 35
        if box_x + label_width > image.width() - 14:
            box_x = max(14, x - label_width - 8)
        box = QRectF(box_x, y - 1, label_width, 30)
        painter.setPen(QPen(QColor("#FFFFFF"), 1.0))
        painter.setBrush(QColor(15, 67, 110, 235))
        painter.drawEllipse(circle)
        painter.drawRoundedRect(box, 4, 4)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(circle, Qt.AlignCenter, str(number))
        painter.drawText(box.adjusted(9, 0, -6, 0),
                         Qt.AlignVCenter | Qt.AlignLeft, label)
    painter.end()
    path = OUT / filename
    if not image.save(str(path)):
        raise RuntimeError(f"could not save {path}")
    print(path.relative_to(ROOT))


def _build_graph() -> StrategyGraph:
    graph = StrategyGraph(name="TUTORIAL")
    pid = BlockRegistry().create("PID", "PID1")
    pid.config.params.update({
        "scale_lo": 0.0,
        "scale_hi": 200.0,
        "eng_units": "gpm",
        "HI_HI_LIM": 180.0,
        "HI_LIM": 160.0,
        "LO_LIM": 40.0,
        "LO_LO_LIM": 20.0,
    })
    pid._apply_config()
    for name, value in (("PV", 96.4), ("SP", 100.0), ("OUT", 48.5)):
        pid.outputs[name].value = value
        pid.outputs[name].status = Quality.GOOD
    graph.add_block(pid)
    return graph


def _build_library(display_root: Path) -> None:
    library = UserPvmLibrary(display_root)
    if library.create_faceplate_blueprint(FACEPLATE) is None:
        raise RuntimeError("could not create tutorial faceplate")
    if library.create_pvm_blueprint(PVM, FACEPLATE) is None:
        raise RuntimeError("could not create tutorial PVM")
    config_root = display_root / "_pvmcfg"
    faceplate_config = PvmConfigDesigner._faceplate_configuration(FACEPLATE)
    faceplate_config.groups[0].properties.append(PvmProperty(
        "PVDisplayText", "String", title="Resolved PV caption",
        description="Internal helper used by class-member bindings",
        default="PV", scope=INTERNAL))
    faceplate_config.save(config_root)
    pvm_config = PvmConfigDesigner._faceplate_configuration(PVM)
    pvm_config.groups[0].properties.append(PvmProperty(
        "PVDisplayText", "String", title="Resolved PV caption",
        description="Internal helper hidden from instance configuration",
        default="PV", scope=INTERNAL))
    pvm_config.save(config_root)

    # Extend the measured Loop_fp seed with elements authors commonly put on
    # a larger module faceplate.  Static and live cells deliberately share
    # one table to make the binding contract visible in the screenshot.
    library.reload()
    faceplate = library.entries[FACEPLATE]
    faceplate["items"].extend([
        {"kind": "text", "id": "fp_diag_title", "x": 220, "y": 66,
         "w": 304, "h": 22, "text": "Live values and diagnostics",
         "font_size": 10, "font_bold": True},
        {"kind": "table", "id": "fp_diag_table", "x": 220, "y": 92,
         "w": 304, "h": 226, "header_height": 25, "row_height": 27,
         "columns": [
             {"key": "parameter", "title": "Parameter", "width": 2},
             {"key": "value", "title": "Live value", "width": 2},
             {"key": "purpose", "title": "Purpose", "width": 3},
         ],
         "rows": [
             {"parameter": "PV", "value": {
                 "path": "Pvm.PVPath", "type": "numeric",
                 "decimals": 2}, "purpose": "Process value"},
             {"parameter": "SP", "value": {
                 "path": "Pvm.SPPath", "type": "numeric",
                 "decimals": 2}, "purpose": "Working setpoint"},
             {"parameter": "OUT", "value": {
                 "path": "Pvm.OUTPath", "type": "numeric",
                 "decimals": 1}, "purpose": "Controller output"},
             {"parameter": "Mode", "value": "AUTO",
              "purpose": "Operator state"},
        ]},
        {"kind": "text", "id": "fp_actions", "x": 220, "y": 336,
         "w": 304, "h": 18, "text": "Faceplate actions",
         "font_size": 9, "font_bold": True},
        {"kind": "icon_button", "id": "fp_detail", "x": 220, "y": 360,
         "w": 34, "h": 34, "icon": "module_detail",
         "tooltip": "Assign a serviced detail action before use"},
        {"kind": "icon_button", "id": "fp_primary", "x": 262, "y": 360,
         "w": 34, "h": 34, "icon": "primary_control",
         "tooltip": "Open primary control display",
         "actions": [{"event": "click", "kind": "open_display",
                       "target": "Loop Overview"}]},
        {"kind": "icon_button", "id": "fp_history", "x": 304, "y": 360,
         "w": 34, "h": 34, "icon": "process_history",
         "tooltip": "Assign a real History action before use"},
        {"kind": "icon_button", "id": "fp_alarm", "x": 346, "y": 360,
         "w": 34, "h": 34, "icon": "alarm",
         "tooltip": "Alarm state indication"},
        {"kind": "icon_button", "id": "fp_help", "x": 388, "y": 360,
         "w": 34, "h": 34, "icon": "alarm_help",
         "tooltip": "Alarm help"},
        {"kind": "text", "id": "fp_action_note", "x": 220, "y": 404,
         "w": 304, "h": 42,
         "text": "An icon becomes a button only after a real "
                 "Interaction action is assigned.",
         "font_size": 8},
    ])
    faceplate["w"] = 540
    faceplate["h"] = 468
    library.save()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ensure_font_directory()
    app = QApplication.instance() or QApplication(sys.argv[:1])
    from azeo_control_trainer.core.presentation.application_style import apply_application_style
    apply_application_style(app)
    apply_application_font()
    graph = _build_graph()

    def graphs():
        return {graph.name: graph}

    choices = {
        "ModuleName": "FIC-101/PID1",
        "Title": "FLOW CONTROL",
        "Description": "Feed flow controller",
        "PVPath": "TUTORIAL/PID1/PV",
        "SPPath": "TUTORIAL/PID1/SP",
        "OUTPath": "TUTORIAL/PID1/OUT",
        "ModulePath": "TUTORIAL",
        "EU0": "0",
        "EU100": "200",
        "UnitName": "Area 1",
    }

    with tempfile.TemporaryDirectory(prefix="azeo_fp_tutorial_") as folder:
        display_root = Path(folder)
        _build_library(display_root)

        window = HmiStudioWindow(
            graphs, display_root, area_name="Tutorial Area")
        window.resize(1600, 920)
        window.show()
        studio = window.current()
        studio.enter_edit()
        heading = studio.add_static(
            "text", 80, 55, w=430, h=36,
            text="Paired PVM + Faceplate tutorial")
        heading.data.update({"font_size": 18, "font_bold": True})
        heading.update()
        studio.place_user_pvm(PVM, 100, 135, choices=choices)
        note = studio.add_static(
            "text", 100, 215, w=600, h=44,
            text="Double-click the grouped PVM or use its mini-faceplate "
                 "button to open the full reusable faceplate.")
        note.data["font_size"] = 10
        note.update()
        studio.fit_drawing()
        _expand(window, "Special Symbols")
        _capture(window, "01_graphics_designer_anatomy.png", [
            (.26, .05, "Home: save, arrange, verify and publish"),
            (.01, .11, "Switch between Components, Displays and Library"),
            (.01, .34, "Searchable components with actual previews"),
            (.43, .43, "Canvas: place and compose elements"),
            (.80, .34, "Properties for the current selection"),
        ])

        ribbon_tabs = window.findChild(QTabBar, "ribbon_tabs")
        ribbon_tabs.setCurrentIndex(ribbon_tabs.count() - 1)
        _capture(window, "09_help_ribbon.png", [])
        ribbon_tabs.setCurrentIndex(1)

        window.show_sidebar("library")
        window.library.rebuild()
        window.library.select_user_class(FACEPLATE)

        def expand_library(item=None):
            tree = window.library.tree
            total = tree.topLevelItemCount() if item is None \
                else item.childCount()
            for index in range(total):
                child = tree.topLevelItem(index) if item is None \
                    else item.child(index)
                text = child.text(0)
                child.setExpanded(any(label in text for label in (
                    "Azeo System", "Library", "PVM Classes",
                    "Faceplate Classes", "Project · authored",
                    "My PVMs", "My Faceplates", "Special Symbols")))
                expand_library(child)

        expand_library()
        window.library.split.setSizes([510, 150])
        _capture(window, "07_engineering_library.png", [
            (.01, .20, "Filter by class, status or usage"),
            (.01, .30, "PVM, Faceplate and Detail classes are separate"),
            (.01, .48, "Each installed class has a recognizable icon"),
            (.01, .64, "Special Symbols and project SVGs are library items"),
            (.01, .78, "Inspector shows pair, bindings and dependencies"),
        ])

        help_center = window.open_help("class_builder_workflow")
        help_center.resize(1100, 720)
        help_center.show()
        _capture(help_center, "08_graphics_designer_help.png", [
            (.02, .08, "Search every workflow, command and error topic"),
            (.02, .29, "Reference grouped by engineering task"),
            (.35, .20, "Procedures and cross-links stay inside Studio"),
            (.61, .82, "F1 routes from the active engineering pane"),
        ])
        help_center.show_topic("illustrated_tutorial")
        help_center.browser.find("Graphics Designer anatomy")
        _capture(help_center, "10_illustrated_help.png", [])
        help_center.close()
        window.show_sidebar("graphics")

        compact = window.edit_user_pvm_layout(PVM)
        compact.fit_drawing()
        compact.canvas.scene().clearSelection()
        compact_surface = next(
            item for item in compact._static_items()
            if item.data.get("id") == "pvm_surface")
        compact_surface.setSelected(True)
        compact._on_selection()
        _expand(window, "My PVMs")
        _capture(window, "02_compact_pvm_class.png", [
            (.17, .30, "Reusable classes appear under My PVMs"),
            (.45, .42, "Compact PVM class: grouped drawing elements"),
            (.80, .35, "Geometry, visibility and Interaction"),
            (.58, .61, "Mini-faceplate icon carries the open action"),
        ])

        full = window.edit_user_pvm_layout(FACEPLATE)
        full.fit_drawing()
        full.canvas.scene().clearSelection()
        full._on_selection()
        _capture(window, "11_class_master_canvas.png", [
            (.31, .18, "Class tab: normal Studio tools, class lifecycle"),
            (.39, .30, "Checkerboard is limited to the master page"),
            (.28, .87, "Dotted boundary is the instance coordinate system"),
            (.80, .30, "Class Inspector summarizes the public contract"),
            (.80, .66, "Edit Interface and Validate stay beside the master"),
        ])
        table = next(item for item in full._static_items()
                     if item.data.get("kind") == "table")
        full.canvas.scene().clearSelection()
        table.setSelected(True)
        full._on_selection()
        _expand(window, "Data")
        full.pane.item_tabs.setCurrentIndex(1)
        _capture(window, "03_faceplate_layout.png", [
            (.17, .30, "Data palette: links, alarms, charts and tables"),
            (.39, .35, "Loop faceplate body from the measured blueprint"),
            (.58, .37, "Selected table: static and live-bound cells"),
            (.80, .33, "Edit columns, rows and bindings as JSON"),
            (.56, .73, "Footer icons use explicit Interaction actions"),
        ])
        _capture(window, "14_class_member_bindings.png", [
            (.17, .28, "Data palette includes links, charts, alarms and tables"),
            (.39, .32, "PV/SP/OUT bind through reusable Pvm.* references"),
            (.58, .38, "Table cells mix static labels and live descriptors"),
            (.80, .34, "Selected member exposes data and Interaction settings"),
            (.39, .71, "Trend, Alarm List and User Entry share the class contract"),
        ])

        designer = PvmConfigDesigner(
            display_root / "_pvmcfg", pvm_class=FACEPLATE)
        designer.resize(1500, 860)
        designer.body_splitter.setSizes([300, 850, 330])
        designer._select_class(FACEPLATE)
        designer.selected = "SPPath"
        designer.selected_group = None
        designer.reload_tree()
        designer._show_form()
        designer.show()
        _capture(designer, "04_pvm_configuration_designer.png", [
            (.02, .95, "Select properties with mouse or arrow keys"),
            (.32, .95, "SP writes use the checked operator service"),
            (.80, .28, "Fit previews the complete class"),
        ])

        designer.selected = "PVDisplayText"
        designer.reload_tree()
        designer._show_form()
        _capture(designer, "12_public_internal_properties.png", [
            (.02, .95, "Internal properties are marked in the tree"),
            (.32, .95, "Internal values cannot be required or writable"),
        ])

        designer.selected = "ControlTag"
        designer.reload_tree()
        designer._show_form()
        _capture(designer, "13_typed_drop_target.png", [
            (.02, .95, "One primary drop target per class"),
            (.32, .95, "Required target accepts the listed block types"),
        ])
        designer.close()

        overview = window.open_display("Overview")
        overview.fit_drawing()
        group = next(item.data.get("group") for item in overview._static_items()
                     if item.data.get("user_pvm") == PVM)
        overview.canvas.scene().clearSelection()
        overview_surface = next(
            item for item in overview._static_items()
            if item.data.get("group") == group
            and item.data.get("id", "").startswith("itm_")
            and item.data.get("kind") == "rect"
            and item.data.get("w") == 132)
        overview_surface.setSelected(True)
        overview._on_selection()
        _expand(window, "My PVMs")
        _capture(window, "05_place_configure_and_pair.png", [
            (.17, .32, "Click the paired PVM class to place it"),
            (.43, .43, "One grouped, linked instance"),
            (.80, .36, "Configure instance or pair from the shortcut menu"),
            (.43, .68, "Save, Validate, Test, then Publish"),
        ])

        overview.visual_choices.pop("PID", None)
        overview._choose_block_visual(
            "PID", overview.classes_for("PID"),
            overview.authored_classes_for("PID"))
        chooser = overview._chooser
        chooser.resize(720, 430)
        chooser.show()
        _capture(chooser, "15_control_block_drop.png", [
            (.04, .07, "Only visuals compatible with the dragged PID appear"),
            (.04, .23, "Native and authored classes are peers"),
            (.04, .74, "Remember the choice for this block type/display"),
            (.70, .86, "Cancel creates no placeholder"),
        ])
        chooser.close()

        built = overview._build_user_pvm_choices_dialog(overview_surface)
        if built is None:
            raise RuntimeError("tutorial PVM has no public instance editor")
        instance_dialog, _editors, _current, properties = built
        if any(prop.scope == INTERNAL for prop in properties):
            raise RuntimeError("internal property leaked into instance editor")
        instance_dialog.resize(720, 430)
        instance_dialog.show()
        _capture(instance_dialog, "16_instance_configuration.png", [
            (.03, .08, "Only public class parameters appear"),
            (.03, .18, "The typed Control Tag is required"),
            (.03, .42, "Drop-filled PV/SP/OUT paths remain reviewable"),
            (.03, .76, "Ranges, units and optional values are per instance"),
        ])
        instance_dialog.close()

        overview.save_draft()
        class_master = window.edit_user_pvm_layout(PVM)
        class_master.canvas.scene().clearSelection()
        class_master._on_selection()
        window._sync_chrome()
        window.show_sidebar("library")
        window.library.rebuild()
        window.library.select_user_class(PVM)
        _capture(window, "17_validation_and_usages.png", [
            (.01, .22, "Class State comes from structural validation"),
            (.01, .39, "Used count is the affected-display impact"),
            (.01, .50, "Inspector names pairing, references and usages"),
            (.80, .30, "Class Inspector keeps interface preflight visible"),
            (.53, .08, "Save class here; publish each affected display later"),
        ])
        window.show_sidebar("graphics")

        popup = UserFaceplateView(
            FACEPLATE, display_root, graphs, choices=choices)
        popup.resize(900, 760)
        popup.show()
        popup.refresh()
        _capture(popup, "06_test_runtime_faceplate.png", [
            (.02, .06, "Resolved class title and live PV/OUT"),
            (.02, .31, "PV scale, centered PV bar and writable SP"),
            (.63, .12, "Live table uses the same binding engine"),
            (.02, .82, "Trend, alarms and unit remain reusable elements"),
            (.63, .67, "Only icons with actions paint as buttons"),
        ])
        popup.close()

        from azeo_control_trainer.azeo_graphics_designer.engineering_tools import AssemblyDialog
        output = BlockRegistry().create("AO", "AO1")
        graph.add_block(output)
        assembly = AssemblyDialog(overview)
        assembly.source.setCurrentIndex(1)
        mapping = {"LOOP/PID": "TUTORIAL/PID1", "VALVE/AO": "TUTORIAL/AO1"}
        for row in range(assembly.mapping.rowCount()):
            assembly.mapping.cellWidget(row, 1).setCurrentText(
                mapping[assembly.mapping.item(row, 0).text()])
        assembly.preview()
        assembly.resize(1180, 840)
        assembly.show()
        _capture(assembly, "18_assembly_mapping.png", [])
        assembly.close()
        window.close()
        _pump(app)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
