#include "azeocore/units/u700.hpp"

#include <algorithm>
#include <cmath>

namespace azeocore::units {

namespace {
constexpr double P_STD = 1.01325;
constexpr double STOICH_AIR = 9.6;    // Nm3 air per Nm3 fuel gas
constexpr double BFW_COND = 30.0;     // uS/cm of the feedwater
inline double b2d(bool b) { return b ? 1.0 : 0.0; }
}  // namespace

SteamBoiler::SteamBoiler(TagDatabase& db_, ProcessBus& bus_, double dt_)
    : ProcessUnit(db_, bus_, dt_, "U700", "Steam boiler B1 and MP steam header"),
      v_bfw("FCV-7001", 110, ValveChar::EqualPercent, 5),
      v_fuel("FCV-7002", 22, ValveChar::Linear, 3),
      v_damper("FCV-7003", 1400, ValveChar::Linear, 15),
      oil_lag(6.0, 0.0), b2_lag(45.0, 0.0),
      v_blowdown("FCV-7004", 0.4, ValveChar::Linear, 6),
      v_letdown("PCV-7001", 190, ValveChar::Linear, 4),
      v_spray("TCV-7001", 22, ValveChar::Linear, 4),
      v_da_makeup("LCV-7001", 90, ValveChar::Linear, 6),
      inventory(0.0, -320.0, 320.0), bfw_flow(2.0, 0.0),
      drum_pressure(44.0 + P_STD, P_STD * 0.2, 62.0),
      header_pressure(36.0 + P_STD, P_STD * 0.2, 48.0),
      da_level(55.0, 0.0, 100.0), steam(12.0, 0.0), swell(0.0, SWELL_TAU, 0.0),
      superheat(45.0, 385.0), flue_temp(60.0, 165.0), o2(6.0, 3.2), co(4.0, 20.0),
      conductivity(1500.0, 0.0, 5000.0),
      tx_level("LT-7001", -300, 300, 1.0, 0.6), tx_steam("FT-7001", 0, 70, 0.8, 0.5),
      tx_o2("AT-7001", 0, 21, 14.0, 0.8) {
    LT7001 = &ai("LT-7001", "B1 steam drum level", "mm", -300, 300, 0.0);
    LT7002 = &ai("LT-7002", "Deaerator level", "%", 0, 100, 55.0);
    PT7001 = &ai("PT-7001", "B1 steam drum pressure", "barg", 0, 60, 44.0);
    PT7002 = &ai("PT-7002", "MP steam header pressure", "barg", 0, 45, 36.0);
    PT7003 = &ai("PT-7003", "B1 furnace draft", "mmH2O", -25, 10, -6.0);
    PT7004 = &ai("PT-7004", "Deaerator pressure", "barg", 0, 5, 1.2);
    FT7001 = &ai("FT-7001", "B1 steam flow to header", "t/h", 0, 70, 0.0);
    FT7002 = &ai("FT-7002", "B1 feedwater flow", "t/h", 0, 80, 0.0);
    FT7003 = &ai("FT-7003", "B1 fuel gas flow", "Nm3/h", 0, 5000, 0.0);
    FT7004 = &ai("FT-7004", "B1 combustion air flow", "kNm3/h", 0, 55, 0.0);
    FT7005 = &ai("FT-7005", "B1 fuel oil flow", "kg/h", 0, 3000, 0.0);
    FT7006 = &ai("FT-7006", "Steam from B2 to header", "t/h", 0, 150, 0.0);
    TT7001 = &ai("TT-7001", "B1 superheated steam temperature", "degC", 0, 450, 385.0);
    TT7002 = &ai("TT-7002", "B1 feedwater temperature", "degC", 0, 200, 105.0);
    TT7003 = &ai("TT-7003", "B1 flue gas temperature", "degC", 0, 500, 165.0);
    AT7001 = &ai("AT-7001", "B1 flue gas oxygen", "mol%", 0, 21, 3.2);
    AT7002 = &ai("AT-7002", "B1 flue gas carbon monoxide", "ppm", 0, 2000, 20.0);
    CT7001 = &ai("CT-7001", "B1 steam drum conductivity", "uS/cm", 0, 5000, 1800.0);
    IT7003 = &ai("IT-7003", "B1 forced draft fan motor current", "A", 0, 300, 0.0);
    ZT7001 = &ai("ZT-7001", "FCV-7001 position feedback", "%", 0, 100, 0.0);
    ao("FCV-7001", "B1 feedwater control valve", "%", 0.0, 100.0, 42.0);
    ao("FCV-7002", "B1 fuel gas control valve", "%", 0.0, 100.0, 25.0);
    ao("FCV-7003", "B1 forced draft fan inlet damper", "%", 0.0, 100.0, 20.0);
    ao("FCV-7004", "B1 continuous blowdown valve", "%", 0.0, 100.0, 26.0);
    ao("FCV-7005", "B1 fuel oil control valve", "%", 0.0, 100.0, 0.0);
    ao("SC-7001", "B1 forced draft fan VFD speed reference", "%", 0.0, 100.0, 65.0);
    ao("SC-7002", "P-701A VFD speed reference", "%", 0.0, 100.0, 100.0);
    ao("PCV-7001", "MP steam header letdown valve", "%", 0.0, 100.0, 0.0);
    ao("TCV-7001", "B1 desuperheater spray valve", "%", 0.0, 100.0, 20.0);
    ao("LCV-7001", "Deaerator makeup water valve", "%", 0.0, 100.0, 40.0);
    di("BS-7001", "B1 main flame detected", "No flame", "Flame");
    di("LSLL-7001", "B1 steam drum level low low", "Normal", "Tripped");
    di("LSHH-7001", "B1 steam drum level high high", "Normal", "Tripped");
    di("PSHH-7001", "B1 steam drum pressure high high", "Normal", "Tripped");
    di("XS-7010", "B1 furnace purge complete permissive", "Not purged", "Purged", true);
    do_("XY-7010", "B1 burner igniter energise command", "De-energise", "Energise", true);
    do_("XY-7011", "B1 furnace purge sequence start command", "Idle", "Start");
    xv7001 = std::make_unique<SdvPackage>(*this, "XV-7001", "B1 fuel gas shutdown valve", 1.0);
    IT7001 = &ai("IT-7001", "P-701A motor current", "A", 0, 300);
    IT7002 = &ai("IT-7002", "P-701B motor current", "A", 0, 300);
    di("ZSO-HV7001", "HV-7001 open limit switch", "Not open", "Open", false);
    di("ZSC-HV7001", "HV-7001 closed limit switch", "Not closed", "Closed", true);
    bfw_pumps = std::make_unique<PumpTrain>(*this, "P-701A", "P-701B", "B1 boiler feedwater pump", "MOV-7001A",
                                            "MOV-7001B", 760.0, 120.0, 12.0, 185.0, true, 20.0, true);
    fd_fan = std::make_unique<MotorPackage>(*this, "FD-701", "B1 forced draft fan", 280.0, true, 4.0);

    dyn("xv7001", *xv7001); dyn("bfw_pumps", *bfw_pumps); dyn("fd_fan", *fd_fan);
    dyn("v_bfw", v_bfw); dyn("v_fuel", v_fuel); dyn("v_damper", v_damper); dyn("oil_lag", oil_lag);
    dyn("b2_lag", b2_lag); dyn("v_blowdown", v_blowdown); dyn("v_letdown", v_letdown); dyn("v_spray", v_spray);
    dyn("v_da_makeup", v_da_makeup); dyn("inventory", inventory); dyn("bfw_flow", bfw_flow);
    dyn("drum_pressure", drum_pressure); dyn("header_pressure", header_pressure); dyn("da_level", da_level);
    dyn("steam", steam); dyn("swell", swell); dyn("superheat", superheat); dyn("flue_temp", flue_temp);
    dyn("o2", o2); dyn("co", co); dyn("conductivity", conductivity);
    dyn("tx_level", tx_level); dyn("tx_steam", tx_steam); dyn("tx_o2", tx_o2);

    add_malfunction("MF-027", "P-701A", "Boiler feedwater pump trip", "Rotating", "", 0, 1,
                    [this](bool a, double) { bfw_pumps->motor_a.device.trip_on_overload = a; });
    add_malfunction("MF-037", "B1", "Boiler tube leak", "Process", "Leak rate", 0, 5,
                    [this](bool a, double v) { tube_leak = a ? v : 0.0; });
    add_malfunction("MF-038", "U700", "MP steam header demand swing", "Process", "Extra demand", 0, 20,
                    [this](bool a, double v) { bus_set("mp_extra_demand", a ? v : 0.0); });
}

void SteamBoiler::step(double dt) {
    const bool air = bus_get("air_failure") != 0.0;
    const bool esd = bus_get("esd_u700") != 0.0;
    v_bfw.step(dt, t("FCV-7001").effective(), air);
    v_fuel.step(dt, t("FCV-7002").effective(), air);
    v_damper.step(dt, t("FCV-7003").effective(), air);
    v_blowdown.step(dt, t("FCV-7004").effective(), air);
    v_letdown.step(dt, t("PCV-7001").effective(), air);
    v_spray.step(dt, t("TCV-7001").effective(), air);
    v_da_makeup.step(dt, t("LCV-7001").effective(), air);
    xv7001->step(dt, esd, air);

    // ---- firing
    const double supply_bara = bus_get("b1_supply_pressure_bara");
    double fuel = xv7001->is_open() ? v_fuel.gas_flow(supply_bara, P_STD, 0.65, 300.0) : 0.0;
    fuel = clamp(fuel, 0.0, 4000.0);
    bus_set("b1_fuel_demand", fuel);
    const bool low_pressure = supply_bara - P_STD < 1.2;
    const bool pilot = t("XY-7010").effective() != 0.0;
    if (esd || !xv7001->is_open() || low_pressure || fuel < 60.0) lit = false;
    else if (pilot && fuel > 80.0 && t("XS-7010").value != 0.0) lit = true;
    fd_fan->step(dt, !esd, t("SC-7001").effective(), esd);
    const double air_flow = AIR_MAX * (v_damper.position / 100.0) * (0.3 + 0.7 * fd_fan->speed() / 100.0);
    const double stoich = (fuel * STOICH_AIR + FT7005->value * 11.4) / 1000.0;
    const double excess = clamp(stoich > 1e-6 ? safe_div(air_flow - stoich, stoich, 1.0) : 1.0, -0.6, 3.0);
    const double eta = excess >= 0.0
        ? clamp(0.92 - 0.55 * py_pow(excess - 0.13, 2.0) - 0.16 * excess, 0.25, 0.92)
        : clamp(0.62 + 1.6 * excess, 0.15, 0.92);
    const double lhv = bus_get("fg_lhv");
    const double oil_cmd = clamp(t("FCV-7005").effective(), 0.0, 100.0);
    const double oil_kgh = oil_lag.step(lit ? 30.0 * oil_cmd : 0.0, dt);
    FT7005->set(clamp(oil_kgh, 0.0, 3000.0));
    const double duty_mw = lit ? (fuel * lhv + oil_kgh * 41.0) / 3600.0 * eta : 0.0;
    const double steam_capacity = clamp(duty_mw / 20.5 * DUTY_MAX, 0.0, DUTY_MAX);

    // ---- feedwater
    t("ZSO-HV7001").set(0.0);
    t("ZSC-HV7001").set(1.0);
    IT7001->set(bfw_pumps->motor_a.current());
    IT7002->set(bfw_pumps->motor_b.current());
    bfw_pumps->step(dt, PT7004->value + 0.1 + 1.4 * da_level.y / 100.0, FT7002->value, da_level.y > 8.0,
                    t("SC-7002").effective(), PT7004->value, 0.25, 60.0   /* suction line friction, u700_boiler.py */, esd);
    const double p_bfw = bfw_pumps->discharge_pressure(bfw_flow.y, 950.0);
    double feedwater = bfw_pumps->any_running()
        ? v_bfw.flow(std::max(p_bfw - (drum_pressure.y - P_STD), 0.0), 0.95) * 0.95
        : 0.0;
    feedwater = bfw_flow.step(clamp(feedwater, 0.0, 80.0), dt);
    const double blowdown = v_blowdown.flow(std::max(drum_pressure.y - P_STD, 0.0), 0.9) * 0.9;

    // ---- steam side: the drum, the superheater line and the header
    const double generated = steam.step(steam_capacity, dt);
    double steam_flow = STEAM_LINE_K * std::sqrt(std::max(drum_pressure.y - header_pressure.y, 0.0));
    steam_flow = clamp(steam_flow, 0.0, 90.0);
    const double reboiler_demand = bus_get("t1_steam_demand") + bus_get("t2_steam_demand");
    const double extra = bus_get("mp_extra_demand");
    const double letdown = v_letdown.gas_flow(header_pressure.y, P_STD, 0.62, 640.0) * 0.0009;
    const double header_out = reboiler_demand + extra + letdown;
    const double b2 = b2_lag.step(clamp((35.2 - (header_pressure.y - P_STD)) * 40.0, 0.0, 150.0), dt);
    FT7006->set(b2);
    drum_pressure.step((generated - steam_flow) * 0.055, dt);
    header_pressure.step((steam_flow + b2 - header_out) * 0.055 / MP_HEADER_VOLUME * 60.0, dt);
    bus_set("mp_steam_pressure_bara", header_pressure.y);
    bus_set("mp_steam_temperature", superheat.y);

    // ---- drum inventory and the shrink-and-swell that hides it
    const double net_mass = feedwater - steam_flow - blowdown - tube_leak;
    const double inventory_before = inventory.y;
    inventory.step(net_mass / DRUM_VOLUME * 22.0 / 60.0, dt);
    const double drum_accumulation = (inventory.y - inventory_before) / std::max(dt, 1e-12) * DRUM_VOLUME * 60.0 / 22.0;
    record_balance("B1 drum mass inventory", feedwater, steam_flow + blowdown + tube_leak, drum_accumulation, "t/h",
                   1e-5);
    const double swell_y = swell.step(steam_flow, dt);
    const double indicated = clamp(inventory.y + SWELL_GAIN * (steam_flow - swell_y), -320.0, 320.0);

    // ---- deaerator
    const double makeup = v_da_makeup.flow(3.0, 1.0);
    const double da_before = da_level.y;
    da_level.step((makeup - feedwater) / 22.0 * 100.0 / 60.0, dt);
    record_inventory_balance("deaerator inventory", makeup, feedwater, da_before, da_level.y, 22.0, dt, 100.0, 60.0,
                             "m3/min");

    // ---- water chemistry, superheat and flue gas
    const double solids_in = feedwater * BFW_COND;
    const double solids_out = blowdown * conductivity.y;
    conductivity.step((solids_in - solids_out) * 0.5, dt);
    const double spray = v_spray.flow(std::max(p_bfw - (drum_pressure.y - P_STD), 0.0), 0.95);
    superheat.step(clamp(300.0 + duty_mw * 5.2 - spray * 3.4, 150.0, 445.0), dt);
    flue_temp.step(clamp(140.0 + duty_mw * 4.6 + excess * 40.0, 40.0, 495.0), dt);
    const double o2_ss = lit ? clamp(21.0 * excess / (1.0 + excess) * 0.95, 0.0, 12.0) : 20.9;
    const double co_ss = excess > 0.02 ? 20.0 : clamp(60.0 + 5200.0 * (0.02 - excess), 20.0, 2000.0);

    // ---- instruments
    { const double v_ = tx_level.step(dt, indicated); LT7001->set(v_, tx_level.quality); }
    LT7002->set(da_level.y);
    PT7001->set(clamp(drum_pressure.y - P_STD, 0, 60));
    PT7002->set(clamp(header_pressure.y - P_STD, 0, 45));
    PT7003->set(clamp(-2.0 - 0.09 * fd_fan->speed(), -25, 10));
    PT7004->set(1.2);
    { const double v_ = tx_steam.step(dt, steam_flow); FT7001->set(v_, tx_steam.quality); }
    FT7002->set(feedwater);
    FT7003->set(fuel);
    FT7004->set(air_flow);
    TT7001->set(superheat.y);
    TT7002->set(105.0);
    TT7003->set(flue_temp.y);
    { const double v_ = tx_o2.step(dt, o2.step(o2_ss, dt)); AT7001->set(v_, tx_o2.quality); }
    AT7002->set(co.step(lit ? co_ss : 0.0, dt));
    CT7001->set(conductivity.y);
    IT7003->set(fd_fan->current());
    ZT7001->set(v_bfw.position);
    t("BS-7001").set(b2d(lit));
    t("LSLL-7001").set(b2d(indicated < -200.0));
    t("LSHH-7001").set(b2d(indicated > 200.0));
    t("PSHH-7001").set(b2d(drum_pressure.y - P_STD > 52.0));
    t("XS-7010").set(b2d(t("XS-7010").value != 0.0 || t("XY-7011").effective() != 0.0));

    // the calculation trace: firing, the drum balance and the header
    tr("fuel", fuel); tr("oil_kgh", oil_kgh); tr("air_flow", air_flow); tr("stoich", stoich); tr("excess", excess);
    tr("eta", eta); tr("duty_mw", duty_mw); tr("steam_capacity", steam_capacity); tr("generated", generated);
    tr("steam", steam_flow); tr("feedwater", feedwater); tr("blowdown", blowdown); tr("tube_leak", tube_leak);
    tr("p_bfw", p_bfw); tr("drum_bara", drum_pressure.y); tr("header_bara", header_pressure.y);
    tr("reboiler_demand", reboiler_demand); tr("extra", extra); tr("letdown", letdown); tr("b2", b2);
    tr("inventory", inventory.y); tr("swell", steam_flow - swell_y); tr("indicated", indicated);
    tr("makeup", makeup); tr("da_level", da_level.y); tr("conductivity", conductivity.y); tr("spray", spray);
    tr("lit", b2d(lit)); tr("FCV-7001", v_bfw.position); tr("FCV-7002", v_fuel.position);
    tr("FCV-7003", v_damper.position); tr("fan_speed", fd_fan->speed());
}

Value SteamBoiler::save_state() const {
    Dict d;
    d["inv"] = inventory.y; d["pd"] = drum_pressure.y; d["ph"] = header_pressure.y; d["steam"] = steam.y;
    d["da"] = da_level.y; d["lit"] = lit ? 1.0 : 0.0; d["cond"] = conductivity.y;
    d["bfw"] = bfw_pumps->state(); d["oill"] = oil_lag.y; d["b2"] = b2_lag.y;
    return d;
}

void SteamBoiler::load_state(const Value& s) {
    inventory.reset(s.get_number("inv", 0.0));
    drum_pressure.reset(s.get_number("pd", 45.0));
    header_pressure.reset(s.get_number("ph", 37.0));
    steam.reset(s.get_number("steam", 0.0));
    da_level.reset(s.get_number("da", 55.0));
    conductivity.reset(s.get_number("cond", 1800.0));
    lit = s.get_number("lit", 0.0) != 0.0;
    bfw_pumps->restore(s.get("bfw"));
    oil_lag.reset(s.get_number("oill", 0.0));
    b2_lag.reset(s.get_number("b2", 0.0));
}

std::vector<ParameterSpec> SteamBoiler::parameters() const {
    return {{"AIR_MAX", AIR_MAX, "kNm3/h", "B1 maximum combustion-air flow", 0.0, 10000.0, true},
            {"DRUM_SPAN_MM", DRUM_SPAN_MM, "mm", "B1 indicated drum-level span", 1.0, 10000.0, true},
            {"DRUM_VOLUME", DRUM_VOLUME, "m3", "B1 steam-drum effective inventory volume", 0.1, 10000.0, true},
            {"DUTY_MAX", DUTY_MAX, "t/h", "B1 maximum steam generation", 0.0, 10000.0, true},
            {"MP_HEADER_VOLUME", MP_HEADER_VOLUME, "m3", "MP steam-header effective volume", 0.1, 100000.0, true},
            {"STEAM_LINE_K", STEAM_LINE_K, "t/h/sqrt(bar)", "B1 superheater line conductance", 0.0, 10000.0, true},
            {"SWELL_GAIN", SWELL_GAIN, "mm/(t/h)", "B1 shrink-and-swell inverse-response gain", -10000.0, 10000.0, true},
            {"SWELL_TAU", SWELL_TAU, "s", "B1 shrink-and-swell lag", 0.001, 100000.0, true}};
}

}  // namespace azeocore::units
