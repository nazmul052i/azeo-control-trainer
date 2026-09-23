r"""Per-block documentation dialog — DCS-standard block reference page.

A modeless, top-level dialog showing the canonical reference for one
block type. Read directly from the class via :mod:`inspect`, so a
plugin-shipped block needs no extra metadata to appear here.

Sections:

  * Overview     — display name, category, one-line description
  * Description  — full class docstring (rendered as monospace with
                   wrap), so any narrative the block author put in
                   the docstring shows up
  * Terminals    — table of inputs + outputs (name / direction /
                   type / BKCAL flag / description)
  * Parameters   — table from get_config_schema() (key / type /
                   default / description)
  * See also     — links to other blocks the docstring mentions

The dialog is reusable: passing a different ``block`` swaps content.
A small "See also" anchor handler navigates to another block-type's
documentation in the same window.

Public API
----------
``BlockDocumentationDialog(block_or_class, parent=None)``
    Opens documentation for either a FunctionBlock *instance* (uses
    its config + current terminal layout) or a FunctionBlock
    *class* (cleaner reference, no instance state).

``open_block_documentation(block_or_class, parent=None)``
    Singleton helper — reuses the same dialog across calls so the
    operator can keep one reference window open beside the canvas.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

import html
import inspect
import re
from typing import Any

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QPushButton,
    QTextBrowser, QVBoxLayout,
)


_CSS = """
<style>
  body  { font-family: 'Segoe UI', sans-serif; color: #0E3260; }
  h2    { color: #0E3260; border-bottom: 2px solid #5B8BA0;
          padding-bottom: 4px; margin-top: 0; }
  h3    { color: #1A4F8B; margin-top: 14px; margin-bottom: 4px; }
  p.lead { color: #555; font-size: 10pt; margin-top: 4px; }
  code  { background: #ECEFF4; padding: 1px 4px;
          font-family: Consolas, 'Courier New', monospace;
          color: #7B2D8C; border-radius: 2px; }
  pre   { background: #FAFBFD; border: 1px solid #B0B8C8;
          padding: 8px 12px; border-radius: 3px;
          font-family: Consolas, 'Courier New', monospace;
          font-size: 9pt; color: #0E3260; white-space: pre-wrap; }
  table { border-collapse: collapse; margin: 8px 0; width: 100%; }
  th    { background: #E3F2FD; color: #0E3260; font-weight: bold;
          padding: 4px 10px; border: 1px solid #B0B8C8; text-align: left; }
  td    { padding: 3px 10px; border: 1px solid #B0B8C8;
          font-family: Consolas, 'Courier New', monospace; font-size: 9pt;
          vertical-align: top; }
  td.t  { font-family: Consolas; color: #7B2D8C; }
  td.d  { font-family: 'Segoe UI', sans-serif; color: #444; }
  .badge { display: inline-block; padding: 1px 6px; border-radius: 2px;
           color: white; font-weight: bold; font-size: 9pt;
           font-family: Consolas; }
  .b-in   { background: #2E6E8A; }
  .b-out  { background: #3A8F4F; }
  .b-bkcal{ background: #B22222; }
  .info   { background: #E3F2FD; border-left: 3px solid #1976D2;
            padding: 6px 10px; margin: 8px 0; color: #0E3260; }
</style>
"""


# Category descriptions / colour hint
_CAT_BADGE = {
    "IO":        ("Input / Output",        "#3A8F4F"),
    "CONTROL":   ("Control",               "#1A4F8B"),
    "APC":       ("Advanced Process Control", "#7B2D8C"),
    "MATH":      ("Math",                  "#5A6B7D"),
    "SIGNAL":    ("Signal Processing",     "#2E6E8A"),
    "LOGIC":     ("Logic",                 "#8A6B1F"),
    "SAFETY":    ("Safety",                "#A4242F"),
    "SFC":       ("Sequential Function Chart", "#9B5418"),
    "COMPOSITE": ("Composite",             "#5B3A7E"),
}


def _is_class(obj) -> bool:
    return inspect.isclass(obj)


def _render(block_or_class) -> tuple[str, str, str]:
    """Return (window_title, html_body, block_type) for the given block/class."""
    if _is_class(block_or_class):
        cls = block_or_class
        # Temporary instance so we can read terminals + default config
        try:
            instance = cls("_doc")
        except Exception:
            instance = None
    else:
        instance = block_or_class
        cls = type(instance)

    bt = getattr(cls, "block_type", "?")
    display_name = getattr(cls, "display_name", cls.__name__)
    description = getattr(cls, "description", "")
    category = getattr(cls, "category", None)
    cat_value = category.value if category is not None else ""
    cat_label, cat_color = _CAT_BADGE.get(cat_value, (cat_value, "#666"))

    # ── Overview ─────────────────────────────────────────────────
    parts = [
        f"<h2>{html.escape(display_name)} "
        f"<span style='font-family:Consolas;color:#7B2D8C;font-size:11pt;'>"
        f"({html.escape(bt)})</span></h2>",
        f"<p class='lead'>"
        f"<span class='badge' style='background:{cat_color};'>"
        f"{html.escape(cat_label or 'Block')}</span> "
        f"&nbsp;{html.escape(description)}</p>",
    ]

    # ── Description (class docstring) ─────────────────────────────
    doc = inspect.getdoc(cls) or ""
    if doc:
        parts.append("<h3>Description</h3>")
        # Linkify other block names mentioned in the docstring — naive
        # match of words in ALL_CAPS that look like block_types.
        body = html.escape(doc)
        body = re.sub(r"\b([A-Z_][A-Z0-9_]{2,})\b",
                       lambda m: (f"<a href='block:{m.group(1)}'>"
                                  f"<code>{m.group(1)}</code></a>"
                                  if m.group(1) != bt else f"<code>{m.group(1)}</code>"),
                       body)
        parts.append(f"<pre>{body}</pre>")

    # ── Terminals ────────────────────────────────────────────────
    if instance is not None:
        if instance.inputs or instance.outputs:
            parts.append("<h3>Terminals</h3>")
            rows = []
            for name, t in instance.inputs.items():
                badge = "<span class='badge b-in'>IN</span>"
                if t.is_bkcal:
                    badge += " <span class='badge b-bkcal'>BKCAL</span>"
                rows.append(
                    f"<tr><td>{html.escape(name)}</td><td>{badge}</td>"
                    f"<td class='t'>{t.data_type.value}</td>"
                    f"<td class='d'>{html.escape(t.description or '')}</td></tr>")
            for name, t in instance.outputs.items():
                badge = "<span class='badge b-out'>OUT</span>"
                if t.is_bkcal:
                    badge += " <span class='badge b-bkcal'>BKCAL</span>"
                rows.append(
                    f"<tr><td>{html.escape(name)}</td><td>{badge}</td>"
                    f"<td class='t'>{t.data_type.value}</td>"
                    f"<td class='d'>{html.escape(t.description or '')}</td></tr>")
            parts.append(
                "<table><tr><th>Name</th><th>Direction</th>"
                "<th>Type</th><th>Description</th></tr>"
                + "".join(rows) + "</table>")

    # ── Parameters ───────────────────────────────────────────────
    if instance is not None:
        try:
            schema = instance.get_config_schema() or {}
        except Exception:
            schema = {}
        if schema:
            parts.append("<h3>Configuration Parameters</h3>")
            rows = []
            for key, meta in schema.items():
                if isinstance(meta, (tuple, list)) and len(meta) >= 3:
                    t, default, desc = meta[0], meta[1], meta[2]
                    tname = getattr(t, "__name__", str(t))
                else:
                    tname, default, desc = "?", meta, ""
                cur = ""
                if not _is_class(block_or_class):
                    actual = instance.config.params.get(key, default)
                    if actual != default:
                        cur = (f" &nbsp;<span style='color:#1A4F8B;'>"
                                f"(current: {html.escape(repr(actual))})</span>")
                rows.append(
                    f"<tr><td>{html.escape(key)}</td>"
                    f"<td class='t'>{tname}</td>"
                    f"<td>{html.escape(repr(default))}</td>"
                    f"<td class='d'>{html.escape(str(desc))}{cur}</td></tr>")
            parts.append(
                "<table><tr><th>Key</th><th>Type</th>"
                "<th>Default</th><th>Description</th></tr>"
                + "".join(rows) + "</table>")

    # ── Source file (helps engineers find the block) ──────────────
    try:
        src_file = inspect.getfile(cls)
        parts.append(
            f"<p class='lead'><b>Source:</b> "
            f"<code>{html.escape(src_file)}</code></p>")
    except (TypeError, OSError):
        pass

    title = f"Block Help — {display_name} ({bt})"
    return title, _CSS + "<body>" + "\n".join(parts) + "</body>", bt


class BlockDocumentationDialog(QDialog):
    """Modeless block-reference dialog. Pass a FunctionBlock instance or class."""

    _LAST_GEOMETRY = None    # restore size across opens

    blockTypeRequested = Signal(str)   # for "See also" anchor follow-ups

    def __init__(self, block_or_class, parent=None):
        super().__init__(parent, Qt.Window)
        self._build()
        self._history: list[Any] = []
        self._history_pos: int = -1
        self.show_block(block_or_class)
        if BlockDocumentationDialog._LAST_GEOMETRY is not None:
            self.restoreGeometry(BlockDocumentationDialog._LAST_GEOMETRY)

    def closeEvent(self, ev):
        BlockDocumentationDialog._LAST_GEOMETRY = self.saveGeometry()
        super().closeEvent(ev)

    def showEvent(self, ev):
        super().showEvent(ev)
        from azeo_control_trainer.core.presentation.dialog_layout import fit_dialog_to_screen
        fit_dialog_to_screen(self)

    # ─────────────────────────────────────────────────────────────
    def _build(self):
        self.resize(820, 660)
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        # Nav bar — back / forward / close
        nav = QHBoxLayout()
        self._btn_back = QPushButton("◀")
        self._btn_back.setToolTip("Back")
        self._btn_back.setEnabled(False)
        self._btn_back.clicked.connect(self._on_back)
        self._btn_fwd = QPushButton("▶")
        self._btn_fwd.setToolTip("Forward")
        self._btn_fwd.setEnabled(False)
        self._btn_fwd.clicked.connect(self._on_forward)
        for b in (self._btn_back, self._btn_fwd):
            b.setStyleSheet(
                f"QPushButton {{ background: #FAFBFD; border: 1px solid {UI.border};"
                " border-radius: 2px; padding: 2px 10px; font-size: 11pt; }"
                f" QPushButton:hover {{ background: {UI.hover}; }}"
                " QPushButton:disabled { color: #C8CCD3; }")
        nav.addWidget(self._btn_back)
        nav.addWidget(self._btn_fwd)
        nav.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(
            f"QPushButton {{ background: {UI.blue}; color: white;"
            " border: none; border-radius: 3px; padding: 4px 18px;"
            " font-weight: bold; }"
            " QPushButton:hover { background: #1A4F8B; }")
        close_btn.clicked.connect(self.close)
        nav.addWidget(close_btn)
        v.addLayout(nav)

        # Browser
        self._browser = QTextBrowser()
        self._browser.setStyleSheet(
            f"QTextBrowser {{ background: white; border: 1px solid {UI.border};"
            " padding: 8px; }")
        self._browser.setOpenLinks(False)
        self._browser.anchorClicked.connect(self._on_anchor)
        v.addWidget(self._browser, 1)

    # ─────────────────────────────────────────────────────────────
    def show_block(self, block_or_class):
        title, body, bt = _render(block_or_class)
        self.setWindowTitle(title)
        self._browser.setHtml(body)
        # Push onto history (trim forward branch on new push)
        if self._history_pos < len(self._history) - 1:
            self._history = self._history[: self._history_pos + 1]
        self._history.append(block_or_class)
        self._history_pos = len(self._history) - 1
        self._update_nav()

    def _update_nav(self):
        self._btn_back.setEnabled(self._history_pos > 0)
        self._btn_fwd.setEnabled(self._history_pos < len(self._history) - 1)

    def _on_back(self):
        if self._history_pos > 0:
            self._history_pos -= 1
            self._render_index(self._history_pos)

    def _on_forward(self):
        if self._history_pos < len(self._history) - 1:
            self._history_pos += 1
            self._render_index(self._history_pos)

    def _render_index(self, idx: int):
        item = self._history[idx]
        title, body, _ = _render(item)
        self.setWindowTitle(title)
        self._browser.setHtml(body)
        self._update_nav()

    def _on_anchor(self, url: QUrl):
        s = url.toString()
        if not s.startswith("block:"):
            return
        bt = s[len("block:"):]
        from azeo_control_trainer.core.strategy.model.block_registry import registry
        cls = registry.get(bt)
        if cls is None:
            return
        self.blockTypeRequested.emit(bt)
        self.show_block(cls)


# ───────────────────────────────────────────────────────────────────────
# Singleton helper
# ───────────────────────────────────────────────────────────────────────

def open_block_documentation(block_or_class, parent=None) -> BlockDocumentationDialog:
    """Return (or create) the singleton documentation dialog and switch
    its view to ``block_or_class``."""
    inst = getattr(open_block_documentation, "_instance", None)
    if inst is None:
        inst = BlockDocumentationDialog(block_or_class, parent=parent)
        open_block_documentation._instance = inst
        inst.destroyed.connect(
            lambda *_: setattr(open_block_documentation, "_instance", None))
    else:
        inst.show_block(block_or_class)
    inst.show()
    inst.raise_()
    inst.activateWindow()
    return inst
