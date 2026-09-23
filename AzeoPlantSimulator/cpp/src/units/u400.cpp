#include "azeocore/units/u400.hpp"

#include <algorithm>
#include <cmath>

namespace azeocore::units {

namespace {
constexpr double P_STD = 1.01325;
constexpr double R_GAS = 8.314;
inline double b2d(bool b) { return b ? 1.0 : 0.0; }
}  // namespace

ReactorSection::ReactorSection(TagDatabase& db_, ProcessBus& bus_, double dt_)
    : ProcessUnit(db_, bus_, dt_, "U400", "Reactor R1 and separator D3"),
      quench("FCV-4001", 360, ValveChar::Linear, 3, false),
      cooler("FCV-4002", 300, ValveChar::Linear, 8, false),
      v_add("FCV-4003", 0.06, ValveChar::Linear, 4),
      add_lag(240.0, 25.0 / 90.0), fresh_lag(900.0, 0.12), add_dead(360.0, dt_, 25.0 / 90.0),
      lcv4001("LCV-4001", 260, ValveChar::EqualPercent, 5),
      lcv4002("LCV-4002", 3.5, ValveChar::Linear, 5),
      pcv4001("PCV-4001", 8, ValveChar::Linear, 3),
      bed1(120.0, 0.0, 620.0), bed2(118.0, 0.0, 620.0),
      pressure(42.0 + P_STD, P_STD * 0.2, 62.0), d3_pressure(39.0 + P_STD, P_STD * 0.2, 58.0),
      d3_level(50.0, 0.0, 100.0), d3_interface(30.0, 0.0, 100.0),
      d3_temp(120.0, 55.0), impurity_dead(180.0, dt_, 90.0), conversion(30.0, 0.0),
      tx_bed1("TT-4002", 0, 550, 8.0, 0.1),
      tx_impurity("AT-4001", 0, 500, 20.0, 0.2, 420.0, 30.0),
      tx_interface("LT-4002", 0, 100, 3.0, 0.25),
      tx_level("LT-4001", 0, 100, 1.5, 0.4) {
    TT4001 = &ai("TT-4001", "R1 inlet temperature", "degC", 0, 500, 107.0);
    TT4002 = &ai("TT-4002", "R1 bed 1 temperature", "degC", 0, 550, 120.0);
    TT4003 = &ai("TT-4003", "R1 bed 2 temperature", "degC", 0, 550, 118.0);
    TT4004 = &ai("TT-4004", "R1 outlet temperature", "degC", 0, 550, 118.0);
    TT4005 = &ai("TT-4005", "D3 separator temperature", "degC", 0, 250, 55.0);
    PT4001 = &ai("PT-4001", "R1 inlet pressure", "barg", 0, 60, 42.0);
    PT4002 = &ai("PT-4002", "D3 separator pressure", "barg", 0, 55, 39.0);
    PDT4001 = &ai("PDT-4001", "R1 catalyst bed differential pressure", "bar", 0, 5, 0.9);
    FT4001 = &ai("FT-4001", "R1 quench gas flow", "kNm3/h", 0, 40);
    FT4002 = &ai("FT-4002", "D3 off-gas flow", "Nm3/h", 0, 3000);
    FT4003 = &ai("FT-4003", "D3 liquid to T1", "m3/h", 0, 200);
    FT4004 = &ai("FT-4004", "D3 sour water draw", "m3/h", 0, 20);
    FT4005 = &ai("FT-4005", "R1 additive injection flow", "L/h", 0, 60, 25.0);
    LT4001 = &ai("LT-4001", "D3 separator liquid level", "%", 0, 100, 50.0);
    LT4002 = &ai("LT-4002", "D3 separator interface level", "%", 0, 100, 30.0);
    AT4001 = &ai("AT-4001", "R1 product impurity", "ppm", 0, 500, 90.0);
    AT4002 = &ai("AT-4002", "D3 off-gas hydrogen sulphide", "mol%", 0, 5, 1.2);
    XY4010 = &ai("XI-4001", "R1 conversion", "%", 0, 100, 0.0);
    ao("FCV-4001", "R1 quench gas control valve", "%", 0.0, 100.0, 35.0);
    ao("FCV-4002", "R1 effluent cooler cooling water valve", "%", 0.0, 100.0, 60.0);
    ao("LCV-4001", "D3 liquid level valve to T1", "%", 0.0, 100.0, 45.0);
    ao("LCV-4002", "D3 interface sour water valve", "%", 0.0, 100.0, 15.0);
    ao("PCV-4001", "D3 separator pressure control valve", "%", 0.0, 100.0, 20.0);
    ao("FCV-4003", "R1 additive injection valve", "%", 0.0, 100.0, 42.0);
    di("PSHH-4001", "R1 pressure high high", "Normal", "Tripped");
    // one switch per bed for the SIS to vote, and the voted result the P&ID names
    di("TSHH-4001A", "R1 bed 1 temperature high high", "Normal", "Tripped");
    di("TSHH-4001B", "R1 bed 2 temperature high high", "Normal", "Tripped");
    di("TSHH-4001", "R1 catalyst bed temperature high high", "Normal", "Tripped");
    di("LSLL-4001", "D3 separator level low low", "Normal", "Tripped");
    di("LSHH-4001", "D3 separator level high high", "Normal", "Tripped");
    xv4001 = std::make_unique<SdvPackage>(*this, "XV-4001", "R1 feed shutdown valve", 3.0);
    xv4002 = std::make_unique<SdvPackage>(*this, "XV-4002", "D3 off-gas shutdown valve", 2.0);
    bdv4001 = std::make_unique<SdvPackage>(*this, "BDV-4001", "R1 emergency depressuring valve", 2.0, true, false);

    dyn("xv4001", *xv4001); dyn("xv4002", *xv4002); dyn("bdv4001", *bdv4001);
    dyn("quench", quench); dyn("cooler", cooler); dyn("v_add", v_add); dyn("add_lag", add_lag); dyn("fresh_lag", fresh_lag); dyn("add_dead", add_dead);
    dyn("lcv4001", lcv4001); dyn("lcv4002", lcv4002); dyn("pcv4001", pcv4001);
    dyn("bed1", bed1); dyn("bed2", bed2); dyn("pressure", pressure); dyn("d3_pressure", d3_pressure);
    dyn("d3_level", d3_level); dyn("d3_interface", d3_interface); dyn("d3_temp", d3_temp);
    dyn("impurity_dead", impurity_dead); dyn("conversion", conversion);
    dyn("tx_bed1", tx_bed1); dyn("tx_impurity", tx_impurity); dyn("tx_interface", tx_interface); dyn("tx_level", tx_level);

    add_malfunction("MF-017", "LT-4002", "Interface level measurement noise", "Transmitter", "Sigma", 0, 15,
                    [this](bool a, double v) { tx_interface.noise_sigma_pct = a ? v : 0.25; });
    add_malfunction("MF-020", "AT-4001", "Analyser sample line plugged", "Analyser", "", 0, 1,
                    [this](bool a, double) { tx_impurity.failure = a ? TxFailure::Frozen : TxFailure::None; });
    add_malfunction("MF-032", "R1", "Catalyst deactivation", "Process", "Activity", 40, 100,
                    [this](bool a, double v) { activity = a ? v : 100.0; });
    add_malfunction("MF-033", "R1", "Reaction runaway on quench loss", "Process", "", 0, 1,
                    [this](bool a, double) { quench_failed = a; });
    add_malfunction("MF-034", "R1", "Catalyst bed fouling", "Process", "dP increase", 0, 60,
                    [this](bool a, double v) { fouled_pct = a ? v : 0.0; });
}

double ReactorSection::conversion_of(double temp_c, double volumetric_flow_m3s) const {
    const double t_k = clamp(temp_c + 273.15, 250.0, 900.0);
    const double k = K0 * py_exp(-EA / (R_GAS * t_k)) * (activity / 100.0);
    const double tau = safe_div(BED_VOLUME, std::max(volumetric_flow_m3s, 1e-5), 0.0);
    return clamp(1.0 - py_exp(-clamp(k * tau, 0.0, 30.0)), 0.0, 0.995);
}

void ReactorSection::step(double dt) {
    const bool air = bus_get("air_failure") != 0.0;
    const bool esd = bus_get("esd_u400") != 0.0;
    const bool depressure = bus_get("esd_depressure") != 0.0;
    quench.step(dt, t("FCV-4001").effective(), air);
    cooler.step(dt, t("FCV-4002").effective(), air);
    v_add.step(dt, t("FCV-4003").effective(), air);
    lcv4001.step(dt, t("LCV-4001").effective(), air);
    lcv4002.step(dt, t("LCV-4002").effective(), air);
    pcv4001.step(dt, t("PCV-4001").effective(), air);
    xv4001->step(dt, esd, air);
    xv4002->step(dt, esd, air);
    bdv4001->step(dt, depressure, air);
    const double feed = bus_get("charge_flow") * xv4001->fraction();
    const double t_in = bus_get("h1_outlet_temperature");
    TT4001->set(t_in);
    const double gas_in = bus_get("recycle_gas_to_h1") / 1000.0;
    const double h2_avail = py_pow(clamp(safe_div(gas_in, std::max(feed, 1.0) * GAS_TO_OIL, 1.0), 0.35, 1.0), 0.5);
    const double mass_kg_s = feed * 780.0 / 3600.0;
    const double cp = 2.35;
    const double gas_available = bus_get("c1_discharge_pressure");
    double quench_flow = 0.0;
    if (!quench_failed)
        quench_flow = quench.gas_flow(std::max(gas_available, pressure.y + 1.0), pressure.y, 0.5, 320.0) / 1000.0;
    quench_flow = clamp(quench_flow, 0.0, 40.0);
    bus_set("quench_and_makeup_demand", quench_flow * 1000.0);
    const double q_m3s = std::max(feed, 0.1) / 3600.0;
    const double reactive = mass_kg_s * REACTIVE_FRACTION;
    const double conv1 = conversion_of(bed1.y, q_m3s) * h2_avail;
    const double heat1 = reactive * conv1 * DH_RXN;
    const double d_t1 = (mass_kg_s * cp * (t_in - bed1.y) + heat1) / BED_MASS_CP;
    bed1.step(d_t1, dt);
    const double quench_duty = quench_flow * 1000.0 / 3600.0 * 1.3 * 0.35;
    double t_mid = bed1.y;
    if (mass_kg_s > 0.5) t_mid = clamp(bed1.y - safe_div(quench_duty, mass_kg_s * cp, 0.0), 0.0, 620.0);
    const double remaining = reactive * (1.0 - conv1);
    const double conv2 = conversion_of(bed2.y, q_m3s) * h2_avail;
    const double heat2 = remaining * conv2 * DH_RXN;
    const double d_t2 = (mass_kg_s * cp * (t_mid - bed2.y) + heat2) / BED_MASS_CP;
    bed2.step(d_t2, dt);
    const double hot = std::max(std::max(bed1.y, bed2.y) - 400.0, 0.0);
    activity = clamp(activity - (0.02 + 0.5 * hot / 50.0) / 3600.0 * dt, 20.0, 100.0);
    const double overall = conv1 + (1.0 - conv1) * conv2;
    const double conv_pct = clamp(overall * 100.0, 0.0, 99.5);
    conversion.step(conv_pct, dt);
    const double impurity_true = clamp(500.0 * py_exp(-0.055 * conversion.y), 1.0, 500.0);
    const double make_up = feed * 0.9;
    const double relief = 4200.0 * bdv4001->fraction();
    const double anchor = d3_pressure.y + PDT4001->value + 1.8;
    double d_p = ((make_up * 8.0 + quench_flow * 1000.0 - relief - bus_get("d3_offgas_total")) * P_STD / (3600.0 * 120.0));
    d_p += (anchor - pressure.y) / 60.0;
    pressure.step(d_p, dt);
    PDT4001->set(clamp(0.9 * (1.0 + fouled_pct / 100.0) * (1.0 + py_pow(feed / 130.0, 2.0)) / 2.0, 0.0, 5.0));
    const double t_effluent = bed2.y;
    bus_set("r1_effluent_temperature", t_effluent);
    const double cw_dp = bus_get("cooling_water_dp_bar");
    const double cw_supply = bus_get("cooling_water_temperature");
    const double cw_flow = cooler.flow(cw_dp, 1.0);
    const double cw_design = 0.865 * cooler.cv_rated * std::sqrt(2.4);
    const double cooling = clamp(cw_flow / std::max(cw_design, 1.0), 0.0, 1.5);
    const double cool_target = clamp(
        t_effluent - cooling * 0.82 * (t_effluent - (cw_supply + 12.0)), 20.0, 195.0);
    d3_temp.step(cool_target, dt);
    const double cw_duty = clamp(mass_kg_s * cp * std::max(t_effluent - cool_target, 0.0), 0.0, 100000.0);
    bus_set("u400_cw_flow", cw_flow);
    bus_set("u400_cw_duty", cw_duty);
    const double vap_shift = clamp((d3_temp.y - 195.0) / 100.0, -0.15, 0.15);
    double offgas_total = clamp((240.0 + 7.4 * feed + quench_flow * 40.0) * (1.0 + 1.8 * vap_shift), 0.0, 3000.0);
    offgas_total *= xv4002->fraction();
    const double to_fuel = offgas_total * 0.62;
    const double h2_consumed = H2_CONS_K * feed * overall;
    const double through_gas = std::max(gas_in - h2_consumed, 0.0) * 1000.0;
    const double to_c1 = (offgas_total - to_fuel) + quench_flow * 1000.0 + through_gas;
    bus_set("d3_offgas_available", to_fuel);
    bus_set("d3_offgas_to_c1", to_c1);
    bus_set("d3_offgas_total", offgas_total + through_gas);
    bus_set("r1_h2_consumption", h2_consumed * 1000.0);
    const double liquid_in = feed * (clamp(0.86 - 0.10 * vap_shift, 0.5, 1.0) - D3_WATER_FRAC);
    const double liquid_out = lcv4001.flow(std::max(d3_pressure.y - bus_get("t1_pressure_bara"), 0.0), 0.76);
    const double water_in = feed * D3_WATER_FRAC;
    const double water_out = lcv4002.flow(std::max(d3_pressure.y - P_STD, 0.0), 1.0);
    const double liquid_before = d3_level.y;
    const double water_before = d3_interface.y;
    d3_level.step((liquid_in - liquid_out) / (D3_AREA * 4.6) * 100.0 / 3600.0, dt);
    d3_interface.step((water_in - water_out) / (D3_AREA * 1.2) * 100.0 / 3600.0, dt);
    record_inventory_balance("D3 hydrocarbon inventory", liquid_in, liquid_out, liquid_before, d3_level.y, D3_AREA * 4.6, dt);
    record_inventory_balance("D3 water inventory", water_in, water_out, water_before, d3_interface.y, D3_AREA * 1.2, dt);
    const double vent = pcv4001.gas_flow(d3_pressure.y, P_STD, 0.6, 330.0);
    const double d_pd3 = ((offgas_total * 0.35 - vent) * P_STD / (3600.0 * D3_VOLUME));
    d3_pressure.step(d_pd3, dt);
    const double additive = v_add.flow(4.0, 1.0) * 1000.0;
    FT4005->set(clamp(additive, 0.0, 60.0));
    const double r_eff = add_lag.step(add_dead.step(additive / std::max(feed, 1.0)), dt);
    const double fresh_z = fresh_lag.step((bus.has("fresh_lightfrac") ? bus.read("fresh_lightfrac") : 0.12), dt);
    bus_set("d3_liquid_lightfrac", clamp(0.34 + fresh_z + 0.5 * (r_eff - 25.0 / 90.0), 0.40, 0.52));
    bus_set("d3_liquid_to_t1", liquid_out);
    bus_set("d3_liquid_temperature", d3_temp.y);
    bus_set("r1_bed_temperature", bed2.y);
    bus_set("r1_conversion", conversion.y);
    { const double v_ = tx_bed1.step(dt, bed1.y); TT4002->set(v_, tx_bed1.quality); }
    TT4003->set(bed2.y);
    TT4004->set(bed2.y);
    TT4005->set(d3_temp.y);
    PT4001->set(clamp(pressure.y - P_STD, 0.0, 60.0));
    PT4002->set(clamp(d3_pressure.y - P_STD, 0.0, 55.0));
    FT4001->set(quench_flow);
    FT4002->set(to_fuel);
    FT4003->set(liquid_out);
    FT4004->set(water_out);
    { const double v_ = tx_level.step(dt, d3_level.y); LT4001->set(v_, tx_level.quality); }
    { const double v_ = tx_interface.step(dt, d3_interface.y); LT4002->set(v_, tx_interface.quality); }
    { const double v_ = tx_impurity.step(dt, impurity_dead.step(impurity_true)); AT4001->set(v_, tx_impurity.quality); }
    AT4002->set(clamp(0.6 + conversion.y * 0.02, 0.0, 5.0));
    XY4010->set(conversion.y);
    t("TSHH-4001A").set(b2d(bed1.y > 470.0));
    t("TSHH-4001B").set(b2d(bed2.y > 470.0));
    t("TSHH-4001").set(b2d(std::max(bed1.y, bed2.y) > 470.0));
    t("PSHH-4001").set(b2d(pressure.y - P_STD > 54.0));
    t("LSLL-4001").set(b2d(d3_level.y < 12.0));
    t("LSHH-4001").set(b2d(d3_level.y > 88.0));

    // the calculation trace: the reactor's energy balance and D3's material balance
    tr("feed", feed); tr("t_in", t_in); tr("gas_in", gas_in); tr("h2_avail", h2_avail); tr("quench_flow", quench_flow);
    tr("conv1", conv1); tr("conv2", conv2); tr("overall", overall); tr("heat1", heat1); tr("heat2", heat2);
    tr("bed1", bed1.y); tr("bed2", bed2.y); tr("t_mid", t_mid); tr("activity", activity); tr("pressure", pressure.y);
    tr("offgas_total", offgas_total); tr("to_fuel", to_fuel); tr("to_c1", to_c1); tr("h2_consumed", h2_consumed);
    tr("liquid_in", liquid_in); tr("liquid_out", liquid_out); tr("water_in", water_in); tr("water_out", water_out);
    tr("d3_level", d3_level.y); tr("d3_interface", d3_interface.y); tr("d3_pressure", d3_pressure.y); tr("vent", vent);
    tr("additive", additive); tr("r_eff", r_eff); tr("impurity_true", impurity_true);
}

Value ReactorSection::save_state() const {
    Dict d;
    d["b1"] = bed1.y; d["b2"] = bed2.y; d["p"] = pressure.y; d["pd3"] = d3_pressure.y;
    d["l"] = d3_level.y; d["i"] = d3_interface.y; d["act"] = activity; d["addl"] = add_lag.y; d["freshz"] = fresh_lag.y;
    return d;
}

void ReactorSection::apply(const Value& state) {
    ProcessUnit::apply(state);
    pressure.reset(std::min(pressure.y, 54.0));
}

void ReactorSection::load_state(const Value& s) {
    bed1.reset(s.get_number("b1", 120.0));
    bed2.reset(s.get_number("b2", 118.0));
    pressure.reset(std::min(s.get_number("p", 43.0), 54.0));
    d3_pressure.reset(s.get_number("pd3", 40.0));
    d3_level.reset(s.get_number("l", 50.0));
    d3_interface.reset(s.get_number("i", 30.0));
    activity = s.get_number("act", 100.0);
    const double r = s.get_number("addl", 25.0 / 90.0);
    add_lag.reset(r); fresh_lag.reset(s.get_number("freshz", 0.12));
    add_dead.reset(r);
}

std::vector<ParameterSpec> ReactorSection::parameters() const {
    return {{"BED_MASS_CP", BED_MASS_CP, "kJ/K", "Thermal capacitance per R1 bed", 1.0, 1e9, true},
            {"BED_VOLUME", BED_VOLUME, "m3", "Catalyst volume per R1 bed", 0.1, 10000.0, true},
            {"D3_AREA", D3_AREA, "m2", "D3 liquid cross-sectional area", 0.1, 10000.0, true},
            {"D3_VOLUME", D3_VOLUME, "m3", "D3 vapour-space volume", 0.1, 10000.0, true},
            {"D3_WATER_FRAC", D3_WATER_FRAC, "fraction", "Water fraction of the charge that settles to the D3 interface", 0.0, 0.05, true},
            {"DH_RXN", DH_RXN, "kJ/kg", "Reaction heat release per converted mass", 0.0, 1e6, true},
            {"EA", EA, "J/mol", "Lumped reaction activation energy", 0.0, 1e7, true},
            {"GAS_TO_OIL", GAS_TO_OIL, "kNm3/m3", "Design recycle-gas to charge ratio", 0.0, 100.0, true},
            {"H2_CONS_K", H2_CONS_K, "kNm3/m3", "Hydrogen consumption at full conversion", 0.0, 100.0, true},
            {"K0", K0, "1/s", "Lumped Arrhenius pre-exponential factor", 0.0, 1e12, true},
            {"REACTIVE_FRACTION", REACTIVE_FRACTION, "fraction", "Reactive fraction of liquid feed", 0.0, 1.0, true}};
}

}  // namespace azeocore::units
