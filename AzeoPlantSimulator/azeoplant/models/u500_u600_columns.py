"""U500 and U600 - distillation columns T1 and T2.

Both columns are the same equipment with different sizing, so they share one
class. T1 separates the D3 liquid, sending distillate to storage and bottoms to
T2. T2 takes that, makes the R2 product overhead and recycles its bottoms to D1,
which is what closes the plant material balance and makes the snowball effect
possible.

Separation model
----------------
A tray-by-tray column is overkill for control training and slow enough to hurt
the step rate. Instead the split is solved from a **separation factor**:

    S = (x_D / (1 − x_D)) · ((1 − x_B) / x_B) = α^(N · η)

where ``η`` rises with reflux ratio and saturates, so more reflux buys purity
with diminishing returns exactly as it does on a real column. Combined with the
overall balance ``F·z = D·x_D + B·x_B`` this gives two equations in two unknowns,
solved by **bisection**. Bisection is used deliberately: it cannot diverge, it
takes a fixed number of iterations, and a solver that occasionally fails to
converge is worse than one that is slightly less exact.

Compositions then pass through a lag and a dead time, because the thing that
makes composition control hard is not the steady state, it is the several
minutes before the tray inventory reflects what you did.

Interaction
-----------
Reflux and reboil both affect both products, which is what makes dual composition
control a decoupling problem rather than two independent loops. Both reboilers
draw on the same MP steam header from B1, so loading one column disturbs the
other. Neither of those couplings is bolted on; they fall out of the model.
"""

from __future__ import annotations

import logging
import math

from ..core.devices import ControlValve, HeatExchanger, Transmitter, ValveChar
from ..core.dynamics import Integrator, Lag, clamp, safe_div
from ..core.tags import Quality
from .base import ProcessUnit
from .packages import PumpTrain, SdvPackage, min_flow_valve

log = logging.getLogger(__name__)

P_STD = 1.01325


def solve_split(feed_frac_dist: float, z_feed: float, sep_factor: float) -> tuple:
    """Return ``(x_dist, x_bot)`` for a given distillate fraction and separation.

    Bisection on the distillate purity. Bounded, fixed cost, cannot diverge.
    """
    d = clamp(feed_frac_dist, 0.001, 0.999)
    z = clamp(z_feed, 0.001, 0.999)
    s = clamp(sep_factor, 1.0001, 1e9)

    def residual(xd: float) -> float:
        xb = clamp((z - d * xd) / (1.0 - d), 1e-6, 1.0 - 1e-6)
        return (xd / (1.0 - xd)) * ((1.0 - xb) / xb) - s

    lo, hi = max(z, 1e-4), 1.0 - 1e-6
    if residual(lo) > 0.0:
        xd = lo
    elif residual(hi) < 0.0:
        xd = hi
    else:
        for _ in range(28):
            mid = 0.5 * (lo + hi)
            if residual(mid) < 0.0:
                lo = mid
            else:
                hi = mid
        xd = 0.5 * (lo + hi)
    xb = clamp((z - d * xd) / (1.0 - d), 1e-6, 1.0 - 1e-6)
    return xd, xb


def _full_open_flow(valve: ControlValve, dp_bar: float, sg: float) -> float:
    """Flow the valve passes wide open, without disturbing its actual position."""
    held = valve.position
    try:
        valve.position = 100.0
        return valve.flow(dp_bar, sg)
    finally:
        valve.position = held


class DistillationColumn(ProcessUnit):
    """Shared implementation. Subclasses set the tag numbers and the sizing."""

    N = 5                      # tag number prefix, 5 for T1 and 6 for T2
    COLUMN = "T1"
    STAGES = 32
    ALPHA = 2.6
    DRUM_VOLUME = 29.0
    BOOT_WATER_FRAC = 0.004    # share of the feed that is water, to the boot
    SUMP_VOLUME = 37.0
    COLUMN_VOLUME = 65.0
    # Exchanger surfaces, kW/K clean. Both are sized with margin over the
    # nominal duty, so neither is the limiting element on a healthy plant and
    # the columns behave as their steam and cooling water valves say. They
    # become the constraint once fouled, or once their driving force collapses.
    REBOILER_UA = 340.0        # kW/K, against superheated MP steam
    CONDENSER_UA = 430.0       # kW/K, against cooling water
    LATENT_KJ_PER_M3 = 180000.0   # column liquid, ~250 kJ/kg at ~720 kg/m3
    STEAM_LATENT_KJ_KG = 1950.0
    # Relative volatility falls as temperature rises, per Clausius-Clapeyron
    # on the two pseudo-components. DH_ALPHA_K is the effective (dHvap_H -
    # dHvap_L)/R; calibrated so alpha equals ALPHA exactly at the nominal mid
    # column temperature, which keeps the lined-out plant where it was while
    # making a pressure raise genuinely cost separation, as it does in life.
    DH_ALPHA_K = 2400.0
    # Below this boilup the trays weep: liquid rains through the holes instead
    # of contacting vapour, and separation collapses at LOW rates, not only at
    # flooding. Roughly a fifth of nominal vapour traffic.
    WEEP_BOILUP = 65.0
    CP_LIQ_KJ_KG = 2.4
    P_NOMINAL = 8.0            # barg
    T_LIGHT = 118.0            # degC, light key boiling point at nominal pressure
    T_HEAVY = 268.0
    FLOOD_DP = 420.0           # mbar
    STEAM_MAX = 40.0           # t/h at full valve and full header pressure
    PARAMETER_META = {
        "N": {"eu": "-", "description": "Column tag-number prefix",
              "lo": 1.0, "hi": 99.0},
        "STAGES": {"eu": "count", "description": "Theoretical contacting stages",
                   "lo": 1.0, "hi": 500.0},
        "ALPHA": {"eu": "ratio", "description": "Nominal light/heavy relative volatility",
                  "lo": 1.0, "hi": 100.0},
        "BOOT_WATER_FRAC": {"eu": "fraction", "description": "Water fraction of the feed that settles to the reflux drum boot",
                            "lo": 0.0, "hi": 0.05},
        "DRUM_VOLUME": {"eu": "m3", "description": "Reflux-drum effective volume",
                        "lo": 0.1, "hi": 10000.0},
        "SUMP_VOLUME": {"eu": "m3", "description": "Column-sump effective volume",
                        "lo": 0.1, "hi": 10000.0},
        "COLUMN_VOLUME": {"eu": "m3", "description": "Column vapour-space effective volume",
                          "lo": 0.1, "hi": 100000.0},
        "REBOILER_UA": {"eu": "kW/K", "description": "Clean reboiler heat-transfer conductance",
                        "lo": 0.0, "hi": 100000.0},
        "CONDENSER_UA": {"eu": "kW/K", "description": "Clean condenser heat-transfer conductance",
                         "lo": 0.0, "hi": 100000.0},
        "LATENT_KJ_PER_M3": {"eu": "kJ/m3", "description": "Column-liquid volumetric latent heat",
                             "lo": 1.0, "hi": 1e9},
        "STEAM_LATENT_KJ_KG": {"eu": "kJ/kg", "description": "MP steam latent heat",
                               "lo": 1.0, "hi": 1e6},
        "DH_ALPHA_K": {"eu": "K", "description": "Relative-volatility temperature coefficient",
                       "lo": -1e6, "hi": 1e6},
        "WEEP_BOILUP": {"eu": "m3/h", "description": "Minimum vapour traffic before tray weeping",
                        "lo": 0.0, "hi": 10000.0},
        "CP_LIQ_KJ_KG": {"eu": "kJ/(kg K)", "description": "Column-liquid heat capacity",
                         "lo": 0.01, "hi": 100.0},
        "P_NOMINAL": {"eu": "barg", "description": "Nominal column pressure",
                      "lo": -1.0, "hi": 500.0},
        "T_LIGHT": {"eu": "degC", "description": "Light-key nominal boiling temperature",
                    "lo": -273.15, "hi": 1000.0},
        "T_HEAVY": {"eu": "degC", "description": "Heavy-key nominal boiling temperature",
                    "lo": -273.15, "hi": 1000.0},
        "FLOOD_DP": {"eu": "mbar", "description": "Column flooding differential pressure",
                     "lo": 0.0, "hi": 100000.0},
        "STEAM_MAX": {"eu": "t/h", "description": "Maximum reboiler steam flow",
                      "lo": 0.0, "hi": 10000.0},
    }
    HAS_WATER_BOOT = True
    DIST_LABEL = "distillate"
    BOT_LABEL = "bottoms"

    def build(self) -> None:
        n = self.N
        c = self.COLUMN

        self.FT_feed = self.ai(f"FT-{n}001", f"{c} feed flow", "m3/h", 0, 300, 0.0)
        self.FT_reflux = self.ai(f"FT-{n}002", f"{c} reflux flow", "m3/h", 0, 400, 0.0)
        self.FT_dist = self.ai(f"FT-{n}003", f"{c} {self.DIST_LABEL}", "m3/h", 0, 120, 0.0)
        self.FT_bot = self.ai(f"FT-{n}004", f"{c} {self.BOT_LABEL}", "m3/h", 0, 200, 0.0)
        self.FT_steam = self.ai(f"FT-{n}005", f"{c} reboiler steam flow", "t/h", 0, 45, 0.0)
        self.FT_cw = self.ai(f"FT-{n}006", f"{c} condenser cooling water flow",
                             "m3/h", 0, 1600, 0.0)
        self.FT_mf1 = self.ai(f"FT-{n}007", f"{c} reflux pump minimum flow",
                              "m3/h", 0, 60, 0.0)
        self.FT_mf2 = self.ai(f"FT-{n}008", f"{c} bottoms pump minimum flow",
                              "m3/h", 0, 50, 0.0)

        self.TT_feed = self.ai(f"TT-{n}001", f"{c} feed temperature", "degC", 0, 350, 60.0)
        self.TT_t1 = self.ai(f"TT-{n}002", f"{c} upper tray temperature",
                             "degC", 0, 250, 130.0)
        self.TT_t2 = self.ai(f"TT-{n}003", f"{c} middle tray temperature",
                             "degC", 0, 300, 170.0)
        self.TT_t3 = self.ai(f"TT-{n}004", f"{c} lower tray temperature",
                             "degC", 0, 350, 210.0)
        self.TT_ovhd = self.ai(f"TT-{n}005", f"{c} overhead vapour temperature",
                               "degC", 0, 250, 122.0)
        self.TT_bot = self.ai(f"TT-{n}006", f"{c} bottoms temperature",
                              "degC", 0, 400, 240.0)
        self.TT_cond = self.ai(f"TT-{n}007", f"{c} condenser outlet temperature",
                               "degC", 0, 150, 48.0)

        self.PT_ovhd = self.ai(f"PT-{n}001", f"{c} overhead pressure", "barg",
                               0, 15, self.P_NOMINAL)
        self.PT_bot = self.ai(f"PT-{n}002", f"{c} bottom pressure", "barg", 0, 16,
                              self.P_NOMINAL + 0.3)
        self.PT_p1 = self.ai(f"PT-{n}003", f"{c} reflux pump discharge pressure",
                             "barg", 0, 25, 0.0)
        self.PT_p2 = self.ai(f"PT-{n}004", f"{c} bottoms pump discharge pressure",
                             "barg", 0, 25, 0.0)
        self.PDT = self.ai(f"PDT-{n}001", f"{c} column differential pressure",
                           "mbar", 0, 500, 180.0)

        self.LT_drum = self.ai(f"LT-{n}001", f"{c} reflux drum level", "%", 0, 100, 50.0)
        self.LT_sump = self.ai(f"LT-{n}002", f"{c} column bottom level", "%", 0, 100, 50.0)
        if self.HAS_WATER_BOOT:
            self.LT_boot = self.ai(f"LT-{n}003", f"{c} reflux drum water boot level",
                                   "%", 0, 100, 30.0)

        self.AT_dist = self.ai(f"AT-{n}001", f"{c} distillate heavy key content",
                               "mol%", 0, 10, 1.5)
        self.AT_bot = self.ai(f"AT-{n}002", f"{c} bottoms light key content",
                              "mol%", 0, 10, 2.0)
        self.ZT = self.ai(f"ZT-{n}001", f"FCV-{n}001 position feedback", "%", 0, 100, 0.0)

        self.ao(f"FCV-{n}001", f"{c} reflux flow control valve", value=45.0)
        self.ao(f"FCV-{n}002", f"{c} {self.DIST_LABEL} control valve", value=35.0)
        self.ao(f"FCV-{n}003", f"{c} {self.BOT_LABEL} control valve", value=40.0)
        self.ao(f"FCV-{n}004", f"{c} reboiler steam control valve", value=45.0)
        self.ao(f"FCV-{n}005", f"{c} condenser cooling water valve", value=60.0)
        self.ao(f"PCV-{n}001", f"{c} overhead pressure control valve", value=20.0)
        if self.HAS_WATER_BOOT:
            self.ao(f"LCV-{n}001", f"{c} reflux drum water boot draw valve", value=0.0)

        self.di(f"LSLL-{n}001", f"{c} reflux drum level low low", "Normal", "Tripped")
        self.di(f"LSHH-{n}001", f"{c} reflux drum level high high", "Normal", "Tripped")
        self.di(f"LSLL-{n}002", f"{c} column bottom level low low", "Normal", "Tripped")
        self.di(f"PSHH-{n}001", f"{c} overhead pressure high high", "Normal", "Tripped")
        self.di(f"UA-{n}001", f"{c} flooding detected", "Normal", "Flooding")
        self.IT_ra = self.ai(f"IT-{n}001", f"P-{n}01A motor current", "A", 0, 150)
        self.IT_rb = self.ai(f"IT-{n}002", f"P-{n}01B motor current", "A", 0, 150)
        self.IT_ba = self.ai(f"IT-{n}003", f"P-{n}02A motor current", "A", 0, 150)
        self.IT_bb = self.ai(f"IT-{n}004", f"P-{n}02B motor current", "A", 0, 150)
        self.di(f"ZSO-HV{n}001", f"HV-{n}001 open limit switch",
                "Not open", "Open", value=False)
        self.di(f"ZSC-HV{n}001", f"HV-{n}001 closed limit switch",
                "Not closed", "Closed", value=True)
        if self.COLUMN == "T1":
            self.di("ZSO-HV5002", "HV-5002 open limit switch",
                    "Not open", "Open", value=False)
            self.di("ZSC-HV5002", "HV-5002 closed limit switch",
                    "Not closed", "Closed", value=True)
            self.ao("PCV-5002", "T1 condenser hot gas bypass (split range 0-50%)",
                    value=0.0)

        # ----------------------------------------------------------- equipment
        self.xv_feed = SdvPackage(self, f"XV-{n}001", f"{c} feed shutdown valve",
                                  stroke=3.0)
        self.reflux_pumps = PumpTrain(
            self, f"P-{n}01A", f"P-{n}01B", f"{c} reflux pump",
            f"MOV-{n}001A", f"MOV-{n}001B", head_shutoff=58.0, flow_max=450.0,
            flow_min=20.0, rated_current=95.0, travel_time=25.0, duty_running=True)
        self.bottoms_pumps = PumpTrain(
            self, f"P-{n}02A", f"P-{n}02B", f"{c} bottoms pump",
            f"MOV-{n}002A", f"MOV-{n}002B", head_shutoff=88.0, flow_max=350.0,
            flow_min=18.0, rated_current=105.0, travel_time=25.0, duty_running=True)
        self.mf1 = min_flow_valve(self, f"FCV-{n}006", f"{c} reflux pump minimum flow", 60)
        self.mf2 = min_flow_valve(self, f"FCV-{n}007", f"{c} bottoms pump minimum flow", 50)

        self.v_reflux = ControlValve(f"FCV-{n}001", cv_rated=260,
                                     char=ValveChar.LINEAR, stroke_time=5)
        self.v_dist = ControlValve(f"FCV-{n}002", cv_rated=60,
                                   char=ValveChar.LINEAR, stroke_time=5)
        self.v_bot = ControlValve(f"FCV-{n}003", cv_rated=80,
                                  char=ValveChar.LINEAR, stroke_time=5)
        self.v_steam = ControlValve(f"FCV-{n}004", cv_rated=330,
                                    char=ValveChar.LINEAR, stroke_time=5)
        self.v_cw = ControlValve(f"FCV-{n}005", cv_rated=1000, char=ValveChar.LINEAR,
                                 stroke_time=8, fail_closed=False)
        self.v_press = ControlValve(f"PCV-{n}001", cv_rated=70,
                                    char=ValveChar.LINEAR, stroke_time=3)
        self.v_boot = (ControlValve(f"LCV-{n}001", cv_rated=15,
                                    char=ValveChar.LINEAR, stroke_time=4)
                       if self.HAS_WATER_BOOT else None)

        # -------------------------------------------------------------- states
        self.drum = Integrator(50.0, 0.0, 100.0)
        self.sump = Integrator(50.0, 0.0, 100.0)
        self.boot = Integrator(30.0, 0.0, 100.0)
        self.pressure = Integrator(self.P_NOMINAL + P_STD, P_STD * 0.2,
                                   self.P_NOMINAL + 8.0)
        self.boilup = Lag(35.0, 0.0)
        self.reflux_flow = Lag(1.5, 0.0)   # pump line inertia
        self.bot_flow = Lag(1.5, 0.0)
        # Light-key HOLDUP in the reflux drum and in the column liquid
        # (trays and sump, lumped), carried as the composition of each
        # inventory. The tray sections split the column liquid into the
        # top vapour and the bottom liquid; the drum then mixes what
        # condenses. So the top vapour and the bottom liquid lead the
        # distillate analyser by the drum's mixing time, which is the
        # case for tray temperature control made by the physics rather
        # than by a tuned lag. The former first-order lags and dead
        # times on the split had no holdup behind them: during the
        # shipped snapshot's drum drain the light key leaving T1 ran ten
        # per cent above the light key entering (2026-09-05).
        self.x_drum = 0.985        # light fraction of the drum liquid
        self.x_col = 0.5           # light fraction of the column liquid
        self.y_top = 0.985         # top vapour, what the condenser gets
        self.x_bot_out = 0.02      # bottom liquid, what the sump sends out
        self._vapour_light = 0.0   # light key made but not yet condensed
        self.t_cond = Lag(60.0, 48.0)
        self.fouling_trays = 0.0
        self.hx_reboiler = HeatExchanger(f"E-{self.N}02", ua_clean=self.REBOILER_UA)
        self.hx_condenser = HeatExchanger(f"E-{self.N}01", ua_clean=self.CONDENSER_UA)
        # Full-open cooling water rate, used to turn the valve opening into the
        # fraction of condenser surface actually wetted. Taken from the valve
        # itself so a re-sized valve stays consistent with the exchanger.
        self.cw_max = max(_full_open_flow(self.v_cw, 2.4, 1.0), 1.0)
        self._q_cond = 0.0        # vapour condensed by the subcooled feed
        self.flooding = False

        self.tx_dist = Transmitter(f"AT-{n}001", 0, 10, tau=25.0,
                                   noise_sigma_pct=1.2, update_period=300.0,
                                   transport=45.0)
        self.tx_bot = Transmitter(f"AT-{n}002", 0, 10, tau=25.0,
                                  noise_sigma_pct=1.2, update_period=300.0,
                                  transport=45.0)
        self.tx_drum = Transmitter(f"LT-{n}001", 0, 100, tau=1.2, noise_sigma_pct=0.4)
        self.tx_pdt = Transmitter(f"PDT-{n}001", 0, 500, tau=0.8, noise_sigma_pct=0.8)

        self._build_column_malfunctions()

    def _build_column_malfunctions(self) -> None:
        from ..core.devices import TxFailure
        n, c = self.N, self.COLUMN
        self.add_malfunction(f"MF-{n}01", f"FCV-{n}001", "Reflux valve stiction",
                             "Valve", "Stick band", 0, 10,
                             lambda a, v: setattr(self.v_reflux, "stiction",
                                                  v if a else 0.0))
        self.add_malfunction(f"MF-{n}02", f"LT-{n}001",
                             "Reflux drum level transmitter frozen", "Transmitter",
                             "", 0, 1,
                             lambda a, v: setattr(self.tx_drum, "failure",
                                                  TxFailure.FROZEN if a else TxFailure.NONE))
        self.add_malfunction(f"MF-{n}03", c, "Tray fouling", "Process",
                             "dP increase", 0, 60,
                             lambda a, v: setattr(self, "fouling_trays", v if a else 0.0))
        self.add_malfunction(f"MF-{n}04", f"E-{n}01", "Condenser fouling", "Process",
                             "UA loss", 0, 60,
                             lambda a, v: setattr(self.hx_condenser, "fouling_pct",
                                                  v if a else 0.0))
        self.add_malfunction(f"MF-{n}06", f"E-{n}02", "Reboiler fouling", "Process",
                             "UA loss", 0, 80,
                             lambda a, v: setattr(self.hx_reboiler, "fouling_pct",
                                                  v if a else 0.0))
        self.add_malfunction(f"MF-{n}05", f"AT-{n}001", "Analyser out of service",
                             "Analyser", "", 0, 1,
                             lambda a, v: setattr(self.tx_dist, "failure",
                                                  TxFailure.OUT_OF_SERVICE if a
                                                  else TxFailure.NONE))

    # ------------------------------------------------------------- subclass API
    def feed_stream(self) -> tuple:
        """Return ``(flow_m3h, temperature_c, light_key_fraction)``."""
        raise NotImplementedError

    def publish_products(self, distillate: float, bottoms: float) -> None:
        """Put the products on the bus for whatever is downstream."""
        raise NotImplementedError

    # --------------------------------------------------------------------- step
    def step(self, dt: float) -> None:
        t, n = self.tags, self.N
        air = bool(self.bus.get("air_failure", 0.0))
        esd = bool(self.bus.get("esd_u500", 0.0))

        for valve, tag in ((self.v_reflux, f"FCV-{n}001"), (self.v_dist, f"FCV-{n}002"),
                           (self.v_bot, f"FCV-{n}003"), (self.v_steam, f"FCV-{n}004"),
                           (self.v_cw, f"FCV-{n}005"), (self.v_press, f"PCV-{n}001"),
                           (self.mf1, f"FCV-{n}006"), (self.mf2, f"FCV-{n}007")):
            valve.step(dt, t[tag].effective, air)
        if self.v_boot is not None:
            self.v_boot.step(dt, t[f"LCV-{n}001"].effective, air)
        self.xv_feed.step(dt, trip=esd, air_failure=air)

        feed, t_feed, z_feed = self.feed_stream()
        feed *= self.xv_feed.fraction
        p_col = self.pressure.y

        # ------------------------------------------------------------- reboiler
        # Two limits act in series: the steam the valve can pass, and the duty
        # the surface can transfer against the header temperature. The lesser
        # wins, so a healthy reboiler follows its valve and a fouled one pinches.
        steam_p = float(self.bus.get("mp_steam_pressure_bara", 36.0))
        drive = clamp(safe_div(steam_p - p_col - 2.0, 25.0, 0.0), 0.0, 1.0)
        steam_available = clamp(
            self.STEAM_MAX * (self.v_steam.position / 100.0) * math.sqrt(drive),
            0.0, self.STEAM_MAX)
        duty_demand = steam_available * 1000.0 / 3600.0 * self.STEAM_LATENT_KJ_KG
        t_steam = float(self.bus.get("mp_steam_temperature", 373.0))
        duty_reboil = self.hx_reboiler.transfer(
            duty_demand, t_steam, float(self.TT_bot.value),
            driver=self.v_steam.position / 100.0)
        # Condensing steam follows the duty actually transferred, which is what
        # the header sees and what the flow transmitter reads.
        steam = duty_reboil * 3600.0 / (1000.0 * self.STEAM_LATENT_KJ_KG)
        self.bus[f"t{n - 4}_steam_demand"] = steam
        boilup = self.boilup.step(duty_reboil * 3600.0 / self.LATENT_KJ_PER_M3, dt)

        # ------------------------------------------------------------ condenser
        # Overhead temperature carries the column pressure through its bubble
        # point, so the driving force collapses as the column is pumped down.
        # That is what makes the column self-regulating: without it the vent
        # simply runs the pressure to the integrator floor.
        cw_dp = float(self.bus.get("cooling_water_dp_bar", 2.4))
        cw = self.v_cw.flow(cw_dp, 1.0)
        t_cw = float(self.bus.get("cooling_water_temperature", 28.0))
        v_top = max(boilup - (getattr(self, "_q_cond", 0.0)), 0.0)
        # Condensate backup: as the reflux drum fills, liquid backs up into
        # the condenser and drowns tube surface, and the duty falls until
        # condensation matches what the pumps take away. It is why a real drum
        # cannot be condensed over the top, and it is the mechanism flooded
        # condenser pressure control is built on. Without it the drum of an
        # uncontrolled column simply integrates to overflow.
        # Cooling water lays scale at a rate nobody notices in a session
        # and everybody notices across a campaign. Injectable fouling sits on
        # top of it; cleaning is a turnaround job, so nothing removes it here.
        self.hx_condenser.fouling_pct = min(
            self.hx_condenser.fouling_pct + 0.8 / 86400.0 * dt,
            self.hx_condenser.MAX_FOULING)
        flooded = clamp((95.0 - self.drum.y) / 12.0, 0.05, 1.0)
        # The hot gas bypass routes vapour past the tubes: open it and the
        # condenser sees less of the overhead, which is how the split-range
        # pressure controller HOLDS pressure up against a cold condenser.
        hgb = (float(self.tags["PCV-5002"].effective) / 100.0
               if self.COLUMN == "T1" else 0.0)
        cond_duty = self.hx_condenser.transfer(
            v_top * (1.0 - 0.6 * hgb) * self.LATENT_KJ_PER_M3 / 3600.0,
            float(self.TT_ovhd.value), t_cw,
            driver=cw / self.cw_max * flooded)
        self.bus[f"t{n - 4}_cw_flow"] = cw
        self.bus[f"t{n - 4}_cw_duty"] = cond_duty
        condensed = min(v_top, cond_duty * 3600.0 / self.LATENT_KJ_PER_M3)
        # The vent removes non-condensables, not the whole overhead.
        vent = self.v_press.gas_flow(p_col, P_STD, 2.6, 380.0) * 0.00018

        # -------------------------------------------------------------- pumps
        # Both drums hold saturated liquid, so the only NPSH available is static
        # head. That makes the inventory self-limiting: draw the drum down and
        # the pump cavitates, loses prime and develops no head, which is exactly
        # what stops a real column from being pumped dry. Without this the
        # reflux valve can pull more than the condenser makes and the model
        # runs the drum to empty. The pump keeps running throughout - loss of
        # prime is hydraulics, not a motor permissive.
        drum_primed = self.drum.y > 6.0
        sump_primed = self.sump.y > 6.0
        p_col_g = p_col - P_STD
        # Suction line friction is referenced to seventy percent of the pump's
        # own runout, not to an arbitrary design flow. Getting this wrong makes
        # the pump cavitate at perfectly normal reflux rates, which then looks
        # like a separation problem rather than a hydraulic one.
        t = self.tags
        t[f"ZSO-HV{n}001"].set(False)
        t[f"ZSC-HV{n}001"].set(True)
        if self.COLUMN == "T1":
            t["ZSO-HV5002"].set(False)
            t["ZSC-HV5002"].set(True)
        self.IT_ra.set(self.reflux_pumps.motor_a.current)
        self.IT_rb.set(self.reflux_pumps.motor_b.current)
        self.IT_ba.set(self.bottoms_pumps.motor_a.current)
        self.IT_bb.set(self.bottoms_pumps.motor_b.current)
        self.reflux_pumps.step(dt, p_col_g + 0.05 + 0.9 * self.drum.y / 100.0,
                               self.FT_reflux.value, drum_primed, 100.0,
                               p_col_g, 0.25,
                               self.reflux_pumps.pump_a.flow_max * 0.7, esd)
        self.bottoms_pumps.step(dt, p_col_g + 0.10 + 1.2 * self.sump.y / 100.0,
                                self.FT_bot.value, sump_primed, 100.0,
                                p_col_g, 0.25,
                                self.bottoms_pumps.pump_a.flow_max * 0.7, esd)

        # Same algebraic loop as the boiler feedwater and the effluent
        # discharge: the pump curve and the valve are solved against each other
        # through last scan's flow. Carried in its own state it cannot oscillate.
        p_dis_1 = self.reflux_pumps.discharge_pressure(self.reflux_flow.y, 720.0)
        p_dis_2 = self.bottoms_pumps.discharge_pressure(self.bot_flow.y, 810.0)

        reflux = (self.v_reflux.flow(max(p_dis_1 - (p_col - P_STD) - 0.4, 0.0), 0.72)
                  if self.reflux_pumps.any_running else 0.0)
        distillate = (self.v_dist.flow(max(p_dis_1 - 3.0, 0.0), 0.72)
                      if self.reflux_pumps.any_running else 0.0)
        bottoms = (self.v_bot.flow(max(p_dis_2 - 4.0, 0.0), 0.81)
                   if self.bottoms_pumps.any_running else 0.0)
        reflux = self.reflux_flow.step(reflux, dt)
        bottoms = self.bot_flow.step(bottoms, dt)
        mf1 = self.mf1.flow(max(p_dis_1 - (p_col - P_STD) - 0.4, 0.0), 0.72)
        mf2 = self.mf2.flow(max(p_dis_2 - (p_col - P_STD) - 0.9, 0.0), 0.81)

        # -------------------------------------------------------- hydraulics
        vapour_load = boilup + reflux * 0.15
        dp = (180.0 * (vapour_load / 300.0) ** 1.8
              * (1.0 + self.fouling_trays / 100.0)) + 20.0
        dp = clamp(dp, 0.0, 500.0)
        self.flooding = dp > self.FLOOD_DP
        if self.flooding:
            # Entrainment destroys separation long before the column floods hard.
            reflux *= 0.7

        # ------------------------------------------------------- inventories
        drum_before = self.drum.y
        sump_before = self.sump.y
        drum_in, drum_out = condensed + mf1, reflux + distillate + mf1
        # Subcooled feed condenses ``_q_cond`` of the rising vapour at the
        # feed tray.  That condensate returns to the liquid traffic below the
        # tray; it does not disappear merely because it never reaches the
        # overhead condenser.  Omitting this return created a hidden material
        # sink equal to F-D-B (about 2.5 m3/h in T1 and 7 m3/h in T2 at the
        # commissioned point), making the specified product split impossible
        # whatever the controllers did.
        feed_condensate = max(getattr(self, "_q_cond", 0.0), 0.0)
        # The boot's water arrives with the feed (BOOT_WATER_FRAC), so
        # it is taken out of the column liquid here rather than created
        # on top of it: the plant-wide balance found it as a 0.3 m3/h
        # source (2026-09-05).
        boot_share = self.BOOT_WATER_FRAC if self.v_boot is not None else 0.0
        sump_in = feed * (1.0 - boot_share) + reflux + feed_condensate + mf2
        sump_out = boilup + bottoms + mf2
        self.drum.step((drum_in - drum_out)
                       / self.DRUM_VOLUME * 100.0 / 3600.0, dt)
        self.sump.step((sump_in - sump_out)
                       / self.SUMP_VOLUME * 100.0 / 3600.0, dt)
        self.record_inventory_balance(
            f"{self.COLUMN} reflux drum inventory", drum_in, drum_out,
            drum_before, self.drum.y, self.DRUM_VOLUME, dt)
        self.record_inventory_balance(
            f"{self.COLUMN} sump inventory", sump_in, sump_out,
            sump_before, self.sump.y, self.SUMP_VOLUME, dt)
        if self.v_boot is not None:
            boot_in = feed * self.BOOT_WATER_FRAC
            boot_out = self.v_boot.flow(max(p_col - P_STD, 0.0), 1.0)
            self.boot.step((boot_in - boot_out) / 3.0 * 100.0 / 3600.0, dt)

        # The balance is against the vapour that actually reaches the top:
        # what the subcooled feed already condensed is liquid, and counting
        # it as gas put a standing bias on the pressure that only the vent
        # could bleed, which is what pinned T1 four bar above its design
        # point no matter the condenser size.
        d_p = ((v_top - condensed) * 0.30 - vent) * P_STD / self.COLUMN_VOLUME
        self.pressure.step(d_p, dt)

        # -------------------------------------------------------- separation
        # Feed quality: a subcooled feed condenses vapour at the feed tray, so
        # less overhead reaches the condenser and the stripping section runs
        # more liquid. Bounded, because the point is the trend, not a rigorous
        # enthalpy balance the architecture forbids.
        t_bubble_feed = self.T_LIGHT * z_feed + self.T_HEAVY * (1.0 - z_feed)
        q_feed = clamp(1.0 + self.CP_LIQ_KJ_KG * 720.0
                       * max(t_bubble_feed - t_feed, 0.0)
                       / self.LATENT_KJ_PER_M3, 1.0, 1.25)
        internal_reflux = reflux + (q_feed - 1.0) * feed

        r_ratio = safe_div(internal_reflux, max(distillate, 0.5), 0.0)
        eta = 1.0 - math.exp(-0.42 * clamp(r_ratio, 0.0, 20.0))
        eta *= (1.0 - self.fouling_trays / 200.0)
        if self.flooding:
            eta *= 0.55
        if boilup < self.WEEP_BOILUP:
            # Weeping: the low-rate failure. Vapour no longer holds the liquid
            # on the trays and contacting collapses, so turning a column down
            # too far ruins separation just as surely as flooding it.
            eta *= clamp(boilup / self.WEEP_BOILUP, 0.30, 1.0)

        t_mid_k = 0.5 * (float(self.TT_ovhd.value) + float(self.TT_bot.value)) + 273.15
        t0_k = 0.5 * (self.T_LIGHT + self.T_HEAVY) + 273.15
        alpha = self.ALPHA * math.exp(
            self.DH_ALPHA_K * (1.0 / clamp(t_mid_k, 250.0, 700.0) - 1.0 / t0_k))
        sep = clamp(alpha, 1.05, 12.0) ** (self.STAGES * clamp(eta, 0.02, 1.0) * 0.55)
        # The trays split the COLUMN LIQUID, not the feed: feed, reflux and
        # the feed-tray condensate all land in that inventory first, and
        # what leaves it is the boilup and the bottoms. phi is the vapour
        # share of what leaves; the Fenske relation between the vapour and
        # the bottom liquid is unchanged, so the steady state is the one
        # the old split solved for - F z = D xd + B xb with the same
        # separation - reached through real holdups instead of lags.
        phi = safe_div(boilup, max(boilup + bottoms, 1e-3), 0.5)
        y_top, x_bot_out = solve_split(phi, self.x_col, sep)
        self.y_top, self.x_bot_out = y_top, x_bot_out

        # Light-key holdup, m3 of light key in each liquid inventory. What
        # enters at its inlet composition either leaves at the inventory's
        # own composition or is still there, so the component balance
        # closes to round-off: the accountability reading is the identity,
        # not an estimate. It goes non-zero only when an inventory clamps
        # at empty or full, which is a real loss and is reported as one.
        h = dt / 3600.0
        v_drum0 = drum_before / 100.0 * self.DRUM_VOLUME
        v_drum1 = self.drum.y / 100.0 * self.DRUM_VOLUME
        v_col0 = sump_before / 100.0 * self.SUMP_VOLUME
        v_col1 = self.sump.y / 100.0 * self.SUMP_VOLUME
        x_drum0, x_col0 = self.x_drum, self.x_col
        drum_light_in = condensed * y_top + mf1 * x_drum0
        drum_light_out = (reflux + distillate + mf1) * x_drum0
        col_light_in = (feed * z_feed + reflux * x_drum0
                        + feed_condensate * y_top + mf2 * x_col0)
        col_light_out = boilup * y_top + bottoms * x_bot_out + mf2 * x_col0
        l_drum = v_drum0 * x_drum0 + (drum_light_in - drum_light_out) * h
        l_col = v_col0 * x_col0 + (col_light_in - col_light_out) * h
        # An empty inventory has no composition: hold the last one until
        # liquid returns rather than divide by nothing.
        if v_drum1 > 0.05:
            self.x_drum = clamp(l_drum / v_drum1, 0.0, 1.0)
        if v_col1 > 0.05:
            self.x_col = clamp(l_col / v_col1, 0.0, 1.0)
        self.record_balance(
            f"{self.COLUMN} drum light key", drum_light_in, drum_light_out,
            (v_drum1 * self.x_drum - v_drum0 * x_drum0) / h,
            "m3/h light key", tolerance=1e-5, kind="component")
        self.record_balance(
            f"{self.COLUMN} column light key", col_light_in, col_light_out,
            (v_col1 * self.x_col - v_col0 * x_col0) / h,
            "m3/h light key", tolerance=1e-5, kind="component")
        self._vapour_light = (v_top - condensed) * y_top

        # The top vapour and the bottom liquid lead the drum: a composition
        # upset shows on the tray temperatures before the distillate
        # analyser, the way it really does.
        xr, xs = y_top, x_bot_out
        xd, xb = self.x_drum, x_bot_out

        self._q_cond = (q_feed - 1.0) * feed

        # --------------------------------------------------- tray temperatures
        p_corr = (self.pressure.y - (self.P_NOMINAL + P_STD)) * 7.5
        def bubble(x: float) -> float:
            return self.T_LIGHT * x + self.T_HEAVY * (1.0 - x) + p_corr

        t_ovhd = bubble(xr)
        t_bot = bubble(xb)
        self.t_cond.step(clamp(38.0 + 24.0 * (1.0 - cw / 1600.0), 25.0, 140.0), dt)

        # --------------------------------------------------------------- output
        self.publish_products(distillate, bottoms)
        self.bus[f"t{n - 4}_pressure_bara"] = self.pressure.y

        self.FT_feed.set(feed)
        self.FT_reflux.set(reflux)
        self.FT_dist.set(distillate)
        self.FT_bot.set(bottoms)
        self.FT_steam.set(steam)
        self.FT_cw.set(cw)
        self.FT_mf1.set(mf1)
        self.FT_mf2.set(mf2)
        self.TT_feed.set(t_feed)
        # Upper and lower tray temperatures read the section states, which
        # lead the product analysers by design: a composition upset shows on
        # TT before AT, so a temperature-inferential scheme built in the DCS
        # genuinely beats analyser feedback on speed, as it should.
        self.TT_t1.set(clamp(bubble(0.5 * (xr + 0.85)), 0, 250))
        self.TT_t2.set(clamp(0.5 * (bubble(xr) + bubble(xs)), 0, 300))
        self.TT_t3.set(clamp(bubble(0.5 * (xs + 0.25)), 0, 350))
        self.TT_ovhd.set(clamp(t_ovhd, 0, 250))
        self.TT_bot.set(clamp(t_bot, 0, 400))
        self.TT_cond.set(self.t_cond.y)
        self.PT_ovhd.set(clamp(self.pressure.y - P_STD, 0, 15))
        self.PT_bot.set(clamp(self.pressure.y - P_STD + dp / 1000.0, 0, 16))
        self.PT_p1.set(clamp(p_dis_1, 0, 25))
        self.PT_p2.set(clamp(p_dis_2, 0, 25))
        self.PDT.set(self.tx_pdt.step(dt, dp), Quality(self.tx_pdt.quality))
        self.LT_drum.set(self.tx_drum.step(dt, self.drum.y), Quality(self.tx_drum.quality))
        self.LT_sump.set(self.sump.y)
        if self.HAS_WATER_BOOT:
            self.LT_boot.set(self.boot.y)
        # The transmitter adds its noise AFTER the true-value clamp, so a
        # clean product (true ~0.05 mol%) would otherwise publish negative
        # excursions. An analyser's output stage clamps to its range.
        self.AT_dist.set(clamp(self.tx_dist.step(
            dt, clamp((1.0 - xd) * 100.0, 0, 10)), 0.0, 10.0),
            Quality(self.tx_dist.quality))
        self.AT_bot.set(clamp(self.tx_bot.step(
            dt, clamp(xb * 100.0, 0, 10)), 0.0, 10.0),
            Quality(self.tx_bot.quality))
        self.ZT.set(self.v_reflux.position)

        t[f"LSLL-{n}001"].set(self.drum.y < 10.0)
        t[f"LSHH-{n}001"].set(self.drum.y > 90.0)
        t[f"LSLL-{n}002"].set(self.sump.y < 10.0)
        t[f"PSHH-{n}001"].set(self.pressure.y - P_STD > self.P_NOMINAL + 4.0)
        t[f"UA-{n}001"].set(self.flooding)

    def save_state(self):
        return {"drum": self.drum.y, "sump": self.sump.y, "p": self.pressure.y,
                "xdr": self.x_drum, "xcol": self.x_col, "ytop": self.y_top,
                "xbo": self.x_bot_out, "boilup": self.boilup.y,
                "qc": getattr(self, "_q_cond", 0.0),
                "rp": self.reflux_pumps.state(), "bp": self.bottoms_pumps.state()}

    def load_state(self, s):
        self.drum.reset(s.get("drum", 50.0))
        self.sump.reset(s.get("sump", 50.0))
        self.pressure.reset(s.get("p", self.P_NOMINAL + P_STD))
        self._q_cond = float(s.get("qc", 0.0))
        # older snapshots carried the lagged product compositions as xd/xb
        self.x_drum = float(s.get("xdr", s.get("xd", 0.985)))
        self.x_col = float(s.get("xcol", 0.5))
        self.y_top = float(s.get("ytop", self.x_drum))
        self.x_bot_out = float(s.get("xbo", s.get("xb", 0.02)))
        self.boilup.reset(s.get("boilup", 0.0))
        self.reflux_pumps.restore(s.get("rp", {}))
        self.bottoms_pumps.restore(s.get("bp", {}))


class ColumnT1(DistillationColumn):
    code = "U500"
    name = "Distillation column T1"
    N = 5
    COLUMN = "T1"
    STAGES = 32
    ALPHA = 2.6
    P_NOMINAL = 8.0
    # Sized so the column's equilibrium sits near its 8 barg design point
    # rather than within half a bar of the 12 barg PSHH: the schedule's
    # trip only makes sense against a condenser that can hold the column
    # at nominal pressure.
    CONDENSER_UA = 620.0
    T_LIGHT = 118.0
    T_HEAVY = 268.0
    HAS_WATER_BOOT = True
    DIST_LABEL = "distillate to storage / T2 feed"
    BOT_LABEL = "bottoms recycle to D1"
    # The distillate header splits between the rundown to storage and the
    # T2 feed line, the way the reference plant runs it. A piping split,
    # not a control loop: T2 takes its share of whatever LIC-5001 draws.
    SPLIT_TO_T2 = 0.85
    PARAMETER_META = {
        "SPLIT_TO_T2": {"eu": "fraction", "description": "T1 distillate fraction routed to T2",
                        "lo": 0.0, "hi": 1.0},
    }

    def feed_stream(self):
        # Feed composition follows the reactor's additive dosing (the
        # quality handle): more additive, lighter liquid.
        return (float(self.bus.get("d3_liquid_to_t1", 0.0)),
                float(self.bus.get("d3_liquid_temperature", 60.0)),
                float(self.bus.get("d3_liquid_lightfrac", 0.46)))

    def publish_products(self, distillate, bottoms):
        # Stream routing per the Whitehouse reference AND our own PFD,
        # which always drew it this way (the model deviated, 2026-09):
        # distillate splits to storage and T2; bottoms - the unconverted
        # heavies - recycle to D1 and go back around through the reactor.
        self.bus["t1_distillate"] = distillate
        self.bus["t1_distillate_to_t2"] = distillate * self.SPLIT_TO_T2
        self.bus["t1_distillate_to_storage"] = (
            distillate * (1.0 - self.SPLIT_TO_T2))
        self.bus["t1_distillate_temperature"] = self.t_cond.y
        self.bus["t1_bottoms_recycle"] = bottoms
        self.bus["t1_bottoms_temperature"] = float(self.TT_bot.value)


class ColumnT2(DistillationColumn):
    code = "U600"
    name = "Distillation column T2"
    N = 6
    COLUMN = "T2"
    STAGES = 40
    ALPHA = 1.9
    DRUM_VOLUME = 23.0
    SUMP_VOLUME = 35.0
    COLUMN_VOLUME = 75.0
    P_NOMINAL = 5.5
    T_LIGHT = 96.0
    T_HEAVY = 232.0
    FLOOD_DP = 400.0
    STEAM_MAX = 30.0
    # T2 runs cooler overhead, so the same duty needs more surface.
    CONDENSER_UA = 600.0
    HAS_WATER_BOOT = False
    DIST_LABEL = "R2 product to blender"
    BOT_LABEL = "heavy product rundown"

    def feed_stream(self):
        # T2 is fed by the T1 distillate split (subcooled from the T1
        # reflux drum - the reference preheats it in E3, which we do not
        # have; the feed-quality term carries the subcooling honestly).
        return (float(self.bus.get("t1_distillate_to_t2", 0.0)),
                float(self.bus.get("t1_distillate_temperature", 60.0)),
                0.64)

    def publish_products(self, distillate, bottoms):
        self.bus["r2_product"] = distillate
        # The topology decision of 2026-09-05, measured before it was made:
        # these bottoms are mostly heavy key, and recycling them to D1
        # gave the plant no heavy exit at all - the feed drum flooded in
        # four closed-loop hours by conservation of mass, whatever the
        # loops did. They leave as the heavy product rundown. The recycle
        # duty moved to T1 bottoms (2026-09, Whitehouse routing, which our
        # PFD always drew): those are unconverted feed the reactor turns
        # over, so that loop converges where this one could not.
        self.bus["t2_heavy_rundown"] = bottoms
