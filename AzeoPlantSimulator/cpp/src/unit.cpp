#include "azeocore/unit.hpp"

#include <algorithm>
#include <cmath>

#include "azeocore/devices.hpp"

namespace azeocore {

void Malfunction::set(bool active_, std::optional<double> value_) {
    active = active_;
    if (value_) value = *value_;
    if (!apply) return;
    try {
        apply(active, value);
    } catch (const std::exception& e) {
        log_error("azeoplant.models.base", "Malfunction " + mf_id + " failed to apply: " + e.what());
    }
}

ProcessUnit::ProcessUnit(TagDatabase& db_, ProcessBus& bus_, double dt_, std::string code_, std::string name_)
    : code(std::move(code_)), name(std::move(name_)), db(db_), bus(bus_), dt(dt_),
      logger("azeoplant.models." + code) {}

void ProcessUnit::run_step(double dt_) {
    ++step_count;
    if (trace_enabled) trace.clear();
    step(dt_);
    if (!trace_enabled) return;
    for (const BalanceReading& b : balances_) trace.put(b.name + " residual", b.residual);
    if (trace_every > 0 && step_count % static_cast<unsigned long long>(trace_every) == 0 && log_enabled(LogLevel::Debug))
        log_debug(logger, code + " step=" + std::to_string(step_count) + " " + trace.render());
}

Tag& ProcessUnit::ai(const std::string& n, const std::string& desc, const std::string& eu, double lo, double hi,
                     std::optional<double> value) {
    Tag& t = db.analog(n, TagKind::AI, code, desc, eu, lo, hi, value);
    tags[n] = &t;
    tag_order.push_back(n);
    return t;
}

Tag& ProcessUnit::ao(const std::string& n, const std::string& desc, const std::string& eu, double lo, double hi,
                     double value) {
    Tag& t = db.analog(n, TagKind::AO, code, desc, eu, lo, hi, value);
    tags[n] = &t;
    tag_order.push_back(n);
    return t;
}

Tag& ProcessUnit::di(const std::string& n, const std::string& desc, const std::string& state0,
                     const std::string& state1, bool value) {
    Tag& t = db.discrete(n, TagKind::DI, code, desc, state0, state1, value);
    tags[n] = &t;
    tag_order.push_back(n);
    return t;
}

Tag& ProcessUnit::do_(const std::string& n, const std::string& desc, const std::string& state0,
                      const std::string& state1, bool value) {
    Tag& t = db.discrete(n, TagKind::DO, code, desc, state0, state1, value);
    tags[n] = &t;
    tag_order.push_back(n);
    return t;
}

Malfunction& ProcessUnit::add_malfunction(const std::string& mf_id, const std::string& target,
                                          const std::string& description, const std::string& category,
                                          const std::string& param_label, double param_min, double param_max,
                                          std::function<void(bool, double)> apply) {
    Malfunction mf;
    mf.mf_id = mf_id; mf.target = target; mf.description = description; mf.category = category;
    mf.param_label = param_label; mf.param_min = param_min; mf.param_max = param_max; mf.apply = std::move(apply);
    malfunctions.push_back(std::move(mf));
    return malfunctions.back();
}

void ProcessUnit::dyn(const std::string& attr, Stateful& member) { dynamics_.emplace_back(attr, &member); }

std::vector<std::pair<std::string, Transmitter*>> ProcessUnit::transmitters() const {
    std::vector<std::pair<std::string, Transmitter*>> out;
    for (const auto& kv : dynamics_)
        if (auto* t = dynamic_cast<Transmitter*>(kv.second)) out.emplace_back(kv.first, t);
    return out;
}

std::vector<std::pair<std::string, ControlValve*>> ProcessUnit::valves() const {
    std::vector<std::pair<std::string, ControlValve*>> out;
    for (const auto& kv : dynamics_)
        if (auto* v = dynamic_cast<ControlValve*>(kv.second)) out.emplace_back(kv.first, v);
    return out;
}

Value ProcessUnit::capture() const {
    Value payload = save_state();
    if (!payload.is_dict()) payload = Dict{};
    Dict dynamic;
    for (const auto& kv : dynamics_) dynamic[kv.first] = kv.second->capture_state();
    if (!dynamic.empty()) payload["_dyn"] = std::move(dynamic);
    Dict active;
    for (const Malfunction& mf : malfunctions) if (mf.active) active[mf.mf_id] = mf.value;
    if (!active.empty()) payload["_mf"] = std::move(active);
    return payload;
}

void ProcessUnit::apply(const Value& state) {
    load_state(state);
    const Value& dynamic = state.get("_dyn");
    if (dynamic.is_dict()) {
        for (auto& kv : dynamics_) {
            if (dynamic.has(kv.first)) kv.second->apply_state(dynamic.get(kv.first));
        }
    }
    const Value& wanted = state.get("_mf");
    for (Malfunction& mf : malfunctions) {
        if (wanted.is_dict() && wanted.has(mf.mf_id)) mf.set(true, wanted.get(mf.mf_id).number());
        else if (mf.active) mf.set(false, mf.value);
    }
}

void ProcessUnit::clear_malfunctions() {
    for (Malfunction& mf : malfunctions) mf.set(false, 0.0);
}

BalanceReading& ProcessUnit::record_balance(const std::string& n, double inflow, double outflow, double accumulation,
                                            const std::string& eu, double tolerance, const std::string& kind) {
    BalanceReading r;
    r.unit = code; r.name = n; r.inflow = inflow; r.outflow = outflow; r.accumulation = accumulation;
    r.residual = inflow - outflow - accumulation; r.tolerance = std::max(tolerance, 0.0); r.eu = eu; r.kind = kind;
    auto it = balance_index_.find(n);
    if (it == balance_index_.end()) {
        balance_index_[n] = balances_.size();
        balances_.push_back(r);
        return balances_.back();
    }
    balances_[it->second] = r;
    return balances_[it->second];
}

BalanceReading& ProcessUnit::record_inventory_balance(const std::string& n, double inflow, double outflow,
                                                      double before, double after, double capacity, double dt_,
                                                      double state_span, double time_base_s, const std::string& eu,
                                                      double tolerance) {
    const double h = std::max(dt_, 1e-12);
    const double accumulation = (after - before) / h * capacity * time_base_s / std::max(std::fabs(state_span), 1e-12);
    return record_balance(n, inflow, outflow, accumulation, eu, tolerance);
}

}  // namespace azeocore
