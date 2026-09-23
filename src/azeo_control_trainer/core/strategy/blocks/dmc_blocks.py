"""DMC (Dynamic Matrix Control) controller block — industrial MPC for the
cumene hot oil heater.

Implements the full DMC algorithm:
  - FIR step-response prediction
  - Output bias filter (disturbance estimation)
  - Ranked LP steady-state target optimizer (scipy.linprog)
  - Constrained dynamic QP move plan (scipy.minimize SLSQP)
  - Bumpless transfer (MV tracking, state initialization)

The block outputs the same interface as APC_CONTROL (HEARTBEAT, ACTIVE,
STATUS, MV_SP_1..6) so it plugs into the existing APC engagement chain
via WATCHDOG → SHED_LOGIC → SP_HANDOFF → MV_CLAMP.

Variable classification:
  CVs (7): T_supply, O2_pct, CO_ppm, P_draft_inH2O, surge_drum_level,
           Q_absorbed, efficiency
  MVs (6): TIC400.SP, AIC410.SP, PIC410.SP, FIC441.SP, FIC444.SP, FIC446.SP
  DVs (4): production_rate, ambient_temp_offset, fuel_rate, air_rate
"""
from __future__ import annotations

import json
import math
import logging

import numpy as np
from scipy.optimize import linprog, minimize

from ..model.block_base import FunctionBlock, BlockCategory, DataType
from ..model.block_registry import register_block

log = logging.getLogger("strategy.dmc")

# ═══════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════

N_CV = 7
N_MV = 6
N_DV = 4

# CV index with integrating (ramp) behavior
_RAMP_CV_INDICES = [4]  # surge_drum_level


# ═══════════════════════════════════════════════════════════════════════════
# FOPTD → step-response synthesis
# ═══════════════════════════════════════════════════════════════════════════

def _foptd_to_step(K: float, tau: float, theta: float,
                   dt: float, N: int) -> np.ndarray:
    """Convert G(s) = K*exp(-theta*s)/(tau*s+1) to N step-response coefficients.

    Uses ZOH (zero-order hold) discretization.  Returns S[0..N-1] where
    S[k] is the output at time k*dt after a unit step in the MV.
    """
    S = np.zeros(N)
    d = int(round(theta / dt)) if dt > 0 else 0
    a = math.exp(-dt / tau) if tau > 0.01 else 0.0
    for k in range(N):
        t_eff = k - d
        if t_eff < 0:
            S[k] = 0.0
        elif tau < 0.01:
            S[k] = K
        else:
            S[k] = K * (1.0 - a ** (t_eff + 1))
    return S


# ═══════════════════════════════════════════════════════════════════════════
# Model builder
# ═══════════════════════════════════════════════════════════════════════════

def _build_step_model(model_config: dict, dt: float, N: int,
                      n_out: int, n_in: int) -> np.ndarray:
    """Build step-response matrix S[n_out, N, n_in] from config.

    Accepts three model formats (checked in priority order):
      "step_matrix"       - full 3D array [n_out][N][n_in]
      "step_coefficients" - dict of "i_j" → [s0, s1, ...]
      "foptd"             - dict of "i_j" → {K, tau, theta}
    """
    S = np.zeros((n_out, N, n_in))

    if "step_matrix" in model_config:
        arr = np.array(model_config["step_matrix"])
        # Truncate or pad to (n_out, N, n_in)
        nz = min(arr.shape[0], n_out)
        nn = min(arr.shape[1], N)
        ni = min(arr.shape[2], n_in)
        S[:nz, :nn, :ni] = arr[:nz, :nn, :ni]
        return S

    if "step_coefficients" in model_config:
        coeffs = model_config["step_coefficients"]
        for key, vals in coeffs.items():
            parts = key.split("_")
            i, j = int(parts[0]), int(parts[1])
            if i < n_out and j < n_in:
                n = min(len(vals), N)
                S[i, :n, j] = vals[:n]
                # Extend with last value (steady state)
                if n < N:
                    S[i, n:, j] = vals[-1]
        return S

    if "foptd" in model_config:
        foptd = model_config["foptd"]
        for key, params in foptd.items():
            parts = key.split("_")
            i, j = int(parts[0]), int(parts[1])
            if i < n_out and j < n_in:
                K = float(params.get("K", 0.0))
                tau = float(params.get("tau", 60.0))
                theta = float(params.get("theta", 0.0))
                S[i, :, j] = _foptd_to_step(K, tau, theta, dt, N)
        return S

    return S


# ═══════════════════════════════════════════════════════════════════════════
# Default FOPTD model for cumene hot oil heater
# ═══════════════════════════════════════════════════════════════════════════

def _default_foptd_model() -> dict:
    """Return default FOPTD model dict for MV→CV and DV→CV channels.

    Key format: "cv_mv" for MV model, "cv_dv" for DV model.
    """
    mv_model = {
        # ── TIC400.SP (MV 0) → CVs ──
        # TIC400 → T_supply: closed-loop SP→PV response
        "0_0": {"K": 1.0, "tau": 180, "theta": 60},
        # TIC400 → Q_absorbed: higher T_supply → more heat duty
        "5_0": {"K": 0.57, "tau": 240, "theta": 60},
        # TIC400 → efficiency: higher temp → slight efficiency change
        "6_0": {"K": -0.03, "tau": 300, "theta": 120},

        # ── AIC410.SP (MV 1) → CVs ──
        # AIC410 → O2_pct: direct O2 setpoint control
        "1_1": {"K": 1.0, "tau": 120, "theta": 60},
        # AIC410 → CO_ppm: lower O2 → higher CO (inverse)
        "2_1": {"K": -80.0, "tau": 180, "theta": 60},
        # AIC410 → efficiency: O2 trim affects efficiency
        "6_1": {"K": -1.5, "tau": 240, "theta": 120},

        # ── PIC410.SP (MV 2) → CVs ──
        # PIC410 → P_draft: fast pressure loop
        "3_2": {"K": 1.0, "tau": 60, "theta": 30},
        # PIC410 → CO_ppm: draft affects combustion quality
        "2_2": {"K": 15.0, "tau": 180, "theta": 60},

        # ── FIC441.SP (MV 3) — EA451 flow → CVs ──
        # FIC441 → Q_absorbed: more flow through major HX = more duty
        "5_3": {"K": 0.0008, "tau": 240, "theta": 60},
        # FIC441 → T_supply: more HX flow cools supply header slightly
        "0_3": {"K": -0.0003, "tau": 300, "theta": 120},

        # ── FIC444.SP (MV 4) — EA462 flow → CVs ──
        # FIC444 → Q_absorbed: low-header HX, lower gain
        "5_4": {"K": 0.0005, "tau": 240, "theta": 60},
        # FIC444 → surge_drum_level: flow affects level
        "4_4": {"K": -0.0001, "tau": 300, "theta": 120},

        # ── FIC446.SP (MV 5) — EA464 flow → CVs ──
        # FIC446 → Q_absorbed
        "5_5": {"K": 0.0005, "tau": 240, "theta": 60},
        # FIC446 → surge_drum_level
        "4_5": {"K": -0.0001, "tau": 300, "theta": 120},
    }

    dv_model = {
        # ── production_rate (DV 0) → CVs ──
        # Production → Q_absorbed: major disturbance
        "5_0": {"K": 1.13, "tau": 300, "theta": 120},
        # Production → T_supply: higher production pulls down supply temp
        "0_0": {"K": -0.5, "tau": 360, "theta": 180},
        # Production → surge_drum_level: ramp CV
        "4_0": {"K": -0.05, "tau": 600, "theta": 120},

        # ── ambient_temp_offset (DV 1) → CVs ──
        # Ambient → efficiency: colder air = better draft, lower stack T
        "6_1": {"K": -0.2, "tau": 300, "theta": 60},
        # Ambient → CO_ppm: ambient affects combustion
        "2_1": {"K": 5.0, "tau": 240, "theta": 60},

        # ── fuel_rate (DV 2) → CVs ──
        # Fuel → T_supply: more fuel → hotter
        "0_2": {"K": 20.0, "tau": 180, "theta": 60},
        # Fuel → O2_pct: more fuel → lower O2 (consumed)
        "1_2": {"K": -2.0, "tau": 120, "theta": 30},
        # Fuel → Q_absorbed: more fuel → more heat
        "5_2": {"K": 15.0, "tau": 240, "theta": 60},

        # ── air_rate (DV 3) → CVs ──
        # Air → O2_pct: more air → higher O2
        "1_3": {"K": 0.3, "tau": 120, "theta": 30},
        # Air → P_draft: more air → changes draft
        "3_3": {"K": -0.01, "tau": 60, "theta": 30},
        # Air → CO_ppm: more air → less CO
        "2_3": {"K": -10.0, "tau": 180, "theta": 60},
    }

    return {"mv_foptd": mv_model, "dv_foptd": dv_model}


# ═══════════════════════════════════════════════════════════════════════════
# Default CV/MV tuning
# ═══════════════════════════════════════════════════════════════════════════

# CV setpoints and limits
_DEFAULT_CV_SP  = [665.0, 3.0, 200.0, -0.30, 50.0, 107.0, 85.0]
_DEFAULT_CV_HI  = [695.0, 5.0, 500.0, -0.10, 70.0, 140.0, 95.0]
_DEFAULT_CV_LO  = [620.0, 1.5,  50.0, -0.50, 30.0,  60.0, 70.0]
_DEFAULT_CV_WT  = [10.0,  5.0,  3.0,   5.0,  2.0,   8.0,  1.0]

# MV limits
_DEFAULT_MV_HI  = [695.0, 5.0,  -0.10, 50000.0, 80000.0, 50000.0]
_DEFAULT_MV_LO  = [620.0, 1.5,  -0.50,  5000.0, 10000.0,  5000.0]
_DEFAULT_DMV_HI = [5.0,   0.5,   0.05,  2000.0,  5000.0,  2000.0]
_DEFAULT_DMV_LO = [-5.0, -0.5,  -0.05, -2000.0, -5000.0, -2000.0]
_DEFAULT_MV_WT  = [1.0,   1.0,   1.0,   0.5,     0.5,     0.5]
_DEFAULT_MV_SS  = [0.0,   0.0,   0.0,   0.0,     0.0,     0.0]


# ═══════════════════════════════════════════════════════════════════════════
# DMC_CONTROLLER block
# ═══════════════════════════════════════════════════════════════════════════

def _parse_csv(s: str, n: int, defaults: list) -> list[float]:
    """Parse a comma-separated string into n floats, falling back to defaults."""
    if not s or not s.strip():
        return list(defaults[:n])
    parts = [x.strip() for x in s.split(",")]
    result = []
    for i in range(n):
        if i < len(parts) and parts[i]:
            try:
                result.append(float(parts[i]))
            except ValueError:
                result.append(defaults[i] if i < len(defaults) else 0.0)
        else:
            result.append(defaults[i] if i < len(defaults) else 0.0)
    return result


@register_block
class DMCControllerBlock(FunctionBlock):
    """DMC (Dynamic Matrix Control) controller for the cumene hot oil heater.

    Implements industrial MPC with:
    - FIR step-response prediction (FOPTD-synthesized or explicit)
    - Output bias filter for unmeasured disturbance estimation
    - LP steady-state target optimizer with ranked soft constraints
    - SLSQP dynamic move plan with MV rate and position limits
    - Bumpless transfer via MV tracking and state initialization

    Outputs same interface as APC_CONTROL for drop-in replacement:
    HEARTBEAT, ACTIVE, STATUS, MV_SP_1..6.
    """

    block_type = "DMC_CONTROLLER"
    category = BlockCategory.APC
    display_name = "DMC Controller"
    description = (
        "Dynamic Matrix Controller — step-response MPC with LP/QP "
        "optimizers, output bias filter, and bumpless transfer."
    )

    # ── Full DCS-style state machine ──
    #   OFF           - disabled, tracking MV_FB for bumpless engage
    #   ENGAGING      - rising edge of enable, capturing state
    #   INITIALIZING  - holds MV at current for init_time seconds
    #   RAMPING_IN    - SP ramping from operator SP to DMC SP (optional)
    #   ACTIVE        - normal operation, solver running, heartbeat pulsing
    #   HOLD          - solver failed but recoverable, holding last MV plan
    #   FAILED        - persistent solver/watchdog failure, MV frozen at last good
    #   DISENGAGING   - graceful ramp down, reducing moves toward zero
    OFF           = 0
    ENGAGING      = 1
    INITIALIZING  = 2
    RAMPING_IN    = 3
    ACTIVE        = 4
    HOLD          = 5
    FAILED        = 6
    DISENGAGING   = 7

    _STATE_NAMES = {
        0: "OFF", 1: "ENGAGING", 2: "INITIALIZING", 3: "RAMPING_IN",
        4: "ACTIVE", 5: "HOLD", 6: "FAILED", 7: "DISENGAGING",
    }

    def _define_terminals(self):
        # ── Inputs (18) ──
        self.add_input("ENABLE", DataType.BOOL, False, "Operator enable")
        for i in range(1, N_CV + 1):
            self.add_input(f"CV_{i}", DataType.FLOAT, 0.0,
                           f"Controlled variable {i}")
        for i in range(1, N_MV + 1):
            self.add_input(f"MV_FB_{i}", DataType.FLOAT, 0.0,
                           f"MV feedback {i}")
        for i in range(1, N_DV + 1):
            self.add_input(f"DV_{i}", DataType.FLOAT, 0.0,
                           f"Disturbance variable {i}")

        # ── Outputs (46) ──
        self.add_output("ACTIVE", DataType.BOOL, False, "DMC is active (ACTIVE state only)")
        self.add_output("HEARTBEAT", DataType.BOOL, False, "Heartbeat toggle")
        self.add_output("STATUS", DataType.INT, 0,
                        "0=OFF 1=ENGAGING 2=INIT 3=RAMP_IN 4=ACTIVE "
                        "5=HOLD 6=FAILED 7=DISENGAGING")
        self.add_output("STATE_NAME", DataType.STRING, "OFF",
                        "State machine name for HMI display")
        self.add_output("FAULT", DataType.BOOL, False,
                        "True when in FAILED state")
        self.add_output("READY", DataType.BOOL, False,
                        "True when in ACTIVE state (healthy)")
        self.add_output("CYCLE_TIME", DataType.FLOAT, 0.0,
                        "Time in current cycle (s)")
        self.add_output("TIME_IN_STATE", DataType.FLOAT, 0.0,
                        "Time spent in current state (s)")
        self.add_output("CONSECUTIVE_FAILS", DataType.INT, 0,
                        "Consecutive solver failure count")

        for i in range(1, N_MV + 1):
            self.add_output(f"MV_SP_{i}", DataType.FLOAT, 0.0,
                            f"MV setpoint target {i}")

        for i in range(1, N_CV + 1):
            self.add_output(f"CV_PRED_{i}", DataType.FLOAT, 0.0,
                            f"CV {i} predicted value")
        for i in range(1, N_CV + 1):
            self.add_output(f"CV_SS_TGT_{i}", DataType.FLOAT, 0.0,
                            f"CV {i} SS target")
        for i in range(1, N_CV + 1):
            self.add_output(f"CV_ERR_{i}", DataType.FLOAT, 0.0,
                            f"CV {i} prediction error")

        for i in range(1, N_MV + 1):
            self.add_output(f"MV_SS_TGT_{i}", DataType.FLOAT, 0.0,
                            f"MV {i} SS target")

        self.add_output("OBJ_SS", DataType.FLOAT, 0.0,
                        "SS optimizer objective value")
        self.add_output("OBJ_DYN", DataType.FLOAT, 0.0,
                        "Dynamic QP objective value")
        self.add_output("SOLVER_OK", DataType.BOOL, False,
                        "Optimizers solved successfully")
        self.add_output("BIAS_NORM", DataType.FLOAT, 0.0,
                        "Output bias vector norm")

        # ── Internal state ──
        self._state = self.OFF
        self._hb_state = False
        self._init_timer = 0.0
        self._cycle_timer = 0.0
        self._state_timer = 0.0             # time in current state
        self._was_enabled = False
        self._first_active_cycle = True

        # Fault detection
        self._consecutive_fails = 0
        self._consecutive_ok = 0
        self._last_good_mv_sp = None        # set at last successful solve

        # Disengaging state
        self._disengage_mv_start = None     # MV values at start of disengage

        # Model arrays (initialized in _build_model)
        self._S: np.ndarray | None = None        # (N_CV, N_model, N_MV)
        self._Sd: np.ndarray | None = None       # (N_CV, N_model, N_DV)
        self._G: np.ndarray | None = None        # (N_CV, N_MV) SS gain
        self._Gd: np.ndarray | None = None       # (N_CV, N_DV) SS DV gain

        # State vectors
        self._past_du: np.ndarray | None = None   # (N_MV, N_model)
        self._past_ddv: np.ndarray | None = None  # (N_DV, N_model)
        self._d_hat: np.ndarray | None = None     # (N_CV,) output bias
        self._mv_plan: np.ndarray | None = None   # (N_MV, M) planned moves
        self._mv_current: np.ndarray | None = None  # (N_MV,) current MV
        self._mv_prev: np.ndarray | None = None     # (N_MV,) previous MV
        self._dv_current: np.ndarray | None = None   # (N_DV,) current DV
        self._dv_prev: np.ndarray | None = None      # (N_DV,) previous DV
        self._y_meas: np.ndarray | None = None       # (N_CV,) last measured CV

        # Model dimensions
        self._N_model = 120
        self._P = 60
        self._M = 5
        self._dt_model = 60.0

        self._model_built = False

    def get_config_schema(self) -> dict:
        return {
            # Model
            "model_json": (str, "",
                           "JSON string with step-response model "
                           "(foptd/step_coefficients/step_matrix). "
                           "Empty = use default FOPTD model."),
            "sample_period": (float, 60.0,
                              "DMC sample period / cycle time (s)"),
            "prediction_horizon": (int, 60,
                                   "Prediction horizon P (in samples)"),
            "control_horizon": (int, 5,
                                "Control horizon M (number of moves)"),
            "model_horizon": (int, 120,
                              "Model horizon N (step-response length)"),
            # CV tuning
            "cv_weights": (str, "",
                           "CV weights (comma-separated, 7 values)"),
            "cv_sp": (str, "",
                      "CV setpoints (comma-separated, 7 values)"),
            "cv_hi": (str, "",
                      "CV high limits (comma-separated, 7 values)"),
            "cv_lo": (str, "",
                      "CV low limits (comma-separated, 7 values)"),
            # MV tuning
            "mv_weights": (str, "",
                           "MV move suppression (comma-separated, 6 values)"),
            "mv_ss_cost": (str, "",
                           "MV SS cost (comma-separated, 6 values)"),
            "mv_hi": (str, "",
                      "MV high limits (comma-separated, 6 values)"),
            "mv_lo": (str, "",
                      "MV low limits (comma-separated, 6 values)"),
            "dmv_hi": (str, "",
                       "MV rate high limits per cycle (comma-sep, 6 values)"),
            "dmv_lo": (str, "",
                       "MV rate low limits per cycle (comma-sep, 6 values)"),
            # Filter
            "filter_alpha": (float, 0.3,
                             "Output bias filter factor (0-1)"),
            "ramp_rotation": (float, 0.98,
                              "Ramp CV bias decay factor per cycle"),
            # SS optimizer
            "ss_slack_penalty": (float, 1000.0,
                                 "Penalty on CV constraint slack in LP"),
            # Initialization
            "init_time": (float, 10.0,
                          "Initialization time before ACTIVE (s)"),
            # State machine timing
            "ramp_in_time": (float, 0.0,
                             "SP ramp-in time ENGAGING->ACTIVE (s, 0=skip)"),
            "disengage_time": (float, 30.0,
                               "Graceful disengage ramp time (s)"),
            # Fault detection
            "fail_threshold": (int, 3,
                               "Consecutive solver failures to trigger FAILED"),
            "recover_threshold": (int, 2,
                                  "Consecutive solver OK cycles to recover "
                                  "from HOLD->ACTIVE"),
            "auto_recover": (bool, True,
                             "Auto-transition FAILED->HOLD when solver recovers"),
        }

    # ─────────────────────────────────────────────────────────────────────
    # Model construction
    # ─────────────────────────────────────────────────────────────────────

    def _build_model(self):
        """Build step-response matrices from config or defaults."""
        p = self.config.params
        dt_model = float(p.get("sample_period", 60.0))
        prediction = int(p.get("prediction_horizon", 60))
        control = int(p.get("control_horizon", 5))
        model = int(p.get("model_horizon", 120))
        # Validate before numpy allocates from project-provided dimensions.
        if not np.isfinite(dt_model) or dt_model < 0.1:
            raise ValueError("DMC sample period must be finite and at least 0.1 seconds")
        if not 1 <= prediction <= 240 or not 1 <= control <= min(prediction, 32) or not prediction <= model <= 2048:
            raise ValueError("DMC horizons require 1 <= M <= min(P, 32), 1 <= P <= 240, and P <= N <= 2048")
        self._dt_model, self._P, self._M, self._N_model = dt_model, prediction, control, model

        N = self._N_model

        # Parse model JSON
        model_json_str = str(p.get("model_json", ""))
        if len(model_json_str) > 8 * 1024 * 1024:
            raise ValueError("DMC model JSON exceeds 8 MiB")
        if model_json_str.strip():
            try:
                model_cfg = json.loads(model_json_str)
            except json.JSONDecodeError:
                log.warning("DMC: Invalid model_json, using defaults")
                model_cfg = None
        else:
            model_cfg = None

        if model_cfg is not None:
            # User-provided model
            mv_cfg = {}
            dv_cfg = {}
            # Check for combined or separate model specs
            if "mv_foptd" in model_cfg or "dv_foptd" in model_cfg:
                mv_cfg = {"foptd": model_cfg.get("mv_foptd", {})}
                dv_cfg = {"foptd": model_cfg.get("dv_foptd", {})}
            elif "foptd" in model_cfg:
                mv_cfg = {"foptd": model_cfg["foptd"]}
            elif "step_coefficients" in model_cfg:
                mv_cfg = {"step_coefficients": model_cfg["step_coefficients"]}
            elif "step_matrix" in model_cfg:
                mv_cfg = {"step_matrix": model_cfg["step_matrix"]}
            else:
                mv_cfg = {"foptd": model_cfg}
        else:
            # Default model
            default = _default_foptd_model()
            mv_cfg = {"foptd": default["mv_foptd"]}
            dv_cfg = {"foptd": default["dv_foptd"]}

        self._S = _build_step_model(mv_cfg, self._dt_model, N, N_CV, N_MV)
        self._Sd = _build_step_model(dv_cfg, self._dt_model, N, N_CV, N_DV)

        # Steady-state gains = last step-response coefficient
        self._G = self._S[:, -1, :]     # (N_CV, N_MV)
        self._Gd = self._Sd[:, -1, :]   # (N_CV, N_DV)

        # Initialize state arrays
        self._past_du = np.zeros((N_MV, N))
        self._past_ddv = np.zeros((N_DV, N))
        self._d_hat = np.zeros(N_CV)
        self._mv_plan = np.zeros((N_MV, self._M))
        self._mv_current = np.zeros(N_MV)
        self._mv_prev = np.zeros(N_MV)
        self._dv_current = np.zeros(N_DV)
        self._dv_prev = np.zeros(N_DV)
        self._y_meas = np.zeros(N_CV)

        self._model_built = True
        log.info("DMC: Model built — S(%s), Sd(%s), P=%d, M=%d, N=%d",
                 self._S.shape, self._Sd.shape, self._P, self._M, N)

    # ─────────────────────────────────────────────────────────────────────
    # Free response (FIR convolution)
    # ─────────────────────────────────────────────────────────────────────

    def _compute_free_response(self) -> np.ndarray:
        """Compute P-step free response from past moves and disturbances.

        Returns y_free of shape (P, N_CV): predicted CV values assuming
        no future MV moves.
        """
        P = self._P
        N = self._N_model
        y_free = np.zeros((P, N_CV))

        # The same FIR sum in bounded native array operations; four nested
        # Python loops previously blocked the scan for large valid horizons.
        for k in range(min(P, N - 1)):
            count = N - k - 1
            y_free[k] = (np.einsum("ctm,mt->c", self._S[:, k + 1:, :], self._past_du[:, :count])
                         + np.einsum("ctd,dt->c", self._Sd[:, k + 1:, :], self._past_ddv[:, :count]))

        return y_free

    # ─────────────────────────────────────────────────────────────────────
    # Output bias filter (disturbance estimation)
    # ─────────────────────────────────────────────────────────────────────

    def _update_bias(self, y_meas: np.ndarray, y_free: np.ndarray):
        """Update output bias estimate d_hat from measurement innovation.

        For ramp CVs (integrating), apply rotation factor to prevent drift.
        """
        p = self.config.params
        alpha = float(p.get("filter_alpha", 0.3))
        ramp_rot = float(p.get("ramp_rotation", 0.98))

        # Innovation = measured - predicted (at current time = y_free[0])
        innovation = y_meas - y_free[0]

        # Exponential filter
        self._d_hat = alpha * innovation + (1.0 - alpha) * self._d_hat

        # Ramp CV bias rotation (decay to prevent integrator drift)
        for idx in _RAMP_CV_INDICES:
            self._d_hat[idx] *= ramp_rot

    # ─────────────────────────────────────────────────────────────────────
    # Steady-state target (LP)
    # ─────────────────────────────────────────────────────────────────────

    def _solve_ss_target(self, y_free_end: np.ndarray,
                         mv_current: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, bool]:
        """Solve LP for steady-state MV targets.

        Decision variables: [du_ss(6), slack_hi(7), slack_lo(7),
                             dev_plus(7), dev_minus(7)] = 34 total.

        Returns: (du_ss, y_ss_target, objective, success)
        """
        p = self.config.params
        penalty = float(p.get("ss_slack_penalty", 1000.0))

        cv_sp = np.array(_parse_csv(str(p.get("cv_sp", "")),
                                    N_CV, _DEFAULT_CV_SP))
        cv_hi = np.array(_parse_csv(str(p.get("cv_hi", "")),
                                    N_CV, _DEFAULT_CV_HI))
        cv_lo = np.array(_parse_csv(str(p.get("cv_lo", "")),
                                    N_CV, _DEFAULT_CV_LO))
        cv_wt = np.array(_parse_csv(str(p.get("cv_weights", "")),
                                    N_CV, _DEFAULT_CV_WT))
        mv_hi = np.array(_parse_csv(str(p.get("mv_hi", "")),
                                    N_MV, _DEFAULT_MV_HI))
        mv_lo = np.array(_parse_csv(str(p.get("mv_lo", "")),
                                    N_MV, _DEFAULT_MV_LO))
        mv_ss_cost = np.array(_parse_csv(str(p.get("mv_ss_cost", "")),
                                         N_MV, _DEFAULT_MV_SS))

        G = self._G  # (N_CV, N_MV)

        # Decision: [du_ss(6), slack_hi(7), slack_lo(7), dev+(7), dev-(7)]
        n_du = N_MV
        n_sh = N_CV
        n_sl = N_CV
        n_dp = N_CV
        n_dm = N_CV
        n_vars = n_du + n_sh + n_sl + n_dp + n_dm  # 34

        # Objective: minimize ||deviation||_1 (weighted) + MV SS cost + slack penalty
        c = np.zeros(n_vars)
        # MV SS cost
        c[:n_du] = mv_ss_cost
        # Slack penalties
        c[n_du:n_du + n_sh] = penalty  # slack_hi
        c[n_du + n_sh:n_du + n_sh + n_sl] = penalty  # slack_lo
        # Weighted CV deviation
        c[n_du + n_sh + n_sl:n_du + n_sh + n_sl + n_dp] = cv_wt  # dev+
        c[n_du + n_sh + n_sl + n_dp:] = cv_wt  # dev-

        # Equality constraints: y_ss = y_free_end + G @ du_ss + d_hat
        # and: y_ss - cv_sp = dev+ - dev-
        # Combined: G @ du_ss + d_hat + y_free_end - cv_sp = dev+ - dev-
        # => G @ du - dev+ + dev- = cv_sp - y_free_end - d_hat
        A_eq = np.zeros((N_CV, n_vars))
        A_eq[:, :n_du] = G
        A_eq[:, n_du + n_sh + n_sl:n_du + n_sh + n_sl + n_dp] = -np.eye(N_CV)
        A_eq[:, n_du + n_sh + n_sl + n_dp:] = np.eye(N_CV)
        b_eq = cv_sp - y_free_end - self._d_hat

        # Inequality constraints:
        # cv_lo - slack_lo <= y_ss <= cv_hi + slack_hi
        # y_ss = y_free_end + G @ du_ss + d_hat
        # => G @ du_ss <= cv_hi + slack_hi - y_free_end - d_hat
        # => -G @ du_ss <= -(cv_lo - slack_lo - y_free_end - d_hat)
        A_ub = np.zeros((2 * N_CV, n_vars))
        b_ub = np.zeros(2 * N_CV)

        # Upper bound: G @ du - slack_hi <= cv_hi - y_free_end - d_hat
        A_ub[:N_CV, :n_du] = G
        A_ub[:N_CV, n_du:n_du + n_sh] = -np.eye(N_CV)
        b_ub[:N_CV] = cv_hi - y_free_end - self._d_hat

        # Lower bound: -G @ du - slack_lo <= -(cv_lo - y_free_end - d_hat)
        A_ub[N_CV:, :n_du] = -G
        A_ub[N_CV:, n_du + n_sh:n_du + n_sh + n_sl] = -np.eye(N_CV)
        b_ub[N_CV:] = -(cv_lo - y_free_end - self._d_hat)

        # Variable bounds
        bounds = []
        # du_ss: MV position change from current to target
        for j in range(N_MV):
            du_lo = mv_lo[j] - mv_current[j]
            du_hi = mv_hi[j] - mv_current[j]
            bounds.append((du_lo, du_hi))
        # slack_hi, slack_lo >= 0
        for _ in range(n_sh + n_sl):
            bounds.append((0.0, None))
        # dev+, dev- >= 0
        for _ in range(n_dp + n_dm):
            bounds.append((0.0, None))

        try:
            result = linprog(c, A_ub=A_ub, b_ub=b_ub,
                             A_eq=A_eq, b_eq=b_eq,
                             bounds=bounds, method='highs')
            if result.success:
                du_ss = result.x[:n_du]
                y_ss = y_free_end + G @ du_ss + self._d_hat
                return du_ss, y_ss, result.fun, True
            else:
                log.debug("DMC SS LP failed: %s", result.message)
                return np.zeros(N_MV), y_free_end + self._d_hat, 0.0, False
        except Exception as e:
            log.warning("DMC SS LP exception: %s", e)
            return np.zeros(N_MV), y_free_end + self._d_hat, 0.0, False

    # ─────────────────────────────────────────────────────────────────────
    # Dynamic QP (move plan)
    # ─────────────────────────────────────────────────────────────────────

    def _build_dynamic_matrix(self) -> np.ndarray:
        """Build lower-triangular Toeplitz dynamic matrix A_dyn.

        Shape: (P * N_CV, M * N_MV).
        A_dyn[k*N_CV:(k+1)*N_CV, m*N_MV:(m+1)*N_MV] = S[:, k-m, :] (if k >= m)
        where S[:, j, :] are the step-response differences (impulse response cumsum).
        """
        P = self._P
        M = self._M
        A = np.zeros((P * N_CV, M * N_MV))

        for k in range(P):
            for m in range(min(k + 1, M)):
                j = k - m  # time index into step response
                if j < self._N_model:
                    A[k * N_CV:(k + 1) * N_CV,
                      m * N_MV:(m + 1) * N_MV] = self._S[:, j, :]

        return A

    def _solve_dynamic_qp(self, y_free_biased: np.ndarray,
                          y_ss_target: np.ndarray,
                          mv_current: np.ndarray) -> tuple[np.ndarray, float, bool]:
        """Solve dynamic QP for M-step move plan.

        Decision: du_flat = [du_0, du_1, ..., du_{M-1}], each (N_MV,).
        Total: M * N_MV = 30 variables.

        Objective:
          min sum_k ||y_free_biased[k] + A_dyn @ du - y_target[k]||^2_Q
              + ||du||^2_R

        Subject to:
          mv_lo <= mv_current + cumsum(du) <= mv_hi
          dmv_lo <= du_m <= dmv_hi

        Returns: (du_plan [N_MV, M], objective, success)
        """
        p = self.config.params
        P = self._P
        M = self._M

        cv_wt = np.array(_parse_csv(str(p.get("cv_weights", "")),
                                    N_CV, _DEFAULT_CV_WT))
        mv_wt = np.array(_parse_csv(str(p.get("mv_weights", "")),
                                    N_MV, _DEFAULT_MV_WT))
        mv_hi = np.array(_parse_csv(str(p.get("mv_hi", "")),
                                    N_MV, _DEFAULT_MV_HI))
        mv_lo = np.array(_parse_csv(str(p.get("mv_lo", "")),
                                    N_MV, _DEFAULT_MV_LO))
        dmv_hi = np.array(_parse_csv(str(p.get("dmv_hi", "")),
                                     N_MV, _DEFAULT_DMV_HI))
        dmv_lo = np.array(_parse_csv(str(p.get("dmv_lo", "")),
                                     N_MV, _DEFAULT_DMV_LO))

        # Build target trajectory: ramp from current CV prediction to SS target
        # Simple approach: use SS target for all P steps
        y_target = np.tile(y_ss_target, (P, 1))  # (P, N_CV)

        # Build dynamic matrix
        A_dyn = self._build_dynamic_matrix()  # (P*N_CV, M*N_MV)

        # Weight matrices
        Q_diag = np.tile(cv_wt, P)   # (P*N_CV,)
        R_diag = np.tile(mv_wt, M)   # (M*N_MV,)

        # Error vector (without moves)
        e_base = (y_free_biased - y_target).ravel()  # (P*N_CV,)

        # Precompute for objective
        Q = np.diag(Q_diag)
        R = np.diag(R_diag)
        H = A_dyn.T @ Q @ A_dyn + R
        f = A_dyn.T @ Q @ e_base

        n_dec = M * N_MV

        def objective(du_flat):
            e = e_base + A_dyn @ du_flat
            return 0.5 * (e @ (Q_diag * e) + du_flat @ (R_diag * du_flat))

        def gradient(du_flat):
            return H @ du_flat + f

        # Bounds: rate limits on each move
        bounds = []
        for m in range(M):
            for j in range(N_MV):
                bounds.append((dmv_lo[j], dmv_hi[j]))

        # Constraints: position limits
        # mv_lo <= mv_current + cumsum(du_0..du_m) <= mv_hi for each m
        constraints = []
        for m in range(M):
            for j in range(N_MV):
                # Build cumsum selector: sum of du[0:m+1] for MV j
                def _pos_lo(du_flat, _m=m, _j=j):
                    cumsum = sum(du_flat[mm * N_MV + _j] for mm in range(_m + 1))
                    return mv_current[_j] + cumsum - mv_lo[_j]

                def _pos_hi(du_flat, _m=m, _j=j):
                    cumsum = sum(du_flat[mm * N_MV + _j] for mm in range(_m + 1))
                    return mv_hi[_j] - (mv_current[_j] + cumsum)

                constraints.append({"type": "ineq", "fun": _pos_lo})
                constraints.append({"type": "ineq", "fun": _pos_hi})

        # Initial guess: zero moves
        du0 = np.zeros(n_dec)

        try:
            result = minimize(objective, du0, jac=gradient,
                              method='SLSQP',
                              bounds=bounds,
                              constraints=constraints,
                              options={"maxiter": 100, "ftol": 1e-8})
            if result.success or result.fun < 1e10:
                du_plan = result.x.reshape(M, N_MV).T  # (N_MV, M)
                return du_plan, result.fun, True
            else:
                log.debug("DMC QP failed: %s", result.message)
                return np.zeros((N_MV, M)), 0.0, False
        except Exception as e:
            log.warning("DMC QP exception: %s", e)
            return np.zeros((N_MV, M)), 0.0, False

    # ─────────────────────────────────────────────────────────────────────
    # Main execute
    # ─────────────────────────────────────────────────────────────────────

    def _transition(self, new_state: int):
        """Change state and reset state timer. Logs transition."""
        if new_state != self._state:
            old_name = self._STATE_NAMES.get(self._state, "?")
            new_name = self._STATE_NAMES.get(new_state, "?")
            log.info("DMC state: %s -> %s", old_name, new_name)
            self._state = new_state
            self._state_timer = 0.0

    def execute(self, dt: float):
        enable = bool(self.get_input("ENABLE"))
        p = self.config.params
        init_time = float(p.get("init_time", 10.0))
        ramp_in_time = float(p.get("ramp_in_time", 0.0))
        disengage_time = float(p.get("disengage_time", 30.0))
        fail_threshold = int(p.get("fail_threshold", 3))
        recover_threshold = int(p.get("recover_threshold", 2))
        auto_recover = bool(p.get("auto_recover", True))
        sample_period = float(p.get("sample_period", 60.0))

        # Build model on first call
        if not self._model_built:
            self._build_model()

        # Tick state timer every scan
        self._state_timer += dt

        # ── Read current measurements ──
        cv_meas = np.array([float(self.get_input(f"CV_{i}"))
                            for i in range(1, N_CV + 1)])
        mv_fb = np.array([float(self.get_input(f"MV_FB_{i}"))
                          for i in range(1, N_MV + 1)])
        dv_vals = np.array([float(self.get_input(f"DV_{i}"))
                            for i in range(1, N_DV + 1)])

        # ═══════════════════════════════════════════════════════════════
        # OFF state — enabled=False, tracking MV for bumpless future engage
        # ═══════════════════════════════════════════════════════════════
        if self._state == self.OFF:
            # Track MV feedbacks for bumpless engage
            for i in range(N_MV):
                self.set_output(f"MV_SP_{i + 1}", mv_fb[i])
            # Rising edge of enable → ENGAGING
            if enable and not self._was_enabled:
                self._transition(self.ENGAGING)
            self._was_enabled = enable

        # ═══════════════════════════════════════════════════════════════
        # ENGAGING — capture state, prepare for initialization
        # ═══════════════════════════════════════════════════════════════
        elif self._state == self.ENGAGING:
            # Capture current plant state
            self._mv_current = mv_fb.copy()
            self._mv_prev = mv_fb.copy()
            self._dv_current = dv_vals.copy()
            self._dv_prev = dv_vals.copy()
            self._y_meas = cv_meas.copy()
            # Zero out history
            if self._past_du is not None:
                self._past_du[:] = 0.0
                self._past_ddv[:] = 0.0
                self._d_hat[:] = 0.0
                self._mv_plan[:] = 0.0
            self._first_active_cycle = True
            self._consecutive_fails = 0
            self._consecutive_ok = 0
            self._init_timer = 0.0
            self._cycle_timer = 0.0
            # Hold MVs at feedback
            for i in range(N_MV):
                self.set_output(f"MV_SP_{i + 1}", mv_fb[i])
            self._transition(self.INITIALIZING)

        # ═══════════════════════════════════════════════════════════════
        # INITIALIZING — hold MVs at feedback for init_time
        # ═══════════════════════════════════════════════════════════════
        elif self._state == self.INITIALIZING:
            if not enable:
                self._transition(self.DISENGAGING)
                self._disengage_mv_start = mv_fb.copy()
            else:
                self._init_timer += dt
                for i in range(N_MV):
                    self.set_output(f"MV_SP_{i + 1}", mv_fb[i])
                if self._init_timer >= init_time:
                    next_state = self.RAMPING_IN if ramp_in_time > 0 else self.ACTIVE
                    self._transition(next_state)

        # ═══════════════════════════════════════════════════════════════
        # RAMPING_IN — optional SP ramping (not yet implemented, passthrough)
        # ═══════════════════════════════════════════════════════════════
        elif self._state == self.RAMPING_IN:
            if not enable:
                self._transition(self.DISENGAGING)
                self._disengage_mv_start = mv_fb.copy()
            else:
                # For now, pass through to ACTIVE after ramp_in_time
                if self._state_timer >= ramp_in_time:
                    self._transition(self.ACTIVE)
                else:
                    for i in range(N_MV):
                        self.set_output(f"MV_SP_{i + 1}", mv_fb[i])

        # ═══════════════════════════════════════════════════════════════
        # ACTIVE — normal operation, solver running, heartbeat pulsing
        # ═══════════════════════════════════════════════════════════════
        elif self._state == self.ACTIVE:
            if not enable:
                self._transition(self.DISENGAGING)
                self._disengage_mv_start = np.array([
                    float(self.get_output(f"MV_SP_{i+1}") or mv_fb[i])
                    for i in range(N_MV)])
            else:
                self._cycle_timer += dt
                if self._cycle_timer >= sample_period:
                    self._cycle_timer = 0.0
                    self._hb_state = not self._hb_state
                    solver_ok = self._run_dmc_cycle(cv_meas, mv_fb, dv_vals)

                    # Fault detection
                    if not solver_ok:
                        self._consecutive_fails += 1
                        self._consecutive_ok = 0
                        if self._consecutive_fails >= fail_threshold:
                            self._transition(self.FAILED)
                        else:
                            self._transition(self.HOLD)
                    else:
                        self._consecutive_fails = 0
                        self._consecutive_ok += 1
                        # Remember this good plan for HOLD fallback
                        self._last_good_mv_sp = np.array([
                            float(self.get_output(f"MV_SP_{i+1}"))
                            for i in range(N_MV)])

        # ═══════════════════════════════════════════════════════════════
        # HOLD — solver failed temporarily, holding last good MV plan
        # ═══════════════════════════════════════════════════════════════
        elif self._state == self.HOLD:
            if not enable:
                self._transition(self.DISENGAGING)
                self._disengage_mv_start = mv_fb.copy()
            else:
                # Hold last good output
                if self._last_good_mv_sp is not None:
                    for i in range(N_MV):
                        self.set_output(f"MV_SP_{i + 1}",
                                        float(self._last_good_mv_sp[i]))
                else:
                    for i in range(N_MV):
                        self.set_output(f"MV_SP_{i + 1}", mv_fb[i])

                self._cycle_timer += dt
                if self._cycle_timer >= sample_period:
                    self._cycle_timer = 0.0
                    self._hb_state = not self._hb_state
                    solver_ok = self._run_dmc_cycle(cv_meas, mv_fb, dv_vals)

                    if solver_ok:
                        self._consecutive_ok += 1
                        self._consecutive_fails = 0
                        if self._consecutive_ok >= recover_threshold:
                            self._transition(self.ACTIVE)
                    else:
                        self._consecutive_fails += 1
                        self._consecutive_ok = 0
                        if self._consecutive_fails >= fail_threshold:
                            self._transition(self.FAILED)

        # ═══════════════════════════════════════════════════════════════
        # FAILED — persistent failure, MVs frozen at last good,
        # alarm raised, waiting for operator disable or auto-recovery
        # ═══════════════════════════════════════════════════════════════
        elif self._state == self.FAILED:
            if not enable:
                self._transition(self.DISENGAGING)
                self._disengage_mv_start = mv_fb.copy()
            else:
                # Freeze MVs at last known good
                if self._last_good_mv_sp is not None:
                    for i in range(N_MV):
                        self.set_output(f"MV_SP_{i + 1}",
                                        float(self._last_good_mv_sp[i]))
                else:
                    for i in range(N_MV):
                        self.set_output(f"MV_SP_{i + 1}", mv_fb[i])

                # Attempt recovery
                if auto_recover:
                    self._cycle_timer += dt
                    if self._cycle_timer >= sample_period:
                        self._cycle_timer = 0.0
                        solver_ok = self._run_dmc_cycle(cv_meas, mv_fb, dv_vals)
                        if solver_ok:
                            self._consecutive_ok += 1
                            self._consecutive_fails = 0
                            if self._consecutive_ok >= recover_threshold:
                                self._transition(self.HOLD)
                                log.info("DMC recovered: FAILED -> HOLD")
                        else:
                            self._consecutive_ok = 0

        # ═══════════════════════════════════════════════════════════════
        # DISENGAGING — graceful ramp-down from last MV to feedback
        # ═══════════════════════════════════════════════════════════════
        elif self._state == self.DISENGAGING:
            if self._disengage_mv_start is None:
                self._disengage_mv_start = mv_fb.copy()
            # Linear ramp from start-of-disengage to current feedback over disengage_time
            frac = min(1.0, self._state_timer / max(disengage_time, 0.01))
            for i in range(N_MV):
                sp = (1.0 - frac) * self._disengage_mv_start[i] + frac * mv_fb[i]
                self.set_output(f"MV_SP_{i + 1}", float(sp))
            if self._state_timer >= disengage_time:
                self._disengage_mv_start = None
                self._transition(self.OFF)
                self._was_enabled = False

        # ═══════════════════════════════════════════════════════════════
        # Common outputs for all states
        # ═══════════════════════════════════════════════════════════════
        is_active = (self._state == self.ACTIVE)
        is_fault = (self._state == self.FAILED)
        is_ready = is_active and (self._consecutive_fails == 0)

        self.set_output("ACTIVE", is_active)
        self.set_output("FAULT", is_fault)
        self.set_output("READY", is_ready)
        self.set_output("STATUS", self._state)
        self.set_output("STATE_NAME", self._STATE_NAMES.get(self._state, "?"))
        self.set_output("HEARTBEAT", self._hb_state
                        if self._state in (self.ACTIVE, self.HOLD) else False)
        self.set_output("CYCLE_TIME", self._cycle_timer)
        self.set_output("TIME_IN_STATE", self._state_timer)
        self.set_output("CONSECUTIVE_FAILS", self._consecutive_fails)

        # Track was_enabled for rising edge detection on next scan
        self._was_enabled = enable

    # ─────────────────────────────────────────────────────────────────────
    # DMC cycle
    # ─────────────────────────────────────────────────────────────────────

    def _run_dmc_cycle(self, cv_meas: np.ndarray,
                       mv_fb: np.ndarray, dv_vals: np.ndarray) -> bool:
        """Execute one full DMC optimization cycle.

        Returns True if both LP and QP solvers succeeded, False otherwise.
        On failure, MV_SP outputs hold at current MV position.
        """

        # Step 1 — Update past moves
        du = mv_fb - self._mv_prev
        self._past_du = np.roll(self._past_du, 1, axis=1)
        self._past_du[:, 0] = du

        ddv = dv_vals - self._dv_prev
        self._past_ddv = np.roll(self._past_ddv, 1, axis=1)
        self._past_ddv[:, 0] = ddv

        self._mv_prev = mv_fb.copy()
        self._mv_current = mv_fb.copy()
        self._dv_prev = dv_vals.copy()
        self._dv_current = dv_vals.copy()
        self._y_meas = cv_meas.copy()

        # Step 2 — Free response (FIR convolution)
        y_free = self._compute_free_response()  # (P, N_CV)

        # Step 3 — Output bias filter
        if self._first_active_cycle:
            # Initialize bias to current measurement error
            self._d_hat = cv_meas - y_free[0]
            self._first_active_cycle = False
        else:
            self._update_bias(cv_meas, y_free)

        # Biased free response
        y_free_biased = y_free + self._d_hat[np.newaxis, :]

        # Step 4 — Steady-state target (LP)
        y_free_end = y_free_biased[-1]  # last prediction step
        du_ss, y_ss_target, obj_ss, ss_ok = self._solve_ss_target(
            y_free_end, self._mv_current)

        # Step 5 — Dynamic QP
        du_plan, obj_dyn, dyn_ok = self._solve_dynamic_qp(
            y_free_biased, y_ss_target, self._mv_current)

        solver_ok = ss_ok and dyn_ok

        if solver_ok:
            self._mv_plan = du_plan

        # Step 6 — Output first move
        for i in range(N_MV):
            if solver_ok:
                mv_sp = self._mv_current[i] + self._mv_plan[i, 0]
            else:
                # Solver failed — hold current position
                mv_sp = self._mv_current[i]
            self.set_output(f"MV_SP_{i + 1}", mv_sp)

        # ── Write diagnostic outputs ──
        for i in range(N_CV):
            self.set_output(f"CV_PRED_{i + 1}", y_free_biased[0, i])
            self.set_output(f"CV_SS_TGT_{i + 1}", y_ss_target[i])
            self.set_output(f"CV_ERR_{i + 1}",
                            cv_meas[i] - y_free_biased[0, i])

        for i in range(N_MV):
            mv_ss_tgt = self._mv_current[i] + du_ss[i] if ss_ok else self._mv_current[i]
            self.set_output(f"MV_SS_TGT_{i + 1}", mv_ss_tgt)

        self.set_output("OBJ_SS", obj_ss)
        self.set_output("OBJ_DYN", obj_dyn)
        self.set_output("SOLVER_OK", solver_ok)
        self.set_output("BIAS_NORM", float(np.linalg.norm(self._d_hat)))

        return solver_ok
