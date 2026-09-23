"""Central tag metadata registry for all process variables.

Builds TagMeta entries from ``azeo_control_trainer.config.units.TAG_QUANTITY``
and ``azeo_control_trainer.config.control_tuning.TUNING`` so the Qt widgets
know display names, categories, engineering ranges, unit labels, and
SI-to-display conversion functions.

Display names use ISA instrument nomenclature:
  First letter  = measured variable (T, F, P, A, Q, X)
  Second letter = function (T=Transmitter, I=Indicator, V=Valve,
                            Y=Compute/Relay, E=Element)
  Number        = loop number (matches controller loop where applicable)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict

from azeo_control_trainer.config.units import (
    TAG_QUANTITY, K_to_F, Pa_to_inH2O, kgs_to_bpd,
    kgs_to_mmscfd_fuel, kgs_to_mmscfd_air, W_to_MMBtuh,
    F_to_K, inH2O_to_Pa, bpd_to_kgs,
    mmscfd_to_kgs_fuel, mmscfd_to_kgs_air,
    TEMP_UNIT, PRESS_UNIT, FEED_FLOW_UNIT, GAS_FLOW_UNIT, DUTY_UNIT,
)


@dataclass
class TagMeta:
    """Metadata for a single process tag."""
    tag: str
    display_name: str
    description: str
    category: str
    quantity: str
    eng_min: float
    eng_max: float
    si_to_display: Callable
    display_to_si: Callable | None
    display_unit: str
    format_spec: str = ".1f"


def _identity(x):
    return x


# -- Quantity -> conversion / unit / default range --------------------------
_QUANTITY_INFO = {
    'temperature': (K_to_F, F_to_K, TEMP_UNIT, 0.0, 1000.0, '.1f'),
    'pressure':    (Pa_to_inH2O, inH2O_to_Pa, PRESS_UNIT, -2.0, 0.0, '.2f'),
    'feed_flow':   (kgs_to_bpd, bpd_to_kgs, FEED_FLOW_UNIT, 0.0, 200_000.0, '.0f'),
    'fuel_flow':   (kgs_to_mmscfd_fuel, mmscfd_to_kgs_fuel, GAS_FLOW_UNIT, 0.0, 5.0, '.3f'),
    'air_flow':    (kgs_to_mmscfd_air, mmscfd_to_kgs_air, GAS_FLOW_UNIT, 0.0, 20.0, '.2f'),
    'duty':        (W_to_MMBtuh, None, DUTY_UNIT, 0.0, 200.0, '.2f'),
    'percent':     (_identity, _identity, '%', 0.0, 100.0, '.1f'),
    'ppm':         (_identity, _identity, 'ppm', 0.0, 1000.0, '.0f'),
    'time':        (_identity, _identity, 's', 0.0, 86400.0, '.0f'),
    'fraction':    (lambda x: x * 100, lambda x: x / 100, '%', 0.0, 100.0, '.1f'),
    'dimensionless': (_identity, _identity, '', 0.0, 9999.0, '.3f'),
}

# -- ISA instrument tags, descriptions, and custom ranges -------------------
# Keys: 'isa' = ISA tag (display_name), 'desc' = description, 'min'/'max' = range
_CUSTOM: Dict[str, dict] = {
    # --- Temperatures ---
    'COT':            {'isa': 'TT101',  'desc': 'Coil Outlet Temp',              'min': 500, 'max': 800},
    'T_gas':          {'isa': 'TI102',  'desc': 'Firebox Gas Temp',              'min': 1000, 'max': 2000},
    'T_flue_out':     {'isa': 'TI103',  'desc': 'Stack Flue Temp',              'min': 300, 'max': 900},
    'T_conv_out':     {'isa': 'TI104',  'desc': 'Convection Outlet Temp',       'min': 200, 'max': 700},
    'T_fluid_1':      {'isa': 'TT101A', 'desc': 'Pass 1 Fluid Temp',            'min': 400, 'max': 800},
    'T_fluid_2':      {'isa': 'TT101B', 'desc': 'Pass 2 Fluid Temp',            'min': 400, 'max': 800},
    'T_fluid_3':      {'isa': 'TT101C', 'desc': 'Pass 3 Fluid Temp',            'min': 400, 'max': 800},
    'T_fluid_4':      {'isa': 'TT101D', 'desc': 'Pass 4 Fluid Temp',            'min': 400, 'max': 800},
    'T_metal_1':      {'isa': 'TI111A', 'desc': 'Pass 1 Tube Metal Temp',       'min': 400, 'max': 900},
    'T_metal_2':      {'isa': 'TI111B', 'desc': 'Pass 2 Tube Metal Temp',       'min': 400, 'max': 900},
    'T_metal_3':      {'isa': 'TI111C', 'desc': 'Pass 3 Tube Metal Temp',       'min': 400, 'max': 900},
    'T_metal_4':      {'isa': 'TI111D', 'desc': 'Pass 4 Tube Metal Temp',       'min': 400, 'max': 900},
    'T_skin_1':       {'isa': 'TI112A', 'desc': 'Pass 1 Tube Skin Temp',        'min': 400, 'max': 1000},
    'T_skin_2':       {'isa': 'TI112B', 'desc': 'Pass 2 Tube Skin Temp',        'min': 400, 'max': 1000},
    'T_skin_3':       {'isa': 'TI112C', 'desc': 'Pass 3 Tube Skin Temp',        'min': 400, 'max': 1000},
    'T_skin_4':       {'isa': 'TI112D', 'desc': 'Pass 4 Tube Skin Temp',        'min': 400, 'max': 1000},
    'T_gas_zone1':    {'isa': 'TI105',  'desc': 'Zone 1 Gas Temp (Burner)',      'min': 1000, 'max': 2000},
    'T_gas_zone2':    {'isa': 'TI106',  'desc': 'Zone 2 Gas Temp (Bridgewall)',  'min': 900, 'max': 1800},
    'T_refr_intermediate': {'isa': 'TI107', 'desc': 'Refractory Intermediate Temp', 'min': 500, 'max': 1500},
    'T_refr_cold':    {'isa': 'TI108',  'desc': 'Refractory Cold Face Temp',    'min': 200, 'max': 800},
    'T_flame':        {'isa': 'TI109',  'desc': 'Adiabatic Flame Temp',         'min': 1500, 'max': 2500},
    # --- Flows ---
    'feed_rate':      {'isa': 'FT101',  'desc': 'Combined Crude Rate'},
    'feed_rate_pass_1': {'isa': 'FT101A', 'desc': 'Pass 1 Feed Flow'},
    'feed_rate_pass_2': {'isa': 'FT101B', 'desc': 'Pass 2 Feed Flow'},
    'feed_rate_pass_3': {'isa': 'FT101C', 'desc': 'Pass 3 Feed Flow'},
    'feed_rate_pass_4': {'isa': 'FT101D', 'desc': 'Pass 4 Feed Flow'},
    'fuel_rate':      {'isa': 'FT102',  'desc': 'Fuel Gas Flow'},
    'air_rate':       {'isa': 'FT103',  'desc': 'Combustion Air Flow'},
    'corrected_fuel_rate': {'isa': 'FY102', 'desc': 'Corrected Fuel Flow'},
    'meter_indicated': {'isa': 'FI102A', 'desc': 'Meter Indicated Flow'},
    # --- Pressures ---
    'P_draft':        {'isa': 'PT101',  'desc': 'Firebox Draft Pressure'},
    'P_fuel_gas':     {'isa': 'PT102',  'desc': 'Fuel Gas Header Pressure',     'min': -2, 'max': 5},
    # --- Analyzers ---
    'O2_pct':         {'isa': 'AT101',  'desc': 'Stack O2 Analyzer',            'min': 0, 'max': 10},
    'CO_ppm':         {'isa': 'AT102',  'desc': 'Stack CO Analyzer'},
    'CO2_pct':        {'isa': 'AT103',  'desc': 'Stack CO2',                    'min': 0, 'max': 20},
    'H2O_pct':        {'isa': 'AT104',  'desc': 'Stack H2O',                    'min': 0, 'max': 30},
    'N2_pct':         {'isa': 'AT105',  'desc': 'Stack N2',                     'min': 60, 'max': 85},
    'NOx_ppm':        {'isa': 'AT106',  'desc': 'NOx (thermal)',                'min': 0, 'max': 500},
    'SO2_ppm':        {'isa': 'AT107',  'desc': 'SO2 Emissions',               'min': 0, 'max': 50},
    'CO_kinetic':     {'isa': 'AT108',  'desc': 'CO (kinetic model)',           'min': 0, 'max': 1000},
    # --- Combustion computed ---
    'excess_air_pct': {'isa': 'AY101',  'desc': 'Excess Air',                  'min': 0, 'max': 50},
    'efficiency':     {'isa': 'EI101',  'desc': 'Thermal Efficiency',           'min': 60, 'max': 100},
    'tau_residence':  {'isa': 'XI101',  'desc': 'Furnace Residence Time',       'min': 0, 'max': 60},
    'energy_balance_error': {'isa': 'XI102', 'desc': 'Energy Balance Error',    'min': -5, 'max': 5},
    # --- Heat duty ---
    'Q_release':      {'isa': 'QI101',  'desc': 'Total Heat Release'},
    'Q_rad_total':    {'isa': 'QI102',  'desc': 'Radiant Section Absorption'},
    'Q_conv':         {'isa': 'QI103',  'desc': 'Convection Section Absorption'},
    'Q_duty':         {'isa': 'QI104',  'desc': 'Process Duty'},
    'Q_rad_z1':       {'isa': 'QI105',  'desc': 'Zone 1 Radiant Duty'},
    'Q_rad_z2':       {'isa': 'QI106',  'desc': 'Zone 2 Radiant Duty'},
    'Q_interzone':    {'isa': 'QI107',  'desc': 'Inter-zone Heat Exchange'},
    # --- Two-phase ---
    'vapor_fraction_avg': {'isa': 'XI103',  'desc': 'Avg Vapor Fraction',      'min': 0, 'max': 100},
    'vapor_fraction_1':   {'isa': 'XI103A', 'desc': 'Pass 1 Vapor Fraction',   'min': 0, 'max': 100},
    'vapor_fraction_2':   {'isa': 'XI103B', 'desc': 'Pass 2 Vapor Fraction',   'min': 0, 'max': 100},
    'vapor_fraction_3':   {'isa': 'XI103C', 'desc': 'Pass 3 Vapor Fraction',   'min': 0, 'max': 100},
    'vapor_fraction_4':   {'isa': 'XI103D', 'desc': 'Pass 4 Vapor Fraction',   'min': 0, 'max': 100},
    # --- Tube-side pressure drop ---
    'tube_dp_total':  {'isa': 'PDI101',  'desc': 'Total Tube-side \u0394P',    'min': 0, 'max': 10},
    'tube_dp_conv':   {'isa': 'PDI102',  'desc': 'Conv Section Tube \u0394P',  'min': 0, 'max': 5},
    'tube_dp_rad_1':  {'isa': 'PDI103A', 'desc': 'Pass 1 Tube \u0394P',       'min': 0, 'max': 3},
    'tube_dp_rad_2':  {'isa': 'PDI103B', 'desc': 'Pass 2 Tube \u0394P',       'min': 0, 'max': 3},
    'tube_dp_rad_3':  {'isa': 'PDI103C', 'desc': 'Pass 3 Tube \u0394P',       'min': 0, 'max': 3},
    'tube_dp_rad_4':  {'isa': 'PDI103D', 'desc': 'Pass 4 Tube \u0394P',       'min': 0, 'max': 3},
    # --- Gas-side pressure drop ---
    'gas_dp_natural':       {'isa': 'PDI104', 'desc': 'Natural Draft',         'min': -2, 'max': 0},
    'gas_dp_stack_friction': {'isa': 'PDI105', 'desc': 'Stack Friction Loss',  'min': 0, 'max': 2},
    'gas_dp_conv_bank':     {'isa': 'PDI106', 'desc': 'Conv Bank Gas \u0394P', 'min': 0, 'max': 2},
    'gas_dp_net_available': {'isa': 'PDI107', 'desc': 'Net Available Draft',   'min': -2, 'max': 0},
    # --- Fuel analysis ---
    'fuel_sg':        {'isa': 'AY102',  'desc': 'Fuel Specific Gravity',       'min': 0.4, 'max': 1.2},
    'fuel_nhv':       {'isa': 'AY103',  'desc': 'Fuel NHV (BTU/SCF)',          'min': 500, 'max': 2000},
    'fuel_wi':        {'isa': 'AY104',  'desc': 'Fuel Wobbe Index',            'min': 500, 'max': 2000},
    # --- Simulation ---
    'sim_time':       {'isa': 'XI999',  'desc': 'Simulation Time'},
    # --- BMS (Burner Management System) ---
    'bms.state':           {'isa': 'ZI201',  'desc': 'BMS State'},
    'bms.state_timer':     {'isa': 'ZI202',  'desc': 'BMS State Timer',         'min': 0, 'max': 120},
    'bms.blower_running':  {'isa': 'ZI203',  'desc': 'FD Blower Running'},
    'bms.blower_proven':   {'isa': 'ZI204',  'desc': 'Blower Air Proven'},
    'bms.draft_proven':    {'isa': 'ZI205',  'desc': 'Draft Proven'},
    'bms.igniter_on':      {'isa': 'ZI206',  'desc': 'Igniter Active'},
    'bms.pilot_fuel_open': {'isa': 'ZSV201', 'desc': 'Pilot Fuel Valve'},
    'bms.pilot_lit':       {'isa': 'ZI207',  'desc': 'Pilot Flame Detected'},
    'bms.main_fuel_open':  {'isa': 'ZSV202', 'desc': 'Main Fuel SSOV'},
    'bms.main_flame':      {'isa': 'ZI208',  'desc': 'Main Flame Detected'},
    'bms.flame_uv':        {'isa': 'BE201',  'desc': 'UV Flame Detector'},
    'bms.flame_ir':        {'isa': 'BE202',  'desc': 'IR Flame Detector'},
    'bms.permit_start':    {'isa': 'ZI209',  'desc': 'Start Permissive'},
    'bms.trip_active':     {'isa': 'ZI210',  'desc': 'Trip Active'},
    'bms.last_trip_reason': {'isa': 'ZI211', 'desc': 'Last Trip Reason'},
    'bms.startup_attempts': {'isa': 'ZI212', 'desc': 'Startup Attempts',       'min': 0, 'max': 10},
    'bms.blower_cmd':      {'isa': 'ZC201',  'desc': 'Blower Command'},
    'bms.startup_cmd':     {'isa': 'ZC202',  'desc': 'Startup Command'},
    'bms.shutdown_cmd':    {'isa': 'ZC203',  'desc': 'Shutdown Command'},
    'bms.trip_cmd':        {'isa': 'ZC204',  'desc': 'Trip Command'},
}

# -- Categories for tags ----------------------------------------------------
_TAG_CATEGORY = {
    'COT': 'Temperatures', 'T_gas': 'Temperatures', 'T_flue_out': 'Temperatures',
    'T_conv_out': 'Temperatures',
    'T_fluid_1': 'Temperatures', 'T_fluid_2': 'Temperatures',
    'T_fluid_3': 'Temperatures', 'T_fluid_4': 'Temperatures',
    'T_metal_1': 'Temperatures', 'T_metal_2': 'Temperatures',
    'T_metal_3': 'Temperatures', 'T_metal_4': 'Temperatures',
    'T_skin_1': 'Temperatures', 'T_skin_2': 'Temperatures',
    'T_skin_3': 'Temperatures', 'T_skin_4': 'Temperatures',
    'feed_rate': 'Flows', 'fuel_rate': 'Flows', 'air_rate': 'Flows',
    'feed_rate_pass_1': 'Flows', 'feed_rate_pass_2': 'Flows',
    'feed_rate_pass_3': 'Flows', 'feed_rate_pass_4': 'Flows',
    'P_draft': 'Pressure', 'P_fuel_gas': 'Pressure',
    'O2_pct': 'Combustion', 'CO_ppm': 'Combustion',
    'CO2_pct': 'Combustion', 'H2O_pct': 'Combustion', 'N2_pct': 'Combustion',
    'excess_air_pct': 'Combustion', 'efficiency': 'Combustion',
    'Q_release': 'Heat Duty', 'Q_rad_total': 'Heat Duty', 'Q_conv': 'Heat Duty',
    'Q_duty': 'Heat Duty',
    'corrected_fuel_rate': 'Flows', 'meter_indicated': 'Flows',
    'fuel_sg': 'Fuel Analysis', 'fuel_nhv': 'Fuel Analysis', 'fuel_wi': 'Fuel Analysis',
    'sim_time': 'Simulation',
    # Zone temperatures
    'T_gas_zone1': 'Temperatures', 'T_gas_zone2': 'Temperatures',
    'T_refr_intermediate': 'Temperatures', 'T_refr_cold': 'Temperatures',
    # Emissions
    'T_flame': 'Combustion', 'tau_residence': 'Combustion',
    'NOx_ppm': 'Combustion', 'SO2_ppm': 'Combustion', 'CO_kinetic': 'Combustion',
    # Two-phase
    'vapor_fraction_avg': 'Two-Phase',
    'vapor_fraction_1': 'Two-Phase', 'vapor_fraction_2': 'Two-Phase',
    'vapor_fraction_3': 'Two-Phase', 'vapor_fraction_4': 'Two-Phase',
    # Pressure drop
    'tube_dp_total': 'Pressure Drop', 'tube_dp_conv': 'Pressure Drop',
    'tube_dp_rad_1': 'Pressure Drop', 'tube_dp_rad_2': 'Pressure Drop',
    'tube_dp_rad_3': 'Pressure Drop', 'tube_dp_rad_4': 'Pressure Drop',
    'gas_dp_natural': 'Pressure Drop', 'gas_dp_stack_friction': 'Pressure Drop',
    'gas_dp_conv_bank': 'Pressure Drop', 'gas_dp_net_available': 'Pressure Drop',
    # Zoned heat duty
    'Q_rad_z1': 'Heat Duty', 'Q_rad_z2': 'Heat Duty', 'Q_interzone': 'Heat Duty',
    # Energy balance
    'energy_balance_error': 'Combustion',
    # BMS (Burner Management System)
    'bms.state': 'BMS', 'bms.state_timer': 'BMS',
    'bms.blower_running': 'BMS', 'bms.blower_proven': 'BMS',
    'bms.draft_proven': 'BMS',
    'bms.igniter_on': 'BMS', 'bms.pilot_fuel_open': 'BMS',
    'bms.pilot_lit': 'BMS', 'bms.main_fuel_open': 'BMS',
    'bms.main_flame': 'BMS',
    'bms.flame_uv': 'BMS', 'bms.flame_ir': 'BMS',
    'bms.permit_start': 'BMS', 'bms.trip_active': 'BMS',
    'bms.last_trip_reason': 'BMS', 'bms.startup_attempts': 'BMS',
    'bms.blower_cmd': 'BMS', 'bms.startup_cmd': 'BMS',
    'bms.shutdown_cmd': 'BMS', 'bms.trip_cmd': 'BMS',
}

# Valve tags  (ISA tag, description)
_VALVE_TAGS = {
    'valve_feed':   ('FV101',  'Feed Control Valve'),
    'valve_fuel':   ('FV102',  'Fuel Control Valve'),
    'valve_air':    ('FV103',  'Air Control Damper'),
    'valve_damper': ('XV101',  'Stack Damper'),
    'valve_pass_1': ('FV101A', 'Pass 1 Flow Valve'),
    'valve_pass_2': ('FV101B', 'Pass 2 Flow Valve'),
    'valve_pass_3': ('FV101C', 'Pass 3 Flow Valve'),
    'valve_pass_4': ('FV101D', 'Pass 4 Flow Valve'),
}


def build_tag_registry() -> Dict[str, TagMeta]:
    """Build the complete tag registry from config modules."""
    registry: Dict[str, TagMeta] = {}

    # Process variable tags from TAG_QUANTITY
    for tag, quantity in TAG_QUANTITY.items():
        info = _QUANTITY_INFO.get(quantity)
        if info is None:
            continue
        conv, inv, unit, default_min, default_max, fmt = info
        custom = _CUSTOM.get(tag, {})
        display = custom.get('isa', tag)
        desc = custom.get('desc', '')
        eng_min = custom.get('min', default_min)
        eng_max = custom.get('max', default_max)
        category = _TAG_CATEGORY.get(tag, 'Other')

        registry[tag] = TagMeta(
            tag=tag, display_name=display, description=desc,
            category=category, quantity=quantity,
            eng_min=eng_min, eng_max=eng_max,
            si_to_display=conv, display_to_si=inv,
            display_unit=unit, format_spec=fmt,
        )

    # Valve tags (0-1 fraction -> 0-100%)
    for vtag, (isa_tag, desc) in _VALVE_TAGS.items():
        conv_fn = _QUANTITY_INFO['fraction'][0]
        inv_fn = _QUANTITY_INFO['fraction'][1]
        registry[vtag] = TagMeta(
            tag=vtag, display_name=isa_tag, description=desc,
            category='Valves', quantity='fraction',
            eng_min=0.0, eng_max=100.0,
            si_to_display=conv_fn, display_to_si=inv_fn,
            display_unit='%', format_spec='.1f',
        )

    # BMS tags (discrete states -- no SI conversion needed)
    for bms_tag in _TAG_CATEGORY:
        if not bms_tag.startswith('bms.'):
            continue
        custom = _CUSTOM.get(bms_tag, {})
        display = custom.get('isa', bms_tag)
        desc = custom.get('desc', '')
        eng_min = custom.get('min', 0)
        eng_max = custom.get('max', 1)
        registry[bms_tag] = TagMeta(
            tag=bms_tag, display_name=display, description=desc,
            category='BMS', quantity='dimensionless',
            eng_min=eng_min, eng_max=eng_max,
            si_to_display=_identity, display_to_si=None,
            display_unit='', format_spec='',
        )

    return registry


# Pre-built registry (import-time)
TAG_REGISTRY = build_tag_registry()

# -- Quantity -> converter name for LiveValueItem ----------------------------
_QTY_CONVERTER = {
    'temperature': 'K_to_F',
    'pressure':    'Pa_to_inH2O',
    'feed_flow':   'kgs_to_bpd',
    'fuel_flow':   'kgs_to_mmscfd_fuel',
    'air_flow':    'kgs_to_mmscfd_air',
    'duty':        'W_to_MMBtuh',
    'mass_flow':   'kgs_to_lbh',
}

# Category display order
_CATEGORY_ORDER = [
    'Temperatures', 'Flows', 'Pressure', 'Combustion', 'Heat Duty',
    'Two-Phase', 'Pressure Drop',
    'Fuel Analysis', 'Valves',
    'BMS', 'Simulation', 'Other',
]


def build_value_presets() -> dict[str, list[tuple[str, dict]]]:
    """Build categorized LiveValueItem presets from the tag registry.

    Returns ``{category: [(menu_label, preset_dict), ...]}``.
    """
    result: dict[str, list[tuple[str, dict]]] = {}
    for tag in sorted(TAG_REGISTRY):
        meta = TAG_REGISTRY[tag]
        converter = _QTY_CONVERTER.get(meta.quantity, 'identity')
        unit = meta.display_unit
        fmt = meta.format_spec or '.1f'
        # Menu label: "FT101, Combined Crude Rate, BPD"
        parts = [meta.display_name]
        if meta.description:
            parts.append(meta.description)
        if unit:
            parts.append(unit)
        label = ', '.join(parts)
        preset = {
            "data_key": tag,
            "prefix": "",
            "fmt": fmt,
            "unit": unit,
            "converter": converter,
            "w": 100,
            "h": 14,
        }
        result.setdefault(meta.category, []).append((label, preset))

    # Sort categories by defined order
    ordered: dict[str, list[tuple[str, dict]]] = {}
    for cat in _CATEGORY_ORDER:
        if cat in result:
            ordered[cat] = result.pop(cat)
    for cat in sorted(result):
        ordered[cat] = result[cat]
    return ordered


VALUE_PRESETS_BY_CATEGORY = build_value_presets()
