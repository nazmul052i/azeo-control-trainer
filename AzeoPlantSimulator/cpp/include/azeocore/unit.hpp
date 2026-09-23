// The process-unit base: the C++ twin of azeoplant/models/base.py.
//
// A unit owns its equipment, its tags and its malfunctions, reads and
// writes the process bus for anything that crosses a battery limit, and
// contains no controller. Its snapshot is the same dictionary the Python
// unit writes: the plain values from save_state, every dynamic member
// under "_dyn" by attribute name, the active malfunctions under "_mf".
// Python finds the dynamic members by reflection; a native unit
// registers them in build(), by the same names.
#pragma once

#include "azeocore/export.h"

#include <functional>
#include <map>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "azeocore/bus.hpp"
#include "azeocore/log.hpp"
#include "azeocore/state.hpp"
#include "azeocore/tags.hpp"

namespace azeocore {

class AZEOCORE_API Transmitter;
struct AZEOCORE_API ControlValve;

struct AZEOCORE_API Malfunction {
    std::string mf_id;
    std::string target;
    std::string description;
    std::string category;
    std::string param_label;
    double param_min = 0.0;
    double param_max = 100.0;
    std::function<void(bool, double)> apply;
    bool active = false;
    double value = 0.0;
    void set(bool active_, std::optional<double> value_ = std::nullopt);
};

struct ParameterSpec {
    std::string name;
    double value;
    std::string eu;
    std::string description;
    std::optional<double> lo;
    std::optional<double> hi;
    bool documented = false;
};

class AZEOCORE_API ProcessUnit : public Stateful {
public:
    ProcessUnit(TagDatabase& db, ProcessBus& bus, double dt, std::string code, std::string name);
    ~ProcessUnit() override = default;

    const std::string code;
    const std::string name;
    TagDatabase& db;
    ProcessBus& bus;
    double dt;
    std::map<std::string, Tag*> tags;
    std::vector<std::string> tag_order;
    std::vector<Malfunction> malfunctions;

    virtual void step(double dt) = 0;
    // What the engine calls: clears the trace, steps, appends the balance
    // residuals, and logs the frame at debug level when tracing is on.
    void run_step(double dt);

    // the calculation trace (see log.hpp)
    TraceFrame trace;
    bool trace_enabled = false;
    int trace_every = 10;
    unsigned long long step_count = 0;
    const std::string logger;   // "azeoplant.models.<code>"
    void tr(const char* name_, double value) { if (trace_enabled) trace.put(name_, value); }

    // plain values not held by tags or dynamic members
    virtual Value save_state() const { return Dict{}; }
    virtual void load_state(const Value&) {}
    virtual std::vector<ParameterSpec> parameters() const { return {}; }

    Value capture() const;
    virtual void apply(const Value& state);   // a unit may post-process a restore, as U400 does
    Value capture_state() const override { return capture(); }
    void apply_state(const Value& s) override { apply(s); }
    void clear_malfunctions();

    const std::vector<BalanceReading>& balance_readings() const { return balances_; }

    // public, as in Python: the packages create their tags through them
    Tag& ai(const std::string& name, const std::string& desc, const std::string& eu, double lo, double hi,
            std::optional<double> value = std::nullopt);
    Tag& ao(const std::string& name, const std::string& desc, const std::string& eu = "%", double lo = 0.0,
            double hi = 100.0, double value = 0.0);
    Tag& di(const std::string& name, const std::string& desc, const std::string& state0 = "Off",
            const std::string& state1 = "On", bool value = false);
    Tag& do_(const std::string& name, const std::string& desc, const std::string& state0 = "Idle",
             const std::string& state1 = "Cmd", bool value = false);
    Malfunction& add_malfunction(const std::string& mf_id, const std::string& target, const std::string& description,
                                 const std::string& category, const std::string& param_label = "",
                                 double param_min = 0.0, double param_max = 100.0,
                                 std::function<void(bool, double)> apply = nullptr);
    // a dynamic member, by the attribute name its Python twin has
    void dyn(const std::string& attr, Stateful& member);
    // the instruments among the registered members, by attribute name:
    // what the realism layer (azeoplant/core/instruments.py) walks
    std::vector<std::pair<std::string, Transmitter*>> transmitters() const;
    std::vector<std::pair<std::string, ControlValve*>> valves() const;
    BalanceReading& record_balance(const std::string& name, double inflow, double outflow, double accumulation,
                                   const std::string& eu, double tolerance = 1e-6, const std::string& kind = "material");
    BalanceReading& record_inventory_balance(const std::string& name, double inflow, double outflow, double before,
                                             double after, double capacity, double dt, double state_span = 100.0,
                                             double time_base_s = 3600.0, const std::string& eu = "m3/h",
                                             double tolerance = 1e-5);
    double bus_get(const std::string& name) { return bus.read(name); }
    void bus_set(const std::string& name, double value) { bus.write(name, value); }

private:
    std::vector<std::pair<std::string, Stateful*>> dynamics_;
    std::vector<BalanceReading> balances_;
    std::map<std::string, std::size_t> balance_index_;
};

inline std::string base_of(const std::string& tag) {
    std::string out;
    for (char c : tag) if (c != '-') out.push_back(c);
    return out;
}

}  // namespace azeocore
