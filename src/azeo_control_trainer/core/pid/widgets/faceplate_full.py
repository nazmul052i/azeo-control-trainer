"""
ISA-101 PID Faceplate Widget — 3-Layer Hierarchy.

Implements ISA-101 Theme dynamo patterns with proper layering:
  Layer 1 — InlineDynamo: compact overview widget (bar graph + tag + mode)
  Layer 2 — FaceplatePopup: operator controls (SP/OP/mode), single-click from dynamo
  Layer 3 — ControllerDetailDialog: tuning/alarm/diagnostics, from faceplate button

Wired to PIDBlock (isa_pid_block.py):
  - Mode enum: OOS / IMan / LO / Man / Auto / Cas / RCas / ROut  (all 8)
  - AlarmState dataclass: hi_hi_act / hi_act / lo_act / lo_lo_act / dv_hi_act / dv_lo_act
  - BlockError dataclass: summary() → list[str], any_active()
  - LimitStatus enum: NOT_LIMITED / HIGH_LIMITED / LOW_LIMITED / NOT_CONNECTED / NOT_INVITED
  - Structure enum: all 8 structures including TWO_DOF
  - PIDForm enum: STANDARD / SERIES
  - ScaleRange: eu0 / eu100 / span
  - PIDBlock.OUT  (SignalStatus) — .value and .limit used for bar graph colouring
  - validate_pid_config() displayed live in the Status tab

Reference: ISA-101 Themes White Paper (August 2016)
"""

import collections
import logging
import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QColor, QPainter, QPen, QBrush, QFont,
    QLinearGradient, QPainterPath, QMouseEvent,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QDoubleSpinBox, QFrame,
    QDialog, QComboBox, QCheckBox, QTextEdit,
    QTabWidget, QSizePolicy, QScrollArea,
)

from ..theme.colors import (
    BG_FACEPLATE, BG_INSET, BG_DARK, BG_RAISED, BG_PANEL, BG_TREND,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_ON_DARK,
    DV_PV_BG, DV_SP_WORK, DV_PV_FG, DV_OUT_FG, DV_OUT_BG,
    DV_ALARM_BAR1, DV_ALARM_BAR2, DV_STATUS_BORDER, DV_MODULE_SELECT,
    DV_PV_LEVEL_BG,
    COLOR_PV, COLOR_SP_LIGHT, COLOR_OP,
    STATUS_AUTO, STATUS_MAN, STATUS_CAS, STATUS_OOS, STATUS_INIT,
    ALARM_CRITICAL, ALARM_WARN, ALARM_ADVISORY, ALARM_OK,
    TREND_COLORS,
)

from ..core.pid_block_core import (
    PIDBlock, Mode, PIDForm, Structure,
    LimitStatus, SignalStatus, ScaleRange,
    BlockError, AlarmState, validate_pid_config,
    LinearizationType, ShedOption, TrackOption, ProcessType,
)

log = logging.getLogger(__name__)

# ── PID parameter tooltips (shown on hover in ControllerDetailDialog) ─
_PID_TOOLTIPS: dict[str, str] = {
    # -- Tuning tab ------------------------------------------------------------
    "gain": (
        "Controller Gain (Kp)\n"
        "Multiplier on the error signal. Higher values give faster, more\n"
        "aggressive response but risk oscillation. Typical range: 0.5 \u2013 2.0.\n"
        "For standard form: OUT = Kp \u00d7 [error + (1/Ti)\u222b error dt + Td \u00d7 d(PV)/dt]"
    ),
    "reset": (
        "Integral Time \u2014 Ti (seconds/repeat)\n"
        "Time for the integral term to repeat the proportional action.\n"
        "Smaller Ti = faster integral = eliminates offset quicker, but too\n"
        "small causes integral windup and oscillation. Typical: 2 \u2013 20 s.\n"
        "Set to maximum (9999) to effectively disable integral action."
    ),
    "rate": (
        "Derivative Time \u2014 Td (seconds)\n"
        "Predictive term that responds to rate-of-change of PV.\n"
        "Higher Td = more damping on fast disturbances. Set to 0 for PI\n"
        "control only (most common). Use with caution on noisy signals.\n"
        "Filtered by Alpha parameter to limit noise amplification."
    ),
    "bias": (
        "Bias (P/PD structures only) \u2014 %\n"
        "Manual reset / bias value for proportional-only or PD structures.\n"
        "Without integral action, bias provides the steady-state output offset.\n"
        "Typical: set to expected steady-state output."
    ),
    "alpha": (
        "Derivative Filter Coefficient (\u03b1)\n"
        "First-order filter on the derivative term: Td_eff = Td / (1 + \u03b1\u00b7Td\u00b7s).\n"
        "Range: 0.05 (light filtering) to 1.0 (heavy filtering).\n"
        "Default 0.1. Increase if D-term is noisy; decrease for faster response."
    ),
    "pv_ftime": (
        "PV Input Filter Time Constant (seconds)\n"
        "First-order low-pass filter applied to the PV input before the\n"
        "PID calculation. Smooths sensor noise. 0 = no filtering.\n"
        "Typical: 0.5 \u2013 5 s. Too large adds phase lag and reduces stability."
    ),
    "sp_ftime": (
        "SP Filter Time Constant (seconds)\n"
        "First-order filter on setpoint changes. Softens SP steps into\n"
        "smooth ramps, reducing overshoot on SP changes. 0 = no filter.\n"
        "Only affects SP response; disturbance rejection is unaffected."
    ),
    "sp_rate_dn": (
        "SP Rate Limit \u2014 Decrease (EU/second)\n"
        "Maximum rate at which SP can decrease. 0 = immediate (no limit).\n"
        "Use to ramp SP down slowly, protecting equipment from thermal shock\n"
        "or pressure transients."
    ),
    "sp_rate_up": (
        "SP Rate Limit \u2014 Increase (EU/second)\n"
        "Maximum rate at which SP can increase. 0 = immediate (no limit).\n"
        "Use to ramp SP up slowly during startups or load changes."
    ),
    "ff_gain": (
        "Feedforward Gain (FF_GAIN)\n"
        "Scales the feedforward input (FF_VAL) before adding to output:\n"
        "OUT += FF_GAIN \u00d7 FF_VAL. Set to 0 to disable feedforward.\n"
        "Requires ff_enable = True."
    ),
    "ideadband": (
        "Integral Deadband (EU)\n"
        "When |error| < I-Deadband, integral action is suppressed.\n"
        "Prevents limit-cycle oscillation on processes with deadband\n"
        "(e.g., sticky valves). Set to 0 to disable."
    ),
    "beta": (
        "Two-Degree-of-Freedom \u2014 Beta (\u03b2)\n"
        "Proportional setpoint weight. Range: 0 \u2013 1.\n"
        "P-term = Kp \u00d7 (\u03b2\u00b7SP \u2212 PV) instead of Kp \u00d7 (SP \u2212 PV).\n"
        "\u03b2 = 0: No proportional kick on SP change.\n"
        "\u03b2 = 1: Full proportional response to SP changes.\n"
        "Only active when Structure = TWO_DOF."
    ),
    "gamma": (
        "Two-Degree-of-Freedom \u2014 Gamma (\u03b3)\n"
        "Derivative setpoint weight. Range: 0 \u2013 1.\n"
        "\u03b3 = 0: Derivative on PV only (no SP kick \u2014 most common).\n"
        "\u03b3 = 1: Derivative on full error.\n"
        "Only active when Structure = TWO_DOF."
    ),
    "form": (
        "PID Equation Form\n"
        "Standard (ISA): OUT = Kp \u00d7 [e + (1/Ti)\u222be dt + Td\u00b7de/dt]\n"
        "  \u2192 Gain affects all three terms simultaneously.\n"
        "Series (interacting): OUT = Kp \u00d7 (1 + 1/Ti\u00b7s) \u00d7 (1 + Td\u00b7s)\n"
        "  \u2192 I and D are decoupled; changing one doesn't affect the other."
    ),
    "structure": (
        "PID Structure \u2014 where P, I, D terms act\n"
        "PID_ON_ERROR: All terms act on error (SP\u2212PV). Fast SP response.\n"
        "PI_ERROR_D_PV: P+I on error, D on PV only. Prevents derivative kick.\n"
        "I_ERROR_PD_PV: I on error, P+D on PV. Smoothest SP response.\n"
        "TWO_DOF: Uses Beta/Gamma weights for independent SP vs disturbance tuning."
    ),
    # -- Limits tab -- Alarm limits --------------------------------------------
    "hi_hi_lim": (
        "High-High Alarm Limit (Critical)\n"
        "Triggers a critical (red) alarm when PV exceeds this value.\n"
        "Set above Hi Lim with adequate margin. Use for safety-critical\n"
        "conditions requiring immediate operator action.\n"
        "Set to +INF to disable."
    ),
    "hi_lim": (
        "High Alarm Limit (Warning)\n"
        "Triggers a warning (yellow) alarm when PV exceeds this value.\n"
        "Set below Hi-Hi Lim. Gives operator early warning before\n"
        "critical condition is reached.\n"
        "Set to +INF to disable."
    ),
    "dv_hi_lim": (
        "Deviation High Alarm Limit\n"
        "Triggers when PV > SP + DevHi. Detects loss of control when\n"
        "PV deviates too far above setpoint. Moves with SP.\n"
        "Typical: 5-15% of PV span."
    ),
    "dv_lo_lim": (
        "Deviation Low Alarm Limit\n"
        "Triggers when PV < SP + DevLo (DevLo is typically negative).\n"
        "Detects loss of control when PV deviates too far below setpoint.\n"
        "Moves with SP. Typical: -15 to -5% of PV span."
    ),
    "lo_lim": (
        "Low Alarm Limit (Warning)\n"
        "Triggers a warning (yellow) alarm when PV drops below this value.\n"
        "Set above Lo-Lo Lim. Gives operator early warning.\n"
        "Set to -INF to disable."
    ),
    "lo_lo_lim": (
        "Low-Low Alarm Limit (Critical)\n"
        "Triggers a critical (red) alarm when PV drops below this value.\n"
        "Set below Lo Lim. Use for safety-critical low conditions.\n"
        "Set to -INF to disable."
    ),
    "alarm_hys": (
        "Alarm Hysteresis (deadband)\n"
        "PV must return past the alarm limit by this amount before the\n"
        "alarm clears. Prevents alarm chattering when PV oscillates\n"
        "near a limit. Typical: 1-2% of PV span."
    ),
    # -- Limits tab -- Output / SP / ARW limits --------------------------------
    "out_hi_lim": (
        "Output High Limit (%)\n"
        "Maximum controller output. Clamps OUT to prevent over-driving\n"
        "the final control element. Typical: 100%. Reduce to protect\n"
        "equipment (e.g., 90% max valve opening)."
    ),
    "out_lo_lim": (
        "Output Low Limit (%)\n"
        "Minimum controller output. Clamps OUT to maintain a minimum\n"
        "flow or position. Typical: 0%. Raise for processes requiring\n"
        "minimum flow (e.g., 10% minimum air damper)."
    ),
    "sp_hi_lim": (
        "Setpoint High Limit (EU)\n"
        "Maximum allowed setpoint value. Prevents operator from setting\n"
        "SP above safe operating range. Applied in Auto and Cas modes."
    ),
    "sp_lo_lim": (
        "Setpoint Low Limit (EU)\n"
        "Minimum allowed setpoint value. Prevents operator from setting\n"
        "SP below safe operating range. Applied in Auto and Cas modes."
    ),
    "arw_hi_lim": (
        "Anti-Reset-Windup High Limit (%)\n"
        "Stops integral accumulation when output exceeds this level.\n"
        "Prevents integral windup during saturation. Set equal to or\n"
        "slightly above Out Hi Lim. Default: same as Out Hi Lim."
    ),
    "arw_lo_lim": (
        "Anti-Reset-Windup Low Limit (%)\n"
        "Stops integral accumulation when output drops below this level.\n"
        "Prevents integral windup during saturation. Set equal to or\n"
        "slightly below Out Lo Lim. Default: same as Out Lo Lim."
    ),
    # -- Options tab -- FRSIPID_OPTS -------------------------------------------
    "use_pidplus": (
        "PIDPlus Algorithm\n"
        "Enables automatic gain adaptation based on PV noise characteristics.\n"
        "Dynamically adjusts effective gain for optimal response across\n"
        "varying process conditions."
    ),
    "use_nonlinear_gain": (
        "Nonlinear Gain Modification\n"
        "Applies a variable gain schedule based on error magnitude:\n"
        "Inside NL_GAP: gain reduced to Kp x NL_MINMOD\n"
        "Inside NL_TBAND: gain transitions linearly\n"
        "Outside NL_TBAND: full gain Kp applied"
    ),
    "dynamic_reset_limit": (
        "Dynamic Reset Limiting (DRL)\n"
        "Limits integral action based on what the downstream block can\n"
        "actually accept (via BKCAL_IN feedback). Prevents integral\n"
        "windup in cascade configurations."
    ),
    "use_delayed_out_bad_pv": (
        "Delayed Output on Bad PV\n"
        "When PV goes BAD, holds the output for one scan at last good\n"
        "value, then slowly ramps output. Prevents sudden output jumps\n"
        "on transient sensor failures."
    ),
    # -- Options tab -- CONTROL_OPTS -------------------------------------------
    "direct_acting": (
        "Controller Action Direction\n"
        "Direct Acting: Output INCREASES when PV INCREASES.\n"
        "  Example: cooling valve (more PV heat -> open valve more)\n"
        "Reverse Acting (unchecked): Output INCREASES when PV DECREASES.\n"
        "Most process controllers are reverse-acting."
    ),
    "increase_to_close": (
        "Increase to Close (I/O Option)\n"
        "Inverts the output signal: IO_OUT = (EU100 + EU0 - OUT).\n"
        "Used for fail-open (air-to-close) actuators. The PID still\n"
        "calculates in normal direction; only the final I/O output\n"
        "is inverted. Requires OOS to change."
    ),
    "bypass": (
        "Bypass Mode\n"
        "When enabled, PID calculation is bypassed and PV is passed\n"
        "directly to the output (OUT = PV scaled to output range).\n"
        "Used for open-loop testing. Requires bypass_enable = True."
    ),
    "bypass_enable": (
        "Bypass Enable\n"
        "Master enable for the Bypass flag. Both bypass_enable AND bypass\n"
        "must be True for bypass to activate. Two-step requirement\n"
        "prevents accidental bypass activation."
    ),
    "sp_pv_track_lo_iman": (
        "SP Tracks PV in LO/IMan Modes\n"
        "When in Local Override (LO) or Initialization Manual (IMan),\n"
        "SP is set equal to PV. Ensures bumpless transfer when\n"
        "transitioning back to Auto/Cas mode."
    ),
    "sp_pv_track_man": (
        "SP Tracks PV in Manual Mode\n"
        "When in Manual, SP is continuously set equal to PV.\n"
        "Provides bumpless transfer from Manual to Auto.\n"
        "Disable if you want SP to hold its value during manual."
    ),
    "sp_pv_track_rout": (
        "SP Tracks PV in Remote Output Mode\n"
        "When in ROut mode, SP tracks PV. Ensures smooth transition\n"
        "when switching from remote output back to local control modes."
    ),
    "use_pv_for_bkcal_out": (
        "Use PV for Back-Calculation Output\n"
        "BKCAL_OUT = PV instead of SP. Required in cascade configs\n"
        "where the upstream controller needs the actual process value,\n"
        "not the setpoint, for proper anti-windup tracking."
    ),
    "no_out_limits_in_man": (
        "No Output Limits in Manual Mode\n"
        "Disables Out Hi/Lo Lim clamping when in Manual mode, allowing\n"
        "the operator to drive output through the full 0-100% range.\n"
        "Useful for commissioning and testing actuator full stroke."
    ),
    "obey_sp_lim_cas_rcas": (
        "Obey SP Limits in Cascade/Remote Cascade\n"
        "When enabled, SP Hi/Lo Lim are enforced even when SP is being\n"
        "written by a cascade master or remote host."
    ),
    "track_enable": (
        "Tracking Enable\n"
        "Enables external tracking: when TRK_IN_D is True, the output\n"
        "follows TRK_VAL. Used for override control, interlocks, or\n"
        "external output forcing."
    ),
    "track_in_manual": (
        "Track in Manual Mode\n"
        "Allows the tracking function to override the output even when\n"
        "the block is in Manual mode. Normally, Manual mode gives the\n"
        "operator exclusive output control."
    ),
    "ff_enable": (
        "Feedforward Enable\n"
        "Enables the feedforward path: OUT += FF_GAIN x FF_VAL.\n"
        "Use for measurable disturbances. FF_GAIN scales the contribution.\n"
        "Disable to remove feedforward without changing FF_GAIN."
    ),
    "simulate_enabled": (
        "Simulation Mode\n"
        "When enabled, PV reads from SIMULATE_IN instead of the real\n"
        "process input (IN). Used for offline testing, loop checkout,\n"
        "and training without affecting the live process."
    ),
    # -- Options tab -- Nonlinear gain parameters ------------------------------
    "nl_gap": (
        "Nonlinear Gain -- Gap (EU)\n"
        "Error band centered on zero where gain is reduced to\n"
        "Kp x NL_MINMOD. When |error| < NL_GAP, the controller\n"
        "uses reduced gain to prevent limit cycling near setpoint.\n"
        "Set to 0 to disable. Typical: 1-3x noise amplitude."
    ),
    "nl_hyst": (
        "Nonlinear Gain -- Hysteresis (EU)\n"
        "Prevents rapid switching between reduced and full gain as\n"
        "error crosses the gap/tband boundaries. Adds deadband to\n"
        "the gain transition. Typical: 0.5x NL_GAP."
    ),
    "nl_tband": (
        "Nonlinear Gain -- Transition Band (EU)\n"
        "Error range between NL_GAP and NL_TBAND where gain linearly\n"
        "transitions from NL_MINMOD x Kp to full Kp. Must be >= NL_GAP.\n"
        "Wider band = smoother gain transition."
    ),
    "nl_minmod": (
        "Nonlinear Gain -- Minimum Gain Multiplier (0-1)\n"
        "Gain inside the NL_GAP = Kp x NL_MINMOD.\n"
        "0.0 = zero gain inside gap (integral only).\n"
        "0.5 = half gain inside gap.\n"
        "1.0 = full gain everywhere (disables nonlinear effect)."
    ),
    # -- Options tab -- PIDPlus recovery ---------------------------------------
    "recovery_fltr": (
        "PIDPlus Saturation Recovery Filter (0-1)\n"
        "Controls how aggressively the PIDPlus algorithm exits output\n"
        "saturation. 0 = aggressive (fast recovery, risk of overshoot).\n"
        "1 = standard (conservative recovery). Default: 0.5."
    ),
    # -- New Azeo parameters -------------------------------------------------
    "l_type": (
        "Linearization Type (L_TYPE)\n"
        "Direct: No linearization -- PV passes through as-is.\n"
        "Indirect: Square root linearization for flow transmitters.\n"
        "IndirectSquareRoot: Inverse square root.\n"
        "Applied before PV filtering in the execution pipeline."
    ),
    "low_cut": (
        "Low Cutoff Value (LOW_CUT) -- PV EU\n"
        "When Low Cutoff IO option is enabled and the converted PV\n"
        "falls below this value, PV is forced to 0.0.\n"
        "Critical for zero-based flow sensors. Typical: 2-5% of PV span."
    ),
    "low_cutoff_enabled": (
        "Low Cutoff IO Option\n"
        "Enables the LOW_CUT feature. When enabled, any PV reading\n"
        "below LOW_CUT is forced to exactly 0.0.\n"
        "Use with flow transmitters to eliminate low-flow noise."
    ),
    "shed_opt": (
        "Shed Option (SHED_OPT)\n"
        "Action taken when RCAS_IN or ROUT_IN communication times out:\n"
        "  No Action: Do nothing (remote host handles recovery)\n"
        "  Normal: Shed to next lower mode (RCas->Cas, ROut->Auto)\n"
        "  Manual: Shed directly to Manual mode\n"
        "  Auto: Shed to Automatic mode"
    ),
    "shed_time": (
        "Shed Time (SHED_TIME) -- seconds\n"
        "Timeout duration for RCAS_IN / ROUT_IN updates. If no new\n"
        "value is received within this time, the block sheds per\n"
        "SHED_OPT. Set to 0 to disable timeout checking.\n"
        "Typical: 10-60 seconds."
    ),
    "bad_if_limited": (
        "STATUS_OPTS: Bad if Limited\n"
        "Sets PV status to BAD when the PV input is at a limit.\n"
        "Use when a limited PV reading is unreliable (e.g., sensor\n"
        "pegged at 0% or 100% indicates a wiring fault)."
    ),
    "uncertain_if_limited": (
        "STATUS_OPTS: Uncertain if Limited\n"
        "Sets PV status to UNCERTAIN when PV input is at a limit.\n"
        "Less severe than Bad if Limited -- the reading may still\n"
        "be usable but confidence is reduced."
    ),
    "target_manual_if_bad_in": (
        "STATUS_OPTS: Target Manual if Bad IN\n"
        "When PV goes BAD, latches the TARGET mode to Manual.\n"
        "Prevents automatic return to Auto when PV recovers --\n"
        "operator must explicitly request Auto mode."
    ),
    "use_uncertain_as_good": (
        "STATUS_OPTS: Use Uncertain as Good\n"
        "Treats UNCERTAIN PV status as GOOD for mode resolution.\n"
        "The block stays in Auto/Cas even with uncertain PV."
    ),
    "bal_time": (
        "Balance Time (BAL_TIME) -- seconds\n"
        "For P and PD structures (no integral term), the manual\n"
        "bias is dissipated exponentially over BAL_TIME seconds\n"
        "after switching to Auto. Set to 0 for immediate bias removal.\n"
        "Typical: 30-120 s."
    ),
    "condalm_enabled": (
        "Conditional Alarm Enable (CONDALM_ENABLED)\n"
        "Master gate for all alarm evaluation. When disabled (False),\n"
        "no alarms are evaluated or reported. Use during commissioning\n"
        "or when the block is in an abnormal but expected condition."
    ),
    "track_opt": (
        "Tracking Option (TRACK_OPT)\n"
        "Controls behavior when TRK_VAL signal has bad quality:\n"
        "  Always Use Value: Use TRK_VAL regardless of quality\n"
        "  Use Last Good Value: Hold last known-good TRK_VAL\n"
        "  Track if Bad: Activate tracking when signal is bad\n"
        "Only relevant when Track Enable is True."
    ),
    "process_type": (
        "Process Type (PROCESS_TYPE)\n"
        "Metadata classification of the controlled process:\n"
        "  Self-Regulating: Process reaches steady state on its own\n"
        "  Integrating: Process integrates -- no natural steady state\n"
        "Informational only -- does not change PID algorithm behavior."
    ),
    "sp_track_retained_target": (
        "SP Track -- Retain Target Mode\n"
        "When SP-PV tracking is active (in Man/LO/IMan), this option\n"
        "preserves the original target mode. When tracking releases,\n"
        "the block returns to the previously targeted mode."
    ),
    "act_on_ir": (
        "ACT_ON_IR -- Bias Adjustment on Transfer\n"
        "For P and PD structures: automatically adjusts the internal\n"
        "bias value for bumpless transfer when transitioning from\n"
        "Manual to Auto."
    ),
    "stdev_time": (
        "STDEV Window (STDEV_TIME) -- seconds\n"
        "Time window over which the running standard deviation is\n"
        "calculated. Longer windows give more stable readings.\n"
        "Set to 0 to disable. Typical: 30-120 seconds."
    ),
    "stdev_cap": (
        "STDEV Cap (STDEV_CAP)\n"
        "Maximum value for the STDEV calculation. Prevents the\n"
        "variability metric from being dominated by large transients.\n"
        "Typical: 1-2x expected normal variability."
    ),
    "var_idx_lim": (
        "Variability Index Limit (VAR_IDX_LIM) -- %\n"
        "Threshold for abnormal variability detection. When VAR_IDX\n"
        "exceeds this percentage, the loop is flagged as abnormal.\n"
        "VAR_IDX = (STDEV / STDEV_CAP) x 100%. Typical: 75-90%.\n"
        "Set to 100 to effectively disable abnormality detection."
    ),
}

# ── Palette ───────────────────────────────────────────────────────────
_DYNAMO_BG         = BG_FACEPLATE
_PV_LOOP_BG        = DV_PV_BG
_SP_WORK           = DV_SP_WORK
_PV_FG             = DV_PV_FG
_OUT_FG            = DV_OUT_FG
_OUT_BG            = DV_OUT_BG
_TAG_COLOR         = TEXT_SECONDARY
_VALUE_COLOR       = TEXT_PRIMARY
_BORDER_NORMAL     = DV_ALARM_BAR1
_BORDER_STATUS     = DV_STATUS_BORDER
_BORDER_ALARM_CRIT = ALARM_CRITICAL
_BORDER_ALARM_WARN = ALARM_WARN
_ALARM_LIMIT_COLOR = "#B8B8C0"
_LIMIT_COLOR       = "#FF8C00"      # orange — OUT at limit

# ── Mode display map (Mode enum → short label shown in UI) ────────────
_MODE_DISPLAY: dict[Mode, str] = {
    Mode.OOS:  "OOS",
    Mode.IMan: "IMAN",
    Mode.LO:   "LO",
    Mode.Man:  "MAN",
    Mode.Auto: "AUTO",
    Mode.Cas:  "CAS",
    Mode.RCas: "RCAS",
    Mode.ROut:  "ROUT",
}
_DISPLAY_MODE: dict[str, Mode] = {v: k for k, v in _MODE_DISPLAY.items()}

# Modes the operator can request (CAS/AUTO/MAN buttons in faceplate)
_OPERATOR_MODES = [Mode.Cas, Mode.Auto, Mode.Man]


# =====================================================================
# CombinationBarGraph
# =====================================================================
class CombinationBarGraph(QWidget):
    """ISA-101 / Azeo-style vertical PV/SP combination bar graph.

    Features:
      - Wide bar with gradient fill (Azeo PV blue)
      - Scale graduation tick marks at 0/25/50/75/100%
      - SP target indicator (horizontal line + left triangle)
      - Alarm limit dashes
      - Orange tint when OUT is at a hard limit
    """

    def __init__(self, pv_color: str = _PV_FG, lo: float = 0.0,
                 hi: float = 100.0, parent=None) -> None:
        super().__init__(parent)
        self._value: float = 50.0
        self._lo = lo
        self._hi = hi
        self._pv_color = QColor(pv_color)
        self._alarm_hi: float = hi
        self._alarm_lo: float = lo
        self._sp_target: float | None = None
        self._limit_status: LimitStatus = LimitStatus.NOT_LIMITED
        self.setFixedWidth(36)
        self.setMinimumHeight(100)

    def set_value(self, v: float) -> None:
        self._value = max(self._lo, min(self._hi, v))
        self.update()

    def set_alarm_bands(self, lo: float, hi: float) -> None:
        self._alarm_lo = lo
        self._alarm_hi = hi
        self.update()

    def set_sp_target(self, sp: float | None) -> None:
        self._sp_target = sp
        self.update()

    def set_limit_status(self, status: LimitStatus) -> None:
        """Orange tint on bar when PID OUT is at a hard limit."""
        if status != self._limit_status:
            self._limit_status = status
            self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        mx, my = 3, 4
        bx, by = mx, my
        bw = w - 2 * mx
        bh = h - 2 * my

        # Background — Azeo tan
        p.fillRect(bx, by, bw, bh, QColor(_PV_LOOP_BG))
        p.setPen(QPen(QColor(_BORDER_NORMAL), 1))
        p.drawRect(bx, by, bw - 1, bh - 1)

        rng = max(1e-6, self._hi - self._lo)

        # Scale graduation tick marks (0%, 25%, 50%, 75%, 100%)
        tick_pen = QPen(QColor("#B8B8C0"), 1, Qt.SolidLine)
        for pct in (0.25, 0.50, 0.75):
            ty = int(by + bh - pct * bh)
            p.setPen(tick_pen)
            p.drawLine(bx + 1, ty, bx + 5, ty)
            p.drawLine(bx + bw - 6, ty, bx + bw - 2, ty)

        # Alarm limit dashes
        for lv in (self._alarm_lo, self._alarm_hi):
            frac = max(0.0, min(1.0, (lv - self._lo) / rng))
            if 0.01 < frac < 0.99:
                ly = int(by + bh - frac * bh)
                p.setPen(QPen(QColor(ALARM_WARN), 1, Qt.DashLine))
                p.drawLine(bx + 1, ly, bx + bw - 2, ly)

        # PV bar fill — Azeo-style gradient
        pv_frac = max(0.0, min(1.0, (self._value - self._lo) / rng))
        fill_h = int(bh * pv_frac)
        in_alarm = self._value > self._alarm_hi or self._value < self._alarm_lo
        fill_color = QColor(ALARM_CRITICAL) if in_alarm else QColor(self._pv_color)

        if fill_h > 0:
            fill_top = by + bh - fill_h
            # Horizontal gradient for 3D depth effect
            grad = QLinearGradient(bx, 0, bx + bw, 0)
            lighter = QColor(fill_color).lighter(130)
            lighter.setAlpha(210)
            grad.setColorAt(0.0, lighter)
            grad.setColorAt(0.35, fill_color)
            grad.setColorAt(0.65, fill_color)
            grad.setColorAt(1.0, lighter)
            p.fillRect(bx + 1, fill_top, bw - 2, fill_h - 1, QBrush(grad))
            # Limit tint (top 4 px)
            if self._limit_status in (LimitStatus.HIGH_LIMITED,
                                       LimitStatus.LOW_LIMITED):
                p.fillRect(bx + 1, fill_top, bw - 2, min(4, fill_h),
                           QColor(_LIMIT_COLOR))

        # SP indicator — horizontal line + left triangle (Azeo style)
        if self._sp_target is not None:
            sp_frac = max(0.0, min(1.0, (self._sp_target - self._lo) / rng))
            sp_y = int(by + bh - sp_frac * bh)
            sp_color = QColor(_SP_WORK)
            # Thick SP line
            p.setPen(QPen(sp_color, 2))
            p.drawLine(bx + 1, sp_y, bx + bw - 2, sp_y)
            # Left triangle pointer
            p.setBrush(sp_color)
            p.setPen(Qt.NoPen)
            path = QPainterPath()
            path.moveTo(bx + 6, sp_y)
            path.lineTo(bx, sp_y - 4)
            path.lineTo(bx, sp_y + 4)
            path.closeSubpath()
            p.drawPath(path)
            # Right triangle pointer
            path2 = QPainterPath()
            path2.moveTo(bx + bw - 7, sp_y)
            path2.lineTo(bx + bw - 1, sp_y - 4)
            path2.lineTo(bx + bw - 1, sp_y + 4)
            path2.closeSubpath()
            p.drawPath(path2)

        p.end()


# =====================================================================
# OutBarGraph
# =====================================================================
class OutBarGraph(QWidget):
    """ISA-101 / Azeo-style vertical OUT bar graph.

    Features:
      - Wide bar with gradient fill (Azeo teal-green)
      - Scale graduation tick marks at 0/25/50/75/100%
      - Orange fill when OUT is at a hard limit
    """

    def __init__(self, lo: float = 0.0, hi: float = 100.0, parent=None) -> None:
        super().__init__(parent)
        self._value: float = 50.0
        self._lo = lo
        self._hi = hi
        self._limit_status: LimitStatus = LimitStatus.NOT_LIMITED
        self.setFixedWidth(30)
        self.setMinimumHeight(60)

    def set_value(self, v: float) -> None:
        self._value = max(self._lo, min(self._hi, v))
        self.update()

    def set_limit_status(self, status: LimitStatus) -> None:
        if status != self._limit_status:
            self._limit_status = status
            self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        m = 3
        bx, by, bw, bh = m, m, w - 2*m, h - 2*m

        # Background — Azeo light green
        p.fillRect(bx, by, bw, bh, QColor(_OUT_BG))
        p.setPen(QPen(QColor(_BORDER_NORMAL), 1))
        p.drawRect(bx, by, bw - 1, bh - 1)

        # Scale graduation tick marks
        tick_pen = QPen(QColor("#9BAAA0"), 1, Qt.SolidLine)
        for pct in (0.25, 0.50, 0.75):
            ty = int(by + bh - pct * bh)
            p.setPen(tick_pen)
            p.drawLine(bx + 1, ty, bx + 4, ty)
            p.drawLine(bx + bw - 5, ty, bx + bw - 2, ty)

        rng = max(1e-6, self._hi - self._lo)
        frac = max(0.0, min(1.0, (self._value - self._lo) / rng))
        fill_h = int(bh * frac)
        if fill_h > 0:
            fill_top = by + bh - fill_h
            at_limit = self._limit_status in (LimitStatus.HIGH_LIMITED,
                                               LimitStatus.LOW_LIMITED)
            base_clr = QColor(_LIMIT_COLOR) if at_limit else QColor(_OUT_FG)
            # Horizontal gradient for 3D depth
            grad = QLinearGradient(bx, 0, bx + bw, 0)
            lighter = QColor(base_clr).lighter(130)
            lighter.setAlpha(210)
            grad.setColorAt(0.0, lighter)
            grad.setColorAt(0.35, base_clr)
            grad.setColorAt(0.65, base_clr)
            grad.setColorAt(1.0, lighter)
            p.fillRect(bx + 1, fill_top, bw - 2, fill_h - 1, QBrush(grad))
        p.end()


# =====================================================================
# HorizontalOutBar
# =====================================================================
class HorizontalOutBar(QWidget):
    """Horizontal OUT bar graph with range labels, Azeo style."""

    def __init__(self, lo: float = 0.0, hi: float = 100.0, parent=None) -> None:
        super().__init__(parent)
        self._value: float = 50.0
        self._lo = lo
        self._hi = hi
        self._limit_status: LimitStatus = LimitStatus.NOT_LIMITED
        self.setFixedHeight(22)
        self.setMinimumWidth(80)

    def set_value(self, v: float) -> None:
        self._value = max(self._lo, min(self._hi, v))
        self.update()

    def set_limit_status(self, status: LimitStatus) -> None:
        if status != self._limit_status:
            self._limit_status = status
            self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        m = 1
        bx, by, bw, bh = m, m, w - 2 * m, h - 2 * m
        p.fillRect(bx, by, bw, bh, QColor(_OUT_BG))
        p.setPen(QPen(QColor(_BORDER_NORMAL), 1))
        p.drawRect(bx, by, bw - 1, bh - 1)
        rng = max(1e-6, self._hi - self._lo)
        frac = max(0.0, min(1.0, (self._value - self._lo) / rng))
        fill_w = int(bw * frac)
        if fill_w > 0:
            at_limit = self._limit_status in (LimitStatus.HIGH_LIMITED,
                                               LimitStatus.LOW_LIMITED)
            base_clr = QColor(_LIMIT_COLOR) if at_limit else QColor(_OUT_FG)
            # Vertical gradient for 3D depth
            grad = QLinearGradient(0, by, 0, by + bh)
            lighter = QColor(base_clr).lighter(130)
            lighter.setAlpha(210)
            grad.setColorAt(0.0, lighter)
            grad.setColorAt(0.4, base_clr)
            grad.setColorAt(0.6, base_clr)
            grad.setColorAt(1.0, lighter)
            p.fillRect(bx + 1, by + 1, fill_w - 1, bh - 2, QBrush(grad))
        p.end()


# =====================================================================
# DigitalReadout — unchanged
# =====================================================================
class DigitalReadout(QWidget):
    """Azeo-style digital readout — large value with label header."""

    def __init__(self, value: float = 0.0, decimals: int = 1,
                 label: str = "", parent=None) -> None:
        super().__init__(parent)
        self._value = value
        self._decimals = decimals
        self._label = label
        self._alarm = False
        self._font = QFont("Consolas", 13, QFont.Bold)
        self._label_font = QFont("Segoe UI", 8, QFont.Bold)
        self.setFixedHeight(40)
        self.setMinimumWidth(80)

    def set_value(self, v: float) -> None:
        self._value = v
        self.update()

    def set_alarm(self, active: bool) -> None:
        self._alarm = active
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # Background with subtle inset border (Azeo style)
        p.fillRect(0, 0, w, h, QColor(_PV_LOOP_BG))
        p.setPen(QPen(QColor(_BORDER_NORMAL), 1))
        p.drawRect(0, 0, w - 1, h - 1)

        # Label header (top-left)
        if self._label:
            p.setFont(self._label_font)
            p.setPen(QColor(_TAG_COLOR))
            p.drawText(4, 2, w - 8, 14, Qt.AlignLeft | Qt.AlignTop, self._label)

        # Large value (bottom-right, Azeo prominent display)
        fg = QColor(ALARM_CRITICAL) if self._alarm else QColor(_VALUE_COLOR)
        p.setFont(self._font)
        p.setPen(fg)
        p.drawText(4, 14, w - 8, h - 14,
                   Qt.AlignRight | Qt.AlignVCenter,
                   f"{self._value:.{self._decimals}f}")
        p.end()


# =====================================================================
# ModeIndicator — all 8 PIDBlock modes with distinct colours
# =====================================================================
class ModeIndicator(QLabel):
    """ISA mode badge for all 8 PIDBlock Mode values.

    AUTO  — muted, no background (normal)
    CAS   — blue (cascade running)
    RCAS  — blue (remote cascade)
    MAN   — orange-red (operator manual — abnormal)
    ROUT  — teal (remote output)
    LO    — grey (local override / tracking)
    IMAN  — dark blue (cascade initializing)
    OOS   — dark red (out of service)
    """

    _STYLE: dict[str, tuple[str, str, bool]] = {
        # label: (fg_color, bg_color, bold)
        "AUTO": (DV_ALARM_BAR2,  "transparent",   False),
        "CAS":  ("white",         DV_STATUS_BORDER, True),
        "RCAS": ("white",         DV_STATUS_BORDER, True),
        "MAN":  ("white",         DV_MODULE_SELECT,  True),
        "ROUT": ("white",         "#009999",         True),
        "LO":   ("white",         "#808080",         True),
        "IMAN": ("white",         "#003A6B",         True),
        "OOS":  ("white",         "#8B0000",         True),
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._mode_str = "AUTO"
        self.setFixedHeight(16)
        self.setFixedWidth(46)
        self.setAlignment(Qt.AlignCenter)
        self._update_display()

    def set_mode(self, mode: str | Mode) -> None:
        if isinstance(mode, Mode):
            self._mode_str = _MODE_DISPLAY.get(mode, mode.name)
        else:
            self._mode_str = str(mode).upper()
        self._update_display()

    def _update_display(self) -> None:
        m = self._mode_str
        fg, bg, bold = self._STYLE.get(m, ("white", "#555555", True))
        self.setText(m)
        self.setStyleSheet(
            f"QLabel {{ color: {fg}; font-size: 7pt; "
            f"{'font-weight: bold; ' if bold else ''}"
            f"background-color: {bg}; border: none; "
            f"border-radius: 2px; padding: 0px 2px; }}"
        )


# =====================================================================
# ModeLamp — unchanged
# =====================================================================
class ModeLamp(QPushButton):
    """Mode button with active/inactive glow styling."""

    def __init__(self, label: str, color: str, parent=None) -> None:
        super().__init__(label, parent)
        self._color = color
        self._active = False
        self.setFixedSize(62, 28)
        self.setCheckable(True)
        self.setAutoDefault(False)
        self.setDefault(False)
        self._update_style()
        self.toggled.connect(self._on_toggle)

    def set_active(self, active: bool) -> None:
        if active != self._active:
            self._active = active
            self.setChecked(active)
            self._update_style()

    def _on_toggle(self, checked: bool) -> None:
        self._active = checked
        self._update_style()

    def _update_style(self) -> None:
        if self._active:
            c = QColor(self._color)
            glow = c.lighter(130).name()
            self.setStyleSheet(
                f"QPushButton {{"
                f"  background-color: qlineargradient(y1:0,y2:1,stop:0 {glow},stop:1 {self._color});"
                f"  color: white; border: 2px solid {c.darker(140).name()};"
                f"  border-radius: 3px; font-size: 9pt; font-weight: bold; padding-bottom: 1px; }}"
            )
        else:
            self.setStyleSheet(
                f"QPushButton {{"
                f"  background-color: {BG_INSET}; color: {DV_ALARM_BAR2};"
                f"  border: 1px solid {DV_ALARM_BAR1};"
                f"  border-radius: 3px; font-size: 9pt; font-weight: bold; }}"
            )


# =====================================================================
# LAYER 3 — Controller Detail Dialog
# =====================================================================
class ControllerDetailDialog(QDialog):
    """PID function block detail dialog.

    Four tabs:
      Tuning  — Gain / Reset / Rate / Alpha / PV-Filter / SP-Filter /
                SP Rate Dn & Up / FF Gain / I-Deadband /
                PID Form combo / Structure combo /
                Two-DoF Beta & Gamma (grayed out unless TWO_DOF selected)
      Limits  — Alarm limits (HiHi/Hi/DevHi/DevLo/Lo/LoLo)
                Output / SP / ARW limits
      Options — FRSIPID_OPTS checkboxes (PIDPlus, NL Gain, DRL, Delayed OUT)
                CONTROL_OPTS checkboxes (DirectAct, Bypass, SP-PV Track, etc.)
                NL tuning spinboxes (NL_GAP / NL_HYST / NL_TBAND / NL_MINMOD)
                PIDPlus RECOVERY_FLTR
      Status  — Actual/Target mode, OUT limit, BKCAL_OUT, BlockErrors,
                AlarmState fields, integrator value, config warnings,
                full mode-change buttons (OOS -> ROut)

    Signals:
      param_changed(str, float)    — any spinbox / combo changed
      flag_changed(str, bool)      — any checkbox changed
      mode_change_requested(Mode)  — mode button pressed in Status tab
    """

    param_changed         = Signal(str, float)
    flag_changed          = Signal(str, bool)
    mode_change_requested = Signal(object)   # Mode enum

    # ---- legacy compat -----------------------------------------------
    kp_changed          = Signal(float)
    ti_changed          = Signal(float)
    alarm_limits_changed = Signal(dict)

    # Parameters that require OOS mode to change (configuration params)
    _OOS_ONLY_PARAMS = frozenset({
        # Tuning tab — equation form / structure
        "form", "structure",
        # Options tab — FRSIPID_OPTS
        "use_pidplus", "use_nonlinear_gain", "dynamic_reset_limit",
        "use_delayed_out_bad_pv",
        # Options tab — CONTROL_OPTS (structural / wiring)
        "direct_acting", "increase_to_close",
        "use_pv_for_bkcal_out", "no_out_limits_in_man",
        "obey_sp_lim_cas_rcas",
        "sp_pv_track_lo_iman", "sp_pv_track_man", "sp_pv_track_rout",
        "track_in_manual", "sp_track_retained_target", "act_on_ir",
        # Options tab — Linearization / Shedding / Tracking combos
        "l_type", "low_cutoff_enabled",
        "shed_opt", "track_opt",
        "process_type",
    })
    # Parameters that require Manual or OOS to change
    _MAN_OR_OOS_PARAMS = frozenset({
        "simulate_enabled", "bypass",
    })
    # Parameters that require Bypass active or OOS to change
    _BYPASS_OR_OOS_PARAMS = frozenset({
        "bypass_enable",
        # Alarm / status options only changeable under bypass or OOS
        "bad_if_limited", "uncertain_if_limited",
        "target_manual_if_bad_in", "use_uncertain_as_good",
        "condalm_enabled",
        # Feedforward / track wiring
        "ff_enable", "track_enable",
    })

    def __init__(
        self,
        tag: str,
        description: str,
        sp_lo: float, sp_hi: float, sp_units: str,
        op_lo: float, op_hi: float, op_units: str,
        decimals: int,
        alarm_limits: dict | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._tag = tag
        self._desc = description
        self._sp_lo, self._sp_hi = sp_lo, sp_hi
        self._op_lo, self._op_hi = op_lo, op_hi
        self._sp_units = sp_units
        self._op_units = op_units
        self._decimals = decimals
        self._alarm_limits = dict(alarm_limits) if alarm_limits else {}
        self._spins:  dict[str, QDoubleSpinBox] = {}
        self._checks: dict[str, QCheckBox]      = {}
        self._combos: dict[str, QComboBox]      = {}
        self._current_mode: Mode = Mode.OOS
        self._bypass_active: bool = False

        self.setWindowTitle(f"{tag} / {description}")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setMinimumWidth(520)
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setSpacing(4)
        title = QLabel(f"{self._tag} / {self._desc}")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f"font-size: 11pt; font-weight: bold; color: {TEXT_PRIMARY};"
        )
        title_row.addWidget(title, 1)

        btn_help = QPushButton("?")
        btn_help.setFixedSize(28, 28)
        btn_help.setAutoDefault(False)
        btn_help.setDefault(False)
        btn_help.setToolTip("Open PID Parameter Reference")
        btn_help.setStyleSheet(
            f"QPushButton {{ background-color: {BG_INSET}; "
            f"color: {TEXT_PRIMARY}; border: 1px solid {DV_ALARM_BAR1}; "
            f"border-radius: 14px; font-size: 14pt; font-weight: bold; "
            f"padding: 0px; min-height: 0px; min-width: 0px; "
            f"max-height: 28px; max-width: 28px; }}"
            f"QPushButton:hover {{ background-color: {BG_RAISED}; "
            f"color: #5b8def; }}"
        )
        btn_help.clicked.connect(self._open_help)
        title_row.addWidget(btn_help)
        root.addLayout(title_row)

        self._live_banner = QLabel("")
        self._live_banner.setAlignment(Qt.AlignCenter)
        self._live_banner.setStyleSheet(
            f"background-color: {DV_PV_BG}; color: {TEXT_PRIMARY}; "
            f"font-family: Consolas; font-size: 10pt; "
            f"border: 1px solid {DV_ALARM_BAR1}; padding: 4px;"
        )
        root.addWidget(self._live_banner)

        tabs = QTabWidget()
        tabs.addTab(self._build_tuning_tab(),  "Tuning")
        tabs.addTab(self._build_limits_tab(),  "Limits")
        tabs.addTab(self._build_options_tab(), "Options")
        tabs.addTab(self._build_status_tab(),  "Status")
        root.addWidget(tabs)

        btn_close = QPushButton("Close")
        btn_close.setAutoDefault(False)
        btn_close.setDefault(False)
        btn_close.clicked.connect(self.close)
        root.addWidget(btn_close)

    # -- Help --------------------------------------------------------------
    def _open_help(self) -> None:
        """Show the in-app PID parameter reference dialog."""
        from .help_dialog import PidHelpDialog
        PidHelpDialog.show_help(self)

    # -- Tuning tab --------------------------------------------------------
    def _build_tuning_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(4)
        lay.setContentsMargins(8, 8, 8, 8)
        grid = QGridLayout()
        grid.setSpacing(3)
        grid.setColumnMinimumWidth(1, 90)

        tuning = [
            # (attr,          label,            lo,      hi,     dec, step,  sfx)
            ("gain",          "Gain",           0.001,  200.0,  3,   0.1,   ""),
            ("reset",         "Reset",          0.1,   9999.0,  1,   1.0,   " s/rep"),
            ("rate",          "Rate",           0.0,    600.0,  1,   0.5,   " s"),
            ("bias",          "Bias (P/PD)",    -100.0, 100.0,  2,   0.5,   " %"),
            ("alpha",         "Alpha (D filt)", 0.05,     1.0,  3,   0.01,  ""),
            ("pv_ftime",      "PV Filter \u03c4",    0.0,    300.0,  1,   0.5,   " s"),
            ("sp_ftime",      "SP Filter \u03c4",    0.0,    300.0,  1,   0.5,   " s"),
            ("sp_rate_dn",    "SP Rate Dn",     0.0,   9999.0,  1,   1.0,   " EU/s"),
            ("sp_rate_up",    "SP Rate Up",     0.0,   9999.0,  1,   1.0,   " EU/s"),
            ("ff_gain",       "FF Gain",       -10.0,    10.0,  3,   0.1,   ""),
            ("ideadband",     "I Deadband",     0.0,    100.0,  2,   0.1,   ""),
            ("beta",          "Beta (2-DoF P)", 0.0,      1.0,  2,   0.05,  ""),
            ("gamma",         "Gamma(2-DoF D)", 0.0,      1.0,  2,   0.05,  ""),
            ("bal_time",      "BAL_TIME",       0.0,    600.0,  1,   1.0,   " s"),
            ("stdev_time",    "STDEV Window",   0.0,    600.0,  1,   5.0,   " s"),
            ("stdev_cap",     "STDEV Cap",      0.01,  1000.0,  2,   1.0,   ""),
            ("var_idx_lim",   "VAR_IDX Limit",  0.0,    100.0,  1,   5.0,   " %"),
        ]
        for row, (attr, label, lo, hi, dec, step, sfx) in enumerate(tuning):
            lbl = QLabel(label)
            lbl.setStyleSheet(f"font-size: 8pt; color: {TEXT_PRIMARY};")
            tip = _PID_TOOLTIPS.get(attr, "")
            if tip:
                lbl.setToolTip(tip)
            grid.addWidget(lbl, row, 0)
            sp = self._make_spin(attr, lo, hi, dec, step)
            if sfx:
                sp.setSuffix(sfx)
            grid.addWidget(sp, row, 1)
        lay.addLayout(grid)

        # Equation Form
        form_row = QHBoxLayout()
        form_lbl = QLabel("Equation Form")
        form_lbl.setToolTip(_PID_TOOLTIPS.get("form", ""))
        form_row.addWidget(form_lbl)
        form_combo = QComboBox()
        form_combo.addItems([f.value for f in PIDForm])
        form_combo.currentIndexChanged.connect(
            lambda i: self.param_changed.emit("form", float(i))
        )
        self._combos["form"] = form_combo
        form_combo.setToolTip(_PID_TOOLTIPS.get("form", ""))
        form_row.addWidget(form_combo)
        lay.addLayout(form_row)

        # Structure
        struct_row = QHBoxLayout()
        struct_lbl = QLabel("Structure")
        struct_lbl.setToolTip(_PID_TOOLTIPS.get("structure", ""))
        struct_row.addWidget(struct_lbl)
        struct_combo = QComboBox()
        struct_combo.addItems([s.value for s in Structure])
        struct_combo.currentIndexChanged.connect(self._on_structure_changed)
        self._combos["structure"] = struct_combo
        struct_combo.setToolTip(_PID_TOOLTIPS.get("structure", ""))
        struct_row.addWidget(struct_combo)
        lay.addLayout(struct_row)

        lay.addStretch()
        return w

    # -- Limits tab --------------------------------------------------------
    def _build_limits_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(4)
        pv_span = self._sp_hi - self._sp_lo

        def _header(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet(
                f"font-weight: bold; color: {_TAG_COLOR}; font-size: 9pt; margin-top:4px;"
            )
            return lbl

        lay.addWidget(_header("Alarm Limits (PV-based)"))
        alm_grid = QGridLayout()
        alm_grid.setSpacing(3)
        alm_grid.setColumnMinimumWidth(1, 90)
        alarm_defs = [
            ("hi_hi_lim",  "Hi Hi Lim",  self._sp_lo - pv_span,  self._sp_hi + pv_span, 1, 0.5),
            ("hi_lim",     "Hi Lim",     self._sp_lo - pv_span,  self._sp_hi + pv_span, 1, 0.5),
            ("dv_hi_lim",  "Dev Hi Lim", 0.0,                     pv_span,               1, 0.5),
            ("dv_lo_lim",  "Dev Lo Lim", -pv_span,                0.0,                   1, 0.5),
            ("lo_lim",     "Lo Lim",     self._sp_lo - pv_span,  self._sp_hi + pv_span, 1, 0.5),
            ("lo_lo_lim",  "Lo Lo Lim",  self._sp_lo - pv_span,  self._sp_hi + pv_span, 1, 0.5),
            ("alarm_hys",  "Alarm Hyst", 0.0,                     pv_span / 2,           1, 0.1),
        ]
        for row, (attr, label, lo, hi, dec, step) in enumerate(alarm_defs):
            lbl = QLabel(label)
            lbl.setStyleSheet(f"font-size: 8pt; color: {TEXT_PRIMARY};")
            tip = _PID_TOOLTIPS.get(attr, "")
            if tip:
                lbl.setToolTip(tip)
            alm_grid.addWidget(lbl, row, 0)
            sp = self._make_spin(attr, lo, hi, dec, step)
            sp.setSuffix(f" {self._sp_units}")
            alm_grid.addWidget(sp, row, 1)
        lay.addLayout(alm_grid)

        lay.addWidget(_header("Output / SP / ARW Limits"))
        out_grid = QGridLayout()
        out_grid.setSpacing(3)
        out_grid.setColumnMinimumWidth(1, 90)
        out_defs = [
            ("out_hi_lim", "Out Hi Lim",  self._op_lo,           self._op_hi + 50,     1, 0.5),
            ("out_lo_lim", "Out Lo Lim",  self._op_lo - 50,      self._op_hi,           1, 0.5),
            ("sp_hi_lim",  "SP Hi Lim",   self._sp_lo,           self._sp_hi + pv_span, 1, 0.5),
            ("sp_lo_lim",  "SP Lo Lim",   self._sp_lo - pv_span, self._sp_hi,           1, 0.5),
            ("arw_hi_lim", "ARW Hi Lim",  self._op_lo,           self._op_hi + 50,     1, 0.5),
            ("arw_lo_lim", "ARW Lo Lim",  self._op_lo - 50,      self._op_hi,           1, 0.5),
        ]
        for row, (attr, label, lo, hi, dec, step) in enumerate(out_defs):
            lbl = QLabel(label)
            lbl.setStyleSheet(f"font-size: 8pt; color: {TEXT_PRIMARY};")
            tip = _PID_TOOLTIPS.get(attr, "")
            if tip:
                lbl.setToolTip(tip)
            out_grid.addWidget(lbl, row, 0)
            sfx = self._op_units if attr in ("out_hi_lim","out_lo_lim","arw_hi_lim","arw_lo_lim") \
                  else self._sp_units
            sp = self._make_spin(attr, lo, hi, dec, step)
            sp.setSuffix(f" {sfx}")
            out_grid.addWidget(sp, row, 1)
        lay.addLayout(out_grid)

        lay.addStretch()
        return w

    # -- Options tab -------------------------------------------------------
    def _build_options_tab(self) -> QWidget:
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(5)

        def _header(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet(
                f"font-weight: bold; color: {_TAG_COLOR}; font-size: 9pt; margin-top:4px;"
            )
            return lbl

        lay.addWidget(_header("FRSIPID_OPTS"))
        for attr, label in [
            ("use_pidplus",             "Use PIDPlus"),
            ("use_nonlinear_gain",      "Use Nonlinear Gain Modification"),
            ("dynamic_reset_limit",     "Dynamic Reset Limit (DRL)"),
            ("use_delayed_out_bad_pv",  "Use Delayed OUT on Bad PV"),
        ]:
            cb = self._make_check(attr, label)
            lay.addWidget(cb)

        lay.addWidget(_header("CONTROL_OPTS"))
        for attr, label in [
            ("direct_acting",         "Direct Acting"),
            ("increase_to_close",     "Increase to Close"),
            ("bypass",                "Bypass"),
            ("bypass_enable",         "Bypass Enable"),
            ("sp_pv_track_lo_iman",   "SP-PV Track in LO / IMan"),
            ("sp_pv_track_man",       "SP-PV Track in Man"),
            ("sp_pv_track_rout",      "SP-PV Track in ROut"),
            ("use_pv_for_bkcal_out",  "Use PV for BKCAL_OUT"),
            ("no_out_limits_in_man",  "No OUT Limits in Manual"),
            ("obey_sp_lim_cas_rcas",  "Obey SP Lim if Cas/RCas"),
            ("track_enable",          "Track Enable"),
            ("track_in_manual",       "Track in Manual"),
            ("ff_enable",             "Feedforward Enable"),
            ("simulate_enabled",      "Simulation Enabled"),
        ]:
            cb = self._make_check(attr, label)
            lay.addWidget(cb)

        lay.addWidget(_header("Nonlinear Gain Parameters"))
        pv_span = self._sp_hi - self._sp_lo
        nl_grid = QGridLayout()
        nl_grid.setSpacing(3)
        nl_grid.setColumnMinimumWidth(1, 90)
        for row, (attr, label, lo, hi, dec, step) in enumerate([
            ("nl_gap",    "NL_GAP",    0.0,     pv_span, 2, 0.1),
            ("nl_hyst",   "NL_HYST",   0.0,     pv_span, 2, 0.1),
            ("nl_tband",  "NL_TBAND",  0.0,     pv_span, 2, 0.1),
            ("nl_minmod", "NL_MINMOD", 0.0,     1.0,     2, 0.01),
        ]):
            lbl = QLabel(label)
            lbl.setStyleSheet(f"font-size: 8pt; color: {TEXT_PRIMARY};")
            tip = _PID_TOOLTIPS.get(attr, "")
            if tip:
                lbl.setToolTip(tip)
            nl_grid.addWidget(lbl, row, 0)
            nl_grid.addWidget(self._make_spin(attr, lo, hi, dec, step), row, 1)
        lay.addLayout(nl_grid)

        lay.addWidget(_header("PIDPlus: Saturation Recovery"))
        rf_row = QHBoxLayout()
        rf_lbl = QLabel("Recovery Filter  (0=aggressive \u2026 1=standard)")
        rf_lbl.setToolTip(_PID_TOOLTIPS.get("recovery_fltr", ""))
        rf_lbl.setStyleSheet(f"font-size: 8pt; color: {TEXT_PRIMARY};")
        rf_row.addWidget(rf_lbl)
        rf_row.addWidget(self._make_spin("recovery_fltr", 0.0, 1.0, 2, 0.05))
        lay.addLayout(rf_row)

        # -- Linearization -------------------------------------------------
        lay.addWidget(_header("Linearization"))
        ltype_row = QHBoxLayout()
        ltype_lbl = QLabel("L_TYPE")
        ltype_lbl.setToolTip(_PID_TOOLTIPS.get("l_type", ""))
        ltype_lbl.setStyleSheet(f"font-size: 8pt; color: {TEXT_PRIMARY};")
        ltype_row.addWidget(ltype_lbl)
        ltype_combo = QComboBox()
        ltype_combo.addItems([lt.value for lt in LinearizationType])
        ltype_combo.setToolTip(_PID_TOOLTIPS.get("l_type", ""))
        ltype_combo.currentIndexChanged.connect(
            lambda i: self._on_combo("l_type", i)
        )
        self._combos["l_type"] = ltype_combo
        ltype_row.addWidget(ltype_combo)
        lay.addLayout(ltype_row)
        lay.addWidget(self._make_check("low_cutoff_enabled", "Low Cutoff Enabled"))
        lowcut_row = QHBoxLayout()
        lowcut_lbl = QLabel("LOW_CUT")
        lowcut_lbl.setToolTip(_PID_TOOLTIPS.get("low_cut", ""))
        lowcut_lbl.setStyleSheet(f"font-size: 8pt; color: {TEXT_PRIMARY};")
        lowcut_row.addWidget(lowcut_lbl)
        lowcut_row.addWidget(self._make_spin("low_cut", 0.0, 9999.0, 2, 0.1))
        lay.addLayout(lowcut_row)

        # -- Remote Shedding -----------------------------------------------
        lay.addWidget(_header("Remote Shedding"))
        shed_row = QHBoxLayout()
        shed_lbl = QLabel("SHED_OPT")
        shed_lbl.setToolTip(_PID_TOOLTIPS.get("shed_opt", ""))
        shed_lbl.setStyleSheet(f"font-size: 8pt; color: {TEXT_PRIMARY};")
        shed_row.addWidget(shed_lbl)
        shed_combo = QComboBox()
        shed_combo.addItems([so.value for so in ShedOption])
        shed_combo.setToolTip(_PID_TOOLTIPS.get("shed_opt", ""))
        shed_combo.currentIndexChanged.connect(
            lambda i: self._on_combo("shed_opt", i)
        )
        self._combos["shed_opt"] = shed_combo
        shed_row.addWidget(shed_combo)
        lay.addLayout(shed_row)
        shed_time_row = QHBoxLayout()
        shed_time_lbl = QLabel("SHED_TIME")
        shed_time_lbl.setToolTip(_PID_TOOLTIPS.get("shed_time", ""))
        shed_time_lbl.setStyleSheet(f"font-size: 8pt; color: {TEXT_PRIMARY};")
        shed_time_row.addWidget(shed_time_lbl)
        shed_time_row.addWidget(self._make_spin("shed_time", 0.0, 600.0, 1, 1.0))
        lay.addLayout(shed_time_row)

        # -- STATUS_OPTS ---------------------------------------------------
        lay.addWidget(_header("STATUS_OPTS"))
        for attr, label in [
            ("bad_if_limited",           "Bad if Limited"),
            ("uncertain_if_limited",     "Uncertain if Limited"),
            ("target_manual_if_bad_in",  "Target Manual if Bad IN"),
            ("use_uncertain_as_good",    "Use Uncertain as Good"),
        ]:
            lay.addWidget(self._make_check(attr, label))

        # -- Conditional Alarm ---------------------------------------------
        lay.addWidget(self._make_check("condalm_enabled", "Conditional Alarm Enable"))

        # -- Tracking Options ----------------------------------------------
        lay.addWidget(_header("Tracking Options"))
        topt_row = QHBoxLayout()
        topt_lbl = QLabel("TRACK_OPT")
        topt_lbl.setToolTip(_PID_TOOLTIPS.get("track_opt", ""))
        topt_lbl.setStyleSheet(f"font-size: 8pt; color: {TEXT_PRIMARY};")
        topt_row.addWidget(topt_lbl)
        topt_combo = QComboBox()
        topt_combo.addItems([to.value for to in TrackOption])
        topt_combo.setToolTip(_PID_TOOLTIPS.get("track_opt", ""))
        topt_combo.currentIndexChanged.connect(
            lambda i: self._on_combo("track_opt", i)
        )
        self._combos["track_opt"] = topt_combo
        topt_row.addWidget(topt_combo)
        lay.addLayout(topt_row)

        # -- Process Type --------------------------------------------------
        ptype_row = QHBoxLayout()
        ptype_lbl = QLabel("PROCESS_TYPE")
        ptype_lbl.setToolTip(_PID_TOOLTIPS.get("process_type", ""))
        ptype_lbl.setStyleSheet(f"font-size: 8pt; color: {TEXT_PRIMARY};")
        ptype_row.addWidget(ptype_lbl)
        ptype_combo = QComboBox()
        ptype_combo.addItems([pt.value for pt in ProcessType])
        ptype_combo.setToolTip(_PID_TOOLTIPS.get("process_type", ""))
        ptype_combo.currentIndexChanged.connect(
            lambda i: self._on_combo("process_type", i)
        )
        self._combos["process_type"] = ptype_combo
        ptype_row.addWidget(ptype_combo)
        lay.addLayout(ptype_row)

        # -- Additional control options ------------------------------------
        lay.addWidget(self._make_check("sp_track_retained_target",
                                       "SP Track Retained Target"))
        lay.addWidget(self._make_check("act_on_ir",
                                       "ACT on IR (Bias Adjust)"))

        lay.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        return scroll

    # -- Status tab --------------------------------------------------------
    def _build_status_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(5)

        def _row(label: str) -> QLabel:
            h = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setStyleSheet(
                f"font-size: 8pt; font-weight: bold; color: {_TAG_COLOR}; min-width:130px;"
            )
            val = QLabel("---")
            val.setStyleSheet(
                f"font-family: Consolas; font-size: 8pt; color: {TEXT_PRIMARY};"
            )
            h.addWidget(lbl)
            h.addWidget(val, 1)
            lay.addLayout(h)
            return val

        self._st_actual_mode = _row("Actual Mode:")
        self._st_target_mode = _row("Target Mode:")
        self._st_out_limit   = _row("OUT Limit Status:")
        self._st_bkcal       = _row("BKCAL_OUT:")
        self._st_block_err   = _row("Block Errors:")
        self._st_alarms      = _row("Active Alarms:")
        self._st_integral    = _row("Integrator:")
        self._st_field_val   = _row("FIELD_VAL (PV %):")
        self._st_nl_gain_mod = _row("NL_GAIN_MOD (KNL):")
        self._st_stdev       = _row("STDEV:")
        self._st_var_idx     = _row("VAR_IDX:")
        self._st_bad_active  = _row("BAD_ACTIVE:")
        self._st_abnorm      = _row("ABNORM_ACTIVE:")
        self._st_delayed_pv  = _row("Delayed Bad PV:")
        self._st_io_out      = _row("IO_OUT (post-inv):")
        self._st_bypass      = _row("Bypass Active:")

        warn_hdr = QLabel("Configuration Warnings  (validate_pid_config)")
        warn_hdr.setStyleSheet(
            f"font-weight: bold; color: {_TAG_COLOR}; font-size: 9pt; margin-top:6px;"
        )
        lay.addWidget(warn_hdr)
        self._warn_text = QTextEdit()
        self._warn_text.setReadOnly(True)
        self._warn_text.setFixedHeight(90)
        self._warn_text.setStyleSheet(
            f"font-family: Consolas; font-size: 8pt; "
            f"background-color: {BG_INSET};"
        )
        lay.addWidget(self._warn_text)

        # Full mode-change buttons (OOS through ROut)
        mode_hdr = QLabel("Mode Control")
        mode_hdr.setStyleSheet(
            f"font-weight: bold; color: {_TAG_COLOR}; font-size: 9pt; margin-top:4px;"
        )
        lay.addWidget(mode_hdr)
        mode_row = QHBoxLayout()
        for mode in [Mode.OOS, Mode.Man, Mode.Auto, Mode.Cas, Mode.RCas, Mode.ROut]:
            btn = QPushButton(_MODE_DISPLAY[mode])
            btn.setFixedHeight(26)
            btn.setAutoDefault(False)
            btn.setStyleSheet("font-size: 8pt; font-weight: bold;")
            btn.clicked.connect(
                lambda _=False, m=mode: self.mode_change_requested.emit(m)
            )
            mode_row.addWidget(btn)
        lay.addLayout(mode_row)
        lay.addStretch()
        return w

    # ------------------------------------------------------------------
    # Widget factory helpers
    # ------------------------------------------------------------------
    def _make_spin(self, attr: str, lo: float, hi: float,
                   decimals: int, step: float) -> QDoubleSpinBox:
        sp = QDoubleSpinBox()
        sp.setRange(lo, hi)
        sp.setDecimals(decimals)
        sp.setSingleStep(step)
        sp.setFixedHeight(22)
        sp.setStyleSheet("font-size: 8pt;")
        sp.setKeyboardTracking(False)   # commit only on Enter / focus-out
        tip = _PID_TOOLTIPS.get(attr, "")
        if tip:
            sp.setToolTip(tip)
        sp.valueChanged.connect(lambda v, a=attr: self._on_param(a, v))
        self._spins[attr] = sp
        return sp

    def _make_check(self, attr: str, label: str) -> QCheckBox:
        cb = QCheckBox(label)
        cb.setStyleSheet(f"font-size: 8pt; color: {TEXT_PRIMARY};")
        tip = _PID_TOOLTIPS.get(attr, "")
        if tip:
            cb.setToolTip(tip)
        cb.toggled.connect(lambda v, a=attr: self._on_flag(a, v))
        self._checks[attr] = cb
        return cb

    def _is_param_editable(self, attr: str) -> bool:
        """Check if parameter can be changed in the current mode.

        Three protection tiers:
          OOS_ONLY         — configuration params, only in OOS
          MAN_OR_OOS       — simulate/bypass toggle, Manual or OOS
          BYPASS_OR_OOS    — safety/alarm params, only when bypass
                             is active or in OOS
        """
        mode = self._current_mode
        if attr in self._OOS_ONLY_PARAMS:
            return mode == Mode.OOS
        if attr in self._MAN_OR_OOS_PARAMS:
            return mode in (Mode.OOS, Mode.Man)
        if attr in self._BYPASS_OR_OOS_PARAMS:
            return mode == Mode.OOS or self._bypass_active
        return True

    def _update_param_protection(self) -> None:
        """Enable/disable widgets based on current controller mode."""
        _locked_style = f"font-size: 8pt; background-color: {BG_INSET}; color: {DV_ALARM_BAR2};"
        _normal_style = "font-size: 8pt;"

        for attr, spin in self._spins.items():
            editable = self._is_param_editable(attr)
            spin.setEnabled(editable)
            # Preserve Two-DoF graying for beta/gamma
            if attr in ("beta", "gamma"):
                continue
            spin.setStyleSheet(_normal_style if editable else _locked_style)

        for attr, cb in self._checks.items():
            editable = self._is_param_editable(attr)
            cb.setEnabled(editable)

        for attr, combo in self._combos.items():
            editable = self._is_param_editable(attr)
            combo.setEnabled(editable)

    def _on_param(self, attr: str, value: float) -> None:
        if not self._is_param_editable(attr):
            return
        self.param_changed.emit(attr, value)
        # Legacy compat
        if attr == "gain":
            self.kp_changed.emit(value)
        elif attr == "reset":
            self.ti_changed.emit(value)
        elif attr in ("hi_hi_lim", "hi_lim", "lo_lim", "lo_lo_lim",
                      "dv_hi_lim", "dv_lo_lim"):
            lim_map = {"hi_hi_lim": "hihi", "hi_lim": "hi",
                       "lo_lim": "lo", "lo_lo_lim": "lolo",
                       "dv_hi_lim": "dv_hi", "dv_lo_lim": "dv_lo"}
            limits = {k: self._spins[a].value()
                      for a, k in lim_map.items() if a in self._spins}
            limits["tag"] = self._tag
            self.alarm_limits_changed.emit(limits)

    def _on_flag(self, attr: str, value: bool) -> None:
        if not self._is_param_editable(attr):
            return
        self.flag_changed.emit(attr, value)

    def _on_structure_changed(self, index: int) -> None:
        is_two_dof = list(Structure)[index] == Structure.TWO_DOF
        for attr in ("beta", "gamma"):
            if attr in self._spins:
                self._spins[attr].setEnabled(is_two_dof)
                self._spins[attr].setStyleSheet(
                    "font-size: 8pt;" if is_two_dof
                    else f"font-size: 8pt; color: {DV_ALARM_BAR2};"
                )
        self.param_changed.emit("structure", float(index))

    def _on_combo(self, attr: str, index: int) -> None:
        """Generic handler for new enum combo boxes."""
        if not self._is_param_editable(attr):
            return
        self.param_changed.emit(attr, float(index))

    # ------------------------------------------------------------------
    # Public update API
    # ------------------------------------------------------------------
    def update_live(self, pv: float, sp: float, op: float,
                    mode: str = "", block_err: str = "",
                    alarm_status: str = "") -> None:
        """Update the top live banner (backward-compat signature)."""
        err = sp - pv
        self._live_banner.setText(
            f"PV: {pv:.{self._decimals}f}   "
            f"SP: {sp:.{self._decimals}f}   "
            f"OP: {op:.1f}   "
            f"Err: {err:+.{self._decimals}f}"
        )

    def update_from_block(self, block: PIDBlock) -> None:
        """
        Full sync from a live PIDBlock instance.  Touches every widget.

        Attribute mapping to PIDBlock:
          Spinboxes  -> block.<attr>  (direct name match, aliases resolved below)
          Checkboxes -> block.<attr>  (bool flags)
          Form combo -> block.form    (PIDForm enum -> index)
          Struct combo -> block.structure (Structure enum -> index)
          Status tab -> block.actual_mode / target_mode (Mode enum)
                       block.OUT.limit (LimitStatus)
                       block.BKCAL_OUT.value / .limit
                       block.block_err.summary()  (BlockError)
                       block.alarm_state  (AlarmState dataclass)
                       block._integral    (float, internal diagnostic)
          Warnings   -> validate_pid_config(block)
        """
        # ---- Track mode and bypass state for parameter protection ----
        self._current_mode = block.actual_mode
        self._bypass_active = (
            getattr(block, 'bypass', False)
            and getattr(block, 'bypass_enable', False)
        )

        # ---- Spinboxes ----
        _alias = {
            # detail-dialog attr -> PIDBlock attr
            "hihi_lim":       "hi_hi_lim",
            "lolo_lim":       "lo_lo_lim",
            "pv_filter_time": "pv_ftime",
        }
        for attr, spin in self._spins.items():
            # Skip updating if user is currently editing this spinbox
            if spin.hasFocus():
                continue
            block_attr = _alias.get(attr, attr)
            val = getattr(block, block_attr, None)
            if val is None:
                continue
            spin.blockSignals(True)
            try:
                fv = float(val)
                if math.isfinite(fv):
                    spin.setValue(fv)
            except (TypeError, ValueError):
                pass
            spin.blockSignals(False)

        # ---- Checkboxes ----
        for attr, cb in self._checks.items():
            val = getattr(block, attr, None)
            if val is not None:
                cb.blockSignals(True)
                cb.setChecked(bool(val))
                cb.blockSignals(False)

        # ---- Form combo ----
        if "form" in self._combos:
            forms = list(PIDForm)
            try:
                idx = forms.index(block.form)
                self._combos["form"].blockSignals(True)
                self._combos["form"].setCurrentIndex(idx)
                self._combos["form"].blockSignals(False)
            except ValueError:
                pass

        # ---- Structure combo ----
        if "structure" in self._combos:
            structs = list(Structure)
            try:
                idx = structs.index(block.structure)
                self._combos["structure"].blockSignals(True)
                self._combos["structure"].setCurrentIndex(idx)
                self._combos["structure"].blockSignals(False)
                self._on_structure_changed(idx)
            except ValueError:
                pass

        # ---- New enum combos ----
        _new_combos = [
            ("l_type",       LinearizationType, getattr(block, 'l_type', LinearizationType.DIRECT)),
            ("shed_opt",     ShedOption,         getattr(block, 'shed_opt', ShedOption.NORMAL)),
            ("track_opt",    TrackOption,        getattr(block, 'track_opt', TrackOption.ALWAYS_USE_VALUE)),
            ("process_type", ProcessType,        getattr(block, 'process_type', ProcessType.SELF_REGULATING)),
        ]
        for key, enum_cls, current_val in _new_combos:
            if key in self._combos:
                members = list(enum_cls)
                try:
                    idx = members.index(current_val)
                    self._combos[key].blockSignals(True)
                    self._combos[key].setCurrentIndex(idx)
                    self._combos[key].blockSignals(False)
                except ValueError:
                    pass

        # ---- Status tab ----
        self._st_actual_mode.setText(
            _MODE_DISPLAY.get(block.actual_mode, block.actual_mode.name)
        )
        self._st_target_mode.setText(
            _MODE_DISPLAY.get(block.target_mode, block.target_mode.name)
        )

        # OUT limit -- colour orange when clamped
        limit_name = block.OUT.limit.name
        limit_style = (f"font-family: Consolas; font-size: 8pt; color: {_LIMIT_COLOR}; font-weight:bold;"
                       if block.OUT.limit in (LimitStatus.HIGH_LIMITED, LimitStatus.LOW_LIMITED)
                       else f"font-family: Consolas; font-size: 8pt; color: {TEXT_PRIMARY};")
        self._st_out_limit.setText(limit_name)
        self._st_out_limit.setStyleSheet(limit_style)

        self._st_bkcal.setText(
            f"{block.BKCAL_OUT.value:.3f}  [{block.BKCAL_OUT.limit.name}]"
        )

        # Block errors
        errs = block.block_err.summary()
        self._st_block_err.setText(", ".join(errs) if errs else "OK")
        self._st_block_err.setStyleSheet(
            f"font-family: Consolas; font-size: 8pt; "
            f"color: {'#CC0000' if errs else ALARM_OK};"
        )

        # Alarm state -- list all active flags by name
        active_alarms = [k for k, v in vars(block.alarm_state).items() if v]
        self._st_alarms.setText(", ".join(active_alarms) if active_alarms else "None")
        self._st_alarms.setStyleSheet(
            f"font-family: Consolas; font-size: 8pt; "
            f"color: {'#CC0000' if active_alarms else ALARM_OK};"
        )

        # Integrator (internal diagnostic, read-only)
        self._st_integral.setText(f"{block._integral:.4f}")

        # New Azeo diagnostic displays
        self._st_field_val.setText(f"{getattr(block, 'field_val', 0.0):.2f} %")
        self._st_nl_gain_mod.setText(f"{getattr(block, 'nl_gain_mod', 1.0):.4f}")
        self._st_stdev.setText(f"{getattr(block, 'stdev', 0.0):.4f}")

        # VAR_IDX with color coding
        var_idx = getattr(block, 'var_idx', 0.0)
        var_lim = getattr(block, 'var_idx_lim', 100.0)
        var_over = var_idx > var_lim
        # The marker is a plain literal, not an escape inside the
        # f-string expression: a backslash there is 3.12+ syntax and
        # the project's floor is 3.11.
        abnormal = " \u25b2 ABNORMAL" if var_over else ""
        self._st_var_idx.setText(f"{var_idx:.1f} %{abnormal}")
        self._st_var_idx.setStyleSheet(
            f"font-family: Consolas; font-size: 8pt; "
            f"color: {'#CC0000' if var_over else TEXT_PRIMARY};"
            f"{' font-weight: bold;' if var_over else ''}"
        )

        # BAD_ACTIVE / ABNORM_ACTIVE
        bad_act = getattr(block, 'bad_active', False)
        self._st_bad_active.setText("YES" if bad_act else "No")
        self._st_bad_active.setStyleSheet(
            f"font-family: Consolas; font-size: 8pt; "
            f"color: {'#CC0000' if bad_act else ALARM_OK};"
            f"{' font-weight: bold;' if bad_act else ''}"
        )

        abnorm_act = getattr(block, 'abnorm_active', False)
        self._st_abnorm.setText("YES" if abnorm_act else "No")
        self._st_abnorm.setStyleSheet(
            f"font-family: Consolas; font-size: 8pt; "
            f"color: {'#FF8C00' if abnorm_act else ALARM_OK};"
            f"{' font-weight: bold;' if abnorm_act else ''}"
        )

        # Delayed bad PV
        delayed = getattr(block, '_delayed_bad_pv_active', False)
        self._st_delayed_pv.setText("ACTIVE" if delayed else "No")
        self._st_delayed_pv.setStyleSheet(
            f"font-family: Consolas; font-size: 8pt; "
            f"color: {'#FF8C00' if delayed else TEXT_PRIMARY};"
        )

        # IO_OUT (post increase-to-close inversion)
        io_out = getattr(block, '_io_out_value', block.OUT.value)
        itc = getattr(block, 'increase_to_close', False)
        itc_label = f"{io_out:.2f} %{' (inverted)' if itc else ''}"
        self._st_io_out.setText(itc_label)

        # Bypass active
        byp = getattr(block, 'bypass', False) and getattr(block, 'bypass_enable', False)
        self._st_bypass.setText("YES" if byp else "No")
        self._st_bypass.setStyleSheet(
            f"font-family: Consolas; font-size: 8pt; "
            f"color: {'#FF8C00' if byp else TEXT_PRIMARY};"
        )

        # Live banner
        self.update_live(block.PV, block.SP, block.OUT.value)

        # Parameter protection based on mode
        self._update_param_protection()

        # Config warnings
        warnings = validate_pid_config(block)
        self._warn_text.setPlainText(
            "\n".join(warnings) if warnings else "No configuration warnings."
        )
        self._warn_text.setStyleSheet(
            f"font-family: Consolas; font-size: 8pt; "
            f"color: {'#FF8C00' if warnings else ALARM_OK}; "
            f"background-color: {BG_INSET};"
        )

    # Backward-compat aliases
    def set_tuning(self, gain: float, reset_sec: float, rate_sec: float = 0.0) -> None:
        for attr, val in [("gain", gain), ("reset", reset_sec), ("rate", rate_sec)]:
            if attr in self._spins:
                self._spins[attr].blockSignals(True)
                self._spins[attr].setValue(val)
                self._spins[attr].blockSignals(False)

    def sync_from_block(self, block: PIDBlock) -> None:
        self.update_from_block(block)


# =====================================================================
# Azeo-style faceplate sub-widgets
# =====================================================================


class _NoWheelSpinBox(QDoubleSpinBox):
    """QDoubleSpinBox that ignores mouse wheel so scrolling doesn't change values."""

    def wheelEvent(self, event):
        event.ignore()


class FaceplateValueAdjust(QFrame):
    """Compact value display with up/down arrows and keyboard-editable input."""

    value_changed = Signal(float)

    def __init__(self, value=0.0, decimals=1, color=TEXT_PRIMARY, parent=None):
        super().__init__(parent)
        self._value = value
        self._decimals = decimals

        self.setStyleSheet("QFrame { background: transparent; border: none; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        _btn_style = (
            f"QToolButton {{ background: transparent; border: none; "
            f"color: {TEXT_SECONDARY}; font-size: 7pt; padding: 0px; "
            f"min-height: 10px; max-height: 10px; }}"
            f"QToolButton:hover {{ color: {TEXT_PRIMARY}; }}"
        )

        from PySide6.QtWidgets import QToolButton
        self._up_btn = QToolButton()
        self._up_btn.setText("\u25b2")
        self._up_btn.setStyleSheet(_btn_style)
        self._up_btn.clicked.connect(self._increment)
        layout.addWidget(self._up_btn, 0, Qt.AlignCenter)

        # Editable spinbox (no wheel tracking)
        self._spin = _NoWheelSpinBox()
        self._spin.setDecimals(decimals)
        self._spin.setValue(value)
        self._spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self._spin.setAlignment(Qt.AlignCenter)
        self._spin.setFixedWidth(50)
        self._spin.setFixedHeight(20)
        self._spin.setKeyboardTracking(False)
        self._spin.setStyleSheet(
            f"QDoubleSpinBox {{ font-size: 9pt; font-weight: bold; "
            f"color: {color}; background: {BG_INSET}; "
            f"border: none; padding: 0px 1px; }}"
            f"QDoubleSpinBox:disabled {{ color: {DV_ALARM_BAR2}; "
            f"background: {BG_FACEPLATE}; }}"
        )
        self._spin.valueChanged.connect(self._on_spin_changed)
        layout.addWidget(self._spin, 0, Qt.AlignCenter)

        self._dn_btn = QToolButton()
        self._dn_btn.setText("\u25bc")
        self._dn_btn.setStyleSheet(_btn_style)
        self._dn_btn.clicked.connect(self._decrement)
        layout.addWidget(self._dn_btn, 0, Qt.AlignCenter)

    def set_range(self, lo, hi, step=0.5):
        self._spin.setRange(lo, hi)
        self._spin.setSingleStep(step)

    def set_value(self, v):
        """Update displayed value without triggering callback."""
        self._value = v
        self._spin.blockSignals(True)
        self._spin.setValue(v)
        self._spin.blockSignals(False)

    def set_editable(self, editable: bool):
        """Enable/disable the arrows and input field."""
        self._up_btn.setEnabled(editable)
        self._dn_btn.setEnabled(editable)
        self._spin.setEnabled(editable)

    def has_focus(self) -> bool:
        return self._spin.hasFocus()

    def _on_spin_changed(self, v):
        self._value = v
        self.value_changed.emit(v)

    def _increment(self):
        self._spin.stepUp()

    def _decrement(self):
        self._spin.stepDown()


class FaceplateScaleAndPVBar(QWidget):
    """Two thin vertical bars side by side:
      Left:  Gray scale bar with tick marks and SP triangle indicator
      Right: Thin PV zone bar with alarm coloring (HH/HI/LO/LL)
    SP dashed line extends from the triangle across into the PV bar.
    """

    SCALE_BAR_W = 12    # gray scale bar width
    PV_BAR_W = 14       # PV zone bar width
    GAP = 2             # gap between bars
    TRI_W = 7           # SP triangle width

    def __init__(self, lo=0.0, hi=100.0, parent=None):
        super().__init__(parent)
        self._lo, self._hi = lo, hi
        # Alarm limits as absolute values (not percentages)
        self._lolo = lo + (hi - lo) * 0.10
        self._lo_alarm = lo + (hi - lo) * 0.20
        self._hi_alarm = lo + (hi - lo) * 0.80
        self._hihi = lo + (hi - lo) * 0.90
        self._pv = 0.0
        self._sp = 0.0
        total_w = self.TRI_W + self.SCALE_BAR_W + self.GAP + self.PV_BAR_W + 10
        self.setFixedWidth(total_w)
        self.setMinimumHeight(130)

    def set_pv(self, v: float): self._pv = v; self.update()
    def set_sp(self, v: float): self._sp = v; self.update()

    def set_alarm_bands(self, lo: float, hi: float,
                        lolo: float | None = None,
                        hihi: float | None = None) -> None:
        """Set alarm limits in engineering units."""
        self._lo_alarm = lo
        self._hi_alarm = hi
        rng = self._hi - self._lo
        self._lolo = lolo if lolo is not None else self._lo + rng * 0.10
        self._hihi = hihi if hihi is not None else self._hi - rng * 0.10
        self.update()

    def _y_for_value(self, val, bar_y, bar_h, rng):
        frac = max(0.0, min(1.0, (val - self._lo) / rng))
        return bar_y + bar_h - int(bar_h * frac)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        h = self.height()
        margin = 4
        bar_y, bar_h = margin, h - 2 * margin
        rng = self._hi - self._lo if self._hi != self._lo else 1.0

        # X positions
        tri_x = 0
        scale_x = self.TRI_W
        pv_x = scale_x + self.SCALE_BAR_W + self.GAP

        # PV bar alarm zone colors
        _PV_NORMAL = "#7AB8D8"
        _PV_HIGH = "#3C6291"
        _PV_HIGH_HIGH = "#1E3A5F"
        _PV_LOW_YELLOW = "#E8D44D"
        _BAR_BG = "#B8C0CC"
        _BORDER_COLOR = "#9BADC6"

        # ── Gray scale bar (left) ──
        p.fillRect(scale_x, bar_y, self.SCALE_BAR_W, bar_h, QColor(_BAR_BG))

        # Tick marks every 10%
        p.setPen(QPen(QColor(TEXT_SECONDARY), 1))
        for pct in range(0, 101, 10):
            val = self._lo + (self._hi - self._lo) * pct / 100.0
            y = self._y_for_value(val, bar_y, bar_h, rng)
            tick_len = self.SCALE_BAR_W if pct % 20 == 0 else 6
            tx = scale_x + self.SCALE_BAR_W - tick_len
            p.drawLine(tx, y, scale_x + self.SCALE_BAR_W, y)

        # Scale bar border
        p.setPen(QPen(QColor(_BORDER_COLOR), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(scale_x, bar_y, self.SCALE_BAR_W, bar_h)

        # ── SP triangle indicator (left of scale bar) ──
        sp_y = self._y_for_value(self._sp, bar_y, bar_h, rng)
        from PySide6.QtGui import QPolygonF
        from PySide6.QtCore import QPointF
        tri = QPolygonF([
            QPointF(tri_x, sp_y - 5),
            QPointF(tri_x, sp_y + 5),
            QPointF(tri_x + self.TRI_W, sp_y),
        ])
        p.setBrush(QColor("#808890"))
        p.setPen(QPen(QColor("#606878"), 1))
        p.drawPolygon(tri)

        # SP dashed line across both bars
        p.setPen(QPen(QColor("#606878"), 1, Qt.DashLine))
        p.drawLine(scale_x, sp_y, pv_x + self.PV_BAR_W, sp_y)

        # ── PV zone bar (right) — alarm coloring ──
        y_hihi = self._y_for_value(self._hihi, bar_y, bar_h, rng)
        y_hi = self._y_for_value(self._hi_alarm, bar_y, bar_h, rng)
        y_lo = self._y_for_value(self._lo_alarm, bar_y, bar_h, rng)
        y_lolo = self._y_for_value(self._lolo, bar_y, bar_h, rng)

        # HI-HI (top) — darker blue
        p.fillRect(pv_x, bar_y, self.PV_BAR_W, y_hihi - bar_y,
                   QColor(_PV_HIGH_HIGH))
        # HI — dark blue
        p.fillRect(pv_x, y_hihi, self.PV_BAR_W, y_hi - y_hihi,
                   QColor(_PV_HIGH))
        # Normal — light blue
        p.fillRect(pv_x, y_hi, self.PV_BAR_W, y_lo - y_hi,
                   QColor(_PV_NORMAL))
        # LO — yellow
        p.fillRect(pv_x, y_lo, self.PV_BAR_W, y_lolo - y_lo,
                   QColor(_PV_LOW_YELLOW))
        # LO-LO (bottom) — yellow
        p.fillRect(pv_x, y_lolo, self.PV_BAR_W, bar_y + bar_h - y_lolo,
                   QColor(_PV_LOW_YELLOW))

        # Dim above PV level
        pv_y = self._y_for_value(self._pv, bar_y, bar_h, rng)
        overlay = QColor(200, 200, 210, 130)
        p.fillRect(pv_x, bar_y, self.PV_BAR_W, pv_y - bar_y, overlay)

        # PV level marker (white line)
        p.setPen(QPen(QColor("#FFFFFF"), 2))
        p.drawLine(pv_x, pv_y, pv_x + self.PV_BAR_W, pv_y)

        # Zone boundary lines
        p.setPen(QPen(QColor("#FFFFFF"), 0.5, Qt.DashLine))
        for y_line in [y_hihi, y_hi, y_lo, y_lolo]:
            p.drawLine(pv_x + 1, y_line, pv_x + self.PV_BAR_W - 1, y_line)

        # PV bar border
        p.setPen(QPen(QColor(_BORDER_COLOR), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(pv_x, bar_y, self.PV_BAR_W, bar_h)

        p.end()


class FaceplateHorizontalOPBar(QWidget):
    """Horizontal OP bar with scale labels and position pointer (^)."""

    def __init__(self, lo=0.0, hi=100.0, parent=None):
        super().__init__(parent)
        self._lo, self._hi = lo, hi
        self._op = 0.0
        self._limit_status: LimitStatus = LimitStatus.NOT_LIMITED
        self.setFixedHeight(28)

    def set_op(self, v: float): self._op = v; self.update()

    def set_limit_status(self, status: LimitStatus) -> None:
        if status != self._limit_status:
            self._limit_status = status
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        margin_x = 4
        bar_x, bar_w = margin_x, w - 2 * margin_x
        bar_y, bar_h = 8, 12
        rng = self._hi - self._lo if self._hi != self._lo else 1.0

        # Scale labels (top)
        p.setPen(QColor(TEXT_SECONDARY))
        font = QFont("Segoe UI", 6)
        p.setFont(font)
        p.drawText(bar_x, 7, f"{self._lo:.1f}")
        hi_text = f"{self._hi:.1f}"
        fm = p.fontMetrics()
        p.drawText(bar_x + bar_w - fm.horizontalAdvance(hi_text), 7, hi_text)

        # Trough
        p.fillRect(bar_x, bar_y, bar_w, bar_h, QColor(DV_OUT_BG))

        # OP fill (from left)
        frac = max(0.0, min(1.0, (self._op - self._lo) / rng))
        fill_w = int(bar_w * frac)
        at_limit = self._limit_status in (LimitStatus.HIGH_LIMITED,
                                           LimitStatus.LOW_LIMITED)
        fill_color = QColor(_LIMIT_COLOR) if at_limit else QColor(_OUT_FG)
        p.fillRect(bar_x, bar_y, fill_w, bar_h, fill_color)

        # Border
        p.setPen(QPen(QColor(DV_ALARM_BAR1), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(bar_x, bar_y, bar_w, bar_h)

        # Pointer triangle (^) below bar at OP position
        from PySide6.QtGui import QPolygonF
        from PySide6.QtCore import QPointF
        ptr_x = bar_x + fill_w
        ptr_y = bar_y + bar_h + 1
        tri = QPolygonF([
            QPointF(ptr_x - 4, ptr_y + 6),
            QPointF(ptr_x + 4, ptr_y + 6),
            QPointF(ptr_x, ptr_y),
        ])
        p.setBrush(QColor(TEXT_SECONDARY))
        p.setPen(Qt.NoPen)
        p.drawPolygon(tri)

        p.end()


class FaceplateMiniTrend(QWidget):
    """Small embedded trend/sparkline for PV history.

    Click to open trend dialog; double-click for historian.
    """
    clicked = Signal()
    double_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(40)
        self.setCursor(Qt.PointingHandCursor)
        self._data: collections.deque = collections.deque(maxlen=200)

    def add_point(self, value: float) -> None:
        self._data.append(value)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        p.fillRect(0, 0, w, h, QColor(BG_TREND))
        p.setPen(QPen(QColor(DV_ALARM_BAR1), 1))
        p.drawRect(0, 0, w - 1, h - 1)

        if len(self._data) < 2:
            p.end()
            return

        # Grid lines
        p.setPen(QPen(QColor(60, 80, 100), 0.5, Qt.DotLine))
        for i in range(1, 4):
            y = h * i / 4
            p.drawLine(0, int(y), w, int(y))

        data = list(self._data)
        lo = min(data) - 1
        hi = max(data) + 1
        rng = hi - lo if hi != lo else 1.0

        p.setPen(QPen(QColor("#7AB8D8"), 1.5))
        from PySide6.QtCore import QPointF
        points = []
        n = len(data)
        for i, v in enumerate(data):
            x = w * i / (n - 1)
            y = h - (h * (v - lo) / rng)
            points.append(QPointF(x, y))
        path = QPainterPath()
        path.moveTo(points[0])
        for pt in points[1:]:
            path.lineTo(pt)
        p.drawPath(path)
        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)


class FaceplateSliderToggle(QWidget):
    """A two-option slider toggle switch (Bypass / Normal)."""

    def __init__(self, left_text="Bypass", right_text="Normal", parent=None):
        super().__init__(parent)
        self._left_text = left_text
        self._right_text = right_text
        self._is_right = True  # True = Normal (right) active
        self.setFixedHeight(18)
        self.setMinimumWidth(80)
        self.setCursor(Qt.PointingHandCursor)

    def is_right(self) -> bool:
        return self._is_right

    def set_right(self, active: bool):
        self._is_right = active
        self.update()

    def mousePressEvent(self, event):
        if event.x() < self.width() / 2:
            self._is_right = False
        else:
            self._is_right = True
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        half = w // 2
        r = 3

        # Overall trough background
        p.setPen(QPen(QColor(DV_ALARM_BAR1), 1))
        p.setBrush(QColor(BG_INSET))
        p.drawRoundedRect(0, 0, w - 1, h - 1, r, r)

        # Active slider tab (raised look)
        if self._is_right:
            tab_x, tab_w = half, w - half - 1
        else:
            tab_x, tab_w = 0, half

        p.setPen(QPen(QColor(DV_ALARM_BAR1), 1))
        p.setBrush(QColor(DV_ALARM_BAR2))
        p.drawRoundedRect(tab_x, 0, tab_w, h - 1, r, r)

        # Text
        font = QFont("Segoe UI", 6, QFont.Bold)
        p.setFont(font)
        if not self._is_right:
            p.setPen(QColor(TEXT_ON_DARK))
        else:
            p.setPen(QColor(TEXT_SECONDARY))
        p.drawText(0, 0, half, h, Qt.AlignCenter, self._left_text)

        if self._is_right:
            p.setPen(QColor(TEXT_ON_DARK))
        else:
            p.setPen(QColor(TEXT_SECONDARY))
        p.drawText(half, 0, w - half, h, Qt.AlignCenter, self._right_text)

        p.end()


# =====================================================================
# LAYER 2 — Faceplate Popup (Azeo Distillation-style)
# =====================================================================
class FaceplatePopup(QDialog):
    """Azeo-style faceplate popup — primary operator interaction.

    Layout matches Azeo distillation faceplate:
      Row 1: Header (tag + description + pin/minimize/close)
      Row 2: Digital readouts (PV | OP)
      Row 3: Main body (SP/OP adjusters | Scale+PV bar | Mode+Bypass)
      Row 4: Horizontal OP bar
      Row 5: Mini trend sparkline
      Row 6: Alarm + help
      Row 7: Unit label
      Row 8: Detail button + navigation dots
    """

    sp_changed       = Signal(float)
    op_changed       = Signal(float)
    mode_changed     = Signal(object)   # Mode enum
    detail_requested = Signal()
    trend_requested  = Signal()
    historian_requested = Signal()
    autotune_requested = Signal()

    def __init__(
        self,
        tag: str,
        description: str,
        sp_lo: float, sp_hi: float, sp_units: str,
        op_lo: float, op_hi: float, op_units: str,
        pv_color: str = _PV_FG,
        decimals: int = 1,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._tag = tag
        self._description = description
        self._sp_lo, self._sp_hi = sp_lo, sp_hi
        self._op_lo, self._op_hi = op_lo, op_hi
        self._sp_units = sp_units
        self._op_units = op_units
        self._pv_color = pv_color
        self._decimals = decimals
        self._mode: Mode = Mode.Auto
        self._pinned: bool = False

        self.setWindowTitle(f"{tag} \u2014 {description}")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setFixedWidth(210)
        self._drag_pos = None
        self._build_ui()

    # ------------------------------------------------------------------
    def _lbl(self, text, size=8, bold=False, color=TEXT_PRIMARY,
             align=Qt.AlignLeft):
        lbl = QLabel(text)
        weight = "bold" if bold else "normal"
        lbl.setStyleSheet(
            f"QLabel {{ font-size: {size}pt; font-weight: {weight}; "
            f"color: {color}; background: transparent; border: none; }}"
        )
        lbl.setAlignment(align)
        return lbl

    def _build_ui(self) -> None:
        _HEADER_BG = BG_DARK
        _BORDER_CLR = DV_ALARM_BAR1

        self.setStyleSheet(
            f"FaceplatePopup {{ background-color: {BG_FACEPLATE}; "
            f"border: none; }}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 4)
        root.setSpacing(0)

        # ── Row 1: Header with pin/minimize/close ──
        header = QFrame()
        header.setFixedHeight(34)
        header.setStyleSheet(
            f"QFrame {{ background-color: {_HEADER_BG}; border: none; }}"
        )
        hl = QHBoxLayout(header)
        hl.setContentsMargins(4, 2, 4, 2)
        hl.setSpacing(2)

        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.addWidget(self._lbl(
            self._tag, 9, True, TEXT_ON_DARK, Qt.AlignCenter))
        title_col.addWidget(self._lbl(
            self._description, 6, False, DV_PV_LEVEL_BG, Qt.AlignCenter))
        hl.addLayout(title_col, 1)

        _wbtn_style = (
            f"QPushButton {{ background: transparent; border: none; "
            f"font-size: 8pt; padding: 0px; color: {TEXT_ON_DARK}; }}"
            f"QPushButton:hover {{ background-color: {DV_ALARM_BAR2}; "
            f"color: {TEXT_ON_DARK}; border-radius: 2px; }}"
        )

        # Pin button
        self._pin_btn = QPushButton("\U0001F4CC")
        self._pin_btn.setCheckable(True)
        self._pin_btn.setChecked(False)
        self._pin_btn.setFixedSize(16, 16)
        self._pin_btn.setToolTip("Pin: keep faceplate on top")
        self._pin_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; "
            f"font-size: 8pt; padding: 0px; }}"
            f"QPushButton:checked {{ background-color: {DV_ALARM_BAR2}; "
            f"border-radius: 2px; }}"
        )
        self._pin_btn.toggled.connect(self._on_pin_toggled)
        hl.addWidget(self._pin_btn, 0, Qt.AlignTop)

        # Minimize
        _min_btn = QPushButton("\u2015")
        _min_btn.setFixedSize(16, 16)
        _min_btn.setToolTip("Minimize")
        _min_btn.setStyleSheet(_wbtn_style)
        _min_btn.clicked.connect(self._on_minimize)
        hl.addWidget(_min_btn, 0, Qt.AlignTop)

        # Close
        _close_btn = QPushButton("\u2715")
        _close_btn.setFixedSize(16, 16)
        _close_btn.setToolTip("Close")
        _close_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; "
            f"font-size: 8pt; padding: 0px; color: {TEXT_ON_DARK}; }}"
            f"QPushButton:hover {{ background-color: #CC3333; "
            f"color: {TEXT_ON_DARK}; border-radius: 2px; }}"
        )
        _close_btn.clicked.connect(self.close)
        hl.addWidget(_close_btn, 0, Qt.AlignTop)

        root.addWidget(header)

        # Shadow line under header
        shadow = QFrame()
        shadow.setFixedHeight(2)
        shadow.setStyleSheet(
            "QFrame { border: none; "
            "background: qlineargradient(x1:0,y1:0,x2:0,y2:1, "
            "stop:0 rgba(0,0,0,80), stop:1 rgba(0,0,0,0)); }"
        )
        root.addWidget(shadow)

        # Body wrapper with side margins
        body_wrapper = QWidget()
        body_root = QVBoxLayout(body_wrapper)
        body_root.setContentsMargins(4, 2, 4, 0)
        body_root.setSpacing(2)
        root.addWidget(body_wrapper, 1)

        # ── Row 2: PV | OP readouts ──
        readout_row = QHBoxLayout()
        readout_row.setContentsMargins(0, 0, 0, 0)
        readout_row.setSpacing(0)
        readout_row.addStretch(2)

        readout = QFrame()
        readout.setStyleSheet(
            f"QFrame {{ background-color: {BG_INSET}; "
            f"border: none; border-radius: 2px; }}"
        )
        rl = QHBoxLayout(readout)
        rl.setContentsMargins(4, 2, 4, 2)
        rl.setSpacing(6)

        # PV value + label
        pv_col = QVBoxLayout()
        pv_col.setSpacing(0)
        self._pv_digital = self._lbl(
            "--", 10, True, TEXT_PRIMARY, Qt.AlignCenter)
        pv_col.addWidget(self._pv_digital)
        pv_col.addWidget(self._lbl("PV", 6, False, TEXT_SECONDARY, Qt.AlignCenter))
        rl.addLayout(pv_col)

        # Vertical separator
        sep = QFrame()
        sep.setFixedWidth(1)
        sep.setStyleSheet(f"QFrame {{ background-color: {_BORDER_CLR}; border: none; }}")
        rl.addWidget(sep)

        # OP value + label
        op_col = QVBoxLayout()
        op_col.setSpacing(0)
        self._op_digital = self._lbl(
            "--", 10, True, _OUT_FG, Qt.AlignCenter)
        op_col.addWidget(self._op_digital)
        op_col.addWidget(self._lbl("OP", 6, False, TEXT_SECONDARY, Qt.AlignCenter))
        rl.addLayout(op_col)

        readout_row.addWidget(readout)
        readout_row.addStretch(2)
        body_root.addLayout(readout_row)

        # Scale top label
        scale_top_row = QHBoxLayout()
        scale_top_row.addStretch(2)
        scale_top_row.addWidget(self._lbl(
            f"{self._sp_hi:.1f}", 6, False, TEXT_SECONDARY, Qt.AlignCenter))
        scale_top_row.addStretch(2)
        body_root.addLayout(scale_top_row)

        # ── Row 3: Main body ──
        body = QHBoxLayout()
        body.setSpacing(2)

        # Left: SP adjust + OP adjust
        left = QVBoxLayout()
        left.setSpacing(2)
        left.addStretch()

        self._sp_adjust = FaceplateValueAdjust(
            value=(self._sp_lo + self._sp_hi) / 2,
            decimals=self._decimals, color=_PV_FG,
        )
        self._sp_adjust.set_range(self._sp_lo, self._sp_hi, 0.5)
        self._sp_adjust.value_changed.connect(self.sp_changed.emit)
        left.addWidget(self._sp_adjust)

        left.addSpacing(6)

        self._op_adjust = FaceplateValueAdjust(
            value=(self._op_lo + self._op_hi) / 2,
            decimals=1, color=_OUT_FG,
        )
        self._op_adjust.set_range(self._op_lo, self._op_hi, 1.0)
        self._op_adjust.value_changed.connect(self.op_changed.emit)
        left.addWidget(self._op_adjust)

        left.addStretch()
        body.addLayout(left, 2)

        # Center: Scale + PV bar
        self._pv_bar = FaceplateScaleAndPVBar(self._sp_lo, self._sp_hi)
        body.addWidget(self._pv_bar, 3)

        # Right: Mode + Bypass (same weight as left for symmetry)
        right = QVBoxLayout()
        right.setSpacing(2)
        right.addStretch()

        # Mode combo
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["AUTO", "MAN", "CAS"])
        self._mode_combo.setFixedHeight(20)
        self._mode_combo.setStyleSheet(
            f"QComboBox {{ font-size: 7pt; font-weight: bold; "
            f"background: {BG_INSET}; color: {TEXT_PRIMARY}; "
            f"border: 1px solid {DV_ALARM_BAR1}; border-radius: 2px; "
            f"padding: 0px 2px; }}"
            f"QComboBox::drop-down {{ border: none; width: 14px; }}"
            f"QComboBox::down-arrow {{ image: none; "
            f"border-left: 4px solid transparent; "
            f"border-right: 4px solid transparent; "
            f"border-top: 5px solid {TEXT_PRIMARY}; }}"
        )
        self._mode_combo.currentTextChanged.connect(self._on_mode_combo_changed)
        right.addWidget(self._mode_combo)

        # Mode indicator badge
        self._mode_ind = ModeIndicator()
        right.addWidget(self._mode_ind)

        right.addSpacing(10)

        # Bypass / Normal toggle
        self._bypass_toggle = FaceplateSliderToggle("Bypass", "Normal")
        self._bypass_toggle.set_right(True)
        right.addWidget(self._bypass_toggle)

        right.addStretch()
        body.addLayout(right, 2)

        body_root.addLayout(body, 1)

        # Scale bottom + units
        scale_bot_row = QHBoxLayout()
        scale_bot_row.addStretch(2)
        scale_bot_row.addWidget(self._lbl(
            f"{self._sp_lo:.0f}", 6, False, TEXT_SECONDARY, Qt.AlignCenter))
        scale_bot_row.addStretch(2)
        body_root.addLayout(scale_bot_row)

        body_root.addWidget(self._lbl(
            self._sp_units, 6, False, TEXT_SECONDARY, Qt.AlignCenter))

        # ── Row 4: Horizontal OP bar ──
        self._op_h_bar = FaceplateHorizontalOPBar(self._op_lo, self._op_hi)
        body_root.addWidget(self._op_h_bar)

        # ── Row 5: Mini trend (click=trend, double-click=historian) ──
        self._mini_trend = FaceplateMiniTrend()
        self._mini_trend.clicked.connect(self.trend_requested.emit)
        self._mini_trend.double_clicked.connect(self.historian_requested.emit)
        body_root.addWidget(self._mini_trend)

        # ── Row 6: Alarm + Help ──
        alarm_row = QHBoxLayout()
        alarm_row.setContentsMargins(2, 1, 2, 1)

        self._alarm_icon = self._lbl("[!]", 8, True, ALARM_OK, Qt.AlignCenter)
        self._alarm_text = self._lbl("", 8, True, ALARM_OK, Qt.AlignLeft)
        self._alarm_icon.setVisible(False)
        self._alarm_text.setVisible(False)
        alarm_row.addWidget(self._alarm_icon)
        alarm_row.addWidget(self._alarm_text)
        alarm_row.addStretch()

        # Limit status label
        self._limit_label = QLabel("")
        self._limit_label.setStyleSheet(
            f"QLabel {{ font-size: 8pt; font-weight: bold; "
            f"color: {_LIMIT_COLOR}; background: transparent; border: none; }}"
        )
        alarm_row.addWidget(self._limit_label)

        # Help button
        help_btn = QPushButton("?")
        help_btn.setFixedSize(18, 18)
        help_btn.setStyleSheet(
            f"QPushButton {{ background-color: {BG_INSET}; "
            f"color: {TEXT_SECONDARY}; border: 1px solid {_BORDER_CLR}; "
            f"border-radius: 9px; font-size: 9pt; font-weight: bold; "
            f"padding: 0px; }}"
            f"QPushButton:hover {{ color: #5b8def; }}"
        )
        alarm_row.addWidget(help_btn)
        body_root.addLayout(alarm_row)

        # ── Row 7: Unit label (placeholder) ──
        body_root.addWidget(self._lbl(
            f"Unit: {self._sp_units}", 6, False, TEXT_SECONDARY, Qt.AlignCenter))

        # ── Row 8: Detail button + navigation dots ──
        footer_row = QHBoxLayout()
        footer_row.setContentsMargins(4, 1, 4, 1)

        _footer_btn_style = (
            f"QPushButton {{ background-color: {BG_INSET}; "
            f"color: {TEXT_SECONDARY}; border: none; "
            f"border-radius: 3px; font-size: 12pt; padding: 0px; }}"
            f"QPushButton:hover {{ background-color: {DV_ALARM_BAR2}; "
            f"color: {TEXT_ON_DARK}; }}"
        )

        detail_btn = QPushButton("\u2699")
        detail_btn.setFixedSize(20, 20)
        detail_btn.setToolTip("Open Detailed Faceplate")
        detail_btn.setStyleSheet(_footer_btn_style)
        detail_btn.clicked.connect(self.detail_requested.emit)
        footer_row.addWidget(detail_btn)

        # Trend button — opens PID trend dialog
        trend_btn = QPushButton("\U0001F4C8")
        trend_btn.setFixedSize(20, 20)
        trend_btn.setToolTip("Open Trend")
        trend_btn.setStyleSheet(_footer_btn_style)
        trend_btn.clicked.connect(self.trend_requested.emit)
        footer_row.addWidget(trend_btn)

        # Historian button — expand to full historian popup
        hist_btn = QPushButton("\U0001F4CA")
        hist_btn.setFixedSize(20, 20)
        hist_btn.setToolTip("Open in Historian")
        hist_btn.setStyleSheet(_footer_btn_style)
        hist_btn.clicked.connect(self.historian_requested.emit)
        footer_row.addWidget(hist_btn)

        # Auto-Tune button
        self._autotune_btn = QPushButton("\u2261")
        self._autotune_btn.setFixedSize(20, 20)
        self._autotune_btn.setToolTip("Auto-Tune (Relay Feedback)")
        self._autotune_btn.setStyleSheet(_footer_btn_style)
        self._autotune_btn.clicked.connect(self.autotune_requested.emit)
        footer_row.addWidget(self._autotune_btn)

        footer_row.addStretch()

        body_root.addLayout(footer_row)

        self._update_controls_enabled()

    # ── Draggable frameless window ──
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    # ------------------------------------------------------------------
    def _on_pin_toggled(self, checked: bool) -> None:
        self._pinned = checked

    def _on_minimize(self) -> None:
        self.showMinimized()

    @property
    def pinned(self) -> bool:
        return self._pinned

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if (event.type() == event.Type.ActivationChange
                and not self.isActiveWindow()
                and not self._pinned):
            self.close()

    # ------------------------------------------------------------------
    def _on_mode_combo_changed(self, mode_str: str) -> None:
        mode = _DISPLAY_MODE.get(mode_str, Mode.Auto)
        self._mode = mode
        self._mode_ind.set_mode(mode)
        self.mode_changed.emit(mode)
        self._update_controls_enabled()

    def _set_mode(self, mode: Mode) -> None:
        self._mode = mode
        self._sync_mode_display()
        self.mode_changed.emit(mode)
        self._update_controls_enabled()

    def _sync_mode_display(self) -> None:
        mode_str = _MODE_DISPLAY.get(self._mode, "AUTO")
        self._mode_ind.set_mode(self._mode)
        # Update combo without triggering signal
        idx = self._mode_combo.findText(mode_str)
        if idx >= 0:
            self._mode_combo.blockSignals(True)
            self._mode_combo.setCurrentIndex(idx)
            self._mode_combo.blockSignals(False)

    def _update_controls_enabled(self) -> None:
        """OP editable in Man/ROut; SP editable in Auto only."""
        op_editable = self._mode in (Mode.Man, Mode.ROut)
        sp_editable = self._mode == Mode.Auto
        self._sp_adjust.set_editable(sp_editable)
        self._op_adjust.set_editable(op_editable)

    # ------------------------------------------------------------------
    # Public update API
    # ------------------------------------------------------------------
    def update_live(self, pv: float, sp: float, op: float,
                    limit_status: LimitStatus = LimitStatus.NOT_LIMITED) -> None:
        """Refresh readouts, bar graphs, sparkline, and limit indicator."""
        d = self._decimals
        self._pv_digital.setText(f"{pv:.{d}f}")
        self._op_digital.setText(f"{op:.1f}")

        # PV bar with SP indicator
        self._pv_bar.set_pv(pv)
        self._pv_bar.set_sp(sp)

        # Horizontal OP bar
        self._op_h_bar.set_op(op)
        self._op_h_bar.set_limit_status(limit_status)

        # SP/OP adjust widgets (don't trigger callbacks)
        if not self._sp_adjust.has_focus():
            self._sp_adjust.set_value(sp)
        if not self._op_adjust.has_focus():
            self._op_adjust.set_value(op)

        # Limit status
        if limit_status == LimitStatus.HIGH_LIMITED:
            self._limit_label.setText("\u25b2 HIGH LIMITED")
        elif limit_status == LimitStatus.LOW_LIMITED:
            self._limit_label.setText("\u25bc LOW LIMITED")
        else:
            self._limit_label.setText("")

        # Mini trend
        self._mini_trend.add_point(pv)

    def update_from_block(self, block: PIDBlock) -> None:
        """Full live refresh from a PIDBlock instance.

        Reads:
          block.PV           — process variable
          block.SP_WRK       — working setpoint (post rate-limit)
          block.OUT.value    — controller output
          block.OUT.limit    — LimitStatus enum
          block.actual_mode  — Mode enum
        """
        self.update_live(
            pv=block.PV,
            sp=block.SP_WRK,
            op=block.OUT.value,
            limit_status=block.OUT.limit,
        )
        self.set_mode(block.actual_mode)

    def set_mode(self, mode: str | Mode) -> None:
        """Update mode display without emitting signal."""
        if isinstance(mode, str):
            mode = _DISPLAY_MODE.get(mode.upper(), Mode.Auto)
        self._mode = mode
        self._sync_mode_display()
        self._update_controls_enabled()

    def set_alarm(self, active: bool, text: str = "") -> None:
        """Show or hide alarm indicator."""
        self._alarm_icon.setVisible(active)
        self._alarm_text.setVisible(active)
        if active:
            self._alarm_text.setText(text)

    def set_alarm_bands(self, lo: float, hi: float) -> None:
        self._pv_bar.set_alarm_bands(lo, hi)


# =====================================================================
# PID Trend Dialog — Two stacked pyqtgraph charts
# =====================================================================

# Tag -> (pv_historian_tag, prefix, pv_lo, pv_hi, pv_units)
_PID_TREND_MAP = {
    "PIC-200": ("steam_pressure", "pic", 400, 750, "psig"),
    "AIC-O2":  ("stack_o2",       "aic",   0,  10, "%"),
    "LIC-100": ("drum_level",     "lic",   0, 100, "%"),
    "FIC-101": ("fw_flow",        "fic",   0,  80, "klb/hr"),
    "TIC-300": ("steam_temp_f",   "tic", 400, 900, "\u00b0F"),
}


class PidTrendDialog(QDialog):
    """Two-chart trend popup for a single PID controller.

    Top chart:  PV (blue, left axis), SP (tan, left axis), OP (green, right axis 0-100%)
    Bottom chart: P-term (red), I-term (blue), D-term (green)
    Shared time-window selector.
    """

    def __init__(
        self,
        tag: str,
        description: str,
        pv_tag: str,
        prefix: str,
        pv_lo: float,
        pv_hi: float,
        pv_units: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._tag = tag
        self._prefix = prefix

        self.setWindowTitle(f"{tag} \u2014 {description} \u2014 Trend")
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setMinimumSize(700, 500)
        self.resize(800, 600)

        self._build_ui(tag, description, pv_tag, prefix, pv_lo, pv_hi, pv_units)

    def _build_ui(self, tag, description, pv_tag, prefix, pv_lo, pv_hi, pv_units):
        from ..charts.historian_trend import HistorianTrendWidget, TrendPen

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # Header
        hdr = QLabel(f"<b>{tag}</b> \u2014 {description}")
        hdr.setStyleSheet(
            f"font-size: 10pt; color: {TEXT_PRIMARY}; padding: 2px 4px;"
        )
        root.addWidget(hdr)

        # Shared time window combo
        tw_row = QHBoxLayout()
        tw_row.addWidget(QLabel("Time window:"))
        self._window_combo = QComboBox()
        self._window_combo.addItems([
            "1 min", "2 min", "5 min", "10 min", "30 min", "All"
        ])
        self._window_combo.setCurrentText("5 min")
        self._window_combo.setFixedWidth(80)
        self._window_combo.currentTextChanged.connect(self._on_window_change)
        tw_row.addWidget(self._window_combo)
        tw_row.addStretch()
        root.addLayout(tw_row)

        # ---- Top chart: PV / SP / OP ----
        self._trend_top = HistorianTrendWidget(f"{tag} \u2014 PV / SP / OP")
        self._trend_top.add_pen(TrendPen(
            pv_tag, "PV", pv_units, TREND_COLORS[0],
            pv_lo, pv_hi, line_width=2.0,
        ))
        self._trend_top.add_pen(TrendPen(
            f"ctrl.{tag}.SP", "SP", pv_units, TREND_COLORS[1],
            pv_lo, pv_hi,
        ))
        self._trend_top.add_pen(TrendPen(
            f"ctrl.{tag}.OUT", "OP", "%", TREND_COLORS[2],
            0, 100,
        ), use_right_axis=True)

        # ---- Bottom chart: P / I / D terms ----
        self._trend_bot = HistorianTrendWidget(f"{tag} \u2014 P / I / D Terms")
        self._trend_bot.add_pen(TrendPen(
            f"ctrl.{tag}.P_Term", "P-term", "", "#CC4444",
            -50, 50,
        ))
        self._trend_bot.add_pen(TrendPen(
            f"ctrl.{tag}.I_Term", "I-term", "", "#4488CC",
            -50, 50,
        ))
        self._trend_bot.add_pen(TrendPen(
            f"ctrl.{tag}.D_Term", "D-term", "", "#44AA44",
            -10, 10,
        ), use_right_axis=True)

        # Set initial window to 5 min on both charts
        self._trend_top._time_window_min = 5.0
        self._trend_top._window_combo.setCurrentText("5 min")
        self._trend_bot._time_window_min = 5.0
        self._trend_bot._window_combo.setCurrentText("5 min")

        root.addWidget(self._trend_top, 1)
        root.addWidget(self._trend_bot, 1)

        # Footer
        footer = QHBoxLayout()
        footer.addStretch()
        btn_close = QPushButton("Close")
        btn_close.setFixedSize(60, 26)
        btn_close.clicked.connect(self.close)
        footer.addWidget(btn_close)
        root.addLayout(footer)

    def _on_window_change(self, text: str) -> None:
        """Sync shared time window to both charts."""
        mapping = {
            "1 min": 1.0, "2 min": 2.0, "5 min": 5.0,
            "10 min": 10.0, "30 min": 30.0, "All": 1e9,
        }
        val = mapping.get(text, 5.0)
        self._trend_top._time_window_min = val
        self._trend_bot._time_window_min = val

    def update_from_historian(self, historian) -> None:
        """Pull data and refresh both charts. Called at 20 Hz."""
        self._trend_top.update_all(historian)
        self._trend_bot.update_all(historian)


# =====================================================================
# LAYER 1 — Inline Dynamo
# =====================================================================
class InlineDynamo(QFrame):
    """ISA-101 compact dynamo for process overview display.

    update_from_block(PIDBlock) reads:
      block.PV              — process variable
      block.SP_WRK          — working setpoint (post rate-limit)
      block.OUT.value       — controller output
      block.OUT.limit       — LimitStatus (orange indicator in bar + readout)
      block.actual_mode     — Mode enum → ModeIndicator + border colour
      block.alarm_state     — AlarmState dataclass:
                                hi_hi_act / lo_lo_act → CRITICAL border + ✖
                                hi_act / lo_act / dv_hi_act / dv_lo_act → WARN ⚠
      block.block_err       — BlockError.any_active() → always CRITICAL if set
    """

    faceplate_requested = Signal()

    def __init__(
        self,
        tag: str,
        description: str,
        sp_lo: float = 0.0,
        sp_hi: float = 100.0,
        sp_units: str = "%",
        op_lo: float = 0.0,
        op_hi: float = 100.0,
        op_units: str = "%",
        pv_color: str = _PV_FG,
        decimals: int = 1,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._tag = tag
        self._description = description
        self._sp_lo = sp_lo
        self._sp_hi = sp_hi
        self._op_lo = op_lo
        self._op_hi = op_hi
        self._sp_units = sp_units
        self._op_units = op_units
        self._pv_color = pv_color
        self._decimals = decimals
        self._mode: Mode = Mode.Auto
        self._alarm_active = False
        self._alarm_level = ""

        self._build_ui()
        self.setCursor(Qt.PointingHandCursor)

    @property
    def tag(self) -> str:
        return self._tag

    def set_alarm_limits_config(self, limits: dict) -> None:
        """Configure alarm bands from dict with 'lo' / 'hi' keys."""
        if "lo" in limits and "hi" in limits:
            self._combo_bar.set_alarm_bands(limits["lo"], limits["hi"])

    def _build_ui(self) -> None:
        self.setFrameShape(QFrame.Box)
        self._set_border_style(_BORDER_NORMAL, 1)

        root = QVBoxLayout(self)
        root.setSpacing(1)
        root.setContentsMargins(0, 0, 0, 0)

        # Title bar
        title_bar = QFrame()
        title_bar.setFixedHeight(22)
        title_bar.setStyleSheet(
            f"QFrame {{ background-color: {DV_ALARM_BAR1}; border: none; "
            f"border-top-left-radius: 3px; border-top-right-radius: 3px; }}"
        )
        tl = QHBoxLayout(title_bar)
        tl.setContentsMargins(4, 1, 4, 1)
        tl.setSpacing(3)

        self._alarm_icon = QLabel("")
        self._alarm_icon.setFixedWidth(12)
        self._alarm_icon.setStyleSheet(
            "QLabel { background: transparent; border: none; font-size: 9pt; }"
        )
        tl.addWidget(self._alarm_icon)

        tag_lbl = QLabel(self._tag)
        tag_lbl.setStyleSheet(
            f"QLabel {{ color: {TEXT_ON_DARK}; font-size: 8pt; font-weight: bold; "
            f"background: transparent; border: none; }}"
        )
        tl.addWidget(tag_lbl)
        tl.addStretch()
        self._mode_ind = ModeIndicator()
        tl.addWidget(self._mode_ind)
        root.addWidget(title_bar)

        # Body
        body_frame = QFrame()
        body_frame.setStyleSheet(
            f"QFrame {{ background-color: {_DYNAMO_BG}; border: none; "
            f"margin: 0 1px; "
            f"border-bottom-left-radius: 3px; border-bottom-right-radius: 3px; }}"
        )
        bl = QHBoxLayout(body_frame)
        bl.setContentsMargins(3, 2, 3, 3)
        bl.setSpacing(3)

        self._combo_bar = CombinationBarGraph(
            self._pv_color, self._sp_lo, self._sp_hi
        )
        self._combo_bar.setFixedWidth(24)
        self._combo_bar.setMinimumHeight(50)
        bl.addWidget(self._combo_bar)

        self._value_label = QLabel("PV:---\nSP:---")
        self._value_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._value_label.setStyleSheet(
            f"QLabel {{ font-family: Consolas; font-size: 8pt; "
            f"color: {TEXT_PRIMARY}; background: transparent; border: none; }}"
        )
        bl.addWidget(self._value_label, 1)
        root.addWidget(body_frame, 1)

    def _set_border_style(self, color: str, width: int) -> None:
        self.setStyleSheet(
            f"InlineDynamo {{ background-color: {_DYNAMO_BG}; "
            f"border: {width}px solid {color}; border-radius: 4px; }}"
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.faceplate_requested.emit()
            event.accept()
        else:
            super().mousePressEvent(event)

    # ------------------------------------------------------------------
    # Public update API
    # ------------------------------------------------------------------
    def update_values(self, pv: float, sp: float, op: float,
                      limit_status: LimitStatus = LimitStatus.NOT_LIMITED) -> None:
        """Update bar and readout.  Appends ▲/▼ when OUT is limited."""
        self._combo_bar.set_value(pv)
        self._combo_bar.set_sp_target(sp)
        self._combo_bar.set_limit_status(limit_status)
        d = self._decimals
        sfx = (" \u25b2" if limit_status == LimitStatus.HIGH_LIMITED else
               " \u25bc" if limit_status == LimitStatus.LOW_LIMITED else "")
        self._value_label.setText(f"PV:{pv:.{d}f}\nSP:{sp:.{d}f}{sfx}")

    def _build_tooltip(self, block: PIDBlock) -> str:
        """Build a rich plain-text tooltip from the current block state."""
        d = self._decimals
        mode = block.actual_mode
        mode_str = _MODE_DISPLAY.get(mode, str(mode))

        # Alarm summary
        alm = block.alarm_state
        alarm_parts: list[str] = []
        if alm.hi_hi_act:
            alarm_parts.append("HI-HI")
        if alm.hi_act:
            alarm_parts.append("HI")
        if alm.lo_act:
            alarm_parts.append("LO")
        if alm.lo_lo_act:
            alarm_parts.append("LO-LO")
        if alm.dv_hi_act:
            alarm_parts.append("DV-HI")
        if alm.dv_lo_act:
            alarm_parts.append("DV-LO")
        if block.block_err.any_active():
            alarm_parts.append("BLK ERR")
        alarm_str = ", ".join(alarm_parts) if alarm_parts else "Normal"

        lines = [
            f"{self._tag} -- {self._description}",
            f"PV: {block.PV:.{d}f} {self._sp_units}",
            f"SP: {block.SP_WRK:.{d}f} {self._sp_units}",
            f"OUT: {block.OUT.value:.1f}%",
            f"Mode: {mode_str}",
            f"Alarm: {alarm_str}",
        ]
        return "\n".join(lines)

    def update_from_block(self, block: PIDBlock) -> None:
        """
        Full update from a live PIDBlock.

        Maps:
          block.PV            → bar value
          block.SP_WRK        → SP indicator line
          block.OUT.value     → (stored, passed to popup/detail)
          block.OUT.limit     → LimitStatus → bar tint + readout suffix
          block.actual_mode   → ModeIndicator + border colour
          block.alarm_state   → alarm icon + border width/colour
            .hi_hi_act / .lo_lo_act          → CRITICAL (red border, ✖)
            .hi_act / .lo_act / .dv_hi_act / .dv_lo_act → WARN (orange, ⚠)
          block.block_err.any_active()        → forces CRITICAL
        """
        self.update_values(
            pv=block.PV,
            sp=block.SP_WRK,
            op=block.OUT.value,
            limit_status=block.OUT.limit,
        )
        self.set_mode(block.actual_mode)

        alm = block.alarm_state
        critical = alm.hi_hi_act or alm.lo_lo_act or block.block_err.any_active()
        warning  = alm.hi_act or alm.lo_act or alm.dv_hi_act or alm.dv_lo_act
        any_alarm = critical or warning
        self.set_alarm_state(any_alarm, "CRITICAL" if critical else "WARN")

        # Update tooltip with current data
        self.setToolTip(self._build_tooltip(block))

    def set_alarm_state(self, active: bool, level: str = "WARN") -> None:
        self._alarm_active = active
        self._alarm_level = level
        if active:
            if level == "CRITICAL":
                self._set_border_style(_BORDER_ALARM_CRIT, 3)
                self._alarm_icon.setText("\u2716")
                self._alarm_icon.setStyleSheet(
                    f"QLabel {{ color: {ALARM_CRITICAL}; background: transparent; "
                    f"border: none; font-size: 9pt; font-weight: bold; }}"
                )
            else:
                self._set_border_style(_BORDER_ALARM_WARN, 3)
                self._alarm_icon.setText("\u26a0")
                self._alarm_icon.setStyleSheet(
                    f"QLabel {{ color: {ALARM_WARN}; background: transparent; "
                    f"border: none; font-size: 9pt; font-weight: bold; }}"
                )
        else:
            self._alarm_icon.setText("")
            if self._mode not in (Mode.Auto, Mode.Cas):
                self._set_border_style(_BORDER_STATUS, 2)
            else:
                self._set_border_style(_BORDER_NORMAL, 1)

    def set_mode(self, mode: str | Mode) -> None:
        """Update mode badge; accepts Mode enum or string."""
        if isinstance(mode, str):
            mode = _DISPLAY_MODE.get(mode.upper(), Mode.Auto)
        self._mode = mode
        self._mode_ind.set_mode(mode)
        if not self._alarm_active:
            if mode not in (Mode.Auto, Mode.Cas):
                self._set_border_style(_BORDER_STATUS, 2)
            else:
                self._set_border_style(_BORDER_NORMAL, 1)


# ══════════════════════════════════════════════════════════════════════
# CompactDynamo — PV / SP / OP stacked readout (ISA "detailed dynamo")
# ══════════════════════════════════════════════════════════════════════

class CompactDynamo(QFrame):
    """Compact dynamo showing PV, SP, and OP in a stacked layout.

    Matches the ISA "detailed dynamo" pattern:
      - Tag name (top-left) + mode letter box (top-right)
      - PV:value, SP:value, OP:value stacked rows
      - Border colour follows mode and alarm state

    Uses the same ``update_from_block(PIDBlock)`` and ``update_values()``
    API as InlineDynamo so FaceplateManager can swap them freely.
    """

    faceplate_requested = Signal()

    # Mode enum -> single-letter + colour
    _MODE_LETTER: dict[Mode, str] = {
        Mode.OOS: "O", Mode.IMan: "I", Mode.LO: "L", Mode.Man: "M",
        Mode.Auto: "A", Mode.Cas: "C", Mode.RCas: "R", Mode.ROut: "R",
    }
    _MODE_BG: dict[Mode, str] = {
        Mode.Auto: STATUS_AUTO, Mode.Man: STATUS_MAN,
        Mode.Cas: STATUS_CAS, Mode.RCas: STATUS_CAS,
        Mode.OOS: STATUS_OOS, Mode.IMan: STATUS_INIT,
        Mode.LO: STATUS_INIT, Mode.ROut: STATUS_CAS,
    }

    def __init__(
        self,
        tag: str,
        description: str,
        sp_lo: float = 0.0,
        sp_hi: float = 100.0,
        sp_units: str = "%",
        op_lo: float = 0.0,
        op_hi: float = 100.0,
        op_units: str = "%",
        pv_color: str = _PV_FG,
        decimals: int = 1,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._tag = tag
        self._description = description
        self._sp_lo = sp_lo
        self._sp_hi = sp_hi
        self._op_lo = op_lo
        self._op_hi = op_hi
        self._sp_units = sp_units
        self._op_units = op_units
        self._decimals = decimals
        self._mode: Mode = Mode.Auto
        self._alarm_active = False
        self._alarm_level = ""

        self._build_ui()
        self.setCursor(Qt.PointingHandCursor)

    @property
    def tag(self) -> str:
        return self._tag

    def set_alarm_limits_config(self, limits: dict) -> None:
        """Configure alarm bands (API compat with InlineDynamo)."""
        pass  # No bar graph in compact view

    # ── Build UI ──────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.setFrameShape(QFrame.Box)
        self._set_border_style(_PV_FG, 2)
        self.setFixedSize(138, 72)

        root = QVBoxLayout(self)
        root.setContentsMargins(5, 3, 5, 3)
        root.setSpacing(1)

        # Row 0: Tag + Mode badge
        header = QHBoxLayout()
        header.setSpacing(4)
        header.setContentsMargins(0, 0, 0, 0)

        self._tag_lbl = QLabel(self._tag)
        self._tag_lbl.setStyleSheet(
            f"QLabel {{ font-family: Consolas; font-size: 9pt; font-weight: bold; "
            f"color: {TEXT_SECONDARY}; background: transparent; border: none; }}"
        )
        header.addWidget(self._tag_lbl)

        header.addStretch()

        self._mode_badge = QLabel("A")
        self._mode_badge.setAlignment(Qt.AlignCenter)
        self._mode_badge.setFixedSize(18, 16)
        self._update_mode_badge(Mode.Auto)
        header.addWidget(self._mode_badge)

        root.addLayout(header)

        # Rows 1-3: PV / SP / OP
        mono = "font-family: Consolas; font-size: 9pt; background: transparent; border: none;"

        self._pv_lbl = QLabel("PV:---")
        self._pv_lbl.setStyleSheet(
            f"QLabel {{ {mono} font-weight: bold; color: {TEXT_PRIMARY}; }}")
        root.addWidget(self._pv_lbl)

        self._sp_lbl = QLabel("SP:---")
        self._sp_lbl.setStyleSheet(
            f"QLabel {{ {mono} color: {TEXT_SECONDARY}; }}")
        root.addWidget(self._sp_lbl)

        self._op_lbl = QLabel("OP:---%")
        self._op_lbl.setStyleSheet(
            f"QLabel {{ {mono} color: {COLOR_OP}; }}")
        root.addWidget(self._op_lbl)

    # ── Styling helpers ───────────────────────────────────────────────

    def _set_border_style(self, color: str, width: int) -> None:
        self.setStyleSheet(
            f"CompactDynamo {{ background-color: {_PV_LOOP_BG}; "
            f"border: {width}px solid {color}; border-radius: 3px; }}"
        )

    def _update_mode_badge(self, mode: Mode) -> None:
        letter = self._MODE_LETTER.get(mode, "?")
        bg = self._MODE_BG.get(mode, STATUS_AUTO)
        self._mode_badge.setText(letter)
        self._mode_badge.setStyleSheet(
            f"QLabel {{ font-family: Consolas; font-size: 8pt; font-weight: bold; "
            f"color: {TEXT_ON_DARK}; background: {bg}; "
            f"border: 1px solid {bg}; border-radius: 2px; }}"
        )

    # ── Events ────────────────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.faceplate_requested.emit()
            event.accept()
        else:
            super().mousePressEvent(event)

    # ── Public update API ─────────────────────────────────────────────

    def update_values(self, pv: float, sp: float, op: float,
                      limit_status: LimitStatus = LimitStatus.NOT_LIMITED) -> None:
        d = self._decimals
        self._pv_lbl.setText(f"PV:{pv:.{d}f}")
        self._sp_lbl.setText(f"SP:{sp:.{d}f}")

        # OP as percentage of output range
        span = self._op_hi - self._op_lo
        if span > 1e-9:
            op_pct = (op - self._op_lo) / span * 100.0
        else:
            op_pct = op * 100.0
        self._op_lbl.setText(f"OP:{op_pct:.0f}%")

        # Highlight OP if limited
        if limit_status in (LimitStatus.HIGH_LIMITED, LimitStatus.LOW_LIMITED):
            self._op_lbl.setStyleSheet(
                f"QLabel {{ font-family: Consolas; font-size: 9pt; "
                f"background: transparent; border: none; "
                f"color: {_LIMIT_COLOR}; font-weight: bold; }}")
        else:
            self._op_lbl.setStyleSheet(
                f"QLabel {{ font-family: Consolas; font-size: 9pt; "
                f"background: transparent; border: none; color: {COLOR_OP}; }}")

    def _build_tooltip(self, block: PIDBlock) -> str:
        """Build a rich plain-text tooltip from the current block state."""
        d = self._decimals
        mode = block.actual_mode
        mode_str = _MODE_DISPLAY.get(mode, str(mode))

        # Alarm summary
        alm = block.alarm_state
        alarm_parts: list[str] = []
        if alm.hi_hi_act:
            alarm_parts.append("HI-HI")
        if alm.hi_act:
            alarm_parts.append("HI")
        if alm.lo_act:
            alarm_parts.append("LO")
        if alm.lo_lo_act:
            alarm_parts.append("LO-LO")
        if alm.dv_hi_act:
            alarm_parts.append("DV-HI")
        if alm.dv_lo_act:
            alarm_parts.append("DV-LO")
        if block.block_err.any_active():
            alarm_parts.append("BLK ERR")
        alarm_str = ", ".join(alarm_parts) if alarm_parts else "Normal"

        lines = [
            f"{self._tag} -- {self._description}",
            f"PV: {block.PV:.{d}f} {self._sp_units}",
            f"SP: {block.SP_WRK:.{d}f} {self._sp_units}",
            f"OUT: {block.OUT.value:.1f}%",
            f"Mode: {mode_str}",
            f"Alarm: {alarm_str}",
        ]
        return "\n".join(lines)

    def update_from_block(self, block: PIDBlock) -> None:
        """Full update from a live PIDBlock (or PIDBlockView adapter)."""
        self.update_values(
            pv=block.PV,
            sp=block.SP_WRK,
            op=block.OUT.value,
            limit_status=block.OUT.limit,
        )
        self.set_mode(block.actual_mode)

        alm = block.alarm_state
        critical = alm.hi_hi_act or alm.lo_lo_act or block.block_err.any_active()
        warning = alm.hi_act or alm.lo_act or alm.dv_hi_act or alm.dv_lo_act
        any_alarm = critical or warning
        self.set_alarm_state(any_alarm, "CRITICAL" if critical else "WARN")

        # Update tooltip with current data
        self.setToolTip(self._build_tooltip(block))

    def set_mode(self, mode: str | Mode) -> None:
        if isinstance(mode, str):
            mode = _DISPLAY_MODE.get(mode.upper(), Mode.Auto)
        self._mode = mode
        self._update_mode_badge(mode)
        if not self._alarm_active:
            if mode not in (Mode.Auto, Mode.Cas):
                self._set_border_style(_BORDER_STATUS, 2)
            else:
                self._set_border_style(_PV_FG, 2)

    def set_alarm_state(self, active: bool, level: str = "WARN") -> None:
        self._alarm_active = active
        self._alarm_level = level
        if active:
            if level == "CRITICAL":
                self._set_border_style(_BORDER_ALARM_CRIT, 3)
            else:
                self._set_border_style(_BORDER_ALARM_WARN, 3)
        else:
            if self._mode not in (Mode.Auto, Mode.Cas):
                self._set_border_style(_BORDER_STATUS, 2)
            else:
                self._set_border_style(_BORDER_NORMAL, 1)


# Backward compatibility alias
PidFaceplate = InlineDynamo
