"""Runtime status bar — Azeo-style scan statistics display.

Shows: strategy name, scan count, scan time (last/avg/max), online duration,
block count, error count. Color-coded scan time indicator.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget


_BAR_STYLE = f"""
RuntimeStatusBar {{
    background: qlineargradient(y1:0, y2:1, stop:0 {UI.chrome_alt}, stop:1 {UI.chrome_alt});
    border-top: 1px solid {UI.border};
}}
"""

_LABEL_STYLE = (
    f"color: {UI.text_secondary}; font-size: 9pt; font-family: Consolas; "
    "border: none; background: transparent; padding: 0 2px;"
)
_VALUE_STYLE = (
    f"color: {UI.blue}; font-size: 9pt; font-family: Consolas; font-weight: bold; "
    "border: none; background: transparent; padding: 0 4px;"
)
_VALUE_GOOD = "color: #2D8E3C;"    # green
_VALUE_WARN = "color: #B8962A;"    # amber
_VALUE_BAD = "color: #C82A3A;"     # red


class RuntimeStatusBar(QWidget):
    """Compact status bar showing runtime scan statistics."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(22)
        self.setStyleSheet(_BAR_STYLE)
        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(2)

        def _lbl(text: str) -> QLabel:
            l = QLabel(text)
            l.setStyleSheet(_LABEL_STYLE)
            return l

        def _val(text: str = "") -> QLabel:
            l = QLabel(text)
            l.setStyleSheet(_VALUE_STYLE)
            return l

        # Heartbeat dot
        self._heartbeat = QLabel("\u25cf")
        self._heartbeat.setStyleSheet(
            "color: #4CAF50; font-size: 9pt; border: none; "
            "background: transparent; padding: 0 4px;")
        layout.addWidget(self._heartbeat)
        self._hb_state = True

        layout.addWidget(_lbl("Strategy:"))
        self._name = _val("--")
        layout.addWidget(self._name)

        layout.addWidget(self._sep())

        layout.addWidget(_lbl("Blocks:"))
        self._blocks = _val("0")
        layout.addWidget(self._blocks)

        layout.addWidget(self._sep())

        layout.addWidget(_lbl("Scans:"))
        self._scans = _val("0")
        layout.addWidget(self._scans)

        layout.addWidget(self._sep())

        layout.addWidget(_lbl("Scan:"))
        self._scan_time = _val("0.0 ms")
        layout.addWidget(self._scan_time)

        layout.addWidget(_lbl("Avg:"))
        self._avg_time = _val("0.0 ms")
        layout.addWidget(self._avg_time)

        layout.addWidget(_lbl("Max:"))
        self._max_time = _val("0.0 ms")
        layout.addWidget(self._max_time)

        layout.addWidget(self._sep())

        layout.addWidget(_lbl("Online:"))
        self._online_dur = _val("00:00:00")
        layout.addWidget(self._online_dur)

        layout.addWidget(self._sep())

        layout.addWidget(_lbl("Errors:"))
        self._errors = _val("0")
        layout.addWidget(self._errors)

        layout.addStretch(1)

    def _sep(self) -> QFrame:
        s = QFrame()
        s.setFixedWidth(1)
        s.setFixedHeight(14)
        s.setStyleSheet(f"background: {UI.border};")
        return s

    def update_status(self, status: dict):
        """Update display from runtime.get_status() dict."""
        self._name.setText(status.get("strategy_name", "--") or "--")
        self._blocks.setText(str(status.get("block_count", 0)))
        self._scans.setText(f"{status.get('scan_count', 0):,}")

        # Scan time with color coding
        last_ms = status.get("last_scan_ms", 0.0)
        self._scan_time.setText(f"{last_ms:.1f} ms")
        if last_ms < 10:
            color = _VALUE_GOOD
        elif last_ms < 50:
            color = _VALUE_WARN
        else:
            color = _VALUE_BAD
        self._scan_time.setStyleSheet(_VALUE_STYLE + color)

        avg_ms = status.get("avg_scan_ms", 0.0)
        self._avg_time.setText(f"{avg_ms:.1f} ms")

        max_ms = status.get("max_scan_ms", 0.0)
        self._max_time.setText(f"{max_ms:.1f} ms")

        # Online duration
        dur = status.get("online_duration", 0.0)
        h = int(dur // 3600)
        m = int((dur % 3600) // 60)
        s = int(dur % 60)
        self._online_dur.setText(f"{h:02d}:{m:02d}:{s:02d}")

        # Error count
        errors = status.get("error_count", 0)
        self._errors.setText(str(errors))
        if errors > 0:
            self._errors.setStyleSheet(_VALUE_STYLE + _VALUE_BAD)
        else:
            self._errors.setStyleSheet(_VALUE_STYLE + _VALUE_GOOD)

        # Heartbeat pulse
        self._hb_state = not self._hb_state
        self._heartbeat.setStyleSheet(
            f"color: {'#4CAF50' if self._hb_state else '#1B5E20'}; "
            "font-size: 9pt; border: none; background: transparent; padding: 0 4px;")

    def set_offline(self):
        """Show offline state."""
        self._name.setText("--")
        self._blocks.setText("0")
        self._scans.setText("0")
        self._scan_time.setText("-- ms")
        self._scan_time.setStyleSheet(_VALUE_STYLE)
        self._avg_time.setText("-- ms")
        self._max_time.setText("-- ms")
        self._online_dur.setText("--:--:--")
        self._errors.setText("0")
        self._errors.setStyleSheet(_VALUE_STYLE)
        self._heartbeat.setStyleSheet(
            "color: #555; font-size: 9pt; border: none; "
            "background: transparent; padding: 0 4px;")
