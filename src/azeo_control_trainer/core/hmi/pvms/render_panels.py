"""Per-class PVM panels — where the generic shell is not enough.

Three views the catalog singles out, each fed from the class's own
bindings and refreshed by the host widget:

- **Interlock condition table** — the §8.6 rows, with the elapsed-time
  bar: a run interlock that has been false for 2 of its 4 seconds is
  *about to* trip, and this is the one screen that says so.
- **Cascade stack** — master over slave; the state that matters most is
  the slave not in CAS, which each loop's own faceplate cannot see.
- **SFC running chart** — the chart's steps as a list, active step
  marked, fed by the CONFIG/CHART structure plus the live ACTIVE pin.

Panels never resolve state themselves beyond reading bound results —
layout only, same division as the host (I6).
"""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
    QWidget,
)

from ..theme.roles import Role
# The interlock condition table is the SHARED one. It used to be a
# second implementation here — a flat table with `timer/delay` as
# text — beside `faceplate_fb.ConditionTable`, which is built to the
# DCC_fp figure with a tab per kind and the elapsed-time bar. Two
# tables for one thing is two chances to disagree about what a
# tripped condition looks like, and they already did: this one knew
# a trip inverts and the other did not. One table, one truth — the
# same rule the repo states for promoted shared assets and for
# `DisplayRenderer`.
from .faceplate_fb import ConditionTable as InterlockConditionPanel


def _result(bound, key):
    binding = bound.get(key)
    return None if binding is None or isinstance(binding, tuple) \
        else binding.result


class DeviceControlPanel(QWidget):
    """DC_fp's state buttons, transition state and failure summary."""

    write_requested = Signal(str, object)

    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self._palette = palette
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 2, 8, 2)
        root.setSpacing(6)
        self.pv = QLabel("#######")
        self.pv.setAlignment(Qt.AlignCenter)
        self.pv.setFont(QFont("Consolas", 14, QFont.Bold))
        root.addWidget(self.pv)

        commands = QHBoxLayout()
        self.command_buttons = {}
        for key, text in (("cmd.start", "START"),
                          ("cmd.stop", "STOP"),
                          ("cmd.reset", "RESET")):
            button = QPushButton(text)
            button.setFixedHeight(24)
            button.clicked.connect(
                lambda _checked=False, k=key:
                self.write_requested.emit(k, True))
            commands.addWidget(button)
            self.command_buttons[key] = button
        root.addLayout(commands)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(5)
        self.rows = {}
        device_rows = (
            ("mode", "Actual mode"),
            (None, "STATE TRANSITION"),
            ("time.limit", "Transition limit"),
            ("elapsed", "Elapsed time"),
            (None, "DEVICE STATE"),
            ("state.value", "Device state"),
            ("state.locked", "Reset required"),
            ("state.paused", "Transition held"),
            (None, "FAIL CONDITION"),
            ("state.fail_code", "Failure code"),
            ("state.fail", "Fail condition"),
        )
        for row, (key, caption) in enumerate(device_rows):
            label = QLabel(caption)
            label.setFont(QFont("Segoe UI", 8, QFont.Bold))
            if key is None:
                grid.addWidget(label, row, 0, 1, 2)
                continue
            value = QLabel("")
            value.setFont(QFont("Consolas", 9))
            grid.addWidget(label, row, 0)
            grid.addWidget(value, row, 1)
            self.rows[key] = value
        root.addLayout(grid)
        root.addStretch(1)
        from .faceplate_ui import TrendChart
        self.trend = TrendChart(palette)
        self.trend.setFixedHeight(112)
        root.addWidget(self.trend)
        self.setFixedHeight(465)
        self.apply_theme(palette)

    def apply_theme(self, palette: dict) -> None:
        self._palette = palette
        self.pv.setStyleSheet("color: %s;" % palette[Role.HEADING])
        for button in self.command_buttons.values():
            button.setStyleSheet(
                "background: %s; border: 1px solid %s; font-weight: bold;"
                % (palette[Role.SURFACE_FIELD], palette[Role.LINE]))
        for value in self.rows.values():
            value.setStyleSheet("color: %s;" % palette[Role.TEXT])
        self.trend.apply_theme(palette)

    def refresh(self, bound) -> None:
        pv = _result(bound, "pv.value")
        self.pv.setText("#######" if pv is None or pv.value is None
                        else str(pv.value))
        for key, label in self.rows.items():
            if key == "time.limit":
                state = _result(bound, "state.value")
                terminal = "time.stop_limit" \
                    if state is not None and state.value == 3 \
                    else "time.start_limit"
                result = _result(bound, terminal)
            else:
                result = _result(bound, key)
            value = None if result is None else result.value
            if key in ("elapsed", "time.limit") \
                    and isinstance(value, (int, float)):
                text = f"{value:.1f} s"
            elif isinstance(value, bool):
                text = "ACTIVE" if value else "NORMAL"
            else:
                text = "" if value is None else str(value)
            label.setText(text)
        failed = _result(bound, "state.fail")
        self.rows["state.fail"].setStyleSheet(
            "color: %s;" % self._palette[
                Role.ALARM_P1_TEXT if failed is not None and failed.value
                else Role.TEXT])
        self.trend.refresh(bound)


class CompactValuePanel(QWidget):
    """Dense Azeo function-block body for trainer-specific contracts."""

    write_requested = Signal(str, object)
    ROWS: tuple[tuple[str, str], ...] = ()
    COMMANDS: tuple[tuple[str, str], ...] = ()
    PANEL_HEIGHT = 240

    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self._palette = palette
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 2, 8, 2)
        root.setSpacing(5)
        self.values = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)
        for row, (key, caption) in enumerate(self.ROWS):
            label = QLabel(caption)
            label.setFont(QFont("Segoe UI", 8, QFont.Bold))
            value = QLabel("")
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            value.setFont(QFont("Consolas", 9))
            grid.addWidget(label, row, 0)
            grid.addWidget(value, row, 1)
            self.values[key] = value
        root.addLayout(grid)
        if self.COMMANDS:
            commands = QHBoxLayout()
            self.command_buttons = {}
            for key, caption in self.COMMANDS:
                button = QPushButton(caption)
                button.setFixedHeight(24)
                button.clicked.connect(
                    lambda _checked=False, k=key:
                    self.write_requested.emit(k, True))
                commands.addWidget(button)
                self.command_buttons[key] = button
            root.addLayout(commands)
        else:
            self.command_buttons = {}
        from .faceplate_ui import TrendChart
        self.trend = TrendChart(palette)
        self.trend.setFixedHeight(88)
        root.addWidget(self.trend)
        self.setFixedHeight(self.PANEL_HEIGHT)
        self.apply_theme(palette)

    def apply_theme(self, palette: dict) -> None:
        self._palette = palette
        for value in self.values.values():
            value.setStyleSheet("color: %s;" % palette[Role.TEXT])
        for button in self.command_buttons.values():
            button.setStyleSheet(
                "background: %s; border: 1px solid %s; font-weight: bold;"
                % (palette[Role.SURFACE_FIELD], palette[Role.LINE]))
        self.trend.apply_theme(palette)

    @staticmethod
    def _text(value) -> str:
        if value is None:
            return "#######"
        if isinstance(value, bool):
            return "ACTIVE" if value else "NORMAL"
        if isinstance(value, float):
            return f"{value:.3g}"
        return str(value)

    def refresh(self, bound) -> None:
        for key, label in self.values.items():
            result = _result(bound, key)
            label.setText(self._text(None if result is None else result.value))
        self.trend.refresh(bound)


class AnalogTrackingPanel(CompactValuePanel):
    ROWS = (("pv.value", "Tracked output"),
            ("field.value", "Input"),
            ("request", "Tracking request"),
            ("state.active", "Tracking active"))
    PANEL_HEIGHT = 380

    def __init__(self, palette: dict, parent=None):
        super().__init__(palette, parent)
        self.trend.setFixedHeight(220)


class RatioPanel(CompactValuePanel):
    ROWS = (("ratio.actual", "Actual ratio"),
            ("ratio.target", "Target ratio"),
            ("ratio.bias", "Bias"),
            ("field.value", "Input"),
            ("out.value", "Output"))
    PANEL_HEIGHT = 220


class TotalizerPanel(CompactValuePanel):
    ROWS = (("total", "Total"), ("rate", "Rate"),
            ("target", "Target"), ("enable", "Enabled"))
    COMMANDS = (("cmd.reset", "RESET"),)
    PANEL_HEIGHT = 250


class RampSoakPanel(CompactValuePanel):
    ROWS = (("pv.value", "Output"), ("field.value", "Segment"),
            ("state.running", "Running"),
            ("state.complete", "Complete"))
    COMMANDS = (("cmd.start", "START"), ("cmd.stop", "STOP"),
                ("cmd.reset", "RESET"))
    PANEL_HEIGHT = 300


class PassBalancePanel(CompactValuePanel):
    ROWS = (("a.value", "Pass A"), ("b.value", "Pass B"),
            ("c.value", "Pass C"), ("d.value", "Pass D"),
            ("mean", "Mean"), ("spread", "Spread"))
    PANEL_HEIGHT = 230

    def __init__(self, palette: dict, parent=None):
        super().__init__(palette, parent)
        self.trend.hide()


class CascadePairPanel(QWidget):
    """Master stacked over slave, plus the broken-cascade banner."""

    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self._palette = palette
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        self.banner = QLabel("")
        self.banner.setFont(QFont("Segoe UI", 9, QFont.Bold))
        self.banner.hide()
        layout.addWidget(self.banner)
        self._rows: dict[str, QLabel] = {}
        for half in ("master", "slave"):
            row = QHBoxLayout()
            name = QLabel(half.upper())
            name.setFont(QFont("Segoe UI", 8, QFont.Bold))
            name.setStyleSheet(f"color: {palette[Role.TEXT_DIM]};")
            name.setFixedWidth(60)
            row.addWidget(name)
            values = QLabel("")
            values.setFont(QFont("Consolas", 9))
            row.addWidget(values, 1)
            self._rows[half] = values
            layout.addLayout(row)

    def refresh(self, bound) -> None:
        for half in ("master", "slave"):
            pieces = []
            for field in ("pv", "sp", "out", "mode"):
                result = _result(bound, f"{half}.{field}")
                if result is None:
                    continue
                value = result.value
                text = f"{value:.4g}" if isinstance(value, float) \
                    else str(value)
                pieces.append(f"{field.upper()} {text}")
            self._rows[half].setText("   ".join(pieces))
        slave_mode = _result(bound, "slave.mode")
        broken = slave_mode is not None \
            and str(slave_mode.value).upper() not in ("CAS", "RCAS")
        if broken:
            self.banner.setText("CASCADE BROKEN — slave not in CAS")
            self.banner.setStyleSheet(
                f"color: {self._palette[Role.ALARM_P2_TEXT]};")
        self.banner.setVisible(broken)


class SfcRunningChartPanel(QWidget):
    """The chart's steps, active one marked, with its elapsed time."""

    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self._palette = palette
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 4, 0, 4)
        title = QLabel("SEQUENCE")
        title.setFont(QFont("Segoe UI", 8, QFont.Bold))
        title.setStyleSheet(f"color: {palette[Role.TEXT_DIM]};")
        self._layout.addWidget(title)
        self._step_labels: list[QLabel] = []
        self._step_names: list[str] = []

    def refresh(self, bound) -> None:
        chart_result = _result(bound, "chart")
        try:
            chart = json.loads(chart_result.value) \
                if chart_result is not None else {}
        except (TypeError, ValueError):
            chart = {}
        names = [step.get("name") or step.get("id", "")
                 for step in chart.get("steps", [])]
        if names != self._step_names:
            for label in self._step_labels:
                self._layout.removeWidget(label)
                label.hide()
                label.deleteLater()
            self._step_labels = []
            self._step_names = names
            for name in names:
                label = QLabel(name)
                label.setFont(QFont("Consolas", 9))
                self._layout.addWidget(label)
                self._step_labels.append(label)

        active = _result(bound, "step.active")
        active_name = str(active.value) if active is not None else ""
        step_time = _result(bound, "step.time")
        elapsed = float(step_time.value) \
            if step_time is not None and step_time.value else 0.0
        for name, label in zip(self._step_names, self._step_labels):
            if name == active_name:
                label.setText(f"▶ {name}   {elapsed:.0f}s")
                label.setStyleSheet(
                    f"color: {self._palette[Role.ACTION_DEEP]};"
                    "font-weight: bold;")
            else:
                label.setText(f"  {name}")
                label.setStyleSheet(
                    f"color: {self._palette[Role.TEXT_DIM]};")


#: PvmClass name -> panel factory. Rendering-side only: the pvms package
#: stays Qt-free, so the mapping lives here with the widgets.
CLASS_PANELS = {
    "ATFaceplate": AnalogTrackingPanel,
    "RatioFaceplate": RatioPanel,
    "TotalizerFaceplate": TotalizerPanel,
    "RampSoakFaceplate": RampSoakPanel,
    "PassBalanceFaceplate": PassBalancePanel,
    "DeviceFaceplate": DeviceControlPanel,
    "MotorInterlockDetail": InterlockConditionPanel,
    "CascadePairFaceplate": CascadePairPanel,
    "SfcChartFaceplate": SfcRunningChartPanel,
    "SfcChartDetail": SfcRunningChartPanel,
}
