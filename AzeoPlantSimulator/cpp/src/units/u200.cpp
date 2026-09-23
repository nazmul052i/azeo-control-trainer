#include "azeocore/units/u200.hpp"

#include <algorithm>
#include <cmath>

namespace azeocore::units {

namespace {
constexpr double P_STD = 1.01325;
constexpr double PI = 3.141592653589793;
inline double b2d(bool b) { return b ? 1.0 : 0.0; }
}  // namespace

RecycleCompressor::RecycleCompressor(TagDatabase& db_, ProcessBus& bus_, double dt_)
    : ProcessUnit(db_, bus_, dt_, "U200", "Recycle gas compressor C1"),
      antisurge("FCV-2001", 540, ValveChar::Linear, 1.5, false),
      suction_throttle("FCV-2002", 780, ValveChar::Linear, 8, false),
      cooler_cw("FCV-2003", 320, ValveChar::Linear, 8, false),
      ko_drain("LCV-2001", 30, ValveChar::Linear, 4),
      makeup_valve("PCV-2003", 60, ValveChar::Linear, 6, true),
      discharge_throttle("FCV-2004", 780, ValveChar::Linear, 8, false),
      guide_vanes(6.0, 0.0),
      p_suction(8.0 + P_STD, P_STD * 0.2, 25.0), p_discharge(12.0 + P_STD, P_STD * 0.2, 62.0),
      ko_level(35.0, 0.0, 100.0),
      flow(1.2, 0.0), t_discharge(25.0, 60.0), bearing(180.0, 55.0), lube_pressure(3.0, 0.0), vibration(2.0, 18.0),
      mw_lag(90.0, 12.0),
      tx_flow("FT-2001", 0, 80, 0.6, 0.6), tx_pdis("PT-2002", 0, 60, 0.3, 0.2), tx_vib("VT-2001", 0, 150, 0.4, 1.2) {
    PT2001 = &ai("PT-2001", "C1 suction pressure", "barg", 0, 20, 8.0);
    PT2002 = &ai("PT-2002", "C1 discharge pressure", "barg", 0, 60, 12.0);
    PT2003 = &ai("PT-2003", "C1 lube oil header pressure", "barg", 0, 6);
    PDT2001 = &ai("PDT-2001", "C1 suction filter differential", "mbar", 0, 200, 25.0);
    TT2001 = &ai("TT-2001", "C1 suction temperature", "degC", 0, 150, 45.0);
    TT2002 = &ai("TT-2002", "C1 discharge temperature", "degC", 0, 220, 60.0);
    TT2003 = &ai("TT-2003", "C1 drive end bearing temperature", "degC", 0, 150, 55.0);
    TT2004 = &ai("TT-2004", "C1 non-drive end bearing temperature", "degC", 0, 150, 54.0);
    TT2005 = &ai("TT-2005", "C1 lube oil supply temperature", "degC", 0, 100, 45.0);
    FT2001 = &ai("FT-2001", "C1 suction flow", "kNm3/h", 0, 80);
    FT2002 = &ai("FT-2002", "C1 anti-surge recycle flow", "kNm3/h", 0, 40);
    FT2003 = &ai("FT-2003", "H2 makeup flow to C1 suction", "kNm3/h", 0, 30);
    PT2004 = &ai("PT-2004", "H2 makeup header pressure", "barg", 0, 40, 25.0);
    ST2001 = &ai("ST-2001", "C1 speed feedback", "rpm", 0, 14000);
    IT2001 = &ai("IT-2001", "C1 motor current", "A", 0, 800);
    IT2002 = &ai("IT-2002", "P-201 main lube oil pump current", "A", 0, 60);
    IT2003 = &ai("IT-2003", "P-202 auxiliary lube oil pump current", "A", 0, 60);
    VT2001 = &ai("VT-2001", "C1 radial vibration", "micron", 0, 150, 18.0);
    AT2001 = &ai("AT-2001", "Recycle gas molecular weight", "kg/kmol", 4, 40, 14.0);
    LT2001 = &ai("LT-2001", "V-201 knockout drum level", "%", 0, 100, 35.0);
    ZT2001 = &ai("ZT-2001", "FCV-2001 anti-surge valve position", "%", 0, 100, 100.0);
    UY2001 = &ai("UY-2001", "C1 surge margin", "%", -50, 300, 60.0);
    JT2001 = &ai("JT-2001", "C1 shaft power", "MW", 0, 3, 0.0);
    GT2001 = &ai("GT-2001", "C1 inlet guide-vane angle feedback", "deg", 0, 40, 0.0);
    ao("SC-2001", "C1 VFD speed reference", "%", 0.0, 100.0, 70.0);
    ao("FCV-2001", "C1 anti-surge recycle valve", "%", 0.0, 100.0, 100.0);
    ao("PCV-2003", "H2 makeup valve", "%", 0.0, 100.0, 0.0);
    ao("GV-2001", "C1 inlet guide-vane angle demand", "%", 0.0, 100.0, 0.0);
    ao("FCV-2004", "C1 discharge throttle valve", "%", 0.0, 100.0, 100.0);
    ao("FCV-2002", "C1 suction throttle valve", "%", 0.0, 100.0, 100.0);
    ao("FCV-2003", "C1 discharge cooler cooling water valve", "%", 0.0, 100.0, 55.0);
    ao("LCV-2001", "V-201 knockout drum drain valve", "%", 0.0, 100.0, 30.0);
    di("VSHH-2001", "C1 vibration high high trip", "Normal", "Tripped");
    di("LSHH-2001", "V-201 knockout drum level high high", "Normal", "Tripped");
    di("PSL-2001", "C1 lube oil pressure low", "Normal", "Tripped");
    di("UA-2001", "C1 surge detected", "Normal", "Surging");
    motor = std::make_unique<MotorPackage>(*this, "C1", "Recycle gas compressor", 760.0, true, 6.0);
    lube_main = std::make_unique<MotorPackage>(*this, "P-201", "C1 main lube oil pump", 32.0);
    lube_aux = std::make_unique<MotorPackage>(*this, "P-202", "C1 auxiliary lube oil pump", 32.0);
    xv2001 = std::make_unique<SdvPackage>(*this, "XV-2001", "C1 suction shutdown valve", 4.0);

    dyn("motor", *motor); dyn("lube_main", *lube_main); dyn("lube_aux", *lube_aux); dyn("xv2001", *xv2001);
    dyn("antisurge", antisurge); dyn("suction_throttle", suction_throttle); dyn("cooler_cw", cooler_cw);
    dyn("ko_drain", ko_drain); dyn("makeup_valve", makeup_valve); dyn("discharge_throttle", discharge_throttle);
    dyn("guide_vanes", guide_vanes); dyn("p_suction", p_suction); dyn("p_discharge", p_discharge);
    dyn("ko_level", ko_level); dyn("flow", flow); dyn("t_discharge", t_discharge); dyn("bearing", bearing);
    dyn("lube_pressure", lube_pressure); dyn("vibration", vibration); dyn("mw_lag", mw_lag);
    dyn("tx_flow", tx_flow); dyn("tx_pdis", tx_pdis); dyn("tx_vib", tx_vib);

    // MF-006 changes the stroke time only: the actuator's rate limiter was
    // sized at construction and stays, exactly as the Python unit behaves
    add_malfunction("MF-006", "FCV-2001", "Slow anti-surge valve stroke", "Valve", "Stroke time", 1, 30,
                    [this](bool a, double v) { antisurge.stroke_time = a ? v : 1.5; });
    add_malfunction("MF-025", "C1", "Compressor fouling", "Rotating", "Efficiency loss", 0, 25,
                    [this](bool a, double v) { fouling_pct = a ? v : 0.0; });
    add_malfunction("MF-026", "C1", "Lube oil pressure decay", "Rotating", "", 0, 1,
                    [this](bool a, double) { lube_decay = a; });
    add_malfunction("MF-043", "PCV-2003", "H2 header pressure low", "Process", "Header pressure", 5, 25,
                    [this](bool a, double v) { h2_header = a ? v : 25.0; });
}

double RecycleCompressor::surge_flow(double speed_frac, double mw, double gv_frac) const {
    const double mw_factor = std::sqrt(clamp(mw / 14.0, 0.3, 3.0));
    return SURGE_SLOPE * RATED_FLOW * speed_frac / mw_factor * (0.70 + 0.30 * gv_frac);
}

void RecycleCompressor::step(double dt) {
    const bool air = bus_get("air_failure") != 0.0;
    const bool esd = bus_get("esd_u200") != 0.0;
    lube_main->step(dt, true);
    lube_aux->step(dt, true);
    double oil_target = (lube_main->running() || lube_aux->running()) ? 4.2 : 0.0;
    if (lube_decay) oil_target *= 0.35;
    const double oil = lube_pressure.step(oil_target, dt);
    const bool oil_ok = oil > 1.8;
    antisurge.step(dt, t("FCV-2001").effective(), air);
    suction_throttle.step(dt, t("FCV-2002").effective(), air);
    cooler_cw.step(dt, t("FCV-2003").effective(), air);
    ko_drain.step(dt, t("LCV-2001").effective(), air);
    makeup_valve.step(dt, t("PCV-2003").effective(), air);
    discharge_throttle.step(dt, t("FCV-2004").effective(), air);
    const double gv_deg = guide_vanes.step(clamp(t("GV-2001").effective(), 0.0, 40.0), dt);
    const double gv_frac = 1.0 - 0.55 * gv_deg / 40.0;
    xv2001->step(dt, esd, air);
    const bool permissive = oil_ok && xv2001->is_open() && ko_level.y < 85.0;
    const bool trip = esd || (t("VSHH-2001").value != 0.0) || (t("LSHH-2001").value != 0.0);
    motor->step(dt, permissive, t("SC-2001").effective(), trip);
    const double speed_frac = motor->speed() / 100.0;
    const double mw = AT2001->value;
    const double ps = p_suction.y;
    const double pd = p_discharge.y;
    double head = HEAD_COEFF * (1.0 - fouling_pct / 100.0) * py_pow(speed_frac, 2.0) * gv_frac
                  * std::sqrt(clamp(14.0 / std::max(mw, 1.0), 0.3, 3.0));
    const double required = std::max((pd / std::max(ps, 0.05)) - 1.0, 0.0) * 55.0;
    double q_target = safe_sqrt(safe_div(std::max(head - required, 0.0), FLOW_COEFF, 0.0));
    q_target *= suction_throttle.position / 100.0;
    q_target *= discharge_throttle.position / 100.0;
    q_target = std::min(q_target, CHOKE_FLOW * speed_frac);
    double flow_now = flow.step(speed_frac > 0.05 ? q_target : 0.0, dt);
    const double q_surge = surge_flow(speed_frac, mw, gv_frac);
    const double recycle = speed_frac > 0.05 ? antisurge.gas_flow(pd, ps, mw / 28.96, 330.0) / 1000.0 : 0.0;
    const double total_through = flow_now + recycle;
    motor->device.load_frac = clamp(safe_div(total_through, CHOKE_FLOW * std::max(speed_frac, 0.05), 1.0), 0.10, 1.40);
    const double margin = q_surge > 1e-6 ? safe_div(total_through - q_surge, q_surge, 1.5) : 1.5;
    surging = speed_frac > 0.15 && margin < 0.0;
    if (surging) {
        surge_phase += dt * 2.0 * PI * 3.0;
        const double pulse = py_sin(surge_phase);
        flow_now *= 0.25 * pulse;
        head *= 0.55 + 0.3 * pulse;
    }
    const double offgas_in = bus_get("d3_offgas_to_c1") / 1000.0;
    const double demand = bus_get("quench_and_makeup_demand") / 1000.0;
    const double makeup = !esd ? makeup_valve.gas_flow(h2_header + P_STD, ps, 2.016 / 28.96, 310.0) / 1000.0 : 0.0;
    const double to_h1 = speed_frac > 0.05 ? LINE_K * safe_sqrt(std::max(pd - 12.0, 0.0)) : 0.0;
    const double d_ps = ((offgas_in + makeup + recycle - flow_now) * 1000.0 * P_STD / (3600.0 * SUCTION_VOLUME));
    p_suction.step(d_ps, dt);
    const double d_pd = ((flow_now - recycle - demand - to_h1) * 1000.0 * P_STD / (3600.0 * DISCHARGE_VOLUME));
    p_discharge.step(d_pd, dt);
    TT2001->set(clamp(bus_get("d3_liquid_temperature") * 0.55 + 18.0, 5.0, 95.0));
    const double mw_off = clamp(18.0 - 0.06 * bus_get("r1_conversion"), 4.0, 40.0);
    const double inflow = std::max(offgas_in + makeup, 1e-6);
    const double mw_mix = (offgas_in * mw_off + makeup * 2.016) / inflow;
    AT2001->set(clamp(mw_lag.step(mw_mix, dt), 4.0, 40.0));
    fault_flag = motor->device.faulted;
    const double ratio = clamp(p_discharge.y / std::max(p_suction.y, 0.05), 1.0, 6.0);
    const double t_suc = TT2001->value;
    const double t_poly = (t_suc + 273.15) * py_pow(ratio, 0.28) - 273.15;
    const double cw_dp = bus_get("cooling_water_dp_bar");
    const double cw_supply = bus_get("cooling_water_temperature");
    const double cw_flow = cooler_cw.flow(cw_dp, 1.0);
    const double cw_design = 0.865 * cooler_cw.cv_rated * std::sqrt(2.4);
    const double cooling = clamp(cw_flow / std::max(cw_design, 1.0), 0.0, 1.5);
    const double t_dis = t_discharge.step(
        clamp(t_poly - cooling * 0.45 * (t_poly - (cw_supply + 12.0)), 20.0, 215.0), dt);
    const double gas_kg_s = std::max(total_through, 0.0) * 1000.0 * std::max(mw, 1.0) / 22.414 / 3600.0;
    const double cw_duty = clamp(gas_kg_s * 2.1 * std::max(t_poly - t_dis, 0.0), 0.0, 50000.0);
    bus_set("u200_cw_flow", cw_flow);
    bus_set("u200_cw_duty", cw_duty);
    const double condensate = std::max(0.0, (t_suc - 35.0)) * 0.02 + (surging ? 0.6 : 0.0);
    const double drained = ko_drain.flow(std::max(p_suction.y - P_STD, 0.0), 0.7);
    const double ko_before = ko_level.y;
    ko_level.step((condensate - drained) / 11.0 * 100.0 / 60.0, dt);
    record_inventory_balance("V-201 liquid inventory", condensate, drained, ko_before, ko_level.y, 11.0, dt, 100.0, 60.0,
                             "m3/min", 1e-5);
    const double vib_target = 18.0 + (surging ? 70.0 : 0.0) + fouling_pct * 1.1;
    const double vib = vibration.step(motor->running() ? vib_target : 3.0, dt);
    const double bearing_now = bearing.step(55.0 + (motor->running() ? 28.0 : 0.0) + (oil_ok ? 0.0 : 45.0) + (surging ? 12.0 : 0.0), dt);
    bus_set("c1_discharge_pressure", p_discharge.y);
    bus_set("c1_recycle_gas_flow", std::max(flow_now, 0.0) * 1000.0);
    bus_set("c1_running", motor->running() ? 1.0 : 0.0);
    bus_set("recycle_gas_to_h1", std::max(to_h1, 0.0) * 1000.0);
    bus_set("recycle_gas_mw", AT2001->value);
    PT2001->set(clamp(p_suction.y - P_STD, 0.0, 20.0));
    { const double v_ = tx_pdis.step(dt, clamp(p_discharge.y - P_STD, 0, 60)); PT2002->set(v_, tx_pdis.quality); }
    PT2003->set(oil);
    // 25 mbar clean plus 40 at the 65 kNm3/h design flow, square law
    PDT2001->set(clamp(25.0 + 40.0 * py_pow(std::max(flow_now, 0.0) / 65.0, 2.0), 0, 200));
    TT2002->set(t_dis);
    TT2003->set(bearing_now);
    TT2004->set(bearing_now - 1.5);
    TT2005->set((lube_main->running() || lube_aux->running()) ? 45.0 : 30.0);
    { const double v_ = tx_flow.step(dt, std::max(flow_now, 0.0)); FT2001->set(v_, tx_flow.quality); }
    FT2002->set(clamp(recycle, 0.0, 40.0));
    FT2003->set(clamp(makeup, 0.0, 30.0));
    PT2004->set(clamp(h2_header, 0.0, 40.0));
    ST2001->set(speed_frac * RATED_SPEED);
    IT2001->set(motor->current());
    IT2002->set(lube_main->current());
    IT2003->set(lube_aux->current());
    { const double v_ = tx_vib.step(dt, vib); VT2001->set(v_, tx_vib.quality); }
    LT2001->set(ko_level.y);
    ZT2001->set(antisurge.position);
    UY2001->set(clamp(margin * 100.0, -50.0, 300.0));
    GT2001->set(gv_deg);
    JT2001->set(clamp(2.2 * motor->device.load_frac * py_pow(speed_frac, 2.0) * (motor->running() ? 1.0 : 0.0), 0.0, 3.0));
    t("VSHH-2001").set(b2d(vib > 110.0));
    t("LSHH-2001").set(b2d(ko_level.y > 85.0));
    t("PSL-2001").set(b2d(!oil_ok));
    t("UA-2001").set(b2d(surging));

    // the calculation trace: the compressor map and the gas balance
    tr("speed_frac", speed_frac); tr("mw", mw); tr("head", head); tr("required", required); tr("q_target", q_target);
    tr("flow", flow_now); tr("recycle", recycle); tr("q_surge", q_surge); tr("margin", margin); tr("surging", b2d(surging));
    tr("offgas_in", offgas_in); tr("makeup", makeup); tr("demand", demand); tr("to_h1", to_h1);
    tr("p_suction", p_suction.y); tr("p_discharge", p_discharge.y); tr("oil", oil); tr("condensate", condensate);
    tr("drained", drained); tr("ko_level", ko_level.y); tr("FCV-2001", antisurge.position); tr("gv_deg", gv_deg);
}

Value RecycleCompressor::save_state() const {
    Dict d;
    d["ps"] = p_suction.y; d["pd"] = p_discharge.y; d["ko"] = ko_level.y; d["flow"] = flow.y;
    d["run"] = motor->running() ? 1.0 : 0.0; d["lube"] = lube_main->running() ? 1.0 : 0.0;
    d["mwl"] = mw_lag.y; d["gv"] = guide_vanes.y;
    return d;
}

void RecycleCompressor::load_state(const Value& s) {
    p_suction.reset(s.get_number("ps", 9.0));
    p_discharge.reset(s.get_number("pd", 13.0));
    ko_level.reset(s.get_number("ko", 35.0));
    flow.reset(s.get_number("flow", 0.0));
    motor->device.running = s.get_number("run", 0.0) != 0.0;
    lube_main->device.running = s.get_number("lube", 0.0) != 0.0;
    mw_lag.reset(s.get_number("mwl", 12.0));
    guide_vanes.reset(s.get_number("gv", 0.0));
}

std::vector<ParameterSpec> RecycleCompressor::parameters() const {
    return {{"CHOKE_FLOW", CHOKE_FLOW, "kNm3/h", "C1 rated-speed choke flow", 1.0, 1000.0, true},
            {"DISCHARGE_VOLUME", DISCHARGE_VOLUME, "m3", "C1 discharge-system gas volume", 0.1, 1000.0, true},
            {"FLOW_COEFF", FLOW_COEFF, "kJ/kg/(kNm3/h)^2", "C1 map head-loss coefficient", 0.0, 10.0, true},
            {"HEAD_COEFF", HEAD_COEFF, "kJ/kg", "C1 rated-speed zero-flow head coefficient", 0.0, 1000.0, true},
            {"LINE_K", LINE_K, "kNm3/h/sqrt(bar)", "C1-to-H1 line conductance", 0.0, 1000.0, true},
            {"RATED_FLOW", RATED_FLOW, "kNm3/h", "C1 rated flow, the surge line's scale", 1.0, 1000.0, true},
            {"RATED_SPEED", RATED_SPEED, "rpm", "C1 rated shaft speed", 100.0, 100000.0, true},
            {"SUCTION_VOLUME", SUCTION_VOLUME, "m3", "C1 suction-system gas volume", 0.1, 1000.0, true},
            {"SURGE_SLOPE", SURGE_SLOPE, "fraction", "C1 rated-flow surge-line fraction", 0.0, 1.0, true}};
}

}  // namespace azeocore::units
