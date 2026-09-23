#include "azeocore/units/u500.hpp"

#include <algorithm>
#include <cmath>
#include <tuple>

namespace azeocore::units {

namespace {
constexpr double P_STD = 1.01325;
inline double b2d(bool b) { return b ? 1.0 : 0.0; }
}  // namespace

std::pair<double, double> solve_split(double feed_frac_dist, double z_feed, double sep_factor) {
    // bisection on the distillate purity: bounded, fixed cost, cannot diverge
    const double d = clamp(feed_frac_dist, 0.001, 0.999);
    const double z = clamp(z_feed, 0.001, 0.999);
    const double s = clamp(sep_factor, 1.0001, 1e9);
    auto residual = [&](double xd) {
        const double xb = clamp((z - d * xd) / (1.0 - d), 1e-6, 1.0 - 1e-6);
        return (xd / (1.0 - xd)) * ((1.0 - xb) / xb) - s;
    };
    double lo = std::max(z, 1e-4), hi = 1.0 - 1e-6;
    double xd;
    if (residual(lo) > 0.0) xd = lo;
    else if (residual(hi) < 0.0) xd = hi;
    else {
        for (int i = 0; i < 28; ++i) {
            const double mid = 0.5 * (lo + hi);
            if (residual(mid) < 0.0) lo = mid;
            else hi = mid;
        }
        xd = 0.5 * (lo + hi);
    }
    const double xb = clamp((z - d * xd) / (1.0 - d), 1e-6, 1.0 - 1e-6);
    return {xd, xb};
}

std::string DistillationColumn::tg(const char* prefix, const char* suffix) const {
    return std::string(prefix) + std::to_string(spec.n) + suffix;
}

DistillationColumn::DistillationColumn(TagDatabase& db_, ProcessBus& bus_, double dt_, const std::string& code_,
                                       const std::string& name_, const ColumnSpec& spec_)
    : ProcessUnit(db_, bus_, dt_, code_, name_), spec(spec_),
      v_reflux(tg("FCV-", "001"), 260, ValveChar::Linear, 5),
      v_dist(tg("FCV-", "002"), 60, ValveChar::Linear, 5),
      v_bot(tg("FCV-", "003"), 80, ValveChar::Linear, 5),
      v_steam(tg("FCV-", "004"), 330, ValveChar::Linear, 5),
      v_cw(tg("FCV-", "005"), 1000, ValveChar::Linear, 8, false),
      v_press(tg("PCV-", "001"), 70, ValveChar::Linear, 3),
      drum(50.0, 0.0, 100.0), sump(50.0, 0.0, 100.0), boot(30.0, 0.0, 100.0),
      pressure(spec_.p_nominal + P_STD, P_STD * 0.2, spec_.p_nominal + 8.0),
      boilup(35.0, 0.0), reflux_flow(1.5, 0.0), bot_flow(1.5, 0.0), t_cond(60.0, 48.0),
      hx_reboiler(std::string("E-") + std::to_string(spec_.n) + "02", spec_.reboiler_ua),
      hx_condenser(std::string("E-") + std::to_string(spec_.n) + "01", spec_.condenser_ua),
      tx_dist(tg("AT-", "001"), 0, 10, 25.0, 1.2, 300.0, 45.0),
      tx_bot(tg("AT-", "002"), 0, 10, 25.0, 1.2, 300.0, 45.0),
      tx_drum(tg("LT-", "001"), 0, 100, 1.2, 0.4),
      tx_pdt(tg("PDT-", "001"), 0, 500, 0.8, 0.8) {
    const std::string c = spec.column;
    const std::string n = std::to_string(spec.n);
    FT_feed = &ai(tg("FT-", "001"), c + " feed flow", "m3/h", 0, 300, 0.0);
    FT_reflux = &ai(tg("FT-", "002"), c + " reflux flow", "m3/h", 0, 400, 0.0);
    FT_dist = &ai(tg("FT-", "003"), c + " " + spec.dist_label, "m3/h", 0, 120, 0.0);
    FT_bot = &ai(tg("FT-", "004"), c + " " + spec.bot_label, "m3/h", 0, 200, 0.0);
    FT_steam = &ai(tg("FT-", "005"), c + " reboiler steam flow", "t/h", 0, 45, 0.0);
    FT_cw = &ai(tg("FT-", "006"), c + " condenser cooling water flow", "m3/h", 0, 1600, 0.0);
    FT_mf1 = &ai(tg("FT-", "007"), c + " reflux pump minimum flow", "m3/h", 0, 60, 0.0);
    FT_mf2 = &ai(tg("FT-", "008"), c + " bottoms pump minimum flow", "m3/h", 0, 50, 0.0);
    TT_feed = &ai(tg("TT-", "001"), c + " feed temperature", "degC", 0, 350, 60.0);
    TT_t1 = &ai(tg("TT-", "002"), c + " upper tray temperature", "degC", 0, 250, 130.0);
    TT_t2 = &ai(tg("TT-", "003"), c + " middle tray temperature", "degC", 0, 300, 170.0);
    TT_t3 = &ai(tg("TT-", "004"), c + " lower tray temperature", "degC", 0, 350, 210.0);
    TT_ovhd = &ai(tg("TT-", "005"), c + " overhead vapour temperature", "degC", 0, 250, 122.0);
    TT_bot = &ai(tg("TT-", "006"), c + " bottoms temperature", "degC", 0, 400, 240.0);
    TT_cond = &ai(tg("TT-", "007"), c + " condenser outlet temperature", "degC", 0, 150, 48.0);
    PT_ovhd = &ai(tg("PT-", "001"), c + " overhead pressure", "barg", 0, 15, spec.p_nominal);
    PT_bot = &ai(tg("PT-", "002"), c + " bottom pressure", "barg", 0, 16, spec.p_nominal + 0.3);
    PT_p1 = &ai(tg("PT-", "003"), c + " reflux pump discharge pressure", "barg", 0, 25, 0.0);
    PT_p2 = &ai(tg("PT-", "004"), c + " bottoms pump discharge pressure", "barg", 0, 25, 0.0);
    PDT = &ai(tg("PDT-", "001"), c + " column differential pressure", "mbar", 0, 500, 180.0);
    LT_drum = &ai(tg("LT-", "001"), c + " reflux drum level", "%", 0, 100, 50.0);
    LT_sump = &ai(tg("LT-", "002"), c + " column bottom level", "%", 0, 100, 50.0);
    if (spec.has_water_boot)
        LT_boot = &ai(tg("LT-", "003"), c + " reflux drum water boot level", "%", 0, 100, 30.0);
    AT_dist = &ai(tg("AT-", "001"), c + " distillate heavy key content", "mol%", 0, 10, 1.5);
    AT_bot = &ai(tg("AT-", "002"), c + " bottoms light key content", "mol%", 0, 10, 2.0);
    ZT = &ai(tg("ZT-", "001"), "FCV-" + n + "001 position feedback", "%", 0, 100, 0.0);
    ao(tg("FCV-", "001"), c + " reflux flow control valve", "%", 0.0, 100.0, 45.0);
    ao(tg("FCV-", "002"), c + " " + spec.dist_label + " control valve", "%", 0.0, 100.0, 35.0);
    ao(tg("FCV-", "003"), c + " " + spec.bot_label + " control valve", "%", 0.0, 100.0, 40.0);
    ao(tg("FCV-", "004"), c + " reboiler steam control valve", "%", 0.0, 100.0, 45.0);
    ao(tg("FCV-", "005"), c + " condenser cooling water valve", "%", 0.0, 100.0, 60.0);
    ao(tg("PCV-", "001"), c + " overhead pressure control valve", "%", 0.0, 100.0, 20.0);
    if (spec.has_water_boot) ao(tg("LCV-", "001"), c + " reflux drum water boot draw valve", "%", 0.0, 100.0, 0.0);
    di(tg("LSLL-", "001"), c + " reflux drum level low low", "Normal", "Tripped");
    di(tg("LSHH-", "001"), c + " reflux drum level high high", "Normal", "Tripped");
    di(tg("LSLL-", "002"), c + " column bottom level low low", "Normal", "Tripped");
    di(tg("PSHH-", "001"), c + " overhead pressure high high", "Normal", "Tripped");
    di(tg("UA-", "001"), c + " flooding detected", "Normal", "Flooding");
    IT_ra = &ai(tg("IT-", "001"), "P-" + n + "01A motor current", "A", 0, 150);
    IT_rb = &ai(tg("IT-", "002"), "P-" + n + "01B motor current", "A", 0, 150);
    IT_ba = &ai(tg("IT-", "003"), "P-" + n + "02A motor current", "A", 0, 150);
    IT_bb = &ai(tg("IT-", "004"), "P-" + n + "02B motor current", "A", 0, 150);
    di(tg("ZSO-HV", "001"), "HV-" + n + "001 open limit switch", "Not open", "Open", false);
    di(tg("ZSC-HV", "001"), "HV-" + n + "001 closed limit switch", "Not closed", "Closed", true);
    if (c == "T1") {
        di("ZSO-HV5002", "HV-5002 open limit switch", "Not open", "Open", false);
        di("ZSC-HV5002", "HV-5002 closed limit switch", "Not closed", "Closed", true);
        ao("PCV-5002", "T1 condenser hot gas bypass (split range 0-50%)", "%", 0.0, 100.0, 0.0);
    }
    xv_feed = std::make_unique<SdvPackage>(*this, tg("XV-", "001"), c + " feed shutdown valve", 3.0);
    reflux_pumps = std::make_unique<PumpTrain>(*this, "P-" + n + "01A", "P-" + n + "01B", c + " reflux pump",
                                               "MOV-" + n + "001A", "MOV-" + n + "001B", 58.0, 450.0, 20.0, 95.0,
                                               false, 25.0, true);
    bottoms_pumps = std::make_unique<PumpTrain>(*this, "P-" + n + "02A", "P-" + n + "02B", c + " bottoms pump",
                                                "MOV-" + n + "002A", "MOV-" + n + "002B", 88.0, 350.0, 18.0, 105.0,
                                                false, 25.0, true);
    mf1 = std::make_unique<ControlValve>(min_flow_valve(*this, tg("FCV-", "006"), c + " reflux pump minimum flow", 60));
    mf2 = std::make_unique<ControlValve>(min_flow_valve(*this, tg("FCV-", "007"), c + " bottoms pump minimum flow", 50));
    if (spec.has_water_boot) v_boot.emplace(tg("LCV-", "001"), 15, ValveChar::Linear, 4);
    {
        // flow the cooling water valve passes wide open, without disturbing its position
        const double held = v_cw.position;
        v_cw.position = 100.0;
        cw_max = std::max(v_cw.flow(2.4, 1.0), 1.0);
        v_cw.position = held;
    }

    dyn("xv_feed", *xv_feed); dyn("reflux_pumps", *reflux_pumps); dyn("bottoms_pumps", *bottoms_pumps);
    dyn("mf1", *mf1); dyn("mf2", *mf2);
    dyn("v_reflux", v_reflux); dyn("v_dist", v_dist); dyn("v_bot", v_bot); dyn("v_steam", v_steam);
    dyn("v_cw", v_cw); dyn("v_press", v_press);
    if (v_boot) dyn("v_boot", *v_boot);
    dyn("drum", drum); dyn("sump", sump); dyn("boot", boot); dyn("pressure", pressure);
    dyn("boilup", boilup); dyn("reflux_flow", reflux_flow); dyn("bot_flow", bot_flow); dyn("t_cond", t_cond);
    dyn("hx_reboiler", hx_reboiler); dyn("hx_condenser", hx_condenser);
    dyn("tx_dist", tx_dist); dyn("tx_bot", tx_bot); dyn("tx_drum", tx_drum); dyn("tx_pdt", tx_pdt);

    add_malfunction("MF-" + n + "01", tg("FCV-", "001"), "Reflux valve stiction", "Valve", "Stick band", 0, 10,
                    [this](bool a, double v) { v_reflux.stiction = a ? v : 0.0; });
    add_malfunction("MF-" + n + "02", tg("LT-", "001"), "Reflux drum level transmitter frozen", "Transmitter", "", 0, 1,
                    [this](bool a, double) { tx_drum.failure = a ? TxFailure::Frozen : TxFailure::None; });
    add_malfunction("MF-" + n + "03", c, "Tray fouling", "Process", "dP increase", 0, 60,
                    [this](bool a, double v) { fouling_trays = a ? v : 0.0; });
    add_malfunction("MF-" + n + "04", "E-" + n + "01", "Condenser fouling", "Process", "UA loss", 0, 60,
                    [this](bool a, double v) { hx_condenser.fouling_pct = a ? v : 0.0; });
    add_malfunction("MF-" + n + "06", "E-" + n + "02", "Reboiler fouling", "Process", "UA loss", 0, 80,
                    [this](bool a, double v) { hx_reboiler.fouling_pct = a ? v : 0.0; });
    add_malfunction("MF-" + n + "05", tg("AT-", "001"), "Analyser out of service", "Analyser", "", 0, 1,
                    [this](bool a, double) { tx_dist.failure = a ? TxFailure::OutOfService : TxFailure::None; });
}

void DistillationColumn::step(double dt) {
    const bool air = bus_get("air_failure") != 0.0;
    const bool esd = bus_get("esd_u500") != 0.0;
    const bool is_t1 = std::string(spec.column) == "T1";
    v_reflux.step(dt, t(tg("FCV-", "001")).effective(), air);
    v_dist.step(dt, t(tg("FCV-", "002")).effective(), air);
    v_bot.step(dt, t(tg("FCV-", "003")).effective(), air);
    v_steam.step(dt, t(tg("FCV-", "004")).effective(), air);
    v_cw.step(dt, t(tg("FCV-", "005")).effective(), air);
    v_press.step(dt, t(tg("PCV-", "001")).effective(), air);
    mf1->step(dt, t(tg("FCV-", "006")).effective(), air);
    mf2->step(dt, t(tg("FCV-", "007")).effective(), air);
    if (v_boot) v_boot->step(dt, t(tg("LCV-", "001")).effective(), air);
    xv_feed->step(dt, esd, air);
    auto [feed, t_feed, z_feed] = feed_stream();
    feed *= xv_feed->fraction();

    // ---- reboiler: steam off the shared header, through the foulable surface
    const double p_col = pressure.y;
    const double steam_p = bus_get("mp_steam_pressure_bara");
    const double drive = clamp(safe_div(steam_p - p_col - 2.0, 25.0, 0.0), 0.0, 1.0);
    const double steam_available = clamp(spec.steam_max * (v_steam.position / 100.0) * std::sqrt(drive), 0.0,
                                         spec.steam_max);
    const double duty_demand = steam_available * 1000.0 / 3600.0 * spec.steam_latent_kj_kg;
    const double t_steam = bus_get("mp_steam_temperature");
    const double duty_reboil = hx_reboiler.transfer(duty_demand, t_steam, TT_bot->value, v_steam.position / 100.0);
    const double steam = duty_reboil * 3600.0 / (1000.0 * spec.steam_latent_kj_kg);
    bus_set("t" + std::to_string(spec.n - 4) + "_steam_demand", steam);
    const double boilup_now = boilup.step(duty_reboil * 3600.0 / spec.latent_kj_per_m3, dt);

    // ---- condenser
    const double cw_dp = bus_get("cooling_water_dp_bar");
    const double cw = v_cw.flow(cw_dp, 1.0);
    const double t_cw = bus_get("cooling_water_temperature");
    const double v_top = std::max(boilup_now - q_cond, 0.0);
    hx_condenser.fouling_pct = std::min(hx_condenser.fouling_pct + 0.8 / 86400.0 * dt, HeatExchanger::MAX_FOULING);
    const double flooded = clamp((95.0 - drum.y) / 12.0, 0.05, 1.0);
    const double hgb = is_t1 ? t("PCV-5002").effective() / 100.0 : 0.0;
    const double cond_duty = hx_condenser.transfer(v_top * (1.0 - 0.6 * hgb) * spec.latent_kj_per_m3 / 3600.0,
                                                   TT_ovhd->value, t_cw, cw / cw_max * flooded);
    bus_set("t" + std::to_string(spec.n - 4) + "_cw_flow", cw);
    bus_set("t" + std::to_string(spec.n - 4) + "_cw_duty", cond_duty);
    const double condensed = std::min(v_top, cond_duty * 3600.0 / spec.latent_kj_per_m3);
    const double vent = v_press.gas_flow(p_col, P_STD, 2.6, 380.0) * 0.00018;
    const bool drum_primed = drum.y > 6.0;
    const bool sump_primed = sump.y > 6.0;
    const double p_col_g = p_col - P_STD;

    // ---- pumps and the product valves
    t(tg("ZSO-HV", "001")).set(0.0);
    t(tg("ZSC-HV", "001")).set(1.0);
    if (is_t1) {
        t("ZSO-HV5002").set(0.0);
        t("ZSC-HV5002").set(1.0);
    }
    IT_ra->set(reflux_pumps->motor_a.current());
    IT_rb->set(reflux_pumps->motor_b.current());
    IT_ba->set(bottoms_pumps->motor_a.current());
    IT_bb->set(bottoms_pumps->motor_b.current());
    reflux_pumps->step(dt, p_col_g + 0.05 + 0.9 * drum.y / 100.0, FT_reflux->value, drum_primed, 100.0, p_col_g, 0.25,
                       reflux_pumps->pump_a.flow_max * 0.7, esd);
    bottoms_pumps->step(dt, p_col_g + 0.10 + 1.2 * sump.y / 100.0, FT_bot->value, sump_primed, 100.0, p_col_g, 0.25,
                        bottoms_pumps->pump_a.flow_max * 0.7, esd);
    const double p_dis_1 = reflux_pumps->discharge_pressure(reflux_flow.y, 720.0);
    const double p_dis_2 = bottoms_pumps->discharge_pressure(bot_flow.y, 810.0);
    double reflux = reflux_pumps->any_running() ? v_reflux.flow(std::max(p_dis_1 - (p_col - P_STD) - 0.4, 0.0), 0.72)
                                                : 0.0;
    const double distillate = reflux_pumps->any_running() ? v_dist.flow(std::max(p_dis_1 - 3.0, 0.0), 0.72) : 0.0;
    double bottoms = bottoms_pumps->any_running() ? v_bot.flow(std::max(p_dis_2 - 4.0, 0.0), 0.81) : 0.0;
    reflux = reflux_flow.step(reflux, dt);
    bottoms = bot_flow.step(bottoms, dt);
    const double mf1_flow = mf1->flow(std::max(p_dis_1 - (p_col - P_STD) - 0.4, 0.0), 0.72);
    const double mf2_flow = mf2->flow(std::max(p_dis_2 - (p_col - P_STD) - 0.9, 0.0), 0.81);

    // ---- hydraulics: tray dP and flooding
    const double vapour_load = boilup_now + reflux * 0.15;
    double dp = (180.0 * py_pow(vapour_load / 300.0, 1.8) * (1.0 + fouling_trays / 100.0)) + 20.0;
    dp = clamp(dp, 0.0, 500.0);
    flooding = dp > spec.flood_dp;
    if (flooding) reflux *= 0.7;

    // ---- inventories: the drum, the sump, the boot and the vapour space
    const double drum_before = drum.y;
    const double sump_before = sump.y;
    const double drum_in = condensed + mf1_flow, drum_out = reflux + distillate + mf1_flow;
    const double feed_condensate = std::max(q_cond, 0.0);
    const double boot_share = v_boot ? spec.boot_water_frac : 0.0;
    const double sump_in = feed * (1.0 - boot_share) + reflux + feed_condensate + mf2_flow;
    const double sump_out = boilup_now + bottoms + mf2_flow;
    drum.step((drum_in - drum_out) / spec.drum_volume * 100.0 / 3600.0, dt);
    sump.step((sump_in - sump_out) / spec.sump_volume * 100.0 / 3600.0, dt);
    const std::string c = spec.column;
    record_inventory_balance(c + " reflux drum inventory", drum_in, drum_out, drum_before, drum.y, spec.drum_volume, dt);
    record_inventory_balance(c + " sump inventory", sump_in, sump_out, sump_before, sump.y, spec.sump_volume, dt);
    if (v_boot) {
        const double boot_in = feed * spec.boot_water_frac;
        const double boot_out = v_boot->flow(std::max(p_col - P_STD, 0.0), 1.0);
        boot.step((boot_in - boot_out) / 3.0 * 100.0 / 3600.0, dt);
    }
    const double d_p = ((v_top - condensed) * 0.30 - vent) * P_STD / spec.column_volume;
    pressure.step(d_p, dt);

    // ---- separation: the factor, the split, the component holdup
    const double t_bubble_feed = spec.t_light * z_feed + spec.t_heavy * (1.0 - z_feed);
    const double q_feed = clamp(1.0 + spec.cp_liq_kj_kg * 720.0 * std::max(t_bubble_feed - t_feed, 0.0)
                                    / spec.latent_kj_per_m3, 1.0, 1.25);
    const double internal_reflux = reflux + (q_feed - 1.0) * feed;
    const double r_ratio = safe_div(internal_reflux, std::max(distillate, 0.5), 0.0);
    double eta = 1.0 - py_exp(-0.42 * clamp(r_ratio, 0.0, 20.0));
    eta *= (1.0 - fouling_trays / 200.0);
    if (flooding) eta *= 0.55;
    if (boilup_now < spec.weep_boilup) eta *= clamp(boilup_now / spec.weep_boilup, 0.30, 1.0);
    const double t_mid_k = 0.5 * (TT_ovhd->value + TT_bot->value) + 273.15;
    const double t0_k = 0.5 * (spec.t_light + spec.t_heavy) + 273.15;
    const double alpha = spec.alpha * py_exp(spec.dh_alpha_k * (1.0 / clamp(t_mid_k, 250.0, 700.0) - 1.0 / t0_k));
    const double sep = py_pow(clamp(alpha, 1.05, 12.0), spec.stages * clamp(eta, 0.02, 1.0) * 0.55);
    const double phi = safe_div(boilup_now, std::max(boilup_now + bottoms, 1e-3), 0.5);
    const auto [yt, xbo] = solve_split(phi, x_col, sep);
    y_top = yt;
    x_bot_out = xbo;
    const double h = dt / 3600.0;
    const double v_drum0 = drum_before / 100.0 * spec.drum_volume;
    const double v_drum1 = drum.y / 100.0 * spec.drum_volume;
    const double v_col0 = sump_before / 100.0 * spec.sump_volume;
    const double v_col1 = sump.y / 100.0 * spec.sump_volume;
    const double x_drum0 = x_drum, x_col0 = x_col;
    const double drum_light_in = condensed * y_top + mf1_flow * x_drum0;
    const double drum_light_out = (reflux + distillate + mf1_flow) * x_drum0;
    const double col_light_in = (feed * z_feed + reflux * x_drum0 + feed_condensate * y_top + mf2_flow * x_col0);
    const double col_light_out = boilup_now * y_top + bottoms * x_bot_out + mf2_flow * x_col0;
    const double l_drum = v_drum0 * x_drum0 + (drum_light_in - drum_light_out) * h;
    const double l_col = v_col0 * x_col0 + (col_light_in - col_light_out) * h;
    if (v_drum1 > 0.05) x_drum = clamp(l_drum / v_drum1, 0.0, 1.0);
    if (v_col1 > 0.05) x_col = clamp(l_col / v_col1, 0.0, 1.0);
    record_balance(c + " drum light key", drum_light_in, drum_light_out, (v_drum1 * x_drum - v_drum0 * x_drum0) / h,
                   "m3/h light key", 1e-5, "component");
    record_balance(c + " column light key", col_light_in, col_light_out, (v_col1 * x_col - v_col0 * x_col0) / h,
                   "m3/h light key", 1e-5, "component");
    vapour_light = (v_top - condensed) * y_top;
    const double xr = y_top, xs = x_bot_out;
    const double xd = x_drum, xb = x_bot_out;
    q_cond = (q_feed - 1.0) * feed;
    const double p_corr = (pressure.y - (spec.p_nominal + P_STD)) * 7.5;
    auto bubble = [&](double x) { return spec.t_light * x + spec.t_heavy * (1.0 - x) + p_corr; };
    const double t_ovhd = bubble(xr);
    const double t_bot = bubble(xb);
    t_cond.step(clamp(38.0 + 24.0 * (1.0 - cw / 1600.0), 25.0, 140.0), dt);
    publish_products(distillate, bottoms);
    bus_set("t" + std::to_string(spec.n - 4) + "_pressure_bara", pressure.y);

    // ---- instruments
    FT_feed->set(feed);
    FT_reflux->set(reflux);
    FT_dist->set(distillate);
    FT_bot->set(bottoms);
    FT_steam->set(steam);
    FT_cw->set(cw);
    FT_mf1->set(mf1_flow);
    FT_mf2->set(mf2_flow);
    TT_feed->set(t_feed);
    TT_t1->set(clamp(bubble(0.5 * (xr + 0.85)), 0, 250));
    TT_t2->set(clamp(0.5 * (bubble(xr) + bubble(xs)), 0, 300));
    TT_t3->set(clamp(bubble(0.5 * (xs + 0.25)), 0, 350));
    TT_ovhd->set(clamp(t_ovhd, 0, 250));
    TT_bot->set(clamp(t_bot, 0, 400));
    TT_cond->set(t_cond.y);
    PT_ovhd->set(clamp(pressure.y - P_STD, 0, 15));
    PT_bot->set(clamp(pressure.y - P_STD + dp / 1000.0, 0, 16));
    PT_p1->set(clamp(p_dis_1, 0, 25));
    PT_p2->set(clamp(p_dis_2, 0, 25));
    { const double v_ = tx_pdt.step(dt, dp); PDT->set(v_, tx_pdt.quality); }
    { const double v_ = tx_drum.step(dt, drum.y); LT_drum->set(v_, tx_drum.quality); }
    LT_sump->set(sump.y);
    if (LT_boot) LT_boot->set(boot.y);
    { const double v_ = clamp(tx_dist.step(dt, clamp((1.0 - xd) * 100.0, 0, 10)), 0.0, 10.0); AT_dist->set(v_, tx_dist.quality); }
    { const double v_ = clamp(tx_bot.step(dt, clamp(xb * 100.0, 0, 10)), 0.0, 10.0); AT_bot->set(v_, tx_bot.quality); }
    ZT->set(v_reflux.position);
    t(tg("LSLL-", "001")).set(b2d(drum.y < 10.0));
    t(tg("LSHH-", "001")).set(b2d(drum.y > 90.0));
    t(tg("LSLL-", "002")).set(b2d(sump.y < 10.0));
    t(tg("PSHH-", "001")).set(b2d(pressure.y - P_STD > spec.p_nominal + 4.0));
    t(tg("UA-", "001")).set(b2d(flooding));

    // the calculation trace: the energy side, the hydraulics and the split
    tr("feed", feed); tr("t_feed", t_feed); tr("z_feed", z_feed); tr("steam", steam); tr("duty_reboil", duty_reboil);
    tr("boilup", boilup_now); tr("cw", cw); tr("v_top", v_top); tr("cond_duty", cond_duty); tr("condensed", condensed);
    tr("vent", vent); tr("reflux", reflux); tr("distillate", distillate); tr("bottoms", bottoms);
    tr("mf1", mf1_flow); tr("mf2", mf2_flow); tr("p_dis_1", p_dis_1); tr("p_dis_2", p_dis_2); tr("dp", dp);
    tr("flooding", b2d(flooding)); tr("drum", drum.y); tr("sump", sump.y); tr("boot", boot.y);
    tr("pressure_bara", pressure.y); tr("q_feed", q_feed); tr("r_ratio", r_ratio); tr("eta", eta);
    tr("alpha", alpha); tr("sep", sep); tr("phi", phi); tr("y_top", y_top); tr("x_bot_out", x_bot_out);
    tr("x_drum", x_drum); tr("x_col", x_col); tr("t_ovhd", t_ovhd); tr("t_bot", t_bot);
    tr("FCV-n001", v_reflux.position); tr("FCV-n004", v_steam.position); tr("FCV-n005", v_cw.position);
}

Value DistillationColumn::save_state() const {
    Dict d;
    d["drum"] = drum.y; d["sump"] = sump.y; d["p"] = pressure.y;
    d["xdr"] = x_drum; d["xcol"] = x_col; d["ytop"] = y_top; d["xbo"] = x_bot_out;
    d["boilup"] = boilup.y; d["qc"] = q_cond;
    d["rp"] = reflux_pumps->state(); d["bp"] = bottoms_pumps->state();
    return d;
}

void DistillationColumn::load_state(const Value& s) {
    drum.reset(s.get_number("drum", 50.0));
    sump.reset(s.get_number("sump", 50.0));
    pressure.reset(s.get_number("p", spec.p_nominal + P_STD));
    q_cond = s.get_number("qc", 0.0);
    x_drum = s.has("xdr") ? s.get_number("xdr", 0.985) : s.get_number("xd", 0.985);
    x_col = s.get_number("xcol", 0.5);
    y_top = s.get_number("ytop", x_drum);
    x_bot_out = s.has("xbo") ? s.get_number("xbo", 0.02) : s.get_number("xb", 0.02);
    boilup.reset(s.get_number("boilup", 0.0));
    reflux_pumps->restore(s.get("rp"));
    bottoms_pumps->restore(s.get("bp"));
}

std::vector<ParameterSpec> DistillationColumn::base_parameters() const {
    const ColumnSpec& p = spec;
    return {{"ALPHA", p.alpha, "ratio", "Nominal light/heavy relative volatility", 1.0, 100.0, true},
            {"BOOT_WATER_FRAC", p.boot_water_frac, "fraction",
             "Water fraction of the feed that settles to the reflux drum boot", 0.0, 0.05, true},
            {"COLUMN_VOLUME", p.column_volume, "m3", "Column vapour-space effective volume", 0.1, 100000.0, true},
            {"CONDENSER_UA", p.condenser_ua, "kW/K", "Clean condenser heat-transfer conductance", 0.0, 100000.0, true},
            {"CP_LIQ_KJ_KG", p.cp_liq_kj_kg, "kJ/(kg K)", "Column-liquid heat capacity", 0.01, 100.0, true},
            {"DH_ALPHA_K", p.dh_alpha_k, "K", "Relative-volatility temperature coefficient", -1e6, 1e6, true},
            {"DRUM_VOLUME", p.drum_volume, "m3", "Reflux-drum effective volume", 0.1, 10000.0, true},
            {"FLOOD_DP", p.flood_dp, "mbar", "Column flooding differential pressure", 0.0, 100000.0, true},
            {"LATENT_KJ_PER_M3", p.latent_kj_per_m3, "kJ/m3", "Column-liquid volumetric latent heat", 1.0, 1e9, true},
            {"N", double(p.n), "-", "Column tag-number prefix", 1.0, 99.0, true},
            {"P_NOMINAL", p.p_nominal, "barg", "Nominal column pressure", -1.0, 500.0, true},
            {"REBOILER_UA", p.reboiler_ua, "kW/K", "Clean reboiler heat-transfer conductance", 0.0, 100000.0, true},
            {"STAGES", p.stages, "count", "Theoretical contacting stages", 1.0, 500.0, true},
            {"STEAM_LATENT_KJ_KG", p.steam_latent_kj_kg, "kJ/kg", "MP steam latent heat", 1.0, 1e6, true},
            {"STEAM_MAX", p.steam_max, "t/h", "Maximum reboiler steam flow", 0.0, 10000.0, true},
            {"SUMP_VOLUME", p.sump_volume, "m3", "Column-sump effective volume", 0.1, 10000.0, true},
            {"T_HEAVY", p.t_heavy, "degC", "Heavy-key nominal boiling temperature", -273.15, 1000.0, true},
            {"T_LIGHT", p.t_light, "degC", "Light-key nominal boiling temperature", -273.15, 1000.0, true},
            {"WEEP_BOILUP", p.weep_boilup, "m3/h", "Minimum vapour traffic before tray weeping", 0.0, 10000.0, true}};
}

std::vector<ParameterSpec> DistillationColumn::parameters() const { return base_parameters(); }

// ------------------------------------------------------------------ T1

const ColumnSpec ColumnT1::SPEC = {
    5, "T1", 32.0, 2.6, 29.0, 0.004, 37.0, 65.0, 340.0, 620.0, 180000.0, 1950.0, 2400.0,
    65.0, 2.4, 8.0, 118.0, 268.0, 420.0, 40.0, true,
    "distillate to storage / T2 feed", "bottoms recycle to D1"};

ColumnT1::ColumnT1(TagDatabase& db_, ProcessBus& bus_, double dt_)
    : DistillationColumn(db_, bus_, dt_, "U500", "Distillation column T1", SPEC) {}

std::tuple<double, double, double> ColumnT1::feed_stream() {
    return {bus_get("d3_liquid_to_t1"), bus_get("d3_liquid_temperature"), bus_get("d3_liquid_lightfrac")};
}

void ColumnT1::publish_products(double distillate, double bottoms) {
    bus_set("t1_distillate", distillate);
    bus_set("t1_distillate_to_t2", distillate * SPLIT_TO_T2);
    bus_set("t1_distillate_to_storage", distillate * (1.0 - SPLIT_TO_T2));
    bus_set("t1_distillate_temperature", t_cond.y);
    bus_set("t1_bottoms_recycle", bottoms);
    bus_set("t1_bottoms_temperature", TT_bot->value);
}

std::vector<ParameterSpec> ColumnT1::parameters() const {
    auto out = base_parameters();
    // sorted by name, as discover_parameters lists them: SPLIT_TO_T2 sits between P_NOMINAL and STAGES
    out.insert(out.begin() + 12, ParameterSpec{"SPLIT_TO_T2", SPLIT_TO_T2, "fraction",
                                               "T1 distillate fraction routed to T2", 0.0, 1.0, true});
    return out;
}

// ------------------------------------------------------------------ T2

const ColumnSpec ColumnT2::SPEC = {
    6, "T2", 40.0, 1.9, 23.0, 0.004, 35.0, 75.0, 340.0, 600.0, 180000.0, 1950.0, 2400.0,
    65.0, 2.4, 5.5, 96.0, 232.0, 400.0, 30.0, false,
    "R2 product to blender", "heavy product rundown"};

ColumnT2::ColumnT2(TagDatabase& db_, ProcessBus& bus_, double dt_)
    : DistillationColumn(db_, bus_, dt_, "U600", "Distillation column T2", SPEC) {}

std::tuple<double, double, double> ColumnT2::feed_stream() {
    return {bus_get("t1_distillate_to_t2"), bus_get("t1_distillate_temperature"), 0.64};
}

void ColumnT2::publish_products(double distillate, double bottoms) {
    bus_set("r2_product", distillate);
    bus_set("t2_heavy_rundown", bottoms);
}

}  // namespace azeocore::units
