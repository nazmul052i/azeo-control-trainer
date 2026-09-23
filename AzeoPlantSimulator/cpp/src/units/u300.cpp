#include "azeocore/units/u300.hpp"

#include <algorithm>
#include <cmath>

namespace azeocore::units {

namespace {
constexpr double P_STD = 1.01325;
constexpr double CP_CHARGE = 2.35;     // kJ/kg.K
constexpr double STOICH_AIR = 9.6;     // Nm3 air per Nm3 fuel gas
inline double b2d(bool b) { return b ? 1.0 : 0.0; }
}  // namespace

FiredHeater::FiredHeater(TagDatabase& db_, ProcessBus& bus_, double dt_)
    : ProcessUnit(db_, bus_, dt_, "U300", "Fired heater H1"),
      fcv3001("FCV-3001", 45, ValveChar::Linear, 3),
      fcv3002("FCV-3002", 30, ValveChar::EqualPercent, 4),
      fcv3003("FCV-3003", 30, ValveChar::EqualPercent, 4),
      damper("FCV-3004", 1000, ValveChar::Linear, 12, false),
      pass1("FCV-3005", 100, ValveChar::Linear, 8, false),
      pass2("FCV-3006", 100, ValveChar::Linear, 8, false),
      coke(0.0, 0.0, 60.0), e5_byp(8.0, 50.0), ay_lag(120.0, 38.5),
      burner_pressure(3.0 + P_STD, P_STD * 0.2, 12.0),
      duty(8.0, 0.0), t_out(TAU_OUTLET, 62.0), t_dead(DEADTIME_OUTLET, dt_, 62.0),
      t_pass1(TAU_OUTLET * 0.9, 62.0), t_pass2(TAU_OUTLET * 1.1, 62.0),
      skin1(45.0, 90.0), skin2(45.0, 90.0), o2(6.0, 3.0), co(4.0, 20.0), stack(60.0, 120.0),
      oil_pressure(20.0, 8.0),
      tx_tout("TT-3001", 0, 500, 2.0, 0.12), tx_o2("AT-3001", 0, 21, 12.0, 0.8),
      tx_fuel("FT-3001", 0, 2500, 0.7, 0.5), tx_skin("TT-3005", 0, 750, 6.0, 0.15) {
    TT3001 = &ai("TT-3001", "H1 combined outlet temperature", "degC", 0, 500, 62.0);
    TT3002 = &ai("TT-3002", "H1 pass 1 outlet temperature", "degC", 0, 500, 62.0);
    TT3003 = &ai("TT-3003", "H1 pass 2 outlet temperature", "degC", 0, 500, 62.0);
    TT3004 = &ai("TT-3004", "H1 stack gas temperature", "degC", 0, 600, 120.0);
    TT3005 = &ai("TT-3005", "H1 pass 1 tube skin temperature", "degC", 0, 750, 90.0);
    TT3006 = &ai("TT-3006", "H1 pass 2 tube skin temperature", "degC", 0, 750, 90.0);
    FT3001 = &ai("FT-3001", "H1 fuel gas flow", "Nm3/h", 0, 3000);
    FT3002 = &ai("FT-3002", "H1 fuel oil flow", "kg/h", 0, 1500);
    FT3003 = &ai("FT-3003", "H1 combustion air flow", "kNm3/h", 0, 35);
    FT3004 = &ai("FT-3004", "H1 pass 1 charge flow", "m3/h", 0, 200);
    FT3005 = &ai("FT-3005", "H1 pass 2 charge flow", "m3/h", 0, 200);
    PT3001 = &ai("PT-3001", "H1 fuel gas burner pressure", "barg", 0, 10, 3.0);
    PT3002 = &ai("PT-3002", "H1 fuel oil header pressure", "barg", 0, 25, 8.0);
    PT3003 = &ai("PT-3003", "H1 firebox draft", "mmH2O", -20, 10, -4.0);
    AT3001 = &ai("AT-3001", "H1 flue gas oxygen", "mol%", 0, 21, 3.0);
    AT3002 = &ai("AT-3002", "H1 flue gas carbon monoxide", "ppm", 0, 2000, 20.0);
    IT3001 = &ai("IT-3001", "H1 induced draft fan motor current", "A", 0, 250);
    TT3007 = &ai("TT-3007", "E5 charge outlet temperature", "degC", 0, 300, 62.0);
    AY3001 = &ai("AY-3001", "Fuel gas heating value, inferred", "MJ/Nm3", 20, 50, 38.5);
    ZT3001 = &ai("ZT-3001", "FCV-3001 position feedback", "%", 0, 100);
    ZT3002 = &ai("ZT-3002", "FCV-3002 position feedback", "%", 0, 100);
    ZT3003 = &ai("ZT-3003", "FCV-3003 position feedback", "%", 0, 100);
    di("XS-ID301-AVL", "ID-301 available and in remote", "Local", "Remote", true);
    di("XS-ID301-VFD", "ID-301 VFD healthy", "Faulted", "Healthy", true);
    ao("FCV-3001", "H1 fuel gas control valve", "%", 0.0, 100.0, 72.0);
    ao("FCV-3002", "H1 fuel oil valve A (split range 0-50%)");
    ao("FCV-3003", "H1 fuel oil valve B (split range 50-100%)");
    ao("FCV-3004", "H1 combustion air damper", "%", 0.0, 100.0, 80.0);
    ao("TCV-3002", "E5 charge bypass valve", "%", 0.0, 100.0, 50.0);
    ao("FCV-3005", "H1 pass 1 balancing valve", "%", 0.0, 100.0, 50.0);
    ao("FCV-3006", "H1 pass 2 balancing valve", "%", 0.0, 100.0, 50.0);
    ao("SC-3001", "H1 induced draft fan VFD speed reference", "%", 0.0, 100.0, 85.0);
    di("BS-3001", "H1 main burner flame detected", "No flame", "Flame");
    di("BS-3002", "H1 pilot flame detected", "No flame", "Flame");
    di("PSLL-3001", "H1 fuel gas pressure low low", "Normal", "Tripped");
    di("PSHH-3001", "H1 fuel gas pressure high high", "Normal", "Tripped");
    di("XS-3010", "H1 furnace purge complete permissive", "Not purged", "Purged");
    di("XS-ID301-RUN", "ID-301 induced draft fan running", "Stopped", "Running", true);
    di("XS-ID301-FLT", "ID-301 fault or trip", "Healthy", "Faulted");
    di("ZSO-XV3001", "XV-3001 fuel gas SDV open limit", "Not open", "Open", true);
    di("ZSC-XV3001", "XV-3001 fuel gas SDV closed limit", "Not closed", "Closed");
    di("ZSO-HV3001", "HV-3001 fuel oil manual isolation open limit", "Not open", "Open");
    di("ZSC-HV3001", "HV-3001 fuel oil manual isolation closed limit", "Not closed", "Closed", true);
    do_("XY-XV3001-OPN", "XV-3001 fuel gas SDV open command", "Close", "Open", true);
    do_("XY-3010", "H1 burner igniter energise command", "De-energise", "Energise");
    do_("XY-3011", "H1 furnace purge sequence start command", "Idle", "Start");
    do_("XY-ID301-STR", "ID-301 start command", "Idle", "Start", true);
    do_("XY-ID301-STP", "ID-301 stop command", "Idle", "Stop");
    xv_oil = std::make_unique<SdvPackage>(*this, "XV-3002", "H1 fuel oil shutdown valve", 3.0);

    dyn("fcv3001", fcv3001); dyn("fcv3002", fcv3002); dyn("fcv3003", fcv3003); dyn("damper", damper);
    dyn("pass1", pass1); dyn("pass2", pass2); dyn("coke", coke); dyn("e5_byp", e5_byp); dyn("ay_lag", ay_lag);
    dyn("burner_pressure", burner_pressure); dyn("duty", duty); dyn("t_out", t_out); dyn("t_dead", t_dead);
    dyn("t_pass1", t_pass1); dyn("t_pass2", t_pass2); dyn("skin1", skin1); dyn("skin2", skin2); dyn("o2", o2);
    dyn("co", co); dyn("stack", stack); dyn("xv_oil", *xv_oil); dyn("oil_pressure", oil_pressure);
    dyn("tx_tout", tx_tout); dyn("tx_o2", tx_o2); dyn("tx_fuel", tx_fuel); dyn("tx_skin", tx_skin);

    add_malfunction("MF-003", "FCV-3001", "Control valve seat leakage", "Valve", "Leakage", 0, 15,
                    [this](bool a, double v) { fcv3001.leakage_pct = a ? v : 0.0; });
    add_malfunction("MF-016", "TT-3001", "Thermocouple burnout to upscale", "Transmitter", "", 0, 1,
                    [this](bool a, double) { tx_tout.failure = a ? TxFailure::FailHigh : TxFailure::None; });
    add_malfunction("MF-019", "AT-3001", "Oxygen analyser slow response", "Analyser", "Extra lag", 0, 120,
                    [this](bool a, double v) { tx_o2.extra_lag = a ? v : 0.0; });
    add_malfunction("MF-018", "ID-301", "Induced draft fan trip", "Rotating", "", 0, 1,
                    [this](bool a, double) { id_fan_faulted = a; });
    add_malfunction("MF-030", "H1", "Burner fouling", "Process", "Efficiency loss", 0, 20,
                    [this](bool a, double v) { efficiency_loss = a ? v : 0.0; });
}

double FiredHeater::efficiency(double excess_air) {
    if (excess_air < 0.0) return clamp(0.62 + excess_air * 1.6, 0.15, 0.90);
    return clamp(0.90 - 0.55 * py_pow(excess_air - 0.15, 2.0) - 0.18 * excess_air, 0.30, 0.90);
}

void FiredHeater::step(double dt) {
    const bool air_fail = bus_get("air_failure") != 0.0;
    const bool esd = bus_get("esd_u300") != 0.0;
    const bool sdv_open = (t("XY-XV3001-OPN").effective() != 0.0) && !esd;
    t("ZSO-XV3001").set(b2d(sdv_open));
    t("ZSC-XV3001").set(b2d(!sdv_open));
    fcv3001.step(dt, t("FCV-3001").effective(), air_fail);
    fcv3002.step(dt, t("FCV-3002").effective(), air_fail);
    fcv3003.step(dt, t("FCV-3003").effective(), air_fail);
    damper.step(dt, t("FCV-3004").effective(), air_fail);
    pass1.step(dt, t("FCV-3005").effective(), air_fail);
    pass2.step(dt, t("FCV-3006").effective(), air_fail);
    const bool fan_running = (t("XY-ID301-STR").effective() != 0.0) && !(t("XY-ID301-STP").effective() != 0.0);
    const double fan_speed = fan_running ? t("SC-3001").effective() : 0.0;
    const bool fan_speed_ready = fan_running && fan_speed > 25.0;
    (void)bus_get("fg_header_pressure_bara");   // read, as the Python unit reads it
    const double supply = bus_get("fg_to_h1_flow");
    const double burner_abs = burner_pressure.y;
    const double sg = 0.65;
    const double fuel_gas = sdv_open ? fcv3001.gas_flow(burner_abs, P_STD, sg, 300.0) : 0.0;
    const double net = supply - fuel_gas;
    burner_pressure.step(net * P_STD / (3600.0 * BURNER_HEADER_VOLUME), dt);
    const double burner_barg = clamp(burner_pressure.y - P_STD, 0.0, 10.0);
    bus_set("h1_burner_pressure_bara", burner_pressure.y);
    const bool low_pressure_trip = burner_barg < 1.5;
    const bool pilot = (t("XY-3010").effective() != 0.0) || lit;
    const bool purge_air_ok = damper.position > 30.0 && fan_speed_ready;
    if ((t("XY-3011").effective() != 0.0) && purge_air_ok && !lit) {
        purge_timer += dt;
        if (purge_timer >= 300.0) purged = true;
    } else if (!lit && !purge_air_ok) {
        purge_timer = 0.0;
        purged = false;
    }
    const bool purged_now = purged || lit;
    if (esd || !sdv_open || low_pressure_trip || fuel_gas < 30.0) lit = false;
    else if (pilot && fuel_gas > 40.0) lit = true;
    double fuel_oil = 0.0;
    if (fuel_oil_available) fuel_oil = (fcv3002.flow(6.0, 0.9) + fcv3003.flow(6.0, 0.9)) * 900.0 / 1000.0;
    double fuel_energy_mw = 0.0;
    if (lit) {
        const double lhv = bus_get("fg_lhv");
        fuel_energy_mw = (fuel_gas * lhv + fuel_oil * 41.0) / 3600.0;
    }
    const double air_flow = AIR_MAX * (damper.position / 100.0) * (0.35 + 0.65 * fan_speed / 100.0);
    const double stoich = fuel_gas * STOICH_AIR / 1000.0;
    double excess = stoich > 1e-6 ? safe_div(air_flow - stoich, stoich, 1.0) : 1.0;
    excess = clamp(excess, -0.6, 3.0);
    const double radiant_frac = clamp(0.78 - 0.10 * excess, 0.55, 0.82);
    const double coke_loss = coke.y * 0.15;
    const double eta = efficiency(excess) * (1.0 - (efficiency_loss + coke_loss) / 100.0);
    const double duty_now = duty.step(fuel_energy_mw * eta, dt);
    const double o2_ss = lit ? clamp(21.0 * excess / (1.0 + excess) * 0.95, 0.0, 12.0) : 20.9;
    double co_ss = excess > 0.02 ? 20.0 : clamp(60.0 + 5200.0 * (0.02 - excess), 20.0, 2000.0);
    if (!lit) co_ss = 0.0;
    const double charge = bus_get("charge_flow");
    double t_in = bus_get("charge_temperature");
    const double th_in = bus_get("r1_effluent_temperature");
    const double byp = e5_byp.step(clamp(t("TCV-3002").effective(), 0.0, 100.0), dt) / 100.0;
    const double e5_rise = (E5_EFF * std::max(th_in - t_in, 0.0)) * (1.0 - byp);
    const double t_e5 = t_in + std::min(e5_rise, 120.0);
    TT3007->set(clamp(t_e5, 0.0, 300.0));
    t_in = t_e5;
    AY3001->set(clamp(ay_lag.step(bus_get("fg_lhv"), dt), 20.0, 50.0));
    const double split1 = pass1.position / std::max(pass1.position + pass2.position, 1e-3);
    const double q1 = charge * split1, q2 = charge * (1.0 - split1);
    const double gas = bus_get("recycle_gas_to_h1") / 1000.0;
    const double gas_mw = bus_get("recycle_gas_mw");
    const double gas_kg_s = gas * 1000.0 / 22.4 * gas_mw / 3600.0;
    const double mass_kg_s = charge * 780.0 / 3600.0 + gas_kg_s;
    double rise;
    if (mass_kg_s > 0.5) rise = clamp(duty_now * 1000.0 / (mass_kg_s * CP_CHARGE), 0.0, 420.0);
    else rise = duty_now > 0.1 ? 420.0 : 0.0;
    const double t_ss = clamp(t_in + rise, 0.0, 480.0);
    const double delayed = t_dead.step(t_ss);
    const double t_out_now = t_out.step(delayed, dt);
    const double bias = (split1 - 0.5) * rise * 0.35;
    const double tp1 = t_pass1.step(clamp(t_ss - bias, 0.0, 480.0), dt);
    const double tp2 = t_pass2.step(clamp(t_ss + bias, 0.0, 480.0), dt);
    const double coke_skin = 1.0 + coke.y * 0.012;
    const double rad = duty_now * radiant_frac;
    const double skin_rise1 = 190.0 * coke_skin * safe_div(rad * 0.5, py_pow(std::max(q1, 2.0), 0.85), 0.0);
    const double skin_rise2 = 190.0 * coke_skin * safe_div(rad * 0.5, py_pow(std::max(q2, 2.0), 0.85), 0.0);
    const double s1 = skin1.step(clamp(tp1 + skin_rise1, 20.0, 740.0), dt);
    const double s2 = skin2.step(clamp(tp2 + skin_rise2, 20.0, 740.0), dt);
    const double skin_max = std::max(s1, s2);
    coke.step(std::max(skin_max - 540.0, 0.0) * 0.8 / 50.0 / 3600.0, dt);
    const double stack_now = stack.step(clamp(120.0 + duty_now * (1.0 - radiant_frac) * 55.0 + excess * 45.0, 40.0, 590.0), dt);
    bus_set("h1_inlet_pressure", 11.0);
    bus_set("h1_outlet_temperature", t_out_now);
    bus_set("h1_duty_mw", duty_now);
    { const double v_ = tx_tout.step(dt, t_out_now); TT3001->set(v_, tx_tout.quality); }
    TT3002->set(tp1);
    TT3003->set(tp2);
    TT3004->set(stack_now);
    { const double v_ = tx_skin.step(dt, s1); TT3005->set(v_, tx_skin.quality); }
    TT3006->set(s2);
    { const double v_ = tx_fuel.step(dt, fuel_gas); FT3001->set(v_, tx_fuel.quality); }
    FT3002->set(fuel_oil);
    FT3003->set(air_flow);
    FT3004->set(q1);
    FT3005->set(q2);
    PT3001->set(burner_barg);
    { const double v_ = tx_o2.step(dt, o2.step(o2_ss, dt)); AT3001->set(v_, tx_o2.quality); }
    AT3002->set(co.step(co_ss, dt));
    ZT3001->set(fcv3001.position);
    xv_oil->step(dt);
    ZT3002->set(fcv3002.position);
    ZT3003->set(fcv3003.position);
    IT3001->set(fan_running ? 250.0 * (0.25 + 0.75 * py_pow(fan_speed / 100.0, 3.0)) : 0.0);
    PT3003->set(clamp(-0.5 - 0.12 * fan_speed + excess * 1.5, -20.0, 8.0));
    t("XS-ID301-RUN").set(b2d(fan_running));
    t("XS-ID301-FLT").set(b2d(id_fan_faulted));
    t("BS-3001").set(b2d(lit));
    t("BS-3002").set(b2d(pilot && sdv_open));
    t("PSLL-3001").set(b2d(low_pressure_trip));
    t("PSHH-3001").set(b2d(burner_barg > 8.5));
    t("XS-3010").set(b2d(purged_now));
    PT3002->set(oil_pressure.step(fuel_oil_available ? 8.0 : 0.5, dt));
    t("XS-ID301-AVL").set(1.0);
    t("XS-ID301-VFD").set(b2d(!id_fan_faulted));
    t("ZSO-HV3001").set(b2d(fuel_oil_available));
    t("ZSC-HV3001").set(b2d(!fuel_oil_available));

    // the calculation trace: the energy balance and the combustion side
    tr("fuel_gas", fuel_gas); tr("fuel_oil", fuel_oil); tr("supply", supply); tr("burner_barg", burner_barg);
    tr("air_flow", air_flow); tr("stoich", stoich); tr("excess", excess); tr("eta", eta);
    tr("fuel_energy_mw", fuel_energy_mw); tr("duty_mw", duty_now); tr("charge", charge); tr("t_in", t_in);
    tr("rise", rise); tr("t_ss", t_ss); tr("t_out", t_out_now); tr("skin1", s1); tr("skin2", s2);
    tr("stack", stack_now); tr("coke", coke.y); tr("lit", b2d(lit));
    tr("FCV-3001", fcv3001.position); tr("FCV-3004", damper.position); tr("fan_speed", fan_speed);
}

Value FiredHeater::save_state() const {
    Dict d;
    d["bp"] = burner_pressure.y; d["duty"] = duty.y; d["tout"] = t_out.y;
    d["lit"] = lit ? 1.0 : 0.0; d["oil"] = fuel_oil_available ? 1.0 : 0.0;
    d["ay"] = ay_lag.y; d["e5b"] = e5_byp.y;
    return d;
}

void FiredHeater::load_state(const Value& s) {
    burner_pressure.reset(s.get_number("bp", 4.0));
    duty.reset(s.get_number("duty", 0.0));
    t_out.reset(s.get_number("tout", 62.0));
    t_dead.reset(s.get_number("tout", 62.0));
    lit = s.get_number("lit", 0.0) != 0.0;
    fuel_oil_available = s.get_number("oil", 0.0) != 0.0;
    ay_lag.reset(s.get_number("ay", 38.5));
    e5_byp.reset(s.get_number("e5b", 50.0));
}

std::vector<ParameterSpec> FiredHeater::parameters() const {
    return {{"AIR_MAX", AIR_MAX, "kNm3/h", "H1 maximum combustion-air flow", 0.0, 10000.0, true},
            {"BURNER_HEADER_VOLUME", BURNER_HEADER_VOLUME, "m3", "H1 burner-header gas volume", 0.1, 1000.0, true},
            {"DEADTIME_OUTLET", DEADTIME_OUTLET, "s", "H1 outlet-temperature transport delay", 0.0, 100000.0, true},
            {"DUTY_MAX", DUTY_MAX, "MW", "H1 maximum fired duty", 0.0, 1000.0, true},
            {"E5_EFF", E5_EFF, "fraction", "E5 feed/product exchanger effectiveness", 0.0, 1.0, true},
            {"TAU_OUTLET", TAU_OUTLET, "s", "H1 outlet-temperature lag", 0.001, 100000.0, true}};
}

}  // namespace azeocore::units
