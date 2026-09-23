// The virtual I/O bus: the one doorway into the plant for a DCS, in the
// library. The C++ twin of azeoplant/io/bus.py: signal ownership by tag
// kind, one holder of the output side, checked immediate writes, a
// coalescing write queue drained on the engine step, audited forces,
// stale timers, and the state a snapshot carries. Everything that needs
// Python, the sample and force types handed back, the exceptions raised,
// the subscriber callbacks, stays in the binding, which wraps this class
// and installs on_change to dispatch its subscribers.
#pragma once

#include "azeocore/export.h"

#include <functional>
#include <map>
#include <mutex>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "azeocore/bus.hpp"
#include "azeocore/state.hpp"
#include "azeocore/tags.hpp"

namespace azeocore {

// a value as a client handed it over, parsed once at the doorway
struct PendingValue {
    bool numeric = false;   // could be read as a number (bool, int, float)
    double number = 0.0;
    bool truthy = false;    // Python's bool() of it, for discretes
};

enum class WriteVerdict { Applied, RejectedOwnership, RejectedHolder, RejectedValue };

struct IOSample {
    std::string name;
    double value = 0.0;
    bool analogue = true;
    int quality = 0;
    double ts = 0.0;
    TagKind kind = TagKind::AI;
    std::string unit, eu;
    double lo = 0.0, hi = 100.0;
};

struct QueueHealth {
    int pending = 0, capacity = 0, peak = 0, coalesced = 0, rejected_capacity = 0;
};

class AZEOCORE_API IOBus {
public:
    static constexpr int MAX_PENDING_WRITES = 4096;

    explicit IOBus(TagDatabase& db, double stale_timeout_s = 2.0);
    IOBus(const IOBus&) = delete;
    IOBus& operator=(const IOBus&) = delete;

    TagDatabase& db;
    BusStats stats;
    StaleTracker stale;
    std::optional<std::string> holder;
    // called after every applied DCS write, with the tag name; the binding
    // dispatches the Python subscribers from it
    std::function<void(const std::string&)> on_change;

    static bool dcs_owned(const Tag& t) { return kind_dcs_writable(t.kind); }
    static IOSample sample_of(const Tag& t);

    // ------------------------------------------------------------ reading
    IOSample sample(const std::string& tag) const;                       // throws std::out_of_range
    std::vector<IOSample> samples(const std::vector<std::string>* names) const;   // all tags when null
    const char* owner(const std::string& tag) const;                     // "DCS" or "SIMULATOR"

    // ----------------------------------------------------------- the holder
    bool register_dcs(const std::string& name);
    void release_dcs(const std::string& name);

    // ------------------------------------------------------------ writing
    WriteVerdict write_from_dcs(const std::string& tag, const PendingValue& value, const std::string& source);
    bool authorize_dcs_write(const std::string& tag, const std::string& source);
    void queue_write(const std::string& tag, const PendingValue& value, const std::string& source);   // std::length_error at capacity
    int drain_writes();
    QueueHealth queue_health();

    // ------------------------------------------------------------- forces
    void force(const std::string& tag, double value, const std::string& source, const std::string& reason);
    void simulate_input(const std::string& tag, double value, int quality, const std::string& source);
    void release(const std::string& tag);
    void release_all();
    std::vector<ForceRecord> active_forces() const;

    // ---------------------------------------------------------- the step
    void tick();

    // -------------------------------------------------------------- state
    Value capture_state() const;
    void apply_state(const Value& s);

private:
    std::map<std::string, ForceRecord> forces_;
    std::map<std::string, int> force_qualities_;
    std::vector<std::string> force_order_;
    std::vector<std::pair<std::pair<std::string, std::string>, PendingValue>> queue_;   // (tag, source) -> newest
    std::mutex queue_lock_;

    bool apply_dcs(Tag& t, const PendingValue& value, const std::string& source, bool touch, double now, bool drained);
};

}  // namespace azeocore
