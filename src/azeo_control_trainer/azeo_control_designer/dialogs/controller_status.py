"""Controller Status dialog -- DCS-style controller module viewer.

Shows all strategy modules managed by the controller (CTRL-1) with
live scan statistics.  Supports per-module:
  - Go Online / Go Offline
  - Re-Download (offline -> recompile -> online)
  - Validate (run pre-download checks)
  - Compile (dry-run compile, report errors)
  - Download All / Offline All (bulk actions)
  - Modified indicator (edits since last download)
  - Last download timestamp

Non-modal, auto-refreshes at 1-second intervals.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI
from azeo_control_trainer.core.presentation.engineering_dialog import (
    Command, ENGINEERING_QSS, EngineeringMenus, button_command, polish_dialog,
)
from azeo_control_trainer.core.presentation.configuration_chrome import icon
from weakref import ref
from shiboken6 import isValid

import logging
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout,
)

log = logging.getLogger("strategy.controller")

# ── States ──
_ACTIVE = "ACTIVE"
_INACTIVE = "INACTIVE"
_NOT_LOADED = "NOT LOADED"

_STATE_COLORS = {
    _ACTIVE: "#2E7D32",
    _INACTIVE: "#78909C",
    _NOT_LOADED: "#9E9E9E",
}

# ── ISA-101 Silver styling ──
_DLG_STYLE = ENGINEERING_QSS

_BADGE_RUN = (
    "background: #E4F2EA;"
    "color: #246B46; font-weight: bold; font-size: 9pt;"
    "border: 1px solid #1B5E20; border-radius: 3px;"
    "padding: 2px 12px;"
)
_BADGE_STANDBY = (
    "background: #EEF1F5;"
    "color: #37474F; font-weight: bold; font-size: 9pt;"
    "border: 1px solid #78909C; border-radius: 3px;"
    "padding: 2px 12px;"
)

# Command colors are shared; module-state badges retain their status colors.



def _status_icon(color_hex: str, size: int = 12) -> QIcon:
    """Create a small colored circle icon for module status."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color_hex))
    p.setPen(QColor(color_hex).darker(130))
    p.drawEllipse(1, 1, size - 2, size - 2)
    p.end()
    return QIcon(pm)




# ── Column indices ──
COL_MODULE = 0
COL_STATE = 1
COL_MODIFIED = 2
COL_BLOCKS = 3
COL_SCANS = 4
COL_SCAN_MS = 5
COL_RATE = 6
COL_DOWNLOADED = 7
COL_ACTION = 8

_N_COLS = 9
_HEADERS = [
    "Module", "State", "Mod", "Blocks", "Scans",
    "Last Scan (ms)", "Rate (Hz)", "Downloaded", "",
]


class ControllerStatusDialog(QDialog):
    """Non-modal controller status dialog showing all strategy modules."""

    def __init__(self, designer_tab, store, parent=None):
        super().__init__(parent)
        node = getattr(store, "controller", None)
        self.setWindowTitle("Controller Status -- "
                            + (node.name if node else "CTRL-1"))
        self.setMinimumSize(850, 440)
        self.resize(960, 520)
        self.setStyleSheet(_DLG_STYLE)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        self._designer = designer_tab
        self._store = store
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)

        self._build_ui()
        polish_dialog(self)
        self.menus = EngineeringMenus(self, refresh=self._refresh,
            help_text="Select a module and use Module or its Actions menu to validate, compile or change its online state. "
            "Controller applies actions to all modules. Keylock and the existing download confirmations still apply.")
        self.menus.menu("&Controller", [button_command(self._btn_props),
                                       button_command(self._btn_download_all, mark="download"),
                                       button_command(self._btn_offline_all, mark="disconnect")])
        self._module_menu = self.menus.dynamic_menu("&Module", lambda: self._module_commands(self._table.currentRow()))
        self._module_menu.menuAction().setEnabled(False)
        self._table.itemSelectionChanged.connect(lambda: self._module_menu.menuAction().setEnabled(
            bool(self._table.selectedItems())))
        self.menus.table(self._table, self._module_commands, title="Control module")

    # ────────────────────────────────────────────── build UI
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # ── Title ──
        title = QLabel("Controller Status")
        title.setObjectName("dlgTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "Real-time view of all strategy modules executing on the "
            "controller.  Modules run concurrently in a single scan cycle."
        )
        subtitle.setObjectName("dlgSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # ── Warning banner (hidden when no modules active) ──
        self._warning_bar = QFrame()
        self._warning_bar.setObjectName("warningBar")
        wbar_layout = QHBoxLayout(self._warning_bar)
        wbar_layout.setContentsMargins(8, 4, 8, 4)
        wbar_layout.setSpacing(8)
        self._warning_icon = QLabel("!")
        self._warning_icon.setFixedWidth(18)
        self._warning_icon.setAlignment(Qt.AlignCenter)
        self._warning_icon.setStyleSheet(
            "background: #F57C00; color: white; font-weight: bold;"
            "font-size: 9pt; border-radius: 9px; border: none;"
        )
        wbar_layout.addWidget(self._warning_icon)
        self._warning_text = QLabel("")
        self._warning_text.setWordWrap(True)
        wbar_layout.addWidget(self._warning_text, 1)
        self._warning_bar.hide()
        layout.addWidget(self._warning_bar)

        # ── Controller header bar ──
        hbar = QFrame()
        hbar.setObjectName("headerBar")
        hbar_layout = QHBoxLayout(hbar)
        hbar_layout.setContentsMargins(8, 4, 8, 4)
        hbar_layout.setSpacing(16)

        # The PK node's identity: name, model, keylock, DST gauge —
        # refreshed live, red when over capacity.
        self._lbl_node = QLabel("CTRL-1")
        hbar_layout.addWidget(self._lbl_node)

        self._lbl_state = QLabel("STANDBY")
        self._lbl_state.setStyleSheet(_BADGE_STANDBY)
        self._lbl_state.setAlignment(Qt.AlignCenter)
        hbar_layout.addWidget(self._lbl_state)

        hbar_layout.addSpacing(20)
        self._lbl_modules = QLabel("Modules: 0 / 0")
        hbar_layout.addWidget(self._lbl_modules)

        self._lbl_blocks = QLabel("Blocks: 0")
        hbar_layout.addWidget(self._lbl_blocks)

        self._lbl_scan = QLabel("Scan: --- ms")
        hbar_layout.addWidget(self._lbl_scan)

        self._lbl_identity = QLabel("")
        hbar_layout.addWidget(self._lbl_identity)

        hbar_layout.addStretch()

        # The controller is *declared*, and this is where the declaration
        # is edited — the Azeo gesture of adding a controller node.
        self._btn_props = QPushButton("Controller Properties…")
        self._btn_props.setObjectName("bulkBtn")
        self._btn_props.clicked.connect(self._show_properties)
        commands = QHBoxLayout()
        commands.addWidget(self._btn_props)
        commands.addStretch()

        # ── Bulk action buttons ──
        self._btn_download_all = QPushButton("Download All")
        self._btn_download_all.setObjectName("bulkBtn")
        self._btn_download_all.setToolTip("Download all inactive modules")
        self._btn_download_all.clicked.connect(self._on_download_all)
        commands.addWidget(self._btn_download_all)

        self._btn_offline_all = QPushButton("Offline All")
        self._btn_offline_all.setObjectName("bulkBtn")
        self._btn_offline_all.setToolTip("Take all active modules offline")
        self._btn_offline_all.clicked.connect(self._on_offline_all)
        commands.addWidget(self._btn_offline_all)

        layout.addWidget(hbar)
        layout.addLayout(commands)

        # ── Module table ──
        self._table = QTableWidget()
        self._table.setColumnCount(_N_COLS)
        self._table.setHorizontalHeaderLabels(_HEADERS)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(True)

        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(COL_MODULE, QHeaderView.Stretch)
        hdr.setSectionResizeMode(COL_DOWNLOADED, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(COL_ACTION, QHeaderView.ResizeToContents)
        for col in (COL_STATE, COL_MODIFIED, COL_BLOCKS, COL_SCANS,
                    COL_SCAN_MS, COL_RATE):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeToContents)

        layout.addWidget(self._table, 1)

        # ── Close button ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_close = QPushButton("Close")
        btn_close.setObjectName("btnClose")
        btn_close.clicked.connect(self.close)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

    # ────────────────────────────────────────────── show / hide
    def showEvent(self, event):
        super().showEvent(event)
        self._refresh()
        self._timer.start(1000)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    # ────────────────────────────────────────────── refresh
    def _show_properties(self) -> None:
        from azeo_control_trainer.core.presentation.headless import is_headless
        from .controller_properties import ControllerPropertiesDialog

        dialog = ControllerPropertiesDialog(self._store, parent=self)
        dialog.finished.connect(lambda _r: self._refresh())
        if is_headless():
            return
        dialog.exec()

    def _refresh_identity(self) -> None:
        node = getattr(self._store, "controller", None)
        if node is None:
            self._lbl_identity.setText("")
            return
        self._lbl_node.setText(node.name)
        over = node.over_capacity(self._store)
        usage = node.dst_usage(self._store)
        lock = "  ·  keylock LOCKED" if node.keylock else ""
        self._lbl_identity.setText(
            f"{node.model.name}  ·  DSTs {usage} of {node.dst_limit}{lock}")
        self._lbl_identity.setStyleSheet(
            "color: #C62828; font-weight: bold;" if (over or node.keylock)
            else f"color: {UI.blue};")

    def _refresh(self):
        """Update table, header, warning banner, and bulk buttons."""
        self._refresh_identity()
        from ..designer_tab import StrategyCanvas

        tabs = self._designer._canvas_tabs
        n_tabs = tabs.count()

        # Gather canvas info
        rows_data = []
        for i in range(n_tabs):
            w = tabs.widget(i)
            if not isinstance(w, StrategyCanvas):
                continue
            label = tabs.tabText(i)
            # Strip indicators
            if label.startswith("\u25cf "):
                label = label[2:]
            if label.endswith(" *"):
                label = label[:-2]

            runtime = w.runtime
            if runtime is None:
                state = _NOT_LOADED
                status = {"online": False, "scan_count": 0,
                          "last_scan_ms": 0.0, "block_count": 0}
            elif runtime.is_online:
                state = _ACTIVE
                status = runtime.get_status()
            else:
                state = _INACTIVE
                status = runtime.get_status()

            rows_data.append({
                "index": i,
                "label": label,
                "state": state,
                "block_count": status.get("block_count", 0),
                "scan_count": status.get("scan_count", 0),
                "last_scan_ms": status.get("last_scan_ms", 0.0),
                "has_blocks": len(w.scene.graph.blocks) > 0,
                "modified": w.modified_since_download,
                "downloaded_at": w.downloaded_at,
            })

        # ── Warning banner ──
        active_names = [r["label"] for r in rows_data if r["state"] == _ACTIVE]
        modified_active = [r["label"] for r in rows_data
                           if r["state"] == _ACTIVE and r["modified"]]
        if modified_active:
            self._warning_text.setText(
                f"{len(active_names)} module(s) running. "
                f"{len(modified_active)} modified since download: "
                f"{', '.join(modified_active)} -- Re-Download required."
            )
            self._warning_bar.show()
        elif active_names:
            self._warning_text.setText(
                f"{len(active_names)} module(s) actively running: "
                f"{', '.join(active_names)}"
            )
            self._warning_bar.show()
        else:
            self._warning_bar.hide()

        # ── Bulk buttons ──
        has_inactive_with_blocks = any(
            r["state"] != _ACTIVE and r["has_blocks"] for r in rows_data)
        has_active = len(active_names) > 0
        self._btn_download_all.setEnabled(has_inactive_with_blocks)
        self._btn_offline_all.setEnabled(has_active)

        # ── Update table rows ──
        if self._table.rowCount() != len(rows_data):
            self._table.setRowCount(len(rows_data))

        for row, rd in enumerate(rows_data):
            # Module name
            self._set_cell(row, COL_MODULE, rd["label"], bold=True)
            self._table.item(row, COL_MODULE).setData(Qt.UserRole, ref(tabs.widget(rd["index"])))

            # State with icon
            state_item = self._table.item(row, COL_STATE)
            if state_item is None:
                state_item = QTableWidgetItem()
                state_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self._table.setItem(row, COL_STATE, state_item)
            state_item.setText(rd["state"])
            state_item.setIcon(_status_icon(_STATE_COLORS[rd["state"]]))
            state_item.setForeground(QColor(_STATE_COLORS[rd["state"]]))

            # Modified indicator
            if rd["modified"]:
                self._set_cell(row, COL_MODIFIED, "*",
                               align=Qt.AlignCenter,
                               fg="#E65100")
            else:
                self._set_cell(row, COL_MODIFIED, "",
                               align=Qt.AlignCenter)

            # Block count
            self._set_cell(row, COL_BLOCKS, str(rd["block_count"]),
                           align=Qt.AlignCenter)

            # Scan count
            self._set_cell(row, COL_SCANS,
                           f"{rd['scan_count']:,}" if rd["scan_count"] else "---",
                           align=Qt.AlignRight | Qt.AlignVCenter)

            # Last scan ms
            ms = rd["last_scan_ms"]
            self._set_cell(row, COL_SCAN_MS,
                           f"{ms:.2f}" if ms > 0 else "---",
                           align=Qt.AlignRight | Qt.AlignVCenter)

            # Rate (Hz)
            if ms > 0:
                rate = 1000.0 / ms
                rate_str = f"{rate:.0f}"
            else:
                rate_str = "---"
            self._set_cell(row, COL_RATE, rate_str,
                           align=Qt.AlignRight | Qt.AlignVCenter)

            # Downloaded timestamp
            ts = rd["downloaded_at"]
            if ts is not None:
                ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
            else:
                ts_str = "---"
            self._set_cell(row, COL_DOWNLOADED, ts_str,
                           align=Qt.AlignCenter)

            # Action buttons
            self._update_action_buttons(row, rd)

        self._table.resizeRowsToContents()

        # ── Update header ──
        n_active = len(active_names)
        n_total = len(rows_data)
        total_blocks = sum(r["block_count"] for r in rows_data
                           if r["state"] == _ACTIVE)
        total_scan = sum(r["last_scan_ms"] for r in rows_data
                         if r["state"] == _ACTIVE)

        self._lbl_modules.setText(f"Active: {n_active} / {n_total}")
        self._lbl_blocks.setText(f"Blocks: {total_blocks}")
        self._lbl_scan.setText(
            f"Total Scan: {total_scan:.2f} ms" if total_scan > 0 else
            "Scan: --- ms")

        if n_active > 0:
            self._lbl_state.setText("RUN")
            self._lbl_state.setStyleSheet(_BADGE_RUN)
        else:
            self._lbl_state.setText("STANDBY")
            self._lbl_state.setStyleSheet(_BADGE_STANDBY)

    # ────────────────────────────────────────────── cell helpers
    def _set_cell(self, row, col, text, bold=False, align=None, fg=None):
        """Set or update a table cell."""
        item = self._table.item(row, col)
        if item is None:
            item = QTableWidgetItem()
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            if align:
                item.setTextAlignment(align)
            self._table.setItem(row, col, item)
        item.setText(text)
        if bold:
            f = QFont(self.font())
            f.setBold(True)
            item.setFont(f)
        if fg:
            item.setForeground(QColor(fg))

    # ────────────────────────────────────────────── action buttons
    def _module_commands(self, row):
        item = self._table.item(row, COL_MODULE) if row >= 0 else None
        target = item.data(Qt.UserRole) if item else None
        if not callable(target):
            return []

        def index():
            canvas = target()
            return self._designer._canvas_tabs.indexOf(canvas) if canvas and isValid(canvas) else -1

        def enabled(operation):
            if index() < 0:
                return False
            canvas = target()
            active = bool(canvas.runtime and canvas.runtime.is_online)
            if operation in {"Validate", "Compile"}:
                return bool(canvas.scene.graph.blocks)
            if getattr(getattr(self._store, "controller", None), "keylock", False):
                return False
            return active if operation in {"Go Offline", "Re-Download"} else (
                not active and bool(canvas.scene.graph.blocks))

        actions = [("Validate", self._on_validate, "compile"),
                   ("Compile", self._on_compile, "compile"),
                   ("Go Online", self._on_go_online, "connect"),
                   ("Re-Download", self._on_redownload, "download"),
                   ("Go Offline", self._on_go_offline, "disconnect")]
        return [Command(label, lambda callback=callback: callback(index()), mark,
                        lambda label=label: enabled(label)) for label, callback, mark in actions]

    def _update_action_buttons(self, row, rd):
        existing = self._table.cellWidget(row, COL_ACTION)
        if existing:
            return
        button = QToolButton()
        button.setText("Actions")
        button.setIcon(icon("properties"))
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        button.setProperty("configurationQuiet", True)
        # Resolve the clicked cell at invocation; a closed tab can shift rows.
        def show_actions():
            current = next((r for r in range(self._table.rowCount())
                            if self._table.cellWidget(r, COL_ACTION) is button), -1)
            if current < 0:
                return
            self._table.selectRow(current)
            index = self._table.model().index(current, 0)
            self.menus.open_context(self._table, index, self._module_commands(current),
                                    "Control module", button.mapToGlobal(button.rect().bottomLeft()))
        button.clicked.connect(show_actions)
        self._table.setCellWidget(row, COL_ACTION, button)

    def _on_validate(self, canvas_index: int):
        """Run validation on a single module and show results."""
        from ..designer_tab import StrategyCanvas
        w = self._designer._canvas_tabs.widget(canvas_index)
        if not isinstance(w, StrategyCanvas):
            return

        label = self._clean_label(canvas_index)

        from azeo_control_trainer.core.strategy.engine.validator import validate_strategy
        findings = validate_strategy(w.scene.graph)

        if not findings:
            QMessageBox.information(
                self, "Validation Passed",
                f"Module '{label}' passed all validation checks.")
            return

        lines = []
        errors = warnings = infos = 0
        for f in findings:
            prefix = f.severity
            if f.severity == "ERROR":
                errors += 1
            elif f.severity == "WARNING":
                warnings += 1
            else:
                infos += 1
            block_hint = f" [{f.block_id[:8]}]" if f.block_id else ""
            lines.append(f"  [{prefix}]{block_hint} {f.message}")

        summary_parts = []
        if errors:
            summary_parts.append(f"{errors} error(s)")
        if warnings:
            summary_parts.append(f"{warnings} warning(s)")
        if infos:
            summary_parts.append(f"{infos} info(s)")

        QMessageBox.information(
            self, f"Validation -- {label}",
            f"Module '{label}': {', '.join(summary_parts)}\n\n"
            + "\n".join(lines))

    def _on_compile(self, canvas_index: int):
        """Dry-run compile a module and report results."""
        from ..designer_tab import StrategyCanvas
        w = self._designer._canvas_tabs.widget(canvas_index)
        if not isinstance(w, StrategyCanvas):
            return

        label = self._clean_label(canvas_index)

        from azeo_control_trainer.core.strategy.engine.compiler import (
            compile_strategy, CompileError,
        )
        try:
            compiled = compile_strategy(w.scene.graph)
        except CompileError as e:
            QMessageBox.warning(
                self, f"Compile Failed -- {label}",
                f"Module '{label}' failed to compile:\n\n{e}")
            return

        n_blocks = len(compiled.exec_order)
        n_fwd = len(compiled.forward_wires)
        n_bkcal = len(compiled.bkcal_wires)

        # Show execution order
        order_lines = []
        for idx, bid in enumerate(compiled.exec_order, 1):
            blk = compiled.graph.blocks.get(bid)
            if blk:
                order_lines.append(
                    f"  {idx:2d}. {blk.block_type:<12} "
                    f"{blk.instance_name or bid[:8]}")

        QMessageBox.information(
            self, f"Compile OK -- {label}",
            f"Module '{label}' compiled successfully.\n\n"
            f"Blocks: {n_blocks}   Fwd wires: {n_fwd}   "
            f"BKCAL wires: {n_bkcal}\n\n"
            f"Execution order:\n" + "\n".join(order_lines))

    def _on_go_online(self, canvas_index: int):
        """Bring a single module online."""
        active = self._get_active_module_names()
        if active:
            reply = QMessageBox.question(
                self, "Modules Already Running",
                f"{len(active)} module(s) already active on CTRL-1:\n"
                f"  {', '.join(active)}\n\n"
                "Downloading an additional module will add it to the "
                "running scan cycle.\n\nProceed?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply != QMessageBox.Yes:
                return

        ok = self._designer.go_online_single(canvas_index)
        if ok:
            log.info("Module at index %d brought online via controller dialog",
                     canvas_index)
        else:
            label = self._clean_label(canvas_index)
            QMessageBox.warning(
                self, "Download Failed",
                f"Failed to bring module '{label}' online.\n"
                "Check the log for compilation errors.")
        self._refresh()

    def _on_go_offline(self, canvas_index: int):
        """Take a single module offline."""
        self._designer.take_canvas_offline_by_index(canvas_index)
        log.info("Module at index %d taken offline via controller dialog",
                 canvas_index)
        self._refresh()

    def _on_redownload(self, canvas_index: int):
        """Re-download: offline -> recompile -> online."""
        label = self._clean_label(canvas_index)

        reply = QMessageBox.question(
            self, "Re-Download Module",
            f"Re-downloading '{label}' will:\n\n"
            "  1. Take the module offline (outputs freeze)\n"
            "  2. Recompile with latest strategy edits\n"
            "  3. Bring the module back online\n\n"
            "Controllers in this module will reinitialize.\n"
            "Proceed?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        ok = self._designer.redownload_single(canvas_index)
        if ok:
            log.info("Module '%s' re-downloaded via controller dialog", label)
        else:
            QMessageBox.warning(
                self, "Re-Download Failed",
                f"Failed to re-download module '{label}'.\n"
                "Check the log for compilation errors.")
        self._refresh()

    # ────────────────────────────────────────────── bulk actions
    def _on_download_all(self):
        """Download all inactive modules that have blocks."""
        from ..designer_tab import StrategyCanvas
        tabs = self._designer._canvas_tabs

        targets = []
        for i in range(tabs.count()):
            w = tabs.widget(i)
            if not isinstance(w, StrategyCanvas):
                continue
            if w.runtime and w.runtime.is_online:
                continue
            if len(w.scene.graph.blocks) == 0:
                continue
            label = self._clean_label(i)
            targets.append((i, label))

        if not targets:
            QMessageBox.information(
                self, "Download All",
                "No inactive modules with blocks to download.")
            return

        names = ", ".join(t[1] for t in targets)
        active = self._get_active_module_names()
        warn = ""
        if active:
            warn = (f"\n\n{len(active)} module(s) already active: "
                    f"{', '.join(active)}")

        reply = QMessageBox.question(
            self, "Download All",
            f"Download {len(targets)} module(s) to CTRL-1?\n"
            f"  {names}{warn}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        ok_count = 0
        fail_names = []
        for idx, label in targets:
            if self._designer.go_online_single(idx):
                ok_count += 1
            else:
                fail_names.append(label)

        if fail_names:
            QMessageBox.warning(
                self, "Download All -- Errors",
                f"{ok_count} downloaded, {len(fail_names)} failed:\n"
                f"  {', '.join(fail_names)}")
        self._refresh()

    def _on_offline_all(self):
        """Take all active modules offline."""
        active = self._get_active_module_names()
        if not active:
            return

        reply = QMessageBox.question(
            self, "Offline All",
            f"Take {len(active)} module(s) offline?\n"
            f"  {', '.join(active)}\n\n"
            "All control outputs will freeze at current values.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._designer._go_offline()
        log.info("All modules taken offline via controller dialog")
        self._refresh()

    # ────────────────────────────────────────────── helpers
    def _clean_label(self, canvas_index: int) -> str:
        """Get clean module name (no indicators) for a tab index."""
        label = self._designer._canvas_tabs.tabText(canvas_index)
        if label.startswith("\u25cf "):
            label = label[2:]
        if label.endswith(" *"):
            label = label[:-2]
        return label

    def _get_active_module_names(self) -> list[str]:
        """Return names of all currently active modules."""
        from ..designer_tab import StrategyCanvas
        tabs = self._designer._canvas_tabs
        names = []
        for i in range(tabs.count()):
            w = tabs.widget(i)
            if isinstance(w, StrategyCanvas) and w.runtime and w.runtime.is_online:
                names.append(self._clean_label(i))
        return names
