"""Flowsheet.

Owns the units and the shared ``bus`` dictionary that carries anything crossing a
battery limit: header pressures, charge flow, off-gas availability.

Tear stream
-----------
The plant recycles, so a strict sequential solve is not possible. Rather than
iterate to convergence every step, U100 reads the recycle flow left on the bus by
the previous step. That is a tear stream, and it is legitimate here because the
recycle transport delay is minutes while the step is a tenth of a second. It is
also far more stable than an inner iteration, which is the trade this simulator
wants.

Unit order matters: utilities first, then feed, then the fired heater, so that
within a single step each unit sees the current value of everything upstream of
it and last step's value of everything downstream.
"""

from __future__ import annotations

import logging
from typing import Dict, List

from ..core.tags import TagDatabase
from .accountability import (AccountabilityReport, BalanceReading,
                             BusSignalSpec, ProcessBus)
from .base import Malfunction, ProcessUnit
from .u010_fuel_gas import FuelGasHeader
from .u100_feed import FeedSection
from .u200_compressor import RecycleCompressor
from .u300_heater import FiredHeater
from .u400_reactor import ReactorSection
from .u500_u600_columns import ColumnT1, ColumnT2
from .u700_boiler import SteamBoiler
from .u800_u900 import EffluentTreatment, SafetySystem

log = logging.getLogger(__name__)

# Order matters. Utilities first, then feed, then forward through the train, with
# the safety system last so its trips are latched before the next step begins.
# Anything that flows backwards - the recycle to D1, the off-gas to the fuel
# header - is a tear stream read from the previous step.
UNIT_CLASSES = [FuelGasHeader, FeedSection, RecycleCompressor, FiredHeater,
                ReactorSection, ColumnT1, ColumnT2, SteamBoiler,
                EffluentTreatment, SafetySystem]

def _sig(name: str, default: float, eu: str, producer: str,
         consumers=(), lo=None, hi=None, tear: bool = False,
         description: str = "") -> BusSignalSpec:
    return BusSignalSpec(name, default, eu, producer, tuple(consumers),
                         lo, hi, tear, description)


# This is the internal model contract, not the OPC UA contract.  Names may be
# implementation-oriented, but every one has exactly one producer and an
# explicit set of consumers.  ``tear`` means at least one consumer deliberately
# sees the previous integration step because the producer is later in the unit
# order (or is the unit's own stored feedback).
BUS_SIGNALS = (
    _sig("fg_header_pressure_bara", 17.0, "bara", "U010", ("U300",), 0, 50),
    _sig("fg_to_h1_flow", 0.0, "kg/h", "U010", ("U300",), 0, 10000),
    _sig("fg_lhv", 38.5, "MJ/kg", "U010", ("U300", "U700"), 1, 80),
    _sig("h1_burner_pressure_bara", 4.0, "bara", "U300", ("U010",),
         0, 30, True),
    _sig("h1_inlet_pressure", 11.0, "barg", "U300", ("U100",),
         -1, 60, True),
    _sig("h1_outlet_temperature", 62.0, "degC", "U300", ("U400",), -50, 800),
    _sig("h1_duty_mw", 0.0, "MW", "U300", (), 0, 100),
    _sig("charge_flow", 0.0, "m3/h", "U100", ("U300", "U400"), 0, 500),
    _sig("charge_temperature", 62.0, "degC", "U100", ("U300",), -50, 400),
    _sig("d1_level", 55.0, "%", "U100", (), -20, 120),
    _sig("fresh_lightfrac", 0.12, "frac", "U100", ("U400",), 0, 1,
         description="light key fraction in the fresh feed, what AT-1001 reads"),
    _sig("d3_offgas_available", 900.0, "kg/h", "U400", ("U010",),
         0, 100000, True),
    _sig("b1_fuel_demand", 1500.0, "kg/h", "U700", ("U010",),
         0, 10000, True),
    _sig("t1_bottoms_recycle", 0.0, "m3/h", "U500", ("U100",),
         0, 500, True),
    _sig("air_failure", 0.0, "bool", "FLOWSHEET",
         ("U100", "U200", "U300", "U400", "U500", "U600", "U700", "U800"),
         0, 1, True),
    _sig("b1_supply_pressure_bara", 6.0, "bara", "U010", ("U700",), 0, 30),
    _sig("mp_steam_pressure_bara", 36.0, "bara", "U700", ("U500", "U600"),
         0, 100, True),
    _sig("mp_steam_temperature", 373.0, "degC", "U700", ("U500", "U600"),
         0, 800, True),
    _sig("mp_extra_demand", 0.0, "t/h", "U700", ("U700",), 0, 100, True),
    _sig("t1_steam_demand", 0.0, "t/h", "U500", ("U700",), 0, 100),
    _sig("t2_steam_demand", 0.0, "t/h", "U600", ("U700",), 0, 100),
    _sig("d3_liquid_to_t1", 0.0, "m3/h", "U400", ("U500",), 0, 500),
    _sig("d3_liquid_temperature", 60.0, "degC", "U400", ("U200", "U500", "U800"),
         -50, 500, True),
    _sig("d3_offgas_to_c1", 0.0, "kg/h", "U400", ("U200",), 0, 100000, True),
    _sig("d3_offgas_total", 0.0, "kg/h", "U400", ("U400",), 0, 100000, True),
    _sig("t1_bottoms_temperature", 200.0, "degC", "U500", ("U100",),
         -50, 500, True),
    _sig("t1_distillate", 0.0, "m3/h", "U500", (), 0, 500),
    _sig("t1_distillate_to_t2", 0.0, "m3/h", "U500", ("U600",), 0, 500),
    _sig("t1_distillate_to_storage", 0.0, "m3/h", "U500", (), 0, 500),
    _sig("t1_distillate_temperature", 60.0, "degC", "U500", ("U600",), -50, 400),
    _sig("r2_product", 0.0, "m3/h", "U600", (), 0, 500),
    _sig("t2_heavy_rundown", 0.0, "m3/h", "U600", (), 0, 500),
    _sig("c1_discharge_pressure", 13.0, "bara", "U200", ("U400",), 0, 100),
    _sig("c1_recycle_gas_flow", 0.0, "kg/h", "U200", (), 0, 100000),
    _sig("c1_running", 0.0, "bool", "U200", (), 0, 1),
    _sig("quench_and_makeup_demand", 0.0, "kg/h", "U400", ("U200",),
         0, 100000, True),
    _sig("recycle_gas_to_h1", 0.0, "kg/h", "U200", ("U300", "U400"), 0, 100000),
    _sig("recycle_gas_mw", 12.0, "kg/kmol", "U200", ("U300",), 1, 100),
    _sig("r1_h2_consumption", 0.0, "kg/h", "U400", (), 0, 10000),
    _sig("r1_effluent_temperature", 385.0, "degC", "U400", ("U300",),
         -50, 800, True),
    _sig("r1_bed_temperature", 350.0, "degC", "U400", (), -50, 800),
    _sig("r1_conversion", 60.0, "%", "U400", ("U010", "U200"), 0, 100, True),
    _sig("d3_liquid_lightfrac", 0.46, "fraction", "U400", ("U500",), 0, 1),
    _sig("sour_water_flow", 6.0, "m3/h", "FLOWSHEET", ("U800",), 0, 200, True),
    _sig("t1_pressure_bara", 9.0, "bara", "U500", ("U400",), 0, 100, True),
    _sig("t2_pressure_bara", 6.5, "bara", "U600", (), 0, 100),
    # CT1 is evaluated in U010 before its users, so the supply is current and
    # the return duties are deliberate one-scan tear streams.  At a 100 ms
    # plant step that delay is immaterial beside the header and basin holdup,
    # and avoids an algebraic circulation loop between every exchanger and the
    # tower.
    _sig("ambient_dry_bulb", 30.0, "degC", "ENVIRONMENT", ("U010",), -40, 70),
    _sig("ambient_wet_bulb", 22.0, "degC", "ENVIRONMENT", ("U010",), -40, 50),
    _sig("cooling_water_temperature", 28.0, "degC", "U010",
         ("U200", "U400", "U500", "U600", "U800"), 0, 80),
    _sig("cooling_water_dp_bar", 2.4, "bar", "U010",
         ("U200", "U400", "U500", "U600", "U800"), 0, 10),
    _sig("u200_cw_flow", 235.0, "m3/h", "U200", ("U010",), 0, 2000, True),
    _sig("u200_cw_duty", 1800.0, "kW", "U200", ("U010",), 0, 50000, True),
    _sig("u400_cw_flow", 240.0, "m3/h", "U400", ("U010",), 0, 2000, True),
    _sig("u400_cw_duty", 10500.0, "kW", "U400", ("U010",), 0, 100000, True),
    _sig("t1_cw_flow", 1120.0, "m3/h", "U500", ("U010",), 0, 3000, True),
    _sig("t1_cw_duty", 6500.0, "kW", "U500", ("U010",), 0, 100000, True),
    _sig("t2_cw_flow", 170.0, "m3/h", "U600", ("U010",), 0, 3000, True),
    _sig("t2_cw_duty", 1500.0, "kW", "U600", ("U010",), 0, 100000, True),
    _sig("u800_cw_flow", 180.0, "m3/h", "U800", ("U010",), 0, 2000, True),
    _sig("u800_cw_duty", 900.0, "kW", "U800", ("U010",), 0, 50000, True),
    _sig("esd_total", 0.0, "bool", "U900", (), 0, 1),
    _sig("esd_reaction", 0.0, "bool", "U900", ("U900",), 0, 1, True),
    _sig("esd_fractionation", 0.0, "bool", "U900", ("U900",), 0, 1, True),
    _sig("esd_boiler", 0.0, "bool", "U900", ("U900",), 0, 1, True),
    _sig("esd_depressure", 0.0, "bool", "U900", ("U400",), 0, 1, True),
    _sig("esd_u100", 0.0, "bool", "U900", ("U100",), 0, 1, True),
    _sig("esd_u200", 0.0, "bool", "U900", ("U200",), 0, 1, True),
    _sig("esd_u300", 0.0, "bool", "U900", ("U300",), 0, 1, True),
    _sig("esd_u400", 0.0, "bool", "U900", ("U400",), 0, 1, True),
    _sig("esd_u500", 0.0, "bool", "U900", ("U500", "U600"), 0, 1, True),
    _sig("esd_u700", 0.0, "bool", "U900", ("U700",), 0, 1, True),
)

BUS_DEFAULTS: Dict[str, float] = {spec.name: spec.default for spec in BUS_SIGNALS}


class Flowsheet:
    """Container for the process units and the values they exchange."""

    def __init__(self, db: TagDatabase, dt: float = 0.1) -> None:
        self.db = db
        self.dt = float(dt)
        self.bus = ProcessBus(BUS_SIGNALS)
        self.units: List[ProcessUnit] = []
        for cls in UNIT_CLASSES:
            try:
                unit = cls(db, self.bus, dt=self.dt)
            except Exception:
                log.exception("Failed to build unit %s", cls.__name__)
                raise
            self.units.append(unit)
            log.info("Built %s %s with %d tags", unit.code, unit.name, len(unit.tags))
        self._topology_errors = self.bus.validate_topology(
            [unit.code for unit in self.units])
        if self._topology_errors:
            raise RuntimeError("invalid process-bus contract: "
                               + "; ".join(self._topology_errors))
        self._parameters = tuple(
            parameter
            for unit in self.units
            for parameter in unit.model_parameters()
        )
        counts = db.counts()
        log.info("Flowsheet ready: %d tags (AI %d, AO %d, DI %d, DO %d)",
                 len(db.all()), counts["AI"], counts["AO"], counts["DI"], counts["DO"])

    # ------------------------------------------------------------------ running
    def step_unit(self, unit: ProcessUnit, dt: float) -> None:
        """Run one unit with process-bus accesses attributed to its owner."""
        with self.bus.source(unit.code):
            unit.step(dt)

    def after_step(self, dt: float) -> None:
        """Cross-unit bookkeeping once every unit has stepped."""
        with self.bus.source("FLOWSHEET"):
            # Instrument air failure is a plant-wide utility, published for all units.
            u010 = self.unit("U010")
            if u010 is not None:
                self.bus["air_failure"] = (
                    1.0 if getattr(u010, "air_failed", False) else 0.0)
            # Sour water from D3 is the effluent plant's feed.
            u400 = self.unit("U400")
            if u400 is not None:
                self.bus["sour_water_flow"] = float(u400.FT4004.value)

    def accountability_report(self) -> AccountabilityReport:
        """Point-in-time model contract, parameter and conservation report."""
        balances = [
            reading
            for unit in self.units
            for reading in unit.balance_readings()
        ]

        # Algebraic stream splits have no inventory.  Keeping them beside the
        # dynamic balances catches routing changes that otherwise conserve
        # locally while sending material to the wrong destination.
        t1_dist = self.bus["t1_distillate"]
        t1_out = (self.bus["t1_distillate_to_t2"]
                  + self.bus["t1_distillate_to_storage"])
        balances.append(BalanceReading(
            "U500", "T1 distillate routing", t1_dist, t1_out, 0.0,
            t1_dist - t1_out, 1e-7, "m3/h", "routing"))

        d3_in = (self.bus["d3_offgas_total"]
                 + self.bus["quench_and_makeup_demand"])
        d3_out = (self.bus["d3_offgas_available"]
                  + self.bus["d3_offgas_to_c1"])
        balances.append(BalanceReading(
            "U400", "D3 gas routing", d3_in, d3_out, 0.0,
            d3_in - d3_out, 1e-6, "kg/h", "routing"))

        return AccountabilityReport(
            topology_errors=tuple(self._topology_errors),
            current_range_issues=tuple(self.bus.current_range_issues()),
            balances=tuple(balances),
            parameters=self._parameters,
            bus_stats=self.bus.stats_snapshot(),
        )

    # ------------------------------------------------------------------ lookups
    def unit(self, code: str) -> ProcessUnit | None:
        for u in self.units:
            if u.code == code:
                return u
        return None

    def malfunctions(self) -> List[Malfunction]:
        out: List[Malfunction] = []
        for u in self.units:
            out.extend(u.malfunctions)
        return sorted(out, key=lambda m: m.mf_id)

    def clear_all_malfunctions(self) -> int:
        n = 0
        for u in self.units:
            for mf in u.malfunctions:
                if mf.active:
                    n += 1
                mf.set(False, 0.0)
        log.info("Cleared %d active malfunctions", n)
        return n
