"""PID function block — wraps the shared core PID algorithm directly.

Uses the full ISA-style PIDBlock from azeo_control_trainer.core.pid.core,
exposing all 8 modes, nonlinear gain, feedforward, BKCAL cascade init,
alarm detection, 2-DOF, and ARW.  The underlying PIDBlock instance is
directly accessible via the ``pid_core_block`` property so pid_widget
faceplates can call ``update_from_block()`` on it.
"""
from __future__ import annotations

import logging
import re

from ..model.block_base import FunctionBlock, BlockCategory, DataType
from ..model.terminal import Quality, LimitStatus as TermLimit
from ..model.block_registry import register_block


from ..model.modes import str_to_mode, mode_to_str

_log = logging.getLogger("strategy.block.PID")


@register_block
class PIDBlock(FunctionBlock):
    """Advanced ISA-style PID function block.

    Directly wraps ``azeo_control_trainer.core.pid.core.PIDBlock`` — the full
    PID with all 8 modes, cascade BKCAL, nonlinear gain, PIDPlus,
    feedforward, alarms, 2-DOF, and anti-reset windup.

    The ``pid_core_block`` property gives pid_widget faceplates direct
    access to the underlying PIDBlock for ``update_from_block()`` calls.
    """
    block_type = "PID"
    # Legacy tuning spellings; _ensure_block() already falls back to these,
    # declaring them keeps the canonical Azeo names in the saved config.
    # The upper-case entries are the Azeo parameter names for keys this
    # block spells differently — accepted on load and rewritten to the
    # existing key, which always wins if both are present. Nothing is renamed:
    # every shipped strategy JSON keeps working unchanged.
    config_aliases = {
        "Kp": "GAIN", "Ti": "RESET", "Td": "RATE",
        "ALPHA": "alpha", "BETA": "beta", "GAMMA": "gamma", "BIAS": "bias",
        "IDEADBAND": "ideadband", "FORM": "form", "STRUCTURE": "structure",
        "MODE": "mode",
        "PV_FTIME": "pv_ftime", "SP_FTIME": "sp_ftime",
        "SP_RATE_UP": "sp_rate_up", "SP_RATE_DN": "sp_rate_dn",
        "SP_HI_LIM": "sp_hi", "SP_LO_LIM": "sp_lo",
        "OUT_HI_LIM": "out_hi", "OUT_LO_LIM": "out_lo",
        "ARW_HI_LIM": "arw_hi", "ARW_LO_LIM": "arw_lo",
        "FF_ENABLE": "ff_enable", "FF_GAIN": "ff_gain",
        "HI_HI_LIM": "hi_hi_lim", "HI_LIM": "hi_lim",
        "LO_LIM": "lo_lim", "LO_LO_LIM": "lo_lo_lim",
        "DV_HI_LIM": "dv_hi_lim", "DV_LO_LIM": "dv_lo_lim",
        "ALARM_HYS": "alarm_hys",
        "NL_GAP": "nl_gap", "NL_HYST": "nl_hyst",
        "NL_TBAND": "nl_tband", "NL_MINMOD": "nl_minmod",
        "RECOVERY_FLTR": "recovery_fltr",
        "SHED_OPT": "shed_opt", "SHED_TIME": "shed_time",
    }
    category = BlockCategory.CONTROL
    display_name = "PID Controller"
    description = "Advanced PID — all 8 modes, cascade BKCAL, ARW, 2-DOF"

    # Enumerated parameters — the properties panel renders these as drop-downs
    # so an engineer picks a valid value instead of typing one that silently
    # falls back to the default.
    config_choices = {
        "action": ("reverse", "direct"),
        "form": ("standard", "series"),
        "structure": ("two_dof", "pid_on_error", "pi_error_d_pv",
                      "i_error_pd_pv", "pd_on_error", "p_error_d_pv",
                      "id_on_error", "i_error_d_pv"),
        "arw_scale": ("auto", "absolute", "fraction"),
        "track_mode": ("azeo", "legacy"),
        "reset_impl": ("positional", "external", "positive_feedback"),
        "deriv_filter_mode": ("legacy", "azeo"),
        "series_impl": ("legacy", "azeo"),
        "dv_alarm_basis": ("error", "azeo"),
        "alarm_hys_units": ("eu", "percent"),
        "shed_opt": ("normal", "auto", "manual", "no_action"),
    }

    def __init__(self, instance_name: str = ""):
        self._pid_core: object | None = None   # pid_algo.PIDBlock (lazy)
        self._track_mode: str = "azeo"       # set from config in _ensure_block
        self._pv_filter_enable: bool = False
        self._pv_filt_primed: bool = False
        self._local_sp_written = False
        super().__init__(instance_name)

    def _define_terminals(self):
        # Inputs
        self.add_input("IN", description="Process Variable (PV)")
        self.add_input("SP", description="Setpoint")
        self.add_input("CAS_IN", description="Cascade setpoint input (from upstream PID)")
        self.add_input("RCAS_IN", description="Remote Cascade SP input (from DMC/MPC)")
        self.add_input("ROUT_IN", description="Remote Output input (from DMC/MPC, used in ROUT)")
        self.add_input("BKCAL_IN", description="Back-calculation from downstream", is_bkcal=True)
        self.add_input("FF_VAL", description="Feed-forward value")
        self.add_input("TRK_VAL", description="Track value")
        self.add_input("TRK_IN_D", DataType.BOOL, False, "Track enable")
        self.add_input("SIMULATE_IN", description="Simulation input value")
        self.add_input("MODE_TARGET", DataType.STRING, "", "Remote mode target (AUTO/MAN/RCAS/ROUT)")
        # Optional status companions for the value-only FBD wires. Unwired
        # they are False, which is exactly today's behaviour (Good, not
        # limited); wired they restore the Azeo status handling the bare
        # float wire cannot carry.
        self.add_input("BKCAL_HI_LIM", DataType.BOOL, False,
                       "Downstream block is high-limited (stops reset winding up)")
        self.add_input("BKCAL_LO_LIM", DataType.BOOL, False,
                       "Downstream block is low-limited (stops reset winding down)")
        self.add_input("IN_BAD", DataType.BOOL, False, "PV status is Bad")
        self.add_input("CAS_IN_BAD", DataType.BOOL, False, "Cascade setpoint status is Bad")
        self.add_input("BYPASS", DataType.BOOL, False,
                       "Engage BYPASS (needs bypass_enable): SP passes to OUT")

        # Outputs
        self.add_output("OUT", description="Controller output")
        self.add_output("BKCAL_OUT", description="Back-calculation to upstream", is_bkcal=True)
        self.add_output("PV", description="PV echo")
        self.add_output("SP", description="Active setpoint echo")
        self.add_output(
            "SP_WRK",
            description="Working setpoint after filtering and rate limits")
        self.add_output("MODE", DataType.STRING, "AUTO", "Current mode")
        self.add_output("AWS", DataType.BOOL, False, "Available for Write Status (Auto/Cas/RCas)")
        self.add_output("P_TERM", description="Proportional term")
        self.add_output("I_TERM", description="Integral term")
        self.add_output("D_TERM", description="Derivative term")
        # Back-calculation to a remote host (Azeo RCAS_OUT / ROUT_OUT), so an
        # MPC can initialise its RCAS_IN / ROUT_IN bumplessly instead of a
        # script writing the current setpoint back into the block.
        self.add_output("RCAS_OUT", description="Working SP back to the remote host (RCas init)")
        self.add_output("ROUT_OUT", description="Output back to the remote host (ROut init)")
        self.add_output("NL_GAIN_MOD", description="Nonlinear gain modifier in force (KNL)")

    # ------------------------------------------------------------ enum helpers
    @staticmethod
    def _normalize_enum(value) -> str:
        """Normalise a configured enum string: case, spaces and punctuation.

        ``"PI Action on Error, D Action on PV"``, ``"pi-error-d-pv"`` and
        ``"PI_ERROR_D_PV"`` all resolve to the same key, so a Azeo parameter
        value pasted from the manual works as well as our snake_case spelling.
        """
        return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_",
                                         str(value).strip().lower())).strip("_")

    @staticmethod
    def _structure_map(Structure) -> dict:
        """STRUCTURE strings → Structure enum (canonical + Azeo spellings).

        Unmapped values still fall back to Two-DoF, but now log a warning —
        previously a typo, or any of the shorthand spellings live strategies
        actually use, was silently reinterpreted.
        """
        return {
            # canonical (as written by this designer)
            "pid_on_error": Structure.PID_ON_ERROR,
            "pi_error_d_pv": Structure.PI_ERROR_D_PV,
            "i_error_pd_pv": Structure.I_ERROR_PD_PV,
            "pd_on_error": Structure.PD_ON_ERROR,
            "p_error_d_pv": Structure.P_ERROR_D_PV,
            "id_on_error": Structure.ID_ON_ERROR,
            "i_error_d_pv": Structure.I_ERROR_D_PV,
            "two_dof": Structure.TWO_DOF,
            # Azeo named-set values, normalised
            "pid_action_on_error": Structure.PID_ON_ERROR,
            "pi_action_on_error_d_action_on_pv": Structure.PI_ERROR_D_PV,
            "i_action_on_error_pd_action_on_pv": Structure.I_ERROR_PD_PV,
            "pd_action_on_error": Structure.PD_ON_ERROR,
            "p_action_on_error_d_action_on_pv": Structure.P_ERROR_D_PV,
            "id_action_on_error": Structure.ID_ON_ERROR,
            "i_action_on_error_d_action_on_pv": Structure.I_ERROR_D_PV,
            "two_degrees_of_freedom": Structure.TWO_DOF,
            # shorthand spellings used by shipped strategies
            "pi": Structure.PI_ERROR_D_PV,     # PI on error, D on PV
            "pid": Structure.PID_ON_ERROR,
            "pd": Structure.PD_ON_ERROR,
            "id": Structure.ID_ON_ERROR,
            # "p" is deliberately Two-DoF, not P_ERROR_D_PV: the blocks that
            # use it (FA level loops) set RESET = 9999 rather than disabling
            # the integral, and their operating point lives in the initialised
            # reset — switching to the integral-free structure would drop the
            # output to the proportional term alone. Configure "p_error_d_pv"
            # explicitly for a genuine integral-free P controller.
            "p": Structure.TWO_DOF,
        }

    # ---------------------------------------------------------------- lazy init
    def _ensure_block(self):
        if self._pid_core is not None:
            return
        from azeo_control_trainer.core.pid.core import (
            PIDBlock as CorePID, Mode, ScaleRange,
            SignalStatus, Structure, PIDForm,
        )
        p = self.config.params
        blk = CorePID(name=self.instance_name)

        # Scales
        blk.pv_scale = ScaleRange(
            p.get("pv_scale_lo", 0.0), p.get("pv_scale_hi", 100.0))
        # PID always outputs 0-100% (Azeo / ISA convention).
        # The JSON out_hi/out_lo fields are ignored for the output scale —
        # the PID output is always 0-100% and can connect directly to an AO.
        blk.out_scale = ScaleRange(0.0, 100.0)

        # Tuning
        blk.gain = p.get("GAIN", p.get("Kp", 1.0))
        blk.reset = p.get("RESET", p.get("Ti", 60.0))
        blk.rate = p.get("RATE", p.get("Td", 0.0))
        blk.alpha = p.get("alpha", 0.1)
        blk.bias = p.get("bias", 0.0)
        blk.ideadband = p.get("ideadband", 0.0)

        # 2-DOF
        blk.beta = p.get("beta", 1.0)
        blk.gamma = p.get("gamma", 0.0)

        # Filtering
        blk.pv_ftime = p.get("pv_ftime", 0.0)
        blk.sp_ftime = p.get("sp_ftime", 0.0)

        # Action
        blk.direct_acting = (p.get("action", "reverse") == "direct")

        # Remote shed-on-timeout (Azeo SHED_OPT / SHED_TIME). shed_time=0
        # disables the watchdog (default). Fed by a DMC heartbeat at the bridge.
        from azeo_control_trainer.core.pid.core import ShedOption
        blk.shed_time = float(p.get("shed_time", 0.0))
        _SHED_MAP = {"no_action": ShedOption.NO_ACTION, "normal": ShedOption.NORMAL,
                     "manual": ShedOption.MANUAL, "auto": ShedOption.AUTO}
        blk.shed_opt = _SHED_MAP.get(str(p.get("shed_opt", "normal")).lower(),
                                     ShedOption.NORMAL)

        # Structure / form
        _STRUCTURE_MAP = self._structure_map(Structure)
        _struct_name = self._normalize_enum(p.get("structure", "two_dof"))
        blk.structure = _STRUCTURE_MAP.get(_struct_name, Structure.TWO_DOF)
        if _struct_name not in _STRUCTURE_MAP:
            _log.warning(
                "PID '%s': unknown structure %r – falling back to Two Degrees "
                "of Freedom (beta=%.3g, gamma=%.3g). Valid values: %s",
                self.instance_name, p.get("structure"),
                blk.beta, blk.gamma, ", ".join(sorted(_STRUCTURE_MAP)))
        _form_name = self._normalize_enum(p.get("form", "standard"))
        _FORM_MAP = {
            "standard": PIDForm.STANDARD,
            "series": PIDForm.SERIES,
            "interacting": PIDForm.SERIES,
            "ideal": PIDForm.STANDARD,
        }
        blk.form = _FORM_MAP.get(_form_name, PIDForm.STANDARD)
        if _form_name not in _FORM_MAP:
            _log.warning("PID '%s': unknown form %r – using standard.",
                         self.instance_name, p.get("form"))

        # Output limits in 0-100% scale.
        # Azeo clamps OUT to OUT_HI_LIM/OUT_LO_LIM; here the PID output is
        # always the full 0-100 % span unless a strategy opts in with
        # use_out_limits, because the shipped JSONs carry out_lo/out_hi values
        # that describe the *downstream* range, not a controller clamp.
        blk.out_hi_lim = 100.0
        blk.out_lo_lim = 0.0
        if p.get("use_out_limits", False):
            blk.out_hi_lim = float(p.get("out_hi", 100.0))
            blk.out_lo_lim = float(p.get("out_lo", 0.0))

        arw_hi = float(p.get("arw_hi", 105.0))
        arw_lo = float(p.get("arw_lo", -5.0))
        arw_scale = str(p.get("arw_scale", "auto")).lower()
        if arw_scale == "fraction":
            arw_hi, arw_lo = arw_hi * 100.0, arw_lo * 100.0
        elif arw_scale != "absolute":
            # "auto" (default): historical heuristic — a high limit at or below
            # 1.1 is read as a 0-1 fraction of span, a small negative low limit
            # likewise. It cannot tell 1.25 (meant as 125 %) from 1.25 %.
            if arw_hi <= 1.1:
                arw_hi *= 100.0
            if -0.1 <= arw_lo < 0:
                arw_lo *= 100.0
            if arw_hi <= 10.0:
                _log.warning(
                    "PID '%s': ARW high limit resolves to %.3g %% of a 0-100 %% "
                    "output — reset will freeze almost immediately. Set "
                    "arw_scale to 'fraction' or 'absolute' to state the intent.",
                    self.instance_name, arw_hi)
        blk.arw_hi_lim = arw_hi
        blk.arw_lo_lim = arw_lo

        # SP limits
        blk.sp_hi_lim = p.get("sp_hi", 100.0)
        blk.sp_lo_lim = p.get("sp_lo", 0.0)

        # SP rate limiting
        blk.sp_rate_up = p.get("sp_rate_up", 0.0)
        blk.sp_rate_dn = p.get("sp_rate_dn", 0.0)

        # Feedforward — FF_VAL is converted FF_SCALE → OUT_SCALE before
        # FF_GAIN is applied. The 0-100 default is the identity conversion
        # this block did implicitly before FF_SCALE was configurable.
        blk.ff_enable = p.get("ff_enable", False)
        blk.ff_gain = p.get("ff_gain", 1.0)
        blk.ff_scale = ScaleRange(
            float(p.get("ff_scale_lo", 0.0)), float(p.get("ff_scale_hi", 100.0)))
        # TRK_SCALE → OUT_SCALE conversion for the track value (identity by
        # default, again matching the raw copy done before).
        blk.trk_scale = ScaleRange(
            float(p.get("trk_scale_lo", 0.0)), float(p.get("trk_scale_hi", 100.0)))

        # Nonlinear gain
        blk.use_nonlinear_gain = p.get("nl_enable", False)
        blk.nl_gap = p.get("nl_gap", 0.0)
        blk.nl_hyst = p.get("nl_hyst", 0.0)
        blk.nl_tband = p.get("nl_tband", 0.0)
        blk.nl_minmod = p.get("nl_minmod", 1.0)

        # Alarm limits
        blk.hi_hi_lim = p.get("hi_hi_lim", float('inf'))
        blk.hi_lim = p.get("hi_lim", float('inf'))
        blk.lo_lim = p.get("lo_lim", float('-inf'))
        blk.lo_lo_lim = p.get("lo_lo_lim", float('-inf'))
        blk.dv_hi_lim = p.get("dv_hi_lim", float('inf'))
        blk.dv_lo_lim = p.get("dv_lo_lim", float('-inf'))
        blk.alarm_hys = p.get("alarm_hys", 0.0)

        # FRSIPID options
        blk.use_pidplus = p.get("use_pidplus", False)
        blk.dynamic_reset_limit = p.get("dynamic_reset_limit", False)
        blk.use_delayed_out_bad_pv = p.get("use_delayed_out_bad_pv", False)
        blk.recovery_fltr = p.get("recovery_fltr", 1.0)

        # Control options
        blk.sp_pv_track_man = p.get("sp_pv_track_man", False)
        blk.sp_pv_track_lo_iman = p.get("sp_pv_track_lo_iman", False)
        blk.sp_pv_track_rout = p.get("sp_pv_track_rout", False)
        blk.use_pv_for_bkcal_out = p.get("use_pv_for_bkcal_out", False)
        blk.no_out_limits_in_man = p.get("no_out_limits_in_man", False)
        blk.obey_sp_lim_cas_rcas = p.get("obey_sp_lim_cas_rcas", False)
        blk.track_in_manual = p.get("track_in_manual", True)
        blk.bypass_enable = p.get("bypass_enable", False)
        blk.bypass = p.get("bypass", False)
        blk.simulate_enabled = p.get("simulate_enabled", False)

        # ---- STATUS_OPTS ----
        # All four default off, i.e. a limited output stays Good and an
        # Uncertain input counts as unusable — Azeo's own defaults, and
        # identical to what this block published before terminals carried
        # quality (nothing could produce an Uncertain status then).
        blk.target_manual_if_bad_in = p.get("target_manual_if_bad_in", False)
        blk.bad_if_limited = p.get("bad_if_limited", False)
        blk.uncertain_if_limited = p.get("uncertain_if_limited", False)
        blk.use_uncertain_as_good = p.get("use_uncertain_as_good", False)

        # ---- Tracking (TRK_IN_D / TRK_VAL) ----
        # "azeo": the core's Local-Override path runs — the block tracks in
        # LO, keeps executing (PV filter, alarms, BKCAL_OUT stay live) and
        # returns to its target mode when the track command clears.
        # "legacy": the wrapper forces the target mode to Man and writes OUT
        # directly, so the loop stayed in Man forever after tracking released.
        self._track_mode = str(p.get("track_mode", "azeo")).lower()
        # Azeo gates tracking on CONTROL_OPTS "Track Enable". Historically
        # this block tracked whenever TRK_IN_D was wired, so an unset key keeps
        # that: only an explicit track_enable=false disables tracking.
        blk.track_enable = bool(p.get("track_enable", True))

        # ---- Algorithm options (see pid_block_core for the semantics) ----
        blk.reset_impl = str(p.get("reset_impl", "positional")).lower()
        blk.deriv_filter_mode = str(p.get("deriv_filter_mode", "legacy")).lower()
        blk.series_impl = str(p.get("series_impl", "legacy")).lower()
        blk.dv_alarm_basis = str(p.get("dv_alarm_basis", "error")).lower()
        blk.alarm_hys_units = str(p.get("alarm_hys_units", "eu")).lower()
        blk.sp_rate_keeps_target = p.get("sp_rate_keeps_target", False)
        blk.sp_limit_restriction = p.get("sp_limit_restriction", False)
        blk.pidplus_dt_accumulate = p.get("pidplus_dt_accumulate", False)
        blk.pidplus_improved_integral = p.get("pidplus_improved_integral", False)
        blk.nl_force_standard = p.get("nl_force_standard", False)

        # PV_FTIME is only honoured when the filter is enabled: execute()
        # otherwise re-seeds the filter state with the raw PV every scan
        # (which is what made pv_ftime inert), and several engines already
        # apply their own measurement lag.
        self._pv_filter_enable = bool(p.get("pv_filter_enable", False))

        # Initialize state
        sp_init = p.get("sp_init", 50.0)
        blk.SP = sp_init
        blk.SP_WRK = sp_init
        blk._sp_filt = sp_init
        blk._prev_sp = sp_init
        blk.PV = sp_init
        blk._pv_filt = sp_init
        blk._prev_pv = sp_init
        blk.IN = SignalStatus(value=sp_init)
        # Supervisory selectors and cascade masters cannot all start at the
        # midpoint: a low selector must be parked high, a high selector low,
        # and a master must reproduce its slave's as-found PV. ``out_init``
        # makes that commissioning state engineering data instead of a
        # plant-specific runtime branch. The default preserves legacy graphs.
        init_output = max(
            blk.out_lo_lim,
            min(blk.out_hi_lim, float(p.get("out_init", 50.0))),
        )
        blk.OUT = SignalStatus(value=init_output)

        # Pre-condition integrator for bumpless start
        # _normalized_gain() = GAIN × PV_SPAN / OUT_SPAN; execute()
        # multiplies (p + integral) by gain_a to produce output in OUT scale.
        gain_a = blk._normalized_gain()
        if abs(gain_a) > 1e-12:
            sign = -1.0 if blk.direct_acting else 1.0
            p_init = blk.beta * (sign * sp_init) - (sign * sp_init)
            blk._integral = init_output / gain_a - p_init
            # Same conditioning for the OUT-units reset state used by the
            # positive-feedback network (inert for the default accumulator).
            blk._reset_fb = blk._integral * gain_a

        # Permit all modes; start in configured mode
        blk._permitted_modes = {
            Mode.OOS, Mode.Man, Mode.Auto, Mode.Cas,
            Mode.RCas, Mode.ROut, Mode.IMan, Mode.LO,
        }
        init_mode = str_to_mode(p.get("mode", "AUTO")) or Mode.Auto
        blk.set_target_mode(init_mode)

        self._pid_core = blk

    # ---------------------------------------------------------------- config
    def get_config_schema(self):
        return {
            # -- Tuning --
            "GAIN": (float, 1.0, "Proportional gain"),
            "RESET": (float, 60.0, "Integral time [s]"),
            "RATE": (float, 0.0, "Derivative time [s]"),
            "alpha": (float, 0.1, "Derivative filter coeff"),
            "beta": (float, 1.0, "2-DOF SP weight on P"),
            "gamma": (float, 0.0, "2-DOF SP weight on D"),
            "bias": (float, 0.0, "Manual reset / bias"),
            "ideadband": (float, 0.0, "Integral deadband"),
            # -- Filtering --
            "pv_ftime": (float, 0.0, "PV filter time [s]"),
            "sp_ftime": (float, 0.0, "SP filter time [s]"),
            # -- SP rate limiting --
            "sp_rate_up": (float, 0.0, "SP rate limit up [EU/s] (0=off)"),
            "sp_rate_dn": (float, 0.0, "SP rate limit dn [EU/s] (0=off)"),
            # -- Feedforward --
            "ff_enable": (bool, False, "Enable feedforward"),
            "ff_gain": (float, 1.0, "Feedforward gain"),
            "ff_scale_lo": (float, 0.0, "FF_SCALE low (EU of FF_VAL)"),
            "ff_scale_hi": (float, 100.0, "FF_SCALE high (EU of FF_VAL)"),
            # -- Action & structure --
            "action": (str, "reverse", "reverse or direct"),
            "form": (str, "standard", "PID form: standard or series"),
            "structure": (str, "two_dof", "PID structure"),
            # -- Output limits (PID outputs 0-100% unless use_out_limits) --
            "out_init": (float, 50.0, "Initial Manual output (0-100%)"),
            "out_lo": (float, 0.0, "Output low limit (needs use_out_limits)"),
            "out_hi": (float, 100.0, "Output high limit (needs use_out_limits)"),
            "use_out_limits": (bool, False, "Clamp OUT to out_lo/out_hi (Azeo OUT_*_LIM)"),
            "arw_lo": (float, -5.0, "ARW low limit (0-100 scale)"),
            "arw_hi": (float, 105.0, "ARW high limit (0-100 scale)"),
            "arw_scale": (str, "auto", "ARW limit units: auto/absolute/fraction"),
            # -- SP limits --
            "sp_init": (float, 50.0, "Initial setpoint (%)"),
            "sp_lo": (float, 0.0, "SP low limit"),
            "sp_hi": (float, 100.0, "SP high limit"),
            # -- PV scale --
            "pv_scale_lo": (float, 0.0, "PV scale low (EU)"),
            "pv_scale_hi": (float, 100.0, "PV scale high (EU)"),
            # -- Alarm limits --
            "hi_hi_lim": (float, float('inf'), "PV Hi-Hi alarm limit"),
            "hi_lim": (float, float('inf'), "PV Hi alarm limit"),
            "lo_lim": (float, float('-inf'), "PV Lo alarm limit"),
            "lo_lo_lim": (float, float('-inf'), "PV Lo-Lo alarm limit"),
            "dv_hi_lim": (float, float('inf'), "Deviation Hi alarm limit"),
            "dv_lo_lim": (float, float('-inf'), "Deviation Lo alarm limit"),
            "alarm_hys": (float, 0.0, "Alarm hysteresis"),
            # -- Nonlinear gain --
            "nl_enable": (bool, False, "Enable nonlinear gain"),
            "nl_gap": (float, 0.0, "Nonlinear gap (PV units)"),
            "nl_hyst": (float, 0.0, "Nonlinear hysteresis"),
            "nl_tband": (float, 0.0, "Nonlinear transition band"),
            "nl_minmod": (float, 1.0, "Min gain modifier (0=deadband)"),
            # -- FRSIPID options --
            "use_pidplus": (bool, False, "Enable PIDPlus"),
            "dynamic_reset_limit": (bool, False, "Enable DRL"),
            "use_delayed_out_bad_pv": (bool, False, "Delayed OUT on bad PV"),
            "recovery_fltr": (float, 1.0, "PIDPlus recovery filter (0-1)"),
            # -- Control options --
            "sp_pv_track_man": (bool, False, "SP tracks PV in Manual"),
            "sp_pv_track_lo_iman": (bool, False, "SP tracks PV in LO/IMan"),
            "sp_pv_track_rout": (bool, False, "SP tracks PV in ROut"),
            "use_pv_for_bkcal_out": (bool, False, "Use PV for BKCAL_OUT"),
            "no_out_limits_in_man": (bool, False, "No OUT limits in Manual"),
            "obey_sp_lim_cas_rcas": (bool, False, "Obey SP limits in Cas/RCas"),
            "track_enable": (bool, True, "Track enable (CONTROL_OPTS)"),
            "track_in_manual": (bool, True, "Track in manual (CONTROL_OPTS)"),
            "track_mode": (str, "azeo", "Tracking path: azeo (LO mode) or legacy (forces Man)"),
            "trk_scale_lo": (float, 0.0, "TRK_SCALE low (EU of TRK_VAL)"),
            "trk_scale_hi": (float, 100.0, "TRK_SCALE high (EU of TRK_VAL)"),
            "bypass_enable": (bool, False, "Bypass enable"),
            "bypass": (bool, False, "Bypass engaged (SP passes to OUT)"),
            "simulate_enabled": (bool, False, "Simulation mode"),
            # -- STATUS_OPTS --
            "target_manual_if_bad_in": (bool, False, "Latch target mode in Man on Bad PV"),
            "bad_if_limited": (bool, False, "OUT status Bad while the output is limited"),
            "uncertain_if_limited": (bool, False, "OUT status Uncertain while limited"),
            "use_uncertain_as_good": (bool, False, "Treat an Uncertain PV/CAS_IN as usable"),
            # -- Algorithm options (defaults reproduce the legacy behaviour) --
            "pv_filter_enable": (bool, False, "Apply pv_ftime (else PV is unfiltered)"),
            "reset_impl": (str, "positional",
                           "Reset network: positional, external (BKCAL_IN-fed "
                           "accumulator) or positive_feedback (reset state in "
                           "OUT units, Azeo)"),
            "deriv_filter_mode": (str, "legacy", "Derivative filter: legacy or azeo (with state)"),
            "series_impl": (str, "legacy", "Series form: legacy or azeo"),
            "dv_alarm_basis": (str, "error", "Deviation alarms on: error (SP-PV) or azeo (PV-SP)"),
            "alarm_hys_units": (str, "eu", "ALARM_HYS units: eu or percent (of PV span)"),
            "sp_rate_keeps_target": (bool, False, "SP stays the target; only SP_WRK ramps"),
            "sp_limit_restriction": (bool, False, "Restrict SP limits to PV scale +-10%"),
            "pidplus_dt_accumulate": (bool, False, "PIDPlus: carry skipped scan time into the integral"),
            "pidplus_improved_integral": (bool, False,
                                          "PIDPlus: exact 1-exp(-dt/RESET) reset step "
                                          "(robust when RESET ~ the scan rate)"),
            "nl_force_standard": (bool, False, "Nonlinear gain forces standard form + P on error"),
            # -- Remote shed-on-timeout (RCAS/ROUT watchdog) --
            "shed_time": (float, 0.0, "Seconds without DMC heartbeat before shed (0=off)"),
            "shed_opt": (str, "normal", "Shed target: normal/auto/manual/no_action"),
            # -- Mode --
            "mode": (str, "AUTO", "Initial mode: AUTO, MAN, CAS, OOS, RCAS, ROUT"),
            "normal_mode": (str, "", "Normal mode — what this block SHOULD normally be in. Blank means 'same as the initial mode'. Azeo lights the abnormal-mode icon when actual differs from NORMAL or from target; without this only the target half is answerable."),
            # -- Display metadata (for dynamic faceplate) --
            "pv_unit": (str, "", "PV engineering unit (e.g. degF, %, ppm)"),
            "op_unit": (str, "%", "Output engineering unit"),
            "description": (str, "", "Controller description"),
        }

    # Runtime state carried across a config change so retuning a live loop is
    # bumpless. Rebuilding the core from scratch zeroes the integrator, which
    # steps the output by the whole accumulated reset contribution — editing
    # even a description on a settled loop moved OUT by tens of percent.
    _CARRY_STATE = (
        "_integral", "_integral_prev", "_deriv_filter", "_prev_pv", "_prev_sp",
        "_saturated_time", "_new_measurement", "_sp_filt", "_pv_filt",
        "p_term", "i_term", "d_term",
        "_deriv_state", "_series_prev", "_series_dstate", "_pending_dt",
        "_nl_active", "_remote_age", "_reset_fb", "_reset_fb_prev",
    )

    def _apply_config(self):
        """Rebuild the core with the new config, preserving loop state.

        Mode and the working setpoint/output are carried too, so a parameter
        edit never bumps the valve or sheds the mode of a running loop.
        """
        old = self._pid_core
        self._pid_core = None
        self._ensure_block()
        new = self._pid_core
        self._stamp_terminal_eu()
        if old is None or new is None:
            return
        for attr in self._CARRY_STATE:
            if hasattr(old, attr):
                try:
                    setattr(new, attr, getattr(old, attr))
                except Exception:
                    pass
        # Carry mode + live signals so the loop resumes where it was.
        try:
            new.SP = old.SP
            new.SP_WRK = old.SP_WRK
            new.OUT = old.OUT
            new.PV = old.PV
            new._target_mode = old._target_mode
            new._actual_mode = old._actual_mode
        except Exception:
            pass

    def _stamp_terminal_eu(self) -> None:
        """Engineering units/ranges onto the loop's terminals (HMI §7.1).

        PV and SP share the PV scale; OUT is the controller's own 0–100 %
        span (see the out_scale note in `_ensure_block` — the JSON
        out_lo/out_hi describe the *downstream* range, not this span).
        """
        p = self.config.params
        pv_range = (float(p.get("pv_scale_lo", 0.0)),
                    float(p.get("pv_scale_hi", 100.0)))
        pv_units = str(p.get("pv_eng_units", p.get("eng_units", "")))
        self.set_terminal_eu("IN", pv_units, pv_range)
        self.set_terminal_eu("SP", pv_units, pv_range)
        self.set_terminal_eu("CAS_IN", pv_units, pv_range)
        self.set_terminal_eu("PV", pv_units, pv_range)
        self.set_terminal_eu("SP_WRK", pv_units, pv_range)
        out_range = (0.0, 100.0)
        if p.get("use_out_limits", False):
            out_range = (float(p.get("out_lo", 0.0)),
                         float(p.get("out_hi", 100.0)))
        self.set_terminal_eu(
            "OUT", str(p.get("op_unit", "%") or "%"), out_range)

    # Target vs actual mode, independently readable (HMI §7.2). The core
    # has always tracked the pair; these are the model-level exposure the
    # display-state contract's mode-mismatch priority reads.
    @property
    def mode_target(self) -> str:
        self._ensure_block()
        return mode_to_str(self._pid_core.target_mode)

    @property
    def mode_actual(self) -> str:
        self._ensure_block()
        return mode_to_str(self._pid_core.actual_mode)

    # ---------------------------------------------------------------- execute
    def execute(self, dt: float):
        self._ensure_block()
        from azeo_control_trainer.core.pid.core import SignalStatus, LimitStatus, Mode

        blk = self._pid_core

        pv = self.get_input("IN")
        sp = self.get_input("SP")
        trk_en = self.get_input("TRK_IN_D")
        trk_val = self.get_input("TRK_VAL")

        # Remote mode target from strategy wiring
        if self.inputs["MODE_TARGET"].connected:
            mode_tgt = self.get_input("MODE_TARGET")
            if mode_tgt:
                tgt_str = str(mode_tgt).upper().strip()
                if tgt_str:
                    target_mode = str_to_mode(tgt_str)
                    if target_mode is not None and target_mode != blk.actual_mode:
                        self.set_mode(tgt_str)

        # Set PV. IN_BAD (unwired: False) carries the status the bare float
        # wire cannot, so Bad-PV shedding is reachable from a strategy.
        # Prefer the quality carried on the wire; the explicit IN_BAD pin
        # remains as an override for strategies that wire it.
        pv_q = self.input_status("IN")
        pv_bad = pv_q is Quality.BAD
        pv_unc = pv_q is Quality.UNCERTAIN
        if self.inputs["IN_BAD"].connected:
            pv_bad = pv_bad or bool(self.get_input("IN_BAD"))
        # A Constant limit on the measurement (an AI held in Manual or out of
        # service) means the PV cannot move: the core stops integrating
        # against it instead of winding reset against a frozen number.
        pv_limit = (LimitStatus.CONSTANT
                    if self.input_limit("IN") is TermLimit.CONSTANT
                    else LimitStatus.NOT_LIMITED)
        blk.IN = SignalStatus(value=pv, bad=pv_bad, uncertain=pv_unc,
                              limit=pv_limit)
        if not getattr(self, "_pv_filter_enable", False):
            # Legacy: seed the filter state with the raw PV, i.e. no filtering.
            blk.PV = pv
            blk._pv_filt = pv
        elif not self._pv_filt_primed:
            # First scan with the filter enabled: start the filter state at the
            # live measurement instead of the configured sp_init, otherwise the
            # loop would see a step from the initialisation value.
            blk.PV = pv
            blk._pv_filt = pv
            self._pv_filt_primed = True

        # BYPASS (needs bypass_enable) — a slave passes its SP through to OUT.
        if self.inputs["BYPASS"].connected:
            blk.bypass = bool(self.get_input("BYPASS"))

        # Handle tracking
        if trk_en and self._track_mode == "legacy":
            # Legacy path: force the *target* mode to Man and write OUT.
            blk.set_target_mode(Mode.Man)
            clamped = max(blk.out_lo_lim, min(blk.out_hi_lim, trk_val))
            blk.OUT = SignalStatus(value=clamped)
            self.set_output("OUT", clamped)
        else:
            # Azeo path: hand TRK_IN_D / TRK_VAL to the core, which tracks in
            # Local Override, converts TRK_SCALE → OUT_SCALE, keeps executing
            # (PV, alarms, BKCAL_OUT stay live) and hands control back to the
            # target mode when the track command clears.
            blk.TRK_IN_D = bool(trk_en)
            blk.TRK_VAL = SignalStatus(value=trk_val)

            # BKCAL from downstream. The optional BKCAL_HI_LIM/BKCAL_LO_LIM
            # companions report a limited downstream block (AO at its travel
            # limit, clamped SCALER) so reset stops winding into the limit.
            if self.inputs["BKCAL_IN"].connected:
                bkcal_in = self.get_input("BKCAL_IN")
                # The BKCAL wire now carries the downstream block's limit
                # status itself; the boolean companions stay as an override
                # for strategies that wire them.
                _wl = self.input_limit("BKCAL_IN")
                limit = LimitStatus.NOT_LIMITED
                if _wl is TermLimit.HIGH_LIMITED:
                    limit = LimitStatus.HIGH_LIMITED
                elif _wl is TermLimit.LOW_LIMITED:
                    limit = LimitStatus.LOW_LIMITED
                elif _wl is TermLimit.CONSTANT:
                    limit = LimitStatus.CONSTANT
                if self.inputs["BKCAL_HI_LIM"].connected and self.get_input("BKCAL_HI_LIM"):
                    limit = LimitStatus.HIGH_LIMITED
                elif self.inputs["BKCAL_LO_LIM"].connected and self.get_input("BKCAL_LO_LIM"):
                    limit = LimitStatus.LOW_LIMITED
                bk_q = self.input_status("BKCAL_IN")
                blk.BKCAL_IN = SignalStatus(
                    value=bkcal_in, limit=limit,
                    bad=bk_q is Quality.BAD,
                    uncertain=bk_q is Quality.UNCERTAIN)

            # Cascade / Remote Cascade SP input
            actual = blk.actual_mode
            cas_q = self.input_status("CAS_IN")
            cas_bad = cas_q is Quality.BAD
            cas_unc = cas_q is Quality.UNCERTAIN
            if self.inputs["CAS_IN_BAD"].connected:
                cas_bad = cas_bad or bool(self.get_input("CAS_IN_BAD"))
            cas_available = (
                self.inputs["CAS_IN"].connected
                or getattr(self, "_bridge_cas_available", False)
            )
            # Refresh the core status before its mode resolver runs.  If a
            # Bad CAS input has shed the loop to Auto, conditioning this only
            # inside the actual-CAS branch creates a deadlock: the recovered
            # Good input can never be observed, so CAS can never re-enter.
            if cas_available:
                blk.CAS_IN = SignalStatus(
                    value=self.get_input("CAS_IN"),
                    bad=cas_bad,
                    uncertain=cas_unc,
                )
            if actual == Mode.RCas and self.inputs["RCAS_IN"].connected:
                # RCAS: SP from external MPC/DMC via RCAS_IN
                rcas_in = self.get_input("RCAS_IN")
                rq = self.input_status("RCAS_IN")
                blk.RCAS_IN = SignalStatus(
                    value=rcas_in, bad=cas_bad or rq is Quality.BAD,
                    uncertain=rq is Quality.UNCERTAIN)
            elif actual == Mode.RCas and self.inputs["CAS_IN"].connected:
                # Fallback: RCAS via CAS_IN (legacy wiring)
                cas_in = self.get_input("CAS_IN")
                blk.RCAS_IN = SignalStatus(value=cas_in, bad=cas_bad,
                                           uncertain=cas_unc)
            elif actual in (Mode.Cas, Mode.IMan) and cas_available:
                # CAS: SP from upstream PID cascade
                cas_in = self.get_input("CAS_IN")
                blk.CAS_IN = SignalStatus(value=cas_in, bad=cas_bad,
                                          uncertain=cas_unc)
            elif sp != 0.0 or self.inputs["SP"].connected or self._local_sp_written:
                blk.SP = sp

            # Remote output (ROUT mode): output commanded by DMC/host via a
            # wired ROUT_IN. (Bridge write-backs set blk.ROUT_IN directly.)
            if self.inputs["ROUT_IN"].connected:
                blk.ROUT_IN = SignalStatus(value=self.get_input("ROUT_IN"))

            # Feedforward
            if blk.ff_enable and self.inputs["FF_VAL"].connected:
                blk.FF_VAL = SignalStatus(value=self.get_input("FF_VAL"))

            # Execute the PID algorithm
            blk.execute(dt)

            self.set_output("OUT", blk.OUT.value)

        # Echo outputs
        self.set_output("BKCAL_OUT", blk.BKCAL_OUT.value)
        self.set_output("PV", blk.PV)
        self.set_output("SP", blk.SP)
        self.set_output("SP_WRK", blk.SP_WRK)
        actual_mode = blk.actual_mode
        self.set_output("MODE", mode_to_str(actual_mode))
        # AWS: Available for Write Status — True when controller accepts
        # remote setpoint writes (Auto, Cas, or RCas mode)
        self.set_output("AWS", actual_mode in (Mode.Auto, Mode.Cas, Mode.RCas))
        self.set_output("P_TERM", blk.p_term)
        self.set_output("I_TERM", blk.i_term)
        self.set_output("D_TERM", blk.d_term)
        # Remote back-calculation: a host writing RCAS_IN / ROUT_IN reads these
        # to initialise on the block's current working SP / output.
        self.set_output("RCAS_OUT", blk.SP_WRK)
        self.set_output("ROUT_OUT", blk.OUT.value)
        self.set_output("NL_GAIN_MOD", blk.nl_gain_mod)

        # ---- Publish quality + limit on the wires -----------------------
        # Azeo parameters are value *plus* status. OUT reports that it is
        # sitting on a limit (or cannot move at all, in a mode where the
        # algorithm does not drive it), and BKCAL_OUT carries the same limit
        # upstream — which is what stops a cascade master winding reset into
        # a slave that is already at its limit.
        out_q = Quality.GOOD
        if blk.OUT.bad:
            out_q = Quality.BAD
        elif blk.OUT.uncertain:
            out_q = Quality.UNCERTAIN
        out_limit = self._term_limit(blk.OUT.limit, LimitStatus)
        if (out_limit is TermLimit.NOT_LIMITED
                and actual_mode not in (Mode.Auto, Mode.Cas, Mode.RCas, Mode.ROut)):
            # Man / OOS / LO / IMan: the output is held, not computed.
            out_limit = TermLimit.CONSTANT
        self.set_output_status("OUT", out_q, out_limit)
        self.set_output_status("BKCAL_OUT", out_q,
                               self._term_limit(blk.BKCAL_OUT.limit, LimitStatus))
        # The PV echo carries the measurement's own quality.
        self.set_output_status(
            "PV",
            Quality.BAD if pv_bad else
            (Quality.UNCERTAIN if pv_unc else Quality.GOOD),
            self.input_limit("IN"))

    @staticmethod
    def _term_limit(core_limit, LimitStatus) -> "TermLimit":
        """Map the core's LimitStatus onto the terminal one."""
        if core_limit == LimitStatus.HIGH_LIMITED:
            return TermLimit.HIGH_LIMITED
        if core_limit == LimitStatus.LOW_LIMITED:
            return TermLimit.LOW_LIMITED
        if core_limit == LimitStatus.CONSTANT:
            return TermLimit.CONSTANT
        return TermLimit.NOT_LIMITED

    # ---------------------------------------------------------------- mode control
    def set_mode(self, mode: str):
        self._ensure_block()
        from azeo_control_trainer.core.pid.core import Mode, SignalStatus, LimitStatus
        mode_enum = str_to_mode(mode)
        if mode_enum is None:
            return

        blk = self._pid_core
        old_mode = blk.actual_mode

        # Bumpless transfer: back-calculate integral when leaving manual
        if old_mode in (Mode.Man, Mode.ROut, Mode.IMan) and mode_enum in (Mode.Auto, Mode.Cas, Mode.RCas):
            gain_a = blk._normalized_gain()
            if abs(gain_a) > 1e-12:
                sign = -1.0 if blk.direct_acting else 1.0
                p_now = blk.beta * (sign * blk.SP) - (sign * blk.PV)
                blk._integral = blk.OUT.value / gain_a - p_now
                blk._reset_fb = blk._integral * gain_a
            blk._prev_pv = blk.PV
            blk._prev_sp = blk.SP

        # Ensure BKCAL_IN is "connected" for cascade modes
        if mode_enum in (Mode.Cas, Mode.RCas):
            if blk.BKCAL_IN.limit == LimitStatus.NOT_INVITED:
                blk.BKCAL_IN = SignalStatus(
                    value=blk.BKCAL_IN.value, limit=LimitStatus.NOT_LIMITED)

        blk.set_target_mode(mode_enum)

    def set_sp(self, sp: float):
        self._ensure_block()
        self._pid_core.SP = sp
        # An unwired nonzero SP input is the scan's local setpoint. Leaving
        # its old value behind undid an accepted faceplate write next scan,
        # making the number and SP pointer snap back. Wired inputs keep their
        # upstream ownership and are refused by the checked operator API.
        if not self.inputs["SP"].connected:
            self.inputs["SP"].value = sp
            # Zero is a valid operator target, distinct from the unwritten
            # default input. Keep driving it while SP_WRK ramps toward zero.
            self._local_sp_written = True

    def set_manual_output(self, out: float):
        self._ensure_block()
        from azeo_control_trainer.core.pid.core import SignalStatus, Mode
        blk = self._pid_core
        clamped = max(blk.out_lo_lim, min(blk.out_hi_lim, out))
        if blk.actual_mode in (Mode.Man, Mode.ROut, Mode.OOS):
            blk.OUT = SignalStatus(value=clamped)

    def accepts_operator_write(self, what: str = "SP") -> tuple[bool, str]:
        """Ownership rules used by Loop_fp before it offers a write.

        Azeo documents SP slew in target AUTO/MAN and OUT slew in target
        MAN/OOS.  The generic source previously refused SP in MAN even though
        the faceplate correctly exposed it, leaving a control that looked live
        but could never complete its write.
        """
        name = str(what).upper()
        target = self.mode_target.upper()
        if name == "SP":
            if self.inputs["SP"].connected:
                return False, "SP is wired and owned by upstream control"
            allowed = target in ("AUTO", "MAN")
            return allowed, "" if allowed else f"SP is not operator-owned in {target}"
        if name in ("OUT", "OP"):
            allowed = target in ("MAN", "OOS")
            return allowed, "" if allowed else f"OUT is owned by the block in {target}"
        return False, f"{name} has no PID operator handle"

    def write_operator_value(self, value: float,
                             what: str = "SP") -> tuple[bool, str]:
        allowed, why = self.accepts_operator_write(what)
        if not allowed:
            return False, why
        if str(what).upper() == "SP":
            self.set_sp(float(value))
        else:
            self.set_manual_output(float(value))
        return True, ""

    def set_tuning(self, GAIN=None, RESET=None, RATE=None,
                    Kp=None, Ti=None, Td=None):
        self._ensure_block()
        gain = GAIN if GAIN is not None else Kp
        reset = RESET if RESET is not None else Ti
        rate = RATE if RATE is not None else Td
        if gain is not None:
            self._pid_core.gain = gain
        if reset is not None:
            self._pid_core.reset = max(reset, 0.0)
        if rate is not None:
            self._pid_core.rate = max(rate, 0.0)

    # ---------------------------------------------------------------- properties
    @property
    def pid_core_block(self):
        """Direct access to the underlying pid_algo.PIDBlock.

        Use for pid_widget faceplate sync::

            faceplate.update_from_block(pid_strategy_block.pid_core_block)
        """
        self._ensure_block()
        return self._pid_core

    @property
    def pid(self):
        """Backward-compatible alias — returns the PID core block."""
        return self.pid_core_block

    def reset(self):
        super().reset()
        self._pid_core = None
        self._pv_filt_primed = False
