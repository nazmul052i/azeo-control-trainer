"""Relay-feedback auto-tuning for PID controllers.

Implements the Astroem-Haegglund relay feedback method:
  1. Put controller in manual mode
  2. Apply a symmetric relay (bang-bang) around the current output
  3. Measure the resulting PV oscillation period (Pu) and amplitude (a)
  4. Compute ultimate gain: Ku = 4*d / (pi * a)
  5. Apply Ziegler-Nichols tuning rules to get Kp, Ti, Td

Reference: Astroem & Haegglund, "Automatic Tuning of PID Controllers", 1988
"""
from __future__ import annotations

import logging
import math
import time
from enum import Enum, auto

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMessageBox

from azeo_control_trainer.core.pid.core.pid_block_core import Mode

log = logging.getLogger(__name__)


class TuningRule(Enum):
    """Ziegler-Nichols tuning rule variants."""
    ZN_CLASSIC = "Ziegler-Nichols (Classic)"
    ZN_SOME_OVERSHOOT = "Ziegler-Nichols (Some Overshoot)"
    ZN_NO_OVERSHOOT = "Ziegler-Nichols (No Overshoot)"
    PI_ONLY = "PI Only"
    P_ONLY = "P Only"


# Ziegler-Nichols relay tuning formulas: (Kp_factor, Ti_factor, Td_factor)
# Kp = Kp_factor * Ku,  Ti = Ti_factor * Pu,  Td = Td_factor * Pu
_ZN_FORMULAS: dict[TuningRule, tuple[float, float, float]] = {
    TuningRule.ZN_CLASSIC:         (0.60, 0.50, 0.125),
    TuningRule.ZN_SOME_OVERSHOOT:  (0.33, 0.50, 0.333),
    TuningRule.ZN_NO_OVERSHOOT:    (0.20, 0.50, 0.333),
    TuningRule.PI_ONLY:            (0.45, 0.83, 0.0),
    TuningRule.P_ONLY:             (0.50, 0.0,  0.0),
}


class _TunerState(Enum):
    IDLE = auto()
    WAITING_INITIAL = auto()
    RUNNING = auto()
    DONE = auto()
    ABORTED = auto()


class RelayAutoTuner(QObject):
    """Manages a relay feedback auto-tuning test on a PID controller.

    Parameters
    ----------
    block_view : PIDBlockView
        Adapter for reading PV and writing OP/mode.
    relay_amplitude : float
        Half-amplitude of the relay output perturbation (in OP % units).
        The output will toggle between (OP_center - d) and (OP_center + d).
        Default 5.0 means +/- 5% around current output.
    min_cycles : int
        Minimum number of complete oscillation cycles before stopping.
    poll_interval_ms : int
        PV polling interval in milliseconds.
    """

    finished = Signal(dict)   # emitted with results dict when test completes
    aborted = Signal(str)     # emitted with reason string on abort
    status_changed = Signal(str)  # emitted with status text updates

    def __init__(
        self,
        block_view,
        relay_amplitude: float = 5.0,
        min_cycles: int = 4,
        poll_interval_ms: int = 100,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._view = block_view
        self._d = relay_amplitude  # relay half-amplitude (OP %)
        self._min_cycles = min_cycles
        self._poll_ms = poll_interval_ms

        self._state = _TunerState.IDLE
        self._timer = QTimer(self)
        self._timer.setInterval(poll_interval_ms)
        self._timer.timeout.connect(self._on_tick)

        # Test state
        self._op_center: float = 0.0
        self._sp: float = 0.0
        self._relay_high: bool = True  # current relay position
        self._prev_error_sign: int = 0  # +1 or -1

        # Measurements
        self._zero_crossing_times: list[float] = []
        self._pv_peaks: list[float] = []
        self._pv_valleys: list[float] = []
        self._current_extremum: float = 0.0
        self._tracking_peak: bool = True

        self._saved_mode: Mode | None = None
        self._start_time: float = 0.0
        self._max_duration_s: float = 600.0  # 10 min safety timeout

    @property
    def is_running(self) -> bool:
        return self._state in (_TunerState.WAITING_INITIAL, _TunerState.RUNNING)

    def start(self) -> None:
        """Begin the relay feedback test.

        The controller is switched to MAN mode. The output is toggled
        symmetrically around the current OP value.
        """
        if self.is_running:
            log.warning("Auto-tune already running for %s", self._view.tag)
            return

        # Read current state
        self._op_center = self._view.OUT.value
        self._sp = self._view.PV  # use current PV as the relay setpoint
        self._saved_mode = self._view.actual_mode

        log.info(
            "Auto-tune START for %s: OP_center=%.2f, SP(PV)=%.2f, d=%.1f",
            self._view.tag, self._op_center, self._sp, self._d,
        )

        # Switch to manual
        self._view.set_target_mode(Mode.Man)

        # Reset measurements
        self._zero_crossing_times.clear()
        self._pv_peaks.clear()
        self._pv_valleys.clear()
        self._prev_error_sign = 0
        self._tracking_peak = True
        self._current_extremum = self._sp
        self._start_time = time.monotonic()

        # Apply initial relay output (high side)
        self._relay_high = True
        self._write_relay_output()

        self._state = _TunerState.WAITING_INITIAL
        self.status_changed.emit(
            f"Auto-tuning {self._view.tag}: waiting for initial oscillation..."
        )
        self._timer.start()

    def abort(self, reason: str = "User cancelled") -> None:
        """Abort the relay test and restore previous mode."""
        if not self.is_running:
            return
        self._timer.stop()
        self._state = _TunerState.ABORTED

        # Restore output to center
        self._view.write_op(self._op_center)

        # Restore original mode
        if self._saved_mode is not None and self._saved_mode != Mode.Man:
            self._view.set_target_mode(self._saved_mode)

        log.info("Auto-tune ABORTED for %s: %s", self._view.tag, reason)
        self.status_changed.emit(f"Auto-tune aborted: {reason}")
        self.aborted.emit(reason)

    def _write_relay_output(self) -> None:
        """Write the current relay output level."""
        if self._relay_high:
            op = min(100.0, self._op_center + self._d)
        else:
            op = max(0.0, self._op_center - self._d)
        self._view.write_op(op)

    def _on_tick(self) -> None:
        """Called every poll_interval_ms to sample PV and update relay."""
        now = time.monotonic()

        # Safety timeout
        if now - self._start_time > self._max_duration_s:
            self.abort("Timeout: no oscillation detected within time limit")
            return

        pv = self._view.PV
        error = pv - self._sp  # positive = PV above setpoint
        error_sign = 1 if error >= 0 else -1

        # Track PV extrema
        if self._tracking_peak:
            if pv > self._current_extremum:
                self._current_extremum = pv
        else:
            if pv < self._current_extremum:
                self._current_extremum = pv

        # Detect zero crossing (PV crosses the relay setpoint)
        if self._prev_error_sign != 0 and error_sign != self._prev_error_sign:
            crossing_time = now

            # Record the extremum we were tracking
            if self._tracking_peak and len(self._zero_crossing_times) > 0:
                self._pv_peaks.append(self._current_extremum)
            elif not self._tracking_peak and len(self._zero_crossing_times) > 0:
                self._pv_valleys.append(self._current_extremum)

            self._zero_crossing_times.append(crossing_time)
            self._tracking_peak = not self._tracking_peak
            self._current_extremum = pv

            # Toggle relay
            self._relay_high = error_sign < 0  # PV below SP -> output high
            self._write_relay_output()

            n_crossings = len(self._zero_crossing_times)
            # Need 2 crossings per cycle; first crossing is initial
            n_half_cycles = n_crossings - 1

            if self._state == _TunerState.WAITING_INITIAL:
                self._state = _TunerState.RUNNING
                self.status_changed.emit(
                    f"Auto-tuning {self._view.tag}: oscillation detected, "
                    f"collecting data..."
                )

            if self._state == _TunerState.RUNNING:
                n_full_cycles = n_half_cycles // 2
                self.status_changed.emit(
                    f"Auto-tuning {self._view.tag}: "
                    f"{n_full_cycles}/{self._min_cycles} cycles complete"
                )

                # Check if we have enough data
                if n_full_cycles >= self._min_cycles:
                    self._finish()
                    return

        self._prev_error_sign = error_sign

    def _finish(self) -> None:
        """Compute tuning parameters and emit results."""
        self._timer.stop()
        self._state = _TunerState.DONE

        # Restore output to center
        self._view.write_op(self._op_center)

        # Calculate ultimate period from zero-crossing times
        # Each pair of crossings is a half-period
        crossing_times = self._zero_crossing_times
        if len(crossing_times) < 3:
            self.abort("Not enough zero crossings to compute period")
            return

        # Compute half-periods and average
        half_periods = []
        for i in range(1, len(crossing_times)):
            half_periods.append(crossing_times[i] - crossing_times[i - 1])

        # Discard the first half-period (transient) if we have enough
        if len(half_periods) > 2:
            half_periods = half_periods[1:]

        if not half_periods:
            self.abort("No valid half-periods after filtering")
            return
        avg_half_period = sum(half_periods) / len(half_periods)
        Pu = 2.0 * avg_half_period  # ultimate period (seconds)

        # Calculate PV oscillation amplitude from peaks and valleys
        if not self._pv_peaks or not self._pv_valleys:
            self.abort("Could not measure PV oscillation amplitude")
            return

        # Discard first peak/valley (transient) if possible
        peaks = self._pv_peaks[1:] if len(self._pv_peaks) > 1 else self._pv_peaks
        valleys = self._pv_valleys[1:] if len(self._pv_valleys) > 1 else self._pv_valleys

        if not peaks or not valleys:
            self.abort("Insufficient peak/valley data after transient removal")
            return
        avg_peak = sum(peaks) / len(peaks)
        avg_valley = sum(valleys) / len(valleys)
        a = (avg_peak - avg_valley) / 2.0  # half-amplitude of PV oscillation

        if a < 1e-9:
            self.abort("PV oscillation amplitude too small to compute gain")
            return

        # Ultimate gain: Ku = 4d / (pi * a)
        Ku = (4.0 * self._d) / (math.pi * a)

        log.info(
            "Auto-tune RESULTS for %s: Pu=%.2f s, a=%.4f, Ku=%.4f",
            self._view.tag, Pu, a, Ku,
        )

        # Compute tuning for all rules
        results: dict[TuningRule, dict[str, float]] = {}
        for rule, (kp_f, ti_f, td_f) in _ZN_FORMULAS.items():
            kp = kp_f * Ku
            ti = ti_f * Pu if ti_f > 0 else 9999.0
            td = td_f * Pu
            results[rule] = {"Kp": kp, "Ti": ti, "Td": td}

        result = {
            "tag": self._view.tag,
            "Ku": Ku,
            "Pu": Pu,
            "amplitude": a,
            "relay_d": self._d,
            "n_peaks": len(peaks),
            "n_valleys": len(valleys),
            "tuning_rules": results,
        }

        self.status_changed.emit(
            f"Auto-tune complete for {self._view.tag}: "
            f"Ku={Ku:.3f}, Pu={Pu:.1f}s"
        )
        self.finished.emit(result)


class AutoTuneResultsDialog(QDialog):
    """Dialog to present relay auto-tune results and let the user apply them.

    Shows ultimate gain (Ku), ultimate period (Pu), and computed tuning
    parameters for several Ziegler-Nichols variants. The user picks a rule
    and clicks Apply or Cancel.
    """

    def __init__(self, results: dict, block_view, parent=None) -> None:
        super().__init__(parent)
        self._results = results
        self._view = block_view
        self._selected_rule: TuningRule = TuningRule.ZN_CLASSIC

        tag = results["tag"]
        self.setWindowTitle(f"Auto-Tune Results -- {tag}")
        self.setMinimumWidth(420)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        r = self._results
        tag = r["tag"]

        # Header
        hdr = QLabel(f"<b>Relay Auto-Tune Results for {tag}</b>")
        hdr.setStyleSheet("font-size: 11pt; padding: 4px;")
        layout.addWidget(hdr)

        # Ultimate parameters
        info_text = (
            f"<table>"
            f"<tr><td><b>Ultimate Gain (Ku):</b></td><td>{r['Ku']:.4f}</td></tr>"
            f"<tr><td><b>Ultimate Period (Pu):</b></td><td>{r['Pu']:.2f} s</td></tr>"
            f"<tr><td><b>PV Amplitude:</b></td><td>{r['amplitude']:.4f}</td></tr>"
            f"<tr><td><b>Relay Step (d):</b></td><td>{r['relay_d']:.1f} %</td></tr>"
            f"</table>"
        )
        info_lbl = QLabel(info_text)
        info_lbl.setStyleSheet("font-size: 9pt; padding: 4px;")
        layout.addWidget(info_lbl)

        # Separator
        sep = QLabel("")
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: #808080;")
        layout.addWidget(sep)

        # Tuning rules table
        rules_hdr = QLabel("<b>Computed Tuning Parameters:</b>")
        rules_hdr.setStyleSheet("font-size: 10pt; padding: 4px 4px 2px 4px;")
        layout.addWidget(rules_hdr)

        table_text = (
            "<table border='1' cellpadding='4' cellspacing='0' "
            "style='border-collapse: collapse; font-size: 9pt;'>"
            "<tr style='background: #E0E0E0;'>"
            "<th>Rule</th><th>Kp</th><th>Ti (s)</th><th>Td (s)</th>"
            "</tr>"
        )

        rules = r["tuning_rules"]
        for rule, params in rules.items():
            kp = params["Kp"]
            ti = params["Ti"]
            td = params["Td"]
            ti_str = f"{ti:.2f}" if ti < 9000 else "---"
            td_str = f"{td:.2f}" if td > 0.001 else "0"
            table_text += (
                f"<tr><td>{rule.value}</td>"
                f"<td>{kp:.4f}</td>"
                f"<td>{ti_str}</td>"
                f"<td>{td_str}</td></tr>"
            )
        table_text += "</table>"

        table_lbl = QLabel(table_text)
        table_lbl.setStyleSheet("padding: 4px;")
        layout.addWidget(table_lbl)

        # Rule selection buttons
        select_lbl = QLabel("<b>Select rule to apply:</b>")
        select_lbl.setStyleSheet("font-size: 9pt; padding: 4px 4px 2px 4px;")
        layout.addWidget(select_lbl)

        self._rule_buttons: dict[TuningRule, QPushButton] = {}
        for rule in TuningRule:
            btn = QPushButton(rule.value)
            btn.setCheckable(True)
            btn.setChecked(rule == self._selected_rule)
            btn.clicked.connect(lambda checked, r=rule: self._select_rule(r))
            btn.setStyleSheet(
                "QPushButton { padding: 4px 8px; font-size: 9pt; }"
                "QPushButton:checked { background: #4169E1; color: white; "
                "font-weight: bold; }"
            )
            layout.addWidget(btn)
            self._rule_buttons[rule] = btn

        # Current tuning info
        current_lbl = QLabel(
            f"<i>Current tuning: Kp={self._view.gain:.4f}, "
            f"Ti={self._view.reset:.2f}s, Td={self._view.rate:.2f}s</i>"
        )
        current_lbl.setStyleSheet("font-size: 8pt; color: #606060; padding: 4px;")
        layout.addWidget(current_lbl)

        # Apply / Cancel buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        apply_btn = QPushButton("Apply")
        apply_btn.setFixedSize(80, 28)
        apply_btn.setStyleSheet(
            "QPushButton { background: #2E8B2E; color: white; "
            "font-weight: bold; border-radius: 3px; font-size: 9pt; }"
            "QPushButton:hover { background: #3E9B3E; }"
        )
        apply_btn.clicked.connect(self._apply)
        btn_row.addWidget(apply_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedSize(80, 28)
        cancel_btn.setStyleSheet(
            "QPushButton { background: #C04040; color: white; "
            "font-weight: bold; border-radius: 3px; font-size: 9pt; }"
            "QPushButton:hover { background: #D05050; }"
        )
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        layout.addLayout(btn_row)

    def _select_rule(self, rule: TuningRule) -> None:
        self._selected_rule = rule
        for r, btn in self._rule_buttons.items():
            btn.setChecked(r == rule)

    def _apply(self) -> None:
        """Write the selected tuning parameters to the controller."""
        rule = self._selected_rule
        params = self._results["tuning_rules"][rule]
        tag = self._view.tag

        kp = params["Kp"]
        ti = params["Ti"]
        td = params["Td"]

        log.info(
            "Auto-tune APPLY for %s (%s): Kp=%.4f, Ti=%.2f, Td=%.2f",
            tag, rule.value, kp, ti, td,
        )

        # Write tuning params via the block view's write_param method
        self._view.write_param("gain", kp)
        if ti < 9000:
            self._view.write_param("reset", ti)
        self._view.write_param("rate", td)

        # Switch back to Auto mode
        self._view.set_target_mode(Mode.Auto)

        self.accept()
