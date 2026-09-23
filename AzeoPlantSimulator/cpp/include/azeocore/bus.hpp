// The two buses: the virtual I/O bus (azeoplant/io) that is the one
// doorway into the plant for a DCS, and the process bus
// (azeoplant/models/accountability.py) that the units hand values across
// under a declared contract. The pieces that need Python callables
// (subscriptions, logging) live in the binding; the rules live here.
#pragma once

#include "azeocore/export.h"

#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <vector>

#include "azeocore/tags.hpp"

namespace azeocore {

// ------------------------------------------------------------ I/O bus
struct ForceRecord {
    std::string tag;
    double value = 0.0;
    std::string source;
    std::string reason;
    double t = 0.0;
    double true_value = 0.0;
};

struct BusStats {
    int sim_writes = 0, dcs_writes = 0, queued = 0, drained = 0, rejected_ownership = 0, rejected_holder = 0,
        rejected_value = 0, adjusted_writes = 0, coalesced_writes = 0, rejected_capacity = 0, pending_peak = 0,
        forces_applied = 0;
};

class AZEOCORE_API StaleTracker {
public:
    explicit StaleTracker(double timeout_s = 2.0) : timeout_s(timeout_s) {}
    double timeout_s;
    void touch(const std::string& tag, double now);
    void disarm(const std::string& tag);
    void disarm_all();
    std::vector<std::string> scan(double now);   // tags that just went stale
    bool is_stale(const std::string& tag) const;
    std::vector<std::string> armed() const;

private:
    std::map<std::string, double> last_;
    std::map<std::string, bool> stale_;
    std::vector<std::string> order_;
};

// --------------------------------------------------------- process bus
struct AZEOCORE_API BusSignalSpec {
    std::string name;
    double dflt = 0.0;
    std::string eu;
    std::string producer;
    std::vector<std::string> consumers;
    std::optional<double> lo;
    std::optional<double> hi;
    bool tear = false;
    std::string description;
    void validate() const;   // throws std::invalid_argument as the Python __post_init__ does
};

struct ProcessBusStats {
    int reads = 0, writes = 0, range_excursions = 0, producer_violations = 0, consumer_violations = 0,
        unknown_signals = 0, rejected_nonfinite = 0;
    std::string last_violation;
    std::map<std::string, int> excursions_by_signal;
};

struct BusContractError {
    std::string message;
};

class AZEOCORE_API ProcessBus {
public:
    explicit ProcessBus(const std::vector<BusSignalSpec>& specs);
    const std::map<std::string, BusSignalSpec>& specs() const { return specs_; }
    const std::vector<std::string>& order() const { return order_; }
    std::optional<std::string> active_source() const { return source_; }
    void set_source(std::optional<std::string> s) { source_ = std::move(s); }
    // the mapping semantics with the contract checks; both throw BusContractError
    double read(const std::string& name);
    void write(const std::string& name, double value);
    void restore(const std::string& name, double value);
    double raw(const std::string& name) const { return values_.at(name); }
    bool has(const std::string& name) const { return values_.count(name) != 0; }
    std::vector<std::string> current_range_issues() const;
    std::vector<std::string> validate_topology(const std::vector<std::string>& unit_order) const;
    ProcessBusStats stats;

private:
    std::map<std::string, BusSignalSpec> specs_;
    std::vector<std::string> order_;
    std::map<std::string, double> values_;
    std::optional<std::string> source_;
    bool system_source() const;
};

struct AZEOCORE_API BalanceReading {
    std::string unit;
    std::string name;
    double inflow = 0.0, outflow = 0.0, accumulation = 0.0, residual = 0.0, tolerance = 1e-6;
    std::string eu;
    std::string kind = "material";
    bool healthy() const;
};

AZEOCORE_API std::string format_g(double v);   // Python's %g

}  // namespace azeocore
