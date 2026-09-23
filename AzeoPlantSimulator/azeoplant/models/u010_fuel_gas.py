"""U010 - fuel gas header and utilities.

The shared header is the piece that makes the flowsheet behave like a plant
rather than a set of independent exercises. H1 and B1 both draw on it, D3 off-gas
feeds into it, and import makes up the difference. Fire the boiler harder and the
heater's fuel pressure falls.

Pressure model
--------------
Isothermal ideal gas in a fixed volume. Contained normal volume is
``N = P_abs * V / P_std``, so

    dP_abs/dt = (F_in - F_out) * P_std / (3600 * V)

with flows in Nm3/h. Integration is bounded, so no combination of valve positions
can produce a negative absolute pressure or a divergent one.
"""

from __future__ import annotations

import logging
import math

from ..core.devices import ControlValve, Transmitter, ValveChar
from ..core.dynamics import Integrator, Lag, clamp
from ..core.tags import Quality
from .base import ProcessUnit
from .packages import MotorPackage, MovPackage, PumpTrain, SdvPackage

log = logging.getLogger(__name__)

P_STD = 1.01325          # bara
SUPPLY_PRESSURE = 32.0   # bara at the battery limit


class FuelGasHeader(ProcessUnit):
    code = "U010"
    name = "Fuel gas header and utilities"

    HEADER_VOLUME = 60.0     # m3
    LHV_NOMINAL = 38.5       # MJ/Nm3
    CW_BASIN_VOLUME = 600.0  # m3 at 100 percent indicated level
    CW_DESIGN_FLOW = 2600.0  # m3/h, plant cooling-water circulation
    CW_OTHER_FLOW = 620.0    # m3/h, minor users outside the five explicit exchangers
    CW_TOWER_KAV_L = 1.20    # clean tower characteristic at design L/G
    PARAMETER_META = {
        "HEADER_VOLUME": {"eu": "m3", "description": "Fuel-gas header gas volume",
                          "lo": 1.0, "hi": 1000.0},
        "LHV_NOMINAL": {"eu": "MJ/Nm3", "description": "Nominal fuel-gas lower heating value",
                        "lo": 1.0, "hi": 80.0},
        "CW_BASIN_VOLUME": {"eu": "m3", "description": "CT1 basin volume at 100 percent",
                            "lo": 50.0, "hi": 5000.0},
        "CW_DESIGN_FLOW": {"eu": "m3/h", "description": "CT1 design circulating-water flow",
                           "lo": 500.0, "hi": 10000.0},
        "CW_OTHER_FLOW": {"eu": "m3/h", "description": "Minor cooling-water users at design pressure",
                          "lo": 0.0, "hi": 5000.0},
        "CW_TOWER_KAV_L": {"eu": "-", "description": "Clean CT1 tower characteristic",
                           "lo": 0.1, "hi": 5.0},
    }

    def build(self) -> None:
        # ------------------------------------------------------------- outputs
        self.PT0101 = self.ai("PT-0101", "Natural gas header pressure", "barg", 0, 25)
        self.PT0102 = self.ai("PT-0102", "Fuel gas supply to H1", "barg", 0, 15)
        self.PT0103 = self.ai("PT-0103", "Fuel gas supply to B1", "barg", 0, 15)
        self.PT0104 = self.ai("PT-0104", "Instrument air header pressure", "barg", 0, 10, 7.0)
        self.FT0101 = self.ai("FT-0101", "Natural gas import flow", "Nm3/h", 0, 8000)
        self.FT0102 = self.ai("FT-0102", "D3 off-gas to fuel header", "Nm3/h", 0, 3000)
        self.TT0101 = self.ai("TT-0101", "Fuel gas header temperature", "degC", 0, 80, 25.0)
        self.AT0101 = self.ai("AT-0101", "Fuel gas lower heating value", "MJ/Nm3", 18, 46,
                              self.LHV_NOMINAL)
        self.AT0102 = self.ai("AT-0102", "Fuel gas specific gravity", "-", 0.15, 1.2, 0.65)
        self.TT0102 = self.ai("TT-0102", "Cooling water supply temperature", "degC", 0, 45, 28.0)

        self.PSLL = self.di("PSLL-0101", "Fuel gas header pressure low low",
                            "Normal", "Tripped")
        self.PSHH = self.di("PSHH-0101", "Fuel gas header pressure high high",
                            "Normal", "Tripped")
        self.PSL_AIR = self.di("PSL-0102", "Instrument air pressure low",
                               "Normal", "Tripped")
        self.PSL_CW = self.di("PSL-0103", "Cooling water supply pressure low",
                              "Normal", "Tripped")
        self.LSLL_CW = self.di("LSLL-0101", "CT1 basin level low low",
                               "Normal", "Tripped")
        self.LSHH_CW = self.di("LSHH-0101", "CT1 basin level high high",
                               "Normal", "Tripped")

        # -------------------------------------------------------------- inputs
        self.ao("PCV-0101", "Natural gas import control valve", value=46.0)
        self.ao("PCV-0102", "Fuel gas header relief to flare")
        self.ao("PCV-0103", "Fuel gas pressure reduction to H1", value=40.0)
        self.ao("PCV-0104", "Fuel gas pressure reduction to B1", value=30.0)
        self.ao("FCV-0101", "D3 off-gas to fuel header valve", value=60.0)
        self.ao("SC-0101", "Cooling water circulation pump speed reference", value=100.0)
        self.ao("SC-0102", "CT1 common fan speed reference", value=45.0)
        self.ao("LCV-0101", "CT1 basin makeup water valve", value=46.0)
        self.ao("FCV-0102", "CT1 conductivity blowdown valve", value=15.0)

        # ----------------------------------------------------------- equipment
        self.v_import = ControlValve("PCV-0101", cv_rated=12, char=ValveChar.LINEAR,
                                     stroke_time=4)
        self.v_flare = ControlValve("PCV-0102", cv_rated=20, char=ValveChar.LINEAR,
                                    stroke_time=2)
        self.v_h1 = ControlValve("PCV-0103", cv_rated=14, char=ValveChar.LINEAR,
                                 stroke_time=3)
        self.v_b1 = ControlValve("PCV-0104", cv_rated=8, char=ValveChar.LINEAR,
                                 stroke_time=3)
        self.v_offgas = ControlValve("FCV-0101", cv_rated=60, char=ValveChar.LINEAR,
                                     stroke_time=3)
        self.v_cw_makeup = ControlValve("LCV-0101", cv_rated=35,
                                        char=ValveChar.LINEAR, stroke_time=8)
        self.v_cw_blowdown = ControlValve("FCV-0102", cv_rated=30,
                                          char=ValveChar.LINEAR, stroke_time=8)
        self.mov0101 = MovPackage(self, "MOV-0101",
                                  "Fuel gas header battery limit isolation",
                                  travel_time=25.0, position=100.0)

        # -------------------------------------------------------------- states
        self.pressure = Integrator(y0=16.0 + P_STD, lo=P_STD * 0.2, hi=40.0)
        self.b1_header = Integrator(y0=5.0 + P_STD, lo=P_STD * 0.2, hi=20.0)
        self.lhv = Lag(30.0, self.LHV_NOMINAL)
        self.lhv_target = self.LHV_NOMINAL
        self.lhv_import = self.LHV_NOMINAL   # NG import quality
        self.import_available = True
        self.air_failed = False
        self.air_comp_lost = False
        # The receiver is what buys the operator time: losing the compressor
        # is a two minute countdown, not a light switch. PSL-0102 fires first,
        # and the valves let go only once the header falls out of their spring
        # range, by which point an operator who read the alarm has options.
        self.air_receiver = Integrator(7.0, 0.0, 8.5)

        self.tx_pt0101 = Transmitter("PT-0101", 0, 25, tau=0.4, noise_sigma_pct=0.15)
        self.tx_ft0101 = Transmitter("FT-0101", 0, 8000, tau=0.8, noise_sigma_pct=0.4)
        self.tx_ft0102 = Transmitter("FT-0102", 0, 3000, tau=0.8, noise_sigma_pct=0.5)
        self.FT0103 = self.ai("FT-0103", "Cooling water supply flow", "m3/h",
                              0, 5000, 2600.0)
        self.tx_ft0103 = Transmitter("FT-0103", 0, 5000, tau=2.0,
                                     noise_sigma_pct=0.4)
        self.TT0103 = self.ai("TT-0103", "Cooling water return temperature",
                              "degC", 0, 80, 36.0)
        self.PT0105 = self.ai("PT-0105", "Cooling water supply header pressure",
                              "barg", 0, 8, 2.4)
        self.LT0101 = self.ai("LT-0101", "CT1 basin level", "%", 0, 100, 65.0)
        self.AT0103 = self.ai("AT-0103", "CT1 basin conductivity", "uS/cm",
                              0, 3000, 800.0)
        self.TT0104 = self.ai("TT-0104", "Ambient wet-bulb temperature",
                              "degC", -30, 50, 22.0)
        self.TT0105 = self.ai("TT-0105", "Ambient dry-bulb temperature",
                              "degC", -30, 70, 30.0)
        self.FT0104 = self.ai("FT-0104", "CT1 makeup water flow", "m3/h",
                              0, 100, 24.0)
        self.FT0105 = self.ai("FT-0105", "CT1 blowdown flow", "m3/h",
                              0, 100, 6.0)
        self.IT0101 = self.ai("IT-0101", "CT1 fan A motor current", "A", 0, 300)
        self.IT0102 = self.ai("IT-0102", "CT1 fan B motor current", "A", 0, 300)
        self.IT0103 = self.ai("IT-0103", "P-011A cooling water pump current", "A", 0, 500)
        self.IT0104 = self.ai("IT-0104", "P-011B cooling water pump current", "A", 0, 500)

        self.cw_pumps = PumpTrain(
            self, "P-011A", "P-011B", "Cooling water circulation pump",
            "MOV-0111A", "MOV-0111B", head_shutoff=70.0, flow_max=4025.0,
            flow_min=700.0, rated_current=360.0, vfd_a=True,
            travel_time=20.0, duty_running=True)
        self.ct_fan_a = MotorPackage(self, "CT-011A", "CT1 cell A induced-draft fan",
                                     rated_current=180.0, vfd=True, start_delay=3.0)
        self.ct_fan_b = MotorPackage(self, "CT-011B", "CT1 cell B induced-draft fan",
                                     rated_current=180.0, vfd=True, start_delay=3.0)
        for fan in (self.ct_fan_a, self.ct_fan_b):
            fan.device.running = True
            fan.device._cmd_start = True
            fan.cmd_start.value = True
            fan.device.speed_pct = 45.0
            fan.device._speed_lag.reset(45.0)

        # The supply and return headers have real thermal inventory.  The
        # downstream users publish their flow and duty after U010 has stepped;
        # CT1 therefore sees the previous 100 ms scan, a deliberate tear stream.
        self.cw_supply_temp = Lag(180.0, 28.0)
        self.cw_return_temp = Lag(60.0, 36.0)
        self.cw_header_pressure = Lag(4.0, 2.4)
        self.cw_basin_level = Integrator(65.0, 0.0, 100.0)
        initial_volume = self.CW_BASIN_VOLUME * self.cw_basin_level.y / 100.0
        self.cw_solids = Integrator(800.0 * initial_volume, 0.0,
                                    5000.0 * self.CW_BASIN_VOLUME)
        self.tower_fouling_pct = 0.0
        self.wet_bulb_offset = 0.0
        self.fan_capacity_pct = 100.0
        self.xv_import = SdvPackage(self, "XV-0101",
                                    "Fuel gas header battery limit",
                                    stroke=3.0)

        self._build_malfunctions()

    def _build_malfunctions(self) -> None:
        def _lhv(active, value):
            # the swing acts on the IMPORT quality; the header value is
            # the flow-weighted blend with the offgas
            self.lhv_import = value if active else self.LHV_NOMINAL

        def _no_import(active, _value):
            self.import_available = not active

        def _air(active, _value):
            self.air_comp_lost = active

        def _tower_fouling(active, value):
            self.tower_fouling_pct = value if active else 0.0

        def _hot_weather(active, value):
            self.wet_bulb_offset = value if active else 0.0

        def _pump_wear(active, value):
            wear = value if active else 0.0
            self.cw_pumps.pump_a.wear_pct = wear
            self.cw_pumps.pump_b.wear_pct = wear

        def _fan_capacity(active, value):
            self.fan_capacity_pct = value if active else 100.0

        self.add_malfunction("MF-028", "U010", "Fuel gas heating value swing",
                             "Process", "Heating value", 30.0, 45.0, _lhv)
        self.add_malfunction("MF-029", "U010", "Loss of natural gas import",
                             "Process", "", 0, 1, _no_import)
        self.add_malfunction("MF-041", "U010", "Instrument air failure",
                             "Utility", "", 0, 1, _air)
        self.add_malfunction("MF-044", "CT1", "Cooling tower fill fouling",
                             "Utility", "UA loss", 0, 80, _tower_fouling)
        self.add_malfunction("MF-045", "CT1", "High ambient wet-bulb temperature",
                             "Utility", "Wet-bulb rise", 0, 12, _hot_weather)
        self.add_malfunction("MF-046", "P-011A/B", "Cooling water pump wear",
                             "Utility", "Head loss", 0, 60, _pump_wear)
        self.add_malfunction("MF-047", "CT1", "Cooling tower fan air-side restriction",
                             "Utility", "Air capacity", 20, 100, _fan_capacity)

    def _step_cooling_water(self, dt: float, air_failure: bool) -> None:
        """Advance CT1, its basin, pumps and the common cooling-water headers."""
        t = self.tags
        self.v_cw_makeup.step(dt, t["LCV-0101"].effective, air_failure)
        self.v_cw_blowdown.step(dt, t["FCV-0102"].effective, air_failure)

        consumer_flows = (
            float(self.bus.get("u200_cw_flow", 235.0)),
            float(self.bus.get("u400_cw_flow", 240.0)),
            float(self.bus.get("t1_cw_flow", 1120.0)),
            float(self.bus.get("t2_cw_flow", 170.0)),
            float(self.bus.get("u800_cw_flow", 180.0)),
        )
        consumer_duties = (
            float(self.bus.get("u200_cw_duty", 1800.0)),
            float(self.bus.get("u400_cw_duty", 10500.0)),
            float(self.bus.get("t1_cw_duty", 6500.0)),
            float(self.bus.get("t2_cw_duty", 1500.0)),
            float(self.bus.get("u800_cw_duty", 900.0)),
        )
        dp = max(self.cw_header_pressure.y, 0.0)
        other_flow = self.CW_OTHER_FLOW * math.sqrt(dp / 2.4) if dp > 0.0 else 0.0
        # Spell the additions out so the Python and native cores use the same
        # floating-point evaluation order (Python 3.12+ ``sum`` compensates
        # float error, while the C++ expression deliberately follows plant
        # calculation order).
        total_flow = clamp(consumer_flows[0] + consumer_flows[1]
                           + consumer_flows[2] + consumer_flows[3]
                           + consumer_flows[4] + other_flow, 0.0, 5000.0)
        # The unshown small users return about six degrees warmer at design.
        other_duty = other_flow * 997.0 * 4.18 * 6.0 / 3600.0
        total_duty = clamp(consumer_duties[0] + consumer_duties[1]
                           + consumer_duties[2] + consumer_duties[3]
                           + consumer_duties[4] + other_duty, 0.0, 150000.0)

        level = self.cw_basin_level.y
        pump_speed = clamp(float(t["SC-0101"].effective), 0.0, 100.0)
        self.cw_pumps.step(dt, suction_base=0.45, flow=total_flow,
                           primed=level > 4.0, speed_ref=pump_speed,
                           vapour_pressure=0.04, friction=0.05,
                           design_flow=self.CW_DESIGN_FLOW)
        discharge = self.cw_pumps.discharge_pressure(total_flow, density=997.0)
        dp_target = max(discharge - 0.45, 0.0) if self.cw_pumps.any_running else 0.0
        dp = self.cw_header_pressure.step(clamp(dp_target, 0.0, 8.0), dt)

        fan_sp = clamp(float(t["SC-0102"].effective), 0.0, 100.0)
        self.ct_fan_a.step(dt, True, fan_sp)
        self.ct_fan_b.step(dt, True, fan_sp)
        for fan in (self.ct_fan_a, self.ct_fan_b):
            fan.device.load_frac = ((fan.speed / 100.0) ** 3
                                    if fan.running else 1.0)

        circulating = total_flow if self.cw_pumps.any_running and level > 0.0 else 0.0
        supply = self.cw_supply_temp.y
        delta_t = (total_duty * 3600.0 /
                   max(circulating * 997.0 * 4.18, 1.0)) if circulating > 1.0 else 0.0
        return_target = clamp(supply + delta_t, supply, 80.0)
        return_temp = self.cw_return_temp.step(return_target, dt)

        wet_bulb = clamp(float(self.bus.get("ambient_wet_bulb", 22.0))
                         + self.wet_bulb_offset, -30.0, 50.0)
        dry_bulb = clamp(float(self.bus.get("ambient_dry_bulb", 30.0)),
                         wet_bulb, 70.0)
        air_fraction = (((self.ct_fan_a.speed / 100.0) ** 0.8
                         if self.ct_fan_a.running else 0.0)
                        + ((self.ct_fan_b.speed / 100.0) ** 0.8
                           if self.ct_fan_b.running else 0.0)) / 2.0
        air_fraction *= self.fan_capacity_pct / 100.0
        water_fraction = circulating / self.CW_DESIGN_FLOW
        if circulating > 1.0 and air_fraction > 1e-4:
            lg_relative = water_fraction / air_fraction
            ntu = (self.CW_TOWER_KAV_L
                   * (1.0 - clamp(self.tower_fouling_pct, 0.0, 95.0) / 100.0)
                   * max(lg_relative, 0.05) ** -0.6)
            cold_target = wet_bulb + max(return_temp - wet_bulb, 0.0) * math.exp(-ntu)
        else:
            cold_target = return_temp
        supply = self.cw_supply_temp.step(clamp(cold_target, wet_bulb, 80.0), dt)

        tower_range = max(return_temp - supply, 0.0)
        evaporation = 0.00085 * circulating * tower_range
        drift = 0.0002 * circulating
        makeup = self.v_cw_makeup.flow(3.0, 1.0)
        blowdown = self.v_cw_blowdown.flow(2.4, 1.0) if self.cw_pumps.any_running else 0.0
        level_rate = ((makeup - blowdown - evaporation - drift)
                      / self.CW_BASIN_VOLUME * 100.0 / 3600.0)
        level = self.cw_basin_level.step(level_rate, dt)

        volume = max(self.CW_BASIN_VOLUME * level / 100.0, 1.0)
        conductivity = clamp(self.cw_solids.y / volume, 0.0, 5000.0)
        solids_rate = (makeup * 200.0 - (blowdown + drift) * conductivity) / 3600.0
        self.cw_solids.step(solids_rate, dt)
        conductivity = clamp(self.cw_solids.y / volume, 0.0, 5000.0)

        self.bus["cooling_water_temperature"] = supply
        self.bus["cooling_water_dp_bar"] = dp
        self.TT0102.set(supply)
        self.TT0103.set(return_temp)
        self.PT0105.set(dp)
        self.LT0101.set(level)
        self.AT0103.set(conductivity)
        self.TT0104.set(wet_bulb)
        self.TT0105.set(dry_bulb)
        self.FT0103.set(self.tx_ft0103.step(dt, circulating),
                        Quality(self.tx_ft0103.quality))
        self.FT0104.set(makeup)
        self.FT0105.set(blowdown)
        self.IT0101.set(self.ct_fan_a.current)
        self.IT0102.set(self.ct_fan_b.current)
        self.IT0103.set(self.cw_pumps.motor_a.current)
        self.IT0104.set(self.cw_pumps.motor_b.current)
        self.PSL_CW.set(dp < 1.2)
        self.LSLL_CW.set(level < 10.0)
        self.LSHH_CW.set(level > 92.0)

    # --------------------------------------------------------------------- step
    def step(self, dt: float) -> None:
        # Consumption bleeds the receiver down when the compressor is lost;
        # recovery repressurises it in about half a minute.
        rate = -0.055 if self.air_comp_lost else 0.30
        air_p = self.air_receiver.step(rate if abs(self.air_receiver.y - 7.0) > 1e-6
                                       or self.air_comp_lost else 0.0, dt)
        air_p = min(air_p, 7.0)
        if not self.air_comp_lost and self.air_receiver.y > 7.0:
            self.air_receiver.reset(7.0)
        self.air_failed = air_p < 3.0
        air = self.air_failed
        self.PT0104.set(air_p)
        self.PSL_AIR.set(air_p < 5.5)

        self._step_cooling_water(dt, air)

        p_abs = self.pressure.y
        b1_abs = self.b1_header.y

        self.v_import.step(dt, self.tags["PCV-0101"].effective, air)
        self.v_flare.step(dt, self.tags["PCV-0102"].effective, air)
        self.v_h1.step(dt, self.tags["PCV-0103"].effective, air)
        self.v_b1.step(dt, self.tags["PCV-0104"].effective, air)
        self.v_offgas.step(dt, self.tags["FCV-0101"].effective, air)

        sg = float(self.AT0102.value)
        t_k = float(self.TT0101.value) + 273.15

        self.mov0101.step(dt)
        self.xv_import.step(dt)
        f_import = (self.v_import.gas_flow(SUPPLY_PRESSURE, p_abs, sg, t_k)
                    * self.mov0101.fraction
                    * self.xv_import.position / 100.0
                    if self.import_available else 0.0)
        f_flare = self.v_flare.gas_flow(p_abs, P_STD, sg, t_k)

        # Off-gas availability is set by the reactor section; the valve throttles it.
        offgas_available = float(self.bus.get("d3_offgas_available", 900.0))
        f_offgas = min(offgas_available,
                       self.v_offgas.gas_flow(p_abs + 1.5, p_abs, sg, t_k))

        # Draw to the fired units. H1 has its own burner header, modelled in U300.
        h1_burner_abs = float(self.bus.get("h1_burner_pressure_bara", 4.0))
        f_to_h1 = self.v_h1.gas_flow(p_abs, h1_burner_abs, sg, t_k)
        f_to_b1 = self.v_b1.gas_flow(p_abs, b1_abs, sg, t_k)

        # B1 is not modelled in this build; it is represented as a demand that
        # draws its header down, so the shared-header interaction is real.
        b1_demand = float(self.bus.get("b1_fuel_demand", 1500.0))
        d_b1 = (f_to_b1 - b1_demand) * P_STD / (3600.0 * 25.0)
        self.b1_header.step(d_b1, dt)

        net = f_import + f_offgas - f_flare - f_to_h1 - f_to_b1
        self.pressure.step(net * P_STD / (3600.0 * self.HEADER_VOLUME), dt)

        self.bus["fg_header_pressure_bara"] = self.pressure.y
        self.bus["b1_supply_pressure_bara"] = self.b1_header.y
        self.bus["fg_to_h1_flow"] = f_to_h1
        self.bus["fg_lhv"] = self.lhv.step(self.lhv_target, dt)

        # ---------------------------------------------------------- instruments
        p_barg = self.pressure.y - P_STD
        self.PT0101.set(self.tx_pt0101.step(dt, p_barg), Quality(self.tx_pt0101.quality))
        self.PT0102.set(clamp(h1_burner_abs - P_STD, 0.0, 15.0))
        self.PT0103.set(clamp(self.b1_header.y - P_STD, 0.0, 15.0))
        self.FT0101.set(self.tx_ft0101.step(dt, f_import), Quality(self.tx_ft0101.quality))
        self.FT0102.set(self.tx_ft0102.step(dt, f_offgas), Quality(self.tx_ft0102.quality))
        self.AT0101.set(self.lhv.y)
        # Header temperature follows ambient with a little Joule-Thomson cooling
        # across the import valve; specific gravity tracks the fuel composition,
        # which is what the heating value swing malfunction actually changes.
        self.TT0101.set(clamp(25.0 - 0.004 * f_import, 5.0, 60.0))
        # Specific gravity is the honest BLEND, not a heating-value
        # proxy: the offgas share carries the reactor's hydrogen (its MW
        # follows conversion), so an SG-to-HV inference on the paraffin
        # line reads WRONG exactly when the header runs offgas-rich -
        # hydrogen sits far off that line, which is the whole lesson.
        # The offgas is hydrogen-plus-light-ends off the reactor (MW
        # follows conversion) carrying a CO2 share - the component that
        # sits OFF the paraffin line and puts a real error in any
        # SG-to-HV inference. Heating value is blended the same
        # flow-weighted way, so SG and HV are two views of ONE gas.
        mw_hc = clamp(18.0 - 0.06 * float(self.bus.get("r1_conversion",
                                                       60.0)), 4.0, 40.0)
        co2 = 0.08
        mw_off = mw_hc * (1.0 - co2) + 44.0 * co2
        lhv_off = (10.8 + (mw_hc - 2.0) * 25.0 / 14.0) * (1.0 - co2)
        tot = max(f_offgas + f_import, 1.0)
        sg_blend = (f_offgas * mw_off / 28.96 + f_import * 0.61) / tot
        self.AT0102.set(clamp(sg_blend, 0.15, 1.2))
        self.lhv_target = clamp(
            (f_offgas * lhv_off + f_import * self.lhv_import) / tot,
            18.0, 46.0)
        self.PSLL.set(p_barg < 5.0)
        self.PSHH.set(p_barg > 22.0)

    # -------------------------------------------------------------- persistence
    def save_state(self):
        # lhv_target is computed late in the step and consumed early in
        # the next: a restored plant must resume with the SAME pending
        # value or its first scan runs on the build default.
        return {"p": self.pressure.y, "b1": self.b1_header.y,
                "lhv": self.lhv.y, "lhvi": self.lhv_import,
                "lhvt": self.lhv_target,
                "ct_foul": self.tower_fouling_pct,
                "wb_off": self.wet_bulb_offset,
                "fan_cap": self.fan_capacity_pct}

    def load_state(self, state):
        self.pressure.reset(state.get("p", 17.0))
        self.b1_header.reset(state.get("b1", 6.0))
        self.lhv.reset(state.get("lhv", self.LHV_NOMINAL))
        self.lhv_import = state.get("lhvi", self.LHV_NOMINAL)
        self.lhv_target = state.get("lhvt", self.LHV_NOMINAL)
        self.tower_fouling_pct = state.get("ct_foul", 0.0)
        self.wet_bulb_offset = state.get("wb_off", 0.0)
        self.fan_capacity_pct = state.get("fan_cap", 100.0)
