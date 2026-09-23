"""DMC (Dynamic Matrix Control) Faceplate — ISA-101 style popup for APC status.

Shows DMC supervisory controller status:
  - Enable/Disable toggle
  - Watchdog status & timeout
  - Cycle time
  - Shed status (active constraints)
  - CV/MV summary

Reads/writes tags:
  dmc.enable        — master enable
  dmc.heartbeat     — heartbeat counter
  dmc.watchdog_ok   — watchdog healthy
  dmc.active        — DMC active (enable AND watchdog OK)
  dmc.cycle_time    — last cycle execution time (seconds)
  dmc.shed_status   — shed level (0=normal, 1=partial, 2=full)
  dmc.shed_count    — number of shed constraints
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from azeo_control_trainer.core.pid.theme.colors import (
    BG_PANEL, BG_INSET, BG_RAISED,
    TEXT_PRIMARY, TEXT_SECONDARY,
    ALARM_OK, ALARM_CRITICAL, ALARM_WARN,
)

_FONT = "Consolas"

_TITLE_STYLE = (
    f"QLabel {{ font-size: 12pt; font-weight: bold; color: {TEXT_PRIMARY};"
    f" background: transparent; }}"
)

_LABEL_STYLE = (
    f"QLabel {{ font-size: 9pt; color: {TEXT_PRIMARY};"
    f" background: transparent; }}"
)

_VALUE_STYLE = (
    f"QLabel {{ font-size: 10pt; font-weight: bold; font-family: {_FONT};"
    f" color: {TEXT_PRIMARY}; background: {BG_INSET};"
    f" padding: 3px 8px; border-radius: 3px; border: 1px solid #555; }}"
)

_GRP_STYLE = (
    f"QGroupBox {{ font-weight: bold; font-size: 10pt; color: {TEXT_PRIMARY};"
    f" background: {BG_PANEL}; border: 1px solid #555; border-radius: 4px;"
    f" padding-top: 16px; margin-top: 8px; }}"
    f" QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; }}"
)


def _btn_style(bg, fg, border, hover, pressed):
    return (
        f"QPushButton {{ background: {bg}; color: {fg};"
        f" font-weight: bold; font-size: 9pt;"
        f" padding: 6px 14px; border-radius: 4px;"
        f" border: 2px solid {border}; }}"
        f" QPushButton:hover {{ background: {hover}; }}"
        f" QPushButton:pressed {{ background: {pressed}; }}"
    )


class DMCFaceplatePopup(QDialog):
    """ISA-101 style faceplate for the DMC supervisory controller."""

    enable_changed = Signal(bool)

    def __init__(self, store=None, parent=None):
        super().__init__(parent)
        self._store = store
        self.setWindowTitle("DMC Controller")
        self.setWindowFlags(
            Qt.Tool | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint)
        self.setFixedWidth(360)
        self.setMinimumHeight(420)
        self.setStyleSheet(
            f"QDialog {{ background: {BG_PANEL}; }}")
        self._build_ui()

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    def closeEvent(self, event):
        self._timer.stop()
        super().closeEvent(event)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # Title
        title = QLabel("DMC Supervisory Controller")
        title.setStyleSheet(_TITLE_STYLE)
        title.setAlignment(Qt.AlignCenter)
        root.addWidget(title)

        # ── Enable/Disable ──
        ctrl_grp = QGroupBox("Master Control")
        ctrl_grp.setStyleSheet(_GRP_STYLE)
        cl = QHBoxLayout(ctrl_grp)
        cl.setSpacing(10)

        self._btn_enable = QPushButton("ENABLE")
        self._btn_enable.setStyleSheet(_btn_style(
            "#1B5E20", "white", "#2E7D32", "#2E7D32", "#0D3B12"))
        self._btn_enable.clicked.connect(lambda: self._set_enable(True))

        self._btn_disable = QPushButton("DISABLE")
        self._btn_disable.setStyleSheet(_btn_style(
            "#B71C1C", "white", "#D32F2F", "#D32F2F", "#7F0000"))
        self._btn_disable.clicked.connect(lambda: self._set_enable(False))

        cl.addStretch()
        cl.addWidget(self._btn_enable)
        cl.addWidget(self._btn_disable)
        cl.addStretch()
        root.addWidget(ctrl_grp)

        # ── Status Grid ──
        status_grp = QGroupBox("Status")
        status_grp.setStyleSheet(_GRP_STYLE)
        grid = QGridLayout(status_grp)
        grid.setSpacing(8)

        labels = [
            ("Enable:", 0, 0), ("Active:", 0, 2),
            ("Watchdog:", 1, 0), ("Heartbeat:", 1, 2),
            ("Cycle Time:", 2, 0), ("Shed Status:", 2, 2),
        ]
        for text, row, col in labels:
            lbl = QLabel(text)
            lbl.setStyleSheet(_LABEL_STYLE)
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(lbl, row, col)

        self._v_enable = self._make_val_label()
        self._v_active = self._make_val_label()
        self._v_watchdog = self._make_val_label()
        self._v_heartbeat = self._make_val_label()
        self._v_cycle_time = self._make_val_label()
        self._v_shed = self._make_val_label()

        grid.addWidget(self._v_enable, 0, 1)
        grid.addWidget(self._v_active, 0, 3)
        grid.addWidget(self._v_watchdog, 1, 1)
        grid.addWidget(self._v_heartbeat, 1, 3)
        grid.addWidget(self._v_cycle_time, 2, 1)
        grid.addWidget(self._v_shed, 2, 3)

        root.addWidget(status_grp)

        # ── Watchdog Configuration ──
        wd_grp = QGroupBox("Watchdog Configuration")
        wd_grp.setStyleSheet(_GRP_STYLE)
        wl = QGridLayout(wd_grp)
        wl.setSpacing(8)

        wl.addWidget(self._make_label("Timeout (s):"), 0, 0)
        self._spn_timeout = QSpinBox()
        self._spn_timeout.setRange(5, 300)
        self._spn_timeout.setValue(30)
        self._spn_timeout.setSuffix(" s")
        self._spn_timeout.setStyleSheet(
            f"QSpinBox {{ background: {BG_INSET}; color: {TEXT_PRIMARY};"
            f" font-size: 9pt; font-family: {_FONT};"
            f" border: 1px solid #555; border-radius: 3px; padding: 2px 6px; }}")
        self._spn_timeout.valueChanged.connect(self._on_timeout_changed)
        wl.addWidget(self._spn_timeout, 0, 1)

        wl.addWidget(self._make_label("Cycle Time (s):"), 1, 0)
        self._spn_cycle = QSpinBox()
        self._spn_cycle.setRange(1, 120)
        self._spn_cycle.setValue(60)
        self._spn_cycle.setSuffix(" s")
        self._spn_cycle.setStyleSheet(self._spn_timeout.styleSheet())
        self._spn_cycle.valueChanged.connect(self._on_cycle_changed)
        wl.addWidget(self._spn_cycle, 1, 1)

        root.addWidget(wd_grp)

        # ── Shed Details ──
        shed_grp = QGroupBox("Constraint Shed Details")
        shed_grp.setStyleSheet(_GRP_STYLE)
        sl = QVBoxLayout(shed_grp)
        self._shed_detail = QLabel("No constraints shed")
        self._shed_detail.setStyleSheet(_LABEL_STYLE)
        self._shed_detail.setWordWrap(True)
        sl.addWidget(self._shed_detail)
        root.addWidget(shed_grp)

        root.addStretch()

    @staticmethod
    def _make_val_label() -> QLabel:
        lbl = QLabel("---")
        lbl.setFixedWidth(100)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(_VALUE_STYLE)
        return lbl

    @staticmethod
    def _make_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(_LABEL_STYLE)
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return lbl

    def _set_enable(self, enabled: bool):
        if self._store:
            self._store.queue_write("dmc.enable", 1 if enabled else 0)
        self.enable_changed.emit(enabled)

    def _on_timeout_changed(self, val: int):
        if self._store:
            self._store.queue_write("dmc.watchdog_timeout", float(val))

    def _on_cycle_changed(self, val: int):
        if self._store:
            self._store.queue_write("dmc.cycle_time_sp", float(val))

    def _poll(self):
        if not self._store:
            return

        enable = self._store.get("dmc.enable", 0)
        active = self._store.get("dmc.active", 0)
        watchdog = self._store.get("dmc.watchdog_ok", 0)
        heartbeat = self._store.get("dmc.heartbeat", 0)
        cycle_time = self._store.get("dmc.cycle_time", 0)
        shed_status = self._store.get("dmc.shed_status", 0)
        shed_count = self._store.get("dmc.shed_count", 0)

        self._set_bool(self._v_enable, enable, "ENABLED", "DISABLED")
        self._set_bool(self._v_active, active, "ACTIVE", "INACTIVE")
        self._set_bool(self._v_watchdog, watchdog, "OK", "TIMEOUT",
                       bad_color=ALARM_CRITICAL)
        self._v_heartbeat.setText(str(int(heartbeat)))

        ct = float(cycle_time) if cycle_time else 0.0
        self._v_cycle_time.setText(f"{ct:.1f} s")

        shed_level = int(shed_status) if shed_status else 0
        shed_text = {0: "NORMAL", 1: "PARTIAL", 2: "FULL"}.get(shed_level, "---")
        shed_colors = {
            0: (ALARM_OK, "white"),
            1: (ALARM_WARN, "black"),
            2: (ALARM_CRITICAL, "white"),
        }
        bg, fg = shed_colors.get(shed_level, ("#666", TEXT_PRIMARY))
        self._v_shed.setText(shed_text)
        self._v_shed.setStyleSheet(
            f"QLabel {{ font-size: 10pt; font-weight: bold; font-family: {_FONT};"
            f" color: {fg}; background: {bg};"
            f" padding: 3px 8px; border-radius: 3px; border: 1px solid #555; }}")

        # Shed detail text
        sc = int(shed_count) if shed_count else 0
        if shed_level == 0:
            self._shed_detail.setText("No constraints shed")
        elif shed_level == 1:
            self._shed_detail.setText(
                f"Partial shed: {sc} constraint(s) relaxed\n"
                "LP optimizer active — low-priority CVs released")
        else:
            self._shed_detail.setText(
                f"Full shed: {sc} constraint(s) — DMC output frozen\n"
                "All MVs holding last good output")

        # Update timeout/cycle spinboxes from store (don't fight user edits)
        if not self._spn_timeout.hasFocus():
            timeout_val = self._store.get("dmc.watchdog_timeout", 30)
            self._spn_timeout.setValue(int(float(timeout_val)))
        if not self._spn_cycle.hasFocus():
            cycle_sp = self._store.get("dmc.cycle_time_sp", 60)
            self._spn_cycle.setValue(int(float(cycle_sp)))

    def _set_bool(self, label: QLabel, value, true_text: str, false_text: str,
                  bad_color: str = "#666"):
        is_true = bool(value) and value not in (0, 0.0, "0", None)
        if is_true:
            label.setText(true_text)
            label.setStyleSheet(
                f"QLabel {{ font-size: 10pt; font-weight: bold; font-family: {_FONT};"
                f" color: white; background: {ALARM_OK};"
                f" padding: 3px 8px; border-radius: 3px; border: 1px solid #1B5E20; }}")
        else:
            label.setText(false_text)
            label.setStyleSheet(
                f"QLabel {{ font-size: 10pt; font-weight: bold; font-family: {_FONT};"
                f" color: white; background: {bad_color};"
                f" padding: 3px 8px; border-radius: 3px; border: 1px solid #555; }}")
