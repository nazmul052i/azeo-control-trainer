"""
ISA-Style PID Function Block (Python Implementation)
========================================================
Implementation of the Azeo ISA-style PID behavior contract, backed by the
executable regression suite.

Covers:
  - Standard and Series PID equation forms
  - All 8 operating modes: OOS, IMan, LO, Man, Auto, Cas, RCas, ROut
  - Nonlinear gain (KNL): NL_GAP, NL_HYST, NL_TBAND, NL_MINMOD
  - PIDPlus features: exception reporting, improved integral, saturation recovery
  - Feedforward: FF_VAL, FF_GAIN, FF_SCALE / FF_ENABLE
  - Anti-Reset Windup (ARW): clamped and Dynamic Reset Limit (DRL)
  - Setpoint tracking (SP-PV Track)
  - Output tracking (TRK_VAL / TRK_IN_D)
  - BKCAL_IN / BKCAL_OUT cascade initialization
  - Alarm detection: HI/HI_HI/LO/LO_LO/DV_HI/DV_LO
  - BYPASS mode for slave controllers with bad PV
  - Two Degrees of Freedom (BETA / GAMMA)
  - SP rate limiting (SP_RATE_UP / SP_RATE_DN) and SP filtering
  - PV filtering (PV_FILTER / PV_FTIME)
  - Simulation mode (SIMULATE_IN)
  - Block error status (BLOCK_ERR)
  - Comprehensive logging throughout

Author : Azeo Control Trainer
Date   : 2026-03-02
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logger = logging.getLogger("PID.Block")


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class Mode(Enum):
    """PID block operating modes (§ PID function block modes)."""
    OOS   = "Out-of-Service"
    IMan  = "Initializing-Manual"
    LO    = "Local-Override"
    Man   = "Manual"
    Auto  = "Automatic"
    Cas   = "Cascade"
    RCas  = "Remote-Cascade"
    ROut  = "Remote-Out"


class PIDForm(Enum):
    """PID equation form (FORM parameter)."""
    STANDARD = "Standard"   # Ideal / ISA form
    SERIES   = "Series"     # Interacting form


class Structure(Enum):
    """PID equation structure (STRUCTURE parameter)."""
    PID_ON_ERROR          = "PID Action on Error"
    PI_ERROR_D_PV         = "PI Action on Error, D Action on PV"
    I_ERROR_PD_PV         = "I Action on Error, PD Action on PV"
    PD_ON_ERROR           = "PD Action on Error"
    P_ERROR_D_PV          = "P Action on Error, D Action on PV"
    ID_ON_ERROR           = "ID Action on Error"
    I_ERROR_D_PV          = "I Action on Error, D Action on PV"
    TWO_DOF               = "Two Degrees of Freedom"


class LinearizationType(Enum):
    """L_TYPE — input linearization option."""
    DIRECT              = "Direct"
    INDIRECT            = "Indirect"
    INDIRECT_SQUARE_ROOT = "IndirectSquareRoot"


class ShedOption(Enum):
    """SHED_OPT — remote shedding behavior on timeout."""
    NO_ACTION = "No Action"
    NORMAL    = "Normal"
    MANUAL    = "Manual"
    AUTO      = "Auto"


class TrackOption(Enum):
    """TRACK_OPT — behavior when TRK_VAL quality is bad."""
    ALWAYS_USE_VALUE    = "Always Use Value"
    USE_LAST_GOOD_VALUE = "Use Last Good Value"
    TRACK_IF_BAD        = "Track if Bad"


class ProcessType(Enum):
    """PROCESS_TYPE — informational metadata."""
    SELF_REGULATING = "Self-Regulating"
    INTEGRATING     = "Integrating"


class LimitStatus(Enum):
    """Status qualifiers passed via BKCAL."""
    NOT_LIMITED    = auto()
    HIGH_LIMITED   = auto()
    LOW_LIMITED    = auto()
    NOT_CONNECTED  = auto()   # Bad:NotConnected (BKCAL not wired)
    NOT_INVITED    = auto()   # Slave not yet in CAS
    CONSTANT       = auto()   # Cannot move at all (source block in Man/OOS)


# ---------------------------------------------------------------------------
# Helper data structures
# ---------------------------------------------------------------------------

@dataclass
class SignalStatus:
    """Composite value+status as used in ISA BKCAL/IN/OUT parameters.

    ``uncertain`` is the third Azeo quality (Good / Uncertain / Bad). It
    defaults to False, so every existing caller keeps producing a Good signal;
    STATUS_OPTS *Use Uncertain as Good* decides whether an Uncertain signal is
    usable for control (see :meth:`PIDBlock._status_is_bad`).
    """
    value: float = 0.0
    limit: LimitStatus = LimitStatus.NOT_LIMITED
    bad: bool = False
    uncertain: bool = False

    def is_limited(self) -> bool:
        return self.limit in (LimitStatus.HIGH_LIMITED, LimitStatus.LOW_LIMITED)


@dataclass
class ScaleRange:
    """EU100/EU0 scale range (maps to PV_SCALE, OUT_SCALE, etc.)."""
    eu0:   float = 0.0
    eu100: float = 100.0

    @property
    def span(self) -> float:
        return self.eu100 - self.eu0

    def clamp(self, value: float, *, margin: float = 0.0) -> float:
        lo = self.eu0  - margin * self.span
        hi = self.eu100 + margin * self.span
        return max(lo, min(hi, value))

    def to_percent(self, value: float) -> float:
        if math.isclose(self.span, 0.0):
            return 0.0
        return (value - self.eu0) / self.span * 100.0

    def from_percent(self, pct: float) -> float:
        return self.eu0 + pct / 100.0 * self.span


@dataclass
class BlockError:
    """BLOCK_ERR bit-field (§ Block Errors)."""
    out_of_service:  bool = False
    readback_failed: bool = False
    output_failure:  bool = False
    local_override:  bool = False
    simulate_active: bool = False
    input_failure:   bool = False   # Bad PV

    def any_active(self) -> bool:
        return any(vars(self).values())

    def summary(self) -> list[str]:
        names = {
            "out_of_service":  "Out of Service",
            "readback_failed": "Readback Failed",
            "output_failure":  "Output Failure",
            "local_override":  "Local Override",
            "simulate_active": "Simulate Active",
            "input_failure":   "Input Failure / Bad PV",
        }
        return [label for attr, label in names.items() if getattr(self, attr)]


# ---------------------------------------------------------------------------
# Alarm state
# ---------------------------------------------------------------------------

@dataclass
class AlarmState:
    hi_hi_act: bool = False
    hi_act:    bool = False
    lo_act:    bool = False
    lo_lo_act: bool = False
    dv_hi_act: bool = False
    dv_lo_act: bool = False


# ---------------------------------------------------------------------------
# Main PID Function Block
# ---------------------------------------------------------------------------

class PIDBlock:
    """
    ISA-style PID Function Block.

    Usage::
        pid = PIDBlock(name="TIC-101")
        pid.pv_scale = ScaleRange(0.0, 200.0)
        pid.out_scale = ScaleRange(0.0, 100.0)
        pid.gain = 1.2
        pid.reset = 30.0   # seconds/repeat
        pid.rate = 0.0
        pid.set_target_mode(Mode.Auto)

        for t, pv_raw in process_data:
            pid.IN = SignalStatus(value=pv_raw)
            pid.execute(dt=1.0)
            print(pid.OUT.value)
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, name: str = "PID1"):
        self._name = name
        self._log = logging.getLogger(f"PID.Block.{name}")
        self._log.info("Initializing PID block '%s'", name)

        # ---- Scales ----
        self.pv_scale:  ScaleRange = ScaleRange(0.0, 100.0)
        self.out_scale: ScaleRange = ScaleRange(0.0, 100.0)
        self.ff_scale:  ScaleRange = ScaleRange(0.0, 100.0)
        self.trk_scale: ScaleRange = ScaleRange(0.0, 100.0)

        # ---- Tuning parameters ----
        self.gain:   float = 1.0    # normalized proportional gain
        self.reset:  float = 60.0   # integral time [s/repeat]; 0 = disable I
        self.rate:   float = 0.0    # derivative time [s]; 0 = disable D
        self.alpha:  float = 0.125  # derivative filter factor (0.05–1.0)
        self.bias:   float = 0.0    # manual reset (P/PD structures only)
        self.beta:   float = 1.0    # Two-DoF: fraction of P on SP
        self.gamma:  float = 0.0    # Two-DoF: fraction of D on SP

        # ---- Nonlinear gain ----
        self.nl_gap:    float = 0.0   # control gap (PV scale units)
        self.nl_hyst:   float = 0.0   # hysteresis
        self.nl_tband:  float = 0.0   # transition band
        self.nl_minmod: float = 1.0   # minimum gain modifier (0=deadband)
        self._nl_active: bool = False  # tracks whether error exceeded gap+hyst

        # ---- Feature flags (FRSIPID_OPTS) ----
        self.use_pidplus:            bool = False
        self.use_nonlinear_gain:     bool = False
        self.dynamic_reset_limit:    bool = False
        self.use_delayed_out_bad_pv: bool = False

        # ---- Algorithm option selectors -------------------------------
        # Every one of these defaults to the historical behaviour of this
        # block so that a strategy that does not set them is bit-identical
        # to the pre-audit implementation.  The non-default value is the
        # Azeo-faithful one (see doc/AZEO_FUNCTION_BLOCKS.md, PID).
        #
        #   reset_impl        "positional"  Σ e·dt/Tr accumulator (today)
        #                     "external"    the accumulator is driven by
        #                                   BKCAL_IN instead of by the error,
        #                                   so a limited slave stops the
        #                                   master's reset (Azeo DRL).  The
        #                                   state is still held in PV-error
        #                                   units, i.e. a GAIN change still
        #                                   rescales it.
        #                     "positive_feedback"
        #                                   the complete Azeo reset network:
        #                                   OUT = GAINa·KNL·E + filter(FB, Tr)
        #                                   with the reset state ``_reset_fb``
        #                                   held in *output* units and fed from
        #                                   BKCAL_IN (Dynamic Reset Limit) or
        #                                   from OUT.  A GAIN change no longer
        #                                   rescales accumulated reset and any
        #                                   downstream limit inherently limits
        #                                   the integral.
        #   deriv_filter_mode "legacy"      memoryless derivative attenuator
        #                     "azeo"      real Td·s/(a·Td·s+1) filter state
        #   series_impl       "legacy"      (1 + d_term)·(p + i)
        #                     "azeo"      derivative applied to the PI signal
        #   dv_alarm_basis    "error"       deviation alarms on SP − PV (today)
        #                     "azeo"      deviation alarms on PV − SP
        #   alarm_hys_units   "eu"          ALARM_HYS in engineering units
        #                     "percent"     ALARM_HYS as % of PV span
        self.reset_impl:        str = "positional"
        self.deriv_filter_mode: str = "legacy"
        self.series_impl:       str = "legacy"
        self.dv_alarm_basis:    str = "error"
        self.alarm_hys_units:   str = "eu"
        # SP_RATE_* keeps SP as the operator target and ramps only SP_WRK
        # (Azeo).  False = today: SP is overwritten by the ramped value,
        # which stalls the ramp when SP is written once.
        self.sp_rate_keeps_target: bool = False
        # Restrict SP_HI_LIM/SP_LO_LIM to PV_SCALE ± 10 % of span at runtime,
        # the way the output limits already are.
        self.sp_limit_restriction: bool = False
        # PIDPlus: carry the dt of scans without a new measurement into the
        # next integral update instead of losing it.
        self.pidplus_dt_accumulate: bool = False
        # PIDPlus "improved integral": the reset step over an interval Δt is
        # the exact first-order response 1 − exp(−Δt/RESET) instead of the
        # Euler approximation Δt/RESET.  The two agree for Δt ≪ RESET; the
        # exponential form stays bounded when the measurement interval
        # approaches or exceeds RESET, which is the case PIDPlus exists for
        # (slow / wireless transmitters).  Applies to every reset network.
        self.pidplus_improved_integral: bool = False
        # Exception reporting: an engine that genuinely knows whether a fresh
        # measurement arrived this scan sets this (True/False) before
        # execute(); None means "infer it", which is all the FBD runtime can
        # do — it publishes value + quality + limit on a wire, not a sample
        # timestamp.  A Constant limit status on IN is the one honest "this
        # measurement did not update" signal available, and is honoured below.
        self.measurement_updated: Optional[bool] = None
        # FRSIPID nonlinear gain: force STANDARD form + P-on-error, per spec.
        self.nl_force_standard: bool = False
        # Honour a limited BKCAL_IN status as a windup stop (Azeo's default,
        # non-DRL protection).  Inert until something publishes a limited
        # status on BKCAL_IN — the FBD wrapper only does so when the optional
        # BKCAL_HI_LIM / BKCAL_LO_LIM terminals are wired.
        self.bkcal_limit_windup: bool = True

        # ---- PID equation options ----
        self.form:      PIDForm   = PIDForm.STANDARD
        self.structure: Structure = Structure.PI_ERROR_D_PV

        # ---- Output limits ----
        self.out_hi_lim: float = 100.0
        self.out_lo_lim: float = 0.0
        # Anti-Reset Windup limits
        self.arw_hi_lim: float = 100.0
        self.arw_lo_lim: float = 0.0

        # ---- Setpoint limits / rate ----
        self.sp_hi_lim:   float = 100.0
        self.sp_lo_lim:   float = 0.0
        self.sp_rate_up:  float = 0.0   # 0 = immediate (PV units/s)
        self.sp_rate_dn:  float = 0.0
        self.sp_ftime:    float = 0.0   # SP filter time constant [s]
        self.pv_ftime:    float = 0.0   # PV filter time constant [s]

        # ---- Alarm limits ----
        self.hi_hi_lim: float = math.inf
        self.hi_lim:    float = math.inf
        self.lo_lim:    float = -math.inf
        self.lo_lo_lim: float = -math.inf
        self.dv_hi_lim: float = math.inf   # deviation high
        self.dv_lo_lim: float = -math.inf  # deviation low (negative)
        self.alarm_hys: float = 0.0

        # ---- Feedforward ----
        self.ff_enable: bool  = False
        self.ff_gain:   float = 1.0
        self.FF_VAL:    SignalStatus = SignalStatus()

        # ---- Tracking ----
        self.track_enable:    bool = False  # CONTROL_OPTS: Track Enable
        self.track_in_manual: bool = False  # CONTROL_OPTS: Track in Manual
        self.TRK_IN_D:        bool = False  # discrete track trigger
        self.TRK_VAL:         SignalStatus = SignalStatus()

        # ---- Direct/Reverse acting ----
        self.direct_acting: bool = False    # False = reverse acting (default)

        # ---- SP-PV tracking options ----
        self.sp_pv_track_lo_iman:  bool = False
        self.sp_pv_track_man:      bool = False
        self.sp_pv_track_rout:     bool = False
        self.use_pv_for_bkcal_out: bool = False
        self.obey_sp_lim_cas_rcas: bool = False
        self.no_out_limits_in_man: bool = False

        # ---- Bypass ----
        self.bypass:        bool = False
        self.bypass_enable: bool = False

        # ---- Simulation ----
        self.simulate_enabled: bool = False
        self.SIMULATE_IN:      SignalStatus = SignalStatus(
            limit=LimitStatus.NOT_CONNECTED
        )

        # ---- PIDPlus saturation recovery ----
        self.recovery_fltr: float = 1.0  # 0.0 (aggressive) – 1.0 (standard)

        # ---- I-deadband ----
        self.ideadband: float = 0.0

        # ---- Balance time (P/PD structures) ----
        self.bal_time: float = 0.0  # seconds; bias decay after Man→Auto

        # ---- Linearization ----
        self.l_type: LinearizationType = LinearizationType.DIRECT
        self.low_cutoff_enabled: bool = False
        self.low_cut: float = 0.0  # PV EU; force PV=0 below this

        # ---- Remote shedding ----
        self.shed_opt:  ShedOption = ShedOption.NORMAL
        self.shed_time: float = 0.0  # seconds; 0=disabled
        self._remote_age: float = 0.0  # seconds since last fresh host write

        # ---- Tracking option ----
        self.track_opt: TrackOption = TrackOption.ALWAYS_USE_VALUE

        # ---- Process type (metadata only) ----
        self.process_type: ProcessType = ProcessType.SELF_REGULATING

        # ---- Increase-to-close (I/O inversion) ----
        self.increase_to_close: bool = False

        # ---- STATUS_OPTS ----
        self.bad_if_limited:          bool = False
        self.uncertain_if_limited:    bool = False
        self.target_manual_if_bad_in: bool = False
        self.use_uncertain_as_good:   bool = False

        # ---- Conditional alarm ----
        self.condalm_enabled: bool = True

        # ---- SP track / bias options ----
        self.sp_track_retained_target: bool = False
        self.act_on_ir: bool = False

        # ---- Statistics / variability ----
        self.stdev_time:  float = 0.0   # seconds; 0=disabled
        self.stdev_cap:   float = 100.0
        self.var_idx_lim: float = 100.0  # %; 100=disabled

        # ---- Diagnostic outputs (computed each scan) ----
        self.field_val:    float = 0.0   # PV as % of span
        self.nl_gain_mod:  float = 1.0   # effective NL gain multiplier
        self.stdev:        float = 0.0   # running standard deviation
        self.var_idx:      float = 0.0   # variability index %
        self.bad_active:   bool  = False
        self.abnorm_active: bool = False
        self._delayed_bad_pv_active: bool = False
        self._io_out_value: float = 0.0  # post increase-to-close

        # ---- BKCAL / cascade ports ----
        self.BKCAL_IN:  SignalStatus = SignalStatus(limit=LimitStatus.NOT_INVITED)
        self.BKCAL_OUT: SignalStatus = SignalStatus()
        self.CAS_IN:    SignalStatus = SignalStatus()
        self.ROUT_IN:   SignalStatus = SignalStatus()
        self.RCAS_IN:   SignalStatus = SignalStatus()

        # ---- Input / Output ----
        self.IN:  SignalStatus = SignalStatus()
        self.OUT: SignalStatus = SignalStatus()

        # ---- Public state ----
        self.PV:     float = 0.0
        self.SP:     float = 0.0
        self.SP_WRK: float = 0.0   # working SP (after rate limiting)
        self.ERROR:  float = 0.0

        # ---- Mode state ----
        self._target_mode: Mode  = Mode.OOS
        self._actual_mode: Mode  = Mode.OOS
        self._permitted_modes: set[Mode] = {
            Mode.OOS, Mode.Man, Mode.Auto, Mode.Cas, Mode.RCas, Mode.ROut
        }

        # ---- Status / alarms ----
        self.block_err:  BlockError = BlockError(out_of_service=True)
        self.alarm_state: AlarmState = AlarmState()

        # ---- PID term contributions (cached for trending) ----
        self.p_term: float = 0.0
        self.i_term: float = 0.0
        self.d_term: float = 0.0

        # ---- Internal integrator state ----
        self._integral:         float = 0.0   # integrator accumulator
        self._integral_prev:    float = 0.0   # reset value at start of scan
        self._deriv_filter:     float = 0.0   # filtered derivative state
        self._prev_pv:          float = 0.0   # PV at previous scan
        self._prev_sp:          float = 0.0   # SP at previous scan
        self._saturated_time:   float = 0.0   # continuous saturation duration
        self._new_measurement:  bool = True
        self._deriv_state:      float = 0.0   # filtered derivative (azeo mode)
        self._series_prev: Optional[float] = None  # previous PI signal (series)
        self._series_dstate:    float = 0.0   # filtered d/dt of the PI signal
        self._pending_dt:       float = 0.0   # PIDPlus: unused elapsed time
        self._bad_pv_latched:   bool = False  # Target-to-Manual-if-Bad-IN latch
        # Positive-feedback (external-reset) network state, in OUT_SCALE units.
        # Only used by reset_impl = "positive_feedback"; kept in step with
        # ``_integral`` (= _reset_fb / GAINa) so every existing consumer of the
        # accumulator — bumpless transfer, faceplates, mode changes — is
        # unaffected by which network is selected.
        self._reset_fb:         float = 0.0
        self._reset_fb_prev:    float = 0.0

        # ---- SP filter state ----
        self._sp_filt: float = 0.0

        # ---- PV filter state ----
        self._pv_filt: float = 0.0

        self._log.info("Block '%s' initialized in OOS mode.", name)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def target_mode(self) -> Mode:
        return self._target_mode

    @property
    def actual_mode(self) -> Mode:
        return self._actual_mode

    @property
    def name(self) -> str:
        return self._name

    # ------------------------------------------------------------------
    # Mode management
    # ------------------------------------------------------------------

    def set_target_mode(self, mode: Mode) -> None:
        """
        Set the desired (target) mode.  Actual mode may differ during
        cascade initialization (IMan) or on error conditions.
        """
        if mode not in self._permitted_modes:
            self._log.warning(
                "Mode %s is not in permitted modes %s – request ignored.",
                mode, self._permitted_modes
            )
            return

        if mode == self._target_mode:
            # Re-selecting the mode the block has already targeted still has to
            # re-resolve when the *actual* mode has shed away from it (Bad PV,
            # cascade not invited); otherwise an operator pressing AUTO after a
            # shed gets no response at all. Silent — logging here would spam the
            # journal for callers that re-assert the mode every scan.
            if self._actual_mode != mode:
                self._resolve_mode()
            return

        self._log.info("Mode request: %s -> %s", self._target_mode, mode)
        self._target_mode = mode
        self._resolve_mode()

    def _resolve_mode(self) -> None:
        """
        Determine actual mode from target mode and system conditions.
        Implements cascade initialization logic and shed conditions.
        """
        tgt = self._target_mode
        prev = self._actual_mode

        # ---- OOS ----
        if tgt == Mode.OOS:
            self._actual_mode = Mode.OOS
            self.block_err.out_of_service = True
            if prev != Mode.OOS:
                self._log.info("Block '%s' is Out of Service.", self._name)
            return

        self.block_err.out_of_service = False

        # ---- Bad PV check ----
        if self._pv_is_bad():
            self._actual_mode = Mode.Man
            # STATUS_OPTS "Target to Manual if Bad IN": the *target* latches in
            # Manual too, so the loop does not silently resume control when the
            # transmitter recovers — an operator has to re-select Auto.
            if self.target_manual_if_bad_in and tgt != Mode.Man:
                self._log.warning(
                    "PV is Bad and Target-to-Manual-if-Bad-IN is set – target "
                    "mode latched to Manual (block: %s).", self._name)
                self._target_mode = Mode.Man
                self._bad_pv_latched = True
            if prev != Mode.Man:
                self._log.warning(
                    "PV is Bad – actual mode forced to Manual (block: %s).", self._name
                )
            return

        # ---- Bad cascade setpoint source ----
        # Azeo sheds a Cas/RCas block whose SP source is Bad to the next
        # permitted mode at or above Manual — Auto (holding the last working
        # setpoint) when Auto is permitted. Inert unless a caller marks the
        # CAS_IN / RCAS_IN status Bad.
        if ((tgt == Mode.Cas and self._status_is_bad(self.CAS_IN))
                or (tgt == Mode.RCas and self._status_is_bad(self.RCAS_IN))):
            self._actual_mode = (
                Mode.Auto if Mode.Auto in self._permitted_modes else Mode.Man)
            if prev != self._actual_mode:
                self._log.warning(
                    "%s input is Bad – %s shed to %s.",
                    "CAS_IN" if tgt == Mode.Cas else "RCAS_IN",
                    self._name, self._actual_mode.value)
            return

        # ---- Cascade / RCas modes require BKCAL invitation ----
        if tgt in (Mode.Cas, Mode.RCas):
            if self.BKCAL_IN.limit == LimitStatus.NOT_INVITED:
                self._actual_mode = Mode.IMan
                if prev != Mode.IMan:
                    self._log.debug(
                        "Target=%s but BKCAL_IN is Not-Invited -> IMan.", tgt
                    )
            else:
                self._actual_mode = tgt
                if prev != tgt:
                    self._log.debug("Cascade active: actual mode = %s.", tgt)
            return

        # ---- ROut ----
        if tgt == Mode.ROut:
            if prev != Mode.ROut:
                # Bumpless entry: seed the remote output with the current OUT
                # so there is no jump until the host (DMC) writes ROUT_IN.
                self.ROUT_IN = SignalStatus(value=self.OUT.value)
            self._actual_mode = Mode.ROut
            return

        # ---- Manual ----
        if tgt == Mode.Man:
            self._actual_mode = Mode.Man
            return

        # ---- Auto ----
        if tgt == Mode.Auto:
            # If BKCAL_IN is still Not-Invited (no downstream cascade) that's fine
            self._actual_mode = Mode.Auto
            return

        self._actual_mode = tgt

    # ------------------------------------------------------------------
    # Simulation / PV selection
    # ------------------------------------------------------------------

    def _get_raw_pv(self) -> SignalStatus:
        """Return the PV source signal respecting simulation state."""
        if self.simulate_enabled:
            # SIMULATE_IN takes priority over manual SIMULATE value
            if self.SIMULATE_IN.limit != LimitStatus.NOT_CONNECTED:
                self._log.debug("Simulation: using SIMULATE_IN value.")
                return self.SIMULATE_IN
            # Fallback: manual simulated value stored in SIMULATE_IN.value
            self._log.debug("Simulation: using manual SIMULATE value.")
            return self.SIMULATE_IN
        return self.IN

    def _status_is_bad(self, sig: SignalStatus) -> bool:
        """Is this signal unusable for control?

        Bad always is.  Uncertain is too, unless STATUS_OPTS *Use Uncertain as
        Good* is set — Azeo's rule.  Nothing in this platform published an
        Uncertain status before terminals carried quality, so the default
        cannot change any existing loop.
        """
        if sig.bad:
            return True
        return bool(sig.uncertain) and not self.use_uncertain_as_good

    def _pv_is_bad(self) -> bool:
        return self._status_is_bad(self._get_raw_pv())

    # ------------------------------------------------------------------
    # Signal filtering helpers
    # ------------------------------------------------------------------

    def _first_order_filter(
        self, current: float, previous: float, tau: float, dt: float
    ) -> float:
        """
        First-order exponential filter.
        τ = 0  ⟹  no filtering (pass-through).
        """
        if tau <= 0.0 or dt <= 0.0:
            return current
        alpha = dt / (dt + tau)
        return previous + alpha * (current - previous)

    # ------------------------------------------------------------------
    # Nonlinear gain (KNL) calculation
    # ------------------------------------------------------------------

    def _compute_knl(self, error: float) -> float:
        """
        Compute nonlinear gain modifier KNL from error and NL parameters.
        See Figure: KNL calculation in the documentation.
        """
        if not self.use_nonlinear_gain:
            # NL_GAIN_MOD readback — published every scan so the faceplate
            # shows the modifier actually in force (1.0 when NL is off).
            self.nl_gain_mod = 1.0
            return 1.0

        abs_err = abs(error)
        gap     = self.nl_gap
        hyst    = self.nl_hyst
        tband   = self.nl_tband
        minmod  = self.nl_minmod

        # Hysteresis: once error has exceeded gap+hyst, must return below gap
        if self._nl_active:
            if abs_err < gap:
                self._nl_active = False
                self._log.debug("KNL: hysteresis cleared (|E|=%.3f < gap=%.3f)", abs_err, gap)
        else:
            if abs_err > gap + hyst:
                self._nl_active = True
                self._log.debug("KNL: hysteresis triggered (|E|=%.3f > gap+hyst=%.3f)", abs_err, gap+hyst)

        if not self._nl_active:
            # Within gap -> minimum gain
            knl = minmod
        else:
            outer_edge = gap + tband
            if abs_err >= outer_edge or math.isclose(tband, 0.0):
                knl = 1.0
            else:
                # Linear transition between minmod and 1.0
                fraction = (abs_err - gap) / tband if tband > 0 else 1.0
                knl = minmod + fraction * (1.0 - minmod)

        self._log.debug("KNL=%.4f  |E|=%.3f  gap=%.3f  hyst=%.3f  tband=%.3f",
                        knl, abs_err, gap, hyst, tband)
        self.nl_gain_mod = knl
        return knl

    # ------------------------------------------------------------------
    # Feedforward calculation
    # ------------------------------------------------------------------

    def _compute_feedforward(self) -> float:
        """
        Compute feedforward contribution F(s) (in OUT_SCALE units).
        F = FF_VAL_scaled × FF_GAIN
        """
        if not self.ff_enable or self.FF_VAL.bad:
            return 0.0

        # Scale FF_VAL from ff_scale to out_scale
        ff_pct = self.ff_scale.to_percent(self.FF_VAL.value)
        ff_scaled = self.out_scale.from_percent(ff_pct)
        contribution = ff_scaled * self.ff_gain

        self._log.debug(
            "Feedforward: FF_VAL=%.3f  scaled=%.3f  gain=%.3f  F=%.3f",
            self.FF_VAL.value, ff_scaled, self.ff_gain, contribution
        )
        return contribution

    # ------------------------------------------------------------------
    # BETA / GAMMA lookup from structure
    # ------------------------------------------------------------------

    _STRUCTURE_BETA_GAMMA = {
        Structure.PID_ON_ERROR:   (1.0, 1.0),
        Structure.PI_ERROR_D_PV:  (1.0, 0.0),
        Structure.I_ERROR_PD_PV:  (0.0, 0.0),
        Structure.PD_ON_ERROR:    (1.0, 1.0),
        Structure.P_ERROR_D_PV:   (1.0, 0.0),
        Structure.ID_ON_ERROR:    (None, 1.0),  # None = no P term
        Structure.I_ERROR_D_PV:   (None, 0.0),
        Structure.TWO_DOF:        None,         # uses configured values
    }

    def _get_beta_gamma(self) -> tuple[Optional[float], float]:
        entry = self._STRUCTURE_BETA_GAMMA.get(self.structure)
        if entry is None:
            return self.beta, self.gamma
        if entry[0] is None:
            return None, entry[1]
        return entry

    # ------------------------------------------------------------------
    # GAIN normalization (GAINa)
    # ------------------------------------------------------------------

    def _normalized_gain(self) -> float:
        """
        GAINa = GAIN × (OUT_span / PV_span)

        ISA convention: GAIN is in %output / %PV.
        1% PV = PV_span/100 EU;  1% OUT = OUT_span/100 EU.
        For GAIN=1: 1%PV error → 1%OUT change → OUT_span/100 EU output.
        So: gain_a * (PV_span/100) = OUT_span/100  ⟹  gain_a = OUT_span/PV_span.
        With GAIN factor: gain_a = GAIN × OUT_span / PV_span.
        """
        if math.isclose(self.pv_scale.span, 0.0):
            self._log.error("PV span is zero – cannot normalize gain!")
            return self.gain
        return self.gain * (self.out_scale.span / self.pv_scale.span)

    # ------------------------------------------------------------------
    # PID calculation
    # ------------------------------------------------------------------

    def _compute_pid_output(
        self, error: float, pv: float, sp: float, dt: float
    ) -> float:
        """
        Core PID calculation producing a raw (pre-limit) output contribution.
        Implements both Standard and Series forms, with all STRUCTURE options.
        Returns delta in OUT_SCALE engineering units.
        """
        beta, gamma = self._get_beta_gamma()
        gain_a = self._normalized_gain()
        Tr     = self.reset   # reset time [s/repeat]
        Td     = self.rate    # derivative time [s]
        a      = self.alpha   # derivative filter

        # Apply reverse/direct acting at the error level so that all
        # internal calculations (integrator, ARW, output limits) work in
        # the same output-scale coordinate system.
        sign = -1.0 if self.direct_acting else 1.0
        calc_error = sign * error
        calc_pv    = sign * pv
        calc_sp    = sign * sp

        knl = self._compute_knl(calc_error)

        # FRSIPID "Use Nonlinear Gain Modification" additionally forces the
        # standard form with proportional action on error (per spec).
        force_standard = self.use_nonlinear_gain and self.nl_force_standard
        if force_standard and beta is not None:
            beta = 1.0

        # Integral enable / reset enable
        integral_enabled    = self.structure not in (
            Structure.PD_ON_ERROR, Structure.P_ERROR_D_PV
        )
        proportional_enabled = beta is not None
        derivative_enabled   = Td > 0.0

        # ------ Proportional term ------
        if proportional_enabled:
            # Azeo two-degrees-of-freedom setpoint weighting acts on the
            # setpoint, not on the error: BETA scales how much of an SP change
            # reaches proportional action, leaving disturbance response (which
            # moves PV alone) at full gain. beta*(SP-PV) would instead scale
            # the whole loop gain. Matches the derivative form below and the
            # bumpless-transfer conditioning in pid_block.py.
            e_p = beta * calc_sp - calc_pv
            p_term = knl * e_p
        else:
            p_term = 0.0

        # ------ Integral term ------
        # Snapshot reset at the start of the scan so the output limiter can
        # freeze it (undo this scan's growth) when OUT ends up limited.
        self._integral_prev = self._integral
        self._reset_fb_prev = self._reset_fb
        i_term = 0.0
        pf = self.reset_impl == "positive_feedback"
        if integral_enabled and Tr > 0.0:
            # PIDPlus: a scan without a new measurement contributes no integral
            # action; with pidplus_dt_accumulate its elapsed time is carried
            # into the next update instead of being dropped.
            i_dt = dt
            if self.use_pidplus and self.pidplus_dt_accumulate:
                if not self._new_measurement:
                    self._pending_dt += dt
                else:
                    i_dt = dt + self._pending_dt
                    self._pending_dt = 0.0
            # Azeo: a PV whose limit status is Constant cannot move, so it
            # carries no new information and integration stops until it does.
            # The one source of that status today is an upstream AI held in
            # Manual or out of service.
            pv_constant = self._get_raw_pv().limit == LimitStatus.CONSTANT
            # Dead-band: stop integration when error is within IDEADBAND
            if abs(calc_error) > self.ideadband and not pv_constant:
                # PIDPlus: update integral only when new measurement is available
                if self.use_pidplus and not self._new_measurement:
                    pass  # skip integral update this scan
                elif pf:
                    # Azeo reset network in full: the reset state is a lag of
                    # the *output* signal (positive feedback), or of BKCAL_IN
                    # when Dynamic Reset Limit selects the external reset. It
                    # lives in OUT_SCALE units, so an online GAIN change no
                    # longer rescales accumulated reset, and a slave that is
                    # stuck or limited limits the master's reset by
                    # construction. Adding it to GAINa·E below reproduces
                    # ordinary integral action when nothing limits.
                    src = self._reset_feedback_source()
                    self._reset_fb += (src - self._reset_fb) * self._reset_weight(i_dt, Tr)
                elif self._external_reset_active():
                    # Partial external reset: the accumulator follows BKCAL_IN
                    # but is still held in PV-error units.  Feedforward is
                    # summed outside the PID algorithm; remove it from reset
                    # feedback or a constant FF contribution is integrated a
                    # second time and slowly drives the loop away from the
                    # achieved downstream output.
                    feedback = self.BKCAL_IN.value - self._compute_feedforward()
                    fb = feedback / gain_a if gain_a > 1e-12 else 0.0
                    self._integral += (fb - self._integral) * self._reset_weight(i_dt, Tr)
                else:
                    i_increment = knl * calc_error * self._reset_weight(i_dt, Tr)
                    self._integral += i_increment
            if pf:
                # Mirror the OUT-units reset state into the accumulator every
                # scan so the ARW / limit clamps below, the bumpless-transfer
                # conditioning and the faceplates all keep working unchanged.
                self._integral = (self._reset_fb / gain_a
                                  if abs(gain_a) > 1e-12 else 0.0)
            # Anti-Reset Windup: once the computed output passes an ARW limit,
            # stop reset accumulating further in that direction (freeze this
            # scan's growth). The previous decay was multiplied by gain_a with a
            # Tr/16 time constant, so at realistic gains a single scan drove the
            # integral through zero and reversed the output under a sustained
            # error. Freezing — rather than re-seating the integral onto the
            # limit — leaves the accumulated reset intact, so the loop resumes
            # from where it was instead of fighting the proportional term.
            if gain_a > 1e-12:
                out_est = gain_a * (p_term + self._integral)
                if out_est > self.arw_hi_lim and self._integral > self._integral_prev:
                    self._integral = self._integral_prev
                    self._log.debug("ARW high limit active – reset frozen.")
                elif out_est < self.arw_lo_lim and self._integral < self._integral_prev:
                    self._integral = self._integral_prev
                    self._log.debug("ARW low limit active – reset frozen.")
            # Azeo's default windup protection: the reset contribution is
            # clamped when BKCAL_IN reports a limit and the increment would
            # drive further into it. The downstream block (AO at its travel
            # limit, SCALER clamped, slave in Man) is the authority — the local
            # OUT clamp only sees limits this block imposed on itself.
            if self.bkcal_limit_windup and self._integral != self._integral_prev:
                _bk = self.BKCAL_IN.limit
                if _bk == LimitStatus.HIGH_LIMITED and self._integral > self._integral_prev:
                    self._integral = self._integral_prev
                    self._log.debug("BKCAL_IN high-limited – reset frozen.")
                elif _bk == LimitStatus.LOW_LIMITED and self._integral < self._integral_prev:
                    self._integral = self._integral_prev
                    self._log.debug("BKCAL_IN low-limited – reset frozen.")
                elif _bk == LimitStatus.CONSTANT:
                    # The downstream block cannot move at all (it is in Manual
                    # / out of service), so no amount of reset reaches the
                    # process.
                    self._integral = self._integral_prev
                    self._log.debug("BKCAL_IN constant – reset frozen.")
            if pf and self._integral == self._integral_prev:
                # A freeze above undid this scan's step; the OUT-units reset
                # state has to be rolled back with it.
                self._reset_fb = self._reset_fb_prev
            i_term = self._integral

        # ------ Derivative term ------
        d_term = 0.0
        if derivative_enabled:
            # Determine what signal derivative is taken on
            if self.structure in (
                Structure.PID_ON_ERROR, Structure.ID_ON_ERROR, Structure.PD_ON_ERROR
            ):
                d_signal_now = gamma * calc_sp - calc_pv if gamma else -calc_pv
            elif self.structure == Structure.TWO_DOF:
                d_signal_now = gamma * calc_sp - calc_pv
            else:
                # D on PV only
                d_signal_now = -calc_pv

            d_signal_prev = self._deriv_filter

            if dt > 0.0:
                raw_deriv = (d_signal_now - d_signal_prev) / dt
                # First-order derivative filter: τ = a × Td
                tau_d = a * Td
                if self.deriv_filter_mode == "azeo":
                    # Td·s/(a·Td·s+1) — a real lag with state, so a PV step
                    # produces derivative action that decays over ~a·Td instead
                    # of the single-scan impulse the memoryless form gave.
                    self._deriv_state = self._first_order_filter(
                        raw_deriv, self._deriv_state, tau_d, dt)
                    d_term = Td * self._deriv_state
                else:
                    filt_deriv = self._first_order_filter(raw_deriv, 0.0, tau_d, dt)
                    if self.form == PIDForm.STANDARD:
                        d_term = Td * filt_deriv
                    else:  # SERIES: derivative filter already in denominator
                        d_term = Td / (a * Td + dt) * (d_signal_now - d_signal_prev)

            self._deriv_filter = d_signal_now

        # ------ Combine P, I, D ------
        use_series = (self.form == PIDForm.SERIES) and not force_standard
        integral_active = integral_enabled and Tr > 0.0
        if not use_series:
            if integral_active:
                raw = gain_a * (p_term + i_term + d_term)
            else:
                raw = gain_a * (p_term + d_term) + self.bias
        elif self.series_impl == "azeo":
            # Azeo series: GAINa·(1 + Td·s/(a·Td·s+1))·((Tr·s+1)/(Tr·s))·E —
            # the derivative operator acts on the *signal* leaving the PI
            # section. The legacy form multiplies by (1 + d_term) where d_term
            # carries engineering units, so its "derivative" scales with PV
            # magnitude instead of with the rate of change.
            pi_signal = (p_term + i_term) if integral_active else p_term
            d_series = 0.0
            if derivative_enabled and dt > 0.0:
                if self._series_prev is None:
                    self._series_prev = pi_signal
                raw_d = (pi_signal - self._series_prev) / dt
                self._series_dstate = self._first_order_filter(
                    raw_d, self._series_dstate, a * Td, dt)
                d_series = Td * self._series_dstate
            self._series_prev = pi_signal
            raw = gain_a * (pi_signal + d_series)
            if not integral_active:
                raw += self.bias
            d_term = d_series
        else:
            if integral_active:
                raw = gain_a * (1.0 + d_term) * (p_term + i_term)
            else:
                raw = gain_a * (1.0 + d_term) * p_term + self.bias

        # Cache individual contributions for trending
        self.p_term = p_term
        self.i_term = i_term
        self.d_term = d_term

        return raw

    # ------------------------------------------------------------------
    # Dynamic Reset Limit (DRL)
    # ------------------------------------------------------------------

    def _reset_weight(self, dt: float, tr: float) -> float:
        """Fraction of the gap the reset network closes over ``dt``.

        ``dt/Tr`` is the Euler step this block has always used.  PIDPlus's
        *improved integral* uses the exact first-order response instead, which
        is identical for dt ≪ Tr and stays bounded by 1 when the measurement
        interval approaches or exceeds RESET (the Euler step overshoots, and
        past dt = 2·Tr the loop is unstable on the reset term alone).
        """
        if tr <= 0.0:
            return 0.0
        if self.use_pidplus and self.pidplus_improved_integral:
            return 1.0 - math.exp(-dt / tr)
        return dt / tr

    def _bkcal_reset_usable(self) -> bool:
        """True when BKCAL_IN may drive the reset network."""
        return (not self.BKCAL_IN.bad
                and self.BKCAL_IN.limit not in (
            LimitStatus.NOT_CONNECTED, LimitStatus.NOT_INVITED)
                )

    def _reset_feedback_source(self) -> float:
        """Feedback signal of the positive-feedback reset network.

        Azeo feeds it from the block's own OUT; *Dynamic Reset Limit* (and
        an explicit external reset) feeds it from BKCAL_IN instead, so the
        reset can never run away from what the downstream block is actually
        doing.
        """
        if ((self.dynamic_reset_limit or self.reset_impl == "external")
                and self._bkcal_reset_usable()):
            feedback = self.BKCAL_IN.value
        else:
            feedback = self.OUT.value
        # OUT/BKCAL include the separately summed feedforward contribution;
        # the positive-feedback reset network represents only the algorithm
        # output.  Removing FF here keeps both internal and external reset
        # networks symmetric with condition_for_output().
        return feedback - self._compute_feedforward()

    def _seed_reset(self, out_value: float) -> None:
        """Back-calculate both reset states from a known output.

        Used by every mode that holds the output (Man, ROut, LO, IMan) so the
        return to Auto is bumpless whichever reset network is selected.
        """
        algorithm_output = out_value - self._compute_feedforward()
        self._integral = (
            algorithm_output / (self._normalized_gain() + 1e-12)
        )
        self._reset_fb = algorithm_output

    def _external_reset_active(self) -> bool:
        """True when reset is taken from BKCAL_IN (external-reset network).

        Selected either explicitly (``reset_impl = "external"``) or by the
        FRSIPID *Dynamic Reset Limit* option, which is exactly this network in
        Azeo.  Requires a usable BKCAL_IN — an unwired one would drag the
        reset state to zero.
        """
        if not (self.dynamic_reset_limit or self.reset_impl == "external"):
            return False
        return self._bkcal_reset_usable()

    def _apply_dynamic_reset_limit(self, computed_out: float, dt: float) -> float:
        """Retained for callers that invoke it directly.

        Dynamic Reset Limit is now realised where it belongs — in the reset
        term (``_external_reset_active``) — so the output needs no nudge.  The
        old implementation added ``(BKCAL_IN − OUT)·dt/RESET`` to OUT while the
        integrator kept winding at full rate, and it *disabled* the
        output-limit windup clamp, so selecting Azeo's anti-windup option
        removed the last windup protection the block had.
        """
        return computed_out

    # ------------------------------------------------------------------
    # Output saturation recovery (PIDPlus)
    # ------------------------------------------------------------------

    def _apply_saturation_recovery(
        self, out_raw: float, dt: float
    ) -> float:
        """
        Enhanced saturation recovery (PIDPlus only).
        Modifies integral when output has been saturated for ≥ RESET time.
        """
        if not self.use_pidplus or math.isclose(self.recovery_fltr, 1.0):
            return out_raw

        at_limit = (
            out_raw >= self.out_hi_lim or out_raw <= self.out_lo_lim
        )

        if at_limit:
            self._saturated_time += dt
        else:
            self._saturated_time = 0.0
            return out_raw

        # Only engage recovery after saturation duration ≥ reset time
        if self._saturated_time < self.reset:
            return out_raw

        # Apply filter-based recovery
        f = self.recovery_fltr
        if out_raw >= self.out_hi_lim:
            target = self.out_hi_lim
        else:
            target = self.out_lo_lim

        recovered = target + f * (out_raw - target)
        self._log.debug(
            "Saturation recovery: fltr=%.2f  sat_time=%.1fs  raw=%.3f  recovered=%.3f",
            f, self._saturated_time, out_raw, recovered
        )
        return recovered

    # ------------------------------------------------------------------
    # SP limiting and rate-limiting
    # ------------------------------------------------------------------

    def _limit_sp(self, sp_desired: float) -> float:
        """Clamp SP to configured SP_HI_LIM / SP_LO_LIM.

        With ``sp_limit_restriction`` the configured limits are additionally
        restricted at runtime to PV_SCALE ± 10 % of span, the way the output
        limits already are — otherwise a placeholder ``sp_hi = 1e6`` clamps
        nothing and the operator can drive the setpoint off scale.
        """
        hi, lo = self.sp_hi_lim, self.sp_lo_lim
        if self.sp_limit_restriction:
            span = self.pv_scale.span
            hi = min(hi, self.pv_scale.eu100 + 0.1 * span)
            lo = max(lo, self.pv_scale.eu0 - 0.1 * span)
        sp_clamped = max(lo, min(hi, sp_desired))
        if not math.isclose(sp_clamped, sp_desired):
            self._log.debug(
                "SP clamped %.3f -> %.3f (limits=[%.3f, %.3f])",
                sp_desired, sp_clamped, lo, hi
            )
        return sp_clamped

    def _rate_limit_sp(self, sp_target: float, dt: float) -> float:
        """
        Apply SP_RATE_UP / SP_RATE_DN ramp limits to the working setpoint.
        A rate of 0.0 means immediate (no ramp).
        """
        if dt <= 0.0:
            return sp_target

        delta = sp_target - self.SP_WRK

        if delta > 0.0 and self.sp_rate_up > 0.0:
            max_step = self.sp_rate_up * dt
            delta = min(delta, max_step)
        elif delta < 0.0 and self.sp_rate_dn > 0.0:
            max_step = self.sp_rate_dn * dt
            delta = max(delta, -max_step)

        return self.SP_WRK + delta

    # ------------------------------------------------------------------
    # BKCAL_OUT update
    # ------------------------------------------------------------------

    def _update_bkcal_out(self) -> None:
        """
        BKCAL_OUT passes SP_WRK (or PV if Use PV for BKCAL_OUT is set)
        plus the current output limit status back to an upstream master.
        """
        if self.use_pv_for_bkcal_out:
            self.BKCAL_OUT.value = self.PV
        else:
            self.BKCAL_OUT.value = self.SP_WRK

        # Propagate limit status — this is what lets an upstream master stop
        # winding reset into a slave that is already at its limit.
        if self.OUT.limit in (LimitStatus.HIGH_LIMITED, LimitStatus.LOW_LIMITED):
            self.BKCAL_OUT.limit = self.OUT.limit
        else:
            self.BKCAL_OUT.limit = LimitStatus.NOT_LIMITED
        # ... and the quality selected by STATUS_OPTS goes with it.
        self.BKCAL_OUT.bad = self.OUT.bad
        self.BKCAL_OUT.uncertain = self.OUT.uncertain

    # ------------------------------------------------------------------
    # Alarm detection
    # ------------------------------------------------------------------

    def _check_alarms(self) -> None:
        """
        Evaluate all alarm conditions against current PV and SP values.
        Uses alarm hysteresis (ALARM_HYS).
        """
        pv  = self.PV
        hys = self.alarm_hys
        if self.alarm_hys_units == "percent":
            # Azeo ALARM_HYS is a percent of scale (capped at 50 %).
            hys = min(abs(hys), 50.0) * abs(self.pv_scale.span) / 100.0
        # Azeo compares the deviation alarms against PV − SP, so DV_HI_LIM
        # is the allowed excursion *above* setpoint. ERROR is SP − PV, i.e.
        # the opposite sign, which fires the wrong alarm of the pair.
        err = -self.ERROR if self.dv_alarm_basis == "azeo" else self.ERROR

        def _rising(current_act: bool, value: float,
                     hi: float, lo: Optional[float] = None) -> bool:
            if lo is None:
                lo = hi
            if current_act:
                return value > (lo - hys)
            return value > hi

        def _falling(current_act: bool, value: float,
                      lo: float, hi: Optional[float] = None) -> bool:
            if hi is None:
                hi = lo
            if current_act:
                return value < (hi + hys)
            return value < lo

        s = self.alarm_state
        # Save previous state (avoid deepcopy overhead at 200 calls/sec)
        prev_hh  = s.hi_hi_act
        prev_h   = s.hi_act
        prev_l   = s.lo_act
        prev_ll  = s.lo_lo_act
        prev_dvh = s.dv_hi_act
        prev_dvl = s.dv_lo_act

        s.hi_hi_act = _rising(prev_hh,  pv,  self.hi_hi_lim)
        s.hi_act    = _rising(prev_h,   pv,  self.hi_lim)
        s.lo_act    = _falling(prev_l,  pv,  self.lo_lim)
        s.lo_lo_act = _falling(prev_ll, pv,  self.lo_lo_lim)
        s.dv_hi_act = _rising(prev_dvh, err, self.dv_hi_lim)
        s.dv_lo_act = _falling(prev_dvl, err, self.dv_lo_lim)

        # Log alarm transitions
        _prev = [prev_hh, prev_h, prev_l, prev_ll, prev_dvh, prev_dvl]
        _now  = [s.hi_hi_act, s.hi_act, s.lo_act, s.lo_lo_act, s.dv_hi_act, s.dv_lo_act]
        _names = ["hi_hi_act", "hi_act", "lo_act", "lo_lo_act", "dv_hi_act", "dv_lo_act"]
        for name, was, now in zip(_names, _prev, _now):
            if now and not was:
                self._log.warning("ALARM ACTIVE  : %s  PV=%.3f  SP=%.3f", name, pv, self.SP)
            elif was and not now:
                self._log.info("ALARM CLEARED : %s  PV=%.3f", name, pv)

    # ------------------------------------------------------------------
    # Remote shed-on-timeout watchdog (Azeo SHED_OPT / SHED_TIME)
    # ------------------------------------------------------------------

    def feed_watchdog(self) -> None:
        """Reset the remote (RCAS/ROUT) shed timer.

        Call this on each *fresh* host write — e.g. when a DMC heartbeat ticks.
        While in RCAS/ROUT, if this is not called for longer than ``shed_time``
        the block sheds to a safe local mode per ``shed_opt``.
        """
        self._remote_age = 0.0

    def _shed_target(self, cur: Mode):
        """Map SHED_OPT to a shed target mode for the current remote mode."""
        if self.shed_opt == ShedOption.NO_ACTION:
            return None
        if self.shed_opt == ShedOption.MANUAL:
            return Mode.Man
        if self.shed_opt == ShedOption.AUTO:
            return Mode.Auto
        # NORMAL: shed to the next-lower local mode — RCas -> Auto (hold SP),
        # ROut -> Man (hold OP).
        return Mode.Auto if cur == Mode.RCas else Mode.Man

    def _update_remote_shed(self, dt: float) -> None:
        """Age the remote watchdog and shed the target mode on timeout."""
        if self._target_mode in (Mode.RCas, Mode.ROut):
            self._remote_age += dt
            if self.shed_time > 0.0 and self._remote_age > self.shed_time:
                shed_to = self._shed_target(self._target_mode)
                if shed_to is not None and shed_to != self._target_mode:
                    self._log.warning(
                        "Remote shed: %s host lost for %.1fs (> shed_time=%.1fs)"
                        " -> %s", self._target_mode.value, self._remote_age,
                        self.shed_time, shed_to.value)
                    self._target_mode = shed_to
                self._remote_age = 0.0
        else:
            self._remote_age = 0.0

    # ------------------------------------------------------------------
    # Main execute() method
    # ------------------------------------------------------------------

    def execute(self, dt: float = 1.0) -> None:
        """
        Execute one scan of the PID function block.

        :param dt: Elapsed time since last scan [seconds].
                   With PIDPlus, this is used only when a new measurement
                   is available; the actual elapsed interval is tracked.
        :raises ValueError: if dt ≤ 0.
        """
        if dt <= 0.0:
            raise ValueError(f"dt must be positive, got {dt}")

        self._log.debug("--- Execute scan  dt=%.3fs  mode=%s ---",
                        dt, self._actual_mode)

        # ---- Validate alpha ----
        self.alpha = max(0.05, min(1.0, self.alpha))

        # ---- Remote shed-on-timeout watchdog (Azeo SHED_OPT/SHED_TIME) ----
        self._update_remote_shed(dt)

        # ---- Resolve current mode ----
        self._resolve_mode()
        actual = self._actual_mode

        # ---- OOS: do nothing ----
        if actual == Mode.OOS:
            self._log.debug("Block is OOS – skipping execution.")
            return

        # ---- Get PV ----
        raw_in = self._get_raw_pv()

        if self._status_is_bad(raw_in):
            self.block_err.input_failure = True
            self._log.warning("PV source is Bad – holding last PV=%.3f", self.PV)
        else:
            self.block_err.input_failure = False
            pv_unfiltered = raw_in.value

            # PV filter
            self.PV = self._first_order_filter(
                pv_unfiltered, self._pv_filt, self.pv_ftime, dt
            )
            self._pv_filt = self.PV

        # ---- Setpoint selection ----
        if actual in (Mode.Cas, Mode.IMan):
            sp_raw = self.CAS_IN.value
        elif actual == Mode.RCas:
            sp_raw = self.RCAS_IN.value
        else:
            sp_raw = self.SP  # operator / remote SP

        # Optionally obey SP limits in Cas/RCas
        if actual in (Mode.Cas, Mode.RCas) and self.obey_sp_lim_cas_rcas:
            sp_raw = self._limit_sp(sp_raw)
        elif actual not in (Mode.Cas, Mode.RCas, Mode.IMan):
            sp_raw = self._limit_sp(sp_raw)

        # SP filter
        sp_filt = self._first_order_filter(sp_raw, self._sp_filt, self.sp_ftime, dt)
        self._sp_filt = sp_filt

        # Cascade/remote-cascade setpoint limits and slew are one option in
        # Azeo.  When the option is selected, applying only the clamp while
        # bypassing the configured rate made column adoption settings inert.
        if (actual == Mode.Auto or
                (actual in (Mode.Cas, Mode.RCas)
                 and self.obey_sp_lim_cas_rcas)):
            self.SP_WRK = self._rate_limit_sp(sp_filt, dt)
        else:
            self.SP_WRK = sp_filt

        if not self.sp_rate_keeps_target:
            # Legacy: the ramped value overwrites the target, so a setpoint
            # written once (set_sp / an operator entry) advances by a single
            # increment and the ramp then stalls — the next scan reads the
            # already-ramped SP as the target. Azeo keeps SP as the target
            # and publishes the ramp only in SP_WRK.
            self.SP = self.SP_WRK

        # ---- SP-PV tracking ----
        if (
            (actual in (Mode.LO, Mode.IMan) and self.sp_pv_track_lo_iman) or
            (actual == Mode.Man  and self.sp_pv_track_man) or
            (actual == Mode.ROut and self.sp_pv_track_rout)
        ):
            self.SP = self.PV
            self.SP_WRK = self.PV
            self._log.debug("SP-PV tracking active: SP set to PV=%.3f", self.PV)

        # ---- Error ----
        # Always against the *working* setpoint: identical to SP unless
        # sp_rate_keeps_target holds SP at the un-ramped operator target.
        self.ERROR = self.SP_WRK - self.PV
        self._log.debug("PV=%.4f  SP=%.4f  ERROR=%.4f", self.PV, self.SP_WRK, self.ERROR)

        # ---- PIDPlus: detect new measurement ----
        if self.use_pidplus:
            if self.measurement_updated is not None:
                # True exception reporting: a caller that actually knows when a
                # sample arrived says so. Nothing in the FBD runtime does —
                # a wire carries value + quality + limit, not a timestamp.
                self._new_measurement = bool(self.measurement_updated)
            elif raw_in.limit == LimitStatus.CONSTANT:
                # The source says the value cannot change: an honest "no new
                # measurement", as opposed to inferring it from an unchanged
                # number (which a genuinely steady process also produces).
                self._new_measurement = False
            else:
                # Fallback inference: PV value change (no wall-clock dependency)
                self._new_measurement = not math.isclose(
                    raw_in.value, self._prev_pv, rel_tol=1e-9, abs_tol=1e-9
                )
        else:
            self._new_measurement = True

        # ---- Calculate output ----
        if actual in (Mode.Auto, Mode.Cas, Mode.RCas):
            if self.bypass and self.bypass_enable:
                # BYPASS: pass SP directly to output (as percent of OUT_SCALE)
                sp_pct = self.pv_scale.to_percent(self.SP_WRK)
                out_raw = self.out_scale.from_percent(sp_pct)
                self._log.info("BYPASS active: OUT=%.3f (from SP=%.3f)",
                               out_raw, self.SP_WRK)
            else:
                out_raw = self._compute_pid_output(
                    self.ERROR, self.PV, self.SP_WRK, dt
                )
                # Feedforward addition
                out_raw += self._compute_feedforward()

                # Dynamic Reset Limit
                if self.dynamic_reset_limit:
                    out_raw = self._apply_dynamic_reset_limit(out_raw, dt)

                # PIDPlus saturation recovery
                out_raw = self._apply_saturation_recovery(out_raw, dt)

        elif actual == Mode.ROut:
            # Remote-Output: output commanded by a remote host (DMC) via ROUT_IN.
            # Shed-to-hold if the host value is Bad (e.g. comms dropout).
            if self.ROUT_IN is not None and not self.ROUT_IN.bad:
                out_raw = self.ROUT_IN.value
            else:
                out_raw = self.OUT.value
            # Freeze integrator so return to Auto is bumpless.
            self._seed_reset(out_raw)

        elif actual == Mode.Man:
            # Manual: output set by the operator (set_output_manual writes OUT).
            out_raw = self.OUT.value
            self._seed_reset(out_raw)

        elif actual in (Mode.LO,):
            # Local Override: tracking active
            out_raw = self.out_scale.from_percent(
                self.trk_scale.to_percent(self.TRK_VAL.value)
            )
            self._seed_reset(out_raw)

        elif actual == Mode.IMan:
            # Initializing manual: hold output, initialize integrator
            out_raw = self.OUT.value
            self._seed_reset(out_raw)
            # Check if slave (downstream) is now inviting CAS
            if self.BKCAL_IN.limit != LimitStatus.NOT_INVITED:
                self._log.info("CAS invitation received – transitioning IMan -> target mode.")
                self._resolve_mode()

        else:
            out_raw = self.OUT.value

        # ---- Output tracking (TRK_IN_D) ----
        if (
            self.track_enable and
            self.TRK_IN_D and
            (actual != Mode.Man or self.track_in_manual)
        ):
            out_raw = self.out_scale.from_percent(
                self.trk_scale.to_percent(self.TRK_VAL.value)
            )
            # Back-calculate reset from the tracked output so that releasing
            # the track command returns control without a bump. p_term is only
            # current when the algorithm actually ran this scan.
            gain_a = self._normalized_gain()
            if abs(gain_a) > 1e-12:
                p_now = (self.p_term
                         if actual in (Mode.Auto, Mode.Cas, Mode.RCas) else 0.0)
                self._integral = out_raw / gain_a - p_now
                self._reset_fb = self._integral * gain_a
            self._actual_mode = Mode.LO
            self.block_err.local_override = True
            self._log.info("Tracking active: OUT=%.3f from TRK_VAL=%.3f",
                           out_raw, self.TRK_VAL.value)
        else:
            self.block_err.local_override = (self._actual_mode == Mode.LO)

        # ---- Output limiting ----
        # Runtime limits can extend 10 % beyond scale (per spec)
        out_lo = max(
            self.out_lo_lim,
            self.out_scale.eu0 - 0.1 * self.out_scale.span
        )
        out_hi = min(
            self.out_hi_lim,
            self.out_scale.eu100 + 0.1 * self.out_scale.span
        )

        # No-OUT-limits-in-Manual option
        if actual == Mode.Man and self.no_out_limits_in_man:
            out_limited = out_raw
            limit_status = LimitStatus.NOT_LIMITED
        else:
            out_limited = max(out_lo, min(out_hi, out_raw))
            # Freeze reset while OUT is limited: undo this scan's integral
            # increment if it pushed further into the limit, leaving the
            # accumulated reset intact. Zeroing the integrator instead (the
            # previous behaviour) discarded the whole reset history, so the
            # output collapsed on the scan after PV recovered instead of
            # easing off the limit.
            _i_prev = self._integral_prev
            # The clamp used to be skipped whenever Dynamic Reset Limit was
            # selected, i.e. choosing Azeo's anti-windup option switched the
            # last windup protection off. DRL is now the external-reset network
            # in _compute_pid_output, which limits the reset by construction —
            # freezing here as well is consistent and never over-restricts.
            if out_raw > out_hi:
                limit_status = LimitStatus.HIGH_LIMITED
                if self._integral > _i_prev:
                    self._integral = _i_prev
                    self._reset_fb = self._reset_fb_prev
            elif out_raw < out_lo:
                limit_status = LimitStatus.LOW_LIMITED
                if self._integral < _i_prev:
                    self._integral = _i_prev
                    self._reset_fb = self._reset_fb_prev
            else:
                limit_status = LimitStatus.NOT_LIMITED

        # STATUS_OPTS "Bad if Limited" / "Uncertain if Limited": the quality of
        # OUT reflects that the block can no longer move its output. Both
        # default off, i.e. a limited output stays Good, which is what this
        # block always published.
        _limited = limit_status in (LimitStatus.HIGH_LIMITED,
                                    LimitStatus.LOW_LIMITED)
        self.OUT = SignalStatus(
            value=out_limited, limit=limit_status,
            bad=bool(_limited and self.bad_if_limited),
            uncertain=bool(_limited and self.uncertain_if_limited
                           and not self.bad_if_limited),
        )
        self._log.debug("OUT=%.4f  limit=%s", out_limited, limit_status)

        # ---- BKCAL_OUT ----
        self._update_bkcal_out()

        # ---- Alarm detection ----
        self._check_alarms()

        # ---- Simulation flag ----
        self.block_err.simulate_active = self.simulate_enabled

        # ---- Save state for next scan ----
        self._prev_pv = self.PV
        self._prev_sp = self.SP

    # ------------------------------------------------------------------
    # Operator helpers
    # ------------------------------------------------------------------

    def set_output_manual(self, value: float) -> None:
        """Set the output manually (valid in Man and ROut modes only)."""
        if self._actual_mode not in (Mode.Man, Mode.ROut):
            self._log.warning(
                "set_output_manual called in mode %s – ignored.", self._actual_mode
            )
            return
        clamped = self.out_scale.clamp(value)
        self.OUT = SignalStatus(value=clamped)
        self._log.info("Manual output set: %.4f", clamped)

    def set_setpoint(self, value: float) -> None:
        """Set operator setpoint (valid in Auto mode)."""
        if self._actual_mode != Mode.Auto:
            self._log.warning(
                "set_setpoint called in mode %s – ignored.", self._actual_mode
            )
            return
        self.SP = self._limit_sp(value)
        self._log.info("Setpoint set: %.4f (after limiting)", self.SP)

    def reset_block_to_manual(self) -> None:
        """Force block to Manual mode and latch it there (e.g. on alarm)."""
        self._target_mode = Mode.Man
        self._actual_mode = Mode.Man
        self._log.warning("Block '%s' forced to Manual.", self._name)

    def acknowledge_simulation(self, enabled: bool) -> None:
        """Enable or disable simulation mode."""
        self.simulate_enabled = enabled
        self.block_err.simulate_active = enabled
        self._log.info("Simulation %s.", "ENABLED" if enabled else "DISABLED")

    # ------------------------------------------------------------------
    # Status / diagnostics
    # ------------------------------------------------------------------

    def get_status_summary(self) -> dict:
        """Return a concise snapshot of block state for logging/HMI."""
        return {
            "name":        self._name,
            "actual_mode": self._actual_mode.value,
            "target_mode": self._target_mode.value,
            "PV":          round(self.PV, 4),
            "SP":          round(self.SP, 4),
            "ERROR":       round(self.ERROR, 4),
            "OUT":         round(self.OUT.value, 4),
            "OUT_limit":   self.OUT.limit.name,
            "BKCAL_OUT":   round(self.BKCAL_OUT.value, 4),
            "block_errors": self.block_err.summary(),
            "alarms": {
                "HI_HI":  self.alarm_state.hi_hi_act,
                "HI":     self.alarm_state.hi_act,
                "LO":     self.alarm_state.lo_act,
                "LO_LO":  self.alarm_state.lo_lo_act,
                "DV_HI":  self.alarm_state.dv_hi_act,
                "DV_LO":  self.alarm_state.dv_lo_act,
            },
        }

    def __repr__(self) -> str:
        return (
            f"PIDBlock(name={self._name!r}, "
            f"mode={self._actual_mode.value}, "
            f"PV={self.PV:.3f}, SP={self.SP:.3f}, OUT={self.OUT.value:.3f})"
        )

    # ------------------------------------------------------------------
    # Backward-compatibility layer (PIDBlockImpl API)
    # ------------------------------------------------------------------
    # Allows PIDBlock to be a near-drop-in replacement for the old
    # core.isa_pid.PIDBlockImpl class used by process_model.py,
    # sim_engine.py, and main_window.py.

    @property
    def pv(self) -> float:
        """Compat: read PV (same as self.PV)."""
        return self.PV

    @pv.setter
    def pv(self, value: float) -> None:
        """Compat: set PV — routes to self.IN signal."""
        self.IN = SignalStatus(value=value)

    @property
    def sp(self) -> float:
        """Compat: read SP (same as self.SP)."""
        return self.SP

    @sp.setter
    def sp(self, value: float) -> None:
        """Compat: set SP operator setpoint directly."""
        self.SP = value

    @property
    def op(self) -> float:
        """Compat: read output (same as self.OUT.value)."""
        return self.OUT.value

    @op.setter
    def op(self, value: float) -> None:
        """Compat: set output — routes through manual if in Man/ROut."""
        if self._actual_mode in (Mode.Man, Mode.ROut):
            self.set_output_manual(value)
        else:
            self.OUT = SignalStatus(value=value, limit=self.OUT.limit)

    @property
    def sp_wrk(self) -> float:
        """Compat: read working SP (same as self.SP_WRK)."""
        return self.SP_WRK

    @property
    def tag(self) -> str:
        """Compat: tag name (same as self.name)."""
        return self._name

    @property
    def description(self) -> str:
        """Compat: block description."""
        return getattr(self, '_description', self._name)

    @description.setter
    def description(self, value: str) -> None:
        self._description = value

    @property
    def pv_units(self) -> str:
        """Compat: PV engineering units label."""
        return getattr(self, '_pv_units', '')

    @pv_units.setter
    def pv_units(self, value: str) -> None:
        self._pv_units = value

    @property
    def out_units(self) -> str:
        """Compat: output engineering units label."""
        return getattr(self, '_out_units', '%')

    @out_units.setter
    def out_units(self, value: str) -> None:
        self._out_units = value

    def compute(self, dt_sec: float) -> None:
        """Compat: alias for execute()."""
        self.execute(dt_sec)

    def initialize(self, pv: float, output: float,
                   sp: float | None = None, first_dt: float = 0.0) -> None:
        """Bumpless initialization at steady state.

        Pre-conditions integrator so first compute() produces no bump.
        """
        if sp is None:
            sp = self.SP
        self.condition_for_output(
            pv=pv, sp=sp, output=output, first_dt=first_dt,
        )

    def condition_for_output(self, *, pv: float, sp: float,
                             output: float, first_dt: float = 0.0) -> None:
        """Back-calculate all dynamic states from a measured operating point.

        ``output / gain`` alone is not a bumpless reset value whenever the
        loop has proportional action: the next scan adds that contribution a
        second time.  Reset must instead hold the residual after proportional
        action and feedforward are removed.  Derivative memories are seated
        on the same PV/SP point so RATE cannot manufacture a startup kick.

        Both the standalone PID API and strategy bridge use this routine; a
        live loop must not initialise differently from the algorithm in a
        focused test.
        """
        pv = float(pv)
        sp = float(sp)
        output = float(output)

        self.IN = SignalStatus(value=pv)
        self.PV = pv
        self._pv_filt = pv
        self._prev_pv = pv
        self.SP = sp
        self.SP_WRK = sp
        self._sp_filt = sp
        self._prev_sp = sp
        self.OUT = SignalStatus(value=output)
        self.ERROR = sp - pv

        sign = -1.0 if self.direct_acting else 1.0
        calc_error = sign * self.ERROR
        calc_pv = sign * pv
        calc_sp = sign * sp
        beta, gamma = self._get_beta_gamma()
        if self.use_nonlinear_gain and self.nl_force_standard and beta is not None:
            beta = 1.0
        knl = self._compute_knl(calc_error)
        p_term = (0.0 if beta is None
                  else knl * (beta * calc_sp - calc_pv))

        # Feedforward is summed outside _compute_pid_output(), so remove it
        # before solving for the reset contribution.
        algorithm_output = output - self._compute_feedforward()
        gain_a = self._normalized_gain()
        integral_active = (
            self.structure not in (
                Structure.PD_ON_ERROR, Structure.P_ERROR_D_PV,
            )
            and self.reset > 0.0
        )
        target_integral = self._integral
        reset_fb = self._reset_fb
        if integral_active and abs(gain_a) > 1e-12:
            target_integral = algorithm_output / gain_a - p_term
            initial_integral = target_integral
            reset_fb = gain_a * target_integral

            # Reset advances before the core combines P+I+D.  Seat the state
            # one scheduled reset step behind its target so scan 1 lands on
            # the readback exactly; otherwise a large live error still makes
            # the supposedly bumpless first output move by dt/RESET.
            will_integrate = (
                first_dt > 0.0
                and abs(calc_error) > self.ideadband
                and (not self.use_pidplus
                     or self.measurement_updated is True)
            )
            if will_integrate:
                weight = self._reset_weight(first_dt, self.reset)
                if self.reset_impl == "positive_feedback":
                    source = self._reset_feedback_source()
                    if abs(1.0 - weight) > 1e-12:
                        reset_fb = (
                            gain_a * target_integral - weight * source
                        ) / (1.0 - weight)
                        initial_integral = reset_fb / gain_a
                elif self._external_reset_active():
                    feedback = (
                        self.BKCAL_IN.value - self._compute_feedforward()
                    ) / gain_a
                    if abs(1.0 - weight) > 1e-12:
                        initial_integral = (
                            target_integral - weight * feedback
                        ) / (1.0 - weight)
                        reset_fb = gain_a * initial_integral
                else:
                    initial_integral -= knl * calc_error * weight
                    reset_fb = gain_a * initial_integral
            self._integral = initial_integral
        elif not integral_active:
            # In P/PD structures BIAS is the manual-reset operating point and
            # is the only state available to adopt a live downstream output.
            self._integral = 0.0
            self.bias = algorithm_output - gain_a * p_term

        self._integral_prev = self._integral
        self._reset_fb = reset_fb
        self._reset_fb_prev = self._reset_fb

        if self.structure in (
            Structure.PID_ON_ERROR, Structure.ID_ON_ERROR,
            Structure.PD_ON_ERROR,
        ):
            derivative_signal = (
                gamma * calc_sp - calc_pv if gamma else -calc_pv
            )
        elif self.structure == Structure.TWO_DOF:
            derivative_signal = gamma * calc_sp - calc_pv
        else:
            derivative_signal = -calc_pv
        self._deriv_filter = derivative_signal
        self._deriv_state = 0.0
        # Azeo-series derivative acts on the PI section.  It sees the
        # post-reset state during scan 1, not the pre-compensated accumulator.
        self._series_prev = p_term + target_integral
        self._series_dstate = 0.0
        self._pending_dt = 0.0
        self.p_term = p_term
        self.i_term = self._integral if integral_active else 0.0
        self.d_term = 0.0

    def reset_block(self) -> None:
        """Reset block to initial (OOS) state."""
        self._target_mode = Mode.OOS
        self._actual_mode = Mode.OOS
        self.block_err = BlockError(out_of_service=True)
        self.alarm_state = AlarmState()
        self._integral = 0.0
        self._reset_fb = 0.0
        self._reset_fb_prev = 0.0
        self._deriv_filter = 0.0
        self._deriv_state = 0.0
        self._series_prev = None
        self._series_dstate = 0.0
        self._pending_dt = 0.0
        self._bad_pv_latched = False
        self._saturated_time = 0.0
        self._prev_pv = 0.0
        self._prev_sp = 0.0
        self._pv_filt = 0.0
        self._sp_filt = 0.0

    def set_mode(self, mode: Mode) -> None:
        """Compat: alias for set_target_mode()."""
        self.set_target_mode(mode)

    # Old-style alarm limit aliases (hihi_lim ↔ hi_hi_lim, lolo_lim ↔ lo_lo_lim)
    @property
    def hihi_lim(self) -> float:
        return self.hi_hi_lim

    @hihi_lim.setter
    def hihi_lim(self, v: float) -> None:
        self.hi_hi_lim = v

    @property
    def lolo_lim(self) -> float:
        return self.lo_lo_lim

    @lolo_lim.setter
    def lolo_lim(self, v: float) -> None:
        self.lo_lo_lim = v

    @property
    def pv_scale_lo(self) -> float:
        return self.pv_scale.eu0

    @pv_scale_lo.setter
    def pv_scale_lo(self, v: float) -> None:
        self.pv_scale = ScaleRange(v, self.pv_scale.eu100)

    @property
    def pv_scale_hi(self) -> float:
        return self.pv_scale.eu100

    @pv_scale_hi.setter
    def pv_scale_hi(self, v: float) -> None:
        self.pv_scale = ScaleRange(self.pv_scale.eu0, v)

    @property
    def out_scale_lo(self) -> float:
        return self.out_scale.eu0

    @out_scale_lo.setter
    def out_scale_lo(self, v: float) -> None:
        self.out_scale = ScaleRange(v, self.out_scale.eu100)

    @property
    def out_scale_hi(self) -> float:
        return self.out_scale.eu100

    @out_scale_hi.setter
    def out_scale_hi(self, v: float) -> None:
        self.out_scale = ScaleRange(self.out_scale.eu0, v)


# Backward compatibility aliases
PidMode = Mode


# ===========================================================================
# Module-level validation / edge case checks
# ===========================================================================

def validate_pid_config(pid: PIDBlock) -> list[str]:
    """
    Run configuration sanity checks and return a list of warning strings.
    Modelled on PID configuration validation rules.
    """
    warnings: list[str] = []

    # Scale checks
    if math.isclose(pid.pv_scale.span, 0.0):
        warnings.append("PV_SCALE span is zero – PID cannot function correctly.")
    if math.isclose(pid.out_scale.span, 0.0):
        warnings.append("OUT_SCALE span is zero – gain normalization will fail.")

    # Limit consistency
    if pid.out_hi_lim <= pid.out_lo_lim:
        warnings.append(
            f"OUT_HI_LIM ({pid.out_hi_lim}) <= OUT_LO_LIM ({pid.out_lo_lim})."
        )
    if pid.sp_hi_lim <= pid.sp_lo_lim:
        warnings.append(
            f"SP_HI_LIM ({pid.sp_hi_lim}) <= SP_LO_LIM ({pid.sp_lo_lim})."
        )
    if pid.arw_hi_lim <= pid.arw_lo_lim:
        warnings.append(
            f"ARW_HI_LIM ({pid.arw_hi_lim}) <= ARW_LO_LIM ({pid.arw_lo_lim})."
        )

    # ARW limits should be within OUT limits
    if pid.arw_hi_lim > pid.out_hi_lim:
        warnings.append("ARW_HI_LIM exceeds OUT_HI_LIM – ARW will never activate on high side.")
    if pid.arw_lo_lim < pid.out_lo_lim:
        warnings.append("ARW_LO_LIM is below OUT_LO_LIM – ARW will never activate on low side.")

    # Tuning checks
    if pid.gain < 0.0:
        warnings.append(f"GAIN ({pid.gain}) is negative – did you mean reverse-acting?")
    if pid.reset < 0.0:
        warnings.append(f"RESET ({pid.reset}) is negative – integral disabled or invalid.")
    if pid.rate < 0.0:
        warnings.append(f"RATE ({pid.rate}) is negative – derivative disabled or invalid.")
    if not (0.05 <= pid.alpha <= 1.0):
        warnings.append(
            f"ALPHA ({pid.alpha}) outside [0.05, 1.0] – derivative filter may be unstable."
        )

    # PIDPlus checks
    if pid.use_pidplus and not (0.0 <= pid.recovery_fltr <= 1.0):
        warnings.append(
            f"RECOVERY_FLTR ({pid.recovery_fltr}) must be in [0.0, 1.0]."
        )

    # Nonlinear checks
    if pid.use_nonlinear_gain:
        if pid.nl_tband < 0.0:
            warnings.append("NL_TBAND must be >= 0.")
        if not (0.0 <= pid.nl_minmod <= 1.0):
            warnings.append(f"NL_MINMOD ({pid.nl_minmod}) must be in [0.0, 1.0].")
        if pid.nl_gap == 0.0 and pid.nl_hyst != 0.0:
            warnings.append("NL_GAP is 0 so NL_HYST has no effect (per spec).")

    # Two-DoF beta/gamma range
    if pid.structure == Structure.TWO_DOF:
        if not (0.0 <= pid.beta <= 1.0):
            warnings.append(f"BETA ({pid.beta}) must be in [0.0, 1.0] for Two-DoF.")
        if not (0.0 <= pid.gamma <= 1.0):
            warnings.append(f"GAMMA ({pid.gamma}) must be in [0.0, 1.0] for Two-DoF.")

    # Cascade BKCAL wiring
    if pid.target_mode == Mode.Cas and (
        pid.BKCAL_IN.limit == LimitStatus.NOT_CONNECTED
    ):
        warnings.append(
            "Block targets Cas mode but BKCAL_IN appears unconnected – "
            "slave will not reach CAS and master PID will never be active."
        )

    # SP rate limiting
    if pid.sp_rate_up < 0.0:
        warnings.append("SP_RATE_UP must be >= 0 (0 = immediate).")
    if pid.sp_rate_dn < 0.0:
        warnings.append("SP_RATE_DN must be >= 0 (0 = immediate).")

    # Alarm limit ordering
    if pid.hihi_lim < pid.hi_lim:
        warnings.append("HI_HI_LIM < HI_LIM – alarm priority order incorrect.")
    if pid.lolo_lim > pid.lo_lim:
        warnings.append("LO_LO_LIM > LO_LIM – alarm priority order incorrect.")
    if pid.dv_lo_lim >= 0.0:
        warnings.append("DV_LO_LIM should be negative (it is compared against PV-SP).")

    for w in warnings:
        logger.warning("[CONFIG-CHECK] %s: %s", pid.name, w)

    return warnings


# ===========================================================================
# Quick demonstration
# ===========================================================================

def _demo() -> None:
    """
    Brief simulation: temperature controller with cascade initialization,
    feedforward, and nonlinear gain – illustrates the block in action.
    """
    print("\n" + "=" * 70)
    print("PID Block – Demo Simulation")
    print("=" * 70)

    # ---- Master temperature controller ----
    master = PIDBlock(name="TIC-101-MASTER")
    master.pv_scale  = ScaleRange(0.0, 300.0)   # 0–300 °C
    master.out_scale = ScaleRange(0.0, 100.0)    # 0–100 % flow SP
    master.gain  = 1.5
    master.reset = 120.0  # 2 min
    master.rate  = 0.0
    master.sp_hi_lim = 250.0
    master.sp_lo_lim = 50.0
    master.hi_lim    = 220.0
    master.hi_hi_lim = 240.0
    master.lo_lim    = 80.0
    master.lo_lo_lim = 60.0
    master.dv_hi_lim =  15.0
    master.dv_lo_lim = -15.0
    master.alarm_hys = 2.0
    master.structure = Structure.PI_ERROR_D_PV
    master.dynamic_reset_limit = True  # DRL enabled for cascade master

    # ---- Slave flow controller ----
    slave = PIDBlock(name="FIC-101-SLAVE")
    slave.pv_scale  = ScaleRange(0.0, 100.0)
    slave.out_scale = ScaleRange(0.0, 100.0)
    slave.gain  = 0.8
    slave.reset = 20.0
    slave.rate  = 0.0
    slave.structure = Structure.PI_ERROR_D_PV

    # ---- Wire cascade: master.OUT -> slave.CAS_IN ----
    # Wire BKCAL: slave.BKCAL_OUT -> master.BKCAL_IN
    # (in a real PID module these are wired block connectors)

    # ---- Commission: bring slave to CAS first, then master to Auto ----
    slave.BKCAL_IN = SignalStatus(value=50.0, limit=LimitStatus.NOT_LIMITED)
    slave.set_target_mode(Mode.Cas)
    slave.execute(dt=1.0)
    # Slave is now in CAS; its BKCAL_OUT can invite the master
    # Simulate slave sending back "GoodCascade/NotLimited"
    master.BKCAL_IN = SignalStatus(value=slave.BKCAL_OUT.value, limit=LimitStatus.NOT_LIMITED)
    master.set_target_mode(Mode.Auto)
    master.SP = 180.0   # 180 °C setpoint

    print(f"\nInitial state:")
    print(f"  Master: {master}")
    print(f"  Slave : {slave}")

    # ---- Validate configuration ----
    print("\n--- Configuration Validation ---")
    w = validate_pid_config(master)
    if not w:
        print(f"  {master.name}: No configuration warnings.")

    # ---- Step response simulation ----
    print("\n--- Step Response (30 scans @ 1 s) ---")
    print(f"{'Scan':>5}  {'Temp(PV)':>9}  {'SP':>7}  {'OUT':>7}  "
          f"{'FlowPV':>7}  {'Mode':>12}  {'Alarms'}")
    print("-" * 75)

    pv_temp  = 150.0   # initial temperature
    pv_flow  = 40.0    # initial flow
    process_gain_temp  = 0.8   # temperature response to flow (°C per %)
    process_gain_flow  = 0.5   # flow response to valve (% per %)
    process_tau   = 20.0       # temperature time constant [s]
    pv_temp_filt  = pv_temp

    for k in range(30):
        # Slave execution
        slave.IN = SignalStatus(value=pv_flow)
        slave.CAS_IN = SignalStatus(value=master.OUT.value)
        slave.execute(dt=1.0)

        # Master execution
        master.IN = SignalStatus(value=pv_temp)
        master.execute(dt=1.0)

        # Propagate BKCAL
        master.BKCAL_IN = SignalStatus(
            value=slave.BKCAL_OUT.value,
            limit=slave.BKCAL_OUT.limit
        )

        # Simple first-order process models
        pv_flow  += (slave.OUT.value * process_gain_flow - pv_flow) * 0.1
        pv_temp  += (pv_flow * process_gain_temp - pv_temp) / process_tau

        alarms = [k for k, v in master.alarm_state.__dict__.items() if v]
        alarm_str = ",".join(alarms) if alarms else "—"

        print(
            f"{k+1:>5}  {pv_temp:>9.2f}  {master.SP:>7.2f}  {master.OUT.value:>7.2f}"
            f"  {pv_flow:>7.2f}  {master.actual_mode.value:>12}  {alarm_str}"
        )

    print("\nFinal status summary:")
    import json
    print(json.dumps(master.get_status_summary(), indent=2))


if __name__ == "__main__":
    _demo()
