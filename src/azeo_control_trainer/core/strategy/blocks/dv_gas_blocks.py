"""Azeo Energy Metering blocks — natural-gas custody flow metering.

Layout:
    AGA_SI — Flow Metering, SI units  (kPa / °C / mm / kg / m³ / GJ)
    AGA_US — Flow Metering, US units  (inH2O@68°F / psia / °F / in / lb / ft³ / MMBTU)

Both blocks are the same meter: an orifice (differential-pressure) or turbine
(pulse) gas meter that reports instantaneous mass flow, flowing- and base-
condition volumetric flow, energy flow, and the four totalizers.  They differ
only in the engineering units fixed to each parameter, so the physics lives in
module-level functions and each block class wraps it with its unit conversions.

Standards implemented (per the Azeo block reference,
``doc/AZEO_FUNCTION_BLOCKS.md`` — "Flow Metering (AGA_SI)" / "(AGA_US)"):

* **AGA Report No. 8 (1994), detail characterization method** — compressibility
  and density from the 21-component composition.  The coefficient tables below
  are the AGA-8 DETAIL tables; this implementation reproduces the NIST
  reference implementation to 12 significant figures (see ``tests/_smoke_dv_gas.py``).
* **AGA Report No. 3 (1995)** — orifice mass flow: Reader-Harris/Gallagher
  discharge coefficient (iterated against pipe Reynolds number), expansion
  factor, and thermal expansion of the orifice bore and pipe bore.
* **AGA Report No. 7 (1996)** — turbine metering: flowing volume → base volume
  and mass via the two AGA-8 densities.
* **ISO 6976:1999** — real-gas *superior* (gross) volumetric calorific value,
  15/15 data, using the ISO 6976 summation-factor compression correction.

Infrastructure the Azeo block has and this repo does not (omitted, not faked):

* Per-parameter (per-terminal) status.  Azeo sets the output status to the
  worst of the *input parameter* statuses and the status implied by
  ERROR_STATE; this repo's ``Terminal`` carries no status, so the block status
  is derived from ERROR_STATE alone (the accumulator "percent good" bookkeeping
  described by the doc is implemented against that block status).
* "Restore parameter values after restart" — the module-level Azeo download
  option that makes the totalizers survive a controller restart.
* MODE: the Azeo AGA blocks offer Auto and OOS only (there is no operator
  output to hold), so no Man mode is exposed here.
"""
from __future__ import annotations

import math
from typing import Sequence

from ..model.block_base import (
    FunctionBlock, BlockCategory, BlockStatus, DataType,
)
from ..model.block_registry import register_block


# ═══════════════════════════════════════════════════════════════════════
#  AGA-8 (1994) DETAIL characterization — coefficient tables
# ═══════════════════════════════════════════════════════════════════════
# Component order below is the AGA-8 standard order:
#   1 methane, 2 nitrogen, 3 CO2, 4 ethane, 5 propane, 6 i-butane,
#   7 n-butane, 8 i-pentane, 9 n-pentane, 10 n-hexane, 11 n-heptane,
#   12 n-octane, 13 n-nonane, 14 n-decane, 15 hydrogen, 16 oxygen,
#   17 CO, 18 water, 19 H2S, 20 helium, 21 argon
# The Azeo GAS_COMP array uses a *different* order (see _GC_TO_AGA8).

_R_AGA8 = 8.31451        # J/(mol·K) — the gas constant AGA-8 (1994) is fit with
_NC = 21                 # components
_NT = 58                 # equation-of-state terms

# Table 4 — equation-of-state coefficients a(n)
_AN = (
    0.1538326, 1.341953, -2.998583, -0.04831228, 0.3757965, -1.589575,
    -0.05358847, 0.88659463, -0.71023704, -1.471722, 1.32185035,
    -0.78665925, 2.29129e-09, 0.1576724, -0.4363864, -0.04408159,
    -0.003433888, 0.03205905, 0.02487355, 0.07332279, -0.001600573,
    0.6424706, -0.4162601, -0.06689957, 0.2791795, -0.6966051, -0.002860589,
    -0.008098836, 3.150547, 0.007224479, -0.7057529, 0.5349792, -0.07931491,
    -1.418465, -5.99905e-17, 0.1058402, 0.03431729, -0.007022847,
    0.02495587, 0.04296818, 0.7465453, -0.2919613, 7.294616, -9.936757,
    -0.005399808, -0.2432567, 0.04987016, 0.003733797, 1.874951,
    0.002168144, -0.6587164, 0.000205518, 0.009776195, -0.02048708,
    0.01557322, 0.006862415, -0.001226752, 0.002850908
)

# Temperature exponents u(n)
_UN = (
    0.0, 0.5, 1.0, 3.5, -0.5, 4.5, 0.5, 7.5, 9.5, 6.0, 12.0, 12.5, -6.0,
    2.0, 3.0, 2.0, 2.0, 11.0, -0.5, 0.5, 0.0, 4.0, 6.0, 21.0, 23.0, 22.0,
    -1.0, -0.5, 7.0, -1.0, 6.0, 4.0, 1.0, 9.0, -13.0, 21.0, 8.0, -0.5, 0.0,
    2.0, 7.0, 9.0, 22.0, 23.0, 1.0, 9.0, 3.0, 8.0, 23.0, 1.5, 5.0, -0.5,
    4.0, 7.0, 3.0, 0.0, 1.0, 0.0
)

# Density exponents b(n)
_BN = (
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2,
    2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 4, 4, 4, 4, 4, 4, 4, 5, 5, 5, 5,
    5, 6, 6, 7, 7, 8, 8, 8, 9, 9
)

# Exponents on density inside exp[-c(n)·D^k(n)]
_KN = (
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 3, 2, 2, 2, 4, 4, 0, 0, 2, 2, 2, 4,
    4, 4, 4, 0, 1, 1, 2, 2, 3, 3, 4, 4, 4, 0, 0, 2, 2, 2, 4, 4, 0, 2, 2, 4,
    4, 0, 2, 0, 2, 1, 2, 2, 2, 2
)

# Term flags f(n) / g(n) / q(n) / s(n) / w(n)
_FN = (
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0
)

_GN = (
    0, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    1, 0, 0, 0, 1, 0, 0, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 1, 0, 0, 1, 0, 1, 0, 0
)

_QN = (
    0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 1, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0,
    1, 0, 0, 1, 0, 0, 0, 0, 0, 1
)

_SN = (
    0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0
)

_WN = (
    0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0
)

# Table 2 — molar masses, g/mol
_MW = (
    16.043, 28.0135, 44.01, 30.07, 44.097, 58.123, 58.123, 72.15, 72.15,
    86.177, 100.204, 114.231, 128.258, 142.285, 2.0159, 31.9988, 28.01,
    18.0153, 34.082, 4.0026, 39.948
)

# Characteristic energy parameters E(i), K
_EI = (
    151.3183, 99.73778, 241.9606, 244.1667, 298.1183, 324.0689, 337.6389,
    365.5999, 370.6823, 402.636293, 427.72263, 450.325022, 470.840891,
    489.558373, 26.95794, 122.7667, 105.5348, 514.0156, 296.355, 2.610111,
    119.6299
)

# Size parameters K(i), m³/kmol^(1/3)
_KI = (
    0.4619255, 0.4479153, 0.4557489, 0.5279209, 0.583749, 0.6406937,
    0.6341423, 0.6738577, 0.6798307, 0.7175118, 0.7525189, 0.784955,
    0.8152731, 0.8437826, 0.3514916, 0.4186954, 0.4533894, 0.3825868,
    0.4618263, 0.3589888, 0.4216551
)

# Orientation parameters G(i)
_GI = (
    0.0, 0.027815, 0.189065, 0.0793, 0.141239, 0.256692, 0.281835, 0.332267,
    0.366911, 0.289731, 0.337542, 0.383381, 0.427354, 0.469659, 0.034369,
    0.021, 0.038953, 0.3325, 0.0885, 0.0, 0.0
)

# Quadrupole parameters Q(i)
_QI = (
    0.0, 0.0, 0.69, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
    0.0, 0.0, 0.0, 1.06775, 0.633276, 0.0, 0.0
)

# High-temperature parameters F(i)
_FI = (
    0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
    1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
)

# Dipole parameters S(i)
_SI = (
    0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
    0.0, 0.0, 0.0, 1.5822, 0.39, 0.0, 0.0
)

# Association parameters W(i)
_WI = (
    0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
    0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0
)

# Table 5 — binary interaction parameters (0-based (i, j),
# all unlisted pairs default to 1.0)
_EIJ = {
    (0, 1): 0.97164, (0, 2): 0.960644, (0, 4): 0.994635, (0, 5): 1.01953,
    (0, 6): 0.989844, (0, 7): 1.00235, (0, 8): 0.999268, (0, 9): 1.107274,
    (0, 10): 0.88088, (0, 11): 0.880973, (0, 12): 0.881067,
    (0, 13): 0.881161, (0, 14): 1.17052, (0, 16): 0.990126,
    (0, 17): 0.708218, (0, 18): 0.931484, (1, 2): 1.02274, (1, 3): 0.97012,
    (1, 4): 0.945939, (1, 5): 0.946914, (1, 6): 0.973384, (1, 7): 0.95934,
    (1, 8): 0.94552, (1, 14): 1.08632, (1, 15): 1.021, (1, 16): 1.00571,
    (1, 17): 0.746954, (1, 18): 0.902271, (2, 3): 0.925053,
    (2, 4): 0.960237, (2, 5): 0.906849, (2, 6): 0.897362, (2, 7): 0.726255,
    (2, 8): 0.859764, (2, 9): 0.855134, (2, 10): 0.831229,
    (2, 11): 0.80831, (2, 12): 0.786323, (2, 13): 0.765171,
    (2, 14): 1.28179, (2, 16): 1.5, (2, 17): 0.849408, (2, 18): 0.955052,
    (3, 4): 1.02256, (3, 6): 1.01306, (3, 8): 1.00532, (3, 14): 1.16446,
    (3, 17): 0.693168, (3, 18): 0.946871, (4, 6): 1.0049,
    (4, 14): 1.034787, (5, 14): 1.3, (6, 14): 1.3, (9, 18): 1.008692,
    (10, 18): 1.010126, (11, 18): 1.011501, (12, 18): 1.012821,
    (13, 18): 1.014089, (14, 16): 1.1,
}

_UIJ = {
    (0, 1): 0.886106, (0, 2): 0.963827, (0, 4): 0.990877, (0, 6): 0.992291,
    (0, 8): 1.00367, (0, 9): 1.302576, (0, 10): 1.191904,
    (0, 11): 1.205769, (0, 12): 1.219634, (0, 13): 1.233498,
    (0, 14): 1.15639, (0, 18): 0.736833, (1, 2): 0.835058,
    (1, 3): 0.816431, (1, 4): 0.915502, (1, 6): 0.993556,
    (1, 14): 0.408838, (1, 18): 0.993476, (2, 3): 0.96987,
    (2, 9): 1.066638, (2, 10): 1.077634, (2, 11): 1.088178,
    (2, 12): 1.098291, (2, 13): 1.108021, (2, 16): 0.9, (2, 18): 1.04529,
    (3, 4): 1.065173, (3, 5): 1.25, (3, 6): 1.25, (3, 7): 1.25,
    (3, 8): 1.25, (3, 14): 1.61666, (3, 18): 0.971926, (9, 18): 1.028973,
    (10, 18): 1.033754, (11, 18): 1.038338, (12, 18): 1.042735,
    (13, 18): 1.046966,
}

_KIJ = {
    (0, 1): 1.00363, (0, 2): 0.995933, (0, 4): 1.007619, (0, 6): 0.997596,
    (0, 8): 1.002529, (0, 9): 0.982962, (0, 10): 0.983565,
    (0, 11): 0.982707, (0, 12): 0.981849, (0, 13): 0.980991,
    (0, 14): 1.02326, (0, 18): 1.00008, (1, 2): 0.982361, (1, 3): 1.00796,
    (1, 14): 1.03227, (1, 18): 0.942596, (2, 3): 1.00851, (2, 9): 0.910183,
    (2, 10): 0.895362, (2, 11): 0.881152, (2, 12): 0.86752,
    (2, 13): 0.854406, (2, 18): 1.00779, (3, 4): 0.986893,
    (3, 14): 1.02034, (3, 18): 0.999969, (9, 18): 0.96813,
    (10, 18): 0.96287, (11, 18): 0.957828, (12, 18): 0.952441,
    (13, 18): 0.948338,
}

_GIJ = {
    (0, 2): 0.807653, (0, 14): 1.95731, (1, 2): 0.982746, (2, 3): 0.370296,
    (2, 17): 1.67309,
}

# Azeo GAS_COMP array index (0-based) → AGA-8 component index (0-based).
# GAS_COMP order per the doc: methane, nitrogen, CO2, ethane, propane, water,
# H2S, hydrogen, CO, oxygen, i-butane, n-butane, i-pentane, n-pentane,
# n-hexane, n-heptane, n-octane, n-nonane, n-decane, helium, argon.
_GC_TO_AGA8 = (0, 1, 2, 3, 4, 17, 18, 14, 16, 15, 5, 6, 7, 8, 9, 10, 11, 12, 13, 19, 20)

# GAS_COMP config-parameter names, in Azeo array order.
_GC_PARAMS = (
    "GC_METHANE", "GC_NITROGEN", "GC_CO2", "GC_ETHANE", "GC_PROPANE",
    "GC_WATER", "GC_H2S", "GC_HYDROGEN", "GC_CO", "GC_OXYGEN",
    "GC_I_BUTANE", "GC_N_BUTANE", "GC_I_PENTANE", "GC_N_PENTANE",
    "GC_N_HEXANE", "GC_N_HEPTANE", "GC_N_OCTANE", "GC_N_NONANE",
    "GC_N_DECANE", "GC_HELIUM", "GC_ARGON",
)

# Dry air, in AGA-8 component order — used for the real-gas relative density
# (specific gravity), which AGA-8 defines as the ratio of the gas and air mass
# densities at the *base* conditions.
_AIR_X = [0.0] * _NC
_AIR_X[1] = 0.780848    # N2
_AIR_X[15] = 0.209390   # O2
_AIR_X[20] = 0.009332   # Ar
_AIR_X[2] = 0.000430    # CO2


# ═══════════════════════════════════════════════════════════════════════
#  AGA-8 DETAIL solver
# ═══════════════════════════════════════════════════════════════════════

def _aga8_setup():
    """Pre-compute the composition-independent binary parameter tables."""
    ki25 = [k ** 2.5 for k in _KI]
    ei25 = [e ** 2.5 for e in _EI]
    bsnij2 = [[[0.0] * 18 for _ in range(_NC)] for _ in range(_NC)]
    kij5 = [[0.0] * _NC for _ in range(_NC)]
    uij5 = [[0.0] * _NC for _ in range(_NC)]
    gij5 = [[0.0] * _NC for _ in range(_NC)]
    for i in range(_NC):
        for j in range(i, _NC):
            key = (i, j)
            eij = _EIJ.get(key, 1.0)
            uij = _UIJ.get(key, 1.0)
            kij = _KIJ.get(key, 1.0)
            gij = _GIJ.get(key, 1.0)
            for n in range(18):
                b = 1.0
                if _GN[n]:
                    b = gij * (_GI[i] + _GI[j]) / 2.0
                if _QN[n]:
                    b *= _QI[i] * _QI[j]
                if _FN[n]:
                    b *= _FI[i] * _FI[j]
                if _SN[n]:
                    b *= _SI[i] * _SI[j]
                if _WN[n]:
                    b *= _WI[i] * _WI[j]
                bsnij2[i][j][n] = (_AN[n] * (eij * math.sqrt(_EI[i] * _EI[j])) ** _UN[n]
                                   * (_KI[i] * _KI[j]) ** 1.5 * b)
            kij5[i][j] = (kij ** 5 - 1.0) * ki25[i] * ki25[j]
            uij5[i][j] = (uij ** 5 - 1.0) * ei25[i] * ei25[j]
            gij5[i][j] = (gij - 1.0) * (_GI[i] + _GI[j]) / 2.0
    return ki25, ei25, bsnij2, kij5, uij5, gij5


_KI25, _EI25, _BSNIJ2, _KIJ5, _UIJ5, _GIJ5 = _aga8_setup()

# Composition-dependent terms are expensive and change rarely — cache them
# keyed on the (rounded) composition, exactly as the reference implementation
# short-circuits when x() has not changed.
_XTERM_CACHE: dict[tuple, tuple] = {}


def _aga8_x_terms(x: Sequence[float]):
    """Mixture size/energy parameters (K³, second-virial sums, C*n)."""
    key = tuple(round(v, 9) for v in x)
    hit = _XTERM_CACHE.get(key)
    if hit is not None:
        return hit

    k3 = 0.0
    u = 0.0
    g = 0.0
    q = 0.0
    f = 0.0
    bs = [0.0] * 18
    for i in range(_NC):
        xi = x[i]
        if xi <= 0.0:
            continue
        xi2 = xi * xi
        k3 += xi * _KI25[i]
        u += xi * _EI25[i]
        g += xi * _GI[i]
        q += xi * _QI[i]
        f += xi2 * _FI[i]
        bii = _BSNIJ2[i][i]
        for n in range(18):
            bs[n] += xi2 * bii[n]
    k3 *= k3
    u *= u
    for i in range(_NC - 1):
        if x[i] <= 0.0:
            continue
        for j in range(i + 1, _NC):
            if x[j] <= 0.0:
                continue
            xij = 2.0 * x[i] * x[j]
            k3 += xij * _KIJ5[i][j]
            u += xij * _UIJ5[i][j]
            g += xij * _GIJ5[i][j]
            bij = _BSNIJ2[i][j]
            for n in range(18):
                bs[n] += xij * bij[n]
    k3 = k3 ** 0.6
    u = u ** 0.2

    q2 = q * q
    csn = [0.0] * _NT
    for n in range(12, _NT):
        c = _AN[n] * u ** _UN[n]
        if _GN[n]:
            c *= g
        if _QN[n]:
            c *= q2
        if _FN[n]:
            c *= f
        csn[n] = c

    result = (k3, bs, csn)
    if len(_XTERM_CACHE) > 64:
        _XTERM_CACHE.clear()
    _XTERM_CACHE[key] = result
    return result


def _aga8_pressure(t_k: float, d_mol_l: float, k3: float, bs, csn):
    """Pressure (kPa), Z and dP/dD at temperature ``t_k`` and molar density."""
    dred = k3 * d_mol_l
    dknn = [1.0] * 10
    for n in range(1, 10):
        dknn[n] = dred * dknn[n - 1]
    expn = [1.0] * 5
    for n in range(1, 5):
        expn[n] = math.exp(-dknn[n])
    rt = _R_AGA8 * t_k

    ar01 = 0.0      # D·∂(ar)/∂D
    ar02 = 0.0      # D²·∂²(ar)/∂D²
    for n in range(_NT):
        tun = t_k ** (-_UN[n])
        sum_b = 0.0
        sum_0 = 0.0
        coef_d1 = 0.0
        coef_d2 = 0.0
        if n < 18:
            s = bs[n] * d_mol_l
            if n >= 12:
                s -= csn[n] * dred
            sum_b = s * tun
        if n >= 12:
            sum_0 = csn[n] * dknn[_BN[n]] * tun * expn[_KN[n]]
            bkd = _BN[n] - _KN[n] * dknn[_KN[n]]
            ckd = _KN[n] * _KN[n] * dknn[_KN[n]]
            coef_d1 = bkd
            coef_d2 = bkd * (bkd - 1.0) - ckd
        ar01 += rt * (sum_0 * coef_d1 + sum_b)
        ar02 += rt * (sum_0 * coef_d2)

    z = 1.0 + ar01 / rt
    p = d_mol_l * rt * z
    dpdd = rt + 2.0 * ar01 + ar02
    return p, z, dpdd


def aga8_detail(x: Sequence[float], t_k: float, p_kpa: float,
                iterate_lim: int = 20) -> tuple[float, float, float, bool]:
    """AGA-8 (1994) detail method.

    ``x`` — 21 mole *fractions* in AGA-8 component order (must sum to 1).
    Returns ``(Z, molar_density_mol_per_l, molar_mass_g_per_mol, converged)``.

    The density solve is Newton's method on log(P) vs log(v), as specified by
    AGA-8; ``iterate_lim`` is the block's ITERATE_LIM8.
    """
    mr = sum(x[i] * _MW[i] for i in range(_NC))
    if p_kpa <= 0.0 or t_k <= 0.0:
        return 1.0, 0.0, mr, True

    k3, bs, csn = _aga8_x_terms(x)
    d = p_kpa / _R_AGA8 / t_k          # ideal-gas first estimate
    plog = math.log(p_kpa)
    vlog = -math.log(d)
    for _ in range(max(1, int(iterate_lim))):
        if vlog < -7.0 or vlog > 100.0:
            break
        d = math.exp(-vlog)
        p2, z, dpdd = _aga8_pressure(t_k, d, k3, bs, csn)
        if dpdd < 1e-15 or p2 < 1e-15:
            vlog += 0.1
            continue
        vdiff = (math.log(p2) - plog) * p2 / (-d * dpdd)
        vlog -= vdiff
        if abs(vdiff) < 1e-7:
            d = math.exp(-vlog)
            _, z, _ = _aga8_pressure(t_k, d, k3, bs, csn)
            return z, d, mr, True
    # Non-convergent: fall back to the ideal gas, flagged for ERROR_STATE.
    return 1.0, p_kpa / _R_AGA8 / t_k, mr, False


def aga8_density(x: Sequence[float], t_c: float, p_kpa: float,
                 iterate_lim: int = 20) -> tuple[float, float, float, bool]:
    """Convenience wrapper: returns ``(Z, mass_density_kg_m3, Mr, converged)``."""
    t_k = t_c + 273.15
    z, d_mol_l, mr, ok = aga8_detail(x, t_k, p_kpa, iterate_lim)
    # ρ[kg/m³] = P[kPa]·Mr[g/mol] / (Z·R·T)  (mol/l == kmol/m³)
    rho = d_mol_l * mr if d_mol_l else 0.0
    return z, rho, mr, ok


# ═══════════════════════════════════════════════════════════════════════
#  ISO 6976:1999 — real-gas superior (gross) volumetric calorific value
# ═══════════════════════════════════════════════════════════════════════
# Molar superior calorific values, kJ/mol, 15 °C combustion reference
# (ISO 6976 Table 3), in Azeo GAS_COMP order.  Cross-checked against the
# GPA 2145 ideal volumetric heating values at 60 °F / 14.696 psia
# (methane 1010.0, ethane 1769.7, propane 2516.1 BTU/ft³ …).
_ISO6976_HS = (
    891.51,    # methane
    0.0,       # nitrogen
    0.0,       # carbon dioxide
    1562.14,   # ethane
    2221.10,   # propane
    0.0,       # water
    562.38,    # hydrogen sulfide
    286.15,    # hydrogen
    282.98,    # carbon monoxide
    0.0,       # oxygen
    2870.58,   # i-butane
    2879.76,   # n-butane
    3531.68,   # i-pentane
    3538.60,   # n-pentane
    4198.24,   # n-hexane
    4857.18,   # n-heptane
    5516.01,   # n-octane
    6175.82,   # n-nonane
    6834.90,   # n-decane
    0.0,       # helium
    0.0,       # argon
)

# ISO 6976 summation factors √b at 15 °C / 101.325 kPa, in GAS_COMP order.
# The mixture compression factor is Z = 1 − (Σ xᵢ·√bᵢ)².  The C6+ entries are
# extrapolated along the homologous series; at the AGA-8 composition limits
# (C6–C10 ≤ 0.2 mol% combined) their contribution is below 1e-5 in Z.
_ISO6976_SQRT_B = (
    0.0447, 0.0224, 0.0819, 0.0922, 0.1338, 0.0733, 0.0810, -0.0043, 0.0221,
    0.0298, 0.1789, 0.1871, 0.2510, 0.2523, 0.3286, 0.4100, 0.5100, 0.6100,
    0.7100, 0.0000, 0.0277,
)

# ISO 6976 metering reference conditions.  The Azeo source prints the SI base
# pressure as "0.101325 kPa" (aga_si) and the US one as "14.696 psig" (aga_us).
# Both are typos in the source: the ISO 6976 15/15 metering reference is
# 101.325 kPa **absolute** = 14.696 **psia**.  Implemented physically correct.
_ISO6976_REF_PRES_KPA = 101.325
_ISO6976_REF_TEMP_C = 15.0


def iso6976_heating_value(gc_frac: Sequence[float], base_t_c: float,
                          base_p_kpa: float) -> float:
    """Real-gas superior volumetric calorific value, MJ/m³.

    ``gc_frac`` — 21 mole fractions in Azeo GAS_COMP order.  Evaluated at the
    block's base conditions; with the defaults (15 °C / 101.325 kPa) this is
    exactly the ISO 6976 15/15 superior calorific value.
    """
    hs_molar = sum(gc_frac[i] * _ISO6976_HS[i] for i in range(_NC))   # kJ/mol
    s = sum(gc_frac[i] * _ISO6976_SQRT_B[i] for i in range(_NC))
    z = 1.0 - s * s
    t_k = base_t_c + 273.15
    if t_k <= 0.0 or base_p_kpa <= 0.0 or z <= 0.0:
        return 0.0
    vm = _R_AGA8 * t_k / base_p_kpa * z          # real molar volume, l/mol
    return hs_molar / vm                          # kJ/l == MJ/m³


# ═══════════════════════════════════════════════════════════════════════
#  AGA-3 (1995) — orifice metering
# ═══════════════════════════════════════════════════════════════════════
# Linear thermal expansion coefficients, 1/°C (AGA-3 lists 1/°F: stainless
# 9.25e-6, Monel 7.9e-6, carbon steel 6.2e-6).
_THERMAL_EXPANSION = {
    "STAINLESS_STEEL": 16.65e-6,
    "MONEL": 14.22e-6,
    "CARBON_STEEL": 11.16e-6,
}

# Tap-location constants (L1 = upstream, L2 = downstream, both in pipe
# diameters).  Flange taps are 1 inch either side, so L1 = L2 = 25.4/D(mm).
# AGA-3 does not support pipe taps (2.5D–8D).
_TAP_RADIUS = ("RADIUS", 1.0, 0.47)
_TAP_CORNER = ("CORNER", 0.0, 0.0)

# AGA-3 limits of use (the doc's "Invalid Pipe or Orifice Size" error state).
_BETA_MIN, _BETA_MAX = 0.1, 0.75
_ORIF_ID_MIN_MM = 11.43
_PIPE_ID_MIN_MM = 48.26
_RE_MIN = 4000.0


def aga3_discharge_coefficient(beta: float, re_d: float, pipe_id_mm: float,
                               tap_type: str = "FLANGE") -> float:
    """AGA-3 (Reader-Harris/Gallagher) coefficient of discharge Cd(FT)."""
    re_d = max(re_d, 1.0)
    tap = (tap_type or "FLANGE").upper()
    if tap.startswith("CORNER"):
        l1 = l2 = 0.0
    elif tap.startswith("RADIUS"):
        l1, l2 = 1.0, 0.47
    else:                                    # flange taps: 1 in either side
        l1 = l2 = 25.4 / max(pipe_id_mm, 1e-9)

    a = (19000.0 * beta / re_d) ** 0.8
    b = beta ** 4 / (1.0 - beta ** 4)
    c = (1.0e6 / re_d) ** 0.35
    m1 = max(2.8 - pipe_id_mm / 25.4, 0.0)   # small-pipe (< 2.8 in) correction
    m2 = 2.0 * l2 / (1.0 - beta)

    ci_ct = 0.5961 + 0.0291 * beta ** 2 - 0.2290 * beta ** 8 + 0.003 * (1.0 - beta) * m1
    upstm = ((0.0433 + 0.0712 * math.exp(-8.5 * l1) - 0.1145 * math.exp(-6.0 * l1))
             * (1.0 - 0.23 * a) * b)
    dnstm = -0.0116 * (m2 - 0.52 * m2 ** 1.3) * beta ** 1.1 * (1.0 - 0.14 * a)
    ci_ft = ci_ct + upstm + dnstm
    return ci_ft + 0.000511 * (1.0e6 * beta / re_d) ** 0.7 + (0.0210 + 0.0049 * a) * beta ** 4 * c


def aga3_expansion_factor(dp_kpa: float, p_static_kpa: float, beta: float,
                          isentropic_exp: float, pres_tap: str = "UPSTREAM") -> float:
    """AGA-3 expansion factor for the measured (upstream or downstream) tap.

    ``isentropic_exp`` of −1.0 means "incompressible fluid" (Y = 1), per the
    Azeo IS_EXP parameter.
    """
    if isentropic_exp < 0.0:
        return 1.0
    k = max(isentropic_exp, 1e-6)
    if (pres_tap or "UPSTREAM").upper().startswith("DOWN"):
        pf2 = max(p_static_kpa, 1e-9)
        pf1 = pf2 + dp_kpa
    else:
        pf1 = max(p_static_kpa, 1e-9)
        pf2 = max(pf1 - dp_kpa, 1e-9)
    x1 = dp_kpa / pf1
    y1 = 1.0 - (0.41 + 0.35 * beta ** 4) * x1 / k
    if (pres_tap or "UPSTREAM").upper().startswith("DOWN"):
        # Y2 = Y1·√(Pf1·Zf2 / Pf2·Zf1); the compressibility ratio deviates from
        # unity by well under 0.1 % across a normal ΔP, and the block holds a
        # single (cached) AGA-8 solve at the measured tap, so Zf2/Zf1 = 1 here.
        return y1 * math.sqrt(pf1 / pf2)
    return y1


def aga3_mass_flow(dp_kpa: float, p_static_kpa: float, t_flow_c: float,
                   rho_tap: float, *,
                   orif_id_mm: float, pipe_id_mm: float,
                   orif_ref_c: float, pipe_ref_c: float,
                   orif_alpha: float, pipe_alpha: float,
                   viscosity_kg_m_hr: float, isentropic_exp: float,
                   tap_type: str = "FLANGE", pres_tap: str = "UPSTREAM",
                   iterate_lim: int = 20) -> dict:
    """AGA-3 (1995) orifice mass flow, iterated on the Reynolds number.

    All arguments SI: ΔP and static pressure kPa, temperatures °C, density
    kg/m³ at the measured tap, bore/pipe mm at their reference temperatures,
    viscosity kg/(m·hr).  Returns a dict with ``mass_kg_hr``, ``beta``,
    ``re``, ``cd``, ``y``, ``converged`` and the temperature-corrected bores.
    """
    # Thermal expansion of the orifice plate and the meter tube to flowing T.
    d_mm = orif_id_mm * (1.0 + orif_alpha * (t_flow_c - orif_ref_c))
    dd_mm = pipe_id_mm * (1.0 + pipe_alpha * (t_flow_c - pipe_ref_c))
    beta = d_mm / dd_mm if dd_mm > 0.0 else 0.0

    result = {"mass_kg_hr": 0.0, "beta": beta, "re": 0.0, "cd": 0.0, "y": 1.0,
              "converged": True, "orif_id_mm": d_mm, "pipe_id_mm": dd_mm}
    if dp_kpa <= 0.0 or rho_tap <= 0.0 or beta <= 0.0 or beta >= 1.0:
        return result

    d_m = d_mm / 1000.0
    dd_m = dd_mm / 1000.0
    ev = 1.0 / math.sqrt(1.0 - beta ** 4)                    # velocity of approach
    y = aga3_expansion_factor(dp_kpa, p_static_kpa, beta, isentropic_exp, pres_tap)
    mu = max(viscosity_kg_m_hr, 1e-12) / 3600.0              # → Pa·s (kg/m·s)

    # qm = (π/4)·Cd·Ev·Y·d²·√(2·ΔP·ρ)   [ΔP in Pa → qm in kg/s]
    k_geom = (math.pi / 4.0) * ev * y * d_m ** 2 * math.sqrt(2.0 * dp_kpa * 1000.0 * rho_tap)

    cd = 0.6
    re = 0.0
    converged = False
    for _ in range(max(1, int(iterate_lim))):
        qm = cd * k_geom                                     # kg/s
        re = 4.0 * qm / (math.pi * mu * dd_m)
        cd_new = aga3_discharge_coefficient(beta, re, dd_mm, tap_type)
        if abs(cd_new - cd) < 1e-9:
            cd = cd_new
            converged = True
            break
        cd = cd_new
    qm = cd * k_geom
    re = 4.0 * qm / (math.pi * mu * dd_m)

    result.update({"mass_kg_hr": qm * 3600.0, "re": re, "cd": cd, "y": y,
                   "converged": converged})
    return result


# ═══════════════════════════════════════════════════════════════════════
#  Shared block implementation (all internals SI)
# ═══════════════════════════════════════════════════════════════════════

# ERROR_STATE named set, worst-first (the block reports the most severe active
# state).  The status each one forces comes from the doc's status-handling
# section: composition / non-convergence → Bad, geometry / Re → Uncertain.
_ERR_CLEAR = "Clear"
_ERR_COMP = "Gas Composition Sum Not 100%"
_ERR_SIZE = "Invalid Pipe or Orifice Size"
_ERR_RE = "Reynolds Number Out of Range"
_ERR_AGA8 = "AGA-8 Algo Not Convergent"
_ERR_AGA3 = "AGA-3 Algo Not Convergent"

_ERR_SEVERITY = (
    (_ERR_COMP, BlockStatus.BAD),
    (_ERR_AGA8, BlockStatus.BAD),
    (_ERR_AGA3, BlockStatus.BAD),
    (_ERR_SIZE, BlockStatus.UNCERTAIN),
    (_ERR_RE, BlockStatus.UNCERTAIN),
)

_COMP_SUM_LO, _COMP_SUM_HI = 99.995, 100.005
_AGA8_MAX_HOLD_S = 60.0     # AGA-8 recalculates at least this often


def _aga_units(*, pres: str, pres_delta: str, temp: str, length: str,
               visc: str, den: str, htg: str, dp: str) -> dict[str, str]:
    """Per-parameter units for one AGA unit set (doc §Parameters).

    ``pres`` is the absolute-pressure unit (BASE_PRES); ``pres_delta`` the
    plain pressure unit the doc gives for the PRES_CHNG deadband. The
    GAS_COMP array is mole percent in both unit sets.
    """
    units = {
        "BASE_PRES": pres,
        "BASE_TEMP": temp,
        "ORIF_ID": length,
        "ORIF_TEMP": temp,
        "PIPE_ID": length,
        "PIPE_TEMP": temp,
        "VISCOSITY": visc,
        "LOW_CUT": dp,
        "PRES_CHNG": pres_delta,
        "TEMP_CHNG": temp,
        "DEN_FLW": den,
        "DEN_BASE": den,
        "HTG_VAL": htg,
    }
    units.update({name: "mole %" for name in _GC_PARAMS})
    return units


class _AgaFlowMeterBlock(FunctionBlock):
    """Common AGA_SI / AGA_US implementation — everything internal is SI.

    Subclasses supply the unit conversions (class attributes ``_U_*``) and the
    unit strings / defaults used to build the configuration schema.
    """

    category = BlockCategory.MATH

    # Input scaling: multiply the terminal/config value to reach the SI unit.
    _U_DP = 1.0             # IN (ΔP) → kPa
    _U_PRES = 1.0           # PRES_IN / BASE_PRES / PRES_CHNG → kPa
    _U_LEN = 1.0            # ORIF_ID / PIPE_ID → mm
    _U_VISC = 1.0           # VISCOSITY → kg/(m·hr)
    _U_DEN = 1.0            # DEN_FLW / DEN_BASE → kg/m³
    _U_HTG = 1.0            # HTG_VAL → MJ/m³
    _U_VOL = 1.0            # VOL_FLW_* → m³/hr
    _U_MASS = 1.0           # MASS_FLW → kg/hr
    _U_ENGY = 1.0           # ENGY_FLW → GJ/hr
    _U_VOL_ACC = 1.0        # volume totalizer → m³
    _U_TDELTA = 1.0         # TEMP_CHNG → °C

    # Unit labels + defaults for the config schema.
    _UNITS: dict[str, str] = {}
    _DEFAULTS: dict[str, float] = {}

    # Named sets, identical in both unit sets (doc §Parameters). MODE:
    # "the Azeo AGA blocks offer Auto and OOS only" — there is no
    # operator output to hold, hence no Man. ``config_units`` differs per
    # unit set and is declared on each subclass.
    config_choices = {
        "MODE": ("AUTO", "OOS"),
        "METER_TYPE": ("DIFFERENTIAL_PRESSURE", "TURBINE"),
        "AGA8_OPT": ("CALCULATE", "MANUAL"),
        "TAP_TYPE": ("FLANGE", "RADIUS", "CORNER"),
        "PRES_TAP": ("UPSTREAM", "DOWNSTREAM"),
        "ORIF_MAT": ("STAINLESS_STEEL", "MONEL", "CARBON_STEEL"),
        "PIPE_MAT": ("STAINLESS_STEEL", "MONEL", "CARBON_STEEL"),
    }

    def __init__(self, instance_name: str = ""):
        # AGA-8 hold state
        self._last_p_kpa = None
        self._last_t_c = None
        self._last_comp = None
        self._since_calc = 1e9
        self._props = None          # (z_f, rho_f, z_b, rho_b, mr, rel_den, htg)
        # Totalizers (SI): value + the part accumulated with Good status
        self._acc = {k: 0.0 for k in (
            "curr_vol", "curr_vol_good", "curr_engy", "curr_engy_good",
            "curr_hrs", "curr_hrs_good", "vol_acc", "vol_acc_good",
            "last_vol", "last_vol_good", "last_engy", "last_engy_good",
            "last_hrs", "last_hrs_good")}
        self._timer_prev = False
        super().__init__(instance_name)

    # ── terminals ───────────────────────────────────────────────────
    def _define_terminals(self):
        u = self._UNITS
        self.add_input("IN", description=f"ΔP ({u['dp']}) or turbine volumetric flow ({u['vol']})")
        self.add_input("PRES_IN", description=f"Static pressure ({u['pres']} abs)")
        self.add_input("TEMP_IN", description=f"Flowing temperature ({u['temp']})")
        self.add_input("TIMER_ACCUM", description="0 = accumulate; >0 = copy CURR_→LAST_ and reset")

        self.add_output("MASS_FLW", description=f"Mass flow ({u['mass']})")
        self.add_output("VOL_FLW_F", description=f"Flowing volumetric flow ({u['vol']})")
        self.add_output("VOL_FLW_B", description=f"Base volumetric flow ({u['vol']})")
        self.add_output("ENGY_FLW", description=f"Energy flow ({u['engy']})")

        self.add_output("CURR_VOLUME", description=f"Active base-volume total ({u['vol_acc']})")
        self.add_output("CURR_ENERGY", description=f"Active energy total ({u['engy_acc']})")
        self.add_output("CURR_HRS_ON", description="Active flow-hours total (hours)")
        self.add_output("VOL_ACCUM", description=f"Ad-hoc base-volume total ({u['vol_acc']})")
        self.add_output("LAST_VOLUME", description=f"Base volume at last TIMER_ACCUM reset ({u['vol_acc']})")
        self.add_output("LAST_ENERGY", description=f"Energy at last TIMER_ACCUM reset ({u['engy_acc']})")
        self.add_output("LAST_HRS_ON", description="Flow-hours at last TIMER_ACCUM reset (hours)")
        for nm in ("PCT_CURR_VOLUME", "PCT_CURR_ENERGY", "PCT_CURR_HRS_ON",
                   "PCT_VOL_ACCUM", "PCT_LAST_VOLUME", "PCT_LAST_ENERGY",
                   "PCT_LAST_HRS_ON"):
            self.add_output(nm, default=100.0,
                            description="Percent of the total accumulated with Good status")

        self.add_output("DEN_FLW", description=f"Flowing density ({u['den']})")
        self.add_output("DEN_BASE", description=f"Base density ({u['den']})")
        self.add_output("ZF", default=1.0, description="Compressibility at flowing conditions")
        self.add_output("ZB", default=1.0, description="Compressibility at base conditions")
        self.add_output("F_PV", default=1.0, description="Supercompressibility √(Zb/Zf)")
        self.add_output("REL_DEN", description="Real-gas relative density (specific gravity)")
        self.add_output("HTG_VAL", description=f"ISO 6976 volumetric heating value ({u['htg']})")
        self.add_output("BETA_RATIO", description="Temperature-corrected orifice/pipe diameter ratio")
        self.add_output("RE_NUM", description="Pipe Reynolds number")
        self.add_output("COMP_SUM", default=100.0, description="Sum of the GAS_COMP mole percents")
        self.add_output("ERROR_ACT", DataType.BOOL, False, "1 = an error condition is active")
        self.add_output("ERROR_STATE", DataType.STRING, _ERR_CLEAR,
                        "Named error state (Clear when ERROR_ACT is 0)")

    # ── configuration ───────────────────────────────────────────────
    def get_config_schema(self):
        u = self._UNITS
        d = self._DEFAULTS
        schema = {
            "MODE": (str, "AUTO", "AUTO = compute normally; OOS = hold outputs, status OOS"),
            "METER_TYPE": (str, "DIFFERENTIAL_PRESSURE",
                           "DIFFERENTIAL_PRESSURE (orifice, AGA-3) or TURBINE (AGA-7)"),
            "AGA8_OPT": (str, "CALCULATE",
                         "CALCULATE = AGA-8 detail method from GAS_COMP; "
                         "MANUAL = use the entered DEN_*/Z*/F_PV/REL_DEN/HTG_VAL"),
            "BASE_PRES": (float, d["base_pres"], f"Base (reference) pressure, {u['pres']} abs"),
            "BASE_TEMP": (float, d["base_temp"], f"Base (reference) temperature, {u['temp']}"),
            "TAP_TYPE": (str, "FLANGE",
                         "FLANGE, RADIUS (D-D/2) or CORNER taps (pipe taps not supported)"),
            "PRES_TAP": (str, "UPSTREAM", "Static tap location: UPSTREAM or DOWNSTREAM"),
            "ORIF_ID": (float, d["orif_id"], f"Orifice bore at ORIF_TEMP, {u['len']}"),
            "ORIF_TEMP": (float, d["ref_temp"], f"Reference temperature for ORIF_ID, {u['temp']}"),
            "ORIF_MAT": (str, "STAINLESS_STEEL",
                         "Orifice plate material: STAINLESS_STEEL, MONEL or CARBON_STEEL"),
            "PIPE_ID": (float, d["pipe_id"], f"Meter tube bore at PIPE_TEMP, {u['len']}"),
            "PIPE_TEMP": (float, d["ref_temp"], f"Reference temperature for PIPE_ID, {u['temp']}"),
            "PIPE_MAT": (str, "CARBON_STEEL",
                         "Meter tube material: STAINLESS_STEEL, MONEL or CARBON_STEEL"),
            "VISCOSITY": (float, d["viscosity"], f"Absolute viscosity at flowing conditions, {u['visc']}"),
            "IS_EXP": (float, 1.3, "Isentropic exponent (−1.0 = incompressible; orifice only)"),
            "LOW_CUT": (float, 0.0, f"ΔP below which flow is zero, {u['dp']} (orifice only)"),
            "PRES_CHNG": (float, d["pres_chng"], f"PRES_IN change that retriggers AGA-8, {u['pres']}"),
            "TEMP_CHNG": (float, d["temp_chng"], f"TEMP_IN change that retriggers AGA-8, {u['temp']}"),
            "ITERATE_LIM3": (int, 20, "Iteration limit for the AGA-3 mass-flow solve"),
            "ITERATE_LIM8": (int, 20, "Iteration limit for the AGA-8 density solve"),
            "RESET_ACCUM": (float, 0.0, ">0 resets VOL_ACCUM; the block writes it back to 0"),
            # Manual AGA-8 entry (AGA8_OPT = MANUAL); ignored in CALCULATE mode.
            "DEN_FLW": (float, 0.0, f"Entered flowing density, {u['den']} (AGA8_OPT = MANUAL)"),
            "DEN_BASE": (float, 0.0, f"Entered base density, {u['den']} (AGA8_OPT = MANUAL)"),
            "ZF": (float, 1.0, "Entered flowing compressibility (AGA8_OPT = MANUAL)"),
            "ZB": (float, 1.0, "Entered base compressibility (AGA8_OPT = MANUAL)"),
            "F_PV": (float, 1.0, "Entered supercompressibility (AGA8_OPT = MANUAL)"),
            "REL_DEN": (float, 0.0, "Entered relative density (AGA8_OPT = MANUAL)"),
            "HTG_VAL": (float, 0.0,
                        f"Entered heating value, {u['htg']} (AGA8_OPT = MANUAL; "
                        f"not calculated in that mode)"),
        }
        # GAS_COMP[1..21], mole percent — default is 100 % methane.
        for idx, name in enumerate(_GC_PARAMS):
            label = name[3:].replace("_", "-").title()
            schema[name] = (float, 100.0 if idx == 0 else 0.0,
                            f"GAS_COMP[{idx + 1}] {label}, mole %")
        return schema

    # ── helpers ─────────────────────────────────────────────────────
    def _gas_comp(self) -> list[float]:
        p = self.config.params
        sch = self._schema_cache
        return [float(p.get(nm, sch[nm][1])) for nm in _GC_PARAMS]

    @property
    def _schema_cache(self):
        cache = getattr(self.__class__, "_SCHEMA", None)
        if cache is None:
            cache = self.get_config_schema()
            self.__class__._SCHEMA = cache
        return cache

    def _cfg(self, name):
        return self.config.params.get(name, self._schema_cache[name][1])

    def _cfg_f(self, name) -> float:
        return float(self._cfg(name))

    def _cfg_s(self, name) -> str:
        return str(self._cfg(name)).upper().replace(" ", "_")

    def _to_si_temp(self, value: float) -> float:
        """Subclass hook — convert a configured/measured temperature to °C."""
        return value

    # ── AGA-8 property block (recalculated on demand) ────────────────
    def _update_properties(self, gc_pct, p_kpa, t_c, base_p_kpa, base_t_c, dt):
        """Returns (props_dict, converged). Honors PRES_CHNG/TEMP_CHNG/60 s."""
        self._since_calc += dt
        comp_key = tuple(round(v, 6) for v in gc_pct)
        due = (self._props is None
               or comp_key != self._last_comp
               or self._since_calc >= _AGA8_MAX_HOLD_S
               or self._last_p_kpa is None
               or abs(p_kpa - self._last_p_kpa) > self._cfg_f("PRES_CHNG") * self._U_PRES
               or abs(t_c - self._last_t_c) > self._cfg_f("TEMP_CHNG") * self._U_TDELTA)
        if not due:
            return self._props, True

        total = sum(gc_pct)
        x_gc = [v / total for v in gc_pct] if total > 0 else [0.0] * _NC
        x8 = [0.0] * _NC
        for i, tgt in enumerate(_GC_TO_AGA8):
            x8[tgt] = x_gc[i]

        lim = int(self._cfg("ITERATE_LIM8"))
        z_f, rho_f, mr, ok_f = aga8_density(x8, t_c, p_kpa, lim)
        z_b, rho_b, _, ok_b = aga8_density(x8, base_t_c, base_p_kpa, lim)
        _, rho_air, _, ok_a = aga8_density(_AIR_X, base_t_c, base_p_kpa, lim)

        props = {
            "z_f": z_f, "rho_f": rho_f, "z_b": z_b, "rho_b": rho_b, "mr": mr,
            "f_pv": math.sqrt(z_b / z_f) if z_f > 0 and z_b > 0 else 1.0,
            "rel_den": rho_b / rho_air if rho_air > 0 else 0.0,
            "htg": iso6976_heating_value(x_gc, base_t_c, base_p_kpa),
        }
        converged = ok_f and ok_b and ok_a
        if converged:
            self._props = props
            self._last_p_kpa = p_kpa
            self._last_t_c = t_c
            self._last_comp = comp_key
            self._since_calc = 0.0
            return props, True
        # Non-convergent: keep the last good property set (if any) and flag it.
        return (self._props or props), False

    # ── execution ───────────────────────────────────────────────────
    def execute(self, dt: float):
        p = self.config.params
        if self._cfg_s("MODE").startswith("OOS"):
            # OOS: hold every output (including the totalizers) and report OOS.
            self.status = BlockStatus.OOS
            return

        errors: set[str] = set()

        # -- inputs → SI ---------------------------------------------
        in_raw = float(self.get_input("IN"))
        p_kpa = float(self.get_input("PRES_IN")) * self._U_PRES
        t_c = self._to_si_temp(float(self.get_input("TEMP_IN")))
        base_p_kpa = self._cfg_f("BASE_PRES") * self._U_PRES
        base_t_c = self._to_si_temp(self._cfg_f("BASE_TEMP"))
        meter_type = self._cfg_s("METER_TYPE")
        turbine = meter_type.startswith("TURBINE")

        # -- composition ---------------------------------------------
        gc_pct = self._gas_comp()
        comp_sum = sum(gc_pct)
        calculate = self._cfg_s("AGA8_OPT").startswith("CALC")
        if calculate and not (_COMP_SUM_LO <= comp_sum <= _COMP_SUM_HI):
            errors.add(_ERR_COMP)

        # -- AGA-8 properties ----------------------------------------
        if calculate:
            if _ERR_COMP in errors:
                # The composition is unusable, so no new AGA-8 solve is made:
                # hold the last calculated property set (outputs go Bad).
                props = self._props or {"rho_f": 0.0, "rho_b": 0.0, "z_f": 1.0,
                                        "z_b": 1.0, "f_pv": 1.0, "rel_den": 0.0,
                                        "htg": 0.0}
            else:
                props, ok8 = self._update_properties(gc_pct, p_kpa, t_c,
                                                     base_p_kpa, base_t_c, dt)
                if not ok8:
                    errors.add(_ERR_AGA8)
            rho_f = props["rho_f"]
            rho_b = props["rho_b"]
            z_f, z_b = props["z_f"], props["z_b"]
            f_pv, rel_den = props["f_pv"], props["rel_den"]
            htg = props["htg"]
        else:
            # AGA8_OPT = MANUAL: use the entered values.  Per the doc HTG_VAL
            # is *not* computed by the block in this mode either.
            rho_f = self._cfg_f("DEN_FLW") * self._U_DEN
            rho_b = self._cfg_f("DEN_BASE") * self._U_DEN
            z_f = self._cfg_f("ZF")
            z_b = self._cfg_f("ZB")
            f_pv = self._cfg_f("F_PV")
            rel_den = self._cfg_f("REL_DEN")
            htg = self._cfg_f("HTG_VAL") * self._U_HTG

        # -- meter path ----------------------------------------------
        mass_kg_hr = 0.0
        vol_f = 0.0
        beta = 0.0
        re = 0.0
        flowing = False

        pipe_id_mm = self._cfg_f("PIPE_ID") * self._U_LEN
        orif_id_mm = self._cfg_f("ORIF_ID") * self._U_LEN
        visc = self._cfg_f("VISCOSITY") * self._U_VISC

        if turbine:
            # AGA-7: IN is the flowing volumetric flow (from a PIN block).
            vol_f = in_raw * self._U_VOL
            mass_kg_hr = vol_f * rho_f
            flowing = vol_f > 0.0
            if pipe_id_mm > 0.0 and visc > 0.0 and mass_kg_hr > 0.0:
                # Pipe Reynolds number: informational for turbine meters (the
                # doc: viscosity/pipe data only affect Re in that case).
                re = 4.0 * (mass_kg_hr / 3600.0) / (math.pi * (visc / 3600.0)
                                                    * (pipe_id_mm / 1000.0))
            if pipe_id_mm < _PIPE_ID_MIN_MM:
                errors.add(_ERR_SIZE)
        else:
            dp_kpa = in_raw * self._U_DP
            low_cut = self._cfg_f("LOW_CUT") * self._U_DP
            orif_mat = self._cfg_s("ORIF_MAT")
            pipe_mat = self._cfg_s("PIPE_MAT")
            res = aga3_mass_flow(
                max(dp_kpa, 0.0), p_kpa, t_c, rho_f,
                orif_id_mm=orif_id_mm, pipe_id_mm=pipe_id_mm,
                orif_ref_c=self._to_si_temp(self._cfg_f("ORIF_TEMP")),
                pipe_ref_c=self._to_si_temp(self._cfg_f("PIPE_TEMP")),
                orif_alpha=_THERMAL_EXPANSION.get(orif_mat, _THERMAL_EXPANSION["STAINLESS_STEEL"]),
                pipe_alpha=_THERMAL_EXPANSION.get(pipe_mat, _THERMAL_EXPANSION["CARBON_STEEL"]),
                viscosity_kg_m_hr=visc,
                isentropic_exp=self._cfg_f("IS_EXP"),
                tap_type=self._cfg_s("TAP_TYPE"),
                pres_tap=self._cfg_s("PRES_TAP"),
                iterate_lim=int(self._cfg("ITERATE_LIM3")),
            )
            beta = res["beta"]
            re = res["re"]

            # AGA-3 limits of use → "Invalid Pipe or Orifice Size" (Uncertain).
            if (beta < _BETA_MIN or beta > _BETA_MAX
                    or res["orif_id_mm"] < _ORIF_ID_MIN_MM
                    or res["pipe_id_mm"] < _PIPE_ID_MIN_MM):
                errors.add(_ERR_SIZE)

            if dp_kpa > low_cut and dp_kpa > 0.0:
                flowing = True
                mass_kg_hr = res["mass_kg_hr"]
                if not res["converged"]:
                    errors.add(_ERR_AGA3)
                # Re is only meaningful above the low-flow cutoff.
                if re < _RE_MIN:
                    errors.add(_ERR_RE)
            else:
                mass_kg_hr = 0.0
                re = 0.0
            vol_f = mass_kg_hr / rho_f if rho_f > 0.0 else 0.0

        vol_b = mass_kg_hr / rho_b if rho_b > 0.0 else 0.0
        engy = vol_b * htg / 1000.0             # MJ/hr → GJ/hr

        # -- status --------------------------------------------------
        state = _ERR_CLEAR
        status = BlockStatus.GOOD
        for name, sts in _ERR_SEVERITY:
            if name in errors:
                state, status = name, sts
                break
        self.status = status
        good = status is BlockStatus.GOOD

        # -- totalization --------------------------------------------
        # TIMER_ACCUM is edge-triggered (typically a DTE pulse): the rising
        # edge copies CURR_ → LAST_ and zeroes CURR_; accumulation is
        # suspended for as long as TIMER_ACCUM stays > 0.
        timer = float(self.get_input("TIMER_ACCUM")) > 0.0
        if timer and not self._timer_prev:
            a = self._acc
            for src, dst in (("curr_vol", "last_vol"), ("curr_engy", "last_engy"),
                             ("curr_hrs", "last_hrs")):
                a[dst] = a[src]
                a[dst + "_good"] = a[src + "_good"]
                a[src] = 0.0
                a[src + "_good"] = 0.0
        self._timer_prev = timer

        if not timer and dt > 0.0:
            hrs = dt / 3600.0
            a = self._acc
            dv = vol_b * hrs
            de = engy * hrs
            a["curr_vol"] += dv
            a["vol_acc"] += dv
            a["curr_engy"] += de
            if good:
                a["curr_vol_good"] += dv
                a["vol_acc_good"] += dv
                a["curr_engy_good"] += de
            if flowing:
                a["curr_hrs"] += hrs
                if good:
                    a["curr_hrs_good"] += hrs

        if self._cfg_f("RESET_ACCUM") > 0.0:
            self._acc["vol_acc"] = 0.0
            self._acc["vol_acc_good"] = 0.0
            p["RESET_ACCUM"] = 0.0          # the block writes it back to 0

        # -- outputs (SI → block units) ------------------------------
        a = self._acc
        self.set_output("MASS_FLW", mass_kg_hr / self._U_MASS)
        self.set_output("VOL_FLW_F", vol_f / self._U_VOL)
        self.set_output("VOL_FLW_B", vol_b / self._U_VOL)
        self.set_output("ENGY_FLW", engy / self._U_ENGY)

        self.set_output("CURR_VOLUME", a["curr_vol"] / self._U_VOL_ACC)
        self.set_output("CURR_ENERGY", a["curr_engy"] / self._U_ENGY)
        self.set_output("CURR_HRS_ON", a["curr_hrs"])
        self.set_output("VOL_ACCUM", a["vol_acc"] / self._U_VOL_ACC)
        self.set_output("LAST_VOLUME", a["last_vol"] / self._U_VOL_ACC)
        self.set_output("LAST_ENERGY", a["last_engy"] / self._U_ENGY)
        self.set_output("LAST_HRS_ON", a["last_hrs"])
        for pct_name, key in (("PCT_CURR_VOLUME", "curr_vol"),
                              ("PCT_CURR_ENERGY", "curr_engy"),
                              ("PCT_CURR_HRS_ON", "curr_hrs"),
                              ("PCT_VOL_ACCUM", "vol_acc"),
                              ("PCT_LAST_VOLUME", "last_vol"),
                              ("PCT_LAST_ENERGY", "last_engy"),
                              ("PCT_LAST_HRS_ON", "last_hrs")):
            tot = a[key]
            self.set_output(pct_name, 100.0 * a[key + "_good"] / tot if tot > 0 else 100.0)

        self.set_output("DEN_FLW", rho_f / self._U_DEN)
        self.set_output("DEN_BASE", rho_b / self._U_DEN)
        self.set_output("ZF", z_f)
        self.set_output("ZB", z_b)
        self.set_output("F_PV", f_pv)
        self.set_output("REL_DEN", rel_den)
        self.set_output("HTG_VAL", htg / self._U_HTG)
        self.set_output("BETA_RATIO", beta)
        self.set_output("RE_NUM", re)
        self.set_output("COMP_SUM", comp_sum)
        self.set_output("ERROR_ACT", state != _ERR_CLEAR)
        self.set_output("ERROR_STATE", state)

    def reset(self):
        super().reset()
        self._props = None
        self._last_p_kpa = None
        self._last_t_c = None
        self._last_comp = None
        self._since_calc = 1e9
        self._timer_prev = False
        for k in self._acc:
            self._acc[k] = 0.0


# ═══════════════════════════════════════════════════════════════════════
#  AGA_SI — Flow Metering, SI units
# ═══════════════════════════════════════════════════════════════════════

@register_block
class AgaSiFlowMeteringBlock(_AgaFlowMeterBlock):
    """Flow Metering, SI units (AGA_SI).

    Natural-gas custody-style metering: instantaneous mass flow (kg/hr),
    flowing and base volumetric flow (m³/hr), energy flow (GJ/hr) and the
    four totalizers, for an orifice (AGA-3) or turbine (AGA-7) meter, with
    AGA-8 detail-method densities and compressibilities and an ISO 6976
    superior calorific value.

    ``IN`` is ΔP in kPa for an orifice meter (wire it from an AI on the ΔP
    transmitter) or flowing volumetric flow in m³/hr for a turbine meter
    (wire it from a Pulse Input block with PULSE_VAL in m³/pulse,
    TIME_UNITS = Hours).  ``TIMER_ACCUM`` is normally wired from a DTE block
    to schedule the contract-hour totalizer reset.

    Source-typo note: the Azeo source prints the ISO 6976 base pressure as
    "0.101325 kPa"; the physically correct ISO 6976 metering reference is
    101.325 kPa absolute, which is what this block uses (and the BASE_PRES
    default).
    """
    block_type = "AGA_SI"
    display_name = "Flow Metering — SI (AGA_SI)"
    description = "AGA-3/7/8 + ISO 6976 gas flow metering in SI units"

    _UNITS = {
        "dp": "kPa", "pres": "kPa", "temp": "°C", "len": "mm",
        "visc": "kg/m-hr", "den": "kg/m³", "htg": "MJ/m³", "mass": "kg/hr",
        "vol": "m³/hr", "engy": "GJ/hr", "vol_acc": "m³", "engy_acc": "GJ",
    }
    _DEFAULTS = {
        "base_pres": _ISO6976_REF_PRES_KPA,   # 101.325 kPa abs (see typo note)
        "base_temp": _ISO6976_REF_TEMP_C,     # 15 °C
        "orif_id": 50.8, "pipe_id": 102.26, "ref_temp": 20.0,
        "viscosity": 0.0396,                  # ≈ 1.1e-5 Pa·s
        "pres_chng": 10.0, "temp_chng": 1.0,
    }

    config_units = _aga_units(
        pres="kPa abs", pres_delta="kPa", temp="°C", length="mm",
        visc="kg/m-hr", den="kg/m³", htg="MJ/m³", dp="kPa",
    )


# ═══════════════════════════════════════════════════════════════════════
#  AGA_US — Flow Metering, US customary units
# ═══════════════════════════════════════════════════════════════════════

@register_block
class AgaUsFlowMeteringBlock(_AgaFlowMeterBlock):
    """Flow Metering, U.S. units (AGA_US).

    The U.S.-units twin of AGA_SI — same meter, same standards, every
    parameter fixed to a U.S. engineering unit: ΔP in inH2O at 68 °F,
    pressure psia, temperature °F, bores in inches, mass lb/hr, volume
    ft³/hr (totalizers MCF), energy MMBTU/hr.

    ΔP wired into ``IN`` must be in inH2O at 68 °F (×27.730 from psi,
    ×1.00083 from inH2O at 60 °F).  Limits of use in U.S. units: β 0.1–0.75,
    orifice bore ≥ 0.45 in, meter tube ≥ 1.9 in, Re ≥ 4000.

    Source-typo note: the Azeo source prints the ISO 6976 base pressure as
    "14.696 psig"; it is physically 14.696 **psia** (= 101.325 kPa absolute),
    which is what this block uses (and the BASE_PRES default).
    """
    block_type = "AGA_US"
    display_name = "Flow Metering — US (AGA_US)"
    description = "AGA-3/7/8 + ISO 6976 gas flow metering in US customary units"

    # 1 inH2O at 68 °F = 248.6415 Pa (= 1/27.7295 psi, the doc's ×27.730).
    _U_DP = 0.2486415            # inH2O@68°F → kPa
    _U_PRES = 6.894757           # psia → kPa
    _U_LEN = 25.4                # in → mm
    _U_VISC = 1.48816394         # lb/(ft·hr) → kg/(m·hr)
    _U_DEN = 16.0184634          # lb/ft³ → kg/m³
    _U_HTG = 0.0372589458        # BTU/ft³ → MJ/m³
    _U_VOL = 0.0283168466        # ft³/hr → m³/hr
    _U_MASS = 0.45359237         # lb/hr → kg/hr
    _U_ENGY = 1.05505585         # MMBTU/hr → GJ/hr
    _U_VOL_ACC = 28.3168466      # MCF (1000 ft³) → m³
    _U_TDELTA = 5.0 / 9.0        # Δ°F → Δ°C

    _UNITS = {
        "dp": "inH2O@68°F", "pres": "psia", "temp": "°F", "len": "in",
        "visc": "lb/ft-hr", "den": "lb/ft³", "htg": "BTU/ft³", "mass": "lb/hr",
        "vol": "ft³/hr", "engy": "MMBTU/hr", "vol_acc": "MCF", "engy_acc": "MMBTU",
    }
    _DEFAULTS = {
        "base_pres": 14.696,     # psia (see typo note — the source says "psig")
        "base_temp": 60.0,       # °F — the U.S. custody base temperature
        "orif_id": 2.0, "pipe_id": 4.026, "ref_temp": 68.0,
        "viscosity": 0.0266,     # ≈ 1.1e-5 Pa·s
        "pres_chng": 1.5, "temp_chng": 2.0,
    }

    # PRES_CHNG is a pressure *difference*, so the doc's table gives it as
    # plain psi while BASE_PRES / PRES_IN are psia.
    config_units = _aga_units(
        pres="psia", pres_delta="psi", temp="°F", length="in",
        visc="lb/ft-hr", den="lb/ft³", htg="BTU/ft³", dp="inH2O@68°F",
    )

    def _to_si_temp(self, value: float) -> float:
        """°F → °C."""
        return (value - 32.0) / 1.8
