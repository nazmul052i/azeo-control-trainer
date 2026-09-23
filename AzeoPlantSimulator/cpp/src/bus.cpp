#include "azeocore/bus.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <stdexcept>

namespace azeocore {

std::string format_g(double v) {
    char buf[64];
    std::snprintf(buf, sizeof buf, "%g", v);
    return buf;
}

// ---------------------------------------------------------- StaleTracker
void StaleTracker::touch(const std::string& tag, double now) {
    if (last_.find(tag) == last_.end()) order_.push_back(tag);
    last_[tag] = now;
    stale_[tag] = false;
}

void StaleTracker::disarm(const std::string& tag) {
    last_.erase(tag);
    stale_.erase(tag);
    order_.erase(std::remove(order_.begin(), order_.end(), tag), order_.end());
}

void StaleTracker::disarm_all() {
    last_.clear();
    stale_.clear();
    order_.clear();
}

std::vector<std::string> StaleTracker::scan(double now) {
    std::vector<std::string> fresh;
    for (const auto& tag : order_) {
        const bool stale = (now - last_[tag]) > timeout_s;
        if (stale && !stale_[tag]) fresh.push_back(tag);
        stale_[tag] = stale;
    }
    return fresh;
}

bool StaleTracker::is_stale(const std::string& tag) const {
    auto it = stale_.find(tag);
    return it != stale_.end() && it->second;
}

std::vector<std::string> StaleTracker::armed() const { return order_; }

// --------------------------------------------------------- BusSignalSpec
void BusSignalSpec::validate() const {
    if (name.empty()) throw std::invalid_argument("bus signal name cannot be empty");
    if (producer.empty()) throw std::invalid_argument(name + ": bus producer cannot be empty");
    if (!std::isfinite(dflt)) throw std::invalid_argument(name + ": default must be finite");
    if (lo && !std::isfinite(*lo)) throw std::invalid_argument(name + ": low limit must be finite");
    if (hi && !std::isfinite(*hi)) throw std::invalid_argument(name + ": high limit must be finite");
    if (lo && hi && *lo >= *hi) throw std::invalid_argument(name + ": low limit must be below high limit");
}

// ------------------------------------------------------------ ProcessBus
ProcessBus::ProcessBus(const std::vector<BusSignalSpec>& specs) {
    for (const auto& s : specs) {
        s.validate();
        if (specs_.count(s.name)) throw std::invalid_argument("duplicate bus signal '" + s.name + "'");
        specs_[s.name] = s;
        order_.push_back(s.name);
        values_[s.name] = s.dflt;
    }
}

bool ProcessBus::system_source() const { return source_ && (*source_ == "SNAPSHOT" || *source_ == "INITIAL"); }

double ProcessBus::read(const std::string& name) {
    auto it = specs_.find(name);
    if (it == specs_.end()) {
        ++stats.unknown_signals;
        stats.last_violation = "undeclared process-bus signal '" + name + "'";
        throw BusContractError{stats.last_violation};
    }
    const BusSignalSpec& spec = it->second;
    if (source_ && !system_source() && *source_ != spec.producer
        && std::find(spec.consumers.begin(), spec.consumers.end(), *source_) == spec.consumers.end()) {
        ++stats.consumer_violations;
        std::string cons;
        for (std::size_t k = 0; k < spec.consumers.size(); ++k) cons += (k ? ", " : "") + spec.consumers[k];
        stats.last_violation = *source_ + " read '" + name + "', declared consumers are " + (cons.empty() ? "none" : cons);
        throw BusContractError{stats.last_violation};
    }
    ++stats.reads;
    return values_[name];
}

void ProcessBus::write(const std::string& name, double value) {
    auto it = specs_.find(name);
    if (it == specs_.end()) {
        ++stats.unknown_signals;
        stats.last_violation = "undeclared process-bus signal '" + name + "'";
        throw BusContractError{stats.last_violation};
    }
    const BusSignalSpec& spec = it->second;
    if (!std::isfinite(value)) {
        ++stats.rejected_nonfinite;
        stats.last_violation = name + ": non-finite bus value rejected";
        throw BusContractError{stats.last_violation};
    }
    if (source_ && !system_source() && *source_ != spec.producer) {
        ++stats.producer_violations;
        stats.last_violation = *source_ + " wrote '" + name + "', owned by " + spec.producer;
        throw BusContractError{stats.last_violation};
    }
    if ((spec.lo && value < *spec.lo) || (spec.hi && value > *spec.hi)) {
        ++stats.range_excursions;
        ++stats.excursions_by_signal[name];
    }
    values_[name] = value;
    ++stats.writes;
}

void ProcessBus::restore(const std::string& name, double value) {
    auto saved = source_;
    source_ = "SNAPSHOT";
    try {
        write(name, value);
    } catch (...) {
        source_ = saved;
        throw;
    }
    source_ = saved;
}

std::vector<std::string> ProcessBus::current_range_issues() const {
    std::vector<std::string> out;
    for (const auto& n : order_) {
        const BusSignalSpec& spec = specs_.at(n);
        const double v = values_.at(n);
        auto trim = [](std::string s) { while (!s.empty() && s.back() == ' ') s.pop_back(); return s; };
        if (spec.lo && v < *spec.lo) out.push_back(trim(n + "=" + format_g(v) + " below " + format_g(*spec.lo) + " " + spec.eu));
        if (spec.hi && v > *spec.hi) out.push_back(trim(n + "=" + format_g(v) + " above " + format_g(*spec.hi) + " " + spec.eu));
    }
    return out;
}

std::vector<std::string> ProcessBus::validate_topology(const std::vector<std::string>& unit_order) const {
    std::vector<std::string> errors;
    std::map<std::string, std::size_t> pos;
    for (std::size_t i = 0; i < unit_order.size(); ++i) pos[unit_order[i]] = i;
    for (const auto& n : order_) {
        const BusSignalSpec& spec = specs_.at(n);
        const bool known_producer = pos.count(spec.producer) || spec.producer == "FLOWSHEET" || spec.producer == "ENVIRONMENT";
        if (!known_producer) errors.push_back(n + ": unknown producer " + spec.producer);
        for (const auto& c : spec.consumers) {
            if (!pos.count(c)) { errors.push_back(n + ": unknown consumer " + c); continue; }
            if (pos.count(spec.producer) && pos[spec.producer] > pos[c] && !spec.tear)
                errors.push_back(n + ": backwards dependency " + spec.producer + "->" + c + " is not declared as a tear");
        }
        if ((spec.lo && spec.dflt < *spec.lo) || (spec.hi && spec.dflt > *spec.hi))
            errors.push_back(n + ": default is outside expected range");
    }
    return errors;
}

// -------------------------------------------------------- BalanceReading
bool BalanceReading::healthy() const { return std::isfinite(residual) && std::fabs(residual) <= tolerance; }

}  // namespace azeocore
