"""U100 - feed surge drum D1, charge pumps and suction MOVs.

This unit carries the whole discrete pattern: two pumps in a duty-standby pair,
each behind a control-room operable motor operated valve, with minimum flow
protection and a field manual drain valve that only exists on the simulator HMI.

Hydraulics
----------
Flow is solved explicitly from the previous step's value rather than by an
algebraic loop:

1. the pump develops head at last step's flow;
2. that gives a discharge pressure;
3. the control valve passes flow at the resulting differential;
4. the result is passed through a short lag.

The lag is what makes it stable. An implicit solve would be marginally more
accurate and considerably more likely to oscillate at large step sizes, which is
the wrong trade for a training simulator.

The interlock and the physics are deliberately independent. ``MC-P101A`` in
The controller should refuse to start against a shut suction valve, but if that interlock
is defeated the pump still cavitates here, because suction pressure is computed
from the actual valve opening.
"""

from __future__ import annotations

import logging

from ..core.devices import (CentrifugalPump, ControlValve, Motor,
                            MotorOperatedValve, Transmitter, TxFailure, ValveChar)
from ..core.dynamics import Integrator, Lag, clamp, safe_div
from ..core.tags import Quality
from .base import ProcessUnit

log = logging.getLogger(__name__)


class FeedSection(ProcessUnit):
    code = "U100"
    name = "Feed surge drum D1 and charge pumps"

    D1_AREA = 37.0            # m2, the design residence time at 10,000 bpd
    D1_HEIGHT = 5.4           # m, so 200 m3 total
    DENSITY = 780.0           # kg/m3
    VAPOUR_PRESSURE = 0.35    # barg at operating temperature
    STATIC_HEAD = 0.25        # bar from drum liquid level to pump suction
    PARAMETER_META = {
        "D1_AREA": {"eu": "m2", "description": "D1 horizontal cross-sectional area",
                    "lo": 0.1, "hi": 1000.0},
        "D1_HEIGHT": {"eu": "m", "description": "D1 effective liquid height",
                      "lo": 0.1, "hi": 50.0},
        "DENSITY": {"eu": "kg/m3", "description": "Charge liquid reference density",
                    "lo": 100.0, "hi": 2000.0},
        "VAPOUR_PRESSURE": {"eu": "barg", "description": "Charge vapour pressure at operating temperature",
                            "lo": -1.0, "hi": 20.0},
        "STATIC_HEAD": {"eu": "bar", "description": "D1 liquid static head at pump suction",
                        "lo": 0.0, "hi": 20.0},
    }

    def build(self) -> None:
        # ------------------------------------------------------------- outputs
        self.LT1001 = self.ai("LT-1001", "D1 feed surge drum level", "%", 0, 100, 55.0)
        self.PT1001 = self.ai("PT-1001", "D1 vapour space pressure", "barg", 0, 10, 2.5)
        self.PT1002 = self.ai("PT-1002", "P-101 discharge header pressure", "barg", 0, 30)
        self.PT1003 = self.ai("PT-1003", "P-101A suction pressure", "barg", 0, 10)
        self.PT1004 = self.ai("PT-1004", "P-101B suction pressure", "barg", 0, 10)
        self.PDT1001 = self.ai("PDT-1001", "P-101A suction strainer differential", "bar", 0, 2)
        self.TT1001 = self.ai("TT-1001", "D1 outlet temperature", "degC", 0, 200, 62.0)
        self.TT1002 = self.ai("TT-1002", "P-101A bearing temperature", "degC", 0, 150, 45.0)
        self.FT1001 = self.ai("FT-1001", "Charge flow from D1", "m3/h", 0, 300)
        self.FT1002 = self.ai("FT-1002", "T1 bottoms recycle to D1", "m3/h", 0, 180)
        self.FT1003 = self.ai("FT-1003", "P-101 minimum flow recycle", "m3/h", 0, 60)
        # the battery-limit metering station: what the refinery is charged
        # for and what the columns will have to separate
        self.FT1004 = self.ai("FT-1004", "Fresh feed flow from battery limit", "m3/h", 0, 150, 66.2)
        self.PT1005 = self.ai("PT-1005", "Fresh feed supply pressure", "barg", 0, 10, 6.0)
        self.TT1003 = self.ai("TT-1003", "Fresh feed temperature", "degC", 0, 100, 38.0)
        self.AT1001 = self.ai("AT-1001", "Fresh feed light key content", "mol%", 0, 50, 12.0)
        self.ST1001 = self.ai("ST-1001", "P-101A speed feedback", "%", 0, 100)
        self.IT1001 = self.ai("IT-1001", "P-101A motor current", "A", 0, 200)
        self.IT1002 = self.ai("IT-1002", "P-101B motor current", "A", 0, 200)
        self.VT1001 = self.ai("VT-1001", "P-101A vibration", "micron", 0, 100, 12.0)
        self.DT1001 = self.ai("DT-1001", "Charge density", "kg/m3", 600, 900, self.DENSITY)
        self.ZT1001 = self.ai("ZT-1001", "FCV-1001 position feedback", "%", 0, 100)

        # -------------------------------------------------------------- inputs
        self.ao("FCV-1001", "D1 charge flow control valve", value=68.0)
        self.ao("FCV-1002", "P-101 minimum flow recycle valve", value=0.0)
        self.ao("LCV-1001", "D1 off-specification import valve")
        self.ao("SC-1001", "P-101A VFD speed reference", value=100.0)

        # ------------------------------------------------------- discrete pairs
        self._add_pump_io("P101A", "P-101A", vfd=True)
        self._add_pump_io("P101B", "P-101B", vfd=False)
        self._add_mov_io("MOV1001A", "MOV-1001A")
        self._add_mov_io("MOV1001B", "MOV-1001B")

        self.di("ZSO-HV1001", "HV-1001 D1 drain manual valve open limit",
                "Not open", "Open")
        self.di("ZSC-HV1001", "HV-1001 D1 drain manual valve closed limit",
                "Not closed", "Closed", value=True)
        self.di("LSLL-1001", "D1 level low low", "Normal", "Tripped")
        self.di("LSHH-1001", "D1 level high high", "Normal", "Tripped")
        self.di("ZSO-XV1001", "XV-1001 charge SDV open limit", "Not open", "Open",
                value=True)
        self.di("ZSC-XV1001", "XV-1001 charge SDV closed limit", "Not closed", "Closed")
        self.do("XY-XV1001-OPN", "XV-1001 charge SDV open command", "Close", "Open",
                value=True)

        # ----------------------------------------------------------- equipment
        self.mov_a = MotorOperatedValve("MOV-1001A", travel_time=30.0, position=100.0)
        self.mov_b = MotorOperatedValve("MOV-1001B", travel_time=30.0, position=0.0)
        from .packages import MovPackage
        self.mov_recycle = MovPackage(self, "MOV-1002",
                                      "D1 recycle inlet isolation",
                                      travel_time=25.0, position=100.0)
        self.di("ZSO-HV1002", "HV-1002 open limit switch",
                "Not open", "Open", value=False)
        self.di("ZSC-HV1002", "HV-1002 closed limit switch",
                "Not closed", "Closed", value=True)
        self.motor_a = Motor("P-101A", rated_current=165.0, vfd=True)
        self.motor_b = Motor("P-101B", rated_current=165.0, vfd=False)
        self.pump_a = CentrifugalPump("P-101A", head_shutoff=280.0, flow_max=360.0,
                                      flow_min=25.0)
        self.pump_b = CentrifugalPump("P-101B", head_shutoff=280.0, flow_max=360.0,
                                      flow_min=25.0)
        self.fcv1001 = ControlValve("FCV-1001", cv_rated=260,
                                    char=ValveChar.EQUAL_PERCENT, stroke_time=6)
        self.fcv1002 = ControlValve("FCV-1002", cv_rated=70, char=ValveChar.LINEAR,
                                    stroke_time=4, fail_closed=False)
        self.lcv1001 = ControlValve("LCV-1001", cv_rated=90, char=ValveChar.LINEAR,
                                    stroke_time=6)
        self.xv1001_open = True
        self.hv1001_position = 0.0        # field manual drain, simulator HMI only

        # -------------------------------------------------------------- states
        self.level = Integrator(y0=55.0, lo=0.0, hi=100.0)
        self.charge_flow = Lag(1.5, 0.0)
        self.minflow = Lag(1.2, 0.0)
        self.strainer_dp = 0.05
        self.local_a = False        # local/remote selectors, set from field ops
        self.local_b = False
        self.mov_a_local = False
        self.mov_b_local = False
        # Battery-limit feed. Sized so fresh + T1 bottoms recycle lands
        # the charge near its proven ~95 m3/h operating point: the
        # recycle loop gain is about 0.86 (D3 liquid split) x 0.60 (T1
        # bottoms fraction at the 0.40 draw), so charge = 2.07 x fresh.
        self.fresh_feed = 66.2            # m3/h, 10,000 bpd; instructor adjustable
        # The feed's other boundary conditions, all instructor adjustable.
        # The light key already in the feed (a fraction) is what the
        # reactor adds to; U400 reads it from the bus.
        self.fresh_temperature = 38.0     # degC
        self.fresh_pressure = 6.0         # barg at the battery limit
        self.fresh_lightfrac = 0.12       # light key fraction in the fresh feed
        self.bearing_temp = Lag(120.0, 45.0)

        # --------------------------------------------------------- transmitters
        self.tx_level = Transmitter("LT-1001", 0, 100, tau=1.5, noise_sigma_pct=0.25)
        self.tx_flow = Transmitter("FT-1001", 0, 200, tau=0.6, noise_sigma_pct=0.45)
        self.tx_fresh = Transmitter("FT-1004", 0, 150, tau=0.6, noise_sigma_pct=0.45)
        self.tx_fresh_p = Transmitter("PT-1005", 0, 10, tau=0.5, noise_sigma_pct=0.3)
        self.tx_fresh_t = Transmitter("TT-1003", 0, 100, tau=2.0, noise_sigma_pct=0.2)
        self.tx_fresh_z = Transmitter("AT-1001", 0, 50, tau=30.0, noise_sigma_pct=0.5)
        self.tx_pdis = Transmitter("PT-1002", 0, 30, tau=0.4, noise_sigma_pct=0.2)
        self.tx_psuc_a = Transmitter("PT-1003", 0, 10, tau=0.4, noise_sigma_pct=0.2)

        self._build_malfunctions()

    # ------------------------------------------------------------------ tag I/O
    def _add_pump_io(self, base: str, tag: str, vfd: bool) -> None:
        self.di(f"XS-{base}-RUN", f"{tag} running feedback", "Stopped", "Running")
        self.di(f"XS-{base}-FLT", f"{tag} fault or trip", "Healthy", "Faulted")
        self.di(f"XS-{base}-AVL", f"{tag} available and in remote",
                "Not avail", "Available", value=True)
        if vfd:
            self.di(f"XS-{base}-VFD", f"{tag} VFD healthy", "Fault", "Healthy", value=True)
        self.do(f"XY-{base}-STR", f"{tag} start command", "Idle", "Start")
        self.do(f"XY-{base}-STP", f"{tag} stop command", "Idle", "Stop")

    def _add_mov_io(self, base: str, tag: str) -> None:
        self.di(f"ZSO-{base}", f"{tag} open limit switch", "Not open", "Open")
        self.di(f"ZSC-{base}", f"{tag} closed limit switch", "Not closed", "Closed")
        self.di(f"XS-{base}-TRQ", f"{tag} torque or thermal overload trip",
                "Healthy", "Tripped")
        self.di(f"XS-{base}-AVL", f"{tag} actuator in remote", "Local", "Remote",
                value=True)
        self.do(f"XY-{base}-OPN", f"{tag} open command from control room", "Idle", "Open")
        self.do(f"XY-{base}-CLS", f"{tag} close command from control room", "Idle", "Close")

    def _build_malfunctions(self) -> None:
        def mf(setter):
            return setter

        self.add_malfunction("MF-001", "FCV-1001", "Control valve stiction", "Valve",
                             "Stick band", 0, 10,
                             lambda a, v: setattr(self.fcv1001, "stiction", v if a else 0.0))
        self.add_malfunction("MF-002", "FCV-1001", "Control valve hysteresis", "Valve",
                             "Deadband", 0, 10,
                             lambda a, v: setattr(self.fcv1001, "hysteresis", v if a else 0.0))
        self.add_malfunction("MF-007", "MOV-1001A", "MOV fails to open on command",
                             "MOV", "", 0, 1,
                             lambda a, v: setattr(self.mov_a, "fail_to_open", a))
        self.add_malfunction("MF-008", "MOV-1001A", "MOV torque switch trips mid-travel",
                             "MOV", "Trip at travel", 0, 100,
                             lambda a, v: setattr(self.mov_a, "torque_trip_at", v if a else None))
        self.add_malfunction("MF-009", "MOV-1001A", "MOV open limit switch fails to make",
                             "MOV", "", 0, 1,
                             lambda a, v: setattr(self.mov_a, "open_limit_faulty", a))
        self.add_malfunction("MF-010", "MOV-1001A", "MOV slow travel", "MOV",
                             "Travel factor", 1, 6,
                             lambda a, v: setattr(self.mov_a, "slow_travel_factor",
                                                  v if a else 1.0))
        self.add_malfunction("MF-011", "MOV-1001B", "MOV drifts closed without command",
                             "MOV", "", 0, 1,
                             lambda a, v: setattr(self.mov_b, "drifts_closed", a))
        self.add_malfunction("MF-012", "FT-1001", "Transmitter drift", "Transmitter",
                             "Drift per hour", -5, 5,
                             lambda a, v: setattr(self.tx_flow, "drift_pct_per_hour",
                                                  v if a else 0.0))
        self.add_malfunction("MF-013", "FT-1001", "Transmitter noise increase",
                             "Transmitter", "Sigma", 0, 5,
                             lambda a, v: setattr(self.tx_flow, "noise_sigma_pct",
                                                  v if a else 0.45))
        self.add_malfunction("MF-014", "LT-1001", "Transmitter frozen at last value",
                             "Transmitter", "", 0, 1,
                             lambda a, v: setattr(self.tx_level, "failure",
                                                  TxFailure.FROZEN if a else TxFailure.NONE))
        self.add_malfunction("MF-021", "P-101A", "Pump trip on overload", "Rotating",
                             "", 0, 1,
                             lambda a, v: setattr(self.motor_a, "trip_on_overload", a))
        self.add_malfunction("MF-022", "P-101A", "Pump impeller wear", "Rotating",
                             "Head loss", 0, 40,
                             lambda a, v: setattr(self.pump_a, "wear_pct", v if a else 0.0))
        self.add_malfunction("MF-023", "P-101A", "VFD communication fault", "Rotating",
                             "", 0, 1,
                             lambda a, v: setattr(self.motor_a, "vfd_comms_fault", a))
        self.add_malfunction("MF-031", "D1", "Fresh feed rate step", "Process",
                             "Feed rate", 0, 160,
                             lambda a, v: setattr(self, "fresh_feed", v if a else 66.2))
        self.add_malfunction("MF-035", "D1", "Fresh feed temperature step", "Process",
                             "Feed temperature", 10, 80,
                             lambda a, v: setattr(self, "fresh_temperature", v if a else 38.0))
        self.add_malfunction("MF-036", "D1", "Fresh feed light key step", "Process",
                             "Light key mol%", 0, 30,
                             lambda a, v: setattr(self, "fresh_lightfrac", v / 100.0 if a else 0.12))
        self.add_malfunction("MF-040", "D1", "Fresh feed supply pressure step", "Process",
                             "Supply pressure", 1, 10,
                             lambda a, v: setattr(self, "fresh_pressure", v if a else 6.0))

    # --------------------------------------------------------------------- step
    def step(self, dt: float) -> None:
        air = bool(self.bus.get("air_failure", 0.0))
        t = self.tags

        # --------------------------------------------------------- motor valves
        self.mov_a.remote = not self.mov_a_local
        self.mov_b.remote = not self.mov_b_local
        t["XS-MOV1001A-AVL"].set(self.mov_a.remote and not self.mov_a.torque_tripped)
        t["XS-MOV1001B-AVL"].set(self.mov_b.remote and not self.mov_b.torque_tripped)
        self.mov_a.command(bool(t["XY-MOV1001A-OPN"].effective),
                           bool(t["XY-MOV1001A-CLS"].effective))
        self.mov_b.command(bool(t["XY-MOV1001B-OPN"].effective),
                           bool(t["XY-MOV1001B-CLS"].effective))
        self.mov_a.step(dt)
        self.mov_b.step(dt)

        t["ZSO-MOV1001A"].set(self.mov_a.zso)
        t["ZSC-MOV1001A"].set(self.mov_a.zsc)
        t["XS-MOV1001A-TRQ"].set(self.mov_a.torque_tripped)
        t["ZSO-MOV1001B"].set(self.mov_b.zso)
        t["ZSC-MOV1001B"].set(self.mov_b.zsc)
        t["XS-MOV1001B-TRQ"].set(self.mov_b.torque_tripped)

        # -------------------------------------------------------- suction sides
        level_head = self.STATIC_HEAD * (self.level.y / 100.0)
        p_drum = float(self.PT1001.value)
        flow_prev = self.charge_flow.y + self.minflow.y

        def suction(mov: MotorOperatedValve) -> float:
            frac = max(mov.flow_fraction, 1e-3)
            loss = self.strainer_dp + 0.55 * (flow_prev / 150.0) ** 2 / (frac ** 2)
            return clamp(p_drum + level_head - loss, -0.9, 12.0)

        p_suc_a = suction(self.mov_a)
        p_suc_b = suction(self.mov_b)

        # --------------------------------------------------------------- motors
        # No start permissive here: the interlock lives in the controller MC-/XC-
        # modules, and a trainee who defeats it must still get the physical
        # consequence. A pump started against a shut suction valve sees its
        # suction pressure collapse and cavitates; a drum pumped empty loses
        # prime and the pump develops no head.
        self.motor_a.command(bool(t["XY-P101A-STR"].effective),
                             bool(t["XY-P101A-STP"].effective))
        self.motor_b.command(bool(t["XY-P101B-STR"].effective),
                             bool(t["XY-P101B-STP"].effective))
        self.motor_a.step(dt, True, float(t["SC-1001"].effective))
        self.motor_b.step(dt, True, 100.0)

        primed = self.level.y > 2.0
        cav_a = self.pump_a.check_cavitation(p_suc_a, self.VAPOUR_PRESSURE,
                                             self.motor_a.running)
        cav_b = self.pump_b.check_cavitation(p_suc_b, self.VAPOUR_PRESSURE,
                                             self.motor_b.running)
        if not primed:
            if self.motor_a.running:
                self.pump_a.cavitating = cav_a = True
            if self.motor_b.running:
                self.pump_b.cavitating = cav_b = True

        # ------------------------------------------------------------ hydraulics
        esd = bool(self.bus.get("esd_u100", 0.0))
        self.xv1001_open = bool(t["XY-XV1001-OPN"].effective) and not esd and not air
        t["ZSO-XV1001"].set(self.xv1001_open)
        t["ZSC-XV1001"].set(not self.xv1001_open)

        self.fcv1001.step(dt, t["FCV-1001"].effective, air)
        self.fcv1002.step(dt, t["FCV-1002"].effective, air)
        self.lcv1001.step(dt, t["LCV-1001"].effective, air)

        if primed:
            head_a = self.pump_a.head(flow_prev, self.motor_a.speed_pct)
            head_b = self.pump_b.head(flow_prev, self.motor_b.speed_pct)
            if cav_a:
                head_a *= 0.45                  # cavitating pump loses head
            if cav_b:
                head_b *= 0.45
        else:
            head_a = head_b = 0.0               # gas locked: no liquid, no head
        best = max((head_a, p_suc_a), (head_b, p_suc_b), key=lambda x: x[0])
        p_dis = best[1] + best[0] * self.DENSITY * 9.81 / 1e5

        downstream = float(self.bus.get("h1_inlet_pressure", 11.0))
        sg = self.DENSITY / 1000.0

        q_charge = 0.0
        if self.xv1001_open:
            q_charge = self.fcv1001.flow(p_dis - downstream, sg)
        q_min = self.fcv1002.flow(p_dis - p_drum, sg)

        self.charge_flow.step(q_charge, dt)
        self.minflow.step(q_min, dt)

        # ----------------------------------------------------------- inventory
        t = self.tags
        t["ZSO-HV1002"].set(False)
        t["ZSC-HV1002"].set(True)
        self.mov_recycle.step(dt)
        recycle = (float(self.bus.get("t1_bottoms_recycle", 0.0))
                   * self.mov_recycle.device.position / 100.0)
        makeup = self.lcv1001.flow(4.0, sg)
        drain = 55.0 * (self.hv1001_position / 100.0) ** 0.5
        inventory_in = self.fresh_feed + recycle + makeup + self.minflow.y
        inventory_out = self.charge_flow.y + self.minflow.y + drain
        net = inventory_in - inventory_out
        level_before = self.level.y
        self.level.step(net / (self.D1_AREA * self.D1_HEIGHT) * 100.0 / 3600.0, dt)
        self.record_inventory_balance(
            "D1 liquid inventory", inventory_in, inventory_out,
            level_before, self.level.y, self.D1_AREA * self.D1_HEIGHT, dt)

        self.bus["charge_flow"] = self.charge_flow.y
        self.bus["charge_temperature"] = float(self.TT1001.value)
        self.bus["d1_level"] = self.level.y
        self.bus["fresh_lightfrac"] = self.fresh_lightfrac

        # ---------------------------------------------------------- instruments
        self.LT1001.set(self.tx_level.step(dt, self.level.y),
                        Quality(self.tx_level.quality))
        self.FT1001.set(self.tx_flow.step(dt, self.charge_flow.y),
                        Quality(self.tx_flow.quality))
        self.FT1004.set(self.tx_fresh.step(dt, self.fresh_feed),
                        Quality(self.tx_fresh.quality))
        self.PT1005.set(self.tx_fresh_p.step(dt, self.fresh_pressure),
                        Quality(self.tx_fresh_p.quality))
        self.TT1003.set(self.tx_fresh_t.step(dt, self.fresh_temperature),
                        Quality(self.tx_fresh_t.quality))
        self.AT1001.set(self.tx_fresh_z.step(dt, self.fresh_lightfrac * 100.0),
                        Quality(self.tx_fresh_z.quality))
        self.PT1002.set(self.tx_pdis.step(dt, p_dis), Quality(self.tx_pdis.quality))
        self.PT1003.set(self.tx_psuc_a.step(dt, p_suc_a), Quality(self.tx_psuc_a.quality))
        self.PT1004.set(clamp(p_suc_b, 0.0, 10.0))
        self.PDT1001.set(clamp(self.strainer_dp + 0.55 * (flow_prev / 150.0) ** 2, 0, 2))
        self.FT1002.set(recycle)
        self.FT1003.set(self.minflow.y)
        self.ST1001.set(self.motor_a.speed_pct)
        self.IT1001.set(self.motor_a.current)
        self.IT1002.set(self.motor_b.current)
        self.ZT1001.set(self.fcv1001.position)

        vib = 12.0 + (28.0 if cav_a else 0.0) + self.pump_a.wear_pct * 0.6
        self.VT1001.set(vib if self.motor_a.running else 2.0)
        self.TT1002.set(self.bearing_temp.step(
            45.0 + (35.0 if self.motor_a.running else 0.0) + (15.0 if cav_a else 0.0), dt))

        # D1 operates on a light gas blanket; pressure follows the vapour balance
        # loosely, and outlet temperature is the mixing cup of feed and recycle.
        self.PT1001.set(clamp(2.5 + 0.004 * (self.level.y - 55.0), 0.5, 8.0))
        t_recycle = float(self.bus.get("t1_bottoms_temperature", 200.0))
        total_in = max(self.fresh_feed + recycle, 1e-3)
        t_mix = (self.fresh_feed * self.fresh_temperature
                 + recycle * min(t_recycle, 260.0)) / total_in
        self.TT1001.set(clamp(t_mix, 10.0, 145.0))
        self.DT1001.set(clamp(820.0 - 0.62 * float(self.TT1001.value), 600.0, 900.0))
        t["XS-P101A-AVL"].set(not self.local_a and not self.motor_a.faulted)
        t["XS-P101B-AVL"].set(not self.local_b and not self.motor_b.faulted)
        t["XS-P101A-RUN"].set(self.motor_a.running)
        t["XS-P101A-FLT"].set(self.motor_a.faulted)
        t["XS-P101A-VFD"].set(self.motor_a.vfd_healthy)
        t["XS-P101B-RUN"].set(self.motor_b.running)
        t["XS-P101B-FLT"].set(self.motor_b.faulted)
        t["ZSO-HV1001"].set(self.hv1001_position >= 99.0)
        t["ZSC-HV1001"].set(self.hv1001_position <= 1.0)
        t["LSLL-1001"].set(self.level.y < 8.0)
        t["LSHH-1001"].set(self.level.y > 92.0)

    # -------------------------------------------------------------- persistence
    def save_state(self):
        return {
            "level": self.level.y,
            "charge": self.charge_flow.y,
            "mov_a": self.mov_a.position,
            "mov_b": self.mov_b.position,
            "motor_a": self.motor_a.state(),
            "motor_b": self.motor_b.state(),
            "hv1001": self.hv1001_position,
            "feed": self.fresh_feed,
            "feed_t": self.fresh_temperature,
            "feed_p": self.fresh_pressure,
            "feed_z": self.fresh_lightfrac,
        }

    def load_state(self, s):
        self.level.reset(s.get("level", 55.0))
        self.charge_flow.reset(s.get("charge", 0.0))
        self.mov_a.position = s.get("mov_a", 100.0)
        self.mov_b.position = s.get("mov_b", 0.0)
        # Older snapshots carry only run_a/run_b; Motor.restore reconstructs
        # the run command from the run state in that case.
        self.motor_a.restore(s.get("motor_a", {"run": s.get("run_a", 0.0)}))
        self.motor_b.restore(s.get("motor_b", {"run": s.get("run_b", 0.0)}))
        self.hv1001_position = s.get("hv1001", 0.0)
        self.fresh_feed = s.get("feed", 66.2)
        self.fresh_temperature = s.get("feed_t", 38.0)
        self.fresh_pressure = s.get("feed_p", 6.0)
        self.fresh_lightfrac = s.get("feed_z", 0.12)
