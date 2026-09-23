"""FPS (US refinery engineering) unit conversions.

Internal model uses SI (K, Pa, kg/s, W, m).
Display / interface layers use FPS (°F, inH₂O, BPD, MMSCFD, MMBtu/h, ft).
All conversions are centralised here.
"""

# ── Unit labels ──────────────────────────────────────────────────────────────

TEMP_UNIT = "\u00b0F"          # °F
PRESS_UNIT = "inH\u2082O"     # inH₂O
FEED_FLOW_UNIT = "BPD"
GAS_FLOW_UNIT = "MMSCFD"
MASS_FLOW_UNIT = "lb/h"       # retained for flue-gas / misc mass flows
DUTY_UNIT = "MMBtu/h"
LENGTH_UNIT = "ft"
AREA_UNIT = "ft\u00b2"        # ft²
HTC_UNIT = "BTU/(h\u00b7ft\u00b2\u00b7\u00b0F)"  # BTU/(h·ft²·°F)

# ── Standard conditions & physical constants for volumetric gas flow ────────

_STD_TEMP = 288.71      # K  (60 °F)
_STD_PRESS = 101325.0   # Pa (14.696 psia)
_R = 8.314              # J/(mol·K)
_M3_TO_FT3 = 35.3147
_BBL_M3 = 0.158987      # m³ per barrel

# Molar volume at standard conditions [m³/mol]
_MOLAR_VOL_STD = _R * _STD_TEMP / _STD_PRESS  # ≈ 0.02369 m³/mol

# Crude oil density at 60 °F [kg/m³]  (from crude_properties.density(288.15))
_CRUDE_STD_DENSITY = 903.0

# Fuel gas average MW [g/mol]  (from operating_conditions.FUEL_AVG_MW)
# Composition: 70% CH4, 15% C2H6, 5% C3H8, 8% H2, 2% N2
_FUEL_MW = 18.665

# Air MW [g/mol]
_AIR_MW = 28.97

# Pre-computed conversion factors
_KGS_TO_BPD = 86400.0 / (_CRUDE_STD_DENSITY * _BBL_M3)   # ≈ 601.83
_KGS_TO_MMSCFD_FUEL = (86400.0 * (1000.0 / _FUEL_MW)
                       * _MOLAR_VOL_STD * _M3_TO_FT3 / 1e6)  # ≈ 3.873
_KGS_TO_MMSCFD_AIR = (86400.0 * (1000.0 / _AIR_MW)
                      * _MOLAR_VOL_STD * _M3_TO_FT3 / 1e6)   # ≈ 2.494

# ── Forward conversions (SI → FPS) ──────────────────────────────────────────

def K_to_F(T):
    """Kelvin → degrees Fahrenheit."""
    return (T - 273.15) * 9.0 / 5.0 + 32.0


def Pa_to_inH2O(P):
    """Pascals → inches of water column."""
    return P * 0.00401865


def kgs_to_bpd(m):
    """kg/s crude oil → barrels per day (at 60 °F)."""
    return m * _KGS_TO_BPD


def kgs_to_mmscfd_fuel(m):
    """kg/s fuel gas → MMSCFD (at 60 °F, 14.696 psia)."""
    return m * _KGS_TO_MMSCFD_FUEL


def kgs_to_mmscfd_air(m):
    """kg/s air → MMSCFD (at 60 °F, 14.696 psia)."""
    return m * _KGS_TO_MMSCFD_AIR


def kgs_to_lbh(m):
    """kg/s → lb/h.  Kept for flue-gas and miscellaneous mass flows."""
    return m * 7936.64


def W_to_MMBtuh(Q):
    """Watts → MMBtu/h."""
    return Q * 3.412142e-6


def m_to_ft(L):
    """Metres → feet."""
    return L * 3.28084


def m2_to_ft2(A):
    """Square metres → square feet."""
    return A * 10.7639


def WpmK_to_BtuphftF(h):
    """W/(m²·K) → BTU/(h·ft²·°F)."""
    return h * 0.17611


# ── Reverse conversions (FPS → SI) ──────────────────────────────────────────

def F_to_K(T):
    """Degrees Fahrenheit → Kelvin."""
    return (T - 32.0) * 5.0 / 9.0 + 273.15


def inH2O_to_Pa(P):
    """Inches of water column → Pascals."""
    return P / 0.00401865


def bpd_to_kgs(m):
    """Barrels per day → kg/s crude oil (at 60 °F)."""
    return m / _KGS_TO_BPD


def mmscfd_to_kgs_fuel(m):
    """MMSCFD fuel gas → kg/s (at 60 °F, 14.696 psia)."""
    return m / _KGS_TO_MMSCFD_FUEL


def mmscfd_to_kgs_air(m):
    """MMSCFD air → kg/s (at 60 °F, 14.696 psia)."""
    return m / _KGS_TO_MMSCFD_AIR


def lbh_to_kgs(m):
    """lb/h → kg/s."""
    return m / 7936.64


def MMBtuh_to_W(Q):
    """MMBtu/h → Watts."""
    return Q / 3.412142e-6


def ft_to_m(L):
    """Feet → metres."""
    return L / 3.28084


def ft2_to_m2(A):
    """Square feet → square metres."""
    return A / 10.7639


def Fdelta_to_Kdelta(dT):
    """°F temperature *offset* → K offset (magnitude only, no zero shift)."""
    return dT * 5.0 / 9.0


def Kdelta_to_Fdelta(dT):
    """K temperature *offset* → °F offset (magnitude only, no zero shift)."""
    return dT * 9.0 / 5.0


# ── Tag → quantity mapping ───────────────────────────────────────────────────

TAG_QUANTITY = {
    # Temperatures (stored in K)
    'COT': 'temperature',
    'T_gas': 'temperature',
    'T_flue_out': 'temperature',
    'T_conv_out': 'temperature',
    'T_fluid_1': 'temperature',
    'T_fluid_2': 'temperature',
    'T_fluid_3': 'temperature',
    'T_fluid_4': 'temperature',
    'T_metal_1': 'temperature',
    'T_metal_2': 'temperature',
    'T_metal_3': 'temperature',
    'T_metal_4': 'temperature',
    # Flows (stored in kg/s — each has its own display unit)
    'feed_rate': 'feed_flow',
    'feed_rate_pass_1': 'feed_flow',
    'feed_rate_pass_2': 'feed_flow',
    'feed_rate_pass_3': 'feed_flow',
    'feed_rate_pass_4': 'feed_flow',
    'fuel_rate': 'fuel_flow',
    'air_rate': 'air_flow',
    # Pressure (stored in Pa)
    'P_draft': 'pressure',
    'P_fuel_gas': 'pressure',
    # Heat duty (stored in W)
    'Q_release': 'duty',
    'Q_rad_total': 'duty',
    'Q_conv': 'duty',
    # Corrected / metered fuel flow
    'corrected_fuel_rate': 'fuel_flow',
    'meter_indicated': 'fuel_flow',
    # Additional duty
    'Q_duty': 'duty',
    # Flue gas composition (dimensionless %)
    'O2_pct': 'percent',
    'CO2_pct': 'percent',
    'H2O_pct': 'percent',
    'N2_pct': 'percent',
    'CO_ppm': 'ppm',
    'excess_air_pct': 'percent',
    # Efficiency
    'efficiency': 'percent',
    # Fuel analysis (displayed as raw dimensionless numbers)
    'fuel_sg': 'dimensionless',
    'fuel_nhv': 'dimensionless',
    'fuel_wi': 'dimensionless',
    # Simulation
    'sim_time': 'time',
    # --- Zone temperatures ---
    'T_gas_zone1': 'temperature',
    'T_gas_zone2': 'temperature',
    'T_refr_intermediate': 'temperature',
    'T_refr_cold': 'temperature',
    # --- Emissions ---
    'T_flame': 'temperature',
    'tau_residence': 'time',
    'NOx_ppm': 'ppm',
    'SO2_ppm': 'ppm',
    'CO_kinetic': 'ppm',
    # --- Two-phase ---
    'vapor_fraction_avg': 'fraction',
    'vapor_fraction_1': 'fraction',
    'vapor_fraction_2': 'fraction',
    'vapor_fraction_3': 'fraction',
    'vapor_fraction_4': 'fraction',
    # --- Pressure drop ---
    'tube_dp_total': 'pressure',
    'tube_dp_conv': 'pressure',
    'tube_dp_rad_1': 'pressure',
    'tube_dp_rad_2': 'pressure',
    'tube_dp_rad_3': 'pressure',
    'tube_dp_rad_4': 'pressure',
    'gas_dp_natural': 'pressure',
    'gas_dp_stack_friction': 'pressure',
    'gas_dp_conv_bank': 'pressure',
    'gas_dp_net_available': 'pressure',
    # --- Zoned heat duty ---
    'Q_rad_z1': 'duty',
    'Q_rad_z2': 'duty',
    'Q_interzone': 'duty',
    # --- Energy balance ---
    'energy_balance_error': 'percent',
}

# Quantity → (conversion function, unit label, default format spec)
_QUANTITY_FMT = {
    'temperature': (K_to_F,             TEMP_UNIT,      '.1f'),
    'pressure':    (Pa_to_inH2O,        PRESS_UNIT,     '.2f'),
    'feed_flow':   (kgs_to_bpd,         FEED_FLOW_UNIT, '.0f'),
    'fuel_flow':   (kgs_to_mmscfd_fuel, GAS_FLOW_UNIT,  '.3f'),
    'air_flow':    (kgs_to_mmscfd_air,  GAS_FLOW_UNIT,  '.2f'),
    'mass_flow':   (kgs_to_lbh,         MASS_FLOW_UNIT, '.1f'),
    'duty':        (W_to_MMBtuh,        DUTY_UNIT,      '.2f'),
    'length':      (m_to_ft,            LENGTH_UNIT,    '.2f'),
    'area':        (m2_to_ft2,          AREA_UNIT,      '.1f'),
    'htc':         (WpmK_to_BtuphftF,   HTC_UNIT,       '.2f'),
    'percent':     (lambda x: x,        '%',            '.1f'),
    'ppm':         (lambda x: x,        'ppm',          '.0f'),
    'fraction':    (lambda x: x * 100,  '%',            '.1f'),
    'dimensionless': (lambda x: x,     '',             '.3f'),
    'time':        (lambda x: x,        's',            '.0f'),
}


def fmt(value, quantity, fmt_spec=None):
    """Format a SI value for display in FPS units.

    Args:
        value: numeric value in SI units.
        quantity: quantity tag (key in TAG_QUANTITY) or quantity name
                  (key in _QUANTITY_FMT).
        fmt_spec: optional format spec override (e.g. '.2f').

    Returns:
        Formatted string like ``"680.0 °F"``.
    """
    # Resolve tag → quantity name if needed
    q = TAG_QUANTITY.get(quantity, quantity)
    entry = _QUANTITY_FMT.get(q)
    if entry is None:
        return f"{value}"
    conv, unit, default_fmt = entry
    spec = fmt_spec or default_fmt
    converted = conv(value)
    return f"{converted:{spec}} {unit}"
