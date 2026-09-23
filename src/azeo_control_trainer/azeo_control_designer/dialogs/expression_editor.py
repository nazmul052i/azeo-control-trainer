"""The CND/ACT expression editor — Azeo Control Designer's dialog, here.

Modelled point-for-point on the reference (user screenshot of
`FHR_DMC_ALM/CND1 Expression`): a toolbar band with Clipboard, Operators,
Function Library, Parameters and Editing groups; a line-numbered
expression pane; and a **Parse** button whose output pane says what is
wrong before the module is ever downloaded.

The parameter browsers are the heart of it, because in Azeo the
expression *is* the wiring:

- **Internal Parameter…** browses this module: its *module parameters*
  (input / output / internal read / internal write — inserted bare,
  `SP_HI_LIM`), every
  block's terminals (inserted as `param('PID1/OUT')`), and this block's
  own wired inputs (`IN1`…`IN8`, annotated with what feeds them).
- **External Parameter…** browses the tag store's field points and
  inserts `tag('LI-101.PV')`.
- **Module Parameters…** manages the module's own parameter table —
  Azeo's parameter list with its connection types.

Everything the toolbar inserts is something the parser accepts; a button
for syntax the language refuses would be the dead-Font-group mistake.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QPainter, QSyntaxHighlighter, QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QMenu, QMessageBox, QPlainTextEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from azeo_control_trainer.core.strategy.blocks.expression_check import (
    check_expression, known_functions,
)

_MONO = QFont("Consolas", 10)

#: Operator buttons, in Azeo's two-row arrangement — every one is a
#: spelling the evaluator actually accepts.
_OPERATORS = (
    ("+", "-", "*", "/", "(", ")", "==", "!="),
    ("<", "<=", ">", ">=", "and", "or", "not", "if/else"),
)

_INSERTS = {"if/else": " if  else "}

# Keep the UI vocabulary in Azeo's connection-type order.  The stored
# values are stable identifiers used by the compiler and serialized modules.
_PARAMETER_ACCESS = (
    ("input", "Input"),
    ("output", "Output"),
    ("internal_read", "Internal read"),
    ("internal_write", "Internal write"),
)
_PARAMETER_ACCESS_LABEL = dict(_PARAMETER_ACCESS)


class _LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self):                             # noqa: N802
        return QSize(self._editor.gutter_width(), 0)

    def paintEvent(self, event):                    # noqa: N802
        self._editor.paint_gutter(event)


class ExpressionEdit(QPlainTextEdit):
    """The expression pane: line numbers and a light highlighter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFont(_MONO)
        self.setTabStopDistance(28)
        self._gutter = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_margin)
        self.updateRequest.connect(self._update_gutter)
        self._update_margin()
        _Highlighter(self.document())

    def gutter_width(self) -> int:
        digits = max(2, len(str(self.blockCount())))
        return 12 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_margin(self, *_):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect, dy):
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(),
                                rect.height())

    def resizeEvent(self, event):                   # noqa: N802
        super().resizeEvent(event)
        content = self.contentsRect()
        self._gutter.setGeometry(QRect(content.left(), content.top(),
                                       self.gutter_width(),
                                       content.height()))

    def paint_gutter(self, event) -> None:
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), QColor("#F0F2F6"))
        painter.setPen(QColor("#8494A4"))
        painter.setFont(_MONO)
        block = self.firstVisibleBlock()
        top = self.blockBoundingGeometry(block).translated(
            self.contentOffset()).top()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible():
                painter.drawText(0, int(top), self._gutter.width() - 6,
                                 self.fontMetrics().height(),
                                 Qt.AlignRight,
                                 str(block.blockNumber() + 1))
            top += self.blockBoundingRect(block).height()
            block = block.next()
        painter.end()

    def insert_snippet(self, text: str) -> None:
        cursor = self.textCursor()
        cursor.insertText(text)
        self.setFocus()


class _Highlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        import re

        def fmt(color, bold=False):
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.Bold)
            return f

        self._rules = [
            (re.compile(r"\b(and|or|not|if|else|True|False)\b"),
             fmt("#7A5CB8", bold=True)),
            (re.compile(r"\b(IN[1-8]|b[1-8]|x|y|z|dt|time)\b"),
             fmt("#1D6FBF", bold=True)),
            (re.compile(r"\b(param|tag|write_param)\b"), fmt("#B26A00",
                                                             bold=True)),
            (re.compile(r"'[^']*'|\"[^\"]*\""), fmt("#2E7D32")),
            (re.compile(r"\b\d+(\.\d+)?\b"), fmt("#C62828")),
        ]

    def highlightBlock(self, text):                 # noqa: N802
        for pattern, style in self._rules:
            for match in pattern.finditer(text):
                self.setFormat(match.start(),
                               match.end() - match.start(), style)


def _group(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    outer = QVBoxLayout(frame)
    outer.setContentsMargins(6, 2, 6, 0)
    outer.setSpacing(1)
    body = QVBoxLayout()
    body.setSpacing(2)
    outer.addLayout(body, 1)
    caption = QLabel(title)
    caption.setAlignment(Qt.AlignCenter)
    caption.setStyleSheet("color:#7a8494; font-size:9pt;")
    outer.addWidget(caption)
    return frame, body


class ExpressionEditorDialog(QDialog):
    """`<MODULE>/<BLOCK> Expression` — edit, browse, parse, apply."""

    applied = Signal(str)

    def __init__(self, block, graph, store=None, parent=None):
        super().__init__(parent)
        self._block = block
        self._graph = graph
        self._store = store
        module = getattr(graph, "name", "") or "MODULE"
        self.setWindowTitle(f"{module}/{block.instance_name} Expression")
        self.resize(760, 480)

        root = QVBoxLayout(self)
        root.addWidget(self._build_toolbar())

        root.addWidget(QLabel("Expression:"))
        self.editor = ExpressionEdit()
        self.editor.setPlainText(
            str(block.config.params.get("EXPRESSION", "")))
        self.editor.moveCursor(QTextCursor.End)
        root.addWidget(self.editor, 3)

        root.addWidget(QLabel("Parser output:"))
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(_MONO)
        self.output.setMaximumHeight(88)
        root.addWidget(self.output, 1)

        buttons = QHBoxLayout()
        parse = QPushButton("Parse")
        parse.clicked.connect(self.parse)
        buttons.addWidget(parse)
        buttons.addStretch(1)
        box = QDialogButtonBox(QDialogButtonBox.Ok
                               | QDialogButtonBox.Cancel)
        box.accepted.connect(self._apply)
        box.rejected.connect(self.reject)
        buttons.addWidget(box)
        root.addLayout(buttons)

    # ------------------------------------------------------------- toolbar
    def _build_toolbar(self) -> QWidget:
        bar = QFrame()
        bar.setStyleSheet("QFrame { background:#EDF0F4; }"
                          "QPushButton { padding: 2px 7px; }")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(4, 3, 4, 1)
        lay.setSpacing(8)

        clip, clip_body = _group("Clipboard")
        row = QHBoxLayout()
        for label, slot in (("Paste", self.editor_paste),
                            ("Cut", lambda: self.editor.cut()),
                            ("Copy", lambda: self.editor.copy())):
            button = QPushButton(label)
            button.clicked.connect(slot)
            row.addWidget(button)
        clip_body.addLayout(row)
        lay.addWidget(clip)

        ops, ops_body = _group("Operators")
        grid = QGridLayout()
        grid.setSpacing(2)
        for r, opsrow in enumerate(_OPERATORS):
            for c, op in enumerate(opsrow):
                button = QPushButton(op)
                button.setFixedWidth(40)
                button.clicked.connect(
                    lambda _=False, o=op: self.editor.insert_snippet(
                        _INSERTS.get(o, f" {o} ")))
                grid.addWidget(button, r, c)
        ops_body.addLayout(grid)
        lay.addWidget(ops)

        lib, lib_body = _group("Function Library")
        functions = QPushButton("Functions ▾")
        menu = QMenu(functions)
        for name, doc in known_functions().items():
            action = menu.addAction(name)
            if doc:
                action.setToolTip(doc)
            action.triggered.connect(
                lambda _=False, n=name: self.editor.insert_snippet(
                    f"{n}("))
        menu.setToolTipsVisible(True)
        functions.setMenu(menu)
        lib_body.addWidget(functions)
        lay.addWidget(lib)

        params, params_body = _group("Parameters")
        internal = QPushButton("Internal\nParameter…")
        internal.clicked.connect(self.browse_internal)
        external = QPushButton("External\nParameter…")
        external.clicked.connect(self.browse_external)
        manage = QPushButton("Module\nParameters…")
        manage.clicked.connect(self.manage_parameters)
        row = QHBoxLayout()
        for b in (internal, external, manage):
            row.addWidget(b)
        params_body.addLayout(row)
        lay.addWidget(params)

        editing, editing_body = _group("Editing")
        row = QHBoxLayout()
        for label, slot in (("Find", self.find_text),
                            ("Replace", self.replace_text),
                            ("Go To", self.goto_line)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            row.addWidget(button)
        editing_body.addLayout(row)
        lay.addWidget(editing)
        lay.addStretch(1)
        return bar

    def editor_paste(self) -> None:
        self.editor.paste()

    # ------------------------------------------------------------ browsing
    def module_parameter_names(self) -> tuple:
        if self._graph is None:
            return ()
        return tuple(self._graph.module_parameters())

    def browse_internal(self) -> None:
        """This module, as Azeo shows it: parameters, then every block."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Internal Parameter")
        lay = QVBoxLayout(dialog)
        tree = QTreeWidget()
        tree.setHeaderLabels(["Name", "Detail", "Insert as"])
        lay.addWidget(tree)

        if self._graph is not None:
            params_root = QTreeWidgetItem(tree, ["Module Parameters",
                                                 "", ""])
            for name, spec in sorted(
                    self._graph.module_parameters().items()):
                access_id = str(spec.get("access", "internal_read"))
                access = _PARAMETER_ACCESS_LABEL.get(
                    access_id, access_id.replace("_", " ").title())
                QTreeWidgetItem(params_root,
                                [name, f"{access} = {spec.get('value')}",
                                 name])
            params_root.setExpanded(True)

            inputs_root = QTreeWidgetItem(tree, ["This block's inputs",
                                                 "", ""])
            wires = getattr(self._graph, "wires", {})
            for i in range(1, 9):
                source = ""
                for wire in wires.values():
                    if wire.dst_block_id == self._block.id \
                            and wire.dst_terminal == f"IN{i}":
                        src = self._graph.blocks.get(wire.src_block_id)
                        source = (f"wired from "
                                  f"{getattr(src, 'instance_name', '?')}."
                                  f"{wire.src_terminal}")
                        break
                QTreeWidgetItem(inputs_root,
                                [f"IN{i}", source or "unwired", f"IN{i}"])
            inputs_root.setExpanded(True)

            blocks_root = QTreeWidgetItem(tree, ["Blocks", "", ""])
            for other in sorted(self._graph.blocks.values(),
                                key=lambda b: b.instance_name):
                node = QTreeWidgetItem(blocks_root,
                                       [other.instance_name,
                                        other.block_type, ""])
                for terminal in list(other.outputs.values()) \
                        + list(other.inputs.values()):
                    reference = (f"param('{other.instance_name}/"
                                 f"{terminal.name}')")
                    QTreeWidgetItem(node, [terminal.name,
                                           terminal.direction.value,
                                           reference])
        tree.setColumnWidth(0, 190)
        tree.setColumnWidth(1, 220)

        def insert(item, _column):
            reference = item.text(2)
            if reference:
                self.editor.insert_snippet(reference)
                dialog.accept()

        tree.itemDoubleClicked.connect(insert)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel)

        def ok():
            item = tree.currentItem()
            if item is not None and item.text(2):
                self.editor.insert_snippet(item.text(2))
            dialog.accept()

        buttons.accepted.connect(ok)
        buttons.rejected.connect(dialog.reject)
        lay.addWidget(buttons)
        dialog.resize(560, 440)
        self._browse_dialog = dialog        # reachable for the smoke test
        from azeo_control_trainer.core.presentation.headless import is_headless

        if not is_headless():
            dialog.exec()

    def browse_external(self) -> None:
        """The tag store's field points — inserted as tag('KEY')."""
        tagdb = getattr(self._store, "tagdb", None)
        dialog = QDialog(self)
        dialog.setWindowTitle("External Parameter")
        lay = QVBoxLayout(dialog)
        table = QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(["Store tag", "Touched by"])
        if tagdb is not None:
            for tag_name, blocks in sorted(tagdb.field_tags().items()):
                row = table.rowCount()
                table.insertRow(row)
                table.setItem(row, 0, QTableWidgetItem(tag_name))
                table.setItem(row, 1, QTableWidgetItem(", ".join(blocks)))
        table.setColumnWidth(0, 180)
        table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(table)

        def insert_row(row, _column=0):
            item = table.item(row, 0)
            if item is not None:
                self.editor.insert_snippet(f"tag('{item.text()}')")
                dialog.accept()

        table.cellDoubleClicked.connect(insert_row)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel)
        buttons.accepted.connect(
            lambda: insert_row(max(0, table.currentRow())))
        buttons.rejected.connect(dialog.reject)
        lay.addWidget(buttons)
        dialog.resize(480, 420)
        self._browse_dialog = dialog
        from azeo_control_trainer.core.presentation.headless import is_headless

        if not is_headless():
            dialog.exec()

    def manage_parameters(self) -> None:
        """The module's parameter table — Azeo's parameter list."""
        if self._graph is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Module Parameters")
        lay = QVBoxLayout(dialog)
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["Parameter", "Default",
                                         "Connection type"])

        def reload():
            table.setRowCount(0)
            for name, spec in sorted(
                    self._graph.module_parameters().items()):
                row = table.rowCount()
                table.insertRow(row)
                table.setItem(row, 0, QTableWidgetItem(name))
                table.setItem(row, 1,
                              QTableWidgetItem(str(spec.get("value"))))
                access = AuthoringComboBox()
                for access_id, label in _PARAMETER_ACCESS:
                    access.addItem(label, access_id)
                selected = access.findData(
                    spec.get("access", "internal_read"))
                access.setCurrentIndex(max(0, selected))

                def change_access(index, n=name, widget=access):
                    current = self._graph.module_parameters().get(n)
                    if current is None:
                        return
                    self._graph.set_module_parameter(
                        n, current["value"], widget.itemData(index),
                        current.get("description", ""))

                access.currentIndexChanged.connect(change_access)
                table.setCellWidget(row, 2, access)

        reload()
        table.setColumnWidth(0, 160)
        table.setColumnWidth(2, 150)
        lay.addWidget(table)

        def commit_edit(item):
            name_item = table.item(item.row(), 0)
            if item.column() == 1 and name_item is not None:
                name = name_item.text()
                spec = self._graph.module_parameters().get(name)
                if spec is None:
                    return
                text = item.text()
                try:
                    value = float(text) if "." in text or text.lstrip(
                        "-").isdigit() else (
                        True if text == "True" else
                        False if text == "False" else text)
                except ValueError:
                    value = text
                spec["value"] = value

        table.itemChanged.connect(commit_edit)

        row = QHBoxLayout()
        add = QPushButton("Add…")

        def add_parameter():
            name, ok = QInputDialog.getText(dialog, "Add parameter",
                                            "Name (e.g. SP_HI_LIM):")
            if not ok or not name.strip():
                return
            try:
                self._graph.set_module_parameter(name.strip().upper(), 0.0)
            except ValueError as error:
                QMessageBox.warning(dialog, "Parameter", str(error))
                return
            reload()

        add.clicked.connect(add_parameter)
        remove = QPushButton("Remove")

        def remove_parameter():
            item = table.item(table.currentRow(), 0)
            if item is not None:
                self._graph.remove_module_parameter(item.text())
                reload()

        remove.clicked.connect(remove_parameter)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(dialog.accept)
        row.addWidget(close)
        lay.addLayout(row)
        dialog.resize(480, 380)
        self._manage_dialog = dialog
        from azeo_control_trainer.core.presentation.headless import is_headless

        if not is_headless():
            dialog.exec()

    # ------------------------------------------------------------- editing
    def find_text(self) -> None:
        needle, ok = QInputDialog.getText(self, "Find", "Find what:")
        if ok and needle and not self.editor.find(needle):
            cursor = self.editor.textCursor()
            cursor.movePosition(QTextCursor.Start)
            self.editor.setTextCursor(cursor)
            self.editor.find(needle)

    def replace_text(self) -> None:
        needle, ok = QInputDialog.getText(self, "Replace", "Find what:")
        if not ok or not needle:
            return
        replacement, ok = QInputDialog.getText(self, "Replace",
                                               "Replace with:")
        if not ok:
            return
        self.editor.setPlainText(
            self.editor.toPlainText().replace(needle, replacement))

    def goto_line(self) -> None:
        line, ok = QInputDialog.getInt(self, "Go To", "Line:", 1, 1,
                                       max(1, self.editor.blockCount()))
        if ok:
            block = self.editor.document().findBlockByNumber(line - 1)
            cursor = QTextCursor(block)
            self.editor.setTextCursor(cursor)
            self.editor.setFocus()

    # -------------------------------------------------------------- parse
    def parse(self) -> str:
        error = check_expression(self.editor.toPlainText(),
                                 extra_names=self.module_parameter_names())
        if error is None:
            self.output.setPlainText("No errors detected.")
            self.output.setStyleSheet("color: #2E7D32;")
        else:
            self.output.setPlainText(error)
            self.output.setStyleSheet("color: #C62828;")
        return error or ""

    def _apply(self) -> None:
        # Parse on OK, the way Azeo refuses to keep an expression its
        # parser rejects — a saved typo is a Bad verdict on scan 1.
        if self.parse():
            return
        self.applied.emit(self.editor.toPlainText().strip())
        self.accept()
