#include "azeocore/iobus.hpp"

#include <algorithm>
#include <stdexcept>

#include "azeocore/log.hpp"

namespace azeocore {

namespace {
const char* const LOGGER = "azeoplant.io.bus";
void info(const std::string& m) { log(LogLevel::Info, LOGGER, m); }
void warn(const std::string& m) { log(LogLevel::Warning, LOGGER, m); }
}  // namespace

IOBus::IOBus(TagDatabase& db_, double stale_timeout_s) : db(db_), stale(stale_timeout_s) {}

// ---------------------------------------------------------------- reading

IOSample IOBus::sample_of(const Tag& t) {
    IOSample s;
    s.name = t.name; s.value = t.value; s.analogue = t.analogue(); s.quality = t.quality; s.ts = t.ts;
    s.kind = t.kind; s.unit = t.unit; s.eu = t.eu; s.lo = t.lo; s.hi = t.hi;
    return s;
}

IOSample IOBus::sample(const std::string& tag) const {
    db.lock.lock();
    try {
        IOSample s = sample_of(db.at(tag));
        db.lock.unlock();
        return s;
    } catch (...) { db.lock.unlock(); throw; }
}

std::vector<IOSample> IOBus::samples(const std::vector<std::string>* names) const {
    std::vector<IOSample> out;
    db.lock.lock();
    try {
        if (names == nullptr) for (Tag* t : db.all()) out.push_back(sample_of(*t));
        else for (const auto& n : *names) out.push_back(sample_of(db.at(n)));
    } catch (...) { db.lock.unlock(); throw; }
    db.lock.unlock();
    return out;
}

const char* IOBus::owner(const std::string& tag) const { return dcs_owned(db.at(tag)) ? "DCS" : "SIMULATOR"; }

// ------------------------------------------------------------- the holder

bool IOBus::register_dcs(const std::string& name) {
    if (holder && *holder != name) {
        warn("DCS registration refused: " + *holder + " already holds the output side, " + name + " rejected");
        return false;
    }
    holder = name;
    info("Output side held by " + name);
    return true;
}

void IOBus::release_dcs(const std::string& name) {
    if (holder && *holder == name) {
        holder.reset();
        info("Output side released by " + name);
    }
}

// ---------------------------------------------------------------- writing

bool IOBus::apply_dcs(Tag& t, const PendingValue& value, const std::string& source, bool touch, double now,
                      bool drained) {
    if (!value.numeric && t.analogue()) {
        ++stats.rejected_value;
        warn("Rejected " + source + " write to " + t.name + ": value is not a number");
        return false;
    }
    const double v = t.analogue() ? value.number : (value.truthy ? 1.0 : 0.0);
    std::pair<double, bool> r;
    try {
        r = t.set_from_dcs(v);
    } catch (const std::exception& e) {
        ++stats.rejected_value;
        warn("Rejected " + source + " write to " + t.name + ": " + e.what());
        return false;
    }
    if (r.second) {
        ++stats.adjusted_writes;
        warn("Clamped " + source + " write to " + t.name + " within [" + format_g(t.lo) + ", " + format_g(t.hi) + "]");
    }
    if (touch) stale.touch(t.name, now);
    if (drained) ++stats.drained; else ++stats.dcs_writes;
    if (on_change) on_change(t.name);
    return true;
}

WriteVerdict IOBus::write_from_dcs(const std::string& tag, const PendingValue& value, const std::string& source) {
    Tag& t = db.at(tag);
    if (!dcs_owned(t)) { ++stats.rejected_ownership; return WriteVerdict::RejectedOwnership; }
    if (source != "sis" && holder && source != *holder) { ++stats.rejected_holder; return WriteVerdict::RejectedHolder; }
    return apply_dcs(t, value, source, false, 0.0, false) ? WriteVerdict::Applied : WriteVerdict::RejectedValue;
}

bool IOBus::authorize_dcs_write(const std::string& tag, const std::string& source) {
    Tag* t = db.get(tag);
    if (!t) return false;
    if (!dcs_owned(*t)) { ++stats.rejected_ownership; return false; }
    if (holder && source != *holder) { ++stats.rejected_holder; return false; }
    stale.touch(tag, wall_time());
    ++stats.dcs_writes;
    return true;
}

void IOBus::queue_write(const std::string& tag, const PendingValue& value, const std::string& source) {
    std::lock_guard<std::mutex> g(queue_lock_);
    const auto key = std::make_pair(tag, source);
    auto it = std::find_if(queue_.begin(), queue_.end(), [&](const auto& e) { return e.first == key; });
    if (it != queue_.end()) {
        queue_.erase(it);
        ++stats.coalesced_writes;
    } else if (queue_.size() >= static_cast<std::size_t>(MAX_PENDING_WRITES)) {
        ++stats.rejected_capacity;
        throw std::length_error("virtual I/O write queue reached its distinct-route capacity ("
                                + std::to_string(MAX_PENDING_WRITES) + ")");
    }
    queue_.emplace_back(key, value);
    ++stats.queued;
    stats.pending_peak = std::max(stats.pending_peak, static_cast<int>(queue_.size()));
}

int IOBus::drain_writes() {
    decltype(queue_) pending;
    {
        std::lock_guard<std::mutex> g(queue_lock_);
        pending.swap(queue_);
    }
    int n = 0;
    const double now = wall_time();
    for (auto& e : pending) {
        const std::string& tag = e.first.first;
        const std::string& source = e.first.second;
        Tag* t = db.get(tag);
        if (!t) continue;
        if (!dcs_owned(*t)) { ++stats.rejected_ownership; warn("Rejected " + source + " write to " + tag + ": simulator-owned"); continue; }
        if (holder && source != *holder) { ++stats.rejected_holder; continue; }
        if (apply_dcs(*t, e.second, source, true, now, true)) ++n;
    }
    return n;
}

QueueHealth IOBus::queue_health() {
    std::lock_guard<std::mutex> g(queue_lock_);
    return {static_cast<int>(queue_.size()), MAX_PENDING_WRITES, stats.pending_peak, stats.coalesced_writes,
            stats.rejected_capacity};
}

// ----------------------------------------------------------------- forces

void IOBus::force(const std::string& tag, double value, const std::string& source, const std::string& reason) {
    if (reason.empty()) throw std::invalid_argument("a force needs a reason");
    Tag& t = db.at(tag);
    ForceRecord rec;
    rec.tag = tag; rec.source = source; rec.reason = reason; rec.t = wall_time();
    rec.value = t.analogue() ? value : (value != 0.0 ? 1.0 : 0.0);
    if (dcs_owned(t)) { t.override_on = true; t.override_value = rec.value; }
    else rec.true_value = t.value;
    if (!forces_.count(tag)) force_order_.push_back(tag);
    force_qualities_.erase(tag);
    forces_[tag] = rec;
    info("FORCE " + tag + " = " + format_g(rec.value) + " by " + source + ": " + reason);
}

void IOBus::simulate_input(const std::string& tag, double value, int quality, const std::string& source) {
    Tag& t = db.at(tag);
    if (dcs_owned(t)) throw std::logic_error("only AI and DI can be simulated: " + tag);
    if (quality < Q_GOOD || quality > Q_BAD) throw std::invalid_argument("unsupported quality");
    // Preserve the Trainer's engineering input simulation across native ticks
    // and snapshots; an ordinary force alone loses its requested signal quality.
    force(tag, value, source, "Virtual I/O signal simulation");
    force_qualities_[tag] = quality;
    t.value = t.analogue() ? value : (value != 0.0 ? 1.0 : 0.0);
    t.quality = quality;
    t.ts = wall_time();
}

void IOBus::release(const std::string& tag) {
    auto it = forces_.find(tag);
    if (it == forces_.end()) return;
    const std::string src = it->second.source;
    forces_.erase(it);
    force_qualities_.erase(tag);
    force_order_.erase(std::remove(force_order_.begin(), force_order_.end(), tag), force_order_.end());
    Tag* t = db.get(tag);
    if (t && dcs_owned(*t)) t->override_on = false;
    info("RELEASE " + tag + " (was forced by " + src + ")");
}

void IOBus::release_all() {
    const auto order = force_order_;
    for (const auto& tag : order) release(tag);
}

std::vector<ForceRecord> IOBus::active_forces() const {
    std::vector<ForceRecord> out;
    for (const auto& tag : force_order_) out.push_back(forces_.at(tag));
    return out;
}

// --------------------------------------------------------------- the step

void IOBus::tick() {
    drain_writes();
    for (const auto& tag : force_order_) {
        ForceRecord& rec = forces_.at(tag);
        Tag* t = db.get(tag);
        if (!t || dcs_owned(*t)) continue;
        rec.true_value = t->value;
        t->value = rec.value;
        const auto quality = force_qualities_.find(tag);
        if (quality != force_qualities_.end()) {
            t->quality = quality->second;
            t->ts = wall_time();
        }
        ++stats.forces_applied;
    }
    for (const auto& tag : stale.scan(wall_time())) {
        Tag* t = db.get(tag);
        if (t) {
            t->quality = Q_UNCERTAIN;
            warn(tag + " stale: no external write for " + format_g(stale.timeout_s) + " s");
        }
    }
}

// ------------------------------------------------------------------- state

Value IOBus::capture_state() const {
    Dict d;
    List forces;
    for (const auto& tag : force_order_) {
        const ForceRecord& r = forces_.at(tag);
        Dict f;
        f["tag"] = r.tag; f["value"] = r.value; f["source"] = r.source; f["reason"] = r.reason; f["t"] = r.t;
        forces.push_back(Value(f));
    }
    d["forces"] = Value(forces);
    Dict qualities;
    for (const auto& [tag, quality] : force_qualities_) qualities[tag] = static_cast<double>(quality);
    d["force_qualities"] = Value(qualities);
    d["holder"] = holder ? Value(*holder) : Value(nullptr);
    return d;
}

void IOBus::apply_state(const Value& s) {
    release_all();
    if (s.is_null() || !s.is_dict() || !s.has("forces")) return;
    const Value& forces = s.get("forces");
    if (!forces.is_list()) return;
    for (const Value& rec : forces.list()) {
        if (!rec.is_dict() || !rec.has("tag")) continue;
        const std::string tag = rec.get_string("tag", "");
        const std::string source = rec.get_string("source", "restored");
        const std::string reason = rec.get_string("reason", "restored from snapshot");
        if (!db.get(tag)) continue;              // a tag this plant does not have: skipped, as Python's KeyError is
        try {
            force(tag, rec.get("value").number(0.0), source, reason);
        } catch (const std::invalid_argument&) {
            continue;
        }
    }
    if (s.has("force_qualities") && s.get("force_qualities").is_dict()) {
        for (const auto& [tag, value] : s.get("force_qualities").dict()) {
            const int quality = static_cast<int>(value.number(-1));
            if (forces_.count(tag) && quality >= Q_GOOD && quality <= Q_BAD)
                force_qualities_[tag] = quality;
        }
    }
}

}  // namespace azeocore
