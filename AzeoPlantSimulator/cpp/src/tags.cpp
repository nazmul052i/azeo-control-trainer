#include "azeocore/tags.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <stdexcept>

namespace azeocore {

const char* tag_kind_name(TagKind k) {
    switch (k) {
    case TagKind::AI: return "AI";
    case TagKind::AO: return "AO";
    case TagKind::DI: return "DI";
    case TagKind::DO: return "DO";
    }
    return "AI";
}

std::optional<TagKind> tag_kind_from(std::string_view name) {
    if (name == "AI") return TagKind::AI;
    if (name == "AO") return TagKind::AO;
    if (name == "DI") return TagKind::DI;
    if (name == "DO") return TagKind::DO;
    return std::nullopt;
}

double wall_time() {
    using namespace std::chrono;
    return duration<double>(system_clock::now().time_since_epoch()).count();
}

static double clamp_plain(double x, double lo, double hi) { return x < lo ? lo : (x > hi ? hi : x); }

// -------------------------------------------------------------------- Tag
double Tag::span() const {
    const double s = hi - lo;
    return std::fabs(s) > 1e-12 ? s : 1.0;
}

double Tag::pct() const {
    if (!analogue()) return value != 0.0 ? 100.0 : 0.0;
    return clamp_plain((value - lo) / span() * 100.0, -10.0, 110.0);
}

double Tag::effective() const {
    if (override_on && kind_dcs_writable(kind)) return override_value;
    return value;
}

void Tag::set(double v, int q) {
    if (analogue()) {
        if (!std::isfinite(v)) {
            quality = Q_BAD;
            ts = wall_time();
            return;
        }
        const double margin = TAG_OVER_RANGE * span();
        if (v < lo - margin || v > hi + margin) {
            ++excursions;
            const double over = v > hi ? v - hi : lo - v;
            worst = std::max(worst, over);
        }
        value = v;
    } else {
        value = v != 0.0 ? 1.0 : 0.0;
    }
    quality = q;
    ts = wall_time();
}

std::pair<double, bool> Tag::set_from_dcs(double v) {
    if (!kind_dcs_writable(kind))
        throw std::domain_error(name + " is " + tag_kind_name(kind) + "; only AO and DO accept DCS writes");
    double applied;
    bool adjusted;
    if (analogue()) {
        if (!std::isfinite(v)) throw std::invalid_argument(name + ": non-finite write rejected");
        applied = clamp_plain(v, lo, hi);
        adjusted = applied != v;
    } else {
        applied = v != 0.0 ? 1.0 : 0.0;
        adjusted = false;
    }
    value = applied;
    quality = Q_GOOD;
    ts = wall_time();
    return {applied, adjusted};
}

std::string Tag::node_id() const { return "ns=2;s=PLANT_SIM." + unit + "." + tag_kind_name(kind) + "." + name; }

std::string Tag::format() const {
    if (!analogue()) return value != 0.0 ? state1 : state0;
    const double mag = std::max(std::fabs(lo), std::fabs(hi));
    const int dp = mag >= 500 ? 0 : (mag >= 20 ? 1 : 2);
    char buf[64];
    std::snprintf(buf, sizeof buf, "%.*f", dp, value);
    std::string out = buf;
    if (!eu.empty()) out += " " + eu;
    return out;
}

// ------------------------------------------------------------ TagDatabase
Tag& TagDatabase::add(Tag tag) {
    auto it = tags_.find(tag.name);
    if (it != tags_.end())
        throw std::invalid_argument("tag '" + tag.name + "' already defined by unit " + it->second.unit
                                    + "; duplicate from " + tag.unit);
    const std::string name = tag.name;
    if (tag.ts == 0.0) tag.ts = wall_time();
    auto res = tags_.emplace(name, std::move(tag));
    order_.push_back(name);
    return res.first->second;
}

Tag& TagDatabase::analog(const std::string& name, TagKind kind, const std::string& unit, const std::string& desc,
                         const std::string& eu, double lo, double hi, std::optional<double> value) {
    Tag t;
    t.name = name; t.kind = kind; t.unit = unit; t.desc = desc; t.eu = eu; t.lo = lo; t.hi = hi;
    t.value = value ? *value : lo;
    return add(std::move(t));
}

Tag& TagDatabase::discrete(const std::string& name, TagKind kind, const std::string& unit, const std::string& desc,
                           const std::string& state0, const std::string& state1, bool value) {
    Tag t;
    t.name = name; t.kind = kind; t.unit = unit; t.desc = desc; t.lo = 0.0; t.hi = 1.0;
    t.value = value ? 1.0 : 0.0; t.state0 = state0; t.state1 = state1;
    return add(std::move(t));
}

Tag& TagDatabase::at(const std::string& name) {
    auto it = tags_.find(name);
    if (it == tags_.end()) throw std::out_of_range(name);
    return it->second;
}

Tag* TagDatabase::get(const std::string& name) {
    auto it = tags_.find(name);
    return it == tags_.end() ? nullptr : &it->second;
}

std::vector<Tag*> TagDatabase::all() {
    std::vector<Tag*> out;
    out.reserve(order_.size());
    for (const auto& n : order_) out.push_back(&tags_.at(n));
    return out;
}

std::vector<Tag*> TagDatabase::by_kind(const std::vector<TagKind>& kinds) {
    std::vector<Tag*> out;
    for (const auto& n : order_) {
        Tag& t = tags_.at(n);
        if (std::find(kinds.begin(), kinds.end(), t.kind) != kinds.end()) out.push_back(&t);
    }
    return out;
}

std::vector<Tag*> TagDatabase::by_unit(const std::string& unit) {
    std::vector<Tag*> out;
    for (const auto& n : order_) {
        Tag& t = tags_.at(n);
        if (t.unit == unit) out.push_back(&t);
    }
    return out;
}

std::vector<std::string> TagDatabase::units() const {
    std::vector<std::string> seen;
    for (const auto& n : order_) {
        const std::string& u = tags_.at(n).unit;
        if (std::find(seen.begin(), seen.end(), u) == seen.end()) seen.push_back(u);
    }
    return seen;
}

std::map<std::string, int> TagDatabase::counts() const {
    std::map<std::string, int> out{{"AI", 0}, {"AO", 0}, {"DI", 0}, {"DO", 0}};
    for (const auto& kv : tags_) ++out[tag_kind_name(kv.second.kind)];
    return out;
}

std::vector<std::tuple<std::string, int, double>> TagDatabase::excursions() {
    std::vector<std::tuple<std::string, int, double>> out;
    for (const auto& n : order_) {
        const Tag& t = tags_.at(n);
        if (t.analogue() && t.excursions) out.emplace_back(t.name, t.excursions, t.worst);
    }
    std::stable_sort(out.begin(), out.end(),
                     [](const auto& a, const auto& b) { return std::get<2>(a) > std::get<2>(b); });
    return out;
}

}  // namespace azeocore
