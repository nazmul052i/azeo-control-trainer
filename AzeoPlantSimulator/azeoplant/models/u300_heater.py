"""U300 - fired heater H1.

Two-pass charge heater firing gas from the shared header with fuel oil as the
split-range trim. Everything a heater exercise needs is here: a burner header
whose pressure sags when the boiler takes fuel, an excess air relationship that
produces both O2 and CO, tube skin temperatures that respond to duty over flow,
and pass outlet temperatures that can be unbalanced with the pass valves.

Combustion
----------
Duty is fuel energy times efficiency, where efficiency falls away from the
optimum excess air in both directions: too little air gives incomplete
combustion, too much carries heat up the stack. That single curve is what makes
an O2 trim exercise worth doing.

The outlet temperature is a steady-state energy balance followed by a lag and a
dead time. Both are exact discrete forms, so the model stays stable at any speed
factor and at zero charge flow, which is the operating point that breaks naive
implementations because duty over flow goes to infinity.
"""

from __future__ import annotations

import logging
import math

from ..core.devices import ControlValve, Transmitter, ValveChar
from .packages import SdvPackage
from ..core.dynamics import DeadTime, Integrator, Lag, clamp, safe_div
from ..core.tags import Quality
from .base import ProcessUnit

log = logging.getLogger(__name__)

P_STD = 1.01325
CP_CHARGE = 2.35          # kJ/kg.K
STOICH_AIR = 9.6          # Nm3 air per Nm3 fuel gas


class FiredHeater(ProcessUnit):
    code = "U300"
    name = "Fired heater H1"

    BURNER_HEADER_VOLUME = 5.0     # m3
    DUTY_MAX = 30.0                # MW
    TAU_OUTLET = 105.0             # s
    DEADTIME_OUTLET = 28.0         # s
    E5_EFF = 0.60                  # feed/product exchanger effectiveness
    AIR_MAX = 45.0                 # kNm3/h at full damper and full fan speed
    PARAMETER_META = {
        "BURNER_HEADER_VOLUME": {"eu": "m3", "description": "H1 burner-header gas volume",
                                 "lo": 0.1, "hi": 1000.0},
        "DUTY_MAX": {"eu": "MW", "description": "H1 maximum fired duty",
                     "lo": 0.0, "hi": 1000.0},
        "TAU_OUTLET": {"eu": "s", "description": "H1 outlet-temperature lag",
                       "lo": 0.001, "hi": 100000.0},
        "DEADTIME_OUTLET": {"eu": "s", "description": "H1 outlet-temperature transport delay",
                            "lo": 0.0, "hi": 100000.0},
        "E5_EFF": {"eu": "fraction", "description": "E5 feed/product exchanger effectiveness",
                   "lo": 0.0, "hi": 1.0},
        "AIR_MAX": {"eu": "kNm3/h", "description": "H1 maximum combustion-air flow",
                    "lo": 0.0, "hi": 10000.0},
    }

    def build(self) -> None:
        # ------------------------------------------------------------- outputs
        self.TT3001 = self.ai("TT-3001", "H1 combined outlet temperature", "degC", 0, 500, 62.0)
        self.TT3002 = self.ai("TT-3002", "H1 pass 1 outlet temperature", "degC", 0, 500, 62.0)
        self.TT3003 = self.ai("TT-3003", "H1 pass 2 outlet temperature", "degC", 0, 500, 62.0)
        self.TT3004 = self.ai("TT-3004", "H1 stack gas temperature", "degC", 0, 600, 120.0)
        self.TT3005 = self.ai("TT-3005", "H1 pass 1 tube skin temperature", "degC", 0, 750, 90.0)
        self.TT3006 = self.ai("TT-3006", "H1 pass 2 tube skin temperature", "degC", 0, 750, 90.0)
        self.FT3001 = self.ai("FT-3001", "H1 fuel gas flow", "Nm3/h", 0, 3000)
        self.FT3002 = self.ai("FT-3002", "H1 fuel oil flow", "kg/h", 0, 1500)
        self.FT3003 = self.ai("FT-3003", "H1 combustion air flow", "kNm3/h", 0, 35)
        self.FT3004 = self.ai("FT-3004", "H1 pass 1 charge flow", "m3/h", 0, 200)
        self.FT3005 = self.ai("FT-3005", "H1 pass 2 charge flow", "m3/h", 0, 200)
        self.PT3001 = self.ai("PT-3001", "H1 fuel gas burner pressure", "barg", 0, 10, 3.0)
        self.PT3002 = self.ai("PT-3002", "H1 fuel oil header pressure", "barg", 0, 25, 8.0)
        self.PT3003 = self.ai("PT-3003", "H1 firebox draft", "mmH2O", -20, 10, -4.0)
        self.AT3001 = self.ai("AT-3001", "H1 flue gas oxygen", "mol%", 0, 21, 3.0)
        self.AT3002 = self.ai("AT-3002", "H1 flue gas carbon monoxide", "ppm", 0, 2000, 20.0)
        self.IT3001 = self.ai("IT-3001", "H1 induced draft fan motor current", "A", 0, 250)
        self.TT3007 = self.ai("TT-3007", "E5 charge outlet temperature",
                              "degC", 0, 300, 62.0)
        self.AY3001 = self.ai("AY-3001", "Fuel gas heating value, inferred",
                              "MJ/Nm3", 20, 50, 38.5)
        self.ZT3001 = self.ai("ZT-3001", "FCV-3001 position feedback", "%", 0, 100)
        self.ZT3002 = self.ai("ZT-3002", "FCV-3002 position feedback", "%", 0, 100)
        self.ZT3003 = self.ai("ZT-3003", "FCV-3003 position feedback", "%", 0, 100)
        self.di("XS-ID301-AVL", "ID-301 available and in remote",
                "Local", "Remote", value=True)
        self.di("XS-ID301-VFD", "ID-301 VFD healthy",
                "Faulted", "Healthy", value=True)

        # -------------------------------------------------------------- inputs
        self.ao("FCV-3001", "H1 fuel gas control valve", value=72.0)
        self.ao("FCV-3002", "H1 fuel oil valve A (split range 0-50%)")
        self.ao("FCV-3003", "H1 fuel oil valve B (split range 50-100%)")
        self.ao("FCV-3004", "H1 combustion air damper", value=80.0)
        self.ao("TCV-3002", "E5 charge bypass valve", value=50.0)
        self.ao("FCV-3005", "H1 pass 1 balancing valve", value=50.0)
        self.ao("FCV-3006", "H1 pass 2 balancing valve", value=50.0)
        self.ao("SC-3001", "H1 induced draft fan VFD speed reference", value=85.0)

        # ------------------------------------------------------------- discrete
        self.di("BS-3001", "H1 main burner flame detected", "No flame", "Flame")
        self.di("BS-3002", "H1 pilot flame detected", "No flame", "Flame")
        self.di("PSLL-3001", "H1 fuel gas pressure low low", "Normal", "Tripped")
        self.di("PSHH-3001", "H1 fuel gas pressure high high", "Normal", "Tripped")
        self.di("XS-3010", "H1 furnace purge complete permissive", "Not purged", "Purged")
        self.di("XS-ID301-RUN", "ID-301 induced draft fan running", "Stopped", "Running",
                value=True)
        self.di("XS-ID301-FLT", "ID-301 fault or trip", "Healthy", "Faulted")
        self.di("ZSO-XV3001", "XV-3001 fuel gas SDV open limit", "Not open", "Open",
                value=True)
        self.di("ZSC-XV3001", "XV-3001 fuel gas SDV closed limit", "Not closed", "Closed")
        self.di("ZSO-HV3001", "HV-3001 fuel oil manual isolation open limit",
                "Not open", "Open")
        self.di("ZSC-HV3001", "HV-3001 fuel oil manual isolation closed limit",
                "Not closed", "Closed", value=True)

        self.do("XY-XV3001-OPN", "XV-3001 fuel gas SDV open command", "Close", "Open",
                value=True)
        self.do("XY-3010", "H1 burner igniter energise command", "De-energise", "Energise")
        self.do("XY-3011", "H1 furnace purge sequence start command", "Idle", "Start")
        self.do("XY-ID301-STR", "ID-301 start command", "Idle", "Start", value=True)
        self.do("XY-ID301-STP", "ID-301 stop command", "Idle", "Stop")

        # ----------------------------------------------------------- equipment
        self.fcv3001 = ControlValve("FCV-3001", cv_rated=45, char=ValveChar.LINEAR,
                                    stroke_time=3)
        self.fcv3002 = ControlValve("FCV-3002", cv_rated=30,
                                    char=ValveChar.EQUAL_PERCENT, stroke_time=4)
        self.fcv3003 = ControlValve("FCV-3003", cv_rated=30,
                                    char=ValveChar.EQUAL_PERCENT, stroke_time=4)
        self.damper = ControlValve("FCV-3004", cv_rated=1000, char=ValveChar.LINEAR,
                                   stroke_time=12, fail_closed=False)
        self.pass1 = ControlValve("FCV-3005", cv_rated=100, char=ValveChar.LINEAR,
                                  stroke_time=8, fail_closed=False)
        self.pass2 = ControlValve("FCV-3006", cv_rated=100, char=ValveChar.LINEAR,
                                  stroke_time=8, fail_closed=False)

        # -------------------------------------------------------------- states
        self.coke = Integrator(0.0, 0.0, 60.0)   # % coke laydown
        self.e5_byp = Lag(8.0, 50.0)             # E5 bypass damper travel
        self.ay_lag = Lag(120.0, 38.5)           # heating-value inference lag
        self.burner_pressure = Integrator(y0=3.0 + P_STD, lo=P_STD * 0.2, hi=12.0)
        self.duty = Lag(8.0, 0.0)
        self.t_out = Lag(self.TAU_OUTLET, 62.0)
        self.t_dead = DeadTime(self.DEADTIME_OUTLET, self.dt, 62.0)
        self.t_pass1 = Lag(self.TAU_OUTLET * 0.9, 62.0)
        self.t_pass2 = Lag(self.TAU_OUTLET * 1.1, 62.0)
        self.skin1 = Lag(45.0, 90.0)
        self.skin2 = Lag(45.0, 90.0)
        self.o2 = Lag(6.0, 3.0)
        self.co = Lag(4.0, 20.0)
        self.stack = Lag(60.0, 120.0)
        self.lit = False
        self.efficiency_loss = 0.0
        self.fuel_oil_available = False
        self.xv_oil = SdvPackage(self, "XV-3002", "H1 fuel oil shutdown valve",
                                 stroke=3.0)
        self.purge_timer = 0.0
        self.purged = False
        self.oil_pressure = Lag(20.0, 8.0)
        self.id_fan_faulted = False

        self.tx_tout = Transmitter("TT-3001", 0, 500, tau=2.0, noise_sigma_pct=0.12)
        self.tx_o2 = Transmitter("AT-3001", 0, 21, tau=12.0, noise_sigma_pct=0.8)
        self.tx_fuel = Transmitter("FT-3001", 0, 2500, tau=0.7, noise_sigma_pct=0.5)
        self.tx_skin = Transmitter("TT-3005", 0, 750, tau=6.0, noise_sigma_pct=0.15)

        self._build_malfunctions()

    def _build_malfunctions(self) -> None:
        from ..core.devices import TxFailure

        self.add_malfunction("MF-003", "FCV-3001", "Control valve seat leakage", "Valve",
                             "Leakage", 0, 15,
                             lambda a, v: setattr(self.fcv3001, "leakage_pct", v if a else 0.0))
        self.add_malfunction("MF-016", "TT-3001", "Thermocouple burnout to upscale",
                             "Transmitter", "", 0, 1,
                             lambda a, v: setattr(self.tx_tout, "failure",
                                                  TxFailure.FAIL_HIGH if a else TxFailure.NONE))
        self.add_malfunction("MF-019", "AT-3001", "Oxygen analyser slow response",
                             "Analyser", "Extra lag", 0, 120,
                             lambda a, v: setattr(self.tx_o2, "extra_lag", v if a else 0.0))
        self.add_malfunction("MF-018", "ID-301", "Induced draft fan trip", "Rotating",
                             "", 0, 1,
                             lambda a, v: setattr(self, "id_fan_faulted", a))
        self.add_malfunction("MF-030", "H1", "Burner fouling", "Process",
                             "Efficiency loss", 0, 20,
                             lambda a, v: setattr(self, "efficiency_loss", v if a else 0.0))

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _efficiency(excess_air: float) -> float:
        """Thermal efficiency against excess air fraction, peaked near 15 percent."""
        if excess_air < 0.0:
            return clamp(0.62 + excess_air * 1.6, 0.15, 0.90)
        return clamp(0.90 - 0.55 * (excess_air - 0.15) ** 2 - 0.18 * excess_air, 0.30, 0.90)

    # --------------------------------------------------------------------- step
    def step(self, dt: float) -> None:
        t = self.tags
        air_fail = bool(self.bus.get("air_failure", 0.0))

        esd = bool(self.bus.get("esd_u300", 0.0))
        sdv_open = bool(t["XY-XV3001-OPN"].effective) and not esd
        t["ZSO-XV3001"].set(sdv_open)
        t["ZSC-XV3001"].set(not sdv_open)

        self.fcv3001.step(dt, t["FCV-3001"].effective, air_fail)
        self.fcv3002.step(dt, t["FCV-3002"].effective, air_fail)
        self.fcv3003.step(dt, t["FCV-3003"].effective, air_fail)
        self.damper.step(dt, t["FCV-3004"].effective, air_fail)
        self.pass1.step(dt, t["FCV-3005"].effective, air_fail)
        self.pass2.step(dt, t["FCV-3006"].effective, air_fail)

        fan_running = (bool(t["XY-ID301-STR"].effective)
                       and not bool(t["XY-ID301-STP"].effective))
        fan_speed = float(t["SC-3001"].effective) if fan_running else 0.0
        fan_speed_ready = fan_running and fan_speed > 25.0

        # ------------------------------------------------------- burner header
        header_abs = float(self.bus.get("fg_header_pressure_bara", 17.0))
        supply = float(self.bus.get("fg_to_h1_flow", 0.0))
        burner_abs = self.burner_pressure.y
        sg = 0.65

        fuel_gas = (self.fcv3001.gas_flow(burner_abs, P_STD, sg, 300.0)
                    if sdv_open else 0.0)
        net = supply - fuel_gas
        self.burner_pressure.step(net * P_STD / (3600.0 * self.BURNER_HEADER_VOLUME), dt)
        burner_barg = clamp(self.burner_pressure.y - P_STD, 0.0, 10.0)
        self.bus["h1_burner_pressure_bara"] = self.burner_pressure.y

        # -------------------------------------------------------------- firing
        low_pressure_trip = burner_barg < 1.5
        pilot = bool(t["XY-3010"].effective) or self.lit
        # Furnace purge: five air changes at a proven minimum airflow before a
        # light is permitted. Losing airflow part way restarts the count, which
        # is the behaviour a burner management sequence has to cope with.
        purge_air_ok = self.damper.position > 30.0 and fan_speed_ready
        if bool(t["XY-3011"].effective) and purge_air_ok and not self.lit:
            self.purge_timer += dt
            if self.purge_timer >= 300.0:
                self.purged = True
        elif not self.lit and not purge_air_ok:
            self.purge_timer = 0.0
            self.purged = False
        purged = self.purged or self.lit
        if esd or not sdv_open or low_pressure_trip or fuel_gas < 30.0:
            self.lit = False
        elif pilot and fuel_gas > 40.0:
            self.lit = True

        fuel_oil = 0.0
        if self.fuel_oil_available:
            fuel_oil = (self.fcv3002.flow(6.0, 0.9) + self.fcv3003.flow(6.0, 0.9)) * 900.0 / 1000.0

        fuel_energy_mw = 0.0
        if self.lit:
            lhv = float(self.bus.get("fg_lhv", 38.5))
            fuel_energy_mw = (fuel_gas * lhv + fuel_oil * 41.0) / 3600.0

        # ----------------------------------------------------------------- air
        # Combustion air is a fan against a damper, not a valve across a pressure
        # drop: flow follows the damper opening scaled by fan speed. Modelling it
        # as a gas valve gives numbers that are wrong by orders of magnitude
        # because the available differential is only tens of millibar.
        air_flow = (self.AIR_MAX * (self.damper.position / 100.0)
                    * (0.35 + 0.65 * fan_speed / 100.0))
        stoich = fuel_gas * STOICH_AIR / 1000.0                            # kNm3/h
        excess = safe_div(air_flow - stoich, stoich, default=1.0) if stoich > 1e-6 else 1.0
        excess = clamp(excess, -0.6, 3.0)

        # Radiant and convective split. High excess air carries more of the
        # release past the firebox into the convection bank, so the same fuel
        # makes less radiant flux and a hotter stack, which is exactly what an
        # oxygen trim exercise is meant to show costing money.
        radiant_frac = clamp(0.78 - 0.10 * excess, 0.55, 0.82)
        coke_loss = self.coke.y * 0.15
        eta = self._efficiency(excess) * (1.0 - (self.efficiency_loss + coke_loss) / 100.0)
        duty = self.duty.step(fuel_energy_mw * eta, dt)

        o2_ss = clamp(21.0 * excess / (1.0 + excess) * 0.95, 0.0, 12.0) if self.lit else 20.9
        co_ss = 20.0 if excess > 0.02 else clamp(60.0 + 5200.0 * (0.02 - excess), 20.0, 2000.0)
        if not self.lit:
            co_ss = 0.0

        # --------------------------------------------------------- charge side
        charge = float(self.bus.get("charge_flow", 0.0))
        t_in = float(self.bus.get("charge_temperature", 62.0))

        # E5 feed/product exchanger (the reference's arrangement): the
        # charge recovers heat from the REACTOR EFFLUENT on its way to
        # D3, with a bypass on the charge side holding the E5 outlet.
        # The effluent-side drop is absorbed by the D3 trim cooling and
        # the flash design point, so what the charge gains here shows up
        # as fuel the heater no longer fires - the E5 lesson. (A first
        # attempt put the hot side on the D3 LIQUID and stole 57 degC of
        # T1 feed enthalpy: the column's reflux collapsed within the
        # lineup. The reference's own display routes the hot side on to
        # D3, upstream of the flash.)
        th_in = float(self.bus.get("r1_effluent_temperature", 385.0))
        byp = self.e5_byp.step(
            clamp(float(t["TCV-3002"].effective), 0.0, 100.0), dt) / 100.0
        e5_rise = (self.E5_EFF * max(th_in - t_in, 0.0)) * (1.0 - byp)
        t_e5 = t_in + min(e5_rise, 120.0)
        self.TT3007.set(clamp(t_e5, 0.0, 300.0))
        t_in = t_e5

        self.AY3001.set(clamp(
            self.ay_lag.step(float(self.bus.get("fg_lhv", 38.5)), dt),
            20.0, 50.0))

        split1 = self.pass1.position / max(self.pass1.position + self.pass2.position, 1e-3)
        q1, q2 = charge * split1, charge * (1.0 - split1)

        # The recycle hydrogen from C1 mixes with the charge at the inlet
        # (the Whitehouse routing) and rides through the coils: the duty
        # heats the combined stream, so cutting the gas visibly eases the
        # firing and starving it never shows up as free heater capacity.
        gas = float(self.bus.get("recycle_gas_to_h1", 0.0)) / 1000.0  # kNm3/h
        gas_mw = float(self.bus.get("recycle_gas_mw", 12.0))
        gas_kg_s = gas * 1000.0 / 22.4 * gas_mw / 3600.0

        mass_kg_s = charge * 780.0 / 3600.0 + gas_kg_s
        # Guard the zero-flow singularity: below a threshold the tubes soak rather
        # than heat a stream, so the rise is capped instead of dividing by zero.
        if mass_kg_s > 0.5:
            rise = clamp(duty * 1000.0 / (mass_kg_s * CP_CHARGE), 0.0, 420.0)
        else:
            rise = 420.0 if duty > 0.1 else 0.0

        t_ss = clamp(t_in + rise, 0.0, 480.0)
        delayed = self.t_dead.step(t_ss)
        t_out = self.t_out.step(delayed, dt)

        bias = (split1 - 0.5) * rise * 0.35
        tp1 = self.t_pass1.step(clamp(t_ss - bias, 0.0, 480.0), dt)
        tp2 = self.t_pass2.step(clamp(t_ss + bias, 0.0, 480.0), dt)

        # Tube skin rides on radiant flux over the inside film coefficient,
        # which falls as flow ^0.8. Starve a pass and its skin runs away while
        # the other pass reads normal: the classic lost-pass scenario. Coke on
        # the inside wall insulates the metal from the process and lifts skin
        # further, which lays more coke; the runaway is bounded but real.
        coke_skin = 1.0 + self.coke.y * 0.012
        # Each pass sees half the firebox whatever its balancing valve does:
        # the burners do not know where the charge went. That asymmetry is the
        # whole lost-pass scenario, so the flux split is geometry, not flow.
        rad = duty * radiant_frac
        skin_rise1 = 190.0 * coke_skin * safe_div(rad * 0.5, max(q1, 2.0) ** 0.85, 0.0)
        skin_rise2 = 190.0 * coke_skin * safe_div(rad * 0.5, max(q2, 2.0) ** 0.85, 0.0)
        skin1 = self.skin1.step(clamp(tp1 + skin_rise1, 20.0, 740.0), dt)
        skin2 = self.skin2.step(clamp(tp2 + skin_rise2, 20.0, 740.0), dt)

        # Coking is slow chemistry with a hard threshold: hours at excursion
        # skin temperatures, geological time below them. Decoking is a
        # turnaround activity, so nothing on line takes it away.
        skin_max = max(skin1, skin2)
        self.coke.step(max(skin_max - 540.0, 0.0) * 0.8 / 50.0 / 3600.0, dt)

        stack = self.stack.step(
            clamp(120.0 + duty * (1.0 - radiant_frac) * 55.0 + excess * 45.0,
                  40.0, 590.0), dt)

        # -------------------------------------------------------------- outputs
        self.bus["h1_inlet_pressure"] = 11.0
        self.bus["h1_outlet_temperature"] = t_out
        self.bus["h1_duty_mw"] = duty

        self.TT3001.set(self.tx_tout.step(dt, t_out), Quality(self.tx_tout.quality))
        self.TT3002.set(tp1)
        self.TT3003.set(tp2)
        self.TT3004.set(stack)
        self.TT3005.set(self.tx_skin.step(dt, skin1), Quality(self.tx_skin.quality))
        self.TT3006.set(skin2)
        self.FT3001.set(self.tx_fuel.step(dt, fuel_gas), Quality(self.tx_fuel.quality))
        self.FT3002.set(fuel_oil)
        self.FT3003.set(air_flow)
        self.FT3004.set(q1)
        self.FT3005.set(q2)
        self.PT3001.set(burner_barg)
        self.AT3001.set(self.tx_o2.step(dt, self.o2.step(o2_ss, dt)),
                        Quality(self.tx_o2.quality))
        self.AT3002.set(self.co.step(co_ss, dt))
        self.ZT3001.set(self.fcv3001.position)
        self.xv_oil.step(dt)
        self.ZT3002.set(self.fcv3002.position)
        self.ZT3003.set(self.fcv3003.position)

        self.IT3001.set(250.0 * (0.25 + 0.75 * (fan_speed / 100.0) ** 3)
                        if fan_running else 0.0)
        self.PT3003.set(clamp(-0.5 - 0.12 * fan_speed + excess * 1.5, -20.0, 8.0))
        t["XS-ID301-RUN"].set(fan_running)
        t["XS-ID301-FLT"].set(self.id_fan_faulted)

        t["BS-3001"].set(self.lit)
        t["BS-3002"].set(pilot and sdv_open)
        t["PSLL-3001"].set(low_pressure_trip)
        t["PSHH-3001"].set(burner_barg > 8.5)
        t["XS-3010"].set(purged)
        # Fuel oil header pressure decays when the manual isolation is shut.
        self.PT3002.set(self.oil_pressure.step(
            8.0 if self.fuel_oil_available else 0.5, dt))
        t["XS-ID301-AVL"].set(True)
        t["XS-ID301-VFD"].set(not self.id_fan_faulted)
        t["ZSO-HV3001"].set(self.fuel_oil_available)
        t["ZSC-HV3001"].set(not self.fuel_oil_available)

    # -------------------------------------------------------------- persistence
    def save_state(self):
        return {"bp": self.burner_pressure.y, "duty": self.duty.y, "tout": self.t_out.y,
                "lit": float(self.lit), "oil": float(self.fuel_oil_available),
                "ay": self.ay_lag.y, "e5b": self.e5_byp.y}

    def load_state(self, s):
        self.burner_pressure.reset(s.get("bp", 4.0))
        self.duty.reset(s.get("duty", 0.0))
        self.t_out.reset(s.get("tout", 62.0))
        self.t_dead.reset(s.get("tout", 62.0))
        self.lit = bool(s.get("lit", 0.0))
        self.fuel_oil_available = bool(s.get("oil", 0.0))
        self.ay_lag.reset(s.get("ay", 38.5))
        self.e5_byp.reset(s.get("e5b", 50.0))
