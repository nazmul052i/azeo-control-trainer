// The Azeo PID function block: the C++ twin of azeoplant/control/pid.py.
//
// Everything the Python block does, in the same order with the same
// arithmetic: the eight modes with the shed to Man on a bad PV, the
// standard form with GAIN normalised to percent of span, the reset as a
// positive-feedback network (so external reset falls out of it), the
// STRUCTURE table through BETA and GAMMA, SP and OUT limits, SP rate
// limiting and filtering, direct or reverse action, SP-PV tracking in
// Man, feedforward, PV filtering, BKCAL_OUT, and the absolute and
// deviation alarms with hysteresis and the post-SP-change suppression.
#pragma once

#include "azeocore/export.h"

#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "azeocore/dynamics.hpp"
#include "azeocore/state.hpp"

namespace azeocore::control {

enum class Mode { OOS, IMAN, LO, MAN, AUTO, CAS, RCAS, ROUT };
AZEOCORE_API const char* mode_value(Mode m);                       // the Python enum's value, "Auto"
AZEOCORE_API std::optional<Mode> mode_from(std::string_view value);

enum class Structure {
    PID_ON_ERROR, PI_ERROR_D_PV, I_ERROR_PD_PV, PD_ON_ERROR, P_ERROR_D_PV, ID_ON_ERROR, I_ERROR_D_PV, TWO_DEGREES
};
AZEOCORE_API const char* structure_value(Structure s);
AZEOCORE_API std::optional<Structure> structure_from(std::string_view value);

struct AZEOCORE_API PidAlarms {
    bool hi_hi = false, hi = false, lo = false, lo_lo = false, dv_hi = false, dv_lo = false;
    bool any_active() const { return hi_hi || hi || lo || lo_lo || dv_hi || dv_lo; }
};

// an insertion-ordered {name: bool} map, what the Python block keeps in a dict
struct AZEOCORE_API BoolMap {
    std::vector<std::pair<std::string, bool>> items;
    bool get(std::string_view key, bool dflt) const;
    bool has(std::string_view key) const;
    void set(const std::string& key, bool value);
    void erase(std::string_view key);
    void clear() { items.clear(); }
    Value to_value() const;
};

// the same shape holding a number per alarm: the on-delays and their timers
struct AZEOCORE_API NumMap {
    std::vector<std::pair<std::string, double>> items;
    double get(std::string_view key, double dflt) const;
    void set(const std::string& key, double value);
    void clear() { items.clear(); }
    Value to_value() const;
};

class AZEOCORE_API PID {
public:
    std::string name = "PID";
    std::string description;
    double pv_eu0 = 0.0, pv_eu100 = 100.0;
    double out_eu0 = 0.0, out_eu100 = 100.0;
    double gain = 1.0;           // normalised, percent of span per percent
    double reset = 100.0;        // seconds; 0 or inf disables integral
    double rate = 0.0;           // seconds
    Structure structure = Structure::PI_ERROR_D_PV;
    double beta = 1.0, gamma = 0.0;
    double pv_ftime = 0.0, sp_ftime = 0.0;
    double sp_hi_lim = INFINITY, sp_lo_lim = -INFINITY;
    double sp_rate_up = 0.0, sp_rate_dn = 0.0;   // EU/s, 0 disables
    double out_hi_lim = 100.0, out_lo_lim = 0.0;
    bool direct_acting = false;
    bool sp_pv_track_in_man = true;
    bool use_pv_for_bkcal = false;
    bool ff_enable = false;
    double ff_gain = 0.0;
    double hi_hi_lim = INFINITY, hi_lim = INFINITY, lo_lim = -INFINITY, lo_lo_lim = -INFINITY;
    double dv_hi_lim = INFINITY, dv_lo_lim = -INFINITY;
    double alarm_hys = 0.5;      // percent of PV span
    double arw_hi_lim = 100.0, arw_lo_lim = 0.0;
    bool simulate_enable = false;
    double simulate_value = 0.0;
    Mode target_mode = Mode::MAN;
    double sp = 0.0, sp_wrk = 0.0, out = 0.0, pv = 0.0;
    bool pv_good = true;
    double field_value = 0.0;
    bool field_good = true;
    PidAlarms alarms;
    BoolMap alarm_enab, alarm_shelved;
    NumMap alarm_delay;          // per-alarm on-delay, seconds

    // the dataclass's __post_init__ state
    std::optional<Lag> pv_filter;
    bool pv_filter_primed = false;
    Lag reset_fb{100.0, 0.0};
    std::optional<double> d_prev;
    double sp_suppress_dev = 0.0;
    NumMap alarm_timer;          // how long each alarm condition has stood
    std::string out_limited;     // "", "high", "low"
    Mode actual = Mode::MAN;
    bool was_closed = false;

    PID();
    void post_init();            // after the fields are set: filters, the reset network, sp_wrk

    double pv_span() const { return std::max(pv_eu100 - pv_eu0, 1e-9); }
    double pct(double eu) const { return (eu - pv_eu0) / pv_span() * 100.0; }
    double out_eu() const { return out_eu0 + out / 100.0 * (out_eu100 - out_eu0); }
    Mode actual_mode() const { return actual; }
    void set_mode(Mode m) { target_mode = m; }
    double bkcal_out() const { return use_pv_for_bkcal ? pv : sp_wrk; }
    std::string bkcal_limit() const;

    double step(double dt, double pv, bool pv_good = true, std::optional<double> cas_in = std::nullopt,
                std::optional<double> bkcal_in = std::nullopt, const std::string& bkcal_in_limit = "",
                double ff_val = 0.0);
    void note_sp_change(double seconds = 60.0) { sp_suppress_dev = seconds; }
    Value capture_state() const;
    void apply_state(const Value& s);

private:
    void alarm_scan(double dt);
};

}  // namespace azeocore::control
