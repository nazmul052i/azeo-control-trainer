r"""Per-block diagnostics dialog — DCS-standard status / metrics / terminals view.

Distinct from :mod:`diagnostics_dialog` (that one is system-level —
controller uptime, total scan time, block inventory). This dialog
shows everything a designer needs to understand the runtime state of
**one** block:

  * Status header — name, type, status enum, exec order #, bypass,
    any-forced flags
  * Scan metrics — last / avg / max execute() duration in µs, total
    execution count (populated by the strategy runtime)
  * Terminals table — every input + output with current value, type,
    forced flag, connected flag
  * Errors — last error message and error count (when the block
    exposes them, e.g. ACT block)
  * Config dump — read-only view of the block's config.params

Refreshes at 1 Hz; safe to leave open beside the canvas.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget, QPlainTextEdit,
)


_STATUS_TINT = {
    "GOOD":      "#2D8E3C",
    "BAD":       "#C62828",
    "UNCERTAIN": "#FF8F00",
}


class BlockDiagnosticsDialog(QDialog):
    """Live diagnostics view for one FunctionBlock."""

    def __init__(self, block, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._block = block
        self.setWindowTitle(
            f"Diagnostics — {block.instance_name}  ({block.block_type})")
        self.resize(680, 600)
        self._build()
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._refresh()

    def closeEvent(self, ev):
        self._timer.stop()
        super().closeEvent(ev)

    def done(self, result):
        # Close buttons and Escape finish a QDialog without closeEvent.
        self._timer.stop()
        super().done(result)

    # ─────────────────────────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ── Status header ─────────────────────────────────────────
        hdr = QGroupBox("Status")
        hdr.setStyleSheet(self._group_style())
        hl = QGridLayout(hdr)
        hl.setSpacing(6)
        self._lbl_status = self._mk_value("GOOD")
        self._lbl_exec_order = self._mk_value("—")
        self._lbl_bypass = self._mk_value("No")
        self._lbl_forced = self._mk_value("No")
        hl.addWidget(QLabel("Status:"),       0, 0); hl.addWidget(self._lbl_status,     0, 1)
        hl.addWidget(QLabel("Bypassed:"),     1, 0); hl.addWidget(self._lbl_bypass,     1, 1)
        hl.addWidget(QLabel("Exec order #:"), 0, 2); hl.addWidget(self._lbl_exec_order, 0, 3)
        hl.addWidget(QLabel("Forced:"),       1, 2); hl.addWidget(self._lbl_forced,     1, 3)
        root.addWidget(hdr)

        # ── Scan metrics ──────────────────────────────────────────
        sm = QGroupBox("Scan metrics")
        sm.setStyleSheet(self._group_style())
        sl = QGridLayout(sm); sl.setSpacing(6)
        self._lbl_last = self._mk_value("0 µs")
        self._lbl_avg = self._mk_value("0 µs")
        self._lbl_max = self._mk_value("0 µs")
        self._lbl_count = self._mk_value("0")
        sl.addWidget(QLabel("Last:"),  0, 0); sl.addWidget(self._lbl_last,  0, 1)
        sl.addWidget(QLabel("Avg:"),   0, 2); sl.addWidget(self._lbl_avg,   0, 3)
        sl.addWidget(QLabel("Max:"),   1, 0); sl.addWidget(self._lbl_max,   1, 1)
        sl.addWidget(QLabel("Count:"), 1, 2); sl.addWidget(self._lbl_count, 1, 3)
        root.addWidget(sm)

        # ── Terminals ─────────────────────────────────────────────
        tg = QGroupBox("Terminals")
        tg.setStyleSheet(self._group_style())
        tl = QVBoxLayout(tg)
        self._terms = QTableWidget(0, 6)
        self._terms.setHorizontalHeaderLabels(
            ["Name", "Dir", "Type", "Value", "Forced", "Connected"])
        self._terms.verticalHeader().setVisible(False)
        self._terms.setEditTriggers(QTableWidget.NoEditTriggers)
        self._terms.setSelectionBehavior(QTableWidget.SelectRows)
        self._terms.setStyleSheet(
            f"QTableWidget {{ background: white; border: 1px solid {UI.border};"
            " font-family: Consolas; font-size: 9pt; }")
        hh = self._terms.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 4, 5):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        tl.addWidget(self._terms)
        root.addWidget(tg, 2)

        # ── Errors (only meaningful for some blocks) ──────────────
        eg = QGroupBox("Errors")
        eg.setStyleSheet(self._group_style())
        el = QGridLayout(eg); el.setSpacing(6)
        self._lbl_err_count = self._mk_value("0")
        self._lbl_err_msg = QLabel("(no errors)")
        self._lbl_err_msg.setWordWrap(True)
        self._lbl_err_msg.setStyleSheet(
            "QLabel { font-family: Consolas; padding: 2px 6px; }")
        el.addWidget(QLabel("Count:"),        0, 0); el.addWidget(self._lbl_err_count, 0, 1)
        el.addWidget(QLabel("Last error:"),   1, 0); el.addWidget(self._lbl_err_msg,   1, 1)
        root.addWidget(eg)

        # ── Config dump ───────────────────────────────────────────
        cg = QGroupBox("Config")
        cg.setStyleSheet(self._group_style())
        cl = QVBoxLayout(cg)
        self._cfg = QPlainTextEdit()
        self._cfg.setReadOnly(True)
        self._cfg.setMaximumHeight(140)
        self._cfg.setStyleSheet(
            f"QPlainTextEdit {{ background: white; border: 1px solid {UI.border};"
            " font-family: Consolas; font-size: 9pt; padding: 4px; }")
        cl.addWidget(self._cfg)
        root.addWidget(cg, 1)

        bbox = QDialogButtonBox(QDialogButtonBox.Close)
        bbox.rejected.connect(self.reject)
        bbox.accepted.connect(self.accept)
        root.addWidget(bbox)

    @staticmethod
    def _group_style() -> str:
        return ("QGroupBox { background: #FAFBFD; border: 1px solid #B0B8C8;"
                " border-radius: 3px; margin-top: 8px; padding: 8px; }"
                " QGroupBox::title { subcontrol-origin: margin; left: 8px;"
                " color: #0E3260; font-weight: bold; }")

    @staticmethod
    def _mk_value(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "QLabel { background: white; padding: 2px 6px;"
            f" border: 1px solid {UI.border}; font-family: Consolas;"
            f" color: {UI.blue}; min-width: 90px; }}")
        return lbl

    # ─────────────────────────────────────────────────────────────
    def _refresh(self):
        blk = self._block
        status_name = getattr(blk.status, "name", str(blk.status))
        self._lbl_status.setText(status_name)
        tint = _STATUS_TINT.get(status_name, "#0E3260")
        self._lbl_status.setStyleSheet(
            f"QLabel {{ background: white; padding: 2px 6px;"
            f" border: 1px solid {tint}; color: {tint};"
            f" font-family: Consolas; font-weight: bold; min-width: 90px; }}")
        self._lbl_exec_order.setText(
            str(blk._exec_order) if getattr(blk, "_exec_order", -1) >= 0 else "—")
        self._lbl_bypass.setText("Yes" if blk.bypassed else "No")
        forced = blk.any_forced()
        self._lbl_forced.setText(
            f"Yes ({len(blk.forced_terminals())})" if forced else "No")

        self._lbl_last.setText(f"{blk._last_exec_us:.1f} µs")
        self._lbl_avg.setText(f"{blk.avg_exec_us:.1f} µs")
        self._lbl_max.setText(f"{blk._max_exec_us:.1f} µs")
        self._lbl_count.setText(f"{blk._exec_count:,d}")

        rows: list[tuple[str, str, str, str, bool, bool]] = []
        for n, t in blk.inputs.items():
            rows.append((n, "IN",  t.data_type.value, str(t.value),
                          t.forced, t.connected))
        for n, t in blk.outputs.items():
            rows.append((n, "OUT", t.data_type.value, str(t.value),
                          t.forced, t.connected))
        self._terms.setRowCount(len(rows))
        for r, (name, direction, dtype, value, forced, connected) in enumerate(rows):
            self._terms.setItem(r, 0, QTableWidgetItem(name))
            self._terms.setItem(r, 1, QTableWidgetItem(direction))
            self._terms.setItem(r, 2, QTableWidgetItem(dtype))
            self._terms.setItem(r, 3, QTableWidgetItem(value))
            f_item = QTableWidgetItem("●" if forced else "")
            if forced:
                f_item.setForeground(QColor("#C62828"))
            self._terms.setItem(r, 4, f_item)
            self._terms.setItem(r, 5, QTableWidgetItem("●" if connected else ""))

        err_count = getattr(blk, "_error_count", 0)
        err_msg = ""
        if hasattr(blk, "get_script_error"):
            try: err_msg = blk.get_script_error()
            except Exception: err_msg = ""
        if not err_msg:
            err_msg = getattr(blk, "_last_error", "")
        self._lbl_err_count.setText(str(err_count))
        self._lbl_err_msg.setText(err_msg if err_msg else "(no errors)")

        if blk.config and blk.config.params:
            lines = []
            for k, v in blk.config.params.items():
                lines.append(f"{k:<24} = {v!r}")
            self._cfg.setPlainText("\n".join(lines))
        else:
            self._cfg.setPlainText("(no config)")
