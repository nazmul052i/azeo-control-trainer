"""Assemblies, bulk binding previews and commissioning in the existing editor."""
from __future__ import annotations

import copy
import json
import logging
import math
from pathlib import Path

from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QCompleter, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from azeo_control_trainer.core.presentation.authoring_dialog import (
    add_authoring_dialog_header,
    mark_primary_action,
)
from azeo_control_trainer.core.presentation.dialog_layout import scrolling_body
from azeo_control_trainer.core.hmi.pvms.engineering import (
    applicable_cases, assembly_payload, check_case, control_requirements, document_digest,
    mapping_issues, remap_controls, save_assembly, starter_assemblies,
)
from azeo_control_trainer.core.hmi.pvms.instances import TemplateStore
from azeo_control_trainer.core.hmi.pvms.json_io import atomic_write_json
from .studio.preview import PreviewSource
from azeo_control_trainer.core.hmi.pvms.procedure_assemblies import (
    pa_assemblies, procedure_catalog, procedure_library, procedure_references,
    remap_procedures, revision_issues,
)

log = logging.getLogger("graphics.engineering_tools")


class EngineeringDialog(QDialog):
    def showEvent(self, event):  # noqa: N802
        from azeo_control_trainer.core.presentation.dialog_layout import fit_dialog_to_screen
        fit_dialog_to_screen(self)
        super().showEvent(event)

    def __init__(self, studio, title):
        super().__init__(studio)
        self.studio = studio
        self.setWindowTitle(f"{title} — {studio.display.name}")
        self.resize(1050, 620)
        self.root = scrolling_body(self)
        add_authoring_dialog_header(
            self,
            self.root,
            title,
            f"{studio.display.name} · governed display engineering workflow",
        )
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.root.addWidget(self.status)

    def button(self, row, label, fn, *, primary=False):
        button = QPushButton(label)
        button.clicked.connect(lambda _=False: self.run(fn))
        if primary:
            mark_primary_action(button)
        row.addWidget(button)
        return button

    def run(self, fn):
        try:
            return fn()
        except Exception as error:  # noqa: BLE001 - Qt event boundary
            log.exception("Engineering workflow failed")
            self.status.setText(str(error))
            return None

    def editable(self):
        self.studio.enter_edit()
        if not self.studio.store.owns_lock(self.studio.display.name):
            raise ValueError("The display must be owned for editing")


class AssemblyDialog(EngineeringDialog):
    DUPLICATE = "selection_copy"
    REMAP = "selection_remap"

    def __init__(self, studio):
        super().__init__(studio, "Assemblies and bulk tag mapping")
        self.resize(1180, 590)
        self.preview_view = None
        self._models = {}
        self._candidate = None
        self._loaded_source_index = -1
        self.status.setText("Choose equipment, assign its required control objects, then preview and place. No controller configuration is changed.")
        top = QHBoxLayout()
        self.source = AuthoringComboBox()
        self.source.setObjectName("assembly_source")
        self.source.addItem("Current display — remap in place", None)
        from .component_icons import pvm_icon
        from azeo_control_trainer.core.hmi.pvms.base import registry
        for name, document in starter_assemblies().items():
            self.source.addItem(name, document)
            pvm = document["pvms"][0]
            kind, _, role = pvm["class"].partition("/")
            cls = registry.get(kind, role, pvm.get("variant", ""))
            if cls:
                self.source.setItemIcon(self.source.count() - 1, pvm_icon(cls, 32))
        self.source.addItem("Selected equipment — duplicate and remap", self.DUPLICATE)
        self.source.addItem("Selected equipment — remap in place", self.REMAP)
        for name, template in TemplateStore(studio.store.root).entries.items():
            if name.startswith("Assembly · "):
                self.source.addItem(name, template.document)
        from azeo_control_trainer.core.hmi.pvms.publishing import PvmDisplay
        for name, items in pa_assemblies(studio.user_library(), studio._config_for_name).items():
            self.source.addItem("PA · " + name, PvmDisplay(name=name, items=items).to_dict())
        self.source.setIconSize(QSize(32, 32))
        self.source.setEditable(True)
        self.source.setInsertPolicy(AuthoringComboBox.NoInsert)
        self.source.completer().setCompletionMode(QCompleter.PopupCompletion)
        self.source.completer().setFilterMode(Qt.MatchContains)
        self.source.completer().setCaseSensitivity(Qt.CaseInsensitive)
        self.source.setToolTip("Type an equipment or saved assembly name, then choose a matching entry.")
        top.addWidget(self.source, 1)
        self.button(top, "Use selected equipment", self.use_selection)
        self.button(top, "Refresh references", self.reload)
        self.root.addLayout(top)
        save_row = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("New assembly name")
        save_row.addWidget(self.name, 1)
        self.button(save_row, "Save selection as assembly", self.save_selection)
        self.root.addLayout(save_row)
        split = QSplitter(Qt.Horizontal)
        self.mapping = QTableWidget(0, 4)
        self.mapping.setObjectName("assembly_mapping")
        self.mapping.setHorizontalHeaderLabels(("Required reference", "Target block / procedure revision", "Check", "Search"))
        header = self.mapping.horizontalHeader()
        for column in (0, 1, 2):
            header.setSectionResizeMode(column, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        self.mapping.setColumnWidth(3, 72)
        self.mapping.verticalHeader().hide()
        self.mapping.setEditTriggers(QTableWidget.NoEditTriggers)
        split.addWidget(self.mapping)
        preview = QWidget()
        self.preview_layout = QVBoxLayout(preview)
        self.preview_layout.setContentsMargins(0, 0, 0, 0)
        self.preview_caption = QLabel("Equipment preview")
        self.preview_caption.setWordWrap(True)
        self.preview_layout.addWidget(self.preview_caption)
        self.theme = AuthoringComboBox()
        from azeo_control_trainer.core.hmi.theme.service import ThemeService, THEME_LABELS
        self._themes = ThemeService(parent=self)
        for theme in self._themes.names():
            self.theme.addItem(THEME_LABELS.get(theme, theme), theme)
        self.preview_layout.addWidget(self.theme)
        split.addWidget(preview)
        split.setSizes([750, 380])
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 1)
        self.root.addWidget(split, 1)
        position = QHBoxLayout()
        position.addWidget(QLabel("Place top-left at"))
        self.position_x, self.position_y = QDoubleSpinBox(), QDoubleSpinBox()
        for caption, spin in (("X", self.position_x), ("Y", self.position_y)):
            spin.setRange(-100000, 100000)
            spin.setDecimals(1)
            spin.setPrefix(caption + " ")
            position.addWidget(spin)
        center = studio.canvas.mapToScene(studio.canvas.viewport().rect().center())
        self.position_x.setValue(max(0, center.x()))
        self.position_y.setValue(max(0, center.y()))
        position.addStretch(1)
        self.root.addLayout(position)
        bottom = QHBoxLayout()
        self.button(bottom, "Reload source", self.reload)
        self.button(bottom, "Preview mapped bindings", self.preview)
        self.apply_button = self.button(
            bottom, "Apply to canvas", self.apply, primary=True)
        self.apply_button.setEnabled(False)
        self.button(bottom, "Help", self.help)
        self.root.addLayout(bottom)
        self.source.currentIndexChanged.connect(lambda *_: self.run(self.reload))
        self.source.editTextChanged.connect(self.invalidate)
        self.theme.currentIndexChanged.connect(lambda *_: self.run(self.retheme))
        self.reload()

    def help(self):
        host = self.studio.window()
        if hasattr(host, "open_help"):
            host.open_help("engineering_training")

    def use_selection(self):
        index = self.source.findData(self.DUPLICATE)
        if self.source.currentIndex() == index:
            self.reload()
        else:
            self.source.setCurrentIndex(index)

    def _selection_document(self):
        from azeo_control_trainer.core.hmi.pvms.publishing import PvmDisplay
        records = self.studio.copy_selection_payload()["records"]
        if not records:
            raise ValueError("Select equipment on the canvas, then Reload source.")
        return PvmDisplay(name="Selected equipment", level=self.studio.display.level,
                          pvms=[r["data"] for r in records if r["type"] == "pvm"],
                          items=[r["data"] for r in records if r["type"] != "pvm"],
                          stacking_order=[r["data"]["id"] for r in records]).to_dict()

    def _chosen_source(self):
        if self.source.currentText() != self.source.itemText(self.source.currentIndex()):
            raise ValueError("Choose an assembly from the search results.")
        return self.source.currentData()

    def reload(self):
        self.invalidate()
        self._loaded_source_index = -1
        selected = self._chosen_source()
        document = self._selection_document() if selected in (self.DUPLICATE, self.REMAP) \
            else selected or self.studio._document()
        self._document = copy.deepcopy(document)
        self._source_kind = selected if isinstance(selected, str) or selected is None else "template"
        self._requirements = control_requirements(self._document)
        self._procedure_refs = procedure_references(self._document, self.studio._config_for_name)
        self._procedure_library = procedure_library(self.studio.store.root)
        self._requirements.update({ref: {"PROCEDURE"} for ref in self._procedure_refs})
        roots = sorted(self._requirements)
        graphs = self.studio.graphs_provider()
        # Share each compatible model across rows; thousands of project tags
        # must not become a separate Qt item tree for every assembly reference.
        self.mapping.setRowCount(0)
        for model in self._models.values():
            model.deleteLater()
        self._models = {}
        choices = [(f"{module}/{b.instance_name}", b.block_type, str(getattr(graph, "description", "") or ""))
                   for module, graph in graphs.items() for b in graph.blocks.values()]
        choices.extend((f"{module}/PARAMETERS", "PARAMETERS", str(getattr(graph, "description", "") or ""))
                       for module, graph in graphs.items() if graph.module_parameters())
        procedures, self._procedure_errors = procedure_catalog(self._procedure_library) if self._procedure_refs else ({}, [])
        choices.extend((ref, "PROCEDURE", title) for ref, title in procedures.items())
        self.mapping.setRowCount(len(roots))
        for row, path in enumerate(roots):
            families = frozenset(self._requirements[path])
            if families not in self._models:
                model = QStandardItemModel(self)
                model.appendRow(QStandardItem(""))
                for target, family, description in sorted(choices):
                    if families == {family} or (not families and family != "PROCEDURE"):
                        item = QStandardItem(target)
                        item.setToolTip(f"{family} · {description}")
                        model.appendRow(item)
                self._models[families] = model
            item = QTableWidgetItem(path)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            item.setToolTip("Requires " + (", ".join(sorted(families)) or "a control object with the referenced parameters"))
            self.mapping.setItem(row, 0, item)
            box = AuthoringComboBox()
            box.setEditable(True)
            box.setInsertPolicy(AuthoringComboBox.NoInsert)
            box.setModel(self._models[families])
            box.completer().setCompletionMode(QCompleter.PopupCompletion)
            box.completer().setFilterMode(Qt.MatchContains)
            box.completer().setCaseSensitivity(Qt.CaseInsensitive)
            box.setCurrentText(path if self._source_kind != "template" else "")
            box.lineEdit().setPlaceholderText("Choose " + (" / ".join(sorted(families)) or "control object"))
            box.currentTextChanged.connect(self.invalidate)
            self.mapping.setCellWidget(row, 1, box)
            self.mapping.setItem(row, 2, QTableWidgetItem("Not checked"))
            browse = QPushButton("Browse…")
            browse.clicked.connect(lambda _=False, r=row: self.run(lambda: self.browse(r)))
            self.mapping.setCellWidget(row, 3, browse)
            self.mapping.setRowHeight(row, 42)
        inserting = self._source_kind in ("template", self.DUPLICATE)
        self.position_x.setEnabled(inserting)
        self.position_y.setEnabled(inserting)
        self.apply_button.setText("Place assembly" if inserting else "Apply mapping")
        self.render_preview(self._document, checked=False)
        self._loaded_source_index = self.source.currentIndex()
        self.status.setText(f"{len(roots)} required references. Choose each target, then Preview mapped bindings."
                            + (f" {len(self._procedure_errors)} unreadable procedure files; hover for details." if self._procedure_errors else ""))
        self.status.setToolTip("\n".join(self._procedure_errors))

    def browse(self, row):
        from .param_browser import ParameterBrowserDialog
        box = self.mapping.cellWidget(row, 1)
        families = self._requirements[self.mapping.item(row, 0).text()]
        if families == {"PROCEDURE"}:
            box.setFocus()
            box.showPopup()
            return
        answer = ParameterBrowserDialog.browse(self.studio.graphs_provider, box.currentText(), self,
                                              block_types=families or None)
        if answer:
            box.setCurrentText(answer[0])

    def invalidate(self):
        self._candidate = None
        self.apply_button.setEnabled(False)
        for row in range(self.mapping.rowCount()):
            item = self.mapping.item(row, 2)
            if item:
                item.setText("Not checked")
        if self.preview_view is not None:
            self.preview_caption.setText("Source layout · mappings not checked")

    def _mapping_values(self):
        return {self.mapping.item(row, 0).text(): self.mapping.cellWidget(row, 1).currentText().strip()
                for row in range(self.mapping.rowCount())}

    def preview(self):
        self.invalidate()
        self._chosen_source()
        if self._loaded_source_index != self.source.currentIndex():
            raise ValueError("Reload the selected source before previewing its mappings.")
        mapping = self._mapping_values()
        graphs = self.studio.graphs_provider()
        errors = mapping_issues(self._document, mapping, graphs)
        errors.update(revision_issues(self._document, mapping, self._procedure_library, self.studio._config_for_name))
        for row in range(self.mapping.rowCount()):
            error = errors.get(self.mapping.item(row, 0).text())
            self.mapping.item(row, 2).setText(error or "Compatible; resolved")
            self.mapping.item(row, 2).setToolTip(error or "All explicit parameters resolve on the selected control object.")
        candidate = self._mapped_document(mapping, graphs)
        self._revision_digests = self._procedure_digests(mapping)
        self.render_preview(candidate, checked=True)
        self._candidate = candidate
        self._canvas_digest = document_digest(self.studio._document())
        self.apply_button.setEnabled(True)
        self.status.setText(f"{len(mapping)} references checked. Faceplates and embedded trends follow these references. "
                            "Apply creates one undo step. Preview writes are blocked.")
        return self._candidate

    def _mapped_document(self, mapping, graphs):
        document = remap_controls(self._document, mapping, graphs)
        return remap_procedures(document, mapping, self._procedure_library, self.studio._config_for_name)

    def _procedure_digests(self, mapping):
        from azeo_control_trainer.core.procedures.model import load_definition
        return {ref: load_definition(self._procedure_library / mapping.get(ref, ref), self._procedure_library).digest
                for ref in self._procedure_refs}

    def apply(self):
        if self._candidate is None:
            raise ValueError("Preview the mappings before applying")
        if document_digest(self.studio._document()) != self._canvas_digest:
            self.invalidate()
            raise ValueError("The display changed. Reload and preview its mappings again")
        self._chosen_source()
        # A controller module can change while this modeless preview is open.
        # Revalidate immediately before insertion, rather than trusting its old green rows.
        try:
            candidate = self._mapped_document(self._mapping_values(), self.studio.graphs_provider())
            if self._procedure_digests(self._mapping_values()) != self._revision_digests:
                raise ValueError("A procedure revision changed. Preview the mappings again.")
        except Exception:
            self.invalidate()
            raise
        self.editable()
        if self._source_kind in (None, self.REMAP):
            if self._source_kind == self.REMAP:
                merged = self.studio._document()
                for family in ("pvms", "items"):
                    replacements = {item["id"]: item for item in candidate.get(family, [])}
                    merged[family] = [replacements.get(item["id"], item) for item in merged.get(family, [])]
                candidate = merged
            self.studio.checkpoint()
            self.studio._load_document(copy.deepcopy(candidate))
            self.studio.mark_unsaved()
        else:
            payload = assembly_payload(candidate)
            endpoints = [r["data"] for r in payload["records"] if r["type"] != "pipe"]
            if not endpoints:
                raise ValueError("The assembly has no equipment to place")
            left = min(float(item.get("x", 0)) for item in endpoints)
            top = min(float(item.get("y", 0)) for item in endpoints)
            self.studio.paste_payload(payload, dx=self.position_x.value() - left, dy=self.position_y.value() - top)
            self.position_x.setValue(self.position_x.value() + self._preview_width + 32)
        self.invalidate()
        self.status.setText("Applied as one undo step. Choose the next equipment references and preview to place another, or close and Verify the display.")

    def render_preview(self, document, *, checked):
        from azeo_control_trainer.core.hmi.pvms.rendering.viewer import PvmDisplayView
        from .quick_online import ReadOnlySource
        preview = copy.deepcopy(document)
        preview["level"] = self.studio.display.level
        view = PvmDisplayView(preview, self.studio.graphs_provider, config_root=self.studio.store.root,
                              theme=self.theme.currentData(), live=False, write_handler=lambda *_: False,
                              write_checker=lambda *_: False,
                              source=ReadOnlySource(self.studio.preview_source.source))
        view.setInteractive(False)
        view.setMinimumSize(240, 230)
        old = self.preview_view
        self.preview_view = view
        self.preview_layout.addWidget(view, 1)
        if old is not None:
            self.preview_layout.removeWidget(old)
            old.close()
            old.deleteLater()
        rect = view.scene().itemsBoundingRect()
        self._preview_width = rect.width()
        view.setSceneRect(rect.adjusted(-16, -16, 16, 16))
        self.fit_preview()
        state = "Mapped preview" if checked else "Source layout · references not checked"
        self.preview_caption.setText(f"{state}\n{rect.width():.0f} × {rect.height():.0f} drawing units · destination L{self.studio.display.level}")

    def fit_preview(self):
        if self.preview_view is not None and not self.preview_view.sceneRect().isEmpty():
            self.preview_view.fitInView(self.preview_view.sceneRect(), Qt.KeepAspectRatio)

    def retheme(self):
        if self.preview_view is not None:
            self.preview_view.apply_theme(self.theme.currentData())

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self.run(self.fit_preview)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self.run(self.fit_preview)

    def closeEvent(self, event):  # noqa: N802
        if self.preview_view is not None:
            self.preview_view.close()
        super().closeEvent(event)

    def save_selection(self):
        name = save_assembly(self.studio.store.root, self.name.text(), self.studio.copy_selection_payload())
        document = TemplateStore(self.studio.store.root).entries[name].document
        self.source.addItem(name, document)
        self.source.setCurrentIndex(self.source.count() - 1)
        self.status.setText(f"Saved {name}; member PVMs and internal connections are included.")


class CommissioningDialog(EngineeringDialog):
    def __init__(self, studio):
        super().__init__(studio, "Commissioning checklist")
        self.status.setText("Save expected states, check bindings, then preview each case and record visual evidence. TEST blocks process writes.")
        self._before_preview = None
        self.cases = copy.deepcopy(studio.display.commissioning)
        self.results = {i: (case["check_passed"], case.get("check_message", ""))
                        for i, case in enumerate(self.cases) if "check_passed" in case}
        self.result_digests = {i: case.get("check_digest", "") for i, case in enumerate(self.cases)}
        top = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setPlaceholderText("MODULE/BLOCK/PARAMETER, for example FIC-101/PID1/PV")
        top.addWidget(self.path, 1)
        self.button(top, "Use selected binding", self.use_selected)
        self.button(top, "Add applicable states", self.add_cases)
        self.button(top, "Add selected equipment", self.add_selection_cases)
        self.root.addLayout(top)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(("Case", "Parameter", "Automated check", "Visual review"))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.root.addWidget(self.table, 1)
        form = QFormLayout()
        self.case_path = QLineEdit()
        self.expected = QLineEdit()
        self.expected_field = AuthoringComboBox()
        expected_row = QHBoxLayout()
        expected_row.addWidget(self.expected_field)
        expected_row.addWidget(self.expected, 1)
        self.simulated = QLineEdit()
        self.simulated.setPlaceholderText("Number, true, false or null; blank keeps the live value")
        self.quality = AuthoringComboBox()
        self.quality.addItems(("Keep live quality", "GOOD", "BAD", "UNCERTAIN"))
        simulation_row = QHBoxLayout()
        simulation_row.addWidget(self.simulated, 1)
        simulation_row.addWidget(self.quality)
        self.visual = QLineEdit()
        self.evidence = QLineEdit()
        form.addRow("Case parameter", self.case_path)
        form.addRow("Simulated input", simulation_row)
        form.addRow("Expected value / state", expected_row)
        form.addRow("Expected appearance and behavior", self.visual)
        form.addRow("Observed evidence", self.evidence)
        self.root.addLayout(form)
        row = QHBoxLayout()
        for label, fn in (("Update case", self.update_case), ("Check all", self.check_all),
                          ("Preview case", self.preview_case), ("Record visual review", self.review),
                          ("Restore preview", self.restore_preview), ("Save checklist", self.save)):
            self.button(row, label, fn)
        self.root.addLayout(row)
        extra = QHBoxLayout()
        self.button(extra, "Next pending case", self.next_case)
        self.button(extra, "Record review and next", self.review_next)
        self.button(
            extra, "Save checklist and draft", self.save_draft, primary=True)
        self.button(extra, "Remove selected case", self.remove_case)
        self.root.addLayout(extra)
        self.table.itemSelectionChanged.connect(self.select)
        self.expected_field.currentTextChanged.connect(self.select_expected)
        self.reload()

    def reload(self):
        row = self.table.currentRow()
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.cases))
        digest = document_digest(self.studio._document())
        for i, case in enumerate(self.cases):
            result = self.results.get(i) if self.result_digests.get(i) == digest else None
            reviewed = case.get("reviewed") and case.get("review_digest") == digest
            texts = (case["name"], case["path"], ("Pass" if result[0] else "Failed") if result else "Not run",
                     "Reviewed" if reviewed else "Review required")
            for c, value in enumerate(texts):
                self.table.setItem(i, c, QTableWidgetItem(value))
        if self.cases:
            self.table.selectRow(max(0, min(row, len(self.cases) - 1)))
        self.table.blockSignals(False)
        self.select()

    def selected_case(self):
        index = self.table.currentRow()
        if not 0 <= index < len(self.cases):
            raise ValueError("Select a commissioning case")
        return self.cases[index]

    def select(self):
        if not 0 <= self.table.currentRow() < len(self.cases):
            return
        case = self.selected_case()
        self.case_path.setText(case["path"])
        self.expected_field.blockSignals(True)
        self.expected_field.clear()
        self.expected_field.addItems(case["expected"])
        self.expected_field.blockSignals(False)
        self.select_expected()
        self.simulated.setText(json.dumps(case["override"]["value"]) if "value" in case["override"] else "")
        self.quality.setCurrentText(case["override"].get("quality", "Keep live quality"))
        self.visual.setText(case.get("visual_expectation", ""))
        self.evidence.setText(case.get("review_note", ""))

    def select_expected(self):
        if 0 <= self.table.currentRow() < len(self.cases):
            self.expected.setText(str(self.selected_case()["expected"].get(self.expected_field.currentText(), "")))

    def use_selected(self):
        selected = self.studio.selection.snapshot().primary
        binding = getattr(selected, "binding", None)
        path = str(getattr(binding, "path", "") or "")
        if not path:
            path = str(getattr(getattr(selected, "pvm", None), "params", {}).get("path", ""))
            if path:
                path += "/PV"
        self.path.setText(path)

    def add_cases(self):
        if len(self.path.text().split("/")) < 3:
            raise ValueError("Select a complete parameter path")
        candidates = applicable_cases(self.path.text().strip(), self.studio.preview_source.source)
        self._add_unique(candidates)
        self.reload()

    def _add_unique(self, candidates):
        existing = {(c["name"], c["path"]) for c in self.cases}
        for case in candidates:
            if (case["name"], case["path"]) not in existing:
                self.cases.append(case)
                existing.add((case["name"], case["path"]))

    def add_selection_cases(self):
        from azeo_control_trainer.core.hmi.binding.result import UNRESOLVED
        from azeo_control_trainer.core.hmi.pvms.elements import element_paths
        payload = self.studio.copy_selection_payload()
        source = self.studio.preview_source.source
        paths = set()
        for record in payload.get("records", []):
            data = record["data"]
            if record["type"] == "pvm":
                for root in data.get("params", {}).values():
                    for field in ("PV", "OUT", "STATE"):
                        path = str(root) + "/" + field
                        if source.read(path) is not UNRESOLVED:
                            paths.add(path)
                            break
            else:
                paths.update(element_paths(data))
        if not paths:
            raise ValueError("Select equipment with resolved live parameters, or enter a parameter path")
        for path in sorted(paths):
            self._add_unique(applicable_cases(path, source))
        self.reload()
        self.status.setText(f"Added applicable states for {len(paths)} parameters. Existing cases were retained.")

    def next_case(self):
        self.restore_preview()
        digest = document_digest(self.studio._document())
        count = len(self.cases)
        for offset in range(1, count + 1):
            index = (self.table.currentRow() + offset) % count
            case = self.cases[index]
            if (not case.get("reviewed") or case.get("review_digest") != digest
                    or self.result_digests.get(index) != digest or not self.results.get(index, (False,))[0]):
                self.table.selectRow(index)
                return index
        self.status.setText("All cases checked and visually reviewed for this draft. Save the checklist and draft.")
        return None

    def review_next(self):
        self.review()
        return self.next_case()

    def save_draft(self):
        self.save()
        if not self.studio.save_draft():
            raise ValueError("Checklist is in memory; saving the draft failed. Resolve the save error and retry.")
        self.status.setText("Checklist and display draft saved. Open Review → Release readiness for publication.")

    def update_case(self):
        case = self.selected_case()
        key = self.expected_field.currentText()
        raw = self.expected.text().strip()
        if key in {"alarm_active", "unresolved"} or isinstance(case["expected"][key], bool):
            if raw.lower() not in {"true", "false"}:
                raise ValueError("This expected state must be True or False")
            value = raw.lower() == "true"
        elif key == "value":
            value = float(raw)
            if not math.isfinite(value):
                raise ValueError("The expected value must be finite")
        else:
            value = raw.upper()
        override = copy.deepcopy(case["override"])
        raw_input = self.simulated.text().strip()
        if raw_input:
            input_value = json.loads(raw_input)
            if not isinstance(input_value, (int, float, bool, type(None))) or (
                    isinstance(input_value, (int, float)) and not math.isfinite(input_value)):
                raise ValueError("Use a finite number, true, false or null for the simulated input")
            override["value"] = input_value
        else:
            override.pop("value", None)
        if self.quality.currentIndex():
            override["quality"] = self.quality.currentText()
        else:
            override.pop("quality", None)
        self.restore_preview()
        case.update(path=self.case_path.text().strip(), expected={**case["expected"], key: value}, override=override,
                    visual_expectation=self.visual.text().strip(), reviewed=False)
        for field in ("check_passed", "check_digest", "check_message", "review_digest"):
            case.pop(field, None)
        self.results.pop(self.table.currentRow(), None)
        self.result_digests.pop(self.table.currentRow(), None)
        self.reload()

    def remove_case(self):
        index = self.table.currentRow()
        self.selected_case()
        self.restore_preview()
        del self.cases[index]
        self.results.clear()
        self.result_digests.clear()
        self.reload()

    def check_all(self):
        digest = document_digest(self.studio._document())
        for index, case in enumerate(self.cases):
            source = PreviewSource(self.studio.preview_source.source)
            source.set_override(case["path"], **case["override"])
            source.enabled = True
            self.results[index] = check_case(source, case)
            self.result_digests[index] = digest
        self.reload()
        failures = [f"{self.cases[i]['name']}: {message}" for i, (passed, message) in self.results.items() if not passed]
        self.status.setText("; ".join(failures) if failures else "All expected binding states observed. Visual reviews remain separate.")
        return self.results

    def preview_case(self):
        case = self.selected_case()
        source = PreviewSource(self.studio.preview_source.source)
        source.enabled = True
        source.set_override(case["path"], **case["override"])
        passed, message = check_case(source, case)
        if not passed:
            raise ValueError(message)
        if self._before_preview is None:
            self._before_preview = (self.studio.mode, copy.deepcopy(self.studio.preview_source.overrides))
        self.studio.preview_source.clear()
        self.studio.preview_source.set_override(case["path"], **case["override"])
        self.studio.enter_test()
        self.results[self.table.currentRow()] = (passed, message)
        self.result_digests[self.table.currentRow()] = document_digest(self.studio._document())
        self._preview_index = self.table.currentRow()
        self.status.setText(f"Previewing {case['name']} on the canvas. Inspect the appearance, navigation and disabled writes.")

    def restore_preview(self):
        if self._before_preview is None:
            return
        mode, overrides = self._before_preview
        self.studio.preview_source.overrides = overrides
        if mode == "edit":
            self.studio.enter_edit()
        elif mode == "test":
            self.studio.enter_test()
        else:
            self.studio.enter_view()
        self._before_preview = None

    def review(self):
        case = self.selected_case()
        if self._before_preview is None or getattr(self, "_preview_index", -1) != self.table.currentRow():
            raise ValueError("Preview this case before recording a visual review")
        if self.result_digests.get(self.table.currentRow()) != document_digest(self.studio._document()):
            raise ValueError("The display changed during this preview. Preview the current draft again.")
        if not self.evidence.text().strip() or not self.visual.text().strip():
            raise ValueError("Describe the expected appearance and record observed evidence")
        case.update(reviewed=True, review_note=self.evidence.text().strip(),
                    visual_expectation=self.visual.text().strip(),
                    review_digest=document_digest(self.studio._document()))
        self.restore_preview()
        self.reload()

    def save(self):
        self.restore_preview()
        self.editable()
        for i, case in enumerate(self.cases):
            if i in self.results:
                case["check_passed"], case["check_message"] = self.results[i]
                # Saving after a canvas edit must not certify an earlier check.
                case["check_digest"] = self.result_digests[i]
        self.studio.checkpoint()
        self.studio.display.commissioning = copy.deepcopy(self.cases)
        self.studio.mark_unsaved()
        folder = Path(self.studio.store.root) / "_commissioning"
        atomic_write_json(folder / f"{document_digest(self.studio._document())}.json",
                          {"display": self.studio.display.name, "cases": self.cases,
                           "results": {str(k): list(v) for k, v in self.results.items()}})
        self.status.setText("Checklist added to the display draft and results saved. Save the display to retain the authored checklist.")

    def closeEvent(self, event):  # noqa: N802
        try:
            self.restore_preview()
        except Exception:  # noqa: BLE001
            log.exception("Could not restore commissioning preview")
        super().closeEvent(event)
