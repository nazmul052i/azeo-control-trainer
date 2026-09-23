#include "azeocore/units/u800.hpp"

#include <algorithm>
#include <cmath>

namespace azeocore::units {

namespace {
inline double b2d(bool b) { return b ? 1.0 : 0.0; }
}  // namespace

// ------------------------------------------------------------------ U800

EffluentTreatment::EffluentTreatment(TagDatabase& db_, ProcessBus& bus_, double dt_)
    : ProcessUnit(db_, bus_, dt_, "U800", "Effluent treatment"),
      v_coarse("FCV-8001", 9, ValveChar::Linear, 3),
      v_fine("FCV-8002", 1.5, ValveChar::EqualPercent, 3),
      v_acid("FCV-8003", 1.5, ValveChar::EqualPercent, 3),
      v_discharge("FCV-8004", 130, ValveChar::Linear, 6),
      v_cw("TCV-8001", 170, ValveChar::Linear, 8, false),
      excess_acid(0.0, -0.05, 0.05), eff_flow(2.0, 0.0), level(55.0, 0.0, 100.0),
      ph_dead(35.0, dt_, 7.0), e4_lag(60.0, 50.0), ph_lag(12.0, 7.0),
      tx_ph("AT-8002", 0, 14, 6.0, 0.4) {
    AT8001 = &ai("AT-8001", "Neutralisation tank inlet pH", "pH", 0, 14, 4.2);
    AT8002 = &ai("AT-8002", "Neutralisation tank outlet pH", "pH", 0, 14, 7.0);
    AT8003 = &ai("AT-8003", "Effluent outlet chemical oxygen demand", "mg/l", 0, 1000, 320.0);
    FT8001 = &ai("FT-8001", "Effluent flow to treatment", "m3/h", 0, 120, 0.0);
    TT8001 = &ai("TT-8001", "Effluent temperature", "degC", 0, 80, 38.0);
    LT8001 = &ai("LT-8001", "NT-801 neutralisation tank level", "%", 0, 100, 55.0);
    IT8001 = &ai("IT-8001", "M-801 agitator motor current", "A", 0, 60, 0.0);
    QT8001 = &ai("QY-8001", "Linearised pH signal", "%", 0, 100, 50.0);
    ao("FCV-8001", "Caustic coarse dosing valve (split range 50-100%)");
    ao("FCV-8002", "Caustic fine dosing valve (split range 0-50%)", "%", 0.0, 100.0, 18.0);
    ao("FCV-8003", "Acid dosing valve");
    ao("FCV-8004", "Effluent discharge control valve", "%", 0.0, 100.0, 45.0);
    ao("TCV-8001", "E4 effluent cooler cooling water valve", "%", 0.0, 100.0, 50.0);
    di("ASHH-8001", "Effluent pH outside discharge consent", "Normal", "Tripped");
    di("HV-8001-ZSO", "HV-8001 caustic manual isolation open", "Not open", "Open", true);
    agitator = std::make_unique<MotorPackage>(*this, "M-801", "NT-801 agitator", 48.0);
    IT8002 = &ai("IT-8002", "P-801A motor current", "A", 0, 120);
    IT8003 = &ai("IT-8003", "P-801B motor current", "A", 0, 120);
    di("ZSO-HV8001", "HV-8001 open limit switch", "Not open", "Open", true);
    di("ZSC-HV8001", "HV-8001 closed limit switch", "Not closed", "Closed", false);
    transfer = std::make_unique<PumpTrain>(*this, "P-801A", "P-801B", "Effluent transfer pump", "MOV-8001A",
                                           "MOV-8001B", 95.0, 140.0, 10.0, 70.0, false, 15.0, true);

    dyn("agitator", *agitator); dyn("transfer", *transfer);
    dyn("v_coarse", v_coarse); dyn("v_fine", v_fine); dyn("v_acid", v_acid); dyn("v_discharge", v_discharge);
    dyn("v_cw", v_cw);
    dyn("excess_acid", excess_acid); dyn("eff_flow", eff_flow); dyn("level", level); dyn("ph_dead", ph_dead);
    dyn("e4_lag", e4_lag); dyn("ph_lag", ph_lag); dyn("tx_ph", tx_ph);

    add_malfunction("MF-039", "NT-801", "Effluent acid slug", "Process", "Inlet pH", 1, 7,
                    [this](bool a, double v) { inlet_ph = a ? v : 4.2; });
    add_malfunction("MF-042", "AT-8002", "pH probe coating, slow response", "Analyser", "Extra lag", 0, 300,
                    [this](bool a, double v) { tx_ph.extra_lag = a ? v : 0.0; });
}

double EffluentTreatment::ph_from_excess(double excess, double buffer_k) {
    // titration curve; buffering flattens it away from the equivalence point
    const double x = excess / std::max(buffer_k, 1e-6);
    return clamp(7.0 - 3.2 * std::copysign(std::log10(1.0 + std::fabs(x)), x), 0.5, 13.5);
}

void EffluentTreatment::step(double dt) {
    const bool air = bus_get("air_failure") != 0.0;
    v_coarse.step(dt, t("FCV-8001").effective(), air);
    v_fine.step(dt, t("FCV-8002").effective(), air);
    v_acid.step(dt, t("FCV-8003").effective(), air);
    v_discharge.step(dt, t("FCV-8004").effective(), air);
    v_cw.step(dt, t("TCV-8001").effective(), air);
    agitator->step(dt, level.y > 20.0);
    const double inflow = clamp(bus_get("sour_water_flow") + 22.0, 0.0, 120.0);
    t("ZSO-HV8001").set(1.0);
    t("ZSC-HV8001").set(0.0);
    IT8002->set(transfer->motor_a.current());
    IT8003->set(transfer->motor_b.current());
    transfer->step(dt, 0.9, eff_flow.y, level.y > 8.0, 100.0, 0.15, 0.35, 60.0);
    const double p_dis = transfer->discharge_pressure(eff_flow.y, 1000.0);
    double outflow = transfer->any_running() ? v_discharge.flow(std::max(p_dis - 1.2, 0.0), 1.0) : 0.0;
    outflow = eff_flow.step(outflow, dt);
    const double level_before = level.y;
    level.step((inflow - outflow) / TANK_VOLUME * 100.0 / 3600.0, dt);
    record_inventory_balance("neutralization tank inventory", inflow, outflow, level_before, level.y, TANK_VOLUME, dt);

    // ---- neutralisation: concentration is integrated, pH is read off the curve
    const double caustic = (v_coarse.flow(3.0, 1.2) + v_fine.flow(3.0, 1.2)) * (manual_caustic_open ? 1.0 : 0.0);
    const double acid = v_acid.flow(3.0, 1.2);
    const double inlet_excess = (py_pow(10.0, -inlet_ph) - py_pow(10.0, inlet_ph - 14.0)) * 60.0;
    const double acid_in = inflow * inlet_excess + acid * 2.0;
    const double base_in = caustic * 2.0;
    const double volume = std::max(TANK_VOLUME * level.y / 100.0, 1.0);
    const double mixing = agitator->running() ? MIX_TAU_RUNNING : MIX_TAU_STOPPED;
    const double rate = (acid_in - base_in - outflow * excess_acid.y * 1000.0) / (volume * 1000.0) / (mixing / 45.0)
                        / 3600.0 * 1000.0;
    excess_acid.step(rate, dt);
    const double ph_true = ph_from_excess(excess_acid.y);
    const double ph = ph_lag.step(ph_dead.step(ph_true), dt);
    t("HV-8001-ZSO").set(b2d(manual_caustic_open));
    const double base_t = clamp(30.0 + 0.18 * bus_get("d3_liquid_temperature"), 10.0, 78.0);
    const double cw_dp = bus_get("cooling_water_dp_bar");
    const double cw_supply = bus_get("cooling_water_temperature");
    const double cw_flow = v_cw.flow(cw_dp, 1.0);
    const double cw_design = 0.865 * v_cw.cv_rated * std::sqrt(2.4);
    const double cool = e4_lag.step(clamp(cw_flow / std::max(cw_design, 1.0), 0.0, 1.5), dt);
    const double outlet_t = clamp(base_t - cool * 0.75 * std::max(base_t - cw_supply, 0.0), 10.0, 78.0);
    TT8001->set(outlet_t);
    const double cw_duty = clamp(inflow * 1000.0 / 3600.0 * 4.18 * std::max(base_t - outlet_t, 0.0), 0.0, 50000.0);
    bus_set("u800_cw_flow", cw_flow);
    bus_set("u800_cw_duty", cw_duty);
    AT8001->set(inlet_ph);
    { const double v_ = tx_ph.step(dt, ph); AT8002->set(v_, tx_ph.quality); }
    AT8003->set(clamp(320.0 + std::fabs(ph - 7.0) * 90.0, 0, 1000));
    FT8001->set(outflow);
    LT8001->set(level.y);
    IT8001->set(agitator->current());
    const double x_lin = std::copysign(py_pow(10.0, std::fabs(ph - 7.0) / 3.2) - 1.0, ph - 7.0);
    QT8001->set(clamp(50.0 + x_lin * 4.0, 0.0, 100.0));
    t("ASHH-8001").set(b2d(!(6.0 <= ph && ph <= 9.0)));

    // the calculation trace: the tank balance and the titration
    tr("inflow", inflow); tr("outflow", outflow); tr("level", level.y); tr("p_dis", p_dis);
    tr("caustic", caustic); tr("acid", acid); tr("acid_in", acid_in); tr("base_in", base_in);
    tr("volume", volume); tr("mixing", mixing); tr("rate", rate); tr("excess_acid", excess_acid.y);
    tr("ph_true", ph_true); tr("ph", ph); tr("inlet_ph", inlet_ph); tr("x_lin", x_lin);
    tr("FCV-8001", v_coarse.position); tr("FCV-8002", v_fine.position); tr("FCV-8003", v_acid.position);
    tr("FCV-8004", v_discharge.position); tr("agitator", b2d(agitator->running()));
}

Value EffluentTreatment::save_state() const {
    Dict d;
    d["ex"] = excess_acid.y; d["lvl"] = level.y; d["ph"] = ph_lag.y; d["e4"] = e4_lag.y;
    return d;
}

void EffluentTreatment::load_state(const Value& s) {
    excess_acid.reset(s.get_number("ex", 0.0));
    level.reset(s.get_number("lvl", 55.0));
    ph_lag.reset(s.get_number("ph", 7.0));
    e4_lag.reset(s.get_number("e4", 50.0));
}

std::vector<ParameterSpec> EffluentTreatment::parameters() const {
    return {{"MIX_TAU_RUNNING", MIX_TAU_RUNNING, "s", "Neutralization mixing lag with agitator running", 0.001, 100000.0, true},
            {"MIX_TAU_STOPPED", MIX_TAU_STOPPED, "s", "Neutralization mixing lag with agitator stopped", 0.001, 1000000.0, true},
            {"TANK_VOLUME", TANK_VOLUME, "m3", "Neutralization tank effective volume", 0.1, 10000.0, true}};
}

// ------------------------------------------------------------------ U900

const std::vector<SafetySystem::Effect>& SafetySystem::effects() {
    static const std::vector<Effect> e = {
        {"XY-9001", "esd_total", "ESD-0 total plant shutdown"},
        {"XY-9002", "esd_reaction", "ESD-1 shutdown U200, U300 and U400"},
        {"XY-9003", "esd_fractionation", "ESD-1 shutdown U500 and U600"},
        {"XY-9004", "esd_boiler", "ESD-1 shutdown U700"},
        {"XY-9005", "esd_depressure", "R1 emergency depressuring"},
    };
    return e;
}

SafetySystem::SafetySystem(TagDatabase& db_, ProcessBus& bus_, double dt_)
    : ProcessUnit(db_, bus_, dt_, "U900", "Safety instrumented system") {
    static const std::pair<const char*, const char*> inputs[] = {
        {"HS-9001", "Field ESD pushbutton (level 1)"},
        {"HS-9002", "Control room ESD pushbutton (level 0)"},
        {"GD-9001", "Gas detected zone 1 reactor area at 20% LEL"},
        {"GD-9002", "Gas detected zone 2 compressor house at 20% LEL"},
        {"GD-9003", "Gas detected zone 3 fired equipment at 20% LEL"},
        {"FD-9001", "Flame detected zone 1"},
        {"FD-9002", "Flame detected zone 2"},
        {"XS-9002", "SIS reset request from field"},
    };
    for (const auto& [tag, desc] : inputs) di(tag, desc, "Normal", "Detected");
    di("XS-9001", "Fire water pump running", "Stopped", "Running");
    for (const auto& e : effects()) do_(e.tag, std::string(e.desc) + " command", "Normal", "Trip");
    do_("XY-9006", "ESD healthy and first-out reset lamp", "Off", "On");
    ai("XI-9001", "Active ESD effects", "-", 0, 10, 0.0);
    for (const auto& [tag, desc] : inputs) initiators.emplace_back(tag, false);

    struct Mf { const char* id; const char* tag; const char* desc; };
    static const Mf mfs[] = {
        {"MF-901", "GD-9001", "Gas detected, reactor area"},
        {"MF-902", "GD-9002", "Gas detected, compressor house"},
        {"MF-903", "GD-9003", "Gas detected, fired equipment"},
        {"MF-904", "FD-9001", "Flame detected, zone 1"},
        {"MF-905", "HS-9001", "Field ESD pushbutton pressed"},
        {"MF-906", "HS-9002", "Control room ESD pushbutton pressed"},
    };
    for (const auto& m : mfs) {
        const std::string tag = m.tag;
        add_malfunction(m.id, m.tag, m.desc, "Safety", "", 0, 1, [this, tag](bool a, double) {
            for (auto& [name, state] : initiators) if (name == tag) state = a;
        });
    }
}

void SafetySystem::step(double) {
    for (const auto& [tag, state] : initiators) t(tag).set(b2d(state));
    const bool total = t("XY-9001").effective() != 0.0;
    int active = 0;
    for (const auto& e : effects()) {
        const bool tripped = (t(e.tag).effective() != 0.0) || total;
        bus_set(e.key, tripped ? 1.0 : 0.0);
        active += tripped ? 1 : 0;
    }
    const bool reaction = (bus_get("esd_reaction") != 0.0) || total;
    const bool fractionation = (bus_get("esd_fractionation") != 0.0) || total;
    const bool boiler = (bus_get("esd_boiler") != 0.0) || total;
    bus_set("esd_u100", total ? 1.0 : 0.0);
    bus_set("esd_u200", reaction ? 1.0 : 0.0);
    bus_set("esd_u300", reaction ? 1.0 : 0.0);
    bus_set("esd_u400", reaction ? 1.0 : 0.0);
    bus_set("esd_u500", fractionation ? 1.0 : 0.0);
    bus_set("esd_u700", boiler ? 1.0 : 0.0);
    t("XS-9001").set(b2d(t("FD-9001").value != 0.0 || t("FD-9002").value != 0.0));
    t("XI-9001").set(double(active));
    tr("total", b2d(total)); tr("reaction", b2d(reaction)); tr("fractionation", b2d(fractionation));
    tr("boiler", b2d(boiler)); tr("active", double(active));
}

Value SafetySystem::save_state() const { return Dict{}; }

void SafetySystem::load_state(const Value&) {}

}  // namespace azeocore::units
