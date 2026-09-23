"""One creation surface for blank and template-based display drafts.

The dialog names the destination before the display exists: its level, the
parent it will sit under in the Displays tree and the ISA navigation, and the
draft file the store will write. A display made from a template inherits the
template's level; a blank display takes the level chosen here. An empty
parent is allowed and warned about, because a display without a parent sits
at the top of the Displays folder and takes no place in the L1-L4 navigation
until Properties gives it one.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from azeo_control_trainer.core.presentation.authoring_controls import (
    AuthoringComboBox,
    name_form_fields,
)
from azeo_control_trainer.core.presentation.authoring_dialog import (
    add_authoring_dialog_header,
    style_dialog_buttons,
)

if TYPE_CHECKING:  # pragma: no cover
    from azeo_control_trainer.core.hmi.pvms.instances import TemplateStore

LEVEL_TITLES = {1: "Plant overview", 2: "Unit operation", 3: "Equipment detail", 4: "Support"}
NO_PARENT = "(none: top of the Displays folder)"


class NewDisplayDialog(QDialog):
    """Collect a display name, its starting point and where it will live."""

    def __init__(
        self,
        templates: "TemplateStore",
        parent=None,
        *,
        prefer_template: bool = False,
        hierarchy: dict | None = None,
        display_root: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.templates = templates
        #: {name: (level, parent)} of the displays that exist today.
        self.hierarchy = dict(hierarchy or {})
        self.display_root = Path(display_root) if display_root else None
        self.setWindowTitle("Create Display")
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        introduction = (
            "Create an empty operator canvas or copy a display template. "
            "A template becomes an independent draft and remains unchanged."
        )
        add_authoring_dialog_header(
            self, root, "Create display", introduction)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setObjectName("newDisplayName")
        self.name_edit.setPlaceholderText("Display name")
        form.addRow("Name", self.name_edit)

        source = QWidget()
        source_layout = QVBoxLayout(source)
        source_layout.setContentsMargins(0, 0, 0, 0)
        self.blank_option = QRadioButton("Blank display · 1600 × 900")
        self.blank_option.setObjectName("newDisplayBlank")
        self.template_option = QRadioButton("From display template")
        self.template_option.setObjectName("newDisplayTemplate")
        source_layout.addWidget(self.blank_option)
        source_layout.addWidget(self.template_option)
        form.addRow("Start with", source)

        self.template_combo = AuthoringComboBox()
        self.template_combo.setObjectName("newDisplayTemplateList")
        self.template_combo.addItems(templates.names("display"))
        form.addRow("Template", self.template_combo)

        self.template_summary = QLabel()
        self.template_summary.setObjectName("newDisplayTemplateSummary")
        self.template_summary.setWordWrap(True)
        form.addRow("", self.template_summary)

        self.level_combo = AuthoringComboBox()
        self.level_combo.setObjectName("newDisplayLevel")
        for level, title in LEVEL_TITLES.items():
            self.level_combo.addItem(f"L{level} · {title}", level)
        form.addRow("Level", self.level_combo)

        self.parent_combo = AuthoringComboBox()
        self.parent_combo.setObjectName("newDisplayParent")
        form.addRow("Parent display", self.parent_combo)

        self.parent_warning = QLabel()
        self.parent_warning.setObjectName("newDisplayParentWarning")
        self.parent_warning.setWordWrap(True)
        self.parent_warning.setStyleSheet("color: #8A5A00;")
        form.addRow("", self.parent_warning)

        self.destination = QLabel()
        self.destination.setObjectName("newDisplayDestination")
        self.destination.setWordWrap(True)
        self.destination.setTextInteractionFlags(self.destination.textInteractionFlags()
                                                 | self.destination.textInteractionFlags().TextSelectableByMouse)
        form.addRow("Will be created as", self.destination)
        root.addLayout(form)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self.create_button = self.buttons.button(QDialogButtonBox.Ok)
        self.create_button.setText("Create")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        style_dialog_buttons(self.buttons, QDialogButtonBox.Ok)
        root.addWidget(self.buttons)

        has_templates = self.template_combo.count() > 0
        self.template_option.setEnabled(has_templates)
        if prefer_template and has_templates:
            self.template_option.setChecked(True)
        else:
            self.blank_option.setChecked(True)

        self.name_edit.textChanged.connect(self._sync_state)
        self.blank_option.toggled.connect(self._sync_state)
        self.template_option.toggled.connect(self._sync_state)
        self.template_combo.currentTextChanged.connect(self._sync_state)
        self.level_combo.currentIndexChanged.connect(self._sync_state)
        self.parent_combo.currentIndexChanged.connect(self._sync_destination)
        name_form_fields(self)
        self._sync_state()
        self.name_edit.setFocus()

    # ------------------------------------------------------------ results
    @property
    def display_name(self) -> str:
        return self.name_edit.text().strip()

    @property
    def template_name(self) -> str:
        if not self.template_option.isChecked():
            return ""
        return self.template_combo.currentText().strip()

    @property
    def display_level(self) -> int:
        template = self._template()
        if template is not None and template.document.get("level"):
            return int(template.document["level"])
        return int(self.level_combo.currentData() or 1)

    @property
    def parent_name(self) -> str:
        data = self.parent_combo.currentData()
        return str(data) if data else ""

    def parent_candidates(self, level: int) -> list[str]:
        """Displays one level above, the only ones that may host this one."""
        return sorted(name for name, (existing_level, _parent) in self.hierarchy.items()
                      if existing_level == level - 1)

    def destination_text(self) -> str:
        level = self.display_level
        name = self.display_name or "<name>"
        parent = self.parent_name
        where = f"Displays › {parent} › {name}" if parent else f"Displays › {name}"
        text = f"{where}  ·  L{level} {LEVEL_TITLES.get(level, '')}".rstrip()
        if self.display_root is not None:
            text += f"\n{self.display_root / (self.display_name or '<name>') / 'draft.json'}"
        return text

    # ------------------------------------------------------------ state
    def _template(self):
        if not self.template_option.isChecked():
            return None
        return self.templates.entries.get(self.template_combo.currentText())

    def _sync_state(self, *_args) -> None:
        from_template = self.template_option.isChecked()
        self.template_combo.setEnabled(from_template)
        self.template_summary.setVisible(from_template)
        self._update_template_summary()
        template = self._template()
        fixed_level = template is not None and bool(template.document.get("level"))
        if fixed_level:
            index = self.level_combo.findData(int(template.document["level"]))
            if index >= 0 and self.level_combo.currentIndex() != index:
                self.level_combo.setCurrentIndex(index)
        self.level_combo.setEnabled(not fixed_level)
        self._sync_parents()
        self.create_button.setEnabled(
            bool(self.display_name)
            and (not from_template or bool(self.template_name))
        )

    def _sync_parents(self) -> None:
        level = self.display_level
        chosen = self.parent_name
        self.parent_combo.blockSignals(True)
        self.parent_combo.clear()
        self.parent_combo.addItem(NO_PARENT, "")
        for name in self.parent_candidates(level):
            self.parent_combo.addItem(f"{name}  ·  L{level - 1}", name)
        index = self.parent_combo.findData(chosen)
        self.parent_combo.setCurrentIndex(index if index >= 0 else 0)
        self.parent_combo.blockSignals(False)
        self.parent_combo.setEnabled(level > 1)
        self._sync_destination()

    def _sync_destination(self, *_args) -> None:
        level = self.display_level
        if level > 1 and not self.parent_name:
            candidates = self.parent_candidates(level)
            self.parent_warning.setText(
                f"No parent: an L{level} display without an L{level - 1} parent sits at the top "
                "of the Displays folder and takes no place in the operator navigation until "
                "Properties gives it one."
                + ("" if candidates else f" No L{level - 1} display exists yet."))
            self.parent_warning.setVisible(True)
        else:
            self.parent_warning.clear()
            self.parent_warning.setVisible(False)
        self.destination.setText(self.destination_text())

    def _update_template_summary(self) -> None:
        template = self.templates.entries.get(self.template_combo.currentText())
        if template is None:
            self.template_summary.setText("No display template is available.")
            return
        document = template.document
        facts = [("Built-in · edited" if template.overridden else "Built-in")
                 if template.builtin else "Project"]
        if document.get("level"):
            facts.append(f"L{document['level']}")
        width, height = document.get("width"), document.get("height")
        if width and height:
            facts.append(f"{width} × {height}")
        description = str(document.get("description", "")).strip()
        summary = " · ".join(facts)
        if description:
            summary += f"\n{description}"
        self.template_summary.setText(summary)


__all__ = ["NewDisplayDialog", "LEVEL_TITLES", "NO_PARENT"]
