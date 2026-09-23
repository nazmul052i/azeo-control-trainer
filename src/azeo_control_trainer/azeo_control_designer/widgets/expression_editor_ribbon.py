r"""Azeo-style ribbon expression editor for ACT / CND scripts.

Layout mirrors the standard Azeo "Condition / Expression" editor:

    ┌─────────────────────────────────────────────────────────────────────┐
    │ ┌─Clipboard──┐┌────Operators────┐┌──Function Library──┐┌──Azeo    │
    │ │ Paste      ││ + * = <= >= (   ││ fx Functions       ││  Functions │
    │ │ Cut Copy   ││ - / != < > )    ││ Recently Used      ││ Internal/  │
    │ │            ││ := NOT AND OR   ││                    ││ External…  │
    │ └────────────┘└─────────────────┘└────────────────────┘└────────────│
    │ ─────────────────────────────────────────────────────────────────── │
    │  Toolbar:   Save  Open  Undo  Redo  Help  |  Find  Replace  Go To  │
    │ ─────────────────────────────────────────────────────────────────── │
    │ Expression:                                                         │
    │ 1  | clamp(IN1, 0, 100) > get_tag('xmeas_9')                       │
    │                                                                     │
    │ ───────────────────────────────────────────────────────────────────│
    │ Parser output:                                                      │
    │                                                                     │
    │                                                                     │
    │ ───────────────────────────────────────────────────────────────────│
    │ [Parse]                                          [OK]    [Cancel]   │
    └─────────────────────────────────────────────────────────────────────┘

The editor delegates the actual text widget to :class:`CodeEditor` from
``pyscript_editor`` — it already provides syntax highlighting, line
numbers, autocomplete, Tab-to-spaces, and auto-indent. The ribbon
wrapper adds the DCS-style chrome on top.

Public API
----------
``RibbonExpressionEditor(initial_text="", mode="expression", store=None,
                          parent=None)``
    Embeddable widget. ``mode`` is ``"expression"`` (single line, AST
    eval) or ``"script"`` (multi-line, ``compile`` with ``exec``).
    Pass a SharedDataStore for the tag picker to read from.

``RibbonExpressionEditorDialog(...)`` — same as above but wrapped in a
top-level QDialog with OK/Cancel.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

from typing import Callable

from azeo_control_trainer.core.presentation.menu_style import studio_menu
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMenu, QToolButton,
    QVBoxLayout, QWidget, QPlainTextEdit,
)


# ───────────────────────────────────────────────────────────────────────
# Ribbon group helper
# ───────────────────────────────────────────────────────────────────────
class _RibbonGroup(QFrame):
    """One titled column on the ribbon, with a label strip at the bottom."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(
            f"_RibbonGroup {{ background: {UI.pane};"
            " border-right: 1px solid #C0C5D0; }")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 6, 2)
        outer.setSpacing(2)
        # Content area (caller fills)
        self.content = QHBoxLayout()
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(2)
        outer.addLayout(self.content, 1)
        # Title strip
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            "color: #555; font-size: 9pt; padding-top: 2px;")
        title_lbl.setAlignment(Qt.AlignHCenter)
        outer.addWidget(title_lbl)

    def add(self, w):
        self.content.addWidget(w)
        return w


def _ribbon_tool_button(text: str, tooltip: str = "",
                          checkable: bool = False,
                          on_click: Callable | None = None) -> QToolButton:
    btn = QToolButton()
    btn.setText(text)
    btn.setToolTip(tooltip or text)
    btn.setCheckable(checkable)
    btn.setMinimumWidth(28)
    btn.setMinimumHeight(22)
    btn.setStyleSheet(
        f"QToolButton {{ background: #FAFBFD; border: 1px solid {UI.border};"
        " border-radius: 2px; padding: 1px 6px; font-family: Consolas;"
        f" font-size: 9pt; color: {UI.blue}; }}"
        f" QToolButton:hover {{ background: {UI.hover}; border-color: {UI.blue}; }}"
        f" QToolButton:pressed {{ background: {UI.selection}; }}")
    if on_click is not None:
        btn.clicked.connect(on_click)
    return btn


# ───────────────────────────────────────────────────────────────────────
# Main widget
# ───────────────────────────────────────────────────────────────────────
class RibbonExpressionEditor(QWidget):
    """Azeo-style expression editor.

    Signals
    -------
    parseCompleted(bool, str)
        Emitted by the Parse button — ``ok`` flag + message text.
    textChangedSignal
        Mirrors the inner CodeEditor's ``textChanged`` signal.
    """

    parseCompleted = Signal(bool, str)
    textChangedSignal = Signal()

    def __init__(self, initial_text: str = "", mode: str = "expression",
                 store=None, parent=None):
        super().__init__(parent)
        self._mode = mode
        self._store = store
        self._recent_funcs: list[str] = []
        self._shortcuts: list[QShortcut] = []
        self._build_ui(initial_text)
        # Ctrl+F: open Find
        self._shortcuts.extend((
            QShortcut(QKeySequence.Find, self, activated=self._show_find),
            QShortcut(QKeySequence.Replace, self,
                      activated=self._show_replace),
            QShortcut(QKeySequence("Ctrl+G"), self,
                      activated=self._show_goto),
            QShortcut(QKeySequence.HelpContents, self,
                      activated=self._show_help),
        ))

    # ----- UI build -----
    def _build_ui(self, initial_text: str):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Ribbon row ─────────────────────────────────────────────
        ribbon = QFrame()
        ribbon.setStyleSheet(
            f"QFrame {{ background: {UI.pane};"
            f" border-bottom: 1px solid {UI.border}; }}")
        rrow = QHBoxLayout(ribbon)
        rrow.setContentsMargins(2, 2, 2, 2)
        rrow.setSpacing(0)

        # Clipboard group
        gclip = _RibbonGroup("Clipboard")
        gclip.add(_ribbon_tool_button("Paste", "Paste from clipboard (Ctrl+V)",
                                         on_click=self._cmd_paste))
        gclip.add(_ribbon_tool_button("Cut", "Cut selection (Ctrl+X)",
                                         on_click=self._cmd_cut))
        gclip.add(_ribbon_tool_button("Copy", "Copy selection (Ctrl+C)",
                                         on_click=self._cmd_copy))
        rrow.addWidget(gclip)

        # Operators group
        gops = _RibbonGroup("Operators")
        op_grid = QVBoxLayout()
        op_grid.setSpacing(2)
        for row in (
            ["+", "*", "=", "<=", ">=", "("],
            ["-", "/", "!=", "<", ">", ")"],
            [":=", "NOT", "AND", "OR"],
        ):
            r = QHBoxLayout()
            r.setSpacing(2)
            for op in row:
                b = _ribbon_tool_button(
                    op, f"Insert {op}",
                    on_click=lambda _, t=op: self._insert(t))
                b.setMinimumWidth(36 if len(op) >= 3 else 26)
                r.addWidget(b)
            op_grid.addLayout(r)
        wrap = QWidget()
        wrap.setLayout(op_grid)
        gops.add(wrap)
        rrow.addWidget(gops)

        # Function Library group
        gfn = _RibbonGroup("Function Library")
        fn_col = QVBoxLayout()
        fn_col.setSpacing(2)
        self._fn_btn = QToolButton()
        self._fn_btn.setText("ƒx  Functions")
        self._fn_btn.setPopupMode(QToolButton.InstantPopup)
        self._fn_btn.setStyleSheet(
            f"QToolButton {{ background: #FAFBFD; border: 1px solid {UI.border};"
            " border-radius: 2px; padding: 2px 8px; font-size: 9pt; }"
            " QToolButton::menu-indicator { image: none; }")
        self._fn_btn.setMenu(self._build_functions_menu())
        fn_col.addWidget(self._fn_btn)

        self._recent_btn = QToolButton()
        self._recent_btn.setText("Recently Used")
        self._recent_btn.setPopupMode(QToolButton.InstantPopup)
        self._recent_btn.setStyleSheet(self._fn_btn.styleSheet())
        self._recent_btn.setMenu(self._build_recent_menu())
        fn_col.addWidget(self._recent_btn)
        wrap2 = QWidget()
        wrap2.setLayout(fn_col)
        gfn.add(wrap2)
        rrow.addWidget(gfn)

        # Azeo-style DCS Functions group
        gdv = _RibbonGroup("DCS Functions")
        dv_grid = QVBoxLayout()
        dv_grid.setSpacing(2)
        for row in (
            [("Internal Parameter", self._pick_internal_param),
             ("Named State",        self._pick_named_state)],
            [("External Parameter", self._pick_external_param),
             ("SELSTR",             lambda: self._insert("select_hi("))],
            [("Alias",              self._pick_alias),
             ("LOGEVENT",           lambda: self._insert("log_event("))],
        ):
            r = QHBoxLayout()
            r.setSpacing(2)
            for label, slot in row:
                b = _ribbon_tool_button(label, label, on_click=slot)
                b.setMinimumWidth(96)
                r.addWidget(b)
            dv_grid.addLayout(r)
        wrap3 = QWidget()
        wrap3.setLayout(dv_grid)
        gdv.add(wrap3)
        rrow.addWidget(gdv)

        # Editing group
        gedit = _RibbonGroup("Editing")
        edit_grid = QVBoxLayout()
        edit_grid.setSpacing(2)
        for row in (
            [("Find",    self._show_find),
             ("Replace", self._show_replace)],
            [("Go To",   self._show_goto),
             ("Insert",  self._pick_insert)],
        ):
            r = QHBoxLayout()
            r.setSpacing(2)
            for label, slot in row:
                b = _ribbon_tool_button(label, label, on_click=slot)
                b.setMinimumWidth(64)
                r.addWidget(b)
            edit_grid.addLayout(r)
        wrap4 = QWidget()
        wrap4.setLayout(edit_grid)
        gedit.add(wrap4)
        rrow.addWidget(gedit)

        rrow.addStretch(1)
        root.addWidget(ribbon)

        # ── Secondary thin toolbar: Save / Open / Undo / Redo / Help ──
        sub = QFrame()
        sub.setStyleSheet("QFrame { background: #ECEEF3;"
                           " border-bottom: 1px solid #C0C5D0; }")
        sr = QHBoxLayout(sub)
        sr.setContentsMargins(4, 2, 4, 2)
        sr.setSpacing(4)
        for label, tip, slot in (
            ("Save",  "Save (Ctrl+S)",   self._cmd_save),
            ("Open",  "Open (Ctrl+O)",   self._cmd_open),
            ("Undo",  "Undo (Ctrl+Z)",   lambda: self.editor.undo()),
            ("Redo",  "Redo (Ctrl+Y)",   lambda: self.editor.redo()),
            ("Help",  "Show syntax help", self._show_help),
        ):
            sr.addWidget(_ribbon_tool_button(label, tip, on_click=slot))
        sr.addStretch(1)
        # Mode indicator
        self._mode_lbl = QLabel()
        self.set_mode(self._mode)
        self._mode_lbl.setStyleSheet("color: #555; font-size: 9pt;")
        sr.addWidget(self._mode_lbl)
        root.addWidget(sub)

        # ── Expression label + editor ──────────────────────────────
        expr_lbl = QLabel("Expression:")
        expr_lbl.setStyleSheet(
            f"color: {UI.blue}; font-weight: bold; font-size: 9pt;"
            f" background: {UI.hover}; padding: 3px 8px;"
            f" border-bottom: 1px solid {UI.border};")
        root.addWidget(expr_lbl)

        from .pyscript_editor import CodeEditor, _collect_function_names
        self.editor = CodeEditor(name_list=_collect_function_names())
        self.editor.setPlainText(initial_text)
        self.editor.textChanged.connect(self.textChangedSignal.emit)
        root.addWidget(self.editor, 3)

        # ── Find bar (hidden by default) ───────────────────────────
        self._find_bar = self._build_find_bar()
        root.addWidget(self._find_bar)
        self._find_bar.setVisible(False)

        # ── Parser output (always visible like DV) ─────────────────
        pout_lbl = QLabel("Parser output:")
        pout_lbl.setStyleSheet(
            f"color: {UI.blue}; font-weight: bold; font-size: 9pt;"
            f" background: {UI.hover}; padding: 3px 8px;"
            f" border-top: 1px solid {UI.border}; border-bottom: 1px solid {UI.border};")
        root.addWidget(pout_lbl)

        self.parser_output = QPlainTextEdit()
        self.parser_output.setReadOnly(True)
        self.parser_output.setMaximumHeight(110)
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        mono.setPointSize(9)
        self.parser_output.setFont(mono)
        self.parser_output.setStyleSheet(
            "QPlainTextEdit { background: #FFFFFF; color: #333;"
            f" border: 1px solid {UI.border}; padding: 4px; }}")
        root.addWidget(self.parser_output, 1)

        # ── Parse / OK / Cancel button row owned by the dialog wrapper.
        # When embedded in a non-dialog host, expose a small Parse button row.
        self._parse_row = QHBoxLayout()
        self._parse_row.setContentsMargins(4, 4, 4, 4)
        parse_btn = _ribbon_tool_button(
            "Parse", "Validate the expression (also Alt+P)",
            on_click=self.parse_now)
        parse_btn.setMinimumWidth(80)
        parse_btn.setStyleSheet(
            f"QToolButton {{ background: {UI.blue}; color: white;"
            " border: 1px solid #1A4F8B; border-radius: 3px;"
            " font-weight: bold; padding: 4px 14px; }"
            " QToolButton:hover { background: #3A6FC0; }")
        self._parse_row.addWidget(parse_btn)
        self._parse_row.addStretch(1)
        parse_row_widget = QWidget()
        parse_row_widget.setLayout(self._parse_row)
        root.addWidget(parse_row_widget)
        self._shortcuts.append(QShortcut(
            QKeySequence("Alt+P"), self, activated=self.parse_now))

    # ───────────────────────────────────────────────────────────────
    # Functions menu — categorised by group, plus a flat Recently Used
    # ───────────────────────────────────────────────────────────────
    def _build_functions_menu(self) -> QMenu:
        """Group the helper functions by their docstring category headers."""
        menu = studio_menu(parent=self)
        # Retain wrappers for Qt-owned submenus; styling/redrawing a rediscovered
        # wrapper after its native menu was collected broke Functions on reopen.
        self._function_submenus = []

        def group_menu(title):
            child = studio_menu(parent=menu)
            child.setTitle(title)
            self._function_submenus.append(child)
            menu.addMenu(child)
            return child
        try:
            from azeo_control_trainer.core.strategy.blocks.script_functions import (
                get_script_functions,
            )
            funcs = get_script_functions()
        except Exception:
            funcs = {}

        # Curated category groupings (mirror the comment banners in
        # script_functions.py). Anything not categorised lands under "Other".
        groups: dict[str, list[str]] = {
            "Process Control":  ["clamp", "scale", "deadband", "hysteresis",
                                  "lerp", "normalize", "denormalize", "remap"],
            "Signal Processing": ["ewma", "rate_of_change", "integrate",
                                   "rate_limit"],
            "PLC Logic":         ["rising_edge", "falling_edge", "sr_latch",
                                   "timer_on", "timer_off", "counter"],
            "Alarm":             ["alarm_hi_lo", "alarm_rate", "alarm_dev"],
            "Selection":         ["select_hi", "select_lo", "select_mid",
                                   "first_good"],
            "Valve":             ["valve_eq_pct", "valve_quick_open",
                                   "valve_installed"],
            "Bit":               ["get_bit", "set_bit", "clear_bit",
                                   "toggle_bit", "test_bits"],
            "Unit Conversion":   ["temp_convert", "press_convert",
                                   "flow_sq_root"],
            "Timing":            ["pulse", "blink", "ramp"],
            "Data":              ["buffer_push", "peak_detect", "time_avg"],
            "Statistical":       ["avg", "std_dev", "median"],
            "Safety / Voting":   ["vote_2oo3", "vote_1oo2", "watchdog"],
            "Math":              ["poly", "heat_duty"],
        }
        seen: set[str] = set()
        for cat, names in groups.items():
            sub = group_menu(cat)
            for n in names:
                if n in funcs:
                    seen.add(n)
                    sub.addAction(n,
                        lambda _checked=False, x=n: self._insert_function(x))
        # Plugin-registered helpers + anything we missed land under "Other"
        leftover = sorted(set(funcs) - seen)
        if leftover:
            sub = group_menu("Other")
            for n in leftover:
                sub.addAction(n,
                    lambda _checked=False, x=n: self._insert_function(x))
        return menu

    def _build_recent_menu(self) -> QMenu:
        menu = studio_menu(parent=self)
        menu.aboutToShow.connect(lambda: self._refresh_recent_menu(menu))
        return menu

    def _refresh_recent_menu(self, menu: QMenu):
        menu.clear()
        if not self._recent_funcs:
            act = menu.addAction("(no functions used yet)")
            act.setEnabled(False)
            return
        for n in self._recent_funcs[:12]:
            menu.addAction(n,
                lambda _checked=False, x=n: self._insert_function(x))

    # ───────────────────────────────────────────────────────────────
    # Editor mutation helpers
    # ───────────────────────────────────────────────────────────────
    def _insert(self, text: str):
        """Insert ``text`` at the current cursor; space-pad operators."""
        cur = self.editor.textCursor()
        # Word-like ops get spaces; pure symbols don't
        if text.isalpha():
            cur.insertText(f" {text} ")
        elif text in ("AND", "OR", "NOT"):
            cur.insertText(f" {text} ")
        else:
            cur.insertText(text)
        self.editor.setFocus()

    def _insert_function(self, name: str):
        cur = self.editor.textCursor()
        cur.insertText(f"{name}()")
        cur.movePosition(QTextCursor.Left)
        self.editor.setTextCursor(cur)
        self.editor.setFocus()
        # Bump recently-used list
        if name in self._recent_funcs:
            self._recent_funcs.remove(name)
        self._recent_funcs.insert(0, name)
        self._recent_funcs = self._recent_funcs[:12]

    # ───── Clipboard ─────
    def _cmd_paste(self): self.editor.paste()
    def _cmd_cut(self):   self.editor.cut()
    def _cmd_copy(self):  self.editor.copy()

    # ───── Save / Open ─────
    def _cmd_save(self):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "Save expression", "", "Expression (*.txt *.py);;All files (*)")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self.editor.toPlainText())
                self._set_parser_msg(f"Saved to {path}", ok=True)
            except Exception as e:
                self._set_parser_msg(f"Save failed: {e}", ok=False)

    def _cmd_open(self):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Open expression", "", "Expression (*.txt *.py);;All files (*)")
        if path:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.editor.setPlainText(f.read())
                self._set_parser_msg(f"Opened {path}", ok=True)
            except Exception as e:
                self._set_parser_msg(f"Open failed: {e}", ok=False)

    # ───── DCS Functions ─────
    def _pick_internal_param(self):
        tag = self._ask_tag("Pick internal parameter (read)")
        if tag:
            self._insert(f"get_tag('{tag}')")

    def _pick_external_param(self):
        tag = self._ask_tag("Pick external parameter (write target)")
        if tag:
            cur = self.editor.textCursor()
            cur.insertText(f"set_tag('{tag}', )")
            cur.movePosition(QTextCursor.Left)
            self.editor.setTextCursor(cur)

    def _pick_alias(self):
        name, ok = QInputDialog.getText(self, "Insert Alias",
            "Alias / variable name:")
        if ok and name:
            self._insert(name)

    def _pick_named_state(self):
        items = ["AUTO", "MANUAL", "CAS", "RCAS", "ROUT", "OOS",
                  "MAN", "LO", "IMAN"]
        item, ok = QInputDialog.getItem(self, "Insert Named State",
            "Named state:", items, 0, False)
        if ok and item:
            self._insert(item)

    def _pick_insert(self):
        items = ["IN1", "IN2", "IN3", "IN4", "OUT1", "OUT2", "OUT3", "OUT4",
                  "dt", "time", "state", "True", "False", "None"]
        item, ok = QInputDialog.getItem(self, "Insert Symbol", "Insert:",
                                          items, 0, False)
        if ok and item:
            self._insert(item)

    def _ask_tag(self, title: str) -> str | None:
        """Open a tag picker against the store if available, else free-text."""
        tag_list: list[str] = []
        if self._store is not None:
            try:
                tag_list = sorted(self._store.get_all().keys())
            except Exception:
                tag_list = []
        if tag_list:
            tag, ok = QInputDialog.getItem(self, title, "Tag name:",
                                             tag_list, 0, True)
            if ok and tag:
                return tag.strip()
            return None
        # Fallback: free-text input
        tag, ok = QInputDialog.getText(self, title, "Tag name:")
        return tag.strip() if (ok and tag) else None

    # ───── Find / Replace / Go To ─────
    def _build_find_bar(self) -> QWidget:
        bar = QFrame()
        bar.setStyleSheet("QFrame { background: #ECEEF3;"
                           " border-top: 1px solid #C0C5D0; }")
        row = QHBoxLayout(bar)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(4)
        row.addWidget(QLabel("Find:"))
        self._find_input = QLineEdit()
        self._find_input.setStyleSheet(
            f"QLineEdit {{ background: white; border: 1px solid {UI.border};"
            " border-radius: 2px; padding: 2px 4px; }")
        row.addWidget(self._find_input, 1)
        row.addWidget(_ribbon_tool_button("Next",  "Find next (F3)",
            on_click=lambda: self._find_text(True)))
        row.addWidget(_ribbon_tool_button("Prev",  "Find previous",
            on_click=lambda: self._find_text(False)))
        row.addWidget(QLabel("Replace:"))
        self._repl_input = QLineEdit()
        self._repl_input.setStyleSheet(self._find_input.styleSheet())
        row.addWidget(self._repl_input, 1)
        row.addWidget(_ribbon_tool_button("Replace", "Replace selection + find next",
            on_click=self._replace_one))
        row.addWidget(_ribbon_tool_button("All",     "Replace all",
            on_click=self._replace_all))
        row.addWidget(_ribbon_tool_button("Close",   "Close (Esc)",
            on_click=lambda: self._find_bar.setVisible(False)))
        self._shortcuts.extend((
            QShortcut(QKeySequence("F3"), self,
                      activated=lambda: self._find_text(True)),
            QShortcut(QKeySequence("Escape"), self,
                      activated=lambda: self._find_bar.setVisible(False)),
        ))
        return bar

    def _show_find(self):
        self._find_bar.setVisible(True)
        self._find_input.setFocus()
        self._find_input.selectAll()

    def _show_replace(self):
        self._show_find()
        self._repl_input.setFocus()

    def _show_goto(self):
        line, ok = QInputDialog.getInt(self, "Go To Line", "Line number:",
                                         1, 1, self.editor.blockCount(), 1)
        if not ok:
            return
        block = self.editor.document().findBlockByNumber(line - 1)
        if block.isValid():
            cur = QTextCursor(block)
            self.editor.setTextCursor(cur)
            self.editor.setFocus()

    def _find_text(self, forward: bool):
        from PySide6.QtGui import QTextDocument
        needle = self._find_input.text()
        if not needle:
            return
        opts = (QTextDocument.FindFlag(0) if forward
                else QTextDocument.FindBackward)
        if not self.editor.find(needle, opts):
            # wrap around
            cur = self.editor.textCursor()
            cur.movePosition(QTextCursor.Start if forward else QTextCursor.End)
            self.editor.setTextCursor(cur)
            self.editor.find(needle, opts)

    def _replace_one(self):
        cur = self.editor.textCursor()
        if cur.hasSelection() and cur.selectedText() == self._find_input.text():
            cur.insertText(self._repl_input.text())
        self._find_text(True)

    def _replace_all(self):
        needle = self._find_input.text()
        if not needle:
            return
        replacement = self._repl_input.text()
        text = self.editor.toPlainText()
        new = text.replace(needle, replacement)
        self.editor.setPlainText(new)
        self._set_parser_msg(
            f"Replaced {text.count(needle)} occurrences of {needle!r}",
            ok=True)

    # ───── Help ─────
    def _show_help(self):
        # Modeless reference dialog — tree + searchable rich-text panel.
        # Stays open beside the editor so the operator can keep it as a
        # working reference while writing the expression.
        from .expression_help_dialog import open_expression_help
        open_expression_help(self.window())

    # ───── Parse ─────
    def parse_now(self):
        text = self.editor.toPlainText().strip()
        try:
            from azeo_control_trainer.core.strategy.blocks.action_block import (
                compile_action_source,
            )
            mode = 1 if self._mode == "expression" else 2
            kind, _parsed = compile_action_source(text, mode)
            language = {
                "expression": "restricted expression",
                "iec_st": "restricted IEC ST",
                "azeo_python": "Azeo Python Extension",
            }[kind]
            self._set_parser_msg(
                f"OK — {language} parses cleanly ({len(text)} chars).",
                ok=True)
            self.parseCompleted.emit(True, "ok")
            return True
        except SyntaxError as e:
            self._set_parser_msg(
                f"ERROR: Syntax error at line {e.lineno}, col {e.offset}\n"
                f"  {e.msg}\n"
                f"  Offending line: {(e.text or '').rstrip()}",
                ok=False)
            self.parseCompleted.emit(False, str(e))
        except Exception as e:
            self._set_parser_msg(f"ERROR: {e}", ok=False)
            self.parseCompleted.emit(False, str(e))
        return False

    def _set_parser_msg(self, msg: str, ok: bool):
        self.parser_output.setPlainText(msg)
        color = "#0E7D4B" if ok else "#B71C1C"
        self.parser_output.setStyleSheet(
            f"QPlainTextEdit {{ background: #FFFFFF; color: {color};"
            f" border: 1px solid #B0B8C8; padding: 4px; }}")

    # ----- Public API -----
    def text(self) -> str:
        return self.editor.toPlainText()

    def set_text(self, text: str):
        self.editor.setPlainText(text)

    def set_mode(self, mode: str):
        self._mode = mode
        label = ("IEC ST / EXPRESSION" if mode == "expression"
                 else "AZEO PYTHON EXTENSION")
        self._mode_lbl.setText(f"Mode: {label}")


# ───────────────────────────────────────────────────────────────────────
# Dialog wrapper — OK / Cancel
# ───────────────────────────────────────────────────────────────────────
class RibbonExpressionEditorDialog(QDialog):
    """Top-level dialog hosting :class:`RibbonExpressionEditor`."""

    textApplied = Signal(str, int)   # (text, script_mode 1=expr 2=script)

    def __init__(self, initial_text: str = "", mode: str = "expression",
                 store=None, title: str = "Expression Editor",
                 parent=None):
        super().__init__(parent, Qt.Window | Qt.WindowStaysOnTopHint)
        self.setWindowTitle(title)
        self.resize(900, 620)
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(4)
        self.editor = RibbonExpressionEditor(
            initial_text=initial_text, mode=mode, store=store)
        v.addWidget(self.editor, 1)
        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

    def _on_accept(self):
        # Invalid source remains in the editor instead of being deferred to
        # scan 1, which makes Parse an authoring gate rather than decoration.
        if not self.editor.parse_now():
            return
        text = self.editor.text()
        mode_int = 1 if self.editor._mode == "expression" else 2
        self.textApplied.emit(text, mode_int)
        self.accept()

    def text(self) -> str:
        return self.editor.text()
