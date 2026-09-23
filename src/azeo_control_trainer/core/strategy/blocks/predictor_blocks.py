"""Heat Balance Predictor block — first-principles energy balance with online
data reconciliation for the cumene hot oil heater.

Uses LMTD-based HX duty model, Therminol 66 properties, and recursive
least-squares (RLS) to adapt UA values to match measured plant data.
Computes an ideal supply temperature setpoint for feedforward control.
"""

from __future__ import annotations

import math

from ..model.block_base import FunctionBlock, BlockCategory, DataType
from ..model.block_registry import register_block

# ---------------------------------------------------------------------------
# Therminol 66 inline property helpers (avoid importing process module
# from strategy layer — keep strategy blocks self-contained).
# ---------------------------------------------------------------------------

def _t66_cp_btu(T_F: float) -> float:
    """Therminol 66 Cp [BTU/(lb*degF)] at T [degF]."""
    T_C = (T_F - 32.0) * 5.0 / 9.0
    T_C = max(0.0, min(T_C, 380.0))
    cp_si = 1496.0 + 3.313 * T_C  # J/(kg*K)
    return cp_si * 2.388459e-4     # -> BTU/(lb*degF)


def _t66_density_lbft3(T_F: float) -> float:
    """Therminol 66 density [lb/ft3] at T [degF]."""
    T_C = (T_F - 32.0) * 5.0 / 9.0
    T_C = max(0.0, min(T_C, 380.0))
    rho_kgm3 = 1020.6 - 0.5808 * T_C - 2.022e-4 * T_C ** 2
    return rho_kgm3 * 0.062428


def _bpd_to_lbps(bpd: float, T_F: float) -> float:
    """BPD -> lb/s at temperature T_F."""
    if bpd <= 0.0:
        return 0.0
    rho = _t66_density_lbft3(T_F)
    return bpd * 5.6146 / 86400.0 * rho


def _lmtd(dT_hot: float, dT_cold: float) -> float:
    """Log-mean temperature difference [degF]."""
    dT1 = max(dT_hot, 0.1)
    dT2 = max(dT_cold, 0.1)
    if abs(dT1 - dT2) < 0.01:
        return 0.5 * (dT1 + dT2)
    return (dT1 - dT2) / math.log(dT1 / dT2)


# ---------------------------------------------------------------------------
# HX design data — embedded here so block is self-contained.
# 7 active HXs (EA453 standby excluded).
# ---------------------------------------------------------------------------

_NUM_HX = 7


def _calc_design_duty(flow_bpd: float, T_in: float, T_out: float) -> float:
    """Compute design duty [MMBTU/hr] from Q = m * Cp * dT."""
    if flow_bpd <= 0:
        return 0.0
    T_avg = 0.5 * (T_in + T_out)
    m_lbs = _bpd_to_lbps(flow_bpd, T_avg)
    cp = _t66_cp_btu(T_avg)
    return m_lbs * cp * (T_in - T_out) * 3600.0 / 1e6


# (design_flow_bpd, process_temp_F, header, holdup_lb, design_T_in, design_T_out,
#  design_duty_mmbtu — stated value from P&ID or None to compute from flow×Cp×dT)
_HX_RAW = [
    # 0: EA442, Recycle Col. Reb., low-temp header
    (15299,  380.0, 'low',  8000.0, 537.0, 410.0, 20.821),
    # 1: EA451, #1 Cumene Col. Reb., high-temp header
    (35790,  470.0, 'high', 15000.0, 665.0, 496.0, 33.91),
    # 2: EA428, #2 Cumene Col. Reb., high-temp header
    (8851,   400.0, 'high', 5000.0, 665.0, 431.0, 9.089),
    # 3: EA426, PIPB Reb., high-temp header
    (5165,   450.0, 'high', 3500.0, 663.0, 478.0, 5.331),
    # 4: EA462, #1 Rectifier Col. Reb., low-temp header (compute from flow)
    (68679,  460.0, 'low',  25000.0, 537.0, 484.0, None),
    # 5: EA434, Transalky Feed Heater, low-temp header (compute from flow)
    (862,    300.0, 'low',  800.0, 440.0, 326.0, None),
    # 6: EA464, #1 Rect. Col. Preheater, low-temp header (compute from flow)
    (36529,  400.0, 'low',  15000.0, 495.0, 425.0, None),
]

# Use stated design duties where available; compute from flow×Cp×dT otherwise.
# This ensures predictor UAs match hotoil_hx_model.py exactly.
_HX_DESIGN_DUTIES = [
    d[6] if d[6] is not None else _calc_design_duty(d[0], d[4], d[5])
    for d in _HX_RAW
]


def _design_ua(duty_mmbtu: float, T_in: float, T_out: float,
               T_proc: float) -> float:
    """Back-calculate design UA [BTU/(hr*degF)] from design conditions."""
    dT_hot = T_in - T_proc
    dT_cold = T_out - T_proc
    if dT_hot <= 0 or dT_cold <= 0:
        return 1e4
    lm = _lmtd(dT_hot, dT_cold)
    return duty_mmbtu * 1e6 / max(lm, 1.0)


# Pre-compute design UAs from computed duties
_DESIGN_UAs = [
    _design_ua(_HX_DESIGN_DUTIES[i], _HX_RAW[i][4], _HX_RAW[i][5], _HX_RAW[i][1])
    for i in range(_NUM_HX)
]

# Process temperatures for each HX
_PROC_TEMPS = [d[1] for d in _HX_RAW]

# Which header feeds each HX: True = high-temp, False = low-temp
_IS_HIGH_HEADER = [d[2] == 'high' for d in _HX_RAW]

# Total design duty across all active HXs [MMBTU/hr]
_TOTAL_DESIGN_DUTY = sum(_HX_DESIGN_DUTIES)  # ~113.264


# ---------------------------------------------------------------------------
# Block implementation
# ---------------------------------------------------------------------------

@register_block
class HeatBalancePredictorBlock(FunctionBlock):
    """First-principles heat balance predictor with online data reconciliation.

    Reads 7 HX flows and outlet temps, computes total heat demand using
    LMTD model with reconciled UA values, and outputs the ideal supply
    temperature setpoint for feedforward control of TIC400.

    Data reconciliation uses recursive least-squares (RLS) to adapt each
    HX UA to match measured vs predicted duty, tracking fouling/degradation.
    """

    block_type = "HEAT_BAL_PREDICTOR"
    category = BlockCategory.APC
    display_name = "Heat Balance Predictor"
    description = (
        "First-principles heat balance feedforward with online UA "
        "reconciliation. Computes ideal supply temp SP from measured "
        "HX duties and reconciled model parameters."
    )

    # State constants
    IDLE, INITIALIZING, ACTIVE, FAULTED = 0, 1, 2, 3

    def _define_terminals(self):
        # --- Scalar inputs ---
        self.add_input("ENABLE", DataType.BOOL, True, "Enable predictor")
        self.add_input("T_SUPPLY", DataType.FLOAT, 665.0, "Supply header temp [degF]")
        self.add_input("T_RETURN", DataType.FLOAT, 477.0, "Return header temp [degF]")
        self.add_input("T_LO_SUPPLY", DataType.FLOAT, 537.0, "Low-temp header temp [degF]")
        self.add_input("Q_FIRED", DataType.FLOAT, 125.0, "Fired duty [MMBTU/hr]")
        self.add_input("PUMP_FLOW", DataType.FLOAT, 171175.0, "Total pump flow [BPD]")
        self.add_input("CONSTRAINT_OP", DataType.FLOAT, 50.0, "Draft ctrl output [0-100%]")
        self.add_input("FUEL_VALVE", DataType.FLOAT, 65.0, "Fuel valve position [0-100%]")
        self.add_input("PROD_RATE", DataType.FLOAT, 100.0, "Production rate [0-100%]")

        # --- Per-HX inputs (7 flows + 7 outlet temps) ---
        for i in range(_NUM_HX):
            self.add_input(f"FLOW_{i}", DataType.FLOAT, 0.0,
                           f"HX {i} flow [BPD]")
            self.add_input(f"TOUT_{i}", DataType.FLOAT, 0.0,
                           f"HX {i} outlet temp [degF]")

        # --- Outputs ---
        self.add_output("IDEAL_SP", DataType.FLOAT, 665.0,
                        "Ideal supply temp SP [degF]")
        self.add_output("Q_DEMAND", DataType.FLOAT, 107.0,
                        "Total heat demand [MMBTU/hr]")
        self.add_output("Q_DEMAND_F", DataType.FLOAT, 107.0,
                        "Filtered heat demand [MMBTU/hr]")
        self.add_output("Q_DEMAND_FF", DataType.FLOAT, _TOTAL_DESIGN_DUTY,
                        "FF demand from production rate [MMBTU/hr]")
        self.add_output("Q_FIRING_REQ", DataType.FLOAT, 126.0,
                        "Required firing [MMBTU/hr]")
        self.add_output("ETA_RECON", DataType.FLOAT, 85.0,
                        "Reconciled efficiency [%]")
        self.add_output("C_TEMP", DataType.FLOAT, 477.0,
                        "Blended return temp [degF]")
        self.add_output("C_FLOW", DataType.FLOAT, 171175.0,
                        "Total HX flow [BPD]")
        self.add_output("FOULING_IDX", DataType.FLOAT, 0.0,
                        "Overall fouling indicator [0-1]")
        self.add_output("ADAPT_TGT", DataType.FLOAT, 665.0,
                        "Adaptive supply target [degF]")
        self.add_output("ACTIVE", DataType.BOOL, False,
                        "Predictor is active")
        self.add_output("STATUS", DataType.INT, 0,
                        "0=IDLE 1=INIT 2=ACTIVE 3=FAULT")
        self.add_output("RECON_QUALITY", DataType.FLOAT, 0.0,
                        "Reconciliation quality [0-100]")

        # Per-HX reconciled outputs
        for i in range(_NUM_HX):
            self.add_output(f"UA_RATIO_{i}", DataType.FLOAT, 1.0,
                            f"HX {i} UA/UA_design ratio")
            self.add_output(f"Q_HX_PRED_{i}", DataType.FLOAT, 0.0,
                            f"HX {i} predicted duty [MMBTU/hr]")

        # --- Internal state ---
        self._state = self.IDLE
        self._init_timer = 0.0
        self._scan_count = 0

        # Reconciled UA values (start at design)
        self._ua = list(_DESIGN_UAs)
        # RLS covariance per UA (scalar, diagonal approx)
        self._P_ua = [1e8] * _NUM_HX
        # Reconciled efficiency
        self._eta_recon = 0.85
        # Adaptive target
        self._adapt_tgt = 665.0
        # Filtered demand
        self._Q_filt = 107.0
        self._Q_prev = 107.0
        # Gross error flags
        self._gross_err = [False] * _NUM_HX
        # Previous measurement (for rate-of-change)
        self._Q_demand_prev = 107.0
        # Observed thermal gain: K = Q_absorbed / (T_supply - T_return)
        # At design: 107 MMBTU/hr / 188°F ≈ 0.569 MMBTU/(hr·°F)
        # This captures pump flow, Cp, and heater pass geometry without
        # needing to know the internal mass flow model.
        self._K_thermal = 107.0 / 188.0

    def get_config_schema(self):
        return {
            # Feedforward tuning
            "ff_gain": (float, 1.0,
                        "Feedforward gain (0=off, 1=full)"),
            "filter_tau": (float, 30.0,
                           "Demand filter time constant [s]"),
            # Constraint adaptation
            "tgt_nominal": (float, 665.0,
                            "Nominal supply temp target [degF]"),
            "tgt_hi": (float, 695.0,
                       "Max adaptive target [degF]"),
            "tgt_lo": (float, 620.0,
                       "Min adaptive target [degF]"),
            "constraint_hi": (float, 0.85,
                              "Draft OP high alarm [0-1]"),
            "constraint_lo": (float, 0.30,
                              "Draft OP low alarm [0-1]"),
            "fuel_limit": (float, 0.92,
                           "Fuel valve high alarm [0-1]"),
            "adapt_rate": (float, 0.25,
                           "Target adaptation rate [degF/cycle]"),
            # Reconciliation
            "recon_enable": (bool, True,
                             "Enable online UA reconciliation"),
            "recon_period": (float, 60.0,
                             "Reconciliation period [s]"),
            "lambda_forget": (float, 0.995,
                              "RLS forgetting factor (0.98-0.999)"),
            "ua_lo_frac": (float, 0.3,
                           "Min UA as fraction of design"),
            "ua_hi_frac": (float, 1.5,
                           "Max UA as fraction of design"),
            "gross_err_threshold": (float, 3.5,
                                    "Normalized residual for gross error"),
            # Initialization
            "init_time": (float, 30.0,
                          "Init hold time before going ACTIVE [s]"),
            # Rate-of-change FF
            "dqdt_gain": (float, 0.0,
                          "Rate-of-change FF gain [s lookahead], 0=off"),
            "prod_ff_weight": (float, 0.6,
                               "Production FF weight (0=meas, 1=FF only)"),
        }

    def execute(self, dt: float):
        enable = bool(self.get_input("ENABLE"))
        p = self.config.params

        # ---- State machine ----
        if not enable:
            self._state = self.IDLE
            self._init_timer = 0.0
            self.set_output("ACTIVE", False)
            self.set_output("STATUS", self.IDLE)
            return

        if self._state == self.IDLE:
            self._state = self.INITIALIZING
            self._init_timer = 0.0

        if self._state == self.INITIALIZING:
            self._init_timer += dt
            init_time = float(p.get("init_time", 30.0))
            if self._init_timer >= init_time:
                self._state = self.ACTIVE
            else:
                self.set_output("ACTIVE", False)
                self.set_output("STATUS", self.INITIALIZING)
                return

        # ---- Read inputs ----
        T_s = float(self.get_input("T_SUPPLY"))
        T_r = float(self.get_input("T_RETURN"))
        T_ls = float(self.get_input("T_LO_SUPPLY"))
        Q_fired = float(self.get_input("Q_FIRED"))
        pump_flow = float(self.get_input("PUMP_FLOW"))
        constr_op = float(self.get_input("CONSTRAINT_OP")) / 100.0   # 0-100% → 0-1
        fuel_valve = float(self.get_input("FUEL_VALVE")) / 100.0    # 0-100% → 0-1

        flows = []
        t_outs = []
        for i in range(_NUM_HX):
            flows.append(float(self.get_input(f"FLOW_{i}")))
            t_outs.append(float(self.get_input(f"TOUT_{i}")))

        # ---- Step 1: Blended return conditions ----
        total_flow_bpd = sum(flows)
        if total_flow_bpd > 100.0:
            # Flow-weighted average return temperature
            weighted_sum = sum(f * t for f, t in zip(flows, t_outs))
            C_temp = weighted_sum / total_flow_bpd
        else:
            C_temp = T_r
            total_flow_bpd = pump_flow

        # ---- Step 2: Calculate measured HX duties ----
        Q_hx_meas = []
        for i in range(_NUM_HX):
            if flows[i] < 10.0:
                Q_hx_meas.append(0.0)
                continue
            # Determine inlet temp from header assignment
            T_hi = T_s if _IS_HIGH_HEADER[i] else T_ls
            T_avg = 0.5 * (T_hi + t_outs[i])
            m_lbs = _bpd_to_lbps(flows[i], T_avg)
            cp = _t66_cp_btu(T_avg)
            dT = T_hi - t_outs[i]
            Q_btu_s = m_lbs * cp * max(dT, 0.0)
            Q_hx_meas.append(Q_btu_s * 3600.0 / 1e6)  # MMBTU/hr

        Q_demand_meas = sum(Q_hx_meas)

        # ---- Step 3: Model-predicted HX duties (from reconciled UAs) ----
        Q_hx_pred = []
        for i in range(_NUM_HX):
            T_hi = T_s if _IS_HIGH_HEADER[i] else T_ls
            dT_hot = T_hi - _PROC_TEMPS[i]
            dT_cold = t_outs[i] - _PROC_TEMPS[i]
            if dT_hot > 0.1 and dT_cold > 0.1:
                lm = _lmtd(dT_hot, dT_cold)
                Q_pred = self._ua[i] * lm / 1e6  # MMBTU/hr
            else:
                Q_pred = 0.0
            Q_hx_pred.append(Q_pred)

        Q_demand_pred = sum(Q_hx_pred)

        # ---- Step 4: Online data reconciliation (RLS) ----
        recon_enable = bool(p.get("recon_enable", True))
        recon_period = float(p.get("recon_period", 60.0))
        self._scan_count += 1
        scans_per_recon = max(1, int(recon_period / max(dt, 0.001)))

        if recon_enable and self._scan_count >= scans_per_recon:
            self._scan_count = 0
            lam = float(p.get("lambda_forget", 0.995))
            ua_lo_frac = float(p.get("ua_lo_frac", 0.3))
            ua_hi_frac = float(p.get("ua_hi_frac", 1.5))
            ge_thresh = float(p.get("gross_err_threshold", 3.5))
            R_noise = 1.0  # measurement noise variance [MMBTU/hr]^2

            for i in range(_NUM_HX):
                if Q_hx_meas[i] < 0.01 and Q_hx_pred[i] < 0.01:
                    continue  # skip inactive HX

                # Residual: measured - predicted
                e_i = Q_hx_meas[i] - Q_hx_pred[i]

                # Sensitivity: dQ_pred / dUA_i = LMTD_i / 1e6
                T_hi = T_s if _IS_HIGH_HEADER[i] else T_ls
                dT_hot = T_hi - _PROC_TEMPS[i]
                dT_cold = t_outs[i] - _PROC_TEMPS[i]
                if dT_hot <= 0.1 or dT_cold <= 0.1:
                    continue
                H_i = _lmtd(dT_hot, dT_cold) / 1e6

                if H_i < 1e-10:
                    continue

                # Gross error detection
                sigma_pred = max(abs(H_i) * math.sqrt(self._P_ua[i]), 0.1)
                r_norm = abs(e_i) / sigma_pred
                if r_norm > ge_thresh:
                    self._gross_err[i] = True
                    continue  # skip update for this HX
                else:
                    self._gross_err[i] = False

                # RLS update (scalar per UA)
                S = H_i * self._P_ua[i] * H_i + R_noise
                K_i = self._P_ua[i] * H_i / S
                self._ua[i] += K_i * e_i
                self._P_ua[i] = (1.0 - K_i * H_i) * self._P_ua[i] / lam

                # Clamp UA to bounds
                ua_lo = _DESIGN_UAs[i] * ua_lo_frac
                ua_hi = _DESIGN_UAs[i] * ua_hi_frac
                self._ua[i] = max(ua_lo, min(self._ua[i], ua_hi))

                # Clamp covariance
                self._P_ua[i] = max(self._P_ua[i], 1e2)
                self._P_ua[i] = min(self._P_ua[i], 1e12)

            # Reconcile efficiency
            if Q_fired > 1.0:
                eta_meas = Q_demand_meas / Q_fired
                e_eta = eta_meas - self._eta_recon
                self._eta_recon += 0.05 * e_eta  # slow tracking
                self._eta_recon = max(0.50, min(self._eta_recon, 0.98))

        # ---- Step 5: Demand filtering ----
        filter_tau = float(p.get("filter_tau", 30.0))
        if filter_tau > 0.0 and dt > 0.0:
            alpha = dt / (filter_tau + dt)
        else:
            alpha = 1.0
        Q_filt = alpha * Q_demand_pred + (1.0 - alpha) * self._Q_prev
        self._Q_prev = Q_filt

        # Rate-of-change feedforward
        dqdt_gain = float(p.get("dqdt_gain", 0.0))
        if dqdt_gain > 0.0 and dt > 0.0:
            dQ_dt = (Q_demand_meas - self._Q_demand_prev) / dt
            Q_anticipated = Q_filt + dQ_dt * dqdt_gain
        else:
            Q_anticipated = Q_filt
        self._Q_demand_prev = Q_demand_meas

        # ---- Step 5b: Production rate feedforward ----
        prod_rate = float(self.get_input("PROD_RATE"))
        prod_frac = max(0.0, min(prod_rate / 100.0, 1.5))
        Q_demand_ff = _TOTAL_DESIGN_DUTY * prod_frac

        prod_ff_wt = float(p.get("prod_ff_weight", 0.6))
        if prod_ff_wt > 0.0 and prod_rate < 99.5:
            Q_anticipated = prod_ff_wt * Q_demand_ff + (1.0 - prod_ff_wt) * Q_anticipated

        self.set_output("Q_DEMAND_FF", round(Q_demand_ff, 3))

        # ---- Step 6: Ideal supply temperature ----
        # Use the observed thermal gain K = Q_absorbed / dT to compute
        # the supply temp needed for a given demand.  This avoids depending
        # on the internal mass-flow model of the heater (whose per-pass
        # flow split makes m*Cp*dT ≠ Q_absorbed at total pump flow).
        #
        # K_thermal [MMBTU/(hr·°F)] is reconciled from live data and
        # captures pump flow, oil Cp, and heater pass geometry implicitly.

        dT_obs = T_s - T_r
        if dT_obs > 5.0 and Q_demand_meas > 1.0:
            K_obs = Q_demand_meas / dT_obs
            # Smooth update — 5% tracking rate per reconciliation cycle
            self._K_thermal = 0.95 * self._K_thermal + 0.05 * K_obs

        if self._K_thermal > 0.01:
            T_ideal = T_r + Q_anticipated / self._K_thermal
        else:
            T_ideal = T_s  # fallback to current

        # ---- Step 7: Adaptive target (constraint pushing) ----
        adapt_rate = float(p.get("adapt_rate", 0.25))
        constr_hi = float(p.get("constraint_hi", 0.85))
        constr_lo = float(p.get("constraint_lo", 0.30))
        fuel_limit = float(p.get("fuel_limit", 0.92))
        tgt_hi = float(p.get("tgt_hi", 695.0))
        tgt_lo = float(p.get("tgt_lo", 620.0))

        # Proportional pushback based on constraint proximity
        tgt_delta = 0.0
        if constr_op > constr_hi:
            # Draft controller near limit — reduce target
            tgt_delta = -adapt_rate * (constr_op - constr_hi) / (1.0 - constr_hi + 0.01)
        elif constr_op < constr_lo:
            # Excess capacity — allow higher target
            tgt_delta = adapt_rate * (constr_lo - constr_op) / (constr_lo + 0.01)

        # Fuel valve constraint (hard pushback)
        if fuel_valve > fuel_limit:
            fuel_pushback = -adapt_rate * 2.0 * (fuel_valve - fuel_limit) / (1.0 - fuel_limit + 0.01)
            tgt_delta = min(tgt_delta, fuel_pushback)

        self._adapt_tgt += tgt_delta
        self._adapt_tgt = max(tgt_lo, min(self._adapt_tgt, tgt_hi))

        # ---- Step 8: Final SP with FF gain and clamping ----
        ff_gain = float(p.get("ff_gain", 1.0))
        T_sp = T_r + ff_gain * (T_ideal - T_r)
        T_sp = max(tgt_lo, min(T_sp, self._adapt_tgt))

        # ---- Step 9: Compute quality metrics ----
        # Fouling: average UA degradation
        ua_ratios = [self._ua[i] / _DESIGN_UAs[i] if _DESIGN_UAs[i] > 0 else 1.0
                     for i in range(_NUM_HX)]
        fouling_idx = max(0.0, 1.0 - sum(ua_ratios) / _NUM_HX)

        # Reconciliation quality: how well model matches measurements
        if Q_demand_meas > 0.1:
            q_err_pct = abs(Q_demand_pred - Q_demand_meas) / Q_demand_meas * 100
            recon_quality = max(0.0, 100.0 - q_err_pct * 10.0)
        else:
            recon_quality = 50.0

        # Required firing rate
        eta = max(self._eta_recon, 0.50)
        Q_firing_req = Q_anticipated / eta

        # ---- Write outputs ----
        self.set_output("IDEAL_SP", round(T_sp, 2))
        self.set_output("Q_DEMAND", round(Q_demand_meas, 3))
        self.set_output("Q_DEMAND_F", round(Q_filt, 3))
        self.set_output("Q_FIRING_REQ", round(Q_firing_req, 3))
        self.set_output("ETA_RECON", round(self._eta_recon * 100, 2))
        self.set_output("C_TEMP", round(C_temp, 2))
        self.set_output("C_FLOW", round(total_flow_bpd, 1))
        self.set_output("FOULING_IDX", round(fouling_idx, 4))
        self.set_output("ADAPT_TGT", round(self._adapt_tgt, 2))
        self.set_output("ACTIVE", True)
        self.set_output("STATUS", self.ACTIVE)
        self.set_output("RECON_QUALITY", round(recon_quality, 1))

        # Per-HX reconciled outputs
        for i in range(_NUM_HX):
            self.set_output(f"UA_RATIO_{i}", round(ua_ratios[i], 4))
            self.set_output(f"Q_HX_PRED_{i}", round(Q_hx_pred[i], 3))
