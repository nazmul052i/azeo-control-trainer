"""Graphics Designer TypeScript editor and Script Assistant."""
from __future__ import annotations

from azeo_control_trainer.core.hmi.compatibility import PVM_SCOPE_NAMES

from PySide6.QtCore import Qt, QStringListModel
from PySide6.QtGui import (
    QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextCursor,
)
from PySide6.QtWidgets import (
    QCompleter, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QSplitter,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)
from azeo_control_trainer.core.presentation.authoring_dialog import (
    add_authoring_dialog_header,
    style_dialog_buttons,
)

from azeo_control_trainer.core.hmi.pvms.scripting import (
    RESTRICTED_NOTES, GraphicsScriptRuntime, ScriptContext, api_completions,
    api_declarations,
)
from azeo_control_trainer.core.presentation.brand import AUTHORING_BLUE
from azeo_control_trainer.core.presentation.authoring_style import AUTHORING_CONTROLS_QSS


SNIPPETS = (
    ("DLSYS.Read", 'const pv: number = DLSYS.Read("MODULE/BLOCK/PV");'),
    ("DLSYS.CondRead", 'const result = DLSYS.CondRead("MODULE/BLOCK/PV");'),
    ("DLSYS.Exists", 'if (DLSYS.Exists("MODULE/BLOCK/PV")) {\n  \n}'),
    ("DLSYS.CanWrite", 'DLSYS.CanWrite("MODULE/BLOCK/SP")'),
    ("DLSYS.Write", 'DLSYS.Write("MODULE/BLOCK/SP", 50);'),
    ("DLSYS.AckAllAlarms", "DLSYS.AckAllAlarms();"),
    ("DL.OpenDisplay", 'DL.OpenDisplay("Overview");'),
    ("DL store", 'DL.AddStoreItem("name", value);\nDL.GetStoreItem("name");'),
    ("DL selected tag", 'DL.SelectTag("MODULE/BLOCK/PV");'),
    ("Display property", 'Dsp.ElementName.Visible = true;'),
    ("Current element", 'This.FillColor = "#4C6684";'),
    ("PVM property", 'Pvm.Label = "Running";'),
    ("Layout variable", "Lyt.BatchId = 1001;"),
    ("Global standard", "const colour = GL.StandardName;"),
    ("Diagnostic", 'DL.SubmitDiagnosticFailure("message");'),
    ("Date / Math", "const stamp = new Date();\nconst value = Math.round(1.5);"),
)


class _TypeScriptHighlighter(QSyntaxHighlighter):
    """Small, dependency-free highlighter; validation is handled separately."""

    def __init__(self, document):
        super().__init__(document)
        self.rules = []
        from PySide6.QtCore import QRegularExpression

        def add(pattern, colour, bold=False):
            form = QTextCharFormat()
            form.setForeground(QColor(colour))
            if bold:
                form.setFontWeight(QFont.Bold)
            self.rules.append((QRegularExpression(pattern), form))

        add(r"\b(const|let|var|if|else|for|while|return|function|async|await|"
            r"true|false|null|undefined|new)\b", AUTHORING_BLUE, True)
        add(r"\b(number|string|boolean|object|void|unknown)\b", "#7B4FA3")
        add(r"\b(DL|DLSYS|DLPATH|Dsp|Lyt|Grp|" + "|".join(PVM_SCOPE_NAMES)
            + r"|GL|ENV|SQL|This)\b",
            "#007B78", True)
        add(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`[^`]*`',
            "#9A4B16")
        add(r"//[^\n]*", "#6A737D")
        add(r"\b\d+(?:\.\d+)?\b", "#875F00")

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        for expression, form in self.rules:
            match = expression.globalMatch(text)
            while match.hasNext():
                result = match.next()
                self.setFormat(result.capturedStart(),
                               result.capturedLength(), form)


class _ScriptEdit(QPlainTextEdit):
    """The code pane, with completion over the real graphics API.

    Completion reads `scripting.api_completions()` rather than a list kept
    here, so a member added to the runtime appears in the editor instead
    of being something only the manual knows about.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # Held on the instance: a completer owned only by C++ is the same
        # trap as a parentless QShortcut wrapper.
        self.completer = QCompleter(self)
        self.completer.setModel(QStringListModel(list(api_completions()),
                                                 self.completer))
        self.completer.setWidget(self)
        self.completer.setCompletionMode(QCompleter.PopupCompletion)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.activated.connect(self._insert_completion)

    def _prefix(self) -> str:
        """The dotted identifier left of the cursor (`DLSYS.Re`)."""
        cursor = self.textCursor()
        line = cursor.block().text()[:cursor.positionInBlock()]
        index = len(line)
        while index and (line[index - 1].isalnum()
                         or line[index - 1] in "_$."):
            index -= 1
        return line[index:]

    def _insert_completion(self, completion: str) -> None:
        prefix = self._prefix()
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.Left, QTextCursor.KeepAnchor,
                            len(prefix))
        cursor.insertText(completion)
        self.setTextCursor(cursor)

    def keyPressEvent(self, event):                 # noqa: N802
        popup = self.completer.popup()
        if popup.isVisible() and event.key() in (
                Qt.Key_Enter, Qt.Key_Return, Qt.Key_Tab, Qt.Key_Escape,
                Qt.Key_Up, Qt.Key_Down):
            # The popup owns these keys while it is open; passing them to
            # the document would insert a newline behind the list.
            event.ignore()
            return
        super().keyPressEvent(event)
        prefix = self._prefix()
        if len(prefix) < 2 and not prefix.endswith("."):
            popup.hide()
            return
        self.completer.setCompletionPrefix(prefix)
        if not self.completer.completionCount():
            popup.hide()
            return
        popup.setCurrentIndex(self.completer.completionModel().index(0, 0))
        rect = self.cursorRect()
        rect.setWidth(popup.sizeHintForColumn(0)
                      + popup.verticalScrollBar().sizeHint().width() + 24)
        self.completer.complete(rect)


class ScriptAssistantDialog(QDialog):
    """TypeScript editor with object-model snippets, find/replace and test."""

    def __init__(self, source: str = "", *, runner=None, parent=None):
        super().__init__(parent)
        # The assistant is also importable as a standalone authoring tool;
        # without the packaged face an offscreen/clean workstation resolves
        # Qt's default to a missing font and the entire editor becomes tofu.
        from azeo_control_trainer.core.hmi.theme.fonts import apply_application_font
        apply_application_font()
        self.runtime = GraphicsScriptRuntime()
        self.runner = runner
        self.setWindowTitle("Script Assistant — TypeScript")
        self.setWindowFlags(self.windowFlags() | Qt.WindowMinMaxButtonsHint)
        self.resize(1060, 680)
        self.setStyleSheet(AUTHORING_CONTROLS_QSS)
        self._build(source)

    @property
    def source(self) -> str:
        return self.editor.toPlainText()

    def _build(self, source: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        add_authoring_dialog_header(
            self,
            root,
            "Script Assistant",
            "TypeScript event script · restricted graphics API",
        )

        find_bar = QHBoxLayout()
        self.find_text = QLineEdit()
        self.find_text.setPlaceholderText("Find in active script")
        self.replace_text = QLineEdit()
        self.replace_text.setPlaceholderText("Replace with")
        find = QPushButton("Find Next")
        find.clicked.connect(self.find_next)
        replace = QPushButton("Replace")
        replace.clicked.connect(self.replace_one)
        replace_all = QPushButton("Replace All")
        replace_all.clicked.connect(self.replace_all)
        for widget in (self.find_text, self.replace_text, find, replace,
                       replace_all):
            find_bar.addWidget(widget)
        root.addLayout(find_bar)

        split = QSplitter(Qt.Horizontal)
        self.side = QTabWidget()
        assistant = QWidget()
        left = QVBoxLayout(assistant)
        left.setContentsMargins(0, 0, 0, 0)
        left.addWidget(QLabel("OBJECTS & SNIPPETS"))
        self.snippets = QListWidget()
        for label, code in SNIPPETS:
            row = QListWidgetItem(label)
            row.setData(Qt.UserRole, code)
            row.setToolTip(code)
            self.snippets.addItem(row)
        self.snippets.itemDoubleClicked.connect(self.insert_snippet)
        left.addWidget(self.snippets)
        insert = QPushButton("Insert Selected")
        insert.clicked.connect(
            lambda: self.insert_snippet(self.snippets.currentItem()))
        left.addWidget(insert)
        self.side.addTab(assistant, "Snippets")

        # The boundary belongs in front of the author, not behind a
        # refusal at run time.
        self.reference = QPlainTextEdit()
        self.reference.setObjectName("script_reference")
        self.reference.setReadOnly(True)
        self.reference.setFont(QFont("Consolas", 9))
        self.reference.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.reference.setPlainText(api_declarations())
        self.side.addTab(self.reference, "API")

        limits = QPlainTextEdit()
        limits.setObjectName("script_limits")
        limits.setReadOnly(True)
        limits.setPlainText("\n\n".join(
            f"• {note}" for note in RESTRICTED_NOTES))
        self.side.addTab(limits, "Restrictions")
        split.addWidget(self.side)

        editor_host = QWidget()
        centre = QVBoxLayout(editor_host)
        centre.setContentsMargins(0, 0, 0, 0)
        self.editor = _ScriptEdit()
        self.editor.setObjectName("typescript_editor")
        self.editor.setFont(QFont("Consolas", 10))
        self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.editor.setPlaceholderText(
            '// Click event\nconst pv: number = DLSYS.Read("FIC-101/PID1/PV");')
        self.editor.setPlainText(source)
        self.highlighter = _TypeScriptHighlighter(self.editor.document())
        centre.addWidget(self.editor, 1)
        split.addWidget(editor_host)
        # Wide enough that a declaration line reads without dragging the
        # splitter. No minimum width is imposed: a hard minimum on a
        # splitter child becomes the dialog's own minimum.
        split.setSizes([340, 700])
        root.addWidget(split, 1)

        controls = QHBoxLayout()
        validate = QPushButton("Validate")
        validate.clicked.connect(self.validate_script)
        test = QPushButton("Test")
        test.clicked.connect(self.test_script)
        controls.addWidget(validate)
        controls.addWidget(test)
        controls.addStretch()
        root.addLayout(controls)

        self.output = QPlainTextEdit()
        self.output.setObjectName("script_output")
        self.output.setReadOnly(True)
        self.output.setMaximumHeight(105)
        self.output.setPlaceholderText("Validation and test output")
        root.addWidget(self.output)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept_valid)
        buttons.rejected.connect(self.reject)
        style_dialog_buttons(buttons, QDialogButtonBox.Save)
        root.addWidget(buttons)

    def insert_snippet(self, item) -> None:
        if item is None:
            return
        self.editor.insertPlainText(str(item.data(Qt.UserRole)))
        self.editor.setFocus()

    def find_next(self) -> bool:
        needle = self.find_text.text()
        return bool(needle and self.editor.find(needle))

    def replace_one(self) -> bool:
        cursor = self.editor.textCursor()
        if cursor.hasSelection() and cursor.selectedText() == self.find_text.text():
            cursor.insertText(self.replace_text.text())
            return True
        if not self.find_next():
            return False
        self.editor.textCursor().insertText(self.replace_text.text())
        return True

    def replace_all(self) -> int:
        old = self.find_text.text()
        if not old:
            return 0
        source = self.source
        count = source.count(old)
        if count:
            self.editor.setPlainText(source.replace(old, self.replace_text.text()))
        return count

    def validate_script(self) -> bool:
        answer = self.runtime.validate(self.source)
        self._show_result("Validated", answer)
        return answer.ok

    def test_script(self) -> bool:
        answer = self.runner(self.source) if self.runner is not None \
            else self.runtime.run(self.source, ScriptContext())
        self._show_result("Test", answer)
        return answer.ok

    def mark_error_line(self, line: int) -> bool:
        """Underline the failing line and put the caret on it.

        A pass/fail message makes the author hunt for the statement; the
        runtime already knows which line it was.
        """
        self.editor.setExtraSelections([])
        if line <= 0:
            return False
        block = self.editor.document().findBlockByNumber(line - 1)
        if not block.isValid():
            return False
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(QColor("#FBE3E4"))
        selection.format.setProperty(QTextCharFormat.FullWidthSelection, True)
        cursor = QTextCursor(block)
        selection.cursor = cursor
        selection.cursor.clearSelection()
        self.editor.setExtraSelections([selection])
        self.editor.setTextCursor(cursor)
        self.editor.centerCursor()
        return True

    def _show_result(self, label, answer) -> None:
        if answer.ok:
            self.editor.setExtraSelections([])
            lines = [f"{label}: OK"]
            if answer.value is not None:
                lines.append(f"Result: {answer.value!r}")
        else:
            where = f" at line {answer.line}" if answer.line else ""
            lines = [f"{label}: ERROR{where}: {answer.error}"]
            self.mark_error_line(answer.line)
        lines.extend(answer.diagnostics)
        self.output.setPlainText("\n".join(lines))

    def _accept_valid(self) -> None:
        if self.validate_script():
            self.accept()


def edit_script(source: str = "", *, runner=None, parent=None) -> str | None:
    """Open the assistant and return saved source (None when cancelled)."""
    dialog = ScriptAssistantDialog(source, runner=runner, parent=parent)
    from azeo_control_trainer.core.presentation.headless import is_headless
    if is_headless():
        return source
    return dialog.source if dialog.exec() == QDialog.Accepted else None


__all__ = ["SNIPPETS", "ScriptAssistantDialog", "edit_script"]
