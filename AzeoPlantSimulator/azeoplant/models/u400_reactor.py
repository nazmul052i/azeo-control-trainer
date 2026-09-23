"""U400 - reactor R1 and separator D3.

The hardest control problem in the flowsheet. R1 is a two-bed exothermic fixed
bed reactor with interbed quench, and it is **open loop unstable** over part of
its range: conversion rises with temperature, conversion releases heat, and heat
raises temperature. Past a point the positive feedback outruns the quench and the
bed runs away.

Runaway
-------
Bed temperature follows a lumped energy balance:

    M·cp·dT/dt = F·cp·(T_in − T) + (−ΔH)·r(T)·V − quench

with ``r(T)`` an Arrhenius rate. The activation energy is what makes the loop
gain rise with temperature, and it is why ``TIC-4001`` needs a much tighter tune
at high severity than at low. Losing quench flow with the beds hot produces a
genuine excursion, not a slow drift: MF-033 is worth running once on every
course.

The product analyser ``AT-4001`` updates every seven minutes and nothing else
measures product quality directly. That combination — an unstable process behind
a discontinuous measurement — is the argument for inferential control and dead
time compensation in one unit.

Separator D3 is an isothermal flash with a water interface. Its off-gas is split
between the fuel header and the compressor suction, which is where this unit
reaches back into U010 and U200.
"""

from __future__ import annotations

import logging
import math

from ..core.devices import ControlValve, Transmitter, ValveChar
from ..core.dynamics import DeadTime, Integrator, Lag, clamp, safe_div
from ..core.tags import Quality
from .base import ProcessUnit
from .packages import SdvPackage

log = logging.getLogger(__name__)

P_STD = 1.01325
R_GAS = 8.314


class ReactorSection(ProcessUnit):
    code = "U400"
    name = "Reactor R1 and separator D3"

    BED_VOLUME = 35.0          # m3 per bed
    BED_MASS_CP = 7.8e4        # kJ/K, catalyst plus metal per bed
    DH_RXN = 1850.0            # kJ per kg converted, exothermic
    EA = 78000.0               # J/mol
    K0 = 3625.0                # 1/s, tuned for about 50 % conversion at 350 degC
    REACTIVE_FRACTION = 0.05   # mass fraction of the feed that reacts
    GAS_TO_OIL = 0.17          # design recycle-H2 rate, kNm3/h per m3/h feed
    H2_CONS_K = 0.075          # H2 consumed, kNm3/h per m3/h feed at full conv
    D3_VOLUME = 60.0           # m3
    D3_AREA = 13.0              # m2
    D3_WATER_FRAC = 0.012    # share of the charge that is water, to the D3 boot
    PARAMETER_META = {
        "D3_WATER_FRAC": {"eu": "fraction", "description": "Water fraction of the charge that settles to the D3 interface",
                          "lo": 0.0, "hi": 0.05},
        "BED_VOLUME": {"eu": "m3", "description": "Catalyst volume per R1 bed",
                       "lo": 0.1, "hi": 10000.0},
        "BED_MASS_CP": {"eu": "kJ/K", "description": "Thermal capacitance per R1 bed",
                        "lo": 1.0, "hi": 1e9},
        "DH_RXN": {"eu": "kJ/kg", "description": "Reaction heat release per converted mass",
                   "lo": 0.0, "hi": 1e6},
        "EA": {"eu": "J/mol", "description": "Lumped reaction activation energy",
               "lo": 0.0, "hi": 1e7},
        "K0": {"eu": "1/s", "description": "Lumped Arrhenius pre-exponential factor",
               "lo": 0.0, "hi": 1e12},
        "REACTIVE_FRACTION": {"eu": "fraction", "description": "Reactive fraction of liquid feed",
                              "lo": 0.0, "hi": 1.0},
        "GAS_TO_OIL": {"eu": "kNm3/m3", "description": "Design recycle-gas to charge ratio",
                       "lo": 0.0, "hi": 100.0},
        "H2_CONS_K": {"eu": "kNm3/m3", "description": "Hydrogen consumption at full conversion",
                      "lo": 0.0, "hi": 100.0},
        "D3_VOLUME": {"eu": "m3", "description": "D3 vapour-space volume",
                      "lo": 0.1, "hi": 10000.0},
        "D3_AREA": {"eu": "m2", "description": "D3 liquid cross-sectional area",
                    "lo": 0.1, "hi": 10000.0},
    }

    def build(self) -> None:
        self.TT4001 = self.ai("TT-4001", "R1 inlet temperature", "degC", 0, 500, 107.0)
        self.TT4002 = self.ai("TT-4002", "R1 bed 1 temperature", "degC", 0, 550, 120.0)
        self.TT4003 = self.ai("TT-4003", "R1 bed 2 temperature", "degC", 0, 550, 118.0)
        self.TT4004 = self.ai("TT-4004", "R1 outlet temperature", "degC", 0, 550, 118.0)
        self.TT4005 = self.ai("TT-4005", "D3 separator temperature", "degC", 0, 250, 55.0)
        self.PT4001 = self.ai("PT-4001", "R1 inlet pressure", "barg", 0, 60, 42.0)
        self.PT4002 = self.ai("PT-4002", "D3 separator pressure", "barg", 0, 55, 39.0)
        self.PDT4001 = self.ai("PDT-4001", "R1 catalyst bed differential pressure",
                               "bar", 0, 5, 0.9)
        self.FT4001 = self.ai("FT-4001", "R1 quench gas flow", "kNm3/h", 0, 40)
        self.FT4002 = self.ai("FT-4002", "D3 off-gas flow", "Nm3/h", 0, 3000)
        self.FT4003 = self.ai("FT-4003", "D3 liquid to T1", "m3/h", 0, 200)
        self.FT4004 = self.ai("FT-4004", "D3 sour water draw", "m3/h", 0, 20)
        self.FT4005 = self.ai("FT-4005", "R1 additive injection flow",
                              "L/h", 0, 60, 25.0)
        self.LT4001 = self.ai("LT-4001", "D3 separator liquid level", "%", 0, 100, 50.0)
        self.LT4002 = self.ai("LT-4002", "D3 separator interface level", "%", 0, 100, 30.0)
        self.AT4001 = self.ai("AT-4001", "R1 product impurity", "ppm", 0, 500, 90.0)
        self.AT4002 = self.ai("AT-4002", "D3 off-gas hydrogen sulphide",
                              "mol%", 0, 5, 1.2)
        self.XY4010 = self.ai("XI-4001", "R1 conversion", "%", 0, 100, 0.0)

        self.ao("FCV-4001", "R1 quench gas control valve", value=35.0)
        self.ao("FCV-4002", "R1 effluent cooler cooling water valve", value=60.0)
        self.ao("LCV-4001", "D3 liquid level valve to T1", value=45.0)
        self.ao("LCV-4002", "D3 interface sour water valve", value=15.0)
        self.ao("PCV-4001", "D3 separator pressure control valve", value=20.0)
        self.ao("FCV-4003", "R1 additive injection valve", value=42.0)

        self.di("PSHH-4001", "R1 pressure high high", "Normal", "Tripped")
        # one switch per bed for the SIS to vote, and the voted result
        # the P&ID and the schedule name
        self.di("TSHH-4001A", "R1 bed 1 temperature high high",
                "Normal", "Tripped")
        self.di("TSHH-4001B", "R1 bed 2 temperature high high",
                "Normal", "Tripped")
        self.di("TSHH-4001", "R1 catalyst bed temperature high high",
                "Normal", "Tripped")
        self.di("LSLL-4001", "D3 separator level low low", "Normal", "Tripped")
        self.di("LSHH-4001", "D3 separator level high high", "Normal", "Tripped")

        self.xv4001 = SdvPackage(self, "XV-4001", "R1 feed shutdown valve", stroke=3.0)
        self.xv4002 = SdvPackage(self, "XV-4002", "D3 off-gas shutdown valve", stroke=2.0)
        self.bdv4001 = SdvPackage(self, "BDV-4001", "R1 emergency depressuring valve",
                                  stroke=2.0, fail_open=True, initially_open=False)

        self.quench = ControlValve("FCV-4001", cv_rated=360, char=ValveChar.LINEAR,
                                   stroke_time=3, fail_closed=False)
        self.cooler = ControlValve("FCV-4002", cv_rated=300, char=ValveChar.LINEAR,
                                   stroke_time=8, fail_closed=False)
        # Additive injection: the reactor's QUALITY handle. The dosing
        # rate steers selectivity toward light make, and the effect
        # reaches the T1 analyser through the whole D3/column holdup -
        # which is what makes it the deadtime-compensation laboratory.
        self.v_add = ControlValve("FCV-4003", cv_rated=0.06,
                                  char=ValveChar.LINEAR, stroke_time=4)
        self.add_lag = Lag(240.0, 25.0 / 90.0)
        # the fresh feed's own light key, arriving through H1 and the beds
        self.fresh_lag = Lag(900.0, 0.12)
        self.add_dead = DeadTime(360.0, self.dt, 25.0 / 90.0)
        self.lcv4001 = ControlValve("LCV-4001", cv_rated=260,
                                    char=ValveChar.EQUAL_PERCENT, stroke_time=5)
        # The water draw is ranged 0-20 m3/h.  Cv 35 passed about 190 m3/h
        # across D3's normal pressure drop, leaving the healthy loop below
        # one percent stem and turning ordinary transmitter noise into a
        # draw/no-draw cycle.  Size the valve to the instrumented service.
        self.lcv4002 = ControlValve("LCV-4002", cv_rated=3.5, char=ValveChar.LINEAR,
                                     stroke_time=5)
        self.pcv4001 = ControlValve("PCV-4001", cv_rated=8, char=ValveChar.LINEAR,
                                    stroke_time=3)

        self.bed1 = Integrator(120.0, 0.0, 620.0)
        self.bed2 = Integrator(118.0, 0.0, 620.0)
        self.pressure = Integrator(42.0 + P_STD, P_STD * 0.2, 62.0)
        self.d3_pressure = Integrator(39.0 + P_STD, P_STD * 0.2, 58.0)
        self.d3_level = Integrator(50.0, 0.0, 100.0)
        self.d3_interface = Integrator(30.0, 0.0, 100.0)
        self.d3_temp = Lag(120.0, 55.0)
        self.impurity_dead = DeadTime(180.0, self.dt, 90.0)
        self.conversion = Lag(30.0, 0.0)
        self.activity = 100.0
        self.quench_failed = False
        self.fouled_pct = 0.0

        self.tx_bed1 = Transmitter("TT-4002", 0, 550, tau=8.0, noise_sigma_pct=0.1)
        # An impurity analyser's repeatability is stated at the ppm level,
        # not as a share of its 500 ppm span: 1.5 % of span was 7.5 ppm
        # of noise on a reading that sits near 5 ppm, and once a day a
        # sample landed under-range, went Uncertain, and shed AIC-4001 to
        # Man for one cycle (24 h soak, 2026-09-05).
        self.tx_impurity = Transmitter("AT-4001", 0, 500, tau=20.0,
                                       transport=30.0,
                                       noise_sigma_pct=0.2, update_period=420.0)
        self.tx_interface = Transmitter("LT-4002", 0, 100, tau=3.0,
                                        noise_sigma_pct=0.25)
        self.tx_level = Transmitter("LT-4001", 0, 100, tau=1.5, noise_sigma_pct=0.4)

        self._build_malfunctions()

    def _build_malfunctions(self) -> None:
        from ..core.devices import TxFailure
        self.add_malfunction("MF-017", "LT-4002", "Interface level measurement noise",
                             "Transmitter", "Sigma", 0, 15,
                             lambda a, v: setattr(self.tx_interface,
                                                  "noise_sigma_pct", v if a else 0.25))
        self.add_malfunction("MF-020", "AT-4001", "Analyser sample line plugged",
                             "Analyser", "", 0, 1,
                             lambda a, v: setattr(self.tx_impurity, "failure",
                                                  TxFailure.FROZEN if a else TxFailure.NONE))
        self.add_malfunction("MF-032", "R1", "Catalyst deactivation", "Process",
                             "Activity", 40, 100,
                             lambda a, v: setattr(self, "activity", v if a else 100.0))
        self.add_malfunction("MF-033", "R1", "Reaction runaway on quench loss",
                             "Process", "", 0, 1,
                             lambda a, v: setattr(self, "quench_failed", a))
        self.add_malfunction("MF-034", "R1", "Catalyst bed fouling", "Process",
                             "dP increase", 0, 60,
                             lambda a, v: setattr(self, "fouled_pct", v if a else 0.0))

    # ------------------------------------------------------------------ kinetics
    def _conversion(self, temp_c: float, volumetric_flow_m3s: float) -> float:
        """Fractional conversion of the reactive component across one bed.

        First order plug flow: ``X = 1 - exp(-k tau)``. Bounded in [0, 1) by
        construction, so no combination of temperature and flow can produce a
        conversion above unity and a negative remaining mass.
        """
        t_k = clamp(temp_c + 273.15, 250.0, 900.0)
        k = self.K0 * math.exp(-self.EA / (R_GAS * t_k)) * (self.activity / 100.0)
        tau = safe_div(self.BED_VOLUME, max(volumetric_flow_m3s, 1e-5), 0.0)
        return clamp(1.0 - math.exp(-clamp(k * tau, 0.0, 30.0)), 0.0, 0.995)

    def step(self, dt: float) -> None:
        t = self.tags
        air = bool(self.bus.get("air_failure", 0.0))
        esd = bool(self.bus.get("esd_u400", 0.0))
        depressure = bool(self.bus.get("esd_depressure", 0.0))

        self.quench.step(dt, t["FCV-4001"].effective, air)
        self.cooler.step(dt, t["FCV-4002"].effective, air)
        self.v_add.step(dt, t["FCV-4003"].effective, air)
        self.lcv4001.step(dt, t["LCV-4001"].effective, air)
        self.lcv4002.step(dt, t["LCV-4002"].effective, air)
        self.pcv4001.step(dt, t["PCV-4001"].effective, air)
        self.xv4001.step(dt, trip=esd, air_failure=air)
        self.xv4002.step(dt, trip=esd, air_failure=air)
        # The depressuring cause de-energises the BDV, which fails open. Loss
        # of instrument air does the same, which is what fail-open means.
        self.bdv4001.step(dt, trip=depressure, air_failure=air)

        feed = float(self.bus.get("charge_flow", 0.0)) * self.xv4001.fraction
        t_in = float(self.bus.get("h1_outlet_temperature", 107.0))
        self.TT4001.set(t_in)

        # Recycle hydrogen arrives WITH the feed, heated through H1 (the
        # Whitehouse routing): the compressor's forward flow, in kNm3/h.
        # The reaction needs it - below the design gas-to-oil ratio the
        # hydrogen partial pressure starves and conversion falls away.
        gas_in = float(self.bus.get("recycle_gas_to_h1", 0.0)) / 1000.0
        h2_avail = clamp(safe_div(gas_in, max(feed, 1.0) * self.GAS_TO_OIL,
                                  1.0), 0.35, 1.0) ** 0.5

        mass_kg_s = feed * 780.0 / 3600.0
        cp = 2.35

        # ------------------------------------------------------------- quench
        gas_available = float(self.bus.get("c1_discharge_pressure", 13.0))
        quench_flow = 0.0
        if not self.quench_failed:
            quench_flow = self.quench.gas_flow(max(gas_available, self.pressure.y + 1.0),
                                               self.pressure.y, 0.5, 320.0) / 1000.0
        quench_flow = clamp(quench_flow, 0.0, 40.0)
        self.bus["quench_and_makeup_demand"] = quench_flow * 1000.0

        # -------------------------------------------------------------- bed 1
        q_m3s = max(feed, 0.1) / 3600.0
        reactive = mass_kg_s * self.REACTIVE_FRACTION
        conv1 = self._conversion(self.bed1.y, q_m3s) * h2_avail
        heat1 = reactive * conv1 * self.DH_RXN
        d_t1 = (mass_kg_s * cp * (t_in - self.bed1.y) + heat1) / self.BED_MASS_CP
        self.bed1.step(d_t1, dt)

        # quench between the beds cools the stream entering bed 2
        quench_duty = quench_flow * 1000.0 / 3600.0 * 1.3 * 0.35
        t_mid = self.bed1.y
        if mass_kg_s > 0.5:
            t_mid = clamp(self.bed1.y - safe_div(quench_duty, mass_kg_s * cp, 0.0),
                          0.0, 620.0)

        remaining = reactive * (1.0 - conv1)
        conv2 = self._conversion(self.bed2.y, q_m3s) * h2_avail
        heat2 = remaining * conv2 * self.DH_RXN
        d_t2 = (mass_kg_s * cp * (t_mid - self.bed2.y) + heat2) / self.BED_MASS_CP
        self.bed2.step(d_t2, dt)

        # Catalyst dies by sintering, and sintering is thermal: a bed held
        # hot loses activity at a rate that compounds, which is why end of run
        # means creeping the inlet temperature up to hold conversion while the
        # TSHH margin shrinks. Regeneration is a turnaround, not a button.
        hot = max(max(self.bed1.y, self.bed2.y) - 400.0, 0.0)
        self.activity = clamp(
            self.activity - (0.02 + 0.5 * hot / 50.0) / 3600.0 * dt, 20.0, 100.0)

        overall = conv1 + (1.0 - conv1) * conv2
        conversion = clamp(overall * 100.0, 0.0, 99.5)
        self.conversion.step(conversion, dt)
        impurity_true = clamp(500.0 * math.exp(-0.055 * self.conversion.y), 1.0, 500.0)

        # ---------------------------------------------------------- pressures
        make_up = feed * 0.9
        relief = 4200.0 * self.bdv4001.fraction
        # The reactor rides on the separator it discharges into: loop
        # pressure is D3 plus the bed and line losses, with the flow
        # imbalance still moving it dynamically (quench loss raises it,
        # relief and depressuring drop it fast). Without the anchor the
        # integrator walks to its own ceiling and parks the pressure at a
        # value nothing upstream could ever have delivered, which is what
        # kept PSHH-4001 standing in the old lineup.
        anchor = self.d3_pressure.y + float(self.PDT4001.value) + 1.8
        d_p = ((make_up * 8.0 + quench_flow * 1000.0 - relief
                - float(self.bus.get("d3_offgas_total", 0.0)))
               * P_STD / (3600.0 * 120.0))
        d_p += (anchor - self.pressure.y) / 60.0
        self.pressure.step(d_p, dt)
        self.PDT4001.set(clamp(0.9 * (1.0 + self.fouled_pct / 100.0)
                               * (1.0 + (feed / 130.0) ** 2) / 2.0, 0.0, 5.0))

        # ----------------------------------------------------------- separator
        t_effluent = self.bed2.y
        self.bus["r1_effluent_temperature"] = t_effluent
        cw_dp = float(self.bus.get("cooling_water_dp_bar", 2.4))
        cw_supply = float(self.bus.get("cooling_water_temperature", 28.0))
        cw_flow = self.cooler.flow(cw_dp, 1.0)
        cw_design = 0.865 * self.cooler.cv_rated * math.sqrt(2.4)
        cooling = clamp(cw_flow / max(cw_design, 1.0), 0.0, 1.5)
        cool_target = clamp(
            t_effluent - cooling * 0.82 * (t_effluent - (cw_supply + 12.0)),
            20.0, 195.0)
        self.d3_temp.step(cool_target, dt)
        cw_duty = clamp(mass_kg_s * cp * max(t_effluent - cool_target, 0.0),
                        0.0, 100000.0)
        self.bus["u400_cw_flow"] = cw_flow
        self.bus["u400_cw_duty"] = cw_duty

        # The flash follows the separator temperature: a hotter D3 sends
        # more overhead and less liquid, so the E-401 cooling water valve is
        # coupled to the fuel gas header and through it to every burner on
        # site. Neutral at the 195 degree design point.
        vap_shift = clamp((self.d3_temp.y - 195.0) / 100.0, -0.15, 0.15)
        offgas_total = clamp((240.0 + 7.4 * feed + quench_flow * 40.0)
                             * (1.0 + 1.8 * vap_shift), 0.0, 3000.0)
        offgas_total *= self.xv4002.fraction
        # Only GENERATED light ends make fuel gas: the recycle hydrogen
        # passing through with the feed flashes overhead and returns to
        # C1 suction, less what the reaction consumed - which is what
        # the H2 makeup at the compressor exists to replace.
        to_fuel = offgas_total * 0.62
        h2_consumed = self.H2_CONS_K * feed * overall
        through_gas = max(gas_in - h2_consumed, 0.0) * 1000.0
        to_c1 = (offgas_total - to_fuel) + quench_flow * 1000.0 + through_gas
        self.bus["d3_offgas_available"] = to_fuel
        self.bus["d3_offgas_to_c1"] = to_c1
        self.bus["d3_offgas_total"] = offgas_total + through_gas
        self.bus["r1_h2_consumption"] = h2_consumed * 1000.0

        # The water that settles to the D3 interface (D3_WATER_FRAC)
        # comes out of the same charge, so the hydrocarbon liquid is the
        # charge less the gas LESS that water. Booked on top of it, the
        # plant-wide liquid balance carried a 1.1 m3/h source (2026-09-05).
        liquid_in = feed * (clamp(0.86 - 0.10 * vap_shift, 0.5, 1.0)
                            - self.D3_WATER_FRAC)
        liquid_out = self.lcv4001.flow(
            max(self.d3_pressure.y - float(self.bus.get("t1_pressure_bara", 9.0)), 0.0),
            0.76)
        water_in = feed * self.D3_WATER_FRAC
        water_out = self.lcv4002.flow(max(self.d3_pressure.y - P_STD, 0.0), 1.0)

        liquid_before = self.d3_level.y
        water_before = self.d3_interface.y
        self.d3_level.step((liquid_in - liquid_out) / (self.D3_AREA * 4.6)
                           * 100.0 / 3600.0, dt)
        self.d3_interface.step((water_in - water_out) / (self.D3_AREA * 1.2)
                               * 100.0 / 3600.0, dt)
        self.record_inventory_balance(
            "D3 hydrocarbon inventory", liquid_in, liquid_out,
            liquid_before, self.d3_level.y, self.D3_AREA * 4.6, dt)
        self.record_inventory_balance(
            "D3 water inventory", water_in, water_out,
            water_before, self.d3_interface.y, self.D3_AREA * 1.2, dt)

        vent = self.pcv4001.gas_flow(self.d3_pressure.y, P_STD, 0.6, 330.0)
        d_pd3 = ((offgas_total * 0.35 - vent) * P_STD / (3600.0 * self.D3_VOLUME))
        self.d3_pressure.step(d_pd3, dt)

        # Additive dosing steers selectivity: more additive makes a
        # lighter D3 liquid. The effect walks through the injection
        # lag, the beds and the separator inventory before any column
        # analyser can see it - a genuinely long dead time.
        additive = self.v_add.flow(4.0, 1.0) * 1000.0        # L/h
        self.FT4005.set(clamp(additive, 0.0, 60.0))
        r_eff = self.add_lag.step(
            self.add_dead.step(additive / max(feed, 1.0)), dt)
        # What the feed brought plus what the beds made: 0.34 of light key
        # is the reaction's at the design additive ratio, on top of the
        # 0.12 the fresh feed carries (AT-1001), so the design point is the
        # 0.46 the columns were commissioned on and a feed quality swing
        # reaches T1 the way it would, through the charge.
        fresh_z = self.fresh_lag.step(
            float(self.bus.get("fresh_lightfrac", 0.12)), dt)
        self.bus["d3_liquid_lightfrac"] = clamp(
            0.34 + fresh_z + 0.5 * (r_eff - 25.0 / 90.0), 0.40, 0.52)

        self.bus["d3_liquid_to_t1"] = liquid_out
        self.bus["d3_liquid_temperature"] = self.d3_temp.y
        self.bus["r1_bed_temperature"] = self.bed2.y
        self.bus["r1_conversion"] = self.conversion.y

        # --------------------------------------------------------- instruments
        self.TT4002.set(self.tx_bed1.step(dt, self.bed1.y), Quality(self.tx_bed1.quality))
        self.TT4003.set(self.bed2.y)
        self.TT4004.set(self.bed2.y)
        self.TT4005.set(self.d3_temp.y)
        self.PT4001.set(clamp(self.pressure.y - P_STD, 0.0, 60.0))
        self.PT4002.set(clamp(self.d3_pressure.y - P_STD, 0.0, 55.0))
        self.FT4001.set(quench_flow)
        self.FT4002.set(to_fuel)
        self.FT4003.set(liquid_out)
        self.FT4004.set(water_out)
        self.LT4001.set(self.tx_level.step(dt, self.d3_level.y),
                        Quality(self.tx_level.quality))
        self.LT4002.set(self.tx_interface.step(dt, self.d3_interface.y),
                        Quality(self.tx_interface.quality))
        self.AT4001.set(self.tx_impurity.step(dt, self.impurity_dead.step(impurity_true)),
                        Quality(self.tx_impurity.quality))
        self.AT4002.set(clamp(0.6 + self.conversion.y * 0.02, 0.0, 5.0))
        self.XY4010.set(self.conversion.y)

        t["TSHH-4001A"].set(self.bed1.y > 470.0)
        t["TSHH-4001B"].set(self.bed2.y > 470.0)
        t["TSHH-4001"].set(max(self.bed1.y, self.bed2.y) > 470.0)
        t["PSHH-4001"].set(self.pressure.y - P_STD > 54.0)
        t["LSLL-4001"].set(self.d3_level.y < 12.0)
        t["LSHH-4001"].set(self.d3_level.y > 88.0)

    def save_state(self):
        return {"b1": self.bed1.y, "b2": self.bed2.y, "p": self.pressure.y,
                "pd3": self.d3_pressure.y, "l": self.d3_level.y,
                "i": self.d3_interface.y, "act": self.activity,
                "addl": self.add_lag.y,
                "freshz": self.fresh_lag.y}

    def apply(self, state):
        super().apply(state)
        # The fine _dyn pass reinstates the exact saved integrator, so the
        # correction of an unphysically high pre-anchor pressure has to run
        # after it, not inside load_state.
        self.pressure.reset(min(self.pressure.y, 54.0))

    def load_state(self, s):
        self.bed1.reset(s.get("b1", 120.0))
        self.bed2.reset(s.get("b2", 118.0))
        self.pressure.reset(min(float(s.get("p", 43.0)), 54.0))
        self.d3_pressure.reset(s.get("pd3", 40.0))
        self.d3_level.reset(s.get("l", 50.0))
        self.d3_interface.reset(s.get("i", 30.0))
        self.activity = s.get("act", 100.0)
        r = float(s.get("addl", 25.0 / 90.0))
        self.add_lag.reset(r)
        self.fresh_lag.reset(s.get("freshz", 0.12))
        self.add_dead.reset(r)
