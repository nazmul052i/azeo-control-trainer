"""One typed binding workflow for every Graphics Designer property.

The existing property pane grew several small, unrelated dialogs: a control
path picker, a free-form ``Pvm.`` reference field and raw JSON for compound
bindings.  This module deliberately owns neither a scene nor a property pane;
it is the reusable contract between them.  Callers supply a target and a
catalog, the editor returns one validated :class:`BindingEditorResult`.

The model above the Qt dialog is intentionally usable on its own.  Publish
preflight and importers can therefore apply exactly the same type and syntax
rules without constructing a widget.
"""
from __future__ import annotations

from azeo_control_trainer.core.hmi.compatibility import (
    LEGACY_SCOPE_PREFIX, PVM_SCOPE_PREFIX, PVM_SCOPE_PREFIXES, canonical_scope_reference,
)

import json
import re
import string
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping

from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
from azeo_control_trainer.core.presentation.authoring_dialog import (
    add_authoring_dialog_header,
    style_dialog_buttons,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from azeo_control_trainer.core.hmi.binding import expression as binding_expression
from azeo_control_trainer.core.hmi.pvms.configurator.model import (
    INTERNAL,
    REFERENCE_TYPES,
    SELECTION_TYPE,
    PvmConfiguration,
)


class BindingKind(str, Enum):
    """The five source families presented by the unified editor."""

    DIRECT_TAG = "direct_tag"
    INDIRECT_TAG = "indirect_tag"
    CLASS_PROPERTY = "class_property"
    COMPONENT_PROPERTY = "component_property"
    EXPRESSION = "expression"


class ValueType(str, Enum):
    """Small common type system spanning control and graphics data."""

    ANY = "Any"
    BOOLEAN = "Boolean"
    NUMBER = "Number"
    STRING = "String"
    COLOR = "Color"
    FONT = "Font"
    IMAGE = "Image"
    REFERENCE = "Reference"


_NUMERIC_TYPES = {
    "NUMBER", "MEASUREMENT", "DEGREE ANGLE", "REAL", "LREAL", "FLOAT",
    "DOUBLE", "INT", "INTEGER", "DINT", "SINT", "USINT", "UINT",
    "UDINT", "LONG", "SHORT", "BYTE", "WORD", "DWORD",
}
_STRING_TYPES = {
    "STRING", "WSTRING", "CHAR", "LOCALIZABLE STRING",
    "MULTI-LANGUAGE STRING", "SELECTION",
}


def normalize_value_type(value: object) -> ValueType:
    """Translate a controller or PVM type name into the editor's types."""

    if isinstance(value, ValueType):
        return value
    text = str(getattr(value, "value", value) or "").strip().upper()
    if text in _NUMERIC_TYPES:
        return ValueType.NUMBER
    if text in _STRING_TYPES:
        return ValueType.STRING
    if text in {"BOOL", "BOOLEAN"}:
        return ValueType.BOOLEAN
    if text == "COLOR":
        return ValueType.COLOR
    if text == "FONT":
        return ValueType.FONT
    if text in {"IMAGE", "SVG", "BITMAP"}:
        return ValueType.IMAGE
    if text in {name.upper() for name in REFERENCE_TYPES}:
        return ValueType.REFERENCE
    return ValueType.ANY


def types_compatible(target: object, source: object) -> bool:
    """Whether a source can drive a target without a hidden conversion."""

    target_type = normalize_value_type(target)
    source_type = normalize_value_type(source)
    if ValueType.ANY in (target_type, source_type):
        return True
    if target_type is source_type:
        return True
    # Labels deliberately accept scalar values; formatting remains the
    # label's concern.  The opposite conversion is never implicit.
    return target_type is ValueType.STRING and source_type in {
        ValueType.NUMBER, ValueType.BOOLEAN, ValueType.REFERENCE,
    }


@dataclass(frozen=True)
class BindingTarget:
    """The visual property being configured."""

    name: str
    value_type: ValueType | str = ValueType.ANY
    object_name: str = "Selection"
    writable: bool = False

    @property
    def normalized_type(self) -> ValueType:
        return normalize_value_type(self.value_type)


@dataclass(frozen=True)
class BindingCandidate:
    """One browsable source in a :class:`BindingCatalog`."""

    reference: str
    label: str
    value_type: ValueType | str = ValueType.ANY
    kind: BindingKind = BindingKind.DIRECT_TAG
    group: str = ""
    description: str = ""
    writable: bool = False
    sample: object = None
    scope: str = ""

    @property
    def normalized_type(self) -> ValueType:
        return normalize_value_type(self.value_type)


@dataclass(frozen=True)
class ExpressionReference:
    """One named input to a display-only arithmetic expression."""

    name: str
    source: str
    value_type: ValueType | str = ValueType.NUMBER
    kind: BindingKind = BindingKind.DIRECT_TAG

    @property
    def normalized_type(self) -> ValueType:
        return normalize_value_type(self.value_type)


@dataclass(frozen=True)
class BindingIssue:
    severity: str
    message: str
    field: str = ""

    def __str__(self) -> str:
        return f"{self.field}: {self.message}" if self.field else self.message


@dataclass(frozen=True)
class BindingEditorResult:
    """A complete binding decision, including validation evidence."""

    target: BindingTarget
    kind: BindingKind
    source: str = ""
    source_type: ValueType | str = ValueType.ANY
    expression: str = ""
    references: tuple[ExpressionReference, ...] = ()
    bidirectional: bool = False
    fallback: object = None
    issues: tuple[BindingIssue, ...] = ()

    @property
    def valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def descriptor(self) -> dict:
        """Return the canonical, serializable property descriptor.

        Direct, indirect and PVM-property descriptors already match the
        runtime property grammar.  ``component_property`` and expression
        ``refs`` are retained explicitly so integration never has to parse a
        display string back into structure.
        """

        if self.kind in (BindingKind.DIRECT_TAG, BindingKind.INDIRECT_TAG):
            out = {"kind": "animation", "path": self.source,
                   "type": "value"}
            if self.kind is BindingKind.INDIRECT_TAG:
                out["indirect"] = True
        elif self.kind is BindingKind.CLASS_PROPERTY:
            out = {"kind": "pvm", "ref": self.source}
        elif self.kind is BindingKind.COMPONENT_PROPERTY:
            out = {"kind": "property", "ref": self.source}
            if self.bidirectional:
                out["bidirectional"] = True
        else:
            out = {
                "kind": "expression",
                "expr": self.expression,
                "refs": {ref.name: ref.source for ref in self.references},
            }
        if self.fallback is not None:
            out["default"] = self.fallback
        return out

    def summary(self) -> str:
        if self.kind is BindingKind.EXPRESSION:
            return f"{self.target.name} ← {self.expression}"
        return f"{self.target.name} ← {self.source}"


_ALIAS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_formatter = string.Formatter()


@dataclass
class BindingCatalog:
    """All sources visible to one editor invocation."""

    candidates: list[BindingCandidate] = field(default_factory=list)

    def add(self, candidate: BindingCandidate) -> None:
        if not candidate.reference:
            return
        key = (candidate.kind, candidate.reference)
        if any((one.kind, one.reference) == key for one in self.candidates):
            return
        self.candidates.append(candidate)

    def find(self, kind: BindingKind, reference: str) -> BindingCandidate | None:
        if kind is BindingKind.CLASS_PROPERTY:
            reference = canonical_scope_reference(reference)
        return next((one for one in self.candidates
                     if one.kind is kind and one.reference == reference), None)

    def by_kind(self, kind: BindingKind) -> tuple[BindingCandidate, ...]:
        return tuple(sorted(
            (one for one in self.candidates if one.kind is kind),
            key=lambda one: (one.group.casefold(), one.label.casefold())))

    def indirect_fields(self) -> set[str]:
        fields: set[str] = set()
        for candidate in self.by_kind(BindingKind.CLASS_PROPERTY):
            ref = candidate.reference.removeprefix(PVM_SCOPE_PREFIX).removeprefix(LEGACY_SCOPE_PREFIX)
            fields.add(ref)
        return fields

    def add_configuration(self, config: PvmConfiguration) -> None:
        """Expose public and internal class properties and subproperties."""

        for group in config.groups:
            for prop in group.properties:
                scope = "Internal" if prop.scope == INTERNAL else "Public"
                value_type = ValueType.STRING if prop.ptype == SELECTION_TYPE \
                    else normalize_value_type(prop.ptype)
                self.add(BindingCandidate(
                    f"Pvm.{prop.name}", prop.title or prop.name, value_type,
                    BindingKind.CLASS_PROPERTY, group.name,
                    prop.description, prop.direction != "read", scope=scope))
                for column in prop.columns:
                    self.add(BindingCandidate(
                        f"Pvm.{prop.name}.{column}",
                        f"{prop.title or prop.name} / {column}", ValueType.ANY,
                        BindingKind.CLASS_PROPERTY, group.name,
                        f"Selection subproperty {prop.name}.{column}",
                        scope=scope))

    def add_graphs(self, graphs: Mapping[str, object]) -> None:
        """Build the same control hierarchy used by Control Data."""

        for module, graph in sorted(graphs.items()):
            blocks = getattr(graph, "blocks", {})
            for block in sorted(blocks.values(),
                                key=lambda one: one.instance_name):
                base = f"{module}/{block.instance_name}"
                for label, terminals in (("Inputs", block.inputs),
                                         ("Outputs", block.outputs)):
                    for name, terminal in sorted(terminals.items()):
                        if getattr(terminal, "hidden", False):
                            continue
                        data_type = getattr(terminal, "data_type", "")
                        self.add(BindingCandidate(
                            f"{base}/{name}", name,
                            normalize_value_type(data_type),
                            BindingKind.DIRECT_TAG,
                            f"{module} / {block.instance_name} / {label}",
                            f"{block.block_type} {label.lower()} parameter",
                            sample=getattr(terminal, "value", None)))
                config = dict(getattr(block.config, "params", {}) or {})
                schema = getattr(block, "get_config_schema", lambda: {})()
                for name in sorted(set(config) | set(schema)):
                    value = config.get(name)
                    if value is None and name in schema:
                        definition = schema[name]
                        if isinstance(definition, (tuple, list)) \
                                and len(definition) > 1:
                            value = definition[1]
                    self.add(BindingCandidate(
                        f"{base}/CONFIG/{name}", name,
                        normalize_value_type(type(value).__name__),
                        BindingKind.DIRECT_TAG,
                        f"{module} / {block.instance_name} / Configuration",
                        "Configured function-block value", sample=value))

    def add_component_properties(self, values: Iterable[BindingCandidate | dict]) -> None:
        """Add sibling/group/display properties supplied by the canvas."""

        for value in values:
            if isinstance(value, BindingCandidate):
                candidate = value
            else:
                candidate = BindingCandidate(
                    reference=str(value.get("reference", "")),
                    label=str(value.get("label") or value.get("reference", "")),
                    value_type=value.get("value_type", ValueType.ANY),
                    kind=BindingKind.COMPONENT_PROPERTY,
                    group=str(value.get("group", "Graphics properties")),
                    description=str(value.get("description", "")),
                    writable=bool(value.get("writable", False)),
                    sample=value.get("sample"),
                    scope=str(value.get("scope", "")),
                )
            if candidate.kind is not BindingKind.COMPONENT_PROPERTY:
                candidate = BindingCandidate(
                    candidate.reference, candidate.label, candidate.value_type,
                    BindingKind.COMPONENT_PROPERTY, candidate.group,
                    candidate.description, candidate.writable,
                    candidate.sample, candidate.scope)
            self.add(candidate)


def _template_fields(template: str) -> tuple[set[str], str | None]:
    fields: set[str] = set()
    try:
        for _literal, field_name, format_spec, conversion in _formatter.parse(template):
            if field_name is None:
                continue
            if format_spec or conversion:
                return set(), "format specifiers and conversions are not supported"
            fields.add(field_name)
    except ValueError as exc:
        return set(), f"invalid template: {exc}"
    return fields, None


def validate_binding(
        target: BindingTarget, kind: BindingKind, *, source: str = "",
        source_type: object = ValueType.ANY, expression: str = "",
        references: Iterable[ExpressionReference] = (),
        catalog: BindingCatalog | None = None,
        bidirectional: bool = False) -> tuple[BindingIssue, ...]:
    """Validate one prospective result without constructing the dialog."""

    issues: list[BindingIssue] = []
    source = source.strip()
    refs = tuple(references)
    catalog = catalog or BindingCatalog()

    def error(message: str, field: str = "Source") -> None:
        issues.append(BindingIssue("error", message, field))

    if kind is BindingKind.EXPRESSION:
        if not expression.strip():
            error("enter an arithmetic expression", "Expression")
        aliases: set[str] = set()
        for ref in refs:
            ref_source = ref.source.strip()
            if not _ALIAS.match(ref.name):
                error(f"{ref.name!r} is not a valid reference name", "References")
            elif ref.name in aliases:
                error(f"reference {ref.name!r} is duplicated", "References")
            aliases.add(ref.name)
            if not ref_source:
                error(f"reference {ref.name!r} has no source", "References")
            candidate = catalog.find(ref.kind, ref_source)
            ref_type = candidate.normalized_type if candidate is not None \
                else ref.normalized_type
            if ref_source and candidate is None:
                if ref.kind in (BindingKind.CLASS_PROPERTY,
                                BindingKind.COMPONENT_PROPERTY):
                    error(f"reference {ref.name!r} is not present in this "
                          "authoring context", "References")
                elif ref.kind is BindingKind.DIRECT_TAG and not (
                        ref_source.startswith("ns=")
                        or len(ref_source.split("/")) >= 3):
                    error(f"reference {ref.name!r} must identify "
                          "MODULE/BLOCK/PARAMETER", "References")
                elif ref.kind is BindingKind.INDIRECT_TAG:
                    fields, problem = _template_fields(ref_source)
                    unknown = sorted(fields - catalog.indirect_fields())
                    if problem:
                        error(f"reference {ref.name!r}: {problem}",
                              "References")
                    elif not fields:
                        error(f"reference {ref.name!r} needs a placeholder",
                              "References")
                    elif unknown:
                        error(f"reference {ref.name!r} has unknown "
                              "placeholder(s): " + ", ".join(unknown),
                              "References")
            if ref_type not in (ValueType.NUMBER, ValueType.ANY):
                error(f"reference {ref.name!r} is {ref_type.value}; "
                      "display expressions accept numeric inputs", "References")
        if expression.strip():
            problem = binding_expression.check(expression.strip(), aliases)
            if problem:
                error(problem, "Expression")
        if not types_compatible(target.normalized_type, ValueType.NUMBER):
            error(f"a Number expression cannot drive {target.normalized_type.value}",
                  "Target")
        return tuple(issues)

    if not source:
        error("choose a binding source")
        return tuple(issues)

    candidate = catalog.find(kind, source)
    if candidate is not None:
        source_type = candidate.normalized_type
    elif kind in (BindingKind.CLASS_PROPERTY,
                  BindingKind.COMPONENT_PROPERTY):
        error("the selected property is not present in this authoring context")

    if kind is BindingKind.DIRECT_TAG:
        if "{" in source or "}" in source:
            error("use Indirect Tag for a path containing placeholders")
        elif not (source.startswith("ns=") or len(source.split("/")) >= 3):
            error("a control path must identify MODULE/BLOCK/PARAMETER")
    elif kind is BindingKind.INDIRECT_TAG:
        fields, problem = _template_fields(source)
        if problem:
            error(problem)
        elif not fields:
            error("an indirect path needs at least one {Property} placeholder")
        else:
            unknown = sorted(fields - catalog.indirect_fields())
            if unknown:
                error("unknown class placeholder(s): " + ", ".join(unknown))
    elif kind is BindingKind.CLASS_PROPERTY and not source.startswith(PVM_SCOPE_PREFIXES):
        error("class property references begin with 'Pvm.'")

    if not types_compatible(target.normalized_type, source_type):
        error(f"{normalize_value_type(source_type).value} cannot drive "
              f"{target.normalized_type.value}", "Type")

    if bidirectional:
        if kind is not BindingKind.COMPONENT_PROPERTY:
            error("bidirectional mode applies only to component properties",
                  "Options")
        elif not target.writable or candidate is None or not candidate.writable:
            error("both properties must be writable for a bidirectional binding",
                  "Options")
    return tuple(issues)


def make_binding_result(
        target: BindingTarget, kind: BindingKind, *, source: str = "",
        source_type: object = ValueType.ANY, expression: str = "",
        references: Iterable[ExpressionReference] = (),
        catalog: BindingCatalog | None = None, bidirectional: bool = False,
        fallback: object = None) -> BindingEditorResult:
    refs = tuple(references)
    issues = validate_binding(
        target, kind, source=source, source_type=source_type,
        expression=expression, references=refs, catalog=catalog,
        bidirectional=bidirectional)
    return BindingEditorResult(
        target, kind, source.strip(), normalize_value_type(source_type),
        expression.strip(), refs, bidirectional, fallback, issues)


_CANDIDATE_ROLE = Qt.UserRole + 1


class _SourceTree(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by tag, property, block or group")
        self.search.setAccessibleName("Filter binding sources")
        layout.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Source", "Type", "Current value"])
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setAccessibleName("Available binding sources")
        self.tree.header().setStretchLastSection(False)
        self.tree.header().resizeSection(0, 290)
        self.tree.header().resizeSection(1, 84)
        self.tree.header().resizeSection(2, 110)
        layout.addWidget(self.tree, 1)
        self.search.textChanged.connect(self._filter)
        self.tree.currentItemChanged.connect(lambda *_args: self.changed.emit())

    def set_candidates(self, candidates: Iterable[BindingCandidate]) -> None:
        self.tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        for candidate in candidates:
            group_name = candidate.group or "Other"
            group = groups.get(group_name)
            if group is None:
                group = QTreeWidgetItem([group_name, "", ""])
                group.setFlags(group.flags() & ~Qt.ItemIsSelectable)
                self.tree.addTopLevelItem(group)
                groups[group_name] = group
            row = QTreeWidgetItem([
                candidate.label,
                candidate.normalized_type.value,
                "" if candidate.sample is None else str(candidate.sample),
            ])
            row.setData(0, _CANDIDATE_ROLE, candidate)
            row.setToolTip(0, candidate.description or candidate.reference)
            group.addChild(row)
            group.setExpanded(True)
        self._filter(self.search.text())

    def candidate(self) -> BindingCandidate | None:
        item = self.tree.currentItem()
        value = item.data(0, _CANDIDATE_ROLE) if item else None
        return value if isinstance(value, BindingCandidate) else None

    def select_reference(self, reference: str) -> bool:
        iterator = self.tree.invisibleRootItem()
        for group_index in range(iterator.childCount()):
            group = iterator.child(group_index)
            for row_index in range(group.childCount()):
                row = group.child(row_index)
                candidate = row.data(0, _CANDIDATE_ROLE)
                if isinstance(candidate, BindingCandidate) \
                        and candidate.reference == reference:
                    self.tree.setCurrentItem(row)
                    self.tree.scrollToItem(row)
                    return True
        return False

    def _filter(self, text: str) -> None:
        query = text.strip().casefold()
        root = self.tree.invisibleRootItem()
        for group_index in range(root.childCount()):
            group = root.child(group_index)
            visible = False
            for row_index in range(group.childCount()):
                row = group.child(row_index)
                candidate = row.data(0, _CANDIDATE_ROLE)
                haystack = " ".join((
                    getattr(candidate, "label", ""),
                    getattr(candidate, "reference", ""),
                    getattr(candidate, "group", ""),
                    getattr(candidate, "description", ""),
                )).casefold()
                show = not query or query in haystack
                row.setHidden(not show)
                visible = visible or show
            group.setHidden(not visible)


class UnifiedBindingEditor(QDialog):
    """Professional source browser and typed binding result editor."""

    resultChanged = Signal(object)

    _PAGES = (
        (BindingKind.DIRECT_TAG, "Direct Tag"),
        (BindingKind.INDIRECT_TAG, "Indirect Tag"),
        (BindingKind.CLASS_PROPERTY, "Class Property"),
        (BindingKind.COMPONENT_PROPERTY, "Property"),
        (BindingKind.EXPRESSION, "Expression"),
    )

    def __init__(self, target: BindingTarget, catalog: BindingCatalog,
                 initial: BindingEditorResult | None = None, parent=None):
        super().__init__(parent)
        self.target = target
        self.catalog = catalog
        self._accepted_result: BindingEditorResult | None = None
        self.setWindowTitle(f"Binding · {target.object_name}.{target.name}")
        self.setMinimumSize(820, 560)
        self.setModal(True)

        outer = QVBoxLayout(self)
        add_authoring_dialog_header(
            self,
            outer,
            "Configure binding",
            f"{target.object_name}.{target.name}  ·  "
            f"{target.normalized_type.value}",
        )
        self.setAccessibleName(
            f"Binding target {target.object_name} {target.name}, "
            f"{target.normalized_type.value}")
        self.shared_catalog_button = QPushButton("Browse shared configuration…")
        self.shared_catalog_button.clicked.connect(self._browse_configuration)
        outer.addWidget(self.shared_catalog_button)

        splitter = QSplitter(Qt.Horizontal)
        self.kinds = QListWidget()
        self.kinds.setAccessibleName("Binding source type")
        self.kinds.setMaximumWidth(175)
        for kind, title in self._PAGES:
            row = QListWidgetItem(title)
            row.setData(Qt.UserRole, kind)
            self.kinds.addItem(row)
        splitter.addWidget(self.kinds)
        self.pages = QStackedWidget()
        splitter.addWidget(self.pages)
        splitter.setStretchFactor(1, 1)
        outer.addWidget(splitter, 1)

        self.direct_tree = _SourceTree()
        self.direct_tree.set_candidates(catalog.by_kind(BindingKind.DIRECT_TAG))
        self.pages.addWidget(self.direct_tree)

        self.indirect_page = QWidget()
        indirect_layout = QVBoxLayout(self.indirect_page)
        indirect_form = QFormLayout()
        self.indirect_path = QLineEdit()
        self.indirect_path.setPlaceholderText("{ControlTag}/PV")
        self.indirect_path.setAccessibleName("Indirect tag template")
        indirect_form.addRow("Path template", self.indirect_path)
        self.indirect_type = AuthoringComboBox()
        self.indirect_type.addItems([value.value for value in ValueType])
        self.indirect_type.setCurrentText(ValueType.ANY.value)
        indirect_form.addRow("Result type", self.indirect_type)
        indirect_layout.addLayout(indirect_form)
        fields = sorted(catalog.indirect_fields())
        self.indirect_help = QLabel(
            "Use a class property inside braces. Available fields: "
            + (", ".join(fields) if fields else "none"))
        self.indirect_help.setWordWrap(True)
        self.indirect_help.setAccessibleName("Available indirect tag fields")
        indirect_layout.addWidget(self.indirect_help)
        indirect_layout.addStretch(1)
        self.pages.addWidget(self.indirect_page)

        self.class_tree = _SourceTree()
        self.class_tree.set_candidates(
            catalog.by_kind(BindingKind.CLASS_PROPERTY))
        self.pages.addWidget(self.class_tree)

        self.property_page = QWidget()
        property_layout = QVBoxLayout(self.property_page)
        self.property_tree = _SourceTree()
        self.property_tree.set_candidates(
            catalog.by_kind(BindingKind.COMPONENT_PROPERTY))
        property_layout.addWidget(self.property_tree, 1)
        self.bidirectional = QCheckBox("Bidirectional")
        self.bidirectional.setToolTip(
            "Keep both writable properties synchronized")
        property_layout.addWidget(self.bidirectional)
        self.pages.addWidget(self.property_page)

        self.expression_page = QWidget()
        expression_layout = QVBoxLayout(self.expression_page)
        expression_layout.addWidget(QLabel(
            "Display arithmetic only: +, −, ×, ÷, abs, min, max, round"))
        self.expression_text = QPlainTextEdit()
        self.expression_text.setPlaceholderText("pv - sp")
        self.expression_text.setAccessibleName("Binding expression")
        self.expression_text.setMaximumHeight(92)
        expression_layout.addWidget(self.expression_text)
        expression_layout.addWidget(QLabel("Named references"))
        self.expression_refs = QTableWidget(0, 3)
        self.expression_refs.setHorizontalHeaderLabels(
            ["Name", "Source", "Type"])
        self.expression_refs.setAccessibleName("Expression references")
        self.expression_refs.horizontalHeader().setStretchLastSection(True)
        self.expression_refs.setSelectionBehavior(QAbstractItemView.SelectRows)
        expression_layout.addWidget(self.expression_refs, 1)
        ref_buttons = QHBoxLayout()
        add_ref = QPushButton("Add tag…")
        remove_ref = QPushButton("Remove")
        add_ref.clicked.connect(self._add_expression_ref)
        remove_ref.clicked.connect(self._remove_expression_ref)
        ref_buttons.addWidget(add_ref)
        ref_buttons.addWidget(remove_ref)
        ref_buttons.addStretch(1)
        expression_layout.addLayout(ref_buttons)
        self.pages.addWidget(self.expression_page)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        outer.addWidget(line)
        self.validation = QLabel()
        self.validation.setWordWrap(True)
        self.validation.setAccessibleName("Binding validation status")
        outer.addWidget(self.validation)
        self.preview = QLineEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("Binding summary")
        outer.addWidget(self.preview)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._accept_if_valid)
        self.buttons.rejected.connect(self.reject)
        style_dialog_buttons(self.buttons, QDialogButtonBox.Ok)
        outer.addWidget(self.buttons)

        self.kinds.currentRowChanged.connect(self._kind_changed)
        self.direct_tree.changed.connect(self._refresh)
        self.class_tree.changed.connect(self._refresh)
        self.property_tree.changed.connect(self._refresh)
        self.indirect_path.textChanged.connect(self._refresh)
        self.indirect_type.currentTextChanged.connect(self._refresh)
        self.bidirectional.toggled.connect(self._refresh)
        self.expression_text.textChanged.connect(self._refresh)
        self.expression_refs.itemChanged.connect(self._refresh)

        self.kinds.setCurrentRow(0)
        if initial is not None:
            self.set_result(initial)
        self._refresh()

    def binding_kind(self) -> BindingKind:
        row = self.kinds.currentItem()
        value = row.data(Qt.UserRole) if row else BindingKind.DIRECT_TAG
        return value if isinstance(value, BindingKind) \
            else BindingKind(str(value))

    def current_result(self) -> BindingEditorResult:
        kind = self.binding_kind()
        source = ""
        source_type: object = ValueType.ANY
        bidirectional = False
        if kind is BindingKind.DIRECT_TAG:
            candidate = self.direct_tree.candidate()
            if candidate:
                source, source_type = candidate.reference, candidate.value_type
        elif kind is BindingKind.INDIRECT_TAG:
            source = self.indirect_path.text()
            source_type = self.indirect_type.currentText()
        elif kind is BindingKind.CLASS_PROPERTY:
            candidate = self.class_tree.candidate()
            if candidate:
                source, source_type = candidate.reference, candidate.value_type
        elif kind is BindingKind.COMPONENT_PROPERTY:
            candidate = self.property_tree.candidate()
            if candidate:
                source, source_type = candidate.reference, candidate.value_type
            bidirectional = self.bidirectional.isChecked()
        return make_binding_result(
            self.target, kind, source=source, source_type=source_type,
            expression=self.expression_text.toPlainText(),
            references=self._expression_references(), catalog=self.catalog,
            bidirectional=bidirectional)

    def binding_result(self) -> BindingEditorResult | None:
        """The accepted binding, or ``None`` when the dialog was cancelled.

        This deliberately does not override ``QDialog.result()``: Qt callers
        still need its Accepted/Rejected integer after ``exec()``.
        """

        return self._accepted_result

    def set_result(self, result: BindingEditorResult) -> None:
        for index, (kind, _title) in enumerate(self._PAGES):
            if kind is result.kind:
                self.kinds.setCurrentRow(index)
                break
        if result.kind is BindingKind.DIRECT_TAG:
            self.direct_tree.select_reference(result.source)
        elif result.kind is BindingKind.INDIRECT_TAG:
            self.indirect_path.setText(result.source)
            self.indirect_type.setCurrentText(
                normalize_value_type(result.source_type).value)
        elif result.kind is BindingKind.CLASS_PROPERTY:
            self.class_tree.select_reference(result.source)
        elif result.kind is BindingKind.COMPONENT_PROPERTY:
            self.property_tree.select_reference(result.source)
            self.bidirectional.setChecked(result.bidirectional)
        elif result.kind is BindingKind.EXPRESSION:
            self.expression_text.setPlainText(result.expression)
            self._set_expression_references(result.references)
        self._refresh()

    def select_source(self, kind: BindingKind, reference: str) -> bool:
        """Programmatic equivalent of choosing a tree row (also testable)."""

        for index, (candidate_kind, _title) in enumerate(self._PAGES):
            if candidate_kind is kind:
                self.kinds.setCurrentRow(index)
                break
        trees = {
            BindingKind.DIRECT_TAG: self.direct_tree,
            BindingKind.CLASS_PROPERTY: self.class_tree,
            BindingKind.COMPONENT_PROPERTY: self.property_tree,
        }
        tree = trees.get(kind)
        selected = tree.select_reference(reference) if tree else False
        self._refresh()
        return selected

    def _kind_changed(self, index: int) -> None:
        self.pages.setCurrentIndex(max(0, index))
        self._refresh()

    def _expression_references(self) -> tuple[ExpressionReference, ...]:
        found = []
        for row in range(self.expression_refs.rowCount()):
            cells = [self.expression_refs.item(row, col)
                     for col in range(3)]
            name, source, value_type = [cell.text() if cell else ""
                                        for cell in cells]
            found.append(ExpressionReference(
                name.strip(), source.strip(), value_type or ValueType.ANY))
        return tuple(found)

    def _set_expression_references(
            self, references: Iterable[ExpressionReference]) -> None:
        self.expression_refs.blockSignals(True)
        self.expression_refs.setRowCount(0)
        for ref in references:
            row = self.expression_refs.rowCount()
            self.expression_refs.insertRow(row)
            for column, text in enumerate((
                    ref.name, ref.source, ref.normalized_type.value)):
                self.expression_refs.setItem(
                    row, column, QTableWidgetItem(text))
        self.expression_refs.blockSignals(False)

    def _browse_configuration(self) -> None:
        from azeo_control_trainer.core.presentation.configuration_catalog import (
            ConfigurationCatalogDialog, context_root,
        )
        dlg = ConfigurationCatalogDialog(context_root(self), self, pick="tag")
        self._configuration_picker = dlg
        def accept_tag():
            if dlg.selected_tag:
                self.use_configuration_tag(dlg.selected_tag)
        dlg.accepted.connect(accept_tag)
        dlg.show()

    def use_configuration_tag(self, tag: dict) -> None:
        reference = tag["path"]
        # The explicit snapshot choice must not inherit a same-named local entry's type.
        self.catalog.candidates[:] = [c for c in self.catalog.candidates
                                     if (c.kind, c.reference) != (BindingKind.DIRECT_TAG, reference)]
        self.catalog.add(BindingCandidate(
            reference, reference, normalize_value_type(tag.get("data_type", "")),
            BindingKind.DIRECT_TAG, "Shared configuration snapshot",
            tag.get("description", ""), bool(tag.get("writable")), sample=tag.get("value")))
        self.direct_tree.set_candidates(self.catalog.by_kind(BindingKind.DIRECT_TAG))
        self.select_source(BindingKind.DIRECT_TAG, reference)

    def _add_expression_ref(self) -> None:
        """Add the first unused numeric tag; integration may preselect one."""

        used = {ref.source for ref in self._expression_references()}
        candidate = next((one for one in self.catalog.by_kind(
            BindingKind.DIRECT_TAG)
            if one.reference not in used and one.normalized_type in (
                ValueType.NUMBER, ValueType.ANY)), None)
        if candidate is None:
            return
        names = {ref.name for ref in self._expression_references()}
        base = re.sub(r"[^A-Za-z0-9_]", "_", candidate.label).strip("_") \
            or "value"
        if base[0].isdigit():
            base = "v_" + base
        name, suffix = base, 2
        while name in names:
            name, suffix = f"{base}_{suffix}", suffix + 1
        row = self.expression_refs.rowCount()
        self.expression_refs.insertRow(row)
        for column, text in enumerate((
                name, candidate.reference, candidate.normalized_type.value)):
            self.expression_refs.setItem(row, column, QTableWidgetItem(text))
        self._refresh()

    def _remove_expression_ref(self) -> None:
        rows = sorted({index.row() for index in
                       self.expression_refs.selectionModel().selectedRows()},
                      reverse=True)
        for row in rows:
            self.expression_refs.removeRow(row)
        self._refresh()

    def _refresh(self, *_args) -> None:
        result = self.current_result()
        self.preview.setText(result.summary())
        ok = self.buttons.button(QDialogButtonBox.Ok)
        ok.setEnabled(result.valid)
        if result.valid:
            self.validation.setText(
                "<span style='color:#137333'><b>Ready</b> · "
                "source and target types are compatible</span>")
        else:
            messages = "<br>".join(str(issue) for issue in result.issues)
            self.validation.setText(
                "<span style='color:#b3261e'><b>Cannot apply</b> · "
                f"{messages}</span>")
        self.resultChanged.emit(result)

    def _accept_if_valid(self) -> None:
        result = self.current_result()
        if not result.valid:
            return
        self._accepted_result = result
        super().accept()


def result_json(result: BindingEditorResult) -> str:
    """Human-readable descriptor preview used by hosts and diagnostics."""

    return json.dumps(result.descriptor(), indent=2, sort_keys=True)


__all__ = [
    "BindingCandidate", "BindingCatalog", "BindingEditorResult",
    "BindingIssue", "BindingKind", "BindingTarget", "ExpressionReference",
    "UnifiedBindingEditor", "ValueType", "make_binding_result",
    "normalize_value_type", "result_json", "types_compatible",
    "validate_binding",
]
