"""PIDBlockView -- adapter bridging SharedDataStore to the ISA PIDBlock interface.

Reads controller data published by SimEngine (``ctrl.{tag}.*`` keys) and
presents a duck-typed PIDBlock-compatible view that the ISA-101 widgets
(InlineDynamo, FaceplatePopup, ControllerDetailDialog) can consume via
their ``update_from_block(block)`` methods.

Operator writes (SP, OP, mode) are routed back through
``SharedDataStore.queue_write()``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, fields as _dc_fields
from typing import Callable

from azeo_control_trainer.core.pid.core.pid_block_core import (
    Mode, LimitStatus, SignalStatus, ScaleRange,
    AlarmState, BlockError, PIDForm, Structure,
)
from azeo_control_trainer.config.units import (
    K_to_F, F_to_K, Pa_to_inH2O, inH2O_to_Pa,
    kgs_to_bpd, bpd_to_kgs,
    kgs_to_mmscfd_fuel, mmscfd_to_kgs_fuel,
    kgs_to_mmscfd_air, mmscfd_to_kgs_air,
    TEMP_UNIT, PRESS_UNIT, FEED_FLOW_UNIT, GAS_FLOW_UNIT,
)


def _identity(x: float) -> float:
    return x


# -- Per-controller display configuration ------------------------------------
# Extends CONTROLLER_DISPLAY from faceplate.py with ISA-specific fields
# (alarm limits, decimals, SP/OP ranges in display units).

@dataclass
class ControllerViewConfig:
    """Configuration for one PID controller's ISA-101 HMI view."""
    tag: str
    description: str
    pv_lo: float              # PV scale low (display units)
    pv_hi: float              # PV scale high (display units)
    pv_unit: str
    op_lo: float = 0.0        # OP scale low (%)
    op_hi: float = 100.0      # OP scale high (%)
    op_unit: str = "%"
    decimals: int = 1
    si_to_display: Callable = _identity
    display_to_si: Callable = _identity
    # Alarm limits (display units, inf = not configured)
    hihi_lim: float = math.inf
    hi_lim: float = math.inf
    lo_lim: float = -math.inf
    lolo_lim: float = -math.inf
    dv_hi_lim: float = math.inf
    dv_lo_lim: float = math.inf


def _coerce_view_config(tag: str, cfg) -> ControllerViewConfig:
    """Return a ControllerViewConfig from either an instance or a plain dict.

    Serialized sources (P&ID dynamo JSON, plugin-supplied dicts) may hand us a
    bare dict; build a config from its recognised keys and fall back to sane
    defaults for the required fields so the faceplate never crashes.
    """
    if isinstance(cfg, ControllerViewConfig):
        return cfg
    if isinstance(cfg, dict):
        valid = {f.name for f in _dc_fields(ControllerViewConfig)}
        kw = {k: v for k, v in cfg.items() if k in valid}
        kw.setdefault("tag", cfg.get("tag") or cfg.get("controller_tag") or tag)
        kw.setdefault("description",
                      cfg.get("description") or cfg.get("desc") or tag)
        kw.setdefault("pv_lo", 0.0)
        kw.setdefault("pv_hi", 100.0)
        kw.setdefault("pv_unit", cfg.get("pv_unit") or cfg.get("unit") or "EU")
        return ControllerViewConfig(**kw)
    raise TypeError(
        f"controller config for {tag!r} must be ControllerViewConfig or dict, "
        f"got {type(cfg).__name__}")


# Alarm limits are in display units (deg F, %, ppm, etc.)
CONTROLLER_CONFIGS: dict[str, ControllerViewConfig] = {
    'TIC101': ControllerViewConfig(
        tag='TIC101', description='COT Temperature',
        pv_lo=500.0, pv_hi=800.0, pv_unit=TEMP_UNIT, decimals=1,
        si_to_display=K_to_F, display_to_si=F_to_K,
        hihi_lim=780.0, hi_lim=760.0, lo_lim=550.0, lolo_lim=530.0,
    ),
    'FIC101': ControllerViewConfig(
        tag='FIC101', description='Total Feed Flow',
        pv_lo=0.0, pv_hi=200_000.0, pv_unit=FEED_FLOW_UNIT, decimals=0,
        si_to_display=kgs_to_bpd, display_to_si=bpd_to_kgs,
    ),
    'FIC102': ControllerViewConfig(
        tag='FIC102', description='Fuel Flow',
        pv_lo=0.0, pv_hi=5.0, pv_unit=GAS_FLOW_UNIT, decimals=2,
        si_to_display=kgs_to_mmscfd_fuel, display_to_si=mmscfd_to_kgs_fuel,
    ),
    'FIC103': ControllerViewConfig(
        tag='FIC103', description='Air Flow',
        pv_lo=0.0, pv_hi=20.0, pv_unit=GAS_FLOW_UNIT, decimals=2,
        si_to_display=kgs_to_mmscfd_air, display_to_si=mmscfd_to_kgs_air,
    ),
    'PIC101': ControllerViewConfig(
        tag='PIC101', description='Firebox Draft',
        pv_lo=-2.0, pv_hi=0.0, pv_unit=PRESS_UNIT, decimals=2,
        si_to_display=Pa_to_inH2O, display_to_si=inH2O_to_Pa,
        hi_lim=-0.05, lo_lim=-1.5,
    ),
    'PIC102': ControllerViewConfig(
        tag='PIC102', description='Fuel Gas Pressure',
        pv_lo=0.0, pv_hi=100.0, pv_unit=PRESS_UNIT, decimals=1,
        si_to_display=Pa_to_inH2O, display_to_si=inH2O_to_Pa,
    ),
    'AIC101': ControllerViewConfig(
        tag='AIC101', description='Stack O2 Trim',
        pv_lo=0.0, pv_hi=10.0, pv_unit='%', decimals=1,
        hihi_lim=8.0, hi_lim=6.0, lo_lim=0.8, lolo_lim=0.5,
    ),
    'AIC102': ControllerViewConfig(
        tag='AIC102', description='CO Override',
        pv_lo=0.0, pv_hi=1000.0, pv_unit='ppm', decimals=0,
        hihi_lim=800.0, hi_lim=500.0,
    ),
}

# Add per-pass temperature controllers (TIC101A through TIC101D)
for _i, _suffix in enumerate("ABCD", 1):
    _tag = f"TIC101{_suffix}"
    CONTROLLER_CONFIGS[_tag] = ControllerViewConfig(
        tag=_tag, description=f"Pass {_i} Temp",
        pv_lo=500.0, pv_hi=800.0, pv_unit=TEMP_UNIT, decimals=1,
        si_to_display=K_to_F, display_to_si=F_to_K,
        hihi_lim=780.0, hi_lim=760.0, lo_lim=550.0, lolo_lim=530.0,
    )

# Add per-pass feed controllers (FIC101A through FIC101D)
for _i, _suffix in enumerate("ABCD", 1):
    _tag = f"FIC101{_suffix}"
    CONTROLLER_CONFIGS[_tag] = ControllerViewConfig(
        tag=_tag, description=f"Pass {_i} Feed",
        pv_lo=0.0, pv_hi=50_000.0, pv_unit=FEED_FLOW_UNIT, decimals=0,
        si_to_display=kgs_to_bpd, display_to_si=bpd_to_kgs,
    )


# -- Mode string <-> Mode enum mapping ----------------------------------------

_STR_TO_MODE: dict[str, Mode] = {
    "AUTO":    Mode.Auto,
    "MAN":     Mode.Man,
    "MANUAL":  Mode.Man,
    "CAS":     Mode.Cas,
    "CASCADE": Mode.Cas,
    "RCAS":    Mode.RCas,
    "ROUT":    Mode.ROut,
    "IMAN":    Mode.IMan,
}

_MODE_TO_STR: dict[Mode, str] = {v: k for k, v in _STR_TO_MODE.items()}

_ARW_TO_LIMIT: dict[str, LimitStatus] = {
    "NO_ALARM":    LimitStatus.NOT_LIMITED,
    "HI_LIMITED":  LimitStatus.HIGH_LIMITED,
    "LO_LIMITED":  LimitStatus.LOW_LIMITED,
}


# -- PIDBlockView --------------------------------------------------------------

class PIDBlockView:
    """Read-only adapter presenting SharedDataStore controller data as a
    PIDBlock-compatible interface for ISA-101 HMI widgets.

    Call ``refresh(data)`` once per tick with the latest data dict from
    ``SharedDataStore.get_all()``.  Widgets then call
    ``update_from_block(view)`` as if *view* were a real ``PIDBlock``.
    """

    def __init__(self, tag: str, cfg, store):
        # Configs can arrive as a ControllerViewConfig or, from serialized
        # sources (P&ID dynamo JSON, plugin dicts), as a plain dict. Coerce
        # so the faceplate stack only ever sees a ControllerViewConfig.
        cfg = _coerce_view_config(tag, cfg)
        self._tag = tag
        self._cfg = cfg
        self._store = store

        # Identity
        self.name = tag
        self._description = cfg.description

        # Scales in display units
        self.pv_scale = ScaleRange(eu0=cfg.pv_lo, eu100=cfg.pv_hi)
        self.out_scale = ScaleRange(eu0=cfg.op_lo, eu100=cfg.op_hi)

        # Live values (display units)
        self.PV: float = 0.0
        self.SP: float = 0.0
        self.SP_WRK: float = 0.0
        self.ERROR: float = 0.0
        self.OUT = SignalStatus(value=0.0, limit=LimitStatus.NOT_LIMITED)
        self.BKCAL_OUT = SignalStatus(value=0.0, limit=LimitStatus.NOT_LIMITED)

        # Mode
        self._actual_mode: Mode = Mode.Auto
        self._target_mode: Mode = Mode.Auto

        # Tuning (stored as-is from the PID controller)
        self.gain: float = 0.0
        self.reset: float = 1.0
        self.rate: float = 0.0
        self.alpha: float = 0.1
        self.beta: float = 1.0
        self.gamma: float = 0.0
        self.pv_ftime: float = 0.0
        self.sp_ftime: float = 0.0
        self.sp_rate_dn: float = 0.0
        self.sp_rate_up: float = 0.0
        self.ff_gain: float = 0.0
        self.ideadband: float = 0.0
        self.bias: float = 0.0

        # PID terms
        self.p_term: float = 0.0
        self.i_term: float = 0.0
        self.d_term: float = 0.0
        self._integral: float = 0.0  # diagnostic

        # Alarm & error state
        self.alarm_state = AlarmState()
        self.block_err = BlockError()

        # Limits (display units)
        self.hihi_lim: float = cfg.hihi_lim
        self.hi_lim: float = cfg.hi_lim
        self.lo_lim: float = cfg.lo_lim
        self.lolo_lim: float = cfg.lolo_lim
        self.dv_hi_lim: float = cfg.dv_hi_lim
        self.dv_lo_lim: float = cfg.dv_lo_lim
        self.out_hi_lim: float = cfg.op_hi
        self.out_lo_lim: float = cfg.op_lo
        self.sp_hi_lim: float = cfg.pv_hi
        self.sp_lo_lim: float = cfg.pv_lo
        self.alarm_hys: float = 0.5
        self.arw_hi_lim: float = cfg.op_hi
        self.arw_lo_lim: float = cfg.op_lo

        # Structure / form (defaults)
        self.form = PIDForm.STANDARD
        self.structure = Structure.PID_ON_ERROR

        # Options
        self.use_pidplus: bool = False
        self.use_nonlinear_gain: bool = False
        self.dynamic_reset_limit: bool = False
        self.direct_acting: bool = False
        self.bypass: bool = False
        self.bypass_enable: bool = False
        self.sp_pv_track_in_man: bool = False
        self.sp_pv_track_in_lo: bool = False
        self.sp_pv_track_in_iman: bool = False
        self.use_pv_for_bkcal_out: bool = False
        self.no_out_limits_in_man: bool = False
        self.obey_sp_lim_cas_rcas: bool = False
        self.track_enable: bool = False
        self.track_in_manual: bool = False
        self.ff_enable: bool = False
        self.simulate_enabled: bool = False
        self.use_delayed_out_bad_pv: bool = False
        self.nl_gap: float = 0.0
        self.nl_hyst: float = 0.0
        self.nl_tband: float = 0.0
        self.nl_minmod: float = 0.0
        self.recovery_fltr: float = 0.0

        # Units (for display in detail dialog)
        self.pv_units: str = cfg.pv_unit
        self.out_units: str = cfg.op_unit

        # Permitted modes
        self.permitted_modes = frozenset({Mode.Auto, Mode.Man, Mode.Cas})

    # -- Properties for PIDBlock compatibility --
    @property
    def tag(self) -> str:
        return self._tag

    @property
    def description(self) -> str:
        return self._description

    @description.setter
    def description(self, value: str) -> None:
        self._description = value

    @property
    def actual_mode(self) -> Mode:
        return self._actual_mode

    @property
    def target_mode(self) -> Mode:
        return self._target_mode

    @property
    def sp_wrk(self) -> float:
        return self.SP_WRK

    @property
    def normal_mode(self) -> Mode:
        return Mode.Auto

    # -- Refresh from SharedDataStore --

    def refresh(self, data: dict) -> None:
        """Read all controller data from the shared store dict and update
        the view. Called once per UI tick (typically 1 Hz).

        Args:
            data: Result of ``SharedDataStore.get_all()``
        """
        tag = self._tag
        conv = self._cfg.si_to_display

        # PV, SP, OUT (convert SI -> display units)
        raw_pv = data.get(f'ctrl.{tag}.PV', 0.0)
        raw_sp = data.get(f'ctrl.{tag}.SP', 0.0)
        raw_out = data.get(f'ctrl.{tag}.OUT', 0.0)

        self.PV = conv(raw_pv) if raw_pv is not None else 0.0
        self.SP = conv(raw_sp) if raw_sp is not None else 0.0
        self.SP_WRK = self.SP  # no SP rate limiting in adapter
        self.ERROR = self.PV - self.SP

        # Output already in 0-100% (Azeo convention)
        out_val = float(raw_out) if raw_out is not None else 0.0
        arw = str(data.get(f'ctrl.{tag}.ARWStatus', 'NO_ALARM'))
        limit = _ARW_TO_LIMIT.get(arw, LimitStatus.NOT_LIMITED)
        self.OUT = SignalStatus(value=out_val, limit=limit)

        # BKCAL_OUT
        bkcal = data.get(f'ctrl.{tag}.BKCAL_OUT', 0.0)
        self.BKCAL_OUT = SignalStatus(
            value=float(bkcal) if bkcal is not None else 0.0,
            limit=limit,
        )

        # Mode
        mode_str = str(data.get(f'ctrl.{tag}.Mode', 'AUTO'))
        self._actual_mode = _STR_TO_MODE.get(mode_str, Mode.Auto)
        self._target_mode = self._actual_mode

        # Tuning parameters
        self.gain = float(data.get(f'ctrl.{tag}.GAIN', data.get(f'ctrl.{tag}.Kp', 0.0)) or 0.0)
        self.reset = float(data.get(f'ctrl.{tag}.RESET', data.get(f'ctrl.{tag}.Ti', 1.0)) or 1.0)
        self.rate = float(data.get(f'ctrl.{tag}.RATE', data.get(f'ctrl.{tag}.Td', 0.0)) or 0.0)
        self.beta = float(data.get(f'ctrl.{tag}.Beta', 1.0) or 1.0)
        self.gamma = float(data.get(f'ctrl.{tag}.Gamma', 0.0) or 0.0)
        self.alpha = float(data.get(f'ctrl.{tag}.Alpha', 0.1) or 0.1)
        self.bias = float(data.get(f'ctrl.{tag}.bias', 0.0) or 0.0)
        self.ideadband = float(data.get(f'ctrl.{tag}.ideadband', 0.0) or 0.0)

        # Filtering & rate limiting
        self.pv_ftime = float(data.get(f'ctrl.{tag}.pv_ftime', 0.0) or 0.0)
        self.sp_ftime = float(data.get(f'ctrl.{tag}.sp_ftime', 0.0) or 0.0)
        self.sp_rate_up = float(data.get(f'ctrl.{tag}.sp_rate_up', 0.0) or 0.0)
        self.sp_rate_dn = float(data.get(f'ctrl.{tag}.sp_rate_dn', 0.0) or 0.0)
        self.ff_gain = float(data.get(f'ctrl.{tag}.ff_gain', 0.0) or 0.0)

        # P/I/D terms
        self.p_term = float(data.get(f'ctrl.{tag}.P_Term', 0.0) or 0.0)
        self.i_term = float(data.get(f'ctrl.{tag}.I_Term', 0.0) or 0.0)
        self.d_term = float(data.get(f'ctrl.{tag}.D_Term', 0.0) or 0.0)
        self._integral = float(data.get(f'ctrl.{tag}._integral', self.i_term) or 0.0)

        # Limits
        sp_min = data.get(f'ctrl.{tag}.SP_Min')
        sp_max = data.get(f'ctrl.{tag}.SP_Max')
        if sp_min is not None and sp_max is not None:
            self.sp_lo_lim = conv(sp_min)
            self.sp_hi_lim = conv(sp_max)

        op_min = data.get(f'ctrl.{tag}.OP_Min')
        op_max = data.get(f'ctrl.{tag}.OP_Max')
        if op_min is not None:
            self.out_lo_lim = float(op_min)  # already 0-100%
        if op_max is not None:
            self.out_hi_lim = float(op_max)

        arw_lo = data.get(f'ctrl.{tag}.arw_lo_lim')
        arw_hi = data.get(f'ctrl.{tag}.arw_hi_lim')
        if arw_lo is not None:
            self.arw_lo_lim = float(arw_lo)
        if arw_hi is not None:
            self.arw_hi_lim = float(arw_hi)

        # Alarm limits (these are in PV engineering units from the PID block)
        _alm_keys = [
            ('hi_hi_lim', 'hihi_lim'), ('hi_lim', 'hi_lim'),
            ('lo_lim', 'lo_lim'), ('lo_lo_lim', 'lolo_lim'),
            ('dv_hi_lim', 'dv_hi_lim'), ('dv_lo_lim', 'dv_lo_lim'),
        ]
        for store_key, view_attr in _alm_keys:
            val = data.get(f'ctrl.{tag}.{store_key}')
            if val is not None:
                fv = float(val)
                if math.isfinite(fv):
                    setattr(self, view_attr, conv(fv) if view_attr not in ('dv_hi_lim', 'dv_lo_lim') else fv)
        alarm_hys = data.get(f'ctrl.{tag}.alarm_hys')
        if alarm_hys is not None:
            self.alarm_hys = float(alarm_hys)

        # Structure / form
        form_str = data.get(f'ctrl.{tag}.form')
        if form_str is not None:
            for f in PIDForm:
                if f.value == form_str:
                    self.form = f
                    break
        struct_str = data.get(f'ctrl.{tag}.structure')
        if struct_str is not None:
            for s in Structure:
                if s.value == struct_str:
                    self.structure = s
                    break

        # Boolean options
        _BOOL_KEYS = [
            'direct_acting', 'use_pidplus', 'use_nonlinear_gain',
            'dynamic_reset_limit', 'use_delayed_out_bad_pv',
            'bypass', 'bypass_enable',
            'sp_pv_track_man', 'sp_pv_track_lo_iman', 'sp_pv_track_rout',
            'use_pv_for_bkcal_out', 'no_out_limits_in_man',
            'obey_sp_lim_cas_rcas', 'track_enable', 'track_in_manual',
            'ff_enable', 'simulate_enabled',
        ]
        for attr in _BOOL_KEYS:
            val = data.get(f'ctrl.{tag}.{attr}')
            if val is not None:
                # Map pid_algo attr names to view attr names if different
                view_attr = attr
                if attr == 'sp_pv_track_man':
                    view_attr = 'sp_pv_track_in_man'
                elif attr == 'sp_pv_track_lo_iman':
                    view_attr = 'sp_pv_track_in_lo'
                    # Also set iman variant
                    self.sp_pv_track_in_iman = bool(val)
                setattr(self, view_attr, bool(val))

        # Nonlinear gain params
        for attr in ('nl_gap', 'nl_hyst', 'nl_tband', 'nl_minmod', 'recovery_fltr'):
            val = data.get(f'ctrl.{tag}.{attr}')
            if val is not None:
                setattr(self, attr, float(val))

        # Alarm state — prefer block-computed alarm state if available
        alarm_dict = data.get(f'ctrl.{tag}.alarm_state')
        if isinstance(alarm_dict, dict):
            self.alarm_state = AlarmState(
                hi_hi_act=alarm_dict.get('hi_hi_act', False),
                hi_act=alarm_dict.get('hi_act', False),
                lo_act=alarm_dict.get('lo_act', False),
                lo_lo_act=alarm_dict.get('lo_lo_act', False),
                dv_hi_act=alarm_dict.get('dv_hi_act', False),
                dv_lo_act=alarm_dict.get('dv_lo_act', False),
            )
        else:
            # Fallback: compute from PV vs limits
            pv = self.PV
            self.alarm_state = AlarmState(
                hi_hi_act=pv >= self.hihi_lim if math.isfinite(self.hihi_lim) else False,
                hi_act=pv >= self.hi_lim if math.isfinite(self.hi_lim) else False,
                lo_act=pv <= self.lo_lim if math.isfinite(self.lo_lim) else False,
                lo_lo_act=pv <= self.lolo_lim if math.isfinite(self.lolo_lim) else False,
                dv_hi_act=(
                    abs(self.ERROR) >= self.dv_hi_lim
                    if math.isfinite(self.dv_hi_lim) and self._actual_mode in (Mode.Auto, Mode.Cas)
                    else False
                ),
                dv_lo_act=(
                    abs(self.ERROR) >= self.dv_lo_lim
                    if math.isfinite(self.dv_lo_lim) and self._actual_mode in (Mode.Auto, Mode.Cas)
                    else False
                ),
            )

        # Block errors
        err_dict = data.get(f'ctrl.{tag}.block_err')
        if isinstance(err_dict, dict):
            self.block_err = BlockError()
            if err_dict.get('any_active', False):
                for msg in err_dict.get('summary', []):
                    msg_lower = msg.lower()
                    if 'out of service' in msg_lower:
                        self.block_err.out_of_service = True
                    elif 'input failure' in msg_lower or 'bad pv' in msg_lower:
                        self.block_err.input_failure = True
                    elif 'simulate' in msg_lower:
                        self.block_err.simulate_active = True
                    elif 'local override' in msg_lower:
                        self.block_err.local_override = True

    # -- Write-back methods --

    def set_target_mode(self, mode: Mode) -> None:
        """Request mode change via SharedDataStore."""
        mode_str = _MODE_TO_STR.get(mode, "AUTO")
        self._store.queue_write(f'ctrl.{self._tag}.wb.Mode', mode_str)

    def write_sp(self, display_value: float) -> None:
        """Write setpoint (in display units) back to simulation."""
        si_val = self._cfg.display_to_si(display_value)
        self._store.queue_write(f'ctrl.{self._tag}.wb.SP', si_val)

    def write_op(self, percent: float) -> None:
        """Write output (0-100%) back to simulation."""
        self._store.queue_write(f'ctrl.{self._tag}.wb.ManOut', percent)

    def write_param(self, attr: str, value: float) -> None:
        """Write a tuning/config parameter back to the controller via bridge.

        Uses the ``ctrl.<tag>.wb.<key>`` convention so the bridge knows
        these are operator write-backs rather than published values.
        """
        # Handle form/structure combos (value is enum index)
        if attr == 'form':
            forms = list(PIDForm)
            idx = int(value)
            if 0 <= idx < len(forms):
                self._store.queue_write(
                    f'ctrl.{self._tag}.wb.form', forms[idx].value)
            return
        if attr == 'structure':
            structs = list(Structure)
            idx = int(value)
            if 0 <= idx < len(structs):
                self._store.queue_write(
                    f'ctrl.{self._tag}.wb.structure', structs[idx].value)
            return

        # Output/SP limits: already in 0-100% (Azeo convention)
        if attr in ('out_hi_lim', 'out_lo_lim', 'arw_hi_lim', 'arw_lo_lim'):
            pass  # already in 0-100%
        # SP limits come in display units, convert to SI
        elif attr in ('sp_hi_lim', 'sp_lo_lim'):
            value = self._cfg.display_to_si(value)
        # Alarm limits in display PV units, convert to SI
        elif attr in ('hi_hi_lim', 'hi_lim', 'lo_lim', 'lo_lo_lim'):
            value = self._cfg.display_to_si(value)

        attr_to_key = {
            'gain': 'GAIN', 'reset': 'RESET', 'rate': 'RATE',
            'alpha': 'Alpha', 'beta': 'Beta', 'gamma': 'Gamma',
            'pv_ftime': 'pv_ftime', 'sp_ftime': 'sp_ftime',
            'sp_rate_up': 'sp_rate_up', 'sp_rate_dn': 'sp_rate_dn',
            'ff_gain': 'ff_gain', 'ideadband': 'ideadband',
            'bias': 'bias', 'recovery_fltr': 'recovery_fltr',
            'nl_gap': 'nl_gap', 'nl_hyst': 'nl_hyst',
            'nl_tband': 'nl_tband', 'nl_minmod': 'nl_minmod',
            'alarm_hys': 'alarm_hys',
            'hi_hi_lim': 'hi_hi_lim', 'hi_lim': 'hi_lim',
            'lo_lim': 'lo_lim', 'lo_lo_lim': 'lo_lo_lim',
            'dv_hi_lim': 'dv_hi_lim', 'dv_lo_lim': 'dv_lo_lim',
            'out_hi_lim': 'OP_Max', 'out_lo_lim': 'OP_Min',
            'sp_hi_lim': 'SP_Max', 'sp_lo_lim': 'SP_Min',
            'arw_hi_lim': 'arw_hi_lim', 'arw_lo_lim': 'arw_lo_lim',
        }
        key = attr_to_key.get(attr, attr)
        self._store.queue_write(f'ctrl.{self._tag}.wb.{key}', value)

    def write_flag(self, attr: str, value: bool) -> None:
        """Write a boolean option back to the controller via bridge."""
        self._store.queue_write(f'ctrl.{self._tag}.wb.{attr}', value)
