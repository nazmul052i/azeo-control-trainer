"""Azeo capability: ALARM_DET, DIAG, INSPECT, FLC.

The last four implementable blocks from `docs/ref_docs/
AZEO_FUNCTION_BLOCKS.md`. The two the reference itself rules out stay
out: the MPC family ("out of implementation scope" — its optimizer
equations exist only as unrecovered figures) and NN (propagation equations
likewise figure-only). A block built by guessing its algorithm would be a
Azeo lie with a Azeo name.

Documented divergences, in the spirit of the rest of the dv_* set:

- **ALARM_DET** implements the standard six-condition detection and Azeo's
  conditional-alarming parameters through the same state machine as the
  extended ``ALARM`` block; their saved names remain independent contracts.
- **INSPECT** has "no processing algorithm" in Azeo — the Inspect server
  computes everything. The trainer has no Inspect server, so this block
  *is* the server for its scope: device counts come from the DIAG blocks
  it can see (its module, or every on-scan module with SYSTEM), and
  UTILIZATION is the running fraction of mode-carrying blocks found in
  their configured normal mode.
- **FLC** carries the fuzzy core exactly per the recovered equations
  (two membership functions per input, four rules, three output
  singletons, scaling-factor clipping, SP-change adaptation) with
  Man/Auto/OOS and SP/OUT limiting. The PID block's full cascade
  scaffolding (Cas/RCas/ROut, tracking, bypass) is not duplicated here —
  wire an FLC where a single loop needs the nonlinear PI, use PID for
  cascades.
"""
from __future__ import annotations

from ..model.block_registry import register_block
from ..model.block_base import BlockCategory, BlockStatus, FunctionBlock
from ..model.terminal import DataType, LimitStatus, Quality
from .alarm_engine import (
    AlarmRule,
    BASE_ANALOG_ALARMS,
    STANDARD_ALARMS,
    StandardAlarmEngine,
)


# ═══════════════════════════════════════════════════════════════════════
#  ALARM_DET — Alarm Detection
# ═══════════════════════════════════════════════════════════════════════
@register_block
class AlarmDetectionBlock(FunctionBlock):
    """Alarm Detection — six alarm conditions on any wired value.

    High/high-high/low/low-low limits on PV plus deviation-high/-low on
    ``PV − SP``; each reports through its ``_ACT`` discrete. ``ALARM_HYS``
    (percent of the IN scale) is the amount the value must return inside a
    limit before the active alarm clears. Alarming runs regardless of the
    input's status — a Bad measurement stops the *quality* from being
    trusted, not the operator from being told (the block reports Bad on
    PV while still evaluating).
    """

    block_type = "ALARM_DET"
    category = BlockCategory.IO
    display_name = "Alarm Detection (ALARM_DET)"
    description = "HI/HI_HI/LO/LO_LO + deviation alarms on a wired value"

    # ALARM_DET retains the Azeo names as its canonical persisted surface;
    # aliases accept an ALARM-authored template without renaming terminals in
    # the debugger or changing files that already use this block type.
    terminal_aliases = {
        "PV": "IN",
        "HI_HI": "HI_HI_ACT", "HI": "HI_ACT",
        "LO": "LO_ACT", "LO_LO": "LO_LO_ACT",
        "DEV_HI": "DV_HI_ACT", "DEV_LO": "DV_LO_ACT",
    }
    config_aliases = {
        "DEADBAND": "ALARM_HYS",
        "DEV_HI": "DV_HI_LIM", "DEV_LO": "DV_LO_LIM",
        "IN_SCALE_LO": "IN_LO", "IN_SCALE_HI": "IN_HI",
        "HI_EN": "HI_ENAB", "HH_EN": "HI_HI_ENAB",
        "LO_EN": "LO_ENAB", "LL_EN": "LO_LO_ENAB",
    }
    config_units = {
        "ALARM_HYS": "%",
        **{
            f"{name}_{suffix}": unit
            for name in STANDARD_ALARMS
            for suffix, unit in (
                ("DELAY_ON", "s"),
                ("DELAY_OFF", "s"),
                ("ENAB_DELAY", "s"),
                ("HYS", "%"),
            )
        },
    }

    def __init__(self, instance_name=""):
        self._alarm_engine = StandardAlarmEngine()
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN", description="Value to alarm on")
        self.add_input("SP", description="Setpoint for deviation alarming")
        self.add_input("ENABLE", DataType.BOOL, True,
                       "False = alarming disabled, all _ACT cleared")
        self.add_output("PV", DataType.FLOAT, 0.0,
                        "Value used for limit detection (scaled if enabled)")
        for name, desc in (("HI_HI_ACT", "PV above HI_HI_LIM"),
                           ("HI_ACT", "PV above HI_LIM"),
                           ("LO_ACT", "PV below LO_LIM"),
                           ("LO_LO_ACT", "PV below LO_LO_LIM"),
                           ("DV_HI_ACT", "PV−SP above DV_HI_LIM"),
                           ("DV_LO_ACT", "PV−SP below DV_LO_LIM")):
            self.add_output(name, DataType.BOOL, False, desc)

    def get_config_schema(self):
        schema = {
            "HI_HI_LIM": (float, 1e9, "High-high alarm limit [EU]"),
            "HI_LIM": (float, 1e9, "High alarm limit [EU]"),
            "LO_LIM": (float, -1e9, "Low alarm limit [EU]"),
            "LO_LO_LIM": (float, -1e9, "Low-low alarm limit [EU]"),
            "DV_HI_LIM": (float, 1e9,
                          "Allowed PV−SP above SP before DV_HI"),
            "DV_LO_LIM": (float, -1e9,
                          "Allowed PV−SP below SP (negative number)"),
            "ALARM_HYS": (float, 0.5,
                          "Return inside the limit before an alarm clears "
                          "[% of IN scale; max 50]"),
            "SCALE_ENABLE": (bool, False,
                             "IN is percent — scale onto IN_LO..IN_HI"),
            "IN_LO": (float, 0.0, "IN scale low [EU]"),
            "IN_HI": (float, 100.0, "IN scale high [EU]"),
            "DEFAULT": (float, 0.0,
                        "PV used when IN is unwired/not connected"),
            "CONDALM_ENABLED": (
                bool, False,
                "Enable per-alarm ENAB, delays, and hysteresis"),
        }
        if self.config.params.get("CONDALM_ENABLED", False):
            for name in STANDARD_ALARMS:
                schema[f"{name}_ENAB"] = (
                    bool, True, f"{name}: enable conditional alarm processing")
                schema[f"{name}_DELAY_ON"] = (
                    float, 0.0,
                    f"{name}: condition persistence before ACT [s]")
                schema[f"{name}_DELAY_OFF"] = (
                    float, 0.0,
                    f"{name}: ACT hold after the condition clears [s]")
                schema[f"{name}_ENAB_DELAY"] = (
                    float, 0.0,
                    f"{name}: suppression after ENAB rises [s]")
                schema[f"{name}_HYS"] = (
                    float, 0.0,
                    f"{name}: conditional deadband [% of IN range]")
        return schema

    @staticmethod
    def _hysteresis_eu(value: float, *, span: float) -> float:
        hys = max(float(value or 0.0), 0.0)
        # ALARM_DET declares both standard and conditional HYS parameters as
        # Percent (or Percent of IN_SCALE).  SCALE_ENABLE changes PV scaling,
        # not the percentage nature of the deadband.
        return min(hys, 50.0) / 100.0 * abs(span)

    def _pv(self, p) -> tuple[float, Quality]:
        terminal = self.inputs["IN"]
        raw = self.get_input("IN")
        # Terminal has no NOT_CONNECTED quality enum.  Explicit DEFAULT in a
        # saved block is therefore the unambiguous request to use it when the
        # graph has no wire.  When DEFAULT is absent, retaining a directly set
        # input value preserves online/debugger injection and older modules.
        use_default = raw is None or (
            not terminal.connected and not terminal.forced and "DEFAULT" in p)
        value = float(p.get("DEFAULT", 0.0) if use_default else raw)
        lo = float(p.get("IN_LO", 0.0))
        hi = float(p.get("IN_HI", 100.0))
        if bool(p.get("SCALE_ENABLE", False)):
            value = lo + (hi - lo) * value / 100.0
        quality = Quality.GOOD if use_default else self.input_status("IN")
        return value, quality

    def execute(self, dt: float):
        p = self.config.params
        lo = float(p.get("IN_LO", 0.0))
        hi = float(p.get("IN_HI", 100.0))
        span = hi - lo
        base_hys = self._hysteresis_eu(
            p.get("ALARM_HYS", 0.5), span=span)
        conditional = bool(p.get("CONDALM_ENABLED", False))
        pv, quality = self._pv(p)
        sp = float(self.get_input("SP") or 0.0)
        self.set_output("PV", pv)
        self.set_output_status("PV", quality, LimitStatus.NOT_LIMITED)

        def hys_for(name: str) -> float:
            if conditional and name in BASE_ANALOG_ALARMS:
                return self._hysteresis_eu(
                    p.get(f"{name}_HYS", 0.0), span=span)
            return base_hys

        def alarm_rule(name: str, key: str, default: float,
                       high: bool) -> AlarmRule:
            return AlarmRule(
                limit=float(p.get(key, default)),
                high=high,
                hysteresis=hys_for(name),
                enabled=(bool(p.get(f"{name}_ENAB", True))
                         if conditional else True),
                delay_on=float(p.get(f"{name}_DELAY_ON", 0.0) or 0.0),
                delay_off=float(p.get(f"{name}_DELAY_OFF", 0.0) or 0.0),
                enable_delay=float(
                    p.get(f"{name}_ENAB_DELAY", 0.0) or 0.0),
            )

        rules = {
            "HI_HI": alarm_rule("HI_HI", "HI_HI_LIM", 1e9, True),
            "HI": alarm_rule("HI", "HI_LIM", 1e9, True),
            "LO": alarm_rule("LO", "LO_LIM", -1e9, False),
            "LO_LO": alarm_rule("LO_LO", "LO_LO_LIM", -1e9, False),
            "DV_HI": alarm_rule("DV_HI", "DV_HI_LIM", 1e9, True),
            "DV_LO": alarm_rule("DV_LO", "DV_LO_LIM", -1e9, False),
        }
        active = self._alarm_engine.evaluate(
            pv=pv,
            sp=sp,
            dt=dt,
            rules=rules,
            conditional=conditional,
            master_enabled=bool(self.get_input("ENABLE")),
        )
        for name, output in (
            ("HI_HI", "HI_HI_ACT"), ("HI", "HI_ACT"),
            ("LO", "LO_ACT"), ("LO_LO", "LO_LO_ACT"),
            ("DV_HI", "DV_HI_ACT"), ("DV_LO", "DV_LO_ACT"),
        ):
            self.set_output(output, active[name])

    def reset(self):
        super().reset()
        self._alarm_engine.reset()


# ═══════════════════════════════════════════════════════════════════════
#  DIAG — Diagnostic
# ═══════════════════════════════════════════════════════════════════════
@register_block
class DiagnosticBlock(FunctionBlock):
    """Diagnostic — device alerts for non-fieldbus assets, as one word.

    ``OUT = Σ bit values of the true inputs`` with the spec's exact bits
    (ADVISORY 0x1000, COMMFAIL 0x2000, FAILED 0x4000, MAINT 0x8000).
    INSPECT decodes the word. Name each DIAG after the asset it monitors —
    the spec warns that duplicate names make the Inspect view ambiguous.
    """

    block_type = "DIAG"
    category = BlockCategory.APC
    display_name = "Diagnostic (DIAG)"
    description = "Encode device alerts into one word for INSPECT"

    ADVISORY = 0x1000
    COMMFAIL = 0x2000
    FAILED = 0x4000
    MAINT = 0x8000

    def _define_terminals(self):
        for name, desc in (("ADVISORY", "Advisory alert active"),
                           ("COMMFAIL", "Communications-failure alert"),
                           ("FAILED", "Failed alert active"),
                           ("MAINT", "Maintenance alert active")):
            self.add_input(name, DataType.BOOL, False, desc)
        self.add_output("OUT", DataType.FLOAT, 0.0,
                        "Sum of the active alerts' bit values")

    def get_config_schema(self):
        return {}

    def execute(self, dt: float):
        word = 0
        for name, bit in (("ADVISORY", self.ADVISORY),
                          ("COMMFAIL", self.COMMFAIL),
                          ("FAILED", self.FAILED),
                          ("MAINT", self.MAINT)):
            if bool(self.get_input(name)):
                word |= bit
        self.set_output("OUT", float(word))
        self.set_output_status(
            "OUT",
            self.worst_input_status("ADVISORY", "COMMFAIL", "FAILED",
                                    "MAINT"),
            LimitStatus.NOT_LIMITED)


# ═══════════════════════════════════════════════════════════════════════
#  INSPECT — utilization and device-alert statistics
# ═══════════════════════════════════════════════════════════════════════
@register_block
class InspectBlock(FunctionBlock):
    """Inspect — the trainer's Inspect server, scoped to what it can see.

    Counts device alerts by decoding every DIAG block in scope (this
    module; every on-scan module when SYSTEM) and reports UTILIZATION as
    the running fraction of mode-carrying blocks found in their configured
    normal mode. PROCESS is PROCESS_IN averaged over the TIME_WINDOW
    (first-order, time-constant = the window) — the spec's "no algorithm,
    the server computes" adapted to a trainer that has no server.
    """

    block_type = "INSPECT"
    category = BlockCategory.APC
    display_name = "Inspect (INSPECT)"
    description = "Device-alert counts, utilization, windowed average"

    config_choices = {"TIME_WINDOW": ("HOUR", "SHIFT", "DAY")}
    _WINDOW_S = {"HOUR": 3600.0, "SHIFT": 28800.0, "DAY": 86400.0}

    def __init__(self, instance_name: str = ""):
        self._process_avg = 0.0
        self._utilization = 100.0
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("ENABLED", DataType.BOOL, True,
                       "False = Inspect processing disabled")
        self.add_input("PROCESS_IN", description="Value averaged into "
                                                 "PROCESS")
        self.add_output("DEVICES", DataType.FLOAT, 0.0,
                        "DIAG blocks monitored")
        for name in ("ADVISORY", "COMMFAIL", "FAILED", "MAINT"):
            self.add_output(name, DataType.FLOAT, 0.0,
                            f"Devices with the {name} flag set")
        self.add_output("UTILIZATION", DataType.FLOAT, 100.0,
                        "% of mode-carrying blocks in their normal mode")
        self.add_output("PROCESS", DataType.FLOAT, 0.0,
                        "PROCESS_IN averaged over TIME_WINDOW")

    def get_config_schema(self):
        return {
            "SYSTEM": (bool, False,
                       "True = every on-scan module; False = this module"),
            "TIME_WINDOW": (str, "HOUR",
                            "Averaging window for PROCESS / UTILIZATION"),
            "PROCESS_LABEL": (str, "", "Label for the PROCESS value"),
        }

    def reset(self):
        super().reset()
        self._process_avg = 0.0
        self._utilization = 100.0

    def _graphs_in_scope(self) -> list:
        own = getattr(self, "_module_graph", None)
        if not bool(self.config.params.get("SYSTEM", False)):
            return [own] if own is not None else []
        graphs = [own] if own is not None else []
        context = getattr(self, "runtime_context", None)
        store = getattr(context, "store", None)
        try:
            for runtime in store.get_strategy_runtimes():
                graph = getattr(getattr(runtime, "compiled", None),
                                "graph", None)
                if graph is not None and graph not in graphs:
                    graphs.append(graph)
        except Exception:                           # noqa: BLE001
            pass
        return graphs

    def execute(self, dt: float):
        if not bool(self.get_input("ENABLED")):
            return
        p = self.config.params
        window = self._WINDOW_S.get(
            str(p.get("TIME_WINDOW", "HOUR")).upper(), 3600.0)
        # First-order average: over one window a step settles ~63 %,
        # which is what a rolling window feels like without a day-long
        # sample buffer per block.
        alpha = min(1.0, dt / max(dt, window))

        devices = advisory = commfail = failed = maint = 0
        normal = carrying = 0
        for graph in self._graphs_in_scope():
            for block in graph.blocks.values():
                if block.block_type == "DIAG":
                    devices += 1
                    word = int(block.get_output("OUT") or 0)
                    advisory += bool(word & DiagnosticBlock.ADVISORY)
                    commfail += bool(word & DiagnosticBlock.COMMFAIL)
                    failed += bool(word & DiagnosticBlock.FAILED)
                    maint += bool(word & DiagnosticBlock.MAINT)
                mode_out = block.outputs.get("MODE")
                if mode_out is not None:
                    carrying += 1
                    configured = str(block.config.params.get(
                        "mode", "AUTO")).upper()
                    actual = str(mode_out.value or configured).upper()
                    normal += configured in actual or actual in configured
        self.set_output("DEVICES", float(devices))
        self.set_output("ADVISORY", float(advisory))
        self.set_output("COMMFAIL", float(commfail))
        self.set_output("FAILED", float(failed))
        self.set_output("MAINT", float(maint))

        if carrying:
            sample = 100.0 * normal / carrying
            self._utilization += alpha * (sample - self._utilization)
        self.set_output("UTILIZATION", self._utilization)

        value = self.get_input("PROCESS_IN")
        if value is not None:
            self._process_avg += alpha * (float(value) - self._process_avg)
            self.set_output("PROCESS", self._process_avg)
            self.set_output_status("PROCESS",
                                   self.input_status("PROCESS_IN"),
                                   LimitStatus.NOT_LIMITED)


# ═══════════════════════════════════════════════════════════════════════
#  FLC — Fuzzy Logic Control
# ═══════════════════════════════════════════════════════════════════════
@register_block
class FuzzyLogicControlBlock(FunctionBlock):
    """Fuzzy Logic Control — the fixed-rule nonlinear PI, per the spec.

    Per scan: the error (``PV − SP`` for the reverse-acting table) and the
    change in error are clipped at their scaling factors ``SF_ERROR`` /
    ``SF_DELTERR``, fuzzified into the two membership functions each
    (Negative / Positive), pushed through the four fixed rules, and
    defuzzified against the three output singletons into an *increment*
    scaled by ``SF_OUTPUT``. Incremental form is what makes the block
    naturally bumpless and windup-free: a clamped OUT simply stops
    integrating.

    SP-change adaptation is the spec's: an SP step larger than
    ``SP_FACTOR`` percent of the PV span scales the factors up in the
    ratio of the step to the nominal 1 % step, and the larger factors
    stay in force until the error has stayed inside SF_ERROR again.
    """

    block_type = "FLC"
    category = BlockCategory.APC
    display_name = "Fuzzy Logic Control (FLC)"
    description = "Fixed-rule fuzzy nonlinear PI — a PID drop-in for "\
                  "single loops"

    config_choices = {"mode": ("AUTO", "MAN", "OOS"),
                      "ACTION": ("REVERSE", "DIRECT")}
    config_units = {"SP_FACTOR": "%"}

    #: Nominal SP change, percent of span (the spec's ΔYsp).
    _NOMINAL_SP_PCT = 1.0

    def __init__(self, instance_name: str = ""):
        self._e_prev: float | None = None
        self._out = 0.0
        self._sp_prev: float | None = None
        self._adapt = 1.0
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN", description="Process variable (PV)")
        self.add_input("SP", description="Setpoint")
        self.add_input("TRK_IN_D", DataType.BOOL, False,
                       "True = OUT tracks TRK_VAL")
        self.add_input("TRK_VAL", description="Tracking value")
        self.add_output("OUT", DataType.FLOAT, 0.0, "Controller output")
        self.add_output("PV", DataType.FLOAT, 0.0, "Filon the block's PV")
        self.add_output("ERROR", DataType.FLOAT, 0.0, "SP − PV")
        self.add_output("MODE", DataType.STRING, "AUTO", "Actual mode")
        self.add_output("BKCAL_OUT", DataType.FLOAT, 0.0,
                        "For an upstream block's BKCAL_IN", is_bkcal=True)

    def get_config_schema(self):
        return {
            "SF_ERROR": (float, 10.0,
                         "Se — error scaling factor [EU]; clips |PV−SP|"),
            "SF_DELTERR": (float, 2.0,
                           "SΔe — change-in-error scaling factor [EU]"),
            "SF_OUTPUT": (float, 2.0,
                          "SΔu — output-increment scaling factor "
                          "[EU of OUT per scan at full membership]"),
            "SP_FACTOR": (float, 1.0,
                          "Min SP change [% of PV span] that triggers "
                          "scaling-factor adaptation"),
            "PV_LO": (float, 0.0, "PV scale low [EU]"),
            "PV_HI": (float, 100.0, "PV scale high [EU]"),
            "OUT_LO_LIM": (float, 0.0, "Minimum output"),
            "OUT_HI_LIM": (float, 100.0, "Maximum output"),
            "SP_LO_LIM": (float, 0.0, "Lowest allowed SP"),
            "SP_HI_LIM": (float, 100.0, "Highest allowed SP"),
            "ACTION": (str, "REVERSE",
                       "REVERSE (OUT rises when PV < SP) or DIRECT"),
            "mode": (str, "AUTO", "Block mode"),
            "normal_mode": (str, "", "Normal mode — what this block SHOULD normally be in. Blank means 'same as the initial mode'. Azeo lights the abnormal-mode icon when actual differs from NORMAL or from target; without this only the target half is answerable."),
        }

    def _apply_config(self):
        """Publish the two scales consumed by the shared Loop_fp body."""
        p = self.config.params
        pv_range = (float(p.get("PV_LO", 0.0)),
                    float(p.get("PV_HI", 100.0)))
        out_range = (float(p.get("OUT_LO_LIM", 0.0)),
                     float(p.get("OUT_HI_LIM", 100.0)))
        for name in ("IN", "SP", "PV"):
            self.set_terminal_eu(name, "", pv_range)
        self.set_terminal_eu("OUT", "%", out_range)

    @property
    def mode_target(self) -> str:
        return str(self.config.params.get("mode", "AUTO")).upper()

    @property
    def mode_actual(self) -> str:
        return str(self.outputs["MODE"].value or self.mode_target).upper()

    def set_mode(self, mode: str) -> None:
        value = str(mode).upper()
        if value not in self.config_choices["mode"]:
            raise ValueError("mode must be AUTO, MAN or OOS")
        self.config.params["mode"] = value

    def accepts_operator_write(self, what: str = "SP") -> tuple[bool, str]:
        name = str(what).upper()
        target = self.mode_target
        if name == "SP":
            if self.inputs["SP"].connected:
                return False, "SP is wired and owned by upstream control"
            allowed = target in ("AUTO", "MAN")
            return allowed, "" if allowed else f"SP is not operator-owned in {target}"
        if name in ("OUT", "OP"):
            allowed = target in ("MAN", "OOS")
            return allowed, "" if allowed else f"OUT is owned by the block in {target}"
        return False, f"{name} has no FLC operator handle"

    def write_operator_value(self, value: float,
                             what: str = "SP") -> tuple[bool, str]:
        allowed, why = self.accepts_operator_write(what)
        if not allowed:
            return False, why
        if str(what).upper() == "SP":
            self.inputs["SP"].value = float(value)
        else:
            lo = float(self.config.params.get("OUT_LO_LIM", 0.0))
            hi = float(self.config.params.get("OUT_HI_LIM", 100.0))
            self._out = max(lo, min(hi, float(value)))
            self.set_output("OUT", self._out)
        return True, ""

    def reset(self):
        super().reset()
        self._e_prev = None
        self._sp_prev = None
        self._adapt = 1.0
        self._out = float(self.config.params.get("OUT_LO_LIM", 0.0))

    def execute(self, dt: float):
        p = self.config.params
        mode = str(p.get("mode", "AUTO")).upper()
        self.set_output("MODE", mode)
        if mode == "OOS":
            self.set_output_status("OUT", Quality.BAD,
                                   LimitStatus.NOT_LIMITED)
            self.status = BlockStatus.BAD
            return

        pv = float(self.get_input("IN") or 0.0)
        sp = max(float(p.get("SP_LO_LIM", 0.0)),
                 min(float(p.get("SP_HI_LIM", 100.0)),
                     float(self.get_input("SP") or 0.0)))
        self.set_output("PV", pv)
        self.set_output("ERROR", sp - pv)
        quality = self.input_status("IN")
        self.set_output_status("PV", quality, LimitStatus.NOT_LIMITED)

        out_lo = float(p.get("OUT_LO_LIM", 0.0))
        out_hi = float(p.get("OUT_HI_LIM", 100.0))

        if bool(self.get_input("TRK_IN_D")):
            # LO — track function active: OUT follows TRK_VAL.
            self._out = max(out_lo, min(out_hi, float(
                self.get_input("TRK_VAL") or self._out)))
            self.set_output("MODE", "LO")
        elif mode == "MAN":
            # Operator owns OUT: hold, and re-seat internal state on it so
            # the return to Auto is bumpless (incremental form).
            self._out = max(out_lo, min(out_hi,
                                        float(self.get_output("OUT")
                                              or self._out)))
            self._e_prev = None
        else:
            span = max(1e-9, float(p.get("PV_HI", 100.0))
                       - float(p.get("PV_LO", 0.0)))
            # SP-change adaptation (spec): a step beyond SP_FACTOR % of
            # span raises the factors in the ratio step / nominal-1%.
            if self._sp_prev is not None:
                step_pct = abs(sp - self._sp_prev) / span * 100.0
                if step_pct > float(p.get("SP_FACTOR", 1.0)):
                    self._adapt = max(self._adapt,
                                      step_pct / self._NOMINAL_SP_PCT)
            error = pv - sp                     # the reverse-acting table
            se = max(1e-9, float(p.get("SF_ERROR", 10.0))) * self._adapt
            sde = max(1e-9, float(p.get("SF_DELTERR", 2.0))) * self._adapt
            sdu = max(0.0, float(p.get("SF_OUTPUT", 2.0))) * self._adapt
            if abs(error) <= max(1e-9, float(p.get("SF_ERROR", 10.0))):
                self._adapt = 1.0               # error small again: revert

            if self._e_prev is None:
                delta_e = 0.0
            else:
                delta_e = error - self._e_prev
            self._e_prev = error

            e_n = max(-1.0, min(1.0, error / se))
            de_n = max(-1.0, min(1.0, delta_e / sde))
            # Membership: Negative / Positive partition over [-1, 1].
            e_neg, e_pos = (1.0 - e_n) / 2.0, (1.0 + e_n) / 2.0
            de_neg, de_pos = (1.0 - de_n) / 2.0, (1.0 + de_n) / 2.0
            # The four fixed rules (reverse acting), AND = min:
            #   e N ∧ Δe N → P;  e N ∧ Δe P → ZO;
            #   e P ∧ Δe N → ZO; e P ∧ Δe P → N.
            rule_p = min(e_neg, de_neg)
            rule_z1 = min(e_neg, de_pos)
            rule_z2 = min(e_pos, de_neg)
            rule_n = min(e_pos, de_pos)
            total = rule_p + rule_z1 + rule_z2 + rule_n
            du_norm = (rule_p - rule_n) / total if total > 0 else 0.0
            delta_u = sdu * du_norm
            if str(p.get("ACTION", "REVERSE")).upper() == "DIRECT":
                delta_u = -delta_u
            self._out = max(out_lo, min(out_hi, self._out + delta_u))

        self.set_output("OUT", self._out)
        limit = (LimitStatus.HIGH_LIMITED if self._out >= out_hi else
                 LimitStatus.LOW_LIMITED if self._out <= out_lo else
                 LimitStatus.NOT_LIMITED)
        self.set_output_status("OUT", quality, limit)
        self.set_output("BKCAL_OUT", self._out)
        self.set_output_status("BKCAL_OUT", quality, limit)
