"""Python script editor widget for ACT (and other script-bearing) blocks.

Three layered pieces:

    PythonHighlighter
        QSyntaxHighlighter that colours keywords, builtins, strings,
        comments, numbers, decorators, and the ACT-specific helper
        names (``clamp``, ``ewma``, ``get_tag``, ``set_tag``, ...).

    CodeEditor
        QPlainTextEdit subclass with:
          - line-number gutter
          - current-line highlight
          - Tab/Shift-Tab block indent (spaces, never tabs)
          - auto-indent after ``:``
          - QCompleter popup over the ACT namespace + Python keywords

    PyScriptEditorWidget
        The whole side-by-side editor: code editor on the left,
        searchable reference panel on the right listing every callable
        in the ACT namespace with one-line descriptions. Subscribers
        wire ``Apply`` to push the new source back into the block
        config (the ACT dialog does this).

The reference list is built from
:func:`azeo_control_trainer.core.strategy.blocks.script_functions.get_script_functions`
so plugin-registered helpers (per ``register_script_function``) appear
in the list automatically.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

import inspect
import keyword
import re
from typing import Callable

from PySide6.QtCore import (
    QEvent, QRect, QRegularExpression, QSize, Qt, QStringListModel, Signal,
)
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QPainter, QSyntaxHighlighter,
    QTextCharFormat, QTextCursor, QTextFormat, QKeyEvent,
)
from PySide6.QtWidgets import (
    QCompleter, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QSplitter, QTextEdit, QVBoxLayout, QWidget,
)


# ──────────────────────────────────────────────────────────────────────
# Syntax highlighter
# ──────────────────────────────────────────────────────────────────────

class PythonHighlighter(QSyntaxHighlighter):
    """Pragmatic Python syntax highlighter — covers the patterns ACT
    scripts actually use without trying to be a complete Python lexer."""

    KEYWORDS = keyword.kwlist + ["True", "False", "None"]
    BUILTINS = [
        "abs", "all", "any", "bool", "dict", "enumerate", "filter",
        "float", "int", "len", "list", "map", "max", "min", "range",
        "round", "set", "sorted", "str", "sum", "tuple", "zip",
        "print", "isinstance", "type",
    ]

    def __init__(self, doc, extra_names: list[str] | None = None):
        super().__init__(doc)
        self._build_formats()
        self._build_rules(extra_names or [])
        # Multi-line string state
        self._tri_double = QRegularExpression('"""')
        self._tri_single = QRegularExpression("'''")

    # ----- styling -----
    def _fmt(self, color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
        f = QTextCharFormat()
        f.setForeground(QColor(color))
        if bold:
            f.setFontWeight(QFont.Bold)
        if italic:
            f.setFontItalic(True)
        return f

    def _build_formats(self):
        self.f_keyword = self._fmt("#1A6FB0", bold=True)
        self.f_builtin = self._fmt("#7B2D8C")
        self.f_helper  = self._fmt("#0E7D4B", bold=True)
        self.f_string  = self._fmt("#A04420")
        self.f_comment = self._fmt("#777777", italic=True)
        self.f_number  = self._fmt("#9B5E1A")
        self.f_deco    = self._fmt("#8A5A00", italic=True)
        self.f_def     = self._fmt("#0E3260", bold=True)
        self.f_self    = self._fmt("#5F2C8A", italic=True)
        self.f_tripled = self._fmt("#A04420")

    def _build_rules(self, extras: list[str]):
        rules: list[tuple[QRegularExpression, QTextCharFormat]] = []
        kw = r'\b(?:' + '|'.join(self.KEYWORDS) + r')\b'
        rules.append((QRegularExpression(kw), self.f_keyword))
        bi = r'\b(?:' + '|'.join(self.BUILTINS) + r')\b'
        rules.append((QRegularExpression(bi), self.f_builtin))
        if extras:
            uniq = sorted(set(re.escape(x) for x in extras))
            rules.append((QRegularExpression(r'\b(?:' + '|'.join(uniq) + r')\b'),
                          self.f_helper))
        # function definitions
        rules.append((QRegularExpression(r'\bdef\s+(\w+)'), self.f_def))
        # self / state
        rules.append((QRegularExpression(r'\b(?:self|state|IN[1-8]|OUT[1-4]|dt|time)\b'),
                      self.f_self))
        # decorators
        rules.append((QRegularExpression(r'@\w+'), self.f_deco))
        # numbers (int / float / hex)
        rules.append((QRegularExpression(r'\b(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\b'),
                      self.f_number))
        # single-line strings (both quotes)
        rules.append((QRegularExpression(r'"[^"\\\n]*(?:\\.[^"\\\n]*)*"'), self.f_string))
        rules.append((QRegularExpression(r"'[^'\\\n]*(?:\\.[^'\\\n]*)*'"), self.f_string))
        # comments must come last so they win over keywords/strings
        rules.append((QRegularExpression(r'#[^\n]*'), self.f_comment))
        self._rules = rules

    # ----- main entry point -----
    def highlightBlock(self, text: str):
        # Single-line rules first
        for rx, fmt in self._rules:
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), fmt)
        # Triple-quoted strings spanning lines — minimal state machine
        self.setCurrentBlockState(0)
        self._match_triples(text, '"""', 1)
        self._match_triples(text, "'''", 2)

    def _match_triples(self, text: str, delim: str, state: int):
        start = 0
        if self.previousBlockState() == state:
            end = text.find(delim)
            if end == -1:
                self.setCurrentBlockState(state)
                self.setFormat(0, len(text), self.f_tripled)
                return
            self.setFormat(0, end + 3, self.f_tripled)
            start = end + 3
        while True:
            s = text.find(delim, start)
            if s == -1:
                break
            e = text.find(delim, s + 3)
            if e == -1:
                self.setCurrentBlockState(state)
                self.setFormat(s, len(text) - s, self.f_tripled)
                return
            self.setFormat(s, e - s + 3, self.f_tripled)
            start = e + 3


# ──────────────────────────────────────────────────────────────────────
# Code editor with line numbers + indent + completer
# ──────────────────────────────────────────────────────────────────────

class _LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event):
        self._editor.paint_line_numbers(event)


class CodeEditor(QPlainTextEdit):
    """Python code editor — line numbers, current-line highlight,
    tab-as-spaces, auto-indent, and completer over a configurable
    name list."""

    TAB_SPACES = 4

    def __init__(self, name_list: list[str] | None = None, parent=None):
        super().__init__(parent)
        self._gutter = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._highlight_current_line)

        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        mono.setPointSize(10)
        self.setFont(mono)
        self.setTabStopDistance(self.TAB_SPACES * self.fontMetrics().horizontalAdvance(' '))
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setStyleSheet(
            f"QPlainTextEdit {{ background: #FAFBFD; color: {UI.blue};"
            f" border: 1px solid {UI.border}; border-radius: 3px;"
            f" selection-background-color: {UI.selection}; padding: 4px; }}")

        self._highlighter = PythonHighlighter(self.document(), extra_names=name_list or [])
        self._completer: QCompleter | None = None
        if name_list:
            self.set_completer_words(name_list)

        self._update_gutter_width(0)
        self._highlight_current_line()

    # ----- completer -----
    def set_completer_words(self, words: list[str]):
        c = QCompleter(sorted(set(words)), self)
        c.setCaseSensitivity(Qt.CaseInsensitive)
        c.setWrapAround(False)
        c.setWidget(self)
        c.setCompletionMode(QCompleter.PopupCompletion)
        c.activated.connect(self._insert_completion)
        self._completer = c

    def _text_under_cursor(self) -> str:
        tc = self.textCursor()
        tc.select(QTextCursor.WordUnderCursor)
        return tc.selectedText()

    def _insert_completion(self, completion: str):
        if self._completer is None:
            return
        tc = self.textCursor()
        extra = len(completion) - len(self._completer.completionPrefix())
        tc.movePosition(QTextCursor.Left, n=0)
        tc.movePosition(QTextCursor.EndOfWord)
        tc.insertText(completion[-extra:] if extra > 0 else "")
        self.setTextCursor(tc)

    # ----- gutter / line numbers -----
    def line_number_area_width(self) -> int:
        digits = len(str(max(1, self.blockCount())))
        return 8 + self.fontMetrics().horizontalAdvance('9') * max(3, digits)

    def _update_gutter_width(self, _new_block_count: int):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_gutter(self, rect: QRect, dy: int):
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width(0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cr = self.contentsRect()
        self._gutter.setGeometry(QRect(cr.left(), cr.top(),
                                        self.line_number_area_width(), cr.height()))

    def paint_line_numbers(self, event):
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), QColor("#EEF1F6"))
        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        bottom = top + self.blockBoundingRect(block).height()
        painter.setPen(QColor("#8090A0"))
        f = self.font(); painter.setFont(f)
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(0, int(top),
                                 self._gutter.width() - 4,
                                 self.fontMetrics().height(),
                                 Qt.AlignRight, str(block_number + 1))
            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
            block_number += 1

    def _highlight_current_line(self):
        sel = QTextEdit.ExtraSelection()
        line_color = QColor("#E6EEF7")
        sel.format.setBackground(line_color)
        sel.format.setProperty(QTextFormat.FullWidthSelection, True)
        sel.cursor = self.textCursor()
        sel.cursor.clearSelection()
        self.setExtraSelections([sel])

    # ----- key handling: tab/auto-indent/completer popup -----
    def keyPressEvent(self, e: QKeyEvent):
        # Completer popup acceptance keys
        if self._completer and self._completer.popup().isVisible():
            if e.key() in (Qt.Key_Enter, Qt.Key_Return, Qt.Key_Escape,
                            Qt.Key_Tab, Qt.Key_Backtab):
                e.ignore()
                return

        # Tab / Shift-Tab — indent / dedent selected block (or insert spaces)
        if e.key() == Qt.Key_Tab:
            cur = self.textCursor()
            if cur.hasSelection():
                self._indent_selection(cur, True)
                return
            cur.insertText(" " * self.TAB_SPACES)
            return
        if e.key() == Qt.Key_Backtab:
            cur = self.textCursor()
            self._indent_selection(cur, False)
            return

        # Enter — auto-indent + extra indent if previous line ends with ':'
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            cur = self.textCursor()
            block_text = cur.block().text()
            indent = re.match(r'[ \t]*', block_text).group(0)
            extra = ' ' * self.TAB_SPACES if block_text.rstrip().endswith(':') else ''
            super().keyPressEvent(e)
            self.insertPlainText(indent + extra)
            return

        super().keyPressEvent(e)

        # Trigger completer for word chars
        if self._completer:
            prefix = self._text_under_cursor()
            if len(prefix) >= 2 and re.match(r'^[A-Za-z_]\w*$', prefix):
                if prefix != self._completer.completionPrefix():
                    self._completer.setCompletionPrefix(prefix)
                    self._completer.popup().setCurrentIndex(
                        self._completer.completionModel().index(0, 0))
                cr = self.cursorRect()
                cr.setWidth(self._completer.popup().sizeHintForColumn(0)
                            + self._completer.popup().verticalScrollBar().sizeHint().width())
                self._completer.complete(cr)
            else:
                self._completer.popup().hide()

    def _indent_selection(self, cur: QTextCursor, indent: bool):
        start = cur.selectionStart()
        end = cur.selectionEnd()
        cur.setPosition(start)
        cur.movePosition(QTextCursor.StartOfBlock)
        cur.setPosition(end, QTextCursor.KeepAnchor)
        cur.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
        text = cur.selectedText().replace(' ', '\n')
        if indent:
            new = '\n'.join((' ' * self.TAB_SPACES) + line for line in text.split('\n'))
        else:
            new = '\n'.join(_dedent_one_level(line, self.TAB_SPACES) for line in text.split('\n'))
        cur.insertText(new)


def _dedent_one_level(line: str, tab_spaces: int) -> str:
    n = 0
    while n < tab_spaces and n < len(line) and line[n] == ' ':
        n += 1
    return line[n:]


# ──────────────────────────────────────────────────────────────────────
# Side-by-side editor + reference panel
# ──────────────────────────────────────────────────────────────────────

class PyScriptEditorWidget(QWidget):
    """Editor + reference panel composite for embedding in a dialog."""

    textChanged = Signal()

    def __init__(self, initial_text: str = "", parent=None):
        super().__init__(parent)
        self._names = _collect_function_names()
        self._build_ui(initial_text)

    def _build_ui(self, initial_text: str):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        split = QSplitter(Qt.Horizontal)
        root.addWidget(split, 1)

        # ----- Left: code editor -----
        self.editor = CodeEditor(name_list=self._names)
        self.editor.setPlainText(initial_text)
        self.editor.textChanged.connect(self.textChanged.emit)
        split.addWidget(self.editor)

        # ----- Right: searchable reference panel -----
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 6, 6, 6)
        rl.setSpacing(4)
        title = QLabel("Built-in helpers")
        title.setStyleSheet(
            f"font-weight: bold; color: {UI.blue}; font-size: 9pt;"
            f" background: {UI.hover}; padding: 4px 6px;"
            f" border: 1px solid {UI.border};")
        rl.addWidget(title)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter...")
        self._search.setStyleSheet("font-size: 9pt; padding: 2px 4px;")
        self._search.textChanged.connect(self._filter)
        rl.addWidget(self._search)

        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget {{ background: white; border: 1px solid {UI.border};"
            " font-family: Consolas, 'Courier New'; font-size: 9pt; }"
            f" QListWidget::item:hover {{ background: {UI.selection}; }}"
            f" QListWidget::item:selected {{ background: {UI.blue}; color: white; }}")
        self._list.itemDoubleClicked.connect(self._insert_helper)
        self._populate_list()
        rl.addWidget(self._list, 1)

        hint = QLabel("Double-click to insert. Type 2+ chars in editor for autocomplete.")
        hint.setStyleSheet("color: #777; font-size: 9pt;")
        hint.setWordWrap(True)
        rl.addWidget(hint)

        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([520, 280])

    # ----- public API -----
    def text(self) -> str:
        return self.editor.toPlainText()

    def set_text(self, text: str):
        self.editor.setPlainText(text)

    # ----- reference panel -----
    def _populate_list(self):
        self._list.clear()
        from azeo_control_trainer.core.strategy.blocks.script_functions import get_script_functions
        funcs = get_script_functions()
        for name in sorted(funcs.keys()):
            fn = funcs[name]
            sig = _safe_signature(fn)
            doc = (inspect.getdoc(fn) or "").split('\n', 1)[0]
            item = QListWidgetItem(f"{name}{sig}")
            item.setData(Qt.UserRole, name)
            item.setToolTip(f"{name}{sig}\n\n{doc}" if doc else f"{name}{sig}")
            self._list.addItem(item)
        # also add tag I/O entries — they only exist with a runtime context
        # but operators still want to discover them
        for name, sig, doc in (
            ("get_tag", "(name, default=None)",
             "Read any tag from the SharedDataStore."),
            ("set_tag", "(name, value)",
             "Write a tag via store.queue_write — must be allow-listed by the plugin."),
            ("tag_exists", "(name)",
             "True iff the tag exists in the current store snapshot."),
        ):
            item = QListWidgetItem(f"{name}{sig}")
            item.setData(Qt.UserRole, name)
            item.setToolTip(f"{name}{sig}\n\n{doc}")
            self._list.addItem(item)

    def _filter(self, text: str):
        t = text.lower().strip()
        for i in range(self._list.count()):
            it = self._list.item(i)
            it.setHidden(bool(t) and t not in it.text().lower())

    def _insert_helper(self, item: QListWidgetItem):
        name = item.data(Qt.UserRole)
        cur = self.editor.textCursor()
        cur.insertText(name + "()")
        cur.movePosition(QTextCursor.Left)
        self.editor.setTextCursor(cur)
        self.editor.setFocus()


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _collect_function_names() -> list[str]:
    """Combined list of names for highlighter / completer."""
    from azeo_control_trainer.core.strategy.blocks.script_functions import get_script_functions
    names: set[str] = set(get_script_functions().keys())
    names.update([
        "get_tag", "set_tag", "tag_exists",
        "state", "dt", "time", "IN1", "IN2", "IN3", "IN4",
        "IN5", "IN6", "IN7", "IN8",
        "OUT1", "OUT2", "OUT3", "OUT4",
        "OUT1_PREV", "OUT2_PREV", "OUT3_PREV", "OUT4_PREV",
        "x", "y", "z",
        "MAN", "AUTO", "CAS", "RCAS", "OOS",
        "pi", "e", "inf",
    ])
    return sorted(names)


def _safe_signature(fn: Callable) -> str:
    try:
        return str(inspect.signature(fn))
    except (ValueError, TypeError):
        return "(...)"
