"""Control blocks — Splitter, OnOff, RampSoak, GainScheduler."""
from __future__ import annotations
from ..model.block_base import FunctionBlock, BlockCategory, DataType
from ..model.block_registry import register_block


@register_block
class SplitterBlock(FunctionBlock):
    """Split-range output (Honeywell SPLRNG).

    Maps a single 0-100% input to two outputs with configurable breakpoints.
    Example: 0-50% → OUT1 (100-0%), 50-100% → OUT2 (0-100%)
    Used for split-range valve control (heating/cooling).
    """
    block_type = "SPLITTER"
    category = BlockCategory.CONTROL
    display_name = "Split Range"
    description = "Split-range: 1 input → 2 outputs with configurable breakpoints"

    def _define_terminals(self):
        self.add_input("IN", description="Controller output (0-100%)")
        self.add_output("OUT1", description="Output 1 (e.g. heating valve)")
        self.add_output("OUT2", description="Output 2 (e.g. cooling valve)")

    def get_config_schema(self):
        return {
            "SPLIT_PT": (float, 50.0, "Split point (% of input range)"),
            "OUT1_LO": (float, 0.0, "OUT1 at IN=SPLIT_PT"),
            "OUT1_HI": (float, 100.0, "OUT1 at IN=0"),
            "OUT2_LO": (float, 0.0, "OUT2 at IN=SPLIT_PT"),
            "OUT2_HI": (float, 100.0, "OUT2 at IN=100"),
            "REVERSE_1": (bool, True, "OUT1 reverse acting (decreases as IN increases)"),
            "REVERSE_2": (bool, False, "OUT2 reverse acting"),
        }

    def execute(self, dt: float):
        p = self.config.params
        inp = self.get_input("IN")
        split = p.get("SPLIT_PT", 50.0)
        out1_lo = p.get("OUT1_LO", 0.0)
        out1_hi = p.get("OUT1_HI", 100.0)
        out2_lo = p.get("OUT2_LO", 0.0)
        out2_hi = p.get("OUT2_HI", 100.0)

        if inp <= split:
            # Below split: OUT1 active, OUT2 at minimum
            frac = inp / max(split, 1e-6)
            if p.get("REVERSE_1", True):
                out1 = out1_hi - frac * (out1_hi - out1_lo)
            else:
                out1 = out1_lo + frac * (out1_hi - out1_lo)
            out2 = out2_lo
        else:
            # Above split: OUT1 at minimum, OUT2 active
            frac = (inp - split) / max(100.0 - split, 1e-6)
            out1 = out1_lo if p.get("REVERSE_1", True) else out1_hi
            if p.get("REVERSE_2", False):
                out2 = out2_hi - frac * (out2_hi - out2_lo)
            else:
                out2 = out2_lo + frac * (out2_hi - out2_lo)

        self.set_output("OUT1", max(min(out1, max(out1_lo, out1_hi)), min(out1_lo, out1_hi)))
        self.set_output("OUT2", max(min(out2, max(out2_lo, out2_hi)), min(out2_lo, out2_hi)))


@register_block
class OnOffBlock(FunctionBlock):
    """On/Off controller with deadband (Honeywell ONOFFC).

    Simple thermostat-style control. Output goes high when PV < SP - DB/2,
    goes low when PV > SP + DB/2.
    """
    block_type = "ONOFF"
    category = BlockCategory.CONTROL
    display_name = "On/Off Controller"
    description = "On/Off controller with deadband (thermostat)"

    def __init__(self, instance_name=""):
        self._state = False
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("PV", description="Process variable")
        self.add_input("SP", description="Setpoint")
        self.add_output("OUT", DataType.BOOL, False, "Control output")
        self.add_output("OUT_FLOAT", description="Control output (0.0 or 100.0)")

    def get_config_schema(self):
        return {
            "DEADBAND": (float, 1.0, "Hysteresis deadband"),
            "REVERSE": (bool, False, "Reverse acting (cooling)"),
        }

    def execute(self, dt: float):
        pv = self.get_input("PV")
        sp = self.get_input("SP")
        db = self.config.params.get("DEADBAND", 1.0)
        reverse = self.config.params.get("REVERSE", False)

        half_db = db / 2.0
        if not reverse:
            # Direct: heat when PV too low
            if pv < sp - half_db:
                self._state = True
            elif pv > sp + half_db:
                self._state = False
        else:
            # Reverse: cool when PV too high
            if pv > sp + half_db:
                self._state = True
            elif pv < sp - half_db:
                self._state = False

        self.set_output("OUT", self._state)
        self.set_output("OUT_FLOAT", 100.0 if self._state else 0.0)

    def reset(self):
        super().reset()
        self._state = False


@register_block
class RampSoakBlock(FunctionBlock):
    """Ramp/Soak profile controller (Honeywell RMPSOAKC).

    Executes a configurable temperature/setpoint profile with alternating
    ramp and soak segments. Up to 8 ramp-soak pairs.
    """
    block_type = "RAMP_SOAK"
    category = BlockCategory.CONTROL
    display_name = "Ramp/Soak"
    description = "Profile controller with up to 8 ramp-soak segments"

    def __init__(self, instance_name=""):
        self._current_value = 0.0
        self._segment = 0          # 0-based: 0=ramp1, 1=soak1, 2=ramp2, ...
        self._segment_elapsed = 0.0
        self._running = False
        self._complete = False
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("START", DataType.BOOL, False, "Start profile")
        self.add_input("STOP", DataType.BOOL, False, "Stop/hold profile")
        self.add_input("RESET", DataType.BOOL, False, "Reset to beginning")
        self.add_output("OUT", description="Current setpoint output")
        self.add_output("SEGMENT", DataType.INT, 0, "Current segment number")
        self.add_output("RUNNING", DataType.BOOL, False, "Profile is running")
        self.add_output("COMPLETE", DataType.BOOL, False, "Profile complete")

    def get_config_schema(self):
        return {
            "START_VALUE": (float, 25.0, "Starting value"),
            "N_SEGMENTS": (int, 3, "Number of ramp-soak pairs (1-8)"),
            # Segment 1
            "TARGET_1": (float, 100.0, "Target value after ramp 1"),
            "RAMP_RATE_1": (float, 2.0, "Ramp rate 1 [units/min]"),
            "SOAK_TIME_1": (float, 60.0, "Soak time 1 [s]"),
            # Segment 2
            "TARGET_2": (float, 200.0, "Target value after ramp 2"),
            "RAMP_RATE_2": (float, 5.0, "Ramp rate 2 [units/min]"),
            "SOAK_TIME_2": (float, 120.0, "Soak time 2 [s]"),
            # Segment 3
            "TARGET_3": (float, 25.0, "Target value after ramp 3"),
            "RAMP_RATE_3": (float, 3.0, "Ramp rate 3 [units/min]"),
            "SOAK_TIME_3": (float, 60.0, "Soak time 3 [s]"),
            # Segments 4-8 (defaults to 0 = unused)
            "TARGET_4": (float, 0.0, "Target 4"), "RAMP_RATE_4": (float, 1.0, "Rate 4"), "SOAK_TIME_4": (float, 0.0, "Soak 4"),
            "TARGET_5": (float, 0.0, "Target 5"), "RAMP_RATE_5": (float, 1.0, "Rate 5"), "SOAK_TIME_5": (float, 0.0, "Soak 5"),
            "TARGET_6": (float, 0.0, "Target 6"), "RAMP_RATE_6": (float, 1.0, "Rate 6"), "SOAK_TIME_6": (float, 0.0, "Soak 6"),
            "TARGET_7": (float, 0.0, "Target 7"), "RAMP_RATE_7": (float, 1.0, "Rate 7"), "SOAK_TIME_7": (float, 0.0, "Soak 7"),
            "TARGET_8": (float, 0.0, "Target 8"), "RAMP_RATE_8": (float, 1.0, "Rate 8"), "SOAK_TIME_8": (float, 0.0, "Soak 8"),
        }

    def execute(self, dt: float):
        p = self.config.params

        if self.get_input("RESET"):
            self._segment = 0
            self._segment_elapsed = 0.0
            self._running = False
            self._complete = False
            self._current_value = p.get("START_VALUE", 25.0)

        if self.get_input("START") and not self._running and not self._complete:
            self._running = True
            if self._segment == 0 and self._segment_elapsed == 0.0:
                self._current_value = p.get("START_VALUE", 25.0)

        if self.get_input("STOP"):
            self._running = False

        n_seg = min(max(int(p.get("N_SEGMENTS", 3)), 1), 8)
        total_phases = n_seg * 2  # ramp + soak per segment

        if self._running and not self._complete:
            seg_pair = self._segment // 2  # which ramp-soak pair (0-based)
            is_ramp = (self._segment % 2) == 0

            if seg_pair < n_seg:
                target = p.get(f"TARGET_{seg_pair + 1}", 0.0)
                rate = max(p.get(f"RAMP_RATE_{seg_pair + 1}", 1.0), 0.001)  # units/min
                soak = p.get(f"SOAK_TIME_{seg_pair + 1}", 60.0)

                if is_ramp:
                    # Ramp phase: move toward target at rate
                    rate_per_sec = rate / 60.0
                    delta = target - self._current_value
                    max_change = rate_per_sec * dt
                    if abs(delta) <= max_change:
                        self._current_value = target
                        self._segment += 1
                        self._segment_elapsed = 0.0
                    else:
                        self._current_value += max_change if delta > 0 else -max_change
                else:
                    # Soak phase: hold at current value
                    self._segment_elapsed += dt
                    if self._segment_elapsed >= soak:
                        self._segment += 1
                        self._segment_elapsed = 0.0

            if self._segment >= total_phases:
                self._complete = True
                self._running = False

        self.set_output("OUT", self._current_value)
        self.set_output("SEGMENT", self._segment // 2 + 1)
        self.set_output("RUNNING", self._running)
        self.set_output("COMPLETE", self._complete)

    def reset(self):
        super().reset()
        self._current_value = 0.0
        self._segment = 0
        self._segment_elapsed = 0.0
        self._running = False
        self._complete = False


@register_block
class GainSchedBlock(FunctionBlock):
    """Gain scheduler (Honeywell PIDEX/ADAPT equivalent).

    Outputs interpolated Kp, Ti, Td values based on a scheduling variable.
    Wire outputs to PID config or use with external PID tuning writes.
    Up to 5 breakpoints.
    """
    block_type = "GAIN_SCHED"
    category = BlockCategory.CONTROL
    display_name = "Gain Scheduler"
    description = "PV-indexed gain scheduling table (Kp/Ti/Td interpolation)"

    def _define_terminals(self):
        self.add_input("SCHED_VAR", description="Scheduling variable (e.g. PV, load)")
        self.add_output("KP", description="Interpolated proportional gain")
        self.add_output("TI", description="Interpolated integral time [s]")
        self.add_output("TD", description="Interpolated derivative time [s]")
        self.add_output("REGION", DataType.INT, 1, "Active region index")

    def get_config_schema(self):
        return {
            "N_POINTS": (int, 3, "Number of breakpoints (2-5)"),
            "X_1": (float, 0.0, "Breakpoint 1 (scheduling var)"),
            "KP_1": (float, 1.0, "Kp at breakpoint 1"),
            "TI_1": (float, 60.0, "Ti at breakpoint 1 [s]"),
            "TD_1": (float, 0.0, "Td at breakpoint 1 [s]"),
            "X_2": (float, 50.0, "Breakpoint 2"),
            "KP_2": (float, 2.0, "Kp at breakpoint 2"),
            "TI_2": (float, 30.0, "Ti at breakpoint 2 [s]"),
            "TD_2": (float, 0.0, "Td at breakpoint 2 [s]"),
            "X_3": (float, 100.0, "Breakpoint 3"),
            "KP_3": (float, 0.5, "Kp at breakpoint 3"),
            "TI_3": (float, 120.0, "Ti at breakpoint 3 [s]"),
            "TD_3": (float, 0.0, "Td at breakpoint 3 [s]"),
            "X_4": (float, 150.0, "Breakpoint 4"), "KP_4": (float, 1.0, "Kp 4"), "TI_4": (float, 60.0, "Ti 4"), "TD_4": (float, 0.0, "Td 4"),
            "X_5": (float, 200.0, "Breakpoint 5"), "KP_5": (float, 1.0, "Kp 5"), "TI_5": (float, 60.0, "Ti 5"), "TD_5": (float, 0.0, "Td 5"),
        }

    def execute(self, dt: float):
        p = self.config.params
        n = min(max(int(p.get("N_POINTS", 3)), 2), 5)
        x_pts = [p.get(f"X_{i+1}", 0.0) for i in range(n)]
        kp_pts = [p.get(f"KP_{i+1}", 1.0) for i in range(n)]
        ti_pts = [p.get(f"TI_{i+1}", 60.0) for i in range(n)]
        td_pts = [p.get(f"TD_{i+1}", 0.0) for i in range(n)]

        sv = self.get_input("SCHED_VAR")

        # Clamp and interpolate
        if sv <= x_pts[0]:
            kp, ti, td, region = kp_pts[0], ti_pts[0], td_pts[0], 1
        elif sv >= x_pts[-1]:
            kp, ti, td, region = kp_pts[-1], ti_pts[-1], td_pts[-1], n
        else:
            for i in range(n - 1):
                if x_pts[i] <= sv <= x_pts[i + 1]:
                    span = x_pts[i + 1] - x_pts[i]
                    frac = (sv - x_pts[i]) / max(span, 1e-12)
                    kp = kp_pts[i] + frac * (kp_pts[i + 1] - kp_pts[i])
                    ti = ti_pts[i] + frac * (ti_pts[i + 1] - ti_pts[i])
                    td = td_pts[i] + frac * (td_pts[i + 1] - td_pts[i])
                    region = i + 1
                    break
            else:
                kp, ti, td, region = kp_pts[0], ti_pts[0], td_pts[0], 1

        self.set_output("KP", kp)
        self.set_output("TI", ti)
        self.set_output("TD", td)
        self.set_output("REGION", region)
