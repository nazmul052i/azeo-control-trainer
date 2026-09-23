// The tag database: the C++ twin of azeoplant/core/tags.py.
//
// Kind and quality are carried as small integers here; the Python binding
// converts them to the Python enums the rest of the code compares against
// by identity. The recursive mutex is the engine's step lock, held by the
// engine for a whole step and by readers for a snapshot, exactly as the
// Python RLock is.
#pragma once

#include "azeocore/export.h"

#include <cstdint>
#include <map>
#include <mutex>
#include <optional>
#include <string>
#include <tuple>
#include <vector>

namespace azeocore {

enum class TagKind : int { AI = 0, AO = 1, DI = 2, DO = 3 };
AZEOCORE_API const char* tag_kind_name(TagKind k);
AZEOCORE_API std::optional<TagKind> tag_kind_from(std::string_view name);
inline bool kind_analogue(TagKind k) { return k == TagKind::AI || k == TagKind::AO; }
inline bool kind_dcs_writable(TagKind k) { return k == TagKind::AO || k == TagKind::DO; }

constexpr int Q_GOOD = 0;
constexpr int Q_UNCERTAIN = 1;
constexpr int Q_BAD = 2;
constexpr double TAG_OVER_RANGE = 0.02;

AZEOCORE_API double wall_time();   // seconds since the epoch, as Python's time.time()

struct AZEOCORE_API Tag {
    std::string name;
    TagKind kind = TagKind::AI;
    std::string unit;
    std::string desc;
    std::string eu;
    double lo = 0.0;
    double hi = 100.0;
    double value = 0.0;        // engineering units; 0/1 for discrete
    int quality = Q_GOOD;
    double ts = 0.0;
    std::string state0 = "Off";
    std::string state1 = "On";
    bool override_on = false;
    double override_value = 0.0;
    int excursions = 0;
    double worst = 0.0;

    bool analogue() const { return kind_analogue(kind); }
    bool discrete_value() const { return value != 0.0; }
    double span() const;
    double pct() const;
    double effective() const;      // override for AO/DO, else value
    void set(double v, int q = Q_GOOD);
    // returns (applied, adjusted); throws std::invalid_argument on a non-finite
    // analogue write, std::domain_error on a write to a simulator-owned tag
    std::pair<double, bool> set_from_dcs(double v);
    std::string node_id() const;
    std::string format() const;
};

class RecursiveLock {
public:
    void lock() { m_.lock(); }
    void unlock() { m_.unlock(); }
    bool try_lock() { return m_.try_lock(); }
private:
    std::recursive_mutex m_;
};

class AZEOCORE_API TagDatabase {
public:
    RecursiveLock lock;

    Tag& add(Tag tag);   // throws std::invalid_argument on a duplicate name
    Tag& analog(const std::string& name, TagKind kind, const std::string& unit, const std::string& desc,
                const std::string& eu, double lo, double hi, std::optional<double> value = std::nullopt);
    Tag& discrete(const std::string& name, TagKind kind, const std::string& unit, const std::string& desc,
                  const std::string& state0 = "Off", const std::string& state1 = "On", bool value = false);
    bool contains(const std::string& name) const { return tags_.count(name) != 0; }
    Tag& at(const std::string& name);                 // throws std::out_of_range
    Tag* get(const std::string& name);
    std::vector<Tag*> all();
    std::vector<Tag*> by_kind(const std::vector<TagKind>& kinds);
    std::vector<Tag*> by_unit(const std::string& unit);
    std::vector<std::string> units() const;
    std::map<std::string, int> counts() const;
    std::vector<std::tuple<std::string, int, double>> excursions();
    std::size_t size() const { return tags_.size(); }
    const std::vector<std::string>& order() const { return order_; }

private:
    std::map<std::string, Tag> tags_;
    std::vector<std::string> order_;   // insertion order, as the Python dict keeps it
};

}  // namespace azeocore
