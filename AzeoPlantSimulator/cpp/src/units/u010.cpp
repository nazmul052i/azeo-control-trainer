#include "azeocore/units/u010.hpp"

#include <algorithm>
#include <cmath>

namespace azeocore::units {

FuelGasHeader::FuelGasHeader(TagDatabase& db_, ProcessBus& bus_, double dt_)
    : ProcessUnit(db_, bus_, dt_, "U010", "Fuel gas header and utilities"),
      PT0101(&ai("PT-0101", "Natural gas header pressure", "barg", 0, 25)),
      PT0102(&ai("PT-0102", "Fuel gas supply to H1", "barg", 0, 15)),
      PT0103(&ai("PT-0103", "Fuel gas supply to B1", "barg", 0, 15)),
      PT0104(&ai("PT-0104", "Instrument air header pressure", "barg", 0, 10, 7.0)),
      FT0101(&ai("FT-0101", "Natural gas import flow", "Nm3/h", 0, 8000)),
      FT0102(&ai("FT-0102", "D3 off-gas to fuel header", "Nm3/h", 0, 3000)),
      TT0101(&ai("TT-0101", "Fuel gas header temperature", "degC", 0, 80, 25.0)),
      AT0101(&ai("AT-0101", "Fuel gas lower heating value", "MJ/Nm3", 18, 46, LHV_NOMINAL)),
      AT0102(&ai("AT-0102", "Fuel gas specific gravity", "-", 0.15, 1.2, 0.65)),
      TT0102(&ai("TT-0102", "Cooling water supply temperature", "degC", 0, 45, 28.0)),
      PSLL(&di("PSLL-0101", "Fuel gas header pressure low low", "Normal", "Tripped")),
      PSHH(&di("PSHH-0101", "Fuel gas header pressure high high", "Normal", "Tripped")),
      PSL_AIR(&di("PSL-0102", "Instrument air pressure low", "Normal", "Tripped")),
      PSL_CW(&di("PSL-0103", "Cooling water supply pressure low", "Normal", "Tripped")),
      LSLL_CW(&di("LSLL-0101", "CT1 basin level low low", "Normal", "Tripped")),
      LSHH_CW(&di("LSHH-0101", "CT1 basin level high high", "Normal", "Tripped")),
      PCV0101(&ao("PCV-0101", "Natural gas import control valve", "%", 0.0, 100.0, 46.0)),
      PCV0102(&ao("PCV-0102", "Fuel gas header relief to flare")),
      PCV0103(&ao("PCV-0103", "Fuel gas pressure reduction to H1", "%", 0.0, 100.0, 40.0)),
      PCV0104(&ao("PCV-0104", "Fuel gas pressure reduction to B1", "%", 0.0, 100.0, 30.0)),
      FCV0101(&ao("FCV-0101", "D3 off-gas to fuel header valve", "%", 0.0, 100.0, 60.0)),
      SC0101(&ao("SC-0101", "Cooling water circulation pump speed reference", "%", 0.0, 100.0, 100.0)),
      SC0102(&ao("SC-0102", "CT1 common fan speed reference", "%", 0.0, 100.0, 45.0)),
      LCV0101(&ao("LCV-0101", "CT1 basin makeup water valve", "%", 0.0, 100.0, 46.0)),
      FCV0102(&ao("FCV-0102", "CT1 conductivity blowdown valve", "%", 0.0, 100.0, 15.0)),
      v_import("PCV-0101", 12, ValveChar::Linear, 4),
      v_flare("PCV-0102", 20, ValveChar::Linear, 2),
      v_h1("PCV-0103", 14, ValveChar::Linear, 3),
      v_b1("PCV-0104", 8, ValveChar::Linear, 3),
      v_offgas("FCV-0101", 60, ValveChar::Linear, 3),
      v_cw_makeup("LCV-0101", 35, ValveChar::Linear, 8),
      v_cw_blowdown("FCV-0102", 30, ValveChar::Linear, 8),
      mov0101(*this, "MOV-0101", "Fuel gas header battery limit isolation", 25.0, 100.0),
      pressure(16.0 + P_STD, P_STD * 0.2, 40.0),
      b1_header(5.0 + P_STD, P_STD * 0.2, 20.0),
      lhv(30.0, LHV_NOMINAL),
      lhv_target(LHV_NOMINAL),
      lhv_import(LHV_NOMINAL),
      air_receiver(7.0, 0.0, 8.5),
      tx_pt0101("PT-0101", 0, 25, 0.4, 0.15),
      tx_ft0101("FT-0101", 0, 8000, 0.8, 0.4),
      tx_ft0102("FT-0102", 0, 3000, 0.8, 0.5),
      FT0103(&ai("FT-0103", "Cooling water supply flow", "m3/h", 0, 5000, 2600.0)),
      tx_ft0103("FT-0103", 0, 5000, 2.0, 0.4),
      TT0103(&ai("TT-0103", "Cooling water return temperature", "degC", 0, 80, 36.0)),
      PT0105(&ai("PT-0105", "Cooling water supply header pressure", "barg", 0, 8, 2.4)),
      LT0101(&ai("LT-0101", "CT1 basin level", "%", 0, 100, 65.0)),
      AT0103(&ai("AT-0103", "CT1 basin conductivity", "uS/cm", 0, 3000, 800.0)),
      TT0104(&ai("TT-0104", "Ambient wet-bulb temperature", "degC", -30, 50, 22.0)),
      TT0105(&ai("TT-0105", "Ambient dry-bulb temperature", "degC", -30, 70, 30.0)),
      FT0104(&ai("FT-0104", "CT1 makeup water flow", "m3/h", 0, 100, 24.0)),
      FT0105(&ai("FT-0105", "CT1 blowdown flow", "m3/h", 0, 100, 6.0)),
      IT0101(&ai("IT-0101", "CT1 fan A motor current", "A", 0, 300)),
      IT0102(&ai("IT-0102", "CT1 fan B motor current", "A", 0, 300)),
      IT0103(&ai("IT-0103", "P-011A cooling water pump current", "A", 0, 500)),
      IT0104(&ai("IT-0104", "P-011B cooling water pump current", "A", 0, 500)),
      cw_pumps(*this, "P-011A", "P-011B", "Cooling water circulation pump", "MOV-0111A", "MOV-0111B",
               70.0, 4025.0, 700.0, 360.0, true, 20.0, true),
      ct_fan_a(*this, "CT-011A", "CT1 cell A induced-draft fan", 180.0, true, 3.0),
      ct_fan_b(*this, "CT-011B", "CT1 cell B induced-draft fan", 180.0, true, 3.0),
      cw_supply_temp(180.0, 28.0), cw_return_temp(60.0, 36.0), cw_header_pressure(4.0, 2.4),
      cw_basin_level(65.0, 0.0, 100.0),
      cw_solids(800.0 * CW_BASIN_VOLUME * 65.0 / 100.0, 0.0, 5000.0 * CW_BASIN_VOLUME),
      xv_import(*this, "XV-0101", "Fuel gas header battery limit", 3.0) {
    for (MotorPackage* fan : {&ct_fan_a, &ct_fan_b}) {
        fan->device.running = true;
        fan->device.set_cmd_start(true);
        fan->cmd_start->value = 1.0;
        fan->device.reset_speed(45.0);
    }
    // the dynamic members, by the attribute names the Python unit has
    dyn("v_import", v_import); dyn("v_flare", v_flare); dyn("v_h1", v_h1); dyn("v_b1", v_b1);
    dyn("v_offgas", v_offgas); dyn("v_cw_makeup", v_cw_makeup); dyn("v_cw_blowdown", v_cw_blowdown);
    dyn("mov0101", mov0101); dyn("pressure", pressure); dyn("b1_header", b1_header);
    dyn("lhv", lhv); dyn("air_receiver", air_receiver); dyn("tx_pt0101", tx_pt0101); dyn("tx_ft0101", tx_ft0101);
    dyn("tx_ft0102", tx_ft0102); dyn("tx_ft0103", tx_ft0103); dyn("cw_pumps", cw_pumps);
    dyn("ct_fan_a", ct_fan_a); dyn("ct_fan_b", ct_fan_b); dyn("cw_supply_temp", cw_supply_temp);
    dyn("cw_return_temp", cw_return_temp); dyn("cw_header_pressure", cw_header_pressure);
    dyn("cw_basin_level", cw_basin_level); dyn("cw_solids", cw_solids); dyn("xv_import", xv_import);

    add_malfunction("MF-028", "U010", "Fuel gas heating value swing", "Process", "Heating value", 30.0, 45.0,
                    [this](bool active, double value) { lhv_import = active ? value : LHV_NOMINAL; });
    add_malfunction("MF-029", "U010", "Loss of natural gas import", "Process", "", 0, 1,
                    [this](bool active, double) { import_available = !active; });
    add_malfunction("MF-041", "U010", "Instrument air failure", "Utility", "", 0, 1,
                    [this](bool active, double) { air_comp_lost = active; });
    add_malfunction("MF-044", "CT1", "Cooling tower fill fouling", "Utility", "UA loss", 0, 80,
                    [this](bool active, double value) { tower_fouling_pct = active ? value : 0.0; });
    add_malfunction("MF-045", "CT1", "High ambient wet-bulb temperature", "Utility", "Wet-bulb rise", 0, 12,
                    [this](bool active, double value) { wet_bulb_offset = active ? value : 0.0; });
    add_malfunction("MF-046", "P-011A/B", "Cooling water pump wear", "Utility", "Head loss", 0, 60,
                    [this](bool active, double value) {
                        const double wear = active ? value : 0.0;
                        cw_pumps.pump_a.wear_pct = wear; cw_pumps.pump_b.wear_pct = wear;
                    });
    add_malfunction("MF-047", "CT1", "Cooling tower fan air-side restriction", "Utility", "Air capacity", 20, 100,
                    [this](bool active, double value) { fan_capacity_pct = active ? value : 100.0; });
}

void FuelGasHeader::step_cooling_water(double dt, bool air_failure) {
    v_cw_makeup.step(dt, LCV0101->effective(), air_failure);
    v_cw_blowdown.step(dt, FCV0102->effective(), air_failure);

    const double u200_flow = bus_get("u200_cw_flow");
    const double u400_flow = bus_get("u400_cw_flow");
    const double t1_flow = bus_get("t1_cw_flow");
    const double t2_flow = bus_get("t2_cw_flow");
    const double u800_flow = bus_get("u800_cw_flow");
    const double dp0 = std::max(cw_header_pressure.y, 0.0);
    const double other_flow = dp0 > 0.0 ? CW_OTHER_FLOW * std::sqrt(dp0 / 2.4) : 0.0;
    const double total_flow = clamp(u200_flow + u400_flow + t1_flow + t2_flow + u800_flow + other_flow,
                                    0.0, 5000.0);
    const double other_duty = other_flow * 997.0 * 4.18 * 6.0 / 3600.0;
    const double total_duty = clamp(bus_get("u200_cw_duty") + bus_get("u400_cw_duty")
                                    + bus_get("t1_cw_duty") + bus_get("t2_cw_duty")
                                    + bus_get("u800_cw_duty") + other_duty,
                                    0.0, 150000.0);

    const double level0 = cw_basin_level.y;
    const double pump_speed = clamp(SC0101->effective(), 0.0, 100.0);
    cw_pumps.step(dt, 0.45, total_flow, level0 > 4.0, pump_speed, 0.04, 0.05, CW_DESIGN_FLOW);
    const double discharge = cw_pumps.discharge_pressure(total_flow, 997.0);
    const double dp_target = cw_pumps.any_running() ? std::max(discharge - 0.45, 0.0) : 0.0;
    const double dp = cw_header_pressure.step(clamp(dp_target, 0.0, 8.0), dt);

    const double fan_sp = clamp(SC0102->effective(), 0.0, 100.0);
    ct_fan_a.step(dt, true, fan_sp);
    ct_fan_b.step(dt, true, fan_sp);
    for (MotorPackage* fan : {&ct_fan_a, &ct_fan_b})
        fan->device.load_frac = fan->running() ? py_pow(fan->speed() / 100.0, 3.0) : 1.0;

    const double circulating = (cw_pumps.any_running() && level0 > 0.0) ? total_flow : 0.0;
    const double supply0 = cw_supply_temp.y;
    const double delta_t = circulating > 1.0
        ? total_duty * 3600.0 / std::max(circulating * 997.0 * 4.18, 1.0) : 0.0;
    const double return_target = clamp(supply0 + delta_t, supply0, 80.0);
    const double return_temp = cw_return_temp.step(return_target, dt);

    const double wet_bulb = clamp(bus_get("ambient_wet_bulb") + wet_bulb_offset, -30.0, 50.0);
    const double dry_bulb = clamp(bus_get("ambient_dry_bulb"), wet_bulb, 70.0);
    double air_fraction = ((ct_fan_a.running() ? py_pow(ct_fan_a.speed() / 100.0, 0.8) : 0.0)
                           + (ct_fan_b.running() ? py_pow(ct_fan_b.speed() / 100.0, 0.8) : 0.0)) / 2.0;
    air_fraction *= fan_capacity_pct / 100.0;
    const double water_fraction = circulating / CW_DESIGN_FLOW;
    double cold_target;
    if (circulating > 1.0 && air_fraction > 1e-4) {
        const double lg_relative = water_fraction / air_fraction;
        const double ntu = CW_TOWER_KAV_L * (1.0 - clamp(tower_fouling_pct, 0.0, 95.0) / 100.0)
                           * py_pow(std::max(lg_relative, 0.05), -0.6);
        cold_target = wet_bulb + std::max(return_temp - wet_bulb, 0.0) * py_exp(-ntu);
    } else {
        cold_target = return_temp;
    }
    const double supply = cw_supply_temp.step(clamp(cold_target, wet_bulb, 80.0), dt);

    const double tower_range = std::max(return_temp - supply, 0.0);
    const double evaporation = 0.00085 * circulating * tower_range;
    const double drift = 0.0002 * circulating;
    const double makeup = v_cw_makeup.flow(3.0, 1.0);
    const double blowdown = cw_pumps.any_running() ? v_cw_blowdown.flow(2.4, 1.0) : 0.0;
    const double level_rate = (makeup - blowdown - evaporation - drift)
                              / CW_BASIN_VOLUME * 100.0 / 3600.0;
    const double level = cw_basin_level.step(level_rate, dt);
    const double volume = std::max(CW_BASIN_VOLUME * level / 100.0, 1.0);
    double conductivity = clamp(cw_solids.y / volume, 0.0, 5000.0);
    const double solids_rate = (makeup * 200.0 - (blowdown + drift) * conductivity) / 3600.0;
    cw_solids.step(solids_rate, dt);
    conductivity = clamp(cw_solids.y / volume, 0.0, 5000.0);

    bus_set("cooling_water_temperature", supply);
    bus_set("cooling_water_dp_bar", dp);
    TT0102->set(supply); TT0103->set(return_temp); PT0105->set(dp); LT0101->set(level);
    AT0103->set(conductivity); TT0104->set(wet_bulb); TT0105->set(dry_bulb);
    { const double v_ = tx_ft0103.step(dt, circulating); FT0103->set(v_, tx_ft0103.quality); }
    FT0104->set(makeup); FT0105->set(blowdown);
    IT0101->set(ct_fan_a.current()); IT0102->set(ct_fan_b.current());
    IT0103->set(cw_pumps.motor_a.current()); IT0104->set(cw_pumps.motor_b.current());
    PSL_CW->set(dp < 1.2 ? 1.0 : 0.0);
    LSLL_CW->set(level < 10.0 ? 1.0 : 0.0);
    LSHH_CW->set(level > 92.0 ? 1.0 : 0.0);
}

void FuelGasHeader::step(double dt) {
    const double rate = air_comp_lost ? -0.055 : 0.30;
    double air_p = air_receiver.step((std::fabs(air_receiver.y - 7.0) > 1e-6 || air_comp_lost) ? rate : 0.0, dt);
    air_p = std::min(air_p, 7.0);
    if (!air_comp_lost && air_receiver.y > 7.0) air_receiver.reset(7.0);
    air_failed = air_p < 3.0;
    const bool air = air_failed;
    PT0104->set(air_p);
    PSL_AIR->set(air_p < 5.5 ? 1.0 : 0.0);

    step_cooling_water(dt, air);

    const double p_abs = pressure.y;
    const double b1_abs = b1_header.y;
    v_import.step(dt, PCV0101->effective(), air);
    v_flare.step(dt, PCV0102->effective(), air);
    v_h1.step(dt, PCV0103->effective(), air);
    v_b1.step(dt, PCV0104->effective(), air);
    v_offgas.step(dt, FCV0101->effective(), air);
    const double sg = AT0102->value;
    const double t_k = TT0101->value + 273.15;
    mov0101.step(dt);
    xv_import.step(dt);

    const double f_import = import_available
        ? v_import.gas_flow(SUPPLY_PRESSURE, p_abs, sg, t_k) * mov0101.fraction() * xv_import.position / 100.0
        : 0.0;
    const double f_flare = v_flare.gas_flow(p_abs, P_STD, sg, t_k);
    const double offgas_available = bus_get("d3_offgas_available");
    const double f_offgas = std::min(offgas_available, v_offgas.gas_flow(p_abs + 1.5, p_abs, sg, t_k));
    const double h1_burner_abs = bus_get("h1_burner_pressure_bara");
    const double f_to_h1 = v_h1.gas_flow(p_abs, h1_burner_abs, sg, t_k);
    const double f_to_b1 = v_b1.gas_flow(p_abs, b1_abs, sg, t_k);
    const double b1_demand = bus_get("b1_fuel_demand");
    const double d_b1 = (f_to_b1 - b1_demand) * P_STD / (3600.0 * 25.0);
    b1_header.step(d_b1, dt);
    const double net = f_import + f_offgas - f_flare - f_to_h1 - f_to_b1;
    pressure.step(net * P_STD / (3600.0 * HEADER_VOLUME), dt);
    // the calculation trace: the header's gas balance and its valves
    tr("f_import", f_import); tr("f_offgas", f_offgas); tr("f_flare", f_flare);
    tr("f_to_h1", f_to_h1); tr("f_to_b1", f_to_b1); tr("b1_demand", b1_demand); tr("net", net);
    tr("p_abs", pressure.y); tr("b1_abs", b1_header.y); tr("air_p", air_p);
    tr("PCV-0101", v_import.position); tr("PCV-0102", v_flare.position); tr("PCV-0103", v_h1.position);
    tr("PCV-0104", v_b1.position); tr("FCV-0101", v_offgas.position); tr("XV-0101", xv_import.position);
    bus_set("fg_header_pressure_bara", pressure.y);
    bus_set("b1_supply_pressure_bara", b1_header.y);
    bus_set("fg_to_h1_flow", f_to_h1);
    bus_set("fg_lhv", lhv.step(lhv_target, dt));

    const double p_barg = pressure.y - P_STD;
    { const double v_ = tx_pt0101.step(dt, p_barg); PT0101->set(v_, tx_pt0101.quality); }
    PT0102->set(clamp(h1_burner_abs - P_STD, 0.0, 15.0));
    PT0103->set(clamp(b1_header.y - P_STD, 0.0, 15.0));
    { const double v_ = tx_ft0101.step(dt, f_import); FT0101->set(v_, tx_ft0101.quality); }
    { const double v_ = tx_ft0102.step(dt, f_offgas); FT0102->set(v_, tx_ft0102.quality); }
    AT0101->set(lhv.y);
    TT0101->set(clamp(25.0 - 0.004 * f_import, 5.0, 60.0));
    const double mw_hc = clamp(18.0 - 0.06 * bus_get("r1_conversion"), 4.0, 40.0);
    const double co2 = 0.08;
    const double mw_off = mw_hc * (1.0 - co2) + 44.0 * co2;
    const double lhv_off = (10.8 + (mw_hc - 2.0) * 25.0 / 14.0) * (1.0 - co2);
    const double tot = std::max(f_offgas + f_import, 1.0);
    const double sg_blend = (f_offgas * mw_off / 28.96 + f_import * 0.61) / tot;
    AT0102->set(clamp(sg_blend, 0.15, 1.2));
    lhv_target = clamp((f_offgas * lhv_off + f_import * lhv_import) / tot, 18.0, 46.0);
    PSLL->set(p_barg < 5.0 ? 1.0 : 0.0);
    PSHH->set(p_barg > 22.0 ? 1.0 : 0.0);
}

Value FuelGasHeader::save_state() const {
    Dict d;
    d["p"] = pressure.y; d["b1"] = b1_header.y; d["lhv"] = lhv.y; d["lhvi"] = lhv_import; d["lhvt"] = lhv_target;
    d["ct_foul"] = tower_fouling_pct; d["wb_off"] = wet_bulb_offset; d["fan_cap"] = fan_capacity_pct;
    return d;
}

void FuelGasHeader::load_state(const Value& s) {
    pressure.reset(s.get_number("p", 17.0));
    b1_header.reset(s.get_number("b1", 6.0));
    lhv.reset(s.get_number("lhv", LHV_NOMINAL));
    lhv_import = s.get_number("lhvi", LHV_NOMINAL);
    lhv_target = s.get_number("lhvt", LHV_NOMINAL);
    tower_fouling_pct = s.get_number("ct_foul", 0.0);
    wet_bulb_offset = s.get_number("wb_off", 0.0);
    fan_capacity_pct = s.get_number("fan_cap", 100.0);
}

std::vector<ParameterSpec> FuelGasHeader::parameters() const {
    return {{"CW_BASIN_VOLUME", CW_BASIN_VOLUME, "m3", "CT1 basin volume at 100 percent", 50.0, 5000.0, true},
            {"CW_DESIGN_FLOW", CW_DESIGN_FLOW, "m3/h", "CT1 design circulating-water flow", 500.0, 10000.0, true},
            {"CW_OTHER_FLOW", CW_OTHER_FLOW, "m3/h", "Minor cooling-water users at design pressure", 0.0, 5000.0, true},
            {"CW_TOWER_KAV_L", CW_TOWER_KAV_L, "-", "Clean CT1 tower characteristic", 0.1, 5.0, true},
            {"HEADER_VOLUME", HEADER_VOLUME, "m3", "Fuel-gas header gas volume", 1.0, 1000.0, true},
            {"LHV_NOMINAL", LHV_NOMINAL, "MJ/Nm3", "Nominal fuel-gas lower heating value", 1.0, 80.0, true}};
}

}  // namespace azeocore::units
