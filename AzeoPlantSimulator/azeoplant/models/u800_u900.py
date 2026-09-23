"""U800 effluent treatment and U900 safety instrumented system.

U800 - pH neutralisation
------------------------
The classic severely non-linear loop. pH is a logarithm of concentration, so the
process gain across the titration curve varies by orders of magnitude: near
neutrality a drop of reagent moves the measurement several units, while out on
the flat ends nothing happens at all. A single fixed-gain PID cannot control it,
which is the entire argument for gain scheduling and PV linearisation.

Concentration is what is integrated here, not pH. pH is computed from it at the
end. That is the right way round physically, and it is also what makes the
linearisation exercise honest: the trainee can compare controlling pH directly
against controlling the linearised signal and see the difference.

U900 - safety instrumented system
---------------------------------
The SIS **logic lives in the DCS**, not here. This unit supplies the initiators
an operator or instructor can trigger — pushbuttons, gas and flame detection —
and it consumes the ESD output commands, translating them into trips the process
models obey through the bus.

That division is deliberate. A cause and effect matrix implemented inside the
simulator would teach nothing; implemented in the DCS against a plant that
genuinely trips, it teaches everything.
"""

from __future__ import annotations

import logging
import math

from ..core.devices import ControlValve, Transmitter, ValveChar
from ..core.dynamics import DeadTime, Integrator, Lag, clamp
from ..core.tags import Quality
from .base import ProcessUnit
from .packages import MotorPackage, PumpTrain

log = logging.getLogger(__name__)


class EffluentTreatment(ProcessUnit):
    code = "U800"
    name = "Effluent treatment"

    TANK_VOLUME = 49.0
    MIX_TAU_RUNNING = 45.0
    MIX_TAU_STOPPED = 600.0
    PARAMETER_META = {
        "TANK_VOLUME": {"eu": "m3", "description": "Neutralization tank effective volume",
                        "lo": 0.1, "hi": 10000.0},
        "MIX_TAU_RUNNING": {"eu": "s", "description": "Neutralization mixing lag with agitator running",
                            "lo": 0.001, "hi": 100000.0},
        "MIX_TAU_STOPPED": {"eu": "s", "description": "Neutralization mixing lag with agitator stopped",
                            "lo": 0.001, "hi": 1000000.0},
    }

    def build(self) -> None:
        self.AT8001 = self.ai("AT-8001", "Neutralisation tank inlet pH", "pH", 0, 14, 4.2)
        self.AT8002 = self.ai("AT-8002", "Neutralisation tank outlet pH", "pH", 0, 14, 7.0)
        self.AT8003 = self.ai("AT-8003", "Effluent outlet chemical oxygen demand",
                              "mg/l", 0, 1000, 320.0)
        self.FT8001 = self.ai("FT-8001", "Effluent flow to treatment", "m3/h", 0, 120, 0.0)
        self.manual_caustic_open = True
        self.TT8001 = self.ai("TT-8001", "Effluent temperature", "degC", 0, 80, 38.0)
        self.LT8001 = self.ai("LT-8001", "NT-801 neutralisation tank level",
                              "%", 0, 100, 55.0)
        self.IT8001 = self.ai("IT-8001", "M-801 agitator motor current", "A", 0, 60, 0.0)
        self.QT8001 = self.ai("QY-8001", "Linearised pH signal", "%", 0, 100, 50.0)

        self.ao("FCV-8001", "Caustic coarse dosing valve (split range 50-100%)")
        self.ao("FCV-8002", "Caustic fine dosing valve (split range 0-50%)", value=18.0)
        self.ao("FCV-8003", "Acid dosing valve")
        self.ao("FCV-8004", "Effluent discharge control valve", value=45.0)
        self.ao("TCV-8001", "E4 effluent cooler cooling water valve",
                value=50.0)

        self.di("ASHH-8001", "Effluent pH outside discharge consent",
                "Normal", "Tripped")
        self.di("HV-8001-ZSO", "HV-8001 caustic manual isolation open",
                "Not open", "Open", value=True)

        self.agitator = MotorPackage(self, "M-801", "NT-801 agitator", 48.0)
        self.IT8002 = self.ai("IT-8002", "P-801A motor current", "A", 0, 120)
        self.IT8003 = self.ai("IT-8003", "P-801B motor current", "A", 0, 120)
        self.di("ZSO-HV8001", "HV-8001 open limit switch",
                "Not open", "Open", value=True)
        self.di("ZSC-HV8001", "HV-8001 closed limit switch",
                "Not closed", "Closed", value=False)
        self.transfer = PumpTrain(self, "P-801A", "P-801B", "Effluent transfer pump",
                                  "MOV-8001A", "MOV-8001B", head_shutoff=95.0,
                                  flow_max=140.0, flow_min=10.0, rated_current=70.0,
                                  travel_time=15.0, duty_running=True)

        self.v_coarse = ControlValve("FCV-8001", cv_rated=9, char=ValveChar.LINEAR,
                                     stroke_time=3)
        self.v_fine = ControlValve("FCV-8002", cv_rated=1.5,
                                   char=ValveChar.EQUAL_PERCENT, stroke_time=3)
        self.v_acid = ControlValve("FCV-8003", cv_rated=1.5,
                                   char=ValveChar.EQUAL_PERCENT, stroke_time=3)
        self.v_discharge = ControlValve("FCV-8004", cv_rated=130,
                                        char=ValveChar.LINEAR, stroke_time=6)
        self.v_cw = ControlValve("TCV-8001", cv_rated=170,
                                 char=ValveChar.LINEAR, stroke_time=8,
                                 fail_closed=False)

        # Excess acid concentration in mol/l. Negative means excess base.
        self.excess_acid = Integrator(0.0, -0.05, 0.05)
        self.eff_flow = Lag(2.0, 0.0)    # discharge line inertia
        self.level = Integrator(55.0, 0.0, 100.0)
        self.ph_dead = DeadTime(35.0, self.dt, 7.0)
        self.e4_lag = Lag(60.0, 50.0)
        self.ph_lag = Lag(12.0, 7.0)
        self.inlet_ph = 4.2

        self.tx_ph = Transmitter("AT-8002", 0, 14, tau=6.0, noise_sigma_pct=0.4)

        self.add_malfunction("MF-039", "NT-801", "Effluent acid slug", "Process",
                             "Inlet pH", 1, 7,
                             lambda a, v: setattr(self, "inlet_ph", v if a else 4.2))
        self.add_malfunction("MF-042", "AT-8002", "pH probe coating, slow response",
                             "Analyser", "Extra lag", 0, 300,
                             lambda a, v: setattr(self.tx_ph, "extra_lag",
                                                  v if a else 0.0))

    @staticmethod
    def _ph_from_excess(excess: float, buffer_k: float = 0.004) -> float:
        """Titration curve. Buffering flattens it away from the equivalence point."""
        x = excess / max(buffer_k, 1e-6)
        return clamp(7.0 - 3.2 * math.copysign(math.log10(1.0 + abs(x)), x), 0.5, 13.5)

    def step(self, dt: float) -> None:
        t = self.tags
        air = bool(self.bus.get("air_failure", 0.0))
        for valve, tag in ((self.v_coarse, "FCV-8001"), (self.v_fine, "FCV-8002"),
                           (self.v_acid, "FCV-8003"), (self.v_discharge, "FCV-8004"),
                           (self.v_cw, "TCV-8001")):
            valve.step(dt, t[tag].effective, air)

        self.agitator.step(dt, permissive=self.level.y > 20.0)
        inflow = clamp(float(self.bus.get("sour_water_flow", 6.0)) + 22.0, 0.0, 120.0)

        # The pump curve and the discharge valve are solved against each other.
        # Reading back last scan's flow closes that algebraic loop with a one
        # scan lag, and the pair then flips between two solutions on alternate
        # scans. Carrying the flow in its own state gives the liquid the
        # inertia it physically has and the loop settles.
        self.tags["ZSO-HV8001"].set(True)
        self.tags["ZSC-HV8001"].set(False)
        self.IT8002.set(self.transfer.motor_a.current)
        self.IT8003.set(self.transfer.motor_b.current)
        self.transfer.step(dt, 0.9, self.eff_flow.y, self.level.y > 8.0,
                           100.0, 0.15, 0.35, 60.0)
        p_dis = self.transfer.discharge_pressure(self.eff_flow.y, 1000.0)
        outflow = (self.v_discharge.flow(max(p_dis - 1.2, 0.0), 1.0)
                   if self.transfer.any_running else 0.0)
        outflow = self.eff_flow.step(outflow, dt)

        level_before = self.level.y
        self.level.step((inflow - outflow) / self.TANK_VOLUME * 100.0 / 3600.0, dt)
        self.record_inventory_balance(
            "neutralization tank inventory", inflow, outflow,
            level_before, self.level.y, self.TANK_VOLUME, dt)

        caustic = (self.v_coarse.flow(3.0, 1.2) + self.v_fine.flow(3.0, 1.2)) \
            * (1.0 if self.manual_caustic_open else 0.0)
        acid = self.v_acid.flow(3.0, 1.2)

        inlet_excess = (10.0 ** (-self.inlet_ph) - 10.0 ** (self.inlet_ph - 14.0)) * 60.0
        acid_in = inflow * inlet_excess + acid * 2.0
        base_in = caustic * 2.0
        volume = max(self.TANK_VOLUME * self.level.y / 100.0, 1.0)
        mixing = self.MIX_TAU_RUNNING if self.agitator.running else self.MIX_TAU_STOPPED

        rate = (acid_in - base_in - outflow * self.excess_acid.y * 1000.0) \
            / (volume * 1000.0) / (mixing / 45.0) / 3600.0 * 1000.0
        self.excess_acid.step(rate, dt)

        ph_true = self._ph_from_excess(self.excess_acid.y)
        ph = self.ph_lag.step(self.ph_dead.step(ph_true), dt)

        t["HV-8001-ZSO"].set(self.manual_caustic_open)
        # Effluent arrives hot from the sour water draw and cools in the tank.
        # E4 cools the incoming effluent; TC-8001 holds the treatment
        # temperature the chemistry (and the discharge consent) wants.
        base_t = clamp(30.0 + 0.18 * float(self.bus.get(
            "d3_liquid_temperature", 60.0)), 10.0, 78.0)
        cw_dp = float(self.bus.get("cooling_water_dp_bar", 2.4))
        cw_supply = float(self.bus.get("cooling_water_temperature", 28.0))
        cw_flow = self.v_cw.flow(cw_dp, 1.0)
        cw_design = 0.865 * self.v_cw.cv_rated * math.sqrt(2.4)
        cool = self.e4_lag.step(clamp(cw_flow / max(cw_design, 1.0), 0.0, 1.5), dt)
        outlet_t = clamp(base_t - cool * 0.75 * max(base_t - cw_supply, 0.0),
                         10.0, 78.0)
        self.TT8001.set(outlet_t)
        cw_duty = clamp(inflow * 1000.0 / 3600.0 * 4.18
                        * max(base_t - outlet_t, 0.0), 0.0, 50000.0)
        self.bus["u800_cw_flow"] = cw_flow
        self.bus["u800_cw_duty"] = cw_duty
        self.AT8001.set(self.inlet_ph)
        self.AT8002.set(self.tx_ph.step(dt, ph), Quality(self.tx_ph.quality))
        self.AT8003.set(clamp(320.0 + abs(ph - 7.0) * 90.0, 0, 1000))
        self.FT8001.set(outflow)
        self.LT8001.set(self.level.y)
        self.IT8001.set(self.agitator.current)
        # Linearised signal: the TRUE inverse titration curve - back to
        # excess-reagent units, in which the process gain is constant. A
        # PID on this signal sees the same slope at pH 4 as at pH 7,
        # which a straight line in pH (the old placeholder) does not.
        x_lin = math.copysign(10.0 ** (abs(ph - 7.0) / 3.2) - 1.0, ph - 7.0)
        self.QT8001.set(clamp(50.0 + x_lin * 4.0, 0.0, 100.0))
        t["ASHH-8001"].set(not (6.0 <= ph <= 9.0))

    def save_state(self):
        return {"ex": self.excess_acid.y, "lvl": self.level.y,
                "ph": self.ph_lag.y, "e4": self.e4_lag.y}

    def load_state(self, s):
        self.excess_acid.reset(s.get("ex", 0.0))
        self.level.reset(s.get("lvl", 55.0))
        self.ph_lag.reset(s.get("ph", 7.0))
        self.e4_lag.reset(s.get("e4", 50.0))


class SafetySystem(ProcessUnit):
    """Supplies ESD initiators and obeys the ESD outputs the DCS decides on."""

    code = "U900"
    name = "Safety instrumented system"

    EFFECTS = {
        "XY-9001": ("esd_total", "ESD-0 total plant shutdown"),
        "XY-9002": ("esd_reaction", "ESD-1 shutdown U200, U300 and U400"),
        "XY-9003": ("esd_fractionation", "ESD-1 shutdown U500 and U600"),
        "XY-9004": ("esd_boiler", "ESD-1 shutdown U700"),
        "XY-9005": ("esd_depressure", "R1 emergency depressuring"),
    }

    def build(self) -> None:
        for tag, desc in [
                ("HS-9001", "Field ESD pushbutton (level 1)"),
                ("HS-9002", "Control room ESD pushbutton (level 0)"),
                ("GD-9001", "Gas detected zone 1 reactor area at 20% LEL"),
                ("GD-9002", "Gas detected zone 2 compressor house at 20% LEL"),
                ("GD-9003", "Gas detected zone 3 fired equipment at 20% LEL"),
                ("FD-9001", "Flame detected zone 1"),
                ("FD-9002", "Flame detected zone 2"),
                ("XS-9002", "SIS reset request from field")]:
            self.di(tag, desc, "Normal", "Detected")
        self.di("XS-9001", "Fire water pump running", "Stopped", "Running")

        for tag, (_key, desc) in self.EFFECTS.items():
            self.do(tag, f"{desc} command", "Normal", "Trip")
        self.do("XY-9006", "ESD healthy and first-out reset lamp", "Off", "On")

        self.ai("XI-9001", "Active ESD effects", "-", 0, 10, 0.0)

        self.initiators = {t: False for t in
                           ["HS-9001", "HS-9002", "GD-9001", "GD-9002", "GD-9003",
                            "FD-9001", "FD-9002", "XS-9002"]}
        for mf_id, tag, desc in [
                ("MF-901", "GD-9001", "Gas detected, reactor area"),
                ("MF-902", "GD-9002", "Gas detected, compressor house"),
                ("MF-903", "GD-9003", "Gas detected, fired equipment"),
                ("MF-904", "FD-9001", "Flame detected, zone 1"),
                ("MF-905", "HS-9001", "Field ESD pushbutton pressed"),
                ("MF-906", "HS-9002", "Control room ESD pushbutton pressed")]:
            self.add_malfunction(
                mf_id, tag, desc, "Safety", "", 0, 1,
                lambda a, v, _t=tag: self.initiators.__setitem__(_t, bool(a)))

    def set_initiator(self, tag: str, state: bool) -> None:
        """The one write path both cores share for pushbuttons, detectors
        and the field reset (the native unit's ``initiators`` is a copy)."""
        self.initiators[tag] = bool(state)

    def step(self, dt: float) -> None:
        t = self.tags
        for tag, state in self.initiators.items():
            t[tag].set(state)
        total = bool(t["XY-9001"].effective)
        active = 0
        for tag, (key, _desc) in self.EFFECTS.items():
            tripped = bool(t[tag].effective) or total
            self.bus[key] = 1.0 if tripped else 0.0
            active += 1 if tripped else 0

        # Translate the two shutdown levels into per-unit trips the models obey.
        reaction = bool(self.bus.get("esd_reaction", 0.0)) or total
        fractionation = bool(self.bus.get("esd_fractionation", 0.0)) or total
        boiler = bool(self.bus.get("esd_boiler", 0.0)) or total
        self.bus["esd_u100"] = 1.0 if total else 0.0
        self.bus["esd_u200"] = 1.0 if reaction else 0.0
        self.bus["esd_u300"] = 1.0 if reaction else 0.0
        self.bus["esd_u400"] = 1.0 if reaction else 0.0
        self.bus["esd_u500"] = 1.0 if fractionation else 0.0
        self.bus["esd_u700"] = 1.0 if boiler else 0.0

        t["XS-9001"].set(bool(t["FD-9001"].value) or bool(t["FD-9002"].value))
        self.tags["XI-9001"].set(float(active))

    def save_state(self):
        return {}

    def load_state(self, s):
        return None
