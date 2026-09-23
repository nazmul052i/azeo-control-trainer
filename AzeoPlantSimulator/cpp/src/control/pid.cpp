#include "azeocore/control/pid.hpp"

#include <algorithm>
#include <cmath>

namespace azeocore::control {

namespace {

struct StructureRow {
    std::optional<double> beta, gamma;
    bool has_p, has_i, has_d;
};

// the STRUCTURE table of the reference, as (beta, gamma, P, I, D)
const StructureRow& structure_row(Structure s) {
    static const StructureRow rows[] = {
        {1.0, 1.0, true, true, true},                  // PID action on error
        {1.0, 0.0, true, true, true},                  // PI action on error, D action on PV
        {0.0, 0.0, true, true, true},                  // I action on error, PD action on PV
        {1.0, 1.0, true, false, true},                 // PD action on error
        {1.0, 0.0, true, false, true},                 // P action on error, D action on PV
        {std::nullopt, 1.0, false, true, true},        // ID action on error
        {std::nullopt, 0.0, false, true, true},        // I action on error, D action on PV
        {std::nullopt, std::nullopt, true, true, true} // Two degrees of freedom
    };
    return rows[static_cast<int>(s)];
}

const char* const MODE_VALUES[] = {"OOS", "IMan", "LO", "Man", "Auto", "Cas", "RCas", "ROut"};
const char* const STRUCTURE_VALUES[] = {
    "PID action on error", "PI action on error, D action on PV", "I action on error, PD action on PV",
    "PD action on error", "P action on error, D action on PV", "ID action on error",
    "I action on error, D action on PV", "Two degrees of freedom"};

inline bool closed(Mode m) { return m == Mode::AUTO || m == Mode::CAS || m == Mode::RCAS; }

}  // namespace

const char* mode_value(Mode m) { return MODE_VALUES[static_cast<int>(m)]; }

std::optional<Mode> mode_from(std::string_view value) {
    for (int i = 0; i < 8; ++i)
        if (value == MODE_VALUES[i]) return static_cast<Mode>(i);
    return std::nullopt;
}

const char* structure_value(Structure s) { return STRUCTURE_VALUES[static_cast<int>(s)]; }

std::optional<Structure> structure_from(std::string_view value) {
    for (int i = 0; i < 8; ++i)
        if (value == STRUCTURE_VALUES[i]) return static_cast<Structure>(i);
    return std::nullopt;
}

// ------------------------------------------------------------------ BoolMap

bool BoolMap::get(std::string_view key, bool dflt) const {
    for (const auto& [k, v] : items)
        if (k == key) return v;
    return dflt;
}

bool BoolMap::has(std::string_view key) const {
    for (const auto& [k, v] : items)
        if (k == key) return true;
    return false;
}

void BoolMap::set(const std::string& key, bool value) {
    for (auto& [k, v] : items)
        if (k == key) { v = value; return; }
    items.emplace_back(key, value);
}

void BoolMap::erase(std::string_view key) {
    for (auto it = items.begin(); it != items.end(); ++it)
        if (it->first == key) { items.erase(it); return; }
}

Value BoolMap::to_value() const {
    Dict d;
    for (const auto& [k, v] : items) d[k] = v;
    return d;
}

double NumMap::get(std::string_view key, double dflt) const {
    for (const auto& [k, v] : items)
        if (k == key) return v;
    return dflt;
}

void NumMap::set(const std::string& key, double value) {
    for (auto& [k, v] : items)
        if (k == key) { v = value; return; }
    items.emplace_back(key, value);
}

Value NumMap::to_value() const {
    Dict d;
    for (const auto& [k, v] : items) d[k] = v;
    return d;
}

// ------------------------------------------------------------------ PID

PID::PID() {
    for (const char* n : {"hi_hi", "hi", "lo", "lo_lo", "dv_hi", "dv_lo"}) alarm_enab.set(n, true);
}

void PID::post_init() {
    if (pv_ftime > 0.0) pv_filter.emplace(pv_ftime, pv);
    else pv_filter.reset();
    pv_filter_primed = false;
    reset_fb = Lag(std::max(reset, 1e-3), out);
    d_prev.reset();
    sp_suppress_dev = 0.0;
    out_limited.clear();
    actual = Mode::MAN;
    sp_wrk = sp;
    was_closed = false;
}

std::string PID::bkcal_limit() const {
    if (!closed(actual)) return "constant";
    return out_limited;
}

double PID::step(double dt, double pv_in, bool pv_good_in, std::optional<double> cas_in,
                 std::optional<double> bkcal_in, const std::string& /*bkcal_in_limit*/, double ff_val) {
    field_value = pv_in;
    field_good = pv_good_in;
    double pv_now = pv_in;
    bool good = pv_good_in;
    if (simulate_enable) {
        pv_now = simulate_value;
        good = true;
    }
    if (pv_filter) {
        if (!pv_filter_primed) {
            pv_filter->reset(pv_now);
            pv_filter_primed = true;
        }
        pv_now = pv_filter->step(pv_now, dt);
    }
    pv = pv_now;
    pv_good = good;
    if (target_mode == Mode::OOS) {
        actual = Mode::OOS;
        return out;
    }
    const Mode want = target_mode;
    if (!good && closed(want)) actual = Mode::MAN;
    else actual = want;
    const Mode mode = actual;
    if ((mode == Mode::CAS || mode == Mode::RCAS) && cas_in) sp = *cas_in;
    else if (mode == Mode::MAN && sp_pv_track_in_man) sp = pv_now;
    double sp_target = clamp(sp, sp_lo_lim, sp_hi_lim);
    if (was_closed && closed(mode) && (sp_rate_up > 0.0 || sp_rate_dn > 0.0)) {
        const double step_up = (sp_rate_up != 0.0 ? sp_rate_up : INFINITY) * dt;
        const double step_dn = (sp_rate_dn != 0.0 ? sp_rate_dn : INFINITY) * dt;
        sp_target = clamp(sp_target, sp_wrk - step_dn, sp_wrk + step_up);
    }
    if (sp_ftime > 0.0 && was_closed) {
        const double a = 1.0 - py_exp(-dt / std::max(sp_ftime, 1e-6));
        sp_target = sp_wrk + (sp_target - sp_wrk) * a;
    }
    sp_wrk = sp_target;
    alarm_scan(dt);
    if (mode == Mode::MAN || mode == Mode::ROUT || mode == Mode::LO || mode == Mode::IMAN) {
        out = clamp(out, out_lo_lim, out_hi_lim);
        d_prev.reset();
        out_limited.clear();
        was_closed = false;
        return out;
    }
    const StructureRow& row = structure_row(structure);
    const double b = row.beta ? *row.beta : beta;
    const double g = row.gamma ? *row.gamma : gamma;
    const double sp_pct = pct(sp_wrk), pv_pct = pct(pv_now);
    const double sign = direct_acting ? 1.0 : -1.0;
    double p_term = gain * sign * (pv_pct - b * sp_pct);
    double d_term = 0.0;
    if (row.has_d && rate > 0.0) {
        const double d_input = sign * (pv_pct - g * sp_pct);
        if (!d_prev) d_prev = d_input;
        const double alpha = dt / std::max(rate / 8.0, dt);
        const double d_raw = (d_input - *d_prev) / std::max(dt, 1e-9);
        d_term = gain * rate * d_raw * std::min(alpha, 1.0);
        d_prev = d_input;
    }
    if (!row.has_p) p_term = 0.0;
    const double ff = ff_enable ? ff_gain * ff_val : 0.0;
    double i_term;
    if (row.has_i && reset > 0.0 && std::isfinite(reset)) {
        reset_fb.tau = std::max(reset, 1e-3);
        const double feedback = bkcal_in ? *bkcal_in : out;
        i_term = reset_fb.step(feedback - ff, dt);
    } else {
        i_term = reset_fb.y;   // frozen: P/PD structures hold bias
    }
    if (!was_closed) {
        reset_fb.reset(out - p_term - ff);
        i_term = reset_fb.y;
        was_closed = true;
    }
    const double unlimited = p_term + d_term + i_term + ff;
    out = clamp(unlimited, out_lo_lim, out_hi_lim);
    if (unlimited > out_hi_lim - 1e-9) out_limited = "high";
    else if (unlimited < out_lo_lim + 1e-9) out_limited = "low";
    else out_limited.clear();
    if (!bkcal_in && !out_limited.empty())
        reset_fb.reset(clamp(reset_fb.y, arw_lo_lim - std::fabs(p_term) - 1.0, arw_hi_lim + std::fabs(p_term) + 1.0));
    return out;
}

void PID::alarm_scan(double dt) {
    const double hys = alarm_hys / 100.0 * pv_span();
    const double pv_now = pv;
    auto latch = [&](bool active, double level, bool above) {
        if (above) return !active ? pv_now >= level : pv_now > level - hys;
        return !active ? pv_now <= level : pv_now < level + hys;
    };
    // the on-delay: the condition must stand for its delay before the
    // indication shows, and the timer clears the moment it drops
    auto timed = [&](const char* key, bool cond) {
        const double delay = alarm_delay.get(key, 0.0);
        if (!cond) { alarm_timer.set(key, 0.0); return false; }
        if (delay <= 0.0) return true;
        const double held = std::min(alarm_timer.get(key, 0.0) + dt, delay);
        alarm_timer.set(key, held);
        return held >= delay;
    };
    PidAlarms& a = alarms;
    const BoolMap& en = alarm_enab;
    a.hi_hi = timed("hi_hi", latch(a.hi_hi, hi_hi_lim, true)) && en.get("hi_hi", true);
    a.hi = timed("hi", latch(a.hi, hi_lim, true)) && en.get("hi", true);
    a.lo = timed("lo", latch(a.lo, lo_lim, false)) && en.get("lo", true);
    a.lo_lo = timed("lo_lo", latch(a.lo_lo, lo_lo_lim, false)) && en.get("lo_lo", true);
    sp_suppress_dev = std::max(0.0, sp_suppress_dev - dt);
    const double dev = pv_now - sp_wrk;
    if (sp_suppress_dev <= 0.0) {
        a.dv_hi = timed("dv_hi", std::isfinite(dv_hi_lim) ? latch(a.dv_hi, sp_wrk + dv_hi_lim, true) : false);
        a.dv_lo = timed("dv_lo", std::isfinite(dv_lo_lim) ? latch(a.dv_lo, sp_wrk + dv_lo_lim, false) : false);
    } else if (std::fabs(dev) < std::min(std::fabs(dv_hi_lim), std::fabs(dv_lo_lim))) {
        sp_suppress_dev = 0.0;
    }
}

Value PID::capture_state() const {
    Dict d;
    d["sp"] = sp; d["spw"] = sp_wrk; d["out"] = out;
    d["mode"] = std::string(mode_value(target_mode));
    d["pv"] = pv; d["pvg"] = pv_good;
    d["fb"] = reset_fb.y; d["closed"] = was_closed;
    d["dprev"] = d_prev ? Value(*d_prev) : Value(nullptr);
    d["sup"] = sp_suppress_dev;
    d["gain"] = gain; d["reset"] = reset; d["rate"] = rate;
    d["sim"] = simulate_enable; d["simv"] = simulate_value;
    d["arwh"] = arw_hi_lim; d["arwl"] = arw_lo_lim;
    d["splo"] = sp_lo_lim; d["sphi"] = sp_hi_lim;
    d["spft"] = sp_ftime;
    d["enab"] = alarm_enab.to_value();
    d["shlv"] = alarm_shelved.to_value();
    d["adly"] = alarm_delay.to_value();
    d["atmr"] = alarm_timer.to_value();
    return d;
}

void PID::apply_state(const Value& s) {
    sp = s.get_number("sp", sp);
    sp_wrk = s.get_number("spw", sp);
    out = s.get_number("out", out);
    pv_filter_primed = false;
    if (s.has("mode")) {
        if (auto m = mode_from(s.get_string("mode", mode_value(target_mode)))) target_mode = *m;
    }
    pv = s.get_number("pv", pv);
    pv_good = s.get_bool("pvg", true);
    reset_fb.reset(s.get_number("fb", out));
    was_closed = s.get_bool("closed", false);
    if (s.has("dprev") && !s.get("dprev").is_null()) d_prev = s.get("dprev").number(0.0);
    else d_prev.reset();
    sp_suppress_dev = s.get_number("sup", 0.0);
    gain = s.get_number("gain", gain);
    reset = s.get_number("reset", reset);
    rate = s.get_number("rate", rate);
    simulate_enable = s.get_bool("sim", false);
    simulate_value = s.get_number("simv", simulate_value);
    arw_hi_lim = s.get_number("arwh", arw_hi_lim);
    arw_lo_lim = s.get_number("arwl", arw_lo_lim);
    sp_lo_lim = s.get_number("splo", sp_lo_lim);
    sp_hi_lim = s.get_number("sphi", sp_hi_lim);
    sp_ftime = s.get_number("spft", sp_ftime);
    if (s.has("enab") && s.get("enab").is_dict()) {
        for (const auto& [k, v] : s.get("enab").dict()) alarm_enab.set(k, v.boolean(true));
    }
    if (s.has("shlv") && s.get("shlv").is_dict()) {
        alarm_shelved.clear();
        for (const auto& [k, v] : s.get("shlv").dict()) alarm_shelved.set(k, v.boolean(false));
    }
    if (s.has("adly") && s.get("adly").is_dict()) {
        alarm_delay.clear();
        for (const auto& [k, v] : s.get("adly").dict()) alarm_delay.set(k, v.number(0.0));
    }
    alarm_timer.clear();
    if (s.has("atmr") && s.get("atmr").is_dict()) {
        for (const auto& [k, v] : s.get("atmr").dict()) alarm_timer.set(k, v.number(0.0));
    }
}

}  // namespace azeocore::control
