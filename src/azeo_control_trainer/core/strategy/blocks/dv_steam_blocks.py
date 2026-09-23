"""Azeo Energy Metering blocks — steam / water property calculations.

Seven blocks from the Azeo "Energy Metering Blocks" palette, all of
which evaluate water/steam thermodynamic properties from the IAPWS
*Revised Release on the IAPWS Industrial Formulation 1997 for the
Thermodynamic Properties of Water and Steam* (IF97):

    STM — Steam Properties (h, s, v from gauge pressure + temperature)
    SST — Saturated Steam Properties, given Temperature (h, s, v, p)
    TSS — Saturated Temperature (Tsat from gauge pressure)
    WTH — Water Enthalpy (saturated-liquid h from temperature)
    WTS — Water Entropy (saturated-liquid s from temperature)
    SDR — Steam Density Ratio (meter density-correction factor)
    ISE — Isentropic Expansion (h, T, quality from pressure + entropy)

The property core below is a real IF97 implementation — Region 1
(compressed/saturated liquid), Region 2 (vapour/superheated steam),
Region 3 (near-critical, Helmholtz form with an iterative density
solve), Region 4 (saturation line, both the ps(T) and the Ts(p)
backward equation) and Region 5 (800–2000 degC) — not a correlation
fit. It reproduces every value in the IF97 verification tables to the
released tolerance (see ``tests/_smoke_dv_steam.py``).

Deviations from Azeo, all forced by this repo's framework rather than
by the spec:

* Azeo parameters carry a status *and* a limit substatus. Terminals
  here carry only a value, so per-block range clamping is reported via
  ``self.status`` (BlockStatus.BAD / UNCERTAIN per the block's documented
  status handling) plus discrete ``HI_LIMITED`` / ``LO_LIMITED`` outputs.
  "Output status = worst status of the inputs" cannot be honoured — the
  wire protocol has no status to propagate.
* These blocks have no Azeo mode attribute, so no MODE parameter is
  exposed.

Units: every block except SDR carries the ``UNIT_SET`` named set
("SI" or "US"). SI is degC / MPa gauge / kJ/kg / kJ/(kg K) / m3/kg;
US is degF / psig / Btu/lb / Btu/(lb degR) / cu ft/lb. Note that the
spec's parameter tables state *gauge* pressure for both unit sets even
though its SI range figures are quoted as absolute MPa; the parameter
table wins here, so SI PRES is gauge (absolute = PRES + 0.101325).
"""
from __future__ import annotations

import math

import numpy as np
from scipy.optimize import brentq

from ..model.block_base import (
    FunctionBlock, BlockCategory, BlockStatus, DataType,
)
from ..model.block_registry import register_block


# ═══════════════════════════════════════════════════════════════════════
#  IAPWS-IF97 property core
# ═══════════════════════════════════════════════════════════════════════

R_W = 0.461526          # specific gas constant, kJ/(kg K)
T_CRIT = 647.096        # K
P_CRIT = 22.064         # MPa
RHO_CRIT = 322.0        # kg/m3
P_TRIPLE = 611.213e-6   # MPa — IF97 ps(273.15 K)
T_TRIPLE = 273.15       # K
T_13 = 623.15           # K — region 1/3 boundary
T_25 = 1073.15          # K — 800 degC, region 2/5 boundary
T_5MAX = 2273.15        # K — 2000 degC, upper IF97 limit
P_5MAX = 50.0           # MPa — region 5 pressure limit
P_MAX = 100.0           # MPa — regions 1/2/3 pressure limit

ATM_MPA = 0.101325                  # standard atmosphere, MPa
PSI_MPA = 0.00689475729316836       # MPa per psi
BTU_LB = 2.326                      # kJ/kg per Btu/lb (IT, exact)
BTU_LB_R = 4.1868                   # kJ/(kg K) per Btu/(lb degR) (exact)
FT3_LB = 0.0624279605761446         # m3/kg per cu ft/lb

# ── Region 1: dimensionless Gibbs energy, pi = p/16.53 MPa, tau = 1386/T
_R1_I = np.array([0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 2, 2, 2, 2,
                  2, 3, 3, 3, 4, 4, 4, 5, 8, 8, 21, 23, 29, 30, 31, 32],
                 dtype=float)
_R1_J = np.array([-2, -1, 0, 1, 2, 3, 4, 5, -9, -7, -1, 0, 1, 3, -3, 0,
                  1, 3, 17, -4, 0, 6, -5, -2, 10, -8, -11, -6, -29, -31,
                  -38, -39, -40, -41], dtype=float)
_R1_N = np.array([
    0.14632971213167, -0.84548187169114, -0.37563603672040e1,
    0.33855169168385e1, -0.95791963387872, 0.15772038513228,
    -0.16616417199501e-1, 0.81214629983568e-3, 0.28319080123804e-3,
    -0.60706301565874e-3, -0.18990068218419e-1, -0.32529748770505e-1,
    -0.21841717175414e-1, -0.52838357969930e-4, -0.47184321073267e-3,
    -0.30001780793026e-3, 0.47661393906987e-4, -0.44141845330846e-5,
    -0.72694996297594e-15, -0.31679644845054e-4, -0.28270797985312e-5,
    -0.85205128120103e-9, -0.22425281908000e-5, -0.65171222895601e-6,
    -0.14341729937924e-12, -0.40516996860117e-6, -0.12734301741641e-8,
    -0.17424871230634e-9, -0.68762131295531e-18, 0.14478307828521e-19,
    0.26335781662795e-22, -0.11947622640071e-22, 0.18228094581404e-23,
    -0.93537087292458e-25])

# ── Region 2 ideal-gas part, tau = 540/T
_R2O_J = np.array([0, 1, -5, -4, -3, -2, -1, 2, 3], dtype=float)
_R2O_N = np.array([
    -0.96927686500217e1, 0.10086655968018e2, -0.56087911283020e-2,
    0.71452738081455e-1, -0.40710498223928, 0.14240819171444e1,
    -0.43839511319450e1, -0.28408632460772, 0.21268463753307e-1])

# ── Region 2 residual part, pi = p/1 MPa
_R2R_I = np.array([1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 4, 4, 4,
                   5, 6, 6, 6, 7, 7, 7, 8, 8, 9, 10, 10, 10, 16, 16, 18,
                   20, 20, 20, 21, 22, 23, 24, 24, 24], dtype=float)
_R2R_J = np.array([0, 1, 2, 3, 6, 1, 2, 4, 7, 36, 0, 1, 3, 6, 35, 1, 2,
                   3, 7, 3, 16, 35, 0, 11, 25, 8, 36, 13, 4, 10, 14, 29,
                   50, 57, 20, 35, 48, 21, 53, 39, 26, 40, 58], dtype=float)
_R2R_N = np.array([
    -0.17731742473213e-2, -0.17834862292358e-1, -0.45996013696365e-1,
    -0.57581259083432e-1, -0.50325278727930e-1, -0.33032641670203e-4,
    -0.18948987516315e-3, -0.39392777243355e-2, -0.43797295650573e-1,
    -0.26674547914087e-4, 0.20481737692309e-7, 0.43870667284435e-6,
    -0.32277677238570e-4, -0.15033924542148e-2, -0.40668253562649e-1,
    -0.78847309559367e-9, 0.12790717852285e-7, 0.48225372718507e-6,
    0.22922076337661e-5, -0.16714766451061e-10, -0.21171472321355e-2,
    -0.23895741934104e2, -0.59059564324270e-17, -0.12621808899101e-5,
    -0.38946842435739e-1, 0.11256211360459e-10, -0.82311340897998e1,
    0.19809712802088e-7, 0.10406965210174e-18, -0.10234747095929e-12,
    -0.10018179379511e-8, -0.80882908646985e-10, 0.10693031879409,
    -0.33662250574171, 0.89185845355421e-24, 0.30629316876232e-12,
    -0.42002467698208e-5, -0.59056029685639e-25, 0.37826947613457e-5,
    -0.12768608934681e-14, 0.73087610595061e-28, 0.55414715350778e-16,
    -0.94369707241210e-6])

# ── Region 3: dimensionless Helmholtz energy, delta = rho/322, tau = Tc/T
#    Term 1 (n = 1.0658...) is the  n*ln(delta)  term and is held apart.
_R3_N1 = 0.10658070028513e1
_R3_N = np.array([
    -0.15732845290239e2, 0.20944396974307e2, -0.76867707878716e1,
    0.26185947787954e1, -0.28080781148620e1, 0.12053369696517e1,
    -0.84566812812502e-2, -0.12654315477714e1, -0.11524407806681e1,
    0.88521043984318, -0.64207765181607, 0.38493460186671,
    -0.85214708824206, 0.48972281541877e1, -0.30502617256965e1,
    0.39420536879154e-1, 0.12558408424308, -0.27999329698710,
    0.13899799569460e1, -0.20189915023570e1, -0.82147637173963e-2,
    -0.47596035734923, 0.43984074473500e-1, -0.44476435428739,
    0.90572070719733, 0.70522450087967, 0.10770512626332,
    -0.32913623258954, -0.50871062041158, -0.22175400873096e-1,
    0.94260751665092e-1, 0.16436278447961, -0.13503372241348e-1,
    -0.14834345352472e-1, 0.57922953628084e-3, 0.32308904703711e-2,
    0.80964802996215e-4, -0.16557679795037e-3, -0.44923899061815e-4])
# Exponent pairs in release order, one per _R3_N entry (39 residual terms
# following the leading n1*ln(delta) term).
_R3_IJ = [
    (0, 0), (0, 1), (0, 2), (0, 7), (0, 10), (0, 12), (0, 23),
    (1, 2), (1, 6), (1, 15), (1, 17),
    (2, 0), (2, 2), (2, 6), (2, 7), (2, 22), (2, 26),
    (3, 0), (3, 2), (3, 4), (3, 16), (3, 26),
    (4, 0), (4, 2), (4, 4), (4, 26),
    (5, 1), (5, 3), (5, 26),
    (6, 0), (6, 2), (6, 26),
    (7, 2), (8, 26), (9, 2), (9, 26),
    (10, 0), (10, 1), (11, 26),
]
_R3_I = np.array([ij[0] for ij in _R3_IJ], dtype=float)
_R3_J = np.array([ij[1] for ij in _R3_IJ], dtype=float)

# ── Region 5 ideal-gas part, tau = 1000/T
_R5O_J = np.array([0, 1, -3, -2, -1, 2], dtype=float)
_R5O_N = np.array([
    -0.13179983674201e2, 0.68540841634434e1, -0.24805148933466e-1,
    0.36901534980333, -0.31161318213925e1, -0.32961626538917])

# ── Region 5 residual part
_R5R_I = np.array([1, 1, 1, 2, 2, 3], dtype=float)
_R5R_J = np.array([1, 2, 3, 3, 9, 7], dtype=float)
_R5R_N = np.array([
    0.15736404855259e-2, 0.90153761673944e-3, -0.50270077677648e-2,
    0.22440037409485e-5, -0.41163275453471e-5, 0.37919454822955e-7])

# ── Region 4 saturation-line coefficients (used both ways)
_R4_N = np.array([
    0.11670521452767e4, -0.72421316703206e6, -0.17073846940092e2,
    0.12020824702470e5, -0.32325550322333e7, 0.14915108613530e2,
    -0.48232657361591e4, 0.40511340542057e6, -0.23855557567849,
    0.65017534844798e3])

# ── Region 2/3 boundary (B23)
_B23_N = np.array([
    0.34805185628969e3, -0.11671859879975e1, 0.10192970039326e-2,
    0.57254459862746e3, 0.13918839778870e2])

# Wagner & Pruss (IAPWS-95 supplementary release) saturated-density
# correlations — used only as Newton/bracket seeds for the region-3
# saturated-density solve, never as the reported property.
_WP_RHOL = ((1.99274064, 1.0 / 3), (1.09965342, 2.0 / 3),
            (-0.510839303, 5.0 / 3), (-1.75493479, 16.0 / 3),
            (-45.5170352, 43.0 / 3), (-6.74694450e5, 110.0 / 3))
_WP_RHOV = ((-2.03150240, 2.0 / 6), (-2.68302940, 4.0 / 6),
            (-5.38626492, 8.0 / 6), (-17.2991605, 18.0 / 6),
            (-44.7586581, 37.0 / 6), (-63.9201063, 71.0 / 6))


# ── Region 4 — saturation line ──────────────────────────────────────

def p_sat(T: float) -> float:
    """Saturation pressure [MPa abs] at temperature ``T`` [K] (IF97 eq. 30)."""
    n = _R4_N
    th = T + n[8] / (T - n[9])
    a = th * th + n[0] * th + n[1]
    b = n[2] * th * th + n[3] * th + n[4]
    c = n[5] * th * th + n[6] * th + n[7]
    return (2.0 * c / (-b + math.sqrt(b * b - 4.0 * a * c))) ** 4


def T_sat(p: float) -> float:
    """Saturation temperature [K] at pressure ``p`` [MPa abs] (IF97 eq. 31)."""
    n = _R4_N
    beta = p ** 0.25
    e = beta * beta + n[2] * beta + n[5]
    f = n[0] * beta * beta + n[3] * beta + n[6]
    g = n[1] * beta * beta + n[4] * beta + n[7]
    d = 2.0 * g / (-f - math.sqrt(f * f - 4.0 * e * g))
    return (n[9] + d - math.sqrt((n[9] + d) ** 2 - 4.0 * (n[8] + n[9] * d))) / 2.0


def _b23_p(T: float) -> float:
    """Region 2/3 boundary pressure [MPa] at ``T`` [K] (IF97 eq. 5)."""
    return _B23_N[0] + _B23_N[1] * T + _B23_N[2] * T * T


# ── Region 1 ────────────────────────────────────────────────────────

def _region1(p: float, T: float) -> tuple[float, float, float]:
    """(v [m3/kg], h [kJ/kg], s [kJ/kg K]) from p [MPa abs], T [K]."""
    pi = p / 16.53
    tau = 1386.0 / T
    a = 7.1 - pi
    b = tau - 1.222
    ai = a ** _R1_I
    bj = b ** _R1_J
    g = float(np.sum(_R1_N * ai * bj))
    g_pi = float(np.sum(-_R1_N * _R1_I * a ** (_R1_I - 1.0) * bj))
    g_tau = float(np.sum(_R1_N * ai * _R1_J * b ** (_R1_J - 1.0)))
    v = R_W * T / p * pi * g_pi / 1000.0   # kJ/(kg K)*K/MPa -> m3/kg
    h = R_W * T * tau * g_tau
    s = R_W * (tau * g_tau - g)
    return v, h, s


# ── Region 2 ────────────────────────────────────────────────────────

def _region2(p: float, T: float) -> tuple[float, float, float]:
    """(v, h, s) for vapour/superheated steam from p [MPa abs], T [K]."""
    pi = p
    tau = 540.0 / T
    tj = tau ** _R2O_J
    g0 = math.log(pi) + float(np.sum(_R2O_N * tj))
    g0_pi = 1.0 / pi
    g0_tau = float(np.sum(_R2O_N * _R2O_J * tau ** (_R2O_J - 1.0)))

    b = tau - 0.5
    pii = pi ** _R2R_I
    bj = b ** _R2R_J
    gr = float(np.sum(_R2R_N * pii * bj))
    gr_pi = float(np.sum(_R2R_N * _R2R_I * pi ** (_R2R_I - 1.0) * bj))
    gr_tau = float(np.sum(_R2R_N * pii * _R2R_J * b ** (_R2R_J - 1.0)))

    v = R_W * T / p * pi * (g0_pi + gr_pi) / 1000.0
    h = R_W * T * tau * (g0_tau + gr_tau)
    s = R_W * (tau * (g0_tau + gr_tau) - (g0 + gr))
    return v, h, s


# ── Region 3 ────────────────────────────────────────────────────────

def _region3_from_rho(rho: float, T: float) -> tuple[float, float, float, float]:
    """(p [MPa], v, h, s) from density ``rho`` [kg/m3] and ``T`` [K]."""
    delta = rho / RHO_CRIT
    tau = T_CRIT / T
    di = delta ** _R3_I
    tj = tau ** _R3_J
    f = _R3_N1 * math.log(delta) + float(np.sum(_R3_N * di * tj))
    f_d = _R3_N1 / delta + float(
        np.sum(_R3_N * _R3_I * delta ** (_R3_I - 1.0) * tj))
    f_t = float(np.sum(_R3_N * di * _R3_J * tau ** (_R3_J - 1.0)))
    p = rho * R_W * T * delta * f_d / 1000.0    # kPa -> MPa
    h = R_W * T * (tau * f_t + delta * f_d)
    s = R_W * (tau * f_t - f)
    return p, 1.0 / rho, h, s


def _region3_dpdrho(rho: float, T: float) -> float:
    """dp/drho [MPa kg^-1 m3] for the region-3 Newton solve."""
    delta = rho / RHO_CRIT
    tau = T_CRIT / T
    tj = tau ** _R3_J
    f_d = _R3_N1 / delta + float(
        np.sum(_R3_N * _R3_I * delta ** (_R3_I - 1.0) * tj))
    f_dd = -_R3_N1 / (delta * delta) + float(
        np.sum(_R3_N * _R3_I * (_R3_I - 1.0) * delta ** (_R3_I - 2.0) * tj))
    return R_W * T * (2.0 * delta * f_d + delta * delta * f_dd) / 1000.0


def _wp_rho_l(T: float) -> float:
    th = max(1e-12, 1.0 - T / T_CRIT)
    return RHO_CRIT * (1.0 + sum(b * th ** e for b, e in _WP_RHOL))


def _wp_rho_v(T: float) -> float:
    th = max(1e-12, 1.0 - T / T_CRIT)
    return RHO_CRIT * math.exp(sum(c * th ** e for c, e in _WP_RHOV))


def _rho_sat3(T: float, liquid: bool) -> float:
    """Saturated liquid/vapour density [kg/m3] at ``T`` in region 3.

    Newton on p3(rho, T) = ps(T), seeded from the Wagner & Pruss
    correlation. Newton (not a bracketed solve) because the seed is
    already within ~0.1 % and stays on the correct side of the two-phase
    loop, where dp/drho > 0 and convergence is quadratic.
    """
    if T >= T_CRIT - 1e-9:
        return RHO_CRIT
    ps = p_sat(T)
    rho = _wp_rho_l(T) if liquid else _wp_rho_v(T)
    for _ in range(80):
        dpdr = _region3_dpdrho(rho, T)
        if dpdr <= 0.0:
            # Strayed inside the loop — step back toward the branch.
            rho *= 1.01 if liquid else 0.99
            continue
        step = (_region3_from_rho(rho, T)[0] - ps) / dpdr
        step = max(-0.2 * rho, min(0.2 * rho, step))
        rho -= step
        if abs(step) < 1e-12 * rho:
            break
    return rho


def _region3_rho(p: float, T: float, liquid: bool) -> float:
    """Solve region-3 density at (p, T) on the requested branch.

    Along an isotherm below Tc, p(rho) is monotonic only on each side of
    the two-phase loop, so the branch is selected first and the bracket
    is anchored on that branch's saturated density.
    """
    f = lambda r: _region3_from_rho(r, T)[0] - p   # noqa: E731

    if T < T_CRIT and not liquid:
        hi = _rho_sat3(T, False)
        if f(hi) <= 0.0:
            return hi      # p at/above ps: clamp to the saturated vapour
        return brentq(f, 1e-3, hi, xtol=1e-12, rtol=1e-15, maxiter=200)

    if T >= T_CRIT:
        lo = 1e-3
    else:
        lo = _rho_sat3(T, True)
        if f(lo) >= 0.0:
            return lo      # p at/below ps: clamp to the saturated liquid

    # Walk the upper bound up the *rising* part of the isotherm only. The
    # region-3 equation is fitted to ~100 MPa and turns over (dp/drho < 0,
    # eventually negative p) if extrapolated much past its density range,
    # so a fixed upper bracket is not safe.
    hi = lo
    for _ in range(300):
        nxt = hi * 1.05 + 1e-3
        if nxt > 1200.0 or _region3_dpdrho(nxt, T) <= 0.0:
            break
        hi = nxt
        if f(hi) > 0.0:
            break
    if f(hi) <= 0.0:
        return hi          # p beyond the equation's supported density range
    return brentq(f, lo, hi, xtol=1e-12, rtol=1e-15, maxiter=200)


def _region3(p: float, T: float) -> tuple[float, float, float]:
    liquid = T >= T_CRIT or p > p_sat(T)
    rho = _region3_rho(p, T, liquid)
    _, v, h, s = _region3_from_rho(rho, T)
    return v, h, s


# ── Region 5 ────────────────────────────────────────────────────────

def _region5(p: float, T: float) -> tuple[float, float, float]:
    pi = p
    tau = 1000.0 / T
    g0 = math.log(pi) + float(np.sum(_R5O_N * tau ** _R5O_J))
    g0_pi = 1.0 / pi
    g0_tau = float(np.sum(_R5O_N * _R5O_J * tau ** (_R5O_J - 1.0)))
    pii = pi ** _R5R_I
    tj = tau ** _R5R_J
    gr = float(np.sum(_R5R_N * pii * tj))
    gr_pi = float(np.sum(_R5R_N * _R5R_I * pi ** (_R5R_I - 1.0) * tj))
    gr_tau = float(np.sum(_R5R_N * pii * _R5R_J * tau ** (_R5R_J - 1.0)))
    v = R_W * T / p * pi * (g0_pi + gr_pi) / 1000.0
    h = R_W * T * tau * (g0_tau + gr_tau)
    s = R_W * (tau * (g0_tau + gr_tau) - (g0 + gr))
    return v, h, s


# ── Region dispatch ─────────────────────────────────────────────────

def if97_region(p: float, T: float) -> int:
    """IF97 region number for p [MPa abs], T [K]."""
    if T > T_25:
        return 5
    if T > T_13:
        return 3 if p > _b23_p(T) else 2
    return 1 if p > p_sat(T) else 2


def props(p: float, T: float) -> tuple[float, float, float]:
    """(v [m3/kg], h [kJ/kg], s [kJ/kg K]) at p [MPa abs], T [K]."""
    reg = if97_region(p, T)
    if reg == 1:
        return _region1(p, T)
    if reg == 2:
        return _region2(p, T)
    if reg == 3:
        return _region3(p, T)
    return _region5(p, T)


def sat_liquid(T: float) -> tuple[float, float, float]:
    """Saturated-liquid (v, h, s) at ``T`` [K]."""
    if T <= T_13:
        return _region1(p_sat(T), T)
    _, v, h, s = _region3_from_rho(_rho_sat3(T, liquid=True), T)
    return v, h, s


def sat_vapour(T: float) -> tuple[float, float, float]:
    """Saturated-vapour (v, h, s) at ``T`` [K]."""
    if T <= T_13:
        return _region2(p_sat(T), T)
    _, v, h, s = _region3_from_rho(_rho_sat3(T, liquid=False), T)
    return v, h, s


def T_from_ps(p: float, s: float, t_lo: float, t_hi: float) -> float:
    """Invert s(p, T) for T on a single-phase branch.

    IF97's backward T(p,s) equations are themselves approximations meant
    to seed an iteration; solving the forward equation directly is both
    simpler and exact to the forward release, so that is what is done.
    """
    f = lambda t: props(p, t)[2] - s          # noqa: E731
    lo, hi = t_lo, t_hi
    if f(lo) > 0.0:
        return lo
    if f(hi) < 0.0:
        return hi
    return brentq(f, lo, hi, xtol=1e-9, rtol=1e-14, maxiter=200)


# ═══════════════════════════════════════════════════════════════════════
#  Unit-set conversion helpers  (UNIT_SET = "SI" | "US")
# ═══════════════════════════════════════════════════════════════════════

def _is_us(params: dict) -> bool:
    return str(params.get("UNIT_SET", "SI")).strip().upper() == "US"


def _temp_to_K(t: float, us: bool) -> float:
    return ((t - 32.0) / 1.8 if us else t) + 273.15


def _K_to_temp(tk: float, us: bool) -> float:
    c = tk - 273.15
    return c * 1.8 + 32.0 if us else c


def _pres_to_MPa_abs(p: float, us: bool) -> float:
    return (p * PSI_MPA if us else p) + ATM_MPA


def _MPa_abs_to_pres(pa: float, us: bool) -> float:
    g = pa - ATM_MPA
    return g / PSI_MPA if us else g


def _h_out(h: float, us: bool) -> float:
    return h / BTU_LB if us else h


def _s_in(s: float, us: bool) -> float:
    return s * BTU_LB_R if us else s


def _s_out(s: float, us: bool) -> float:
    return s / BTU_LB_R if us else s


def _v_out(v: float, us: bool) -> float:
    return v / FT3_LB if us else v


def _clamp(value: float, lo: float, hi: float) -> tuple[float, int]:
    """Clamp to [lo, hi]; second element is -1 low / 0 none / +1 high."""
    if value < lo:
        return lo, -1
    if value > hi:
        return hi, 1
    return value, 0


#: UNIT_SET named set — the doc's "United States Engineering Units (US)"
#: / "System International Engineering Units (SI)". Declared per block
#: rather than on the shared base because SDR has no UNIT_SET parameter
#: (both of its inputs simply share the caller's units).
_UNIT_SET_CHOICES = {"UNIT_SET": ("SI", "US")}


class _SteamBlockBase(FunctionBlock):
    """Shared plumbing for the energy-metering property blocks."""

    category = BlockCategory.MATH

    def _publish_limits(self, flag: int):
        self.set_output("LO_LIMITED", flag < 0)
        self.set_output("HI_LIMITED", flag > 0)

    def _add_limit_outputs(self):
        self.add_output("HI_LIMITED", DataType.BOOL, False,
                        "Input clamped at the high IF97/range boundary")
        self.add_output("LO_LIMITED", DataType.BOOL, False,
                        "Input clamped at the low IF97/range boundary")

    @staticmethod
    def _unit_set_schema() -> dict:
        return {"UNIT_SET": (str, "SI",
                             "Engineering unit set: SI (degC / MPa gauge / "
                             "kJ/kg / kJ/kg-K / m3/kg) or US (degF / psig / "
                             "Btu/lb / Btu/lb-degR / cu ft/lb)")}


# ═══════════════════════════════════════════════════════════════════════
#  STM — Steam Properties
# ═══════════════════════════════════════════════════════════════════════

@register_block
class SteamPropertiesBlock(_SteamBlockBase):
    """Steam Properties (STM) — h, s and v from gauge pressure + temperature.

    Applies across the IF97 water/steam surface: superheated and
    saturated steam, and compressed water below the saturation line.
    Valid ranges per the Azeo spec: temperature 32 to 3632 degF
    (0 to 2000 degC); pressure above -14.696 psig (0 MPa absolute) and
    at most 14489 psig (100 MPa absolute) for T < 1472 degF (800 degC),
    or 7237.1 psig (50 MPa absolute) for T >= 800 degC — which is
    exactly the IF97 region 1/2/3 and region 5 envelope.

    The spec also notes that the minimum meaningful temperature for
    *saturated steam* is the greater of 383 degF (195 degC) and Tsat at
    the input pressure. That is application guidance about which side of
    the saturation line you are on, not a computational limit, so it is
    not enforced: below it the block simply returns compressed-water
    (region 1) properties, which is the physically correct answer.

    Out-of-range inputs are clamped to the boundary for the calculation
    and the block status goes BAD, per the spec's status handling.
    """
    block_type = "STM"
    display_name = "Steam Properties (STM)"
    description = "IF97 steam enthalpy / entropy / specific volume from P and T"
    config_choices = _UNIT_SET_CHOICES

    def _define_terminals(self):
        self.add_input("PRES", description="Steam gauge pressure (psig | MPa g)")
        self.add_input("TEMP", description="Steam temperature (degF | degC)")
        self.add_output("ENTHALPY", description="Steam enthalpy (Btu/lb | kJ/kg)")
        self.add_output("ENTROPY",
                        description="Steam entropy (Btu/lb-degR | kJ/kg-K)")
        self.add_output("SPEC_VOL",
                        description="Steam specific volume (cu ft/lb | m3/kg)")
        self._add_limit_outputs()

    def get_config_schema(self):
        return self._unit_set_schema()

    def execute(self, dt: float):
        us = _is_us(self.config.params)
        tk = _temp_to_K(float(self.get_input("TEMP")), us)
        pa = _pres_to_MPa_abs(float(self.get_input("PRES")), us)

        tk, t_flag = _clamp(tk, T_TRIPLE, T_5MAX)
        # IF97 caps pressure at 50 MPa once above 800 degC (region 5).
        p_hi = P_5MAX if tk > T_25 else P_MAX
        pa, p_flag = _clamp(pa, P_TRIPLE, p_hi)

        flag = t_flag or p_flag
        v, h, s = props(pa, tk)

        self.set_output("ENTHALPY", _h_out(h, us))
        self.set_output("ENTROPY", _s_out(s, us))
        self.set_output("SPEC_VOL", _v_out(v, us))
        self._publish_limits(flag)
        self.status = BlockStatus.BAD if flag else BlockStatus.GOOD


# ═══════════════════════════════════════════════════════════════════════
#  SST — Saturated Steam Properties, given Temperature
# ═══════════════════════════════════════════════════════════════════════

@register_block
class SaturatedSteamBlock(_SteamBlockBase):
    """Saturated Steam Properties given Temperature (SST).

    From a saturation temperature, returns saturated-*vapour* enthalpy,
    entropy and specific volume plus the saturation pressure. Valid
    range 32 to 705.103 degF (0 to 373.946 degC, i.e. up to the critical
    point). Above 350 degC the saturated vapour lies in IF97 region 3,
    so the density is solved on the vapour branch of the region-3
    Helmholtz equation at ps(T).

    Out-of-range inputs are boundary-clamped with BAD block status.
    """
    block_type = "SST"
    display_name = "Sat. Steam Properties (SST)"
    description = "IF97 saturated-steam h / s / v and pressure from temperature"
    config_choices = _UNIT_SET_CHOICES

    def _define_terminals(self):
        self.add_input("TEMP",
                       description="Saturation temperature (degF | degC)")
        self.add_output("ENTHALPY",
                        description="Saturated-steam enthalpy (Btu/lb | kJ/kg)")
        self.add_output("ENTROPY",
                        description="Saturated-steam entropy (Btu/lb-degR | kJ/kg-K)")
        self.add_output("SPEC_VOL",
                        description="Saturated-steam specific volume (cu ft/lb | m3/kg)")
        self.add_output("PRES",
                        description="Saturation pressure (psig | MPa gauge)")
        self._add_limit_outputs()

    def get_config_schema(self):
        return self._unit_set_schema()

    def execute(self, dt: float):
        us = _is_us(self.config.params)
        tk = _temp_to_K(float(self.get_input("TEMP")), us)
        tk, flag = _clamp(tk, T_TRIPLE, T_CRIT)

        v, h, s = sat_vapour(tk)
        self.set_output("ENTHALPY", _h_out(h, us))
        self.set_output("ENTROPY", _s_out(s, us))
        self.set_output("SPEC_VOL", _v_out(v, us))
        self.set_output("PRES", _MPa_abs_to_pres(p_sat(tk), us))
        self._publish_limits(flag)
        self.status = BlockStatus.BAD if flag else BlockStatus.GOOD


# ═══════════════════════════════════════════════════════════════════════
#  TSS — Saturated Temperature
# ═══════════════════════════════════════════════════════════════════════

@register_block
class SaturatedTemperatureBlock(_SteamBlockBase):
    """Saturated Temperature (TSS) — Tsat from steam gauge pressure.

    Valid pressure range -14.607 to 3185.4 psig (611.213 Pa to
    22.064 MPa absolute — triple point to critical point). Unlike
    ISE/SST/STM, the spec sets status **Uncertain** (not Bad) when the
    input is out of range, with the output limit substatus set
    LowLimited or HighLimited; ``LO_LIMITED`` / ``HI_LIMITED`` carry that
    here. The spec also notes input *limit* status is not propagated —
    moot in this framework, which has no per-terminal status.
    """
    block_type = "TSS"
    display_name = "Saturated Temperature (TSS)"
    description = "IF97 saturation temperature from steam gauge pressure"
    config_choices = _UNIT_SET_CHOICES

    def _define_terminals(self):
        self.add_input("PRES",
                       description="Steam gauge pressure (psig | MPa gauge)")
        self.add_output("TEMP",
                        description="Saturation temperature (degF | degC)")
        self._add_limit_outputs()

    def get_config_schema(self):
        return self._unit_set_schema()

    def execute(self, dt: float):
        us = _is_us(self.config.params)
        pa = _pres_to_MPa_abs(float(self.get_input("PRES")), us)
        pa, flag = _clamp(pa, P_TRIPLE, P_CRIT)

        self.set_output("TEMP", _K_to_temp(T_sat(pa), us))
        self._publish_limits(flag)
        self.status = BlockStatus.UNCERTAIN if flag else BlockStatus.GOOD


# ═══════════════════════════════════════════════════════════════════════
#  WTH — Water Enthalpy
# ═══════════════════════════════════════════════════════════════════════

@register_block
class WaterEnthalpyBlock(_SteamBlockBase):
    """Water Enthalpy (WTH) — saturated-liquid enthalpy at a temperature.

    Valid range 32 to 704 degF (0 to 373.3 degC). Out-of-guideline
    inputs are clamped and status set UNCERTAIN (per the spec, WTH/WTS
    use Uncertain rather than Bad). Typical use: the feedwater or
    condensate enthalpy term of a steam energy balance.
    """
    block_type = "WTH"
    display_name = "Water Enthalpy (WTH)"
    description = "IF97 saturated-water enthalpy from temperature"
    config_choices = _UNIT_SET_CHOICES

    _T_MIN = T_TRIPLE
    _T_MAX = 373.3 + 273.15

    def _define_terminals(self):
        self.add_input("TEMP", description="Water temperature (degF | degC)")
        self.add_output("ENTHALPY",
                        description="Water enthalpy (Btu/lb | kJ/kg)")
        self._add_limit_outputs()

    def get_config_schema(self):
        return self._unit_set_schema()

    def execute(self, dt: float):
        us = _is_us(self.config.params)
        tk = _temp_to_K(float(self.get_input("TEMP")), us)
        tk, flag = _clamp(tk, self._T_MIN, self._T_MAX)

        _, h, _s = sat_liquid(tk)
        self.set_output("ENTHALPY", _h_out(h, us))
        self._publish_limits(flag)
        self.status = BlockStatus.UNCERTAIN if flag else BlockStatus.GOOD


# ═══════════════════════════════════════════════════════════════════════
#  WTS — Water Entropy
# ═══════════════════════════════════════════════════════════════════════

@register_block
class WaterEntropyBlock(_SteamBlockBase):
    """Water Entropy (WTS) — saturated-liquid entropy at a temperature.

    Companion to WTH; valid range 32 to 704 degF (0 to 373.3 degC), with
    UNCERTAIN status on a clamped input. Typically feeds ENTROPY into an
    ISE isentropic-expansion calculation.
    """
    block_type = "WTS"
    display_name = "Water Entropy (WTS)"
    description = "IF97 saturated-water entropy from temperature"
    config_choices = _UNIT_SET_CHOICES

    _T_MIN = T_TRIPLE
    _T_MAX = 373.3 + 273.15

    def _define_terminals(self):
        self.add_input("TEMP", description="Water temperature (degF | degC)")
        self.add_output("ENTROPY",
                        description="Water entropy (Btu/lb-degR | kJ/kg-K)")
        self._add_limit_outputs()

    def get_config_schema(self):
        return self._unit_set_schema()

    def execute(self, dt: float):
        us = _is_us(self.config.params)
        tk = _temp_to_K(float(self.get_input("TEMP")), us)
        tk, flag = _clamp(tk, self._T_MIN, self._T_MAX)

        _v, _h, s = sat_liquid(tk)
        self.set_output("ENTROPY", _s_out(s, us))
        self._publish_limits(flag)
        self.status = BlockStatus.UNCERTAIN if flag else BlockStatus.GOOD


# ═══════════════════════════════════════════════════════════════════════
#  SDR — Steam Density Ratio
# ═══════════════════════════════════════════════════════════════════════

@register_block
class SteamDensityRatioBlock(_SteamBlockBase):
    """Steam Density Ratio (SDR) — meter density-correction factor.

    The classic pressure/temperature compensation factor for a steam
    flow meter: the square root of the ratio of actual steam density to
    the density of steam at the meter's calibration pressure and
    temperature.

    The Azeo manual never writes the formula explicitly; it states the
    output in prose as "the square root of the ratio of steam density to
    the calibration density" while giving both *inputs* as specific
    volumes. Since density is the reciprocal of specific volume, that
    prose renders as::

        RATIO = sqrt(rho / rho_cal) = sqrt((1/SPEC_VOL) / (1/SPE_VOL_CALI))
              = sqrt(SPE_VOL_CALI / SPEC_VOL)

    which is the reading implemented here (see doc/AZEO_FUNCTION_BLOCKS.md,
    "Steam Density Ratio (SDR)", where the ambiguity is flagged). It is
    also the physically right one: an orifice/vortex meter calibrated at
    a reference density reads high when actual density is lower, and the
    correction multiplies the indicated flow by sqrt(rho_actual/rho_cal).

    Both inputs simply share the same units — there is no UNIT_SET.
    Negative inputs force RATIO to zero with BAD status.
    """
    block_type = "SDR"
    display_name = "Steam Density Ratio (SDR)"
    description = "sqrt(density ratio) meter correction from two specific volumes"

    def _define_terminals(self):
        self.add_input("SPEC_VOL", DataType.FLOAT, 1.0,
                       "Actual specific volume (cu ft/lb | m3/kg)")
        self.add_input("SPE_VOL_CALI", DataType.FLOAT, 1.0,
                       "Specific volume at meter calibration condition")
        self.add_output("RATIO", description="Steam density ratio (dimensionless)")

    def execute(self, dt: float):
        v = float(self.get_input("SPEC_VOL"))
        v_cal = float(self.get_input("SPE_VOL_CALI"))

        # Spec: "If an input value is negative, RATIO status is set to Bad
        # and its value forced to zero." A zero actual specific volume is
        # likewise unusable (division by zero) and is treated the same way.
        if v < 0.0 or v_cal < 0.0 or v == 0.0:
            self.set_output("RATIO", 0.0)
            self.status = BlockStatus.BAD
            return

        self.set_output("RATIO", math.sqrt(v_cal / v))
        self.status = BlockStatus.GOOD


# ═══════════════════════════════════════════════════════════════════════
#  ISE — Isentropic Expansion
# ═══════════════════════════════════════════════════════════════════════

@register_block
class IsentropicExpansionBlock(_SteamBlockBase):
    """Isentropic Expansion (ISE) — final enthalpy after expansion to a pressure.

    Given the exhaust pressure and the entropy at the turbine/expander
    inlet, returns the enthalpy, temperature and steam quality at the end
    of an isentropic expansion. If the (p, s) point falls inside the
    two-phase dome the block returns the combined liquid+vapour enthalpy
    at constant entropy, the saturation temperature, and the moisture
    fraction. Valid pressure range -14.696 psig < P <= 14489 psig
    (0 < P <= 100 MPa absolute).

    QUALITY is reported as **percent moisture** per the spec, i.e.
    100*(1 - x): 0 % for superheated/supercritical steam and 100 % for a
    subcooled-liquid result.

    Out-of-range pressure clamps to the IF97 boundary; a (p, s) pair
    implying T > 800 degC (1472 degF) clamps the *entropy* to the value
    at the 800 degC boundary. Either clamp sets the limit outputs and
    forces BAD status.
    """
    block_type = "ISE"
    display_name = "Isentropic Expansion (ISE)"
    description = "IF97 enthalpy / temperature / moisture after isentropic expansion"
    config_choices = _UNIT_SET_CHOICES

    def _define_terminals(self):
        self.add_input("PRES",
                       description="Expansion-end gauge pressure (psig | MPa gauge)")
        self.add_input("ENTROPY",
                       description="Entropy at expansion (Btu/lb-degR | kJ/kg-K)")
        self.add_output("ENTHALPY",
                        description="Final enthalpy (Btu/lb | kJ/kg)")
        self.add_output("TEMP", description="Steam temperature (degF | degC)")
        self.add_output("QUALITY",
                        description="Steam quality as percent moisture")
        self._add_limit_outputs()

    def get_config_schema(self):
        return self._unit_set_schema()

    def execute(self, dt: float):
        us = _is_us(self.config.params)
        pa = _pres_to_MPa_abs(float(self.get_input("PRES")), us)
        s = _s_in(float(self.get_input("ENTROPY")), us)

        pa, flag = _clamp(pa, P_TRIPLE, P_MAX)

        # Below the critical pressure the two-phase dome must be tested
        # before inverting s(p, T), which is discontinuous across it.
        if pa < P_CRIT:
            ts = T_sat(pa)
            _vf, hf, sf = sat_liquid(ts)
            _vg, hg, sg = sat_vapour(ts)
        else:
            ts = hf = sf = hg = sg = 0.0

        if pa < P_CRIT and sf <= s <= sg:
            x = (s - sf) / (sg - sf) if sg > sf else 0.0
            h = hf + x * (hg - hf)
            tk = ts
            moisture = 100.0 * (1.0 - x)
        else:
            if pa < P_CRIT and s < sf:
                # Subcooled liquid branch: region 1, below Tsat.
                tk = T_from_ps(pa, s, T_TRIPLE, min(ts, T_13))
                moisture = 100.0
            else:
                lo = ts if pa < P_CRIT else T_TRIPLE
                s_max = props(pa, T_25)[2]
                if s > s_max:
                    # Spec: clamp entropy to the 800 degC boundary value.
                    s = s_max
                    flag = flag or 1
                tk = T_from_ps(pa, s, lo, T_25)
                moisture = 0.0
            h = props(pa, tk)[1]

        self.set_output("ENTHALPY", _h_out(h, us))
        self.set_output("TEMP", _K_to_temp(tk, us))
        self.set_output("QUALITY", moisture)
        self._publish_limits(flag)
        self.status = BlockStatus.BAD if flag else BlockStatus.GOOD
