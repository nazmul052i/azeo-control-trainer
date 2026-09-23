"""U700 - steam boiler B1 and the MP steam header.

This unit is what turns two other couplings from numbers into physics. B1 draws
fuel from the same header as H1, so firing one starves the other; and B1 supplies
the MP steam that both column reboilers consume, so loading one column disturbs
the other through the header pressure.

Shrink and swell
----------------
The reason three-element level control exists. Steam bubbles below the water
surface occupy volume, so drum level responds to *pressure* and *steaming rate*
as well as to inventory — and it responds the wrong way round. Open the steam
valve and pressure falls, bubbles expand, and the indicated level **rises** even
though mass is leaving. A single-element controller sees a rising level, cuts
feedwater, and makes a real low-level condition worse.

Modelled as an inverse-response term added to the true inventory level:

    indicated = inventory + K · (swell dynamics of steam flow), K negative

with a 22 second time constant. Trainees should be able to see the level kick the
wrong way for the first half minute after a load change, and should find that a
single-element scheme cannot be tuned out of trouble.
"""

from __future__ import annotations

import logging
import math

from ..core.devices import ControlValve, Transmitter, ValveChar
from ..core.dynamics import Integrator, Lag, LeadLag, clamp, safe_div
from ..core.tags import Quality
from .base import ProcessUnit
from .packages import MotorPackage, PumpTrain, SdvPackage

log = logging.getLogger(__name__)

P_STD = 1.01325
STOICH_AIR = 9.6


#: conductivity of the treated, deaerated boiler feedwater, uS/cm.
#: Demineralised make-up plus condensate return sits in the tens;
#: the 1000 this used to be is raw water, and made every
#: achievable blowdown rate rail the drum at its instrument range.
BFW_COND = 30.0


class SteamBoiler(ProcessUnit):
    code = "U700"
    name = "Steam boiler B1 and MP steam header"

    DRUM_VOLUME = 23.0
    DRUM_SPAN_MM = 600.0        # -300 to +300
    MP_HEADER_VOLUME = 340.0
    DUTY_MAX = 80.0             # t/h of steam
    AIR_MAX = 55.0              # kNm3/h
    SWELL_GAIN = -14.0          # mm per t/h step
    SWELL_TAU = 22.0
    STEAM_LINE_K = 14.0         # t/h per sqrt(bar) through the superheater: 40 t/h at 8 bar
    PARAMETER_META = {
        "DRUM_VOLUME": {"eu": "m3", "description": "B1 steam-drum effective inventory volume",
                        "lo": 0.1, "hi": 10000.0},
        "DRUM_SPAN_MM": {"eu": "mm", "description": "B1 indicated drum-level span",
                         "lo": 1.0, "hi": 10000.0},
        "MP_HEADER_VOLUME": {"eu": "m3", "description": "MP steam-header effective volume",
                             "lo": 0.1, "hi": 100000.0},
        "DUTY_MAX": {"eu": "t/h", "description": "B1 maximum steam generation",
                     "lo": 0.0, "hi": 10000.0},
        "AIR_MAX": {"eu": "kNm3/h", "description": "B1 maximum combustion-air flow",
                    "lo": 0.0, "hi": 10000.0},
        "SWELL_GAIN": {"eu": "mm/(t/h)", "description": "B1 shrink-and-swell inverse-response gain",
                       "lo": -10000.0, "hi": 10000.0},
        "SWELL_TAU": {"eu": "s", "description": "B1 shrink-and-swell lag",
                      "lo": 0.001, "hi": 100000.0},
        "STEAM_LINE_K": {"eu": "t/h/sqrt(bar)", "description": "B1 superheater line conductance",
                         "lo": 0.0, "hi": 10000.0},
    }

    def build(self) -> None:
        self.LT7001 = self.ai("LT-7001", "B1 steam drum level", "mm", -300, 300, 0.0)
        self.LT7002 = self.ai("LT-7002", "Deaerator level", "%", 0, 100, 55.0)
        self.PT7001 = self.ai("PT-7001", "B1 steam drum pressure", "barg", 0, 60, 44.0)
        self.PT7002 = self.ai("PT-7002", "MP steam header pressure", "barg", 0, 45, 36.0)
        self.PT7003 = self.ai("PT-7003", "B1 furnace draft", "mmH2O", -25, 10, -6.0)
        self.PT7004 = self.ai("PT-7004", "Deaerator pressure", "barg", 0, 5, 1.2)
        self.FT7001 = self.ai("FT-7001", "B1 steam flow to header", "t/h", 0, 70, 0.0)
        self.FT7002 = self.ai("FT-7002", "B1 feedwater flow", "t/h", 0, 80, 0.0)
        self.FT7003 = self.ai("FT-7003", "B1 fuel gas flow", "Nm3/h", 0, 5000, 0.0)
        self.FT7004 = self.ai("FT-7004", "B1 combustion air flow", "kNm3/h", 0, 55, 0.0)
        self.FT7005 = self.ai("FT-7005", "B1 fuel oil flow", "kg/h", 0, 3000, 0.0)
        self.FT7006 = self.ai("FT-7006", "Steam from B2 to header",
                              "t/h", 0, 150, 0.0)
        self.TT7001 = self.ai("TT-7001", "B1 superheated steam temperature",
                              "degC", 0, 450, 385.0)
        self.TT7002 = self.ai("TT-7002", "B1 feedwater temperature", "degC", 0, 200, 105.0)
        self.TT7003 = self.ai("TT-7003", "B1 flue gas temperature", "degC", 0, 500, 165.0)
        self.AT7001 = self.ai("AT-7001", "B1 flue gas oxygen", "mol%", 0, 21, 3.2)
        self.AT7002 = self.ai("AT-7002", "B1 flue gas carbon monoxide", "ppm", 0, 2000, 20.0)
        self.CT7001 = self.ai("CT-7001", "B1 steam drum conductivity",
                              "uS/cm", 0, 5000, 1800.0)
        self.IT7003 = self.ai("IT-7003", "B1 forced draft fan motor current",
                              "A", 0, 300, 0.0)
        self.ZT7001 = self.ai("ZT-7001", "FCV-7001 position feedback", "%", 0, 100, 0.0)

        self.ao("FCV-7001", "B1 feedwater control valve", value=42.0)
        self.ao("FCV-7002", "B1 fuel gas control valve", value=25.0)
        self.ao("FCV-7003", "B1 forced draft fan inlet damper", value=20.0)
        self.ao("FCV-7004", "B1 continuous blowdown valve", value=26.0)
        self.ao("FCV-7005", "B1 fuel oil control valve", value=0.0)
        self.ao("SC-7001", "B1 forced draft fan VFD speed reference", value=65.0)
        self.ao("SC-7002", "P-701A VFD speed reference", value=100.0)
        self.ao("PCV-7001", "MP steam header letdown valve", value=0.0)
        self.ao("TCV-7001", "B1 desuperheater spray valve", value=20.0)
        self.ao("LCV-7001", "Deaerator makeup water valve", value=40.0)

        self.di("BS-7001", "B1 main flame detected", "No flame", "Flame")
        self.di("LSLL-7001", "B1 steam drum level low low", "Normal", "Tripped")
        self.di("LSHH-7001", "B1 steam drum level high high", "Normal", "Tripped")
        self.di("PSHH-7001", "B1 steam drum pressure high high", "Normal", "Tripped")
        self.di("XS-7010", "B1 furnace purge complete permissive",
                "Not purged", "Purged", value=True)

        self.do("XY-7010", "B1 burner igniter energise command",
                "De-energise", "Energise", value=True)
        self.do("XY-7011", "B1 furnace purge sequence start command", "Idle", "Start")

        # ----------------------------------------------------------- equipment
        self.xv7001 = SdvPackage(self, "XV-7001", "B1 fuel gas shutdown valve",
                                 stroke=1.0)
        self.IT7001 = self.ai("IT-7001", "P-701A motor current", "A", 0, 300)
        self.IT7002 = self.ai("IT-7002", "P-701B motor current", "A", 0, 300)
        self.di("ZSO-HV7001", "HV-7001 open limit switch",
                "Not open", "Open", value=False)
        self.di("ZSC-HV7001", "HV-7001 closed limit switch",
                "Not closed", "Closed", value=True)
        self.bfw_pumps = PumpTrain(
            self, "P-701A", "P-701B", "B1 boiler feedwater pump",
            "MOV-7001A", "MOV-7001B", head_shutoff=760.0, flow_max=120.0,
            flow_min=12.0, rated_current=185.0, vfd_a=True, travel_time=20.0,
            duty_running=True)
        self.fd_fan = MotorPackage(self, "FD-701", "B1 forced draft fan",
                                   rated_current=280.0, vfd=True, start_delay=4.0)

        self.v_bfw = ControlValve("FCV-7001", cv_rated=110,
                                  char=ValveChar.EQUAL_PERCENT, stroke_time=5)
        self.v_fuel = ControlValve("FCV-7002", cv_rated=22, char=ValveChar.LINEAR,
                                   stroke_time=3)
        self.v_damper = ControlValve("FCV-7003", cv_rated=1400,
                                     char=ValveChar.LINEAR, stroke_time=15)
        # Oil dosing modelled as a first-order command-to-flow response,
        # 3000 kg/h at full stroke. The generic valve device carries a
        # position floor that made the flow DISCONTINUOUS (nothing
        # deliverable between zero and ~300 kg/h), and the flow PI then
        # bang-banged the trim on every scan - measured, twice, before
        # this became a lag.
        self.oil_lag = Lag(6.0, 0.0)
        # B2, the OTHER boiler: modelled as a pressure-supported steam
        # import that backstops the header just below B1's target. With
        # B1 holding 36 barg it stays invisible; base-load B1 and the
        # header settles where B2's support picks it up - which is how a
        # two-boiler site actually shares.
        self.b2_lag = Lag(45.0, 0.0)
        # Continuous blowdown is a SMALL valve: a couple of percent of
        # feedwater holds the drum at its cycles of concentration. Rated
        # at 12 it passed a fifth of the feedwater wide open at 8 %,
        # which is a boiler dumping its inventory down the drain.
        self.v_blowdown = ControlValve("FCV-7004", cv_rated=0.4,
                                       char=ValveChar.LINEAR, stroke_time=6)
        self.v_letdown = ControlValve("PCV-7001", cv_rated=190,
                                      char=ValveChar.LINEAR, stroke_time=4)
        self.v_spray = ControlValve("TCV-7001", cv_rated=22, char=ValveChar.LINEAR,
                                    stroke_time=4)
        self.v_da_makeup = ControlValve("LCV-7001", cv_rated=90,
                                        char=ValveChar.LINEAR, stroke_time=6)

        # -------------------------------------------------------------- states
        self.inventory = Integrator(0.0, -320.0, 320.0)     # mm of true level
        self.bfw_flow = Lag(2.0, 0.0)   # feedwater line inertia
        self.drum_pressure = Integrator(44.0 + P_STD, P_STD * 0.2, 62.0)
        self.header_pressure = Integrator(36.0 + P_STD, P_STD * 0.2, 48.0)
        self.da_level = Integrator(55.0, 0.0, 100.0)
        self.steam = Lag(12.0, 0.0)
        self.swell = LeadLag(0.0, self.SWELL_TAU, 0.0)
        self.superheat = Lag(45.0, 385.0)
        self.flue_temp = Lag(60.0, 165.0)
        self.o2 = Lag(6.0, 3.2)
        self.co = Lag(4.0, 20.0)
        self.conductivity = Integrator(1500.0, 0.0, 5000.0)
        self.lit = False
        self.tube_leak = 0.0

        self.tx_level = Transmitter("LT-7001", -300, 300, tau=1.0, noise_sigma_pct=0.6)
        self.tx_steam = Transmitter("FT-7001", 0, 70, tau=0.8, noise_sigma_pct=0.5)
        self.tx_o2 = Transmitter("AT-7001", 0, 21, tau=14.0, noise_sigma_pct=0.8)

        self._build_malfunctions()

    def _build_malfunctions(self) -> None:
        self.add_malfunction("MF-027", "P-701A", "Boiler feedwater pump trip",
                             "Rotating", "", 0, 1,
                             lambda a, v: setattr(self.bfw_pumps.motor_a.device,
                                                  "trip_on_overload", a))
        self.add_malfunction("MF-037", "B1", "Boiler tube leak", "Process",
                             "Leak rate", 0, 5,
                             lambda a, v: setattr(self, "tube_leak", v if a else 0.0))
        self.add_malfunction("MF-038", "U700", "MP steam header demand swing",
                             "Process", "Extra demand", 0, 20,
                             lambda a, v: self.bus.__setitem__(
                                 "mp_extra_demand", v if a else 0.0))

    # --------------------------------------------------------------------- step
    def step(self, dt: float) -> None:
        t = self.tags
        air = bool(self.bus.get("air_failure", 0.0))
        esd = bool(self.bus.get("esd_u700", 0.0))

        for valve, tag in ((self.v_bfw, "FCV-7001"), (self.v_fuel, "FCV-7002"),
                           (self.v_damper, "FCV-7003"), (self.v_blowdown, "FCV-7004"),
                           (self.v_letdown, "PCV-7001"), (self.v_spray, "TCV-7001"),
                           (self.v_da_makeup, "LCV-7001")):
            valve.step(dt, t[tag].effective, air)
        self.xv7001.step(dt, trip=esd, air_failure=air)

        # -------------------------------------------------------------- firing
        supply_bara = float(self.bus.get("b1_supply_pressure_bara", 6.0))
        fuel = (self.v_fuel.gas_flow(supply_bara, P_STD, 0.65, 300.0)
                if self.xv7001.is_open else 0.0)
        fuel = clamp(fuel, 0.0, 4000.0)
        self.bus["b1_fuel_demand"] = fuel

        low_pressure = supply_bara - P_STD < 1.2
        pilot = bool(t["XY-7010"].effective)
        if esd or not self.xv7001.is_open or low_pressure or fuel < 60.0:
            self.lit = False
        elif pilot and fuel > 80.0 and bool(t["XS-7010"].value):
            self.lit = True

        self.fd_fan.step(dt, permissive=not esd,
                         speed_ref=float(t["SC-7001"].effective), trip=esd)
        air_flow = (self.AIR_MAX * (self.v_damper.position / 100.0)
                    * (0.3 + 0.7 * self.fd_fan.speed / 100.0))
        stoich = (fuel * STOICH_AIR
                  + float(self.FT7005.value) * 11.4) / 1000.0
        excess = clamp(safe_div(air_flow - stoich, stoich, 1.0)
                       if stoich > 1e-6 else 1.0, -0.6, 3.0)
        eta = clamp(0.92 - 0.55 * (excess - 0.13) ** 2 - 0.16 * excess, 0.25, 0.92) \
            if excess >= 0.0 else clamp(0.62 + 1.6 * excess, 0.15, 0.92)

        lhv = float(self.bus.get("fg_lhv", 38.5))
        oil_cmd = clamp(float(t["FCV-7005"].effective), 0.0, 100.0)
        oil_kgh = self.oil_lag.step(
            30.0 * oil_cmd if self.lit else 0.0, dt)
        self.FT7005.set(clamp(oil_kgh, 0.0, 3000.0))
        duty_mw = ((fuel * lhv + oil_kgh * 41.0) / 3600.0 * eta
                   if self.lit else 0.0)
        steam_capacity = clamp(duty_mw / 20.5 * self.DUTY_MAX, 0.0, self.DUTY_MAX)

        # ------------------------------------------------------------ feedwater
        # Deaerator water is at its boiling point, so NPSH is static head only.
        self.tags["ZSO-HV7001"].set(False)
        self.tags["ZSC-HV7001"].set(True)
        self.IT7001.set(self.bfw_pumps.motor_a.current)
        self.IT7002.set(self.bfw_pumps.motor_b.current)
        self.bfw_pumps.step(dt, float(self.PT7004.value) + 0.1
                            + 1.4 * self.da_level.y / 100.0,
                            float(self.FT7002.value), self.da_level.y > 8.0,
                            float(self.tags["SC-7002"].effective),
                            # Suction line friction 0.25 bar at 60 t/h (was
                            # 0.8): deaerator water is at its boiling point, so
                            # the NPSH margin is the static head less this loss,
                            # and at 0.8 it crossed the 0.3 bar cavitation
                            # threshold at 50 t/h on a boiler rated 80 - the
                            # 72-hour soak's trip (docs/Refinery_Basis_Campaign.md).
                            float(self.PT7004.value), 0.25, 60.0, esd)
        # The pump curve falls with flow and the valve passes more with more
        # differential, so solving the two against last scan's flow is an
        # algebraic loop closed with a one scan lag. Its gain exceeds one when
        # the drum floods and the pair then oscillates between two solutions on
        # alternate scans. Carrying the flow in its own state instead gives the
        # water the inertia it physically has and settles the loop.
        p_bfw = self.bfw_pumps.discharge_pressure(self.bfw_flow.y, 950.0)
        feedwater = (self.v_bfw.flow(max(p_bfw - (self.drum_pressure.y - P_STD), 0.0),
                                     0.95) * 0.95
                     if self.bfw_pumps.any_running else 0.0)
        feedwater = self.bfw_flow.step(clamp(feedwater, 0.0, 80.0), dt)

        blowdown = self.v_blowdown.flow(
            max(self.drum_pressure.y - P_STD, 0.0), 0.9) * 0.9

        # ------------------------------------------------------ steam and header
        # Two connected volumes. Generation raises drum pressure, and steam leaves
        # the drum through the superheater against the header differential. Making
        # the outflow depend on that differential is what stops drum pressure from
        # integrating away: without it there is no path back down and the boiler
        # feedwater pumps eventually cannot overcome the drum.
        generated = self.steam.step(steam_capacity, dt)
        steam = self.STEAM_LINE_K * math.sqrt(
            max(self.drum_pressure.y - self.header_pressure.y, 0.0))
        steam = clamp(steam, 0.0, 90.0)

        reboiler_demand = (float(self.bus.get("t1_steam_demand", 0.0))
                           + float(self.bus.get("t2_steam_demand", 0.0)))
        extra = float(self.bus.get("mp_extra_demand", 0.0))
        letdown = self.v_letdown.gas_flow(self.header_pressure.y, P_STD,
                                          0.62, 640.0) * 0.0009
        header_out = reboiler_demand + extra + letdown

        b2 = self.b2_lag.step(
            clamp((35.2 - (self.header_pressure.y - P_STD)) * 40.0,
                  0.0, 150.0), dt)
        self.FT7006.set(b2)
        self.drum_pressure.step((generated - steam) * 0.055, dt)
        self.header_pressure.step((steam + b2 - header_out) * 0.055
                                  / self.MP_HEADER_VOLUME * 60.0, dt)
        self.bus["mp_steam_pressure_bara"] = self.header_pressure.y
        # The column reboilers size their surface duty against this, so a cold
        # header is a real constraint on them, not just a low pressure.
        self.bus["mp_steam_temperature"] = self.superheat.y

        # ------------------------------------------------- level, shrink & swell
        net_mass = feedwater - steam - blowdown - self.tube_leak
        inventory_before = self.inventory.y
        self.inventory.step(net_mass / self.DRUM_VOLUME * 22.0 / 60.0, dt)
        drum_accumulation = ((self.inventory.y - inventory_before)
                             / max(dt, 1e-12) * self.DRUM_VOLUME * 60.0 / 22.0)
        self.record_balance(
            "B1 drum mass inventory", feedwater,
            steam + blowdown + self.tube_leak, drum_accumulation,
            "t/h", tolerance=1e-5)
        # Inverse response: a rise in steaming rate expands the bubble volume and
        # pushes indicated level up while mass is actually falling.
        swell = self.swell.step(steam, dt)
        indicated = clamp(self.inventory.y + self.SWELL_GAIN * (steam - swell),
                          -320.0, 320.0)

        # ----------------------------------------------------------- deaerator
        makeup = self.v_da_makeup.flow(3.0, 1.0)
        da_before = self.da_level.y
        self.da_level.step((makeup - feedwater) / 22.0 * 100.0 / 60.0, dt)
        self.record_inventory_balance(
            "deaerator inventory", makeup, feedwater,
            da_before, self.da_level.y, 22.0, dt,
            time_base_s=60.0, eu="m3/min")

        # ------------------------------------------------------------ chemistry
        # Drum solids concentrate by the ratio of feedwater to blowdown -
        # the cycles of concentration - so the steady drum conductivity is
        # simply the feedwater's, times that ratio. Treated, deaerated
        # feedwater is the point: at BFW_COND the design 2 % blowdown
        # gives about 1500 uS/cm, under the 1800 the DCS holds and well
        # under the 3500 alarm. Shut the blowdown in and the cycles climb
        # with no limit but the instrument range, which is exactly the
        # lesson the alarm exists to teach.
        solids_in = feedwater * BFW_COND
        solids_out = blowdown * self.conductivity.y
        self.conductivity.step((solids_in - solids_out) * 0.5, dt)

        spray = self.v_spray.flow(max(p_bfw - (self.drum_pressure.y - P_STD), 0.0), 0.95)
        self.superheat.step(clamp(300.0 + duty_mw * 5.2 - spray * 3.4, 150.0, 445.0), dt)
        self.flue_temp.step(clamp(140.0 + duty_mw * 4.6 + excess * 40.0, 40.0, 495.0), dt)
        o2_ss = clamp(21.0 * excess / (1.0 + excess) * 0.95, 0.0, 12.0) if self.lit else 20.9
        co_ss = 20.0 if excess > 0.02 else clamp(60.0 + 5200.0 * (0.02 - excess),
                                                20.0, 2000.0)

        # ---------------------------------------------------------- instruments
        self.LT7001.set(self.tx_level.step(dt, indicated), Quality(self.tx_level.quality))
        self.LT7002.set(self.da_level.y)
        self.PT7001.set(clamp(self.drum_pressure.y - P_STD, 0, 60))
        self.PT7002.set(clamp(self.header_pressure.y - P_STD, 0, 45))
        self.PT7003.set(clamp(-2.0 - 0.09 * self.fd_fan.speed, -25, 10))
        self.PT7004.set(1.2)
        self.FT7001.set(self.tx_steam.step(dt, steam), Quality(self.tx_steam.quality))
        self.FT7002.set(feedwater)
        self.FT7003.set(fuel)
        self.FT7004.set(air_flow)
        self.TT7001.set(self.superheat.y)
        self.TT7002.set(105.0)
        self.TT7003.set(self.flue_temp.y)
        self.AT7001.set(self.tx_o2.step(dt, self.o2.step(o2_ss, dt)),
                        Quality(self.tx_o2.quality))
        self.AT7002.set(self.co.step(co_ss if self.lit else 0.0, dt))
        self.CT7001.set(self.conductivity.y)
        self.IT7003.set(self.fd_fan.current)
        self.ZT7001.set(self.v_bfw.position)

        t["BS-7001"].set(self.lit)
        t["LSLL-7001"].set(indicated < -200.0)
        t["LSHH-7001"].set(indicated > 200.0)
        t["PSHH-7001"].set(self.drum_pressure.y - P_STD > 52.0)
        t["XS-7010"].set(bool(t["XS-7010"].value) or bool(t["XY-7011"].effective))

    def save_state(self):
        return {"inv": self.inventory.y, "pd": self.drum_pressure.y,
                "ph": self.header_pressure.y, "steam": self.steam.y,
                "da": self.da_level.y, "lit": float(self.lit),
                "cond": self.conductivity.y, "bfw": self.bfw_pumps.state(),
                "oill": self.oil_lag.y, "b2": self.b2_lag.y}

    def load_state(self, s):
        self.inventory.reset(s.get("inv", 0.0))
        self.drum_pressure.reset(s.get("pd", 45.0))
        self.header_pressure.reset(s.get("ph", 37.0))
        self.steam.reset(s.get("steam", 0.0))
        self.da_level.reset(s.get("da", 55.0))
        self.conductivity.reset(s.get("cond", 1800.0))
        self.lit = bool(s.get("lit", 0.0))
        self.bfw_pumps.restore(s.get("bfw", {}))
        self.oil_lag.reset(s.get("oill", 0.0))
        self.b2_lag.reset(s.get("b2", 0.0))
