"""U200 - recycle gas compressor C1.

A variable speed centrifugal compressor returning separator off-gas to the
reactor loop. The point of the unit is the surge line: a compressor operating to
the left of it is not merely inefficient, it stalls violently, and anti-surge
control is the only thing standing between the machine and a rebuild.

Surge model
-----------
The surge line is a quadratic in corrected flow against polytropic head. The
distance from it is expressed as a **surge margin**, which is what the anti-surge
controller acts on:

    margin = (Q_operating - Q_surge) / Q_surge

Below zero the machine surges: flow reverses in bursts, discharge pressure
collapses and recovers at a few hertz, and vibration spikes. That is modelled as
an oscillation rather than a smooth curve, because a trainee needs to recognise
surge on a trend, not read about it.

Molecular weight matters. Lighter gas produces less head at the same speed, which
moves the surge line, which is why ``FY-2001`` compensates for it.
"""

from __future__ import annotations

import logging
import math

from ..core.devices import ControlValve, Transmitter, ValveChar
from ..core.dynamics import Integrator, Lag, clamp, safe_div, safe_sqrt
from ..core.tags import Quality
from .base import ProcessUnit
from .packages import MotorPackage, SdvPackage

log = logging.getLogger(__name__)

P_STD = 1.01325


class RecycleCompressor(ProcessUnit):
    code = "U200"
    name = "Recycle gas compressor C1"

    SUCTION_VOLUME = 16.0      # m3, V-201 knockout drum plus suction line
    DISCHARGE_VOLUME = 26.0    # m3
    RATED_SPEED = 12800.0      # rpm
    RATED_FLOW = 80.0          # kNm3/h, the machine as re-rated for the basis:
                               # FT-2001's span. The surge line was drawn for
                               # the original 40 while the plant ran 63 through
                               # it, which put the margin past 400 % (2026-09-08).
    HEAD_COEFF = 62.0          # kJ/kg at rated speed, zero flow
    FLOW_COEFF = 0.0035         # head loss per (kNm3/h)^2
    SURGE_SLOPE = 0.34         # surge flow as a fraction of rated at full speed
    CHOKE_FLOW = 138.0          # kNm3/h at full speed: stonewall, the right-hand
                               # limit of the map, set ~20 percent past the
                               # head-curve runout so it only bites when the
                               # resistance genuinely collapses.
    LINE_K = 8.7               # forward line to H1: kNm3/h per sqrt(bar) of
                               # discharge-to-heater differential, sized for
                               # ~15 kNm3/h at the 18 barg loop pressure
    PARAMETER_META = {
        "SUCTION_VOLUME": {"eu": "m3", "description": "C1 suction-system gas volume",
                           "lo": 0.1, "hi": 1000.0},
        "DISCHARGE_VOLUME": {"eu": "m3", "description": "C1 discharge-system gas volume",
                             "lo": 0.1, "hi": 1000.0},
        "RATED_FLOW": {"eu": "kNm3/h", "description": "C1 rated flow, the surge line's scale",
                       "lo": 1.0, "hi": 1000.0},
        "RATED_SPEED": {"eu": "rpm", "description": "C1 rated shaft speed",
                        "lo": 100.0, "hi": 100000.0},
        "HEAD_COEFF": {"eu": "kJ/kg", "description": "C1 rated-speed zero-flow head coefficient",
                       "lo": 0.0, "hi": 1000.0},
        "FLOW_COEFF": {"eu": "kJ/kg/(kNm3/h)^2", "description": "C1 map head-loss coefficient",
                       "lo": 0.0, "hi": 10.0},
        "SURGE_SLOPE": {"eu": "fraction", "description": "C1 rated-flow surge-line fraction",
                        "lo": 0.0, "hi": 1.0},
        "CHOKE_FLOW": {"eu": "kNm3/h", "description": "C1 rated-speed choke flow",
                       "lo": 1.0, "hi": 1000.0},
        "LINE_K": {"eu": "kNm3/h/sqrt(bar)", "description": "C1-to-H1 line conductance",
                   "lo": 0.0, "hi": 1000.0},
    }

    def build(self) -> None:
        self.PT2001 = self.ai("PT-2001", "C1 suction pressure", "barg", 0, 20, 8.0)
        self.PT2002 = self.ai("PT-2002", "C1 discharge pressure", "barg", 0, 60, 12.0)
        self.PT2003 = self.ai("PT-2003", "C1 lube oil header pressure", "barg", 0, 6)
        self.PDT2001 = self.ai("PDT-2001", "C1 suction filter differential",
                               "mbar", 0, 200, 25.0)
        self.TT2001 = self.ai("TT-2001", "C1 suction temperature", "degC", 0, 150, 45.0)
        self.TT2002 = self.ai("TT-2002", "C1 discharge temperature", "degC", 0, 220, 60.0)
        self.TT2003 = self.ai("TT-2003", "C1 drive end bearing temperature",
                              "degC", 0, 150, 55.0)
        self.TT2004 = self.ai("TT-2004", "C1 non-drive end bearing temperature",
                              "degC", 0, 150, 54.0)
        self.TT2005 = self.ai("TT-2005", "C1 lube oil supply temperature",
                              "degC", 0, 100, 45.0)
        self.FT2001 = self.ai("FT-2001", "C1 suction flow", "kNm3/h", 0, 80)
        self.FT2002 = self.ai("FT-2002", "C1 anti-surge recycle flow", "kNm3/h", 0, 40)
        self.FT2003 = self.ai("FT-2003", "H2 makeup flow to C1 suction",
                              "kNm3/h", 0, 30)
        self.PT2004 = self.ai("PT-2004", "H2 makeup header pressure",
                              "barg", 0, 40, 25.0)
        self.ST2001 = self.ai("ST-2001", "C1 speed feedback", "rpm", 0, 14000)
        self.IT2001 = self.ai("IT-2001", "C1 motor current", "A", 0, 800)
        self.IT2002 = self.ai("IT-2002", "P-201 main lube oil pump current",
                              "A", 0, 60)
        self.IT2003 = self.ai("IT-2003", "P-202 auxiliary lube oil pump current",
                              "A", 0, 60)
        self.VT2001 = self.ai("VT-2001", "C1 radial vibration", "micron", 0, 150, 18.0)
        self.AT2001 = self.ai("AT-2001", "Recycle gas molecular weight",
                              "kg/kmol", 4, 40, 14.0)
        self.LT2001 = self.ai("LT-2001", "V-201 knockout drum level", "%", 0, 100, 35.0)
        self.ZT2001 = self.ai("ZT-2001", "FCV-2001 anti-surge valve position",
                              "%", 0, 100, 100.0)
        self.UY2001 = self.ai("UY-2001", "C1 surge margin", "%", -50, 300, 60.0)
        self.JT2001 = self.ai("JT-2001", "C1 shaft power", "MW", 0, 3, 0.0)
        self.GT2001 = self.ai("GT-2001", "C1 inlet guide-vane angle feedback",
                              "deg", 0, 40, 0.0)

        self.ao("SC-2001", "C1 VFD speed reference", value=70.0)
        self.ao("FCV-2001", "C1 anti-surge recycle valve", value=100.0)
        self.ao("PCV-2003", "H2 makeup valve", value=0.0)
        # The four-handle classroom: capacity can be moved by speed, the
        # suction throttle, the discharge throttle, or the inlet guide
        # vanes - each with a different gain, character and cost.
        self.ao("GV-2001", "C1 inlet guide-vane angle demand", value=0.0)
        self.ao("FCV-2004", "C1 discharge throttle valve", value=100.0)
        self.ao("FCV-2002", "C1 suction throttle valve", value=100.0)
        self.ao("FCV-2003", "C1 discharge cooler cooling water valve", value=55.0)
        self.ao("LCV-2001", "V-201 knockout drum drain valve", value=30.0)

        self.di("VSHH-2001", "C1 vibration high high trip", "Normal", "Tripped")
        self.di("LSHH-2001", "V-201 knockout drum level high high", "Normal", "Tripped")
        self.di("PSL-2001", "C1 lube oil pressure low", "Normal", "Tripped")
        self.di("UA-2001", "C1 surge detected", "Normal", "Surging")

        # ---------------------------------------------------------- equipment
        self.motor = MotorPackage(self, "C1", "Recycle gas compressor",
                                  rated_current=760.0, vfd=True, start_delay=6.0)
        self.lube_main = MotorPackage(self, "P-201", "C1 main lube oil pump", 32.0)
        self.lube_aux = MotorPackage(self, "P-202", "C1 auxiliary lube oil pump", 32.0)
        self.xv2001 = SdvPackage(self, "XV-2001", "C1 suction shutdown valve",
                                 stroke=4.0)

        self.antisurge = ControlValve("FCV-2001", cv_rated=540, char=ValveChar.LINEAR,
                                      stroke_time=1.5, fail_closed=False)
        self.suction_throttle = ControlValve("FCV-2002", cv_rated=780,
                                             char=ValveChar.LINEAR, stroke_time=8,
                                             fail_closed=False)
        self.cooler_cw = ControlValve("FCV-2003", cv_rated=320, char=ValveChar.LINEAR,
                                      stroke_time=8, fail_closed=False)
        self.ko_drain = ControlValve("LCV-2001", cv_rated=30, char=ValveChar.LINEAR,
                                     stroke_time=4)
        self.makeup_valve = ControlValve("PCV-2003", cv_rated=60,
                                         char=ValveChar.LINEAR, stroke_time=6,
                                         fail_closed=True)
        self.discharge_throttle = ControlValve("FCV-2004", cv_rated=780,
                                               char=ValveChar.LINEAR,
                                               stroke_time=8, fail_closed=False)
        self.guide_vanes = Lag(6.0, 0.0)       # vane actuator, degrees

        # ------------------------------------------------------------- states
        self.p_suction = Integrator(8.0 + P_STD, P_STD * 0.2, 25.0)
        self.p_discharge = Integrator(12.0 + P_STD, P_STD * 0.2, 62.0)
        self.ko_level = Integrator(35.0, 0.0, 100.0)
        self.flow = Lag(1.2, 0.0)
        self.t_discharge = Lag(25.0, 60.0)
        self.bearing = Lag(180.0, 55.0)
        self.lube_pressure = Lag(3.0, 0.0)
        self.vibration = Lag(2.0, 18.0)
        self.surge_phase = 0.0
        self.surging = False
        self.fouling_pct = 0.0
        self.lube_decay = False
        # H2 makeup header: a battery-limit supply, instructor adjustable
        self.h2_header = 25.0          # barg
        self.mw_lag = Lag(90.0, 12.0)  # suction MW answers the blend slowly

        self.tx_flow = Transmitter("FT-2001", 0, 80, tau=0.6, noise_sigma_pct=0.6)
        self.tx_pdis = Transmitter("PT-2002", 0, 60, tau=0.3, noise_sigma_pct=0.2)
        self.tx_vib = Transmitter("VT-2001", 0, 150, tau=0.4, noise_sigma_pct=1.2)

        self._build_malfunctions()

    def _build_malfunctions(self) -> None:
        self.add_malfunction("MF-006", "FCV-2001", "Slow anti-surge valve stroke",
                             "Valve", "Stroke time", 1, 30,
                             lambda a, v: self.antisurge.__setattr__(
                                 "_rl", self.antisurge._rl) or
                             setattr(self.antisurge, "stroke_time", v if a else 1.5))
        self.add_malfunction("MF-025", "C1", "Compressor fouling", "Rotating",
                             "Efficiency loss", 0, 25,
                             lambda a, v: setattr(self, "fouling_pct", v if a else 0.0))
        self.add_malfunction("MF-026", "C1", "Lube oil pressure decay", "Rotating",
                             "", 0, 1,
                             lambda a, v: setattr(self, "lube_decay", a))
        self.add_malfunction("MF-043", "PCV-2003", "H2 header pressure low",
                             "Process", "Header pressure", 5, 25,
                             lambda a, v: setattr(self, "h2_header",
                                                  v if a else 25.0))

    # ------------------------------------------------------------------ physics
    def _surge_flow(self, speed_frac: float, mw: float,
                    gv_frac: float = 1.0) -> float:
        """Surge flow in kNm3/h. Lighter gas moves the line to the right;
        closing the inlet guide vanes moves it favourably left, which is
        why IGVs are the premium turndown handle."""
        mw_factor = math.sqrt(clamp(mw / 14.0, 0.3, 3.0))
        return (self.SURGE_SLOPE * self.RATED_FLOW * speed_frac / mw_factor
                * (0.70 + 0.30 * gv_frac))

    def step(self, dt: float) -> None:
        t = self.tags
        air = bool(self.bus.get("air_failure", 0.0))
        esd = bool(self.bus.get("esd_u200", 0.0))

        # ------------------------------------------------------------ lube oil
        self.lube_main.step(dt, permissive=True)
        self.lube_aux.step(dt, permissive=True)
        oil_target = 4.2 if (self.lube_main.running or self.lube_aux.running) else 0.0
        if self.lube_decay:
            oil_target *= 0.35
        oil = self.lube_pressure.step(oil_target, dt)
        oil_ok = oil > 1.8

        # --------------------------------------------------------------- valves
        self.antisurge.step(dt, t["FCV-2001"].effective, air)
        self.suction_throttle.step(dt, t["FCV-2002"].effective, air)
        self.cooler_cw.step(dt, t["FCV-2003"].effective, air)
        self.ko_drain.step(dt, t["LCV-2001"].effective, air)
        self.makeup_valve.step(dt, t["PCV-2003"].effective, air)
        self.discharge_throttle.step(dt, t["FCV-2004"].effective, air)
        gv_deg = self.guide_vanes.step(
            clamp(float(t["GV-2001"].effective), 0.0, 40.0), dt)
        # vanes at 0 deg pass everything; 40 deg cuts head hard
        gv_frac = 1.0 - 0.55 * gv_deg / 40.0
        self.xv2001.step(dt, trip=esd, air_failure=air)

        # ---------------------------------------------------------- the machine
        # Surge deliberately does not gate the permissive. A surging machine is
        # still a running machine; it is the vibration trip that stops it. Using
        # surge as a permissive produces a start-stop oscillation that no real
        # compressor does and that hides the fault from the trainee.
        permissive = (oil_ok and self.xv2001.is_open and self.ko_level.y < 85.0)
        trip = esd or bool(t["VSHH-2001"].value) or bool(t["LSHH-2001"].value)
        self.motor.step(dt, permissive, float(t["SC-2001"].effective), trip)
        speed_frac = self.motor.speed / 100.0

        mw = float(self.AT2001.value)
        ps = self.p_suction.y
        pd = self.p_discharge.y

        head = (self.HEAD_COEFF * (1.0 - self.fouling_pct / 100.0)
                * speed_frac ** 2 * gv_frac
                * math.sqrt(clamp(14.0 / max(mw, 1.0), 0.3, 3.0)))
        required = max((pd / max(ps, 0.05)) - 1.0, 0.0) * 55.0
        # Flow rides down the head curve until developed head matches what the
        # system demands. Solved explicitly and lagged, which is unconditionally
        # stable where an implicit solve would chatter near surge.
        q_target = safe_sqrt(safe_div(max(head - required, 0.0), self.FLOW_COEFF, 0.0))
        q_target *= self.suction_throttle.position / 100.0
        q_target *= self.discharge_throttle.position / 100.0
        # Choke: volumetric flow through the machine caps at stonewall, which
        # scales with speed. Opening the anti-surge wide at low discharge
        # resistance therefore stops paying at some point, as it does in life.
        q_target = min(q_target, self.CHOKE_FLOW * speed_frac)
        flow = self.flow.step(q_target if speed_frac > 0.05 else 0.0, dt)

        q_surge = self._surge_flow(speed_frac, mw, gv_frac)
        recycle = (self.antisurge.gas_flow(pd, ps, mw / 28.96, 330.0) / 1000.0
                   if speed_frac > 0.05 else 0.0)
        total_through = flow + recycle
        # The motor sees the gas load, so the ammeter follows the operating
        # point: light at low flow, heavy toward choke. IT-2001 becomes a
        # diagnostic instead of a speed curve.
        self.motor.device.load_frac = clamp(
            safe_div(total_through, self.CHOKE_FLOW * max(speed_frac, 0.05), 1.0),
            0.10, 1.40)

        margin = (safe_div(total_through - q_surge, q_surge, 1.5)
                  if q_surge > 1e-6 else 1.5)
        self.surging = bool(speed_frac > 0.15 and margin < 0.0)

        if self.surging:
            # Surge is a limit cycle at a few hertz, not a smooth degradation.
            self.surge_phase += dt * 2.0 * math.pi * 3.0
            pulse = math.sin(self.surge_phase)
            flow *= 0.25 * pulse
            head *= 0.55 + 0.3 * pulse

        # ------------------------------------------------------------ pressures
        offgas_in = float(self.bus.get("d3_offgas_to_c1", 0.0)) / 1000.0
        demand = float(self.bus.get("quench_and_makeup_demand", 0.0)) / 1000.0
        # H2 makeup from the battery-limit header into the suction drum:
        # this is what replaces the hydrogen the reactor consumes.
        makeup = (self.makeup_valve.gas_flow(self.h2_header + P_STD, ps,
                                             2.016 / 28.96, 310.0) / 1000.0
                  if not esd else 0.0)
        # Forward gas to the heater mix (the Whitehouse routing): the
        # line to H1 flows on the discharge-to-heater differential and
        # carries the recycle hydrogen around with the charge.
        to_h1 = (self.LINE_K * safe_sqrt(max(pd - 12.0, 0.0))
                 if speed_frac > 0.05 else 0.0)
        d_ps = ((offgas_in + makeup + recycle - flow) * 1000.0 * P_STD
                / (3600.0 * self.SUCTION_VOLUME))
        self.p_suction.step(d_ps, dt)
        d_pd = ((flow - recycle - demand - to_h1) * 1000.0 * P_STD
                / (3600.0 * self.DISCHARGE_VOLUME))
        self.p_discharge.step(d_pd, dt)

        # Suction conditions come from the separator overhead. The gas MW
        # is the blend of the offgas (lighter as conversion rises) with
        # the near-pure hydrogen makeup - which is why cutting the makeup
        # visibly moves the surge line.
        self.TT2001.set(clamp(float(self.bus.get("d3_liquid_temperature", 55.0))
                              * 0.55 + 18.0, 5.0, 95.0))
        mw_off = clamp(18.0 - 0.06 * float(self.bus.get("r1_conversion", 60.0)),
                       4.0, 40.0)
        inflow = max(offgas_in + makeup, 1e-6)
        mw_mix = (offgas_in * mw_off + makeup * 2.016) / inflow
        self.AT2001.set(clamp(self.mw_lag.step(mw_mix, dt), 4.0, 40.0))
        self.fault_flag = self.motor.device.faulted

        # -------------------------------------------------------- temperatures
        ratio = clamp(self.p_discharge.y / max(self.p_suction.y, 0.05), 1.0, 6.0)
        t_suc = float(self.TT2001.value)
        t_poly = (t_suc + 273.15) * ratio ** 0.28 - 273.15
        cw_dp = float(self.bus.get("cooling_water_dp_bar", 2.4))
        cw_supply = float(self.bus.get("cooling_water_temperature", 28.0))
        cw_flow = self.cooler_cw.flow(cw_dp, 1.0)
        cw_design = 0.865 * self.cooler_cw.cv_rated * math.sqrt(2.4)
        cooling = clamp(cw_flow / max(cw_design, 1.0), 0.0, 1.5)
        t_dis = self.t_discharge.step(
            clamp(t_poly - cooling * 0.45 * (t_poly - (cw_supply + 12.0)),
                  20.0, 215.0), dt)
        gas_kg_s = max(total_through, 0.0) * 1000.0 * max(mw, 1.0) / 22.414 / 3600.0
        cw_duty = clamp(gas_kg_s * 2.1 * max(t_poly - t_dis, 0.0), 0.0, 50000.0)
        self.bus["u200_cw_flow"] = cw_flow
        self.bus["u200_cw_duty"] = cw_duty

        # ------------------------------------------------------ knockout drum
        condensate = max(0.0, (t_suc - 35.0)) * 0.02 + (0.6 if self.surging else 0.0)
        drained = self.ko_drain.flow(max(self.p_suction.y - P_STD, 0.0), 0.7)
        ko_before = self.ko_level.y
        self.ko_level.step((condensate - drained) / 11.0 * 100.0 / 60.0, dt)
        self.record_inventory_balance(
            "V-201 liquid inventory", condensate, drained,
            ko_before, self.ko_level.y, 11.0, dt,
            time_base_s=60.0, eu="m3/min")

        vib_target = 18.0 + (70.0 if self.surging else 0.0) + self.fouling_pct * 1.1
        vib = self.vibration.step(vib_target if self.motor.running else 3.0, dt)
        bearing = self.bearing.step(
            55.0 + (28.0 if self.motor.running else 0.0)
            + (0.0 if oil_ok else 45.0) + (12.0 if self.surging else 0.0), dt)

        # ------------------------------------------------------------ published
        self.bus["c1_discharge_pressure"] = self.p_discharge.y
        self.bus["c1_recycle_gas_flow"] = max(flow, 0.0) * 1000.0
        self.bus["c1_running"] = 1.0 if self.motor.running else 0.0
        self.bus["recycle_gas_to_h1"] = max(to_h1, 0.0) * 1000.0
        self.bus["recycle_gas_mw"] = float(self.AT2001.value)

        self.PT2001.set(clamp(self.p_suction.y - P_STD, 0.0, 20.0))
        self.PT2002.set(self.tx_pdis.step(dt, clamp(self.p_discharge.y - P_STD, 0, 60)),
                        Quality(self.tx_pdis.quality))
        self.PT2003.set(oil)
        # 25 mbar clean plus 40 at the 65 kNm3/h design flow, square law
        self.PDT2001.set(clamp(25.0 + 40.0 * (max(flow, 0.0) / 65.0) ** 2, 0, 200))
        self.TT2002.set(t_dis)
        self.TT2003.set(bearing)
        self.TT2004.set(bearing - 1.5)
        self.TT2005.set(45.0 if (self.lube_main.running or self.lube_aux.running) else 30.0)
        self.FT2001.set(self.tx_flow.step(dt, max(flow, 0.0)),
                        Quality(self.tx_flow.quality))
        self.FT2002.set(clamp(recycle, 0.0, 40.0))
        self.FT2003.set(clamp(makeup, 0.0, 30.0))
        self.PT2004.set(clamp(self.h2_header, 0.0, 40.0))
        self.ST2001.set(speed_frac * self.RATED_SPEED)
        self.IT2001.set(self.motor.current)
        self.IT2002.set(self.lube_main.current)
        self.IT2003.set(self.lube_aux.current)
        self.VT2001.set(self.tx_vib.step(dt, vib), Quality(self.tx_vib.quality))
        self.LT2001.set(self.ko_level.y)
        self.ZT2001.set(self.antisurge.position)
        self.UY2001.set(clamp(margin * 100.0, -50.0, 300.0))
        self.GT2001.set(gv_deg)
        # Shaft power follows the operating point: gas load times head.
        self.JT2001.set(clamp(2.2 * self.motor.device.load_frac
                              * speed_frac ** 2
                              * (1.0 if self.motor.running else 0.0), 0.0, 3.0))

        t["VSHH-2001"].set(vib > 110.0)
        t["LSHH-2001"].set(self.ko_level.y > 85.0)
        t["PSL-2001"].set(not oil_ok)
        t["UA-2001"].set(self.surging)

    # -------------------------------------------------------------- persistence
    def save_state(self):
        return {"ps": self.p_suction.y, "pd": self.p_discharge.y,
                "ko": self.ko_level.y, "flow": self.flow.y,
                "run": float(self.motor.running),
                "lube": float(self.lube_main.running),
                "mwl": self.mw_lag.y, "gv": self.guide_vanes.y}

    def load_state(self, s):
        self.p_suction.reset(s.get("ps", 9.0))
        self.p_discharge.reset(s.get("pd", 13.0))
        self.ko_level.reset(s.get("ko", 35.0))
        self.flow.reset(s.get("flow", 0.0))
        self.motor.device.running = bool(s.get("run", 0.0))
        self.lube_main.device.running = bool(s.get("lube", 0.0))
        self.mw_lag.reset(s.get("mwl", 12.0))
        self.guide_vanes.reset(s.get("gv", 0.0))
